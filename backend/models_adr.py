"""`adr_record` — the registry of architecture decision records (ADR 0045).

TWO SOURCES, ONE TABLE (owner decision, 2026-10-24, TASK-1354). The `source` column says which:

  * NULL = git. The row indexes a file, `docs/adr/NNNN-*.md`, which stays authoritative;
    this table holds what Atlas needs to find, list and link it, with provenance (repo,
    path, commit) so a reader can always go back to the file. One writer feeds these rows:
    `tools/adr_sync.py`, one way, git -> Atlas, through `PUT /api/adrs/{adr_id}`. Every row
    written before the column existed is one of these, which is why git is NULL and not a
    value: no backfill, and nothing about them changes.
  * 'atlas' = a decision born in Atlas (the setup guide, an agent, a human). The row IS the
    record: `content` is its markdown, and repo/path/commit and legacy_number stay NULL. It
    is created by `PUT /api/adrs/new`, which allocates the project's next sequence under a
    row lock on the project node, and it starts Proposed until a human accepts it. The sync
    never creates, changes or orphans one, and the API refuses a git write to its id.

Nothing at boot reads a file.

THE ID IS PER PROJECT (owner decision, 2026-10-24). `adr_id` = the project's prefix + its own 3-digit
sequence: `AT001` is Atlas's first ADR, `HA001` the Sphere's. Prefixes are configured per repo
in the sync's config (the homelab's is `deploy/homelab/adr_sources.json`), so ids are unique across projects because prefixes are, and
`UNIQUE(project_node_id, seq)` holds inside one. The old shared number (`ADR 0041`) is KEPT as
`legacy_number`, a searchable alias, because ~40 prose mentions still say it; no file is renamed.

WHY A TABLE OF ITS OWN and not an `adr` node type: a node type reaches every tree consumer
(outline, structure, attention rollups, the radial graph, the frozen legacy board), and a table
reaches only the routes written for it. The decision note for the run that built this argues it.

NO FOREIGN KEY on `project_node_id`, deliberately. A FK to `node.id` would make deleting a
project node fail on its ADRs, which changes `DELETE /api/node` for a table that route has never
heard of. Instead every read INNER JOINs `node`, so an ADR whose project is gone is unreachable
(owner included), and the write checks the project exists. Same "outlive the node" stance as
`graph_event`, with the opposite read rule.

Registered on `models_graph.Base`, so `main.startup()`'s existing `create_all` makes it, and
`_reconcile_graph_schema` would add any later nullable column. An install that never runs the
sync has an empty table and behaves exactly as before.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models_graph import Base, JSONType

#: The normalised status vocabulary. `status_raw` keeps the file's own words
#: ("Accepted — owner decision, 2026-08-11"); this is the leading word, lower-cased.
ADR_STATUSES = ("proposed", "accepted", "deprecated", "superseded", "rejected", "unknown")

#: Where a record comes from. Git is stored as NULL (every pre-existing row is one) and
#: reported as "git"; an Atlas-native record stores ADR_SOURCE_ATLAS.
ADR_SOURCES = ("git", "atlas")
ADR_SOURCE_ATLAS = "atlas"

#: `AT001`: 2-4 capital letters, then at least three digits.
ADR_ID_PATTERN = r"^([A-Z]{2,4})(\d{3,})$"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AdrRecord(Base):
    __tablename__ = "adr_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: The per-project id, `AT001`. Unique across the whole table.
    adr_id: Mapped[str] = mapped_column(String(16))
    project_node_id: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(4))
    seq: Mapped[int] = mapped_column(Integer)
    #: The shared-sequence number the file is named by (`0041`), or NULL.
    legacy_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slug: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    status_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Lists of adr_ids, or `ADR-NNNN` for a link the sync could not resolve.
    supersedes: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    superseded_by: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    amends: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    repo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The file's markdown. Served by the detail route only, never by a listing.
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    #: Set when the file has gone from git. The row is kept (the sync never deletes).
    orphaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: NULL = synced from a git file; 'atlas' = recorded in Atlas (the header says how each is
    #: written). Nullable with no default, so `_reconcile_graph_schema` adds it to a populated
    #: table and every existing row reads as git.
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)

    __table_args__ = (
        Index("ux_adr_record_adr_id", "adr_id", unique=True),
        Index("ux_adr_record_project_seq", "project_node_id", "seq", unique=True),
        # NULLs are distinct in a Postgres unique index, so an ADR without a
        # legacy number never collides; two with the same one in ONE project do.
        Index("ux_adr_record_project_legacy", "project_node_id", "legacy_number", unique=True),
        Index("ix_adr_record_legacy", "legacy_number"),
    )


#: Full-text over an ADR: id, alias, title and body. No GIN index — the corpus
#: is tens of rows; add one in `ensure_search_indexes` if it ever is not.
ADR_SEARCH_SQL = ("to_tsvector('english', coalesce(adr_record.adr_id,'') || ' ' || "
                  "coalesce(adr_record.title,'') || ' ' || coalesce(adr_record.content,''))")
