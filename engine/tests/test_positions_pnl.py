from datetime import date
from decimal import Decimal

from provenance.engine.attribution import attribution
from provenance.engine.pnl import pnl
from provenance.engine.positions import positions
from provenance.engine.tests.conftest import marks

D = Decimal


def test_positions_average_cost_and_realized(trades):
    book = positions(trades, date(2026, 10, 3), "X").data
    aaa = book[("X", 1)]
    assert aaa.qty == D(90)
    assert aaa.avg_cost == D("10.66666667")
    assert aaa.realized_pnl.quantize(D("0.01")) == D("20.00")
    bbb = book[("X", 2)]
    assert bbb.qty == 0 and bbb.realized_pnl == D(100)
    assert book[("X", 3)].qty == D(200)


def test_positions_cutoff_is_inclusive(trades):
    book = positions(trades, date(2026, 10, 1), "X").data
    assert book[("X", 1)].qty == D(100) and ("X", 2) in book


def test_positions_cite_trade_ids(trades):
    res = positions(trades, date(2026, 10, 3), "X")
    assert res.source_rows == [f"trade:{i}" for i in range(1, 7)]


def test_pnl_hand_calculated(trades, instruments):
    # Period (Oct 1, Oct 3]. Start positions: AAA 100, BBB -50, CCC 200.
    start = marks({1: "10.00", 2: "20.00", 3: "5.00"}, date(2026, 10, 1))
    end = marks({1: "11.50", 2: "18.00", 3: "4.50"}, date(2026, 10, 3))
    res = pnl("X", date(2026, 10, 1), date(2026, 10, 3), trades, instruments, start, end)
    bi = res.data["by_instrument"]
    # AAA: MV_end 90*11.5=1035; MV_start 100*10=1000; cash = 50*12 - 60*11 = 600-660 = -60 => 1035-1000+60 = 95
    assert bi[1]["total"] == D("95.00")
    # BBB: MV_end 0; MV_start -50*20=-1000; cash 50*18=900 => 0+1000-900 = 100 (all realized)
    assert bi[2]["total"] == D("100.00") and bi[2]["realized"] == D("100.00") and bi[2]["unrealized"] == D("0.00")
    # CCC: 200*4.5 - 200*5 = -100
    assert bi[3]["total"] == D("-100.00")
    assert res.data["total"] == D("95.00")
    assert res.data["realized"] == D("120.00")  # 20 + 100
    assert res.data["unrealized"] == D("-25.00")
    assert "price:1:2026-10-03:primary_synth" in res.source_rows and "trade:5" in res.source_rows


def test_attribution_sorted_and_by_sector(trades, instruments):
    start = marks({1: "10.00", 2: "20.00", 3: "5.00"}, date(2026, 10, 1))
    end = marks({1: "11.50", 2: "18.00", 3: "4.50"}, date(2026, 10, 3))
    res = attribution("X", date(2026, 10, 1), date(2026, 10, 3), trades, instruments, start, end)
    assert [r["symbol"] for r in res.data["by_position"]] == ["CCC", "AAA", "BBB"]
    sectors = {r["sector"]: r["pnl"] for r in res.data["by_sector"]}
    assert sectors == {"Health": D("-100.00"), "Tech": D("195.00")}
    assert res.data["top_detractors"][0]["symbol"] == "CCC"


def test_decimal_precision_sum_of_many_small_trades():
    from datetime import datetime
    from provenance.engine.types import TradeRow
    ts = [TradeRow(i, "X", 1, D(1), D("0.10"), datetime(2026, 10, 1, 9, i % 60)) for i in range(10_000)]
    book = positions(ts, date(2026, 10, 1), "X").data[("X", 1)]
    assert book.qty == D(10_000)
    assert book.avg_cost == D("0.10")
    assert book.qty * book.avg_cost == D("1000.00")   # floats would give 1000.0000000001 or similar
