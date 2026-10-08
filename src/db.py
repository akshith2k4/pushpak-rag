"""Shared Postgres connection pool (Neon free tier).

Reads DATABASE_URL from the environment or repo-root .env.
Pool is small on purpose (free-tier connection limits): min 1, max 5.
"""
from __future__ import annotations

import os
from pathlib import Path

from psycopg_pool import ConnectionPool

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _load_dotenv()
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL is not set (env or .env)")
        _pool = ConnectionPool(url, min_size=1, max_size=5, kwargs={"connect_timeout": 15})
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
