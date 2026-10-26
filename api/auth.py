"""Token auth. Role and manager_id come from the users table; the permission filter is applied before any tool call."""
from __future__ import annotations

import sqlite3

from fastapi import Depends, Header, HTTPException

from provenance.copilot.tools import User
from provenance.db import connect


def get_conn() -> sqlite3.Connection:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def current_user(authorization: str = Header(default=""), conn: sqlite3.Connection = Depends(get_conn)) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "missing bearer token")
    row = conn.execute("SELECT id, email, role, manager_id FROM users WHERE token=?", (token,)).fetchone()
    if row is None:
        raise HTTPException(401, "invalid token")
    return User(row["id"], row["email"], row["role"], row["manager_id"])


def require_role(*roles: str):
    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, f"requires role in {roles}")
        return user
    return dep
