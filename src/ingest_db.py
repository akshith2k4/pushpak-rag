"""Phase 3a: upsert chunks.jsonl into Neon + embed retrievable rows with Gemini.

- Upserts ALL chunks (metadata rows included, embedding NULL) — idempotent via
  stable_id PK + ON CONFLICT DO UPDATE.
- Embeds rows WHERE retrievable AND embedding IS NULL (skip --reembed to redo):
  gemini-embedding-2, taskType=RETRIEVAL_DOCUMENT, outputDimensionality=768,
  title="<doc> — <section>", batched via batchEmbedContents with backoff.
- Records embedding_model/dims in ingest_meta (F1 guard) and asserts:
  total rows == 174, non-null embeddings == 171.

Usage:
    python3 src/ingest_db.py               # full run
    python3 src/ingest_db.py --limit 1     # single-row insert test (F2)
    python3 src/ingest_db.py --reembed      # redo all embeddings
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from pgvector.psycopg import register_vector

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db import close_pool, get_pool  # noqa: E402
from src.embed_config import (  # noqa: E402
    EMBEDDING_DIMS,
    EMBEDDING_MODEL,
    TASK_DOCUMENT,
    document_text,
    record_model,
)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
BATCH_SIZE = 10
BATCH_PAUSE_SEC = 4
MAX_RETRIES = 6
RETRY_BASE_SEC = 5


def _api_key() -> str:
    from src.db import _load_dotenv  # lazy, reuses .env parsing

    _load_dotenv()
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is not set (env or .env)")
    return key


def embed_batch(key: str, items: list[dict]) -> list[list[float]]:
    """Call batchEmbedContents for up to BATCH_SIZE docs. Returns vectors in order."""
    payload = {
        "requests": [
            {
                "model": EMBEDDING_MODEL,
                "content": {"parts": [{"text": document_text(it["doc"], it["section"], it["text"])}]},
                "taskType": TASK_DOCUMENT,
                "title": f"{it['doc']} — {it['section']}"[:300],
                "outputDimensionality": EMBEDDING_DIMS,
            }
            for it in items
        ]
    }
    last_err: str = ""
    for attempt in range(MAX_RETRIES):
        r = requests.post(
            f"{GEMINI_BASE}/{EMBEDDING_MODEL}:batchEmbedContents",
            params={"key": key},
            json=payload,
            timeout=120,
        )
        if r.status_code == 200:
            vecs = [e["values"] for e in r.json()["embeddings"]]
            for v in vecs:
                if len(v) != EMBEDDING_DIMS:
                    raise RuntimeError(f"Dim mismatch: got {len(v)}, want {EMBEDDING_DIMS}")
            return vecs
        last_err = f"{r.status_code}: {r.text[:300]}"
        if r.status_code in (429, 500, 503):
            wait = min(RETRY_BASE_SEC * (2 ** attempt), 90)
            print(f"    retry {attempt + 1}/{MAX_RETRIES} after {wait}s ({r.status_code})", flush=True)
            time.sleep(wait)
            continue
        raise RuntimeError(f"Embed failed (no retry): {last_err}")
    raise RuntimeError(f"Embed failed after {MAX_RETRIES} retries: {last_err}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="embed at most N rows (test)")
    ap.add_argument("--reembed", action="store_true", help="redo existing embeddings")
    args = ap.parse_args()

    key = _api_key()
    chunks = [json.loads(l) for l in (ROOT / "data" / "chunks.jsonl").open(encoding="utf-8") if l.strip()]
    print(f"chunks.jsonl: {len(chunks)} chunks", flush=True)

    pool = get_pool()
    try:
        with pool.connection() as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                # 1. Upsert all metadata (embedding untouched on conflict).
                for c in chunks:
                    cur.execute(
                        """
                        INSERT INTO chunks
                          (stable_id, chunk_id, doc, module, section, page, page_end,
                           text, tags, type, retrievable, last_updated)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (stable_id) DO UPDATE SET
                          chunk_id=EXCLUDED.chunk_id, doc=EXCLUDED.doc, module=EXCLUDED.module,
                          section=EXCLUDED.section, page=EXCLUDED.page, page_end=EXCLUDED.page_end,
                          text=EXCLUDED.text, tags=EXCLUDED.tags, type=EXCLUDED.type,
                          retrievable=EXCLUDED.retrievable, last_updated=EXCLUDED.last_updated;
                        """,
                        (
                            c["stable_id"], c["chunk_id"], c["doc"], c["module"], c["section"],
                            c["page"], c["page_end"], c["text"], c["tags"], c["type"],
                            c["retrievable"], c["last_updated"],
                        ),
                    )
                conn.commit()
                cur.execute("SELECT count(*) FROM chunks;")
                print("rows in db:", cur.fetchone()[0], flush=True)

                # 2. Rows needing embeddings.
                if args.reembed:
                    cur.execute(
                        "SELECT stable_id, doc, section, text FROM chunks "
                        "WHERE retrievable ORDER BY chunk_id;"
                    )
                else:
                    cur.execute(
                        "SELECT stable_id, doc, section, text FROM chunks "
                        "WHERE retrievable AND embedding IS NULL ORDER BY chunk_id;"
                    )
                pending = [
                    {"stable_id": r[0], "doc": r[1], "section": r[2], "text": r[3]}
                    for r in cur.fetchall()
                ]
                if args.limit:
                    pending = pending[: args.limit]
                print(
                    f"to embed: {len(pending)} (reembed={args.reembed}, limit={args.limit or 'none'})",
                    flush=True,
                )

                # 3. Batch embed + update. Commit per batch so a later 429
                # never rolls back earlier progress.
                for i in range(0, len(pending), BATCH_SIZE):
                    batch = pending[i : i + BATCH_SIZE]
                    vecs = embed_batch(key, batch)
                    for item, vec in zip(batch, vecs):
                        cur.execute(
                            "UPDATE chunks SET embedding = %s WHERE stable_id = %s;",
                            (vec, item["stable_id"]),
                        )
                    conn.commit()
                    print(
                        f"  embedded {min(i + BATCH_SIZE, len(pending))}/{len(pending)} (committed)",
                        flush=True,
                    )
                    if i + BATCH_SIZE < len(pending):
                        time.sleep(BATCH_PAUSE_SEC)

                # 4. Record model + stats, assert acceptance.
                record_model(cur)
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                cur.execute(
                    "INSERT INTO ingest_meta (key, value) VALUES ('ingested_at', %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;",
                    (now,),
                )
                conn.commit()
                cur.execute("SELECT count(*) FROM chunks;")
                total = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL;")
                embedded = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM chunks WHERE retrievable AND embedding IS NULL;")
                missing = cur.fetchone()[0]
                print(f"total={total} embedded={embedded} retrievable-missing={missing}", flush=True)
                assert total == 174, f"row count {total} != 174"
                if not args.limit:
                    assert embedded == 171, f"embedded {embedded} != 171"
                    assert missing == 0
    finally:
        close_pool()
    print("INGEST_DB OK")


if __name__ == "__main__":
    main()
