"""
The query side of the retrieval add-on (ADR 0044 §5, STORY-atlas-retrieval-query): the vector list,
its fusion with full text, and the one-hop expansion. `routers/search.py` calls `run()` only when a
caller asks for `mode=semantic|hybrid`; the default `fulltext` never imports this module.

VISIBILITY IS INSIDE THE STATEMENT, BEFORE `ORDER BY … LIMIT`, exactly as every other read applies
it (`deps._visible_node_ids`, ADR 0026): the ranking never sees a node the caller may not see, so a
hidden node can neither appear nor push a visible one off the page, and `total` counts only what
the caller may see. The same statement re-checks, at query time:
  * the node still exists (`JOIN node`): a chunk of a node deleted before the reconciler swept it
    is invisible;
  * a document chunk's document is still SERVED: its path is in the node's `docs[]`, not marked
    external, and the `node_doc` row exists. Un-publishing a document by PATCH hides it here
    before the next pass removes its chunks (storing is not serving, ADR 0029);
  * the active model: a chunk of another model is never compared with this query's vector.
The scan is exact (no ANN index), so the filter cannot under-return.

FUSION is Reciprocal Rank Fusion at node level, k = 60: score = Σ 1 / (60 + rank) over the lists a
node is in. It uses positions, not scores, so full text's 0.0 rank for a document-only hit fuses
correctly, and the two scales never have to be made comparable.

EXPANSION (`expand=1`) is one hop and never recursive: from the top `EXPAND_SEEDS` fused results,
along `contains` (both ways), `requires` (both ways) and the binding edges, to visible nodes not
already in the result, at most `EXPAND_MAX`, each scored half its seed's score and carrying
`via: {from, edge, direction}`. Anything deeper is atlas_structure's job.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from retrieval import Config
from retrieval.embedder import EmbedError, embed, vector_literal

RRF_K = 60
EXPAND_SEEDS = 3
EXPAND_MAX = 10
EXPAND_EDGES = ("contains", "requires", "works_on", "serves")
SNIPPET_WORDS = 40


def top_k(environ=None) -> int:
    """How many nodes the vector list contributes (`ATLAS_RETRIEVAL_TOP_K`, default 30).

    Read per call from `os.environ`; `environ` is for the configuration catalogue (TASK-1433)."""
    env = os.environ if environ is None else environ
    try:
        return max(1, min(int(env.get("ATLAS_RETRIEVAL_TOP_K", "30")), 200))
    except ValueError:
        return 30


#: One statement, fully literal (parameters only). `:all_visible` / `:all_families` switch the two
#: filters off for an unrestricted caller or no `families=`; otherwise the arrays apply.
VECTOR_SQL = text("""
WITH best AS (
    SELECT DISTINCT ON (c.node_id)
           c.node_id, c.doc_path, c.heading, c.content,
           c.embedding <=> CAST(:qv AS vector) AS dist
    FROM retrieval_chunk c
    JOIN node n ON n.id = c.node_id
    WHERE c.model = :model
      AND (:all_visible OR c.node_id = ANY(:visible))
      AND (:all_families OR n.type IN (SELECT t.id FROM node_type t WHERE t.family = ANY(:families)))
      AND (c.doc_path IS NULL OR (
            EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(n.attributes -> 'docs', '[]'::jsonb)) d
                    WHERE d ->> 'path' = c.doc_path
                      AND coalesce((d ->> 'external')::boolean, false) = false)
            AND EXISTS (SELECT 1 FROM node_doc nd WHERE nd.node_id = c.node_id AND nd.path = c.doc_path)))
    ORDER BY c.node_id, dist
)
SELECT node_id, doc_path, heading, content, dist FROM best ORDER BY dist, node_id LIMIT :k
""")

#: Neighbours of the seeds over the expansion edges, both directions, in one statement.
EXPAND_SQL = text("""
SELECT e.src AS seed, e.dst AS other, e.edge_type, 'out' AS direction
  FROM edge e WHERE e.src = ANY(:seeds) AND e.edge_type = ANY(:edges)
UNION ALL
SELECT e.dst AS seed, e.src AS other, e.edge_type, 'in' AS direction
  FROM edge e WHERE e.dst = ANY(:seeds) AND e.edge_type = ANY(:edges)
