"""
`oidc`: Atlas as a confidential OpenID Connect relying party (ADR 0041 §5, STORY-oss-13).

Authorization code flow with PKCE (S256), `state` and `nonce`, against ONE
configured issuer. Atlas issues its own `__Host-` session afterwards, exactly as
the password login does; the IdP's access, refresh and ID tokens are used once, in
memory, and never stored.

WHY PyJWT AND NOT A FULL OAUTH FRAMEWORK. The protocol half of a code flow is four
HTTP calls and three random strings, and doing it here keeps every decision
readable in one file: what is sent, what is checked, and what a failure records.
The half that must not be hand-rolled is JOSE: signature verification, algorithm
handling and the registered-claim checks. That is PyJWT (`jwt.decode` with an
explicit algorithm list, issuer, audience and required claims), a widely used,
actively maintained 2.x library whose only dependency for RSA/EC is
`cryptography`. Authlib was the alternative; its client integrations want their
own session storage and its JOSE module is being moved to a separate package, so
it would have brought more surface for the same checks.

THE CHECKS, in the order a callback meets them (`authenticate`):

  1. the `state` names a flow this server started, for THIS browser's pre-session,
     within ten minutes; the row is deleted on first sight (single use);
  2. the code is redeemed at the token endpoint with the flow's `code_verifier`
     and the client's credential (secret_basic, secret_post or private_key_jwt);
     the IdP refuses a wrong verifier, and Atlas then refuses the login;
  3. the ID token: signature by a key from the issuer's JWKS (refetched once for
     an unknown `kid`), `alg` from an asymmetric allowlist (never `none`, never
     HS*), `iss` exactly the discovered issuer, `aud` containing the client id
     (`azp` equal to it when present or when there are several audiences), `exp`,
     `iat` and `sub` present, 60 s of leeway;
  4. `nonce` equals the flow's, compared in constant time;
  5. claim mapping (`map_claims`): subject, name, email and groups by preset.

STEP-UP (TASK-1415, ATLAS_OIDC_STEP_UP=on): a signed-in IdP-backed person who
creates an agent credential is sent through this same flow again, with
`prompt=login` and `max_age`, bound to their full session instead of a
pre-session. Checks 1-5 are the same code (`_complete`); a step-up adds a fresh
`auth_time` (`authenticate_step_up`).

Discovery is lazy and cached for an hour: the app boots, and break-glass login
works, while the IdP is unreachable. TLS to the IdP is verified (httpx default,
which honours SSL_CERT_FILE for an internal CA).
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import logging
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

import models_auth
import settings
from models_auth import AuthUser, AuthUserIdpState

from . import flows, groups
from .base import Identity, LoginRefused

logger = logging.getLogger("atlas.identity.oidc")

#: The `auth_user.idp` value of every account this provider signs in.
OIDC_IDP = "oidc"

#: Asymmetric only. `none` and the HS* family are never accepted: an HMAC "signature"
#: keyed with the client secret is something the client itself could forge.
ALLOWED_ALGS = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512",
                          "ES256", "ES384", "ES512"})
LEEWAY_SECONDS = 60
DISCOVERY_TTL_SECONDS = 3600
JWKS_MIN_REFRESH_SECONDS = 60
HTTP_TIMEOUT_SECONDS = 10
#: `auth_user.subject` is VARCHAR(64).
MAX_SUBJECT = 64
#: A step-up's `state` starts with this (TASK-1415); `secrets.token_urlsafe` never
#: emits a dot, so a login's state never does. Tampering with it changes the
#: state's HMAC and finds no flow.
STEP_UP_STATE_PREFIX = "su."
#: Appended to the provider name on a step-up flow row (`auth_login_flow.provider`).
STEP_UP_FLOW_SUFFIX = "+step-up"

#: Claim names per preset. `subject` is a rule, not a claim name, for entra.
PRESETS: dict = {
    "generic": {"subject": "sub", "name": ("name", "preferred_username"),
                "email": ("email",), "groups": "groups"},
    "keycloak": {"subject": "sub", "name": ("name", "preferred_username"),
                 "email": ("email",), "groups": "groups"},
    "entra": {"subject": "tid+oid", "name": ("name",),
              "email": ("email", "preferred_username"), "groups": "groups"},
}

#: Tests set this to an `httpx.MockTransport`; production leaves it None.
_TRANSPORT: Optional[httpx.BaseTransport] = None


def use_transport(transport: Optional[httpx.BaseTransport]) -> None:
    """Route every IdP call through `transport` (tests: a local stub IdP, no network)."""
    global _TRANSPORT
    _TRANSPORT = transport


def _client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, transport=_TRANSPORT,
                        follow_redirects=False)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def pkce_challenge(verifier: str) -> str:
    """RFC 7636 S256: BASE64URL(SHA256(ASCII(code_verifier)))."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def _https(url: Any, what: str) -> str:
    if not isinstance(url, str) or urlsplit(url).scheme != "https":
        raise LoginRefused("discovery_%s_not_https" % what)
    return url


