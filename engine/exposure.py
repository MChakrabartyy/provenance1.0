"""Exposure (gross, net, by sector) and firm-wide concentration. Options are delta-adjusted."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from provenance.engine.greeks import greeks
from provenance.engine.positions import positions
from provenance.engine.types import Instrument, Mark, Result, TradeRow

CENT = Decimal("0.01")


def position_exposures(manager_ids: list[str], as_of: date, trades: list[TradeRow], instruments: dict[int, Instrument],
                       marks: dict[int, Mark], rate: Decimal, vol: Decimal) -> Result:
    """One row per open position with market value and delta-adjusted exposure."""
    res = Result(data=[], source_rows=[])
    for mid in manager_ids:
        pos = positions(trades, as_of, mid)
        for (m, iid), p in pos.data.items():
            if p.qty == 0:
                continue
            inst = instruments[iid]
            mark = marks.get(iid)
            if mark is None:
                res.warnings.append(f"No close for {inst.symbol} on {as_of}; position excluded from exposure")
                continue
            res.add_rows(p.trade_ids); res.add_rows([mark.row_id])
            if mark.flagged:
                res.warnings.append(f"{inst.symbol} {as_of}: {mark.flagged}")
            if mark.price_date != as_of:
                res.warnings.append(f"{inst.symbol}: no close on {as_of}, marked with {mark.price_date} close")
            mv = (p.qty * mark.close * inst.multiplier).quantize(CENT)
            delta = Decimal(1)
            delta_adj = mv
            if inst.type == "option" and inst.underlying_id is not None:
                spot_mark = marks.get(inst.underlying_id)
                if spot_mark is not None and inst.strike is not None and inst.expiry is not None:
                    g = greeks(inst.call_put or "call", spot_mark.close, inst.strike, inst.expiry, as_of, rate, vol)
                    delta = g.data["delta"]
                    delta_adj = (p.qty * inst.multiplier * delta * spot_mark.close).quantize(CENT)
                    res.add_rows([spot_mark.row_id])
                else:
                    res.warnings.append(f"{inst.symbol}: no underlying close; delta-adjusted exposure uses option market value")
            res.data.append({"manager_id": m, "instrument_id": iid, "symbol": inst.symbol, "name": inst.name_key, "sector": inst.sector,
                             "type": inst.type, "qty": p.qty, "avg_cost": p.avg_cost, "close": mark.close, "market_value": mv,
                             "delta": delta, "delta_adjusted_exposure": delta_adj, "unrealized_pnl": ((mark.close - p.avg_cost) * p.qty * inst.multiplier).quantize(CENT)})
    return res


def exposure(manager_ids: list[str], as_of: date, trades, instruments, marks, rate, vol) -> Result:
    res = position_exposures(manager_ids, as_of, trades, instruments, marks, rate, vol)
    rows = res.data
    gross = sum((abs(r["delta_adjusted_exposure"]) for r in rows), Decimal(0))
    net = sum((r["delta_adjusted_exposure"] for r in rows), Decimal(0))
    long_ = sum((r["delta_adjusted_exposure"] for r in rows if r["delta_adjusted_exposure"] > 0), Decimal(0))
    short = sum((r["delta_adjusted_exposure"] for r in rows if r["delta_adjusted_exposure"] < 0), Decimal(0))
    by_sector: dict[str, dict] = {}
    by_manager: dict[str, dict] = {}
    for r in rows:
        s = by_sector.setdefault(r["sector"], {"sector": r["sector"], "gross": Decimal(0), "net": Decimal(0)})
        s["gross"] += abs(r["delta_adjusted_exposure"]); s["net"] += r["delta_adjusted_exposure"]
        m = by_manager.setdefault(r["manager_id"], {"manager_id": r["manager_id"], "gross": Decimal(0), "net": Decimal(0)})
        m["gross"] += abs(r["delta_adjusted_exposure"]); m["net"] += r["delta_adjusted_exposure"]
    res.data = {"scope": manager_ids if len(manager_ids) > 1 else manager_ids[0], "as_of": as_of.isoformat(),
                "gross": gross.quantize(CENT), "net": net.quantize(CENT), "long": long_.quantize(CENT), "short": short.quantize(CENT),
                "by_sector": sorted(by_sector.values(), key=lambda x: -x["gross"]),
                "by_manager": sorted(by_manager.values(), key=lambda x: -x["gross"]),
                "positions": sorted(rows, key=lambda r: -abs(r["delta_adjusted_exposure"]))}
    return res


def concentration(manager_ids: list[str], as_of: date, trades, instruments, marks, rate, vol, top_n: int = 5) -> Result:
    res = exposure(manager_ids, as_of, trades, instruments, marks, rate, vol)
    gross = res.data["gross"] or Decimal(1)
    names: dict[str, Decimal] = {}
    for r in res.data["positions"]:
        names[r["name"]] = names.get(r["name"], Decimal(0)) + r["delta_adjusted_exposure"]
    top_names = sorted(({"name": n, "net_exposure": v.quantize(CENT), "pct_of_gross": (abs(v) / gross * 100).quantize(CENT)} for n, v in names.items()),
                       key=lambda x: -abs(x["net_exposure"]))[:top_n]
    top_sectors = [{"sector": s["sector"], "gross": s["gross"].quantize(CENT), "net": s["net"].quantize(CENT), "pct_of_gross": (s["gross"] / gross * 100).quantize(CENT)}
                   for s in res.data["by_sector"][:top_n]]
    res.data = {"as_of": as_of.isoformat(), "firm_gross": res.data["gross"], "firm_net": res.data["net"],
                "largest_names": top_names, "largest_sectors": top_sectors}
    return res
