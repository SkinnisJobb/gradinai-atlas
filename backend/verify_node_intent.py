"""
Verification for ADR 0032 (node intent — durable purpose, inherited downward).

No pytest suite exists in this repo (see verify_object_graph.py's docstring), so
this follows the standalone run-it-and-read-it convention against a real,
disposable Postgres via `testdb` (TASK-1044).

FIVE SECTIONS, proving different things:

  A. THE INHERITANCE RULE, against `app._intents_for` with a hand-built graph:
     own intents in position order, a child inheriting its parent's, a
     grandchild inheriting from both levels NEAREST-FIRST, a diamond (two
     `contains` parents) producing a UNION with no duplicates, slug shadowing
     in both directions (own over inherited, nearer ancestor over further),
     `contains` CYCLE termination, and — the one most likely to regress —
     that inheritance does NOT flow along `uses` or `hosted_on`.

  B. The write API over HTTP through the REAL auth gate: create/patch/delete,
     every validation refusal NAMING its field, the slug 409, and a write with
     no CSRF token being refused. Never a shortcut that only proves the ORM
     path.

  C. `GET /api/intents` (the corpus query the compliance loop is built on —
     owner-side, one row per statement, filterable by kind) and
     `GET /api/node/{id}` carrying `intents.own` / `intents.inherited`.

  D. `DELETE /api/node/{id}` cleaning up that node's intents — the
     `_delete_node_dependents` wiring. Without it a node with an intent
     cannot be deleted at all (bare FK, no ON DELETE CASCADE, by design).

  E. `intent_count` on `/api/graph`, which feeds the map's `◎` glyph after the
     `goal` type's retirement. OWN intents only, never the inherited union —
     every node below an authored one inherits something, so a union-fed glyph
     would light up nearly the whole tree and distinguish nothing.

Run (from a venv with the backend requirements installed):
    python verify_node_intent.py
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

#: TestClient must speak https — `__Host-` cookies REQUIRE Secure.
TEST_ORIGIN = "https://testserver"

from sqlalchemy import select  # noqa: E402

import app  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthUser  # noqa: E402
from models_graph import Edge, Node, NodeIntent  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def mk_node(s, nid: str, ntype: str) -> None:
    s.add(Node(id=nid, type=ntype, name=nid, attributes={}, attention_count=0,
               completion=0.0, origin="atlas"))


def mk_edge(s, src: str, dst: str, etype: str) -> None:
    s.add(Edge(src=src, dst=dst, edge_type=etype, directed=True, origin="atlas"))


def mk_intent(s, node_id: str, slug: str, kind: str = "principle",
              title: str | None = None, position: int = 0) -> None:
    s.add(NodeIntent(node_id=node_id, slug=slug, kind=kind,
                     title=title or f"{node_id}:{slug}",
                     statement=f"statement for {slug} on {node_id}",
                     source="verify_node_intent", position=position))


def _client():
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN)


def _sign_in(client) -> str:
    username, password = "verify-node-intent", "Correct-Horse-Battery-9!"
    with app.session() as s:
        if not s.scalar(select(AuthUser).where(AuthUser.subject == username)):
            s.add(AuthUser(
                id="user-verify-node-intent", subject=username, name="Verify Node Intent",
                role="owner", is_service=False, must_change_password=False,
                password_hash=passwords.hash_password(password),
                created_at=datetime.now(timezone.utc),
            ))
            s.commit()
    page = client.get("/auth/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    client.post("/auth/login",
                data={"username": username, "password": password, "csrf_token": token},
                headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
    return client.get("/api/me").json()["csrf_token"]


# --------------------------------------------------------------------------- #
# A. The inheritance rule
# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. inheritance: union down `contains`, nearest-first, slug shadowing --")
    with app.session() as s:
        # root -> mid -> leaf, plus a diamond (mid2 also contains leaf),
        # a `uses`/`hosted_on` pair that must NOT propagate, and a cycle.
        for nid in ("i-root", "i-mid", "i-mid2", "i-leaf", "i-other", "i-host", "i-plain"):
            mk_node(s, nid, "capability")
        mk_node(s, "i-cyc-a", "capability")
        mk_node(s, "i-cyc-b", "capability")
        s.flush()

        mk_edge(s, "i-root", "i-mid", "contains")
        mk_edge(s, "i-root", "i-mid2", "contains")
        mk_edge(s, "i-root", "i-plain", "contains")   # authored nothing of its own
        mk_edge(s, "i-mid", "i-leaf", "contains")
        mk_edge(s, "i-mid2", "i-leaf", "contains")     # diamond
        mk_edge(s, "i-other", "i-leaf", "uses")        # must not propagate
        mk_edge(s, "i-leaf", "i-host", "hosted_on")    # must not propagate
        mk_edge(s, "i-cyc-a", "i-cyc-b", "contains")
        mk_edge(s, "i-cyc-b", "i-cyc-a", "contains")   # cycle

        # root carries two, in a curated order
        mk_intent(s, "i-root", "dual-audience", "principle", position=0)
        mk_intent(s, "i-root", "no-file-storage", "constraint", position=1)
        # mid overrides the root's `dual-audience` for everything below it
        mk_intent(s, "i-mid", "dual-audience", "principle", title="MID OVERRIDE", position=0)
        # mid2 (the other diamond parent) adds one of its own
        mk_intent(s, "i-mid2", "second-parent", "outcome", position=0)
        # leaf shadows the constraint with its own
        mk_intent(s, "i-leaf", "no-file-storage", "constraint", title="LEAF OWN", position=0)
        mk_intent(s, "i-other", "must-not-leak", "principle", position=0)
        mk_intent(s, "i-host", "must-not-leak-up", "principle", position=0)
        mk_intent(s, "i-cyc-a", "cyc", "principle", position=0)
        s.commit()

        root = app._intents_for(s, "i-root")
        check("own intents come back in position order",
              [i["slug"] for i in root["own"]] == ["dual-audience", "no-file-storage"],
              str([i["slug"] for i in root["own"]]))
        check("a root inherits nothing", root["inherited"] == [])

        mid = app._intents_for(s, "i-mid")
        check("a child inherits its parent's intents",
              "no-file-storage" in [i["slug"] for i in mid["inherited"]])
        check("a node's OWN slug shadows the inherited one of the same name",
              [i["slug"] for i in mid["own"]] == ["dual-audience"]
              and "dual-audience" not in [i["slug"] for i in mid["inherited"]])
        check("an inherited intent carries its OWNER, so the panel can link to it",
              all(i.get("node_id") and i.get("owner_name") for i in mid["inherited"]),
              str(mid["inherited"]))

        leaf = app._intents_for(s, "i-leaf")
        inh = {i["slug"]: i for i in leaf["inherited"]}
        check("a grandchild inherits from every level above it",
              {"dual-audience", "second-parent"} <= set(inh), str(sorted(inh)))
        check("the NEAREST ancestor wins a slug collision",
              inh["dual-audience"]["node_id"] == "i-mid",
              f"got {inh['dual-audience']['node_id']!r} — an override written at a "
              "layer must outrank the root it refines")
        check("a diamond produces a UNION, not duplicates",
              len([i for i in leaf["inherited"] if i["slug"] == "dual-audience"]) == 1)
        check("the node's own slug shadows an ancestor's",
              [i["title"] for i in leaf["own"]] == ["LEAF OWN"]
              and "no-file-storage" not in inh)
        check("a node never inherits from ITSELF",
              all(i["node_id"] != "i-leaf" for i in leaf["inherited"]))
        check("`uses` does NOT propagate intent",
              "must-not-leak" not in inh,
              "Atlas `uses` the seat; the seat's intents are not Atlas's (ADR 0032)")
        check("`hosted_on` does NOT propagate intent", "must-not-leak-up" not in inh)

        cyc = app._intents_for(s, "i-cyc-b")   # would hang if the walk trusted the data
        check("a `contains` cycle TERMINATES instead of hanging the read path",
              [i["slug"] for i in cyc["inherited"]] == ["cyc"],
              str(cyc["inherited"]))


# --------------------------------------------------------------------------- #
# B. The write API, through the real auth gate
# --------------------------------------------------------------------------- #
def section_b(client, csrf: str) -> None:
    print("\n-- B. write API: create / patch / delete, refusals naming their field --")
    with app.session() as s:
        mk_node(s, "i-write", "capability")
        s.commit()

    def post(payload):
        return client.post("/api/node/i-write/intent",
                           json={**payload, "csrf_token": csrf}, headers={"Origin": TEST_ORIGIN})

    r = post({"slug": "first", "kind": "principle", "title": "First",
              "statement": "The first one."})
    check("create returns 201", r.status_code == 201, r.text[:200])
    first_id = r.json().get("id")
    check("create appends at the end of the list", r.json().get("position") == 0,
          str(r.json().get("position")))

    r2 = post({"slug": "second", "kind": "non-goal", "title": "Second",
               "statement": "Do not build this."})
    check("a second create appends after the first", r2.json().get("position") == 1,
          str(r2.json().get("position")))
    second_id = r2.json().get("id")

    r = post({"slug": "bad-kind", "kind": "goal", "title": "X", "statement": "Y"})
    check("an unknown kind is a 400 NAMING the field",
          r.status_code == 400 and r.json().get("field") == "kind", r.text[:200])

    r = post({"slug": "Not_A_Slug", "kind": "principle", "title": "X", "statement": "Y"})
    check("a malformed slug is a 400 naming `slug`",
          r.status_code == 400 and r.json().get("field") == "slug", r.text[:200])

    r = post({"slug": "empty-title", "kind": "principle", "title": "   ", "statement": "Y"})
    check("a blank title is a 400 naming `title`",
          r.status_code == 400 and r.json().get("field") == "title", r.text[:200])

    r = post({"slug": "first", "kind": "principle", "title": "Dup", "statement": "Y"})
    check("a duplicate slug on the same node is a 409, not a 500 from the unique index",
          r.status_code == 409 and r.json().get("error") == "intent_exists", r.text[:200])

    r = client.post("/api/node/i-write/intent",
                    json={"slug": "no-csrf", "kind": "principle", "title": "X",
                          "statement": "Y"},
                    headers={"Origin": TEST_ORIGIN})
    check("a write with no CSRF token is REFUSED by the existing guard",
          r.status_code in (401, 403), f"{r.status_code} {r.text[:120]}")

    r = post({"slug": "nowhere", "kind": "principle", "title": "X", "statement": "Y"})
    r404 = client.post("/api/node/i-does-not-exist/intent",
                       json={"slug": "x", "kind": "principle", "title": "X",
                             "statement": "Y", "csrf_token": csrf},
                       headers={"Origin": TEST_ORIGIN})
    check("an intent on an unknown node is a 404", r404.status_code == 404, r404.text[:120])

    r = client.patch(f"/api/intent/{first_id}",
                     json={"title": "First, edited", "kind": "constraint",
                           "csrf_token": csrf}, headers={"Origin": TEST_ORIGIN})
    check("patch updates title and kind",
          r.status_code == 200 and r.json()["title"] == "First, edited"
          and r.json()["kind"] == "constraint", r.text[:200])

    r = client.patch(f"/api/intent/{second_id}",
                     json={"slug": "first", "csrf_token": csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("patching a slug onto one that already exists on the node is a 409",
          r.status_code == 409, r.text[:200])

    # `title` and `source` are String(200). Over the bound, the value used to reach
    # Postgres and come back as a 500; it is a 400 naming the field now, and the
    # bound itself is accepted (the comparison is perturbed by one character).
    for field in ("source", "title"):
        over, at = "s" * 201, "s" * 200
        extra = {"title": "T", "source": None}
        r = post({"slug": f"long-{field}", "kind": "principle", "statement": "Y",
                  **{**extra, field: over}})
        check(f"create: a 201-char {field} is a 400 naming `{field}`, not a 500",
              r.status_code == 400 and r.json().get("field") == field, r.text[:200])
        with app.session() as s:
            left = s.scalars(select(NodeIntent).where(NodeIntent.node_id == "i-write",
                                                      NodeIntent.slug == f"long-{field}")).all()
        check(f"…and nothing was written", left == [], str(left))
        r = post({"slug": f"long-{field}", "kind": "principle", "statement": "Y",
                  **{**extra, field: at}})
        check(f"create: a 200-char {field} is accepted and stored whole",
              r.status_code == 201 and r.json().get(field) == at, r.text[:200])
        r = client.patch(f"/api/intent/{first_id}", json={field: over, "csrf_token": csrf},
                         headers={"Origin": TEST_ORIGIN})
        check(f"patch: a 201-char {field} is a 400 naming `{field}`",
              r.status_code == 400 and r.json().get("field") == field, r.text[:200])
        r = client.patch(f"/api/intent/{first_id}", json={field: at, "csrf_token": csrf},
                         headers={"Origin": TEST_ORIGIN})
        check(f"patch: a 200-char {field} is accepted",
              r.status_code == 200 and r.json().get(field) == at, r.text[:200])

    r = client.request("DELETE", f"/api/intent/{second_id}",
                       json={"csrf_token": csrf}, headers={"Origin": TEST_ORIGIN})
    check("delete returns 200", r.status_code == 200, r.text[:200])

    with app.session() as s:
        rows = s.scalars(select(NodeIntent).where(NodeIntent.node_id == "i-write")
                         .order_by(NodeIntent.position)).all()
        check("positions stay DENSE 0..n-1 after a delete",
              [r.position for r in rows] == list(range(len(rows))),
              str([r.position for r in rows]))


# --------------------------------------------------------------------------- #
# C. The read surfaces
# --------------------------------------------------------------------------- #
def section_c(client) -> None:
    print("\n-- C. GET /api/intents (the compliance query) and GET /api/node/{id} --")
    r = client.get("/api/intents")
    check("the corpus endpoint answers", r.status_code == 200, r.text[:200])
    body = r.json()
    slugs = [(i["node_id"], i["slug"]) for i in body["intents"]]
    check("it is OWNER-side — each statement appears ONCE, at the node responsible",
          len(slugs) == len(set(slugs)) and ("i-leaf", "no-file-storage") in slugs,
          str(len(slugs)))
    check("an inherited copy is NOT repeated per descendant",
          len([1 for n, sl in slugs if sl == "dual-audience"]) == 2,
          "i-root and i-mid own one each; i-leaf inherits and must not appear")
    check("it advertises the kind vocabulary",
          body.get("kinds") == ["constraint", "principle", "non-goal", "outcome"],
          str(body.get("kinds")))

    r = client.get("/api/intents?kind=non-goal")
    check("filtering by kind works",
          r.status_code == 200 and all(i["kind"] == "non-goal" for i in r.json()["intents"]),
          r.text[:200])
    r = client.get("/api/intents?kind=nonsense")
    check("an unknown kind filter is a 400 naming the field",
          r.status_code == 400 and r.json().get("field") == "kind", r.text[:160])

    r = client.get("/api/node/i-leaf")
    check("GET /api/node/{id} carries the intents", r.status_code == 200, r.text[:160])
    got = r.json().get("intents") or {}
    check("…split into own and inherited",
          [i["slug"] for i in got.get("own", [])] == ["no-file-storage"]
          and "dual-audience" in [i["slug"] for i in got.get("inherited", [])],
          str(got)[:300])


# --------------------------------------------------------------------------- #
# D. Node deletion
# --------------------------------------------------------------------------- #
def section_d(client, csrf: str) -> None:
    print("\n-- D. deleting a node cleans up its intents (bare FK, cleanup in code) --")
    with app.session() as s:
        mk_node(s, "i-doomed", "capability")
        s.flush()
        mk_intent(s, "i-doomed", "goes-away", "outcome", position=0)
        s.commit()

    r = client.request("DELETE", "/api/node/i-doomed",
                       json={"csrf_token": csrf}, headers={"Origin": TEST_ORIGIN})
    check("a node carrying an intent can be deleted at all",
          r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    with app.session() as s:
        left = s.scalars(select(NodeIntent).where(NodeIntent.node_id == "i-doomed")).all()
        check("…and its intent rows go with it", left == [], str(left))


# --------------------------------------------------------------------------- #
# E. The map glyph's data (ADR 0032 -- goal retirement)
# --------------------------------------------------------------------------- #
def section_e(client) -> None:
    print("\n-- E. /api/graph carries own-intent counts for the map glyph --")
    r = client.get("/api/graph?focus=i-leaf&depth=3&edges=contains")
    check("the graph neighbourhood answers", r.status_code == 200, r.text[:160])
    by_id = {n["id"]: n for n in r.json().get("nodes", [])}

    check("every node in the neighbourhood carries intent_count",
          bool(by_id) and all("intent_count" in n for n in by_id.values()),
          str([k for k, v in by_id.items() if "intent_count" not in v]))
    check("a node that OWNS intents reports how many",
          by_id.get("i-root", {}).get("intent_count") == 2,
          str(by_id.get("i-root", {}).get("intent_count")))
    check("the count is OWN only, never the inherited union",
          by_id.get("i-leaf", {}).get("intent_count") == 1,
          "i-leaf owns 1 and inherits more; a glyph fed by the union would light up "
          "nearly every node and so distinguish nothing")
    check("a node that authored NOTHING reports 0, not null or absent",
          by_id.get("i-plain", {}).get("intent_count") == 0,
          f"i-plain inherits 2 from i-root and owns none; got "
          f"{by_id.get('i-plain', {}).get('intent_count')!r}")

    # The single-node route carries the intents THEMSELVES, so a count there
    # would be redundant -- same rule as include_prose: emit it where it is used.
    r = client.get("/api/node/i-leaf")
    check("a single-node fetch does NOT carry intent_count (it carries the intents)",
          "intent_count" not in r.json(), str(sorted(r.json().keys()))[:200])

    # The retired type must be gone from the seeded vocabulary, not merely unused.
    import node_types
    check("`goal` is no longer a seeded bridge type",
          "goal" not in node_types.BRIDGE_TYPES, str(node_types.BRIDGE_TYPES))


def main() -> None:
    print("=" * 78)
    print("Atlas node-intent verification (ADR 0032)")
    print(f"DB:      {os.environ['ATLAS_DB_URL']}")
    print("=" * 78)

    app.startup()  # real startup: create_all + schema reconcile + node_type seed
    client = _client()
    csrf = _sign_in(client)

    try:
        section_a()
        section_b(client, csrf)
        section_c(client)
        section_d(client, csrf)
        section_e(client)
    finally:
        DB.stop()

    print("\n" + "=" * 78)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
