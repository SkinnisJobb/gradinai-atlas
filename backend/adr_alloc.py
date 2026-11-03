"""The ONE place an Atlas-native ADR gets its number and its row (ADR 0045, TASK-1354).

Two writers create an Atlas-native ADR: `PUT /api/adrs/new` (`routers/adrs.put_adr`, which the
setup guide reaches by calling that function) and `atlas-admin guardrails apply`
(`guardrails.apply`). Both used to carry their own copy of lock-then-highest-sequence, and two
copies of an allocation rule are one edit away from two numbering schemes. Both now call this
module, and `verify_adr_records.py` tokenises every backend module and fails on a second
allocation site, the same way `verify_graph_event.py` §B holds the one event writer.

  lock_project(s, project)    SELECT ... FOR UPDATE on the project's node row. Every ADR write
                              in one project takes it (an explicit-id PUT too), so a sync write
                              and an allocation serialise.
  allocate(s, project, prefix)  the project's highest sequence + 1, and the id it makes. Read
                              AFTER the lock: every write in this project that committed before
                              is seen. The caller holds the lock (a dry run may read unlocked:
                              it writes nothing).
  add_atlas_adr(s, ...)       the row (source atlas; no legacy number, repo, path or commit) and
                              its `node.adr.write` event, through `deps.record_graph_event`, the
                              ONE event writer. No commit: the caller owns the transaction.

Which prefix, and the refusals around it, stay with each caller: the route answers 400s naming
the field, the CLI raises `GuardrailsError`, and neither shape changes here.

Imports nothing from FastAPI or the routers, and `deps` only inside `add_atlas_adr`, so
`guardrails.py` can keep importing lazily.
"""
from __future__ import annotations

from datetime import date as Date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models_adr import ADR_SOURCE_ATLAS, AdrRecord
from models_graph import Node as GraphNode


def lock_project(s: Session, project: str) -> None:
    """Serialise every ADR write in one project (`routers/adrs.py`'s ATOMIC ALLOCATION)."""
    s.execute(select(GraphNode.id).where(GraphNode.id == project).with_for_update())


def allocate(s: Session, project: str, prefix: str) -> tuple[int, str]:
    """`(seq, adr_id)` for the project's next ADR: highest sequence + 1, as `PREFIX` + 3 digits.
    The caller holds `lock_project` for this project (see the header)."""
    seq = (s.scalar(select(func.max(AdrRecord.seq)).where(
        AdrRecord.project_node_id == project)) or 0) + 1
    return seq, f"{prefix}{seq:03d}"


def add_atlas_adr(s: Session, *, adr_id: str, project: str, prefix: str, seq: int, title: str,
                  slug: str, status: str, date: Date, content: str | None, now: datetime,
                  status_raw: str | None = None, supersedes: list[str] | None = None,
                  superseded_by: list[str] | None = None, amends: list[str] | None = None,
                  extra_detail: dict | None = None) -> AdrRecord:
    """Insert one Atlas-native ADR allocated by `allocate`, and record its `node.adr.write`.

    The event's detail is `{adr_id, created: True, fields: None, bytes, source: "atlas"}`, the
    shape `PUT /api/adrs/new` has always written, plus `extra_detail` (the CLI's operator and
    verb) after it."""
    import deps

    row = AdrRecord(
        adr_id=adr_id, project_node_id=project, prefix=prefix, seq=seq, synced_at=now,
        source=ADR_SOURCE_ATLAS, title=title, slug=slug, legacy_number=None, status=status,
        status_raw=status_raw, date=date, supersedes=list(supersedes or []),
        superseded_by=list(superseded_by or []), amends=list(amends or []), repo=None, path=None,
        commit=None, content=content)
    s.add(row)
    deps.record_graph_event(s, "node.adr.write", entity_type="node", entity_id=project, detail={
        "adr_id": adr_id, "created": True, "fields": None, "bytes": len(content or ""),
        "source": ADR_SOURCE_ATLAS, **(extra_detail or {})})
    return row
