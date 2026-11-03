"""
Verification for the audit-trail reader: a route over `auth_event` (which had a
writer since ADR 0025 and no reader at all — the only way to read it back was
`psql`), an `actor_kind=agent|person|system` filter on `GET /api/events`, and a
write-time CHECK on `GraphEvent.action` that `GRAPH_EVENT_ACTIONS` had never
actually enforced.

No pytest suite exists in this repo (see `verify_object_graph.py`'s docstring
for why); this follows the same standalone, run-it-and-read-it convention as
its siblings, against a real disposable Postgres via `testdb` (TASK-1044).

FOUR SECTIONS:

  A. `GET /api/auth-events` — OWNER-ONLY, proven by attempting the refusal
     rather than reading the code: no credential (401, before the route even
     runs), a member session (403), and — the case this suite exists to catch
     — a Bearer AGENT TOKEN MINTED WITH `admin` SCOPE (403). An agent's own
     role is always `member` (ADR 0034; `Principal.is_owner` reads the
     principal's OWN row, never a linked person's), so an admin-scoped token
     is refused on the role axis and never reaches the scope axis at all.
     Then the happy path: an owner session gets 200 and the expected shape.

  B. ROUTE COVERAGE. `verify_node_access.py` §F enumerates the live routes
     and fails on any `/api` route with no `COVERAGE` entry — this section
     asserts, by reading that file's source, that the new route has one. The
     real enforcement is §F itself, which runs unmodified as its own script in
     this suite; this section exists so a regression in the coverage entry is
     caught here too, by name, rather than only as an unlabelled §F failure in
     a different file.

  C. `GET /api/events?actor_kind=`. **This section used to assert a FALSE
     invariant** — that `agent` ∪ `person` equals the unfiltered set — which
     is only true while no `system`-actor row (a principal-less write: a
     migration, a REPL, a startup path) exists in the window. It passed by
     accident of the fixture: nothing in it ever created one. Repaired here
     (this branch, 2026-08-24) by making `system` a real, filterable third
     partition — see `app._EVENT_ACTOR_KIND_FILTERS` for the reachability
     decision and why the mapping is exhaustive over `GraphEvent.actor_kind`'s
     written domain BY CONSTRUCTION rather than by luck. The real acceptance
     criterion, restated correctly: `agent`, `person` and `system` are
     pairwise DISJOINT and TOGETHER equal the unfiltered set. Proven by
     generating one event from a real signed-in session, one from a real
     Bearer service token, AND one from a real principal-less write (the
     `record_graph_event` call site itself, with no principal on the
     ContextVar — the same shape a migration or a REPL produces; there is no
     HTTP route that triggers one, by design: every request that reaches a
     write route has already passed the gate) — comparing four real HTTP
     responses, never asserted from the ORM layer, because the claim is about
     what the ROUTE partitions, not about the column it reads. This section
     also proves the OLD two-partition claim is false in the system row's
     presence, rather than merely retiring it quietly. `actor_kind=bogus` is
     a 400 naming the field, same shape as the existing `since`/`until`
     refusal this route already had, and its vocabulary is checked against
     `_EVENT_ACTOR_KIND_FILTERS` rather than a hardcoded list.

  D. `record_graph_event` validates `action` against `GRAPH_EVENT_ACTIONS`.
     Two halves: every EXISTING call site in `app.py` is scanned (not
     sampled) and asserted to pass a literal already in the vocabulary — the
     acceptance criterion says "check them all", so this is a static sweep of
     every `record_graph_event(s, "...")` call, not a handful driven over
     HTTP. Then the negative case: calling the writer directly with an action
     outside the vocabulary raises `ValueError` and leaves no row behind. A
     real mutating route (`POST /api/node`) is driven once more to prove the
     new raise does not disturb an ordinary, valid write.

Run (from a venv with the backend requirements installed):
    python verify_auth_events.py
"""
from __future__ import annotations

import base64
import os
import re
import sys
from datetime import datetime, timezone

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

from sqlalchemy import select  # noqa: E402

