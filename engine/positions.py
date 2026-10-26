"""Positions from trades using average cost. Realized P&L is booked when a position is reduced or flipped."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from provenance.engine.types import Position, Result, TradeRow

Q = Decimal("0.00000001")


def positions(trades: list[TradeRow], as_of: date, manager_id: str | None = None) -> Result:
    """Return {(manager_id, instrument_id): Position} built from all trades with trade_time.date() <= as_of."""
    cutoff = datetime.combine(as_of, datetime.max.time())
    book: dict[tuple[str, int], Position] = {}
    used: list[str] = []
    for t in sorted(trades, key=lambda x: (x.trade_time, x.id)):
        if t.trade_time > cutoff or (manager_id and t.manager_id != manager_id):
            continue
        key = (t.manager_id, t.instrument_id)
        p = book.setdefault(key, Position(t.manager_id, t.instrument_id))
        _apply(p, t)
        p.trade_ids.append(t.row_id)
        used.append(t.row_id)
    # drop flat positions but keep their realized P&L visible through trade ids
    return Result(data=book, source_rows=used)


def _apply(p: Position, t: TradeRow) -> None:
    q0, q1 = p.qty, t.qty
    if q0 == 0 or (q0 > 0) == (q1 > 0):
        # opening or adding in the same direction: weighted average cost
        new_qty = q0 + q1
        p.avg_cost = ((q0 * p.avg_cost + q1 * t.price) / new_qty).quantize(Q) if new_qty != 0 else Decimal(0)
        p.qty = new_qty
        return
    # reducing or flipping
    closed = min(abs(q0), abs(q1))
    direction = Decimal(1) if q0 > 0 else Decimal(-1)
    p.realized_pnl += (closed * (t.price - p.avg_cost) * direction)
    remaining = q0 + q1
    if remaining == 0:
        p.qty = Decimal(0); p.avg_cost = Decimal(0)
    elif (remaining > 0) == (q0 > 0):
        p.qty = remaining  # partial reduce, avg cost unchanged
    else:
        p.qty = remaining; p.avg_cost = t.price  # flipped: new position opened at trade price
