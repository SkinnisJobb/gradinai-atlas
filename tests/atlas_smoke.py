"""Shared helpers for the install smoke (STORY-oss-17, .github/workflows/install-smoke.yml).

Everything here talks to a LIVE OSS install through its one door, the TLS ingress, and trusts
exactly the install CA that scripts/init-certs.sh made (never the system store, never an
unverified connection). Configuration comes from the environment the workflow sets:

  SMOKE_URL   https://<ATLAS_PUBLIC_HOST>        the public URL of the install
  SMOKE_CA    deploy/oss/secrets/tls/ca.crt       the install CA
  SMOKE_DIR   a 0700 directory of the job's own generated credentials, one file each:
                <user>.password   the password set at the first sign-in (bootstrap_users.py)
                <user>.add        `atlas-admin user add` output (holds the temp password)
                <user>.token      the agent token `atlas-admin token mint --out` minted
  SMOKE_GATEWAY_URL  https://<ATLAS_PUBLIC_HOST>:<ATLAS_GATEWAY_PORT>/mcp, the gateway profile's
                     door (tests/mcp_contract/test_gateway.py only)

A credential is read from its file when it is needed and never printed, logged or put in an
exception message. `mask()` registers each value with the Actions log masker as a second line
of defence.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
from pathlib import Path
from typing import Any

import httpx

URL = os.environ.get("SMOKE_URL", "https://atlas.test").rstrip("/")
CA = os.environ.get("SMOKE_CA", "deploy/oss/secrets/tls/ca.crt")
DIR = Path(os.environ.get("SMOKE_DIR", "smoke"))
MCP_URL = URL + "/mcp/"
GATEWAY_URL = os.environ.get("SMOKE_GATEWAY_URL", "")

#: The two people of the smoke, and their agents (`token mint --user <user> --purpose agent`: <user>-agent).
OWNER, MEMBER = "alice", "bob"
SHARED_PROJECT = "smoke-shared"     # both bound: alice owner, bob editor
PRIVATE_PROJECT = "smoke-private-a"  # alice's; bob has no role until the isolation leg binds him


def ssl_ctx() -> ssl.SSLContext:
    """The install CA and nothing else."""
    return ssl.create_default_context(cafile=CA)


def mask(value: str) -> None:
    """Register `value` with the Actions log masker. The workflow already masks every
    generated value at line start in the step that creates it; this is the second line of
    defence. Written to the ORIGINAL stdout file descriptor with a leading newline: a workflow
    command is honoured only at the start of a line, and under `pytest -s` the current line
    may already hold a test id, in which case the command would be printed instead of obeyed."""
    if os.environ.get("GITHUB_ACTIONS") == "true" and value:
        os.write(1, ("\n::add-mask::" + value + "\n").encode())


def secret(name: str) -> str:
    """One credential file from SMOKE_DIR, stripped. Never printed."""
    value = (DIR / name).read_text(encoding="utf-8").strip()
    mask(value)
    return value


def token(user: str) -> str:
    return secret(f"{user}.token")


# --------------------------------------------------------------------------- #
# Browser-shaped sessions (the real login form, cookies, CSRF)
# --------------------------------------------------------------------------- #
_CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


def _csrf_of(html: str) -> str:
    m = _CSRF.search(html)
    if not m:
        raise AssertionError("no csrf_token in the page")
    return m.group(1)


class Session:
    """A signed-in person, as a browser would be: the session cookie, its CSRF token, and the
    Origin header the gate checks on every write."""

    def __init__(self) -> None:
        self.http = httpx.Client(base_url=URL, verify=ssl_ctx(), follow_redirects=False, timeout=30)
        self.csrf = ""
        self.me: dict = {}

    def login(self, username: str, password: str) -> httpx.Response:
        r = self.http.get("/auth/login")
        assert r.status_code == 200, f"GET /auth/login: {r.status_code}"
        return self.http.post("/auth/login", headers={"Origin": URL}, data={
            "username": username, "password": password,
            "csrf_token": _csrf_of(r.text), "next": "/board"})

    def change_password(self, current: str, new: str) -> httpx.Response:
        r = self.http.get("/auth/password/change")
        assert r.status_code == 200, f"GET /auth/password/change: {r.status_code}"
        return self.http.post("/auth/password/change", headers={"Origin": URL}, data={
            "current_password": current, "new_password": new, "confirm_password": new,
            "csrf_token": _csrf_of(r.text)})

    def load_me(self) -> dict:
        r = self.http.get("/api/me")
        assert r.status_code == 200, f"GET /api/me: {r.status_code}"
        self.me = r.json()
        self.csrf = self.me.get("csrf_token") or ""
        return self.me

    def write(self, method: str, path: str, body: dict) -> httpx.Response:
        return self.http.request(method, path, headers={"Origin": URL},
                                 json={**body, "csrf_token": self.csrf})

    def get(self, path: str, **params: Any) -> httpx.Response:
        return self.http.get(path, params=params or None)


def signed_in(user: str) -> Session:
    """A session for `user` with the password bootstrap_users.py set."""
    s = Session()
    r = s.login(user, secret(f"{user}.password"))
    assert r.status_code == 303 and r.headers.get("location") == "/board", (
        f"login as {user}: {r.status_code} -> {r.headers.get('location')}")
    s.load_me()
    return s


# --------------------------------------------------------------------------- #
# MCP, with the Python `mcp` client, through the ingress
# --------------------------------------------------------------------------- #
def _tool_payload(result: Any) -> dict:
    """The atlas-mcp envelope ({"ok": ..., "result"|"error": ...}) out of a CallToolResult."""
    sc = getattr(result, "structuredContent", None)
    if isinstance(sc, dict):
        if "ok" in sc:
            return sc
        inner = sc.get("result")
        if isinstance(inner, dict) and "ok" in inner:
            return inner
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            try:
                parsed = json.loads(text)
            except ValueError:
                continue
            if isinstance(parsed, dict) and "ok" in parsed:
                return parsed
    raise AssertionError(f"no atlas-mcp envelope in the tool result: {result!r}"[:500])


#: Seconds any one MCP request may take, and a whole session. Without them a lost response
#: hangs the step until the job's own timeout (seen once in CI, in the attribution test).
MCP_REQUEST_TIMEOUT_S = 60
MCP_SESSION_TIMEOUT_S = 180


def _say(msg: str) -> None:
    print(msg, flush=True)


async def _mcp(bearer: str, calls: list[tuple[str, dict]], url: str = MCP_URL,
               raw: bool = False) -> dict:
    from datetime import timedelta

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    http = httpx.AsyncClient(headers={"Authorization": "Bearer " + bearer}, verify=ssl_ctx(),
                             timeout=httpx.Timeout(30, read=120))
    out: dict = {"tools": [], "results": []}
    async with http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write,
                                     read_timeout_seconds=timedelta(seconds=MCP_REQUEST_TIMEOUT_S)) as session:
                _say("  mcp: initialize")
                await session.initialize()
                _say("  mcp: tools/list")
                listed = await session.list_tools()
                out["tools"] = sorted(t.name for t in listed.tools)
                for name, args in calls:
                    _say(f"  mcp: tools/call {name}")
                    if raw:
                        # The CallToolResult itself, or the protocol error that refused it.
                        try:
                            out["results"].append(await session.call_tool(name, args))
                        except Exception as exc:  # noqa: BLE001 - McpError and friends
                            out["results"].append(exc)
                    else:
                        out["results"].append(_tool_payload(await session.call_tool(name, args)))
                _say("  mcp: closing the session")
    return out


def mcp(user: str, *calls: tuple[str, dict], url: str = MCP_URL, raw: bool = False) -> dict:
    """Open an MCP session as `user`'s agent at `url` (the ingress's /mcp/ by default), list the
    tools, make `calls` in order. Returns {"tools": [names], "results": [envelope per call]};
    with raw=True each result is the CallToolResult, or the exception that refused the call."""
    return asyncio.run(asyncio.wait_for(_mcp(token(user), list(calls), url, raw), MCP_SESSION_TIMEOUT_S))


# --------------------------------------------------------------------------- #
# The world the legs share
# --------------------------------------------------------------------------- #
def project_role_bound_events(owner: Session, pid: str) -> list[dict]:
    """The `project_role_bound` auth_event lines for project `pid`, from the owner's audit export
    (`GET /api/audit/export?kinds=auth_event`, the only read route over auth_event)."""
    r = owner.get("/api/audit/export", kinds="auth_event")
    assert r.status_code == 200, f"GET /api/audit/export: {r.status_code} {r.text[:200]}"
    lines = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    return [e for e in lines if e.get("event") == "project_role_bound"
            and (e.get("detail") or "").startswith(f"project={pid} ")]


def ensure_project(owner: Session, pid: str, name: str, bindings: list[tuple[str, str]], *,
                   prove_creation: bool = True) -> None:
    """Create project `pid` as the instance owner and set its role bindings directly.

    With the work install's settings (ATLAS_RBAC=projects, private by default,
    ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER=false), on a fresh create this proves two rules:

      * ADR 0051: the creator owns a new project root. Right after the create the owner reads
        it (200), holds exactly one binding on it, `owner`, and exactly one `project_role_bound`
        auth_event records it.
      * TASK-1367 (ADR 0041 §8): the instance owner administers a project it holds NO role on.
        To get there honestly the creator's own binding is removed first (PUT with an empty
        list), which makes the content 404 to them; GET /api/access/projects still lists it
        without its content, and the PUT below still accepts it.

    The final PUT replaces the whole binding list, so a caller that wants the creator to keep
    seeing the project lists it itself (every caller does). No node grant is involved.

    `prove_creation=False` skips both proofs and only creates and binds: the upgrade leg seeds
    the PREVIOUS release with this checkout's tests, and that release may predate ADR 0051.
    """
    r = owner.write("POST", "/api/node", {"id": pid, "type": "project", "name": name,
                                          "summary": "install smoke project " + pid})
    assert r.status_code in (201, 409), f"create {pid}: {r.status_code} {r.text[:200]}"
    if r.status_code == 201 and prove_creation:
        me = owner.get("/api/me").json()["user_id"]
        r = owner.get(f"/api/node/{pid}")
        assert r.status_code == 200, f"the creator cannot read the project it just made: {r.status_code}"
        r = owner.get("/api/access/projects")
        assert r.status_code == 200, f"GET /api/access/projects: {r.status_code}"
        listed = next((p for p in r.json()["projects"] if p["id"] == pid), None)
        assert listed is not None, f"{pid} is not listed for the instance owner"
        want = [{"principal_kind": "user", "principal_id": me, "role": "owner"}]
        assert listed.get("bindings") == want, f"the creator's binding on {pid}: {listed.get('bindings')}"
        events = project_role_bound_events(owner, pid)
        assert len(events) == 1 and events[0].get("user_id") == me \
            and events[0].get("detail") == f"project={pid} user={me} role=owner", events
        print(f"{pid}: its creator reads it (200), holds `owner`, one project_role_bound event")

        r = owner.write("PUT", f"/api/access/project/{pid}/bindings", {"bindings": []})
        assert r.status_code == 200, f"unbind the creator on {pid}: {r.status_code} {r.text[:300]}"
        r = owner.get(f"/api/node/{pid}")
        assert r.status_code == 404, f"{pid} is still visible with no role on it: {r.status_code}"
        r = owner.get("/api/access/projects")
        assert r.status_code == 200, f"GET /api/access/projects: {r.status_code}"
        listed = next((p for p in r.json()["projects"] if p["id"] == pid), None)
        assert listed is not None, f"{pid} is not listed for the instance owner once unbound"
        assert "summary" not in listed and "install smoke project" not in json.dumps(listed), listed
        print(f"{pid}: with the creator unbound, hidden (404) and listed for binding without content")
    r = owner.write("PUT", f"/api/access/project/{pid}/bindings", {"bindings": [
        {"principal_kind": "user", "principal_id": uid, "role": role} for uid, role in bindings]})
    assert r.status_code == 200, f"bind {pid}: {r.status_code} {r.text[:300]}"


def user_id(owner: Session, username: str) -> str:
    r = owner.get("/api/access/projects")
    assert r.status_code == 200, f"GET /api/access/projects: {r.status_code}"
    for acct in r.json()["accounts"]:
        if acct["username"] == username:
            return acct["user_id"]
    raise AssertionError(f"no account {username!r}")
