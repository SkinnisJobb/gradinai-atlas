"""
Verification for STORY-atlas-config-panel, PHASE 1: the read-only configuration panel.

`settings_catalog.py` describes every setting (part node, kind, how its source is decided);
`GET /api/config` (routers/config.py) serves it to an owner; the board's node panel shows it on
the `atlas-system*` part nodes. Sections:

  A. The catalog is complete and well-formed, with no database. Every `ATLAS_*` name written in
     ANY module of the API (backend/, test harness excluded: settings.py, retrieval/, auth.py,
     passwords.py, ...) is described, and the check FAILS when a new one is added without a
     description (a perturbed settings.py, and a new env read in another module). Every secret
     read through `settings.read_secret` anywhere in the backend, and the retrieval add-on's key,
     is described as a secret. Every entry names a real part node (system_handbook.NODES). The
     retrieval add-on's values are `retrieval.load_config`'s own, its verdict is shown, and a
     switch it refuses shows that and no value.
  B. Through the real gate, one child process per configuration (settings are read at import):
     with every secret given as a FILE holding a known value (strict mode on), and with the
     secrets as plain env values. Each setting's effective value and source; each secret only
     `set from file` / `set from env` / `not set`, its entry carrying no other field; and no
     response body (config, health, me) contains any secret's value, and no secret entry carries
     its length. Who may read it: owner session and an owner's admin-scoped token 200; anonymous
     401; member session, a member's admin token and an owner's read token 403.
  C. Read-only (criterion 2 in phase-1 form, "no write route exists"): the live route table has
     exactly one route under /api/config and it is GET; WRITE_SCOPE_ROUTES names nothing there;
     POST/PUT/PATCH/DELETE on it by a signed-in owner with a valid CSRF token are refused.
  D. A homelab-shaped database (the default profile, the curated fixture and an install's own
     `atlas`, `atlas-api`, ... nodes, no `atlas-system*` set under the homelab copy; the neutral
     copy's default boot seeds that set, and it is then part of the database) is byte-identical, every graph and
     data table, after an owner reads /api/config through the gate; the route answers there
     with every part.

Everything here runs with `ATLAS_CONFIG_WRITES` unset (it is stripped with every other catalog
name), so it also pins that phase 2's flag off leaves phase 1 exactly as it was. Criteria 3 and 4
of the story (the flag-gated change path, bootstrap refusal in both modes) are
`verify_config_writes.py`.

Run:
    python verify_config_panel.py
"""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
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
import system_handbook as H  # noqa: E402
import baseload_data  # noqa: E402
import distribution  # noqa: E402

#: How many `atlas-system*` nodes the default profile's fresh boot writes (STORY-oss-23 A3b).
#: The homelab's copy defaults to `homelab`, which never seeds the system node set: zero, as
#: section D has always pinned. Any other default seeds the whole set (handbook + baseload,
#: `system_node.ALL_IDS`) on the fresh graph, and those rows are part of what must not change.
DEFAULT_SYSTEM_NODES = (0 if distribution.DEFAULT_SEED_PROFILE == "homelab"
                        else len(H.NODES) + len(baseload_data.NODES))

ENGINE = create_engine(DB.url)
ORIGIN = "https://testserver"
PASSWORD = "Correct-Horse-Battery-9!"

#: Variables this script sets per configuration; stripped from whatever the runner passed.
OWN = {s.name for s in C.CATALOG} | {s.name + "_FILE" for s in C.CATALOG if s.secret} | {
    "ATLAS_RESEED"}
#: Kept from the runner: CI's second pass disables every feature, and that must still pass.
KEEP = {"ATLAS_DISABLED_FEATURES"}
BASE_ENV = {k: v for k, v in os.environ.items() if k not in OWN or k in KEEP}
BASE_ENV.update({"ATLAS_BREACH_CHECK": "bundled", "ATLAS_LOGIN_ORIGINS": ORIGIN,
                 "ATLAS_FRONTEND_DIR": str(HERE.parent / "frontend")})

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
        print("  stderr: " + proc.stderr[-1500:].replace("\n", "\n  "))
    return proc


