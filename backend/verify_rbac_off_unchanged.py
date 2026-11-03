"""
STORY-oss-11 / ADR 0041 §0: with `ATLAS_RBAC` unset (or `off`), Atlas is TODAY,
byte for byte — the property this story turns on.

  A. SETTINGS. `settings.load_rbac({})` is the documented default (off, shared,
     editor, implicit owner); each new variable accepts its values and refuses
     a bad one NAMING the variable; and the app REFUSES TO BOOT on a bad one
     (a child process importing `app` with ATLAS_RBAC=bogus).
  B. THE RESOLVER, against a FROZEN ORACLE. `rbac_testkit.oracle_visible` is
     `_visible_node_ids` as it stood on master before this story. On a fixture
     of today's model (shared and private projects, inner private subtrees,
     root and inner grants, a diamond, a cycle, bound work, people and agents
     of both roles, tokens of every scope) PLUS RBAC rows that would change the
     answer if anything read them, every principal's answer equals the
     oracle's: the same `None`, or the same set. Then the switch is flipped ON
     and the answers are required to MOVE, so the comparison is one that can
     fail.
  C. NOTHING READS THE NEW TABLES. Every RBAC reader is replaced with one that
     raises, and B's comparison, the write-role helper and real HTTP writes all
     still succeed.
  D. TODAY'S WRITE RULES over HTTP: a member's session deletes what it can see;
     an admin-scoped agent of a member deletes; a read token is refused on
     scope; and an account BOUND `owner` of a private project it holds no grant
     for still gets today's 404 — the binding is inert.

    python verify_rbac_off_unchanged.py
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys

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
for _k in ("ATLAS_RBAC", "ATLAS_DEFAULT_PROJECT_VISIBILITY", "ATLAS_SHARED_PROJECT_ROLE",
           "ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER"):
    os.environ.pop(_k, None)  # this script is about the DEFAULT; nothing may leak in

import dataclasses  # noqa: E402

import sqlalchemy as sa  # noqa: E402

import app  # noqa: E402
import deps  # noqa: E402
import rbac  # noqa: E402
import settings  # noqa: E402
from rbac_testkit import (  # noqa: E402
    T_ALICE, T_ALICE_BOT, T_BOB, T_CAROL, T_LONE_BOT, acting, bind, build_today_model,
    check, client, finish, group, mint, mk_edge, mk_node, oracle_visible, resolver, sign_in,
)


def section_a() -> None:
    print("\n-- A. settings: defaults are today, bad values refuse to boot --")
    d = settings.load_rbac({})
    check("load_rbac({}) is the documented default",
          d == settings.RbacSettings(mode="off", default_project_visibility="shared",
                                     shared_project_role="editor",
                                     instance_admin_implicit_project_owner=True), str(d))
    check("...and the default is OFF", d.enabled is False)
    check("the running app read the default (no ATLAS_RBAC in this environment)",
          rbac.SETTINGS == settings.RbacSettings() and not rbac.enabled(), str(rbac.SETTINGS))
    check("blank values read as the default",
          settings.load_rbac({"ATLAS_RBAC": " ", "ATLAS_SHARED_PROJECT_ROLE": ""}) == d)
    on = settings.load_rbac({"ATLAS_RBAC": "Projects", "ATLAS_DEFAULT_PROJECT_VISIBILITY": "private",
                             "ATLAS_SHARED_PROJECT_ROLE": "viewer",
                             "ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER": "0"})
    check("the work-install values parse", on == settings.RbacSettings(
        "projects", "private", "viewer", False) and on.enabled, str(on))
    for var, bad in (("ATLAS_RBAC", "groups"), ("ATLAS_DEFAULT_PROJECT_VISIBILITY", "public"),
                     ("ATLAS_SHARED_PROJECT_ROLE", "owner"),
                     ("ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER", "maybe")):
        try:
            settings.load_rbac({var: bad})
            ok, msg = False, "accepted"
        except settings.SettingsError as exc:
            ok, msg = var in str(exc), str(exc)
        check(f"{var}={bad!r} is refused, naming the variable", ok, msg)

    env = dict(os.environ, ATLAS_RBAC="bogus")
    p = subprocess.run([sys.executable, "-c", "import app"], cwd=os.path.dirname(os.path.abspath(__file__)),
                       env=env, capture_output=True, text=True, timeout=120)
    check("the app refuses to start with ATLAS_RBAC=bogus, naming it",
          p.returncode != 0 and "ATLAS_RBAC" in (p.stderr + p.stdout),
          f"exit {p.returncode}: {(p.stderr or p.stdout).strip().splitlines()[-1:] }")


def _rbac_rows_that_would_matter() -> None:
    """Bindings that change answers IF read: Carol owner of a project she cannot
    see today, Bob's group maintainer of another, the lone bot maintainer."""
    bind("t-private", "user", T_CAROL, "owner")
    group("g-t-bobs", "t-bobs", [T_BOB])
    bind("t-other-private", "group", "g-t-bobs", "maintainer")
    bind("t-private", "user", T_LONE_BOT, "maintainer")
    bind("t-shared", "user", T_ALICE, "viewer")  # would forbid her writes if read


