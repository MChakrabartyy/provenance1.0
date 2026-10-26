"""FastAPI gateway. Same engine the copilot uses; permissions applied before any tool or retrieval call."""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from provenance.api.auth import current_user, get_conn, require_role
from provenance.config import DATA_DIR
from provenance.copilot.agent import ask
from provenance.copilot.tools import PermissionDenied, Tools, User
from provenance.db import migrate

WEB = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Provenance", version="0.0.1")


@app.exception_handler(PermissionDenied)
async def _perm(_, exc: PermissionDenied):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


class AskIn(BaseModel):
    question: str


def _tools(conn, user) -> Tools:
    t = Tools(conn, user)
    try:
        t.latest_date()
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return t


def _as_of(tools: Tools, as_of: str | None) -> str:
    return as_of or tools.latest_date().isoformat()


@app.get("/api/me")
def me(user: User = Depends(current_user), conn=Depends(get_conn)):
    managers = [dict(r) for r in conn.execute("SELECT id, name, pod, strategy FROM managers ORDER BY id")]
    if user.allowed_managers is not None:
        managers = [m for m in managers if m["id"] in user.allowed_managers]
    latest = conn.execute("SELECT run_date FROM pipeline_runs WHERE status IN ('success','partial') ORDER BY run_date DESC LIMIT 1").fetchone()
    return {"email": user.email, "role": user.role, "manager_id": user.manager_id, "managers": managers, "as_of": latest["run_date"] if latest else None}


@app.post("/api/ask")
def ask_route(body: AskIn, user: User = Depends(current_user), conn=Depends(get_conn)):
    return ask(conn, user, body.question).model_dump()


@app.get("/api/exposure")
def exposure_route(scope: str = "firm", as_of: str | None = None, user: User = Depends(require_role("pm", "risk")), conn=Depends(get_conn)):
    t = _tools(conn, user)
    if user.role == "pm":
        scope = user.manager_id
    return t.get_exposure(scope, _as_of(t, as_of))


@app.get("/api/concentration")
def concentration_route(as_of: str | None = None, user: User = Depends(require_role("risk")), conn=Depends(get_conn)):
    t = _tools(conn, user)
    return t.get_concentration(_as_of(t, as_of))


@app.get("/api/pnl")
def pnl_route(manager_id: str | None = None, days: int = Query(5, ge=1, le=60), end: str | None = None, user: User = Depends(require_role("pm", "risk")), conn=Depends(get_conn)):
    t = _tools(conn, user)
    mid = user.manager_id if user.role == "pm" else manager_id
    if not mid:
        raise HTTPException(400, "manager_id required")
    e = date.fromisoformat(_as_of(t, end))
    s = t.trading_days_back(e, days)
    return t.get_pnl_attribution(mid, s.isoformat(), e.isoformat())


@app.get("/api/positions")
def positions_route(manager_id: str | None = None, as_of: str | None = None, top_n: int = 50, user: User = Depends(require_role("pm", "risk")), conn=Depends(get_conn)):
    t = _tools(conn, user)
    mid = user.manager_id if user.role == "pm" else manager_id
    if not mid:
        raise HTTPException(400, "manager_id required")
    return t.get_positions(mid, _as_of(t, as_of), top_n)


@app.get("/api/runs")
def runs_route(user: User = Depends(current_user), conn=Depends(get_conn)):
    return [dict(r) | {"report": json.loads(r["report"] or "{}")} for r in conn.execute("SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT 20")]


@app.get("/api/runs/{run_date}")
def run_route(run_date: str, user: User = Depends(current_user), conn=Depends(get_conn)):
    return Tools(conn, user).get_quality_report(run_date)


@app.post("/api/runs")
def trigger_run(run_date: str | None = None, user: User = Depends(require_role("engineer")), conn=Depends(get_conn)):
    from provenance.ingestion.documents import ingest_documents
    from provenance.ingestion.pipeline import run
    report = run(date.fromisoformat(run_date) if run_date else None, DATA_DIR, conn)
    report["documents"] = ingest_documents(conn, DATA_DIR / "memos")
    return report


@app.get("/api/rows/{row_id}")
def row_route(row_id: str, user: User = Depends(current_user), conn=Depends(get_conn)):
    """Resolve a citation like trade:123 or price:7:2026-10-23:primary_synth to the underlying row (permission-checked)."""
    kind, _, rest = row_id.partition(":")
    allowed = user.allowed_managers
    if kind == "trade":
        r = conn.execute("SELECT t.*, i.symbol FROM trades t JOIN instruments i ON i.id=t.instrument_id WHERE t.id=?", (int(rest),)).fetchone()
        if r and allowed is not None and r["manager_id"] not in allowed:
            raise HTTPException(403, "not your pod")
    elif kind == "price":
        iid, d, src = rest.split(":")
        r = conn.execute("SELECT p.*, i.symbol FROM prices p JOIN instruments i ON i.id=p.instrument_id WHERE p.instrument_id=? AND p.as_of_date=? AND p.source=?", (int(iid), d, src)).fetchone()
    elif kind == "quarantine":
        r = conn.execute("SELECT * FROM quarantine WHERE id=?", (int(rest),)).fetchone()
        if r and user.role == "pm" and r["manager_id"] not in (None, user.manager_id):
            raise HTTPException(403, "not your pod")
    elif kind == "pipeline_run":
        r = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (int(rest),)).fetchone()
    elif kind == "chunk":
        doc, idx = rest.split(":")
        r = conn.execute("SELECT c.document_id, c.chunk_index, c.text, c.manager_id, d.title, d.source_url FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.document_id=? AND c.chunk_index=?", (int(doc), int(idx))).fetchone()
        if r and allowed is not None and r["manager_id"] is not None and r["manager_id"] not in allowed:
            raise HTTPException(403, "not your pod")
    else:
        raise HTTPException(400, "unknown row kind")
    if r is None:
        raise HTTPException(404, "row not found")
    return {"row_id": row_id, "kind": kind, "row": {k: r[k] for k in r.keys() if k not in ("embedding", "report")}}


@app.get("/api/documents/{doc_id}/chunks/{idx}")
def chunk_route(doc_id: int, idx: int, user: User = Depends(current_user), conn=Depends(get_conn)):
    return row_route(f"chunk:{doc_id}:{idx}", user, conn)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