def reset() -> None:
    with ENGINE.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


# Boots, makes an owner, a member and their agents, signs both in, and reads/writes as each.
# Output: one line "PROBE {json}". Every value it prints is a test value made here.
PROBE = r'''
import json, re, sys
from datetime import datetime, timezone
cfg = json.loads(sys.stdin.read())
import app, auth, passwords
from models_auth import AuthUser
from route_table import live_routes
app.startup()
from fastapi.testclient import TestClient
ORIGIN = "https://testserver"
PW = cfg["password"]

def user(uid, subject, role="member", is_service=False, person=None, password=False):
    with app.session() as s:
        s.add(AuthUser(id=uid, subject=subject, name=subject, role=role, is_service=is_service,
                       must_change_password=False, person_user_id=person,
                       password_hash=passwords.hash_password(PW) if password else None,
                       created_at=datetime.now(timezone.utc)))
        s.commit()

def mint(uid, scopes):
    with app.session() as s:
        raw, _row = auth.mint_service_token(s, owner=s.get(AuthUser, uid), scopes=scopes, label="cp")
        s.commit()
        return raw

def sign_in(subject):
    c = TestClient(app.app, base_url=ORIGIN, follow_redirects=False)
    page = c.get("/auth/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    c.post("/auth/login", data={"username": subject, "password": PW, "csrf_token": csrf},
           headers={"Origin": ORIGIN})
    return c, c.get("/api/me").json()["csrf_token"]

user("u-cp-owner", "cp-owner", role="owner", password=True)
user("u-cp-member", "cp-member", password=True)
user("u-cp-owner-bot", "bot-cp-owner", is_service=True, person="u-cp-owner")
user("u-cp-member-bot", "bot-cp-member", is_service=True, person="u-cp-member")
tokens = {"owner_admin": mint("u-cp-owner-bot", ["read", "admin"]),
          "owner_read": mint("u-cp-owner-bot", ["read"]),
          "member_admin": mint("u-cp-member-bot", ["read", "admin"])}
owner, owner_csrf = sign_in("cp-owner")
member, _ = sign_in("cp-member")
anon = TestClient(app.app, base_url=ORIGIN, follow_redirects=False)
J = {"Accept": "application/json"}

def res(r):
    return [r.status_code, r.text]

out = {"get": {}, "write": {}, "bodies": {}}
out["get"]["owner"] = res(owner.get("/api/config", headers=J))
out["get"]["member"] = res(member.get("/api/config", headers=J))
out["get"]["anonymous"] = res(anon.get("/api/config", headers=J))
for k, t in tokens.items():
    out["get"][k] = res(anon.get("/api/config", headers={**J, "Authorization": "Bearer " + t}))
for m in ("POST", "PUT", "PATCH", "DELETE"):
    for p in ("/api/config", "/api/config/ATLAS_RBAC"):
        r = owner.request(m, p, json={"csrf_token": owner_csrf, "value": "projects"},
                          headers={**J, "Origin": ORIGIN})
        out["write"][m + " " + p] = r.status_code
for p in ("/api/health", "/api/me"):
    out["bodies"][p] = res(owner.get(p, headers=J))
out["routes"] = sorted([m, r.path] for r in live_routes(app.app)
                       if getattr(r, "path", "").startswith("/api/config")
                       for m in (getattr(r, "methods", None) or ["*"]))
out["write_scope"] = sorted(" ".join(k) for k in auth.WRITE_SCOPE_ROUTES if k[1].startswith("/api/config"))
print("PROBE " + json.dumps(out))
'''


def probe(env: dict) -> dict:
    proc = run_child(["-c", PROBE], env, stdin=json.dumps({"password": PASSWORD}))
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE ")), None)
    return json.loads(line[len("PROBE "):]) if line else {}


def entries(body: dict) -> dict:
    return {e["name"]: dict(e, part=p["id"]) for p in body.get("parts", []) for e in p["settings"]}


# --------------------------------------------------------------------------- #
print("=" * 78)
print("A. the catalog: every setting described, each with a real part node")
print("=" * 78)
missing = C.undescribed()
api_names = C.names_in_api()
check("criterion 1: every ATLAS_* name written in a module of the API has a description",
      not missing, ", ".join(missing) or f"{len(api_names)} names in {len(C.api_modules())} modules")