import app  # noqa: E402
import auth  # noqa: E402
import models_graph  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthUser  # noqa: E402
from models_graph import GraphEvent  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


HERE = os.path.dirname(os.path.abspath(__file__))

OWNER_ID = "u-ae-owner"
MEMBER_ID = "u-ae-member"
AGENT_ID = "u-ae-agent"

PW = "Correct-Horse-Battery-AuthEvents-9!"


def mk_user(uid: str, username: str, role: str = "member", is_service: bool = False) -> None:
    with app.session() as s:
        if s.get(AuthUser, uid) is None:
            s.add(AuthUser(
                id=uid, subject=username, name=username.title(), role=role,
                is_service=is_service, must_change_password=False,
                password_hash=None if is_service else passwords.hash_password(PW),
                created_at=datetime.now(timezone.utc)))
            s.commit()


def sign_in(username: str):
    """A real signed-in client, cookie and CSRF token both — the middleware's
    principal, not a hand-built one. Same idiom as `verify_graph_event.py`."""
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


def anon_client():
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN)


def mint(owner_id: str, scopes: list[str], label: str) -> str:
    with app.session() as s:
        raw, _row = auth.mint_service_token(
            s, owner=s.get(AuthUser, owner_id), scopes=scopes, label=label)
        s.commit()
    return raw


# --------------------------------------------------------------------------- #
# A. GET /api/auth-events — owner-only, proven by attempting the refusal
# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. GET /api/auth-events: owner-only, over the real gate --")

    anon = anon_client()
    r = anon.get("/api/auth-events")
    check("no credential at all -> 401 (the deny-by-default gate, before the "
          "route runs)", r.status_code == 401, f"got {r.status_code}")

    member, _member_csrf = sign_in("ae-member")
    r = member.get("/api/auth-events")
    check("a signed-in MEMBER -> 403, not 404 and not 200",
          r.status_code == 403, f"got {r.status_code} {r.text[:160]}")

    # THE CASE THIS SUITE EXISTS TO CATCH: a Bearer token minted with the
    # WIDEST scope there is. If this passed, the route would be gated on scope
    # rather than on role, and any admin-scoped agent token — including one
    # minted for ordinary write work — could read every account's login
    # history. It must still 403, because a bot's OWN role is always `member`
    # (ADR 0034: `Principal.is_owner` reads the principal's own row, never a
    # linked person's).
    admin_token = mint(AGENT_ID, ["read", "write", "admin"], "ae-admin-agent")
    agent = bearer_client(admin_token)
    r = agent.get("/api/auth-events")
    check("a Bearer token minted with admin scope -> STILL 403 — scope is not "
          "the axis this route is gated on", r.status_code == 403,
          f"got {r.status_code} {r.text[:160]}")
    me = agent.get("/api/me")
    check("...and that token really does carry admin scope, so the refusal "
          "above is not an accident of an under-scoped fixture",
          me.status_code == 200 and "admin" in (me.json().get("scopes") or []),
          str(me.json()))

    owner, _owner_csrf = sign_in("owner")
    r = owner.get("/api/auth-events")
    check("an OWNER session -> 200", r.status_code == 200,
          f"got {r.status_code} {r.text[:160]}")
    body = r.json() if r.status_code == 200 else {}
    for field in ("total", "count", "page", "page_size", "filters", "max_page_size", "items"):
        check(f"...response carries {field!r}", field in body, str(sorted(body)))
    # Real rows: the logins performed above (member, owner) each wrote
    # `login_ok`, so a route that returned an empty shell would still pass the
    # field check above and fail this one.
    check("...and it actually served rows, not an empty shell",
          isinstance(body.get("items"), list) and len(body["items"]) > 0
          and body.get("total", 0) > 0,
          f"total={body.get('total')} count={body.get('count')}")
    check("...each row carries the AuthEvent shape",
          all({"id", "at", "event", "user_id", "actor", "ip", "user_agent",
               "reason", "detail"} <= set(item) for item in body.get("items", [])),
          str(body.get("items", [{}])[0].keys() if body.get("items") else None))

    # Filter by event name, and require it actually narrowed the result.
    r = owner.get("/api/auth-events", params={"event": "login_ok"})
    check("?event=login_ok narrows to only login_ok rows",
          r.status_code == 200 and r.json()["total"] > 0
          and all(i["event"] == "login_ok" for i in r.json()["items"]),
          str(r.json() if r.status_code == 200 else r.text[:160]))

    # A malformed time bound is a 400 naming the field, same shape the
    # sibling route (`GET /api/events`) already uses for the identical helper.
    r = owner.get("/api/auth-events", params={"since": "not-a-timestamp"})
    check("a malformed ?since= is a 400 naming the field",
          r.status_code == 400 and r.json().get("field") == "since",
          f"{r.status_code} {r.text[:160]}")


