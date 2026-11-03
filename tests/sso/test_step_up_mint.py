"""An SSO-only person creates their own agent credential (install smoke, OSS-26 B; TASK-1415).

Runs after the SSO leg (test_oidc_login.py), on the same stack, with atlas-api recreated on one
more line of .env: ATLAS_OIDC_STEP_UP=on (deploy/oss/.env.example's commented value). The person
is JIT-provisioned by SSO, so they have NO Atlas password, which is the case that used to end in
403 password_incorrect. Each step is the real flow through the ingress:

  * the credential screen's data says `reauth: sso`; a mint without a step-up is refused
    (403 step_up_required);
  * a step-up whose ID token carries a STALE `auth_time` (an hour old) is refused, and still
    no credential can be minted;
  * /auth/oidc/step-up?purpose=claude-code answers a page naming what is approved ("You are
    creating an agent credential named sso-dave-claude-code for ..."), whose link is the IdP's
    authorization request with prompt=login and max_age; the IdP's login form is posted with a
    fresh `auth_time`; Atlas's callback answers a page saying the same again, with no new
    session; the mint answers 201 with an `atlas_svc_` token, shown once;
  * that token works: /api/me as the agent, and an MCP session through the ingress
    (initialize, tools/list, one atlas_get).

The mock IdP signs whatever claims are posted beside the username, which is how the smoke
chooses `auth_time`; a real IdP sets it from the sign-in it just performed.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import time
from urllib.parse import parse_qs, urlsplit

import httpx

import atlas_smoke as smoke
from test_oidc_login import CALLBACK, ISSUER, SESSION_COOKIE, Browser

REQUIRED = "atlas-users"
PERSON = "sso-dave"
NEXT = "/board?stepup=1"
AGENT = f"{PERSON}-claude-code"
APPROVAL = f"You are creating an agent credential named <strong>{AGENT}</strong> for <strong>"
CONTINUE = re.compile(r'id="stepUpContinue" href="([^"]+)"')


def _say(msg: str) -> None:
    print(msg, flush=True)


def _csrf(b: Browser) -> str:
    r = b.me()
    assert r.status_code == 200, f"/api/me: {r.status_code}"
    return r.json()["csrf_token"]


def _mint(b: Browser, purpose: str) -> httpx.Response:
    return b.atlas.post("/api/me/agent-credential", headers={"Origin": smoke.URL}, json={
        "purpose": purpose, "scopes": ["read"], "expires_days": 30, "csrf_token": _csrf(b)})


def _step_up(b: Browser, auth_time: int) -> httpx.Response:
    """Atlas's step-up start, the IdP's login form with `auth_time`, Atlas's callback."""
    r = b.atlas.get("/auth/oidc/step-up", params={"purpose": "claude-code", "next": NEXT})
    assert r.status_code == 200, f"/auth/oidc/step-up: {r.status_code} {r.text[:200]}"
    assert APPROVAL in r.text, f"the start page does not name what is approved: {r.text[:300]}"
    m = CONTINUE.search(r.text)
    assert m, "the start page has no link to the IdP"
    auth_url = html.unescape(m.group(1))
    assert auth_url.startswith(ISSUER + "/authorize?"), f"redirected to {auth_url[:80]}"
    q = {k: v[0] for k, v in parse_qs(urlsplit(auth_url).query).items()}
    assert q.get("prompt") == "login", f"prompt={q.get('prompt')}"
    assert q.get("max_age", "").isdigit(), f"max_age={q.get('max_age')}"
    assert q.get("code_challenge_method") == "S256" and q.get("nonce") and q.get("state")
    assert q.get("redirect_uri") == CALLBACK, q.get("redirect_uri")

    r = b.idp.get(auth_url)
    assert r.status_code == 200 and 'name="username"' in r.text, f"IdP login page: {r.status_code}"
    claims = {"groups": [REQUIRED], "name": "SSO Dave", "email": f"{PERSON}@idp.test",
              "auth_time": auth_time}
    r = b.idp.post(auth_url, data={"username": PERSON, "claims": json.dumps(claims)})
    assert r.status_code in (302, 303), f"IdP login submit: {r.status_code} {r.text[:200]}"
    cb = r.headers["location"]
    assert cb.startswith(CALLBACK + "?"), f"IdP redirected to {cb[:80]}"
    _say(f"  step-up: prompt=login max_age={q['max_age']}, auth_time {int(time.time()) - auth_time} s old")
    return b.atlas.get(cb[len(smoke.URL):])


