"""
Verification for agent credentials for SSO-only people (TASK-1415, OSS-26 B):
ATLAS_OIDC_STEP_UP=on, against the STUB IdP of verify_oidc_login.py (no network).

An IdP-backed person has no Atlas password, so `auth.reauthenticate` could never
pass for them and nobody on an SSO-only install could create their own MCP token.
With step-up on they re-authenticate at the IdP instead: a fresh code + PKCE flow
with `prompt=login` and `max_age`, bound to their session and to the ONE credential
they named, whose ID token must carry an `auth_time` no older than the flow and no
older than the window.

  A. CONFIGURATION: off by default (settings parse to off; verify_oidc_login.py's
     route-table check proves no step-up route then); `on` without the oidc provider,
     an unknown value and a window outside 60..900 stop the boot, naming the variable;
     with it on, the route table is routes.snapshot.txt plus the two SSO routes and
     exactly one more, /auth/oidc/step-up, and the public routes are unchanged. The
     start names what it approves: no purpose/agent, a bad purpose and someone else's
     agent are refused before any flow starts.
  B. WITHOUT A STEP-UP an IdP-backed person cannot mint: 403 step_up_required naming
     the route, a `login_fail` event, and a password they send is not a way round it
     (link-idp may have kept a verifier). The credential screen says `reauth: sso`.
  C. THE STEP-UP, WITH CONSENT NAMED: the start answers a page "You are creating an
     agent credential named <name> for <account>" whose link is the authorization
     request (prompt=login, max_age = the window, S256, the step-up state prefix, a
     nonce); the callback answers a page saying the same again, with NO new session
     cookie; the screen shows the approved agent and the time left; the mint of THAT
     name answers 201 and the token authenticates; single use; rotation names the
     existing agent and takes its own step-up; a refused mint keeps the grant.
  D. CONSENT IS BINDING: a mint whose purpose differs from the approved one is
     refused (403 step_up_agent_mismatch, the grant kept, the approved one then
     mints); a second start in the same session supersedes the first, whose callback
     is then refused (step_up_superseded).
  E. AGENT NAMES MATCH `atlas-admin token mint`: an IdP person with an opaque
     GUID-style subject gets `<email local part>-<purpose>`, the same agent the CLI
     names; an IdP person with no email falls back to the subject.
  F. EVERY BROKEN STEP-UP IS REFUSED, with no live grant and a `login_fail` naming
     why: a stale auth_time, NO auth_time (Entra ID without the optional claim,
     simulated: the page names step_up_auth_time_missing and tells the operator to add
     the claim), one older than the flow (an IdP that ignored prompt=login), one in
     the future, a different IdP account, a replayed callback, the callback in another
     session of the same person, no session at all, a person who has left the required
     group, and a grant past its window.
  G. NOTHING BUT A SESSION: an agent token cannot start a step-up or mint, a grant is
     bound to the session it was made in, and no IdP token is stored in auth_step_up.
  H. LOCAL ACCOUNTS ARE UNCHANGED, with step-up on: no password is FastAPI's own 422
     naming body.password, byte for byte, and still before the write guard; a wrong
     one 403 password_incorrect; the right one 201; the credential screen's response
     has no step-up keys; a local person cannot start a step-up (400).

Run:  python verify_oidc_step_up.py
"""
from __future__ import annotations

import base64
import contextlib
import html
import io
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
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
PW = "Correct-Horse-Battery-Step-Up-7!"
TMP = tempfile.mkdtemp(prefix="atlas-oidc-step-up-")
WINDOW = 120   # ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS here: not the default, so it is proven read

for _k in [k for k in os.environ if k.startswith(("ATLAS_AUTH_PROVIDERS", "ATLAS_LOCAL_LOGIN",
                                                    "ATLAS_OIDC_"))]:
    os.environ.pop(_k)
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.pop("ATLAS_DISABLED_FEATURES", None)
os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", TEST_ORIGIN)

import oidc_testkit  # noqa: E402