check("the scan reaches beyond settings.py: the retrieval add-on, the password module, the gate",
      "retrieval/__init__.py" in api_names.get("ATLAS_EMBED_URL", [])
      and "retrieval/query.py" in api_names.get("ATLAS_RETRIEVAL_TOP_K", [])
      and "passwords.py" in api_names.get("ATLAS_BREACH_CHECK", [])
      and "auth.py" in api_names.get("ATLAS_MACHINE_GRACE_HOSTS", []))
check("the test harness is not scanned (it sets variables, it does not read them)",
      not any(m.startswith(("verify_", "testdb", "testseed")) for ms in api_names.values() for m in ms))
other = C.undescribed('import os\n\ndef f():\n    return os.environ.get("ATLAS_SOMEWHERE_NEW", "")\n')
check("...and it fails when a module outside settings.py reads a new variable (perturbed source)",
      other == ["ATLAS_SOMEWHERE_NEW"], str(other))
src = Path(settings.__file__).read_text(encoding="utf-8")
perturbed = C.undescribed(src + '\nNEW_THING_ENV = "ATLAS_NEW_THING"\n')
check("...and the check fails when a setting is added without one (perturbed source)",
      perturbed == ["ATLAS_NEW_THING"], str(perturbed))
check("a secret's NAME_FILE form counts as the secret, a plain *_FILE setting does not",
      C.is_described("ATLAS_TOKEN_PEPPER_FILE") and C.is_described(settings.OIDC_GROUP_MAP_FILE_ENV)
      and not C.is_described("ATLAS_PRODUCT_NAME_FILE"))


def read_secret_names() -> dict[str, set]:
    """module -> the secret names its `read_secret(...)` calls read, resolved statically."""
    out: dict[str, set] = {}
    for path in sorted(HERE.rglob("*.py")):
        rel = path.relative_to(HERE).as_posix()
        if rel.startswith(("verify_", ".venv", "test")) or "/." in rel or rel == "settings.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Name)
                  and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None))
                    == "read_secret" and node.args):
                continue
            a = node.args[0]
            if isinstance(a, ast.Constant):
                name = a.value
            elif isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name) and a.value.id == "settings":
                name = getattr(settings, a.attr)
            elif isinstance(a, ast.Name) and a.id in consts:
                name = consts[a.id]
            else:
                # github_link derives the name from its own *_FILE constants: take every one.
                for v in consts.values():
                    if v.startswith("ATLAS_") and v.endswith("_FILE"):
                        out.setdefault(rel, set()).add(v[: -len("_FILE")])
                continue
            out.setdefault(rel, set()).add(name)
    return out


found = read_secret_names()
all_secret_reads = set().union(*found.values()) if found else set()
not_secret = sorted(n for n in all_secret_reads if not (n in C.BY_NAME and C.BY_NAME[n].secret))
check("every name read through settings.read_secret in the backend is described as a secret",
      bool(all_secret_reads) and not not_secret,
      f"undescribed or not secret: {not_secret}" if not_secret else
      f"{len(all_secret_reads)} names from {len(found)} modules")
check("the peppers, the database URL and the GitHub secrets are among them",
      {"ATLAS_PASSWORD_PEPPER", "ATLAS_TOKEN_PEPPER", "ATLAS_DB_URL",
       "ATLAS_GITHUB_INVITE_TOKEN"} <= all_secret_reads, str(sorted(all_secret_reads)))
check("the retrieval add-on's key is a secret, read from a file only",
      C.BY_NAME["ATLAS_EMBED_KEY"].secret and not C.BY_NAME["ATLAS_EMBED_KEY"].allow_env
      and C.BY_NAME["ATLAS_EMBED_KEY"].part == "atlas-system-retrieval")
check("...and its plain form is never counted as set (the add-on refuses it)",
      C.secret_state({"ATLAS_EMBED_KEY": "plain-key"}, C.BY_NAME["ATLAS_EMBED_KEY"])
      == (C.SECRET_NOT_SET, None))