def test_sso_person_mints_an_agent_token_after_a_fresh_idp_sign_in():
    b = Browser()
    r = b.sso_login(PERSON, [REQUIRED])
    assert r.status_code == 303 and r.headers.get("location") == "/board", (
        f"SSO login: {r.status_code} -> {r.headers.get('location')}")
    me = b.me().json()
    assert me["kind"] == "session" and me["username"] == PERSON
    listing = b.atlas.get("/api/me/agent-credentials").json()
    _say(f"{PERSON} (SSO only, no Atlas password): credential screen says reauth="
         f"{listing.get('reauth')} via {listing.get('step_up_url')}, "
         f"window {listing.get('step_up_max_age')} s")
    assert listing.get("reauth") == "sso" and listing.get("step_up_url") == "/auth/oidc/step-up"

    r = _mint(b, "claude-code")
    assert r.status_code == 403 and r.json().get("error") == "step_up_required", (
        f"mint without a step-up: {r.status_code} {r.text[:200]}")
    _say("mint without a step-up: 403 step_up_required")

    # A STALE auth_time: an hour old, so older than the window and than the flow.
    r = _step_up(b, int(time.time()) - 3600)
    assert r.status_code == 403, f"stale step-up callback: {r.status_code} {r.text[:200]}"
    assert SESSION_COOKIE not in [c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")]
    r = _mint(b, "claude-code")
    assert r.status_code == 403 and r.json().get("error") == "step_up_required", (
        f"mint after a stale step-up: {r.status_code} {r.text[:200]}")
    _say("step-up with an auth_time an hour old: callback 403, and the mint is still refused")

    # A FRESH one.
    r = _step_up(b, int(time.time()))
    assert r.status_code == 200 and APPROVAL in r.text, (
        f"fresh step-up callback: {r.status_code} {r.text[:300]}")
    assert SESSION_COOKIE not in [c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")]
    left = b.atlas.get("/api/me/agent-credentials").json().get("step_up_seconds_left", 0)
    assert left > 0, f"no fresh step-up recorded ({left})"
    r = _mint(b, "claude-code")
    assert r.status_code == 201, f"mint after a fresh step-up: {r.status_code} {r.text[:300]}"
    body = r.json()
    token = body["token"]
    smoke.mask(token)
    assert token.startswith("atlas_svc_") and body["agent"] == AGENT
    _say(f"start page and callback page both say 'creating an agent credential named {AGENT}'; "
         f"no new session; mint 201, agent {body['agent']}, "
         f"scopes {body['scopes']} (the token itself is not printed)")

    r = httpx.get(smoke.URL + "/api/me", headers={"Authorization": "Bearer " + token},
                  verify=smoke.ssl_ctx(), timeout=30)
    assert r.status_code == 200 and r.json().get("username") == f"{PERSON}-claude-code", (
        f"/api/me with the new token: {r.status_code}")
    out = asyncio.run(asyncio.wait_for(
        smoke._mcp(token, [("atlas_get", {"id": "atlas-system"})]), smoke.MCP_SESSION_TIMEOUT_S))
    got = out["results"][0]
    _say(f"the new token: /api/me 200 as {PERSON}-claude-code; MCP tools/list -> "
         f"{len(out['tools'])} tools; atlas_get atlas-system ok={got.get('ok')}")
    assert "atlas_get" in out["tools"]
    assert got.get("ok") is True, f"atlas_get atlas-system: {json.dumps(got)[:300]}"

    r = _mint(b, "second")
    assert r.status_code == 403 and r.json().get("error") == "step_up_required", (
        f"a second mint on one step-up: {r.status_code}")
    _say("a second mint on the same step-up: 403 step_up_required (single use)")
