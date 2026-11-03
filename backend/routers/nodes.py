"""`nodes` routes, split out of `app.py` (STORY-oss-04); the code is moved, not changed.
Registered on the app by `main.py` via `include_router`.
"""
from __future__ import annotations

from itertools import chain

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

import models_graph  # Atlas v2 object-graph schema (ADR 0019); coexists with Component below
import auth         # THE GATE (TASK-967) + service tokens (TASK-968)
from models_graph import Edge as GraphEdge, Node as GraphNode
from db import session
from deps import (
    _assignee_map,
    _graph_node_json,
    _prose_tier_or_400,
    _trim_prose,
    WORKITEM_TYPES,
    _status_payload,
    record_graph_event,
    _attribute_validation_response,
    _acceptance_list,
    _existing_result_row,
    _result_heading_display,
    _visible_node_ids,
    _guard_node_access,
    _require_project_role,
    _project_root_creator,
    _bind_project_root_creator,
    _intent_json,
)
import rbac
import audit_log  # STORY-oss-16: the read audit (off unless ATLAS_AUDIT_READS=1)
import payload_fields  # STORY-oss-14: ignored fields -> `warnings`, or a 422 under ATLAS_STRICT_API

router = APIRouter()


class StatusWrite(BaseModel):
    """`status: null` clears the node's own status — there is no second source
    to revert to any more (TASK-1056 deleted the override/three-way-merge
    machinery that used to make this a "revert to the file" operation)."""
    status: str | None = None
    csrf_token: str | None = None
    note: str | None = None


@router.post("/api/node/{node_id}/status")
def set_node_status(node_id: str, body: StatusWrite, request: Request) -> JSONResponse:
    from attention import SETTABLE_STATUSES, recompute_derived, _norm

    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused

    wanted = _norm(body.status)
    if wanted is not None and wanted not in SETTABLE_STATUSES:
        return JSONResponse(
            {"error": "unknown status", "detail": f"{body.status!r} is not one of "
             f"{list(SETTABLE_STATUSES)}", "allowed": list(SETTABLE_STATUSES)},
            status_code=400,
        )

    with session() as s:
        n = s.get(GraphNode, node_id)
        if n is None:
            return JSONResponse({"error": f"unknown node {node_id!r}"}, status_code=404)
        denied = _guard_node_access(s, node_id) or _require_project_role(s, [node_id], rbac.EDITOR)
        if denied is not None:
            return denied
        if n.type not in WORKITEM_TYPES:
            # An object/bridge node has no workflow status to set — its colour
            # comes from a health hint, not a lifecycle (attention.object_health).
            return JSONResponse(
                {"error": "not a workitem",
                 "detail": f"node {node_id!r} is a {n.type!r}; status applies to "
                           f"{list(WORKITEM_TYPES)} only"},
                status_code=409,
            )

        # ONE branch (TASK-1056): write the status on the node, same as every
        # other write this API makes. See this route's own module comment for
        # why there used to be a second branch here.
        attrs = dict(n.attributes or {})
        previous = attrs.get("status")
        if wanted is None:
            attrs.pop("status", None)
        else:
            attrs["status"] = wanted
        n.attributes = attrs
        s.flush()

        # TASK-1045. Recorded even when `previous == wanted` — a re-assertion
        # is a real act by a real principal, and a log that silently drops
        # no-ops cannot answer "who touched this and when".
        record_graph_event(s, "node.status", entity_type="node", entity_id=node_id,
                           detail={"from": previous, "to": wanted})

        # Recompute in the SAME request, not on the next ingest: the caller is a
        # board waiting to repaint, and a status change moves the fill, the ring,
        # the blocking frontier and completed_at. Cheap at this graph's size.
        recompute_derived(s)
        s.refresh(n)
        return JSONResponse(_status_payload(s, n))


class NodeWrite(payload_fields.TracksUnknownFields):
    """Body for `POST /api/node`. `id` is caller-supplied (backlog-style human
    ids, e.g. `TASK-1200` / `service-new-thing`) — there is no id generator;
    the write API is a second author onto the same id space the ingesters
    use, not a separate namespace."""
    id: str
    type: str
    name: str
    summary: str | None = None
    abstract: str | None = None
    detail: str | None = None
    owner: str | None = None
    source: str | None = None
    attributes: dict = Field(default_factory=dict)
    csrf_token: str | None = None


class NodePatch(payload_fields.TracksUnknownFields):
    """Every field optional; only what's SET on the request body is applied
    (`exclude_unset`), so a PATCH that omits `attributes` never clobbers it."""
    type: str | None = None
    name: str | None = None
    summary: str | None = None
    abstract: str | None = None
    detail: str | None = None
    owner: str | None = None
    source: str | None = None
    attributes: dict | None = None
    #: STORY-oss-12: a PROJECT node's key (`ABC`), or null to clear it. Applied
    #: by `_apply_project_key`, never by the generic setattr loop.
    key: str | None = None
    csrf_token: str | None = None


