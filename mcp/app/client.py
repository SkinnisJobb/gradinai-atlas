"""HTTP client for atlas-api — the only place atlas-mcp talks to the board.

WHERE atlas-api IS. `ATLAS_BASE_URL`, and nothing else in this module, says it. The
value belongs to the deployment, not to this file, and there are two layouts:

  - THE HOMELAB (mcp/docker-compose.yml + docker-compose.tls.yml, its own compose
    project, started with `--env-file ../deploy/homelab/atlas-mcp.env`, which is
    where the value lives): the board's LAN name on :8443, such as
    `https://atlas.example.com:8443`, its LAN front door. atlas-mcp leaves its container and comes back in through caddy-lan
    (TLS terminates there; caddy-lan reaches atlas-api :8000 on atlas-api's compose
    network). That name is in BOTH `ATLAS_LOGIN_ORIGINS` and `ATLAS_BEARER_HOSTS`
    (backend/auth.py) and resolves from inside a container via the LAN's normal DNS.
  - THE OSS INSTALL (deploy/oss/docker-compose.yml, one compose project):
    `https://atlas-api:9443`. On the internal `mcp` network the name `atlas-api`
    belongs to atlas-tls, the stack's Caddy, whose unpublished :9443 front proxies
    over HTTPS to atlas-api's own TLS listener; the certificate is checked against
    the install CA only (`ATLAS_CA_BUNDLE`). atlas-api admits a Bearer request with
    no cookie on that Host because the install sets `ATLAS_INTERNAL_HOSTS:
    atlas-api:9443` (backend/auth.py, `INTERNAL_HOSTS`). atlas-mcp shares no network
    with atlas-api there, so it has no plain-HTTP route to the API at all.

In the homelab two addresses that LOOK plausible both fail. Do not "fix" this back
to either one:

  - `host.docker.internal:8095` — the pattern other MCP services on the same host
    use to reach a sibling stack — cannot work here, because
    atlas-api publishes its raw port on `127.0.0.1:8095` ONLY (TASK-964); there is
    no LAN listener on 8095 to reach even from the host, let alone from a container.
  - `http://atlas-api:8000` over a shared compose network also fails: the auth
    gate's host allow-list (backend/auth.py, the `ALLOWED_HOSTS` check) 421s
    `misdirected_request` for any Host header that is not one of
    `ATLAS_LOGIN_ORIGINS`, the homelab leaves `ATLAS_INTERNAL_HOSTS` empty, and
    atlas-mcp is not on atlas-api's compose network in the first place. It would
    also be plain HTTP carrying a Bearer token, which app/tls.py refuses.

Do not change any atlas-api production config to make a different address work; if
the real address ever moves, change `ATLAS_BASE_URL` in the deployment's own
configuration (deploy/homelab/atlas-mcp.env, or the OSS compose file) instead. It
must be https:// with a certificate that verifies (system store, or ATLAS_CA_BUNDLE):
see app/tls.py (OSS-24).

`ATLAS_BASE_URL` is read from the environment with NO default and validated at
IMPORT time, so a missing value is a startup failure naming the variable.

WHOSE CREDENTIAL (caller auth, 2026-10). This client holds NO token of its own.
Every request carries the Bearer token of the MCP caller whose tool call it
serves, read from the HTTP request that carried that call, so atlas-api applies
that person's scopes, grants and audit, not one shared bot's. The gate that
refuses a caller with no token is `app/caller_auth.py`; this module only
forwards what the gate let in, and refuses to send a request with no caller
credential at all (fail closed, defence in depth).

The one exception is OPT-IN and temporary: `ATLAS_MCP_ALLOW_SHARED_TOKEN=1` lets a
request that arrives WITHOUT an Authorization header act as `ATLAS_TOKEN`, so the
homelab can migrate client by client. `ATLAS_TOKEN` is read by `_require_secret`, so
`ATLAS_TOKEN_FILE` also works and `ATLAS_REQUIRE_SECRET_FILES=1` refuses the plain var. It is off by default, warns at startup, and
never sends a DELETE: the shared identity must not be a way around a caller's
missing `admin` scope. A caller that DOES send a token always acts as itself.

No token, caller or shared, is ever logged or put into an error message.
"""

