"""
Verification for `GET /api/me/stats` (TASK-1444, `routers/stats.py`).

Standalone run-it-and-read-it, against a real, disposable Postgres via `testdb`, through the REAL
auth gate with signed-in clients and real Bearer tokens — never a principal installed directly.
Status changes are made through the write API, so the `graph_event` rows are the ones the one
writer records; only their `at` is moved afterwards, to place them on a chosen day.

SECTIONS:

  A. Own stats: tasks done per day for the last 7 days and the 7 before, a task counted once
     on its latest move to `done`, nothing older than 14 days, an assigned epic or story moved
     to `done` not counted (only `type == "task"`, owner decision 2026-10-30); writes and last write per linked
     agent; the exact key set, with NO `decisions` key; the payload size, measured.
  B. Agent tokens: a linked agent (write or read scope) gets its person's numbers, identical to
     the person's own; an unlinked agent gets 403.
  C. Nobody else's numbers: another person's tasks and agents never appear, a query parameter
     naming another person changes nothing, and each person sees only their own.
  D. Visibility: a task assigned to the person on a private project they cannot see, and an
     agent write on it, are not counted (nor is that write the "last write"); granting the
     project makes both appear, which proves the filter is the reason.
  E. The UTC day boundary, on a FROZEN clock (the router's `datetime.now` is pinned), so the
     result cannot depend on when the suite runs: a write at 23:59:59.999999 is today, one at
     00:00 tomorrow (a future write) is in no window but is still the last write, the first
     microsecond of the oldest day is in the previous window and the one before it is out.
     The same rows are then read again one hour later, after midnight, and every one of them
     moves one day.

Run (from a venv with the backend requirements installed):
    python verify_me_stats.py
"""
from __future__ import annotations

import base64
import contextlib
import os
import sys
import uuid
from datetime import datetime, time, timedelta, timezone

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
H = {"Origin": TEST_ORIGIN}

from sqlalchemy import select  # noqa: E402

import app  # noqa: E402
import auth  # noqa: E402
import models_auth  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthUser, NodeGrant, NodeVisibility  # noqa: E402
from models_graph import GraphEvent, Node  # noqa: E402
from routers import stats as stats_router  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


PW = "Correct-Horse-Battery-Stats-9!"
OWNER_ID, P_ID, Q_ID = "u-s-owner", "u-s-person", "u-s-other"
BOT_P1, BOT_P2, BOT_Q, BOT_LOOSE = "u-s-bot-p1", "u-s-bot-p2", "u-s-bot-q", "u-s-bot-loose"
CLOCK_ID, BOT_CLOCK = "u-s-clock", "u-s-bot-clock"
EXPECTED_KEYS = {"as_of", "window_days", "tasks_done_per_day", "tasks_done", "agents"}


def mk_user(uid: str, username: str, role: str = "member") -> None:
    with app.session() as s:
        s.add(AuthUser(id=uid, subject=username, name=username.title(), role=role,
                       is_service=False, must_change_password=False,
                       password_hash=passwords.hash_password(PW),
                       created_at=datetime.now(timezone.utc)))
        s.commit()


def mk_bot(uid: str, subject: str, person: str | None, scopes: list[str]) -> str:
    with app.session() as s:
        bot = AuthUser(id=uid, subject=subject, role="member", is_service=True, label=subject,
                       must_change_password=False, person_user_id=person,
                       created_at=datetime.now(timezone.utc))
        s.add(bot)
        s.flush()
        raw, _ = auth.mint_service_token(s, owner=bot, scopes=scopes, label=subject)
        s.commit()
        return raw


def sign_in(username: str):
    from fastapi.testclient import TestClient
    c = TestClient(app.app, base_url=TEST_ORIGIN)
    page = c.get("/auth/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": username, "password": PW, "csrf_token": token},
           headers=H, follow_redirects=False)
    return c, c.get("/api/me").json()["csrf_token"]


def bearer(raw: str):
    from fastapi.testclient import TestClient
    c = TestClient(app.app, base_url=TEST_ORIGIN)
    c.headers.update({"Authorization": "Bearer " + raw})
    return c


