"""Jev steered rerank for Pushpak retrieval (noul primitive, criteria-as-policy).

Locked V2 criteria (validated in the q045/q033 experiment):
- true:  "Contains the specific fact, rule, or quantity the question asks for,
           or explicitly marks the information as unspecified, blank, or to be provided"
- false: "Shares words or topics with the question but provides neither the
           requested fact nor any statement that it is unspecified"

Generic "relevance" scoring was shown to bury refusal sources (#7 -> #13 on
q045); the steered criteria rescued it (#7 -> #2). Generic scoring must NOT
be substituted here.

Cost/latency: ~1 short call per candidate (~340 input tokens each).
Calls run threaded (MAX_WORKERS). Requires JEV_API_KEY; callers must fail
loudly when rerank is requested without a key (no silent fallback that
would invalidate eval comparisons).
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import requests

from src.db import _load_dotenv

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"

INSTRUCTIONS = "Does the passage directly answer the question?"
CRITERIA_TRUE = (
    "Contains the specific fact, rule, or quantity the question asks for, "
    "or explicitly marks the information as unspecified, blank, or to be provided"
)
CRITERIA_FALSE = (
    "Shares words or topics with the question but provides neither the "
    "requested fact nor any statement that it is unspecified"
)

MAX_WORKERS = 5


def api_key() -> str:
    _load_dotenv()
    key = os.environ.get("JEV_API_KEY", "").strip()
    if not key:
        raise RuntimeError("JEV_API_KEY is not set (env or .env) — rerank requested without a key")
    return key


def passage_text(doc: str, section: str, text: str) -> str:
    return f"{doc} — {section}\n{text}"


def _score_one(key: str, question: str, passage: str) -> float:
    r = requests.post(
        JEV_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": JEV_MODEL,
            "state": f"Question: {question}\nPassage: {passage}",
            "questions": {
                "contains": {
                    "type": "noul",
                    "instructions": INSTRUCTIONS,
                    "criteria": {"true": CRITERIA_TRUE, "false": CRITERIA_FALSE},
                }
            },
        },
        timeout=90,
    )
    r.raise_for_status()
    ans = r.json()["answers"]["contains"]
    score = ans.get("noul", ans.get("probability", 0.0))
    return float(score)


def score_passages(question: str, passages: list[str]) -> list[float]:
    """Score passages in order (threaded). Returns noul true-probabilities."""
    key = api_key()  # fail fast before spending on partial batches
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        return list(ex.map(lambda p: _score_one(key, question, p), passages))


def rerank(
    question: str,
    candidates: list[dict],
    threshold: float | None = None,
    alpha: float = 0.5,
) -> list[dict]:
    """Re-sort fused candidates by normalized RRF+Jev combo (tie-break: Jev, RRF).

    Pure-Jev sorting was shown to discard the fusion signal (fixed q045 but
    broke q049's table chunk). combo = alpha*rrf_n + (1-alpha)*jev_n with both
    min-max normalized inside the pool keeps both signals.
    threshold: if set, drop candidates scoring below it (default None = re-sort
    only; thresholds need calibration against the full golden set first).
    """
    if not candidates:
        return []
    passages = [passage_text(c["doc"], c["section"], c["text"]) for c in candidates]
    scores = score_passages(question, passages)
    for c, s in zip(candidates, scores):
        c["jev_score"] = round(s, 4)
    if threshold is not None:
        candidates = [c for c in candidates if c["jev_score"] >= threshold]
        if not candidates:
            return []
    rrf_vals = [c["rrf"] for c in candidates]
    jev_vals = [c["jev_score"] for c in candidates]

    def _norm(vals: list[float]) -> list[float]:
        lo, hi = min(vals), max(vals)
        if hi == lo:
            return [1.0] * len(vals)
        return [(v - lo) / (hi - lo) for v in vals]

    rrf_n, jev_n = _norm(rrf_vals), _norm(jev_vals)
    for c, rn, jn in zip(candidates, rrf_n, jev_n):
        c["combo"] = round(alpha * rn + (1 - alpha) * jn, 6)
    candidates.sort(key=lambda c: (-c["combo"], -c["jev_score"], -c["rrf"]))
    return candidates
