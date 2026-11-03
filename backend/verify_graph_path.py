"""
Verification for `GET /api/graph/path` (TASK-1298): how X is connected to Y, as the k
shortest paths over the caller's VISIBLE graph, through the real auth gate, against a
real disposable Postgres via `testdb`.

The graph: `p-a` reaches `p-b` three ways a member can see and one they cannot.
  R1   p-a -contains-> p-m1 <-depends_on- p-b           2 hops, out then in
  R1'  p-a -depends_on-> p-m1 <-depends_on- p-b         2 hops: a second edge, a second path
  R2   p-a -uses-> p-m2 -uses-> p-m3 -uses-> p-b        3 hops
  RH   p-a -uses-> p-h -uses-> p-b                      2 hops, through a node inside a
                                                        PRIVATE project (`p-secret`)
plus `p-z`, visible and connected to nothing.

SECTIONS:
  A. Shape and correctness, as a member: every path starts at `from`, ends at `to`,
     chains hop to hop, repeats no node, is no longer than `hops`, and every hop's
     edge_type and direction agree with the stored edge; shortest first; two calls give
     one answer.
  B. The knobs: `max`, `hops`, `edges`, the clamps, from == to, and "not connected" is a
     200 with no paths rather than a 404.
  C. Visibility, which is the acceptance criterion: the owner's answer contains RH and
     the member's does not, nor any id or edge of the private project; and the member's
     answer is IDENTICAL to the one the graph gives with the private rows physically
     deleted (deletion equivalence, `verify_node_access.py` §C's method), so neither the
     count of paths nor which visible ones were found betrays the hidden route. A hidden
     `from` or `to` 404s exactly like an absent one.
  D. Bytes: printed for each answer, and the path read must be smaller than the
     `/api/graph` neighbourhood a caller would otherwise walk to find the same route.
  E. The search itself against brute-force enumeration on random multigraphs: the
     lengths of the k shortest loopless paths must match exactly.

Run (from a venv with the backend requirements installed):
    python verify_graph_path.py
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

from sqlalchemy import select  # noqa: E402

import app  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthUser, NodeVisibility  # noqa: E402
from models_graph import Edge, Node  # noqa: E402

failures: list[str] = []
OWNER_PW = "Correct-Horse-Battery-Owner-9!"
MEMBER_PW = "Correct-Horse-Battery-Member-9!"
HIDDEN = ("p-secret", "p-h")


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def mk_node(s, nid: str, ntype: str = "capability") -> None:
    s.add(Node(id=nid, type=ntype, name=f"name of {nid}", attributes={}, attention_count=0,
               completion=0.0, origin="atlas"))


def mk_edge(s, src: str, dst: str, etype: str) -> None:
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


def build() -> None:
    with app.session() as s:
        mk_node(s, "p-a", "project")
        mk_node(s, "p-b", "project")
        for nid in ("p-m1", "p-m2", "p-m3", "p-z"):
            mk_node(s, nid)
        mk_node(s, "p-secret", "project")
        mk_node(s, "p-h")
        s.flush()
        mk_edge(s, "p-a", "p-m1", "contains")
        mk_edge(s, "p-a", "p-m1", "depends_on")
        mk_edge(s, "p-b", "p-m1", "depends_on")
        mk_edge(s, "p-a", "p-m2", "uses")
        mk_edge(s, "p-m2", "p-m3", "uses")
        mk_edge(s, "p-m3", "p-b", "uses")
        mk_edge(s, "p-secret", "p-h", "contains")   # p-h is hidden through its parent
        mk_edge(s, "p-a", "p-h", "uses")
        mk_edge(s, "p-h", "p-b", "uses")
        s.add(NodeVisibility(node_id="p-secret", visibility="private"))
        s.commit()


def stored_edges() -> dict[int, tuple[str, str, str]]:
    with app.session() as s:
        return {e.id: (e.src, e.dst, e.edge_type) for e in s.scalars(select(Edge)).all()}


def route(p: dict) -> list[str]:
    """A path as its node ids, start to end."""
    return [p["hops"][0]["from"]] + [h["to"] for h in p["hops"]] if p["hops"] else []


def section_e() -> None:
    """Yen against brute force. The fixture above has three routes, which a wrong
    algorithm could still find; random multigraphs are where a spur search that bans too
    much (a missed path) or too little (a repeated or looping one) shows up. Lengths must
    match the brute-force k shortest exactly; ties may come in either order, so the paths
    themselves are checked for validity and distinctness instead of for position."""
    import random
    print("\n-- E. the k-shortest search against brute force, 300 random multigraphs --")
    rng = random.Random(1298)
    bad: list[str] = []
    for trial in range(300):
        n_nodes, n_edges = rng.randint(2, 8), rng.randint(1, 16)
        names = [f"n{i}" for i in range(n_nodes)]
        edge_list = [(eid, rng.choice(names), rng.choice(names)) for eid in range(n_edges)]
        adj: dict[str, list[tuple[int, str]]] = {}
        for eid, a, b in edge_list:
            adj.setdefault(a, []).append((eid, b))
            adj.setdefault(b, []).append((eid, a))
        for nbrs in adj.values():
            nbrs.sort(key=lambda h: (h[1], h[0]))
        k, hops = rng.randint(1, 10), rng.randint(1, 6)
        src, dst = rng.choice(names), rng.choice(names)

        every: list[tuple[str, ...]] = []

        def dfs(node: str, nodes: tuple[str, ...], edges: tuple[int, ...]) -> None:
            if node == dst:
                every.append(edges)
                return
            if len(edges) == hops:
                return
            for eid, nxt in adj.get(node, ()):
                if nxt not in nodes:
                    dfs(nxt, nodes + (nxt,), edges + (eid,))
        dfs(src, (src,), ())
        want = sorted(len(e) for e in every)[:k]
        got = app._k_shortest_paths(src, dst, adj, k, hops)
        valid = len({e for _, e in got}) == len(got)
        for nodes, edges in got:
            valid &= nodes[0] == src and nodes[-1] == dst and len(set(nodes)) == len(nodes)
            valid &= len(edges) == len(nodes) - 1 <= hops
            for i, eid in enumerate(edges):
                _, a, b = edge_list[eid]
                valid &= {a, b} == {nodes[i], nodes[i + 1]}
        if [len(e) for _, e in got] != want or not valid:
            bad.append(f"trial {trial}: want lengths {want}, got {[len(e) for _, e in got]}, "
                       f"valid={valid}")
    check("every answer is valid, distinct, and as short as brute force says", not bad,
          "; ".join(bad[:3]))


def main() -> None:
    # `startup()` is an @app.on_event handler, so importing app creates no tables, and the
    # users and graph below are written directly rather than over HTTP (verify_node_access.py
    # does the same, for the same reason).
    app.startup()
    mk_user("u-owner", "verify-path-owner", "owner", OWNER_PW)
    mk_user("u-member", "verify-path-member", "member", MEMBER_PW)
    build()
    owner, member = client(), client()
    sign_in(owner, "verify-path-owner", OWNER_PW)
    sign_in(member, "verify-path-member", MEMBER_PW)
    edges_db = stored_edges()
    hidden_edge_ids = {eid for eid, (src, dst, _) in edges_db.items()
                       if src in HIDDEN or dst in HIDDEN}

    def path(c, frm, to, **params):
        r = c.get("/api/graph/path", params={"from": frm, "to": to, **params})
        return r.status_code, r.json(), r.content

    print("\n-- A. shape and correctness, as a member --")
    code, m_all, raw_all = path(member, "p-a", "p-b", max=10)
    check("a visible pair answers 200", code == 200, str(code))
    check("the top-level keys are exactly the documented ones",
          set(m_all) == {"from", "to", "edge_types", "hops_max", "paths", "nodes"}, str(sorted(m_all)))
    paths = m_all["paths"]
    check("the member finds the three visible routes and only those",
          sorted(route(p) for p in paths) == sorted([["p-a", "p-m1", "p-b"], ["p-a", "p-m1", "p-b"],
                                                     ["p-a", "p-m2", "p-m3", "p-b"]]),
          str([route(p) for p in paths]))
    ok_shape = True
    for p in paths:
        r = route(p)
        ok_shape &= r[0] == "p-a" and r[-1] == "p-b" and len(set(r)) == len(r)
        ok_shape &= p["length"] == len(p["hops"]) <= m_all["hops_max"]
        for a, b in zip(p["hops"], p["hops"][1:]):
            ok_shape &= a["to"] == b["from"]
        for h in p["hops"]:
            ok_shape &= set(h) == {"from", "to", "edge_type", "direction", "edge"}
            src, dst, etype = edges_db[h["edge"]]
            ok_shape &= h["edge_type"] == etype
            ok_shape &= ((h["direction"] == "out" and (src, dst) == (h["from"], h["to"]))
                         or (h["direction"] == "in" and (src, dst) == (h["to"], h["from"])))
    check("every path runs from -> to, chains, repeats no node, stays within hops, "
          "and every hop matches its stored edge and direction", ok_shape)
    check("shortest first", [p["length"] for p in paths] == sorted(p["length"] for p in paths),
          str([p["length"] for p in paths]))
    first = paths[0]["hops"]
    check("the shortest route reports one hop out along contains, one hop in against depends_on",
          [(h["edge_type"], h["direction"]) for h in first] == [("contains", "out"), ("depends_on", "in")],
          str(first))
    check("two edges between the same pair are two paths", paths[1]["hops"][0]["edge_type"] == "depends_on"
          and route(paths[1]) == route(paths[0]), str(paths[1]))
    check("nodes carries every id on a path, once, as {type, name}",
          set(m_all["nodes"]) == {"p-a", "p-b", "p-m1", "p-m2", "p-m3"}
          and all(set(v) == {"type", "name"} for v in m_all["nodes"].values()), str(m_all["nodes"]))
    check("one database gives one answer", path(member, "p-a", "p-b", max=10)[2] == raw_all)

    print("\n-- B. the knobs --")
    _, d, _ = path(member, "p-a", "p-b")
    check("max defaults to 3", len(d["paths"]) == 3, str(len(d["paths"])))
    _, d, _ = path(member, "p-a", "p-b", max=1)
    check("max=1 is the shortest path alone", [route(p) for p in d["paths"]] == [["p-a", "p-m1", "p-b"]])
    _, d, _ = path(member, "p-a", "p-b", max=1000)
    check("an oversized max is clamped, not refused", len(d["paths"]) == 3, str(len(d["paths"])))
    _, d, _ = path(member, "p-a", "p-b", max=10, hops=2)
    check("hops=2 drops the 3-hop route", d["hops_max"] == 2
          and all(p["length"] <= 2 for p in d["paths"]) and len(d["paths"]) == 2, str(d["paths"]))
    _, d, _ = path(member, "p-a", "p-b", hops=999)
    check("an oversized hops is clamped to the ceiling", d["hops_max"] == app.PATH_HOPS_CAP,
          str(d["hops_max"]))
    _, d, _ = path(member, "p-a", "p-b", max=10, edges="uses")
    check("edges=uses walks only uses edges", [route(p) for p in d["paths"]] == [["p-a", "p-m2", "p-m3", "p-b"]]
          and d["edge_types"] == ["uses"], str(d))
    _, d, _ = path(member, "p-a", "p-b", edges="contains")
    check("an edge filter that leaves no route is an empty answer", d["paths"] == [], str(d["paths"]))
    code, d, _ = path(member, "p-a", "p-z")
    check("not connected is a 200 with no paths, not a 404", code == 200 and d["paths"] == [],
          f"{code} {d}")
    _, d, _ = path(member, "p-a", "p-a")
    check("from == to is one path of length 0",
          d["paths"] == [{"length": 0, "hops": []}], str(d["paths"]))

    print("\n-- C. visibility: a route through a hidden node never surfaces --")
    _, o_all, _ = path(owner, "p-a", "p-b", max=10)
    check("the owner's answer includes the route through the private project",
          ["p-a", "p-h", "p-b"] in [route(p) for p in o_all["paths"]], str([route(p) for p in o_all["paths"]]))
    check("the member's answer names no hidden node, anywhere in the body",
          not any(h.encode() in raw_all for h in HIDDEN))
    check("...and takes no edge incident to one",
          not any(h["edge"] in hidden_edge_ids for p in paths for h in p["hops"]))
    _, m_uses, raw_uses = path(member, "p-a", "p-b", max=10, edges="uses")
    _, m_three, raw_three = path(member, "p-a", "p-b")
    _, o_three, _ = path(owner, "p-a", "p-b")
    check("the owner's top 3 is displaced by the hidden route; the member's is not short of one",
          ["p-a", "p-h", "p-b"] in [route(p) for p in o_three["paths"]] and len(m_three["paths"]) == 3)

    # A hidden end, and an absent one, for either end of the query.
    for label, frm, to, absent in (("to", "p-a", "p-h", ("p-a", "p-nope")),
                                   ("from", "p-h", "p-a", ("p-nope", "p-a")),
                                   ("to (a private root)", "p-a", "p-secret", ("p-a", "p-nope"))):
        ch, bh, _ = path(member, frm, to)
        ca, ba, _ = path(member, *absent)
        hid = to if label.startswith("to") else frm
        check(f"a hidden `{label}` 404s", ch == 404, str(ch))
        check(f"...identically to an absent id (status, keys, message with the id swapped)",
              ca == 404 and set(bh) == set(ba)
              and bh["detail"].replace(hid, "X") == ba["detail"].replace("p-nope", "X"),
              f"{bh} vs {ba}")
    ch, _, _ = path(owner, "p-a", "p-h")
    check("the owner reaches the hidden node itself", ch == 200, str(ch))

    # THE ORACLE: physically delete the private rows and their edges, then ask the OWNER
    # (unrestricted). The member's earlier answers must be byte-identical: same paths,
    # same order, same edge ids. Anything else means the hidden route still shaped them.
    with app.session() as s:
        for e in s.scalars(select(Edge)).all():
            if e.src in HIDDEN or e.dst in HIDDEN:
                s.delete(e)
        s.execute(NodeVisibility.__table__.delete().where(NodeVisibility.node_id.in_(HIDDEN)))
        for nid in HIDDEN:
            n = s.get(Node, nid)
            if n is not None:
                s.delete(n)
        s.commit()
    for label, params, member_raw in (("max=10", {"max": 10}, raw_all),
                                      ("max=10, edges=uses", {"max": 10, "edges": "uses"}, raw_uses),
                                      ("default max", {}, raw_three)):
        _, _, deleted_raw = path(owner, "p-a", "p-b", **params)
        check(f"deletion equivalence — {label}", deleted_raw == member_raw,
              f"member={member_raw[:200]!r} deleted={deleted_raw[:200]!r}")

    print("\n-- D. bytes --")
    _, _, one = path(member, "p-a", "p-b", max=1)
    print(f"   max=1   {len(one):>6} B")
    print(f"   max=3   {len(raw_three):>6} B")
    print(f"   max=10  {len(raw_all):>6} B   ({sum(p['length'] for p in paths)} hops over "
          f"{len(paths)} paths)")
    hood = member.get("/api/graph", params={"focus": "p-a", "depth": 3}).content
    print(f"   /api/graph focus=p-a depth=3   {len(hood):>6} B")
    check("the path read is smaller than the neighbourhood that contains the same routes",
          len(raw_all) < len(hood), f"{len(raw_all)} vs {len(hood)}")
    per_hop = len(raw_all) / max(1, sum(p["length"] for p in paths))
    check("a hop costs about 100 bytes, nodes included", per_hop < 150, f"{per_hop:.0f} B/hop")

    section_e()

    print(f"\n{len(failures)} failed" if failures else "\nall checks passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
