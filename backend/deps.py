"""Helpers shared by more than one router: access filtering, the graph-event writer, the
node/edge JSON shapes, prose tiers, and the validation helpers the write routes share.

Split out of `app.py` (STORY-oss-04); the code is moved, not changed. A helper used by ONE
router lives in that router's module instead.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

import models_graph  # Atlas v2 object-graph schema (ADR 0019); coexists with Component below
import node_types    # the node_type seeds: workitem types and ladder (TASK-1389)
import models_auth   # auth_* + node_visibility, own Bases (ADR 0025/0026, TASK-966/973)
import auth         # THE GATE (TASK-967) + service tokens (TASK-968)
import rbac         # project RBAC (ADR 0041); inert unless ATLAS_RBAC=projects
import settings
from models_graph import Edge as GraphEdge, Node as GraphNode

FRONTEND = settings.load_frontend_dir()  # ATLAS_FRONTEND_DIR; the rule is in settings.py (TASK-1433)


_EMPTY_WORK_LOAD = {"count": 0, "done": 0, "completion": 0.0, "workitem_ids": []}

_LEADING_H1 = re.compile(r"^#[ \t]+.*(?:\r?\n|$)")

# Upper bound on a PROJECTED abstract, in characters. ADR 0020 sizes the
# abstract tier at ~250 words; 1200 chars is that order of magnitude and is
# well above the ~240-char first paragraph the real backlog averages, so it
# never truncates authored prose — it exists to stop an unparagraphed body
# projecting itself wholesale into every listing. See
# `_project_prose_from_body`.
_ABSTRACT_MAX_CHARS = 1200


def _project_prose_from_body(body: str | None) -> tuple[str | None, str | None]:
    """The presentation projection (TASK-1046): split a verbatim `node.body`
    into an (abstract, detail) pair for a node that has PROSE but no AUTHORED
    ADR-0020 tiers — the read-side half of "filling `body` alone changes
    nothing on screen" (see `_graph_node_json` below, which only calls this
    when `n.abstract` is falsy: authored always wins, unconditionally, over
    projected — see that call site).

    `abstract` is the body's first paragraph (text up to the first blank
    line); `detail` is everything after it. Both stripped. Returns
    `(None, None)` for an empty/whitespace-only body — "nothing to project",
    the same honest-null convention every other unauthored-prose column in
    this schema already follows, rather than surfacing an empty string that
    would merely LOOK unauthored.

    One deliberate deviation from "just split the text": a single leading
    Markdown H1 (`# Some Title`) is stripped before splitting. `node.name` is
    already sourced from that exact same heading elsewhere (the backlog
    frontmatter parser's title extraction), so using it AGAIN as the
    "description" would show the task's own name back to the reader instead
    of anything new — the entire reason this projection exists is to surface
    the first thing about the body that node.name does NOT already say.
    """
    if not body or not body.strip():
        return None, None
    text = _LEADING_H1.sub("", body.strip(), count=1).strip()
    if not text:
        return None, None
    # THE ABSTRACT MUST BE A SENTENCE, NOT A SECTION LABEL. Splitting on the
    # first blank line is only right if the first block is prose. Measured on
    # the imported corpus: 38 of 306 bodies open straight into a `##` after the
    # title, so "first paragraph" yielded descriptions like "## What comes out"
    # — a heading where the reader expects an explanation, on 12% of the board.
    #
    # So skip any leading blocks that are ONLY heading lines and take the first
    # block containing real prose. Nothing is discarded: the skipped headings
    # stay in `detail`, in their original order, with the chosen paragraph
    # lifted out of the sequence.
    blocks = re.split(r"\n[ \t]*\n", text)
    is_heading_only = lambda b: all(  # noqa: E731 — a one-line predicate, used twice
        not ln.strip() or ln.lstrip().startswith("#") for ln in b.splitlines())
    idx = next((i for i, b in enumerate(blocks)
                if b.strip() and not is_heading_only(b)), None)
    if idx is None:
        # Every block is a heading — no prose to promote. Fall back to the old
        # behaviour rather than inventing something.
        idx = 0

    # The chosen block may still LEAD with heading lines, when a heading is
    # followed by prose with no blank line between them — `## Goal\nOne binding
    # per workitem.` is the corpus's most common shape and accounted for the
    # last 14 heading-led descriptions. Peel those lines off the front; they
    # go back into `detail` in place, so the text is moved, never dropped.
    lines = blocks[idx].splitlines()
    lead: list[str] = []
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("#")):
        lead.append(lines.pop(0))
    stripped = "\n".join(lines).strip()
    if stripped:
        abstract = stripped
        lead_text = "\n".join(lead).strip()
    else:
        # Peeling left nothing — the block was heading-only after all (the
        # all-headings fallback). Keep it whole as the abstract and DO NOT also
        # put it back into detail, or it shows up twice.
        abstract = blocks[idx].strip() or None
        lead_text = ""
    rest = []
    for i, b in enumerate(blocks):
        if i == idx:
            if lead_text:
                rest.append(lead_text)
        elif b.strip():
            rest.append(b)
    detail = "\n\n".join(rest).strip() or None

    # THE ABSTRACT MUST BE BOUNDED BY CONSTRUCTION, not by trusting the body to
    # contain a blank line. "First paragraph" is a bound only for prose that is
    # paragraphed: a body written as one unbroken block projects ENTIRELY into
    # the abstract, and the abstract is the tier that ships in every listing
    # (see `_graph_node_json`) — so one such node would put its whole text into
    # every full-tree read, which is exactly the payload rule this projection
    # is supposed to respect. Measured on the real corpus the first paragraph
    # averages ~240 chars, so this cap is inert for authored prose and only
    # fires on the pathological shape.
    #
    # Overflow is not discarded — it is prepended to `detail`, which is gated
    # and carries everything else. `node.body` remains the verbatim source
    # either way; abstract/detail are presentation, not storage.
    if abstract and len(abstract) > _ABSTRACT_MAX_CHARS:
        cut = abstract.rfind(" ", 0, _ABSTRACT_MAX_CHARS)
        if cut < _ABSTRACT_MAX_CHARS // 2:  # no sensible word break — hard cut
            cut = _ABSTRACT_MAX_CHARS
        overflow = abstract[cut:].strip()
        abstract = abstract[:cut].rstrip() + "…"
        detail = f"{overflow}\n\n{detail}" if detail else (overflow or None)
    return abstract, detail


def _assignee_map(s: Session) -> dict[str, dict]:
    """`{auth_user.id: {id, username, name}}` for every account, in one query.

    Precomputed per request and handed to `_graph_node_json`, the same shape as
    `counts`/`loads`/`intent_counts` above — for the same reason: resolving an
    assignee per node would be one round trip per node on a full tree read.

    EVERY account, not just the assignable ones. This is a RESOLVER, not the
    picker's list — `/api/people` decides who may be assigned (humans, active)
    and is deliberately narrower. A node assigned before an account was
    disabled must still render that person's name rather than going blank, or
    disabling somebody would silently orphan their work on screen.

    It carries no email, no role and no grants — `{id, username, name}` is the
    whole shape, and it is the same shape `/api/people` returns so the board
    reads one vocabulary. Widening it here would leak through every node
    payload at once, which is a much larger surface than the one endpoint that
    was actually reasoned about.
    """
    rows = s.execute(select(models_auth.AuthUser.id,
                            models_auth.AuthUser.subject,
                            models_auth.AuthUser.name)).all()
    return {uid: {"id": uid, "username": subject, "name": name}
            for uid, subject, name in rows}


#: Attribute keys that are RETIRED and never served, however old a row is
#: (TASK-1157). `frontmatter_raw` is the verbatim YAML header of a markdown file
#: deleted in August 2026: nothing reads it, it disagrees with the row it sits on
#: (`status: pending` on cancelled items), and it made a 22-item listing several
#: KB of dead YAML. `migrate_strip_frontmatter_raw.py` removed it from live's
#: rows on 2026-09-15; this line is what makes an un-migrated database — staging,
#: the fork, a restored dump — stop serving it too.
_RETIRED_ATTRIBUTES = ("frontmatter_raw",)


def _served_attributes(attributes: dict | None) -> dict | None:
    if not attributes or not any(k in attributes for k in _RETIRED_ATTRIBUTES):
        return attributes
    return {k: v for k, v in attributes.items() if k not in _RETIRED_ATTRIBUTES}


def _graph_node_json(n: GraphNode, counts: dict[str, dict] | None = None,
                      loads: dict[str, dict] | None = None,
                      include_prose: bool = True,
                      intent_counts: dict[str, int] | None = None,
                      assignees: dict[str, dict] | None = None) -> dict:
    from attention import authored_status

    # Presentation projection (TASK-1046): `abstract`/`detail` are the two
    # ADR-0020 tiers `panel.js` actually renders (`panel.js:545` gates the
    # whole Description section on `abstract`). An AUTHORED value — set by the
    # world-manifest importer (import_world.py) or the write API — always wins,
    # untouched; only a node with prose (`n.body`, TASK-1046) and NO authored
    # abstract gets one synthesised from the body. This is intentionally
    # gated on `abstract` alone, not `detail` independently: the two are a
    # PAIR in the panel (detail only ever shows alongside abstract, inside a
    # "More" disclosure), so a node with an authored abstract keeps its
    # authored detail too (even if that's None), rather than mixing an
    # authored abstract with a body-derived detail that may not even be
    # about the same prose.
    #
    # `include_prose` (TASK-1050) gates ONLY the body-projection fallback and
    # the raw `body` field — never the authored abstract/detail/summary,
    # which are small, ADR-0020-sized and already served today (the world
    # tab's `/api/graph` reads on them unconditionally — board.html:351).
    # This is what makes "a full tree read does not grow by 7,460 lines of
    # prose" true WITHOUT breaking any authored-abstract node's rendering:
    # the only thing suppressed by default is pulling the SOURCE markdown
    # (`n.body`, up to 16,638 lines across the corpus once TASK-1052 imports
    # it) into a response nobody asked for. `/api/graph` and `/api/graph/tree`
    # pass `include_prose=False` by default (their own `?include_prose=1`
    # opts back in); a single-item fetch (`GET /api/node/{id}`) and the
    # workitem listing's own `?include_prose=1` pass `True` explicitly.
    # `_status_payload` (write-API responses) relies on this function's
    # default staying `True` — a just-created/edited node's response is
    # single-item by construction, so it keeps full prose with NO call-site
    # change needed.
    # WHAT `include_prose` GATES, AND WHAT IT MUST NOT. Measured on staging
    # with all 306 workitem bodies loaded: abstracts total 73 KB across the
    # whole backlog, details 863 KB, raw bodies ~937 KB. So the SIZE problem
    # this flag exists for is `detail` and `body`, never `abstract`.
    #
    # Gating the abstract too was a real regression, caught by clicking the
    # board: with prose in the database and the flag defaulting off, a full
    # tree read carried NO abstract for any of the 306 workitems, so every
    # panel went back to "No description authored yet." — silently undoing
    # TASK-1046's whole point while every test still passed, because no test
    # asserted the DEFAULT response renders a description.
    #
    # So: the abstract is ALWAYS projected (bounded — one paragraph per node,
    # +73 KB on a 325 KB tree), and only `detail`/`body` are opt-in. That is
    # also what the task's own acceptance asks for: "prose is fetched per
    # item, not in listings" — a one-paragraph summary IS the listing tier
    # (ADR 0020), the full text is not.
    # AUTHORED prose is untouched by the flag in both directions: the 42 world
    # nodes carry manifest-authored abstract/detail, they were always in this
    # response, and they cost nothing. Only the PROJECTED detail is gated.
    abstract, detail = n.abstract, n.detail
    if not abstract:
        proj_abstract, proj_detail = _project_prose_from_body(n.body)
        if proj_abstract:
            abstract = proj_abstract
            # `detail` follows the flag: it is the large half.
            detail = proj_detail if include_prose else None

    d = {
        "id": n.id, "type": n.type, "name": n.name,
        "summary": n.summary, "abstract": abstract, "detail": detail,
        "owner": n.owner, "source": n.source, "attributes": _served_attributes(n.attributes),
        # WHO IS DOING THIS (TASK-1118). Resolved to `{id, username, name}` or
        # null — never a bare uuid, which the board would then have to resolve
        # with a second call it has no endpoint for.
        #
        # THE KEY IS ALWAYS PRESENT, so a consumer can read `assignee` without
        # testing whether this particular route bothered to emit it. A caller
        # that forgets to pass `assignees` therefore renders an assigned node
        # as unassigned rather than crashing — fail-safe, but silent, which is
        # why every caller of this function passes the map and
        # `verify_assignment.py` asserts a known-assigned node resolves through
        # each of them. Do not add a sixth caller without the map.
        "assignee": (assignees or {}).get(n.assignee_user_id) if n.assignee_user_id else None,
        # STORY-oss-14. Always present, like `assignee`: null and [] when unset,
        # which is every node written before the columns existed.
        "priority": n.priority,
        "labels": list(n.labels or []),
        # Two-axis derived values (ADR 0019), cached at ingest by attention.py:
        "attention": n.attention, "completion": n.completion,
        "attention_count": n.attention_count,
    }
    if n.project_key:
        # STORY-oss-12: only when set, so every payload of an instance that never
        # keyed a project is byte-for-byte today's.
        d["key"] = n.project_key
    if intent_counts is not None:
        # Own-intent count for the map's `◎` glyph (ADR 0032). Only emitted
        # when the caller precomputed it — a single-node fetch returns the
        # intents themselves and has no use for a count of them.
        d["intent_count"] = intent_counts.get(n.id, 0)
    if include_prose:
        # The verbatim source markdown (TASK-1046's `node.body`). Never
        # emitted unless asked for — see the `include_prose` note above.
        d["body"] = n.body

    # --- lifecycle status ---------------------------------------------------- #
    # `status` is the node's own current value. UNTIL TASK-1056
    # (STORY-atlas-retire-status-roundtrip) this also emitted `authored_status`,
    # `status_source`, `status_pending`, `status_set_at` and `status_set_by` —
    # the two-source model needed while a board click could still be "not in
    # git yet" (models_graph.StatusOverride). That table is gone: every node's
    # status lives directly on `node.attributes["status"]` now, so there is no
    # second value left to compare against and nothing left for those fields
    # to distinguish. See app.set_node_status for the write side.
    d["status"] = authored_status(n)
    # When this node reached a done status — the axis the board's age filter
    # sorts on. Null for anything not done. See models_graph.Node.completed_at.
    d["completed_at"] = n.completed_at.isoformat() if n.completed_at else None
    if counts is not None:
        c = counts.get(n.id, {})
        # done/in-progress/todo over the contains subtree — the 3-weight ring
        # (TASK-855). Derived on read (no dedicated column); agrees with the
        # cached completion fraction since ingest is the only writer.
        d["completion_counts"] = {
            "done": c.get("done", 0),
            "in_progress": c.get("in_progress", 0),
            "todo": c.get("todo", 0),
        }
        # Goal-directed urgency layer (ADR 0019): why a not-started node lights up.
        # `state` is the colour before urgency; `attention` (above) already folds
        # urgency in. `urgency_blocks` samples the not-done work waiting on this node.
        d["state"] = c.get("state")
        d["urgency"] = c.get("urgency", "none")
        d["urgency_blocks"] = c.get("urgency_blocks", [])
    if loads is not None:
        # Per-object work-load readout (ADR 0019 Addendum, TASK-860): count of
        # inbound `works_on` workitems + their completion. See
        # attention.work_load for why this is computed on read, not cached.
        d["work_load"] = loads.get(n.id, _EMPTY_WORK_LOAD)
    return d


def _graph_edge_json(e: GraphEdge) -> dict:
    return {"id": e.id, "src": e.src, "dst": e.dst,
            "edge_type": e.edge_type, "directed": e.directed}


# --------------------------------------------------------------------------- #
# PROSE TIERS on the two graph reads.
#
# `include_prose` (TASK-1050) gates ONLY the body-projection fallback and the
# raw `body`; AUTHORED abstract/detail are emitted unconditionally and
# deliberately, because gating the abstract once blanked every panel silently
# (see `_graph_node_json`'s note). That decision rested on a measurement:
# "the 42 world nodes carry manifest-authored abstract/detail ... and they
# cost nothing."
#
# THAT PREMISE HAS EXPIRED, and this is the measurement that replaces it.
# A 7-node neighbourhood of one world node, measured 2026-09-10: 51.6 KB
# total, of which authored abstract 32.4 KB (63%) and authored detail 13.4 KB
# (26%) — about 6.5 KB of prose per node, against 0.6 KB for ALL of its edges.
# World-node prose stopped being manifest-sized once it was hand-written. A
# caller that wants the SHAPE of the graph pays ~50x for prose it did not ask
# for, and an agent traversing the tree spends its whole context on it.
#
# So: an OPT-IN ladder. `prose` UNSET is byte-identical to before.
#
# TWO PROPERTIES THAT KEEP THIS SAFE, both deliberate:
#   1. It is a SEPARATE parameter, not a widening of `include_prose` — that
#      one is load-bearing and documented where it stands.
#   2. It only ever REMOVES fields, never adds. `prose=full` is therefore a
#      no-op synonym for omitting it; to ADD the raw body, `include_prose=1`
#      is still the switch. Subtractive wins if both are passed.
#
# DO NOT PROMOTE ANY TIER TO THE DEFAULT without driving the board first.
# `board.html:675` copies summary/abstract/detail off THIS payload into the
# node object it keeps, and `panel.js:191-200` renders them before the
# per-panel `/api/node/{id}` fetch resolves. Defaulting `detail` away would
# blank the "More" disclosure until that second fetch lands — the same class
# of silent regression the `include_prose` note records, and it would pass
# every test for the same reason.
_PROSE_TIERS: dict[str, tuple[str, ...]] = {
    "none": ("summary", "abstract", "detail", "body"),
    "summary": ("abstract", "detail", "body"),
    "abstract": ("detail", "body"),
    "full": (),
}


def _prose_tier_or_400(prose: str | None) -> JSONResponse | None:
    """Reject an unknown `prose=` value by NAMING the legal ones.

    Fails loudly rather than falling back to the default: a typo'd tier that
    silently returned full prose would be indistinguishable from the feature
    not working, which is how a caller concludes the payload cannot be
    trimmed and stops trying.
    """
    if prose is not None and prose not in _PROSE_TIERS:
        return JSONResponse(
            {"error": "invalid_prose_tier",
             "detail": f"`prose` must be one of {', '.join(_PROSE_TIERS)} — got {prose!r}. "
                       "Omit it for the full payload."},
            status_code=400,
        )
    return None


def _trim_prose(d: dict, prose: str | None) -> dict:
    """Drop the prose fields a tier excludes. Unset tier is a no-op.

    Mutates and returns the same dict — every caller has just built it.
    """
    if not prose:
        return d
    for key in _PROSE_TIERS[prose]:
        d.pop(key, None)
    return d


# --------------------------------------------------------------------------- #
# Status writes — the board's "mark this analyze / in-progress / done" control.
#
# UNTIL TASK-1056 (STORY-atlas-retire-status-roundtrip) this route had two
# branches: an atlas-origin node's status was written directly, but a
# git-origin node's click landed in a separate `status_override` overlay row,
# because the backlog markdown in AgentPlanningSetup used to be the source of
# truth for a workitem's status and Atlas genuinely could not write it. That
# stopped being true on 2026-08-10 (TASK-1052 — every workitem is atlas-origin
# in Postgres now), and the table had been provably empty for a full day
# before it was deleted. So: one branch, for every node — normalise, validate,
# write `attributes["status"]`, recompute, return. See models_graph.py's
# history for the removed `StatusOverride` model if the shape is ever needed
# again.
#
# These are the FIRST state-changing routes outside /auth/*. They go through
# auth.guard_write, which applies the Origin allow-list + the synchronizer token
# to cookie sessions and correctly exempts Bearer service principals.
# --------------------------------------------------------------------------- #
#: Derived from `node_types.NODE_TYPE_SEEDS` (TASK-1389), never typed here.
WORKITEM_TYPES = node_types.WORKITEM_TYPES

#: The work hierarchy's ORDER, top first (TASK-1378): a parent must sit strictly
#: above its child, so epic > story > task. It is each seed's `rank`, and only
#: the seeds that have one (TASK-1389): a workitem type is no longer a rung just
#: by being in `WORKITEM_TYPES`, so a type off the ladder (ADR 0048's
#: `decision`) can join the vocabulary without becoming a rung below task. The
#: ladder is epic > story > task (owner decision 2026-11-10). A further rung,
#: if one is ever seeded with a `rank`, is taken by this rule with no other edit.
#: Levels may be skipped (a task straight under an epic); a level may not be
#: repeated (a story under a story) or inverted (an epic under a task).
#: Enforced by `routers/workitems._check_work_parent` (create, PATCH re-parent,
#: and a PATCH type change against the existing parent) and
#: `_check_work_children` (a type change against existing children); board
#: v2's move picker offers the same choices (`frontend/v2/core/graph-model.js`
#: `parentChoices`), but only this one decides.
WORK_HIERARCHY_RANK: dict[str, int] = {t: node_types.NODE_TYPE_SEEDS[t]["rank"] for t in node_types.WORKITEM_LADDER}

#: A workitem's `priority` (STORY-oss-14), lowest first: the order IS the
#: meaning, so a consumer may sort by index. Four, not Jira's five, because a
#: priority nobody can tell from its neighbour is noise: `low` can wait,
#: `medium` is the ordinary queue, `high` goes next, `critical` interrupts
#: whatever is in flight. "Not set" is NULL, never a fifth word.
#: mcp/app/tools_work.py keeps a copy; keep the two equal.
WORKITEM_PRIORITIES = ("low", "medium", "high", "critical")
#: Limits on `labels`: short tags, not prose. A comma is refused because the
#: list filter (`?label=a,b`) splits on it.
LABEL_MAX_CHARS = 40
LABELS_MAX = 20


def _status_payload(s: Session, n: GraphNode) -> dict:
    return _graph_node_json(n, None, None, assignees=_assignee_map(s))


# `_node_is_durable` / the `durable` response field — REMOVED (TASK-1058,
# STORY-atlas-retire-status-roundtrip). It answered "will this row's current
# field values still be here after the next boot ingest?" — a question with
# no referent once there is no boot ingest (TASK-1056 left this here
# deliberately for TASK-1058 to remove; see this file's git history for the
# TASK-1043 reasoning if the shape is ever needed again).


# --------------------------------------------------------------------------- #
# THE ONE WRITER for `graph_event` (TASK-1045). Modelled on `auth._event()`,
# which is the single writer for `auth_event` — same shape, same discipline,
# and for the same reason: an audit log written from a dozen inline inserts is
# an audit log where half the call sites eventually drift, and nobody can say
# which half by reading it.
#
# DO NOT INLINE A `s.add(GraphEvent(...))` ANYWHERE ELSE. If a new mutation
# needs recording, call this. If it needs a field this does not carry, add the
# field here.
#
# WHAT IT GUARANTEES
#   * The actor is derived from `auth.current_principal` and from nothing else.
#     There is deliberately no `actor=` parameter: a caller cannot forge who
#     did something, because there is no argument through which to try. This is
#     the whole reason the principal is a ContextVar rather than a request
#     attribute (see `auth.current_principal`) — it is reachable from here
#     without threading a `Request` through every write path.
#   * The event joins THE CALLER'S TRANSACTION and is not committed here, so it
#     lands exactly when the mutation it describes lands and evaporates with it
#     on any of the `s.rollback()` paths. An event that could be committed
#     independently of its subject would be the worse failure: a log that
#     records changes which did not happen.
#
#     TWO COMMIT SHAPES, and a new call site has to sit before whichever one it
#     is in. Most mutating routes end at `attention.recompute_derived(s)`, which
#     commits as its last act. The node-doc and node-intent routes change no
#     derived value, say so in their own comments, and call `s.commit()`
#     directly — so their events are recorded immediately BEFORE that commit,
#     never after it. A `record_graph_event` placed after the commit its
#     mutation rode on is not an error anything raises; it is a row that
#     silently rolls back when the session closes.
#   * `responsible_user_id` resolves through ADR 0034's person -> bot link, so
#     the log answers "on whose authority?" and not only "which token?".
#
# WHAT IT DOES NOT GUARANTEE, said plainly rather than left to be discovered:
#   * APPEND-ONLY IS A CONVENTION, NOT A MECHANISM. Nothing in the database
#     stops a row being updated or deleted — no trigger, no hash chain, no
#     `REVOKE`. The property holds because this app ships no update or delete
#     path for `graph_event`, and for no other reason.
#   * NO RETENTION. This table only grows. Nothing prunes, compacts or archives
#     it, and no decision has been taken about that yet.
#   * IT IS NOT A DIFF. `detail` records what changed at the granularity the
#     call site knew — field names for a body edit, from/to for status and
#     assignee. Reconstructing a node's prose from this table is not possible
#     and is not the claim.
# --------------------------------------------------------------------------- #
def record_graph_event(s: Session, action: str, *, entity_type: str, entity_id: str,
                       node_id: str | None = None, related_node_id: str | None = None,
                       detail: dict | None = None) -> models_graph.GraphEvent:
    """Append one row to `graph_event`. See this section's header first.

    `node_id`/`related_node_id` are the nodes whose VISIBILITY governs the
    event, which for a node action is the node itself and for an edge action is
    both endpoints. They are not decoration: `GET /api/events` filters on them
    inside the query, and an event recorded without them would be readable by
    everyone (which is why `node_id` defaults to `entity_id` when the entity IS
    a node, rather than defaulting to NULL and failing open by omission).

    `action` MUST be one of `models_graph.GRAPH_EVENT_ACTIONS` — checked HERE,
    at the one writer, rather than with a CHECK constraint (the model's header
    explains why a constraint is the wrong tool: `_reconcile_graph_schema` can
    only ADD COLUMN, so a CHECK declared on the model would exist on every
    fresh test database and never on live). Raising here is what makes the
    vocabulary load-bearing rather than merely documentary: before this, a
    typo'd action string was accepted, stored, and rendered verbatim in the
    History panel — a defect nothing would ever have surfaced, because
    `GET /api/events` returns whatever `action` string was written the same as
    any other. This is a caller bug, not a request error, so it is a bare
    `ValueError` at the write site and not a 400 handed back to an HTTP caller.
    """
    if action not in models_graph.GRAPH_EVENT_ACTIONS:
        raise ValueError(
            f"record_graph_event: {action!r} is not in GRAPH_EVENT_ACTIONS "
            f"({', '.join(models_graph.GRAPH_EVENT_ACTIONS)})"
        )
    principal = auth.current_principal.get()
    if node_id is None and entity_type == "node":
        node_id = entity_id
    row = models_graph.GraphEvent(
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id)[:128],
        node_id=node_id,
        related_node_id=related_node_id,
        # `"system"` for a principal-less write — a REPL, a migration, a
        # startup path. Never invented for a request: every request that
        # reaches a write route has passed the gate, so a `"system"` row
        # arriving from one would itself be the finding.
        actor_kind=(principal.kind if principal is not None else "system"),
        actor_user_id=(principal.user_id if principal is not None else None),
        actor_subject=(principal.subject if principal is not None else None),
        actor_token_id=(principal.token_id if principal is not None else None),
        responsible_user_id=_responsible_user_id(principal),
        detail=detail,
    )
    s.add(row)
    return row


def _responsible_user_id(principal) -> str | None:
    """The PERSON accountable for `principal`'s act, or None.

    Three cases, and the third one is the point:
      * a browser session — the actor IS a person, so they are accountable for
        their own act;
      * an agent LINKED to a person (ADR 0034) — the person, read off
        `Principal.person_user_id`. This is the column Paperclip calls
        `responsibleUserId`, and it is why `atlas_node_write` from somebody's
        agent is attributable to somebody rather than only to a token;
      * an agent with no person recorded, or no principal at all — NULL.
        Deliberately not back-filled with the bot's own id: that would make a
        bot look like the human who authorised it, which is the one thing this
        column exists to distinguish. `admin bot list` already names the
        unlinked agents, so a NULL here points at a fixable gap rather than
        hiding one.

    Role is NEVER read here — `Principal.person_user_id` is an id and nothing
    else, which is what keeps ADR 0034's "grants inherit, role never does" true
    by construction. This function does not change that; it only reads the id.
    """
    if principal is None:
        return None
    if principal.person_user_id:
        return principal.person_user_id
    return None if principal.is_service else principal.user_id


# --------------------------------------------------------------------------- #
# Graph write API (TASK-1017) — generalises the status route above from one
# column to the whole node/edge graph: create, edit and delete nodes and
# edges over HTTP. Same guard, same origin contract, same recompute-in-request
# discipline. READ THIS BEFORE ADDING A SIXTH ROUTE HERE.
#
# * Every route goes through auth.guard_write, exactly like set_node_status
#   above — Origin allow-list + synchronizer token for cookie sessions, Bearer
#   principals exempt. Do not invent a second write guard.
# * Every row this API creates gets origin="atlas". This was a DURABILITY
#   contract (TASK-1016) while a boot ingest existed to delete anything not
#   marked so; that ingest and both reconcilers were deleted in TASK-1058,
#   and `origin` is now PROVENANCE ONLY — how a row first entered the
#   database. Nothing branches on it. Keep setting it because the field's
#   meaning depends on it being set honestly, not because a reconciler is
#   coming for the row.
# * `node.attributes` is validated against its node_type's JSON-Schema by
#   models_graph's `before_flush` event (validate_node) — the SAME validation
#   the backlog/world ingesters go through, so there is exactly one
#   implementation of "is this node shaped right". This module never calls
#   jsonschema directly; it catches the ValueError the event raises and turns
#   it into a 400. See `_attribute_validation_response` for why the field name
#   comes from the chained `jsonschema.ValidationError` on `exc.__cause__` and
#   not from the ValueError's own message (which does not always name the
#   field — e.g. a bare type mismatch on a nested property).
# * `verify_object_graph.py`'s route-enumeration check
#   ("NO state-changing GET") is an ALLOW-LIST of every mutating route in the
#   app; it must be updated in the SAME commit as a new one here or it stays
#   red on purpose.
# --------------------------------------------------------------------------- #

#: `contains` is world-only (object -> object) per ADR 0027 / the in-flight
#: project-workspaces epic. `decomposes_into` (work-only) does not exist yet
#: (STORY-atlas-work-world-vocabulary), so this is the ONE family rule the
#: write API enforces for now — everything else is permissive. This is
#: deliberately narrower than what the backlog ingest already has on disk:
#: existing epic/story/task `contains` edges (workitem -> workitem hierarchy)
#: were written directly by ingest_graph.py (deleted, TASK-1058), never
#: through this endpoint, and
#: are untouched by this constraint. A NEW `contains` edge requested through
#: THIS api must be object -> object, full stop.
#:
#: `works_on` (TASK-1048) is added here too: workitem -> object, enforced for
#: ANY caller of POST /api/edge, not only the /api/workitem routes below —
#: closing the same hole `create_edge` left open for the low-level
#: construction kit, per the write-API-must-not-allow-what-the-ingest-refuses
#: framing in TASK-1048's own background.
#: ENFORCING THE `edge_type` REGISTRY HERE WAS BUILT, MEASURED AND DECLINED
#: (owner decision, 2026-08-27). Do not rebuild it without reading this first.
#:
#: Since `c8cf667` the `edge_type` table declares all NINE edge types with
#: their legal `(src_family, dst_family)` pairs, where this dict declares
#: four. Note what that asymmetry costs today: `_check_family` treats a
#: `required=None` — i.e. any type absent from this dict — as a PERMISSIVE
#: NO-OP, so `depends_on`, `hosted_on`, `contributes_to` and both `dead`
#: types get no family check at all through generic POST /api/edge.
#:
#: A working implementation that closed that hole exists on branch
#: `feat/edge-type-registry-followup` (commit `d2c5bfa`): fail-closed on an
#: unknown type, 400 on a `dead` one, `_check_family` taking a LIST of pairs.
#: It was not merged, for two reasons and in this order:
#:
#:   1. IT WIDENED SOMETHING NOBODY ASKED IT TO. `edge_type.pairs` lists
#:      `["workitem","workitem"]` for `contains`, because both hierarchies
#:      share that one string (permanently: TASK-1030's rename was cancelled
#:      on 2026-08-27). Reading
#:      the same list as PERMISSION makes a work-hierarchy `contains` edge
#:      creatable through generic POST /api/edge — and therefore through
#:      `atlas_edge_write`, i.e. by every federated MCP client. That route
#:      does not REPLACE an existing parent the way POST /api/workitem's
#:      `parent` field does, so a task could quietly acquire a second parent
#:      in the work tree. A validation win traded for an integrity hazard.
#:   2. The practical gap is small. `edge_type_warnings` on /api/health
#:      already OBSERVES a `dead` type gaining rows; enforcement only makes
#:      it preventable, and live carries zero `serves` and zero `for`.
#:
#: THE DESIGN FIX, if this is ever revisited: `pairs` answers "what family
#: combinations legitimately EXIST" — it is the drift check's input, derived
#: from measuring live. It does not answer "what may be CREATED through this
#: endpoint". Those are two questions and the table cannot currently tell
#: them apart. Separate them (a `generic_write` column, or equivalent)
#: BEFORE wiring enforcement to `pairs`; do not encode the exception in code
#: here, which is the duplication this registry exists to remove.
EDGE_FAMILY_RULES: dict[str, tuple[str, str]] = {
    "contains": ("object", "object"),
    "works_on": ("workitem", "object"),
    # `uses` — one project stands on another without being owned by it. Added
    # for `project-a --uses--> project-b`: project-a is project-b's first
    # consumer, which is a dependency, not containment (`contains` is "is part
    # of" and nothing else) and not contribution (`contributes_to` runs
    # party -> project). Object -> object, like `contains`.
    "uses": ("object", "object"),
    # `instance_of` — a deployed environment points at what it is an instance
    # OF, instead of forking the product tree beside itself. ADR 0030: the
    # staging stack used to hang under a `sys-atlas-staging` node that sat as a
    # SIBLING of the product it stages. Object -> object.
    "instance_of": ("object", "object"),
    # `requires` — a hard run-time dependency between world objects (ADR 0044
    # §7). Its own type rather than widening `depends_on`, because
    # `attention.py` reads every `depends_on` row with no family filter: a
    # world-level `depends_on` would leak into the blocked-work signals.
    # Object -> object, checked here so a workitem cannot carry one.
    "requires": ("object", "object"),
}


def _node_family(s: Session, n: GraphNode) -> str | None:
    nt = s.get(models_graph.NodeType, n.type)
    return nt.family if nt is not None else None


def _check_family(s: Session, edge_type: str, required: tuple[str, str] | None,
                   src: GraphNode, dst: GraphNode) -> JSONResponse | None:
    """Shared body of the family check `create_edge` already did inline for
    `EDGE_FAMILY_RULES` entries — extracted (TASK-1048) so `create_workitem`/
    `patch_workitem` can enforce `WORK_HIERARCHY_FAMILY_RULE` (which, per the
    comment above, cannot live IN `EDGE_FAMILY_RULES`) with the identical
    check and the identical error shape, rather than a second implementation
    that could drift from this one. `required=None` is a permissive no-op,
    matching `EDGE_FAMILY_RULES.get(edge_type)` returning `None` for an
    unlisted type."""
    if required is None:
        return None
    src_family, dst_family = _node_family(s, src), _node_family(s, dst)
    if (src_family, dst_family) != required:
        return JSONResponse(
            {"error": "family_mismatch",
             "detail": f"edge type {edge_type!r} requires "
                       f"({required[0]!r}, {required[1]!r}) families; got "
                       f"({src_family!r}, {dst_family!r}) for {src.id!r} -> {dst.id!r}"},
            status_code=409,
        )
    return None


def _existing_binding(s: Session, node_id: str) -> GraphEdge | None:
    """This workitem's current outbound `works_on` edge, if any. ADR 0028 §1:
    AT MOST ONE, never exactly one — 166/306 backlog workitems (54%) carry
    none at all (see the TASK-1048 measured mapping doc), so this is never
    required, only capped. Deterministic pick (lowest edge id) so a 409
    naming "the existing binding" always names the SAME one even for the one
    pre-existing violation already in the data (`TASK-850`, bound to both
    `atlas-api` and `atlas-postgres` — the owner has not chosen which survives;
    this function does not choose for him, it only ever stops the count from
    going higher — see `_refuse_second_binding`)."""
    rows = s.scalars(
        select(GraphEdge)
        .where(GraphEdge.src == node_id, GraphEdge.edge_type == "works_on")
        .order_by(GraphEdge.id)
    ).all()
    return rows[0] if rows else None


def _refuse_second_binding(s: Session, node_id: str) -> JSONResponse | None:
    """409, naming the existing binding, iff `node_id` already has one.
    Called from BOTH `create_edge` (closes the hole for the generic
    construction kit) and `patch_workitem`'s binding-set path below — the
    ONE place this rule is decided, per TASK-1048 step 3."""
    existing = _existing_binding(s, node_id)
    if existing is None:
        return None
    return JSONResponse(
        {"error": "binding_exists",
         "detail": f"{node_id!r} is already bound to {existing.dst!r} (edge {existing.id}); "
                   f"at most one works_on binding is allowed (ADR 0028 §1) — delete the "
                   f"existing edge first",
         "existing_binding": existing.dst, "existing_edge_id": existing.id},
        status_code=409,
    )


class EdgeDelete(BaseModel):
    csrf_token: str | None = None


def _attribute_validation_response(exc: ValueError) -> JSONResponse:
    """Turn models_graph.validate_node's before_flush ValueError into a 400
    that NAMES the offending field.

    `exc`'s own message does not always name it: for a JSON-Schema `type`
    mismatch on a nested property, `jsonschema`'s `.message` is just
    "5 is not of type 'string'" — no property name in the text at all. What
    DOES carry the field is the chained `jsonschema.ValidationError`, which
    `validate_node` preserves on `exc.__cause__` via `raise ValueError(...)
    from e`. `.absolute_path` is the JSON path segments to the offending
    value (e.g. `deque(['status'])`); joined, that is the field name. When the
    failure is not a schema error at all (e.g. "references unknown node_type
    'x'") there is no `__cause__` to read and the ValueError's own message is
    already specific enough to act on.
    """
    import jsonschema

    cause = exc.__cause__
    if isinstance(cause, jsonschema.ValidationError):
        field = ".".join(str(p) for p in cause.absolute_path) or "(root)"
        return JSONResponse(
            {"error": "invalid_attributes", "field": field, "detail": cause.message},
            status_code=400,
        )
    return JSONResponse({"error": "invalid_node", "detail": str(exc)}, status_code=400)


def _acceptance_list(s: Session, node_id: str) -> list[str]:
    rows = s.scalars(
        select(models_graph.AcceptanceCriterion)
        .where(models_graph.AcceptanceCriterion.node_id == node_id)
        .order_by(models_graph.AcceptanceCriterion.position)
    ).all()
    return [r.text for r in rows]


def _existing_result_row(s: Session, node_id: str) -> models_graph.WorkitemResult | None:
    """This node's `workitem_result` row, if any. Today the corpus and every
    write path here maintain "at most one row per node" (see
    `WorkitemResult`'s own docstring), but this still picks deterministically
    (lowest id) rather than assuming a single result, same convention as
    `_existing_parent_edge`/`_existing_binding`."""
    rows = s.scalars(
        select(models_graph.WorkitemResult)
        .where(models_graph.WorkitemResult.node_id == node_id)
        .order_by(models_graph.WorkitemResult.id)
    ).all()
    return rows[0] if rows else None


def _result_heading_display(heading: str) -> str:
    """`"## Outcome"` -> `"Outcome"`: strip the leading `##` marker and
    surrounding whitespace for display, WITHOUT touching the stored value —
    `WorkitemResult.heading` stays the raw line forever (see its own
    docstring on why: a checker rebuilding `"## " + name` would pass its own
    round-trip test while silently rewriting the file's own words)."""
    return heading.lstrip("#").strip()


# `GET /api/status/pending` — DELETED (TASK-1056, STORY-atlas-retire-status-
# roundtrip). It answered "board clicks that have not reached git yet"; there
# is no git for a click to reach any more (status_override is gone), so the
# concept had no referent and the route could only ever return
# `{"count": 0}`. Its only consumer, `AgentPlanningSetup/scripts/
# apply-board-status.py`, is deleted by TASK-1057 in the same story.


# --------------------------------------------------------------------------- #
# Read API (TASK-1050) — "agents must be able to read the backlog without the
# filesystem". THE GATE ON DELETING ANYTHING: every task in this repo that
# names a defect, cites a decision or checks whether something is already
# filed was written by an agent that ran `grep -r backlog/`. This section is
# what answers those same questions over HTTP instead.
#
# Three routes:
#   GET /api/node/{id}          — one workitem, COMPLETE, in one call: every
#                                  field, verbatim body, ordered acceptance,
#                                  parents, world binding, both directions of
#                                  depends_on.
#   GET /api/workitems           — list/filter without downloading the whole
#                                  graph: by parent, status, type, binding,
#                                  owner, composed onto one WHERE clause so
#                                  adding a filter later (ADR 0028's reserved
#                                  `?levels=` — deliberately NOT built here,
#                                  see STORY-atlas-node-backlog's own TASK-1027)
#                                  is one more `clauses.append(...)`.
#   (search is `?q=` on the listing route, not a separate endpoint — see
#   `_search_node_ids` below.)
#
# SCOPE NOTE — the sibling endpoint this task's own instructions say to build
# ON, not duplicate (`GET /api/node/{id}/backlog`, STORY-atlas-node-backlog /
# TASK-1026 + TASK-1027), is STILL `status: pending` in the backlog as of
# this commit — it has not been built. There is nothing to build on top of
# yet. If it lands later, it answers a different question ("what work rolls
# up onto THIS node, bucketed own/below/linked") from what this section
# answers ("find/filter/search across the whole backlog") — complementary,
# not overlapping.
#
# NODE VISIBILITY NOTE — `_visible_node_ids` below IS the enforcement of
# `node_visibility` (live since 2026-08-16). There is NO global choke point:
# the `do_orm_execute`-over-every-Session design (TASK-974 / ADR 0026) was
# never built, and this local, per-query implementation is what actually
# refuses. Read that as "one rule, applied at every read call site", not as "a
# stopgap waiting for the real thing" — an earlier version of this comment
# said "nothing anywhere in this codebase enforces node_visibility today",
# which was true when it was written and false from 2026-08-16 onward.
#
# It implements the exact rule TASK-974's task file specifies — fail closed,
# ALL (not ANY) over multiple `contains` parents, built from a raw edge scan
# (never `attention.derive()`'s adjacency, which deliberately skips
# bridge-family destinations and would wrongly treat a bridge child as a
# parentless, visible root — see that file's "Traps").
#
# Because it is per-query, a NEW route does not inherit it. `verify_node_access.py`
# §F enumerates `route_table.live_routes(app.app)` and FAILS on any `/api` route with no access
# classification — that check, not this comment, is what stops somebody
# forgetting.
# --------------------------------------------------------------------------- #
def _visible_node_ids(s: Session) -> set[str] | None:
    """Node ids the CURRENT PRINCIPAL (`auth.current_principal`) may see, or
    `None` for "no restriction — return everything".

    Applied INSIDE every query below as a `WHERE ... id IN (...)` clause, not
    as a post-filter over an already-loaded list — a post-filter leaks
    COUNTS even when it strips rows (a caller could tell a hidden node
    exists by comparing a filtered page against an unfiltered total), which
    is the exact failure mode TASK-1026's own acceptance criteria name for
    the sibling endpoint. Same rule here.

    `None` is returned (unrestricted) for an owner AND, as a fast path, for
    everyone else whenever nothing is private-and-ungranted FOR THEM. Two
    different callers therefore get two different answers from one database
    at one instant, which is the intended behaviour and not a cache bug.

    Rule (ADR 0026 / TASK-974, EXTENDED with per-account grants):
        visible(n) = False  if no principal at all (fail closed — should be
                             unreachable here, the auth gate already refused
                             an unauthenticated caller before this runs, but
                             a wrong assumption failing CLOSED is the safe
                             direction)
                   = True   if principal.is_owner (no restriction)
                   = False  if n is marked 'private' in node_visibility AND
                             this principal holds no `node_grant` row for it
                   = True   if n has no `contains` parent (a root)
                   = ALL(visible(p) for p in n's contains-parents)

    THE GRANT IS THE ONLY THING THAT CHANGED, and it changes one line: a
    private node the caller has been granted stops being blocked, exactly as
    if it had never been marked. Everything downstream — the ALL-over-parents
    walk, the fail-closed cycle handling, the unresolvable-parent rule — is
    untouched, because a grant is a statement about ONE node and never about
    its subtree. A granted node's children are then visible for the ordinary
    reason (their parent resolved visible), which is what makes "grant the
    project root" mean "see that project", with no separate subtree concept
    to keep in sync.

    Grants resolve by `principal.user_id` — the account the caller IS, which
    for a Bearer principal is `auth_service_token.owner_user_id` — UNION the
    grants of the person that account is linked to, if any (ADR 0034).

    THAT ACCOUNT IS USUALLY NOT A PERSON, and an earlier version of this
    docstring said the opposite. CLAUDE.md's "a token belongs to the HUMAN,
    who stays accountable" is a statement about ACCOUNTABILITY, and it was
    read here as a statement about identity. It is not: `admin bot add`
    creates a distinct `auth_user` row per agent (`is_service = true`) and
    mints the token against that, so on live every service token resolves to
    a bot account of its own — none to a person (measured 2026-08-16).

    An agent's grants are its OWN plus its person's (TASK-1114, below). The
    consequence an owner met first, before that landed, was that marking a
    project private hid it from that project's agents too until each bot
    account was granted it by hand — correct, and also the drift ADR 0034
    exists to remove, because a hidden node and a nonexistent one return the
    same 404 and the drift therefore presented as a suspected MCP bug. What
    has NOT changed: an agent's reach is still stated rather than assumed,
    because the statement is now its person's grant list; an agent with no
    person recorded still inherits nothing; and the settings grid still lists
    every agent account rather than filtering them out.
    A cycle on the parent walk, or a parent id that does not resolve to a
    real node, fails CLOSED (False) — guessing "visible" in either of those
    cases is the concealment-shaped bug TASK-974's task file warns about
    (union/ANY semantics turning a second parent into a silent un-hide).
    """
    principal = auth.current_principal.get()
    if principal is None:
        return set()  # fail closed — see docstring; should not be reachable
    # PROJECT RBAC (ADR 0041, STORY-oss-11) — OPT-IN, and the ONLY change to this
    # function. With `ATLAS_RBAC=off` (the default) this is one boolean read and
    # everything below runs exactly as before; with `projects` the RBAC branch
    # answers instead, narrowing inner private subtrees through the same
    # `_walk_unblocked` as below. One resolver, two branches: every route that
    # calls this (search, the ADR registry, events, outline, ...) inherits
    # RBAC visibility with no code of its own.
    if rbac.enabled():
        return rbac.visible_node_ids(s, principal, _walk_unblocked)
    if principal.is_owner:
        return None

    private_ids = set(s.scalars(
        select(models_auth.NodeVisibility.node_id).where(
            models_auth.NodeVisibility.visibility == models_auth.VISIBILITY_PRIVATE
        )
    ).all())
    if not private_ids:
        return None  # fast path — nothing marked private, nothing to restrict

    # The per-account half. Read for the caller's own user_id and, if the caller
    # is an agent, for the PERSON it belongs to. A principal with no user_id
    # (there is no such kind today, but the field is Optional) holds no grants
    # and is treated as granted nothing — fail closed.
    #
    # GRANTS INHERIT DOWN THE LINK; ROLE NEVER DOES (ADR 0034 / TASK-1114). The
    # safety property runs the opposite way to intuition and is the reason this
    # is on by default: an OWNER holds no grants — an owner is unrestricted by
    # ROLE, four lines above, before a grant row is ever read — so an owner's
    # agent inherits an EMPTY SET and sees shared projects only until granted
    # something deliberately. Linking the identities makes the owner's agent
    # LESS capable, not more.
    #
    # `principal.person_user_id` is an id and nothing else, so this cannot
    # accidentally become role inheritance: there is no role here to read. See
    # its comment in auth.Principal.
    #
    # A UNION, not a replacement: an agent keeps its own grants, so it can still
    # be given something its operator does not have. A dangling link — the
    # column is bare text with no FK — matches no grant row and contributes
    # nothing, which is the fail-safe direction.
    granted_ids: set[str] = set()
    grant_holders = [uid for uid in (principal.user_id, principal.person_user_id) if uid]
    if grant_holders:
        granted_ids = set(s.scalars(
            select(models_auth.NodeGrant.node_id).where(
                models_auth.NodeGrant.user_id.in_(grant_holders)
            )
        ).all())

    blocked_ids = private_ids - granted_ids
    if not blocked_ids:
        return None  # every private node is granted to THIS caller

    return _walk_unblocked(s, blocked_ids)


def _walk_unblocked(s: Session, blocked_ids: set[str]) -> set[str]:
    """Every node id reachable as visible when `blocked_ids` are hidden: the
    ALL-over-parents containment walk, with the binding hop and the fail-closed
    cycle and unresolvable-parent cases. The rule is `_visible_node_ids`';
    it is its own function only so the RBAC branch (`rbac.visible_node_ids`)
    narrows inner private subtrees with this same code rather than a copy.
    Moved out of `_visible_node_ids` unchanged (STORY-oss-11).
    """
    all_ids = set(s.scalars(select(GraphNode.id)).all())
    parents_of: dict[str, list[str]] = {}
    for src, dst in s.execute(
        select(GraphEdge.src, GraphEdge.dst).where(GraphEdge.edge_type == "contains")
    ).all():
        parents_of.setdefault(dst, []).append(src)

    # A WORKITEM FOLLOWS WHAT IT WORKS ON. Without this, marking a project
    # private hides the project and leaves its BACKLOG on screen: a task is not
    # itself marked, and it has no `contains` parent of its own (epics are work
    # roots), so the containment rule alone rules it visible. The board then
    # renders those tasks under its "not bound to a project" bucket — the names
    # and the count of the private work, in a list whose own label says nobody
    # can see where they belong. Measured in a browser on 2026-08-16, which is
    # the only reason it is known.
    #
    # EFFECTIVE bindings, not raw `works_on` edges (TASK-1025): a task under a
    # bound story inherits that story's object, so hiding the object has to hide
    # the whole chain, not just the one item that happened to carry the edge.
    from attention import effective_bindings
    binding_of = effective_bindings(s)

    memo: dict[str, bool] = {}

    def visible(nid: str, path: frozenset[str]) -> bool:
        if nid in memo:
            return memo[nid]
        if nid in path:
            memo[nid] = False  # a `contains` cycle — fail closed
            return False
        if nid in blocked_ids:
            memo[nid] = False
            return False
        # The binding hop. `works_on` points workitem -> object and an object
        # never binds, so this recursion is one level deep and cannot loop; the
        # `path` guard is passed anyway rather than relying on that staying true.
        bound_to = binding_of.get(nid)
        if bound_to is not None and not visible(bound_to, path | {nid}):
            memo[nid] = False
            return False
        parents = parents_of.get(nid) or []
        if not parents:
            memo[nid] = True
            return True
        ok = True
        nxt = path | {nid}
        for p in parents:
            if p not in all_ids or not visible(p, nxt):
                ok = False
                break
        memo[nid] = ok
        return ok

    return {nid for nid in all_ids if visible(nid, frozenset())}


def _may_see_node(s: Session, node_id: str) -> bool:
    """Whether the current principal may see ONE node.

    A thin wrapper over `_visible_node_ids` and deliberately not a cheaper
    independent check: the rule is ALL-over-parents with a fail-closed cycle
    case, so "is this one node private?" is NOT the question — a perfectly
    shared node inside a private project is invisible, and any shortcut that
    reads `node_visibility` for the single id gets that backwards. One rule,
    one implementation, however small the caller's question is.
    """
    visible_ids = _visible_node_ids(s)
    return visible_ids is None or node_id in visible_ids


def _guard_node_access(s: Session, node_id: str) -> JSONResponse | None:
    """`None` if the caller may act on `node_id`, else the 404 to return.

    WRITES ARE GATED BY THE SAME RULE AS READS, and this is the function that
    says so. `auth.guard_write` answers "may this principal write at all" —
    Origin, CSRF, scope — and knows nothing about WHICH node. Without this,
    a member holding the `write` scope could PATCH or DELETE a node they
    cannot read, and would learn it exists from the response.

    It answers 404 and never 403, matching the read side: "you may not touch
    this" and "this does not exist" must be indistinguishable, or the error
    code itself confirms the id.
    """
    if _may_see_node(s, node_id):
        return None
    return JSONResponse({"error": f"unknown node {node_id!r}"}, status_code=404)


def _require_project_role(s: Session, node_ids, need: str,
                          root_needs: str | None = None) -> JSONResponse | None:
    """`None` if the caller holds project role `need` on every node's project,
    else the 403 to return (ADR 0041 §2). ALWAYS `None` with RBAC off, before a
    row is read — today's write rules are `guard_write` plus
    `_guard_node_access`, and they stay exactly that.

    Called AFTER the route's visibility check, so a node the caller cannot see
    is still that route's 404, and this 403 only ever reaches someone who can
    already read the node. `need` is `rbac.EDITOR` for a create or change and
    `rbac.MAINTAINER` for a delete; `root_needs=rbac.OWNER` on deleting a node
    that is itself a project root.
    """
    return rbac.require(s, auth.current_principal.get(), node_ids, need, root_needs=root_needs)


def _project_root_creator(node_id: str, *,
                          work_item: bool = False) -> tuple[str | None, JSONResponse | None]:
    """The person who will own the project root `node_id` this request is about
    to create, or the 403 to return BEFORE the insert (ADR 0051, `rbac.
    root_creator`). `(None, None)` with RBAC off, before anything is read."""
    return rbac.root_creator(auth.current_principal.get(), node_id, work_item=work_item)


def _bind_project_root_creator(s: Session, node_id: str, person_id: str | None,
                               request: Request) -> None:
    """Bind `person_id` `owner` on the new project root `node_id`, in the
    caller's transaction (ADR 0051). A no-op for `person_id=None`, which is
    every RBAC-off request: `_project_root_creator` returned it without a read.

    Audited EXACTLY as `PUT /api/access/project/{id}/bindings` audits a binding
    (`routers.access`): one `project_role_bound` `auth_event`, `user_id` the
    person whose access moved, `actor` the caller's subject (the agent's, when an
    agent created it), same `detail` shape. In `auth_event`, never `graph_event`,
    for that route's reason: who was given a private project is not readable by
    everyone who can see the node. The node's own `node.create` graph_event is
    unchanged. Call it before the route's commit, like every audit write here.

    A row already there is RAISED to `owner`, never left lower: an id can be
    re-created after a delete, and `_delete_node_dependents` does not remove
    `project_role_binding` rows (it predates them), so the creator's old role on a
    deleted node of the same id must not outrank this rule.
    """
    if person_id is None:
        return
    B = models_auth.ProjectRoleBinding
    principal = auth.current_principal.get()
    granted_by = principal.user_id if principal is not None else None
    row = s.get(B, (node_id, models_auth.PRINCIPAL_USER, person_id))
    if row is not None and row.role == rbac.OWNER:
        return
    if row is None:
        s.add(B(project_id=node_id, principal_kind=models_auth.PRINCIPAL_USER,
                principal_id=person_id, role=rbac.OWNER, granted_by=granted_by))
    else:
        row.role, row.granted_by = rbac.OWNER, granted_by
    auth._event(s, "project_role_bound", user_id=person_id,
                actor=principal.subject if principal is not None else None,
                ip=auth._client_ip(request), user_agent=auth._user_agent(request),
                detail="project=%s %s=%s role=%s" % (node_id, models_auth.PRINCIPAL_USER,
                                                      person_id, rbac.OWNER))


# --------------------------------------------------------------------------- #
# THE SETUP GUIDE'S RBAC EXEMPTION — one named exception to the project-role check.
#
# Approved (owner decision, 2026-10-26). With `ATLAS_RBAC=projects`, the setup guide records its
# Proposed ADRs through the registry's own write (`routers.adrs.put_adr`), which asks for
# `editor` on the target project. An instance owner whose role there comes only from the
# shared default (or whose admin-scoped agent, capped and never inheriting the instance role,
# ADR 0034) would need a `project_role_binding` on `atlas-system` before the guide could
# write at all: a first-run guide that cannot run on first run.
#
# So, and ONLY so: while a guide route has opened `setup_guide_rbac_exemption(project)` for
# `atlas-system`, the registry's project-role check is skipped for THAT project and THAT
# caller. What it does not cover, each on purpose:
#   * any project other than `atlas-system` (the exemption is never armed for one);
#   * any route other than the guide's two writes (POST /api/setup/answers and the accept);
#     `PUT /api/adrs/new` and `PUT /api/adrs/{id}` called directly never arm it;
#   * any caller other than the one the guide route already admitted (an owner's session,
#     or an admin-scoped token linked to a live owner, `routers.setup`): the guide checks
#     that BEFORE arming it, and the exemption is pinned to that principal object;
#   * visibility: `_guard_node_access` still runs first, so a project the caller cannot see
#     is still a 404.
# With RBAC off the project-role check returns None anyway, so this changes nothing there.
# `verify_setup_guide.py` §I proves each line of the list above.
# --------------------------------------------------------------------------- #
SETUP_GUIDE_RBAC_EXEMPT_PROJECT = "atlas-system"
_setup_guide_rbac_exemption: ContextVar[tuple | None] = ContextVar(
    "setup_guide_rbac_exemption", default=None)


@contextmanager
def setup_guide_rbac_exemption(project: str):
    """Arm the exemption for the duration of one guide write. Inert for any project other than
    `atlas-system`. The caller (a guide route) has already admitted the principal."""
    armed = (project, auth.current_principal.get())         if project == SETUP_GUIDE_RBAC_EXEMPT_PROJECT else None
    token = _setup_guide_rbac_exemption.set(armed)
    try:
        yield
    finally:
        _setup_guide_rbac_exemption.reset(token)


def _require_project_role_unless_setup_guide(s: Session, project: str,
                                             need: str) -> JSONResponse | None:
    """`_require_project_role(s, [project], need)`, except under the setup guide's exemption
    (the block above): armed, for `atlas-system`, by the same principal that armed it."""
    armed = _setup_guide_rbac_exemption.get()
    if (armed is not None and project == SETUP_GUIDE_RBAC_EXEMPT_PROJECT and armed[0] == project
            and armed[1] is not None and armed[1] is auth.current_principal.get()):
        return None
    return _require_project_role(s, [project], need)


# --------------------------------------------------------------------------- #
# Node intent (ADR 0032) — a node's durable purpose, inherited DOWNWARD.
#
# WORK ROLLS UP; INTENT ROLLS DOWN. ADR 0028 settled that a task bound to
# `atlas-api` appears there and on every node CONTAINING it, never on anything
# inside it, and an earlier draft's downward push was rejected. This travels
# the other way and that is not an inconsistency: work is a QUANTITY (rolling
# it down double-counts, and makes every leaf look accountable for its
# ancestors' backlog), intent is a CONSTRAINT (a constraint on the whole
# binding the part is precisely what `contains` asserts).
#
# The union below is therefore idempotent — it behaves like the `max` rollups
# (health, attention), never like the `sum` ones ADR 0030 warns double-count
# across several parents. Several `contains` parents is the normal case here.
# --------------------------------------------------------------------------- #
def _intent_json(row: models_graph.NodeIntent, owner_name: str | None = None) -> dict:
    d = {
        "id": row.id, "node_id": row.node_id, "slug": row.slug, "kind": row.kind,
        "title": row.title, "statement": row.statement, "source": row.source,
        "position": row.position,
    }
    if owner_name is not None:
        d["owner_name"] = owner_name
    return d


#: Server-side pagination bounds (TASK-1050 acceptance: "the largest project
#: has 191 tasks under it today" is TASK-1026's number for a single node's
#: backlog; the whole workitem corpus is ~350 nodes — small enough that even
#: the max page size is cheap, but still bounded so a caller can never force
#: an unbounded response by omitting `page_size`).
WORKITEM_LIST_DEFAULT_PAGE_SIZE = 50
WORKITEM_LIST_MAX_PAGE_SIZE = 200


def _search_hits(s: Session, q: str) -> dict[str, dict]:
    """`_search_node_ids`, keeping WHERE each node matched.

    Returns `{node_id: {"sources": set[str], "doc_paths": set[str]}}`. The
    sources are SEARCH_SOURCES; `doc_paths` are the SERVED documents that
    matched, so a caller can go straight to `GET /api/node/{id}/doc?path=`.

    EIGHT sources: `comment` (a workitem's comments, after `result` below); STORY-oss-14 added `labels` (last below); ADR 0045 added `adr` (below, with `adr_ids` per hit); TASK-1156 added `prose` — `summary`/`abstract`/
    `detail`, where world nodes actually keep their context (see
    `models_graph.NODE_PROSE_SEARCH_SQL`). `body` still includes `name`.
    Every scope note on `_search_node_ids` applies here unchanged: no family
    filter inside, no access filter inside — both callers apply their own.
    """
    def match(sql: str):
        return text(f"({sql}) @@ plainto_tsquery('english', :q)").bindparams(q=q)

    hits: dict[str, dict] = {}

    def add(node_id: str, source: str, doc_path: str | None = None) -> None:
        h = hits.setdefault(node_id, {"sources": set(), "doc_paths": set()})
        h["sources"].add(source)
        if doc_path:
            h["doc_paths"].add(doc_path)

    for nid in s.scalars(select(GraphNode.id).where(match(models_graph.NODE_PROSE_SEARCH_SQL))):
        add(nid, "prose")
    for nid in s.scalars(select(GraphNode.id).where(match(models_graph.NODE_BODY_SEARCH_SQL))):
        add(nid, "body")
    for nid in s.scalars(select(models_graph.AcceptanceCriterion.node_id)
                         .where(match(models_graph.AC_TEXT_SEARCH_SQL))):
        add(nid, "acceptance")
    for nid in s.scalars(select(models_graph.WorkitemResult.node_id)
                         .where(match(models_graph.WORKITEM_RESULT_SEARCH_SQL))):
        add(nid, "result")
    for nid in s.scalars(select(models_graph.WorkitemComment.node_id)
                         .where(match(models_graph.WORKITEM_COMMENT_SEARCH_SQL),
                                models_graph.WorkitemComment.deleted_at.is_(None))):
        add(nid, "comment")
    for nid, path in s.execute(select(models_graph.NodeDoc.node_id, models_graph.NodeDoc.path)
                               .where(match(models_graph.NODE_DOC_SEARCH_SQL),
                                      text(models_graph.NODE_DOC_SERVED_SQL))):
        add(nid, "doc", path)
    # SIXTH SOURCE (ADR 0045): the ADR registry. The hit is the ADR's PROJECT node, the same
    # way a document hit is its node; `adr_ids` says which ADRs, as `doc_paths` says which
    # documents. Matches the id (`AT001`), the legacy number (`0041`, `ADR 0041`) and the
    # words of title and body. Orphaned records (their file is gone) are not searched.
    # `adr_ids` is added only to a hit that has one, so an empty registry changes nothing.
    import models_adr
    A = models_adr.AdrRecord
    alias = [match(models_adr.ADR_SEARCH_SQL)]
    ref = q.strip().upper().replace(" ", "")
    m_id = re.match(models_adr.ADR_ID_PATTERN, ref)
    m_legacy = re.match(r"^(?:ADR[_-]?)?(\d{1,5})$", ref)
    if m_legacy:  # legacy first: `ADR0041` must not read as prefix `ADR`
        alias.append(A.legacy_number == int(m_legacy.group(1)))
    elif m_id:
        alias.append(A.adr_id == ref)
    for nid, adr_id in s.execute(select(A.project_node_id, A.adr_id)
                                 .where(or_(*alias), A.orphaned_at.is_(None))):
        add(nid, "adr")
        hits[nid].setdefault("adr_ids", set()).add(adr_id)
    # SEVENTH SOURCE (STORY-oss-14): a workitem's `labels`. Exact tags, not
    # stemmed prose: a hit is a node carrying the whole query as one label
    # (`good first issue`), or every word of it as labels (`backend auth`, the
    # same AND `plainto_tsquery` gives the other sources). Both are `@>` on the
    # array, which `ix_node_labels_gin` serves. No labels anywhere, no hits.
    whole = " ".join(q.strip().lower().split())
    if whole:
        words = sorted(set(whole.split()))
        for nid in s.scalars(select(GraphNode.id).where(or_(
                GraphNode.labels.contains([whole]), GraphNode.labels.contains(words)))):
            add(nid, "labels")
    return hits


# --------------------------------------------------------------------------- #
# The access-administration surface (STORY-atlas-access-admin).
#
# WHY THIS IS A SEPARATE PAIR OF ROUTES rather than fields on `PATCH
# /api/node/{id}`: visibility is not a property of the node, it is a property
# of the RELATIONSHIP between a node and an account. Putting `visibility` on
# the node write API would mean every caller holding the ordinary `write`
# scope could re-share a private project as a side effect of editing its name
# — and `PATCH /api/node` is the most-used write route there is. Access
# changes cost `admin`, are owner-only, and have their own path so that a
# review of who-changed-what can find them.
#
# OWNER-ONLY, and 403 rather than 404. The rest of this module hides a node's
# EXISTENCE from a caller who may not see it; there is nothing to hide about
# the existence of an admin page. A member who calls this learns only that
# access administration exists, which their own login screen already implies.
# --------------------------------------------------------------------------- #
_NOT_OWNER = {"error": "forbidden",
              "detail": "Access administration is limited to owner accounts."}


def _require_owner() -> JSONResponse | None:
    """`None` if the caller is an owner, else the 403 to return."""
    principal = auth.current_principal.get()
    if principal is not None and principal.is_owner:
        return None
    return JSONResponse(_NOT_OWNER, status_code=403)


def _acting_user_id() -> str | None:
    principal = auth.current_principal.get()
    return principal.user_id if principal is not None else None


# --------------------------------------------------------------------------- #
# GitHub identity + repo access (github_link.py has the design).
#
# Two halves, two different callers:
#
#   /api/me/github*        the SIGNED-IN PERSON, linking or unlinking their OWN
#                          GitHub identity. Browser session only: a Bearer
#                          principal is refused outright (an agent must not be
#                          able to link an identity or start an OAuth flow).
#   /api/access/github/*   the OWNER, approving or revoking repo access. EXACTLY
#                          the mechanism of `PUT /api/access/node/{id}`:
#                          `_require_owner()` (a bot's own role is always member,
#                          so no token qualifies, whatever its scope), then
#                          `auth.guard_write` (cookie session -> Origin + CSRF
#                          synchronizer token), plus an `admin` entry in
#                          `auth.WRITE_SCOPE_ROUTES`. Agents propose; humans commit.
#
# Linking grants nothing. Only the owner's PUT sends an invitation.
# --------------------------------------------------------------------------- #
_GH_OFF = {"error": "github_not_configured",
           "detail": "GitHub linking is not configured on this server."}


class CsrfOnly(BaseModel):
    csrf_token: str | None = None