STUB = oidc_testkit.StubIdP()
GROUP_MAP = oidc_testkit.write_group_map(TMP, {"atlas-users": "users"})
os.environ.update(STUB.env(
    TMP, ATLAS_OIDC_REQUIRED_GROUPS="atlas-users", ATLAS_OIDC_GROUP_MAP_FILE=GROUP_MAP,
    ATLAS_OIDC_STEP_UP="on", ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS=str(WINDOW)))

import identity.oidc  # noqa: E402

identity.oidc.use_transport(STUB.transport)

from sqlalchemy import select, text  # noqa: E402

import admin  # noqa: E402
import app  # noqa: E402
import auth  # noqa: E402
import identity  # noqa: E402
import passwords  # noqa: E402
import settings  # noqa: E402
from models_auth import AuthEvent, AuthUser  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


GROUPS_OK = ["atlas-users"]
STEP_UP = "/auth/oidc/step-up"
NEXT = "/board?stepup=1"
#: An Entra-style subject: an object id. Never a readable agent name.
GUID = "3f2a9c1e-7b4d-4e0a-9c55-1d2e3f4a5b6c"
_CONTINUE = re.compile(r'id="stepUpContinue" href="([^"]+)"')


def client(**kw):
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN, **kw)


def reset_limits() -> None:
    for ip in ("testclient", "127.0.0.1", ""):
        auth._IP_LIMITER.reset(auth._login_key(ip))
        auth._IP_LIMITER.reset(auth._bearer_key(ip))


def sso_login(sub: str, c=None, groups=None, email=None):
    c = c or client()
    r = c.get("/auth/oidc/start", follow_redirects=False)
    claims = {"sub": sub, "groups": groups or GROUPS_OK}
    if email:
        claims["email"] = email
    cb = STUB.authorize(r.headers.get("location", ""), claims)
    r = c.get(cb, follow_redirects=False)
    assert r.status_code == 303 and c.get("/api/me").status_code == 200, (sub, last_fail_reason())
    return c


def last_fail_reason() -> str:
    with app.session() as s:
        row = s.scalars(select(AuthEvent).where(AuthEvent.event == "login_fail")
                        .order_by(AuthEvent.id.desc()).limit(1)).first()
        return row.reason if row is not None else ""


def step_up_start(c, purpose: str = "claude-code", *, agent: str = ""):
    """GET the start page; returns (response, the IdP authorization URL from its link)."""
    params = {"next": NEXT}
    if agent:
        params["agent"] = agent
    else:
        params["purpose"] = purpose
    r = c.get(STEP_UP, params=params, follow_redirects=False)
    m = _CONTINUE.search(r.text) if r.status_code == 200 else None
    return r, html.unescape(m.group(1)) if m else ""


def step_up(c, sub: str, purpose: str = "claude-code", *, agent: str = "", auth_time=None,
            groups=None, drop_auth_time=False, cb_client=None):
    """Start a step-up in `c` for `purpose` (or `agent`), sign in at the stub as `sub`,
    return (callback response, callback path)."""
    _r, loc = step_up_start(c, purpose, agent=agent)
    claims = {"sub": sub, "groups": groups or GROUPS_OK}
    if not drop_auth_time:
        claims["auth_time"] = int(time.time()) if auth_time is None else auth_time
    cb = STUB.authorize(loc, claims)
    r = (cb_client or c).get(cb, follow_redirects=False)
    return r, cb


def csrf(c) -> str:
    return c.get("/api/me").json()["csrf_token"]


def mint(c, purpose: str, **extra):
    body = {"purpose": purpose, "scopes": ["read"], "expires_days": 30, "csrf_token": csrf(c)}
    body.update(extra)
    return c.post("/api/me/agent-credential", json=body, headers={"Origin": TEST_ORIGIN})


def reissue(c, agent: str, **extra):
    body = {"purpose": "x", "scopes": ["read"], "expires_days": 30, "csrf_token": csrf(c)}
    body.update(extra)
    return c.post("/api/me/agent/%s/credential" % agent, json=body, headers={"Origin": TEST_ORIGIN})


def bearer(raw: str):
    return client(headers={"Authorization": "Bearer " + raw})


