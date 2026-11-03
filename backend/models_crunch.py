"""`crunch_state` and `crunch_history`: crunch week's state (STORY-atlas-crunch-mode).

crunch.py has the design (what crunch week is, who may switch it, what each act writes); this
module holds only the two tables and the one question asked of them before anything else.

ONE ACTIVE ROW. `crunch_state` holds at most one row, id `current`: the crunch week that is
scheduled or in force, or the last one that ended (`active` false). Activating again reuses the
row. `revision` grows by one on every write and every write is conditioned on the revision it
read (`UPDATE ... WHERE revision = :read`), so two writers racing (an owner's PUT and the expiry
of the same window, say) cannot both win: the loser changes nothing and is told so.

APPEND-ONLY HISTORY. `crunch_history` gets one row per act (activate, change, extend, end, and a
backup an install's pipeline reports) and nothing ever updates or deletes one. Append-only is a
convention here exactly as it is for `graph_event` (no trigger). The history the board shows
comes from this table; the audit trail is the `node.crunch.*` graph_event and the ADR on
`atlas-system` each act writes as well (crunch.py).

CREATED ON FIRST USE, NEVER AT BOOT. An install that never activates crunch week has neither
table: `main.startup()` creates nothing for it, and only reconciles the columns of tables that
already exist (additive only, `main._reconcile_graph_schema`). The first activation that passes
every check calls `ensure_tables` just before it writes. That is what makes "never activated"
mean "the database is byte-identical, schema included" (verify_crunch.py §B), rather than "two
extra empty tables". Every reader asks `tables_exist` first and treats a missing table as off.

Its own Base, no foreign key (like every table here: the reconciler can never add one to live).
`ATLAS_RESEED` does not drop it (it is configuration, like the accounts and `config_value`).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: The one row's id.
STATE_ID = "current"

#: What a history row records. `backup` is an install's pipeline reporting one (crunch.py).
HISTORY_ACTIONS = ("activate", "change", "extend", "end", "backup")


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CrunchState(Base):
    __tablename__ = "crunch_state"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    #: True while a window is scheduled or in force; false once it ended (by hand or expiry).
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    #: The validated config (crunch.validate_config's output). Never a secret: it has none.
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    activated_by_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    activated_by_subject: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The ADR of the act that produced the current config (activate, change or extend).
    adr_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    #: `manual` or `expired`, once ended.
    ended_reason: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class CrunchHistory(Base):
    __tablename__ = "crunch_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    #: One of HISTORY_ACTIONS.
    action: Mapped[str] = mapped_column(String(16))
    #: Who: the person's subject, or `system` for an expiry (no person acted).
    by_subject: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    by_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    adr_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    #: The same detail the act's graph_event carries (never a secret).
    detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


#: Set once the tables are known to exist in this process, so the banner (read on every board
#: load) asks the catalogue at most until the first activation.
_EXIST = False


def tables_exist(s) -> bool:
    """Do both tables exist? A missing table means crunch week was never activated here."""
    global _EXIST
    if _EXIST:
        return True
    found = s.execute(text("SELECT to_regclass('crunch_state') IS NOT NULL "
                           "AND to_regclass('crunch_history') IS NOT NULL")).scalar()
    if found:
        _EXIST = True
    return bool(found)


def ensure_tables(engine) -> None:
    """Create both tables if missing (first activation only). Idempotent."""
    global _EXIST
    Base.metadata.create_all(engine)
    _EXIST = True
