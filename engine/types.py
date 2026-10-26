"""Engine inputs and outputs. Pure data; no DB handles anywhere in the engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

OPTION_MULTIPLIER = Decimal(100)


@dataclass(frozen=True)
class Instrument:
    id: int
    symbol: str
    type: str                 # stock | option
    sector: str
    underlying: str | None = None
    underlying_id: int | None = None
    strike: Decimal | None = None
    expiry: date | None = None
    call_put: str | None = None

    @property
    def multiplier(self) -> Decimal:
        return OPTION_MULTIPLIER if self.type == "option" else Decimal(1)

    @property
    def name_key(self) -> str:
        """Single-name key for concentration: options roll up to their underlying."""
        return self.underlying or self.symbol


@dataclass(frozen=True)
class TradeRow:
    id: int
    manager_id: str
    instrument_id: int
    qty: Decimal
    price: Decimal
    trade_time: datetime

    @property
    def row_id(self) -> str:
        return f"trade:{self.id}"


@dataclass(frozen=True)
class Mark:
    """A close used to mark a position, with the row it came from."""
    instrument_id: int
    as_of_date: date       # the date we wanted
    price_date: date       # the date the close is actually from (may be earlier if no close that day)
    close: Decimal
    source: str
    flagged: str | None = None   # quality warning text if any input for this instrument/date was quarantined

    @property
    def row_id(self) -> str:
        return f"price:{self.instrument_id}:{self.price_date.isoformat()}:{self.source}"


@dataclass
class Position:
    manager_id: str
    instrument_id: int
    qty: Decimal = Decimal(0)
    avg_cost: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    trade_ids: list[str] = field(default_factory=list)


@dataclass
class Result:
    """Every engine function returns its data plus the ids of the rows it used (the citation)."""
    data: Any
    source_rows: list[str]
    warnings: list[str] = field(default_factory=list)

    def add_rows(self, rows) -> None:
        seen = set(self.source_rows)
        for r in rows:
            if r not in seen:
                seen.add(r)
                self.source_rows.append(r)
