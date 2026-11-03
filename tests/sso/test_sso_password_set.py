"""An SSO sign-in is not held by a pending password change; a personal password is set after a
fresh IdP sign-in (install smoke; TASK-1442).

Runs after legs 9c/9d, on the same stack, with .env holding ATLAS_OIDC_STEP_UP=on. The leg makes
the account the way an operator does: `atlas-admin user add pella --role member` (WITH a
temporary password, so must_change_password is set), then `user link-idp pella --subject
sso-pella`. Each step is the real flow through the ingress:

  * the SSO login lands on /board, not on /auth/password/change; /api/me is 200 with
    `password_setup`; /board is 200;
  * POST /api/me/password without a step-up is refused (403 step_up_required);
  * a step-up whose ID token carries a STALE `auth_time` is refused, and the set still is;
  * /auth/oidc/step-up?password=1 answers a page saying what is approved; the IdP's login form is
    posted with a fresh `auth_time`; the callback says it again; the set answers 200, and
    /api/me no longer carries `password_setup`;
  * a second set is refused (400 password_not_pending).
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
PERSON = "sso-pella"
NEW_PW = "Copper-Kettle-Morning-Fjord-93!"
NEXT = "/board?setpw=1"
APPROVAL = "You are setting a personal Atlas password for"
CONTINUE = re.compile(r'id="stepUpContinue" href="([^"]+)"')


def _say(msg: str) -> None:
    print(msg, flush=True)


def _set(b: Browser, pw: str = NEW_PW) -> httpx.Response:
    csrf = b.me().json()["csrf_token"]
    return b.atlas.post("/api/me/password", headers={"Origin": smoke.URL},
                        json={"new_password": pw, "confirm_password": pw, "csrf_token": csrf})


def _step_up(b: Browser, auth_time: int) -> httpx.Response:
    """Atlas's step-up start for a password, the IdP's login form with `auth_time`, the callback."""
    r = b.atlas.get("/auth/oidc/step-up", params={"password": "1", "next": NEXT})
    assert r.status_code == 200, f"/auth/oidc/step-up: {r.status_code} {r.text[:200]}"
    assert APPROVAL in r.text, f"the start page does not say what is approved: {r.text[:300]}"
    m = CONTINUE.search(r.text)
    assert m, "the start page has no link to the IdP"
    auth_url = html.unescape(m.group(1))
    assert auth_url.startswith(ISSUER + "/authorize?"), f"redirected to {auth_url[:80]}"
    q = {k: v[0] for k, v in parse_qs(urlsplit(auth_url).query).items()}
    assert q.get("prompt") == "login" and q.get("max_age", "").isdigit(), q
    r = b.idp.get(auth_url)
    assert r.status_code == 200 and 'name="username"' in r.text, f"IdP login page: {r.status_code}"
    claims = {"groups": [REQUIRED], "name": "SSO Pella", "email": f"{PERSON}@idp.test",
              "auth_time": auth_time}
    r = b.idp.post(auth_url, data={"username": PERSON, "claims": json.dumps(claims)})
    assert r.status_code in (302, 303), f"IdP login submit: {r.status_code} {r.text[:200]}"
    cb = r.headers["location"]
    assert cb.startswith(CALLBACK + "?"), f"IdP redirected to {cb[:80]}"
    return b.atlas.get(cb[len(smoke.URL):])


def test_sso_sign_in_is_not_held_by_a_pending_password_change():
    b = Browser()
    r = b.sso_login(PERSON, [REQUIRED])
    assert r.status_code == 303, f"SSO login: {r.status_code} -> {r.headers.get('location')}"
    assert r.headers.get("location") != "/auth/password/change", r.headers.get("location")
    me = b.me()
    assert me.status_code == 200, f"/api/me: {me.status_code} {me.text[:200]}"
    body = me.json()
    assert body["username"] == PERSON and body["must_change_password"] is False, body
    setup = body.get("password_setup") or {}
    assert setup.get("step_up") == "sso" and setup.get("step_up_url") == "/auth/oidc/step-up", body
    board = b.atlas.get("/board", headers={"Accept": "text/html"})
    assert board.status_code == 200, f"/board: {board.status_code} -> {board.headers.get('location')}"
    _say(f"{PERSON} (user add with a temporary password, then link-idp): SSO login lands on "
         f"{r.headers.get('location')}; /api/me 200 with password_setup.step_up=sso; /board 200")

    r = _set(b)
    assert r.status_code == 403 and r.json().get("error") == "step_up_required", (
        f"set without a step-up: {r.status_code} {r.text[:200]}")
    _say("POST /api/me/password without a step-up: 403 step_up_required")

    r = _step_up(b, int(time.time()) - 3600)
    assert r.status_code == 403, f"stale step-up callback: {r.status_code} {r.text[:200]}"
    r = _set(b)
    assert r.status_code == 403 and r.json().get("error") == "step_up_required", (
        f"set after a stale step-up: {r.status_code} {r.text[:200]}")
    _say("step-up with an auth_time an hour old: callback 403, and the set is still refused")

    r = _step_up(b, int(time.time()))
    assert r.status_code == 200 and APPROVAL in r.text, (
        f"fresh step-up callback: {r.status_code} {r.text[:300]}")
    assert SESSION_COOKIE not in [c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")]
    setup = b.me().json().get("password_setup") or {}
    assert setup.get("step_up_seconds_left", 0) > 0, setup
    r = _set(b)
    assert r.status_code == 200 and r.json().get("changed") is True, (
        f"set after a fresh step-up: {r.status_code} {r.text[:300]}")
    body = b.me().json()
    assert "password_setup" not in body and body["must_change_password"] is False, body
    _say("start and callback pages say what is approved; no new session; set 200; "
         "/api/me has no password_setup any more")

    r = _set(b)
    assert r.status_code == 400 and r.json().get("error") == "password_not_pending", (
        f"a second set: {r.status_code} {r.text[:200]}")
    _say("a second set: 400 password_not_pending")
