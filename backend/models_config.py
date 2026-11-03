"""`config_value`: settings an owner set from the board (STORY-atlas-config-panel, phase 2).

routers/config.py has the design and the precedence (environment > board > default); this
module holds the table and the two things done with it: applying the stored values to the
process at boot, and applying one change while the API runs.

ONE ROW PER SETTING, holding the board's current value for it and who set it when. A change
replaces the row, a reset to the default deletes it; the history of changes is the
`graph_event` each one writes (`node.config.set`), not this table.

ITS OWN BASE, AND CREATED ONLY WITH THE FLAG ON. `main.startup()` creates (and reconciles) it
only when `ATLAS_CONFIG_WRITES` is on, so an install with the flag off, the homelab among them,
has no such table, reads nothing and gets exactly today's schema. Turning the flag off later
leaves the table in place and unread: every setting is then the environment's or the default,
as before. `ATLAS_RESEED` does not drop it (it is configuration, like the accounts, not graph).
No foreign key, like every table here.

HOW A VALUE TAKES EFFECT. A stored value is put into the process environment (`os.environ`)
under its own name, but only where the real environment does not set that name. Every
changeable setting's reader re-reads `os.environ` on each use (settings_catalog's header says
which, and why that is the condition for `changeable`), so nothing that consumes a setting had
to change and a value set from the board is honoured at once and after every restart. Boot
reads the table and opens no file. `_APPLIED` remembers what this process put there, which is
how "the environment sets it" stays distinguishable from "the board set it" after the fact.

ONE PROCESS. The API runs as one uvicorn process (backend/Dockerfile). With several workers a
live change would reach only the worker that served it until the others restart; revisit this
before running more than one.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

import settings_catalog


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConfigValue(Base):
    __tablename__ = "config_value"

    #: The variable's name, one of settings_catalog.CHANGEABLE when written.
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: The canonical text, as the setting's `validate` returned it. Never a secret: no secret
    #: is changeable (settings_catalog.change_refusal refuses one first).
    value: Mapped[str] = mapped_column(Text)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    set_by_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    set_by_subject: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)


#: name -> the value this process put into os.environ from the board.
_APPLIED: dict = {}


def board_applied() -> dict:
    """The settings whose effective value is currently a board-set one."""
    return {n: v for n, v in _APPLIED.items() if os.environ.get(n) == v}


def environment_sets(name: str) -> bool:
    """Does the REAL environment set `name`? True when os.environ holds a non-blank value that
    this process did not put there from the board."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return False
    return not (name in _APPLIED and raw == _APPLIED[name])


def _put(name: str, value: Optional[str]) -> None:
    if value is None:
        if name in _APPLIED and os.environ.get(name) == _APPLIED[name]:
            os.environ.pop(name, None)
        _APPLIED.pop(name, None)
    else:
        os.environ[name] = value
        _APPLIED[name] = value


def apply_at_boot(s: Session) -> dict:
    """Apply every stored value the environment does not override. A READ: writes no row.

    Returns {"applied": [...], "environment_wins": [...], "ignored": [...]} for the boot log.
    A row for a name that is no longer changeable, or whose value no longer validates, is
    ignored (and kept); the setting is then the environment's or the default."""
    rows = {r.name: r.value for r in s.scalars(select(ConfigValue)).all()}
    report: dict = {"applied": [], "environment_wins": [], "ignored": []}
    for name in list(_APPLIED):
        if name not in rows:
            _put(name, None)
    for name, value in sorted(rows.items()):
        if settings_catalog.change_refusal(name) is not None:
            report["ignored"].append(name)
            continue
        try:
            canonical = settings_catalog.BY_NAME[name].validate(value)
        except ValueError:
            report["ignored"].append(name)
            continue
        if environment_sets(name):
            report["environment_wins"].append(name)
            continue
        _put(name, canonical)
        report["applied"].append(name)
    return report


def store(s: Session, name: str, value: Optional[str], *, user_id: Optional[str],
          subject: Optional[str]) -> Optional[str]:
    """Write (or, for None, delete) the board's value for `name` in the caller's transaction.
    Returns the previous board value. The caller commits, then calls `apply_now`."""
    row = s.get(ConfigValue, name)
    previous = row.value if row is not None else None
    if value is None:
        if row is not None:
            s.delete(row)
    elif row is None:
        s.add(ConfigValue(name=name, value=value, set_at=_now(), set_by_user_id=user_id,
                          set_by_subject=subject))
    else:
        row.value, row.set_at, row.set_by_user_id, row.set_by_subject = (
            value, _now(), user_id, subject)
    return previous


def apply_now(name: str, value: Optional[str]) -> None:
    """Make a committed change effective in this process (None: back to the default)."""
    _put(name, value)
