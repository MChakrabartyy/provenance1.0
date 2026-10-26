"""Document ingestion: memos (per manager) and filing excerpts (firm-wide) -> documents + chunks.

Chunking: split on markdown headings first, then pack to ~500-800 tokens (approximated as words/0.75)
with ~100 tokens of overlap. Each chunk carries document_id, chunk_index and manager_id so retrieval
can filter permissions in SQL before any text is read.

Embedding: v0 uses a local hashed bag-of-words vector (no network, no key) so the whole system runs
offline. The `embedding` column holds it as JSON. Swapping in a real embedding model means replacing
`embed()` and the cosine in copilot/retrieval.py with pgvector `<=>`; nothing else changes.
Re-embedding happens only when a document's content hash changes.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path

DIM = 512
TARGET_TOKENS, MAX_TOKENS, OVERLAP_TOKENS = 500, 800, 100
STOP = set("the a an and or of to in on for with is are was were be by at as it this that we our from has have not".split())


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9\-\.]*", text.lower()) if w not in STOP]


def embed(text: str) -> list[float]:
    """Hashed term-frequency vector, L2 normalised. Deterministic and dependency free."""
    vec = [0.0] * DIM
    for w in tokens(text):
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        vec[h % DIM] += 1.0 if (h >> 8) % 2 else -1.0
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / n, 6) for v in vec]


def chunk(text: str) -> list[str]:
    sections = re.split(r"\n(?=#{1,6} )", text)
    out: list[str] = []
    for sec in sections:
        words = sec.split()
        if not words:
            continue
        max_words = int(MAX_TOKENS * 0.75); target = int(TARGET_TOKENS * 0.75); overlap = int(OVERLAP_TOKENS * 0.75)
        if len(words) <= max_words:
            out.append(sec.strip()); continue
        i = 0
        while i < len(words):
            out.append(" ".join(words[i:i + target]))
            i += max(1, target - overlap)
    return out


def doc_meta(path: Path) -> dict:
    name = path.stem
    m = re.match(r"manager_([a-z])_([A-Z0-9]+)_(\d{4}-\d{2}-\d{2})", name)
    if m:
        return {"manager_id": m.group(1).upper(), "title": f"Manager {m.group(1).upper()} memo: {m.group(2)} ({m.group(3)})", "doc_type": "memo", "published_at": m.group(3), "source_url": None}
    m = re.match(r"filing_([A-Z0-9]+)_(10K|10Q)", name)
    if m:
        return {"manager_id": None, "title": f"{m.group(1)} {m.group(2).replace('10', '10-')} (synthetic excerpt)", "doc_type": "filing", "published_at": None,
                "source_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={m.group(1)}&type=10-K"}
    return {"manager_id": None, "title": name, "doc_type": "other", "published_at": None, "source_url": None}


def ingest_documents(conn: sqlite3.Connection, memo_dir: Path) -> dict:
    stats = {"documents_new": 0, "documents_unchanged": 0, "chunks_written": 0}
    for path in sorted(memo_dir.glob("*.md")):
        text = path.read_text()
        h = hashlib.sha256(text.encode()).hexdigest()
        if conn.execute("SELECT 1 FROM documents WHERE content_hash=?", (h,)).fetchone():
            stats["documents_unchanged"] += 1; continue
        meta = doc_meta(path)
        # a changed version of the same title replaces the old document and its chunks
        old = conn.execute("SELECT id FROM documents WHERE title=?", (meta["title"],)).fetchone()
        if old:
            conn.execute("DELETE FROM chunks WHERE document_id=?", (old["id"],)); conn.execute("DELETE FROM documents WHERE id=?", (old["id"],))
        cur = conn.execute("INSERT INTO documents (manager_id, title, doc_type, source_url, published_at, content_hash) VALUES (?,?,?,?,?,?)",
                           (meta["manager_id"], meta["title"], meta["doc_type"], meta["source_url"], meta["published_at"], h))
        doc_id = cur.lastrowid
        for i, c in enumerate(chunk(text)):
            conn.execute("INSERT INTO chunks (document_id, chunk_index, text, embedding, manager_id) VALUES (?,?,?,?,?)", (doc_id, i, c, json.dumps(embed(c)), meta["manager_id"]))
            stats["chunks_written"] += 1
        stats["documents_new"] += 1
    return stats
