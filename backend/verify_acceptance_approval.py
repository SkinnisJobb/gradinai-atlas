"""
Verification for STORY-atlas-acceptance-human-approved: an agent may change a
workitem's acceptance only with a person's approval.

`acceptance_rail_refusal` (routers/workitems.py) refuses every machine
principal that changes an existing item's acceptance. This story adds two
opt-in ways past it, both carrying a HUMAN's approval, and changes nothing
when neither is used. `verify_loop_rails.py` still proves the rail with
default configuration; this script proves the rest, through the real gate,
against a real Postgres, with real HMAC'd tokens and real browser sessions:

  A. THE SETTING. `ATLAS_ACCEPTANCE_AGENT_WRITE` through `settings.load()`:
     unset/blank/`off` = off, `attested` = attested, anything else refuses to
     load naming the variable, and the per-request reader fails CLOSED.
  B. DEFAULT = TODAY, BYTE FOR BYTE. With the setting unset or `off`, a
     machine's acceptance PATCH gets exactly the rail's refusal, frozen here as
     an oracle, whether or not it carries an `approval`.
  C. (a) ATTESTED WRITE. Every way an approval can be missing or invalid is the
     rail's 403 plus a hint naming the setting and the field, writes nothing
     and records nothing; a valid one succeeds and records approver, quote,
     agent, old and new. Two perturbations show the checks are load-bearing.
  D. (b) PROPOSALS. Any principal with write scope proposes in any mode,
     without changing a criterion; only a session approves (through the normal
     PATCH path, recording the approver) or rejects; a new proposal supersedes
     the old one, recorded; decisions name the proposal they decide.
  E. VISIBILITY. A proposal on an item the caller cannot see is invisible like
     the item: the same 404 as an id that never existed, on all four routes,
     and its events are not countable.

Run (from a venv with the backend requirements installed):
    python verify_acceptance_approval.py
"""
from __future__ import annotations

import base64
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
os.environ.pop("ATLAS_ACCEPTANCE_AGENT_WRITE", None)
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")

import app        # noqa: E402
import auth       # noqa: E402
import models_graph  # noqa: E402
import passwords  # noqa: E402
import settings   # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from models_auth import NodeVisibility  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

TEST_ORIGIN = "https://testserver"
H = {"Origin": TEST_ORIGIN}
ENV = "ATLAS_ACCEPTANCE_AGENT_WRITE"
FAILURES: list[str] = []

#: THE ORACLE: the rail's refusal to a token, exactly as it was before this
#: story. Frozen here rather than read off `acceptance_rail_refusal`, so a
#: change to the rail's words shows up as a failure in this file too.
RAIL_BODY = {
    "error": "acceptance_is_not_yours_to_write",
    "detail": "Acceptance criteria define what this item is judged against, "
              "so the principal doing the work cannot change them. Propose "
              "the change to a human, who can make it from the board. Every "
              "other field on this route is unaffected.",
    "field": "acceptance",
    "principal_kind": "token",
}
RAIL_BYTES = JSONResponse(RAIL_BODY, status_code=403).body

PW = "Correct-Horse-Battery-Acceptance-7!"
SUFFIX = uuid.uuid4().hex[:6]
OWNER, MEMBER, GONE = f"own-{SUFFIX}", f"mem-{SUFFIX}", f"gone-{SUFFIX}"
BOT, BOT_MEMBER, BOT_READ = f"bot-{SUFFIX}", f"botm-{SUFFIX}", f"botr-{SUFFIX}"
ITEM = f"TASK-appr-{SUFFIX}"
HIDDEN = f"TASK-hidden-{SUFFIX}"
PROJECT = f"proj-private-{SUFFIX}"


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def now():
    return datetime.now(timezone.utc)


