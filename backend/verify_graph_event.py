"""
Verification for `graph_event` — TASK-1045, the audit log this repo has been
naming in comments since 2026-08-10 and had not built.

No pytest suite exists in this repo (see verify_object_graph.py's docstring);
this follows the same standalone, run-it-and-read-it convention as its
siblings, and takes its harness shape from verify_assignment.py because the
property in section E is that access layer's and is proven the same way — by
attempting the violation, never by asserting the happy path.

SEVEN SECTIONS:

  A. THE TABLE. `graph_event` exists, its timestamp is timezone-aware, and it
     carries NO foreign key and NO check constraint. Both absences are
     asserted rather than assumed: `_reconcile_graph_schema`'s entire DDL
     vocabulary is `ALTER TABLE ... ADD COLUMN <type>`, so an FK or a CHECK
     declared on the model would exist on every fresh test database and never
     on live — and this suite would then prove constraints the deployment does
     not have. The assertion is that they are absent.

  B. EXACTLY ONE WRITER. `app.record_graph_event` is the only place in the
     backend that constructs a `GraphEvent`, proven by scanning the source
     rather than by convention — a scattering of inline inserts is precisely
     the failure this table's design note forbids, and it is the one that
     creeps back in silently.

  C. WRITES HAPPEN ON REAL MUTATIONS. Every action in
     `GRAPH_EVENT_ACTIONS` is produced by driving the REAL API through the
     REAL gate: node create/update/delete, status, assignee, edge
     create/delete. A log nobody writes to passes every test that only reads
     it back.

  D. THE ACTOR IS THE AUTHENTICATED PRINCIPAL. A session write names the
     person; an agent's write names the agent AND resolves
     `responsible_user_id` through ADR 0034's person -> bot link; an agent
     with no person recorded resolves to NULL rather than to itself. And the
     forgery case: a request body carrying `actor`/`responsible_user_id`
     changes nothing, because there is no parameter through which to try.

  E. EVENTS ARE ACCESS-FILTERED, AND THE COUNT IS TOO. The load-bearing one.
     A member with no grant on a private project must not read an event about
     it — and must not be able to COUNT one either, which is why this section
     measures `total` BEFORE and AFTER the owner generates private events and
     requires it not to move. A post-filter over the page would pass a
     "the rows are absent" check and fail this one.

  F. THE READ API. Paging honesty on a corpus LARGER than the page-size cap
     (verify_assignment.py §G exists because a suite passed while a view
     rendered 200 of 372 rows labelled "200 items"), the `entity_id` filter,
     the time bound, and the refusal of a malformed one.

  G. THE ROUTES THAT COULD CHANGE MEANING WITHOUT LEAVING A TRACE. Section C
     drives the routes that were wired first; this one drives the holes found
     afterwards. `NodePatch.attributes` accepts `{"status": "done"}`, so the
     GENERIC node route (and `PATCH /api/workitem`, one door over) could move a
     workitem's whole lifecycle while the log recorded only "fields:
     [attributes]" — the audit table's most-asked question, *who set this to
     done*, had a hole straight through it. Node documents and node intents
     were not recorded at all. Each newly-wired route is proven twice here: the
     event is WRITTEN, and it is ACCESS-FILTERED — the second half in §E's
     style, by generating the events on a project a member cannot see and
     requiring that member's `total` not to move.

Run (from an environment with SQLAlchemy/psycopg2/jsonschema installed):
    python verify_graph_event.py
"""
from __future__ import annotations

import base64
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:  # pragma: no cover - no skip path, ever
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

from sqlalchemy import inspect as sa_inspect, select  # noqa: E402

import app  # noqa: E402
import auth  # noqa: E402
import models_graph  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthUser, NodeGrant, NodeVisibility  # noqa: E402
from models_graph import GraphEvent, Node  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


HERE = os.path.dirname(os.path.abspath(__file__))

# The API's modules, in which the call sites below are looked for. This was
# `app.py` alone until STORY-oss-04 split it into main/db/deps and routers/;
# the checks are unchanged, only the set of files they read is.
def _api_modules() -> list[str]:
    names = [n for n in ("app.py", "main.py", "db.py", "deps.py")
             if os.path.exists(os.path.join(HERE, n))]
    routers = os.path.join(HERE, "routers")
    if os.path.isdir(routers):
        names += [os.path.join("routers", n) for n in sorted(os.listdir(routers))
                  if n.endswith(".py")]
    return names


def _emitting_source() -> str:
    """Every production module that may call the writer, for the vocabulary check: the API
    modules above PLUS every other top-level backend module (crunch.py, whose expiry is
    recorded by whichever read notices it; adr_alloc.py, cicd.py, crunch_backup.py, ... run by
    the CLI and the opt-in services). Test-only modules are excluded. Reading MORE files only
    makes "emitted <= declared" stricter, and lets "declared <= emitted" see an action whose
    one emit site is outside a router (STORY-atlas-crunch-mode)."""
    names = list(_api_modules())
    for n in sorted(os.listdir(HERE)):
        if (n.endswith(".py") and n not in names and n != "models_graph.py"
                and not n.startswith(("verify_", "test")) and "testkit" not in n):
            names.append(n)
    return "\n".join(open(os.path.join(HERE, n), encoding="utf-8").read() for n in names)

OWNER_ID = "u-ev-owner"
AVERY_ID = "u-ev-avery"
BJORN_ID = "u-ev-bjorn"
AGENT_ID = "u-ev-agent"          # linked to AVERY
LONE_AGENT_ID = "u-ev-lone-bot"  # linked to nobody

PW = "Correct-Horse-Battery-GraphEvent-9!"


def mk_user(uid, username, role="member", is_service=False, person=None) -> None:
    with app.session() as s:
        if s.get(AuthUser, uid) is None:
            s.add(AuthUser(
                id=uid, subject=username, name=username.title(), role=role,
                is_service=is_service, must_change_password=False,
                # A service row may hold no password
                # (ck_auth_user_service_has_no_password).
                password_hash=None if is_service else passwords.hash_password(PW),
                person_user_id=person,
                created_at=datetime.now(timezone.utc)))
            s.commit()


