"""P&L attribution: by position and by sector, sorted by contribution."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from provenance.engine.pnl import pnl
from provenance.engine.types import Instrument, Mark, Result, TradeRow


def attribution(manager_id: str, start: date, end: date, trades: list[TradeRow], instruments: dict[int, Instrument],
                marks_start: dict[int, Mark], marks_end: dict[int, Mark]) -> Result:
    res = pnl(manager_id, start, end, trades, instruments, marks_start, marks_end)
    by_inst = res.data["by_instrument"]
    by_position = sorted(
        ({"instrument_id": iid, "symbol": v["symbol"], "sector": v["sector"], "pnl": v["total"]} for iid, v in by_inst.items()),
        key=lambda r: r["pnl"])
    sectors: dict[str, Decimal] = {}
    for v in by_inst.values():
        sectors[v["sector"]] = sectors.get(v["sector"], Decimal(0)) + v["total"]
    by_sector = sorted(({"sector": s, "pnl": p} for s, p in sectors.items()), key=lambda r: r["pnl"])
    res.data = {"manager_id": manager_id, "start": start.isoformat(), "end": end.isoformat(), "total": res.data["total"],
                "realized": res.data["realized"], "unrealized": res.data["unrealized"],
                "by_position": by_position, "by_sector": by_sector,
                "top_detractors": by_position[:5], "top_contributors": list(reversed(by_position[-5:]))}
    return res
