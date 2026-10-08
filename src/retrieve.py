"""Phase 3b hybrid retrieval: vector + keyword (OR semantics) -> RRF -> top-5.

- Query embedding: EMBEDDING_MODEL @ EMBEDDING_DIMS, taskType=RETRIEVAL_QUERY,
  NO title parameter (title is document-side only). F1 guard asserted per call.
- Vector channel: WHERE retrievable AND embedding IS NOT NULL
  ORDER BY embedding <=> %s::vector LIMIT 20. (::vector cast required —
  vector <=> float[] has no operator.)
- Keyword channel: intra-word hyphens -> spaces ("over-deliver" would parse as
  over AND NOT deliver), tokens OR-joined (AND semantics zeroes chunks missing
  any single term, e.g. refusal chunks). websearch_to_tsquery still ranks via
  ts_rank, so precision survives; RRF only consumes ranks.
- Fusion: RRF k=60 over the two rank lists, dedupe by stable_id, top-5 with
  full citation metadata. No reranker / module filter / cache (locked).
"""
from __future__ import annotations

import os
import re

import requests

from src.db import _load_dotenv, get_pool
from src.embed_config import (
    EMBEDDING_DIMS,
    EMBEDDING_MODEL,
    TASK_QUERY,
    assert_model,
)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
RRF_K = 60
VECTOR_DEPTH = 20
KEYWORD_DEPTH = 20
RERANK_DEPTH = 15  # fused candidates rescored by Jev (15-20 calls/question)


def _api_key() -> str:
    _load_dotenv()
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is not set (env or .env)")
    return key


def sanitise_for_tsquery(query: str) -> str:
    """Hyphens -> spaces, then OR-join distinct word tokens.

    Returns e.g. "return OR fresh OR shortage" so websearch_to_tsquery uses
    OR semantics. Caller logs the parsed tsquery (see retrieve debug).
    """
    cleaned = re.sub(r"-", " ", query)
    tokens = re.findall(r"[a-z0-9]+", cleaned.lower())
    seen, uniq = set(), []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return " OR ".join(uniq)


def embed_query(key: str, text: str, cache: dict | None = None) -> list[float]:
    if cache is not None and text in cache:
        return cache[text]
    r = requests.post(
        f"{GEMINI_BASE}/{EMBEDDING_MODEL}:embedContent",
        params={"key": key},
        json={
            "content": {"parts": [{"text": text}]},
            "taskType": TASK_QUERY,
            "outputDimensionality": EMBEDDING_DIMS,
        },
        timeout=60,
    )
    r.raise_for_status()
    vec = r.json()["embedding"]["values"]
    if len(vec) != EMBEDDING_DIMS:
        raise RuntimeError(f"Query dim mismatch: got {len(vec)}, want {EMBEDDING_DIMS}")
    if cache is not None:
        cache[text] = vec
    return vec


def retrieve(
    query: str,
    top_k: int = 5,
    embed_cache: dict | None = None,
    rerank: bool = False,
    rerank_threshold: float | None = None,
) -> dict:
    """Return {"results": [top_k dicts], "debug": {...}}.

    Each result: chunk_id, stable_id, doc, module, section, page, text, tags,
    type + rrf score and per-channel ranks (1-based, None if absent).
    With rerank=True, the top RERANK_DEPTH fused candidates are rescored by
    Jev steered noul (src/rerank.py) and re-sorted; jev_score is attached.
    Rerank without JEV_API_KEY raises (no silent fallback).
    """
    key = _api_key()
    qvec = embed_query(key, query, embed_cache)
    or_text = sanitise_for_tsquery(query)

    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            assert_model(cur)  # F1: fail loudly on model drift, not garbage ranks
            cur.execute("SELECT websearch_to_tsquery('english', %s)::text;", (or_text,))
            parsed_tsquery = cur.fetchone()[0]

            cur.execute(
                """SELECT stable_id FROM chunks
                   WHERE retrievable AND embedding IS NOT NULL
                   ORDER BY embedding <=> %s::vector LIMIT %s;""",
                (qvec, VECTOR_DEPTH),
            )
            vec_ranks = [r[0] for r in cur.fetchall()]

            cur.execute(
                """SELECT stable_id FROM chunks, websearch_to_tsquery('english', %s) q
                   WHERE retrievable AND fts @@ q
                   ORDER BY ts_rank(fts, q) DESC LIMIT %s;""",
                (or_text, KEYWORD_DEPTH),
            )
            kw_ranks = [r[0] for r in cur.fetchall()]

            union = list(dict.fromkeys(vec_ranks + kw_ranks))
            if not union:
                return {"results": [], "debug": _debug(query, or_text, parsed_tsquery, {}, {})}
            cur.execute(
                """SELECT stable_id, chunk_id, doc, module, section, page, text, tags, type
                   FROM chunks WHERE stable_id = ANY(%s);""",
                (union,),
            )
            meta = {r[0]: r[1:] for r in cur.fetchall()}

    vec_pos = {sid: i + 1 for i, sid in enumerate(vec_ranks)}
    kw_pos = {sid: i + 1 for i, sid in enumerate(kw_ranks)}
    scored = []
    for sid in union:
        s = 0.0
        if sid in vec_pos:
            s += 1.0 / (RRF_K + vec_pos[sid])
        if sid in kw_pos:
            s += 1.0 / (RRF_K + kw_pos[sid])
        (chunk_id, doc, module, section, page, text, tags, ctype) = meta[sid]
        scored.append(
            {
                "chunk_id": chunk_id, "stable_id": sid, "doc": doc, "module": module,
                "section": section, "page": page, "text": text, "tags": tags, "type": ctype,
                "rrf": round(s, 6), "vec_rank": vec_pos.get(sid), "kw_rank": kw_pos.get(sid),
            }
        )
    scored.sort(key=lambda d: -d["rrf"])
    debug = _debug(query, or_text, parsed_tsquery, vec_pos, kw_pos)
    if rerank:
        from src.rerank import rerank as jev_rerank

        pool = scored[:RERANK_DEPTH]
        debug["rerank_pool"] = len(pool)
        scored = jev_rerank(query, pool, threshold=rerank_threshold) + scored[RERANK_DEPTH:]
    return {"results": scored[:top_k], "debug": debug}


def _debug(query: str, or_text: str, parsed: str, vec_pos: dict, kw_pos: dict) -> dict:
    return {
        "query": query,
        "or_text": or_text,
        "parsed_tsquery": parsed,
        "vector_depth_hits": len(vec_pos),
        "keyword_depth_hits": len(kw_pos),
    }
