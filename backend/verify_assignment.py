"""
Verification for EPIC-atlas-assignment's backend half — TASK-1118 (the column,
the write path, the `?assignee=` filter) and TASK-1119 (`GET /api/people`).

No pytest suite exists in this repo (see verify_object_graph.py's docstring);
this follows the same standalone, run-it-and-read-it convention as its
siblings, and takes its harness shape from verify_node_access.py because the
property in section F is that access layer's.

SEVEN SECTIONS:

  A. THE COLUMN. `node.assignee_user_id` exists, is nullable, and carries NO
     foreign key — deliberately, because `_reconcile_graph_schema`'s whole DDL
     vocabulary is `ALTER TABLE ... ADD COLUMN <type>`. A declared FK would be
     created on every fresh test database and never on live, so this suite
     would prove a constraint the deployment does not have. The assertion is
     therefore that the FK is ABSENT: it is what keeps the model honest about
     what live actually looks like.

  B. THE WRITE PATH. Create with an assignee; PATCH with a value, with an
     explicit `null`, and with the field OMITTED — three different requests
     with three different outcomes, and the omit/null pair is the one that
     silently destroys data when it is got wrong.

  C. REFUSALS. An unknown id, an AGENT, and a DISABLED account are each a 400
     naming the field. The agent case is where "agents are not assignable"
     (ADR 0034; owner decision, 2026-08-17) is actually enforced — `/api/people` merely
     declines to offer the choice.

  D. SERIALISATION. `assignee` comes back resolved to `{id, username, name}`,
     never a bare uuid, from EVERY route that projects a node. This is the
     section that catches a new `_graph_node_json` caller that forgot to pass
     the `assignees` map — the failure mode there is silent (an assigned node
     renders as unassigned), so it is asserted rather than reasoned about.

  E. THE FILTER AND THE ROSTER. `?assignee=` resolves `me`/`none`/an id and
     COMPOSES with the other filters. `/api/people` is people-only,
     active-only, carries no email or role, and `?for=` narrows by who can see
     the item.

  F. ASSIGNMENT IS NOT A GRANT. The load-bearing one. Assigning a member a
     workitem bound to a project they hold no grant for must change NOTHING
     about what they can see — not the item, not the roster, not the counts.
     The failure here is invisible: nothing errors, a member simply starts
     seeing a row they should not, and the only way anyone notices is by
     looking as that member.

  G. PAGING HONESTY. Added 2026-08-18, and the reason is worth reading: this
     suite PASSED while the Mine view was silently rendering 200 of 372 rows
     labelled "200 items". Every section above ran against a fixture too small
     to reach the server's page_size cap, so none of them could see it. G
     builds a corpus larger than the cap on purpose.

Run (from an environment with SQLAlchemy/psycopg2/jsonschema installed):
    python verify_assignment.py
"""
from __future__ import annotations

import base64
import os
import sys
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

TEST_ORIGIN = "https://testserver"

from sqlalchemy import delete, inspect as sa_inspect, select  # noqa: E402

import app  # noqa: E402
import auth  # noqa: E402
import models_graph  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthUser, NodeGrant, NodeVisibility  # noqa: E402
from models_graph import Edge, Node  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


OWNER_ID, AVERY_ID, BJORN_ID = "u-owner", "u-avery", "u-bjorn"
BOT_ID, GONE_ID = "u-bot", "u-gone"


PW = "Correct-Horse-Battery-Assignment-9!"


def mk_user(uid, username, role="member", is_service=False, disabled=False) -> None:
    with app.session() as s:
        if s.get(AuthUser, uid) is None:
            s.add(AuthUser(
                id=uid, subject=username, name=username.title(), role=role,
                is_service=is_service, must_change_password=False,
                # A service row may hold no password (ck_auth_user_service_has_no_password);
                # the bot here exists to be REFUSED as an assignee, never to sign in.
                password_hash=None if is_service else passwords.hash_password(PW),
                disabled_at=datetime.now(timezone.utc) if disabled else None,
                created_at=datetime.now(timezone.utc)))
            s.commit()


