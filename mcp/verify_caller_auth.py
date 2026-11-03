"""Prove atlas-mcp authenticates every caller and acts AS that caller (caller auth, 2026-10).

No database and no real atlas-api: the API is a small in-process fake that knows
three tokens and applies the API's scope rule (GET = read, POST/PATCH/PUT = write,
DELETE = admin) so this script can see what the MCP SENDS and what comes back. That
the real API enforces those scopes and attributes the write to the person is proven
against a real Postgres by mcp/verify_caller_auth_api.py.

  A. client.py: the credential rule, with no mcp package -- the caller's token is
     forwarded, nothing is sent without one, the shared fallback never deletes.
  B. startup: ATLAS_TOKEN is ignored unless ATLAS_MCP_ALLOW_SHARED_TOKEN=1, which
     warns, and no message repeats a token.
  C. caller_auth.py: the gate on its own -- 401 before the wrapped app runs, 503
     when the API cannot answer, the cache keyed by digest.
  D. the whole server, over HTTP through the real FastMCP: no token is a 401 and no
     tool runs; a read-scoped token cannot write or delete; the token forwarded is
     the one on THAT request even on another caller's session; no token in any log.
  E. the token's source: mcp.server.lowlevel.server.request_ctx exists, and a
     missing name is a loud error -- only a fully absent mcp package yields None.

    pip install -r mcp/requirements.txt && python mcp/verify_caller_auth.py

Same convention as the other mcp/verify_*.py: one [PASS]/[FAIL] line per claim,
exit 1 if any failed.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("ATLAS_BASE_URL", "https://atlas.invalid")
os.environ.pop("ATLAS_TOKEN", None)
os.environ.pop("ATLAS_MCP_ALLOW_SHARED_TOKEN", None)
os.environ["ATLAS_MCP_ENABLE_DELETE"] = "1"
sys.path.insert(0, str(HERE))

import httpx  # noqa: E402

from app import caller_auth  # noqa: E402
from app import client as client_mod  # noqa: E402
from app.envelope import AtlasError  # noqa: E402

failures: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


# Shape-valid fakes (the real shape: atlas_svc_<6 hex>_<43 urlsafe>).
T_READ = "atlas_svc_aaaaaa_" + "r" * 43
T_WRITE = "atlas_svc_bbbbbb_" + "w" * 43
T_ADMIN = "atlas_svc_cccccc_" + "a" * 43
T_SHARED = "atlas_svc_dddddd_" + "s" * 43
T_UNKNOWN = "atlas_svc_eeeeee_" + "u" * 43
ALL_TOKENS = (T_READ, T_WRITE, T_ADMIN, T_SHARED, T_UNKNOWN)


class FakeAPI:
    """atlas-api's auth answer in miniature: who a token is, and its scopes."""

    PEOPLE = {
        T_READ: ("bot-alex-reader", ("read",)),
        T_WRITE: ("bot-alex-writer", ("read", "write")),
        T_ADMIN: ("bot-sam-admin", ("read", "write", "admin")),
        T_SHARED: ("bot-atlas-mcp", ("read", "write", "admin")),
    }

    def __init__(self):
        self.seen = []  # (method, path, token or None)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        token = client_mod.bearer_from_header(request.headers.get("authorization"))
        self.seen.append((request.method, request.url.path, token))
        who = self.PEOPLE.get(token)
        if who is None:
            return httpx.Response(401, json={"error": "unauthorized", "detail": "Authentication is required."})
        name, scopes = who
        needed = {"GET": "read", "DELETE": "admin"}.get(request.method, "write")
        if needed not in scopes:
            return httpx.Response(403, json={"error": "forbidden",
                                             "detail": f"missing required scope: {needed}"})
        if request.url.path == "/api/me":
            return httpx.Response(200, json={"username": name, "scopes": list(scopes)})
        if request.method == "GET" and request.url.path.startswith("/api/node/"):
            return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1],
                                             "read_by": name, "attributes": {}})
        return httpx.Response(200, json={"by": name})

    def calls(self, method=None):
        return [c for c in self.seen if method is None or c[0] == method]


