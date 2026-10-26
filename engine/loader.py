"""The only engine-adjacent module that talks to the database. Loads inputs, calls pure functions."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal

from provenance.config import DEFAULT_VOL
from provenance.db import D
from provenance.engine.types import Instrument, Mark, TradeRow
from provenance.ingestion.prices import PRIMARY_SOURCE


def load_instruments(conn: sqlite3.Connection) -> dict[int, Instrument]:
    rows = conn.execute("SELECT * FROM instruments").fetchall()
    by_symbol = {r["symbol"]: r["id"] for r in rows}
    out: dict[int, Instrument] = {}
    for r in rows:
        out[r["id"]] = Instrument(
            id=r["id"], symbol=r["symbol"], type=r["type"], sector=r["sector"], underlying=r["underlying"] or None,
            underlying_id=by_symbol.get(r["underlying"]) if r["underlying"] else None,
            strike=D(r["strike"]) if r["strike"] not in (None, "") else None,
            expiry=date.fromisoformat(r["expiry"]) if r["expiry"] else None, call_put=r["call_put"] or None)
    return out


def load_trades(conn: sqlite3.Connection, manager_ids: list[str] | None, through: date) -> list[TradeRow]:
    sql = "SELECT id, manager_id, instrument_id, qty, price, trade_time FROM trades WHERE trade_time <= ?"
    params: list = [datetime.combine(through, datetime.max.time()).isoformat()]
    if manager_ids:
        sql += f" AND manager_id IN ({','.join('?' * len(manager_ids))})"; params += manager_ids
    return [TradeRow(r["id"], r["manager_id"], r["instrument_id"], D(r["qty"]), D(r["price"]), datetime.fromisoformat(r["trade_time"]))
            for r in conn.execute(sql + " ORDER BY trade_time, id", params)]


def load_marks(conn: sqlite3.Connection, as_of: date, lookback_days: int = 7) -> dict[int, Mark]:
    """Close per instrument for as_of: primary source first, then secondary, then the most recent prior close.

    Any quarantined price row for (instrument, as_of) attaches a warning so callers can surface it.
    """
    flagged: dict[int, str] = {}
    for q in conn.execute("SELECT raw_row, rule, reason FROM quarantine WHERE table_name='prices' AND raw_row LIKE ?", (f'%"as_of_date": "{as_of.isoformat()}"%',)):
        import json
        raw = json.loads(q["raw_row"])
        iid = conn.execute("SELECT id FROM instruments WHERE symbol=?", (raw.get("symbol"),)).fetchone()
        if iid:
            flagged[iid["id"]] = f"price flagged {q['rule']} ({q['reason']})"
    rows = conn.execute(
        "SELECT instrument_id, as_of_date, close, source FROM prices WHERE as_of_date <= ? AND as_of_date >= date(?, ?) ORDER BY as_of_date DESC, CASE source WHEN ? THEN 0 ELSE 1 END",
        (as_of.isoformat(), as_of.isoformat(), f"-{lookback_days} days", PRIMARY_SOURCE)).fetchall()
    marks: dict[int, Mark] = {}
    for r in rows:
        iid = r["instrument_id"]
        if iid in marks:
            continue
        marks[iid] = Mark(iid, as_of, date.fromisoformat(r["as_of_date"]), D(r["close"]), r["source"], flagged.get(iid))
    return marks


def load_rate(conn: sqlite3.Connection, as_of: date, tenor: str = "3M") -> Decimal:
    r = conn.execute("SELECT rate FROM rates WHERE tenor=? AND as_of_date <= ? ORDER BY as_of_date DESC LIMIT 1", (tenor, as_of.isoformat())).fetchone()
    return D(r["rate"]) if r else Decimal("0.04")


def default_vol() -> Decimal:
    return Decimal(DEFAULT_VOL)


def latest_run_date(conn: sqlite3.Connection) -> date | None:
    r = conn.execute("SELECT run_date FROM pipeline_runs WHERE status IN ('success','partial') ORDER BY run_date DESC, id DESC LIMIT 1").fetchone()
    return date.fromisoformat(r["run_date"]) if r else None


def trading_days(conn: sqlite3.Connection) -> list[date]:
    return [date.fromisoformat(r["as_of_date"]) for r in conn.execute("SELECT DISTINCT as_of_date FROM prices ORDER BY as_of_date")]