def setup() -> dict:
    """People, agents, tokens, an ordinary item and one bound to a private project."""
    tokens = {}
    with app.session() as s:
        def user(subject, role, *, service=False, disabled=False):
            u = auth.AuthUser(
                id=str(uuid.uuid4()), subject=subject, name=subject.title(), role=role,
                is_service=service, password_hash=None if service else passwords.hash_password(PW),
                must_change_password=False, created_at=now(),
                disabled_at=now() if disabled else None)
            s.add(u)
            return u
        user(OWNER, "owner")
        user(MEMBER, "member")
        user(GONE, "member", disabled=True)
        bot = user(BOT, "owner", service=True)
        botm = user(BOT_MEMBER, "member", service=True)
        botr = user(BOT_READ, "owner", service=True)
        s.flush()
        tokens["bot"], _ = auth.mint_service_token(s, owner=bot, scopes=["read", "write"], label="appr")
        tokens["botm"], _ = auth.mint_service_token(s, owner=botm, scopes=["read", "write"], label="appr")
        tokens["botr"], _ = auth.mint_service_token(s, owner=botr, scopes=["read"], label="appr")
        for nid, typ in ((ITEM, "task"), (HIDDEN, "task"), (PROJECT, "project")):
            s.add(models_graph.Node(id=nid, type=typ, name=nid, body="probe",
                                    attributes={"status": "pending"} if typ == "task" else {},
                                    origin="atlas"))
        s.flush()
        s.add(models_graph.Edge(src=HIDDEN, dst=PROJECT, edge_type="works_on", directed=True,
                                origin="atlas"))
        for nid in (ITEM, HIDDEN):
            s.add(models_graph.AcceptanceCriterion(node_id=nid, position=0, text="ORIGINAL"))
        s.add(NodeVisibility(node_id=PROJECT, visibility="private", set_by=None, set_at=now()))
        s.commit()
    return tokens


def token_client(raw: str) -> TestClient:
    c = TestClient(app.app, base_url=TEST_ORIGIN)
    c.headers.update({"Authorization": "Bearer " + raw})
    return c


def session_client(username: str):
    c = TestClient(app.app, base_url=TEST_ORIGIN)
    page = c.get("/auth/login")
    tok = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": username, "password": PW, "csrf_token": tok},
           headers=H, follow_redirects=False)
    return c, c.get("/api/me").json()["csrf_token"]


def criteria(nid: str = ITEM) -> list[str]:
    with app.session() as s:
        return app._acceptance_list(s, nid)


def events(action: str, nid: str = ITEM) -> list:
    with app.session() as s:
        return list(s.scalars(select(models_graph.GraphEvent).where(
            models_graph.GraphEvent.entity_id == nid,
            models_graph.GraphEvent.action == action).order_by(models_graph.GraphEvent.id)).all())


def set_mode(value: str | None) -> None:
    if value is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = value


def section_a() -> None:
    print("\n== A. the setting, through settings.load() ==")
    base = {"ATLAS_LOGIN_ORIGINS": "https://testserver"}
    check("unset reads as off", settings.load(base).acceptance_agent_write == "off")
    check("blank reads as off", settings.load({**base, ENV: "  "}).acceptance_agent_write == "off")
    check("off reads as off", settings.load({**base, ENV: "off"}).acceptance_agent_write == "off")
    check("attested (any case) reads as attested",
          settings.load({**base, ENV: " Attested "}).acceptance_agent_write == "attested")
    try:
        settings.load({**base, ENV: "on"})
        check("a malformed value refuses to load, naming the variable", False, "no error")
    except settings.SettingsError as exc:
        check("a malformed value refuses to load, naming the variable", ENV in str(exc), str(exc))
    set_mode("yes-please")
    check("the per-request reader fails CLOSED on a malformed value",
          app.attested_acceptance_mode() == "off", app.attested_acceptance_mode())
    set_mode(None)


