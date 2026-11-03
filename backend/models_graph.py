"""
Atlas v2 — the typed object-graph schema (ADR 0019).

`node` / `node_type` / `edge` / `probe` / `probe_result`: a flexible-schema world
model that sits ALONGSIDE the existing `component`/`gate`/`requirement` tables
from app.py (v0). Nothing here touches those tables — v1 and v0 coexist in the
same database until a later story migrates the seeded architecture map too
(see ADR 0019 "What 0019 supersedes in 0017").

Design notes (see the report back to the reviewer for the full rationale):
  * `Node.attributes` (and every other "arbitrary JSON" column) uses
    `postgresql.JSONB` (`JSONType`, below) — Postgres is the only dialect
    this schema runs against; see AGENTS.md's "Atlas stores its data in
    POSTGRES" note.
  * `node.attributes` is validated against the owning `node_type.attributes_schema`
    (JSON-Schema, via the `jsonschema` package) on every flush — enforced with a
    SQLAlchemy `before_flush` event so it fires for ANY session/write path (the
    backlog ingester, a future API, a REPL), not just one call site.
  * `node.id` reuses the backlog's own human-readable ids (`TASK-850`,
    `STORY-atlas-object-graph`, `EPIC-project-atlas`, ...) rather than a
    surrogate integer/UUID. That keeps ingestion idempotent by natural key and
    keeps `source` (git path) and `id` correlated for humans reading the DB
    directly. It lives in a different table (`node`, not `component`) so there
    is no id-collision risk with the old WBS-style Component ids
    (`Org-100000`, `Project-110000`, ...).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Engine,
    Float,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    event,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

try:
    import jsonschema
except ImportError:  # pragma: no cover - surfaced at validation time with a clear message
    jsonschema = None


# --------------------------------------------------------------------------- #
# JSONB — Postgres is the only dialect this schema runs against (TASK-1044),
# so this used to be `JSON().with_variant(JSONB(), "postgresql")` (the
# standard SQLAlchemy cross-dialect recipe, needed while the verify harness
# also ran on SQLite) is now just `JSONB()` directly. Verified before
# collapsing it, not assumed: `CreateTable(...).compile(dialect=postgresql
# .dialect())` emits byte-identical DDL for both forms — the `.with_variant`
# wrapper was a no-op on the only dialect that ever mattered here.
# --------------------------------------------------------------------------- #
JSONType = JSONB()


# --------------------------------------------------------------------------- #
# Full-text search (TASK-1050) — "what replaces `grep -r backlog/`". Postgres
# `tsvector` + a GIN index on the SAME expression, declared here as raw SQL
# text so the index (below, in `Node`/`AcceptanceCriterion.__table_args__`)
# and the query (`app._search_node_ids`) stay textually identical — the
# surest way for the planner to actually recognise the index covers the
# query, and the surest way to avoid one of the two silently drifting out of
# sync with the other over time.
#
# `to_tsvector` is Postgres SQL; `app._search_node_ids` uses it directly.
# --------------------------------------------------------------------------- #
NODE_BODY_SEARCH_SQL = "to_tsvector('english', coalesce(name,'') || ' ' || coalesce(body,''))"
AC_TEXT_SEARCH_SQL = "to_tsvector('english', text)"

# TASK-1156 — the fifth source: a node's AUTHORED prose tiers. World nodes keep
# their context in `abstract`/`detail`, not `body`: measured on live
# 2026-09-14, `sphere-device-heating` had a 5.6 KB abstract, no body and no
# documents, so "radiator" — a word its abstract is about — found nothing, and
# "heating" found it only through its NAME. A separate expression and index
# rather than a widening of NODE_BODY_SEARCH_SQL, because changing that string
# would orphan `ix_node_body_search_gin` (CREATE INDEX IF NOT EXISTS cannot
# replace an index, and dropping one is not boot's job).
NODE_PROSE_SEARCH_SQL = ("to_tsvector('english', coalesce(summary,'') || ' ' || "
                         "coalesce(abstract,'') || ' ' || coalesce(detail,''))")

# TASK-1063 — same expression as AC_TEXT_SEARCH_SQL (both tables have a plain
# `text` column), but declared as ITS OWN constant rather than reused, because
# the byte-for-byte-identical rule this file already lives by (see the block
# comment above) is about the SQL STRING and the QUERY matching each other for
# a given table/index pair, not about two unrelated tables sharing a spelling.
# `app._search_node_ids` and `ensure_search_indexes` below both reference this
# name, not AC_TEXT_SEARCH_SQL, so `workitem_result` moving to a differently-
# shaped text representation later (unlikely, but see the AC one) cannot
# silently also change what the result-search index covers.
WORKITEM_RESULT_SEARCH_SQL = "to_tsvector('english', text)"

# node_doc — the fourth search source (STORY-atlas-search-node-docs). Same
# "own constant, same byte-for-byte rule" reasoning as WORKITEM_RESULT_SEARCH_SQL
# above: `node_doc` happens to have a plain `content` column shaped the same
# way, but it is declared separately so it can drift independently.
#
# `app._search_node_ids` and `ensure_search_indexes` below both reference this
# name for the INDEXED tsvector expression. The visibility half of "may this
# match surface" is a SEPARATE predicate — NODE_DOC_SERVED_SQL, below — and is
# deliberately NOT folded into this constant: the GIN index only needs to
# cover the tsvector match, and the served-or-not check does not benefit from
# (and would not be helped by) being part of the same expression.
NODE_DOC_SEARCH_SQL = "to_tsvector('english', content)"

# workitem_comment — the eighth search source. Own constant, same byte-for-byte rule as the
# others: `ensure_search_indexes` and `deps._search_hits` both reference this name.
WORKITEM_COMMENT_SEARCH_SQL = "to_tsvector('english', body)"

# STORING IS NOT SERVING (TASK-1076, `app.put_node_doc`'s own docstring) — a
# `node_doc` row can exist with no `docs[]` allowlist entry on its owning
# node ("expected-not-served", 11 such rows on live as of that task), and
# `GET /api/node/{id}/doc` refuses to return its content until one is added.
# Search must refuse it for the SAME reason: a caller who cannot GET a
# document must not be able to learn it exists, or read a snippet of it,
# through the search box instead — that would make the allowlist decorative.
#
# So a node_doc row is a search HIT only when its `path` appears in the
# owning node's `attributes.docs[]` as a non-external entry — mirroring
# `app.node_doc`'s own two checks (`entry is None` -> 404,
# `entry.get("external")` -> not served, content withheld) exactly. This is
# a correlated EXISTS against `node`, not a join column on `node_doc`
# itself, because the allowlist lives in JSONB on the OWNING node and can
# change without touching the document's own row.
#
# `coalesce((d ->> 'external')::boolean, false)` treats a MISSING `external`
# key the same as `false` (served) — the same default `entry.get("external")`
# gives in Python. `->>` on a non-object JSONB array element (a malformed
# `docs[]` entry that is a bare string, say) returns NULL rather than
# raising, so a hand-edited or malformed entry degrades to "not this one",
# never to a 500.
NODE_DOC_SERVED_SQL = (
    "EXISTS (SELECT 1 FROM node n, "
    "jsonb_array_elements(coalesce(n.attributes -> 'docs', '[]'::jsonb)) AS d "
    "WHERE n.id = node_doc.node_id AND d ->> 'path' = node_doc.path "
    "AND coalesce((d ->> 'external')::boolean, false) = false)"
)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# Provenance (ADR 0019 -> TASK-1016; REDEFINED TASK-1058).
#
# `node.origin` / `edge.origin` used to answer "who is allowed to delete this
# row on the next ingest" for the four boot-time reconcilers
# (`ingest_graph._reconcile_nodes`/`_reconcile_edges`,
# `ingest_world._reconcile_world_nodes`/`_reconcile_world_edges`). All four
# are DELETED (TASK-1058, STORY-atlas-retire-status-roundtrip) — nothing at
# boot reads a backlog file or the world manifest any more (see AGENTS.md's
# "Shipped 2026-08-10: the backlog lives in Postgres"), so nothing branches on
# this column to decide whether a row survives a restart any more either.
#
# THE COLUMN STAYS. Decision recorded here, not dropped silently: `origin`
# still records an honest fact — HOW a row first entered the database
# (`"git"` = written by one of the now-deleted file ingests, historically;
# `"atlas"` = written by the write API or a one-time importer). That is real
# provenance, it costs nothing to keep, and dropping it would be an `ALTER
# TABLE ... DROP COLUMN` against a populated Postgres for no operational gain
# — the kind of schema change AGENTS.md says needs the same deliberateness as
# the `status_override` drop (`backend/migrate_drop_status_override.py`,
# the owner's own call), not a side effect of an unrelated cleanup task. Measured
# before deciding: `node` is 358/358 `"atlas"` on live today (every workitem
# and every world node has been imported through `import_workitems.py`/
# `import_world.py`), but `edge` is 797 `"git"` / 10 `"atlas"` — those 797
# rows are exactly what the now-deleted reconcilers wrote while they still
# ran, and `"git"` is still the true, honest description of how they got
# there. Do not write a migration, a comment, or a health check that assumes
# every row (or every edge) carries the same value.
#
# WHAT STILL READS IT. `app.py`'s `edit_node`/the workitem PATCH route still
# check `origin_is_git_value(n.origin)` BEFORE flipping a node to `"atlas"`,
# to report `detached_from_git: true` in the response when an edit is the
# first one to touch a row that arrived via the old file ingest — that is
# real, still-true information for a caller ("this row used to track a file;
# your edit is what detached it"), not reconciler bookkeeping, and it needs
# no reconciler to keep meaning it. Nothing else branches on `origin` any
# more.
# --------------------------------------------------------------------------- #
def origin_is_git_value(value: str | None) -> bool:
    """True for `"git"` or `NULL` (the pre-`origin`-column default, backfilled
    to `"git"` by `app._backfill_graph_origin` — see that function's
    docstring). Historically the Python-side twin of a SQL `origin_is_git()`
    filter used by the four reconcilers above to decide what they were allowed
    to delete; that filter is gone with them, but this predicate is still used
    for its ORIGINAL, narrower purpose — see this column's docstring.
    """
    return value is None or value != "atlas"


# No CHECK constraint on origin (`git`|`atlas`) even though the column is
# two-valued: `_reconcile_graph_schema()` (app.py) only ever emits a bare
# `ALTER TABLE ... ADD COLUMN`, never `ADD CONSTRAINT` — so a CheckConstraint
# declared here would apply to every freshly-`create_all`'d table (tests, a
# hypothetical fresh install) but silently NOT reach the one deploy that
# matters, the already-populated live Postgres. An enforced-in-dev,
# unenforced-in-prod constraint is worse than no constraint (it would pass
# every local check and lull a reviewer), so it is deliberately left off.
# `origin_is_git` above is what's actually load-bearing.


# --------------------------------------------------------------------------- #
# node_type — the type registry ("types as data"). A JSON-Schema per type
# validates node.attributes; status gates agent-authored types behind
# propose -> approve (ADR 0019 "Extension / authoring").
# --------------------------------------------------------------------------- #
class NodeType(Base):
    __tablename__ = "node_type"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. "task", "service"
    attributes_schema: Mapped[dict] = mapped_column(JSONType, default=dict)  # JSON-Schema
    probe_config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    agent_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    health_formula: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed|approved

    # --- Node families (ADR 0019 Addendum, TASK-858) --------------------- #
    # `workitem`  — task|story|feature|epic. Fill = attention (state ⊔ urgency).
    # `object`    — org|project|system|service|component. Fill = health().
    # `bridge`    — goal|skill|doc|vision. Connective tissue, no fill rollup
    #               role of its own beyond carrying a static health hint (v1).
    family: Mapped[str] = mapped_column(String(16), default="workitem")

    # A `workitem`-family type may declare the edge types that satisfy the
    # required-world-link constraint (ADR 0019 Addendum: "a workitem must have
    # >=1 outbound works_on or serves edge"). A list of edge_type strings, e.g.
    # ["works_on", "serves"]; null/empty = this type opts OUT of the
    # constraint entirely. Carried on the TYPE (registry, "types as data") so
    # the validator below never hard-codes which edge types satisfy it.
    requires_edge: Mapped[list | None] = mapped_column(JSONType, nullable=True)

    __table_args__ = (
        CheckConstraint("status in ('proposed','approved')", name="ck_node_type_status"),
        CheckConstraint("family in ('workitem','object','bridge')", name="ck_node_type_family"),
    )


# --------------------------------------------------------------------------- #
# edge_type — the EDGE-side mirror of `NodeType` above. Until now the edge
# vocabulary had no home in code: `EDGE_FAMILY_RULES` in app.py declares only
# four types (`contains`, `works_on`, `uses`, `instance_of`) and is a
# VALIDATION table for generic `POST /api/edge`, not a vocabulary — see the
# long comment beside it for why `contains` cannot simply gain more entries.
# Measured live 2026-08-26: `depends_on` (333 rows), `hosted_on` (23) and
# `contributes_to` (2) are in heavy use and declared nowhere; `serves` and
# `for` appear in binding filters and old docs with zero rows. This table is
# that registry, seeded by `node_types.ensure_edge_types` exactly the way
# `node_type` is seeded by `ensure_backlog_node_types`/`ensure_world_node_types`
# — schema/documentation, not enforcement. It is deliberately NOT consulted by
# `_check_family`/`EDGE_FAMILY_RULES`: this pass is read-only in effect, and
# wiring it into the write path is a separate, later decision.
#
# `pairs` is a LIST of [src_family, dst_family] pairs, not two columns,
# because `contains` genuinely has THREE valid pairs today and shares one
# string between all of them until TASK-1030 gives the work hierarchy its own
# edge type (see `WORK_HIERARCHY_EDGE_TYPE` below): `["object","object"]` for
# the world hierarchy, `["workitem","workitem"]` for the work hierarchy, and
# `["object","bridge"]` for a capability containing a skill (the
# `seat-practice` -> `skill-homelab-ops` row, measured live 2026-08-26 — the
# original seed only carried the first two and was corrected against that
# measurement). A schema that can only hold one pair per edge type cannot
# represent that.
class EdgeType(Base):
    __tablename__ = "edge_type"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. "contains", "works_on"
    # [[src_family, dst_family], ...] — see the class comment for why a LIST.
    pairs: Mapped[list] = mapped_column(JSONType, default=list)
    description: Mapped[str] = mapped_column(Text, default="")  # what the edge MEANS, one/two sentences
    # `active` = in genuine use or intended for it; `dead` = retired vocabulary
    # kept for history (`serves`, `for`). The THIRD condition the drift check
    # in node_types.py exists for is exactly this column disagreeing with
    # reality: a `dead` type with live rows is the hazard, not a curiosity —
    # `serves` reintroduces the rollup double-count AGENTS.md warns about.
    status: Mapped[str] = mapped_column(String(16), default="active")

    __table_args__ = (
        CheckConstraint("status in ('active','dead')", name="ck_edge_type_status"),
    )


# --------------------------------------------------------------------------- #
# node — the object: identity + typed attributes + layered summaries + cached
# derived signals. Attention/completion derivation is a later story (STORY-
# atlas-attention-model); the columns exist now so probes can start writing to
# them without another migration.
# --------------------------------------------------------------------------- #
class Node(Base):
    __tablename__ = "node"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    type: Mapped[str] = mapped_column(ForeignKey("node_type.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))

    # Context-economy tiers (ADR 0019 "Context economy") — humans zoom through
    # these, agents pull the cheapest tier that suffices.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The workitem record (TASK-1046): the markdown BODY, verbatim — everything
    # after the frontmatter fence, byte-for-byte, TOAST-compressed by Postgres
    # for free at this size. This is deliberately NOT a fourth context-economy
    # tier alongside summary/abstract/detail above: those three are curated,
    # bounded-length projections ADR 0020 sized for a UI panel (abstract <=250
    # words, detail "soft ~2k tokens"); `body` is the SOURCE text an importer
    # (TASK-1052) reads a task/story/epic markdown file into, unedited, so the
    # row and the file stay byte-comparable during the git-to-Postgres
    # transition. Prose revision history was considered (option C in the
    # TASK-1046 decision doc) and explicitly DEFERRED alongside the audit log
    # — this column has no history table; an edit simply overwrites it.
    #
    # NULLABLE, no backfill, same reasoning as every other column this repo
    # adds via `_reconcile_graph_schema`'s additive ALTER TABLE: on the day
    # this ships, every existing row gets `body IS NULL` (nothing has read a
    # markdown file into it yet — TASK-1052 is the importer, out of scope
    # here), and NULL is the only honest value for "no body authored". The
    # presentation projection (`app.py`'s `_graph_node_json`) treats a NULL
    # body exactly like today's NULL abstract: nothing to project, panel shows
    # "No description authored yet."
    body: Mapped[str | None] = mapped_column(Text, nullable=True)

    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # WHO IS DOING THIS (EPIC-atlas-assignment, TASK-1118). An `auth_user.id`,
    # or NULL for "unassigned" — and NULL is a real answer, not a missing one.
    #
    # THIS REPLACES `owner` FOR WORKITEMS. `owner` above is a free-text string
    # that read "Alex" on 359 of 374 workitems and joined to nothing: the
    # account it names has subject `alex`, so no query could connect the two.
    # It survives here because ~34 OBJECT-family nodes still use it for a
    # different purpose (who looks after this part of the world), and because
    # `_reconcile_graph_schema` cannot drop a column anyway. Nothing should
    # write `owner` on a workitem again — see TASK-1120.
    #
    # NOT a ForeignKey, and NOT because a dangling id would be fine. Same trap
    # `AuthUser.person_user_id` documents at length: `_reconcile_graph_schema()`
    # is the only thing that reaches the populated live Postgres and its whole
    # DDL vocabulary is `ALTER TABLE ... ADD COLUMN <type>` — no DEFAULT, no
    # CHECK, no REFERENCES. Declaring the FK here would create it on every
    # fresh test database and never on the one deployment that matters, so the
    # suite would prove a constraint live does not have. Validation lives in
    # the write path instead (`app.create_workitem` / `app.patch_workitem`
    # both refuse an id with no `auth_user` row, with a 400 naming the field).
    #
    # Resolution is by id and an id matching no row yields nothing, so the
    # fail-safe direction is "shows as unassigned", never "shows as somebody".
    assignee_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # PRIORITY AND LABELS (STORY-oss-14-strict-api). Written only through
    # `/api/workitem`, validated there (`routers/workitems._priority_labels_refusal`,
    # a 400 naming the field), and served on every node read by `_graph_node_json`.
    #
    # COLUMNS, NOT `attributes` KEYS, and the reason is search. `attributes` is
    # validated per type by a JSON-Schema and indexed only as one GIN blob, so a
    # filter on it is a JSONB containment query nobody else in this codebase
    # writes, and a priority hidden inside it would be invisible to the
    # full-text sources `deps._search_hits` reads. A typed column is filtered with
    # a plain predicate (`?priority=`), the labels array with `&&` under its own
    # GIN index (`?label=`), and `_search_hits` reads the labels as its seventh
    # source. Both are NULLABLE with no default and no CHECK, the only shape
    # `_reconcile_graph_schema` can add to the populated live table: every
    # existing row reads back `priority: null, labels: []`, which is today.
    #
    # `priority` is one of `WORKITEM_PRIORITIES` below, or NULL for "not set"
    # (there is no `none` value: NULL already says it). `labels` is a list of
    # short lowercased strings, stored NULL when empty.
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    labels: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    # PROJECT KEY (STORY-oss-12-project-keys). OPT-IN, project nodes only: a
    # project given a key (`ABC`) has the tasks filed under it minted `ABC-1`,
    # `ABC-2`, ... from its own counter (`ProjectKeyCounter` below) instead of
    # the global `TASK-` sequence. NULL is today, and every existing row reads
    # NULL, so nothing changes until somebody sets one.
    #
    # A COLUMN, NOT `attributes.key`, for the reasons `priority` is one, plus
    # two of its own: uniqueness across projects is a UNIQUE index here (two
    # concurrent PATCHes naming the same key cannot both land), and
    # `attributes` is replaced wholesale by PATCH /api/node, so a key kept there
    # could be erased by any caller rewriting some other attribute. Nullable, no
    # default, no CHECK: the shape `_reconcile_graph_schema` can add to the
    # populated live table. Written only through PATCH /api/node `key`
    # (`routers/nodes._apply_project_key`: shape, permission, uniqueness, and
    # immutability once an id has been minted with it). Served as `key`.
    project_key: Mapped[str | None] = mapped_column(String(10), nullable=True)

    source:Mapped[str | None] = mapped_column(String(300), nullable=True)  # git path/ref

    attributes: Mapped[dict] = mapped_column(JSONType, default=dict)  # type-specific, validated

    # Cached derived signals (written by the probe/attention engine — later story).
    attention: Mapped[str | None] = mapped_column(String(16), nullable=True)  # red|orange|yellow|green|purple|grey
    completion: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0..1
    attention_count: Mapped[int] = mapped_column(Integer, default=0)

    # WHEN this node reached a done status — the axis the board's "hide work
    # finished more than N days ago" filter sorts on. DERIVED, and re-derived on
    # every recompute (see attention.stamp_completion_dates):
    #   * an authored `completed:` frontmatter date always wins — it is the only
    #     source that survives a DB rebuild, and the only one that can speak
    #     about work finished before Atlas ever saw it (backfilled from git by
    #     AgentPlanningSetup/scripts/backfill-completed-dates.py);
    #   * otherwise Atlas stamps the moment it FIRST observed the node done, and
    #     never moves that stamp while it stays done — this is why the ingest is
    #     a diff-based upsert and not a purge+rebuild (see ingest_graph's
    #     docstring: "a node's identity SURVIVES a re-ingest");
    #   * a node that leaves a done status has its stamp cleared, so reopened
    #     work stops looking aged.
    completed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Provenance (TASK-1016; meaning REDEFINED TASK-1058 — see the module-level
    # comment above `origin_is_git_value`). Records HOW this row first entered
    # the database: `"git"` if it was written by one of the now-deleted
    # boot-time file ingests, `"atlas"` if it was authored via the write API
    # or a one-time importer (`import_workitems.py`/`import_world.py`).
    # Nothing branches on this to decide whether a row survives a restart any
    # more — there is no reconciler left to make that decision. NULLABLE
    # despite the Python `default="git"`: a populated Postgres can still carry
    # a legacy NULL here (backfilled to `"git"` by `app._backfill_graph_origin`
    # on every boot; see `origin_is_git_value`'s docstring for why NULL reads
    # as `git`, not as "atlas-authored").
    origin: Mapped[str | None] = mapped_column(String(16), default="git", nullable=True)

    __table_args__ = (
        Index("ix_node_attributes_gin", "attributes", postgresql_using="gin"),
        # The age filter reads this on every board load; a done-heavy backlog
        # makes it the selective column.
        Index("ix_node_completed_at", "completed_at"),
        # STORY-oss-14: `?priority=` and `?label=` on GET /api/workitems.
        Index("ix_node_priority", "priority"),
        Index("ix_node_labels_gin", "labels", postgresql_using="gin"),
        # STORY-oss-12: a key names one project. NULLs never collide in a
        # Postgres unique index, so every unkeyed node coexists.
        Index("ux_node_project_key", "project_key", unique=True),
        # NOTE: the TASK-1050 full-text GIN index over NODE_BODY_SEARCH_SQL is
        # deliberately NOT declared here — see `ensure_search_indexes()`
        # below for why a `text()`-expression Index needs to run AFTER
        # `create_all`, not as part of it.
    )


# --------------------------------------------------------------------------- #
# acceptance_criterion — the workitem record (TASK-1046), one row per
# `acceptance:` list entry. Its OWN table, not a JSON list on `node.attributes`
# — the owner's decision (queryable per-criterion beats "no schema change"; see
# the TASK-1046 mapping doc). 1,493 criteria measured across 296 files on
# `main` the day this shipped.
#
# `position` IS the ordering, and order is meaning: `acceptance:` is an
# ordered checklist in every file that carries one (up to 15 entries,
# `task-967`), not a set. Every read path must `ORDER BY position`; nothing
# here relies on insertion order or the autoincrement `id` to reconstruct it,
# because neither is guaranteed to survive a re-import (TASK-1052 rewriting a
# node's whole criterion set is a delete-and-reinsert, not an in-place patch —
# there being nothing meaningful to patch one criterion of when the source of
# truth is "line N of the list changed").
#
# DELETE BEHAVIOUR — deliberately NO `ON DELETE CASCADE`, even though this is
# a brand-new table where create_all WOULD emit that DDL for free (unlike the
# columns above, which only ever reach a populated Postgres through the
# additive-only `_reconcile_graph_schema` ALTER-TABLE path and so could never
# safely carry one). Consistency wins here: this schema's existing FK'd rows
# (`probe`/`probe_result`) are cleaned up explicitly, IN CODE, at the place a
# node is ever actually deleted — `app.py`'s `_delete_node_dependents` (the
# write API's `DELETE /api/node/{id}`) — so a multi-table deletion stays
# visible at its call site instead of splitting the story between a DDL
# clause here and application code there. `acceptance_criterion` follows the
# same convention: a bare `ForeignKey("node.id")`. (Until TASK-1058 there was
# a SECOND cleanup site, each boot-time ingest reconciler's `removed_ids`
# branch — `ingest_graph.py`, `ingest_world.py` — deleting a node whose
# backlog file/manifest entry had disappeared; both reconcilers are gone, so
# `_delete_node_dependents` is the only site left.)
# --------------------------------------------------------------------------- #
class AcceptanceCriterion(Base):
    __tablename__ = "acceptance_criterion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # No `index=True` here — the composite index below already covers a
    # plain "every criterion for this node_id" lookup (its leading column is
    # node_id), so a separate single-column index would be pure duplication.
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"))
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        # The read path always wants "every criterion for this node, in
        # order" — a single composite index serves that query directly rather
        # than sorting a `node_id`-only index's results in Python.
        Index("ix_acceptance_criterion_node_position", "node_id", "position"),
        # NOTE: the TASK-1050 full-text GIN index over AC_TEXT_SEARCH_SQL is
        # deliberately NOT declared here — see `ensure_search_indexes()`
        # below for why.
    )


# --------------------------------------------------------------------------- #
# workitem_result — the workitem record (TASK-1063), one row per body `##
# Result`/`## Outcome`/`## Results` section. Splits out of the single
# `node.body` blob for the same reason `AcceptanceCriterion` (above) already
# has its own table: `PATCH /api/workitem` replaces `body` WHOLESALE, and
# three task specs' recorded outcomes were destroyed the same way an in-line
# acceptance list would have been, before that table existed. Giving the
# result block its own row makes that particular loss structurally
# impossible — a `PATCH` that never touches `workitem_result` cannot destroy
# it, no matter what it does to `body`.
#
# Measured against the real corpus (C:\Projects\AgentPlanningSetup\backlog,
# 2026-08-10, 306 files): 221 carry exactly one result-ish heading (218
# `## Result`, 3 `## Outcome`; `Results` is matched too — see RES below — but
# 0 files in this corpus use it), never two, and 85 carry none. In 9 of the
# 221 the section is NOT the last thing in the body (e.g.
# `epic-company-formation/epic.md` has Sequencing/Key numbers/Rejected/Parked/
# "Acceptance for epic close" after it) — see `split_result` below for how
# that is handled.
# --------------------------------------------------------------------------- #
class WorkitemResult(Base):
    __tablename__ = "workitem_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Bare FK, no ON DELETE CASCADE — same call, same reason, as
    # `AcceptanceCriterion.node_id` above: this schema does multi-table
    # deletes IN CODE, at the place a node carrying this row is ever actually
    # deleted, so cleanup stays visible at its call site instead of splitting
    # the story between a DDL clause here and application code there. Wired
    # at `app._delete_node_dependents` (the write API's `DELETE
    # /api/node/{id}`) — the only such site left after TASK-1058 deleted the
    # two boot-time ingest reconcilers that used to have a matching
    # `removed_ids` branch of their own.
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"))

    # A SECTION INDEX, not a byte offset. `position` says "this was the Nth
    # `##` section in the body" (0-based), so `reassemble` can put the
    # heading back at the right SLOT even after the body around it has been
    # edited — a byte offset would instead couple this row to the exact bytes
    # of the body at import time, and go silently wrong (wrong insertion
    # point, or an exception on an out-of-range slice) the moment anything
    # before the result section changed length. `AcceptanceCriterion.position`
    # is "list order" for the same underlying reason: order is meaning, and
    # the meaning has to survive edits elsewhere in the document.
    position: Mapped[int] = mapped_column(Integer)

    # The heading line, VERBATIM — "## Result" or "## Outcome" (3 files in
    # the corpus), never normalised to a canonical name. A checker that
    # rebuilt `"## " + name` from a stored `name` field would pass its own
    # round-trip test while silently rewriting the file's own words the
    # moment it hit one of those 3 files — storing the raw line makes that
    # class of bug impossible to write by construction.
    heading: Mapped[str] = mapped_column(Text)

    # Everything after the heading line, up to (not including) the next `##`
    # heading or end-of-body, VERBATIM. See `split_result` for the exact
    # slicing rule — this is deliberately NOT trimmed of leading/trailing
    # blank lines, so `reassemble` reproduces the original body byte-for-byte.
    text: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        # Same reasoning as `ix_acceptance_criterion_node_position`: the read
        # path always wants "the result section(s) for this node, in order",
        # and today that is at most one row per node — but the index is
        # `(node_id, position)`, not `node_id` alone, on the same theory as
        # AcceptanceCriterion's: it costs nothing extra and serves an ordered
        # read directly rather than sorting in Python if that ever changes.
        Index("ix_workitem_result_node_position", "node_id", "position"),
        # NOTE: the TASK-1063 full-text GIN index over WORKITEM_RESULT_SEARCH_SQL
        # is deliberately NOT declared here — same reason as the other two
        # GIN indexes in this file; see `ensure_search_indexes()` below.
    )


# --------------------------------------------------------------------------- #
# node_doc — the 40 node documents under `docs/nodes/` (ADR 0029, amends ADR
# 0020's "artifact-file directory convention" clause). One row per file,
# content VERBATIM.
#
# `path` STOPS BEING A FILESYSTEM LOCATION AND BECOMES A STABLE DOCUMENT KEY
# (ADR 0029's own heading). It is exactly the string an `attributes.docs[]`
# entry already carries — e.g. `docs/nodes/atlas-api/architecture.md` — so
# nothing about `docs[]`'s shape, its `kind`/`format` enums, or any consumer
# of it changes. `app.node_doc` used to `open()` that string under
# `DOCS_ROOT`; it now does `select(NodeDoc).where(node_id=..., path=...)`.
# The lookup key does not care that the string still LOOKS like a path — see
# ADR 0029 "the second [check] becomes a plain namespacing convention on the
# key."
#
# STORE ALL 40, SERVE ONLY WHAT IS ALLOWLISTED (TASK-1054's own framing,
# restated in ADR 0029). This table holds a row for every file under
# `docs/nodes/` EXCLUDING `_templates/` (`import_node_docs.py`), including the
# 9 orphan `README.md` files with no `docs[]` entry pointing at them — those
# rows exist so nothing is lost, but `/api/node/{id}/doc` still refuses them
# because the allowlist check (unchanged, and still the FIRST check) never
# finds their path in any node's `docs[]`. Whether to give them a `docs[]`
# entry is a separate, reversible decision for the owner — this table does not
# make it.
#
# DELETE BEHAVIOUR — same call as `AcceptanceCriterion`/`WorkitemResult`
# immediately below in this file: a bare `ForeignKey("node.id")`, NO `ON
# DELETE CASCADE`, cleaned up IN CODE at the one place a node is actually
# deleted (`app._delete_node_dependents`) rather than in DDL — see those two
# tables' docstrings for the full reasoning; it applies here unchanged.
#
# UNIQUE INDEX on `(node_id, path)`, not just `path` — `path` is
# node-scoped by construction (a node can only ever read its OWN `docs[]`
# entries), and two different nodes coincidentally sharing a bare filename
# (unlikely under `docs/nodes/<node-id>/...` but not impossible for a
# manually-authored external-style key later) must not collide.
# --------------------------------------------------------------------------- #
class NodeDoc(Base):
    __tablename__ = "node_doc"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"))
    path: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        Index("ix_node_doc_node_path", "node_id", "path", unique=True),
    )


# --------------------------------------------------------------------------- #
# node_intent — a node's durable PURPOSE (ADR 0032). One row per statement.
#
# WHY A TABLE AND NOT `attributes.intents[]`. The JSON shape was the first
# proposal, on the `attributes.docs[]` precedent, and it was rejected for the
# reason `WorkitemResult` exists twenty lines below: `PATCH /api/node`
# replaces `attributes` WHOLESALE, so every intent edit would be a
# read-modify-write of the whole blob, and a concurrent edit silently drops
# the other writer's row. That is not a hypothetical here — two agent
# sessions work the same checkouts. One row per intent makes that class of
# loss impossible by construction, exactly as splitting `## Result` out of
# `node.body` did.
#
# The second reason is the one that actually decided it: the question this
# feature exists to answer — "what work would make us compliant with every
# stated intent?" — is a query ACROSS every node, not a read of one.
# `jsonb_array_elements` over 72 nodes would work and would be unindexed
# misery; `select(NodeIntent).where(kind=...)` is the thing `/api/intents`
# (and `atlas-mcp`, when it is built) actually wants.
#
# `node_id` IS THE OWNER, NOT THE SCOPE. This row records where the intent
# LIVES. Who is bound by it is derived by walking `contains` DOWNWARD from
# here, at read time, every time (`app._intents_for`). There is deliberately
# no denormalised copy on descendants: one edit to `atlas` would then have to
# rewrite 28 rows, and they drift apart the first time that write half-fails
# — which is the `status_override` lesson, and the reason that table is gone.
#
# NO `path` COLUMN, EVER. `statement` holds the words, inline, in Postgres.
# An intent is never a pointer to a file. This is the precise line the
# retired `doc` node type failed to hold (its whole content was a pointer),
# and `attributes.docs[]` — the shape this was almost modelled on — is
# exactly the field that carries file paths. If an intent needs supporting
# material, that is what `docs[]` is for; the intent itself must be readable
# without a second fetch, because it is what an agent reads FIRST.
#
# DELETE BEHAVIOUR — same call as `NodeDoc` above and
# `AcceptanceCriterion`/`WorkitemResult` below: bare FK, no `ON DELETE
# CASCADE`, cleaned up in code at `app._delete_node_dependents`.
#
# UNIQUE INDEX on `(node_id, slug)` — this is not decoration. The slug is the
# ONLY override mechanism ADR 0032 admits: a child carrying an inherited
# slug SHADOWS it from that node downward. Two rows with the same slug on one
# node would make "which one shadows" undefined, so Postgres refuses it
# rather than leaving it to a hand-written Python check the way a JSON-Schema
# implementation would have had to.
# --------------------------------------------------------------------------- #
class NodeIntent(Base):
    __tablename__ = "node_intent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # No `index=True` — the composite index below leads with node_id and so
    # already serves "every intent for this node, in order" (same call, same
    # reasoning, as `AcceptanceCriterion.node_id`).
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"))

    # Stable, node-unique, and REQUIRED even though nothing dereferences it
    # today. It is the one field that cannot be retrofitted: rows authored
    # without it would have no stable name to shadow BY, and shadowing is the
    # whole override story. Cheap now, impossible later.
    slug: Mapped[str] = mapped_column(String(64))

    # One of `node_types.INTENT_KINDS`. A plain string against a Python
    # tuple, NOT a `node_type`-style registry table: a `node_type` row carries
    # a JSON-Schema, a health formula, probe config and agent instructions,
    # and a kind carries none of those — it is a name and an action semantic.
    # A table for that would be ceremony. Validated in the write API, not by
    # a DB constraint, so adding a kind stays a one-line edit.
    kind: Mapped[str] = mapped_column(String(16))

    title: Mapped[str] = mapped_column(String(200))

    # The content. Inline, verbatim, never a pointer — see the header above.
    statement: Mapped[str] = mapped_column(Text)

    # Provenance: "ADR 0030", "owner decision 2026-08-10", a task id. Free text on
    # purpose — an intent harvested from a decision made in conversation has
    # no id to cite, and demanding one would just leave the field empty.
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # List order within the owning node — "the most important one first" is
    # meaning, and `AcceptanceCriterion.position` carries the same load for
    # the same reason. Dense 0..n-1 in returned order; the write API
    # renumbers rather than exposing gaps.
    position: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        # The read path is always "every intent for this node, in order" —
        # served directly by this composite rather than sorting in Python.
        Index("ix_node_intent_node_position", "node_id", "position"),
        Index("ix_node_intent_node_slug", "node_id", "slug", unique=True),
    )


# --------------------------------------------------------------------------- #
# graph_event — WHO CHANGED WHAT IN THE GRAPH (TASK-1045). The table `app.py`
# has named in two comments since 2026-08-10 as the right answer to "who
# changed this spec, and when", filed and deliberately unscheduled until now.
# Nothing recorded a graph mutation before this: not a status, not an assignee,
# not an edge, not a doc. `auth_event` (models_auth) is the sibling and covers
# IDENTITY only — logins, tokens, CSRF rejections — and says nothing about the
# graph.
#
# APPEND-ONLY IS A CONVENTION HERE, NOT A MECHANISM, AND THAT IS STATED RATHER
# THAN IMPLIED. There is no trigger, no hash chain and no `REVOKE`. The property
# holds only because no route in this app updates or deletes a row of this
# table, and because `app._delete_node_dependents` deliberately does NOT clean
# these up when a node is deleted (the whole point of a delete event is that it
# outlives its subject). Anyone with the database password can still rewrite
# history and nothing here would notice. The nearest-neighbour implementation
# this is modelled on — Paperclip's `activity_log` — makes the same claim with
# the same absence of enforcement; the claim is repeated here only in the form
# it can actually be defended in.
#
# SHAPED LIKE `AuthEvent`, DELIBERATELY, so two audit tables in one database
# read the same way:
#   * surrogate autoincrement id — an audit log wants monotonic insertion
#     order, and nothing grants rights off this id;
#   * timezone-aware `at`, with a Python default AND a server default;
#   * NO CHECK CONSTRAINT ON THE VOCABULARY. `action` is a plain string. A new
#     action name must never be an `ALTER TABLE` against a populated Postgres —
#     the same call `AuthEvent.event` makes, and the same call `Node.origin`
#     makes further up this file for the same reason: `_reconcile_graph_schema`
#     can only ADD COLUMN, never ADD CONSTRAINT, so a constraint declared here
#     would exist on every fresh test database and never on live.
#
# NO FOREIGN KEY ON ANY COLUMN, and that is not laziness in either direction:
#   * `node_id` / `related_node_id` / `entity_id` must OUTLIVE the row they
#     name — a delete event whose FK cascaded away would be the one event
#     nobody could ever read. Same call, same reason, as
#     `NodeVisibility.node_id`'s ("the mark must OUTLIVE the node it marks").
#   * `actor_user_id` / `responsible_user_id` follow `Node.assignee_user_id`
#     and `AuthUser.person_user_id`: `_reconcile_graph_schema`'s whole DDL
#     vocabulary is `ALTER TABLE ... ADD COLUMN <type>`, so a declared FK would
#     be created on every fresh test database and never on the one deployment
#     that matters. Validation is in code, at the single writer
#     (`app.record_graph_event`), which reads identity off the authenticated
#     principal and never off a request body.
# --------------------------------------------------------------------------- #
def _utcnow() -> datetime:
    """Timezone-aware UTC — the twin of `models_auth._utcnow`, defined here
    rather than imported so this module keeps importing nothing beyond
    sqlalchemy and jsonschema (see the `split_result` note below for why that
    independence is load-bearing)."""
    return datetime.now(timezone.utc)


#: Vocabulary for `GraphEvent.action`. A tuple, NOT a CHECK constraint and not
#: a registry table — see the header above. Named here so the writer, the read
#: API and `verify_graph_event.py` all say the same words, and so a typo is an
#: AttributeError at import rather than a row nobody ever queries for.
GRAPH_EVENT_ACTIONS = (
    "node.create",
    "node.update",
    "node.delete",
    "node.status",
    "node.assignee",
    "edge.create",
    "edge.delete",
    # The sub-records a node owns. THREE SEGMENTS, and the middle one names the
    # sub-record rather than a second entity: `node_doc` and `node_intent` rows
    # have no independent existence — both are keyed by the node they hang off
    # (`(node_id, path)` and `(node_id, slug)`), neither is reachable by an id
    # of its own on any read route, and both are governed by that node's
    # visibility. So they are recorded with `entity_type="node"` and the OWNING
    # NODE as `entity_id`, which is what makes "everything that ever happened to
    # this node" one query on `ix_graph_event_entity` instead of three.
    #
    # An EDGE is the counter-example and the reason this is a judgement and not
    # a rule: it has its own primary key, its own routes, and TWO governing
    # nodes, so it earns `entity_type="edge"`. If an intent ever gets a life of
    # its own — an id in a URL a caller keeps — this decision is the one to
    # revisit, and `GRAPH_EVENT_ENTITY_TYPES` is where it would show up.
    "node.doc.write",
    "node.doc.delete",
    "node.intent.create",
    "node.intent.update",
    "node.intent.delete",
    # ADR 0045. An ADR record is governed by its PROJECT's visibility, so it is
    # recorded on the project node like a doc or an intent; `detail.adr_id` names it.
    "node.adr.write",
    # STORY-atlas-acceptance-human-approved. Acceptance is a sub-record of its
    # workitem (like a doc or an intent), so these are recorded on the node.
    #   attested: a machine replaced acceptance under ATLAS_ACCEPTANCE_AGENT_WRITE=
    #             attested, naming the human who approved it (detail: approver,
    #             quote, agent, old, new);
    #   propose / approve / reject: the proposal flow (`AcceptanceProposal`).
    "node.acceptance.attested",
    "node.acceptance.propose",
    "node.acceptance.approve",
    "node.acceptance.reject",
    # Comments on a workitem (`routers/comments.py`), recorded on the item like an
    # intent; detail names the comment id, never its text.
    "node.comment.create",
    "node.comment.update",
    "node.comment.delete",
    # STORY-atlas-config-panel, phase 2: an owner changed a setting from the board. Recorded on
    # the setting's part node (`atlas-system*`); detail names the setting, from/to and sources.
    # Never a secret: none is changeable (routers/config.py).
    "node.config.set",
    # STORY-atlas-crunch-mode: crunch week was activated, changed, extended or ended (by the
    # owner, or by expiry with reason `expired` and no principal), or a pipeline reported a
    # backup under its policy. Recorded on `atlas-system`; detail is the window, the backup
    # policy, the skipped env names and the ADR id (crunch.py). Never a secret.
    "node.crunch.activate",
    "node.crunch.change",
    "node.crunch.extend",
    "node.crunch.end",
    "node.crunch.backup",
)

#: `GraphEvent.entity_type`. Two values today, and deliberately still two after
#: the doc/intent actions above joined the vocabulary — see the note there.
#: `entity_id` is TEXT for both — an edge's primary key is an integer and is
#: stringified on the way in, so one column serves both rather than a pair of
#: half-NULL columns.
GRAPH_EVENT_ENTITY_TYPES = ("node", "edge")


class GraphEvent(Base):
    __tablename__ = "graph_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False,
                                       default=_utcnow, server_default=func.now())

    #: One of GRAPH_EVENT_ACTIONS. See the header: no CHECK, on purpose.
    action: Mapped[str] = mapped_column(String(48))

    #: WHAT KIND OF THING was acted on, and WHICH ONE. `entity_id` is 128 wide
    #: to match `node.id`; an edge id is `str(int)` and fits trivially.
    entity_type: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[str] = mapped_column(String(128))

    #: THE ACCESS AXIS, and the reason these two columns exist beside
    #: `entity_id` rather than being derived from it at read time. An event
    #: about a node the caller cannot see must be neither readable NOR
    #: COUNTABLE (a post-filter that strips rows still leaks totals), so
    #: `GET /api/events` filters on these INSIDE the query — and for an EDGE
    #: event `entity_id` is an integer that resolves to nothing once the edge
    #: is gone, so the governing nodes have to be recorded at write time.
    #:
    #: `node_id` is the node an event is about (for an edge: its `src`).
    #: `related_node_id` is the second governing node (for an edge: its `dst`),
    #: NULL otherwise. An event is visible only when EVERY node it names is
    #: visible — an AND, never an OR, because an edge from a shared node into a
    #: private one is a statement about the private one.
    node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    related_node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: WHO DID IT, derived from `auth.current_principal` and never from a
    #: request field. `actor_kind` mirrors `auth.Principal.kind`
    #: (session|token|grace) and defaults to `"system"` for a write with no
    #: principal at all — a migration, a REPL, a startup path. Paperclip's
    #: `activity_log.actorType` carries the same default for the same reason.
    actor_kind: Mapped[str] = mapped_column(String(16), default="system")
    actor_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: The acting account's `subject` (`alex`, `bot-atlas-mcp`), copied at
    #: write time. Redundant with `actor_user_id` until that account is
    #: deleted, at which point it is the only thing left that says who this was.
    actor_subject: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: WHICH CREDENTIAL — `auth_service_token.id`, or NULL for a browser
    #: session. A different question from the two columns above, and the one a
    #: rotation answers.
    actor_token_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    #: ON WHOSE AUTHORITY. The column worth stealing from Paperclip
    #: (`activity_log.responsibleUserId`): it is the database expression of ADR
    #: 0034's person -> bot link, and it is what lets a reader ask "on whose
    #: authority?" rather than only "which token?". For a browser session the
    #: actor IS the person, so this equals `actor_user_id`; for an agent it is
    #: `auth_user.person_user_id`, the human who stays accountable for what
    #: their agents do. NULL for an agent with no person recorded (`admin bot
    #: list` already names those) and for a principal-less write — "nobody is
    #: on the hook for this" is a real answer and must not be forged into one.
    responsible_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    #: Free-form, per action: changed field names for an update, from/to for a
    #: status or assignee change, `src`/`dst`/`edge_type` for an edge. JSONB, so
    #: it is queryable later without a schema change — and deliberately WITHOUT
    #: a declared schema, because pinning one would make every new action an
    #: ALTER against a populated database.
    detail: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    __table_args__ = (
        # The read API's default order, and its `since`/`until` bound.
        Index("ix_graph_event_at", "at"),
        # "everything that ever happened to THIS thing" — the question a panel
        # asks. Composite, leading with the type, so it also serves a bare
        # `entity_type` filter.
        Index("ix_graph_event_entity", "entity_type", "entity_id"),
        # The access filter runs on every read of this table, so it is indexed
        # even though it is an `IN` over a set rather than a point lookup.
        Index("ix_graph_event_node_id", "node_id"),
        Index("ix_graph_event_responsible", "responsible_user_id"),
    )


# --------------------------------------------------------------------------- #
# acceptance_proposal — an agent's PROPOSED acceptance list, waiting for a
# human (STORY-atlas-acceptance-human-approved, option b).
#
# `routers/workitems.acceptance_rail_refusal` keeps a machine principal from
# changing the criteria of an existing workitem. That rail is unchanged; this
# table is the other half of "agents propose, humans commit": an agent may
# always store a proposal, and a browser session approves (applied through the
# ordinary session PATCH) or rejects it from the board.
#
# * ONE PENDING ROW PER ITEM, enforced by the route (a new proposal marks the
#   old one `superseded`, and the `node.acceptance.propose` event names it),
#   not by a partial unique index: `_reconcile_graph_schema` can only add a
#   plain index to a table that already exists, so a constraint declared here
#   would exist on fresh databases and never on a deployment that predates it.
# * ROWS ARE KEPT, with their outcome in `status`, so "what was proposed and
#   who decided" survives. Only `pending` is ever served as the proposal.
# * NO FOREIGN KEY on `node_id`, like `graph_event`: a node delete must not be
#   blocked by, or cascade through, a record of a decision. A proposal on a
#   deleted node is unreachable (every route 404s on the node first).
# * Who proposed and who decided are copied off the authenticated principal at
#   write time (id AND subject, so the record still reads after an account is
#   deleted), never off a request body.
# --------------------------------------------------------------------------- #
ACCEPTANCE_PROPOSAL_STATUSES = ("pending", "approved", "rejected", "superseded")


class AcceptanceProposal(Base):
    __tablename__ = "acceptance_proposal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(128))
    #: One of ACCEPTANCE_PROPOSAL_STATUSES. No CHECK, for `graph_event`'s reason.
    status: Mapped[str] = mapped_column(String(16), default="pending")
    #: The proposed list, in order.
    proposed: Mapped[list] = mapped_column(JSONType)
    #: The item's acceptance when the proposal was made, so a reader can see
    #: what the agent was looking at. The board diffs against the CURRENT list.
    base: Mapped[list] = mapped_column(JSONType)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False,
                                                default=_utcnow, server_default=func.now())
    proposed_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    proposed_by_subject: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_by_subject: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_acceptance_proposal_node_status", "node_id", "status"),
    )


# --------------------------------------------------------------------------- #
# workitem_comment — a conversation on an epic, story or task, written from the
# node panel's Comments tab or by an agent (`routers/comments.py`).
#
# * A NEW TABLE, so `create_all` makes it on a populated database too; nothing
#   here needs `_reconcile_graph_schema`.
# * Bare FK on `node_id`, cleaned up IN CODE at `_delete_node_dependents`, for
#   `WorkitemResult.node_id`'s reason. A comment belongs to its item the way an
#   intent belongs to its node: deleting the item deletes the conversation.
# * The author is copied off the authenticated principal at write time (id AND
#   subject, as `AcceptanceProposal` does), never off a request body, so a row
#   still reads after its account is deleted.
# * `body` is markdown, verbatim. An edit overwrites it and sets `edited_at`,
#   and the text it replaced goes to `workitem_comment_revision` first (below):
#   nothing a comment ever said is lost (auditability, owner decision 2026-10-29).
# --------------------------------------------------------------------------- #
class WorkitemComment(Base):
    __tablename__ = "workitem_comment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False,
                                               default=_utcnow, server_default=func.now())
    created_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by_subject: Mapped[str | None] = mapped_column(String(64), nullable=True)
    edited_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # A deleted comment is HIDDEN, never removed (auditability, owner decision 2026-10-29): the row and
    # its text stay, and these say when and by whom. Every read filters `deleted_at IS NULL`.
    deleted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    deleted_by_subject: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        # The read path is always "this item's comments, oldest first".
        Index("ix_workitem_comment_node_created", "node_id", "created_at"),
    )


# --------------------------------------------------------------------------- #
# workitem_comment_revision — every text a comment had before an edit replaced it.
#
# * Written by `routers/comments.edit_comment` in the same transaction as the
#   overwrite, and only when the text actually changes. Append-only by
#   convention; no route reads it (an audit record, read in the database).
# * `written_at` is when the replaced text was written (the comment's
#   `edited_at`, or `created_at` for the first text); `replaced_at` and the
#   `replaced_by_*` pair say when and by whom it was replaced, copied off the
#   principal as `WorkitemComment`'s author is.
# * A NEW TABLE, so `create_all` makes it. Bare FK to `workitem_comment.id`,
#   cleaned up in code at `_delete_node_dependents` before the comments.
# --------------------------------------------------------------------------- #
class WorkitemCommentRevision(Base):
    __tablename__ = "workitem_comment_revision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("workitem_comment.id"))
    body: Mapped[str] = mapped_column(Text)
    written_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False,
                                                default=_utcnow, server_default=func.now())
    replaced_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    replaced_by_subject: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_workitem_comment_revision_comment", "comment_id", "replaced_at"),
    )


# --------------------------------------------------------------------------- #
# The `## Result` splitter (TASK-1063) — PROVEN over the real corpus before
# being written this way: run over all 306 files at
# C:\Projects\AgentPlanningSetup\backlog on 2026-08-10, 221 round-trip
# byte-for-byte through split_result -> reassemble, 0 failures, and the other
# 85 correctly produce no row. Fence-aware H2 detection (skip `##` lines
# inside a ``` or ~~~ fenced code block) and naive H2 detection gave IDENTICAL
# results on this corpus, but fence-aware is what's implemented — a result
# section containing a fenced code sample that itself starts a line with `##`
# (a markdown example, say) is exactly the kind of body this repo's own
# workitems author, and naive detection would silently mis-slice it the first
# time one appears even though today's corpus doesn't exercise the
# difference.
#
# `import_workitems.py` and `verify_result_split.py` both import these two
# functions from here rather than each defining their own copy — unlike
# `import_workitems.py`'s OTHER duplicated helpers (`_source_path` etc.,
# which are copied on purpose to avoid pulling in app.py's auth-pepper
# fail-closed import chain), this parsing logic has no such dependency: this
# module (`models_graph`) imports nothing beyond sqlalchemy/jsonschema, so
# there is no reason to fork it and every reason not to (a splitter and its
# verifier drifting out of sync would defeat the verifier's whole purpose).
# --------------------------------------------------------------------------- #
H2 = re.compile(r'^##\s')
RES = re.compile(r'^##\s+(Result|Outcome|Results)\b', re.I)
FENCE = re.compile(r'^\s*(```|~~~)')


def h2_indices(lines: list[str]) -> list[int]:
    """Line indices of every `##` heading NOT inside a fenced code block.
    Fence state toggles on ANY fence line (``` or ~~~ opens OR closes,
    matching CommonMark and every markdown renderer this repo already
    depends on via `marked` — see `frontend/board.html`), so a body with an
    odd number of fence markers ends "inside a fence" for its remaining
    lines, same as a real renderer would."""
    out: list[int] = []
    in_fence = False
    for i, l in enumerate(lines):
        if FENCE.match(l):
            in_fence = not in_fence
            continue
        if not in_fence and H2.match(l):
            out.append(i)
    return out


def split_result(body: str) -> tuple[str, str, str, int] | None:
    """(body_without_result, heading, text, section_index), or None if `body`
    carries no `## Result`/`## Outcome`/`## Results` heading.

    Takes the block to the NEXT `##` heading, never to end-of-file — 9 of the
    221 real files that carry a result section have real content AFTER it
    (Sequencing, Key numbers, Rejected, Parked, "Acceptance for epic close"
    in `epic-company-formation/epic.md`, for instance); splitting to EOF would
    silently swallow that content into the result row instead of leaving it in
    `body_without_result`.

    Only the FIRST matching heading is used (`next(...)`) — the corpus has
    zero files with two result-ish headings (verified across all 306), so
    there is nothing to choose between in practice, but the function must
    still make a definite choice rather than error if that ever changes.
    """
    lines = body.split('\n')
    idxs = h2_indices(lines)
    r = next((i for i in idxs if RES.match(lines[i])), None)
    if r is None:
        return None
    after = [i for i in idxs if i > r]
    e = after[0] if after else len(lines)
    heading = lines[r]
    text = '\n'.join(lines[r + 1:e])
    body_wo = '\n'.join(lines[:r] + lines[e:])
    section_index = sum(1 for i in idxs if i < r)
    return body_wo, heading, text, section_index


def reassemble(body_wo: str, heading: str, text: str, section_index: int) -> str:
    """The exact inverse of `split_result`: given the body it left behind plus
    the three fields it split off, reproduce the ORIGINAL body byte-for-byte.

    NOT test-only — TASK-1067's write-API gate depends on this to detect
    whether a `PATCH /api/workitem` body edit also touched the result
    section, so this is production code, held to the same comment standard
    as everything else in this module.

    `section_index` is what makes this well-defined even though `body_wo` no
    longer contains the result heading at all: re-scan `body_wo`'s OWN `##`
    headings and re-insert immediately before whichever one now sits at slot
    `section_index` (or at the end, if `body_wo` has fewer sections than that
    — the result heading was the last section). This is why `position` is a
    section INDEX and not a byte offset (see `WorkitemResult.position`'s own
    docstring) — a byte offset computed against the ORIGINAL body would be
    meaningless against `body_wo`, which is shorter by exactly the spliced-out
    section.
    """
    lines = body_wo.split('\n')
    idxs = h2_indices(lines)
    ip = idxs[section_index] if section_index < len(idxs) else len(lines)
    return '\n'.join(lines[:ip] + [heading] + text.split('\n') + lines[ip:])


def ensure_search_indexes(engine: Engine) -> None:
    """Create the TASK-1050 full-text GIN indexes, idempotent, called every
    boot right after `app._reconcile_graph_schema()`.

    WHY THIS IS RAW SQL AND NOT A DECLARATIVE `Index(...)` IN
    `__table_args__` — the pattern every OTHER index in this file uses, and
    the exact pattern TASK-1046 extended `_reconcile_graph_schema()` to pick
    up automatically for an already-existing table. `postgresql_using="gin"`
    on a declarative `Index` is a syntax MODIFIER SQLAlchemy compiles fine
    (`ix_node_attributes_gin` already works that way), but the index
    EXPRESSION here — `to_tsvector('english', ...)`, not a plain column — has
    to be a `text(...)` clause, and `Base.metadata.create_all()` would try to
    compile that `Index` the moment the table is first created, before this
    function ever runs. Keeping it out of `Base.metadata` and creating it
    here, after `create_all`/`_reconcile_graph_schema` have already ensured
    the table and its columns exist, is what makes the ordering safe.

    `CREATE INDEX IF NOT EXISTS` (Postgres-native, not `checkfirst=True`)
    gives the same "a previous boot already created this — no-op, not an
    error" idempotency `Index.create(checkfirst=True)` gives the rest of this
    schema, without going through SQLAlchemy's own reflection-then-create
    path (which is written for `Index` objects that actually live on a
    `Table`, and these deliberately don't).
    """
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_node_body_search_gin "
            f"ON node USING gin ({NODE_BODY_SEARCH_SQL})"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_node_prose_search_gin "
            f"ON node USING gin ({NODE_PROSE_SEARCH_SQL})"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_acceptance_criterion_text_search_gin "
            f"ON acceptance_criterion USING gin ({AC_TEXT_SEARCH_SQL})"
        ))
        # TASK-1063: moving result prose out of `node.body` and into
        # `workitem_result` would otherwise remove 221 result sections from
        # search coverage silently — this index (and the matching third hits
        # query in `app._search_node_ids`) is what keeps them found.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_workitem_result_text_search_gin "
            f"ON workitem_result USING gin ({WORKITEM_RESULT_SEARCH_SQL})"
        ))
        # STORY-atlas-search-node-docs — the fourth source: `node_doc.content`.
        # Same expression as NODE_DOC_SEARCH_SQL above, textually identical on
        # purpose (see that constant's docstring). NODE_DOC_SERVED_SQL, the
        # allowlist predicate `app._search_node_ids` ANDs onto this match, is
        # NOT part of the indexed expression — it is evaluated as an ordinary
        # (unindexed) filter over what is already a small per-node JSONB
        # array, the same way NodeVisibility's access filter is an ordinary
        # WHERE clause rather than something folded into a search index.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_node_doc_content_search_gin "
            f"ON node_doc USING gin ({NODE_DOC_SEARCH_SQL})"
        ))
        # The eighth source: a workitem's comments (`routers/comments.py`). A comment is as
        # visible as its item, so no predicate beyond the callers' own access filter.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_workitem_comment_body_search_gin "
            f"ON workitem_comment USING gin ({WORKITEM_COMMENT_SEARCH_SQL})"
        ))


# --------------------------------------------------------------------------- #
# task_id_seq — server-side allocation of TASK-NNN ids (TASK-1047).
#
# WHY A SEQUENCE, NOT A SCAN. The pre-Atlas convention (`.claude/commands/
# file-task.md`) had the CLIENT scan `task-NNN-*.md` filenames in one story
# directory and pick the next integer — a read-then-write race by construction,
# and this repo runs parallel agents in worktrees on purpose (ADR 0016). A
# Postgres `SEQUENCE`'s `nextval()` is NOT transactional — it is not rolled
# back by a failed/aborted transaction and it is safe under concurrent callers
# by the database's own guarantee — which is exactly the property a read-then-
# write scan can never have. The owner accepted the one consequence that follows: a
# workitem created then deleted burns a number, and the corpus (already
# non-contiguous, `TASK-001`..`TASK-1062` with gaps) stays non-contiguous in a
# different way.
#
# WIRING. Declared as a standalone `Sequence` attached to `Base.metadata`
# (this module's `Base`, i.e. the graph schema, per the task's instruction) so
# `models_graph.Base.metadata.create_all(engine)` — already called on every
# `app.py` boot — creates it. `create_all(checkfirst=True)` is the default, and
# `SchemaGenerator._can_create_sequence` (sqlalchemy/sql/ddl.py) guards
# creation behind `dialect.has_sequence(...)`, so re-running `create_all` on a
# boot where the sequence already exists is a no-op, not an error — verified
# against SQLAlchemy 2.0.51's own source rather than assumed.
#
# THE PART create_all CANNOT COVER AT ALL: a brand-new
# `CREATE SEQUENCE task_id_seq` starts at `start=1` — on a Postgres that
# already holds `TASK-001`..`TASK-1062` (the live case, the day this ships),
# the very first `nextval()` would return 1 and collide immediately with an
# EXISTING node. `_reconcile_graph_schema()` (app.py) does not help either —
# it only ever emits `ALTER TABLE ... ADD COLUMN`, nothing about sequences.
# So seeding above the current maximum is NOT optional bootstrap polish, it is
# the difference between this shipping safely and this shipping broken on the
# one database that matters; `seed_task_id_sequence` below is called explicitly
# at every startup, after ingest, in `app.py`.
# --------------------------------------------------------------------------- #
task_id_seq = Sequence("task_id_seq", metadata=Base.metadata, start=1)

TASK_ID_PREFIX = "TASK-"
# Matches the corpus: `task-001-...` through `task-1046-...` — zero-padded to
# (at least) 3 digits, i.e. Python's `{:03d}` — which is a no-op once the
# number itself is 4 digits (`1063` -> `"1063"`, never `"01063"`). One place;
# `allocate_task_id` and everything that seeds the sequence route through it.
TASK_ID_MIN_WIDTH = 3
_TASK_ID_RE = re.compile(r"^TASK-(\d+)$")


def format_task_id(n: int) -> str:
    """The one place that knows the `TASK-` prefix and the zero-padding width."""
    return f"{TASK_ID_PREFIX}{n:0{TASK_ID_MIN_WIDTH}d}"


def _task_number(node_id: str | None) -> int | None:
    """`"TASK-1046"` -> `1046`; anything else (a story/epic slug id, or a test
    fixture id like `TASK-COLLIDE` — see ingest_graph.py's verify cases) ->
    `None`, deliberately swallowed rather than raising, since this is used to
    scan an id column that also holds non-numeric-suffixed ids."""
    if not node_id:
        return None
    m = _TASK_ID_RE.match(node_id)
    return int(m.group(1)) if m else None


def max_task_number(session: Session, extra_ids: Iterable[str] = ()) -> int:
    """Highest numeric suffix among every `TASK-<n>` id this process can see —
    persisted `node` rows AND `extra_ids` the caller supplies. `0` if none.

    `extra_ids` exists for one reason (TASK-1047 acceptance: "never collide
    with an id already in the graph or in a file that still exists"): the
    backlog markdown is still the source of truth for existing workitems while
    this epic is mid-flight, and a file's id does not reach the `node` table
    until ingest has actually run (and ingest is opt-in/non-fatal — it can be
    unconfigured or fail). The caller (`app.py`, which alone knows
    `ATLAS_BACKLOG_DIR`) passes every id `load_backlog_nodes()` finds on disk
    so a number is never handed out that a not-yet-ingested file is already
    using.
    """
    best = 0
    for (nid,) in session.execute(select(Node.id).where(Node.id.like("TASK-%"))).all():
        n = _task_number(nid)
        if n is not None and n > best:
            best = n
    for nid in extra_ids:
        n = _task_number(nid)
        if n is not None and n > best:
            best = n
    return best


def seed_task_id_sequence(session: Session, extra_ids: Iterable[str] = ()) -> int | None:
    """Idempotently advance `task_id_seq` so it can never mint an id that
    collides with an existing one, without ever moving it BACKWARDS.

    Returns the sequence's new `last_value`, or `None` when there was
    nothing to seed (a genuinely fresh install — see the first branch below).

    HOW THE MONOTONIC PART WORKS. `pg_sequences.last_value` is NULL until the
    sequence's very first `nextval()` — i.e. "nothing has been allocated from
    this yet, it is safe to plant the exact starting point with no waste" —
    and a real integer afterwards, meaning "this value is ALREADY SPENT, never
    hand it out again." Both branches below are written to that distinction
    on purpose:
      * NULL and no existing TASK- id anywhere (a genuinely fresh install):
        do nothing — the default `start=1` is already correct, and touching it
        would waste `TASK-001` on every fresh install for no reason.
      * NULL and an existing maximum `target` (the live case, the first boot
        after this ships against a populated Postgres, e.g. `target=1062`):
        `setval(seq, target + 1, false)` — `is_called=false` means the next
        `nextval()` returns that value EXACTLY, so the first id this process
        ever mints is `target + 1` (`TASK-1063`), not `target + 2`.
      * Already has a real `last_value` (every later boot, or a boot after any
        real allocation happened): `current` already means "this value is
        spent", the same thing `target` means once cast the same way — so
        just take `GREATEST(current, target)`, `is_called=true` so the next
        `nextval()` returns `desired + 1`, and never set it below where it
        already is. This is what makes a boot idempotent AND crash-safe:
        seeding twice, or seeding after some ids were already minted, can
        only push the floor up or leave it alone.
    """
    target = max_task_number(session, extra_ids)  # 0 if no TASK- id exists anywhere yet
    current = session.execute(
        text("SELECT last_value FROM pg_sequences WHERE sequencename = :n"),
        {"n": task_id_seq.name},
    ).scalar()

    if current is None:
        if target == 0:
            return None  # genuinely fresh install — leave start=1 alone, don't burn TASK-001
        new_value = session.execute(
            text("SELECT setval(:n, :v, false)"),  # next nextval() returns exactly target+1...
            {"n": task_id_seq.name, "v": target + 1},
        ).scalar()
        return new_value

    desired = max(current, target)  # `current` is already-spent (is_called=true); don't double-add
    if desired == current:
        return current  # already seeded at least this high — no-op, no wasted number
    new_value = session.execute(
        text("SELECT setval(:n, :v, true)"),  # next nextval() returns desired+1
        {"n": task_id_seq.name, "v": desired},
    ).scalar()
    return new_value


def allocate_task_id(session: Session) -> str:
    """Mint the next `TASK-NNN` id. THE function TASK-1048's create route must
    call — no caller should ever construct a task id any other way.

    `nextval('task_id_seq')`: non-transactional by Postgres's own design,
    which is the entire reason a sequence was chosen over a scan (see
    `task_id_seq`'s docstring) — two overlapping transactions, including one
    that later rolls back, are GUARANTEED distinct values by the database
    itself, not by any care taken in this function.
    """
    n = session.execute(select(task_id_seq.next_value())).scalar()
    return format_task_id(n)


# --------------------------------------------------------------------------- #
# Project keys — per-project `KEY-n` task ids (STORY-oss-12-project-keys).
#
# OPT-IN. Only a project node with `project_key` set changes anything: a task
# filed under it (its nearest project, resolved the way RBAC resolves one —
# `routers/workitems._minting_project_key`) is minted `KEY-n` here. Everything
# else keeps `allocate_task_id` above, untouched.
#
# WHY A COUNTER TABLE, NOT ONE POSTGRES SEQUENCE PER KEY.
#   * A sequence per key is DDL at request time (`CREATE SEQUENCE` named after
#     user input), outside `_reconcile_graph_schema`'s additive vocabulary and
#     invisible to `create_all` — and `nextval()` is non-transactional, so every
#     refused or rolled-back create burns a number for good.
#   * One row per key, advanced by `INSERT ... ON CONFLICT DO UPDATE ...
#     RETURNING`, is ordinary transactional DML. The UPDATE takes the row lock,
#     so two concurrent creates under one key SERIALISE on it: the second waits
#     for the first to commit (or roll back) and then reads its value. No
#     duplicates by construction, and no gaps by race either — a create that
#     rolls back releases its number to the next caller. The cost: concurrent
#     creates IN THE SAME PROJECT queue for the rest of the request's
#     transaction. Creates in other projects, and every `TASK-` create, never
#     touch the row.
#
# KEYED BY THE KEY STRING, NOT BY THE PROJECT. A minted `ABC-7` outlives being
# moved to another project, and outlives the project itself; if `ABC` is later
# taken by a different project its counter continues from 7, so an old id can
# never be minted twice. That is also what "immutable" means here: once a row
# exists for a key (an id has ever been minted with it), the project holding
# it cannot change or clear it (`project_key_minted`).
#
# THE FLOOR. A node with a `KEY-n` id the counter never minted (a caller-
# supplied id through POST /api/node) is honoured: every allocation takes
# `GREATEST(counter, highest existing KEY-n)` — the same "never collide with an
# existing id" rule `seed_task_id_sequence` applies to `TASK-`.
# --------------------------------------------------------------------------- #
PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
#: Keys that would read as one of the instance's own id prefixes.
RESERVED_PROJECT_KEYS = frozenset({"TASK", "STORY", "EPIC", "FEATURE", "SUBTASK", "ADR"})


class ProjectKeyCounter(Base):
    """The last number minted under one project key. See the block above."""
    __tablename__ = "project_key_counter"

    key: Mapped[str] = mapped_column(String(10), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False)


def project_key_problem(value) -> str | None:
    """Why `value` is not a legal project key, or None. `None` itself is legal
    (it clears the key); the caller decides whether clearing is allowed."""
    if value is None:
        return None
    if not isinstance(value, str) or not PROJECT_KEY_RE.match(value):
        return ("a project key is 2-10 characters, uppercase letters A-Z and digits, "
                "starting with a letter (e.g. ABC, OPS2)")
    if value in RESERVED_PROJECT_KEYS:
        return f"{value!r} is reserved: it is one of this instance's own id prefixes"
    return None


def format_project_task_id(key: str, n: int) -> str:
    """`("ABC", 7)` -> `"ABC-7"`. No zero padding: a new id space, so there is
    no corpus width to match (contrast `format_task_id`)."""
    return f"{key}-{n}"


def project_task_number(key: str, node_id: str | None) -> int | None:
    """`("ABC", "ABC-7")` -> 7; anything else -> None."""
    if not node_id or not node_id.startswith(key + "-"):
        return None
    rest = node_id[len(key) + 1:]
    return int(rest) if rest.isdigit() else None


def any_project_key(session: Session) -> bool:
    """Is any project keyed at all? False on every instance that never set one,
    and then the create route takes exactly today's path."""
    return session.execute(
        select(Node.id).where(Node.project_key.is_not(None)).limit(1)
    ).first() is not None


def project_key_minted(session: Session, key: str) -> bool:
    """Has any id ever been minted under `key`? Once true, the key is fixed."""
    return session.get(ProjectKeyCounter, key) is not None


def allocate_project_task_id(session: Session, key: str) -> str:
    """Mint the next `KEY-n` for a validated `key`, inside the caller's
    transaction. The row lock taken here is held until that transaction ends —
    see the block above for why that is the guarantee, and what it costs."""
    floor = 0
    # `key` is PROJECT_KEY_RE-shaped (letters and digits), so it carries no
    # LIKE wildcard.
    for (nid,) in session.execute(select(Node.id).where(Node.id.like(key + "-%"))).all():
        n = project_task_number(key, nid)
        if n is not None and n > floor:
            floor = n
    n = session.execute(
        text(
            "INSERT INTO project_key_counter (key, last_value) VALUES (:k, :floor + 1) "
            "ON CONFLICT (key) DO UPDATE "
            "SET last_value = GREATEST(project_key_counter.last_value, :floor) + 1 "
            "RETURNING last_value"
        ),
        {"k": key, "floor": floor},
    ).scalar()
    return format_project_task_id(key, n)


# --------------------------------------------------------------------------- #
# edge — typed, directed. Replaces parent_id; no fixed root. Adding a new
# edge_type value needs zero migration (it's just a string).
# --------------------------------------------------------------------------- #
class Edge(Base):
    __tablename__ = "edge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    src: Mapped[str] = mapped_column(ForeignKey("node.id"))
    dst: Mapped[str] = mapped_column(ForeignKey("node.id"))
    edge_type: Mapped[str] = mapped_column(String(64))  # contains|depends_on|serves|part_of|blocks|...
    directed: Mapped[bool] = mapped_column(Boolean, default=True)

    # Provenance (TASK-1016; meaning REDEFINED TASK-1058) — same contract as
    # Node.origin above: records HOW this edge first entered the database, not
    # who may delete it (there is no reconciler left to make that decision).
    # Measured live the day this was redefined: `edge` is 797 `"git"` / 10
    # `"atlas"` — unlike `node` (358/358 `"atlas"`), the two values are NOT
    # uniform here, and neither a comment nor a migration should assume they
    # are. NULLABLE for the same populated-Postgres reason as `Node.origin`;
    # see `origin_is_git_value`'s docstring.
    origin: Mapped[str | None] = mapped_column(String(16), default="git", nullable=True)

    __table_args__ = (
        Index("ix_edge_src_type", "src", "edge_type"),
        Index("ix_edge_dst_type", "dst", "edge_type"),
    )


# --------------------------------------------------------------------------- #
# status_override / base_status / the three-way merge — DELETED (TASK-1056,
# STORY-atlas-retire-status-roundtrip). The table existed for exactly one
# reason: the backlog markdown in AgentPlanningSetup used to be the source of
# truth for a workitem's status, and Atlas genuinely could not write it — so a
# board click landed in this separate overlay instead, and
# `ingest_graph.reconcile_status_overrides` reconciled it against the file on
# every boot (three-way: the override, the `base_status` it was written over,
# and whatever the file says now).
#
# That reason stopped holding on 2026-08-10 (TASK-1052): all 306 workitems
# were imported into Postgres as `atlas`-origin rows, and an `atlas`-origin
# node never got an override (see `app.set_node_status`'s history — it wrote
# directly onto `node.attributes["status"]` for exactly that case). By the
# time this table was dropped it had been 0 rows on both live and staging for
# a full day. `set_node_status` now has exactly ONE branch, for every node,
# regardless of origin: normalise, validate, write `attributes["status"]`,
# recompute, return. See the git history of this file for the removed
# `StatusOverride` model if the shape is ever needed again.
# --------------------------------------------------------------------------- #
# probe / probe_result — per-node health-check config + cached results. No
# scheduler/runner yet (v1 is on-demand only, per ADR 0019 scope) — just
# somewhere for results to live.
# --------------------------------------------------------------------------- #
class Probe(Base):
    __tablename__ = "probe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))  # http-healthcheck|docker-health|goal-tracker|...
    config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    interval: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds; null = on-demand only
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ProbeResult(Base):
    __tablename__ = "probe_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    probe_id: Mapped[int] = mapped_column(ForeignKey("probe.id"), index=True)
    ts: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(16))  # ok|warn|fail|unknown
    latency: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


# --------------------------------------------------------------------------- #
# App-layer validation: node.attributes must satisfy its node_type's JSON-
# Schema on every write. Registered globally on Session so it applies no
# matter which code path (ingester, future API, a script) does the write.
# --------------------------------------------------------------------------- #
def validate_node(session: Session, n: Node) -> None:
    """Raise ValueError if `n.attributes` doesn't satisfy its node_type's schema."""
    if jsonschema is None:
        raise RuntimeError("the jsonschema package is required — add it to requirements.txt")
    with session.no_autoflush:
        ntype = session.get(NodeType, n.type)
    if ntype is None:
        raise ValueError(f"node {n.id!r} references unknown node_type {n.type!r}")
    schema = ntype.attributes_schema or {}
    if not schema:
        return
    try:
        jsonschema.validate(instance=n.attributes or {}, schema=schema)
    except jsonschema.ValidationError as e:
        raise ValueError(
            f"node {n.id!r} attributes failed validation against node_type "
            f"{n.type!r}: {e.message}"
        ) from e


#: The work hierarchy's edge type — epic contains story contains task. Renamed
#: to `decomposes_into` by TASK-1030, at which point this one line is the change.
#:
#: It lives HERE rather than in app.py because `attention.py` needs it too
#: (effective_bindings walks parent links between workitems) and cannot import
#: app — app imports attention, so that direction is a cycle. app.py re-exports
#: it under the same name.
WORK_HIERARCHY_EDGE_TYPE = "contains"


# --------------------------------------------------------------------------- #
# `validate_required_world_link` WAS HERE AND IS DELETED (TASK-1025 step 5).
#
# It enforced the workitem family's `requires_edge` constraint: a new workitem
# had to carry a works_on/serves edge. The owner removed it on 2026-08-09 for two
# reasons, both of which the code proved:
#
#   * It was NON-FATAL by design — it printed a line and let the flush through —
#     so it enforced nothing. Something that cannot fail is documentation
#     wearing a validator's clothes.
#   * It was satisfiable by `serves`, which points at a GOAL, so a workitem
#     could pass while still having no world object at all. It did not even
#     measure the thing it was named after.
#
# The `requires_edge` COLUMN stays on NodeType (see above): dropping it is an
# ALTER against a populated Postgres for no gain, and the type registry may want
# it back for a constraint that actually blocks. The seeds in node_types.py no
# longer set it, so the live rows clear on the next boot.
#
# The real answer to orphan work is not a validator: it is that unbound work is
# VISIBLE. `attention.effective_bindings()` (TASK-1025) makes an unbound
# workitem count against its nearest bound ancestor instead of vanishing, and
# ADR 0030's binding pass took the unbound count to zero.
# --------------------------------------------------------------------------- #


@event.listens_for(Session, "before_flush")
def _validate_nodes_before_flush(session: Session, flush_context, instances) -> None:
    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, Node):
            validate_node(session, obj)

