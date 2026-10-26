"""Manager A: CSV with columns Ticker, Qty, Px, Time (US date format MM/DD/YYYY HH:MM)."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from provenance.ingestion.adapters.base import dec, ts
from provenance.models import RawTrade, Trade


class ManagerAAdapter:
    manager_id = "A"

    def parse(self, path: Path) -> Iterator[RawTrade]:
        with path.open(newline="") as f:
            for i, row in enumerate(csv.DictReader(f), start=2):
                yield RawTrade(self.manager_id, path.name, row, i)

    def to_standard(self, raw: RawTrade) -> Trade:
        r = raw.row
        return Trade(
            manager_id=self.manager_id,
            symbol=(r.get("Ticker") or "").strip().upper(),
            qty=dec(r.get("Qty")),
            price=dec(r.get("Px")),
            trade_time=ts(r.get("Time"), ("%m/%d/%Y %H:%M", "%m/%d/%Y")),
            external_trade_id=(r.get("TradeID") or f"{raw.source_file}:{raw.row_number}").strip(),
            source_file=raw.source_file,
            raw=r,
        )
