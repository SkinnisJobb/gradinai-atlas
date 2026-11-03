"""
Verification script for TASK-850 (node/edge/node_type/probe schema) and
TASK-851 (backlog ingest onto node+edges) — ADR 0019.

No pytest suite exists in this repo yet, so this is a standalone, run-it-and-
read-it script: a real, disposable Postgres via `testdb` (TASK-1044) bound to
the same ATLAS_DB_URL override app.py already supports, populated with the
curated fixture in `testseed.py`, printing proof of every acceptance criterion.

CHANGED (TASK-1058, STORY-atlas-retire-status-roundtrip): this script used to
call `app.startup()` with `ATLAS_BACKLOG_DIR` set, which ran the (now deleted)
boot-time backlog reconciler (`ingest_graph.ingest_backlog_graph`) to populate
the graph. `startup()` no longer reads any file — see its own docstring — so
this script now populates the SAME real backlog via `import_workitems.
import_workitems`, the one-time importer that is Postgres's actual source of
truth now (AGENTS.md "Shipped 2026-08-10: the backlog lives in Postgres").
Every node this script asserts against comes from that call, not from boot.
Sections that existed ONLY to prove the deleted reconcilers' PROTECT/HANDOFF/
GHOST/divergence behaviour (`verify_reconcile`, `verify_origin_provenance`,
the "idempotent rebuild" bonus, 14j, 14l) are deleted, not rewired — there is
no reconciler behaviour left for them to assert. A NEW section (15) asserts
the thing that replaces all of them: starting the app against an already-
populated database changes NOTHING (TASK-1058 acceptance #5) — "boot is a
read".

Run (from the venv with fastapi/SQLAlchemy/PyYAML/jsonschema/psycopg2 installed):
    python verify_object_graph.py
"""
from __future__ import annotations

import base64
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb  # noqa: E402

import sqlalchemy as sa
from sqlalchemy import func, select

# Only used to resolve `node.source` for GIT-ORIGIN rows (section 3). The
# fixture creates none, so this is dead against the fixture -- it matters
# when this script is pointed at a database that still holds file-backed
# rows. Env-overridable so that case is not pinned to one machine; the
# default is where the planning repo lives on the owner's workstation.
AGENTPLANNINGSETUP_ROOT = os.environ.get(
    "ATLAS_PLANNING_ROOT", r"C:\Projects\AgentPlanningSetup")


# Must be resolved BEFORE `import app` — app.py reads ATLAS_DB_URL at module
# import time to build its module-level `engine`. There is no SQLite fallback
# to degrade to any more: if no real Postgres can be reached or started, this
# exits non-zero with a clear message instead of proceeding.
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

# --------------------------------------------------------------------------- #
# Auth configuration for the harness (TASK-967 / TASK-968).
#
# THESE ARE NOT A BACKDOOR. They are the same three values uvicorn refuses to
# start without — `passwords.py` and `auth.py` both raise at IMPORT when they
# are missing, naming the key, which is exactly the fail-closed behaviour the
# container gets. The gate below runs in FULL: every check mints a REAL
# credential (a real Argon2id password verifier, a real HMAC'd service token)
# and presents it to the real middleware.
#
# `ATLAS_AUTH_DISABLED` does not exist and must never be added. A test backdoor
# is a production backdoor with a comment on it.
#
# `ATLAS_BREACH_CHECK=bundled` keeps the breach screening zero-egress so the
# harness never makes an outbound HTTP call.
# --------------------------------------------------------------------------- #
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")

#: TestClient must speak https, because `__Host-` cookies REQUIRE `Secure` and
#: an http cookie jar will not store or replay one. That is physics, not a knob
#: — it is the same reason TASK-964 gives the LAN a real certificate.
TEST_ORIGIN = "https://testserver"

# STORY-oss-08: this script pins the default surface: every write route's scope, and /api/health's `deploy`,
# so it runs with every feature on whatever the caller set; verify_feature_flags.py
# proves the switched-off side. Before the app import: main.py reads the flags then.
os.environ.pop("ATLAS_DISABLED_FEATURES", None)
import app  # noqa: E402
import auth  # noqa: E402
import jsonschema  # noqa: E402
import passwords  # noqa: E402
import models_graph  # noqa: E402
import testseed  # noqa: E402
from models_auth import AuthServiceToken, AuthSession, AuthUser  # noqa: E402
from models_graph import Edge, Node, NodeType, Probe  # noqa: E402
from attention import derive as attention_derive  # noqa: E402
from attention import default_goal_config as attention_default_goal_config  # noqa: E402
from attention import recompute_derived as attention_recompute_derived  # noqa: E402
import distribution  # noqa: E402  (pure data: which copy, and its default seed profile)
import settings  # noqa: E402

# STORY-oss-23 A3b: what this process's configuration says about the two estate-valued defaults
# this script leans on, read from `distribution.py` through `settings` (an env var still wins).
#  * V0_ON: whether the v0 Component/Gate/Requirement/Decision tables exist and their four routes
#    (/api/tree, /api/summary, /api/priority, /api/goals) answer. On under the homelab copy, as
#    every check here always assumed; off under the neutral copy, where those routes are 404.
#  * BOOT_SEEDS_SET: whether a boot onto an empty graph seeds the system node set (every profile
#    but `homelab` does). Section 22 needs a truly empty store, so there it stubs that seeding out.
V0_ON = settings.load().legacy_v0
V0_ROUTES = ("/api/tree", "/api/summary", "/api/priority", "/api/goals")
BOOT_SEEDS_SET = settings.load().seed != "homelab"

failures: list[str] = []
#: Checks belonging to TASK-967/968, tracked separately so an auth regression is
#: never confused with the pre-existing backlog-data drift in sections 8-9.
auth_failures: list[str] = []
_in_auth_section = False

#: Set by verify_auth(); every later API call carries it. The read API is
#: authenticated now, so the section-7 checks would 401 without a real token.
AUTH_BEARER: str | None = None


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)
        if _in_auth_section:
            auth_failures.append(label)


