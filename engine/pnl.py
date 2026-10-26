"""P&L over a period, marked to close prices.

total P&L per instrument over (start, end] =
    qty_end * close_end * mult  -  qty_start * close_start * mult  -  sum(qty_i * price_i * mult for trades in period)
i.e. change in market value minus net cash invested. This is exact, needs no cost basis, and is
reproducible from rows alone. Realized comes from average cost; unrealized = total - realized.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from provenance.engine.positions import positions
from provenance.engine.types import Instrument, Mark, Result, TradeRow

CENT = Decimal("0.01")


def pnl(manager_id: str, start: date, end: date, trades: list[TradeRow], instruments: dict[int, Instrument],
        marks_start: dict[int, Mark], marks_end: dict[int, Mark]) -> Result:
    pos_start = positions(trades, start, manager_id)
    pos_end = positions(trades, end, manager_id)
    res = Result(data={}, source_rows=[])
    res.add_rows(pos_end.source_rows)
    lo = datetime.combine(start, datetime.max.time())
    hi = datetime.combine(end, datetime.max.time())
    inst_ids = {k[1] for k in pos_start.data} | {k[1] for k in pos_end.data} | {t.instrument_id for t in trades if t.manager_id == manager_id and lo < t.trade_time <= hi}
    per_instrument: dict[int, dict] = {}
    for iid in inst_ids:
        inst = instruments[iid]
        mult = inst.multiplier
        ps = pos_start.data.get((manager_id, iid)); pe = pos_end.data.get((manager_id, iid))
        q0 = ps.qty if ps else Decimal(0); q1 = pe.qty if pe else Decimal(0)
        mv0 = Decimal(0); mv1 = Decimal(0)
        if q0 != 0:
            m0 = marks_start.get(iid)
            if m0 is None:
                res.warnings.append(f"No close for {inst.symbol} on {start}; start value treated as 0"); 
            else:
                mv0 = q0 * m0.close * mult; res.add_rows([m0.row_id]); _flag(res, m0, inst)
        if q1 != 0:
            m1 = marks_end.get(iid)
            if m1 is None:
                res.warnings.append(f"No close for {inst.symbol} on {end}; end value treated as 0")
            else:
                mv1 = q1 * m1.close * mult; res.add_rows([m1.row_id]); _flag(res, m1, inst)
        cash = sum((t.qty * t.price * mult for t in trades if t.manager_id == manager_id and t.instrument_id == iid and lo < t.trade_time <= hi), Decimal(0))
        total = (mv1 - mv0 - cash).quantize(CENT)
        realized = ((pe.realized_pnl if pe else Decimal(0)) - (ps.realized_pnl if ps else Decimal(0))).quantize(CENT)
        per_instrument[iid] = {"symbol": inst.symbol, "sector": inst.sector, "total": total, "realized": realized,
                               "unrealized": (total - realized).quantize(CENT), "qty_start": q0, "qty_end": q1}
    res.data = {
        "manager_id": manager_id, "start": start.isoformat(), "end": end.isoformat(),
        "total": sum((v["total"] for v in per_instrument.values()), Decimal(0)).quantize(CENT),
        "realized": sum((v["realized"] for v in per_instrument.values()), Decimal(0)).quantize(CENT),
        "unrealized": sum((v["unrealized"] for v in per_instrument.values()), Decimal(0)).quantize(CENT),
        "by_instrument": per_instrument,
    }
    return res


def _flag(res: Result, m: Mark, inst: Instrument) -> None:
    if m.flagged:
        res.warnings.append(f"{inst.symbol} {m.as_of_date}: {m.flagged}")
    if m.price_date != m.as_of_date:
        res.warnings.append(f"{inst.symbol}: no close on {m.as_of_date}, marked with {m.price_date} close")
