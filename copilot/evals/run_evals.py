"""Run the eval set and print a scorecard. One command: python -m provenance.copilot.evals.run_evals

Pass conditions by category:
  numeric      every listed figure matches the engine's own recomputation exactly, and no unbacked numbers
  retrieval    cites the expected document (and chunk when given)
  quality      includes the required warning / mentions the rule
  permission   refuses, and leaks nothing from the other pod
  out_of_scope declines
Tracked: numeric accuracy, citation correctness, required-warning rate, refusal correctness,
hallucinated-number rate (target 0), median and p95 latency.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import time
from decimal import Decimal
from pathlib import Path

import yaml

from provenance.copilot.agent import ask, money, pct
from provenance.copilot.answer import number_check
from provenance.copilot.tools import Tools, User
from provenance.db import connect

HERE = Path(__file__).resolve().parent


def user_for(conn, token: str) -> User:
    r = conn.execute("SELECT id, email, role, manager_id FROM users WHERE token=?", (token,)).fetchone()
    return User(r["id"], r["email"], r["role"], r["manager_id"])


def resolve_path(data, path: str):
    """total | by_sector[sector=Semiconductors].net | positions[0].delta_adjusted_exposure"""
    cur = data
    for part in re.findall(r"[^.\[\]]+(?:\[[^\]]+\])?", path):
        m = re.match(r"([^\[]+)(?:\[([^\]]+)\])?$", part)
        key, idx = m.group(1), m.group(2)
        cur = cur[key]
        if idx is not None:
            if "=" in idx:
                k, v = idx.split("=", 1); cur = next(x for x in cur if str(x[k]) == v)
            else:
                cur = cur[int(idx)]
    return cur


def expected_value(conn, user: User, spec: dict, dates: dict) -> str:
    t = Tools(conn, user)
    args = {k: (v.format(**dates) if isinstance(v, str) else v) for k, v in spec["args"].items()}
    res = getattr(t, spec["tool"])(**args)
    v = resolve_path(res["data"], spec["path"])
    return money(v) if spec.get("format") == "money" else pct(v) if spec.get("format") == "pct" else str(v)


def grade(conn, user: User, q: dict, ans, dates: dict) -> tuple[bool, str]:
    e = q["expect"]
    notes = []
    ok = True
    if "confidence" in e and ans.confidence != e["confidence"]:
        ok = False; notes.append(f"confidence {ans.confidence} != {e['confidence']}")
    if "figure_count" in e and len(ans.figures) != e["figure_count"]:
        ok = False; notes.append(f"{len(ans.figures)} figures != {e['figure_count']}")
    for fs in e.get("figures", []):
        want = expected_value(conn, user_for(conn, "tok-risk"), fs, dates)  # recompute with full access
        if "label" in fs:
            got = next((f for f in ans.figures if f.label == fs["label"]), None)
        else:
            got = ans.figures[fs["label_index"]] if len(ans.figures) > fs["label_index"] else None
        if got is None:
            ok = False; notes.append(f"missing figure {fs.get('label', fs.get('label_index'))}")
        elif got.value != want:
            ok = False; notes.append(f"{got.label}: {got.value} != engine {want}")
        elif not got.source_rows:
            ok = False; notes.append(f"{got.label} has no source rows")
    titles = [c.title or "" for c in ans.citations]
    if "cites_title" in e and not any(e["cites_title"] in t for t in titles):
        ok = False; notes.append(f"no citation matching {e['cites_title']!r} in {titles}")
    if "cites_chunk" in e and not any(c.chunk_index == e["cites_chunk"] and e.get("cites_title", "") in (c.title or "") for c in ans.citations):
        ok = False; notes.append(f"expected chunk {e['cites_chunk']}")
    if "not_cites_title" in e and any(e["not_cites_title"] in t for t in titles):
        ok = False; notes.append(f"leaked citation {e['not_cites_title']}")
    for k in ("answer_contains", "answer_contains_2"):
        if k in e and e[k].lower() not in ans.answer.lower():
            ok = False; notes.append(f"answer lacks {e[k]!r}")
    if "answer_not_contains" in e and e["answer_not_contains"].lower() in ans.answer.lower():
        ok = False; notes.append(f"answer leaks {e['answer_not_contains']!r}")
    if "warning_contains" in e and not any(e["warning_contains"] in w for w in ans.warnings):
        ok = False; notes.append(f"no warning containing {e['warning_contains']!r}")
    return ok, "; ".join(notes)


def main(out_path: Path | None = None) -> int:
    conn = connect()
    t = Tools(conn, user_for(conn, "tok-risk"))
    as_of = t.latest_date()
    dates = {"as_of": as_of.isoformat(), "week": t.trading_days_back(as_of, 5).isoformat(), "day": t.trading_days_back(as_of, 1).isoformat(), "month": t.trading_days_back(as_of, 21).isoformat()}
    questions = yaml.safe_load((HERE / "questions.yaml").read_text())
    results, latencies = [], []
    for q in questions:
        user = user_for(conn, q["user"])
        t0 = time.perf_counter()
        ans = ask(conn, user, q["question"])
        ms = int((time.perf_counter() - t0) * 1000); latencies.append(ms)
        ok, notes = grade(conn, user, q, ans, dates)
        halluc = bool(number_check(ans)) and ans.confidence != "refused"
        results.append({"id": q["id"], "category": q["category"], "pass": ok, "notes": notes, "latency_ms": ms, "hallucinated_numbers": halluc,
                        "confidence": ans.confidence, "question": q["question"], "answer": ans.answer})
    cats = {}
    for r in results:
        c = cats.setdefault(r["category"], {"n": 0, "pass": 0}); c["n"] += 1; c["pass"] += r["pass"]
    total = len(results); passed = sum(r["pass"] for r in results)
    lat = sorted(latencies)
    card = {
        "as_of": as_of.isoformat(), "total": total, "passed": passed, "score_pct": round(100 * passed / total, 1),
        "numeric_accuracy_pct": round(100 * cats["numeric"]["pass"] / cats["numeric"]["n"], 1),
        "citation_correctness_pct": round(100 * cats["retrieval"]["pass"] / cats["retrieval"]["n"], 1),
        "required_warning_rate_pct": round(100 * cats["quality"]["pass"] / cats["quality"]["n"], 1),
        "refusal_correctness_pct": round(100 * (cats["permission"]["pass"] + cats["out_of_scope"]["pass"]) / (cats["permission"]["n"] + cats["out_of_scope"]["n"]), 1),
        "hallucinated_number_rate_pct": round(100 * sum(r["hallucinated_numbers"] for r in results) / total, 1),
        "latency_ms_median": int(statistics.median(lat)), "latency_ms_p95": lat[min(len(lat) - 1, int(0.95 * len(lat)))],
        "by_category": cats,
    }
    print(f"\nProvenance copilot scorecard  (data as of {card['as_of']})")
    print("-" * 64)
    for k in ("numeric_accuracy_pct", "citation_correctness_pct", "required_warning_rate_pct", "refusal_correctness_pct", "hallucinated_number_rate_pct"):
        print(f"{k:34s} {card[k]:6.1f}%")
    print(f"{'latency median / p95 (ms)':34s} {card['latency_ms_median']} / {card['latency_ms_p95']}")
    print(f"{'overall':34s} {passed}/{total} ({card['score_pct']}%)")
    print("-" * 64)
    for r in results:
        flag = "PASS" if r["pass"] else "FAIL"
        print(f"{flag} {r['id']:4s} {r['category']:12s} {r['latency_ms']:5d}ms  {r['question'][:60]}")
        if not r["pass"]:
            print(f"       -> {r['notes']}")
    out = out_path or HERE / "last_scorecard.json"
    out.write_text(json.dumps({"scorecard": card, "results": results}, indent=2))
    print(f"\nwrote {out}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