def section_b(bot: TestClient) -> None:
    print("\n== B. default configuration: today's rail, byte for byte ==")
    approval = {"by": OWNER, "quote": "go ahead"}
    for mode in (None, "off"):
        set_mode(mode)
        label = "unset" if mode is None else mode
        for body in ({"acceptance": ["B"]}, {"acceptance": ["B"], "approval": approval}):
            r = bot.patch(f"/api/workitem/{ITEM}", json=body, headers=H)
            check(f"setting {label}, approval {'sent' if 'approval' in body else 'absent'}: "
                  "403 with the oracle's exact bytes",
                  r.status_code == 403 and r.content == RAIL_BYTES, f"{r.status_code} {r.text[:140]}")
    check("nothing changed and nothing was attested",
          criteria() == ["ORIGINAL"] and not events("node.acceptance.attested"), repr(criteria()))
    set_mode(None)


def section_c(bot: TestClient, owner) -> None:
    print("\n== C. (a) attested write ==")
    set_mode("attested")
    try:
        bad = {
            "no approval": None,
            "approval is not an object": "yes",
            "no quote": {"by": OWNER},
            "blank quote": {"by": OWNER, "quote": "   "},
            "no by": {"quote": "go"},
            "unknown keys": {"by": OWNER, "quote": "go", "scope": "everything"},
            "quote too long": {"by": OWNER, "quote": "x" * (app.APPROVAL_QUOTE_MAX + 1)},
            "unknown account": {"by": "nobody-" + SUFFIX, "quote": "go"},
            "an agent account": {"by": BOT, "quote": "I approve myself"},
            "a disabled person": {"by": GONE, "quote": "go"},
        }
        for label, approval in bad.items():
            body = {"acceptance": ["C"]}
            if approval is not None:
                body["approval"] = approval
            r = bot.patch(f"/api/workitem/{ITEM}", json=body, headers=H)
            j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            hint = j.get("hint", "")
            check(f"{label}: the rail's 403, with a hint naming {ENV} and `approval`",
                  r.status_code == 403 and {k: v for k, v in j.items() if k != "hint"} == RAIL_BODY
                  and ENV in hint and "approval" in hint, f"{r.status_code} {r.text[:160]}")
        check("none of them changed a criterion or recorded an attestation",
              criteria() == ["ORIGINAL"] and not events("node.acceptance.attested"), repr(criteria()))

        r = bot.patch(f"/api/workitem/{ITEM}", json={"status": "in_progress", "approval": {"by": OWNER, "quote": "q"}},
                      headers=H)
        check("approval without acceptance is a 400 naming the field",
              r.status_code == 400 and r.json().get("field") == "approval", r.text[:120])

        quote = "Yes, those three criteria. — Own"
        r = bot.patch(f"/api/workitem/{ITEM}",
                      json={"acceptance": ["C1", "C2", "C3"], "approval": {"by": OWNER, "quote": quote}},
                      headers=H)
        check("a valid approval: 200, and the criteria changed",
              r.status_code == 200 and criteria() == ["C1", "C2", "C3"], f"{r.status_code} {r.text[:160]}")
        evs = events("node.acceptance.attested")
        d = evs[-1].detail if evs else {}
        check("one node.acceptance.attested event, with approver, quote, agent, old and new",
              len(evs) == 1 and d.get("approver", {}).get("username") == OWNER
              and d.get("quote") == quote and d.get("agent") == BOT
              and d.get("old") == ["ORIGINAL"] and d.get("new") == ["C1", "C2", "C3"], repr(d)[:240])
        check("the event's actor is the agent's token, not the approver",
              bool(evs) and evs[-1].actor_kind == "token" and evs[-1].actor_subject == BOT)
        check("the ordinary node.update event is still written, naming acceptance",
              any("acceptance" in (e.detail or {}).get("fields", []) for e in events("node.update")))
        r = owner[0].get(f"/api/workitem/{ITEM}/acceptance-proposal")
        la = r.json().get("last_attested") or {}
        check("GET .../acceptance-proposal serves the attested line for the board",
              r.status_code == 200 and la.get("agent") == BOT and la.get("quote") == quote
              and (la.get("approver") or {}).get("username") == OWNER, r.text[:200])

        c, csrf = owner
        r = c.patch(f"/api/workitem/{ITEM}",
                    json={"acceptance": ["C1", "C2", "C3", "C4"], "approval": {"by": MEMBER, "quote": "x"},
                          "csrf_token": csrf}, headers=H)
        check("a session's approval field is ignored: 200, no attestation recorded",
              r.status_code == 200 and len(events("node.acceptance.attested")) == 1, r.text[:120])

        print("  -- perturbation 1: remove the approver check, and an agent can vouch --")
        original = app._resolve_approver
        app._resolve_approver = lambda s, name: (s.scalars(select(auth.AuthUser).where(
            auth.AuthUser.subject == name)).first(), None)
        try:
            r = bot.patch(f"/api/workitem/{ITEM}",
                          json={"acceptance": ["by an agent's say-so"], "approval": {"by": BOT, "quote": "me"}},
                          headers=H)
            check("WITHOUT _resolve_approver an agent approver is accepted", r.status_code == 200,
                  f"{r.status_code} {r.text[:120]}")
        finally:
            app._resolve_approver = original
        r = bot.patch(f"/api/workitem/{ITEM}",
                      json={"acceptance": ["again"], "approval": {"by": BOT, "quote": "me"}}, headers=H)
        check("restored: refused once more", r.status_code == 403, f"{r.status_code}")

        print("  -- perturbation 2: under attested the rail still decides first --")
        original_rail = app.acceptance_rail_refusal
        app.acceptance_rail_refusal = lambda: None
        try:
            n_before = len(events("node.acceptance.attested"))
            r = bot.patch(f"/api/workitem/{ITEM}", json={"acceptance": ["no rail"]}, headers=H)
            check("WITHOUT the rail a write needs no approval at all, and is not recorded as attested",
                  r.status_code == 200 and len(events("node.acceptance.attested")) == n_before,
                  f"{r.status_code}")
        finally:
            app.acceptance_rail_refusal = original_rail
    finally:
        set_mode(None)


