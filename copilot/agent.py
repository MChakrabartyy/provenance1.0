"""The copilot: picks tools, reads their exact output, retrieves documents, writes a fixed-shape answer.

Two narrators share the same tools, guardrails and logging:
  * LLMNarrator       - Anthropic tool loop (used when ANTHROPIC_API_KEY is set and PROVENANCE_LLM != 0)
  * PlannerNarrator   - deterministic keyword planner; no network, so the eval harness and the demo run offline

Guardrails applied to BOTH, after the narrator returns:
  * every number in `answer` must appear in `figures` with source rows, else retry once, then refuse
  * if any input row used was flagged by quality checks, a warning is required (added from tool warnings)
  * prediction / advice questions are refused before any tool runs
  * permission errors from tools become refusals; the model never sees data it may not
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date, datetime, timezone
from decimal import Decimal

from provenance.config import ANTHROPIC_MODEL, LLM_ENABLED, LLM_TIMEOUT_S
from provenance.copilot.answer import Answer, Citation, Figure, is_prediction_request, number_check, refusal
from provenance.copilot.tools import TOOL_SCHEMAS, PermissionDenied, Tools, User

SYSTEM_PROMPT = """You are Provenance, the copilot for a multi-manager fund platform. You explain; you never calculate.
Rules:
- Every number you state must come from a tool result and be listed in `figures` with the tool's source_rows. Do not do arithmetic yourself; if a figure is not in a tool result, do not state it.
- Cite documents with document_id and chunk_index from search_documents. Do not quote documents you did not retrieve.
- Copy every warning from tool results into `warnings`.
- Never predict prices or give investment advice; refuse politely with confidence "refused".
- If a tool returns a permission error, refuse and say the user may only see their own pod.
- Respond with ONLY a JSON object matching: {"answer": str, "figures": [{"label": str, "value": str, "source_rows": [str]}], "citations": [{"document_id": int, "chunk_index": int, "title": str}], "warnings": [str], "confidence": "answered"|"partial"|"refused"}
Context: today's data date is {as_of}. The user is {role} {scope}."""


def money(d) -> str:
    return f"{Decimal(str(d)):,.2f}"


def pct(d) -> str:
    return f"{Decimal(str(d)):.2f}%"


# ---------------------------------------------------------------- planner (offline narrator)

