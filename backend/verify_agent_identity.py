"""
Verification for STORY-atlas-agent-identity — an agent's account is its own,
linked to the person accountable for it (ADR 0034).

TASK-1112 built the `auth_user.person_user_id` column, the one rule that decides
who may be linked to whom, and the `admin bot` commands that set and clear it.
TASK-1114 then made GRANTS inherit down that link while ROLE does not. The third
sibling extends this file rather than starting its own:

TASK-1113 then made offboarding reach machines. All three are built. Section H
covers the story that follows them, `STORY-atlas-agent-credentials`, whose
whole premise is that everything above happens without an operator.

No pytest suite exists in this repo (see `verify_object_graph.py`'s docstring for
why), so this is the same standalone run-it-and-read-it convention as its
siblings, against a real disposable Postgres via `testdb` (TASK-1044).

TEN SECTIONS:

  A. THE RULE — `models_auth.refuse_person_link`, pure Python, no database. It
     is the one place both `admin bot link` and the future credential screen ask
     "may this link exist", so it is tested where it lives rather than only
     through a caller.

  B. THE MIGRATION, which is the check a fresh `create_all` cannot make. Live's
     `auth_user` is populated and predates this column. This section builds that
     exact PRE-shape (the real table, minus one column), puts a real bot row in
     it, and then runs the real boot sequence — so "the column arrives on a
     populated table and the row survives" is measured, not assumed from the
     model. AGENTS.md: a schema change that passes against a fresh database can
     still fail on deploy.

  C. THE COMMANDS — `bot add`, `bot add --person`, `bot link`, `bot unlink`,
     `bot list --json`, driven through `admin.main()` exactly as an operator
     types them, including the two refusals.

  D. THE AUDIT ROWS. Linking is an operator action on an account; if it is not
     in `auth_event` it did not happen, as far as anybody reconstructing an
     access decision later is concerned.

  E. WHAT THE LINK DOES, AND THE ONE THING IT MUST NEVER DO. Since TASK-1114
     grants inherit down the link, so a member's agent sees what that member
     sees with nothing maintained by hand. Role does NOT inherit, and that is
     the safety property the whole ADR turns on: an OWNER holds no grants, so
     an owner's agent inherits an EMPTY set and must not gain the owner's
     unrestricted view. Section A asserts that structurally — `Principal`
     carries the person's id and no role — and this section asserts it in
     behaviour.

  F. THE SAME THING OVER HTTP with a real service token, because section E
     hand-builds its principal and therefore cannot catch `_resolve_token`
     failing to carry the link off the bot's row — a bug that passes a unit
     test and 404s in production.

  G. OFFBOARDING REACHING MACHINES (TASK-1113): disabling a person disables
     their agents, re-enabling them deliberately does NOT, and an agent with no
     person is untouched by anybody's disable. Every check is an HTTP status
     from a real token, because "the row says disabled" is not the claim.

  H. SELF-SERVICE CREDENTIALS (TASK-1115): a contributor mints their own agent
     from a real browser session — the scope ceiling, the re-authentication, the
     mandatory expiry, shown-once, and the grant ceiling asserted rather than
     assumed. The minted token is then USED, because a credential that
     authenticates is the only proof the route worked.

  I. THE CREDENTIAL LIFECYCLE (TASK-1116): listing, ROTATION and revoke. The
     rotation half exists because usernames are permanent, so first-issue 409s
     on an agent that already exists — without it, revoking leaves a contributor
     inventing a second agent name or asking an operator. Two credentials live
     at once is the property being proven: mint, deploy, confirm, revoke, with
     no outage window.

  J. `token add` never prints a raw token by default: it refuses before minting
     unless told `--out -`, `--out PATH` or `--print` (2026-09-15).

Run (from a venv with the backend requirements installed):
    python verify_agent_identity.py
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb  # noqa: E402

DB = testdb.start()
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

from sqlalchemy import select  # noqa: E402

import admin  # noqa: E402
import app  # noqa: E402
import auth  # noqa: E402
import models_auth  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthEvent, AuthUser, NodeGrant, NodeVisibility  # noqa: E402
from models_graph import Node  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def run_admin(*argv: str) -> tuple[int, str]:
    """Drive the CLI the way an operator does, and capture what it printed.

    `admin.main()` and not the `cmd_*` function directly: the argument parser is
    half of the interface being tested here (`--person` has to reach `bot add`,
    `bot link` has to take two positionals), and calling the handler with a
    hand-built Namespace would prove the handler while leaving the wiring
    unproven. `main()` also converts an `AdminError` into an exit code and a
    message, which is what a refusal actually looks like.
    """
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = admin.main(list(argv))
    return code, buf.getvalue()


# --------------------------------------------------------------------------- #
# A. The rule
# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. refuse_person_link: the one rule both callers ask --")

    def acct(subject: str, service: bool) -> AuthUser:
        # Detached instances, never added to a session: the rule is about two
        # rows' shapes and must not need a database to answer.
        return AuthUser(id="u-" + subject, subject=subject, is_service=service)

    bot = acct("bot-agent", True)
    person = acct("avery", False)
    other_bot = acct("bot-other", True)

    check("an agent may be linked to a person",
          models_auth.refuse_person_link(bot, person) is None)

    refusal = models_auth.refuse_person_link(person, bot)
    check("a PERSON may not be given a person — the link is an agent's, not a human's",
          refusal is not None and "person, not an agent" in refusal, str(refusal))

    refusal = models_auth.refuse_person_link(bot, other_bot)
    check("an agent may not stand behind another agent",
          refusal is not None and "agent, not a person" in refusal, str(refusal))

    refusal = models_auth.refuse_person_link(bot, bot)
    check("an agent may not be its own person", refusal is not None, str(refusal))

    # THE GUARD THAT FAILS THE BUILD (TASK-1114's own wording). Grants inherit
    # down the person link and ROLE NEVER DOES, and the way that is guaranteed
    # is structural: `Principal` carries the person's ID and nothing else, so
    # there is no role to consult through the link. A future field holding the
    # person's role — or their whole row — would silently make an OWNER's agent
    # unrestricted, which is the single outcome ADR 0034 exists to prevent. It
    # would not fail any behavioural test until somebody read it, so the field
    # list itself is asserted here.
    import dataclasses
    person_fields = [f.name for f in dataclasses.fields(auth.Principal)
                     if "person" in f.name]
    check("Principal carries the person's ID and NOTHING else — role cannot "
          "be inherited because there is nothing to read",
          person_fields == ["person_user_id"], str(person_fields))

    owners_agent = auth.Principal(kind="token", user_id="u-bot", subject="bot",
                                  role=models_auth.ROLE_MEMBER, is_service=True,
                                  must_change_password=False, scopes=("read",),
                                  person_user_id="u-an-owner")
    check("an agent linked to an owner is NOT is_owner", owners_agent.is_owner is False)


# --------------------------------------------------------------------------- #
# B. The migration — the ALTER against a POPULATED auth_user
# --------------------------------------------------------------------------- #
def section_b() -> None:
    print("\n-- B. the column reaches a POPULATED auth_user, the way live is --")
    import sqlalchemy as sa
    from sqlalchemy import insert
    from sqlalchemy.orm import Session

    eng = app.engine  # the module-global `_reconcile_graph_schema()` actually reads
    check("app.engine is bound to Postgres, not something forgiving",
          str(eng.url).startswith("postgresql"), str(eng.url))

    DB.reset_schema()

    # The PRE-shape: the REAL auth_user, minus exactly one column. `to_metadata`
    # copies the actual Table object, so this is byte-identical to live's table
    # apart from the deliberate omission — a hand-written approximation would
    # prove the approximation.
    pre_md = sa.MetaData()
    for table in models_auth.Base.metadata.sorted_tables:
        t2 = table.to_metadata(pre_md)
        if table.name == "auth_user":
            t2._columns.remove(t2.c.person_user_id)
    pre_md.create_all(eng)

    cols_before = {c["name"] for c in sa.inspect(eng).get_columns("auth_user")}
    check("PRE-shape: auth_user exists and has NO person_user_id",
          "auth_user" in sa.inspect(eng).get_table_names()
          and "person_user_id" not in cols_before)

    # A real row, so the ALTER runs against a table that is not empty. An empty
    # table's ALTER proves nothing; the populated one is the live case.
    with Session(eng) as s:
        s.execute(insert(AuthUser.__table__).values(
            id="u-pre-bot", idp="local", subject="bot-pre-existing", role="member",
            is_service=True, label="bot-pre-existing", must_change_password=False,
            created_at=datetime.now(timezone.utc)))
        s.commit()

    # The real boot sequence, in the real order.
    models_auth.create_all(eng)
    app._reconcile_graph_schema()

    cols_after = {c["name"] for c in sa.inspect(eng).get_columns("auth_user")}
    check("person_user_id now exists on the ALREADY-POPULATED auth_user",
          "person_user_id" in cols_after, sorted(cols_after - cols_before))
    with Session(eng) as s:
        row = s.get(AuthUser, "u-pre-bot")
        check("the pre-existing bot row SURVIVED the ALTER",
              row is not None and row.subject == "bot-pre-existing")
        check("...and its link is NULL — nothing was guessed from its name",
              row is not None and row.person_user_id is None,
              "" if row is None else str(row.person_user_id))

    # Idempotent: a second boot must be a no-op, not a second ALTER.
    app._reconcile_graph_schema()
    check("a second boot re-runs cleanly (the reconciler is idempotent)",
          "person_user_id" in {c["name"] for c in sa.inspect(eng).get_columns("auth_user")})

    # The auth tables deliberately survive a reseed (see models_auth's docstring
    # on the reseed asymmetry), so this fixture would otherwise turn up as a
    # third agent in section C's listing. Remove it here rather than teaching a
    # later section about a row it has no reason to know exists.
    with Session(eng) as s:
        s.delete(s.get(AuthUser, "u-pre-bot"))
        s.commit()


# --------------------------------------------------------------------------- #
# C. The commands
# --------------------------------------------------------------------------- #
def section_c() -> None:
    print("\n-- C. admin bot add / link / unlink / list --")
    code, _ = run_admin("user", "add", "avery", "--role", "member", "--no-password")
    check("a person exists to be accountable", code == 0)
    code, _ = run_admin("user", "add", "bjorn", "--role", "member", "--no-password")
    check("a second person exists (a handover needs somewhere to go)", code == 0)

    code, out = run_admin("bot", "add", "bot-unlinked")
    check("`bot add` still works with no --person", code == 0, out.strip().splitlines()[:1])
    check("...and SAYS the link is missing rather than leaving it silent",
          "bot link" in out, out)

    code, out = run_admin("bot", "add", "bot-avery", "--person", "avery")
    check("`bot add --person` records the link at creation", code == 0, out)
    check("...and names the person back", "avery" in out, out)
    with app.session() as s:
        bot = s.scalar(select(AuthUser).where(AuthUser.subject == "bot-avery"))
        person = s.scalar(select(AuthUser).where(AuthUser.subject == "avery"))
        check("the stored link points at the person's row",
              bot is not None and person is not None and bot.person_user_id == person.id)

    code, out = run_admin("bot", "link", "bot-unlinked", "avery")
    check("`bot link` sets the link on an existing agent", code == 0, out)
    # THE MESSAGE STATES SEMANTICS, NOT TASK NUMBERS. Its first version named
    # two unbuilt tasks and was wrong within the day TASK-1114 landed — caught
    # by running the command on staging, not by any check. What a link MEANS
    # does not go stale, so that is what is asserted.
    check("...and says what the link now DOES: grants inherit, role never does",
          "GRANTS, never ROLE" in out and "sees what" in out, out)
    check("...and warns that disabling the person will disable the agent",
          "will disable this agent too" in out, out)

    # A handover: re-pointing must announce what it replaced, because it moves
    # what that agent will see the moment grants inherit.
    code, out = run_admin("bot", "link", "bot-unlinked", "bjorn")
    check("re-linking an agent to another person is allowed", code == 0, out)
    check("...and REPORTS the person it replaced", "was: avery" in out, out)

    code, out = run_admin("bot", "link", "avery", "bjorn")
    check("linking a PERSON is refused", code != 0, out)

    code, out = run_admin("bot", "link", "bot-avery", "bot-unlinked")
    check("linking an agent to another AGENT is refused", code != 0, out)

    code, out = run_admin("bot", "unlink", "bot-unlinked")
    check("`bot unlink` clears it", code == 0, out)
    with app.session() as s:
        bot = s.scalar(select(AuthUser).where(AuthUser.subject == "bot-unlinked"))
        check("...and the stored link is NULL again",
              bot is not None and bot.person_user_id is None)

    code, out = run_admin("bot", "unlink", "bot-unlinked")
    check("unlinking twice is not an error, it is a no-op that says so",
          code == 0 and "nothing to clear" in out, out)

    code, out = run_admin("bot", "list", "--json")
    listing = json.loads(out)
    by_name = {row["username"]: row for row in listing}
    check("`bot list` lists agents and only agents",
          set(by_name) == {"bot-avery", "bot-unlinked"}, sorted(by_name))
    check("...carrying the resolved person", by_name["bot-avery"]["person"] == "avery",
          str(by_name["bot-avery"]))
    check("...and reporting an unlinked agent as unlinked",
          by_name["bot-unlinked"]["person"] is None, str(by_name["bot-unlinked"]))

    code, out = run_admin("bot", "list")
    check("the human-readable listing NAMES what still needs a decision",
          "bot-unlinked" in out.split("no person recorded for:")[-1], out)

    # A DANGLING link must stay readable. The column is bare text on purpose
    # (there is no FK — see models_auth), so this state is reachable, and the
    # answer is "no person", never a uuid rendered where a username belongs.
    with app.session() as s:
        bot = s.scalar(select(AuthUser).where(AuthUser.subject == "bot-avery"))
        bot.person_user_id = "u-no-such-account"
        s.commit()
    code, out = run_admin("bot", "list", "--json")
    dangling = {row["username"]: row for row in json.loads(out)}["bot-avery"]
    check("a dangling link resolves to NO person, not to a raw id",
          dangling["person"] is None, str(dangling))
    check("...while the stored id is still reported, so it is diagnosable",
          dangling["person_user_id"] == "u-no-such-account", str(dangling))
    with app.session() as s:
        bot = s.scalar(select(AuthUser).where(AuthUser.subject == "bot-avery"))
        person = s.scalar(select(AuthUser).where(AuthUser.subject == "avery"))
        bot.person_user_id = person.id
        s.commit()


# --------------------------------------------------------------------------- #
# D. The audit
# --------------------------------------------------------------------------- #
def section_d() -> None:
    print("\n-- D. linking is an operator action, so it is in auth_event --")
    with app.session() as s:
        events = [e.event for e in s.scalars(select(AuthEvent)).all()]
    check("a link writes bot_person_linked", "bot_person_linked" in events, sorted(set(events)))
    check("an unlink writes bot_person_unlinked", "bot_person_unlinked" in events,
          sorted(set(events)))
    # The column carries no CHECK by design, so an unknown name would be written
    # happily and never noticed. The vocabulary list is the only thing that
    # catches a typo, which is why it is asserted rather than assumed.
    unknown = sorted(set(events) - set(models_auth.EVENTS))
    check("every event name written here is in models_auth.EVENTS", not unknown, str(unknown))


# --------------------------------------------------------------------------- #
# E. What the link does NOT do
# --------------------------------------------------------------------------- #
def section_e() -> None:
    print("\n-- E. grants inherit down the link; role never does --")
    with app.session() as s:
        s.add(Node(id="e-private", type="project", name="e-private", attributes={},
                   attention_count=0, completion=0.0, origin="atlas"))
        # A SHARED node beside it, so "the agent cannot see the private project"
        # is distinguishable from "the agent can see nothing at all". With one
        # node in the graph those two states are the same empty set, and a check
        # that cannot tell correct from broken proves neither.
        s.add(Node(id="e-shared", type="project", name="e-shared", attributes={},
                   attention_count=0, completion=0.0, origin="atlas"))
        s.add(NodeVisibility(node_id="e-private",
                             visibility=models_auth.VISIBILITY_PRIVATE))
        owner = AuthUser(id="u-e-owner", subject="e-owner", role="owner",
                         is_service=False, must_change_password=False,
                         created_at=datetime.now(timezone.utc))
        member = AuthUser(id="u-e-member", subject="e-member", role="member",
                          is_service=False, must_change_password=False,
                          created_at=datetime.now(timezone.utc))
        owner_bot = AuthUser(id="u-e-owner-bot", subject="bot-e-owner", role="member",
                             is_service=True, label="bot-e-owner",
                             must_change_password=False, person_user_id=owner.id,
                             created_at=datetime.now(timezone.utc))
        member_bot = AuthUser(id="u-e-member-bot", subject="bot-e-member", role="member",
                              is_service=True, label="bot-e-member",
                              must_change_password=False, person_user_id=member.id,
                              created_at=datetime.now(timezone.utc))
        s.add_all([owner, member, owner_bot, member_bot])
        # The member IS granted the private project. Their agent is not.
        s.add(NodeGrant(node_id="e-private", user_id=member.id))
        s.commit()

    def visible_to(user_id: str, role: str, person: str | None = None) -> set[str] | None:
        token = auth.current_principal.set(auth.Principal(
            kind="bearer", user_id=user_id, subject=user_id, role=role,
            is_service=True, must_change_password=False, scopes=("read",),
            person_user_id=person))
        try:
            with app.session() as s:
                return app._visible_node_ids(s)
        finally:
            auth.current_principal.reset(token)

    # THE PERMANENT HALF. An agent's own row is `role = member`, and linking it
    # to an OWNER must never change that. An owner holds no grants — an owner is
    # unrestricted BY ROLE — so an owner's agent inheriting grants inherits an
    # EMPTY set. This check must still pass after TASK-1114 lands. If making
    # inheritance work requires loosening it, inheritance has been built wrong.
    seen = visible_to("u-e-owner-bot", "member", person="u-e-owner")
    check("AN OWNER'S AGENT DOES NOT SEE THE OWNER'S PRIVATE PROJECT — role is "
          "never inherited (survived TASK-1114)",
          seen is not None and "e-private" not in seen, str(seen))
    check("...and it is not simply blind: it sees the SHARED project",
          seen is not None and "e-shared" in seen, str(seen))

    owner_seen = visible_to("u-e-owner", "owner")
    check("...while the owner themselves is unrestricted, so the fixture is real",
          owner_seen is None, str(owner_seen))

    # INVERTED BY TASK-1114, exactly as this file said it would be, and the one
    # above was not touched. A member's agent now inherits that member's grant,
    # so the drift is gone: the agent sees what its operator sees, with nothing
    # maintained by hand.
    seen = visible_to("u-e-member-bot", "member", person="u-e-member")
    check("A MEMBER'S AGENT INHERITS ITS PERSON'S GRANT — no per-agent grant "
          "maintained by hand",
          seen is None or "e-private" in seen, str(seen))

    # The link is what does it, not the fact of being a service account: an
    # agent with NO person recorded inherits nothing, which is what keeps the
    # four pre-existing bots inert until somebody links them deliberately.
    seen = visible_to("u-e-member-bot", "member", person=None)
    check("...and an UNLINKED agent inherits nothing",
          seen is not None and "e-private" not in seen and "e-shared" in seen, str(seen))

    # A dangling link — the column is bare text with no FK, so this state is
    # reachable — matches no grant row and contributes nothing. Fail-safe.
    seen = visible_to("u-e-member-bot", "member", person="u-no-such-person")
    check("...and a DANGLING link inherits nothing rather than failing open",
          seen is not None and "e-private" not in seen, str(seen))

    # An OWN grant still applies, so an agent can be given something its operator
    # does not have. Union, not replacement.
    with app.session() as s:
        s.add(Node(id="e-agent-only", type="project", name="e-agent-only", attributes={},
                   attention_count=0, completion=0.0, origin="atlas"))
        s.flush()
        s.add(NodeVisibility(node_id="e-agent-only",
                             visibility=models_auth.VISIBILITY_PRIVATE))
        s.add(NodeGrant(node_id="e-agent-only", user_id="u-e-owner-bot"))
        s.commit()
    seen = visible_to("u-e-owner-bot", "member", person="u-e-owner")
    check("an agent's OWN grant still applies — it may hold what its operator does not",
          seen is None or "e-agent-only" in seen, str(seen))
    upward = visible_to("u-e-member", "member")
    check("...and that grant did NOT leak upward to a person",
          upward is not None and "e-agent-only" not in upward, str(upward))

    # SHARPER than the inheritance check above, which ran while `e-private` was
    # the only private node and could therefore pass with an unrestricted None.
    # Now there are two private projects and the member holds exactly one, so
    # inheritance has to be selective rather than generous.
    seen = visible_to("u-e-member-bot", "member", person="u-e-member")
    check("inheritance is SELECTIVE — the member's agent gets e-private (its "
          "person's) and not e-agent-only (somebody else's)",
          seen is not None and "e-private" in seen and "e-agent-only" not in seen,
          str(seen))

    member_seen = visible_to("u-e-member", "member")
    check("...while the member themselves does hold the grant — so the drift "
          "ADR 0034 describes is real and measured here",
          member_seen is None or "e-private" in member_seen, str(member_seen))


# --------------------------------------------------------------------------- #
# F. Over HTTP, with a REAL token
# --------------------------------------------------------------------------- #
def section_f() -> None:
    """The acceptance criterion in its own words: *demonstrated by granting the
    person and measuring the agent's token*.

    Section E drives `_visible_node_ids` directly, which is the right place to
    prove the RULE. It is the wrong place to prove the SYSTEM: the principal
    there is hand-built, so it cannot catch the failure where `_resolve_token`
    forgets to carry `person_user_id` off the bot's row and every agent silently
    inherits nothing. That gap is exactly the shape of a bug that passes a unit
    test and 404s in production, so this section mints a real service token and
    goes through the real gate.
    """
    print("\n-- F. a real Bearer token: the agent sees what its person is granted --")
    from fastapi.testclient import TestClient

    with app.session() as s:
        s.add(Node(id="f-private", type="project", name="f-private", attributes={},
                   attention_count=0, completion=0.0, origin="atlas"))
        s.add(Node(id="f-shared", type="project", name="f-shared", attributes={},
                   attention_count=0, completion=0.0, origin="atlas"))
        s.flush()
        s.add(NodeVisibility(node_id="f-private",
                             visibility=models_auth.VISIBILITY_PRIVATE))
        person = AuthUser(id="u-f-person", subject="f-person", role="member",
                          is_service=False, must_change_password=False,
                          created_at=datetime.now(timezone.utc))
        linked = AuthUser(id="u-f-linked", subject="bot-f-linked", role="member",
                          is_service=True, label="bot-f-linked",
                          must_change_password=False, person_user_id="u-f-person",
                          created_at=datetime.now(timezone.utc))
        loose = AuthUser(id="u-f-loose", subject="bot-f-loose", role="member",
                         is_service=True, label="bot-f-loose",
                         must_change_password=False,
                         created_at=datetime.now(timezone.utc))
        s.add_all([person, linked, loose])
        s.flush()
        raw_linked, _ = auth.mint_service_token(s, owner=linked, scopes=["read"],
                                                label="f-linked")
        raw_loose, _ = auth.mint_service_token(s, owner=loose, scopes=["read"],
                                               label="f-loose")
        s.commit()

    def client(raw):
        c = TestClient(app.app, base_url="https://testserver")
        c.headers.update({"Authorization": "Bearer " + raw})
        return c

    linked_c, loose_c = client(raw_linked), client(raw_loose)

    # BEFORE the grant: the person holds nothing, so neither agent sees it. This
    # is the baseline that makes the next check mean something — without it, an
    # agent that could see everything would pass the "after" check too.
    r = linked_c.get("/api/node/f-private")
    check("before the grant, the linked agent 404s on the private project",
          r.status_code == 404, f"got {r.status_code}")

    with app.session() as s:
        s.add(NodeGrant(node_id="f-private", user_id="u-f-person"))
        s.commit()

    r = linked_c.get("/api/node/f-private")
    check("GRANT THE PERSON, AND THE AGENT'S TOKEN SEES IT — nothing was "
          "granted to the agent",
          r.status_code == 200, f"got {r.status_code}")

    r = loose_c.get("/api/node/f-private")
    check("...while an agent with no person recorded still 404s",
          r.status_code == 404, f"got {r.status_code}")

    r = loose_c.get("/api/node/f-shared")
    check("...and that agent is not simply broken: the shared project is 200",
          r.status_code == 200, f"got {r.status_code}")

    # The token principal itself must carry the link. If `_resolve_token` drops
    # it, every check above still passes for the WRONG reason on a database
    # where the agent happens to hold its own grant — so assert the field, not
    # only the outcome.
    me = linked_c.get("/api/me")
    check("the token authenticates as the BOT, not as its person",
          me.status_code == 200 and me.json().get("username") == "bot-f-linked",
          str(me.json()))
    check("...and as a member, never inheriting the person's role",
          me.status_code == 200 and me.json().get("role") == "member", str(me.json()))

    # REVOKING AT THE PERSON REACHES THE AGENT. Not the disable cascade
    # (TASK-1113, unbuilt) — this is the grant coming back off the person, which
    # is the everyday case: an owner unticks a person in the grid and their
    # agents lose it in the same click.
    with app.session() as s:
        row = s.get(NodeGrant, ("f-private", "u-f-person"))
        if row is not None:
            s.delete(row)
        s.commit()
    r = linked_c.get("/api/node/f-private")
    check("REMOVE THE PERSON'S GRANT AND THE AGENT LOSES IT TOO, same click",
          r.status_code == 404, f"got {r.status_code}")


# --------------------------------------------------------------------------- #
# G. Offboarding reaches machines
# --------------------------------------------------------------------------- #
def section_g() -> None:
    """TASK-1113. The acceptance says *measured with curl, not asserted from the
    row* — so every check here is an HTTP status from a real token through the
    real gate, and the one row assertion left is the asymmetry, which is about
    what is stored rather than what is served.
    """
    print("\\n-- G. disabling a person disables their agents --")
    from fastapi.testclient import TestClient

    with app.session() as s:
        person = AuthUser(id="u-g-person", subject="g-person", role="member",
                          is_service=False, must_change_password=False,
                          created_at=datetime.now(timezone.utc))
        bot_a = AuthUser(id="u-g-bot-a", subject="bot-g-a", role="member",
                         is_service=True, label="bot-g-a", must_change_password=False,
                         person_user_id="u-g-person",
                         created_at=datetime.now(timezone.utc))
        bot_b = AuthUser(id="u-g-bot-b", subject="bot-g-b", role="member",
                         is_service=True, label="bot-g-b", must_change_password=False,
                         person_user_id="u-g-person",
                         created_at=datetime.now(timezone.utc))
        # A bystander: nobody's agent. It must be untouched by anyone's disable,
        # which is what keeps the four pre-existing live bots working.
        loner = AuthUser(id="u-g-loner", subject="bot-g-loner", role="member",
                         is_service=True, label="bot-g-loner", must_change_password=False,
                         created_at=datetime.now(timezone.utc))
        s.add_all([person, bot_a, bot_b, loner])
        s.flush()
        raw_a, _ = auth.mint_service_token(s, owner=bot_a, scopes=["read"], label="g-a")
        raw_b, _ = auth.mint_service_token(s, owner=bot_b, scopes=["read"], label="g-b")
        raw_l, _ = auth.mint_service_token(s, owner=loner, scopes=["read"], label="g-loner")
        s.commit()

    def status(raw: str) -> int:
        c = TestClient(app.app, base_url="https://testserver")
        c.headers.update({"Authorization": "Bearer " + raw})
        return c.get("/api/me").status_code

    check("baseline: all three agents authenticate",
          status(raw_a) == 200 and status(raw_b) == 200 and status(raw_l) == 200,
          f"a={status(raw_a)} b={status(raw_b)} loner={status(raw_l)}")

    code, out = run_admin("user", "disable", "g-person")
    check("`user disable` succeeds", code == 0, out)
    check("...and NAMES the agents it took down, rather than counting them",
          "bot-g-a" in out and "bot-g-b" in out, out)
    check("...and says the re-enable is not symmetric",
          "does NOT bring these back" in out, out)

    check("A DISABLED PERSON'S AGENT IS REFUSED — 401 from the real gate",
          status(raw_a) == 401 and status(raw_b) == 401,
          f"a={status(raw_a)} b={status(raw_b)}")
    check("...while an agent with no person recorded is untouched",
          status(raw_l) == 200, f"got {status(raw_l)}")

    with app.session() as s:
        rows = {u.subject: u.disabled_at for u in s.scalars(
            select(AuthUser).where(AuthUser.subject.in_(
                ["bot-g-a", "bot-g-b", "bot-g-loner"]))).all()}
    check("the cascade wrote real rows (not only an auth-time refusal)",
          rows["bot-g-a"] is not None and rows["bot-g-b"] is not None, str(rows))
    check("...and did not touch the unlinked agent's row",
          rows["bot-g-loner"] is None, str(rows))

    # THE ASYMMETRY. This is the check that would fail if the cascade were only
    # an auth-time test of the person's `disabled_at`: re-enabling the person
    # would silently re-arm every agent at once.
    code, out = run_admin("user", "enable", "g-person")
    check("`user enable` succeeds", code == 0, out)
    check("...and NAMES the agents that stay down, with the command to fix each",
          "bot-g-a" in out and "user enable bot-g-a" in out, out)
    check("RE-ENABLING THE PERSON DOES NOT RE-ARM THEIR AGENTS — still 401",
          status(raw_a) == 401 and status(raw_b) == 401,
          f"a={status(raw_a)} b={status(raw_b)}")

    code, out = run_admin("user", "enable", "bot-g-a")
    check("each agent is brought back deliberately, one command", code == 0, out)
    check("...and only that one comes back",
          status(raw_a) == 200 and status(raw_b) == 401,
          f"a={status(raw_a)} b={status(raw_b)}")

    # THE ROUTE THE CASCADE CANNOT SEE. `bot link` attaches an agent to an
    # already-disabled person, and no `user disable` will ever revisit it — the
    # cascade already ran. `auth._resolve_token` checking the person is what
    # closes it, and the same second line covers the login-throttle hard-disable
    # (`_record_login_failure` stamps `disabled_at` on a person directly).
    with app.session() as s:
        s.get(AuthUser, "u-g-person").disabled_at = datetime.now(timezone.utc)
        s.get(AuthUser, "u-g-loner").person_user_id = "u-g-person"
        s.commit()
    check("AN AGENT LINKED TO AN ALREADY-DISABLED PERSON IS REFUSED TOO — the "
          "cascade never ran for it, the gate catches it anyway",
          status(raw_l) == 401, f"got {status(raw_l)}")
    with app.session() as s:
        check("...and its own row is still enabled, so the state is honest "
              "rather than rewritten behind the operator",
              s.get(AuthUser, "u-g-loner").disabled_at is None)

    code, out = run_admin("bot", "list")
    check("`bot list` SURFACES that state instead of showing a working agent",
          "REFUSED because their person is disabled" in out and "bot-g-loner" in out, out)


# --------------------------------------------------------------------------- #
# H. Self-service agent credentials
# --------------------------------------------------------------------------- #
def section_h() -> None:
    """TASK-1115. A contributor mints their own agent credential, over HTTP,
    through the real gate, with a real session and a real CSRF token.

    Driven as a BROWSER would drive it — cookie session plus synchronizer token
    — because that is the only principal kind the route accepts, and because a
    hand-built principal cannot exercise `guard_write`'s CSRF path at all.
    """
    print("\n-- H. a contributor mints their own agent credential --")
    from fastapi.testclient import TestClient

    ORIGIN = "https://testserver"
    MEMBER_PW = "Correct-Horse-Battery-Member-5!"
    OWNER_PW = "Correct-Horse-Battery-Owner-5!"

    with app.session() as s:
        for uid, subject, role, pw in (("u-h-member", "h-member", "member", MEMBER_PW),
                                       ("u-h-owner", "h-owner", "owner", OWNER_PW)):
            if not s.scalar(select(AuthUser).where(AuthUser.subject == subject)):
                s.add(AuthUser(id=uid, subject=subject, name=subject, role=role,
                               is_service=False, must_change_password=False,
                               password_hash=passwords.hash_password(pw),
                               created_at=datetime.now(timezone.utc)))
        s.commit()

    def sign_in(username: str, password: str):
        c = TestClient(app.app, base_url=ORIGIN)
        page = c.get("/auth/login")
        tok = page.text.split('name="csrf_token" value="')[1].split('"')[0]
        c.post("/auth/login",
               data={"username": username, "password": password, "csrf_token": tok},
               headers={"Origin": ORIGIN}, follow_redirects=False)
        return c, c.get("/api/me").json()["csrf_token"]

    member, member_csrf = sign_in("h-member", MEMBER_PW)
    owner, owner_csrf = sign_in("h-owner", OWNER_PW)

    def mint(client, csrf, **over):
        payload = {"purpose": "claude-code", "scopes": ["read"], "expires_days": 30,
                   "password": MEMBER_PW, "csrf_token": csrf}
        payload.update(over)
        return client.post("/api/me/agent-credential", json=payload,
                           headers={"Origin": ORIGIN})

    # ---- the happy path, which is the whole point of the story ------------
    r = mint(member, member_csrf)
    check("A MEMBER MINTS THEIR OWN CREDENTIAL, no shell and no operator",
          r.status_code == 201, f"{r.status_code} {r.text[:200]}")
    created = r.json() if r.status_code == 201 else {}
    raw = created.get("token", "")
    check("the response carries the raw token exactly once, flagged as such",
          bool(raw) and created.get("shown_once") is True, str(list(created)))
    check("the agent is named <person>-<purpose>, so two people cannot collide",
          created.get("agent") == "h-member-claude-code", str(created.get("agent")))
    check("...and it reports the person it belongs to",
          created.get("person") == "h-member", str(created.get("person")))
    check("...and a finite expiry", bool(created.get("expires_at")), str(created.get("expires_at")))

    # ---- the account behind it, which the contributor never manages -------
    with app.session() as s:
        bot = s.scalar(select(AuthUser).where(AuthUser.subject == "h-member-claude-code"))
    check("the bot account was created by the same call",
          bot is not None and bot.is_service, str(bot))
    check("...linked to the person who asked for it",
          bot is not None and bot.person_user_id == "u-h-member")
    check("...as a MEMBER, never inheriting the creator's role",
          bot is not None and bot.role == models_auth.ROLE_MEMBER)
    check("...holding no password, so it can never reach the login form",
          bot is not None and bot.password_hash is None)
    check("...and not carrying must_change_password, which would mint a dead token",
          bot is not None and not bot.must_change_password)

    # ---- THE TOKEN ACTUALLY WORKS, which is the only proof that matters ---
    agent = TestClient(app.app, base_url=ORIGIN)
    agent.headers.update({"Authorization": "Bearer " + raw})
    me = agent.get("/api/me")
    check("THE MINTED TOKEN AUTHENTICATES — as the bot, with the scope asked for",
          me.status_code == 200 and me.json().get("username") == "h-member-claude-code"
          and me.json().get("scopes") == ["read"], f"{me.status_code} {me.text[:200]}")

    # ---- shown once ------------------------------------------------------
    with app.session() as s:
        rows = s.scalars(select(models_auth.AuthServiceToken).where(
            models_auth.AuthServiceToken.owner_user_id == bot.id)).all()
    check("the raw token is NOT stored anywhere in clear",
          all(raw not in (row.token_hash or "") and raw not in (row.prefix or "")
              for row in rows), str([(r.prefix, r.token_hash[:8]) for r in rows]))

    # ---- the ceiling -----------------------------------------------------
    r = mint(member, member_csrf, purpose="admin-agent", scopes=["admin"])
    check("A MEMBER IS REFUSED `admin` — 400 naming its field",
          r.status_code == 400 and r.json().get("field") == "scopes",
          f"{r.status_code} {r.text[:200]}")
    r = mint(member, member_csrf, purpose="writer", scopes=["read", "write"])
    check("...but read+write is theirs to take", r.status_code == 201,
          f"{r.status_code} {r.text[:160]}")

    r = mint(owner, owner_csrf, purpose="admin-agent", scopes=["admin"],
             password=OWNER_PW)
    check("an OWNER may take `admin` deliberately", r.status_code == 201,
          f"{r.status_code} {r.text[:160]}")
    admin_raw = r.json().get("token", "") if r.status_code == 201 else ""

    r = mint(member, member_csrf, purpose="scopeless", scopes=[])
    check("no scope at all is refused — a scopeless token is broken, not safe",
          r.status_code == 400 and r.json().get("field") == "scopes",
          f"{r.status_code} {r.text[:160]}")

    # ---- re-authentication ----------------------------------------------
    r = mint(member, member_csrf, purpose="no-password", password="wrong-password")
    check("A LIVE SESSION IS NOT ENOUGH — the wrong password is refused",
          r.status_code == 403 and r.json().get("field") == "password",
          f"{r.status_code} {r.text[:160]}")
    with app.session() as s:
        fails = [e for e in s.scalars(select(AuthEvent)).all()
                 if (e.reason or "").startswith("agent_credential:")]
    check("...and the failure is recorded like any other credential failure",
          bool(fails), str([e.reason for e in fails]))
    with app.session() as s:
        leaked = s.scalar(select(AuthEvent).where(AuthEvent.detail.contains("wrong-password")))
    check("...with the submitted password nowhere in the audit log", leaked is None)

    # ---- expiry ----------------------------------------------------------
    for days, label in ((0, "zero"), (99999, "absurd"), (-1, "negative")):
        r = mint(member, member_csrf, purpose="p%s" % abs(days), expires_days=days)
        check("an expiry of %s is refused — every credential must expire" % label,
              r.status_code == 400 and r.json().get("field") == "expires_days",
              f"{r.status_code} {r.text[:120]}")

    # ---- a Bearer caller may not mint another credential -----------------
    # Driven with the ADMIN-scoped token deliberately. A read-scoped one is
    # refused by `guard_write` on the scope axis before it ever reaches the
    # kind check, which would prove the weaker claim; the admin token SATISFIES
    # the scope requirement and must still be refused. That is the claim worth
    # making: one leaked token is not a key factory whatever it is scoped for.
    admin_agent = TestClient(app.app, base_url=ORIGIN)
    admin_agent.headers.update({"Authorization": "Bearer " + admin_raw})
    ok = admin_agent.get("/api/me")
    check("the admin-scoped agent token authenticates, so the next check is real",
          ok.status_code == 200 and ok.json().get("scopes") == ["admin"],
          f"{ok.status_code} {ok.text[:160]}")
    r = admin_agent.post("/api/me/agent-credential",
                         json={"purpose": "second", "scopes": ["read"], "expires_days": 30,
                               "password": MEMBER_PW},
                         headers={"Origin": ORIGIN})
    check("AN ADMIN-SCOPED BEARER STILL CANNOT MINT — it passes the scope gate "
          "and is refused on KIND",
          r.status_code == 403 and r.json().get("error") == "session_required",
          f"{r.status_code} {r.text[:160]}")
    r = agent.post("/api/me/agent-credential",
                   json={"purpose": "third", "scopes": ["read"], "expires_days": 30,
                         "password": MEMBER_PW},
                   headers={"Origin": ORIGIN})
    check("...and a read-scoped one is stopped earlier still, on scope",
          r.status_code == 403 and "admin" in r.json().get("detail", ""),
          f"{r.status_code} {r.text[:160]}")

    # ---- CSRF ------------------------------------------------------------
    r = member.post("/api/me/agent-credential",
                    json={"purpose": "nocsrf", "scopes": ["read"], "expires_days": 30,
                          "password": MEMBER_PW},
                    headers={"Origin": ORIGIN})
    check("a session POST with no synchronizer token is refused",
          r.status_code == 403, f"{r.status_code} {r.text[:160]}")

    # ---- the password field's contract, unchanged by TASK-1415 ------------
    # With ATLAS_OIDC_STEP_UP unset (this script), the field is declared exactly
    # as before: FastAPI's own 422, naming body.password, BEFORE the write guard.
    nopw = {"purpose": "nopw", "scopes": ["read"], "expires_days": 30, "csrf_token": member_csrf}
    r = member.post("/api/me/agent-credential", json=nopw, headers={"Origin": ORIGIN})
    check("TASK-1415: no password is FastAPI's 422 naming body.password, the body unchanged",
          r.status_code == 422 and r.json() == {"detail": [{
              "type": "missing", "loc": ["body", "password"], "msg": "Field required",
              "input": nopw}]}, f"{r.status_code} {r.text[:200]}")
    r = member.post("/api/me/agent-credential", json={"purpose": "nopw", "scopes": ["read"]},
                    headers={"Origin": ORIGIN})
    check("TASK-1415: ...and still before the write guard (no password, no CSRF token: 422)",
          r.status_code == 422, f"{r.status_code} {r.text[:160]}")
    r = mint(member, member_csrf, purpose="wrongpw", password="not-" + MEMBER_PW)
    check("TASK-1415: a wrong password is 403 password_incorrect naming the field",
          r.status_code == 403 and r.json().get("error") == "password_incorrect"
          and r.json().get("field") == "password", f"{r.status_code} {r.text[:160]}")
    r = member.get("/api/me/agent-credentials")
    check("TASK-1415: the credential screen's response has no step-up keys",
          r.status_code == 200 and not {"reauth", "step_up_url", "agent_prefix"} & set(r.json()),
          str(sorted(r.json())))

    # ---- names are permanent, so a collision is reported not resolved -----
    r = mint(member, member_csrf, purpose="claude-code")
    check("re-using a purpose is a 409 rather than a silently renamed account",
          r.status_code == 409 and r.json().get("field") == "purpose",
          f"{r.status_code} {r.text[:160]}")

    r = mint(member, member_csrf, purpose="Not A Handle!")
    check("a purpose that cannot form a handle is 400 naming its field",
          r.status_code == 400 and r.json().get("field") == "purpose",
          f"{r.status_code} {r.text[:160]}")

    # ---- THE GRANT CEILING, asserted rather than assumed ------------------
    # The story asks for this explicitly: a member must not be able to give
    # their own agent visibility they do not have. It is true by construction
    # today — inheritance gives exactly the person's set, and PUT /api/access is
    # owner-only — but the two axes are independent and only ONE of them is
    # checked on this route, so it is stated here rather than left to luck.
    with app.session() as s:
        s.add(Node(id="h-private", type="project", name="h-private", attributes={},
                   attention_count=0, completion=0.0, origin="atlas"))
        s.flush()
        s.add(NodeVisibility(node_id="h-private",
                             visibility=models_auth.VISIBILITY_PRIVATE))
        s.commit()
    seen = agent.get("/api/node/h-private")
    check("A FRESHLY MINTED AGENT CANNOT SEE WHAT ITS PERSON CANNOT SEE",
          seen.status_code == 404, f"got {seen.status_code}")
    person_seen = member.get("/api/node/h-private")
    check("...and neither can the person, so the agent matched them exactly",
          person_seen.status_code == 404, f"got {person_seen.status_code}")

    with app.session() as s:
        s.add(NodeGrant(node_id="h-private", user_id="u-h-member"))
        s.commit()
    check("GRANT THE PERSON AND THE NEW AGENT FOLLOWS — no administration step",
          agent.get("/api/node/h-private").status_code == 200,
          f"got {agent.get('/api/node/h-private').status_code}")


# --------------------------------------------------------------------------- #
# I. Seeing, rotating and revoking your own credentials
# --------------------------------------------------------------------------- #
def section_i() -> None:
    """TASK-1116. The half that makes the screen worth having: without it,
    minting is a one-way door and the operator is back in the loop.

    THE ROTATION CHECKS ARE THE POINT. The owner asked "when we revoke my
    credentials we won't have issues creating the new?" — and the honest answer
    for the BOARD was no, because usernames are permanent and TASK-1115 409s on
    an agent that already exists. Everything below the listing checks exists
    because of that question.
    """
    print("\n-- I. see, rotate and revoke your own credentials --")
    from fastapi.testclient import TestClient

    ORIGIN = "https://testserver"
    PW = "Correct-Horse-Battery-Rotate-7!"
    OTHER_PW = "Correct-Horse-Battery-Other-7!"

    with app.session() as s:
        for uid, subject, pw in (("u-i-person", "i-person", PW),
                                 ("u-i-other", "i-other", OTHER_PW)):
            if not s.scalar(select(AuthUser).where(AuthUser.subject == subject)):
                s.add(AuthUser(id=uid, subject=subject, name=subject, role="member",
                               is_service=False, must_change_password=False,
                               password_hash=passwords.hash_password(pw),
                               created_at=datetime.now(timezone.utc)))
        s.commit()

    def sign_in(username, password):
        c = TestClient(app.app, base_url=ORIGIN)
        page = c.get("/auth/login")
        tok = page.text.split('name="csrf_token" value="')[1].split('"')[0]
        c.post("/auth/login", data={"username": username, "password": password,
                                    "csrf_token": tok},
               headers={"Origin": ORIGIN}, follow_redirects=False)
        return c, c.get("/api/me").json()["csrf_token"]

    me, csrf = sign_in("i-person", PW)
    other, other_csrf = sign_in("i-other", OTHER_PW)

    first = me.post("/api/me/agent-credential",
                    json={"purpose": "fleet", "scopes": ["read"], "expires_days": 30,
                          "password": PW, "csrf_token": csrf},
                    headers={"Origin": ORIGIN})
    check("a credential exists to work with", first.status_code == 201,
          f"{first.status_code} {first.text[:160]}")
    old_raw = first.json().get("token", "")
    agent_name = first.json().get("agent", "")

    # ---- the listing ------------------------------------------------------
    r = me.get("/api/me/agent-credentials")
    check("GET /api/me/agent-credentials -> 200", r.status_code == 200, str(r.status_code))
    data = r.json() if r.status_code == 200 else {}
    agents = {a["agent"]: a for a in data.get("agents", [])}
    check("the caller sees their own agent", agent_name in agents, str(list(agents)))
    cred = agents.get(agent_name, {}).get("credentials", [{}])[0]
    check("...with scopes, expiry and last-used",
          cred.get("scopes") == ["read"] and cred.get("expires_at")
          and "last_used_at" in cred, str(cred))
    check("...flagged never_used before anything has presented it",
          cred.get("never_used") is True and cred.get("live") is True, str(cred))
    check("THE TOKEN VALUE IS NOT IN THE LISTING",
          old_raw and old_raw not in r.text, "the raw secret must never be re-servable")
    check("...nor is the stored digest",
          "token_hash" not in r.text, r.text[:120])
    check("the screen is told what it may offer, so no control is guaranteed to fail",
          data.get("scopes_available") == ["read", "write"]
          and data.get("expiry_days", {}).get("min") == 1, str(data.get("scopes_available")))

    r = other.get("/api/me/agent-credentials")
    check("ANOTHER PERSON'S AGENTS ARE SIMPLY ABSENT, not filtered afterwards",
          r.status_code == 200 and r.json().get("agents") == [], r.text[:160])

    # ---- ROTATION: the answer to the question that prompted this ----------
    r = me.post("/api/me/agent-credential",
                json={"purpose": "fleet", "scopes": ["read"], "expires_days": 30,
                      "password": PW, "csrf_token": csrf},
                headers={"Origin": ORIGIN})
    check("creating the SAME agent again is still 409 — names are permanent",
          r.status_code == 409, f"{r.status_code} {r.text[:120]}")

    r = me.post("/api/me/agent/%s/credential" % agent_name,
                json={"purpose": "ignored", "scopes": ["read"], "expires_days": 30,
                      "password": PW, "csrf_token": csrf},
                headers={"Origin": ORIGIN})
    check("BUT A REPLACEMENT CREDENTIAL FOR THAT SAME AGENT SUCCEEDS — no new "
          "account, no operator",
          r.status_code == 201, f"{r.status_code} {r.text[:200]}")
    new_raw = r.json().get("token", "") if r.status_code == 201 else ""
    check("...returned once, like any other credential",
          bool(new_raw) and new_raw != old_raw and r.json().get("shown_once") is True)
    check("...and it SAYS the old one still works, which is the whole point",
          r.json().get("old_still_valid") is True
          and r.json().get("live_credentials") == 2, str(r.json()))

    def works(raw):
        c = TestClient(app.app, base_url=ORIGIN)
        c.headers.update({"Authorization": "Bearer " + raw})
        return c.get("/api/me").status_code

    check("BOTH CREDENTIALS AUTHENTICATE AT ONCE — mint, deploy, confirm, then "
          "revoke, with no outage window",
          works(old_raw) == 200 and works(new_raw) == 200,
          f"old={works(old_raw)} new={works(new_raw)}")

    # ---- rotation is no softer a path than first issue --------------------
    r = me.post("/api/me/agent/%s/credential" % agent_name,
                json={"purpose": "x", "scopes": ["read"], "expires_days": 30,
                      "password": "wrong", "csrf_token": csrf},
                headers={"Origin": ORIGIN})
    check("rotating with the wrong password is refused, exactly as creating is",
          r.status_code == 403 and r.json().get("field") == "password",
          f"{r.status_code} {r.text[:120]}")
    r = me.post("/api/me/agent/%s/credential" % agent_name,
                json={"purpose": "x", "scopes": ["admin"], "expires_days": 30,
                      "password": PW, "csrf_token": csrf},
                headers={"Origin": ORIGIN})
    check("...and the scope ceiling still applies, so rotation is not an escalation",
          r.status_code == 400 and r.json().get("field") == "scopes",
          f"{r.status_code} {r.text[:120]}")
    r = me.post("/api/me/agent/%s/credential" % agent_name,
                json={"purpose": "x", "scopes": ["read"], "expires_days": 0,
                      "password": PW, "csrf_token": csrf},
                headers={"Origin": ORIGIN})
    check("...and expiry is still mandatory",
          r.status_code == 400 and r.json().get("field") == "expires_days",
          f"{r.status_code} {r.text[:120]}")

    r = other.post("/api/me/agent/%s/credential" % agent_name,
                   json={"purpose": "x", "scopes": ["read"], "expires_days": 30,
                         "password": OTHER_PW, "csrf_token": other_csrf},
                   headers={"Origin": ORIGIN})
    check("SOMEBODY ELSE'S AGENT IS A 404, indistinguishable from a name that "
          "never existed — agent names are guessable by construction",
          r.status_code == 404, f"{r.status_code} {r.text[:120]}")
    r = other.post("/api/me/agent/no-such-agent-at-all/credential",
                   json={"purpose": "x", "scopes": ["read"], "expires_days": 30,
                         "password": OTHER_PW, "csrf_token": other_csrf},
                   headers={"Origin": ORIGIN})
    check("...the same 404 an invented name gets", r.status_code == 404, str(r.status_code))

    # ---- REVOKE -----------------------------------------------------------
    listing = me.get("/api/me/agent-credentials").json()
    creds = listing["agents"][0]["credentials"]
    old_id = [c["id"] for c in creds if c["prefix"] == old_raw.split("_")[2]][0]

    r = other.post("/api/me/agent-credential/%s/revoke" % old_id,
                   json={"csrf_token": other_csrf}, headers={"Origin": ORIGIN})
    check("ANOTHER PERSON CANNOT REVOKE YOUR CREDENTIAL — 404, not 403",
          r.status_code == 404, f"{r.status_code} {r.text[:120]}")
    check("...and it really is still alive after that attempt", works(old_raw) == 200)

    r = me.post("/api/me/agent-credential/%s/revoke" % old_id,
                json={"csrf_token": csrf}, headers={"Origin": ORIGIN})
    check("revoking your own credential -> 200", r.status_code == 200,
          f"{r.status_code} {r.text[:160]}")
    check("REVOKED ON THE VERY NEXT REQUEST — measured through the gate",
          works(old_raw) == 401, f"got {works(old_raw)}")
    check("...while the replacement is untouched", works(new_raw) == 200,
          f"got {works(new_raw)}")
    check("...and the response says what survived it",
          r.json().get("agent_still_exists") is True
          and r.json().get("live_credentials_left") == 1, str(r.json()))

    r = me.post("/api/me/agent-credential/%s/revoke" % old_id,
                json={"csrf_token": csrf}, headers={"Origin": ORIGIN})
    check("revoking twice is idempotent and says so",
          r.status_code == 200 and r.json().get("already") is True, r.text[:120])

    # ---- the agent survives its last credential ---------------------------
    listing = me.get("/api/me/agent-credentials").json()
    live_ids = [c["id"] for c in listing["agents"][0]["credentials"] if c["live"]]
    for tid in live_ids:
        me.post("/api/me/agent-credential/%s/revoke" % tid,
                json={"csrf_token": csrf}, headers={"Origin": ORIGIN})
    with app.session() as s:
        bot = s.scalar(select(AuthUser).where(AuthUser.subject == agent_name))
    check("REVOKING THE LAST CREDENTIAL LEAVES THE AGENT AND ITS PERSON LINK — "
          "the account is the identity, the token is the credential",
          bot is not None and bot.person_user_id == "u-i-person"
          and bot.disabled_at is None)
    r = me.post("/api/me/agent/%s/credential" % agent_name,
                json={"purpose": "x", "scopes": ["read"], "expires_days": 30,
                      "password": PW, "csrf_token": csrf},
                headers={"Origin": ORIGIN})
    check("...so it can be given a fresh one, which is why that matters",
          r.status_code == 201, f"{r.status_code} {r.text[:120]}")

    # ---- session-only, on every one of them -------------------------------
    # TWO refusals, and they are not the same one. A read-scoped token is
    # stopped by `guard_write` on the scope axis; an ADMIN-scoped one satisfies
    # that gate and must still be refused on KIND. Only the second proves the
    # claim, so both are asserted rather than the easier one alone.
    agent_client = TestClient(app.app, base_url=ORIGIN)
    agent_client.headers.update({"Authorization": "Bearer " + r.json()["token"]})
    rr = agent_client.post("/api/me/agent-credential/%s/revoke" % old_id,
                           json={}, headers={"Origin": ORIGIN})
    check("a read-scoped Bearer is refused on SCOPE before it gets near a token",
          rr.status_code == 403 and "admin" in rr.json().get("detail", ""),
          f"{rr.status_code} {rr.text[:120]}")

    # An owner's admin-scoped agent — the most capable Bearer this system can
    # produce — pointed at a credential belonging to somebody else entirely.
    owner_c, owner_c_csrf = sign_in("h-owner", "Correct-Horse-Battery-Owner-5!")
    adm = owner_c.post("/api/me/agent-credential",
                       json={"purpose": "revoker", "scopes": ["admin"],
                             "expires_days": 30,
                             "password": "Correct-Horse-Battery-Owner-5!",
                             "csrf_token": owner_c_csrf},
                       headers={"Origin": ORIGIN})
    check("an owner's admin-scoped agent exists for the next check",
          adm.status_code == 201, f"{adm.status_code} {adm.text[:120]}")
    admin_client = TestClient(app.app, base_url=ORIGIN)
    admin_client.headers.update({"Authorization": "Bearer " + adm.json()["token"]})
    rr = admin_client.post("/api/me/agent-credential/%s/revoke" % old_id,
                           json={}, headers={"Origin": ORIGIN})
    check("AN ADMIN-SCOPED BEARER STILL CANNOT REVOKE — it passes the scope gate "
          "and is refused on KIND. One stolen key is not a denial of service on "
          "every other agent anybody runs",
          rr.status_code == 403 and rr.json().get("error") == "session_required",
          f"{rr.status_code} {rr.text[:160]}")


# --------------------------------------------------------------------------- #
# J. token add never prints a raw token by default
# --------------------------------------------------------------------------- #
def section_j() -> None:
    print("\n-- J. token add: the raw value goes where --out/--print says, and nowhere by default --")
    import tempfile
    from contextlib import redirect_stderr
    from models_auth import AuthServiceToken as ServiceToken

    code, _ = run_admin("bot", "add", "bot-tokens")
    check("a bot exists to own the tokens", code == 0)

    def live_tokens() -> int:
        with app.session() as s:
            owner = s.scalar(select(AuthUser).where(AuthUser.subject == "bot-tokens"))
            return len(s.scalars(select(ServiceToken).where(
                ServiceToken.owner_user_id == owner.id,
                ServiceToken.revoked_at.is_(None))).all())

    before = live_tokens()
    err = io.StringIO()
    with redirect_stderr(err):
        code, out = run_admin("token", "add", "bot-tokens", "--scopes", "read")
    check("with neither --out nor --print the command is REFUSED", code != 0, out + err.getvalue())
    check("...and nothing token-shaped reached stdout", "atlas_svc_" not in out, out)
    check("...and the refusal happens BEFORE minting — no token row was created",
          live_tokens() == before, f"{before} -> {live_tokens()}")

    err = io.StringIO()
    with redirect_stderr(err):
        code, out = run_admin("token", "add", "bot-tokens", "--scopes", "read", "--out", "-")
    check("`--out -` mints", code == 0, err.getvalue())
    check("...and stdout carries the raw value ALONE, one line, for a redirect",
          out.strip().startswith("atlas_svc_") and len(out.strip().splitlines()) == 1, out)
    check("...while the human-readable lines went to stderr",
          "minted service token" in err.getvalue() and out.strip() not in err.getvalue(),
          err.getvalue())

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "tok")
        err = io.StringIO()
        with redirect_stderr(err):
            code, out = run_admin("token", "add", "bot-tokens", "--scopes", "read", "--out", path)
        check("`--out PATH` mints into a file", code == 0 and os.path.exists(path), err.getvalue())
        check("...and prints no raw value anywhere", "atlas_svc_" not in out, out)
        if os.name == "posix":
            check("...and the file is 0600", (os.stat(path).st_mode & 0o777) == 0o600,
                  oct(os.stat(path).st_mode & 0o777))
        before = live_tokens()
        with redirect_stderr(io.StringIO()):
            code, _ = run_admin("token", "add", "bot-tokens", "--scopes", "read", "--out", path)
        check("an existing credential file is never overwritten, and nothing is minted",
              code != 0 and live_tokens() == before)

    code, out = run_admin("token", "add", "bot-tokens", "--scopes", "read", "--print")
    check("`--print` still shows it, for a human at a terminal", code == 0 and "atlas_svc_" in out, out[:80])


if __name__ == "__main__":
    # `startup()` is an @app.on_event handler, so importing app does not create
    # the tables. Same explicit call as verify_node_access.py.
    app.startup()

    section_a()
    section_b()
    # Section B reset the schema to build its PRE-shape and then reconciled it;
    # startup() again so the rest runs against a fully built, seeded database.
    app.startup()
    section_c()
    section_d()
    section_e()
    section_f()
    section_g()
    section_h()
    section_i()
    section_j()

    print()
    if failures:
        print(f"FAILED — {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")