def section_d(bot: TestClient, botr: TestClient, owner) -> None:
    print("\n== D. (b) proposals ==")
    c, csrf = owner
    # A known starting point, set by a person.
    c.patch(f"/api/workitem/{ITEM}", json={"acceptance": ["D0"], "csrf_token": csrf}, headers=H)
    url = f"/api/workitem/{ITEM}/acceptance-proposal"

    for mode in (None, "attested"):
        set_mode(mode)
        r = bot.post(url, json={"acceptance": ["D1", "D2"], "note": "split the criterion"}, headers=H)
        check(f"bot proposes with the setting {mode or 'unset'}: 201", r.status_code == 201,
              f"{r.status_code} {r.text[:140]}")
    set_mode(None)
    p_first_id = events("node.acceptance.propose")[-2].detail["proposal_id"]
    p = bot.get(url).json().get("pending") or {}
    check("proposing changed no criterion", criteria() == ["D0"], repr(criteria()))
    check("GET shows one pending proposal: proposed, base, note, proposer",
          p.get("proposed") == ["D1", "D2"] and p.get("base") == ["D0"]
          and p.get("note") == "split the criterion" and p.get("proposed_by", {}).get("username") == BOT,
          repr(p)[:200])
    ev = events("node.acceptance.propose")[-1]
    check("the second proposal superseded the first, and its event says so",
          ev.detail.get("supersedes") == p_first_id and p.get("id") != p_first_id, repr(ev.detail))
    with app.session() as s:
        first = s.get(models_graph.AcceptanceProposal, p_first_id)
        n_pending = s.scalar(select(func.count()).select_from(models_graph.AcceptanceProposal).where(
            models_graph.AcceptanceProposal.node_id == ITEM,
            models_graph.AcceptanceProposal.status == "pending"))
    check("...the first is kept as superseded, and exactly one is pending",
          first.status == "superseded" and n_pending == 1, f"{first.status} {n_pending}")
    pid = p.get("id")

    r = bot.post(url, json={"acceptance": ["D0"]}, headers=H)
    check("proposing the current list is a 409", r.status_code == 409, r.text[:120])
    r = bot.post(url, json={"acceptance": ["ok", "  "]}, headers=H)
    check("an empty criterion is a 400 naming the field",
              r.status_code == 400 and r.json().get("field") == "acceptance", r.text[:120])
    r = botr.post(url, json={"acceptance": ["read-only"]}, headers=H)
    check("a read-scoped token cannot propose (scope)", r.status_code == 403, r.text[:120])

    for verb in ("approve", "reject"):
        r = bot.post(f"{url}/{pid}/{verb}", json={}, headers=H)
        check(f"a token cannot {verb}, whatever its scopes: 403 decision_needs_a_person",
              r.status_code == 403 and r.json().get("error") in ("decision_needs_a_person", "forbidden"),
              r.text[:120])
    r = c.post(f"{url}/{pid}/approve", json={}, headers=H)
    check("a session without its CSRF token cannot approve", r.status_code == 403, r.text[:120])
    r = c.post(f"{url}/{p_first_id}/approve", json={"csrf_token": csrf}, headers=H)
    check("approving the SUPERSEDED proposal is a 409, not a silent apply",
          r.status_code == 409 and criteria() == ["D0"], r.text[:120])
    r = c.post(f"{url}/999999/approve", json={"csrf_token": csrf}, headers=H)
    check("an unknown proposal id is a 404", r.status_code == 404, r.text[:120])
    check("still nothing applied", criteria() == ["D0"], repr(criteria()))

    updates_before = len(events("node.update"))
    r = c.post(f"{url}/{pid}/approve", json={"csrf_token": csrf}, headers=H)
    check("a session's Approve: 200, and the proposed list is now the acceptance",
          r.status_code == 200 and criteria() == ["D1", "D2"], f"{r.status_code} {r.text[:160]}")
    j = r.json() if r.status_code == 200 else {}
    check("...the proposal is approved and names the approver",
          (j.get("proposal") or {}).get("status") == "approved"
          and ((j.get("proposal") or {}).get("decided_by") or {}).get("username") == OWNER, repr(j)[:200])
    ev = events("node.acceptance.approve")
    check("...node.acceptance.approve records the approver as actor, the proposer, old and new",
          len(ev) == 1 and ev[0].actor_kind == "session" and ev[0].actor_subject == OWNER
          and ev[0].detail.get("proposed_by") == BOT and ev[0].detail.get("old") == ["D0"]
          and ev[0].detail.get("new") == ["D1", "D2"], repr(ev[0].detail if ev else None))
    check("...and it went through the normal session PATCH (a node.update naming acceptance, by the person)",
          len(events("node.update")) == updates_before + 1
          and events("node.update")[-1].actor_subject == OWNER
          and "acceptance" in events("node.update")[-1].detail.get("fields", []))
    check("GET has no pending proposal after the approve", bot.get(url).json().get("pending") is None)
    r = c.post(f"{url}/{pid}/approve", json={"csrf_token": csrf}, headers=H)
    check("approving it twice is a 409", r.status_code == 409, r.text[:120])

    r = bot.post(url, json={"acceptance": ["rejected idea"]}, headers=H)
    rid = r.json().get("id")
    r = c.post(f"{url}/{rid}/reject", json={"csrf_token": csrf}, headers=H)
    check("a session's Reject: 200, the acceptance untouched",
          r.status_code == 200 and criteria() == ["D1", "D2"]
          and (r.json().get("proposal") or {}).get("status") == "rejected", r.text[:160])
    check("...recorded, and nothing pending", len(events("node.acceptance.reject")) == 1
          and bot.get(url).json().get("pending") is None)