def _apply_project_key(s: Session, n: GraphNode, value: str | None,
                       new_type: str | None) -> JSONResponse | None:
    """Set or clear `n.project_key` (STORY-oss-12-project-keys), or the refusal.

    In order, each a refusal NAMING THE FIELD:
      * 400 `key_not_project` — only a `project` node carries a key (judged on
        the type this same PATCH leaves it with);
      * 403 — owner/maintainer only. RBAC on (`ATLAS_RBAC=projects`): project
        role `maintainer` on this project, through the same `rbac.require`
        every other write uses (an agent's `admin` scope caps at maintainer).
        RBAC off, where there are no project roles: the instance `owner`, or a
        token carrying the `admin` scope — the same two the RBAC model maps to
        maintainer, so a key is set by the same callers either way;
      * 400 `invalid_key` — `models_graph.project_key_problem`;
      * 409 `key_immutable` — an id has already been minted with the current
        key, so the project's ids already say it (`project_key_minted`);
      * 409 `key_taken` — another project holds it (also the answer when two
        PATCHes race for one key and the unique index refuses the second).
    Setting the value it already has is a no-op and passes every check but the
    first two."""
    import sqlalchemy.exc

    if (new_type or n.type) != rbac.PROJECT_NODE_TYPE:
        return JSONResponse(
            {"error": "key_not_project", "field": "key",
             "detail": f"only a {rbac.PROJECT_NODE_TYPE!r} node carries a key; "
                       f"{n.id!r} is a {(new_type or n.type)!r}"},
            status_code=400,
        )
    if rbac.enabled():
        refused = _require_project_role(s, [n.id], rbac.MAINTAINER)
        if refused is not None:
            return refused
    else:
        p = auth.current_principal.get()
        if p is None or not (p.is_owner or p.has_scope("admin")):
            return JSONResponse(
                {"error": "forbidden", "field": "key",
                 "detail": "setting a project key takes an instance owner or an admin-scoped "
                           "token (project role maintainer when ATLAS_RBAC=projects)"},
                status_code=403,
            )
    problem = models_graph.project_key_problem(value)
    if problem is not None:
        return JSONResponse({"error": "invalid_key", "field": "key", "detail": problem},
                            status_code=400)
    if value == n.project_key:
        return None
    if n.project_key and models_graph.project_key_minted(s, n.project_key):
        return JSONResponse(
            {"error": "key_immutable", "field": "key",
             "detail": f"ids have already been minted as {n.project_key}-n; the key of "
                       f"{n.id!r} can no longer change"},
            status_code=409,
        )

    def taken() -> JSONResponse:
        return JSONResponse(
            {"error": "key_taken", "field": "key",
             "detail": f"another project already has the key {value!r}"},
            status_code=409,
        )

    if value is not None and s.execute(
        select(GraphNode.id).where(GraphNode.project_key == value, GraphNode.id != n.id)
    ).first() is not None:
        return taken()
    n.project_key = value
    try:
        s.flush()
    except sqlalchemy.exc.IntegrityError:
        s.rollback()
        return taken()
    return None


class NodeDelete(BaseModel):
    csrf_token: str | None = None


def _delete_node_dependents(s: Session, node_id: str) -> None:
    """Clean up rows that FK to a node but are not graph edges (Probe + its
    ProbeResult rows, AcceptanceCriterion — TASK-1046, WorkitemResult —
    TASK-1063, NodeDoc — ADR 0029, NodeIntent — ADR 0032, WorkitemComment and
    its WorkitemCommentRevision rows), so
    DELETE /api/node/{id} never hits an FK violation the caller cannot see
    coming.

    The old boot-time ingest reconcilers (`ingest_graph.py`, `ingest_world.py`)
    used to note this exact gap in their own node-removal paths and leave it
    for a bulk, non-interactive reconcile where a rare IntegrityError was
    tolerable; both are deleted (TASK-1058) and this route — a single
    human/agent request answered directly — is the only node-deletion path
    left, so it closes the gap here instead of surfacing a raw 500.

    `acceptance_criterion` (TASK-1046), `workitem_result` (TASK-1063) and
    `node_doc` (ADR 0029) all join this list rather than getting
    `ondelete="CASCADE"` on their FK — see those tables' docstrings in
    models_graph.py for why this schema does multi-table deletes IN CODE, at
    every place a node actually gets deleted, instead of in DDL.

    `graph_event` (TASK-1045) is DELIBERATELY ABSENT from this list, and it is
    the one table here that must stay absent. Its rows are the record that this
    node existed and was deleted; cleaning them up with the node would delete
    exactly the evidence the table is for. It carries no FK to `node` precisely
    so that it can outlive one — same call, same reason, as
    `models_auth.NodeVisibility.node_id`.
    """
    probes = s.scalars(select(models_graph.Probe).where(models_graph.Probe.node_id == node_id)).all()
    for p in probes:
        s.execute(delete(models_graph.ProbeResult).where(models_graph.ProbeResult.probe_id == p.id))
        s.delete(p)
    s.execute(delete(models_graph.AcceptanceCriterion).where(
        models_graph.AcceptanceCriterion.node_id == node_id))
    s.execute(delete(models_graph.WorkitemResult).where(
        models_graph.WorkitemResult.node_id == node_id))
    s.execute(delete(models_graph.NodeDoc).where(
        models_graph.NodeDoc.node_id == node_id))
    s.execute(delete(models_graph.NodeIntent).where(
        models_graph.NodeIntent.node_id == node_id))
    s.execute(delete(models_graph.WorkitemCommentRevision).where(
        models_graph.WorkitemCommentRevision.comment_id.in_(
            select(models_graph.WorkitemComment.id).where(
                models_graph.WorkitemComment.node_id == node_id))))
    s.execute(delete(models_graph.WorkitemComment).where(
        models_graph.WorkitemComment.node_id == node_id))


