"""
Verification for `GET /api/node/{id}/structure` (TASK-1210): where a node sits and what
it links to, names and states only, through the real auth gate, against a real
disposable Postgres via `testdb`.

The graph: an epic contains a story, which contains three tasks; t1 depends on t2;
t1 binds to a shared world node. Beside it, a PRIVATE project holding a task and a
world node, and the backlog points INTO it: t1 also depends on the hidden task, the
story also contains the hidden task (still hidden: visibility is ALL-over-parents),
and t3 binds to the hidden world node -- which makes t3 itself hidden, because a
workitem follows what it works on (effective binding).

SECTIONS:
  A. Shape: every node is {id, type, name, status} plus the one fact its list needs;
     no prose column reaches the response, even when the node carries prose.
  B. Place and links, as a member: ancestors nearest first, children with their own
     counts, dependencies, dependants, bindings.
  C. Visibility: nothing in the private project appears for a member -- not as a
     dependency, not as a binding, not as a child, not in a child COUNT -- while the
     owner sees all of it; a hidden focus 404s exactly like a nonexistent one.
  D. The children cap: `children_limit`, `children_total`, `children_truncated`.
  E. Bytes against `GET /api/node/{id}` for a task, a story and an epic (printed,
     and the structure read must be the smaller one).

Run (from a venv with the backend requirements installed):
    python verify_node_structure.py
"""
from __future__ import annotations

import base64
import json
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

from sqlalchemy import select  # noqa: E402

import app  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthUser, NodeVisibility  # noqa: E402
from models_graph import Edge, Node  # noqa: E402

failures: list[str] = []
PROSE = "THIS-PROSE-MUST-NEVER-APPEAR-IN-A-STRUCTURE-READ"
OWNER_PW = "Correct-Horse-Battery-Owner-9!"
MEMBER_PW = "Correct-Horse-Battery-Member-9!"


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def mk_node(s, nid: str, ntype: str, status: str | None = None) -> None:
    attrs = {"status": status} if status else {}
    s.add(Node(id=nid, type=ntype, name=f"name of {nid}", attributes=attrs, attention_count=0,
               completion=0.0, origin="atlas", summary=PROSE, abstract=PROSE, detail=PROSE,
               body=PROSE if ntype in ("epic", "story", "task") else None))


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


def client():
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN)


def sign_in(c, username: str, password: str) -> None:
    page = c.get("/auth/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": username, "password": password, "csrf_token": token},
           headers={"Origin": TEST_ORIGIN}, follow_redirects=False)


def ids(rows) -> list[str]:
    return [r["id"] for r in rows]


def build() -> None:
    with app.session() as s:
        mk_node(s, "s-epic", "epic", "in_progress")
        mk_node(s, "s-story", "story", "pending")
        for t, st in (("s-t1", "pending"), ("s-t2", "done"), ("s-t3", "pending")):
            mk_node(s, t, "task", st)
        mk_node(s, "s-world", "capability")
        mk_node(s, "s-secret", "project")
        mk_node(s, "s-secret-task", "task", "pending")
        mk_node(s, "s-secret-world", "capability")
        s.flush()
        mk_edge(s, "s-epic", "s-story")
        for t in ("s-t1", "s-t2", "s-t3"):
            mk_edge(s, "s-story", t)
        mk_edge(s, "s-secret", "s-secret-task")
        mk_edge(s, "s-secret", "s-secret-world")
        mk_edge(s, "s-story", "s-secret-task")            # a second parent: still hidden
        mk_edge(s, "s-t1", "s-t2", "depends_on")
        mk_edge(s, "s-t1", "s-secret-task", "depends_on")
        mk_edge(s, "s-t1", "s-world", "works_on")
        mk_edge(s, "s-t3", "s-secret-world", "works_on")   # t3 is hidden through its binding
        s.add(NodeVisibility(node_id="s-secret", visibility="private"))
        s.commit()


