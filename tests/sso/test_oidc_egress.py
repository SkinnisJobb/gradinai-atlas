"""OIDC sign-in with the IdP reachable only through `egress` (install smoke, leg 9b; OSS-26 A).

Leg 9 proved the code flow with the IdP on a network it shares with atlas-api. A real IdP is
outside the node, and in the base layout atlas-api has no route out at all. Here the same
throwaway IdP (tests/sso/docker-compose.idp-egress.yml) sits on the stack's `egress` network
only, and the workflow runs these by test id around what it does to atlas-api:

  test_without_oidc_overlay_the_idp_is_unreachable  atlas-api with the OIDC settings and their
                                     secrets, but NOT the shipped deploy/oss/oidc.yml (the
                                     override INSTALL.md used to leave to the operator, minus its
                                     network): "Sign in with SSO" answers 503 "Single sign-on
                                     unavailable", no redirect to the IdP
  test_with_oidc_overlay_a_person_signs_in  atlas-api recreated with deploy/oss/oidc.yml, which
                                     joins it to `egress` and mounts the secrets: the full code
                                     flow + PKCE succeeds for a new person in the required group,
                                     and /api/me shows them

The workflow then checks auth_event for the oidc:discovery_unavailable refusal and the login.
"""
from __future__ import annotations

import atlas_smoke as smoke

from test_oidc_login import REQUIRED, SESSION_COOKIE, Browser, _set_cookies

PERSON = "sso-erin"


def test_without_oidc_overlay_the_idp_is_unreachable():
    b = Browser()
    r = b.atlas.get("/auth/oidc/start", params={"next": "/board"})
    print(f"/auth/oidc/start without oidc.yml: {r.status_code} location={r.headers.get('location')!r}")
    assert r.status_code == 503, f"expected 503 (IdP unreachable), got {r.status_code}: {r.text[:200]}"
    assert "Single sign-on unavailable" in r.text
    assert "location" not in r.headers
    print("UNREACHABLE MET: no route to the IdP without oidc.yml -> 503 'Single sign-on unavailable'")


def test_with_oidc_overlay_a_person_signs_in():
    b = Browser()
    r = b.sso_login(PERSON, [REQUIRED])
    assert r.status_code == 303 and r.headers.get("location") == "/board", (
        f"callback: {r.status_code} -> {r.headers.get('location')}")
    assert SESSION_COOKIE in _set_cookies(r), "no session cookie after a good callback"
    me = b.me()
    assert me.status_code == 200, f"/api/me: {me.status_code}"
    body = me.json()
    print(f"{PERSON} through egress: callback 303 -> /board, /api/me kind={body['kind']} "
          f"username={body['username']} role={body['role']}")
    assert body["kind"] == "session" and body["username"] == PERSON and body["role"] == "member"
    print(f"EGRESS MET: {PERSON} signed in at {smoke.URL} with the IdP reachable only through "
          f"egress (oidc.yml)")
