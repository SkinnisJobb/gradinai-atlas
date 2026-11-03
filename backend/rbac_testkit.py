"""Shared harness for the project-RBAC verify scripts (STORY-oss-11).

NOT a verify script itself (`verify_all.py` discovers `verify_*.py` only). The
caller sets the environment and imports `app` FIRST, exactly as every verify
script does, then imports this; nothing here touches `os.environ`.

Three things live here because three scripts need the same answer:

  * `oracle_visible` — `deps._visible_node_ids` exactly as it stood on master
    before STORY-oss-11 (41570cf), FROZEN, as `verify_settings.py` freezes its
    pre-change parsers. "RBAC off is today, byte for byte" is a comparison
    against this, not against the code under test.
  * `build_today_model` — a fixture of TODAY'S access model and nothing newer:
    shared and private projects, a private subtree inside a shared project,
    grants on roots and inside, a diamond, a cycle, bound and unbound work, and
    people and agents of both roles with tokens of every scope.
  * the HTTP helpers (`client`, `sign_in`, `bearer`).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import select

import app
import auth
import models_auth
import passwords
from models_auth import AuthUser, NodeGrant, NodeVisibility
from models_graph import Edge, Node

TEST_ORIGIN = "https://testserver"
PW = "Correct-Horse-Battery-Rbac-9!"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def finish() -> None:
    import sys
    print()
    if failures:
        print(f"FAILED — {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")


# ---- rows ------------------------------------------------------------------ #
def mk_node(s, nid: str, ntype: str = "capability", name: str | None = None, **kw) -> None:
    s.add(Node(id=nid, type=ntype, name=name or nid, attributes=kw.pop("attributes", {}),
               attention_count=0, completion=0.0, origin="atlas", **kw))


def mk_edge(s, src: str, dst: str, etype: str = "contains") -> None:
    s.add(Edge(src=src, dst=dst, edge_type=etype, directed=True, origin="atlas"))


def mk_user(uid: str, username: str, role: str = "member", *, is_service: bool = False,
            person: str | None = None) -> None:
    with app.session() as s:
        if s.get(AuthUser, uid) is None:
            s.add(AuthUser(id=uid, subject=username, name=username, role=role,
                           is_service=is_service, must_change_password=False,
                           password_hash=None if is_service else passwords.hash_password(PW),
                           person_user_id=person, created_at=datetime.now(timezone.utc)))
            s.commit()


def mint(owner_id: str, scopes: list[str], label: str) -> str:
    with app.session() as s:
        raw, _row = auth.mint_service_token(s, owner=s.get(AuthUser, owner_id),
                                            scopes=scopes, label=label)
        s.commit()
        return raw


def bind(project_id: str, kind: str, principal_id: str, role: str) -> None:
    with app.session() as s:
        s.merge(models_auth.ProjectRoleBinding(project_id=project_id, principal_kind=kind,
                                               principal_id=principal_id, role=role))
        s.commit()


def unbind_all() -> None:
    with app.session() as s:
        for row in s.scalars(select(models_auth.ProjectRoleBinding)).all():
            s.delete(row)
        s.commit()


def group(gid: str, name: str, members: list[str]) -> None:
    with app.session() as s:
        if s.get(models_auth.AuthGroup, gid) is None:
            s.add(models_auth.AuthGroup(id=gid, name=name, source="local"))
        for uid in members:
            s.merge(models_auth.AuthGroupMember(group_id=gid, user_id=uid, source="local"))
        s.commit()


# ---- principals ------------------------------------------------------------ #
def person(uid: str, role: str = "member") -> auth.Principal:
    return auth.Principal(kind="session", user_id=uid, subject=uid, role=role,
                          is_service=False, must_change_password=False)


def agent(uid: str, scopes: tuple[str, ...], person_id: str | None) -> auth.Principal:
    return auth.Principal(kind="token", user_id=uid, subject=uid, role="member",
                          is_service=True, must_change_password=False, scopes=scopes,
                          person_user_id=person_id)


@contextmanager
def acting(principal):
    tok = auth.current_principal.set(principal)
    try:
        yield
    finally:
        auth.current_principal.reset(tok)


def resolver(principal):
    with acting(principal), app.session() as s:
        return app._visible_node_ids(s)


# ---- HTTP ------------------------------------------------------------------ #
def client(bearer_token: str | None = None):
    from fastapi.testclient import TestClient
    c = TestClient(app.app, base_url=TEST_ORIGIN)
    if bearer_token:
        c.headers["Authorization"] = "Bearer " + bearer_token
    return c


def sign_in(username: str):
    """A signed-in browser client and its CSRF token."""
    c = client()
    page = c.get("/auth/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": username, "password": PW, "csrf_token": token},
           headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
    csrf = c.get("/api/me").json()["csrf_token"]
    c.headers["Origin"] = TEST_ORIGIN
    return c, csrf


# ---- the frozen oracle ------------------------------------------------------ #
def oracle_visible(s, principal):
    """`deps._visible_node_ids` at 41570cf, verbatim in logic. Do not edit to
    match new behaviour: the point is that it does not move."""
    from attention import effective_bindings
    if principal is None:
        return set()
    if principal.is_owner:
        return None
    private_ids = set(s.scalars(select(NodeVisibility.node_id).where(
        NodeVisibility.visibility == models_auth.VISIBILITY_PRIVATE)).all())
    if not private_ids:
        return None
    granted_ids: set[str] = set()
    holders = [u for u in (principal.user_id, principal.person_user_id) if u]
    if holders:
        granted_ids = set(s.scalars(select(NodeGrant.node_id).where(
            NodeGrant.user_id.in_(holders))).all())
    blocked = private_ids - granted_ids
    if not blocked:
        return None
    all_ids = set(s.scalars(select(Node.id)).all())
    parents_of: dict[str, list[str]] = {}
    for src, dst in s.execute(select(Edge.src, Edge.dst).where(Edge.edge_type == "contains")).all():
        parents_of.setdefault(dst, []).append(src)
    binding_of = effective_bindings(s)
    memo: dict[str, bool] = {}

    def visible(nid, path):
        if nid in memo:
            return memo[nid]
        if nid in path:
            memo[nid] = False
            return False
        if nid in blocked:
            memo[nid] = False
            return False
        b = binding_of.get(nid)
        if b is not None and not visible(b, path | {nid}):
            memo[nid] = False
            return False
        ps = parents_of.get(nid) or []
        if not ps:
            memo[nid] = True
            return True
        ok = True
        for p in ps:
            if p not in all_ids or not visible(p, path | {nid}):
                ok = False
                break
        memo[nid] = ok
        return ok

    return {nid for nid in all_ids if visible(nid, frozenset())}


# ---- today's model ---------------------------------------------------------- #
#: Accounts in the today fixture.
T_OWNER, T_ALICE, T_BOB, T_CAROL = "u-t-owner", "u-t-alice", "u-t-bob", "u-t-carol"
T_OWNER_BOT, T_ALICE_BOT, T_LONE_BOT = "u-t-owner-bot", "u-t-alice-bot", "u-t-lone-bot"


def build_today_model() -> dict:
    """Today's access model, with NO RBAC rows. Returns the principals to
    compare, keyed by a readable name."""
    with app.session() as s:
        for nid, t in (("t-shared", "project"), ("t-shared-cap", "capability"),
                       ("t-shared-secret", "capability"), ("t-shared-secret-kid", "capability"),
                       ("t-private", "project"), ("t-private-cap", "capability"),
                       ("t-other-private", "project"), ("t-other-cap", "capability"),
                       ("t-org", "org"), ("t-org-proj", "project"), ("t-org-proj-cap", "capability"),
                       ("t-dia", "capability"), ("t-cyc-x", "capability"), ("t-cyc-y", "capability"),
                       ("t-epic", "epic"), ("t-story", "story"), ("t-task-private", "task"),
                       ("t-task-shared", "task"), ("t-task-loose", "task")):
            mk_node(s, nid, t)
        s.flush()
        for a, b in (("t-shared", "t-shared-cap"), ("t-shared", "t-shared-secret"),
                     ("t-shared-secret", "t-shared-secret-kid"), ("t-private", "t-private-cap"),
                     ("t-other-private", "t-other-cap"), ("t-org", "t-org-proj"),
                     ("t-org-proj", "t-org-proj-cap"), ("t-shared", "t-dia"), ("t-private", "t-dia"),
                     ("t-cyc-x", "t-cyc-y"), ("t-cyc-y", "t-cyc-x"),
                     ("t-epic", "t-story"), ("t-story", "t-task-private")):
            mk_edge(s, a, b)
        mk_edge(s, "t-story", "t-private-cap", "works_on")
        mk_edge(s, "t-task-shared", "t-shared-cap", "works_on")
        for nid in ("t-shared-secret", "t-private", "t-other-private", "t-cyc-x"):
            s.add(NodeVisibility(node_id=nid, visibility="private"))
        s.add(NodeGrant(node_id="t-private", user_id=T_ALICE))           # root grant
        s.add(NodeGrant(node_id="t-shared-secret", user_id=T_BOB))       # inner grant
        s.add(NodeGrant(node_id="t-other-private", user_id=T_ALICE_BOT))  # the agent's own
        s.commit()
    mk_user(T_OWNER, "t-owner", "owner")
    mk_user(T_ALICE, "t-alice")
    mk_user(T_BOB, "t-bob")
    mk_user(T_CAROL, "t-carol")
    mk_user(T_OWNER_BOT, "bot-t-owner", is_service=True, person=T_OWNER)
    mk_user(T_ALICE_BOT, "bot-t-alice", is_service=True, person=T_ALICE)
    mk_user(T_LONE_BOT, "bot-t-lone", is_service=True)
    out = {
        "owner": person(T_OWNER, "owner"), "alice": person(T_ALICE), "bob": person(T_BOB),
        "carol": person(T_CAROL), "no-user-id": person(None),
    }
    for scopes in (("read",), ("read", "write"), ("read", "write", "admin"), ("ingest",)):
        tag = "+".join(scopes)
        out["owner-bot " + tag] = agent(T_OWNER_BOT, scopes, T_OWNER)
        out["alice-bot " + tag] = agent(T_ALICE_BOT, scopes, T_ALICE)
        out["lone-bot " + tag] = agent(T_LONE_BOT, scopes, None)
    return out
