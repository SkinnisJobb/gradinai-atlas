"""
The add-on's storage (ADR 0044 §1): the `vector` extension and two tables of DERIVED data.

RUN ONLY WHEN `ATLAS_RETRIEVAL=on`, from `retrieval.service.boot`, never from `create_all` or
`main._reconcile_graph_schema`. With the add-on off this module is never imported, so the
schema is exactly what it was. The tables are not SQLAlchemy models for the same reason: a
model registered on `models_graph.Base` would be created by every boot, on or off.

IDEMPOTENT, AND THE DDL IS ADDITIVE except in `rebuild`. `ensure` only ever runs
`CREATE … IF NOT EXISTS`. Everything in these tables can be recomputed from `node` and
`node_doc`, so `rebuild` (reached only through the owner's `POST /api/retrieval/reindex` with
`rebuild: true`) may drop them: it is the one way to change the vector dimension, and it
loses nothing that the next reconciler pass does not put back.

`node_id` is bare text and NOT a foreign key, so deleting a node is unchanged
(`_delete_node_dependents` is not touched). Every reader joins `node`, which makes a chunk
whose node is gone invisible before the reconciler sweeps it.

NO ANN INDEX. An exact scan over a few thousand vectors takes milliseconds and keeps the
visibility filter trivially correct; a filtered HNSW scan can under-return. ADR 0044 §1 says
to add one only when a measurement asks for it.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from retrieval import Config

#: Literal statements, never a name formatted into SQL: chunks first, they hang off sources.
DROP_TABLES = ("DROP TABLE IF EXISTS retrieval_chunk", "DROP TABLE IF EXISTS retrieval_source")


def _ddl(dim: int) -> list[str]:
    return [
        "CREATE TABLE IF NOT EXISTS retrieval_source ("
        " node_id text NOT NULL,"
        " source text NOT NULL CHECK (source IN ('prose', 'doc')),"
        " doc_path text NULL,"
        " content_hash text NOT NULL,"
        " model text NOT NULL,"
        " indexed_at timestamptz NOT NULL DEFAULT now())",
        "CREATE UNIQUE INDEX IF NOT EXISTS retrieval_source_key "
        "ON retrieval_source (node_id, source, coalesce(doc_path, ''))",
        "CREATE TABLE IF NOT EXISTS retrieval_chunk ("
        " id bigserial PRIMARY KEY,"
        " node_id text NOT NULL,"
        " source text NOT NULL,"
        " doc_path text NULL,"
        " ordinal int NOT NULL,"
        " heading text NOT NULL DEFAULT '',"
        " content text NOT NULL,"
        " model text NOT NULL,"
        f" embedding vector({int(dim)}) NOT NULL)",
        "CREATE INDEX IF NOT EXISTS retrieval_chunk_source "
        "ON retrieval_chunk (node_id, source, coalesce(doc_path, ''))",
        "CREATE INDEX IF NOT EXISTS retrieval_chunk_model ON retrieval_chunk (model)",
    ]


def stored_dim(engine: Engine) -> Optional[int]:
    """The dimension of the existing `embedding` column, or None when there is no table.
    For pgvector's `vector(n)` the type modifier IS n."""
    with engine.connect() as c:
        return c.execute(text(
            "SELECT a.atttypmod FROM pg_attribute a "
            "WHERE a.attrelid = to_regclass('retrieval_chunk') AND a.attname = 'embedding'"
        )).scalar()


def ensure(engine: Engine, cfg: Config) -> Optional[str]:
    """Create what is missing. Returns None when the store is usable, else why it is not."""
    try:
        with engine.begin() as c:
            if not c.execute(text(
                    "SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")).scalar():
                return ("this Postgres has no pgvector extension; the add-on needs an image "
                        "that ships it (pgvector/pgvector:pg16, deploy/retrieval.override.yml)")
            c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            for stmt in _ddl(cfg.dim):
                c.execute(text(stmt))
    except DBAPIError as exc:
        return f"could not create the retrieval tables ({type(exc.orig).__name__})"
    have = stored_dim(engine)
    if have is not None and have != cfg.dim:
        return (f"the stored vectors have {have} dimensions and ATLAS_EMBED_DIM is {cfg.dim}; "
                f"POST /api/retrieval/reindex with rebuild=true recreates the tables")
    return None


def rebuild(engine: Engine, cfg: Config) -> Optional[str]:
    """Drop the derived tables and create them again for `cfg.dim`. See the module docstring."""
    with engine.begin() as c:
        for stmt in DROP_TABLES:
            c.execute(text(stmt))
    return ensure(engine, cfg)
