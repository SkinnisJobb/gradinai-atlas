"""`audit_read`: one row per agent read of a node, a search or a document (STORY-oss-16).

A NEW TABLE ONLY, and nothing else in the schema moves. It is registered on
`models_auth.Base`, so the `models_auth.create_all(engine)` that `main.startup()`,
`atlas-migrate upgrade` and `atlas-admin init-db` already call creates it on a
populated database (CREATE TABLE for a missing table is the one thing `create_all`
does reliably there). `models_auth.metadatas()` imports this module so the table is
registered wherever that metadata is used.

WHY THE AUTH BASE, not `models_graph.Base`: `ATLAS_RESEED=1` drops the graph Bases and
must never drop this one. A read log is accountability about ACCOUNTS (who read what),
like `auth_event`; a graph reseed must not erase who read the graph before it.

WHAT A ROW HOLDS, AND WHAT IT NEVER HOLDS. Ids and labels only: the person (`user_id`,
the human accountable for the agent through ADR 0034's link, NULL for an agent nobody
has been linked to), the agent account (`actor_user_id`, `actor_subject`), the
credential (`token_id`, the `auth_service_token.id`, never the token or its digest),
the MCP tool name the caller declared (`tool`, from `X-Atlas-Tool`), the route template
(`route`), the node ids the response served (`node_ids`) and the caller's request id
(`request_id`, from `X-Request-ID`). Never the content read, never a search string,
never a session id, a token, a hash or a password.

`tool` and `request_id` are CALLER-ASSERTED labels (atlas-mcp sets them; a token holder
could set anything). The person, agent, token and node ids are the API's own facts,
resolved by the gate and the route, which is why the row is written by the API.

Written ONLY by `audit_log.record_read`, and only when `ATLAS_AUDIT_READS=1`. Purged
ONLY by `audit_log.purge_reads` (`atlas-admin audit purge`), and only when
`ATLAS_AUDIT_RETENTION_DAYS` is set. No foreign key, for the reasons `models_auth`'s
header gives: an audit row must outlive the account and the node it names.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from models_auth import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditRead(Base):
    __tablename__ = "audit_read"

    # BIGINT: reads outnumber writes by orders of magnitude, and an audit log wants a
    # monotonic insertion order to break ties between rows of one instant.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                         default=_utcnow, server_default=func.now())

    #: The PERSON on whose authority the agent read (its `person_user_id`), or NULL.
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: The agent account and its username, copied at write time (it may be gone later).
    actor_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_subject: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Which credential: `auth_service_token.id`. Never the token, never its digest.
    token_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    #: The MCP tool the caller declared (`X-Atlas-Tool`), validated, or NULL.
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The route TEMPLATE that served it, e.g. `GET /api/node/{node_id}`.
    route: Mapped[str] = mapped_column(String(128), nullable=False)
    #: The node ids the response served, in the order served. `[]` for an empty search.
    node_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: The caller's `X-Request-ID`, validated, or NULL.
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("ix_audit_read_at", "at"),
        Index("ix_audit_read_user_id", "user_id"),
        Index("ix_audit_read_token_id", "token_id"),
    )