def main() -> None:
    print("=" * 78)
    print("Atlas v2 object-graph verification — TASK-9001 / TASK-851")
    print(f"DB:      {os.environ['ATLAS_DB_URL']}")
    print("Fixture: backend/testseed.py (curated — no second repo, no markdown)")
    print("=" * 78)

    # Exercises the ACTUAL app.py startup path: create_all for both the v0
    # Component metadata and the new models_graph metadata, the auth tables,
    # schema reconcile and node_type registry seeding. Boot no longer reads
    # any file (TASK-1058) — the real backlog is loaded straight after, via
    # the same importer Postgres's data actually came from.
    app.startup()
    # WAS: import the 309 markdown files from a hardcoded path in a second repo.
    # The corpus was never this script's subject -- every assertion below is
    # about a RULE (a done leaf is green, a parent takes the worst child colour,
    # a closed parent has no open children), and the backlog was just the
    # nearest available pile of nodes. That pile cost six stale assertions in
    # TASK-1068, one of which hid a real defect for weeks, and it pinned this
    # script to one Windows workstation. `testseed` is 31 nodes that contain
    # each case on purpose and prove it in self_check().
    with app.session() as s:
        testseed.build(s)
    with app.session() as s:
        testseed.self_check(s)
    # `import_workitems` is a one-time IMPORTER, not a reconciler — it never
    # calls `attention.recompute_derived` (unlike the old boot-time ingests,
    # which did as their last step). Real Postgres has correctly-cached
    # `attention`/`completion`/`completed_at` today because the world
    # ingest's boot-time recompute call covered the WHOLE graph right up
    # until this task deleted it (TASK-1058); this harness has to do
    # explicitly, once, what a real boot already did for that data.
    with app.session() as s:
        attention_recompute_derived(s)

    with app.session() as s:
        # -- 1. All new tables create cleanly ---------------------------------
        print("\n-- 1. Tables create cleanly --")
        existing_tables = set(sa.inspect(app.engine).get_table_names())
        for t in ("node", "node_type", "edge", "probe", "probe_result"):
            check(f"table '{t}' exists", t in existing_tables)
        # old tables still present too (coexistence, TASK-9001 acceptance)
        for t in ("component", "gate", "requirement", "decision"):
            if V0_ON:
                check(f"pre-existing table '{t}' untouched", t in existing_tables)
            else:
                check(f"v0 table '{t}' is not created (ATLAS_LEGACY_V0 off, distribution "
                      f"{distribution.NAME!r})", t not in existing_tables)

        # -- 2. Ingest produced node/edge rows; report counts -----------------
        print("\n-- 2. Node/edge counts --")
        nodes_by_type = dict(s.execute(select(Node.type, func.count()).group_by(Node.type)).all())
        edges_by_type = dict(s.execute(select(Edge.edge_type, func.count()).group_by(Edge.edge_type)).all())
        total_nodes = sum(nodes_by_type.values())
        total_edges = sum(edges_by_type.values())
        print(f"nodes by type: {nodes_by_type}  (total {total_nodes})")
        print(f"edges by type: {edges_by_type}  (total {total_edges})")
        check("epic/story/task nodes all exist", all(t in nodes_by_type for t in ("epic", "story", "task")))
        check("contains edges exist", edges_by_type.get("contains", 0) > 0)
        check("depends_on edges exist", edges_by_type.get("depends_on", 0) > 0)

        # -- 3. Every node has a non-null source pointing at an existing file -
        print("\n-- 3. node.source and node.origin --")
        all_nodes = s.scalars(select(Node)).all()
        # REWRITTEN. This used to assert that EVERY node has a non-null `source`
        # resolving to a real file on disk. That was true when every row arrived
        # through a file ingest. It has not been true since Postgres became
        # authoritative: rows created through the write API, by the one-time
        # importers, or by this fixture have no file behind them and never will.
        # Asserting it globally would now be asserting the old world.
        #
        # What is still true, and worth guarding: a row that CLAIMS to come from
        # git must name a path that exists. That is the claim `verify_arrival`
        # depends on when it diffs the database against the markdown.
        git_backed = [n for n in all_nodes if models_graph.origin_is_git_value(n.origin)]
        bad_source = []
        for n in git_backed:
            full = os.path.join(AGENTPLANNINGSETUP_ROOT, n.source.replace("/", os.sep)) if n.source else ""
            if not n.source or not os.path.isfile(full):
                bad_source.append((n.id, n.source))
        check("every GIT-origin node names a source file that exists",
              len(bad_source) == 0, f"{len(bad_source)} broken: {bad_source[:5]}")
        print(f"  git-origin nodes: {len(git_backed)} of {len(all_nodes)}")

        # The fixture's own rows must be honest about where they came from:
        # atlas-origin, no source. A seeded row that claimed a file would make
        # the check above vacuous by lying rather than by being scoped.
        dishonest = [n.id for n in all_nodes
                     if not models_graph.origin_is_git_value(n.origin) and n.source]
        check("no atlas-origin node claims a source file", len(dishonest) == 0,
              f"{len(dishonest)}: {dishonest[:5]}")

        # -- 4. node_type rows exist and attributes validate -------------------
        print("\n-- 4. node_type rows + attribute validation --")
        types = {nt.id: nt for nt in s.scalars(select(NodeType)).all()}
        check("node_type rows exist for epic/story/task", all(t in types for t in ("epic", "story", "task")),
              f"have: {sorted(types)}")
        bad_attrs = []
        for n in all_nodes:
            nt = types.get(n.type)
            if nt is None:
                bad_attrs.append((n.id, "no matching node_type"))
                continue
            try:
                jsonschema.validate(instance=n.attributes or {}, schema=nt.attributes_schema or {})
            except jsonschema.ValidationError as e:
                bad_attrs.append((n.id, e.message))
        check("every node's attributes validate against its node_type's JSON-Schema", len(bad_attrs) == 0,
              f"{len(bad_attrs)} invalid: {bad_attrs[:5]}")

        # Prove validation is actually ENFORCED (not coincidence): a write
        # with attributes that violate the schema must be rejected.
        bad_node = Node(id="TASK-verify-bad", type="task", name="bad",
                         attributes={"depends_on": "not-a-list"})  # schema wants a list
        s.add(bad_node)
        try:
            s.flush()
            check("a write with schema-violating attributes is rejected", False, "flush unexpectedly succeeded")
            s.rollback()
        except ValueError as e:
            check("a write with schema-violating attributes is rejected", True, str(e)[:140])
            s.rollback()

        # -- 5. Spot check: task -> story -> epic (up); depends_on (lateral) --
        print("\n-- 5. Graph traversal spot check (not just a tree) --")
        contains_child_to_parent = dict(
            s.execute(select(Edge.dst, Edge.src).where(Edge.edge_type == "contains")).all()
        )
        depends_rows = s.execute(select(Edge.src, Edge.dst).where(Edge.edge_type == "depends_on")).all()
        pick = None
        for src, dst in depends_rows:
            n = s.get(Node, src)
            if n and n.type == "task" and src in contains_child_to_parent:
                pick = (src, dst)
                break
        check("found a task with both a contains-parent chain and a depends_on edge", pick is not None)
        if pick:
            task_id, dep_id = pick
            story_id = contains_child_to_parent.get(task_id)
            epic_id = contains_child_to_parent.get(story_id) if story_id else None
            print(f"  {task_id}  --contains(up)-->  {story_id}  --contains(up)-->  {epic_id}")
            print(f"  {task_id}  --depends_on(lateral)-->  {dep_id}")
            check("upward contains: task -> story resolves", story_id is not None)
            check("upward contains: story -> epic resolves", epic_id is not None)
            check("story parent is actually type='story'", story_id and s.get(Node, story_id).type == "story")
            check("epic grandparent is actually type='epic'", epic_id and s.get(Node, epic_id).type == "epic")
            check("lateral depends_on target node exists", s.get(Node, dep_id) is not None)

        # -- 6. No orphan edges --------------------------------------------------
        print("\n-- 6. No orphan edges --")
        node_ids = {n.id for n in all_nodes}
        all_edges = s.scalars(select(Edge)).all()
        orphans = [(e.id, e.src, e.dst) for e in all_edges if e.src not in node_ids or e.dst not in node_ids]
        check("no orphan edges (src and dst both exist)", len(orphans) == 0,
              f"{len(orphans)} orphans: {orphans[:5]}")

        # Prove it's actually enforced at the DB layer (Postgres's native FK
        # constraint), not just "happens to be true" — a dangling insert must fail.
        dangling = Edge(src="TASK-does-not-exist", dst=all_nodes[0].id, edge_type="contains")
        s.add(dangling)
        try:
            s.flush()
            check("FK constraint rejects a dangling edge insert", False, "flush unexpectedly succeeded")
            s.rollback()
        except Exception as e:
            check("FK constraint rejects a dangling edge insert", True, type(e).__name__)
            s.rollback()

        # The "Bonus: idempotent rebuild" section that stood here MOVED to
        # verify_workitem_import.py. Its subject was the IMPORTER (re-running it
        # reproduces identical counts), not the graph rules this script is about
        # — and keeping it here was the last thing forcing this script to read
        # the markdown backlog. verify_workitem_import.py's section 5 already
        # proved the same property in more detail, counter by counter.

        # -- 8. Attention + completion + attention-count (TASK-854 / TASK-855) --
        # STATE semantics (ADR 0019): red = needs attention now; grey = not started.
        print("\n-- 8. Attention engine + completion + attention-count --")
        # Values were cached onto node columns by the ingest recompute pass; the
        # derive() map gives us the done/ip/todo breakdown too.
        derived = attention_derive(s)

        def A(nid):  # cached column value
            n = s.get(Node, nid)
            return n.attention if n else None

        # 8-histogram: new STATE-semantics distribution (expect mostly grey+green, a few red).
        hist: dict[str, int] = {}
        for v in derived.values():
            hist[v["attention"]] = hist.get(v["attention"], 0) + 1
        print(f"  attention histogram: {dict(sorted(hist.items(), key=lambda kv: -kv[1]))}")

        # 8a. Leaf attention from OWN state: done->green, blocked->red, planned->grey.
        check("done task TASK-9001 is green", A("TASK-9001") == "green", f"got {A('TASK-9001')}")
        check("planned task TASK-9006 is grey (not started, not red)",
              A("TASK-9006") == "grey", f"got {A('TASK-9006')}")
        check("blocked task TASK-9008 is red", A("TASK-9008") == "red", f"got {A('TASK-9008')}")

        # 8b. Worst-child rollup (grey excluded): green children + grey children -> green.
        #
        # THE FIXTURE IS CHOSEN FROM THE DATA, not written down. This used to name
        # a server-standup story and assert "5 done + 1 grey". The story grew to
        # 10 children and the assertion failed for two years' worth of reasons that
        # had nothing to do with rollup — and, being dismissed as "known stale", it
        # hid a real one: that story is `done` with 4 children still open, which is
        # why it is red. A snapshot of one node is not a test of a rule.
        #
        # What the rule says: a parent takes the WORST child colour, with grey
        # excluded. So any story whose children are all green-or-grey, with at least
        # one of each, must be green — and must sit strictly between 0 and 1 complete.
        # The parent must not itself be closed: a closed parent is scored by its own
        # state, which is a different rule and the subject of 9d below.
        def _kids(nid):
            return [s.get(Node, e.dst) for e in s.scalars(
                select(Edge).where(Edge.src == nid, Edge.edge_type == "contains")).all()]

        def _pick_rollup_fixture():
            best = None
            for st in s.scalars(select(Node).where(Node.type == "story")).all():
                if (st.attributes or {}).get("status") in ("done", "cancelled", "accepted", "closed"):
                    continue
                kids = _kids(st.id)
                if not kids:
                    continue
                colours = {k.attention for k in kids}
                if not colours <= {"green", "grey"} or "green" not in colours or "grey" not in colours:
                    continue
                if best is None or len(kids) > len(_kids(best.id)):
                    best = st
            return best

        FIX = _pick_rollup_fixture()
        check("a rollup fixture exists (an open story with only green and grey children)",
              FIX is not None,
              "no story in the corpus has only green+grey children — the rollup rule "
              "below cannot be exercised against real data")
        if FIX is not None:
            fix_kids = _kids(FIX.id)
            n_done = sum(1 for k in fix_kids
                         if (k.attributes or {}).get("status") in ("done", "cancelled"))
            print(f"  rollup fixture: {FIX.id} — {len(fix_kids)} children, "
                  f"{n_done} done, colours={sorted({k.attention for k in fix_kids})}")
            check(f"{FIX.id} rolls up green (green + grey children; grey excluded from worst)",
                  A(FIX.id) == "green", f"got {A(FIX.id)}")

        # 8c. A fully-done subtree rolls up green (STORY-seed-all-done: 850-853 done).
        check("fully-done STORY-seed-all-done rolls up green",
              A("STORY-seed-all-done") == "green", f"got {A('STORY-seed-all-done')}")

        # 8d. attention_count = red+orange descendants (now genuinely meaningful — real problems only).
        acount_fix = s.get(Node, FIX.id).attention_count if FIX is not None else 0
        acount_clean = s.get(Node, "STORY-seed-all-done").attention_count
        if FIX is not None:
            check(f"attention_count == 0 on {FIX.id} (a grey not-started child is NOT a problem)",
                  acount_fix == 0, f"got {acount_fix}")
        check("attention_count == 0 on fully-done STORY-seed-all-done",
              acount_clean == 0, f"got {acount_clean}")
        check("attention_count == 0 on green leaf TASK-9001",
              s.get(Node, "TASK-9001").attention_count == 0)
        # somewhere with a blocked (red) descendant must carry a non-zero count
        max_acount = max(n.attention_count for n in s.scalars(select(Node)).all())
        check("some node carries attention_count > 0 (red descendants exist in the backlog)",
              max_acount > 0, f"max attention_count = {max_acount}")

        # 8e. completion is a sane fraction everywhere; independent spot values.
        all_completions = [n.completion for n in s.scalars(select(Node)).all()]
        check("every node completion is a fraction in [0,1]",
              all(0.0 <= c <= 1.0 for c in all_completions))
        check("done leaf TASK-9001 completion == 1.0", s.get(Node, "TASK-9001").completion == 1.0,
              f"got {s.get(Node, 'TASK-9001').completion}")
        check("planned leaf TASK-9006 completion == 0.0", s.get(Node, "TASK-9006").completion == 0.0,
              f"got {s.get(Node, 'TASK-9006').completion}")
        if FIX is not None:
            d_fix = derived[FIX.id]
            fix_completion = s.get(Node, FIX.id).completion
            # EXPECTED IS DERIVED FROM THE CHILDREN, not written down: completion is
            # the done fraction of the leaves below. Restated here rather than read
            # from attention.py, so agreeing with it means something.
            fix_kids = _kids(FIX.id)
            expect = sum(1 for k in fix_kids
                         if (k.attributes or {}).get("status") in ("done", "cancelled")) / len(fix_kids)
            print(f"  {FIX.id}: attention={A(FIX.id)} completion={fix_completion:.3f} "
                  f"(expected {expect:.3f} from {len(fix_kids)} children) "
                  f"counts={{done:{d_fix['done']}, in_progress:{d_fix['in_progress']}, todo:{d_fix['todo']}}} "
                  f"attention_count={acount_fix}")
            check(f"{FIX.id} completion == its done fraction ({expect:.3f})",
                  abs(fix_completion - expect) < 1e-9, f"got {fix_completion}")
            # completion (how done) and attention (needs-a-look) are independent axes:
            # this story is green AND only partly complete.
            check("completion and attention are independent (green story, partial completion)",
                  A(FIX.id) == "green" and 0.0 < fix_completion < 1.0,
                  f"attention={A(FIX.id)} completion={fix_completion}")

        # 8f. no attention value is grey purely from a hand-set stale colour —
        # every leaf's attention is reproducible from its status (drift-proof).
        stale = []
        for n in s.scalars(select(Node)).all():
            if n.id not in derived:
                continue
            if n.attention != derived[n.id]["attention"]:
                stale.append(n.id)
        check("cached attention matches a fresh recompute (no drift)", len(stale) == 0,
              f"{len(stale)} drifted: {stale[:5]}")

        # -- 9. Goal-directed urgency layer (attention = worst(state, urgency)) --
        print("\n-- 9. Goal-directed urgency (blocks-active-goal-work) --")
        urg_hist: dict[str, int] = {}
        for v in derived.values():
            urg_hist[v["urgency"]] = urg_hist.get(v["urgency"], 0) + 1
        print(f"  urgency histogram: {dict(sorted(urg_hist.items(), key=lambda kv: -kv[1]))}")
        print(f"  attention histogram (state+urgency): "
              f"{dict(sorted(hist.items(), key=lambda kv: -kv[1]))}")

        def V(nid):
            return derived[nid]

        # 9a. FRONTIER-ONLY RED. In the chain 840->841->842->843->(845,846,847)
        # (all planned, each depends_on the previous), only TASK-9010 has no
        # incomplete deps -> it is the actionable frontier -> RED. Everything
        # blocked behind it is ORANGE (on the critical path, can't act yet).
        v840 = V("TASK-9010")
        check("chain frontier TASK-9010: own state is grey (not started)", v840["state"] == "grey",
              f"got {v840['state']}")
        check("chain frontier TASK-9010: urgency red (actionable now, blocks goal work)",
              v840["urgency"] == "red", f"got {v840['urgency']}")
        check("chain frontier TASK-9010: attention red = worst(grey, red)", v840["attention"] == "red")
        check("TASK-9010 urgency_blocks names the work waiting behind it (incl TASK-9011)",
              "TASK-9011" in v840["urgency_blocks"], f"got {v840['urgency_blocks'][:5]}")

        # 9b. Everything blocked BEHIND the frontier is orange, not red.
        for blocked_id in ("TASK-9011", "TASK-9012", "TASK-9013"):
            vb = V(blocked_id)
            check(f"blocked-behind {blocked_id}: urgency orange (own dep not done)",
                  vb["urgency"] == "orange", f"got {vb['urgency']}")
            check(f"blocked-behind {blocked_id}: attention orange = worst(grey, orange)",
                  vb["attention"] == "orange", f"got {vb['attention']}")

        # 9b'. No node in a not-done dependency chain is red unless its own deps are
        # all done — i.e. red is reserved for the actionable frontier.
        #
        # Reads the `depends_on` EDGE, not `attributes["depends_on"]` — the
        # only representation that exists now (TASK-1048 decision: depends_on
        # gets exactly ONE home, the edge table; see app._strip_depends_on_
        # mirror's docstring). `import_workitems.py` never writes the legacy
        # attribute mirror, so reading it here would silently make `deps`
        # always empty and this check vacuously true.
        false_reds = []
        for nid, v in derived.items():
            if v["urgency"] == "red":
                deps = [row[0] for row in s.execute(
                    select(Edge.dst).where(Edge.src == nid, Edge.edge_type == "depends_on")
                ).all() if s.get(Node, row[0])]
                undone = [d for d in deps if derived[d]["completion"] < 1.0
                          and s.get(Node, d).attributes.get("status") not in ("done", "accepted", "closed")]
                if undone:
                    false_reds.append((nid, undone))
        check("every RED-urgency node is actionable now (no incomplete own deps)",
              len(false_reds) == 0, f"offenders: {false_reds[:5]}")

        # 9c. A not-started leaf that blocks NOTHING stays grey.
        v005 = V("TASK-9016")
        check("TASK-9016 (pending, blocks nothing) stays grey", v005["attention"] == "grey"
              and v005["urgency"] == "none", f"att={v005['attention']} urg={v005['urgency']}")

        # 9d. Done stays green; blocked stays red — urgency never fires on a done node.
        check("done TASK-9001 stays green (done -> no urgency)",
              V("TASK-9001")["attention"] == "green" and V("TASK-9001")["urgency"] == "none")
        check("blocked TASK-9008 stays red (state red stands)",
              V("TASK-9008")["attention"] == "red")
        done_with_urgency = [nid for nid, v in derived.items()
                             if s.get(Node, nid).attributes.get("status") in ("done", "accepted", "closed")
                             and v["urgency"] != "none"]
        check("no DONE node ever carries urgency", len(done_with_urgency) == 0,
              f"offenders: {done_with_urgency[:5]}")

        # 9e. Example "urgent because it blocks X" nodes (hand-inspectable).
        examples = sorted(
            ((nid, v) for nid, v in derived.items()
             if v["urgency"] != "none" and s.get(Node, nid).type == "task"),
            key=lambda kv: (kv[1]["urgency"], kv[0]),
        )[:6]
        for nid, v in examples:
            print(f"  URGENT {nid:12} {v['urgency']:6} (state {v['state']}) blocks {v['urgency_blocks'][:3]}")

        # 9f. Config re-derivation: default = all epics active weight 1.0.
        nodes_by_id = {n.id: n for n in s.scalars(select(Node)).all()}
        cfg = attention_default_goal_config(nodes_by_id)
        # The count is DERIVED, not written down. It used to be `len(cfg) == 6`,
        # which went stale the moment an epic was added and then failed for
        # years-of-backlog reasons that had nothing to do with goal config. What
        # this check is actually about is the RULE — every epic is a goal, active,
        # weight 1.0 — so it asserts that against however many epics exist.
        epic_ids = {n.id for n in nodes_by_id.values() if n.type == "epic"}
        check("default goal config: one active goal per epic, weight 1.0",
              all(c["active"] and c["weight"] == 1.0 for c in cfg.values())
              and set(cfg) == epic_ids,
              f"{len(cfg)} goals vs {len(epic_ids)} epics; "
              f"missing={sorted(epic_ids - set(cfg))[:3]} extra={sorted(set(cfg) - epic_ids)[:3]}")

        # Deactivate ALL goals -> urgency vanishes everywhere; attention falls back to state.
        cfg_off = {eid: {"weight": 0.0, "active": False} for eid in cfg}
        d_off = attention_derive(s, cfg_off)
        check("all-goals-off: no node carries urgency",
              all(v["urgency"] == "none" for v in d_off.values()))
        check("all-goals-off: TASK-9013 falls back to its grey state",
              d_off["TASK-9013"]["attention"] == "grey", f"got {d_off['TASK-9013']['attention']}")

        # Deactivate ONLY EPIC-seed-platform -> its work loses urgency, but a
        # bottleneck under EPIC-seed-property keeps it (re-derives correctly).
        cfg_one = attention_default_goal_config(nodes_by_id)
        cfg_one["EPIC-seed-platform"] = {"weight": 0.0, "active": False}
        d_one = attention_derive(s, cfg_one)
        check("deactivating EPIC-seed-platform drops TASK-9013 to grey",
              d_one["TASK-9013"]["attention"] == "grey", f"got {d_one['TASK-9013']['attention']}")
        check("TASK-9021 (under still-active EPIC-seed-property) keeps its red urgency",
              d_one["TASK-9021"]["urgency"] == "red", f"got {d_one['TASK-9021']['urgency']}")

        # -- 11. A CLOSED PARENT HAS NO OPEN CHILDREN -------------------------
        #
        # Added by TASK-1068, and the reason it exists is worth stating. Five of
        # that task's six failures were assertions that had gone stale. The
        # sixth — "no DONE node ever carries urgency", offender
        # a server-standup story — was not stale at all: that story is
        # `done` with four of its ten children still open. It had been read as
        # collateral from the stale ones and waved through on every run for
        # weeks, because nothing in the suite named the actual defect.
        #
        # 9d sees the SYMPTOM (a closed node still carrying urgency, because
        # urgency flows up from open work below it). This is the CAUSE, and it
        # is a plain structural fact about the graph — no derivation, no goal
        # config, nothing to go stale. If it ever fails, the backlog is lying:
        # either the parent closed too early, or its remaining children should
        # have been closed, cancelled, or moved.
        #
        # `cancelled` parents are exempt on purpose. Abandoning a story does not
        # assert its tasks were finished, so open children under it are not a
        # contradiction — only a completing closure (done/accepted/closed)
        # claims the work below it is finished. A cancelled CHILD does count as
        # closed: it is terminal, and nothing is waiting on it.
        print("\n-- 11. Backlog hygiene: a closed parent has no open children --")
        COMPLETING = {"done", "accepted", "closed"}
        TERMINAL = COMPLETING | {"cancelled"}

        kids_of: dict[str, list[Node]] = {}
        for e in s.scalars(select(Edge).where(Edge.edge_type == "contains")).all():
            child = s.get(Node, e.dst)
            if child is not None:
                kids_of.setdefault(e.src, []).append(child)

        contradictions = []
        for parent in s.scalars(select(Node)).all():
            if (parent.attributes or {}).get("status") not in COMPLETING:
                continue
            open_kids = [k.id for k in kids_of.get(parent.id, [])
                         if (k.attributes or {}).get("status") not in TERMINAL]
            if open_kids:
                contradictions.append((parent, sorted(open_kids),
                                       len(kids_of.get(parent.id, []))))

        # Printed in full, not just the first few: every offender is one line of
        # work for somebody, and a truncated list is how this got missed before.
        for parent, open_kids, n_all in sorted(contradictions, key=lambda c: -len(c[1])):
            print(f"  {parent.type:6} {parent.id:42} "
                  f"open={len(open_kids)}/{n_all}  {open_kids}")
        check("no closed parent has open children",
              len(contradictions) == 0,
              f"{len(contradictions)} closed parent(s) with open children: "
              + ", ".join(f"{p.id} ({len(k)}/{n})"
                          for p, k, n in contradictions[:5]))

        # Section 10 ("a probe on an existing node SURVIVES a re-run of the
        # importer — identity preserved") MOVED to verify_workitem_import.py,
        # section 5b. It asserted that the IMPORTER never delete-then-recreates
        # a surviving id, which is a claim about the importer and needs the real
        # markdown to make. This script is about the rules over the graph, and
        # it no longer reads a file.

    # Sections 11 ("Upsert reconcile on an isolated temp backlog": add/update/
    # remove/PROTECT/HANDOFF/GHOST/divergence) and 13 ("Provenance survives
    # the boot ingest") are DELETED, not rewired (TASK-1058). Both existed
    # ONLY to prove the two boot-time reconcilers' behaviour — the diff-based
    # upsert against a directory scan, the PROTECT/HANDOFF/GHOST state
    # machine, the origin-flip-on-claim rule — and that machinery is deleted
    # along with the reconcilers themselves (`ingest_graph.py`/
    # `ingest_world.py` no longer exist). There is nothing left for these
    # sections to assert; see this file's git history if the shape is ever
    # needed again. Section 15 (added by this task, below) asserts what
    # replaces them: starting the app changes NOTHING.

    # -- 12. THE GATE. Runs BEFORE verify_api(), which needs the token it mints.
    verify_auth()

    # -- 7. Graph read API via FastAPI TestClient (TASK-852 + 854/855 payload) --
    verify_api()

    # -- 14. Graph write API (TASK-1017): POST/PATCH/DELETE node+edge --------
    verify_graph_write()

    # -- 20. write-scope enforcement (TASK-1086/1087) -------------------------
    verify_write_scope_enforcement()

    # -- 15. TASK-1058 acceptance #5: boot is a READ ---------------------------
    verify_boot_is_a_read()

    # -- 21. TASK-1127: the bundled breach corpus reaches the image -----------
    verify_breach_corpus_shipped()

    # -- 22. TASK-1129: an empty store is a 200, not a 404 ---------------------
    # LAST ON PURPOSE, and the only destructive section: it needs a database
    # with no nodes in it, so it reseeds the graph out from under everything
    # above. Anything added after this call runs against an empty store --
    # which is why section 21 above must stay ABOVE it.
    verify_empty_store_is_not_an_error()

    print("\n" + "=" * 78)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)  "
              f"({len(auth_failures)} in the TASK-967/968 auth section)")
        for f in failures:
            print(f"  - {'[auth] ' if f in auth_failures else ''}{f}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")


