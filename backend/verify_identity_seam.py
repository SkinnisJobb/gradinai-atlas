"""
Verification for the identity-provider seam (STORY-oss-13-identity-seam, ADR 0041 §5-§7).

The claim is twofold: with NO new configuration the login is today's, byte for
byte, and the seam is real (a provider registered in code logs a person in
through the real routes, the real gate and the real session machinery).

  A. DEFAULTS ARE TODAY. `identity.SETTINGS` is the default object: `local` only,
     ATLAS_LOCAL_LOGIN=all. `auth.PUBLIC_ROUTES` is the frozen pre-story set, no
     `/auth/<provider>/*` route is registered, and the login page equals a FROZEN
     COPY of the pre-story `_login_page` for several inputs (a perturbation proves
     the comparison can fail). Then the strongest form: the side table
     `auth_user_idp_state` is DROPPED and a password login, a forced change and an
     agent token all still work, so the defaults provably never read it.
  B. SETTINGS. `settings.load_identity` over a table of environments: each bad
     value raises `SettingsError` naming the variable (blank, unknown provider,
     contradictions, http issuer, multi-tenant Entra issuer, plain secret variable,
     missing credential, out-of-range numbers), and the good ones parse.
  C. AN UNKNOWN PROVIDER STOPS THE BOOT: `import app` in a child process with
     ATLAS_AUTH_PROVIDERS=bogus (and set-but-blank) exits non-zero naming it.
  D. LocalPasswordProvider WRAPS THE LIVE PATH: for each fixture account (ok,
     wrong password, unknown, disabled, bot) the provider's answer agrees with
     what `POST /auth/login` does, and it never provisions.
  E. LOCAL ACCOUNTS ARE idp='local' WITH A SUBJECT, by construction: the column is
     NOT NULL with server default 'local' (read from information_schema), and every
     path that makes an account (`atlas-admin user add`, `bot add`, a raw insert
     naming no idp, the self-service agent credential) yields idp='local' and a
     non-empty subject. No backfill exists or is needed.
  F. `atlas-admin token mint|list|revoke --user`: mint refuses a missing --out and
     `--out -`, writes the raw token only to the --out file (0600 on POSIX; neither
     stdout nor stderr contains it), caps scopes by the person's role, reuses the
     agent for the same purpose; list --user shows exactly that person's tokens;
     revoke --user TOKEN and revoke --user revoke what they say, effective at once.
  H. `atlas-admin user add --out PATH` (TASK-1470): `--out -`, an existing file and
     `--no-password` are refused with no account created; the temporary password is
     written only to the new file (0600 on POSIX; neither stdout nor stderr has it) and
     signs in once; without `--out` the password is still printed, as before.
  G. A FAKE IdentityProvider, registered in code, LOGS IN THROUGH THE REAL ROUTES
     (child process: ATLAS_AUTH_PROVIDERS=local,fake): its start/callback are public
     and registered, the page offers "Sign in with SSO", a callback mints a session
     `/api/me` accepts, a replayed or cross-browser callback is refused, a refused
     provisioning creates nothing, and the password form still works beside it.

Run:  python verify_identity_seam.py
"""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FAKE_CHILD = "--fake-provider-child" in sys.argv
TEST_ORIGIN = "https://testserver"
PW = "Correct-Horse-Battery-Identity-Seam-7!"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def _pepper() -> str:
    return base64.b64encode(os.urandom(32)).decode()


IDENTITY_VARS = [k for k in os.environ if k.startswith(("ATLAS_AUTH_PROVIDERS", "ATLAS_LOCAL_LOGIN",
                                                         "ATLAS_OIDC_"))]
for _k in IDENTITY_VARS:          # the defaults are the subject: nothing inherited
    os.environ.pop(_k)

if FAKE_CHILD:
    os.environ["ATLAS_AUTH_PROVIDERS"] = "local,fake"
    DB_URL = os.environ["ATLAS_DB_URL"]
else:
    import testdb  # noqa: E402
    try:
        DB = testdb.start()
    except testdb.TestDatabaseUnavailable as e:  # pragma: no cover - never a skip
        print(f"FATAL: {e}")
        sys.exit(1)
    DB_URL = DB.url
    os.environ["ATLAS_DB_URL"] = DB_URL
    os.environ["ATLAS_RESEED"] = "1"
    os.environ.setdefault("ATLAS_PASSWORD_PEPPER", _pepper())
    os.environ.setdefault("ATLAS_TOKEN_PEPPER", _pepper())
os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", TEST_ORIGIN)

import identity  # noqa: E402
import settings  # noqa: E402
from identity import Identity, LoginRefused  # noqa: E402


