"""One pipeline run: load reference data, prices, trades; apply quality rules; derive positions; write the audit row.

Idempotency:
  * a file is identified by its content hash; a file already ingested by a completed run is skipped
  * trades are unique on (manager_id, external_trade_id); a resent file with the same ids is quarantined as duplicates
  * prices and positions are upserted on their natural keys, so recomputing a date replaces rather than appends
Failure handling:
  * a price source that fails after retries makes the run `partial` and is named in the report; it is never skipped silently
  * any unexpected exception marks the run `failed` with the error text
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from provenance.config import DATA_DIR
from provenance.db import connect, migrate, transaction
from provenance.engine.loader import load_trades
from provenance.engine.positions import positions as compute_positions
from provenance.ingestion.adapters.registry import ADAPTERS, FILE_LAYOUT
from provenance.ingestion.observability import RunLogger, alert
from provenance.ingestion.prices import PRIMARY_SOURCE, SOURCES, SourceError, fetch_rates, fetch_source
from provenance.ingestion.quality import QualityContext, check_price, check_trade, source_disagreement
from provenance.models import PriceRow, QualityFailure, Trade

SCHEMA_EXTRA = """
CREATE TABLE IF NOT EXISTS ingested_files (
  path TEXT PRIMARY KEY, content_hash TEXT NOT NULL, run_id INTEGER NOT NULL, rows INTEGER NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return str(o)


def load_reference(conn: sqlite3.Connection, data_dir: Path) -> None:
    with (data_dir / "managers.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            conn.execute("INSERT INTO managers VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, pod=excluded.pod, strategy=excluded.strategy",
                         (r["id"], r["name"], r["pod"], r["strategy"], r["onboarded_at"]))
    with (data_dir / "instruments.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            conn.execute("""INSERT INTO instruments (symbol,type,underlying,strike,expiry,call_put,sector) VALUES (?,?,?,?,?,?,?)
                            ON CONFLICT(symbol) DO UPDATE SET sector=excluded.sector""",
                         (r["symbol"], r["type"], r["underlying"] or None, r["strike"] or None, r["expiry"] or None, r["call_put"] or None, r["sector"]))
    with (data_dir / "users.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            conn.execute("INSERT INTO users (email, role, manager_id, token) VALUES (?,?,?,?) ON CONFLICT(email) DO UPDATE SET role=excluded.role, manager_id=excluded.manager_id, token=excluded.token",
                         (r["email"], r["role"], r["manager_id"] or None, r["token"]))


def quarantine(conn, run_id: int, table: str, raw: dict, failure: QualityFailure, manager_id: str | None) -> None:
    conn.execute("INSERT INTO quarantine (run_id, table_name, raw_row, rule, reason, manager_id, created_at) VALUES (?,?,?,?,?,?,?)",
                 (run_id, table, json.dumps(raw, default=_json), failure.rule, failure.reason, manager_id, _now()))


def ingest_prices(conn, run_id: int, data_dir: Path, run_date: date, ctx: QualityContext, report: dict, log: RunLogger) -> list[str]:
    """Returns the list of sources that failed."""
    failed: list[str] = []
    passed: list[PriceRow] = []
    for source in SOURCES:
        try:
            rows = fetch_source(data_dir, source, run_date)
        except SourceError as e:
            failed.append(source); log.log("source_failed", source=source, error=str(e)); alert("price source failed", run_id=run_id, source=source)
            continue
        rows.sort(key=lambda r: (r.symbol, r.as_of_date))
        # prior-close context: only closes strictly before the first date in this batch (so a full replay is clean)
        first = min((r.as_of_date for r in rows), default=run_date).isoformat()
        for k in [k for k in ctx.prior_close if k[1] == source]:
            del ctx.prior_close[k]
        for r in conn.execute("SELECT i.symbol, p.close FROM prices p JOIN instruments i ON i.id=p.instrument_id WHERE p.source=? AND p.as_of_date < ? ORDER BY p.as_of_date", (source, first)):
            ctx.prior_close[(r["symbol"], source)] = Decimal(str(r["close"]))
        for r in rows:
            report["rows_in"] += 1
            f = check_price(r, ctx)
            if f:
                report["quarantined_by_rule"][f.rule] = report["quarantined_by_rule"].get(f.rule, 0) + 1
                quarantine(conn, run_id, "prices", {**r.raw, "symbol": r.symbol, "as_of_date": r.as_of_date.isoformat(), "source": r.source}, f, None)
            else:
                passed.append(r)
    for r, f in source_disagreement(passed, ctx, PRIMARY_SOURCE):
        report["quarantined_by_rule"][f.rule] = report["quarantined_by_rule"].get(f.rule, 0) + 1
        quarantine(conn, run_id, "prices", {**r.raw, "symbol": r.symbol, "as_of_date": r.as_of_date.isoformat(), "source": r.source}, f, None)
        passed.remove(r)
    inst = {k: v["id"] for k, v in ctx.known_instruments.items()}
    now = _now()
    conn.executemany("""INSERT INTO prices (instrument_id, as_of_date, close, source, fetched_at, run_id) VALUES (?,?,?,?,?,?)
                        ON CONFLICT(instrument_id, as_of_date, source) DO UPDATE SET close=excluded.close, fetched_at=excluded.fetched_at, run_id=excluded.run_id""",
                     [(inst[r.symbol], r.as_of_date.isoformat(), str(r.close), r.source, now, run_id) for r in passed])
    report["prices_stored"] = len(passed)
    return failed


def ingest_rates(conn, data_dir: Path, run_date: date, report: dict) -> None:
    try:
        rows = fetch_rates(data_dir, run_date)
    except SourceError as e:
        report["sources_failed"].append("rates"); report["notes"].append(str(e)); return
    conn.executemany("INSERT INTO rates VALUES (?,?,?) ON CONFLICT(as_of_date, tenor) DO UPDATE SET rate=excluded.rate",
                     [(r["as_of_date"], r["tenor"], r["rate"]) for r in rows])


def ingest_trades(conn, run_id: int, data_dir: Path, run_date: date, ctx: QualityContext, report: dict, log: RunLogger) -> None:
    ctx.existing_trade_keys = {(r["manager_id"], r["external_trade_id"]) for r in conn.execute("SELECT manager_id, external_trade_id FROM trades")}
    already = {r["path"]: r["content_hash"] for r in conn.execute("SELECT path, content_hash FROM ingested_files")}
    inst = {k: v["id"] for k, v in ctx.known_instruments.items()}
    for mid, adapter in ADAPTERS.items():
        folder, pattern = FILE_LAYOUT[mid]
        for path in sorted((data_dir / folder).glob(pattern)):
            file_date = _date_in_name(path.name)
            if file_date and file_date > run_date:
                continue
            h = _hash(path)
            rel = str(path.relative_to(data_dir))
            if already.get(rel) == h:
                report["files_skipped"] += 1
                continue
            n = 0
            for raw in adapter.parse(path):
                n += 1; report["rows_in"] += 1
                t: Trade = adapter.to_standard(raw)
                f = check_trade(t, ctx)
                if f:
                    report["quarantined_by_rule"][f.rule] = report["quarantined_by_rule"].get(f.rule, 0) + 1
                    quarantine(conn, run_id, "trades", {"manager_id": mid, "source_file": raw.source_file, "row_number": raw.row_number, **raw.row}, f, mid)
                    continue
                conn.execute("INSERT INTO trades (manager_id, instrument_id, qty, price, trade_time, source_file, run_id, external_trade_id) VALUES (?,?,?,?,?,?,?,?)",
                             (mid, inst[t.symbol], str(t.qty), str(t.price), t.trade_time.isoformat(), rel, run_id, t.external_trade_id))
                report["trades_stored"] += 1
            conn.execute("INSERT INTO ingested_files VALUES (?,?,?,?) ON CONFLICT(path) DO UPDATE SET content_hash=excluded.content_hash, run_id=excluded.run_id, rows=excluded.rows", (rel, h, run_id, n))
            report["files_processed"] += 1
            log.log("file_ingested", manager_id=mid, file=rel, rows=n)


def _date_in_name(name: str) -> date | None:
    import re
    m = re.search(r"(\d{4}-\d{2}-\d{2})", name)
    return date.fromisoformat(m.group(1)) if m else None


def derive_positions(conn, run_id: int, run_date: date, report: dict) -> None:
    days = [date.fromisoformat(r["as_of_date"]) for r in conn.execute("SELECT DISTINCT as_of_date FROM prices WHERE as_of_date <= ? ORDER BY as_of_date", (run_date.isoformat(),))]
    trades = load_trades(conn, None, run_date)
    rows = []
    for d in days:
        for (mid, iid), p in compute_positions(trades, d).data.items():
            rows.append((mid, iid, d.isoformat(), str(p.qty), str(p.avg_cost), run_id))
    conn.executemany("""INSERT INTO positions (manager_id, instrument_id, as_of_date, qty, avg_cost, run_id) VALUES (?,?,?,?,?,?)
                        ON CONFLICT(manager_id, instrument_id, as_of_date) DO UPDATE SET qty=excluded.qty, avg_cost=excluded.avg_cost, run_id=excluded.run_id""", rows)
    report["positions_written"] = len(rows)


def run(run_date: date | None = None, data_dir: Path = DATA_DIR, conn: sqlite3.Connection | None = None) -> dict:
    own = conn is None
    conn = conn or connect()
    migrate(conn); conn.executescript(SCHEMA_EXTRA)
    if run_date is None:
        cal = json.loads((data_dir / "calendar.json").read_text())
        run_date = date.fromisoformat(cal[-1])
    cur = conn.execute("INSERT INTO pipeline_runs (run_date, started_at, status) VALUES (?,?,?)", (run_date.isoformat(), _now(), "running"))
    run_id = cur.lastrowid
    log = RunLogger(run_id)
    log.log("run_started", run_date=run_date.isoformat())
    done = log.timer()
    report = {"run_id": run_id, "run_date": run_date.isoformat(), "rows_in": 0, "rows_passed": 0, "quarantined_by_rule": {}, "sources_failed": [],
              "files_processed": 0, "files_skipped": 0, "trades_stored": 0, "prices_stored": 0, "positions_written": 0, "notes": []}
    status = "success"
    try:
        with transaction(conn):
            load_reference(conn, data_dir)
            known = {r["symbol"]: dict(r) for r in conn.execute("SELECT * FROM instruments")}
            ctx = QualityContext(known_instruments=known)
            # reruns replace this date's quarantine rows for prices (they are recomputed every run)
            conn.execute("DELETE FROM quarantine WHERE table_name='prices' AND run_id IN (SELECT id FROM pipeline_runs WHERE run_date=? AND id<>?)", (run_date.isoformat(), run_id))
            failed = ingest_prices(conn, run_id, data_dir, run_date, ctx, report, log)
            report["sources_failed"] += failed
            ingest_rates(conn, data_dir, run_date, report)
            ingest_trades(conn, run_id, data_dir, run_date, ctx, report, report and log)
            derive_positions(conn, run_id, run_date, report)
        if report["sources_failed"]:
            status = "partial"
    except Exception as e:  # noqa: BLE001
        status = "failed"; report["error"] = f"{type(e).__name__}: {e}"
        log.log("run_failed", error=report["error"]); alert("pipeline run failed", run_id=run_id, error=report["error"])
    rows_q = sum(report["quarantined_by_rule"].values())
    report["rows_passed"] = report["rows_in"] - rows_q
    report["duration_ms"] = done()
    conn.execute("UPDATE pipeline_runs SET finished_at=?, status=?, rows_in=?, rows_quarantined=?, error=?, report=? WHERE id=?",
                 (_now(), status, report["rows_in"], rows_q, report.get("error"), json.dumps(report, default=_json), run_id))
    report["status"] = status
    log.log("run_finished", status=status, rows_in=report["rows_in"], rows_quarantined=rows_q, duration_ms=report["duration_ms"], quarantined_by_rule=report["quarantined_by_rule"])
    if rows_q > 50:
        alert("quarantine spike", run_id=run_id, rows_quarantined=rows_q)
    if own:
        conn.close()
    return report


if __name__ == "__main__":
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    print(json.dumps(run(d), indent=2, default=_json))
