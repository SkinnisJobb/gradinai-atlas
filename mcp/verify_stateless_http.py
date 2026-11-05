"""Prove the opt-in stateless MCP transport, ATLAS_MCP_STATELESS_HTTP (TASK-1480, ARO 2.2).

What this script holds the change to:

  A. the setting: unset, empty or 0/false/no/off is off; 1/true/yes/on is on; anything
     else is refused, and the process will not start, naming the variable.
  B. off (the default), in process: FastMCP and the session manager main.py runs are
     stateful, with the 4 h idle timeout, exactly as before the switch existed.
  C. on, TWO SEPARATE atlas-mcp PROCESSES (uvicorn, each on its own port) in front of one
     fake atlas-api: `initialize` and a tool call to each answer, with no session id
     issued or needed; a tool call to the instance that never saw an `initialize`
     answers too. Auth is unchanged: no token is a 401 on both, and each call reaches
     atlas-api with the caller's own token, two callers through the two instances.
  D. off, the same two processes: a session from instance 1 works on instance 1 and is
     refused (404) on instance 2. That is today's behaviour, and the reason for the switch.

No database and no real atlas-api: a small fake on loopback answers `/api/me` and
`/api/node/<id>` (the shape verify_transport_security.py uses). Loopback http is the one
non-https ATLAS_BASE_URL app/tls.py allows.

    pip install -r mcp/requirements.txt && python mcp/verify_stateless_http.py

Same convention as the other mcp/verify_*.py: one [PASS]/[FAIL] line per claim,
exit 1 if any failed.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("ATLAS_BASE_URL", "https://atlas.invalid")
os.environ.pop("ATLAS_TOKEN", None)
os.environ.pop("ATLAS_MCP_ALLOW_SHARED_TOKEN", None)
os.environ.pop("ATLAS_MCP_STATELESS_HTTP", None)
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
ENV = "ATLAS_MCP_STATELESS_HTTP"


# --------------------------------------------------------------------------- #
# A. The setting
# --------------------------------------------------------------------------- #
def _boot(value) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != ENV}
    if value is not None:
        env[ENV] = value
    code = "import app.settings as s; print('STATELESS', s.STATELESS_HTTP)"
    return subprocess.run([sys.executable, "-c", code], cwd=str(HERE), env=env,
                          capture_output=True, text=True, timeout=60)


def section_a() -> None:
    print("-- A. ATLAS_MCP_STATELESS_HTTP --")
    check("unset: off", settings.load_stateless_http({}) is False)
    check("the module-level value is off with nothing set", settings.STATELESS_HTTP is False)
    for raw in ("", "  ", "0", "false", "No", "OFF"):
        check(f"{raw!r}: off", settings.load_stateless_http({ENV: raw}) is False)
    for raw in ("1", "true", "Yes", " on "):
        check(f"{raw!r}: on", settings.load_stateless_http({ENV: raw}) is True)
    for raw in ("2", "stateless", "enabled"):
        try:
            settings.load_stateless_http({ENV: raw})
            raised = None
        except settings.StatelessHttpError as e:
            raised = e
        check(f"{raw!r}: refused, naming the variable", raised is not None and ENV in str(raised), repr(raised))
    r = _boot("maybe")
    check("a value that is not a switch: the process refuses to start, naming it",
          r.returncode != 0 and ENV in r.stderr, r.stderr[-200:])
    r = _boot("1")
    check("1: the process starts with it on", r.returncode == 0 and "STATELESS True" in r.stdout, r.stderr[-200:])


# --------------------------------------------------------------------------- #
# The wire (the same as verify_transport_security.py)
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


def _initialize(http, token):
    r, msg = _rpc(http, token, "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "verify_stateless_http", "version": "0"}})
    sid = r.headers.get("mcp-session-id")
    if r.status_code == 200:
        _rpc(http, token, "notifications/initialized", sid=sid, rid=None)
    return r, msg, sid


def _call(http, token, node_id, sid=None):
    r, msg = _rpc(http, token, "tools/call", {"name": "atlas_get", "arguments": {"id": node_id}},
                  sid=sid, rid=7)
    if msg is None:
        return r, None
    content = (msg.get("result") or {}).get("content") or [{}]
    try:
        return r, json.loads(content[0].get("text") or "null")
    except ValueError:
        return r, {"raw": content}


def _server_name(msg) -> str:
    return (((msg or {}).get("result") or {}).get("serverInfo") or {}).get("name")


def _read_by(out) -> str:
    return ((out or {}).get("result") or {}).get("read_by")


# --------------------------------------------------------------------------- #
# B. Off, in process
# --------------------------------------------------------------------------- #
def section_b() -> bool:
    print("-- B. off (the default): the stateful transport, as before --")
    try:
        from app import main
        from app import mcp_server
    except ImportError as e:
        check("the mcp package is installed (pip install -r mcp/requirements.txt)", False, str(e))
        return False
    real = mcp_server.mcp._real
    check("FastMCP's stateless_http is False", real.settings.stateless_http is False,
          str(real.settings.stateless_http))
    check("FastMCP keeps the 4 h idle timeout", real.settings.session_idle_timeout == FOUR_HOURS_S,
          str(real.settings.session_idle_timeout))
    manager = main.mcp.session_manager
    check("the session manager main.py runs is stateful", manager.stateless is False, str(manager.stateless))
    check("...with the 4 h idle timeout", manager.session_idle_timeout == FOUR_HOURS_S,
          str(manager.session_idle_timeout))
    return True


# --------------------------------------------------------------------------- #
# Two real processes against a fake atlas-api
# --------------------------------------------------------------------------- #
SEEN: list = []


class _FakeAPI(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _answer(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self) -> None:
        auth = self.headers.get("authorization") or ""
        token = auth[7:] if auth.lower().startswith("bearer ") else None
        path = self.path.split("?", 1)[0]
        SEEN.append((self.command, path, token))
        who = PEOPLE.get(token)
        if who is None:
            return self._answer(401, {"error": "unauthorized", "detail": "Authentication is required."})
        if path == "/api/me":
            return self._answer(200, {"username": who, "scopes": ["read", "write", "admin"]})
        if self.command == "GET" and path.startswith("/api/node/"):
            return self._answer(200, {"id": path.rsplit("/", 1)[-1], "read_by": who, "attributes": {}})
        return self._answer(200, {"by": who})

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _handle


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_instance(port: int, api_port: int, stateless: bool) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items()
           if k not in (ENV, "ATLAS_CA_BUNDLE", "ATLAS_MCP_CLIENT_CERT_FILE", "ATLAS_MCP_CLIENT_KEY_FILE")}
    env["ATLAS_BASE_URL"] = f"http://127.0.0.1:{api_port}"
    env["ATLAS_MCP_ALLOWED_HOSTS"] = "127.0.0.1:*"
    if stateless:
        env[ENV] = "1"
    code = ("import uvicorn; from app.main import app; "
            f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='warning')")
    return subprocess.Popen([sys.executable, "-c", code], cwd=str(HERE), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _wait_healthy(proc: subprocess.Popen, port: int) -> str:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return "exited: " + (proc.stdout.read() or "")[-400:]
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                return ""
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    return "no /health in 60 s"


class _Pair:
    """Two atlas-mcp processes, each on its own port, sharing the fake atlas-api."""

    def __init__(self, api_port: int, stateless: bool):
        self.ports = (_free_port(), _free_port())
        self.procs = [_start_instance(p, api_port, stateless) for p in self.ports]

    def __enter__(self):
        problems = [_wait_healthy(proc, port) for proc, port in zip(self.procs, self.ports)]
        check("both instances started", not any(problems), "; ".join(p for p in problems if p))
        self.clients = [httpx.Client(base_url=f"http://127.0.0.1:{p}", timeout=30) for p in self.ports]
        return self if not any(problems) else None

    def __exit__(self, *exc):
        for c in getattr(self, "clients", []):
            c.close()
        for proc in self.procs:
            proc.terminate()
        for proc in self.procs:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            if proc.stdout:
                proc.stdout.close()


def section_c(api_port: int) -> None:
    print("-- C. on: two separate instances, both answer --")
    with _Pair(api_port, stateless=True) as pair:
        if pair is None:
            return
        one, two = pair.clients
        for label, http in (("instance 1", one), ("instance 2", two)):
            r, msg, sid = _initialize(http, T_ALEX)
            check(f"{label}: initialize answers 200 as atlas",
                  r.status_code == 200 and _server_name(msg) == "atlas", f"{r.status_code} {r.text[:160]!r}")
            check(f"{label}: no session id is issued", not sid, str(sid))
            r, out = _call(http, T_ALEX, "n-" + label[-1])
            check(f"{label}: a tool call, with no session id, answers", r.status_code == 200
                  and _read_by(out) == "bot-alex", f"{r.status_code} {str(out)[:160]}")

        # The point of the switch: `initialize` on one, the work on the other.
        SEEN.clear()
        _initialize(one, T_SAM)
        r, out = _call(two, T_SAM, "cross")
        check("initialize on instance 1, tool call on instance 2: answers",
              r.status_code == 200 and _read_by(out) == "bot-sam", f"{r.status_code} {str(out)[:160]}")
        r, out = _call(one, T_ALEX, "back")
        check("...and the next call, by another caller, on instance 1: answers as that caller",
              r.status_code == 200 and _read_by(out) == "bot-alex", f"{r.status_code} {str(out)[:160]}")
        check("each call reached atlas-api with its caller's own token",
              ("GET", "/api/node/cross", T_SAM) in SEEN and ("GET", "/api/node/back", T_ALEX) in SEEN,
              str(SEEN[-6:]))
        check("no request reached atlas-api without a caller's token",
              all(tok in PEOPLE for _m, _p, tok in SEEN), str([s for s in SEEN if s[2] not in PEOPLE]))

        for label, http in (("instance 1", one), ("instance 2", two)):
            r, msg = _rpc(http, None, "tools/list", {})
            check(f"{label}: no token is still the gate's 401", r.status_code == 401, str(r.status_code))
            r, msg = _rpc(http, "atlas_svc_zzzzzz_" + "x" * 43, "tools/list", {})
            check(f"{label}: a token atlas-api refuses is still 401", r.status_code == 401, str(r.status_code))


def section_d(api_port: int) -> None:
    print("-- D. off: the same two instances keep per-process sessions, as today --")
    with _Pair(api_port, stateless=False) as pair:
        if pair is None:
            return
        one, two = pair.clients
        r, msg, sid = _initialize(one, T_ALEX)
        check("instance 1: initialize answers 200 with a session id",
              r.status_code == 200 and bool(sid) and _server_name(msg) == "atlas", f"{r.status_code} {sid}")
        r, out = _call(one, T_ALEX, "same", sid=sid)
        check("instance 1: a tool call on that session answers", r.status_code == 200
              and _read_by(out) == "bot-alex", f"{r.status_code} {str(out)[:160]}")
        r, out = _call(two, T_ALEX, "other", sid=sid)
        check("instance 2: the same session id is refused (404), as before the switch",
              r.status_code == 404, f"{r.status_code} {r.text[:160]!r}")
        r, out = _call(two, T_ALEX, "none")
        check("instance 2: a tool call with no session id is refused (400)",
              r.status_code == 400, f"{r.status_code} {r.text[:160]!r}")


def main_() -> None:
    section_a()
    if section_b():
        api = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAPI)
        threading.Thread(target=api.serve_forever, daemon=True).start()
        try:
            section_c(api.server_address[1])
            section_d(api.server_address[1])
        finally:
            api.shutdown()
            api.server_close()
    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main_()
