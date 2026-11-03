"""The Claude Code client pack against the live install (install smoke, the client-pack leg;
STORY-oss-19 criterion 3).

The workflow has followed clients/claude-code/README.md's three steps against this stack: an
operator minted alice's token with `atlas-admin token mint --out`, it was moved into
~/.config/atlas/token (a scratch HOME) without being printed, the helper was copied beside it,
and the template was pointed at the install. Here the template's OWN `headersHelper` command is
run the way Claude Code runs it (through the shell, from another directory, HOME set, nothing
else of this environment), and the header it prints is what every request below carries:

  * /api/me with it is alice's claude-code agent, accountable to alice;
  * an MCP session through the ingress with it: initialize, tools/list, and atlas_get of
    alice's PRIVATE project (leg 5's), which only an agent accountable to alice can read.

The header is held in memory only: never printed, never in argv or an environment variable.

Environment, beyond tests/atlas_smoke.py's: SMOKE_CLIENT_HOME (the scratch HOME) and
SMOKE_CLIENT_MCP_JSON (the atlas.mcp.json made from the template).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from datetime import timedelta

import httpx

import atlas_smoke as smoke

HOME = os.environ.get("SMOKE_CLIENT_HOME", "")
MCP_JSON = os.environ.get("SMOKE_CLIENT_MCP_JSON", "")


def _server() -> dict:
    with open(MCP_JSON, encoding="utf-8") as fh:
        return json.load(fh)["mcpServers"]["atlas"]


def _helper_headers() -> dict:
    """Run the template's headersHelper as Claude Code does; return the headers it printed."""
    cmd = _server()["headersHelper"]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": HOME}
    # Claude Code runs a headersHelper through /bin/sh on Linux; so does this, explicitly.
    r = subprocess.run(["/bin/sh", "-c", cmd], env=env, cwd=tempfile.gettempdir(),
                       capture_output=True, text=True, timeout=15)
    assert r.returncode == 0, f"headersHelper exited {r.returncode}: {r.stderr[:200]}"
    headers = json.loads(r.stdout)
    assert set(headers) == {"Authorization"}, f"helper printed headers {sorted(headers)}"
    value = headers["Authorization"]
    assert value.startswith("Bearer atlas_svc_"), "the helper's header is not a Bearer agent token"
    smoke.mask(value.split(" ", 1)[1])
    return headers


def test_template_points_at_the_install():
    srv = _server()
    print(f"atlas.mcp.json: type={srv['type']} url={srv['url']} headersHelper={srv['headersHelper']}")
    assert srv["type"] == "http" and srv["url"] == smoke.MCP_URL
    assert "atlas_svc_" not in json.dumps(srv), "a token in the MCP config"


def test_helper_header_is_alices_claude_code_agent():
    r = httpx.get(smoke.URL + "/api/me", headers=_helper_headers(), verify=smoke.ssl_ctx(), timeout=30)
    assert r.status_code == 200, f"/api/me with the helper's header: {r.status_code}"
    me = r.json()
    print(f"/api/me with the helper's header: kind={me['kind']} username={me['username']} "
          f"is_service={me['is_service']} scopes={','.join(me['scopes'])}")
    assert me["is_service"] is True and me["username"] == f"{smoke.OWNER}-claude-code"
    assert set(me["scopes"]) == {"read", "write"}


async def _session(headers: dict) -> tuple[list[str], dict]:  # (tool names, atlas_get envelope)
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    http = httpx.AsyncClient(headers=headers, verify=smoke.ssl_ctx(),
                             timeout=httpx.Timeout(30, read=120))
    async with http:
        async with streamable_http_client(smoke.MCP_URL, http_client=http) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(
                    seconds=smoke.MCP_REQUEST_TIMEOUT_S)) as session:
                await session.initialize()
                tools = sorted(t.name for t in (await session.list_tools()).tools)
                got = smoke._tool_payload(await session.call_tool(
                    "atlas_get", {"id": smoke.PRIVATE_PROJECT}))
    return tools, got


def test_helper_header_opens_an_mcp_session():
    tools, got = asyncio.run(asyncio.wait_for(_session(_helper_headers()),
                                              smoke.MCP_SESSION_TIMEOUT_S))
    print(f"MCP through the ingress with the helper's header: tools/list -> {len(tools)} tools; "
          f"atlas_get {smoke.PRIVATE_PROJECT} (alice's private project) ok={got.get('ok')}")
    for name in ("atlas_get", "atlas_node_write", "atlas_file_work"):
        assert name in tools
    assert got.get("ok") is True, f"atlas_get {smoke.PRIVATE_PROJECT}: {json.dumps(got)[:300]}"
