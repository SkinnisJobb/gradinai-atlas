"""
In-flight redirect logins: one row per `GET /auth/<provider>/start`, deleted at its callback.

WHY A TABLE, AND WHY ITS OWN METADATA. The authorization-code flow needs three
values to survive the round trip to the IdP and back: `state` (which callback
belongs to which start), `nonce` (bound into the ID token) and the PKCE
`code_verifier` (proves the code is redeemed by whoever asked for it). Keeping
them server-side, keyed by HMAC(state) and bound to the browser's pre-session
(`__Host-atlas_pre`, the login-CSRF session `GET /auth/login` already mints), means
the browser carries nothing but the cookie it already had, a callback from another
browser finds no flow, and a replayed callback finds the row already gone.

It is on its OWN MetaData and created on first use (`ensure`), not by
`models_auth.create_all`: an install that never turns OIDC on never gets the
table, so the homelab's schema is unchanged by this story. It is not on ADR 0041's
list of new tables because it holds no identity, only ten minutes of transient
login state; the PR says so.

`nonce` and `code_verifier` are stored in clear for at most FLOW_TTL_SECONDS. A
database reader in that window learns them, but still needs the authorization
code, which only the IdP and the user's browser ever see.

STEP-UP (TASK-1415, ATLAS_OIDC_STEP_UP). A step-up flow is the same row with
provider `<name>+step-up` and its binding the FULL session's hash instead of a
pre-session's, so `take` for a login never returns one and the reverse. Beside it
is one `auth_step_up` row: "the person in session S is approving agent credential
X", made live by the callback ("...and signed in at the IdP at T"), consumed by the
mint of X in S and gone within the step-up window.
Same metadata, same lazy creation, same argument: transient state, no identity, and
an install that never turns step-up on never gets the table.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import DateTime, MetaData, String, Text, delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

#: Ten minutes from start to callback, the same order as the pre-session's 15.
FLOW_TTL_SECONDS = 10 * 60


class FlowBase(DeclarativeBase):
    metadata = MetaData()


class AuthLoginFlow(FlowBase):
    __tablename__ = "auth_login_flow"

    #: HMAC(token pepper, state). The raw state is only ever in the redirect URLs.
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    #: `auth_session.id_hash` of the pre-session that started the flow.
    pre_session_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    next_path: Mapped[str] = mapped_column(Text, nullable=False, default="/")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


_ready: set = set()
_lock = threading.Lock()


def ensure(engine: Engine) -> None:
    """CREATE TABLE IF NOT EXISTS, once per engine per process."""
    key = id(engine)
    if key in _ready:
        return
    with _lock:
        if key not in _ready:
            FlowBase.metadata.create_all(engine)
            _ready.add(key)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def store(s: Session, *, state_hash: str, provider: str, pre_session_hash: str, nonce: str,
          code_verifier: str, next_path: str) -> None:
    now = _now()
    s.execute(delete(AuthLoginFlow).where(AuthLoginFlow.expires_at <= now))
    s.add(AuthLoginFlow(state_hash=state_hash, provider=provider,
                        pre_session_hash=pre_session_hash, nonce=nonce,
                        code_verifier=code_verifier, next_path=next_path,
                        created_at=now, expires_at=now + timedelta(seconds=FLOW_TTL_SECONDS)))


@dataclass(frozen=True)
class Flow:
    """A consumed flow's values (the row itself is already gone)."""

    pre_session_hash: str
    nonce: str
    code_verifier: str
    next_path: str
    #: When the flow was started. A step-up's `auth_time` must not be older (TASK-1415).
    created_at: Optional[datetime] = None


def take(s: Session, *, state_hash: str, provider: str) -> Optional[Flow]:
    """Remove and return the flow for this state, or None.

    SINGLE USE, AND COMMITTED HERE: the delete is committed before the code is
    redeemed, so a refusal later in the callback (which rolls its own work back)
    cannot resurrect the row, and a replayed callback finds nothing. Call it with
    nothing else pending on `s`.
    """
    row = s.scalar(select(AuthLoginFlow).where(AuthLoginFlow.state_hash == state_hash)
                   .with_for_update())
    if row is None:
        s.rollback()
        return None
    created = row.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    flow = Flow(pre_session_hash=row.pre_session_hash, nonce=row.nonce,
                code_verifier=row.code_verifier, next_path=row.next_path or "/",
                created_at=created)
    same_provider = row.provider == provider
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    s.delete(row)
    s.commit()
    if not same_provider or expires <= _now():
        return None
    return flow