# --------------------------------------------------------------------------- #
# The fake provider of section G. Registered BEFORE the app is imported, which is
# the whole point: the registry is how a provider joins ATLAS_AUTH_PROVIDERS.
# --------------------------------------------------------------------------- #
class FakeProvider:
    """A redirect provider whose "IdP" is a dict. It proves the seam, not OIDC."""

    name = "fake"
    kind = "redirect"
    session_seconds = 3600

    def __init__(self) -> None:
        self.flows: dict = {}

    def begin(self, s, *, flow_binding, next_path):
        import secrets
        state = secrets.token_urlsafe(16)
        self.flows[state] = (flow_binding, next_path)
        return "https://fake-idp.test/authorize?state=" + state

    def authenticate(self, s, credentials, *, flow_binding=None):
        flow = self.flows.pop(credentials.get("state", ""), None)   # single use
        if flow is None:
            raise LoginRefused("state_unknown")
        if flow[0] != flow_binding:
            raise LoginRefused("state_other_browser")
        who = credentials.get("code", "")
        if not who:
            raise LoginRefused("code_missing")
        return Identity(provider=self.name, idp="fake", subject=who, name=who.title(),
                        next_path=flow[1])

    def lookup(self, s, ident):
        from sqlalchemy import select
        from models_auth import AuthUser
        return s.scalar(select(AuthUser).where(AuthUser.idp == ident.idp,
                                               AuthUser.subject == ident.subject))

    def provision(self, s, ident):
        if not ident.subject.startswith("ok-"):
            raise LoginRefused("not_allowed")
        import uuid
        from models_auth import AuthUser
        user = AuthUser(id=str(uuid.uuid4()), idp=ident.idp, subject=ident.subject,
                        name=ident.name, role="member", is_service=False,
                        password_hash=None, must_change_password=False,
                        created_at=datetime.now(timezone.utc))
        s.add(user)
        s.flush()
        return user

    def after_login(self, s, user, ident):
        pass


if FAKE_CHILD:
    identity.register("fake", lambda cfg, ctx: FakeProvider())

from sqlalchemy import select, text  # noqa: E402

import admin  # noqa: E402
import app  # noqa: E402
import auth  # noqa: E402
import models_auth  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthEvent, AuthServiceToken, AuthUser  # noqa: E402

FROZEN_PUBLIC_ROUTES = frozenset({
    "/", "/healthz", "/readyz", "/auth/login", "/auth/password/change",
    "/auth/denied", "/auth/privacy", "/favicon.ico"})


def frozen_login_page(csrf: str, next_path: str, error: str = "") -> str:
    """A VERBATIM COPY of `auth._login_page` as it was before STORY-oss-13."""
    import html
    body = (
        '<form method="post" action="/auth/login">'
        '<input type="hidden" name="csrf_token" value="%s">'
        '<input type="hidden" name="next" value="%s">'
        '<label for="username">Username</label>'
        '<input id="username" name="username" autocomplete="username" autocapitalize="none" '
        'autocorrect="off" spellcheck="false" required>'
        '<label for="password">Password</label>'
        '<input id="password" name="password" type="password" '
        'autocomplete="current-password" required>'
        '<button type="submit">Sign in</button>'
        '</form>'
        '<p class="note">Accounts are created by the operator. There is no sign-up, '
        'and no password-reset link &mdash; ask your instance administrator.</p>'
        '<p class="note"><a href="/auth/privacy">How your data is handled</a></p>'
    ) % (html.escape(csrf), html.escape(next_path))
    return auth._page("Sign in", "Project Atlas", body, error)


def client(**kw):
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN, **kw)


def mk_user(username: str, *, role: str = "member", is_service: bool = False,
            disabled: bool = False, must_change: bool = False, person_id=None) -> str:
    import uuid
    with app.session() as s:
        row = s.scalar(select(AuthUser).where(AuthUser.idp == "local", AuthUser.subject == username))
        if row is not None:
            return row.id
        uid = str(uuid.uuid4())
        s.add(AuthUser(
            id=uid, subject=username, name=username.title(), role=role, is_service=is_service,
            must_change_password=must_change, label=username if is_service else None,
            password_hash=None if is_service else passwords.hash_password(PW),
            person_user_id=person_id,
            disabled_at=datetime.now(timezone.utc) if disabled else None,
            created_at=datetime.now(timezone.utc)))
        s.commit()
        return uid


def password_login(username: str, password: str = PW):
    c = client()
    page = c.get("/auth/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    r = c.post("/auth/login", data={"username": username, "password": password,
                                    "csrf_token": token},
               headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
    return c, r


def logged_in(c) -> bool:
    return c.get("/api/me").status_code == 200


def mint(owner_id: str, scopes=("read",)) -> str:
    with app.session() as s:
        raw, _ = auth.mint_service_token(s, owner=s.get(AuthUser, owner_id), scopes=list(scopes),
                                         label="seam")
        s.commit()
    return raw


def bearer_status(raw: str) -> int:
    return client(headers={"Authorization": "Bearer " + raw}).get("/api/me").status_code


