"""`workitems` routes, split out of `app.py` (STORY-oss-04); the code is moved, not changed.
Registered on the app by `main.py` via `include_router`.
"""
from __future__ import annotations

import itertools  # not `from itertools import chain`: list_workitems has a local `chain`
import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, false, or_, select
from sqlalchemy.orm import Session

import models_graph  # Atlas v2 object-graph schema (ADR 0019); coexists with Component below
import models_auth   # auth_* + node_visibility, own Bases (ADR 0025/0026, TASK-966/973)
import auth         # THE GATE (TASK-967) + service tokens (TASK-968)
import settings     # ATLAS_ACCEPTANCE_AGENT_WRITE (STORY-atlas-acceptance-human-approved)
import rbac         # project RBAC (ADR 0041); inert unless ATLAS_RBAC=projects
import payload_fields  # STORY-oss-14: ignored fields -> `warnings`, or a 422 under ATLAS_STRICT_API
from models_graph import Edge as GraphEdge, Node as GraphNode
from db import session
import audit_log  # STORY-oss-16 / TASK-1382: the read audit (off unless ATLAS_AUDIT_READS=1)
from deps import (
    _assignee_map,
    _graph_node_json,
    WORKITEM_TYPES,
    WORK_HIERARCHY_RANK,
    WORKITEM_PRIORITIES,
    LABEL_MAX_CHARS,
    LABELS_MAX,
    _status_payload,
    record_graph_event,
    EDGE_FAMILY_RULES,
    _check_family,
    _existing_binding,
    _refuse_second_binding,
    _attribute_validation_response,
    _acceptance_list,
    _existing_result_row,
    _result_heading_display,
    _visible_node_ids,
    _guard_node_access,
    _require_project_role,
    _project_root_creator,
    _bind_project_root_creator,
    WORKITEM_LIST_DEFAULT_PAGE_SIZE,
    WORKITEM_LIST_MAX_PAGE_SIZE,
    _search_hits,
)

router = APIRouter()

#: The work-HIERARCHY edge type (epic contains story contains task) — `contains`,
#: shared with the world hierarchy for good: the rename to `decomposes_into`
#: (TASK-1030) was cancelled on 2026-08-27. ONE constant all the same, named in
#: one place rather than at four call sites across create_workitem/patch_workitem.
#:
#: Deliberately NOT folded into EDGE_FAMILY_RULES above: that dict is keyed by
#: the edge-type STRING, and `contains` already holds an ("object", "object")
#: rule there for the WORLD hierarchy (see that rule's own comment) — reusing
#: the same key for the WORK hierarchy would silently overwrite or conflict
#: with it, since both hierarchies share one string. So the routes below
#: enforce this rule directly
#: (via `_check_family`), scoped to just the parent edge they create — never
#: through EDGE_FAMILY_RULES, and never through generic POST /api/edge (which
#: is why an existing epic/story/task `contains` edge, world OR work, is left
#: alone by the object/object rule above: that rule only binds NEW edges made
#: through /api/edge, per its own comment).
#: Defined in models_graph so `attention.py` can use it too without importing
#: app (app imports attention, so that direction is a cycle). Re-exported here
#: because every existing reference in this file — and the comment above — names
#: it unqualified.
WORK_HIERARCHY_EDGE_TYPE = models_graph.WORK_HIERARCHY_EDGE_TYPE
WORK_HIERARCHY_FAMILY_RULE: tuple[str, str] = ("workitem", "workitem")


def _work_descendants(s: Session, root_id: str) -> set[str]:
    """Every workitem below `root_id` through work `contains` edges (both ends
    workitems), `root_id` excluded. Iterative and visited-set guarded, so a
    cycle already in the data cannot loop it."""
    seen: set[str] = set()
    frontier = [root_id]
    while frontier:
        rows = s.scalars(
            select(GraphEdge.dst)
            .join(GraphNode, GraphNode.id == GraphEdge.dst)
            .where(GraphEdge.src.in_(frontier),
                   GraphEdge.edge_type == WORK_HIERARCHY_EDGE_TYPE,
                   GraphNode.type.in_(WORKITEM_TYPES))
        ).all()
        frontier = [d for d in dict.fromkeys(rows) if d not in seen and d != root_id]
        seen.update(frontier)
    return seen


def _check_work_parent(s: Session, child: GraphNode | None, child_type: str,
                       parent: GraphNode, field: str = "parent") -> JSONResponse | None:
    """TASK-1378: the rules a work parent must keep, on top of the family rule.
    A 400 naming the rule, or None. Read-only: it runs before anything is
    written, so a refusal leaves the edge table and `graph_event` untouched.

    1. no self-parent (`parent_is_self`);
    2. hierarchy order (`parent_order`, the ladder is `deps.WORK_HIERARCHY_RANK`):
       the parent sits strictly above the child — epic > story > task, levels
       may be skipped, never repeated or inverted;
    3. no cycle (`parent_cycle`): the parent is not one of the child's work
       descendants through `contains`.
    `child_type` is the type the child will have after this request, so a
    PATCH that changes `type` and `parent` together is judged as a whole.
    `child=None` is a create: the node does not exist yet, so only (2) applies.
    `field` names what the caller sent that broke the rule: `parent` for a
    move or a create, `type` for a type change judged against the parent the
    item already has."""
    if child is not None and parent.id == child.id:
        return JSONResponse(
            {"error": "parent_is_self", "field": field,
             "detail": f"{child.id!r} cannot be its own parent"},
            status_code=400,
        )
    p_rank, c_rank = WORK_HIERARCHY_RANK.get(parent.type), WORK_HIERARCHY_RANK.get(child_type)
    if p_rank is None or c_rank is None or p_rank >= c_rank:
        return JSONResponse(
            {"error": "parent_order", "field": field,
             "detail": f"a {parent.type!r} ({parent.id!r}) cannot contain a {child_type!r}: the work "
                       f"hierarchy is {' > '.join(WORKITEM_TYPES)}, and a parent must sit above its child",
             "order": list(WORKITEM_TYPES)},
            status_code=400,
        )
    if child is not None and parent.id in _work_descendants(s, child.id):
        return JSONResponse(
            {"error": "parent_cycle", "field": field,
             "detail": f"{parent.id!r} is below {child.id!r} in the work hierarchy; "
                       f"making it the parent would create a cycle"},
            status_code=400,
        )
    return None


def _check_work_children(s: Session, n: GraphNode, new_type: str) -> JSONResponse | None:
    """TASK-1378: a type change judged against the item's existing work
    children (`contains` edges to workitems). Every child must still sit
    strictly below the item's NEW type, else `child_order` (400, field `type`)
    naming the first child that would break the order: a story with tasks
    under it cannot become a task. Read-only, like `_check_work_parent`."""
    rank = WORK_HIERARCHY_RANK.get(new_type)
    rows = s.execute(
        select(GraphNode.id, GraphNode.type)
        .join(GraphEdge, GraphEdge.dst == GraphNode.id)
        .where(GraphEdge.src == n.id, GraphEdge.edge_type == WORK_HIERARCHY_EDGE_TYPE,
               GraphNode.type.in_(WORKITEM_TYPES))
        .order_by(GraphNode.id)
    ).all()
    for cid, ctype in rows:
        if rank is None or WORK_HIERARCHY_RANK[ctype] <= rank:
            return JSONResponse(
                {"error": "child_order", "field": "type",
                 "detail": f"{n.id!r} cannot become a {new_type!r}: it contains the {ctype!r} {cid!r}, "
                           f"and the work hierarchy is {' > '.join(WORKITEM_TYPES)}; a parent must "
                           f"sit above its children",
                 "order": list(WORKITEM_TYPES)},
                status_code=400,
            )
    return None


# --------------------------------------------------------------------------- #
# Workitem authoring (TASK-1048) — "create and update a workitem over HTTP" as
# ONE call, on top of the node/edge construction kit above. POST /api/node +
# POST /api/edge already let a caller BUILD a task out of three or four calls;
# that is not an authoring path (nothing stops a half-built one — a task
# inserted, then the parent edge failing for some reason, left with no
# parent — from landing, which is exactly the invisible-work failure ADR 0028
# warns about). These two routes are the transactional whole-record path:
#
# * `POST /api/workitem` mints/accepts the id, creates the node, its
#   acceptance list, its parent edge, its (at most one) world binding and its
#   depends_on edges in ONE unit of work — see `create_workitem`'s own
#   docstring for exactly how atomicity is achieved (it leans on
#   `recompute_derived`'s `s.commit()` being the only commit reachable from
#   any REQUEST path in this module. There IS one other `session.commit()`
#   in app.py — `_seed_task_id_sequence`'s — but it runs once at startup and
#   never inside a request, so it cannot commit a half-built workitem).
# * `PATCH /api/workitem/{id}` accepts every field the create route does,
#   `exclude_unset` — same NodePatch discipline as `PATCH /api/node/{id}`,
#   extended to the four edge-backed fields that route cannot touch.
# * Both reuse `auth.guard_write`, `_check_family`/`EDGE_FAMILY_RULES`/
#   `WORK_HIERARCHY_FAMILY_RULE`, `_refuse_second_binding` and
#   `recompute_derived` — no second write guard, no second family-rule
#   implementation, no second single-binding rule.
# --------------------------------------------------------------------------- #


class WorkitemWrite(payload_fields.TracksUnknownFields):
    """Body for `POST /api/workitem`. `id` is accepted ONLY for `epic`/`story`
    (caller-supplied slugs — there is no minter for those, per the id-scheme
    decision); a `task` must NOT supply one — `create_workitem` calls
    `models_graph.allocate_task_id` and refuses a caller-supplied id outright,
    per that function's own contract ("no caller should ever construct a task
    id any other way").

    `result` (TASK-1064) is the TEXT ONLY of the `## Result`/`## Outcome`
    block — never the heading, never a position. Those are presentation and
    placement details a caller recording an outcome should not have to know;
    see `models_graph.WorkitemResult` and `_replace_result` for where they
    come from on create (heading is always synthesised as `"## Result"`,
    position is appended at the end)."""
    type: str
    id: str | None = None
    name: str
    #: RETIRED ON WORKITEMS (TASK-1120). Declared here ONLY so the route can
    #: refuse it by name — Pydantic drops an undeclared key in silence, and a
    #: silent drop is the outcome `owner_retired_refusal` exists to avoid. It is
    #: never read into the node row; sending it at all is a 400.
    owner: str | None = None
    #: `auth_user.id`, or null/omitted for unassigned (TASK-1118). Validated by
    #: `_resolve_assignee` — a person, active, or a 400 naming the field.
    #: THIS is the field `owner` above was retired in favour of.
    assignee: str | None = None
    summary: str | None = None
    abstract: str | None = None
    detail: str | None = None
    body: str | None = None
    result: str | None = None
    acceptance: list[str] = Field(default_factory=list)
    parent: str | None = None
    binding: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    status: str | None = None
    #: STORY-oss-14: one of `WORKITEM_PRIORITIES`, or null/omitted for unset.
    priority: str | None = None
    #: STORY-oss-14: short tags, normalised by `_normalise_priority_labels`.
    labels: list[str] | None = None
    attributes: dict = Field(default_factory=dict)
    csrf_token: str | None = None


class WorkitemPatch(payload_fields.TracksUnknownFields):
    """Every field `WorkitemWrite` accepts, optional, `exclude_unset` — same
    contract as `NodePatch`'s `attributes` (a PATCH that omits a field never
    touches it), extended to the edge-backed fields:

    * `parent`/`binding`: `null` explicitly UNSETS (unparents / unbinds);
      omitted leaves the existing edge alone; a string re-points it (parent:
      moves it, replacing whatever edge was there — see `patch_workitem`;
      binding: refused with a 409 if one already exists, per ADR 0028 §1 —
      unbind first).
    * `acceptance`/`depends_on`: when the field is PRESENT at all — including
      `[]` and including an explicit `null`, both of which CLEAR it — it
      REPLACES the full set — `acceptance` positionally (a delete +
      reinsert, matching `AcceptanceCriterion`'s own "nothing to patch one
      criterion of" docstring), `depends_on` by diff (a set, not an ordered
      list — see `_replace_depends_on`).
    * `result` (TASK-1064): follows `acceptance`'s presence/absence discipline,
      not its replace-wholesale one — see `_replace_result`. Omitted leaves
      the existing `workitem_result` row untouched; a string UPDATES the row's
      `text` in place, PRESERVING its existing `heading`/`position` (3 of the
      221 result-bearing files in the corpus write `## Outcome`, not
      `## Result` — a blind rewrite of the heading would silently rewrite the
      document's own words), or creates one (heading `"## Result"`, position =
      the current number of `##` sections in `body`, i.e. appended at the
      end) if none exists yet; `null` deletes the row. In every case
      `node.body` itself is never touched by this field — that is the whole
      point of TASK-1064; see the body-handling note in `patch_workitem`."""
    type: str | None = None
    name: str | None = None
    #: RETIRED ON WORKITEMS (TASK-1120) — same as `WorkitemWrite.owner`:
    #: declared so it can be refused by name, never applied. A PATCH carrying it
    #: is a 400 and changes nothing else in the same request.
    owner: str | None = None
    #: OMITTED leaves the assignee alone; explicit `null` UNASSIGNS. Same
    #: presence discipline as `parent`/`binding` above, via `exclude_unset`.
    assignee: str | None = None
    summary: str | None = None
    abstract: str | None = None
    detail: str | None = None
    body: str | None = None
    result: str | None = None
    acceptance: list[str] | None = None
    parent: str | None = None
    binding: str | None = None
    depends_on: list[str] | None = None
    status: str | None = None
    #: STORY-oss-14. Omitted leaves it; null clears it; a value replaces it.
    priority: str | None = None
    #: STORY-oss-14. Omitted leaves them; a list REPLACES the set; [] or null clears.
    labels: list[str] | None = None
    attributes: dict | None = None
    #: STORY-atlas-acceptance-human-approved: `{"by": <a person's username>,
    #: "quote": <their words>}`. Read ONLY when a machine principal changes
    #: `acceptance` under `ATLAS_ACCEPTANCE_AGENT_WRITE=attested`; see
    #: `attested_acceptance_refusal`. Untyped and validated there, so a
    #: malformed approval gets the rail's 403 and its hint rather than a 422.
    #: Never written to the node.
    approval: Any = None
    csrf_token: str | None = None