import contextlib
import contextvars
import os
import re
import sys
import uuid
from typing import Any, Iterator, Optional

import httpx

from .envelope import AtlasError
from .tls import check_base_url, ssl_context

_TOKEN_PREFIX = "atlas_svc_"

#: The SHAPE of a service token, checked before anything is sent. Deliberately
#: looser than backend/auth.py's `_SERVICE_TOKEN_RE`: whether a token is VALID is
#: the API's question, not this facade's. This only keeps junk (whitespace, a
#: pasted "...", a session cookie, a 10 kB header) from ever leaving the process.
_TOKEN_SHAPE = re.compile(r"^atlas_svc_[A-Za-z0-9_-]{1,200}$")


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(
            f"{name} is not set. atlas-mcp refuses to start without it — set it in "
            f"mcp/.env or the environment before running the container."
        )
    return value


# --------------------------------------------------------------------------- #
# The one place atlas-mcp reads a secret (TASK-1349).
# --------------------------------------------------------------------------- #
# atlas-mcp has no settings module and cannot import backend/settings.py (its
# image holds only mcp/), so this is the smallest reader with the SAME semantics
# as `settings.read_secret` for a required secret, no default:
#
#   1. `NAME_FILE` set -> read that file, stripped. It WINS over a plain `NAME`
#      (a note naming both variables, never the value, says so). Unreadable,
#      not UTF-8 or empty is a startup failure naming the path, never a silent
#      fall-through to the env var.
#   2. else `NAME` set -> accepted, as before, UNLESS `ATLAS_REQUIRE_SECRET_FILES=1`
#      (opt-in strict mode), which refuses a plain env secret.
#   3. `NAME` set but blank -> fails as "set but empty" (TASK-1348).
#   4. neither -> fails naming the variable.
#
# ADDITIVE: with no new variable set, a plain `ATLAS_TOKEN` in mcp/.env (the
# compose `env_file:`) is read exactly as before. No value is ever printed.
_STRICT_ENV = "ATLAS_REQUIRE_SECRET_FILES"


def _strict_mode() -> bool:
    return _truthy(_STRICT_ENV)


def _secret_is_set(name: str) -> bool:
    """Is `name` or `name_FILE` configured (non-blank)? Opens no file, reads no value."""
    return bool(os.environ.get(name + "_FILE", "").strip() or os.environ.get(name, "").strip())


def _require_secret(name: str) -> str:
    """`name` from `name_FILE` or (outside strict mode) `name`; exits naming the variable."""
    file_var = name + "_FILE"
    strict = _strict_mode()
    path = os.environ.get(file_var, "").strip()
    if path:
        if os.environ.get(name, "").strip():
            print(f"[atlas-mcp] note: {file_var} and {name} are both set; using the file "
                  f"named by {file_var} and ignoring {name}.", file=sys.stderr, flush=True)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                value = fh.read().strip()
        except OSError as exc:
            sys.exit(f"{file_var} points at {path!r}, which cannot be read ({type(exc).__name__}).")
        except UnicodeDecodeError:
            sys.exit(f"{file_var} points at {path!r}, which is not UTF-8 text.")
        if not value:
            sys.exit(f"{file_var} points at {path!r}, which is empty.")
        return value

    raw = os.environ.get(name)
    if raw is not None and raw.strip():
        if not strict:
            return raw.strip()
        sys.exit(
            f"{name} is set as a plain environment variable, which {_STRICT_ENV}=1 refuses. "
            f"Put the value in a file and set {file_var} to its path (e.g. a Docker secret "
            f"under /run/secrets/), or unset {_STRICT_ENV}."
        )
    if raw is not None and not strict:
        sys.exit(f"{name} is set but empty. Give it a value, or unset it (or set {file_var} "
                 f"to the path of a file holding it).")
    if strict:
        sys.exit(f"{file_var} is not set: {_STRICT_ENV}=1 requires {file_var} "
                 f"(the path of a file holding it).")
    sys.exit(
        f"{name} is not set (or {file_var}). atlas-mcp refuses to start without it — set it "
        f"in mcp/.env or the environment before running the container."
    )


