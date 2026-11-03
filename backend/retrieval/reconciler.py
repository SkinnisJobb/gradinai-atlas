"""
The content-hash reconciler (ADR 0044 §4): makes `retrieval_*` match what Atlas holds NOW.

ONE PASS, `run_pass`:
  1. Take `pg_try_advisory_lock(LOCK_KEY)` on its own connection, so one worker per database
     runs a pass at a time (several uvicorn workers or a second container skip, not queue).
  2. `collect_sources`: every world node in scope (its card) and every SERVED document of it,
     each chunked and hashed. The hash covers the chunker version, the model and the document
     prefix, so changing any of them re-embeds everything it affects.
  3. Delete the sources that are no longer there: a deleted node, a document taken out of
     `docs[]` or marked external, a node that left the scope. Their chunks go with them.
  4. Embed what is new or changed, `cfg.batch` texts per call, and replace each source's chunks
     and its `retrieval_source` row IN ONE TRANSACTION, so a source is never half old, half new.
  5. Sweep every chunk that does not belong to a source row of the SAME model. With step 4 this
     is what makes "a model change never mixes vectors" structural rather than hoped for.

NEVER IN THE WRITE PATH. No route that writes the graph calls this, so a write cannot fail or
slow down because the embedder is off. The cost is lag: the index can be up to one interval
behind. It is then stale, never wrong, because the query side (STORY-atlas-retrieval-query)
re-checks visibility and doc serving against `node` at query time.

`graph_event` IS NOT THE QUEUE (ADR 0044 §4): comparing hashes heals itself whatever path did a
write, and does not depend on every writer recording an event.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import models_auth
import models_graph
from retrieval import Config, chunking
from retrieval.embedder import EmbedError, embed, vector_literal

log = logging.getLogger("atlas.retrieval")

#: 'ATLR'. Any constant works; it only has to differ from every other advisory lock taken on
#: this database, and nothing else in Atlas takes one.
LOCK_KEY = 0x41544C52
WORLD_FAMILIES = ("object", "bridge")

EmbedFn = Callable[[Config, Sequence[str]], list[list[float]]]


@dataclass
class Source:
    node_id: str
    source: str  # 'prose' | 'doc'
    doc_path: Optional[str]
    chunks: list[chunking.Chunk] = field(default_factory=list)
    hash: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.node_id, self.source, self.doc_path or "")


def source_hash(cfg: Config, chunks: Sequence[chunking.Chunk]) -> str:
    h = hashlib.sha256()
    for part in (chunking.CHUNKER_VERSION, cfg.model, cfg.doc_prefix):
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    for c in chunks:
        h.update(c.heading.encode("utf-8"))
        h.update(b"\0")
        h.update(c.content.encode("utf-8"))
        h.update(b"\1")
    return h.hexdigest()


def in_scope(s: Session, cfg: Config) -> Optional[set[str]]:
    """The node ids whose text may be sent to the endpoint, or None for "all of them".

    `all`: everything. `shared` (the product default, fail-closed): only nodes that a principal
    holding NO grant would see, i.e. nothing marked private and nothing under a private mark.
    It is computed with `deps._walk_unblocked`, the same walk `_visible_node_ids` uses, with
    EVERY private mark treated as blocked. What is indexed and who may see a result are two
    separate controls: this one decides what leaves the database; the query side still
    filters every result by the caller's own visible set.
    """
    if cfg.scope == "all":
        return None
    private_ids = set(s.scalars(
        select(models_auth.NodeVisibility.node_id).where(
            models_auth.NodeVisibility.visibility == models_auth.VISIBILITY_PRIVATE)
    ).all())
    if not private_ids:
        return None
    from deps import _walk_unblocked  # deps imports the gate; only an enabled add-on pays for it
    return _walk_unblocked(s, private_ids)


def collect_sources(s: Session, cfg: Config) -> dict[tuple[str, str, str], Source]:
    """Everything that SHOULD be indexed right now, chunked and hashed. Reads only.

    First round (ADR 0044 §3): world families only. Workitems resolve visibility through
    their effective binding and get their own round and their own oracle case.
    ONLY SERVED DOCUMENTS: `models_graph.NODE_DOC_SERVED_SQL`, the predicate search and
    `GET /api/node/{id}/doc` already use, so storing a document is still not serving it.
    """
    allowed = in_scope(s, cfg)
    nodes = s.execute(
        select(models_graph.Node)
        .join(models_graph.NodeType, models_graph.NodeType.id == models_graph.Node.type)
        .where(models_graph.NodeType.family.in_(WORLD_FAMILIES))
    ).scalars().all()
    out: dict[tuple[str, str, str], Source] = {}
    names: dict[str, tuple[str, dict]] = {}
    for n in nodes:
        if allowed is not None and n.id not in allowed:
            continue
        names[n.id] = (n.name, n.attributes or {})
        src = Source(n.id, "prose", None,
                     chunking.node_card(n.name, n.type, n.summary, n.abstract, n.detail))
        src.hash = source_hash(cfg, src.chunks)
        out[src.key] = src
    if not names:
        return out
    docs = s.execute(
        select(models_graph.NodeDoc.node_id, models_graph.NodeDoc.path,
               models_graph.NodeDoc.content)
        .where(models_graph.NodeDoc.node_id.in_(list(names)))
        .where(text(models_graph.NODE_DOC_SERVED_SQL))
    ).all()
    for node_id, path, content in docs:
        name, attrs = names[node_id]
        title = next((d.get("title") for d in attrs.get("docs") or []
                      if isinstance(d, dict) and d.get("path") == path), None) or path
        src = Source(node_id, "doc", path, chunking.doc_chunks(name, title, content or ""))
        src.hash = source_hash(cfg, src.chunks)
        out[src.key] = src
    return out


def _existing(conn) -> dict[tuple[str, str, str], str]:
    rows = conn.execute(text(
        "SELECT node_id, source, coalesce(doc_path, ''), content_hash FROM retrieval_source"))
    return {(a, b, c): h for a, b, c, h in rows}


def _delete_sources(conn, keys) -> None:
    for node_id, source, doc_path in keys:
        p = {"n": node_id, "s": source, "d": doc_path}
        conn.execute(text("DELETE FROM retrieval_chunk WHERE node_id = :n AND source = :s "
                          "AND coalesce(doc_path, '') = :d"), p)
        conn.execute(text("DELETE FROM retrieval_source WHERE node_id = :n AND source = :s "
                          "AND coalesce(doc_path, '') = :d"), p)


def _write_source(conn, cfg: Config, src: Source, vectors: list[list[float]]) -> None:
    _delete_sources(conn, [src.key])
    for c, v in zip(src.chunks, vectors):
        conn.execute(text(
            "INSERT INTO retrieval_chunk (node_id, source, doc_path, ordinal, heading, content, "
            "model, embedding) VALUES (:n, :s, :d, :o, :h, :c, :m, CAST(:e AS vector))"),
            {"n": src.node_id, "s": src.source, "d": src.doc_path, "o": c.ordinal,
             "h": c.heading, "c": c.content, "m": cfg.model, "e": vector_literal(v)})
    conn.execute(text(
        "INSERT INTO retrieval_source (node_id, source, doc_path, content_hash, model, indexed_at) "
        "VALUES (:n, :s, :d, :h, :m, now())"),
        {"n": src.node_id, "s": src.source, "d": src.doc_path, "h": src.hash, "m": cfg.model})


SWEEP_SQL = (
    "DELETE FROM retrieval_chunk c WHERE NOT EXISTS (SELECT 1 FROM retrieval_source r "
    "WHERE r.node_id = c.node_id AND r.source = c.source "
    "AND coalesce(r.doc_path, '') = coalesce(c.doc_path, '') AND r.model = c.model)"
)


def pending(engine: Engine, cfg: Config) -> int:
    """How many sources a pass would embed now. Reads only; used by the status route."""
    with Session(engine) as s:
        sources = collect_sources(s, cfg)
    with engine.connect() as conn:
        have = _existing(conn)
    return sum(1 for k, src in sources.items() if have.get(k) != src.hash)


def run_pass(engine: Engine, cfg: Config, embed_fn: EmbedFn = embed) -> dict:
    """One reconciliation. Returns counts, and `error` when the embedder failed (the sources
    written before the failure stay written; the rest wait for the next pass). Never raises
    for an embedder problem; a database error propagates to the loop, which reports it."""
    stats = {"started_at": datetime.now(timezone.utc).isoformat(), "embedded_sources": 0,
             "embedded_chunks": 0, "removed_sources": 0, "swept_chunks": 0, "error": None}
    lock_conn = engine.connect()
    try:
        got = lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
        lock_conn.commit()  # the lock is session-level; do not sit idle in a transaction
        if not got:
            stats["skipped"] = "another worker is running a pass"
            return stats
        try:
            with Session(engine) as s:
                sources = collect_sources(s, cfg)
            with engine.begin() as conn:
                gone = [k for k in _existing(conn) if k not in sources]
                _delete_sources(conn, gone)
                stats["removed_sources"] = len(gone)
                have = _existing(conn)
            todo = [src for k, src in sources.items() if have.get(k) != src.hash]

            i = 0
            while i < len(todo):
                # A group of whole sources holding about one batch of texts; one source with
                # more chunks than a batch is embedded over several calls, and written once.
                group, n = [], 0
                while i < len(todo) and (not group or n + len(todo[i].chunks) <= cfg.batch):
                    group.append(todo[i])
                    n += len(todo[i].chunks)
                    i += 1
                texts = [c.content for src in group for c in src.chunks]
                try:
                    vectors: list[list[float]] = []
                    for j in range(0, len(texts), cfg.batch):
                        vectors.extend(embed_fn(cfg, texts[j:j + cfg.batch]))
                except EmbedError as exc:
                    stats["error"] = str(exc)
                    break
                with engine.begin() as conn:
                    k = 0
                    for src in group:
                        _write_source(conn, cfg, src, vectors[k:k + len(src.chunks)])
                        k += len(src.chunks)
                stats["embedded_sources"] += len(group)
                stats["embedded_chunks"] += len(texts)

            with engine.begin() as conn:
                stats["swept_chunks"] = conn.execute(text(SWEEP_SQL)).rowcount
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            lock_conn.commit()
    finally:
        lock_conn.close()
    stats["finished_at"] = datetime.now(timezone.utc).isoformat()
    return stats
