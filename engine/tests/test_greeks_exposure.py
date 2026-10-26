from datetime import date, timedelta
from decimal import Decimal

from provenance.engine.exposure import concentration, exposure
from provenance.engine.greeks import greeks
from provenance.engine.tests.conftest import marks

D = Decimal


def test_black_scholes_textbook_values():
    # Hull example: S=42, K=40, r=10%, sigma=20%, T=0.5y -> call 4.76, put 0.81
    as_of = date(2026, 1, 1)
    expiry = as_of + timedelta(days=round(0.5 * 365))
    c = greeks("call", D(42), D(40), expiry, as_of, D("0.10"), D("0.20")).data
    p = greeks("put", D(42), D(40), expiry, as_of, D("0.10"), D("0.20")).data
    assert abs(c["price"] - D("4.76")) < D("0.02")
    assert abs(p["price"] - D("0.81")) < D("0.02")
    assert abs(c["delta"] - D("0.7791")) < D("0.002")
    assert abs(p["delta"] - (c["delta"] - 1)) < D("0.000001")   # put-call delta parity
    assert c["gamma"] == p["gamma"] and c["vega"] == p["vega"]
    assert c["theta"] < 0


def test_greeks_at_expiry_are_intrinsic():
    d = date(2026, 6, 19)
    g = greeks("call", D(110), D(100), d, d, D("0.05"), D("0.3")).data
    assert g["price"] == D(10) and g["delta"] == D(1)


def test_exposure_gross_net_and_delta_adjusted(trades, instruments):
    from datetime import datetime
    from provenance.engine.types import TradeRow
    # add 2 call contracts for manager X on AAA
    ts = trades + [TradeRow(7, "X", 4, D(2), D("5.00"), datetime(2026, 10, 3, 11, 0))]
    as_of = date(2026, 10, 3)
    m = marks({1: "11.50", 2: "18.00", 3: "4.50", 4: "5.00"}, as_of)
    res = exposure(["X"], as_of, ts, instruments, m, D("0.04"), D("0.30"))
    rows = {r["symbol"]: r for r in res.data["positions"]}
    assert rows["AAA"]["market_value"] == D("1035.00") and rows["CCC"]["market_value"] == D("900.00")
    assert "BBB" not in rows  # flat positions are excluded
    opt = rows["AAA261218C00000012"]
    assert opt["market_value"] == D("1000.00")           # 2 * 100 * 5
    assert D(0) < opt["delta"] < D(1)
    assert opt["delta_adjusted_exposure"] == (D(200) * opt["delta"] * D("11.50")).quantize(D("0.01"))
    assert res.data["net"] == res.data["long"] + res.data["short"]
    assert res.data["gross"] == sum(abs(r["delta_adjusted_exposure"]) for r in rows.values())
    sectors = {s["sector"]: s for s in res.data["by_sector"]}
    assert sectors["Health"]["gross"] == D("900.00")


def test_concentration_rolls_options_to_underlying(trades, instruments):
    from datetime import datetime
    from provenance.engine.types import TradeRow
    ts = trades + [TradeRow(7, "X", 4, D(2), D("5.00"), datetime(2026, 10, 3, 11, 0))]
    as_of = date(2026, 10, 3)
    m = marks({1: "11.50", 2: "18.00", 3: "4.50", 4: "5.00"}, as_of)
    res = concentration(["X"], as_of, ts, instruments, m, D("0.04"), D("0.30"))
    names = {n["name"] for n in res.data["largest_names"]}
    assert names == {"AAA", "CCC"}
    assert sum(s["pct_of_gross"] for s in res.data["largest_sectors"]) == D("100.00")
