"""
Atlas authentication — the gate (TASK-967) and the machine credential (TASK-968).

These two tasks are ONE change on purpose: the gate slams shut and the four
non-browser callers get a credential in the same commit. Splitting them is how
the board freezes while `/healthz` stays green.

────────────────────────────────────────────────────────────────────────────────
1. Why an ASGI middleware and not `Depends`
────────────────────────────────────────────────────────────────────────────────
`app.mount("/static", StaticFiles(...))` was a mounted ASGI sub-app. Route-level
`Depends` and `FastAPI(dependencies=[...])` do not see a mount, do not see
`/docs`, and do not see a route somebody adds at 23:00. `add_middleware` wraps
the entire router, every mount, and every route registered before OR after it.

A router-based scheme fails OPEN when a route is added. Middleware fails CLOSED.
For a part-time operator with an internet-facing board that difference is the
whole argument, and `backend/plans/Security-137100.md:27-28` asked for it
verbatim: "as middleware, not per-route checks."

The `/static` mount is DELETED, not narrowed — narrowing would have left
`/static/board.html` (an unauthenticated byte-identical copy of the board) and
`/static/index.html` (whose `fetch(...).then(r => r.json())` has no `r.ok`
check and would render a 401 body as data). Four vendored files are served by
an explicit `GET /assets/{name}` allowlist in `app.py` instead — and the gate
still covers `/assets/`, so static is defended twice.

────────────────────────────────────────────────────────────────────────────────
2. Deny by default, and the exempt list
────────────────────────────────────────────────────────────────────────────────
`PUBLIC_ROUTES` below is the ONLY thing that is not authenticated. Everything
else — `/board`, all eight `/api/*` routes (INCLUDING `/api/health`, which
is a reconnaissance document: env var names, `/backlog` host paths, source
paths, node ids and raw exception strings), `/assets/*`, `/docs`, `/openapi.json`
and every path that does not exist — requires a principal.

**`/` is the one deliberate exception to "public route == fixed response"**
(ADR 0035, 2026-08-25): the gate itself still treats it as public — an
unauthenticated caller is never refused — but the `/` HANDLER in `app.py`
reads `current_principal` (set below even on a public route, whenever a valid
credential WAS presented) and renders one of two different things: a public
informational page for nobody / a machine principal, a 302 to `/board` for a
signed-in browser. `/board` itself is NOT in `PUBLIC_ROUTES` and is gated
exactly as before — this note is about `/` alone.

**Adding to `PUBLIC_ROUTES` requires an ADR note.** The runner-up failure mode
for this whole story is `/api/health` being added to it six weeks later so
somebody's healthcheck goes green. `/healthz` and `/readyz` exist precisely so
no piece of infrastructure ever needs `/api/health`.

────────────────────────────────────────────────────────────────────────────────
3. Two credential branches, deliberately not three
────────────────────────────────────────────────────────────────────────────────
    A. `Authorization: Bearer atlas_svc_…`  -> machine  (CSRF exempt)
    B. `__Host-atlas_session` cookie        -> human    (CSRF token required)
    both present                            -> 400, refused outright

Branch A has ONE opt-in sub-case (ATLAS_OAUTH_RESOURCE, ADR 0041 §9,
STORY-oss-25): a Bearer shaped like a JWT is an IdP access token, validated in
`identity/access_token.py` and acting as an agent account linked to its person.
It is still branch A: same CSRF exemption, same scope checks, same 401.
`_resolve_bearer` is the dispatch; unset, it is `_resolve_token` and nothing else.

"Which one wins" is where confusion bugs live, and a browser that somehow
acquired an `Authorization` header is not a case worth guessing about.

**There is no third branch reading `Cf-Access-Jwt-Assertion`.** After ADR 0025
the Cloudflare Access application is retired, so nothing issues that header on
any path. The bug class where a LAN peer forges it does not exist here because
the code that would read it is not written.

An *invalid* Bearer is always a 401 — even on a public route — because a script
presenting a broken credential must be told. An *unresolvable cookie* is treated
as anonymous and cleared, because an expired cookie on the way to `/auth/login`
is ordinary, not an attack.

────────────────────────────────────────────────────────────────────────────────
4. Content negotiation happens in ONE place, and a Bearer is never redirected
────────────────────────────────────────────────────────────────────────────────
`302 -> /auth/login?next=…` only when ALL of: method is GET or HEAD, `Accept`
contains `text/html`, the path is not under `/api/`, and NO `Authorization`
header was presented. Everything else is `401` JSON with
`WWW-Authenticate: Bearer error="invalid_token"` (RFC 6750).

A script that follows a redirect gets an HTML login page with a 200, parses it
as data, finds nothing, and reports success. That is the precise mechanism by
which this class of change freezes a board while health stays green, and this
repo has already lived it once (AGENTS.md:94-105).

`WWW-Authenticate: Basic` is NEVER sent — browsers render a native credential
popup for it, which is not this flow.

────────────────────────────────────────────────────────────────────────────────
5. The forced-change interlock is AT THE GATE
────────────────────────────────────────────────────────────────────────────────
`must_change_password` is **re-read from `auth_user` on every request**. Never a
claim in the cookie, never cached on the session row: a cached `false` is a
complete bypass with no revocation path.

Such a principal reaches `MUST_CHANGE_ALLOWLIST` and nothing else. Every other
path — every existing route, every route added in six months, a made-up path —
is `302 -> /auth/password/change` for an HTML GET and `403` JSON otherwise. It
is deny-by-default so nobody has to remember anything when adding a route.
Hiding the board behind a UI redirect and calling it done is the exact defect
the acceptance criterion exists to catch.

PASSWORD SIGN-INS ONLY (TASK-1442, TASK-1454). The flag means "the password this
account signs in with is a temporary one", so the interlock holds a session that
was established WITH A PASSWORD and never one the IdP vouched for. The rule is
`identity.forced_change_applies(user, method)`; `_resolve_session` hands it the
session's `auth_method`, read through `session_auth_method`:

    local account                    every session held (it has no IdP)
    IdP-backed, an SSO session       passes, even with the flag set: the IdP has
                                     just authenticated that person
    IdP-backed, a password session   held: since TASK-1454 a linked account signs
    (its local name, TASK-1454)      in with a password under its local name, and
                                     a temporary one forces the change as for anyone
    IdP-backed, minted or unknown    held (fail closed)

Until TASK-1454 the method was read off the ACCOUNT (`idp != 'local'` meant "an
OIDC session"), which was true only because a linked account could not sign in with
a password. The session row now says how it began; a row older than the column
(NULL) still means what it meant then, which is what `session_auth_method` reads.
The flag is kept on purpose (owner decision, 2026-10-29): for an SSO session it means "no
personal password yet", which `/api/me` reports as `password_setup`, and the person
sets one after a fresh IdP step-up (`POST /api/me/password`, §14c). Still re-read on
every request; an install with no OIDC has no IdP-backed account, so nothing there
changes.

────────────────────────────────────────────────────────────────────────────────
6. CSRF — the synchronizer token pattern, plus an Origin check
────────────────────────────────────────────────────────────────────────────────
These are the app's FIRST mutating routes, so the posture for the whole app is
decided here:

    cookie-authenticated  => a synchronizer token is REQUIRED
    Bearer-authenticated  => exempt (an Authorization header is never ambient)
    both present          => 400
    no state-changing GET, EVER

The app is stateful (there is a Postgres session table), so the stateless
double-submit variants buy nothing but complexity. The token is derived from the
session secret under the server-side pepper:

    csrf_token = HMAC(pepper, "csrf-token-v1" || sid)

so the server can recompute it from the cookie it already has on every request
and embed it in any server-rendered form, while an attacker without the cookie
cannot produce it. `auth_session.csrf_hash` stores its digest and is checked too.

`GET /auth/login` mints the `kind='pre'` session and embeds the token: this is
**login CSRF** defence, and it is not theoretical — without it an attacker logs
a victim into the attacker's account and watches what they do, on a board that
is about to grow per-node privacy.

`SameSite=Lax` stays on and is **defence in depth, not the control**: the cookie
sits on `atlas.example.com`, so anything ever put on `*.example.com` is
"same-site" to it. OWASP is explicit that SameSite does not replace a CSRF
defence.

No `CORSMiddleware` is added: every caller is same-origin, and Starlette will
happily let you configure `allow_credentials` with `*`.

────────────────────────────────────────────────────────────────────────────────
7. Sessions
────────────────────────────────────────────────────────────────────────────────
`sid` = `secrets.token_urlsafe(32)` (256 bits, double the ASVS 7.2.3 floor and
free). The row's PK is `HMAC(pepper, sid)` — **the raw value is never stored**,
so a `pgdata` leak yields no live sessions. Compared with `compare_digest`.

Cookie `__Host-atlas_session`: `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`,
no `Domain`, **no `Max-Age`** — expiry is enforced server-side against the row,
because the client controls the cookie.

Idle 24 h sliding (`last_seen_at` written only when >60 s stale, or the board's
four parallel boot fetches generate four UPDATEs per page load); absolute 14 d.

Rotation happens TWICE: the pre-session row is DELETED and a new id minted at
login, and the id is minted again at password change. Skipping the second is the
standard session-fixation defect.

`Secure` cookies do not travel over plain HTTP and `__Host-` requires `Secure`.
That is physics, and it is why TASK-964's LAN hostname with a real certificate
exists. Do NOT "fix" a cookie that fails to appear by dropping `Secure`.

────────────────────────────────────────────────────────────────────────────────
8. Service tokens (TASK-968)
────────────────────────────────────────────────────────────────────────────────
`atlas_svc_<6 hex>_<43 urlsafe>`. The fixed prefix buys O(1) routing in the gate
without probing the session table, an indexed lookup instead of a scan, a
pattern gitleaks can match, and unambiguous grepping across two repos.

Stored as `HMAC-SHA256(ATLAS_TOKEN_PEPPER, token)` — **not Argon2**. Against a
256-bit CSPRNG secret there is no dictionary and no feasible brute force
regardless of hash speed, while a fast keyed hash permits an indexed equality
lookup instead of a full scan running a KDF per row. Running Argon2 per request
against an unauthenticated internet-facing endpoint is a self-inflicted DoS.

Revoked, expired and never-existed are **byte-identical**: same status, same
body, same headers. Every token has a mandatory `owner_user_id` pointing at an
`is_service` `auth_user` row, one per caller, never shared — one shared
credential means the audit log says "atlas" for every call and revoking one
caller revokes all of them.

A password change does NOT revoke service tokens (a forgotten browser password
says nothing about an ingest credential). **Disabling the user does** — that is
the deliberate, loud lever.

────────────────────────────────────────────────────────────────────────────────
9. Fail closed
────────────────────────────────────────────────────────────────────────────────
`ATLAS_TOKEN_PEPPER` and `ATLAS_LOGIN_ORIGINS` raise at IMPORT, naming the key,
before uvicorn binds. There is no generated-if-missing fallback: a per-boot
random pepper logs every user out on every reboot, and this box reboots
unattended. Importing this module also imports `passwords`, which enforces
`ATLAS_PASSWORD_PEPPER` the same way.

**Bootstrap fails closed with no escape hatch.** With an empty `auth_user` table
the app serves the login page and refuses everything; every credential returns
the same generic failure. There is no `ATLAS_BOOTSTRAP_SUPERUSER_EMAIL`, no
seeded default account, no self-registration route and no reset-token route, now
or later. The first owner is created by `admin.py user add` (TASK-970) against
the live database. An app that mints an account for whoever asks first is an app
with a race on the open internet.

**`ATLAS_AUTH_DISABLED` does not exist and must not be added.** A test backdoor
is a production backdoor with a comment on it. `verify_object_graph.py` mints
real credentials against the real gate.

The ONE deliberate exception is `ATLAS_MACHINE_GRACE_HOSTS` — TASK-968's staged
rollout. It is off unless a host is explicitly named, it can never let a browser
or a bad credential through, and it is documented at its definition below.
"""
from __future__ import annotations

import base64
import html
import logging
import os
import re
import secrets
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

import identity
from identity import flows as _flows  # step-up grants (TASK-1415); importing reads nothing
import models_auth
import passwords
import settings
from models_auth import (
    AuthEvent,
    AuthLoginThrottle,
    AuthServiceToken,
    AuthSession,
    AuthUser,
    resolve_role,
)

logger = logging.getLogger("atlas.auth")

__all__ = [
    "AuthGate", "Principal", "current_principal", "install", "verify_ready",
    "PUBLIC_ROUTES", "MUST_CHANGE_ALLOWLIST", "SESSION_COOKIE", "PRE_COOKIE",
    "mint_service_token", "token_warnings", "guard_write", "WRITE_SCOPE_ROUTES",
    "mint_browser_session", "session_mint_enabled", "SESSION_MINT_ENV",
]


# --------------------------------------------------------------------------- #
# 1. Configuration — fail closed at IMPORT, before uvicorn binds (ADR 0015)
# --------------------------------------------------------------------------- #

TOKEN_PEPPER_ENV = "ATLAS_TOKEN_PEPPER"
LOGIN_ORIGINS_ENV = "ATLAS_LOGIN_ORIGINS"
BEARER_HOSTS_ENV = "ATLAS_BEARER_HOSTS"
MACHINE_GRACE_ENV = "ATLAS_MACHINE_GRACE_HOSTS"

PEPPER_BYTES = 32

_TOKEN_PEPPER_HELP = (
    "%s is required and must be %d bytes of CSPRNG output, base64-encoded. Mint "
    "one with:  python -c \"import base64,os;print(base64.b64encode(os.urandom(%d))"
    ".decode())\"  then file it in KeePassXC and deliver it to the container as "
    "the env var itself or, preferably, as a file named by %s_FILE (a Docker "
    "secret; see settings.py — ATLAS_REQUIRE_SECRET_FILES=1 makes the file "
    "mandatory). atlas-api declares no `env_file:`, so either variable needs "
    "an explicit `environment:` entry "
    "(check with `docker compose config`, never by reading the .env). There is no "
    "generated-if-missing fallback: a per-boot random pepper would invalidate "
    "every live session and every service token on every reboot, and this box "
    "reboots unattended."
) % (TOKEN_PEPPER_ENV, PEPPER_BYTES, PEPPER_BYTES, TOKEN_PEPPER_ENV)

_ORIGINS_HELP = (
    "%s is required: a comma-separated list of the ABSOLUTE origins this app is "
    "reachable on, e.g. "
    "'https://atlas.example.com,https://atlas-lan.example.com:8443'. It is the "
    "Host allow-list (anything else gets 421 before a handler runs, which kills "
    "host-header injection and DNS rebinding in one rule) AND the Origin "
    "allow-list for every state-changing POST. There is no permissive default: "
    "an unset value would mean 'trust the Host header', which is the bug."
) % (LOGIN_ORIGINS_ENV,)


class MissingSecretError(RuntimeError):
    """A required auth secret / config value is absent. Raised at IMPORT.

    `variable` names the setting at fault, carried over from the settings error it
    wraps (TASK-1439), so the configuration catalogue blames that variable and not
    the setting whose reader happened to import this module."""

    def __init__(self, *args, variable: Optional[str] = None):
        super().__init__(*args)
        self.variable = variable


def _load_token_pepper() -> bytes:
    try:
        raw = settings.read_secret(TOKEN_PEPPER_ENV)
    except settings.SecretError as exc:
        raise MissingSecretError("%s %s" % (exc, _TOKEN_PEPPER_HELP),
                                 variable=TOKEN_PEPPER_ENV) from None
    try:
        pepper = base64.b64decode(raw, validate=True)
    except Exception as exc:  # binascii.Error and friends
        raise MissingSecretError(
            "%s is not valid base64 (%s). %s"
            % (TOKEN_PEPPER_ENV, type(exc).__name__, _TOKEN_PEPPER_HELP),
            variable=TOKEN_PEPPER_ENV,
        ) from None  # `from None`: never let the value near a traceback
    if len(pepper) != PEPPER_BYTES:
        raise MissingSecretError(
            "%s decodes to %d bytes, expected %d. %s"
            % (TOKEN_PEPPER_ENV, len(pepper), PEPPER_BYTES, _TOKEN_PEPPER_HELP),
            variable=TOKEN_PEPPER_ENV,
        )
    return pepper


def _load_settings() -> "settings.Settings":
    """The central settings (STORY-oss-06), read once at import. A malformed new
    variable fails closed here exactly like a missing origin list does."""
    try:
        return settings.load()
    except settings.SettingsError as exc:
        raise MissingSecretError(str(exc), variable=exc.variable) from None


