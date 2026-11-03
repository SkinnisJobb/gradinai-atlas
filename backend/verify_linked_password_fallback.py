"""
Verification for a linked account's personal password as a fallback (TASK-1454, OSS-26 N),
against the STUB IdP of verify_oidc_login.py (no network).

Owner decision, 2026-10-29: a linked account keeps a LOCAL SIGN-IN NAME (the pre-link username,
`auth_user_idp_state.local_name`), and the password form accepts it, under ATLAS_LOCAL_LOGIN:
`all` (the "on" of the decision) any such account with a password, `break-glass` a flagged
one only, each use its own audit row. Only with `local` AND `oidc` in ATLAS_AUTH_PROVIDERS;
otherwise unchanged. Because a linked account can now hold a PASSWORD session, the session
records how it began (`auth_session.auth_method`), and the forced-change gate reads that,
not the account's `idp` (the invariant #219 relied on).

  A. ON (local,oidc + all): a linked account with a personal password signs in with its
     local name (any case) and lands where it was going; /api/me 200, not forced; the
     session is `auth_method='password'`; `login_ok` says `local_name=`. A wrong password,
     and the IdP subject typed as a username, fail generically.
  B. THE GATE: `identity.forced_change_applies` over every (account, method), and
     `session_auth_method` for a row older than the column. A linked account signing in
     with its TEMPORARY password is held (303 to the change page, /api/me 403, /board 302)
     while its SSO session at the same moment is not (200, password_setup); a legacy NULL
     row reads as before (a linked account's SSO, a local one's password). The change
     with the temporary password works, keeps the session a password one, and the new
     password then signs in.
  C. `--no-password` (SSO-only) accounts: a local name, still no password: refused.
  D. THE NAME: `link-idp --no-local-name` keeps none (refused); `user set-local-name`
     gives one (audited) and the password signs in; collisions refused there and in
     `user add` / `bot add`; a local account always wins its own name, even against a
     linked account whose local name was forced onto it in the database; `--clear`
     takes it away; `user list` / `user show` show it, and a local account's JSON is
     unchanged.
  E. BREAK-GLASS (local,oidc + break-glass): a linked account that is not flagged is
     refused (not_break_glass); a flagged one signs in under its local name, and every
     use is its own `login_break_glass` row.
  F. OIDC ALONE (break-glass implied): no linked account signs in with a password.
  G. NO OIDC (local only): a local name is not accepted, and the side table is not read
     at all (renamed away for the duration, the login still answers the same).

Run:  python verify_linked_password_fallback.py
"""
from __future__ import annotations

import base64
import contextlib
import dataclasses
import io
import os
import sys
import tempfile
import uuid
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
PERSONAL_PW = "Amber-Ledger-Quietly-Rowing-61!"
CHANGED_PW = "Slate-Orchard-Winter-Compass-27!"
LOCAL_PW = "Correct-Horse-Battery-Gate-8!"
TMP = tempfile.mkdtemp(prefix="atlas-linked-fallback-")

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
os.environ.update(STUB.env(TMP, ATLAS_OIDC_REQUIRED_GROUPS="atlas-users",
                           ATLAS_OIDC_GROUP_MAP_FILE=GROUP_MAP, ATLAS_OIDC_STEP_UP="on"))

import identity.oidc  # noqa: E402

identity.oidc.use_transport(STUB.transport)

from sqlalchemy import select, text  # noqa: E402

import admin  # noqa: E402
import app  # noqa: E402
import auth  # noqa: E402
import identity  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthEvent, AuthSession, AuthUser, AuthUserIdpState  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


GROUPS_OK = ["atlas-users"]
TEMP: dict = {}
SUBS = {"ned": "kc-ned", "nia": "kc-nia", "sid": "kc-sid", "oli": "kc-oli", "bgl": "kc-bgl",
        "tia": "kc-tia"}


