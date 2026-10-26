"""Permission-filtered vector search. The manager filter is in the SQL WHERE, before any text is read."""
from __future__ import annotations

import json
import sqlite3

from provenance.ingestion.documents import embed, tokens


def search_documents(conn: sqlite3.Connection, query: str, allowed_manager_ids: list[str] | None, k: int = 5, manager_id: str | None = None) -> list[dict]:
    """allowed_manager_ids=None means firm-wide access (risk role). Firm-wide documents (manager_id NULL) are visible to everyone.

    `manager_id` narrows further to one manager's memos plus firm-wide docs; it must be inside the allowed set.
    """
    sql = "SELECT c.id, c.document_id, c.chunk_index, c.text, c.embedding, c.manager_id, d.title, d.doc_type, d.source_url, d.published_at FROM chunks c JOIN documents d ON d.id=c.document_id WHERE 1=1"
    params: list = []
    scope = allowed_manager_ids
    if manager_id is not None:
        if allowed_manager_ids is not None and manager_id not in allowed_manager_ids:
            return []
        scope = [manager_id]
    if scope is not None:
        sql += f" AND (c.manager_id IS NULL OR c.manager_id IN ({','.join('?' * len(scope))}))"; params += scope
    q = embed(query); qtok = set(tokens(query))
    scored = []
    for r in conn.execute(sql, params):
        v = json.loads(r["embedding"])
        cos = sum(a * b for a, b in zip(q, v))
        overlap = len(qtok & set(tokens(r["text"]))) / (len(qtok) or 1)
        score = 0.6 * cos + 0.4 * overlap
        if score <= 0:
            continue
        scored.append({"chunk_id": r["id"], "document_id": r["document_id"], "chunk_index": r["chunk_index"], "manager_id": r["manager_id"],
                       "title": r["title"], "doc_type": r["doc_type"], "source_url": r["source_url"], "published_at": r["published_at"],
                       "text": r["text"], "score": round(score, 4)})
    scored.sort(key=lambda x: -x["score"])
    return scored[:k]
