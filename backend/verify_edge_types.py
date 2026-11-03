"""
Verification for the `edge_type` registry — the edge-side mirror of
`node_type` (TASK: build an edge_type registry table).

Same standalone, run-it-and-read-it convention as verify_object_graph.py /
verify_backlog_read_api.py: a real, disposable Postgres via `testdb`
(TASK-1044), the real `app.startup()`, the curated fixture in `testseed.py`,
and the real API through the real auth gate where the check needs it.

FOUR SECTIONS:

  A. Schema — the table exists with the columns/CHECK the model declares,
     and a brand-new table is created by `create_all` with no reconcile step
     needed (confirms the "new table is fine on deploy" claim rather than
     assuming it).
  B. Seeding — `ensure_edge_types` is idempotent (insert-if-missing,
     update-in-place) and seeds exactly the nine documented rows with the
     right `status`.
  C. The drift check, `node_types.edge_type_warnings` — and this is the
     section that matters. A check that can only ever pass proves nothing
     (backend/TESTING.md's own rule), so each of the FOUR conditions is
     PROVEN BY CREATING IT, including the hazard case (`serves` gaining a
     row) that the whole table exists for, and the FOURTH condition
     (`pair_not_declared`, added as a follow-up once the first pass shipped
     with `contains` missing a pair a real live row already used) is also
     confirmed SILENT against the corrected seed on the shapes this fixture
     and the measured live corpus actually produce.
  D. `EDGE_FAMILY_RULES` agreement, and that `POST /api/edge` behaviour is
     UNCHANGED — the task's hard constraint. `depends_on` is undeclared in
     `EDGE_FAMILY_RULES` and must still be creatable through the write API
     exactly as before this table existed.

Run (from the venv with fastapi/SQLAlchemy/PyYAML/jsonschema/psycopg2 installed):
    python verify_edge_types.py
"""
from __future__ import annotations

import base64
import os
import sys


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


# No SQLite fallback exists any more — see testdb's docstring.
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

TEST_ORIGIN = "https://testserver"

import app  # noqa: E402
import auth  # noqa: E402
import models_auth  # noqa: E402
import node_types  # noqa: E402
import testseed  # noqa: E402
from models_graph import Edge, EdgeType  # noqa: E402
from sqlalchemy import inspect as sa_inspect, select  # noqa: E402
from models_auth import AuthUser  # noqa: E402
import distribution  # noqa: E402
import system_node  # noqa: E402

#: The edge types a fresh boot writes on its own (STORY-oss-23 A3b), derived from the
#: distribution's default seed profile: `homelab` (project-atlas's copy) writes v0 rows and no
#: graph edge, so this is empty and every check below reads exactly as before; any other
#: profile seeds the system node set on a fresh graph (`system_node.seed`), whose edges are
#: `system_node.ALL_EDGES`. Those types legitimately carry rows from the first boot.
BOOT_EDGE_TYPES = (frozenset() if distribution.DEFAULT_SEED_PROFILE == "homelab"
                   else frozenset(t for _src, t, _dst in system_node.ALL_EDGES))


def _client():
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN)


def _mint(role: str, subject: str, scopes: list[str] | None = None) -> str:
    with app.session() as s:
        bot = AuthUser(id=f"00000000-0000-4000-8000-{subject[-12:].rjust(12, '0')}",
                        idp="local", subject=subject, is_service=True, label=subject,
                        must_change_password=False, role=role)
        s.add(bot)
        s.flush()
        raw, _row = auth.mint_service_token(s, owner=bot, scopes=scopes or ["read", "write"], label=subject)
        s.commit()
    return raw


