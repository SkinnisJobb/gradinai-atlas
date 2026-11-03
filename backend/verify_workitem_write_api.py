"""
Verification for TASK-1048: create and update a workitem over HTTP.

Same shape as verify_status_lifecycle.py — no pytest suite exists in this repo, so
this is a standalone run-it-and-read-it script against a real, disposable Postgres
via `testdb` (TASK-1044), populated from `testseed.py` (curated fixture) + world
manifest, and driving the REAL API (POST /api/workitem, PATCH /api/workitem/{id})
through the REAL auth gate (cookie session + synchronizer-token CSRF, exactly like
verify_status_lifecycle.py's section 5 — never a shortcut that only proves the ORM
path).

Covers, per the task's own "Verification" list:
  1. create returns a minted id (a `task`, via models_graph.allocate_task_id)
  2. create is atomic — a request that fails partway leaves NO partial node
  3. a second `works_on` binding gets a 409 NAMING the existing one (ADR 0028 §1),
     including tolerance of the real, pre-existing TASK-850 double-binding
  4. a workitem with NO binding is created successfully (the 54%-unbound case)
  5. PATCH round-trips body and acceptance, including ORDER
  6. the origin behaviour this route chose (unconditional flip to atlas + an
     honest `detached_from_git` flag — same resolution as edit_node/TASK-1043)

Plus the surrounding contract: id-minting refusals, invalid `attributes` naming the
field, family-rule enforcement for both the work-hierarchy edge and the binding edge,
`depends_on`'s single home (the attributes mirror is stripped, never round-tripped),
parent re-parenting and clearing, binding clearing, and that the SAME single-binding
rule reaches the low-level `POST /api/edge` construction kit, not only this route.

Section 11 covers TASK-1064 ("recording a result stops rewriting the spec"): create
and PATCH both storing/updating a `workitem_result` row through the `result` field
WITHOUT EVER touching `node.body` — the specific defect that lost TASK-1051/1053/1055's
specs before `workitem_result` (TASK-1063) existed. Every `result`-touching check below
asserts `body` byte-for-byte against what was sent/already stored, not merely that the
request succeeded; the `## Outcome` case is built explicitly (3 of 221 result-bearing
corpus files use it, not `## Result`) to prove PATCH preserves an existing heading
rather than rewriting it to the synthesised default.

Section 8b covers TASK-1378: a PATCH re-parent is refused with a 400 naming the
rule (`parent_order`, `parent_is_self`, `parent_cycle`) when it breaks the work
hierarchy order (epic > story > task), names the item itself, or names one of its
descendants — nothing moves and no graph_event is written — and a valid move,
including one that skips a rung, is still 200. The same order holds at create
(400 `parent_order`, nothing written, `task_id_seq` not advanced), and a PATCH
`type` change is judged against the existing parent (`parent_order`, field
`type`) and the existing work children (`child_order`).

Section 12 covers TASK-1120 ("`owner` is retired on workitems"): both write routes
refuse an `owner` key with a 400 naming the field — on PRESENCE, so an explicit
`null` is refused too — the create path no longer writes the column, a refused
PATCH is a no-op on the rest of the same request, and `PATCH /api/node` still
accepts `owner` because on an object-family node it is the right field.

Run (from a venv with the backend requirements installed):
    python verify_workitem_write_api.py
"""
from __future__ import annotations

import base64
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import select, text


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb  # noqa: E402

# Must be resolved BEFORE `import app` — app.py reads ATLAS_DB_URL at module
# import time. No SQLite fallback exists any more — see testdb's docstring.
try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:
    print(f"FATAL: {e}")
    sys.exit(1)
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

import app  # noqa: E402
from models_auth import AuthUser  # noqa: E402
from models_graph import AcceptanceCriterion, Edge, GraphEvent, Node, WorkitemResult, h2_indices, reassemble  # noqa: E402
import passwords  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def _client():
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN)


