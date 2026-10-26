"""SQLite connection helpers. Decimal values round-trip as strings so money is never a float."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from provenance.config import DB_PATH, ROOT

MIGRATIONS = ROOT / "infra" / "migrations"

sqlite3.register_adapter(Decimal, str)


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)  # one connection per request; FastAPI may close it on another thread
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    for sql_file in sorted(MIGRATIONS.glob("*.sql")):
        conn.executescript(sql_file.read_text())


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def D(value) -> Decimal:
    """Coerce a DB value (str/int/float/None) to Decimal."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
