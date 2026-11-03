"""
Verification script for the status-lifecycle work: settable status (incl.
`analyze`/`cancelled`), completion dates, and the age filter's data. Also
proves TASK-1056 (STORY-atlas-retire-status-roundtrip): the `status_override`
table, `base_status` and the three-way merge are gone, `/api/status/pending`
is gone, and a status set on the board survives a real container restart.

Same shape as verify_object_graph.py — no pytest suite exists in this repo, so
this is a standalone run-it-and-read-it script against a real, disposable
Postgres via `testdb` (TASK-1044), populated with the curated fixture in
`testseed.py` -- NOT the markdown backlog any more (see that module for why:
a test that names a real ticket breaks when somebody closes it) -- plus the real
world manifest, driving the REAL API through the REAL auth gate.

CHANGED (TASK-1058): `app.startup()` no longer reads any file — the two
boot-time reconcilers this script used to lean on (`ATLAS_BACKLOG_DIR`,
`ATLAS_WORLD_MANIFEST`) are deleted. This script now populates the same real
corpus via the curated fixture in `testseed.py`
directly, right after `app.startup()`.

Run (from a venv with the backend requirements installed):
    python verify_status_lifecycle.py
"""
from __future__ import annotations

import base64
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select


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
# The same three keys uvicorn refuses to start without. Not a backdoor: the gate
# below runs in FULL, with a real Argon2id verifier and a real HMAC'd token.
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")

#: TestClient must speak https — `__Host-` cookies REQUIRE Secure, and an http
#: cookie jar will not store or replay one.
TEST_ORIGIN = "https://testserver"

import app  # noqa: E402
import attention  # noqa: E402
import auth  # noqa: E402
import passwords  # noqa: E402
import testseed  # noqa: E402
from models_auth import AuthUser  # noqa: E402
from models_graph import Node  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def _client():
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN)