def _sign_in(client) -> str:
    username, password = "verify-workitem-write", "Correct-Horse-Battery-9!"
    with app.session() as s:
        if not s.scalar(select(AuthUser).where(AuthUser.subject == username)):
            s.add(AuthUser(
                id="user-verify-workitem-write", subject=username, name="Verify Workitem Write",
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


def main() -> None:
    print("=" * 78)
    print("Atlas workitem-write-API verification (TASK-1048)")
    print(f"DB:      {os.environ['ATLAS_DB_URL']}")
    print("Fixture: backend/testseed.py (curated)")
    print("=" * 78)

    app.startup()  # real startup path: create_all, schema reconcile, node_type registry seed
    # CHANGED (TASK-1058): boot no longer reads ATLAS_BACKLOG_DIR/
    # ATLAS_WORLD_MANIFEST.
    # CHANGED AGAIN (testseed): this used to import the 309 markdown files
    # from a hardcoded Windows path in a second repo. The corpus was never
    # the subject here -- it was one line of convenience that left a
    # populated database behind it, and it pinned this script to one
    # workstation, to a repo the server cannot clone, and to a corpus
    # frozen at TASK-1062. `testseed` is 27 nodes that describe themselves.
    import testseed
    with app.session() as s:
        testseed.build(s)
    with app.session() as s:
        testseed.self_check(s)
    # TASK-1059: `import_world` is deleted along with `world_manifest.yaml`.
    # The world it built was only ever fixture scaffolding here -- `testseed`
    # carries three world nodes (a system containing a service containing a
    # component), which is what these checks actually need.
    # `_seed_task_id_sequence` ran inside the FIRST app.startup() above,
    # against an empty node table (nothing had been imported yet) — it left
    # `task_id_seq` starting at 1, which now COLLIDES with the real TASK-001
    # this import just wrote. Run startup() again (ATLAS_RESEED must be OFF,
    # or it would wipe the import back out) so the sequence re-seeds against
    # the real max id, same fix verify_task_id_allocation.py already proves.
    os.environ["ATLAS_RESEED"] = ""
    app.startup()

    client = _client()
    csrf = _sign_in(client)
    check("signed in and hold a CSRF token", bool(csrf))
    H = {"Origin": TEST_ORIGIN}

    def post_workitem(payload, **kw):
        return client.post("/api/workitem", json=dict(payload, csrf_token=csrf), headers=H, **kw)

    def patch_workitem(node_id, payload, **kw):
        return client.patch(f"/api/workitem/{node_id}", json=dict(payload, csrf_token=csrf),
                            headers=H, **kw)

    # -- 0. auth gate parity (brief — verify_object_graph.py owns the full matrix) -- #
    print("\n-- 0. auth gate --")
    anon = _client()
    r = anon.post("/api/workitem", json={"type": "task", "name": "x"})
    check("unauthenticated create -> 401", r.status_code == 401, f"got {r.status_code}")
    r = client.post("/api/workitem", json={"type": "task", "name": "x"}, headers=H)
    check("session create without csrf_token -> 403", r.status_code == 403, f"got {r.status_code}")

    # -- 1. create returns a minted id ------------------------------------------- #
    print("\n-- 1. create returns a minted id --")
    with app.session() as s:
        max_before = max(
            (int(n.id.split("-")[1]) for n in s.scalars(select(Node)).all()
             if n.id.startswith("TASK-") and n.id.split("-")[1].isdigit()),
            default=0,
        )
    r = post_workitem({"type": "task", "name": "verify-write-api plain task"})
    check("create task -> 201", r.status_code == 201, f"got {r.status_code} {r.text[:200]}")
    body1 = r.json()
    minted_id = body1.get("id")
    check("response carries a minted TASK- id", bool(minted_id) and minted_id.startswith("TASK-"),
          str(minted_id))
    check("the minted id exceeds every pre-existing TASK- id",
          minted_id is not None and int(minted_id.split("-")[1]) > max_before,
          f"minted={minted_id} max_before=TASK-{max_before:03d}")
    check("the response no longer carries `durable` (TASK-1058)",
          "durable" not in body1, str(body1)[:200])
    check("a fresh create is never reported as detached (nothing to detach from)",
          "detached_from_git" not in body1, str(body1.get("detached_from_git")))

    # A caller-supplied task id is refused, not silently honoured.
    r = post_workitem({"type": "task", "id": "TASK-9999", "name": "should be refused"})
    check("supplying an id for a task -> 400 id_is_minted",
          r.status_code == 400 and r.json().get("error") == "id_is_minted",
          f"got {r.status_code} {r.text[:150]}")
    with app.session() as s:
        check("...and TASK-9999 was NOT created", s.get(Node, "TASK-9999") is None)

    # A story/epic requires a caller-supplied slug id.
    r = post_workitem({"type": "story", "name": "no id supplied"})
    check("no id for a story -> 400 id_required",
          r.status_code == 400 and r.json().get("error") == "id_required",
          f"got {r.status_code} {r.text[:150]}")
    r = post_workitem({"type": "story", "id": "story-verify-write-api", "name": "a real story"})
    check("story with a caller-supplied slug id -> 201", r.status_code == 201,
          f"got {r.status_code} {r.text[:200]}")
    check("...and the id is exactly the one supplied", r.json().get("id") == "story-verify-write-api")

    # An unknown type is refused before anything touches the DB.
    r = post_workitem({"type": "goal", "name": "not a workitem type"})
    check("type='goal' (not epic/story/task) -> 400 invalid_type",
          r.status_code == 400 and r.json().get("error") == "invalid_type",
          f"got {r.status_code} {r.text[:150]}")

    # -- 2. create is atomic ------------------------------------------------------ #
    print("\n-- 2. create is atomic — a failure leaves NO partial node --")
    r = post_workitem({"type": "task", "name": "doomed by a bad depends_on",
                       "depends_on": ["TASK-DOES-NOT-EXIST"]})
    check("create with an unknown depends_on id -> 404",
          r.status_code == 404 and r.json().get("error") == "unknown_depends_on",
          f"got {r.status_code} {r.text[:150]}")
    with app.session() as s:
        leaked = s.scalars(select(Node).where(Node.name == "doomed by a bad depends_on")).all()
        check("...and NO node with that name exists anywhere (the id was minted, then discarded)",
              len(leaked) == 0, f"{len(leaked)} leaked row(s)")

    r = post_workitem({"type": "task", "name": "doomed by a bad parent", "parent": "no-such-node"})
    check("create with an unknown parent id -> 404",
          r.status_code == 404 and r.json().get("error") == "unknown_parent",
          f"got {r.status_code} {r.text[:150]}")
    with app.session() as s:
        leaked = s.scalars(select(Node).where(Node.name == "doomed by a bad parent")).all()
        check("...and NO node with that name exists either", len(leaked) == 0, f"{len(leaked)} leaked")

    # Family mismatch discovered only AFTER the node row is flushed (the family
    # check needs the new node's own type, so it necessarily runs post-insert) —
    # the sharpest atomicity case: prove the ALREADY-FLUSHED row does not survive.
    r = post_workitem({"type": "task", "name": "doomed by a bad parent family", "parent": "seed-world-service"})
    check("create with an OBJECT-family parent -> 409 family_mismatch (work hierarchy is workitem-only)",
          r.status_code == 409 and r.json().get("error") == "family_mismatch",
          f"got {r.status_code} {r.text[:200]}")
    with app.session() as s:
        leaked = s.scalars(select(Node).where(Node.name == "doomed by a bad parent family")).all()
        check("...and the flushed node did NOT survive the rollback (real atomicity, not just "
              "pre-validation)", len(leaked) == 0, f"{len(leaked)} leaked row(s)")

    # A WORKITEM-family id as a binding target. Any task will do; it used to
    # name TASK-1010, a real ticket, for no reason beyond it existing.
    r = post_workitem({"type": "task", "name": "doomed by a bad binding family",
                       "binding": testseed.SEED.done_leaf})
    check("create with a WORKITEM-family binding target -> 409 family_mismatch (works_on is object-only)",
          r.status_code == 409 and r.json().get("error") == "family_mismatch",
          f"got {r.status_code} {r.text[:200]}")
    with app.session() as s:
        leaked = s.scalars(select(Node).where(Node.name == "doomed by a bad binding family")).all()
        check("...and that node did not survive either", len(leaked) == 0, f"{len(leaked)} leaked")

    r = post_workitem({"type": "task", "name": "doomed by bad attributes",
                       "attributes": {"status": 12345}})
    check("create with a schema-invalid attributes value -> 400, naming the field",
          r.status_code == 400 and r.json().get("field") == "status",
          f"got {r.status_code} {r.text[:200]}")
    with app.session() as s:
        leaked = s.scalars(select(Node).where(Node.name == "doomed by bad attributes")).all()
        check("...and no partial node was created", len(leaked) == 0, f"{len(leaked)} leaked")

    # -- 3. a second works_on binding is refused, naming the existing one -------- #
    print("\n-- 3. AT MOST ONE works_on binding (ADR 0028 §1) --")
    # The doubly-bound node used to be TASK-850, a real ticket whose two
    # bindings were an accident of history — so this section asserted the
    # accident, and would have gone red the moment anyone tidied it. The fixture
    # carries the violation deliberately now (testseed CASES:
    # "double-binding-violation"), which is the same test with a stable subject.
    DOUBLE = testseed.SEED.doubly_bound_leaf
    EXPECTED = sorted([testseed.SEED.world_service, testseed.SEED.world_component])
    with app.session() as s:
        pre_bindings = sorted(e.dst for e in s.scalars(
            select(Edge).where(Edge.src == DOUBLE, Edge.edge_type == "works_on")).all())
    check(f"{DOUBLE} (the seeded pre-existing violation) already has 2 works_on bindings",
          pre_bindings == EXPECTED, str(pre_bindings))

    r = patch_workitem(DOUBLE, {"binding": testseed.SEED.world_system})
    check(f"PATCHing a THIRD binding onto the already-doubly-bound {DOUBLE} -> 409, "
          "tolerating the violation without making it worse",
          r.status_code == 409 and r.json().get("error") == "binding_exists",
          f"got {r.status_code} {r.text[:200]}")
    err_double = r.json()
    check("...the 409 NAMES an existing binding (one of the two — deterministic, lowest edge id)",
          err_double.get("existing_binding") in EXPECTED, str(err_double))
    with app.session() as s:
        post_bindings = sorted(e.dst for e in s.scalars(
            select(Edge).where(Edge.src == DOUBLE, Edge.edge_type == "works_on")).all())
    check(f"...and {DOUBLE}'s binding set is UNCHANGED — still exactly the pre-existing two",
          post_bindings == EXPECTED, str(post_bindings))

    # An ordinary node: bind once, then a second bind attempt is refused.
    r = post_workitem({"type": "task", "name": "single-binding test", "binding": "seed-world-service"})
    check("create with exactly one binding -> 201", r.status_code == 201, f"got {r.status_code}")
    bound_id = r.json()["id"]
    check("response reports the binding", r.json().get("binding") == "seed-world-service", str(r.json()))

    r = patch_workitem(bound_id, {"binding": "seed-world-component"})
    check("PATCHing a SECOND (different) binding onto a freshly single-bound node -> 409",
          r.status_code == 409 and r.json().get("error") == "binding_exists",
          f"got {r.status_code} {r.text[:200]}")
    check("...naming the FIRST (only) existing binding",
          r.json().get("existing_binding") == "seed-world-service", str(r.json()))

    # The same rule reaches the low-level construction kit (POST /api/edge), not
    # only this route — TASK-1048's own framing: "not possible to create over HTTP
    # a shape the ingest refuses".
    r = client.post("/api/edge", json={"src": bound_id, "dst": "seed-world-component",
                                       "edge_type": "works_on", "csrf_token": csrf}, headers=H)
    check("POST /api/edge directly for a SECOND works_on -> 409 (same rule, same route)",
          r.status_code == 409 and r.json().get("error") == "binding_exists",
          f"got {r.status_code} {r.text[:200]}")

    # Clearing then rebinding works.
    r = patch_workitem(bound_id, {"binding": None})
    check("PATCH binding=null clears it", r.status_code == 200 and r.json().get("binding") is None,
          f"got {r.status_code} {r.json().get('binding')}")
    with app.session() as s:
        remaining = s.scalars(
            select(Edge).where(Edge.src == bound_id, Edge.edge_type == "works_on")).all()
        check("...the works_on edge is actually gone", len(remaining) == 0, len(remaining))
    r = patch_workitem(bound_id, {"binding": "seed-world-component"})
    check("...and rebinding after the clear succeeds", r.status_code == 200 and
          r.json().get("binding") == "seed-world-component", f"got {r.status_code} {r.json()}")

    # -- 4. a workitem with NO binding is created successfully ------------------- #
    print("\n-- 4. a workitem with NO binding is created successfully (the 54%-unbound case) --")
    r = post_workitem({"type": "task", "name": "unbound workitem, on purpose"})
    check("create with no binding at all -> 201", r.status_code == 201, f"got {r.status_code}")
    check("response reports binding: null", r.json().get("binding") is None, str(r.json().get("binding")))

    # -- 5. PATCH round-trips body and acceptance, including ORDER --------------- #
    print("\n-- 5. PATCH round-trips body and acceptance, including ORDER --")
    r = post_workitem({
        "type": "task", "name": "prose and acceptance test",
        "body": "# A Title\n\nOriginal first paragraph.\n\nOriginal detail.",
        "acceptance": ["first", "second", "third"],
    })
    check("create with body + acceptance -> 201", r.status_code == 201, f"got {r.status_code} {r.text[:200]}")
    created = r.json()
    check("response echoes the body verbatim", created.get("body") ==
          "# A Title\n\nOriginal first paragraph.\n\nOriginal detail.", str(created.get("body")))
    check("response echoes acceptance IN ORDER", created.get("acceptance") == ["first", "second", "third"],
          str(created.get("acceptance")))
    pat_id = created["id"]
    with app.session() as s:
        rows = s.scalars(select(AcceptanceCriterion).where(AcceptanceCriterion.node_id == pat_id)
                         .order_by(AcceptanceCriterion.position)).all()
        check("acceptance_criterion rows persisted with position 0,1,2",
              [rr.position for rr in rows] == [0, 1, 2], [rr.position for rr in rows])

    r = patch_workitem(pat_id, {
        "body": "# New Title\n\nReplaced first paragraph.\n\nReplaced detail.",
        "acceptance": ["only one now"],
    })
    check("PATCH body + acceptance -> 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    patched = r.json()
    check("PATCHed body round-trips verbatim", patched.get("body") ==
          "# New Title\n\nReplaced first paragraph.\n\nReplaced detail.", str(patched.get("body")))
    check("PATCHed acceptance REPLACES the old list, not appends",
          patched.get("acceptance") == ["only one now"], str(patched.get("acceptance")))
    with app.session() as s:
        rows = s.scalars(select(AcceptanceCriterion).where(AcceptanceCriterion.node_id == pat_id)).all()
        check("old acceptance rows are GONE, exactly one remains", len(rows) == 1, len(rows))

    # Re-order via PATCH — order must survive a full replace with the same texts
    # in a different sequence.
    r = patch_workitem(pat_id, {"acceptance": ["z-last", "a-first", "m-middle"]})
    check("PATCH acceptance with a specific order -> 200", r.status_code == 200, f"got {r.status_code}")
    check("the ORDER supplied is exactly the order returned",
          r.json().get("acceptance") == ["z-last", "a-first", "m-middle"], str(r.json().get("acceptance")))

    # A PATCH that omits body/acceptance entirely must not touch either.
    r = patch_workitem(pat_id, {"summary": "someone-else"})
    check("PATCH omitting body/acceptance -> 200", r.status_code == 200, f"got {r.status_code}")
    check("...body is UNCHANGED (exclude_unset — omitted fields are never touched)",
          r.json().get("body") == "# New Title\n\nReplaced first paragraph.\n\nReplaced detail.",
          str(r.json().get("body")))
    check("...acceptance is UNCHANGED too",
          r.json().get("acceptance") == ["z-last", "a-first", "m-middle"], str(r.json().get("acceptance")))

    # -- 5b. TASK-1101: omitted / [] / null must mean the SAME thing for all three
    # list-shaped fields. `acceptance` used to test `is not None` after the pop,
    # so an explicit null was indistinguishable from omission and silently did
    # nothing — while `binding: null` and `depends_on: null` both cleared. The
    # three are checked together, in the same shape, so a future divergence
    # between them fails here rather than being discovered by a caller.
    print("")
    print("-- 5b. omitted / [] / null across acceptance, depends_on, binding (TASK-1101) --")

    dep_a = post_workitem({"type": "task", "name": "dep target A"}).json()["id"]
    tri = post_workitem({
        "type": "task", "name": "tri-field null semantics",
        "acceptance": ["keep me"], "depends_on": [dep_a], "binding": "seed-world-service",
    }).json()
    tri_id = tri["id"]
    check("setup: created with one acceptance, one depends_on, one binding",
          tri.get("acceptance") == ["keep me"] and tri.get("depends_on") == [dep_a]
          and tri.get("binding") == "seed-world-service", str(tri))

    # OMITTED — nothing may change.
    r = patch_workitem(tri_id, {"summary": "unchanged-probe"})
    j = r.json()
    check("omitted: acceptance untouched", j.get("acceptance") == ["keep me"], str(j.get("acceptance")))
    check("omitted: depends_on untouched", j.get("depends_on") == [dep_a], str(j.get("depends_on")))
    check("omitted: binding untouched", j.get("binding") == "seed-world-service", str(j.get("binding")))

    # EXPLICIT NULL — all three clear. This is the case TASK-1101 fixed.
    r = patch_workitem(tri_id, {"acceptance": None, "depends_on": None, "binding": None})
    check("null on all three -> 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    j = r.json()
    check("null CLEARS acceptance (was a silent no-op before TASK-1101)",
          j.get("acceptance") == [], str(j.get("acceptance")))
    check("null CLEARS depends_on", j.get("depends_on") == [], str(j.get("depends_on")))
    check("null CLEARS binding", not j.get("binding"), str(j.get("binding")))
    with app.session() as s:
        rows = s.scalars(select(AcceptanceCriterion)
                         .where(AcceptanceCriterion.node_id == tri_id)).all()
        check("...and the acceptance_criterion rows are actually gone from the table",
              len(rows) == 0, len(rows))

    # EMPTY LIST — same as null, and must not error.
    r = patch_workitem(tri_id, {"acceptance": ["re-added"]})
    check("re-add acceptance for the [] case -> 200", r.status_code == 200, f"got {r.status_code}")
    r = patch_workitem(tri_id, {"acceptance": [], "depends_on": []})
    check("[] on acceptance/depends_on -> 200", r.status_code == 200, f"got {r.status_code}")
    j = r.json()
    check("[] clears acceptance, exactly as null does", j.get("acceptance") == [], str(j.get("acceptance")))
    check("[] clears depends_on, exactly as null does", j.get("depends_on") == [], str(j.get("depends_on")))

    # -- 6. origin behaviour: fresh atlas node vs. a real git-origin PATCH ------- #
    print("\n-- 6. origin behaviour (the TASK-1048 'Traps' decision) --")
    check("a fresh create is atlas-origin with no detach flag (nothing to detach)",
          "detached_from_git" not in created)
    with app.session() as s:
        n = s.get(Node, pat_id)
        check("...and the row itself is origin='atlas'", n is not None and n.origin == "atlas",
              str(n.origin if n else None))

    # A synthetic git-origin row, not a real one from the corpus (TASK-1058):
    # every row the fixture writes is origin='atlas'
    # now — there is no boot-time reconciler left to have produced a REAL
    # git-origin task, so this test builds the one fixture it needs directly,
    # the same way verify_workitem_record.py's isolated-schema checks do.
    # `origin='git'` still occurs on real, historical rows (measured live:
    # edge is 797 git / 10 atlas — see models_graph.Node.origin's docstring),
    # so this is a real, reachable state, just not one either importer
    # produces any more.
    with app.session() as s:
        s.add(Node(id="TASK-VERIFY-GITORIGIN", type="task", name="synthetic git-origin fixture",
                    owner="Alex", attributes={}, origin="git", source="synthetic/fixture.md"))
        s.commit()
    with app.session() as s:
        real_task = s.get(Node, "TASK-VERIFY-GITORIGIN")
        real_id, was_origin = real_task.id, real_task.origin
    print(f"     git-origin target: {real_id} (origin={was_origin!r})")
    r = patch_workitem(real_id, {"summary": "verify-workitem-write"})
    check("PATCHing a REAL, git-origin backlog task through this route -> 200",
          r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    detach_body = r.json()
    check("...the response is HONEST: detached_from_git is true (same resolution as edit_node)",
          detach_body.get("detached_from_git") is True, str(detach_body))
    check("...and carries the same explanatory note text edit_node uses",
          "detaches it from that file" in (detach_body.get("note") or ""), str(detach_body.get("note")))
    with app.session() as s:
        n = s.get(Node, real_id)
        check("...the row itself is now origin='atlas' (this IS the chosen, documented resolution — "
              "not a silent revert)", n is not None and n.origin == "atlas", str(n.origin if n else None))

    # -- 7. depends_on: single home, diff-synced, mirror stripped ---------------- #
    print("\n-- 7. depends_on has exactly one home (the edge table) --")
    r = post_workitem({"type": "task", "name": "dep target A"})
    dep_a = r.json()["id"]
    r = post_workitem({"type": "task", "name": "dep target B"})
    dep_b = r.json()["id"]
    r = post_workitem({"type": "task", "name": "dependent node", "depends_on": [dep_a, dep_b],
                       "attributes": {"depends_on": ["a-stale-mirror-value"], "links": {"x": 1}}})
    check("create with depends_on + an attributes.depends_on mirror -> 201",
          r.status_code == 201, f"got {r.status_code} {r.text[:200]}")
    dep_node = r.json()
    check("response's depends_on is the real edge-backed set",
          sorted(dep_node.get("depends_on") or []) == sorted([dep_a, dep_b]), str(dep_node.get("depends_on")))
    check("the attributes.depends_on MIRROR was stripped, not stored — edges are the ONLY home",
          "depends_on" not in (dep_node.get("attributes") or {}), str(dep_node.get("attributes")))
    check("...other attributes keys survive untouched (only depends_on is stripped)",
          (dep_node.get("attributes") or {}).get("links") == {"x": 1}, str(dep_node.get("attributes")))
    dep_id = dep_node["id"]
    with app.session() as s:
        edges = sorted(e.dst for e in s.scalars(
            select(Edge).where(Edge.src == dep_id, Edge.edge_type == "depends_on")).all())
        check("depends_on edges persisted exactly as requested", edges == sorted([dep_a, dep_b]), edges)

    # PATCH diff-syncs: drop dep_a, add a third.
    r = post_workitem({"type": "task", "name": "dep target C"})
    dep_c = r.json()["id"]
    r = patch_workitem(dep_id, {"depends_on": [dep_b, dep_c]})
    check("PATCH depends_on diff-syncs (drop A, keep B, add C) -> 200", r.status_code == 200,
          f"got {r.status_code}")
    check("response reflects the new set exactly",
          sorted(r.json().get("depends_on") or []) == sorted([dep_b, dep_c]), str(r.json().get("depends_on")))
    with app.session() as s:
        edges = sorted(e.dst for e in s.scalars(
            select(Edge).where(Edge.src == dep_id, Edge.edge_type == "depends_on")).all())
        check("...and the edge table matches — old edge (A) is really gone, not just hidden",
              edges == sorted([dep_b, dep_c]), edges)

    r = patch_workitem(dep_id, {"depends_on": []})
    check("PATCH depends_on=[] clears every dependency", r.status_code == 200 and
          r.json().get("depends_on") == [], f"got {r.status_code} {r.json().get('depends_on')}")

    r = post_workitem({"type": "task", "name": "doomed by unknown depends_on target",
                       "depends_on": ["nope-does-not-exist"]})
    check("depends_on naming a nonexistent id -> 404 unknown_depends_on",
          r.status_code == 404 and r.json().get("error") == "unknown_depends_on",
          f"got {r.status_code} {r.text[:150]}")

    # -- 8. parent: family rule, set, replace, clear ------------------------------ #
    print("\n-- 8. parent (work-hierarchy edge, WORK_HIERARCHY_EDGE_TYPE) --")
    r = post_workitem({"type": "story", "id": "story-verify-parent-a", "name": "parent A"})
    check("parent-candidate story A created", r.status_code == 201, f"got {r.status_code}")
    r = post_workitem({"type": "story", "id": "story-verify-parent-b", "name": "parent B"})
    check("parent-candidate story B created", r.status_code == 201, f"got {r.status_code}")

    r = post_workitem({"type": "task", "name": "child of A", "parent": "story-verify-parent-a"})
    check("create with a valid workitem parent -> 201", r.status_code == 201, f"got {r.status_code}")
    child = r.json()
    check("response reports the parent", child.get("parent") == "story-verify-parent-a", str(child))
    child_id = child["id"]
    with app.session() as s:
        e = s.scalar(select(Edge).where(Edge.dst == child_id, Edge.edge_type == app.WORK_HIERARCHY_EDGE_TYPE))
        check(f"a real {app.WORK_HIERARCHY_EDGE_TYPE!r} edge (parent -> child) exists",
              e is not None and e.src == "story-verify-parent-a")

    # Re-parent via PATCH: A -> B.
    r = patch_workitem(child_id, {"parent": "story-verify-parent-b"})
    check("PATCH parent re-parents (A -> B) -> 200", r.status_code == 200, f"got {r.status_code}")
    check("response reports the NEW parent", r.json().get("parent") == "story-verify-parent-b",
          str(r.json()))
    with app.session() as s:
        edges = s.scalars(select(Edge).where(
            Edge.dst == child_id, Edge.edge_type == app.WORK_HIERARCHY_EDGE_TYPE)).all()
        check("...exactly ONE parent edge exists (old one replaced, not accumulated)",
              len(edges) == 1 and edges[0].src == "story-verify-parent-b",
              [(e.src, e.dst) for e in edges])

    # Clear the parent.
    r = patch_workitem(child_id, {"parent": None})
    check("PATCH parent=null unparents -> 200", r.status_code == 200 and r.json().get("parent") is None,
          f"got {r.status_code} {r.json().get('parent')}")
    with app.session() as s:
        edges = s.scalars(select(Edge).where(
            Edge.dst == child_id, Edge.edge_type == app.WORK_HIERARCHY_EDGE_TYPE)).all()
        check("...no parent edge remains", len(edges) == 0, len(edges))

    # -- 8b. TASK-1378: a re-parent keeps the hierarchy order, no self, no cycle -- #
    print("\n-- 8b. PATCH parent: order (epic > story > task), no self-parent, no cycle --")
    r = post_workitem({"type": "epic", "id": "epic-verify-order", "name": "order epic"})
    check("order epic created", r.status_code == 201, f"got {r.status_code}")
    r = post_workitem({"type": "epic", "id": "epic-verify-order-loop", "name": "order epic, legacy loop"})
    check("second order epic created", r.status_code == 201, f"got {r.status_code}")
    r = patch_workitem(child_id, {"parent": "story-verify-parent-a"})
    check("task back under story A -> 200", r.status_code == 200, f"got {r.status_code} {r.text[:150]}")

    def _parent_of(nid):
        with app.session() as s:
            return sorted(e.src for e in s.scalars(select(Edge).where(
                Edge.dst == nid, Edge.edge_type == app.WORK_HIERARCHY_EDGE_TYPE)).all())

    def _event_count():
        with app.session() as s:
            return len(s.scalars(select(GraphEvent.id)).all())

    def refused(label, nid, parent, code):
        before_parent, before_events = _parent_of(nid), _event_count()
        r = patch_workitem(nid, {"parent": parent})
        check(f"{label} -> 400 {code}",
              r.status_code == 400 and r.json().get("error") == code and r.json().get("field") == "parent",
              f"got {r.status_code} {r.text[:200]}")
        check("...nothing moved", _parent_of(nid) == before_parent, f"{before_parent} -> {_parent_of(nid)}")
        check("...and no graph_event was written", _event_count() == before_events,
              f"{before_events} -> {_event_count()}")

    refused("a story under a task (inverted order)", "story-verify-parent-a", child_id, "parent_order")
    refused("an epic under a story (inverted order)", "epic-verify-order", "story-verify-parent-b", "parent_order")
    refused("a story under a story (same rung)", "story-verify-parent-a", "story-verify-parent-b", "parent_order")
    refused("a task under a task (same rung)", child_id, dep_id, "parent_order")
    refused("an item as its own parent", "story-verify-parent-a", "story-verify-parent-a", "parent_is_self")
    # A cycle cannot be built through this route once the order holds, but legacy
    # rows can break the order: plant one (story B contains the loop epic) and ask
    # for the epic to become story B's parent. The order allows it; the cycle does not.
    with app.session() as s:
        s.add(Edge(src="story-verify-parent-b", dst="epic-verify-order-loop",
                   edge_type=app.WORK_HIERARCHY_EDGE_TYPE, origin="atlas"))
        s.commit()
    refused("a descendant as the new parent (cycle)", "story-verify-parent-b", "epic-verify-order-loop",
            "parent_cycle")
    with app.session() as s:
        for e in s.scalars(select(Edge).where(Edge.src == "story-verify-parent-b",
                                              Edge.dst == "epic-verify-order-loop")).all():
            s.delete(e)
        s.commit()

    r = patch_workitem("story-verify-parent-a", {"parent": "epic-verify-order"})
    check("a valid move (story under an epic) -> 200",
          r.status_code == 200 and r.json().get("parent") == "epic-verify-order", f"got {r.status_code} {r.text[:150]}")
    r = patch_workitem(child_id, {"parent": "epic-verify-order"})
    check("a valid move that skips a rung (task straight under an epic) -> 200",
          r.status_code == 200 and r.json().get("parent") == "epic-verify-order", f"got {r.status_code} {r.text[:150]}")
    r = patch_workitem(child_id, {"parent": None})
    check("...and detached again -> 200", r.status_code == 200, f"got {r.status_code}")

    # The same order at CREATE, before any id is minted.
    def _type_of(nid):
        with app.session() as s:
            return s.get(Node, nid).type

    def _seq_now():
        with app.session() as s:
            return s.execute(text("SELECT last_value FROM task_id_seq")).scalar()

    refused_names = []
    for label, payload in [
        ("a story created under a task", {"type": "story", "id": "story-verify-order-bad", "parent": child_id}),
        ("an epic created under an epic", {"type": "epic", "id": "epic-verify-order-bad", "parent": "epic-verify-order"}),
        ("a task created under a task", {"type": "task", "parent": dep_id}),
    ]:
        name = "order refused: " + label
        refused_names.append(name)
        before_seq, before_events = _seq_now(), _event_count()
        r = post_workitem({**payload, "name": name})
        check(f"create: {label} -> 400 parent_order",
              r.status_code == 400 and r.json().get("error") == "parent_order" and r.json().get("field") == "parent",
              f"got {r.status_code} {r.text[:200]}")
        with app.session() as s:
            leaked = s.scalars(select(Node).where(Node.name == name)).all()
        check("...nothing written", len(leaked) == 0 and _event_count() == before_events,
              f"{len(leaked)} row(s), events {before_events} -> {_event_count()}")
        check("...and no task id spent (task_id_seq did not advance)", _seq_now() == before_seq,
              f"{before_seq} -> {_seq_now()}")
    r = post_workitem({"type": "task", "name": "order ok: task under an epic", "parent": "epic-verify-order"})
    check("create: a task straight under an epic -> 201", r.status_code == 201, f"got {r.status_code} {r.text[:150]}")
    skip_task = r.json().get("id")
    r = patch_workitem(skip_task, {"parent": None})
    check("...detached again -> 200", r.status_code == 200, f"got {r.status_code}")

    # A type change is judged against the parent the item already has and the
    # children it already holds.
    r = patch_workitem(child_id, {"parent": "story-verify-parent-a"})
    check("task under story A again -> 200", r.status_code == 200, f"got {r.status_code} {r.text[:150]}")
    before_events = _event_count()
    r = patch_workitem("story-verify-parent-a", {"type": "task"})
    check("type: a story with a task under it cannot become a task -> 400 child_order",
          r.status_code == 400 and r.json().get("error") == "child_order" and r.json().get("field") == "type",
          f"got {r.status_code} {r.text[:200]}")
    check("...the type is unchanged and no graph_event was written",
          _type_of("story-verify-parent-a") == "story" and _event_count() == before_events,
          f"{_type_of('story-verify-parent-a')}, events {before_events} -> {_event_count()}")
    r = patch_workitem(child_id, {"type": "epic"})
    check("type: a task under a story cannot become an epic -> 400 parent_order (field type)",
          r.status_code == 400 and r.json().get("error") == "parent_order" and r.json().get("field") == "type",
          f"got {r.status_code} {r.text[:200]}")
    check("...the type is unchanged", _type_of(child_id) == "task", _type_of(child_id))
    r = patch_workitem(child_id, {"type": "epic", "parent": None})
    check("type and parent together are judged as a whole: detached, the task may become an epic -> 200",
          r.status_code == 200 and _type_of(child_id) == "epic", f"got {r.status_code} {r.text[:200]}")
    r = patch_workitem(child_id, {"type": "task"})
    check("...and back to a task (no children) -> 200", r.status_code == 200 and _type_of(child_id) == "task",
          f"got {r.status_code} {r.text[:200]}")

    r = post_workitem({"type": "task", "name": "doomed by an object-family parent",
                       "parent": "seed-world-component"})
    check("an OBJECT-family parent -> 409 family_mismatch (same rule create used above, "
          "re-checked for a different node)",
          r.status_code == 409 and r.json().get("error") == "family_mismatch",
          f"got {r.status_code} {r.text[:150]}")

    # -- 9. PATCH refuses a non-workitem node -------------------------------------- #
    print("\n-- 9. /api/workitem refuses a non-workitem node --")
    r = patch_workitem("seed-world-service", {"summary": "someone"})
    check("PATCH /api/workitem/atlas-api (an object node) -> 409 not_a_workitem",
          r.status_code == 409 and r.json().get("error") == "not_a_workitem",
          f"got {r.status_code} {r.text[:150]}")

    # -- 10. unknown status is refused, a settable one round-trips --------------- #
    print("\n-- 10. status --")
    r = post_workitem({"type": "task", "name": "bad status", "status": "not-a-real-status"})
    check("create with an unknown status -> 400 unknown_status",
          r.status_code == 400 and r.json().get("error") == "unknown_status",
          f"got {r.status_code} {r.text[:150]}")
    r = post_workitem({"type": "task", "name": "good status", "status": "in_progress"})
    check("create with a settable status -> 201", r.status_code == 201, f"got {r.status_code}")
    check("response reports the effective status", r.json().get("status") == "in_progress",
          str(r.json().get("status")))
    check("...and the response no longer carries `durable` (TASK-1058)",
          "durable" not in r.json(), str(r.json())[:200])

    # -- 11. result (TASK-1064): recording a result stops rewriting the spec ----- #
    print("\n-- 11. result: create/PATCH round-trip `workitem_result` WITHOUT touching body --")

    # 11a. Create with `result` — the row is stored and `body` is exactly what was sent.
    create_body = "# Title\n\nIntro paragraph.\n\n## Details\n\nSome detail content."
    r = post_workitem({"type": "task", "name": "result: create stores row, body untouched",
                       "body": create_body, "result": "created outcome text"})
    check("create with result -> 201", r.status_code == 201, f"got {r.status_code} {r.text[:200]}")
    created = r.json()
    check("response's body is EXACTLY what was sent (result never touched it)",
          created.get("body") == create_body, str(created.get("body")))
    check("response echoes the result text", created.get("result") == "created outcome text",
          str(created.get("result")))
    check("response's result_heading is 'Result' (synthesised display heading for a new row)",
          created.get("result_heading") == "Result", str(created.get("result_heading")))
    result_id = created["id"]
    with app.session() as s:
        row = s.scalar(select(WorkitemResult).where(WorkitemResult.node_id == result_id))
        n = s.get(Node, result_id)
        check("workitem_result row exists with the raw heading '## Result'",
              row is not None and row.heading == "## Result", str(row.heading if row else None))
        expected_pos = len(h2_indices(create_body.split("\n")))
        check(f"...position == {expected_pos} (the section count of the body sent, appended at the end)",
              row is not None and row.position == expected_pos, str(row.position if row else None))
        check("node.body in the DB is byte-for-byte identical to what was sent",
              n is not None and n.body == create_body, "mismatch")

    # 11b. PATCH `result` on an item that already has a row — body byte-identical,
    # heading/position PRESERVED. Built explicitly against a '## Outcome' heading
    # (3 of the 221 real result-bearing files use it, not '## Result') — the row is
    # seeded directly (this API's OWN create path only ever synthesises '## Result';
    # '## Outcome' rows come from the one-time importer, TASK-1063) so this proves
    # PATCH does not silently normalise a caller's own heading to the default.
    outcome_body = "# Spec\n\nOriginal spec paragraph.\n\n## Extra\n\nExtra body content."
    r = post_workitem({"type": "task", "name": "result: PATCH preserves an existing '## Outcome' heading",
                       "body": outcome_body})
    check("create (no result) for the Outcome-heading fixture -> 201", r.status_code == 201,
          f"got {r.status_code} {r.text[:200]}")
    outcome_id = r.json()["id"]
    with app.session() as s:
        s.add(WorkitemResult(node_id=outcome_id, position=1, heading="## Outcome",
                             text="legacy outcome text"))
        s.commit()

    r = patch_workitem(outcome_id, {"result": "updated outcome text"})
    check("PATCH result on an item with an existing '## Outcome' row -> 200",
          r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    patched_outcome = r.json()
    check("PATCHed response's body is byte-identical to the ORIGINAL body — this PATCH sent no "
          "`body` field at all, and `result` must never touch it",
          patched_outcome.get("body") == outcome_body, str(patched_outcome.get("body")))
    check("...result text is updated", patched_outcome.get("result") == "updated outcome text",
          str(patched_outcome.get("result")))
    check("...result_heading is STILL 'Outcome' — the caller's own heading survives, not "
          "silently rewritten to 'Result'",
          patched_outcome.get("result_heading") == "Outcome", str(patched_outcome.get("result_heading")))
    with app.session() as s:
        row = s.scalar(select(WorkitemResult).where(WorkitemResult.node_id == outcome_id))
        n = s.get(Node, outcome_id)
        check("...the raw heading in the DB is verbatim '## Outcome', unchanged",
              row is not None and row.heading == "## Outcome", str(row.heading if row else None))
        check("...position is unchanged (1)", row is not None and row.position == 1,
              str(row.position if row else None))
        check("...node.body in the DB is byte-for-byte identical to what it was BEFORE this PATCH",
              n is not None and n.body == outcome_body, "mismatch")

    # 11c. PATCH `result` on an item with NO row — creates one appended at the right
    # position, and reassemble() reproduces spec+result in the right order.
    plain_body = "# Plain\n\nJust a spec, no result yet.\n\n## Notes\n\nSome notes."
    r = post_workitem({"type": "task", "name": "result: PATCH creates a row when none exists",
                       "body": plain_body})
    plain_id = r.json()["id"]
    with app.session() as s:
        check("...no workitem_result row exists yet for the fixture",
              s.scalar(select(WorkitemResult).where(WorkitemResult.node_id == plain_id)) is None)

    r = patch_workitem(plain_id, {"result": "freshly recorded outcome"})
    check("PATCH result on an item with none -> 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    created_via_patch = r.json()
    check("...body is untouched — byte-identical to what it was before this PATCH",
          created_via_patch.get("body") == plain_body, str(created_via_patch.get("body")))
    check("...result text is stored", created_via_patch.get("result") == "freshly recorded outcome",
          str(created_via_patch.get("result")))
    check("...result_heading is 'Result' (synthesised heading for a brand-new row)",
          created_via_patch.get("result_heading") == "Result", str(created_via_patch.get("result_heading")))
    with app.session() as s:
        row = s.scalar(select(WorkitemResult).where(WorkitemResult.node_id == plain_id))
        n = s.get(Node, plain_id)
        check("workitem_result row created with the raw heading '## Result'",
              row is not None and row.heading == "## Result", str(row.heading if row else None))
        expected_pos2 = len(h2_indices(plain_body.split("\n")))
        check(f"...position == {expected_pos2} (appended after the one existing '##' section)",
              row is not None and row.position == expected_pos2, str(row.position if row else None))
        rebuilt = reassemble(n.body, row.heading, row.text, row.position)
        expected_rebuilt = "\n".join(
            plain_body.split("\n") + [row.heading] + row.text.split("\n")
        )
        check("reassemble(body, heading, text, position) reproduces spec-then-result, in that order",
              rebuilt == expected_rebuilt, f"got: {rebuilt!r}")
        check("...and the spec text still appears BEFORE the result text in the reassembled body",
              rebuilt.index("Just a spec") < rebuilt.index("freshly recorded outcome"), rebuilt)

    # 11d. Omitting `result` from a PATCH leaves the existing row untouched.
    r = patch_workitem(outcome_id, {"summary": "someone-recording-results"})
    check("PATCH omitting result -> 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    untouched = r.json()
    check("...result is UNCHANGED (exclude_unset — an omitted field is never touched)",
          untouched.get("result") == "updated outcome text", str(untouched.get("result")))
    check("...result_heading is UNCHANGED too", untouched.get("result_heading") == "Outcome",
          str(untouched.get("result_heading")))
    with app.session() as s:
        row = s.scalar(select(WorkitemResult).where(WorkitemResult.node_id == outcome_id))
        check("...the workitem_result row itself is byte-identical (heading/position/text all same)",
              row is not None and row.heading == "## Outcome" and row.position == 1
              and row.text == "updated outcome text",
              str((row.heading, row.position, row.text) if row else None))

    # 11e. `result: null` deletes the row; body stays untouched.
    r = patch_workitem(outcome_id, {"result": None})
    check("PATCH result=null -> 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    cleared = r.json()
    check("...result is null in the response", cleared.get("result") is None, str(cleared.get("result")))
    check("...result_heading is null too", cleared.get("result_heading") is None,
          str(cleared.get("result_heading")))
    with app.session() as s:
        row = s.scalar(select(WorkitemResult).where(WorkitemResult.node_id == outcome_id))
        n = s.get(Node, outcome_id)
        check("...the workitem_result row is actually GONE, not just hidden", row is None, str(row))
        check("...node.body is STILL byte-identical to the original — a delete never touches it either",
              n is not None and n.body == outcome_body, "mismatch")

    # 11f. GET /api/node/{id} carries result/result_heading; GET /api/workitems does not.
    r = client.get(f"/api/node/{result_id}")
    check("GET /api/node/{id} -> 200", r.status_code == 200, f"got {r.status_code}")
    node_detail = r.json()
    check("...carries 'result'", node_detail.get("result") == "created outcome text",
          str(node_detail.get("result")))
    check("...carries 'result_heading'", node_detail.get("result_heading") == "Result",
          str(node_detail.get("result_heading")))

    r = client.get(f"/api/node/{plain_id}")
    check("GET /api/node/{id} for the PATCH-created row -> 200", r.status_code == 200, f"got {r.status_code}")
    plain_detail = r.json()
    check("...carries the PATCH-created result text",
          plain_detail.get("result") == "freshly recorded outcome", str(plain_detail.get("result")))

    r = client.get("/api/workitems", params={"type": "task", "page_size": 200})
    check("GET /api/workitems -> 200", r.status_code == 200, f"got {r.status_code}")
    listing = r.json()
    items = listing.get("items", [])
    check("...listing returned at least one item to check", len(items) > 0, len(items))
    check("...NO listing item carries 'result' — never emitted in a listing",
          all("result" not in item for item in items),
          [i["id"] for i in items if "result" in i])
    check("...NO listing item carries 'result_heading' either",
          all("result_heading" not in item for item in items),
          [i["id"] for i in items if "result_heading" in i])

    # -- 12. TASK-1120: `owner` is RETIRED on the workitem write path --------- #
    #
    # The acceptance criterion is "`owner` IS RETIRED ON WORKITEMS", and the
    # unmet half of it was a write site: `create_workitem` passed
    # `owner=body.owner` straight into the `GraphNode`. Two claims are checked
    # here and neither can be satisfied by the other:
    #
    #   (a) the field is REFUSED, by name, with a 400 — not accepted, and not
    #       silently dropped. A silent drop returns 200 and leaves the caller
    #       believing it took effect; see `app.owner_retired_refusal`.
    #   (b) nothing on the create path writes the column any more. Asserted
    #       against the ROW, not the response, because the response never
    #       carried `owner` for a workitem in the first place and would have
    #       looked identical either way.
    print("\n-- 12. owner is retired on workitems (TASK-1120) --")

    r = post_workitem({"type": "task", "name": "owner on create", "owner": "Alex"})
    check("POST /api/workitem with owner -> 400", r.status_code == 400, f"got {r.status_code}")
    check("...error is owner_retired and NAMES the field (same shape as _resolve_assignee)",
          r.json().get("error") == "owner_retired" and r.json().get("field") == "owner",
          str(r.json())[:200])
    check("...and the refusal points at the field that replaced it",
          "assignee" in (r.json().get("detail") or ""), str(r.json().get("detail"))[:200])

    # PRESENCE, not value — this is the half of the decision that costs
    # something, so it is the half worth pinning. An explicit null would have
    # had the same EFFECT as omitting the key, and is still refused: a client
    # that always serializes the field is exactly the stale caller this is
    # meant to find, and it is fixed the same way (stop sending the key).
    r = post_workitem({"type": "task", "name": "owner null on create", "owner": None})
    check("POST with an explicit `owner: null` -> 400 as well (refused on PRESENCE)",
          r.status_code == 400 and r.json().get("error") == "owner_retired",
          f"got {r.status_code} {r.text[:150]}")

    # ...and a refused create must not have burned a task id or left a row.
    with app.session() as s:
        strays = s.scalars(select(Node).where(Node.name.in_(
            ("owner on create", "owner null on create")))).all()
    check("...no node row survives either refusal", not strays,
          str([n.id for n in strays]))

    clean = post_workitem({"type": "task", "name": "created with no owner at all"})
    check("a create that omits owner still works -> 201", clean.status_code == 201,
          f"got {clean.status_code} {clean.text[:150]}")
    clean_id = clean.json()["id"]
    with app.session() as s:
        row = s.get(Node, clean_id)
        check("...and the ROW's owner column is NULL — the create path no longer writes it",
              row is not None and row.owner is None,
              repr(row.owner if row else "no row"))

    r = patch_workitem(clean_id, {"owner": "Alex"})
    check("PATCH /api/workitem with owner -> 400 owner_retired",
          r.status_code == 400 and r.json().get("error") == "owner_retired",
          f"got {r.status_code} {r.text[:150]}")
    r = patch_workitem(clean_id, {"owner": None})
    check("PATCH with `owner: null` -> 400 too", r.status_code == 400
          and r.json().get("error") == "owner_retired", f"got {r.status_code} {r.text[:150]}")

    # A refused PATCH must be a NO-OP on the rest of the same request. The
    # refusal returns before the setattr loop, so `name` here must not land.
    r = patch_workitem(clean_id, {"owner": "Alex", "name": "renamed by a refused patch"})
    check("a refused PATCH changes nothing else in the same request",
          r.status_code == 400, f"got {r.status_code}")
    with app.session() as s:
        row = s.get(Node, clean_id)
        check("...the name is untouched", row is not None
              and row.name == "created with no owner at all", str(row.name if row else None))

    # ORDERING, pinned deliberately (see the comment at the check in
    # `patch_workitem`): on an OBJECT node, "wrong route" beats "retired
    # field", because `owner` is still the right field there — through
    # /api/node. Flip the two checks and this fails.
    r = patch_workitem("seed-world-service", {"owner": "someone"})
    check("PATCH an OBJECT node with owner -> 409 not_a_workitem, NOT 400 — the more "
          "useful of the two true answers",
          r.status_code == 409 and r.json().get("error") == "not_a_workitem",
          f"got {r.status_code} {r.text[:150]}")

    # THE FIELD IS NOT RETIRED EVERYWHERE. /api/node is the world-node write
    # path and `owner` means "who looks after this part of the world" there —
    # deliberately untouched by TASK-1120. Checked so a later, wider sweep of
    # the string has to fail here before it removes the wrong one.
    r = client.patch("/api/node/seed-world-service",
                     json={"owner": "Alex", "csrf_token": csrf}, headers=H)
    check("PATCH /api/node with owner is STILL 200 — object nodes keep the field",
          r.status_code == 200, f"got {r.status_code} {r.text[:150]}")
    with app.session() as s:
        row = s.get(Node, "seed-world-service")
        check("...and it actually landed on the row", row is not None and row.owner == "Alex",
              repr(row.owner if row else "no row"))

    print("\n" + "=" * 78)
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL CHECKS PASSED")
    print("=" * 78)


if __name__ == "__main__":
    main()