# --------------------------------------------------------------------------- #
# A. The credential rule in client.py
# --------------------------------------------------------------------------- #
def section_a(api: FakeAPI) -> None:
    print("-- A. client.py forwards the caller's token and sends nothing without one --")
    c = client_mod.AtlasClient()
    c._client._transport = httpx.MockTransport(api)

    with client_mod.as_caller(T_READ):
        out = c.get("/api/node/x")
    check("the caller's token is the one atlas-api receives",
          api.seen[-1] == ("GET", "/api/node/x", T_READ), str(api.seen[-1:]))
    check("and the API's answer is that caller's", out.get("read_by") == "bot-alex-reader", str(out))

    n = len(api.seen)
    try:
        c.get("/api/node/x")
        raised = None
    except AtlasError as e:
        raised = e
    check("no caller credential: refused as unauthorized", raised is not None
          and raised.error_type == "unauthorized", repr(raised))
    check("and nothing was sent to atlas-api", len(api.seen) == n, str(api.seen[n:]))

    with client_mod.as_caller("Bearer-ish junk with spaces"):
        try:
            c.get("/api/node/x")
            raised = None
        except AtlasError as e:
            raised = e
    check("a malformed caller token is refused before sending",
          raised is not None and raised.error_type == "unauthorized" and len(api.seen) == n,
          repr(raised))
    check("the refusal does not echo the token", raised is not None and "junk" not in str(raised))

    print("-- A2. the shared fallback: header-less calls only, never DELETE --")
    client_mod.SHARED_TOKEN, client_mod.ALLOW_SHARED_TOKEN = T_SHARED, True
    try:
        c.get("/api/node/y")
        check("with the fallback on, a header-less call acts as the shared token",
              api.seen[-1] == ("GET", "/api/node/y", T_SHARED), str(api.seen[-1:]))
        with client_mod.as_caller(T_READ):
            c.get("/api/node/z")
        check("a caller WITH a token still acts as itself, not the shared one",
              api.seen[-1] == ("GET", "/api/node/z", T_READ), str(api.seen[-1:]))
        n = len(api.seen)
        try:
            c.delete("/api/edge/1")
            raised = None
        except AtlasError as e:
            raised = e
        check("the shared identity never sends a DELETE",
              raised is not None and raised.error_type == "forbidden" and len(api.seen) == n,
              repr(raised))
    finally:
        client_mod.SHARED_TOKEN, client_mod.ALLOW_SHARED_TOKEN = None, False
    c.close()


# --------------------------------------------------------------------------- #
# B. Startup
# --------------------------------------------------------------------------- #
def _import_client(env_extra: dict) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in ("ATLAS_TOKEN", "ATLAS_MCP_ALLOW_SHARED_TOKEN")}
    env.update(env_extra)
    code = "import app.client as c; print('SHARED', c.SHARED_TOKEN is not None)"
    return subprocess.run([sys.executable, "-c", code], cwd=str(HERE), env=env,
                          capture_output=True, text=True, timeout=60)


def section_b() -> None:
    print("-- B. startup: ATLAS_TOKEN is opt-in, warned about, never printed --")
    r = _import_client({})
    check("no ATLAS_TOKEN at all: starts, with no shared identity",
          r.returncode == 0 and "SHARED False" in r.stdout, r.stderr[-200:])
    r = _import_client({"ATLAS_TOKEN": T_SHARED})
    check("ATLAS_TOKEN set without the flag: IGNORED, and says so",
          r.returncode == 0 and "SHARED False" in r.stdout and "IGNORED" in r.stderr, r.stderr[-200:])
    check("...without printing it", T_SHARED not in r.stdout + r.stderr)
    r = _import_client({"ATLAS_MCP_ALLOW_SHARED_TOKEN": "1"})
    check("the flag without ATLAS_TOKEN: refuses to start, naming ATLAS_TOKEN",
          r.returncode != 0 and "ATLAS_TOKEN" in r.stderr, r.stderr[-200:])
    r = _import_client({"ATLAS_MCP_ALLOW_SHARED_TOKEN": "1", "ATLAS_TOKEN": T_SHARED})
    check("the flag with ATLAS_TOKEN: starts with the fallback, and WARNS",
          r.returncode == 0 and "SHARED True" in r.stdout and "WARNING" in r.stderr, r.stderr[-200:])
    check("...without printing the token", T_SHARED not in r.stdout + r.stderr)
    r = _import_client({"ATLAS_MCP_ALLOW_SHARED_TOKEN": "1", "ATLAS_TOKEN": "atlas_svc_…placeholder"})
    check("a pasted placeholder is refused at startup, value not echoed",
          r.returncode != 0 and "placeholder" not in r.stderr, r.stderr[-200:])


