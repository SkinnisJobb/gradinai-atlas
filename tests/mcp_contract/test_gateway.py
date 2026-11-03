"""The gateway profile (STORY-oss-15), against the live OSS install (install smoke, gateway leg).

client --https--> atlas-gateway (AgentGateway) --https--> atlas-tls :9444 --> atlas-mcp

SMOKE_GATEWAY_URL is the gateway's door; SMOKE_GATEWAY_MODE says which rendering of the gateway
configuration is running, because the leg restarts it with three (see install-smoke.yml):

  default     deploy/gateway/gateway.yaml as shipped:
                * tools/list through the gateway is atlas-mcp's own list, names un-doubled
                  (`atlas_get`, never `atlas_atlas_get`);
                * a write with alice's token is attributed to alice's agent, bob's to bob's:
                  each caller's own Authorization header reaches atlas-mcp;
                * no token: refused, no session.
  restricted  the atlas server with `tools: [atlas_get, atlas_health]`: only those are listed,
              and a write tool is refused when called (the allow-list is enforced).
  wrong-ca    the atlas server verified against a file that is not the install CA: the gateway
              must refuse the upstream (no session), proving it verifies the hop.
  session-key as shipped, with ATLAS_GATEWAY_SESSION_KEY_FILE set (TASK-1460): the session ID
              the gateway hands out is AES-256-GCM sealed, not the base64 JSON it is in
              `default` (checked there too, so the check can tell the two apart), and a whole
              MCP session (initialize, tools/list, a tool call) works through it, which means
              the gateway decrypts its own session IDs with the operator's key.
"""
from __future__ import annotations

import base64
import json
import os
import secrets

import httpx
import pytest

import atlas_smoke as smoke

MODE = os.environ.get("SMOKE_GATEWAY_MODE", "default")
URL = smoke.GATEWAY_URL

pytestmark = pytest.mark.skipif(not URL, reason="SMOKE_GATEWAY_URL is not set (gateway leg only)")

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "install-smoke-gateway", "version": "1"}}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def only(mode: str):
    return pytest.mark.skipif(MODE != mode, reason=f"gateway mode {MODE}, this test is for {mode}")


@only("default")
@pytest.mark.parametrize("user", [smoke.OWNER, smoke.MEMBER])
def test_tools_list_through_the_gateway_is_unprefixed(user):
    via = smoke.mcp(user, url=URL)["tools"]
    direct = smoke.mcp(user)["tools"]
    print(f"{user}'s agent through the gateway: tools/list -> {len(via)} tools: {', '.join(via)}")
    assert "atlas_get" in via
    doubled = [t for t in via if t.startswith("atlas_atlas_")]
    assert not doubled, f"target prefix applied on top of atlas-mcp's own: {doubled}"
    assert via == direct, f"through the gateway {sorted(set(via) ^ set(direct))} differ from /mcp/"


@only("default")
def test_writes_through_the_gateway_are_attributed_per_user(owner, ids, shared_project):
    for user in (smoke.OWNER, smoke.MEMBER):
        mark = f"written by {user}'s agent through the gateway, nonce {secrets.token_hex(4)}"
        res = smoke.mcp(user, ("atlas_node_write", {"id": shared_project, "summary": mark}), url=URL)["results"][0]
        assert res["ok"], f"{user}'s write through the gateway: {res}"

        r = owner.get("/api/events", entity_id=shared_project, action="node.update", page_size=20)
        assert r.status_code == 200, r.text[:300]
        ev = r.json()["items"][0]  # newest first: the write just made
        responsible = (ev.get("responsible") or {}).get("username")
        print(f"through the gateway: /api/events id={ev['id']} action={ev['action']} "
              f"entity={ev['entity_id']} actor={ev['actor']['subject']} "
              f"token={ev['actor']['token_id']} responsible={responsible}")
        assert ev["actor"]["subject"] == f"{user}-agent", ev["actor"]
        assert ev["responsible_user_id"] == ids[user]
        assert responsible == user
        r = owner.get(f"/api/node/{shared_project}")
        assert r.status_code == 200 and r.json().get("summary") == mark, "the write did not land"


@only("default")
@pytest.mark.parametrize("auth", [None, "Bearer atlas_svc_" + "0" * 32],
                         ids=["no-token", "unknown-token"])
def test_gateway_opens_no_session_without_a_valid_token(auth):
    headers = dict(HEADERS)
    if auth:
        headers["Authorization"] = auth
    r = httpx.post(URL, json=INIT, headers=headers, verify=smoke.ssl_ctx(), timeout=30)
    body = r.text
    print(f"POST gateway /mcp ({'Bearer <redacted>' if auth else 'no Authorization'}) -> "
          f"{r.status_code} {body[:160]!r}")
    assert r.status_code >= 400 or '"error"' in body, "the gateway opened a session for a caller atlas-mcp refuses"
    assert "serverInfo" not in body


