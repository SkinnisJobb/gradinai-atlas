"""The Atlas work verbs — `atlas_file_work` (TASK-1088) and `atlas_update_work`
(TASK-1089), `EPIC-atlas-mcp`.

Both are thin wrappers over `POST /api/workitem` and `PATCH /api/workitem/{id}`
(backend/app.py) — those routes already do the transactional, atomic, one-call
create/update `.claude/commands/file-task.md` used to talk a human through by
hand. This module's job is narrower than "expose the API": it encodes the
conventions that file had to spell out in prose (a task id is minted, an
epic/story id is not; `status` has a normalised vocabulary and `proposed` is
not in it) as LOCAL refusals that fire before any network call, and it adds
one safety rail the raw API does not have — the four wholesale-replace fields
on `atlas_update_work` each require an explicit `set_<field>=True` flag, so a
value provided without its flag is refused rather than silently ignored or
silently applied.

ACCEPTANCE (STORY-atlas-acceptance-human-approved). atlas-api refuses an
agent's change to an existing item's acceptance. `atlas_update_work` offers the
two ways past that, rather than a new tool, so the gateway's tool allow-list
needs no change and an agent finds them where it already changes acceptance:
`approval={"by", "quote"}` with `set_acceptance=True` (honoured only where the
install sets ATLAS_ACCEPTANCE_AGENT_WRITE=attested), and
`propose_acceptance=True`, which stores the list for a person to approve on the
board and works everywhere.

No `from __future__ import annotations` here — this module defines tool
functions and FastMCP inspects their real signatures to build a JSON-Schema
(see mcp_server.py's own docstring).

Registration: `register(mcp)` is called once from the bottom of mcp_server.py.
It declares the tools with `@mcp.tool()` itself, inside the function, rather
than the module importing `mcp` and decorating at import time — the shared
`mcp` instance lives in mcp_server.py and three agents are touching
`mcp/app/` in parallel this round, so every tool module takes an `mcp`
argument instead of importing the singleton, and mcp_server.py stays
untouched apart from one `register(mcp)` call at the bottom.
"""

import re
from typing import Any

from .client import client
from .envelope import AtlasError, envelope

# --------------------------------------------------------------------------- #
# Local copies of backend vocabularies. They are genuinely owned by
# backend/attention.py and backend/deps.py — importing them directly would
# mean importing `app.py`, which drags in the fail-closed pepper/DB-URL
# startup chain (see backend/models_graph.py's own comment on why
# `import_workitems.py` forks small helpers rather than doing that). A local
# constant with a comment pointing at the source; don't let them drift.
#
# THE WORKITEM TYPES ARE NOT COPIED HERE (TASK-1390). They are read from
# `GET /api/vocabulary` (`workitem_types`, and `workitem_ladder` for the
# hierarchy rungs, TASK-1389) once per process by `_workitem_vocabulary`
# below, so a type added in the backend needs no edit in this module.
# --------------------------------------------------------------------------- #

#: backend/attention.py (`SETTABLE_STATUSES`). `"proposed"` is a real,
#: readable status (the board shows it) but is deliberately NOT in this
#: tuple — both `POST` and `PATCH /api/workitem` 400 `unknown_status` for it.
SETTABLE_STATUSES = ("pending", "analyze", "in_progress", "blocked", "done", "cancelled")

#: backend/deps.py (`WORKITEM_PRIORITIES`), lowest first (STORY-oss-14). Only
#: checked locally so a typo is refused before the network call with the list in
#: the message; atlas-api validates it again and owns the labels rules.
WORKITEM_PRIORITIES = ("low", "medium", "high", "critical")

#: Sentinel default for `atlas_update_work`'s `result` parameter — the ONE
#: field in that tool that must distinguish "omitted" (leave the
#: `workitem_result` row alone) from "explicitly `null`" (delete it) WITHOUT
#: a companion `set_result` flag, per this task's own "the safe path (status,
#: result) takes no flags" requirement. Verified empirically against the
#: real FastMCP/pydantic stack (mcp==1.27.2, pydantic==2.13.4) before relying
#: on it: a non-JSON-serializable object as a parameter default produces a
#: `PydanticJsonSchemaWarning` (schema omits the field's "default" key
#: entirely, which is what we want — the field still reads as optional,
#: `anyOf: [string, null]`, in the generated inputSchema) and NOT an error,
#: and FastMCP's argument model correctly round-trips: an omitted `result`
#: arrives here as this exact object, an explicit `"result": null` arrives as
#: Python `None`, and an explicit string arrives as that string. The other
#: four wholesale fields don't need this trick — they get real `set_<x>`
#: flags instead, which is a plainer mechanism and doubles as this tool's
#: "deliberate act" safety rail (see `_require_flag`).
_UNSET: Any = object()