check("the parts are exactly system_handbook's nodes, in its order",
      list(C.PARTS) == [n["id"] for n in H.NODES], str(C.PARTS))
names = [s.name for s in C.CATALOG]
check("no setting is described twice", len(names) == len(set(names)))
check("every entry has a known kind, a part and a summary",
      all(s.kind in C.KINDS and s.part in C.PARTS and s.summary.strip() for s in C.CATALOG))
check("every plain entry has a loader and a value; no secret has either",
      all((s.loader and s.value) if not s.secret else (s.loader is None and s.value is None)
          for s in C.CATALOG))
check("the bootstrap settings are the database URL, the two peppers and the login origins",
      sorted(s.name for s in C.CATALOG if s.kind == "bootstrap")
      == sorted([settings.DB_URL_ENV, "ATLAS_PASSWORD_PEPPER", "ATLAS_TOKEN_PEPPER",
                 settings.LOGIN_ORIGINS_ENV]))
print("  settings described: %d (%s)" % (len(names), ", ".join(
    "%s %d" % (p, sum(1 for s in C.CATALOG if s.part == p)) for p in C.PARTS)))

# describe() alone, on a mapping, with every secret set in env: no value key on any secret.
fake = {s.name: "known-value-%02d-xyz" % i for i, s in enumerate(C.CATALOG) if s.secret}
d = C.describe({**fake, settings.LOGIN_ORIGINS_ENV: ORIGIN})
secret_entries = [e for e in entries(d).values() if e["secret"]]
check("describe(): a secret entry is {name, kind, secret, summary, state, source} and nothing else",
      secret_entries and all(set(e) - {"part"} == {"name", "kind", "secret", "summary", "state", "source"}
                             for e in secret_entries))
blob = json.dumps(d)
check("describe(): no secret value appears in the output", not any(v in blob for v in fake.values()))

# The retrieval add-on through its own parser, retrieval.load_config.
import retrieval  # noqa: E402

# Off first: the checks after it switch the add-on on, which imports its other modules.
part = next(p for p in C.describe({settings.LOGIN_ORIGINS_ENV: ORIGIN})["parts"]
            if p["id"] == "atlas-system-retrieval")
es = {e["name"]: e for e in part["settings"]}
check("retrieval off: the status says off, and its other modules are not imported to show a value",
      part.get("status") == "off" and es["ATLAS_RETRIEVAL_TOP_K"].get("inactive")
      and "retrieval.query" not in sys.modules and "retrieval.entra" not in sys.modules)
retr = {settings.LOGIN_ORIGINS_ENV: ORIGIN, "ATLAS_RETRIEVAL": "on",
        "ATLAS_EMBED_URL": "http://embedder.example.test/v1", "ATLAS_EMBED_MODEL": "m-test",
        "ATLAS_EMBED_DIM": "768", "ATLAS_EMBED_BATCH": "seven", "ATLAS_EMBED_SCOPE": "all"}
part = next(p for p in C.describe(retr)["parts"] if p["id"] == "atlas-system-retrieval")
es = {e["name"]: e for e in part["settings"]}
cfg = retrieval.load_config(retr)
check("retrieval: every value is retrieval.load_config's own (url, model, dim, scope, batch, interval)",
      es["ATLAS_EMBED_URL"]["value"] == cfg.url and es["ATLAS_EMBED_MODEL"]["value"] == cfg.model
      and es["ATLAS_EMBED_DIM"]["value"] == cfg.dim and es["ATLAS_EMBED_SCOPE"]["value"] == cfg.scope
      and es["ATLAS_EMBED_BATCH"]["value"] == cfg.batch == 32
      and es["ATLAS_RETRIEVAL_INTERVAL_S"]["value"] == cfg.interval_s, str(part)[:300])
check("retrieval: its own verdict is shown (unavailable, naming its first problem)",
      part.get("status") == "unavailable: " + cfg.problem and "http://" in cfg.problem,
      str(part.get("status")))