#: The status POST /api/node gives a work-family node created without one
#: (TASK-1472). See the note in `create_node`.
DEFAULT_WORK_STATUS = "pending"


@router.post("/api/node")
def create_node(body: NodeWrite, request: Request) -> JSONResponse:
    from attention import recompute_derived

    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    # STORY-oss-14: a key NodeWrite does not declare (`priority`, `status`, a
    # typo) is named — a warning on the 201 by default, a 422 under strict.
    ignored = body.unknown_field_problems()
    refused = payload_fields.strict_refusal(ignored)
    if refused is not None:
        return refused

    with session() as s:
        if s.get(GraphNode, body.id) is not None:
            return JSONResponse(
                {"error": "node_exists", "detail": f"node {body.id!r} already exists"},
                status_code=409,
            )
        # ADR 0051 (RBAC on only): this route creates no edge, so the new node is
        # ALWAYS a project root, its own project until a `contains` edge gives it
        # a parent. Its creator's person is bound `owner` below; an unlinked
        # agent, with no person to bind, is refused here, before the row exists.
        # RBAC off: (None, None), nothing read.
        root_owner, refused = _project_root_creator(body.id)
        if refused is not None:
            return refused
        attrs = dict(body.attributes or {})
        # A WORK-FAMILY NODE IS GIVEN THE DEFAULT STATUS (TASK-1472) when the
        # caller sent none. Without one the row had no status at all: the API
        # reports null, which no board column names, and every reader has to
        # guess (board v2 guesses `pending`, TASK-1368). Defaulted rather than
        # refused: this
        # route is how atlas_node_write creates any node, and a dozen verify
        # sections file tasks through it, so a refusal would break callers
        # that work today. POST /api/workitem stays the route that also files
        # the parent and the binding. `pending` is what atlas_file_work
        # defaults to and what the board draws a status-less item as
        # (frontend/v2/core/graph-model.js `DEFAULT_WORK_STATUS`). A status the
        # caller DID send is left exactly as sent, invalid ones included, so
        # the attributes validator below still names them.
        nt = s.get(models_graph.NodeType, body.type)
        if (nt is not None and nt.family == "workitem"
                and attrs.get("status") is None):
            attrs["status"] = DEFAULT_WORK_STATUS
        n = GraphNode(
            id=body.id, type=body.type, name=body.name,
            summary=body.summary, abstract=body.abstract, detail=body.detail,
            owner=body.owner, source=body.source, attributes=attrs,
            origin="atlas",  # provenance: authored through the API, not a file
        )
        s.add(n)
        try:
            s.flush()
        except ValueError as exc:
            s.rollback()
            return _attribute_validation_response(exc)

        # TASK-1045 — AFTER the flush that can still raise, so a rejected
        # attributes payload never leaves an event describing a node that does
        # not exist.
        record_graph_event(s, "node.create", entity_type="node", entity_id=n.id,
                           detail={"type": n.type, "name": n.name})

        # A status arrives here inside `attributes`, not as a field of its own —
        # this route has no `status` parameter, it has a whole attributes dict.
        # Without this the audit log's most-asked question ("who set this to
        # done") had a hole straight through the generic route: a node could be
        # created already `done` and `?action=node.status` would never show it.
        # Read off `n.attributes` rather than `body.attributes` so it records
        # what was actually STORED after validation.
        created_status = (n.attributes or {}).get("status")
        if created_status is not None:
            record_graph_event(s, "node.status", entity_type="node", entity_id=n.id,
                               detail={"from": None, "to": created_status})

        # Before `recompute_derived`, whose commit this rides on. A no-op with
        # RBAC off (`root_owner` is None).
        _bind_project_root_creator(s, n.id, root_owner, request)

        recompute_derived(s)
        s.refresh(n)
        return JSONResponse(payload_fields.add_warnings(_status_payload(s, n), ignored),
                            status_code=201)