#: The process's one read of the workitem vocabulary, `(types, ladder)`, or
#: None until the first successful read. A failed read is not cached, so the
#: next call tries again.
_vocabulary: tuple[tuple[str, ...], tuple[str, ...]] | None = None


def _workitem_vocabulary() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(workitem_types, workitem_ladder)` as `GET /api/vocabulary` states
    them, fetched on first use and cached for the life of the process.

    FAILS CLOSED. If the API cannot answer, the call that needed the list is
    refused with `vocabulary_unavailable` rather than checked against a guess:
    a built-in fallback list would be exactly the second copy this replaced.
    The write it guards goes to the same API, so it could not have succeeded
    anyway.

    One documented fallback: an atlas-api older than TASK-1389 sends no
    `workitem_ladder`. Every workitem type was a rung then, so the ladder is
    read as `workitem_types`.
    """
    global _vocabulary
    if _vocabulary is None:
        try:
            vocab = client.get("/api/vocabulary", params={"schemas": "none"})
        except AtlasError as e:
            raise AtlasError(
                "vocabulary_unavailable",
                "the workitem types are read from GET /api/vocabulary, which did "
                f"not answer ({e.error_type}: {e}). Nothing was sent; try again.",
            ) from e
        types = (vocab or {}).get("workitem_types")
        if not isinstance(types, list) or not types:
            raise AtlasError(
                "vocabulary_unavailable",
                "GET /api/vocabulary returned no `workitem_types` list. Nothing was sent.",
            )
        ladder = vocab.get("workitem_ladder")
        if not isinstance(ladder, list):
            ladder = types
        _vocabulary = (tuple(types), tuple(ladder))
    return _vocabulary


def _provided(value: Any) -> bool:
    """True if `value` is not the `_UNSET` sentinel, i.e. the caller actually
    included this argument in the tool call (even as `null`)."""
    return value is not _UNSET


# --------------------------------------------------------------------------- #
# `## Result` / `## Outcome` detection for atlas_file_work's body refusal.
#
# Deliberately duplicated from backend/models_graph.py's `H2`/`RES`/`FENCE`
# rather than imported — same reasoning as the
# SETTABLE_STATUSES comment above: models_graph.py itself has no import chain
# that would drag in app.py's fail-closed startup (it only imports
# sqlalchemy/jsonschema), so importing it would WORK, but mcp/app has no
# dependency on backend/ today and this task must not create one just to
# reuse three short regexes. Keep this in sync with models_graph.py's `RES`
# pattern (`^##\s+(Result|Outcome|Results)\b`, case-insensitive) and its
# fence-aware skip if that ever changes.
# --------------------------------------------------------------------------- #
_H2 = re.compile(r"^##\s")
_RESULT_HEADING = re.compile(r"^##\s+(Result|Outcome|Results)\b", re.I)
_FENCE = re.compile(r"^\s*(```|~~~)")


def _body_has_result_section(body: str) -> bool:
    """True if `body` carries a top-level `## Result`/`## Outcome`/`## Results`
    heading outside any fenced code block — the exact condition
    backend/models_graph.py's `split_result` uses to find the section it
    would move into `workitem_result`. A body that already has one is
    refused by `atlas_file_work` before it ever reaches the API: the result
    belongs in its own store, recorded later through `atlas_update_work`'s
    `result` field, never authored into the spec up front."""
    in_fence = False
    for line in body.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence and _H2.match(line) and _RESULT_HEADING.match(line):
            return True
    return False


def _validate_priority(priority: str | None) -> None:
    """Refuse a priority `POST`/`PATCH /api/workitem` would 400 on. None (unset,
    or clear on update) is always fine."""
    if priority is not None and priority.strip().lower() not in WORKITEM_PRIORITIES:
        raise AtlasError(
            "invalid_priority",
            f"{priority!r} is not a priority: one of {', '.join(WORKITEM_PRIORITIES)} "
            "(lowest first), or null.",
        )


