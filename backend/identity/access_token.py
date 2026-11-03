"""
`access_token`: an IdP-issued OAuth 2.1 access token as an API credential
(ADR 0041 §9, STORY-oss-25). OPT-IN: nothing here runs unless ATLAS_OAUTH_RESOURCE
is set (`identity.oauth_enabled()`), and `atlas_svc_` tokens never reach it.

WHY HERE AND NOT IN atlas-mcp. atlas-mcp and atlas-api are ONE protected resource
(the MCP forwards each caller's own token, per request, to the API that answers
it; ADR 0033: the MCP holds no logic). So the token is validated where every other
credential is, in the gate's Bearer branch (`auth._resolve_bearer`), and the MCP
only advertises where to get one (RFC 9728 metadata, `mcp/app/oauth_resource.py`).
The token never leaves this resource: the API sends it nowhere.

THE CHECKS, in the order a token meets them (`resolve`); each failure is a 401
and a `login_fail` auth_event with reason `oauth:<why>`:

  1. shape: three base64url segments, at most 16 KiB;
  2. the header's `alg` is in the OIDC provider's asymmetric allowlist narrowed
     to what discovery advertises (never `none`, never HS*);
  3. the signature, by the issuer's JWKS key for `kid`, the SAME cached,
     rotation-aware key set the SSO login uses (`OidcProvider.verification_key`);
  4. `iss` exactly the discovered issuer, `aud` containing ATLAS_OAUTH_RESOURCE
     (or one of ATLAS_OAUTH_AUDIENCES), `exp` present, `exp`/`nbf`/`iat` with
     60 s of leeway; a `nonce` claim (an ID token) is refused;
  5. the PERSON: the subject by the SSO preset's rule (`oidc.subject_of`: Entra
     keys on tid+oid with the tenant pinned), looked up as (oidc, subject). No
     account: refused, with a reason the caller can act on (see PROVISIONING);
     a disabled person, or one whose last SSO login is older than
     ATLAS_OIDC_REVALIDATE_DAYS (ADR 0041 §6), is refused. A pending password
     change (`must_change_password`) is NOT a refusal (TASK-1455): the token was
     issued by the IdP, so it follows the SSO session's rule,
     `identity.forced_change_applies(person, AUTH_SSO)`;
  6. the CLIENT: `client_id` (RFC 9068), else `azp` (OIDC, Entra v2), else
     `appid` (Entra v1). None: refused.

THE ACCOUNT IT ACTS AS. One agent account per (person, client), created at the
first accepted token: `auth_user` (idp `oauth`, subject `oauth-<40 hex>` of a
SHA-256 over the person's id and the client id), `is_service`, role `member`,
linked to the person (ADR 0034), so grants inherit and role never does, project
RBAC caps it exactly as it caps a minted token, and `graph_event` attributes its
writes to the agent AND the person. `user_created` and `bot_person_linked`
auth_events record it. Disabling that row (`atlas-admin user disable`) turns that
one client off for that person; disabling the person turns off all of them.

SCOPES. The token's `scp` / `scope` values map through ATLAS_OAUTH_SCOPE_MAP to
read | write | admin (the highest wins; write carries read, admin carries both);
nothing mapped is read. The result is then narrowed to what the person's role may
self-mint (`models_auth.self_mintable_scopes`: a member's agent never holds
admin), and project RBAC narrows it again per project (`rbac.reach_of`).

PROVISIONING: REFUSE, DELIBERATELY. A token for a person Atlas has no account for
is a 401 telling them to sign in with SSO once. Creating the person here would
make a second provisioning path beside the SSO login's, without its
ATLAS_OIDC_REQUIRED_GROUPS check (an access token need not carry groups), its
group sync, or its audit trail; SSO login (or `atlas-admin user link-idp`) stays
the one door through which a person is created.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models_auth
import settings
from models_auth import AuthUser

from .base import LoginRefused

#: `auth_user.idp` of the agent accounts this module creates.
OAUTH_AGENT_IDP = "oauth"
MAX_TOKEN_CHARS = 16 * 1024
MAX_CLIENT_ID = 128
LEEWAY_SECONDS = 60
#: How often an agent's `last_login_at` is stamped (not on every request).
LAST_SEEN_WRITE_SECONDS = 60

_JWT_SHAPE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$")

#: The refusals the caller is told about, because they can act on them and the
#: token was genuine (signed, for this resource) when they are reached.
_PUBLIC = {
    "no_person": "No Atlas account for this IdP identity yet: sign in to the board with "
                 "SSO once, then retry.",
    "person_revalidation_due": "Your last SSO sign-in is too old: sign in to the board "
                               "with SSO again, then retry.",
}


class Refused(Exception):
    """A presented access token that is not accepted. `reason` goes to auth_event;
    `public` (or None) is what the 401 may say."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.public: Optional[str] = _PUBLIC.get(reason)


