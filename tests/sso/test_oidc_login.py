"""OIDC single sign-on on the live OSS install (install smoke, the SSO leg; STORY-oss-13).

The stack is the one every earlier leg used, with atlas-api recreated on the work install's
sign-in settings as deploy/oss/.env.example writes them (ATLAS_AUTH_PROVIDERS=oidc,
ATLAS_LOCAL_LOGIN=break-glass, a required group, a group map, a 12 h session) and a throwaway
OpenID provider beside it (tests/sso/docker-compose.idp.yml). Each login here is the REAL
authorization-code flow, driven the way a browser follows it: Atlas's /auth/oidc/start answers
303 to the IdP's authorization endpoint; the IdP's login form is posted; the IdP answers 302 to
Atlas's callback; Atlas redeems the code (with the PKCE verifier and the client credential) at
the IdP's token endpoint itself, over TLS verified against the IdP's CA, and checks the ID token.

  * the login page offers "Sign in with SSO" and no password form; the unlinked
    /auth/login?break_glass=1 has the form;
  * a person in the required group signs in: a session, and /api/me shows them as a `member`
    whose username is their IdP subject (JIT-provisioned at this first login);
  * a callback replayed is refused (the state is single-use);
  * a person in no required group is refused, with no session;
  * the same person who signed in, once out of the group, is refused at their next login
    (required groups are enforced at EVERY login, not only the first);
  * `atlas-admin user link-idp`: bob, a local person, signs in by SSO onto HIS account (same
    id); his agent's token is suspended from the link until that first SSO login, then works;
  * local login as configured: alice's password is refused (not break-glass); the
    break-glass owner's temporary password is accepted, into the forced change.

The workflow then reads the database for what HTTP cannot show (the session's 12 h lifetime,
auth_login_flow, the IdP group memberships, the auth_event reasons).

Environment, beyond tests/atlas_smoke.py's: SMOKE_IDP_CA (the IdP's CA certificate) and
SMOKE_IDP_ISSUER (https://idp.test:8446/atlas). No IdP credential exists: the mock IdP signs in
whoever is named on its form, with the claims posted beside the name, which is what lets one
container stand in for a directory.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

import atlas_smoke as smoke

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap_users  # noqa: E402  (temp_password: the parser of `user add` output)

IDP_CA = os.environ.get("SMOKE_IDP_CA", "")
ISSUER = os.environ.get("SMOKE_IDP_ISSUER", "https://idp.test:8446/atlas").rstrip("/")
CALLBACK = smoke.URL + "/auth/oidc/callback"
SESSION_COOKIE = "__Host-atlas_session"

REQUIRED = "atlas-users"          # ATLAS_OIDC_REQUIRED_GROUPS
MAINTAINERS = "atlas-maintainers"  # mapped, not required
OTHER = "contractors"             # neither mapped nor required


class Browser:
    """One browser: a cookie jar for Atlas, the install CA for Atlas, the IdP's CA for the IdP.
    Redirects are followed by hand, so each hop is asserted."""

    def __init__(self) -> None:
        self.atlas = httpx.Client(base_url=smoke.URL, verify=smoke.ssl_ctx(),
                                  follow_redirects=False, timeout=30)
        self.idp = httpx.Client(verify=ssl.create_default_context(cafile=IDP_CA),
                                follow_redirects=False, timeout=30)
        self.last_callback = ""

    def sso_login(self, username: str, groups: list[str]) -> httpx.Response:
        """Start at Atlas, sign in at the IdP as `username` with `groups`, land on the callback.
        Returns Atlas's answer to the callback."""
        r = self.atlas.get("/auth/oidc/start", params={"next": "/board"})
        assert r.status_code == 303, f"/auth/oidc/start: {r.status_code} {r.text[:200]}"
        auth_url = r.headers["location"]
        assert auth_url.startswith(ISSUER + "/authorize?"), f"redirected to {auth_url[:80]}"
        q = {k: v[0] for k, v in parse_qs(urlsplit(auth_url).query).items()}
        assert q.get("response_type") == "code", q.get("response_type")
        assert q.get("code_challenge_method") == "S256", q.get("code_challenge_method")
        assert q.get("redirect_uri") == CALLBACK, q.get("redirect_uri")
        assert "openid" in q.get("scope", "").split(), q.get("scope")
        for p in ("state", "nonce", "code_challenge", "client_id"):
            assert q.get(p), f"the authorization request has no {p}"

        # The IdP's login page, then its form (method=post, no action: the same URL).
        r = self.idp.get(auth_url)
        assert r.status_code == 200 and 'name="username"' in r.text, (
            f"IdP login page: {r.status_code} {r.text[:200]}")
        claims = {"groups": groups, "name": f"SSO {username}", "email": f"{username}@idp.test"}
        r = self.idp.post(auth_url, data={"username": username, "claims": json.dumps(claims)})
        assert r.status_code in (302, 303), f"IdP login submit: {r.status_code} {r.text[:200]}"
        cb = r.headers["location"]
        assert cb.startswith(CALLBACK + "?"), f"IdP redirected to {cb[:80]}"
        cq = {k: v[0] for k, v in parse_qs(urlsplit(cb).query).items()}
        assert cq.get("state") == q["state"] and cq.get("code"), "callback without code/state"
        self.last_callback = cb

        return self.atlas.get(cb[len(smoke.URL):])

    def me(self) -> httpx.Response:
        return self.atlas.get("/api/me")


