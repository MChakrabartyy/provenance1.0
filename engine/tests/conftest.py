from datetime import date, datetime
from decimal import Decimal

import pytest

from provenance.engine.types import Instrument, Mark, TradeRow

D = Decimal


@pytest.fixture
def instruments():
    return {
        1: Instrument(1, "AAA", "stock", "Tech"),
        2: Instrument(2, "BBB", "stock", "Tech"),
        3: Instrument(3, "CCC", "stock", "Health"),
        4: Instrument(4, "AAA261218C00000012", "option", "Tech", underlying="AAA", underlying_id=1, strike=D(12), expiry=date(2026, 12, 18), call_put="call"),
    }


@pytest.fixture
def trades():
    t = lambda i, mid, iid, q, px, d: TradeRow(i, mid, iid, D(q), D(px), datetime(2026, 10, d, 10, 0))
    return [
        t(1, "X", 1, 100, "10.00", 1),    # buy 100 AAA @10
        t(2, "X", 2, -50, "20.00", 1),    # short 50 BBB @20
        t(3, "X", 3, 200, "5.00", 1),     # buy 200 CCC @5
        t(4, "X", 1, 50, "12.00", 2),     # add 50 AAA @12 -> avg (1000+600)/150 = 10.6667
        t(5, "X", 1, -60, "11.00", 3),    # sell 60 AAA @11 -> realized 60*(11-10.666667)=20.00
        t(6, "X", 2, 50, "18.00", 3),     # cover BBB @18 -> realized 50*(20-18)=100
    ]


def marks(prices: dict[int, str], d: date):
    return {iid: Mark(iid, d, d, D(px), "primary_synth") for iid, px in prices.items()}