@dataclass(frozen=True)
class Resolved:
    """Who an accepted token acts as. `auth` turns it into a Principal."""

    agent_id: str
    agent_subject: str
    label: str
    scopes: tuple
    person_id: str
    client_id: str


def looks_like_jwt(raw: str) -> bool:
    return 0 < len(raw) <= MAX_TOKEN_CHARS and raw.isascii() and bool(_JWT_SHAPE.match(raw))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _scope_values(claims: Mapping[str, Any]) -> list:
    out: list = []
    for name in ("scp", "scope"):
        value = claims.get(name)
        if isinstance(value, str):
            out.extend(value.split())
        elif isinstance(value, list):
            out.extend(v for v in value if isinstance(v, str))
    return out


def scopes_for(cfg: settings.OAuthSettings, claims: Mapping[str, Any], person_role: str) -> tuple:
    """The Atlas scopes a token carries: mapped tier, narrowed to the person's role."""
    present = set(_scope_values(claims))
    tier = "read"
    for idp_scope, mapped in cfg.scope_map:
        if idp_scope in present and settings.OAUTH_TIERS.index(mapped) > settings.OAUTH_TIERS.index(tier):
            tier = mapped
    ceiling = set(models_auth.self_mintable_scopes(person_role))
    return tuple(sc for sc in settings.OAUTH_TIER_SCOPES[tier] if sc in ceiling)


def client_of(claims: Mapping[str, Any]) -> str:
    for name in ("client_id", "azp", "appid"):
        value = claims.get(name)
        if isinstance(value, str) and value.strip():
            value = value.strip()
            if len(value) > MAX_CLIENT_ID or any(c.isspace() or not c.isprintable() for c in value):
                raise Refused("client_id_unusable")
            return value
    raise Refused("client_id_missing")


def agent_subject(person_id: str, client_id: str) -> str:
    return "oauth-" + hashlib.sha256(("%s\n%s" % (person_id, client_id)).encode("utf-8")).hexdigest()[:40]


