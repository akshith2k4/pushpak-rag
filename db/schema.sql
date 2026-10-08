-- Pushpak RAG — Phase 2 schema (Neon Postgres + pgvector, free tier).
-- Run with: python3 db/migrate.py   (idempotent: IF NOT EXISTS everywhere)

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS chunks (
  stable_id    TEXT PRIMARY KEY,          -- sha1 content hash from ingest.py
  chunk_id     TEXT NOT NULL,             -- positional id (c000...)
  doc          TEXT NOT NULL,
  module       TEXT NOT NULL,
  section      TEXT NOT NULL,
  page         INT NOT NULL,
  page_end     INT NOT NULL,
  text         TEXT NOT NULL,
  tags         TEXT[] NOT NULL DEFAULT '{}',
  type         TEXT NOT NULL,
  retrievable  BOOLEAN NOT NULL DEFAULT TRUE,
  last_updated DATE NOT NULL,
  embedding    vector(768),               -- gemini-embedding-2, outputDimensionality=768
  fts          tsvector GENERATED ALWAYS AS (
                 to_tsvector('english', doc || ' ' || section || ' ' || text)
               ) STORED
);

-- HNSW cosine index for vector search (Phase 3). Query filters retrievable=true.
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
  ON chunks USING hnsw (embedding vector_cosine_ops);

-- GIN index for keyword / hybrid search (Phase 3).
CREATE INDEX IF NOT EXISTS chunks_fts_gin ON chunks USING gin (fts);

-- One-row-per-key ingest metadata (F1 model-drift guard): the embedding model
-- and dims used for the stored vectors, plus run stats. Retrieval asserts these.
CREATE TABLE IF NOT EXISTS ingest_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
