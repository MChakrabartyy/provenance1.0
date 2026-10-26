"""Manager D: CSV with different column names (symbol, shares, exec_price, exec_ts, order_ref)."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from provenance.ingestion.adapters.base import dec, ts
from provenance.models import RawTrade, Trade


class ManagerDAdapter:
    manager_id = "D"

    def parse(self, path: Path) -> Iterator[RawTrade]:
        with path.open(newline="") as f:
            for i, row in enumerate(csv.DictReader(f), start=2):
                yield RawTrade(self.manager_id, path.name, row, i)

    def to_standard(self, raw: RawTrade) -> Trade:
        r = raw.row
        return Trade(
            manager_id=self.manager_id,
            symbol=(r.get("symbol") or "").strip().upper(),
            qty=dec(r.get("shares")),
            price=dec(r.get("exec_price")),
            trade_time=ts(r.get("exec_ts"), ()),
            external_trade_id=(r.get("order_ref") or f"{raw.source_file}:{raw.row_number}").strip(),
            source_file=raw.source_file,
            raw=r,
        )
