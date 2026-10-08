"""Phase 6 semantic cache over Upstash Redis REST API.

- Exact fast path: key = sha1(normalized question). Hit -> return stored answer.
- Semantic path: `pushpak:cache:index` holds recent entry keys (cap 200). Each
  entry stores the question embedding; on exact-miss we scan the index and
  cosine-compare locally, hit if similarity >= SEMANTIC_THRESHOLD.
- TTL 24h on every entry (EX 86400). Stale index members are skipped.
- Invalidation: each entry stores the ingest fingerprint (ingest_meta
  ingested_at + embedding model/dims). On lookup the fingerprint must match
  the live DB values, else the entry is treated as a miss (re-ingest safe).
- Degraded without credentials: all functions return miss/False, never raise.

Upstash REST: POST {url}/<CMD>/<args...> with `Authorization: Bearer <token>`.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re

import requests

from src.db import _load_dotenv

TTL_SEC = 24 * 3600
INDEX_KEY = "pushpak:cache:index"
INDEX_CAP = 200
# Measured: true paraphrases score ~0.88 in gemini-embedding-2 space, unrelated
# short questions far lower. 0.85 catches real paraphrases; a wrong cached
# answer is costlier than a slow correct one, so this stays conservative.
SEMANTIC_THRESHOLD = 0.85


def _cfg() -> tuple[str, str] | tuple[None, None]:
    _load_dotenv()
    url = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip().strip('"')
    tok = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip().strip('"')
    if not url or not tok:
        return None, None
    return url.rstrip("/"), tok


def _rest(path: str, timeout: int = 15):
    url, tok = _cfg()
    if not url:
        return None
    r = requests.post(
        f"{url}/{path}",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json().get("result")


def ping() -> bool:
    try:
        return _rest("ping") == "PONG"
    except Exception:  # noqa: BLE001
        return False


def normalize(question: str) -> str:
    q = question.strip().lower()
    q = re.sub(r"\s+", " ", q)
    return q.strip(" ?.")


def exact_key(question: str) -> str:
    return "pushpak:cache:" + hashlib.sha1(normalize(question).encode()).hexdigest()[:16]


def fingerprint(cur) -> str:
    """Current ingest identity: ingested_at + model + dims."""
    cur.execute("SELECT key, value FROM ingest_meta WHERE key IN ('ingested_at','embedding_model','embedding_dims');")
    return json.dumps(dict(sorted(cur.fetchall())), sort_keys=True)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def lookup(question: str, qvec: list[float], fp: str) -> dict | None:
    """Exact hit first, then semantic scan. Returns stored payload or None."""
    if _cfg()[0] is None:
        return None
    try:
        hit = _rest(f"get/{exact_key(question)}")
        if hit:
            entry = json.loads(hit)
            if entry.get("fingerprint") == fp:
                entry["_match"] = "exact"
                return entry
        members = _rest(f"lrange/{INDEX_KEY}/0/{INDEX_CAP - 1}") or []
        best, best_sim = None, 0.0
        for mkey in members:
            raw = _rest(f"get/{mkey}")
            if not raw:
                continue
            entry = json.loads(raw)
            if entry.get("fingerprint") != fp or "qvec" not in entry:
                continue
            sim = _cosine(qvec, entry["qvec"])
            if sim > best_sim:
                best, best_sim = entry, sim
        if best is not None and best_sim >= SEMANTIC_THRESHOLD:
            best["_match"] = f"semantic:{best_sim:.3f}"
            return best
        return None
    except Exception:  # noqa: BLE001
        return None  # cache never breaks answering


def store(question: str, qvec: list[float], fp: str, payload: dict) -> bool:
    """Store answer payload under exact key + index. Returns success."""
    if _cfg()[0] is None:
        return False
    try:
        # Round embeddings to 4 decimals: keeps URL-path payloads small,
        # cosine similarity at threshold 0.95 is unaffected.
        entry = {"question": normalize(question),
                 "qvec": [round(float(x), 4) for x in qvec],
                 "fingerprint": fp, **payload}
        key = exact_key(question)
        _rest(f"set/{key}/{_enc(entry)}/EX/{TTL_SEC}")
        _rest(f"lpush/{INDEX_KEY}/{key}")
        _rest(f"ltrim/{INDEX_KEY}/0/{INDEX_CAP - 1}")
        return True
    except Exception:  # noqa: BLE001
        return False


def _enc(entry: dict) -> str:
    import urllib.parse

    return urllib.parse.quote(json.dumps(entry, ensure_ascii=False), safe="")


def ttl(key: str) -> int | None:
    try:
        return _rest(f"ttl/{key}")
    except Exception:  # noqa: BLE001
        return None


def clear() -> bool:
    """Flush Pushpak cache keys (tests/maintenance). Best-effort per key."""
    if _cfg()[0] is None:
        return False
    ok = True
    try:
        members = _rest(f"lrange/{INDEX_KEY}/0/-1") or []
    except Exception:  # noqa: BLE001
        return False
    for mkey in members:
        try:
            _rest(f"del/{mkey}")
        except Exception:  # noqa: BLE001
            ok = False
    try:
        _rest(f"del/{INDEX_KEY}")
    except Exception:  # noqa: BLE001
        ok = False
    return ok