# --------------------------------------------------------------------------- #
# B. Route coverage — the entry exists where §F looks for it
# --------------------------------------------------------------------------- #
def section_b() -> None:
    print("\n-- B. verify_node_access.py §F classifies the new route --")
    path = os.path.join(HERE, "verify_node_access.py")
    src = open(path, encoding="utf-8").read()
    found = bool(re.search(r'\(\s*"GET",\s*"/api/auth-events"\s*\)', src))
    check("verify_node_access.py's COVERAGE table has a "
          '("GET", "/api/auth-events") entry',
          found, "found" if found else "MISSING from verify_node_access.py's COVERAGE dict")


# --------------------------------------------------------------------------- #
# C. GET /api/events?actor_kind= — three-way partition, disjoint, and
# together the unfiltered set. See this file's header for why the OLD
# two-partition version of this section was a false invariant.
# --------------------------------------------------------------------------- #
def section_c() -> None:
    print("\n-- C. ?actor_kind=agent|person|system on GET /api/events --")
    owner, owner_csrf = sign_in("owner")
    write_token = mint(AGENT_ID, ["write"], "ae-write-agent")
    agent = bearer_client(write_token)

    since_ts = datetime.now(timezone.utc).isoformat()

    r = owner.post("/api/node", json={"id": "EV-AE-PERSON", "type": "project",
                                      "name": "auth-events person node",
                                      "csrf_token": owner_csrf},
                   headers={"Origin": TEST_ORIGIN})
    check("setup: the person-session node was created", r.status_code == 201,
          f"{r.status_code} {r.text[:160]}")

    r = agent.post("/api/node", json={"id": "EV-AE-AGENT", "type": "project",
                                      "name": "auth-events agent node"})
    check("setup: the agent-token node was created", r.status_code == 201,
          f"{r.status_code} {r.text[:160]}")

    # SETUP: a real principal-less write — the case this whole section is
    # about. There is no HTTP route that produces one (every request that
    # reaches a write route has already passed the gate and carries a
    # principal), so this calls the one writer directly with
    # `auth.current_principal` explicitly cleared — the same shape a
    # migration or a REPL session produces. Explicit set-then-reset, not
    # reliance on the ContextVar's `default=None`, so this is legible as
    # "deliberately no principal" rather than "forgot to sign in", and so a
    # future default change cannot silently turn this into a different case.
    tok = auth.current_principal.set(None)
    try:
        with app.session() as s:
            app.record_graph_event(s, "node.update", entity_type="node",
                                   entity_id="EV-AE-SYSTEM")
            s.commit()
    finally:
        auth.current_principal.reset(tok)

    unfiltered = owner.get("/api/events", params={"since": since_ts}).json()
    agent_only = owner.get("/api/events", params={"since": since_ts, "actor_kind": "agent"}).json()
    person_only = owner.get("/api/events", params={"since": since_ts, "actor_kind": "person"}).json()
    system_only = owner.get("/api/events", params={"since": since_ts, "actor_kind": "system"}).json()

    all_ids = {i["id"] for i in unfiltered["items"]}
    agent_ids = {i["id"] for i in agent_only["items"]}
    person_ids = {i["id"] for i in person_only["items"]}
    system_ids = {i["id"] for i in system_only["items"]}

    check("agent-only partition is non-empty", len(agent_ids) > 0, str(agent_ids))
    check("person-only partition is non-empty", len(person_ids) > 0, str(person_ids))
    check("system-only partition is non-empty", len(system_ids) > 0, str(system_ids))

    # THE INVARIANT THIS SECTION USED TO ASSERT, PROVEN FALSE. Before this
    # repair, "agent ∪ person == unfiltered" passed only because nothing in
    # the fixture ever produced a system-actor row. Now that one exists in
    # the window, the two-partition union must fall short of the unfiltered
    # set — if it did not, `actor_kind=system` would be returning rows that
    # also satisfy `agent` or `person`, which is its own bug.
    check("the OLD two-partition claim (agent union person == unfiltered) "
          "is FALSE now that a system-actor row exists -- this is why it "
          "was retired, not merely a style change",
          (agent_ids | person_ids) != all_ids,
          f"agent|person={agent_ids | person_ids} all={all_ids} -- these "
          "should NOT be equal")

    check("the THREE partitions are pairwise DISJOINT",
          not (agent_ids & person_ids) and not (agent_ids & system_ids)
          and not (person_ids & system_ids),
          f"agent&person={agent_ids & person_ids} "
          f"agent&system={agent_ids & system_ids} "
          f"person&system={person_ids & system_ids}")
    check("...and TOGETHER equal the unfiltered set",
          (agent_ids | person_ids | system_ids) == all_ids,
          f"union={agent_ids | person_ids | system_ids} all={all_ids}")
    check("`total` agrees with the item-set arithmetic, not just the page",
          unfiltered["total"] == agent_only["total"] + person_only["total"] + system_only["total"],
          f"unfiltered={unfiltered['total']} agent={agent_only['total']} "
          f"person={person_only['total']} system={system_only['total']}")

    # Each event created above must land in the partition its credential kind
    # implies — not merely "the counts add up", which a bug that mis-routed
    # events into the wrong bucket could still satisfy by accident if the
    # OTHER buckets happened to balance out.
    person_evt = next((i for i in unfiltered["items"] if i["entity_id"] == "EV-AE-PERSON"), None)
    agent_evt = next((i for i in unfiltered["items"] if i["entity_id"] == "EV-AE-AGENT"), None)
    system_evt = next((i for i in unfiltered["items"] if i["entity_id"] == "EV-AE-SYSTEM"), None)
    check("the session-created node's event is in the PERSON partition, "
          "and ONLY that one",
          person_evt is not None and person_evt["id"] in person_ids
          and person_evt["id"] not in agent_ids and person_evt["id"] not in system_ids,
          str(person_evt))
    check("the token-created node's event is in the AGENT partition, "
          "and ONLY that one",
          agent_evt is not None and agent_evt["id"] in agent_ids
          and agent_evt["id"] not in person_ids and agent_evt["id"] not in system_ids,
          str(agent_evt))
    check("the principal-less write's event is in the SYSTEM partition, "
          "and ONLY that one",
          system_evt is not None and system_evt["id"] in system_ids
          and system_evt["id"] not in agent_ids and system_evt["id"] not in person_ids,
          str(system_evt))
    check("the system event's actor carries no account — nobody signed in did it",
          system_evt is not None and system_evt["actor"]["kind"] == "system"
          and system_evt["actor"]["user_id"] is None
          and system_evt["actor"]["account"] is None
          and system_evt["responsible_user_id"] is None,
          str(system_evt.get("actor") if system_evt else None))

    r = owner.get("/api/events", params={"actor_kind": "bogus"})
    check("an unrecognised ?actor_kind= is a 400 naming the field",
          r.status_code == 400 and r.json().get("field") == "actor_kind",
          f"{r.status_code} {r.text[:160]}")
    # DERIVED, not pinned: the refusal's vocabulary must be read out of the
    # same dict the filter itself uses, or the two can drift independently —
    # exactly the "vocabulary narrower than reality" shape this section
    # exists to catch, one level up.
    check("...and the refusal's vocabulary is exactly "
          "app._EVENT_ACTOR_KIND_FILTERS's keys, including 'system'",
          r.json().get("detail", "").endswith(str(sorted(app._EVENT_ACTOR_KIND_FILTERS))),
          f"detail={r.json().get('detail')!r} "
          f"expected sorted(_EVENT_ACTOR_KIND_FILTERS)={sorted(app._EVENT_ACTOR_KIND_FILTERS)}")