# --------------------------------------------------------------------------- #
# Claim mapping (presets)
# --------------------------------------------------------------------------- #

def _first_str(claims: Mapping[str, Any], names) -> Optional[str]:
    for name in names:
        value = claims.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def subject_of(cfg: settings.OidcSettings, claims: Mapping[str, Any]) -> str:
    """The `auth_user.subject` validated claims name, per preset. Raises LoginRefused.

    entra: the subject is the (tid, oid) pair. The issuer pins the tenant
    (settings refuses a multi-tenant issuer for this preset), so `tid` must equal
    it and the stored subject is the `oid`; `sub` is pairwise per application in
    Entra and would change if the app registration were recreated.

    ONE RULE FOR BOTH TOKEN KINDS: the ID token at SSO login (`map_claims`) and an
    access token at the API (`identity.access_token`, STORY-oss-25) go through
    this function, so one person is one account whichever door they came in by.
    """
    preset = PRESETS[cfg.preset]
    if preset["subject"] == "tid+oid":
        tid = claims.get("tid")
        oid = claims.get("oid")
        if not isinstance(tid, str) or not isinstance(oid, str) or not oid.strip():
            raise LoginRefused("entra_tid_oid_missing")
        if tid.lower() != (cfg.entra_tenant_id or ""):
            raise LoginRefused("entra_tenant_mismatch")
        subject = oid.strip().lower()
    else:
        subject = claims.get(preset["subject"])
        if not isinstance(subject, str) or not subject.strip():
            raise LoginRefused("subject_missing")
        subject = subject.strip()
    if len(subject) > MAX_SUBJECT or any(not c.isprintable() or c.isspace() for c in subject):
        raise LoginRefused("subject_unusable")
    return subject


def map_claims(cfg: settings.OidcSettings, claims: Mapping[str, Any]) -> Identity:
    """Validated ID-token claims -> Identity, per preset. Raises LoginRefused.

    The subject is `subject_of`'s; the rest is name, email and groups.
    """
    subject = subject_of(cfg, claims)
    preset = PRESETS[cfg.preset]
    name =_first_str(claims, (cfg.name_claim,) if cfg.name_claim else preset["name"])
    email = _first_str(claims, (cfg.email_claim,) if cfg.email_claim else preset["email"])

    groups_claim = cfg.groups_claim or preset["groups"]
    # Entra group overage: the claim is replaced by a pointer to Graph. Atlas
    # cannot know the memberships, so it refuses rather than syncing "none".
    claim_names = claims.get("_claim_names")
    if (isinstance(claim_names, dict) and groups_claim in claim_names) or \
            claims.get("hasgroups") is True:
        raise LoginRefused("groups_overage")
    raw_groups = claims.get(groups_claim)
    if raw_groups is None:
        values: tuple = ()
    elif isinstance(raw_groups, str):
        values = (raw_groups,)
    elif isinstance(raw_groups, list) and all(isinstance(g, str) for g in raw_groups):
        values = tuple(dict.fromkeys(g for g in raw_groups if g))
    else:
        raise LoginRefused("groups_claim_malformed")
    return Identity(provider="oidc", idp=OIDC_IDP, subject=subject,
                    name=(name or None) and name[:120], email=(email or None) and email[:320],
                    groups=values)