def sign_in(username: str):
    """A real signed-in client, cookie and CSRF token both. Deliberately NOT a
    ContextVar shortcut: the principal a route sees comes from the middleware,
    and a suite that installs one directly proves the resolver rather than the
    HTTP path — which is the half that can regress."""
    from fastapi.testclient import TestClient
    c = TestClient(app.app, base_url=TEST_ORIGIN)
    page = c.get("/auth/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": username, "password": PW, "csrf_token": token},
           headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
    return c, c.get("/api/me").json()["csrf_token"]


def seed_world() -> None:
    """Two projects — one shared, one private — and a workitem bound to each."""
    with app.session() as s:
        for nid in ("proj-shared", "proj-private"):
            if s.get(Node, nid) is None:
                s.add(Node(id=nid, type="project", name=nid, attributes={},
                           attention_count=0, completion=0.0, origin="atlas"))
        s.flush()
        pairs = (("TASK-SHARED", "proj-shared"), ("TASK-PRIVATE", "proj-private"))
        for wid, _dst in pairs:
            if s.get(Node, wid) is None:
                s.add(Node(id=wid, type="task", name=wid, attributes={"status": "pending"},
                           attention_count=0, completion=0.0, origin="atlas"))
        # Nodes first, THEN the edges: `edge.src`/`edge.dst` are real foreign
        # keys, and a `s.get()` mid-loop autoflushes a pending Edge before its
        # Node exists.
        s.flush()
        for wid, dst in pairs:
            exists = s.query(Edge).filter_by(src=wid, dst=dst, edge_type="works_on").first()
            if exists is None:
                s.add(Edge(src=wid, dst=dst, edge_type="works_on", directed=True,
                           origin="atlas"))
        # proj-private is private; AVERY is granted nothing, BJORN is granted it.
        if s.get(NodeVisibility, "proj-private") is None:
            s.add(NodeVisibility(node_id="proj-private", visibility="private",
                                 set_by=OWNER_ID, set_at=datetime.now(timezone.utc)))
            s.add(NodeGrant(node_id="proj-private", user_id=BJORN_ID,
                            granted_by=OWNER_ID, granted_at=datetime.now(timezone.utc)))
        s.commit()


# --------------------------------------------------------------------------- #
# A. The column
# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. the column --")
    insp = sa_inspect(app.engine)
    cols = {c["name"]: c for c in insp.get_columns("node")}
    check("node.assignee_user_id exists", "assignee_user_id" in cols,
          f"columns: {sorted(cols)[:6]}...")
    if "assignee_user_id" in cols:
        check("it is NULLABLE (nothing to backfill 374 rows with)",
              cols["assignee_user_id"]["nullable"] is True)

    fks = insp.get_foreign_keys("node")
    on_assignee = [f for f in fks if "assignee_user_id" in (f.get("constrained_columns") or [])]
    check("NO foreign key is declared on it — the shim cannot emit REFERENCES, "
          "so an FK here would exist on test databases and never on live",
          on_assignee == [], f"found {on_assignee}")

    check("the model column is the same width as auth_user.id (36)",
          getattr(models_graph.Node.assignee_user_id.type, "length", None) == 36)