def live_grants() -> int:
    with app.session() as s:
        return s.scalar(text("SELECT count(*) FROM auth_step_up WHERE auth_time IS NOT NULL"))


def mk_local(username: str) -> str:
    import uuid
    uid = str(uuid.uuid4())
    with app.session() as s:
        s.add(AuthUser(id=uid, subject=username, name=username.title(), role="member",
                       is_service=False, must_change_password=False,
                       password_hash=passwords.hash_password(PW),
                       created_at=datetime.now(timezone.utc)))
        s.commit()
    return uid


def run_admin(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = admin.main(list(argv))
        except SystemExit as exc:
            rc = int(exc.code or 0)
    return rc, out.getvalue(), err.getvalue()


def route_lines() -> list[str]:
    from route_table import live_routes, route_class
    out = []
    for r in live_routes(app.app):
        if getattr(r, "path", None) is None:
            continue
        for m in sorted(getattr(r, "methods", None) or ["*"]):
            out.append(f"{m} {r.path} {getattr(r, 'name', '')} {route_class(r)}")
    return sorted(out)


def _refused_step_up(label: str, r, reason_part: str, before: int) -> None:
    check(f"refused: {label} (403 page, no live grant; login_fail says {reason_part})",
          r.status_code == 403 and auth.SESSION_COOKIE not in r.headers.get("set-cookie", "")
          and live_grants() == before and reason_part in last_fail_reason(),
          f"{r.status_code} grants={live_grants()} {last_fail_reason()}")


# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. configuration, and the start names what it approves --")
    base = dict(STUB.env(TMP))
    off = settings.load_identity(base)
    check("unset: step-up is off (today's password prompt for everyone)",
          off.oidc.step_up is False and off.oidc.step_up_max_age == settings.DEFAULT_OIDC_STEP_UP_MAX_AGE)
    check("the default window is 300 s", settings.DEFAULT_OIDC_STEP_UP_MAX_AGE == 300)
    bad = [
        ("on without the oidc provider", {"ATLAS_OIDC_STEP_UP": "on"}, "ATLAS_OIDC_STEP_UP"),
        ("an unknown value", dict(base, ATLAS_OIDC_STEP_UP="yes"), "ATLAS_OIDC_STEP_UP"),
        ("set but blank", dict(base, ATLAS_OIDC_STEP_UP=" "), "ATLAS_OIDC_STEP_UP"),
        ("a window under 60 s", dict(base, ATLAS_OIDC_STEP_UP="on",
                                     ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS="30"),
         "ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS"),
        ("a window over 900 s", dict(base, ATLAS_OIDC_STEP_UP="on",
                                     ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS="3600"),
         "ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS"),
    ]
    for label, env, name in bad:
        try:
            settings.load_identity(env)
            ok, msg = False, "accepted"
        except settings.SettingsError as exc:
            ok, msg = name in str(exc), str(exc)
        check(f"refused at boot: {label} (names {name})", ok, msg[:120])
    check("this run: on, window %d s" % WINDOW,
          identity.step_up_enabled() and identity.SETTINGS.oidc.step_up_max_age == WINDOW)
    snapshot = [ln for ln in (HERE / "routes.snapshot.txt").read_text(encoding="utf-8").splitlines()
                if ln.strip()]
    extra = sorted(set(route_lines()) - set(snapshot))
    missing = sorted(set(snapshot) - set(route_lines()))
    check("the route table is the snapshot + the two SSO routes + /auth/oidc/step-up",
          not missing and extra == ["GET /auth/oidc/callback sso_oidc_callback APIRoute",
                                    "GET /auth/oidc/start sso_oidc_start APIRoute",
                                    "GET /auth/oidc/step-up sso_oidc_step_up APIRoute"],
          f"extra={extra} missing={missing}")
    check("/auth/oidc/step-up is NOT a public route", STEP_UP not in auth.PUBLIC_ROUTES)
    r = client().get(STEP_UP, params={"purpose": "x"}, follow_redirects=False)
    check("signed out: the step-up route is gated (no IdP link)",
          r.status_code in (302, 401) and "stepUpContinue" not in r.text,
          f"{r.status_code} {r.headers.get('location')}")
    reset_limits()
    c = sso_login("kc-sue")
    r = c.get(STEP_UP, follow_redirects=False)
    check("a start naming nothing is refused (400 step_up_needs_agent)",
          r.status_code == 400 and r.json().get("error") == "step_up_needs_agent", r.text[:120])
    r, loc = step_up_start(c, "Not A Handle!")
    check("a purpose that cannot form a name is refused (400 bad_purpose)",
          r.status_code == 400 and r.json().get("error") == "bad_purpose" and not loc)
    r, loc = step_up_start(c, agent="someone-elses-agent")
    check("rotating an agent that is not yours is refused (404)", r.status_code == 404 and not loc)


# --------------------------------------------------------------------------- #
def section_b() -> None:
    print("\n-- B. without a step-up an IdP-backed person cannot mint --")
    reset_limits()
    c = sso_login("kc-sue")
    listing = c.get("/api/me/agent-credentials").json()
    check("the credential screen says reauth=sso, the route, nothing fresh yet, the window, "
          "the prefix and the account",
          listing.get("reauth") == "sso" and listing.get("step_up_url") == STEP_UP
          and listing.get("step_up_seconds_left") == 0 and listing.get("step_up_agent") is None
          and listing.get("step_up_max_age") == WINDOW and listing.get("agent_prefix") == "kc-sue"
          and listing.get("account") == "kc-sue",
          str({k: listing.get(k) for k in ("reauth", "step_up_url", "agent_prefix", "account")}))
    r = mint(c, "claude-code")
    body = r.json()
    check("mint with no step-up: 403 step_up_required naming the route",
          r.status_code == 403 and body.get("error") == "step_up_required"
          and body.get("step_up_url") == STEP_UP, f"{r.status_code} {body}")
    check("...and a login_fail agent_credential:step_up_required",
          last_fail_reason() == "agent_credential:step_up_required", last_fail_reason())
    r = mint(c, "claude-code", password="anything-at-all")
    check("a password is no way round it for an IdP-backed person (403 step_up_required)",
          r.status_code == 403 and r.json().get("error") == "step_up_required")


# --------------------------------------------------------------------------- #
def section_c() -> None:
    print("\n-- C. the step-up, with the consent named, then the mint --")
    reset_limits()
    c = sso_login("kc-sue")
    before_cookies = dict(c.cookies)
    r, loc = step_up_start(c, "claude-code")
    q = {k: v[0] for k, v in parse_qs(urlsplit(loc).query).items()}
    check("start: a PAGE (200, not a redirect) saying what is approved, for whom",
          r.status_code == 200 and "You are creating an agent credential named "
          "<strong>kc-sue-claude-code</strong> for <strong>kc-sue</strong>" in r.text,
          f"{r.status_code} {r.text[:200]}")
    check("...whose link is the IdP's authorization endpoint", loc.startswith(STUB.authorization_endpoint),
          loc[:80])
    check("the request asks for a NEW sign-in: prompt=login, max_age=%d" % WINDOW,
          q.get("prompt") == "login" and q.get("max_age") == str(WINDOW), str(q))
    check("...the same code + PKCE flow: response_type=code, S256, nonce, redirect_uri",
          q.get("response_type") == "code" and q.get("code_challenge_method") == "S256"
          and len(q.get("nonce", "")) >= 32 and q.get("redirect_uri") == STUB.redirect_uri)
    check("the state carries the step-up prefix",
          q.get("state", "").startswith(identity.oidc.STEP_UP_STATE_PREFIX))
    with app.session() as s:
        tags = [row[0] for row in s.execute(text("SELECT provider FROM auth_login_flow"))]
        pending = s.execute(text("SELECT agent, auth_time FROM auth_step_up")).all()
    check("the flow row is tagged oidc+step-up", "oidc+step-up" in tags, str(tags))
    check("a PENDING step-up names the agent, with no auth_time yet",
          [(a, t) for a, t in pending] == [("kc-sue-claude-code", None)], str(pending))
    cb = STUB.authorize(loc, {"sub": "kc-sue", "groups": GROUPS_OK, "auth_time": int(time.time())})
    r = c.get(cb, follow_redirects=False)
    sent = STUB.token_requests[-1] if STUB.token_requests else {}
    check("the token request carried the verifier for this flow's challenge",
          identity.oidc.pkce_challenge(sent.get("code_verifier", "")) == q.get("code_challenge"))
    check("callback: a page saying again what was approved, a link back to next, NO new "
          "session cookie (nothing is rotated)",
          r.status_code == 200 and "You are creating an agent credential named "
          "<strong>kc-sue-claude-code</strong> for <strong>kc-sue</strong>" in r.text
          and 'href="%s"' % html.escape(NEXT, quote=True) in r.text
          and auth.SESSION_COOKIE not in r.headers.get("set-cookie", "")
          and dict(c.cookies) == before_cookies,
          f"{r.status_code} {last_fail_reason()} {r.text[:200]}")
    with app.session() as s:
        ok = s.scalars(select(AuthEvent).where(AuthEvent.event == "step_up_ok")).all()
    check("a step_up_ok auth_event naming the agent",
          len(ok) == 1 and ok[0].reason == "provider=oidc" and "kc-sue-claude-code" in (ok[0].detail or ""))
    listing = c.get("/api/me/agent-credentials").json()
    check("the screen shows the approved agent and the time left (0 < left <= window)",
          listing.get("step_up_agent") == "kc-sue-claude-code"
          and 0 < listing.get("step_up_seconds_left", 0) <= WINDOW, str(listing.get("step_up_agent")))
    r = mint(c, "claude-code")
    body = r.json()
    check("mint of the approved name: 201, a token, the agent named for the person",
          r.status_code == 201 and body.get("token", "").startswith("atlas_svc_")
          and body.get("agent") == "kc-sue-claude-code", f"{r.status_code} {str(body)[:160]}")
    raw = body.get("token", "")
    check("the token authenticates (/api/me 200, as the agent)",
          bearer(raw).get("/api/me").status_code == 200)
    check("the grant is spent", live_grants() == 0, str(live_grants()))
    r = mint(c, "claude-code-2")
    check("a second mint needs a second step-up (403 step_up_required)",
          r.status_code == 403 and r.json().get("error") == "step_up_required")
    r = reissue(c, "kc-sue-claude-code")
    check("rotation without a step-up: 403 step_up_required",
          r.status_code == 403 and r.json().get("error") == "step_up_required")
    r, _loc = step_up_start(c, agent="kc-sue-claude-code")
    check("a rotation's start page names the existing agent",
          r.status_code == 200 and "You are issuing a new credential for the agent "
          "<strong>kc-sue-claude-code</strong>" in r.text, r.text[:200])
    step_up(c, "kc-sue", agent="kc-sue-claude-code")
    r = reissue(c, "kc-sue-claude-code")
    check("rotation after a step-up naming that agent: 201", r.status_code == 201 and r.json().get("token"),
          f"{r.status_code} {r.text[:160]}")

    # A refused mint keeps the grant (the delete rides the mint's transaction).
    step_up(c, "kc-sue", "claude-code")
    r = mint(c, "claude-code")   # the agent exists, so a NEW one by that name is a 409
    check("a mint refused for a taken name (409) keeps the grant",
          r.status_code == 409 and live_grants() == 1, f"{r.status_code} grants={live_grants()}")
    r = reissue(c, "kc-sue-claude-code")
    check("...which the same agent's rotation then spends (201)",
          r.status_code == 201 and live_grants() == 0, f"{r.status_code} grants={live_grants()}")


# --------------------------------------------------------------------------- #
def section_d() -> None:
    print("\n-- D. consent is binding: the approved name and only that --")
    reset_limits()
    c = sso_login("kc-sue")
    r, _ = step_up(c, "kc-sue", "alpha")
    check("(a step-up approving kc-sue-alpha)", r.status_code == 200 and live_grants() == 1)
    r = mint(c, "beta")
    body = r.json()
    check("a mint of ANOTHER name is refused: 403 step_up_agent_mismatch naming the approved one",
          r.status_code == 403 and body.get("error") == "step_up_agent_mismatch"
          and body.get("approved_agent") == "kc-sue-alpha", f"{r.status_code} {body}")
    check("...a login_fail agent_credential:step_up_agent_mismatch, and the grant is kept",
          last_fail_reason() == "agent_credential:step_up_agent_mismatch" and live_grants() == 1,
          f"{last_fail_reason()} grants={live_grants()}")
    r = reissue(c, "kc-sue-claude-code")
    check("...rotating a different existing agent is refused the same way",
          r.status_code == 403 and r.json().get("error") == "step_up_agent_mismatch")
    r = mint(c, "alpha")
    check("...and the approved name then mints (201)", r.status_code == 201
          and r.json().get("agent") == "kc-sue-alpha", f"{r.status_code} {r.text[:120]}")

    _r, loc_first = step_up_start(c, "first")
    _r, _loc_second = step_up_start(c, "second")
    cb = STUB.authorize(loc_first, {"sub": "kc-sue", "groups": GROUPS_OK, "auth_time": int(time.time())})
    r = c.get(cb, follow_redirects=False)
    _refused_step_up("a superseded start's callback (a later start replaced it)", r,
                     "oidc+step-up:step_up_superseded", 0)


# --------------------------------------------------------------------------- #
def section_e() -> None:
    print("\n-- E. agent names match `atlas-admin token mint` --")
    reset_limits()
    c = sso_login(GUID, email="Gina.Example@example.com")
    listing = c.get("/api/me/agent-credentials").json()
    check("a GUID-subject person's prefix is their email local part, not the GUID",
          listing.get("agent_prefix") == "gina.example", str(listing.get("agent_prefix")))
    r, _loc = step_up_start(c, "claude-code")
    check("...and the start page names gina.example-claude-code",
          "<strong>gina.example-claude-code</strong>" in r.text and GUID not in r.text.split(
              "<strong>")[1], r.text[:200])
    step_up(c, GUID, "claude-code")
    r = mint(c, "claude-code")
    check("self-service names the agent gina.example-claude-code",
          r.status_code == 201 and r.json().get("agent") == "gina.example-claude-code",
          f"{r.status_code} {r.text[:160]}")
    path = os.path.join(TMP, "gina-token")
    rc, out, err = run_admin("token", "mint", "--user", "oidc:" + GUID, "--purpose", "claude-code",
                             "--out", path)
    with app.session() as s:
        agents = s.scalars(select(AuthUser.subject).where(
            AuthUser.subject.like("gina.example-%"))).all()
    check("`atlas-admin token mint` for the same person and purpose lands on THE SAME agent",
          rc == 0 and "gina.example-claude-code" in out + err
          and sorted(agents) == ["gina.example-claude-code"], f"rc={rc} {sorted(agents)} {err[:120]}")
    reset_limits()
    c2 = sso_login("kc-noemail")
    step_up(c2, "kc-noemail", "claude-code")
    r = mint(c2, "claude-code")
    check("an IdP person with NO email falls back to the subject (kc-noemail-claude-code)",
          r.status_code == 201 and r.json().get("agent") == "kc-noemail-claude-code",
          f"{r.status_code} {r.text[:160]}")


# --------------------------------------------------------------------------- #
def section_f() -> None:
    print("\n-- F. every broken step-up is refused --")
    reset_limits()
    c = sso_login("kc-sue")
    now = int(time.time())
    n = live_grants()
    r, _ = step_up(c, "kc-sue", auth_time=now - WINDOW - 60)
    _refused_step_up("a stale auth_time (window + 60 s old)", r, "oidc+step-up:auth_time", n)
    # The window on its own: a flow started long ago (its row aged by hand), an
    # auth_time after the flow started but older than the window now.
    _r, loc = step_up_start(c)
    with app.session() as s:
        s.execute(text("UPDATE auth_login_flow SET created_at = now() - interval '300 seconds' "
                       "WHERE provider = 'oidc+step-up'"))
        s.commit()
    cb = STUB.authorize(loc, {"sub": "kc-sue", "groups": GROUPS_OK, "auth_time": now - WINDOW - 30})
    r = c.get(cb, follow_redirects=False)
    _refused_step_up("an auth_time after the flow began but older than the window", r,
                     "oidc+step-up:auth_time_stale", n)
    reset_limits()
    # ENTRA ID WITHOUT THE OPTIONAL CLAIM, simulated: the ID token has no auth_time.
    r, _ = step_up(c, "kc-sue", drop_auth_time=True)
    _refused_step_up("NO auth_time (Entra ID without the optional claim)", r,
                     "oidc+step-up:auth_time_missing", n)
    check("...the page names the error step_up_auth_time_missing and tells the operator to "
          "add the optional claim auth_time",
          "step_up_auth_time_missing" in r.text and "optional claim auth_time" in r.text
          and "Entra ID" in r.text, r.text[:300])
    r, _ = step_up(c, "kc-sue", auth_time=now - 100)
    _refused_step_up("an auth_time from before the flow (prompt=login ignored)", r,
                     "oidc+step-up:auth_time_before_step_up", n)
    reset_limits()
    r, _ = step_up(c, "kc-sue", auth_time=now + 3600)
    _refused_step_up("an auth_time in the future", r, "oidc+step-up:auth_time_in_future", n)
    sso_login("kc-tom")   # another person, so the lookup finds an account
    r, _ = step_up(c, "kc-tom")
    _refused_step_up("another IdP account signs in", r, "oidc+step-up:step_up_other_account", n)
    r, cb = step_up(c, "kc-sue")
    check("(a good step-up, to replay)", r.status_code == 200 and live_grants() == n + 1)
    with app.session() as s:
        s.execute(text("DELETE FROM auth_step_up"))
        s.commit()
    r = c.get(cb, follow_redirects=False)
    _refused_step_up("a replayed callback", r, "oidc+step-up:state_unknown", 0)
    reset_limits()
    other = sso_login("kc-sue")   # a SECOND session of the same person
    r, _ = step_up(c, "kc-sue", cb_client=other)
    _refused_step_up("the callback in another session of the same person", r,
                     "oidc+step-up:state_other_browser", 0)
    _r, loc = step_up_start(c)
    cb = STUB.authorize(loc, {"sub": "kc-sue", "groups": GROUPS_OK, "auth_time": int(time.time())})
    r = client().get(cb, follow_redirects=False)
    _refused_step_up("the callback with no session at all", r, "oidc+step-up:state_other_browser", 0)
    reset_limits()
    r, _ = step_up(c, "kc-sue", groups=["contractors"])
    _refused_step_up("a person who has left the required group", r,
                     "oidc+step-up:not_in_required_group", 0)
    reset_limits()
    r, _ = step_up(c, "kc-sue", "late")
    with app.session() as s:
        s.execute(text("UPDATE auth_step_up SET expires_at = now() - interval '1 second'"))
        s.commit()
    r = mint(c, "late")
    check("a grant past its window does not mint (403 step_up_required)",
          r.status_code == 403 and r.json().get("error") == "step_up_required", r.text[:120])
    check("...and is cleared", live_grants() == 0)
    check("the person's own session still works throughout", c.get("/api/me").status_code == 200)


# --------------------------------------------------------------------------- #
def section_g() -> None:
    print("\n-- G. nothing but the session that stepped up --")
    reset_limits()
    # Earlier sections start step-ups they never finish (pages viewed, superseded
    # starts); those flow rows expire on their own. Clear them, so the check at
    # the end sees only what this section's completed step-ups leave behind.
    with app.session() as s:
        s.execute(text("DELETE FROM auth_login_flow"))
        s.commit()
    c = sso_login("kc-sue")
    step_up(c, "kc-sue", "g-agent")
    r = mint(c, "g-agent")
    raw = r.json().get("token", "")
    check("(kc-sue has an agent token)", r.status_code == 201 and raw)
    b = bearer(raw)
    r = b.get(STEP_UP, params={"purpose": "laundered"}, follow_redirects=False)
    check("an agent token cannot start a step-up (403 session_required, no IdP link)",
          r.status_code == 403 and r.json().get("error") == "session_required", f"{r.status_code}")
    step_up(c, "kc-sue", "laundered")
    # With a password, so the body passes validation and reaches the gate's refusal
    # (without one it is the schema's 422 first, exactly as before TASK-1415).
    r = b.post("/api/me/agent-credential",
               json={"purpose": "laundered", "scopes": ["read"], "password": "irrelevant"})
    check("an agent token cannot mint, even while its person holds a fresh grant for that name",
          r.status_code == 403 and live_grants() == 1, f"{r.status_code} grants={live_grants()}")
    other = sso_login("kc-sue")
    r = mint(other, "laundered")
    check("another session of the same person cannot spend the grant (403, grant kept)",
          r.status_code == 403 and r.json().get("error") == "step_up_required" and live_grants() == 1,
          f"{r.status_code} grants={live_grants()}")
    with app.engine.connect() as conn:
        rows = [" ".join(str(v) for v in row) for row in conn.execute(text("SELECT * FROM auth_step_up"))]
        flows_left = [row[0] for row in conn.execute(text("SELECT provider FROM auth_login_flow"))]
    check("auth_step_up holds no IdP token (no JWT, no access/refresh token)",
          rows and not any("eyJ" in x or "stub-access-" in x or "stub-refresh-" in x for x in rows))
    check("...and no flow row is left behind by a completed step-up",
          not any("+step-up" in str(p) for p in flows_left), str(flows_left))


# --------------------------------------------------------------------------- #
def section_h() -> None:
    print("\n-- H. local accounts keep the password, unchanged (step-up on) --")
    reset_limits()
    mk_local("lou")
    c = client()
    page = c.get("/auth/login")
    tok = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    r = c.post("/auth/login", data={"username": "lou", "password": PW, "csrf_token": tok},
               headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
    check("(lou, local, signs in with a password)", r.status_code == 303
          and c.get("/api/me").status_code == 200)
    listing = c.get("/api/me/agent-credentials").json()
    check("the credential screen's response has no step-up keys (today's, byte for byte)",
          not {"reauth", "step_up_url", "agent_prefix", "account"} & set(listing),
          str(sorted(listing)))
    nopw = {"purpose": "claude-code", "scopes": ["read"], "expires_days": 30, "csrf_token": csrf(c)}
    r = c.post("/api/me/agent-credential", json=nopw, headers={"Origin": TEST_ORIGIN})
    check("no password: FastAPI's own 422 naming body.password, the body byte for byte",
          r.status_code == 422 and r.json() == {"detail": [{
              "type": "missing", "loc": ["body", "password"], "msg": "Field required",
              "input": nopw}]}, f"{r.status_code} {r.text[:200]}")
    r = c.post("/api/me/agent-credential", json={"purpose": "claude-code", "scopes": ["read"]},
               headers={"Origin": TEST_ORIGIN})
    check("...still BEFORE the write guard (no password and no CSRF token: 422, not 403)",
          r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "password"],
          f"{r.status_code} {r.text[:160]}")
    r = c.post("/api/me/agent-credential", headers={"Origin": TEST_ORIGIN},
               json={"purpose": "claude-code", "password": "", "csrf_token": csrf(c)})
    check("an empty password: 422 string_too_short, as before",
          r.status_code == 422 and r.json()["detail"][0]["type"] == "string_too_short", r.text[:160])
    r = mint(c, "claude-code", password="wrong-" + PW)
    check("a wrong password: 403 password_incorrect naming the field", r.status_code == 403
          and r.json().get("error") == "password_incorrect" and r.json().get("field") == "password")
    r = mint(c, "claude-code", password=PW)
    check("the right password: 201, agent lou-claude-code",
          r.status_code == 201 and r.json().get("agent") == "lou-claude-code", r.text[:120])
    r = c.get(STEP_UP, params={"purpose": "x"}, follow_redirects=False)
    check("a local person cannot start a step-up (400 step_up_not_applicable)",
          r.status_code == 400 and r.json().get("error") == "step_up_not_applicable")


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