@router.patch("/api/node/{node_id}")
def edit_node(node_id: str, body: NodePatch, request: Request) -> JSONResponse:
    from attention import recompute_derived

    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    ignored = body.unknown_field_problems()  # STORY-oss-14, as in create_node
    refused = payload_fields.strict_refusal(ignored)
    if refused is not None:
        return refused

    with session() as s:
        n = s.get(GraphNode, node_id)
        if n is None:
            return JSONResponse({"error": f"unknown node {node_id!r}"}, status_code=404)
        denied = _guard_node_access(s, node_id) or _require_project_role(s, [node_id], rbac.EDITOR)
        if denied is not None:
            return denied

        # Was this row reconciled from a file BEFORE this edit? Captured before
        # the flip below overwrites `origin`, so the response can say so — see
        # the note on the flip itself for why this must be visible, not silent.
        was_git = models_graph.origin_is_git_value(n.origin)

        # TASK-1045 — READ BEFORE the setattr loop below replaces `attributes`
        # wholesale. `NodePatch.attributes` accepts `{"status": "done"}`, so
        # this route can move a workitem's whole lifecycle without ever
        # touching POST /api/node/{id}/status; before this was captured, such a
        # change left only a `node.update` saying `fields: ["attributes"]` and
        # `?action=node.status` could not see it.
        status_before = (n.attributes or {}).get("status")

        changed_fields: list[str] = []
        fields = body.model_dump(exclude_unset=True, exclude={"csrf_token"})
        # STORY-oss-12. `key` is not a column name and has its own rules, so it
        # leaves `fields` before the generic loop. A keyed project cannot be
        # retyped out from under its key in the same breath.
        if "key" in fields:
            refused = _apply_project_key(s, n, fields.pop("key"), fields.get("type"))
            if refused is not None:
                return refused
            changed_fields.append("key")
        elif (n.project_key and fields.get("type") is not None
              and fields["type"] != rbac.PROJECT_NODE_TYPE):
            return JSONResponse(
                {"error": "key_not_project", "field": "type",
                 "detail": f"{node_id!r} carries the project key {n.project_key!r}; "
                           "clear the key before changing its type"},
                status_code=409,
            )
        for field, value in fields.items():
            if field == "attributes" and value is None:
                continue  # attributes is NOT NULL; omit rather than corrupt the row
            setattr(n, field, value)
            changed_fields.append(field)
        # An edit made through the write API is atlas-authored FROM NOW ON.
        #
        # WHY THIS COMMENT WAS REWRITTEN (2026-08-12). It used to justify the
        # flip by what the reconcilers did with it: TASK-1016's hole (a
        # git-origin row that stayed git-origin would be DELETED, not just
        # reverted, once its file vanished) and TASK-1043's fix (
        # `ingest_graph._reconcile_nodes` protecting an atlas-origin row whose
        # `source` matched its file). BOTH RECONCILERS ARE DELETED (TASK-1058).
        # Nothing deletes a row when its file disappears any more, and nothing
        # reverts one either — so neither the hazard nor the protection is a
        # live reason for anything.
        #
        # What the flip still does, and the only thing it does: it makes
        # `detached_from_git` below true and honest. `origin` is provenance
        # now — "this row arrived via the old file ingest, and your edit is
        # what stopped it tracking that file". See `models_graph.Node.origin`'s
        # docstring, which is the authority on this column: "Nothing else
        # branches on `origin` any more."
        #
        # Read that before deciding the flip is dead weight. It is cheap and
        # it is honest, but it is no longer load-bearing, and the argument for
        # removing it (branch `feat/task-1017-graph-write-api`, eea64d6) is no
        # longer wrong for the reason it was rejected — it is simply moot.
        n.origin = "atlas"

        try:
            s.flush()
        except ValueError as exc:
            s.rollback()
            return _attribute_validation_response(exc)

        # TASK-1045. FIELD NAMES, NOT VALUES. `body` and `detail` on a workitem
        # run to thousands of characters, and copying every version of them into
        # a table that only grows is a different feature (prose history) that
        # was considered and deferred with the audit log itself — see
        # `Node.body`'s own docstring. Names answer "who touched the spec, and
        # when", which is the question this table was filed to answer.
        record_graph_event(s, "node.update", entity_type="node", entity_id=node_id,
                           detail={"fields": sorted(changed_fields)})

        # ...and the status the `attributes` payload may have carried with it,
        # as its OWN event, so one filter answers "who set this to done"
        # whichever route did it.
        #
        # ONLY WHEN IT ACTUALLY MOVED. This is the one place the two status
        # paths deliberately differ: `POST /api/node/{id}/status` records even a
        # re-assertion, because a caller naming the status IS asserting it. Here
        # the caller named `attributes`, and a PATCH that rewrites some other
        # key while carrying the unchanged status along is not a status act —
        # firing on it would fill the log with events that say nothing changed
        # and make the ones that do harder to find. Compared on the STORED value
        # (not `attention._norm`'s reading of it), so the event fires exactly
        # when the bytes on the row moved.
        status_after = (n.attributes or {}).get("status")
        if status_after != status_before:
            record_graph_event(s, "node.status", entity_type="node", entity_id=node_id,
                               detail={"from": status_before, "to": status_after})

        recompute_derived(s)
        s.refresh(n)
        payload = _status_payload(s, n)
        if was_git:
            payload["detached_from_git"] = True
            payload["note"] = (
                "this node was reconciled from a backlog file or the world manifest before "
                "this edit; editing it through the API detaches it from that file — future "
                "changes to the file will no longer apply to this node (TASK-1043)."
            )
        return JSONResponse(payload_fields.add_warnings(payload, ignored))