# --------------------------------------------------------------------------- #
# Step-up grants (TASK-1415)
# --------------------------------------------------------------------------- #

class AuthStepUp(FlowBase):
    """One step-up for one agent credential, bound to one Atlas session.

    TWO STATES IN ONE ROW. Written PENDING by the step-up start (`begin_step_up`):
    the agent name the person was shown ("You are creating an agent credential
    named X"), the flow it rides on, no `auth_time`. Made LIVE by that flow's
    callback (`complete_step_up`): `auth_time` set, valid until auth_time + the
    window. Spent by the mint of THAT agent name in that session
    (`consume_step_up`), inside the mint's own transaction, so a refused mint
    leaves it for a retry. A second start in the same session replaces the row,
    and the first flow's callback then finds a different flow hash and is refused.
    Holds no token and no IdP claim beyond the time of the sign-in.
    """
    __tablename__ = "auth_step_up"

    #: `auth_session.id_hash` of the FULL session the step-up was done in.
    session_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: The agent the person approved: a new `<base>-<purpose>` or an existing agent.
    agent: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The `auth_login_flow.state_hash` of the flow this step-up rides on; NULL once live.
    flow_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    #: The ID token's `auth_time`: NULL while pending.
    auth_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Pending: the flow's own expiry. Live: auth_time + ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def begin_step_up(s: Session, *, session_hash: str, user_id: str, agent: str,
                  flow_hash: str) -> None:
    """Record what this session is about to approve, replacing any earlier step-up."""
    now = _now()
    s.execute(delete(AuthStepUp).where(AuthStepUp.expires_at <= now))
    s.execute(delete(AuthStepUp).where(AuthStepUp.session_hash == session_hash))
    s.add(AuthStepUp(session_hash=session_hash, user_id=user_id, agent=agent,
                     flow_hash=flow_hash, auth_time=None,
                     expires_at=now + timedelta(seconds=FLOW_TTL_SECONDS)))


def complete_step_up(s: Session, *, session_hash: str, user_id: str, flow_hash: str,
                     auth_time: datetime, max_age_seconds: int) -> Optional[str]:
    """Make the pending step-up for this flow live. Returns its agent name, or None
    when this session holds no pending step-up for exactly this flow and person."""
    row = s.scalar(select(AuthStepUp).where(AuthStepUp.session_hash == session_hash)
                   .with_for_update())
    if (row is None or row.user_id != user_id or row.auth_time is not None
            or row.flow_hash is None or row.flow_hash != flow_hash):
        return None
    row.auth_time = auth_time
    row.flow_hash = None
    row.expires_at = auth_time + timedelta(seconds=max_age_seconds)
    return row.agent


def peek_step_up(s: Session, *, session_hash: str, user_id: str) -> Optional[tuple]:
    """(expiry, agent) of this session's LIVE step-up for this person, or None. Reads only."""
    row = s.get(AuthStepUp, session_hash)
    if row is None or row.user_id != user_id or row.auth_time is None:
        return None
    expires = _aware(row.expires_at)
    return (expires, row.agent) if expires > _now() else None


def consume_step_up(s: Session, *, session_hash: str, user_id: str, agent: str) -> str:
    """Spend this session's live step-up on `agent`. Returns "ok", "none" or "mismatch".

    "mismatch": the step-up is live but was approved for ANOTHER agent name; it is
    kept, so the person can create the one they approved. "none": no live step-up
    (a pending or expired row is deleted). NOT COMMITTED HERE: the caller's commit
    is the mint's, so a mint that fails afterwards rolls the delete back with it.
    The row is locked FOR UPDATE, so two concurrent mints cannot both spend it.
    """
    row = s.scalar(select(AuthStepUp).where(AuthStepUp.session_hash == session_hash)
                   .with_for_update())
    if row is None:
        return "none"
    live = (row.user_id == user_id and row.auth_time is not None
            and _aware(row.expires_at) > _now())
    if live and row.agent != agent:
        return "mismatch"
    s.delete(row)
    return "ok" if live else "none"