@contextlib.contextmanager
def configured(**changes):
    """ATLAS_AUTH_PROVIDERS / ATLAS_LOCAL_LOGIN as an install would set them, for a section.
    Every reader asks `identity.SETTINGS` per request, so swapping it is the setting."""
    prev = identity.SETTINGS
    identity.SETTINGS = dataclasses.replace(prev, **changes)
    try:
        yield
    finally:
        identity.SETTINGS = prev


def client(**kw):
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN, **kw)


def reset_limits() -> None:
    for ip in ("testclient", "127.0.0.1", ""):
        auth._IP_LIMITER.reset(auth._login_key(ip))
        auth._IP_LIMITER.reset(auth._bearer_key(ip))
    with app.session() as s:
        s.execute(text("DELETE FROM auth_login_throttle"))
        s.commit()


def last_event(name: str):
    with app.session() as s:
        row = s.scalars(select(AuthEvent).where(AuthEvent.event == name)
                        .order_by(AuthEvent.id.desc()).limit(1)).first()
        if row is not None:
            s.expunge(row)
        return row


def last_fail_reason() -> str:
    row = last_event("login_fail")
    return row.reason if row is not None else ""


def count_events(name: str, user_id: str) -> int:
    with app.session() as s:
        return len(s.scalars(select(AuthEvent.id).where(AuthEvent.event == name,
                                                        AuthEvent.user_id == user_id)).all())


