"""
Verification for STORY-atlas-crunch-mode, the backend: crunch week's state, routes and audit trail.

The design is in crunch.py's header, the routes and their refusal order in routers/crunch.py's,
the tables in models_crunch.py's. Sections:

  A. No database: `validate_config` normalises a good config and refuses each bad one with a
     `CrunchConfigError` naming the field (not an object, an unknown key, ends_at missing, not a
     date, no timezone, before starts_at, in the past, longer than MAX_DAYS, starts too far
     ahead, an unknown backup policy or key, retention out of range or set with policy none,
     skip_envs not a list, with no matrix any non-empty skip_envs, a name not in the matrix, a
     production environment, a duplicate); THE CLOCK: no starts_at is "now" (None, set by the
     server when it writes), a sent start in the past is refused (no backdating), one in the
     future is a schedule, the set window's own start sent back is accepted and an earlier one
     refused; THE LENGTH: duration_hours instead of ends_at (exactly one; both or neither 400
     naming the field; 1..744), the end computed from the effective start, or added to a set
     window's end; the five `node.crunch.*` actions are in the
     graph_event vocabulary; both write routes are mapped to `admin`; and nothing under mcp/
     names /api/crunch (there is no MCP tool).
  B. NEVER ACTIVATED, on a homelab-shaped database (no atlas-system): the four routes are
     registered and answer as specified for every caller kind (owner session, member session,
     an owner's admin token, a member's admin token, an owner's read token, anonymous), each
     refusal by its name; an owner's PUT with CSRF and the right password is 409
     `system_node_missing` saying how to add it; afterwards no crunch table exists, no
     `node.crunch.*` event was written, and every table is byte-identical (schema included:
     the set of tables is compared too).
  C. ON an `ATLAS_SEED=none` install (atlas-system present), with the example guardrail matrix
     applied: with no matrix a non-empty skip_envs is 400; then every refusal by name with
     nothing written (member, no CSRF, no password, wrong password as a `crunch:` login_fail,
     tokens of every kind, each bad config naming its field); activate, change and extend each
     need the step-up and CSRF and write exactly one `node.crunch.<act>` event and one ADR
     (Accepted, on atlas-system, with its one `node.adr.write`), by the owner's session; the
     same config again writes nothing; GET and the banner show it; end (step-up and CSRF too)
     writes one `node.crunch.end` reason manual and one ADR, and a second end is 409
     `not_active`; EXPIRY: with the clock moved past ends_at the banner says off, and exactly
     one `node.crunch.end` reason `expired` (actor system) and one ADR are recorded however
     many reads follow; no password, pepper or token value appears in any event, history row,
     ADR or response. THE CLOCK over HTTP: an activation without starts_at is in force at once
     (its start IS activated_at, whatever this process's clock says); a past starts_at and a
     far-future one are 400 `starts_at`; a near-future one is scheduled (GET and the banner
     off), cannot be moved earlier, and can be ended. THE LENGTH over HTTP: an activation with
     duration_hours ends exactly that long after its server-set start, an extend by
     duration_hours adds to the stored end, both or neither or out of bounds is 400, and the
     state never carries duration_hours. FROM THE DATABASE (TASK-1432): while that activation
     is in force, crunch.current() carries only keys guardrails.CRUNCH_KEYS accepts (revision
     among them), and `atlas-admin guardrails show`, `guardrails export` and `cicd generate`
     with `--crunch-from-db`, each its own process reading the real state, succeed and overlay.

Run:
    python verify_crunch.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:
    print(f"FATAL: {e}")
    sys.exit(1)

from sqlalchemy import MetaData, create_engine, select, text  # noqa: E402

ENGINE = create_engine(DB.url)
ORIGIN = "https://testserver"
PASSWORD = "Correct-Horse-Battery-Crunch-9!"
WRONG = "not-the-password-Crunch-1!"
PEPPER_P = base64.b64encode(os.urandom(32)).decode()
PEPPER_T = base64.b64encode(os.urandom(32)).decode()
FLAGS = ("ATLAS_SEED", "ATLAS_LEGACY_V0", "ATLAS_RESEED", "ATLAS_DB_URL", "ATLAS_TEST_DB_URL")
BASE_ENV = {k: v for k, v in os.environ.items() if k not in FLAGS}
BASE_ENV.update({"ATLAS_BREACH_CHECK": "bundled", "ATLAS_LOGIN_ORIGINS": ORIGIN,
                 "ATLAS_FRONTEND_DIR": str(HERE.parent / "frontend"),
                 "ATLAS_PASSWORD_PEPPER": PEPPER_P, "ATLAS_TOKEN_PEPPER": PEPPER_T,
                 "ATLAS_DB_URL": DB.url})
NONE = {"ATLAS_SEED": "none", "ATLAS_LEGACY_V0": "0"}

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def run_child(args: list[str], env: dict, stdin: str | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run([sys.executable, *args], cwd=HERE, env={**BASE_ENV, **env}, input=stdin,
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        print(f"  child {args[:2]!r} exited {proc.returncode}")
        print("  stdout: " + proc.stdout[-1500:].replace("\n", "\n  "))
        print("  stderr: " + proc.stderr[-2500:].replace("\n", "\n  "))
    return proc


# --------------------------------------------------------------------------- #
# The child: boots, makes an owner and a member (with passwords) and their agents, signs both
# in, then runs one MODE. Output: one line "PROBE {json}". Every value it prints is a test value.
# --------------------------------------------------------------------------- #
PRELUDE = r'''
import json, re, sys
from datetime import datetime, timedelta, timezone
cfg = json.loads(sys.stdin.read())
import app, auth, passwords, crunch
from db import engine
from models_auth import AuthUser
from route_table import live_routes
from sqlalchemy import text as T
app.startup()
from fastapi.testclient import TestClient
ORIGIN = "https://testserver"
PW = cfg["password"]
WRONG = cfg["wrong"]
J = {"Accept": "application/json"}
H = {**J, "Origin": ORIGIN}

def user(uid, subject, role="member", is_service=False, person=None, password=False):
    with app.session() as s:
        if s.get(AuthUser, uid) is not None:
            return
        s.add(AuthUser(id=uid, subject=subject, name=subject, role=role, is_service=is_service,
                       must_change_password=False, person_user_id=person,
                       password_hash=passwords.hash_password(PW) if password else None,
                       created_at=datetime.now(timezone.utc)))
        s.commit()

def mint(uid, scopes):
    with app.session() as s:
        raw, _row = auth.mint_service_token(s, owner=s.get(AuthUser, uid), scopes=scopes, label="cr")
        s.commit()
        return raw

def sign_in(subject):
    c = TestClient(app.app, base_url=ORIGIN, follow_redirects=False)
    page = c.get("/auth/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    c.post("/auth/login", data={"username": subject, "password": PW, "csrf_token": csrf},
           headers={"Origin": ORIGIN})
    return c, c.get("/api/me").json()["csrf_token"]

user("u-cr-owner", "cr-owner", role="owner", password=True)
user("u-cr-member", "cr-member", password=True)
user("u-cr-owner-bot", "bot-cr-owner", is_service=True, person="u-cr-owner")
user("u-cr-member-bot", "bot-cr-member", is_service=True, person="u-cr-member")
tokens = {"owner_admin": mint("u-cr-owner-bot", ["read", "write", "admin"]),
          "owner_read": mint("u-cr-owner-bot", ["read"]),
          "member_admin": mint("u-cr-member-bot", ["read", "write", "admin"])}
owner, owner_csrf = sign_in("cr-owner")
member, member_csrf = sign_in("cr-member")
anon = TestClient(app.app, base_url=ORIGIN, follow_redirects=False)
SESSIONS = {"owner": (owner, owner_csrf), "member": (member, member_csrf)}

def res(r):
    try:
        body = r.json()
    except Exception:
        body = None
    return [r.status_code, body, r.text, r.headers.get("cache-control")]

def call(who, method, path, body=None, password=PW, csrf=True):
    """`who`: owner | member | anon | a token name. A session sends its CSRF token unless
    csrf=False; `password` None leaves it out."""
    headers = dict(H)
    if who in SESSIONS:
        client, token = SESSIONS[who]
    else:
        client, token = anon, None
        if who != "anon":
            headers["Authorization"] = "Bearer " + tokens[who]
    if method == "GET":
        return res(client.get(path, headers=headers))
    body = dict(body or {})
    if password is not None:
        body["password"] = password
    if token and csrf:
        body["csrf_token"] = token
    return res(client.request(method, path, json=body, headers=headers))

def put(who, config, **kw):
    return call(who, "PUT", "/api/crunch", {"config": config}, **kw)

def end(who, **kw):
    return call(who, "POST", "/api/crunch/end", {}, **kw)

def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()

def state():
    with engine.connect() as conn:
        has = conn.execute(T("SELECT to_regclass('crunch_state') IS NOT NULL")).scalar()
        hist = conn.execute(T("SELECT to_regclass('crunch_history') IS NOT NULL")).scalar()
        ev = {a: n for a, n in conn.execute(T(
            "SELECT action, count(*) FROM graph_event WHERE action LIKE 'node.crunch.%' "
            "GROUP BY action"))}
        adr_ev = conn.execute(T("SELECT count(*) FROM graph_event WHERE action = 'node.adr.write'")).scalar()
        adrs = conn.execute(T("SELECT count(*) FROM adr_record WHERE slug LIKE 'crunch-week-%'")).scalar() \
            if conn.execute(T("SELECT to_regclass('adr_record') IS NOT NULL")).scalar() else 0
        hrows = conn.execute(T("SELECT count(*) FROM crunch_history")).scalar() if hist else None
    return {"table": bool(has), "history_table": bool(hist), "events": ev, "adr_events": adr_ev,
            "adrs": adrs, "history": hrows}

def routes():
    return sorted([m, r.path] for r in live_routes(app.app)
                  if getattr(r, "path", "").startswith("/api/crunch")
                  for m in (getattr(r, "methods", None) or ["*"]))

KINDS = ("owner", "member", "owner_admin", "member_admin", "owner_read", "anon")
out = {"routes": routes(), "state_at_boot": state()}
'''

MODE_NEVER = r'''
E = iso(datetime.now(timezone.utc) + timedelta(days=2))
good = {"ends_at": E}
out["calls"] = {k: {"get": call(k, "GET", "/api/crunch"),
                    "banner": call(k, "GET", "/api/crunch/banner"),
                    "put": put(k, good),
                    "end": end(k)} for k in KINDS}
out["state_after"] = state()
'''

MODE_ON = r'''
import guardrails
R = {}
now0 = datetime.now(timezone.utc)
E = iso(now0 + timedelta(days=3))
out["no_matrix_skip"] = put("owner", {"ends_at": E, "skip_envs": ["stst"]})
with app.session() as s:
    guardrails.apply(s, guardrails.example(), operator="verify-crunch")
S0 = state()
out["S0"] = S0
out["get0"] = call("owner", "GET", "/api/crunch")
good = {"ends_at": E, "backup": {"policy": "once", "retention_days": 14},
        "skip_envs": ["stst", "atst", "preprod"]}
for k in ("member", "owner_admin", "member_admin", "owner_read"):
    R["put " + k] = put(k, good)
    R["end " + k] = end(k)
    R["get " + k] = call(k, "GET", "/api/crunch")
    R["banner " + k] = call(k, "GET", "/api/crunch/banner")
R["put no_csrf"] = put("owner", good, csrf=False)
R["put no_password"] = put("owner", good, password=None)
R["put wrong_password"] = put("owner", good, password=WRONG)
R["end nothing set"] = end("owner")
BAD = {
    "config": None,
    "colour": {"ends_at": E, "colour": "red"},
    "ends_at": {},
    "starts_at  ": {"starts_at": iso(now0 - timedelta(hours=1)), "ends_at": E},
    "ends_at ": {"ends_at": "next tuesday"},
    "ends_at  ": {"ends_at": E[:19]},
    "ends_at   ": {"ends_at": iso(now0 - timedelta(hours=1))},
    "ends_at    ": {"ends_at": iso(now0 + timedelta(days=40))},
    "starts_at": {"starts_at": iso(now0 + timedelta(days=60)), "ends_at": iso(now0 + timedelta(days=61))},
    "backup.policy": {"ends_at": E, "backup": {"policy": "weekly"}},
    "backup.retention_days": {"ends_at": E, "backup": {"policy": "daily", "retention_days": 0}},
    "backup.retention_days ": {"ends_at": E, "backup": {"policy": "none", "retention_days": 5}},
    "backup.where": {"ends_at": E, "backup": {"policy": "daily", "where": "/tmp"}},
    "skip_envs": {"ends_at": E, "skip_envs": "stst"},
    "skip_envs[0]": {"ends_at": E, "skip_envs": ["prod"]},
    "skip_envs[0] ": {"ends_at": E, "skip_envs": ["qa"]},
    "skip_envs[1]": {"ends_at": E, "skip_envs": ["stst", "stst"]},
    "duration_hours": {"ends_at": E, "duration_hours": 5},
    "duration_hours ": {"duration_hours": 0},
    "duration_hours  ": {"duration_hours": 745},
}
out["bad"] = {k: put("owner", v) for k, v in BAD.items()}
out["after_refusals"] = state()
with engine.connect() as conn:
    out["reauth_fail_events"] = conn.execute(T(
        "SELECT count(*) FROM auth_event WHERE event = 'login_fail' AND reason LIKE 'crunch:%'")).scalar()
out["refusals"] = R

def last_event(action):
    with engine.connect() as conn:
        return [list(r) for r in conn.execute(T(
            "SELECT node_id, entity_id, actor_kind, actor_subject, detail::text FROM graph_event "
            "WHERE action = :a ORDER BY id DESC LIMIT 1"), {"a": action})]

def adr(adr_id):
    with engine.connect() as conn:
        return [list(r) for r in conn.execute(T(
            "SELECT project_node_id, status, source, title FROM adr_record WHERE adr_id = :a"),
            {"a": adr_id})]

steps = {}
def step(name, response):
    st = state()
    act = (response[1] or {}).get("action") if isinstance(response[1], dict) else None
    sid = ((response[1] or {}).get("state") or {}).get("adr_id") if isinstance(response[1], dict) else None
    steps[name] = {"r": response, "state": st, "event": last_event("node.crunch." + (act or name)),
                   "adr": adr(sid) if sid else None}

step("activate", put("owner", good))
act_state = (steps["activate"]["r"][1] or {}).get("state") or {}
out["banner_on_member"] = call("member", "GET", "/api/crunch/banner")
out["banner_on_owner"] = call("owner", "GET", "/api/crunch/banner")
out["get_on"] = call("owner", "GET", "/api/crunch")

# TASK-1432: the --crunch-from-db verbs against THIS active crunch week, each in its own process
# reading the real crunch.current() off the database (no stub), plus the state they read.
import subprocess as SP, tempfile
from pathlib import Path as PP
def admin_cli(argv):
    p = SP.run([sys.executable, "-c", "import sys, admin; sys.exit(admin.main(%r))" % (argv,)],
               capture_output=True, text=True, timeout=300)
    return [p.returncode, p.stdout, p.stderr]
with app.session() as s:
    out["current_on"] = crunch.current(s)
A0 = ["--actor", "verify-crunch"]
gen_dir = PP(tempfile.mkdtemp(prefix="verify-crunch-fromdb-")) / "out"
out["from_db"] = {
    "show": admin_cli(A0 + ["guardrails", "show", "--crunch-from-db"]),
    "export": admin_cli(A0 + ["guardrails", "export", "--format", "claude-code", "--out", "-",
                              "--crunch-from-db"]),
    "generate": admin_cli(A0 + ["cicd", "generate", "--matrix", guardrails.EXAMPLE_FILE,
                                "--answers", "data/cicd/example-answers.yaml",
                                "--platform", "github", "--out", str(gen_dir),
                                "--crunch-from-db"]),
}
out["from_db_files"] = {str(f.relative_to(gen_dir)): f.read_text(encoding="utf-8")
                        for f in sorted(gen_dir.rglob("*")) if f.is_file()} if gen_dir.exists() else {}
step("same", put("owner", dict(good, starts_at=act_state.get("starts_at"))))
ext = dict(good, starts_at=act_state.get("starts_at"), ends_at=iso(now0 + timedelta(days=5)))
out["extend_no_password"] = put("owner", ext, password=None)
out["extend_no_csrf"] = put("owner", ext, csrf=False)
out["before_extend"] = state()
step("extend", put("owner", ext))
chg = dict(ext, backup={"policy": "daily", "retention_days": 3})
out["change_wrong_password"] = put("owner", chg, password=WRONG)
step("change", put("owner", chg))
out["end_no_password"] = end("owner", password=None)
out["end_no_csrf"] = end("owner", csrf=False)
out["end_member"] = end("member")
out["end_owner_admin"] = end("owner_admin")
out["before_end"] = state()
step("end", end("owner"))
out["end_again"] = end("owner")
out["banner_after_end"] = call("member", "GET", "/api/crunch/banner")
out["get_after_end"] = call("owner", "GET", "/api/crunch")

# A scheduled start: an hour from now, by this process's clock (the margin makes it the
# server's future too). Inactive until then; it cannot be moved earlier; it can be ended.
t1 = datetime.now(timezone.utc)
sched = {"starts_at": iso(t1 + timedelta(hours=1)), "ends_at": iso(t1 + timedelta(hours=3))}
step("schedule", put("owner", sched))
out["sched_get"] = call("owner", "GET", "/api/crunch")
out["sched_banner"] = call("member", "GET", "/api/crunch/banner")
out["sched_earlier"] = put("owner", dict(sched, starts_at=iso(t1 + timedelta(minutes=30))))
out["sched_end"] = end("owner")
out["sent_start"] = sched["starts_at"]

# Expiry: activate a two-hour window, then move the clock three hours on.
# THE LENGTH: two hours from the server-set start, then extended by one from the stored end.
step("reactivate", put("owner", {"duration_hours": 2}))
step("extend_len", put("owner", {"duration_hours": 1}))
out["before_expiry"] = state()
real = crunch.now
crunch.now = lambda: real() + timedelta(hours=4)
out["banner_expired"] = [call("member", "GET", "/api/crunch/banner") for _ in range(3)]
out["get_expired"] = call("owner", "GET", "/api/crunch")
out["after_expiry"] = state()
with engine.connect() as conn:
    out["expired_events"] = [list(r) for r in conn.execute(T(
        "SELECT actor_kind, actor_subject, detail::text FROM graph_event "
        "WHERE action = 'node.crunch.end' ORDER BY id"))]
out["end_after_expiry"] = end("owner")
crunch.now = real
out["steps"] = steps
with engine.connect() as conn:
    out["blob_events"] = [list(r) for r in conn.execute(T(
        "SELECT action, actor_subject, actor_token_id, detail::text FROM graph_event "
        "WHERE action LIKE 'node.crunch.%' OR action = 'node.adr.write' ORDER BY id"))]
    out["blob_history"] = [list(r) for r in conn.execute(T(
        "SELECT action, by_subject, detail::text FROM crunch_history ORDER BY id"))]
    out["blob_adrs"] = [list(r) for r in conn.execute(T(
        "SELECT adr_id, content FROM adr_record WHERE slug LIKE 'crunch-week-%' ORDER BY seq"))]
out["tokens"] = list(tokens.values())
'''

for _name in ("MODE_NEVER", "MODE_ON"):
    globals()[_name] += '\nprint("PROBE " + json.dumps(out))\n'


def probe(mode: str, env: dict) -> dict:
    proc = run_child(["-c", PRELUDE + mode], env,
                     stdin=json.dumps({"password": PASSWORD, "wrong": WRONG}))
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE ")), None)
    if line is None and proc.returncode == 0:
        print("  (the child printed no PROBE line)")
    return json.loads(line[len("PROBE "):]) if line else {}


def reset() -> None:
    with ENGINE.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def digest() -> dict:
    """(row count, sha256 of every row) per table, except the account tables a sign-in writes.
    The KEYS are the set of tables, so a table that appears is a difference too."""
    out = {}
    with ENGINE.connect() as conn:
        meta = MetaData()
        meta.reflect(bind=conn, schema="public")
        for key in sorted(meta.tables):
            tbl = meta.tables[key]
            if tbl.name.startswith("auth_"):
                continue
            rows = sorted(repr(tuple(r)) for r in conn.execute(select(tbl)))
            out[tbl.name] = (len(rows), hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest())
        for name, last in conn.execute(text(
                "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public'")):
            if not name.startswith("auth_"):
                out[f"sequence:{name}"] = (-1, str(last))
    return out


def changed(a: dict, b: dict) -> list:
    return sorted(t for t in set(a) | set(b) if a.get(t) != b.get(t))


def err(r) -> str | None:
    return (r[1] or {}).get("error") if r and isinstance(r[1], dict) else None


def is_(r, status: int, error: str | None = None) -> bool:
    return bool(r) and r[0] == status and (error is None or err(r) == error)


def show(r) -> str:
    return f"got {r[0]} {r[2][:200]}" if r else "no response"


# --------------------------------------------------------------------------- #
print("=" * 78)
print("A. validate_config, the vocabulary, the scope map, no MCP tool")
print("=" * 78)
os.environ.setdefault("ATLAS_DB_URL", DB.url)
import crunch  # noqa: E402
import guardrails  # noqa: E402
import models_graph  # noqa: E402

AT = datetime(2030, 6, 1, 12, 0, tzinfo=timezone.utc)
MATRIX = {"environments": [{"name": "dev"}, {"name": "stst"}, {"name": "prod", "production": True}]}


def iso(dt: datetime) -> str:
    return dt.isoformat()


END = iso(AT + timedelta(days=3))
ok = crunch.validate_config({"ends_at": END}, at=AT)
check("a minimal config: starts_at None (now, set by the server when it writes), backup "
      "none/0, nothing skipped",
      ok == {"starts_at": None, "ends_at": END, "backup": {"policy": "none", "retention_days": 0},
             "skip_envs": []}, str(ok))
ok = crunch.validate_config({"starts_at": "2030-06-01T16:00:00+02:00", "ends_at": "2030-06-04T12:00:00Z",
                             "backup": {"policy": "per_deploy"}, "skip_envs": ["dev", "stst"]},
                            matrix=MATRIX, at=AT)
check("a full config: times normalised to UTC, retention defaulted, envs kept in order",
      ok == {"starts_at": iso(AT + timedelta(hours=2)), "ends_at": "2030-06-04T12:00:00+00:00",
             "backup": {"policy": "per_deploy", "retention_days": crunch.DEFAULT_RETENTION_DAYS},
             "skip_envs": ["dev", "stst"]}, str(ok))
check("every backup policy of the story is accepted",
      all(crunch.validate_config({"ends_at": END, "backup": {"policy": p}}, at=AT)["backup"]["policy"] == p
          for p in ("none", "once", "per_deploy", "hourly", "daily")))
BAD_A = [
    ("config", [1], None),
    ("colour", {"ends_at": END, "colour": "red"}, None),
    ("ends_at", {}, None),
    ("ends_at", {"ends_at": "soon"}, None),
    ("ends_at", {"ends_at": "2030-06-04T12:00:00"}, None),
    ("ends_at", {"starts_at": END, "ends_at": iso(AT + timedelta(days=1))}, None),
    ("ends_at", {"ends_at": iso(AT - timedelta(hours=1))}, None),
    ("starts_at", {"starts_at": iso(AT - timedelta(minutes=1)), "ends_at": END}, None),
    ("starts_at", {"starts_at": iso(AT), "ends_at": END}, None),
    ("ends_at", {"ends_at": iso(AT + timedelta(days=crunch.MAX_DAYS + 1))}, None),
    ("starts_at", {"starts_at": iso(AT + timedelta(days=crunch.MAX_LEAD_DAYS + 1)),
                   "ends_at": iso(AT + timedelta(days=crunch.MAX_LEAD_DAYS + 2))}, None),
    ("starts_at", {"starts_at": 5, "ends_at": END}, None),
    ("backup", {"ends_at": END, "backup": "daily"}, None),
    ("backup.policy", {"ends_at": END, "backup": {"policy": "weekly"}}, None),
    ("backup.where", {"ends_at": END, "backup": {"policy": "daily", "where": "x"}}, None),
    ("backup.retention_days", {"ends_at": END, "backup": {"policy": "daily", "retention_days": 0}}, None),
    ("backup.retention_days", {"ends_at": END, "backup": {"policy": "daily", "retention_days": 366}}, None),
    ("backup.retention_days", {"ends_at": END, "backup": {"policy": "daily", "retention_days": True}}, None),
    ("backup.retention_days", {"ends_at": END, "backup": {"policy": "none", "retention_days": 3}}, None),
    ("skip_envs", {"ends_at": END, "skip_envs": "dev"}, MATRIX),
    ("skip_envs", {"ends_at": END, "skip_envs": ["dev"]}, None),
    ("skip_envs[0]", {"ends_at": END, "skip_envs": ["prod"]}, MATRIX),
    ("skip_envs[0]", {"ends_at": END, "skip_envs": ["qa"]}, MATRIX),
    ("skip_envs[0]", {"ends_at": END, "skip_envs": ["Dev!"]}, MATRIX),
    ("skip_envs[1]", {"ends_at": END, "skip_envs": ["dev", "dev"]}, MATRIX),
]
for field, raw, matrix in BAD_A:
    try:
        crunch.validate_config(raw, matrix=matrix, at=AT)
        check(f"refused, naming {field}: {json.dumps(raw)[:80]}", False, "accepted")
    except crunch.CrunchConfigError as exc:
        check(f"refused, naming {field}: {json.dumps(raw)[:80]}", exc.field == field,
              f"named {exc.field!r}: {exc}")
try:
    crunch.validate_config({"ends_at": END, "skip_envs": ["prod"]}, matrix=MATRIX, at=AT)
except crunch.CrunchConfigError as exc:
    check("a production environment is refused saying so", "production" in str(exc), str(exc))
# THE CLOCK, with no database.
_soon = AT + timedelta(hours=1)
check("THE CLOCK: a start in the server's future is a schedule, kept to the millisecond",
      crunch.validate_config({"starts_at": iso(_soon), "ends_at": END}, at=AT)["starts_at"] == iso(_soon))
_running = AT - timedelta(hours=5)
check("THE CLOCK: the set window's own (past) start sent back unchanged is accepted",
      crunch.validate_config({"starts_at": iso(_running), "ends_at": END}, at=AT,
                             current_start=_running)["starts_at"] == iso(_running))
for _label, _start, _cur in (("another past start than the set window's", AT - timedelta(hours=4), _running),
                             ("a start earlier than a scheduled window's", AT + timedelta(minutes=30), _soon)):
    try:
        crunch.validate_config({"starts_at": iso(_start), "ends_at": END}, at=AT, current_start=_cur)
        check(f"THE CLOCK: {_label} is refused", False, "accepted")
    except crunch.CrunchConfigError as exc:
        check(f"THE CLOCK: {_label} is refused, naming starts_at", exc.field == "starts_at", str(exc))
check("THE CLOCK: a sent start is kept to whole milliseconds (what a browser sends back)",
      crunch.validate_config({"starts_at": "2030-06-01T13:00:00.123456Z", "ends_at": END},
                             at=AT)["starts_at"] == "2030-06-01T13:00:00.123000+00:00")
check("an empty skip_envs is fine with no matrix (perturbation of the no-matrix refusal)",
      crunch.validate_config({"ends_at": END, "skip_envs": []}, at=AT)["skip_envs"] == [])
check("CONFIG_SCHEMA describes exactly the five config fields",
      set(crunch.CONFIG_SCHEMA["properties"]) == set(crunch.CONFIG_KEYS) == {
          "starts_at", "ends_at", "duration_hours", "backup", "skip_envs"})

# THE LENGTH, with no database.
_d = crunch.validate_config({"duration_hours": 24}, at=AT)
check("THE LENGTH: duration_hours stands in for ends_at (start and end left to the server)",
      _d["duration_hours"] == 24 and _d["ends_at"] is None and _d["starts_at"] is None, str(_d))
for _label, _raw, _field in (
        ("both ends_at and duration_hours", {"ends_at": END, "duration_hours": 5}, "duration_hours"),
        ("neither ends_at nor duration_hours", {"backup": {"policy": "none"}}, "ends_at"),
        ("duration_hours 0", {"duration_hours": 0}, "duration_hours"),
        ("duration_hours 745", {"duration_hours": crunch.MAX_DURATION_HOURS + 1}, "duration_hours"),
        ("duration_hours true", {"duration_hours": True}, "duration_hours"),
        ("duration_hours 1.5", {"duration_hours": 1.5}, "duration_hours"),
        ("duration_hours '24'", {"duration_hours": "24"}, "duration_hours")):
    try:
        crunch.validate_config(_raw, at=AT)
        check(f"THE LENGTH: {_label} is refused", False, "accepted")
    except crunch.CrunchConfigError as exc:
        check(f"THE LENGTH: {_label} is refused, naming {_field}", exc.field == _field,
              f"named {exc.field!r}: {exc}")
check("THE LENGTH: 744 hours (31 days) is the upper bound, accepted",
      crunch.MAX_DURATION_HOURS == 744
      and crunch.validate_config({"duration_hours": 744}, at=AT)["duration_hours"] == 744)
_e = crunch._effective_start(None, crunch.validate_config({"duration_hours": 24}, at=AT), AT)
check("THE LENGTH: a new window started now ends the length after the server's now, and the "
      "config keeps ends_at only", _e["starts_at"] == iso(AT) and _e["ends_at"] == iso(AT + timedelta(hours=24))
      and "duration_hours" not in _e, str(_e))
_e = crunch._effective_start(None, crunch.validate_config(
    {"starts_at": iso(AT + timedelta(hours=2)), "duration_hours": 5}, at=AT), AT)
check("THE LENGTH: a scheduled window ends the length after its scheduled start",
      _e["ends_at"] == iso(AT + timedelta(hours=7)), str(_e))
from models_crunch import CrunchState as _CS  # noqa: E402
_row = _CS(id="current", active=True, revision=1,
           config={"starts_at": iso(AT - timedelta(hours=1)), "ends_at": iso(AT + timedelta(hours=10))})
_e = crunch._effective_start(_row, crunch.validate_config({"duration_hours": 3}, at=AT), AT)
check("THE LENGTH: with a window set, duration_hours extends it from its stored end",
      _e["ends_at"] == iso(AT + timedelta(hours=13)) and _e["starts_at"] == iso(AT - timedelta(hours=1)),
      str(_e))
_row.config = {"starts_at": iso(AT - timedelta(days=30)), "ends_at": iso(AT + timedelta(hours=10))}
try:
    crunch._effective_start(_row, crunch.validate_config({"duration_hours": 48}, at=AT), AT)
    check("THE LENGTH: an extend past MAX_DAYS in total is refused", False, "accepted")
except crunch.CrunchConfigError as exc:
    check("THE LENGTH: an extend past MAX_DAYS in total is refused, naming ends_at", exc.field == "ends_at")
want_actions = {"node.crunch.activate", "node.crunch.change", "node.crunch.extend",
                "node.crunch.end", "node.crunch.backup"}
check("the five node.crunch.* actions are in GRAPH_EVENT_ACTIONS",
      want_actions <= set(models_graph.GRAPH_EVENT_ACTIONS)
      and set(crunch.EVENT_ACTIONS.values()) == want_actions)
auth_src = (HERE / "auth.py").read_text(encoding="utf-8")
check("auth.WRITE_SCOPE_ROUTES maps both write routes to admin (read from the source; importing "
      "auth needs the peppers)", '("PUT", "/api/crunch"): "admin"' in auth_src
      and '("POST", "/api/crunch/end"): "admin"' in auth_src)
mcp_hits = sorted(str(p.relative_to(HERE.parent)) for p in (HERE.parent / "mcp").rglob("*")
                  if p.is_file() and p.suffix in (".py", ".md", ".json", ".yml", ".yaml", ".txt")
                  and "/api/crunch" in p.read_text(encoding="utf-8", errors="replace"))
check("no file under mcp/ names /api/crunch: there is no MCP tool for crunch week", not mcp_hits,
      str(mcp_hits))

# --------------------------------------------------------------------------- #
print("=" * 78)
print("B. never activated, homelab-shaped database: every caller, nothing changes")
print("=" * 78)
reset()
# STORY-oss-23 A3b: the homelab-shaped database has no atlas-system. Under the homelab copy that is
# the default boot (`homelab` never seeds the set); every profile the neutral copy offers seeds it
# on a fresh graph, so there the set's seeding is stubbed out in this one child (an install made
# before the set existed); startup is otherwise real.
import distribution  # noqa: E402  (pure data: which copy, and its default seed profile)
NO_SET = ([] if distribution.DEFAULT_SEED_PROFILE == "homelab"
          else ["import system_node", "system_node.seed = lambda s: []"])
SEED = "\n".join(NO_SET + [
    "import app, testseed",
    "from db import session",
    "from attention import recompute_derived",
    "app.startup()",
    "with session() as s:",
    "    testseed.build(s)",
    "    recompute_derived(s)",
    "    s.commit()",
])
check("homelab-shaped database built (default profile, the curated fixture)",
      run_child(["-c", SEED], {}).returncode == 0)
run_child(["-c", "import app; app.startup()"], {})  # settle sequences
before = digest()
r = probe(MODE_NEVER, {})
after = digest()
check("[never] the probe ran", bool(r))
ROUTES = [["GET", "/api/crunch"], ["GET", "/api/crunch/banner"], ["POST", "/api/crunch/end"],
          ["PUT", "/api/crunch"]]
if r:
    check("[never] the four routes are registered", r["routes"] == ROUTES, str(r["routes"]))
    c = r["calls"]
    g = c["owner"]["get"]
    check("[never] GET /api/crunch, owner session: 200, off, nothing set, no envs, no-store",
          g[0] == 200 and g[1] == {"active": False, "state": None, "history": [], "envs": [],
                                   "production_envs": [], "last_backup": None}
          and g[3] == "no-store", show(g))
    check("[never] GET /api/crunch, member session: 403 forbidden", is_(c["member"]["get"], 403, "forbidden"),
          show(c["member"]["get"]))
    for k in ("owner_admin", "member_admin", "owner_read"):
        check(f"[never] GET /api/crunch, {k} token: 403 session_required",
              is_(c[k]["get"], 403, "session_required"), show(c[k]["get"]))
        check(f"[never] GET /api/crunch/banner, {k} token: 403 session_required",
              is_(c[k]["banner"], 403, "session_required"), show(c[k]["banner"]))
        want = ("forbidden" if k == "owner_read" else "session_required")
        check(f"[never] PUT /api/crunch, {k} token: 403 {want}", is_(c[k]["put"], 403, want), show(c[k]["put"]))
        check(f"[never] POST /api/crunch/end, {k} token: 403 {want}", is_(c[k]["end"], 403, want),
              show(c[k]["end"]))
    check("[never] an owner's read token is refused on the admin scope",
          "admin" in ((c["owner_read"]["put"][1] or {}).get("detail", "")))
    for k in ("owner", "member"):
        b = c[k]["banner"]
        check(f"[never] banner, {k} session: 200 exactly {{active: false, ends_at: null}}, no-store",
              b[0] == 200 and b[1] == {"active": False, "ends_at": None} and b[3] == "no-store", show(b))
    for route in ("get", "banner", "put", "end"):
        check(f"[never] {route}, anonymous: 401", c["anon"][route][0] == 401, show(c["anon"][route]))
    check("[never] PUT/end, member session: 403 forbidden",
          is_(c["member"]["put"], 403, "forbidden") and is_(c["member"]["end"], 403, "forbidden"))
    p = c["owner"]["put"]
    check("[never] PUT, owner with CSRF and password, no atlas-system: 409 system_node_missing, "
          "saying how to add it", is_(p, 409, "system_node_missing")
          and "system_node.py --apply" in (p[1] or {}).get("detail", ""), show(p))
    check("[never] POST end, owner: 409 not_active", is_(c["owner"]["end"], 409, "not_active"),
          show(c["owner"]["end"]))
    st = r["state_after"]
    check("[never] no crunch table was created and no node.crunch.* event written",
          not st["table"] and not st["history_table"] and st["events"] == {}, str(st))
ch = changed(before, after)
check("[never] every table is byte-identical, and the set of tables is the same", not ch, ", ".join(ch))
check("[never] ...and the comparison covers the graph", "node" in before and "graph_event" in before,
      f"{len(before)} tables and sequences")

# --------------------------------------------------------------------------- #
print("=" * 78)
print("C. on an ATLAS_SEED=none install with the example matrix: refusals, acts, expiry")
print("=" * 78)
reset()
check("fresh ATLAS_SEED=none boot", run_child(["-c", "import app; app.startup()"], NONE).returncode == 0)
r = probe(MODE_ON, NONE)
check("[on] the probe ran", bool(r))
if r:
    p = r["no_matrix_skip"]
    check("[on] with no matrix applied, a non-empty skip_envs is 400 bad_config naming skip_envs",
          is_(p, 400, "bad_config") and (p[1] or {}).get("field") == "skip_envs", show(p))
    g = r["get0"][1] or {}
    check("[on] GET lists the matrix's environments, prod as production",
          g.get("envs") == ["dev", "stst", "atst", "preprod", "prod"]
          and g.get("production_envs") == ["prod"], str(g.get("envs")))
    R = r["refusals"]
    expect = {"put member": (403, "forbidden"), "end member": (403, "forbidden"),
              "get member": (403, "forbidden"), "banner member": (200, None),
              "put no_csrf": (403, "csrf_failed"), "put no_password": (403, "step_up_required"),
              "put wrong_password": (403, "password_incorrect"), "end nothing set": (409, "not_active")}
    for k in ("owner_admin", "member_admin", "owner_read"):
        tok = "forbidden" if k == "owner_read" else "session_required"
        expect.update({f"put {k}": (403, tok), f"end {k}": (403, tok),
                       f"get {k}": (403, "session_required"), f"banner {k}": (403, "session_required")})
    check("[on] every refusal case ran", set(R) == set(expect), str(set(R) ^ set(expect)))
    for case, (status, error) in sorted(expect.items()):
        check(f"[on] {case}: {status} {error or ''}".rstrip(), is_(R.get(case), status, error),
              show(R.get(case)))
    check("[on] a missing or wrong password names the field `password`",
          all((R[c][1] or {}).get("field") == "password" for c in ("put no_password", "put wrong_password")))
    for field, resp in sorted(r["bad"].items()):
        check(f"[on] bad config -> 400 bad_config naming {field.strip()}",
              is_(resp, 400, "bad_config") and (resp[1] or {}).get("field") == field.strip(), show(resp))
    check("[on] the refusals wrote nothing: no crunch table, no node.crunch.* event, no ADR",
          r["after_refusals"] == r["S0"] and not r["S0"]["table"], f"{r['S0']} -> {r['after_refusals']}")
    check("[on] the wrong password is one auth_event login_fail `crunch:`", r["reauth_fail_events"] == 1,
          str(r["reauth_fail_events"]))

    S = r["steps"]
    base = r["S0"]

    def delta(a: dict, b: dict) -> tuple:
        ev = {k: b["events"].get(k, 0) - a["events"].get(k, 0)
              for k in set(a["events"]) | set(b["events"]) if b["events"].get(k, 0) != a["events"].get(k, 0)}
        return ev, b["adrs"] - a["adrs"], b["adr_events"] - a["adr_events"]

    prev = base
    for name, act, before_key in (("activate", "activate", None), ("extend", "extend", "before_extend"),
                                  ("change", "change", None), ("end", "end", "before_end")):
        sres = S.get(name) or {}
        resp = sres.get("r")
        if before_key:
            prev = r[before_key]
        ev, adrs, adr_ev = delta(prev, sres.get("state", prev))
        check(f"[on] {name}: 200, action {act}", bool(resp) and resp[0] == 200
              and (resp[1] or {}).get("action") == act, show(resp))
        check(f"[on] {name}: exactly one node.crunch.{act} event, one ADR and its one node.adr.write",
              ev == {f"node.crunch.{act}": 1} and adrs == 1 and adr_ev == 1, f"{ev} adrs+{adrs} adr_ev+{adr_ev}")
        e = sres.get("event") or []
        check(f"[on] {name}: the event is on atlas-system, by the owner's session",
              bool(e) and e[0][0] == "atlas-system" and e[0][1] == "atlas-system"
              and e[0][2] == "session" and e[0][3] == "cr-owner", str(e))
        if name != "end":
            a = sres.get("adr") or []
            check(f"[on] {name}: its ADR is Accepted, source atlas, on atlas-system",
                  bool(a) and a[0][0] == "atlas-system" and a[0][1] == "accepted" and a[0][2] == "atlas",
                  str(a))
        prev = sres.get("state", prev)
    d = json.loads((S["end"].get("event") or [[None] * 5])[0][4] or "{}")
    check("[on] the manual end's event says reason manual", d.get("reason") == "manual", str(d))
    st = (S["activate"]["r"][1] or {}).get("state") or {}
    check("[on] the activated state: the config plus active, activated_by, activated_at, adr_id",
          st.get("active") is True and st.get("activated_by") == "cr-owner" and st.get("adr_id")
          and st.get("activated_at") and st.get("skip_envs") == ["stst", "atst", "preprod"]
          and st.get("backup") == {"policy": "once", "retention_days": 14}, str(st))
    check("[on] THE CLOCK: activated with no starts_at, its start IS activated_at (the server's "
          "clock), and it is in force at once", st.get("starts_at") == st.get("activated_at")
          and st.get("active") is True, f"{st.get('starts_at')} vs {st.get('activated_at')}")
    ev_detail = json.loads((S["activate"].get("event") or [[None] * 5])[0][4] or "{}")
    check("[on] the activate event names the ADR and the window", ev_detail.get("adr_id") == st.get("adr_id")
          and ev_detail.get("ends_at") == st.get("ends_at"), str(ev_detail))
    bm, bo = r["banner_on_member"], r["banner_on_owner"]
    check("[on] the banner, to a member and to the owner: exactly {active: true, ends_at}",
          bm[0] == 200 and bm[1] == {"active": True, "ends_at": st.get("ends_at")} and bo[1] == bm[1],
          show(bm))
    go = r["get_on"][1] or {}
    check("[on] GET shows it active with one history entry {action, at, by}",
          go.get("active") is True and [h.get("action") for h in go.get("history", [])] == ["activate"]
          and go["history"][0].get("by") == "cr-owner", str(go.get("history")))
    # TASK-1432: --crunch-from-db while crunch week is on, against the real state.
    cur = r.get("current_on") or {}
    check("[on] crunch.current() while on: the config plus state-only keys, revision among them, "
          "every key one the release path accepts (guardrails.CRUNCH_KEYS)",
          cur.get("active") is True and "revision" in cur and set(cur) <= guardrails.CRUNCH_KEYS,
          f"{sorted(cur)}; not accepted: {sorted(set(cur) - guardrails.CRUNCH_KEYS)}")
    fd = r.get("from_db") or {}
    sh = fd.get("show") or [None, "", ""]
    check("[on] guardrails show --crunch-from-db succeeds and shows the overlay",
          sh[0] == 0 and "Crunch week" in sh[1] and "unknown key" not in sh[2], (sh[2] or "")[-200:])
    ex = fd.get("export") or [None, "", ""]
    deny = (json.loads(ex[1]) if ex[0] == 0 and ex[1] else {}).get("permissions", {}).get("deny", [])
    check("[on] guardrails export --crunch-from-db succeeds and still denies the agent prod deploy",
          ex[0] == 0 and "Bash(tools/deploy.sh prod*)" in deny, (ex[2] or "")[-200:])
    gn = fd.get("generate") or [None, "", ""]
    files = r.get("from_db_files") or {}
    check("[on] cicd generate --crunch-from-db succeeds and writes the crunch variant",
          gn[0] == 0 and any("CRUNCH WEEK VARIANT" in t and "stst, atst, preprod" in t
                             for t in files.values()), f"{sorted(files)} {(gn[2] or '')[-200:]}")
    same = S["same"]
    check("[on] the same config again: 200, changed false, nothing written",
          same["r"][0] == 200 and (same["r"][1] or {}).get("changed") is False
          and same["state"] == S["activate"]["state"], show(same["r"]))
    check("[on] extend without the password: step_up_required; without CSRF: csrf_failed",
          is_(r["extend_no_password"], 403, "step_up_required") and is_(r["extend_no_csrf"], 403, "csrf_failed"))
    check("[on] change with a wrong password: password_incorrect",
          is_(r["change_wrong_password"], 403, "password_incorrect"))
    check("[on] end without the password, without CSRF, by a member, by an owner's admin token: refused by name",
          is_(r["end_no_password"], 403, "step_up_required") and is_(r["end_no_csrf"], 403, "csrf_failed")
          and is_(r["end_member"], 403, "forbidden") and is_(r["end_owner_admin"], 403, "session_required"))
    check("[on] those refusals wrote nothing", r["before_extend"] == S["same"]["state"]
          and r["before_end"] == S["change"]["state"])
    check("[on] a second end: 409 not_active", is_(r["end_again"], 409, "not_active"), show(r["end_again"]))
    check("[on] after the end the banner is off", r["banner_after_end"][1] == {"active": False, "ends_at": None})
    ga = r["get_after_end"][1] or {}
    check("[on] ...and GET is off, its history newest first: end, change, extend, activate",
          ga.get("active") is False and ga.get("state") is None
          and [h["action"] for h in ga.get("history", [])] == ["end", "change", "extend", "activate"],
          str(ga.get("history")))

    # Expiry.
    sg = (r["sched_get"][1] or {})
    ss = sg.get("state") or {}
    check("[on] THE CLOCK: a start an hour ahead is scheduled: 200 activate, GET not active, its "
          "state active false with the start as sent",
          (S["schedule"]["r"][1] or {}).get("action") == "activate" and sg.get("active") is False
          and ss.get("active") is False and crunch._parse_ts("x", ss.get("starts_at"))
          == crunch._parse_ts("x", r["sent_start"]), f"{sg.get('active')} {ss}")
    check("[on] ...the banner is off until then",
          r["sched_banner"][1] == {"active": False, "ends_at": None}, show(r["sched_banner"]))
    check("[on] ...moving that start earlier is 400 bad_config naming starts_at",
          is_(r["sched_earlier"], 400, "bad_config")
          and (r["sched_earlier"][1] or {}).get("field") == "starts_at", show(r["sched_earlier"]))
    check("[on] ...and a scheduled window can be ended", is_(r["sched_end"], 200), show(r["sched_end"]))
    ra = (S["reactivate"]["r"][1] or {}).get("state") or {}
    ex = (S["extend_len"]["r"][1] or {}).get("state") or {}
    P = crunch._parse_ts
    check("[on] THE LENGTH: activated with duration_hours 2: in force at once, starts_at == "
          "activated_at, ends_at exactly two hours later, no duration_hours in the state",
          ra.get("active") is True and ra.get("starts_at") == ra.get("activated_at")
          and ra.get("ends_at") and P("x", ra["ends_at"]) - P("x", ra["starts_at"]) == timedelta(hours=2)
          and "duration_hours" not in ra, str(ra))
    check("[on] THE LENGTH: duration_hours 1 on the running window is an extend from its stored end",
          (S["extend_len"]["r"][1] or {}).get("action") == "extend" and ex.get("ends_at")
          and ra.get("ends_at") and P("x", ex["ends_at"]) - P("x", ra["ends_at"]) == timedelta(hours=1)
          and ex.get("starts_at") == ra.get("starts_at"), f"{ra.get('ends_at')} -> {ex.get('ends_at')}")
    check("[on] reactivated for two hours", (S["reactivate"]["r"][1] or {}).get("action") == "activate",
          show(S["reactivate"]["r"]))
    be = r["banner_expired"]
    check("[on] expiry: with the clock past ends_at the banner says off, three reads in a row",
          all(b[0] == 200 and b[1] == {"active": False, "ends_at": None} for b in be), str([b[1] for b in be]))
    ev, adrs, adr_ev = delta(r["before_expiry"], r["after_expiry"])
    check("[on] expiry: exactly ONE node.crunch.end and one ADR, however many reads followed",
          ev == {"node.crunch.end": 1} and adrs == 1 and adr_ev == 1, f"{ev} adrs+{adrs}")
    last = r["expired_events"][-1] if r["expired_events"] else [None, None, "{}"]
    check("[on] ...with reason expired, written with no principal (actor system)",
          json.loads(last[2]).get("reason") == "expired" and last[0] == "system", str(last))
    ge = r["get_expired"][1] or {}
    check("[on] ...and GET shows it off, the latest history entry an end by system",
          ge.get("active") is False and (ge.get("history") or [{}])[0].get("by") == "system"
          and ge["history"][0].get("reason") == "expired", str(ge.get("history", [])[:1]))
    check("[on] ending an expired crunch week: 409 not_active", is_(r["end_after_expiry"], 409, "not_active"))

    # No secret anywhere.
    secrets = [PASSWORD, WRONG, PEPPER_P, PEPPER_T] + list(r.get("tokens") or [])
    stored = json.dumps([r["blob_events"], r["blob_history"], r["blob_adrs"]])
    found = [x[:6] + "..." for x in secrets if x and x in stored]
    check("[on] no password, pepper or token value in any event, history row or ADR", not found, str(found))
    responses = json.dumps({k: v for k, v in r.items() if k not in ("tokens",)})
    found = [x[:6] + "..." for x in (PASSWORD, WRONG, PEPPER_P, PEPPER_T) if x in responses]
    check("[on] ...nor in any response", not found, str(found))
    check("[on] ...and the search finds one when it is there (perturbation)", PASSWORD in json.dumps(
        [stored, PASSWORD]))
    check("[on] the ADRs restate the window and name the act", r["blob_adrs"]
          and all("Crunch week" in a[1] and "Window:" in a[1] for a in r["blob_adrs"]))

if failures:
    print(f"\n{len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nall checks passed")