def _load_origins(cfg: "settings.Settings") -> tuple[tuple[str, ...], dict[str, str]]:
    """Return (allowed origins, host -> origin).

    The host map is what the 421 check consults. Any absolute URL this app ever
    emits comes from THIS config and never from the request: `request.url_for()`
    yields `http://` behind cloudflared and would produce `http://` redirects on
    the public hostname.

    The list is `ATLAS_LOGIN_ORIGINS`, or, only when that is unset, the origin
    of `ATLAS_PUBLIC_URL` (`settings.load`). With neither set this still refuses
    to boot: there is no permissive default.
    """
    raw = cfg.login_origins.strip()
    source = cfg.login_origins_source or LOGIN_ORIGINS_ENV
    if not raw:
        raise MissingSecretError(
            "%s is not set (nor is %s, which it would be derived from). %s"
            % (LOGIN_ORIGINS_ENV, settings.PUBLIC_URL_ENV, _ORIGINS_HELP),
            variable=LOGIN_ORIGINS_ENV,
        )
    origins: list[str] = []
    hosts: dict[str, str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip().rstrip("/")
        if not chunk:
            continue
        parts = urlsplit(chunk)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise MissingSecretError(
                "%s entry %r is not an absolute origin like https://host[:port]. %s"
                % (source, chunk, _ORIGINS_HELP),
                variable=source,
            )
        origin = "%s://%s" % (parts.scheme.lower(), parts.netloc.lower())
        origins.append(origin)
        # The exact netloc maps to this origin; a host with the scheme's default
        # port may also arrive with or without it (`settings.origin_host_forms`:
        # the rule this loop always used, now shared with the derived bearer list).
        exact, *twins = settings.origin_host_forms(chunk)
        hosts[exact] = origin
        for twin in twins:
            hosts.setdefault(twin, origin)
    if not origins:
        raise MissingSecretError("%s parsed to nothing. %s" % (source, _ORIGINS_HELP),
                                 variable=source)
    return tuple(dict.fromkeys(origins)), hosts


_TOKEN_PEPPER = _load_token_pepper()
SETTINGS = _load_settings()
ALLOWED_ORIGINS, ALLOWED_HOSTS = _load_origins(SETTINGS)


def _load_identity() -> "settings.IdentitySettings":
    """Identity providers (STORY-oss-13), configured once at import. A malformed or
    unknown ATLAS_AUTH_PROVIDERS / ATLAS_LOCAL_LOGIN / ATLAS_OIDC_* value, or an
    unreadable OIDC secret file, stops the boot here. Default: `local`, today."""
    try:
        return identity.configure({
            # A flow's `state` is stored as a keyed digest, like every other secret here.
            "digest": lambda raw: _flow_state_digest(raw),
            "engine": lambda: _ENGINE,
        })
    except (settings.SettingsError, settings.SecretError) as exc:
        raise MissingSecretError(str(exc), variable=exc.variable) from None


IDENTITY = _load_identity()


def _hosts_from_env(name: str) -> frozenset[str]:
    return frozenset(
        chunk.strip().lower() for chunk in os.environ.get(name, "").split(",") if chunk.strip()
    )


#: Hosts on which `Authorization: Bearer` is honoured at all. TASK-968 asks this
#: to be DECIDED and recorded: all four machine callers reach the box over the
#: LAN or loopback, so refusing Bearer on the tunnel vhost costs nothing today
#: and closes an open-internet request path entirely — re-open it deliberately
#: when STORY-atlas-mcp TASK-959 needs it, not "in case".
#:
#: Unset means "accept on every allowed host". That default is deliberate and is
#: the SAFE one for the failure this story fears: a narrow default plus a
#: forgotten config line freezes the ingest silently. `install()` logs a WARN
#: when it is unset so the omission is visible at boot, and the deploy is
#: expected to set it to the LAN host only.
#:
#: A Bearer presented on a host NOT in this set is treated as an invalid token —
#: the SAME 401 with the same body and the same `WWW-Authenticate`, never a
#: distinguishable "wrong origin" error and never a 302.
#:
#: Read through `settings.load` (STORY-oss-06): `ATLAS_BEARER_HOSTS` as before,
#: or, only when that is unset and `ATLAS_PUBLIC_URL` is set, the public URL's
#: host forms. Neither set is today's "every allowed host".
BEARER_HOSTS = SETTINGS.bearer_hosts

#: `ATLAS_INTERNAL_HOSTS` (STORY-oss-06), default EMPTY = today. A host here is
#: admitted past the 421 Host check for a request that presents
#: `Authorization: Bearer` and carries NO cookie, and Bearer is honoured on it
#: even when BEARER_HOSTS is narrower. Nothing else is admitted on it: a cookie
#: session, an anonymous request or the login page on an internal host is still
#: 421, so the browser Host check (host-header injection, DNS rebinding) is
#: exactly as strict as before. It exists so the MCP can reach the API by its
#: compose service name instead of hair-pinning through the public hostname.
INTERNAL_HOSTS = SETTINGS.internal_hosts

#: TASK-968's staged rollout, and the ONLY configurable path through the gate.
#:
#: Step 1 issue every token and teach every caller to send it, while
#: unauthenticated machine calls STILL SUCCEED and log
#: `WARN unauthenticated caller <path> <ip> <ua>`; step 2 watch until that count
#: has been zero for 24 h; step 3 unset this and enforcement is live. It exists
#: because a caller nobody remembered is exactly the caller that freezes the
#: board, and step 2 is the only thing that turns "I think that's all of them"
#: into evidence. The BROWSER path has no such phase — it flips with this deploy.
#:
#: It is NOT `ATLAS_AUTH_DISABLED` and must never grow into it. Four hard
#: limits, all enforced in `_evaluate`:
#:   * empty/unset (the default) => it never applies anywhere;
#:   * it only applies on a host explicitly named here — never implicitly, and
#:     never on a host it was not configured for;
#:   * it never applies to a request that PRESENTED a credential: a bad Bearer
#:     or a bad cookie is still a 401/302, so it cannot mask a broken token;
#:   * it never applies to a browser-shaped request (one that accepts text/html),
#:     so it cannot serve the board to an anonymous visitor;
#:   * it never relaxes the must_change interlock, and the grace principal holds
#:     `read` and nothing else.
MACHINE_GRACE_HOSTS = settings.load_machine_grace_hosts()  # the same rule; settings.py, TASK-1433


# --------------------------------------------------------------------------- #
# 2. The exempt list. ADDITIONS REQUIRE AN ADR NOTE.
# --------------------------------------------------------------------------- #
#
# `/api/health` is deliberately NOT here — see the module docstring §2.
# `/auth/start` and `/auth/callback` are gone with the OIDC handshake.
#
# "Exempt" means the GATE does not refuse. `/auth/password/change` still
# requires a principal, enforced in its own handler, because a password change
# with no session is meaningless; it is exempt so that a `must_change`
# principal's path through the gate is one rule and not two.
#
# `/` (ADR 0035, 2026-08-25) is exempt for a different reason: the bare domain
# is now a public informational landing page for a stranger with no session at
# all. Unlike every other entry here it is NOT one fixed response — `app.py`'s
# `/` handler reads `current_principal` (still set below, even on an exempt
# route, whenever a credential WAS presented) and 302s a signed-in BROWSER
# straight to `/board`, which stays gated exactly as before and is NOT itself
# in this set.
PUBLIC_ROUTES = frozenset({
    "/",
    "/healthz",
    "/readyz",
    "/auth/login",
    "/auth/password/change",
    "/auth/denied",
    "/auth/privacy",
    "/favicon.ico",
})
# ADR 0041 §5 (STORY-oss-13): `/auth/<provider>/start` and `/auth/<provider>/callback`
# for each configured REDIRECT provider (OIDC). Empty unless ATLAS_AUTH_PROVIDERS names
# one, so the default set above is unchanged; `identity.public_routes` says why each is.
PUBLIC_ROUTES = PUBLIC_ROUTES | identity.public_routes()
# ADR 0047 (STORY-atlas-system-node-handbook): the handbook site, ONLY when ATLAS_PUBLIC_DOCS=1;
# unset, the set above is unchanged. Exact paths, never a prefix: /docs, /docs/ and one entry
# per generated file (`system_handbook.SITE_FILES`), so /docs/<anything else> stays gated. Why
# that is safe to hand a stranger: `routers/handbook.py` serves only those files, read from the
# image (never from the database, so nothing a user writes is published, including edits to
# the same documents on the atlas-system nodes), touches no table, reads no principal, and
# answers GET only. Its text is the public repository's `system_handbook.py` and passes the scrub
# gate in enforce mode. A request that PRESENTS a bad credential is still a 401 (step 2 below).
import system_handbook  # noqa: E402  (pure data; imports nothing)
if settings.load_public_docs():
    PUBLIC_ROUTES = PUBLIC_ROUTES | system_handbook.public_paths()
# ADR 0049 (TASK-1463): the product website's own stylesheet, script and images, ONLY when
# ATLAS_LANDING=v2; unset or v1, the set above is unchanged and `/` serves today's page. Exact
# paths, never a prefix: one entry per file in `landing_site.SITE_FILES`, so /site/<anything
# else> stays gated. Why that is safe to hand a stranger: `routers/landing.py` serves only those
# files, read from the image, touches no table, reads no principal and answers GET/HEAD only;
# the page itself makes no request the CSP does not allow (`connect-src 'none'`), reflects no
# input and names nothing from the running system. A request that PRESENTS a bad credential is
# still a 401 (step 2 below).
import landing_site  # noqa: E402  (pure data; imports nothing)
if settings.load_landing() == "v2":
    PUBLIC_ROUTES = PUBLIC_ROUTES | landing_site.public_paths()

# --------------------------------------------------------------------------- #
# Scope requirement for every mutating route (TASK-1085 design, TASK-1086
# enforcement). Keyed on (METHOD, ROUTE TEMPLATE) — not path alone — because
# three templates carry a `write` verb and an `admin` verb on the SAME path:
# "/api/node/{node_id}" is PATCH=write / DELETE=admin, and likewise
# "/api/node/{node_id}/doc" (PUT/DELETE) and "/api/intent/{intent_id}"
# (PATCH/DELETE).
#
# ONE authoritative list: `verify_object_graph.py`'s mutating-route assertion
# imports this dict rather than keeping its own copy, so a route added here
# with no scope decision — or omitted from here entirely — turns that check
# red instead of silently defaulting to allowed.
#
# Intent WRITES are `write`, not `admin`: EPIC-atlas-mcp overturned the rule
# that agents don't author intents, and pricing authoring at the destructive
# tier would force the MCP's token to carry every DELETE route just to write
# a sentence. Intent DELETES stay `admin`, like every other DELETE.
WRITE_SCOPE_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/node"): "write",
    ("PATCH", "/api/node/{node_id}"): "write",
    ("DELETE", "/api/node/{node_id}"): "admin",
    ("POST", "/api/node/{node_id}/status"): "write",
    ("POST", "/api/edge"): "write",
    ("DELETE", "/api/edge/{edge_id}"): "admin",
    ("POST", "/api/workitem"): "write",
    ("PATCH", "/api/workitem/{workitem_id}"): "write",
    # STORY-atlas-acceptance-human-approved. Proposing is ordinary work (`write`).
    # Approving and rejecting are refused to every non-session principal by the
    # routes themselves; `admin` here is the same belt-and-braces as the agent
    # credential routes below, so losing that guard degrades to "refused unless
    # admin" rather than to "any write token may approve its own proposal".
    ("POST", "/api/workitem/{workitem_id}/acceptance-proposal"): "write",
    ("POST", "/api/workitem/{workitem_id}/acceptance-proposal/{proposal_id}/approve"): "admin",
    ("POST", "/api/workitem/{workitem_id}/acceptance-proposal/{proposal_id}/reject"): "admin",
    ("PUT", "/api/node/{node_id}/doc"): "write",
    ("DELETE", "/api/node/{node_id}/doc"): "admin",
    ("POST", "/api/node/{node_id}/intent"): "write",
    ("PATCH", "/api/intent/{intent_id}"): "write",
    ("DELETE", "/api/intent/{intent_id}"): "admin",
    # Comments on a workitem (routers/comments.py): writing and editing are ordinary work;
    # a DELETE is `admin`, like every other DELETE.
    ("POST", "/api/workitem/{workitem_id}/comments"): "write",
    ("PATCH", "/api/comment/{comment_id}"): "write",
    ("DELETE", "/api/comment/{comment_id}"): "admin",
    ("PUT", "/api/adrs/{adr_id}"): "write",  # ADR 0045: the registry's one writer is adr_sync
    # The setup guide (STORY-atlas-setup-guide-adrs): owner-only routes, so `admin` (an owner's
    # agent runs the interview with a `read,write,admin` token). Accepting is ALSO refused to
    # every non-session principal by the route itself; `admin` here is the belt and braces.
    ("POST", "/api/setup/answers"): "admin",
    ("POST", "/api/setup/adrs/{adr_id}/accept"): "admin",
    # Access administration costs `admin`, never `write` — changing who may see
    # a project is not the same class of act as filing a task against it, and a
    # token minted for an agent to do ordinary work must not be able to re-share
    # a private one. The route is ALSO owner-only in app.py; this is the scope
    # half of the same refusal, and both are required (an owner's `read`-scoped
    # token must still be refused here).
    ("PUT", "/api/access/node/{node_id}"): "admin",
    # Groups and project role bindings (ADR 0041 §3, STORY-oss-11): the same
    # class of act as the row above. The routes also refuse every non-session
    # principal ("no token ever exercises owner", §4); this is the scope half.
    ("POST", "/api/access/groups"): "admin",
    ("PUT", "/api/access/groups/{group_id}/members"): "admin",
    ("DELETE", "/api/access/groups/{group_id}"): "admin",
    ("PUT", "/api/access/project/{project_id}/bindings"): "admin",
    # Minting a credential costs `admin`, and the entry is BELT AND BRACES.
    # `create_agent_credential` refuses every non-session principal before it
    # gets here — a Bearer caller must not be able to mint another Bearer
    # caller, or one leaked token becomes a key factory. This is the same
    # refusal expressed on the scope axis, so that removing the route's own
    # guard downgrades the failure to "refused unless admin" rather than to
    # "allowed". `admin` and not `write`: creating an account plus a credential
    # is not the class of act that files a task.
    ("POST", "/api/me/agent-credential"): "admin",
    ("POST", "/api/me/agent/{agent}/credential"): "admin",
    ("POST", "/api/me/agent-credential/{token_id}/revoke"): "admin",
    # GitHub repo access. Approving is the same class of act as re-sharing a
    # project, so it costs `admin` AND is owner-only AND session-CSRF (the
    # PUT /api/access/node/{id} mechanism, copied on purpose). Unlink is the
    # caller's own, session-only; `admin` here so a token is refused on the
    # scope axis as well as the kind axis.
    ("PUT", "/api/access/github/{user_id}"): "admin",
    ("POST", "/api/access/github/{user_id}/refresh"): "admin",
    ("POST", "/api/me/github/unlink"): "admin",
    # A personal password for an SSO person (TASK-1442). The route refuses every
    # non-session principal; `admin` is the belt, as for the routes above.
    ("POST", "/api/me/password"): "admin",
    # Crunch week (STORY-atlas-crunch-mode). Both routes refuse every non-session principal
    # (routers/crunch.py: no token, whatever its scopes, changes it); `admin` here is the belt,
    # so losing that guard degrades to "refused unless admin", never to "any write token".
    ("PUT", "/api/crunch"): "admin",
    ("POST", "/api/crunch/end"): "admin",
}

#: Everything a `must_change_password` principal may reach. Deliberately does
#: NOT include `/assets/*`: TASK-967 acceptance criterion 7 names
#: `/assets/d3.min.js` among the paths that must answer 302/403, and the
#: forced-change page carries no JavaScript, so denying it costs nothing and is
#: the fail-closed reading of the two places the task describes this list.
MUST_CHANGE_ALLOWLIST = frozenset({
    "/auth/login",
    "/auth/password/change",
    "/auth/logout",
    "/auth/denied",
    # A person part-way through a forced password change is exactly the person
    # deciding whether to continue at all. Denying them the notice about their
    # own data would be the wrong answer to the only question it answers.
    "/auth/privacy",
    "/healthz",
    "/readyz",
    "/favicon.ico",
})

#: WHO TO WRITE TO ABOUT YOUR DATA (TASK-941). Rendered on the public privacy
#: page, so this address is visible to anyone who can reach the login screen —
#: including over the public tunnel. It is the one value in that page nobody but
#: the owner can decide, which is why it is a named constant and not buried in the
#: HTML. It changes again when the AB is registered and the controller stops
#: being a person — one line, same as this one was.
#:
#: ⚠ THIS MAILBOX MUST ACTUALLY ROUTE SOMEWHERE. It is the only address on a
#: page that promises a copy, a correction or a deletion on request, so an
#: address that bounces makes the one operative sentence on the notice false.
#: The install's domain's DNS is already on Cloudflare (the LAN certificate is issued
#: DNS-01 through it), so Cloudflare Email Routing can forward this to a real
#: inbox without running a mail server. Chosen over a personal address on
#: purpose (owner decision, 2026-08-16): this page is readable anonymously over the
#: public tunnel, and a personal address on it is public forever.
#:
#: The value is `settings.DEFAULT_PRIVACY_CONTACT` unless `ATLAS_PRIVACY_CONTACT`
#: overrides it (STORY-oss-06); the default is unchanged.
PRIVACY_CONTACT = SETTINGS.privacy_contact

#: The privacy page's "Where it lives" paragraph: `settings.DEFAULT_PRIVACY_HOSTING`
#: (the original install's hosting) unless `ATLAS_PRIVACY_HOSTING` overrides it
#: (TASK-1459). Plain text, escaped where it is rendered.
PRIVACY_HOSTING = SETTINGS.privacy_hosting

#: The controller, TODAY. GDPR attaches to whoever is controller at the time,
#: and the company does not exist yet (registration is deferred to ~2026-10),
#: so the answer is a natural person and saying otherwise would be false.
#: The value is `distribution.DEFAULT_PRIVACY_CONTROLLER` (STORY-oss-23 A4b), unchanged.
PRIVACY_CONTROLLER = settings.distribution.DEFAULT_PRIVACY_CONTROLLER
#: The sentence after the controller line, HTML: `distribution.PRIVACY_COMPANY_NOTE`
#: (A4b), unchanged. Empty in the neutral copy, and the paragraph then ends at "holds it."
PRIVACY_COMPANY_NOTE = settings.distribution.PRIVACY_COMPANY_NOTE

SESSION_COOKIE = "__Host-atlas_session"
PRE_COOKIE = "__Host-atlas_pre"

SESSION_IDLE_SECONDS = 24 * 60 * 60          # sliding
SESSION_ABSOLUTE_SECONDS = 14 * 24 * 60 * 60  # hard ceiling
PRE_SESSION_SECONDS = 15 * 60
LAST_SEEN_WRITE_INTERVAL = 60                 # do not UPDATE on every board fetch
SERVICE_TOKEN_DEFAULT_DAYS = 365
TOKEN_EXPIRY_WARN_DAYS = 30
TOKEN_UNUSED_WARN_DAYS = 90

#: The ONE unauthenticated body. Revoked token, expired token, token that never
#: existed, missing cookie and expired cookie all produce exactly this.
_UNAUTHORIZED_BODY = {"error": "unauthorized", "detail": "Authentication is required."}
_FORBIDDEN_CHANGE_BODY = {
    "error": "password_change_required",
    "detail": "This account must set a new password before anything else is available.",
    "change_password": "/auth/password/change",
}

_SERVICE_TOKEN_RE = re.compile(r"^atlas_svc_([0-9a-f]{6})_([A-Za-z0-9_-]{43})$")


# --------------------------------------------------------------------------- #
# 3. The principal, and the ContextVar STORY-atlas-node-visibility needs
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Principal:
    """Who is making this request. Holds NO secret — never the raw sid."""

    kind: str                       # session | token | grace
    user_id: Optional[str]
    subject: Optional[str]
    role: str
    is_service: bool
    must_change_password: bool
    scopes: tuple[str, ...] = ()
    session_id_hash: Optional[str] = None
    token_id: Optional[str] = None
    label: Optional[str] = None

    #: The PERSON this agent belongs to (ADR 0034 / TASK-1114), or None. Set on
    #: a token principal only — a person has no person, and a session principal
    #: therefore leaves it None.
    #:
    #: AN ID, AND DELIBERATELY NOTHING ELSE. Grants inherit down this link and
    #: ROLE NEVER DOES, and the cheapest way to guarantee that is to make the
    #: other direction unrepresentable: there is no `person_role` field here and
    #: no reference to the person's row, so `is_owner` cannot be consulted
    #: through the link because there is nothing to consult. A future field
    #: carrying the person's role would silently make an owner's agent
    #: unrestricted, which is the one outcome ADR 0034 exists to prevent —
    #: `verify_agent_identity.py` asserts on this field list for that reason.
    person_user_id: Optional[str] = None

    @property
    def is_owner(self) -> bool:
        """THIS principal's own role. Never the linked person's — see
        `person_user_id`. An agent's own row is always `member`, including the
        owner's own agents, which is what makes grant inheritance safe."""
        return self.role == models_auth.ROLE_OWNER

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