# --------------------------------------------------------------------------- #
# C. The gate alone
# --------------------------------------------------------------------------- #
def section_c() -> None:
    print("-- C. caller_auth.RequireCallerToken: refuse before the wrapped app runs --")
    reached = []
    verified = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"inner"})

    async def verifier(token):
        verified.append(token)
        if token == "atlas_svc_down":
            raise httpx.ConnectError("no route")
        return token in (T_READ, T_WRITE)

    gate = caller_auth.RequireCallerToken(inner, verifier=verifier)
    caller_auth._ok_cache.clear()

    async def go(headers):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate),
                                     base_url="http://mcp") as h:
            return await h.post("/", headers=headers, content=b"{}")

    def run(headers):
        return asyncio.run(go(headers))

    r = run({})
    check("no Authorization header: 401", r.status_code == 401, str(r.status_code))
    check("with a Bearer challenge", r.headers.get("www-authenticate", "").startswith("Bearer"),
          str(r.headers))
    check("the wrapped app never ran", reached == [], str(reached))
    for label, value in (("Basic auth", "Basic dXNlcjpwYXNz"),
                         ("an empty Bearer", "Bearer "),
                         ("a non-service token", "Bearer eyJhbGciOi.jwt.thing"),
                         ("a non-ASCII token", "Bearer atlas_svc_…")):
        r = run({"Authorization": value.encode("utf-8")})
        check(f"{label}: 401, the app never ran, the API never asked",
              r.status_code == 401 and reached == [] and verified == [],
              f"{r.status_code} {reached} {verified}")
    r = run({"Authorization": "Bearer " + T_UNKNOWN})
    check("a well-shaped token atlas-api does not accept: 401", r.status_code == 401
          and reached == [] and verified == [T_UNKNOWN], f"{r.status_code} {verified}")
    check("the 401 body does not echo the token", T_UNKNOWN not in r.text, r.text)
    r = run({"Authorization": "Bearer atlas_svc_down"})
    check("atlas-api unreachable: 503, fail closed", r.status_code == 503 and reached == [],
          str(r.status_code))
    verified.clear()
    r = run({"Authorization": "Bearer " + T_READ})
    check("a token atlas-api accepts: through to the app", r.status_code == 200
          and reached == ["/"], f"{r.status_code} {reached}")
    run({"Authorization": "bearer " + T_READ})
    check("a second request inside the cache window does not re-ask the API",
          verified == [T_READ], str(verified))
    check("the cache holds a digest, never the token",
          all(T_READ not in k for k in caller_auth._ok_cache._until), "")
    caller_auth._ok_cache.clear()

    async def lifespan_passes():
        seen = []

        async def app(scope, receive, send):
            seen.append(scope["type"])
        await caller_auth.RequireCallerToken(app)({"type": "lifespan"}, None, None)
        return seen
    check("non-HTTP scopes (lifespan) pass through untouched",
          asyncio.run(lifespan_passes()) == ["lifespan"])


# --------------------------------------------------------------------------- #
# D. The whole server over HTTP
# --------------------------------------------------------------------------- #
ACCEPT = "application/json, text/event-stream"


