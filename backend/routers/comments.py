"""`comments` routes: the conversation on an epic, story or task.

The node panel's Comments tab is the board's caller; an agent with a `write` token may comment
too, and its row names the agent, not its person. The table is `models_graph.WorkitemComment`.

Who may do what:
- read: anyone who can see the item. Hidden and absent are the same 404, as everywhere.
- write: anyone who may edit the item (project role `editor`, a no-op with RBAC off).
- edit: the author only. Words put in somebody else's mouth are not a comment. The text an
  edit replaces is kept in `workitem_comment_revision`, for audit; no route returns it.
- delete: the author, or an instance owner (moderation). A delete HIDES the comment: the row,
  its text and who deleted it stay in the table for audit, and no route here returns it again.
A comment id is a small integer, so every refusal on `/api/comment/{id}` for a comment on a
hidden item is the same 404 as "no such comment", for `routers/intents.edit_intent`'s reason.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

import models_graph
import models_auth
import auth         # THE GATE (TASK-967)
import rbac         # project RBAC (ADR 0041); inert unless ATLAS_RBAC=projects
import audit_log    # the read audit (off unless ATLAS_AUDIT_READS=1)
from models_graph import Node as GraphNode
from db import session
from deps import (
    record_graph_event,
    _may_see_node,
    _guard_node_access,
    _require_project_role,
    WORKITEM_TYPES,
)

router = APIRouter()

#: The longest comment accepted. Long enough for a pasted log excerpt; a design belongs in a doc.
COMMENT_MAX = 10000


class CommentWrite(BaseModel):
    body: str
    csrf_token: str | None = None


class CommentDelete(BaseModel):
    csrf_token: str | None = None


def _iso(v) -> str | None:
    return v.isoformat() if v is not None else None


def _comment_json(c: models_graph.WorkitemComment, names: dict[str, str] | None = None) -> dict:
    return {
        "id": c.id, "node_id": c.node_id, "body": c.body,
        "created_at": _iso(c.created_at), "edited_at": _iso(c.edited_at),
        "author": {"id": c.created_by_user_id, "username": c.created_by_subject,
                   "name": (names or {}).get(c.created_by_user_id or "")},
    }


def _author_names(s: Session, rows) -> dict[str, str]:
    ids = {r.created_by_user_id for r in rows if r.created_by_user_id}
    if not ids:
        return {}
    return {u.id: u.name for u in s.scalars(
        select(models_auth.AuthUser).where(models_auth.AuthUser.id.in_(ids))).all() if u.name}


def _body_error(body: str) -> JSONResponse | None:
    """400 NAMING the field, the shape every write route here uses."""
    if not body.strip():
        return JSONResponse({"error": "invalid_comment", "field": "body",
                             "detail": "a comment must not be empty"}, status_code=400)
    if len(body) > COMMENT_MAX:
        return JSONResponse({"error": "invalid_comment", "field": "body",
                             "detail": f"a comment is at most {COMMENT_MAX} characters"},
                            status_code=400)
    return None


def _workitem(s: Session, workitem_id: str, need: str | None = None):
    """`(node, None)` for a visible workitem, else `(None, response)`."""
    n = s.get(GraphNode, workitem_id)
    if n is None:
        return None, JSONResponse({"error": f"unknown node {workitem_id!r}"}, status_code=404)
    denied = _guard_node_access(s, workitem_id)
    if denied is None and need is not None:
        denied = _require_project_role(s, [workitem_id], need)
    if denied is not None:
        return None, denied
    if n.type not in WORKITEM_TYPES:
        return None, JSONResponse(
            {"error": "not_a_workitem",
             "detail": f"node {workitem_id!r} is a {n.type!r}; comments belong to "
                       f"{list(WORKITEM_TYPES)} only"},
            status_code=409)
    return n, None


def _comment(s: Session, comment_id: int):
    """`(row, None)` for a comment on an item the caller can see, else `(None, 404)`."""
    row = s.get(models_graph.WorkitemComment, comment_id)
    if row is None or row.deleted_at is not None or not _may_see_node(s, row.node_id):
        return None, JSONResponse({"error": f"unknown comment {comment_id}"}, status_code=404)
    return row, None


@router.get("/api/workitem/{workitem_id}/comments")
def list_comments(workitem_id: str, request: Request) -> JSONResponse:
    with session() as s:
        _n, refusal = _workitem(s, workitem_id)
        if refusal is not None:
            return refusal
        rows = s.scalars(
            select(models_graph.WorkitemComment)
            .where(models_graph.WorkitemComment.node_id == workitem_id,
                   models_graph.WorkitemComment.deleted_at.is_(None))
            .order_by(models_graph.WorkitemComment.created_at, models_graph.WorkitemComment.id)
        ).all()
        names = _author_names(s, rows)
        audit_log.record_read(request, "GET /api/workitem/{workitem_id}/comments", [workitem_id])
        return JSONResponse({"comments": [_comment_json(r, names) for r in rows],
                             "count": len(rows)})


@router.post("/api/workitem/{workitem_id}/comments")
def create_comment(workitem_id: str, body: CommentWrite, request: Request) -> JSONResponse:
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    bad = _body_error(body.body)
    if bad is not None:
        return bad
    with session() as s:
        _n, refusal = _workitem(s, workitem_id, rbac.EDITOR)
        if refusal is not None:
            return refusal
        principal = auth.current_principal.get()
        row = models_graph.WorkitemComment(
            node_id=workitem_id, body=body.body, created_at=models_graph._utcnow(),
            created_by_user_id=principal.user_id if principal else None,
            created_by_subject=principal.subject if principal else None)
        s.add(row)
        s.flush()
        record_graph_event(s, "node.comment.create", entity_type="node", entity_id=workitem_id,
                           detail={"comment_id": row.id})
        payload = _comment_json(row, _author_names(s, [row]))
        # COMMIT: a comment changes no derived value, so `recompute_derived` would be a lie
        # about what changed. Same call as the intent and acceptance-proposal routes.
        s.commit()
        return JSONResponse(payload, status_code=201)


@router.patch("/api/comment/{comment_id}")
def edit_comment(comment_id: int, body: CommentWrite, request: Request) -> JSONResponse:
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    bad = _body_error(body.body)
    if bad is not None:
        return bad
    with session() as s:
        row, refusal = _comment(s, comment_id)
        if refusal is not None:
            return refusal
        principal = auth.current_principal.get()
        if principal is None or not principal.user_id or principal.user_id != row.created_by_user_id:
            return JSONResponse({"error": "not_your_comment",
                                 "detail": "only the author may edit a comment"}, status_code=403)
        if body.body != row.body:
            s.add(models_graph.WorkitemCommentRevision(
                comment_id=row.id, body=row.body, written_at=row.edited_at or row.created_at,
                replaced_at=models_graph._utcnow(), replaced_by_user_id=principal.user_id,
                replaced_by_subject=principal.subject))
            row.body = body.body
            row.edited_at = models_graph._utcnow()
            s.flush()
            record_graph_event(s, "node.comment.update", entity_type="node",
                               entity_id=row.node_id, detail={"comment_id": row.id})
        payload = _comment_json(row, _author_names(s, [row]))
        s.commit()   # same reason as create_comment
        return JSONResponse(payload)


@router.delete("/api/comment/{comment_id}")
def delete_comment(comment_id: int, body: CommentDelete, request: Request) -> JSONResponse:
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    with session() as s:
        row, refusal = _comment(s, comment_id)
        if refusal is not None:
            return refusal
        principal = auth.current_principal.get()
        mine = bool(principal and principal.user_id and principal.user_id == row.created_by_user_id)
        if not mine and not (principal and principal.is_owner):
            return JSONResponse({"error": "not_your_comment",
                                 "detail": "only the author or an instance owner may delete a "
                                           "comment"}, status_code=403)
        node_id = row.node_id
        gone = {"comment_id": comment_id, "author": row.created_by_subject}
        row.deleted_at = models_graph._utcnow()
        row.deleted_by_user_id = principal.user_id
        row.deleted_by_subject = principal.subject
        s.flush()
        record_graph_event(s, "node.comment.delete", entity_type="node", entity_id=node_id,
                           detail=gone)
        s.commit()   # same reason as create_comment
        return JSONResponse({"deleted": comment_id, "node_id": node_id})
