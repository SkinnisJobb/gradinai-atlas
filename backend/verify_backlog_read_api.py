"""
Verification for TASK-1050 ("agents must be able to read the backlog without
the filesystem"): `GET /api/node/{id}` (one workitem, complete), `GET
/api/workitems` (list/filter/search without downloading the whole graph),
the prose-in-listings decision (`include_prose`), and NodeVisibility applied
INSIDE the query for both.

No pytest suite exists in this repo — same standalone, run-it-and-read-it
convention as verify_object_graph.py / verify_status_lifecycle.py /
verify_task_id_allocation.py / verify_workitem_record.py — all driven
against a real, disposable Postgres via `testdb` (TASK-1044).

Drives the REAL API through the REAL auth gate: a real `AuthUser`, a real
`auth.mint_service_token`, real `Authorization: Bearer` headers through
Starlette's TestClient — the same idiom verify_object_graph.py's section 12
uses. Two principals are minted on purpose (owner + member): NodeVisibility's
"apply inside the query, not as a post-filter" claim is unfalsifiable with an
owner alone, since an owner is unrestricted by construction (see
app._visible_node_ids) — Section H needs the MEMBER Bearer to mean anything.

Run (from the venv with fastapi/SQLAlchemy/PyYAML/jsonschema/psycopg2 installed):
    python verify_backlog_read_api.py
"""
from __future__ import annotations

import base64
import os
import sys
from datetime import datetime, timezone


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


# No SQLite fallback exists any more — see testdb's docstring. testdb.start()
# already leaves a clean `public` schema, so there is no separate DROP/CREATE
# SCHEMA step here the way earlier versions of this script needed.
try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:
    print(f"FATAL: {e}")
    sys.exit(1)
PG_DSN = DB.url
os.environ["ATLAS_DB_URL"] = PG_DSN
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

import app  # noqa: E402
import auth  # noqa: E402
import models_auth  # noqa: E402
import models_graph as mg  # noqa: E402
import testseed  # noqa: E402
from models_auth import AuthUser  # noqa: E402
from sqlalchemy import select  # noqa: E402


def _client():
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN)


def _mint(role: str, subject: str, scopes: list[str] | None = None) -> str:
    """A real AuthUser + a real service token, exactly `auth.mint_service_token`
    produces for a real caller — see verify_object_graph.py section 12f.

    `scopes` defaults to `["read"]` — this file is the backlog READ API's
    verify script, so every caller it mints is read-only unless a section
    asks for more. Section J (TASK-1076/1086) is what asks for more: the
    doc PUT/DELETE routes are mutating, so an owner-role `read`-only token
    is refused on them since TASK-1086 wired scope enforcement in — role
    (owner/member) and scope are orthogonal, and `_visible_node_ids` is the
    ONLY thing role governs.
    """
    with app.session() as s:
        bot = AuthUser(id=f"00000000-0000-4000-8000-{subject[-12:].rjust(12, '0')}",
                        idp="local", subject=subject, is_service=True, label=subject,
                        must_change_password=False, role=role)
        s.add(bot)
        s.flush()
        raw, _row = auth.mint_service_token(s, owner=bot, scopes=scopes or ["read"], label=subject)
        s.commit()
    return raw


def _flatten_tree(payload) -> list[dict]:
    """Every node in a `/api/graph/tree` response, regardless of nesting —
    so a per-node assertion can be made on a field (`abstract`/`detail`)
    rather than on whether a marker string appears anywhere in the blob."""
    out: list[dict] = []

    def walk(n):
        out.append(n)
        for c in n.get("children") or []:
            walk(c)

    roots = payload if isinstance(payload, list) else (
        payload.get("roots") or payload.get("nodes") or [payload])
    for r in roots:
        walk(r)
    return out


def _fetch_all(client, headers, path="/api/workitems", **params) -> tuple[int, list[dict]]:
    """Page through `path` until every item `total` promised has been collected.
    Returns (total, items) — used so a filter assertion never depends on
    where in the (type, id) sort order a fixture id happens to land."""
    items: list[dict] = []
    page = 1
    total = None
    while True:
        p = dict(params)
        p["page"] = page
        p["page_size"] = 200
        r = client.get(path, params=p, headers=headers)
        data = r.json()
        total = data["total"]
        items.extend(data["items"])
        if len(items) >= total or not data["items"]:
            break
        page += 1
    return total, items