# --------------------------------------------------------------------------- #
# D. record_graph_event validates `action` against GRAPH_EVENT_ACTIONS
# --------------------------------------------------------------------------- #
def section_d() -> None:
    print("\n-- D. record_graph_event raises on an action outside GRAPH_EVENT_ACTIONS --")

    # D1 — every EXISTING call site, checked, not sampled. The acceptance
    # criterion is "you must check them all", so this scans every literal
    # `record_graph_event(s, "...")` call in app.py rather than driving a
    # hand-picked subset of routes over HTTP.
    # Every API module: `app.py` alone until STORY-oss-04 split it into
    # main/db/deps and routers/. The check is unchanged; only the files it reads.
    api_files = [os.path.join(HERE, n) for n in ("app.py", "main.py", "db.py", "deps.py")]
    routers = os.path.join(HERE, "routers")
    if os.path.isdir(routers):
        api_files += [os.path.join(routers, n) for n in sorted(os.listdir(routers)) if n.endswith(".py")]
    app_src = "\n".join(open(p, encoding="utf-8").read() for p in api_files if os.path.exists(p))
    call_sites = re.findall(r'record_graph_event\(\s*s,\s*"([^"]+)"', app_src)
    check("the API modules contain real record_graph_event call sites to check",
          len(call_sites) > 0, f"found {len(call_sites)}")
    unknown = sorted({a for a in call_sites if a not in models_graph.GRAPH_EVENT_ACTIONS})
    check(f"every one of {len(call_sites)} call sites passes a literal already "
          f"in GRAPH_EVENT_ACTIONS", not unknown,
          f"unknown action(s): {unknown}" if unknown else "")

    # D2 — the negative case, called directly (this is a caller-bug, not a
    # request error, so there is no HTTP route through which to trigger it).
    with app.session() as s:
        tok = auth.current_principal.set(auth.Principal(
            kind="session", user_id=OWNER_ID, subject="owner", role="owner",
            is_service=False, must_change_password=False,
            scopes=("read", "write", "admin")))
        raised, message = False, ""
        try:
            app.record_graph_event(s, "node.frobnicate", entity_type="node",
                                   entity_id="EV-AE-BAD")
        except ValueError as e:
            raised, message = True, str(e)
        finally:
            auth.current_principal.reset(tok)
    check("record_graph_event(...) raises ValueError on an unknown action",
          raised, "no exception raised")
    check("...and the error names the offending action", "node.frobnicate" in message,
          message)

    with app.session() as s:
        n = s.scalar(select(GraphEvent).where(GraphEvent.entity_id == "EV-AE-BAD"))
    check("...and NO row was written for the rejected call", n is None, str(n))

    # D3 — a real, valid mutation still succeeds and is still recorded, so the
    # new raise has not broken the ordinary path.
    owner, owner_csrf = sign_in("owner")
    r = owner.post("/api/node", json={"id": "EV-AE-STILL-WORKS", "type": "project",
                                      "name": "still works", "csrf_token": owner_csrf},
                   headers={"Origin": TEST_ORIGIN})
    check("an ordinary, VALID write still succeeds", r.status_code == 201,
          f"{r.status_code} {r.text[:160]}")
    with app.session() as s:
        evs = list(s.scalars(select(GraphEvent).where(
            GraphEvent.entity_id == "EV-AE-STILL-WORKS",
            GraphEvent.action == "node.create")).all())
    check("...and it still wrote its node.create event", len(evs) == 1, str(len(evs)))


if __name__ == "__main__":
    app.startup()

    mk_user(OWNER_ID, "owner", role="owner")
    mk_user(MEMBER_ID, "ae-member")
    mk_user(AGENT_ID, "bot-ae-agent", is_service=True)

    section_a()
    section_b()
    section_c()
    section_d()

    print()
    if failures:
        print(f"FAILED — {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")