def validate(raw: str, provider, cfg: settings.OAuthSettings) -> dict:
    """Checks 1-4: the claims of a genuine token for this resource, or Refused."""
    if not looks_like_jwt(raw):
        raise Refused("malformed")
    try:
        header = jwt.get_unverified_header(raw)
    except jwt.PyJWTError:
        raise Refused("malformed") from None
    try:
        doc = provider.discovery()
    except LoginRefused as exc:
        raise Refused(exc.reason) from None
    alg = header.get("alg")
    if not isinstance(alg, str) or alg not in provider.allowed_algs(doc):
        raise Refused("alg_refused")
    try:
        key = provider.verification_key(doc, header.get("kid"))
    except LoginRefused as exc:
        raise Refused(exc.reason) from None
    try:
        claims = jwt.decode(
            raw, key=key.key, algorithms=[alg], audience=list(cfg.audiences),
            issuer=doc["issuer"], leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iss", "aud"]})
    except jwt.ExpiredSignatureError:
        raise Refused("expired") from None
    except jwt.ImmatureSignatureError:
        raise Refused("not_yet_valid") from None
    except jwt.InvalidAudienceError:
        raise Refused("audience") from None
    except jwt.InvalidIssuerError:
        raise Refused("issuer") from None
    except jwt.InvalidSignatureError:
        raise Refused("signature") from None
    except jwt.PyJWTError as exc:
        raise Refused("invalid:%s" % type(exc).__name__) from None
    if "nonce" in claims:
        # An ID token (it carries the login's nonce) is not an access token. Its
        # audience is Atlas's client id, which settings already keeps off the
        # accepted list; this is the second line.
        raise Refused("id_token_presented")
    return claims


def resolve(s: Session, raw: str, *, provider, cfg: settings.OAuthSettings,
            suspended, now: Optional[datetime] = None) -> Resolved:
    """Checks 1-6, then the agent account (created on first use). Raises Refused.

    `suspended(s, person, now=)` is `identity.agent_tokens_suspended` (ADR 0041 §6).
    Leaves `s` usable after a refusal: the caller writes the `login_fail` with it.
    """
    from .oidc import OIDC_IDP, subject_of

    now = now or _now()
    claims = validate(raw, provider, cfg)
    try:
        subject = subject_of(provider.cfg, claims)
    except LoginRefused as exc:
        raise Refused(exc.reason) from None
    client_id = client_of(claims)

    person = s.scalar(select(AuthUser).where(
        AuthUser.idp == OIDC_IDP, AuthUser.subject == subject, AuthUser.is_service.is_(False)))
    if person is None:
        raise Refused("no_person")
    if person.disabled_at is not None:
        raise Refused("person_disabled")
    # The forced-change rule the SSO session follows (TASK-1455): the IdP has verified
    # this person, so a pending local password change does not refuse their token. The
    # one predicate, not the raw flag: `forced_change_applies` holds only a credential
    # established with a password, and still holds every local account (none reaches
    # this line: the lookup above is `idp='oidc'`). Kept so that answer stays its call.
    from . import AUTH_SSO, forced_change_applies
    if forced_change_applies(person, AUTH_SSO):
        raise Refused("person_must_change")
    if suspended(s, person, now=now):
        raise Refused("person_revalidation_due")

    scopes = scopes_for(cfg, claims, person.role)
    handle = agent_subject(person.id, client_id)
    agent = _agent(s, person, handle, client_id, now)
    if agent.disabled_at is not None:
        raise Refused("agent_disabled")
    if agent.person_user_id != person.id or not agent.is_service:
        # Relinked or edited by hand: this row no longer says whose it is.
        raise Refused("agent_link_mismatch")
    last = agent.last_login_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if last is None or (now - last).total_seconds() >= LAST_SEEN_WRITE_SECONDS:
        agent.last_login_at = now
        s.commit()
    return Resolved(agent_id=agent.id, agent_subject=agent.subject,
                    label=agent.label or agent.subject, scopes=scopes,
                    person_id=person.id, client_id=client_id)


def _agent(s: Session, person: AuthUser, handle: str, client_id: str, now: datetime) -> AuthUser:
    def find() -> Optional[AuthUser]:
        return s.scalar(select(AuthUser).where(
            AuthUser.idp == OAUTH_AGENT_IDP, AuthUser.subject == handle))

    agent = find()
    if agent is not None:
        return agent
    label = ("oauth:" + client_id)[:64]
    agent = AuthUser(
        id=str(uuid.uuid4()), idp=OAUTH_AGENT_IDP, subject=handle,
        name=("%s via %s" % (person.name or person.subject, client_id))[:120],
        role=models_auth.ROLE_MEMBER,      # NEVER the person's role (ADR 0034)
        is_service=True, label=label, password_hash=None,
        must_change_password=False, person_user_id=person.id, created_at=now)
    if models_auth.refuse_person_link(agent, person) is not None:  # pragma: no cover
        raise Refused("agent_link_refused")
    s.add(agent)
    try:
        s.flush()
        detail = "client=%s person=%s:%s" % (client_id, person.idp, person.subject)
        s.add(models_auth.AuthEvent(event="user_created", user_id=agent.id, actor="oauth",
                                    reason="oauth_agent", detail=detail))
        s.add(models_auth.AuthEvent(event="bot_person_linked", user_id=agent.id, actor="oauth",
                                    reason="oauth_agent", detail="person=%s:%s" % (
                                        person.idp, person.subject)))
        s.commit()
    except IntegrityError:
        # Two first requests at once: the other one created it. Use theirs.
        s.rollback()
        agent = find()
        if agent is None:  # pragma: no cover
            raise Refused("agent_unavailable") from None
    return agent