def _existing_parent_edge(s: Session, node_id: str) -> GraphEdge | None:
    """This workitem's current inbound work-hierarchy edge (its parent), if
    any. A node may have more than one INCOMING `contains` edge in principle
    (ADR 0027's multi-root world is not itself a workitem-hierarchy
    constraint) but this route only ever manages the one IT created —
    deterministic pick (lowest edge id), same convention as
    `_existing_binding`."""
    rows = s.scalars(
        select(GraphEdge)
        .where(GraphEdge.dst == node_id, GraphEdge.edge_type == WORK_HIERARCHY_EDGE_TYPE)
        .order_by(GraphEdge.id)
    ).all()
    return rows[0] if rows else None


def acceptance_rail_refusal():
    """RAIL 1 (STORY-atlas-fork-loop-rails): an implementing agent may not
    rewrite the criteria it is judged against.

    Returns a `JSONResponse` to send back, or `None` to allow the write.

    **Why this exists.** `acceptance` rides ordinary `write` scope, so without
    it the same token that does the work can redefine what "done" means. A
    green run then proves only that the agent agreed with itself — and every
    other guarantee in `EPIC-atlas-experimental-fork` rests on criteria the
    implementer cannot touch.

    **The rule.** A MACHINE principal cannot change the acceptance list of an
    EXISTING workitem; a browser session can. Creation is untouched — `POST
    /api/workitem` still carries `acceptance`, because authoring new work is
    proposing, not re-judging work already under way.

    **Why not scope it to the assignee**, which is what "the implementer"
    literally means: an agent would unassign itself, edit, and reassign — three
    ordinary writes, each individually legitimate, and nothing in the store
    remembers who held it a moment ago. Closing that needs an audit trail —
    `graph_event` EXISTS as of TASK-1045 and records every assignee change, but
    it is a RECORD, not a rail: it says who unassigned and reassigned, after the
    fact, and refuses nothing. So the assignee-scoped version would still LOOK
    like a rail while being one PATCH away from useless.
    This rule has no such hole and is a strict superset of the criterion.

    It restates a decision already recorded as an intent on `atlas`: agents
    propose, humans commit. ADR 0033 let agents write intents, so tool-absence
    stopped being the boundary; this is the boundary in its place, for the one
    thing that defines success.

    **Fails closed.** No principal at all is refused too — this route is behind
    the gate, so that should be unreachable, and a rail whose unreachable branch
    permits is a rail waiting for a routing change.

    **A separate function rather than an inline `if` on purpose**, so
    `verify_loop_rails.py` can remove it and watch the check go red. A rail that
    cannot be shown to fail has not been shown to do anything.

    **Unchanged by the two opt-in ways past it** (STORY-atlas-acceptance-human-
    approved, the block below): an attested write and an approved proposal both
    start from THIS function's answer and only decide what to do with its
    refusal. With `ATLAS_ACCEPTANCE_AGENT_WRITE` unset, its refusal is returned
    as it always was.

    LIVE since 2026-08-25. It was proven on the fork first, per the story's
    out-of-scope list, and this line said "NOT ON LIVE YET" until the deploy
    that carried it. Verified by grepping this symbol in the running
    `atlas-api` container, not inferred from the branch it sits on.
    """
    principal = auth.current_principal.get()
    if principal is not None and principal.kind == "session":
        return None
    return JSONResponse(
        {"error": "acceptance_is_not_yours_to_write",
         "detail": "Acceptance criteria define what this item is judged against, "
                   "so the principal doing the work cannot change them. Propose "
                   "the change to a human, who can make it from the board. Every "
                   "other field on this route is unaffected.",
         "field": "acceptance",
         "principal_kind": (principal.kind if principal else None)},
        status_code=403,
    )


# --------------------------------------------------------------------------- #
# STORY-atlas-acceptance-human-approved — two opt-in ways past the rail above,
# both carrying a HUMAN's approval. Neither edits `acceptance_rail_refusal`:
# the rail still decides first, and these only answer what to do with its
# refusal.
#
# (a) ATTESTED WRITE, `ATLAS_ACCEPTANCE_AGENT_WRITE=attested` (default `off`).
#     A machine PATCH of `acceptance` that the rail refuses is let through when
#     it carries `approval: {by, quote}` naming an existing, active PERSON
#     account. The server cannot see the conversation the quote came from, so
#     this is an attestation, not a proof: what it does guarantee is that the
#     name is a real current human, and that the claim is recorded
#     (`node.acceptance.attested`, with approver, quote, agent and the old and
#     new lists) where everyone who can see the item can read it. With the
#     setting `off` the rail's 403 is returned untouched, byte for byte.
# (b) PROPOSAL, any mode. A machine stores a proposed list
#     (`POST /api/workitem/{id}/acceptance-proposal`); a browser session
#     approves it, which applies it through `patch_workitem` exactly as a
#     person editing acceptance on the board would, or rejects it.
# --------------------------------------------------------------------------- #

#: The longest approval quote accepted. A quote is a person's words about one
#: change, not a document; the cap keeps an event row from carrying a transcript.
APPROVAL_QUOTE_MAX = 2000


def attested_acceptance_mode() -> str:
    """`ATLAS_ACCEPTANCE_AGENT_WRITE`, read now through `settings.load()`.

    `"off"` (the default, and today's rail exactly) or `"attested"`. A value
    that does not parse reads as `"off"`: boot already refuses it
    (`settings.load` raises naming the variable), so this branch is reachable
    only if the environment changed under a running process, and an unreadable
    setting must not be what opens the rail."""
    try:
        return settings.load().acceptance_agent_write
    except settings.SettingsError:
        return "off"


def attested_acceptance_refusal(refusal: JSONResponse, reason: str) -> JSONResponse:
    """The rail's own 403, plus a `hint` saying how an attested write is made.

    Used only when the setting is `attested`. With the setting `off` the rail's
    response goes back untouched, so an install that never sets the variable
    gets exactly the refusal it got before this story."""
    payload = json.loads(bytes(refusal.body))
    payload["hint"] = (
        f"{reason} This install has ATLAS_ACCEPTANCE_AGENT_WRITE=attested: a machine "
        "principal may change acceptance when the request also carries `approval`, "
        "{\"by\": <the approving person's username>, \"quote\": <their words>}. "
        "Or propose the list (POST /api/workitem/{id}/acceptance-proposal) and a "
        "person approves it on the board."
    )
    return JSONResponse(payload, status_code=refusal.status_code)


def _approval_shape_error(approval) -> str | None:
    """Why `approval` is not `{by: str, quote: str}`, or None. Shape only; who
    `by` names is checked against the accounts by `_resolve_approver`."""
    if approval is None:
        return "The request carries no `approval`."
    if not isinstance(approval, dict):
        return "`approval` must be an object, {\"by\": ..., \"quote\": ...}."
    by, quote = approval.get("by"), approval.get("quote")
    if not isinstance(by, str) or not by.strip():
        return "`approval.by` must be the approving person's username."
    if not isinstance(quote, str) or not quote.strip():
        return "`approval.quote` must be the approving person's words."
    if len(quote) > APPROVAL_QUOTE_MAX:
        return f"`approval.quote` is longer than {APPROVAL_QUOTE_MAX} characters."
    extra = sorted(set(approval) - {"by", "quote"})
    if extra:
        return f"`approval` carries unknown keys {extra}; only `by` and `quote` are read."
    return None


def _resolve_approver(s: Session, username: str):
    """`(AuthUser, None)` for one existing, active PERSON with this username,
    else `(None, reason)`.

    An approval is a person's act. An agent or service account cannot approve,
    or one agent could vouch for another and the attestation would be the
    self-assertion this story exists to avoid; a disabled account cannot
    either, because offboarding must end a person's ability to authorise."""
    rows = s.scalars(select(models_auth.AuthUser).where(
        models_auth.AuthUser.subject == username.strip())).all()
    people = [u for u in rows if not u.is_service]
    if not people:
        if rows:
            return None, f"`approval.by` {username!r} is an agent or service account, not a person."
        return None, f"`approval.by` {username!r} is not an account on this install."
    active = [u for u in people if u.disabled_at is None]
    if not active:
        return None, f"`approval.by` {username!r} is a disabled account."
    if len(active) > 1:
        return None, f"`approval.by` {username!r} names more than one account."
    return active[0], None


def _replace_acceptance(s: Session, node_id: str, texts: list[str]) -> None:
    """Delete-and-reinsert, not an in-place patch — `AcceptanceCriterion`'s
    own docstring: "nothing meaningful to patch one criterion of when the
    source of truth is 'line N of the list changed'"."""
    s.execute(delete(models_graph.AcceptanceCriterion).where(
        models_graph.AcceptanceCriterion.node_id == node_id))
    for i, text in enumerate(texts):
        s.add(models_graph.AcceptanceCriterion(node_id=node_id, position=i, text=text))


def _result_section_count(body: str | None) -> int:
    """How many `##` sections `body` currently has — what a freshly-created
    `workitem_result` row's `position` is set to, so it lands appended at the
    end (see `models_graph.WorkitemResult.position`'s "section index, not a
    byte offset" docstring). `0` for an empty/absent body, same as
    `models_graph.h2_indices` on an empty line list."""
    if not body:
        return 0
    return len(models_graph.h2_indices(body.split("\n")))


def _replace_result(s: Session, node_id: str, text: str | None, body: str | None) -> None:
    """Apply a `result` write (POST create-path caller passes the row
    directly, PATCH goes through here) — TASK-1064's whole point: this NEVER
    reads or writes `node.body`, so recording a result cannot destroy the
    spec that lives there (see the body-handling note in `patch_workitem` for
    the incident this replaces).

    * `text is None` (PATCH `result: null`) — delete the existing row, if any.
    * an existing row — update its `text` IN PLACE, preserving `heading` and
      `position` exactly (see `WorkitemPatch`'s own docstring for why: 3 of
      the 221 result-bearing files write `## Outcome`, not `## Result`).
    * no existing row — create one: heading is always the canonical
      `"## Result"` (there is no prior heading to preserve), position is
      `_result_section_count(body)` so `reassemble` would append it as the
      LAST section of the current body.
    """
    existing = _existing_result_row(s, node_id)
    if text is None:
        if existing is not None:
            s.delete(existing)
        return
    if existing is not None:
        existing.text = text
        return
    s.add(models_graph.WorkitemResult(
        node_id=node_id, position=_result_section_count(body),
        heading="## Result", text=text,
    ))


def _edge_snapshot(e: GraphEdge) -> dict:
    """The four fields that describe an edge, read while the row still exists.

    TASK-1045: an edge id says nothing once the row is gone, so every
    `edge.delete` event copies `src`/`dst`/`edge_type` into its `detail`. This
    is the one place that shape is spelled, so the delete sites cannot drift
    apart from each other."""
    return {"id": e.id, "src": e.src, "dst": e.dst, "edge_type": e.edge_type}


