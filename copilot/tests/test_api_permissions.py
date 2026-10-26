"""Integration: a PM login sees only their pod, risk sees the firm, engineer sees pipeline only. Runs against the local DB."""
import pytest
from fastapi.testclient import TestClient

from provenance.api.main import app
from provenance.db import connect

pytestmark = pytest.mark.skipif(not connect().execute("SELECT 1 FROM pipeline_runs LIMIT 1").fetchone(), reason="run the pipeline first")
client = TestClient(app)


def h(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_unauthenticated():
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers=h("nope")).status_code == 401


def test_pm_sees_only_own_pod():
    me = client.get("/api/me", headers=h("tok-pm-a")).json()
    assert [m["id"] for m in me["managers"]] == ["A"]
    r = client.get("/api/positions?manager_id=B", headers=h("tok-pm-a")).json()   # manager_id is overridden to A
    assert r["data"]["manager_id"] == "A"
    assert client.get("/api/exposure?scope=firm", headers=h("tok-pm-a")).json()["data"]["scope"] == "A"
    assert client.get("/api/concentration", headers=h("tok-pm-a")).status_code == 403
    a = client.post("/api/ask", json={"question": "Show me Manager B's positions"}, headers=h("tok-pm-a")).json()
    assert a["confidence"] == "refused"


def test_risk_sees_firm():
    me = client.get("/api/me", headers=h("tok-risk")).json()
    assert len(me["managers"]) == 4
    e = client.get("/api/exposure?scope=firm", headers=h("tok-risk")).json()
    assert e["data"]["gross"] and len(e["data"]["by_manager"]) == 4
    assert client.get("/api/pnl?manager_id=C", headers=h("tok-risk")).json()["data"]["manager_id"] == "C"
    assert client.get("/api/concentration", headers=h("tok-risk")).status_code == 200


def test_engineer_pipeline_only():
    assert client.get("/api/exposure", headers=h("tok-eng")).status_code == 403
    runs = client.get("/api/runs", headers=h("tok-eng")).json()
    assert runs and runs[0]["status"] in ("success", "partial")
    assert client.post("/api/runs", headers=h("tok-risk")).status_code == 403
    rep = client.post("/api/runs", headers=h("tok-eng")).json()
    assert rep["status"] == "success" and rep["files_skipped"] > 0 and rep["trades_stored"] == 0   # idempotent rerun


def test_rows_resolve_and_are_permission_checked():
    a = client.post("/api/ask", json={"question": "Why is Manager C down this week?"}, headers=h("tok-risk")).json()
    assert a["confidence"] == "answered" and a["figures"]
    row_id = a["figures"][0]["source_rows"][0]
    assert client.get(f"/api/rows/{row_id}", headers=h("tok-risk")).status_code == 200
    assert client.get(f"/api/rows/{row_id}", headers=h("tok-pm-a")).status_code == 403   # Manager C's trade, PM A asks
    c = a["citations"][0]
    assert client.get(f"/api/documents/{c['document_id']}/chunks/{c['chunk_index']}", headers=h("tok-pm-c")).status_code == 200
    assert client.get(f"/api/documents/{c['document_id']}/chunks/{c['chunk_index']}", headers=h("tok-pm-a")).status_code == 403


def test_ask_logs_every_exchange():
    conn = connect()
    before = conn.execute("SELECT count(*) FROM copilot_logs").fetchone()[0]
    client.post("/api/ask", json={"question": "Were any of today's prices stale?"}, headers=h("tok-risk"))
    assert conn.execute("SELECT count(*) FROM copilot_logs").fetchone()[0] == before + 1
