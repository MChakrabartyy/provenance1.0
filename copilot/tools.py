"""Engine functions wrapped as LLM tools. Permissions are enforced here, before any data is read.

Every tool returns {"data": ..., "source_rows": [...], "warnings": [...]} so the narrator can cite rows.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from provenance.engine import loader
from provenance.engine.attribution import attribution
from provenance.engine.exposure import concentration, exposure, position_exposures


class PermissionDenied(Exception):
    pass


@dataclass
class User:
    id: int
    email: str
    role: str                 # pm | risk | engineer
    manager_id: str | None

    @property
    def allowed_managers(self) -> list[str] | None:
        """None = all managers (risk). PMs get their own; engineers get none (pipeline data only)."""
        if self.role == "risk":
            return None
        if self.role == "pm":
            return [self.manager_id] if self.manager_id else []
        return []


def _jsonable(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, date):
        return o.isoformat()
    return o


class Tools:
    def __init__(self, conn: sqlite3.Connection, user: User):
        self.conn, self.user = conn, user
        self.calls: list[dict] = []

    # ----- scope helpers -----
    def _check_manager(self, manager_id: str) -> None:
        allowed = self.user.allowed_managers
        if allowed is not None and manager_id not in allowed:
            raise PermissionDenied(f"{self.user.role} user may not access manager {manager_id}")

    def _scope_ids(self, scope: str) -> list[str]:
        if scope.lower() in ("firm", "all", "*"):
            if self.user.role != "risk":
                raise PermissionDenied("firm-wide scope requires the risk role")
            return [r["id"] for r in self.conn.execute("SELECT id FROM managers ORDER BY id")]
        self._check_manager(scope)
        return [scope]

    def latest_date(self) -> date:
        d = loader.latest_run_date(self.conn)
        if d is None:
            raise RuntimeError("no completed pipeline run; run the pipeline first")
        return d

    def trading_days_back(self, end: date, n: int) -> date:
        days = [d for d in loader.trading_days(self.conn) if d <= end]
        return days[-n - 1] if len(days) > n else days[0]

    def _record(self, name: str, args: dict, result: dict) -> dict:
        out = {"data": _jsonable(result["data"]), "source_rows": result["source_rows"], "warnings": result["warnings"]}
        self.calls.append({"tool": name, "args": _jsonable(args), "source_rows": len(out["source_rows"]), "warnings": out["warnings"]})
        return out

    # ----- tools -----
    def get_pnl_attribution(self, manager_id: str, start: str, end: str) -> dict:
        self._check_manager(manager_id)
        s, e = date.fromisoformat(start), date.fromisoformat(end)
        trades = loader.load_trades(self.conn, [manager_id], e)
        inst = loader.load_instruments(self.conn)
        res = attribution(manager_id, s, e, trades, inst, loader.load_marks(self.conn, s), loader.load_marks(self.conn, e))
        return self._record("get_pnl_attribution", {"manager_id": manager_id, "start": start, "end": end}, {"data": res.data, "source_rows": res.source_rows, "warnings": res.warnings})

    def get_exposure(self, scope: str, as_of: str) -> dict:
        ids = self._scope_ids(scope)
        d = date.fromisoformat(as_of)
        res = exposure(ids, d, loader.load_trades(self.conn, ids, d), loader.load_instruments(self.conn), loader.load_marks(self.conn, d), loader.load_rate(self.conn, d), loader.default_vol())
        return self._record("get_exposure", {"scope": scope, "date": as_of}, {"data": res.data, "source_rows": res.source_rows, "warnings": res.warnings})

    def get_concentration(self, as_of: str) -> dict:
        ids = self._scope_ids("firm")
        d = date.fromisoformat(as_of)
        res = concentration(ids, d, loader.load_trades(self.conn, ids, d), loader.load_instruments(self.conn), loader.load_marks(self.conn, d), loader.load_rate(self.conn, d), loader.default_vol())
        return self._record("get_concentration", {"date": as_of}, {"data": res.data, "source_rows": res.source_rows, "warnings": res.warnings})

    def get_positions(self, manager_id: str, as_of: str, top_n: int = 10) -> dict:
        self._check_manager(manager_id)
        d = date.fromisoformat(as_of)
        res = position_exposures([manager_id], d, loader.load_trades(self.conn, [manager_id], d), loader.load_instruments(self.conn), loader.load_marks(self.conn, d), loader.load_rate(self.conn, d), loader.default_vol())
        rows = sorted(res.data, key=lambda r: -abs(r["delta_adjusted_exposure"]))[:top_n]
        return self._record("get_positions", {"manager_id": manager_id, "date": as_of, "top_n": top_n}, {"data": {"manager_id": manager_id, "as_of": as_of, "positions": rows}, "source_rows": res.source_rows, "warnings": res.warnings})

    def get_quality_report(self, run_date: str) -> dict:
        row = self.conn.execute("SELECT * FROM pipeline_runs WHERE run_date=? ORDER BY id DESC LIMIT 1", (run_date,)).fetchone()
        if row is None:
            return self._record("get_quality_report", {"run_date": run_date}, {"data": {"run_date": run_date, "status": "no run"}, "source_rows": [], "warnings": [f"no pipeline run for {run_date}"]})
        report = json.loads(row["report"] or "{}")
        q_sql = "SELECT id, table_name, rule, reason, raw_row, manager_id FROM quarantine WHERE run_id IN (SELECT id FROM pipeline_runs WHERE run_date=?)"
        params: list = [run_date]
        allowed = self.user.allowed_managers
        if self.user.role == "pm":
            q_sql += " AND (manager_id IS NULL OR manager_id IN (%s))" % ",".join("?" * len(allowed)); params += allowed
        quarantined = []
        for q in self.conn.execute(q_sql + " ORDER BY id", params):
            raw = json.loads(q["raw_row"])
            quarantined.append({"id": q["id"], "table": q["table_name"], "rule": q["rule"], "reason": q["reason"], "manager_id": q["manager_id"],
                                "symbol": raw.get("symbol") or raw.get("Ticker") or raw.get("Security"), "as_of_date": raw.get("as_of_date")})
        by_rule: dict[str, int] = {}
        for x in quarantined:
            by_rule[x["rule"]] = by_rule.get(x["rule"], 0) + 1
        data = {"run_id": row["id"], "run_date": run_date, "status": row["status"], "rows_in": row["rows_in"], "rows_quarantined": len(quarantined),
                "quarantined_by_rule": by_rule, "sources_failed": report.get("sources_failed", []), "quarantined": quarantined}
        return self._record("get_quality_report", {"run_date": run_date}, {"data": data, "source_rows": [f"pipeline_run:{row['id']}"] + [f"quarantine:{q['id']}" for q in quarantined], "warnings": []})

    def search_documents(self, query: str, manager_id: str | None = None, k: int = 5) -> dict:
        from provenance.copilot.retrieval import search_documents
        if manager_id:
            self._check_manager(manager_id)
        hits = search_documents(self.conn, query, self.user.allowed_managers, k, manager_id)
        return self._record("search_documents", {"query": query, "manager_id": manager_id, "k": k},
                            {"data": {"hits": hits}, "source_rows": [f"chunk:{h['document_id']}:{h['chunk_index']}" for h in hits], "warnings": []})


TOOL_SCHEMAS = [
    {"name": "get_pnl_attribution", "description": "Exact P&L for one manager over (start, end], total/realized/unrealized, by position and sector, with the trade and price rows used.",
     "input_schema": {"type": "object", "properties": {"manager_id": {"type": "string"}, "start": {"type": "string", "description": "ISO date, exclusive"}, "end": {"type": "string", "description": "ISO date, inclusive"}}, "required": ["manager_id", "start", "end"], "additionalProperties": False}, "strict": True},
    {"name": "get_exposure", "description": "Gross/net/long/short delta-adjusted exposure by sector and manager. scope is a manager_id or 'firm' (risk role only).",
     "input_schema": {"type": "object", "properties": {"scope": {"type": "string"}, "as_of": {"type": "string"}}, "required": ["scope", "as_of"], "additionalProperties": False}, "strict": True},
    {"name": "get_concentration", "description": "Firm-wide largest single names and sectors as a share of gross exposure (risk role only).",
     "input_schema": {"type": "object", "properties": {"as_of": {"type": "string"}}, "required": ["as_of"], "additionalProperties": False}, "strict": True},
    {"name": "get_positions", "description": "A manager's open positions ranked by absolute delta-adjusted exposure.",
     "input_schema": {"type": "object", "properties": {"manager_id": {"type": "string"}, "as_of": {"type": "string"}, "top_n": {"type": "integer"}}, "required": ["manager_id", "as_of", "top_n"], "additionalProperties": False}, "strict": True},
    {"name": "get_quality_report", "description": "Pipeline run status and quarantined rows (stale prices, duplicates, etc.) for a run date.",
     "input_schema": {"type": "object", "properties": {"run_date": {"type": "string"}}, "required": ["run_date"], "additionalProperties": False}, "strict": True},
    {"name": "search_documents", "description": "Permission-filtered search over research memos and filings. Returns chunks with document_id and chunk_index for citation.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "manager_id": {"type": ["string", "null"]}, "k": {"type": "integer"}}, "required": ["query", "manager_id", "k"], "additionalProperties": False}, "strict": True},
]