check("retrieval: the switch on, from env; the key not set", es["ATLAS_RETRIEVAL"]["value"] is True
      and es["ATLAS_RETRIEVAL"]["source"] == "env" and es["ATLAS_EMBED_KEY"]["state"] == C.SECRET_NOT_SET)
part = next(p for p in C.describe({**retr, "ATLAS_RETRIEVAL": "maybe"})["parts"]
            if p["id"] == "atlas-system-retrieval")
es = {e["name"]: e for e in part["settings"]}
check("retrieval: a switch the add-on refuses shows that, and no value",
      es["ATLAS_RETRIEVAL"]["value"] is None and "SettingsError" in es["ATLAS_RETRIEVAL"].get("error", "")
      and "does not parse" in part.get("status", ""), str(es["ATLAS_RETRIEVAL"]))

# --------------------------------------------------------------------------- #
print("=" * 78)
print("B. through the real gate: values, sources, secret states, no leak, who may read")
print("=" * 78)
tmp = Path(tempfile.mkdtemp(prefix="atlas-config-panel-"))
pepper_p = base64.b64encode(os.urandom(32)).decode()
pepper_t = base64.b64encode(os.urandom(32)).decode()
known = {
    "ATLAS_PASSWORD_PEPPER": pepper_p, "ATLAS_TOKEN_PEPPER": pepper_t, "ATLAS_DB_URL": DB.url,
    "ATLAS_OIDC_CLIENT_SECRET": "oidc-client-secret-" + os.urandom(9).hex(),
    "ATLAS_OIDC_CLIENT_KEY": "-----BEGIN PRIVATE KEY-----" + os.urandom(20).hex(),
    "ATLAS_GITHUB_OAUTH_CLIENT_ID": "gh-client-id-" + os.urandom(7).hex(),
    "ATLAS_GITHUB_OAUTH_CLIENT_SECRET": "gh-client-secret-" + os.urandom(11).hex(),
    "ATLAS_GITHUB_INVITE_TOKEN": "gh-invite-token-" + os.urandom(13).hex(),
    "ATLAS_EMBED_KEY": "embed-key-" + os.urandom(15).hex(),
}
check("every secret in the catalog has a known test value",
      set(known) == {s.name for s in C.CATALOG if s.secret}, str(sorted(set(known) ^ {
          s.name for s in C.CATALOG if s.secret})))
files_env = {"ATLAS_REQUIRE_SECRET_FILES": "1", "ATLAS_PUBLIC_URL": ORIGIN,
             "ATLAS_PRODUCT_NAME": "Configured Name", "ATLAS_SEED": "none", "ATLAS_LEGACY_V0": "0"}
for name, value in known.items():
    f = tmp / name.lower()
    f.write_text(value + "\n", encoding="utf-8")
    files_env[name + "_FILE"] = str(f)
plain_env = {"ATLAS_PASSWORD_PEPPER": pepper_p, "ATLAS_TOKEN_PEPPER": pepper_t,
             "ATLAS_DB_URL": DB.url, "ATLAS_RBAC": "off"}


def leaks(text_: str) -> list[str]:
    hits = [n for n, v in known.items() if v in text_]
    if "postgres:%s@" % testdb.POSTGRES_PASSWORD in text_:
        hits.append("the database password")
    return hits


def secret_fields_ok(body: dict) -> list[str]:
    bad = []
    for e in entries(body).values():
        if not e["secret"]:
            continue
        if set(e) - {"part"} != {"name", "kind", "secret", "summary", "state", "source"}:
            bad.append(e["name"] + ": fields " + ",".join(sorted(e)))
        if e["state"] not in (C.SECRET_FROM_FILE, C.SECRET_FROM_ENV, C.SECRET_NOT_SET):
            bad.append(e["name"] + ": state " + str(e["state"]))
        n = len(known[e["name"]])
        if any(v in (n, str(n), n + 1, str(n + 1)) for k, v in e.items() if k not in ("secret",)):
            bad.append(e["name"] + ": a field equals the value's length")
    return bad