class PlannerNarrator:
    MANAGER_RE = re.compile(r"\bmanager\s+([A-D])\b", re.I)

    def __init__(self, tools: Tools):
        self.t = tools
        self.user = tools.user
        self.as_of = tools.latest_date()
        self.sectors = [r["sector"] for r in tools.conn.execute("SELECT DISTINCT sector FROM instruments")]

    def _manager(self, q: str) -> str | None:
        m = self.MANAGER_RE.search(q)
        if m:
            return m.group(1).upper()
        if self.user.role == "pm":
            return self.user.manager_id
        return None

    def _period(self, q: str) -> tuple[date, date]:
        ql = q.lower()
        n = 1 if "today" in ql or "yesterday" in ql else 21 if "month" in ql else 5
        return self.t.trading_days_back(self.as_of, n), self.as_of

    def _sector(self, q: str) -> str | None:
        ql = q.lower()
        for s in self.sectors:
            if s.lower() in ql or (s == "Semiconductors" and "semi" in ql) or (s == "Technology" and " tech" in ql):
                return s
        return None

    def _symbol(self, q: str) -> str | None:
        syms = {r["symbol"] for r in self.t.conn.execute("SELECT symbol FROM instruments WHERE type='stock'")}
        names = {"nvidia": "NVDA", "apple": "AAPL", "microsoft": "MSFT", "lilly": "LLY", "tesla": "TSLA", "meta": "META", "amazon": "AMZN"}
        for w in re.findall(r"[A-Za-z]+", q):
            if w.upper() in syms:
                return w.upper()
            if w.lower() in names:
                return names[w.lower()]
        return None

    def answer(self, q: str) -> Answer:
        ql = q.lower()
        pnl_intent = re.search(r"\b(why|drove|driver\w*|p&l|pnl|attribution|down|up|lost|made|perform\w*)\b", ql)
        quality_intent = re.search(r"\b(stale|quarantin\w*|flagged|bad data|quality|failed source\w*|pipeline|last night\w*)\b", ql)
        if quality_intent and not (pnl_intent and self._manager(q)):
            return self._quality(q)
        if re.search(r"\b(10-k|10k|10-q|filing|memo|say about|said about|wrote|research)\b", ql):
            return self._documents(q)
        if re.search(r"\b(position\w*|holding\w*|top \d+|book)\b", ql):
            return self._positions(q)
        if re.search(r"\b(why|drove|driver\w*|p&l|pnl|attribution|down|up|lost|made|perform\w*)\b", ql):
            return self._attribution(q)
        if re.search(r"\b(concentrat\w*|largest|biggest)\b", ql) and self.user.role == "risk":
            return self._concentration(q)
        if re.search(r"\b(exposure\w*|exposed|sector\w*|gross|net|long|short)\b", ql):
            return self._exposure(q)
        return self._documents(q, partial=True)

    # ----- intents -----
    def _attribution(self, q: str) -> Answer:
        mid = self._manager(q)
        if mid is None:
            mid = "firm"
        start, end = self._period(q)
        if mid == "firm":
            ids = self.t._scope_ids("firm")
            figs, parts, rows = [], [], []
            for m in ids:
                r = self.t.get_pnl_attribution(m, start.isoformat(), end.isoformat())
                figs.append(Figure(label=f"Manager {m} P&L", value=money(r["data"]["total"]), source_rows=r["source_rows"]))
                parts.append(f"Manager {m} {money(r['data']['total'])}")
            return Answer(answer=f"Firm P&L by manager from {start} to {end}: " + "; ".join(parts) + ".", figures=figs, confidence="answered", warnings=self._warnings())
        r = self.t.get_pnl_attribution(mid, start.isoformat(), end.isoformat())
        d = r["data"]
        figs = [Figure(label="Total P&L", value=money(d["total"]), source_rows=r["source_rows"]),
                Figure(label="Realized", value=money(d["realized"]), source_rows=r["source_rows"]),
                Figure(label="Unrealized", value=money(d["unrealized"]), source_rows=r["source_rows"])]
        total = Decimal(d["total"])
        movers = d["top_detractors"] if total < 0 else d["top_contributors"]
        mover_txt = []
        for p in movers[:3]:
            figs.append(Figure(label=f"{p['symbol']} P&L", value=money(p["pnl"]), source_rows=r["source_rows"]))
            mover_txt.append(f"{p['symbol']} {money(p['pnl'])}")
        sec = d["by_sector"][0] if total < 0 else d["by_sector"][-1]
        figs.append(Figure(label=f"{sec['sector']} sector P&L", value=money(sec["pnl"]), source_rows=r["source_rows"]))
        direction = "down" if total < 0 else "up"
        text = (f"Manager {mid} is {direction} {money(d['total'])} from {start} to {end} (realized {money(d['realized'])}, unrealized {money(d['unrealized'])}). "
                f"The largest {'detractors' if total < 0 else 'contributors'} were {', '.join(mover_txt)}; the {sec['sector']} sector contributed {money(sec['pnl'])}.")
        cits: list[Citation] = []
        for p in movers[:3]:
            hits = self.t.search_documents(f"{p['symbol']} thesis positioning risks", mid, 8)["data"]["hits"]
            memo = [h for h in hits if h["doc_type"] == "memo" and p["symbol"] in h["title"]]
            if memo:
                for h in memo[:2]:
                    cits.append(Citation(document_id=h["document_id"], chunk_index=h["chunk_index"], title=h["title"]))
                text += f" The analyst memo on {p['symbol']} is cited for the thesis and risks."
                break
        return Answer(answer=text, figures=figs, citations=cits, warnings=self._warnings(), confidence="answered")

    def _exposure(self, q: str) -> Answer:
        mid = self._manager(q)
        ql = q.lower()
        wants_firm = mid is None or re.search(r"\b(firm\w*|all managers|across|whole book)\b", ql)
        if wants_firm and self.user.role != "risk":
            raise PermissionDenied("firm-wide exposure requires the risk role")
        scope = "firm" if wants_firm else mid
        r = self.t.get_exposure(scope, self.as_of.isoformat())
        d = r["data"]
        sector = self._sector(q)
        figs = [Figure(label="Gross exposure", value=money(d["gross"]), source_rows=r["source_rows"]),
                Figure(label="Net exposure", value=money(d["net"]), source_rows=r["source_rows"])]
        who = "the firm" if scope == "firm" else f"Manager {scope}"
        if sector:
            row = next((s for s in d["by_sector"] if s["sector"] == sector), None)
            if row is None:
                return Answer(answer=f"{who} has no {sector} exposure as of {self.as_of}.", figures=figs, warnings=self._warnings(), confidence="answered")
            figs += [Figure(label=f"{sector} net exposure", value=money(row["net"]), source_rows=r["source_rows"]),
                     Figure(label=f"{sector} gross exposure", value=money(row["gross"]), source_rows=r["source_rows"])]
            text = f"As of {self.as_of}, {who} has net exposure of {money(row['net'])} to {sector} (gross {money(row['gross'])}), out of total gross exposure {money(d['gross'])} and net {money(d['net'])}."
        else:
            top = d["by_sector"][:3]
            for s in top:
                figs.append(Figure(label=f"{s['sector']} net", value=money(s["net"]), source_rows=r["source_rows"]))
            text = (f"As of {self.as_of}, {who} has gross exposure {money(d['gross'])} and net exposure {money(d['net'])}. Largest sectors by gross: "
                    + ", ".join(f"{s['sector']} (net {money(s['net'])})" for s in top) + ".")
        return Answer(answer=text, figures=figs, warnings=self._warnings(), confidence="answered")

    def _concentration(self, q: str) -> Answer:
        r = self.t.get_concentration(self.as_of.isoformat())
        d = r["data"]
        figs, names = [], []
        for n in d["largest_names"][:3]:
            figs.append(Figure(label=f"{n['name']} % of gross", value=pct(n["pct_of_gross"]), source_rows=r["source_rows"]))
            names.append(f"{n['name']} ({pct(n['pct_of_gross'])} of gross)")
        s = d["largest_sectors"][0]
        figs.append(Figure(label=f"{s['sector']} % of gross", value=pct(s["pct_of_gross"]), source_rows=r["source_rows"]))
        text = f"As of {self.as_of}, the firm is most concentrated in {', '.join(names)}. The largest sector is {s['sector']} at {pct(s['pct_of_gross'])} of gross exposure."
        return Answer(answer=text, figures=figs, warnings=self._warnings(), confidence="answered")

    def _positions(self, q: str) -> Answer:
        mid = self._manager(q)
        if mid is None:
            return Answer(answer="Which manager's positions? Name a manager (for example 'Manager C').", confidence="partial")
        m = re.search(r"top\s+(\d+)", q.lower()); n = int(m.group(1)) if m else 5
        r = self.t.get_positions(mid, self.as_of.isoformat(), n)
        rows = r["data"]["positions"]
        figs, parts = [], []
        for p in rows:
            figs.append(Figure(label=f"{p['symbol']} delta-adjusted exposure", value=money(p["delta_adjusted_exposure"]), source_rows=r["source_rows"]))
            parts.append(f"{p['symbol']} {money(p['delta_adjusted_exposure'])}")
        text = f"Manager {mid}'s top {len(rows)} positions by delta-adjusted exposure as of {self.as_of}: " + "; ".join(parts) + "."
        return Answer(answer=text, figures=figs, warnings=self._warnings(), confidence="answered")

    def _quality(self, q: str) -> Answer:
        r = self.t.get_quality_report(self.as_of.isoformat())
        d = r["data"]
        ql = q.lower()
        rule = "stale_price" if "stale" in ql else "duplicate_trade" if "duplicate" in ql else None
        items = [x for x in d["quarantined"] if rule is None or x["rule"] == rule]
        figs = [Figure(label="Rows quarantined", value=str(d["rows_quarantined"]), source_rows=r["source_rows"]),
                Figure(label="Rows in", value=str(d["rows_in"]), source_rows=r["source_rows"])]
        if rule:
            figs.append(Figure(label=f"{rule} rows", value=str(len(items)), source_rows=r["source_rows"]))
            if items:
                desc = "; ".join(f"{x['symbol'] or x['table']} on {x['as_of_date'] or self.as_of} ({x['reason']})" for x in items[:5])
                text = f"Yes. The {self.as_of} run flagged {len(items)} {rule.replace('_', ' ')} row(s): {desc}. Those rows were quarantined and not used in any calculation."
            else:
                text = f"No. The {self.as_of} run quarantined {d['rows_quarantined']} row(s) in total, none for {rule.replace('_', ' ')}."
        else:
            by_rule = ", ".join(f"{k} {v}" for k, v in d["quarantined_by_rule"].items()) or "none"
            for k, v in d["quarantined_by_rule"].items():
                figs.append(Figure(label=f"{k} rows", value=str(v), source_rows=r["source_rows"]))
            text = f"The {self.as_of} run finished with status {d['status']}: {d['rows_in']} rows in, {d['rows_quarantined']} quarantined ({by_rule})."
            if d["sources_failed"]:
                text += f" Failed sources: {', '.join(d['sources_failed'])}."
        return Answer(answer=text, figures=figs, warnings=self._warnings(), confidence="answered")

    def _documents(self, q: str, partial: bool = False) -> Answer:
        mid = self._manager(q) if "memo" in q.lower() or "analyst" in q.lower() else None
        sym = self._symbol(q)
        query = q if not sym else f"{sym} {q}"
        r = self.t.search_documents(query, mid, 3)
        hits = r["data"]["hits"]
        if sym:
            hits = [h for h in hits if sym in h["title"]] or hits
        if not hits:
            return Answer(answer="I could not find a document that answers this in the sources you have access to.", confidence="partial", warnings=self._warnings())
        h = hits[0]
        body = re.sub(r"^#+ .*\n", "", h["text"]).strip().replace("\n", " ")
        cits = [Citation(document_id=x["document_id"], chunk_index=x["chunk_index"], title=x["title"]) for x in hits[:2]]
        text = f"From {h['title']}: {body[:400]}"
        if "synthetic" in h["title"].lower() or "SYNTHETIC" in h["text"]:
            text += " (This document is synthetic test data.)"
        # strip numbers the number check would reject: quoted document text is cited, not a figure
        return Answer(answer=text, citations=cits, warnings=self._warnings(), confidence="partial" if partial else "answered", figures=[])

    def _warnings(self) -> list[str]:
        out: list[str] = []
        for c in self.t.calls:
            for w in c.get("warnings", []):
                if w not in out:
                    out.append(w)
        return out