# --------------------------------------------------------------------------- #
# The provider
# --------------------------------------------------------------------------- #

class OidcProvider:
    name = "oidc"
    kind = "redirect"

    def __init__(self, cfg: settings.IdentitySettings, *, state_digest, engine_getter) -> None:
        """`state_digest(raw) -> hex` is auth's keyed digest (the token pepper);
        `engine_getter()` returns the app engine (for the lazy flow table)."""
        self.cfg = cfg.oidc
        self.session_seconds = self.cfg.max_session_hours * 3600
        self.revalidate_days = self.cfg.revalidate_days
        self._digest = state_digest
        self._engine = engine_getter
        self.group_map = groups.load_group_map(self.cfg.group_map_file)
        self._secret: Optional[str] = None
        self._signing_key = None
        self._signing_alg: Optional[str] = None
        if self.cfg.token_auth == "private_key_jwt":
            self._load_signing_key()
        else:
            self._secret = settings.read_secret("ATLAS_OIDC_CLIENT_SECRET", allow_env=False)
        self._lock = threading.Lock()
        self._discovery: Optional[dict] = None
        self._discovered_at = 0.0
        self._jwks: Optional[jwt.PyJWKSet] = None
        self._jwks_at = 0.0

    def _load_signing_key(self) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec, rsa

        pem = settings.read_secret("ATLAS_OIDC_CLIENT_KEY", allow_env=False)
        try:
            key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        except (ValueError, TypeError):
            raise settings.SettingsError(
                "%s_FILE does not hold an unencrypted PEM private key." % settings.OIDC_CLIENT_KEY_ENV,
                variable=settings.OIDC_CLIENT_KEY_ENV) from None
        if isinstance(key, rsa.RSAPrivateKey):
            self._signing_alg = "RS256"
        elif isinstance(key, ec.EllipticCurvePrivateKey) and key.curve.name == "secp256r1":
            self._signing_alg = "ES256"
        else:
            raise settings.SettingsError("%s_FILE must be an RSA or P-256 key." % settings.OIDC_CLIENT_KEY_ENV,
                                         variable=settings.OIDC_CLIENT_KEY_ENV)
        self._signing_key = key

    # -- discovery and keys ------------------------------------------------- #

    def discovery(self) -> dict:
        with self._lock:
            if self._discovery is not None and time.monotonic() - self._discovered_at < DISCOVERY_TTL_SECONDS:
                return self._discovery
        url = self.cfg.issuer + "/.well-known/openid-configuration"
        try:
            with _client() as http:
                resp = http.get(url, headers={"Accept": "application/json"})
            doc = resp.json() if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            doc = None
        if not isinstance(doc, dict):
            raise LoginRefused("discovery_unavailable")
        if str(doc.get("issuer", "")).rstrip("/") != self.cfg.issuer:
            raise LoginRefused("discovery_issuer_mismatch")
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            _https(doc.get(field), field)
        methods = doc.get("code_challenge_methods_supported")
        if isinstance(methods, list) and "S256" not in methods:
            raise LoginRefused("discovery_no_s256")
        with self._lock:
            self._discovery, self._discovered_at = doc, time.monotonic()
        return doc

    def _fetch_jwks(self, uri: str) -> jwt.PyJWKSet:
        try:
            with _client() as http:
                resp = http.get(uri, headers={"Accept": "application/json"})
            data = resp.json() if resp.status_code == 200 else None
            if not isinstance(data, dict):
                raise LoginRefused("jwks_unavailable")
            keyset = jwt.PyJWKSet.from_dict(data)
        except (httpx.HTTPError, ValueError, jwt.PyJWTError):
            raise LoginRefused("jwks_unavailable") from None
        with self._lock:
            self._jwks, self._jwks_at = keyset, time.monotonic()
        return keyset

    def _key_for(self, kid: Optional[str], uri: str) -> jwt.PyJWK:
        keyset = self._jwks or self._fetch_jwks(uri)
        for attempt in (0, 1):
            keys = [k for k in keyset.keys if getattr(k, "public_key_use", None) in (None, "sig")]
            if kid is not None:
                match = [k for k in keys if k.key_id == kid]
            else:
                match = keys if len(keys) == 1 else []
            if match:
                return match[0]
            if attempt == 0 and time.monotonic() - self._jwks_at >= JWKS_MIN_REFRESH_SECONDS:
                keyset = self._fetch_jwks(uri)   # key rotation at the IdP
            else:
                break
        raise LoginRefused("jwks_no_matching_key")

    def verification_key(self, doc: dict, kid: Optional[str]) -> jwt.PyJWK:
        """The issuer's signing key for `kid`: the cached JWKS, refetched at most once a
        minute for an unknown `kid` (rotation). Shared with `identity.access_token`
        (STORY-oss-25), so an ID token and an access token are checked against one key set."""
        return self._key_for(kid, doc["jwks_uri"])

    @staticmethod
    def allowed_algs(doc: dict) -> frozenset:
        """ALLOWED_ALGS (asymmetric only) narrowed to what discovery advertises (RS256
        when it advertises nothing), exactly the rule `_validate` applies."""
        advertised = doc.get("id_token_signing_alg_values_supported") or ["RS256"]
        return ALLOWED_ALGS & set(advertised if isinstance(advertised, list) else [])

    # -- the seam ----------------------------------------------------------- #

    def begin(self, s: Session, *, flow_binding: str, next_path: str,
              step_up: bool = False) -> str:
        """Store a fresh flow and return the authorization URL to send the browser to.

        `step_up` (TASK-1415): the same flow, asking the IdP to authenticate the
        person AGAIN (`prompt=login`, and `max_age` so a conforming IdP must return
        `auth_time`). Its state carries STEP_UP_STATE_PREFIX, which is how the one
        registered callback tells the two apart before consuming anything, and the
        row's provider is `<name>+step-up`, so neither kind can be completed as the
        other: a login `take` never returns a step-up row, and the reverse.
        """
        doc = self.discovery()
        flows.ensure(self._engine())
        state = (STEP_UP_STATE_PREFIX if step_up else "") + secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)          # 86 unreserved chars (RFC 7636: 43-128)
        flows.store(s, state_hash=self._digest(state), provider=self._flow_tag(step_up),
                    pre_session_hash=flow_binding, nonce=nonce, code_verifier=verifier,
                    next_path=next_path)
        params = {
            "response_type": "code",
            "client_id": self.cfg.client_id,
            "redirect_uri": self.cfg.redirect_uri,
            "scope": " ".join(self.cfg.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
        if step_up:
            params["prompt"] = "login"
            params["max_age"] = str(self.cfg.step_up_max_age)
        endpoint = doc["authorization_endpoint"]
        return endpoint + ("&" if "?" in endpoint else "?") + urlencode(params)

    def _flow_tag(self, step_up: bool) -> str:
        return self.name + STEP_UP_FLOW_SUFFIX if step_up else self.name

    def _complete(self, s: Session, credentials: Mapping[str, str], *,
                  flow_binding: Optional[str], step_up: bool) -> tuple[dict, "flows.Flow"]:
        """Checks 1-4 of the module docstring, shared by a login and a step-up:
        the state (single use, this browser's binding), the code redeemed with the
        PKCE verifier, the ID token, the nonce. Returns (validated claims, flow)."""
        if credentials.get("error"):
            raise LoginRefused("idp_error:%s" % str(credentials.get("error"))[:64])
        state = credentials.get("state") or ""
        code = credentials.get("code") or ""
        if not state:
            raise LoginRefused("state_missing")
        flows.ensure(self._engine())
        flow = flows.take(s, state_hash=self._digest(state), provider=self._flow_tag(step_up))
        if flow is None:
            raise LoginRefused("state_unknown")
        if not flow_binding or not hmac.compare_digest(flow.pre_session_hash, flow_binding):
            raise LoginRefused("state_other_browser")
        if not code:
            raise LoginRefused("code_missing")
        nonce, verifier = flow.nonce, flow.code_verifier
        doc = self.discovery()
        id_token = self._redeem(doc, code, verifier)
        claims = self._validate(doc, id_token)
        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise LoginRefused("nonce_mismatch")
        return claims, flow

    def authenticate(self, s: Session, credentials: Mapping[str, str], *,
                     flow_binding: Optional[str] = None) -> Identity:
        claims, flow = self._complete(s, credentials, flow_binding=flow_binding, step_up=False)
        return dataclasses.replace(map_claims(self.cfg, claims), next_path=flow.next_path)

    def authenticate_step_up(self, s: Session, credentials: Mapping[str, str], *,
                             flow_binding: Optional[str]) -> tuple[Identity, datetime]:
        """A step-up callback (TASK-1415): every check a login makes, plus a FRESH
        `auth_time`. Returns (identity, auth_time). `flow_binding` is the hash of
        the full session the step-up was started from.

        `auth_time` must be present (OIDC Core §2 requires it once `max_age` was
        asked for), a number, not in the future beyond the leeway, NOT BEFORE THE
        FLOW STARTED (so an IdP that ignored `prompt=login` and answered from an
        older IdP session is refused rather than trusted), and at most
        ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS old now.
        """
        claims, flow = self._complete(s, credentials, flow_binding=flow_binding, step_up=True)
        raw = claims.get("auth_time")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise LoginRefused("auth_time_missing")
        now = time.time()
        if raw > now + LEEWAY_SECONDS:
            raise LoginRefused("auth_time_in_future")
        started = flow.created_at.timestamp() if flow.created_at is not None else now
        if raw < started - LEEWAY_SECONDS:
            raise LoginRefused("auth_time_before_step_up")
        if now - raw > self.cfg.step_up_max_age:
            raise LoginRefused("auth_time_stale")
        ident = dataclasses.replace(map_claims(self.cfg, claims), next_path=flow.next_path)
        return ident, datetime.fromtimestamp(min(raw, now), tz=timezone.utc)

    def _client_auth(self, token_endpoint: str) -> tuple[dict, dict]:
        """(extra form fields, extra headers) for the token request."""
        cid = self.cfg.client_id
        if self.cfg.token_auth == "client_secret_basic":
            from urllib.parse import quote
            pair = "%s:%s" % (quote(cid, safe=""), quote(self._secret or "", safe=""))
            return {}, {"Authorization": "Basic " + base64.b64encode(pair.encode()).decode()}
        if self.cfg.token_auth == "client_secret_post":
            return {"client_id": cid, "client_secret": self._secret or ""}, {}
        now = int(time.time())
        headers = {}
        if self.cfg.client_key_id:
            headers["x5t" if self.cfg.preset == "entra" else "kid"] = self.cfg.client_key_id
        assertion = jwt.encode(
            {"iss": cid, "sub": cid, "aud": token_endpoint, "jti": str(uuid.uuid4()),
             "iat": now, "nbf": now, "exp": now + 120},
            self._signing_key, algorithm=self._signing_alg, headers=headers or None)
        return {"client_id": cid,
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion}, {}

    def _redeem(self, doc: dict, code: str, verifier: str) -> str:
        endpoint = doc["token_endpoint"]
        form, headers = self._client_auth(endpoint)
        form.update({"grant_type": "authorization_code", "code": code,
                     "redirect_uri": self.cfg.redirect_uri, "code_verifier": verifier})
        headers["Accept"] = "application/json"
        try:
            with _client() as http:
                resp = http.post(endpoint, data=form, headers=headers)
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None
        except (httpx.HTTPError, ValueError):
            raise LoginRefused("token_endpoint_unavailable") from None
        if resp.status_code != 200 or not isinstance(body, dict):
            err = body.get("error") if isinstance(body, dict) else None
            raise LoginRefused("token_exchange_refused:%s" % str(err or resp.status_code)[:64])
        id_token = body.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise LoginRefused("token_response_without_id_token")
        # The access and refresh tokens in `body` are dropped here, unused and unstored.
        return id_token

    def _validate(self, doc: dict, id_token: str) -> dict:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError:
            raise LoginRefused("id_token_malformed") from None
        alg = header.get("alg")
        advertised = doc.get("id_token_signing_alg_values_supported") or ["RS256"]
        allowed = ALLOWED_ALGS & set(advertised if isinstance(advertised, list) else [])
        if alg not in allowed:
            raise LoginRefused("id_token_alg_refused")
        key = self._key_for(header.get("kid"), doc["jwks_uri"])
        try:
            claims = jwt.decode(
                id_token, key=key.key, algorithms=[alg], audience=self.cfg.client_id,
                issuer=doc["issuer"], leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        except jwt.ExpiredSignatureError:
            raise LoginRefused("id_token_expired") from None
        except jwt.InvalidAudienceError:
            raise LoginRefused("id_token_audience") from None
        except jwt.InvalidIssuerError:
            raise LoginRefused("id_token_issuer") from None
        except jwt.PyJWTError as exc:
            raise LoginRefused("id_token_invalid:%s" % type(exc).__name__) from None
        aud = claims.get("aud")
        azp = claims.get("azp")
        if (isinstance(aud, list) and len(aud) > 1) or azp is not None:
            if azp != self.cfg.client_id:
                raise LoginRefused("id_token_azp")
        return claims

    def lookup(self, s: Session, identity: Identity) -> Optional[AuthUser]:
        return s.scalar(select(AuthUser).where(
            AuthUser.idp == identity.idp, AuthUser.subject == identity.subject))

    def _in_required_groups(self, identity: Identity) -> bool:
        return bool(self.cfg.required_groups & set(identity.groups))

    def check_login_allowed(self, identity: Identity) -> None:
        """Every OIDC login, not only the first, needs a required group once any is set:
        a leaver removed from the group in the IdP is refused at their next login."""
        if self.cfg.required_groups and not self._in_required_groups(identity):
            raise LoginRefused("not_in_required_group")

    def provision(self, s: Session, identity: Identity) -> AuthUser:
        """JIT: a `member`, only for a caller in ATLAS_OIDC_REQUIRED_GROUPS. Never an owner."""
        if not self.cfg.required_groups:
            raise LoginRefused("jit_provisioning_off")
        if not self._in_required_groups(identity):
            raise LoginRefused("not_in_required_group")
        user = AuthUser(
            id=str(uuid.uuid4()), idp=identity.idp, subject=identity.subject,
            email=identity.email, name=identity.name, role=models_auth.ROLE_MEMBER,
            is_service=False, password_hash=None, must_change_password=False,
            created_at=datetime.now(timezone.utc))
        s.add(user)
        s.flush()
        s.add(models_auth.AuthEvent(event="user_created", user_id=user.id, actor="oidc",
                                    reason="jit:%s" % self.name, detail="role=member"))
        return user

    def after_login(self, s: Session, user: AuthUser, identity: Identity) -> None:
        """Refresh contact fields, sync IdP groups, stamp the last OIDC login (§6)."""
        if identity.name and user.name != identity.name:
            user.name = identity.name
        if identity.email and user.email != identity.email:
            user.email = identity.email
        groups.sync_idp_groups(s, user, identity.groups, self.group_map, idp=OIDC_IDP)
        state = s.get(AuthUserIdpState, user.id)
        if state is None:
            state = AuthUserIdpState(user_id=user.id, break_glass=False)
            s.add(state)
        state.last_idp_login_at = datetime.now(timezone.utc)