for label, env, want_state, want_source in (
        ("every secret from a FILE, strict mode on", files_env, C.SECRET_FROM_FILE, "file"),
        ("the required secrets as plain env values", plain_env, None, "env")):
    reset()
    r = probe(env)
    check(f"[{label}] the probe ran", bool(r))
    if not r:
        continue
    status, raw = r["get"]["owner"]
    check(f"[{label}] owner session: 200", status == 200, str(status))
    body = json.loads(raw) if status == 200 else {}
    es = entries(body)
    check(f"[{label}] criterion 1: every described setting is served, once, on its part node",
          sorted(es) == sorted(names) and all(es[s.name]["part"] == s.part for s in C.CATALOG)
          and sum(len(p["settings"]) for p in body.get("parts", [])) == len(names)
          and [p["id"] for p in body.get("parts", [])] == list(C.PARTS))
    check(f"[{label}] it says it is read-only", body.get("read_only") is True)
    for who, want in (("owner_admin", 200), ("anonymous", 401), ("member", 403),
                      ("member_admin", 403), ("owner_read", 403)):
        check(f"[{label}] {who.replace('_', ' ')}: {want}", r["get"][who][0] == want,
              str(r["get"][who][0]))
    check(f"[{label}] an owner's admin token reads the same body as the owner's session",
          r["get"]["owner_admin"][1] == raw)
    every_body = raw + "".join(b for _s, b in r["bodies"].values()) + "".join(
        b for _s, b in r["get"].values())
    check(f"[{label}] criterion 1: no response body (config, health, me, refusals) contains a "
          "secret's value", not leaks(every_body), str(leaks(every_body)))
    bad = secret_fields_ok(body)
    check(f"[{label}] criterion 1: each secret entry carries only its state, never a length",
          not bad, "; ".join(bad))
    if want_state == C.SECRET_FROM_FILE:
        check(f"[{label}] every secret reads `set from file`, source file",
              all(es[n]["state"] == C.SECRET_FROM_FILE and es[n]["source"] == "file" for n in known),
              str({n: es[n]["state"] for n in known}))
        check(f"[{label}] strict mode is shown on, from env",
              es["ATLAS_REQUIRE_SECRET_FILES"]["value"] is True
              and es["ATLAS_REQUIRE_SECRET_FILES"]["source"] == "env")
        check(f"[{label}] a plain setting set in env: its value, source env",
              es["ATLAS_PRODUCT_NAME"]["value"] == "Configured Name"
              and es["ATLAS_PRODUCT_NAME"]["source"] == "env")
        check(f"[{label}] the bearer hosts are derived from ATLAS_PUBLIC_URL, and say so",
              es["ATLAS_BEARER_HOSTS"]["source"] == "derived"
              and es["ATLAS_BEARER_HOSTS"]["derived_from"] == ["ATLAS_PUBLIC_URL"]
              and "testserver" in es["ATLAS_BEARER_HOSTS"]["value"], str(es["ATLAS_BEARER_HOSTS"]))
        check(f"[{label}] the seed profile is `none`, from env",
              es["ATLAS_SEED"]["value"] == "none" and es["ATLAS_SEED"]["source"] == "env")
    else:
        required = ("ATLAS_PASSWORD_PEPPER", "ATLAS_TOKEN_PEPPER", "ATLAS_DB_URL")
        check(f"[{label}] the peppers and the database URL read `set from env`",
              all(es[n]["state"] == C.SECRET_FROM_ENV and es[n]["source"] == "env" for n in required))
        check(f"[{label}] the optional secrets, given nowhere, read `not set` with no source",
              all(es[n]["state"] == C.SECRET_NOT_SET and es[n]["source"] is None
                  for n in known if n not in required))
        check(f"[{label}] a value set to its own default still reads source env",
              es["ATLAS_RBAC"]["value"] == "off" and es["ATLAS_RBAC"]["source"] == "env")
        check(f"[{label}] an unset setting reads its default, source default",
              es["ATLAS_PRIVACY_CONTACT"]["source"] == "default"
              and es["ATLAS_PRIVACY_CONTACT"]["value"] == settings.DEFAULT_PRIVACY_CONTACT
              and es["ATLAS_SEED"]["value"] == settings.DEFAULT_SEED_PROFILE)
        check(f"[{label}] the three empty parts carry a note instead of nothing",
              all(p.get("note") for p in body["parts"] if not p["settings"])
              and sum(1 for p in body["parts"] if not p["settings"]) == 3)
        check(f"[{label}] the retrieval part shows its settings and says it is off",
              len(next(p for p in body["parts"] if p["id"] == "atlas-system-retrieval")["settings"]) > 0
              and next(p for p in body["parts"] if p["id"] == "atlas-system-retrieval").get("status") == "off")

    # ------------------------------------------------------------------- #
    if want_state == C.SECRET_FROM_FILE:
        print("=" * 78)
        print("C. read-only: no write route of any kind")
        print("=" * 78)
        check("criterion 2 (phase 1): the live route table has exactly one route under "
              "/api/config, and it is GET", r["routes"] == [["GET", "/api/config"]], str(r["routes"]))
        check("WRITE_SCOPE_ROUTES names nothing under /api/config", r["write_scope"] == [],
              str(r["write_scope"]))
        refused = {k: v for k, v in r["write"].items() if 200 <= v < 300}
        check("POST/PUT/PATCH/DELETE on /api/config by a signed-in owner with CSRF are all refused",
              not refused and len(r["write"]) == 8, str(r["write"]))

