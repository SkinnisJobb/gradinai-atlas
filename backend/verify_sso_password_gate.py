"""
Verification for SSO sign-ins and a pending password change (TASK-1442, OSS-26 M):
ATLAS_OIDC_STEP_UP=on, against the STUB IdP of verify_oidc_login.py (no network).

An operator makes an account the usual way (`atlas-admin user add <name>`, which
prints a temporary password and sets `must_change_password`), then moves it to SSO
(`user link-idp`). The flag is KEPT (owner decision, 2026-10-29): a personal password stays
possible as a fallback. Before this task the SSO session was sent to the forced
password-change page, which asks for a temporary password the person was never
meant to use.

  A. AN SSO SESSION WITH THE FLAG GETS IN: the login lands on the board, not on
     /auth/password/change; /api/me answers 200 with must_change_password false and
     `password_setup` (step-up sso, nothing confirmed); /board and an API read are
     200. The flag is still set in the database. Since TASK-1454 the temporary
     password DOES sign the linked account in, under its local name, and that
     password session is held by the gate (the gate reads how a session began,
     not the account; verify_linked_password_fallback.py has the rest).
  B. WITHOUT A STEP-UP the password is not set: POST /api/me/password is 403
     step_up_required naming the route, a `login_fail` password_set:step_up_required,
     and the flag stays.
  C. A STALE STEP-UP (auth_time older than the window) is refused at the callback,
     and the set is still refused.
  D. A FRESH STEP-UP (?password=1): the start and callback pages say what is being
     approved; /api/me shows the time left; a mismatched confirmation and a weak
     password are 400 and keep the grant; then 200: the flag is cleared, a verifier
     is stored, the grant is spent, `password_changed` reason sso_step_up, the
     session keeps working and another session of the account is ended; /api/me has
     no `password_setup` any more; a second set is 400 password_not_pending.
  E. THE GRANT IS BOUND TO ITS PURPOSE: an agent-credential step-up cannot set a
     password (403 step_up_required, password_set:step_up_purpose_mismatch, kept),
     and a password step-up cannot mint a credential (kept, then spent by the set);
     another session of the same person cannot spend it; a token, even admin-scoped,
     is 403 session_required.
  F. NOTHING ELSE CHANGES: an SSO-only account (`user add --no-password`, link-idp)
     has no `password_setup`, cannot start a password step-up (400
     password_not_pending) and cannot set one; a LOCAL account signing in with its
     temporary password is still forced (303 to the change page, /api/me 403
     password_change_required, /board 302, POST /api/me/password held by the gate);
     a local account without the flag is 400 step_up_not_applicable.

Installs without OIDC: the gate change reads `idp`, and such an install has only
local accounts; verify_object_graph.py / verify_identity_seam.py keep proving the
forced change there.

Run:  python verify_sso_password_gate.py
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
import uuid
from datetime import datetime, timedelta, timezone
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
NEW_PW = "Quiet-Lantern-Harbour-Seven-42!"
LOCAL_PW = "Correct-Horse-Battery-Gate-8!"
TMP = tempfile.mkdtemp(prefix="atlas-sso-password-gate-")
WINDOW = 120

for _k in [k for k in os.environ if k.startswith(("ATLAS_AUTH_PROVIDERS", "ATLAS_LOCAL_LOGIN",
                                                    "ATLAS_OIDC_"))]:
    os.environ.pop(_k)
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
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
NEXT = "/board?setpw=1"
PAM_SUB = "kc-pam"          # user add pam (temporary password), link-idp: the case
PIA_SUB = "kc-pia"          # the same, for section E
SAM_SUB = "kc-sam"          # user add sam --no-password, link-idp: SSO-only
APPROVAL = "You are setting a personal Atlas password for <strong>%s@example.com</strong>"
_CONTINUE = re.compile(r'id="stepUpContinue" href="([^"]+)"')
TEMP = {}


def client(**kw):
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN, **kw)


def reset_limits() -> None:
    for ip in ("testclient", "127.0.0.1", ""):
        auth._IP_LIMITER.reset(auth._login_key(ip))
        auth._IP_LIMITER.reset(auth._bearer_key(ip))


def last_fail_reason() -> str:
    with app.session() as s:
        row = s.scalars(select(AuthEvent).where(AuthEvent.event == "login_fail")
                        .order_by(AuthEvent.id.desc()).limit(1)).first()
        return row.reason if row is not None else ""


def sso_login_raw(sub: str):
    """(client, the callback's response) for an SSO login of `sub`."""
    reset_limits()
    c = client()
    r = c.get("/auth/oidc/start", follow_redirects=False)
    cb = STUB.authorize(r.headers.get("location", ""), {"sub": sub, "groups": GROUPS_OK,
                                                        "email": sub + "@example.com"})
    return c, c.get(cb, follow_redirects=False)


def sso_login(sub: str):
    c, r = sso_login_raw(sub)
    assert r.status_code == 303 and c.get("/api/me").status_code == 200, (sub, last_fail_reason())
    return c


def start(c, **params):
    r = c.get(STEP_UP, params={"next": NEXT, **params}, follow_redirects=False)
    m = _CONTINUE.search(r.text) if r.status_code == 200 else None
    return r, html.unescape(m.group(1)) if m else ""


def step_up(c, sub: str, *, auth_time=None, **params):
    """Start a step-up in `c` (password=1 unless told otherwise), sign in at the stub."""
    _r, loc = start(c, **(params or {"password": "1"}))
    claims = {"sub": sub, "groups": GROUPS_OK, "email": sub + "@example.com",
              "auth_time": int(time.time()) if auth_time is None else auth_time}
    return c.get(STUB.authorize(loc, claims), follow_redirects=False)


def me(c) -> dict:
    return c.get("/api/me").json()


def set_pw(c, new: str = NEW_PW, confirm=None):
    return c.post("/api/me/password", headers={"Origin": TEST_ORIGIN}, json={
        "new_password": new, "confirm_password": new if confirm is None else confirm,
        "csrf_token": me(c)["csrf_token"]})


def mint(c, purpose: str):
    return c.post("/api/me/agent-credential", headers={"Origin": TEST_ORIGIN}, json={
        "purpose": purpose, "scopes": ["read"], "expires_days": 30,
        "csrf_token": me(c)["csrf_token"]})


def user(sub: str) -> AuthUser:
    with app.session() as s:
        row = s.scalar(select(AuthUser).where(AuthUser.subject == sub))
        s.expunge(row)
        return row


def live_grants() -> int:
    with app.session() as s:
        return s.scalar(text("SELECT count(*) FROM auth_step_up WHERE auth_time IS NOT NULL"))


def clear_grants() -> None:
    with app.session() as s:
        s.execute(text("DELETE FROM auth_step_up"))
        s.commit()


def run_admin(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            rc = admin.main(list(argv)) or 0
        except SystemExit as exc:
            rc = int(exc.code or 0)
    return rc, out.getvalue()


def printed_secret(output: str) -> str:
    """The temporary password `user add` printed once (an indented line of its own)."""
    for line in output.splitlines():
        if line.startswith("      ") and line.strip() and " " not in line.strip():
            return line.strip()
    return ""


def password_login(username: str, password: str):
    reset_limits()
    c = client()
    page = c.get("/auth/login")
    tok = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    r = c.post("/auth/login", data={"username": username, "password": password, "csrf_token": tok},
               headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
    return c, r


def html_get(c, path: str):
    return c.get(path, headers={"Accept": "text/html"}, follow_redirects=False)


# --------------------------------------------------------------------------- #
def setup() -> None:
    print("\n-- setup: accounts made the operator's way, then moved to SSO --")
    for name, sub in (("pam", PAM_SUB), ("pia", PIA_SUB)):
        rc, out = run_admin("user", "add", name, "--role", "member")
        TEMP[name] = printed_secret(out)
        rc2, _ = run_admin("user", "link-idp", name, "--subject", sub)
        u = user(sub)
        check(f"{name}: user add (temporary password) then link-idp; the flag is kept",
              rc == 0 and rc2 == 0 and bool(TEMP[name]) and u.idp == "oidc"
              and u.must_change_password is True and u.password_hash is not None,
              f"rc={rc}/{rc2} flag={u.must_change_password}")
    rc, _ = run_admin("user", "add", "sam", "--role", "member", "--no-password")
    rc2, _ = run_admin("user", "link-idp", "sam", "--subject", SAM_SUB)
    u = user(SAM_SUB)
    check("sam: user add --no-password then link-idp (SSO-only, no flag)",
          rc == 0 and rc2 == 0 and u.must_change_password is False and u.password_hash is None)


def section_a() -> None:
    print("\n-- A. an SSO session with the flag set reaches the board --")
    c, r = sso_login_raw(PAM_SUB)
    check("the SSO login lands where it was going, not on /auth/password/change",
          r.status_code == 303 and r.headers.get("location") != "/auth/password/change",
          f"{r.status_code} -> {r.headers.get('location')}")
    m = c.get("/api/me")
    body = m.json() if m.status_code == 200 else {}
    setup_ = body.get("password_setup") or {}
    check("/api/me: 200, must_change_password false, password_setup (sso, nothing confirmed)",
          m.status_code == 200 and body.get("must_change_password") is False
          and setup_.get("step_up") == "sso" and setup_.get("step_up_url") == STEP_UP
          and setup_.get("step_up_seconds_left") == 0 and setup_.get("step_up_max_age") == WINDOW,
          f"{m.status_code} {m.text[:240]}")
    b = html_get(c, "/board")
    check("GET /board is 200 (no redirect to the change page)", b.status_code == 200,
          f"{b.status_code} -> {b.headers.get('location')}")
    g = c.get("/api/graph?depth=1")
    check("an API read is 200", g.status_code == 200, f"{g.status_code} {g.text[:120]}")
    check("the database still holds must_change_password for pam",
          user(PAM_SUB).must_change_password is True)
    pc, r = password_login("pam", TEMP["pam"])
    check("the linked account's temporary password signs in under its local name and is "
          "sent to the change page (TASK-1454: a password session is held)",
          r.status_code == 303 and r.headers.get("location") == "/auth/password/change"
          and pc.get("/api/me").status_code == 403,
          f"{r.status_code} -> {r.headers.get('location')}")
    check("...while the SSO session of the same account is still not held",
          c.get("/api/me").status_code == 200)


def section_b() -> None:
    print("\n-- B. without a step-up the password is not set --")
    c = sso_login(PAM_SUB)
    r = set_pw(c)
    check("POST /api/me/password with no step-up: 403 step_up_required naming the route",
          r.status_code == 403 and r.json().get("error") == "step_up_required"
          and r.json().get("step_up_url") == STEP_UP, f"{r.status_code} {r.text[:160]}")
    check("...a login_fail password_set:step_up_required",
          last_fail_reason() == "password_set:step_up_required", last_fail_reason())
    check("...and the flag stays", user(PAM_SUB).must_change_password is True)


def section_c() -> None:
    print("\n-- C. a stale step-up is refused --")
    c = sso_login(PAM_SUB)
    r = step_up(c, PAM_SUB, auth_time=int(time.time()) - WINDOW - 60)
    check("a stale auth_time: the callback is a 403 page and no grant is live",
          r.status_code == 403 and live_grants() == 0
          and "oidc+step-up:auth_time" in last_fail_reason(), f"{r.status_code} {last_fail_reason()}")
    r = set_pw(c)
    check("...and the set is still refused (403 step_up_required)",
          r.status_code == 403 and r.json().get("error") == "step_up_required"
          and user(PAM_SUB).must_change_password is True)


def section_d() -> None:
    print("\n-- D. a fresh step-up sets the password, once, and clears the flag --")
    other = sso_login(PAM_SUB)            # a second session of the same person
    c = sso_login(PAM_SUB)
    r, loc = start(c, password="1")
    check("the start page names what is approved and the account",
          r.status_code == 200 and loc.startswith(STUB.authorization_endpoint)
          and APPROVAL % PAM_SUB in r.text, f"{r.status_code} {r.text[:200]}")
    with app.session() as s:
        pending = s.execute(text("SELECT agent FROM auth_step_up WHERE auth_time IS NULL")).scalars().all()
    check("the pending grant is %s" % auth.PASSWORD_GRANT, auth.PASSWORD_GRANT in pending, str(pending))
    r = step_up(c, PAM_SUB)
    check("the callback page says again what was approved, with no new session cookie",
          r.status_code == 200 and APPROVAL % PAM_SUB in r.text
          and auth.SESSION_COOKIE not in r.headers.get("set-cookie", ""),
          f"{r.status_code} {last_fail_reason()} {r.text[:200]}")
    with app.session() as s:
        ok = s.scalars(select(AuthEvent).where(AuthEvent.event == "step_up_ok")
                       .order_by(AuthEvent.id.desc()).limit(1)).first()
    check("a step_up_ok with detail password=set", ok is not None and ok.detail == "password=set",
          str(ok.detail if ok else None))
    s_ = me(c).get("password_setup") or {}
    check("/api/me shows the time left", 0 < s_.get("step_up_seconds_left", 0) <= WINDOW, str(s_))
    r = set_pw(c, NEW_PW, NEW_PW + "x")
    check("a mismatched confirmation: 400 password_mismatch, the grant kept",
          r.status_code == 400 and r.json().get("error") == "password_mismatch" and live_grants() == 1,
          f"{r.status_code} {r.text[:120]}")
    r = set_pw(c, "short")
    check("a password the policy refuses: 400 password_policy, the grant kept",
          r.status_code == 400 and r.json().get("error") == "password_policy" and live_grants() == 1,
          f"{r.status_code} {r.text[:120]}")
    r = set_pw(c)
    u = user(PAM_SUB)
    check("the set: 200, the flag cleared, a new verifier, the temp expiry gone",
          r.status_code == 200 and r.json().get("changed") is True
          and u.must_change_password is False and u.temp_expires_at is None
          and passwords.verify_password(u.password_hash, NEW_PW).ok
          and not passwords.verify_password(u.password_hash, TEMP["pam"]).ok,
          f"{r.status_code} {r.text[:160]}")
    check("...the grant is spent", live_grants() == 0)
    with app.session() as s:
        ev = s.scalars(select(AuthEvent).where(AuthEvent.event == "password_changed",
                                               AuthEvent.user_id == u.id)).all()
    check("...one password_changed event, reason sso_step_up",
          [e.reason for e in ev] == ["sso_step_up"], str([e.reason for e in ev]))
    body = me(c)
    check("...this session still works, and /api/me has no password_setup any more",
          "password_setup" not in body and body.get("must_change_password") is False, str(body)[:200])
    check("...the account's other session is ended", other.get("/api/me").status_code == 401)
    r = set_pw(c)
    check("a second set: 400 password_not_pending",
          r.status_code == 400 and r.json().get("error") == "password_not_pending", r.text[:120])
    r, loc = start(c, password="1")
    check("...and a password step-up cannot be started any more (400 password_not_pending)",
          r.status_code == 400 and r.json().get("error") == "password_not_pending" and not loc)


def section_e() -> None:
    print("\n-- E. the grant is bound to its purpose and its session --")
    clear_grants()
    c = sso_login(PIA_SUB)
    step_up(c, PIA_SUB, purpose="claude-code")
    r = set_pw(c)
    check("an agent-credential grant cannot set a password (403 step_up_required, kept)",
          r.status_code == 403 and r.json().get("error") == "step_up_required" and live_grants() == 1
          and last_fail_reason() == "password_set:step_up_purpose_mismatch",
          f"{r.status_code} {r.text[:160]} {last_fail_reason()}")
    s_ = me(c).get("password_setup") or {}
    check(".../api/me shows no confirmed password step-up", s_.get("step_up_seconds_left") == 0, str(s_))
    clear_grants()

    step_up(c, PIA_SUB)
    r = mint(c, "claude-code")
    check("a password grant cannot mint an agent credential (403 step_up_required, kept)",
          r.status_code == 403 and r.json().get("error") == "step_up_required" and live_grants() == 1
          and last_fail_reason() == "agent_credential:step_up_purpose_mismatch",
          f"{r.status_code} {r.text[:160]} {last_fail_reason()}")
    listing = c.get("/api/me/agent-credentials").json()
    check("...the credential screen does not show it as a confirmed credential",
          listing.get("step_up_agent") is None and listing.get("step_up_seconds_left") == 0, str(listing))
    other = sso_login(PIA_SUB)
    r = set_pw(other)
    check("another session of the same person cannot spend it (403 step_up_required, kept)",
          r.status_code == 403 and r.json().get("error") == "step_up_required" and live_grants() == 1,
          f"{r.status_code} {r.text[:120]}")
    pia = user(PIA_SUB)
    with app.session() as s:
        bot = AuthUser(id=str(uuid.uuid4()), subject="kc-pia-pw-bot", label="kc-pia-pw-bot",
                       role="member", is_service=True, must_change_password=False,
                       person_user_id=pia.id, created_at=datetime.now(timezone.utc))
        s.add(bot)
        s.flush()
        raw, _row = auth.mint_service_token(s, owner=bot, scopes=["admin"], label="pw")
        s.commit()
    b = client(headers={"Authorization": "Bearer " + raw})
    r = b.post("/api/me/password", json={"new_password": NEW_PW, "confirm_password": NEW_PW})
    check("an admin-scoped token cannot set a password (403 session_required, kept)",
          r.status_code == 403 and r.json().get("error") == "session_required" and live_grants() == 1,
          f"{r.status_code} {r.text[:120]}")
    r = set_pw(c)
    check("...the session that stepped up then sets it (200), spending the grant",
          r.status_code == 200 and live_grants() == 0 and user(PIA_SUB).must_change_password is False,
          f"{r.status_code} {r.text[:120]}")
    with app.session() as s:
        s.execute(text("UPDATE auth_user SET must_change_password = true WHERE subject = :s"),
                  {"s": PIA_SUB})
        s.commit()
    step_up(c, PIA_SUB)
    with app.session() as s:
        s.execute(text("UPDATE auth_step_up SET expires_at = now() - interval '1 second'"))
        s.commit()
    r = set_pw(c)
    check("a grant past its window does not set a password (403 step_up_required)",
          r.status_code == 403 and r.json().get("error") == "step_up_required", r.text[:120])
    clear_grants()


def section_f() -> None:
    print("\n-- F. nothing else changes --")
    c = sso_login(SAM_SUB)
    body = me(c)
    check("an SSO-only account (--no-password) has no password_setup",
          "password_setup" not in body and body.get("must_change_password") is False, str(body)[:200])
    r, loc = start(c, password="1")
    check("...cannot start a password step-up (400 password_not_pending)",
          r.status_code == 400 and r.json().get("error") == "password_not_pending" and not loc,
          f"{r.status_code} {r.text[:120]}")
    r = set_pw(c)
    check("...and cannot set one (400 password_not_pending), nothing stored",
          r.status_code == 400 and r.json().get("error") == "password_not_pending"
          and user(SAM_SUB).password_hash is None, f"{r.status_code} {r.text[:120]}")

    with app.session() as s:
        s.add(AuthUser(id=str(uuid.uuid4()), subject="lou", name="Lou", role="member",
                       is_service=False, must_change_password=True,
                       password_hash=passwords.hash_password(LOCAL_PW),
                       temp_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                       created_at=datetime.now(timezone.utc)))
        s.add(AuthUser(id=str(uuid.uuid4()), subject="lea", name="Lea", role="member",
                       is_service=False, must_change_password=False,
                       password_hash=passwords.hash_password(LOCAL_PW),
                       created_at=datetime.now(timezone.utc)))
        s.commit()
    c, r = password_login("lou", LOCAL_PW)
    check("a LOCAL sign-in with the temporary password is still sent to the change page",
          r.status_code == 303 and r.headers.get("location") == "/auth/password/change",
          f"{r.status_code} -> {r.headers.get('location')}")
    m = c.get("/api/me")
    check("...its /api/me is 403 password_change_required",
          m.status_code == 403 and m.json().get("error") == "password_change_required", m.text[:160])
    b = html_get(c, "/board")
    check("...GET /board is 302 to /auth/password/change",
          b.status_code == 302 and b.headers.get("location") == "/auth/password/change",
          f"{b.status_code} -> {b.headers.get('location')}")
    r = c.post("/api/me/password", headers={"Origin": TEST_ORIGIN},
               json={"new_password": NEW_PW, "confirm_password": NEW_PW})
    check("...and POST /api/me/password is held by the gate (403 password_change_required)",
          r.status_code == 403 and r.json().get("error") == "password_change_required", r.text[:160])
    check("...the flag stays", user("lou").must_change_password is True)
    c, r = password_login("lea", LOCAL_PW)
    r = set_pw(c)
    check("a local account without the flag: 400 step_up_not_applicable",
          r.status_code == 400 and r.json().get("error") == "step_up_not_applicable", r.text[:160])


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