def token_shape_ok(token: Optional[str]) -> bool:
    """True when `token` looks like a service token. Never says why not, never echoes it."""
    return bool(token) and token.isascii() and bool(_TOKEN_SHAPE.match(token))


#: A JWT's shape (three base64url segments), for an IdP access token (STORY-oss-25).
#: Accepted from a CALLER only, and only while ATLAS_OAUTH_RESOURCE is set; the shared
#: fallback token is always a service token. Whether it is VALID is atlas-api's question.
_JWT_SHAPE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$")
_JWT_MAX = 16 * 1024


def caller_token_shape_ok(token: Optional[str]) -> bool:
    """A caller's token: a service token, or (feature on) a JWT-shaped access token."""
    if token_shape_ok(token):
        return True
    from . import settings
    return (settings.OAUTH_RESOURCE is not None and bool(token) and token.isascii()
            and len(token) <= _JWT_MAX and bool(_JWT_SHAPE.match(token)))


def bearer_from_header(value: Optional[str]) -> Optional[str]:
    """The token in an `Authorization: Bearer <token>` header value, or None."""
    if not value:
        return None
    scheme, _, rest = value.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return rest.strip() or None


def _validate_shared_token(token: str) -> str:
    # A pasted placeholder containing a literal "…" once died deep inside http.client
    # with a UnicodeEncodeError that said nothing about the real mistake: check the
    # shape up front. The message never repeats any part of the value.
    if not token_shape_ok(token):
        sys.exit(
            "ATLAS_TOKEN does not look like a service token (expected ASCII starting "
            f"with {_TOKEN_PREFIX!r}). It is only read when ATLAS_MCP_ALLOW_SHARED_TOKEN=1."
        )
    return token


# HTTPS only (loopback excepted), verified against the system store or ATLAS_CA_BUNDLE:
# every request below carries a caller's Bearer token. See app/tls.py (OSS-24).
ATLAS_BASE_URL = check_base_url(_require_env("ATLAS_BASE_URL").rstrip("/"))
#: One verifying SSL context for every outbound client (this module and caller_auth).
SSL_CONTEXT = ssl_context()

#: Migration fallback, default OFF. See the module docstring.
ALLOW_SHARED_TOKEN = _truthy("ATLAS_MCP_ALLOW_SHARED_TOKEN")
SHARED_TOKEN: Optional[str] = None
if ALLOW_SHARED_TOKEN:
    SHARED_TOKEN = _validate_shared_token(_require_secret("ATLAS_TOKEN"))
    print(
        "[atlas-mcp] WARNING: ATLAS_MCP_ALLOW_SHARED_TOKEN=1: a caller that sends no "
        "Authorization header acts as the shared ATLAS_TOKEN identity (never for DELETE). "
        "This is a migration fallback: give every client its own atlas_svc_ token and "
        "unset the flag.",
        file=sys.stderr, flush=True,
    )
elif _secret_is_set("ATLAS_TOKEN"):
    print(
        "[atlas-mcp] note: ATLAS_TOKEN (or ATLAS_TOKEN_FILE) is set but IGNORED: every call "
        "acts as its caller's own Bearer token. Remove it from mcp/.env, or set "
        "ATLAS_MCP_ALLOW_SHARED_TOKEN=1 "
        "to use it as a migration fallback.",
        file=sys.stderr, flush=True,
    )


