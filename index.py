"""Vercel entrypoint. The repo root is itself the `provenance` package, so alias it, then build the
read-only demo database in /tmp on first load (Vercel's filesystem is read-only elsewhere)."""
from __future__ import annotations

import importlib.util
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TMP = Path(os.environ.get("PROVENANCE_TMP", "/tmp/provenance"))
os.environ.setdefault("PROVENANCE_DATA_DIR", str(TMP / "data"))
os.environ.setdefault("PROVENANCE_DB", str(TMP / "provenance.db"))

if "provenance" not in sys.modules:
    spec = importlib.util.spec_from_file_location("provenance", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["provenance"] = pkg
    spec.loader.exec_module(pkg)

from provenance.config import DATA_DIR, DB_PATH  # noqa: E402
from provenance.api.main import app  # noqa: E402,F401

_lock = threading.Lock()


def _bootstrap() -> None:
    from provenance.data.synthetic.generator import main as generate
    from provenance.db import connect
    from provenance.ingestion.documents import ingest_documents
    from provenance.ingestion.pipeline import run

    generate(DATA_DIR)
    report = run(data_dir=DATA_DIR)
    if report["status"] == "failed":
        raise RuntimeError(report.get("error", "pipeline failed"))
    ingest_documents(connect(), DATA_DIR / "memos")


@app.middleware("http")
async def _ensure_db(request, call_next):
    if not DB_PATH.exists():
        with _lock:
            if not DB_PATH.exists():
                try:
                    _bootstrap()
                except Exception:
                    DB_PATH.unlink(missing_ok=True)  # retry on the next request
                    raise
    return await call_next(request)
