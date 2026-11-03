"""
Verification for OIDC login (STORY-oss-13, ADR 0041 §5-§7), against a STUB IdP with
no network: `oidc_testkit.StubIdP` answers discovery, JWKS and the token endpoint
through an httpx MockTransport, with an RSA key generated at test time.

The app runs as the work install would: ATLAS_AUTH_PROVIDERS=local,oidc,
ATLAS_LOCAL_LOGIN=break-glass, a required group and a group map.

  A. THE CONFIGURATION SHOWS: two public routes beyond the frozen set, the route
     table is routes.snapshot.txt plus exactly those two, the login page offers
     "Sign in with SSO" and no password form, and the unlinked ?break_glass=1 has it.
  B. CODE + PKCE LOGIN CREATES A SESSION: the authorization request carries
     response_type=code, S256, state, nonce, client_id, redirect_uri, openid; the
     token request's verifier hashes to that challenge; the callback rotates into a
     session /api/me accepts; the account is (oidc, sub), a `member` with no
     password; the session is bounded by ATLAS_OIDC_MAX_SESSION_HOURS; and no IdP
     token is stored anywhere in the auth tables.
  C. EVERY BROKEN PROOF IS REFUSED, each with no session and a `login_fail` naming
     why: missing, unknown, replayed and cross-browser state; wrong and missing
     nonce; a wrong PKCE verifier; wrong audience, issuer, azp; expired; another
     key; alg none; HS256; unknown kid; an IdP error; the token endpoint down;
     discovery down (a 503 page, no redirect).
  D. PRESETS: generic, Keycloak and Entra map subject, name, email and groups;
     Entra keys on tid+oid with the tenant pinned; overage and malformed groups
     claims fail closed; a claim override applies.
  E. JIT PROVISIONING only for a member of ATLAS_OIDC_REQUIRED_GROUPS, and a known
     user who has left the required group is refused at their next login.
  F. BREAK-GLASS: a normal local user's password login fails; a break-glass owner's
     succeeds with a distinct `login_break_glass` auth_event; `--break-glass` needs
     --role owner.
  G. OFFBOARDING REACHES AGENTS: an IdP-backed person's agent token (minted with
     `atlas-admin token mint --user oidc:<sub>`) works, gets 401 once their last
     OIDC login is older than ATLAS_OIDC_REVALIDATE_DAYS, and works again after
     they log in; a local person's agent is untouched with no side-table row.
  H. `atlas-admin user link-idp`: a local person keeps their id and agents, the
     agent is suspended until the first SSO login, then the SSO login lands on the
     SAME account and the agent works; a taken subject is refused.

Run:  python verify_oidc_login.py
"""
from __future__ import annotations

import base64
import contextlib
import io
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:  # pragma: no cover - never a skip
    print(f"FATAL: {e}")
    sys.exit(1)

TEST_ORIGIN = "https://testserver"
PW = "Correct-Horse-Battery-Oidc-Login-5!"
TMP = tempfile.mkdtemp(prefix="atlas-oidc-login-")

for _k in [k for k in os.environ if k.startswith(("ATLAS_AUTH_PROVIDERS", "ATLAS_LOCAL_LOGIN",
                                                    "ATLAS_OIDC_"))]:
    os.environ.pop(_k)
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
# §A compares the route table with routes.snapshot.txt, so this script pins the DEFAULT
# feature set (as verify_routes.py does) whatever the caller set (STORY-oss-08).
os.environ.pop("ATLAS_DISABLED_FEATURES", None)
os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", TEST_ORIGIN)

import oidc_testkit  # noqa: E402

STUB = oidc_testkit.StubIdP()
GROUP_MAP = oidc_testkit.write_group_map(TMP, {"atlas-users": "users", "/team-a": "team-a"})
os.environ.update(STUB.env(
    TMP, ATLAS_LOCAL_LOGIN="break-glass", ATLAS_OIDC_REQUIRED_GROUPS="atlas-users",
    ATLAS_OIDC_GROUP_MAP_FILE=GROUP_MAP, ATLAS_OIDC_MAX_SESSION_HOURS="8"))