def sign_in(username: str):
    """A real signed-in client, cookie and CSRF token both. Deliberately NOT a
    ContextVar shortcut: the principal a route sees comes from the middleware,
    and this suite's whole claim in section D is about what the middleware
    resolved — installing a principal directly would prove the assertion
    against itself."""
    from fastapi.testclient import TestClient
    c = TestClient(app.app, base_url=TEST_ORIGIN)
    page = c.get("/auth/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": username, "password": PW, "csrf_token": token},
           headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
    return c, c.get("/api/me").json()["csrf_token"]


def bearer_client(raw: str):
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN,
                      headers={"Authorization": f"Bearer {raw}"})


def mint(owner_id: str, label: str) -> str:
    with app.session() as s:
        raw, _row = auth.mint_service_token(
            s, owner=s.get(AuthUser, owner_id), scopes=["read", "write", "admin"], label=label)
        s.commit()
    return raw


def events_for(entity_id: str, action: str | None = None) -> list[GraphEvent]:
    """Straight out of the table, no HTTP — sections A-D assert what was
    WRITTEN; section E is the one that asserts what is SERVED."""
    with app.session() as s:
        stmt = select(GraphEvent).where(GraphEvent.entity_id == entity_id)
        if action:
            stmt = stmt.where(GraphEvent.action == action)
        return list(s.scalars(stmt.order_by(GraphEvent.id)).all())


# --------------------------------------------------------------------------- #
# A. The table
# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. the table: shaped like auth_event, constrained like nothing --")
    insp = sa_inspect(app.engine)
    check("graph_event exists", "graph_event" in insp.get_table_names(),
          str(sorted(t for t in insp.get_table_names() if "event" in t)))

    cols = {c["name"]: c for c in insp.get_columns("graph_event")}
    for name in ("id", "at", "action", "entity_type", "entity_id", "node_id",
                 "related_node_id", "actor_kind", "actor_user_id", "actor_subject",
                 "actor_token_id", "responsible_user_id", "detail"):
        check(f"column {name} exists", name in cols, f"columns: {sorted(cols)}")

    if "at" in cols:
        check("`at` is TIMEZONE-AWARE — a bare timestamp would make two events "
              "from two timezones unorderable",
              getattr(cols["at"]["type"], "timezone", False) is True,
              str(cols["at"]["type"]))
        check("`at` is NOT NULL", cols["at"]["nullable"] is False)

    fks = insp.get_foreign_keys("graph_event")
    check("NO foreign key is declared on ANY column — _reconcile_graph_schema "
          "cannot emit REFERENCES, so an FK here would exist on test databases "
          "and never on live; and the node columns must OUTLIVE the node",
          fks == [], f"found {fks}")

    checks = insp.get_check_constraints("graph_event")
    check("NO check constraint on the action vocabulary — a new action name "
          "must never be an ALTER TABLE against a populated Postgres",
          checks == [], f"found {checks}")

    idx = {i["name"] for i in insp.get_indexes("graph_event")}
    for name in ("ix_graph_event_at", "ix_graph_event_entity",
                 "ix_graph_event_node_id", "ix_graph_event_responsible"):
        check(f"index {name} exists", name in idx, str(sorted(idx)))

    # The vocabulary and the call sites must agree. Derived from the source,
    # not pinned to a list written twice.
    src = _emitting_source()
    emitted = set(re.findall(r'record_graph_event\(\s*s,\s*"([^"]+)"', src))
    check("every action the API emits is in models_graph.GRAPH_EVENT_ACTIONS",
          emitted <= set(models_graph.GRAPH_EVENT_ACTIONS),
          f"not declared: {sorted(emitted - set(models_graph.GRAPH_EVENT_ACTIONS))}")
    check("...and every declared action is actually emitted somewhere — a "
          "vocabulary entry nothing writes is a promise the table does not keep",
          set(models_graph.GRAPH_EVENT_ACTIONS) <= emitted,
          f"declared but never emitted: {sorted(set(models_graph.GRAPH_EVENT_ACTIONS) - emitted)}")


