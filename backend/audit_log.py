"""Read audit, audit export and read-audit retention (STORY-oss-16-audit-export).

Three jobs, one module, all opt-in:

  1. `record_read`   the ONE writer of `audit_read` (models_audit.py). Called by every
                     read route atlas-mcp uses to serve nodes (`AUDITED_READ_ROUTES`).
  2. `export_lines`  the generator behind `GET /api/audit/export`: `graph_event`,
                     `auth_event` and `audit_read` as JSON Lines, oldest first.
  3. `purge_reads`   deletes `audit_read` rows older than ATLAS_AUDIT_RETENTION_DAYS,
                     run by `atlas-admin audit purge`. Nothing else is ever purged.

WHERE A READ IS RECORDED, AND WHY THE API. atlas-mcp holds no logic and no copy of the
graph (backend/CLAUDE.md), and anything it wrote would be the facade's own claim about
what the API served. The API is where the facts are established: the gate resolved the
token to an agent account, a credential id and the person behind it, and the route has
just decided which node ids the caller may see. Recording there also covers a token
caller that bypasses the MCP and calls the API directly, which an MCP-side log would
miss. The MCP contributes only two LABELS, sent as headers: the tool name
(`X-Atlas-Tool`) and a per-tool-call request id (`X-Request-ID`). Both are validated,
stored as given, and documented as caller-asserted.

WHO IS RECORDED: a TOKEN principal only ("an agent read"). A browser session is a
person reading the board, which this story does not log, and the grace principal has
no account to name.

DEFAULT OFF, ONE BOOLEAN. `READS_ENABLED` is read once, at import (main.py imports this
module, so a bad value stops the boot). With it False, `record_read` returns on its
first line and no read path does anything else. The routes pass the ids they served
as a GENERATOR where building the list would cost anything, so with auditing off the
list is never built.

ONE CALL, ONE ROW (TASK-1382). A route that answers by calling another audited route
in process (`/api/context` takes its seeds from `/api/search`) wraps that inner call
in `nested()`, so the outer route's row is the only one: an auditor counting reads
must not see a search the agent never asked for. A direct call of the inner route is
recorded exactly as before. The suppression is a ContextVar, so it is scoped to the
request that set it and can never leak into another one.

WHAT IS NOT RECORDED, ON PURPOSE (unchanged by TASK-1382, a decision for the owner):
a refused read (400, 404, 413) serves no node and writes no row, and a search string
is never stored, only the ids it returned.

FAIL CLOSED WHEN ON. The row is written in its own session and committed BEFORE the
route returns its response. If the write fails, the exception propagates and the
caller gets a 500 instead of the content: with auditing switched on, an unaudited read
is the outcome an operator asked not to have.
"""
from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Iterable, Iterator, Optional

from sqlalchemy import delete, select, tuple_

import auth
import models_auth
import models_graph
import settings
from models_audit import AuditRead

logger = logging.getLogger("atlas.audit")

_AUDIT = settings.load_audit()
#: THE switch. Read once; verify scripts flip it directly.
READS_ENABLED: bool = _AUDIT.reads

#: The routes that call `record_read`, by template. `verify_audit_export.py` checks
#: that each of these really records (§D, §G), and that no `record_read` call names a
#: route missing from this list (§G's source scan).
#:
#: Left out on purpose: `/api/retrieval/*` (owner-only counts and a reindex, no node
#: served), `/api/graph/tree` (the board's whole-store read; no MCP tool calls it),
#: `/api/audit/export` (an export, not a node read) and every write.
AUDITED_READ_ROUTES = (
    "GET /api/node/{node_id}",
    "GET /api/node/{node_id}/doc",
    "GET /api/search",
    "GET /api/context",
    "GET /api/graph",
    "GET /api/graph/outline",
    "GET /api/graph/path",
    "GET /api/node/{node_id}/structure",
    "GET /api/workitems",
    "GET /api/adrs",
    "GET /api/adrs/{ref}",
    "GET /api/intents",
    "GET /api/workitem/{workitem_id}/comments",
)

#: True inside `nested()`: an in-process call of one audited route by another.
_NESTED: ContextVar[bool] = ContextVar("atlas_audit_nested", default=False)

TOOL_HEADER = "x-atlas-tool"
REQUEST_ID_HEADER = "x-request-id"
_TOOL_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _label(value: Optional[str], pattern: re.Pattern) -> Optional[str]:
    """A caller-supplied label, or None when absent or not the expected shape.

    A malformed label is dropped rather than refused: the read is still audited
    (who, which credential, which nodes), only the caller's own annotation is lost."""
    if not value:
        return None
    value = value.strip()
    return value if pattern.match(value) else None


@contextmanager
def nested():
    """Run an audited route's handler from inside another audited route without it
    writing its own row: the outer route records the whole call (TASK-1382). With
    auditing off this is the one boolean check and nothing else."""
    if not READS_ENABLED:
        yield
        return
    token = _NESTED.set(True)
    try:
        yield
    finally:
        _NESTED.reset(token)