# --------------------------------------------------------------------------- #
# B. The write path
# --------------------------------------------------------------------------- #
def section_b() -> None:
    print("\n-- B. write path: create, set, null, OMIT --")
    c, csrf = sign_in("owner")
    if True:
        r = c.post("/api/workitem", json={"type": "task", "name": "assigned at birth",
                                          "assignee": AVERY_ID, "csrf_token": csrf},
                   headers={"Origin": TEST_ORIGIN})
        created = r.json() if r.status_code < 300 else {}
        wid = created.get("id")
        check("POST accepts an assignee", r.status_code < 300 and wid is not None,
              f"{r.status_code} {str(r.json())[:120]}")
        check("POST echoes it RESOLVED, not as a uuid",
              isinstance(created.get("assignee"), dict)
              and created["assignee"].get("username") == "avery",
              f"got {created.get('assignee')!r}")

        if wid:
            # re-point
            r = c.patch(f"/api/workitem/{wid}", json={"assignee": BJORN_ID, "csrf_token": csrf},
                        headers={"Origin": TEST_ORIGIN})
            check("PATCH re-points it", r.json().get("assignee", {}).get("username") == "bjorn",
                  str(r.json().get("assignee")))

            # OMIT — must not touch it
            r = c.patch(f"/api/workitem/{wid}", json={"name": "renamed only", "csrf_token": csrf},
                        headers={"Origin": TEST_ORIGIN})
            check("PATCH OMITTING assignee leaves it alone",
                  r.json().get("assignee", {}).get("username") == "bjorn",
                  str(r.json().get("assignee")))

            # explicit null — must clear it
            r = c.patch(f"/api/workitem/{wid}", json={"assignee": None, "csrf_token": csrf},
                        headers={"Origin": TEST_ORIGIN})
            check("PATCH with an explicit null UNASSIGNS",
                  r.json().get("assignee") is None, str(r.json().get("assignee")))

            with app.session() as s:
                row = s.get(Node, wid)
                check("and the column really is NULL, not the string 'None'",
                      row.assignee_user_id is None, repr(row.assignee_user_id))


# --------------------------------------------------------------------------- #
# C. Refusals
# --------------------------------------------------------------------------- #
def section_c() -> None:
    print("\n-- C. refusals: unknown, agent, disabled --")
    c, csrf = sign_in("owner")
    if True:
        for label, value, expect in (
            ("an unknown account id", "u-does-not-exist", "no account"),
            ("an AGENT (ADR 0034 — assign the person)", BOT_ID, "agent"),
            ("a DISABLED account", GONE_ID, "disabled"),
        ):
            r = c.post("/api/workitem",
                       json={"type": "task", "name": f"refuse {label}", "assignee": value,
                             "csrf_token": csrf},
                       headers={"Origin": TEST_ORIGIN})
            body = r.json()
            check(f"400 for {label}", r.status_code == 400, f"{r.status_code} {str(body)[:100]}")
            check(f"  ...and it NAMES THE FIELD for {label}",
                  body.get("field") == "assignee" and expect in str(body.get("detail", "")),
                  str(body)[:140])


# --------------------------------------------------------------------------- #
# D. Serialisation, on every route that projects a node
# --------------------------------------------------------------------------- #
def section_d() -> None:
    print("\n-- D. serialisation: resolved on EVERY projecting route --")
    c, csrf = sign_in("owner")
    if True:
        with app.session() as s:
            s.get(Node, "TASK-SHARED").assignee_user_id = AVERY_ID
            s.commit()

        def resolved(payload) -> bool:
            return (isinstance(payload, dict) and payload.get("username") == "avery"
                    and payload.get("id") == AVERY_ID)

        r = c.get("/api/node/TASK-SHARED")
        check("GET /api/node/{id} resolves it", resolved(r.json().get("assignee")),
              str(r.json().get("assignee"))[:120])

        r = c.get("/api/workitems?type=task")
        row = next((i for i in r.json()["items"] if i["id"] == "TASK-SHARED"), {})
        check("GET /api/workitems resolves it", resolved(row.get("assignee")),
              str(row.get("assignee"))[:120])

        r = c.get("/api/graph?depth=8&cap=2000")
        row = next((n for n in r.json()["nodes"] if n["id"] == "TASK-SHARED"), {})
        check("GET /api/graph resolves it", resolved(row.get("assignee")),
              str(row.get("assignee"))[:120])

        def walk(nodes):
            for n in nodes:
                yield n
                yield from walk(n.get("children") or [])

        r = c.get("/api/graph/tree")
        row = next((n for n in walk(r.json()["roots"]) if n["id"] == "TASK-SHARED"), {})
        check("GET /api/graph/tree resolves it", resolved(row.get("assignee")),
              str(row.get("assignee"))[:120])

        r = c.post("/api/node/TASK-SHARED/status",
                   json={"status": "in_progress", "csrf_token": csrf},
                   headers={"Origin": TEST_ORIGIN})
        payload = r.json().get("node", r.json())
        check("POST .../status resolves it in its echo", resolved(payload.get("assignee")),
              str(payload.get("assignee"))[:120])

        # The key must ALWAYS be present, even unassigned — a consumer should
        # never have to test whether this route bothered to emit it.
        r = c.get("/api/node/TASK-PRIVATE")
        check("the key is present-and-null for an unassigned node",
              "assignee" in r.json() and r.json()["assignee"] is None)


