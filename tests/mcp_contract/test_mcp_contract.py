"""MCP contract, against the live OSS install through its TLS ingress (install smoke, leg 4).

  * /mcp/ without a token, or with one the API does not accept, is a 401 with
    `WWW-Authenticate: Bearer`, before any tool runs.
  * each person's own agent token opens a session and lists the tools.
  * a write by alice's agent is attributed to alice's agent, accountable alice, in /api/events;
    a write by bob's agent to bob's agent, accountable bob. One door, two identities.
"""
from __future__ import annotations

import secrets

import httpx
import pytest

import atlas_smoke as smoke

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "install-smoke", "version": "1"}}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.mark.parametrize("auth", [None, "Bearer atlas_svc_" + "0" * 32, "Bearer not-a-token"],
                         ids=["no-token", "unknown-token", "malformed-token"])
def test_mcp_refuses_without_a_valid_token(auth):
    headers = dict(HEADERS)
    if auth:
        headers["Authorization"] = auth
    r = httpx.post(smoke.MCP_URL, json=INIT, headers=headers, verify=smoke.ssl_ctx(), timeout=30)
    print(f"POST /mcp/ ({auth and auth.split()[0] + ' <redacted>' or 'no Authorization'}) -> "
          f"{r.status_code} WWW-Authenticate={r.headers.get('www-authenticate')!r}")
    assert r.status_code == 401
    assert (r.headers.get("www-authenticate") or "").lower().startswith("bearer")


def test_api_refuses_without_a_token():
    r = httpx.get(smoke.URL + "/api/me", verify=smoke.ssl_ctx(), timeout=30)
    print(f"GET /api/me without a token -> {r.status_code}")
    assert r.status_code in (302, 303, 401)
    assert r.status_code == 401 or "/auth/login" in (r.headers.get("location") or "")


@pytest.mark.parametrize("user", [smoke.OWNER, smoke.MEMBER])
def test_each_agent_token_lists_the_tools(user):
    out = smoke.mcp(user)
    print(f"{user}'s agent: tools/list -> {len(out['tools'])} tools: {', '.join(out['tools'])}")
    for name in ("atlas_get", "atlas_node_write", "atlas_node_search", "atlas_file_work"):
        assert name in out["tools"]


def test_writes_are_attributed_per_user(owner, ids, shared_project):
    marks = {}
    for user in (smoke.OWNER, smoke.MEMBER):
        marks[user] = f"written by {user}'s agent, nonce {secrets.token_hex(4)}"
        out = smoke.mcp(user, ("atlas_node_write", {"id": shared_project, "summary": marks[user]}))
        res = out["results"][0]
        assert res["ok"], f"{user}'s write: {res}"

    r = owner.get("/api/events", entity_id=shared_project, action="node.update", page_size=20)
    assert r.status_code == 200, r.text[:300]
    events = r.json()["items"]
    seen = {}
    for ev in events:  # newest first
        actor = ev["actor"]
        agent = (actor.get("account") or {}).get("username") or actor.get("subject")
        if agent in (f"{smoke.OWNER}-agent", f"{smoke.MEMBER}-agent") and agent not in seen:
            seen[agent] = ev
    for user in (smoke.OWNER, smoke.MEMBER):
        ev = seen.get(f"{user}-agent")
        assert ev is not None, f"no node.update by {user}-agent in /api/events: {events}"
        responsible = (ev.get("responsible") or {}).get("username")
        print(f"/api/events id={ev['id']} action={ev['action']} entity={ev['entity_id']} "
              f"actor.kind={ev['actor']['kind']} actor={ev['actor']['subject']} "
              f"token={ev['actor']['token_id']} responsible={responsible}")
        assert ev["actor"]["subject"] == f"{user}-agent"
        assert ev["actor"]["token_id"], "the event names the token that wrote it"
        assert ev["responsible_user_id"] == ids[user]
        assert responsible == user
    assert seen[f"{smoke.OWNER}-agent"]["actor"]["token_id"] != seen[f"{smoke.MEMBER}-agent"]["actor"]["token_id"]