@router.delete("/api/node/{node_id}")
def delete_node(node_id: str, request: Request, cascade: int = 0,
                body: NodeDelete = NodeDelete()) -> JSONResponse:
    from attention import recompute_derived

    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused

    with session() as s:
        n = s.get(GraphNode, node_id)
        if n is None:
            return JSONResponse({"error": f"unknown node {node_id!r}"}, status_code=404)
        denied = _guard_node_access(s, node_id)
        if denied is not None:
            return denied

        incident = s.scalars(
            select(GraphEdge).where(or_(GraphEdge.src == node_id, GraphEdge.dst == node_id))
        ).all()
        # ADR 0041 §2 (RBAC on only): `maintainer` on the project of every row
        # removed — the node, and both ends of each edge a cascade takes with it —
        # and `owner` when the node is itself a project root.
        refused = _require_project_role(s, [node_id], rbac.MAINTAINER, root_needs=rbac.OWNER) or (
            _require_project_role(s, [x for e in incident for x in (e.src, e.dst)], rbac.MAINTAINER)
            if cascade else None)
        if refused is not None:
            return refused
        if incident and not cascade:
            return JSONResponse(
                {"error": "node_has_edges",
                 "detail": f"node {node_id!r} has {len(incident)} edge(s); pass ?cascade=1 "
                           f"to delete them with it",
                 "edge_count": len(incident)},
                status_code=409,
            )
        for e in incident:
            # TASK-1045. A cascade is still an edge delete, and an edge that
            # vanishes with no event is exactly the case an audit log is for —
            # so `?cascade=1` produces one `edge.delete` per edge PLUS the
            # `node.delete` below, not a single line claiming a count.
            record_graph_event(s, "edge.delete", entity_type="edge", entity_id=e.id,
                               node_id=e.src, related_node_id=e.dst,
                               detail={"src": e.src, "dst": e.dst, "edge_type": e.edge_type,
                                       "cascade_of": node_id})
            s.delete(e)

        # TASK-1045 — captured BEFORE the delete, because after it there is
        # nothing left to read them off.
        gone = {"type": n.type, "name": n.name, "edges_deleted": len(incident)}

        _delete_node_dependents(s, node_id)
        s.delete(n)
        s.flush()

        # The delete event survives its subject deliberately —
        # `_delete_node_dependents` does NOT clear `graph_event`, and must not.
        #
        # KNOWN CONSEQUENCE, not a defect to rediscover: `GET /api/events`
        # resolves visibility through `_visible_node_ids`, which is computed
        # over the nodes that EXIST. A deleted node is in no caller's visible
        # set, so this event is readable by an owner (unrestricted) and by
        # nobody else. That is the fail-closed direction and it is the right
        # one for a first cut; making a delete legible to the members who could
        # see the node while it lived means snapshotting visibility at write
        # time, which is a decision, not an oversight.
        record_graph_event(s, "node.delete", entity_type="node", entity_id=node_id,
                           detail=gone)

        recompute_derived(s)
        return JSONResponse({"deleted": node_id, "edges_deleted": len(incident)})


# NOTE (integration of TASK-1048 + TASK-1050): a SECOND `_acceptance_list`
# returning `[{"position": …, "text": …}]` used to live here. Both stories
# defined a helper of that name independently, the merge kept both, and Python
# silently bound the later one — so the WRITE API's response shape changed with
# nothing raising and neither story's own suite failing. Only running the two
# suites together caught it.
#
# One definition survives, above: it returns `list[str]`, the same shape
# `WorkitemWrite.acceptance` ACCEPTS, so an agent can round-trip a workitem
# without reshaping it. `position` is not exposed because it carries no
# information a list index does not — `_replace_acceptance` is delete-and-
# reinsert, so positions are always dense 0..n-1 in returned order.


def _edge_targets(s: Session, node_id: str, edge_types: tuple[str, ...], direction: str) -> list[dict]:
    """The OTHER end of every `node_id` edge of the given types, one direction
    at a time. `direction="out"` reads edges where `node_id` is `src` (e.g.
    this workitem's outbound `works_on`/`serves` binding, or what it
    `depends_on`); `direction="in"` reads edges where `node_id` is `dst`
    (e.g. its `contains` parent(s), or what `depends_on` IT).

    THE EDGE-DIRECTION CONTRACT, stated here rather than pointed at. It used
    to live in `ingest_graph.py`'s docstring; that file is deleted (TASK-1058)
    and the contract is not, so it is written out in full:
      * `contains`          src = parent,         dst = child
      * `depends_on`        src = the dependent,  dst = the dependency
      * `works_on`/`serves` src = the workitem,   dst = the object"""
    col_self, col_other = (GraphEdge.src, GraphEdge.dst) if direction == "out" else (GraphEdge.dst, GraphEdge.src)
    rows = s.execute(
        select(GraphEdge.edge_type, col_other).where(col_self == node_id, GraphEdge.edge_type.in_(edge_types))
    ).all()
    other_ids = [r[1] for r in rows]
    others = ({n.id: n for n in s.scalars(select(GraphNode).where(GraphNode.id.in_(other_ids))).all()}
              if other_ids else {})
    out = []
    for edge_type, other_id in rows:
        n = others.get(other_id)
        out.append({"id": other_id, "edge_type": edge_type,
                     "name": n.name if n else None, "type": n.type if n else None})
    return sorted(out, key=lambda r: (r["edge_type"], r["id"]))


