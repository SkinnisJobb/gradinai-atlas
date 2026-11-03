"""`GET /api/me/stats` (TASK-1444, STORY-atlas-dashboard-agent-stats): the signed-in person's
task flow and their agents' writes, as a handful of numbers.

ONE READ-ONLY ROUTE, so the dashboard never pages through `graph_event` in the browser.
Registered on the app by `main.py` via `include_router`.

WHOSE NUMBERS. Always the caller's own person, resolved from the principal and never from the
request: there is no parameter, and an unknown query parameter is ignored like on every other
route, so there is no way to ask for somebody else.
  - a browser session is the person (`principal.user_id`);
  - an agent token resolves through `auth_user.person_user_id` (ADR 0034) and gets that
    person's numbers, or a 403 when the agent is linked to nobody;
  - a principal with no account (the grace principal) gets a 403.

WHAT IS COUNTED.
  - `tasks_done_per_day`: TASKS assigned to the person (`node.assignee_user_id`) whose
    `node.status` event moved them to `done`, per UTC day, the last 7 days (`current`, the last
    entry is today) and the 7 before (`previous`), oldest first. A workitem counts ONCE, on
    the day of its latest move to `done` inside the 14 days, so a re-assertion or a
    done -> in_progress -> done round trip is one item and not two. Only `type == "task"`:
    an epic or story reaching done is not a task done (owner decision, 2026-10-30).
  - `agents`: per agent account linked to the person, the `graph_event` rows it wrote in the
    same two windows and the time of its latest write (any age), or null.
  - `decisions`: deliberately ABSENT, not zero, until the decision type exists (TASK-1453).

ACCESS. Every count is computed from rows already filtered by `_visible_node_ids` INSIDE the
query, the same rule as `GET /api/events`: an event is counted only when every node it names
is visible to the caller. A count over a hidden node is information about a hidden node, so a
post-filter would not do. For an agent token that is the agent's own view (its grants plus its
person's), which is never more than the person's.

PAYLOAD (ADR 0040: measure it). Numbers, two dates and one short label per agent. Measured in
`verify_me_stats.py` and reported there.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select

import auth
import models_auth
import models_graph
from attention import _norm
from db import session
from deps import _visible_node_ids

router = APIRouter()

#: Days per window. Two windows: the last `WINDOW_DAYS` and the `WINDOW_DAYS` before them.
WINDOW_DAYS = 7


def _stats_person_id(principal) -> str | None:
    """The person whose numbers this caller may read, or None (-> 403)."""
    if principal is None:
        return None
    if principal.is_service:
        return principal.person_user_id or None
    return principal.user_id or None


def _event_visibility_clauses(visible_ids: set[str] | None) -> list:
    """`GET /api/events`'s access rule: every node an event names must be visible."""
    if visible_ids is None:
        return []
    Event = models_graph.GraphEvent
    return [or_(Event.node_id.is_(None), Event.node_id.in_(visible_ids)),
            or_(Event.related_node_id.is_(None), Event.related_node_id.in_(visible_ids))]


@router.get("/api/me/stats")
def my_stats() -> JSONResponse:
    """The signed-in person's tasks done per day and their agents' writes, last 7 days and the 7 before.

    An agent token gets its own person's numbers, or 403 when it is linked to no person. There
    is no parameter that selects a person.
    """
    principal = auth.current_principal.get()
    person_id = _stats_person_id(principal)
    if person_id is None:
        return JSONResponse(
            {"error": "forbidden",
             "detail": "stats belong to a person; this credential is linked to none."},
            status_code=403)

    today = datetime.now(timezone.utc).date()
    first_day = today - timedelta(days=2 * WINDOW_DAYS - 1)
    start = datetime.combine(first_day, time.min, tzinfo=timezone.utc)

    def bucket(at) -> int | None:
        """Index 0..13 from the oldest day, or None outside the two windows."""
        if at is None:
            return None
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        i = (at.astimezone(timezone.utc).date() - first_day).days
        return i if 0 <= i < 2 * WINDOW_DAYS else None

    Event, Node = models_graph.GraphEvent, models_graph.Node
    with session() as s:
        visible_ids = _visible_node_ids(s)
        vis = _event_visibility_clauses(visible_ids)

        # --- tasks done per day --------------------------------------------------
        rows = s.execute(
            select(Event.node_id, Event.at, Event.detail)
            .join(Node, Node.id == Event.node_id)
            .where(Event.action == "node.status",
                   Event.at >= start,
                   Node.assignee_user_id == person_id,
                   Node.type == "task",
                   *vis)
        ).all()
        latest_done: dict[str, object] = {}
        for node_id, at, detail in rows:
            to = detail.get("to") if isinstance(detail, dict) else None
            if _norm(to) != "done":
                continue
            if node_id not in latest_done or at > latest_done[node_id]:
                latest_done[node_id] = at
        per_day = [0] * (2 * WINDOW_DAYS)
        for at in latest_done.values():
            i = bucket(at)
            if i is not None:
                per_day[i] += 1

        # --- the person's agents ---------------------------------------------------
        agents = s.scalars(
            select(models_auth.AuthUser)
            .where(models_auth.AuthUser.person_user_id == person_id,
                   models_auth.AuthUser.is_service.is_(True))
            .order_by(models_auth.AuthUser.subject)).all()
        agent_out = []
        for bot in agents:
            ats = s.scalars(
                select(Event.at).where(Event.actor_user_id == bot.id, Event.at >= start, *vis)
            ).all()
            counts = [0, 0]
            for at in ats:
                i = bucket(at)
                if i is not None:
                    counts[0 if i >= WINDOW_DAYS else 1] += 1
            last = s.scalar(
                select(func.max(Event.at)).where(Event.actor_user_id == bot.id, *vis))
            agent_out.append({
                "agent": bot.subject,
                "writes": counts[0],
                "writes_previous": counts[1],
                "last_write_at": last.isoformat() if last is not None else None,
            })

    current, previous = per_day[WINDOW_DAYS:], per_day[:WINDOW_DAYS]
    return JSONResponse({
        "as_of": today.isoformat(),
        "window_days": WINDOW_DAYS,
        "tasks_done_per_day": {"current": current, "previous": previous},
        "tasks_done": {"current": sum(current), "previous": sum(previous)},
        "agents": agent_out,
    })