def _construction_sites(path: str) -> list[int]:
    """Line numbers where `GraphEvent(` is CODE, not prose.

    Tokenised rather than grepped, and that is not fastidiousness: the first
    version of this check regexed the raw source and turned red on
    `record_graph_event`'s own comment, which says "DO NOT INLINE A
    `s.add(GraphEvent(...))` ANYWHERE ELSE". A check a comment can break is a
    check people learn to edit around."""
    import tokenize
    hits: list[int] = []
    prev = None
    with open(path, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL,
                            tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
                continue
            if (tok.type == tokenize.OP and tok.string == "("
                    and prev is not None and prev.type == tokenize.NAME
                    and prev.string == "GraphEvent"):
                hits.append(prev.start[0])
            prev = tok
    return hits


# --------------------------------------------------------------------------- #
# B. Exactly one writer
# --------------------------------------------------------------------------- #
def section_b() -> None:
    print("\n-- B. exactly ONE writer, proven from the source --")
    check("app.record_graph_event exists and is callable",
          callable(getattr(app, "record_graph_event", None)))

    # The module that holds the writer (app.py until STORY-oss-04, deps.py since).
    writer = next(n for n in _api_modules()
                  if "def record_graph_event(" in open(os.path.join(HERE, n), encoding="utf-8").read())
    offenders: list[str] = []
    total = 0
    routers = os.path.join(HERE, "routers")
    for name in sorted(os.listdir(HERE)) + (
            [os.path.join("routers", n) for n in sorted(os.listdir(routers))]
            if os.path.isdir(routers) else []):
        if not name.endswith(".py"):
            continue
        # models_graph.py DECLARES the class; the verify scripts read it back.
        # Neither is a writer.
        if name == "models_graph.py" or name.startswith("verify_"):
            continue
        hits = _construction_sites(os.path.join(HERE, name))
        total += len(hits)
        if hits and name != writer:
            offenders.append(f"{name}:{len(hits)}")
    check(f"no backend module outside {writer} constructs a GraphEvent",
          not offenders, str(offenders))
    check(f"{writer} constructs one EXACTLY ONCE — inside record_graph_event; a "
          "second construction is an inline insert, which is the failure this "
          "table's design note forbids",
          total == 1, f"{total} construction site(s)")

    # ...and that one site is inside the writer, not merely somewhere in the file.
    sites = _construction_sites(os.path.join(HERE, writer))
    src = open(os.path.join(HERE, writer), encoding="utf-8").read()
    first = src[:src.index("def record_graph_event(")].count("\n") + 1
    last = src[:src.index("def _responsible_user_id(")].count("\n") + 1
    check("the single construction site is inside record_graph_event's body",
          bool(sites) and all(first < ln < last for ln in sites),
          f"lines {sites}, writer spans {first}..{last}")

    check("record_graph_event takes NO actor parameter — a caller cannot forge "
          "who did something because there is no argument through which to try",
          not re.search(r"def record_graph_event\([^)]*actor", src, re.S))


# --------------------------------------------------------------------------- #
# C. Writes happen on real mutations
# --------------------------------------------------------------------------- #
def section_c() -> dict:
    print("\n-- C. a write happens on every mutation that changes meaning --")
    c, csrf = sign_in("owner")
    H = {"Origin": TEST_ORIGIN}
    seen: dict = {}

    # -- node create / update / status ------------------------------------- #
    r = c.post("/api/node", json={"id": "EV-OBJ-A", "type": "project",
                                  "name": "Event object A", "csrf_token": csrf}, headers=H)
    check("POST /api/node -> 201", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    evs = events_for("EV-OBJ-A", "node.create")
    check("...writes exactly one node.create", len(evs) == 1, f"{len(evs)}")
    if evs:
        check("...whose node_id defaults to the node itself (the access column "
              "is never left NULL for a node action)", evs[0].node_id == "EV-OBJ-A",
              repr(evs[0].node_id))
        seen["create"] = evs[0]

    c.post("/api/node", json={"id": "EV-OBJ-B", "type": "project",
                              "name": "Event object B", "csrf_token": csrf}, headers=H)

    r = c.patch("/api/node/EV-OBJ-A", json={"name": "Event object A, renamed",
                                            "summary": "s", "csrf_token": csrf}, headers=H)
    check("PATCH /api/node -> 200", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    evs = events_for("EV-OBJ-A", "node.update")
    check("...writes one node.update naming the CHANGED FIELDS and no values",
          len(evs) == 1 and evs[0].detail.get("fields") == ["name", "summary"],
          str(evs and evs[0].detail))

    r = c.post("/api/workitem", json={"type": "task", "name": "Event task",
                                      "status": "pending", "csrf_token": csrf}, headers=H)
    check("POST /api/workitem -> 201", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    task_id = r.json().get("id")
    seen["task_id"] = task_id
    check("...writes a node.create for the workitem", len(events_for(task_id, "node.create")) == 1)
    evs = events_for(task_id, "node.status")
    check("...and a node.status of its own, not folded into the create's detail — "
          "'when did this go in-flight' has to be findable by filtering on action",
          len(evs) == 1 and evs[0].detail == {"from": None, "to": "pending"},
          str(evs and evs[0].detail))

    r = c.post(f"/api/node/{task_id}/status", json={"status": "in_progress",
                                                    "csrf_token": csrf}, headers=H)
    check("POST /api/node/{id}/status -> 200", r.status_code == 200, f"{r.status_code}")
    evs = events_for(task_id, "node.status")
    check("...writes a second node.status carrying from AND to",
          len(evs) == 2 and evs[1].detail == {"from": "pending", "to": "in_progress"},
          str(evs[-1].detail if evs else None))

    # -- assignee ----------------------------------------------------------- #
    r = c.patch(f"/api/workitem/{task_id}", json={"assignee": AVERY_ID, "csrf_token": csrf},
                headers=H)
    check("PATCH /api/workitem assignee -> 200", r.status_code == 200,
          f"{r.status_code} {r.text[:160]}")
    evs = events_for(task_id, "node.assignee")
    check("...writes a node.assignee carrying from AND to",
          len(evs) == 1 and evs[0].detail == {"from": None, "to": AVERY_ID},
          str(evs and evs[0].detail))

    # -- edge create / delete ----------------------------------------------- #
    r = c.post("/api/edge", json={"src": "EV-OBJ-A", "dst": "EV-OBJ-B",
                                  "edge_type": "uses", "csrf_token": csrf}, headers=H)
    check("POST /api/edge -> 201", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    edge_id = r.json().get("id")
    evs = events_for(str(edge_id), "edge.create")
    check("...writes one edge.create", len(evs) == 1, f"{len(evs)}")
    if evs:
        check("...recording BOTH endpoints as governing nodes — an edge into a "
              "private node is a statement about the private node",
              (evs[0].node_id, evs[0].related_node_id) == ("EV-OBJ-A", "EV-OBJ-B"),
              f"{evs[0].node_id} -> {evs[0].related_node_id}")

    r = c.request("DELETE", f"/api/edge/{edge_id}", json={"csrf_token": csrf}, headers=H)
    check("DELETE /api/edge -> 200", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    evs = events_for(str(edge_id), "edge.delete")
    check("...writes an edge.delete that COPIES src/dst/edge_type — an edge id "
          "resolves to nothing once the row is gone",
          len(evs) == 1 and evs[0].detail.get("edge_type") == "uses"
          and evs[0].detail.get("src") == "EV-OBJ-A",
          str(evs and evs[0].detail))

    # -- node delete, and the cascade --------------------------------------- #
    c.post("/api/edge", json={"src": "EV-OBJ-A", "dst": "EV-OBJ-B",
                              "edge_type": "uses", "csrf_token": csrf}, headers=H)
    r = c.request("DELETE", "/api/node/EV-OBJ-B?cascade=1", json={"csrf_token": csrf}, headers=H)
    check("DELETE /api/node?cascade=1 -> 200", r.status_code == 200,
          f"{r.status_code} {r.text[:160]}")
    evs = events_for("EV-OBJ-B", "node.delete")
    check("...writes a node.delete", len(evs) == 1, f"{len(evs)}")
    with app.session() as s:
        casc = list(s.scalars(select(GraphEvent).where(
            GraphEvent.action == "edge.delete")).all())
    check("...and one edge.delete per cascaded edge, not a single line claiming "
          "a count — an edge that vanishes with no event is exactly what this "
          "table is for",
          any(e.detail and e.detail.get("cascade_of") == "EV-OBJ-B" for e in casc),
          str([e.detail for e in casc]))

    with app.session() as s:
        check("the delete event OUTLIVES its node — _delete_node_dependents must "
              "never clean this table, or the delete is the one act nobody can read",
              s.get(Node, "EV-OBJ-B") is None
              and s.scalar(select(GraphEvent).where(GraphEvent.entity_id == "EV-OBJ-B",
                                                    GraphEvent.action == "node.delete")) is not None)
    return seen


# --------------------------------------------------------------------------- #
# D. The actor is the authenticated principal
# --------------------------------------------------------------------------- #
def section_d() -> None:
    print("\n-- D. the actor is the PRINCIPAL, and the accountable person is "
          "resolved through the person -> bot link --")
    c, csrf = sign_in("owner")
    H = {"Origin": TEST_ORIGIN}

    # A browser session: the actor IS a person, so they are accountable.
    c.post("/api/node", json={"id": "EV-ACTOR-SESSION", "type": "project",
                              "name": "By a session", "csrf_token": csrf}, headers=H)
    evs = events_for("EV-ACTOR-SESSION", "node.create")
    if not evs:
        check("a session write was recorded at all", False, "no event")
    else:
        e = evs[0]
        check("actor_kind is 'session'", e.actor_kind == "session", e.actor_kind)
        check("actor_user_id is the signed-in account", e.actor_user_id == OWNER_ID,
              str(e.actor_user_id))
        check("actor_subject is copied at write time, so a deleted account still "
              "has a name in the log", e.actor_subject == "owner", str(e.actor_subject))
        check("actor_token_id is NULL for a browser", e.actor_token_id is None,
              str(e.actor_token_id))
        check("responsible_user_id is the person themselves", e.responsible_user_id == OWNER_ID,
              str(e.responsible_user_id))

    # An AGENT linked to a person (ADR 0034).
    raw = mint(AGENT_ID, "ev-agent")
    bot = bearer_client(raw)
    r = bot.post("/api/node", json={"id": "EV-ACTOR-AGENT", "type": "project",
                                    "name": "By an agent"})
    check("an agent's write succeeds (the section is about attribution, not refusal)",
          r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    evs = events_for("EV-ACTOR-AGENT", "node.create")
    if not evs:
        check("an agent write was recorded at all", False, "no event")
    else:
        e = evs[0]
        check("actor_kind is 'token'", e.actor_kind == "token", e.actor_kind)
        check("actor_user_id is the BOT's own account, not the person's — every "
              "service token belongs to a bot row (ADR 0034)",
              e.actor_user_id == AGENT_ID, str(e.actor_user_id))
        check("actor_token_id names WHICH credential", bool(e.actor_token_id),
              str(e.actor_token_id))
        check("responsible_user_id resolves through person_user_id to the HUMAN — "
              "this is the column that lets the log answer 'on whose authority?' "
              "rather than only 'which token?'",
              e.responsible_user_id == AVERY_ID, str(e.responsible_user_id))

    # An agent with NO person recorded: NULL, never itself.
    raw2 = mint(LONE_AGENT_ID, "ev-lone")
    lone = bearer_client(raw2)
    lone.post("/api/node", json={"id": "EV-ACTOR-LONE", "type": "project", "name": "Unlinked"})
    evs = events_for("EV-ACTOR-LONE", "node.create")
    if evs:
        check("an UNLINKED agent resolves to NULL, never back-filled with its own "
              "id — that would make a bot look like the human who authorised it",
              evs[0].responsible_user_id is None, str(evs[0].responsible_user_id))
        check("...while still naming the bot as the ACTOR",
              evs[0].actor_user_id == LONE_AGENT_ID, str(evs[0].actor_user_id))

    # Forgery. Extra body fields must change nothing.
    bot.post("/api/node", json={"id": "EV-ACTOR-FORGE", "type": "project",
                                "name": "Forged?", "actor": OWNER_ID,
                                "actor_user_id": OWNER_ID, "actor_kind": "session",
                                "responsible_user_id": OWNER_ID})
    evs = events_for("EV-ACTOR-FORGE", "node.create")
    if not evs:
        check("the forgery attempt was recorded at all", False, "no event")
    else:
        e = evs[0]
        check("a client-supplied actor is IGNORED — the identity comes off the "
              "principal and there is no parameter through which to try",
              (e.actor_user_id, e.actor_kind, e.responsible_user_id)
              == (AGENT_ID, "token", AVERY_ID),
              f"{e.actor_user_id} / {e.actor_kind} / {e.responsible_user_id}")


# --------------------------------------------------------------------------- #
# E. Access filtering — proven by attempting the violation
# --------------------------------------------------------------------------- #
def section_e() -> None:
    print("\n-- E. an event about a node you cannot see is neither READABLE nor "
          "COUNTABLE --")
    with app.session() as s:
        for nid in ("EV-PROJ-SHARED", "EV-PROJ-PRIVATE"):
            if s.get(Node, nid) is None:
                s.add(Node(id=nid, type="project", name=nid, attributes={},
                           attention_count=0, completion=0.0, origin="atlas"))
        s.flush()
        if s.get(NodeVisibility, "EV-PROJ-PRIVATE") is None:
            s.add(NodeVisibility(node_id="EV-PROJ-PRIVATE", visibility="private",
                                 set_by=OWNER_ID, set_at=datetime.now(timezone.utc)))
            s.add(NodeGrant(node_id="EV-PROJ-PRIVATE", user_id=BJORN_ID,
                            granted_by=OWNER_ID, granted_at=datetime.now(timezone.utc)))
        s.commit()

    avery, _ = sign_in("avery")
    bjorn, _ = sign_in("bjorn")
    owner, csrf = sign_in("owner")
    H = {"Origin": TEST_ORIGIN}

    before_avery = avery.get("/api/events").json()["total"]
    before_owner = owner.get("/api/events").json()["total"]

    # The owner now makes changes that are ENTIRELY about the private project:
    # a workitem bound to it (so the task follows what it works on), a status
    # change on that task, and an edge from the SHARED project into the private
    # one — the case that tests the AND across both governing columns.
    r = owner.post("/api/workitem", json={"type": "task", "name": "Private work",
                                          "binding": "EV-PROJ-PRIVATE",
                                          "status": "pending", "csrf_token": csrf}, headers=H)
    check("the owner can create work bound to the private project",
          r.status_code == 201, f"{r.status_code} {r.text[:200]}")
    private_task = r.json().get("id")
    owner.post(f"/api/node/{private_task}/status",
               json={"status": "in_progress", "csrf_token": csrf}, headers=H)
    r = owner.post("/api/edge", json={"src": "EV-PROJ-SHARED", "dst": "EV-PROJ-PRIVATE",
                                      "edge_type": "uses", "csrf_token": csrf}, headers=H)
    check("...and an edge from the shared project INTO the private one",
          r.status_code == 201, f"{r.status_code} {r.text[:200]}")
    mixed_edge_id = r.json().get("id")

    after_avery = avery.get("/api/events").json()
    after_owner = owner.get("/api/events").json()

    check("the OWNER's total rose — the events really were written, so the "
          "member's flat total below is a filter and not an empty table",
          after_owner["total"] > before_owner,
          f"{before_owner} -> {after_owner['total']}")
    check("THE MEMBER'S TOTAL DID NOT MOVE. A post-filter that strips rows from "
          "the page still leaks this number, which is how many changes were made "
          "to a project they cannot see",
          after_avery["total"] == before_avery,
          f"{before_avery} -> {after_avery['total']}")

    ids = [i["entity_id"] for i in after_avery["items"]]
    check("...and the private task's events are absent from the member's page",
          private_task not in ids, str(ids[:8]))
    check("...including the SHARED->PRIVATE edge: an event is visible only when "
          "EVERY node it names is visible (an AND, never an OR)",
          str(mixed_edge_id) not in ids, str(ids[:8]))

    r = avery.get(f"/api/events?node_id=EV-PROJ-PRIVATE")
    check("filtering by the private node returns an empty 200 — never a 403, "
          "which would confirm the node exists",
          r.status_code == 200 and r.json()["total"] == 0 and r.json()["count"] == 0,
          f"{r.status_code} total={r.json().get('total')}")

    r = avery.get(f"/api/events?entity_id={private_task}")
    check("...and so does filtering by the hidden task's own id",
          r.status_code == 200 and r.json()["total"] == 0, f"{r.json().get('total')}")

    r = bjorn.get("/api/events?node_id=EV-PROJ-PRIVATE")
    check("the GRANTED member DOES see them — two callers, one database, one "
          "instant, two correct answers", r.json()["total"] > 0, str(r.json()["total"]))

    r = owner.get(f"/api/events?entity_id={private_task}")
    check("and so does the owner", r.json()["total"] > 0, str(r.json()["total"]))

    # The deleted-node consequence, asserted rather than left to be discovered.
    owner.post("/api/node", json={"id": "EV-GONE", "type": "project",
                                  "name": "Soon gone", "csrf_token": csrf}, headers=H)
    owner.request("DELETE", "/api/node/EV-GONE", json={"csrf_token": csrf}, headers=H)
    r = owner.get("/api/events?entity_id=EV-GONE")
    check("an owner reads events about a DELETED node (unrestricted)",
          r.json()["total"] >= 2, str(r.json()["total"]))
    r = avery.get("/api/events?entity_id=EV-GONE")
    check("a member does NOT — a deleted id is in no computed visible set, which "
          "is the fail-closed direction and is documented on delete_node",
          r.json()["total"] == 0, str(r.json()["total"]))


# --------------------------------------------------------------------------- #
# F. The read API
# --------------------------------------------------------------------------- #
def section_f() -> None:
    print("\n-- F. the read API: paging honesty, filters, and a refused time bound --")
    owner, csrf = sign_in("owner")
    H = {"Origin": TEST_ORIGIN}

    # A corpus LARGER than the cap, on purpose. verify_assignment.py §G exists
    # because a suite passed while a view rendered 200 of 372 rows labelled
    # "200 items"; a cap that is never hit is a cap that is never tested.
    owner.post("/api/node", json={"id": "EV-BULK", "type": "project",
                                  "name": "Bulk", "csrf_token": csrf}, headers=H)
    wanted = app.EVENT_LIST_MAX_PAGE_SIZE + 5
    have = owner.get("/api/events?entity_id=EV-BULK").json()["total"]
    for i in range(have, wanted):
        owner.patch("/api/node/EV-BULK", json={"name": f"Bulk {i}", "csrf_token": csrf},
                    headers=H)

    r = owner.get(f"/api/events?entity_id=EV-BULK&page_size=100000")
    body = r.json()
    check("page_size is CLAMPED to the documented maximum, so a caller cannot "
          "force an unbounded response", body["page_size"] == app.EVENT_LIST_MAX_PAGE_SIZE,
          str(body["page_size"]))
    check("`count` is the rows ACTUALLY RETURNED and `total` is the rows that "
          "match — the pair verify_assignment §G exists because of",
          body["count"] == len(body["items"]) and body["total"] >= wanted
          and body["count"] < body["total"],
          f"count={body['count']} total={body['total']} items={len(body['items'])}")
    check("...and `max_page_size` is reported, so a client can page correctly "
          "without guessing", body["max_page_size"] == app.EVENT_LIST_MAX_PAGE_SIZE)

    page1 = owner.get("/api/events?entity_id=EV-BULK&page_size=10&page=1").json()
    page2 = owner.get("/api/events?entity_id=EV-BULK&page_size=10&page=2").json()
    check("pages do not overlap — several events from ONE request share a "
          "timestamp, so the order has to break the tie on id",
          not (set(i["id"] for i in page1["items"]) & set(i["id"] for i in page2["items"])))
    check("newest first", [i["id"] for i in page1["items"]]
          == sorted((i["id"] for i in page1["items"]), reverse=True))

    check("the entity_id filter returns ONLY that entity",
          all(i["entity_id"] == "EV-BULK" for i in page1["items"]),
          str({i["entity_id"] for i in page1["items"]}))

    r = owner.get("/api/events?action=node.status")
    check("the action filter narrows to one verb",
          r.json()["total"] > 0 and all(i["action"] == "node.status" for i in r.json()["items"]),
          str({i["action"] for i in r.json()["items"]}))

    # Time bounds.
    now = datetime.now(timezone.utc)
    future = (now + timedelta(days=1)).isoformat()
    past = (now - timedelta(days=1)).isoformat()
    # `params=` and NOT an f-string into the path: an ISO timestamp ends
    # `+00:00`, and a raw `+` in a query string decodes to a SPACE. The first
    # draft of this section failed for exactly that reason — a harness bug that
    # presents identically to a server bug.
    check("?since= in the future matches nothing",
          owner.get("/api/events", params={"since": future}).json()["total"] == 0)
    check("?since= in the past matches everything the caller can see",
          owner.get("/api/events", params={"since": past}).json()["total"]
          == owner.get("/api/events").json()["total"])
    check("?until= in the past matches nothing",
          owner.get("/api/events", params={"until": past}).json()["total"] == 0)
    check("a naive timestamp is read as UTC rather than refused — every stamp "
          "this app stores is tz-aware UTC, so there is one sensible reading",
          owner.get("/api/events", params={"since": "2020-01-01T00:00:00"}).status_code == 200)
    check("...and a trailing `Z` is accepted as well as `+00:00`",
          owner.get("/api/events", params={"since": "2020-01-01T00:00:00Z"}).status_code == 200)

    r = owner.get("/api/events?since=not-a-timestamp")
    check("a MALFORMED time bound is a 400 NAMING THE FIELD, never silently "
          "ignored — a bound that quietly does nothing returns a superset of "
          "what was asked for",
          r.status_code == 400 and r.json().get("field") == "since",
          f"{r.status_code} {r.text[:160]}")

    # Resolution.
    r = owner.get("/api/events?entity_id=EV-BULK&page_size=1")
    item = r.json()["items"][0]
    check("an item resolves its actor to {id, username, name} — the same shape "
          "/api/people and every node payload use",
          item["actor"]["account"] and item["actor"]["account"]["username"] == "owner",
          str(item["actor"]))
    check("...and carries the raw ids too, because the account may be gone",
          item["actor"]["user_id"] == OWNER_ID and item["responsible_user_id"] == OWNER_ID,
          str(item["responsible_user_id"]))

    r = owner.get("/api/events?responsible=me&page_size=1")
    check("?responsible=me resolves against the calling principal",
          r.json()["total"] > 0 and r.json()["items"][0]["responsible_user_id"] == OWNER_ID)

    # `detail` resolution: a node.assignee event's from/to are account ids and
    # get the same {id, username, name} treatment as actor/responsible; a
    # node.status event's from/to are status strings and must come back
    # UNTOUCHED — the negative case this check exists for, because both
    # actions share the identical {"from", "to"} shape and a resolver that
    # doesn't check `action` first would corrupt every status row.
    owner.post("/api/workitem", json={"type": "task", "name": "Detail resolution task",
                                      "status": "pending", "csrf_token": csrf}, headers=H)
    r = owner.get("/api/events?action=node.create&entity_type=node&page_size=1")
    detail_task = r.json()["items"][0]["entity_id"]
    owner.patch(f"/api/workitem/{detail_task}", json={"assignee": AVERY_ID, "csrf_token": csrf},
                headers=H)
    owner.post(f"/api/node/{detail_task}/status", json={"status": "in_progress",
                                                         "csrf_token": csrf}, headers=H)

    r = owner.get(f"/api/events?entity_id={detail_task}&action=node.assignee")
    items = r.json()["items"]
    check("a node.assignee event's detail.to is RESOLVED to a person object, "
          "not left as a raw auth_user id — this is what the History tab "
          "reads to show a name instead of u-ev-avery",
          len(items) == 1 and items[0]["detail"]["from"] is None
          and items[0]["detail"]["to"] == {"id": AVERY_ID, "username": "avery", "name": "Avery"},
          str(items[0]["detail"] if items else None))

    r = owner.get(f"/api/events?entity_id={detail_task}&action=node.status")
    items = r.json()["items"]
    check("a node.status event's detail.from/to are STATUS STRINGS, left "
          "exactly as written — the negative case for the resolver above",
          any(i["detail"] == {"from": "pending", "to": "in_progress"} for i in items),
          str([i["detail"] for i in items]))


# --------------------------------------------------------------------------- #
# G. The routes that could change meaning without leaving a trace
# --------------------------------------------------------------------------- #
DOC_PATH = "docs/nodes/ev-cover/notes.md"


def section_g() -> None:
    print("\n-- G. status through the GENERIC route, and the sub-records a node "
          "owns --")
    owner, csrf = sign_in("owner")
    H = {"Origin": TEST_ORIGIN}

    # -- a status the caller spelled as an ATTRIBUTE ------------------------ #
    r = owner.post("/api/node", json={"id": "EV-GEN-TASK", "type": "task",
                                      "name": "Filed through the generic route",
                                      "attributes": {"status": "pending"},
                                      "csrf_token": csrf}, headers=H)
    check("POST /api/node carrying a status inside `attributes` -> 201",
          r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    evs = events_for("EV-GEN-TASK", "node.status")
    check("...writes a node.status. This route has no `status` FIELD — the "
          "status arrives inside the attributes dict — so a node created "
          "already in-flight left nothing for ?action=node.status to find",
          len(evs) == 1 and evs[0].detail == {"from": None, "to": "pending"},
          str(evs and evs[0].detail))

    r = owner.patch("/api/node/EV-GEN-TASK",
                    json={"attributes": {"status": "done"}, "csrf_token": csrf}, headers=H)
    check("PATCH /api/node moving that status -> 200", r.status_code == 200,
          f"{r.status_code} {r.text[:160]}")
    evs = events_for("EV-GEN-TASK", "node.status")
    check("...writes a second node.status carrying from AND to — the SAME detail "
          "shape POST /api/node/{id}/status records, so a reader does not have "
          "to know which route made the change",
          len(evs) == 2 and evs[1].detail == {"from": "pending", "to": "done"},
          str(evs[-1].detail if evs else None))

    r = owner.get("/api/events", params={"action": "node.status", "entity_id": "EV-GEN-TASK"})
    check("GET /api/events?action=node.status finds both, which is the acceptance "
          "criterion stated as a query", r.json()["total"] == 2, str(r.json()["total"]))

    # ...and the no-op case, which is where an over-eager fix goes wrong.
    before = len(events_for("EV-GEN-TASK", "node.status"))
    owner.patch("/api/node/EV-GEN-TASK", json={"name": "Renamed only",
                                               "csrf_token": csrf}, headers=H)
    owner.patch("/api/node/EV-GEN-TASK", json={"attributes": {"status": "done"},
                                               "csrf_token": csrf}, headers=H)
    check("a PATCH that does not MOVE the status writes NO status event — neither "
          "one that omits `attributes` nor one that carries the value it already "
          "has. A log of no-ops buries the acts that mattered",
          len(events_for("EV-GEN-TASK", "node.status")) == before,
          f"{before} -> {len(events_for('EV-GEN-TASK', 'node.status'))}")

    # The same hole, one route over.
    r = owner.post("/api/workitem", json={"type": "task", "name": "Attr-status work",
                                          "status": "pending", "csrf_token": csrf}, headers=H)
    wid = r.json().get("id")
    owner.patch(f"/api/workitem/{wid}", json={"attributes": {"status": "analyze"},
                                              "csrf_token": csrf}, headers=H)
    evs = events_for(wid, "node.status")
    check("PATCH /api/workitem with the status inside `attributes` — the same "
          "hole one door over, because WorkitemPatch carries a dict too — is "
          "recorded as well",
          len(evs) == 2 and evs[1].detail == {"from": "pending", "to": "analyze"},
          str(evs[-1].detail if evs else None))

    # -- the assignee, and why there is no event for it HERE ----------------- #
    check("the generic node route CANNOT set an assignee: `assignee` is a field "
          "of WorkitemWrite/WorkitemPatch and of neither NodeWrite nor NodePatch, "
          "so there is no unrecorded assignee change to catch here. This is "
          "asserted rather than assumed — adding the field without wiring the "
          "event turns THIS check red",
          "assignee" not in app.NodeWrite.model_fields
          and "assignee" not in app.NodePatch.model_fields,
          f"NodeWrite: {sorted(app.NodeWrite.model_fields)}")

    # -- node documents ------------------------------------------------------ #
    owner.post("/api/node", json={"id": "EV-COVER", "type": "project",
                                  "name": "Cover", "csrf_token": csrf}, headers=H)
    r = owner.put("/api/node/EV-COVER/doc",
                  json={"path": DOC_PATH, "content": "hello", "csrf_token": csrf}, headers=H)
    check("PUT /api/node/{id}/doc -> 201", r.status_code == 201,
          f"{r.status_code} {r.text[:200]}")
    evs = events_for("EV-COVER", "node.doc.write")
    check("...writes a node.doc.write naming the OWNING NODE — as entity_id AND "
          "as node_id, which is the column the access filter reads",
          len(evs) == 1 and evs[0].node_id == "EV-COVER"
          and evs[0].entity_type == "node", str(evs and (evs[0].entity_id, evs[0].node_id)))
    check("...carrying the path and `created`, and the SIZE rather than the "
          "CONTENT — this table says a document changed, never what it said",
          bool(evs) and evs[0].detail.get("path") == DOC_PATH
          and evs[0].detail.get("created") is True
          and evs[0].detail.get("bytes") == 5
          and "content" not in evs[0].detail,
          str(evs and evs[0].detail))

    owner.put("/api/node/EV-COVER/doc",
              json={"path": DOC_PATH, "content": "hello again", "csrf_token": csrf}, headers=H)
    evs = events_for("EV-COVER", "node.doc.write")
    check("an UPSERT over an existing document is its own event, marked "
          "created=False — 'who overwrote this' is the question the route's "
          "upsert shape makes askable",
          len(evs) == 2 and evs[1].detail.get("created") is False,
          str(evs[-1].detail if evs else None))

    r = owner.request("DELETE", f"/api/node/EV-COVER/doc?path={DOC_PATH}",
                      json={"csrf_token": csrf}, headers=H)
    check("DELETE /api/node/{id}/doc -> 200", r.status_code == 200,
          f"{r.status_code} {r.text[:200]}")
    evs = events_for("EV-COVER", "node.doc.delete")
    check("...writes a node.doc.delete sized from the row while it was still "
          "readable — the deletion is the event a reader most wants sized",
          len(evs) == 1 and evs[0].detail.get("path") == DOC_PATH
          and evs[0].detail.get("bytes") == len("hello again"),
          str(evs and evs[0].detail))

    # -- node intents -------------------------------------------------------- #
    r = owner.post("/api/node/EV-COVER/intent",
                   json={"slug": "ev-purpose", "kind": "principle", "title": "A purpose",
                         "statement": "It holds.", "csrf_token": csrf}, headers=H)
    check("POST /api/node/{id}/intent -> 201", r.status_code == 201,
          f"{r.status_code} {r.text[:200]}")
    intent_id = r.json().get("id")
    evs = events_for("EV-COVER", "node.intent.create")
    check("...writes a node.intent.create against the OWNING NODE, with the "
          "intent's own id in `detail` where it is a pointer rather than the "
          "thing the access filter reads",
          len(evs) == 1 and evs[0].node_id == "EV-COVER"
          and evs[0].detail.get("intent_id") == intent_id
          and evs[0].detail.get("slug") == "ev-purpose",
          str(evs and evs[0].detail))

    r = owner.patch(f"/api/intent/{intent_id}",
                    json={"title": "A sharper purpose", "csrf_token": csrf}, headers=H)
    check("PATCH /api/intent/{id} -> 200", r.status_code == 200,
          f"{r.status_code} {r.text[:200]}")
    evs = events_for("EV-COVER", "node.intent.update")
    check("...resolves the intent to its NODE first — an intent id names no node "
          "by itself, so an event recorded against the id alone would be "
          "unfilterable — and records FIELD NAMES, not the statement's prose",
          len(evs) == 1 and evs[0].node_id == "EV-COVER"
          and evs[0].detail.get("fields") == ["title"]
          and "statement" not in evs[0].detail,
          str(evs and evs[0].detail))

    owner.patch(f"/api/intent/{intent_id}", json={"csrf_token": csrf}, headers=H)
    check("a PATCH that applies nothing writes nothing — same rule as the "
          "unchanged status above",
          len(events_for("EV-COVER", "node.intent.update")) == 1,
          str(len(events_for("EV-COVER", "node.intent.update"))))

    r = owner.request("DELETE", f"/api/intent/{intent_id}",
                      json={"csrf_token": csrf}, headers=H)
    check("DELETE /api/intent/{id} -> 200", r.status_code == 200,
          f"{r.status_code} {r.text[:200]}")
    evs = events_for("EV-COVER", "node.intent.delete")
    check("...writes a node.intent.delete carrying slug and kind, captured while "
          "the row was still readable — the integer id resolves to nothing "
          "afterwards, exactly like a deleted edge's",
          len(evs) == 1 and evs[0].detail.get("slug") == "ev-purpose"
          and evs[0].detail.get("kind") == "principle",
          str(evs and evs[0].detail))

    # -- and every one of the above is bounded by the access filter ---------- #
    # §E's method, applied to the newly-wired routes: the violation is ATTEMPTED
    # rather than the happy path asserted. A member with no grant must not be
    # able to COUNT a document written on a project they cannot see, which is
    # the failure a post-filter over the page would sail straight through.
    with app.session() as s:
        if s.get(Node, "EV-G-PRIVATE") is None:
            s.add(Node(id="EV-G-PRIVATE", type="project", name="Hidden cover",
                       attributes={}, attention_count=0, completion=0.0, origin="atlas"))
            s.flush()
        if s.get(NodeVisibility, "EV-G-PRIVATE") is None:
            s.add(NodeVisibility(node_id="EV-G-PRIVATE", visibility="private",
                                 set_by=OWNER_ID, set_at=datetime.now(timezone.utc)))
            s.add(NodeGrant(node_id="EV-G-PRIVATE", user_id=BJORN_ID,
                            granted_by=OWNER_ID, granted_at=datetime.now(timezone.utc)))
        s.commit()

    avery, _ = sign_in("avery")
    bjorn, _ = sign_in("bjorn")
    before_avery = avery.get("/api/events").json()["total"]
    before_owner = owner.get("/api/events").json()["total"]

    hidden_doc = "docs/nodes/ev-g-private/secret.md"
    owner.put("/api/node/EV-G-PRIVATE/doc",
              json={"path": hidden_doc, "content": "not for avery",
                    "csrf_token": csrf}, headers=H)
    r = owner.post("/api/node/EV-G-PRIVATE/intent",
                   json={"slug": "hidden-purpose", "kind": "constraint", "title": "Hidden",
                         "statement": "Also not for avery.", "csrf_token": csrf}, headers=H)
    hidden_intent = r.json().get("id")
    owner.patch(f"/api/intent/{hidden_intent}",
                json={"title": "Hidden, sharpened", "csrf_token": csrf}, headers=H)
    owner.request("DELETE", f"/api/intent/{hidden_intent}",
                  json={"csrf_token": csrf}, headers=H)
    owner.request("DELETE", f"/api/node/EV-G-PRIVATE/doc?path={hidden_doc}",
                  json={"csrf_token": csrf}, headers=H)
    # ...and a status moved through the generic route on work bound to it, so the
    # first half of this section is covered by the filter too.
    r = owner.post("/api/workitem", json={"type": "task", "name": "Hidden work",
                                          "binding": "EV-G-PRIVATE", "status": "pending",
                                          "csrf_token": csrf}, headers=H)
    hidden_task = r.json().get("id")
    owner.patch(f"/api/node/{hidden_task}", json={"attributes": {"status": "done"},
                                                  "csrf_token": csrf}, headers=H)

    after_avery = avery.get("/api/events").json()
    after_owner = owner.get("/api/events").json()

    check("the OWNER's total rose across those five new event kinds — so the "
          "member's flat total below is a filter and not an empty table",
          after_owner["total"] > before_owner,
          f"{before_owner} -> {after_owner['total']}")
    check("THE MEMBER'S TOTAL DID NOT MOVE. A doc or an intent event that named "
          "no node would be readable by everyone; naming the owning node is what "
          "puts it under the existing WHERE clause",
          after_avery["total"] == before_avery,
          f"{before_avery} -> {after_avery['total']}")

    for act in ("node.doc.write", "node.doc.delete", "node.intent.create",
                "node.intent.update", "node.intent.delete"):
        r = avery.get("/api/events", params={"node_id": "EV-G-PRIVATE", "action": act})
        check(f"a member filtering ?action={act} on the private node gets an empty "
              f"200 — never a 403, which would confirm the node exists",
              r.status_code == 200 and r.json()["total"] == 0,
              f"{r.status_code} total={r.json().get('total')}")

    r = bjorn.get("/api/events", params={"node_id": "EV-G-PRIVATE"})
    check("the GRANTED member sees every one of them — two callers, one database, "
          "one instant, two correct answers", r.json()["total"] >= 5, str(r.json()["total"]))

    r = avery.get("/api/events", params={"entity_id": hidden_task, "action": "node.status"})
    check("...and the status moved through the GENERIC route on work bound to the "
          "private project is invisible too — a workitem follows what it works on",
          r.json()["total"] == 0, str(r.json()["total"]))


if __name__ == "__main__":
    app.startup()

    mk_user(OWNER_ID, "owner", role="owner")
    mk_user(AVERY_ID, "avery")
    mk_user(BJORN_ID, "bjorn")
    mk_user(AGENT_ID, "bot-ev-agent", is_service=True, person=AVERY_ID)
    mk_user(LONE_AGENT_ID, "bot-ev-lone", is_service=True, person=None)

    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    section_f()
    section_g()

    print()
    if failures:
        print(f"FAILED — {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")