def _replace_depends_on(s: Session, node_id: str, desired_ids: list[str]) -> tuple[list[GraphEdge], list[dict]]:
    """Diff-sync this node's OUTBOUND `depends_on` edges to exactly
    `desired_ids` (deduped; a set, unlike `acceptance` — order carries no
    meaning here, matching `attention.py`'s own edge-adjacency read).

    Returns `(added, removed)` — the new `GraphEdge` objects (their ids are
    still None until the caller flushes) and a snapshot per removed row. It
    RETURNS them rather than recording the events itself: `record_graph_event`
    is called from routes, so every call site stays visible in the handler
    somebody reads when they ask "what does this endpoint log?"."""
    desired = set(desired_ids)
    current = {e.dst: e for e in s.scalars(
        select(GraphEdge).where(GraphEdge.src == node_id, GraphEdge.edge_type == "depends_on")
    ).all()}
    added: list[GraphEdge] = []
    removed: list[dict] = []
    for dst in desired - set(current):
        e = GraphEdge(src=node_id, dst=dst, edge_type="depends_on", origin="atlas")
        s.add(e)
        added.append(e)
    for dst in set(current) - desired:
        removed.append(_edge_snapshot(current[dst]))
        s.delete(current[dst])
    return added, removed


def _strip_depends_on_mirror(attrs: dict | None) -> dict:
    """TASK-1048 decision: `depends_on` gets exactly ONE home, the edge table
    — never `attributes["depends_on"]`. `ingest_graph.py` used to write that
    key for GIT-authored nodes; it is deleted (TASK-1058), so nothing writes
    it any more, but rows it already wrote still CARRY it — which is why this
    stripper stays. Nothing reads it: `attention.py`'s blocking/urgency logic and
    `panel.js`'s depends_on lookup both read the EDGE, never the attribute
    (checked, not assumed — see both files). This route therefore never
    creates or preserves that copy, so a node it ever touches cannot end up
    carrying two representations that silently drift apart — the exact shape
    of bug this repo has been bitten by before with two sources of one fact
    (see AGENTS.md's status-override design notes). Called on every
    create/PATCH that writes `attributes` at all, so a node that started as a
    git-origin import with the legacy mirror loses it the first time this API
    touches the node, not just on a fresh create."""
    attrs = dict(attrs or {})
    attrs.pop("depends_on", None)
    return attrs


def _normalise_priority_labels(priority, labels) -> tuple[str | None, list[str] | None, JSONResponse | None]:
    """STORY-oss-14: validate a write's `priority` and `labels`. Returns
    `(priority, labels, refusal)`, the values as they are STORED.

    `priority` is matched case-insensitively against `WORKITEM_PRIORITIES` and
    stored lowercase; None stays None (unset). `labels` are stripped, lowercased
    and de-duplicated in the order given; an empty result is stored as None, so
    `[]` and `null` both mean "no labels". Lowercase because a label is a tag
    people filter and search on, and `Backend` and `backend` being two tags is a
    mistake nobody makes on purpose. A refusal is a 400 NAMING THE FIELD, the
    `_resolve_assignee` shape, and it is returned before anything is written."""
    def refuse(field: str, detail: str):
        return None, None, JSONResponse(
            {"error": f"invalid_{field}", "field": field, "detail": detail}, status_code=400)

    if priority is not None:
        wanted = priority.strip().lower()
        if wanted not in WORKITEM_PRIORITIES:
            return refuse("priority", f"{priority!r} is not one of {list(WORKITEM_PRIORITIES)} "
                                      f"(lowest first); send null to clear it.")
        priority = wanted
    if labels is not None:
        clean: list[str] = []
        for raw in labels:
            label = " ".join(raw.split()).lower()
            if not label:
                return refuse("labels", "a label is empty; send [] or null to clear the labels.")
            if len(label) > LABEL_MAX_CHARS:
                return refuse("labels", f"label {label[:LABEL_MAX_CHARS]!r}... is longer than "
                                        f"{LABEL_MAX_CHARS} characters; a label is a short tag.")
            if "," in label:
                return refuse("labels", f"label {label!r} contains a comma, which the "
                                        "`?label=` filter splits on.")
            if label not in clean:
                clean.append(label)
        if len(clean) > LABELS_MAX:
            return refuse("labels", f"{len(clean)} labels; at most {LABELS_MAX}.")
        labels = clean or None
    return priority, labels, None


def _depends_on_mirror_problem(attrs: dict | None) -> list[dict]:
    """STORY-oss-14: `attributes.depends_on` is stripped by `_strip_depends_on_mirror`
    on every write here. Said out loud rather than dropped."""
    if isinstance(attrs, dict) and "depends_on" in attrs:
        return [payload_fields.problem(
            "attributes.depends_on", payload_fields.IGNORED,
            "`attributes.depends_on` is not stored: dependencies live only as edges. "
            "Send the top-level `depends_on` list instead.")]
    return []


def _workitem_payload(s: Session, n: GraphNode) -> dict:
    """The create/PATCH response for the routes below: `_status_payload`
    (node columns + derived status) plus the things it does not project —
    `body`, `result`/`result_heading` (TASK-1064), `acceptance` and the three
    edge-backed fields (`parent`/`binding`/`depends_on`). `_graph_node_json`
    itself is left untouched here on purpose (TASK-1050, the READ-path story
    landing alongside this one, is where a general projection belongs); this
    route promises ITS OWN response is complete regardless of when that
    lands, by reading the acceptance/result rows and edges directly rather
    than waiting on it."""
    d = _status_payload(s, n)
    d["body"] = n.body
    result_row = _existing_result_row(s, n.id)
    d["result"] = result_row.text if result_row is not None else None
    d["result_heading"] = (
        _result_heading_display(result_row.heading) if result_row is not None else None
    )
    d["acceptance"] = _acceptance_list(s, n.id)
    parent_edge = _existing_parent_edge(s, n.id)
    d["parent"] = parent_edge.src if parent_edge is not None else None
    binding_edge = _existing_binding(s, n.id)
    d["binding"] = binding_edge.dst if binding_edge is not None else None
    dep_rows = s.scalars(
        select(GraphEdge).where(GraphEdge.src == n.id, GraphEdge.edge_type == "depends_on")
    ).all()
    d["depends_on"] = sorted(e.dst for e in dep_rows)
    return d


@router.get("/api/vocabulary", response_model=None)
def vocabulary(schemas: str = "full") -> dict | JSONResponse:
    """The write API's legal vocabulary, machine-readable (TASK-1081).

    Atlas serves no OpenAPI document on purpose (`docs_url`/`redoc_url`/
    `openapi_url` are all `None` at construction, above) — that decision is
    about not dumping the whole route surface to an authenticated caller
    (TASK-967) and this route does not reopen it: it is one curated, scoped
    endpoint, not a schema of every route. Without it an agent calling
    `POST/PATCH /api/node`, `/api/workitem` or `/api/edge` has no way to learn
    a legal node type, edge type, status or intent kind except by collecting
    400s until it stops getting them — `node_types.py`, `EDGE_FAMILY_RULES`,
    `attention.SETTABLE_STATUSES` and `node_types.INTENT_KINDS` already hold
    every one of those lists; this just reads them at request time.

    DERIVED, NOT TRANSCRIBED. Every value below is read off the constant or
    the table the write routes themselves enforce against — never a second
    hand-typed copy:
      - `node_types`      — the live `node_type` table (same rows
                             `POST /api/node`'s `type` FK and
                             `models_graph.validate_node`'s schema check use;
                             seeded from `node_types.py` at every boot, see
                             `ensure_all_node_types`).
      - `edge_types.rules` — `EDGE_FAMILY_RULES`, the same dict
                             `POST /api/edge` looks `edge_type` up in.
      - `workitem_statuses`— `attention.SETTABLE_STATUSES`, the same tuple
                             `POST/PATCH /api/workitem` and
                             `POST /api/node/{id}/status` validate against.
      - `workitem_types`   — `WORKITEM_TYPES`, the same tuple
                             `POST/PATCH /api/workitem` validates against.
      - `workitem_ladder`  — `node_types.WORKITEM_LADDER`, the hierarchy
                             rungs top first, the same order
                             `WORK_HIERARCHY_RANK` enforces parents by
                             (TASK-1389). A workitem type off the ladder is in
                             `workitem_types` but not here.
      - `intent_kinds`     — `node_types.INTENT_KINDS`, the same tuple
                             `POST /api/node/{id}/intent` validates against.
      - `workitem_priorities` — `WORKITEM_PRIORITIES`, lowest first, the
                             tuple `POST/PATCH /api/workitem` validates against.
    """
    from attention import SETTABLE_STATUSES
    from node_types import INTENT_KINDS, WORKITEM_LADDER

    if schemas not in ("full", "shared", "none"):
        return JSONResponse(
            {"error": "invalid_schemas_mode",
             "detail": f"`schemas` must be one of full, shared, none — got {schemas!r}."},
            status_code=400,
        )

    with session() as s:
        rows = s.scalars(select(models_graph.NodeType)).all()
        node_types = {
            nt.id: {
                "family": nt.family,
                "attributes_schema": nt.attributes_schema,
                "agent_instructions": nt.agent_instructions,
                "status": nt.status,
            }
            for nt in rows
        }

    # ONE SCHEMA, SIXTEEN COPIES (TASK-1157). Every backlog type shares one
    # `attributes_schema` and every world type shares another, so `full` ships
    # the same two blocks once per type — most of this response, to deliver a
    # list of type names. `shared` names each distinct schema once and points
    # the types at it; `none` drops them for a caller that only wants the legal
    # values. `full` stays the default and stays byte-identical: it is what the
    # board and any existing caller already parse.
    shared_schemas: dict[str, dict] = {}
    if schemas != "full":
        by_json: dict[str, str] = {}
        for type_id, payload in node_types.items():
            schema = payload.pop("attributes_schema")
            if schemas == "none":
                continue
            key = json.dumps(schema, sort_keys=True)
            name = by_json.get(key)
            if name is None:
                name = by_json[key] = "schema_%d" % (len(by_json) + 1)
                shared_schemas[name] = schema
            payload["attributes_schema_ref"] = name

    edge_type_rules = {
        edge_type: {"src_family": src_family, "dst_family": dst_family}
        for edge_type, (src_family, dst_family) in EDGE_FAMILY_RULES.items()
    }

    return {
        "node_types": node_types,
        # Present only for `schemas=shared`: each distinct attributes schema
        # once, referenced by the types above through `attributes_schema_ref`.
        **({"attributes_schemas": shared_schemas} if shared_schemas else {}),
        "edge_types": {
            "rules": edge_type_rules,
            # Not a second rule for the same key — a caveat on the one above.
            # `WORK_HIERARCHY_EDGE_TYPE` (today `"contains"`) already appears
            # in `rules` with the WORLD-hierarchy requirement
            # (object -> object, enforced by POST /api/edge); the WORK
            # hierarchy reuses that same edge-type string but is
            # `WORK_HIERARCHY_FAMILY_RULE` (workitem -> workitem) and is
            # enforced only inline in create_workitem/patch_workitem — never
            # through EDGE_FAMILY_RULES or generic POST /api/edge. See the
            # comment on WORK_HIERARCHY_EDGE_TYPE above for why the two
            # cannot share one dict entry while both hierarchies use the
            # one edge-type string.
            "notes": {
                WORK_HIERARCHY_EDGE_TYPE: (
                    f"the rule above for {WORK_HIERARCHY_EDGE_TYPE!r} is the WORLD-hierarchy "
                    f"rule, enforced by POST /api/edge. The WORK hierarchy (epic contains "
                    f"story contains task) reuses the same edge-type string but requires "
                    f"{WORK_HIERARCHY_FAMILY_RULE[0]} -> {WORK_HIERARCHY_FAMILY_RULE[1]}, and "
                    f"is only creatable through POST/PATCH /api/workitem -- never through "
                    f"POST /api/edge."
                ),
            },
        },
        "workitem_statuses": list(SETTABLE_STATUSES),
        "workitem_types": list(WORKITEM_TYPES),
        # TASK-1389: the hierarchy ladder, top first; additive beside the list above.
        "workitem_ladder": list(WORKITEM_LADDER),
        "intent_kinds": list(INTENT_KINDS),
        # STORY-oss-14: lowest first, the order `priority` is validated against.
        "workitem_priorities": list(WORKITEM_PRIORITIES),
    }


def _resolve_assignee(s: Session, value: str | None) -> tuple[str | None, JSONResponse | None]:
    """Validate a write's `assignee` value. Returns `(user_id, refusal)`.

    `None` in means unassigned, which is always legal — clearing an assignee
    needs no account to exist and no permission beyond the write itself.

    THIS IS WHERE "agents are not assignable" IS ACTUALLY ENFORCED (ADR 0034;
    owner decision, 2026-08-17). `/api/people` not offering a bot is a convention the
    picker follows; this is the rule. An agent's work is its person's
    accountability, so a bot in this column would name somebody who is not
    answerable for it.

    Disabled accounts are refused too, but note the deliberate asymmetry with
    `_assignee_map`, which resolves EVERY account including disabled ones: you
    cannot newly assign work to a revoked account, and work already assigned to
    one keeps rendering that person's name instead of silently going blank.
    Disabling somebody must not orphan their work on screen — that is how you
    lose track of what they were carrying.

    The FK this column deliberately does not have (see
    `models_graph.Node.assignee_user_id`) is what makes this function
    load-bearing rather than belt-and-braces: it is the only thing standing
    between the write API and a dangling id.
    """
    if value is None:
        return None, None
    row = s.get(models_auth.AuthUser, value)
    if row is None:
        return None, JSONResponse(
            {"error": "invalid_assignee",
             "field": "assignee",
             "detail": f"no account with id {value!r}"},
            status_code=400,
        )
    if row.is_service:
        return None, JSONResponse(
            {"error": "invalid_assignee",
             "field": "assignee",
             "detail": (f"{row.subject!r} is an agent, not a person — an agent's work is "
                        f"its person's accountability (ADR 0034), so assign the person")},
            status_code=400,
        )
    if row.disabled_at is not None:
        return None, JSONResponse(
            {"error": "invalid_assignee",
             "field": "assignee",
             "detail": f"account {row.subject!r} is disabled"},
            status_code=400,
        )
    return value, None