def record_read(request, route: str, node_ids: Iterable[str]) -> None:
    """Write one `audit_read` row for this request, when auditing is on and the caller
    is an agent. `node_ids` may be a generator; it is consumed only when a row is
    written, de-duplicated in the order served. Raises if the row cannot be written
    (see the module docstring)."""
    if not READS_ENABLED:
        return
    if _NESTED.get():
        return
    principal = auth.current_principal.get()
    if principal is None or principal.kind != "token":
        return
    headers = request.headers if request is not None else {}
    row = AuditRead(
        at=datetime.now(timezone.utc),
        user_id=principal.person_user_id,
        actor_user_id=principal.user_id,
        actor_subject=principal.subject,
        token_id=principal.token_id,
        tool=_label(headers.get(TOOL_HEADER), _TOOL_RE),
        route=route,
        node_ids=list(dict.fromkeys(node_ids)),
        request_id=_label(headers.get(REQUEST_ID_HEADER), _REQUEST_ID_RE),
    )
    from db import session  # late: db reads ATLAS_DB_URL, which admin.py does not need
    with session() as s:
        s.add(row)
        s.commit()


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
EXPORT_KINDS = ("graph_event", "auth_event", "audit_read")
#: Rows fetched per query. The export holds at most one batch per kind in memory.
EXPORT_BATCH = 500


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


# One function per kind: the FULL list of keys each line carries, in a fixed order.
# A column added to a table later does not reach the export until it is named here,
# which is the point: a new column is reviewed for secrets before it is exported.
def _graph_event_line(r: models_graph.GraphEvent) -> dict:
    return {"kind": "graph_event", "id": r.id, "at": _iso(r.at), "action": r.action,
            "entity_type": r.entity_type, "entity_id": r.entity_id,
            "node_id": r.node_id, "related_node_id": r.related_node_id,
            "actor_kind": r.actor_kind, "actor_user_id": r.actor_user_id,
            "actor_subject": r.actor_subject, "actor_token_id": r.actor_token_id,
            "responsible_user_id": r.responsible_user_id, "detail": r.detail}


def _auth_event_line(r: models_auth.AuthEvent) -> dict:
    return {"kind": "auth_event", "id": r.id, "at": _iso(r.at), "event": r.event,
            "user_id": r.user_id, "actor": r.actor, "ip": r.ip,
            "user_agent": r.user_agent, "reason": r.reason, "detail": r.detail}


def _audit_read_line(r: AuditRead) -> dict:
    return {"kind": "audit_read", "id": r.id, "at": _iso(r.at),
            "user_id": r.user_id, "actor_user_id": r.actor_user_id,
            "actor_subject": r.actor_subject, "token_id": r.token_id, "tool": r.tool,
            "route": r.route, "node_ids": list(r.node_ids or []),
            "request_id": r.request_id}


_KIND_MODEL = {
    "graph_event": (models_graph.GraphEvent, _graph_event_line),
    "auth_event": (models_auth.AuthEvent, _auth_event_line),
    "audit_read": (AuditRead, _audit_read_line),
}


def _rows(kind: str, since: Optional[datetime], until: Optional[datetime]) -> Iterator[tuple]:
    """(at, kind order, id, line) for one kind, oldest first, by keyset pages.

    Keyset on (at, id), never OFFSET: a row inserted during a long export cannot shift
    a page boundary and duplicate or drop a row, and each page is one index range."""
    from db import session
    model, render = _KIND_MODEL[kind]
    order = EXPORT_KINDS.index(kind)
    after: Optional[tuple] = None
    while True:
        clauses = []
        if since is not None:
            clauses.append(model.at >= since)
        if until is not None:
            clauses.append(model.at < until)
        if after is not None:
            clauses.append(tuple_(model.at, model.id) > tuple_(*after))
        with session() as s:
            batch = s.scalars(select(model).where(*clauses)
                              .order_by(model.at, model.id).limit(EXPORT_BATCH)).all()
            out = [(r.at, order, r.id, render(r)) for r in batch]
            if batch:
                after = (batch[-1].at, batch[-1].id)
        yield from out
        if len(out) < EXPORT_BATCH:
            return


def export_lines(kinds: Iterable[str], since: Optional[datetime],
                 until: Optional[datetime]) -> Iterator[str]:
    """One JSON object per line, ordered by time across every kind asked for.

    Ties at one instant break by kind (EXPORT_KINDS order), then id, so the same
    export of the same rows is byte-identical. Keys are emitted in the fixed order of
    the `_*_line` functions."""
    import heapq
    streams = [_rows(k, since, until) for k in EXPORT_KINDS if k in set(kinds)]
    for _at, _order, _id, line in heapq.merge(*streams, key=lambda t: (t[0], t[1], t[2])):
        yield json.dumps(line, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #
def purge_reads(engine, retention_days: Optional[int], *, now: Optional[datetime] = None) -> int:
    """Delete `audit_read` rows older than `retention_days`. Returns the count.

    `None` (ATLAS_AUDIT_RETENTION_DAYS unset) deletes nothing: keep forever is the
    default. ONLY `audit_read` is touched; there is no code path here or anywhere
    that purges `graph_event` or `auth_event`."""
    if retention_days is None:
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        result = s.execute(delete(AuditRead).where(AuditRead.at < cutoff))
        s.commit()
        return int(result.rowcount or 0)