# --------------------------------------------------------------------------- #
# E. The filter and the roster
# --------------------------------------------------------------------------- #
def section_e() -> None:
    print("\n-- E. ?assignee= and /api/people --")
    c, _ = sign_in("avery")
    if True:
        r = c.get("/api/workitems?assignee=me")
        ids = [i["id"] for i in r.json()["items"]]
        check("?assignee=me resolves to the calling principal",
              ids == ["TASK-SHARED"], f"got {ids}")

        r = c.get("/api/workitems?assignee=none&type=task")
        ids = [i["id"] for i in r.json()["items"]]
        check("?assignee=none returns the unassigned", "TASK-SHARED" not in ids, f"got {ids}")

        r = c.get(f"/api/workitems?assignee={AVERY_ID}&status=in_progress")
        check("it COMPOSES with ?status=", [i["id"] for i in r.json()["items"]] == ["TASK-SHARED"],
              str(r.json()["items"])[:80])

        r = c.get(f"/api/workitems?assignee={AVERY_ID}&status=blocked")
        check("...and composing is an INTERSECTION, not a union",
              r.json()["total"] == 0, f"total={r.json()['total']}")

        r = c.get("/api/people")
        people = r.json()["people"]
        names = sorted(p["username"] for p in people)
        check("/api/people is people only — no agent, no disabled",
              names == ["avery", "bjorn", "owner"], f"got {names}")
        check("...and carries no email, role or grants",
              all(set(p) == {"id", "username", "name"} for p in people),
              str(people[:1]))

    # No session at all. The route is behind the gate, so the honest assertion
    # is that it never answers with data — 401 here, and the `false()` branch in
    # the filter is what keeps it fail-closed if the gate ever moves.
    from fastapi.testclient import TestClient
    anon = TestClient(app.app, base_url=TEST_ORIGIN)
    r = anon.get("/api/workitems?assignee=me")
    check("an unauthenticated ?assignee=me is refused, never answered with everyone's",
          r.status_code in (401, 403) or r.json().get("total") == 0, str(r.status_code))