import identity.oidc  # noqa: E402

identity.oidc.use_transport(STUB.transport)

import jwt  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

import admin  # noqa: E402
import app  # noqa: E402
import auth  # noqa: E402
import models_auth  # noqa: E402
import passwords  # noqa: E402
import settings  # noqa: E402
from identity import LoginRefused  # noqa: E402
from models_auth import AuthEvent, AuthSession, AuthUser, AuthUserIdpState  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


FROZEN_PUBLIC_ROUTES = frozenset({
    "/", "/healthz", "/readyz", "/auth/login", "/auth/password/change",
    "/auth/denied", "/auth/privacy", "/favicon.ico"})
GROUPS_OK = ["atlas-users", "/team-a", "some-unmapped-group"]


def client():
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN)


def reset_limits() -> None:
    for ip in ("testclient", "127.0.0.1", ""):
        auth._IP_LIMITER.reset(auth._login_key(ip))
        auth._IP_LIMITER.reset(auth._bearer_key(ip))


def start(c):
    r = c.get("/auth/oidc/start", follow_redirects=False)
    return r, r.headers.get("location", "")


def sso_login(claims: dict, c=None, **kw):
    c = c or client()
    r, loc = start(c)
    cb = STUB.authorize(loc, claims, **kw)
    return c, c.get(cb, follow_redirects=False), cb


def signed_in(c) -> bool:
    return c.get("/api/me").status_code == 200


def last_fail_reason() -> str:
    with app.session() as s:
        row = s.scalars(select(AuthEvent).where(AuthEvent.event == "login_fail")
                        .order_by(AuthEvent.id.desc()).limit(1)).first()
        return row.reason if row is not None else ""


def run_admin(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = admin.main(list(argv))
        except SystemExit as exc:
            rc = int(exc.code or 0)
    return rc, out.getvalue(), err.getvalue()


def bearer_status(raw: str) -> int:
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN,
                      headers={"Authorization": "Bearer " + raw}).get("/api/me").status_code


def mk_local(username: str, *, role: str = "member") -> str:
    import uuid
    uid = str(uuid.uuid4())
    with app.session() as s:
        s.add(AuthUser(id=uid, subject=username, name=username.title(), role=role,
                       is_service=False, must_change_password=False,
                       password_hash=passwords.hash_password(PW),
                       created_at=datetime.now(timezone.utc)))
        s.commit()
    return uid


