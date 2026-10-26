"""Manager B: JSON with nested instrument objects and option fields."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from provenance.ingestion.adapters.base import d8, dec, option_symbol, ts
from provenance.models import RawTrade, Trade


class ManagerBAdapter:
    manager_id = "B"

    def parse(self, path: Path) -> Iterator[RawTrade]:
        data = json.loads(path.read_text())
        for i, fill in enumerate(data.get("fills", []), start=1):
            yield RawTrade(self.manager_id, path.name, fill, i)

    def to_standard(self, raw: RawTrade) -> Trade:
        r = raw.row
        inst = r.get("instrument") or {}
        kind = (inst.get("kind") or "stock").lower()
        und = (inst.get("underlying") or inst.get("symbol") or "").upper()
        strike = dec(inst.get("strike"))
        expiry = d8(inst.get("expiry"))
        right = (inst.get("right") or "").lower()
        if kind == "option" and und and strike is not None and expiry is not None and right:
            symbol = option_symbol(und, expiry, right, strike)
        else:
            symbol = und
        return Trade(
            manager_id=self.manager_id,
            symbol=symbol,
            qty=dec(r.get("quantity")),
            price=dec(r.get("fill_price")),
            trade_time=ts(r.get("executed_at"), ()),
            external_trade_id=str(r.get("trade_id") or f"{raw.source_file}:{raw.row_number}"),
            source_file=raw.source_file,
            instrument_type=kind,
            underlying=und or None,
            strike=strike,
            expiry=expiry,
            call_put=("call" if right.startswith("c") else "put") if right else None,
            raw=r,
        )
