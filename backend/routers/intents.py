"""`intents` routes, split out of `app.py` (STORY-oss-04); the code is moved, not changed.
Registered on the app by `main.py` via `include_router`.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import models_graph  # Atlas v2 object-graph schema (ADR 0019); coexists with Component below
import auth         # THE GATE (TASK-967) + service tokens (TASK-968)
from models_graph import Node as GraphNode
from db import session
import audit_log  # STORY-oss-16 / TASK-1382: the read audit (off unless ATLAS_AUDIT_READS=1)
from deps import (
    record_graph_event,
    _visible_node_ids,
    _may_see_node,
    _guard_node_access,
    _require_project_role,
    _intent_json,
)
import rbac

router = APIRouter()


class IntentWrite(BaseModel):
    """Body for `POST /api/node/{node_id}/intent`.

    `position` is omitted by callers that just want to append — the default
    is end-of-list, because "the most important first" is an ordering a human
    curates, not one a create call should have to compute.
    """
    slug: str
    kind: str
    title: str
    statement: str
    source: str | None = None
    position: int | None = None
    csrf_token: str | None = None


class IntentPatch(BaseModel):
    slug: str | None = None
    kind: str | None = None
    title: str | None = None
    statement: str | None = None
    source: str | None = None
    position: int | None = None
    csrf_token: str | None = None


class IntentDelete(BaseModel):
    csrf_token: str | None = None


def _intent_field_error(field: str, detail: str) -> JSONResponse:
    """400 that NAMES the field, matching `_attribute_validation_response`'s
    shape. A write API that returns "invalid" without saying which field is a
    write API that gets retried by hand."""
    return JSONResponse({"error": "invalid_intent", "field": field, "detail": detail},
                        status_code=400)


def _validate_intent_fields(kind: str | None, slug: str | None, title: str | None,
                            statement: str | None) -> JSONResponse | None:
    from node_types import INTENT_KINDS  # local import: see startup()

    if kind is not None and kind not in INTENT_KINDS:
        return _intent_field_error(
            "kind", f"{kind!r} is not one of {', '.join(INTENT_KINDS)}")
    if slug is not None and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", slug):
        return _intent_field_error(
            "slug", "must be lowercase kebab-case, 1-64 chars, starting alphanumeric")
    for name, value in (("title", title), ("statement", statement)):
        if value is not None and not value.strip():
            return _intent_field_error(name, "must not be empty")
    return None


def _renumber_intents(s: Session, node_id: str) -> None:
    """Make `position` dense 0..n-1 again, preserving current order.

    Called after every create/patch/delete for the same reason
    `_replace_acceptance` reinserts rather than patching: `position` is list
    order, and a list order with holes in it is one an ordering bug can hide
    in. Ties (two rows given the same position by a caller) break by id, so
    the result is deterministic rather than whatever the query planner
    returned."""
    rows = s.scalars(
        select(models_graph.NodeIntent)
        .where(models_graph.NodeIntent.node_id == node_id)
    ).all()
    rows.sort(key=lambda r: (r.position, r.id))
    for i, r in enumerate(rows):
        r.position = i


@router.get("/api/intents")
def list_intents(request: Request, kind: str | None = None,
                 node_id: str | None = None) -> JSONResponse:
    """The whole intent corpus, owner-side — every row, not the inherited
    projection.

    THIS IS THE ENDPOINT THE FEATURE EXISTS FOR. "What work would make us
    compliant with every stated intent?" is a question across the whole tree,
    and answering it from `GET /api/node/{id}` would mean 72 calls whose
    inherited halves mostly duplicate each other. It is also the surface
    `atlas-mcp` reads once it is built — that service is specified read-only,
    which is what keeps an agent from weakening a principle so its own work
    passes (ADR 0032).

    Owner-side deliberately: a compliance run wants each statement ONCE, at
    the node responsible for it, not once per descendant that inherits it.
    """
    from node_types import INTENT_KINDS  # local import: see startup()

    with session() as s:
        visible_ids = _visible_node_ids(s)
        stmt = select(models_graph.NodeIntent)
        if kind is not None:
            if kind not in INTENT_KINDS:
                return _intent_field_error(
                    "kind", f"{kind!r} is not one of {', '.join(INTENT_KINDS)}")
            stmt = stmt.where(models_graph.NodeIntent.kind == kind)
        if node_id is not None:
            stmt = stmt.where(models_graph.NodeIntent.node_id == node_id)
        if visible_ids is not None:
            stmt = stmt.where(models_graph.NodeIntent.node_id.in_(visible_ids))
        rows = s.scalars(
            stmt.order_by(models_graph.NodeIntent.node_id, models_graph.NodeIntent.position)
        ).all()
        owner_ids = {r.node_id for r in rows}
        names = {
            n.id: n.name
            for n in s.scalars(select(GraphNode).where(GraphNode.id.in_(owner_ids))).all()
        } if owner_ids else {}
        # TASK-1382: the nodes whose intents were served, each once, in the order served.
        audit_log.record_read(request, "GET /api/intents", (r.node_id for r in rows))
        return JSONResponse({
            "intents": [_intent_json(r, owner_name=names.get(r.node_id)) for r in rows],
            "count": len(rows),
            "kinds": list(INTENT_KINDS),
        })


@router.post("/api/node/{node_id}/intent")
def create_intent(node_id: str, body: IntentWrite, request: Request) -> JSONResponse:
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused

    bad = _validate_intent_fields(body.kind, body.slug, body.title, body.statement)
    if bad is not None:
        return bad

    with session() as s:
        if s.get(GraphNode, node_id) is None:
            return JSONResponse({"error": f"unknown node {node_id!r}"}, status_code=404)
        denied = _guard_node_access(s, node_id) or _require_project_role(s, [node_id], rbac.EDITOR)
        if denied is not None:
            return denied
        clash = s.scalars(
            select(models_graph.NodeIntent).where(
                models_graph.NodeIntent.node_id == node_id,
                models_graph.NodeIntent.slug == body.slug)
        ).first()
        if clash is not None:
            # 409, not 400: the request is well-formed, the STATE conflicts.
            # The unique index would raise anyway; catching it here is what
            # turns an IntegrityError 500 into something a caller can act on.
            return JSONResponse(
                {"error": "intent_exists",
                 "detail": f"node {node_id!r} already has an intent with slug {body.slug!r}"},
                status_code=409)

        if body.position is None:
            n_existing = s.scalar(
                select(func.count()).select_from(models_graph.NodeIntent)
                .where(models_graph.NodeIntent.node_id == node_id)) or 0
            position = n_existing
        else:
            position = body.position

        row = models_graph.NodeIntent(
            node_id=node_id, slug=body.slug, kind=body.kind, title=body.title,
            statement=body.statement, source=body.source, position=position)
        s.add(row)
        s.flush()
        _renumber_intents(s, node_id)

        # TASK-1045. `entity_id` is the OWNING NODE for the same reason the doc
        # events use it — an intent is keyed by `(node_id, slug)` and inherits
        # DOWN `contains` from that node; it has no life apart from it. The
        # intent's own integer id travels in `detail`, where it is a pointer
        # rather than the thing the access filter reads. That filter reads
        # `node_id`, which defaults to `entity_id` here, so an intent authored
        # on a private project is as invisible as the project.
        record_graph_event(s, "node.intent.create", entity_type="node", entity_id=node_id,
                           detail={"intent_id": row.id, "slug": row.slug, "kind": row.kind})

        # COMMIT, not just flush -- `session()` does not commit, and the only
        # commit reachable from a request path in this module is
        # `recompute_derived`'s. An intent changes no derived value (it is not
        # health, not attention, not a work-load count), so calling that just
        # to persist a row would be a lie about what changed; this route says
        # so itself, exactly like the node-doc PUT below. The first version of
        # THIS route flushed and returned 201 while the row rolled back on
        # session exit, which is the same defect that route's comment records
        # -- `verify_node_intent.py` section B caught it the same way.
        payload = _intent_json(row)
        s.commit()
        return JSONResponse(payload, status_code=201)


@router.patch("/api/intent/{intent_id}")
def edit_intent(intent_id: int, body: IntentPatch, request: Request) -> JSONResponse:
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused

    bad = _validate_intent_fields(body.kind, body.slug, body.title, body.statement)
    if bad is not None:
        return bad

    with session() as s:
        row = s.get(models_graph.NodeIntent, intent_id)
        # The 404 for "you may not see the node this intent belongs to" is the
        # SAME 404 as "no such intent" — an intent id is a small integer, so a
        # distinguishable refusal here would let anyone enumerate the intents
        # of every hidden node by counting upwards.
        if row is None or not _may_see_node(s, row.node_id):
            return JSONResponse({"error": f"unknown intent {intent_id}"}, status_code=404)
        refused = _require_project_role(s, [row.node_id], rbac.EDITOR)
        if refused is not None:
            return refused

        updates = body.model_dump(exclude_unset=True, exclude={"csrf_token"})
        new_slug = updates.get("slug")
        if new_slug is not None and new_slug != row.slug:
            clash = s.scalars(
                select(models_graph.NodeIntent).where(
                    models_graph.NodeIntent.node_id == row.node_id,
                    models_graph.NodeIntent.slug == new_slug)
            ).first()
            if clash is not None:
                return JSONResponse(
                    {"error": "intent_exists",
                     "detail": f"node {row.node_id!r} already has an intent with slug {new_slug!r}"},
                    status_code=409)

        applied: list[str] = []
        for field, value in updates.items():
            if value is None and field in ("slug", "kind", "title", "statement", "position"):
                continue  # NOT NULL columns — omit rather than corrupt the row
            setattr(row, field, value)
            applied.append(field)
        s.flush()
        _renumber_intents(s, row.node_id)

        # TASK-1045. FIELD NAMES, not values, exactly as `edit_node` records
        # them — `statement` is prose and this table is not a diff. Recorded
        # only when something was actually applied: a PATCH carrying nothing but
        # a csrf token, or one whose every field was dropped by the NOT NULL
        # guard above, changed no row and must not leave one saying it did.
        #
        # `_renumber_intents` may also move the POSITION of this node's other
        # intents. Not recorded per sibling: it is a consequence of the act
        # already recorded here, on the same node, and one event per shuffled
        # row would bury the act that caused them.
        if applied:
            record_graph_event(s, "node.intent.update", entity_type="node",
                               entity_id=row.node_id,
                               detail={"intent_id": intent_id, "slug": row.slug,
                                       "fields": sorted(applied)})
        payload = _intent_json(row)
        s.commit()   # same reason as create_intent above
        return JSONResponse(payload)


@router.delete("/api/intent/{intent_id}")
def delete_intent(intent_id: int, body: IntentDelete, request: Request) -> JSONResponse:
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused

    with session() as s:
        row = s.get(models_graph.NodeIntent, intent_id)
        # The 404 for "you may not see the node this intent belongs to" is the
        # SAME 404 as "no such intent" — an intent id is a small integer, so a
        # distinguishable refusal here would let anyone enumerate the intents
        # of every hidden node by counting upwards.
        if row is None or not _may_see_node(s, row.node_id):
            return JSONResponse({"error": f"unknown intent {intent_id}"}, status_code=404)
        refused = _require_project_role(s, [row.node_id], rbac.MAINTAINER)
        if refused is not None:
            return refused
        node_id = row.node_id
        # TASK-1045 — captured while the row is still readable. `slug` and
        # `kind` are what a reader needs to know WHICH intent went; the integer
        # id resolves to nothing afterwards, the same way a deleted edge's does.
        gone = {"intent_id": intent_id, "slug": row.slug, "kind": row.kind}
        s.delete(row)
        s.flush()
        _renumber_intents(s, node_id)
        record_graph_event(s, "node.intent.delete", entity_type="node", entity_id=node_id,
                           detail=gone)
        s.commit()   # same reason as create_intent above
        return JSONResponse({"deleted": intent_id, "node_id": node_id})
