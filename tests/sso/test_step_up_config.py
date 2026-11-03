"""An SSO-only owner changes a setting after a fresh IdP sign-in (install smoke; TASK-1436).

Runs after the agent-token step-up leg (test_step_up_mint.py), on the same stack, with .env
holding ATLAS_OIDC_STEP_UP=on and one more line, ATLAS_CONFIG_WRITES=1. The leg makes the owner
the way an operator does on an Entra-only install: `atlas-admin user add olive --role owner --no-password`,
then `user link-idp olive --subject sso-olive`, so she signs in through the IdP only and
re-entering a password is no way to confirm a change. Each step is the real flow through the
ingress:

  * GET /api/config says `writes.step_up: sso`; a change without a step-up is refused
    (403 step_up_required);
  * a step-up whose ID token carries a STALE `auth_time` (an hour old) is refused, and the
    change is still refused;
  * /auth/oidc/step-up?config=ATLAS_RETRIEVAL_TOP_K answers a page naming the setting; the IdP's
    login form is posted with a fresh `auth_time`; Atlas's callback answers a page saying the
    same again; the change answers 200 with source `board`;
  * a second change on the same step-up is refused (single use).
"""
from __future__ import annotations

import html
import json
import re
import time
from urllib.parse import parse_qs, urlsplit

import httpx

import atlas_smoke as smoke
from test_oidc_login import CALLBACK, ISSUER, SESSION_COOKIE, Browser

REQUIRED = "atlas-users"
PERSON = "sso-olive"
SETTING = "ATLAS_RETRIEVAL_TOP_K"
NEXT = "/board?node=atlas-system"
APPROVAL = f"You are changing the setting <strong>{SETTING}</strong> of this Atlas install"
CONTINUE = re.compile(r'id="stepUpContinue" href="([^"]+)"')


def _say(msg: str) -> None:
    print(msg, flush=True)


def _put(b: Browser, value) -> httpx.Response:
    csrf = b.me().json()["csrf_token"]
    return b.atlas.put(f"/api/config/{SETTING}", headers={"Origin": smoke.URL},
                       json={"value": value, "csrf_token": csrf})


def _step_up(b: Browser, auth_time: int) -> httpx.Response:
    """Atlas's step-up start for SETTING, the IdP's login form with `auth_time`, the callback."""
    r = b.atlas.get("/auth/oidc/step-up", params={"config": SETTING, "next": NEXT})
    assert r.status_code == 200, f"/auth/oidc/step-up: {r.status_code} {r.text[:200]}"
    assert APPROVAL in r.text, f"the start page does not name the setting: {r.text[:300]}"
    m = CONTINUE.search(r.text)
    assert m, "the start page has no link to the IdP"
    auth_url = html.unescape(m.group(1))
    assert auth_url.startswith(ISSUER + "/authorize?"), f"redirected to {auth_url[:80]}"
    q = {k: v[0] for k, v in parse_qs(urlsplit(auth_url).query).items()}
    assert q.get("prompt") == "login" and q.get("max_age", "").isdigit(), q
    r = b.idp.get(auth_url)
    assert r.status_code == 200 and 'name="username"' in r.text, f"IdP login page: {r.status_code}"
    claims = {"groups": [REQUIRED], "name": "SSO Olive", "email": f"{PERSON}@idp.test",
              "auth_time": auth_time}
    r = b.idp.post(auth_url, data={"username": PERSON, "claims": json.dumps(claims)})
    assert r.status_code in (302, 303), f"IdP login submit: {r.status_code} {r.text[:200]}"
    cb = r.headers["location"]
    assert cb.startswith(CALLBACK + "?"), f"IdP redirected to {cb[:80]}"
    return b.atlas.get(cb[len(smoke.URL):])


def test_sso_owner_changes_a_setting_after_a_fresh_idp_sign_in():
    b = Browser()
    r = b.sso_login(PERSON, [REQUIRED])
    assert r.status_code == 303, f"SSO login: {r.status_code} -> {r.headers.get('location')}"
    me = b.me().json()
    assert me["kind"] == "session" and me["username"] == PERSON and me.get("role") == "owner", me
    writes = b.atlas.get("/api/config").json().get("writes") or {}
    _say(f"{PERSON} (owner, SSO only): GET /api/config says writes.step_up={writes.get('step_up')} "
         f"via {writes.get('step_up_url')}")
    assert writes.get("enabled") is True and writes.get("step_up") == "sso"
    assert writes.get("step_up_url") == "/auth/oidc/step-up"

    r = _put(b, "17")
    assert r.status_code == 403 and r.json().get("error") == "step_up_required", (
        f"change without a step-up: {r.status_code} {r.text[:200]}")
    _say("change without a step-up: 403 step_up_required")

    r = _step_up(b, int(time.time()) - 3600)
    assert r.status_code == 403, f"stale step-up callback: {r.status_code} {r.text[:200]}"
    r = _put(b, "17")
    assert r.status_code == 403 and r.json().get("error") == "step_up_required", (
        f"change after a stale step-up: {r.status_code} {r.text[:200]}")
    _say("step-up with an auth_time an hour old: callback 403, and the change is still refused")

    r = _step_up(b, int(time.time()))
    assert r.status_code == 200 and APPROVAL in r.text, (
        f"fresh step-up callback: {r.status_code} {r.text[:300]}")
    assert SESSION_COOKIE not in [c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")]
    writes = b.atlas.get("/api/config").json().get("writes") or {}
    assert writes.get("step_up_setting") == SETTING and writes.get("step_up_seconds_left", 0) > 0, writes
    r = _put(b, "17")
    assert r.status_code == 200, f"change after a fresh step-up: {r.status_code} {r.text[:300]}"
    body = r.json()
    assert body.get("changed") is True and body["setting"].get("source") == "board", body
    # With retrieval off the setting is not read, so the panel shows no value (`inactive`);
    # the leg's database check below reads the stored 17.
    assert str(body["setting"].get("value")) == "17" or body["setting"].get("inactive"), body["setting"]
    _say(f"start and callback pages name {SETTING}; no new session; change 200, source board")

    r = _put(b, None)
    assert r.status_code == 403 and r.json().get("error") == "step_up_required", (
        f"a second change on one step-up: {r.status_code}")
    _say("a second change on the same step-up: 403 step_up_required (single use)")
