"""Adapter protocol: one small class per manager turns their file shape into the standard Trade.

Adapters never touch the database. They only yield objects. Parsing problems are not raised here:
a field that cannot be parsed becomes None so the quality layer can quarantine the row with a reason.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator, Protocol

from provenance.models import RawTrade, Trade


class ManagerAdapter(Protocol):
    manager_id: str

    def parse(self, path: Path) -> Iterator[RawTrade]: ...
    def to_standard(self, raw: RawTrade) -> Trade: ...


def dec(value) -> Decimal | None:
    """Parse '1,200', ' 42.5 ', 1200 -> Decimal; anything unparseable -> None."""
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if s == "":
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def ts(value, fmts: tuple[str, ...]) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def d8(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def option_symbol(und: str, expiry: date, call_put: str, strike: Decimal) -> str:
    cp = "C" if call_put.lower().startswith("c") else "P"
    return f"{und}{expiry.strftime('%y%m%d')}{cp}{int(strike):08d}"
