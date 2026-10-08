"""Semantic cache (Redis/Upstash) — Phase 6 (not yet implemented).

Key on normalized question + retrieved stable_id set. TTL 24h.
Invalidate on re-ingest (stable_id mismatch). Cache common questions first:
"Can driver over-deliver?", "What is Return Fresh?", photo-count questions.
Requires REDIS_URL (Upstash). Runs degraded without it until then.
"""
