"""`events` routes, split out of `app.py` (STORY-oss-04); the code is moved, not changed.
Registered on the app by `main.py` via `include_router`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select

import models_graph  # Atlas v2 object-graph schema (ADR 0019); coexists with Component below
import models_auth   # auth_* + node_visibility, own Bases (ADR 0025/0026, TASK-966/973)
from db import session
from deps import _assignee_map, _visible_node_ids, _require_owner, _acting_user_id

router = APIRouter()


# --------------------------------------------------------------------------- #
# GET /api/events (TASK-1045) — reading `graph_event` back.
#
# Paging and response shape are `GET /api/workitems`'s, deliberately, down to
# the field names: `total`/`count`/`page`/`page_size`/`filters`/
# `max_page_size`/`items`. Two list endpoints in one API that page differently
# is two things for a client to learn and one of them to get wrong, and `total`
# vs `count` is precisely the pair the Mine view already got wrong once
# (verify_assignment.py §G exists because of it).
#
# `total` IS COMPUTED AFTER THE ACCESS FILTER, not before. That is the whole
# reason the filter is a WHERE clause here rather than a list comprehension
# over the page: a caller who could see a filtered page against an unfiltered
# total could count the events about nodes they cannot see, and a count of
# changes to a private project is information about a private project. Same
# rule, same reason, as `list_workitems`.
# --------------------------------------------------------------------------- #
EVENT_LIST_DEFAULT_PAGE_SIZE = 50
EVENT_LIST_MAX_PAGE_SIZE = 200

#: `?actor_kind=` vocabulary for `GET /api/events` — "agent", "person" or
#: "system", answering a DIFFERENT question than the `GraphEvent.actor_kind`
#: column it filters on (`session | token | grace | system`, the CREDENTIAL
#: kind). The mapping below is exact, not a heuristic: `auth.py` refuses any
#: Bearer token whose owning account is not `is_service` (every "token"
#: principal is a bot), and `admin session mint` refuses to mint a session
#: for a service principal (a session, full or "grace" mid-forced-change, is
#: never a bot) — so partitioning on the stored credential kind partitions
#: actors into agent vs person exactly, with nothing left to reconcile at
#: read time.
#:
#: DECISION (this file, 2026-08-24, repairing the audit-reader branch):
#: `"system"` — a principal-less write: a migration, a REPL, a startup path —
#: IS a first-class bucket here, not an omission. It used to match neither
#: `agent` nor `person` on purpose, which read as tidy right up until a test
#: asserted `agent ∪ person == unfiltered` and that stopped being true the
#: moment a system-actor row existed in the window: nobody signed in did it,
#: so it belongs in NEITHER of the other two, but it still belongs in
#: SOMETHING a caller can ask for by name — otherwise a whole class of graph
#: events (anything ADR 0034's person→bot link cannot even attribute to a
#: bot) is unreachable through the one documented filter. Exposing it costs
#: nothing extra: the mapping is exact and exhaustive over the CREDENTIAL
#: kinds `record_graph_event` ever actually writes (`session|token|grace` when
#: `auth.current_principal` is set, `"system"` when it is not — see that
#: function), so `agent ∪ person ∪ system` covers `GraphEvent.actor_kind`'s
#: entire written domain BY CONSTRUCTION, not by luck of the fixture. The 400
#: on an unrecognised `?actor_kind=` already reads its vocabulary out of this
#: dict (`sorted(_EVENT_ACTOR_KIND_FILTERS)`), so the refusal message stays in
#: lockstep with the filter automatically — nothing else to keep in sync.
_EVENT_ACTOR_KIND_FILTERS: dict[str, tuple[str, ...]] = {
    "agent": ("token",),
    "person": ("session", "grace"),
    "system": ("system",),
}


def _event_time_bound(value: str | None, field: str) -> tuple[object | None, JSONResponse | None]:
    """Parse an ISO-8601 `since`/`until`, or a 400 NAMING THE FIELD.

    Naive input is read as UTC rather than refused: every timestamp this app
    stores is tz-aware UTC, so `?since=2026-08-24` has exactly one sensible
    meaning and refusing it would only teach callers to append `+00:00`.
    A malformed value is a 400 and never silently ignored — a time bound that
    quietly does nothing returns a superset of what was asked for, which for
    an audit query is the wrong direction to fail.
    """
    if value is None:
        return None, None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, JSONResponse(
            {"error": "bad_time_bound", "field": field,
             "detail": f"{field}={value!r} is not an ISO-8601 timestamp"},
            status_code=400,
        )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed, None


#: Actions whose `detail.from`/`detail.to` are ACCOUNT IDS rather than status
#: vocabulary — currently just the one. Named as a set, not a single string
#: compare inline, so a second id-shaped action (there isn't one yet) has one
#: place to join rather than a second `if row.action == "..."` growing up
#: beside this one.
_GRAPH_EVENT_ACCOUNT_DETAIL_ACTIONS = frozenset({"node.assignee"})


def _resolve_account_detail(detail: object, people: dict[str, dict]) -> object:
    """`{"from": uid_or_None, "to": uid_or_None}` -> the same shape with each
    id RESOLVED to `{id, username, name}` — same idiom as `actor`/`responsible`
    above, applied to the one `detail` shape that also names an account.

    Only called for `_GRAPH_EVENT_ACCOUNT_DETAIL_ACTIONS`; `node.status`'s
    `from`/`to` are status strings and must never reach this function, or
    every status row would render a name where a status belongs.

    An id with no matching account (the account was since deleted) resolves
    to `{"id": uid}` — never to `None` and never dropped — because "assigned
    to somebody now gone" and "unassigned" are different facts and collapsing
    them would say the wrong one.
    """
    if not isinstance(detail, dict):
        return detail
    resolved = dict(detail)
    for key in ("from", "to"):
        if key not in detail:
            continue
        uid = detail[key]
        resolved[key] = (people.get(uid) or {"id": uid}) if uid else None
    return resolved


def _graph_event_json(row: models_graph.GraphEvent, people: dict[str, dict]) -> dict:
    """One event. `actor` and `responsible` are RESOLVED to `{id, username,
    name}` — the same shape `/api/people` and every node payload use, so the
    board reads one vocabulary for "a person" everywhere.

    A raw id is still carried in `actor.user_id` / `responsible_user_id`
    because the account may be gone, in which case there is nothing to resolve
    and `actor.subject` (copied at write time) is all that is left. That is why
    the column exists.

    `detail` gets the same treatment CONDITIONALLY: a `node.assignee` event's
    `from`/`to` are account ids and are resolved the same way; every other
    action's `detail` is passed through verbatim, because `node.status` uses
    the identical `{"from", "to"}` shape for status strings and resolving
    those through the people map would corrupt every status row in the log.
    """
    detail = row.detail
    if row.action in _GRAPH_EVENT_ACCOUNT_DETAIL_ACTIONS:
        detail = _resolve_account_detail(detail, people)
    return {
        "id": row.id,
        "at": row.at.isoformat() if row.at is not None else None,
        "action": row.action,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "node_id": row.node_id,
        "related_node_id": row.related_node_id,
        "actor": {
            "kind": row.actor_kind,
            "user_id": row.actor_user_id,
            "subject": row.actor_subject,
            "token_id": row.actor_token_id,
            "account": people.get(row.actor_user_id) if row.actor_user_id else None,
        },
        "responsible_user_id": row.responsible_user_id,
        "responsible": people.get(row.responsible_user_id) if row.responsible_user_id else None,
        "detail": detail,
    }


@router.get("/api/events")
def list_events(entity_id: str | None = None, entity_type: str | None = None,
                node_id: str | None = None, action: str | None = None,
                actor: str | None = None, actor_kind: str | None = None,
                responsible: str | None = None,
                since: str | None = None, until: str | None = None,
                page: int = 1,
                page_size: int = EVENT_LIST_DEFAULT_PAGE_SIZE) -> JSONResponse:
    """Who changed what in the graph, newest first.

    Filters (every one composes onto the next, the same way `/api/workitems`'s
    do):
      entity_id    — the exact thing acted on: a node id, or an edge id as a
                     string. Pairs with `entity_type` when a node and an edge
                     could conceivably share a spelling.
      entity_type  — `node` | `edge`.
      node_id      — every event GOVERNED by this node, which for an edge means
                     either endpoint. This is the "show me this node's history"
                     filter; `entity_id` is the narrower "this exact row".
      action       — comma-separated, e.g. `node.status,node.assignee`.
      actor        — an `auth_user.id`, or `me`. THE SPECIFIC ACCOUNT, and you
                     have to already know its id.
      actor_kind   — `agent` | `person` | `system`. Answers "was this a bot,
                     a human, or nobody signed in" — `system` is a
                     principal-less write (a migration, a REPL, a startup
                     path), and is filterable in its own right rather than
                     only showing up in the unfiltered list; see
                     `_EVENT_ACTOR_KIND_FILTERS` for the exact mapping onto
                     the stored credential kind, and why the three buckets
                     are exhaustive over it by construction. An unrecognised
                     value is a 400 naming the field, same as a malformed
                     `since`/`until`.
      responsible  — an `auth_user.id`, or `me`. Different question from
                     `actor`: an agent acts, a person is accountable.
      since/until  — ISO-8601, inclusive lower bound and exclusive upper.
      page/page_size — 1-based; `page_size` clamps to EVENT_LIST_MAX_PAGE_SIZE.

    ACCESS. `_visible_node_ids` is applied INSIDE the query, on BOTH governing
    columns, and an event is returned only when EVERY node it names is visible
    — an AND, because an edge from a shared node into a private one describes
    the private one. `verify_graph_event.py` §E proves this by attempting the
    violation rather than by asserting the happy path.

    An event about a node that has since been DELETED is visible to an owner
    (who is unrestricted) and to nobody else, because a deleted id is in no
    computed visible set. Fail-closed and deliberate; see `delete_node`.
    """
    page = max(1, page)
    page_size = max(1, min(page_size, EVENT_LIST_MAX_PAGE_SIZE))

    since_ts, refused = _event_time_bound(since, "since")
    if refused is not None:
        return refused
    until_ts, refused = _event_time_bound(until, "until")
    if refused is not None:
        return refused

    actor_kinds = None
    if actor_kind is not None:
        actor_kinds = _EVENT_ACTOR_KIND_FILTERS.get(actor_kind)
        if actor_kinds is None:
            return JSONResponse(
                {"error": "bad_actor_kind", "field": "actor_kind",
                 "detail": f"actor_kind={actor_kind!r} must be one of "
                           f"{sorted(_EVENT_ACTOR_KIND_FILTERS)}"},
                status_code=400,
            )

    Event = models_graph.GraphEvent
    with session() as s:
        clauses = []
        if entity_id:
            clauses.append(Event.entity_id == entity_id)
        if entity_type:
            clauses.append(Event.entity_type == entity_type)
        if node_id:
            # Either governing column — "this node's history" includes the
            # edges drawn INTO it, which is where a re-parent or a re-binding
            # shows up.
            clauses.append(or_(Event.node_id == node_id, Event.related_node_id == node_id))
        if action:
            wanted = [a.strip() for a in action.split(",") if a.strip()]
            if wanted:
                clauses.append(Event.action.in_(wanted))
        me = _acting_user_id()
        if actor:
            clauses.append(Event.actor_user_id == (me if actor == "me" else actor))
        if actor_kinds is not None:
            clauses.append(Event.actor_kind.in_(actor_kinds))
        if responsible:
            clauses.append(Event.responsible_user_id == (me if responsible == "me" else responsible))
        if since_ts is not None:
            clauses.append(Event.at >= since_ts)
        if until_ts is not None:
            clauses.append(Event.at < until_ts)

        # THE ACCESS FILTER — one more clause on the SAME list, so it is part
        # of the WHERE that `total` counts, not a pass over the page.
        visible_ids = _visible_node_ids(s)
        if visible_ids is not None:
            clauses.append(or_(Event.node_id.is_(None), Event.node_id.in_(visible_ids)))
            clauses.append(or_(Event.related_node_id.is_(None),
                               Event.related_node_id.in_(visible_ids)))

        total = s.scalar(select(func.count()).select_from(Event).where(*clauses)) or 0
        rows = s.scalars(
            select(Event).where(*clauses)
            # `id` breaks the tie: several events from ONE request share a
            # timestamp to the microsecond, and an unstable order across pages
            # drops or duplicates rows at a page boundary.
            .order_by(Event.at.desc(), Event.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        ).all()

        people = _assignee_map(s)
        return JSONResponse({
            "total": total, "count": len(rows), "page": page, "page_size": page_size,
            "filters": {"entity_id": entity_id, "entity_type": entity_type,
                        "node_id": node_id, "action": action, "actor": actor,
                        "actor_kind": actor_kind,
                        "responsible": responsible, "since": since, "until": until},
            "max_page_size": EVENT_LIST_MAX_PAGE_SIZE,
            "items": [_graph_event_json(r, people) for r in rows],
        })


# --------------------------------------------------------------------------- #
# GET /api/auth-events — reading `auth_event` back.
#
# `auth_event` has had a writer since ADR 0025 and no reader at all: the model
# file's own header explains why access changes are logged HERE rather than to
# `graph_event` ("`graph_event` has a read route ... no read route, no filter
# to get wrong") but that reasoning is about WHERE an access change is logged,
# not about whether the identity/audit log as a whole should stay unreadable
# from the API forever. Until now the only way to read it back was `psql`.
#
# OWNER-ONLY, same rule and same shape as `GET /api/access`, and for the same
# two reasons: `auth_event` carries login/lockout detail (`ip`, `user_agent`,
# `reason`) about EVERY account, not just the caller's own, and `user_id` has
# no visibility axis to filter on — these rows name ACCOUNTS, not nodes, so
# `_visible_node_ids` does not apply and there is no per-row access rule to
# reach for. `_require_owner()` is the correct existing primitive: it already
# refuses a Bearer principal, because a bot's OWN role is always `member`
# (`Principal.is_owner` reads the principal's own row, never a linked
# person's — ADR 0034) — an `admin`-scoped agent token is still refused here,
# same as it is on `GET /api/access`.
# --------------------------------------------------------------------------- #
AUTH_EVENT_LIST_DEFAULT_PAGE_SIZE = 50
AUTH_EVENT_LIST_MAX_PAGE_SIZE = 200


def _auth_event_json(row: models_auth.AuthEvent, people: dict[str, dict]) -> dict:
    return {
        "id": row.id,
        "at": row.at.isoformat() if row.at is not None else None,
        "event": row.event,
        "user_id": row.user_id,
        # Resolved the same way `_graph_event_json` resolves `actor`/
        # `responsible`: `user_id` is always a real `auth_user.id` or None
        # (never a raw submitted username — `auth._event` is called with
        # `user.id if user else None` at every login site), so it is safe to
        # look up. An id with no matching account (deleted since) resolves to
        # `None` here, and `row.user_id` still carries the raw id.
        "account": people.get(row.user_id) if row.user_id else None,
        "actor": row.actor,
        "ip": row.ip,
        "user_agent": row.user_agent,
        "reason": row.reason,
        "detail": row.detail,
    }


@router.get("/api/auth-events")
def list_auth_events(event: str | None = None, user_id: str | None = None,
                     actor: str | None = None,
                     since: str | None = None, until: str | None = None,
                     page: int = 1,
                     page_size: int = AUTH_EVENT_LIST_DEFAULT_PAGE_SIZE) -> JSONResponse:
    """The identity/access audit trail, newest first. Owner-only.

    Filters:
      event         — comma-separated, e.g. `login_fail,lockout_engaged`.
      user_id       — the SUBJECT account's `auth_user.id` (whose login, whose
                      access changed) — not who did it. `me` is accepted.
      actor         — free text, matched exactly against the stored operator
                      name (`AuthEvent.actor`) — there is no account id here,
                      because a failed login very often names no account at
                      all (see the model header).
      since/until   — ISO-8601, inclusive lower bound and exclusive upper.
      page/page_size — 1-based; `page_size` clamps to
                      AUTH_EVENT_LIST_MAX_PAGE_SIZE.

    `total` is computed AFTER every filter, same idiom as `GET /api/events`,
    even though there is no access filter to apply here — paging honesty (a
    page that under- or over-reports how many rows exist) is the property
    worth keeping identical across both audit readers, not just the leak this
    one happens not to have.
    """
    denied = _require_owner()
    if denied is not None:
        return denied

    page = max(1, page)
    page_size = max(1, min(page_size, AUTH_EVENT_LIST_MAX_PAGE_SIZE))

    since_ts, refused = _event_time_bound(since, "since")
    if refused is not None:
        return refused
    until_ts, refused = _event_time_bound(until, "until")
    if refused is not None:
        return refused

    Event = models_auth.AuthEvent
    with session() as s:
        clauses = []
        if event:
            wanted = [e.strip() for e in event.split(",") if e.strip()]
            if wanted:
                clauses.append(Event.event.in_(wanted))
        if user_id:
            me = _acting_user_id()
            clauses.append(Event.user_id == (me if user_id == "me" else user_id))
        if actor:
            clauses.append(Event.actor == actor)
        if since_ts is not None:
            clauses.append(Event.at >= since_ts)
        if until_ts is not None:
            clauses.append(Event.at < until_ts)

        total = s.scalar(select(func.count()).select_from(Event).where(*clauses)) or 0
        rows = s.scalars(
            select(Event).where(*clauses)
            .order_by(Event.at.desc(), Event.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        ).all()

        people = _assignee_map(s)
        return JSONResponse({
            "total": total, "count": len(rows), "page": page, "page_size": page_size,
            "filters": {"event": event, "user_id": user_id, "actor": actor,
                        "since": since, "until": until},
            "max_page_size": AUTH_EVENT_LIST_MAX_PAGE_SIZE,
            "items": [_auth_event_json(r, people) for r in rows],
        })