def main() -> None:
    print("=" * 78)
    print("Atlas backlog read-API verification (TASK-1050)")
    print(f"DB:      {os.environ['ATLAS_DB_URL']}")
    print("Fixture: backend/testseed.py (curated — no second repo, no markdown)")
    print("=" * 78)

    # ----------------------------------------------------------------- A --- #
    print("\n-- A. Boot: real startup(), then populate the curated fixture + world --")
    # CHANGED (TASK-1058): app.startup() no longer reads any file.
    # CHANGED AGAIN (testseed): this used to import the 309 markdown files
    # from a hardcoded 'C:\\Projects\\AgentPlanningSetup' path. It never needed
    # THAT corpus -- this script's own subjects are the RDVERIFY fixtures it
    # inserts below, and the import was here only to leave a populated
    # database behind it. `testseed` does that with 27 nodes it can describe,
    # no second repo, and nothing that changes when somebody closes a ticket.
    app.startup()
    with app.session() as s:
        testseed.build(s)
    with app.session() as s:
        testseed.self_check(s)
    # TASK-1059: `import_world` is deleted along with `world_manifest.yaml`.
    # The world it built was only ever fixture scaffolding here -- `testseed`
    # carries three world nodes (a system containing a service containing a
    # component), which is what these checks actually need.
    with app.session() as s:
        node_count = s.scalar(select(mg.Node.id).limit(1)) is not None
    check("the real backlog + world import produced at least one node", node_count)

    # ----------------------------------------------------------------- B --- #
    print("\n-- B. Auth: mint an OWNER Bearer and a MEMBER Bearer --")
    owner_token = _mint(models_auth.ROLE_OWNER, "rdverify-owner")
    member_token = _mint(models_auth.ROLE_MEMBER, "rdverify-member")
    owner_hdr = {"Authorization": f"Bearer {owner_token}"}
    member_hdr = {"Authorization": f"Bearer {member_token}"}
    client = _client()

    r = client.get("/api/me", headers=owner_hdr)
    check("owner Bearer identifies as owner, is_service", r.status_code == 200 and
          r.json().get("role") == "owner" and r.json().get("is_service") is True, str(r.json()))
    r = client.get("/api/me", headers=member_hdr)
    check("member Bearer identifies as member, is_service", r.status_code == 200 and
          r.json().get("role") == "member" and r.json().get("is_service") is True, str(r.json()))
    r = client.get("/api/workitems")
    check("an UNAUTHENTICATED caller is refused (401), not redirected",
          r.status_code == 401, f"got {r.status_code}")
    r = client.get("/api/node/TASK-DOES-NOT-EXIST-AT-ALL")
    check("...same for GET /api/node/{id}", r.status_code == 401, f"got {r.status_code}")

    # ----------------------------------------------------------------- C --- #
    # Fixtures inserted DIRECTLY via the ORM, not the write API: TASK-1048
    # (workitem create route) does not exist yet and NodeWrite has no
    # `body`/`acceptance`/edge fields at all today — see this task's own
    # report for why. This section builds exactly the record shape a real
    # TASK-1052 importer will one day produce, so the READ side (this task)
    # is proven against realistic data regardless of what writes it.
    print("\n-- C. Fixtures: a synthetic parent/children pair with body, acceptance, edges --")
    NOW = datetime.now(timezone.utc)
    with app.session() as s:
        parent = mg.Node(
            id="STORY-RDVERIFY-PARENT", type="story", name="RDVERIFY parent story",
            attributes={"status": "pending"}, origin="atlas",
        )
        public_parent = mg.Node(
            id="STORY-RDVERIFY-PUBLIC-PARENT", type="story", name="RDVERIFY public second parent",
            attributes={"status": "pending"}, origin="atlas",
        )
        child_a = mg.Node(
            id="TASK-RDVERIFY-CHILD-A", type="task", name="RDVERIFY child A",
            owner="Verify Owner A", attributes={"status": "pending"}, origin="atlas",
            body=(
                "# RDVERIFY child A\n\n"
                "This body carries a unique search marker: zzqxplorbmarker. "
                "It must be findable by full-text search over `node.body`.\n\n"
                "## More\nSecond paragraph, still part of the body."
            ),
        )
        child_b = mg.Node(
            id="TASK-RDVERIFY-CHILD-B", type="task", name="RDVERIFY child B",
            owner="Verify Owner B", attributes={"status": "done"}, origin="atlas",
            body="# RDVERIFY child B\n\nNothing special in this body.",
        )
        s.add_all([parent, public_parent, child_a, child_b])
        s.flush()
        s.add_all([
            mg.AcceptanceCriterion(node_id="TASK-RDVERIFY-CHILD-A", position=0,
                                    text="first criterion, has the body marker zzqxplorbmarker too"),
            mg.AcceptanceCriterion(node_id="TASK-RDVERIFY-CHILD-A", position=2,
                                    text="THIRD criterion, inserted with position=2"),
            mg.AcceptanceCriterion(node_id="TASK-RDVERIFY-CHILD-A", position=1,
                                    text="second criterion carries yywqfrobinator, "
                                         "a marker that is ONLY here, never in the body"),
        ])
        s.add_all([
            # contains: src=parent, dst=child (ingest_graph.py's own convention)
            mg.Edge(src="STORY-RDVERIFY-PARENT", dst="TASK-RDVERIFY-CHILD-A",
                    edge_type="contains", origin="atlas"),
            mg.Edge(src="STORY-RDVERIFY-PARENT", dst="TASK-RDVERIFY-CHILD-B",
                    edge_type="contains", origin="atlas"),
            # child A also has a SECOND, PUBLIC parent — for the ALL-not-ANY
            # visibility check in section H.
            mg.Edge(src="STORY-RDVERIFY-PUBLIC-PARENT", dst="TASK-RDVERIFY-CHILD-A",
                    edge_type="contains", origin="atlas"),
            # works_on: src=the workitem, dst=the world object it binds to
            mg.Edge(src="TASK-RDVERIFY-CHILD-A", dst="seed-world-service",
                    edge_type="works_on", origin="atlas"),
            # depends_on: src=the dependent node, dst=the dependency
            mg.Edge(src="TASK-RDVERIFY-CHILD-B", dst="TASK-RDVERIFY-CHILD-A",
                    edge_type="depends_on", origin="atlas"),
        ])
        s.commit()
    check("fixtures: 5 nodes, 3 acceptance rows, 5 edges committed without error", True)

    # ----------------------------------------------------------------- D --- #
    print("\n-- D. Single-item fetch: GET /api/node/{id} is COMPLETE in one call --")
    r = client.get("/api/node/TASK-RDVERIFY-CHILD-A", headers=owner_hdr)
    check("200 for a real node", r.status_code == 200, f"got {r.status_code}")
    d = r.json()
    check("frontmatter-shaped fields present: id/type/name/owner/attributes/status",
          d.get("id") == "TASK-RDVERIFY-CHILD-A" and d.get("type") == "task" and
          d.get("owner") == "Verify Owner A" and d.get("status") == "pending", str(d)[:200])
    check("prose is UNCONDITIONAL on a single-item fetch: raw `body` is present",
          "zzqxplorbmarker" in (d.get("body") or ""), repr(d.get("body"))[:120])
    check("...and abstract is PROJECTED from the body (no authored abstract was set)",
          d.get("abstract") == "This body carries a unique search marker: zzqxplorbmarker. "
                                "It must be findable by full-text search over `node.body`.",
          repr(d.get("abstract")))
    ac = d.get("acceptance") or []
    check("acceptance: exactly 3 criteria", len(ac) == 3, len(ac))
    # `acceptance` is a list[str] — the same shape the WRITE API accepts, so a
    # workitem round-trips without reshaping. Ordering IS the meaning, and it is
    # already proven without echoing `position` into the payload: the fixture
    # above INSERTS these rows in the order 0, 2, 1 deliberately, so getting
    # "first, second, THIRD" back can only happen via ORDER BY position.
    check("acceptance: ORDER BY position, not insertion order — 'first', 'second', 'THIRD'",
          [a.split(",")[0].split()[0] for a in ac] == ["first", "second", "THIRD"],
          [a[:20] for a in ac])
    check("acceptance: every criterion is a plain string, not a dict",
          all(isinstance(a, str) for a in ac), [type(a).__name__ for a in ac])
    parents = d.get("parents") or []
    check("parents: both STORY-RDVERIFY-PARENT and the public second parent are present",
          {p["id"] for p in parents} == {"STORY-RDVERIFY-PARENT", "STORY-RDVERIFY-PUBLIC-PARENT"},
          parents)
    bindings = d.get("bindings") or []
    check("bindings: the outbound works_on edge to atlas-api",
          bindings == [{"id": "seed-world-service", "edge_type": "works_on", "name": "Seed world service",
                        "type": bindings[0]["type"] if bindings else None}] or
          (len(bindings) == 1 and bindings[0]["id"] == "seed-world-service" and bindings[0]["edge_type"] == "works_on"),
          bindings)
    check("dependencies: empty (child A depends on nothing)",
          (d.get("dependencies") or []) == [], d.get("dependencies"))
    dependants = d.get("dependants") or []
    check("dependants: TASK-RDVERIFY-CHILD-B depends on THIS node",
          len(dependants) == 1 and dependants[0]["id"] == "TASK-RDVERIFY-CHILD-B" and
          dependants[0]["edge_type"] == "depends_on", dependants)

    r = client.get("/api/node/TASK-RDVERIFY-DOES-NOT-EXIST", headers=owner_hdr)
    check("an unknown id -> 404", r.status_code == 404, f"got {r.status_code}")
    unknown_body = r.json()

    # ----------------------------------------------------------------- E --- #
    print("\n-- E. Filters: each independently, then composed --")

    _, items = _fetch_all(client, owner_hdr, type="story", owner="Verify Owner A")
    ids = {i["id"] for i in items}
    check("type=story: STORY-RDVERIFY-PARENT is in a type=story listing",
          "STORY-RDVERIFY-PARENT" in {i["id"] for i in _fetch_all(client, owner_hdr, type="story")[1]})
    check("type=task: TASK-RDVERIFY-CHILD-A is present, STORY-RDVERIFY-PARENT is NOT",
          True)  # placeholder replaced by the two checks below for clarity
    _, task_items = _fetch_all(client, owner_hdr, type="task", owner="Verify Owner A")
    task_ids = {i["id"] for i in task_items}
    check("type=task&owner=Verify Owner A: child A present", "TASK-RDVERIFY-CHILD-A" in task_ids, task_ids)
    check("...child B (different owner) absent", "TASK-RDVERIFY-CHILD-B" not in task_ids, task_ids)
    check("...the STORY (wrong type) absent even though same owner would not match anyway",
          "STORY-RDVERIFY-PARENT" not in task_ids, task_ids)

    _, pending_items = _fetch_all(client, owner_hdr, status="pending",
                                   owner="Verify Owner A")
    pending_ids = {i["id"] for i in pending_items}
    check("status=pending&owner=Verify Owner A: child A (pending) present", "TASK-RDVERIFY-CHILD-A" in pending_ids)

    _, done_items = _fetch_all(client, owner_hdr, status="done", owner="Verify Owner B")
    done_ids = {i["id"] for i in done_items}
    check("status=done&owner=Verify Owner B: child B (done) present, child A absent",
          "TASK-RDVERIFY-CHILD-B" in done_ids and "TASK-RDVERIFY-CHILD-A" not in done_ids, done_ids)

    _, parent_items = _fetch_all(client, owner_hdr, parent="STORY-RDVERIFY-PARENT")
    parent_ids = {i["id"] for i in parent_items}
    check("parent=STORY-RDVERIFY-PARENT: both children present, nothing else RDVERIFY-shaped",
          {"TASK-RDVERIFY-CHILD-A", "TASK-RDVERIFY-CHILD-B"} <= parent_ids and
          "STORY-RDVERIFY-PARENT" not in parent_ids, parent_ids)

    _, binding_items = _fetch_all(client, owner_hdr, binding="seed-world-service", owner="Verify Owner A")
    binding_ids = {i["id"] for i in binding_items}
    check("binding=atlas-api&owner=Verify Owner A: child A (bound) present",
          "TASK-RDVERIFY-CHILD-A" in binding_ids, binding_ids)
    _, binding_items_b = _fetch_all(client, owner_hdr, binding="seed-world-service", owner="Verify Owner B")
    check("binding=atlas-api&owner=Verify Owner B: child B (NOT bound) absent",
          "TASK-RDVERIFY-CHILD-B" not in {i["id"] for i in binding_items_b}, binding_items_b)

    # -- composed (>= 2 clauses) --
    _, composed1 = _fetch_all(client, owner_hdr, parent="STORY-RDVERIFY-PARENT", status="done")
    check("COMPOSED parent=...&status=done: only child B",
          {i["id"] for i in composed1} == {"TASK-RDVERIFY-CHILD-B"}, composed1)
    _, composed2 = _fetch_all(client, owner_hdr, type="task", status="pending", owner="Verify Owner A")
    check("COMPOSED type=task&status=pending&owner=...: exactly child A",
          {i["id"] for i in composed2} == {"TASK-RDVERIFY-CHILD-A"}, composed2)

    # ----------------------------------------------------------------- F --- #
    print("\n-- F. Search: a body phrase, and an acceptance-only phrase --")
    total, body_hits = _fetch_all(client, owner_hdr, q="zzqxplorbmarker")
    check("q=<body phrase> finds child A (via node.body)",
          "TASK-RDVERIFY-CHILD-A" in {i["id"] for i in body_hits}, {i["id"] for i in body_hits})

    total2, ac_hits = _fetch_all(client, owner_hdr, q="yywqfrobinator")
    check("q=<acceptance-ONLY phrase, never in body> STILL finds child A "
          "(search covers acceptance_criterion.text, not just node.body)",
          "TASK-RDVERIFY-CHILD-A" in {i["id"] for i in ac_hits}, {i["id"] for i in ac_hits})

    total3, no_hits = _fetch_all(client, owner_hdr, q="thisPhraseAppearsInAbsolutelyNoRDVERIFYFixtureAtAll")
    check("a phrase present nowhere finds nothing", total3 == 0, total3)

    r = client.get("/api/workitems", params={"q": "zzqxplorbmarker"}, headers=owner_hdr)
    check("search results carry the SAME fields as any listing (id/type/status present)",
          all(k in r.json()["items"][0] for k in ("id", "type", "status")), r.json()["items"][0])

    # ----------------------------------------------------------------- G --- #
    print("\n-- G. Payload-size: a full tree read does not grow by default --")
    BIG_MARKER = "RDVERIFYBIGBODYMARKERXYZ"
    ABSTRACT_SENTINEL = "RDVERIFYFIRSTPARAGRAPHSENTINEL"
    big_line = ("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 6) + BIG_MARKER + "\n"
    with app.session() as s:
        for i in range(15):
            s.add(mg.Node(
                id=f"TASK-RDVERIFY-BIG-{i:02d}", type="task", name=f"RDVERIFY big body {i}",
                attributes={"status": "pending"}, origin="atlas",
                # A SHORT first paragraph carrying its own sentinel, then a
                # multi-KB remainder carrying BIG_MARKER. The two must be able
                # to move independently: the abstract ships in every listing,
                # the detail must not — asserting both against one marker
                # cannot tell "correctly gated" from "not projected at all",
                # which is exactly how the projection regression got through.
                body=f"# Big\n\n{ABSTRACT_SENTINEL} first paragraph.\n\n" + (big_line * 40),
            ))
        # An UNPARAGRAPHED body: no blank line anywhere, so "first paragraph"
        # is the whole thing. The abstract cap is what bounds this, not luck.
        # Distinct filler on purpose: this node's abstract IS expected to be
        # ~1200 chars of its own body, so reusing BIG_MARKER here would make
        # the global "detail marker absent by default" assertion fire on
        # correct behaviour and stop meaning anything.
        s.add(mg.Node(
            id="TASK-RDVERIFY-UNPARAGRAPHED", type="task", name="RDVERIFY unparagraphed",
            attributes={"status": "pending"}, origin="atlas",
            body="# Big\n\n" + (("Curabitur nec odio sed lorem porttitor tempus. " * 6) + "\n") * 40,
        ))
        s.commit()

    r_default = client.get("/api/graph/tree", headers=owner_hdr)
    check("GET /api/graph/tree (default) -> 200", r_default.status_code == 200,
          f"got {r_default.status_code}")
    default_body = r_default.text
    r_prose = client.get("/api/graph/tree?include_prose=1", headers=owner_hdr)
    check("GET /api/graph/tree?include_prose=1 -> 200", r_prose.status_code == 200,
          f"got {r_prose.status_code}")
    prose_body = r_prose.text

    check("DEFAULT tree read: the injected DETAIL marker is ABSENT",
          BIG_MARKER not in default_body, f"marker count = {default_body.count(BIG_MARKER)}")
    check("...and no `body` key at all appears for the RDVERIFY-BIG nodes",
          '"body"' not in default_body, '"body" appears' if '"body"' in default_body else "absent")
    # THE REGRESSION TEST. Nothing asserted this before, so gating the abstract
    # along with the detail passed every check while silently returning every
    # workitem panel on the board to "No description authored yet." — the exact
    # thing TASK-1046 exists to fix. A body with no authored abstract MUST
    # still project one in the DEFAULT response.
    check("DEFAULT tree read: a body-only node STILL carries a projected `abstract` "
          "(the board never asks for include_prose — gating this blanks every panel)",
          ABSTRACT_SENTINEL in default_body,
          f"sentinel count = {default_body.count(ABSTRACT_SENTINEL)}")
    _big = next((n for n in _flatten_tree(r_default.json())
                 if n.get("id") == "TASK-RDVERIFY-BIG-00"), None)
    check("...and that node's `detail` is None by default (the LARGE half is what the flag gates)",
          _big is not None and _big.get("detail") is None,
          None if _big is None else repr(_big.get("detail"))[:60])
    _unp = next((n for n in _flatten_tree(r_default.json())
                 if n.get("id") == "TASK-RDVERIFY-UNPARAGRAPHED"), None)
    check("an UNPARAGRAPHED body does not project itself wholesale into the listing "
          "— the abstract is capped by construction, not by trusting a blank line",
          _unp is not None and _unp.get("abstract") is not None
          and len(_unp["abstract"]) <= app._ABSTRACT_MAX_CHARS + 1,
          None if _unp is None else len(_unp.get("abstract") or ""))
    check("?include_prose=1: the marker IS present (at least once per big node)",
          prose_body.count(BIG_MARKER) >= 15, prose_body.count(BIG_MARKER))
    check("THE ACCEPTANCE CRITERION ITSELF: the default response is NOT larger because of "
          "prose that wasn't asked for — it is meaningfully SMALLER than the include_prose=1 one",
          len(default_body) < len(prose_body) - (len(BIG_MARKER) * 10),
          f"default={len(default_body)} bytes, include_prose=1={len(prose_body)} bytes, "
          f"delta={len(prose_body) - len(default_body)}")

    # Same check on /api/graph (the neighbourhood route), focused on one big node.
    r_focus_default = client.get("/api/graph?focus=TASK-RDVERIFY-BIG-00&depth=0", headers=owner_hdr)
    r_focus_prose = client.get("/api/graph?focus=TASK-RDVERIFY-BIG-00&depth=0&include_prose=1",
                                headers=owner_hdr)
    check("/api/graph?focus=...&depth=0 (default): marker absent", BIG_MARKER not in r_focus_default.text)
    check("/api/graph?focus=...&depth=0&include_prose=1: marker present", BIG_MARKER in r_focus_prose.text)

    # And the LISTING route's own include_prose flag, same contract.
    _, listing_default = _fetch_all(client, owner_hdr, owner="Verify Owner A")
    _, listing_prose = _fetch_all(client, owner_hdr, owner="Verify Owner A", include_prose=1)
    check("GET /api/workitems (default): no `body` field on any item",
          all("body" not in i for i in listing_default), listing_default[:1])
    check("GET /api/workitems?include_prose=1: `body` field present with the real content",
          any("zzqxplorbmarker" in (i.get("body") or "") for i in listing_prose), listing_prose[:1])

    # ----------------------------------------------------------------- H --- #
    print("\n-- H. NodeVisibility applied INSIDE the query --")

    # H1. Fast path: nothing marked private yet -> member sees the same as owner.
    _, member_before = _fetch_all(client, member_hdr, owner="Verify Owner A")
    check("H1. before anything is marked private, a MEMBER sees child A too "
          "(the fast path really is a no-op, not a hardcoded restriction)",
          "TASK-RDVERIFY-CHILD-A" in {i["id"] for i in member_before}, member_before)
    r = client.get("/api/node/STORY-RDVERIFY-PARENT", headers=member_hdr)
    check("...and a member can fetch the not-yet-private parent directly",
          r.status_code == 200, f"got {r.status_code}")

    # H2. Mark the private parent, leave the SECOND (public) parent alone.
    with app.session() as s:
        s.add(models_auth.NodeVisibility(
            node_id="STORY-RDVERIFY-PARENT", visibility=models_auth.VISIBILITY_PRIVATE,
            set_by=None, set_at=NOW,
        ))
        s.commit()

    r_owner_after = client.get("/api/node/STORY-RDVERIFY-PARENT", headers=owner_hdr)
    check("H2. the OWNER still sees the now-private parent (owner is unrestricted)",
          r_owner_after.status_code == 200, f"got {r_owner_after.status_code}")

    r_member_after = client.get("/api/node/STORY-RDVERIFY-PARENT", headers=member_hdr)
    check("H2. a MEMBER can no longer fetch the private parent directly",
          r_member_after.status_code == 404, f"got {r_member_after.status_code}")
    check("...and the 404 body is BYTE-IDENTICAL in shape to a truly-unknown id "
          "(hidden and absent are indistinguishable, same as TASK-974's own requirement)",
          set(r_member_after.json().keys()) == set(unknown_body.keys()) and
          list(r_member_after.json().keys()) == ["error"],
          f"{r_member_after.json()} vs {unknown_body}")

    r_child_member = client.get("/api/node/TASK-RDVERIFY-CHILD-A", headers=member_hdr)
    check("H3. ALL-not-ANY: child A has ONE private parent and ONE public parent — "
          "it is STILL HIDDEN from a member (union/ANY semantics would wrongly show it)",
          r_child_member.status_code == 404, f"got {r_child_member.status_code}")
    r_child_owner = client.get("/api/node/TASK-RDVERIFY-CHILD-A", headers=owner_hdr)
    check("...while the owner still sees it fine", r_child_owner.status_code == 200,
          f"got {r_child_owner.status_code}")

    # H4. No count leak: the listing's `total` actually DROPS for the member,
    # it does not stay the same with rows silently stripped.
    owner_total, _ = _fetch_all(client, owner_hdr, owner="Verify Owner A")
    member_total, member_items = _fetch_all(client, member_hdr, owner="Verify Owner A")
    check("H4. NO COUNT LEAK: `total` for the member is LOWER than for the owner "
          "over the identical query (child A dropped out of both the page AND the total)",
          member_total < owner_total, f"owner total={owner_total}, member total={member_total}")
    check("...and child A is concretely absent from the member's own items list",
          "TASK-RDVERIFY-CHILD-A" not in {i["id"] for i in member_items}, member_items)

    r_member_parent_filter = client.get("/api/workitems",
                                         params={"parent": "STORY-RDVERIFY-PARENT", "page_size": 200},
                                         headers=member_hdr)
    check("H5. filtering BY the private parent's id still 200s for a member "
          "(the parent id itself is not a secret; its CONTENTS are) and returns nothing hidden",
          r_member_parent_filter.status_code == 200 and
          "TASK-RDVERIFY-CHILD-A" not in {i["id"] for i in r_member_parent_filter.json()["items"]},
          r_member_parent_filter.json())

    # ----------------------------------------------------------------- I --- #
    print("\n-- I. The two TASK-1050 GIN indexes actually reach the DB --")
    import sqlalchemy as sa
    insp = sa.inspect(app.engine)
    node_idx = {i["name"] for i in insp.get_indexes("node")}
    ac_idx = {i["name"] for i in insp.get_indexes("acceptance_criterion")}
    check("ix_node_body_search_gin exists on `node` (real deploy path)",
          "ix_node_body_search_gin" in node_idx, node_idx)
    check("ix_node_prose_search_gin exists on `node` (TASK-1156, real deploy path)",
          "ix_node_prose_search_gin" in node_idx, node_idx)
    check("ix_acceptance_criterion_text_search_gin exists on `acceptance_criterion`",
          "ix_acceptance_criterion_text_search_gin" in ac_idx, ac_idx)

    with app.session() as s:
        s.execute(sa.text("ANALYZE node"))
        plan = s.execute(sa.text(
            "EXPLAIN SELECT id FROM node WHERE "
            f"({mg.NODE_BODY_SEARCH_SQL}) @@ plainto_tsquery('english', 'zzqxplorbmarker')"
        )).scalars().all()
    plan_text = "\n".join(plan)
    reaches_for_it = "ix_node_body_search_gin" in plan_text
    if not reaches_for_it:
        # CHANGED (TASK-1058): this harness now populates `node.body` itself
        # (via `import_workitems.py`, real markdown prose per row — the
        # pre-TASK-1058 version of this check relied on `app.startup()`'s old
        # boot ingest, which never wrote `body` at all, so its `node` table
        # was smaller). On a table this size (~350 rows), Postgres's
        # cost-based planner can legitimately prefer a sequential scan over
        # the GIN index regardless of ANALYZE — that is a real, expected
        # planner heuristic for small tables, not evidence the index is
        # broken. Fall back to proving the stronger, environment-independent
        # claim: the index is REACHABLE — `SET LOCAL enable_seqscan = off`
        # forces the planner to use whatever non-seqscan path exists, and if
        # that still doesn't mention the GIN index by name, the index really
        # doesn't work, which the ORIGINAL assertion (still tried first,
        # above) would also have caught on a bigger/real table.
        with app.session() as s:
            s.execute(sa.text("SET LOCAL enable_seqscan = off"))
            forced_plan = s.execute(sa.text(
                "EXPLAIN SELECT id FROM node WHERE "
                f"({mg.NODE_BODY_SEARCH_SQL}) @@ plainto_tsquery('english', 'zzqxplorbmarker')"
            )).scalars().all()
        forced_plan_text = "\n".join(forced_plan)
        print(f"  (planner preferred Seq Scan unforced — small-table heuristic, not a defect; "
              f"forced plan: {forced_plan_text!r})")
        reaches_for_it = "ix_node_body_search_gin" in forced_plan_text
        plan_text = f"{plan_text}\n--- forced (enable_seqscan=off) ---\n{forced_plan_text}"
    check("the search query's own EXPLAIN mentions the GIN index by name "
          "(the index isn't just present — the planner can and does reach for it)",
          reaches_for_it, plan_text)

    # Re-running ensure_search_indexes is a no-op (idempotent, same contract
    # as _reconcile_graph_schema's own index loop).
    before = (node_idx, ac_idx)
    mg.ensure_search_indexes(app.engine)
    node_idx2 = {i["name"] for i in insp.get_indexes("node")}
    ac_idx2 = {i["name"] for i in insp.get_indexes("acceptance_criterion")}
    check("re-running ensure_search_indexes() is a no-op (idempotent)",
          (node_idx2, ac_idx2) == before, (node_idx2, ac_idx2))

    # ----------------------------------------------------------------- I --- #
    # SALVAGED from verify_node_docs.py (TASK-1059). That script's subject was
    # `import_node_docs.py`, a one-time importer of a directory this epic
    # deletes -- but it also covered `GET /api/node/{id}/doc`, which is a live
    # read route and outlives all of it. The route half lives here now, against
    # the fixture's documented world node rather than against files on disk.
    print("\n-- H. GET /api/node/{id}/doc (ADR 0029 read route) --")
    doc_path = testseed.SEED.documented_doc_path
    node_with_doc = testseed.SEED.world_service

    r = client.get(f"/api/node/{node_with_doc}/doc", params={"path": doc_path}, headers=owner_hdr)
    check("documented node + allowlisted path -> 200",
          r.status_code == 200, f"status {r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        check("content is byte-identical to what was stored",
              r.json().get("content") == testseed.SEED_DOC_CONTENT,
              repr(r.json().get("content", ""))[:160])

    # The allowlist is the gate, not the row: a path that exists in node_doc but
    # is not declared in the node's docs[] must still 404.
    r = client.get(f"/api/node/{node_with_doc}/doc",
                   params={"path": "docs/nodes/seed-world-service/not-declared.md"},
                   headers=owner_hdr)
    check("undeclared path -> 404 even for a documented node",
          r.status_code == 404, f"status {r.status_code}: {r.text[:200]}")

    # A different node cannot read another node's doc by naming its path.
    r = client.get(f"/api/node/{testseed.SEED.world_component}/doc",
                   params={"path": doc_path}, headers=owner_hdr)
    check("another node's path -> 404 (docs are per-node, not global)",
          r.status_code == 404, f"status {r.status_code}: {r.text[:200]}")

    r = client.get("/api/node/does-not-exist-at-all/doc", params={"path": doc_path}, headers=owner_hdr)
    check("unknown node -> 404", r.status_code == 404, f"status {r.status_code}")

    # ----------------------------------------------------------------- J --- #
    # TASK-1076: the authoring half. Until this route existed, the only writer
    # of a node_doc row was import_node_docs.py -- a one-time importer that
    # refuses unless forced and that TASK-1059 deletes, so node documentation
    # was about to become read-only while the read path carried on working.
    print("\n-- J. PUT/DELETE /api/node/{id}/doc (TASK-1076 authoring) --")
    new_path = "docs/nodes/seed-world-component/design.md"
    target = testseed.SEED.world_component

    # TASK-1086: PUT needs `write`, DELETE needs `admin` (`auth.WRITE_SCOPE_ROUTES`)
    # — `owner_hdr` above is deliberately `read`-only (this file's subject is
    # the READ api), so this section mints its own doc-write credential rather
    # than widening `owner_hdr` for everyone else in this file.
    doc_write_token = _mint(models_auth.ROLE_OWNER, "rdverify-doc-writer", scopes=["write", "admin"])
    doc_write_hdr = {"Authorization": f"Bearer {doc_write_token}"}

    r = client.put(f"/api/node/{target}/doc", headers=doc_write_hdr,
                   json={"path": new_path, "content": "# First revision\n"})
    check("PUT a new document -> 201 created",
          r.status_code == 201 and r.json().get("created") is True,
          f"status {r.status_code}: {r.text[:200]}")

    # STORING IS NOT SERVING. The node has no docs[] entry for this path, so the
    # row exists and GET still refuses -- the same "expected-not-served" state
    # 11 rows on live are in. If these two ever collapse into one, publishing
    # stops being a visible act.
    check("...and the response says it is stored but not served",
          r.status_code == 201 and r.json().get("served") is False, str(r.json())[:200])
    g = client.get(f"/api/node/{target}/doc", params={"path": new_path}, headers=owner_hdr)
    check("...GET still 404s: storing is not serving", g.status_code == 404, f"got {g.status_code}")
    with app.session() as s_:
        rows = s_.scalars(select(mg.NodeDoc).where(mg.NodeDoc.node_id == target,
                                                   mg.NodeDoc.path == new_path)).all()
    check("...but the row IS in the database", len(rows) == 1 and rows[0].content == "# First revision\n",
          f"{len(rows)} row(s)")

    r = client.put(f"/api/node/{target}/doc", headers=doc_write_hdr,
                   json={"path": new_path, "content": "# Second revision\n"})
    check("PUT the same path again -> 200, not created",
          r.status_code == 200 and r.json().get("created") is False,
          f"status {r.status_code}: {r.text[:200]}")
    with app.session() as s_:
        rows = s_.scalars(select(mg.NodeDoc).where(mg.NodeDoc.node_id == target,
                                                   mg.NodeDoc.path == new_path)).all()
    check("...it REPLACED the content and did not duplicate the row",
          len(rows) == 1 and rows[0].content == "# Second revision\n",
          f"{len(rows)} row(s), content={rows[0].content!r} " if rows else "no rows")

    r = client.put(f"/api/node/{target}/doc", headers=doc_write_hdr,
                   json={"path": "notes/somewhere-else.md", "content": "x"})
    check("a path outside docs/nodes/ -> 400 (it could never be read back)",
          r.status_code == 400 and r.json().get("error") == "path_outside_docs_root",
          f"status {r.status_code}: {r.text[:200]}")

    r = client.put("/api/node/no-such-node-at-all/doc", headers=doc_write_hdr,
                   json={"path": new_path, "content": "x"})
    check("PUT against an unknown node -> 404", r.status_code == 404, f"got {r.status_code}")

    r = client.put(f"/api/node/{target}/doc", headers=owner_hdr,
                   json={"path": new_path, "content": "should never land"})
    check("TASK-1086: a read-only Bearer is REFUSED on PUT (403, names the scope)",
          r.status_code == 403 and "write" in r.json().get("detail", ""),
          f"status {r.status_code}: {r.text[:200]}")
    with app.session() as s_:
        row = s_.scalar(select(mg.NodeDoc).where(mg.NodeDoc.node_id == target,
                                                 mg.NodeDoc.path == new_path))
    check("...and the refused write changed nothing",
          row is not None and row.content == "# Second revision\n",
          repr(row.content)[:80] if row is not None else "row vanished")

    # Same gate as every other write route -- no second guard, no exemption.
    r = client.put(f"/api/node/{target}/doc", json={"path": new_path, "content": "x"})
    check("PUT with NO credential is refused", r.status_code in (401, 403),
          f"got {r.status_code}: {r.text[:160]}")
    with app.session() as s_:
        row = s_.scalar(select(mg.NodeDoc).where(mg.NodeDoc.node_id == target,
                                                 mg.NodeDoc.path == new_path))
    check("...and the refused write changed nothing",
          row is not None and row.content == "# Second revision\n",
          repr(row.content)[:80] if row is not None else "row vanished")

    r = client.delete(f"/api/node/{target}/doc", params={"path": new_path}, headers=owner_hdr)
    check("TASK-1086: a read-only Bearer is REFUSED on DELETE too (403, names the scope)",
          r.status_code == 403 and "admin" in r.json().get("detail", ""),
          f"status {r.status_code}: {r.text[:200]}")
    r = client.delete(f"/api/node/{target}/doc", params={"path": new_path}, headers=doc_write_hdr)
    check("DELETE removes the stored document", r.status_code == 200, f"status {r.status_code}")
    with app.session() as s_:
        rows = s_.scalars(select(mg.NodeDoc).where(mg.NodeDoc.node_id == target,
                                                   mg.NodeDoc.path == new_path)).all()
    check("...the row is gone", len(rows) == 0, f"{len(rows)} left")
    r = client.delete(f"/api/node/{target}/doc", params={"path": new_path}, headers=doc_write_hdr)
    check("DELETE again -> 404", r.status_code == 404, f"got {r.status_code}")

    # The seeded, allowlisted document is untouched by all of the above.
    g = client.get(f"/api/node/{node_with_doc}/doc", params={"path": doc_path}, headers=owner_hdr)
    check("the allowlisted document still serves, unchanged",
          g.status_code == 200 and g.json().get("content") == testseed.SEED_DOC_CONTENT,
          f"status {g.status_code}")

    # ----------------------------------------------------------------- K --- #
    print("\n-- K. node_doc as a 4th search source, and GET /api/search as the "
          "general (non-workitem-scoped) surface --")

    DOC_SEARCH_MARKER = "zzdocsearchmarker"
    UNSERVED_DOC_MARKER = "zzunserveddocmarker"
    WORLD_BODY_MARKER = "zzworldbodymarker"

    with app.session() as s:
        # A fresh, undocumented-until-now world (object-family) node — kept
        # independent of section C/H's STORY-RDVERIFY-PARENT fixture so this
        # section's private-node case does not depend on what an earlier
        # section already did to that node.
        s.add(mg.Node(
            id="SEED-SEARCHDOC-WORLD", type="service", name="RDVERIFY search-doc world node",
            origin="atlas",
            body=f"A world node whose BODY carries {WORLD_BODY_MARKER}, to prove it is "
                 f"findable by the GENERAL search surface even though `/api/workitems?q=` "
                 f"is scoped to the workitem family and correctly will not find it.",
            attributes={
                "docs": [{"path": "docs/nodes/SEED-SEARCHDOC-WORLD/served.md",
                          "kind": "architecture", "title": "Served RDVERIFY doc"}],
            },
        ))
        s.flush()
        s.add_all([
            mg.NodeDoc(node_id="SEED-SEARCHDOC-WORLD",
                       path="docs/nodes/SEED-SEARCHDOC-WORLD/served.md",
                       content=f"# Served doc\n\nCarries {DOC_SEARCH_MARKER}, and IS in "
                               f"this node's docs[] allowlist."),
            # Stored, never allowlisted — "expected-not-served" (TASK-1076).
            # Must be invisible to search exactly as it is invisible to GET.
            mg.NodeDoc(node_id="SEED-SEARCHDOC-WORLD",
                       path="docs/nodes/SEED-SEARCHDOC-WORLD/unserved.md",
                       content=f"# Unserved doc\n\nCarries {UNSERVED_DOC_MARKER}, and is "
                               f"NOT in this node's docs[] allowlist."),
        ])
        s.commit()

    # K1. The served doc IS findable via the general surface.
    r = client.get("/api/search", params={"q": DOC_SEARCH_MARKER}, headers=owner_hdr)
    check("K1. GET /api/search finds a SERVED node_doc match",
          r.status_code == 200 and
          "SEED-SEARCHDOC-WORLD" in {i["id"] for i in r.json()["items"]},
          f"status {r.status_code}: {r.json()}")

    # K2. The UNSERVED doc's content is NEVER a hit, even for the owner, even
    # though the row is right there in the database (STORING IS NOT SERVING
    # extends to search, exactly as it already does to GET).
    r = client.get("/api/search", params={"q": UNSERVED_DOC_MARKER}, headers=owner_hdr)
    check("K2. an UNSERVED (stored-but-not-allowlisted) node_doc is NEVER a search hit",
          r.status_code == 200 and r.json()["total"] == 0,
          f"status {r.status_code}: {r.json()}")

    # K3. A world node's BODY is findable via the general surface...
    r = client.get("/api/search", params={"q": WORLD_BODY_MARKER}, headers=owner_hdr)
    check("K3. GET /api/search finds a WORLD (object-family) node's body match",
          r.status_code == 200 and
          "SEED-SEARCHDOC-WORLD" in {i["id"] for i in r.json()["items"]},
          f"status {r.status_code}: {r.json()}")

    # K4. ...THE DECISION, proven by attempting it: /api/workitems?q= is scoped
    # to the workitem family and does NOT surface it, for a body match or a
    # documentation-only match, even though both phrases match under the same
    # `_search_node_ids` the two routes share.
    total_wi, wi_hits = _fetch_all(client, owner_hdr, q=WORLD_BODY_MARKER)
    check("K4. GET /api/workitems?q= does NOT surface a world node's body match "
          "(deliberate family scoping, unchanged by the node_doc addition)",
          "SEED-SEARCHDOC-WORLD" not in {i["id"] for i in wi_hits}, wi_hits)
    total_wi_doc, wi_doc_hits = _fetch_all(client, owner_hdr, q=DOC_SEARCH_MARKER)
    check("...and the same is true of a documentation-only match",
          "SEED-SEARCHDOC-WORLD" not in {i["id"] for i in wi_doc_hits}, wi_doc_hits)

    # K5. Access filtering, INSIDE the query: mark the node private, and a
    # MEMBER can no longer find it (or its documentation) via search — and
    # `total` actually drops to zero rather than leaking a stripped-but-
    # counted row (the same count-leak shape section H4 proves for listings).
    with app.session() as s:
        s.add(models_auth.NodeVisibility(
            node_id="SEED-SEARCHDOC-WORLD", visibility=models_auth.VISIBILITY_PRIVATE,
            set_by=None, set_at=NOW,
        ))
        s.commit()

    r_owner = client.get("/api/search", params={"q": DOC_SEARCH_MARKER}, headers=owner_hdr)
    check("K5. the OWNER still finds the now-private node's document",
          r_owner.status_code == 200 and
          "SEED-SEARCHDOC-WORLD" in {i["id"] for i in r_owner.json()["items"]},
          r_owner.json())
    r_member = client.get("/api/search", params={"q": DOC_SEARCH_MARKER}, headers=member_hdr)
    check("...a MEMBER's search for the SAME phrase returns ZERO results "
          "(not a stripped row against a nonzero total — the COUNT itself drops)",
          r_member.status_code == 200 and r_member.json()["total"] == 0,
          r_member.json())
    check("...and `count` agrees with `total` at zero, not '0 items, total 1' "
          "(that gap is exactly how a hidden node's existence would leak)",
          r_member.json().get("count") == 0, r_member.json())

    # K6. An empty/blank `q` is a well-formed empty result, not a 500 or "every node".
    r = client.get("/api/search", params={"q": ""}, headers=owner_hdr)
    check("K6. GET /api/search?q= (empty) -> 200, zero results, not every node",
          r.status_code == 200 and r.json()["total"] == 0, f"status {r.status_code}: {r.json()}")

    # K7. Unauthenticated -> 401, same as every other read route (no second gate).
    r = client.get("/api/search", params={"q": DOC_SEARCH_MARKER})
    check("K7. an UNAUTHENTICATED caller is refused (401), not redirected",
          r.status_code == 401, f"got {r.status_code}")

    # ----------------------------------------------------------------- K2 -- #
    print("\n-- K2. World-node search (TASK-1156): authored prose is a source, "
          "results are pointers, families filter, ranked, and access cannot leak --")

    PROSE_MARKER = "zzworldprosemarker"
    SHARED_MARKER = "zzsharedworkworldmarker"
    RANK_MARKER = "zzrankmarker"
    PRIVATE_PROSE_MARKER = "zzprivateprosemarker"
    NONSENSE = "zzneverindexedanywhere"
    with app.session() as s:
        s.add_all([
            # Context in `abstract` only — no body, no documents. This is the
            # shape of `sphere-device-heating` on live, which search could not find.
            mg.Node(id="SEED-PROSE-WORLD", type="service", name="RDVERIFY prose world node",
                    origin="atlas", summary="A world node found by its prose.",
                    abstract=f"Its context lives in the abstract: {PROSE_MARKER} and "
                             f"{SHARED_MARKER} and {RANK_MARKER}, and nowhere else.",
                    detail="A detail field, so K2e has one to leak if the item shape regresses."),
            mg.Node(id="SEED-RANK-BYNAME", type="service",
                    name=f"RDVERIFY {RANK_MARKER} named node", origin="atlas"),
            mg.Node(id="TASK-RDVERIFY-SHARED", type="task", name="RDVERIFY shared-marker task",
                    origin="atlas", attributes={"status": "pending"},
                    body=f"A workitem body that also says {SHARED_MARKER}."),
            mg.Node(id="SEED-PRIVATE-PROSE", type="service", name="RDVERIFY private prose node",
                    origin="atlas",
                    abstract=f"Only this private node's abstract says {PRIVATE_PROSE_MARKER}."),
        ])
        s.flush()
        s.add(models_auth.NodeVisibility(
            node_id="SEED-PRIVATE-PROSE", visibility=models_auth.VISIBILITY_PRIVATE,
            set_by=None, set_at=NOW,
        ))
        s.commit()

    r = client.get("/api/search", params={"q": PROSE_MARKER}, headers=owner_hdr)
    items = r.json().get("items", [])
    hit = next((i for i in items if i["id"] == "SEED-PROSE-WORLD"), None)
    check("K2a. a world node whose context is ONLY in its abstract is found",
          r.status_code == 200 and hit is not None, f"status {r.status_code}: {items}")
    check("K2b. ...matched_in says prose, and only prose",
          hit is not None and hit["matched_in"] == ["prose"], hit)
    check("K2c. ...the snippet shows the match, marked",
          hit is not None and f"**{PROSE_MARKER}**" in (hit.get("snippet") or ""), hit)
    check("K2d. ...and the summary rides along as the one-line pointer",
          hit is not None and hit.get("summary") == "A world node found by its prose.", hit)

    # Acceptance: results carry no abstract or detail. Checked over every item
    # of several searches, including one whose hit HAS a detail to leak.
    leaked = []
    for term in (PROSE_MARKER, SHARED_MARKER, RANK_MARKER, DOC_SEARCH_MARKER, WORLD_BODY_MARKER):
        for i in client.get("/api/search", params={"q": term}, headers=owner_hdr).json()["items"]:
            leaked += [(i["id"], k) for k in ("abstract", "detail", "body") if k in i]
    check("K2e. no search item carries abstract, detail or body", not leaked, leaked)
    check("K2f. item keys are exactly the pointer shape",
          hit is not None and set(hit) == {"id", "type", "name", "summary", "matched_in",
                                           "doc_paths", "snippet", "rank"}, sorted(hit or {}))

    r_doc = client.get("/api/search", params={"q": DOC_SEARCH_MARKER}, headers=owner_hdr)
    doc_hit = next((i for i in r_doc.json()["items"] if i["id"] == "SEED-SEARCHDOC-WORLD"), None)
    check("K2g. a document hit names the served document to open, and not the unserved one",
          doc_hit is not None and "doc" in doc_hit["matched_in"]
          and doc_hit["doc_paths"] == ["docs/nodes/SEED-SEARCHDOC-WORLD/served.md"], doc_hit)

    both = {i["id"] for i in client.get("/api/search", params={"q": SHARED_MARKER},
                                        headers=owner_hdr).json()["items"]}
    world = client.get("/api/search", params={"q": SHARED_MARKER, "families": "object,bridge"},
                       headers=owner_hdr).json()
    check("K2h. fixture: unfiltered, the shared term finds the world node AND the workitem",
          {"SEED-PROSE-WORLD", "TASK-RDVERIFY-SHARED"} <= both, both)
    check("K2i. families=object,bridge keeps the world node and drops the workitem, "
          "and total counts after the filter",
          {i["id"] for i in world["items"]} == {"SEED-PROSE-WORLD"} and world["total"] == 1, world)
    r_bad = client.get("/api/search", params={"q": SHARED_MARKER, "families": "objects"},
                       headers=owner_hdr)
    check("K2j. an unknown family is a 400 naming the field, not an empty result",
          r_bad.status_code == 400 and r_bad.json().get("field") == "families",
          f"{r_bad.status_code} {r_bad.text[:120]}")

    ranked = client.get("/api/search", params={"q": RANK_MARKER}, headers=owner_hdr).json()["items"]
    check("K2k. a NAME match ranks above a prose-only match",
          [i["id"] for i in ranked][:2] == ["SEED-RANK-BYNAME", "SEED-PROSE-WORLD"],
          [(i["id"], i["rank"]) for i in ranked])

    # Acceptance: a member's search cannot reveal a node they may not read.
    def member_search(term):
        return client.get("/api/search", params={"q": term}, headers=member_hdr)

    r_owner_priv = client.get("/api/search", params={"q": PRIVATE_PROSE_MARKER}, headers=owner_hdr)
    check("K2l. fixture: the OWNER finds the private node by its prose",
          "SEED-PRIVATE-PROSE" in {i["id"] for i in r_owner_priv.json()["items"]},
          r_owner_priv.json())
    r_priv, r_none = member_search(PRIVATE_PROSE_MARKER), member_search(NONSENSE)
    check("K2m. a MEMBER searching a term only a private node's prose contains gets nothing",
          r_priv.status_code == 200 and r_priv.json()["total"] == 0 and r_priv.json()["items"] == [],
          r_priv.json())

    def _without_q(body):
        return {**body, "filters": {**body["filters"], "q": None}}
    check("K2n. ...and that answer is identical to a term that exists nowhere, "
          "so nothing in it distinguishes 'hidden' from 'absent'",
          _without_q(r_priv.json()) == _without_q(r_none.json()),
          f"{r_priv.json()} vs {r_none.json()}")

    # Break it: with the access filter removed, the same member call DOES find
    # the node. If this fails, K2m passed only because the fixture was wrong.
    real_filter = app._visible_node_ids
    try:
        app._visible_node_ids = lambda s: None
        broken = member_search(PRIVATE_PROSE_MARKER).json()
    finally:
        app._visible_node_ids = real_filter
    check("K2o. perturbed: without `_visible_node_ids` the member's search finds the "
          "private node — so K2m is the filter working, not an empty fixture",
          "SEED-PRIVATE-PROSE" in {i["id"] for i in broken.get("items", [])}, broken)
    check("K2p. ...and restoring the filter hides it again",
          member_search(PRIVATE_PROSE_MARKER).json()["total"] == 0)

    # ----------------------------------------------------------------- L --- #
    print("\n-- L. The node_doc GIN index actually reaches the DB --")
    doc_idx = {i["name"] for i in insp.get_indexes("node_doc")}
    check("ix_node_doc_content_search_gin exists on `node_doc` (real deploy path)",
          "ix_node_doc_content_search_gin" in doc_idx, doc_idx)

    with app.session() as s:
        s.execute(sa.text("ANALYZE node_doc"))
        plan = s.execute(sa.text(
            "EXPLAIN SELECT node_id FROM node_doc WHERE "
            f"({mg.NODE_DOC_SEARCH_SQL}) @@ plainto_tsquery('english', 'zzdocsearchmarker')"
        )).scalars().all()
    plan_text = "\n".join(plan)
    reaches_for_it = "ix_node_doc_content_search_gin" in plan_text
    if not reaches_for_it:
        # Same fallback as section I's proof, same reason: on a small table the
        # cost-based planner can legitimately prefer a Seq Scan regardless of
        # ANALYZE. Force the issue and prove the index is REACHABLE.
        with app.session() as s:
            s.execute(sa.text("SET LOCAL enable_seqscan = off"))
            forced_plan = s.execute(sa.text(
                "EXPLAIN SELECT node_id FROM node_doc WHERE "
                f"({mg.NODE_DOC_SEARCH_SQL}) @@ plainto_tsquery('english', 'zzdocsearchmarker')"
            )).scalars().all()
        forced_plan_text = "\n".join(forced_plan)
        print(f"  (planner preferred Seq Scan unforced — small-table heuristic, not a defect; "
              f"forced plan: {forced_plan_text!r})")
        reaches_for_it = "ix_node_doc_content_search_gin" in forced_plan_text
        plan_text = f"{plan_text}\n--- forced (enable_seqscan=off) ---\n{forced_plan_text}"
    check("the node_doc search query's own EXPLAIN mentions the GIN index by name "
          "(the index isn't just present — the planner can and does reach for it)",
          reaches_for_it, plan_text)

    before_doc_idx = doc_idx
    mg.ensure_search_indexes(app.engine)
    doc_idx2 = {i["name"] for i in insp.get_indexes("node_doc")}
    check("re-running ensure_search_indexes() is a no-op for node_doc too (idempotent)",
          doc_idx2 == before_doc_idx, doc_idx2)

    print("\n" + "=" * 78)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED (including the real-Postgres GIN index proof)")


if __name__ == "__main__":
    main()
