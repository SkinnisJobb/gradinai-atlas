"""The caller gate in front of `/mcp/`: no valid Bearer token, no MCP at all.

Every HTTP request to the MCP mount must carry `Authorization: Bearer atlas_svc_...`.
A request without one, with a malformed one, or with one atlas-api does not accept
is answered with HTTP 401 and a `WWW-Authenticate: Bearer` challenge HERE, before
FastMCP sees a byte of it: no session is created, no tool is listed, none runs.

WHO DECIDES. atlas-api, never this module. The gate asks `GET /api/me` with the
caller's own token and lets the request through iff the API answers 200. It reads
nothing from that answer: scopes, grants and visibility are the API's to apply on
every real call, made with the same token (`client.py`). So this is not a second
copy of the auth rules (ADR 0033: the MCP holds no logic); it is the API's own
answer, asked once at the door so an unauthenticated caller gets a real 401 instead
of a session whose every tool fails.

CACHE. A positive answer is remembered for `ATLAS_MCP_AUTH_CACHE_S` (default 60 s),
keyed by a SHA-256 of the token, never the token itself. The cache only spares a
round trip at the door: a token revoked inside that window still fails on every API
call it makes, because the API checks each one. A negative answer is not cached, so
a corrected token works at once. (atlas-api throttles repeated bad Bearer attempts
per source IP in its own bucket, and a valid token is never throttled by it.)

If atlas-api cannot be reached, the answer is 503, not a pass: fail closed.

`ATLAS_MCP_ALLOW_SHARED_TOKEN=1` (client.py) lets a request with NO Authorization
header through, to act as the shared fallback identity. A request that presents a
token is always checked as above, whether or not the fallback is on.

IdP ACCESS TOKENS (STORY-oss-25, opt-in by ATLAS_OAUTH_RESOURCE). A JWT-shaped Bearer is
then let through to the same API check (`client.caller_token_shape_ok`), and every 401
carries the RFC 9728 challenge pointing at `app/oauth_resource.py`'s metadata instead of
the realm. Off, both are exactly as before. Still the API's decision, never this gate's.

Nothing here logs a token, a prefix of one, or the header.
"""

import hashlib
import json
import logging
import os
import threading
import time
from typing import Awaitable, Callable, Optional

import httpx

from . import client as client_mod
from . import oauth_resource

log = logging.getLogger("atlas-mcp.auth")

CACHE_SECONDS = float(os.environ.get("ATLAS_MCP_AUTH_CACHE_S", "60") or 60)
_CACHE_MAX = 4096

_REALM = 'Bearer realm="atlas-mcp"'


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class _OkCache:
    """digest -> expiry. Thread-safe; bounded by dropping everything when full."""

    def __init__(self):
        self._lock = threading.Lock()
        self._until: dict = {}

    def hit(self, key: str) -> bool:
        with self._lock:
            until = self._until.get(key)
            if until is None:
                return False
            if until <= time.monotonic():
                del self._until[key]
                return False
            return True

    def put(self, key: str) -> None:
        if CACHE_SECONDS <= 0:
            return
        with self._lock:
            if len(self._until) >= _CACHE_MAX:
                self._until.clear()
            self._until[key] = time.monotonic() + CACHE_SECONDS

    def clear(self) -> None:
        with self._lock:
            self._until.clear()


_ok_cache = _OkCache()

#: Returns True (accepted), a falsy value (refused: False, or `_Refused` carrying the
#: API's reason) or raises httpx.RequestError (unreachable).
Verifier = Callable[[str], Awaitable[bool]]

#: Test seam only: the verify scripts point the door check at an in-process API.
#: None in production, where it is a real network call to ATLAS_BASE_URL.
API_TRANSPORT: Optional[httpx.AsyncBaseTransport] = None