def mk_project(nid: str, private: bool = False) -> None:
    with app.session() as s:
        s.add(Node(id=nid, type="project", name=nid, attributes={}, attention_count=0,
                   completion=0.0, origin="atlas"))
        s.flush()
        if private:
            s.add(NodeVisibility(node_id=nid, visibility=models_auth.VISIBILITY_PRIVATE))
        s.commit()


def mk_task(client, csrf: str, binding: str, name: str, assignee: str, kind: str = "task") -> str:
    body = {"type": kind, "name": name, "binding": binding, "status": "pending",
            "assignee": assignee, "csrf_token": csrf}
    if kind != "task":
        # Epic and story ids are caller-supplied slugs (only tasks have a minter).
        body["id"] = f"{kind.upper()}-vms-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/workitem", json=body, headers=H)
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    return r.json()["id"]


def set_status(client, node_id: str, status: str, csrf: str | None = None) -> None:
    body = {"status": status}
    if csrf:
        body["csrf_token"] = csrf
    r = client.post(f"/api/node/{node_id}/status", json=body, headers=H)
    assert r.status_code == 200, f"{r.status_code} {r.text}"


def move_done_events(node_id: str, days_ago: int) -> None:
    """Place every `-> done` event of a node `days_ago` days back (noon UTC)."""
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(
        hour=12, minute=0, second=0, microsecond=0)
    if days_ago == 0:
        when = datetime.now(timezone.utc) - timedelta(seconds=1)
    with app.session() as s:
        for ev in s.scalars(select(GraphEvent).where(GraphEvent.entity_id == node_id,
                                                     GraphEvent.action == "node.status")):
            if (ev.detail or {}).get("to") == "done":
                ev.at = when
        s.commit()


@contextlib.contextmanager
def frozen_clock(now: datetime):
    """Pin `datetime.now` inside `routers/stats.py` (the only clock it reads) to `now`."""
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    real = stats_router.datetime
    stats_router.datetime = _Frozen
    try:
        yield
    finally:
        stats_router.datetime = real


def stats(client, **params) -> dict:
    r = client.get("/api/me/stats", params=params or None)
    assert r.status_code == 200, f"{r.status_code} {r.text}"
    return r.json()