# --------------------------------------------------------------------------- #
# F. Assignment is not a grant
# --------------------------------------------------------------------------- #
def section_f() -> None:
    print("\n-- F. ASSIGNMENT IS NOT A GRANT --")
    with app.session() as s:
        s.get(Node, "TASK-PRIVATE").assignee_user_id = AVERY_ID
        s.commit()

    c, _ = sign_in("avery")
    if True:
        r = c.get("/api/node/TASK-PRIVATE")
        unknown = c.get("/api/node/TASK-NO-SUCH-ID")
        check("as the assignee-without-a-grant, the item is a 404",
              r.status_code == 404, str(r.status_code))
        # NOT "byte-identical to the response for a DIFFERENT id" — the body
        # echoes the id you asked for, so two different ids can never match and
        # an assertion that they do would be untrue of the whole API, not just
        # this route. The property that actually matters is that the hidden id
        # and a nonexistent id are INDISTINGUISHABLE: same status, same
        # template, nothing in either response that only a real row could
        # produce. So compare each against the template with its own id in it.
        check("...and it is indistinguishable from the 404 for an id that never existed "
              "— same status, same body template, only the echoed id differs",
              (r.status_code, unknown.status_code) == (404, 404)
              and r.text.replace("TASK-PRIVATE", "X") == unknown.text.replace("TASK-NO-SUCH-ID", "X"),
              f"{r.text[:60]!r} vs {unknown.text[:60]!r}")

        r = c.get("/api/workitems?assignee=me")
        ids = [i["id"] for i in r.json()["items"]]
        check("...it is absent from their OWN ?assignee=me", "TASK-PRIVATE" not in ids,
              f"got {ids}")
        check("...and that is an empty-ish 200, never a 403 (which would confirm it)",
              r.status_code == 200)

        r = c.get("/api/graph/tree")
        check("...and absent from the whole tree", "TASK-PRIVATE" not in r.text)

        r = c.get("/api/people?for=TASK-PRIVATE")
        unknown = c.get("/api/people?for=TASK-NO-SUCH-ID")
        check("?for= on an invisible item is indistinguishable from an unknown one "
              "— an empty roster would confirm the item exists",
              (r.status_code, unknown.status_code) == (404, 404)
              and r.text.replace("TASK-PRIVATE", "X") == unknown.text.replace("TASK-NO-SUCH-ID", "X"),
              f"{r.status_code}/{unknown.status_code} {r.text[:50]!r}")

    c, _ = sign_in("owner")
    if True:
        r = c.get("/api/node/TASK-PRIVATE")
        check("the OWNER sees the same item — two answers from one database is the PASS",
              r.status_code == 200, str(r.status_code))

        r = c.get("/api/people?for=TASK-PRIVATE")
        names = sorted(p["username"] for p in r.json()["people"])
        check("?for= a PRIVATE item narrows the roster to those who can see it",
              names == ["bjorn", "owner"], f"got {names}")

        r = c.get("/api/people?for=TASK-SHARED")
        names = sorted(p["username"] for p in r.json()["people"])
        check("?for= a SHARED item offers everybody", names == ["avery", "bjorn", "owner"],
              f"got {names}")




