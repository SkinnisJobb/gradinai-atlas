"""
Verification for STORY-atlas-fork-loop-rails, rail 1: an implementing agent
cannot rewrite the acceptance criteria it is judged against.

Same shape as verify_status_lifecycle.py and verify_agent_identity.py — no
pytest suite exists in this repo, so this is a standalone run-it-and-read-it
script against a real disposable Postgres via `testdb`, driving the REAL API
through the REAL auth gate with a REAL HMAC'd service token.

TWO THINGS THIS SCRIPT REFUSES TO DO, both named in the story's own acceptance:

  - It does not ASSERT the refusal, it ATTEMPTS the write and reads the answer.
    A rail confirmed by reading the source is a rail nobody has tried.
  - It REMOVES the rail and re-attempts, requiring the write to succeed. A check
    that cannot fail proves nothing, and the only way to know this one can is to
    watch it go the other way. If that section ever passes with the rail still
    installed, the whole file is decoration.

Run (from a venv with the backend requirements installed):
    python verify_loop_rails.py
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:
    print(f"FATAL: {e}")
    sys.exit(1)
os.environ["ATLAS_DB_URL"] = DB.url
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", "dGVzdC1wZXBwZXItMzJieXRlcy1sb25nLWVub3VnaCE=")  # gitleaks:allow (fixed test value)
os.environ.setdefault("ATLAS_TOKEN_PEPPER", "dGVzdC10b2tlbi1wZXBwZXItMzJieXRlcy1sb25nISE=")  # gitleaks:allow (fixed test value)
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")

import app        # noqa: E402
import auth       # noqa: E402
import models_graph  # noqa: E402
import passwords  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TEST_ORIGIN = "https://testserver"

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def main() -> int:
    app.startup()

    # ---------------------------------------------------------------- fixture
    # One human (session principal), one bot with a WRITE-scoped token — the
    # loop's own shape — and one workitem with criteria to attack.
    username = f"human-{uuid.uuid4().hex[:8]}"
    password = "correct horse battery staple " + uuid.uuid4().hex
    item_id = f"TASK-rail-{uuid.uuid4().hex[:8]}"

    with app.session() as s:
        human = auth.AuthUser(
            id=str(uuid.uuid4()), subject=username, name="Rail Human", role="owner",
            is_service=False, password_hash=passwords.hash_password(password),
            must_change_password=False, created_at=datetime.now(timezone.utc),
        )
        bot = auth.AuthUser(
            id=str(uuid.uuid4()), subject=f"bot-{uuid.uuid4().hex[:8]}", name="Rail Bot", role="member",
            is_service=True, password_hash=None, must_change_password=False,
            created_at=datetime.now(timezone.utc),
        )
        s.add_all([human, bot])
        s.flush()
        raw_token, _ = auth.mint_service_token(
            s, owner=bot, scopes=["read", "write"], label="loop-rail-probe")
        s.add(models_graph.Node(
            id=item_id, type="task", name="a task with criteria",
            body="probe", attributes={"status": "pending"}, origin="atlas",
        ))
        s.flush()
        s.add(models_graph.AcceptanceCriterion(node_id=item_id, position=0, text="ORIGINAL criterion"))
        s.commit()

    def bot_client() -> TestClient:
        c = TestClient(app.app, base_url=TEST_ORIGIN)
        c.headers.update({"Authorization": "Bearer " + raw_token})
        return c

    def human_client() -> TestClient:
        c = TestClient(app.app, base_url=TEST_ORIGIN)
        page = c.get("/auth/login")
        tok = page.text.split('name="csrf_token" value="')[1].split('"')[0]
        c.post("/auth/login",
               data={"username": username, "password": password, "csrf_token": tok},
               headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
        return c

    def criteria() -> list[str]:
        with app.session() as s:
            return app._acceptance_list(s, item_id)

    print("\n== 1. baseline: the token is real and can write OTHER fields ==")
    bot_c = bot_client()
    r = bot_c.get("/api/me")
    check("bot token authenticates", r.status_code == 200 and r.json()["is_service"] is True,
          f"{r.status_code}")
    r = bot_c.patch(f"/api/workitem/{item_id}", json={"status": "in_progress"},
                    headers={"Origin": TEST_ORIGIN})
    check("bot CAN change status — the rail is narrow, not a write ban",
          r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    print("\n== 2. the rail: attempted, not asserted ==")
    r = bot_c.patch(f"/api/workitem/{item_id}",
                    json={"acceptance": ["REWRITTEN by the implementer"]},
                    headers={"Origin": TEST_ORIGIN})
    check("bot is REFUSED when it rewrites acceptance", r.status_code == 403,
          f"{r.status_code} {r.text[:160]}")
    check("the refusal carries a reason, not just a code",
          r.status_code == 403 and r.json().get("error") == "acceptance_is_not_yours_to_write"
          and "field" in r.json(),
          r.text[:160])
    check("and the criteria on disk are UNCHANGED", criteria() == ["ORIGINAL criterion"],
          repr(criteria()))

    print("\n== 3. a human at a browser is unaffected ==")
    hc = human_client()
    csrf = hc.get("/api/me").json()["csrf_token"]
    r = hc.patch(f"/api/workitem/{item_id}",
                 json={"acceptance": ["EDITED by a human", "second"], "csrf_token": csrf},
                 headers={"Origin": TEST_ORIGIN})
    check("session CAN change acceptance", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    check("and the change landed", criteria() == ["EDITED by a human", "second"], repr(criteria()))

    print("\n== 4. creation still carries acceptance (proposing != re-judging) ==")
    r = bot_c.post("/api/workitem",
                   json={"type": "task", "name": "filed by an agent",
                         "acceptance": ["a criterion authored at creation"]},
                   headers={"Origin": TEST_ORIGIN})
    check("bot CAN file new work WITH criteria", r.status_code in (200, 201),
          f"{r.status_code} {r.text[:120]}")

    print("\n== 5. REMOVE the rail and watch the check fail ==")
    # The point of the whole file. If this section cannot make the write
    # succeed, section 2 was not testing the rail — it was testing something
    # else that happened to return 403, and we would never know.
    original = app.acceptance_rail_refusal
    app.acceptance_rail_refusal = lambda: None          # the rail, removed
    try:
        r = bot_c.patch(f"/api/workitem/{item_id}",
                        json={"acceptance": ["REWRITTEN with the rail removed"]},
                        headers={"Origin": TEST_ORIGIN})
        check("WITHOUT the rail the same request SUCCEEDS", r.status_code == 200,
              f"{r.status_code} {r.text[:120]}")
        check("...and the criteria really were overwritten",
              criteria() == ["REWRITTEN with the rail removed"], repr(criteria()))
    finally:
        app.acceptance_rail_refusal = original

    r = bot_c.patch(f"/api/workitem/{item_id}", json={"acceptance": ["and again"]},
                    headers={"Origin": TEST_ORIGIN})
    check("rail restored: refused once more", r.status_code == 403, f"{r.status_code}")

    # ------------------------------------------------------------------ #
    # STORY-atlas-acceptance-human-approved. Sections 1-5 above ran with no
    # ATLAS_ACCEPTANCE_AGENT_WRITE at all, and are unchanged: they ARE the
    # proof that the default is today's rail. The detail (every refusal
    # reason, visibility, supersede, the MCP shape) is in
    # verify_acceptance_approval.py; these are the two rail-level cases.
    # ------------------------------------------------------------------ #
    approval = {"by": username, "quote": "yes, replace the criteria with that list"}

    print("\n== 6. (a) attested write: opt-in, and only with a person's approval ==")
    r_plain = bot_c.patch(f"/api/workitem/{item_id}", json={"acceptance": ["x"]},
                          headers={"Origin": TEST_ORIGIN})
    r_off = bot_c.patch(f"/api/workitem/{item_id}", json={"acceptance": ["x"], "approval": approval},
                        headers={"Origin": TEST_ORIGIN})
    check("setting unset: an approval changes nothing, the refusal is byte-identical",
          r_off.status_code == 403 and r_off.content == r_plain.content,
          f"{r_off.status_code} {r_off.text[:120]}")
    os.environ["ATLAS_ACCEPTANCE_AGENT_WRITE"] = "attested"
    try:
        r = bot_c.patch(f"/api/workitem/{item_id}", json={"acceptance": ["attested change"]},
                        headers={"Origin": TEST_ORIGIN})
        check("attested, no approval: still 403 acceptance_is_not_yours_to_write, with a hint",
              r.status_code == 403 and r.json().get("error") == "acceptance_is_not_yours_to_write"
              and "approval" in r.json().get("hint", ""), r.text[:160])
        before = criteria()
        r = bot_c.patch(f"/api/workitem/{item_id}",
                        json={"acceptance": ["attested change"], "approval": approval},
                        headers={"Origin": TEST_ORIGIN})
        check("attested, with a person's approval: the write succeeds", r.status_code == 200,
              f"{r.status_code} {r.text[:160]}")
        check("...and the criteria changed", criteria() == ["attested change"], repr(criteria()))
        with app.session() as s:
            ev = s.scalars(app.select(models_graph.GraphEvent).where(
                models_graph.GraphEvent.entity_id == item_id,
                models_graph.GraphEvent.action == "node.acceptance.attested")
                .order_by(models_graph.GraphEvent.id.desc())).first()
            d = (ev.detail or {}) if ev is not None else {}
        check("a node.acceptance.attested event holds approver, quote, agent, old and new",
              ev is not None and d.get("approver", {}).get("username") == username
              and d.get("quote") == approval["quote"] and d.get("agent") == ev.actor_subject
              and ev.actor_kind == "token" and d.get("old") == before
              and d.get("new") == ["attested change"], repr(d)[:200])
    finally:
        os.environ.pop("ATLAS_ACCEPTANCE_AGENT_WRITE", None)

    print("\n== 7. (b) proposal: any mode, and only a person applies it ==")
    r = bot_c.post(f"/api/workitem/{item_id}/acceptance-proposal",
                   json={"acceptance": ["proposed by the agent"]}, headers={"Origin": TEST_ORIGIN})
    check("bot CAN propose with the setting off", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
    pid = r.json().get("id") if r.status_code == 201 else None
    check("...and proposing changed no criterion", criteria() == ["attested change"], repr(criteria()))
    r = bot_c.post(f"/api/workitem/{item_id}/acceptance-proposal/{pid}/approve", json={},
                   headers={"Origin": TEST_ORIGIN})
    check("bot CANNOT approve its own proposal", r.status_code == 403, f"{r.status_code} {r.text[:120]}")
    csrf = hc.get("/api/me").json()["csrf_token"]
    r = hc.post(f"/api/workitem/{item_id}/acceptance-proposal/{pid}/approve",
                json={"csrf_token": csrf}, headers={"Origin": TEST_ORIGIN})
    check("a session's Approve applies it", r.status_code == 200 and criteria() == ["proposed by the agent"],
          f"{r.status_code} {r.text[:120]} {criteria()!r}")

    print()
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} check(s): " + "; ".join(FAILURES))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