#: `intents=` on the single-node read, the same subtractive ladder as `prose`
#: (TASK-1157). Measured on live 2026-09-17: `GET /api/node/sphere-devices` was
#: 23,391 bytes, of which 7,203 were intents — and 7,177 of those were INHERITED
#: text this node did not author. Intent inherits downward (ADR 0032), so the
#: deeper you read, the more times you pay for the identical statements: the
#: eight on `sphere-device-heating` arrive again on each of its children.
#:
#: OMITTING IT IS BYTE-IDENTICAL to before, for the same reason the `prose`
#: tiers are: `panel.js` renders `intents.own` and `intents.inherited` off this
#: payload, and a default that trimmed them would empty the panel's Intent
#: section rather than save anybody anything.
_INTENT_TIERS = ("all", "own", "none")


@router.get("/api/node/{node_id}")
def get_node(node_id: str, request: Request, prose: str | None = None,
             intents: str | None = None) -> JSONResponse:
    """One workitem (or any node), COMPLETE, in a single call — the answer to
    `grep -r backlog/ | grep TASK-1234` + opening the file by hand.

    Includes everything `_graph_node_json` projects, PLUS the verbatim
    `body`, `result`/`result_heading` (TASK-1064 — the recorded outcome, out
    of its own `workitem_result` row, never mixed back into `body`), the
    ordered `acceptance` list, `parents` (this node's `contains`
    container(s)), `bindings` (its outbound `works_on`/`serves` world link),
    `dependencies` (what it `depends_on`) and `dependants` (what
    `depends_on` it).

    Prose is UNCONDITIONAL here (`include_prose=True`) — a single-item fetch
    IS the opt-in a listing's `?include_prose=1` stands in for; nobody calls
    this route for a node they don't want the details of. `?prose=` and
    `?intents=` (TASK-1157) let a caller that wants LESS say so, using the same
    tiers as `/api/graph`: `prose=none|summary|abstract|full`, and
    `intents=all|own|none`, where `own` drops the inherited half and `none`
    drops the key. Both omitted is byte-identical to before. `result`/
    `result_heading` follow the SAME rule as `acceptance` (below): both are
    per-item detail projected only here and in the create/PATCH response
    (`_workitem_payload`), never by `_graph_node_json` itself — so
    `GET /api/workitems` (a listing) does not carry either, same as it never
    carried `acceptance`.

    NodeVisibility is applied INSIDE the query (`_visible_node_ids`), not as
    a post-filter: a hidden node's row is excluded by the WHERE clause before
    it is ever loaded, so "hidden" and "does not exist" return the exact same
    404 body — the same distinction-erasing shape TASK-974's task file
    requires for `/api/graph` and the doc route.
    """
    bad = _prose_tier_or_400(prose)
    if bad is not None:
        return bad
    if intents is not None and intents not in _INTENT_TIERS:
        # Same discipline as `_prose_tier_or_400`: name the legal values rather
        # than falling back, so a typo cannot read as "trimming does not work".
        return JSONResponse(
            {"error": "invalid_intent_tier",
             "detail": f"`intents` must be one of {', '.join(_INTENT_TIERS)} — got {intents!r}. "
                       "Omit it for own + inherited."},
            status_code=400,
        )

    with session() as s:
        visible_ids = _visible_node_ids(s)
        stmt = select(GraphNode).where(GraphNode.id == node_id)
        if visible_ids is not None:
            stmt = stmt.where(GraphNode.id.in_(visible_ids))
        n = s.scalars(stmt).first()
        if n is None:
            return JSONResponse({"error": f"unknown node {node_id!r}"}, status_code=404)

        d = _graph_node_json(n, None, None, include_prose=True,
                             assignees=_assignee_map(s))
        result_row = _existing_result_row(s, node_id)
        d["result"] = result_row.text if result_row is not None else None
        d["result_heading"] = (
            _result_heading_display(result_row.heading) if result_row is not None else None
        )
        d["acceptance"] = _acceptance_list(s, node_id)
        d["parents"] = _edge_targets(s, node_id, ("contains",), "in")
        d["bindings"] = _edge_targets(s, node_id, ("works_on", "serves"), "out")
        d["dependencies"] = _edge_targets(s, node_id, ("depends_on",), "out")
        d["dependants"] = _edge_targets(s, node_id, ("depends_on",), "in")
        if intents != "none":
            got = _intents_for(s, node_id, visible_ids)
            d["intents"] = {"own": got["own"]} if intents == "own" else got
        # STORY-oss-16: one boolean when ATLAS_AUDIT_READS is off (the default).
        audit_log.record_read(request, "GET /api/node/{node_id}", [n.id])
        return JSONResponse(_trim_prose(d, prose))