def _rpc(http, token, method, params=None, sid=None, rid=1):
    headers = {"Accept": ACCEPT, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if sid:
        headers["mcp-session-id"] = sid
    body = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        body["id"] = rid
    if params is not None:
        body["params"] = params
    r = http.post("/mcp/", json=body, headers=headers)
    msg = None
    if r.status_code == 200 and r.text.strip():
        if r.headers.get("content-type", "").startswith("text/event-stream"):
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    msg = json.loads(line[5:].strip())
        else:
            msg = r.json()
    return r, msg


def _session(http, token):
    r, msg = _rpc(http, token, "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "verify_caller_auth", "version": "0"}})
    sid = r.headers.get("mcp-session-id")
    if r.status_code == 200:
        _rpc(http, token, "notifications/initialized", sid=sid, rid=None)
    return r, msg, sid


def _call(http, token, sid, name, arguments, rid=7):
    r, msg = _rpc(http, token, "tools/call", {"name": name, "arguments": arguments},
                  sid=sid, rid=rid)
    if msg is None:
        return r, None
    content = (msg.get("result") or {}).get("content") or [{}]
    try:
        return r, json.loads(content[0].get("text") or "null")
    except ValueError:
        return r, {"raw": content}


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001
            self.lines.append(str(record.msg))


def section_d(api: FakeAPI) -> None:
    print("-- D. the whole server: FastMCP behind the gate, talking to a fake atlas-api --")
    try:
        from fastapi.testclient import TestClient
        from app import main
    except ImportError as e:
        check("the mcp package is installed (pip install -r mcp/requirements.txt)", False, str(e))
        return

    # Every logger at DEBUG, into a list rather than the console: the claim in D3
    # is about what ANY library would have written, at the most verbose level.
    capture = _Capture()
    root = logging.getLogger()
    old_handlers, old_level = list(root.handlers), root.level
    root.handlers = [capture]
    root.setLevel(logging.DEBUG)

    transport = httpx.MockTransport(api)
    client_mod.client._client._transport = transport
    caller_auth.API_TRANSPORT = transport
    caller_auth._ok_cache.clear()
    try:
        with TestClient(main.app, base_url="http://localhost:8092") as http:
            n = len(api.seen)
            r, msg, _ = _session(http, None)
            check("initialize with no token: HTTP 401", r.status_code == 401, str(r.status_code))
            check("...no MCP session was created", "mcp-session-id" not in r.headers, str(r.headers))
            check("...and atlas-api was never called", len(api.seen) == n, str(api.seen[n:]))

            r, msg, _ = _session(http, T_UNKNOWN)
            check("initialize with a token atlas-api rejects: HTTP 401", r.status_code == 401,
                  str(r.status_code))

            r, msg, sid_read = _session(http, T_READ)
            check("initialize with a valid token: 200 and a session",
                  r.status_code == 200 and bool(sid_read), f"{r.status_code} {msg}")

            n = len(api.seen)
            r, _ = _rpc(http, None, "tools/call", {"name": "atlas_get", "arguments": {"id": "x"}},
                        sid=sid_read, rid=3)
            check("a tools/call on a live session WITHOUT a token: 401, no tool ran",
                  r.status_code == 401 and len(api.seen) == n, f"{r.status_code} {api.seen[n:]}")

            r, out = _call(http, T_READ, sid_read, "atlas_get", {"id": "x"})
            check("atlas_get as the read token: ok", (out or {}).get("ok") is True, str(out)[:200])
            check("the token atlas-api received is the caller's",
                  ("GET", "/api/node/x", T_READ) in api.seen, str(api.seen[-3:]))
            check("the answer is the caller's view", ((out or {}).get("result") or {})
                  .get("read_by") == "bot-alex-reader", str(out)[:200])

            r, out = _call(http, T_READ, sid_read, "atlas_node_write",
                           {"id": "x", "summary": "changed"})
            check("a read-scoped token cannot write: forbidden from the API",
                  (out or {}).get("ok") is False
                  and (out.get("error") or {}).get("type") == "forbidden", str(out)[:200])

            r, out = _call(http, T_READ, sid_read, "atlas_delete", {"kind": "edge", "edge_id": 1})
            check("a read-scoped token cannot delete: forbidden from the API",
                  (out or {}).get("ok") is False
                  and (out.get("error") or {}).get("type") == "forbidden", str(out)[:200])
            check("the delete went to the API AS the read token -- the MCP adds no authority",
                  ("DELETE", "/api/edge/1", T_READ) in api.seen
                  and not any(m == "DELETE" and t != T_READ for m, _, t in api.seen),
                  str(api.calls("DELETE")))

            r, out = _call(http, T_WRITE, sid_read, "atlas_node_write",
                           {"id": "x", "summary": "changed"})
            check("identity is per MESSAGE: the write token on the reader's session acts "
                  "as the writer", (out or {}).get("ok") is True
                  and any(m == "PATCH" and t == T_WRITE for m, _, t in api.seen), str(out)[:200])
            r, out = _call(http, T_READ, sid_read, "atlas_node_write", {"id": "x", "summary": "again"})
            check("...and the next read-token message on that session is the reader again",
                  (out or {}).get("ok") is False, str(out)[:200])

            r, out = _call(http, T_WRITE, sid_read, "atlas_delete", {"kind": "edge", "edge_id": 2})
            check("write without admin cannot delete", (out or {}).get("ok") is False
                  and (out.get("error") or {}).get("type") == "forbidden", str(out)[:200])
            r, out = _call(http, T_ADMIN, sid_read, "atlas_delete", {"kind": "edge", "edge_id": 3})
            check("an admin-scoped token can", (out or {}).get("ok") is True, str(out)[:200])

            print("-- D2. the opt-in shared fallback, end to end --")
            client_mod.SHARED_TOKEN, client_mod.ALLOW_SHARED_TOKEN = T_SHARED, True
            try:
                r, msg, sid_anon = _session(http, None)
                check("with the flag, a header-less initialize is let through",
                      r.status_code == 200 and bool(sid_anon), str(r.status_code))
                r, out = _call(http, None, sid_anon, "atlas_get", {"id": "y"})
                check("...and acts as the shared identity",
                      ("GET", "/api/node/y", T_SHARED) in api.seen, str(api.seen[-2:]))
                n = len(api.seen)
                r, out = _call(http, None, sid_anon, "atlas_delete", {"kind": "edge", "edge_id": 4})
                check("...but never deletes as it, and asks the API nothing",
                      (out or {}).get("ok") is False and not any(
                          m == "DELETE" for m, _, _ in api.seen[n:]), str(out)[:200])
                r, msg, _ = _session(http, T_UNKNOWN)
                check("a PRESENTED bad token is still a 401 with the flag on",
                      r.status_code == 401, str(r.status_code))
            finally:
                client_mod.SHARED_TOKEN, client_mod.ALLOW_SHARED_TOKEN = None, False
    finally:
        root.handlers = old_handlers
        root.setLevel(old_level)
        caller_auth.API_TRANSPORT = None

    print("-- D3. no token in any log line --")
    leaked = [line for line in capture.lines if any(t in line for t in ALL_TOKENS)]
    check(f"none of {len(capture.lines)} log lines carries a token", not leaked, str(leaked[:3]))


# --------------------------------------------------------------------------- #
# E. Where the caller's token is read from (TASK-1366, mcp 1.30)
# --------------------------------------------------------------------------- #
_NO_MCP = """
import sys
class _NoMcp:
    def find_spec(self, name, path=None, target=None):
        if name == "mcp" or name.startswith("mcp."):
            raise ModuleNotFoundError("No module named 'mcp'", name="mcp")
sys.meta_path.insert(0, _NoMcp())
import app.client as c
print("TOKEN", c._token_from_mcp_request())
"""


def section_e() -> None:
    print("-- E. request_ctx: only a fully absent mcp package may yield no token --")
    try:
        import mcp.server.lowlevel.server as lowlevel
    except ImportError as e:
        check("the mcp package is installed (pip install -r mcp/requirements.txt)", False, str(e))
        return
    check("mcp.server.lowlevel.server still has request_ctx (client.py reads the token there)",
          hasattr(lowlevel, "request_ctx"))
    check("outside an MCP request: no token, no error",
          client_mod._token_from_mcp_request() is None)

    real = sys.modules["mcp.server.lowlevel.server"]
    import types
    sys.modules["mcp.server.lowlevel.server"] = types.ModuleType("mcp.server.lowlevel.server")
    try:
        client_mod._token_from_mcp_request()
        raised = None
    except RuntimeError as e:
        raised = e
    finally:
        sys.modules["mcp.server.lowlevel.server"] = real
    check("mcp installed but request_ctx gone: a loud RuntimeError, not a silent None",
          raised is not None and "request_ctx" in str(raised), repr(raised))

    env = {k: v for k, v in os.environ.items()
           if k not in ("ATLAS_TOKEN", "ATLAS_MCP_ALLOW_SHARED_TOKEN")}
    r = subprocess.run([sys.executable, "-c", _NO_MCP], cwd=str(HERE), env=env,
                       capture_output=True, text=True, timeout=60)
    check("no mcp package at all: no token (the stubbed-script case), no error",
          r.returncode == 0 and "TOKEN None" in r.stdout, (r.stdout + r.stderr)[-300:])


def main() -> None:
    api = FakeAPI()
    section_a(api)
    section_b()
    section_c()
    section_d(FakeAPI())
    section_e()
    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