#: SET before `call_next` and RESET in a `finally`, on every request.
#:
#: NOT only `request.state`: STORY-atlas-node-visibility enforces from a
#: `Session`-class event that has no request handle (TASK-972 item 4), and
#: TASK-979 asserts on this by name. anyio copies the ambient context into the
#: threadpool worker that runs a `def` endpoint, so a synchronous handler and
#: any SQLAlchemy event it triggers both observe the value set here.
current_principal: ContextVar[Optional[Principal]] = ContextVar(
    "atlas_current_principal", default=None
)


# --------------------------------------------------------------------------- #
# 4. Engine wiring. `install()` supplies it; nothing here imports app.py, so
#    there is no import cycle.
# --------------------------------------------------------------------------- #

_ENGINE: Optional[Engine] = None
_FRONTEND_DIR: str = ""

#: Per-IP failure ceiling (TASK-965 owns the numbers; this task owns the keys).
#:
#: THE KEY IS NAMESPACED, and that is load-bearing. Bearer failures and login
#: failures are counted in SEPARATE buckets, because behind cloudflared every
#: public request shares one socket peer until `ATLAS_TRUSTED_PROXY_CIDRS` is
#: configured. Sharing one bucket would mean twenty bogus `Authorization`
#: headers from anywhere on the internet lock the LOGIN FORM for everybody —
#: an attacker-triggered outage of the only way in, caused by a missing config
#: line. Separate buckets keep each control local to its own failure mode.
#:
#: A SUCCESSFUL request is never counted, in either bucket. That is what makes
#: it impossible to throttle a healthy ingest no matter how many calls it makes
#: — the failure this repo has already lived once.
_IP_LIMITER = passwords.IpFailureLimiter()


def _login_key(ip: str) -> str:
    return "login:" + ip


def _bearer_key(ip: str) -> str:
    return "bearer:" + ip


@contextmanager
def _session():
    if _ENGINE is None:  # pragma: no cover - install() is unconditional in app.py
        raise RuntimeError("auth.install() was never called; refusing to resolve a principal")
    with Session(_ENGINE) as s:
        yield s


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: Optional[datetime]) -> Optional[datetime]:
    """Normalise to UTC-aware before any comparison (Postgres already returns
    aware values for a `DateTime(timezone=True)` column — this is a defensive
    no-op on the one dialect this runs against, not a dialect workaround)."""
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 5. Digests. Every secret compared here is high-entropy, so a keyed SHA-256 is
#    right and Argon2 would be a self-inflicted DoS (TASK-968 §"Token design").
# --------------------------------------------------------------------------- #

def _session_digest(sid: str) -> str:
    return passwords.digest_secret(sid, label="session-v1", key=_TOKEN_PEPPER)


def _csrf_for(sid: str) -> str:
    """The synchronizer token for a session, derived from its secret.

    Deriving rather than storing means the server can recompute it from the
    cookie it already holds — so a server-rendered form and `/api/me` can both
    carry it — while an attacker without the cookie cannot produce it, because
    the derivation is keyed by the pepper. `auth_session.csrf_hash` stores its
    digest and is verified as well.
    """
    return passwords.digest_secret(sid, label="csrf-token-v1", key=_TOKEN_PEPPER)


def _csrf_digest(token: str) -> str:
    return passwords.digest_secret(token, label="csrf-hash-v1", key=_TOKEN_PEPPER)


def _token_digest(token: str) -> str:
    return passwords.digest_secret(token, label="service-token-v1", key=_TOKEN_PEPPER)


# --------------------------------------------------------------------------- #
# 6. Audit. NEVER log or store a password, a temp password, a raw cookie or a
#    raw service token (ASVS 16.2.5). A submitted username is attacker
#    controlled, so everything written here is encoded first — a raw newline in
#    `reason` or `detail` is log injection.
# --------------------------------------------------------------------------- #

def _clean(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = "".join(ch if ch.isprintable() else "?" for ch in text)
    return text[:limit] or None


def _event(
    s: Session,
    event: str,
    *,
    user_id: Optional[str] = None,
    actor: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    reason: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    s.add(AuthEvent(
        event=_clean(event, 48) or "unknown",
        user_id=user_id,
        actor=_clean(actor, 64),
        ip=_clean(ip, 64),
        user_agent=_clean(user_agent, 256),
        reason=_clean(reason, 256),
        detail=_clean(detail, 2000),
    ))


# --------------------------------------------------------------------------- #
# 7. Request helpers
# --------------------------------------------------------------------------- #

def _peer(request: Request) -> str:
    return request.client.host if request.client else ""


def _client_ip(request: Request) -> str:
    """TASK-965 owns the extraction rule; this task only calls it.

    `CF-Connecting-IP` is believed ONLY when the socket peer is inside
    `ATLAS_TRUSTED_PROXY_CIDRS`. Behind cloudflared every public request shares
    one socket peer, so a ceiling keyed on the peer would be a GLOBAL ceiling —
    the first attacker to trip it locks out every other visitor.
    """
    return passwords.client_ip(_peer(request), request.headers)


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")


def _request_host(request: Request) -> str:
    return (request.headers.get("host") or "").strip().lower()


def _accepts_html(request: Request) -> bool:
    return "text/html" in (request.headers.get("accept") or "").lower()


def _wants_html_redirect(request: Request) -> bool:
    """The ONE place 302-vs-401 is decided.

    All four conditions must hold. The `Authorization` clause is the important
    one: a request that presented a Bearer token is NEVER answered with a 302,
    whatever it says it accepts. HEAD is included because `curl -sSI` — which
    the acceptance criteria use — sends HEAD, and Starlette routes HEAD at the
    GET handler.
    """
    if request.method not in ("GET", "HEAD"):
        return False
    if request.headers.get("authorization"):
        return False
    if request.url.path.startswith("/api/"):
        return False
    return _accepts_html(request)


def _json(status: int, body: dict, headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers=headers)


MAX_FORM_BYTES = 64 * 1024


async def _read_form(request: Request) -> dict[str, str]:
    """Parse an `application/x-www-form-urlencoded` body, and ONLY that.

    Deliberately not `request.form()`: Starlette routes that through
    `python-multipart` even for urlencoded bodies, and this app has no reason to
    accept a multipart login. Adding a parser dependency to serve the most
    security-sensitive endpoint in the app is the wrong trade — and narrowing
    the accepted content type means a cross-site form (which cannot set
    arbitrary headers but CAN post multipart) never reaches the credential path
    at all.

    A body over `MAX_FORM_BYTES` is discarded rather than parsed: the login POST
    is unauthenticated and internet-facing.
    """
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype != "application/x-www-form-urlencoded":
        return {}
    raw = await request.body()
    if len(raw) > MAX_FORM_BYTES:
        logger.warning("form body of %d bytes discarded on %s", len(raw), request.url.path)
        return {}
    parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items() if values}


def _safe_next(value: Optional[str]) -> str:
    """Only same-origin RELATIVE paths, or the login page is an open redirect.

    Rejects anything with a scheme, anything protocol-relative (`//evil`), and
    anything with a backslash (which some agents fold to `/`).
    """
    if not value:
        return "/"
    if not value.startswith("/") or value.startswith("//") or value.startswith("/\\"):
        return "/"
    if "\\" in value or "\n" in value or "\r" in value:
        return "/"
    return value


def _login_redirect(request: Request) -> RedirectResponse:
    target = request.url.path
    if request.url.query:
        target = "%s?%s" % (target, request.url.query)
    target = _safe_next(target)
    location = "/auth/login"
    if target != "/":
        location = "%s?next=%s" % (location, quote(target, safe=""))
    # 302, and a RELATIVE Location: an absolute URL derived from the request is
    # host-header injection, and one derived from config is unnecessary here.
    return RedirectResponse(location, status_code=302)


def _set_session_cookie(response: Response, sid: str) -> None:
    # No `Max-Age` and no `Expires`: a non-persistent cookie whose real lifetime
    # is the server-side row, because the client controls the cookie.
    response.set_cookie(
        SESSION_COOKIE, sid, path="/", httponly=True, secure=True, samesite="lax",
    )


def _set_pre_cookie(response: Response, sid: str) -> None:
    response.set_cookie(
        PRE_COOKIE, sid, path="/", httponly=True, secure=True, samesite="lax",
        max_age=PRE_SESSION_SECONDS,
    )


def _clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, path="/", httponly=True, secure=True, samesite="lax")


# --------------------------------------------------------------------------- #
# 8. Principal resolution — exactly two branches
# --------------------------------------------------------------------------- #

def session_auth_method(row: AuthSession, user: AuthUser) -> str:
    """How session `row` of `user` was established (§5, TASK-1454).

    `auth_session.auth_method` when recorded. NULL is a row from before the column,
    and means what it meant then: a local account's sessions were password sign-ins,
    and an IdP-backed account's were SSO ones, because it could not sign in with a
    password before TASK-1454 and `link-idp` ended every session it had. Every
    session minted since records its method (`_mint_session` requires one)."""
    if row.auth_method:
        return row.auth_method
    return identity.AUTH_PASSWORD if identity.is_local(user) else identity.AUTH_SSO


def forced_change_applies(user: AuthUser, method: Optional[str]) -> bool:
    """Does the forced-change interlock hold a credential of `user` established by
    `method`? `identity.forced_change_applies`, the one rule (§5, TASK-1442/1454)."""
    return identity.forced_change_applies(user, method)


def password_pending(user: AuthUser) -> bool:
    """An IdP-backed person whose account still carries `must_change_password`: they
    signed in through the IdP and have no personal Atlas password yet (TASK-1442)."""
    return bool(user.must_change_password) and not identity.is_local(user)         and not user.is_service


def _resolve_session(s: Session, sid: str) -> Optional[Principal]:
    """Cookie branch. Returns None for anything not a live, full session."""
    id_hash = _session_digest(sid)
    row = s.get(AuthSession, id_hash)
    if row is None:
        return None
    # Belt and braces: the PK lookup already matched, but the rule is
    # compare_digest for every secret comparison, never `==`.
    if not passwords.constant_time_equals(row.id_hash, id_hash):  # pragma: no cover
        return None
    if row.kind != "full" or not row.user_id:
        return None

    now = _now()
    absolute = _aware(row.absolute_expires_at)
    last_seen = _aware(row.last_seen_at) or _aware(row.created_at) or now
    if absolute is not None and absolute <= now:
        s.delete(row)
        s.commit()
        return None
    if (now - last_seen).total_seconds() >= SESSION_IDLE_SECONDS:
        s.delete(row)
        s.commit()
        return None

    user = s.get(AuthUser, row.user_id)
    if user is None or user.disabled_at is not None or user.is_service:
        # Disabling a user kills their sessions on the next request, without a
        # restart and without a background job.
        s.delete(row)
        s.commit()
        return None

    if (now - last_seen).total_seconds() >= LAST_SEEN_WRITE_INTERVAL:
        row.last_seen_at = now
        s.commit()

    return Principal(
        kind="session",
        user_id=user.id,
        subject=user.subject,
        role=resolve_role(user.role),
        is_service=False,
        # RE-READ FROM auth_user ON EVERY REQUEST. A cached copy is a bypass
        # with no revocation path. A password sign-in only, read off how THIS
        # session began, not off the account (§5, TASK-1442/1454).
        must_change_password=forced_change_applies(user, session_auth_method(row, user)),
        scopes=(),
        session_id_hash=row.id_hash,
        label=user.name or user.subject,
    )


def _resolve_token(s: Session, raw: str) -> Optional[Principal]:
    """Bearer branch. Every failure returns None, indistinguishably."""
    match = _SERVICE_TOKEN_RE.match(raw)
    if match is None:
        return None
    prefix = match.group(1)
    row = s.scalar(select(AuthServiceToken).where(AuthServiceToken.prefix == prefix))
    if row is None:
        return None
    if not passwords.constant_time_equals(row.token_hash, _token_digest(raw)):
        return None

    now = _now()
    if row.revoked_at is not None:
        return None
    expires = _aware(row.expires_at)
    if expires is not None and expires <= now:
        return None
    scopes = tuple(sc for sc in (row.scopes or "").split(",") if sc)
    if not scopes:
        # Deny by default: an empty scope string is a BROKEN token, never a
        # superuser. The DB CHECK forbids it; this is the second line.
        logger.error("service token %s has no scopes; refusing it", prefix)
        return None

    owner = s.get(AuthUser, row.owner_user_id)
    if owner is None or owner.disabled_at is not None or not owner.is_service:
        return None

    # OFFBOARDING REACHES MACHINES (ADR 0034 / TASK-1113). `admin user disable`
    # already stamps `disabled_at` on every agent linked to the person, and that
    # is what makes re-enabling them a deliberate, per-agent act. This is the
    # second line, for the routes that never pass through that command:
    #
    #   * the login-throttle HARD DISABLE (`_record_login_failure`, below) sets
    #     `disabled_at` on a person directly and has no idea agents exist;
    #   * `bot link` can attach an agent to an ALREADY-disabled person, which no
    #     cascade will ever revisit;
    #   * anything that disables a row by hand.
    #
    # It reads the PERSON'S `disabled_at` and NOTHING ELSE — never their role,
    # never their grants. Grant inheritance is the resolver's job and role is
    # inherited nowhere (see Principal.person_user_id).
    if owner.person_user_id:
        person = s.get(AuthUser, owner.person_user_id)
        if person is not None and person.disabled_at is not None:
            return None
        # ADR 0041 §6 (STORY-oss-13): an IdP-backed person's agents stop when that
        # person has not completed an OIDC login within ATLAS_OIDC_REVALIDATE_DAYS.
        # Suspended, not revoked: their next login lifts it. A LOCAL person (every
        # account on an install without OIDC) answers False without a read.
        if person is not None and identity.agent_tokens_suspended(s, person, now=now):
            return None
    if owner.must_change_password:
        # A must_change principal cannot USE a token either (TASK-968).
        return None

    last_used = _aware(row.last_used_at)
    if last_used is None:
        row.last_used_at = now
        _event(s, "token_used_first_time", user_id=owner.id, reason=prefix,
               detail=_clean(row.label, 200))
        s.commit()
    elif (now - last_used).total_seconds() >= LAST_SEEN_WRITE_INTERVAL:
        row.last_used_at = now
        s.commit()

    return Principal(
        kind="token",
        user_id=owner.id,
        subject=owner.subject,
        role=resolve_role(owner.role),
        is_service=True,
        must_change_password=False,
        scopes=scopes,
        token_id=row.id,
        label=row.label or owner.label or owner.subject,
        # Free: the bot's row is already loaded above. Carrying the id here
        # rather than looking it up in the resolver keeps "who is this caller"
        # one query, in the one place that answers it.
        person_user_id=owner.person_user_id,
    )


def _resolve_bearer(s: Session, raw: str):
    """The Bearer branch's dispatch: (Principal or None, access-token refusal or None).

    `atlas_svc_...` takes `_resolve_token`, unchanged, ALWAYS, and so does every
    Bearer while ATLAS_OAUTH_RESOURCE is unset (the default: today, byte for byte).
    With it set, anything else that is shaped like a JWT is an IdP access token
    (ADR 0041 §9, STORY-oss-25), checked by `identity.access_token`, and acts as
    the agent account of (its person, its OAuth client): kind `token` like a
    minted credential, so CSRF exemption, scope checks, RBAC caps, read audit and
    `graph_event` attribution treat it exactly as one. `token_id` is None: there
    is no `auth_service_token` row behind it.
    """
    if raw.startswith("atlas_svc_") or not identity.oauth_enabled():
        return _resolve_token(s, raw), None
    from identity import access_token
    if not access_token.looks_like_jwt(raw):
        return None, None
    try:
        hit = access_token.resolve(s, raw, provider=identity.provider("oidc"),
                                   cfg=identity.OAUTH, suspended=identity.agent_tokens_suspended,
                                   now=_now())
    except access_token.Refused as exc:
        return None, exc
    return Principal(
        kind="token",
        user_id=hit.agent_id,
        subject=hit.agent_subject,
        role=models_auth.ROLE_MEMBER,     # the agent's own row is always member (ADR 0034)
        is_service=True,
        must_change_password=False,
        scopes=hit.scopes,
        label=hit.label,
        person_user_id=hit.person_id,
    ), None


# --------------------------------------------------------------------------- #
# 9. The gate
# --------------------------------------------------------------------------- #

@dataclass
class _Outcome:
    response: Optional[Response] = None
    principal: Optional[Principal] = None
    clear_session_cookie: bool = False


def _evaluate(request: Request) -> _Outcome:
    """Everything the gate decides, synchronously. Runs in a threadpool.

    Reads only scope-level attributes (method, url, headers, cookies, client) —
    never the body, which the handler still needs.
    """
    path = request.url.path
    host = _request_host(request)

    # -- 0. Host allow-list. Before ANY handler, and before any DB work. ------
    # Kills host-header injection and DNS rebinding in one rule. The origin used
    # in anything absolute comes from config, never from the request.
    header_auth = request.headers.get("authorization")
    cookie_sid = request.cookies.get(SESSION_COOKIE)
    # ATLAS_INTERNAL_HOSTS (default empty, so this is False everywhere unless
    # configured): Bearer only, and only with no cookie of any kind, so a
    # browser is never served on an internal name.
    internal = (
        host not in ALLOWED_HOSTS
        and SETTINGS.is_internal_host(host)
        and header_auth is not None
        and header_auth.partition(" ")[0].lower() == "bearer"
        and not request.headers.get("cookie")
    )
    if host not in ALLOWED_HOSTS and not internal:
        return _Outcome(response=_json(421, {
            "error": "misdirected_request",
            "detail": "This server does not answer for that host.",
        }))

    # -- 1. Both credential types present -> refuse outright, never resolve. --
    if header_auth and cookie_sid:
        return _Outcome(response=_json(400, {
            "error": "ambiguous_credentials",
            "detail": "Present a session cookie or an Authorization header, never both.",
        }))

    principal: Optional[Principal] = None
    bad_credential = False
    clear_cookie = False
    refusal = None          # an IdP access token's refusal (STORY-oss-25), else None

    with _session() as s:
        if header_auth:
            scheme, _, value = header_auth.partition(" ")
            if scheme.lower() != "bearer" or not value.strip():
                bad_credential = True
            elif BEARER_HOSTS and host not in BEARER_HOSTS and not internal:
                # Bearer is not honoured on this origin. Indistinguishable from
                # a bad token on purpose — never a distinct "wrong origin" hint.
                bad_credential = True
            else:
                principal, refusal = _resolve_bearer(s, value.strip())
                bad_credential = principal is None
        elif cookie_sid:
            principal = _resolve_session(s, cookie_sid)
            # An expired or unknown cookie is ORDINARY (it is what a browser
            # carries on its way back to the login page), so it is treated as
            # anonymous and cleared — not as a 401.
            clear_cookie = principal is None

        ip = _client_ip(request)

        # -- 2. A presented-but-invalid Bearer is always a 401. ---------------
        if bad_credential:
            decision = _IP_LIMITER.record_failure(_bearer_key(ip))
            _event(s, "login_fail", ip=ip, user_agent=_user_agent(request),
                   reason=("oauth:%s" % refusal.reason) if refusal is not None else "bearer_invalid",
                   detail=_clean(path, 200))
            s.commit()
            if not decision.allowed:
                # Token spraying is now an open-internet request path. A VALID
                # token never reaches this branch, so the ingest can never be
                # throttled by it.
                return _Outcome(response=_json(
                    passwords.HTTP_TOO_MANY_REQUESTS,
                    {"error": "rate_limited", "detail": "Too many failed attempts."},
                    passwords.retry_after_headers(decision.retry_after_seconds)))
            return _Outcome(response=_unauthorized(
                presented=True, detail=refusal.public if refusal is not None else None))

        # -- 3. No principal. ------------------------------------------------
        if principal is None:
            if path in PUBLIC_ROUTES:
                return _Outcome(principal=None, clear_session_cookie=clear_cookie)
            grace = _grace_principal(request, host, header_auth, cookie_sid)
            if grace is not None:
                logger.warning(
                    "unauthenticated caller %s %s %s %r — allowed by %s; this MUST "
                    "reach zero before enforcement is flipped (TASK-968)",
                    request.method, path, ip, _clean(_user_agent(request), 120),
                    MACHINE_GRACE_ENV,
                )
                return _Outcome(principal=grace)
            if _wants_html_redirect(request):
                response = _login_redirect(request)
            else:
                response = _unauthorized(presented=False)
            if clear_cookie:
                _clear_cookie(response, SESSION_COOKIE)
            return _Outcome(response=response)

        # -- 4. THE FORCED-CHANGE INTERLOCK, at the gate. --------------------
        if principal.must_change_password and path not in MUST_CHANGE_ALLOWLIST:
            if _wants_html_redirect(request):
                return _Outcome(response=RedirectResponse("/auth/password/change", status_code=302))
            return _Outcome(response=_json(403, _FORBIDDEN_CHANGE_BODY))

    return _Outcome(principal=principal, clear_session_cookie=clear_cookie)