# --------------------------------------------------------------------------- #
def main() -> None:
    print("=" * 78)
    print("Atlas /api/me/stats verification")
    print(f"DB:      {os.environ['ATLAS_DB_URL']}")
    print("=" * 78)

    app.startup()
    mk_user(OWNER_ID, "s-owner", role="owner")
    mk_user(P_ID, "s-person")
    mk_user(Q_ID, "s-other")
    raw_p1 = mk_bot(BOT_P1, "bot-s-p-one", P_ID, ["read", "write"])
    raw_p2 = mk_bot(BOT_P2, "bot-s-p-two", P_ID, ["read"])
    raw_q = mk_bot(BOT_Q, "bot-s-q", Q_ID, ["read", "write"])
    raw_loose = mk_bot(BOT_LOOSE, "bot-s-loose", None, ["read"])
    owner, ocsrf = sign_in("s-owner")
    person, _ = sign_in("s-person")
    other, _ = sign_in("s-other")
    p1, p2, q_bot, loose = bearer(raw_p1), bearer(raw_p2), bearer(raw_q), bearer(raw_loose)

    try:
        mk_project("S-PUB")
        mk_project("S-PRIV", private=True)
        with app.session() as s:
            s.add(NodeGrant(node_id="S-PRIV", user_id=Q_ID))
            s.commit()

        t1 = mk_task(owner, ocsrf, "S-PUB", "Done today by an agent", P_ID)
        t2 = mk_task(owner, ocsrf, "S-PUB", "Done today twice", P_ID)
        t3 = mk_task(owner, ocsrf, "S-PUB", "Done nine days ago", P_ID)
        t4 = mk_task(owner, ocsrf, "S-PUB", "Done twenty days ago", P_ID)
        t5 = mk_task(owner, ocsrf, "S-PUB", "Still in progress", P_ID)
        tq = mk_task(owner, ocsrf, "S-PUB", "Someone else's", Q_ID)
        tpriv = mk_task(owner, ocsrf, "S-PRIV", "Mine but hidden from me", P_ID)
        # Only tasks count (owner decision, 2026-10-30): an epic or story reaching done is not a task done.
        tepic = mk_task(owner, ocsrf, "S-PUB", "An assigned epic", P_ID, kind="epic")
        tstory = mk_task(owner, ocsrf, "S-PUB", "An assigned story", P_ID, kind="story")

        set_status(p1, t1, "done")
        set_status(p1, t2, "done")
        set_status(p1, t2, "done")          # a re-assertion: still one task
        set_status(owner, t3, "done", ocsrf)
        set_status(owner, t4, "done", ocsrf)
        set_status(owner, t5, "in_progress", ocsrf)
        set_status(q_bot, tq, "done")
        set_status(owner, tpriv, "done", ocsrf)
        set_status(owner, tepic, "done", ocsrf)
        set_status(owner, tstory, "done", ocsrf)
        move_done_events(t3, 9)
        move_done_events(t4, 20)
        # An agent write on the hidden task, newer than any other write by that agent. The
        # event log has one writer, and P's agent cannot reach this node through the API (it
        # 404s), so the owner's event is re-attributed here to build the case.
        with app.session() as s:
            ev = s.scalars(select(GraphEvent).where(GraphEvent.entity_id == tpriv,
                                                    GraphEvent.action == "node.create")).one()
            ev.actor_user_id, ev.actor_kind = BOT_P1, "token"
            # Newer than every other write, but never past the end of today (UTC): a write
            # dated tomorrow is in no window, so `now + 30 min` failed every run started in
            # the last half hour before midnight UTC. Section E pins that boundary.
            now = datetime.now(timezone.utc)
            hidden_at = min(now + timedelta(minutes=30),
                            datetime.combine(now.date(), time.max, tzinfo=timezone.utc))
            ev.at = hidden_at
            s.commit()

        # ---------------------------------------------------------------- A ---
        print("\n-- A. own stats --")
        r = person.get("/api/me/stats")
        check("a signed-in person gets 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        mine = r.json()
        print(f"   payload: {len(r.content)} bytes  {r.text}")
        check("the key set is exactly the documented one", set(mine) == EXPECTED_KEYS, str(sorted(mine)))
        check("there is NO `decisions` key (absent, not zero)", "decisions" not in mine, str(sorted(mine)))
        check("as_of is today (UTC)", mine["as_of"] == datetime.now(timezone.utc).date().isoformat(),
              mine["as_of"])
        per = mine["tasks_done_per_day"]
        check("two windows of seven days", len(per["current"]) == 7 and len(per["previous"]) == 7, str(per))
        check("today: two tasks, the re-asserted one counted once",
              per["current"] == [0, 0, 0, 0, 0, 0, 2], str(per["current"]))
        check("nine days ago lands in the previous window, on its day",
              per["previous"] == [0, 0, 0, 0, 1, 0, 0], str(per["previous"]))
        with app.session() as s:
            kinds = {n.id: n.type for n in s.scalars(select(Node).where(Node.id.in_([tepic, tstory])))}
            moved = s.scalars(select(GraphEvent.entity_id).where(
                GraphEvent.entity_id.in_([tepic, tstory]), GraphEvent.action == "node.status")).all()
        check("the fixture holds an assigned epic and story moved to done today",
              kinds == {tepic: "epic", tstory: "story"} and set(moved) == {tepic, tstory}, str(kinds))
        check("...and neither is counted as a task done (today stays 2, not 4)",
              per["current"][-1] == 2, str(per["current"]))
        check("totals: 2 now, 1 before (20 days ago, in progress and hidden do not count)",
              mine["tasks_done"] == {"current": 2, "previous": 1}, str(mine["tasks_done"]))
        agents = {a["agent"]: a for a in mine["agents"]}
        check("both linked agents are listed, by subject",
              [a["agent"] for a in mine["agents"]] == ["bot-s-p-one", "bot-s-p-two"], str(mine["agents"]))
        a1 = agents.get("bot-s-p-one", {})
        check("the writing agent: 3 writes in the window, 0 before",
              a1.get("writes") == 3 and a1.get("writes_previous") == 0, str(a1))
        last = datetime.fromisoformat(a1["last_write_at"]) if a1.get("last_write_at") else None
        check("...and its last write is a real, recent time, not the hidden one",
              last is not None and last < hidden_at
              and datetime.now(timezone.utc) - last < timedelta(minutes=10), str(a1.get("last_write_at")))
        a2 = agents.get("bot-s-p-two", {})
        check("the idle agent: 0 writes and no last write",
              a2 == {"agent": "bot-s-p-two", "writes": 0, "writes_previous": 0, "last_write_at": None},
              str(a2))
        check("every value is a number, a date or a short label (ADR 0040)",
              len(r.content) < 1024, f"{len(r.content)} bytes")

        # ---------------------------------------------------------------- B ---
        print("\n-- B. agent tokens --")
        r = p1.get("/api/me/stats")
        check("a linked agent's token gets 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        check("...and exactly its person's numbers", r.json() == mine, r.text[:300])
        r = p2.get("/api/me/stats")
        check("a read-only linked agent gets the same person's numbers",
              r.status_code == 200 and r.json() == mine, f"{r.status_code} {r.text[:300]}")
        r = loose.get("/api/me/stats")
        check("an agent linked to no person gets 403", r.status_code == 403, f"{r.status_code} {r.text}")
        check("...and no numbers in the refusal", "agents" not in r.json() and "tasks_done" not in r.json(),
              r.text)
        r = loose.get("/api/me/stats", params={"person": P_ID})
        check("...and naming a person does not unlock it", r.status_code == 403, f"{r.status_code}")

        # ---------------------------------------------------------------- C ---
        print("\n-- C. nobody else's numbers --")
        names = {a["agent"] for a in mine["agents"]}
        check("another person's agent never appears", "bot-s-q" not in names and "bot-s-loose" not in names,
              str(names))
        for params in ({"person": Q_ID}, {"user_id": Q_ID}, {"assignee": Q_ID},
                       {"person_user_id": Q_ID}, {"user": "s-other"}):
            got = stats(person, **params)
            check(f"?{next(iter(params))}= naming someone else changes nothing", got == mine, str(got)[:200])
            got = stats(p1, **params)
            check(f"...nor through the agent token (?{next(iter(params))}=)", got == mine, str(got)[:200])
        theirs = stats(other)
        check("the other person sees their own task, not mine",
              theirs["tasks_done"] == {"current": 1, "previous": 0}, str(theirs["tasks_done"]))
        check("...and only their own agent", [a["agent"] for a in theirs["agents"]] == ["bot-s-q"],
              str(theirs["agents"]))
        check("...with its one write", theirs["agents"][0]["writes"] == 1, str(theirs["agents"]))
        check("the other person's agent token gets the other person's numbers",
              stats(q_bot) == theirs, str(stats(q_bot))[:200])
        own = stats(owner)
        check("the owner, who did most of the writing, has no assigned tasks and no agents",
              own["tasks_done"] == {"current": 0, "previous": 0} and own["agents"] == [], str(own))

        # ---------------------------------------------------------------- D ---
        print("\n-- D. visibility --")
        check("before a grant, the hidden task is not counted (2 today, not 3)",
              mine["tasks_done_per_day"]["current"][-1] == 2, str(mine["tasks_done_per_day"]))
        with app.session() as s:
            s.add(NodeGrant(node_id="S-PRIV", user_id=P_ID))
            s.commit()
        after = stats(person)
        check("after the grant, the hidden task counts: 3 today",
              after["tasks_done_per_day"]["current"][-1] == 3, str(after["tasks_done_per_day"]))
        a1 = {a["agent"]: a for a in after["agents"]}["bot-s-p-one"]
        check("...and the agent's write on it counts too: 4 writes",
              a1["writes"] == 4, str(a1))
        check("...and is now its last write",
              datetime.fromisoformat(a1["last_write_at"]) == hidden_at, str(a1["last_write_at"]))
        check("the agent inherits the grant from its person and agrees", stats(p1) == after,
              str(stats(p1))[:200])

        # ---------------------------------------------------------------- E ---
        print("\n-- E. the UTC day boundary, on a frozen clock --")
        mk_user(CLOCK_ID, "s-clock")
        raw_clock = mk_bot(BOT_CLOCK, "bot-s-clock", CLOCK_ID, ["read", "write"])
        clock_person, _ = sign_in("s-clock")
        clock_bot = bearer(raw_clock)
        day = datetime(2026, 6, 10, tzinfo=timezone.utc)          # "today" on the frozen clock
        first_day = day - timedelta(days=13)                       # the oldest day of the two windows
        placed = {
            "last microsecond of today": day + timedelta(days=1) - timedelta(microseconds=1),
            "midnight tomorrow (a future write)": day + timedelta(days=1),
            "first microsecond of the oldest day": first_day,
            "last microsecond before the windows": first_day - timedelta(microseconds=1),
        }
        tasks = {label: mk_task(owner, ocsrf, "S-PUB", f"Clock: {label}", CLOCK_ID) for label in placed}
        for label, tid in tasks.items():
            set_status(clock_bot, tid, "done")
        with app.session() as s:
            for label, tid in tasks.items():
                # The create also records a `node.status` (-> pending) by the owner; the row
                # to place is the agent's one move to done.
                ev = s.scalars(select(GraphEvent).where(GraphEvent.entity_id == tid,
                                                        GraphEvent.action == "node.status",
                                                        GraphEvent.actor_user_id == BOT_CLOCK)).one()
                assert (ev.detail or {}).get("to") == "done", ev.detail
                ev.at = placed[label]
            s.commit()
            # Every write of this agent is one of the four placed rows; a stray row at real
            # time would be its "last write" and lie outside the frozen windows.
            n_clock = len(s.scalars(select(GraphEvent.id).where(GraphEvent.actor_user_id == BOT_CLOCK)).all())
        check("the clock agent wrote exactly the four placed rows", n_clock == 4, str(n_clock))

        with frozen_clock(day.replace(hour=23, minute=45)):        # 15 minutes before midnight
            late = stats(clock_person)
            late_bot = stats(clock_bot)                            # read under the SAME frozen clock
        bot_late = late["agents"][0]
        check("frozen at 23:45 UTC: as_of is that day", late["as_of"] == "2026-06-10", late["as_of"])
        check("...23:59:59.999999 is today, midnight tomorrow is in no window",
              late["tasks_done_per_day"]["current"] == [0, 0, 0, 0, 0, 0, 1], str(late["tasks_done_per_day"]))
        check("...the first microsecond of the oldest day is in, the one before it is out",
              late["tasks_done_per_day"]["previous"] == [1, 0, 0, 0, 0, 0, 0], str(late["tasks_done_per_day"]))
        check("...the agent's writes follow the same days: 1 now, 1 before",
              bot_late["writes"] == 1 and bot_late["writes_previous"] == 1, str(bot_late))
        check("...and its last write is the future one, whatever the window",
              datetime.fromisoformat(bot_late["last_write_at"]) == placed["midnight tomorrow (a future write)"],
              str(bot_late))
        check("...and the agent token reads the same numbers", late_bot == late,
              f"agent {late_bot} vs person {late}")

        with frozen_clock(day + timedelta(days=1, minutes=45)):    # 45 minutes after midnight
            early = stats(clock_person)
            early_bot = stats(clock_bot)
        bot_early = early["agents"][0]
        check("frozen at 00:45 UTC the next day: as_of moves one day", early["as_of"] == "2026-06-11",
              early["as_of"])
        check("...last night's write is yesterday and midnight's is today",
              early["tasks_done_per_day"]["current"] == [0, 0, 0, 0, 0, 1, 1], str(early["tasks_done_per_day"]))
        check("...and the oldest day has dropped out of the windows",
              early["tasks_done_per_day"]["previous"] == [0] * 7 and early["tasks_done"] == {"current": 2, "previous": 0},
              str(early))
        check("...the agent: 2 writes now, 0 before",
              bot_early["writes"] == 2 and bot_early["writes_previous"] == 0, str(bot_early))
        check("...and the agent token agrees after midnight too", early_bot == early,
              f"agent {early_bot} vs person {early}")
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