# ---------------------------------------------------------------- LLM narrator

class LLMNarrator:
    def __init__(self, tools: Tools):
        import anthropic
        self.t = tools
        self.client = anthropic.Anthropic(timeout=LLM_TIMEOUT_S, max_retries=2)
        self.as_of = tools.latest_date()

    def _run_tool(self, name: str, args: dict) -> str:
        fn = getattr(self.t, name)
        try:
            return json.dumps(fn(**args), default=str)
        except PermissionDenied as e:
            return json.dumps({"error": "permission_denied", "detail": str(e)})
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": type(e).__name__, "detail": str(e)})

    def answer(self, q: str, feedback: str | None = None) -> Answer:
        u = self.t.user
        system = SYSTEM_PROMPT.format(as_of=self.as_of.isoformat(), role=u.role, scope=f"for manager {u.manager_id}" if u.manager_id else "with firm-wide access")
        messages: list[dict] = [{"role": "user", "content": q}]
        if feedback:
            messages.append({"role": "user", "content": feedback})
        for _ in range(8):
            resp = self.client.messages.create(model=ANTHROPIC_MODEL, max_tokens=16000, system=system, tools=TOOL_SCHEMAS, messages=messages)
            if resp.stop_reason == "refusal":
                return refusal("The model declined to answer this question.")
            if resp.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": resp.content})
                results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": self._run_tool(block.name, dict(block.input))})
                messages.append({"role": "user", "content": results})
                continue
            text = "".join(b.text for b in resp.content if b.type == "text")
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                return refusal("The model did not return a structured answer.")
            try:
                return Answer.model_validate_json(m.group(0))
            except Exception as e:  # noqa: BLE001
                return refusal(f"The model's answer did not match the schema: {e}")
        return refusal("Too many tool calls without a final answer.")