def owner_retired_refusal() -> JSONResponse:
    """TASK-1120: `owner` IS RETIRED ON WORKITEMS, and this route says so out
    loud instead of dropping the field.

    A workitem's assignee is an ACCOUNT — `node.assignee_user_id`, an
    `auth_user.id`, validated by `_resolve_assignee`. `owner` was the free-text
    string that preceded it; `migrate_assignee_from_owner.py` mapped the
    existing values onto real accounts and every workitem now reads back with a
    resolved `assignee`. The column SURVIVES (see `models_graph.Node.owner`)
    because on an OBJECT-family node it still means "who looks after this part
    of the world" and is the right field there — so this refusal belongs to the
    two `/api/workitem` routes, not to `POST`/`PATCH /api/node`, which is where
    an object node's `owner` is legitimately written.

    **400, NOT A SILENT IGNORE, and refused on PRESENCE rather than on value.**
    Both halves were argued rather than assumed:

    * Ignoring it accepts the write, returns 200, and leaves the caller
      believing a field they sent took effect. The failure would surface later
      as "the owner I set disappeared", with nothing in the response, the log or
      `graph_event` to connect it to this decision. The whole point of retiring
      the field is that a second, unenforced way to say who work belongs to is
      how work goes missing; a 200 that quietly discards it keeps that second
      way alive in every caller's head.
    * Refusing on PRESENCE means `{"owner": null}` is refused too, even though
      its effect would have been indistinguishable from omitting the key. That
      is deliberate: the API cannot tell "my client always serializes this
      field" from "I mean to set no owner", and both are fixed the same way —
      stop sending the key. Presence-based is also the discipline every other
      field on these routes already uses (`exclude_unset`: `parent`, `binding`,
      `acceptance`, `result`, `assignee` all test presence BEFORE value), and it
      is the version that finds a stale caller which sends `null` today and a
      name tomorrow. The one in-repo caller that sent the key unconditionally —
      `atlas_file_work` in `mcp/` — had the argument removed in the same change.

    The shape is `_resolve_assignee`'s: 400, `error`/`field`/`detail`, NAMING
    THE FIELD, because a caller has to be able to act on it without reading
    this docstring.
    """
    return JSONResponse(
        {"error": "owner_retired",
         "field": "owner",
         "detail": ("`owner` is retired on workitems — who a workitem belongs to is an "
                    "ACCOUNT, not a string. Send `assignee` (an auth_user.id, or null "
                    "for unassigned) instead, or omit the field entirely. `owner` is "
                    "still the right field on an object-family node; write that one "
                    "through POST/PATCH /api/node.")},
        status_code=400,
    )


def _minting_project_key(s: Session, parent_id: str | None,
                         binding_id: str | None) -> str | None:
    """The project key a NEW task is minted under, or None for `TASK-`
    (STORY-oss-12-project-keys).

    The task's project is the one RBAC would give it (`rbac.project_map`,
    ADR 0041 §1): a workitem follows what it WORKS ON, so its own `binding`
    wins, else it inherits through its `parent`, and from there the nearest
    `contains` ancestor of type `project`. The new row does not exist yet, so
    the anchor it WILL have is asked instead — the same answer the map gives
    the row once it is written.

    `TASK-` unless that resolves to exactly ONE project and it carries a key:
    no anchor, a root that is not a project, several projects (a diamond
    across two of them — no single project to name the id after), or a
    `contains` cycle all keep today's id. Decided once, at creation: moving
    the item later never renumbers it (nothing reads this again)."""
    anchor = binding_id or parent_id
    if anchor is None:
        return None
    projects = rbac.project_map(s).get(anchor) or frozenset()
    if len(projects) != 1:
        return None
    (pid,) = projects
    row = s.execute(
        select(GraphNode.project_key).where(GraphNode.id == pid,
                                            GraphNode.type == rbac.PROJECT_NODE_TYPE)
    ).first()
    return row[0] if row and row[0] else None


@router.post("/api/workitem")
def create_workitem(body: WorkitemWrite, request: Request) -> JSONResponse:
    """Create a workitem — node, acceptance, parent edge, binding, depends_on
    — in ONE transaction (TASK-1048 step 1).

    ATOMICITY. Every check that can still fail after the node row exists
    (unknown parent/binding/depends_on id, a family mismatch, an
    attributes-schema violation) runs and is returned BEFORE
    `recompute_derived(s)` — which is the only `session.commit()` reachable
    from any REQUEST path in this module (see `attention.recompute_derived`'s
    own docstring: it calls `session.commit()` as its last line. The one other
    commit in app.py is `_seed_task_id_sequence`'s, which runs at startup only —
    `with session() as s:` is a plain `Session(engine)` context manager, and
    SQLAlchemy's default `Session.close()` on an UNCOMMITTED transaction rolls
    it back — verified directly against this engine, not assumed from the
    docs). So a request that fails ANY check after the node is `s.add()`-ed
    (e.g. an unknown `depends_on` id discovered only after the node itself
    validated) never reaches that commit, and everything this request did —
    the node row included — evaporates when the `with` block exits. A
    half-created task with no parent, the invisible-work failure ADR 0028
    warns about, cannot reach the database from this route.

    ID. A `task` id is ALWAYS minted via `models_graph.allocate_task_id` —
    never accepted from the caller (see `WorkitemWrite`'s docstring). An
    `epic`/`story` id is ALWAYS caller-supplied (no minter exists for those).
    Note the one accepted consequence of `allocate_task_id`'s own contract:
    the sequence's `nextval()` is not transactional, so a task id minted here
    is spent even if this same request goes on to fail a later check and roll
    back everything else — the corpus was already non-contiguous before this
    task and stays non-contiguous in a (harmless) different way.

    A task whose project carries a key (STORY-oss-12) is minted `KEY-n` by
    `models_graph.allocate_project_task_id` instead — transactional, so a
    refused create there spends nothing. `_minting_project_key` decides which.
    """
    from attention import recompute_derived, SETTABLE_STATUSES, _norm

    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused

    # STORY-oss-14: what this request carries that the route will not apply.
    # Named on the 201 (`warnings`) by default; a 422 under ATLAS_STRICT_API,
    # before a task id is minted.
    ignored = body.unknown_field_problems() + _depends_on_mirror_problem(body.attributes)
    refused = payload_fields.strict_refusal(ignored)
    if refused is not None:
        return refused

    if body.type not in WORKITEM_TYPES:
        return JSONResponse(
            {"error": "invalid_type", "detail": f"{body.type!r} is not one of {list(WORKITEM_TYPES)}"},
            status_code=400,
        )

    # TASK-1120. Refused up here with the other body-shape checks, before the
    # session opens and before a task id is minted: a refused create must not
    # burn a sequence number (see this route's ID note), and there is nothing
    # about a retired field that needs the database to decide.
    if "owner" in body.model_fields_set:
        return owner_retired_refusal()

    wanted_status = _norm(body.status) if body.status is not None else None
    if wanted_status is not None and wanted_status not in SETTABLE_STATUSES:
        return JSONResponse(
            {"error": "unknown_status",
             "detail": f"{body.status!r} is not one of {list(SETTABLE_STATUSES)}",
             "allowed": list(SETTABLE_STATUSES)},
            status_code=400,
        )

    # STORY-oss-14, before the session for the same reason as `owner`: a refused
    # create must not burn a sequence number.
    priority, labels, refusal = _normalise_priority_labels(body.priority, body.labels)
    if refusal is not None:
        return refusal

    with session() as s:
        # -- id: minted for a task, caller-supplied (slug) for epic/story ---- #
        mint_unkeyed_now = False
        if body.type == "task":
            if body.id is not None:
                return JSONResponse(
                    {"error": "id_is_minted",
                     "detail": "a task id is minted by the server (models_graph.allocate_task_id); "
                               "do not supply one"},
                    status_code=400,
                )
            # STORY-oss-12. With no keyed project anywhere this is today's line,
            # unchanged and at today's moment. Once a project has a key, which
            # sequence the id comes from depends on where the task lands, so the
            # mint waits (`node_id is None`) until parent and binding are
            # resolved, just before the row is built — see below.
            # TASK-1378: the unkeyed mint waits too, but only until the parent
            # has been resolved and its order checked (just below), so a create
            # refused for its parent spends no TASK- number either.
            node_id = None
            mint_unkeyed_now = not models_graph.any_project_key(s)
        else:
            if not body.id:
                return JSONResponse(
                    {"error": "id_required",
                     "detail": f"{body.type} ids are caller-supplied slugs (no minter exists for "
                               f"this type yet) — see the id-scheme decision"},
                    status_code=400,
                )
            node_id = body.id
            if s.get(GraphNode, node_id) is not None:
                return JSONResponse(
                    {"error": "node_exists", "detail": f"node {node_id!r} already exists"},
                    status_code=409,
                )

        # ACCESS FILTER (STORY-atlas-access-admin). Every id this route accepts
        # from the caller — parent, binding, depends_on — is resolved through
        # this, so a node they cannot see reads back as NOT EXISTING and the
        # existing `unknown_*` 404s carry the refusal unchanged. Attaching new
        # work to a hidden node would also drag that node into the caller's own
        # rollups, so this closes a write-shaped read leak, not just a write.
        _wi_visible = _visible_node_ids(s)

        def _resolve(nid: str):
            if _wi_visible is not None and nid not in _wi_visible:
                return None
            return s.get(GraphNode, nid)

        # -- parent: must exist and be workitem-family, checked up front ----- #
        parent_node = None
        if body.parent:
            parent_node = _resolve(body.parent)
            if parent_node is None:
                return JSONResponse(
                    {"error": "unknown_parent", "detail": f"unknown node id {body.parent!r}"},
                    status_code=404,
                )
            # TASK-1378: the same order rule PATCH keeps, BEFORE any id is
            # minted. A parent outside the workitem types is left to the family
            # check below (409 family_mismatch, unchanged). Self and cycle cannot
            # arise for a node that does not exist yet (an existing id is a 409
            # above), so only the order can refuse here.
            if parent_node.type in WORK_HIERARCHY_RANK:
                order_err = _check_work_parent(s, None, body.type, parent_node)
                if order_err is not None:
                    return order_err

        if body.type == "task" and mint_unkeyed_now:
            node_id = models_graph.allocate_task_id(s)

        # -- binding: at most one — a brand-new node has no prior edges, so ---
        # -- that can only be violated by supplying more than one, and this ---
        # -- field only ever accepts a single id. Existence + family only. ---
        binding_node = None
        if body.binding:
            binding_node = _resolve(body.binding)
            if binding_node is None:
                return JSONResponse(
                    {"error": "unknown_binding", "detail": f"unknown node id {body.binding!r}"},
                    status_code=404,
                )

        # -- depends_on: every id must already exist -------------------------
        dep_ids = list(dict.fromkeys(body.depends_on or []))
        missing = [d for d in dep_ids if _resolve(d) is None]
        if missing:
            return JSONResponse(
                {"error": "unknown_depends_on", "detail": f"unknown node id(s): {missing}"},
                status_code=404,
            )

        # ADR 0041 (RBAC on only): the new item lands in its parent's and its
        # binding's project, so it needs `editor` there. A `depends_on` target is
        # referenced, not changed: seeing it (above) is enough.
        refused = _require_project_role(s, [body.parent, body.binding], rbac.EDITOR)
        if refused is not None:
            return refused
        # ADR 0051 (RBAC on only): with neither a parent nor a binding, the new
        # item is UNBOUND, and `rbac.project_map` makes an unbound work item its
        # own project. It counts as a project root for this rule even though
        # `rbac.require` does not ask `owner` to delete one: what matters here is
        # visibility, and an unbound item on a private-by-default install was
        # the same 201-then-404 as a root `org`. Decided before the id is
        # minted, so a refusal burns no number. RBAC off: (None, None).
        root_owner = None
        if parent_node is None and binding_node is None:
            root_owner, refused = _project_root_creator(node_id or body.id or "(new work item)",
                                                     work_item=True)
            if refused is not None:
                return refused

        # -- the assignee, BEFORE the node row exists (TASK-1118) -------------
        # Validated up here with the other resolvable references, not after
        # `s.add`, because this route's whole atomicity contract is that every
        # check which can still fail once the row exists runs FIRST. A 400 for
        # a bad assignee arriving after the insert would leave a created-then-
        # rolled-back id, and workitem ids come from a sequence — a burned
        # number is accepted for a real creation, not for a typo'd uuid.
        assignee_id, refusal = _resolve_assignee(s, body.assignee)
        if refusal is not None:
            return refusal

        # -- the deferred task id (STORY-oss-12) ------------------------------
        # Last, after every check that needs no row: a keyed id takes a row
        # lock on its project's counter that is held until this request's
        # commit, so the shorter that window the better. A refusal from here
        # on rolls the counter back with everything else — no gap.
        if node_id is None:
            key = _minting_project_key(
                s, parent_node.id if parent_node is not None else None,
                binding_node.id if binding_node is not None else None,
            )
            node_id = (models_graph.allocate_project_task_id(s, key) if key
                       else models_graph.allocate_task_id(s))

        # -- build and validate the node itself -------------------------------
        attrs = _strip_depends_on_mirror(body.attributes)
        if wanted_status is not None:
            attrs["status"] = wanted_status
        # NO `owner=` (TASK-1120). It is refused above, so there is nothing to
        # pass — and this construction site is the one the acceptance criterion
        # "`owner` IS RETIRED ON WORKITEMS" was actually about. A workitem
        # created here has `owner IS NULL` and an `assignee_user_id` or nothing.
        n = GraphNode(
            id=node_id, type=body.type, name=body.name, body=body.body,
            summary=body.summary, abstract=body.abstract, detail=body.detail,
            assignee_user_id=assignee_id, attributes=attrs,
            priority=priority, labels=labels,
            origin="atlas",  # provenance: authored through the API, not a file
        )
        s.add(n)
        try:
            s.flush()
        except ValueError as exc:
            s.rollback()
            return _attribute_validation_response(exc)

        # -- family rules, now that both endpoints are real rows -------------
        if parent_node is not None:
            fam_err = _check_family(s, WORK_HIERARCHY_EDGE_TYPE, WORK_HIERARCHY_FAMILY_RULE, parent_node, n)
            if fam_err is not None:
                s.rollback()
                return fam_err
        if binding_node is not None:
            fam_err = _check_family(s, "works_on", EDGE_FAMILY_RULES["works_on"], n, binding_node)
            if fam_err is not None:
                s.rollback()
                return fam_err

        for i, text in enumerate(body.acceptance or []):
            s.add(models_graph.AcceptanceCriterion(node_id=node_id, position=i, text=text))
        if body.result is not None:
            # Create path only — a brand-new node has no prior result row to
            # preserve a heading/position for. See `_replace_result`'s
            # docstring for the update/delete cases PATCH exercises.
            s.add(models_graph.WorkitemResult(
                node_id=node_id, position=_result_section_count(body.body),
                heading="## Result", text=body.result,
            ))
        # TASK-1045 collects these rather than firing and forgetting them: an
        # edge's id does not exist until the flush below, and the event carries
        # it. `new_edges` is only ever appended to here.
        new_edges: list[GraphEdge] = []
        if parent_node is not None:
            new_edges.append(GraphEdge(src=parent_node.id, dst=node_id,
                                       edge_type=WORK_HIERARCHY_EDGE_TYPE, origin="atlas"))
        if binding_node is not None:
            new_edges.append(GraphEdge(src=node_id, dst=binding_node.id,
                                       edge_type="works_on", origin="atlas"))
        for dep in dep_ids:
            new_edges.append(GraphEdge(src=node_id, dst=dep, edge_type="depends_on",
                                       origin="atlas"))
        for e in new_edges:
            s.add(e)

        try:
            s.flush()
        except ValueError as exc:
            s.rollback()
            return _attribute_validation_response(exc)

        # TASK-1045. One `node.create` for the item, then the status and the
        # assignee as their OWN events when the caller set them — not folded
        # into the create's `detail`, because "when did this become mine" and
        # "when did this go in-flight" are the two questions the log is asked,
        # and an answer that only exists inside a create row is an answer that
        # cannot be found by filtering on `action`.
        record_graph_event(s, "node.create", entity_type="node", entity_id=node_id,
                           detail={"type": n.type, "name": n.name, "via": "workitem"})
        # Read off the STORED attributes, not `wanted_status`: this route also
        # accepts a whole `attributes` dict, so `{"attributes": {"status":
        # "done"}}` with no `status` field creates a done workitem and used to
        # leave `wanted_status is None` and no status event behind it. `attrs`
        # was built from `body.attributes` and then had `wanted_status` written
        # over it, so this covers both spellings with one read.
        created_status = (n.attributes or {}).get("status")
        if created_status is not None:
            record_graph_event(s, "node.status", entity_type="node", entity_id=node_id,
                               detail={"from": None, "to": created_status})
        if assignee_id is not None:
            record_graph_event(s, "node.assignee", entity_type="node", entity_id=node_id,
                               detail={"from": None, "to": assignee_id})
        for e in new_edges:
            record_graph_event(s, "edge.create", entity_type="edge", entity_id=e.id,
                               node_id=e.src, related_node_id=e.dst,
                               detail={"src": e.src, "dst": e.dst, "edge_type": e.edge_type})

        # ADR 0051: the unbound item's creator owns it. Before the commit in
        # `recompute_derived`; a no-op with RBAC off or for a bound item.
        _bind_project_root_creator(s, node_id, root_owner, request)

        recompute_derived(s)
        s.refresh(n)
        return JSONResponse(payload_fields.add_warnings(_workitem_payload(s, n), ignored),
                            status_code=201)