def _validate_status(status: str) -> None:
    """Refuse a status that `POST`/`PATCH /api/workitem` would 400 on, before
    the network call — `"proposed"` gets its own message because it is the
    one a caller is likeliest to reach for: it is readable on the board but
    not one of the settable values."""
    if status == "proposed":
        raise AtlasError(
            "unknown_status",
            "'proposed' is shown on the board but is not one of the settable "
            "statuses (POST/PATCH /api/workitem 400s on it). The six "
            f"settable statuses are: {', '.join(SETTABLE_STATUSES)}.",
        )
    if status not in SETTABLE_STATUSES:
        raise AtlasError(
            "unknown_status",
            f"{status!r} is not one of the settable statuses: "
            f"{', '.join(SETTABLE_STATUSES)}.",
        )


# --------------------------------------------------------------------------- #
# TASK-1088 — atlas_file_work
# --------------------------------------------------------------------------- #

def _file_work_impl(
    type: str,
    name: str,
    id: str | None,
    parent: str | None,
    binding: str | None,
    body: str | None,
    acceptance: list[str] | None,
    depends_on: list[str] | None,
    status: str,
    priority: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    workitem_types, _ = _workitem_vocabulary()
    if type not in workitem_types:
        raise AtlasError(
            "invalid_type",
            f"{type!r} is not one of {list(workitem_types)} "
            "(the workitem types GET /api/vocabulary lists).",
        )

    if type == "task":
        if id is not None:
            raise AtlasError(
                "id_is_minted",
                "a task id is minted server-side from a Postgres sequence "
                "(models_graph.allocate_task_id) — do not supply one; leave "
                "`id` unset and read the minted id off this call's response.",
            )
    else:
        if not id:
            raise AtlasError(
                "id_required",
                f"{type} ids are caller-supplied slugs — there is no minter "
                f"for {type}s (only `task` ids are minted). Pass e.g. "
                f"id='EPIC-some-slug' or id='STORY-some-slug'.",
            )

    _validate_status(status)
    _validate_priority(priority)

    if body and _body_has_result_section(body):
        raise AtlasError(
            "result_in_body",
            "body contains a '## Result'/'## Outcome'/'## Results' heading — "
            "the outcome has its own store (workitem_result, TASK-1063/1064), "
            "never the spec body. Remove the section here; record the "
            "outcome later, once there is one, through atlas_update_work's "
            "`result` argument.",
        )

    # NO `owner` KEY (TASK-1120). It used to be sent unconditionally, `None`
    # included — and atlas-api now answers a workitem payload carrying that key
    # with a 400 `owner_retired`, on PRESENCE rather than on value, so a
    # serialized `"owner": null` would have failed every single call. A
    # workitem's assignee is an account (`assignee`, an auth_user.id) and this
    # tool still cannot set one; see this module's note and mcp/README.md.
    payload = {
        "type": type,
        "name": name,
        "body": body,
        "acceptance": acceptance or [],
        "depends_on": depends_on or [],
        "status": status,
    }
    if id is not None:
        payload["id"] = id
    if parent is not None:
        payload["parent"] = parent
    if binding is not None:
        payload["binding"] = binding
    # STORY-oss-14. Sent only when given, so a call without them is the payload
    # it always was.
    if priority is not None:
        payload["priority"] = priority
    if labels is not None:
        payload["labels"] = labels

    return client.post("/api/workitem", json=payload)


# --------------------------------------------------------------------------- #
# TASK-1089 — atlas_update_work
#
# WHY THE FOUR set_<field> FLAGS EXIST. `body`, `acceptance`, `depends_on` and
# `binding` are all wholesale replace on atlas-api's PATCH — no precondition,
# no diff, no partial update. Before this rail existed, a call meaning only to
# record a result sent a partial `body` and destroyed roughly 7,900 characters
# of spec across TASK-1051/1053/1055 in August 2026; the markdown that would
# have recovered it was already deleted by then. The flag turns "value present
# without its flag" into a local refusal instead of a silent wholesale write.
# --------------------------------------------------------------------------- #

def _require_flag(field_name: str, value: Any, flag: bool) -> None:
    """The safety rail this tool adds beyond what the raw API has: supplying
    a wholesale-replace field's VALUE without also setting its `set_<field>`
    flag to True is refused locally, before any network call, rather than
    silently applied (dangerous) or silently ignored (confusing — the
    caller would have no idea their value never took effect). `value is
    None` alone is not evidence of intent (it is also every field's
    untouched default), so only a non-None value without its flag trips
    this."""
    if value is not None and not flag:
        raise AtlasError(
            "unflagged_wholesale_field",
            f"{field_name} was provided but set_{field_name}=true was "
            f"not — passing {field_name} without its flag is refused "
            f"locally so a wholesale replace can never happen by "
            f"accident. Pass set_{field_name}=true to confirm.",
        )


def _update_work_impl(
    id: str,
    status: str | None,
    result: Any,  # a real value, `None` (explicit clear), or `_UNSET` (omitted)
    body: str | None,
    set_body: bool,
    acceptance: list[str] | None,
    set_acceptance: bool,
    depends_on: list[str] | None,
    set_depends_on: bool,
    binding: str | None,
    set_binding: bool,
    approval: dict | None = None,
    propose_acceptance: bool = False,
    proposal_note: str | None = None,
    priority: Any = _UNSET,  # STORY-oss-14: a value, `None` (clear), or `_UNSET` (omitted)
    labels: Any = _UNSET,    # a list REPLACES the set; `None` or [] clears; `_UNSET` leaves it
    parent: Any = _UNSET,    # TASK-1355: an id moves it there; `None` detaches; `_UNSET` leaves it
) -> dict:
    _require_flag("body", body, set_body)
    if propose_acceptance:
        # STORY-atlas-acceptance-human-approved (b). Proposing replaces nothing,
        # so it needs no set_acceptance; it is its own deliberate flag instead.
        if set_acceptance or approval is not None:
            raise AtlasError(
                "conflicting_acceptance_mode",
                "propose_acceptance=true stores the list for a person to approve on the "
                "board; set_acceptance=true (with or without approval) writes it now. "
                "Pick one.",
            )
        if acceptance is None:
            raise AtlasError(
                "nothing_to_propose",
                "propose_acceptance=true needs `acceptance`, the full list you propose.",
            )
    else:
        _require_flag("acceptance", acceptance, set_acceptance)
        if proposal_note is not None:
            raise AtlasError(
                "note_without_proposal",
                "proposal_note is shown beside a proposal; pass propose_acceptance=true.",
            )
    if approval is not None and not set_acceptance:
        raise AtlasError(
            "approval_without_acceptance",
            "approval attests a change to acceptance; pass it with acceptance and "
            "set_acceptance=true.",
        )
    _require_flag("depends_on", depends_on, set_depends_on)
    _require_flag("binding", binding, set_binding)

    if status is not None:
        _validate_status(status)
    if _provided(priority):
        _validate_priority(priority)

    result_touched = _provided(result)
    parent_touched = _provided(parent)
    touching_four = (set_body or set_acceptance or set_depends_on or set_binding
                     or propose_acceptance)

    # Read-before-write: a before-image of every field this call might
    # change, fetched in ONE call before any PATCH goes out. Not an
    # audit trail (atlas-api records that itself, in graph_event via
    # deps.record_graph_event; nothing here is stored) — purely
    # something this session can restore from if the PATCH turns out
    # to be a mistake.
    before: dict | None = None
    if touching_four or result_touched or parent_touched:
        node = client.get("/api/node/" + id)
        # A work parent is a rung of the hierarchy ladder (TASK-1389/1390).
        _, ladder = _workitem_vocabulary()
        before = {
            "body": node.get("body"),
            "acceptance": node.get("acceptance"),
            "depends_on": sorted(
                d["id"] for d in (node.get("dependencies") or [])
            ),
            "binding": sorted(
                b["id"] for b in (node.get("bindings") or [])
                if b.get("edge_type") == "works_on"
            ),
            "result": node.get("result"),
            # TASK-1355: the work parent(s) it had, so a wrong move can be put back.
            "parent": sorted(
                p["id"] for p in (node.get("parents") or [])
                if p.get("edge_type") == "contains" and p.get("type") in ladder
            ),
        }

    patch: dict = {}
    if status is not None:
        patch["status"] = status
    if result_touched:
        # atlas-api deletes the workitem_result row for `result: null`
        # and leaves it alone if `result` is omitted entirely — the one
        # field here that keeps that distinction without a flag.
        patch["result"] = None if result is _UNSET else result
    if set_body:
        patch["body"] = body
    if set_acceptance:
        # atlas-api's PATCH treats an explicit `"acceptance": null` and
        # `"acceptance": []` the same way — both clear the list (backend/
        # routers/workitems.py's patch_workitem checks presence before
        # popping the key, since TASK-1101). This tool always sends a concrete list rather
        # than a bare `null` regardless, so the wire value is unambiguous
        # whichever atlas-api version is on the other end.
        patch["acceptance"] = acceptance if acceptance is not None else []
        # STORY-atlas-acceptance-human-approved (a). Sent only when given;
        # atlas-api reads it only under ATLAS_ACCEPTANCE_AGENT_WRITE=attested,
        # and otherwise refuses the acceptance change exactly as before.
        if approval is not None:
            patch["approval"] = approval
    # STORY-oss-14. Like `result`, these two keep "omitted" apart from "null"
    # with the sentinel rather than a flag: neither is prose that a partial
    # value could destroy, and `before` does not carry them because the PATCH
    # response (`after`) and `atlas_get` both do.
    if _provided(priority):
        patch["priority"] = priority
    if _provided(labels):
        patch["labels"] = labels if labels is not None else []
    # TASK-1355 — reparent. Passed through as is: atlas-api's PATCH owns every
    # rule (the parent must exist and be a workitem, sit above the item in the
    # epic > story > task order, not be the item or below it (TASK-1378), and
    # the caller needs the role on it) and its refusal comes back unchanged. `null` detaches the item.
    if parent_touched:
        patch["parent"] = parent
    if set_depends_on:
        patch["depends_on"] = depends_on if depends_on is not None else []
    binding_edges_removed = None
    if set_binding:
        patch["binding"] = binding
        if binding is None and before is not None:
            binding_edges_removed = len(before["binding"])

    if not patch and not propose_acceptance:
        raise AtlasError(
            "nothing_to_update",
            "no field was provided — status, result, priority, labels, parent, or one "
            "of the four set_<field>=true wholesale fields must be given.",
        )

    out: dict = {"id": id}
    if patch:
        out["after"] = client.patch("/api/workitem/" + id, json=patch)
    if propose_acceptance:
        proposal: dict = {"acceptance": acceptance}
        if proposal_note is not None:
            proposal["note"] = proposal_note
        out["proposal"] = client.post(
            "/api/workitem/" + id + "/acceptance-proposal", json=proposal)
    if before is not None:
        out["before"] = before
    if binding_edges_removed is not None:
        out["binding_edges_removed"] = binding_edges_removed
    return out


def register(mcp) -> None:
    """Declare this module's tools on the shared FastMCP instance. Called
    once from the bottom of mcp_server.py — see this module's own docstring
    for why registration is a function rather than a decorator at import
    time."""

    @mcp.tool()
    def atlas_file_work(
        type: str,
        name: str,
        id: str | None = None,
        parent: str | None = None,
        binding: str | None = None,
        body: str | None = None,
        acceptance: list[str] | None = None,
        depends_on: list[str] | None = None,
        status: str = "pending",
        priority: str | None = None,
        labels: list[str] | None = None,
    ) -> dict:
        return envelope(_file_work_impl)(
            type=type, name=name, id=id, parent=parent, binding=binding,
            body=body, acceptance=acceptance, depends_on=depends_on,
            status=status, priority=priority, labels=labels,
        )

    # ----------------------------------------------------------------- #
    # TASK-1089 — atlas_update_work
    # ----------------------------------------------------------------- #

    @mcp.tool()
    def atlas_update_work(
        id: str,
        status: str | None = None,
        result: str | None = _UNSET,
        body: str | None = None,
        set_body: bool = False,
        acceptance: list[str] | None = None,
        set_acceptance: bool = False,
        depends_on: list[str] | None = None,
        set_depends_on: bool = False,
        binding: str | None = None,
        set_binding: bool = False,
        approval: dict | None = None,
        propose_acceptance: bool = False,
        proposal_note: str | None = None,
        priority: str | None = _UNSET,
        labels: list[str] | None = _UNSET,
        parent: str | None = _UNSET,
    ) -> dict:
        return envelope(_update_work_impl)(
            id=id, status=status, result=result, body=body, set_body=set_body,
            acceptance=acceptance, set_acceptance=set_acceptance,
            depends_on=depends_on, set_depends_on=set_depends_on,
            binding=binding, set_binding=set_binding,
            approval=approval, propose_acceptance=propose_acceptance,
            proposal_note=proposal_note, priority=priority, labels=labels,
            parent=parent,
        )
