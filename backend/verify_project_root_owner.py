"""
TASK-1468 / ADR 0051 (amends ADR 0041): the creator owns a new project root.

Configuration: the work install's, as `deploy/oss/.env.example` ships it
(`ATLAS_RBAC=projects`, `ATLAS_DEFAULT_PROJECT_VISIBILITY=private`,
`ATLAS_SHARED_PROJECT_ROLE=viewer`, `ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER=0`).
That is where the defect was found, in a clean-VM install test on 2026-11-02:
the owner's agent POSTed a root `org`, got 201, and the next GET was 404.

  A. THE VM SEQUENCE. The instance owner's linked agent (write token) creates a
     root `org` over POST /api/node: 201, and now 200 on the next GET for the
     agent AND for its person's session. A re-create is still 409. A second root
     and a `contains` edge between them succeed (they failed "unknown node").
     The binding is the PERSON's (`owner`), never the agent's. A member person
     who creates a root is bound themself and can delete it (owner); another
     member still gets 404 (private stays private).
  B. AN UNLINKED AGENT gets 403 creating a root, in `rbac._forbidden`'s shape
     plus `reason`, and nothing is written: no node, no binding, no event. The
     same agent still files work bound into a project it is editor on.
  C. AN UNBOUND WORK ITEM is its own project (`rbac.project_map`), so POST
     /api/workitem with neither parent nor binding binds the creator's person
     the same way, whether an agent filed it (`atlas_file_work`) or the person
     did from the board: the second VM finding, a story the owner filed that
     his agent's unfiltered workitem list did not show. An unlinked agent is
     refused before an id is minted. A bound item creates no binding.
  D. AUDIT. Each binding is one `project_role_bound` `auth_event` with exactly
     the shape `PUT /api/access/project/{id}/bindings` writes (user_id the
     person, actor the caller's subject, detail `project=… user=… role=owner`),
     and nothing about it lands in `graph_event`.
  E. RBAC OFF. In process, `mode=off`: an unlinked agent's create is 201 again,
     no `project_role_binding` row and no `project_role_bound` event appear, and
     NO SQL statement touching `project_role_binding` runs during the requests.

    python verify_project_root_owner.py
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
os.environ["ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER"] = "0"

import dataclasses  # noqa: E402

from sqlalchemy import event, func, select  # noqa: E402

import app  # noqa: E402
import db  # noqa: E402
import rbac  # noqa: E402
from models_auth import AuthEvent, ProjectRoleBinding  # noqa: E402
from models_graph import GraphEvent, Node  # noqa: E402
from rbac_testkit import (  # noqa: E402
    bind, check, client, finish, mint, mk_node, mk_user, sign_in,
)

U = {n: f"u-po-{n}" for n in ("owner", "mem", "outsider")}
B = {n: f"u-po-{n}-bot" for n in ("owner", "lone")}
SUBJECT = {B["owner"]: "bot-po-owner", B["lone"]: "bot-po-lone"}


def build() -> None:
    mk_user(U["owner"], "po-owner", "owner")
    mk_user(U["mem"], "po-mem")
    mk_user(U["outsider"], "po-outsider")
    mk_user(B["owner"], SUBJECT[B["owner"]], is_service=True, person=U["owner"])
    mk_user(B["lone"], SUBJECT[B["lone"]], is_service=True)
    with app.session() as s:
        mk_node(s, "po-lone-proj", "project")
        s.commit()
    bind("po-lone-proj", "user", B["lone"], "editor")   # the unlinked agent's own binding


def bindings(project_id: str) -> dict:
    with app.session() as s:
        return {(b.principal_kind, b.principal_id): b.role for b in s.scalars(
            select(ProjectRoleBinding).where(ProjectRoleBinding.project_id == project_id)).all()}


def bound_events(project_id: str) -> list:
    with app.session() as s:
        return [(e.user_id, e.actor, e.detail) for e in s.scalars(
            select(AuthEvent).where(AuthEvent.event == "project_role_bound",
                                    AuthEvent.detail.like(f"project={project_id} %"))).all()]


def count(model, *where) -> int:
    with app.session() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where)) or 0


def section_a(owner_write: str) -> None:
    print("\n-- A. the VM sequence: 201 then 200, for the agent and its person --")
    agent = client(owner_write)
    r = agent.post("/api/node", json={"id": "po-org", "type": "org", "name": "Org"})
    check("the owner's agent creates a root org: 201", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    r = agent.get("/api/node/po-org")
    check("...and the very next GET by the agent is 200 (was 404)", r.status_code == 200,
          f"{r.status_code} {r.text[:120]}")
    person, pc = sign_in("po-owner")
    check("...and by its PERSON's session, 200", person.get("/api/node/po-org").status_code == 200)
    check("the binding is the PERSON's, owner; the agent holds none",
          bindings("po-org") == {("user", U["owner"]): "owner"}, str(bindings("po-org")))
    r = agent.post("/api/node", json={"id": "po-org", "type": "org", "name": "Org again"})
    check("a re-create is still 409", r.status_code == 409, str(r.status_code))
    r = agent.post("/api/node", json={"id": "po-proj", "type": "project", "name": "Proj"})
    check("a second root: 201", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
    r = agent.post("/api/edge", json={"src": "po-org", "dst": "po-proj", "edge_type": "contains"})
    check("a contains edge between the two roots: 201 (was 'unknown node')", r.status_code == 201,
          f"{r.status_code} {r.text[:160]}")
    check("...and the child stays visible to the agent and the person",
          agent.get("/api/node/po-proj").status_code == 200
          and person.get("/api/node/po-proj").status_code == 200)
    r = person.get("/api/graph")
    ids = {n.get("id") for n in ((r.json() or {}).get("nodes") or [])} if r.status_code == 200 else set()
    check("the person's /api/graph (the board's read) includes the new root",
          "po-org" in ids, f"{r.status_code} {sorted(ids)[:8]}")

    mem, mc = sign_in("po-mem")
    r = mem.post("/api/node", json={"id": "po-mem-root", "type": "capability", "name": "Mine",
                                    "csrf_token": mc})
    check("a member person creates a root: 201", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
    check("...is bound owner themself", bindings("po-mem-root") == {("user", U["mem"]): "owner"},
          str(bindings("po-mem-root")))
    check("...reads it: 200", mem.get("/api/node/po-mem-root").status_code == 200)
    out, _ = sign_in("po-outsider")
    check("another member still gets 404: private stays private",
          out.get("/api/node/po-mem-root").status_code == 404)
    r = mem.request("DELETE", "/api/node/po-mem-root", json={"csrf_token": mc})
    check("...and the creator may delete it (deleting a project root takes owner)",
          r.status_code == 200, f"{r.status_code} {r.text[:120]}")


def section_b(lone_write: str) -> None:
    print("\n-- B. an agent with no linked person may not create a project root --")
    events_before = count(AuthEvent, AuthEvent.event == "project_role_bound")
    r = client(lone_write).post("/api/node", json={"id": "po-lone-root", "type": "org", "name": "x"})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    check("403, error forbidden, reason names the missing person, needed_role owner",
          r.status_code == 403 and body.get("error") == "forbidden"
          and body.get("reason") == rbac.REASON_UNLINKED_AGENT and body.get("needed_role") == "owner"
          and body.get("project") == "po-lone-root" and "role" in body and body.get("detail"),
          f"{r.status_code} {r.text[:200]}")
    detail = body.get("detail") or ""
    check("...the node refusal advises linking the agent, and nothing it cannot follow "
          "(POST /api/node takes no parent)",
          "atlas-admin bot link" in detail and "parent" not in detail, detail)
    with app.session() as s:
        check("...and no node was written", s.get(Node, "po-lone-root") is None)
    check("...no binding", bindings("po-lone-root") == {})
    check("...no event", count(AuthEvent, AuthEvent.event == "project_role_bound") == events_before
          and count(GraphEvent, GraphEvent.entity_id == "po-lone-root") == 0)
    r = client(lone_write).post("/api/workitem", json={"type": "task", "name": "bound",
                                                       "binding": "po-lone-proj"})
    check("the same agent still files work INTO a project it is editor on: 201",
          r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    tid = (r.json() or {}).get("id")
    check("...and that bound item gets no binding of its own", tid and bindings(tid) == {}, str(tid))


def section_c(owner_write: str, lone_write: str) -> None:
    print("\n-- C. an unbound work item is its own project, so the same rule --")
    agent = client(owner_write)
    r = agent.post("/api/workitem", json={"type": "epic", "id": "po-epic", "name": "Loose epic"})
    check("the owner's agent files an unbound epic: 201", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    check("...the agent reads it: 200", agent.get("/api/node/po-epic").status_code == 200)
    person, _ = sign_in("po-owner")
    check("...its person reads it: 200", person.get("/api/node/po-epic").status_code == 200)
    check("...bound owner to the person", bindings("po-epic") == {("user", U["owner"]): "owner"},
          str(bindings("po-epic")))
    r = agent.post("/api/workitem", json={"type": "task", "name": "Under the epic", "parent": "po-epic"})
    check("a task under it (a parent, so not a root): 201, no binding of its own",
          r.status_code == 201 and bindings((r.json() or {}).get("id")) == {},
          f"{r.status_code} {r.text[:160]}")
    # The second VM finding: a STORY the instance owner filed on the board (a
    # browser session, POST /api/workitem with no binding) was invisible to his
    # linked agent; an unfiltered workitem list returned 0 for the agent.
    person, pc = sign_in("po-owner")
    r = person.post("/api/workitem", json={"type": "story", "id": "po-story", "name": "Filed on the board",
                                           "csrf_token": pc})
    check("the owner's session files an unbound story (the board's create): 201",
          r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    check("...the person who filed it reads it: 200", person.get("/api/node/po-story").status_code == 200)
    check("...bound owner to that person", bindings("po-story") == {("user", U["owner"]): "owner"},
          str(bindings("po-story")))
    check("...the linked agent reads it: 200", agent.get("/api/node/po-story").status_code == 200)
    for who, c in (("the person", person), ("the agent", agent)):
        r = c.get("/api/workitems")
        ids = {i.get("id") for i in ((r.json() or {}).get("items") or [])} if r.status_code == 200 else set()
        check(f"...and an unfiltered GET /api/workitems lists it for {who} (was 0 for the agent)",
              "po-story" in ids, f"{r.status_code} total={(r.json() or {}).get('total')}")

    with app.session() as s:
        nodes_before = s.scalar(select(func.count()).select_from(Node))
    r = client(lone_write).post("/api/workitem", json={"type": "task", "name": "loose"})
    check("an unlinked agent filing an UNBOUND item: 403 with the reason",
          r.status_code == 403 and (r.json() or {}).get("reason") == rbac.REASON_UNLINKED_AGENT,
          f"{r.status_code} {r.text[:200]}")
    detail = (r.json() or {}).get("detail") or "" if r.status_code == 403 else ""
    check("...and the work-item refusal also offers a parent or binding",
          "atlas-admin bot link" in detail and "under a parent or binding" in detail, detail)
    with app.session() as s:
        check("...and no row was written", s.scalar(select(func.count()).select_from(Node)) == nodes_before)


def section_d() -> None:
    print("\n-- D. the binding is audited like any other binding, in auth_event --")
    ev = bound_events("po-org")
    check("one project_role_bound event for po-org",
          ev == [(U["owner"], SUBJECT[B["owner"]], f"project=po-org user={U['owner']} role=owner")], str(ev))
    ev = bound_events("po-mem-root")
    check("the member's own create: actor is the member",
          ev == [(U["mem"], "po-mem", f"project=po-mem-root user={U['mem']} role=owner")], str(ev))
    check("the unbound epic's binding is audited too", len(bound_events("po-epic")) == 1,
          str(bound_events("po-epic")))
    with app.session() as s:
        actions = set(s.scalars(select(GraphEvent.action).where(GraphEvent.entity_id == "po-org")).all())
    check("graph_event carries the node.create and nothing about the role",
          "node.create" in actions and not any("role" in a or "bind" in a for a in actions), str(actions))


def section_e(lone_write: str) -> None:
    print("\n-- E. RBAC off: nothing extra read, nothing written --")
    seen: list[str] = []

    def spy(conn, cursor, statement, params, context, executemany):
        if "project_role_binding" in statement:
            seen.append(statement)

    prev = rbac.use(dataclasses.replace(rbac.SETTINGS, mode="off"))
    rows_before = count(ProjectRoleBinding)
    events_before = count(AuthEvent, AuthEvent.event == "project_role_bound")
    event.listen(db.engine, "before_cursor_execute", spy)
    try:
        r = client(lone_write).post("/api/node", json={"id": "po-off-root", "type": "org", "name": "Off"})
        check("an unlinked agent creates a root again: 201", r.status_code == 201,
              f"{r.status_code} {r.text[:160]}")
        r = client(lone_write).post("/api/workitem", json={"type": "task", "name": "off loose"})
        check("...and an unbound work item: 201", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    finally:
        event.remove(db.engine, "before_cursor_execute", spy)
        rbac.use(prev)
    check("no project_role_binding row appeared", count(ProjectRoleBinding) == rows_before)
    check("no project_role_bound event appeared",
          count(AuthEvent, AuthEvent.event == "project_role_bound") == events_before)
    check("no SQL statement touched project_role_binding during those requests", seen == [],
          str(seen[:2]))


if __name__ == "__main__":
    app.startup()
    check("this process runs with RBAC on, no implicit instance owner",
          rbac.enabled() and not rbac.SETTINGS.instance_admin_implicit_project_owner, str(rbac.SETTINGS))
    build()
    owner_write = mint(B["owner"], ["read", "write"], "po-owner-write")
    lone_write = mint(B["lone"], ["read", "write"], "po-lone-write")
    section_a(owner_write)
    section_b(lone_write)
    section_c(owner_write, lone_write)
    section_d()
    section_e(lone_write)
    finish()
