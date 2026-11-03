"""
Verification for the access layer (STORY-atlas-access-admin): per-account grants
over the private/shared visibility mark, and the enforcement that makes them
mean something.

No pytest suite exists in this repo (see verify_object_graph.py's docstring), so
this follows the standalone run-it-and-read-it convention against a real,
disposable Postgres via `testdb` (TASK-1044).

SIX SECTIONS:

  A. THE RESOLVER (`app._visible_node_ids`) against a hand-built graph: a
     private root is absent for a member and present for its grantee; a shared
     child of a private parent stays hidden (ALL-over-parents, not ANY); a
     grant on a parent reaches its whole subtree without a subtree concept
     existing; a diamond with one private parent stays hidden; a `contains`
     cycle fails CLOSED; a principal with no user_id holds no grants.

  C. THE ROLLUP LEAK, which is the failure the whole feature turns on: a
     hidden child must not colour its parent, must not count toward its
     `attention_count`, `completion` or `work_load`. Asserted by DELETION
     EQUIVALENCE — the numbers are compared against the same query run over a
     database with those rows physically removed, because "the hidden node is
     absent" is only true if no derived value still carries it. Run for a
     PERSON and for an AGENT (TASK-1114): since grants inherit down the person
     link, an agent reaches the resolver by a different route and a leak on one
     path would be invisible on the other.

  D. THE ROUTES, over HTTP through the real auth gate, as two principals at
     once: `/api/graph/tree`, `/api/graph`, `/api/node/{id}`, the doc routes
     and the write routes. A hidden id must 404 identically to a nonexistent
     one, and an edge id must not be enumerable.

  E. THE ADMIN API: owner-only (403 for a member), `admin` scope, the
     shared-discards-the-mark rule, whole-list grant replacement, and the
     400s naming their field.

  F. ROUTE COVERAGE — the one that survives this story. Every route in
     `route_table.live_routes(app.app)` must appear in COVERAGE below, classified either as
     access-filtered or as reading no node data. A route added later with no
     entry FAILS this section rather than silently shipping unfiltered, which
     is the acceptance criterion "a test that fails if a future endpoint
     forgets to apply it".

  F2. PARAMETER COVERAGE (TASK-1121) — the same rule one level down. Every
     QUERY PARAMETER of every route classified access-filtered must appear in
     PARAM_COVERAGE, classified as selecting node rows or as only shaping the
     response. A route is not a unit of access review once its parameters are
     what decide which rows come back: `?assignee=` (TASK-1118) arrived on an
     already-classified `/api/workitems` and section F stayed green the whole
     time. Both sides are enumerated live — the routes from `live_routes(app.app)`,
     the parameters from each route's `dependant.query_params` — so an
     unclassified parameter and a stale entry each fail here.

Run (from a venv with the backend requirements installed):
    python verify_node_access.py
"""
from __future__ import annotations

import base64
import os
import sys
from datetime import datetime, timezone

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

TEST_ORIGIN = "https://testserver"

from sqlalchemy import func, select  # noqa: E402

# STORY-oss-08: this script pins the default route set: COVERAGE names every route, the GitHub ones included,
# so it runs with every feature on whatever the caller set; verify_feature_flags.py
# proves the switched-off side. Before the app import: main.py reads the flags then.
os.environ.pop("ATLAS_DISABLED_FEATURES", None)
import app  # noqa: E402
import auth  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthEvent, AuthUser, NodeGrant, NodeVisibility  # noqa: E402
from models_graph import Edge, GraphEvent, Node  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def mk_node(s, nid: str, ntype: str = "capability") -> None:
    s.add(Node(id=nid, type=ntype, name=nid, attributes={}, attention_count=0,
               completion=0.0, origin="atlas"))


def mk_edge(s, src: str, dst: str, etype: str = "contains") -> None:
    s.add(Edge(src=src, dst=dst, edge_type=etype, directed=True, origin="atlas"))


def mk_user(uid: str, username: str, role: str, password: str) -> None:
    with app.session() as s:
        if not s.scalar(select(AuthUser).where(AuthUser.subject == username)):
            s.add(AuthUser(id=uid, subject=username, name=username, role=role,
                           is_service=False, must_change_password=False,
                           password_hash=passwords.hash_password(password),
                           created_at=datetime.now(timezone.utc)))
            s.commit()


def as_principal(user_id: str | None, role: str = "member"):
    """Install a principal in the ContextVar the resolver reads, and return the
    token to reset with. The resolver is deliberately request-agnostic (it
    reads `auth.current_principal`, not a Request), so section A can exercise
    it directly without going through HTTP — and sections D/E then prove the
    HTTP path agrees.
    """
    return auth.current_principal.set(auth.Principal(
        kind="session", user_id=user_id, subject=user_id or "anon", role=role,
        is_service=False, must_change_password=False, scopes=("read", "write", "admin"),
    ))


OWNER_ID, MEMBER_ID, GUEST_ID = "u-owner", "u-member", "u-guest"
OWNER_PW = "Correct-Horse-Battery-Owner-9!"
MEMBER_PW = "Correct-Horse-Battery-Member-9!"


# --------------------------------------------------------------------------- #
# A. The resolver
# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. resolver: ALL-over-parents, grants, fail-closed --")
    with app.session() as s:
        for nid in ("a-pub", "a-pub-kid", "a-priv", "a-priv-kid", "a-priv-grandkid",
                    "a-dia", "a-cyc-x", "a-cyc-y", "a-lonely"):
            mk_node(s, nid)
        s.flush()
        mk_edge(s, "a-pub", "a-pub-kid")
        mk_edge(s, "a-priv", "a-priv-kid")
        mk_edge(s, "a-priv-kid", "a-priv-grandkid")
        mk_edge(s, "a-pub", "a-dia")
        mk_edge(s, "a-priv", "a-dia")          # diamond: one parent private
        mk_edge(s, "a-cyc-x", "a-cyc-y")
        mk_edge(s, "a-cyc-y", "a-cyc-x")       # cycle
        s.add(NodeVisibility(node_id="a-priv", visibility="private"))
        s.add(NodeVisibility(node_id="a-cyc-x", visibility="private"))
        s.commit()

    def visible_for(user_id, role="member"):
        tok = as_principal(user_id, role)
        try:
            with app.session() as s:
                return app._visible_node_ids(s)
        finally:
            auth.current_principal.reset(tok)

    owner_view = visible_for(OWNER_ID, "owner")
    check("owner is unrestricted (resolver returns None)", owner_view is None,
          f"got {type(owner_view).__name__}")

    m = visible_for(MEMBER_ID)
    check("private root hidden from a member", m is not None and "a-priv" not in m)
    check("child of a private node hidden too (ALL, not ANY)",
          m is not None and "a-priv-kid" not in m)
    check("grandchild of a private node hidden too",
          m is not None and "a-priv-grandkid" not in m)
    check("diamond with ONE private parent stays hidden",
          m is not None and "a-dia" not in m,
          "ANY-semantics would un-hide it via the public parent")
    check("public subtree unaffected",
          m is not None and {"a-pub", "a-pub-kid"} <= m)
    check("`contains` cycle fails CLOSED",
          m is not None and "a-cyc-y" not in m)

    # Grant the private ROOT to the member; the whole subtree must follow.
    with app.session() as s:
        s.add(NodeGrant(node_id="a-priv", user_id=MEMBER_ID, granted_by=OWNER_ID))
        s.commit()
    m2 = visible_for(MEMBER_ID)
    check("granting the root reveals it", m2 is None or "a-priv" in m2)
    check("granting the root reveals its whole subtree",
          m2 is None or {"a-priv-kid", "a-priv-grandkid"} <= m2,
          "no subtree concept exists — the children resolve because their parent does")
    check("the diamond child follows its now-visible parent",
          m2 is None or "a-dia" in m2)

    g = visible_for(GUEST_ID)
    check("a DIFFERENT member is unaffected by someone else's grant",
          g is not None and "a-priv" not in g)
    check("the cycle stays closed even for the grantee",
          m2 is None or "a-cyc-y" not in m2)

    nobody = visible_for(None)
    check("a principal with no user_id holds no grants",
          nobody is not None and "a-priv" not in nobody)

    # THE BINDING RULE — found in a browser on 2026-08-16, not by this suite,
    # which is why it is written down here rather than only fixed. A workitem
    # bound to a private object is not itself marked and has no `contains`
    # parent of its own, so the containment rule alone leaves it VISIBLE: the
    # board rendered the private project's task names under its "not bound to a
    # project" bucket, and the bucket's count stated how many there were.
    with app.session() as s:
        mk_node(s, "a-priv-epic", "epic")
        mk_node(s, "a-priv-task", "task")
        mk_node(s, "a-pub-task", "task")
        s.flush()
        mk_edge(s, "a-priv-epic", "a-priv-task")     # task inherits the epic's binding
        mk_edge(s, "a-priv-epic", "a-priv", "works_on")
        mk_edge(s, "a-pub-task", "a-pub", "works_on")
        s.commit()

    b = visible_for(GUEST_ID)   # no grant on a-priv
    check("a workitem bound to a private object is hidden with it",
          b is not None and "a-priv-epic" not in b)
    check("...and so is a task INHERITING that binding down the work hierarchy",
          b is not None and "a-priv-task" not in b,
          "effective bindings, not raw works_on edges")
    check("a workitem bound to a shared object is unaffected",
          b is not None and "a-pub-task" in b)

    b2 = visible_for(MEMBER_ID)  # was granted a-priv earlier in this section
    check("the grantee sees the private object's work too",
          b2 is None or {"a-priv-epic", "a-priv-task"} <= b2)

    tok = auth.current_principal.set(None)
    try:
        with app.session() as s:
            none_view = app._visible_node_ids(s)
    finally:
        auth.current_principal.reset(tok)
    check("NO principal at all sees NOTHING (fail closed)",
          none_view == set(), f"got {none_view!r}")

    # Clean up so later sections start from a known state.
    with app.session() as s:
        for row in s.scalars(select(NodeGrant)).all():
            s.delete(row)
        s.commit()