# --------------------------------------------------------------------------- #
# A node's STRUCTURE: where it sits and what it links to, with no prose.
#
# TASK-1210 (owner decision, 2026-10-02): an agent should see the map around a piece of
# work before it reads any of it. `GET /api/node/{id}` is the opposite tier --
# one item, complete, prose and intents included -- and `/api/graph/outline`
# walks a subtree but carries none of the lateral links. This is the middle:
# one call, names and states only.
#
# What "only" means is pinned by `verify_node_structure.py`: every node in the
# answer is `{id, type, name, status}` and nothing else (plus the one fact each
# list needs: `depth`, `children`, `edge_type`). It never selects a prose column,
# so it is not a second copy of TASK-1157's payload tiers on `/api/node/{id}`,
# which strip prose from a full read; this read never had any.
#
# VISIBILITY, AND WHY EVERY LIST IS FILTERED. The focus is resolved through
# `_visible_node_ids` inside the query, so a hidden id 404s exactly like a
# nonexistent one. Every OTHER id in the answer is filtered the same way before
# its row is loaded: a visible task may depend on, or bind to, a node the
# caller may not see, and naming that node here would disclose it. Child
# COUNTS count visible grandchildren only, for the reason `_visible_node_ids`
# gives for filtering inside queries -- a count that includes hidden rows
# leaks that they exist.
# --------------------------------------------------------------------------- #
STRUCTURE_CHILDREN_DEFAULT = 50
STRUCTURE_CHILDREN_MAX = 200


@router.get("/api/node/{node_id}/structure")
def node_structure(node_id: str, request: Request,
                   children_limit: int = STRUCTURE_CHILDREN_DEFAULT) -> JSONResponse:
    """Where a node sits and what it links to: names and states, never prose.

    Returns `node`, `ancestors` (every `contains` ancestor, nearest first, each
    with its `depth`; several parents are normal), `children` (one level down,
    each with `children`, its own count of visible children, so the caller
    knows where it can go deeper), `children_total` and `children_truncated`
    (the list is capped at `children_limit`, default 50, max 200, rather than
    cut silently), `dependencies` (what it `depends_on`), `dependants` (what
    `depends_on` it) and `bindings` (its outbound `works_on`/`serves`, with
    `edge_type`). Every node is `{id, type, name, status}`.
    """
    from attention import authored_status

    limit = max(1, min(children_limit, STRUCTURE_CHILDREN_MAX))
    with session() as s:
        visible_ids = _visible_node_ids(s)

        def seen(ids):
            return [i for i in ids if visible_ids is None or i in visible_ids]

        stmt = select(GraphNode).where(GraphNode.id == node_id)
        if visible_ids is not None:
            stmt = stmt.where(GraphNode.id.in_(visible_ids))
        focus = s.scalars(stmt).first()
        if focus is None:
            return JSONResponse({"error": f"unknown node {node_id!r}"}, status_code=404)

        ancestors = [(d, a) for d, a in _contains_ancestors(s, node_id)
                     if visible_ids is None or a in visible_ids]
        child_ids = sorted(seen(s.scalars(select(GraphEdge.dst).where(
            GraphEdge.src == node_id, GraphEdge.edge_type == "contains")).all()))
        dep_ids = sorted(seen(s.scalars(select(GraphEdge.dst).where(
            GraphEdge.src == node_id, GraphEdge.edge_type == "depends_on")).all()))
        dependant_ids = sorted(seen(s.scalars(select(GraphEdge.src).where(
            GraphEdge.dst == node_id, GraphEdge.edge_type == "depends_on")).all()))
        binding_rows = [(t, d) for t, d in s.execute(select(GraphEdge.edge_type, GraphEdge.dst).where(
            GraphEdge.src == node_id, GraphEdge.edge_type.in_(("works_on", "serves")))).all()
            if visible_ids is None or d in visible_ids]

        rows = {n.id: n for n in s.scalars(select(GraphNode).where(GraphNode.id.in_(
            {a for _, a in ancestors} | set(child_ids) | set(dep_ids) | set(dependant_ids)
            | {d for _, d in binding_rows}))).all()}
        rows[focus.id] = focus

        listed_children = [c for c in child_ids if c in rows]
        children_total = len(listed_children)
        listed_children = listed_children[:limit]
        counts: dict[str, int] = {}
        if listed_children:
            q = select(GraphEdge.src, func.count()).where(
                GraphEdge.src.in_(listed_children), GraphEdge.edge_type == "contains")
            if visible_ids is not None:
                q = q.where(GraphEdge.dst.in_(visible_ids))
            counts = dict(s.execute(q.group_by(GraphEdge.src)).all())

        def brief(nid: str) -> dict:
            n = rows[nid]
            return {"id": n.id, "type": n.type, "name": n.name, "status": authored_status(n)}

        # TASK-1382: every id the body names, in the order it names them. Lazy, so with
        # ATLAS_AUDIT_READS off nothing is built.
        audit_log.record_read(request, "GET /api/node/{node_id}/structure", chain(
            (focus.id,), (a for _, a in ancestors if a in rows), listed_children,
            (i for i in dep_ids if i in rows), (i for i in dependant_ids if i in rows),
            (d for _, d in binding_rows if d in rows)))
        return JSONResponse({
            "node": brief(focus.id),
            "ancestors": [{**brief(a), "depth": d} for d, a in ancestors if a in rows],
            "children": [{**brief(c), "children": counts.get(c, 0)} for c in listed_children],
            "children_total": children_total,
            "children_truncated": children_total > len(listed_children),
            "dependencies": [brief(i) for i in dep_ids if i in rows],
            "dependants": [brief(i) for i in dependant_ids if i in rows],
            "bindings": sorted(({**brief(d), "edge_type": t} for t, d in binding_rows if d in rows),
                               key=lambda b: (b["edge_type"], b["id"])),
        })