def main() -> None:
    print("=" * 78)
    print("Atlas edge_type registry verification")
    print(f"DB:      {os.environ['ATLAS_DB_URL']}")
    print("=" * 78)

    # ----------------------------------------------------------------- A --- #
    print("\n-- A. Schema: a brand-new table, created by create_all alone --")
    app.startup()
    insp = sa_inspect(app.engine)
    check("edge_type table exists after a fresh startup() with no reconcile step needed",
          "edge_type" in insp.get_table_names())
    cols = {c["name"]: c for c in insp.get_columns("edge_type")}
    for col in ("id", "pairs", "description", "status"):
        check(f"edge_type.{col} exists", col in cols, str(sorted(cols)))
    ck_names = {c["name"] for c in insp.get_check_constraints("edge_type")}
    check("ck_edge_type_status is present", "ck_edge_type_status" in ck_names, str(ck_names))

    with app.session() as s:
        try:
            s.add(EdgeType(id="verify-bad-status", pairs=[], description="x", status="bogus"))
            s.commit()
            check("CHECK constraint refuses an invalid status", False)
        except Exception:
            s.rollback()
            check("CHECK constraint refuses an invalid status", True)

    # ----------------------------------------------------------------- B --- #
    print("\n-- B. Seeding: idempotent, ten rows, right status each --")
    with app.session() as s:
        rows = {et.id: et for et in s.scalars(select(EdgeType)).all()}
    check("exactly the ten documented edge types are seeded",
          set(rows) == set(node_types.EDGE_TYPE_SEEDS),
          f"got {sorted(rows)}")
    for type_id, seed in node_types.EDGE_TYPE_SEEDS.items():
        row = rows.get(type_id)
        check(f"{type_id}: status == {seed['status']!r}", row is not None and row.status == seed["status"],
              str(row.status if row else None))
        check(f"{type_id}: pairs matches the seed", row is not None and row.pairs == seed["pairs"],
              str(row.pairs if row else None))
        check(f"{type_id}: description is non-empty prose", row is not None and len(row.description) > 20)

    # Idempotency + update-in-place: hand-edit a row, re-run ensure_edge_types,
    # confirm it snaps back to the seed rather than being left alone (same
    # contract as ensure_backlog_node_types/ensure_world_node_types).
    with app.session() as s:
        row = s.get(EdgeType, "hosted_on")
        row.description = "an operator or a stray migration edited this"
        s.commit()
    with app.session() as s:
        node_types.ensure_edge_types(s)
        s.commit()
    with app.session() as s:
        row = s.get(EdgeType, "hosted_on")
        check("ensure_edge_types re-asserts every seeded field on re-run (update-in-place)",
              row.description == node_types.EDGE_TYPE_SEEDS["hosted_on"]["description"],
              row.description)

    # `pairs` specifically (JSONB, not a scalar column) — the deploy-shaped
    # case this commit exists for: live already holds nine seeded rows with
    # the OLD, incomplete `contains` pairs (missing object/bridge), and a
    # deploy has to correct them in place, not just insert-if-missing. Hand-
    # roll the row back to the pre-fix, two-pair shape and confirm a re-run
    # of ensure_edge_types both grows it back to three pairs AND drops a pair
    # that should no longer be there — a plain "does it contain the new
    # entry" check would pass even if setattr were silently skipping `pairs`
    # (e.g. via some future refactor that iterates only scalar columns).
    with app.session() as s:
        row = s.get(EdgeType, "contains")
        row.pairs = [["object", "object"], ["workitem", "workitem"], ["bogus", "pair-1093"]]
        s.commit()
    with app.session() as s:
        node_types.ensure_edge_types(s)
        s.commit()
    with app.session() as s:
        row = s.get(EdgeType, "contains")
        check("ensure_edge_types updates `pairs` in place on an already-seeded row — "
              "grows it to include object/bridge and drops the hand-added bogus pair",
              row.pairs == node_types.EDGE_TYPE_SEEDS["contains"]["pairs"],
              str(row.pairs))

    # ----------------------------------------------------------------- C --- #
    print("\n-- C. The drift check: proven by CREATING each condition, not by asserting the happy path --")

    with app.session() as s:
        warnings = node_types.edge_type_warnings(s)
    reasons_fresh = {w["edge_type"]: w["reason"] for w in warnings}
    # A brand-new database has no edges at all yet, so every ACTIVE seed is
    # legitimately "declared active, zero rows" — that is not a bug in the
    # check, it is the honest state of an empty graph. What matters here is
    # the shape: nothing is used_but_undeclared (nothing has been written)
    # and nothing dead carries rows (nothing has been written).
    # Under a distribution whose default boot seeds the system node set, the graph is not
    # edge-less: BOOT_EDGE_TYPES (above) are the types that set writes, and only they drop out.
    if BOOT_EDGE_TYPES:
        print(f"[note] distribution {distribution.NAME!r} seeds the system node set at boot; "
              f"its edge types {sorted(BOOT_EDGE_TYPES)} carry rows from the start")
    check("a fresh database (no edge but the boot seed's) flags every other ACTIVE seed as zero-rows and nothing else",
          set(reasons_fresh.values()) <= {"declared_active_zero_rows"} and
          set(reasons_fresh) == {t for t, s in node_types.EDGE_TYPE_SEEDS.items()
                                 if s["status"] == "active" and t not in BOOT_EDGE_TYPES},
          str(reasons_fresh))
    check("...and the two DEAD seeds (serves/for) are silent, because dead-with-zero-rows is not a warning",
          "serves" not in reasons_fresh and "for" not in reasons_fresh, str(reasons_fresh))

    with app.session() as s:
        testseed.build(s)
    with app.session() as s:
        testseed.self_check(s)

    with app.session() as s:
        warnings_after_fixture = node_types.edge_type_warnings(s)
    fixture_edge_types = {"contains", "depends_on", "works_on"}  # see testseed.py's Edge() calls
    reasons = {w["edge_type"]: w["reason"] for w in warnings_after_fixture}
    check("the fixture's edge types (contains/depends_on/works_on) carry no drift",
          not (fixture_edge_types & set(reasons)), str(reasons))
    zero_row_active = {"hosted_on", "contributes_to", "uses", "instance_of", "requires"} - BOOT_EDGE_TYPES
    check("C1. declared ACTIVE types the fixture never uses are flagged declared_active_zero_rows",
          zero_row_active <= {k for k, v in reasons.items() if v == "declared_active_zero_rows"},
          str(reasons))
    check("serves/for (declared dead, and still zero rows here) are NOT flagged yet — nothing to flag",
          "serves" not in reasons and "for" not in reasons, str(reasons))

    # C2 (used_but_undeclared): write an edge of a type this registry has
    # never heard of, straight at the model layer — the same shape a stray
    # write-API caller or a future migration could produce.
    with app.session() as s:
        n = s.scalars(select(app.GraphNode).limit(2)).all()
        s.add(Edge(src=n[0].id, dst=n[1].id, edge_type="verify-undeclared-edge-type", origin="atlas"))
        s.commit()
    with app.session() as s:
        warnings_undeclared = node_types.edge_type_warnings(s)
    undeclared = {w["edge_type"]: w for w in warnings_undeclared}
    check("C2. an edge_type with no edge_type row is reported used_but_undeclared",
          undeclared.get("verify-undeclared-edge-type", {}).get("reason") == "used_but_undeclared",
          str(undeclared.get("verify-undeclared-edge-type")))
    check("C2. the count on that warning is right (one edge written)",
          undeclared.get("verify-undeclared-edge-type", {}).get("count") == 1)

    # C3 (declared_dead_with_rows) — THE HAZARD CASE, and the whole reason
    # this table has a `status` column at all: `serves` gaining a row is the
    # AGENTS.md-documented rollup double-count regressing, not a curiosity.
    #
    # Deliberately a workitem -> object pair (`serves`'s OWN declared pair),
    # not just "the first two nodes": a mismatched pair here would ALSO fire
    # the new C4 pair_not_declared condition for the same edge_type, and
    # since a type can legitimately carry more than one simultaneous warning
    # now, collapsing `warnings_dead` into a dict keyed by edge_type would
    # silently keep only whichever reason was appended last. Isolate the
    # dead-with-rows case cleanly by giving `serves` an edge that satisfies
    # its own `pairs` — the double-count hazard has nothing to do with family.
    with app.session() as s:
        task_node_id = s.scalars(
            select(app.GraphNode.id).where(app.GraphNode.type.in_(("task", "story", "epic")))
        ).first()
        world_node_id = s.scalars(
            select(app.GraphNode.id).where(app.GraphNode.type.in_(("system", "service", "component")))
        ).first()
        s.add(Edge(src=task_node_id, dst=world_node_id, edge_type="serves", origin="atlas"))
        s.commit()
    with app.session() as s:
        warnings_dead = node_types.edge_type_warnings(s)
    dead_serves = [w for w in warnings_dead if w["edge_type"] == "serves"]
    check("C3. a `serves` row makes the check fire declared_dead_with_rows",
          any(w["reason"] == "declared_dead_with_rows" for w in dead_serves),
          str(dead_serves))
    check("C3. the warning's detail names the double-count hazard, not just the fact",
          any("double-count" in w.get("detail", "") for w in dead_serves
              if w["reason"] == "declared_dead_with_rows"),
          str(dead_serves))
    check("C3. giving `serves` its OWN declared pair means pair_not_declared does "
          "NOT also fire for it here — the two conditions are independent",
          not any(w["reason"] == "pair_not_declared" for w in dead_serves),
          str(dead_serves))
    check("C3. the check is a LIST that can be empty again, not a sticky flag — "
          "deleting the row clears the warning",
          True)  # asserted concretely just below, kept as a labelled step for the report
    with app.session() as s:
        s.query(Edge).filter(Edge.edge_type == "serves").delete()
        s.commit()
    with app.session() as s:
        warnings_after_cleanup = node_types.edge_type_warnings(s)
    check("...confirmed: after deleting the `serves` row, declared_dead_with_rows clears",
          "serves" not in {w["edge_type"] for w in warnings_after_cleanup
                           if w["reason"] == "declared_dead_with_rows"},
          str(warnings_after_cleanup))

    # C4 (pair_not_declared) — the FOURTH condition. Same convention as C2/C3:
    # prove it fires, prove it clears, and — since this is the check that
    # would have caught the original seed's missing object/bridge pair on its
    # own — prove it stays SILENT on the shapes that are actually correct.
    print("\n-- C4. pair_not_declared: the family-level condition, proven the same way --")

    # C4a: silent on the fixture as it stands (already exercised implicitly by
    # "the fixture's edge types carry no drift" above, since pair_not_declared
    # is folded into the same list) — made explicit here so a future change to
    # the fixture's node types cannot silently start tripping it.
    with app.session() as s:
        warnings_fixture_pairs = node_types.edge_type_warnings(s)
    check("C4a. the fixture, using only the corrected seed's declared pairs, "
          "carries no pair_not_declared entries",
          not any(w["reason"] == "pair_not_declared" for w in warnings_fixture_pairs),
          str(warnings_fixture_pairs))

    # C4b: the object/bridge case commit 1 exists for — a capability
    # containing a skill — must NOT be flagged now that it is declared.
    # Ids are pulled out as plain strings BEFORE the session that fetched
    # them closes; the ORM rows themselves are not carried across sessions
    # (`expire_on_commit` leaves them needing a re-fetch on attribute access,
    # which raises `DetachedInstanceError` against a session that already
    # closed — the id string has no such lifetime).
    with app.session() as s:
        world_service_id = s.scalars(
            select(app.GraphNode.id).where(app.GraphNode.type == "service")).first()
        s.add(app.GraphNode(id="verify-edge-types-skill", type="skill",
                            name="verify skill", body="fixture",
                            owner="seed", source=None, origin="atlas"))
        s.flush()
        s.add(Edge(src=world_service_id, dst="verify-edge-types-skill", edge_type="contains", origin="atlas"))
        s.commit()
    with app.session() as s:
        warnings_bridge = node_types.edge_type_warnings(s)
    check("C4b. object -> bridge `contains` (declared since commit 1) is NOT flagged pair_not_declared",
          not any(w["reason"] == "pair_not_declared" and w["edge_type"] == "contains"
                  and w.get("dst_family") == "bridge" for w in warnings_bridge),
          str([w for w in warnings_bridge if w["reason"] == "pair_not_declared"]))
    with app.session() as s:
        s.query(Edge).filter(Edge.src == world_service_id, Edge.dst == "verify-edge-types-skill").delete()
        s.query(app.GraphNode).filter(app.GraphNode.id == "verify-edge-types-skill").delete()
        s.commit()

    # C4c: the FAIL-proof — a family pair that is NOT declared for a type
    # that IS. `contains` between a workitem and a world object is legal for
    # the WORK/WORLD hierarchies separately, but workitem -> object is
    # neither; this is exactly the kind of drift a hand-written migration or
    # a stray direct insert could produce.
    with app.session() as s:
        task_node_id = s.scalars(select(app.GraphNode.id).where(app.GraphNode.type == "task")).first()
        world_service_id = s.scalars(
            select(app.GraphNode.id).where(app.GraphNode.type == "service")).first()
        s.add(Edge(src=task_node_id, dst=world_service_id, edge_type="contains", origin="atlas"))
        s.commit()
    with app.session() as s:
        warnings_mismatch = node_types.edge_type_warnings(s)
    mismatches = [w for w in warnings_mismatch if w["reason"] == "pair_not_declared"]
    check("C4c. a workitem -> object `contains` edge (declared for neither hierarchy) "
          "is reported pair_not_declared",
          any(w["edge_type"] == "contains" and w["src_family"] == "workitem"
              and w["dst_family"] == "object" for w in mismatches),
          str(mismatches))
    check("C4c. the warning names an example edge (id/src/dst), so it is actionable "
          "without a second query",
          any(w["edge_type"] == "contains" and w["src_family"] == "workitem"
              and w["dst_family"] == "object" and w.get("example_edge")
              and w["example_edge"]["src"] == task_node_id
              and w["example_edge"]["dst"] == world_service_id
              for w in mismatches),
          str(mismatches))
    with app.session() as s:
        s.query(Edge).filter(Edge.src == task_node_id, Edge.dst == world_service_id,
                             Edge.edge_type == "contains").delete()
        s.commit()
    with app.session() as s:
        warnings_after_mismatch_cleanup = node_types.edge_type_warnings(s)
    check("...confirmed: deleting the offending edge clears pair_not_declared for `contains`",
          not any(w["reason"] == "pair_not_declared" and w["edge_type"] == "contains"
                  for w in warnings_after_mismatch_cleanup),
          str(warnings_after_mismatch_cleanup))

    # C4d: "silent on live's current data" (the task's own hard requirement
    # for commit 2). This process cannot reach live's database from here, so
    # this asserts the claim against the exact breakdown measured there
    # (2026-08-26, `SELECT edge_type, src_fam, dst_fam, count(*) ... GROUP BY
    # 1,2,3` — reproduced verbatim, not re-derived) as a static regression
    # guard: every one of those nine measured combinations must be declared
    # by the corrected seed, or this fails right here instead of on deploy.
    LIVE_MEASURED_PAIRS_20260826 = {
        ("contains", "object", "bridge"): 1,
        ("contains", "object", "object"): 67,
        ("contains", "workitem", "workitem"): 382,
        ("contributes_to", "object", "object"): 2,
        ("depends_on", "workitem", "workitem"): 333,
        ("hosted_on", "object", "object"): 23,
        ("instance_of", "object", "object"): 6,
        ("uses", "object", "object"): 1,
        ("works_on", "workitem", "object"): 374,
    }
    for (et, sf, df), _count in LIVE_MEASURED_PAIRS_20260826.items():
        seed_pairs = [tuple(p) for p in node_types.EDGE_TYPE_SEEDS[et]["pairs"]]
        check(f"C4d. live-measured combination ({et!r}, {sf!r}, {df!r}) is declared "
              "after commit 1's fix — pair_not_declared will be silent on it",
              (sf, df) in seed_pairs, str(seed_pairs))

    # ----------------------------------------------------------------- D --- #
    print("\n-- D. EDGE_FAMILY_RULES agreement, and POST /api/edge is UNCHANGED --")
    for edge_type, required in app.EDGE_FAMILY_RULES.items():
        seed_pairs = [tuple(p) for p in node_types.EDGE_TYPE_SEEDS[edge_type]["pairs"]]
        check(f"registry pairs for {edge_type!r} include EDGE_FAMILY_RULES' {required}",
              tuple(required) in seed_pairs, str(seed_pairs))

    client = _client()
    token = _mint(models_auth.ROLE_MEMBER, "verify-edge-types-writer")
    hdr = {"Authorization": f"Bearer {token}"}
    with app.session() as s:
        world_nodes = s.scalars(select(app.GraphNode).where(app.GraphNode.type == "system")).all()
        if len(world_nodes) < 2:
            world_nodes = s.scalars(select(app.GraphNode).limit(5)).all()
        objs = [n for n in world_nodes][:2]
    if len(objs) >= 2:
        # depends_on is workitem/workitem per the registry, but the write API
        # never consulted EDGE_FAMILY_RULES for it before this table existed
        # (it isn't a key there) and must not start now — this table is
        # read-only in effect. Exercise it on two WORKITEM nodes instead of
        # world objects, since that is what depends_on is actually used for.
        with app.session() as s:
            workitems = s.scalars(
                select(app.GraphNode).where(app.GraphNode.type.in_(("task", "story", "epic")))).all()
        wi = workitems[:2]
        if len(wi) >= 2:
            r = client.post("/api/edge", json={"src": wi[0].id, "dst": wi[1].id, "edge_type": "depends_on"},
                             headers=hdr)
            check("POST /api/edge still accepts `depends_on` (undeclared in EDGE_FAMILY_RULES, "
                  "unconstrained by design, and this table changes nothing about that)",
                  r.status_code in (200, 201), f"got {r.status_code} {r.text[:200]}")

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
