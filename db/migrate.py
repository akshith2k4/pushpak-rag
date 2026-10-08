"""Run the Phase 2 schema migration (idempotent).

Usage:
    python3 db/migrate.py

Reads DATABASE_URL from the environment (or .env in the repo root).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db import close_pool, get_pool  # noqa: E402


def main() -> None:
    schema = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(schema)
            cur.execute("SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','pg_trgm');")
            print("extensions:", cur.fetchall())
            cur.execute(
                "SELECT count(*) AS rows, count(embedding) AS with_embedding FROM chunks;"
            )
            print("chunks table:", cur.fetchone())
    close_pool()
    print("MIGRATE OK")


if __name__ == "__main__":
    main()