# --------------------------------------------------------------------------- #
# C. The rollup leak, by deletion equivalence
# --------------------------------------------------------------------------- #
def section_c() -> None:
    print("\n-- C. rollups: a hidden child must not colour or count toward its parent --")
    import attention

    with app.session() as s:
        mk_node(s, "c-root", "project")
        mk_node(s, "c-open", "capability")
        mk_node(s, "c-secret", "capability")
        mk_node(s, "c-task-open", "task")
        mk_node(s, "c-task-secret", "task")
        s.flush()
        mk_edge(s, "c-root", "c-open")
        mk_edge(s, "c-root", "c-secret")
        mk_edge(s, "c-open", "c-task-open")
        mk_edge(s, "c-secret", "c-task-secret")
        mk_edge(s, "c-task-open", "c-open", "works_on")
        mk_edge(s, "c-task-secret", "c-secret", "works_on")
        s.add(NodeVisibility(node_id="c-secret", visibility="private"))
        s.commit()

    tok = as_principal(MEMBER_ID)
    try:
        with app.session() as s:
            vis = app._visible_node_ids(s)
            member_counts = attention.derive(s, visible_ids=vis)
            member_loads = attention.work_load(s, visible_ids=vis)
    finally:
        auth.current_principal.reset(tok)

    # THE SAME ORACLE, FOR AN AGENT (TASK-1114). Deletion equivalence was only
    # ever asserted for a HUMAN principal, and since grants now inherit down a
    # person link an agent reaches `_visible_node_ids` by a different route —
    # `granted_ids` is a UNION over two ids rather than a lookup on one. A
    # rollup that leaks for an agent and not for its operator would be invisible
    # to every check above, so the agent's numbers are measured here too and
    # compared against the same physically-deleted database below.
    #
    # This agent is linked to the member and holds NO grant of its own, so it
    # inherits an empty-of-`c-secret` set: both principals must land on exactly
    # the deleted database's numbers.
    with app.session() as s:
        if not s.get(AuthUser, "u-c-bot"):
            s.add(AuthUser(id="u-c-bot", subject="verify-c-bot", name="Verify C Bot",
                           role="member", is_service=True, must_change_password=False,
                           person_user_id=MEMBER_ID,
                           created_at=datetime.now(timezone.utc)))
            s.commit()
    bot_tok = auth.current_principal.set(auth.Principal(
        kind="token", user_id="u-c-bot", subject="verify-c-bot", role="member",
        is_service=True, must_change_password=False, scopes=("read",),
        person_user_id=MEMBER_ID))
    try:
        with app.session() as s:
            bot_vis = app._visible_node_ids(s)
            agent_counts = attention.derive(s, visible_ids=bot_vis)
            agent_loads = attention.work_load(s, visible_ids=bot_vis)
    finally:
        auth.current_principal.reset(bot_tok)

    check("an agent sees the same restricted set as the person it belongs to",
          bot_vis == vis, f"agent={bot_vis!r} person={vis!r}")

    check("the hidden node is absent from the member's rollup",
          "c-secret" not in member_counts)
    check("the hidden node's work is absent from the member's work_load",
          "c-secret" not in member_loads)

    root_load = member_loads.get("c-root", {}).get("count", 0)
    check("the parent's work_load does not count the hidden subtree",
          "c-task-secret" not in member_loads.get("c-root", {}).get("workitem_ids", []),
          f"root workitem_ids={member_loads.get('c-root', {}).get('workitem_ids')}")

    member_root = member_counts.get("c-root", {})

    # THE ORACLE: physically delete the hidden rows and their incident edges,
    # then recompute UNFILTERED. Equal numbers is the pass condition — anything
    # else means a derived value still carries the hidden node.
    with app.session() as s:
        for e in s.scalars(select(Edge)).all():
            if e.src in ("c-secret", "c-task-secret") or e.dst in ("c-secret", "c-task-secret"):
                s.delete(e)
        for nid in ("c-secret", "c-task-secret"):
            n = s.get(Node, nid)
            if n is not None:
                s.delete(n)
        s.commit()
    with app.session() as s:
        deleted_counts = attention.derive(s)
        deleted_loads = attention.work_load(s)
    deleted_root = deleted_counts.get("c-root", {})

    agent_root = agent_counts.get("c-root", {})
    agent_load = agent_loads.get("c-root", {}).get("count", 0)

    for field in ("attention", "attention_count", "completion", "done", "in_progress", "todo"):
        check(f"deletion equivalence — c-root.{field}",
              member_root.get(field) == deleted_root.get(field),
              f"member={member_root.get(field)!r} deleted={deleted_root.get(field)!r}")
        check(f"deletion equivalence for an AGENT — c-root.{field}",
              agent_root.get(field) == deleted_root.get(field),
              f"agent={agent_root.get(field)!r} deleted={deleted_root.get(field)!r}")
    check("deletion equivalence — c-root work_load count",
          root_load == deleted_loads.get("c-root", {}).get("count", 0),
          f"member={root_load} deleted={deleted_loads.get('c-root', {}).get('count', 0)}")
    check("deletion equivalence for an AGENT — c-root work_load count",
          agent_load == deleted_loads.get("c-root", {}).get("count", 0),
          f"agent={agent_load} deleted={deleted_loads.get('c-root', {}).get('count', 0)}")


# --------------------------------------------------------------------------- #
# D. The routes, over HTTP, as two principals
# --------------------------------------------------------------------------- #
def _client():
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN)


