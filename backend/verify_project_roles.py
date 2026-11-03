"""
STORY-oss-11 / ADR 0041 §2-§4: the four project roles, the delete rule, agents
capped by scope, the access API, and `atlas-admin rbac plan`.

Configuration: `ATLAS_RBAC=projects`, `ATLAS_DEFAULT_PROJECT_VISIBILITY=private`,
`ATLAS_SHARED_PROJECT_ROLE=viewer`, implicit instance owner (the default).

  A. ROLES over HTTP. viewer reads and gets 403 on every write; editor
     creates and changes and gets 403 on every delete; maintainer deletes in
     its project and gets 403 deleting the project root; a project owner deletes
     the root. An unknown stored role is NO role (404). A `node_grant` on a
     project root counts as `editor`.
  B. GROUPS AND THE CROSS-PROJECT RULE. A maintainer of A through a GROUP
     deletes in A and gets 403 deleting in B, where it is editor; an edge from A
     to B needs maintainer on BOTH ends.
  C. AGENTS (§4). An admin-token agent of an editor cannot delete; of a
     maintainer it can; a read token of a maintainer cannot write; the cap is
     read=viewer, write=editor, admin=maintainer, measured on the resolver; an
     instance owner's agent inherits nothing from the owner's ROLE; an unlinked
     agent's own direct binding works.
  D. THE ACCESS API (board criterion 5). An owner session creates a group, adds
     a member and binds the group `editor` on a project, and that member can
     then read and write there; deleting the group takes it away. The 400s name
     their field; a member, and any token, gets 403; a project maintainer may
     grant up to maintainer and not owner, nor remove an owner; each change is
     an `auth_event`.
  E. ADOPTION. `admin.rbac_plan_rows` (what `atlas-admin rbac plan` prints)
     reports today's vs RBAC-on permission and the expected differences, and
     writes nothing; switching RBAC off in process restores today's rule (a
     member's delete succeeds again).
  F. THE INSTANCE ADMIN BINDS WITHOUT SEEING (TASK-1367, ADR 0041 §8). With the
     work install's `ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER=false`, an owner
     session lists a private project it holds no role on in
     `GET /api/access/projects` (id, name, type, visibility, bindings: no prose)
     and binds roles on it, while `/api/node`, its child and `/api/search` still
     hide it; a hidden non-root node is still a 404 on the bindings route; a
     member and an admin token are still refused; binding itself (audited) is
     what opens the content.

    python verify_project_roles.py
"""
from __future__ import annotations

import base64
import os
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
os.environ["ATLAS_RBAC"] = "projects"
os.environ["ATLAS_DEFAULT_PROJECT_VISIBILITY"] = "private"
os.environ["ATLAS_SHARED_PROJECT_ROLE"] = "viewer"
os.environ.pop("ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER", None)

import dataclasses  # noqa: E402

from sqlalchemy import func, select  # noqa: E402

import admin  # noqa: E402
import app  # noqa: E402
import rbac  # noqa: E402
from models_auth import AuthEvent, NodeGrant, ProjectRoleBinding  # noqa: E402
from rbac_testkit import (  # noqa: E402
    agent, bind, check, client, finish, group, mint, mk_edge, mk_node, mk_user, sign_in,
)

U = {n: f"u-r-{n}" for n in ("owner", "vic", "eddie", "mia", "gus", "pat", "granty", "weird",
                              "outsider")}
B = {n: f"u-r-{n}-bot" for n in ("eddie", "mia", "owner", "lone")}