# ---------------------------------------------------------------- orchestration

def ask(conn: sqlite3.Connection, user: User, question: str, use_llm: bool | None = None) -> Answer:
    t0 = time.perf_counter()
    tools = Tools(conn, user)
    rejected = 0
    if is_prediction_request(question):
        ans = refusal("I can't predict prices or give investment advice. I can explain what happened in the data: P&L drivers, exposures, positions, data quality, and what the documents say.")
    elif user.role == "engineer" and not re.search(r"\b(run|pipeline|quarantin|stale|quality|source|failed)\b", question.lower()):
        ans = refusal("Engineer accounts can see pipeline health only. Ask about runs, quarantined rows, or failed sources.")
    else:
        use_llm = LLM_ENABLED if use_llm is None else use_llm
        try:
            narrator = LLMNarrator(tools) if use_llm else PlannerNarrator(tools)
            ans = narrator.answer(question)
            bad = number_check(ans)
            if bad and ans.confidence != "refused":
                rejected += 1
                ans = narrator.answer(question, feedback=f"Your previous answer stated numbers not backed by figures with source rows: {bad}. Only state numbers from tool results.") if use_llm else narrator.answer(question)
                bad = number_check(ans)
                if bad:
                    rejected += 1
                    ans = refusal(f"I could not produce an answer in which every number is traceable to a data row (unverified: {', '.join(bad)}).")
        except PermissionDenied as e:
            scope = f"your own pod (Manager {user.manager_id})" if user.manager_id else "the pods you are permitted to see"
            ans = refusal(f"You can only see {scope}. {e}")
        except RuntimeError as e:
            ans = refusal(str(e))
    # required warnings: anything a tool flagged must be surfaced
    for c in tools.calls:
        for w in c.get("warnings", []):
            if w not in ans.warnings:
                ans.warnings.append(w)
    latency = int((time.perf_counter() - t0) * 1000)
    conn.execute("INSERT INTO copilot_logs (user_id, question, tool_calls, answer, citations, latency_ms, created_at) VALUES (?,?,?,?,?,?,?)",
                 (user.id, question, json.dumps({"calls": tools.calls, "rejected": rejected, "mode": "llm" if (use_llm if use_llm is not None else LLM_ENABLED) else "planner"}),
                  ans.model_dump_json(), json.dumps([c.model_dump() for c in ans.citations]), latency, datetime.now(timezone.utc).isoformat()))
    return ans
