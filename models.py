"""Standard models shared by adapters, quality rules, and the engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass
class RawTrade:
    """A row exactly as it appeared in a manager's file, plus the file it came from."""
    manager_id: str
    source_file: str
    row: dict[str, Any]
    row_number: int


@dataclass
class Trade:
    """The one shape every adapter must produce."""
    manager_id: str
    symbol: str
    qty: Decimal | None
    price: Decimal | None
    trade_time: datetime | None
    external_trade_id: str
    source_file: str
    # option fields; None for stocks
    instrument_type: str = "stock"
    underlying: str | None = None
    strike: Decimal | None = None
    expiry: date | None = None
    call_put: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def instrument_symbol(self) -> str:
        return self.symbol


@dataclass
class PriceRow:
    symbol: str
    as_of_date: date
    close: Decimal | None
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityFailure:
    rule: str
    reason: str
