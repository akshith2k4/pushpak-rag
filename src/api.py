"""Pushpak Q&A API — Phase 2: health only. /ask lands in Phase 5."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.db import close_pool, get_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    close_pool()


app = FastAPI(title="Pushpak Q&A")


@app.get("/")
def chat() -> FileResponse:
    return FileResponse("static/chat.html")


class AskRequest(BaseModel):
    question: str
    rerank: bool = True
    top_k: int = 5
    use_cache: bool = True
    tools_enabled: bool = True


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
    from src import cache as cache_mod

    if cache_mod._cfg()[0] is not None:
        status["redis"] = "ok" if cache_mod.ping() else "error: upstash unreachable"
    elif not redis_url:
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


@app.post("/ask")
def ask(req: AskRequest) -> dict:
    from src.generate import answer

    try:
        return answer(req.question, top_k=req.top_k, rerank=req.rerank,
                      use_cache=req.use_cache, tools_enabled=req.tools_enabled)
    except Exception as e:  # noqa: BLE001
        return {"answer": "Error answering question.", "error": f"{type(e).__name__}: {e}"}