@router.patch("/api/workitem/{workitem_id}")
def patch_workitem(workitem_id: str, body: WorkitemPatch, request: Request) -> JSONResponse:
    """PATCH for every field `POST /api/workitem` accepts (TASK-1048 step 5):
    the plain node columns via the same `exclude_unset` idiom `PATCH
    /api/node/{id}` uses, PLUS `acceptance`/`parent`/`binding`/`depends_on`,
    which only this route (not the generic one) knows how to round-trip.

    ORIGIN, and why this route matches `edit_node`: both flip `origin` to
    `atlas` unconditionally on every edit. THE REASON IS NOW HISTORICAL. It
    was a defence against the boot ingest — a `git`-origin row was what the
    ingest could DELETE outright once its file disappeared, so leaving origin
    alone was the more dangerous option. **There is no ingest** (TASK-1058)
    and no file behind any row, so nothing reclaims a `git`-origin node and
    the flip defends against nothing. It is kept only so the two write routes
    agree; see `edit_node`'s note on why removing it is moot rather than
    wrong. What the flip DOES cost, now that `origin` means provenance, is
    that editing a git-imported node erases the record that it arrived that
    way. It still reports the change rather than making it silently:
    like `edit_node`, this route puts `detached_from_git: true` and a `note`
    explaining it in the response, every time a node that WAS git-owned gets
    edited here — the same fields, not a second vocabulary for the same fact.
    """
    from attention import recompute_derived, SETTABLE_STATUSES, _norm

    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused

    # `approval` is never a node field: excluded here so the setattr loop below
    # cannot see it, and read off `body` where it matters.
    updates = body.model_dump(exclude_unset=True, exclude={"csrf_token", "approval"})
    approval_provided = "approval" in body.model_fields_set

    if "type" in updates and updates["type"] not in WORKITEM_TYPES:
        return JSONResponse(
            {"error": "invalid_type", "detail": f"{updates['type']!r} is not one of {list(WORKITEM_TYPES)}"},
            status_code=400,
        )

    if approval_provided and "acceptance" not in updates:
        return JSONResponse(
            {"error": "approval_without_acceptance", "field": "approval",
             "detail": "`approval` attests a change to `acceptance`; this request "
                       "changes no acceptance, so there is nothing for it to approve."},
            status_code=400,
        )

    # STORY-atlas-acceptance-human-approved (a). The rail decides first and is
    # called exactly as before. Only its REFUSAL is then reconsidered, and only
    # under `attested`; `attested_approval` stays None on every other path, so a
    # session's approval field is ignored (the rail already let it through).
    attested_approval: dict | None = None
    rail_refusal: JSONResponse | None = None
    if "acceptance" in updates:
        _refusal = acceptance_rail_refusal()
        if _refusal is not None:
            if attested_acceptance_mode() != "attested":
                return _refusal
            shape = _approval_shape_error(body.approval if approval_provided else None)
            if shape is not None:
                return attested_acceptance_refusal(_refusal, shape)
            attested_approval, rail_refusal = body.approval, _refusal

    # STORY-oss-14: what this request carries that the route will not apply —
    # undeclared keys, `attributes.depends_on`, and an `approval` the rail did
    # not need (it let the acceptance change through on its own, so the
    # approval is never read). `warnings` on the 200, or a 422 under strict,
    # before anything is read from or written to the database.
    ignored = body.unknown_field_problems() + _depends_on_mirror_problem(updates.get("attributes"))
    if approval_provided and "acceptance" in updates and attested_approval is None:
        ignored.append(payload_fields.problem(
            "approval", payload_fields.IGNORED,
            "`approval` was not read: this caller may change acceptance without one."))
    refused = payload_fields.strict_refusal(ignored)
    if refused is not None:
        return refused

    if "priority" in updates or "labels" in updates:
        # Normalised in place, so the setattr loop below stores exactly what
        # was validated, and `node.update` names the field like any other.
        p_val, l_val, refusal = _normalise_priority_labels(updates.get("priority"),
                                                            updates.get("labels"))
        if refusal is not None:
            return refusal
        if "priority" in updates:
            updates["priority"] = p_val
        if "labels" in updates:
            updates["labels"] = l_val

    status_was_set = "status" in updates
    wanted_status = None
    if status_was_set:
        wanted_status = _norm(updates.pop("status"))
        if wanted_status is not None and wanted_status not in SETTABLE_STATUSES:
            return JSONResponse(
                {"error": "unknown_status",
                 "detail": f"{body.status!r} is not one of {list(SETTABLE_STATUSES)}",
                 "allowed": list(SETTABLE_STATUSES)},
                status_code=400,
            )

    with session() as s:
        n = s.get(GraphNode, workitem_id)
        if n is None:
            return JSONResponse({"error": f"unknown node {workitem_id!r}"}, status_code=404)
        denied = _guard_node_access(s, workitem_id) or _require_project_role(
            s, [workitem_id], rbac.EDITOR)
        if denied is not None:
            return denied
        if n.type not in WORKITEM_TYPES:
            return JSONResponse(
                {"error": "not_a_workitem",
                 "detail": f"node {workitem_id!r} is a {n.type!r}; /api/workitem is for "
                           f"{list(WORKITEM_TYPES)} only"},
                status_code=409,
            )

        # TASK-1120 — see `owner_retired_refusal`. DELIBERATELY BELOW the
        # `not_a_workitem` check, unlike the create path's, which runs before
        # the session opens. `PATCH /api/workitem/{id}` accepts any node id, so
        # `{"owner": ...}` aimed at an OBJECT node through this route is a
        # caller on the wrong route, not a caller using a retired field — and
        # "that node is not a workitem, use /api/node" is the answer that gets
        # them to the route where `owner` still works. Refusing first would
        # answer the less useful of the two true things.
        if "owner" in updates:
            return owner_retired_refusal()

        # (a) continued: WHO approved is checked against the accounts here,
        # after the item is known to exist and be visible, so a hidden item
        # still 404s exactly like an absent one.
        approver = None
        if attested_approval is not None:
            approver, why = _resolve_approver(s, attested_approval["by"])
            if approver is None:
                return attested_acceptance_refusal(rail_refusal, why)
        acceptance_before = _acceptance_list(s, workitem_id) if approver is not None else None

        # -- validate every edge-backed field up front, before any mutation -- #
        # TASK-1101: presence is tested BEFORE the pop, the same way `binding` and
        # `depends_on` do it below. It used to be `acceptance is not None`, which
        # made an explicit `{"acceptance": null}` indistinguishable from omitting
        # the field: 200, no error, and nothing changed. `binding: null` and
        # `depends_on: null` both CLEAR, so a caller reasonably expects the third
        # list field to behave the same, and silently doing nothing is the worst
        # of the three possible answers.
        acceptance_provided = "acceptance" in updates
        acceptance = updates.pop("acceptance", None)  # null and [] both clear the list

        # `result` (TASK-1064) lives in `workitem_result`, not on the node
        # itself — popped here, same as `acceptance`, so the generic
        # node-column setattr loop below never sees it (there is no
        # `GraphNode.result` column to set). `result_provided` distinguishes
        # "omitted" (untouched) from "provided" (a string updates/creates the
        # row, `None`/`null` deletes it) — see `WorkitemPatch`'s docstring and
        # `_replace_result`.
        result_provided = "result" in updates
        result_value = updates.pop("result", None)

        # ACCESS FILTER — same rule and same reason as `create_workitem`'s:
        # a re-parent or re-binding onto a node the caller cannot see reads
        # back as `unknown_*`, never as "forbidden".
        _wi_visible = _visible_node_ids(s)

        def _resolve(nid: str):
            if _wi_visible is not None and nid not in _wi_visible:
                return None
            return s.get(GraphNode, nid)

        # -- assignee (TASK-1118) --------------------------------------------
        # Popped before the generic setattr loop for the same reason
        # `parent`/`binding` are: the API field is `assignee` and the COLUMN is
        # `assignee_user_id`, so letting it reach that loop would `setattr` an
        # attribute the model does not have. Presence is tested BEFORE the pop
        # — omitted leaves the current assignee alone, explicit `null`
        # unassigns, and those are different requests.
        assignee_provided = "assignee" in updates
        assignee_value = updates.pop("assignee", None)
        new_assignee_id = None
        if assignee_provided:
            new_assignee_id, refusal = _resolve_assignee(s, assignee_value)
            if refusal is not None:
                return refusal

        parent_provided = "parent" in updates
        parent_value = updates.pop("parent", None)
        new_parent_node = None
        if parent_provided and parent_value is not None:
            new_parent_node = _resolve(parent_value)
            if new_parent_node is None:
                return JSONResponse(
                    {"error": "unknown_parent", "detail": f"unknown node id {parent_value!r}"},
                    status_code=404,
                )
            fam_err = _check_family(s, WORK_HIERARCHY_EDGE_TYPE, WORK_HIERARCHY_FAMILY_RULE, new_parent_node, n)
            if fam_err is not None:
                return fam_err
            order_err = _check_work_parent(s, n, updates.get("type", n.type), new_parent_node)
            if order_err is not None:
                return order_err

        # TASK-1378: a type change keeps the order against what the item is
        # already in and already holds. A parent sent in this same request was
        # judged against the new type just above; otherwise the existing work
        # parent is. Then every existing work child (`child_order`).
        new_type = updates.get("type", n.type)
        if new_type != n.type:
            if not parent_provided:
                pe = _existing_parent_edge(s, workitem_id)
                cur_parent = s.get(GraphNode, pe.src) if pe is not None else None
                if cur_parent is not None and cur_parent.type in WORK_HIERARCHY_RANK:
                    order_err = _check_work_parent(s, n, new_type, cur_parent, field="type")
                    if order_err is not None:
                        return order_err
            order_err = _check_work_children(s, n, new_type)
            if order_err is not None:
                return order_err

        binding_provided = "binding" in updates
        binding_value = updates.pop("binding", None)
        new_binding_node = None
        if binding_provided and binding_value is not None:
            new_binding_node = _resolve(binding_value)
            if new_binding_node is None:
                return JSONResponse(
                    {"error": "unknown_binding", "detail": f"unknown node id {binding_value!r}"},
                    status_code=404,
                )
            fam_err = _check_family(s, "works_on", EDGE_FAMILY_RULES["works_on"], n, new_binding_node)
            if fam_err is not None:
                return fam_err
            refuse = _refuse_second_binding(s, workitem_id)
            if refuse is not None:
                return refuse

        dep_provided = "depends_on" in updates
        dep_value = updates.pop("depends_on", None)
        dep_ids: list[str] = []
        if dep_provided and dep_value is not None:
            dep_ids = list(dict.fromkeys(dep_value))
            missing = [d for d in dep_ids if _resolve(d) is None]
            if missing:
                return JSONResponse(
                    {"error": "unknown_depends_on", "detail": f"unknown node id(s): {missing}"},
                    status_code=404,
                )

        # ADR 0041 (RBAC on only): moving the item under a new parent or binding
        # it to a new object puts it in that project, which needs `editor` there.
        refused = _require_project_role(
            s, [new_parent_node.id if new_parent_node is not None else None,
                new_binding_node.id if new_binding_node is not None else None], rbac.EDITOR)
        if refused is not None:
            return refused

        # -- was this row reconciled from a file BEFORE this edit? captured --
        # -- before the flip below, same as edit_node ------------------------
        was_git = models_graph.origin_is_git_value(n.origin)

        # -- plain node-column fields — same setattr idiom as edit_node ------ #
        # `body`, among these, REPLACES THE WHOLE COLUMN WHOLESALE — no
        # precondition (no "expected previous value" the caller must supply),
        # no audit trail. THIS IS DELIBERATE, decided in TASK-1064, not an
        # oversight left over from TASK-1063:
        #
        # The loss that motivated TASK-1063/1064 (TASK-1051/1053/1055 lost
        # 3,291/2,513/2,170 characters of spec) happened because RECORDING A
        # RESULT — a routine, frequent operation — had to round-trip the
        # entire body through the caller to append one section, and a caller
        # that instead sent a fresh, partial body silently destroyed
        # everything else in it. TASK-1063 gave the result block its own
        # table (`workitem_result`); TASK-1064 (this field, `result`,
        # immediately below in `WorkitemPatch`) is what actually stops a
        # result-recording caller from ever touching `body` again — the
        # ROUTINE trigger for the historical loss is gone as of this task.
        #
        # What remains is a caller deliberately choosing to PATCH `body`
        # itself — editing the spec — which is an infrequent, deliberate act,
        # not a routine one. Adding an `expected_body` precondition (refuse
        # the write unless the caller's guess matches the current value)
        # would add friction to every such edit for a hazard that no longer
        # has a routine cause, and it would not even be the right tool for
        # the remaining risk: a precondition stops a LOST-UPDATE race, not a
        # wrong write, and it carries no history of what the write was. "Who
        # changed this spec, and when" is the job of the `graph_event` table,
        # which as of TASK-1045 EXISTS and records a `node.update` naming
        # `body` among its changed fields — a precondition on this field is
        # still not an audit trail and must not be sold as one. So:
        # replace-without-precondition stays. What `graph_event` does NOT give
        # anyone is the previous TEXT: it records that the spec changed, never
        # what it said before. Prose history remains a separate, unbuilt thing.
        # TASK-1045 — read BEFORE the setattr loop below overwrites them.
        previous_status = (n.attributes or {}).get("status")
        previous_assignee = n.assignee_user_id
        changed_fields: list[str] = []

        for field, value in updates.items():
            if field == "attributes" and value is None:
                continue  # attributes is NOT NULL; omit rather than corrupt the row
            setattr(n, field, value)
            changed_fields.append(field)

        # `assignee` is not in `updates` any more (popped and validated above),
        # so it is applied here — and only when the caller actually sent it.
        if assignee_provided:
            n.assignee_user_id = new_assignee_id

        if status_was_set or "attributes" in updates:
            attrs = _strip_depends_on_mirror(n.attributes)
            if status_was_set:
                if wanted_status is None:
                    attrs.pop("status", None)
                else:
                    attrs["status"] = wanted_status
            n.attributes = attrs

        n.origin = "atlas"

        try:
            s.flush()
        except ValueError as exc:
            s.rollback()
            return _attribute_validation_response(exc)

        if acceptance_provided:
            _replace_acceptance(s, workitem_id, acceptance or [])

        if result_provided:
            # Reads `n.body` AS IT STANDS at this point in the request — i.e.
            # AFTER the setattr loop above already applied a same-request
            # `body` update, if any — so a newly-created row's `position`
            # accounts for a body edit made in the SAME PATCH. Never writes
            # `n.body`; see `_replace_result`'s own docstring.
            _replace_result(s, workitem_id, result_value, n.body)

        # TASK-1045 — the edge churn this route does on the caller's behalf.
        # Collected, then recorded once after the flush that gives the new rows
        # their ids; deletes are snapshotted at the moment they are still
        # readable.
        added_edges: list[GraphEdge] = []
        removed_edges: list[dict] = []

        if parent_provided:
            existing_parent = _existing_parent_edge(s, workitem_id)
            if existing_parent is not None and (
                new_parent_node is None or existing_parent.src != new_parent_node.id
            ):
                removed_edges.append(_edge_snapshot(existing_parent))
                s.delete(existing_parent)
            if new_parent_node is not None and (
                existing_parent is None or existing_parent.src != new_parent_node.id
            ):
                e = GraphEdge(src=new_parent_node.id, dst=workitem_id,
                              edge_type=WORK_HIERARCHY_EDGE_TYPE, origin="atlas")
                s.add(e)
                added_edges.append(e)

        if binding_provided:
            if new_binding_node is None:
                # Explicit clear: remove EVERY existing works_on edge, not just
                # one — this is also the only way to unwind the pre-existing
                # TASK-850-style double binding through this API.
                for e in s.scalars(
                    select(GraphEdge).where(GraphEdge.src == workitem_id, GraphEdge.edge_type == "works_on")
                ).all():
                    removed_edges.append(_edge_snapshot(e))
                    s.delete(e)
            else:
                e = GraphEdge(src=workitem_id, dst=new_binding_node.id,
                              edge_type="works_on", origin="atlas")
                s.add(e)
                added_edges.append(e)

        if dep_provided:
            dep_added, dep_removed = _replace_depends_on(s, workitem_id, dep_ids)
            added_edges.extend(dep_added)
            removed_edges.extend(dep_removed)

        try:
            s.flush()
        except ValueError as exc:
            s.rollback()
            return _attribute_validation_response(exc)

        # TASK-1045. `changed_fields` is field NAMES only (see the same note on
        # `edit_node`), widened here with the sub-record flags this route owns
        # and the generic one does not — `acceptance`, `result`, `parent`,
        # `binding`, `depends_on` are not `node` columns, so they would
        # otherwise vanish from the record of an edit that was entirely about
        # them.
        touched = list(changed_fields)
        for flag, label in ((acceptance_provided, "acceptance"), (result_provided, "result"),
                            (parent_provided, "parent"), (binding_provided, "binding"),
                            (dep_provided, "depends_on")):
            if flag:
                touched.append(label)
        if touched:
            record_graph_event(s, "node.update", entity_type="node", entity_id=workitem_id,
                               detail={"fields": sorted(touched), "via": "workitem"})
        # `status_was_set` is the caller naming `status`, which is an assertion
        # and is recorded even when it re-asserts the current value (same rule
        # as `POST /api/node/{id}/status`). The second clause is the hole beside
        # it: `WorkitemPatch.attributes` carries a whole dict, so
        # `{"attributes": {"status": "done"}}` moved the lifecycle with no
        # status event at all. Compared on the stored value, so an attributes
        # PATCH that leaves the status alone stays silent.
        status_after = (n.attributes or {}).get("status")
        if status_was_set or status_after != previous_status:
            record_graph_event(s, "node.status", entity_type="node", entity_id=workitem_id,
                               detail={"from": previous_status, "to": status_after})
        if assignee_provided:
            record_graph_event(s, "node.assignee", entity_type="node", entity_id=workitem_id,
                               detail={"from": previous_assignee, "to": new_assignee_id})
        if approver is not None:
            # (a): the attestation itself. `actor_*` already say which credential
            # wrote; `agent` repeats its subject so the line reads on its own.
            # `old`/`new`, not `from`/`to`: those keys mean a scalar change to
            # every reader of this table.
            principal = auth.current_principal.get()
            record_graph_event(s, "node.acceptance.attested", entity_type="node", entity_id=workitem_id,
                               detail={"approver": {"id": approver.id, "username": approver.subject,
                                                    "name": approver.name},
                                       "quote": attested_approval["quote"].strip(),
                                       "agent": principal.subject if principal else None,
                                       "old": acceptance_before,
                                       "new": list(acceptance or [])})
        for e in added_edges:
            record_graph_event(s, "edge.create", entity_type="edge", entity_id=e.id,
                               node_id=e.src, related_node_id=e.dst,
                               detail={"src": e.src, "dst": e.dst, "edge_type": e.edge_type})
        for snap in removed_edges:
            record_graph_event(s, "edge.delete", entity_type="edge", entity_id=snap["id"],
                               node_id=snap["src"], related_node_id=snap["dst"],
                               detail={k: v for k, v in snap.items() if k != "id"})

        recompute_derived(s)
        s.refresh(n)
        payload = _workitem_payload(s, n)
        if was_git:
            payload["detached_from_git"] = True
            payload["note"] = (
                "this node was reconciled from a backlog file before this edit; editing it "
                "through the API detaches it from that file — future changes to the file will "
                "no longer apply to this node (TASK-1043)."
            )
        return JSONResponse(payload_fields.add_warnings(payload, ignored))


