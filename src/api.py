"""Pushpak Q&A API — Phase 2: health only. /ask lands in Phase 5."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.db import close_pool, get_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    close_pool()


app = FastAPI(title="Pushpak Q&A")


@app.get("/health")
def health() -> dict:
    status: dict = {"api": "ok"}
    try:
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
        status["db"] = "ok"
    except Exception as e:  # noqa: BLE001
        status["db"] = f"error: {type(e).__name__}: {e}"
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        status["redis"] = "not_configured"
    else:
        try:
            import redis

            r = redis.from_url(redis_url, socket_connect_timeout=10)
            r.ping()
            status["redis"] = "ok"
        except Exception as e:  # noqa: BLE001
            status["redis"] = f"error: {type(e).__name__}: {e}"
    return status
