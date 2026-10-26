from datetime import date, datetime
from decimal import Decimal

from provenance.ingestion.quality import QualityContext, check_price, check_trade, source_disagreement
from provenance.models import PriceRow, Trade

D = Decimal
KNOWN = {"AAA": {"id": 1, "type": "stock"}, "OPT": {"id": 2, "type": "option"}}


def trade(**kw) -> Trade:
    base = dict(manager_id="A", symbol="AAA", qty=D(10), price=D("5"), trade_time=datetime(2026, 10, 1, 10), external_trade_id="t1", source_file="f.csv")
    base.update(kw)
    return Trade(**base)


def ctx() -> QualityContext:
    return QualityContext(known_instruments=KNOWN)


def test_required_fields_pass_and_fail():
    assert check_trade(trade(), ctx()) is None
    f = check_trade(trade(qty=None, price=None), ctx())
    assert f and f.rule == "required_fields" and "qty" in f.reason and "price" in f.reason


def test_positive_price():
    assert check_trade(trade(price=D("0.01")), ctx()) is None
    assert check_trade(trade(price=D(0)), ctx()).rule == "positive_price"


def test_option_sanity_expiry_and_strike():
    good = trade(symbol="OPT", instrument_type="option", expiry=date(2026, 12, 18), strike=D(100))
    assert check_trade(good, ctx()) is None
    assert check_trade(trade(symbol="OPT", instrument_type="option", expiry=date(2026, 9, 1), strike=D(100)), ctx()).rule == "option_sanity"
    assert check_trade(trade(symbol="OPT", instrument_type="option", expiry=date(2026, 12, 18), strike=D(0)), ctx()).rule == "option_sanity"


def test_unknown_instrument():
    assert check_trade(trade(symbol="AAA"), ctx()) is None
    assert check_trade(trade(symbol="ZZZQ"), ctx()).rule == "unknown_instrument"


def test_duplicate_trade_within_run_and_from_db():
    c = ctx()
    assert check_trade(trade(external_trade_id="t1"), c) is None
    assert check_trade(trade(external_trade_id="t1"), c).rule == "duplicate_trade"
    c2 = ctx(); c2.existing_trade_keys.add(("A", "t9"))
    assert check_trade(trade(external_trade_id="t9"), c2).rule == "duplicate_trade"
    assert check_trade(trade(external_trade_id="t9", manager_id="B"), c2) is None  # same id, other manager is fine


def price(**kw) -> PriceRow:
    base = dict(symbol="AAA", as_of_date=date(2026, 10, 2), close=D("100"), source="primary_synth", raw={"price_date": "2026-10-02"})
    base.update(kw)
    return PriceRow(**base)


def test_stale_price():
    assert check_price(price(), ctx()) is None
    assert check_price(price(raw={"price_date": "2026-10-01"}), ctx()).rule == "stale_price"


def test_price_jump_threshold_and_options_exempt():
    c = ctx()
    assert check_price(price(as_of_date=date(2026, 10, 1), close=D(100)), c) is None
    assert check_price(price(close=D(124)), c) is None               # 24% ok
    assert check_price(price(close=D(10000)), c).rule == "price_jump"  # 100x
    assert c.prior_close[("AAA", "primary_synth")] == D(124)         # the bad row did not update prior
    c2 = ctx()
    assert check_price(price(symbol="OPT", as_of_date=date(2026, 10, 1), close=D(1)), c2) is None
    assert check_price(price(symbol="OPT", close=D(3)), c2) is None  # options exempt


def test_source_disagreement_quarantines_secondary_only():
    rows = [price(close=D(100)), price(close=D("101.5"), source="secondary_synth"), price(close=D("100.5"), source="secondary_synth", as_of_date=date(2026, 10, 3)), price(close=D(100), as_of_date=date(2026, 10, 3))]
    failures = source_disagreement(rows, ctx(), "primary_synth")
    assert len(failures) == 1
    row, f = failures[0]
    assert row.source == "secondary_synth" and row.as_of_date == date(2026, 10, 2) and f.rule == "source_disagreement"