def run_admin(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = admin.main(list(argv))
        except SystemExit as exc:   # argparse refusals
            rc = int(exc.code or 0)
    return rc, out.getvalue(), err.getvalue()


def route_paths() -> set:
    from route_table import live_routes
    return {r.path for r in live_routes(app.app) if getattr(r, "path", None)}


# --------------------------------------------------------------------------- #
# A. Defaults are today
# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. with no new configuration the login is today's --")
    check("identity.SETTINGS is the default object (local only, ATLAS_LOCAL_LOGIN=all)",
          identity.SETTINGS == settings.IdentitySettings()
          and identity.SETTINGS.providers == ("local",) and identity.SETTINGS.local_login == "all",
          repr(identity.SETTINGS))
    check("no redirect provider is configured", identity.redirect_providers() == [])
    check("auth.PUBLIC_ROUTES is exactly the pre-story frozenset",
          auth.PUBLIC_ROUTES == FROZEN_PUBLIC_ROUTES, str(sorted(auth.PUBLIC_ROUTES)))
    paths = route_paths()
    check("no /auth/<provider>/start or /callback route is registered",
          not any(p.endswith(("/start", "/callback")) and p.startswith("/auth/") for p in paths),
          str(sorted(p for p in paths if p.startswith("/auth/"))))
    snapshot = (HERE / "routes.snapshot.txt").read_text(encoding="utf-8").split()
    check("the snapshot names no SSO route either (verify_routes.py pins the whole table)",
          not any("/auth/oidc" in w for w in snapshot))

    cases = [("tok-1", "/", ""), ("tok-<2>", "/board?x=1&y=2", ""),
             ("t3", "/", passwords.GENERIC_LOGIN_FAILURE_MESSAGE)]
    same = all(auth._login_page(c, n, e) == frozen_login_page(c, n, e) for c, n, e in cases)
    check("the login page equals a frozen copy of the pre-story page, for three inputs", same)
    check("...and the comparison can fail (perturbation: one character of the copy changed)",
          auth._login_page("t", "/") != frozen_login_page("t", "/").replace("Sign in", "Sign  in", 1))
    page = client().get("/auth/login")
    check("GET /auth/login carries no SSO link and the password form",
          page.status_code == 200 and "Sign in with SSO" not in page.text
          and 'action="/auth/login"' in page.text)
    page_bg = client().get("/auth/login?break_glass=1")
    check("...and ?break_glass=1 changes nothing by default (the parameter is not read)",
          page_bg.text.replace(page_bg.text.split('name="csrf_token" value="')[1].split('"')[0], "X")
          == page.text.replace(page.text.split('name="csrf_token" value="')[1].split('"')[0], "X"))

    # The strongest form: take the side table away and the defaults still work.
    owner_id = mk_user("seam-owner", role="owner")
    agent_id = mk_user("seam-owner-agent", is_service=True, person_id=owner_id)
    raw = mint(agent_id)
    with app.engine.begin() as conn:
        conn.execute(text("DROP TABLE auth_user_idp_state"))
    try:
        c, r = password_login("seam-owner")
        check("with auth_user_idp_state DROPPED, a password login still signs in (303 + session)",
              r.status_code == 303 and logged_in(c), f"{r.status_code}")
        check("...and a person-linked agent token still authenticates (200)",
              bearer_status(raw) == 200)
        _c, bad = password_login("seam-owner", "wrong-password-entirely-9!")
        check("...and a wrong password still fails the ordinary way (303 to ?error=1)",
              bad.status_code == 303 and "error=1" in bad.headers.get("location", ""))
    finally:
        models_auth.create_all(app.engine)
    with app.session() as s:
        n = s.scalar(select(text("count(*)")).select_from(text("auth_user_idp_state")))
    check("the side table is back (recreated by create_all) and empty", n == 0, str(n))
    with app.session() as s:
        bg = s.scalars(select(AuthEvent).where(AuthEvent.event == "login_break_glass")).all()
    check("no login_break_glass event is written under the defaults", not bg, str(len(bg)))


# --------------------------------------------------------------------------- #
# B. Settings
# --------------------------------------------------------------------------- #
def section_b(tmp: str) -> None:
    print("\n-- B. settings.load_identity: typed, set-but-blank is an error --")
    secret = os.path.join(tmp, "secret")
    Path(secret).write_text("s\n", encoding="utf-8")
    oidc = {"ATLAS_AUTH_PROVIDERS": "oidc", "ATLAS_OIDC_ISSUER": "https://idp.example/realms/a",
            "ATLAS_OIDC_CLIENT_ID": "atlas", "ATLAS_OIDC_CLIENT_SECRET_FILE": secret,
            "ATLAS_OIDC_REDIRECT_URI": "https://atlas.example/auth/oidc/callback"}
    entra_issuer = "https://login.microsoftonline.com/0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0/v2.0"
    bad = [
        ("blank ATLAS_AUTH_PROVIDERS", {"ATLAS_AUTH_PROVIDERS": " "}, "ATLAS_AUTH_PROVIDERS"),
        ("unknown provider", {"ATLAS_AUTH_PROVIDERS": "local,saml"}, "saml"),
        ("empty entry", {"ATLAS_AUTH_PROVIDERS": "local,"}, "ATLAS_AUTH_PROVIDERS"),
        ("ATLAS_LOCAL_LOGIN typo", {"ATLAS_LOCAL_LOGIN": "some"}, "ATLAS_LOCAL_LOGIN"),
        ("blank ATLAS_LOCAL_LOGIN", {"ATLAS_LOCAL_LOGIN": ""}, "ATLAS_LOCAL_LOGIN"),
        ("break-glass with local only", {"ATLAS_LOCAL_LOGIN": "break-glass"}, "ATLAS_LOCAL_LOGIN"),
        ("all without local", dict(oidc, ATLAS_LOCAL_LOGIN="all"), "ATLAS_LOCAL_LOGIN"),
        ("oidc without issuer", {k: v for k, v in oidc.items() if k != "ATLAS_OIDC_ISSUER"},
         "ATLAS_OIDC_ISSUER"),
        ("oidc without client id", {k: v for k, v in oidc.items() if k != "ATLAS_OIDC_CLIENT_ID"},
         "ATLAS_OIDC_CLIENT_ID"),
        ("oidc without a secret file",
         {k: v for k, v in oidc.items() if k != "ATLAS_OIDC_CLIENT_SECRET_FILE"},
         "ATLAS_OIDC_CLIENT_SECRET_FILE"),
        ("private_key_jwt without a key file", dict(oidc, ATLAS_OIDC_TOKEN_AUTH="private_key_jwt"),
         "ATLAS_OIDC_CLIENT_KEY_FILE"),
        ("the plain client secret variable", dict(oidc, ATLAS_OIDC_CLIENT_SECRET="x"),
         "ATLAS_OIDC_CLIENT_SECRET"),
        ("an http issuer", dict(oidc, ATLAS_OIDC_ISSUER="http://idp.example"), "ATLAS_OIDC_ISSUER"),
        ("a redirect URI on another path", dict(oidc, ATLAS_OIDC_REDIRECT_URI="https://a.example/cb"),
         "ATLAS_OIDC_REDIRECT_URI"),
        ("no redirect URI and no https public URL",
         {k: v for k, v in oidc.items() if k != "ATLAS_OIDC_REDIRECT_URI"}, "ATLAS_OIDC_REDIRECT_URI"),
        ("scopes without openid", dict(oidc, ATLAS_OIDC_SCOPES="profile email"), "ATLAS_OIDC_SCOPES"),
        ("unknown preset", dict(oidc, ATLAS_OIDC_PRESET="okta"), "ATLAS_OIDC_PRESET"),
        ("entra with the multi-tenant issuer",
         dict(oidc, ATLAS_OIDC_PRESET="entra",
              ATLAS_OIDC_ISSUER="https://login.microsoftonline.com/common/v2.0"), "ATLAS_OIDC_ISSUER"),
        ("max session hours 0", dict(oidc, ATLAS_OIDC_MAX_SESSION_HOURS="0"),
         "ATLAS_OIDC_MAX_SESSION_HOURS"),
        ("max session hours above 14 days", dict(oidc, ATLAS_OIDC_MAX_SESSION_HOURS="337"),
         "ATLAS_OIDC_MAX_SESSION_HOURS"),
        ("revalidate days not a number", dict(oidc, ATLAS_OIDC_REVALIDATE_DAYS="thirty"),
         "ATLAS_OIDC_REVALIDATE_DAYS"),
        ("required groups that name none", dict(oidc, ATLAS_OIDC_REQUIRED_GROUPS=" , "),
         "ATLAS_OIDC_REQUIRED_GROUPS"),
        ("blank group map path", dict(oidc, ATLAS_OIDC_GROUP_MAP_FILE=""), "ATLAS_OIDC_GROUP_MAP_FILE"),
        ("an OIDC variable set blank while oidc is off", {"ATLAS_OIDC_ISSUER": ""}, "ATLAS_OIDC_ISSUER"),
    ]
    for label, env, needle in bad:
        try:
            settings.load_identity(env)
            check(f"refused: {label}", False, "accepted")
        except settings.SettingsError as exc:
            check(f"refused: {label} (names {needle})", needle in str(exc), str(exc)[:160])

    d = settings.load_identity({})
    check("{} parses to the defaults", d == settings.IdentitySettings())
    o = settings.load_identity(oidc)
    check("oidc alone: providers (oidc,), ATLAS_LOCAL_LOGIN defaults to break-glass",
          o.providers == ("oidc",) and o.local_login == "break-glass", repr(o.providers))
    both = settings.load_identity(dict(oidc, ATLAS_AUTH_PROVIDERS="local,oidc"))
    check("local,oidc: ATLAS_LOCAL_LOGIN defaults to all", both.local_login == "all")
    derived = settings.load_identity(dict({k: v for k, v in oidc.items()
                                           if k != "ATLAS_OIDC_REDIRECT_URI"},
                                          ATLAS_PUBLIC_URL="https://atlas.example:8443"))
    check("the redirect URI is derived from ATLAS_PUBLIC_URL",
          derived.oidc.redirect_uri == "https://atlas.example:8443/auth/oidc/callback",
          str(derived.oidc.redirect_uri))
    e = settings.load_identity(dict(oidc, ATLAS_OIDC_PRESET="entra", ATLAS_OIDC_ISSUER=entra_issuer))
    check("entra with a tenant issuer pins the tenant id",
          e.oidc.entra_tenant_id == "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
    n = settings.load_identity(dict(oidc, ATLAS_OIDC_MAX_SESSION_HOURS="8",
                                    ATLAS_OIDC_REVALIDATE_DAYS="7",
                                    ATLAS_OIDC_REQUIRED_GROUPS="a, b"))
    check("numbers and the required-group list parse",
          (n.oidc.max_session_hours, n.oidc.revalidate_days, n.oidc.required_groups)
          == (8, 7, frozenset({"a", "b"})))
    check("defaults: 12 h sessions, 30-day revalidation, openid profile email",
          (o.oidc.max_session_hours, o.oidc.revalidate_days, o.oidc.scopes)
          == (12, 30, ("openid", "profile", "email")))


# --------------------------------------------------------------------------- #
# C. An unknown provider stops the boot
# --------------------------------------------------------------------------- #
def section_c() -> None:
    print("\n-- C. an unknown or blank provider name stops the boot --")
    for value, needle in (("bogus", "bogus"), ("", "ATLAS_AUTH_PROVIDERS")):
        env = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_TEST_DB")}
        env["ATLAS_AUTH_PROVIDERS"] = value
        env["ATLAS_DB_URL"] = "postgresql+psycopg2://u:p@127.0.0.1:1/none"
        proc = subprocess.run([sys.executable, "-c", "import app"], cwd=str(HERE), env=env,
                              capture_output=True, text=True, timeout=120)
        check(f"ATLAS_AUTH_PROVIDERS={value!r}: `import app` exits non-zero naming it",
              proc.returncode != 0 and needle in (proc.stderr + proc.stdout)
              and "ATLAS_AUTH_PROVIDERS" in (proc.stderr + proc.stdout),
              f"rc={proc.returncode} {proc.stderr.strip().splitlines()[-1:]}")


# --------------------------------------------------------------------------- #
# D. LocalPasswordProvider agrees with the live login
# --------------------------------------------------------------------------- #
def section_d() -> None:
    print("\n-- D. LocalPasswordProvider wraps the live password path --")
    mk_user("seam-ok")
    mk_user("seam-disabled", disabled=True)
    mk_user("seam-bot", is_service=True)
    provider = identity.provider("local")
    check("the configured `local` provider is a LocalPasswordProvider",
          isinstance(provider, identity.LocalPasswordProvider)
          and isinstance(provider, identity.IdentityProvider))
    cases = [("seam-ok", PW, True), ("seam-ok", "Wrong-Horse-Battery-77!", False),
             ("seam-nobody", PW, False), ("seam-disabled", PW, False), ("seam-bot", PW, False),
             ("SEAM-OK ", PW, True)]
    for username, password, ok in cases:
        with app.session() as s:
            try:
                ident = provider.authenticate(s, {"username": username, "password": password})
                got = True
                same_row = provider.lookup(s, ident) is not None
            except LoginRefused:
                got, same_row = False, True
        c, r = password_login(username, password)
        live = r.status_code == 303 and logged_in(c)
        check(f"{username!r}: provider says {'ok' if got else 'refused'}, POST /auth/login "
              f"{'signs in' if live else 'refuses'} — they agree, as expected ({ok})",
              got == live == ok and same_row, f"provider={got} live={live}")
    with app.session() as s:
        try:
            provider.provision(s, Identity(provider="local", idp="local", subject="seam-new"))
            check("the local provider never provisions", False)
        except LoginRefused:
            check("the local provider never provisions (accounts are operator-made)", True)


# --------------------------------------------------------------------------- #
# E. Local users have idp='local' and a subject
# --------------------------------------------------------------------------- #
def section_e() -> None:
    print("\n-- E. local accounts: idp='local' and subject populated, by construction --")
    with app.engine.connect() as conn:
        col = conn.execute(text(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name='auth_user' AND column_name='idp'")).one()
        subj = conn.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name='auth_user' AND column_name='subject'")).one()
    check("auth_user.idp is NOT NULL with server default 'local'",
          col[0] == "NO" and "local" in str(col[1]), str(tuple(col)))
    check("auth_user.subject is NOT NULL", subj[0] == "NO")
    rc, out, _err = run_admin("user", "add", "seam-cli-person", "--role", "member")
    check("`atlas-admin user add` succeeds", rc == 0, out[:120])
    rc, _o, _e = run_admin("bot", "add", "seam-cli-bot")
    check("`atlas-admin bot add` succeeds", rc == 0)
    with app.engine.begin() as conn:     # a raw insert that names no idp at all
        conn.execute(text(
            "INSERT INTO auth_user (id, subject, role, is_service, must_change_password, created_at) "
            "VALUES ('seam-raw-row', 'seam-raw', 'member', false, false, now())"))
    # The self-service credential path makes an agent account too.
    c, _r = password_login("seam-ok")
    csrf = c.get("/api/me").json()["csrf_token"]
    r = c.post("/api/me/agent-credential", json={"purpose": "seam", "scopes": ["read"],
                                                  "password": PW, "csrf_token": csrf},
               headers={"Origin": TEST_ORIGIN})
    check("the self-service agent credential is created", r.status_code == 201, str(r.status_code))
    with app.session() as s:
        rows = s.scalars(select(AuthUser)).all()
        names = {u.subject: u for u in rows}
        made = [names.get(n) for n in ("seam-cli-person", "seam-cli-bot", "seam-raw", "seam-ok-seam")]
        check("every account made by each path is idp='local' with its subject",
              all(u is not None and u.idp == "local" and u.subject for u in made),
              str([(u.subject, u.idp) if u else None for u in made]))
        local_rows = [u for u in rows if u.idp == "local"]
        check("every row in auth_user is idp='local' with a non-empty subject (no OIDC here)",
              len(local_rows) == len(rows) and all(u.subject for u in rows), str(len(rows)))
        check("is_local() reads NULL as local (defensive; the column cannot hold one)",
              identity.is_local(AuthUser(id="x", idp=None, subject="y")))


# --------------------------------------------------------------------------- #
# F. atlas-admin token mint | list | revoke --user
# --------------------------------------------------------------------------- #
def section_f(tmp: str) -> None:
    print("\n-- F. atlas-admin token mint / list / revoke --user --")
    mk_user("seam-alice")
    mk_user("seam-bob")

    def token_count() -> int:
        with app.session() as s:
            return len(s.scalars(select(AuthServiceToken.id)).all())

    before = token_count()
    rc, _o, err = run_admin("token", "mint", "--user", "seam-alice", "--purpose", "claude")
    check("mint without --out is refused by the parser (rc 2), nothing minted",
          rc == 2 and token_count() == before, f"rc={rc}")
    rc, out, err = run_admin("token", "mint", "--user", "seam-alice", "--purpose", "claude",
                             "--out", "-")
    check("mint --out - is refused (stdout is never a destination), nothing minted",
          rc == 1 and token_count() == before and "atlas_svc_" not in out + err, f"rc={rc}")
    rc, out, err = run_admin("token", "mint", "--user", "seam-alice", "--purpose", "claude",
                             "--scopes", "read,write,admin", "--out", os.path.join(tmp, "x"))
    check("a member's agent cannot be minted `admin` (capped by the person's role)",
          rc == 1 and token_count() == before and not os.path.exists(os.path.join(tmp, "x")),
          err.strip()[:160])

    path = os.path.join(tmp, "alice-token")
    rc, out, err = run_admin("token", "mint", "--user", "seam-alice", "--purpose", "claude",
                             "--scopes", "read,write", "--out", path)
    raw = Path(path).read_text(encoding="utf-8").strip() if os.path.exists(path) else ""
    check("mint --out PATH succeeds and writes one atlas_svc_ token to the file",
          rc == 0 and raw.startswith("atlas_svc_") and token_count() == before + 1, f"rc={rc} {err[:120]}")
    check("the raw token is on NEITHER stdout NOR stderr", raw and raw not in out and raw not in err)
    if os.name == "posix":
        mode = stat.S_IMODE(os.stat(path).st_mode)
        check("the token file is 0600", mode == 0o600, oct(mode))
    rc2, _o, err2 = run_admin("token", "mint", "--user", "seam-alice", "--purpose", "claude",
                              "--out", path)
    check("mint refuses to overwrite an existing --out file", rc2 == 1 and token_count() == before + 1)
    check("the minted token authenticates as Bearer (200)", bearer_status(raw) == 200)
    with app.session() as s:
        agent = s.scalar(select(AuthUser).where(AuthUser.subject == "seam-alice-claude"))
        alice = s.scalar(select(AuthUser).where(AuthUser.subject == "seam-alice"))
        check("the agent is seam-alice-claude, a service account linked to seam-alice",
              agent is not None and agent.is_service and agent.person_user_id == alice.id)

    path2 = os.path.join(tmp, "alice-token-2")
    rc, _o, _e = run_admin("token", "mint", "--user", "seam-alice", "--purpose", "claude",
                           "--out", path2)
    raw2 = Path(path2).read_text(encoding="utf-8").strip()
    with app.session() as s:
        agents = s.scalars(select(AuthUser).where(AuthUser.subject == "seam-alice-claude")).all()
        owners = {t.owner_user_id for t in s.scalars(select(AuthServiceToken).where(
            AuthServiceToken.revoked_at.is_(None))).all() if t.label == "seam-alice-claude"}
    check("minting again for the same purpose adds a token to the SAME agent",
          rc == 0 and len(agents) == 1 and owners == {agents[0].id})
    run_admin("token", "mint", "--user", "seam-bob", "--purpose", "claude",
              "--out", os.path.join(tmp, "bob-token"))

    rc, out, _e = run_admin("token", "list", "--user", "seam-alice", "--json")
    listed = json.loads(out)["tokens"] if rc == 0 else []
    check("token list --user shows exactly seam-alice's two tokens",
          rc == 0 and len(listed) == 2 and {t["owner"] for t in listed} == {"seam-alice-claude"},
          str([t["owner"] for t in listed]))
    check("token list never prints a raw token", raw not in out and raw2 not in out)

    first_id = listed[0]["id"] if listed else ""
    rc, out, _e = run_admin("token", "revoke", "--user", "seam-bob", first_id)
    check("revoke --user seam-bob <alice's token> is refused (not theirs)", rc == 1)
    rc, out, _e = run_admin("token", "revoke", "--user", "seam-alice", first_id)
    statuses = sorted([bearer_status(raw), bearer_status(raw2)])
    check("revoke --user seam-alice <id> revokes that one token, at once", rc == 0 and statuses == [200, 401],
          str(statuses))
    rc, out, _e = run_admin("token", "revoke", "--user", "seam-alice")
    check("revoke --user seam-alice revokes every remaining token of theirs",
          rc == 0 and bearer_status(raw) == 401 and bearer_status(raw2) == 401)
    bob = Path(os.path.join(tmp, "bob-token")).read_text(encoding="utf-8").strip()
    check("...and nobody else's (seam-bob's token still 200)", bearer_status(bob) == 200)
    rc, _o, _e = run_admin("token", "revoke")
    check("revoke with neither a token nor --user is refused", rc == 1)


# --------------------------------------------------------------------------- #
# H. atlas-admin user add --out
# --------------------------------------------------------------------------- #
def section_h(tmp: str) -> None:
    print("\n-- H. atlas-admin user add --out --")

    def exists(username: str) -> bool:
        with app.session() as s:
            return s.scalar(select(AuthUser.id).where(AuthUser.idp == "local",
                                                     AuthUser.subject == username)) is not None

    rc, out, err = run_admin("user", "add", "seam-out-dash", "--out", "-")
    check("user add --out - is refused (stdout is never a destination), no account",
          rc == 1 and not exists("seam-out-dash"), f"rc={rc} {err.strip()[:120]}")
    taken = os.path.join(tmp, "taken")
    Path(taken).write_text("x\n", encoding="utf-8")
    rc, _o, _e = run_admin("user", "add", "seam-out-taken", "--out", taken)
    check("user add refuses to overwrite an existing --out file, no account",
          rc == 1 and not exists("seam-out-taken")
          and Path(taken).read_text(encoding="utf-8") == "x\n", f"rc={rc}")
    rc, _o, _e = run_admin("user", "add", "seam-out-nopw", "--no-password",
                           "--out", os.path.join(tmp, "nopw"))
    check("user add --no-password --out is refused (nothing to write), no account, no file",
          rc == 1 and not exists("seam-out-nopw") and not os.path.exists(os.path.join(tmp, "nopw")))
    rc, _o, _e = run_admin("user", "add", "seam-out-nodir", "--out",
                           os.path.join(tmp, "no-such-dir", "pw"))
    check("an --out file that cannot be created leaves no account behind",
          rc == 1 and not exists("seam-out-nodir"), f"rc={rc}")

    path = os.path.join(tmp, "carol-password")
    rc, out, err = run_admin("user", "add", "seam-out-carol", "--role", "owner", "--out", path)
    secret = Path(path).read_text(encoding="utf-8").strip() if os.path.exists(path) else ""
    check("user add --out PATH creates the account and writes the password to the file",
          rc == 0 and exists("seam-out-carol") and len(secret) >= 12
          and out.startswith("created seam-out-carol"), f"rc={rc} {err[:120]}")
    check("the password is on NEITHER stdout NOR stderr", secret and secret not in out and secret not in err)
    check("stdout names the file", path in out)
    if os.name == "posix":
        mode = stat.S_IMODE(os.stat(path).st_mode)
        check("the password file is 0600", mode == 0o600, oct(mode))
    _c, r = password_login("seam-out-carol", secret)
    check("the password in the file signs in (303 to the forced change)",
          r.status_code == 303 and "/auth/password/change" in r.headers.get("location", ""),
          f"{r.status_code} {r.headers.get('location')}")

    rc, out, _e = run_admin("user", "add", "seam-out-dave")
    with app.session() as s:
        row = s.scalar(select(AuthUser).where(AuthUser.subject == "seam-out-dave"))
    shown = [ln.strip() for ln in out.splitlines() if ln.startswith("      ") and ln.strip()]
    check("without --out the default is unchanged: the password is printed once on stdout",
          rc == 0 and row is not None and len(shown) == 1
          and passwords.verify_password(row.password_hash, shown[0]).ok,
          f"rc={rc} lines={len(shown)}")


# --------------------------------------------------------------------------- #
# G. A fake provider through the real routes (child process)
# --------------------------------------------------------------------------- #
def section_g() -> None:
    print("\n-- G. a fake IdentityProvider, registered in code, logs in through the real routes --")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ATLAS_TEST_DB", "ATLAS_RESEED"))}
    env["ATLAS_DB_URL"] = DB_URL
    proc = subprocess.run([sys.executable, str(HERE / "verify_identity_seam.py"),
                           "--fake-provider-child"], cwd=str(HERE), env=env,
                          capture_output=True, text=True, timeout=600)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(("[PASS]", "[FAIL]"))]
    for ln in lines:
        print("   " + ln)
    fails = [ln for ln in lines if ln.startswith("[FAIL]")]
    check("the child ran its checks and every one passed",
          proc.returncode == 0 and lines and not fails,
          f"rc={proc.returncode} {len(lines)} checks {len(fails)} failed "
          f"{proc.stderr.strip().splitlines()[-3:] if proc.returncode else ''}")


def fake_child() -> int:
    app.startup()
    check("the fake provider is configured beside local",
          identity.SETTINGS.providers == ("local", "fake")
          and isinstance(identity.provider("fake"), FakeProvider))
    check("its start and callback paths are public, and nothing else was added",
          auth.PUBLIC_ROUTES == FROZEN_PUBLIC_ROUTES | {"/auth/fake/start", "/auth/fake/callback"},
          str(sorted(auth.PUBLIC_ROUTES - FROZEN_PUBLIC_ROUTES)))
    paths = route_paths()
    check("the two routes are registered", {"/auth/fake/start", "/auth/fake/callback"} <= paths)
    c = client()
    page = c.get("/auth/login")
    check("the login page offers \"Sign in with SSO\" AND still the password form",
          'href="/auth/fake/start"' in page.text and 'action="/auth/login"' in page.text)

    r = c.get("/auth/fake/start?next=/board", follow_redirects=False)
    loc = r.headers.get("location", "")
    check("start: 303 to the provider's URL, with a pre-session cookie",
          r.status_code == 303 and loc.startswith("https://fake-idp.test/authorize?state=")
          and auth.PRE_COOKIE in c.cookies, f"{r.status_code} {loc[:60]}")
    state = loc.split("state=")[-1]
    other = client()
    other.get("/auth/login")
    r = other.get(f"/auth/fake/callback?state={state}&code=ok-carol", follow_redirects=False)
    check("a callback in ANOTHER browser is refused (no session)",
          r.status_code == 303 and "error=1" in r.headers.get("location", "")
          and other.get("/api/me").status_code == 401)
    r = c.get(f"/auth/fake/callback?state={state}&code=ok-carol", follow_redirects=False)
    check("...and that state is spent: the right browser replaying it is refused too",
          r.status_code == 303 and "error=1" in r.headers.get("location", ""))

    r = c.get("/auth/fake/start?next=/board", follow_redirects=False)
    state = r.headers["location"].split("state=")[-1]
    r = c.get(f"/auth/fake/callback?state={state}&code=ok-carol", follow_redirects=False)
    me = c.get("/api/me")
    check("a fresh flow's callback: 303 to the requested next, and a session /api/me accepts",
          r.status_code == 303 and r.headers.get("location") == "/board" and me.status_code == 200
          and me.json().get("username") == "ok-carol", f"{r.status_code} {me.status_code}")
    with app.session() as s:
        u = s.scalar(select(AuthUser).where(AuthUser.idp == "fake", AuthUser.subject == "ok-carol"))
        sess = s.scalar(select(models_auth.AuthSession).where(models_auth.AuthSession.user_id == u.id))
        ok_ev = s.scalars(select(AuthEvent).where(AuthEvent.event == "login_ok",
                                                  AuthEvent.user_id == u.id)).all()
    check("the account was provisioned by the provider as (fake, ok-carol), a member",
          u is not None and u.role == "member" and not u.is_service)
    life = (sess.absolute_expires_at - sess.created_at).total_seconds() if sess else 0
    check("its session is bounded by the provider's session_seconds (3600), not 14 days",
          0 < life <= 3600, str(life))
    check("a login_ok event names the provider", any(e.reason == "provider=fake" for e in ok_ev))
    r = c.get(f"/auth/fake/callback?state={state}&code=ok-carol", follow_redirects=False)
    check("replaying the successful callback is refused", "error=1" in r.headers.get("location", ""))

    d = client()
    r = d.get("/auth/fake/start", follow_redirects=False)
    state = r.headers["location"].split("state=")[-1]
    r = d.get(f"/auth/fake/callback?state={state}&code=nope-dave", follow_redirects=False)
    with app.session() as s:
        dave = s.scalar(select(AuthUser).where(AuthUser.subject == "nope-dave"))
        fail = s.scalars(select(AuthEvent).where(AuthEvent.event == "login_fail",
                                                 AuthEvent.reason == "fake:not_allowed")).all()
    check("a refused provisioning: no session, no account, a login_fail naming the reason",
          d.get("/api/me").status_code == 401 and dave is None and fail)
    mk_user("seam-fake-local")
    c2, r = password_login("seam-fake-local")
    check("the password login still works beside the provider", r.status_code == 303 and logged_in(c2))
    return 0


def main() -> int:
    if FAKE_CHILD:
        fake_child()
        return 1 if failures else 0
    app.startup()
    with tempfile.TemporaryDirectory(prefix="atlas-identity-seam-") as tmp:
        section_a()
        section_b(tmp)
        section_c()
        section_d()
        section_e()
        section_f(tmp)
        section_h(tmp)
        section_g()
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