# --------------------------------------------------------------------------- #
# Where the caller's token comes from, per request.
# --------------------------------------------------------------------------- #
#: Set ONLY by `as_caller()`, for in-process callers and the verify scripts. The
#: HTTP gate deliberately does NOT set a contextvar: with stateful MCP sessions the
#: server task that runs tools is spawned by the FIRST request of a session and
#: inherits that request's context, so a contextvar set per HTTP request would leak
#: caller A's token into caller B's later call on the same session id.
_explicit_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "atlas_mcp_explicit_token", default=None
)


@contextlib.contextmanager
def as_caller(token: Optional[str]) -> Iterator[None]:
    """Run the enclosed calls as `token` (tests and in-process use only)."""
    reset = _explicit_token.set(token)
    try:
        yield
    finally:
        _explicit_token.reset(reset)


def _token_from_mcp_request() -> Optional[str]:
    """The Bearer token on the HTTP request that carried the CURRENT MCP message.

    mcp's low-level server (1.12.4, and still 1.30.0: checked in the SDK source at
    tag v1.30.0, TASK-1366) sets `request_ctx` around every request handler (tool
    call, prompt get, ...) with `.request` = the Starlette Request whose body held
    that message (per message, not per session), and FastMCP runs a sync tool inline
    in that context. That is exactly "who sent this call".

    Only a fully ABSENT `mcp` package returns None (the verify scripts that exercise
    this module without it). If the package is there but the name is not -- an SDK
    that moved or renamed it -- that is an error, loudly: a silent None here would
    drop every caller's identity. mcp_server.py imports the same name at boot, so
    such an SDK stops the process before it serves anything.
    """
    try:
        from mcp.server.lowlevel.server import request_ctx
    except ModuleNotFoundError as exc:
        if exc.name != "mcp":
            raise RuntimeError(
                "mcp is installed but mcp.server.lowlevel.server could not be imported "
                f"({exc}); atlas-mcp cannot read the caller's token") from exc
        return None
    except ImportError as exc:
        raise RuntimeError(
            "mcp.server.lowlevel.server has no `request_ctx`; atlas-mcp cannot read the "
            "caller's token (pinned mcp version changed?)") from exc
    try:
        ctx = request_ctx.get()
    except LookupError:
        return None
    headers = getattr(getattr(ctx, "request", None), "headers", None)
    if headers is None:
        return None
    return bearer_from_header(headers.get("authorization"))


def resolve_credential(method: str) -> str:
    """The token this request must carry, or AtlasError. Never returns None."""
    token = _explicit_token.get() or _token_from_mcp_request()
    if token:
        if not caller_token_shape_ok(token):
            raise AtlasError("unauthorized", "the caller's Bearer token is not a service token")
        return token
    if SHARED_TOKEN is not None:
        if method.upper() == "DELETE":
            raise AtlasError(
                "forbidden",
                "deletes are never made with the shared fallback identity: send your own "
                "Authorization: Bearer atlas_svc_... token with the admin scope",
            )
        return SHARED_TOKEN
    raise AtlasError(
        "unauthorized",
        "no caller credential: send Authorization: Bearer atlas_svc_... to atlas-mcp",
    )


# --------------------------------------------------------------------------- #
# Which tool call a request serves (STORY-oss-16-audit-export).
# --------------------------------------------------------------------------- #
# atlas-api records agent reads in `audit_read` when ATLAS_AUDIT_READS=1. The API
# knows who the caller is and which nodes it served; only this process knows WHICH
# TOOL asked. So every request sent on behalf of a tool call carries two LABELS:
#
#   X-Atlas-Tool  the MCP tool name, e.g. atlas_get
#   X-Request-ID  one random id per tool call, shared by every API request it makes,
#                 so a SIEM can group the reads one tool call caused
#
# Neither is a credential, neither is derived from one, and the API treats both as
# caller-asserted annotations. The API ignores them unless auditing is on. Set by
# `mcp_server._AuditedFastMCP.call_tool` around each tool call.
_tool_call: contextvars.ContextVar[Optional[tuple]] = contextvars.ContextVar(
    "atlas_mcp_tool_call", default=None
)