def _contains_ancestors(s: Session, node_id: str) -> list[tuple[int, str]]:
    """Every node that reaches `node_id` through `contains`, as
    `(depth, ancestor_id)` sorted NEAREST FIRST.

    Depth is what makes shadowing well-defined. If `atlas` and
    `atlas-frontend` both carry the slug `dual-audience`, a node under
    `atlas-frontend` must get the nearer one — otherwise an override written
    at a layer is silently outranked by the root it was written to refine.
    Ties at equal depth (two parents, same slug) resolve by ancestor id:
    arbitrary, but STABLE, so the panel does not reorder between reads. That
    collision is visible in `GET /api/intents`, which is where it should be
    noticed rather than silently arbitrated here.

    Cycle-safe via `seen`. `contains` is not supposed to have cycles and
    nothing enforces it — a BFS that trusted the data would hang the request
    thread rather than return a wrong answer, which is the worse failure.

    Built from a RAW edge scan, deliberately, for the same reason
    `_visible_node_ids` is: `attention.derive()`'s adjacency skips
    bridge-family destinations, so a walk built on it would silently lose
    ancestors for exactly the node types this feature is meant to replace.
    """
    from collections import defaultdict

    rows = s.execute(
        select(GraphEdge.src, GraphEdge.dst).where(GraphEdge.edge_type == "contains")
    ).all()
    parents_of: dict[str, list[str]] = defaultdict(list)
    for src, dst in rows:
        parents_of[dst].append(src)

    out: list[tuple[int, str]] = []
    seen = {node_id}
    frontier = [node_id]
    depth = 0
    while frontier:
        depth += 1
        nxt: list[str] = []
        for nid in frontier:
            for parent in sorted(parents_of.get(nid, ())):
                if parent in seen:
                    continue
                seen.add(parent)
                out.append((depth, parent))
                nxt.append(parent)
        frontier = nxt
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def _intents_for(s: Session, node_id: str, visible_ids: set[str] | None = None) -> dict:
    """This node's own intents plus everything it inherits.

    Returns `{"own": [...], "inherited": [...]}`. Shadowing is applied last
    and in one direction only: a slug present in `own` removes the inherited
    copy, and among inherited rows the NEAREST ancestor wins. That is the only
    override mechanism ADR 0032 admits — contradiction between two prose
    statements cannot be detected, and this does not pretend to.

    `visible_ids` (the caller's already-computed `_visible_node_ids`) is
    applied to the ANCESTOR set, not just to the focus node. An intent owned
    by a node the caller may not see must not leak its text through a
    descendant they may — the ADR 0026 rule is about the node, and an
    inherited intent carries its owner's id and name in the payload.
    """
    own_rows = s.scalars(
        select(models_graph.NodeIntent)
        .where(models_graph.NodeIntent.node_id == node_id)
        .order_by(models_graph.NodeIntent.position)
    ).all()
    own = [_intent_json(r) for r in own_rows]
    own_slugs = {r.slug for r in own_rows}

    ancestors = _contains_ancestors(s, node_id)
    if visible_ids is not None:
        ancestors = [(d, a) for d, a in ancestors if a in visible_ids]
    if not ancestors:
        return {"own": own, "inherited": []}

    anc_ids = [a for _, a in ancestors]
    rank = {a: i for i, (_, a) in enumerate(ancestors)}  # nearest-first ordering
    rows = s.scalars(
        select(models_graph.NodeIntent)
        .where(models_graph.NodeIntent.node_id.in_(anc_ids))
    ).all()
    names = {
        n.id: n.name
        for n in s.scalars(select(GraphNode).where(GraphNode.id.in_(anc_ids))).all()
    }

    rows.sort(key=lambda r: (rank[r.node_id], r.position))
    inherited: list[dict] = []
    taken = set(own_slugs)
    for r in rows:
        if r.slug in taken:
            continue  # shadowed by this node's own, or by a nearer ancestor
        taken.add(r.slug)
        inherited.append(_intent_json(r, owner_name=names.get(r.node_id)))
    return {"own": own, "inherited": inherited}
