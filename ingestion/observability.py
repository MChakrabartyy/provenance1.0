"""Structured JSON logs with run_id on every line, plus a tiny metrics sink."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from typing import Any


class RunLogger:
    def __init__(self, run_id: int | None = None, stream=None):
        self.run_id = run_id
        self.stream = stream or sys.stderr
        self.metrics: dict[str, Any] = {}

    def log(self, event: str, **fields: Any) -> None:
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "run_id": self.run_id, "event": event, **fields}
        self.stream.write(json.dumps(rec, default=str) + "\n")

    def timer(self):
        start = time.perf_counter()
        return lambda: int((time.perf_counter() - start) * 1000)


def alert(message: str, **fields: Any) -> None:
    """v0 alert sink: prints. Swap for an email/Slack webhook by setting PROVENANCE_ALERT_WEBHOOK."""
    import os
    rec = {"alert": message, **fields}
    url = os.environ.get("PROVENANCE_ALERT_WEBHOOK")
    if url:
        try:
            import httpx
            httpx.post(url, json=rec, timeout=5.0)
            return
        except Exception as e:  # noqa: BLE001
            rec["webhook_error"] = str(e)
    sys.stderr.write("ALERT " + json.dumps(rec, default=str) + "\n")