ORDER BY seed, edge_type, other
""")


@dataclass
class VectorHit:
    node_id: str
    rank: int
    similarity: float
    doc_path: Optional[str]
    heading: str
    snippet: str


@dataclass
class Fused:
    node_id: str
    score: float
    vector: Optional[VectorHit] = None
    via: Optional[dict] = None
    in_fulltext: bool = False
    extra: dict = field(default_factory=dict)


def _snippet(content: str) -> str:
    """The chunk's first ~40 words, without its `node › doc › heading` (or card header) line."""
    parts = content.split("\n\n", 1)
    body = parts[1] if len(parts) == 2 else parts[0]
    words = body.split()
    return " ".join(words[:SNIPPET_WORDS]) + (" …" if len(words) > SNIPPET_WORDS else "")


def vector_list(s: Session, cfg: Config, q: str, visible: Optional[set[str]],
                families: Optional[set[str]]) -> list[VectorHit]:
    """The nearest visible nodes to `q`, best chunk per node. Raises EmbedError."""
    qv = embed(cfg, [q], query=True)[0]
    rows = s.execute(VECTOR_SQL, {
        "qv": vector_literal(qv), "model": cfg.model, "k": top_k(),
        "all_visible": visible is None, "visible": sorted(visible or ()),
        "all_families": families is None, "families": sorted(families or ()),
    }).all()
    return [VectorHit(r.node_id, i + 1, round(1.0 - float(r.dist), 4), r.doc_path, r.heading or "",
                      _snippet(r.content)) for i, r in enumerate(rows)]


def fuse(fulltext_ids: list[str], vectors: list[VectorHit]) -> list[Fused]:
    """RRF over the full-text order and the vector order. Ties break on node id."""
    out: dict[str, Fused] = {}
    for rank, nid in enumerate(fulltext_ids, start=1):
        out[nid] = Fused(nid, 1.0 / (RRF_K + rank), in_fulltext=True)
    for v in vectors:
        f = out.setdefault(v.node_id, Fused(v.node_id, 0.0))
        f.score += 1.0 / (RRF_K + v.rank)
        f.vector = v
    return sorted(out.values(), key=lambda f: (-f.score, f.node_id))


def expand(s: Session, fused: list[Fused], visible: Optional[set[str]],
           families: Optional[set[str]]) -> list[Fused]:
    """One hop from the top seeds; visible, family-matching nodes not already present."""
    seeds = fused[:EXPAND_SEEDS]
    if not seeds:
        return []
    present = {f.node_id for f in fused}
    seed_score = {f.node_id: f.score for f in seeds}
    rows = s.execute(EXPAND_SQL, {"seeds": list(seed_score), "edges": list(EXPAND_EDGES)}).all()
    family_of: dict[str, str] = {}
    if families is not None:
        others = sorted({r.other for r in rows})
        if others:
            family_of = dict(s.execute(text(
                "SELECT n.id, t.family FROM node n JOIN node_type t ON t.id = n.type WHERE n.id = ANY(:ids)"
            ), {"ids": others}).all())
    added: list[Fused] = []
    for seed in seed_score:  # seed order, so the best seed's neighbours come first
        for r in rows:
            if r.seed != seed or r.other in present:
                continue
            if visible is not None and r.other not in visible:
                continue
            if families is not None and family_of.get(r.other) not in families:
                continue
            present.add(r.other)
            added.append(Fused(r.other, round(seed_score[seed] * 0.5, 6),
                               via={"from": seed, "edge": r.edge_type, "direction": r.direction}))
            if len(added) >= EXPAND_MAX:
                return added
    return added


def run(s: Session, q: str, mode: str, fulltext_ids: list[str], visible: Optional[set[str]],
        families: Optional[set[str]], do_expand: bool) -> tuple[list[Fused], Optional[str]]:
    """The fused list for `mode` (semantic | hybrid), or the full-text list and the reason it fell
    back. Never raises for an add-on problem: degradation is reported, never silent."""
    import retrieval
    from retrieval import service

    if not retrieval.enabled():
        reason = "retrieval is off on this instance"
    else:
        cfg, problem = service.query_config()
        reason = problem
        if reason is None:
            try:
                vectors = vector_list(s, cfg, q, visible, families)
            except EmbedError as exc:
                vectors, reason = [], str(exc)
            if reason is None and not vectors and not service.has_vectors(cfg):
                reason = f"the index has no vectors for model {cfg.model} yet"
            if reason is None:
                fused = fuse([] if mode == "semantic" else fulltext_ids, vectors)
                if do_expand:
                    fused += expand(s, fused, visible, families)
                return fused, None

    if mode == "semantic":
        return [], reason
    fused = fuse(fulltext_ids, [])
    if do_expand:
        fused += expand(s, fused, visible, families)
    return fused, reason
