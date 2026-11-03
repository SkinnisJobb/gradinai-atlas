"""`edges` routes, split out of `app.py` (STORY-oss-04); the code is moved, not changed.
Registered on the app by `main.py` via `include_router`.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import auth         # THE GATE (TASK-967) + service tokens (TASK-968)
from models_graph import Edge as GraphEdge, Node as GraphNode
from db import session
from deps import (
    _graph_edge_json,
    record_graph_event,
    EDGE_FAMILY_RULES,
    _check_family,
    _refuse_second_binding,
    EdgeDelete,
    _visible_node_ids,
    _require_project_role,
)
import rbac

router = APIRouter()


class EdgeWrite(BaseModel):
    src: str
    dst: str
    edge_type: str
    directed: bool = True
    csrf_token: str | None = None


@router.post("/api/edge")
def create_edge(body: EdgeWrite, request: Request) -> JSONResponse:
    from attention import recompute_derived

    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused

    with session() as s:
        src = s.get(GraphNode, body.src)
        dst = s.get(GraphNode, body.dst)
        # An endpoint the caller cannot see is reported as MISSING, not as
        # forbidden: otherwise POSTing an edge is a working existence oracle
        # for any id someone cares to guess.
        visible_ids = _visible_node_ids(s)
        if visible_ids is not None:
            if body.src not in visible_ids:
                src = None
            if body.dst not in visible_ids:
                dst = None
        missing = [nid for nid, n in ((body.src, src), (body.dst, dst)) if n is None]
        if missing:
            return JSONResponse(
                {"error": "unknown_node", "detail": f"unknown node id(s): {missing}"},
                status_code=404,
            )

        # ADR 0041 (RBAC on only): an edge is a change to both of its ends.
        refused = _require_project_role(s, [body.src, body.dst], rbac.EDITOR)
        if refused is not None:
            return refused

        fam_err = _check_family(s, body.edge_type, EDGE_FAMILY_RULES.get(body.edge_type), src, dst)
        if fam_err is not None:
            return fam_err

        # ADR 0028 §1 (TASK-1048): AT MOST ONE works_on binding, enforced here
        # too — not only in the higher-level /api/workitem routes — so this
        # generic construction-kit route cannot be used to build a shape the
        # ingest already refuses.
        if body.edge_type == "works_on":
            refuse = _refuse_second_binding(s, body.src)
            if refuse is not None:
                return refuse

        e = GraphEdge(src=body.src, dst=body.dst, edge_type=body.edge_type,
                      directed=body.directed, origin="atlas")
        s.add(e)
        s.flush()

        # TASK-1045 — after the flush, so `e.id` is the real autoincrement
        # value rather than None. Both endpoints are recorded as the governing
        # nodes: an edge into a private node is a statement about that node.
        record_graph_event(s, "edge.create", entity_type="edge", entity_id=e.id,
                           node_id=e.src, related_node_id=e.dst,
                           detail={"src": e.src, "dst": e.dst, "edge_type": e.edge_type})

        recompute_derived(s)
        s.refresh(e)
        return JSONResponse(_graph_edge_json(e), status_code=201)


@router.delete("/api/edge/{edge_id}")
def delete_edge(edge_id: int, request: Request, body: EdgeDelete = EdgeDelete()) -> JSONResponse:
    from attention import recompute_derived

    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused

    with session() as s:
        e = s.get(GraphEdge, edge_id)
        # An edge id is a small integer, so this route is the cheapest thing to
        # enumerate in the whole API. An edge with an endpoint the caller
        # cannot see is reported exactly as a nonexistent one — otherwise
        # counting upwards from 1 maps the shape of every hidden subtree.
        if e is not None:
            _vis = _visible_node_ids(s)
            if _vis is not None and (e.src not in _vis or e.dst not in _vis):
                e = None
        if e is None:
            return JSONResponse({"error": f"unknown edge {edge_id}"}, status_code=404)
        # ADR 0041 §2 (RBAC on only): maintainer on BOTH ends' projects, so a
        # cross-project edge needs it in each.
        refused = _require_project_role(s, [e.src, e.dst], rbac.MAINTAINER)
        if refused is not None:
            return refused
        # TASK-1045 — BEFORE the delete: an edge id is meaningless afterwards,
        # so `src`/`dst`/`edge_type` are copied into `detail` while they can
        # still be read. This is the one action where `entity_id` alone tells a
        # future reader nothing at all.
        record_graph_event(s, "edge.delete", entity_type="edge", entity_id=e.id,
                           node_id=e.src, related_node_id=e.dst,
                           detail={"src": e.src, "dst": e.dst, "edge_type": e.edge_type})
        s.delete(e)
        s.flush()

        recompute_derived(s)
        return JSONResponse({"deleted": edge_id})
