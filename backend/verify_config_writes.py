"""
Verification for STORY-atlas-config-panel, PHASE 2: changing a safe setting from the board.

The design and the precedence (environment > board > default) are in routers/config.py's header;
the `changeable` mark and its condition in settings_catalog.py's; the table in models_config.py.
Sections:

  A. No database: the changeable set is exactly ATLAS_STRICT_API and ATLAS_RETRIEVAL_TOP_K, none
     of them a secret or bootstrap; every bootstrap setting, every secret and each deliberately
     excluded setting (RBAC, identity, OAuth, the feature flags, the switch itself, ...) is
     refused by `change_refusal` with its named error, bootstrap first; `ATLAS_CONFIG_WRITES`
     parses like its neighbours (blank or unknown raises); the validators accept and refuse as
     documented; and each changeable setting's real reader re-reads the process environment
     (`payload_fields.strict_api()`, `retrieval.query.top_k()`), which is what makes "takes
     effect now" true.
  B. FLAG OFF on a homelab-shaped database (criterion 2): no route under /api/config but GET, no
     scope entry, no `config_value` table; a PUT on the setting route by a signed-in owner with
     CSRF and the right password answers byte for byte what a PUT on a path that never existed
     answers (status, body, content type), for a changeable setting and for a bootstrap one
     (criterion 4, off), and by an owner's admin token likewise; GET is phase 1's body; strict
     mode stays off; every graph and data table is byte-identical afterwards.
  C. FLAG ON (criterion 3): GET says writes are enabled and marks each setting changeable or
     not; each refusal is its named error and changes nothing (member session, owner without the
     password, wrong password (an auth_event login_fail `config_write:`), no CSRF, an owner's
     admin token, an owner's read token, a member's admin token, an unknown setting, two secrets,
     the four bootstrap settings (criterion 4, on), three excluded settings, three bad values);
     then an owner with the password sets ATLAS_STRICT_API: 200, source `board`, the reader sees
     it at once and a workitem POST with an unknown field turns from accepted to 422, one
     `node.config.set` graph_event on `atlas-system-api` naming the owner, the setting and the
     sources and no value (no from/to key, neither value anywhere in the row); the same value again
     changes nothing; ATLAS_RETRIEVAL_TOP_K set to 173 and reset with null (back to default, row
     gone). No secret's value appears in any response or event.
  D. BOOT: a second boot with the flag on applies the board value (source `board`, the reader
     sees it) and is a read (every table byte-identical); with ATLAS_STRICT_API=0 in the
     environment, the environment wins (source env) and a PUT is 409 `environment_wins`; with
     the flag off again the stored row is not applied and the setting route is the unknown
     route's 404 again.
  E. verify_node_access.py and verify_object_graph.py pass with ATLAS_CONFIG_WRITES=1, so the new
     route is classified in §F and the read-only Bearer refusal in §20 covers it.

Run:
    python verify_config_writes.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
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

import settings  # noqa: E402
import settings_catalog as C  # noqa: E402

ENGINE = create_engine(DB.url)
ORIGIN = "https://testserver"
PASSWORD = "Correct-Horse-Battery-9!"
FLAG = C.CONFIG_WRITES_ENV

OWN = {s.name for s in C.CATALOG} | {s.name + "_FILE" for s in C.CATALOG if s.secret} | {
    "ATLAS_RESEED", FLAG}
#: Kept from the runner: CI's second pass disables every feature, and that must still pass.
KEEP = {"ATLAS_DISABLED_FEATURES"}
PEPPER_P = base64.b64encode(os.urandom(32)).decode()
PEPPER_T = base64.b64encode(os.urandom(32)).decode()
BASE_ENV = {k: v for k, v in os.environ.items() if k not in OWN or k in KEEP}
BASE_ENV.update({"ATLAS_BREACH_CHECK": "bundled", "ATLAS_LOGIN_ORIGINS": ORIGIN,
                 "ATLAS_FRONTEND_DIR": str(HERE.parent / "frontend"),
                 "ATLAS_PASSWORD_PEPPER": PEPPER_P, "ATLAS_TOKEN_PEPPER": PEPPER_T,
                 "ATLAS_DB_URL": DB.url})
ON = {FLAG: "1"}

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
import json, os, re, sys
from datetime import datetime, timezone
cfg = json.loads(sys.stdin.read())
import app, auth, passwords, payload_fields
from db import engine
from models_auth import AuthUser
from route_table import live_routes
from sqlalchemy import text as T
app.startup()
from fastapi.testclient import TestClient
ORIGIN = "https://testserver"
PW = cfg["password"]
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
        raw, _row = auth.mint_service_token(s, owner=s.get(AuthUser, uid), scopes=scopes, label="cw")
        s.commit()
        return raw

def sign_in(subject):
    c = TestClient(app.app, base_url=ORIGIN, follow_redirects=False)
    page = c.get("/auth/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    c.post("/auth/login", data={"username": subject, "password": PW, "csrf_token": csrf},
           headers={"Origin": ORIGIN})
    return c, c.get("/api/me").json()["csrf_token"]

user("u-cw-owner", "cw-owner", role="owner", password=True)
user("u-cw-member", "cw-member", password=True)
user("u-cw-owner-bot", "bot-cw-owner", is_service=True, person="u-cw-owner")
user("u-cw-member-bot", "bot-cw-member", is_service=True, person="u-cw-member")
tokens = {"owner_admin": mint("u-cw-owner-bot", ["read", "write", "admin"]),
          "owner_read": mint("u-cw-owner-bot", ["read"]),
          "member_admin": mint("u-cw-member-bot", ["read", "write", "admin"])}
owner, owner_csrf = sign_in("cw-owner")
member, member_csrf = sign_in("cw-member")
anon = TestClient(app.app, base_url=ORIGIN, follow_redirects=False)

def res(r):
    try:
        body = r.json()
    except Exception:
        body = None
    return [r.status_code, body, r.text, r.headers.get("content-type")]

def put(client, name, value, password=PW, csrf=None, bearer=None, path="/api/config/"):
    body = {"value": value}
    if password is not None:
        body["password"] = password
    if csrf is not None:
        body["csrf_token"] = csrf
    headers = dict(H)
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    return res(client.put(path + name, json=body, headers=headers))

def state():
    with engine.connect() as conn:
        ev = conn.execute(T("SELECT count(*) FROM graph_event WHERE action = 'node.config.set'")).scalar()
        has = conn.execute(T("SELECT to_regclass('public.config_value') IS NOT NULL")).scalar()
        rows = ([list(r) for r in conn.execute(T("SELECT name, value FROM config_value ORDER BY name"))]
                if has else None)
    return {"events": ev, "table": bool(has), "rows": rows}

def entry(body, name):
    return next((e for p in body.get("parts", []) for e in p["settings"] if e["name"] == name), None)

def top_k():
    from retrieval import query
    return query.top_k()

out = {"routes": sorted([m, r.path] for r in live_routes(app.app)
                        if getattr(r, "path", "").startswith("/api/config")
                        for m in (getattr(r, "methods", None) or ["*"])),
       "write_scope": sorted(" ".join(k) + "=" + v for k, v in auth.WRITE_SCOPE_ROUTES.items()
                             if k[1].startswith("/api/config")),
       "strict_at_boot": payload_fields.strict_api(),
       "env_strict_at_boot": os.environ.get("ATLAS_STRICT_API"),
       "state_at_boot": state()}
out["get"] = res(owner.get("/api/config", headers=J))
'''

MODE_OFF = r'''
out["off"] = {}
for name in ("ATLAS_STRICT_API", "ATLAS_DB_URL", "ATLAS_PASSWORD_PEPPER"):
    real = put(owner, name, "1", csrf=owner_csrf)
    never = put(owner, name, "1", csrf=owner_csrf, path="/api/zz-never-existed/")
    tok = put(anon, name, "1", bearer=tokens["owner_admin"])
    tok_never = put(anon, name, "1", bearer=tokens["owner_admin"], path="/api/zz-never-existed/")
    out["off"][name] = {"real": real, "never": never, "token": tok, "token_never": tok_never}
out["strict_after"] = payload_fields.strict_api()
out["state_after"] = state()
'''

MODE_ON = r'''
R = {}
S0 = state()
R["member"] = put(member, "ATLAS_STRICT_API", "1", csrf=member_csrf)
R["no_password"] = put(owner, "ATLAS_STRICT_API", "1", password=None, csrf=owner_csrf)
R["wrong_password"] = put(owner, "ATLAS_STRICT_API", "1", password="not-the-password-1!", csrf=owner_csrf)
R["no_csrf"] = put(owner, "ATLAS_STRICT_API", "1")
R["owner_admin_token"] = put(anon, "ATLAS_STRICT_API", "1", bearer=tokens["owner_admin"])
R["owner_read_token"] = put(anon, "ATLAS_STRICT_API", "1", bearer=tokens["owner_read"])
R["member_admin_token"] = put(anon, "ATLAS_STRICT_API", "1", bearer=tokens["member_admin"])
R["unknown"] = put(owner, "ATLAS_NO_SUCH_SETTING", "1", csrf=owner_csrf)
for n in ("ATLAS_EMBED_KEY", "ATLAS_OIDC_CLIENT_SECRET"):
    R["secret " + n] = put(owner, n, "x", csrf=owner_csrf)
for n in ("ATLAS_DB_URL", "ATLAS_PASSWORD_PEPPER", "ATLAS_TOKEN_PEPPER", "ATLAS_LOGIN_ORIGINS"):
    R["bootstrap " + n] = put(owner, n, "x", csrf=owner_csrf)
for n, v in (("ATLAS_RBAC", "projects"), ("ATLAS_DISABLED_FEATURES", "github_link"),
             ("ATLAS_AUTH_PROVIDERS", "local")):
    R["excluded " + n] = put(owner, n, v, csrf=owner_csrf)
R["bad strict"] = put(owner, "ATLAS_STRICT_API", "maybe", csrf=owner_csrf)
R["bad top_k 0"] = put(owner, "ATLAS_RETRIEVAL_TOP_K", "0", csrf=owner_csrf)
R["bad top_k abc"] = put(owner, "ATLAS_RETRIEVAL_TOP_K", "abc", csrf=owner_csrf)
out["refusals"] = R
out["state_after_refusals"] = state()
out["strict_after_refusals"] = payload_fields.strict_api()
with engine.connect() as conn:
    out["reauth_fail_events"] = conn.execute(T(
        "SELECT count(*) FROM auth_event WHERE event = 'login_fail' AND reason LIKE 'config_write:%'")).scalar()

wi = {"type": "task", "name": "cw unknown field", "colour": "red", "csrf_token": owner_csrf}
out["workitem_before"] = owner.post("/api/workitem", json=wi, headers=H).status_code
out["set_strict"] = put(owner, "ATLAS_STRICT_API", True, csrf=owner_csrf)
out["strict_live"] = payload_fields.strict_api()
out["workitem_after"] = owner.post("/api/workitem", json=dict(wi, name="cw unknown field 2"), headers=H).status_code
with engine.connect() as conn:
    out["last_event"] = [list(r) for r in conn.execute(T(
        "SELECT node_id, entity_id, actor_kind, actor_subject, detail::text FROM graph_event "
        "WHERE action = 'node.config.set' ORDER BY id DESC LIMIT 1"))]
out["state_after_set"] = state()
out["same_again"] = put(owner, "ATLAS_STRICT_API", "on", csrf=owner_csrf)
out["state_after_same"] = state()
out["get_after_set"] = res(owner.get("/api/config", headers=J))
out["set_top_k"] = put(owner, "ATLAS_RETRIEVAL_TOP_K", 173, csrf=owner_csrf)
out["top_k_live"] = top_k()
out["env_top_k_after_set"] = os.environ.get("ATLAS_RETRIEVAL_TOP_K")
out["reset_top_k"] = put(owner, "ATLAS_RETRIEVAL_TOP_K", None, csrf=owner_csrf)
out["env_top_k_after_reset"] = os.environ.get("ATLAS_RETRIEVAL_TOP_K")
out["top_k_after_reset"] = top_k()
out["state_final"] = state()
with engine.connect() as conn:
    # Every column but the serial id and the timestamp, as text: where a value could hide.
    out["all_events"] = [list(r) for r in conn.execute(T(
        "SELECT action, entity_type, entity_id, node_id, related_node_id, actor_kind, "
        "actor_user_id, actor_subject, actor_token_id, responsible_user_id, detail::text "
        "FROM graph_event WHERE action = 'node.config.set' ORDER BY id"))]
'''

MODE_BOOT = r'''
out["put_strict_off"] = put(owner, "ATLAS_STRICT_API", "0", csrf=owner_csrf)
out["state_after"] = state()
'''


def probe(mode: str, env: dict) -> dict:
    proc = run_child(["-c", PRELUDE + mode], env, stdin=json.dumps({"password": PASSWORD}))
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE ")), None)
    if line is None and proc.returncode == 0:
        print("  (the child printed no PROBE line)")
    return json.loads(line[len("PROBE "):]) if line else {}


for _name in ("MODE_OFF", "MODE_ON", "MODE_BOOT"):
    globals()[_name] += '\nprint("PROBE " + json.dumps(out))\n'


def reset() -> None:
    with ENGINE.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def digest() -> dict:
    """(row count, sha256 of every row) per table, except the account tables a sign-in writes."""
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


def entries(body) -> dict:
    return {e["name"]: e for p in (body or {}).get("parts", []) for e in p["settings"]}


SECRETS = [PEPPER_P, PEPPER_T, "postgres:%s@" % testdb.POSTGRES_PASSWORD]


#: A value that went through a config change in section C, as a whole JSON token.
VALUE_TOKEN = re.compile(r"(?<![\w-])(true|false|on|off|0|1|30|173)(?![\w-])", re.I)


def leaks(blob: str) -> list:
    return [s[:6] + "..." for s in SECRETS if s in blob]


# --------------------------------------------------------------------------- #
print("=" * 78)
print("A. the changeable set, the refusal rule, the flag, the validators, the readers")
print("=" * 78)
check("the changeable set is exactly ATLAS_STRICT_API and ATLAS_RETRIEVAL_TOP_K",
      set(C.CHANGEABLE) == {"ATLAS_STRICT_API", "ATLAS_RETRIEVAL_TOP_K"}, str(C.CHANGEABLE))
check("no changeable setting is a secret or bootstrap, and each has a validator",
      all(not C.BY_NAME[n].secret and C.BY_NAME[n].kind == "plain" and C.BY_NAME[n].validate
          for n in C.CHANGEABLE))
boots = [s.name for s in C.CATALOG if s.kind == "bootstrap"]
check("criterion 4: every bootstrap setting is refused as bootstrap_read_only",
      boots and all(C.change_refusal(n)[0] == "bootstrap_read_only" for n in boots), str(boots))
secrets = [s.name for s in C.CATALOG if s.secret and s.kind != "bootstrap"]
check("every secret is refused as secret_not_changeable",
      secrets and all(C.change_refusal(n)[0] == "secret_not_changeable" for n in secrets))
must_exclude = [settings.RBAC_ENV, settings.DEFAULT_PROJECT_VISIBILITY_ENV,
                settings.SHARED_PROJECT_ROLE_ENV, settings.INSTANCE_ADMIN_IMPLICIT_OWNER_ENV,
                settings.DISABLED_FEATURES_ENV, FLAG, settings.ACCEPTANCE_AGENT_WRITE_ENV,
                settings.AUDIT_READS_ENV, settings.PUBLIC_DOCS_ENV] + [
    s.name for s in C.CATALOG if s.loader in ("identity", "oauth") and not s.secret]
check("RBAC, identity/OAuth, the feature flags, the switch itself, the acceptance rail, the "
      "audit and public docs are refused as not_changeable, each with its stated reason",
      all(C.change_refusal(n)[0] == "not_changeable" and n in C.EXCLUDED for n in must_exclude),
      str([n for n in must_exclude if C.change_refusal(n)[0] != "not_changeable" or n not in C.EXCLUDED]))
check("an unknown name is unknown_setting", C.change_refusal("ATLAS_NO_SUCH")[0] == "unknown_setting")
check("every changeable setting passes the rule", all(C.change_refusal(n) is None for n in C.CHANGEABLE))
check("the switch is described on the root part, read from its own loader",
      C.BY_NAME[FLAG].part == "atlas-system" and not C.BY_NAME[FLAG].changeable)
check("the flag: unset off, 1 on, 0 off",
      C.load_config_writes({}) is False and C.load_config_writes({FLAG: "1"}) is True
      and C.load_config_writes({FLAG: "off"}) is False)
for bad in ("", "  ", "maybe"):
    try:
        C.load_config_writes({FLAG: bad})
        check(f"the flag {bad!r} raises", False)
    except settings.SettingsError as exc:
        check(f"the flag {bad!r} raises, naming the variable", FLAG in str(exc))
vs, vk = C.BY_NAME["ATLAS_STRICT_API"].validate, C.BY_NAME["ATLAS_RETRIEVAL_TOP_K"].validate
check("strict validator: True/'on'/'1' -> '1', False/'off'/'0' -> '0'",
      [vs(v) for v in (True, "on", "1", False, "off", "0")] == ["1"] * 3 + ["0"] * 3)
check("top-k validator: 7/'7'/' 200 ' accepted as text",
      [vk(v) for v in (7, "7", " 200 ")] == ["7", "7", "200"])
for v in ("maybe", 2, None, [1]):
    try:
        vs(v)
        check(f"strict validator refuses {v!r}", False)
    except ValueError as exc:
        check(f"strict validator refuses {v!r}, naming the setting", "ATLAS_STRICT_API" in str(exc))
for v in (0, 201, "abc", True, 1.5):
    try:
        vk(v)
        check(f"top-k validator refuses {v!r}", False)
    except ValueError:
        check(f"top-k validator refuses {v!r}", True)
# The condition for `changeable`: the real reader re-reads os.environ on every use.
import payload_fields  # noqa: E402
from retrieval import query as _rq  # noqa: E402

saved = {n: os.environ.pop(n, None) for n in C.CHANGEABLE}
try:
    a = payload_fields.strict_api()
    os.environ["ATLAS_STRICT_API"] = "1"
    b = payload_fields.strict_api()
    k0 = _rq.top_k()
    os.environ["ATLAS_RETRIEVAL_TOP_K"] = "9"
    k1 = _rq.top_k()
finally:
    for n, v in saved.items():
        os.environ.pop(n, None)
        if v is not None:
            os.environ[n] = v
check("payload_fields.strict_api() re-reads the environment per call (off -> on)",
      a is False and b is True)
check("retrieval.query.top_k() re-reads the environment per call (30 -> 9)", k0 == 30 and k1 == 9)

# --------------------------------------------------------------------------- #
print("=" * 78)
print("B. flag OFF on a homelab-shaped database: nothing can be changed, nothing changes")
print("=" * 78)
reset()
SEED = "\n".join([
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
r = probe(MODE_OFF, {})
after = digest()
check("[off] the probe ran", bool(r))
if r:
    check("[off] criterion 2: the only route under /api/config is GET",
          r["routes"] == [["GET", "/api/config"]], str(r["routes"]))
    check("[off] WRITE_SCOPE_ROUTES names nothing under /api/config", r["write_scope"] == [],
          str(r["write_scope"]))
    check("[off] no config_value table exists", r["state_at_boot"]["table"] is False)
    g = r["get"]
    body = g[1] or {}
    check("[off] GET is phase 1's body: read_only, no writes block, no changeable mark",
          g[0] == 200 and body.get("read_only") is True and "writes" not in body
          and not any("changeable" in e for e in entries(body).values()))
    for name, o in r["off"].items():
        same = (o["real"][0] == o["never"][0] and o["real"][2] == o["never"][2]
                and o["real"][3] == o["never"][3])
        check(f"[off] PUT /api/config/{name} by an owner with CSRF and password answers exactly "
              "as a path that never existed", same and o["real"][0] == 404,
              f"{o['real'][:1] + o['real'][2:]} vs {o['never'][:1] + o['never'][2:]}")
        same = (o["token"][0] == o["token_never"][0] and o["token"][2] == o["token_never"][2])
        check(f"[off] ...and by an owner's admin token, likewise", same, str(o["token"][:1]))
    check("[off] strict mode is off before and after", r["strict_at_boot"] is False
          and r["strict_after"] is False)
    check("[off] no node.config.set event", r["state_after"]["events"] == 0)
ch = changed(before, after)
check("[off] criterion 2: every graph and data table is byte-identical afterwards", not ch,
      ", ".join(ch))
check("[off] ...and the comparison covers the graph", "node" in before and "graph_event" in before,
      f"{len(before)} tables and sequences")

# --------------------------------------------------------------------------- #
print("=" * 78)
print("C. flag ON: refusals by name, then an owner with step-up changes a setting")
print("=" * 78)
r = probe(MODE_ON, ON)
check("[on] the probe ran", bool(r))
if r:
    check("[on] routes under /api/config: GET and PUT /api/config/{name}",
          r["routes"] == [["GET", "/api/config"], ["PUT", "/api/config/{name}"]], str(r["routes"]))
    check("[on] the PUT is mapped to the admin scope", r["write_scope"] == ["PUT /api/config/{name}=admin"],
          str(r["write_scope"]))
    check("[on] the config_value table exists, empty", r["state_at_boot"]["table"] is True
          and r["state_at_boot"]["rows"] == [])
    body = r["get"][1] or {}
    es = entries(body)
    check("[on] GET says writes are enabled (step-up password, precedence env > board > default)",
          body.get("read_only") is False and (body.get("writes") or {}).get("enabled") is True
          and body["writes"].get("precedence") == ["env", "board", "default"]
          and body["writes"].get("step_up") == "password")
    check("[on] every entry carries changeable, true for exactly the changeable set",
          es and all("changeable" in e for e in es.values())
          and {n for n, e in es.items() if e["changeable"]} == set(C.CHANGEABLE))
    R = r["refusals"]
    expect = {
        "member": (403, "forbidden"),
        "no_password": (403, "step_up_required"),
        "wrong_password": (403, "password_incorrect"),
        "no_csrf": (403, "csrf_failed"),
        "owner_admin_token": (403, "session_required"),
        "owner_read_token": (403, "forbidden"),
        "member_admin_token": (403, "session_required"),
        "unknown": (404, "unknown_setting"),
        "secret ATLAS_EMBED_KEY": (403, "secret_not_changeable"),
        "secret ATLAS_OIDC_CLIENT_SECRET": (403, "secret_not_changeable"),
        "bootstrap ATLAS_DB_URL": (403, "bootstrap_read_only"),
        "bootstrap ATLAS_PASSWORD_PEPPER": (403, "bootstrap_read_only"),
        "bootstrap ATLAS_TOKEN_PEPPER": (403, "bootstrap_read_only"),
        "bootstrap ATLAS_LOGIN_ORIGINS": (403, "bootstrap_read_only"),
        "excluded ATLAS_RBAC": (403, "not_changeable"),
        "excluded ATLAS_DISABLED_FEATURES": (403, "not_changeable"),
        "excluded ATLAS_AUTH_PROVIDERS": (403, "not_changeable"),
        "bad strict": (400, "bad_value"),
        "bad top_k 0": (400, "bad_value"),
        "bad top_k abc": (400, "bad_value"),
    }
    check("[on] every refusal case ran", set(R) == set(expect), str(set(R) ^ set(expect)))
    for case, (status, error) in expect.items():
        got = R.get(case) or [None, None]
        ok = got[0] == status and isinstance(got[1], dict) and got[1].get("error") == error
        check(f"[on] {case}: {status} {error}", ok, f"got {got[0]} {got[2][:160] if len(got) > 2 else ''}")
    check("[on] an owner's read token is refused for the admin scope",
          "admin" in ((R.get("owner_read_token") or [0, {}])[1] or {}).get("detail", ""))
    check("[on] a bad value names the field `value`; a missing or wrong password names `password`",
          all((R[c][1] or {}).get("field") == "value" for c in ("bad strict", "bad top_k 0"))
          and all((R[c][1] or {}).get("field") == "password" for c in ("no_password", "wrong_password")))
    check("[on] the refusals changed nothing: no row, no event, strict still off",
          r["state_after_refusals"] == r["state_at_boot"] and r["strict_after_refusals"] is False,
          str(r["state_after_refusals"]))
    check("[on] the wrong password is an auth_event login_fail `config_write:`",
          r["reauth_fail_events"] == 1, str(r["reauth_fail_events"]))
    s1 = r["set_strict"]
    se = (s1[1] or {}).get("setting") or {}
    check("[on] criterion 3: an owner with the password sets ATLAS_STRICT_API: 200, changed, "
          "source board, value on, effect now",
          s1[0] == 200 and s1[1].get("changed") is True and se.get("source") == "board"
          and se.get("value") is True and s1[1].get("effect") == "now", s1[2][:300])
    check("[on] ...it takes effect at once: the reader sees it, and a workitem POST with an "
          "unknown field goes from accepted to 422",
          r["strict_live"] is True and r["workitem_before"] in (200, 201)
          and r["workitem_after"] == 422, f"{r['workitem_before']} -> {r['workitem_after']}")
    ev = r["last_event"]
    detail = json.loads(ev[0][4]) if ev else {}
    check("[on] criterion 3: the change is one node.config.set graph_event on atlas-system-api, "
          "by the owner's session",
          r["state_after_set"]["events"] == 1 and ev and ev[0][0] == "atlas-system-api"
          and ev[0][1] == "atlas-system-api" and ev[0][2] == "session" and ev[0][3] == "cw-owner",
          str(ev))
    check("[on] ...naming the setting and the sources (default -> board), effect now",
          detail.get("setting") == "ATLAS_STRICT_API" and detail.get("from_source") == "default"
          and detail.get("to_source") == "board" and detail.get("effect") == "now", str(detail))
    check("[on] the stored row is the canonical value", r["state_after_set"]["rows"]
          == [["ATLAS_STRICT_API", "1"]], str(r["state_after_set"]["rows"]))
    check("[on] the same value again: 200, changed false, no new event",
          r["same_again"][0] == 200 and r["same_again"][1].get("changed") is False
          and r["state_after_same"] == r["state_after_set"])
    ge = entries(r["get_after_set"][1])
    check("[on] the panel shows source board for it", ge["ATLAS_STRICT_API"]["source"] == "board")
    check("[on] ATLAS_RETRIEVAL_TOP_K set to 173: 200, board, the reader returns 173",
          r["set_top_k"][0] == 200 and r["top_k_live"] == 173 and r["env_top_k_after_set"] == "173",
          r["set_top_k"][2][:200])
    rt = r["reset_top_k"]
    check("[on] ...reset with null: 200, source default, the reader back to 30, the row gone",
          rt[0] == 200 and ((rt[1] or {}).get("setting") or {}).get("source") == "default"
          and r["env_top_k_after_reset"] is None and r["top_k_after_reset"] == 30
          and r["state_final"]["rows"] == [["ATLAS_STRICT_API", "1"]], rt[2][:200])
    check("[on] three changes, three events", r["state_final"]["events"] == 3,
          str(r["state_final"]["events"]))
    # Owner decision, 2026-10-26: an event names the setting and the sources, NEVER a value, old or new.
    evs = r["all_events"]
    details = [json.loads(e[-1]) for e in evs]
    check("[on] every node.config.set detail is exactly {setting, from_source, to_source, "
          "effect}: no from/to keys", len(details) == 3 and all(
              set(d) == {"setting", "from_source", "to_source", "effect"} for d in details),
          str(details))
    check("[on] ...the three events carry the right sources (default->board, default->board, "
          "board->default)", [(d["setting"], d["from_source"], d["to_source"]) for d in details]
          == [("ATLAS_STRICT_API", "default", "board"), ("ATLAS_RETRIEVAL_TOP_K", "default", "board"),
              ("ATLAS_RETRIEVAL_TOP_K", "board", "default")], str(details))
    # The values that passed through: strict off/on (false, true, "0", "1", on/off) and top-k
    # 30 (the default) and 173 (set). Searched as whole tokens over every column of every row.
    found = [(e[2], m.group(0)) for e in evs for col in e if col is not None
             for m in VALUE_TOKEN.finditer(str(col))]
    check("[on] neither the old nor the new value appears anywhere in any event row",
          bool(evs) and not found, str(found))
    check("[on] ...and that search finds a value when one is there (perturbation)",
          bool(VALUE_TOKEN.search(json.dumps(dict(details[0], to="173")) if details else "")))
    blob = json.dumps(r)
    check("[on] no secret's value appears in any response or event", not leaks(blob),
          str(leaks(blob)))

# --------------------------------------------------------------------------- #
print("=" * 78)
print("D. boot: a board value is applied and boot stays a read; the environment wins; flag off")
print("=" * 78)
run_child(["-c", "import app; app.startup()"], ON)  # settle
before = digest()
r = probe("\nprint('PROBE ' + json.dumps(out))\n", ON)
after = digest()
check("[boot] the probe ran", bool(r))
if r:
    es = entries(r["get"][1])
    check("[boot] the stored value is applied at boot: the reader sees it, source board",
          r["strict_at_boot"] is True and r["env_strict_at_boot"] == "1"
          and es["ATLAS_STRICT_API"]["source"] == "board" and es["ATLAS_STRICT_API"]["value"] is True)
ch = changed(before, after)
check("[boot] boot with a board value is a read: every graph, data and config table byte-identical",
      not ch and "config_value" in before, ", ".join(ch) or f"{len(before)} compared")

r = probe(MODE_BOOT, {**ON, "ATLAS_STRICT_API": "0"})
check("[env wins] the probe ran", bool(r))
if r:
    es = entries(r["get"][1])
    check("[env wins] ATLAS_STRICT_API=0 in the environment wins over the board's 1",
          r["strict_at_boot"] is False and es["ATLAS_STRICT_API"]["source"] == "env"
          and es["ATLAS_STRICT_API"]["value"] is False)
    p = r["put_strict_off"]
    check("[env wins] a board change to it is 409 environment_wins, naming the variable",
          p[0] == 409 and (p[1] or {}).get("error") == "environment_wins"
          and "ATLAS_STRICT_API" in (p[1] or {}).get("detail", ""), p[2][:200])
    check("[env wins] ...and changes nothing", r["state_after"] == r["state_at_boot"])

before = digest()
r = probe(MODE_OFF, {})
after = digest()
check("[off again] the probe ran", bool(r))
if r:
    es = entries(r["get"][1])
    check("[off again] the stored row is not applied: strict off, source default, phase 1's body",
          r["strict_at_boot"] is False and es["ATLAS_STRICT_API"]["source"] == "default"
          and r["get"][1].get("read_only") is True and r["state_at_boot"]["rows"] == [
              ["ATLAS_STRICT_API", "1"]])
    check("[off again] criterion 4 with the flag off: a bootstrap PUT is the unknown route's 404",
          all(o["real"][0] == 404 and o["real"][2] == o["never"][2] for o in r["off"].values()))
ch = changed(before, after)
check("[off again] nothing changed", not ch, ", ".join(ch))

# --------------------------------------------------------------------------- #
print("=" * 78)
print("E. the access suite and the write-scope section with the flag ON")
print("=" * 78)
for script in ("verify_node_access.py", "verify_object_graph.py"):
    env = {k: v for k, v in os.environ.items() if k != "ATLAS_DB_URL"}
    env.update(ON)
    env["ATLAS_TEST_DB_URL"] = DB.url
    proc = subprocess.run([sys.executable, str(HERE / script)], env=env, capture_output=True,
                          text=True, timeout=900, cwd=HERE)
    fails = [ln for ln in proc.stdout.splitlines() if ln.startswith("[FAIL]")]
    check(f"E: {script} passes with {FLAG}=1", proc.returncode == 0 and not fails,
          "\n".join(fails[:10]) or proc.stderr[-1500:])
    if script == "verify_object_graph.py":
        check("E: ...and §20 refused a read-only Bearer on the config write route",
              "PUT /api/config/{name} refuses a read-only Bearer" in proc.stdout)

if failures:
    print(f"\n{len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nall checks passed")
