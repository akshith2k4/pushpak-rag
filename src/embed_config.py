"""Single source of truth for embedding config (F1 model-drift guard).

Both ingest (RETRIEVAL_DOCUMENT) and retrieval (RETRIEVAL_QUERY) MUST import
EMBEDDING_MODEL / EMBEDDING_DIMS from here — never hardcode model strings.
Vectors embedded with a different model/dims are mutually incomparable and
fail silently, so ingest records the model in the ingest_meta table and
retrieval asserts it matches before querying.
"""
from __future__ import annotations

EMBEDDING_MODEL = "models/gemini-embedding-2"
EMBEDDING_DIMS = 768

TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"


def document_text(doc: str, section: str, text: str) -> str:
    """Exact string embedded for a chunk (also used for retrieval-time context)."""
    return f"{doc} — {section}\n{text}"


def record_model(cur) -> None:
    cur.execute(
        """
        INSERT INTO ingest_meta (key, value) VALUES
          ('embedding_model', %s),
          ('embedding_dims', %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
        """,
        (EMBEDDING_MODEL, str(EMBEDDING_DIMS)),
    )


def assert_model(cur) -> None:
    """Fail loudly if the DB was embedded with a different model/dims."""
    cur.execute("SELECT key, value FROM ingest_meta WHERE key IN ('embedding_model','embedding_dims');")
    meta = dict(cur.fetchall())
    if meta.get("embedding_model") != EMBEDDING_MODEL or meta.get("embedding_dims") != str(EMBEDDING_DIMS):
        raise RuntimeError(
            f"Embedding model drift: code wants {EMBEDDING_MODEL}@{EMBEDDING_DIMS}, "
            f"DB has {meta.get('embedding_model')}@{meta.get('embedding_dims')}. Re-ingest first."
        )
