"""
TASK-1472: unbound, parentless work reaches the board.

The defect, measured on a test install (the work configuration, ADR 0041 RBAC
on): a story filed with POST /api/workitem with no parent and no binding
existed, and its creator could read and search it, but it never appeared on
board v2 and the board's search could not find it. Board v2 loads
`/api/graph?depth=8&families=object,bridge&cap=2000&prose=none`, which seeds
from the WORLD's roots and walks edges; a workitem connected to nothing is
never reached. The fix is the opt-in `unbound_work=1` (routers/graph.py,
"UNBOUND, PARENTLESS WORK"), which the board now sends.

  A. THE DEFECT, RBAC ON. A member files an unbound story on the board's route.
     Without the parameter it is absent (pinned, so the opt-in stays opt-in);
     with it, present. An unbound epic brings its task and the `contains` edge
     between them. Everything the first pass returned is still returned, and
     every node added is work. Another member, bound to the same world, sees
     none of it, and none of its ids appear anywhere in that body.
  B. THE CAP. With `cap` equal to the first pass's size the answer is the first
     pass, node for node, and `truncated` is set: the world is served first and
     a missed root with no room left is announced, not dropped in silence.
  C. NOTHING ELSE MOVES. With `focus`, with no `families`, and with a
     `families` that already names `workitem`, the parameter is a no-op: the
     body is byte-identical to the one without it.
  D. RBAC OFF, the homelab's configuration: a parentless epic marked private
     (and its child) stays out of a member's body, ids and all, while the
     owner sees them; an unmarked loose item reaches the member.
  E. POST /api/node WITH A WORK TYPE (the second half of TASK-1472). A task or
     epic sent with no status is stored `pending` and records the node.status
     event a status written at creation always records; a status the caller
     sent is kept; a world type gets none; an invalid status is still the 400
     naming the field.

    python verify_unbound_work.py
"""
from __future__ import annotations

import base64
import dataclasses
import json
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
# The configuration the defect was measured in (deploy/oss/.env.example).
os.environ["ATLAS_RBAC"] = "projects"
os.environ["ATLAS_DEFAULT_PROJECT_VISIBILITY"] = "private"
os.environ["ATLAS_SHARED_PROJECT_ROLE"] = "viewer"
os.environ["ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER"] = "0"

from sqlalchemy import select  # noqa: E402

import app  # noqa: E402
import rbac  # noqa: E402
from models_auth import NodeVisibility  # noqa: E402
from models_graph import GraphEvent, Node  # noqa: E402
from rbac_testkit import (  # noqa: E402
    bind, check, finish, mk_edge, mk_node, mk_user, sign_in,
)

#: What frontend/v2/app.js asks for, minus the new parameter.
BOARD = "/api/graph?depth=8&families=object,bridge&cap=2000&prose=none"
WORK_TYPES = {"epic", "story", "task", "decision"}

U = {n: f"u-uw-{n}" for n in ("owner", "mem", "other")}


def build() -> None:
    mk_user(U["owner"], "uw-owner", "owner")
    mk_user(U["mem"], "uw-mem")
    mk_user(U["other"], "uw-other")
    with app.session() as s:
        mk_node(s, "uw-world", "project")
        mk_node(s, "uw-world-part", "capability")
        mk_node(s, "uw-bound-task", "task", attributes={"status": "pending"})
        s.flush()
        mk_edge(s, "uw-world", "uw-world-part")
        mk_edge(s, "uw-bound-task", "uw-world-part", "works_on")
        s.commit()
    for who in ("mem", "other"):
        bind("uw-world", "user", U[who], "viewer")
    bind("uw-world", "user", U["owner"], "owner")


def ids(body: dict) -> set[str]:
    return {n["id"] for n in body.get("nodes") or []}


def edge_pairs(body: dict) -> set[tuple[str, str, str]]:
    return {(e["src"], e["dst"], e["edge_type"]) for e in body.get("edges") or []}


