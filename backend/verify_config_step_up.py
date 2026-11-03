"""
Verification for configuration writes by SSO-only owners (TASK-1436, OSS-26 K):
ATLAS_CONFIG_WRITES=1 and ATLAS_OIDC_STEP_UP=on, against the STUB IdP of
verify_oidc_login.py (no network).

`PUT /api/config/{name}` re-authenticates every change with the owner's password
(routers/config.py). An IdP-backed owner has no Atlas password, so on an SSO-only
install nobody could make a change. With step-up on, such an owner confirms the
change with a fresh IdP sign-in instead: the agent-credential step-up of TASK-1415
(verify_oidc_step_up.py), started with `?config=<NAME>` and bound to THAT setting.

  A. THE START names the setting and refuses what the write would refuse: nothing
     named or two things named (400), an unknown setting (404), a bootstrap one and a
     secret (403), a member (403 forbidden), a local owner (400 step_up_not_applicable).
  B. WITHOUT A STEP-UP the SSO owner is refused (403 step_up_required naming the
     route, a `login_fail` config_write:step_up_required), a password is no way round
     it, and GET /api/config says `writes.step_up: sso` with nothing confirmed.
  C. A STALE STEP-UP (auth_time older than the window) is refused at the callback and
     the write is still refused.
  D. A FRESH STEP-UP: the start and callback pages say "You are changing the setting
     <NAME> of this Atlas install, as <account>"; GET shows the confirmed setting and
     the time left; the change answers 200, source
     board, one node.config.set event; the grant is spent; a second change is refused; a
     value equal to the board's changes nothing and keeps the grant.
  E. THE GRANT IS BOUND TO ITS PURPOSE: one for another setting is 403
     step_up_setting_mismatch (kept, and then spent by its own setting); one for a
     config write cannot mint an agent credential (403 step_up_required,
     agent_credential:step_up_purpose_mismatch, kept); one for an agent credential
     cannot change a setting (403 step_up_required, config_write:step_up_purpose_mismatch,
     kept, and the credential then mints); a grant past its window is refused.
  F. NOTHING BUT THE SESSION: the owner's admin token is still refused
     (session_required), and another session of the same owner cannot spend the grant.
  G. LOCAL OWNERS ARE UNCHANGED, with step-up on: GET says `step_up: password` with no
     step-up keys; no password is 403 step_up_required naming `password`; a wrong one
     403 password_incorrect; the right one 200.

Step-up off is the homelab, and verify_config_writes.py proves that path unchanged.

Run:  python verify_config_step_up.py
"""
from __future__ import annotations

import base64
import html
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:  # pragma: no cover - never a skip
    print(f"FATAL: {e}")
    sys.exit(1)

TEST_ORIGIN = "https://testserver"
PW = "Correct-Horse-Battery-Config-8!"
TMP = tempfile.mkdtemp(prefix="atlas-config-step-up-")
WINDOW = 120
SETTING = "ATLAS_STRICT_API"
OTHER = "ATLAS_RETRIEVAL_TOP_K"

for _k in [k for k in os.environ if k.startswith(("ATLAS_AUTH_PROVIDERS", "ATLAS_LOCAL_LOGIN",
                                                    "ATLAS_OIDC_"))]:
    os.environ.pop(_k)
for _k in (SETTING, OTHER, "ATLAS_DISABLED_FEATURES"):
    os.environ.pop(_k, None)
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ["ATLAS_CONFIG_WRITES"] = "1"
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
import passwords  # noqa: E402
from models_auth import AuthEvent, AuthUser  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


GROUPS_OK = ["atlas-users"]
STEP_UP = "/auth/oidc/step-up"
NEXT = "/board?node=atlas-system-api"
OWNER_SUB = "kc-olga"
_CONTINUE = re.compile(r'id="stepUpContinue" href="([^"]+)"')


def client(**kw):
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN, **kw)


def reset_limits() -> None:
    for ip in ("testclient", "127.0.0.1", ""):
        auth._IP_LIMITER.reset(auth._login_key(ip))
        auth._IP_LIMITER.reset(auth._bearer_key(ip))


def sso_login(sub: str):
    c = client()
    r = c.get("/auth/oidc/start", follow_redirects=False)
    cb = STUB.authorize(r.headers.get("location", ""), {"sub": sub, "groups": GROUPS_OK,
                                                        "email": sub + "@example.com"})
    r = c.get(cb, follow_redirects=False)
    assert r.status_code == 303 and c.get("/api/me").status_code == 200, (sub, last_fail_reason())
    return c


def last_fail_reason() -> str:
    with app.session() as s:
        row = s.scalars(select(AuthEvent).where(AuthEvent.event == "login_fail")
                        .order_by(AuthEvent.id.desc()).limit(1)).first()
        return row.reason if row is not None else ""