def _set_cookies(r: httpx.Response) -> list[str]:
    return [c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")]


def _say(msg: str) -> None:
    print(msg, flush=True)


def _bearer_me(token_file: str) -> int:
    tok = smoke.secret(token_file)
    r = httpx.get(smoke.URL + "/api/me", headers={"Authorization": "Bearer " + tok},
                  verify=smoke.ssl_ctx(), timeout=30)
    return r.status_code


# --------------------------------------------------------------------------- #

def test_login_page_offers_sso_and_hides_the_password_form():
    b = Browser()
    r = b.atlas.get("/auth/login")
    assert r.status_code == 200
    assert 'href="/auth/oidc/start"' in r.text and "Sign in with SSO" in r.text
    assert 'name="password"' not in r.text, "the password form is shown with break-glass only"
    r = b.atlas.get("/auth/login", params={"break_glass": "1"})
    assert r.status_code == 200 and 'name="password"' in r.text
    _say("login page: 'Sign in with SSO', no password form; ?break_glass=1 has the form")


def test_member_of_the_required_group_signs_in():
    b = Browser()
    r = b.sso_login("sso-carol", [REQUIRED, MAINTAINERS, OTHER])
    assert r.status_code == 303 and r.headers.get("location") == "/board", (
        f"callback: {r.status_code} -> {r.headers.get('location')}")
    assert SESSION_COOKIE in _set_cookies(r), "no session cookie after a good callback"
    me = b.me()
    assert me.status_code == 200, f"/api/me: {me.status_code}"
    body = me.json()
    _say(f"sso-carol (groups {REQUIRED}, {MAINTAINERS}, {OTHER}): callback 303 -> /board, "
         f"/api/me: kind={body['kind']} username={body['username']} name={body['name']!r} "
         f"role={body['role']} must_change_password={body['must_change_password']}")
    assert body["kind"] == "session" and body["username"] == "sso-carol"
    assert body["role"] == "member" and body["is_service"] is False
    assert body["must_change_password"] is False

    # The same callback again: the flow was consumed at first sight.
    r = b.atlas.get(b.last_callback[len(smoke.URL):])
    assert r.status_code == 303 and r.headers.get("location", "").startswith("/auth/login?error=1")
    assert SESSION_COOKIE not in _set_cookies(r)
    _say("the same callback replayed: 303 -> /auth/login?error=1, no session (state single-use)")


def test_outside_the_required_group_is_refused():
    b = Browser()
    r = b.sso_login("sso-mallory", [OTHER])
    assert r.status_code == 303 and r.headers.get("location", "").startswith("/auth/login?error=1"), (
        f"callback: {r.status_code} -> {r.headers.get('location')}")
    assert SESSION_COOKIE not in _set_cookies(r)
    me = b.me()
    assert me.status_code in (302, 303, 401), f"/api/me after a refused login: {me.status_code}"
    _say(f"sso-mallory (groups {OTHER}): callback 303 -> /auth/login?error=1, no session, "
         f"/api/me {me.status_code}")


def test_leaving_the_group_refuses_the_next_login():
    b = Browser()
    r = b.sso_login("sso-carol", [OTHER])
    assert r.status_code == 303 and r.headers.get("location", "").startswith("/auth/login?error=1"), (
        f"callback: {r.status_code} -> {r.headers.get('location')}")
    assert SESSION_COOKIE not in _set_cookies(r)
    _say(f"sso-carol, no longer in {REQUIRED}: next login refused (303 -> /auth/login?error=1)")


def test_link_idp_keeps_the_account_and_resumes_its_agent():
    # The workflow ran `atlas-admin user link-idp bob --subject sso-bob` before this leg and
    # wrote bob's account id to SMOKE_DIR/bob.id.
    bob_id = (smoke.DIR / "bob.id").read_text(encoding="utf-8").strip()
    before = _bearer_me("bob.token")
    assert before == 401, f"bob's agent token right after link-idp: {before} (must be suspended)"
    b = Browser()
    r = b.sso_login("sso-bob", [REQUIRED])
    assert r.status_code == 303 and r.headers.get("location") == "/board", (
        f"callback: {r.status_code} -> {r.headers.get('location')}")
    body = b.me().json()
    assert body["user_id"] == bob_id, "the SSO login did not land on bob's own account"
    assert body["username"] == "sso-bob"
    after = _bearer_me("bob.token")
    assert after == 200, f"bob's agent token after his first SSO login: {after}"
    _say(f"link-idp bob -> oidc:sso-bob: agent token {before} until the first SSO login; "
         f"SSO lands on the same account (user_id {bob_id[:8]}...); agent token then {after}")


def _break_glass_login(username: str, password: str) -> httpx.Response:
    """The password form exists only at the unlinked /auth/login?break_glass=1 now; whoever
    posts it, the server decides whether the account may use a password."""
    s = smoke.Session()
    page = s.http.get("/auth/login", params={"break_glass": "1"})
    assert page.status_code == 200
    return s.http.post("/auth/login", headers={"Origin": smoke.URL}, data={
        "username": username, "password": password,
        "csrf_token": smoke._csrf_of(page.text), "next": "/board"})


def test_local_login_is_break_glass_only():
    r = _break_glass_login(smoke.OWNER, smoke.secret(f"{smoke.OWNER}.password"))
    assert r.status_code == 303 and r.headers.get("location", "").startswith("/auth/login?error=1"), (
        f"alice's password login: {r.status_code} -> {r.headers.get('location')}")
    assert SESSION_COOKIE not in _set_cookies(r)
    _say("alice (local owner, not break-glass), her correct password on the break-glass form: "
         "refused (303 -> /auth/login?error=1)")

    r = _break_glass_login("breakglass", bootstrap_users.temp_password("breakglass"))
    assert r.status_code == 303 and r.headers.get("location") == "/auth/password/change", (
        f"break-glass login: {r.status_code} -> {r.headers.get('location')}")
    assert SESSION_COOKIE in _set_cookies(r)
    _say("breakglass (owner --break-glass) at /auth/login?break_glass=1: temp password accepted, "
         "303 -> /auth/password/change")