# =========================================================================== #
# 12. AuthGate — deny by default (TASK-967) and service tokens (TASK-968)
#
# ORDER IS THE POINT OF THIS SECTION. Every route is asserted to 401
# UNAUTHENTICATED **first**, and only then is a real credential minted and the
# 200 asserted. Asserting only the 200 path would pass just as happily against
# a gate that was never installed, which is the exact defect this ships to stop.
#
# Nothing here sets a bypass flag: the users, the Argon2id verifiers and the
# HMAC'd service tokens are real rows created through the same code paths
# `admin.py` (TASK-970) will use, and they are presented to the same middleware
# a request from the internet hits.
# =========================================================================== #

def _client(*, bearer: str | None = None, follow: bool = False):
    from fastapi.testclient import TestClient
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else None
    return TestClient(app.app, base_url=TEST_ORIGIN, follow_redirects=follow,
                      headers=headers)


_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _csrf_from(html_text: str) -> str:
    m = _CSRF_RE.search(html_text)
    return m.group(1) if m else ""


#: Every route a signed-out caller must be refused. Includes the mount's old
#: paths, the FastAPI docs, and a path that does not exist — deny-by-default
#: means a 404 must not be reachable pre-auth either.
#:
#: "/" is DELIBERATELY NOT here any more (ADR 0035, 2026-08-25): it is now a
#: public landing page, and a signed-out caller reaching it is the intended
#: behaviour, not a gate failure. See "12b-bis" below for its own assertions.
#: "/board" stays exactly as gated as everything else in this list.
GATED_PATHS = [
    "/api/health", "/api/tree", "/api/summary", "/api/priority", "/api/goals",
    "/api/graph?focus=EPIC-seed-platform", "/api/graph/tree", "/api/vocabulary",
    "/api/node/TASK-9001/doc?path=docs/nodes/x.md", "/api/me",
    "/board", "/board/legacy", "/board/v2",
    "/assets/d3.min.js", "/assets/marked.min.js", "/assets/favicon.ico",
    "/static/board.html", "/static/index.html", "/static/",
    "/docs", "/redoc", "/openapi.json",
    "/definitely-not-a-real-route",
]

NEW_PASSWORD = "vindruta lampskarm 41 morgondagg"
SECOND_PASSWORD = "kastanjeblad 62 slipsknut ravgryt"


