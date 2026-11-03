"""
Verification for IdP group sync (STORY-oss-13, ADR 0041 §3) over OSS-11's
`auth_group` / `auth_group_member`, driven by real OIDC logins against the stub
IdP of `oidc_testkit` (no network). ATLAS_AUTH_PROVIDERS=oidc alone here, so this
also runs the provider without `local`.

  A. THE ALLOWLIST FILE: each malformed ATLAS_OIDC_GROUP_MAP_FILE (not a mapping,
     no `groups:`, an extra key, a non-string name, bad YAML, unreadable) raises
     SettingsError naming the variable, and a broken file stops the boot.
  B. FIRST LOGIN: allowlisted groups become `source='idp'` memberships (the Atlas
     groups created as `source='idp'` with the IdP value as external_id); values
     not in the map are ignored and materialise nothing.
  C. NEXT LOGINS REPLACE THE IdP SET: a group dropped from the claim loses its IdP
     membership, a new one gains one, and LOCAL memberships, including a local
     membership of the very group whose IdP membership was removed, stay.
  D. A mapping onto an EXISTING local group adds an IdP membership to it and
     leaves the group itself local.
  E. A claim with no allowlisted group empties the user's IdP memberships and
     touches nothing local.
  F. A REFUSED login changes no membership (rolled back with the login).
  G. One user's sync never touches another user's memberships.

Run:  python verify_group_sync.py
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
import uuid
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
TMP = tempfile.mkdtemp(prefix="atlas-group-sync-")

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
MAPPING = {"g-alpha": "alpha", "g-beta": "beta", "/kc/gamma": "gamma", "g-ops": "ops",
           "g-alpha-alias": "alpha"}
GROUP_MAP = oidc_testkit.write_group_map(TMP, MAPPING)
os.environ.update(STUB.env(TMP, ATLAS_AUTH_PROVIDERS="oidc",
                           ATLAS_OIDC_REQUIRED_GROUPS="g-member,g-alpha,g-beta",
                           ATLAS_OIDC_GROUP_MAP_FILE=GROUP_MAP))

import identity.oidc  # noqa: E402

identity.oidc.use_transport(STUB.transport)

from sqlalchemy import select  # noqa: E402

import app  # noqa: E402
import auth  # noqa: E402
import settings  # noqa: E402
from identity import groups as idgroups  # noqa: E402
from models_auth import AuthEvent, AuthGroup, AuthGroupMember, AuthUser  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def client():
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN)


def login(sub: str, groups, **kw) -> bool:
    for ip in ("testclient", ""):
        auth._IP_LIMITER.reset(auth._login_key(ip))
    c = client()
    r = c.get("/auth/oidc/start", follow_redirects=False)
    cb = STUB.authorize(r.headers["location"], {"sub": sub, "groups": list(groups)}, **kw)
    r = c.get(cb, follow_redirects=False)
    return r.status_code == 303 and c.get("/api/me").status_code == 200


def user_id(sub: str) -> str:
    with app.session() as s:
        return s.scalar(select(AuthUser.id).where(AuthUser.idp == "oidc", AuthUser.subject == sub))


def memberships(uid: str) -> set:
    """{(group name, source)} for one user."""
    with app.session() as s:
        rows = s.execute(select(AuthGroup.name, AuthGroupMember.source).join(
            AuthGroupMember, AuthGroupMember.group_id == AuthGroup.id).where(
            AuthGroupMember.user_id == uid)).all()
    return {(n, src) for n, src in rows}


def mk_local_group(name: str) -> str:
    with app.session() as s:
        g = s.scalar(select(AuthGroup).where(AuthGroup.name == name))
        if g is None:
            g = AuthGroup(id=str(uuid.uuid4()), name=name, source="local")
            s.add(g)
            s.commit()
        return g.id


def add_local_member(group: str, uid: str) -> None:
    with app.session() as s:
        gid = s.scalar(select(AuthGroup.id).where(AuthGroup.name == group))
        s.add(AuthGroupMember(group_id=gid, user_id=uid, source="local", added_by=None))
        s.commit()


# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. ATLAS_OIDC_GROUP_MAP_FILE: the allowlist, validated --")
    cases = [("a list, not a mapping", "- a\n- b\n"),
             ("no `groups:` key", "teams:\n  a: b\n"),
             ("an extra top-level key", "groups:\n  a: b\nother: 1\n"),
             ("a non-string Atlas group name", "groups:\n  a: 5\n"),
             ("an empty Atlas group name", "groups:\n  a: ''\n"),
             ("invalid YAML", "groups: [unclosed\n")]
    for label, body in cases:
        path = os.path.join(TMP, "bad-%d.yml" % abs(hash(label)))
        Path(path).write_text(body, encoding="utf-8")
        try:
            idgroups.load_group_map(path)
            check(f"refused: {label}", False, "accepted")
        except settings.SettingsError as exc:
            check(f"refused: {label} (names ATLAS_OIDC_GROUP_MAP_FILE)",
                  "ATLAS_OIDC_GROUP_MAP_FILE" in str(exc), str(exc)[:140])
    try:
        idgroups.load_group_map(os.path.join(TMP, "does-not-exist.yml"))
        check("refused: an unreadable path", False)
    except settings.SettingsError as exc:
        check("refused: an unreadable path", "ATLAS_OIDC_GROUP_MAP_FILE" in str(exc))
    check("no file configured is an empty allowlist", idgroups.load_group_map(None) == {})
    check("the good file loads as written", idgroups.load_group_map(GROUP_MAP) == MAPPING)
    bad = os.path.join(TMP, "boot-bad.yml")
    Path(bad).write_text("- not a mapping\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_TEST_DB")}
    env["ATLAS_OIDC_GROUP_MAP_FILE"] = bad
    proc = subprocess.run([sys.executable, "-c", "import app"], cwd=str(HERE), env=env,
                          capture_output=True, text=True, timeout=120)
    check("a broken map file stops the boot, naming the variable",
          proc.returncode != 0 and "ATLAS_OIDC_GROUP_MAP_FILE" in proc.stderr,
          proc.stderr.strip().splitlines()[-1:] and proc.stderr.strip().splitlines()[-1])


# --------------------------------------------------------------------------- #
def section_b() -> str:
    print("\n-- B. first login: allowlisted groups only --")
    ok = login("sync-ann", ["g-alpha", "g-beta", "unmapped-1", "unmapped-2", "g-alpha-alias"])
    uid = user_id("sync-ann")
    check("(sync-ann signs in by SSO)", ok and uid is not None)
    check("IdP memberships = alpha, beta (two values mapping to alpha make one membership)",
          memberships(uid) == {("alpha", "idp"), ("beta", "idp")}, str(sorted(memberships(uid))))
    with app.session() as s:
        alpha = s.scalar(select(AuthGroup).where(AuthGroup.name == "alpha"))
        names = set(s.scalars(select(AuthGroup.name)).all())
        added = s.scalars(select(AuthEvent).where(AuthEvent.event == "group_member_added",
                                                  AuthEvent.user_id == uid)).all()
    check("a mapped group that did not exist is created source=idp, idp=oidc, external_id=the value",
          alpha is not None and (alpha.source, alpha.idp, alpha.external_id) == ("idp", "oidc", "g-alpha"))
    check("an unmapped value materialises nothing", not ({"unmapped-1", "unmapped-2"} & names), str(names))
    check("each added membership is an auth_event", len(added) == 2, str(len(added)))
    return uid


def section_c(uid: str) -> None:
    print("\n-- C. next logins replace the IdP set; local memberships stay --")
    mk_local_group("ops-local")
    add_local_member("ops-local", uid)
    add_local_member("alpha", uid)          # a LOCAL membership of a group sync manages
    before = memberships(uid)
    check("(setup: local memberships in ops-local and alpha beside the IdP ones)",
          {("ops-local", "local"), ("alpha", "local"), ("alpha", "idp")} <= before, str(sorted(before)))
    ok = login("sync-ann", ["g-beta"])
    after = memberships(uid)
    check("login with only g-beta: the IdP alpha membership is removed, beta kept",
          ok and ("alpha", "idp") not in after and ("beta", "idp") in after, str(sorted(after)))
    check("...and the LOCAL alpha and ops-local memberships are untouched",
          {("alpha", "local"), ("ops-local", "local")} <= after)
    ok = login("sync-ann", ["g-alpha", "/kc/gamma"])
    after = memberships(uid)
    check("login with g-alpha + /kc/gamma: beta (idp) gone, alpha (idp) back, gamma created",
          ok and after == {("alpha", "idp"), ("gamma", "idp"), ("alpha", "local"),
                           ("ops-local", "local")}, str(sorted(after)))
    with app.session() as s:
        removed = s.scalars(select(AuthEvent).where(AuthEvent.event == "group_member_removed",
                                                    AuthEvent.user_id == uid)).all()
    check("each removal is an auth_event", len(removed) == 2, str(len(removed)))


def section_d() -> None:
    print("\n-- D. a mapping onto an existing LOCAL group --")
    mk_local_group("ops")
    ok = login("sync-bo", ["g-member", "g-ops"])
    uid = user_id("sync-bo")
    with app.session() as s:
        ops = s.scalar(select(AuthGroup).where(AuthGroup.name == "ops"))
    check("the user gets an IdP membership of the existing group",
          ok and memberships(uid) == {("ops", "idp")}, str(sorted(memberships(uid))))
    check("...and the group itself stays source=local", ops.source == "local" and ops.external_id is None)


def section_e(uid: str) -> None:
    print("\n-- E. a claim with no allowlisted group --")
    ok = login("sync-ann", ["g-member"])
    check("only an unmapped (required) group: every IdP membership is removed, local ones stay",
          ok and memberships(uid) == {("alpha", "local"), ("ops-local", "local")},
          str(sorted(memberships(uid))))


def section_f(uid: str) -> None:
    print("\n-- F. a refused login changes no membership --")
    login("sync-ann", ["g-alpha"])
    before = memberships(uid)
    ok = login("sync-ann", ["g-beta", "/kc/gamma"], id_token_overrides={"nonce": "wrong"})
    check("a refused login (bad nonce) leaves the memberships exactly as they were",
          not ok and memberships(uid) == before, str(sorted(memberships(uid))))
    ok = login("sync-ann", ["/kc/gamma"])   # gamma is mapped but not a required group
    check("a login refused for want of a required group changes nothing either",
          not ok and memberships(uid) == before, str(sorted(memberships(uid))))


def section_g(ann: str) -> None:
    print("\n-- G. one user's sync never touches another's --")
    bo = user_id("sync-bo")
    before = memberships(bo)
    login("sync-ann", ["g-beta"])
    check("sync-ann's login leaves sync-bo's memberships as they were",
          memberships(bo) == before and ("beta", "idp") in memberships(ann), str(sorted(before)))


def main() -> int:
    app.startup()
    try:
        section_a()
        ann = section_b()
        section_c(ann)
        section_d()
        section_e(ann)
        section_f(ann)
        section_g(ann)
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