def main() -> None:
    # `startup()` is an @app.on_event handler, so importing app creates no tables, and the
    # users and graph below are written directly rather than over HTTP (verify_node_access.py
    # does the same, for the same reason).
    app.startup()
    mk_user("u-owner", "verify-structure-owner", "owner", OWNER_PW)
    mk_user("u-member", "verify-structure-member", "member", MEMBER_PW)
    build()
    owner, member = client(), client()
    sign_in(owner, "verify-structure-owner", OWNER_PW)
    sign_in(member, "verify-structure-member", MEMBER_PW)

    def structure(c, nid, **params):
        r = c.get(f"/api/node/{nid}/structure", params=params)
        return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else None), r.text

    print("\n-- A. shape: names and states only, never prose --")
    code, t1, raw = structure(member, "s-t1")
    check("a visible task answers 200", code == 200, str(code))
    check("the node is exactly {id, type, name, status}",
          set(t1["node"]) == {"id", "type", "name", "status"}, str(t1["node"]))
    check("status is the effective one", t1["node"]["status"] == "pending", str(t1["node"]))
    check("no prose column reaches the response, though every node carries prose", PROSE not in raw)
    keys = {"node", "ancestors", "children", "children_total", "children_truncated",
            "dependencies", "dependants", "bindings"}
    check("the top-level keys are exactly the documented ones", set(t1) == keys, str(sorted(t1)))
    check("ancestors carry depth and nothing else extra",
          all(set(a) == {"id", "type", "name", "status", "depth"} for a in t1["ancestors"]), str(t1["ancestors"]))
    check("bindings carry edge_type and nothing else extra",
          all(set(b) == {"id", "type", "name", "status", "edge_type"} for b in t1["bindings"]), str(t1["bindings"]))

    print("\n-- B. place and links, as a member --")
    check("ancestors nearest first: story at depth 1, epic at depth 2",
          [(a["id"], a["depth"]) for a in t1["ancestors"]] == [("s-story", 1), ("s-epic", 2)], str(t1["ancestors"]))
    check("t1's dependencies are t2 only (the hidden task is filtered)",
          ids(t1["dependencies"]) == ["s-t2"], str(t1["dependencies"]))
    check("t1's binding is the shared world node, with its edge type",
          [(b["id"], b["edge_type"]) for b in t1["bindings"]] == [("s-world", "works_on")], str(t1["bindings"]))
    check("a task has no children", t1["children"] == [] and t1["children_total"] == 0, str(t1["children"]))
    _, t2, _ = structure(member, "s-t2")
    check("t2's dependants are t1", ids(t2["dependants"]) == ["s-t1"], str(t2["dependants"]))
    _, story, _ = structure(member, "s-story")
    check("the story's children are its two visible tasks, each with a child count",
          [(c["id"], c["children"]) for c in story["children"]] == [("s-t1", 0), ("s-t2", 0)],
          str(story["children"]))
    _, epic, _ = structure(member, "s-epic")
    check("the epic's child is the story, which has 2 visible children",
          [(c["id"], c["children"]) for c in epic["children"]] == [("s-story", 2)], str(epic["children"]))
    check("the epic is a root: no ancestors", epic["ancestors"] == [], str(epic["ancestors"]))

    print("\n-- C. visibility: the private project never surfaces for a member --")
    check("t2 has no binding", t2["bindings"] == [], str(t2["bindings"]))
    member_everything = json.dumps([t1, t2, story, epic])
    check("no hidden id appears anywhere in a member's answers (nor t3, hidden by its binding)",
          "s-secret" not in member_everything and "s-t3" not in member_everything)
    check("the story's child total counts visible children only", story["children_total"] == 2,
          str(story["children_total"]))
    _, o_t1, _ = structure(owner, "s-t1")
    check("the owner sees the hidden task as a dependency",
          ids(o_t1["dependencies"]) == ["s-secret-task", "s-t2"], str(o_t1["dependencies"]))
    _, o_epic, _ = structure(owner, "s-epic")
    check("and counts it, and t3, among the story's children", o_epic["children"][0]["children"] == 4,
          str(o_epic["children"]))
    _, o_t3, _ = structure(owner, "s-t3")
    check("the owner sees t3's binding into the private project",
          [(b["id"], b["edge_type"]) for b in o_t3["bindings"]] == [("s-secret-world", "works_on")],
          str(o_t3["bindings"]))
    code_t3, _, _ = structure(member, "s-t3")
    check("a task hidden by its binding 404s for a member", code_t3 == 404, str(code_t3))
    code_h, body_h, _ = structure(member, "s-secret-task")
    code_n, body_n, _ = structure(member, "s-no-such-node")
    check("a hidden focus 404s", code_h == 404, str(code_h))
    check("exactly like a nonexistent one (same status, same keys)",
          code_n == 404 and set(body_h) == set(body_n), f"{body_h} vs {body_n}")

    print("\n-- D. the children cap --")
    _, capped, _ = structure(member, "s-story", children_limit=1)
    check("children_limit caps the list", ids(capped["children"]) == ["s-t1"], str(capped["children"]))
    check("and says so instead of cutting silently",
          capped["children_truncated"] is True and capped["children_total"] == 2,
          f"truncated={capped['children_truncated']} total={capped['children_total']}")
    check("an uncapped list is not truncated", story["children_truncated"] is False)
    _, wild, _ = structure(member, "s-story", children_limit=100000)
    check("an oversized limit is clamped, not refused", len(wild["children"]) == 2)

    print("\n-- E. bytes against the full read --")
    for nid in ("s-t1", "s-story", "s-epic"):
        full = len(member.get(f"/api/node/{nid}").content)
        lean = len(member.get(f"/api/node/{nid}/structure").content)
        print(f"   {nid:<8} /api/node {full:>6} B   /structure {lean:>6} B")
        check(f"{nid}: the structure read is smaller than the full read", lean < full, f"{lean} vs {full}")

    print(f"\n{len(failures)} failed" if failures else "\nall checks passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
