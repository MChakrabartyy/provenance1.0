"""Price and rate fetchers.

v0 reads the two synthetic source files that stand in for yfinance and a second vendor. Each fetcher
is wrapped in a timeout+retry and raises SourceError when the source is unavailable so the run can
finish as `partial` and name the missing source instead of skipping it silently.
"""
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from provenance.ingestion.reliability import PermanentError, TransientError, with_retry
from provenance.models import PriceRow

PRIMARY_SOURCE = "primary_synth"
SOURCES = ("primary_synth", "secondary_synth")


class SourceError(Exception):
    pass


def _parse_close(v) -> Decimal | None:
    try:
        return Decimal(str(v).strip()) if v not in (None, "") else None
    except InvalidOperation:
        return None


def fetch_source(data_dir: Path, source: str, through: date) -> list[PriceRow]:
    path = data_dir / "prices" / f"{source}.csv"

    def read() -> list[PriceRow]:
        if not path.exists():
            raise TransientError(f"price source {source} unavailable at {path}")
        out: list[PriceRow] = []
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                try:
                    as_of = date.fromisoformat(row["as_of_date"])
                except (KeyError, ValueError) as e:
                    raise PermanentError(f"bad row in {source}: {row}") from e
                if as_of > through:
                    continue
                out.append(PriceRow(symbol=row["symbol"].upper(), as_of_date=as_of, close=_parse_close(row.get("close")), source=source, raw=dict(row)))
        return out

    try:
        return with_retry(read, attempts=3, base_delay=0.05)
    except (TransientError, PermanentError) as e:
        raise SourceError(str(e)) from e


def fetch_rates(data_dir: Path, through: date) -> list[dict]:
    path = data_dir / "rates.csv"
    if not path.exists():
        raise SourceError(f"rates source unavailable at {path}")
    with path.open(newline="") as f:
        return [r for r in csv.DictReader(f) if r["as_of_date"] <= through.isoformat()]