# --------------------------------------------------------------------------- #
# STORY-atlas-acceptance-human-approved (b) — acceptance PROPOSALS.
#
# Four routes, one table (`models_graph.AcceptanceProposal`):
#
#   GET  /api/workitem/{id}/acceptance-proposal              read
#   POST /api/workitem/{id}/acceptance-proposal              propose (write scope)
#   POST /api/workitem/{id}/acceptance-proposal/{pid}/approve   a SESSION only
#   POST /api/workitem/{id}/acceptance-proposal/{pid}/reject    a SESSION only
#
# * WORKS IN EVERY MODE. It changes nothing about who may WRITE acceptance:
#   storing a proposal writes no criterion, and the approve route applies the
#   list by calling `patch_workitem` as the approving session, so the rail
#   runs and the change is recorded exactly as if that person had edited it
#   on the board.
# * SESSION-ONLY DECISIONS. A token cannot approve or reject, whatever its
#   scope: an agent approving its own proposal would be the self-assertion this
#   whole story refuses. `guard_write` runs first (CSRF for the session), the
#   kind check second.
# * VISIBILITY IS THE ITEM'S. Every route resolves the item through
#   `_guard_node_access` before touching a proposal, so a proposal on an item
#   the caller cannot see is absent in exactly the way the item is: the same
#   404, and nothing countable.
# * ONE PENDING PROPOSAL PER ITEM. A new one marks the previous `superseded`
#   and its event names the one it replaced. Approve and reject name the
#   proposal id in the path, so a person always decides on the proposal they
#   were shown, never on one that replaced it while the page was open.
# --------------------------------------------------------------------------- #


