"""
The `node_type` registry seeds — TASK-1058.

This is REGISTRY SEEDING, not data ingest. Before TASK-1058 these lived inside
`ingest_graph.py` (`ensure_backlog_node_types`) and `ingest_world.py`
(`ensure_world_node_types`), each called every time that module's reconciler
ran. The reconcilers are gone (STORY-atlas-retire-status-roundtrip: the
backlog markdown and the world manifest are no longer read at boot), but a
fresh, empty database still needs a working `node_type` table — every `Node`
row has a `type` FK into it, and `node.attributes` is validated against the
owning type's JSON-Schema on every flush (`models_graph.validate_node`). So
these two functions and their seed dictionaries move here rather than
disappearing with the modules that used to host them, and `app.startup()`
calls both directly, unconditionally, on every boot (not gated behind "is this
a fresh database" the way the v0 Component seed is) — see that function.

WHY A NEW MODULE, NOT `seed.py`. `seed.py` seeds the legacy v0 Component tree
and imports `from app import Component, Gate, Requirement, Decision` — which
transitively imports `auth`, which refuses to even be IMPORTED without
`ATLAS_PASSWORD_PEPPER`/`ATLAS_TOKEN_PEPPER` set (see `auth.py`'s own
docstring). `import_world.py` (the one-time world-manifest importer) needs
`ensure_world_node_types` too, and it is explicitly written NOT to import
anything that pulls in that auth-pepper chain — that is why it used to import
`ingest_world.py` directly (which only imports `models_graph`) instead of
`ingest_graph.py` (which transitively imports `ingest_backlog.py`, which
imports `app`). Putting the registry seeds in `seed.py` would reintroduce
exactly the entanglement `import_world.py`'s own docstring warns about, for
the module whose whole point is running standalone with no env vars set. This
module imports only `models_graph` — same discipline `ingest_world.py` used to
follow — so both `app.py` and `import_world.py` can depend on it safely.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from models_graph import Edge, EdgeType, Node, NodeType

# --------------------------------------------------------------------------- #
# Backlog (workitem-family) types — epic / story / task.
#
# Moved verbatim from ingest_graph.py's NODE_TYPE_SEEDS (TASK-858/1058).
# `family` (ADR 0019 Addendum, TASK-858) declares the workitem family.
# `requires_edge` used to declare the required-world-link constraint; it is
# UNSET as of TASK-1025 step 5 and the validator is deleted — see the block
# where it stood in models_graph.py for why (it was non-fatal, so it enforced
# nothing, and `serves` satisfied it without a world object existing).
#
# `rank` (TASK-1389) is the type's rung on the work hierarchy, top first: epic
# 0, story 1, task 2. It is NOT a `node_type` column (`ensure_backlog_node_types`
# leaves it out of the row); it only feeds the derived tuples below the seeds.
# A workitem-family type that is not a rung (ADR 0048's `decision`) gets none.
# --------------------------------------------------------------------------- #

# Minimal, permissive JSON-Schema per backlog node type. additionalProperties
# stays True: backlog-derived attributes vary field to field (status/
# depends_on/links/etc.) — only shape-check the couple of fields anything
# actually reads.
_BACKLOG_ATTRS_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": ["string", "null"]},
        "depends_on": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": True,
}

NODE_TYPE_SEEDS: dict[str, dict] = {
    "epic": {
        "attributes_schema": _BACKLOG_ATTRS_SCHEMA,
        "rank": 0,
        "agent_instructions": "A top-level body of work in the AgentPlanningSetup backlog. "
                               "Contains stories via `contains` edges.",
        "status": "approved",
        "family": "workitem",
        # `requires_edge` is deliberately UNSET (TASK-1025 step 5). Seeds update
        # every field in place on each boot, so leaving it out clears the live
        # rows. See models_graph.py where the validator was deleted for why.
        "requires_edge": None,
    },
    "story": {
        "attributes_schema": _BACKLOG_ATTRS_SCHEMA,
        "rank": 1,
        "agent_instructions": "A story under an epic. Contains tasks via `contains` edges.",
        "status": "approved",
        "family": "workitem",
        # `requires_edge` is deliberately UNSET (TASK-1025 step 5). Seeds update
        # every field in place on each boot, so leaving it out clears the live
        # rows. See models_graph.py where the validator was deleted for why.
        "requires_edge": None,
    },
    "task": {
        "attributes_schema": _BACKLOG_ATTRS_SCHEMA,
        "rank": 2,
        "agent_instructions": "A single unit of backlog work. May `depends_on` other tasks.",
        "status": "approved",
        "family": "workitem",
        # `requires_edge` is deliberately UNSET (TASK-1025 step 5). Seeds update
        # every field in place on each boot, so leaving it out clears the live
        # rows. See models_graph.py where the validator was deleted for why.
        "requires_edge": None,
    },
}

# Seed keys that are code-only facts about a type, not `node_type` columns.
_SEED_ONLY_KEYS = ("rank",)

#: Every workitem-family type: the ladder rungs in rank order, then any type
#: without a rank in seed order. `deps.WORKITEM_TYPES` is this tuple, and so is
#: `BACKLOG_TYPES` (TASK-1389: one source, no hand-typed copies).
WORKITEM_TYPES: tuple[str, ...] = tuple(sorted(
    (t for t, seed in NODE_TYPE_SEEDS.items() if seed["family"] == "workitem"),
    key=lambda t: (NODE_TYPE_SEEDS[t].get("rank") is None, NODE_TYPE_SEEDS[t].get("rank") or 0),
))
BACKLOG_TYPES = WORKITEM_TYPES

#: The work hierarchy ladder, top first: only the types that have a `rank`.
#: `deps.WORK_HIERARCHY_RANK` and `/api/vocabulary`'s `workitem_ladder` read it.
WORKITEM_LADDER: tuple[str, ...] = tuple(t for t in WORKITEM_TYPES if NODE_TYPE_SEEDS[t].get("rank") is not None)


def ensure_backlog_node_types(s: Session) -> None:
    """Idempotently make sure epic/story/task node_type rows exist (insert-if-
    missing; update every seeded field in place if they already exist, so an
    edit to the schema above takes effect on the next boot)."""
    existing = {nt.id: nt for nt in s.scalars(select(NodeType).where(NodeType.id.in_(NODE_TYPE_SEEDS))).all()}
    for type_id, seed in NODE_TYPE_SEEDS.items():
        seed = {k: v for k, v in seed.items() if k not in _SEED_ONLY_KEYS}
        if type_id in existing:
            nt = existing[type_id]
            for k, v in seed.items():
                setattr(nt, k, v)
        else:
            s.add(NodeType(id=type_id, **seed))
    s.flush()


# --------------------------------------------------------------------------- #
# World (object/bridge-family) types — moved verbatim from ingest_world.py's
# OBJECT_NODE_TYPE_SEEDS / BRIDGE_NODE_TYPE_SEEDS / ensure_world_node_types
# (ADR 0019 Addendum, TASK-858/1058). `import_world.py` (the one-time
# world-manifest importer) also imports this module's schema/type constants
# for its own `partition_valid_nodes` — see that module's docstring for why it
# does not import `ensure_world_node_types` from anywhere else either.
# --------------------------------------------------------------------------- #
OBJECT_TYPES = ("org", "project", "system", "service", "component", "agent", "db", "host",
                "capability")
# `goal` is RETIRED (ADR 0032, 2026-08-14). Its two instances were milestones,
# not world nodes: both had an empty `body`, both were `contains`-ed by the same
# node their `for` edge pointed at, and `goal-live-object-graph` sat at level 2
# of the `atlas` root beside the layer words while its abstract documented its
# own wiring — for an urgency propagation that never happened (`attention.py`
# derives its goal set from every `epic`, ignoring the type). What people wanted
# from it is `node_intent`, which inherits instead of needing to be navigated to.
#
# `doc` is dead vocabulary too, retired 2026-08-13 and left seeded here at the
# time. It is deliberately NOT removed in the same change as `goal`: nothing
# breaks either way, and one retirement per commit is what keeps a bisect useful.
BRIDGE_TYPES = ("skill", "doc", "vision")

# `node.name` carries TWO registers, and which one a node uses is a property of
# its TYPE, not a style preference:
#
#   prose      a concept named for a human — "Board shell", "Graph engine",
#              "Atlas frontend". Sentence case, so it starts uppercase.
#   identifier the exact string the thing answers to somewhere else —
#              `atlas-api`, `atlas-postgres`, `atlas-mcp`, `reverse-proxy`.
#              You can paste it into `docker exec` and it works.
#
# Only the prose half has a rule worth checking. Capitalising an identifier
# BREAKS it ("Atlas-api" is not a binary, "Atlas-postgres" is not a
# container), which is why this is a report over the types below and NOT a
# transform in the board — see `name_register_warnings` at the foot of this
# module (it lives here, not in `models_graph`, because this module imports
# `models_graph` and not the reverse — see the module docstring).
#
# `service` is deliberately absent even though most of its names look prose:
# the uppercase ones (PostgreSQL, Grafana) are uppercase
# because that IS the product's name, and the lowercase ones (`atlas-api`,
# `atlas-postgres`, `reverse-proxy`) are container names. No rule separates them, so
# asserting one would produce noise, and a check that cries wolf is a check
# people learn to ignore (the `/api/health` lesson from TASK-1057/1058).
# `agent`, `db`, `skill` and `doc` are exempt for the same reason.
#
# Workitem types (`epic`/`story`/`task`/…) are out of scope: their names are
# free prose authored per item, ~323 of them, and the drift this catches is a
# world-map legibility problem — which is where it was reported.
PROSE_NAME_TYPES = ("org", "project", "system", "component", "capability", "host",
                    "goal", "vision")

# Intent kinds (ADR 0032). The kind does NOT classify what the statement is
# about — it says what an agent must DO when reality disagrees with it:
#
#   constraint  a bug        — this is required and is not there
#   principle   a defect     — this is true everywhere below and is not true here
#   non-goal    a removal    — something was built that should not have been
#   outcome     NOTHING      — direction, not specification; generates no work
#
# `outcome` producing nothing is load-bearing, not a gap. It is what lets a
# statement like "plan, structure and track larger AI projects" be recorded as
# true without spawning fifty multi-tenancy tasks on the day it is written.
# An agent reading it as a `constraint` builds multi-tenancy; reading it as an
# `outcome` it builds the simple thing and declines to hard-code one account.
#
# A TUPLE, not a `node_type`-style registry table (owner decision, 2026-08-13). A
# `node_type` row carries a JSON-Schema, a health formula, probe config and
# agent instructions; a kind carries a name and the semantic above. A table
# for that is ceremony, and adding a kind should stay a one-line edit.
#
# Deliberately NOT copied into the frontend, unlike `OBJECT_TYPES` in
# `state.js`. The panel derives its chip class from the kind STRING
# (`"k-" + kind`) and `board.css` carries a neutral fallback, so a kind added
# here renders sanely on the board with no frontend edit — it only needs one
# if you want it to have its own colour. `/api/intents` also returns the
# vocabulary in its `kinds` field, so a client that needs the list can ask
# rather than hardcode it.
INTENT_KINDS = ("constraint", "principle", "non-goal", "outcome")

_OBJECT_AGENT_INSTRUCTIONS_OVERRIDES: dict[str, str] = {
    "db": "A database service node; document schema, who writes it, and backup/restore posture.",
    "agent": "An autonomous agent node; document its decision loop, tools/MCP surface, model routing, and guardrails.",
    # `capability` (ADR 0030) is the level between a layer and the things that
    # run: a durable area of the product that work attaches to. The test that
    # separates it from an epic is that it OUTLIVES the work — if it is finished
    # when the epic is finished, it is an epic, not a node.
    "capability": "A durable capability of a project — an area of the product that work attaches to, "
                  "as opposed to a process that runs (`service`) or a machine (`host`). Document what it "
                  "owns, and list any sub-components that have not yet earned nodes of their own.",
}

# attributes.docs[] — heavy-artifact links (ADR 0020: node documentation
# standard, TASK-864). Metadata only; the content stays in the linked file.
_DOCS_KIND_ENUM = ["architecture", "api", "integration", "skill", "design", "runbook", "adr"]
_DOCS_FORMAT_ENUM = ["mermaid", "openapi", "md", "svg"]
_DOCS_ITEM_SCHEMA = {
    "type": "object",
    "required": ["kind", "title", "path"],
    "properties": {
        "kind": {"type": "string", "enum": _DOCS_KIND_ENUM},
        "title": {"type": "string"},
        "path": {"type": "string"},
        "format": {"type": "string", "enum": _DOCS_FORMAT_ENUM},
        "external": {"type": "boolean"},
    },
    "additionalProperties": False,
}

# Minimal, permissive JSON-Schema per world node type — mirrors
# `_BACKLOG_ATTRS_SCHEMA` above: only shape-check the fields v1 actually reads
# (the static `health` hint, and `docs[]` per ADR 0020).
_WORLD_ATTRS_SCHEMA = {
    "type": "object",
    "properties": {
        "health": {"type": ["string", "null"]},
        "docs": {"type": "array", "items": _DOCS_ITEM_SCHEMA},
    },
    "additionalProperties": True,
}

# A stub — the real probe-driven formula is STORY-atlas-node-types-probes; for
# now this just documents that v1 reads a static hint (attention.object_health).
_HEALTH_FORMULA_STUB = {"kind": "static-hint", "field": "attributes.health"}

OBJECT_NODE_TYPE_SEEDS: dict[str, dict] = {
    t: {
        "family": "object",
        "attributes_schema": _WORLD_ATTRS_SCHEMA,
        "health_formula": _HEALTH_FORMULA_STUB,
        "probe_config": {"kind": None, "note": "no probe wired yet — static health hint only (v1)"},
        "agent_instructions": f"A `{t}` object in the Atlas world tree (ADR 0019 Addendum). "
                               "Health rolls up worst-child over `contains`.",
        "status": "approved",
    }
    for t in OBJECT_TYPES
}
for _t, _instr in _OBJECT_AGENT_INSTRUCTIONS_OVERRIDES.items():
    OBJECT_NODE_TYPE_SEEDS[_t]["agent_instructions"] = _instr

BRIDGE_NODE_TYPE_SEEDS: dict[str, dict] = {
    t: {
        "family": "bridge",
        "attributes_schema": _WORLD_ATTRS_SCHEMA,
        "health_formula": _HEALTH_FORMULA_STUB,
        "probe_config": None,
        "agent_instructions": f"A `{t}` bridge node — connective tissue a workitem/object "
                               "attaches to (ADR 0019 Addendum).",
        "status": "approved",
    }
    for t in BRIDGE_TYPES
}
WORLD_NODE_TYPE_SEEDS: dict[str, dict] = {**OBJECT_NODE_TYPE_SEEDS, **BRIDGE_NODE_TYPE_SEEDS}


def ensure_world_node_types(s: Session) -> None:
    """Idempotently make sure the object/bridge node_type rows exist
    (insert-if-missing; update every seeded field in place otherwise) — same
    pattern as `ensure_backlog_node_types` above."""
    existing = {nt.id: nt for nt in s.scalars(select(NodeType).where(NodeType.id.in_(WORLD_NODE_TYPE_SEEDS))).all()}
    for type_id, seed in WORLD_NODE_TYPE_SEEDS.items():
        if type_id in existing:
            nt = existing[type_id]
            for k, v in seed.items():
                setattr(nt, k, v)
        else:
            s.add(NodeType(id=type_id, **seed))
    s.flush()


def ensure_all_node_types(s: Session) -> None:
    """Both node registries plus the edge registry, in one call — used by
    verify scripts and by anything standing a graph up outside `app.startup()`
    (which seeds all three itself, see that function). Kept as separate
    functions above/below (not just this one) because `import_world.py` only
    ever needed the world half and must not seed backlog types as a side
    effect of running standalone."""
    ensure_backlog_node_types(s)
    ensure_world_node_types(s)
    ensure_edge_types(s)


# --------------------------------------------------------------------------- #
# edge_type registry — the EDGE-side mirror of NODE_TYPE_SEEDS/WORLD_NODE_TYPE_
# SEEDS above. See `models_graph.EdgeType`'s class comment for the schema
# rationale (why `pairs` is a list, why this is read-only in effect).
#
# Ten rows: the seven edge types with live rows on 2026-08-26 (measured via
# `SELECT edge_type, count(*) FROM edge GROUP BY 1`) as `active`, `requires`
# (active, added by ADR 0044 on 2026-10-24), plus `serves` and `for` as `dead`
# — retired vocabulary kept for history, not deleted, because old docs and
# ADRs still name them.
#
# Descriptions are mined from `app.py`'s `EDGE_FAMILY_RULES` comment
# (`contains`/`works_on`/`uses`/`instance_of`), AGENTS.md's world-model table
# (`contributes_to`/`hosted_on`), and ADR 0030/0032 for `depends_on`/`serves`/
# `for` — not invented. Where `EDGE_FAMILY_RULES` already declares a pair for
# a type, the pair here AGREES with it (see the module docstring's own note
# on this — checked, not assumed).
EDGE_TYPE_SEEDS: dict[str, dict] = {
    "contains": {
        "pairs": [["object", "object"], ["workitem", "workitem"], ["object", "bridge"]],
        "description": (
            "Is part of, and nothing else (ADR 0030) — the product decomposition. "
            "Three pairs share this one string, by decision: the work hierarchy "
            "(epic contains story contains task) has no edge type of its own. "
            "object/object is the world hierarchy, workitem/workitem is the work "
            "hierarchy, and object/bridge is a capability containing a skill "
            "(e.g. a capability -> its `skill-*` node — measured live "
            "2026-08-26, and the only row using `skill`, the sole bridge-family "
            "type with any live rows at all). The original seed only carried the "
            "first two pairs and was corrected against live's real data."
        ),
        "status": "active",
    },
    "works_on": {
        "pairs": [["workitem", "object"]],
        "description": (
            "A workitem's binding to the world object it is done against. Every "
            "effective binding in the live graph travels on this edge type — see "
            "AGENTS.md's warning about `serves` reintroducing a double-count if "
            "that changes."
        ),
        "status": "active",
    },
    "depends_on": {
        "pairs": [["workitem", "workitem"]],
        "description": (
            "One workitem blocks another. In heavy use (333 rows measured "
            "2026-08-26) but was declared nowhere until this table — "
            "`EDGE_FAMILY_RULES` is a validation table for `POST /api/edge`, not "
            "a vocabulary, and never covered it."
        ),
        "status": "active",
    },
    "hosted_on": {
        "pairs": [["object", "object"]],
        "description": (
            "Where a service or db RUNS, as opposed to what it is PART OF "
            "(`contains`). Both ends are object-family nodes (a `host` is an "
            "object type too)."
        ),
        "status": "active",
    },
    "requires": {
        "pairs": [["object", "object"]],
        "description": (
            "A HARD run-time dependency between world objects: src does not work "
            "without dst (ADR 0044 §7) — e.g. `atlas-api --requires--> "
            "atlas-postgres`. Covers calls, stores-in and proxies-through. Not "
            "`depends_on`, which orders WORKITEMS and is read by the blocked-work "
            "signals; not `uses`, which is project -> project. A soft dependency "
            "(works worse without it) stays in prose."
        ),
        "status": "active",
    },
    "contributes_to": {
        "pairs": [["object", "object"]],
        "description": (
            "Party contributes to a project — MANY-TO-MANY. Ownership is an edge, "
            "not containment (ADR 0027); a second contributor is one more row."
        ),
        "status": "active",
    },
    "uses": {
        "pairs": [["object", "object"]],
        "description": (
            "One project stands on another without being owned by it — e.g. "
            "`project-a --uses--> project-b`. Object -> object, like "
            "`contains`, but not part-of."
        ),
        "status": "active",
    },
    "instance_of": {
        "pairs": [["object", "object"]],
        "description": (
            "A deployment points at what it deploys, instead of forking the "
            "product tree beside itself (ADR 0030) — e.g. a staging stack is an "
            "INSTANCE of the project it stages, not a sibling of it."
        ),
        "status": "active",
    },
    "serves": {
        "pairs": [["workitem", "object"]],
        "description": (
            "Retired sibling of `works_on` for the workitem -> object binding. "
            "Zero live rows, and declared DEAD deliberately: AGENTS.md records "
            "that every binding travels on `works_on` now, and a single `serves` "
            "row brings the rollup double-count hazard ADR 0028 exists to "
            "prevent straight back."
        ),
        "status": "dead",
    },
    "for": {
        "pairs": [["bridge", "object"]],
        "description": (
            "A `goal` node's pointer to the node it was about (ADR 0032). `goal` "
            "itself is retired (see BRIDGE_TYPES above); zero rows, kept as dead "
            "vocabulary because older ADRs and docs still reference it."
        ),
        "status": "dead",
    },
}


def ensure_edge_types(s: Session) -> None:
    """Idempotently make sure the ten seeded `edge_type` rows exist
    (insert-if-missing; update every seeded field in place if they already
    exist) — same pattern as `ensure_backlog_node_types`/
    `ensure_world_node_types` above, so an edit to the seeds here takes effect
    on the next boot rather than needing a migration."""
    existing = {et.id: et for et in s.scalars(select(EdgeType).where(EdgeType.id.in_(EDGE_TYPE_SEEDS))).all()}
    for type_id, seed in EDGE_TYPE_SEEDS.items():
        if type_id in existing:
            et = existing[type_id]
            for k, v in seed.items():
                setattr(et, k, v)
        else:
            s.add(EdgeType(id=type_id, **seed))
    s.flush()


def edge_type_warnings(s: Session) -> list[dict]:
    """Drift between the `edge_type` registry and what `edge` actually holds.

    Shaped like `name_register_warnings`/`auth.token_warnings`: a list that is
    normally empty, never a boolean that is permanently green — see that
    function's docstring for why (`/api/health`'s `ingest_*` fields were
    deleted in TASK-1058 for being exactly the failure mode this avoids).

    FOUR conditions. The first three compare edge_type STRINGS between `edge`
    and this registry; the fourth compares FAMILIES, which is a materially
    different question — a type can be perfectly declared by name and still
    disagree about what it connects. It was added after the first pass shipped
    with `contains` missing the object/bridge pair a real live row already
    used (a capability -> its `skill-*` node), found only by hand-written
    SQL, which is exactly what this condition exists to make unnecessary next
    time.

      * `used_but_undeclared` — an `edge.edge_type` value with no row here at
        all. Real drift: something is writing an edge type this registry has
        never heard of.
      * `declared_active_zero_rows` — an `active` type with no live edges.
        Dead vocabulary nobody has retired yet (the `doc`-in-`BRIDGE_TYPES`
        shape, one table over).
      * `declared_dead_with_rows` — the hazard this table exists for. `serves`
        is the reason: a `dead` type gaining even one row is the rollup
        double-count regressing, not a curiosity, and the message says so.
      * `pair_not_declared` — a `(edge_type, src_family, dst_family)`
        combination that genuinely exists in `edge` but is absent from that
        type's declared `pairs`. Only checked for a type that IS declared —
        an undeclared type is already reported above, and reporting it twice
        under two reasons would double the drift for one root cause. Carries
        an example edge (id/src/dst) so it is actionable without a second
        query.
    """
    out: list[dict] = []
    declared = {et.id: et for et in s.scalars(select(EdgeType)).all()}
    counts = {edge_type: count for edge_type, count in
              s.execute(select(Edge.edge_type, func.count()).group_by(Edge.edge_type)).all()}

    for edge_type in sorted(set(counts) - set(declared)):
        out.append({
            "edge_type": edge_type,
            "reason": "used_but_undeclared",
            "count": counts[edge_type],
        })

    for type_id in sorted(declared):
        et = declared[type_id]
        count = counts.get(type_id, 0)
        if et.status == "active" and count == 0:
            out.append({"edge_type": type_id, "reason": "declared_active_zero_rows"})
        elif et.status == "dead" and count > 0:
            out.append({
                "edge_type": type_id,
                "reason": "declared_dead_with_rows",
                "count": count,
                "detail": (
                    "declared dead but has live rows — a binding must travel on "
                    "`works_on` alone, or the rollup double-count hazard ADR 0028 "
                    "exists to prevent comes back (AGENTS.md)."
                ),
            })

    # `pair_not_declared` — join each edge to the FAMILY of its src and dst
    # node (via node_type), group by (edge_type, src_family, dst_family), and
    # flag any combination a declared type's `pairs` does not list.
    SrcNode, DstNode = aliased(Node), aliased(Node)
    SrcType, DstType = aliased(NodeType), aliased(NodeType)
    pair_counts = s.execute(
        select(Edge.edge_type, SrcType.family, DstType.family, func.count())
        .select_from(Edge)
        .join(SrcNode, SrcNode.id == Edge.src)
        .join(SrcType, SrcType.id == SrcNode.type)
        .join(DstNode, DstNode.id == Edge.dst)
        .join(DstType, DstType.id == DstNode.type)
        .group_by(Edge.edge_type, SrcType.family, DstType.family)
        .order_by(Edge.edge_type, SrcType.family, DstType.family)
    ).all()
    for edge_type, src_family, dst_family, count in pair_counts:
        et = declared.get(edge_type)
        if et is None:
            continue  # already reported as used_but_undeclared above
        declared_pairs = [tuple(p) for p in (et.pairs or [])]
        if (src_family, dst_family) in declared_pairs:
            continue
        example = s.execute(
            select(Edge.id, Edge.src, Edge.dst)
            .select_from(Edge)
            .join(SrcNode, SrcNode.id == Edge.src)
            .join(SrcType, SrcType.id == SrcNode.type)
            .join(DstNode, DstNode.id == Edge.dst)
            .join(DstType, DstType.id == DstNode.type)
            .where(Edge.edge_type == edge_type, SrcType.family == src_family,
                   DstType.family == dst_family)
            .order_by(Edge.id)
            .limit(1)
        ).first()
        out.append({
            "edge_type": edge_type,
            "reason": "pair_not_declared",
            "src_family": src_family,
            "dst_family": dst_family,
            "count": count,
            "example_edge": (
                {"id": example.id, "src": example.src, "dst": example.dst}
                if example is not None else None
            ),
            "detail": (
                f"{count} edge(s) of type {edge_type!r} run ({src_family!r} -> "
                f"{dst_family!r}), which is not among its declared pairs "
                f"{declared_pairs} — the registry does not describe what the "
                "data actually holds."
            ),
        })
    return out


def name_register_warnings(s: Session) -> list[dict]:
    """World nodes whose `name` should be prose but starts lowercase.

    The board renders `node.name` verbatim, and it must keep doing so: an
    uppercase-first transform in `map.js` would corrupt every identifier-register
    name (see `PROSE_NAME_TYPES` above), and it would make the map disagree with
    the panel, the API and search — the same string reading two ways depending
    on where you look is how a debugging session gets eaten.

    So this REPORTS instead. It is shaped like `auth.token_warnings`: a list
    that is normally empty, not a boolean that is permanently green. That
    distinction is the whole reason `/api/health`'s ingest fields were deleted
    in TASK-1058 — a field which can never change state is one people learn to
    ignore. This one empties when the names are fixed and refills only if
    someone creates a world node from its slug.

    Non-Latin and digit-leading names are NOT flagged: `str.isupper()` is false
    for both, and neither is the drift this looks for.
    """
    out: list[dict] = []
    rows = s.scalars(
        select(Node).where(Node.type.in_(PROSE_NAME_TYPES)).order_by(Node.id)
    ).all()
    for row in rows:
        # The baseload's GENERATED nodes (system_node.py, baseload_data.py) are named by the
        # identifier they answer to (`atlas_get`, `atlas-admin user`) on purpose: decision D-B2 of
        # agent/plans/oss-baseload.md. Their names come from the code, not from a person, so a
        # report here would be one nobody can act on. Every other node is checked as before.
        attrs = row.attributes if isinstance(row.attributes, dict) else {}
        baseload = attrs.get("baseload")
        if isinstance(baseload, dict) and baseload.get("source") == "generated":
            continue
        name = (row.name or "").strip()
        if not name:
            out.append({"id": row.id, "type": row.type, "name": row.name, "reason": "empty_name"})
            continue
        first = name[0]
        if first.isalpha() and not first.isupper():
            out.append({"id": row.id, "type": row.type, "name": name, "reason": "prose_name_lowercase"})
    return out