@only("restricted")
def test_allow_list_is_enforced(owner, shared_project):
    out = smoke.mcp(smoke.OWNER, ("atlas_get", {"id": shared_project}), url=URL, raw=True)
    print(f"restricted gateway: tools/list -> {out['tools']}")
    assert out["tools"] == ["atlas_get", "atlas_health"]
    allowed = out["results"][0]
    assert not isinstance(allowed, Exception) and not getattr(allowed, "isError", False), allowed
    print("restricted gateway: atlas_get -> ok")

    # A tool outside the allow-list. AgentGateway v1.6.0 answers the call itself with HTTP 400,
    # which the Python client raises as a transport error for the whole session; either way the
    # call must not reach atlas-mcp, so the summary must not change.
    mark = f"must be refused by the gateway, nonce {secrets.token_hex(4)}"
    try:
        out = smoke.mcp(smoke.OWNER, ("atlas_node_write", {"id": shared_project, "summary": mark}),
                        url=URL, raw=True)
        refused = out["results"][0]
    except BaseException as exc:  # noqa: BLE001 - the session failing IS the refusal
        refused = exc
    print(f"restricted gateway: atlas_node_write -> {type(refused).__name__}: {str(refused)[:160]}")
    assert isinstance(refused, BaseException) or getattr(refused, "isError", False), \
        f"a tool outside the allow-list was executed: {refused!r}"[:300]
    r = owner.get(f"/api/node/{shared_project}")
    assert r.status_code == 200 and r.json().get("summary") != mark, "the refused write landed"
    print("restricted gateway: the node is unchanged (the call never reached atlas-mcp)")


@only("wrong-ca")
def test_upstream_certificate_is_verified():
    with pytest.raises(BaseException) as info:  # noqa: B017 - any failure to open the session
        smoke.mcp(smoke.OWNER, url=URL)
    print(f"gateway verifying atlas-mcp against a file that is not its CA: no session "
          f"({type(info.value).__name__}: {str(info.value)[:160]})")


# --------------------------------------------------------------------------- #
# MCP session-ID encryption (TASK-1460). AgentGateway v1.7.0 hands out a session ID that is
# base64url(JSON {"t": "mcp", ...}) with `config.session.key: disabled`, and
# base64(nonce || AES-256-GCM ciphertext+tag) with a key. Neither the ID nor its decoded
# content is printed: the plain form carries atlas-mcp's own session ID.
# --------------------------------------------------------------------------- #
def _session_id() -> str:
    headers = dict(HEADERS, Authorization=f"Bearer {smoke.token(smoke.OWNER)}")
    r = httpx.post(URL, json=INIT, headers=headers, verify=smoke.ssl_ctx(), timeout=30)
    assert r.status_code == 200, f"initialize through the gateway: {r.status_code} {r.text[:160]!r}"
    sid = r.headers.get("mcp-session-id", "")
    assert sid, "the gateway answered initialize without an Mcp-Session-Id"
    return sid


def _plain_state(sid: str):
    """The JSON state a key-less gateway encodes, or None when `sid` is not that."""
    try:
        state = json.loads(base64.urlsafe_b64decode(sid + "=" * (-len(sid) % 4)))
    except (ValueError, UnicodeDecodeError):
        return None
    return state if isinstance(state, dict) else None


@only("default")
def test_session_id_is_plain_with_the_key_disabled():
    state = _plain_state(_session_id())
    print(f"default gateway: session ID decodes to JSON with t={state and state.get('t')!r}")
    assert state is not None and state.get("t") == "mcp", "with key: disabled the ID is base64 JSON"


@only("session-key")
def test_session_id_is_encrypted_with_the_operator_key():
    sid = _session_id()
    assert _plain_state(sid) is None, "with ATLAS_GATEWAY_SESSION_KEY_FILE set the ID is still base64 JSON"
    sealed = base64.b64decode(sid, validate=True)
    print(f"session-key gateway: session ID is {len(sid)} chars, {len(sealed)} sealed bytes, not JSON")
    assert len(sealed) >= 12 + 16 + 1, "shorter than a GCM nonce, tag and any plaintext"


@only("session-key")
def test_mcp_is_served_through_an_encrypted_session(shared_project):
    out = smoke.mcp(smoke.OWNER, ("atlas_get", {"id": shared_project}), url=URL, raw=True)
    print(f"session-key gateway: tools/list -> {len(out['tools'])} tools; atlas_get -> "
          f"{type(out['results'][0]).__name__}")
    assert "atlas_get" in out["tools"]
    res = out["results"][0]
    assert not isinstance(res, BaseException) and not getattr(res, "isError", False), repr(res)[:300]
