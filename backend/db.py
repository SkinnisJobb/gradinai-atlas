"""Database wiring: the engine, the session factory, and the v0 Component/Gate ORM models.

Split out of `app.py` (STORY-oss-04); the code is moved, not changed. `ATLAS_DB_URL` is read
here, at import, through `settings.read_secret` (TASK-1349; see `_load_db_url`).
"""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import Integer, String, Text, ForeignKey, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, Session

import settings


# --------------------------------------------------------------------------- #
# Model (a faithful-but-minimal slice of ADR 0017)
# --------------------------------------------------------------------------- #
class Base(DeclarativeBase):
    pass


class Component(Base):
    __tablename__ = "component"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)      # Atlas id e.g. Project-110000
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(32))                       # org|project|system|service|...
    category: Mapped[str] = mapped_column(String(32))                   # Org|Project|Infra|Backend|...
    wbs_code: Mapped[int] = mapped_column(Integer, default=0)
    wbs_path: Mapped[str] = mapped_column(String(64), default="")
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("component.id"), nullable=True)
    owner: Mapped[str] = mapped_column(String(64), default=settings.distribution.DEFAULT_COMPONENT_OWNER)  # A4b
    scope_label: Mapped[str] = mapped_column(String(32), default="project-internal")
    description: Mapped[str] = mapped_column(Text, default="")
    explicit_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # leaf colour
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)  # markdown action plan (plans/<id>.md)
    # Prioritization inputs (ICE/WSJF-lite; 1-10). None = not a schedulable work item.
    value: Mapped[int | None] = mapped_column(Integer, nullable=True)     # how much it unblocks / matters
    effort: Mapped[int | None] = mapped_column(Integer, nullable=True)    # cost to do
    urgency: Mapped[int | None] = mapped_column(Integer, nullable=True)   # time-criticality
    risk: Mapped[int | None] = mapped_column(Integer, nullable=True)      # risk it reduces / de-risks
    blocks: Mapped[str | None] = mapped_column(Text, nullable=True)       # comma-separated node ids this blocks
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)         # JSON: a gate/goal on this node

    # Tree is built from parent_id in Python (see build_children) — no self-ref relationship,
    # which keeps the adjacency-list ORM config out of the way. gates is a plain one-to-many.
    gates: Mapped[list["Gate"]] = relationship(back_populates="component", cascade="all, delete-orphan")


class Gate(Base):
    __tablename__ = "gate"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    component_id: Mapped[str] = mapped_column(ForeignKey("component.id"))
    promotes_to: Mapped[str] = mapped_column(String(16), default="green")
    component: Mapped["Component"] = relationship(back_populates="gates")
    requirements: Mapped[list["Requirement"]] = relationship(
        back_populates="gate", cascade="all, delete-orphan"
    )


class Requirement(Base):
    __tablename__ = "requirement"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gate_id: Mapped[str] = mapped_column(ForeignKey("gate.id"))
    text: Mapped[str] = mapped_column(Text)
    axis: Mapped[str] = mapped_column(String(24))    # security|enforceability|observability|correctness|cost
    verify: Mapped[str] = mapped_column(String(24))  # manual|eval|ci-signal|conformance|ticket-closed
    state: Mapped[str] = mapped_column(String(12), default="unknown")  # pass|fail|unknown
    gate: Mapped["Gate"] = relationship(back_populates="requirements")


class Decision(Base):
    """A decision bound to a node — an ADR now; later also gate transitions and
    agent actions. This per-node log is the provenance/audit seed (see Governance)."""
    __tablename__ = "decision"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("component.id"))
    kind: Mapped[str] = mapped_column(String(16), default="adr")   # adr|gate|agent|note
    ref: Mapped[str] = mapped_column(String(32))                    # e.g. ADR-0017
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="")     # proposed|accepted|…
    detail: Mapped[str] = mapped_column(Text, default="")           # one line: what was decided
    path: Mapped[str] = mapped_column(String(200), default="")      # docs/adr/…md


# ATLAS_LEGACY_V0 (STORY-oss-07). `1`, the default, is today: the four v0 tables above are
# created and `routers/legacy_v0.py` serves them. `0` takes them out of `Base.metadata` here,
# once, at import, so EVERY schema path that reads this metadata follows the flag with no
# edit of its own: `main.startup()`'s create_all and reconcile, `migrate_cli.py upgrade`
# (whose steps `verify_upgrade_command.py` pins to startup's), and the ATLAS_RESEED drop_all
# (which then leaves existing v0 tables and their rows alone). Nothing is dropped from a
# database that already has them. The classes stay mapped; the routes answer 404 first.
LEGACY_V0 = settings.load().legacy_v0
if not LEGACY_V0:
    for _v0_table in list(Base.metadata.sorted_tables):
        Base.metadata.remove(_v0_table)


# --------------------------------------------------------------------------- #
# DB wiring (with startup retry — Postgres may still be coming up)
# --------------------------------------------------------------------------- #
# No fallback DSN (TASK-926 item 4). This used to default to
# `postgresql+psycopg2://atlas:atlas@atlas-postgres:5432/atlas`, which meant a
# deploy that forgot ATLAS_DB_URL silently connected with the weak literal
# credential and looked completely healthy. Since TASK-966 this database holds
# password verifiers, session hashes and service-token digests, so a silent
# fallback to a published password is not a convenience — it is the breach.
# Fail loudly instead, per ADR 0015's "a missing required secret fails fast".
#
# Read through `settings.read_secret` (TASK-1349), the one secret reader, so
# the DSN behaves like every other secret. ADDITIVE: the plain `ATLAS_DB_URL`
# compose builds from ${ATLAS_DB_PASSWORD} is still accepted unchanged. What it
# adds: `ATLAS_DB_URL_FILE` (a Docker secret) is honoured and wins; a value that
# is SET but blank fails naming it as "set but empty" (TASK-1348) instead of
# "not set"; and `ATLAS_REQUIRE_SECRET_FILES=1` refuses the plain env var. There
# is still NO default (`default=` is deliberately not passed): unset fails boot
# with the message below, as it always has. The value is now stripped of
# surrounding whitespace, which no working DSN carries.
_DB_URL_UNSET = (
    "ATLAS_DB_URL is not set. It must be an explicit DSN — there is no default. "
    "In compose it is built from ${ATLAS_DB_PASSWORD}, which lives in the "
    "server's .env (chmod 600, never committed); or set ATLAS_DB_URL_FILE to the "
    "path of a file holding it. Atlas runs on POSTGRES in every environment; point "
    "this at a Postgres instance, e.g. "
    "ATLAS_DB_URL=postgresql+psycopg2://atlas:PASSWORD@localhost:5432/atlas"
)


def _load_db_url() -> str:
    """The DSN, or a RuntimeError naming the variable (never the value)."""
    try:
        return settings.read_secret(settings.DB_URL_ENV)
    except settings.SecretError as exc:
        msg = str(exc)
        if msg.startswith(settings.DB_URL_ENV + " is not set"):
            raise RuntimeError(_DB_URL_UNSET) from None
        raise RuntimeError(msg) from None


DB_URL = _load_db_url()
engine = create_engine(DB_URL, pool_pre_ping=True)


@contextmanager
def session():
    with Session(engine) as s:
        yield s