def build() -> None:
    mk_user(U["owner"], "r-owner", "owner")
    for n in ("vic", "eddie", "mia", "gus", "pat", "granty", "weird", "outsider"):
        mk_user(U[n], f"r-{n}")
    for n in ("eddie", "mia", "owner"):
        mk_user(B[n], f"bot-r-{n}", is_service=True, person=U[n])
    mk_user(B["lone"], "bot-r-lone", is_service=True)
    with app.session() as s:
        for nid, t in (("ra", "project"), ("rb", "project"), ("rc", "project"),
                       ("ra-cap", "capability"), ("rb-cap", "capability"), ("rc-cap", "capability")):
            mk_node(s, nid, t)
        for i in range(1, 8):
            mk_node(s, f"ra-del{i}")
        mk_node(s, "rb-del")
        s.flush()
        mk_edge(s, "ra", "ra-cap")
        mk_edge(s, "rb", "rb-cap")
        mk_edge(s, "rc", "rc-cap")
        for i in range(1, 8):
            mk_edge(s, "ra", f"ra-del{i}")
        mk_edge(s, "rb", "rb-del")
        s.add(NodeGrant(node_id="ra", user_id=U["granty"]))
        s.commit()
    bind("ra", "user", U["vic"], "viewer")
    bind("ra", "user", U["eddie"], "editor")
    bind("ra", "user", U["mia"], "maintainer")
    bind("ra", "user", U["pat"], "owner")
    bind("rc", "user", U["pat"], "owner")
    bind("ra", "user", U["weird"], "superuser")       # not a role: resolves to none
    group("g-r-maint", "ra maintainers", [U["gus"]])
    bind("ra", "group", "g-r-maint", "maintainer")
    bind("rb", "user", U["gus"], "editor")
    bind("ra", "user", B["lone"], "maintainer")       # an unlinked agent's own binding


def forbidden(r, role: str | None = None) -> bool:
    if r.status_code != 403:
        return False
    body = r.json()
    return body.get("error") == "forbidden" and (role is None or body.get("needed_role") == role)