def verify_auth() -> None:
    global _in_auth_section, AUTH_BEARER
    _in_auth_section = True
    print("\n-- 12. AuthGate: deny by default (TASK-967) + service tokens (TASK-968) --")

    anon = _client()

    # ---- 12a. BOOTSTRAP FAILS CLOSED: empty auth_user, nobody can sign in ---
    print("\n  12a. bootstrap fails closed (empty auth_user)")
    with app.session() as s:
        check("auth_user starts EMPTY (no seeded account, no bootstrap env var)",
              s.scalar(select(func.count()).select_from(AuthUser)) == 0)
    r = anon.get("/auth/login")
    check("GET /auth/login serves with an empty auth_user table", r.status_code == 200,
          f"got {r.status_code}")
    check("GET /auth/login sets the __Host-atlas_pre cookie",
          "__Host-atlas_pre" in r.headers.get("set-cookie", ""),
          r.headers.get("set-cookie", "")[:90])
    boot_csrf = _csrf_from(r.text)
    check("the login form embeds a synchronizer token", len(boot_csrf) > 20)
    r = anon.post("/auth/login", data={"username": "alex", "password": "anything at all here",
                                       "csrf_token": boot_csrf, "next": "/"},
                  headers={"Origin": TEST_ORIGIN})
    check("with no accounts, a login attempt returns the generic failure",
          r.status_code == 303 and "error=1" in r.headers.get("location", ""),
          f"{r.status_code} -> {r.headers.get('location')}")
    check("a failed login sets NO session cookie",
          "__Host-atlas_session" not in r.headers.get("set-cookie", ""))
    with app.session() as s:
        check("a failed login creates NO session row",
              s.scalar(select(func.count()).select_from(AuthSession).where(
                  AuthSession.kind == "full")) == 0)

    # ---- 12b. 401 FIRST, for every route --------------------------------- #
    print("\n  12b. every route refuses an unauthenticated caller (asserted BEFORE any 200)")
    anon.cookies.clear()
    codes_json, codes_html = {}, {}
    basic_challenges = []
    for path in GATED_PATHS:
        rj = anon.get(path, headers={"Accept": "application/json"})
        rh = anon.get(path, headers={"Accept": "text/html"})
        codes_json[path] = rj.status_code
        codes_html[path] = (rh.status_code, rh.headers.get("location"))
        for resp in (rj, rh):
            if "basic" in (resp.headers.get("www-authenticate") or "").lower():
                basic_challenges.append(path)
        check(f"401 unauthenticated: {path}", rj.status_code == 401, f"got {rj.status_code}")
        if rj.status_code == 401:
            check(f"  uniform 401 body: {path}",
                  rj.json() == {"error": "unauthorized", "detail": "Authentication is required."},
                  str(rj.json())[:80])
    print(f"  JSON status codes: {codes_json}")
    check("NO response anywhere carries WWW-Authenticate: Basic",
          not basic_challenges, str(basic_challenges))

    print("\n  12b'. content negotiation: HTML 302s, /api/* never does")
    for path in ("/board", "/assets/d3.min.js", "/definitely-not-a-real-route"):
        code, loc = codes_html[path]
        check(f"HTML GET {path} -> 302 /auth/login", code == 302 and (loc or "").startswith("/auth/login"),
              f"got {code} {loc}")
    for path in ("/api/health", "/api/graph/tree", "/api/me"):
        code, loc = codes_html[path]
        check(f"/api/* NEVER redirects, even for text/html: {path}", code == 401,
              f"got {code} {loc}")
    rhead = anon.head("/board", headers={"Accept": "text/html"})
    check("HEAD /board with Accept: text/html -> 302 (curl -sSI is how this is measured)",
          rhead.status_code == 302, f"got {rhead.status_code}")

    print("\n  12b-bis. \"/\" is a public landing page now (ADR 0035), and it is NOT "
          "a fixed response")
    rj_root = anon.get("/", headers={"Accept": "application/json"})
    rh_root = anon.get("/", headers={"Accept": "text/html"})
    check("anon GET / -> 200 (it is in auth.PUBLIC_ROUTES now)",
          rj_root.status_code == 200 and rh_root.status_code == 200,
          f"json={rj_root.status_code} html={rh_root.status_code}")
    check("...content-type is HTML, not JSON — this route ignores Accept, same as /board always did",
          "text/html" in rh_root.headers.get("content-type", ""),
          rh_root.headers.get("content-type"))
    check("...carries NO shared-cache-eligible response (private, no-cache) — see the "
          "2026-08-18 ASSET_CACHE_CONTROL incident this pairing is copied from",
          rh_root.headers.get("cache-control") == "private, no-cache",
          rh_root.headers.get("cache-control"))
    check("...and Vary: Cookie, since the SAME path answers differently for a signed-in caller",
          rh_root.headers.get("vary") == "Cookie", rh_root.headers.get("vary"))
    check("...has exactly one <h1>", rh_root.text.count("<h1>") == 1,
          str(rh_root.text.count("<h1>")))
    check("...NO <script> tag anywhere (same posture as /auth/login)",
          "<script" not in rh_root.text)
    check("...links to /auth/login (the Log-in control)", 'href="/auth/login"' in rh_root.text)
    check("...is NOT the board (no board-only contract id leaks through)",
          "tab-world" not in rh_root.text and "canvasWrap" not in rh_root.text)
    rhead_root = anon.head("/", headers={"Accept": "text/html"})
    check("HEAD / -> 200 too (it is genuinely public, not redirect-only)",
          rhead_root.status_code == 200, f"got {rhead_root.status_code}")

    print("\n  12b''. a Bearer-carrying request is NEVER redirected")
    for bogus in ("atlas_svc_bogus", "atlas_svc_000000_bogus",
                  "atlas_svc_000000_" + "A" * 43):
        r = anon.get("/", headers={"Accept": "text/html", "Authorization": f"Bearer {bogus}"})
        check(f"Bearer {bogus[:24]}… + Accept: text/html -> 401, not 302",
              r.status_code == 401, f"got {r.status_code} {r.headers.get('location')}")
        check("  WWW-Authenticate: Bearer error=\"invalid_token\"",
              r.headers.get("www-authenticate") == 'Bearer error="invalid_token"',
              str(r.headers.get("www-authenticate")))

    # ---- 12c. route enumeration: nothing is outside the gate -------------- #
    print("\n  12c. route enumeration — every registered route is gated or explicitly public")
    from starlette.routing import Mount
    from route_table import live_routes  # TASK-1361: app.routes is no longer flat
    mounts = [r for r in live_routes(app.app)
              if isinstance(getattr(r, "original_route", r), Mount)]
    check("the /static mount is GONE (no Mount left on the app at all)", not mounts,
          str([getattr(m, "path", m) for m in mounts]))

    uncovered, methods_seen = [], {}
    for route in live_routes(app.app):
        path = getattr(route, "path", None)
        if path is None:
            continue
        methods = set(getattr(route, "methods", None) or ["GET"])
        # UNION, not overwrite (TASK-1050 fix): FastAPI registers one Route
        # object PER DECORATOR, so a path like `/api/node/{node_id}` — PATCH
        # (edit_node), DELETE (delete_node), and now GET (TASK-1050's
        # get_node) — appears here as THREE separate `route` iterations, each
        # reporting only its OWN verb. The old `methods_seen[path] = methods`
        # silently kept only the LAST one seen, so adding the GET route (which
        # this same commit does) would have made this path's recorded methods
        # become `{"GET"}` and DROPPED IT from `mutating` below — reporting a
        # route with a live PATCH+DELETE as non-mutating, for a reason with
        # nothing to do with what changed. Verified this was a real, not
        # theoretical, regression: reproduced the wrong `{"GET"}` result
        # before writing this fix.
        methods_seen.setdefault(path, set()).update(methods)
        if path in auth.PUBLIC_ROUTES:
            continue
        probe = re.sub(r"\{[^}]+\}", "probe", path)
        if probe.startswith("/api/graph") and "focus" not in probe:
            probe += "?focus=EPIC-seed-platform"
        if probe.endswith("/doc"):
            probe += "?path=x"
        verb = "post" if "POST" in methods else "get"
        resp = getattr(anon, verb)(probe, headers={"Accept": "application/json"},
                                   **({"data": {}} if verb == "post" else {}))
        if resp.status_code not in (401, 403, 302):
            uncovered.append((path, probe, resp.status_code))
    check("NO route is reachable unauthenticated unless it is in PUBLIC_ROUTES",
          not uncovered, str(uncovered))
    # `/auth/privacy` was added deliberately by TASK-941: a privacy notice that
    # requires a login is not a privacy notice, because the person deciding
    # whether to hand over their data has not signed in yet. It renders two
    # module constants and reads no request state at all.
    check("PUBLIC_ROUTES is exactly the documented frozenset (additions need an ADR note; "
          "\"/\" was added by ADR 0035, 2026-08-25 — the public landing page)",
          auth.PUBLIC_ROUTES == frozenset({
              "/", "/healthz", "/readyz", "/auth/login", "/auth/password/change",
              "/auth/denied", "/auth/privacy", "/favicon.ico"}),
          str(sorted(auth.PUBLIC_ROUTES)))
    check("/api/health is NOT public (it leaks env vars, host paths and exception strings)",
          "/api/health" not in auth.PUBLIC_ROUTES)

    mutating = {p: m for p, m in methods_seen.items() if m - {"GET", "HEAD"}}
    # This list is a WHITELIST, and adding to it is meant to be deliberate: a new
    # mutating route turns this red until someone writes it down here, which is
    # how TASK-1076's PUT/DELETE announced itself the moment it was added.
    #
    # TASK-1086: the graph-write half is no longer a second copy of the route
    # list — it is DERIVED from `auth.WRITE_SCOPE_ROUTES`, the one authoritative
    # map `_check_write_scope` reads. A route added there with no entry here
    # (impossible, since this IS that map) or added here without a scope
    # decision (this assertion catches the SECOND half — see section 20 below,
    # which asserts every entry in the map is actually enforced) turns this red.
    check("NO state-changing GET: mutating routes are exactly the three auth POSTs "
          "plus every route in auth.WRITE_SCOPE_ROUTES (TASK-1017 node/edge CRUD, "
          "TASK-1048 workitem authoring, TASK-1076 node-doc authoring, "
          "ADR 0032 intent, TASK-1086 status/status is folded in too)",
          set(mutating) == (
              {"/auth/login", "/auth/password/change", "/auth/logout"}
              | {path for (_method, path) in auth.WRITE_SCOPE_ROUTES}
          ),
          str(sorted(mutating)))

    # ---- 12d. contentless liveness / readiness ---------------------------- #
    print("\n  12d. /healthz and /readyz")
    r = anon.get("/healthz")
    check("/healthz is 200 unauthenticated", r.status_code == 200, f"got {r.status_code}")
    check("/healthz body is exactly {'ok': true} — no version, config or ingest verdict",
          r.json() == {"ok": True}, str(r.json()))
    r = anon.get("/readyz")
    check("/readyz is 200 unauthenticated and returns exactly {'ok':true,'db':true}",
          r.status_code == 200 and r.json() == {"ok": True, "db": True},
          f"{r.status_code} {r.json()}")
    # /auth/password/change is exempt at the GATE but must still need a principal.
    r = anon.get("/auth/password/change", headers={"Accept": "text/html"})
    check("/auth/password/change is exempt at the gate but still refuses an "
          "unauthenticated caller in its handler",
          r.status_code == 302 and r.headers.get("location", "").startswith("/auth/login"),
          f"got {r.status_code} {r.headers.get('location')}")
    r = anon.post("/auth/password/change", data={"new_password": "x"},
                  headers={"Accept": "application/json", "Origin": TEST_ORIGIN})
    check("POST /auth/password/change unauthenticated -> 401", r.status_code == 401,
          f"got {r.status_code}")
    r = _client(bearer="x").get("/auth/password/change")
    check("a service token can never reach the password-change page",
          r.status_code in (401, 403), f"got {r.status_code}")

    print("\n  12d'. the per-IP ceiling is namespaced (a bearer flood must not lock the login form)")
    for _ in range(passwords.IP_FAILURE_LIMIT + 2):
        anon.get("/api/graph/tree", headers={"Authorization": "Bearer atlas_svc_000000_x"})
    r = anon.get("/api/graph/tree", headers={"Authorization": "Bearer atlas_svc_000000_x"})
    check("a token-spray is rate-limited (429 with Retry-After)",
          r.status_code == 429 and r.headers.get("retry-after"),
          f"got {r.status_code} {r.headers.get('retry-after')}")
    r = anon.get("/auth/login")
    check("…and the LOGIN FORM is untouched by it (separate limiter bucket)",
          r.status_code == 200, f"got {r.status_code}")
    import inspect
    check("a VALID token never touches the limiter at all (a healthy ingest can "
          "never be throttled, however many calls it makes)",
          "_IP_LIMITER" not in inspect.getsource(auth._resolve_token))
    auth._IP_LIMITER = passwords.IpFailureLimiter()  # clear the spray for later sections

    # ---- 12e. unknown Host -> 421 ----------------------------------------- #
    print("\n  12e. host allow-list")
    r = anon.get("/auth/login", headers={"Host": "evil.example"})
    check("an unknown Host gets 421 Misdirected Request before any handler runs",
          r.status_code == 421, f"got {r.status_code}")
    r = anon.get("/healthz", headers={"Host": "evil.example"})
    check("the 421 applies even to an exempt route", r.status_code == 421, f"got {r.status_code}")

    # ---- 12f. mint a REAL service token, then assert 200 ------------------ #
    print("\n  12f. a real service token (minted, hashed, presented to the real gate)")
    with app.session() as s:
        bot = AuthUser(id="00000000-0000-4000-8000-00000000b071", idp="local",
                       subject="bot-sync", is_service=True, label="sync-backlog-to-board",
                       must_change_password=False, role="member")
        s.add(bot)
        s.flush()
        raw_token, token_row = auth.mint_service_token(
            s, owner=bot, scopes=["ingest"], label="sync-backlog-to-board")
        raw_revoked, revoked_row = auth.mint_service_token(
            s, owner=bot, scopes=["read"], label="to-be-revoked")
        raw_expired, expired_row = auth.mint_service_token(
            s, owner=bot, scopes=["read"], label="to-be-expired")
        revoked_row.revoked_at = auth._now()
        expired_row.expires_at = auth._now() - __import__("datetime").timedelta(days=1)
        s.commit()
        token_prefix = token_row.prefix
        revoked_prefix = revoked_row.prefix
    AUTH_BEARER = raw_token
    check("the minted token matches atlas_svc_<6hex>_<43 urlsafe>",
          bool(re.fullmatch(r"atlas_svc_[0-9a-f]{6}_[A-Za-z0-9_-]{43}", raw_token)),
          raw_token[:18] + "…")

    with app.session() as s:
        row = s.scalar(select(AuthServiceToken).where(AuthServiceToken.prefix == token_prefix))
        stored = {c.name: getattr(row, c.name) for c in AuthServiceToken.__table__.columns}
        leaked = [k for k, v in stored.items() if isinstance(v, str) and raw_token in v]
        check("THE RAW TOKEN IS NOWHERE IN THE ROW (only a prefix and an HMAC digest)",
              not leaked, f"leaked in {leaked}")
        check("token_hash is a 64-char hex digest, not the token",
              len(row.token_hash) == 64 and row.token_hash != raw_token)
        check("scopes were minted NARROW (sync gets 'ingest', never 'private')",
              row.scopes == "ingest", row.scopes)
        check("expires_at defaults to 365 days (surfaced, never silent)",
              row.expires_at is not None)

    bearer = _client(bearer=raw_token)
    print("  now the SAME routes with a real credential:")
    for path in ("/api/health", "/api/tree", "/api/summary", "/api/priority", "/api/goals",
                 "/api/graph?focus=EPIC-seed-platform", "/api/graph/tree", "/api/vocabulary",
                 "/api/me", "/", "/board"):
        r = bearer.get(path, headers={"Accept": "application/json"})
        if path in V0_ROUTES and not V0_ON:
            check(f"404 with a real service token: {path} (a v0 route, ATLAS_LEGACY_V0 off)",
                  r.status_code == 404, f"got {r.status_code}")
            continue
        check(f"200 with a real service token: {path}", r.status_code == 200,
              f"got {r.status_code}")
    r = bearer.get("/static/board.html")
    check("/static/board.html is 404 even WITH a credential (the mount is deleted)",
          r.status_code == 404, f"got {r.status_code}")
    for path in ("/redoc", "/openapi.json", "/static/index.html", "/static/"):
        r = bearer.get(path)
        check(f"{path} is 404 with a credential (it does not exist)", r.status_code == 404,
              f"got {r.status_code}")
    # /docs is the handbook site now (STORY-atlas-system-node-handbook, ADR 0047), not FastAPI's
    # schema UI: still gated (GATED_PATHS above), and with a credential it is the static index,
    # never a schema dump of every route.
    r = bearer.get("/docs")
    check("/docs with a credential is the handbook index, not FastAPI's schema UI",
          r.status_code == 200 and "Atlas handbook" in r.text
          and "swagger" not in r.text.lower() and "openapi" not in r.text.lower(),
          f"got {r.status_code}")
    r = bearer.get("/api/me")
    check("/api/me identifies the machine principal",
          r.json().get("is_service") is True and r.json().get("username") == "bot-sync",
          str(r.json()))
    check("/api/me carries the token's scopes", r.json().get("scopes") == ["ingest"],
          str(r.json().get("scopes")))
    r = bearer.get("/api/health")
    health_body = r.json()
    check("/api/health carries node_count/edge_count (TASK-1058's replacement "
          "for the deleted ingest-verdict fields)",
          "node_count" in health_body and "edge_count" in health_body,
          str(sorted(health_body))[:200])
    check("/api/health carries token_warnings", "token_warnings" in health_body)
    # TASK-1154: the stale-deploy signal. No server checkout is mounted in a test run, so both
    # halves must say `unknown` and raise NO warning -- a signal that is red wherever it cannot
    # see is one people learn to ignore. The states themselves are in verify_deploy_status.py.
    deploy = health_body.get("deploy") or {}
    check("/api/health carries deploy, and with no checkout mounted it is `unknown` with no "
          "warnings (never a false stale)",
          deploy.get("component") == "atlas-api"
          and (deploy.get("image") or {}).get("state") == "unknown"
          and deploy.get("warnings") == [],
          str(deploy)[:300])
    # `ingest_ok`/`ingest_problems`/`ingest`/`last_successful_ingest_at`/
    # `ingest_stale` are GONE (TASK-1058) — they reported the outcome of the
    # two boot-time reconcilers, which no longer exist. Assert their absence,
    # not just their old values, so a re-introduction is caught (same
    # discipline verify_status_lifecycle.py uses for the status-override
    # fields TASK-1056 removed).
    for gone_field in ("ingest_ok", "ingest_problems", "ingest",
                       "last_successful_ingest_at", "ingest_stale"):
        check(f"/api/health no longer carries `{gone_field}` (TASK-1058)",
              gone_field not in health_body, str(sorted(health_body))[:200])

    with app.session() as s:
        used = s.scalar(select(AuthServiceToken).where(
            AuthServiceToken.prefix == token_prefix)).last_used_at
        check("last_used_at advances on use (rotation can be verified before revoking)",
              used is not None, str(used))

    # ---- 12g. revoked / expired / never-existed are byte-identical -------- #
    print("\n  12g. a revoked, an expired and a never-existed token are indistinguishable")
    shapes = []
    for label, tok in (("revoked", raw_revoked), ("expired", raw_expired),
                       ("never existed", "atlas_svc_ffffff_" + "Z" * 43)):
        r = anon.get("/api/graph/tree", headers={"Accept": "text/html",
                                                 "Authorization": f"Bearer {tok}"})
        shapes.append((r.status_code, r.headers.get("www-authenticate"), r.content))
        check(f"{label} token -> 401 with a JSON body, never a 302",
              r.status_code == 401 and r.headers.get("content-type", "").startswith("application/json"),
              f"got {r.status_code} {r.headers.get('content-type')}")
    check("all three responses are BYTE-IDENTICAL (status, challenge and body)",
          len(set(shapes)) == 1, str([s[0] for s in shapes]))

    print("\n  12g'. revocation is immediate, with no restart")
    with app.session() as s:
        check("the revoked row still EXISTS (revocation is a stamp, never a DELETE)",
              s.scalar(select(AuthServiceToken).where(
                  AuthServiceToken.prefix == revoked_prefix)) is not None)
    r = _client(bearer=raw_revoked).get("/api/graph/tree")
    check("a revoked token is dead on the very next request", r.status_code == 401,
          f"got {r.status_code}")
    check("the OTHER token for the same principal still works "
          "(rotation without downtime is possible)",
          bearer.get("/api/graph/tree").status_code == 200)

    # ---- 12h. both credential types -> 400 -------------------------------- #
    print("\n  12h. cookie + Authorization together")
    r = _client().get("/api/graph/tree", headers={
        "Authorization": f"Bearer {raw_token}",
        "Cookie": "__Host-atlas_session=whatever",
    })
    check("presenting BOTH a cookie and an Authorization header -> 400, never resolved",
          r.status_code == 400, f"got {r.status_code}")

    # ---- 12i. a real human login, and the forced-change interlock --------- #
    print("\n  12i. a real sign-in with a real Argon2id verifier")
    temp_password = passwords.generate_temp_password()
    with app.session() as s:
        # Clear the pre-session rows the earlier sections' probes left behind, so
        # the rotation samples below count only this flow's rows. Test hygiene —
        # nothing here changes how the gate behaves.
        s.execute(sa.delete(AuthSession))
        s.commit()
        user = AuthUser(
            id="00000000-0000-4000-8000-0000000000pr"[:36], idp="local", subject="probe",
            name="Probe", role="member", is_service=False,
            password_hash=passwords.hash_password(temp_password),
            hash_scheme=passwords.HASH_SCHEME,
            must_change_password=True,
            temp_expires_at=passwords.temp_credential_expires_at(),
        )
        s.add(user)
        s.commit()
        user_id = user.id

    browser = _client()
    r = browser.get("/auth/login")
    csrf = _csrf_from(r.text)
    with app.session() as s:
        pre_hashes = [row.id_hash for row in s.scalars(
            select(AuthSession).where(AuthSession.kind == "pre")).all()]
    check("GET /auth/login mints exactly one kind='pre' row", len(pre_hashes) == 1,
          f"{len(pre_hashes)} rows")

    print("\n  12i'. CSRF is real, not SameSite")
    naked = _client()
    r = naked.post("/auth/login", data={"username": "probe", "password": temp_password})
    check("POST /auth/login with no pre-session and no token -> 403", r.status_code == 403,
          f"got {r.status_code}")
    check("  and it sets no cookie", "set-cookie" not in {k.lower() for k in r.headers})
    r = browser.post("/auth/login",
                     data={"username": "probe", "password": temp_password, "csrf_token": csrf},
                     headers={"Origin": "https://evil.example"})
    check("POST /auth/login with a valid pre-session but a foreign Origin -> 403",
          r.status_code == 403, f"got {r.status_code}")
    r = browser.post("/auth/login",
                     data={"username": "probe", "password": temp_password,
                           "csrf_token": "not-the-token"},
                     headers={"Origin": TEST_ORIGIN})
    check("POST /auth/login with a wrong synchronizer token -> 403", r.status_code == 403,
          f"got {r.status_code}")

    print("\n  12i''. login rotates the session id (sample 1 of 3: the pre row)")
    r = browser.post("/auth/login",
                     data={"username": "probe", "password": temp_password, "csrf_token": csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("a correct temp password signs in", r.status_code == 303, f"got {r.status_code}")
    check("a must_change principal is sent straight at the change page",
          r.headers.get("location") == "/auth/password/change", r.headers.get("location"))
    set_cookie = r.headers.get("set-cookie", "")
    for attribute in ("__Host-atlas_session=", "HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        check(f"session cookie carries {attribute}", attribute.lower() in set_cookie.lower(),
              set_cookie[:160])
    check("session cookie carries NO Domain attribute", "domain=" not in set_cookie.lower())
    check("session cookie carries NO Max-Age (expiry is server-side)",
          "max-age=" not in set_cookie.lower().split("__host-atlas_pre")[0])

    with app.session() as s:
        rows = s.scalars(select(AuthSession)).all()
        kinds = sorted(r.kind for r in rows)
        full_1 = [r.id_hash for r in rows if r.kind == "full"]
    check("the 'pre' row is GONE and exactly one 'full' row exists (sample 2 of 3)",
          kinds == ["full"], str(kinds))
    check("the full session id_hash differs from the pre session's",
          full_1 and full_1[0] not in pre_hashes)

    print("\n  12i'''. THE FORCED-CHANGE INTERLOCK — refused at the GATE, not in the UI")
    interlock = []
    for path in ("/", "/board", "/api/health", "/api/graph/tree", "/assets/d3.min.js",
                 "/api/me", "/made-up-path-nobody-added"):
        rj = browser.get(path, headers={"Accept": "application/json"})
        rh = browser.get(path, headers={"Accept": "text/html"})
        interlock.append((path, rj.status_code, rh.status_code, rh.headers.get("location")))
        check(f"must_change + JSON -> 403 (never 200): {path}", rj.status_code == 403,
              f"got {rj.status_code}")
        if path.startswith("/api/"):
            check(f"  /api/* never redirects even for a must_change principal: {path}",
                  rh.status_code == 403, f"got {rh.status_code}")
        else:
            check(f"must_change + HTML -> 302 /auth/password/change: {path}",
                  rh.status_code == 302 and rh.headers.get("location") == "/auth/password/change",
                  f"got {rh.status_code} {rh.headers.get('location')}")
    check("NOT ONE path outside the allowlist returned 200",
          all(row[1] != 200 and row[2] != 200 for row in interlock), str(interlock))
    r = browser.get("/auth/password/change")
    check("the change page itself IS reachable by a must_change principal",
          r.status_code == 200, f"got {r.status_code}")

    print("\n  12i''''. the change requires the CURRENT password (ASVS 6.2.3)")
    change_csrf = _csrf_from(r.text)
    r = browser.post("/auth/password/change",
                     data={"current_password": "wrong wrong wrong wrong",
                           "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD,
                           "csrf_token": change_csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("a change without the current password is refused", r.status_code == 403,
          f"got {r.status_code}")
    r = browser.post("/auth/password/change",
                     data={"current_password": temp_password,
                           "new_password": "Password1234567",
                           "confirm_password": "Password1234567",
                           "csrf_token": change_csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("a breached password is refused at set time",
          r.status_code == 400 and "breach" in r.text.lower(), f"got {r.status_code}")
    r = browser.post("/auth/password/change",
                     data={"current_password": temp_password, "new_password": "short",
                           "confirm_password": "short", "csrf_token": change_csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("a too-short password is refused", r.status_code == 400, f"got {r.status_code}")

    print("\n  12i'''''. the change succeeds and rotates again (sample 3 of 3)")
    r = browser.post("/auth/password/change",
                     data={"current_password": temp_password, "new_password": NEW_PASSWORD,
                           "confirm_password": NEW_PASSWORD, "csrf_token": change_csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("the forced change succeeds", r.status_code == 303, f"got {r.status_code}")
    with app.session() as s:
        rows = s.scalars(select(AuthSession).where(AuthSession.user_id == user_id)).all()
        full_2 = [row.id_hash for row in rows]
        u = s.get(AuthUser, user_id)
        check("must_change_password is cleared", u.must_change_password is False)
        check("temp_expires_at is cleared", u.temp_expires_at is None)
        check("password_changed_at is stamped", u.password_changed_at is not None)
    check("the session id ROTATED AGAIN: three distinct values",
          len({pre_hashes[0], full_1[0], full_2[0]}) == 3,
          f"{pre_hashes[0][:12]} / {full_1[0][:12]} / {full_2[0][:12]}")
    check("the board is reachable now the change is done",
          browser.get("/api/graph/tree").status_code == 200)
    check("a password change does NOT revoke service tokens (the ingest keeps working)",
          bearer.get("/api/graph/tree").status_code == 200)

    print("\n  12i''''''. \"/\" branches on the PRINCIPAL, not just on being public "
          "(ADR 0035): a session")
    r = browser.get("/", headers={"Accept": "text/html"})
    check("a signed-in BROWSER GET / -> 302 /board (not the landing page, not the raw board)",
          r.status_code == 302 and r.headers.get("location") == "/board",
          f"got {r.status_code} {r.headers.get('location')}")
    check("...and that redirect carries the same private/no-cache/Vary pairing as the "
          "landing page — the two branches of \"/\" must not be conflatable by a shared cache",
          r.headers.get("cache-control") == "private, no-cache" and r.headers.get("vary") == "Cookie",
          f"cache-control={r.headers.get('cache-control')!r} vary={r.headers.get('vary')!r}")
    check("/board itself is unaffected: a signed-in browser still gets it directly, 200",
          browser.get("/board", headers={"Accept": "text/html"}).status_code == 200)
    r_bearer_root = bearer.get("/", headers={"Accept": "text/html"})
    check("a Bearer (machine) principal at / is UNCHANGED by this ADR: still served the "
          "board directly, never the landing page and never redirected",
          r_bearer_root.status_code == 200 and "tab-world" in r_bearer_root.text,
          f"status={r_bearer_root.status_code} has_tab_world={'tab-world' in r_bearer_root.text}")

    # ---- 12j. a password change kills every OTHER session ----------------- #
    print("\n  12j. changing the password invalidates every other session")
    second = _login(_client(), "probe", NEW_PASSWORD)
    with app.session() as s:
        before = s.scalar(select(func.count()).select_from(AuthSession).where(
            AuthSession.user_id == user_id))
    check("two browser profiles -> 2 live sessions", before == 2, f"got {before}")
    r = browser.get("/auth/password/change")
    r = browser.post("/auth/password/change",
                     data={"current_password": NEW_PASSWORD, "new_password": SECOND_PASSWORD,
                           "confirm_password": SECOND_PASSWORD, "csrf_token": _csrf_from(r.text)},
                     headers={"Origin": TEST_ORIGIN})
    check("the second change succeeds", r.status_code == 303, f"got {r.status_code}")
    with app.session() as s:
        after = s.scalar(select(func.count()).select_from(AuthSession).where(
            AuthSession.user_id == user_id))
    check("count(auth_session) for that user: 2 before, 1 after", after == 1, f"got {after}")
    # "/" itself is public now (ADR 0035) and would legitimately answer 200
    # (the landing page) to a signed-out caller — that is the whole point of
    # the change, not a gate failure. "/board" is what stays gated, so it is
    # what proves a stale session cookie is actually treated as signed-out.
    r = second.get("/board", headers={"Accept": "text/html"})
    check("the OTHER profile is signed out and 302s to /auth/login",
          r.status_code == 302 and r.headers.get("location", "").startswith("/auth/login"),
          f"got {r.status_code} {r.headers.get('location')}")
    r = second.get("/", headers={"Accept": "text/html"})
    check("...and \"/\" itself now answers 200 (the public landing page) rather than "
          "redirecting, for that same signed-out cookie",
          r.status_code == 200, f"got {r.status_code}")

    # ---- 12k. logout DELETES the row -------------------------------------- #
    print("\n  12k. logout")
    me = browser.get("/api/me").json()
    r = browser.post("/auth/logout", data={"csrf_token": "wrong"},
                     headers={"Origin": TEST_ORIGIN})
    check("logout without a synchronizer token -> 403", r.status_code == 403,
          f"got {r.status_code}")
    r = browser.post("/auth/logout", data={"csrf_token": me["csrf_token"]},
                     headers={"Origin": TEST_ORIGIN})
    check("logout succeeds", r.status_code == 303, f"got {r.status_code}")
    with app.session() as s:
        check("logout DELETES the row (not just the cookie)",
              s.scalar(select(func.count()).select_from(AuthSession).where(
                  AuthSession.user_id == user_id)) == 0)

    # ---- 12l. the ContextVar TASK-979 depends on -------------------------- #
    print("\n  12l. the principal is published to a ContextVar and reset in a finally")
    source = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth.py"),
                  encoding="utf-8").read()
    check("auth.py declares a ContextVar", "ContextVar(" in source)
    dispatch = source.split("async def dispatch", 1)[1].split("\n\n\n", 1)[0]
    check("it is SET before call_next", dispatch.index("current_principal.set(")
          < dispatch.index("await call_next"))
    check("it is RESET inside a finally",
          "finally:" in dispatch and dispatch.index("finally:") < dispatch.index("current_principal.reset("))

    # A route registered AFTER add_middleware — which also proves the gate
    # covers routes nobody had written when it was installed.
    seen: dict = {}

    @app.app.get("/api/_ctxvar_probe")
    def _ctxvar_probe():           # sync handler => runs in the threadpool
        principal = auth.current_principal.get()
        seen["subject"] = principal.subject if principal else None
        seen["kind"] = principal.kind if principal else None
        return {"subject": seen["subject"]}

    check("a route added AFTER the middleware is still gated",
          anon.get("/api/_ctxvar_probe").status_code == 401)
    r = bearer.get("/api/_ctxvar_probe")
    check("the ContextVar is visible inside a synchronous handler (the threadpool "
          "worker TASK-979's Session event runs in)",
          r.status_code == 200 and seen.get("subject") == "bot-sync", str(seen))
    check("the ContextVar is reset once the request is over",
          auth.current_principal.get() is None)

    # ---- 12m. the staged-rollout flag is OFF unless configured ------------ #
    print("\n  12m. TASK-968's staged-rollout flag")
    check("ATLAS_MACHINE_GRACE_HOSTS is empty by default -> enforcement is live",
          auth.MACHINE_GRACE_HOSTS == frozenset(), str(auth.MACHINE_GRACE_HOSTS))
    # Every environment variable the gate can READ, as a string literal. The
    # docstring names ATLAS_AUTH_DISABLED in backticks to say it must never
    # exist; what matters is that it is never a value the code consults.
    env_literals = sorted(set(re.findall(r'"(ATLAS_[A-Z0-9_]+)"', source)))
    print(f"  env vars auth.py reads: {env_literals}")
    check("no ATLAS_AUTH_DISABLED-style escape hatch is read anywhere in auth.py",
          not [name for name in env_literals
               if "DISABL" in name or "BYPASS" in name or "SKIP" in name or "NOAUTH" in name],
          str(env_literals))
    check("the gate's only configurable relaxation is the staged-rollout host list",
          "ATLAS_MACHINE_GRACE_HOSTS" in env_literals)

    # Turn the flag on for four requests and prove each of its limits holds. It
    # must let a SCRIPT through and nothing else — not a browser, not a bad
    # credential, and never past the forced-change interlock.
    auth.MACHINE_GRACE_HOSTS = frozenset({"testserver"})
    try:
        check("grace ON: an unauthenticated SCRIPT call succeeds and is WARN-logged",
              anon.get("/api/graph/tree", headers={"Accept": "application/json"}).status_code == 200)
        # "/" is a poor probe for this any more — it is public now (ADR 0035)
        # and answers 200 to EVERY signed-out browser regardless of grace.
        # "/board" is still fully gated, so it is what actually proves grace
        # never lets a browser-shaped request through.
        check("grace ON: a BROWSER-shaped request is still refused",
              anon.get("/board", headers={"Accept": "text/html"}).status_code == 302)
        check("grace ON: a BAD Bearer is still 401 (it can never mask a broken token)",
              anon.get("/api/graph/tree",
                       headers={"Authorization": "Bearer atlas_svc_000000_x"}).status_code == 401)
        auth.MACHINE_GRACE_HOSTS = frozenset({"some-other-host"})
        check("grace is per-host: it does not apply on a host it was not configured for",
              anon.get("/api/graph/tree", headers={"Accept": "application/json"}).status_code == 401)
    finally:
        auth.MACHINE_GRACE_HOSTS = frozenset()
    check("grace OFF again: enforcement is live",
          anon.get("/api/graph/tree", headers={"Accept": "application/json"}).status_code == 401)

    _in_auth_section = False


def _login(client, username: str, password: str):
    """Drive the REAL login flow. No shortcut, no fixture, no injected row."""
    r = client.get("/auth/login")
    r = client.post("/auth/login",
                    data={"username": username, "password": password,
                          "csrf_token": _csrf_from(r.text)},
                    headers={"Origin": TEST_ORIGIN})
    if r.status_code != 303:
        raise AssertionError(f"login for {username!r} failed: {r.status_code}")
    return client


def verify_api() -> None:
    """Exercise /api/graph and /api/graph/tree in-process via FastAPI TestClient.

    No `with` block: we don't want TestClient to re-run app startup (which would
    reseed) — the DB is already populated by main()'s app.startup() call, so we
    query it directly.

    Since TASK-967 the read API is AUTHENTICATED, so every call here carries the
    real service token verify_auth() minted. Without it these are all 401 — which
    is the point of running verify_auth() first.
    """
    print("\n-- 7. Graph read API (FastAPI TestClient, authenticated) --")
    client = _client(bearer=AUTH_BEARER)

    # 7a. /api/graph?focus=TASK-9001&depth=2 — reaches its story (up) and epic (up).
    r = client.get("/api/graph", params={"focus": "TASK-9001", "depth": 2})
    check("/api/graph?focus=TASK-9001 returns 200", r.status_code == 200, f"got {r.status_code}")
    data = r.json()
    ids = {n["id"] for n in data["nodes"]}
    print(f"  depth=2 from TASK-9001: {len(ids)} nodes, {len(data['edges'])} edges, truncated={data['truncated']}")
    check("focus TASK-9001 present in result", "TASK-9001" in ids)
    check("upward: parent story STORY-seed-all-done reached", "STORY-seed-all-done" in ids)
    check("upward: grandparent epic EPIC-seed-platform reached", "EPIC-seed-platform" in ids)
    check("depth-2 result is not truncated", data["truncated"] is False)

    # 7b. depth bounding: depth=1 reaches the story but NOT the epic (2 hops away).
    r1 = client.get("/api/graph", params={"focus": "TASK-9001", "depth": 1})
    ids1 = {n["id"] for n in r1.json()["nodes"]}
    check("depth=1 reaches the parent story", "STORY-seed-all-done" in ids1)
    check("depth=1 does NOT reach the epic (2 hops away)", "EPIC-seed-platform" not in ids1)

    # 7c. edge-type bounding: contains-only must exclude any depends_on-only neighbour.
    r_all = client.get("/api/graph", params={"focus": "TASK-9001", "depth": 1})
    r_con = client.get("/api/graph", params={"focus": "TASK-9001", "depth": 1, "edges": "contains"})
    edge_types_con = {e["edge_type"] for e in r_con.json()["edges"]}
    check("edges=contains returns only contains edges", edge_types_con <= {"contains"},
          f"saw {edge_types_con}")
    all_types = {e["edge_type"] for e in r_all.json()["edges"]}
    check("default (no edges param) traverses >1 edge type where present",
          len(all_types) >= 1, f"saw {all_types}")

    # 7d. truncation flag: a tiny cap on a highly-connected focus must trip it.
    r_cap = client.get("/api/graph", params={"focus": "EPIC-seed-platform", "depth": 3, "cap": 3})
    dcap = r_cap.json()
    check("cap=3 bounds the node set to <= 3", len(dcap["nodes"]) <= 3, f"got {len(dcap['nodes'])}")
    check("cap=3 sets truncated=true on a large neighbourhood", dcap["truncated"] is True)

    # 7e. unknown focus -> 404.
    r404 = client.get("/api/graph", params={"focus": "TASK-does-not-exist"})
    check("unknown focus returns 404", r404.status_code == 404, f"got {r404.status_code}")

    # 7f. /api/graph/tree (contains projection) works and roots are epics.
    rt = client.get("/api/graph/tree")
    check("/api/graph/tree returns 200", rt.status_code == 200, f"got {rt.status_code}")
    roots = rt.json()["roots"]
    root_types = {r["type"] for r in roots}
    print(f"  /api/graph/tree: {len(roots)} roots, types={root_types}")
    # SCOPED (was `root_types == {"epic"}`). That held only because this script
    # used to import workitems and nothing else, so every root in the database
    # was an epic by construction. The world tree has roots of its own -- ADR
    # 0030 gave it four -- and the moment anything populates both families the
    # old assertion fails for a reason that is not a defect. What the rule
    # actually says is narrower and still worth guarding: within the WORKITEM
    # family, a node with no incoming `contains` edge is an epic.
    workitem_root_types = {r["type"] for r in roots if r["type"] in ("epic", "story", "task")}
    check("graph tree workitem roots are epics (contains has no incoming edge)",
          workitem_root_types == {"epic"},
          f"workitem root types={workitem_root_types} (all root types={root_types})")

    # 7g. legacy Component-backed /api/tree still works (not broken by v2).
    rlegacy = client.get("/api/tree")
    if V0_ON:
        check("legacy /api/tree still returns 200", rlegacy.status_code == 200, f"got {rlegacy.status_code}")
        check("legacy /api/tree still has roots", len(rlegacy.json().get("roots", [])) > 0)
    else:
        check("legacy /api/tree is 404 with ATLAS_LEGACY_V0 off (no v0 table to read)",
              rlegacy.status_code == 404, f"got {rlegacy.status_code}")

    # 7h. Derived two-axis values are present in the API payloads (TASK-854/855).
    by_id = {n["id"]: n for n in data["nodes"]}  # from the depth=2 TASK-9001 response
    t850 = by_id["TASK-9001"]
    check("/api/graph node carries attention", t850.get("attention") == "green", f"got {t850.get('attention')}")
    check("/api/graph node carries completion", t850.get("completion") == 1.0, f"got {t850.get('completion')}")
    check("/api/graph node carries attention_count", t850.get("attention_count") == 0)
    check("/api/graph node carries completion_counts breakdown",
          set(t850.get("completion_counts", {})) == {"done", "in_progress", "todo"},
          f"got {t850.get('completion_counts')}")
    story_node = by_id.get("STORY-seed-all-done", {})
    print(f"  API STORY-seed-all-done: attention={story_node.get('attention')} "
          f"completion={story_node.get('completion')} counts={story_node.get('completion_counts')} "
          f"attention_count={story_node.get('attention_count')}")

    # /api/graph/tree payload also carries the derived fields + breakdown.
    def find_in_tree(nodes, nid):
        for n in nodes:
            if n["id"] == nid:
                return n
            hit = find_in_tree(n.get("children", []), nid)
            if hit:
                return hit
        return None

    # Chosen from the payload, not written down — the old version named
    # a server-standup story and expected "green: 5 done + 1 grey", which went
    # stale when the story grew and then went WRONG when it was closed with children
    # still open. What this actually checks is that the TREE carries the derived
    # fields at all, and that the rollup rule holds there too: a story whose subtree
    # contains no red or orange must not be red or orange itself.
    def _walk(nodes):
        for n in nodes:
            yield n
            yield from _walk(n.get("children", []))

    stories = [n for n in _walk(roots) if n.get("type") == "story"]
    check("/api/graph/tree contains stories to inspect", bool(stories), f"got {len(stories)}")
    sample = next((n for n in stories if n.get("attention_count") == 0), None)
    check("/api/graph/tree carries completion_counts on a story",
          sample is not None
          and set(sample.get("completion_counts", {})) == {"done", "in_progress", "todo"},
          f"got {sample.get('completion_counts') if sample else None}")
    check("/api/graph/tree: a story with attention_count == 0 is not red or orange",
          sample is not None and sample.get("attention") not in ("red", "orange"),
          f"{sample.get('id') if sample else None} -> "
          f"{sample.get('attention') if sample else None}")
    if sample:
        print(f"  API tree {sample['id']}: attention={sample['attention']} "
              f"completion={sample['completion']:.3f} counts={sample['completion_counts']} "
              f"attention_count={sample['attention_count']}")

    # 7i. Goal-directed urgency is surfaced in the /api/graph payload (urgency layer).
    # TASK-9010 is the actionable frontier of its chain -> red.
    rb = client.get("/api/graph", params={"focus": "TASK-9010", "depth": 0})
    n840 = rb.json()["nodes"][0]
    check("/api/graph exposes state (pre-urgency colour)", n840.get("state") == "grey",
          f"got {n840.get('state')}")
    check("/api/graph exposes urgency", n840.get("urgency") == "red", f"got {n840.get('urgency')}")
    check("/api/graph exposes urgency_blocks (why it's urgent)",
          "TASK-9011" in n840.get("urgency_blocks", []), f"got {n840.get('urgency_blocks')}")
    check("/api/graph attention folds urgency in (red)", n840.get("attention") == "red")
    print(f"  API TASK-9010: state={n840.get('state')} urgency={n840.get('urgency')} "
          f"attention={n840.get('attention')} blocks={n840.get('urgency_blocks')}")

    # 7i2. `work` and `meta` tiers (TASK-1163). atlas-mcp's `atlas_tree` sets
    # these to get a minimal payload; the BOARD omits them and must see exactly
    # what it saw before. Every check is against a payload that DOES carry the
    # field being removed, or it would pass on an empty fixture.
    tier_params = {"focus": testseed.SEED.world_service, "depth": 1}
    r_full = client.get("/api/graph", params=tier_params)
    full_nodes = r_full.json()["nodes"]
    bound = [n for n in full_nodes if (n.get("work_load") or {}).get("workitems")]
    check("tier fixture: the neighbourhood has a node with a per-workitem list",
          bool(bound), f"ids={[n['id'] for n in full_nodes]}")
    check("tier fixture: the neighbourhood has a node carrying attributes",
          any(n.get("attributes") for n in full_nodes))
    r_explicit = client.get("/api/graph", params={**tier_params, "work": "items", "meta": "full"})
    check("work/meta OMITTED is byte-identical to work=items&meta=full (board default unchanged)",
          r_full.content == r_explicit.content,
          f"len {len(r_full.content)} vs {len(r_explicit.content)}")

    r_counts = client.get("/api/graph", params={**tier_params, "work": "counts"})
    counts_nodes = r_counts.json()["nodes"]
    check("work=counts: every work_load is exactly count/done/completion",
          all(set(n["work_load"]) == {"count", "done", "completion"} for n in counts_nodes),
          f"saw {[sorted(n.get('work_load', {})) for n in counts_nodes][:3]}")
    check("work=counts: urgency_blocks is gone", all("urgency_blocks" not in n for n in counts_nodes))
    by_full = {n["id"]: n for n in full_nodes}
    check("work=counts: the counts themselves are unchanged",
          all(n["work_load"]["count"] == by_full[n["id"]]["work_load"]["count"]
              and n["work_load"]["done"] == by_full[n["id"]]["work_load"]["done"]
              for n in counts_nodes))
    check("work=counts did not mutate the shared empty work_load",
          set(app._EMPTY_WORK_LOAD) >= {"workitem_ids"}, f"got {app._EMPTY_WORK_LOAD}")

    r_min = client.get("/api/graph", params={**tier_params, "work": "none", "meta": "none",
                                             "prose": "none"})
    min_body = r_min.json()
    leaked = sorted({k for n in min_body["nodes"] for k in n
                     if k in app._WORK_FIELDS or k in app._META_FIELDS})
    check("work=none&meta=none: no work or meta field on any node", not leaked, f"leaked {leaked}")
    check("work=none&meta=none: id/type/name/attention/intent_count/status remain",
          all({"id", "type", "name", "attention", "intent_count", "status"} <= set(n)
              for n in min_body["nodes"]))
    check("tiers select FIELDS, never rows: same node ids and edges as the full payload",
          {n["id"] for n in min_body["nodes"]} == set(by_full)
          and min_body["edges"] == r_full.json()["edges"])
    print(f"  tiers on {tier_params['focus']}: full {len(r_full.content)} B, "
          f"counts {len(r_counts.content)} B, minimal {len(r_min.content)} B")

    for param in ("work", "meta"):
        r_bad = client.get("/api/graph", params={**tier_params, param: "everything"})
        check(f"{param}=<unknown> is a 400 naming the parameter, not a silent full payload",
              r_bad.status_code == 400 and r_bad.json().get("field") == param,
              f"got {r_bad.status_code} {r_bad.text[:120]}")

    # 7i3. `strict` and `max_bytes` (TASK-1163): refuse instead of cutting off.
    # They moved here from atlas-mcp so the rule has one home and CI covers it.
    # The same capped walk 7d proves truncates is the fixture, so these cannot
    # pass on a neighbourhood too small to trip anything.
    capped = {"focus": "EPIC-seed-platform", "depth": 3, "cap": 3}
    check("strict fixture: the capped walk really truncates without strict",
          client.get("/api/graph", params=capped).json().get("truncated") is True)
    r_strict = client.get("/api/graph", params={**capped, "strict": 1})
    check("strict=1 on a truncated walk is a 413 too_large, not a cut-off 200",
          r_strict.status_code == 413 and r_strict.json().get("error") == "too_large",
          f"got {r_strict.status_code} {r_strict.text[:120]}")
    check("the 413 carries no nodes and names what to narrow",
          "nodes" not in r_strict.json()
          and all(w in r_strict.json().get("detail", "") for w in ("depth", "edges")))
    r_fits = client.get("/api/graph", params={**tier_params, "strict": 1})
    check("strict=1 on a walk that fits is the same 200 body as without it",
          r_fits.status_code == 200 and r_fits.content == r_full.content,
          f"got {r_fits.status_code}")

    full_len = len(r_full.content)
    r_over = client.get("/api/graph", params={**tier_params, "max_bytes": full_len - 1})
    check("max_bytes one under the body size is a 413 too_large reporting the size",
          r_over.status_code == 413 and r_over.json().get("bytes") == full_len,
          f"got {r_over.status_code} {r_over.text[:160]}")
    r_at = client.get("/api/graph", params={**tier_params, "max_bytes": full_len})
    check("max_bytes exactly the body size returns the body unchanged",
          r_at.status_code == 200 and r_at.content == r_full.content, f"got {r_at.status_code}")
    r_zero = client.get("/api/graph", params={**tier_params, "max_bytes": 0})
    check("max_bytes=0 is a 400 naming the field",
          r_zero.status_code == 400 and r_zero.json().get("field") == "max_bytes",
          f"got {r_zero.status_code}")

    # 7j. /api/vocabulary (TASK-1081) — the write API's legal vocabulary,
    # DERIVED from the same constants/tables the write routes enforce
    # against, never a second hand-typed copy. Every check below compares
    # the response to the actual source, not to a value written down here —
    # a check that just re-asserts a literal would pass just as happily
    # against a hand-transcribed (and driftable) second list.
    import node_types
    from attention import SETTABLE_STATUSES as _SETTABLE_STATUSES

    rv = client.get("/api/vocabulary")
    check("/api/vocabulary returns 200", rv.status_code == 200, f"got {rv.status_code}")
    vocab = rv.json()

    seeded_type_ids = set(node_types.NODE_TYPE_SEEDS) | set(node_types.WORLD_NODE_TYPE_SEEDS)
    check("/api/vocabulary node_types is EXACTLY the seeded node_type registry "
          "(the same ids POST /api/node's `type` FK and validate_node's schema "
          "check enforce)",
          set(vocab.get("node_types", {})) == seeded_type_ids,
          f"response has {sorted(vocab.get('node_types', {}))}, "
          f"seeds have {sorted(seeded_type_ids)}")

    with app.session() as s:
        db_task_schema = s.get(NodeType, "task").attributes_schema
        db_project_schema = s.get(NodeType, "project").attributes_schema
    check("/api/vocabulary node_types['task'].attributes_schema is READ from the "
          "live node_type table, not hand-copied",
          vocab["node_types"]["task"]["attributes_schema"] == db_task_schema,
          f"got {vocab['node_types']['task']['attributes_schema']}, db has {db_task_schema}")
    check("...and node_types['project'].attributes_schema matches ITS OWN row too "
          "(not task's, by coincidence)",
          vocab["node_types"]["project"]["attributes_schema"] == db_project_schema,
          f"got {vocab['node_types']['project']['attributes_schema']}")
    check("/api/vocabulary node_types['task'].family == 'workitem' (read off the table)",
          vocab["node_types"]["task"]["family"] == "workitem",
          f"got {vocab['node_types']['task']['family']}")
    check("/api/vocabulary node_types['project'].family == 'object' (read off the table)",
          vocab["node_types"]["project"]["family"] == "object",
          f"got {vocab['node_types']['project']['family']}")

    check("/api/vocabulary edge_types.rules matches app.EDGE_FAMILY_RULES exactly "
          "(the same dict POST /api/edge looks edge_type up in)",
          vocab["edge_types"]["rules"] == {
              et: {"src_family": sf, "dst_family": df}
              for et, (sf, df) in app.EDGE_FAMILY_RULES.items()
          },
          str(vocab["edge_types"]["rules"]))
    check("/api/vocabulary names the work-hierarchy edge via the real "
          "WORK_HIERARCHY_EDGE_TYPE constant, not a hardcoded 'contains' string",
          app.WORK_HIERARCHY_EDGE_TYPE in vocab["edge_types"]["notes"],
          str(vocab["edge_types"]["notes"]))

    check("/api/vocabulary workitem_statuses == attention.SETTABLE_STATUSES "
          "(the same tuple POST/PATCH /api/workitem and .../status validate against)",
          vocab["workitem_statuses"] == list(_SETTABLE_STATUSES),
          f"got {vocab['workitem_statuses']}")
    check("/api/vocabulary workitem_types == app.WORKITEM_TYPES "
          "(the same tuple create_workitem/patch_workitem validate against)",
          vocab["workitem_types"] == list(app.WORKITEM_TYPES),
          f"got {vocab['workitem_types']}")
    # TASK-1389: one source. The ladder and the type list are both read off
    # node_types.NODE_TYPE_SEEDS, and the parent-rank rule off the seeds' `rank`.
    check("/api/vocabulary workitem_ladder == ['epic', 'story', 'task'] "
          "(the hierarchy rungs, top first, from the seeds' `rank`)",
          vocab.get("workitem_ladder") == ["epic", "story", "task"],
          f"got {vocab.get('workitem_ladder')}")
    check("WORKITEM_TYPES, BACKLOG_TYPES and WORK_HIERARCHY_RANK are derived from "
          "node_types.NODE_TYPE_SEEDS (TASK-1389)",
          app.WORKITEM_TYPES == node_types.WORKITEM_TYPES == node_types.BACKLOG_TYPES
          and app.WORK_HIERARCHY_RANK == {t: node_types.NODE_TYPE_SEEDS[t]["rank"]
                                          for t in node_types.WORKITEM_LADDER}
          and app.WORK_HIERARCHY_RANK == {"epic": 0, "story": 1, "task": 2},
          f"types={app.WORKITEM_TYPES} rank={app.WORK_HIERARCHY_RANK}")
    check("/api/vocabulary intent_kinds == node_types.INTENT_KINDS "
          "(the same tuple create_intent validates against)",
          vocab["intent_kinds"] == list(node_types.INTENT_KINDS),
          f"got {vocab['intent_kinds']}")

    # Perturb the derivation: change nothing about the endpoint, just prove the
    # comparison above can actually FAIL. If node_types.py grew an extra seed
    # (or /api/vocabulary silently dropped one), the equality checks above
    # would go red — demonstrated here by asserting a value that must NOT be
    # in the corpus is also not echoed back.
    check("a made-up type is NOT echoed back by /api/vocabulary "
          "(the check above isn't vacuously true)",
          "bogus-node-type-1081" not in vocab["node_types"])
    check("a made-up status is NOT echoed back by /api/vocabulary",
          "bogus-status-1081" not in vocab["workitem_statuses"])


WRITE_API_PASSWORD = "kanelbulle fjaril 77 stormvind"


def verify_graph_write() -> None:
    """TASK-1017: POST/PATCH/DELETE /api/node, POST/DELETE /api/edge.

    Same discipline as verify_auth() above: every route is proven refused
    UNAUTHENTICATED, and refused for a cookie session with NO synchronizer
    token, before any success path is exercised — a 200/201 later can't be
    hiding a gate that was never wired. Uses its own login (not the "probe"
    user from verify_auth(), whose password is rotated and session logged out
    by the end of that section) so this section stands alone.

    `bearer` is minted HERE with `write,admin` — NOT `AUTH_BEARER` from
    section 12f, which is deliberately narrow (`ingest`, unused-but-kept
    per TASK-1086) and would now be REFUSED on every mutating call below
    since TASK-1086 wired scope enforcement into `auth.guard_write`. Section
    20 (below `verify_boot_is_a_read`) is what proves the refusal direction;
    this section only needs the success path to keep meaning what it always
    meant.
    """
    print("\n-- 14. Graph write API (TASK-1017: POST/PATCH/DELETE node + edge) --")

    with app.session() as s:
        s.add(AuthUser(
            id="00000000-0000-4000-8000-0000000wapi1"[:36], idp="local",
            subject="write-api-tester", name="Write API Tester", role="member",
            is_service=False, password_hash=passwords.hash_password(WRITE_API_PASSWORD),
            hash_scheme=passwords.HASH_SCHEME, must_change_password=False,
        ))
        bot = AuthUser(id="00000000-0000-4000-8000-00000000wapb", idp="local",
                       subject="bot-wapi-writer", is_service=True, label="wapi-writer",
                       must_change_password=False, role="member")
        s.add(bot)
        s.flush()
        raw_write_admin, _ = auth.mint_service_token(
            s, owner=bot, scopes=["write", "admin"], label="wapi-writer")
        s.commit()
    browser = _login(_client(), "write-api-tester", WRITE_API_PASSWORD)
    csrf = browser.get("/api/me").json()["csrf_token"]
    bearer = _client(bearer=raw_write_admin)
    anon = _client()

    # The object/bridge node_type rows (needed by the family-matching edge
    # test below, 14g) are seeded unconditionally by app.startup() now
    # (TASK-1058: node_types.ensure_world_node_types, called every boot,
    # registry seeding rather than data ingest) — no manual seeding needed
    # here any more.

    # ---- 14a. every write route refuses an unauthenticated caller ---------
    print("\n  14a. every write route refuses an unauthenticated caller")
    unauth_calls = [
        ("POST", "/api/node", {"id": "TASK-WAPI-ANON", "type": "task", "name": "x"}),
        ("PATCH", "/api/node/TASK-WAPI-ANON", {"name": "y"}),
        ("DELETE", "/api/node/TASK-WAPI-ANON", None),
        ("POST", "/api/edge", {"src": "a", "dst": "b", "edge_type": "depends_on"}),
        ("DELETE", "/api/edge/1", None),
    ]
    for method, path, body in unauth_calls:
        r = anon.request(method, path, json=body)
        check(f"401 unauthenticated: {method} {path}", r.status_code == 401, f"got {r.status_code}")

    # ---- 14b. a cookie session WITHOUT the synchronizer token is refused --
    print("\n  14b. cookie session without a csrf_token is refused (nothing is created)")
    r = browser.post("/api/node", json={"id": "TASK-WAPI-NOCSRF", "type": "task", "name": "x"},
                     headers={"Origin": TEST_ORIGIN})
    check("POST /api/node, valid session, NO csrf_token -> 403", r.status_code == 403,
          f"got {r.status_code}")
    with app.session() as s:
        check("  ...and nothing was created", s.get(Node, "TASK-WAPI-NOCSRF") is None)
    r = browser.post("/api/node",
                     json={"id": "TASK-WAPI-NOCSRF", "type": "task", "name": "x",
                           "csrf_token": "not-the-real-token"},
                     headers={"Origin": TEST_ORIGIN})
    check("POST /api/node with a WRONG csrf_token -> 403", r.status_code == 403,
          f"got {r.status_code}")

    # ---- 14c. a cookie session WITH the synchronizer token succeeds -------
    print("\n  14c. cookie session WITH the synchronizer token succeeds")
    r = browser.post("/api/node",
                     json={"id": "TASK-WAPI-1", "type": "task", "name": "Write API task",
                           "csrf_token": csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("POST /api/node with a real synchronizer token -> 201", r.status_code == 201,
          f"got {r.status_code} {r.text[:200]}")
    body = r.json()
    check("created node carries the requested id/type/name",
          body.get("id") == "TASK-WAPI-1" and body.get("type") == "task"
          and body.get("name") == "Write API task", str(body)[:200])
    # `durable` (TASK-1043) is GONE from the response (TASK-1058) — it
    # answered "will this row survive the next boot ingest?", which has no
    # referent now that there is no boot ingest. See app.py's history for
    # `_node_is_durable` if the shape is ever needed again.
    check("the response no longer carries `durable` (TASK-1058)",
          "durable" not in body, str(body)[:200])
    with app.session() as s:
        n = s.get(Node, "TASK-WAPI-1")
        check("node exists in the DB", n is not None)
        check("origin='atlas' (TASK-1016 contract), NOT 'git'",
              n is not None and n.origin == "atlas", f"got {n.origin if n else None!r}")
    r = browser.post("/api/node",
                     json={"id": "TASK-WAPI-1", "type": "task", "name": "dup", "csrf_token": csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("creating the SAME id twice -> 409, not a raw IntegrityError",
          r.status_code == 409, f"got {r.status_code}")

    # ---- 14d. a Bearer principal needs no csrf_token at all ---------------
    print("\n  14d. a Bearer principal is accepted WITHOUT a csrf_token")
    r = bearer.post("/api/node", json={"id": "TASK-WAPI-BEARER", "type": "task", "name": "bearer-made"})
    check("POST /api/node with a Bearer token and NO csrf_token -> 201", r.status_code == 201,
          f"got {r.status_code} {r.text[:200]}")
    with app.session() as s:
        n = s.get(Node, "TASK-WAPI-BEARER")
        check("Bearer-created node also carries origin='atlas'",
              n is not None and n.origin == "atlas", f"got {n.origin if n else None!r}")

    # ---- 14e. PATCH /api/node/{id}: same guard, updates fields ------------
    print("\n  14e. PATCH /api/node/{id}")
    r = browser.patch("/api/node/TASK-WAPI-1", json={"name": "Renamed", "csrf_token": "wrong"},
                      headers={"Origin": TEST_ORIGIN})
    check("PATCH without a valid csrf_token -> 403", r.status_code == 403, f"got {r.status_code}")
    r = browser.patch("/api/node/TASK-WAPI-1", json={"name": "Renamed", "csrf_token": csrf},
                      headers={"Origin": TEST_ORIGIN})
    check("PATCH with a valid csrf_token -> 200", r.status_code == 200,
          f"got {r.status_code} {r.text[:200]}")
    check("PATCH applied the new name", r.json().get("name") == "Renamed", str(r.json())[:200])
    check("the PATCH response no longer carries `durable` either (TASK-1058)",
          "durable" not in r.json(), str(r.json())[:200])
    check("TASK-1043: NOT reported as detached (it was never git-origin to begin with)",
          "detached_from_git" not in r.json(), str(r.json())[:200])
    r = browser.patch("/api/node/TASK-DOES-NOT-EXIST", json={"name": "x", "csrf_token": csrf},
                      headers={"Origin": TEST_ORIGIN})
    check("PATCH on an unknown node -> 404", r.status_code == 404, f"got {r.status_code}")

    # ---- 14f. invalid attributes -> 400, and the body NAMES the field -----
    print("\n  14f. invalid attributes -> 400, and the body NAMES the offending field")
    r = browser.post("/api/node",
                     json={"id": "TASK-WAPI-BADATTR", "type": "task", "name": "bad",
                           "attributes": {"status": 123}, "csrf_token": csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("attributes.status=123 (schema wants string|null) -> 400, not 500",
          r.status_code == 400, f"got {r.status_code} {r.text[:200]}")
    err = r.json()
    print(f"  400 body: {err}")
    check("the 400 body NAMES the offending field: field == 'status'",
          err.get("field") == "status", str(err))
    check("the 400 body's detail is jsonschema's own message (not a generic string)",
          "not of type" in (err.get("detail") or ""), str(err))
    with app.session() as s:
        check("the invalid node was NOT created", s.get(Node, "TASK-WAPI-BADATTR") is None)

    # ---- 14g. an edge whose type contradicts the endpoints' families -------
    print("\n  14g. an edge whose type contradicts the endpoints' families is refused")
    r = browser.post("/api/node",
                     json={"id": "TASK-WAPI-2", "type": "task", "name": "second write-api task",
                           "csrf_token": csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("second workitem node created", r.status_code == 201, f"got {r.status_code}")
    r = browser.post("/api/edge",
                     json={"src": "TASK-WAPI-1", "dst": "TASK-WAPI-2", "edge_type": "contains",
                           "csrf_token": csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("workitem -> workitem 'contains' (world-only) is refused",
          r.status_code == 409 and r.json().get("error") == "family_mismatch",
          f"got {r.status_code} {r.text[:200]}")
    with app.session() as s:
        check("...and no edge was created",
              s.scalar(select(Edge).where(Edge.src == "TASK-WAPI-1", Edge.dst == "TASK-WAPI-2",
                                          Edge.edge_type == "contains")) is None)

    r = browser.post("/api/node",
                     json={"id": "service-wapi-a", "type": "service", "name": "wapi svc a",
                           "csrf_token": csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("object-family node A created", r.status_code == 201, f"got {r.status_code} {r.text[:200]}")
    r = browser.post("/api/node",
                     json={"id": "service-wapi-b", "type": "service", "name": "wapi svc b",
                           "csrf_token": csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("object-family node B created", r.status_code == 201, f"got {r.status_code} {r.text[:200]}")
    r = browser.post("/api/edge",
                     json={"src": "service-wapi-a", "dst": "service-wapi-b", "edge_type": "contains",
                           "csrf_token": csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("object -> object 'contains' is ACCEPTED (the world side the check enforces)",
          r.status_code == 201, f"got {r.status_code} {r.text[:200]}")
    contains_edge_id = r.json().get("id")
    with app.session() as s:
        e = s.get(Edge, contains_edge_id) if contains_edge_id is not None else None
        check("the accepted edge exists with origin='atlas'",
              e is not None and e.origin == "atlas", f"got {e.origin if e else None!r}")

    r = bearer.post("/api/edge", json={"src": "service-wapi-a", "dst": "TASK-DOES-NOT-EXIST",
                                       "edge_type": "depends_on"})
    check("an edge naming an unknown endpoint -> 404", r.status_code == 404, f"got {r.status_code}")

    # ---- 14h. DELETE /api/node/{id}: refuses with edges unless cascade=1 --
    print("\n  14h. DELETE refuses a node with edges unless ?cascade=1")
    r = browser.request("DELETE", "/api/node/service-wapi-a", json={"csrf_token": "wrong"},
                        headers={"Origin": TEST_ORIGIN})
    check("DELETE without a valid csrf_token -> 403", r.status_code == 403, f"got {r.status_code}")
    r = bearer.request("DELETE", "/api/node/service-wapi-a")
    check("DELETE a node that still has an edge, no ?cascade=1 -> 409",
          r.status_code == 409 and r.json().get("error") == "node_has_edges",
          f"got {r.status_code} {r.text[:200]}")
    with app.session() as s:
        check("  ...node A still exists (refusal did not partially apply)",
              s.get(Node, "service-wapi-a") is not None)
        check("  ...the edge still exists too",
              s.get(Edge, contains_edge_id) is not None if contains_edge_id is not None else False)
    r = bearer.request("DELETE", "/api/node/service-wapi-a", params={"cascade": 1})
    check("DELETE with ?cascade=1 succeeds", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")
    with app.session() as s:
        check("node A is gone", s.get(Node, "service-wapi-a") is None)
        check("its edge is gone too", s.get(Edge, contains_edge_id) is None
              if contains_edge_id is not None else True)
        check("node B (the OTHER end of the edge) still exists — cascade deletes edges, not neighbours",
              s.get(Node, "service-wapi-b") is not None)

    # ---- 14i. DELETE /api/edge/{id}: same guard, plain and simple ---------
    print("\n  14i. DELETE /api/edge/{id}")
    r = browser.post("/api/edge",
                     json={"src": "TASK-WAPI-1", "dst": "TASK-WAPI-BEARER", "edge_type": "depends_on",
                           "csrf_token": csrf},
                     headers={"Origin": TEST_ORIGIN})
    check("a depends_on edge between two workitems is accepted (no family rule for it)",
          r.status_code == 201, f"got {r.status_code} {r.text[:200]}")
    depends_edge_id = r.json().get("id")
    r = anon.request("DELETE", f"/api/edge/{depends_edge_id}")
    check("DELETE /api/edge unauthenticated -> 401", r.status_code == 401, f"got {r.status_code}")
    r = browser.request("DELETE", f"/api/edge/{depends_edge_id}", json={"csrf_token": "wrong"},
                        headers={"Origin": TEST_ORIGIN})
    check("DELETE /api/edge without a valid csrf_token -> 403", r.status_code == 403,
          f"got {r.status_code}")
    r = browser.request("DELETE", f"/api/edge/{depends_edge_id}", json={"csrf_token": csrf},
                        headers={"Origin": TEST_ORIGIN})
    check("DELETE /api/edge with a valid csrf_token -> 200", r.status_code == 200,
          f"got {r.status_code} {r.text[:200]}")
    with app.session() as s:
        check("edge is gone", s.get(Edge, depends_edge_id) is None)
    r = bearer.request("DELETE", f"/api/edge/{depends_edge_id}")
    check("deleting it again -> 404 (not a 500 on a re-delete)", r.status_code == 404,
          f"got {r.status_code}")
    r = bearer.request("DELETE", "/api/node/TASK-DOES-NOT-EXIST")
    check("DELETE on an unknown node -> 404", r.status_code == 404, f"got {r.status_code}")

    # ---- 14j. a value ABSENT from /api/vocabulary is rejected by the write --
    # -- API, not merely missing from a listing (TASK-1081's other half). ----
    print("\n  14j. a type/status /api/vocabulary does not list is refused by the write API")
    vocab = bearer.get("/api/vocabulary").json()

    bad_type = "bogus-node-type-1081"
    check(f"{bad_type!r} really is absent from /api/vocabulary",
          bad_type not in vocab["node_types"])
    r = bearer.post("/api/node", json={
        "id": "TASK-WAPI-BADTYPE", "type": bad_type, "name": "should be refused",
    })
    check("POST /api/node with a type ABSENT from /api/vocabulary -> 400, not 201",
          r.status_code == 400, f"got {r.status_code} {r.text[:200]}")
    with app.session() as s:
        check("  ...and nothing was created", s.get(Node, "TASK-WAPI-BADTYPE") is None)

    bad_status = "bogus-status-1081"
    check(f"{bad_status!r} really is absent from /api/vocabulary",
          bad_status not in vocab["workitem_statuses"])
    r = bearer.post("/api/node/TASK-WAPI-1/status", json={"status": bad_status})
    check("POST .../status with a status ABSENT from /api/vocabulary -> 400, not 200",
          r.status_code == 400, f"got {r.status_code} {r.text[:200]}")
    with app.session() as s:
        check("  ...and the node's status did not change to it",
              s.get(Node, "TASK-WAPI-1").attributes.get("status") != bad_status)

    bad_kind = "bogus-intent-kind-1081"
    check(f"{bad_kind!r} really is absent from /api/vocabulary",
          bad_kind not in vocab["intent_kinds"])
    r = bearer.post("/api/node/TASK-WAPI-1/intent", json={
        "kind": bad_kind, "slug": "vocab-check-1081", "title": "x", "statement": "x",
    })
    check("POST .../intent with a kind ABSENT from /api/vocabulary -> 400, not 201",
          r.status_code == 400, f"got {r.status_code} {r.text[:200]}")


def verify_write_scope_enforcement() -> None:
    """TASK-1086/1087 — `auth.WRITE_SCOPE_ROUTES` is enforced, in BOTH
    directions. A check that cannot fail proves nothing (TESTING.md), so this
    proves three separate claims, not one:

      1. every route in the map REFUSES a `read`-only Bearer with 403,
         naming the missing scope in the response body;
      2. that SAME `read`-only Bearer still gets 200 on ordinary reads — the
         refusal above is a SCOPE check, not a broken credential;
      3. a CORRECTLY scoped Bearer (`write` for the write tier, `write,admin`
         for the admin tier) is NOT refused, and actually completes the
         write — proving direction (1) is a scope check and not a route that
         is simply broken for every caller.

    Runs its own fixtures rather than reusing `verify_graph_write`'s (which
    are already deleted by the end of that section) — every id below is
    `*-SCOPE-*`/`scope-*`, disjoint from every other section's ids.
    """
    print("\n-- 20. write-scope enforcement (TASK-1086/1087): every mutating "
          "route needs its mapped scope, both directions --")

    with app.session() as s:
        ro_bot = AuthUser(id="00000000-0000-4000-8000-00000scoper0", idp="local",
                          subject="bot-scope-reader", is_service=True,
                          label="scope-reader", must_change_password=False, role="member")
        rw_bot = AuthUser(id="00000000-0000-4000-8000-00000scoper1", idp="local",
                          subject="bot-scope-writer", is_service=True,
                          label="scope-writer", must_change_password=False, role="member")
        s.add_all([ro_bot, rw_bot])
        s.flush()
        raw_read, _ = auth.mint_service_token(s, owner=ro_bot, scopes=["read"], label="scope-reader")
        raw_write, _ = auth.mint_service_token(
            s, owner=rw_bot, scopes=["write", "admin"], label="scope-writer")
        s.commit()
    read_only = _client(bearer=raw_read)
    write_admin = _client(bearer=raw_write)

    # ---- 20a. reads still work for the read-only token --------------------
    print("\n  20a. the read-only token is NOT broken — ordinary reads still 200")
    r = read_only.get("/api/graph/tree")
    check("GET /api/graph/tree with a read-only Bearer -> 200", r.status_code == 200,
          f"got {r.status_code}")
    r = read_only.get("/api/me")
    check("GET /api/me with a read-only Bearer -> 200", r.status_code == 200,
          f"got {r.status_code}")

    # ---- 20b. every mutating route refuses the read-only token ------------
    print("\n  20b. every route in auth.WRITE_SCOPE_ROUTES refuses a read-only Bearer")

    def _fill(template: str) -> str:
        # Placeholder ids only — `auth._check_write_scope` runs BEFORE any
        # DB lookup in every one of these handlers (guard_write is their
        # first statement), so the id need not exist for a 403 to prove the
        # scope check ran first. `verify_graph_write` 14a proved the id
        # DOES get looked up once scope passes (its unauthenticated-caller
        # table exercises 404s the normal way).
        return re.sub(r"\{[^}]+\}", "TASK-SCOPE-DOES-NOT-EXIST", template)

    # Minimal bodies that satisfy each route's Pydantic model so a 422 (body
    # rejected before the handler even runs) cannot masquerade as the 403
    # this section is actually proving.
    _BODIES: dict[tuple[str, str], dict] = {
        ("POST", "/api/node/{node_id}/status"): {"status": "pending"},
        ("POST", "/api/node"): {"id": "TASK-SCOPE-NEVER", "type": "task", "name": "x"},
        ("PATCH", "/api/node/{node_id}"): {"name": "x"},
        ("DELETE", "/api/node/{node_id}"): {},
        ("POST", "/api/edge"): {"src": "a", "dst": "b", "edge_type": "depends_on"},
        ("DELETE", "/api/edge/{edge_id}"): {},
        ("POST", "/api/workitem"): {"type": "task", "name": "x"},
        ("PATCH", "/api/workitem/{workitem_id}"): {},
        # STORY-atlas-acceptance-human-approved; the rest of their gates are in
        # verify_acceptance_approval.py.
        ("POST", "/api/workitem/{workitem_id}/acceptance-proposal"): {"acceptance": ["x"]},
        ("POST", "/api/workitem/{workitem_id}/acceptance-proposal/{proposal_id}/approve"): {},
        ("POST", "/api/workitem/{workitem_id}/acceptance-proposal/{proposal_id}/reject"): {},
        ("PUT", "/api/node/{node_id}/doc"): {"path": "docs/nodes/x/y.md", "content": "x"},
        ("DELETE", "/api/node/{node_id}/doc"): {},
        ("POST", "/api/node/{node_id}/intent"): {
            "slug": "scope-slug", "kind": "principle", "title": "X", "statement": "Y"},
        ("PATCH", "/api/intent/{intent_id}"): {"title": "X"},
        ("DELETE", "/api/intent/{intent_id}"): {},
        # Comments on a workitem (routers/comments.py); the rest of their gates are in
        # verify_workitem_comments.py.
        ("POST", "/api/workitem/{workitem_id}/comments"): {"body": "x"},
        ("PATCH", "/api/comment/{comment_id}"): {"body": "x"},
        ("DELETE", "/api/comment/{comment_id}"): {},
        # The ADR registry's upsert (ADR 0045); its own gates are in verify_adr_records.py §F.
        ("PUT", "/api/adrs/{adr_id}"): {"project": "x", "title": "x"},
        # The setup guide (STORY-atlas-setup-guide-adrs): `admin` here; owner-only and, for
        # accept, session-only are proven in verify_setup_guide.py.
        ("POST", "/api/setup/answers"): {"topic": "x", "answers": {}},
        ("POST", "/api/setup/adrs/{adr_id}/accept"): {},
        # Access administration (STORY-atlas-access-admin). It costs `admin`,
        # which is what section 20b checks here; that it is ALSO owner-only is
        # a separate refusal, proven in verify_node_access.py section E.
        ("PUT", "/api/access/node/{node_id}"): {"visibility": "shared"},
        # Groups and project role bindings (STORY-oss-11): `admin` here; owner-only
        # and session-only are proven in verify_project_roles.py.
        ("POST", "/api/access/groups"): {"name": "scope-group"},
        ("PUT", "/api/access/groups/{group_id}/members"): {"members": []},
        ("DELETE", "/api/access/groups/{group_id}"): {},
        ("PUT", "/api/access/project/{project_id}/bindings"): {"bindings": []},
        # Self-service credentials (TASK-1115). It costs `admin` on the scope
        # axis, which is what this section proves. That a Bearer principal is
        # refused REGARDLESS of scope — even an admin-scoped one — is a separate
        # and stronger refusal, proven in verify_agent_identity.py section H.
        ("POST", "/api/me/agent-credential"): {
            "purpose": "scope-probe", "scopes": ["read"], "expires_days": 30,
            "password": "not-checked-scope-refuses-first"},
        ("POST", "/api/me/agent/{agent}/credential"): {
            "purpose": "scope-probe", "scopes": ["read"], "expires_days": 30,
            "password": "not-checked-scope-refuses-first"},
        ("POST", "/api/me/agent-credential/{token_id}/revoke"): {},
        # GitHub repo access: `admin` on the scope axis here; owner-only and
        # session-only are proven in verify_github_link.py.
        ("PUT", "/api/access/github/{user_id}"): {"repo_access": True},
        ("POST", "/api/access/github/{user_id}/refresh"): {},
        ("POST", "/api/me/github/unlink"): {},
        # A personal password for an SSO person (TASK-1442): `admin` on the scope axis
        # here; session-only and the step-up are proven in verify_sso_password_gate.py.
        ("POST", "/api/me/password"): {"new_password": "not-checked",
                                       "confirm_password": "not-checked"},
        # Crunch week: `admin` on the scope axis here; session-only, owner-only and the
        # step-up are proven in verify_crunch.py.
        ("PUT", "/api/crunch"): {"config": {}, "password": "not-checked"},
        ("POST", "/api/crunch/end"): {"password": "not-checked"},
    }
    if app._RETRIEVAL_ON:  # mapped only with the add-on on (main.py); verify_retrieval.py §F runs this so
        _BODIES[("POST", "/api/retrieval/reindex")] = {}
    if app._CONFIG_WRITES_ON:  # mapped only with ATLAS_CONFIG_WRITES on; verify_config_writes.py runs this so
        _BODIES[("PUT", "/api/config/{name}")] = {"value": "1", "password": "not-checked"}
    check("the fixture table covers every route auth.WRITE_SCOPE_ROUTES maps "
          "(a route added to the map with no body fixture here would be silently "
          "skipped below, which defeats the whole section)",
          set(_BODIES) == set(auth.WRITE_SCOPE_ROUTES), str(set(auth.WRITE_SCOPE_ROUTES) ^ set(_BODIES)))

    edge_id_path = "/api/edge/{edge_id}"
    for (method, template), needed_scope in sorted(auth.WRITE_SCOPE_ROUTES.items()):
        path = "/api/intent/999999" if template == "/api/intent/{intent_id}" else _fill(template)
        if template == edge_id_path:
            path = "/api/edge/999999"
        if template == "/api/comment/{comment_id}":
            path = "/api/comment/999999"
        body = _BODIES[(method, template)]
        # ALWAYS send the JSON body, even `{}` — `delete_intent`'s `body:
        # IntentDelete` parameter carries no default (unlike NodeDelete/
        # EdgeDelete on the other DELETE routes), so omitting the body
        # entirely would 422 there before the scope check ever runs, which
        # would masquerade as a false negative for this section's own claim.
        kwargs = {"json": body}
        if template.endswith("/doc"):
            kwargs.setdefault("params", {"path": "docs/nodes/x/y.md"})
        r = read_only.request(method, path, **kwargs)
        check(f"{method} {template} refuses a read-only Bearer -> 403 naming "
              f"'{needed_scope}'",
              r.status_code == 403 and needed_scope in r.json().get("detail", ""),
              f"got {r.status_code} {r.text[:200]}")

    # ---- 20c. a correctly-scoped token completes the SAME routes, chained -#
    print("\n  20c. a correctly-scoped Bearer is NOT refused — a full create/"
          "edit/delete chain actually completes")
    node_id = "TASK-SCOPE-NODE"
    r = write_admin.post("/api/node", json={"id": node_id, "type": "task", "name": "Scope node"})
    check("POST /api/node (write) -> 201", r.status_code == 201, f"got {r.status_code} {r.text[:200]}")

    r = write_admin.post(f"/api/node/{node_id}/status", json={"status": "in_progress"})
    check("POST /api/node/{id}/status (write) -> 200", r.status_code == 200,
          f"got {r.status_code} {r.text[:200]}")

    r = write_admin.patch(f"/api/node/{node_id}", json={"name": "Scope node, renamed"})
    check("PATCH /api/node/{id} (write) -> 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")

    r = write_admin.post("/api/workitem", json={"type": "task", "name": "Scope workitem"})
    check("POST /api/workitem (write) -> 201", r.status_code == 201, f"got {r.status_code} {r.text[:200]}")
    workitem_id = r.json().get("id")

    r = write_admin.patch(f"/api/workitem/{workitem_id}", json={"name": "Scope workitem, renamed"})
    check("PATCH /api/workitem/{id} (write) -> 200", r.status_code == 200,
          f"got {r.status_code} {r.text[:200]}")

    doc_path = f"docs/nodes/{node_id}/scope.md"
    r = write_admin.put(f"/api/node/{node_id}/doc", json={"path": doc_path, "content": "scope doc"})
    check("PUT /api/node/{id}/doc (write) -> 201", r.status_code == 201, f"got {r.status_code} {r.text[:200]}")

    r = write_admin.post(f"/api/node/{node_id}/intent",
                         json={"slug": "scope-real", "kind": "principle",
                               "title": "Scope intent", "statement": "It is enforced."})
    check("POST /api/node/{id}/intent (write) -> 201", r.status_code == 201, f"got {r.status_code} {r.text[:200]}")
    intent_id = r.json().get("id")

    r = write_admin.patch(f"/api/intent/{intent_id}", json={"title": "Scope intent, renamed"})
    check("PATCH /api/intent/{id} (write) -> 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")

    r = write_admin.post("/api/edge", json={"src": node_id, "dst": workitem_id, "edge_type": "depends_on"})
    check("POST /api/edge (write) -> 201", r.status_code == 201, f"got {r.status_code} {r.text[:200]}")
    edge_id = r.json().get("id")

    # Admin tier now — the same `write_admin` Bearer carries `admin` too, so
    # the tear-down proves that tier as well rather than leaving it asserted
    # only by the 403 direction above.
    r = write_admin.request("DELETE", f"/api/edge/{edge_id}")
    check("DELETE /api/edge/{id} (admin) -> 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")

    # IntentDelete carries no Pydantic default (unlike NodeDelete/EdgeDelete
    # on the sibling DELETE routes) — an explicit `json={}` is required or
    # this 422s before the handler runs at all.
    r = write_admin.request("DELETE", f"/api/intent/{intent_id}", json={})
    check("DELETE /api/intent/{id} (admin) -> 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")

    r = write_admin.request("DELETE", f"/api/node/{node_id}/doc", params={"path": doc_path})
    check("DELETE /api/node/{id}/doc (admin) -> 200", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")

    r = write_admin.request("DELETE", f"/api/node/{node_id}")
    check("DELETE /api/node/{id} (admin) -> 200 (no edges left — the depends_on "
          "edge above is already gone)", r.status_code == 200, f"got {r.status_code} {r.text[:200]}")

    r = write_admin.request("DELETE", f"/api/node/{workitem_id}")
    check("DELETE the workitem node too (admin) -> 200", r.status_code == 200,
          f"got {r.status_code} {r.text[:200]}")

    # ---- 20d. `write` alone is not `admin` — the two tiers are NOT implied -#
    print("\n  20d. a `write`-only Bearer is refused on the admin tier (no "
          "read<write<admin implication)")
    with app.session() as s:
        w_bot = AuthUser(id="00000000-0000-4000-8000-00000scoper2", idp="local",
                         subject="bot-scope-write-only", is_service=True,
                         label="scope-write-only", must_change_password=False, role="member")
        s.add(w_bot)
        s.flush()
        raw_write_only, _ = auth.mint_service_token(
            s, owner=w_bot, scopes=["write"], label="scope-write-only")
        s.commit()
    write_only = _client(bearer=raw_write_only)
    r = write_only.post("/api/node", json={"id": "TASK-SCOPE-WONLY", "type": "task", "name": "x"})
    check("write-only Bearer CAN create (write tier) -> 201", r.status_code == 201,
          f"got {r.status_code} {r.text[:200]}")
    r = write_only.request("DELETE", "/api/node/TASK-SCOPE-WONLY")
    check("...but CANNOT delete the SAME node it just created (admin tier) -> 403 "
          "naming 'admin'",
          r.status_code == 403 and "admin" in r.json().get("detail", ""),
          f"got {r.status_code} {r.text[:200]}")
    r = write_admin.request("DELETE", "/api/node/TASK-SCOPE-WONLY")
    check("...cleaned up with the admin-scoped Bearer", r.status_code == 200, f"got {r.status_code}")


def verify_boot_is_a_read() -> None:
    """TASK-1058 acceptance #5, the assertion the whole task turns on:
    starting the app against an ALREADY-POPULATED database changes NOTHING.

    Snapshots every node id with its status/body and the full edge set,
    calls the REAL `app.startup()` again (a second, real boot against the
    same database this whole script has been building up — the closest
    thing to "restart the container" available here), and asserts
    byte-identical before/after. This is the inverse of TASK-1016's old
    case ("an atlas-origin row survives the ingest") — there is no ingest
    left to survive; the correct behaviour now is that boot never touches
    `node`/`edge` at all.
    """
    print("\n-- 15. Boot is a READ: a second app.startup() against a populated "
          "DB changes nothing (TASK-1058) --")

    def _snapshot(s):
        nodes = {
            n.id: (n.attributes.get("status") if n.attributes else None, n.body)
            for n in s.scalars(select(Node)).all()
        }
        edges = {
            (e.src, e.dst, e.edge_type) for e in s.scalars(select(Edge)).all()
        }
        return nodes, edges

    with app.session() as s:
        nodes_before, edges_before = _snapshot(s)
    # THRESHOLD LOWERED (was >100 nodes). 100 was a proxy for "the markdown
    # corpus actually loaded" -- a guard against this section passing vacuously
    # against an empty database. The guard is still needed; the number was never
    # part of the claim. What section 15 asserts is that app.startup() changes
    # NOTHING about data already in the graph, and 31 fixture nodes across three
    # workitem types plus a world tree demonstrate that exactly as well as 306
    # did. `testseed.self_check()` is what now guarantees the data is really
    # there and really varied.
    check("15 setup: there is real data to protect (>20 nodes, >0 edges)",
          len(nodes_before) > 20 and len(edges_before) > 0,
          f"{len(nodes_before)} nodes, {len(edges_before)} edges")

    # ATLAS_RESEED must be OFF for this second boot — it was "1" only to get
    # this script's very first clean schema; left on, app.startup() DROPS AND
    # RECREATES the whole graph (see its own docstring: "DELIBERATELY
    # ASYMMETRIC — wipes the GRAPH, never the ACCOUNTS"), which would wipe the
    # very data this check exists to prove untouched — an ordinary restart
    # (this section's whole point) never sets ATLAS_RESEED.
    os.environ["ATLAS_RESEED"] = ""
    app.startup()  # a REAL second boot against the SAME populated database

    with app.session() as s:
        nodes_after, edges_after = _snapshot(s)

    check("boot is a read: node count is unchanged",
          len(nodes_after) == len(nodes_before),
          f"before={len(nodes_before)} after={len(nodes_after)}")
    check("boot is a read: edge count is unchanged",
          len(edges_after) == len(edges_before),
          f"before={len(edges_before)} after={len(edges_after)}")
    check("boot is a read: every node id, status and body is BYTE-IDENTICAL",
          nodes_after == nodes_before,
          f"{sum(1 for k in nodes_before if nodes_before.get(k) != nodes_after.get(k))} node(s) differ")
    check("boot is a read: the edge set is BYTE-IDENTICAL (no add, no remove, no flip)",
          edges_after == edges_before,
          f"added={edges_after - edges_before} removed={edges_before - edges_after}")


# =========================================================================== #
# 21. The bundled breach corpus reaches the image (TASK-1127)
# =========================================================================== #

def _dockerfile_copy_sources(text: str) -> list[str]:
    """Every source path a `COPY` instruction in `text` names.

    Docker's own rule: the last argument is the destination, everything before
    it (minus `--flags`) is a source, resolved against the BUILD CONTEXT — which
    for this image is the repo root, not `backend/`. Continuations are joined so
    a wrapped COPY is not read as one truncated line.
    """
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    sources: list[str] = []
    for line in joined.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("COPY "):
            continue
        # `COPY --from=<stage or image>` reads another image's filesystem, not the build
        # context, so its sources are not context paths (the backup stage's pg_dump 16,
        # OSS-26 C). Skipping it can only make "the corpus is copied" harder to satisfy.
        if any(a.lower().startswith("--from=") for a in stripped.split()[1:]):
            continue
        args = [a for a in stripped.split()[1:] if not a.startswith("--")]
        sources.extend(args[:-1])          # last arg is the destination
    return sources


def _copied_into_image(text: str, context_root: Path, target: Path) -> bool:
    """Would a build of `text` from `context_root` put `target` in the image?

    Expands each COPY source as a glob against the real context, so a directory
    source covers everything under it and `backend/*.py` covers only modules.
    Answering by GLOBBING rather than by string-matching a known-good line is
    what lets the copy be renamed, widened or merged without this going red for
    a change that is correct.
    """
    for source in _dockerfile_copy_sources(text):
        for match in context_root.glob(source.replace("\\", "/")):
            match = match.resolve()
            if match == target:
                return True
            if match.is_dir() and match in target.parents:
                return True
    return False


def verify_breach_corpus_shipped() -> None:
    """The zero-egress password check is only zero-egress if its corpus ships.

    `backend/Dockerfile` copies backend modules by glob (`backend/*.py`), and a
    glob over `*.py` covers no data directory. All three stacks set
    `ATLAS_BREACH_CHECK: bundled`, so the bundled corpus is the LIVE path rather
    than a fallback — and its absence is silent in the worst direction:
    `passwords._load_corpus` swallows the `OSError` into a log line and caches an
    EMPTY frozenset, `_check_bundled` then hits `if not exact` and returns
    `breached=False`, and the caller ADMITS the password. The check fails OPEN.

    Nothing on a workstation reproduces that — the file is in the repo, so every
    test that reads it passes — which is exactly why the claim under test here is
    about the IMAGE and not about the corpus.

    Derived, not pinned: the path comes from `passwords.BREACH_CORPUS_PATH` and
    the Dockerfile is parsed rather than string-matched, so renaming either side
    keeps the check honest. And it is perturbed at the end — the same predicate
    is re-run against this Dockerfile with the covering COPY deleted, and must
    report the corpus UNSHIPPED. A coverage test that cannot fail proves nothing,
    and this one would pass just as happily if `_copied_into_image` returned
    True unconditionally.
    """
    print("\n-- 21. The bundled breach corpus is IN the image (TASK-1127) --")

    context_root = Path(__file__).resolve().parent.parent   # the build context
    dockerfile = context_root / "backend" / "Dockerfile"
    corpus = Path(passwords.BREACH_CORPUS_PATH).resolve()

    check("21 setup: backend/Dockerfile is readable", dockerfile.is_file(), str(dockerfile))
    text = dockerfile.read_text(encoding="utf-8")

    # (a) the image would carry the file the CODE reads — not a file named here.
    check("the Dockerfile COPYs the corpus passwords.py actually opens",
          _copied_into_image(text, context_root, corpus),
          f"{corpus.relative_to(context_root).as_posix()} vs COPY sources "
          f"{_dockerfile_copy_sources(text)}")

    # (b) and the corpus in the source tree is one worth shipping. `entries` is
    #     printed rather than asserted at a number: it grows when somebody
    #     expands the list, and a threshold on the growing side of a >= is the
    #     kind of assertion that goes red for a correct change.
    status = passwords.corpus_status()
    check("corpus_status() reports the corpus PRESENT in the source tree",
          status["present"] is True, str(status["path"]))
    check("corpus_status() reports it SUFFICIENT (passwords.py's own threshold)",
          status["sufficient"] is True, f"{status['entries']} entries")

    # (c) the perturbation. Delete every COPY line that covers the corpus and
    #     require the predicate to NOTICE — this is the regression itself, run
    #     against a string instead of against the repo.
    without = "\n".join(
        line for line in text.splitlines()
        if not (line.strip().upper().startswith("COPY ")
                and _copied_into_image(line, context_root, corpus))
    )
    check("...and the check FAILS when that COPY is removed (perturbed)",
          not _copied_into_image(without, context_root, corpus),
          "predicate still reported the corpus shipped with no COPY covering it")
# 22. An EMPTY graph is an empty answer, not a fault (TASK-1129)
#
# THIS SECTION NEEDS ITS OWN EMPTY DATABASE, which is why it runs LAST and why
# it is the only section here that is destructive. Every other check is built
# on `testseed`'s fixture, and "there are no nodes" is not a state a fixture
# can hold at the same time as its cases.
#
# It gets that database from `app.startup()`'s own ATLAS_RESEED path, which is
# DELIBERATELY ASYMMETRIC — it drops and recreates the GRAPH and never touches
# the ACCOUNTS — so the service token section 12 minted still authenticates
# against a store with nothing in it. Hand-written DELETEs were the obvious
# alternative and are worse: they would have to know every table carrying a
# `node.id` foreign key, and would go quietly stale the first time one was
# added.
#
# THE CLAIM. `/api/graph` and `/api/graph/tree` derive their roots the same way
# ("a node with no incoming `contains`"), so they must agree about a database
# with no roots to derive. `/api/graph` used to answer 404 there while
# `/api/graph/tree` answered 200 with an empty list, and the board — which
# fetches both — printed "Could not load the backlog graph." at a brand-new
# database (measured on the fork, 2026-08-22).
#
# The `focus` 404 is asserted UNCHANGED in the same breath, because deleting
# the wrong one of those two refusals is precisely how this fix goes wrong:
# a named-node 404 has to stay indistinguishable from "you are not granted it".
# =========================================================================== #

def verify_empty_store_is_not_an_error() -> None:
    print("\n-- 22. An EMPTY graph answers 200 with nothing in it (TASK-1129) --")

    # A real boot with the real reseed switch — not a truncate helper written
    # for the test. See this section's header for why.
    os.environ["ATLAS_RESEED"] = "1"
    if BOOT_SEEDS_SET:
        # Under a profile that seeds the system node set onto an empty graph (every profile but
        # `homelab`), the reseeded graph would hold that set; this section's claim is about a
        # store with nothing in it, so the set's seeding is stubbed out for this one boot.
        import system_node
        real_seed = system_node.seed
        system_node.seed = lambda s: []
        try:
            app.startup()
        finally:
            system_node.seed = real_seed
    else:
        app.startup()
    os.environ["ATLAS_RESEED"] = ""

    with app.session() as s:
        n_nodes = s.scalar(select(func.count()).select_from(Node))
        n_edges = s.scalar(select(func.count()).select_from(Edge))
    # Setup, not a claim about the product: if the reseed left rows behind then
    # every check below is measuring a populated store and proving nothing.
    check("22 setup: the store really is empty", n_nodes == 0 and n_edges == 0,
          f"{n_nodes} nodes, {n_edges} edges")

    client = _client(bearer=AUTH_BEARER)

    r = client.get("/api/graph")
    check("rootless /api/graph on an empty store returns 200, NOT the old 404",
          r.status_code == 200, f"got {r.status_code} {r.text[:160]}")
    if r.status_code == 200:
        d = r.json()
        check("empty /api/graph carries an empty node list", d["nodes"] == [],
              f"got {len(d['nodes'])}")
        check("empty /api/graph carries an empty edge list", d["edges"] == [],
              f"got {len(d['edges'])}")
        check("empty /api/graph is not flagged truncated", d["truncated"] is False)

    # The World tab's ACTUAL request. It goes down the same branch and carried
    # its own "no root nodes in families [...]" refusal; a filter that matches
    # nothing is not an error either.
    rf = client.get("/api/graph", params={"families": "object,bridge"})
    check("the World tab's families-filtered /api/graph returns 200 on an empty store",
          rf.status_code == 200, f"got {rf.status_code} {rf.text[:160]}")
    if rf.status_code == 200:
        check("families-filtered /api/graph carries an empty node list",
              rf.json()["nodes"] == [], f"got {len(rf.json()['nodes'])}")

    # The half that was never broken, asserted here so the two routes are
    # compared against EACH OTHER rather than each against a memory.
    rt = client.get("/api/graph/tree")
    check("/api/graph/tree returns 200 with no roots on an empty store",
          rt.status_code == 200 and rt.json()["roots"] == [],
          f"got {rt.status_code}")
    check("both graph routes agree about an empty store (neither refuses it)",
          r.status_code == 200 and rt.status_code == 200,
          f"/api/graph={r.status_code} /api/graph/tree={rt.status_code}")

    # THE REFUSAL THAT MUST SURVIVE. A caller who NAMES a node that is not
    # there still gets 404 — empty store or not. That is the same response a
    # node the caller may not see returns, and collapsing it would turn a
    # guessed id into a confirmed one.
    rfocus = client.get("/api/graph", params={"focus": "TASK-9001"})
    check("a NAMED focus that does not exist still 404s on an empty store",
          rfocus.status_code == 404, f"got {rfocus.status_code}")


if __name__ == "__main__":
    main()
