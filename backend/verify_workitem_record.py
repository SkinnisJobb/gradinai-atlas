"""
Verification for TASK-1046 (the workitem record: `node.body`, the
`acceptance_criterion` table, the abstract/detail PRESENTATION PROJECTION in
`_graph_node_json`, and extending `_reconcile_graph_schema` to reconcile
INDEXES).

No pytest suite exists in this repo yet — same standalone, run-it-and-read-it
convention as `verify_object_graph.py` / `verify_status_lifecycle.py` /
`verify_task_id_allocation.py` (the last is the closest sibling: same `check`
helper, same section lettering).

THREE SECTIONS, why separated (TASK-1044 merged the old SQLite-scratch
section B into the real Postgres this script now runs on throughout — there
is only one dialect left to prove anything against):

  A. Pure-Python `_project_prose_from_body` logic. No DB. Always runs.
  B. Proves the SHAPE of everything, against a real Postgres (via `testdb`,
     TASK-1044): `node.body` round-trips, `acceptance_criterion` ordering
     round-trips, the presentation projection end to end via
     `_graph_node_json`, and the one remaining node-deletion call site
     (`app._delete_node_dependents`) cleans up `acceptance_criterion` rows
     without an IntegrityError.
  C. Proves the ONE claim section B's own fresh `create_all` cannot: that
     `_reconcile_graph_schema()` actually reaches a database that ALREADY HAS
     the `node` table (no `body` column) and an ALREADY-EXISTING
     `acceptance_criterion`-shaped table missing an index — i.e. the exact
     "silently never appears on live" gap the owner named in the task. Runs
     against `app.engine` (same Postgres, own DROP/CREATE SCHEMA reset via
     `testdb`) so it exercises the actual module-global engine
     `_reconcile_graph_schema()` reads, not a parameter.

CHANGED (TASK-1058, STORY-atlas-retire-status-roundtrip): section B used to
also prove `ingest_graph._content_snapshot` stayed generic over a `body` key,
and that `ingest_graph._reconcile_nodes`'s / `ingest_world._reconcile_world_
nodes`'s `removed_ids` branches cleaned up `acceptance_criterion` rows without
an IntegrityError. Both boot-time reconcilers, and the `git_snapshot` machinery
`_content_snapshot` fed, are DELETED — there is only one node-deletion call
site left (`app._delete_node_dependents`, still proven below), so those checks
are deleted too, not rewired. See this file's git history if the shape is
ever needed again.

WHAT THIS SCRIPT DOES NOT PROVE. It does not import a real markdown file's
body (the importer is TASK-1052, out of scope here). It does not touch the
frontend at all (out of scope by the task's own instruction) — panel.js
already renders `abstract`/`detail` unchanged; section B's `_graph_node_json`
checks are the closest thing to "the panel would show something" this script
can prove without a browser. And a disposable local Postgres container is NOT
staging — the owner's own AGENTS.md instruction is to verify a schema-shaped
change on staging Postgres before merging; section C is independent evidence,
not a substitute for that.

Run (from the venv with fastapi/SQLAlchemy/PyYAML/jsonschema/psycopg2 installed):
    python verify_workitem_record.py
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


# Decide ATLAS_DB_URL BEFORE importing app — `app.py` builds its module-level
# `engine` from this at import time, and section C needs THAT engine to be
# the real Postgres so it can exercise the real `app._reconcile_graph_schema`
# (which reads the module-global `engine`, not a parameter) against it. No
# SQLite fallback exists any more — see testdb's docstring.
try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:
    print(f"FATAL: {e}")
    sys.exit(1)
PG_DSN = DB.url
os.environ["ATLAS_DB_URL"] = PG_DSN
os.environ.setdefault(
    "ATLAS_FRONTEND_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend"),
)
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")

import app  # noqa: E402
import models_graph as mg  # noqa: E402


# =========================================================================== #
# A. Pure-Python `_project_prose_from_body` — no DB
# =========================================================================== #
def verify_projection_logic() -> None:
    print("=" * 78)
    print("A. app._project_prose_from_body — pure logic, no DB")
    print("=" * 78)

    check("no body at all -> (None, None)",
          app._project_prose_from_body(None) == (None, None))
    check("whitespace-only body -> (None, None)",
          app._project_prose_from_body("   \n\n  \t \n") == (None, None))

    # A body with a leading H1 heading (the common case: `node.name` is
    # sourced from this exact line by ingest_backlog.py, so it must not come
    # back out again as the "description").
    body_with_heading = (
        "# The workitem record: what replaces the frontmatter contract\n\n"
        "**A decision task.** This is the first paragraph, spanning\n"
        "two lines because that is realistic prose.\n\n"
        "## Steps\n1. Put A/B/C to Alex.\n2. Produce the mapping table."
    )
    a, d = app._project_prose_from_body(body_with_heading)
    check("leading H1 is stripped, not surfaced as the abstract",
          a is not None and not a.startswith("#"), repr(a))
    check("abstract is exactly the first paragraph after the heading",
          a == "**A decision task.** This is the first paragraph, spanning\n"
               "two lines because that is realistic prose.", repr(a))
    check("detail is everything after the first paragraph, stripped",
          d == "## Steps\n1. Put A/B/C to Alex.\n2. Produce the mapping table.", repr(d))

    # A DESCRIPTION MUST BE A SENTENCE, NOT A SECTION LABEL. Measured after the
    # real import: 38 of 306 bodies open straight into a `##` after the title,
    # which made the board show "## What comes out" as 12% of its descriptions.
    # The abstract skips heading-only blocks; the headings are kept in detail.
    body_heading_first = (
        "# Redaction equivalence\n\n"
        "## The property\n\n"
        "The oracle cannot be deletion, because deleting proves nothing.\n\n"
        "## Steps\n1. Build the harness."
    )
    a2, d2 = app._project_prose_from_body(body_heading_first)
    check("a body opening with a `##` does NOT project the heading as its description",
          a2 == "The oracle cannot be deletion, because deleting proves nothing.", repr(a2))
    check("...and the skipped heading is NOT lost — it stays in detail, in order",
          d2 is not None and d2.startswith("## The property") and "## Steps" in d2, repr(d2))

    # Degenerate: a body that is nothing but headings has no prose to promote.
    # Falling back to the first block beats inventing an empty description.
    a3, _ = app._project_prose_from_body("# T\n\n## Only\n\n### Headings")
    check("a body of ONLY headings falls back to the first block rather than None",
          a3 == "## Only", repr(a3))

    # A body with NO heading at all — the strip must be a no-op, not eat a
    # real content line just because it starts with a literal '#'.
    body_no_heading = "Just prose, no heading at all.\n\nSecond paragraph here."
    a2, d2 = app._project_prose_from_body(body_no_heading)
    check("no leading heading -> first line preserved verbatim",
          a2 == "Just prose, no heading at all.", repr(a2))
    check("detail is the second paragraph", d2 == "Second paragraph here.", repr(d2))

    # A body that is JUST a heading (nothing follows) -> nothing to project.
    check("heading-only body -> (None, None), not an empty-string abstract",
          app._project_prose_from_body("# Just A Title\n\n") == (None, None))

    # A single-paragraph body (no blank line anywhere) -> abstract = the
    # whole thing, detail = None (not "").
    a3, d3 = app._project_prose_from_body("One paragraph, no second one.")
    check("single-paragraph body -> abstract is the whole body",
          a3 == "One paragraph, no second one.", repr(a3))
    check("...and detail is None, not an empty string", d3 is None, repr(d3))

    # Multiple blank lines between paragraph 1 and the rest — the regex must
    # tolerate more than exactly one blank line (`\n[ \t]*\n`, not anchored
    # to exactly two newlines).
    a4, d4 = app._project_prose_from_body("First.\n\n\n\nRest of it, further down.")
    check("extra blank lines between paragraphs still split correctly",
          a4 == "First." and d4 == "Rest of it, further down.", f"a={a4!r} d={d4!r}")

    # A body whose first non-heading line ITSELF looks like a heading further
    # down must not confuse the (single, non-repeating) H1-strip regex — only
    # the very first line is ever a candidate, via `count=1`.
    a5, _d5 = app._project_prose_from_body("# Title\n\nPara one.\n\n# Not stripped, mid-body")
    check("only the FIRST line can ever be stripped as a heading",
          a5 == "Para one.", repr(a5))


# =========================================================================== #
# B. Schema shape, ordering, projection, deletion cleanup — real Postgres
# =========================================================================== #
def verify_shape_and_cleanup() -> None:
    print("\n" + "=" * 78)
    print("B. Schema shape, ordering, projection, deletion cleanup")
    print("   (app.engine — the same live Postgres testdb handed this whole script)")
    print("=" * 78)

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    eng = app.engine
    DB.reset_schema()
    mg.Base.metadata.create_all(eng)  # everything this task added, fresh — the EASY path

    with Session(eng) as s:
        s.add(mg.NodeType(id="verify-task", attributes_schema={}, family="workitem"))
        s.flush()

        # -- B1. node.body round-trips verbatim, including blank lines -------
        raw_body = "# A Title\n\nPara one.\n\nPara two, with a  double  space."
        n1 = mg.Node(id="TASK-WI-1", type="verify-task", name="n1", attributes={}, body=raw_body)
        s.add(n1)
        s.flush()
        s.expire_all()
        n1r = s.get(mg.Node, "TASK-WI-1")
        check("node.body round-trips byte-for-byte", n1r.body == raw_body, repr(n1r.body))

        n_nobody = mg.Node(id="TASK-WI-NOBODY", type="verify-task", name="no body", attributes={})
        s.add(n_nobody)
        s.flush()
        s.expire_all()
        check("node.body defaults to NULL, not an empty string",
              s.get(mg.Node, "TASK-WI-NOBODY").body is None)

        # -- B2. acceptance_criterion ordering round-trips --------------------
        # Inserted OUT of position order on purpose — the read path must sort,
        # never rely on insertion/autoincrement id order.
        s.add_all([
            mg.AcceptanceCriterion(node_id="TASK-WI-1", position=2, text="third criterion (added first)"),
            mg.AcceptanceCriterion(node_id="TASK-WI-1", position=0, text="first criterion"),
            mg.AcceptanceCriterion(node_id="TASK-WI-1", position=1, text="second criterion"),
        ])
        s.flush()
        s.expire_all()
        rows = s.scalars(
            select(mg.AcceptanceCriterion)
            .where(mg.AcceptanceCriterion.node_id == "TASK-WI-1")
            .order_by(mg.AcceptanceCriterion.position)
        ).all()
        check("acceptance_criterion: 3 rows for the node", len(rows) == 3, len(rows))
        check("acceptance_criterion: ORDER BY position reconstructs authored order",
              [r.text for r in rows] == ["first criterion", "second criterion", "third criterion (added first)"],
              [r.text for r in rows])
        check("acceptance_criterion: position values themselves are 0,1,2 in that row order",
              [r.position for r in rows] == [0, 1, 2], [r.position for r in rows])

        # B3 ("_content_snapshot stays generic over a `body` key") — DELETED
        # (TASK-1058). `ingest_graph._content_snapshot`/`ingest_world.
        # _content_snapshot` and the `git_snapshot` column they fed are gone
        # along with the two boot-time reconcilers; there is nothing left to
        # prove generic over anything. See this file's git history if the
        # shape is ever needed again.

        # -- B4. the presentation projection via the REAL _graph_node_json ----
        # 4a. authored abstract WINS, unconditionally, over a present body —
        #     this is the ADR-0020-tier-authored-always-wins contract for
        #     world nodes with manifest prose (and any future write-API node).
        n_authored = mg.Node(id="obj-wi-authored", type="verify-task", name="authored",
                              attributes={}, abstract="Authored abstract.", detail="Authored detail.",
                              body="# Body Title\n\nBody paragraph that must NOT win.")
        s.add(n_authored)
        s.flush()
        d_authored = app._graph_node_json(n_authored)
        check("authored abstract wins over a present body — abstract unchanged",
              d_authored["abstract"] == "Authored abstract.", d_authored["abstract"])
        check("...and authored detail is untouched too (the pair is not split)",
              d_authored["detail"] == "Authored detail.", d_authored["detail"])

        # 4b. no authored abstract, a body IS present -> abstract/detail are
        #     PROJECTED — this is TASK-1046's headline acceptance criterion
        #     ("a task opened on the board shows its description instead of
        #     'No description authored yet.'").
        n_projected = mg.Node(id="TASK-WI-PROJECT", type="verify-task", name="projected",
                               attributes={}, body="# Ignored Title\n\nThe real description.\n\nMore detail.")
        s.add(n_projected)
        s.flush()
        d_projected = app._graph_node_json(n_projected)
        check("no authored abstract + a body -> abstract is PROJECTED from the body",
              d_projected["abstract"] == "The real description.", d_projected["abstract"])
        check("...and detail is projected from the remainder",
              d_projected["detail"] == "More detail.", d_projected["detail"])
        check("THE HEADLINE ACCEPTANCE CRITERION: the API payload no longer implies "
              "'No description authored yet.' for a node with only a body",
              bool(d_projected["abstract"]), d_projected["abstract"])

        # 4c. neither authored nor body -> stays null (not an empty string).
        n_neither = mg.Node(id="TASK-WI-NEITHER", type="verify-task", name="neither", attributes={})
        s.add(n_neither)
        s.flush()
        d_neither = app._graph_node_json(n_neither)
        check("no abstract, no body -> abstract stays None",
              d_neither["abstract"] is None, d_neither["abstract"])
        check("...and detail stays None", d_neither["detail"] is None, d_neither["detail"])

        s.commit()

        # -- B5. deletion cleanup: app._delete_node_dependents ----------------
        n_del = mg.Node(id="TASK-WI-DELETE", type="verify-task", name="to be deleted", attributes={})
        s.add(n_del)
        s.flush()
        s.add_all([
            mg.AcceptanceCriterion(node_id="TASK-WI-DELETE", position=0, text="ac 1"),
            mg.AcceptanceCriterion(node_id="TASK-WI-DELETE", position=1, text="ac 2"),
        ])
        s.commit()
        pre_count = len(s.scalars(
            select(mg.AcceptanceCriterion).where(mg.AcceptanceCriterion.node_id == "TASK-WI-DELETE")
        ).all())
        check("setup: TASK-WI-DELETE has 2 acceptance_criterion rows before deletion", pre_count == 2, pre_count)

        app._delete_node_dependents(s, "TASK-WI-DELETE")
        s.delete(s.get(mg.Node, "TASK-WI-DELETE"))
        try:
            s.commit()
            delete_ok = True
        except Exception as e:  # noqa: BLE001 — the FAIL case IS an IntegrityError
            s.rollback()
            delete_ok = False
            print(f"    (delete raised: {type(e).__name__}: {e})")
        check("app._delete_node_dependents + node delete succeeds with NO IntegrityError",
              delete_ok)
        remaining = s.scalars(
            select(mg.AcceptanceCriterion).where(mg.AcceptanceCriterion.node_id == "TASK-WI-DELETE")
        ).all()
        check("...and its acceptance_criterion rows are gone too", len(remaining) == 0, len(remaining))

        # B6/B7 ("deletion cleanup: ingest_graph._reconcile_nodes's/
        # ingest_world._reconcile_world_nodes's removed_ids branch") —
        # DELETED (TASK-1058). Both boot-time reconcilers are gone, so there
        # is no second node-deletion call site left to prove clean —
        # `app._delete_node_dependents` (B5, above) is the ONLY place a node
        # is ever actually deleted now (see models_graph.AcceptanceCriterion's
        # updated "DELETE BEHAVIOUR" comment). See this file's git history if
        # the shape is ever needed again.

    # `eng` is `app.engine` — not disposed here, section C (and main()'s own
    # summary) still needs it.


# =========================================================================== #
# C. Real Postgres — the index-reconciliation gap the owner named, on the actual
#    dialect, against a table that ALREADY EXISTS and is ALREADY POPULATED.
# =========================================================================== #
def verify_postgres_reconciliation() -> None:
    print("\n" + "=" * 78)
    print(f"C. Real Postgres ({PG_DSN}) — column + index reconciliation on an "
          "ALREADY-EXISTING, POPULATED table")
    print("=" * 78)

    import sqlalchemy as sa
    from sqlalchemy import insert, select, text
    from sqlalchemy.orm import Session

    eng = app.engine  # the module-global `_reconcile_graph_schema()` actually reads
    check("app.engine really is bound to the Postgres DSN this section targets",
          str(eng.url).startswith("postgresql"), str(eng.url))

    DB.reset_schema()  # wipes section B's data — this section builds its own PRE-shape

    # --- C1. Build the "PRE-TASK-1046" shape: every graph table this task
    # touches EXCEPT `node.body` (stripped from a clone) and WITHOUT
    # `acceptance_criterion` at all — i.e. exactly what live Postgres looks
    # like the moment before this task's migration runs. `to_metadata()`
    # copies real Column/Table objects (not a hand-written approximation), so
    # the "pre" schema is byte-identical to the real one apart from the two
    # deliberate omissions.
    pre_md = sa.MetaData()
    for table in mg.Base.metadata.sorted_tables:
        if table.name == "acceptance_criterion":
            continue  # this task's new table — simulate "does not exist yet"
        t2 = table.to_metadata(pre_md)
        if table.name == "node":
            t2._columns.remove(t2.c.body)  # simulate "column does not exist yet"
    pre_md.create_all(eng)

    have_before = set(sa.inspect(eng).get_table_names())
    check("PRE-shape: node table exists", "node" in have_before)
    check("PRE-shape: node has NO body column",
          "body" not in {c["name"] for c in sa.inspect(eng).get_columns("node")})
    check("PRE-shape: acceptance_criterion does not exist yet",
          "acceptance_criterion" not in have_before, have_before)

    # --- C2. Populate it — a REAL row, so the ALTER TABLE below runs against
    # a table that is not empty (an empty-table ALTER proves nothing; a
    # populated one is the live case).
    with Session(eng) as s:
        s.execute(insert(mg.NodeType.__table__).values(
            id="verify-task", attributes_schema={}, family="workitem"))
        s.execute(insert(mg.Node.__table__).values(
            id="TASK-PRE-EXISTING", type="verify-task", name="pre-existing populated row",
            attributes={}, origin="git"))
        s.commit()

    # --- C3. THE REAL BOOT SEQUENCE: create_all (creates acceptance_criterion,
    # a brand-new table, WITH its index — the easy path) THEN
    # _reconcile_graph_schema() (must ADD node.body to the row that already
    # exists above, additively).
    mg.Base.metadata.create_all(eng)
    app._reconcile_graph_schema()

    have_after = {c["name"] for c in sa.inspect(eng).get_columns("node")}
    check("node.body now exists on the table that ALREADY HAD a populated row",
          "body" in have_after, have_after)
    with Session(eng) as s:
        pre_row = s.get(mg.Node, "TASK-PRE-EXISTING")
        check("the pre-existing row SURVIVED the ALTER (not recreated, not dropped)",
              pre_row is not None and pre_row.name == "pre-existing populated row")
        check("...and its new body column is NULL (additive, no data fabricated)",
              pre_row.body is None)

    # --- C4. THE INDEX GAP ITSELF. `acceptance_criterion` was just created a
    # moment ago BY create_all (C3) — its own index came along for free with
    # that CREATE TABLE, so dropping the table's own index and re-adding it
    # via the reconciler simulates "this index was declared in the model
    # AFTER the table's first deploy", the literal scenario the task names.
    with eng.begin() as conn:
        conn.execute(text("DROP INDEX ix_acceptance_criterion_node_position"))
    idx_before = {i["name"] for i in sa.inspect(eng).get_indexes("acceptance_criterion")}
    check("C4 setup: the index is really gone before reconciling",
          "ix_acceptance_criterion_node_position" not in idx_before, idx_before)

    app._reconcile_graph_schema()
    idx_after = {i["name"] for i in sa.inspect(eng).get_indexes("acceptance_criterion")}
    check("_reconcile_graph_schema() re-creates a MISSING index on an "
          "ALREADY-EXISTING table — this is the gap the task names",
          "ix_acceptance_criterion_node_position" in idx_after, idx_after)

    # --- C5. A wholly SYNTHETIC, never-before-declared index, injected via
    # the `metadatas=` override the function already accepts — the most
    # literal reproduction of "a full-text index on body, added later, on a
    # table that already exists". Attached to the ALREADY-POPULATED `node`
    # table from C1-C3, not a fresh one.
    synth_md = sa.MetaData()
    mg.NodeType.__table__.to_metadata(synth_md)  # node.type FK's target — needed so
    node_clone = mg.Node.__table__.to_metadata(synth_md)  # sorted_tables can resolve it
    sa.Index("ix_verify_synthetic_never_seen_before", node_clone.c.owner)
    idx_node_before = {i["name"] for i in sa.inspect(eng).get_indexes("node")}
    check("C5 setup: the synthetic index name has never existed on `node`",
          "ix_verify_synthetic_never_seen_before" not in idx_node_before)

    app._reconcile_graph_schema(metadatas=[synth_md])
    idx_node_after = {i["name"] for i in sa.inspect(eng).get_indexes("node")}
    check("a BRAND NEW index (never declared before this call) reaches the "
          "already-existing, already-populated `node` table",
          "ix_verify_synthetic_never_seen_before" in idx_node_after, idx_node_after)

    # --- C6. Additive-only + idempotent: a second reconcile pass touches
    # nothing that already exists (no drop, no duplicate-create error), and
    # a THIRD pass over the FULL real metadata (not the synthetic one) still
    # succeeds with everything from C3-C5 intact.
    idx_node_pre_rerun = {i["name"] for i in sa.inspect(eng).get_indexes("node")}
    app._reconcile_graph_schema(metadatas=[synth_md])  # re-run: must be a clean no-op
    idx_node_post_rerun = {i["name"] for i in sa.inspect(eng).get_indexes("node")}
    check("re-running the reconciler is a no-op — no index dropped, none duplicated",
          idx_node_pre_rerun == idx_node_post_rerun, f"{idx_node_pre_rerun} vs {idx_node_post_rerun}")

    app._reconcile_graph_schema()  # the REAL metadata list this time, like a real boot
    idx_ac_final = {i["name"] for i in sa.inspect(eng).get_indexes("acceptance_criterion")}
    check("a real full reconcile pass afterwards still leaves the earlier fix in place",
          "ix_acceptance_criterion_node_position" in idx_ac_final, idx_ac_final)

    # `eng` is `app.engine` — not disposed here, `main()`'s summary runs right after.


def verify_result_split_roundtrip() -> None:
    """SALVAGED from verify_result_split.py (TASK-1059).

    That script's subject was the body/result split as wired through
    `import_workitems.py` -- a one-time importer of a directory this epic
    deletes. But `models_graph.split_result` / `.reassemble` are NOT test-only:
    reassemble's own docstring says TASK-1067's write-API gate depends on it to
    detect whether a body edit also touched the result section. That is
    production code on every PATCH, and it needed to outlive the importer.

    Rewritten against synthetic bodies rather than the 306 markdown files, so it
    tests the functions instead of the corpus -- and so the hard case can be
    stated outright instead of hoping a real file happens to contain it.
    """
    print("\n-- R. split_result / reassemble round-trip (salvaged) --")

    no_result = "# A title\n\nSome prose.\n\n## Steps\n1. Do the thing.\n"
    check("a body with no result section splits to None", mg.split_result(no_result) is None)

    at_end = no_result + "\n## Result\n\nIt worked.\n"
    split = mg.split_result(at_end)
    check("a body with a trailing result section splits", split is not None)
    if split is not None:
        body_wo, heading, text, idx = split
        check("the result text is separated out", "It worked." in text, repr(text)[:120])
        check("the remaining body no longer carries it", "It worked." not in body_wo)
        check("reassemble reproduces the original byte-for-byte",
              mg.reassemble(body_wo, heading, text, idx) == at_end,
              repr(mg.reassemble(body_wo, heading, text, idx))[:160])

    # THE HARD CASE, and the reason split_result stops at the next `##` rather
    # than running to end-of-file: real bodies carry sections AFTER the result
    # (Sequencing, Key numbers, Rejected). Splitting to EOF would swallow them
    # into the result row and lose them from the body.
    mid_body = (no_result + "\n## Result\n\nIt worked.\n\n"
                "## Key numbers\n\n- 306 files\n- 1,494 criteria\n")
    split_mid = mg.split_result(mid_body)
    check("a result section in the MIDDLE of a body splits", split_mid is not None)
    if split_mid is not None:
        body_wo, heading, text, idx = split_mid
        check("content AFTER the result stays in the body, not in the result row",
              "Key numbers" in body_wo and "Key numbers" not in text,
              f"body_has={'Key numbers' in body_wo} text_has={'Key numbers' in text}")
        check("mid-body reassemble is byte-exact",
              mg.reassemble(body_wo, heading, text, idx) == mid_body)

        # DISCRIMINATION. A round-trip check that cannot fail proves nothing, so
        # perturb one character and require the comparison to notice.
        perturbed = mg.reassemble(body_wo + "X", heading, text, idx)
        check("perturbing the body by one character breaks the round-trip "
              "(this check can actually fail)", perturbed != mid_body)

    for alias in ("## Outcome", "## Results"):
        body = no_result + f"\n{alias}\n\nDone.\n"
        got = mg.split_result(body)
        check(f"{alias!r} is recognised as a result heading", got is not None)
        if got is not None:
            check(f"{alias!r} round-trips byte-for-byte",
                  mg.reassemble(got[0], got[1], got[2], got[3]) == body)


def main() -> None:
    verify_projection_logic()
    verify_result_split_roundtrip()
    verify_shape_and_cleanup()
    verify_postgres_reconciliation()

    print("\n" + "=" * 78)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED (including the real-Postgres reconciliation proof)")


if __name__ == "__main__":
    main()
