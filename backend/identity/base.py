"""
The identity-provider seam (STORY-oss-13, ADR 0041 §5): the types every provider shares.

A provider answers three questions, in this order, and nothing else:

    authenticate(s, credentials, flow_binding=...) -> Identity
        "who does this credential say the caller is?" It proves the claim (a
        password verified, an ID token's signature, issuer, audience, nonce and
        PKCE verifier checked) and returns the asserted identity. It never
        touches an account row.
    lookup(s, identity) -> AuthUser | None
        "which account is that?" Always by the composite `(idp, subject)` that
        `auth_user` was keyed on for exactly this (ADR 0025, ASVS 6.8.1), never by
        email or display name.
    provision(s, identity) -> AuthUser
        "there is none: may one be created?" Raises `LoginRefused` when not. The
        local provider never provisions (accounts are operator-made, ADR 0025);
        OIDC provisions a `member` only for a caller in ATLAS_OIDC_REQUIRED_GROUPS.

A REDIRECT provider (OIDC) has two more steps: `begin` builds the authorization
URL and stores the flow's state, and `after_login` runs once a session is about to
be issued (group sync, the last-IdP-login stamp). The routes that drive them live
in `auth.py`, next to every other login route, so the session, CSRF, throttle and
audit machinery is the one that already exists (ADR 0041 §5).

`LoginRefused.reason` goes to `auth_event` and never to the caller: every refusal
answers the same generic failure, as the password login does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from models_auth import AuthUser


class LoginRefused(Exception):
    """A login that must not complete. `reason` is for the audit log only."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Identity:
    """What a provider proved about the caller. Holds no credential and no token."""

    provider: str
    idp: str
    subject: str
    name: Optional[str] = None
    email: Optional[str] = None
    #: The IdP group values from the token (ids or names), or () when it carried none.
    groups: tuple = field(default=())
    #: Redirect providers only: the same-origin path the browser asked for when the
    #: flow began (already passed through `auth._safe_next` at the start).
    next_path: str = "/"


@runtime_checkable
class IdentityProvider(Protocol):
    """See the module docstring. `kind` is "password" or "redirect"."""

    name: str
    kind: str

    def authenticate(self, s: Session, credentials: Mapping[str, str], *,
                     flow_binding: Optional[str] = None) -> Identity: ...

    def lookup(self, s: Session, identity: Identity) -> Optional[AuthUser]: ...

    def provision(self, s: Session, identity: Identity) -> AuthUser: ...


class RedirectProvider(IdentityProvider, Protocol):
    """A provider the browser is sent away to and comes back from."""

    #: Absolute lifetime of a session this provider's login issues, in seconds.
    session_seconds: int

    def begin(self, s: Session, *, flow_binding: str, next_path: str) -> str: ...

    def after_login(self, s: Session, user: AuthUser, identity: Identity) -> None: ...