@contextlib.contextmanager
def tool_call(name: str) -> Iterator[str]:
    """Label the enclosed API requests as made by tool `name`. Yields the request id."""
    request_id = uuid.uuid4().hex
    reset = _tool_call.set((name, request_id))
    try:
        yield request_id
    finally:
        _tool_call.reset(reset)


class _CallerAuth(httpx.Auth):
    """Attach the CURRENT caller's token to each outgoing request, at send time.

    An httpx `Auth` rather than a fixed client header, so every path through the
    shared `httpx.Client` (`AtlasClient._request` and tools_graph's `_raw` alike)
    carries the right identity, and none can send without one.
    """

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = "Bearer " + resolve_credential(request.method)
        labels = _tool_call.get()
        if labels is not None:
            request.headers["X-Atlas-Tool"], request.headers["X-Request-ID"] = labels
        yield request


def _to_atlas_error(method: str, path: str, resp: httpx.Response) -> AtlasError:
    """Translate atlas-api's error envelope into an AtlasError.

    atlas-api's 400s (and most other errors) come back as
    `{"error": ..., "detail": ..., "field": ...}` — `field` names which field was
    wrong and is folded into the message, because that is the whole value of the
    response. Status codes map to distinct, stable error types so a caller can
    branch without string-matching the message.
    """
    body: Any = None
    try:
        body = resp.json()
    except ValueError:
        pass

    if isinstance(body, dict):
        reason = body.get("error") or resp.reason_phrase or "error"
        message = body.get("detail") or reason
        field = body.get("field")
        if field:
            message = f"{message} (field: {field})"
    else:
        reason = resp.reason_phrase or "error"
        message = resp.text[:300] if resp.text else reason

    status = resp.status_code
    error_type = {
        400: "invalid_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "too_large",       # /api/graph?strict=1 / ?max_bytes= refused a result
        421: "misdirected_request",
    }.get(status, f"http_{status}")

    return AtlasError(error_type, f"{method} {path} -> {status} {reason}: {message}")


class AtlasClient:
    """A minimal, deliberately narrow client for atlas-api.

    - Bearer auth only, the CALLER's (see `_CallerAuth`): no cookie, no Origin header, no csrf_token. Sending both a
      cookie and the Authorization header is an immediate 400 on atlas-api, and
      Bearer principals are exempt from the synchronizer-token (CSRF) check —
      there is nothing for this client to do about CSRF, and it must not try.
    - Never follows redirects. A Bearer caller is never 302'd by design
      (backend/auth.py); a redirect here means something is misconfigured, and
      following it would hand a tool an HTML login page as if it were JSON.
    """

    def __init__(self, base_url: str = ATLAS_BASE_URL):
        self._client = httpx.Client(
            base_url=base_url,
            auth=_CallerAuth(),
            verify=SSL_CONTEXT,
            follow_redirects=False,
            timeout=20.0,
        )

    def close(self) -> None:
        self._client.close()

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self._request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        return self._request("PATCH", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self._request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self._request("DELETE", path, **kwargs)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.RequestError as e:
            raise AtlasError("unreachable", f"{method} {path} failed: {e}") from e

        if resp.is_redirect:
            raise AtlasError(
                "unexpected_redirect",
                f"{method} {path} returned {resp.status_code} toward "
                f"{resp.headers.get('location', '<no location>')!r} — a Bearer "
                "caller should never be redirected; treating this as a hard "
                "failure rather than following it.",
            )

        if resp.status_code >= 400:
            raise _to_atlas_error(method, path, resp)

        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()


#: One shared client for the process. Tools import this rather than constructing
#: their own, so there is exactly one place the base URL and the credential rule
#: are wired. Sharing the connection pool is safe: the identity is per request.
client = AtlasClient()