async def verify_with_api(token: str) -> bool:
    """Ask atlas-api whether `token` is a live credential: `GET /api/me` answers 200."""
    async with httpx.AsyncClient(
        base_url=client_mod.ATLAS_BASE_URL, follow_redirects=False, timeout=10.0,
        verify=client_mod.SSL_CONTEXT, transport=API_TRANSPORT,
    ) as http:
        resp = await http.get("/api/me", headers={"Authorization": "Bearer " + token})
    if resp.status_code == 200:
        return True
    if resp.status_code in (401, 403):
        return _refusal(resp)
    # 429 (the API throttling bad attempts), 5xx, a redirect: not a yes.
    raise httpx.RequestError(f"GET /api/me answered {resp.status_code}")


class _Refused:
    """A refusal that is falsy like False and may carry the API's own reason.

    STORY-oss-25: for a genuine IdP access token whose person Atlas cannot serve yet,
    atlas-api's 401 says what to do ("sign in with SSO once"). Passing that sentence on is
    the only thing read from the answer, and only while the feature is on; the API's
    generic refusal is never repeated (it says nothing more than this gate does).
    """

    def __init__(self, detail: Optional[str]) -> None:
        self.detail = detail

    def __bool__(self) -> bool:
        return False


_GENERIC_API_DETAIL = "Authentication is required."


def _refusal(resp: httpx.Response):
    if not oauth_resource.enabled() or resp.status_code != 401:
        return False
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        return False
    if not isinstance(detail, str) or detail == _GENERIC_API_DETAIL or len(detail) > 200:
        return False
    return _Refused(detail)


def _json_response(status: int, error: str, detail: str, extra_headers=()):
    body = json.dumps({"error": error, "detail": detail}).encode()
    headers = [(b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode())]
    headers.extend(extra_headers)
    return status, headers, body


def _unauthorized(detail: str, *, presented: bool = False):
    """The 401. Feature off: today's `Bearer realm="atlas-mcp"`, byte for byte. On
    (ATLAS_OAUTH_RESOURCE, STORY-oss-25): the RFC 9728 challenge naming the metadata."""
    challenge = (oauth_resource.challenge(presented=presented)
                 if oauth_resource.enabled() else _REALM)
    return _json_response(401, "unauthorized", detail,
                          [(b"www-authenticate", challenge.encode())])


class RequireCallerToken:
    """ASGI middleware: refuse any HTTP request to the wrapped app without a valid caller token."""

    def __init__(self, app, verifier: Optional[Verifier] = None):
        self.app = app
        self.verifier = verifier  # None: verify_with_api, looked up per call

    async def _decide(self, header_value: Optional[str]):
        """None to let the request through, else (status, headers, body) to answer with."""
        if header_value is None:
            if client_mod.ALLOW_SHARED_TOKEN:
                return None
            return _unauthorized("send Authorization: Bearer atlas_svc_... (your own agent credential)")
        token = client_mod.bearer_from_header(header_value)
        if token is None or not client_mod.caller_token_shape_ok(token):
            return _unauthorized("the Authorization header is not a Bearer service token",
                                 presented=True)
        key = _digest(token)
        if _ok_cache.hit(key):
            return None
        try:
            accepted = await (self.verifier or verify_with_api)(token)
        except Exception as e:  # noqa: BLE001 - unreachable or odd answer: fail closed
            log.warning("caller check could not reach atlas-api: %s", type(e).__name__)
            return _json_response(503, "auth_unavailable",
                                  "atlas-api could not confirm the credential; try again")
        if not accepted:
            log.info("refused a caller: atlas-api did not accept the presented token")
            return _unauthorized(getattr(accepted, "detail", None)
                                 or "atlas-api did not accept this token (revoked, expired or unknown)",
                                 presented=True)
        _ok_cache.put(key)
        return None

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        header_value = None
        for name, value in scope.get("headers") or ():
            if name.lower() == b"authorization":
                header_value = value.decode("latin-1")
                break
        refusal = await self._decide(header_value)
        if refusal is None:
            await self.app(scope, receive, send)
            return
        status, headers, body = refusal
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