# --------------------------------------------------------------------------- #
print("=" * 78)
print("D. a homelab-shaped database is untouched by reading the configuration")
print("=" * 78)
reset()
p = run_child(["-c", "import app; app.startup()"], dict(plain_env))
seed = "\n".join([
    "import app, testseed",
    "from db import session",
    "from models_graph import Node, Edge",
    "from attention import recompute_derived",
    "with session() as s:",
    "    testseed.build(s)",
    "    for nid, t in [('atlas', 'project'), ('atlas-api', 'service'), ('atlas-mcp', 'service'),",
    "                   ('atlas-postgres', 'db'), ('atlas-retrieval', 'capability')]:",
    "        s.add(Node(id=nid, type=t, name=nid, attributes={}, body='own', source=None, origin='atlas'))",
    "    s.flush()",
    "    for nid in ('atlas-api', 'atlas-mcp', 'atlas-postgres', 'atlas-retrieval'):",
    "        s.add(Edge(src='atlas', dst=nid, edge_type='contains', origin='atlas'))",
    "    s.flush()",
    "    recompute_derived(s)",
    "    s.commit()",
])
check("homelab-shaped database built (default profile, fixture, an install's own atlas tree)",
      p.returncode == 0 and run_child(["-c", seed], dict(plain_env)).returncode == 0)
run_child(["-c", "import app; app.startup()"], dict(plain_env))  # settle sequences


def digest() -> dict:
    """(row count, md5 of every row) per table, except the account tables a sign-in writes."""
    out = {}
    with ENGINE.connect() as conn:
        # Reflected tables and SQLAlchemy's own SELECT: no SQL is assembled from a table name.
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


with ENGINE.connect() as conn:
    nodes_before = conn.execute(text("SELECT count(*) FROM node")).scalar()
    sys_nodes = conn.execute(text("SELECT count(*) FROM node WHERE id LIKE 'atlas-system%'")).scalar()
check(f"it holds nodes and {DEFAULT_SYSTEM_NODES or 'no'} atlas-system* node(s) "
      f"(default profile {distribution.DEFAULT_SEED_PROFILE!r})",
      nodes_before > 0 and sys_nodes == DEFAULT_SYSTEM_NODES,
      f"{nodes_before} nodes, {sys_nodes} system nodes")
before = digest()
r = probe(dict(plain_env))
after = digest()
changed = sorted(t for t in set(before) | set(after) if before.get(t) != after.get(t))
check("the owner read /api/config there (200, every part)",
      bool(r) and r["get"]["owner"][0] == 200
      and [p["id"] for p in json.loads(r["get"]["owner"][1])["parts"]] == list(C.PARTS))
check("every graph and data table is byte-identical afterwards (auth_* excluded: signing in "
      "writes sessions)", not changed, ", ".join(changed))
print(f"  compared {len(before)} tables and sequences")

if failures:
    print(f"\n{len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nall checks passed")