def section_a() -> None:
    print("\n-- A. viewer < editor < maintainer < owner, over HTTP --")
    vic, vc = sign_in("r-vic")
    check("viewer reads", vic.get("/api/node/ra-cap").status_code == 200)
    r = vic.patch("/api/node/ra-cap", json={"name": "x", "csrf_token": vc})
    check("viewer PATCH -> 403 naming editor", forbidden(r, "editor"), f"{r.status_code} {r.text[:160]}")
    r = vic.post("/api/workitem", json={"type": "task", "name": "x", "binding": "ra-cap", "csrf_token": vc})
    check("viewer files work into the project -> 403", forbidden(r, "editor"), f"{r.status_code} {r.text[:120]}")
    r = vic.put("/api/node/ra-cap/doc", json={"path": "docs/nodes/ra-cap/v.md", "content": "x", "csrf_token": vc})
    check("viewer writes a doc -> 403", forbidden(r, "editor"), f"{r.status_code}")
    r = vic.post("/api/edge", json={"src": "ra-cap", "dst": "ra-del1", "edge_type": "uses", "csrf_token": vc})
    check("viewer adds an edge -> 403", forbidden(r, "editor"), f"{r.status_code}")
    r = vic.post("/api/node/ra-cap/intent", json={"slug": "v-i", "kind": "principle", "title": "t",
                                                  "statement": "s", "csrf_token": vc})
    check("viewer adds an intent -> 403", forbidden(r, "editor"), f"{r.status_code}")

    ed, ec = sign_in("r-eddie")
    r = ed.patch("/api/node/ra-cap", json={"name": "Ra capability", "csrf_token": ec})
    check("editor PATCH -> 200", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    r = ed.post("/api/workitem", json={"type": "task", "name": "Eddie's task", "binding": "ra-cap",
                                       "csrf_token": ec})
    check("editor files work bound into the project -> 201", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
    task = r.json().get("id")
    r = ed.post(f"/api/node/{task}/status", json={"status": "in_progress", "csrf_token": ec})
    check("editor sets status -> 200", r.status_code == 200, f"{r.status_code}")
    r = ed.put("/api/node/ra-cap/doc", json={"path": "docs/nodes/ra-cap/e.md", "content": "x", "csrf_token": ec})
    check("editor writes a doc", r.status_code in (200, 201), f"{r.status_code} {r.text[:120]}")
    r = ed.post("/api/node/ra-cap/intent", json={"slug": "e-i", "kind": "principle", "title": "t",
                                                 "statement": "s", "csrf_token": ec})
    check("editor adds an intent", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
    intent_id = r.json().get("id")
    r = ed.post("/api/edge", json={"src": "ra-cap", "dst": "ra-del1", "edge_type": "uses", "csrf_token": ec})
    check("editor adds an edge", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
    edge_id = r.json().get("id")
    for label, req in (
        ("node", lambda: ed.request("DELETE", "/api/node/ra-del2?cascade=1", json={"csrf_token": ec})),
        ("edge", lambda: ed.request("DELETE", f"/api/edge/{edge_id}", json={"csrf_token": ec})),
        ("doc", lambda: ed.request("DELETE", "/api/node/ra-cap/doc", params={"path": "docs/nodes/ra-cap/e.md"},
                                   json={"csrf_token": ec})),
        ("intent", lambda: ed.request("DELETE", f"/api/intent/{intent_id}", json={"csrf_token": ec})),
    ):
        r = req()
        check(f"editor DELETE {label} -> 403 naming maintainer", forbidden(r, "maintainer"),
              f"{r.status_code} {r.text[:120]}")

    mia, mc = sign_in("r-mia")
    for label, req in (
        ("edge", lambda: mia.request("DELETE", f"/api/edge/{edge_id}", json={"csrf_token": mc})),
        ("node", lambda: mia.request("DELETE", "/api/node/ra-del2?cascade=1", json={"csrf_token": mc})),
        ("doc", lambda: mia.request("DELETE", "/api/node/ra-cap/doc", params={"path": "docs/nodes/ra-cap/e.md"},
                                    json={"csrf_token": mc})),
        ("intent", lambda: mia.request("DELETE", f"/api/intent/{intent_id}", json={"csrf_token": mc})),
    ):
        r = req()
        check(f"maintainer DELETE {label} in its project -> 200", r.status_code == 200,
              f"{r.status_code} {r.text[:120]}")
    r = mia.request("DELETE", "/api/node/ra?cascade=1", json={"csrf_token": mc})
    check("maintainer deleting the PROJECT ROOT -> 403 naming owner", forbidden(r, "owner"),
          f"{r.status_code} {r.text[:120]}")
    pat, pc = sign_in("r-pat")
    r = pat.request("DELETE", "/api/node/rc?cascade=1", json={"csrf_token": pc})
    check("a project OWNER deletes its project root", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    weird, _ = sign_in("r-weird")
    check("an unknown stored role is NO role: 404", weird.get("/api/node/ra-cap").status_code == 404)
    out, _ = sign_in("r-outsider")
    check("no binding on a private project: 404", out.get("/api/node/ra-cap").status_code == 404)
    gr, gc = sign_in("r-granty")
    r = gr.patch("/api/node/ra-cap", json={"summary": "granted", "csrf_token": gc})
    check("a node_grant on the project root counts as editor: PATCH 200", r.status_code == 200, f"{r.status_code}")
    r = gr.request("DELETE", "/api/node/ra-del3?cascade=1", json={"csrf_token": gc})
    check("...and not as maintainer: DELETE 403", forbidden(r, "maintainer"), f"{r.status_code}")


def section_b() -> None:
    print("\n-- B. a group-mapped maintainer of A, and cross-project edges --")
    gus, gc = sign_in("r-gus")
    r = gus.request("DELETE", "/api/node/ra-del3?cascade=1", json={"csrf_token": gc})
    check("group maintainer of A deletes in A", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    r = gus.request("DELETE", "/api/node/rb-del?cascade=1", json={"csrf_token": gc})
    check("...and gets 403 deleting in B, where it is editor", forbidden(r, "maintainer"),
          f"{r.status_code} {r.text[:120]}")
    check("...though it can read B (so 403, not 404)", gus.get("/api/node/rb-del").status_code == 200)
    owner, oc = sign_in("r-owner")
    r = owner.post("/api/edge", json={"src": "ra-cap", "dst": "rb-cap", "edge_type": "uses", "csrf_token": oc})
    check("setup: the owner adds an A->B edge", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
    eid = r.json().get("id")
    r = gus.request("DELETE", f"/api/edge/{eid}", json={"csrf_token": gc})
    check("a cross-project edge needs maintainer on BOTH ends: 403", forbidden(r, "maintainer"),
          f"{r.status_code} {r.text[:120]}")
    mia, mc = sign_in("r-mia")
    r = mia.request("DELETE", f"/api/edge/{eid}", json={"csrf_token": mc})
    check("an edge into a project the caller cannot see is a 404, not a 403", r.status_code == 404,
          str(r.status_code))
    r = owner.request("DELETE", f"/api/edge/{eid}", json={"csrf_token": oc})
    check("the instance owner (implicit project owner) deletes it", r.status_code == 200, str(r.status_code))


def section_c() -> None:
    print("\n-- C. agents: min(scope cap, person's project role) --")
    ed_admin = mint(B["eddie"], ["read", "write", "admin"], "r-eddie-admin")
    mia_admin = mint(B["mia"], ["read", "write", "admin"], "r-mia-admin")
    mia_read = mint(B["mia"], ["read"], "r-mia-read")
    mia_write = mint(B["mia"], ["read", "write"], "r-mia-write")
    own_admin = mint(B["owner"], ["read", "write", "admin"], "r-owner-admin")
    lone_admin = mint(B["lone"], ["read", "write", "admin"], "r-lone-admin")

    r = client(ed_admin).delete("/api/node/ra-del4?cascade=1")
    check("an admin-token agent of an EDITOR cannot delete: 403", forbidden(r, "maintainer"),
          f"{r.status_code} {r.text[:120]}")
    r = client(mia_admin).delete("/api/node/ra-del4?cascade=1")
    check("an admin-token agent of a MAINTAINER can", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    r = client(mia_read).patch("/api/node/ra-cap", json={"summary": "x"})
    check("a read token of a maintainer cannot write: 403", r.status_code == 403, f"{r.status_code}")
    r = client(mia_write).patch("/api/node/ra-cap", json={"summary": "by mia's agent"})
    check("a write token of a maintainer writes", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    with app.session() as s:
        caps = {sc: rbac.role_on_project(s, agent(B["mia"], sc, U["mia"]), "ra")
                for sc in (("read",), ("read", "write"), ("read", "write", "admin"), ("ingest",))}
        own = rbac.role_on_project(s, agent(B["owner"], ("read", "write", "admin"), U["owner"]), "ra")
    check("the cap, measured on the resolver: read=viewer, write=editor, admin=maintainer, "
          "a retired scope still reads",
          caps == {("read",): "viewer", ("read", "write"): "editor",
                   ("read", "write", "admin"): "maintainer", ("ingest",): "viewer"}, str(caps))
    check("no token exercises owner: the owner's own agent holds no role on a private project",
          own is None, str(own))
    r = client(own_admin).get("/api/node/ra-cap")
    check("...so it 404s there (role is never inherited, ADR 0034)", r.status_code == 404, str(r.status_code))
    r = client(lone_admin).delete("/api/node/ra-del5?cascade=1")
    check("an unlinked agent's OWN direct maintainer binding works", r.status_code == 200,
          f"{r.status_code} {r.text[:120]}")


def _events(name: str) -> int:
    with app.session() as s:
        return s.scalar(select(func.count()).select_from(AuthEvent).where(AuthEvent.event == name))


def section_d() -> None:
    print("\n-- D. the access API: groups, members, bindings (criterion 5) --")
    owner, oc = sign_in("r-owner")
    out, outc = sign_in("r-outsider")
    r = owner.post("/api/access/groups", json={"name": "  ", "csrf_token": oc})
    check("a blank group name: 400 naming `name`", r.status_code == 400 and r.json().get("field") == "name",
          f"{r.status_code} {r.text[:120]}")
    created_before = _events("group_created")
    r = owner.post("/api/access/groups", json={"name": "Team Ra", "csrf_token": oc})
    check("the owner creates a group: 201", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
    gid = r.json().get("id")
    check("...recorded as an auth_event", _events("group_created") == created_before + 1)
    r = owner.post("/api/access/groups", json={"name": "Team Ra", "csrf_token": oc})
    check("the same name again: 409", r.status_code == 409, str(r.status_code))
    r = owner.put(f"/api/access/groups/{gid}/members", json={"members": [B["mia"]], "csrf_token": oc})
    check("an agent as a member: 400 naming `members`",
          r.status_code == 400 and r.json().get("field") == "members", f"{r.status_code} {r.text[:120]}")
    r = owner.put(f"/api/access/groups/{gid}/members", json={"members": ["nobody"], "csrf_token": oc})
    check("an unknown account: 400", r.status_code == 400, str(r.status_code))
    r = owner.put(f"/api/access/groups/{gid}/members", json={"members": [U["outsider"]], "csrf_token": oc})
    check("the owner adds a member: 200", r.status_code == 200
          and [m["user_id"] for m in r.json().get("members", [])] == [U["outsider"]], f"{r.status_code} {r.text[:160]}")

    listing = owner.get("/api/access/projects")
    check("GET /api/access/projects (owner): 200 and lists ra", listing.status_code == 200
          and any(p["id"] == "ra" for p in listing.json().get("projects", [])), str(listing.status_code))
    ra = next(p for p in listing.json()["projects"] if p["id"] == "ra")
    # The fixture's unknown-role row ("superuser") is not re-sendable: the PUT
    # validates every role it is given. Leaving it out removes it, which the
    # owner may do (an unknown role outranks nothing).
    wanted = [b for b in ra["bindings"] if b["role"] in rbac.PROJECT_ROLES] + [
        {"principal_kind": "group", "principal_id": gid, "role": "editor"}]
    r = owner.put("/api/access/project/ra/bindings", json={"bindings": [
        {"principal_kind": "group", "principal_id": gid, "role": "admin"}], "csrf_token": oc})
    check("an unknown role: 400 naming bindings[0].role",
          r.status_code == 400 and r.json().get("field") == "bindings[0].role", f"{r.status_code} {r.text[:120]}")
    r = owner.put("/api/access/project/ra-cap/bindings", json={"bindings": [], "csrf_token": oc})
    check("binding on a node that is not a project root: 400", r.status_code == 400
          and r.json().get("error") == "not_a_project", f"{r.status_code} {r.text[:120]}")
    bound_before = _events("project_role_bound")
    r = owner.put("/api/access/project/ra/bindings", json={"bindings": wanted, "csrf_token": oc})
    check("the owner binds the group editor on ra: 200", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    check("...one project_role_bound event, the rest untouched", _events("project_role_bound") == bound_before + 1,
          str(_events("project_role_bound") - bound_before))
    check("the member now reads ra", out.get("/api/node/ra-cap").status_code == 200)
    r = out.patch("/api/node/ra-cap", json={"summary": "via the group", "csrf_token": outc})
    check("...and writes there (group editor)", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    vic, vc = sign_in("r-vic")
    r = vic.post("/api/access/groups", json={"name": "Vic's", "csrf_token": vc})
    check("a member creating a group: 403", r.status_code == 403, str(r.status_code))
    tok = mint(B["mia"], ["read", "write", "admin"], "r-mia-admin-2")
    r = client(tok).post("/api/access/groups", json={"name": "By a token"})
    check("an admin TOKEN creating a group: 403 (a person's act)", r.status_code == 403, str(r.status_code))
    r = client(tok).put("/api/access/project/ra/bindings", json={"bindings": []})
    check("an admin TOKEN changing bindings: 403", r.status_code == 403, str(r.status_code))
    r = vic.put("/api/access/project/ra/bindings", json={"bindings": [], "csrf_token": vc})
    check("a project VIEWER changing bindings: 403", r.status_code == 403, str(r.status_code))

    mia, mc = sign_in("r-mia")
    lm = mia.get("/api/access/projects")
    ids = [p["id"] for p in lm.json().get("projects", [])] if lm.status_code == 200 else []
    check("a project maintainer lists only the projects it administers", ids == ["ra"], str(ids))
    current = next(p for p in lm.json()["projects"] if p["id"] == "ra")["bindings"]
    r = mia.put("/api/access/project/ra/bindings", json={"bindings": current + [
        {"principal_kind": "user", "principal_id": U["outsider"], "role": "maintainer"}], "csrf_token": mc})
    check("a maintainer grants up to maintainer: 200", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    current = r.json().get("bindings", current)
    r = mia.put("/api/access/project/ra/bindings", json={"bindings": [
        b for b in current if b["principal_id"] != U["outsider"]] + [
        {"principal_kind": "user", "principal_id": U["outsider"], "role": "owner"}], "csrf_token": mc})
    check("...but not owner: 403", r.status_code == 403, f"{r.status_code} {r.text[:120]}")
    r = mia.put("/api/access/project/ra/bindings", json={"bindings": [
        b for b in current if b["principal_id"] != U["pat"]], "csrf_token": mc})
    check("...nor remove an owner's binding: 403", r.status_code == 403, f"{r.status_code} {r.text[:120]}")

    r = owner.request("DELETE", f"/api/access/groups/{gid}", json={"csrf_token": oc})
    check("the owner deletes the group, and its binding goes with it",
          r.status_code == 200 and r.json().get("bindings_removed") == 1, f"{r.status_code} {r.text[:120]}")
    with app.session() as s:
        left = s.scalar(select(func.count()).select_from(ProjectRoleBinding).where(
            ProjectRoleBinding.principal_id == gid))
    check("...no binding names it any more", left == 0, str(left))
    fresh = next(p for p in owner.get("/api/access/projects").json()["projects"] if p["id"] == "ra")
    r = owner.put("/api/access/project/ra/bindings", json={"bindings": [
        b for b in fresh["bindings"] if b["principal_id"] != U["outsider"]], "csrf_token": oc})
    check("cleanup: the owner removes the outsider's direct binding", r.status_code == 200, str(r.status_code))
    check("the outsider is back to 404", out.get("/api/node/ra-cap").status_code == 404)


def section_e() -> None:
    print("\n-- E. adoption: `atlas-admin rbac plan`, and switching off --")
    with app.session() as s:
        n_before = s.scalar(select(func.count()).select_from(ProjectRoleBinding))
        rows = admin.rbac_plan_rows(s)
        n_after = s.scalar(select(func.count()).select_from(ProjectRoleBinding))
    at = {(r["account"], r["kind"], r["project"]): r for r in rows}

    def row(account, project, kind="person"):
        return at.get((account, kind, project)) or {}

    check("the plan writes nothing", n_before == n_after)
    e = row("r-eddie", "ra")
    check("plan: an editor's person session can delete today, can only write with RBAC on",
          (e.get("today"), e.get("rbac"), e.get("changed")) == ("delete", "write", True), str(e))
    o = row("r-outsider", "ra")
    check("plan: an unbound member sees an unmarked project today and nothing with RBAC on "
          "(the default is private here)", (o.get("today"), o.get("rbac")) == ("delete", "none"), str(o))
    m = row("r-mia", "ra")
    check("plan: a maintainer is unchanged", m.get("changed") is False, str(m))
    agent_rows = [r for k, r in at.items() if k[0] == "bot-r-eddie" and k[2] == "ra"
                  and "admin" in k[1]]
    check("plan: the ADR's expected difference — an admin-token agent of an editor loses delete",
          bool(agent_rows) and all((r["today"], r["rbac"]) == ("delete", "write") for r in agent_rows),
          str(agent_rows[:1]))
    print(f"       {sum(1 for r in rows if r['changed'])} of {len(rows)} rows change")

    ed, ec = sign_in("r-eddie")
    r = ed.request("DELETE", "/api/node/ra-del6?cascade=1", json={"csrf_token": ec})
    check("RBAC on: the editor's delete is refused", forbidden(r, "maintainer"), str(r.status_code))
    prev = rbac.use(dataclasses.replace(rbac.SETTINGS, mode="off"))
    try:
        r = ed.request("DELETE", "/api/node/ra-del6?cascade=1", json={"csrf_token": ec})
        check("RBAC switched OFF: the same delete succeeds — today's rule, nothing was converted",
              r.status_code == 200, f"{r.status_code} {r.text[:120]}")
        out, _ = sign_in("r-outsider")
        check("...and the unbound member reads ra again (no mark: shared, as today)",
              out.get("/api/node/ra-cap").status_code == 200)
    finally:
        rbac.use(prev)
    check("switched back ON: the bindings still apply", sign_in("r-outsider")[0].get(
        "/api/node/ra-cap").status_code == 404)


RF_WORD = "quokkaledgerword"   # appears only in rf's and rf-cap's prose


def section_f() -> None:
    print("\n-- F. the instance admin binds on a project it cannot see (implicit owner OFF) --")
    with app.session() as s:
        mk_node(s, "rf", "project", name="Project F", summary=f"rf summary {RF_WORD}")
        mk_node(s, "rf-cap", "capability", summary=f"rf-cap summary {RF_WORD}")
        s.flush()
        mk_edge(s, "rf", "rf-cap")
        s.commit()
    prev = rbac.use(dataclasses.replace(rbac.SETTINGS, instance_admin_implicit_project_owner=False))
    try:
        owner, oc = sign_in("r-owner")
        check("the owner holds no role on rf: GET /api/node/rf -> 404",
              owner.get("/api/node/rf").status_code == 404)
        check("...nor its child", owner.get("/api/node/rf-cap").status_code == 404)

        listing = owner.get("/api/access/projects")
        rows = {p["id"]: p for p in listing.json().get("projects", [])} if listing.status_code == 200 else {}
        rf = rows.get("rf") or {}
        check("GET /api/access/projects (owner session) lists rf", listing.status_code == 200 and bool(rf),
              f"{listing.status_code} {sorted(rows)}")
        check("...with id, name, type, visibility and bindings only",
              set(rf) == {"id", "name", "type", "visibility", "visibility_marked", "grantable_up_to",
                          "bindings"} and rf.get("visibility") == "private" and rf.get("bindings") == [],
              str(rf))
        check("...and no prose: the listing never carries rf's word", RF_WORD not in listing.text)
        check("...and no child: rf-cap is not in it", "rf-cap" not in listing.text)

        r = owner.get("/api/search", params={"q": RF_WORD})
        check("search still finds nothing of rf for the owner",
              r.status_code == 200 and RF_WORD not in r.text.replace(f'"{RF_WORD}"', "")
              and '"rf"' not in r.text and '"rf-cap"' not in r.text, f"{r.status_code} {r.text[:200]}")

        r = owner.put("/api/access/project/rf-cap/bindings", json={"bindings": [], "csrf_token": oc})
        check("a hidden node that is not a project root: still the guard's 404", r.status_code == 404,
              f"{r.status_code} {r.text[:120]}")

        bound_before = _events("project_role_bound")
        r = owner.put("/api/access/project/rf/bindings", json={"bindings": [
            {"principal_kind": "user", "principal_id": U["outsider"], "role": "viewer"}], "csrf_token": oc})
        check("the owner binds the outsider viewer on rf: 200 (was 404)", r.status_code == 200
              and r.json().get("bindings") == [{"principal_kind": "user", "principal_id": U["outsider"],
                                                "role": "viewer"}], f"{r.status_code} {r.text[:160]}")
        check("...recorded as one project_role_bound event", _events("project_role_bound") == bound_before + 1)
        check("...and the response carries no prose", RF_WORD not in r.text)
        out, _ = sign_in("r-outsider")
        check("the outsider now reads rf's child", out.get("/api/node/rf-cap").status_code == 200)
        check("binding SOMEONE ELSE opens nothing for the owner: rf-cap still 404",
              owner.get("/api/node/rf-cap").status_code == 404)

        vic, vc = sign_in("r-vic")
        r = vic.put("/api/access/project/rf/bindings", json={"bindings": [], "csrf_token": vc})
        check("a member binding on rf: still 404 (cannot see it)", r.status_code == 404, str(r.status_code))
        own_admin = mint(B["owner"], ["read", "write", "admin"], "r-owner-admin-f")
        r = client(own_admin).put("/api/access/project/rf/bindings", json={"bindings": []})
        check("the owner's admin TOKEN binding on rf: 403 (a person's act)", r.status_code == 403,
              str(r.status_code))
        mia, _ = sign_in("r-mia")
        lm = mia.get("/api/access/projects")
        ids = [p["id"] for p in lm.json().get("projects", [])] if lm.status_code == 200 else []
        check("a project maintainer still lists only what it administers", ids == ["ra"], str(ids))

        r = owner.put("/api/access/project/rf/bindings", json={"bindings": [
            {"principal_kind": "user", "principal_id": U["outsider"], "role": "viewer"},
            {"principal_kind": "user", "principal_id": U["owner"], "role": "owner"}], "csrf_token": oc})
        check("the owner binds ITSELF owner (audited)", r.status_code == 200
              and _events("project_role_bound") == bound_before + 2, f"{r.status_code} {r.text[:120]}")
        check("...and only now reads rf's content", owner.get("/api/node/rf-cap").status_code == 200)
    finally:
        rbac.use(prev)


if __name__ == "__main__":
    app.startup()
    check("this process runs with RBAC on", rbac.enabled(), str(rbac.SETTINGS))
    build()
    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    section_f()
    finish()