def section_a() -> dict:
    print("\n-- A. the defect, RBAC on: a loose story reaches the board --")
    mem, mc = sign_in("uw-mem")
    # Story and epic ids are caller-supplied slugs; a task id is minted by the
    # server and read back from the 201 (POST /api/workitem's own rules).
    story = "uw-loose-story"
    r = mem.post("/api/workitem", json={"type": "story", "id": story, "name": "Loose story",
                                        "csrf_token": mc})
    check("a member files a story with no parent and no binding: 201",
          r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    r = mem.post("/api/workitem", json={"type": "epic", "id": "uw-loose-epic",
                                        "name": "Loose epic", "csrf_token": mc})
    check("...and an unbound epic: 201", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    r = mem.post("/api/workitem", json={"type": "task", "name": "Under it",
                                        "parent": "uw-loose-epic", "csrf_token": mc})
    check("...and a task under that epic: 201", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    kid = (r.json() or {}).get("id") if r.status_code == 201 else None
    check("...whose minted id is returned", bool(kid), r.text[:160])
    check("the creator reads the story: 200", mem.get(f"/api/node/{story}").status_code == 200)

    before = mem.get(BOARD).json()
    check("WITHOUT unbound_work the story is still absent (the defect; the opt-in stays opt-in)",
          story not in ids(before) and "uw-bound-task" in ids(before), sorted(ids(before)))
    after = mem.get(BOARD + "&unbound_work=1").json()
    got = ids(after)
    check("WITH unbound_work=1 the loose story is in the board's payload",
          story in got, sorted(got))
    check("...and the loose epic, its task, and the contains edge between them",
          bool(kid) and {"uw-loose-epic", kid} <= got
          and ("uw-loose-epic", kid, "contains") in edge_pairs(after),
          sorted(got))
    check("everything the first pass returned is still returned", ids(before) <= got,
          sorted(ids(before) - got))
    with app.session() as s:
        added_types = {s.get(Node, i).type for i in got - ids(before)}
    check("...and every node added is work", added_types <= WORK_TYPES, str(added_types))
    check("not truncated at the board's cap", after.get("truncated") is False)

    other, _ = sign_in("uw-other")
    ob = other.get(BOARD + "&unbound_work=1")
    text = ob.text
    check("another member bound to the same world gets 200", ob.status_code == 200)
    check("...and the bound task (the world walk is unchanged for them)",
          "uw-bound-task" in ids(ob.json()))
    leaked = [i for i in (story, "uw-loose-epic", kid) if i and f'"{i}"' in text]
    check("...and NONE of the member's loose ids appear anywhere in that body", not leaked, str(leaked))
    return {"mem": mem, "story": story, "before": before, "after": after}


def section_b(ctx: dict) -> None:
    print("\n-- B. the cap is spent on the world first --")
    mem, before, after = ctx["mem"], ctx["before"], ctx["after"]
    # Counts from the real answers, not assumed: `n` is the first pass, `full`
    # the first pass plus everything the second pass adds at the board's cap.
    # Section A files a loose story and a loose epic with a task under it, so
    # the second pass adds at least three nodes from at least two roots.
    n, full = len(ids(before)), len(ids(after))
    check(f"fixture: the second pass adds at least 3 nodes ({full - n} added, n={n})",
          full - n >= 3, f"before={n} after={full}")
    body = mem.get(BOARD.replace("cap=2000", f"cap={n}") + "&unbound_work=1").json()
    check(f"cap={n} (the first pass's size): exactly the first pass's nodes",
          ids(body) == ids(before), sorted(ids(body) ^ ids(before)))
    check("...and truncated is set, because a missed root had no room", body.get("truncated") is True)
    body = mem.get(BOARD.replace("cap=2000", f"cap={n + 1}") + "&unbound_work=1").json()
    check(f"cap={n + 1}: exactly one node more, a superset of the first pass, and truncated "
          f"because {full - n - 1} more had no room",
          len(ids(body)) == n + 1 and ids(before) < ids(body) <= ids(after)
          and body.get("truncated") is True,
          f"{len(ids(body))} truncated={body.get('truncated')}")
    body = mem.get(BOARD.replace("cap=2000", f"cap={full}") + "&unbound_work=1").json()
    check(f"cap={full} (exactly room for everything): the full answer, not truncated",
          ids(body) == ids(after) and body.get("truncated") is False,
          f"{len(ids(body))} truncated={body.get('truncated')}")


def section_c(ctx: dict) -> None:
    print("\n-- C. nothing else moves --")
    mem = ctx["mem"]
    for label, url in (
        ("focus=uw-world", "/api/graph?focus=uw-world&depth=3&prose=none"),
        ("no families", "/api/graph?depth=8&cap=2000&prose=none"),
        ("families naming workitem", "/api/graph?depth=8&families=object,bridge,workitem&cap=2000"),
    ):
        a = mem.get(url).json()
        b = mem.get(url + "&unbound_work=1").json()
        check(f"{label}: byte-identical with and without unbound_work=1",
              json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True),
              sorted(ids(a) ^ ids(b)))
    no_fam = mem.get("/api/graph?depth=8&cap=2000&prose=none").json()
    check("...and the unfiltered read already carried the loose story (why it is a no-op there)",
          ctx["story"] in ids(no_fam))


def section_d() -> None:
    print("\n-- D. RBAC off: a private loose epic stays hidden, counts and all --")
    prev = rbac.use(dataclasses.replace(rbac.SETTINGS, mode="off"))
    try:
        with app.session() as s:
            mk_node(s, "uw-hidden-epic", "epic", attributes={"status": "in_progress"})
            mk_node(s, "uw-hidden-kid", "task", attributes={"status": "done"})
            mk_node(s, "uw-open-loose", "task", attributes={"status": "pending"})
            s.flush()
            mk_edge(s, "uw-hidden-epic", "uw-hidden-kid")
            s.add(NodeVisibility(node_id="uw-hidden-epic", visibility="private"))
            s.commit()
        mem, _ = sign_in("uw-mem")
        r = mem.get(BOARD + "&unbound_work=1")
        got = ids(r.json())
        check("the member gets the unmarked loose task", "uw-open-loose" in got, sorted(got))
        leaked = [i for i in ("uw-hidden-epic", "uw-hidden-kid") if f'"{i}"' in r.text]
        check("...and neither the private epic nor its child appears anywhere in the body",
              not leaked, str(leaked))
        owner, _ = sign_in("uw-owner")
        og = ids(owner.get(BOARD + "&unbound_work=1").json())
        check("the owner sees the private epic and its child",
              {"uw-hidden-epic", "uw-hidden-kid", "uw-open-loose"} <= og, sorted(og))
    finally:
        rbac.use(prev)


def section_e() -> None:
    print("\n-- E. POST /api/node with a work type gets the default status --")
    prev = rbac.use(dataclasses.replace(rbac.SETTINGS, mode="off"))
    try:
        owner, oc = sign_in("uw-owner")

        def post(body: dict):
            return owner.post("/api/node", json={**body, "csrf_token": oc})

        r = post({"id": "uw-node-task", "type": "task", "name": "Filed generically"})
        check("a task with no status: 201", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
        check("...reported as pending", (r.json() or {}).get("status") == "pending", r.text[:200])
        with app.session() as s:
            n = s.get(Node, "uw-node-task")
            evs = [e.detail for e in s.scalars(select(GraphEvent).where(
                GraphEvent.entity_id == "uw-node-task", GraphEvent.action == "node.status")).all()]
        check("...stored as attributes.status = pending",
              n is not None and (n.attributes or {}).get("status") == "pending",
              str(n.attributes if n else None))
        check("...with the node.status event a status written at creation records",
              evs == [{"from": None, "to": "pending"}], str(evs))
        r = post({"id": "uw-node-epic", "type": "epic", "name": "e", "attributes": {"status": None}})
        check("an epic sent an explicit null status is given pending too",
              r.status_code == 201 and r.json().get("status") == "pending", r.text[:200])
        r = post({"id": "uw-node-kept", "type": "task", "name": "k",
                  "attributes": {"status": "blocked"}})
        check("a status the caller sent is kept",
              r.status_code == 201 and r.json().get("status") == "blocked", r.text[:200])
        r = post({"id": "uw-node-world", "type": "capability", "name": "w"})
        with app.session() as s:
            w = s.get(Node, "uw-node-world")
        check("a world type gets no status",
              r.status_code == 201 and w is not None and "status" not in (w.attributes or {}),
              str(w.attributes if w else None))
        r = post({"id": "uw-node-bad", "type": "task", "name": "b", "attributes": {"status": 123}})
        check("an invalid status is still the 400 naming the field",
              r.status_code == 400 and (r.json() or {}).get("field") == "status",
              f"{r.status_code} {r.text[:160]}")
    finally:
        rbac.use(prev)


if __name__ == "__main__":
    app.startup()
    check("this process runs with RBAC on, no implicit instance owner",
          rbac.enabled() and not rbac.SETTINGS.instance_admin_implicit_project_owner,
          str(rbac.SETTINGS))
    build()
    ctx = section_a()
    section_b(ctx)
    section_c(ctx)
    section_d()
    section_e()
    finish()