def _unauthorized(*, presented: bool, detail: Optional[str] = None) -> JSONResponse:
    """One body, one status, for every unauthenticated outcome.

    RFC 6750: `error="invalid_token"` describes a credential that was PRESENTED
    and rejected; a bare `Bearer` challenge is right when none was sent. Never
    `Basic` — browsers render a native credential popup for it.

    `detail` is set for ONE case only: a genuine IdP access token (signed, for
    this resource) whose person Atlas cannot serve yet (STORY-oss-25,
    `identity.access_token._PUBLIC`). Every other refusal is the one body.
    """
    challenge = 'Bearer error="invalid_token"' if presented else "Bearer"
    if detail is None:
        return _json(401, _UNAUTHORIZED_BODY, {"WWW-Authenticate": challenge})
    return _json(401, {"error": "unauthorized", "detail": detail},
                 {"WWW-Authenticate": '%s, error_description="%s"' % (challenge, detail)})


def _grace_principal(
    request: Request, host: str, header_auth: Optional[str], cookie_sid: Optional[str]
) -> Optional[Principal]:
    """TASK-968 step 1: let an unauthenticated MACHINE call through with a WARN.

    Refuses to apply unless every one of the limits in the module docstring §9
    holds. In particular it never applies to a browser-shaped request and never
    to a request that presented a credential.
    """
    if not MACHINE_GRACE_HOSTS or host not in MACHINE_GRACE_HOSTS:
        return None
    if header_auth or cookie_sid:
        return None
    if _accepts_html(request):
        return None
    return Principal(
        kind="grace", user_id=None, subject=None, role=models_auth.ROLE_MEMBER,
        is_service=True, must_change_password=False, scopes=("read",),
        label="unauthenticated-caller",
    )


class AuthGate(BaseHTTPMiddleware):
    """Deny by default over EVERY route, the deleted mount and every 404.

    Registered with `app.add_middleware`, so it wraps the whole router — routes
    added after it included. That is the property `Depends` cannot give.
    """

    async def dispatch(self, request: Request, call_next):
        if _ENGINE is None:  # pragma: no cover - install() is unconditional
            return _json(503, {"error": "unavailable", "detail": "auth is not initialised"})
        try:
            outcome = await run_in_threadpool(_evaluate, request)
        except Exception:  # noqa: BLE001 — a broken gate must never serve open
            logger.exception("AuthGate could not evaluate the request; refusing")
            return _json(503, {"error": "unavailable", "detail": "authentication unavailable"})

        if outcome.response is not None:
            return outcome.response

        request.state.principal = outcome.principal
        token = current_principal.set(outcome.principal)
        try:
            response = await call_next(request)
        finally:
            # RESET IN A finally — TASK-979 asserts on exactly this.
            current_principal.reset(token)
        if outcome.clear_session_cookie:
            _clear_cookie(response, SESSION_COOKIE)
        return response


# --------------------------------------------------------------------------- #
# 10. CSRF + Origin, for every state-changing request
# --------------------------------------------------------------------------- #