def start(c, **params):
    r = c.get(STEP_UP, params={"next": NEXT, **params}, follow_redirects=False)
    m = _CONTINUE.search(r.text) if r.status_code == 200 else None
    return r, html.unescape(m.group(1)) if m else ""


def step_up(c, sub: str = OWNER_SUB, *, auth_time=None, **params):
    """Start a step-up in `c` (config=SETTING unless told otherwise), sign in at the stub."""
    _r, loc = start(c, **(params or {"config": SETTING}))
    claims = {"sub": sub, "groups": GROUPS_OK,
              "auth_time": int(time.time()) if auth_time is None else auth_time}
    return c.get(STUB.authorize(loc, claims), follow_redirects=False)


def csrf(c) -> str:
    return c.get("/api/me").json()["csrf_token"]


def put(c, name: str, value, **extra):
    body = {"value": value, "csrf_token": csrf(c), **extra}
    return c.put("/api/config/" + name, json=body, headers={"Origin": TEST_ORIGIN})


def mint(c, purpose: str):
    return c.post("/api/me/agent-credential", headers={"Origin": TEST_ORIGIN}, json={
        "purpose": purpose, "scopes": ["read"], "expires_days": 30, "csrf_token": csrf(c)})


def writes(c) -> dict:
    return (c.get("/api/config").json() or {}).get("writes") or {}


def value_of(c, name: str):
    for p in c.get("/api/config").json()["parts"]:
        for e in p["settings"]:
            if e["name"] == name:
                return e
    return {}


def live_grants() -> int:
    with app.session() as s:
        return s.scalar(text("SELECT count(*) FROM auth_step_up WHERE auth_time IS NOT NULL"))


def config_events() -> int:
    with app.session() as s:
        return s.scalar(text("SELECT count(*) FROM graph_event WHERE action = 'node.config.set'"))


def run_admin(*argv: str) -> int:
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            return admin.main(list(argv)) or 0
        except SystemExit as exc:
            return int(exc.code or 0)


def mk_local_owner(username: str) -> None:
    import uuid
    with app.session() as s:
        s.add(AuthUser(id=str(uuid.uuid4()), subject=username, name=username.title(), role="owner",
                       is_service=False, must_change_password=False,
                       password_hash=passwords.hash_password(PW),
                       created_at=datetime.now(timezone.utc)))
        s.commit()


def owner_session():
    reset_limits()
    return sso_login(OWNER_SUB)


# --------------------------------------------------------------------------- #
def setup() -> None:
    print("\n-- setup: an owner moved to SSO (user add --role owner, link-idp), as an operator would --")
    mk_local_owner("olga")
    rc = run_admin("user", "link-idp", "olga", "--subject", OWNER_SUB)
    with app.session() as s:
        olga = s.scalar(select(AuthUser).where(AuthUser.subject == OWNER_SUB))
    check("olga is an owner whose account signs in through the IdP",
          rc == 0 and olga is not None and olga.idp == "oidc" and olga.role == "owner"
          and auth.step_up_required(olga), f"rc={rc}")


def section_a() -> None:
    print("\n-- A. the start names the setting and refuses what the write would refuse --")
    c = owner_session()
    cases = [
        ("nothing named", {}, 400, "step_up_needs_agent"),
        ("a setting AND a purpose", {"config": SETTING, "purpose": "x"}, 400, "step_up_needs_agent"),
        ("an unknown setting", {"config": "ATLAS_NO_SUCH_SETTING"}, 404, "unknown_setting"),
        ("a bootstrap setting", {"config": "ATLAS_DB_URL"}, 403, "bootstrap_read_only"),
        ("a secret", {"config": "ATLAS_OIDC_CLIENT_SECRET"}, 403, "secret_not_changeable"),
    ]
    for label, params, status, error in cases:
        r, loc = start(c, **params)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        check(f"refused before any flow: {label} ({status} {error})",
              r.status_code == status and not loc and body.get("error") == error,
              f"{r.status_code} {r.text[:120]}")
    reset_limits()
    member = sso_login("kc-sue")
    r, loc = start(member, config=SETTING)
    check("an SSO member cannot start a configuration step-up (403 forbidden)",
          r.status_code == 403 and r.json().get("error") == "forbidden" and not loc, r.text[:120])
    r, loc = start(c, config=SETTING)
    check("the start page names the setting and the account",
          r.status_code == 200 and loc.startswith(STUB.authorization_endpoint)
          and "You are changing the setting <strong>%s</strong> of this Atlas install, as "
              "<strong>%s@example.com</strong>" % (SETTING, OWNER_SUB) in r.text,
          f"{r.status_code} {r.text[:200]}")
    with app.session() as s:
        pending = s.execute(text("SELECT agent FROM auth_step_up WHERE auth_time IS NULL")).scalars().all()
    check("the pending grant is config:%s" % SETTING, "config:" + SETTING in pending, str(pending))


