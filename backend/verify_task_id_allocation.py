"""
Verification for TASK-1047 (server-side workitem id allocation).

No pytest suite exists in this repo yet (see verify_object_graph.py's docstring) — this
follows the same standalone, run-it-and-read-it convention.

THREE SECTIONS, DELIBERATELY SEPARATED, because they prove DIFFERENT things
(TASK-1044 removed the fourth — a SQLite-fallback section proving
`allocate_task_id()`'s old SQLite branch, which no longer exists: the
function is `nextval('task_id_seq')`, full stop, on every dialect there is):

  A. Pure-Python id-formatting/parsing logic. No DB. Always runs.
  B. A real, disposable Postgres (via `testdb`, TASK-1044). Proves:
       - seeding above the max on a database that ALREADY holds TASK- ids —
         the scenario that matters: a fresh `CREATE SEQUENCE ... START WITH 1`
         landing on a table that already has TASK-001..TASK-1062;
       - the sequence never moves backwards across repeated seeding calls;
       - extra_ids (files not yet ingested) are honoured;
       - REAL concurrency: two independent sessions (one of which rolls back) get
         distinct ids, and so do N sessions allocating from separate threads at once.
  C. The real app.py startup() path (schema create/reconcile + both ingests + the new
     _seed_task_id_sequence step) wired against the SAME Postgres and the REAL backlog —
     proves the INTEGRATION point, not just the library function in isolation.

STORY-oss-12-project-keys added two more, against the same app and database:

  D. `models_graph`'s project-key counter, below the routes: key validation; 24 real
     threads minting under one key, each holding the row lock across a sleep and a
     third of them ROLLING BACK — the committed ids are KEY-1..KEY-k exactly, no
     duplicate and no gap; a caller-supplied KEY-n raises the floor; the TASK-
     sequence is not consumed by any of it.
  E. Through the REAL routes and gate (PATCH /api/node `key`, POST /api/workitem): the
     column and counter table arriving on a POPULATED database leave every existing
     id unchanged; no key anywhere = TASK-; the key's refusals (not a project, bad
     shape, reserved, taken, member under RBAC off, editor under RBAC on); tasks under
     a keyed project — bound to it, to an object inside it, or under a story bound to
     it — get KEY-1, KEY-2, ...; a refused create spends no number; an unkeyed
     project and an unanchored task still get TASK-; the key is immutable once used;
     moving an item does not renumber it; and 12 concurrent HTTP creates under the key
     get 12 distinct, contiguous ids.

Run (from the venv with fastapi/SQLAlchemy/PyYAML/jsonschema/psycopg2 installed):
    python verify_task_id_allocation.py
"""
from __future__ import annotations

import base64
import os
import sys
import threading


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb  # noqa: E402

# No SQLite fallback exists any more — see testdb's docstring. This DSN is
# used directly by section B (its own engine, independent of app.py) and,
# once assigned to ATLAS_DB_URL below, by section C's `import app`.
try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:
    print(f"FATAL: {e}")
    sys.exit(1)
PG_DSN = DB.url

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


# =========================================================================== #
# A. Pure-Python id logic — no DB
# =========================================================================== #
def verify_format_and_parse() -> None:
    print("=" * 78)
    print("A. format_task_id / _task_number — pure logic, no DB")
    print("=" * 78)
    import models_graph as mg

    check("format_task_id(1) == 'TASK-001'", mg.format_task_id(1) == "TASK-001", mg.format_task_id(1))
    check("format_task_id(42) == 'TASK-042'", mg.format_task_id(42) == "TASK-042", mg.format_task_id(42))
    check("format_task_id(999) == 'TASK-999'", mg.format_task_id(999) == "TASK-999", mg.format_task_id(999))
    check("format_task_id(1046) == 'TASK-1046' (width is a MINIMUM, not truncation)",
          mg.format_task_id(1046) == "TASK-1046", mg.format_task_id(1046))
    check("format_task_id(1063) == 'TASK-1063'", mg.format_task_id(1063) == "TASK-1063", mg.format_task_id(1063))

    check("_task_number('TASK-1046') == 1046", mg._task_number("TASK-1046") == 1046)
    check("_task_number('TASK-001') == 1", mg._task_number("TASK-001") == 1)
    check("_task_number('STORY-atlas-object-graph') is None (not a TASK- id)",
          mg._task_number("STORY-atlas-object-graph") is None)
    check("_task_number('EPIC-project-atlas') is None", mg._task_number("EPIC-project-atlas") is None)
    check("_task_number('TASK-COLLIDE') is None (non-numeric suffix, a real verify-fixture id "
          "in ingest_graph.py's own comments — must not crash the scanner)",
          mg._task_number("TASK-COLLIDE") is None)
    check("_task_number(None) is None", mg._task_number(None) is None)
    check("_task_number('TASK-WAPI-1') is None (write-API test id, non-numeric suffix)",
          mg._task_number("TASK-WAPI-1") is None)