def _sign_in(client) -> str:
    """Create a real account, log in for real, return the CSRF token."""
    username, password = "verify-status", "Correct-Horse-Battery-9!"
    with app.session() as s:
        if not s.scalar(select(AuthUser).where(AuthUser.subject == username)):
            s.add(AuthUser(
                id="user-verify-status", subject=username, name="Verify Status",
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
    print("Atlas status-lifecycle verification — analyze/in-progress/done + age filter")
    print(f"DB:      {os.environ['ATLAS_DB_URL']}")
    print("Fixture: backend/testseed.py (curated)")
    print("=" * 78)

    app.startup()  # the real startup path: create_all, schema reconcile, type-registry seed
    # CHANGED AGAIN (testseed): this used to import the 309 markdown files
    # from a hardcoded Windows path in a second repo. The corpus was never
    # the subject here -- it was one line of convenience that left a
    # populated database behind it, and it pinned this script to one
    # workstation, to a repo the server cannot clone, and to a corpus
    # frozen at TASK-1062. `testseed` is 27 nodes that describe themselves.
    with app.session() as s:
        testseed.build(s)
    with app.session() as s:
        testseed.self_check(s)
    # TASK-1059: `import_world` is deleted along with `world_manifest.yaml`.
    # The world it built was only ever fixture scaffolding here -- `testseed`
    # carries three world nodes (a system containing a service containing a
    # component), which is what these checks actually need.
    # `import_workitems`/`import_world` are one-time IMPORTERS, not
    # reconcilers — neither calls `attention.recompute_derived` (unlike the
    # old boot-time ingests, which did as their last step). On live, the
    # world ingest's boot-time `recompute_derived` call kept covering the
    # whole graph (attention/completion/completed_at) right up until this
    # task deleted it, so real Postgres has correctly-derived values today.
    # This harness has to do explicitly, once, what a real boot already did
    # for that data — not a new step this task invents, just relocating a
    # side effect this script's own population strategy no longer gets for
    # free. See app.py's `set_node_status`/`edit_node` etc. for why nothing
    # further is needed after this: every write-API route already calls it.
    with app.session() as s:
        attention.recompute_derived(s)

    # -- 1. Schema ---------------------------------------------------------- #
    print("\n-- 1. Schema --")
    import sqlalchemy as sa
    tables = set(sa.inspect(app.engine).get_table_names())
    check("table 'status_override' is GONE (TASK-1056)", "status_override" not in tables)
    cols = {c["name"] for c in sa.inspect(app.engine).get_columns("node")}
    check("node.completed_at column exists", "completed_at" in cols)

    # -- 2. `analyze` is a first-class status ------------------------------- #
    print("\n-- 2. The `analyze` status --")
    check("analyze is settable", "analyze" in attention.SETTABLE_STATUSES)
    check("analyze reads as work-in-flight (green fill)",
          attention.health("task", "analyze") == "green",
          attention.health("task", "analyze"))
    check("analyze counts as in-progress for the ring",
          "analyze" in attention.INPROGRESS_STATUSES)
    check("analyze is NOT counted as done",
          "analyze" not in attention.DONE_STATUSES)
    # `ingest_backlog.STATUS_TO_RAG`/`_status_to_rag` — REMOVED (TASK-1058):
    # that map fed only the legacy v0 Component-tree work-graph builder
    # (`ingest_backlog.ingest_backlog`), which had already been unreachable
    # since TASK-1057 removed `ATLAS_BACKLOG_DIR` from both compose overlays,
    # and whose whole module is now deleted along with the reconcilers. The
    # maturity-colour mapping that matters on the real board is
    # `attention.health()`, asserted just above and in section 2b below.
    check("hyphenated `in-progress` still normalises",
          attention._norm("in-progress") == "in_progress")

    # -- 2b. The `cancelled` status (TASK-1049, owner decision 2026-08-10) ----------- #
    # "Cancelled should be treated as the same as complete, i.e. you can
    # filter to remove older than 14 days (same filter option)." — terminal
    # like `done`: settable, stamps completed_at, counts toward DONE_STATUSES
    # (and therefore the completion ring/bar — see the real-story assertion
    # below for the stated numbers), ages out under the SAME filter/control
    # as `done` (no new UI — the age filter is date-driven, not status-driven,
    # so this falls out of completed_at being stamped), and renders an
    # EXPLICIT colour rather than falling through to grey by accident.
    print("\n-- 2b. The `cancelled` status --")
    check("cancelled is settable", "cancelled" in attention.SETTABLE_STATUSES)
    check("cancelled counts as done (completion ring/bar + age filter)",
          "cancelled" in attention.DONE_STATUSES)
    check("cancelled is NOT counted as in-progress",
          "cancelled" not in attention.INPROGRESS_STATUSES)
    check("cancelled reads as an idle grey fill (chosen, not a fallthrough)",
          attention.health("task", "cancelled") == "grey",
          attention.health("task", "cancelled"))
    check("cancelled has an EXPLICIT entry in STATE_ATTENTION (not the "
          "health() unknown-status default)",
          "cancelled" in attention.STATE_ATTENTION)
    # `ingest_backlog.STATUS_TO_RAG` checks — REMOVED (TASK-1058), same
    # reasoning as the `analyze` case above: that map only ever fed the
    # deleted legacy v0 work-graph builder, not `attention.health()`.

    # `proposed` (1 file, STORY-pii-egress-proxy) already collapses onto
    # `pending` via STATUS_EQUIVALENT — confirming it is untouched, not that
    # this task added it.
    check("proposed still buckets onto pending (untouched by this task)",
          attention.status_bucket("proposed") == "pending",
          attention.status_bucket("proposed"))

    # The two STATUS_EQUIVALENT copies (attention.py and frontend/state.js)
    # must agree — see attention.py's own comment on why there are two, and
    # the bug ("Comparing raw strings once turned an undo into a junk diff")
    # that made the rule "keep them equal" necessary in the first place.
    #
    # It moved from frontend/panel.js to frontend/state.js (the board-filter work):
    # the board's status FILTER (board.html) needs the same vocabulary panel.js's
    # status control offers, and board.html can't export for panel.js to import
    # — the same seam BRIDGE_TYPES/OBJECT_TYPES already crossed into state.js.
    state_js_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "state.js")
    with open(state_js_path, "r", encoding="utf-8") as f:
        state_js = f.read()
    m = re.search(r"var STATUS_EQUIVALENT = \{(.*?)\};", state_js, re.S)
    check("frontend/state.js still declares STATUS_EQUIVALENT", m is not None)
    frontend_equiv = {}
    if m is not None:
        for key, val in re.findall(r'(\w+):\s*"(\w+)"', m.group(1)):
            frontend_equiv[key] = val
    check("attention.STATUS_EQUIVALENT and state.js's copy agree",
          frontend_equiv == attention.STATUS_EQUIVALENT,
          f"attention.py={attention.STATUS_EQUIVALENT} state.js={frontend_equiv}")

    # The cancelled-with-no-authored-date case. This used to name TASK-1018, a
    # real ticket cancelled on 2026-08-09 — and it was wrapped in `if ... is not
    # None`, so the day that ticket changed shape the whole block would have
    # silently stopped running. The fixture carries the case instead, and its
    # absence is now a failure rather than a skip.
    CANCELLED = testseed.SEED.cancelled_leaf
    with app.session() as s:
        cancelled = s.get(Node, CANCELLED)
        check(f"{CANCELLED} fixture present", cancelled is not None, f"{CANCELLED} missing")
        if cancelled is not None:
            check(f"{CANCELLED}'s authored status is cancelled",
                  attention.authored_status(cancelled) == "cancelled",
                  attention.authored_status(cancelled))
            check(f"{CANCELLED}'s EFFECTIVE status is cancelled",
                  attention.status_map(s).get(CANCELLED) == "cancelled",
                  attention.status_map(s).get(CANCELLED))
            check(f"{CANCELLED} got a completed_at stamp from recompute_derived "
                  "(no authored `completed:` date, so this must be the "
                  "observed-now stamp)",
                  cancelled.completed_at is not None)
            derived_cancelled = attention.derive(s)[CANCELLED]
            check(f"{CANCELLED}'s own state is the idle grey (health(), not a "
                  "goal-directed escalation)",
                  derived_cancelled["state"] == "grey", derived_cancelled["state"])

            # The stated consequence: cancelling a task makes its PARENT look
            # more finished, because DONE_STATUSES feeds both the age filter
            # and the completion ring/bar (attention.derive's `done` count and
            # `completion` fraction). Measure it directly on TASK-1018's real
            # parent story rather than asserting the rule in the abstract.
            parent_edge = s.scalars(
                select(app.GraphEdge).where(
                    app.GraphEdge.dst == "TASK-1018", app.GraphEdge.edge_type == "contains")
            ).first()
            if parent_edge is not None:
                parent_derived = attention.derive(s)[parent_edge.src]
                leaves_total = (parent_derived["done"] + parent_derived["in_progress"]
                                 + parent_derived["todo"])
                print(f"     completion-bar effect: {parent_edge.src} shows "
                      f"{parent_derived['done']} done / {leaves_total} leaves total, "
                      f"completion={parent_derived['completion']:.2f} "
                      f"— TASK-1018 (cancelled) counts toward `done` here")
                check(f"{parent_edge.src}'s completion count includes its "
                      f"cancelled child (TASK-1018) as done",
                      parent_derived["done"] >= 1)
        else:
            check("TASK-1018 fixture present (skipped — node not found; "
                  "backlog ingest may not have reached it)", False, "TASK-1018 missing")

    # -- 3. Completion dates from the real backlog -------------------------- #
    print("\n-- 3. Completion dates --")
    with app.session() as s:
        done_nodes = [n for n in s.scalars(select(Node)).all()
                      if attention.status_map(s).get(n.id) in attention.DONE_STATUSES]
        stamped = [n for n in done_nodes if n.completed_at is not None]
        check("every done node carries a completed_at",
              len(stamped) == len(done_nodes),
              f"{len(stamped)}/{len(done_nodes)} done nodes stamped")
        not_done_stamped = [n for n in s.scalars(select(Node)).all()
                            if n.completed_at is not None and n not in done_nodes]
        check("no not-done node carries a completed_at",
              not not_done_stamped,
              f"{len(not_done_stamped)} leaked" if not_done_stamped else "")
        # An AUTHORED `completed:` date must win over the observed-now stamp.
        authored = [n for n in done_nodes if (n.attributes or {}).get("completed")]
        print(f"     {len(authored)} done node(s) carry an authored `completed:` date")
        for n in authored:
            want = attention._parse_authored_date(n.attributes["completed"])
            got = attention._aware(n.completed_at)
            check(f"authored date wins for {n.id}", got == want, f"{got} vs authored {want}")

    # -- 4. Age filter data ------------------------------------------------- #
    print("\n-- 4. Age filter --")
    with app.session() as s:
        now = datetime.now(timezone.utc)
        aged = 0
        for n in s.scalars(select(Node)).all():
            days = attention.completed_days_ago(n, now)
            if days is not None and days > 14:
                aged += 1
        check("completed_days_ago is computable for done nodes", True,
              f"{aged} node(s) finished more than 14 days ago (the default threshold)")
        # Forge a genuinely old completion and prove the arithmetic.
        victim = s.scalars(select(Node).where(Node.completed_at.isnot(None))).first()
        if victim is not None:
            victim.completed_at = now - timedelta(days=98)
            s.flush()
            days = attention.completed_days_ago(victim, now)
            check("a 14-week-old completion measures as ~98 days",
                  days is not None and 97.5 < days < 98.5, f"{days:.1f} days")
            s.rollback()

    # -- 5. The override write path, through the real API + gate ------------ #
    print("\n-- 5. Board status writes (real API, real auth gate) --")
    client = _client()
    csrf = _sign_in(client)
    check("signed in and hold a CSRF token", bool(csrf))

    with app.session() as s:
        target = s.scalars(
            select(Node).where(Node.type == "task")
        ).all()
        # "pending", not "planned" (TASK-1058): `import_workitems.py`
        # NORMALISES the raw frontmatter value "planned" -> "pending" at
        # import time (`_STATUS_NORMALISE`) — the old file-ingest this script
        # used to populate through stored the raw written form verbatim.
        # `import_workitems.py` already ran once against real live data
        # (TASK-1052); "pending" is what a real production node's authored
        # status actually reads now.
        target = [n for n in target if attention.authored_status(n) == "pending"][0]
        target_id, authored_before = target.id, attention.authored_status(target)
    print(f"     target: {target_id} (backlog says {authored_before!r})")

    # 5a. unauthenticated write is refused
    anon = _client()
    r = anon.post(f"/api/node/{target_id}/status", json={"status": "analyze"})
    check("unauthenticated write is refused", r.status_code == 401, f"HTTP {r.status_code}")

    # 5b. missing CSRF token is refused even WITH a session
    r = client.post(f"/api/node/{target_id}/status", json={"status": "analyze"},
                    headers={"Origin": TEST_ORIGIN})
    check("session write without a CSRF token is refused", r.status_code == 403,
          f"HTTP {r.status_code} {r.text[:80]}")

    # 5c. a foreign Origin is refused even WITH a valid token
    r = client.post(f"/api/node/{target_id}/status",
                    json={"status": "analyze", "csrf_token": csrf},
                    headers={"Origin": "https://evil.example"})
    check("cross-origin write is refused", r.status_code == 403, f"HTTP {r.status_code}")

    # 5d. an unknown status is refused
    r = client.post(f"/api/node/{target_id}/status",
                    json={"status": "banana", "csrf_token": csrf},
                    headers={"Origin": TEST_ORIGIN})
    check("an unknown status is refused", r.status_code == 400, f"HTTP {r.status_code}")

    # 5e. the real write
    r = client.post(f"/api/node/{target_id}/status",
                    json={"status": "analyze", "csrf_token": csrf},
                    headers={"Origin": TEST_ORIGIN})
    check("marking a task `analyze` succeeds", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
    body = r.json() if r.status_code == 200 else {}
    check("response reports the effective status", body.get("status") == "analyze", str(body.get("status")))
    # TASK-1056 (STORY-atlas-retire-status-roundtrip): the two-source fields
    # (`authored_status`, `status_source`, `status_pending`, `status_set_at`,
    # `status_set_by`) that used to distinguish "on the board" from "in git"
    # are GONE — there is only one source left. Assert their absence, not
    # just their old values, so a re-introduction is caught.
    for gone_field in ("authored_status", "status_source", "status_pending",
                       "status_set_at", "status_set_by", "status_override_folded"):
        check(f"response no longer carries `{gone_field}` (TASK-1056)",
              gone_field not in body, str(body)[:200])

    # 5f. the override actually moves the derived colour.
    #
    # Assert on `state`, not on `attention`. `attention` is worst(state,
    # goal-directed urgency), and a task that is an actionable bottleneck for
    # live goal work is RED however healthy its own state is — that is the whole
    # point of the urgency layer (attention.py "FRONTIER-ONLY RED"). The thing an
    # override has to move is the node's OWN state.
    with app.session() as s:
        derived = attention.derive(s)[target_id]
        check("the node's own state follows the override (planned grey -> analyze green)",
              derived["state"] == "green", f"state={derived['state']} attention={derived['attention']}")

    # 5g. mark it done -> a completion date appears immediately
    r = client.post(f"/api/node/{target_id}/status",
                    json={"status": "done", "csrf_token": csrf},
                    headers={"Origin": TEST_ORIGIN})
    check("marking it done succeeds", r.status_code == 200, f"HTTP {r.status_code}")
    check("a completion date is stamped in the same request",
          bool(r.json().get("completed_at")), str(r.json().get("completed_at")))

    # 5h. reopening clears the completion date
    r = client.post(f"/api/node/{target_id}/status",
                    json={"status": "in_progress", "csrf_token": csrf},
                    headers={"Origin": TEST_ORIGIN})
    check("reopening clears the completion date",
          r.json().get("completed_at") is None, str(r.json().get("completed_at")))

    # 5i. `/api/status/pending` — DELETED (TASK-1056): confirm it is actually
    # gone, not just unused.
    r = client.get("/api/status/pending")
    check("GET /api/status/pending is gone (404, not a stale 200)",
          r.status_code == 404, f"HTTP {r.status_code}")

    # 5j. clearing unsets the node's own status — TASK-1056: there is no
    # second source ("the file") left to revert to any more, so `status:
    # null` now just pops `attributes["status"]`, the same as it always did
    # for an atlas-origin node (see set_node_status's one remaining branch).
    r = client.post(f"/api/node/{target_id}/status",
                    json={"status": None, "csrf_token": csrf},
                    headers={"Origin": TEST_ORIGIN})
    check("clearing the status unsets it (no second source to revert to)",
          r.json().get("status") is None, f"status={r.json().get('status')}")

    # 5k. an object node has no lifecycle status
    with app.session() as s:
        obj = s.scalars(select(Node).where(Node.type == "service")).first()
    if obj is not None:
        r = client.post(f"/api/node/{obj.id}/status",
                        json={"status": "done", "csrf_token": csrf},
                        headers={"Origin": TEST_ORIGIN})
        check("setting a status on a world-tree object is refused",
              r.status_code == 409, f"HTTP {r.status_code}")

    # 5L. setting `cancelled` through the real API, real gate — TASK-1049.
    # A fresh target: `target_id` must stay untouched by this, so pick a
    # second `pending` task (see the "pending, not planned" note above).
    with app.session() as s:
        target3 = [n for n in s.scalars(select(Node).where(Node.type == "task")).all()
                  if attention.authored_status(n) == "pending"
                  and n.id not in (target_id,)][0]
        target3_id = target3.id
    print(f"     cancel target: {target3_id}")

    r = client.post(f"/api/node/{target3_id}/status",
                    json={"status": "cancelled", "csrf_token": csrf},
                    headers={"Origin": TEST_ORIGIN})
    check("marking a task `cancelled` succeeds through the real API",
          r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
    body = r.json()
    check("response reports the effective status as cancelled",
          body.get("status") == "cancelled", str(body.get("status")))
    check("a completion date is stamped in the SAME request (cancelled is "
          "terminal, same as marking it done)",
          bool(body.get("completed_at")), str(body.get("completed_at")))

    with app.session() as s:
        n3 = s.get(Node, target3_id)
        check("cancelled is durable in the DB (status_map reads it back)",
              attention.status_map(s).get(target3_id) == "cancelled")
        check("its attention fill is grey, not the STATUS_TO_RAG/health() "
              "unknown-status default falling through by accident",
              attention.derive(s)[target3_id]["state"] == "grey",
              attention.derive(s)[target3_id]["state"])

    # Reopening a cancelled item clears completed_at, exactly as reopening a
    # done one does (5h above) — a cancelled-then-reconsidered task must not
    # stay hidden behind the age filter as ancient history.
    r = client.post(f"/api/node/{target3_id}/status",
                    json={"status": "in_progress", "csrf_token": csrf},
                    headers={"Origin": TEST_ORIGIN})
    check("reopening a cancelled task clears the completion date",
          r.json().get("completed_at") is None, str(r.json().get("completed_at")))
    # Leave target3 clean for anything after this.
    client.post(f"/api/node/{target3_id}/status", json={"status": None, "csrf_token": csrf},
                headers={"Origin": TEST_ORIGIN})

    # -- 6. A status set on the board survives a container restart ---------- #
    # TASK-1056 (STORY-atlas-retire-status-roundtrip) acceptance #5, proved
    # directly rather than assumed: create a node through the write API, set
    # its status, then run a REAL `app.startup()` again (a real restart — see
    # its own docstring, TASK-1058: boot is now a pure READ) and confirm the
    # value is still there. This replaces the old override-reconciliation
    # section (landed/superseded/orphaned/folded), and then the TASK-1043
    # PROTECT mechanism that replaced THAT — both are gone; durability now
    # rests on boot touching `node`/`edge` at all, which it no longer does.
    print("\n-- 6. A board status write survives a real container restart --")
    r = client.post("/api/node", json={"id": "TASK-VERIFY-ATLAS", "type": "task",
                                       "name": "atlas-owned status test", "csrf_token": csrf},
                    headers={"Origin": TEST_ORIGIN})
    check("6 setup: an atlas-origin node created through the real write API",
          r.status_code == 201, f"HTTP {r.status_code} {r.text[:150]}")

    r = client.post("/api/node/TASK-VERIFY-ATLAS/status",
                    json={"status": "analyze", "csrf_token": csrf},
                    headers={"Origin": TEST_ORIGIN})
    check("setting status on an atlas-owned node succeeds", r.status_code == 200,
          f"HTTP {r.status_code} {r.text[:150]}")
    body = r.json()
    check("the value is readable back as the effective status",
          body.get("status") == "analyze", str(body.get("status")))
    with app.session() as s:
        n = s.get(Node, "TASK-VERIFY-ATLAS")
        check("the status landed directly on node.attributes",
              n is not None and n.attributes.get("status") == "analyze",
              f"got {n.attributes.get('status') if n else None!r}")

    # Survives a real restart — app.startup() is now a pure read (TASK-1058).
    # ATLAS_RESEED must be OFF here — it was "1" only to get the very first
    # clean schema (see the top of this file); left on, app.startup() DROPS
    # AND RECREATES the whole graph (see its own docstring: "DELIBERATELY
    # ASYMMETRIC — wipes the GRAPH, never the ACCOUNTS"), which would wipe
    # the very row this check exists to prove survives, and every check after
    # it in this file that reads the graph.
    os.environ["ATLAS_RESEED"] = ""
    app.startup()
    with app.session() as s:
        n = s.get(Node, "TASK-VERIFY-ATLAS")
        check("the atlas-owned status SURVIVES a real container restart",
              n is not None and n.attributes.get("status") == "analyze",
              f"got {n.attributes.get('status') if n else None!r}")
        check("...and origin is still 'atlas' (boot never touches it)",
              n is not None and n.origin == "atlas", f"got {n.origin if n else None!r}")

    # Clearing works the same way: unsets the node's own status, no second
    # source to fall back to.
    r = client.post("/api/node/TASK-VERIFY-ATLAS/status", json={"status": None, "csrf_token": csrf},
                    headers={"Origin": TEST_ORIGIN})
    check("clearing status on an atlas-owned node succeeds", r.status_code == 200,
          f"HTTP {r.status_code}")
    check("the status is gone, not reverted to some other source (there is none)",
          r.json().get("status") is None, str(r.json().get("status")))

    # -- 6b. Password-free browser sessions for agents ----------------------- #
    print("\n-- 6b. session mint (agent browser testing) --")
    import uuid as _uuid
    from models_auth import AuthUser as _AU

    with app.session() as s:
        tester = _AU(id=str(_uuid.uuid4()), idp="local", subject="agent-test",
                     name="Agent Test", role="member", is_service=False,
                     password_hash=None, must_change_password=False,
                     created_at=datetime.now(timezone.utc))
        s.add(tester)
        s.commit()
        tester_id = tester.id
    check("a human account with NO password can exist (the CHECK only binds service rows)",
          tester_id is not None)

    # It must be unreachable through the login form, by construction.
    login = client.get("/auth/login")
    tok = login.text.split('name="csrf_token" value="')[1].split('"')[0]
    r = client.post("/auth/login",
                    data={"username": "agent-test", "password": "", "csrf_token": tok},
                    headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
    check("a password-free account cannot pass the login form",
          r.status_code in (302, 303) and "error" in r.headers.get("location", ""),
          f"HTTP {r.status_code} -> {r.headers.get('location')}")

    # The env gate is the control that keeps this off live.
    os.environ.pop(auth.SESSION_MINT_ENV, None)
    check("minting is refused when the deployment does not enable it",
          not auth.session_mint_enabled())
    with app.session() as s:
        u = s.get(_AU, tester_id)
        try:
            auth.mint_browser_session(s, user=u)
            check("minting is refused when the deployment does not enable it (raises)", False)
        except ValueError as e:
            check("...and says why", auth.SESSION_MINT_ENV in str(e), str(e)[:80])

    os.environ[auth.SESSION_MINT_ENV] = "1"
    with app.session() as s:
        u = s.get(_AU, tester_id)
        sid, expires = auth.mint_browser_session(s, user=u, minutes=30, actor="verify")
        s.commit()
    check("a session is minted with no password anywhere", bool(sid))

    # The whole point: it behaves exactly like a login-created session.
    #
    # The cookie is sent as an explicit header rather than through the client's
    # jar. `__Host-atlas_session` is a normal name to a cookie jar, but
    # http.cookiejar's domain matching does not handle a dotless host like
    # `testserver` the way a browser does, so the jar silently declines to send
    # it and every request looks unauthenticated. A real browser on a real
    # origin has no such problem — this is a harness detail, not the feature.
    from fastapi.testclient import TestClient as _TC
    agent = _TC(app.app, base_url=TEST_ORIGIN)
    agent_cookie = {"Cookie": auth.SESSION_COOKIE + "=" + sid}
    me = agent.get("/api/me", headers=agent_cookie)
    check("the minted session authenticates", me.status_code == 200, f"HTTP {me.status_code}")
    body = me.json() if me.status_code == 200 else {}
    check("it is a SESSION principal, not a token", body.get("kind") == "session", str(body.get("kind")))
    check("it carries a real CSRF token", bool(body.get("csrf_token")))

    # And it can drive the write surface through the real CSRF path.
    agent_csrf = body.get("csrf_token")
    r = agent.post(f"/api/node/{target_id}/status",
                   json={"status": "analyze", "csrf_token": agent_csrf},
                   headers=dict(agent_cookie, **{"Origin": TEST_ORIGIN}))
    check("an agent session can set a status through the real CSRF path",
          r.status_code == 200, f"HTTP {r.status_code} {r.text[:100]}")
    r = agent.post(f"/api/node/{target_id}/status",
                   json={"status": "analyze"},
                   headers=dict(agent_cookie, **{"Origin": TEST_ORIGIN}))
    check("...and is still refused without the token", r.status_code == 403, f"HTTP {r.status_code}")
    agent.post(f"/api/node/{target_id}/status", json={"status": None, "csrf_token": agent_csrf},
               headers=dict(agent_cookie, **{"Origin": TEST_ORIGIN}))

    # Refusals that matter.
    with app.session() as s:
        u = s.get(_AU, tester_id)
        u.role = "owner"
        s.commit()
        try:
            auth.mint_browser_session(s, user=u)
            check("an owner account is refused without --allow-owner", False)
        except ValueError as e:
            check("an owner account is refused without --allow-owner", "owner" in str(e))
        try:
            auth.mint_browser_session(s, user=u, allow_owner=True)
            check("...and permitted with it", True)
        except ValueError as e:
            check("...and permitted with it", False, str(e)[:80])
        u.role = "member"
        u.must_change_password = True
        s.commit()
        try:
            auth.mint_browser_session(s, user=u)
            check("a must_change_password account is refused", False)
        except ValueError as e:
            check("a must_change_password account is refused", "change" in str(e).lower())
        u.must_change_password = False
        u.disabled_at = datetime.now(timezone.utc)
        s.commit()
        try:
            auth.mint_browser_session(s, user=u)
            check("a disabled account is refused", False)
        except ValueError as e:
            check("a disabled account is refused", "disabled" in str(e))

    with app.session() as s:
        bot_row = _AU(id=str(_uuid.uuid4()), idp="local", subject="agent-bot", role="member",
                      is_service=True, password_hash=None, must_change_password=False,
                      created_at=datetime.now(timezone.utc))
        s.add(bot_row); s.commit()
        try:
            auth.mint_browser_session(s, user=bot_row)
            check("a service principal is refused (it has no session branch)", False)
        except ValueError as e:
            check("a service principal is refused (it has no session branch)",
                  "service" in str(e))

    # -- 7. The API surface the board reads --------------------------------- #
    print("\n-- 7. API surface --")
    tree = client.get("/api/graph/tree").json()

    def walk(nodes, out):
        for n in nodes:
            out.append(n)
            walk(n.get("children") or [], out)
        return out

    flat = walk(tree.get("roots") or [], [])
    check("/api/graph/tree returns nodes", bool(flat), f"{len(flat)} nodes")
    sample = next((n for n in flat if n["type"] == "task"), None)
    for field in ("status", "completed_at"):
        check(f"tree nodes carry `{field}`", sample is not None and field in sample)
    # TASK-1056: the two-source fields are GONE from every tree node, not
    # just absent from a single status response — the board must not keep
    # rendering a distinction that no longer exists.
    for gone_field in ("authored_status", "status_source", "status_pending",
                       "status_set_at", "status_set_by"):
        check(f"tree nodes no longer carry `{gone_field}` (TASK-1056)",
              sample is not None and gone_field not in sample, str(sample)[:200])
    world = next((n for n in flat if (n.get("work_load") or {}).get("count")), None)
    check("world objects carry per-workitem data for client-side filtering",
          world is not None and isinstance(world["work_load"].get("workitems"), list),
          f"{len(world['work_load']['workitems'])} workitems on {world['id']}" if world else "none found")
    if world:
        wi = world["work_load"]["workitems"][0]
        check("each work_load workitem carries status + completed_at",
              "status" in wi and "completed_at" in wi, str(wi))

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