def run_admin(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = admin.main(list(argv)) or 0
        except SystemExit as exc:
            rc = int(exc.code or 0)
    return rc, out.getvalue(), err.getvalue()


def printed_secret(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("      ") and line.strip() and " " not in line.strip():
            return line.strip()
    return ""


def user(sub: str) -> AuthUser:
    with app.session() as s:
        row = s.scalar(select(AuthUser).where(AuthUser.subject == sub))
        s.expunge(row)
        return row


def set_password(sub: str, pw: str) -> None:
    """A personal password, as `POST /api/me/password` leaves it (verify_sso_password_gate D)."""
    with app.session() as s:
        u = s.scalar(select(AuthUser).where(AuthUser.subject == sub))
        u.password_hash = passwords.hash_password(pw)
        u.hash_scheme = passwords.HASH_SCHEME
        u.must_change_password = False
        u.temp_expires_at = None
        s.commit()


def password_login(username: str, password: str, *, query: str = "", next_path: str = ""):
    reset_limits()
    c = client()
    page = c.get("/auth/login" + query)
    tok = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    data = {"username": username, "password": password, "csrf_token": tok}
    if next_path:
        data["next"] = next_path
    r = c.post("/auth/login", data=data, headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
    return c, r


def failed(r) -> bool:
    return r.status_code == 303 and r.headers.get("location", "").startswith("/auth/login?error")


def sso_login(sub: str):
    reset_limits()
    c = client()
    r = c.get("/auth/oidc/start", follow_redirects=False)
    cb = STUB.authorize(r.headers.get("location", ""), {"sub": sub, "groups": GROUPS_OK,
                                                        "email": sub + "@example.com"})
    r = c.get(cb, follow_redirects=False)
    assert r.status_code == 303 and c.get("/api/me").status_code == 200, (sub, last_fail_reason())
    return c


def html_get(c, path: str):
    return c.get(path, headers={"Accept": "text/html"}, follow_redirects=False)


def session_method(c) -> str:
    sid = c.cookies.get(auth.SESSION_COOKIE)
    with app.session() as s:
        row = s.get(AuthSession, auth._session_digest(sid)) if sid else None
        return row.auth_method if row is not None else "(no session)"


# --------------------------------------------------------------------------- #
def setup() -> None:
    print("\n-- setup: accounts made the operator's way, then moved to SSO --")
    for name in ("ned", "nia", "tia"):
        rc, out, _e = run_admin("user", "add", name, "--role", "member")
        TEMP[name] = printed_secret(out)
        rc2, out2, err2 = run_admin("user", "link-idp", name, "--subject", SUBS[name])
        with app.session() as s:
            state = s.get(AuthUserIdpState, user(SUBS[name]).id)
            kept = state.local_name if state is not None else None
        check(f"{name}: user add, link-idp; the pre-link username is kept as the local name",
              rc == 0 and rc2 == 0 and kept == name and "Local sign-in name kept: %s" % name in out2,
              f"rc={rc}/{rc2} local_name={kept} {err2.strip()[:120]}")
    ev = last_event("user_idp_linked")
    check("...and the link's audit row names it", ev is not None and "local_name=tia" in (ev.detail or ""),
          str(ev.detail if ev else None))
    set_password(SUBS["ned"], PERSONAL_PW)
    rc, _o, _e = run_admin("user", "add", "sid", "--role", "member", "--no-password")
    rc2, _o, _e = run_admin("user", "link-idp", "sid", "--subject", SUBS["sid"])
    check("sid: user add --no-password, link-idp (SSO-only, local name kept, no password)",
          rc == 0 and rc2 == 0 and user(SUBS["sid"]).password_hash is None)
    rc, _o, _e = run_admin("user", "add", "oli", "--role", "member")
    rc2, out2, _e = run_admin("user", "link-idp", "oli", "--subject", SUBS["oli"], "--no-local-name")
    with app.session() as s:
        st = s.get(AuthUserIdpState, user(SUBS["oli"]).id)
    check("oli: link-idp --no-local-name keeps no local name",
          rc == 0 and rc2 == 0 and (st is None or st.local_name is None) and "--no-local-name" in out2,
          out2.strip()[:160])
    set_password(SUBS["oli"], PERSONAL_PW)
    rc, _o, _e = run_admin("user", "add", "bgl", "--role", "owner", "--break-glass")
    rc2, _o, _e = run_admin("user", "link-idp", "bgl", "--subject", SUBS["bgl"])
    check("bgl: a break-glass owner, then linked; the flag and the local name both kept",
          rc == 0 and rc2 == 0)
    set_password(SUBS["bgl"], PERSONAL_PW)
    with app.session() as s:
        for name, flag in (("zed", False), ("lou", True)):
            s.add(AuthUser(id=str(uuid.uuid4()), subject=name, name=name.title(), role="member",
                           is_service=False, must_change_password=flag,
                           password_hash=passwords.hash_password(LOCAL_PW),
                           created_at=datetime.now(timezone.utc)))
        s.commit()


def section_a() -> None:
    print("\n-- A. ATLAS_LOCAL_LOGIN=all (local,oidc): the personal password signs in --")
    ned = user(SUBS["ned"])
    c, r = password_login("ned", PERSONAL_PW, next_path="/board")
    check("ned signs in with his local name and personal password: 303 where he was going",
          r.status_code == 303 and r.headers.get("location") == "/board",
          f"{r.status_code} -> {r.headers.get('location')} {last_fail_reason()}")
    m = c.get("/api/me")
    body = m.json() if m.status_code == 200 else {}
    check("...onto HIS account: /api/me 200, his id, not forced",
          m.status_code == 200 and body.get("user_id") == ned.id
          and body.get("must_change_password") is False, f"{m.status_code} {m.text[:160]}")
    check("...the session records it was a password sign-in", session_method(c) == "password",
          session_method(c))
    check("...GET /board is 200", html_get(c, "/board").status_code == 200)
    ok = last_event("login_ok")
    check("...login_ok, reason ok, detail local_name=ned",
          ok is not None and ok.user_id == ned.id and ok.reason == "ok"
          and ok.detail == "local_name=ned", f"{ok.reason if ok else None} {ok.detail if ok else None}")
    c, r = password_login("NED", PERSONAL_PW)
    check("the name is normalised like a username (NED signs in too)", r.status_code == 303
          and not failed(r), r.headers.get("location", ""))
    c, r = password_login("ned", PERSONAL_PW + "x")
    check("a wrong password: the generic failure, login_fail bad_password:*",
          failed(r) and last_fail_reason().startswith("bad_password"), last_fail_reason())
    c, r = password_login(SUBS["ned"], PERSONAL_PW)
    check("the IdP subject is not a sign-in name (unknown_user)",
          failed(r) and last_fail_reason() == "unknown_user", last_fail_reason())


def section_b() -> None:
    print("\n-- B. the gate keys on how the session began, not on the account --")
    nia = user(SUBS["nia"])
    lou = user("lou")
    fca = identity.forced_change_applies
    cases = [
        (lou, "password", True), (lou, "sso", True), (lou, None, True), (lou, "minted", True),
        (nia, "sso", False), (nia, "password", True), (nia, "minted", True), (nia, None, True),
        (nia, "something-new", True),
        (user(SUBS["ned"]), "password", False), (user("zed"), "password", False),
    ]
    bad = [(u.subject, m, want) for u, m, want in cases if fca(u, m) is not want]
    check("forced_change_applies: a local account always, a linked one unless SSO, "
          "nobody without the flag", not bad, str(bad))
    legacy = AuthSession(id_hash="x", kind="full", user_id=nia.id, csrf_hash="x", auth_method=None,
                         absolute_expires_at=datetime.now(timezone.utc))
    check("session_auth_method: a NULL row reads as it did (linked: sso, local: password)",
          auth.session_auth_method(legacy, nia) == "sso"
          and auth.session_auth_method(legacy, lou) == "password"
          and auth.session_auth_method(AuthSession(auth_method="password"), nia) == "password")

    sso = sso_login(SUBS["nia"])
    c, r = password_login("nia", TEMP["nia"])
    check("nia (linked, temporary password) signs in with it under her local name: "
          "303 to /auth/password/change", r.status_code == 303
          and r.headers.get("location") == "/auth/password/change",
          f"{r.status_code} -> {r.headers.get('location')} {last_fail_reason()}")
    check("...a password session", session_method(c) == "password", session_method(c))
    m = c.get("/api/me")
    check("...held by the gate: /api/me 403 password_change_required",
          m.status_code == 403 and m.json().get("error") == "password_change_required", m.text[:160])
    b = html_get(c, "/board")
    check("...GET /board 302 to /auth/password/change",
          b.status_code == 302 and b.headers.get("location") == "/auth/password/change",
          f"{b.status_code} -> {b.headers.get('location')}")
    r = c.post("/api/me/password", headers={"Origin": TEST_ORIGIN},
               json={"new_password": CHANGED_PW, "confirm_password": CHANGED_PW})
    check("...the SSO-only password route is held too (403 password_change_required)",
          r.status_code == 403 and r.json().get("error") == "password_change_required", r.text[:160])
    m = sso.get("/api/me")
    body = m.json() if m.status_code == 200 else {}
    check("her SSO session at the same moment is NOT held: 200, password_setup offered",
          m.status_code == 200 and body.get("must_change_password") is False
          and "password_setup" in body and session_method(sso) == "sso", m.text[:200])
    with app.session() as s:
        s.execute(text("UPDATE auth_session SET auth_method = NULL WHERE id_hash = :h"),
                  {"h": auth._session_digest(sso.cookies.get(auth.SESSION_COOKIE))})
        s.commit()
    m = sso.get("/api/me")
    check("a linked account's session row from before the column (NULL) is read as SSO: 200",
          m.status_code == 200 and m.json().get("must_change_password") is False, m.text[:160])
    lc, r = password_login("lou", LOCAL_PW)
    with app.session() as s:
        s.execute(text("UPDATE auth_session SET auth_method = NULL WHERE id_hash = :h"),
                  {"h": auth._session_digest(lc.cookies.get(auth.SESSION_COOKIE))})
        s.commit()
    m = lc.get("/api/me")
    check("a local account's NULL row is still held (403 password_change_required)",
          m.status_code == 403 and m.json().get("error") == "password_change_required", m.text[:160])

    page = c.get("/auth/password/change")
    tok = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    r = c.post("/auth/password/change", headers={"Origin": TEST_ORIGIN}, follow_redirects=False,
               data={"current_password": TEMP["nia"], "new_password": CHANGED_PW,
                     "confirm_password": CHANGED_PW, "csrf_token": tok})
    u = user(SUBS["nia"])
    check("the change with the temporary password: 303 /, the flag cleared",
          r.status_code == 303 and r.headers.get("location") == "/"
          and u.must_change_password is False, f"{r.status_code} {r.text[:160]}")
    check("...the rotated session is still a password one, and it reaches /api/me",
          session_method(c) == "password" and c.get("/api/me").status_code == 200,
          session_method(c))
    check("...her SSO session ended with the change (ASVS 7.4.3)",
          sso.get("/api/me").status_code == 401)
    c, r = password_login("nia", CHANGED_PW)
    check("the new password then signs her in (303 /)",
          r.status_code == 303 and r.headers.get("location") == "/", r.headers.get("location", ""))


def section_c() -> None:
    print("\n-- C. --no-password accounts stay without a password --")
    c, r = password_login("sid", "anything-at-all-123")
    check("sid (SSO-only) has a local name and no password: refused, bad_password:*",
          failed(r) and last_fail_reason().startswith("bad_password"), last_fail_reason())
    check("...and still holds no verifier", user(SUBS["sid"]).password_hash is None)


def section_d() -> None:
    print("\n-- D. the local name: the verb, collisions, the listing --")
    oli = user(SUBS["oli"])
    c, r = password_login("oli", PERSONAL_PW)
    check("oli (linked with --no-local-name) cannot sign in with a password (unknown_user)",
          failed(r) and last_fail_reason() == "unknown_user", last_fail_reason())
    rc, out, err = run_admin("user", "set-local-name", "oidc:" + SUBS["oli"], "zed")
    check("set-local-name to a LOCAL account's username is refused", rc == 1 and "local account" in err,
          err.strip()[:160])
    rc, out, err = run_admin("user", "set-local-name", "oidc:" + SUBS["oli"], "ned")
    check("set-local-name to another linked account's local name is refused",
          rc == 1 and "another linked account" in err, err.strip()[:160])
    rc, out, err = run_admin("user", "set-local-name", "zed", "zed2")
    check("set-local-name on a local account is refused", rc == 1 and "local account" in err,
          err.strip()[:160])
    rc, out, err = run_admin("user", "set-local-name", "oidc:" + SUBS["oli"], "oli")
    ev = last_event("user_local_name")
    check("set-local-name oidc:kc-oli oli: done, a user_local_name event naming the operator",
          rc == 0 and ev is not None and ev.user_id == oli.id and ev.reason == "set"
          and "now=oli" in (ev.detail or "") and ev.actor, f"{rc} {err.strip()[:160]}")
    c, r = password_login("oli", PERSONAL_PW)
    check("...and his personal password now signs in under it (303)",
          r.status_code == 303 and not failed(r) and c.get("/api/me").json().get("user_id") == oli.id,
          last_fail_reason())
    rc, _o, err = run_admin("user", "add", "ned", "--role", "member")
    check("user add with a linked account's local name is refused",
          rc == 1 and "another linked account" in err, err.strip()[:160])
    rc, _o, err = run_admin("bot", "add", "ned")
    check("bot add with it is refused too (one namespace)",
          rc == 1 and "another linked account" in err, err.strip()[:160])
    rc, out, _e = run_admin("user", "show", "ned", "--json")
    check("an admin verb finds a linked account by its local name",
          rc == 0 and '"local_name": "ned"' in out and SUBS["ned"] in out, out[:160])
    rc, out, _e = run_admin("user", "show", "zed", "--json")
    check("a local account's `user show --json` has no local_name key (unchanged)",
          rc == 0 and "local_name" not in out)
    rc, out, _e = run_admin("user", "list")
    header = out.splitlines()[0] if out else ""
    ned_line = next((ln for ln in out.splitlines() if ln.startswith("oidc:" + SUBS["ned"])), "")
    check("user list: a local_name column, ned's row shows it",
          rc == 0 and "local_name" in header and " ned " in ned_line + " ", f"{header!r} {ned_line!r}")

    # A local account always wins its own name, even against a collision forced in the database.
    tia = user(SUBS["tia"])
    with app.session() as s:
        s.get(AuthUserIdpState, tia.id).local_name = "zed"
        s.commit()
    c, r = password_login("zed", LOCAL_PW)
    me = c.get("/api/me").json() if r.status_code == 303 and not failed(r) else {}
    check("a local account wins its name over a linked account forced onto it",
          me.get("user_id") == user("zed").id, str(me)[:120])
    with app.session() as s:
        s.get(AuthUserIdpState, tia.id).local_name = "tia"
        s.commit()

    rc, out, _e = run_admin("user", "set-local-name", "oli", "--clear")
    ev = last_event("user_local_name")
    c, r = password_login("oli", PERSONAL_PW)
    check("--clear (naming him by his local name): audited, and the password stops signing in",
          rc == 0 and ev is not None and ev.reason == "cleared" and failed(r)
          and last_fail_reason() == "unknown_user", f"{rc} {last_fail_reason()}")


def section_e() -> None:
    print("\n-- E. ATLAS_LOCAL_LOGIN=break-glass (local,oidc): flagged accounts only --")
    bgl = user(SUBS["bgl"])
    with configured(local_login="break-glass"):
        c, r = password_login("ned", PERSONAL_PW, query="?break_glass=1")
        check("ned (linked, not flagged) is refused: not_break_glass",
              failed(r) and last_fail_reason() == "not_break_glass", last_fail_reason())
        before = count_events("login_break_glass", bgl.id)
        for _ in range(2):
            c, r = password_login("bgl", PERSONAL_PW, query="?break_glass=1")
        me = c.get("/api/me").json() if r.status_code == 303 and not failed(r) else {}
        check("bgl (linked, flagged break-glass) signs in under its local name",
              me.get("user_id") == bgl.id and session_method(c) == "password", last_fail_reason())
        check("...and each use is its own login_break_glass row (2 for 2)",
              count_events("login_break_glass", bgl.id) - before == 2,
              str(count_events("login_break_glass", bgl.id) - before))


def section_f() -> None:
    print("\n-- F. oidc alone (break-glass implied): no linked account signs in with a password --")
    with configured(providers=("oidc",), local_login="break-glass"):
        for name in ("bgl", "ned"):
            c, r = password_login(name, PERSONAL_PW, query="?break_glass=1")
            check(f"{name}: refused, unknown_user (no local-name lookup)",
                  failed(r) and last_fail_reason() == "unknown_user", last_fail_reason())


def section_g() -> None:
    print("\n-- G. no OIDC (local only): unchanged, and the side table is not read --")
    with configured(providers=("local",), local_login="all"):
        check("linked_password_login_enabled() is False", identity.linked_password_login_enabled() is False)
        with app.session() as s:
            s.execute(text("ALTER TABLE auth_user_idp_state RENAME TO auth_user_idp_state_away"))
            s.commit()
        try:
            c, r = password_login("ned", PERSONAL_PW)
            check("ned's local name is not accepted (unknown_user), with the side table gone",
                  failed(r) and last_fail_reason() == "unknown_user", f"{r.status_code} {last_fail_reason()}")
            c, r = password_login("zed", LOCAL_PW)
            check("a local account signs in as before (303 /), with the side table gone",
                  r.status_code == 303 and r.headers.get("location") == "/"
                  and session_method(c) == "password", f"{r.status_code} {r.headers.get('location')}")
        finally:
            with app.session() as s:
                s.execute(text("ALTER TABLE auth_user_idp_state_away RENAME TO auth_user_idp_state"))
                s.commit()


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