def _sign_in(client, username: str, password: str) -> str:
    page = client.get("/auth/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    client.post("/auth/login",
                data={"username": username, "password": password, "csrf_token": token},
                headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
    return client.get("/api/me").json()["csrf_token"]


def section_d() -> None:
    print("\n-- D. routes: a hidden id 404s exactly like a nonexistent one --")
    with app.session() as s:
        mk_node(s, "d-open", "project")
        mk_node(s, "d-secret", "project")
        mk_node(s, "d-secret-kid", "capability")
        s.flush()
        mk_edge(s, "d-secret", "d-secret-kid")
        s.add(NodeVisibility(node_id="d-secret", visibility="private"))
        s.commit()
        secret_edge_id = s.scalar(
            select(Edge.id).where(Edge.src == "d-secret", Edge.dst == "d-secret-kid"))

    owner, member = _client(), _client()
    owner_csrf = _sign_in(owner, "verify-access-owner", OWNER_PW)
    member_csrf = _sign_in(member, "verify-access-member", MEMBER_PW)

    tree = member.get("/api/graph/tree").json()
    ids = []

    def walk(n):
        ids.append(n["id"])
        for kid in n.get("children") or []:
            walk(kid)
    for r in tree.get("roots") or []:
        walk(r)
    check("/api/graph/tree omits the private root for a member", "d-secret" not in ids)
    check("/api/graph/tree omits its children too", "d-secret-kid" not in ids)
    check("/api/graph/tree still carries the shared root", "d-open" in ids)

    otree = owner.get("/api/graph/tree").json()
    oids = []
    for r in otree.get("roots") or []:
        walk_ids = [r]
        while walk_ids:
            cur = walk_ids.pop()
            oids.append(cur["id"])
            walk_ids.extend(cur.get("children") or [])
    check("the OWNER still sees the private root — two answers, one database",
          "d-secret" in oids)

    # /api/graph/outline claims `True` in COVERAGE. Prove it, rather than
    # trusting that it reuses the same derivation: a route that renders the
    # tree as TEXT would leak a private id as a string, past every check that
    # only ever walks `children` dicts.
    m_outline = member.get("/api/graph/outline?families=all").json()
    o_outline = owner.get("/api/graph/outline?families=all").json()
    check("/api/graph/outline omits the private root for a member",
          "d-secret" not in m_outline["text"])
    check("/api/graph/outline omits its children too",
          "d-secret-kid" not in m_outline["text"])
    check("/api/graph/outline still carries the shared root",
          "d-open" in m_outline["text"])
    check("the OWNER's outline DOES carry the private root — two answers, one database",
          "d-secret" in o_outline["text"])
    # `count` is DISTINCT nodes; a node with two parents renders on two lines,
    # so these are bounds, not an equality. Asserting equality would pass today
    # and break the first time the world grows a diamond — which ADR 0030 says
    # is normal, not exceptional.
    _out_lines = [l for l in m_outline["text"].splitlines() if l.strip()]
    check("outline reports a positive count bounded by the lines it rendered",
          0 < m_outline["count"] <= len(_out_lines),
          f"count={m_outline['count']} lines={len(_out_lines)}")
    r_out_hidden = member.get("/api/graph/outline?root=d-secret&families=all")
    r_out_absent = member.get("/api/graph/outline?root=d-no-such-node-at-all&families=all")
    check("/api/graph/outline root on a hidden node is 404",
          r_out_hidden.status_code == 404, f"got {r_out_hidden.status_code}")
    check("...and an absent id answers identically, so neither confirms the other",
          r_out_hidden.status_code == r_out_absent.status_code
          and r_out_hidden.json().keys() == r_out_absent.json().keys())

    r_hidden = member.get("/api/graph?focus=d-secret&depth=2")
    r_absent = member.get("/api/graph?focus=d-no-such-node-at-all&depth=2")
    check("/api/graph focus on a hidden node is 404", r_hidden.status_code == 404,
          f"got {r_hidden.status_code}")
    check("...and its body is byte-identical in SHAPE to an unknown id's",
          set(r_hidden.json().keys()) == set(r_absent.json().keys()),
          f"{r_hidden.json()} vs {r_absent.json()}")

    # /api/graph/path claims `True` too. Either end hidden must answer exactly
    # as an absent id does; that the walk never passes THROUGH a hidden node is
    # proved by deletion equivalence in `verify_graph_path.py`.
    p_hidden = member.get("/api/graph/path?from=d-open&to=d-secret-kid")
    p_absent = member.get("/api/graph/path?from=d-open&to=d-no-such-node-at-all")
    check("/api/graph/path to a hidden node is 404", p_hidden.status_code == 404,
          f"got {p_hidden.status_code}")
    check("...with the same body shape as an unknown id's",
          p_absent.status_code == 404 and p_hidden.json().keys() == p_absent.json().keys(),
          f"{p_hidden.json()} vs {p_absent.json()}")

    n_hidden = member.get("/api/node/d-secret")
    check("/api/node/{id} 404s on a hidden node", n_hidden.status_code == 404,
          f"got {n_hidden.status_code}")

    doc_hidden = member.get("/api/node/d-secret/doc?path=docs/nodes/x.md")
    check("/api/node/{id}/doc 404s on a hidden node", doc_hidden.status_code == 404,
          f"got {doc_hidden.status_code}")

    # WRITES. A member holding write scope must not be able to touch what they
    # cannot see, and must not learn it exists from the refusal.
    w = member.patch("/api/node/d-secret", json={"name": "renamed", "csrf_token": member_csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("PATCH on a hidden node is refused", w.status_code in (403, 404),
          f"got {w.status_code}")
    check("...and the refusal is a 404, not a 403 that confirms it exists",
          w.status_code == 404, f"got {w.status_code}")

    if secret_edge_id is not None:
        e = member.request("DELETE", f"/api/edge/{secret_edge_id}",
                           json={"csrf_token": member_csrf}, headers={"Origin": TEST_ORIGIN})
        check("DELETE on an edge between hidden nodes 404s (no enumeration)",
              e.status_code == 404, f"got {e.status_code}")

    # And the owner is unimpeded.
    ow = owner.get("/api/node/d-secret")
    check("the owner can still read the private node", ow.status_code == 200,
          f"got {ow.status_code}")
    return owner, member, owner_csrf, member_csrf


# --------------------------------------------------------------------------- #
# E. The admin API
# --------------------------------------------------------------------------- #
def section_e(owner, member, owner_csrf, member_csrf) -> None:
    print("\n-- E. the admin API: owner-only, whole-list grants, shared drops the mark --")
    m = member.get("/api/access")
    check("GET /api/access is 403 for a member", m.status_code == 403, f"got {m.status_code}")

    o = owner.get("/api/access")
    check("GET /api/access is 200 for the owner", o.status_code == 200, f"got {o.status_code}")
    data = o.json() if o.status_code == 200 else {}
    node_ids = [n["id"] for n in data.get("nodes", [])]
    check("the matrix lists the private node", "d-secret" in node_ids)
    check("the matrix lists shared roots too", "d-open" in node_ids)
    check("the matrix lists accounts", len(data.get("accounts", [])) >= 2,
          f"{len(data.get('accounts', []))} accounts")

    # SERVICE ACCOUNTS MUST BE OFFERED. `admin bot add` gives each agent its own
    # `auth_user` row and mints its token against that, so grants resolve to the
    # BOT account and never to a person. The first version of the settings grid
    # filtered `is_service` out of its columns on the opposite assumption, which
    # left an owner no way to let their own agents back into a project they had
    # just marked private. The API must keep handing them over; the grid renders
    # what it is given.
    with app.session() as s:
        if not s.scalar(select(AuthUser).where(AuthUser.subject == "verify-access-bot")):
            s.add(AuthUser(id="u-bot", subject="verify-access-bot", name="Verify Bot",
                           role="member", is_service=True, must_change_password=False,
                           created_at=datetime.now(timezone.utc)))
            s.commit()
    again = owner.get("/api/access").json()
    bots = [a for a in again.get("accounts", []) if a.get("is_service")]
    check("GET /api/access includes service accounts, flagged as such",
          any(a["username"] == "verify-access-bot" for a in bots),
          f"{len(bots)} service account(s) offered")

    # WHO THE AGENT BELONGS TO (ADR 0034 / TASK-1112). The grid groups an agent
    # under its operator, and it does that from these two fields — so the API
    # carrying them is what makes the grouping possible at all. The link grants
    # nothing here; `verify_agent_identity.py` is where the rule itself is
    # proven. This section only proves the admin surface reports it.
    bot_row = next((a for a in bots if a["username"] == "verify-access-bot"), {})
    check("an unlinked agent reports no person, in BOTH fields",
          bot_row.get("person_user_id") is None and bot_row.get("person") is None,
          str(bot_row))
    with app.session() as s:
        s.get(AuthUser, "u-bot").person_user_id = MEMBER_ID
        s.commit()
    linked = owner.get("/api/access").json()
    bot_row = next((a for a in linked.get("accounts", [])
                    if a["username"] == "verify-access-bot"), {})
    check("a linked agent carries the person's id AND their username",
          bot_row.get("person_user_id") == MEMBER_ID
          and bot_row.get("person") == "verify-access-member", str(bot_row))
    check("a PERSON's own row carries no person — the link is an agent's",
          all(a.get("person_user_id") is None
              for a in linked.get("accounts", []) if not a.get("is_service")),
          str([a["username"] for a in linked.get("accounts", [])
               if not a.get("is_service") and a.get("person_user_id")]))

    bad = owner.put("/api/access/node/d-secret",
                    json={"visibility": "nonsense", "csrf_token": owner_csrf},
                    headers={"Origin": TEST_ORIGIN})
    check("an unknown visibility is 400 NAMING its field",
          bad.status_code == 400 and bad.json().get("field") == "visibility",
          f"{bad.status_code} {bad.json()}")

    bad2 = owner.put("/api/access/node/d-secret",
                     json={"grants": ["no-such-user"], "csrf_token": owner_csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("an unknown account id is 400 NAMING its field",
          bad2.status_code == 400 and bad2.json().get("field") == "grants",
          f"{bad2.status_code} {bad2.json()}")

    member_uid = None
    for a in data.get("accounts", []):
        if a["username"] == "verify-access-member":
            member_uid = a["user_id"]
    check("the member's account id is in the matrix", member_uid is not None)

    if member_uid:
        g = owner.put("/api/access/node/d-secret",
                      json={"grants": [member_uid], "csrf_token": owner_csrf},
                      headers={"Origin": TEST_ORIGIN})
        check("granting returns the stored grant list",
              g.status_code == 200 and g.json().get("grants") == [member_uid],
              f"{g.status_code} {g.json()}")

        seen = member.get("/api/node/d-secret")
        check("THE GRANT TAKES EFFECT — the member can now read the node",
              seen.status_code == 200, f"got {seen.status_code}")

        # Whole-list replacement: an empty list revokes.
        r = owner.put("/api/access/node/d-secret",
                      json={"grants": [], "csrf_token": owner_csrf},
                      headers={"Origin": TEST_ORIGIN})
        check("an empty grant list revokes", r.status_code == 200 and r.json().get("grants") == [],
              f"{r.status_code} {r.json()}")
        gone = member.get("/api/node/d-secret")
        check("...and the member loses access again", gone.status_code == 404,
              f"got {gone.status_code}")

    sh = owner.put("/api/access/node/d-secret",
                   json={"visibility": "shared", "csrf_token": owner_csrf},
                   headers={"Origin": TEST_ORIGIN})
    check("setting shared returns shared", sh.status_code == 200
          and sh.json().get("visibility") == "shared", f"{sh.status_code} {sh.json()}")
    with app.session() as s:
        row = s.get(NodeVisibility, "d-secret")
    check("shared DISCARDS the stored mark rather than storing 'shared'", row is None,
          "two representations of one state is how the fast path stops firing")
    back = member.get("/api/node/d-secret")
    check("the member sees it once it is shared", back.status_code == 200,
          f"got {back.status_code}")


# --------------------------------------------------------------------------- #
# F. Route coverage — the section that survives this story
# --------------------------------------------------------------------------- #
#: Every API route, classified. `True` = it reads node data and MUST apply the
#: access filter; `False` = it reads no node data at all, and the reason is
#: stated so the claim can be re-checked rather than trusted.
COVERAGE: dict[tuple[str, str], tuple[bool, str]] = {
    ("GET", "/api/health"): (False, "ingest/schema status only, no node rows served"),
    ("GET", "/api/tree"): (False, "legacy Component table (Atlas v1), not `node`"),
    ("GET", "/api/summary"): (False, "legacy Component table"),
    ("GET", "/api/priority"): (False, "legacy Component table"),
    ("GET", "/api/goals"): (False, "legacy Component table"),
    ("GET", "/api/graph"): (True, "seeds, BFS hops and rollups all filtered"),
    ("GET", "/api/graph/tree"): (True, "node dict filtered, roots derived from it"),
    ("GET", "/api/graph/outline"): (True, "same node dict, same filter and same root "
                                          "derivation as /api/graph/tree — only the "
                                          "RENDERING differs, deliberately, so the two "
                                          "cannot disagree about what the tree is"),
    ("GET", "/api/graph/path"): (True, "the walk is over _visible_node_ids: an edge enters "
                                       "the adjacency only when both ends are visible, and "
                                       "a hidden from/to 404s exactly as an absent one does"),
    ("GET", "/api/node/{node_id}"): (True, "_visible_node_ids inside the query"),
    ("GET", "/api/node/{node_id}/structure"): (True, "_visible_node_ids inside the query, on the focus AND on every linked id"),
    ("GET", "/api/workitems"): (True, "_visible_node_ids inside the query"),
    # STORY-atlas-search-node-docs. `total` is computed AFTER `ids & visible_ids`
    # (same pattern as `/api/workitems`), and the search primitive itself
    # (`_search_node_ids`) only matches a `node_doc` row when it is SERVED
    # (`models_graph.NODE_DOC_SERVED_SQL`) — so neither a hidden NODE nor an
    # unlisted (stored-but-not-served) DOCUMENT can surface via search.
    ("GET", "/api/search"): (True, "_visible_node_ids inside the query, plus the "
                                    "served-document predicate in _search_node_ids"),
    # ADR 0044 §0. Every part filters by the visible set inside its own statement, and the
    # impact walk never passes through a hidden node; verify_context.py proves deletion-equivalence.
    ("GET", "/api/context"): (True, "seeds via /api/search; neighbourhood, impact, intents, "
                                    "decisions and docs each filter by _visible_node_ids in SQL"),
    ("GET", "/api/intents"): (True, "_visible_node_ids inside the query"),
    # TASK-1045. `True`, and on BOTH governing columns: an event names the
    # node(s) whose visibility governs it, and `total` is counted after the
    # filter — a filtered page against an unfiltered total would let a caller
    # count the changes made to a private project, which is information about
    # a private project. Proven by attempting the violation in
    # `verify_graph_event.py` §E, not by asserting the happy path.
    ("GET", "/api/events"): (True, "_visible_node_ids inside the query, on both "
                                   "node_id and related_node_id, before `total`"),
    ("GET", "/api/node/{node_id}/doc"): (True, "_may_see_node"),
    ("GET", "/api/vocabulary"): (False, "type/edge/status constants, no node rows"),
    ("GET", "/api/me"): (False, "the caller's own principal"),
    ("GET", "/api/me/stats"): (True, "_visible_node_ids inside every count, on both "
                                     "node_id and related_node_id; the caller's own person only"),
    ("GET", "/api/access"): (True, "owner-only; the admin surface by definition"),
    # TASK (audit reader). `auth_event` names ACCOUNTS, not nodes — there is no
    # `node_id`/`related_node_id` on the row for `_visible_node_ids` to filter
    # on — so this is classified `False` deliberately, the same way
    # `GET /api/me/agent-credentials` is: it reads no node data at all. The
    # access rule that DOES apply (owner-only) is a different axis from this
    # table's coverage, proven in `verify_auth_events.py` instead.
    ("GET", "/api/auth-events"): (False, "auth_event names accounts, not nodes; "
                                         "owner-only via _require_owner, not node-filtered"),
    # STORY-oss-16. `True`, on the stronger half, the way `GET /api/access` is: the
    # export carries graph_event and audit_read rows, which name node ids, and it
    # is deliberately NOT node-filtered: it is the admin surface by definition
    # (owner session, or an admin-scoped token of a live owner; 403 for everyone
    # else, proven in verify_audit_export.py). A GET, so no WRITE_SCOPE_ROUTES entry.
    ("GET", "/api/audit/export"): (True, "owner-only (or an owner's admin token); the "
                                         "admin surface by definition, like /api/access"),
    # STORY-atlas-config-panel (phase 1). The process environment, through
    # settings_catalog.describe(); no node row is read. Owner-only by the export's
    # rule, proven in verify_config_panel.py. A GET, and the only method there.
    ("GET", "/api/config"): (False, "instance configuration from the process environment; "
                                    "reads no node rows; owner-only like /api/audit/export"),
    # TASK-1119. `True` because of `?for=`: without it this route reads no node
    # data at all (it is a roster), but `?for=<workitem-id>` resolves that node
    # through `_guard_node_access` and then narrows the roster by who can see
    # it — so it BOTH reads a node and lets the answer depend on visibility.
    # Classified on the stronger half deliberately; a route that is sometimes
    # node-reading is a node-reading route.
    ("GET", "/api/people"): (True, "?for= resolves a node via _guard_node_access "
                                   "and narrows the roster by visibility"),
    ("PUT", "/api/access/node/{node_id}"): (True, "owner-only + admin scope"),
    # Project RBAC (STORY-oss-11). Groups name ACCOUNTS, not nodes, so the
    # group routes read no node data (owner-only + session-only + admin scope,
    # proven in verify_project_roles.py). The two project routes DO name nodes:
    # the listing is narrowed by `_visible_node_ids` inside it, and the PUT
    # resolves its project through `_guard_node_access` before anything else.
    ("GET", "/api/access/groups"): (False, "groups and their member account ids; owner-only, no node rows"),
    ("POST", "/api/access/groups"): (False, "creates a group; owner-only + session + admin scope"),
    ("PUT", "/api/access/groups/{group_id}/members"): (False, "replaces account ids in a group; "
                                                              "owner-only + session + admin scope"),
    ("DELETE", "/api/access/groups/{group_id}"): (False, "deletes a group, its memberships and "
                                                         "bindings; owner-only + session + admin"),
    ("GET", "/api/access/projects"): (True, "project ids intersected with _visible_node_ids, then "
                                            "narrowed to those the caller may administer"),
    ("PUT", "/api/access/project/{project_id}/bindings"): (True, "_guard_node_access on the project, "
                                                                 "then the grantable-role check"),
    # GitHub linking/repo access. None reads node data (they read github_link
    # rows and call GitHub). Access rules, proven in verify_github_link.py:
    # /api/me/github* are browser-session-only; /api/access/github/* are
    # owner-only + session CSRF + admin scope (the PUT above, copied).
    ("GET", "/api/me/github"): (False, "the caller's own link state; session-only"),
    ("GET", "/api/me/github/start"): (False, "redirect to GitHub; session-only, changes nothing"),
    ("GET", "/api/me/github/callback"): (False, "links the caller's own GitHub id; "
                                                "session-only, state bound to the session"),
    ("POST", "/api/me/github/unlink"): (False, "the caller's own link; session-only + CSRF"),
    ("POST", "/api/me/password"): (False, "the caller's own password, after an IdP step-up; "
                                          "session-only + CSRF; reads no node data"),
    ("PUT", "/api/access/github/{user_id}"): (False, "owner-only + session CSRF + admin scope; "
                                                     "reads no node data"),
    ("POST", "/api/access/github/{user_id}/refresh"): (False, "owner-only + session CSRF + admin scope; "
                                                              "reads no node data"),
    ("POST", "/api/me/agent-credential"): (
        False, "creates an account and a token; reads no node data at all"),
    ("GET", "/api/me/agent-credentials"): (
        False, "the caller's own credentials; no node rows"),
    ("POST", "/api/me/agent/{agent}/credential"): (
        False, "rotation; reads the caller's own agent, no node data"),
    ("POST", "/api/me/agent-credential/{token_id}/revoke"): (
        False, "revokes the caller's own credential; no node data"),
    ("POST", "/api/node"): (False, "creates a new id; nothing existing is read out"),
    ("PATCH", "/api/node/{node_id}"): (True, "_guard_node_access"),
    ("DELETE", "/api/node/{node_id}"): (True, "_guard_node_access"),
    ("POST", "/api/node/{node_id}/status"): (True, "_guard_node_access"),
    ("POST", "/api/edge"): (True, "both endpoints resolved through _visible_node_ids"),
    ("DELETE", "/api/edge/{edge_id}"): (True, "endpoints checked before the delete"),
    ("POST", "/api/workitem"): (True, "parent/binding/depends_on resolved through the filter"),
    ("PATCH", "/api/workitem/{workitem_id}"): (True, "_guard_node_access + _resolve"),
    # STORY-atlas-acceptance-human-approved: every proposal route resolves the
    # ITEM through _guard_node_access before reading a proposal, so a proposal
    # on a hidden item is the item's 404 (verify_acceptance_approval.py §E).
    ("GET", "/api/workitem/{workitem_id}/acceptance-proposal"): (True, "_guard_node_access on the item"),
    ("POST", "/api/workitem/{workitem_id}/acceptance-proposal"): (True, "_guard_node_access on the item"),
    ("POST", "/api/workitem/{workitem_id}/acceptance-proposal/{proposal_id}/approve"): (
        True, "_guard_node_access on the item; session-only"),
    ("POST", "/api/workitem/{workitem_id}/acceptance-proposal/{proposal_id}/reject"): (
        True, "_guard_node_access on the item; session-only"),
    ("PUT", "/api/node/{node_id}/doc"): (True, "_guard_node_access"),
    ("DELETE", "/api/node/{node_id}/doc"): (True, "_guard_node_access"),
    ("POST", "/api/node/{node_id}/intent"): (True, "_guard_node_access"),
    ("PATCH", "/api/intent/{intent_id}"): (True, "_may_see_node on the owning node"),
    ("DELETE", "/api/intent/{intent_id}"): (True, "_may_see_node on the owning node"),
    ("GET", "/api/workitem/{workitem_id}/comments"): (True, "_guard_node_access on the item"),
    ("POST", "/api/workitem/{workitem_id}/comments"): (True, "_guard_node_access on the item"),
    ("PATCH", "/api/comment/{comment_id}"): (True, "_may_see_node on the owning item"),
    ("DELETE", "/api/comment/{comment_id}"): (True, "_may_see_node on the owning item"),
    # ADR 0045. An ADR is governed by its PROJECT node; verify_adr_records.py §D
    # proves a hidden project's ADR is absent from list, get, a 409 and search.
    ("GET", "/api/adrs"): (True, "project_node_id IN _visible_node_ids inside the query, "
                                 "before `total`"),
    ("GET", "/api/adrs/{ref}"): (True, "same visible-project clause; hidden and absent are one 404, "
                                       "and a 409 lists only visible candidates"),
    ("PUT", "/api/adrs/{adr_id}"): (True, "_guard_node_access on the project"),
    # STORY-atlas-setup-guide-adrs. Owner-only by the export's rule (routers.audit._may_export),
    # proven in verify_setup_guide.py; the ADR writes go through put_adr, whose
    # _guard_node_access on the project is the entry above.
    ("GET", "/api/setup"): (True, "owner-only (or an owner's admin token); the install's "
                                  "governing answers and their ADR ids, like /api/audit/export"),
    ("POST", "/api/setup/answers"): (True, "owner-only; the ADR is written by put_adr, with its "
                                           "_guard_node_access on the project"),
    ("POST", "/api/setup/adrs/{adr_id}/accept"): (True, "owner SESSION only; the ADR is read "
                                                        "joined to its node and written by put_adr"),
    # STORY-atlas-crunch-mode. crunch.py's own tables plus the guardrail matrix document on
    # atlas-system; no node the caller names is read. Every one refuses every token; proven in
    # verify_crunch.py.
    ("GET", "/api/crunch"): (False, "crunch_state/crunch_history and the matrix's env names; "
                                    "owner SESSION only"),
    ("PUT", "/api/crunch"): (False, "writes crunch_state, an event and an ADR on atlas-system; "
                                    "owner session + password + admin scope belt"),
    ("POST", "/api/crunch/end"): (False, "same as PUT /api/crunch"),
    ("GET", "/api/crunch/banner"): (False, "{active, ends_at} only; any browser session, no token"),
}

# The retrieval add-on (ADR 0044) mounts these only with ATLAS_RETRIEVAL=on, so they join the
# table only then: the default run keeps its stale-entry check, and verify_retrieval.py §F runs
# this whole script with the add-on on, where an unclassified retrieval route fails here.
if app._RETRIEVAL_ON:
    COVERAGE.update({
        ("GET", "/api/retrieval/status"): (False, "owner-only (403 otherwise); counts over the "
                                                  "whole index, no node row or id served"),
        ("POST", "/api/retrieval/reindex"): (False, "owner-only + admin scope; changes derived "
                                                    "rows only, serves no node data"),
    })

# The configuration write route (STORY-atlas-config-panel phase 2) exists only with
# ATLAS_CONFIG_WRITES on; verify_config_writes.py runs this script with it on.
if app._CONFIG_WRITES_ON:
    COVERAGE[("PUT", "/api/config/{name}")] = (
        False, "changes one setting in config_value; owner session + password only; reads no "
               "node rows (its graph_event names a part node)")


#: TASK-1121 — COVERAGE one level down: every QUERY PARAMETER of every route
#: COVERAGE classifies `True`, classified in the same fail-closed way.
#:
#: WHY THE ROUTE-LEVEL REGISTRY WAS NOT ENOUGH. `GET /api/workitems` has been
#: `(True, "_visible_node_ids inside the query")` since this section was
#: written, and that entry stayed true and stayed green while the route grew
#: `?assignee=` (TASK-1118) — a parameter that selects rows by
#: `node.assignee_user_id` and composes onto the SAME clause list the access
#: filter composes onto. Nothing here noticed it arrive, and nothing here would
#: notice the next one. A route is not a unit of access review once its
#: parameters are what decide which rows come back: the honest unit is
#: `(method, path, parameter)`, and this registry is that unit, enumerated
#: LIVE off `route.dependant.query_params` so it cannot drift from the
#: signatures the way a hand-written list would.
#:
#: THE CLASSIFICATION. `True` = the parameter lets the CALLER NAME OR MATCH
#: graph data — a node id, a node column, an edge type, prose — so it is a
#: selection predicate and the access filter must BOUND it (composed into the
#: same query, never applied to an unfiltered set and never as a post-filter).
#: `False` = it cannot name or match anything in the graph; it only bounds the
#: SIZE or SHAPE of an already-decided set (paging, hop budgets, prose
#: inclusion), so no ordering mistake with the access filter is available to it.
#:
#: Scoped to the `True` routes deliberately. A parameter on a route that reads
#: no node data at all (`?event=` on `/api/auth-events`) has no access filter to
#: be ordered against, and pulling those in would pad the registry with entries
#: whose reason is always the same sentence — which is how a registry stops
#: being read.
PARAM_COVERAGE: dict[tuple[str, str, str], tuple[bool, str]] = {
    # -- GET /api/node/{node_id}/structure ----------------------------------- #
    ("GET", "/api/node/{node_id}/structure", "children_limit"): (False, "a result-size cap on the children list; names nothing in the graph"),
    # -- GET /api/graph ---------------------------------------------------- #
    ("GET", "/api/graph", "focus"): (True, "names a node id — the BFS seed, resolved "
                                            "through the filter before any hop is taken"),
    ("GET", "/api/graph", "edges"): (True, "matches edge_type, so it decides which nodes "
                                            "are REACHED; hops are taken over the filtered set"),
    ("GET", "/api/graph", "families"): (True, "matches node_type.family — a predicate over "
                                               "node rows"),
    ("GET", "/api/graph", "depth"): (False, "a hop budget; names nothing in the graph"),
    ("GET", "/api/graph", "cap"): (False, "a result-size cap; names nothing in the graph"),
    ("GET", "/api/graph", "include_prose"): (False, "which FIELDS are emitted, not which rows"),
    ("GET", "/api/graph", "prose"): (False, "a payload TIER — drops summary/abstract/detail "
                                            "from rows already selected; never changes which"),
    ("GET", "/api/graph", "work"): (False, "a payload TIER — drops work fields, or trims "
                                           "work_load's per-item list, from rows already "
                                           "selected; the rollup itself is computed first"),
    ("GET", "/api/graph", "meta"): (False, "a payload TIER — drops owner/source/attributes/"
                                           "assignee/completed_at; fields, not rows"),
    ("GET", "/api/graph", "strict"): (False, "turns a reached cap into a 413; decides WHETHER "
                                             "the visible set is returned, never which rows"),
    ("GET", "/api/graph", "max_bytes"): (False, "a response-size ceiling over the visible set; "
                                                "names nothing in the graph"),
    ("GET", "/api/graph", "unbound_work"): (True, "adds SEEDS — the workitem roots the families "
                                                  "walk missed — so it decides which rows come "
                                                  "back; every seed goes through the same "
                                                  "_may_see, every hop through the same walk "
                                                  "(TASK-1472, verify_unbound_work.py)"),
    # -- GET /api/graph/tree ------------------------------------------------ #
    ("GET", "/api/graph/tree", "include_prose"): (False, "which fields are emitted"),
    ("GET", "/api/graph/tree", "prose"): (False, "same payload tier; fields, not rows"),
    # -- GET /api/graph/outline --------------------------------------------- #
    ("GET", "/api/graph/outline", "root"): (True, "names a node id — resolved through the "
                                                   "filter, and a hidden one 404s exactly as "
                                                   "an absent one does"),
    ("GET", "/api/graph/outline", "families"): (True, "matches node_type.family — a predicate "
                                                       "over node rows, and the one that keeps "
                                                       "the walk out of the backlog"),
    ("GET", "/api/graph/outline", "max_depth"): (False, "a depth budget; names nothing"),
    ("GET", "/api/graph/outline", "names"): (False, "appends the name to each line — a field, "
                                                     "not a row"),
    # -- GET /api/graph/path ------------------------------------------------ #
    ("GET", "/api/graph/path", "from"): (True, "names a node id — resolved against the visible "
                                               "set; hidden and absent are the same 404"),
    ("GET", "/api/graph/path", "to"): (True, "names a node id — same resolution and same 404 "
                                             "as `from`"),
    ("GET", "/api/graph/path", "edges"): (True, "matches edge_type, so it decides which routes "
                                                "exist; applied to edges already bounded by the "
                                                "visible set"),
    ("GET", "/api/graph/path", "max"): (False, "how many paths; names nothing in the graph"),
    ("GET", "/api/graph/path", "hops"): (False, "a hop budget; names nothing in the graph"),
    # -- GET /api/workitems ------------------------------------------------- #
    ("GET", "/api/workitems", "type"): (True, "matches node.type"),
    ("GET", "/api/workitems", "status"): (True, "matches the EFFECTIVE status — the one "
                                                 "filter applied in Python, and deliberately "
                                                 "over the candidate set visibility has "
                                                 "already bounded, never over the table"),
    ("GET", "/api/workitems", "parent"): (True, "names a node id, resolved via a `contains` "
                                                 "edge scan"),
    ("GET", "/api/workitems", "binding"): (True, "names a node id, resolved via works_on/serves"),
    # TASK-1120 retired `owner` on the WRITE path (both /api/workitem routes now
    # 400 on the key). It survives HERE, as a read filter, because the column
    # survives on object-family nodes and historical workitem values were not
    # dropped — so this entry is not stale, it is the other half of that
    # decision. If the column is ever dropped, this parameter goes with it and
    # the stale-entry check below is what says so.
    ("GET", "/api/workitems", "owner"): (True, "exact match on node.owner — a read filter "
                                                "over a column the write path retired"),
    # THE ENTRY THIS TASK EXISTS FOR (TASK-1121). `?assignee=` selects rows by
    # `node.assignee_user_id`, and `me` resolves against the CALLING PRINCIPAL —
    # so it is a predicate whose value the caller does not even have to know.
    # It is `True` for the ordinary reason (it matches a node column) and it
    # carries one extra hazard worth naming here rather than only in app.py:
    # ASSIGNMENT IS NOT A GRANT. The assignee reference never enters
    # `_visible_node_ids`, so a workitem bound to a private project and assigned
    # to an account with no grant on it must stay absent from that account's OWN
    # `?assignee=me` — the one case where "it's mine" reads like a reason to
    # show it and is not. `verify_assignment.py` proves that behaviour by
    # breaking it; this entry is what makes the parameter's absence from the
    # access review impossible rather than merely unlikely.
    # THE ENTRY THIS TASK EXISTS FOR (TASK-1121). `?assignee=` selects rows by
    # `node.assignee_user_id`, and `me` resolves against the CALLING PRINCIPAL —
    # so it is a predicate whose value the caller does not even have to know.
    # It is `True` for the ordinary reason (it matches a node column) and it
    # carries one extra hazard worth naming here rather than only in app.py:
    # ASSIGNMENT IS NOT A GRANT. The assignee reference never enters
    # `_visible_node_ids`, so a workitem bound to a private project and assigned
    # to an account with no grant on it must stay absent from that account's OWN
    # `?assignee=me` — the one case where "it's mine" reads like a reason to
    # show it and is not. `verify_assignment.py` proves that behaviour by
    # breaking it; this entry is what makes the parameter's absence from the
    # access review impossible rather than merely unlikely.
    ("GET", "/api/workitems", "assignee"): (
        True, "matches node.assignee_user_id (`me` resolves against the principal, `none` "
              "is IS NULL); composed onto the same clause list as _visible_node_ids, and "
              "assignment is NOT a grant — see verify_assignment.py"),
    ("GET", "/api/workitems", "q"): (True, "full-text match over node.body, "
                                            "acceptance_criterion.text and workitem_result.text"),
    ("GET", "/api/workitems", "include_ancestors"): (
        True, "expands the response with `contains` ANCESTOR rows — node data that is not "
              "the listed row, resolved through the same visible_ids so an ancestor the "
              "caller cannot see never lands as a header"),
    # STORY-oss-14. Both select rows by a node column and compose onto the same
    # clause list as _visible_node_ids, like `owner` and `assignee` above.
    ("GET", "/api/workitems", "priority"): (True, "matches node.priority (`none` is IS NULL); "
                                                   "composed onto the same clause list as "
                                                   "_visible_node_ids"),
    ("GET", "/api/workitems", "label"): (True, "matches node.labels (array overlap); composed "
                                                "onto the same clause list as _visible_node_ids"),
    ("GET", "/api/workitems", "include_prose"): (False, "which fields are emitted"),
    ("GET", "/api/workitems", "page"): (False, "paging over an already-filtered set; `total` "
                                                "is counted AFTER the filter"),
    ("GET", "/api/workitems", "page_size"): (False, "paging; clamped to the route's max"),
    # -- GET /api/search ---------------------------------------------------- #
    ("GET", "/api/search", "q"): (True, "full-text match, plus the served-document predicate "
                                         "in _search_node_ids"),
    ("GET", "/api/search", "families"): (True, "matches node_type.family — a predicate over "
                                               "node rows, applied after the access filter "
                                               "and before `total` is counted"),
    ("GET", "/api/search", "page"): (False, "paging; `total` is counted after the filter"),
    ("GET", "/api/search", "page_size"): (False, "paging"),
    # ADR 0044 §5. `mode` selects the vector list, whose statement applies _visible_node_ids and
    # the served-document check BEFORE its ORDER BY … LIMIT (retrieval/query.py); `expand` adds
    # neighbours filtered by the same visible set. verify_retrieval.py proves deletion-equivalence.
    ("GET", "/api/search", "mode"): (True, "semantic/hybrid add a vector list filtered by "
                                           "_visible_node_ids inside its statement, before LIMIT"),
    ("GET", "/api/search", "expand"): (True, "one hop of graph neighbours, kept only if in the "
                                             "caller's visible set, before `total`"),
    # -- GET /api/context (ADR 0044 §0) ------------------------------------- #
    ("GET", "/api/context", "about"): (True, "the search text for the seeds; /api/search's filter"),
    ("GET", "/api/context", "mode"): (True, "the seeds' search mode; /api/search's filter"),
    ("GET", "/api/context", "families"): (True, "node_type.family of seeds and neighbours, "
                                                "applied with the visible set"),
    ("GET", "/api/context", "seeds"): (False, "a budget: how many seeds"),
    ("GET", "/api/context", "neighbours"): (False, "a budget"),
    ("GET", "/api/context", "impact_depth"): (True, "how far the impact walk goes; every step "
                                                    "filters by the visible set"),
    ("GET", "/api/context", "impact"): (False, "a budget"),
    ("GET", "/api/context", "intents"): (False, "a budget"),
    ("GET", "/api/context", "intents_full"): (True, "adds intent statements; _intents_for drops "
                                                    "those owned by a hidden node"),
    ("GET", "/api/context", "decisions"): (False, "a budget"),
    ("GET", "/api/context", "docs"): (False, "a budget"),
    # -- GET /api/intents --------------------------------------------------- #
    ("GET", "/api/intents", "node_id"): (True, "names a node id"),
    ("GET", "/api/intents", "kind"): (False, "matches node_intent.kind; the NODE set the rows "
                                              "hang off is bounded before it"),
    # -- GET /api/audit/export (STORY-oss-16) ------------------------------- #
    ("GET", "/api/audit/export", "since"): (False, "a timestamp bound"),
    ("GET", "/api/audit/export", "until"): (False, "a timestamp bound"),
    ("GET", "/api/audit/export", "kinds"): (False, "which audit TABLES; names no node"),
    # -- GET /api/events ---------------------------------------------------- #
    ("GET", "/api/events", "node_id"): (True, "names a node id — one of the two governing "
                                               "columns the filter is applied to"),
    ("GET", "/api/events", "entity_id"): (True, "names the node or edge the event is about"),
    ("GET", "/api/events", "entity_type"): (False, "'node' or 'edge'; names no row"),
    ("GET", "/api/events", "action"): (False, "matches graph_event.action"),
    ("GET", "/api/events", "actor"): (False, "names an ACCOUNT, not a node"),
    ("GET", "/api/events", "actor_kind"): (False, "'session' or 'token'; names no row"),
    ("GET", "/api/events", "responsible"): (False, "names an ACCOUNT (the person behind an "
                                                    "agent), not a node"),
    ("GET", "/api/events", "since"): (False, "a timestamp bound"),
    ("GET", "/api/events", "until"): (False, "a timestamp bound"),
    ("GET", "/api/events", "page"): (False, "paging; `total` is counted after the filter"),
    ("GET", "/api/events", "page_size"): (False, "paging"),
    # -- the doc routes ----------------------------------------------------- #
    ("GET", "/api/node/{node_id}/doc", "path"): (
        True, "names a document ON the node — the node is gated by _may_see_node first, and "
              "an unlisted (stored-but-not-served) document is its own refusal"),
    ("DELETE", "/api/node/{node_id}/doc", "path"): (
        True, "names the document to remove; same two gates as the read"),
    # -- the ADR registry (ADR 0045) ------------------------------------------ #
    ("GET", "/api/adrs", "project"): (True, "names a project node id, composed onto the visible-"
                                            "project clause"),
    ("GET", "/api/adrs", "q"): (True, "full-text over an ADR's id, title and body, plus an exact "
                                      "id or legacy-number match"),
    ("GET", "/api/adrs", "status"): (True, "matches adr_record.status, after the access clause"),
    ("GET", "/api/adrs", "number"): (True, "matches adr_record.legacy_number, after the access clause"),
    ("GET", "/api/adrs", "page"): (False, "paging; `total` is counted after the filter"),
    ("GET", "/api/adrs", "page_size"): (False, "paging"),
    ("GET", "/api/adrs/{ref}", "project"): (True, "names a project node id to disambiguate a "
                                                  "legacy number; composed onto the access clause"),
    # -- GET /api/setup (STORY-atlas-setup-guide-adrs) ----------------------- #
    ("GET", "/api/setup", "topic"): (False, "a question-set topic id to ask in full; names no node"),
    # -- GET /api/people ---------------------------------------------------- #
    ("GET", "/api/people", "for"): (True, "names a workitem id, resolved through "
                                           "_guard_node_access before the roster is narrowed"),
    # -- GET /api/node/{id} -------------------------------------------------- #
    ("GET", "/api/node/{node_id}", "prose"): (
        False, "a payload TIER, the same ladder /api/graph uses — drops summary/abstract/"
               "detail/body from a node already selected through the filter"),
    ("GET", "/api/node/{node_id}", "intents"): (
        False, "a payload TIER — drops the inherited half, or the key, from a node already "
               "selected. The intents themselves are gated separately: _intents_for takes the "
               "visible set, so an ancestor the caller cannot see never contributes one"),
    # -- DELETE /api/node/{id} ---------------------------------------------- #
    ("DELETE", "/api/node/{node_id}", "cascade"): (
        False, "a confirmation flag widening the delete to the node's own edges; the node "
               "itself is gated by _guard_node_access and this names nothing new"),
}


def _live_query_params(route) -> set[str]:
    """The query parameters FastAPI actually resolves for one route, read off
    the live `dependant` rather than off the handler's signature.

    Derived, not pinned, and deliberately from the same object the route table
    is enumerated from: a parameter added to a handler shows up here on the
    next run with no second place to remember. Body models (`body: NodePatch`)
    and `Request` are not query params and never appear; an aliased one
    (`For: str | None = Query(None, alias="for")`) appears under the alias,
    which is the name a caller actually sends.
    """
    dep = getattr(route, "dependant", None)
    if dep is None:
        return set()
    return {p.alias or p.name for p in dep.query_params}


def section_g(owner, member, owner_csrf, member_csrf) -> None:
    """Access administration leaves an operator-readable trace, and that trace
    does NOT become a disclosure.

    Written by attempting the violation rather than asserting the happy path:
    the interesting claim is not "a row was written", it is "the row is NOT in
    `graph_event`" — because `GET /api/events` serves an event to everyone who
    can still see the node it names, so a grant recorded there would tell every
    other grantee who else was granted.
    """
    print("\n-- G. access changes are logged to auth_event, and NOT to graph_event --")

    member_uid = None
    matrix = owner.get("/api/access")
    if matrix.status_code == 200:
        for a in matrix.json().get("accounts", []):
            if a["username"] == "verify-access-member":
                member_uid = a["user_id"]
    if member_uid is None:
        check("the member's account id is resolvable", False, "cannot run section G")
        return

    def auth_events(kind: str) -> list:
        with app.session() as s:
            return list(s.scalars(
                select(AuthEvent).where(
                    AuthEvent.event == kind).order_by(
                        AuthEvent.id)).all())

    before_added = len(auth_events("node_grant_added"))
    before_removed = len(auth_events("node_grant_removed"))
    before_vis = len(auth_events("node_visibility_changed"))
    with app.session() as s:
        before_graph = s.scalar(select(func.count()).select_from(GraphEvent))

    # private -> grant -> revoke -> shared, all through the real route.
    owner.put("/api/access/node/d-secret",
              json={"visibility": "private", "csrf_token": owner_csrf},
              headers={"Origin": TEST_ORIGIN})
    owner.put("/api/access/node/d-secret",
              json={"grants": [member_uid], "csrf_token": owner_csrf},
              headers={"Origin": TEST_ORIGIN})
    owner.put("/api/access/node/d-secret",
              json={"grants": [], "csrf_token": owner_csrf},
              headers={"Origin": TEST_ORIGIN})

    added, removed = auth_events("node_grant_added"), auth_events("node_grant_removed")
    check("granting writes ONE node_grant_added", len(added) == before_added + 1,
          f"{before_added} -> {len(added)}")
    check("revoking writes ONE node_grant_removed", len(removed) == before_removed + 1,
          f"{before_removed} -> {len(removed)}")
    if len(added) > before_added:
        row = added[-1]
        check("...naming the account whose access moved, not a list in a string",
              row.user_id == member_uid, f"user_id={row.user_id!r}")
        check("...and the node it was granted on",
              "d-secret" in (row.detail or ""), f"detail={row.detail!r}")
        check("...and WHO did it", bool(row.actor), f"actor={row.actor!r}")

    # A no-op write must not manufacture history.
    quiet_before = len(auth_events("node_grant_added"))
    owner.put("/api/access/node/d-secret",
              json={"grants": [], "csrf_token": owner_csrf},
              headers={"Origin": TEST_ORIGIN})
    check("re-sending an unchanged grant list writes NOTHING",
          len(auth_events("node_grant_added")) == quiet_before,
          "an audit log that records non-events teaches people to ignore it")

    vis = auth_events("node_visibility_changed")
    check("a visibility flip is recorded", len(vis) > before_vis,
          f"{before_vis} -> {len(vis)}")

    # THE POINT OF THE WHOLE SECTION.
    with app.session() as s:
        after_graph = s.scalar(select(func.count()).select_from(GraphEvent))
    check("NO graph_event row was written for any of it",
          after_graph == before_graph,
          f"graph_event moved {before_graph} -> {after_graph}; an access change "
          f"recorded there is readable by every other grantee of that node")

    ev = owner.get("/api/events?node_id=d-secret")
    if ev.status_code == 200:
        actions = {i.get("action") for i in ev.json().get("items", [])}
        check("...and /api/events exposes no access action",
              not any(a and ("grant" in a or "visibility" in a) for a in actions),
              f"actions={sorted(a for a in actions if a)}")

    # The growth signal for the table nobody prunes.
    h = owner.get("/api/health")
    check("/api/health reports event_count",
          h.status_code == 200 and isinstance(h.json().get("event_count"), int),
          f"{h.status_code} {h.json() if h.status_code == 200 else ''}")
    if h.status_code == 200:
        check("...and it is the real graph_event count, not a cached number",
              h.json().get("event_count") == after_graph,
              f"health={h.json().get('event_count')} db={after_graph}")


def section_f() -> None:
    print("\n-- F. route coverage: every /api route is classified --")
    live: set[tuple[str, str]] = set()
    #: (method, path) -> the query parameters that route actually accepts.
    params_of: dict[tuple[str, str], set[str]] = {}
    from route_table import live_routes  # TASK-1361: app.routes is no longer flat
    for route in live_routes(app.app):
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        qp = _live_query_params(route)
        for method in (getattr(route, "methods", None) or set()):
            if method in ("HEAD", "OPTIONS"):
                continue
            live.add((method, path))
            params_of[(method, path)] = qp

    # /assets/* is not in COVERAGE (it is not an /api route), but it IS behind
    # the gate, and on 2026-08-18 the public host served a Cloudflare-cached
    # `board.css` to a caller with no session at all — 200 and 66 KB, while the
    # LAN host correctly returned 401. The origin sent no Cache-Control, so the
    # CDN applied a 4-hour default and stored an authenticated response.
    #
    # `private` is what forbids a SHARED cache from storing it. Asserted here
    # rather than trusted, because the symptom is invisible from the origin:
    # everything looks correct on the host you test against.
    import app as _app
    check("asset responses forbid shared caches (private)",
          "private" in _app.ASSET_CACHE_CONTROL,
          _app.ASSET_CACHE_CONTROL)
    check("...and force revalidation, since /assets URLs carry no cache-busting",
          "no-cache" in _app.ASSET_CACHE_CONTROL or "no-store" in _app.ASSET_CACHE_CONTROL,
          _app.ASSET_CACHE_CONTROL)
    _probe = _app._asset_response("board.css")
    check("...and the header is actually ON the response, not just the constant",
          _probe.headers.get("cache-control") == _app.ASSET_CACHE_CONTROL,
          str(_probe.headers.get("cache-control")))

    # /docs (STORY-atlas-system-node-handbook, ADR 0047) is not an /api route either, and it
    # may be PUBLIC (ATLAS_PUBLIC_DOCS=1), so its classification is stated here: it reads NO
    # node data. It serves generated files from the image, never `node_doc`, so a private
    # project's documents cannot reach it whatever the gate says. Proved on the module itself:
    # no database, model or deps-access import, and only the two GET routes.
    import ast as _ast
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "routers", "handbook.py"),
              encoding="utf-8") as _f:
        _src = _f.read()
    _imported = {(n.module or "") if isinstance(n, _ast.ImportFrom) else a.name
                 for n in _ast.walk(_ast.parse(_src)) if isinstance(n, (_ast.Import, _ast.ImportFrom))
                 for a in n.names}
    check("/docs reads no node data: routers/handbook.py imports no db, model, deps access or session",
          not (_imported & {"db", "models_graph", "models_auth", "sqlalchemy", "sqlalchemy.orm"})
          and "_may_see_node" not in _src and "session(" not in _src, str(sorted(_imported)))
    _docs_routes = sorted((m, getattr(r, "path", "")) for r in live_routes(app.app)
                          if getattr(r, "path", "").startswith("/docs")
                          for m in (getattr(r, "methods", None) or set()) if m != "HEAD")
    check("...and serves GET only", _docs_routes == [("GET", "/docs"), ("GET", "/docs/{name:path}")],
          str(_docs_routes))

    unclassified = sorted(live - set(COVERAGE))
    check("every live /api route has a COVERAGE entry", not unclassified,
          "UNCLASSIFIED: " + ", ".join(f"{m} {p}" for m, p in unclassified)
          if unclassified else f"{len(live)} routes")

    stale = sorted(set(COVERAGE) - live)
    check("no COVERAGE entry names a route that no longer exists", not stale,
          "STALE: " + ", ".join(f"{m} {p}" for m, p in stale) if stale else "")

    filtered = sorted(k for k, v in COVERAGE.items() if v[0] and k in live)
    print(f"       {len(filtered)} of {len(live)} live /api routes apply the access filter")
    for m, p in sorted(live):
        applies, why = COVERAGE.get((m, p), (None, "UNCLASSIFIED"))
        mark = "filter" if applies else ("n/a   " if applies is False else "??????")
        print(f"       [{mark}] {m:<6} {p} — {why}")

    # -- F2. PARAMETER coverage (TASK-1121) -------------------------------- #
    #
    # The same fail-closed rule, one level down. `?assignee=` arrived on
    # `/api/workitems` in TASK-1118 and this section stayed green throughout,
    # because the ROUTE was already classified and a route-level registry
    # cannot see a new predicate appear inside a route it already trusts.
    #
    # Enumerated over the `True` routes only — see PARAM_COVERAGE's own note on
    # why a parameter on a non-node-reading route has nothing to be ordered
    # against.
    print("\n-- F2. parameter coverage: every query param of an access-filtered route --")
    node_reading = sorted(k for k, v in COVERAGE.items() if v[0] and k in live)
    live_params: set[tuple[str, str, str]] = {
        (m, p, name) for (m, p) in node_reading for name in params_of.get((m, p), set())
    }

    unclassified_params = sorted(live_params - set(PARAM_COVERAGE))
    check("every query param on an access-filtered route has a PARAM_COVERAGE entry",
          not unclassified_params,
          "UNCLASSIFIED: " + ", ".join(f"{m} {p}?{n}=" for m, p, n in unclassified_params)
          if unclassified_params else f"{len(live_params)} params over "
                                      f"{len(node_reading)} node-reading routes")

    stale_params = sorted(set(PARAM_COVERAGE) - live_params)
    check("no PARAM_COVERAGE entry names a param that no longer exists",
          not stale_params,
          "STALE: " + ", ".join(f"{m} {p}?{n}=" for m, p, n in stale_params)
          if stale_params else "")

    selecting = sorted(k for k, v in PARAM_COVERAGE.items() if v[0] and k in live_params)
    print(f"       {len(selecting)} of {len(live_params)} params select node rows and must "
          f"compose onto the access filter")
    for m, p, n in sorted(live_params):
        applies, why = PARAM_COVERAGE.get((m, p, n), (None, "UNCLASSIFIED"))
        mark = "select" if applies else ("shape " if applies is False else "??????")
        print(f"       [{mark}] {m:<6} {p}?{n}= — {why}")


if __name__ == "__main__":
    # `startup()` is an @app.on_event handler, so importing app does NOT create
    # the tables — and every section below touches them directly rather than
    # only over HTTP. Call it once, explicitly, the way the other verify scripts
    # get it for free by hitting the app first.
    app.startup()

    mk_user(OWNER_ID, "verify-access-owner", "owner", OWNER_PW)
    mk_user(MEMBER_ID, "verify-access-member", "member", MEMBER_PW)
    mk_user(GUEST_ID, "verify-access-guest", "member", MEMBER_PW)

    section_a()
    section_c()
    clients = section_d()
    section_e(*clients)
    section_g(*clients)
    section_f()

    print()
    if failures:
        print(f"FAILED — {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")
