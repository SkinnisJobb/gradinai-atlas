"""`search` routes, split out of `app.py` (STORY-oss-04); the code is moved, not changed.
Registered on the app by `main.py` via `include_router`.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

import models_graph  # Atlas v2 object-graph schema (ADR 0019); coexists with Component below
from models_graph import Node as GraphNode
from db import session
import audit_log  # STORY-oss-16: the read audit (off unless ATLAS_AUDIT_READS=1)
from deps import (
    _visible_node_ids,
    WORKITEM_LIST_DEFAULT_PAGE_SIZE,
    WORKITEM_LIST_MAX_PAGE_SIZE,
    _search_hits,
)

router = APIRouter()


#: The sources `_search_hits` reports, in the order a snippet is drawn from
#: them: a node's own authored prose says what it IS, so it outranks a body,
#: which outranks a document, which outranks a workitem's criteria or outcome,
#: which outrank its comments (a conversation about the item, not the item).
#: `labels` (STORY-oss-14) is last, so a hit that matched nowhere else still
#: lists its sources in the order it always did.
SEARCH_SOURCES = ("prose", "body", "doc", "acceptance", "result", "comment", "adr", "labels")


# --------------------------------------------------------------------------- #
# GET /api/search (STORY-atlas-search-node-docs) — the GENERAL search
# surface, over every node type, not just the workitem family.
#
# THE DECISION THIS ROUTE RECORDS. `_search_node_ids` above always matched
# across every node — including world (object-family) nodes — but its only
# caller until now was `list_workitems`, which ANDs the result against
# `NodeType.family == "workitem"`. So a world node whose body matched `q` was
# found by the query and silently discarded by the caller: reachable in the
# SQL, unreachable from any route. Two ways to read that:
#   (a) a bug — the caller over-narrows what the query already found, or
#   (b) deliberate scoping — `/api/workitems` is a WORKITEM listing (its own
#       name, its own docstring: "list/filter without downloading the whole
#       graph" of the BACKLOG), so restricting its search to workitems is
#       the same kind of scoping as its `type=`/`status=`/`owner=` filters,
#       not an accident.
# This route is what makes it (b) rather than a gap: `/api/workitems?q=`
# keeps its family filter, UNCHANGED, and stays the right answer to "find a
# workitem". This route is the answer to "find a NODE, of any kind" — the
# question node documentation (mostly authored on world nodes: architecture
# docs, runbooks) makes newly worth asking, since restricting doc search to
# the workitem family would make almost every document unfindable by
# construction. A world node's `body`/`name` match surfaces here too, for
# the same reason — it was always a legitimate hit, just never reachable.
#
# NOT wired into the board (frontend is out of scope for this change — see
# the task's own file-ownership rule); this is the surface a future frontend
# iteration reaches for, and it is usable today over the API/MCP.
#
# Same shape as `GET /api/workitems` / `GET /api/events`
# (total/count/page/page_size/items) for the same "one vocabulary" reason
# those two already share it. No `type=`/`status=`/`owner=` filters — this
# route answers ONE question (does `q` match this node, anywhere it is
# indexed) and composing narrower filters onto it is `list_workitems`'s job
# when the caller already knows it wants a workitem.
#
# POINTERS, NOT PAYLOADS (TASK-1156). Items used to be full `_graph_node_json`
# rows, which carry the AUTHORED abstract and detail unconditionally — 2.3 to
# 3.2 KB per hit on live, 2026-09-14, so a search for "which node knows about
# X?" returned the knowledge of every node that mentioned X. An item is now
# what it takes to decide which ONE node to open: id, type, name, the one-line
# summary, where it matched (`matched_in`, plus `doc_paths` for a document
# hit) and a short `snippet` around the match. Nothing reads the old shape:
# the board never called this route.
#
# RANKED, because an agent reads the first page and stops. Weighted `ts_rank`
# over the node's own text — name (A) above authored prose (B) above body (C)
# — and a node that matched only through a document, criterion or outcome
# ranks 0 and sorts after, by type and id as before.
# --------------------------------------------------------------------------- #
_SEARCH_RANK_SQL = (
    "ts_rank(setweight(to_tsvector('english', coalesce(name,'')), 'A') || "
    "setweight(to_tsvector('english', coalesce(summary,'') || ' ' || coalesce(abstract,'') "
    "|| ' ' || coalesce(detail,'')), 'B') || "
    "setweight(to_tsvector('english', coalesce(body,'')), 'C'), plainto_tsquery('english', :q))"
)
_SNIPPET_OPTIONS = "MaxWords=24, MinWords=8, ShortWord=3, MaxFragments=1, StartSel=**, StopSel=**"


def _search_snippet(s: Session, node: GraphNode, hit: dict, q: str) -> str | None:
    """A short excerpt around the match, from the highest-priority source that
    matched (`SEARCH_SOURCES` order). One `ts_headline` per item on the page."""
    for source in SEARCH_SOURCES:
        if source not in hit["sources"]:
            continue
        if source == "prose":
            doc = " ".join(p for p in (node.summary, node.abstract, node.detail) if p)
        elif source == "body":
            doc = " ".join(p for p in (node.name, node.body) if p)
        elif source == "doc":
            doc = s.scalar(select(models_graph.NodeDoc.content).where(
                models_graph.NodeDoc.node_id == node.id,
                models_graph.NodeDoc.path == sorted(hit["doc_paths"])[0]))
        elif source == "acceptance":
            doc = " ".join(s.scalars(select(models_graph.AcceptanceCriterion.text).where(
                models_graph.AcceptanceCriterion.node_id == node.id)))
        elif source == "result":
            doc = " ".join(s.scalars(select(models_graph.WorkitemResult.text).where(
                models_graph.WorkitemResult.node_id == node.id)))
        elif source == "comment":
            doc = " ".join(s.scalars(select(models_graph.WorkitemComment.body).where(
                models_graph.WorkitemComment.node_id == node.id,
                models_graph.WorkitemComment.deleted_at.is_(None))
                .order_by(models_graph.WorkitemComment.created_at)))
        elif source == "labels":  # STORY-oss-14: the tags themselves
            doc = " ".join(node.labels or [])
        else:  # "adr" (ADR 0045): the first matching ADR's id and title
            import models_adr
            r = s.scalar(select(models_adr.AdrRecord).where(
                models_adr.AdrRecord.adr_id == sorted(hit["adr_ids"])[0]))
            doc = f"{r.adr_id} {r.title} {r.content or ''}" if r else None
        if doc:
            return s.scalar(text(
                "SELECT ts_headline('english', :doc, plainto_tsquery('english', :q), :opts)"
            ).bindparams(doc=doc, q=q, opts=_SNIPPET_OPTIONS))
    return None


#: ADR 0044 §5. `fulltext` is today's search and the default; the other two need the retrieval
#: add-on and degrade to full text, saying so, when it is off or unavailable.
SEARCH_MODES = ("fulltext", "semantic", "hybrid")


@router.get("/api/search")
def search_nodes(q: str, request: Request, families: str | None = None, page: int = 1,
                  page_size: int = WORKITEM_LIST_DEFAULT_PAGE_SIZE,
                  mode: str | None = None, expand: int = 0) -> JSONResponse:
    """Full-text search across EVERY node — world and workitem alike — via
    `_search_hits` with no family restriction unless `families` names some.
    NodeVisibility is applied INSIDE the query exactly as `list_workitems`
    applies it: `total` is computed AFTER the access filter, so a caller can
    never infer a hidden node's existence (or a hidden document's) from a
    filtered page against an unfiltered total.

    `families` — comma-separated node_type families (`object,bridge` is the
    world). An unknown family is a 400 naming the legal ones, never an empty
    result that reads as "nothing matched".

    Items are POINTERS (see the note above): `id`, `type`, `name`, `summary`,
    `matched_in`, `doc_paths`, `snippet`, `rank`. Never abstract, detail or body.

    `mode` / `expand` (ADR 0044 §5, STORY-atlas-retrieval-query). Absent, this route is byte for
    byte what it was. `mode=semantic|hybrid` adds the retrieval add-on's vector list, fused with
    full text by RRF (`retrieval/query.py`, where the visibility argument is written down);
    `expand=1` adds one hop of graph neighbours to the top results. Such a response also carries
    `mode`, each item a `score` (and `similarity` / `via` where they apply), and `degraded` when
    the add-on could not answer, in which case `hybrid` is the full-text result and `semantic`
    is empty. The add-on is imported only on that path.
    """
    page = max(1, page)
    page_size = max(1, min(page_size, WORKITEM_LIST_MAX_PAGE_SIZE))
    filters = {"q": q, "families": families}
    if mode is not None and mode not in SEARCH_MODES:
        return JSONResponse({"error": "unknown_mode", "field": "mode",
                             "detail": f"unknown mode {mode!r} — legal: {', '.join(SEARCH_MODES)}."},
                            status_code=400)
    if expand not in (0, 1):
        return JSONResponse({"error": "bad_expand", "field": "expand",
                             "detail": "expand is 0 or 1."}, status_code=400)
    retrieval_path = mode is not None or expand == 1
    if retrieval_path:
        filters.update(mode=mode or "fulltext", expand=expand)

    with session() as s:
        want = ({f.strip() for f in families.split(",") if f.strip()} if families else None)
        if want is not None:
            legal = set(s.scalars(select(models_graph.NodeType.family).distinct()))
            unknown = sorted(want - legal)
            if unknown:
                return JSONResponse(
                    {"error": "unknown_family", "field": "families",
                     "detail": f"unknown node_type famil{'y' if len(unknown) == 1 else 'ies'} "
                               f"{', '.join(unknown)} — legal: {', '.join(sorted(legal))}."},
                    status_code=400)

        if not q or not q.strip():
            audit_log.record_read(request, "GET /api/search", [])
            return JSONResponse({
                "total": 0, "count": 0, "page": page, "page_size": page_size,
                "filters": filters, "max_page_size": WORKITEM_LIST_MAX_PAGE_SIZE,
                "items": [],
            })

        hits = _search_hits(s, q)
        ids = set(hits)
        visible_ids = _visible_node_ids(s)
        if visible_ids is not None:
            ids = ids & visible_ids

        candidates: list[GraphNode] = []
        if ids:
            stmt = select(GraphNode).where(GraphNode.id.in_(ids))
            if want is not None:
                stmt = stmt.join(models_graph.NodeType, models_graph.NodeType.id == GraphNode.type) \
                           .where(models_graph.NodeType.family.in_(want))
            candidates = list(s.scalars(stmt).all())

        ranks: dict[str, float] = {}
        if candidates:
            ranks = dict(s.execute(
                text(f"SELECT id, {_SEARCH_RANK_SQL} FROM node WHERE id IN :ids")
                .bindparams(bindparam("ids", expanding=True))
                .bindparams(q=q, ids=[n.id for n in candidates])
            ).all())
        candidates.sort(key=lambda n: (-ranks.get(n.id, 0.0), n.type, n.id))

        if retrieval_path:
            return _search_retrieval(s, request, q, mode or "fulltext", expand == 1, want,
                                     visible_ids, candidates, hits, ranks, page, page_size, filters)

        total = len(candidates)
        start = (page - 1) * page_size
        page_items = candidates[start:start + page_size]

        items = [{
            "id": n.id, "type": n.type, "name": n.name, "summary": n.summary,
            "matched_in": [src for src in SEARCH_SOURCES if src in hits[n.id]["sources"]],
            "doc_paths": sorted(hits[n.id]["doc_paths"]),
            "snippet": _search_snippet(s, n, hits[n.id], q),
            "rank": round(ranks.get(n.id, 0.0), 4),
            # ADR 0045: only on a hit that matched an ADR, so an install with no
            # ADR records serves exactly the shape it served before.
            **({"adr_ids": sorted(hits[n.id]["adr_ids"])} if hits[n.id].get("adr_ids") else {}),
        } for n in page_items]

        # STORY-oss-16: the ids SERVED on this page, never the query string. One
        # boolean when ATLAS_AUDIT_READS is off (the default).
        audit_log.record_read(request, "GET /api/search", (i["id"] for i in items))
        return JSONResponse({
            "total": total, "count": len(items), "page": page, "page_size": page_size,
            "filters": filters, "max_page_size": WORKITEM_LIST_MAX_PAGE_SIZE,
            "items": items,
        })


def _search_retrieval(s: Session, request: Request, q: str, mode: str, do_expand: bool,
                      want: set[str] | None, visible_ids: set[str] | None,
                      candidates: list[GraphNode], hits: dict, ranks: dict,
                      page: int, page_size: int, filters: dict) -> JSONResponse:
    """`/api/search` with `mode` or `expand` (ADR 0044 §5). `candidates` is today's full-text
    result, already visible and family-filtered and in today's order; the vector list and the
    expansion apply the same two filters inside their own statements (`retrieval/query.py`)."""
    from retrieval import query as rq  # the add-on is imported only on this path

    fulltext_ids = [n.id for n in candidates]
    degraded = None
    if mode == "fulltext":
        fused = rq.fuse(fulltext_ids, [])
        if do_expand:
            fused += rq.expand(s, fused, visible_ids, want)
    else:
        fused, degraded = rq.run(s, q, mode, fulltext_ids, visible_ids, want, do_expand)

    total = len(fused)
    start = (page - 1) * page_size
    page_fused = fused[start:start + page_size]
    by_id = {n.id: n for n in candidates}
    missing = [f.node_id for f in page_fused if f.node_id not in by_id]
    if missing:
        by_id.update({n.id: n for n in s.scalars(select(GraphNode).where(GraphNode.id.in_(missing)))})

    items = []
    for f in page_fused:
        n = by_id.get(f.node_id)
        if n is None:  # deleted between the two statements: never served
            continue
        hit = hits.get(n.id)
        matched = [src for src in SEARCH_SOURCES if hit and src in hit["sources"]]
        doc_paths = set(hit["doc_paths"]) if hit else set()
        if f.vector:
            matched.append("semantic")
            if f.vector.doc_path:
                doc_paths.add(f.vector.doc_path)
        item = {
            "id": n.id, "type": n.type, "name": n.name, "summary": n.summary,
            "matched_in": matched,
            "doc_paths": sorted(doc_paths),
            "snippet": (_search_snippet(s, n, hit, q) if hit
                        else f.vector.snippet if f.vector else None),
            "rank": round(ranks.get(n.id, 0.0), 4),
            "score": round(f.score, 6),
        }
        if f.vector:
            item["similarity"] = f.vector.similarity
        if f.via:
            item["via"] = f.via
        if hit and hit.get("adr_ids"):
            item["adr_ids"] = sorted(hit["adr_ids"])
        items.append(item)

    audit_log.record_read(request, "GET /api/search", (i["id"] for i in items))
    body = {
        "total": total, "count": len(items), "page": page, "page_size": page_size,
        "filters": filters, "max_page_size": WORKITEM_LIST_MAX_PAGE_SIZE,
        "mode": mode, "items": items,
    }
    if degraded:
        body["degraded"] = degraded
    return JSONResponse(body)
