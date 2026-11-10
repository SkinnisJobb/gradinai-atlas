"""`graph` routes, split out of `app.py` (STORY-oss-04); the code is moved, not changed.
Registered on the app by `main.py` via `include_router`.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

import models_graph  # Atlas v2 object-graph schema (ADR 0019); coexists with Component below
#: STORY-oss-16 / TASK-1382: the read audit (off unless ATLAS_AUDIT_READS=1). Every
#: `record_read` below sits after the last refusal and before the return, and is passed
#: the ids the body serves lazily, so with auditing off it costs one boolean.
import audit_log
from models_graph import Edge as GraphEdge, Node as GraphNode
from db import session
from deps import (
    _assignee_map,
    _graph_node_json,
    _graph_edge_json,
    _prose_tier_or_400,
    _trim_prose,
    _visible_node_ids,
)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Atlas v2 object-graph read API (ADR 0019, TASK-852)
#
# `/api/graph` returns a bounded neighbourhood around a focus node — BFS in BOTH
# directions along the requested edge_types up to `depth`, capped at GRAPH_NODE_CAP
# nodes (sets `truncated`). It never walks the whole graph: each hop only queries
# edges incident to the current frontier.
#
# `/api/graph/tree` is a `contains`-only projection of the node/edge store, so a
# frontend can render the work-graph as a tree. The legacy Component-backed
# `/api/tree` above is untouched (the current frontend still uses it).
# --------------------------------------------------------------------------- #
GRAPH_NODE_CAP = 500


def _own_intent_counts(s: Session, visible_ids: set[str] | None = None) -> dict[str, int]:
    """`{node_id: how many intents it OWNS}` in one GROUP BY (ADR 0032).

    OWN, never inherited. The map's `◎` says "this node is where something was
    authored", which is the one thing a glyph can usefully say — every node
    below an authored one inherits something, so an inherited-aware glyph would
    light up almost the whole tree and mean nothing.

    Precomputed per request and handed to `_graph_node_json`, the same shape as
    `counts`/`loads` above it. A per-node query here would be one round trip per
    node on a full tree read, which is exactly what those two dicts exist to
    avoid.

    `visible_ids` restricts to a node set (`None` = no restriction). The glyph
    is only drawn on nodes the caller already receives, so this closes no leak
    on its own — it is filtered for consistency, so that every dict handed to
    `_graph_node_json` was computed over the SAME node set. A helper that
    quietly spans a wider set than its siblings is how the next leak gets
    written.
    """
    q = select(models_graph.NodeIntent.node_id, func.count()).group_by(
        models_graph.NodeIntent.node_id)
    if visible_ids is not None:
        q = q.where(models_graph.NodeIntent.node_id.in_(visible_ids))
    rows = s.execute(q).all()
    return {node_id: n for node_id, n in rows}


# --------------------------------------------------------------------------- #
# WORK and META TIERS on `/api/graph` — the same subtractive ladder as `prose`,
# for the two field groups `prose=none` leaves behind.
#
# Measured 2026-09-14 through atlas-mcp: `focus=atlas-backend&depth=1&edges=
# contains&prose=none` was 9 nodes and ~100 per-workitem entries, because
# `work_load.workitems` lists every bound workitem with its status and
# completion date. With prose gone, that list IS the payload. The board needs
# it (the World tree's age filter re-derives "· N tasks · X%" client-side from
# it — see attention.work_load), so it cannot be dropped by default; an agent
# asking for the shape of a neighbourhood does not need it at all.
#
# Same two safety properties as `prose`: unset is byte-identical, and a tier
# only ever REMOVES — `work=items` and `meta=full` are no-op synonyms for
# omitting them. atlas-mcp's `atlas_tree` is the caller that sets these; the
# minimal defaults live THERE, not here, for the board's sake.
#
#   work=items   everything, as before
#   work=counts  `work_load` keeps count/done/completion; its per-item lists
#                and `urgency_blocks` (a sample of waiting workitems) go
#   work=none    the whole work group goes; `attention` stays, because it
#                already folds urgency in and is one short string
#   meta=none    owner/source/attributes/assignee/completed_at go — the docs
#                list in `attributes` is the large one, and `atlas_get` has it
_WORK_TIERS = ("none", "counts", "items")
_WORK_FIELDS = ("completion", "attention_count", "completion_counts", "state",
                "urgency", "urgency_blocks", "work_load")
_WORK_COUNT_KEYS = ("count", "done", "completion")
_META_TIERS = ("none", "full")
_META_FIELDS = ("owner", "source", "attributes", "assignee", "completed_at")


def _tier_or_400(param: str, value: str | None, legal: tuple[str, ...]) -> JSONResponse | None:
    """`_prose_tier_or_400` for the other tiers: an unknown value NAMES the legal ones."""
    if value is not None and value not in legal:
        return JSONResponse(
            {"error": f"invalid_{param}_tier", "field": param,
             "detail": f"`{param}` must be one of {', '.join(legal)} — got {value!r}. "
                       "Omit it for the full payload."},
            status_code=400,
        )
    return None


def _too_large(what: str, extra: dict) -> JSONResponse:
    """413 `too_large` for `/api/graph?strict=1` and `?max_bytes=` (TASK-1163).

    WHY THESE LIVE IN THE API AND NOT IN atlas-mcp. They were first built as
    refusals inside `atlas_tree`, which put a validation rule in a service
    whose constraint is that it holds none (intent `facade-not-second-brain`),
    enforced only for MCP callers and tested outside CI. Here any caller can
    opt in, the suite covers them, and the MCP only chooses to ask.

    WHY AN ERROR AND NOT A SMALLER BODY. A cut-off neighbourhood reads as a
    complete one; `truncated: true` announces it, and a caller that does not
    check it is exactly the caller these exist for.

    WHY 413. Strictly it names REQUEST content; there is no status for "the
    answer you asked for is too big", and 413 is the one a client can branch on
    without colliding with FastAPI's own 422 for a malformed parameter. Nothing
    hidden is counted: the walk never adds a node the caller may not see, so
    the numbers in the message are about the visible graph only.
    """
    return JSONResponse(
        {"error": "too_large", **extra,
         "detail": f"Refused rather than cut off: {what}. Narrow it — lower `depth`, name "
                   "fewer `edges`, focus on a child node, or drop a `prose`/`work`/`meta` tier."},
        status_code=413,
    )


def _trim_work_meta(d: dict, work: str | None, meta: str | None) -> dict:
    """Drop the work/meta fields a tier excludes. Unset tiers are a no-op.

    Never mutates `work_load` in place: `loads.get(..., _EMPTY_WORK_LOAD)` hands
    every unbound node the SAME module-level dict.
    """
    if work == "none":
        for key in _WORK_FIELDS:
            d.pop(key, None)
    elif work == "counts":
        d.pop("urgency_blocks", None)
        wl = d.get("work_load")
        if wl is not None:
            d["work_load"] = {k: wl[k] for k in _WORK_COUNT_KEYS if k in wl}
    if meta == "none":
        for key in _META_FIELDS:
            d.pop(key, None)
    return d


@router.get("/api/graph")
def graph(request: Request, focus: str | None = None, depth: int = 2,
          edges: str | None = None, cap: int = GRAPH_NODE_CAP, include_prose: int = 0,
          families: str | None = None, prose: str | None = None,
          work: str | None = None, meta: str | None = None,
          strict: int = 0, max_bytes: int | None = None,
          unbound_work: int = 0) -> JSONResponse:
    """Bounded neighbourhood around `focus` — or around the whole graph.

    Query params:
      focus          — node id to centre on. OMIT IT to seed the BFS from every
                       root instead (see the `focus is None` branch below), which
                       is how a caller asks for "the world" without naming a node.
      families       — comma-separated node_type families to seed from; only
                       meaningful when `focus` is omitted. `object,bridge` is
                       what the World tab wants. WITHOUT IT a rootless fetch also
                       seeds from the workitem epics (an epic has no incoming
                       `contains` either) and then walks `contains` down through
                       every story and task, because the work hierarchy and the
                       world hierarchy SHARE that edge type, permanently (a
                       separate `decomposes_into`, TASK-1030, was cancelled).
                       Measured on staging: 348 nodes unfiltered vs 42 filtered.
      depth          — BFS hops outward (both directions). Clamped to [0, 20].
      edges          — comma-separated edge_types to traverse; omitted/empty = all types.
      cap            — max nodes returned; when reached, `truncated` is true.
      include_prose  — TASK-1050, default 0. A neighbourhood is a LISTING (up
                       to `cap` nodes at once) — the body-projection fallback
                       and the raw `body` field are opt-in here for the same
                       payload-size reason `/api/graph/tree` opts them out by
                       default. See `_graph_node_json`'s `include_prose` note.
                       Authored abstract/detail/summary are unaffected either
                       way.
      prose          — OPT-IN payload tier: `none` | `summary` | `abstract` |
                       `full`. Omit it and the response is byte-identical to
                       before this parameter existed. It only ever REMOVES
                       fields, so `full` is a no-op synonym for omitting it,
                       and `none` is the skeleton — id, type, name, attention
                       and counts, ~2% of the bytes. This is the parameter to
                       reach for when you want the SHAPE of the graph; see the
                       `_PROSE_TIERS` note above for why it exists and why it
                       is not the default.
      work           — OPT-IN tier over the work fields: `none` | `counts` |
                       `items`. Omit for the full payload. See `_WORK_TIERS`.
      meta           — OPT-IN tier: `none` drops owner/source/attributes/
                       assignee/completed_at; `full` is a no-op. See `_META_TIERS`.
      strict         — OPT-IN, default 0. With 1, reaching `cap` is a 413
                       `too_large` instead of a 200 with `truncated: true`.
      max_bytes      — OPT-IN. A response body over this many bytes is a 413
                       `too_large` instead of the body. See `_too_large`.
      unbound_work   — OPT-IN, default 0; only meaningful with no `focus` and a
                       `families` that leaves the workitem family out. With 1,
                       the work the families walk could not reach is added
                       AFTER it: see "UNBOUND, PARENTLESS WORK" below (TASK-1472).
    """
    bad = (_prose_tier_or_400(prose)
           or _tier_or_400("work", work, _WORK_TIERS)
           or _tier_or_400("meta", meta, _META_TIERS))
    if bad is None and max_bytes is not None and max_bytes < 1:
        bad = JSONResponse({"error": "invalid_max_bytes", "field": "max_bytes",
                            "detail": f"`max_bytes` must be a positive integer — got {max_bytes}."},
                           status_code=400)
    if bad is not None:
        return bad
    depth = max(0, min(int(depth), 20))
    cap = max(1, int(cap))
    edge_types = [e.strip() for e in edges.split(",") if e.strip()] if edges else None

    with session() as s:
        # ACCESS FILTER (STORY-atlas-access-admin). Computed once, before the
        # BFS, and applied to the SEEDS, to every hop, and to the rollups. It
        # has to reach all three: filtering only the returned node list would
        # still walk THROUGH a hidden node to reach whatever is behind it, and
        # would still report its work in the counts.
        visible_ids = _visible_node_ids(s)

        def _may_see(nid: str) -> bool:
            return visible_ids is None or nid in visible_ids

        # Read again by the UNBOUND, PARENTLESS WORK pass below; only the
        # rootless branch sets them.
        has_parent: set[str] = set()
        want: set[str] | None = None
        fam_of: dict[str, str] = {}
        if focus is None:
            # SEED FROM EVERY ROOT. Same derivation `/api/graph/tree` uses below
            # ("a node with no incoming `contains`"), for the same reason: the
            # world is multi-root (ADR 0027) and nothing is contained by
            # everything.
            #
            # board.html used to name a seed — `WORLD_ROOT_ID =
            # "org-homelab"` — and BFS 8 hops outward. That reached the
            # other roots only because the graph HAPPENED to be connected
            # ("nothing is contained by everything, but everything is connected
            # to something"). A root with no lateral edge of its own was simply
            # absent from the World tab, with no error — and the world
            # restructure creates exactly such a root. Deriving the seeds kills
            # both problems at once, and takes the hardcoded node id out of a
            # frontend module (no module may encode the graph — see AGENTS.md).
            has_parent = set(s.scalars(
                select(GraphEdge.dst).where(GraphEdge.edge_type == "contains")
            ).all())
            want = {f.strip() for f in families.split(",") if f.strip()} if families else None
            fam_of = {nt.id: nt.family for nt in s.scalars(select(models_graph.NodeType)).all()}
            seeds = [n for n in s.scalars(select(GraphNode)).all()
                     if n.id not in has_parent
                     and (want is None or fam_of.get(n.type) in want)
                     and _may_see(n.id)]
            # NO 404 FOR AN EMPTY SEED LIST (TASK-1129). An empty store — or a
            # `families` filter nothing matches — is a legitimate answer with
            # nothing in it, not a fault, and an empty seed list falls straight
            # through to the BFS below, which yields empty `nodes`/`edges` on
            # its own without needing a branch here at all.
            #
            # There USED to be a 404 here. `/api/graph/tree` has always answered
            # 200 with an empty `roots` list off the SAME derivation ("a node
            # with no incoming `contains`"), so two routes over one derivation
            # disagreed about the same database — and the board, which fetches
            # both, printed "Could not load the backlog graph." at a brand-new
            # one. Measured on the fork, 2026-08-22.
            #
            # The `focus` refusal below is NOT this case and stays exactly as it
            # is: there the caller NAMED a node, and its 404 has to be
            # indistinguishable from "you are not granted it" (AGENTS.md — a
            # hidden node and a nonexistent one return the SAME 404). Nothing is
            # named here, so there is nothing to confirm or deny.
        else:
            root = s.get(GraphNode, focus)
            # A node the caller may not see and a node that does not exist give
            # the SAME 404 with the SAME message. Distinguishing them tells an
            # ungranted caller that the id they guessed is real, which is the
            # one fact the whole feature exists to withhold.
            if root is None or not _may_see(focus):
                return JSONResponse({"error": f"unknown focus node {focus!r}"}, status_code=404)
            seeds = [root]

        seen_nodes: dict[str, GraphNode] = {n.id: n for n in seeds}
        seen_edges: dict[int, GraphEdge] = {}
        truncated = False

        def _walk(frontier: set[str]) -> None:
            nonlocal truncated
            for _ in range(depth):
                if not frontier:
                    break
                # Only query edges incident to the current frontier — never the whole graph.
                q = select(GraphEdge).where(
                    or_(GraphEdge.src.in_(frontier), GraphEdge.dst.in_(frontier))
                )
                if edge_types is not None:
                    q = q.where(GraphEdge.edge_type.in_(edge_types))

                next_frontier: set[str] = set()
                for e in s.scalars(q).all():
                    seen_edges[e.id] = e
                    for nid in (e.src, e.dst):  # both directions — no fixed root
                        if nid in seen_nodes:
                            continue
                        if not _may_see(nid):
                            # Not expanded AND not added, so the walk cannot pass
                            # through a hidden node to reach what is behind it.
                            continue
                        if len(seen_nodes) >= cap:
                            truncated = True
                            continue
                        n = s.get(GraphNode, nid)
                        if n is not None:
                            seen_nodes[n.id] = n
                            next_frontier.add(n.id)
                frontier = next_frontier

        _walk(set(seen_nodes))

        # UNBOUND, PARENTLESS WORK (TASK-1472), opt-in with `unbound_work=1`.
        #
        # The board asks for `families=object,bridge` so that the seeds are the
        # WORLD's roots, and the work arrives by walking the binding edges out
        # of the world. A workitem with no `contains` parent and no binding is
        # connected to nothing the world can reach, so that walk never finds it:
        # filed through POST /api/workitem with neither, it existed, its creator
        # could read and search it, and it was on no board and in no board
        # search. Measured on a test install.
        #
        # The fix is a SECOND pass, after the first is complete: seed from every
        # work-family root (no incoming `contains`) the first pass did not
        # reach, and walk from those under the same rules. AFTER, not merged
        # into the first seeds, for two reasons:
        #   - the first pass's answer is unchanged, node for node, so what the
        #     board already showed is exactly what it shows now, plus the extra;
        #   - the `cap` is spent on the world first. Work added here only ever
        #     fills what the cap has left; a root it has no room for sets
        #     `truncated`, the same signal a capped walk already gives.
        # "Not reached" rather than "no binding edge": it is the precise set the
        # first pass missed, and needs no list of binding edge types here.
        #
        # VISIBILITY is the first pass's: every seed goes through `_may_see`
        # (the caller's `_visible_node_ids`), and `_walk` adds nothing it may
        # not see. The rollups below take the same `visible_ids`, so no count
        # moves for anything hidden either.
        #
        # Opt-in because the legacy board (/board/legacy) sends the same
        # `families` for its World tree and is frozen (ADR 0043); without the
        # parameter this route answers byte-for-byte as before.
        if (unbound_work and focus is None and want is not None
                and "workitem" not in want):
            missed = sorted(
                (n for n in s.scalars(select(GraphNode)).all()
                 if fam_of.get(n.type) == "workitem"
                 and n.id not in has_parent
                 and n.id not in seen_nodes
                 and _may_see(n.id)),
                key=lambda n: n.id)
            extra: set[str] = set()
            for n in missed:
                if n.id in seen_nodes:
                    continue
                if len(seen_nodes) >= cap:
                    truncated = True
                    break
                seen_nodes[n.id] = n
                extra.add(n.id)
            _walk(extra)

        # Keep only edges whose BOTH endpoints made it into the node set, so the
        # client never receives an edge dangling to a capped-out node.
        node_ids = set(seen_nodes)
        kept_edges = [e for e in seen_edges.values()
                      if e.src in node_ids and e.dst in node_ids]

        # done/in-progress/todo breakdown for the returned nodes (computed over
        # the FULL contains subtree, not just the neighbourhood, so the ring is
        # correct even when the neighbourhood is depth-bounded).
        if strict and truncated:
            # Before the rollups: a refused walk should not pay for them.
            return _too_large(
                f"more than {cap} nodes within depth={depth}, "
                f"edges={','.join(edge_types) if edge_types else 'all'}",
                {"cap": cap})

        from attention import derive as attention_derive, work_load as attention_work_load
        counts = attention_derive(s, visible_ids=visible_ids)
        loads = attention_work_load(s, visible_ids=visible_ids)
        intent_counts = _own_intent_counts(s, visible_ids=visible_ids)
        assignees = _assignee_map(s)

        resp = JSONResponse({
            "focus": focus,
            "depth": depth,
            "edge_types": edge_types,  # null = all
            "nodes": [_trim_work_meta(_trim_prose(
                          _graph_node_json(n, counts, loads, include_prose=bool(include_prose),
                                           intent_counts=intent_counts, assignees=assignees),
                          prose), work, meta)
                      for n in seen_nodes.values()],
            "edges": [_graph_edge_json(e) for e in kept_edges],
            "truncated": truncated,
        })
        if max_bytes is not None and len(resp.body) > max_bytes:
            return _too_large(
                f"{len(resp.body)} bytes for {len(seen_nodes)} nodes, over max_bytes={max_bytes}",
                {"bytes": len(resp.body), "max_bytes": max_bytes})
        audit_log.record_read(request, "GET /api/graph", iter(seen_nodes))
        return resp


def _graph_tree_node(nid: str, kids_of: dict[str, list[str]],
                     nodes: dict[str, GraphNode], counts: dict[str, dict],
                     seen: set[str], loads: dict[str, dict] | None = None,
                     include_prose: bool = False,
                     assignees: dict[str, dict] | None = None,
                     prose: str | None = None) -> dict:
    """Recursively project the `contains` DAG into a tree dict (cycle-safe)."""
    n = nodes[nid]
    out = _trim_prose(
        _graph_node_json(n, counts, loads, include_prose=include_prose,
                         assignees=assignees),
        prose)
    children = []
    if nid not in seen:
        seen = seen | {nid}
        for child_id in sorted(kids_of.get(nid, [])):
            if child_id in nodes:
                children.append(_graph_tree_node(child_id, kids_of, nodes, counts, seen, loads,
                                                  include_prose, assignees, prose))
    out["children"] = children
    return out


@router.get("/api/graph/tree")
def graph_tree(include_prose: int = 0, prose: str | None = None) -> JSONResponse:
    """`contains`-only projection of the node/edge store as a tree (roots = nodes
    with no incoming `contains` edge).

    `include_prose` (TASK-1050, default 0): THE full-graph read this task's
    acceptance criterion names ("the response ... does not grow by 7,460
    lines of prose"). Every node in the whole store is in this one response,
    so the body-projection fallback (and the raw `body` field) stay off by
    default — pass `?include_prose=1` to opt back in for every node at once
    (expensive on purpose; a per-item fetch is `GET /api/node/{id}`).
    Authored abstract/detail/summary (the world tab's own descriptions) are
    unaffected either way — see `_graph_node_json`'s `include_prose` note.

    `prose` (OPT-IN, and the one that trims the authored fields
    `include_prose` deliberately does not): `none` | `summary` | `abstract` |
    `full`. Omitted, this response is byte-identical to before. This is the
    whole store in one payload, so it is also where the tier pays best —
    `prose=none` is the shape of the graph and nothing else. See the
    `_PROSE_TIERS` note above `/api/graph` for the measurement behind it, and
    for why no tier is the default.
    """
    bad = _prose_tier_or_400(prose)
    if bad is not None:
        return bad
    with session() as s:
        # ACCESS FILTER (STORY-atlas-access-admin). Applied to the NODE DICT,
        # which is what every line below derives from — `kids_of`, `has_parent`
        # and therefore the ROOT LIST. Dropping a private node here does not
        # orphan its children onto the root list, because the rule that hid it
        # already hid everything beneath it (visibility is ALL-over-parents, so
        # a child of a hidden node is hidden too). A `private-splice` mode
        # would change that, which is why it stays unimplemented and unwritten.
        visible_ids = _visible_node_ids(s)
        nodes = {n.id: n for n in s.scalars(select(GraphNode)).all()
                 if visible_ids is None or n.id in visible_ids}
        kids_of: dict[str, list[str]] = {}
        has_parent: set[str] = set()
        for e in s.scalars(select(GraphEdge).where(GraphEdge.edge_type == "contains")).all():
            if e.src in nodes and e.dst in nodes:
                kids_of.setdefault(e.src, []).append(e.dst)
                has_parent.add(e.dst)
        from attention import derive as attention_derive, work_load as attention_work_load
        counts = attention_derive(s, visible_ids=visible_ids)
        loads = attention_work_load(s, visible_ids=visible_ids)
        roots = sorted(nid for nid in nodes if nid not in has_parent)
        return JSONResponse({
            "roots": [_graph_tree_node(r, kids_of, nodes, counts, set(), loads,
                                        bool(include_prose), _assignee_map(s), prose)
                      for r in roots]
        })


@router.get("/api/graph/outline")
def graph_outline(request: Request, root: str | None = None,
                  families: str | None = "object,bridge", max_depth: int = 0, names: int = 0) -> JSONResponse:
    """The `contains` tree as INDENTED TEXT — the shape of the graph, nothing else.

    This exists because "what have we actually got?" is the cheapest question
    to ask and, until now, the most expensive one to answer. `/api/graph/tree`
    is the whole store with every authored abstract attached; a 7-node
    neighbourhood measured 51.6 KB on 2026-09-10, of which 89% was prose and
    1% was edges. An agent traversing that spends its context on descriptions
    it did not ask for, then has none left to reason with.

    So: one line per node, `id [type]`, about 40 bytes. The whole world tree
    lands in a few KB. Read the shape here, then fetch the ONE node whose
    prose you actually need with `GET /api/node/{id}`.

    Query params:
      root       — start at this node instead of at every derived root. 404 if
                   it does not exist OR is not visible to you — the same
                   indistinguishable 404 every other route returns, so this
                   cannot be used to probe for hidden ids.
      families   — comma-separated node_type families, DEFAULT `object,bridge`
                   (the world). This default is the point of the route: the
                   work hierarchy shares the `contains` edge with the world
                   hierarchy, so an unfiltered walk falls straight through an
                   epic into every story and task beneath it. Pass
                   `families=all` if you genuinely want the backlog too.
      max_depth  — 0 (default) = unlimited. Otherwise stop after N levels and
                   report `truncated: true`, so a deep tree cannot silently
                   look complete.
      names      — 1 appends ` · <name>` to each line. Off by default: ids in
                   this estate are already descriptive, and the name is
                   usually a near-duplicate of it.

    Returns `{text, count, roots, truncated}` — `text` is the rendered tree,
    `count` is how many nodes it actually contains. Compare `count` against
    what you expected rather than eyeballing the text; a `families` filter
    that matched nothing renders as an empty string, which reads like a fault
    and is not one.
    """
    max_depth = max(0, int(max_depth))
    with session() as s:
        # Same access filter, same `contains` derivation and the same
        # root rule as `/api/graph/tree` above — deliberately, so the two
        # cannot disagree about what the tree IS. Only the rendering differs.
        visible_ids = _visible_node_ids(s)
        fam_of = {nt.id: nt.family for nt in s.scalars(select(models_graph.NodeType)).all()}
        want = None
        if families and families.strip().lower() != "all":
            want = {f.strip() for f in families.split(",") if f.strip()}
        nodes = {n.id: n for n in s.scalars(select(GraphNode)).all()
                 if (visible_ids is None or n.id in visible_ids)
                 and (want is None or fam_of.get(n.type) in want)}

        if root is not None and root not in nodes:
            # Indistinguishable from "filtered out by `families`", and that is
            # correct: a caller must not be able to tell a hidden node from an
            # object-family filter miss.
            return JSONResponse({"error": "not_found", "detail": root}, status_code=404)

        kids_of: dict[str, list[str]] = {}
        has_parent: set[str] = set()
        for e in s.scalars(select(GraphEdge).where(GraphEdge.edge_type == "contains")).all():
            if e.src in nodes and e.dst in nodes:
                kids_of.setdefault(e.src, []).append(e.dst)
                has_parent.add(e.dst)

        roots = [root] if root is not None else sorted(
            nid for nid in nodes if nid not in has_parent)

        lines: list[str] = []
        counted: dict[str, None] = {}  # a set that keeps the order served (the audit's)
        truncated = False

        def walk(nid: str, prefix: str, is_last: bool, depth: int, seen: frozenset) -> None:
            nonlocal truncated
            n = nodes[nid]
            if depth == 0:
                stem = ""
            else:
                stem = prefix + ("└─ " if is_last else "├─ ")
            line = f"{stem}{n.id} [{n.type}]"
            if names and n.name:
                line += f" · {n.name}"
            lines.append(line)
            counted[nid] = None

            # CYCLE GUARD. `contains` is a DAG, not a tree — several parents is
            # normal (ADR 0030) — so a node can legitimately be reached twice.
            # Re-entering the same node ON ITS OWN PATH is what must not happen.
            if nid in seen:
                return
            if max_depth and depth + 1 >= max_depth:
                if kids_of.get(nid):
                    truncated = True
                return
            kids = sorted(kids_of.get(nid, []))
            child_prefix = prefix if depth == 0 else prefix + ("   " if is_last else "│  ")
            for i, kid in enumerate(kids):
                walk(kid, child_prefix, i == len(kids) - 1, depth + 1, seen | {nid})

        for r in roots:
            walk(r, "", True, 0, frozenset())

        audit_log.record_read(request, "GET /api/graph/outline", iter(counted))
        return JSONResponse({
            "text": "\n".join(lines),
            "count": len(counted),
            "roots": roots,
            "families": sorted(want) if want else "all",
            "truncated": truncated,
        })


# --------------------------------------------------------------------------- #
# PATHS: "how is X connected to Y?" (TASK-1298).
#
# `/api/graph` answers "what is around X", `/structure` "what does X link to".
# Neither answers the question an agent asks when two nodes it already knows
# turn out to be related: by WHICH chain of edges. Walking `/api/graph` outward
# from both ends until they meet is the expensive way to ask it, and returns
# every node passed on the way rather than the route.
#
# THE WALK IS OVER THE CALLER'S VISIBLE GRAPH, NOT A FILTER OVER THE ANSWER.
# The node set is `_visible_node_ids` before a single edge is loaded, and an
# edge enters the adjacency only when BOTH its endpoints are in it. Filtering
# the finished paths instead would be wrong twice: it would drop a route that
# runs through a hidden node and silently return fewer than `max` -- telling
# the caller that a shorter route exists which they may not see -- and it would
# let the hidden route's existence change which visible routes were found
# second and third. Over the visible set, a member's answer is the answer the
# graph would give with the hidden rows physically deleted, which is exactly
# what `verify_graph_path.py` §C compares it against.
#
# ONE 404 FOR "NO SUCH NODE" AND "NOT YOURS". `from` and `to` are resolved
# against the same visible set, so a hidden id and an absent one return the
# same status and the same body, for either end -- the rule every other route
# that names a node follows.
#
# BOUNDED, ON PURPOSE. `max` paths (default 3, clamped to 10) of at most `hops`
# edges (default and ceiling 8). Yen's algorithm over an unweighted BFS: at
# most `max` x `hops` spur searches, each one BFS over the visible edges. The
# edges are loaded once per request; on today's estate that is a few thousand
# rows, the same order `/api/graph/tree` already loads for `contains` alone.
#
# DIRECTION. Edges are traversed both ways, because "connected" does not care
# which way an edge was written, but each hop reports how it was taken: `out`
# when the walk follows the edge (src -> dst), `in` when it goes against it, and
# `none` for an undirected edge. A path is a sequence of EDGES, so two edges of
# different types between the same pair are two paths; `edges=` narrows that.
# --------------------------------------------------------------------------- #
PATH_MAX_DEFAULT = 3
PATH_MAX_CAP = 10
PATH_HOPS_CAP = 8


def _path_bfs(start: str, goal: str, adj: dict[str, list[tuple[int, str]]],
              banned_nodes: set[str], banned_edges: set[int],
              limit: int) -> list[tuple[int, str]] | None:
    """The shortest route `start` -> `goal` within `limit` hops, as
    `[(edge_id, node_reached), ...]`, or None. `[]` when start is the goal.

    Deterministic: `adj` is pre-sorted, and the first parent to reach a node
    keeps it, so one database gives one answer.
    """
    if start == goal:
        return []
    prev: dict[str, tuple[str, int] | None] = {start: None}
    frontier = [start]
    for _ in range(limit):
        nxt: list[str] = []
        for u in frontier:
            for eid, v in adj.get(u, ()):
                if eid in banned_edges or v in banned_nodes or v in prev:
                    continue
                prev[v] = (u, eid)
                if v == goal:
                    steps: list[tuple[int, str]] = []
                    cur = v
                    while prev[cur] is not None:
                        p, e = prev[cur]
                        steps.append((e, cur))
                        cur = p
                    return steps[::-1]
                nxt.append(v)
        if not nxt:
            break
        frontier = nxt
    return None


def _k_shortest_paths(src: str, dst: str, adj: dict[str, list[tuple[int, str]]],
                      k: int, hops: int) -> list[tuple[tuple[str, ...], tuple[int, ...]]]:
    """Yen's k shortest LOOPLESS paths, unweighted, each at most `hops` edges.

    A path is `(nodes, edges)`: nodes[0] is `src`, edges[i] joins nodes[i] to
    nodes[i+1]. Ties break on the node ids, then the edge ids, so the order is
    stable across calls.
    """
    first = _path_bfs(src, dst, adj, set(), set(), hops)
    if first is None:
        return []
    found = [((src, *(n for _, n in first)), tuple(e for e, _ in first))]
    seen = {found[0][1]}
    candidates: list[tuple[tuple[str, ...], tuple[int, ...]]] = []
    while len(found) < k:
        nodes, edges = found[-1]
        for i in range(len(edges)):
            root_nodes, root_edges = nodes[:i + 1], edges[:i]
            # Every edge that an already-found path takes out of this same
            # root is banned, so the spur must leave by a different one; the
            # root's own nodes are banned so the result stays loopless.
            banned_edges = {p_edges[i] for _, p_edges in found
                            if len(p_edges) > i and p_edges[:i] == root_edges}
            spur = _path_bfs(nodes[i], dst, adj, set(root_nodes[:-1]), banned_edges, hops - i)
            if spur is None:
                continue
            cand = (root_nodes + tuple(n for _, n in spur), root_edges + tuple(e for e, _ in spur))
            if cand[1] not in seen:
                seen.add(cand[1])
                candidates.append(cand)
        if not candidates:
            break
        best = min(candidates, key=lambda c: (len(c[1]), c[0], c[1]))
        candidates.remove(best)
        found.append(best)
    return found


@router.get("/api/graph/path")
def graph_path(request: Request, from_: str = Query(..., alias="from"), to: str = Query(...),
               max_: int = Query(PATH_MAX_DEFAULT, alias="max"), edges: str | None = None,
               hops: int = PATH_HOPS_CAP) -> JSONResponse:
    """How `from` is connected to `to`: the `max` shortest paths between them.

    Query params:
      from, to  — node ids, both required. Either one unknown OR not visible to
                  you is the same 404 with the same body.
      max       — how many paths, shortest first. Default 3, clamped to [1, 10].
      edges     — comma-separated edge types to traverse; omitted = all types.
      hops      — the longest path considered, in edges. Default and ceiling 8.

    Returns `{from, to, edge_types, hops_max, paths, nodes}`. Each path is
    `{length, hops}`, each hop `{from, to, edge_type, direction, edge}` with
    `direction` `out` (along the edge), `in` (against it) or `none`
    (undirected). `nodes` maps every id on any path to `{type, name}`, once.
    No path within `hops` is a 200 with an empty `paths`, not a 404: both
    nodes exist and are yours, and "not connected" is an answer.
    """
    k = max(1, min(int(max_), PATH_MAX_CAP))
    hop_limit = max(1, min(int(hops), PATH_HOPS_CAP))
    edge_types = [e.strip() for e in edges.split(",") if e.strip()] if edges else None
    with session() as s:
        visible_ids = _visible_node_ids(s)
        q_nodes = select(GraphNode.id)
        if visible_ids is not None:
            q_nodes = q_nodes.where(GraphNode.id.in_(visible_ids))
        node_ids = set(s.scalars(q_nodes).all())
        for nid in (from_, to):
            if nid not in node_ids:
                return JSONResponse({"error": "not_found", "detail": f"unknown node {nid!r}"},
                                    status_code=404)

        q_edges = select(GraphEdge.id, GraphEdge.src, GraphEdge.dst,
                         GraphEdge.edge_type, GraphEdge.directed)
        if edge_types is not None:
            q_edges = q_edges.where(GraphEdge.edge_type.in_(edge_types))
        edge_rows: dict[int, tuple] = {}
        adj: dict[str, list[tuple[int, str]]] = {}
        for eid, src, dst, etype, directed in s.execute(q_edges).all():
            # Both ends visible, or the edge does not exist for this caller.
            if src not in node_ids or dst not in node_ids:
                continue
            edge_rows[eid] = (src, dst, etype, directed)
            adj.setdefault(src, []).append((eid, dst))
            adj.setdefault(dst, []).append((eid, src))
        for nbrs in adj.values():
            nbrs.sort(key=lambda h: (edge_rows[h[0]][2], h[1], h[0]))

        found = _k_shortest_paths(from_, to, adj, k, hop_limit)

        def hop(u: str, v: str, eid: int) -> dict:
            src, _, etype, directed = edge_rows[eid]
            direction = "none" if not directed else ("out" if src == u else "in")
            return {"from": u, "to": v, "edge_type": etype, "direction": direction, "edge": eid}

        on_path = {n for nodes, _ in found for n in nodes} | {from_, to}
        rows = {n.id: n for n in s.scalars(select(GraphNode).where(GraphNode.id.in_(on_path))).all()}
        audit_log.record_read(request, "GET /api/graph/path",
                              (nid for nid in sorted(on_path) if nid in rows))
        return JSONResponse({
            "from": from_,
            "to": to,
            "edge_types": edge_types,  # null = all
            "hops_max": hop_limit,
            "paths": [{"length": len(e),
                       "hops": [hop(n[i], n[i + 1], e[i]) for i in range(len(e))]}
                      for n, e in found],
            "nodes": {nid: {"type": rows[nid].type, "name": rows[nid].name}
                      for nid in sorted(on_path) if nid in rows},
        })