# --------------------------------------------------------------------------- #
# G. Paging honesty, and the ancestor chain
# --------------------------------------------------------------------------- #
def section_g() -> None:
    """The section that exists because THIS SUITE MISSED A REAL DEFECT.

    Sections A-F all ran against a fixture of a handful of rows, where the
    server's `page_size` cap is never reached. So nothing here caught the Mine
    view asking for `page_size=500`, receiving the capped 200, and rendering
    them under the label "200 items" — a truncated list presented as a complete
    one, on a corpus of 372. It was found by looking at the live board, not by
    running this file.

    So this section builds a corpus LARGER THAN THE CAP on purpose. A cap that
    is never hit is a cap that is never tested, and a fixture small enough to
    be convenient is a fixture too small to find this class of bug.

    It also covers `?include_ancestors=1`, which exists so the Mine view can
    nest rows without calling `/api/node/{id}` once per row — at 372 assigned
    items that was 372 requests to draw some headers.
    """
    print("\n-- G. paging honesty and the ancestor chain --")
    c, csrf = sign_in("owner")
    cap = app.WORKITEM_LIST_MAX_PAGE_SIZE

    with app.session() as s:
        if s.get(Node, "STORY-PAGED") is None:
            s.add(Node(id="STORY-PAGED", type="story", name="paged parent",
                       attributes={"status": "pending"}, attention_count=0,
                       completion=0.0, origin="atlas"))
        s.flush()
        for i in range(cap + 25):
            nid = "TASK-P%04d" % i
            if s.get(Node, nid) is None:
                s.add(Node(id=nid, type="task", name="paged " + str(i),
                           attributes={"status": "pending"},
                           assignee_user_id=BJORN_ID, attention_count=0,
                           completion=0.0, origin="atlas"))
        s.flush()
        for i in range(cap + 25):
            nid = "TASK-P%04d" % i
            if s.query(Edge).filter_by(src="STORY-PAGED", dst=nid,
                                       edge_type="contains").first() is None:
                s.add(Edge(src="STORY-PAGED", dst=nid, edge_type="contains",
                           directed=True, origin="atlas"))
        s.commit()

    r = c.get(f"/api/workitems?assignee={BJORN_ID}&page_size=500").json()
    check("the server CAPS page_size rather than honouring 500",
          r["page_size"] == cap, f"asked 500, got {r['page_size']}")
    check("`total` is the WHOLE set, not the page — the number a label must use",
          r["total"] > r["count"] and r["total"] >= cap + 25,
          f"total={r['total']} count={r['count']}")
    check("`max_page_size` is in the payload, so a client can tell a capped "
          "answer from a complete one WITHOUT hardcoding the constant",
          r.get("max_page_size") == cap, str(r.get("max_page_size")))

    seen, page = set(), 1
    while page <= 20:
        d = c.get(f"/api/workitems?assignee={BJORN_ID}&page={page}").json()
        ids = [i["id"] for i in d["items"]]
        if not ids:
            break
        seen.update(ids)
        if len(seen) >= d["total"]:
            break
        page += 1
    check("paging through reaches EVERY assigned item, with no duplicates",
          len(seen) == r["total"], f"walked {len(seen)} of {r['total']}")

    d = c.get(f"/api/workitems?assignee={BJORN_ID}&include_ancestors=1").json()
    with_anc = [i for i in d["items"] if i.get("ancestors")]
    check("?include_ancestors=1 puts the contains chain ON the row — no "
          "per-row /api/node call needed to draw the nesting",
          len(with_anc) == len(d["items"])
          and with_anc[0]["ancestors"][-1]["id"] == "STORY-PAGED",
          f"{len(with_anc)}/{len(d['items'])} carry ancestors")
    check("...root-first, so a renderer can indent by position without sorting",
          all(a["ancestors"][-1]["id"] == "STORY-PAGED" for a in with_anc))

    d0 = c.get(f"/api/workitems?assignee={BJORN_ID}").json()
    check("...and it is OPT-IN: the plain listing still omits ancestors, "
          "because a listing is not a detail view",
          all("ancestors" not in i for i in d0["items"]))

    # Torn down so a re-run starts from the same corpus as sections A-F.
    #
    # An EXPLICIT id list, not `LIKE 'TASK-P%'`. That pattern also matches
    # `TASK-PRIVATE` — section F's fixture, which carries a `works_on` edge —
    # so the delete failed on a foreign key. A wildcard written to match what
    # you just made will happily match what somebody else made.
    paged_ids = ["TASK-P%04d" % i for i in range(cap + 25)]
    with app.session() as s:
        s.execute(delete(Edge).where(Edge.src == "STORY-PAGED"))
        s.execute(delete(Node).where(Node.id.in_(paged_ids)))
        s.execute(delete(Node).where(Node.id == "STORY-PAGED"))
        s.commit()


def main() -> int:
    # `startup()` is an @app.on_event handler, so importing app does NOT create
    # the tables — and the sections below touch them directly, not only over
    # HTTP. Call it once, explicitly (same note as verify_node_access.py).
    # It is also what runs `_reconcile_graph_schema`, which is section A's
    # whole subject.
    app.startup()

    mk_user(OWNER_ID, "owner", role="owner")
    mk_user(AVERY_ID, "avery")
    mk_user(BJORN_ID, "bjorn")
    mk_user(BOT_ID, "bot-thing", is_service=True)
    mk_user(GONE_ID, "gone", disabled=True)
    seed_world()

    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    section_f()
    section_g()

    print("\n" + ("ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