class AcceptanceProposalWrite(BaseModel):
    acceptance: list[str]
    #: Why the agent proposes it. Shown beside the diff; optional.
    note: str | None = None
    csrf_token: str | None = None


class AcceptanceDecision(BaseModel):
    csrf_token: str | None = None


#: The longest proposal note accepted (same reasoning as APPROVAL_QUOTE_MAX).
PROPOSAL_NOTE_MAX = 4000


def _iso(v) -> str | None:
    return v.isoformat() if v is not None else None


def _proposal_json(p: models_graph.AcceptanceProposal) -> dict:
    return {
        "id": p.id, "node_id": p.node_id, "status": p.status,
        "proposed": list(p.proposed or []), "base": list(p.base or []), "note": p.note,
        "proposed_at": _iso(p.proposed_at),
        "proposed_by": {"id": p.proposed_by_user_id, "username": p.proposed_by_subject},
        "decided_at": _iso(p.decided_at),
        "decided_by": ({"id": p.decided_by_user_id, "username": p.decided_by_subject}
                       if p.decided_by_subject or p.decided_by_user_id else None),
    }


def _pending_proposal(s: Session, node_id: str) -> models_graph.AcceptanceProposal | None:
    """The item's one pending proposal. Newest first, so even a pair left by a
    race between two proposers resolves to the later one deterministically."""
    return s.scalars(
        select(models_graph.AcceptanceProposal)
        .where(models_graph.AcceptanceProposal.node_id == node_id,
               models_graph.AcceptanceProposal.status == "pending")
        .order_by(models_graph.AcceptanceProposal.id.desc())
    ).first()


def _workitem_or_refusal(s: Session, workitem_id: str, need: str | None = None):
    """`(node, None)` for a visible workitem, else `(None, response)`. Absent and
    hidden are the same 404, as on every other route. `need` (a write) adds the
    project-role check, which is a no-op with RBAC off (ADR 0041)."""
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
             "detail": f"node {workitem_id!r} is a {n.type!r}; acceptance belongs to "
                       f"{list(WORKITEM_TYPES)} only"},
            status_code=409)
    return n, None


def _session_only_refusal() -> JSONResponse | None:
    """403 unless the caller is a browser session. Fails closed on no principal,
    for `acceptance_rail_refusal`'s reason."""
    principal = auth.current_principal.get()
    if principal is not None and principal.kind == "session":
        return None
    return JSONResponse(
        {"error": "decision_needs_a_person",
         "detail": "Approving or rejecting an acceptance proposal is a person's decision, "
                   "made from a browser session on the board. A token cannot make it, "
                   "whatever its scopes.",
         "principal_kind": (principal.kind if principal else None)},
        status_code=403)


def _last_attested(s: Session, node_id: str) -> dict | None:
    """The newest `node.acceptance.attested` event on this item, for the board's
    "changed by <agent> on <person>'s approval" line. The caller has already
    passed `_guard_node_access` for `node_id`, which is the event's governing
    node, so this reads nothing the caller could not read from `/api/events`."""
    ev = s.scalars(
        select(models_graph.GraphEvent)
        .where(models_graph.GraphEvent.entity_type == "node",
               models_graph.GraphEvent.entity_id == node_id,
               models_graph.GraphEvent.action == "node.acceptance.attested")
        .order_by(models_graph.GraphEvent.id.desc())
    ).first()
    if ev is None:
        return None
    d = ev.detail or {}
    return {"at": _iso(ev.at), "agent": d.get("agent") or ev.actor_subject,
            "approver": d.get("approver"), "quote": d.get("quote"),
            "old": d.get("old"), "new": d.get("new")}


@router.get("/api/workitem/{workitem_id}/acceptance-proposal")
def get_acceptance_proposal(workitem_id: str) -> JSONResponse:
    """The item's current acceptance, its pending proposal (or null) and its
    latest attested change (or null): everything the board's Acceptance section
    needs beyond the node read."""
    with session() as s:
        n, refusal = _workitem_or_refusal(s, workitem_id)
        if refusal is not None:
            return refusal
        p = _pending_proposal(s, workitem_id)
        return JSONResponse({
            "node_id": workitem_id,
            "acceptance": _acceptance_list(s, workitem_id),
            "pending": _proposal_json(p) if p is not None else None,
            "last_attested": _last_attested(s, workitem_id),
        })


@router.post("/api/workitem/{workitem_id}/acceptance-proposal")
def propose_acceptance(workitem_id: str, body: AcceptanceProposalWrite, request: Request) -> JSONResponse:
    """Store a proposed acceptance list for a person to approve. Changes no
    criterion. Allowed to any principal that may write (`write` scope for a
    token), in every `ATLAS_ACCEPTANCE_AGENT_WRITE` mode."""
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    texts = [t.strip() for t in body.acceptance]
    if any(not t for t in texts):
        return JSONResponse(
            {"error": "invalid_acceptance", "field": "acceptance",
             "detail": "every proposed criterion must be non-empty text"},
            status_code=400)
    note = body.note.strip() if isinstance(body.note, str) and body.note.strip() else None
    if note is not None and len(note) > PROPOSAL_NOTE_MAX:
        return JSONResponse(
            {"error": "invalid_note", "field": "note",
             "detail": f"`note` is longer than {PROPOSAL_NOTE_MAX} characters"},
            status_code=400)

    with session() as s:
        n, refusal = _workitem_or_refusal(s, workitem_id, rbac.EDITOR)
        if refusal is not None:
            return refusal
        current = _acceptance_list(s, workitem_id)
        if texts == current:
            return JSONResponse(
                {"error": "nothing_to_propose", "field": "acceptance",
                 "detail": "the proposed list is the item's acceptance as it stands"},
                status_code=409)
        principal = auth.current_principal.get()
        now = models_graph._utcnow()
        old = _pending_proposal(s, workitem_id)
        if old is not None:
            old.status = "superseded"
            old.decided_at = now
            old.decided_by_user_id = principal.user_id if principal else None
            old.decided_by_subject = principal.subject if principal else None
        row = models_graph.AcceptanceProposal(
            node_id=workitem_id, status="pending", proposed=texts, base=current, note=note,
            proposed_at=now,
            proposed_by_user_id=principal.user_id if principal else None,
            proposed_by_subject=principal.subject if principal else None)
        s.add(row)
        s.flush()
        record_graph_event(s, "node.acceptance.propose", entity_type="node", entity_id=workitem_id,
                           detail={"proposal_id": row.id, "old": current, "new": texts,
                                   "note": note, "supersedes": old.id if old is not None else None})
        payload = _proposal_json(row)
        # COMMIT: a proposal changes no derived value, so `recompute_derived`
        # (the usual commit on this module's request paths) would be a lie about
        # what changed. Same call as the intent and node-doc routes.
        s.commit()
        return JSONResponse(payload, status_code=201)


def _decide(workitem_id: str, proposal_id: str, csrf_token: str | None, request: Request):
    """The shared front half of approve and reject: the write guard, the
    session-only rule, the item's visibility, and the proposal being the
    item's pending one. Returns `(proposal_json, None)` or `(None, response)`."""
    refused = auth.guard_write(request, csrf_token)
    if refused is not None:
        return None, refused
    refused = _session_only_refusal()
    if refused is not None:
        return None, refused
    with session() as s:
        n, refusal = _workitem_or_refusal(s, workitem_id, rbac.EDITOR)
        if refusal is not None:
            return None, refusal
        # A string in the path, parsed here: a non-numeric id is the same 404 as an
        # unknown one, and the scope check (in guard_write, above) runs before any
        # parsing, so a read-only token is refused on scope whatever it sends.
        pid = int(proposal_id) if proposal_id.isdigit() and len(proposal_id) <= 9 else None
        p = s.get(models_graph.AcceptanceProposal, pid) if pid is not None else None
        if p is None or p.node_id != workitem_id:
            return None, JSONResponse(
                {"error": "unknown_proposal",
                 "detail": f"no acceptance proposal {proposal_id} on {workitem_id!r}"},
                status_code=404)
        if p.status != "pending":
            return None, JSONResponse(
                {"error": "proposal_not_pending", "status": p.status,
                 "detail": f"proposal {proposal_id} is {p.status}, so there is nothing to decide"},
                status_code=409)
        out = _proposal_json(p)
        out["current"] = _acceptance_list(s, workitem_id)  # what an approve replaces
        return out, None


def _mark_decided(p: models_graph.AcceptanceProposal, status: str) -> None:
    """Close `p` with `status`, decided by the caller (a session: `_decide`
    refused everyone else). The event is recorded by each route itself, with a
    literal action, so `verify_graph_event.py` can read every call site."""
    principal = auth.current_principal.get()
    p.status = status
    p.decided_at = models_graph._utcnow()
    p.decided_by_user_id = principal.user_id if principal else None
    p.decided_by_subject = principal.subject if principal else None


@router.post("/api/workitem/{workitem_id}/acceptance-proposal/{proposal_id}/approve")
def approve_acceptance_proposal(workitem_id: str, proposal_id: str, body: AcceptanceDecision,
                                request: Request) -> JSONResponse:
    """Apply a pending proposal, as the approving person.

    THROUGH THE NORMAL SESSION PATH, literally: this calls `patch_workitem` with
    `{"acceptance": <proposed>}` under the caller's own session, so the rail,
    the visibility check, `_replace_acceptance`, the `node.update` event and
    `recompute_derived` are the ones a person editing acceptance on the board
    goes through. Only after that succeeds is the proposal marked approved and
    `node.acceptance.approve` recorded with the approver (the event's actor)."""
    prop, refusal = _decide(workitem_id, proposal_id, body.csrf_token, request)
    if refusal is not None:
        return refusal
    applied = patch_workitem(workitem_id,
                             WorkitemPatch(acceptance=prop["proposed"], csrf_token=body.csrf_token),
                             request)
    if applied.status_code != 200:
        return applied
    with session() as s:
        p = s.get(models_graph.AcceptanceProposal, prop["id"])
        _mark_decided(p, "approved")
        record_graph_event(s, "node.acceptance.approve", entity_type="node", entity_id=workitem_id,
                           detail={"proposal_id": p.id, "proposed_by": p.proposed_by_subject,
                                   "old": prop["current"], "new": prop["proposed"]})
        decided = _proposal_json(p)
        s.commit()
    return JSONResponse({"proposal": decided, "workitem": json.loads(bytes(applied.body))})


@router.post("/api/workitem/{workitem_id}/acceptance-proposal/{proposal_id}/reject")
def reject_acceptance_proposal(workitem_id: str, proposal_id: str, body: AcceptanceDecision,
                               request: Request) -> JSONResponse:
    """Discard a pending proposal. The item's acceptance is not touched."""
    prop, refusal = _decide(workitem_id, proposal_id, body.csrf_token, request)
    if refusal is not None:
        return refusal
    with session() as s:
        p = s.get(models_graph.AcceptanceProposal, prop["id"])
        _mark_decided(p, "rejected")
        record_graph_event(s, "node.acceptance.reject", entity_type="node", entity_id=workitem_id,
                           detail={"proposal_id": p.id, "proposed_by": p.proposed_by_subject,
                                   "new": prop["proposed"]})
        decided = _proposal_json(p)
        s.commit()
    return JSONResponse({"proposal": decided})