# =========================================================================== #
# B. Real Postgres
# =========================================================================== #
def verify_postgres_seeding_and_concurrency() -> None:
    print("\n" + "=" * 78)
    print(f"B. Real Postgres ({PG_DSN}) — seeding-on-populated-DB + REAL concurrency")
    print("=" * 78)

    from sqlalchemy import create_engine, insert, select, text
    from sqlalchemy.orm import Session

    import models_graph as mg

    eng = create_engine(PG_DSN)

    # Fresh schema every run: this section owns the database for its duration,
    # rebuilt from models_graph's metadata only (no v0 Component / auth tables
    # needed here) — `DB.reset_schema()` is the same DROP/CREATE SCHEMA every
    # other verify script uses via testdb.
    DB.reset_schema()

    # --- B1. Populate the node table BEFORE the sequence exists at all -------------------
    # This is the scenario the task's instructions call out explicitly: "check what
    # actually happens to a sequence on an already-populated database." We simulate it
    # directly rather than needing an old checkout: create every graph TABLE via a
    # metadata copy that has no sequence attached, insert ids up to a realistic high
    # watermark (mixed width, a gap, and a non-numeric-suffix id exactly like
    # TASK-COLLIDE), and only THEN run the real models_graph.Base.metadata.create_all —
    # which is the first moment task_id_seq is created, exactly as it will be on live's
    # actual first deploy of this feature.
    from sqlalchemy import MetaData
    pre_metadata = MetaData()
    for table in mg.Base.metadata.sorted_tables:
        table.to_metadata(pre_metadata)  # copies every TABLE, no standalone Sequence object
    pre_metadata.create_all(eng)

    with Session(eng) as s:
        s.execute(insert(mg.NodeType.__table__).values(id="task", attributes_schema={}, family="workitem"))
        s.execute(insert(mg.NodeType.__table__).values(id="story", attributes_schema={}, family="workitem"))
        seed_ids = ["TASK-001", "TASK-042", "TASK-999", "TASK-1046", "TASK-1062", "TASK-COLLIDE"]
        for nid in seed_ids:
            ntype = "task" if nid != "TASK-COLLIDE" else "task"
            s.execute(insert(mg.Node.__table__).values(id=nid, type=ntype, name=nid, attributes={}))
        s.execute(insert(mg.Node.__table__).values(id="STORY-unrelated", type="story",
                                                    name="unrelated", attributes={}))
        s.commit()

    check("pre-population: task_id_seq does NOT exist yet",
          not eng.connect().execute(
              text("SELECT to_regclass('public.task_id_seq') IS NOT NULL")
          ).scalar())

    # --- B2. NOW run the real create_all — first-ever CREATE SEQUENCE on this populated DB
    mg.Base.metadata.create_all(eng)  # start=1 by declaration — the collision risk itself
    exists = eng.connect().execute(text("SELECT to_regclass('public.task_id_seq') IS NOT NULL")).scalar()
    check("task_id_seq now exists after create_all", bool(exists))

    with Session(eng) as s:
        raw_last_value = s.execute(text("SELECT last_value FROM pg_sequences WHERE sequencename = 'task_id_seq'")).scalar()
        # pg_sequences.last_value is NULL until the sequence's first-ever nextval() — verified
        # directly against this same Postgres (16.14) with a scratch sequence before writing
        # this assertion: `CREATE SEQUENCE x; SELECT last_value FROM pg_sequences ...` returns
        # a blank/NULL cell even though `SELECT last_value, is_called FROM x` shows (1, false).
        # This is exactly the distinction seed_task_id_sequence()'s `current is None` branch
        # relies on — see its docstring.
        check("brand-new sequence has NOT been consumed yet: pg_sequences.last_value is NULL",
              raw_last_value is None, raw_last_value)

        # --- B3. Seed, then allocate — must not collide with TASK-1062 -------------------
        seeded_to = mg.seed_task_id_sequence(s)
        s.commit()
        check("seed_task_id_sequence() returns a value (Postgres path, not None)", seeded_to is not None)
        first_id = mg.allocate_task_id(s)
        s.commit()
        check("first id allocated on a DB already holding up to TASK-1062 is TASK-1063 (no collision)",
              first_id == "TASK-1063", first_id)

        # --- B4. Re-seeding is idempotent and never rewinds ------------------------------
        mg.seed_task_id_sequence(s)
        s.commit()
        second_id = mg.allocate_task_id(s)
        s.commit()
        check("re-seeding after an allocation does not rewind: next id is TASK-1064, not TASK-1063 again",
              second_id == "TASK-1064", second_id)

        # --- B5. extra_ids (files not yet ingested) are honoured -------------------------
        mg.seed_task_id_sequence(s, extra_ids=["TASK-2000", "TASK-1999", "STORY-not-a-task"])
        s.commit()
        third_id = mg.allocate_task_id(s)
        s.commit()
        check("seeding with extra_ids=['TASK-2000', ...] (files not yet in the node table) "
              "jumps allocation past them: TASK-2001",
              third_id == "TASK-2001", third_id)

        # --- B6. Re-seeding after a HIGHER real max shows up in the node table -----------
        # (e.g. a hand-authored file with an out-of-band high number got ingested)
        s.execute(insert(mg.Node.__table__).values(id="TASK-5000", type="task", name="hand-authored",
                                                     attributes={}))
        s.commit()
        mg.seed_task_id_sequence(s)
        s.commit()
        fourth_id = mg.allocate_task_id(s)
        s.commit()
        check("seeding again after a higher real node (TASK-5000) appears jumps to TASK-5001",
              fourth_id == "TASK-5001", fourth_id)

        # Never colliding with ANY previously-seen id, including the deliberately-skipped
        # ones (TASK-COLLIDE etc.) and every id minted above.
        never_collide = {"TASK-001", "TASK-042", "TASK-999", "TASK-1046", "TASK-1062",
                          "TASK-1063", "TASK-1064", "TASK-2000", "TASK-1999", "TASK-5000"}
        minted = {first_id, second_id, third_id, fourth_id}
        check("no minted id collides with any pre-existing or extra_ids id",
              minted.isdisjoint(never_collide - minted), f"minted={minted}")

    # --- B7. REAL concurrency: two independent sessions, one rolls back ------------------
    print("\n  B7. two independent sessions, one ROLLS BACK — nextval() is non-transactional")
    sA = Session(eng)
    sB = Session(eng)
    id_a = mg.allocate_task_id(sA)   # consumed, session A's transaction is NOT committed
    id_b = mg.allocate_task_id(sB)
    sB.commit()
    sA.rollback()                    # A's transaction (incl. any row inserts) is discarded
    sA.close()
    sB.close()
    check("two concurrent sessions get DISTINCT ids", id_a != id_b, f"a={id_a} b={id_b}")

    with Session(eng) as s:
        id_c = mg.allocate_task_id(s)
        s.commit()
    check("the ROLLED-BACK session's id is NOT reissued (nextval is non-transactional by design — "
          "the owner accepted this burns a number on TASK-1047)",
          id_c not in (id_a, id_b), f"a={id_a} b={id_b} c={id_c}")

    # --- B8. N-way real concurrency stress, actual OS threads -----------------------------
    print("\n  B8. 24 threads, 24 independent sessions, allocating at the same moment")
    N = 24
    results: list[str | None] = [None] * N
    errors: list[str] = []
    barrier = threading.Barrier(N)

    def worker(i: int) -> None:
        try:
            barrier.wait(timeout=10)  # line everyone up so the nextval() calls actually overlap
            with Session(eng) as s:
                results[i] = mg.allocate_task_id(s)
                s.commit()
        except Exception as e:  # noqa: BLE001 — captured and reported, not swallowed
            errors.append(f"thread {i}: {type(e).__name__}: {e}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    check("all 24 threads completed without raising", not errors, "; ".join(errors[:5]))
    check(f"all {N} concurrently-allocated ids are DISTINCT (real DB-level concurrency guarantee, "
          "not application-level locking)",
          len(set(results)) == N, f"got {len(set(r for r in results if r))} distinct of {N}")
    print(f"    sample: {sorted(r for r in results if r)[:6]} ... {sorted(r for r in results if r)[-3:]}")

    eng.dispose()


def verify_postgres_app_startup_integration() -> None:
    print("\n" + "=" * 78)
    print("C. Real app.py startup() wired against this Postgres + the REAL backlog")
    print("   (proves the INTEGRATION point — _seed_task_id_sequence in app.py — not just the library fn)")
    print("=" * 78)

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

    # `app` is imported here, for the first time in this process — sections A/B
    # above deliberately never import it, so its module-level `engine` is built
    # from the ATLAS_DB_URL set just above, not from whatever an earlier import
    # might have already bound.
    assert "app" not in sys.modules, "app was imported earlier than expected — see this function's docstring"
    import app  # noqa: E402
    import testseed  # noqa: E402
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    app.startup()  # real create_all, real reconcile, real node_type registry seed — clean schema
    # CHANGED (TASK-1058): app.startup() no longer reads any file.
    # CHANGED AGAIN (testseed): what this section needs is a populated table
    # with a high-numbered TASK- id to test `_seed_task_id_sequence` against,
    # not the real backlog. The fixture's ids run to TASK-9017 -- higher than
    # anything real, and stable, so the assertions below stop depending on how
    # many tickets happen to exist today. Both are relative to `max_in_db`
    # anyway, which is why this is a swap and not a rewrite.
    with app.session() as s:
        testseed.build(s)
    # ATLAS_RESEED must be OFF from here on — it was only needed once, above,
    # to get a clean schema; leaving it on would drop_all() the backlog this
    # import just wrote and reseed the sequence from an empty table again,
    # making the assertion below pass by accident. `_seed_task_id_sequence`
    # only runs inside `startup()`'s own retry loop, so it must run again
    # AFTER the corpus exists to pick up its real max — same as a real boot
    # would see it (backlog already in Postgres from a previous run).
    os.environ["ATLAS_RESEED"] = ""
    app.startup()

    with Session(app.engine) as s:
        max_in_db = s.execute(text(
            "SELECT max(cast(regexp_replace(id, '^TASK-', '') as integer)) FROM node "
            "WHERE id ~ '^TASK-[0-9]+$'"
        )).scalar()
        print(f"  fixture imported; highest TASK- id in node table: TASK-{max_in_db}")

        import models_graph as mg
        first = mg.allocate_task_id(s)
        s.commit()
        first_n = mg._task_number(first)
        check("the first id allocated after a REAL app.startup() against a REAL backlog "
              "exceeds every existing TASK- id",
              first_n is not None and first_n > max_in_db, f"allocated {first!r}, max was TASK-{max_in_db}")

        # Re-run startup (as a container restart would) and confirm the sequence does not
        # rewind even though the ingest is idempotent and re-touches every existing row.
        # ATLAS_RESEED must be OFF here — it was only needed once, to get a clean schema
        # for this section; a real restart never wipes a persistent Postgres, and leaving
        # it on would drop_all() (sequence included) and reseed from a wiped node table,
        # making this assertion pass by accident (a fresh sequence "not rewinding" its
        # own first value is not the same claim as "a restart does not rewind it").
        os.environ["ATLAS_RESEED"] = ""
        app.startup()
        second = mg.allocate_task_id(s)
        s.commit()
        second_n = mg._task_number(second)
        check("a second app.startup() (simulating a restart) does not rewind the sequence",
              second_n == first_n + 1, f"first={first} second={second}")


def verify_project_key_counter() -> None:
    print("\n" + "=" * 78)
    print("D. Project-key counter (STORY-oss-12) — validation, REAL concurrency, floor")
    print("=" * 78)
    import time

    import app
    import models_graph as mg
    from sqlalchemy.orm import Session

    for good in ("AB", "ABC", "OPS2", "A1234567890"[:10]):
        check(f"project_key_problem({good!r}) is None", mg.project_key_problem(good) is None)
    for bad in ("A", "abc", "1AB", "AB-C", "ABCDEFGHIJK", "", " AB", "TASK", "STORY", "EPIC", 7):
        check(f"project_key_problem({bad!r}) refuses", mg.project_key_problem(bad) is not None)
    check("project_key_problem(None) is None (clearing is a shape-legal value)",
          mg.project_key_problem(None) is None)
    check("format_project_task_id('ABC', 7) == 'ABC-7' (no padding)",
          mg.format_project_task_id("ABC", 7) == "ABC-7")
    check("project_task_number('ABC', 'ABC-12') == 12", mg.project_task_number("ABC", "ABC-12") == 12)
    for other in ("ABCD-1", "AB-1", "ABC-x", "TASK-1", None):
        check(f"project_task_number('ABC', {other!r}) is None",
              mg.project_task_number("ABC", other) is None)

    with Session(app.engine) as s:
        task_before = mg._task_number(mg.allocate_task_id(s))
        s.commit()

    N, ROLLBACK_EVERY = 24, 3
    minted: list[tuple[int, str, bool]] = []
    errors: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(N)

    def worker(i: int) -> None:
        try:
            barrier.wait(timeout=10)
            with Session(app.engine) as s:
                nid = mg.allocate_project_task_id(s, "KD")
                s.add(mg.Node(id=nid, type="task", name=f"kd {i}", attributes={}, origin="atlas"))
                s.flush()
                time.sleep(0.02)  # hold the row lock, so the others really queue on it
                keep = i % ROLLBACK_EVERY != 0
                if keep:
                    s.commit()
                else:
                    s.rollback()
            with lock:
                minted.append((i, nid, keep))
        except Exception as e:  # noqa: BLE001 — captured and reported
            errors.append(f"thread {i}: {type(e).__name__}: {e}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    check(f"all {N} keyed allocations completed without raising", not errors, "; ".join(errors[:5]))

    with Session(app.engine) as s:
        rows = sorted(nid for (nid,) in s.execute(
            mg.select(mg.Node.id).where(mg.Node.id.like("KD-%"))).all())
    kept = sum(1 for _, _, k in minted if k)
    nums = sorted(mg.project_task_number("KD", r) for r in rows)
    check(f"{kept} committed creates left {kept} KD- rows", len(rows) == kept, f"{len(rows)} rows")
    check("the committed ids are exactly KD-1..KD-k: no duplicate, and no gap — a rolled-back "
          "create gave its number back to the next caller",
          nums == list(range(1, kept + 1)), f"got {nums}")

    with Session(app.engine) as s:
        s.add(mg.Node(id="KD-100", type="task", name="caller-supplied", attributes={}, origin="atlas"))
        s.commit()
        nxt = mg.allocate_project_task_id(s, "KD")
        s.commit()
    check("a caller-supplied KD-100 raises the floor: the next mint is KD-101", nxt == "KD-101", nxt)

    with Session(app.engine) as s:
        other = mg.allocate_project_task_id(s, "KE")
        s.commit()
        task_after = mg._task_number(mg.allocate_task_id(s))
        s.commit()
    check("a second key has its own counter: KE-1", other == "KE-1", other)
    check("the TASK- sequence was not consumed by any keyed mint (consecutive around them)",
          task_after == task_before + 1, f"before={task_before} after={task_after}")


def verify_project_keys_http() -> None:
    print("\n" + "=" * 78)
    print("E. Project keys through the REAL routes (PATCH /api/node key, POST /api/workitem)")
    print("=" * 78)
    import dataclasses
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import inspect as sa_inspect, text as sql_text
    from sqlalchemy.orm import Session

    import app
    import models_graph as mg
    import rbac
    import rbac_testkit as kit

    def all_ids() -> dict[str, tuple]:
        with Session(app.engine) as s:
            return {n.id: (n.type, n.name, n.body) for n in s.scalars(mg.select(mg.Node)).all()}

    # Section D's KD-/KE- rows are not this section's subject.
    with Session(app.engine) as s:
        s.execute(sql_text("DELETE FROM node WHERE id LIKE 'KD-%' OR id LIKE 'KE-%'"))
        s.commit()

    # --- E1. the column, its index and the counter table arrive on a POPULATED database
    before = all_ids()
    task_ids_before = {i for i in before if i.startswith("TASK-")}
    with app.engine.begin() as conn:
        conn.execute(sql_text("DROP INDEX IF EXISTS ux_node_project_key"))
        conn.execute(sql_text("ALTER TABLE node DROP COLUMN project_key"))
        conn.execute(sql_text("DROP TABLE project_key_counter"))
    app.startup()  # create_all (the table) + _reconcile_graph_schema (the column, the index)
    insp = sa_inspect(app.engine)
    check("reconcile re-added node.project_key on the populated table",
          "project_key" in {c["name"] for c in insp.get_columns("node")})
    check("…with its unique index", any(i["name"] == "ux_node_project_key" and i["unique"]
                                        for i in insp.get_indexes("node")))
    check("create_all re-created project_key_counter", insp.has_table("project_key_counter"))
    after = all_ids()
    check(f"every existing node ({len(before)}, {len(task_ids_before)} of them TASK-) is unchanged "
          "after the schema change: same ids, types, names, bodies", before == after)
    with Session(app.engine) as s:
        check("every existing node reads project_key NULL", s.execute(sql_text(
            "SELECT count(*) FROM node WHERE project_key IS NOT NULL")).scalar() == 0)
        check("no key anywhere: any_project_key() is False (the create route takes today's path)",
              mg.any_project_key(s) is False)

    # --- fixture: two projects, an object inside the first, an owner and a member
    with app.session() as s:
        kit.mk_node(s, "pk-alpha", "project", "Project Alpha")
        kit.mk_node(s, "pk-beta", "project", "Project Beta")
        kit.mk_node(s, "pk-alpha-cap", "capability", "Alpha capability")
        s.flush()
        kit.mk_edge(s, "pk-alpha", "pk-alpha-cap", "contains")
        s.commit()
    kit.mk_user("u-pk-owner", "pk-owner", "owner")
    kit.mk_user("u-pk-member", "pk-member", "member")
    owner, owner_csrf = kit.sign_in("pk-owner")
    member, member_csrf = kit.sign_in("pk-member")

    def task(binding=None, parent=None, client=owner, csrf=None, **extra):
        body = {"type": "task", "name": "pk task", "csrf_token": csrf or owner_csrf, **extra}
        if binding:
            body["binding"] = binding
        if parent:
            body["parent"] = parent
        return client.post("/api/workitem", json=body)

    def set_key(node_id, value, client=owner, csrf=None):
        return client.patch(f"/api/node/{node_id}", json={"key": value, "csrf_token": csrf or owner_csrf})

    r = task(binding="pk-alpha")
    check("no key set: a task bound to a project gets TASK- (today)",
          r.status_code == 201 and r.json()["id"].startswith("TASK-"), f"{r.status_code} {r.text[:200]}")
    check("…and its payload carries no `key` field", "key" not in r.json())

    # --- E2. the key's refusals
    r = set_key("pk-alpha-cap", "PKA")
    check("key on a non-project node: 400 key_not_project naming the field",
          r.status_code == 400 and r.json().get("error") == "key_not_project"
          and r.json().get("field") == "key", f"{r.status_code} {r.text[:200]}")
    for bad in ("pka", "P", "1PK", "PK-A", "TASK", "ABCDEFGHIJK"):
        r = set_key("pk-alpha", bad)
        check(f"key {bad!r}: 400 invalid_key naming the field",
              r.status_code == 400 and r.json().get("error") == "invalid_key"
              and r.json().get("field") == "key", f"{r.status_code} {r.text[:200]}")
    r = set_key("pk-alpha", "PKA", client=member, csrf=member_csrf)
    check("RBAC off: a member may not set a key (403)", r.status_code == 403, f"{r.status_code} {r.text[:200]}")

    r = set_key("pk-alpha", "PKX")
    check("owner sets PKX: 200, served as `key`", r.status_code == 200 and r.json().get("key") == "PKX",
          f"{r.status_code} {r.text[:200]}")
    r = set_key("pk-alpha", "PKA")
    check("before any id is minted the key may still change (PKX -> PKA)",
          r.status_code == 200 and r.json().get("key") == "PKA", f"{r.status_code} {r.text[:200]}")
    r = set_key("pk-beta", "PKA")
    check("a key another project holds: 409 key_taken", r.status_code == 409
          and r.json().get("error") == "key_taken", f"{r.status_code} {r.text[:200]}")
    r = owner.get("/api/node/pk-alpha")
    check("GET /api/node serves the key", r.status_code == 200 and r.json().get("key") == "PKA",
          r.text[:200])
    with app.session() as s:
        ev = s.execute(mg.select(mg.GraphEvent).where(
            mg.GraphEvent.entity_id == "pk-alpha", mg.GraphEvent.action == "node.update")).scalars().all()
    check("the key change is recorded in graph_event (fields: ['key'])",
          any((e.detail or {}).get("fields") == ["key"] for e in ev), f"{len(ev)} events")

    # --- E3. tasks under the keyed project: KEY-1, KEY-2, ...
    r1 = task(binding="pk-alpha")
    r2 = task(binding="pk-alpha-cap")
    rs = owner.post("/api/workitem", json={"type": "story", "id": "STORY-pk-story", "name": "pk story",
                                           "binding": "pk-alpha", "csrf_token": owner_csrf})
    check("a story under the keyed project keeps its caller-supplied id",
          rs.status_code == 201 and rs.json()["id"] == "STORY-pk-story", f"{rs.status_code} {rs.text[:200]}")
    r3 = task(parent="STORY-pk-story")
    got = [x.json().get("id") if x.status_code == 201 else f"{x.status_code}" for x in (r1, r2, r3)]
    check("bound to the project, bound to an object inside it, under a story bound to it: "
          "PKA-1, PKA-2, PKA-3", got == ["PKA-1", "PKA-2", "PKA-3"], f"got {got}")

    # A refusal AFTER the mint (works_on must point at an object, decided after the row
    # exists) rolls the counter back with the rest: the next create is PKA-4.
    # TASK-1378: a task under a task breaks the work order. Refused before the
    # mint, so the PKA-4 check below proves it spent no number either.
    rord = task(parent=got[2]) if r3.status_code == 201 else None
    check("a task created under a task (wrong order) -> 400 parent_order",
          rord is not None and rord.status_code == 400 and rord.json().get("error") == "parent_order",
          f"{rord.status_code if rord is not None else '-'} {rord.text[:200] if rord is not None else ''}")
    rbad = task(binding="STORY-pk-story")
    r4 = task(binding="pk-alpha")
    check("a create refused after its id was minted (family mismatch) is a 4xx",
          400 <= rbad.status_code < 500, f"{rbad.status_code} {rbad.text[:200]}")
    check("…and spent no number: the next create is PKA-4",
          r4.status_code == 201 and r4.json()["id"] == "PKA-4", f"{r4.status_code} {r4.text[:200]}")

    rb = task(binding="pk-beta")
    rn = task()
    check("a task in the UNKEYED project still gets TASK-",
          rb.status_code == 201 and rb.json()["id"].startswith("TASK-"), rb.text[:200])
    check("a task with no binding and no parent still gets TASK-",
          rn.status_code == 201 and rn.json()["id"].startswith("TASK-"), rn.text[:200])
    rx = task(binding="pk-alpha", id="PKA-99")
    check("a caller-supplied task id is still refused (id_is_minted)",
          rx.status_code == 400 and rx.json().get("error") == "id_is_minted", rx.text[:200])

    # --- E4. immutable once used
    for value in ("PKZ", None):
        r = set_key("pk-alpha", value)
        check(f"after PKA-1 exists, key -> {value!r}: 409 key_immutable",
              r.status_code == 409 and r.json().get("error") == "key_immutable", f"{r.status_code} {r.text[:200]}")
    r = set_key("pk-alpha", "PKA")
    check("re-sending the same key is a no-op 200", r.status_code == 200 and r.json().get("key") == "PKA",
          r.text[:200])
    r = owner.patch("/api/node/pk-alpha", json={"type": "capability", "csrf_token": owner_csrf})
    check("a keyed project cannot be retyped out from under its key (409)", r.status_code == 409, r.text[:200])

    # --- E5. moving an item does not renumber it
    owner.patch("/api/workitem/PKA-1", json={"binding": None, "csrf_token": owner_csrf})
    rm = owner.patch("/api/workitem/PKA-1", json={"binding": "pk-beta", "csrf_token": owner_csrf})
    check("PKA-1 re-bound to the unkeyed project", rm.status_code == 200, f"{rm.status_code} {rm.text[:200]}")
    check("…keeps its id", owner.get("/api/node/PKA-1").status_code == 200
          and rm.json().get("id") == "PKA-1", rm.text[:200])
    r5 = task(binding="pk-alpha")
    check("…and the counter carries on: PKA-5", r5.status_code == 201 and r5.json()["id"] == "PKA-5", r5.text[:200])

    # --- E6. RBAC on: maintainer, not editor
    prev = rbac.use(dataclasses.replace(rbac.SETTINGS, mode="projects"))
    try:
        kit.bind("pk-beta", "user", "u-pk-member", "editor")
        r = set_key("pk-beta", "PKB", client=member, csrf=member_csrf)
        check("RBAC on: an editor on the project may not set its key (403)", r.status_code == 403,
              f"{r.status_code} {r.text[:200]}")
        kit.bind("pk-beta", "user", "u-pk-member", "maintainer")
        r = set_key("pk-beta", "PKB", client=member, csrf=member_csrf)
        check("RBAC on: a maintainer on the project may (200)",
              r.status_code == 200 and r.json().get("key") == "PKB", f"{r.status_code} {r.text[:200]}")
    finally:
        rbac.use(prev)
        kit.unbind_all()

    # --- E7. concurrent creates over HTTP: distinct and contiguous
    N = 12
    clients = []
    for _ in range(N):
        c = kit.client()
        c.cookies = owner.cookies
        c.headers["Origin"] = kit.TEST_ORIGIN
        clients.append(c)
    barrier = threading.Barrier(N)

    def create(c):
        barrier.wait(timeout=20)
        return c.post("/api/workitem", json={"type": "task", "name": "pk concurrent",
                                             "binding": "pk-alpha", "csrf_token": owner_csrf})

    with ThreadPoolExecutor(max_workers=N) as pool:
        results = list(pool.map(create, clients))
    codes = [r.status_code for r in results]
    ids = sorted((r.json()["id"] for r in results if r.status_code == 201),
                 key=lambda i: mg.project_task_number("PKA", i) or -1)
    check(f"{N} concurrent creates under PKA all succeeded", codes == [201] * N, f"{codes}")
    check(f"…with {N} distinct ids PKA-6..PKA-{5 + N}, no duplicate and no gap",
          ids == [f"PKA-{n}" for n in range(6, 6 + N)], f"{ids}")

    final = all_ids()
    check("every TASK- id that existed before this section still exists, unchanged",
          all(final.get(i) == before[i] for i in task_ids_before))


def main() -> None:
    verify_format_and_parse()
    verify_postgres_seeding_and_concurrency()
    verify_postgres_app_startup_integration()
    verify_project_key_counter()
    verify_project_keys_http()

    print("\n" + "=" * 78)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED (including the real-Postgres concurrency proof)")


if __name__ == "__main__":
    main()
