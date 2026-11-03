"""
Verification for STORY-oss-16-audit-export: the read audit (`audit_read`), the export
(`GET /api/audit/export`) and read-audit retention (`atlas-admin audit purge`), through
the REAL routes and the REAL auth gate, against a disposable Postgres (`testdb`).

  A. settings: `settings.load_audit` — unset is today (off, keep forever); 1/true/yes/on
     and 0/false/no/off parse; set-but-blank, any other word and a retention outside
     1..36500 raise naming the variable; a blank value stops `import app` in a child.
  B. schema: `audit_read` is created by today's `models_auth.create_all`, including on a
     POPULATED database that predates it, and it survives ATLAS_RESEED (auth Base).
  C. DEFAULT OFF: agent reads of a node, a search and a document write NO audit_read row,
     and each response is byte-identical to the same read with auditing on.
  D. ON (criterion 1): an agent read records the person, the agent, the token id, the
     tool and request id it declared, the route and the node ids served — for a node, a
     doc and a search (the ids on the page, never the query). A browser session's read,
     a 404 and a malformed label are handled as documented.
  E. export (criterion 2): JSON Lines of graph_event, auth_event and audit_read for an
     owner, in time order with stable keys; `since`/`until`/`kinds` filter; bad input is
     a 400 naming the field; members, non-admin tokens and admin tokens of a member are
     403; an owner's admin token is 200; no secret, hash or session id is in it.
  F. retention (criterion 3): `audit_log.purge_reads` and `atlas-admin audit purge`
     delete audit_read rows older than ATLAS_AUDIT_RETENTION_DAYS and nothing else;
     unset deletes nothing.
  G. the remaining agent read routes (TASK-1382): /api/graph, /api/graph/outline,
     /api/graph/path, /api/node/{id}/structure, /api/workitems, /api/adrs,
     /api/adrs/{ref}, /api/intents. Off: no row and a byte-identical body. On: ONE
     row per read naming the route, the tool and exactly the node ids the body
     serves; a refusal (400/404/413) and a browser session record nothing. One
     /api/context call is ONE row (its inner search records nothing), while a direct
     /api/search still records its own. Off, the ids are never even built. Every
     `record_read` call in the source names a template in AUDITED_READ_ROUTES, and
     every template there is a live GET route that has a call.

Run (from a venv with the backend requirements installed):
    python verify_audit_export.py
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import func, select, text

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:
    print(f"FATAL: {e}")
    sys.exit(1)
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
# The subject of this script is the setting: start from its default, whatever the runner set.
os.environ.pop("ATLAS_AUDIT_READS", None)
os.environ.pop("ATLAS_AUDIT_RETENTION_DAYS", None)

TEST_ORIGIN = "https://testserver"
H = {"Origin": TEST_ORIGIN}

import app  # noqa: E402
import audit_log  # noqa: E402
import auth  # noqa: E402
import models_auth  # noqa: E402
import passwords  # noqa: E402
import settings  # noqa: E402
from models_audit import AuditRead  # noqa: E402
from models_auth import AuthUser  # noqa: E402
from models_graph import GraphEvent, Node, NodeDoc  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def _client():
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN)  # no `with`: no second startup


OWNER, MEMBER = "u-ae-owner", "u-ae-member"
OWNER_BOT, MEMBER_BOT, LOOSE_BOT = "u-ae-owner-bot", "u-ae-member-bot", "u-ae-loose-bot"
PASSWORD = "Correct-Horse-Battery-9!"
NODE, DOC_PATH = "ae-node", "docs/nodes/ae-node/runbook.md"
SEARCH_WORD = "zanzibarquokka"
RAW_TOKENS: list[str] = []


def _user(uid, subject, role="member", is_service=False, person=None, password=False):
    with app.session() as s:
        if s.get(AuthUser, uid) is None:
            s.add(AuthUser(id=uid, subject=subject, name=subject, role=role,
                           is_service=is_service, must_change_password=False,
                           password_hash=passwords.hash_password(PASSWORD) if password else None,
                           person_user_id=person, created_at=datetime.now(timezone.utc)))
            s.commit()


def _mint(owner_id, scopes, label):
    with app.session() as s:
        raw, row = auth.mint_service_token(s, owner=s.get(AuthUser, owner_id),
                                           scopes=scopes, label=label)
        s.commit()
        RAW_TOKENS.append(raw)
        return raw, row.id


def _sign_in(subject):
    c = _client()
    page = c.get("/auth/login")
    csrf = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": subject, "password": PASSWORD, "csrf_token": csrf},
           headers=H, follow_redirects=False)
    return c, c.get("/api/me").json()["csrf_token"]


def _bearer(token, tool=None, rid=None):
    h = {"Authorization": "Bearer " + token}
    if tool is not None:
        h["X-Atlas-Tool"] = tool
    if rid is not None:
        h["X-Request-ID"] = rid
    return h


def _reads() -> list[AuditRead]:
    with app.session() as s:
        rows = list(s.scalars(select(AuditRead).order_by(AuditRead.id)).all())
        for r in rows:
            s.expunge(r)
        return rows


def _clear_reads() -> None:
    with app.session() as s:
        s.execute(sa.delete(AuditRead))
        s.commit()


def _fixture():
    _user(OWNER, "ae-owner", role="owner", password=True)
    _user(MEMBER, "ae-member", password=True)
    _user(OWNER_BOT, "bot-ae-owner", is_service=True, person=OWNER)
    _user(MEMBER_BOT, "bot-ae-member", is_service=True, person=MEMBER)
    _user(LOOSE_BOT, "bot-ae-loose", is_service=True)
    with app.session() as s:
        if s.get(Node, NODE) is None:
            s.add(Node(id=NODE, type="service", name="Audit export node", origin="atlas",
                       body=f"Carries the word {SEARCH_WORD} for the search case.",
                       attributes={"docs": [{"path": DOC_PATH, "kind": "runbook",
                                             "title": "Runbook"}]}))
            s.flush()
            s.add(NodeDoc(node_id=NODE, path=DOC_PATH, content="# Runbook\n\nSecret-free."))
            s.commit()


# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. ATLAS_AUDIT_READS / ATLAS_AUDIT_RETENTION_DAYS through settings.load_audit --")
    d = settings.load_audit({})
    check("unset -> off and keep forever (today)", d.reads is False and d.retention_days is None)
    for v in ("1", "true", "YES", "on"):
        check(f"ATLAS_AUDIT_READS={v!r} -> on", settings.load_audit({"ATLAS_AUDIT_READS": v}).reads)
    for v in ("0", "false", "no", "OFF"):
        check(f"ATLAS_AUDIT_READS={v!r} -> off",
              settings.load_audit({"ATLAS_AUDIT_READS": v}).reads is False)
    check("ATLAS_AUDIT_RETENTION_DAYS=365 -> 365",
          settings.load_audit({"ATLAS_AUDIT_RETENTION_DAYS": "365"}).retention_days == 365)
    bad = [("ATLAS_AUDIT_READS", ""), ("ATLAS_AUDIT_READS", "  "), ("ATLAS_AUDIT_READS", "maybe"),
           ("ATLAS_AUDIT_RETENTION_DAYS", ""), ("ATLAS_AUDIT_RETENTION_DAYS", "a year"),
           ("ATLAS_AUDIT_RETENTION_DAYS", "0"), ("ATLAS_AUDIT_RETENTION_DAYS", "-5"),
           ("ATLAS_AUDIT_RETENTION_DAYS", "36501"), ("ATLAS_AUDIT_RETENTION_DAYS", "1.5")]
    for name, v in bad:
        try:
            settings.load_audit({name: v})
            msg = None
        except settings.SettingsError as e:
            msg = str(e)
        check(f"{name}={v!r} raises SettingsError naming the variable",
              msg is not None and name in msg, str(msg))
    env = dict(os.environ, ATLAS_AUDIT_READS="")
    p = subprocess.run([sys.executable, "-c", "import app"], cwd=str(HERE), env=env,
                       capture_output=True, text=True, timeout=120)
    check("a blank ATLAS_AUDIT_READS stops `import app` (the boot), naming it",
          p.returncode != 0 and "ATLAS_AUDIT_READS" in (p.stderr + p.stdout),
          (p.stderr or p.stdout)[-300:])
    check("this process booted with the default: READS_ENABLED is False",
          audit_log.READS_ENABLED is False)


def section_b() -> None:
    print("\n-- B. audit_read: a new table, made by today's create_all --")
    check("audit_read is on models_auth.Base, which ATLAS_RESEED never drops",
          "audit_read" in models_auth.Base.metadata.tables)
    check("audit_read exists after startup()",
          "audit_read" in sa.inspect(app.engine).get_table_names())
    with app.engine.begin() as conn:
        conn.execute(text("DROP TABLE audit_read"))
    with app.session() as s:
        populated = s.scalar(select(func.count()).select_from(AuthUser))
    models_auth.create_all(app.engine)
    check("on a POPULATED database without it, models_auth.create_all creates it",
          populated > 0 and "audit_read" in sa.inspect(app.engine).get_table_names(),
          f"{populated} accounts")
    cols = {c["name"] for c in sa.inspect(app.engine).get_columns("audit_read")}
    want = {"id", "at", "user_id", "actor_user_id", "actor_subject", "token_id", "tool",
            "route", "node_ids", "request_id"}
    check("its columns are exactly the documented ones (no content, no secret)",
          cols == want, str(sorted(cols ^ want)))


def _three_reads(c, token, tool_prefix="atlas"):
    return (
        c.get(f"/api/node/{NODE}", headers=_bearer(token, f"{tool_prefix}_get", "rid-node-1")),
        c.get(f"/api/node/{NODE}/doc", params={"path": DOC_PATH},
              headers=_bearer(token, f"{tool_prefix}_doc_read", "rid-doc-1")),
        c.get("/api/search", params={"q": SEARCH_WORD},
              headers=_bearer(token, f"{tool_prefix}_node_search", "rid-search-1")),
    )


def section_c(token) -> list:
    print("\n-- C. default off: no row, no behaviour change --")
    _clear_reads()
    c = _client()
    off = _three_reads(c, token)
    check("all three reads succeed with auditing off", all(r.status_code == 200 for r in off),
          str([r.status_code for r in off]))
    check("...and wrote NO audit_read row", _reads() == [], str(len(_reads())))
    audit_log.READS_ENABLED = True
    try:
        on = _three_reads(c, token)
    finally:
        audit_log.READS_ENABLED = False
    check("the same reads with auditing ON return byte-identical bodies and statuses",
          [(r.status_code, r.content) for r in off] == [(r.status_code, r.content) for r in on])
    _clear_reads()
    return off


def section_d(token, token_id) -> None:
    print("\n-- D. on: an agent read records person, agent, token, tool and node ids --")
    _clear_reads()
    c = _client()
    audit_log.READS_ENABLED = True
    try:
        node_r, doc_r, search_r = _three_reads(c, token)
        rows = _reads()
        check("three reads -> three audit_read rows", len(rows) == 3, str(len(rows)))
        by_route = {r.route: r for r in rows}
        check("the routes are the three templates read",
              set(by_route) == {"GET /api/node/{node_id}", "GET /api/node/{node_id}/doc",
                                "GET /api/search"}, str(sorted(by_route)))
        for r in rows:
            check(f"{r.route}: user is the PERSON behind the agent (ADR 0034)",
                  r.user_id == MEMBER, str(r.user_id))
            check(f"{r.route}: actor is the agent account", r.actor_user_id == MEMBER_BOT
                  and r.actor_subject == "bot-ae-member", f"{r.actor_user_id} {r.actor_subject}")
            check(f"{r.route}: token_id is the credential sent", r.token_id == token_id)
            check(f"{r.route}: `at` is set and tz-aware", r.at is not None and r.at.tzinfo is not None)
        n = by_route.get("GET /api/node/{node_id}")
        check("node read: tool, request id and node ids",
              n is not None and n.tool == "atlas_get" and n.request_id == "rid-node-1"
              and n.node_ids == [NODE], str(n and (n.tool, n.request_id, n.node_ids)))
        d = by_route.get("GET /api/node/{node_id}/doc")
        check("doc read: tool, request id and node ids",
              d is not None and d.tool == "atlas_doc_read" and d.node_ids == [NODE],
              str(d and (d.tool, d.node_ids)))
        sr = by_route.get("GET /api/search")
        served = [i["id"] for i in search_r.json()["items"]]
        check("search: node ids are exactly the ids served on the page",
              sr is not None and sr.node_ids == served and NODE in served,
              str(sr and sr.node_ids))
        blob = json.dumps([[r.tool, r.route, r.node_ids, r.request_id] for r in rows])
        check("no row holds the search string or any content read",
              SEARCH_WORD not in blob and "Runbook" not in blob, blob[:200])

        _clear_reads()
        c.get("/api/search", params={"q": "nothing-matches-this-xyzzy"}, headers=_bearer(token))
        rows = _reads()
        check("an empty search is still recorded, with node_ids [] and no labels",
              len(rows) == 1 and rows[0].node_ids == [] and rows[0].tool is None
              and rows[0].request_id is None, str([(r.node_ids, r.tool) for r in rows]))

        _clear_reads()
        c.get(f"/api/node/{NODE}", headers=_bearer(token, "Not A Tool!", "x" * 300))
        rows = _reads()
        check("a malformed X-Atlas-Tool / X-Request-ID is dropped, the read still recorded",
              len(rows) == 1 and rows[0].tool is None and rows[0].request_id is None)

        _clear_reads()
        r404 = c.get("/api/node/ae-does-not-exist", headers=_bearer(token, "atlas_get"))
        check("a 404 read serves nothing and records nothing",
              r404.status_code == 404 and _reads() == [], str(r404.status_code))

        browser, _ = _sign_in("ae-member")
        r = browser.get(f"/api/node/{NODE}")
        check("a browser session's read is not an agent read: no row",
              r.status_code == 200 and _reads() == [], f"{r.status_code} {len(_reads())}")

        loose, loose_id = _mint(LOOSE_BOT, ["read"], "ae-loose")
        c.get(f"/api/node/{NODE}", headers=_bearer(loose, "atlas_get"))
        rows = _reads()
        check("an agent with no person: user_id NULL, agent and token still recorded",
              len(rows) == 1 and rows[0].user_id is None and rows[0].actor_user_id == LOOSE_BOT
              and rows[0].token_id == loose_id)

        stored = {r.request_id for r in _reads()} | {r.token_id for r in _reads()}
        check("no raw token is stored anywhere in audit_read",
              not any(t in json.dumps([str(x) for x in stored]) for t in RAW_TOKENS))
    finally:
        audit_log.READS_ENABLED = False


def _lines(resp) -> list[dict]:
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


KEYS = {
    "graph_event": ["kind", "id", "at", "action", "entity_type", "entity_id", "node_id",
                    "related_node_id", "actor_kind", "actor_user_id", "actor_subject",
                    "actor_token_id", "responsible_user_id", "detail"],
    "auth_event": ["kind", "id", "at", "event", "user_id", "actor", "ip", "user_agent",
                   "reason", "detail"],
    "audit_read": ["kind", "id", "at", "user_id", "actor_user_id", "actor_subject",
                   "token_id", "tool", "route", "node_ids", "request_id"],
}


def section_e(member_token, member_admin_token, owner_read_token, owner_admin_token) -> None:
    print("\n-- E. GET /api/audit/export: JSON Lines, owner-only --")
    owner, csrf = _sign_in("ae-owner")
    r = owner.patch(f"/api/node/{NODE}", json={"summary": "touched by the owner", "csrf_token": csrf},
                    headers=H)
    check("setup: an owner write (a graph_event)", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    audit_log.READS_ENABLED = True
    try:
        _client().get(f"/api/node/{NODE}", headers=_bearer(member_token, "atlas_get", "rid-e"))
    finally:
        audit_log.READS_ENABLED = False

    r = owner.get("/api/audit/export")
    check("owner session: 200 application/x-ndjson",
          r.status_code == 200 and r.headers.get("content-type", "").startswith("application/x-ndjson"),
          f"{r.status_code} {r.headers.get('content-type')}")
    raw_lines = [ln for ln in r.text.split("\n") if ln]
    lines = _lines(r)
    check("every line is one JSON object", len(raw_lines) == len(lines) and
          all(isinstance(o, dict) for o in lines))
    kinds = {o["kind"] for o in lines}
    check("all three kinds are present", kinds == set(audit_log.EXPORT_KINDS), str(kinds))
    check("every line carries exactly its kind's stable keys, in order",
          all(list(o.keys()) == KEYS[o["kind"]] for o in lines),
          str([list(o.keys()) for o in lines if list(o.keys()) != KEYS[o["kind"]]][:1]))
    ats = [datetime.fromisoformat(o["at"]) for o in lines]
    check("ordered by time, oldest first", ats == sorted(ats))
    ar = [o for o in lines if o["kind"] == "audit_read"]
    check("the audit_read line names user, token, tool and node ids",
          any(o["user_id"] == MEMBER and o["tool"] == "atlas_get" and o["node_ids"] == [NODE]
              and o["token_id"] and o["request_id"] == "rid-e" for o in ar), str(ar[-1:]))
    ge = [o for o in lines if o["kind"] == "graph_event" and o["entity_id"] == NODE]
    check("the owner's write is a graph_event line", any(o["actor_user_id"] == OWNER for o in ge))
    check("auth_event lines include the logins", any(o["kind"] == "auth_event" and o["event"] == "login_ok"
                                                     for o in lines))
    check("the same export twice is byte-identical", owner.get("/api/audit/export").content == r.content)

    with app.session() as s:
        secrets = [u.password_hash for u in s.scalars(select(AuthUser)).all() if u.password_hash]
        secrets += [t.token_hash for t in s.scalars(select(models_auth.AuthServiceToken)).all()]
        secrets += [x.id_hash for x in s.scalars(select(models_auth.AuthSession)).all()]
        secrets += [x.csrf_hash for x in s.scalars(select(models_auth.AuthSession)).all()]
    leaked = [x[:8] for x in secrets + RAW_TOKENS + [PASSWORD] if x and x in r.text]
    check("no password, password hash, token, token hash, session id hash or CSRF hash in it",
          not leaked and len(secrets) > 0, str(leaked))
    check("no line has a key naming a secret",
          not any(k in o for o in lines for k in ("password_hash", "token_hash", "id_hash",
                                                  "csrf_hash", "session_id")))

    only = _lines(owner.get("/api/audit/export", params={"kinds": "audit_read"}))
    check("kinds=audit_read returns only audit_read", only and {o["kind"] for o in only} == {"audit_read"})
    two = _lines(owner.get("/api/audit/export", params={"kinds": "graph_event,auth_event"}))
    check("kinds=graph_event,auth_event returns exactly those",
          {o["kind"] for o in two} == {"graph_event", "auth_event"})
    cut = ats[len(ats) // 2].isoformat()
    after = _lines(owner.get("/api/audit/export", params={"since": cut}))
    check("since= is an inclusive lower bound",
          after and all(datetime.fromisoformat(o["at"]) >= datetime.fromisoformat(cut) for o in after)
          and len(after) < len(lines), f"{len(after)} of {len(lines)}")
    before = _lines(owner.get("/api/audit/export", params={"until": cut}))
    check("until= is an exclusive upper bound, and since/until partition the export",
          all(datetime.fromisoformat(o["at"]) < datetime.fromisoformat(cut) for o in before)
          and len(before) + len(after) == len(lines), f"{len(before)}+{len(after)} vs {len(lines)}")
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    r0 = owner.get("/api/audit/export", params={"since": future})
    check("a window with no rows is a 200 with an empty body", r0.status_code == 200 and r0.text == "")
    rb = owner.get("/api/audit/export", params={"since": "last tuesday"})
    check("a malformed since= is a 400 naming the field",
          rb.status_code == 400 and rb.json().get("field") == "since", rb.text[:160])
    rk = owner.get("/api/audit/export", params={"kinds": "graph_event,passwords"})
    check("an unknown kind is a 400 naming the field and the legal kinds",
          rk.status_code == 400 and rk.json().get("field") == "kinds"
          and "audit_read" in rk.json().get("detail", ""), rk.text[:200])

    member, _ = _sign_in("ae-member")
    rm = member.get("/api/audit/export")
    check("member session: 403", rm.status_code == 403, str(rm.status_code))
    c = _client()
    check("no credential: 401", c.get("/api/audit/export").status_code == 401)
    check("a member's agent token: 403",
          c.get("/api/audit/export", headers=_bearer(member_token)).status_code == 403)
    check("an admin-scoped token of a MEMBER's agent: 403",
          c.get("/api/audit/export", headers=_bearer(member_admin_token)).status_code == 403)
    check("an owner's agent token WITHOUT admin scope: 403",
          c.get("/api/audit/export", headers=_bearer(owner_read_token)).status_code == 403)
    ra = c.get("/api/audit/export", headers=_bearer(owner_admin_token))
    check("an owner's admin-scoped agent token (the SIEM credential): 200, same lines",
          ra.status_code == 200 and {o["kind"] for o in _lines(ra)} == set(audit_log.EXPORT_KINDS),
          str(ra.status_code))
    with app.session() as s:
        s.get(AuthUser, OWNER).role = "member"
        s.commit()
    rd = c.get("/api/audit/export", headers=_bearer(owner_admin_token))
    check("...and loses it the moment its person is no longer an owner (re-read per call)",
          rd.status_code == 403, str(rd.status_code))
    with app.session() as s:
        s.get(AuthUser, OWNER).role = "owner"
        s.commit()
    check("the export is not itself an audited read (no audit_read row from exporting)",
          not any(x.route.startswith("GET /api/audit") for x in _reads()))


def section_f() -> None:
    print("\n-- F. retention: only audit_read, only when set --")
    with app.session() as s:
        s.add_all([AuditRead(route="GET /api/node/{node_id}", node_ids=["old"],
                             at=datetime.now(timezone.utc) - timedelta(days=400)),
                   AuditRead(route="GET /api/node/{node_id}", node_ids=["new"],
                             at=datetime.now(timezone.utc) - timedelta(days=10))])
        s.commit()
    with app.engine.begin() as conn:
        conn.execute(text("UPDATE graph_event SET at = now() - interval '400 days' "
                          "WHERE id = (SELECT min(id) FROM graph_event)"))
        conn.execute(text("UPDATE auth_event SET at = now() - interval '400 days' "
                          "WHERE id = (SELECT min(id) FROM auth_event)"))

    def counts():
        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        tables = {"audit_read": AuditRead, "graph_event": GraphEvent,
                  "auth_event": models_auth.AuthEvent}
        with app.session() as s:
            return {name: s.scalar(select(func.count()).select_from(m).where(m.at < cutoff))
                    for name, m in tables.items()}

    before = counts()
    check("setup: an old row in each of the three tables", all(v >= 1 for v in before.values()),
          str(before))
    check("retention unset: purge_reads deletes nothing",
          audit_log.purge_reads(app.engine, None) == 0 and counts() == before)

    env = dict(os.environ)
    env.pop("ATLAS_AUDIT_RETENTION_DAYS", None)
    p = subprocess.run([sys.executable, "admin.py", "audit", "purge"], cwd=str(HERE), env=env,
                       capture_output=True, text=True, timeout=120)
    check("`atlas-admin audit purge` with retention unset: exit 0, says forever, deletes nothing",
          p.returncode == 0 and "forever" in p.stdout and counts() == before,
          (p.stdout + p.stderr)[-300:])

    env["ATLAS_AUDIT_RETENTION_DAYS"] = "365"
    p = subprocess.run([sys.executable, "admin.py", "audit", "purge"], cwd=str(HERE), env=env,
                       capture_output=True, text=True, timeout=120)
    after = counts()
    check("`atlas-admin audit purge` with ATLAS_AUDIT_RETENTION_DAYS=365: exit 0, names the count",
          p.returncode == 0 and "purged" in p.stdout, (p.stdout + p.stderr)[-300:])
    check("...the old audit_read rows are gone", after["audit_read"] == 0, str(after))
    check("...graph_event and auth_event are untouched",
          after["graph_event"] == before["graph_event"] and after["auth_event"] == before["auth_event"],
          str(after))
    with app.session() as s:
        kept = [r.node_ids for r in s.scalars(select(AuditRead)).all()]
    check("...and a recent audit_read row is kept", ["new"] in kept, str(kept))
    check("a second purge deletes nothing (idempotent)",
          audit_log.purge_reads(app.engine, 365) == 0)


# --------------------------------------------------------------------------- #
# G. the remaining agent read routes (TASK-1382)
# --------------------------------------------------------------------------- #
G_PROJ, G_CHILD, G_STORY, G_TASK, G_ADR = "ae-proj", "ae-child", "STORY-AE-1", "TASK-AE-1", "AE001"


def _g_fixture() -> None:
    """ae-proj contains ae-node contains ae-child; ae-child requires ae-node (so it is both
    a neighbour and an impact of ae-node, which the context dedupe needs); TASK-AE-1 in
    STORY-AE-1 works on ae-node; one intent and one ADR on ae-proj."""
    from models_adr import AdrRecord
    from models_graph import Edge, NodeIntent
    with app.session() as s:
        s.add_all([
            Node(id=G_PROJ, type="project", name="Audit project", origin="atlas"),
            Node(id=G_CHILD, type="service", name="Audit child", origin="atlas"),
            Node(id=G_STORY, type="story", name="Audit story", origin="atlas",
                 attributes={"status": "todo"}),
            Node(id=G_TASK, type="task", name="Audit task", origin="atlas",
                 attributes={"status": "todo"}),
        ])
        s.flush()
        s.add_all([
            Edge(src=G_PROJ, dst=NODE, edge_type="contains", directed=True, origin="atlas"),
            Edge(src=NODE, dst=G_CHILD, edge_type="contains", directed=True, origin="atlas"),
            Edge(src=G_CHILD, dst=NODE, edge_type="requires", directed=True, origin="atlas"),
            Edge(src=G_STORY, dst=G_TASK, edge_type="contains", directed=True, origin="atlas"),
            Edge(src=G_TASK, dst=NODE, edge_type="works_on", directed=True, origin="atlas"),
            NodeIntent(node_id=G_PROJ, slug="ae-rule", kind="constraint", title="Audit rule",
                       statement="Audit rule, in full.", position=0),
            AdrRecord(adr_id=G_ADR, project_node_id=G_PROJ, prefix="AE", seq=1, slug="s",
                      title="Audit decision", status="accepted", content="Decided.",
                      source="atlas"),
        ])
        s.commit()


def _outline_ids(body: dict) -> list:
    return [ln.lstrip("│├└─ ").split(" [")[0] for ln in body["text"].splitlines()]


def _structure_ids(body: dict) -> list:
    ids = [body["node"]["id"]]
    for part in ("ancestors", "children", "dependencies", "dependants", "bindings"):
        ids += [x["id"] for x in body[part]]
    return list(dict.fromkeys(ids))


def _workitem_ids(body: dict) -> list:
    ids = [i["id"] for i in body["items"]]
    ids += [a["id"] for i in body["items"] for a in i.get("ancestors", [])]
    return list(dict.fromkeys(ids))


#: (label, path, params, tool, route template, the ids the body serves)
G_READS = (
    ("graph", "/api/graph", {"focus": G_PROJ, "depth": 2}, "atlas_tree", "GET /api/graph",
     lambda b: [n["id"] for n in b["nodes"]]),
    ("outline", "/api/graph/outline", {"root": G_PROJ, "families": "all"}, "atlas_outline",
     "GET /api/graph/outline", _outline_ids),
    ("path", "/api/graph/path", {"from": G_CHILD, "to": G_PROJ}, "atlas_path",
     "GET /api/graph/path", lambda b: sorted(b["nodes"])),
    ("structure", f"/api/node/{NODE}/structure", {}, "atlas_structure",
     "GET /api/node/{node_id}/structure", _structure_ids),
    ("workitems", "/api/workitems", {"binding": NODE, "include_ancestors": 1}, "atlas_search",
     "GET /api/workitems", _workitem_ids),
    ("adrs", "/api/adrs", {"project": G_PROJ}, "atlas_adr_search", "GET /api/adrs",
     lambda b: list(dict.fromkeys(i["project"] for i in b["items"]))),
    ("adr", f"/api/adrs/{G_ADR}", {}, "atlas_adr_get", "GET /api/adrs/{ref}",
     lambda b: [b["project"]]),
    ("intents", "/api/intents", {"node_id": G_PROJ}, "atlas_intents", "GET /api/intents",
     lambda b: list(dict.fromkeys(i["node_id"] for i in b["intents"]))),
)

#: Refused reads: each serves no node, so each records nothing (today's policy, kept).
G_REFUSALS = (
    ("/api/graph", {"focus": "ae-nope"}, 404),
    ("/api/graph", {"focus": G_PROJ, "max_bytes": 10}, 413),
    ("/api/graph/outline", {"root": "ae-nope"}, 404),
    ("/api/graph/path", {"from": "ae-nope", "to": G_PROJ}, 404),
    ("/api/node/ae-nope/structure", {}, 404),
    ("/api/workitems", {"priority": "bogus"}, 400),
    ("/api/adrs", {"status": "bogus"}, 400),
    ("/api/adrs/ZZ999", {}, 404),
    ("/api/intents", {"kind": "bogus"}, 400),
)


def section_g(token, token_id) -> None:
    print("\n-- G. the remaining agent read routes (TASK-1382) --")
    _g_fixture()
    c = _client()
    _clear_reads()

    def read(path, params, tool=None, rid=None):
        return c.get(path, params=params, headers=_bearer(token, tool, rid))

    off = {lbl: read(path, params, tool, f"rid-g-{lbl}") for lbl, path, params, tool, _, _ in G_READS}
    for lbl, resp in off.items():
        check(f"{lbl}: 200 with auditing off", resp.status_code == 200,
              f"{resp.status_code} {resp.text[:160]}")
    check("off: the eight reads wrote NO audit_read row", _reads() == [], str(len(_reads())))

    built = []

    def ids():
        built.append(True)
        yield "never"
    audit_log.record_read(None, "GET /api/graph", ids())
    check("off: record_read never iterates the ids it is handed (nothing is built)",
          built == [] and _reads() == [])

    audit_log.READS_ENABLED = True
    try:
        for lbl, path, params, tool, route, served in G_READS:
            _clear_reads()
            on = read(path, params, tool, f"rid-g-{lbl}")
            rows = _reads()
            check(f"{lbl}: the same read with auditing on is byte-identical",
                  (on.status_code, on.content) == (off[lbl].status_code, off[lbl].content))
            check(f"{lbl}: exactly one audit_read row, route {route}",
                  len(rows) == 1 and rows[0].route == route,
                  str([(r.route, r.node_ids) for r in rows]))
            if len(rows) != 1 or on.status_code != 200:
                continue
            row, want = rows[0], served(on.json())
            check(f"{lbl}: node_ids are exactly the ids the body serves, in order",
                  row.node_ids == want and len(want) > 0, f"{row.node_ids} vs {want}")
            check(f"{lbl}: person, agent, token, tool and request id",
                  row.user_id == MEMBER and row.actor_user_id == MEMBER_BOT
                  and row.token_id == token_id and row.tool == tool
                  and row.request_id == f"rid-g-{lbl}",
                  f"{row.user_id} {row.actor_user_id} {row.tool} {row.request_id}")

        _clear_reads()
        for path, params, status in G_REFUSALS:
            r = read(path, params, "atlas_tree")
            check(f"refused {path} {params}: {status} and no row",
                  r.status_code == status and _reads() == [],
                  f"{r.status_code} {len(_reads())}")

        browser, _ = _sign_in("ae-member")
        codes = [browser.get(path, params=params).status_code for _, path, params, _, _, _ in G_READS]
        check("a browser session's reads of the same routes: 200 and no row",
              all(x == 200 for x in codes) and _reads() == [], f"{codes} {len(_reads())}")

        print("   -- /api/context: one call, one row --")
        _clear_reads()
        ctx = read("/api/context", {"about": SEARCH_WORD, "families": "all", "mode": "fulltext"},
                   "atlas_context", "rid-g-ctx")
        rows = _reads()
        check("context: 200", ctx.status_code == 200, f"{ctx.status_code} {ctx.text[:160]}")
        check("one /api/context call leaves ONE row, GET /api/context, and no GET /api/search row",
              [r.route for r in rows] == ["GET /api/context"], str([r.route for r in rows]))
        if ctx.status_code == 200 and len(rows) == 1:
            b = ctx.json()
            parts = [[x["id"] for x in b[p]["items"]] for p in ("seeds", "neighbourhood", "impact")]
            want = list(dict.fromkeys(parts[0] + parts[1] + parts[2]))
            check("context: node_ids are the seeds, neighbours and impact served, once each",
                  rows[0].node_ids == want and NODE in want, f"{rows[0].node_ids} vs {parts}")
            check("context: the row carries the context call's own labels",
                  rows[0].tool == "atlas_context" and rows[0].request_id == "rid-g-ctx")
        _clear_reads()
        sr = read("/api/search", {"q": SEARCH_WORD}, "atlas_node_search", "rid-g-search")
        rows = _reads()
        check("a direct /api/search after it still records its own GET /api/search row",
              sr.status_code == 200 and [r.route for r in rows] == ["GET /api/search"]
              and rows[0].node_ids == [i["id"] for i in sr.json()["items"]],
              str([(r.route, r.node_ids) for r in rows]))
        _clear_reads()
        with audit_log.nested():
            check("inside nested(), the flag is set", audit_log._NESTED.get() is True)
        check("...and reset when the block ends", audit_log._NESTED.get() is False)
    finally:
        audit_log.READS_ENABLED = False
    _clear_reads()

    print("   -- AUDITED_READ_ROUTES is the whole list --")
    import re
    called: set = set()
    for f in sorted((HERE / "routers").glob("*.py")):
        called |= set(re.findall(r'record_read\(\s*request,\s*"([^"]+)"',
                                 f.read_text(encoding="utf-8")))
    listed = set(audit_log.AUDITED_READ_ROUTES)
    check("every record_read call names a template in AUDITED_READ_ROUTES, and every template "
          "has a call", called == listed,
          f"unlisted {sorted(called - listed)}, uncalled {sorted(listed - called)}")
    from route_table import live_routes  # TASK-1361: app.routes is no longer flat
    live = {f"{m} {r.path}" for r in live_routes(app.app) if getattr(r, "path", None)
            for m in (getattr(r, "methods", None) or ())}
    check("every AUDITED_READ_ROUTES template is a live GET route",
          listed <= live, str(sorted(listed - live)))


def finish() -> None:
    print()
    if failures:
        print(f"{len(failures)} FAILED:")
        for f in failures:
            print(f"  - {f}")

        sys.exit(1)
    print("all checks passed")



if __name__ == "__main__":
    app.startup()
    section_a()
    _fixture()  # before B: B needs a POPULATED database
    section_b()
    member_token, member_token_id = _mint(MEMBER_BOT, ["read"], "ae-member-read")
    member_admin, _ = _mint(MEMBER_BOT, ["read", "admin"], "ae-member-admin")
    owner_read, _ = _mint(OWNER_BOT, ["read"], "ae-owner-read")
    owner_admin, _ = _mint(OWNER_BOT, ["read", "admin"], "ae-owner-admin")
    section_c(member_token)
    section_d(member_token, member_token_id)
    section_e(member_token, member_admin, owner_read, owner_admin)
    section_f()
    section_g(member_token, member_token_id)
    finish()