class CsrfFailure(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _check_origin(request: Request) -> None:
    """Reject if `Origin` is present and not allow-listed; fall back to
    `Referer`; reject if BOTH are absent."""
    origin = request.headers.get("origin")
    if origin:
        if origin.strip().rstrip("/").lower() not in ALLOWED_ORIGINS:
            raise CsrfFailure("bad_origin")
        return
    referer = request.headers.get("referer")
    if referer:
        parts = urlsplit(referer)
        candidate = "%s://%s" % (parts.scheme.lower(), parts.netloc.lower())
        if candidate not in ALLOWED_ORIGINS:
            raise CsrfFailure("bad_referer")
        return
    raise CsrfFailure("no_origin")


def _check_csrf(sid: Optional[str], row: Optional[AuthSession], submitted: Optional[str]) -> None:
    if not sid or row is None:
        raise CsrfFailure("no_session")
    if not submitted:
        raise CsrfFailure("missing_token")
    expected = _csrf_for(sid)
    if not passwords.constant_time_equals(submitted, expected):
        raise CsrfFailure("token_mismatch")
    if not passwords.constant_time_equals(row.csrf_hash, _csrf_digest(expected)):
        raise CsrfFailure("token_stale")


def guard_write(request: Request, csrf_token: Optional[str]) -> Optional[JSONResponse]:
    """Authorise a state-changing API call made from OUTSIDE this module.

    Returns None when the caller may proceed, or the response to send back.
    `/auth/*` does its own inline version of this; everything else — the first
    of which is `POST /api/node/{id}/status` — goes through here, so there is
    one implementation of "may this request change data" rather than one per
    route that later drifts.

    The two principal kinds are treated DIFFERENTLY, and deliberately:

      session — cookie-borne authority, which a third-party page can make the
                browser attach to a cross-site request. Full CSRF defence
                applies: the Origin/Referer allow-list AND the synchronizer
                token from GET /api/me, checked against the digest stored on
                the session row.

      token   — `Authorization: Bearer atlas_svc_…`, and `grace` (the
                dormant `ATLAS_MACHINE_GRACE_HOSTS` bypass — set in neither
                overlay today, but a live code path, not a hypothetical one).
                There is nothing ambient to abuse: no browser attaches that
                header on its own, so a cross-site request simply arrives
                unauthenticated. Requiring an Origin here would only break
                the legitimate callers (agents, MCP, scripted health checks),
                which send none — and would buy no CSRF protection at all.
                What it DOES need, and what CSRF exemption used to skip
                entirely (TASK-1086), is a SCOPE check — see
                `_check_write_scope` below.

    The gate has already run by the time this is called, so `current_principal`
    is set; the None branch is the belt for a route somehow reached without it.
    """
    principal = current_principal.get()
    if principal is None:  # pragma: no cover - AuthGate refuses first
        return _unauthorized(presented=False)
    if principal.kind != "session":
        return _check_write_scope(request, principal)

    sid = request.cookies.get(SESSION_COOKIE)
    try:
        _check_origin(request)
        with _session() as s:
            row = s.get(AuthSession, _session_digest(sid)) if sid else None
            _check_csrf(sid, row, csrf_token)
    except CsrfFailure as exc:
        with _session() as s:
            _event(s, "bad_origin" if exc.reason.startswith(("bad_", "no_origin")) else "csrf_reject",
                   user_id=principal.user_id, actor=principal.subject,
                   ip=_client_ip(request), user_agent=_user_agent(request),
                   reason=exc.reason, detail=str(request.url.path))
            s.commit()
        return _json(403, {"error": "csrf_failed",
                           "detail": "Reload the board and try again."})
    return None


def _check_write_scope(request: Request, principal: Principal) -> Optional[JSONResponse]:
    """Scope check for every NON-SESSION principal on a mutating route
    (TASK-1086). This used to be the `return None` a token or grace
    principal hit unconditionally — the bug this task fixes: a token minted
    `--scopes read` could reach any write or delete route, because nothing
    downstream of the CSRF exemption ever consulted `has_scope`.

    FAILS CLOSED. `WRITE_SCOPE_ROUTES` is keyed on the route's TEMPLATE path
    (`request.scope["route"].path`, which FastAPI's `APIRoute.matches` sets —
    not `request.url.path`, which carries the real id and would never match
    a `{node_id}`-shaped key) and its method. A route with no entry — a new
    mutating route nobody added here, or this function reached from a route
    that was never registered as mutating at all — is REFUSED, never allowed
    by default.
    """
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    needed = WRITE_SCOPE_ROUTES.get((request.method, route_path))
    if needed is None:  # pragma: no cover - belt: every real route is mapped
        return _json(403, {
            "error": "forbidden",
            "detail": "no scope is defined for this route; refused",
        })
    if not principal.has_scope(needed):
        return _json(403, {
            "error": "forbidden",
            "detail": "missing required scope: %s" % needed,
        })
    return None


# --------------------------------------------------------------------------- #
# 11. Session mint / rotate
# --------------------------------------------------------------------------- #

def _mint_session(s: Session, *, kind: str, user_id: Optional[str], request: Request,
                  auth_method: Optional[str], ttl_seconds: Optional[int] = None) -> str:
    """Return the RAW sid. Only the digest is ever stored.

    `auth_method` is REQUIRED, with no default, so no caller can forget it: how a
    full session was established (identity.AUTH_PASSWORD / AUTH_SSO), which the
    forced-change gate reads (§5, TASK-1454); None for a pre-session.

    `ttl_seconds` only ever SHORTENS the absolute lifetime (an OIDC session is
    bounded by ATLAS_OIDC_MAX_SESSION_HOURS, ADR 0041 §6); every existing caller
    omits it and gets today's value."""
    sid = secrets.token_urlsafe(32)   # 256 bits
    now = _now()
    ttl = PRE_SESSION_SECONDS if kind == "pre" else SESSION_ABSOLUTE_SECONDS
    if ttl_seconds is not None:
        ttl = min(ttl, ttl_seconds)
    s.add(AuthSession(
        id_hash=_session_digest(sid),
        kind=kind,
        user_id=user_id,
        csrf_hash=_csrf_digest(_csrf_for(sid)),
        created_at=now,
        last_seen_at=now,
        absolute_expires_at=now + timedelta(seconds=ttl),
        ip=_clean(_client_ip(request), 64),
        user_agent=_clean(_user_agent(request), 256),
        auth_method=auth_method if kind == "full" else None,
    ))
    return sid


#: Environment gate for `mint_browser_session`. The command that calls it is
#: refused outright unless this is set, and it is set ONLY in
#: deploy/staging.override.yml. That is the whole point: a standing,
#: password-free way into a browser session is a fine thing to have on the box
#: where things are meant to break, and a liability on the one behind a public
#: tunnel. Config, not documentation, is what keeps it off live.
SESSION_MINT_ENV = "ATLAS_ALLOW_SESSION_MINT"

#: Short by default. A minted session exists to run one verification pass, not
#: to be a login.
SESSION_MINT_DEFAULT_MINUTES = 30
SESSION_MINT_MAX_MINUTES = 480


def session_mint_enabled() -> bool:
    # Read per call from os.environ, by the rule in settings.py (TASK-1433).
    return settings.load_session_mint()


def mint_browser_session(
    s: Session,
    *,
    user: AuthUser,
    minutes: int = SESSION_MINT_DEFAULT_MINUTES,
    actor: Optional[str] = None,
    allow_owner: bool = False,
) -> tuple[str, datetime]:
    """Create a real browser session for `user` without a password. Returns
    `(raw sid, absolute expiry)`; the raw sid is returned ONCE and never stored.

    WHY THIS EXISTS. Verifying this board means driving it in a browser
    (AGENTS.md "What done means"), and every route is gated. The only way in was
    a password typed into the login form — which an agent must not do, and which
    means a human has to sit through every check. This mints the session
    directly instead: the row is identical to one `_mint_session` creates at
    login, so `/api/me` hands back a real CSRF token and every state-changing
    POST goes through the real synchronizer-token path. No password is created,
    typed, stored or transmitted anywhere in that flow.

    It grants NOTHING that `user reset-password` does not already grant to
    whoever can run it — both require operator access to the container. What it
    avoids is a standing password for a shared test account, which is the thing
    that actually leaks.

    Refusals, each for a distinct reason:
      * not enabled for this deployment — see SESSION_MINT_ENV;
      * `is_service` — a bot has no session branch at all (`_resolve_session`
        deletes the row and returns None), so the sid would be dead on arrival;
      * disabled — a disabled account must not be reachable by any path;
      * `must_change_password` — the gate would bounce every request to the
        forced-change screen, so the session would look broken rather than
        refused;
      * `owner` without `allow_owner` — owner sees the private set
        (STORY-atlas-node-visibility). Testing as one has to be deliberate.
    """
    if not session_mint_enabled():
        raise ValueError(
            "session minting is not enabled on this deployment. It is set only in "
            "deploy/staging.override.yml (%s=1) — deliberately never on live, which "
            "is publicly reachable through the tunnel." % SESSION_MINT_ENV
        )
    if user.is_service:
        raise ValueError(
            "%r is a service principal; it has no session branch. Use a Bearer "
            "service token (`token add`) instead." % user.subject
        )
    if user.disabled_at is not None:
        raise ValueError("%r is disabled" % user.subject)
    if user.must_change_password:
        raise ValueError(
            "%r must change its password, so the gate would redirect every request "
            "to the change screen. Create the test account with `user add "
            "--no-password`, which has no credential to change." % user.subject
        )
    if resolve_role(user.role) == models_auth.ROLE_OWNER and not allow_owner:
        raise ValueError(
            "%r is an owner, and an owner session sees the private set. Pass "
            "--allow-owner if that is genuinely what you are testing." % user.subject
        )
    minutes = max(1, min(int(minutes), SESSION_MINT_MAX_MINUTES))

    sid = secrets.token_urlsafe(32)   # 256 bits, same as the login path
    now = _now()
    expires = now + timedelta(minutes=minutes)
    s.add(AuthSession(
        id_hash=_session_digest(sid),
        kind="full",
        user_id=user.id,
        csrf_hash=_csrf_digest(_csrf_for(sid)),
        created_at=now,
        last_seen_at=now,
        absolute_expires_at=expires,
        ip=None,
        user_agent="minted:session-mint",
        auth_method=identity.AUTH_MINTED,
    ))
    _event(s, "session_minted", user_id=user.id, actor=actor,
           reason="minutes=%d" % minutes,
           detail="password-free browser session for verification")
    return sid, expires


def _prune_sessions(s: Session) -> None:
    """Opportunistic, bounded, and safe to skip. Called from GET /auth/login."""
    try:
        s.execute(delete(AuthSession).where(AuthSession.absolute_expires_at <= _now()))
        s.commit()
    except Exception:  # noqa: BLE001 - housekeeping must never fail a request
        s.rollback()
        logger.warning("session prune failed; continuing")


# --------------------------------------------------------------------------- #
# 12. Server-rendered pages.
#
# THESE ARE THE REAL PAGES, not placeholders. The login form, the forced
# password change, `/auth/denied` and `/auth/privacy` are all rendered by the
# `_PAGE` string template below — there is no template engine and no HTML file
# behind any of them.
#
# `frontend/login.html`, `frontend/logout.html` and `frontend/change-password.html`
# (and the v0 tree view, `frontend/index.html`) were deleted on 2026-11-10 by
# owner decision; git history has them. Nothing had routed to them since the
# `/static` mount was deleted (TASK-967, `verify_object_graph.py` asserts it
# stays deleted), they were never in `ASSET_FILES`, and TASK-969, which once
# owned the login screens, closed 2026-07-31 without wiring them.
#
# Everything here is deliberately self-contained: no external stylesheet, no
# script, no vendored asset — which is also why `/assets/*` does not need to be
# public.
# --------------------------------------------------------------------------- #

# `body.doc` is the DOCUMENT variant of this shell (TASK-941), and its rationale
# lives here rather than in the stylesheet: everything inside `_PAGE` is bytes
# sent to every browser that loads the login page, and maintainer prose is not
# something a visitor should have to download.
#
# It keeps the 26rem card (owner decision, 2026-08-16) and solves length by COLLAPSING
# rather than by widening: every section is a `<details>` that starts shut, so
# the page is short until somebody asks it a question. Native `<details>` and
# not a script, because this shell deliberately carries no JavaScript — which is
# also why `/assets/*` need not be public.
#
# The one thing the class MUST change is the centring. A taller-than-viewport
# `main` under `align-items:center` is the classic flexbox clip, and every
# section expanded is taller than a phone. Scoped to a class, so every form page
# renders byte-identically to before.
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s &middot; Atlas</title>
<style>
 :root{color-scheme:light dark}
 body{font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;margin:0;
      display:flex;min-height:100vh;align-items:center;justify-content:center;
      background:#0f1115;color:#e8eaed}
 main{width:min(26rem,92vw);padding:2rem;border:1px solid #2a2f3a;border-radius:12px;
      background:#161a22}
 /* body.doc — the collapsible document pages. See _PAGE's comment above. */
 body.doc{align-items:flex-start;padding:3rem 1rem}
 body.doc details{border-top:1px solid #2a2f3a;padding:.6rem 0}
 body.doc details[open]{padding-bottom:1rem}
 body.doc summary{cursor:pointer;font-weight:600;font-size:.9rem;list-style:none}
 body.doc summary::-webkit-details-marker{display:none}
 body.doc summary::before{content:"▸";display:inline-block;width:1.2em;color:#9aa3b2}
 body.doc details[open] summary::before{content:"▾"}
 body.doc summary:hover{color:#fff}
 body.doc details p{font-size:.9rem}
 body.doc details ul{margin:.5rem 0 0;padding-left:1.25rem;font-size:.9rem}
 body.doc li{margin:.35rem 0}
 h1{font-size:1.25rem;margin:0 0 .25rem}
 p.sub{margin:0 0 1.25rem;color:#9aa3b2;font-size:.875rem}
 label{display:block;margin:.75rem 0 .25rem;font-size:.8125rem;color:#9aa3b2}
 input{width:100%%;box-sizing:border-box;padding:.55rem .65rem;border-radius:8px;
       border:1px solid #2a2f3a;background:#0f1115;color:#e8eaed;font:inherit}
 button{margin-top:1.25rem;width:100%%;padding:.6rem;border:0;border-radius:8px;
        background:#3b82f6;color:#fff;font:inherit;font-weight:600;cursor:pointer}
 .err{margin:0 0 1rem;padding:.6rem .75rem;border-radius:8px;background:#3b1d1d;
      border:1px solid #7f1d1d;color:#fecaca;font-size:.875rem}
 .note{margin-top:1rem;color:#9aa3b2;font-size:.75rem}
</style></head><body%(bodyclass)s><main>
<h1>%(title)s</h1><p class="sub">%(subtitle)s</p>
%(error)s%(body)s
</main></body></html>
"""


def _page(title: str, subtitle: str, body: str, error: str = "",
           wide: bool = False) -> str:
    return _PAGE % {
        "title": html.escape(title),
        "subtitle": html.escape(subtitle),
        "error": ('<p class="err">%s</p>' % html.escape(error)) if error else "",
        "body": body,
        "bodyclass": ' class="doc"' if wide else "",
    }


def _sso_controls(next_path: str) -> str:
    """"Sign in with SSO", one link per configured redirect provider (STORY-oss-13).
    Empty when none is configured, which is the default and leaves the page as it was.
    A plain link, not a script: this shell carries no JavaScript."""
    links = []
    for p in identity.redirect_providers():
        href = identity.start_path(p.name)
        if next_path and next_path != "/":
            href += "?next=" + quote(next_path, safe="")
        links.append('<p><a class="sso" href="%s">Sign in with SSO</a></p>' % html.escape(href))
    return "".join(links)


def _login_page(csrf: str, next_path: str, error: str = "", break_glass: bool = False) -> str:
    """The sign-in page. With the defaults it is exactly what it always was.

    With a redirect provider it also carries "Sign in with SSO". The password form
    is left off when ATLAS_LOCAL_LOGIN=break-glass (or `local` is not a provider):
    the break-glass form is then reachable only at the UNLINKED
    `/auth/login?break_glass=1` (ADR 0041 §7), where `break_glass` is True.
    """
    sso = _sso_controls(next_path)
    if not sso and not break_glass:
        return _login_page_today(csrf, next_path, error)
    show_form = break_glass or identity.password_form_offered()
    page = _login_page_today(csrf, next_path, error)
    if show_form and not sso:
        return page
    start = page.index('<form method="post"')
    end = page.index("</form>") + len("</form>")
    form = page[start:end] if show_form else ""
    return page[:start] + sso + form + page[end:]


def _login_page_today(csrf: str, next_path: str, error: str = "") -> str:
    body = (
        '<form method="post" action="/auth/login">'
        '<input type="hidden" name="csrf_token" value="%s">'
        '<input type="hidden" name="next" value="%s">'
        '<label for="username">Username</label>'
        '<input id="username" name="username" autocomplete="username" autocapitalize="none" '
        'autocorrect="off" spellcheck="false" required>'
        '<label for="password">Password</label>'
        '<input id="password" name="password" type="password" '
        'autocomplete="current-password" required>'
        '<button type="submit">Sign in</button>'
        '</form>'
        '<p class="note">Accounts are created by the operator. There is no sign-up, '
        'and no password-reset link &mdash; ask your instance administrator.</p>'
        # BEFORE the credential is entered, not after. A privacy notice reachable
        # only once you have signed in is a notice you cannot read while deciding
        # whether to sign in (TASK-941).
        '<p class="note"><a href="/auth/privacy">How your data is handled</a></p>'
    ) % (html.escape(csrf), html.escape(next_path))
    return _page("Sign in", "Project Atlas", body, error)


def _privacy_page() -> str:
    """The privacy notice (TASK-941). PUBLIC, and that is the whole point.

    "Public" here means NO credential of any kind: no session cookie, no Bearer
    token, no account. `/auth/privacy` is in `PUBLIC_ROUTES`, so the
    deny-by-default gate does not refuse it, and it is therefore readable
    anonymously over the public tunnel. That is a requirement rather than an
    oversight — a notice you can only read after signing in is not available to
    the person deciding whether to sign in — and it is why the contact address
    below is world-readable and had to be chosen deliberately.

    GDPR attaches to whoever is the controller at the time, not to whoever it
    will be later, so this does not wait for the AB: invited people's usernames,
    names and emails are personal data held by a natural person today.

    EVERY CLAIM HERE IS CHECKED AGAINST THE SCHEMA, and where the honest answer
    is unflattering it says the unflattering thing. Atlas keeps an append-only
    `auth_event` log it cannot prune, disables accounts rather than deleting
    them so an id is never reissued, and has no self-service erase — so the
    notice says all three rather than implying a delete button exists. A notice
    that describes a system nicer than the one running is worth less than none.

    THE CHANGE RECORD (TASK-1459). Until 2026-10-31 this page said Atlas records
    no author on content. It does: `deps.record_graph_event` writes the actor
    (account, credential kind, token id, username copy) and the accountable
    person on every graph change, nothing prunes `graph_event`
    (`routers/health.py`), `GET /api/events` serves it to anyone who can see the
    node, and comments carry their author. The sections below say so, and
    `verify_privacy_page.py` pins it.

    KEPT ON PURPOSE (owner decision, 2026-10-31). The page used to end the erasure text
    with an offer to replace the id and username copied into the change record
    and the security log. That offer is gone: Atlas records who made every
    change and who wrote every comment so that what people and agents did can
    be traced and reviewed, and an erased account does not rewrite it. No code
    path rewrites those copies, so the page no longer promises one. What an
    erasure does is the operator clearing `auth_user.name` and `.email` by hand
    (no admin command does it) and `admin user disable`, which stamps
    `disabled_at`, deletes the sessions and disables the linked agents
    (`admin.cmd_user_disable`); `_resolve_token` then refuses their tokens.

    The read-audit paragraph is rendered from
    `audit_log.READS_ENABLED` and the retention setting, so it describes the
    install it is served by rather than a default.

    COLLAPSED BY DEFAULT (owner decision, 2026-08-16). What somebody needs before they
    sign in is one line — who holds it and how to reach them — and that stays
    open. The detail is there for the reader who wants it, one click away,
    rather than as a wall nobody finishes.
    """
    mail = html.escape(PRIVACY_CONTACT)

    def section(summary: str, inner: str) -> str:
        return "<details><summary>%s</summary>%s</details>" % (summary, inner)

    body = (
        '<p>Atlas is a private project board for invited people. This page says '
        'what it holds about you, what for, and how to have it removed.</p>'
        '<p><strong>%(controller)s</strong> holds it.%(note)s</p>'
        '<p>Contact for anything on this page: '
        '<a href="mailto:%(mail)s">%(mail)s</a>.</p>'
    ) % {"controller": html.escape(PRIVACY_CONTROLLER), "mail": mail,
         "note": (" " + PRIVACY_COMPANY_NOTE) if PRIVACY_COMPANY_NOTE else ""}

    body += section("What Atlas holds about you",
        '<ul>'
        '<li>Your username, and your display name and email address if the operator '
        'recorded them when creating your account.</li>'
        '<li>A verifier for your password &mdash; a peppered argon2id hash. Never the '
        'password itself.</li>'
        '<li>When you last signed in, and when you last changed your password.</li>'
        '<li>A security log of sign-ins, failed sign-ins, password changes and account '
        'changes, each with the IP address and browser the request came from.</li>'
        '<li>Your active sessions, with the same IP address and browser details.</li>'
        '<li>If you sign in through an organisation&rsquo;s identity provider instead of '
        'a password: the identifier it gives you, and the name, email address and groups '
        'it sends.</li>'
        '<li>The agent credentials you create: a label, what they may do, and when they '
        'were created and last used. Only a hash of each credential is kept.</li>'
        '<li>If you link a GitHub account: its id and login, and whether you were invited '
        'to the code repository. No GitHub token is kept.</li>'
        '<li>Which private projects you have been given access to, and the items assigned '
        'to you.</li>'
        '<li>Who changed what on the board, and who wrote each comment &mdash; see '
        '<em>The change record</em> below.</li>'
        '</ul>')

    # The read audit is off by default and switched on by the operator; say which
    # this install is, rather than describing a default it may not run.
    import audit_log  # local: audit_log imports this module
    if audit_log.READS_ENABLED:
        reads_state = 'It is <strong>switched on</strong> here.'
    else:
        reads_state = 'It is switched off here.'
    retention_days = settings.load_audit().retention_days
    if retention_days is None:
        reads_kept = ('Read-audit entries are kept until the operator deletes them; '
                      'no retention period is set.')
    else:
        reads_kept = ('Read-audit entries older than %d days are deleted when the operator '
                      'runs the clean-up.' % retention_days)

    body += section("The change record",
        '<p>Every change to the board is written to a change record: creating, editing '
        'or deleting an item, a link between items, a document, an intent or a comment, '
        'and every change of status or assignee. Each entry says what changed and when, '
        'and who did it &mdash; the account (its id, and its username at the time), '
        'whether it was a browser session or an agent credential and which credential, '
        'and, for an agent, the person it acts for. Some entries also carry the old and '
        'new value, such as a status, an assignee, an item&rsquo;s name or its acceptance '
        'criteria. It is not a full copy of everything you wrote.</p>'
        '<p>This record is kept on purpose. Atlas records who made every change and '
        'who wrote every comment, so that what people and agents did on the board can '
        'be traced and reviewed afterwards. Closing or erasing an account does not '
        'rewrite it.</p>'
        '<p>Comments carry their author. An edited comment keeps its earlier text, and a '
        'deleted comment is hidden rather than removed, with who deleted it and when.</p>'
        '<p>Anyone who can see an item can see its change record and its comments, with '
        'the names of the people and agents involved: on the board&rsquo;s History tab, '
        'and through the API. The operator&rsquo;s owner accounts can see all of it, '
        'including entries about items since deleted, and can export it together with '
        'the security log.</p>'
        '<p>There is also an optional read audit: when it is on, each time an agent '
        'credential reads items through the API, Atlas records which agent, on whose '
        'behalf, which items it was given and which tool asked &mdash; never the content '
        'or the words searched for. Only owner accounts can read it, through the export. '
        '%s</p>' % reads_state)

    body += section("What it does not hold",
        '<p>There are no analytics, no third-party trackers and no advertising: the '
        'pages load nothing from another site. Nothing here is shared with anyone '
        'outside the people who have accounts.</p>')

    body += section("What it is for",
        '<p>To give named people access to a private project board, and to make it '
        'possible to tell who signed in, and to trace and review who changed what. '
        'Nothing else. None of it is used to profile you or to make automated decisions '
        'about you.</p>')

    body += section("How long it is kept",
        '<p>Your account details for as long as the account exists. The security log '
        'is <strong>append-only and kept indefinitely</strong> &mdash; it is the record '
        'of who had access and when, and it is not pruned.</p>'
        '<p>The change record is kept for as long as this installation keeps its '
        'database: nothing prunes it, and an entry outlives the item it describes. '
        'Comments, with their earlier text, are deleted when the item they belong to '
        'is deleted. %s</p>' % reads_kept)

    body += section("Getting a copy, a correction, or deletion",
        '<p>Ask, and it is done by hand: there is no self-service export or delete '
        'button, and pretending otherwise would be the easy lie here.</p>'
        '<p><a href="mailto:%(mail)s?subject=Atlas%%20%%E2%%80%%94%%20my%%20personal%%20data">'
        'Email a request</a> &mdash; a copy, a correction, or deletion.</p>'
        '<p>On request the operator clears the display name and email address on your '
        'account and closes it. Closing it ends your sessions, and the agent '
        'credentials you created and the agents linked to you stop working. Accounts '
        'are <em>disabled</em> rather than removed, so that an account id can never be '
        'reissued to somebody else.</p>'
        '<p>Erasing an account does not rewrite history. The security log, the change '
        'record and the comments keep the account id, and the username as it was when '
        'each entry was written. That is by design: it is how what people and agents '
        'did stays traceable (see <em>The change record</em>), and it is not changed on '
        'request.</p>'
        '<p>You can also object to any of this, and the honest remedy at this scale '
        'is that your account is closed and you stop using the board.</p>'
        % {"mail": mail})

    body += section("Where it lives",
        '<p>%s</p>' % html.escape(PRIVACY_HOSTING))

    body += '<p class="note"><a href="/auth/login">Back to sign in</a></p>'
    return _page("Privacy", "How Atlas handles your personal data", body, wide=True)


def _change_page(csrf: str, error: str = "", forced: bool = False) -> str:
    subtitle = (
        "Set your own password before continuing." if forced
        else "Change your Atlas password."
    )
    body = (
        '<form method="post" action="/auth/password/change">'
        '<input type="hidden" name="csrf_token" value="%s">'
        '<label for="current_password">Current password</label>'
        '<input id="current_password" name="current_password" type="password" '
        'autocomplete="current-password" required>'
        '<label for="new_password">New password</label>'
        '<input id="new_password" name="new_password" type="password" '
        'autocomplete="new-password" required>'
        '<label for="confirm_password">Repeat new password</label>'
        '<input id="confirm_password" name="confirm_password" type="password" '
        'autocomplete="new-password" required>'
        '<button type="submit">Set password</button>'
        '</form>'
        '<p class="note">%s</p>'
    ) % (html.escape(csrf), html.escape(passwords.POLICY_SUMMARY))
    return _page("New password", subtitle, body, error)


# --------------------------------------------------------------------------- #
# 13. Routes
# --------------------------------------------------------------------------- #

def _principal_of(request: Request) -> Optional[Principal]:
    return getattr(request.state, "principal", None) or current_principal.get()


def _require_session_principal(request: Request) -> Optional[Response]:
    """`/auth/password/change` is exempt at the GATE but still needs a principal.

    A service token may never change a password: bot rows carry none (TASK-966
    puts a CHECK on it), so this is refused rather than left to fail obscurely.
    """
    principal = _principal_of(request)
    if principal is None:
        if _wants_html_redirect(request):
            return _login_redirect(request)
        return _unauthorized(presented=bool(request.headers.get("authorization")))
    if principal.kind != "session":
        return _json(403, {
            "error": "forbidden",
            "detail": "A service token cannot change a password.",
        })
    return None


def install(app: FastAPI, engine: Engine, frontend_dir: str = "") -> None:
    """Wire the gate and the auth routes onto `app`.

    Called from `app.py` immediately after the `FastAPI(...)` constructor, so
    the middleware wraps every route — including the ones registered after it.
    """
    global _ENGINE, _FRONTEND_DIR
    _ENGINE = engine
    _FRONTEND_DIR = frontend_dir

    app.add_middleware(AuthGate)

    if not BEARER_HOSTS:
        logger.warning(
            "%s is not set: Authorization: Bearer is accepted on every allowed host, "
            "including the public one. Set it to the LAN host only unless a third "
            "party genuinely needs Bearer over the tunnel (TASK-968).",
            BEARER_HOSTS_ENV,
        )
    if INTERNAL_HOSTS:
        logger.info(
            "%s=%s: Bearer requests (and nothing else) are admitted on those hosts.",
            settings.INTERNAL_HOSTS_ENV, ",".join(sorted(INTERNAL_HOSTS)),
        )
    if MACHINE_GRACE_HOSTS:
        logger.warning(
            "%s=%s — UNAUTHENTICATED non-browser calls are being ALLOWED on those "
            "hosts and logged as WARN. This is TASK-968 step 1 only; unset it once "
            "the WARN count has been zero for 24 h.",
            MACHINE_GRACE_ENV, ",".join(sorted(MACHINE_GRACE_HOSTS)),
        )

    # ---- liveness / readiness ------------------------------------------- #
    # These exist precisely so that NO piece of infrastructure ever needs
    # `/api/health`. Both are contentless: no version, no config, no ingest
    # verdict, nothing an unauthenticated caller can learn from.

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """Liveness. Makes NO database call by design."""
        return _json(200, {"ok": True})

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        """Readiness. Touches the DB and says nothing else.

        Deliberately does NOT prove the ingest ran — a 200 beside stale Postgres
        rows is this repo's documented failure. That signal is
        `last_successful_ingest_at` on the AUTHENTICATED `/api/health`.
        """
        try:
            with _session() as s:
                s.execute(select(1))
        except Exception:  # noqa: BLE001
            logger.exception("/readyz database probe failed")
            return _json(503, {"ok": False, "db": False})
        return _json(200, {"ok": True, "db": True})

    @app.get("/auth/privacy")
    def privacy() -> HTMLResponse:
        """The privacy notice. Public by design — see `_privacy_page`."""
        return HTMLResponse(_privacy_page())

    @app.get("/auth/denied")
    def denied() -> HTMLResponse:
        """Refusal / disabled page. TASK-970 fills this in."""
        return HTMLResponse(_page(
            "No access",
            "Project Atlas",
            '<p class="note">This account cannot sign in. If that is unexpected, '
            'ask the operator.</p>',
        ), status_code=403)

    # ---- login ----------------------------------------------------------- #

    @app.get("/auth/login")
    def login_form(request: Request) -> HTMLResponse:
        """Mint a `kind='pre'` session, set `__Host-atlas_pre`, embed the token."""
        next_path = _safe_next(request.query_params.get("next"))
        error = passwords.GENERIC_LOGIN_FAILURE_MESSAGE if request.query_params.get("error") else ""
        with _session() as s:
            _prune_sessions(s)
            # Reuse a pre-session the caller already holds rather than minting a
            # second one. This route is unauthenticated and internet-facing, so
            # "one DB row per GET" would be a write amplifier for anyone with a
            # loop; the 15-minute TTL plus the prune above bounds what a
            # cookie-less crawler can accumulate.
            sid = request.cookies.get(PRE_COOKIE)
            row = s.get(AuthSession, _session_digest(sid)) if sid else None
            live = (
                row is not None
                and row.kind == "pre"
                and (_aware(row.absolute_expires_at) or _now()) > _now()
            )
            if not live:
                sid = _mint_session(s, kind="pre", user_id=None, request=request, auth_method=None)
                s.commit()
        # `?break_glass=1` shows the password form when the page would otherwise
        # leave it off (ADR 0041 §7). Read only then: by default the page is today's.
        break_glass = (not identity.password_form_offered()
                       and request.query_params.get("break_glass") == "1")
        response = HTMLResponse(_login_page(_csrf_for(sid), next_path, error,
                                            break_glass=break_glass))
        _set_pre_cookie(response, sid)
        return response

    @app.post("/auth/login")
    async def login(request: Request) -> Response:
        form = await _read_form(request)
        return await run_in_threadpool(
            _do_login, request,
            form.get("username", ""),
            form.get("password", ""),
            form.get("csrf_token", ""),
            _safe_next(form.get("next", "/")),
        )

    # ---- single sign-on (STORY-oss-13, ADR 0041 §5) ---------------------- #
    # One start/callback pair per configured REDIRECT provider, registered only
    # then: with the default ATLAS_AUTH_PROVIDERS=local there is no such route
    # and the route table is `routes.snapshot.txt`, unchanged.
    for _provider in identity.redirect_providers():
        _register_sso_routes(app, _provider)

    # ---- forced / voluntary password change ------------------------------ #

    @app.get("/auth/password/change")
    def change_form(request: Request) -> Response:
        refusal = _require_session_principal(request)
        if refusal is not None:
            return refusal
        principal = _principal_of(request)
        sid = request.cookies.get(SESSION_COOKIE) or ""
        return HTMLResponse(_change_page(
            _csrf_for(sid), forced=bool(principal and principal.must_change_password)
        ))

    @app.post("/auth/password/change")
    async def change_password(request: Request) -> Response:
        refusal = _require_session_principal(request)
        if refusal is not None:
            return refusal
        form = await _read_form(request)
        return await run_in_threadpool(
            _do_change_password, request,
            form.get("current_password", ""),
            form.get("new_password", ""),
            form.get("confirm_password", ""),
            form.get("csrf_token", ""),
        )

    # ---- logout ---------------------------------------------------------- #

    @app.post("/auth/logout")
    async def logout(request: Request) -> Response:
        form = await _read_form(request)
        return await run_in_threadpool(
            _do_logout, request, form.get("csrf_token", "")
        )

    # ---- who am I -------------------------------------------------------- #

    @app.get("/api/me")
    def me(request: Request) -> JSONResponse:
        """The signed-in principal. Authenticated like everything else.

        Carries `csrf_token` so the board (TASK-969) can render a logout form
        without the raw session id ever reaching JavaScript.
        """
        principal = _principal_of(request)
        if principal is None:  # pragma: no cover - the gate refuses first
            return _unauthorized(presented=False)
        body = {
            "kind": principal.kind,
            "user_id": principal.user_id,
            "username": principal.subject,
            "name": principal.label,
            "role": principal.role,
            "is_service": principal.is_service,
            "must_change_password": principal.must_change_password,
            "scopes": list(principal.scopes),
        }
        if principal.kind == "session":
            body["csrf_token"] = _csrf_for(request.cookies.get(SESSION_COOKIE) or "")
            if identity.SETTINGS.oidc_enabled and principal.user_id:
                # TASK-1442: an SSO person with no personal password yet. The key is
                # present only then, so every other body is today's, byte for byte.
                with _session() as s:
                    person = s.get(AuthUser, principal.user_id)
                    # Not in a session the gate holds: a linked account that signed
                    # in with its temporary password changes it at
                    # /auth/password/change, as anyone does (TASK-1454).
                    if (person is not None and password_pending(person)
                            and not principal.must_change_password):
                        body["password_setup"] = password_setup_status(s, principal, person)
        return _json(200, body)


# --------------------------------------------------------------------------- #
# 14. The synchronous handlers, run off the event loop
# --------------------------------------------------------------------------- #

def _login_failure(next_path: str) -> RedirectResponse:
    """ONE response for every failure reason.

    POST-redirect-GET, so the body is byte-identical for an unknown username, a
    wrong password, a disabled account and an expired temp credential — the
    story's `diff <(curl …) <(curl …)` criterion. The re-rendered login page
    mints a fresh pre-session and a fresh token, so a failed attempt does not
    leave a reusable one behind.
    """
    location = "/auth/login?error=1"
    if next_path and next_path != "/":
        location += "&next=" + quote(next_path, safe="")
    return RedirectResponse(location, status_code=303)


def _do_login(request: Request, username: str, password: str, csrf_token: str,
              next_path: str) -> Response:
    ip = _client_ip(request)
    ua = _user_agent(request)
    pre_sid = request.cookies.get(PRE_COOKIE)

    with _session() as s:
        # -- 1. CSRF: Origin/Referer, then the synchronizer token. -----------
        try:
            _check_origin(request)
            pre_row = s.get(AuthSession, _session_digest(pre_sid)) if pre_sid else None
            if pre_row is not None and pre_row.kind != "pre":
                pre_row = None
            if pre_row is not None and (_aware(pre_row.absolute_expires_at) or _now()) <= _now():
                s.delete(pre_row)
                s.commit()
                pre_row = None
            _check_csrf(pre_sid, pre_row, csrf_token)
        except CsrfFailure as exc:
            _event(s, "bad_origin" if exc.reason.startswith(("bad_", "no_origin")) else "csrf_reject",
                   ip=ip, user_agent=ua, reason=exc.reason)
            s.commit()
            # 403 and NO cookie is set — a login CSRF attempt gets nothing.
            return _json(403, {"error": "csrf_failed",
                               "detail": "This form is stale or came from another site. Reload the login page."})

        # -- 2. Rate limits BEFORE the user row is touched (TASK-965). -------
        ip_decision = _IP_LIMITER.check(_login_key(ip))
        if not ip_decision.allowed:
            _event(s, "throttle_engaged", ip=ip, user_agent=ua, reason="ip_ceiling")
            s.commit()
            return _json(passwords.HTTP_TOO_MANY_REQUESTS,
                         {"error": "rate_limited", "detail": passwords.GENERIC_LOGIN_FAILURE_MESSAGE},
                         passwords.retry_after_headers(ip_decision.retry_after_seconds))

        handle = passwords.normalise_username(username)
        key = passwords.throttle_key(username)
        throttle_row = s.get(AuthLoginThrottle, key)
        state = None
        if throttle_row is not None:
            state = passwords.ThrottleState(
                failed_count=throttle_row.failed_count,
                first_failed_at=_aware(throttle_row.first_failed_at),
                last_failed_at=_aware(throttle_row.last_failed_at),
                retry_after=_aware(throttle_row.retry_after),
            )
        decision = passwords.evaluate_throttle(state)
        if not decision.allowed:
            _event(s, "throttle_engaged", ip=ip, user_agent=ua, reason="account_backoff")
            s.commit()
            return _json(passwords.HTTP_TOO_MANY_REQUESTS,
                         {"error": "rate_limited", "detail": passwords.GENERIC_LOGIN_FAILURE_MESSAGE},
                         passwords.retry_after_headers(decision.retry_after_seconds))

        # -- 3. Look the account up. -----------------------------------------
        user = s.scalar(select(AuthUser).where(
            AuthUser.idp == "local", AuthUser.subject == handle
        ))
        # TASK-1454: on a miss, a LINKED account's local sign-in name, so its
        # personal password is a fallback for when the IdP is down. Only with
        # `local` and `oidc` both on (identity.linked_password_login_enabled);
        # otherwise nothing new is read. A local account always wins its own
        # name: this runs only when no `idp='local'` row matched. Everything
        # below (break-glass, the dummy verify, the temp expiry, the forced
        # change) applies to it unchanged.
        by_local_name = False
        if user is None and identity.linked_password_login_enabled():
            user = identity.account_by_local_name(s, handle)
            by_local_name = user is not None

        # Miss / bot row / disabled -> run the dummy verify ANYWAY, then fail
        # identically. The user gets no diagnostic; auth_event gets the reason.
        reason = None
        if user is None:
            reason = "unknown_user"
        elif user.is_service:
            reason = "service_principal"
        elif user.disabled_at is not None:
            reason = "disabled"
        elif not identity.password_login_allowed(s, user):
            # ATLAS_LOCAL_LOGIN=break-glass (ADR 0041 §7): a password signs in only
            # an account flagged break-glass. Same dummy verify and same generic
            # answer as every other refusal. Under the default `all` this reads
            # nothing and is never true.
            reason = "not_break_glass"

        if reason is not None:
            passwords.verify_password(None, password)  # dummy, real cost
            return _fail_login(s, key, throttle_row, user, ip, ua, reason, next_path)

        result = passwords.verify_password(user.password_hash, password)
        if not result.ok:
            return _fail_login(s, key, throttle_row, user, ip, ua,
                               "bad_password:%s" % result.reason, next_path)

        # An expired temp credential is not a distinguishable state.
        temp_expires = _aware(user.temp_expires_at)
        if user.must_change_password and temp_expires is not None and temp_expires <= _now():
            return _fail_login(s, key, throttle_row, user, ip, ua, "temp_expired", next_path)

        # -- 4. Success. check_needs_rehash is the ONLY parameter-migration
        #       path there is without Alembic, and it costs three lines. -----
        if result.needs_rehash and result.new_hash:
            user.password_hash = result.new_hash
            user.hash_scheme = passwords.HASH_SCHEME

        # -- 5. ROTATE: destroy the pre-session, mint a fresh full one. ------
        s.delete(pre_row)
        sid = _mint_session(s, kind="full", user_id=user.id, request=request,
                            auth_method=identity.AUTH_PASSWORD)
        user.last_login_at = _now()
        if throttle_row is not None:
            s.delete(throttle_row)
        _IP_LIMITER.reset(_login_key(ip))
        # A linked account's password sign-in says so (TASK-1454); a local
        # account's row is today's, byte for byte.
        _event(s, "login_ok", user_id=user.id, ip=ip, user_agent=ua,
               reason="must_change" if user.must_change_password else "ok",
               detail=("local_name=%s" % handle) if by_local_name else None)
        if identity.SETTINGS.break_glass_only:
            # Every break-glass use is its own, distinct audit row (ADR 0041 §7), on
            # top of `login_ok`, so anyone counting logins still counts this one.
            _event(s, "login_break_glass", user_id=user.id, ip=ip, user_agent=ua,
                   reason="password_while_break_glass_only")
        s.commit()

        target = "/auth/password/change" if user.must_change_password else (next_path or "/")

    response = RedirectResponse(target, status_code=303)
    _set_session_cookie(response, sid)
    _clear_cookie(response, PRE_COOKIE)
    return response


def _fail_login(s: Session, key: str, throttle_row: Optional[AuthLoginThrottle],
                user: Optional[AuthUser], ip: str, ua: str, reason: str,
                next_path: str) -> Response:
    """Fold the failure into the throttle row, audit it, answer generically."""
    state = None
    if throttle_row is not None:
        state = passwords.ThrottleState(
            failed_count=throttle_row.failed_count,
            first_failed_at=_aware(throttle_row.first_failed_at),
            last_failed_at=_aware(throttle_row.last_failed_at),
            retry_after=_aware(throttle_row.retry_after),
        )
    updated, decision = passwords.record_throttle_failure(state)
    if throttle_row is None:
        throttle_row = AuthLoginThrottle(key_hash=key)
        s.add(throttle_row)
    throttle_row.failed_count = updated.failed_count
    throttle_row.first_failed_at = updated.first_failed_at
    throttle_row.last_failed_at = updated.last_failed_at
    throttle_row.retry_after = updated.retry_after

    ip_decision = _IP_LIMITER.record_failure(_login_key(ip))
    _event(s, "login_fail", user_id=user.id if user else None, ip=ip, user_agent=ua,
           reason=reason, detail="failed_count=%d" % updated.failed_count)

    # NIST SP 800-63B §3.2.2 SHALL: a hard cap exists. Behind the 300 s backoff
    # it takes days to reach, so it is the compliance floor, not the mechanism.
    if decision.hard_disable and user is not None and user.disabled_at is None:
        user.disabled_at = _now()
        _event(s, "lockout_engaged", user_id=user.id, ip=ip, reason="hard_disable_threshold")
    s.commit()

    if not ip_decision.allowed:
        return _json(passwords.HTTP_TOO_MANY_REQUESTS,
                     {"error": "rate_limited", "detail": passwords.GENERIC_LOGIN_FAILURE_MESSAGE},
                     passwords.retry_after_headers(ip_decision.retry_after_seconds))
    return _login_failure(next_path)


def _do_change_password(request: Request, current: str, new: str, confirm: str,
                        csrf_token: str) -> Response:
    principal = _principal_of(request)
    if principal is None or principal.kind != "session":  # pragma: no cover
        return _unauthorized(presented=False)
    sid = request.cookies.get(SESSION_COOKIE) or ""
    ip = _client_ip(request)
    ua = _user_agent(request)

    with _session() as s:
        row = s.get(AuthSession, _session_digest(sid)) if sid else None
        try:
            _check_origin(request)
            _check_csrf(sid, row, csrf_token)
        except CsrfFailure as exc:
            _event(s, "bad_origin" if exc.reason.startswith(("bad_", "no_origin")) else "csrf_reject",
                   user_id=principal.user_id, ip=ip, user_agent=ua, reason=exc.reason)
            s.commit()
            return _json(403, {"error": "csrf_failed",
                               "detail": "This form is stale or came from another site. Reload the page."})

        user = s.get(AuthUser, principal.user_id)
        if user is None or user.disabled_at is not None:  # pragma: no cover
            return _json(403, {"error": "forbidden", "detail": passwords.GENERIC_LOGIN_FAILURE_MESSAGE})
        forced = bool(user.must_change_password)

        def refuse(message: str, status: int = 400) -> Response:
            return HTMLResponse(_change_page(_csrf_for(sid), error=message, forced=forced),
                                status_code=status)

        # ASVS 6.2.3: the CURRENT password is required in the same POST, forced
        # case included, where "current" is the temp secret. Without it a
        # hijacked pre-change session sets the password for the account.
        verified = passwords.verify_password(user.password_hash, current)
        if not verified.ok:
            _IP_LIMITER.record_failure(_login_key(ip))
            _event(s, "login_fail", user_id=user.id, ip=ip, user_agent=ua,
                   reason="change_current_password:%s" % verified.reason)
            s.commit()
            return refuse(passwords.GENERIC_LOGIN_FAILURE_MESSAGE, status=403)

        if new != confirm:
            return refuse("The two new passwords do not match.")

        # The context word is the name the person signs in with: a linked account's
        # local name (TASK-1454), not its opaque IdP subject.
        policy = passwords.check_password_policy(
            new, username=identity.local_name_of(s, user) or user.subject, breach_check=True)
        if not policy.ok:
            if policy.code == "breached":
                _event(s, "breach_list_reject", user_id=user.id, ip=ip, user_agent=ua,
                       reason=policy.code)
                s.commit()
            return refuse(policy.message)
        if policy.breach is not None and policy.breach.source == "unavailable":
            _event(s, "breach_list_unavailable", user_id=user.id, ip=ip, reason="corpus_unavailable")
            s.commit()

        # -- One transaction: verifier, flags, rotation, and every OTHER
        #    session for this user deleted (ASVS 7.4.3). ---------------------
        now = _now()
        user.password_hash = passwords.hash_password(new)
        user.hash_scheme = passwords.HASH_SCHEME
        user.must_change_password = False
        user.temp_expires_at = None
        user.password_changed_at = now
        # The new session continues THIS sign-in, so it keeps how that began (§5).
        method = session_auth_method(row, user) if row is not None else identity.AUTH_PASSWORD
        s.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
        new_sid = _mint_session(s, kind="full", user_id=user.id, request=request,
                                auth_method=method)
        _event(s, "password_changed", user_id=user.id, ip=ip, user_agent=ua,
               reason="forced" if forced else "voluntary")
        if forced:
            _event(s, "forced_change_completed", user_id=user.id, ip=ip, user_agent=ua)
        # Service tokens are DELIBERATELY left standing (TASK-968): a forgotten
        # browser password says nothing about an ingest credential, and silently
        # killing the sync token is the board-freezes-quietly failure wearing a
        # security hat. Disabling the user is the loud lever that kills both.
        s.commit()

    response = RedirectResponse("/", status_code=303)
    _set_session_cookie(response, new_sid)
    _clear_cookie(response, PRE_COOKIE)
    return response


def _do_logout(request: Request, csrf_token: str) -> Response:
    principal = _principal_of(request)
    sid = request.cookies.get(SESSION_COOKIE) or ""
    with _session() as s:
        row = s.get(AuthSession, _session_digest(sid)) if sid else None
        try:
            _check_origin(request)
            _check_csrf(sid, row, csrf_token)
        except CsrfFailure as exc:
            _event(s, "csrf_reject", user_id=principal.user_id if principal else None,
                   ip=_client_ip(request), reason=exc.reason)
            s.commit()
            return _json(403, {"error": "csrf_failed", "detail": "Reload the page and try again."})
        if row is not None:
            # DELETE THE ROW. A logout that only clears the cookie is not a
            # logout — the session id stays valid for anyone who copied it.
            s.delete(row)
        s.commit()
    response = RedirectResponse("/auth/login", status_code=303)
    _clear_cookie(response, SESSION_COOKIE)
    _clear_cookie(response, PRE_COOKIE)
    return response


# --------------------------------------------------------------------------- #
# 14b. Single sign-on through a redirect provider (STORY-oss-13, ADR 0041 §5)
#
# The same machinery as the password login, in the same order: the pre-session
# (`__Host-atlas_pre`) is the login-CSRF binding, the per-IP limiter counts
# failures, every outcome is an `auth_event`, success ROTATES (the pre-session is
# deleted and a full one minted) and the answer to every refusal is the same
# generic failure. What the provider adds is the proof (identity/oidc.py).
#
# A callback is a top-level GET navigation from the IdP, so the SameSite=Lax pre
# cookie arrives with it; a callback in a browser that did not start the flow
# carries a different (or no) pre-session and finds no flow (`state_other_browser`).
# --------------------------------------------------------------------------- #

def _register_sso_routes(app: FastAPI, provider) -> None:
    name = provider.name

    def sso_start(request: Request) -> Response:
        return _do_sso_start(request, provider)

    def sso_callback(request: Request) -> Response:
        return _do_sso_callback(request, provider)

    app.add_api_route(identity.start_path(name), sso_start, methods=["GET"],
                      name="sso_%s_start" % name, include_in_schema=False)
    app.add_api_route(identity.callback_path(name), sso_callback, methods=["GET"],
                      name="sso_%s_callback" % name, include_in_schema=False)

    if identity.step_up_provider() is provider:
        # ATLAS_OIDC_STEP_UP=on only (TASK-1415). Not a public route: the gate
        # resolves the session first, and `_do_step_up_start` refuses any other kind.
        def sso_step_up(request: Request) -> Response:
            return _do_step_up_start(request, provider)

        app.add_api_route(identity.step_up_path(name), sso_step_up, methods=["GET"],
                          name="sso_%s_step_up" % name, include_in_schema=False)


def _sso_unavailable() -> HTMLResponse:
    return HTMLResponse(_page(
        "Single sign-on unavailable", "Project Atlas",
        '<p class="note">The identity provider could not be reached. Try again in a '
        'moment, or ask the operator.</p><p class="note"><a href="/auth/login">Back to '
        'sign-in</a></p>'), status_code=503)


def _do_sso_start(request: Request, provider) -> Response:
    next_path = _safe_next(request.query_params.get("next"))
    ip = _client_ip(request)
    with _session() as s:
        decision = _IP_LIMITER.check(_login_key(ip))
        if not decision.allowed:
            _event(s, "throttle_engaged", ip=ip, user_agent=_user_agent(request),
                   reason="ip_ceiling:sso_start")
            s.commit()
            return _json(passwords.HTTP_TOO_MANY_REQUESTS,
                         {"error": "rate_limited", "detail": passwords.GENERIC_LOGIN_FAILURE_MESSAGE},
                         passwords.retry_after_headers(decision.retry_after_seconds))
        # The pre-session the login page would mint: reuse a live one, else mint.
        sid = request.cookies.get(PRE_COOKIE)
        row = s.get(AuthSession, _session_digest(sid)) if sid else None
        live = (row is not None and row.kind == "pre"
                and (_aware(row.absolute_expires_at) or _now()) > _now())
        if not live:
            sid = _mint_session(s, kind="pre", user_id=None, request=request, auth_method=None)
            s.flush()
        try:
            location = provider.begin(s, flow_binding=_session_digest(sid), next_path=next_path)
        except identity.LoginRefused as exc:
            s.rollback()
            _event(s, "login_fail", ip=ip, user_agent=_user_agent(request),
                   reason="%s:%s" % (provider.name, exc.reason))
            s.commit()
            logger.warning("single sign-on start refused: %s", exc.reason)
            return _sso_unavailable()
        s.commit()
    # 303 and an ABSOLUTE Location from configuration (the IdP's discovered
    # authorization endpoint), never derived from the request.
    response = RedirectResponse(location, status_code=303)
    _set_pre_cookie(response, sid)
    return response


def _do_sso_callback(request: Request, provider) -> Response:
    ip = _client_ip(request)
    ua = _user_agent(request)
    pre_sid = request.cookies.get(PRE_COOKIE)
    params = {k: request.query_params.get(k, "") for k in ("state", "code", "error")}
    if identity.step_up_provider() is provider and \
            params["state"].startswith(_step_up_state_prefix()):
        # ONE registered redirect URI serves both (TASK-1415): the IdP needs no
        # second one. The prefix only chooses which flow kind to look up; the
        # state's HMAC still has to match a stored step-up flow bound to THIS
        # session, so a forged prefix finds nothing.
        return _do_step_up_callback(request, provider, params)

    with _session() as s:
        decision = _IP_LIMITER.check(_login_key(ip))
        if not decision.allowed:
            _event(s, "throttle_engaged", ip=ip, user_agent=ua, reason="ip_ceiling:sso_callback")
            s.commit()
            return _json(passwords.HTTP_TOO_MANY_REQUESTS,
                         {"error": "rate_limited", "detail": passwords.GENERIC_LOGIN_FAILURE_MESSAGE},
                         passwords.retry_after_headers(decision.retry_after_seconds))

        pre_row = s.get(AuthSession, _session_digest(pre_sid)) if pre_sid else None
        if pre_row is not None and (pre_row.kind != "pre"
                                    or (_aware(pre_row.absolute_expires_at) or _now()) <= _now()):
            pre_row = None
        binding = pre_row.id_hash if pre_row is not None else None

        user_id = None
        try:
            # The provider consumes (and commits the deletion of) the flow first,
            # so nothing below can make a state usable twice.
            ident = provider.authenticate(s, params, flow_binding=binding)
            next_path = _safe_next(ident.next_path)
            if hasattr(provider, "check_login_allowed"):
                provider.check_login_allowed(ident)
            user = provider.lookup(s, ident)
            if user is None:
                user = provider.provision(s, ident)
            user_id = user.id
            if user.is_service:
                raise identity.LoginRefused("service_principal")
            if user.disabled_at is not None:
                raise identity.LoginRefused("disabled")
            provider.after_login(s, user, ident)
        except identity.LoginRefused as exc:
            # Drop whatever a half-finished login added (a JIT row, memberships,
            # the last-login stamp); the consumed flow stays consumed.
            s.rollback()
            _IP_LIMITER.record_failure(_login_key(ip))
            _event(s, "login_fail", user_id=user_id, ip=ip, user_agent=ua,
                   reason="%s:%s" % (provider.name, exc.reason))
            s.commit()
            return _login_failure("/")

        # Success: ROTATE exactly as the password login does.
        if pre_row is not None:
            s.delete(pre_row)
        sid = _mint_session(s, kind="full", user_id=user.id, request=request,
                            auth_method=identity.AUTH_SSO,
                            ttl_seconds=provider.session_seconds)
        user.last_login_at = _now()
        _IP_LIMITER.reset(_login_key(ip))
        _event(s, "login_ok", user_id=user.id, ip=ip, user_agent=ua,
               reason="provider=%s" % provider.name)
        s.commit()

    response = RedirectResponse(next_path or "/", status_code=303)
    _set_session_cookie(response, sid)
    _clear_cookie(response, PRE_COOKIE)
    return response


# --------------------------------------------------------------------------- #
# 14c. OIDC step-up for self-service agent credentials (TASK-1415)
#
# WHY. `POST /api/me/agent-credential` re-authenticates before it mints, because
# a long-lived Bearer credential minted from a cookie alone would let a stolen
# session outlive its own revocation (`reauthenticate`). An IdP-backed person has
# no Atlas password to re-enter, so on an SSO-only install nobody could mint
# their own MCP token. With ATLAS_OIDC_STEP_UP=on such a person re-authenticates
# where they are authenticated: at the IdP, again, now.
#
# HOW. `GET /auth/oidc/step-up?purpose=<p>` (or `?agent=<name>` to rotate; a
# signed-in SESSION only) names the agent credential being approved and answers
# a page saying so: "You are creating an agent credential named X for Y", with
# the link to the IdP. Behind it: the same code + PKCE flow as a login
# (identity/oidc.py `begin(step_up=True)`: `prompt=login`, `max_age`), bound to
# the session's hash instead of a pre-session, and a PENDING `auth_step_up` row
# holding the agent name and the flow. The shared callback recognises the
# step-up state, runs every login check plus a fresh `auth_time`
# (`authenticate_step_up`), requires the session's own account, makes the row
# live (auth_time + ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS) and answers a page that
# says again what was approved. The mint of THAT agent name in that session
# spends it (`consume_step_up`); a mint of any other name is refused. No session
# is minted or rotated, and no IdP token is kept.
#
# CONSENT IS EXPLICIT (decided 2026-10-28). Someone holding a stolen session
# cookie could send its owner a step-up link; the page before the IdP and the
# page after it both name the credential and the account, so the person is
# told what they are approving instead of seeing only their IdP's sign-in.
#
# WHAT CANNOT SATISFY IT. A Bearer caller (an `atlas_svc_` token or an IdP access
# token): the start route and the mint both refuse every principal that is not a
# session, before reading a grant. An old session: the grant needs an `auth_time`
# no older than the flow it came back on and no older than the window. Another
# session of the same person: the grant is keyed by the session's hash. A replay:
# the flow row and the grant are each single use. A second start in the same
# session supersedes the first: the first flow's callback is refused.
#
# CONFIGURATION WRITES TOO (TASK-1436). `PUT /api/config/{name}` re-authenticates
# every change with the owner's password (routers/config.py), which an SSO-only
# owner does not have. With step-up on, such an owner starts
# `GET /auth/oidc/step-up?config=<NAME>` instead: the pages name the setting and
# the account, and the grant holds `config:<NAME>` where an agent credential's
# holds the agent name (`config_write_grant`). A handle can never contain `:`
# (`passwords.HANDLE_RE`), so the two kinds cannot collide: a grant approved for
# an agent credential is not spendable on a configuration write, nor the other
# way round, nor one setting's on another; each is refused and the grant kept.
# Same flow, same table, same window, same single use. Local owners keep the
# password prompt.
#
# A PERSONAL PASSWORD TOO (TASK-1442). An account made with a temporary password
# and then linked (`user add`, `user link-idp`) keeps `must_change_password`: the
# SSO session passes the gate (§5), and the person may set a personal password
# WITHOUT the temporary one, because the IdP vouches for them, but only after a
# fresh step-up: `GET /auth/oidc/step-up?password=1`, grant `PASSWORD_GRANT`,
# spent by `POST /api/me/password` (routers/me.py). Only while the flag is set:
# an SSO-only account (`--no-password`) and one that already set its password
# are refused at the start, so neither is changed.
# --------------------------------------------------------------------------- #

#: The grant a configuration write spends is this prefix + the setting's name.
CONFIG_GRANT_PREFIX = "config:"

#: The grant `POST /api/me/password` spends (TASK-1442): an SSO person with no
#: personal password yet sets one. Holds `:`, so no agent handle can equal it.
PASSWORD_GRANT = "password:set"


def _grant_kind(grant: Optional[str]) -> str:
    """What a step-up grant approves: "config", "password" or "agent"."""
    if grant and grant.startswith(CONFIG_GRANT_PREFIX):
        return "config"
    if grant == PASSWORD_GRANT:
        return "password"
    return "agent"


def _grant_detail(grant: str) -> str:
    """The `auth_event.detail` naming what a grant approves."""
    kind = _grant_kind(grant)
    if kind == "config":
        return "config=%s" % _grant_setting(grant)
    if kind == "password":
        return "password=set"
    return "agent=%s" % grant


def config_write_grant(name: str) -> str:
    """What `auth_step_up.agent` holds for a step-up approving a change of `name`."""
    return CONFIG_GRANT_PREFIX + name


def _grant_setting(grant: Optional[str]) -> Optional[str]:
    """The setting a grant approves a change of, or None for an agent credential's."""
    if grant and grant.startswith(CONFIG_GRANT_PREFIX):
        return grant[len(CONFIG_GRANT_PREFIX):]
    return None


def _flow_state_digest(raw: str) -> str:
    """The keyed digest a login flow's `state` is stored under (`identity.flows`)."""
    return passwords.digest_secret(raw, label="login-flow-state-v1", key=_TOKEN_PEPPER)


def _step_up_state_prefix() -> str:
    from identity.oidc import STEP_UP_STATE_PREFIX
    return STEP_UP_STATE_PREFIX


#: The operator-facing instruction when an IdP sends no `auth_time` (Entra ID
#: without the optional claim). Logged, and shown on the refusal page, under the
#: error code `step_up_auth_time_missing` that docs/oss names.
AUTH_TIME_MISSING_HELP = (
    "The identity provider's ID token has no auth_time claim, so Atlas cannot tell "
    "that the sign-in is fresh. An operator must add auth_time to the ID token: with "
    "Entra ID, add the optional claim auth_time (app registration, Token configuration, "
    "ID token); Keycloak sends it by default.")


def self_service_agent_handle(person: AuthUser, purpose: str) -> str:
    """The name of `person`'s agent for `purpose`, or ValueError. ONE RULE with
    `atlas-admin token mint` (`passwords.agent_base`): a local person's username,
    an IdP-backed person's email local part. FALLBACK: an IdP-backed person with
    no email yet gets their subject as the base, as every self-service agent did
    before TASK-1415, rather than no credential at all; `token mint` refuses then."""
    base = passwords.agent_base(person) or person.subject
    return passwords.validated_handle("%s-%s" % (base, purpose))


def _account_label(person: AuthUser) -> str:
    return person.email or person.subject


def _step_up_failed_page(reason: str = "") -> HTMLResponse:
    if reason == "auth_time_missing":
        body = ('<p class="note">%s</p><p class="note">Error: <code>step_up_auth_time_missing'
                '</code>. No credential can be created until this is fixed.</p>'
                % html.escape(AUTH_TIME_MISSING_HELP))
    else:
        body = ('<p class="note">Atlas could not confirm a fresh sign-in at your identity '
                'provider, so nothing can be approved with it: no credential is created, '
                'no setting is changed and no password is set. Start again from where you '
                'began (Settings, Agent credentials, the setting\'s Change, or the board\'s '
                'password notice).</p>')
    return HTMLResponse(_page(
        "Confirmation failed", "Project Atlas",
        body + '<p class="note"><a href="/board">Back to the board</a></p>'),
        status_code=403)


def _approval_sentence(agent: str, person: AuthUser, rotating: bool) -> str:
    what = ("You are issuing a new credential for the agent <strong>%s</strong>"
            if rotating else "You are creating an agent credential named <strong>%s</strong>")
    return (what + " for <strong>%s</strong>.") % (html.escape(agent),
                                                   html.escape(_account_label(person)))


def _config_sentence(setting: str, person: AuthUser) -> str:
    return ("You are changing the setting <strong>%s</strong> of this Atlas install, as "
            "<strong>%s</strong>.") % (html.escape(setting), html.escape(_account_label(person)))


def _password_sentence(person: AuthUser) -> str:
    return ("You are setting a personal Atlas password for <strong>%s</strong>, without "
            "the temporary one.") % html.escape(_account_label(person))


def _password_step_up_refusal(person: AuthUser) -> Optional[Response]:
    """Why `?password=1` cannot be approved, or None: the same refusal the set itself
    gives first (routers/me.py), so nobody signs in again for nothing."""
    if not password_pending(person):
        return _json(400, {"error": "password_not_pending",
                           "detail": "This account has no temporary password to replace. "
                                     "A personal password is set here only by an SSO "
                                     "account created with one."})
    return None


def _config_step_up_refusal(principal: Principal, name: str) -> Optional[Response]:
    """Why `?config=<name>` cannot be approved, or None. The same refusals the write
    itself gives first (routers/config.py), so nobody is sent to the IdP to approve
    a change the write would refuse anyway."""
    from routers import config as _config   # late: routers.config imports this module
    import settings_catalog
    if not _config.WRITES_ON:
        return _json(400, {"error": "config_writes_off",
                           "detail": "Configuration is not changed from the board on this "
                                     "install (ATLAS_CONFIG_WRITES is off)."})
    if not principal.is_owner:
        return _json(403, {"error": "forbidden",
                           "detail": "Configuration is changed by an owner."})
    no = settings_catalog.change_refusal(name)
    if no is not None:
        code, reason = no
        return _json(404 if code == "unknown_setting" else 403,
                     {"error": code, "field": "config", "detail": reason})
    return None


def _do_step_up_start(request: Request, provider) -> Response:
    principal = _principal_of(request)
    if principal is None:  # pragma: no cover - the gate refuses first
        return _unauthorized(presented=bool(request.headers.get("authorization")))
    if principal.kind != "session" or not principal.user_id or not principal.session_id_hash:
        return _json(403, {"error": "session_required",
                           "detail": "A step-up is done by a signed-in person in a browser. "
                                     "A token cannot start one."})
    next_path = _safe_next(request.query_params.get("next"))
    purpose = (request.query_params.get("purpose") or "").strip()
    existing = (request.query_params.get("agent") or "").strip()
    setting = (request.query_params.get("config") or "").strip()
    password = bool((request.query_params.get("password") or "").strip())
    ip, ua = _client_ip(request), _user_agent(request)
    with _session() as s:
        decision = _IP_LIMITER.check(_login_key(ip))
        if not decision.allowed:
            _event(s, "throttle_engaged", ip=ip, user_agent=ua, reason="ip_ceiling:sso_step_up")
            s.commit()
            return _json(passwords.HTTP_TOO_MANY_REQUESTS,
                         {"error": "rate_limited", "detail": passwords.GENERIC_LOGIN_FAILURE_MESSAGE},
                         passwords.retry_after_headers(decision.retry_after_seconds))
        person = s.get(AuthUser, principal.user_id)
        if person is None or not identity.step_up_required(person):
            # A local person re-enters their password; there is nothing to step up to.
            return _json(400, {"error": "step_up_not_applicable",
                               "detail": "Your account signs in with a password; enter it "
                                         "when you create the credential."})
        # WHAT IS BEING APPROVED, named before anything is started: exactly one of
        # a new agent's purpose, an existing agent, a setting (TASK-1436), or a
        # personal password (TASK-1442).
        if [bool(purpose), bool(existing), bool(setting), password].count(True) != 1:
            return _json(400, {"error": "step_up_needs_agent",
                               "detail": "Name what you are approving: purpose=<purpose> for "
                                         "a new agent credential, agent=<name> to issue "
                                         "another credential for an agent you have, "
                                         "config=<setting> to change a setting, or "
                                         "password=1 to set your personal password."})
        if password:
            refused = _password_step_up_refusal(person)
            if refused is not None:
                return refused
            agent = PASSWORD_GRANT
        elif setting:
            refused = _config_step_up_refusal(principal, setting)
            if refused is not None:
                return refused
            agent = config_write_grant(setting)
        elif purpose:
            try:
                agent = self_service_agent_handle(person, purpose)
            except ValueError as exc:
                return _json(400, {"error": "bad_purpose", "field": "purpose", "detail": str(exc)})
        else:
            agent = passwords.normalise_username(existing)
            bot = s.scalar(select(AuthUser).where(AuthUser.idp == "local",
                                                  AuthUser.subject == agent))
            if bot is None or not bot.is_service or bot.person_user_id != person.id:
                return _json(404, {"error": "not_found",
                                   "detail": "No agent of yours called %r." % existing})
        try:
            location = provider.begin(s, flow_binding=principal.session_id_hash,
                                      next_path=next_path, step_up=True)
        except identity.LoginRefused as exc:
            s.rollback()
            _event(s, "login_fail", user_id=principal.user_id, ip=ip, user_agent=ua,
                   reason="%s+step-up:%s" % (provider.name, exc.reason))
            s.commit()
            logger.warning("step-up start refused: %s", exc.reason)
            return _sso_unavailable()
        state = parse_qs(urlsplit(location).query).get("state", [""])[0]
        _flows.begin_step_up(s, session_hash=principal.session_id_hash, user_id=person.id,
                             agent=agent, flow_hash=_flow_state_digest(state))
        s.commit()
        if password:
            sentence = _password_sentence(person)
        elif setting:
            sentence = _config_sentence(setting, person)
        else:
            sentence = _approval_sentence(agent, person, rotating=bool(existing))
    # A PAGE, NOT A REDIRECT: the person reads what they are approving before the
    # IdP asks for their credentials. The link is the discovered authorization
    # endpoint from configuration, never derived from the request.
    if password:
        title, then = "Confirm your personal password", "the password can then be set"
    elif setting:
        title, then = "Confirm a configuration change", "the setting can then be changed"
    else:
        title, then = "Confirm an agent credential", "the credential can then be created"
    return HTMLResponse(_page(
        title, "Project Atlas",
        '<p>%s</p><p class="note">Continue only if you started this yourself, in Atlas. '
        'Your identity provider will ask you to sign in again; %s within %d seconds, once.</p>'
        '<p><a class="sso" id="stepUpContinue" href="%s">Continue to your identity provider</a></p>'
        '<p class="note"><a href="/board">Cancel</a></p>'
        % (sentence, then, identity.SETTINGS.oidc.step_up_max_age,
           html.escape(location, quote=True))))


def _do_step_up_callback(request: Request, provider, params: dict) -> Response:
    ip, ua = _client_ip(request), _user_agent(request)
    sid = request.cookies.get(SESSION_COOKIE)
    with _session() as s:
        decision = _IP_LIMITER.check(_login_key(ip))
        if not decision.allowed:
            _event(s, "throttle_engaged", ip=ip, user_agent=ua, reason="ip_ceiling:sso_step_up")
            s.commit()
            return _json(passwords.HTTP_TOO_MANY_REQUESTS,
                         {"error": "rate_limited", "detail": passwords.GENERIC_LOGIN_FAILURE_MESSAGE},
                         passwords.retry_after_headers(decision.retry_after_seconds))
        # The callback path is public, so the session is resolved here, by the same
        # function the gate uses: a live FULL session, or nothing.
        principal = _resolve_session(s, sid) if sid else None
        binding = principal.session_id_hash if principal is not None else None
        user_id = principal.user_id if principal is not None else None
        try:
            # The flow is consumed (and the deletion committed) first, as at login.
            ident, auth_time = provider.authenticate_step_up(s, params, flow_binding=binding)
            if principal is None:  # pragma: no cover - the binding check refuses first
                raise identity.LoginRefused("no_session")
            provider.check_login_allowed(ident)
            user = provider.lookup(s, ident)
            if user is None or user.id != principal.user_id:
                # Someone else signed in at the IdP: their sign-in proves nothing
                # about the person whose session this is.
                raise identity.LoginRefused("step_up_other_account")
            if user.is_service or user.disabled_at is not None:
                raise identity.LoginRefused("disabled")
            agent = _flows.complete_step_up(
                s, session_hash=principal.session_id_hash, user_id=user.id,
                flow_hash=_flow_state_digest(params.get("state") or ""), auth_time=auth_time,
                max_age_seconds=identity.SETTINGS.oidc.step_up_max_age)
            if agent is None:
                # A later start in this session replaced the step-up this flow began.
                raise identity.LoginRefused("step_up_superseded")
            provider.after_login(s, user, ident)
        except identity.LoginRefused as exc:
            s.rollback()
            _IP_LIMITER.record_failure(_login_key(ip))
            _event(s, "login_fail", user_id=user_id, ip=ip, user_agent=ua,
                   reason="%s+step-up:%s" % (provider.name, exc.reason))
            s.commit()
            if exc.reason == "auth_time_missing":
                logger.warning("step-up refused (step_up_auth_time_missing): %s",
                               AUTH_TIME_MISSING_HELP)
            return _step_up_failed_page(exc.reason)
        setting = _grant_setting(agent)
        for_password = _grant_kind(agent) == "password"
        _event(s, "step_up_ok", user_id=user.id, ip=ip, user_agent=ua,
               reason="provider=%s" % provider.name, detail=_grant_detail(agent))
        if for_password:
            sentence = _password_sentence(user)
        elif setting:
            sentence = _config_sentence(setting, user)
        else:
            rotating = s.scalar(select(AuthUser.id).where(AuthUser.idp == "local",
                                                          AuthUser.subject == agent)) is not None
            sentence = _approval_sentence(agent, user, rotating=rotating)
        s.commit()
    back = html.escape(_safe_next(ident.next_path), quote=True)
    if for_password:
        return HTMLResponse(_page(
            "Sign-in confirmed", "Project Atlas",
            '<p>%s</p><p class="note">Choose the password on the board within %d seconds. '
            'If you did not start this, close this page and sign out: nothing is set until '
            'that password is saved from your Atlas session.</p>'
            '<p><a class="sso" id="stepUpBack" href="%s">Back to the board</a></p>'
            % (sentence, identity.SETTINGS.oidc.step_up_max_age, back)))
    if setting:
        return HTMLResponse(_page(
            "Sign-in confirmed", "Project Atlas",
            '<p>%s</p><p class="note">Save the change on the board, in its Configuration tab, '
            'within %d seconds. If you did not start this, close this page and sign out: '
            'nothing is changed until that change is saved from your Atlas session.</p>'
            '<p><a class="sso" id="stepUpBack" href="%s">Back to the board</a></p>'
            % (sentence, identity.SETTINGS.oidc.step_up_max_age, back)))
    # A PAGE, NOT A SILENT REDIRECT: it says once more what this sign-in approved.
    return HTMLResponse(_page(
        "Sign-in confirmed", "Project Atlas",
        '<p>%s</p><p class="note">Create it in Settings, Agent credentials, within %d seconds. '
        'If you did not start this, close this page and sign out: nothing is created until '
        'that credential is requested from your Atlas session.</p>'
        '<p><a class="sso" id="stepUpBack" href="%s">Back to Agent credentials</a></p>'
        % (sentence, identity.SETTINGS.oidc.step_up_max_age, back)))


def step_up_required(person: AuthUser) -> bool:
    """See `identity.step_up_required`. False for every local person and by default."""
    return identity.step_up_required(person)


def step_up_status(s: Session, principal: Principal, person: AuthUser) -> dict:
    """What the credential screen needs to know about re-authentication. Reads only."""
    if not identity.step_up_required(person):
        return {"reauth": "password"}
    provider = identity.step_up_provider()
    live = _live_grant(s, principal, person)
    if live is not None and _grant_kind(live[1]) != "agent":
        live = None   # approved for a setting or a password: nothing for this screen
    return {"reauth": "sso",
            "step_up_url": identity.step_up_path(provider.name),
            "step_up_seconds_left": max(0, int((live[0] - _now()).total_seconds())) if live else 0,
            "step_up_agent": live[1] if live else None,
            "step_up_max_age": identity.SETTINGS.oidc.step_up_max_age,
            # So the screen can say "named <prefix>-<purpose>" before the step-up.
            "agent_prefix": passwords.agent_base(person) or person.subject,
            "account": _account_label(person)}


def _live_grant(s: Session, principal: Principal, person: AuthUser) -> Optional[tuple]:
    """(expiry, grant) of this session's live step-up, or None. Reads only."""
    if principal.kind != "session" or not principal.session_id_hash:
        return None
    _flows.ensure(_ENGINE)
    return _flows.peek_step_up(s, session_hash=principal.session_id_hash, user_id=person.id)


def config_step_up_status(s: Session, principal: Principal, person: AuthUser) -> dict:
    """What the Configuration tab needs to know about re-authentication (TASK-1436).
    `{"step_up": "password"}` for a local owner and whenever step-up is off; reads only."""
    if not identity.step_up_required(person):
        return {"step_up": "password"}
    live = _live_grant(s, principal, person)
    setting = _grant_setting(live[1]) if live is not None else None
    return {"step_up": "sso",
            "step_up_url": identity.step_up_path(identity.step_up_provider().name),
            "step_up_setting": setting,
            "step_up_seconds_left": (max(0, int((live[0] - _now()).total_seconds()))
                                     if setting else 0),
            "step_up_max_age": identity.SETTINGS.oidc.step_up_max_age}


def password_setup_status(s: Session, principal: Principal, person: AuthUser) -> dict:
    """`/api/me`'s `password_setup` for an SSO person with no personal password yet
    (TASK-1442): `step_up` is "sso", with the route, the time left on a live password
    step-up and the window; or "off" while ATLAS_OIDC_STEP_UP is off, when the board
    cannot set one. Reads only."""
    if not identity.step_up_required(person):
        return {"step_up": "off"}
    live = _live_grant(s, principal, person)
    ok = live is not None and _grant_kind(live[1]) == "password"
    return {"step_up": "sso",
            "step_up_url": identity.step_up_path(identity.step_up_provider().name),
            "step_up_seconds_left": (max(0, int((live[0] - _now()).total_seconds()))
                                     if ok else 0),
            "step_up_max_age": identity.SETTINGS.oidc.step_up_max_age}


def consume_step_up(s: Session, principal: Principal, person: AuthUser, *, agent: str,
                    ip: Optional[str], user_agent: Optional[str], reason: str) -> str:
    """Spend this session's step-up on the credential for `agent`, or, with `agent`
    = `config_write_grant(name)`, on a change of that setting (TASK-1436).

    Returns "ok", "none" (no live step-up, or one approved for the OTHER kind of
    act: a credential's for a setting, a setting's for a credential; it is kept)
    or "mismatch" (one is live, approved for another agent name or another
    setting; it is kept). The caller has already refused every non-session
    principal. The delete rides the caller's transaction: a mint that fails
    afterwards keeps the grant. A refusal is an `auth_event` (`login_fail`,
    `<reason>:step_up_required` / `:step_up_agent_mismatch` /
    `:step_up_setting_mismatch` / `:step_up_purpose_mismatch`) but not a limiter
    failure: no secret was guessed.
    """
    outcome = "none"
    if principal.kind == "session" and principal.session_id_hash:
        _flows.ensure(_ENGINE)
        outcome = _flows.consume_step_up(s, session_hash=principal.session_id_hash,
                                         user_id=person.id, agent=agent)
    wanted = _grant_kind(agent)
    if outcome != "ok":
        why = "step_up_required"
        if outcome == "mismatch":
            live = _live_grant(s, principal, person)
            approved = _grant_kind(live[1]) if live is not None else wanted
            if approved != wanted:
                # Another kind of act (a credential, a setting, a password): no
                # step-up for this one; the grant is kept for what it approved.
                outcome, why = "none", "step_up_purpose_mismatch"
            else:
                why = "step_up_setting_mismatch" if wanted == "config" else "step_up_agent_mismatch"
        _event(s, "login_fail", user_id=person.id, ip=ip, user_agent=user_agent,
               reason="%s:%s" % (reason, why), detail=_grant_detail(agent))
    return outcome


def set_pending_password(s: Session, principal: Principal, person: AuthUser, *,
                         new: str, confirm: str, ip: Optional[str],
                         user_agent: Optional[str]) -> tuple[int, dict]:
    """`POST /api/me/password` (TASK-1442): an SSO person with no personal password
    yet sets one, without the temporary password, by spending a fresh IdP step-up.

    Returns (status, body). The order is the one every refusal can be read in:
    not applicable (a local account changes its password at /auth/password/change;
    step-up off; nothing pending), then the two passwords, then the policy (so a
    weak or mismatched one never spends the step-up), then the step-up. Success is
    the verifier, the flag cleared, the temp expiry dropped, every OTHER session of
    the account ended (ASVS 7.4.3, as `_do_change_password`), and a
    `password_changed` event with reason `sso_step_up`. Commits.
    """
    if identity.is_local(person):
        return 400, {"error": "step_up_not_applicable",
                     "detail": "Your account signs in with a password; change it at "
                               "/auth/password/change, with the current one."}
    if not identity.step_up_required(person):
        return 400, {"error": "step_up_off",
                     "detail": "A personal password is set from the board only with "
                               "ATLAS_OIDC_STEP_UP=on. Ask your operator."}
    if not password_pending(person):
        return 400, {"error": "password_not_pending",
                     "detail": "This account has no temporary password to replace."}
    if new != confirm:
        return 400, {"error": "password_mismatch", "field": "confirm_password",
                     "detail": "The two new passwords do not match."}
    # The context word is the name the person is known by here: the email's local
    # part (the agent-name base), not the opaque IdP subject.
    policy = passwords.check_password_policy(
        new, username=passwords.agent_base(person) or person.subject, breach_check=True)
    if not policy.ok:
        if policy.code == "breached":
            _event(s, "breach_list_reject", user_id=person.id, ip=ip, user_agent=user_agent,
                   reason=policy.code)
            s.commit()
        return 400, {"error": "password_policy", "code": policy.code,
                     "field": "new_password", "detail": policy.message}
    outcome = consume_step_up(s, principal, person, agent=PASSWORD_GRANT, ip=ip,
                              user_agent=user_agent, reason="password_set")
    if outcome != "ok":
        status = password_setup_status(s, principal, person)
        s.commit()   # the failure event is the point; a grant for another act is kept
        return 403, {"error": "step_up_required", "step_up_url": status.get("step_up_url"),
                     "detail": "Confirm with a fresh sign-in at your identity provider first, "
                               "then set the password within %d seconds. A password is not "
                               "set from a session alone." % status.get("step_up_max_age", 0)}
    if policy.breach is not None and policy.breach.source == "unavailable":
        _event(s, "breach_list_unavailable", user_id=person.id, ip=ip,
               reason="corpus_unavailable")
    now = _now()
    person.password_hash = passwords.hash_password(new)
    person.hash_scheme = passwords.HASH_SCHEME
    person.must_change_password = False
    person.temp_expires_at = None
    person.password_changed_at = now
    s.execute(delete(AuthSession).where(AuthSession.user_id == person.id,
                                        AuthSession.id_hash != principal.session_id_hash))
    _event(s, "password_changed", user_id=person.id, ip=ip, user_agent=user_agent,
           reason="sso_step_up")
    s.commit()
    return 200, {"changed": True}


# --------------------------------------------------------------------------- #
# 15. Minting + operational surface used by admin.py (TASK-970) and /api/health
# --------------------------------------------------------------------------- #

def reauthenticate(s: Session, user: AuthUser, password: str, *,
                   ip: Optional[str], user_agent: Optional[str],
                   reason: str) -> bool:
    """Re-check a signed-in person's own password. True if it matched.

    WHY A LIVE SESSION IS NOT ENOUGH (TASK-1115). A cookie session minting a
    long-lived Bearer credential is privilege laundering: a hijacked session
    becomes a key that OUTLIVES the password change made to end it, because
    changing a password kills sessions and does nothing to service tokens. One
    password prompt closes that path, and it is the same argument ASVS 6.2.3
    makes for requiring the current password on a change.

    It lives here rather than in the route because a failure has to reach the
    same two places every other credential failure does — the per-IP limiter and
    `auth_event` — and a second implementation of that is a second thing to
    forget. The caller decides what to return; this decides what is recorded.

    The `reason` is namespaced by the caller (e.g. "agent_credential") so the
    audit log distinguishes a failed re-auth from a failed login. Both are
    `login_fail` on purpose: they are the same fact about the same secret, and
    splitting them would hide re-auth attempts from anyone counting failures.
    """
    verified = passwords.verify_password(user.password_hash, password)
    if verified.ok:
        return True
    _IP_LIMITER.record_failure(_login_key(ip))
    _event(s, "login_fail", user_id=user.id, ip=ip, user_agent=user_agent,
           reason="%s:%s" % (reason, verified.reason))
    return False


def mint_service_token(
    s: Session,
    *,
    owner: AuthUser,
    scopes: Iterable[str],
    label: Optional[str] = None,
    expires_days: int = SERVICE_TOKEN_DEFAULT_DAYS,
) -> tuple[str, AuthServiceToken]:
    """Create one service token. Returns `(raw token, row)`.

    The raw value is returned ONCE and never stored — only
    `HMAC(ATLAS_TOKEN_PEPPER, token)` goes in the row. Multiple live tokens per
    principal are allowed on purpose: rotation is mint -> deploy -> confirm
    `last_used_at` moves -> revoke, and a credential you cannot rotate without
    an outage is one you will never rotate.
    """
    if not owner.is_service:
        raise ValueError("service tokens belong to an is_service auth_user row, one per caller")
    if owner.must_change_password:
        raise ValueError("a must_change_password principal cannot mint a service token")
    if owner.disabled_at is not None:
        raise ValueError("the owning principal is disabled")
    wanted = tuple(dict.fromkeys(sc.strip() for sc in scopes if sc and sc.strip()))
    if not wanted:
        raise ValueError("deny by default: a token with no scope is a broken token")
    unknown = [sc for sc in wanted if sc not in models_auth.SCOPES]
    if unknown:
        raise ValueError("unknown scope(s) %r; the domain is %r" % (unknown, models_auth.SCOPES))

    prefix, raw = passwords.generate_service_token()
    row = AuthServiceToken(
        id=str(uuid.uuid4()),
        prefix=prefix,
        token_hash=_token_digest(raw),
        scopes=",".join(wanted),
        owner_user_id=owner.id,
        label=_clean(label, 64),
        expires_at=_now() + timedelta(days=expires_days) if expires_days else None,
    )
    s.add(row)
    _event(s, "token_created", user_id=owner.id, reason=prefix, detail=_clean(label, 200))
    return raw, row


def token_warnings(s: Session) -> list[dict]:
    """Near-expiry / long-unused service tokens, for the authenticated
    `/api/health` and the board's staleness banner.

    Expiry that is surfaced is safe; expiry that is silent is how the board
    freezes — which is also why `expires_at` defaults to 365 days rather than
    something tidier.
    """
    now = _now()
    out: list[dict] = []
    for row in s.scalars(select(AuthServiceToken).where(AuthServiceToken.revoked_at.is_(None))).all():
        expires = _aware(row.expires_at)
        used = _aware(row.last_used_at)
        reasons = []
        if expires is not None and expires - now <= timedelta(days=TOKEN_EXPIRY_WARN_DAYS):
            reasons.append("expires_in_days=%d" % max(0, (expires - now).days))
        if used is None:
            reasons.append("never_used")
        elif now - used >= timedelta(days=TOKEN_UNUSED_WARN_DAYS):
            reasons.append("unused_for_days=%d" % (now - used).days)
        if reasons:
            out.append({"prefix": row.prefix, "label": row.label, "reasons": reasons})
    return out


def verify_ready(engine: Engine) -> dict:
    """Fail-closed boot check. Raises if the gate cannot enforce.

    Called from `app.startup()` INSIDE its retry loop, so a database that is
    still coming up is retried and a database that never arrives takes uvicorn
    down instead of serving the board open.
    """
    with Session(engine) as s:
        accounts = s.scalar(select(AuthUser).limit(1))
        # A read against every auth table the gate touches: if any of them is
        # missing, this raises here rather than 500-ing on the first request.
        s.execute(select(AuthSession).limit(1))
        s.execute(select(AuthServiceToken).limit(1))
        s.execute(select(AuthLoginThrottle).limit(1))
        s.execute(select(AuthEvent).limit(1))
        count = s.scalar(select(AuthUser.id).limit(1))
    if accounts is None:
        # NOT an error, and NOT a reason to mint one. Bootstrap fails closed:
        # the login page serves, every credential fails, and the first owner
        # comes from `admin.py user add` against the live DB (TASK-970).
        logger.warning(
            "auth_user is EMPTY: the login page will serve and nobody can sign in. "
            "This is correct. Create the first account with `admin.py user add` "
            "(TASK-970) — there is no bootstrap env var and no self-registration."
        )
    return {
        "gate": "AuthGate",
        "public_routes": sorted(PUBLIC_ROUTES),
        "origins": list(ALLOWED_ORIGINS),
        "bearer_hosts": sorted(BEARER_HOSTS) or ["<all allowed hosts>"],
        "machine_grace_hosts": sorted(MACHINE_GRACE_HOSTS),
        "has_accounts": count is not None,
    }
