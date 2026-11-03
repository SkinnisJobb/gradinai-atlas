"""`setup_answer`: the setup guide's state (STORY-atlas-setup-guide-adrs). `setup_guide.py` has the design.

ONE ROW PER RECORDED ANSWER, never updated. A topic's current answer is its newest row; an older
row is the history a supersede left behind, and the ADR it names is untouched (a changed answer
makes a new ADR that supersedes the old one, through the registry's own `supersedes` link). So
the guide is resumable by construction: its progress is whatever rows exist, read fresh by every
`GET /api/setup`, and a client that restarts, or a different client, carries on from there.

Written only by `routers/setup.py`, after the registry (`PUT /api/adrs/new`, its own code path)
has created the ADR; the ADR write is the `graph_event` (`node.adr.write`, attributed to the
caller), and the row carries the same actor so the guide can say who answered.

A NEW TABLE ONLY. Registered on `models_graph.Base` beside `adr_record`, so the `create_all`
that `main.startup()` and `atlas-migrate upgrade` already run creates it, and `ATLAS_RESEED`
drops it together with the ADRs it points at. No foreign key, the registry's reason: an answer
names an ADR id and a project id, both read through the API's own joins. An install that never
runs the guide has an empty table, and nothing else reads it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models_graph import Base, JSONType


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SetupAnswer(Base):
    __tablename__ = "setup_answer"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: The question-set topic id (`guardrails`), stable across versions (setup_guide.py).
    topic_id: Mapped[str] = mapped_column(String(64))
    #: The question set in force when answered: "1", or "1+<extra file version>".
    question_set_version: Mapped[str] = mapped_column(String(80))
    #: {question id: canonical answer}, as `setup_guide.normalise_answers` returns it.
    answers: Mapped[dict] = mapped_column(JSONType)
    project_node_id: Mapped[str] = mapped_column(String(200))
    #: The Proposed ADR this answer created (`adr_record.adr_id`).
    adr_id: Mapped[str] = mapped_column(String(16))
    #: The ADR of the answer this one replaced, or NULL for a topic's first answer.
    supersedes_adr_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    answered_by_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    answered_by_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    answered_by_subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: The person accountable (ADR 0034): the session's own, or the agent's linked person.
    responsible_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_setup_answer_topic", "topic_id", "id"),
        Index("ux_setup_answer_adr", "adr_id", unique=True),
    )
