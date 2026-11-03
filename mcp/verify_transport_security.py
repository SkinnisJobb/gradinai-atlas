"""Prove atlas-mcp's streamable-HTTP transport settings on mcp 1.30 (TASK-1366).

The owner's decisions (2026-10-25), and what this script holds them to:

  A. the setting: ATLAS_MCP_ALLOWED_HOSTS / _ORIGINS unset = the documented defaults,
     set = replaces them, set-but-blank = the process refuses to start, naming it.
  B. the session idle timeout is 4 hours (14400 s) -- on FastMCP's settings, on the
     session manager main.py runs, and on a live session's transport.
  C. DNS-rebinding protection is ON with the explicit list: over HTTP through the real
     gate and the real FastMCP, every default host is accepted (a wildcard `:*` entry
     is tried with the ports in use), a Host outside the list gets 421 and no session,
     a foreign Origin 403, an allowed Origin or none passes.
  D. per-caller identity still holds on 1.30: two tokens, through the TLS door's Host,
     each reach atlas-api as themselves.
  E. tools/list over HTTP is exactly FastMCP's own registry (names, descriptions,
     schemas), so the transport changed nothing a client sees.

No database and no real atlas-api: a small in-process fake answers `/api/me` and
`/api/node/<id>` (the same shape as verify_caller_auth.py's). That 1.30 produces the
same tools/list as 1.12.4 did is measured outside CI (the PR records it); E guards
the wire against FastMCP's registry on whatever SDK is installed.

    pip install -r mcp/requirements.txt && python mcp/verify_transport_security.py

Same convention as the other mcp/verify_*.py: one [PASS]/[FAIL] line per claim,
exit 1 if any failed.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("ATLAS_BASE_URL", "https://atlas.invalid")
os.environ.pop("ATLAS_TOKEN", None)
os.environ.pop("ATLAS_MCP_ALLOW_SHARED_TOKEN", None)
os.environ.pop("ATLAS_MCP_ALLOWED_HOSTS", None)
os.environ.pop("ATLAS_MCP_ALLOWED_ORIGINS", None)
sys.path.insert(0, str(HERE))

import httpx  # noqa: E402

from app import settings  # noqa: E402

failures: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


T_ALEX = "atlas_svc_aaaaaa_" + "r" * 43
T_SAM = "atlas_svc_bbbbbb_" + "w" * 43
PEOPLE = {T_ALEX: "bot-alex", T_SAM: "bot-sam"}

FOUR_HOURS_S = 4 * 60 * 60

#: The defaults are mcp/app/distribution.py's (STORY-oss-23 A2). The homelab's values are
#: pinned there a second time ON PURPOSE, by mcp/verify_distribution.py: changing the list
#: must be a visible, reviewed edit in two places, because a host dropped from it is a 421
#: for everyone who uses that host. Here the lists are read from distribution.py directly,
#: so settings.py is checked against its source, not against itself.
from app import distribution  # noqa: E402

DOOR = distribution.DOOR_HOST
DOOR_NAME = DOOR.rsplit(":", 1)[0]
EXPECTED_HOSTS = tuple(distribution.DEFAULT_ALLOWED_HOSTS)
EXPECTED_ORIGINS = tuple(distribution.DEFAULT_ALLOWED_ORIGINS)

#: Concrete Host headers for each default entry: a `:*` entry with the ports that
#: actually reach it (8092 the publish and local uvicorn) and 18092, a second port
#: that shows `:*` means any port; a fixed entry as itself.
CONCRETE_HOSTS = {
    entry: ((entry[:-1] + "8092", entry[:-1] + "18092") if entry.endswith(":*") else (entry,))
    for entry in EXPECTED_HOSTS
}

#: Hosts that must be refused: a stranger, our own name on the wrong port or with
#: none (the TLS front listens only on the door's port), a rebinding-style lookalike,
#: and a bare address on the service port.
FOREIGN_HOSTS = (
    "evil.example.com",
    DOOR_NAME + ":9999",
    DOOR_NAME,
    DOOR_NAME + ".evil.example.com:" + DOOR.rsplit(":", 1)[1],
    "192.0.2.10:8092",
)


class FakeAPI:
    def __init__(self):
        self.seen = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization") or ""
        token = auth[7:] if auth.lower().startswith("bearer ") else None
        self.seen.append((request.method, request.url.path, token))
        who = PEOPLE.get(token)
        if who is None:
            return httpx.Response(401, json={"error": "unauthorized", "detail": "Authentication is required."})
        if request.url.path == "/api/me":
            return httpx.Response(200, json={"username": who, "scopes": ["read", "write", "admin"]})
        if request.method == "GET" and request.url.path.startswith("/api/node/"):
            return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1],
                                             "read_by": who, "attributes": {}})
        return httpx.Response(200, json={"by": who})


# --------------------------------------------------------------------------- #
# A. The setting
# --------------------------------------------------------------------------- #
def _boot(env_extra: dict) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in ("ATLAS_MCP_ALLOWED_HOSTS", "ATLAS_MCP_ALLOWED_ORIGINS")}
    env.update(env_extra)
    code = "import app.settings as s; print('HOSTS', ','.join(s.ALLOWED_HOSTS))"
    return subprocess.run([sys.executable, "-c", code], cwd=str(HERE), env=env,
                          capture_output=True, text=True, timeout=60)


def section_a() -> None:
    print("-- A. ATLAS_MCP_ALLOWED_HOSTS / _ORIGINS --")
    hosts, origins = settings.load_allowed_hosts({})
    check("unset: the default hosts, exactly", hosts == EXPECTED_HOSTS, str(hosts))
    check("unset: the default origins, exactly", origins == EXPECTED_ORIGINS, str(origins))
    check("the module-level values are the defaults with nothing set",
          settings.ALLOWED_HOSTS == EXPECTED_HOSTS and settings.ALLOWED_ORIGINS == EXPECTED_ORIGINS)
    hosts, origins = settings.load_allowed_hosts(
        {"ATLAS_MCP_ALLOWED_HOSTS": " mcp.example.com:8446 , localhost:* ,",
         "ATLAS_MCP_ALLOWED_ORIGINS": "https://mcp.example.com:8446"})
    check("set: a comma list that REPLACES the default, entries trimmed",
          hosts == ("mcp.example.com:8446", "localhost:*")
          and origins == ("https://mcp.example.com:8446",), f"{hosts} {origins}")
    for name in ("ATLAS_MCP_ALLOWED_HOSTS", "ATLAS_MCP_ALLOWED_ORIGINS"):
        for blank in ("", "  ", " , ,"):
            try:
                settings.load_allowed_hosts({name: blank})
                raised = None
            except settings.AllowedHostsError as e:
                raised = e
            check(f"{name}={blank!r}: refused, naming the variable",
                  raised is not None and name in str(raised), repr(raised))
        r = _boot({name: " "})
        check(f"{name} set but blank: the process refuses to start, naming it",
              r.returncode != 0 and name in r.stderr, r.stderr[-200:])
    r = _boot({})
    check("unset: the process starts with the defaults",
          r.returncode == 0 and "HOSTS " + ",".join(EXPECTED_HOSTS) in r.stdout, r.stderr[-200:])


# --------------------------------------------------------------------------- #
# HTTP helpers (the same wire as verify_caller_auth.py section D)
# --------------------------------------------------------------------------- #
ACCEPT = "application/json, text/event-stream"


def _rpc(http, token, method, params=None, sid=None, rid=1, host=None, origin=None):
    headers = {"Accept": ACCEPT, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if sid:
        headers["mcp-session-id"] = sid
    if host:
        headers["Host"] = host
    if origin:
        headers["Origin"] = origin
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


def _session(http, token, host=None, origin=None):
    r, msg = _rpc(http, token, "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "verify_transport_security", "version": "0"}},
        host=host, origin=origin)
    sid = r.headers.get("mcp-session-id")
    if r.status_code == 200:
        _rpc(http, token, "notifications/initialized", sid=sid, rid=None, host=host, origin=origin)
    return r, msg, sid


def _call(http, token, sid, name, arguments, host=None):
    r, msg = _rpc(http, token, "tools/call", {"name": name, "arguments": arguments},
                  sid=sid, rid=7, host=host)
    if msg is None:
        return r, None
    content = (msg.get("result") or {}).get("content") or [{}]
    try:
        return r, json.loads(content[0].get("text") or "null")
    except ValueError:
        return r, {"raw": content}


# --------------------------------------------------------------------------- #
# B-E. The whole server
# --------------------------------------------------------------------------- #
def sections_b_to_e() -> None:
    try:
        from fastapi.testclient import TestClient
        from app import caller_auth
        from app import client as client_mod
        from app import main
        from app import mcp_server
    except ImportError as e:
        check("the mcp package is installed (pip install -r mcp/requirements.txt)", False, str(e))
        return

    print("-- B. the session idle timeout is 4 hours --")
    real = mcp_server.mcp._real
    check("mcp_server.SESSION_IDLE_TIMEOUT_S is 4 h", mcp_server.SESSION_IDLE_TIMEOUT_S == FOUR_HOURS_S,
          str(mcp_server.SESSION_IDLE_TIMEOUT_S))
    check("FastMCP's settings carry it", real.settings.session_idle_timeout == FOUR_HOURS_S,
          str(real.settings.session_idle_timeout))
    manager = main.mcp.session_manager
    check("the session manager main.py runs uses it",
          getattr(manager, "session_idle_timeout", None) == FOUR_HOURS_S,
          str(getattr(manager, "session_idle_timeout", None)))

    ts = real.settings.transport_security
    check("DNS-rebinding protection is ON", ts is not None and ts.enable_dns_rebinding_protection is True,
          repr(ts))
    check("with the explicit host list", ts is not None and tuple(ts.allowed_hosts) == EXPECTED_HOSTS,
          str(ts and ts.allowed_hosts))
    check("and the explicit origin list", ts is not None and tuple(ts.allowed_origins) == EXPECTED_ORIGINS,
          str(ts and ts.allowed_origins))
    check("the session manager enforces those same settings",
          getattr(manager, "security_settings", None) is ts, repr(getattr(manager, "security_settings", None)))

    api = FakeAPI()
    transport = httpx.MockTransport(api)
    client_mod.client._client._transport = transport
    caller_auth.API_TRANSPORT = transport
    caller_auth._ok_cache.clear()
    try:
        with TestClient(main.app, base_url="http://localhost:8092") as http:
            r, msg, sid = _session(http, T_ALEX, host=DOOR)
            live = getattr(manager, "_server_instances", {}).get(sid)
            check("a live session's transport has the 4 h idle timeout",
                  live is not None and getattr(live, "_idle_timeout", None) == FOUR_HOURS_S
                  and getattr(live, "idle_scope", None) is not None,
                  f"{sid} {getattr(live, '_idle_timeout', None)}")

            print("-- C. allowed hosts: every default accepted, anything else 421 --")
            for entry in EXPECTED_HOSTS:
                for host in CONCRETE_HOSTS[entry]:
                    r, msg, sid = _session(http, T_ALEX, host=host)
                    check(f"Host {host} ({entry}): initialize 200 with a session",
                          r.status_code == 200 and bool(sid)
                          and ((msg or {}).get("result") or {}).get("serverInfo", {}).get("name") == "atlas",
                          str(r.status_code) if r.status_code == 200 else f"{r.status_code} {r.text[:120]!r}")
            r, msg, sid = _session(http, T_ALEX)
            check("the TestClient's own base_url Host (localhost:8092): 200", r.status_code == 200 and bool(sid),
                  str(r.status_code))
            for host in FOREIGN_HOSTS:
                r, msg, sid = _session(http, T_ALEX, host=host)
                check(f"Host {host}: 421 and no session", r.status_code == 421 and not sid,
                      f"{r.status_code} {r.text[:120]!r}")
            r, msg, sid = _session(http, T_ALEX, host=DOOR)
            r2, _ = _rpc(http, T_ALEX, "tools/list", {}, sid=sid, rid=2, host="evil.example.com")
            check("a foreign Host on an EXISTING session is 421 too", r2.status_code == 421,
                  str(r2.status_code))
            r, msg, sid = _session(http, T_ALEX, host=DOOR,
                                   origin="https://evil.example.com")
            check("a foreign Origin: 403 and no session", r.status_code == 403 and not sid,
                  f"{r.status_code} {r.text[:120]!r}")
            for origin in ("https://" + DOOR, "http://localhost:6274"):
                r, msg, sid = _session(http, T_ALEX, host=DOOR, origin=origin)
                check(f"an allowed Origin ({origin}): 200", r.status_code == 200 and bool(sid),
                      str(r.status_code))
            r, msg, sid = _session(http, None, host="evil.example.com")
            check("no token at a foreign Host: still the gate's 401 first, nothing reached FastMCP",
                  r.status_code == 401 and not sid, str(r.status_code))

            print("-- D. per-caller identity on mcp 1.30 --")
            door = DOOR
            r, msg, sid = _session(http, T_ALEX, host=door)
            r, out_a = _call(http, T_ALEX, sid, "atlas_get", {"id": "x"}, host=door)
            r, out_j = _call(http, T_SAM, sid, "atlas_get", {"id": "y"}, host=door)
            check("Alex's call reaches atlas-api with Alex's token",
                  ("GET", "/api/node/x", T_ALEX) in api.seen, str(api.seen[-4:]))
            check("...and answers as Alex", ((out_a or {}).get("result") or {}).get("read_by") == "bot-alex",
                  str(out_a)[:200])
            check("Sam's call on the same session reaches it with Sam's token",
                  ("GET", "/api/node/y", T_SAM) in api.seen, str(api.seen[-4:]))
            check("...and answers as Sam", ((out_j or {}).get("result") or {}).get("read_by") == "bot-sam",
                  str(out_j)[:200])

            print("-- E. tools/list over HTTP is FastMCP's registry, unchanged by the transport --")
            r, msg = _rpc(http, T_ALEX, "tools/list", {}, sid=sid, rid=3, host=door)
            wire = ((msg or {}).get("result") or {}).get("tools") or []
            registry = asyncio.run(real.list_tools())
            want = [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in registry]

            def by_name(items):
                return {t["name"]: t for t in items}
            check("tools/list answered", r.status_code == 200 and len(wire) > 0,
                  f"{r.status_code} {len(wire)}")
            check("the same tool names", sorted(by_name(wire)) == sorted(by_name(want)),
                  f"{sorted(set(by_name(wire)) ^ set(by_name(want)))}")
            diff = [n for n in by_name(want)
                    if {k: v for k, v in by_name(wire).get(n, {}).items() if k != "_meta"}
                    != {k: v for k, v in by_name(want)[n].items() if k != "_meta"}]
            check("each tool's description and schemas are the registry's, byte for byte", not diff,
                  str(diff[:5]))
    finally:
        caller_auth.API_TRANSPORT = None


def main_() -> None:
    section_a()
    sections_b_to_e()
    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main_()