def _search_node_ids(s: Session, q: str) -> set[str]:
    """Node ids whose `body` (or `name`, cheap to include) OR whose authored
    `summary`/`abstract`/`detail` (TASK-1156) OR whose
    `acceptance_criterion.text` OR whose `workitem_result.text` OR whose
    `node_doc.content` OR one of its comments matches `q` — "what replaces `grep -r backlog/`"
    (TASK-1050 acceptance #3). `_search_hits` is the implementation.

    The third (`workitem_result`) source is TASK-1063: splitting `##
    Result`/`## Outcome` sections out of `node.body` into their own table
    would otherwise silently remove 221 workitems' result prose from search
    — this function existing as the sole entry point for "what does `q`
    match" is exactly why that removal could not have been caught by
    forgetting a call site elsewhere; there IS no elsewhere.

    The fourth (`node_doc`) source is STORY-atlas-search-node-docs: the
    content of a node's Docs tab — architecture documents, runbooks, the
    contributor guide — used to be reachable only by `GET
    /api/node/{id}/doc?path=...`, which requires already knowing the exact
    path. It matches ONLY a SERVED document (present in the owning node's
    `attributes.docs[]` allowlist, not marked external) — see
    `models_graph.NODE_DOC_SERVED_SQL`'s docstring for why an unlisted
    ("expected-not-served", TASK-1076) row must not surface here even though
    its content already exists in the table: the same "storing is not
    serving" boundary `app.node_doc` (the GET route) enforces applies to
    search, or the allowlist would be decorative.

    SCOPE, READ THIS BEFORE ADDING A FIFTH SOURCE OR A NEW CALLER: this
    function matches across EVERY node regardless of type or family — a
    world (object-family) node whose body or documentation matches is
    included in the returned set exactly like a workitem. Its two callers
    narrow that set differently, DELIBERATELY:
      - `list_workitems` (`GET /api/workitems?q=`) ANDs this against
        `NodeType.family == "workitem"` — that route lists/filters/searches
        the WORKITEM corpus specifically (its own docstring, its own name),
        so a matching world node is correctly absent from ITS results. This
        is unchanged by the node_doc addition.
      - `search_nodes` (`GET /api/search?q=`) applies NO family filter — it
        is the general surface, and it is where a world-node body match and
        a documentation-only match both actually surface. See that route's
        own docstring for the full reasoning.
    Do not add a family filter INSIDE this function — it would make the
    general surface impossible to build without a second, duplicate query.

    Real full-text (`to_tsvector('english', ...) @@ plainto_tsquery('english',
    :q)`), matching `models_graph.NODE_BODY_SEARCH_SQL` /
    `.AC_TEXT_SEARCH_SQL` / `.WORKITEM_RESULT_SEARCH_SQL` / `.NODE_DOC_SEARCH_SQL`
    byte-for-byte so the GIN indexes declared there
    (`models_graph.ensure_search_indexes`) cover this exact query.
    `plainto_tsquery` (not `websearch_to_tsquery` or a raw `to_tsquery`) is
    deliberate: it treats `q` as plain words to AND together and needs no
    escaping of user input, so a search string containing `to_tsquery`
    operator syntax (`&`, `|`, `:*`) can never be mistaken for a query
    operator or blow up the parse — "not a search product" (the task's own
    words), so the simplest safe operator wins over ranking features.
    """
    return set(_search_hits(s, q))


@router.get("/api/workitems")
def list_workitems(request: Request, type: str | None = None, status: str | None = None,
                    parent: str | None = None, binding: str | None = None,
                    owner: str | None = None, assignee: str | None = None,
                    q: str | None = None,
                    priority: str | None = None, label: str | None = None,
                    include_prose: int = 0, include_ancestors: int = 0, page: int = 1,
                    page_size: int = WORKITEM_LIST_DEFAULT_PAGE_SIZE) -> JSONResponse:
    """List/filter/search workitems WITHOUT downloading the whole graph.

    Query params (every one composes onto the next — see the module note
    above for why, and ADR 0028's reserved-but-unbuilt `?levels=`):
      type           — comma-separated node type(s), e.g. `task,story`.
                       Omitted = every type in the `workitem` node-type
                       FAMILY (read from the type registry, `node_type
                       .family`, never a hardcoded id list — "a type
                       vocabulary is defensible config; a node id is not").
      status         — comma-separated EFFECTIVE status value(s) (override-
                       or-authored, bucketed the same way a board click is —
                       `attention.status_bucket` — so `pending` and `planned`
                       match each other, same as everywhere else in this app).
      parent         — a node id; only items with an outbound `contains`
                       edge FROM this id (i.e. this id is their immediate
                       container).
      binding        — a node id; only items with an outbound `works_on` or
                       `serves` edge TO this id.
      owner          — exact match on `node.owner`.
      q              — full-text search over prose + acceptance — see
                       `_search_node_ids`.
      priority       — comma-separated priority value(s), any of
                       `WORKITEM_PRIORITIES` (STORY-oss-14); `none` matches an
                       item with no priority. An unknown value is a 400 naming
                       the field, never an empty page.
      label          — comma-separated label(s); an item carrying ANY of them
                       (case-insensitive, labels are stored lowercase).
      include_prose  — 0/1, default 0. See `_graph_node_json`'s note: a
                       listing omits the body-projection fallback and the raw
                       `body` field by default; authored abstract/detail are
                       included either way (they're small).
      page/page_size — 1-based; `page_size` is clamped to
                       `WORKITEM_LIST_MAX_PAGE_SIZE`.

    NodeVisibility is applied INSIDE the SQL query (`_visible_node_ids`), and
    `total` is computed AFTER every filter including it — a caller can never
    infer a hidden node's existence by comparing a filtered page against an
    unfiltered total (the same leak TASK-1026's acceptance criteria name for
    the sibling endpoint).
    """
    page = max(1, page)
    page_size = max(1, min(page_size, WORKITEM_LIST_MAX_PAGE_SIZE))

    # STORY-oss-14. Parsed before the session: a typo'd priority must say so
    # rather than read as "nothing has that priority".
    wanted_priorities: list[str] = []
    if priority:
        wanted_priorities = [p.strip().lower() for p in priority.split(",") if p.strip()]
        unknown = [p for p in wanted_priorities if p != "none" and p not in WORKITEM_PRIORITIES]
        if unknown:
            return JSONResponse(
                {"error": "invalid_priority", "field": "priority",
                 "detail": f"unknown priority {', '.join(unknown)}; one of "
                           f"{', '.join(WORKITEM_PRIORITIES)}, or none."},
                status_code=400)
    wanted_labels = sorted({" ".join(lb.split()).lower() for lb in (label or "").split(",")
                            if lb.strip()})

    with session() as s:
        clauses = [models_graph.NodeType.family == "workitem"]
        if wanted_priorities:
            named = [p for p in wanted_priorities if p != "none"]
            alts = [GraphNode.priority.in_(named)] if named else []
            if "none" in wanted_priorities:
                alts.append(GraphNode.priority.is_(None))
            clauses.append(or_(*alts))
        if wanted_labels:
            clauses.append(GraphNode.labels.overlap(wanted_labels))
        if type:
            wanted_types = [t.strip() for t in type.split(",") if t.strip()]
            if wanted_types:
                clauses.append(GraphNode.type.in_(wanted_types))
        if owner:
            clauses.append(GraphNode.owner == owner)
        if assignee:
            # TASK-1118. One more clause on the SAME list the access filter
            # below composes onto — which is what keeps "who may see this"
            # ahead of "whose is this", rather than narrowing an already-
            # narrowed set in the wrong order.
            #
            # `me` resolves against the calling principal, so the board never
            # has to put a user id in a URL to ask its own question. A caller
            # with no principal gets no rows rather than everyone's: the
            # ContextVar is None only where nothing authenticated, and
            # fail-closed is the house rule for exactly that case.
            if assignee == "me":
                pr = auth.current_principal.get()
                me_id = pr.user_id if pr is not None else None
                clauses.append(GraphNode.assignee_user_id == me_id if me_id else false())
            elif assignee == "none":
                clauses.append(GraphNode.assignee_user_id.is_(None))
            else:
                clauses.append(GraphNode.assignee_user_id == assignee)
        if parent:
            clauses.append(GraphNode.id.in_(
                select(GraphEdge.dst).where(GraphEdge.edge_type == "contains", GraphEdge.src == parent)
            ))
        if binding:
            clauses.append(GraphNode.id.in_(
                select(GraphEdge.src).where(GraphEdge.edge_type.in_(("works_on", "serves")),
                                             GraphEdge.dst == binding)
            ))
        visible_ids = _visible_node_ids(s)
        if visible_ids is not None:
            clauses.append(GraphNode.id.in_(visible_ids))
        if q:
            clauses.append(GraphNode.id.in_(_search_node_ids(s, q)))

        stmt = (
            select(GraphNode)
            .join(models_graph.NodeType, GraphNode.type == models_graph.NodeType.id)
            .where(and_(*clauses))
        )
        candidates = list(s.scalars(stmt).all())

        # `status` is the one filter that cannot be a single SQL predicate —
        # the EFFECTIVE value is override-JOIN-authored-JOIN-normalise-and-
        # bucket (attention.status_map), not a column. Applied here, over the
        # candidate set every OTHER filter (including visibility) has
        # already bounded — never over the whole table — so `total` below is
        # still "after every filter", not "after some of them".
        if status:
            from attention import _norm as norm_status, status_bucket, status_map
            wanted_buckets = {status_bucket(norm_status(v)) for v in status.split(",") if v.strip()}
            nodes_by_id = {n.id: n for n in candidates}
            smap = status_map(s, nodes_by_id)
            candidates = [n for n in candidates if status_bucket(smap.get(n.id)) in wanted_buckets]

        candidates.sort(key=lambda n: (n.type, n.id))
        total = len(candidates)
        start = (page - 1) * page_size
        page_items = candidates[start:start + page_size]

        assignees = _assignee_map(s)
        items = [_graph_node_json(n, None, None, include_prose=bool(include_prose),
                                  assignees=assignees)
                 for n in page_items]

        if include_ancestors:
            # The FULL `contains` chain per row, root-first — not one hop.
            #
            # Normally a listing omits parenthood on purpose: it is a listing,
            # not a detail view. But a consumer that nests the rows (the Mine
            # view, TASK-1124) otherwise calls `/api/node/{id}` once PER ROW,
            # which at 372 assigned items is 372 requests to draw some headers.
            # One edge scan here instead, walked in Python.
            #
            # ONE hop was tried first and was wrong for the product, not just
            # for cost: a task showed its story but not the epic above it, so
            # the view could only group by type rather than nest properly.
            edges = s.execute(
                select(models_graph.Edge.src, models_graph.Edge.dst)
                .where(models_graph.Edge.edge_type == "contains")
            ).all()
            # A DAG: a node can have several containers. Take the lowest id so
            # the chain is stable between requests rather than whatever the
            # scan happened to yield first.
            parent_of: dict[str, str] = {}
            for src, dst in edges:
                cur = parent_of.get(dst)
                if cur is None or src < cur:
                    parent_of[dst] = src

            need: set[str] = set()
            chains: dict[str, list[str]] = {}
            for item in items:
                chain, cursor, guard = [], item["id"], 0
                # Cycle-guarded: `contains` is a DAG in principle and a cycle
                # in practice would hang this loop rather than erroring.
                while guard < 32:
                    nxt = parent_of.get(cursor)
                    if nxt is None or nxt in chain or nxt == item["id"]:
                        break
                    chain.append(nxt)
                    cursor = nxt
                    guard += 1
                chain.reverse()          # root-first
                chains[item["id"]] = chain
                need.update(chain)

            # Resolve the ancestor rows through the SAME access filter, so an
            # ancestor the caller may not see never leaks in as a header.
            names: dict[str, GraphNode] = {}
            if need:
                # NOT `q` — that is this route's search parameter, and the
                # response echoes it back in `filters`. Shadowing it put a
                # SQLAlchemy Select into the JSON body and 500'd the route.
                anc_stmt = select(GraphNode).where(GraphNode.id.in_(need))
                if visible_ids is not None:
                    anc_stmt = anc_stmt.where(GraphNode.id.in_(visible_ids))
                for n in s.scalars(anc_stmt).all():
                    names[n.id] = n
            for item in items:
                item["ancestors"] = [
                    {"id": a, "type": names[a].type, "name": names[a].name}
                    for a in chains[item["id"]] if a in names
                ]

        # TASK-1382: the items on the page, then any ancestor named in their headers.
        # Lazy, so with ATLAS_AUDIT_READS off nothing is built.
        audit_log.record_read(request, "GET /api/workitems", itertools.chain(
            (i["id"] for i in items),
            (a["id"] for i in items for a in i.get("ancestors", ()))))
        return JSONResponse({
            "total": total, "count": len(items), "page": page, "page_size": page_size,
            "filters": {"type": type, "status": status, "parent": parent,
                        "binding": binding, "owner": owner, "assignee": assignee,
                        "q": q, "priority": priority, "label": label},
            "max_page_size": WORKITEM_LIST_MAX_PAGE_SIZE,
            "items": items,
        })