def password_login(username: str, password: str = PW, query: str = ""):
    c = client()
    page = c.get("/auth/login" + query)
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    r = c.post("/auth/login", data={"username": username, "password": password,
                                    "csrf_token": token},
               headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
    return c, r


def route_lines() -> list[str]:
    from route_table import live_routes, route_class
    out = []
    for r in live_routes(app.app):
        if getattr(r, "path", None) is None:
            continue
        for m in sorted(getattr(r, "methods", None) or ["*"]):
            out.append(f"{m} {r.path} {getattr(r, 'name', '')} {route_class(r)}")
    return sorted(out)


# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. the configuration shows, and only where it should --")
    check("providers local,oidc with ATLAS_LOCAL_LOGIN=break-glass",
          identity.SETTINGS.providers == ("local", "oidc") and identity.SETTINGS.break_glass_only)
    check("PUBLIC_ROUTES = the frozen set + /auth/oidc/start + /auth/oidc/callback",
          auth.PUBLIC_ROUTES == FROZEN_PUBLIC_ROUTES | {"/auth/oidc/start", "/auth/oidc/callback"},
          str(sorted(auth.PUBLIC_ROUTES - FROZEN_PUBLIC_ROUTES)))
    snapshot = sorted((HERE / "routes.snapshot.txt").read_text(encoding="utf-8").splitlines())
    snapshot = [ln for ln in snapshot if ln.strip()]
    extra = sorted(set(route_lines()) - set(snapshot))
    missing = sorted(set(snapshot) - set(route_lines()))
    check("the route table is routes.snapshot.txt plus exactly the two SSO routes",
          not missing and extra == ["GET /auth/oidc/callback sso_oidc_callback APIRoute",
                                    "GET /auth/oidc/start sso_oidc_start APIRoute"],
          f"extra={extra} missing={missing}")
    page = client().get("/auth/login")
    check("the login page offers \"Sign in with SSO\" and NO password form (break-glass)",
          'href="/auth/oidc/start"' in page.text and "Sign in with SSO" in page.text
          and 'action="/auth/login"' not in page.text)
    page = client().get("/auth/login?break_glass=1")
    check("the unlinked /auth/login?break_glass=1 carries the password form",
          'action="/auth/login"' in page.text and 'name="password"' in page.text)
    page = client().get("/auth/login?next=/board")
    check("the SSO link carries next", 'href="/auth/oidc/start?next=%2Fboard"' in page.text)


# --------------------------------------------------------------------------- #
def section_b() -> None:
    print("\n-- B. authorization code + PKCE creates a session --")
    reset_limits()
    c = client()
    r, loc = start(c)
    q = {k: v[0] for k, v in parse_qs(urlsplit(loc).query).items()}
    check("start: 303 to the discovered authorization endpoint", r.status_code == 303
          and loc.startswith(STUB.authorization_endpoint), f"{r.status_code} {loc[:80]}")
    check("the request: response_type=code, client_id, redirect_uri, scope with openid",
          q.get("response_type") == "code" and q.get("client_id") == STUB.client_id
          and q.get("redirect_uri") == STUB.redirect_uri and "openid" in q.get("scope", "").split())
    check("PKCE S256 with a 43-char challenge, and a state and a nonce of >= 32 chars",
          q.get("code_challenge_method") == "S256" and len(q.get("code_challenge", "")) == 43
          and len(q.get("state", "")) >= 32 and len(q.get("nonce", "")) >= 32)
    claims = {"sub": "kc-erin", "name": "Erin Example", "email": "erin@example.com",
              "preferred_username": "erin", "groups": GROUPS_OK}
    cb = STUB.authorize(loc, claims)
    r = c.get(cb, follow_redirects=False)
    sent = STUB.token_requests[-1] if STUB.token_requests else {}
    check("the token request carried the verifier whose S256 is the challenge",
          identity.oidc.pkce_challenge(sent.get("code_verifier", "")) == q.get("code_challenge")
          and sent.get("grant_type") == "authorization_code")
    me = c.get("/api/me")
    check("callback: 303 and a session /api/me accepts", r.status_code == 303
          and me.status_code == 200 and me.json().get("username") == "kc-erin",
          f"{r.status_code} {me.status_code} {last_fail_reason()}")
    check("the pre-session cookie is cleared and a session cookie set",
          auth.SESSION_COOKIE in r.headers.get("set-cookie", ""))
    with app.session() as s:
        u = s.scalar(select(AuthUser).where(AuthUser.idp == "oidc", AuthUser.subject == "kc-erin"))
        sess = s.scalar(select(AuthSession).where(AuthSession.user_id == u.id)) if u else None
        state = s.get(AuthUserIdpState, u.id) if u else None
        ok = s.scalars(select(AuthEvent).where(AuthEvent.event == "login_ok",
                                               AuthEvent.user_id == u.id)).all() if u else []
    check("JIT: (oidc, kc-erin), role member, no password, not forced to change one, name/email set",
          u is not None and u.role == "member" and u.password_hash is None
          and not u.must_change_password and u.name == "Erin Example" and u.email == "erin@example.com")
    life = (sess.absolute_expires_at - sess.created_at).total_seconds() if sess else -1
    check("the session's absolute lifetime is ATLAS_OIDC_MAX_SESSION_HOURS (8 h)",
          0 < life <= 8 * 3600 + 1, str(life))
    check("auth_user_idp_state.last_idp_login_at is stamped",
          state is not None and state.last_idp_login_at is not None and not state.break_glass)
    check("login_ok names the provider", any(e.reason == "provider=oidc" for e in ok))
    leaked = []
    with app.engine.connect() as conn:
        for table in list(models_auth.Base.metadata.tables) + ["auth_login_flow"]:
            for row in conn.execute(text("SELECT * FROM %s" % table)):
                blob = " ".join(str(v) for v in row)
                if "stub-access-" in blob or "stub-refresh-" in blob or "eyJ" in blob:
                    leaked.append(table)
    check("no IdP access, refresh or ID token is stored in any auth table", not leaked, str(leaked))
    with app.session() as s:
        flows_left = s.scalar(text("SELECT count(*) FROM auth_login_flow"))
    check("the consumed flow row is gone", flows_left == 0, str(flows_left))


# --------------------------------------------------------------------------- #
def _refused(label: str, c, r, reason_part: str) -> None:
    check(f"refused: {label} (no session; login_fail says {reason_part})",
          r.status_code == 303 and "error=1" in r.headers.get("location", "")
          and not signed_in(c) and reason_part in last_fail_reason(),
          f"{r.status_code} {r.headers.get('location')} {last_fail_reason()}")


def section_c() -> None:
    print("\n-- C. every broken proof is refused --")
    claims = {"sub": "kc-erin", "groups": GROUPS_OK}
    reset_limits()
    c = client()
    c.get("/auth/login")
    r = c.get("/auth/oidc/callback?code=abc", follow_redirects=False)
    _refused("no state at all", c, r, "oidc:state_missing")
    c, r, _cb = sso_login(claims, state_override="not-a-state-this-server-issued")
    _refused("an unknown state", c, r, "oidc:state_unknown")
    c, r, cb = sso_login(claims)
    check("(a good login, to replay)", r.status_code == 303 and signed_in(c))
    c2 = client()
    c2.get("/auth/login")
    r = c2.get(cb, follow_redirects=False)
    _refused("a replayed callback", c2, r, "oidc:state_unknown")
    c1 = client()
    _r, loc = start(c1)
    cb = STUB.authorize(loc, claims)
    c3 = client()
    c3.get("/auth/login")
    r = c3.get(cb, follow_redirects=False)
    _refused("a callback in another browser (other pre-session)", c3, r, "oidc:state_other_browser")
    c4 = client()
    _r, loc = start(c4)
    cb = STUB.authorize(loc, claims)
    c4.cookies.clear()
    r = c4.get(cb, follow_redirects=False)
    _refused("a callback with no pre-session cookie", c4, r, "oidc:state_other_browser")
    reset_limits()
    c, r, _ = sso_login(claims, id_token_overrides={"nonce": "attacker-chosen"})
    _refused("a wrong nonce", c, r, "oidc:nonce_mismatch")
    c, r, _ = sso_login(claims, drop_nonce=True)
    _refused("no nonce", c, r, "oidc:nonce_mismatch")
    c, r, _ = sso_login(claims, tamper_challenge=True)
    _refused("a wrong PKCE verifier (the IdP refuses the code)", c, r,
             "oidc:token_exchange_refused:invalid_grant")
    c, r, _ = sso_login(claims, id_token_overrides={"aud": "another-client"})
    _refused("a token for another audience", c, r, "oidc:id_token_audience")
    reset_limits()
    c, r, _ = sso_login(claims, id_token_overrides={"iss": "https://evil.test"})
    _refused("a token from another issuer", c, r, "oidc:id_token_issuer")
    c, r, _ = sso_login(claims, id_token_overrides={"aud": [STUB.client_id, "x"], "azp": "x"})
    _refused("azp naming another client", c, r, "oidc:id_token_azp")
    past = int(datetime.now(timezone.utc).timestamp()) - 3600
    c, r, _ = sso_login(claims, id_token_overrides={"exp": past, "iat": past - 300})
    _refused("an expired token", c, r, "oidc:id_token_expired")
    c, r, _ = sso_login(claims, key=oidc_testkit.new_rsa_key())
    _refused("a token signed by another key (same kid)", c, r, "oidc:id_token_invalid")
    reset_limits()
    unsigned = jwt.encode({"sub": "kc-erin"}, None, algorithm="none")
    c, r, _ = sso_login(claims, raw_id_token=unsigned)
    _refused("alg none", c, r, "oidc:id_token_alg_refused")
    hs = jwt.encode({"sub": "kc-erin", "iss": STUB.issuer, "aud": STUB.client_id},
                    STUB.client_secret, algorithm="HS256")
    c, r, _ = sso_login(claims, raw_id_token=hs)
    _refused("HS256 keyed with the client secret", c, r, "oidc:id_token_alg_refused")
    c, r, _ = sso_login(claims, kid="no-such-kid")
    _refused("an unknown kid", c, r, "oidc:jwks_no_matching_key")
    c = client()
    _r, loc = start(c)
    state = parse_qs(urlsplit(loc).query)["state"][0]
    r = c.get("/auth/oidc/callback?error=access_denied&state=" + state, follow_redirects=False)
    _refused("the IdP's own error", c, r, "oidc:idp_error:access_denied")
    reset_limits()
    STUB.refuse_token_endpoint = True
    try:
        c, r, _ = sso_login(claims)
        _refused("the token endpoint failing", c, r, "oidc:token_exchange_refused")
    finally:
        STUB.refuse_token_endpoint = False
    provider = identity.provider("oidc")
    provider._discovery = None
    STUB.discovery_up = False
    try:
        r, loc = start(client())
        check("discovery down: start answers 503 with a page, and redirects nowhere",
              r.status_code == 503 and not loc and "Single sign-on unavailable" in r.text,
              str(r.status_code))
    finally:
        STUB.discovery_up = True
    c, r, _ = sso_login(claims)
    check("...and once it is back, login works again", r.status_code == 303 and signed_in(c))


# --------------------------------------------------------------------------- #
def section_d() -> None:
    print("\n-- D. presets map subject, name, email and groups --")
    base = {"ATLAS_AUTH_PROVIDERS": "oidc", "ATLAS_OIDC_CLIENT_ID": "c",
            "ATLAS_OIDC_CLIENT_SECRET_FILE": "/x", "ATLAS_OIDC_REDIRECT_URI":
            "https://a.example/auth/oidc/callback"}
    tid = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    entra = settings.load_identity(dict(base, ATLAS_OIDC_PRESET="entra",
                                        ATLAS_OIDC_ISSUER="https://login.microsoftonline.com/%s/v2.0" % tid)).oidc
    kc = settings.load_identity(dict(base, ATLAS_OIDC_PRESET="keycloak",
                                     ATLAS_OIDC_ISSUER="https://kc.example/realms/a")).oidc
    gen = settings.load_identity(dict(base, ATLAS_OIDC_ISSUER="https://idp.example")).oidc
    m = identity.oidc.map_claims

    oid = "6A1B2C3D-0000-4000-8000-00000000ABCD"
    e = m(entra, {"sub": "pairwise-xyz", "tid": tid, "oid": oid, "name": "Ann Entra",
                  "preferred_username": "ann@corp.example", "groups": ["g-1", "g-2"]})
    check("entra: the subject is the oid (lowercased) with the tid pinned, not the pairwise sub",
          (e.idp, e.subject) == ("oidc", oid.lower()), e.subject)
    check("entra: name from `name`, email falls back to preferred_username, groups are ids",
          (e.name, e.email, e.groups) == ("Ann Entra", "ann@corp.example", ("g-1", "g-2")))
    e2 = m(entra, {"tid": tid, "oid": oid, "email": "ann@mail.example", "preferred_username": "upn"})
    check("entra: `email` wins over preferred_username when present", e2.email == "ann@mail.example")
    for label, claims, why in (
            ("another tenant's token", {"tid": "ffffffff-4b5a-6978-8796-a5b4c3d2e1f0", "oid": oid},
             "entra_tenant_mismatch"),
            ("no oid", {"tid": tid}, "entra_tid_oid_missing"),
            ("group overage (_claim_names)", {"tid": tid, "oid": oid,
                                              "_claim_names": {"groups": "src1"},
                                              "_claim_sources": {"src1": {"endpoint": "https://g"}}},
             "groups_overage"),
            ("group overage (hasgroups)", {"tid": tid, "oid": oid, "hasgroups": True}, "groups_overage")):
        try:
            m(entra, claims)
            check(f"entra refuses {label}", False, "accepted")
        except LoginRefused as exc:
            check(f"entra refuses {label} ({why}): fail closed", exc.reason == why, exc.reason)

    k = m(kc, {"sub": "f81d4fae-7dec-11d0-a765-00a0c91e6bf6", "preferred_username": "kim",
               "email": "kim@example.com", "groups": ["/team-a", "/team-a/sub"]})
    check("keycloak: sub, name falls back to preferred_username, email, group paths",
          (k.subject, k.name, k.email, k.groups) == ("f81d4fae-7dec-11d0-a765-00a0c91e6bf6", "kim",
                                                     "kim@example.com", ("/team-a", "/team-a/sub")))
    k2 = m(kc, {"sub": "s", "name": "Kim Keycloak", "preferred_username": "kim"})
    check("keycloak: `name` wins when present; no groups claim is no groups",
          k2.name == "Kim Keycloak" and k2.groups == ())
    g = m(gen, {"sub": "Case-Sensitive-Sub", "groups": "single"})
    check("generic: sub kept as is; a string groups claim is one group",
          g.subject == "Case-Sensitive-Sub" and g.groups == ("single",))
    roles = settings.load_identity(dict(base, ATLAS_OIDC_ISSUER="https://idp.example",
                                        ATLAS_OIDC_GROUPS_CLAIM="roles",
                                        ATLAS_OIDC_NAME_CLAIM="given_name")).oidc
    r = m(roles, {"sub": "s", "roles": ["r1"], "groups": ["ignored"], "given_name": "Gi",
                  "name": "ignored"})
    check("a claim override applies (groups from `roles`, name from `given_name`)",
          r.groups == ("r1",) and r.name == "Gi")
    for label, claims, why in (("a malformed groups claim", {"sub": "s", "groups": [1, 2]},
                                "groups_claim_malformed"),
                               ("no sub", {"name": "x"}, "subject_missing"),
                               ("a subject longer than 64", {"sub": "x" * 65}, "subject_unusable")):
        try:
            m(gen, claims)
            check(f"refused: {label}", False, "accepted")
        except LoginRefused as exc:
            check(f"refused: {label} ({why})", exc.reason == why, exc.reason)


# --------------------------------------------------------------------------- #
def section_e() -> None:
    print("\n-- E. JIT provisioning only inside ATLAS_OIDC_REQUIRED_GROUPS --")
    reset_limits()
    c, r, _ = sso_login({"sub": "kc-outsider", "groups": ["/team-a"]})
    with app.session() as s:
        made = s.scalar(select(AuthUser).where(AuthUser.subject == "kc-outsider"))
    _refused("a user in no required group", c, r, "oidc:not_in_required_group")
    check("...and no account was created for them", made is None)
    c, r, _ = sso_login({"sub": "kc-nogroups"})
    _refused("a user with no groups claim at all", c, r, "oidc:not_in_required_group")
    c, r, _ = sso_login({"sub": "kc-erin", "groups": ["/team-a"]})
    _refused("a KNOWN user who has left the required group", c, r, "oidc:not_in_required_group")


# --------------------------------------------------------------------------- #
def section_f() -> None:
    print("\n-- F. ATLAS_LOCAL_LOGIN=break-glass --")
    reset_limits()
    mk_local("plain-local")
    c, r = password_login("plain-local", query="?break_glass=1")
    check("a normal local user's password login fails (no session)",
          r.status_code == 303 and "error=1" in r.headers.get("location", "") and not signed_in(c))
    check("...recorded as login_fail reason not_break_glass", last_fail_reason() == "not_break_glass",
          last_fail_reason())
    rc, _o, err = run_admin("user", "add", "bg-member", "--role", "member", "--break-glass")
    check("`user add --break-glass` needs --role owner", rc == 1 and "owner" in err, err.strip())
    rc, out, _e = run_admin("user", "add", "bg-owner", "--role", "owner", "--break-glass")
    check("`user add bg-owner --role owner --break-glass` succeeds and says BREAK-GLASS",
          rc == 0 and "BREAK-GLASS" in out)
    with app.session() as s:
        u = s.scalar(select(AuthUser).where(AuthUser.idp == "local", AuthUser.subject == "bg-owner"))
        state_row = s.get(AuthUserIdpState, u.id)
        flag = state_row is not None and state_row.break_glass
        # The temp password printed above is a forced-change credential; set a known one
        # so the login below needs no parsing of the console block.
        u.password_hash = passwords.hash_password(PW)
        u.must_change_password = False
        u.temp_expires_at = None
        s.commit()
        uid = u.id
    check("the flag lives in auth_user_idp_state, not on auth_user", flag)
    reset_limits()
    c, r = password_login("bg-owner", query="?break_glass=1")
    with app.session() as s:
        bg = s.scalars(select(AuthEvent).where(AuthEvent.event == "login_break_glass",
                                               AuthEvent.user_id == uid)).all()
        ok = s.scalars(select(AuthEvent).where(AuthEvent.event == "login_ok",
                                               AuthEvent.user_id == uid)).all()
    check("the break-glass owner's password login succeeds", r.status_code == 303 and signed_in(c))
    check("...with a distinct login_break_glass auth_event (and login_ok beside it)",
          len(bg) == 1 and len(ok) == 1, f"{len(bg)} {len(ok)}")


# --------------------------------------------------------------------------- #
def section_g() -> None:
    print("\n-- G. offboarding: an IdP-backed person's agents need a recent OIDC login --")
    reset_limits()
    c, r, _ = sso_login({"sub": "kc-gus", "email": "gus@example.com", "groups": GROUPS_OK})
    check("(kc-gus signs in by SSO)", r.status_code == 303 and signed_in(c), last_fail_reason())
    path = os.path.join(TMP, "gus-token")
    rc, out, err = run_admin("token", "mint", "--user", "oidc:kc-gus", "--purpose", "claude",
                             "--scopes", "read,write", "--out", path)
    raw = Path(path).read_text(encoding="utf-8").strip() if rc == 0 else ""
    check("`token mint --user oidc:kc-gus` names the agent after the email (gus-claude)",
          rc == 0 and "gus-claude" in out and raw.startswith("atlas_svc_"), err.strip()[:160])
    check("a fresh IdP-backed person's agent token authenticates (200)", bearer_status(raw) == 200)
    with app.session() as s:
        gus_id = s.scalar(select(AuthUser.id).where(AuthUser.subject == "kc-gus"))
        s.get(AuthUserIdpState, gus_id).last_idp_login_at = (
            datetime.now(timezone.utc) - timedelta(days=31))
        s.commit()
    check("31 days after the last OIDC login (ATLAS_OIDC_REVALIDATE_DAYS=30): the token gets 401",
          bearer_status(raw) == 401)
    with app.session() as s:
        tok = s.scalar(select(models_auth.AuthServiceToken).where(
            models_auth.AuthServiceToken.label == "gus-claude"))
    check("...suspended, not revoked (revoked_at stays NULL)", tok is not None and tok.revoked_at is None)
    with app.session() as s:
        s.get(AuthUserIdpState, gus_id).last_idp_login_at = (
            datetime.now(timezone.utc) - timedelta(days=29))
        s.commit()
    check("29 days: still inside the window (200)", bearer_status(raw) == 200)
    with app.session() as s:
        s.get(AuthUserIdpState, gus_id).last_idp_login_at = (
            datetime.now(timezone.utc) - timedelta(days=45))
        s.commit()
    reset_limits()
    c, r, _ = sso_login({"sub": "kc-gus", "email": "gus@example.com", "groups": GROUPS_OK})
    check("after kc-gus logs in again, the same token works again (200)",
          r.status_code == 303 and bearer_status(raw) == 200)

    local = mk_local("local-lena")
    rc, _o, _e = run_admin("token", "mint", "--user", "local-lena", "--purpose", "claude",
                           "--out", os.path.join(TMP, "lena-token"))
    lena = Path(os.path.join(TMP, "lena-token")).read_text(encoding="utf-8").strip()
    with app.session() as s:
        row = s.get(AuthUserIdpState, local)
    check("a LOCAL person's agent works with no side-table row at all (never revalidated)",
          rc == 0 and row is None and bearer_status(lena) == 200)


# --------------------------------------------------------------------------- #
def section_h() -> None:
    print("\n-- H. atlas-admin user link-idp keeps the person and their agents --")
    reset_limits()
    frank_id = mk_local("frank")
    rc, _o, _e = run_admin("token", "mint", "--user", "frank", "--purpose", "claude",
                           "--out", os.path.join(TMP, "frank-token"))
    raw = Path(os.path.join(TMP, "frank-token")).read_text(encoding="utf-8").strip()
    check("(frank, local, has an agent whose token works)", rc == 0 and bearer_status(raw) == 200)
    rc, _o, err = run_admin("user", "link-idp", "frank", "--subject", "kc-erin")
    check("link-idp to a subject another account holds is refused", rc == 1 and "already belongs" in err,
          err.strip()[:160])
    rc, out, err = run_admin("user", "link-idp", "frank", "--subject", "kc-frank")
    with app.session() as s:
        u = s.get(AuthUser, frank_id)
        ev = s.scalars(select(AuthEvent).where(AuthEvent.event == "user_idp_linked",
                                               AuthEvent.user_id == frank_id)).all()
    check("link-idp succeeds: same id, now (oidc, kc-frank), audited",
          rc == 0 and u.idp == "oidc" and u.subject == "kc-frank" and len(ev) == 1, err.strip()[:160])
    check("until frank's first SSO login his agent's token is suspended (401)", bearer_status(raw) == 401)
    c, r, _ = sso_login({"sub": "kc-frank", "name": "Frank", "groups": GROUPS_OK})
    me = c.get("/api/me").json() if signed_in(c) else {}
    with app.session() as s:
        accounts = s.scalars(select(AuthUser).where(AuthUser.subject == "kc-frank")).all()
        agent = s.scalar(select(AuthUser).where(AuthUser.subject == "frank-claude"))
    check("frank's SSO login lands on the SAME account (no second one)",
          r.status_code == 303 and me.get("user_id") == frank_id and len(accounts) == 1,
          f"{r.status_code} {me.get('user_id')} {last_fail_reason()}")
    check("his agent is still his, and its token works again (200)",
          agent is not None and agent.person_user_id == frank_id and bearer_status(raw) == 200)
    rc, _o, err = run_admin("user", "link-idp", "oidc:kc-frank", "--subject", "kc-other")
    check("linking an already-linked account is refused", rc == 1 and "already linked" in err)
    rc, out, _e = run_admin("user", "show", "oidc:kc-frank", "--json")
    check("other commands name the account oidc:<subject>", rc == 0 and '"kc-frank"' in out)


def main() -> int:
    app.startup()
    try:
        section_a()
        section_b()
        section_c()
        section_d()
        section_e()
        section_f()
        section_g()
        section_h()
    finally:
        import shutil
        shutil.rmtree(TMP, ignore_errors=True)
    print()
    if failures:
        print(f"{len(failures)} FAILED:")
        for f in failures:
            print("  - " + f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