def section_b() -> None:
    print("\n-- B. without a step-up the SSO owner is refused --")
    c = owner_session()
    w = writes(c)
    check("GET /api/config says step_up=sso, the route, nothing confirmed, the window",
          w.get("step_up") == "sso" and w.get("step_up_url") == STEP_UP
          and w.get("step_up_setting") is None and w.get("step_up_seconds_left") == 0
          and w.get("step_up_max_age") == WINDOW, str(w))
    r = put(c, SETTING, "1")
    check("PUT with no step-up: 403 step_up_required naming the route",
          r.status_code == 403 and r.json().get("error") == "step_up_required"
          and r.json().get("step_up_url") == STEP_UP, f"{r.status_code} {r.text[:160]}")
    check("...a login_fail config_write:step_up_required",
          last_fail_reason() == "config_write:step_up_required", last_fail_reason())
    r = put(c, SETTING, "1", password="anything-at-all")
    check("a password is no way round it (403 step_up_required)",
          r.status_code == 403 and r.json().get("error") == "step_up_required")
    check("...and nothing was changed", value_of(c, SETTING).get("source") != "board"
          and config_events() == 0)


def section_c() -> None:
    print("\n-- C. a stale step-up is refused --")
    c = owner_session()
    r = step_up(c, auth_time=int(time.time()) - WINDOW - 60)
    check("a stale auth_time: the callback is a 403 page and no grant is live",
          r.status_code == 403 and live_grants() == 0
          and "oidc+step-up:auth_time" in last_fail_reason(),
          f"{r.status_code} {last_fail_reason()}")
    r = put(c, SETTING, "1")
    check("...and the change is still refused (403 step_up_required)",
          r.status_code == 403 and r.json().get("error") == "step_up_required" and config_events() == 0)


def section_d() -> None:
    print("\n-- D. a fresh step-up makes one change --")
    c = owner_session()
    r = step_up(c)
    check("the callback page says again what was approved, with no new session cookie",
          r.status_code == 200 and "You are changing the setting <strong>%s</strong>" % SETTING in r.text
          and auth.SESSION_COOKIE not in r.headers.get("set-cookie", ""),
          f"{r.status_code} {last_fail_reason()} {r.text[:200]}")
    with app.session() as s:
        ok = s.scalars(select(AuthEvent).where(AuthEvent.event == "step_up_ok")
                       .order_by(AuthEvent.id.desc()).limit(1)).first()
    check("a step_up_ok naming the setting", ok is not None and ok.detail == "config=" + SETTING,
          str(ok.detail if ok else None))
    w = writes(c)
    check("GET shows the confirmed setting and the time left",
          w.get("step_up_setting") == SETTING and 0 < w.get("step_up_seconds_left", 0) <= WINDOW, str(w))
    current = value_of(c, SETTING).get("value")
    r = put(c, SETTING, "0" if current else "1")
    entry = r.json().get("setting", {}) if r.status_code == 200 else {}
    check("the change: 200, changed, source board", r.status_code == 200
          and r.json().get("changed") is True and entry.get("source") == "board",
          f"{r.status_code} {r.text[:200]}")
    check("...one node.config.set event, and the grant is spent",
          config_events() == 1 and live_grants() == 0, f"events={config_events()} grants={live_grants()}")
    r = put(c, SETTING, None)
    check("a second change needs a second step-up (403 step_up_required)",
          r.status_code == 403 and r.json().get("error") == "step_up_required" and config_events() == 1)
    step_up(c)
    r = put(c, SETTING, "0" if current else "1")
    check("the board value again: 200 changed=false, no event, and the grant is kept",
          r.status_code == 200 and r.json().get("changed") is False and config_events() == 1
          and live_grants() == 1, f"{r.status_code} {r.text[:120]} grants={live_grants()}")
    with app.session() as s:
        s.execute(text("DELETE FROM auth_step_up"))
        s.commit()