def _answers(principals) -> dict:
    return {name: resolver(p) for name, p in principals.items()}


def _oracle(principals) -> dict:
    out = {}
    for name, p in principals.items():
        with app.session() as s:
            out[name] = oracle_visible(s, p)
    return out


def section_b(principals) -> None:
    print("\n-- B. the resolver equals the frozen pre-RBAC oracle, for every principal --")
    got, want = _answers(principals), _oracle(principals)
    for name in principals:
        g, w = got[name], want[name]
        check(f"{name}: identical to today", g == w and type(g) is type(w),
              "None" if g is None else f"{len(g)} visible ids")
    hidden_somewhere = [n for n, w in want.items() if w is not None and "t-private" not in w]
    check("the fixture really restricts someone (else equality proves nothing)",
          bool(hidden_somewhere), ", ".join(hidden_somewhere))

    prev = rbac.use(dataclasses.replace(rbac.SETTINGS, mode="projects"))
    try:
        moved = [n for n, a in _answers(principals).items() if a != want[n]]
    finally:
        rbac.use(prev)
    check("PERTURBATION: switching RBAC on moves at least one answer (Carol's binding is "
          "read)", "carol" in moved, ", ".join(moved))
    check("...and switching it off again restores every answer", _answers(principals) == want)


def section_c(principals) -> None:
    print("\n-- C. with RBAC off, no RBAC reader runs at all --")
    calls: list[str] = []

    def poisoned(name):
        def boom(*a, **k):
            calls.append(name)
            raise AssertionError(f"rbac.{name} ran with ATLAS_RBAC off")
        return boom

    saved = {n: getattr(rbac, n) for n in ("reach_of", "project_map", "visible_node_ids",
                                           "role_on_project")}
    for n in saved:
        setattr(rbac, n, poisoned(n))
    try:
        got = _answers(principals)
        check("every resolver answer still equals the oracle", got == _oracle(principals))
        with acting(principals["alice"]), app.session() as s:
            r = deps._require_project_role(s, ["t-shared-cap"], rbac.MAINTAINER, root_needs=rbac.OWNER)
        check("_require_project_role is None without reading anything", r is None)
        alice, csrf = sign_in("t-alice")
        r = alice.patch("/api/node/t-shared-cap", json={"name": "renamed by alice", "csrf_token": csrf})
        check("a real PATCH through the gate succeeds", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
        check("...and no RBAC reader was called", calls == [], str(calls))
    finally:
        for n, f in saved.items():
            setattr(rbac, n, f)


def section_d() -> None:
    print("\n-- D. today's write rules over HTTP, whatever the bindings say --")
    with app.session() as s:
        for nid in ("t-del-1", "t-del-2", "t-del-3"):
            mk_node(s, nid)
        s.flush()
        for nid in ("t-del-1", "t-del-2", "t-del-3"):
            mk_edge(s, "t-shared", nid)
        s.commit()

    alice, csrf = sign_in("t-alice")
    r = alice.request("DELETE", "/api/node/t-del-1?cascade=1", json={"csrf_token": csrf})
    check("a member's session deletes a node it can see (bound only `viewer`, which is inert)",
          r.status_code == 200, f"{r.status_code} {r.text[:160]}")

    admin_tok = mint(T_ALICE_BOT, ["read", "write", "admin"], "t-alice-admin")
    r = client(admin_tok).delete("/api/node/t-del-2?cascade=1")
    check("an admin-scoped agent of a MEMBER deletes (today's admin-scope rule)",
          r.status_code == 200, f"{r.status_code} {r.text[:160]}")

    read_tok = mint(T_ALICE_BOT, ["read"], "t-alice-read")
    r = client(read_tok).patch("/api/node/t-del-3", json={"name": "x"})
    check("a read token is refused on scope, as today",
          r.status_code == 403 and "write" in r.json().get("detail", ""), f"{r.status_code} {r.text[:160]}")

    carol, _ = sign_in("t-carol")
    r = carol.get("/api/node/t-private-cap")
    check("Carol, BOUND owner of t-private but not granted it, still gets today's 404",
          r.status_code == 404, f"{r.status_code}")
    bob, _ = sign_in("t-bob")
    r = bob.get("/api/node/t-other-cap")
    check("Bob's GROUP binding on t-other-private is inert too: 404", r.status_code == 404,
          f"{r.status_code}")

    insp = sa.inspect(app.engine)
    tables = set(insp.get_table_names())
    check("the four new tables exist after a normal startup (create_all)",
          {"auth_group", "auth_group_member", "project_role_binding",
           "auth_user_idp_state"} <= tables, str(sorted(t for t in tables if "group" in t or "role" in t)))


if __name__ == "__main__":
    app.startup()
    section_a()
    principals = build_today_model()
    _rbac_rows_that_would_matter()
    section_b(principals)
    section_c(principals)
    section_d()
    finish()
