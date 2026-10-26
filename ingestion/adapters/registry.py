"""manager_id -> adapter. Onboarding a new manager = one new adapter file + one line here."""
from __future__ import annotations

from provenance.ingestion.adapters.base import ManagerAdapter
from provenance.ingestion.adapters.manager_a import ManagerAAdapter
from provenance.ingestion.adapters.manager_b import ManagerBAdapter
from provenance.ingestion.adapters.manager_c import ManagerCAdapter
from provenance.ingestion.adapters.manager_d import ManagerDAdapter

ADAPTERS: dict[str, ManagerAdapter] = {
    "A": ManagerAAdapter(),
    "B": ManagerBAdapter(),
    "C": ManagerCAdapter(),
    "D": ManagerDAdapter(),
}

# Where each manager drops files, relative to the data dir, and the glob that picks them up.
FILE_LAYOUT: dict[str, tuple[str, str]] = {
    "A": ("manager_a", "trades_*.csv"),
    "B": ("manager_b", "fills_*.json"),
    "C": ("manager_c", "blotter_*.xlsx"),
    "D": ("manager_d", "activity_*.csv"),
}


def get_adapter(manager_id: str) -> ManagerAdapter:
    try:
        return ADAPTERS[manager_id]
    except KeyError:
        raise KeyError(f"No adapter registered for manager {manager_id!r}. Add one in ingestion/adapters/ and register it.")