def section_e(botm: TestClient, owner, member) -> None:
    print("\n== E. a proposal on a hidden item is invisible like the item ==")
    oc, ocsrf = owner
    mc, mcsrf = member
    absent = f"TASK-never-{SUFFIX}"

    def same_404(r_hidden, r_absent) -> bool:
        return (r_hidden.status_code == r_absent.status_code == 404
                and r_hidden.text.replace(HIDDEN, "ID") == r_absent.text.replace(absent, "ID"))

    check("precondition: the member cannot see the item", mc.get(f"/api/node/{HIDDEN}").status_code == 404)
    r = oc.post(f"/api/workitem/{HIDDEN}/acceptance-proposal", json={"acceptance": ["secret"], "csrf_token": ocsrf},
                headers=H)
    check("precondition: the owner can propose on it", r.status_code == 201, r.text[:120])
    pid = r.json().get("id")

    for label, call in (
        ("GET", lambda nid: botm.get(f"/api/workitem/{nid}/acceptance-proposal")),
        ("member-agent propose", lambda nid: botm.post(f"/api/workitem/{nid}/acceptance-proposal",
                                                       json={"acceptance": ["x"]}, headers=H)),
        ("member GET", lambda nid: mc.get(f"/api/workitem/{nid}/acceptance-proposal")),
        ("member approve", lambda nid: mc.post(f"/api/workitem/{nid}/acceptance-proposal/{pid}/approve",
                                               json={"csrf_token": mcsrf}, headers=H)),
        ("member reject", lambda nid: mc.post(f"/api/workitem/{nid}/acceptance-proposal/{pid}/reject",
                                              json={"csrf_token": mcsrf}, headers=H)),
    ):
        rh, ra = call(HIDDEN), call(absent)
        check(f"{label}: hidden and absent are the same 404", same_404(rh, ra),
              f"{rh.status_code} {rh.text[:80]} | {ra.status_code} {ra.text[:80]}")
    set_mode("attested")
    try:
        rh = botm.patch(f"/api/workitem/{HIDDEN}", json={"acceptance": ["x"], "approval": {"by": OWNER, "quote": "q"}},
                        headers=H)
        ra = botm.patch(f"/api/workitem/{absent}", json={"acceptance": ["x"], "approval": {"by": OWNER, "quote": "q"}},
                        headers=H)
        check("attested write on a hidden item: the same 404 as an absent one", same_404(rh, ra),
              f"{rh.status_code} | {ra.status_code}")
    finally:
        set_mode(None)
    check("the hidden item's proposal is still pending and its acceptance unchanged",
          criteria(HIDDEN) == ["ORIGINAL"]
          and (oc.get(f"/api/workitem/{HIDDEN}/acceptance-proposal").json().get("pending") or {}).get("id") == pid)

    total_before = mc.get("/api/events").json().get("total")
    owner_before = oc.get("/api/events").json().get("total")
    r = oc.post(f"/api/workitem/{HIDDEN}/acceptance-proposal",
                json={"acceptance": ["secret 2"], "csrf_token": ocsrf}, headers=H)
    oc.post(f"/api/workitem/{HIDDEN}/acceptance-proposal/{r.json().get('id')}/reject",
            json={"csrf_token": ocsrf}, headers=H)
    check("the member's event total does not move while the owner's rises",
          mc.get("/api/events").json().get("total") == total_before
          and oc.get("/api/events").json().get("total") > owner_before,
          f"member {total_before}, owner {owner_before}")


def main() -> int:
    app.startup()
    tokens = setup()
    bot, botm, botr = token_client(tokens["bot"]), token_client(tokens["botm"]), token_client(tokens["botr"])
    owner, member = session_client(OWNER), session_client(MEMBER)

    section_a()
    section_b(bot)
    section_c(bot, owner)
    section_d(bot, botr, owner)
    section_e(botm, owner, member)

    print()
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} check(s): " + "; ".join(FAILURES))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