def section_e() -> None:
    print("\n-- E. the grant is bound to its purpose --")
    c = owner_session()
    step_up(c, config=OTHER)
    r = put(c, SETTING, None)
    check("a grant for another setting: 403 step_up_setting_mismatch, kept",
          r.status_code == 403 and r.json().get("error") == "step_up_setting_mismatch"
          and live_grants() == 1 and last_fail_reason() == "config_write:step_up_setting_mismatch",
          f"{r.status_code} {r.text[:160]} {last_fail_reason()}")
    r = mint(c, "claude-code")
    check("a configuration grant cannot mint an agent credential (403 step_up_required, kept)",
          r.status_code == 403 and r.json().get("error") == "step_up_required" and live_grants() == 1
          and last_fail_reason() == "agent_credential:step_up_purpose_mismatch",
          f"{r.status_code} {r.text[:160]} {last_fail_reason()}")
    listing = c.get("/api/me/agent-credentials").json()
    check("...and the credential screen does not show it as a confirmed credential",
          listing.get("step_up_agent") is None and listing.get("step_up_seconds_left") == 0, str(listing))
    r = put(c, OTHER, "173")
    check("...the setting it approved then changes (200), spending it",
          r.status_code == 200 and r.json().get("changed") is True and live_grants() == 0,
          f"{r.status_code} {r.text[:160]}")

    step_up(c, purpose="claude-code")
    r = put(c, SETTING, None)
    check("an agent-credential grant cannot change a setting (403 step_up_required, kept)",
          r.status_code == 403 and r.json().get("error") == "step_up_required" and live_grants() == 1
          and last_fail_reason() == "config_write:step_up_purpose_mismatch",
          f"{r.status_code} {r.text[:160]} {last_fail_reason()}")
    check("...GET shows no confirmed setting", writes(c).get("step_up_setting") is None)
    r = mint(c, "claude-code")
    check("...and the credential it approved then mints (201)",
          r.status_code == 201 and r.json().get("agent") == "kc-olga-claude-code", r.text[:160])

    step_up(c)
    with app.session() as s:
        s.execute(text("UPDATE auth_step_up SET expires_at = now() - interval '1 second'"))
        s.commit()
    r = put(c, SETTING, None)
    check("a grant past its window does not change a setting (403 step_up_required)",
          r.status_code == 403 and r.json().get("error") == "step_up_required", r.text[:120])


def section_f() -> None:
    print("\n-- F. nothing but the session that stepped up --")
    import uuid
    c = owner_session()
    with app.session() as s:
        olga = s.scalar(select(AuthUser).where(AuthUser.subject == OWNER_SUB))
        # A token belongs to an is_service row; this one is the owner's own agent.
        bot = AuthUser(id=str(uuid.uuid4()), subject="kc-olga-cfg-bot", label="kc-olga-cfg-bot",
                       role="owner", is_service=True, must_change_password=False,
                       person_user_id=olga.id, created_at=datetime.now(timezone.utc))
        s.add(bot)
        s.flush()
        raw, _row = auth.mint_service_token(s, owner=bot, scopes=["admin"], label="cfg")
        s.commit()
    step_up(c)
    b = client(headers={"Authorization": "Bearer " + raw})
    r = b.put("/api/config/" + SETTING, json={"value": None})
    check("the owner's admin token cannot change a setting, even with a live grant "
          "(403 session_required, kept)",
          r.status_code == 403 and r.json().get("error") == "session_required" and live_grants() == 1,
          f"{r.status_code} {r.text[:120]}")
    other = sso_login(OWNER_SUB)
    r = put(other, SETTING, None)
    check("another session of the same owner cannot spend it (403 step_up_required, kept)",
          r.status_code == 403 and r.json().get("error") == "step_up_required" and live_grants() == 1,
          f"{r.status_code} {r.text[:120]}")
    with app.session() as s:
        s.execute(text("DELETE FROM auth_step_up"))
        s.commit()


def section_g() -> None:
    print("\n-- G. local owners keep the password, unchanged (step-up on) --")
    reset_limits()
    mk_local_owner("lou")
    c = client()
    page = c.get("/auth/login")
    tok = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    r = c.post("/auth/login", data={"username": "lou", "password": PW, "csrf_token": tok},
               headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
    check("(lou, a local owner, signs in with a password)", r.status_code == 303)
    w = writes(c)
    check("GET says step_up=password and carries no step-up keys",
          w.get("step_up") == "password" and not any(k.startswith("step_up_") for k in w), str(w))
    r = put(c, SETTING, None)
    check("no password: 403 step_up_required naming password", r.status_code == 403
          and r.json() == {"error": "step_up_required", "field": "password",
                           "detail": "Changing configuration needs your password, every time."},
          r.text[:160])
    r = put(c, SETTING, None, password="wrong-" + PW)
    check("a wrong password: 403 password_incorrect",
          r.status_code == 403 and r.json().get("error") == "password_incorrect")
    r = put(c, SETTING, None, password=PW)
    check("the right password: 200", r.status_code == 200, r.text[:160])
    r, loc = start(c, config=SETTING)
    check("a local owner cannot start a step-up (400 step_up_not_applicable)",
          r.status_code == 400 and r.json().get("error") == "step_up_not_applicable" and not loc)


def main() -> int:
    app.startup()
    try:
        setup()
        section_a()
        section_b()
        section_c()
        section_d()
        section_e()
        section_f()
        section_g()
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
