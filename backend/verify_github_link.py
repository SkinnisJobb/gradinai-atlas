"""
Verification for GitHub identity + repo access (github_link.py, the
`/api/me/github*` and `/api/access/github/*` routes).

The claims, in the order a reviewer would doubt them:

  A. OFF WHEN UNCONFIGURED. No credential files -> every route answers cleanly
     (`configured: false` / 404 `github_not_configured`), nothing raises, and the
     owner grid reports the feature off.
  B. THE STATE IS THE CSRF DEFENCE. A garbage state, a state minted for ANOTHER
     session, an expired one and a missing one are all refused at the callback
     with no link stored and no call made to GitHub.
  C. LINKING STORES THE VERIFIED IDENTITY (numeric id + login) and nothing else:
     the OAuth access token appears in no row, no audit event. It grants nothing:
     no invitation call is made by linking.
  D. ONE GITHUB IDENTITY, ONE ACCOUNT; and a second identity cannot silently
     replace the first.
  E. WHO MAY APPROVE. A member cannot. A token cannot - including an
     `admin`-scoped token, which satisfies the scope axis and must still be
     refused on the owner axis. A cookie owner with no CSRF token cannot. None of
     those made a GitHub call.
  F. APPROVE -> the invitation call goes to the configured repo with the right
     login (re-resolved from the stored id, so a rename is followed); REVOKE ->
     the collaborator is removed and a pending invitation is cancelled.
  G. REFRESH sees an accepted invitation; UNLINK revokes access first and KEEPS
     the link if GitHub fails.
  H. `/api/me/github*` are browser-session only (a token is refused).

No network: `github_link.transport` is replaced by a recorder. Run:
    python verify_github_link.py
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb  # noqa: E402

DB = testdb.start()
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.setdefault(
    "ATLAS_FRONTEND_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend"),
)
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
for _k in ("ATLAS_GITHUB_OAUTH_CLIENT_ID_FILE", "ATLAS_GITHUB_OAUTH_CLIENT_SECRET_FILE",
           "ATLAS_GITHUB_INVITE_TOKEN_FILE", "ATLAS_GITHUB_REPO"):
    os.environ.pop(_k, None)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

# STORY-oss-08: this script pins the GitHub routes themselves,
# so it runs with every feature on whatever the caller set; verify_feature_flags.py
# proves the switched-off side. Before the app import: main.py reads the flags then.
os.environ.pop("ATLAS_DISABLED_FEATURES", None)
import app  # noqa: E402
import auth  # noqa: E402
import github_link  # noqa: E402
import models_auth  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthEvent, AuthUser, GithubLink  # noqa: E402

failures: list[str] = []
ORIGIN = "https://testserver"
REPO = "example-org/project-atlas"
FAKE_USER_TOKEN = "gho_FAKEUSERTOKEN_must_never_be_stored"
PW = {"g-owner": "Correct-Horse-Battery-Owner-5!",
      "avery": "Correct-Horse-Battery-Avery-5!",
      "jude": "Correct-Horse-Battery-Jude-55!"}


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


# --------------------------------------------------------------------------- #
# The fake GitHub
# --------------------------------------------------------------------------- #
class FakeGithub:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.signin = (1001, "avery-gh")            # who the next OAuth sign-in is
        self.logins = {1001: "avery-gh", 1002: "jude-gh"}   # id -> current login
        self.invite_status = 201
        self.collaborators: set[str] = set()
        self.invitations: list[dict] = []
        self.fail_deletes = False

    def __call__(self, method, url, headers, form, json_body):
        self.calls.append((method, url, dict(headers or {}), form, json_body))
        path = urlsplit(url).path
        if url.endswith("/login/oauth/access_token"):
            ok = form and form.get("code") == "good-code"
            return (200, {"access_token": FAKE_USER_TOKEN}) if ok else (200, {"error": "bad_verification_code"})
        if url == github_link.API + "/user":
            if headers.get("Authorization") == "Bearer " + FAKE_USER_TOKEN:
                return 200, {"id": self.signin[0], "login": self.signin[1]}
            return 401, None
        if path.startswith("/user/"):
            gid = int(path.rsplit("/", 1)[1])
            if gid in self.logins:
                return 200, {"id": gid, "login": self.logins[gid]}
            return 404, None
        if method == "PUT" and "/collaborators/" in path:
            return self.invite_status, ({"id": 777} if self.invite_status == 201 else None)
        if method == "DELETE":
            return (500, None) if self.fail_deletes else (204, None)
        if method == "GET" and "/collaborators/" in path:
            return (204, None) if path.rsplit("/", 1)[1] in self.collaborators else (404, None)
        if method == "GET" and path.endswith("/invitations"):
            return 200, self.invitations
        return 404, None

    def github_calls(self) -> int:
        return len(self.calls)


def sign_in(username: str):
    c = TestClient(app.app, base_url=ORIGIN)
    page = c.get("/auth/login")
    tok = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": username, "password": PW[username], "csrf_token": tok},
           headers={"Origin": ORIGIN}, follow_redirects=False)
    return c, c.get("/api/me").json()["csrf_token"]


def put_access(client, csrf, uid, on, **kw):
    payload = {"repo_access": on}
    if csrf is not None:
        payload["csrf_token"] = csrf
    return client.put("/api/access/github/%s" % uid, json=payload,
                      headers={"Origin": ORIGIN}, **kw)


def start_and_state(client) -> str:
    r = client.get("/api/me/github/start", follow_redirects=False)
    return parse_qs(urlsplit(r.headers.get("location", "")).query).get("state", [""])[0]


def link_via_oauth(client, fake, who) -> "object":
    fake.signin = who
    state = start_and_state(client)
    return client.get("/api/me/github/callback", params={"code": "good-code", "state": state},
                      follow_redirects=False)


def row_for(uid):
    with app.session() as s:
        return s.get(GithubLink, uid)


def main() -> int:
    app.startup()
    with app.session() as s:
        for subject, role in (("g-owner", "owner"), ("avery", "member"), ("jude", "member")):
            s.add(AuthUser(id="u-" + subject, subject=subject, name=subject, role=role,
                           is_service=False, must_change_password=False,
                           password_hash=passwords.hash_password(PW[subject]),
                           created_at=datetime.now(timezone.utc)))
        s.commit()

    owner, owner_csrf = sign_in("g-owner")
    avery, avery_csrf = sign_in("avery")
    jude, jude_csrf = sign_in("jude")

    fake = FakeGithub()
    github_link.transport = fake

    # ---- A. off when unconfigured ----------------------------------------
    print("\n-- A. cleanly off when the credential files are absent --")
    r = avery.get("/api/me/github")
    check("my link state answers 200 with configured:false",
          r.status_code == 200 and r.json().get("configured") is False, r.text[:160])
    r = avery.get("/api/me/github/start", follow_redirects=False)
    check("start is a 404 github_not_configured, not a crash",
          r.status_code == 404 and r.json().get("error") == "github_not_configured",
          f"{r.status_code} {r.text[:120]}")
    r = put_access(owner, owner_csrf, "u-avery", True)
    check("the owner's approve is a 404 github_not_configured too",
          r.status_code == 404 and r.json().get("error") == "github_not_configured", r.text[:160])
    r = owner.get("/api/access")
    check("the owner grid reports github.configured:false and carries the account fields",
          r.status_code == 200 and r.json().get("github", {}).get("configured") is False
          and all("repo_access" in a and "github_login" in a for a in r.json()["accounts"]),
          r.text[:160])
    # a file that exists but is empty, and a missing repo, are also 'off'
    tmp = tempfile.mkdtemp()
    paths = {}
    for name, val in (("id", "client-id-123"), ("secret", "client-secret-456"),
                      ("token", "github_pat_FAKEINVITE")):
        paths[name] = os.path.join(tmp, name)
        with open(paths[name], "w", encoding="utf-8") as fh:
            fh.write(val + "\n")
    os.environ["ATLAS_GITHUB_OAUTH_CLIENT_ID_FILE"] = paths["id"]
    os.environ["ATLAS_GITHUB_OAUTH_CLIENT_SECRET_FILE"] = paths["secret"]
    os.environ["ATLAS_GITHUB_INVITE_TOKEN_FILE"] = os.path.join(tmp, "does-not-exist")
    os.environ["ATLAS_GITHUB_REPO"] = REPO
    check("one missing file (the invite token) is enough to be off",
          github_link.configured() is False
          and github_link.status()["missing"] == ["invite_token"], str(github_link.status()))
    os.environ["ATLAS_GITHUB_INVITE_TOKEN_FILE"] = paths["token"]
    open(paths["secret"], "w").close()
    check("an EMPTY file counts as missing", github_link.configured() is False)
    with open(paths["secret"], "w", encoding="utf-8") as fh:
        fh.write("client-secret-456\n")
    check("...and with all three present and a repo set it is on",
          github_link.configured() is True and github_link.status()["repo"] == REPO)
    check("no GitHub call was made while it was off", fake.github_calls() == 0)

    # ---- B. state -------------------------------------------------------
    print("\n-- B. the OAuth state is bound to the session --")
    r = avery.get("/api/me/github")
    check("configured now shows true and not linked",
          r.json().get("configured") is True and r.json().get("linked") is False, r.text[:160])
    r = avery.get("/api/me/github/start", follow_redirects=False)
    loc = r.headers.get("location", "")
    q = parse_qs(urlsplit(loc).query)
    check("start redirects to github.com authorize with our client id, callback and a state",
          r.status_code == 302 and loc.startswith("https://github.com/login/oauth/authorize?")
          and q.get("client_id") == ["client-id-123"]
          and q.get("redirect_uri") == [ORIGIN + "/api/me/github/callback"]
          and bool(q.get("state", [""])[0]), loc[:200])
    check("...and the client SECRET is nowhere in that URL", "client-secret" not in loc)
    good_state = q["state"][0]
    before = fake.github_calls()

    def callback(client, **params):
        return client.get("/api/me/github/callback", params=params, follow_redirects=False)

    r = callback(avery, code="good-code", state="garbage")
    check("a garbage state is refused (400 bad_state)",
          r.status_code == 400 and r.json().get("error") == "bad_state", f"{r.status_code} {r.text[:100]}")
    r = callback(jude, code="good-code", state=good_state)
    check("A STATE MINTED FOR ANOTHER SESSION is refused", r.status_code == 400
          and r.json().get("error") == "bad_state", f"{r.status_code} {r.text[:100]}")
    r = avery.get("/api/me/github/callback", params={"code": "good-code"}, follow_redirects=False)
    check("a missing state is refused", r.status_code == 400, f"{r.status_code}")
    sid = avery.cookies.get(auth.SESSION_COOKIE) or ""
    old = github_link.make_state(sid, auth._TOKEN_PEPPER, now=0)
    r = callback(avery, code="good-code", state=old)
    check("an EXPIRED state is refused", r.status_code == 400, f"{r.status_code}")
    check("none of those stored a link or touched GitHub",
          row_for("u-avery") is None and row_for("u-jude") is None
          and fake.github_calls() == before, f"calls {fake.github_calls() - before}")

    # ---- C. linking ------------------------------------------------------
    print("\n-- C. linking stores the verified id + login, and grants nothing --")
    r = callback(avery, code="good-code", state=good_state)
    check("a valid callback redirects back to Settings with github=linked",
          r.status_code == 303 and "github=linked" in r.headers.get("location", ""),
          f"{r.status_code} {r.headers.get('location')}")
    row = row_for("u-avery")
    check("the numeric id AND login were stored against the Atlas account",
          row is not None and row.github_id == 1001 and row.github_login == "avery-gh",
          str(row and (row.github_id, row.github_login)))
    check("...in state `linked` with no invitation", row.repo_access == "linked"
          and row.invitation_id is None)
    exch = [c for c in fake.calls if c[1].endswith("/login/oauth/access_token")]
    check("the code was exchanged server-side with the file-read client id + secret",
          len(exch) == 1 and exch[0][3].get("client_id") == "client-id-123"
          and exch[0][3].get("client_secret") == "client-secret-456"
          and exch[0][3].get("code") == "good-code", str(exch and exch[0][3]))
    check("LINKING MADE NO INVITATION CALL",
          not [c for c in fake.calls if c[0] in ("PUT", "DELETE")])
    with app.session() as s:
        blob = " ".join(str(v) for r_ in s.scalars(select(GithubLink)).all()
                        for v in (r_.user_id, r_.github_id, r_.github_login, r_.repo_access,
                                  r_.invitation_id))
        blob += " ".join(str((e.detail or "") + (e.reason or "")) for e in s.scalars(select(AuthEvent)).all())
    check("the user's OAuth access token is in no stored row or audit event",
          FAKE_USER_TOKEN not in blob)
    check("...and the table has no token-shaped column",
          not [c for c in GithubLink.__table__.columns.keys() if "token" in c or "secret" in c])
    with app.session() as s:
        ev = s.scalars(select(AuthEvent).where(AuthEvent.event == "github_linked")).all()
    check("the link is in the audit log", len(ev) == 1 and ev[0].user_id == "u-avery")
    me = avery.get("/api/me/github").json()
    check("my link state now reads linked as avery-gh",
          me.get("linked") is True and me.get("login") == "avery-gh"
          and me.get("repo_access") == "linked", str(me))

    # ---- D. uniqueness ---------------------------------------------------
    print("\n-- D. one GitHub identity per account --")
    r = link_via_oauth(jude, fake, (1001, "avery-gh"))
    check("jude cannot claim avery's GitHub identity", "reason=taken" in r.headers.get("location", "")
          and row_for("u-jude") is None, r.headers.get("location", ""))
    r = link_via_oauth(avery, fake, (1002, "jude-gh"))
    check("avery cannot silently swap to a different GitHub identity",
          "reason=already_linked" in r.headers.get("location", "")
          and row_for("u-avery").github_id == 1001, r.headers.get("location", ""))
    r = callback(avery, error="access_denied", state=start_and_state(avery))
    check("the user declining on GitHub returns reason=denied",
          "reason=denied" in r.headers.get("location", ""), r.headers.get("location", ""))
    r = link_via_oauth(jude, fake, (1002, "jude-gh"))
    check("jude links her own identity", row_for("u-jude") is not None
          and row_for("u-jude").github_id == 1002)

    # ---- E. who may approve ---------------------------------------------
    print("\n-- E. only the owner's browser session can approve --")
    mark = fake.github_calls()
    r = put_access(avery, avery_csrf, "u-avery", True)
    check("A MEMBER cannot approve their own access (403)", r.status_code == 403, f"{r.status_code} {r.text[:100]}")
    r = put_access(jude, jude_csrf, "u-avery", True)
    check("...nor anyone else's", r.status_code == 403)
    r = put_access(owner, None, "u-avery", True)
    check("the OWNER without a CSRF token is refused", r.status_code == 403
          and r.json().get("error") == "csrf_failed", f"{r.status_code} {r.text[:100]}")
    r = owner.put("/api/access/github/u-avery", json={"repo_access": True, "csrf_token": owner_csrf},
                  headers={"Origin": "https://evil.example"})
    check("...or from a foreign Origin", r.status_code == 403, f"{r.status_code}")
    r = avery.post("/api/access/github/u-avery/refresh", json={"csrf_token": avery_csrf},
                   headers={"Origin": ORIGIN})
    check("a member cannot refresh either", r.status_code == 403)
    # an admin-scoped token minted by the OWNER for their agent
    r = owner.post("/api/me/agent-credential", json={
        "purpose": "ghtest", "scopes": ["read", "write", "admin"], "expires_days": 7,
        "password": PW["g-owner"], "csrf_token": owner_csrf}, headers={"Origin": ORIGIN})
    admin_tok = r.json().get("token", "") if r.status_code == 201 else ""
    check("(setup) the owner minted an admin-scoped agent token", bool(admin_tok),
          f"{r.status_code} {r.text[:120]}")
    agent = TestClient(app.app, base_url=ORIGIN)
    agent.headers.update({"Authorization": "Bearer " + admin_tok})
    r = agent.put("/api/access/github/u-avery", json={"repo_access": True})
    check("AN ADMIN-SCOPED TOKEN cannot approve (owner axis refuses it)",
          r.status_code == 403, f"{r.status_code} {r.text[:120]}")
    r = agent.post("/api/access/github/u-avery/refresh", json={})
    check("...nor refresh", r.status_code == 403)
    ro = TestClient(app.app, base_url=ORIGIN)
    ro_raw = None
    r2 = owner.post("/api/me/agent-credential", json={
        "purpose": "ghread", "scopes": ["read"], "expires_days": 7,
        "password": PW["g-owner"], "csrf_token": owner_csrf}, headers={"Origin": ORIGIN})
    ro_raw = r2.json().get("token", "")
    ro.headers.update({"Authorization": "Bearer " + ro_raw})
    r = ro.put("/api/access/github/u-avery", json={"repo_access": True})
    check("a read-scoped token cannot either", r.status_code == 403)
    check("NONE OF THOSE REACHED GITHUB", fake.github_calls() == mark,
          f"{fake.github_calls() - mark} calls")
    check("...and avery's state is unchanged", row_for("u-avery").repo_access == "linked")

    # ---- H. session only -------------------------------------------------
    print("\n-- H. /api/me/github* are browser-session only --")
    for method, path in (("GET", "/api/me/github"), ("GET", "/api/me/github/start"),
                         ("GET", "/api/me/github/callback?code=x&state=y")):
        r = agent.request(method, path, follow_redirects=False)
        check("a token is refused on %s %s" % (method, path.split("?")[0]),
              r.status_code == 403, f"{r.status_code}")
    r = agent.post("/api/me/github/unlink", json={})
    check("a token cannot unlink", r.status_code == 403, f"{r.status_code}")

    # ---- F. approve / revoke --------------------------------------------
    print("\n-- F. approve sends the invitation; revoke removes it --")
    mark = len(fake.calls)
    fake.logins[1001] = "avery-renamed"          # avery renamed on GitHub since linking
    r = put_access(owner, owner_csrf, "u-avery", True)
    check("the owner's approve returns 200 state `invited`",
          r.status_code == 200 and r.json().get("repo_access") == "invited", f"{r.status_code} {r.text[:120]}")
    puts = [c for c in fake.calls[mark:] if c[0] == "PUT"]
    check("exactly one invitation call, to the configured repo and the CURRENT login (rename followed)",
          len(puts) == 1 and puts[0][1] == "%s/repos/%s/collaborators/avery-renamed" % (github_link.API, REPO),
          str([p[1] for p in puts]))
    check("...authenticated with the invite credential read from its file, as push",
          puts[0][2].get("Authorization") == "Bearer github_pat_FAKEINVITE"
          and puts[0][4] == {"permission": "push"}, str(puts[0][2].get("Authorization")))
    row = row_for("u-avery")
    check("Atlas stored invited + the invitation id + the refreshed login + who approved",
          row.repo_access == "invited" and row.invitation_id == 777
          and row.github_login == "avery-renamed" and row.repo_access_by == "u-g-owner",
          str((row.repo_access, row.invitation_id, row.github_login, row.repo_access_by)))
    r = owner.get("/api/access")
    acct = {a["username"]: a for a in r.json()["accounts"]}
    check("the grid shows the login and `invited`; unlinked accounts show not_linked",
          acct["avery"]["repo_access"] == "invited" and acct["avery"]["github_login"] == "avery-renamed"
          and acct["g-owner"]["repo_access"] == "not_linked", str({k: (v["repo_access"], v["github_login"]) for k, v in acct.items()}))
    check("...and the repo name for the heading", r.json()["github"]["repo"] == REPO)
    with app.session() as s:
        evs = [e.event for e in s.scalars(select(AuthEvent).where(AuthEvent.user_id == "u-avery")).all()]
    check("the approval is in the audit log", "github_repo_invited" in evs, str(evs))

    # refresh: still pending, then accepted
    fake.invitations = [{"id": 777, "invitee": {"id": 1001}}]
    r = owner.post("/api/access/github/u-avery/refresh", json={"csrf_token": owner_csrf},
                   headers={"Origin": ORIGIN})
    check("refresh while pending keeps `invited`", r.json().get("repo_access") == "invited", r.text[:100])
    fake.collaborators.add("avery-renamed")
    r = owner.post("/api/access/github/u-avery/refresh", json={"csrf_token": owner_csrf},
                   headers={"Origin": ORIGIN})
    check("refresh after acceptance flips to `collaborator`",
          r.json().get("repo_access") == "collaborator" and row_for("u-avery").invitation_id is None, r.text[:100])

    # revoke an accepted collaborator
    mark = len(fake.calls)
    r = put_access(owner, owner_csrf, "u-avery", False)
    dels = [c[1] for c in fake.calls[mark:] if c[0] == "DELETE"]
    check("untick removes the collaborator (DELETE collaborators/<login>)",
          r.status_code == 200 and r.json().get("repo_access") == "linked"
          and dels == ["%s/repos/%s/collaborators/avery-renamed" % (github_link.API, REPO)], str(dels))

    # revoke a pending invitation: cancels the invitation too
    fake.collaborators.clear()
    put_access(owner, owner_csrf, "u-avery", True)
    mark = len(fake.calls)
    r = put_access(owner, owner_csrf, "u-avery", False)
    dels = [c[1] for c in fake.calls[mark:] if c[0] == "DELETE"]
    check("untick while pending CANCELS THE INVITATION (DELETE invitations/777) and removes",
          "%s/repos/%s/invitations/777" % (github_link.API, REPO) in dels and len(dels) == 2
          and row_for("u-avery").repo_access == "linked" and row_for("u-avery").invitation_id is None, str(dels))

    # already a collaborator -> 204
    fake.invite_status = 204
    r = put_access(owner, owner_csrf, "u-avery", True)
    check("an invite answered 204 (already a collaborator) is recorded as collaborator",
          r.json().get("repo_access") == "collaborator", r.text[:100])
    fake.invite_status = 201

    # an account that never linked, an unknown account, a bot
    r = put_access(owner, owner_csrf, "u-g-owner", True)
    check("approving an account with no linked GitHub is a 404 not_linked",
          r.status_code == 404 and r.json().get("error") == "not_linked", r.text[:100])
    # GitHub failure leaves Atlas unchanged
    put_access(owner, owner_csrf, "u-avery", False)
    fake.invite_status = 422
    r = put_access(owner, owner_csrf, "u-jude", True)
    check("a GitHub refusal is a 502 and Atlas records nothing",
          r.status_code == 502 and row_for("u-jude").repo_access == "linked", f"{r.status_code} {r.text[:100]}")
    fake.invite_status = 201

    # ---- G. unlink -------------------------------------------------------
    print("\n-- G. unlinking --")
    put_access(owner, owner_csrf, "u-jude", True)
    fake.fail_deletes = True
    r = jude.post("/api/me/github/unlink", json={"csrf_token": jude_csrf}, headers={"Origin": ORIGIN})
    check("unlink with repo access held, GitHub failing: 502 and the LINK IS KEPT",
          r.status_code == 502 and row_for("u-jude") is not None, f"{r.status_code}")
    fake.fail_deletes = False
    mark = len(fake.calls)
    r = jude.post("/api/me/github/unlink", json={"csrf_token": jude_csrf}, headers={"Origin": ORIGIN})
    check("unlink revokes the repo access on GitHub first, then removes the link",
          r.status_code == 200 and row_for("u-jude") is None
          and any(c[0] == "DELETE" for c in fake.calls[mark:]), f"{r.status_code}")
    r = jude.post("/api/me/github/unlink", json={}, headers={"Origin": ORIGIN})
    check("unlink needs the CSRF token", r.status_code == 403)
    r = avery.post("/api/me/github/unlink", json={"csrf_token": avery_csrf}, headers={"Origin": ORIGIN})
    check("a person with no repo access unlinks without any GitHub call, and can relink",
          r.status_code == 200 and row_for("u-avery") is None)
    r = link_via_oauth(jude, fake, (1002, "jude-gh"))
    check("...after unlinking an identity can be linked again", row_for("u-jude") is not None)

    print()
    if failures:
        print("FAILED (%d): %s" % (len(failures), "; ".join(failures)))
        return 1
    print("All GitHub link checks passed.")
    return 0


if __name__ == "__main__":
    rc = main()
    sys.exit(rc)
