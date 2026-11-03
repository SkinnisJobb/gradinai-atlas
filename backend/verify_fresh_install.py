"""
Verification for STORY-oss-07: seed profiles (`ATLAS_SEED`), the v0 switch (`ATLAS_LEGACY_V0`)
and the first-owner bootstrap (`atlas-admin user add --role owner`).

The criteria the owner approved on 2026-10-24, and the section that proves each:

  1. With no new configuration, a fresh empty DB is seeded exactly as today
     (homelab seed and v0 tables).                                                     -> A
  2. ATLAS_SEED=none gives zero nodes; with ATLAS_LEGACY_V0=0 no v0 tables are created
     and /api/tree, /api/summary, /api/priority, /api/goals return 404.               -> B
  3. ATLAS_SEED=demo gives exactly one demo project, one epic and two stories.         -> C
  (Since STORY-atlas-system-node-handbook, "zero nodes" and "nothing else" read: nothing but
  the install's own system node set, `atlas-system` and its children, which `none` and `demo`
  both seed into a fresh graph. `verify_system_node.py` proves the set itself; here it is
  counted out, so these criteria still say what they said. The homelab default seeds no node.)
  4. Startup on an existing populated DB leaves its data unchanged.                     -> D
  5. `atlas-admin user add --role owner` creates a working owner account.               -> E
  (F: a malformed or contradictory setting refuses to boot, naming the variable.)
  (G, STORY-oss-23 A1: with the NEUTRAL `distribution.py` the public export ships, from
  `tools/carve/overlay/backend/`, in front of this repo's own, a fresh install boots with
  `none` as the default seed and the v0 tables off; and, A4b, the privacy page's controller
  line has no company note, and the v0 owner default and the password context words are neutral.)

THE DISTRIBUTION SEAM (A1). The defaults these criteria talk about (the seed profile, the v0
switch) are read from `distribution.py`. In project-atlas that is the homelab copy, so every
section runs exactly as before. The public export overlays the neutral copy and does not ship
`seed.py`, so there:
  * §A SKIPS, printing why: it is "no configuration == today's homelab seed", and the export
    has neither that seed nor that default;
  * the few other checks that lean on a default ask `distribution` what it is, or spell the
    value out where "unset" was only a way of saying "1"; in this repo each one still runs the
    exact check it ran before.

Every boot is a separate process (`import app; app.startup()`), because `db.py` applies
ATLAS_LEGACY_V0 to the v0 metadata at import: one process is one configuration, as in a
container. The schema and the data are read from the Postgres catalogue and as a digest of
every row of every table, the same way `verify_upgrade_command.py` reads them.

Run:
    python verify_fresh_install.py
"""
from __future__ import annotations

import base64
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

from sqlalchemy import create_engine, text  # noqa: E402

ENGINE = create_engine(DB.url)

import importlib.util  # noqa: E402

import distribution  # noqa: E402  (pure data, imports nothing)

#: The homelab seed ships in project-atlas only; the public export leaves `seed.py` out.
#: `find_spec` locates the module without running it (seed.py imports app).
SEED_IMPORTABLE = importlib.util.find_spec("seed") is not None
#: The distribution's defaults: (homelab, True) in project-atlas, (none, False) neutral.
DEFAULT_SEED = distribution.DEFAULT_SEED_PROFILE
DEFAULT_V0 = distribution.DEFAULT_LEGACY_V0

#: sha256 of each lowercase word that names the homelab estate (its owner, domain and
#: host), the same set as verify_settings.py's. §G tokenises the neutral copy's values
#: and fails on any word whose hash is here: no homelab literal is spelled out in this
#: file, which ships in the export.
_HOMELAB_WORD_SHA256 = frozenset({
    "d9a31550033ee07d6e14302eea8202c07c266b633154513d817ca8bb91de40d1",
    "7a84143d54b59fe2186d394f66fa59b5b81e12c8edf9cbe71cece88d9388ff45",
    "97dc14c9c2dcff7c603f770ee90c83b01c745000b100c8645509c2acbcf4c387",
    "a17a65634b8748a893f0f605efc5bb1ca798817b03aae8030903a60f9fd58223",
})


def homelab_words(text_: str) -> set[str]:
    """The words of `text_` (lowercased; a hyphenated run and each of its parts) whose
    sha256 is in `_HOMELAB_WORD_SHA256`. Empty means the text names none of them."""
    import hashlib
    words: set[str] = set()
    for run in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text_.lower()):
        words.add(run)
        words.update(run.split("-"))
    return {w for w in words if hashlib.sha256(w.encode("utf-8")).hexdigest() in _HOMELAB_WORD_SHA256}

V0_TABLES = {"component", "gate", "requirement", "decision"}
LEGACY_ROUTES = ["/api/tree", "/api/summary", "/api/priority", "/api/goals"]
FLAGS = ("ATLAS_SEED", "ATLAS_LEGACY_V0", "ATLAS_RESEED")

# The environment every child boots with: the throwaway peppers every verify script mints,
# as plain env vars, and NONE of this story's variables, so "no new configuration" is real.
BASE_ENV = {k: v for k, v in os.environ.items() if k not in FLAGS}
BASE_ENV["ATLAS_DB_URL"] = DB.url
BASE_ENV.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
BASE_ENV.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
BASE_ENV.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
BASE_ENV.setdefault("ATLAS_BREACH_CHECK", "bundled")
BASE_ENV.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def run_child(args: list[str], env: dict[str, str] | None = None, stdin: str | None = None,
              quiet_fail: bool = False) -> subprocess.CompletedProcess:
    child_env = dict(BASE_ENV)
    child_env.update(env or {})
    proc = subprocess.run([sys.executable, *args], cwd=HERE, env=child_env, input=stdin,
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 and not quiet_fail:
        print(f"  child {args[:2]!r} env={env} exited {proc.returncode}")
        print("  stdout: " + proc.stdout[-1500:].replace("\n", "\n  "))
        print("  stderr: " + proc.stderr[-1500:].replace("\n", "\n  "))
    return proc


def boot(env: dict[str, str] | None = None, **kw) -> subprocess.CompletedProcess:
    return run_child(["-c", "import app; app.startup()"], env, **kw)


# Boots, then signs in through the REAL login and forced password change, and reads the
# routes it is given. Input on stdin as JSON: {"username", "password" (or null to mint the
# account in-process with `admin user add --role owner`), "paths"}. Output: one line
# "PROBE {json}". The temporary password never leaves the child except as `admin` printed it.
PROBE = r'''
import io, json, re, secrets, sys
from contextlib import redirect_stdout
cfg = json.loads(sys.stdin.read())
import app
app.startup()
import admin
from fastapi.testclient import TestClient
ORIGIN = "https://testserver"
CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')
out = {}
pw = cfg.get("password")
if pw is None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        out["add_exit"] = admin.main(["user", "add", cfg["username"], "--role", "owner"])
    m = re.search(r"Temporary password for \S+\n\s+-+\n\s*\n\s+(\S+)", buf.getvalue())
    pw = m.group(1) if m else ""
c = TestClient(app.app, base_url=ORIGIN, follow_redirects=False)
r = c.get("/auth/login")
m = CSRF.search(r.text)
r = c.post("/auth/login", data={"username": cfg["username"], "password": pw,
                                "csrf_token": m.group(1) if m else ""},
           headers={"Origin": ORIGIN})
out["login"] = [r.status_code, r.headers.get("location")]
r = c.get("/auth/password/change")
m = CSRF.search(r.text)
new = secrets.token_urlsafe(24)
r = c.post("/auth/password/change",
           data={"current_password": pw, "new_password": new, "confirm_password": new,
                 "csrf_token": m.group(1) if m else ""},
           headers={"Origin": ORIGIN})
out["change"] = [r.status_code, r.headers.get("location")]
me = c.get("/api/me")
out["me"] = me.json() if me.status_code == 200 else {"status": me.status_code}
out["me"].pop("csrf_token", None)
out["paths"] = {}
for p in cfg["paths"]:
    r = c.get(p, headers={"Accept": "application/json"})
    try:
        body = r.json()
    except Exception:
        body = r.text[:200]
    out["paths"][p] = [r.status_code, body]
print("PROBE " + json.dumps(out))
'''


def probe(env: dict[str, str] | None, paths: list[str], username: str = "probe-owner",
          password: str | None = None) -> dict:
    proc = run_child(["-c", PROBE], env,
                     stdin=json.dumps({"username": username, "password": password, "paths": paths}))
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE ")), None)
    return json.loads(line[len("PROBE "):]) if line else {"error": proc.returncode}


def reset() -> None:
    with ENGINE.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def tables() -> set[str]:
    with ENGINE.connect() as conn:
        return {r[0] for r in conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}


def schema() -> dict[str, frozenset]:
    q = {
        "columns": """SELECT table_name, column_name, data_type, is_nullable
                      FROM information_schema.columns WHERE table_schema = 'public'""",
        "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'",
        "sequences": "SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'",
    }
    with ENGINE.connect() as conn:
        return {k: frozenset(tuple(r) for r in conn.execute(text(sql))) for k, sql in q.items()}


def data(only: set[str] | None = None) -> dict[str, tuple[int, str]]:
    """(row count, md5 of every row as jsonb text) per table, plus every sequence position."""
    out: dict[str, tuple[int, str]] = {}
    with ENGINE.connect() as conn:
        for t in sorted(tables()):
            if only is not None and t not in only:
                continue
            n, digest = conn.execute(text(
                f'SELECT count(*), coalesce(md5(string_agg(r, E\'\\n\' ORDER BY r)), \'\') '
                f'FROM (SELECT (to_jsonb(t))::text AS r FROM "{t}" t) x')).one()
            out[t] = (n, digest)
        if only is None:
            for name, last in conn.execute(text(
                    "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public'")):
                out[f"sequence:{name}"] = (-1, str(last))
    return out


def count(sql: str) -> int:
    with ENGINE.connect() as conn:
        return int(conn.execute(text(sql)).scalar() or 0)


def nodes_by_type() -> dict[str, int]:
    """Per type, the nodes that are not the system node set (counted out, see the docstring)."""
    with ENGINE.connect() as conn:
        return {t: n for t, n in conn.execute(text(
            "SELECT type, count(*) FROM node WHERE id NOT LIKE 'atlas-system%' GROUP BY type"))}


import system_handbook  # noqa: E402  (pure data, imports nothing)
import baseload_data  # noqa: E402  (generated pure data: the rest of the set, STORY-oss-23)
SYSTEM_IDS = sorted([n["id"] for n in system_handbook.NODES] + [n["id"] for n in baseload_data.NODES])
SYSTEM_EDGES = ({(n["parent"], "contains", n["id"]) for n in system_handbook.NODES if n["parent"]}
                | {(n["parent"], "contains", n["id"]) for n in baseload_data.NODES}
                | {(e["src"], e["edge_type"], e["dst"]) for e in baseload_data.EDGES})


def node_ids() -> list[str]:
    with ENGINE.connect() as conn:
        return sorted(r[0] for r in conn.execute(text("SELECT id FROM node")))


def only_the_system_set() -> bool:
    """The graph holds the system node set (handbook and baseload), its edges, and nothing else."""
    with ENGINE.connect() as conn:
        edges = [tuple(e) for e in conn.execute(text("SELECT src, edge_type, dst FROM edge"))]
    return node_ids() == SYSTEM_IDS and len(edges) == len(SYSTEM_EDGES) and set(edges) == SYSTEM_EDGES


# --------------------------------------------------------------------------- #
# A. no new configuration: exactly today's seed and tables
# --------------------------------------------------------------------------- #
# §A is "no configuration == today's homelab seed". It needs `seed.py` (its oracle imports
# it) and a distribution whose default IS that seed; the public export has neither, so there
# it skips and says why, rather than failing on a file it was never meant to ship.
if not SEED_IMPORTABLE:
    print("=" * 78)
    print("A. SKIPPED: `seed` is not importable (no backend/seed.py: the public export does not "
          "ship the homelab seed), so there is no homelab default to compare against")
    print("=" * 78)
elif DEFAULT_SEED != "homelab":
    print("=" * 78)
    print(f"A. SKIPPED: distribution.py ({distribution.NAME}) defaults ATLAS_SEED to "
          f"{DEFAULT_SEED!r}, not the homelab seed this section pins")
    print("=" * 78)
else:
    print("=" * 78)
    print("A. no ATLAS_SEED / ATLAS_LEGACY_V0: a fresh database is seeded exactly as today")
    print("=" * 78)
    # The homelab compose files are not exported (STORY-oss-23). The scan reads whichever
    # exist; when the homelab's are absent it says so instead of passing silently on nothing.
    _absent = [n for n, gone in (("docker-compose.yml", not (HERE.parent / "docker-compose.yml").is_file()),
                                 ("deploy/*.override.yml", not list((HERE.parent / "deploy").glob("*.override.yml"))))
               if gone]
    if _absent:
        print(f"SKIP the homelab compose scan for {', '.join(_absent)}: not in this tree")
    check("no compose file or deploy override sets ATLAS_SEED or ATLAS_LEGACY_V0",
          not [str(p) for p in [HERE.parent / "docker-compose.yml", *(HERE.parent / "deploy").rglob("*.y*ml")]
               if p.is_file() and re.search(r"ATLAS_(SEED|LEGACY_V0)\b", p.read_text(encoding="utf-8"))])

    # The oracle is today's seed path itself, run without startup: create the v0 tables and call
    # `seed.seed_all` on them. Startup with no new configuration must produce the same v0 rows.
    reset()
    po = run_child(["-c", (
        "import app\n"
        "from db import Base, engine, session\n"
        "from seed import seed_all\n"
        "Base.metadata.create_all(engine)\n"
        "with session() as s:\n"
        "    seed_all(s)\n"
    )])
    check("oracle: seed.seed_all into bare v0 tables exits 0", po.returncode == 0)
    oracle_v0 = data(V0_TABLES)

    reset()
    pa = boot()
    check("startup with no new configuration exits 0", pa.returncode == 0)
    s_default, d_default = schema(), data()
    check("the four v0 tables are created", V0_TABLES <= tables(), str(sorted(V0_TABLES - tables())))
    check("the homelab seed ran: v0 rows are present", count("SELECT count(*) FROM component") > 0,
          f"{count('SELECT count(*) FROM component')} components")
    check("criterion 1: v0 rows are byte-identical to today's seed_all", data(V0_TABLES) == oracle_v0)
    check("and the graph holds no node, as today (the homelab seed writes only v0 rows)",
          count("SELECT count(*) FROM node") == 0)
    check("the node_type registry is seeded, as every boot does", count("SELECT count(*) FROM node_type") > 0)

    reset()
    pe = boot({"ATLAS_SEED": "homelab", "ATLAS_LEGACY_V0": "1"})
    check("ATLAS_SEED=homelab ATLAS_LEGACY_V0=1 spelled out == no configuration (schema and data)",
          pe.returncode == 0 and schema() == s_default and data() == d_default)

    # The digest must be able to fail.
    with ENGINE.begin() as conn:
        conn.execute(text("UPDATE component SET name = name || 'x' WHERE id = (SELECT min(id) FROM component)"))
    check("the data comparison notices one changed character", data() != d_default)

    reset()
    r = probe(None, LEGACY_ROUTES)
    paths = r.get("paths", {})
    check("the legacy routes still answer 200 by default",
          all(paths.get(p, [0])[0] == 200 for p in LEGACY_ROUTES),
          str({p: paths.get(p, [None])[0] for p in LEGACY_ROUTES}))
    check("/api/tree by default has the homelab roots",
          bool((paths.get("/api/tree", [0, {}])[1] or {}).get("roots")))

# --------------------------------------------------------------------------- #
# B. ATLAS_SEED=none, and ATLAS_LEGACY_V0=0
# --------------------------------------------------------------------------- #
print("=" * 78)
print("B. ATLAS_SEED=none seeds nothing; ATLAS_LEGACY_V0=0 creates no v0 table and 404s the routes")
print("=" * 78)
reset()
pb = boot({"ATLAS_SEED": "none"})
check("startup with ATLAS_SEED=none exits 0", pb.returncode == 0)
check("criterion 2: zero nodes but the system node set, and no edge but its own",
      only_the_system_set(), str(node_ids()))
if DEFAULT_V0:
    check("the v0 tables exist (ATLAS_LEGACY_V0 defaults to 1) and are empty",
          V0_TABLES <= tables() and count("SELECT count(*) FROM component") == 0)
else:
    check(f"no v0 table exists (ATLAS_LEGACY_V0 defaults to 0 in distribution.py, {distribution.NAME})",
          not (V0_TABLES & tables()), str(sorted(V0_TABLES & tables())))

reset()
NONE_OFF = {"ATLAS_SEED": "none", "ATLAS_LEGACY_V0": "0"}
pb2 = boot(NONE_OFF)
check("startup with ATLAS_SEED=none ATLAS_LEGACY_V0=0 exits 0", pb2.returncode == 0)
s_off = schema()
check("criterion 2: zero nodes but the system node set", only_the_system_set(), str(node_ids()))
check("criterion 2: no v0 table is created", not (V0_TABLES & tables()), str(sorted(V0_TABLES & tables())))
check("the graph and auth tables are created as usual",
      {"node", "edge", "node_type", "auth_user", "node_visibility"} <= tables())

pu = run_child(["migrate_cli.py", "upgrade"], NONE_OFF)
check("`atlas-migrate upgrade` under ATLAS_LEGACY_V0=0 exits 0 on the booted database",
      pu.returncode == 0 and not (V0_TABLES & tables()))
reset()
pu2 = run_child(["migrate_cli.py", "upgrade"], {"ATLAS_LEGACY_V0": "0", "ATLAS_SEED": "none"})
check("`atlas-migrate upgrade` follows the flag: on an empty DB it builds startup's schema, no v0 table",
      pu2.returncode == 0 and schema() == s_off)

reset()
r = probe(NONE_OFF, LEGACY_ROUTES + ["/api/no-such-route", "/api/graph/tree", "/api/health"])
paths = r.get("paths", {})
missing = paths.get("/api/no-such-route", [None, None])
check("control: the probe is signed in (an authenticated read answers 200)",
      paths.get("/api/graph/tree", [0])[0] == 200, str(paths.get("/api/graph/tree", [None])[0]))
check("control: a path that does not exist answers 404", missing[0] == 404, str(missing))
health = paths.get("/api/health", [None, {}])
check("/api/health still answers 200 with no v0 table, reporting seeded=false",
      health[0] == 200 and isinstance(health[1], dict) and health[1].get("seeded") is False,
      str(health[0]))
for p in LEGACY_ROUTES:
    got = paths.get(p, [None, None])
    check(f"criterion 2: {p} answers 404, the same response as a path that does not exist",
          got[0] == 404 and got[1] == missing[1], str(got))

reset()
# "Unset" here meant "1"; where the distribution's default is 0, say 1 outright.
r = probe(dict({"ATLAS_SEED": "none"}, **({} if DEFAULT_V0 else {"ATLAS_LEGACY_V0": "1"})), LEGACY_ROUTES)
paths = r.get("paths", {})
check("with ATLAS_LEGACY_V0 unset (or 1) the same routes answer 200 on an empty DB (the 404 is the flag)",
      all(paths.get(p, [0])[0] == 200 for p in LEGACY_ROUTES),
      str({p: paths.get(p, [None])[0] for p in LEGACY_ROUTES}))

# --------------------------------------------------------------------------- #
# C. ATLAS_SEED=demo
# --------------------------------------------------------------------------- #
print("=" * 78)
print("C. ATLAS_SEED=demo seeds one project, one epic and two stories")
print("=" * 78)
for env in ({"ATLAS_SEED": "demo"}, {"ATLAS_SEED": "demo", "ATLAS_LEGACY_V0": "0"}):
    reset()
    pc = boot(env)
    check(f"startup with {env} exits 0", pc.returncode == 0)
    by_type = nodes_by_type()
    check(f"criterion 3 ({env}): exactly one project, one epic, two stories and nothing else",
          by_type == {"project": 1, "epic": 1, "story": 2}, str(by_type))
    with ENGINE.connect() as conn:
        edges = {(s, t, d) for s, t, d in conn.execute(text(
            "SELECT n1.type, e.edge_type, n2.type FROM edge e JOIN node n1 ON n1.id = e.src "
            "JOIN node n2 ON n2.id = e.dst WHERE e.src NOT LIKE 'atlas-system%'"))}
    check("the epic works on the project and contains both stories",
          edges == {("epic", "works_on", "project"), ("epic", "contains", "story")}
          and count("SELECT count(*) FROM edge WHERE src NOT LIKE 'atlas-system%'") == 3, str(edges))
    check("and the system node set is there beside it",
          [i for i in node_ids() if i.startswith("atlas-system")] == SYSTEM_IDS)
    check("no homelab v0 rows are seeded",
          "component" not in tables() or count("SELECT count(*) FROM component") == 0)
    d_demo = data()
    boot(env)
    check("a second boot adds nothing (the demo seed only writes into an empty graph)", data() == d_demo)

with ENGINE.connect() as conn:
    text_blob = "\n".join(" ".join(str(x or "") for x in row) for row in conn.execute(text(
        "SELECT id, name, summary, abstract, detail, body, owner FROM node")))
check("the demo content names no address, mailbox or host (no '@', '://', IPv4 or .local)",
      not re.search(r"@|://|\b\d{1,3}(\.\d{1,3}){3}\b|\.local\b", text_blob))
check("the demo nodes carry no owner", count("SELECT count(*) FROM node WHERE owner IS NOT NULL") == 0)

# --------------------------------------------------------------------------- #
# D. an existing populated database is left unchanged, under every profile
# --------------------------------------------------------------------------- #
print("=" * 78)
print("D. startup on a populated database changes no data, whatever ATLAS_SEED / ATLAS_LEGACY_V0 say")
print("=" * 78)
reset()
# The fixture writes v0 rows, so the populating boots need the v0 tables: no configuration in
# this repo (today's check, unchanged), ATLAS_LEGACY_V0=1 where the distribution defaults to 0.
D_BASE = {} if DEFAULT_V0 else {"ATLAS_LEGACY_V0": "1"}
boot(D_BASE)
# The v0 rows come from the homelab seed when the distribution defaults to it. Any other
# default writes none (the export ships no seed.py), so the fixture adds one v0 row itself, and
# "v0 rows unchanged" below stays a claim about real rows under either copy (STORY-oss-23 A3b).
D_V0_ROW = "" if DEFAULT_SEED == "homelab" else (
    "    s.add(Component(id='fixture-v0', name='fixture v0 row', kind='project', category='Project'))\n")
pd = run_child(["-c", (
    "import app, testseed\n"
    "from db import Component, session\n"
    "with session() as s:\n"
    "    testseed.build(s)\n"
    + D_V0_ROW +
    "    s.commit()\n"
)], D_BASE)
check("fixture data loaded on top of a default boot", pd.returncode == 0)
# One settling boot first: the fixture inserts TASK- ids without touching task_id_seq, and every
# boot advances that sequence past the ids it finds (`_seed_task_id_sequence`, today's
# behaviour). After that, a boot is a read, and that is what each profile must keep true.
boot(D_BASE)
d_pop = data()
check("the database is populated (graph nodes AND v0 rows), so 'unchanged' is not vacuous",
      count("SELECT count(*) FROM node") > 0 and count("SELECT count(*) FROM component") > 0,
      f"{count('SELECT count(*) FROM node')} nodes, {count('SELECT count(*) FROM component')} components")
D_ENVS = ({}, {"ATLAS_SEED": "homelab"}, {"ATLAS_SEED": "demo"}, {"ATLAS_SEED": "none"}, {"ATLAS_SEED": "self"},
          {"ATLAS_SEED": "none", "ATLAS_LEGACY_V0": "0"}, {"ATLAS_SEED": "demo", "ATLAS_LEGACY_V0": "0"})
if "homelab" not in distribution.SEED_PROFILES:  # the export: no seed.py, so no such profile
    D_ENVS = tuple(e for e in D_ENVS if e.get("ATLAS_SEED") != "homelab")
for env in D_ENVS:
    pp = boot(env)
    after = data()
    diff = sorted(k for k in set(d_pop) | set(after) if d_pop.get(k) != after.get(k))
    check(f"criterion 4: boot with {env or 'no new configuration'} leaves every row and sequence unchanged",
          pp.returncode == 0 and after == d_pop, f"changed: {diff[:6]}" if diff else "")

# --------------------------------------------------------------------------- #
# E. the first owner: `atlas-admin user add --role owner`
# --------------------------------------------------------------------------- #
print("=" * 78)
print("E. `atlas-admin user add <name> --role owner` on a fresh install gives a working owner")
print("=" * 78)
dockerfile = (HERE / "Dockerfile").read_text(encoding="utf-8")
check("the image puts `atlas-admin` on PATH, calling admin.py",
      "/usr/local/bin/atlas-admin" in dockerfile and 'exec python /app/admin.py "$@"' in dockerfile)
ph = run_child(["admin.py", "--help"])
check("the CLI calls itself atlas-admin", ph.returncode == 0 and "usage: atlas-admin" in ph.stdout,
      ph.stdout.splitlines()[0] if ph.stdout else "")

reset()
check("the database is empty: the app has never booted on it", not tables())
pa_add = run_child(["admin.py", "--actor", "verify", "user", "add", "first-owner", "--role", "owner"],
                   {"ATLAS_SEED": "none", "ATLAS_LEGACY_V0": "0"})
check("`atlas-admin user add first-owner --role owner` exits 0 before any boot (bootstrap)",
      pa_add.returncode == 0)
m = re.search(r"Temporary password for \S+\n\s+-+\n\s*\n\s+(\S+)", pa_add.stdout)
check("it printed a generated temporary password once", m is not None)
with ENGINE.connect() as conn:
    rows = list(conn.execute(text("SELECT subject, role, must_change_password FROM auth_user")))
check("one account, role owner, forced to change its password",
      [(r[0], r[1], bool(r[2])) for r in rows] == [("first-owner", "owner", True)], str(rows))
r = probe({"ATLAS_SEED": "none", "ATLAS_LEGACY_V0": "0"}, ["/api/access", "/api/graph/tree"],
          username="first-owner", password=m.group(1) if m else "")
check("criterion 5: the temp password signs in through the real login (303 to the forced change)",
      r.get("login", [0])[0] == 303 and "/auth/password/change" in (r.get("login", [0, ""])[1] or ""),
      str(r.get("login")))
check("the forced password change succeeds", r.get("change", [0])[0] == 303, str(r.get("change")))
me = r.get("me", {})
check("/api/me: a session principal, role owner, no change pending",
      me.get("role") == "owner" and me.get("kind") == "session" and me.get("must_change_password") is False,
      str({k: me.get(k) for k in ("kind", "role", "must_change_password")}))
paths = r.get("paths", {})
check("criterion 5: the owner reaches an owner-only route (/api/access 200)",
      paths.get("/api/access", [0])[0] == 200, str(paths.get("/api/access", [None])[0]))
check("and reads the graph", paths.get("/api/graph/tree", [0])[0] == 200)

pa_dup = run_child(["admin.py", "--actor", "verify", "user", "add", "first-owner", "--role", "owner"],
                   quiet_fail=True)
check("running the bootstrap again refuses (accounts are never recreated)", pa_dup.returncode == 1,
      pa_dup.stderr.strip()[:120])

# --------------------------------------------------------------------------- #
# F. malformed or contradictory configuration refuses to boot, naming the variable
# --------------------------------------------------------------------------- #
print("=" * 78)
print("F. a malformed or contradictory setting refuses to boot and names the variable")
print("=" * 78)
F_CASES = [({"ATLAS_SEED": "everything"}, "ATLAS_SEED"),
           ({"ATLAS_LEGACY_V0": "maybe", "ATLAS_SEED": "none"}, "ATLAS_LEGACY_V0")]
if DEFAULT_SEED == "homelab":
    F_CASES.append(({"ATLAS_LEGACY_V0": "0"}, "ATLAS_SEED=demo or ATLAS_SEED=none"))
elif "homelab" in distribution.SEED_PROFILES:
    F_CASES.append(({"ATLAS_LEGACY_V0": "0", "ATLAS_SEED": "homelab"}, "ATLAS_SEED=demo or ATLAS_SEED=none"))
else:  # the export: asking for the seed it does not ship is refused at import, not at seed time
    F_CASES.append(({"ATLAS_SEED": "homelab"}, "ATLAS_SEED"))
for env, needle in F_CASES:
    reset()
    pf = run_child(["-c", "import app"], env, quiet_fail=True)
    check(f"{env} fails at import, naming {needle}",
          pf.returncode != 0 and needle in pf.stderr and not tables(), pf.stderr.strip().splitlines()[-1][:160]
          if pf.stderr.strip() else "")

# --------------------------------------------------------------------------- #
# G. the neutral distribution.py (STORY-oss-23 A1): `none` is then the default
# --------------------------------------------------------------------------- #
print("=" * 78)
print("G. with the export's neutral distribution.py, a fresh install boots with ATLAS_SEED=none by default")
print("=" * 78)
OVERLAY = HERE.parent / "tools" / "carve" / "overlay" / "backend"
# In project-atlas the neutral copy is the overlay, put in front of backend/ on sys.path (the
# child is `python -c`, whose sys.path[0] is the cwd, backend/; so insert, not PYTHONPATH). In
# the export the overlay is already applied: backend/distribution.py IS the neutral copy.
NEUTRAL_DIR = OVERLAY if (OVERLAY / "distribution.py").is_file() else HERE
NEUTRAL_PRELUDE = f"import sys\nsys.path.insert(0, {str(NEUTRAL_DIR)!r})\n"
G_PROBE = NEUTRAL_PRELUDE + r'''
import html, json
import distribution, settings
import app
import db, passwords
app.startup()
import auth
from fastapi.testclient import TestClient
cfg = settings.load()
page = TestClient(app.app, base_url="https://testserver").get("/auth/privacy")
print("NEUTRAL " + json.dumps({
    "file": distribution.__file__, "name": distribution.NAME,
    "names": sorted(n for n in vars(distribution) if n.isupper()),
    "seed": cfg.seed, "default_seed": settings.DEFAULT_SEED_PROFILE, "profiles": list(settings.SEED_PROFILES),
    "legacy_v0": cfg.legacy_v0, "privacy_contact": auth.PRIVACY_CONTACT, "privacy_hosting": auth.PRIVACY_HOSTING,
    "privacy_page": [page.status_code, auth.PRIVACY_CONTACT in page.text],
    "hints": settings.load_deploy_hints({}).rebuild, "hint_host": settings.load_deploy_hints({}).host,
    "seed_imported": "seed" in sys.modules,
    # A4b: the controller line, the company note, the v0 owner default, the password words.
    "privacy_controller": auth.PRIVACY_CONTROLLER, "company_note": auth.PRIVACY_COMPANY_NOTE,
    "controller_paragraph": "<p><strong>%s</strong> holds it.</p>" % html.escape(auth.PRIVACY_CONTROLLER)
                            in page.text,
    "component_owner": db.Component.__table__.c.owner.default.arg,
    "context_words": list(passwords._CONTEXT_WORDS),
}))
'''
reset()
pg = run_child(["-c", G_PROBE])
line = next((ln for ln in pg.stdout.splitlines() if ln.startswith("NEUTRAL ")), None)
g = json.loads(line[len("NEUTRAL "):]) if line else {}
check("startup with the neutral distribution.py and no ATLAS_SEED / ATLAS_LEGACY_V0 exits 0",
      pg.returncode == 0 and bool(g), f"exit {pg.returncode}")
check("the child really read the neutral copy",
      g.get("name") == "neutral" and Path(g.get("file") or ".").resolve().parent == NEUTRAL_DIR.resolve(),
      str(g.get("file")))
check("the default seed profile is `none`, and `homelab` is not offered",
      g.get("seed") == "none" and g.get("default_seed") == "none" and "homelab" not in g.get("profiles", ["homelab"]),
      f"seed={g.get('seed')!r} profiles={g.get('profiles')}")
check("ATLAS_LEGACY_V0 defaults to off: no v0 table is created",
      g.get("legacy_v0") is False and not (V0_TABLES & tables()), str(sorted(V0_TABLES & tables())))
check("the fresh graph holds the system node set and nothing else (what `none` seeds)",
      only_the_system_set(), str(node_ids()))
check("the homelab seed module was never imported", g.get("seed_imported") is False)
check("the privacy page names the neutral placeholder mailbox",
      g.get("privacy_contact", "").endswith("@example.com") and g.get("privacy_page") == [200, True],
      f"{g.get('privacy_contact')!r} {g.get('privacy_page')}")
check("the deploy hints name an example.test host, and an unset command names its variable",
      g.get("hint_host", "").endswith(".example.test")
      and all(c.startswith("(no command configured: set ATLAS_DEPLOY_HINT_") for c in (g.get("hints") or {"": ""}).values()),
      f"{g.get('hint_host')!r} {g.get('hints')}")
check("the privacy page names the neutral controller, and with no company note the paragraph ends at 'holds it.'",
      g.get("privacy_controller") == "The operator of this install" and g.get("company_note") == ""
      and g.get("controller_paragraph") is True,
      f"{g.get('privacy_controller')!r} note={g.get('company_note')!r} paragraph={g.get('controller_paragraph')}")
check("the v0 Component.owner default and the password context words are the neutral ones",
      g.get("component_owner") == "owner" and g.get("context_words") == ["atlas"],
      f"{g.get('component_owner')!r} {g.get('context_words')}")
neutral_values = json.dumps({k: v for k, v in g.items() if k not in ("file", "names")})  # values, not the path
check("the neutral copy's defaults name no homelab value (host, mailbox domain, checkout path, hosting)",
      bool(g) and not homelab_words(neutral_values)
      and not re.search(r"(?i)project-atlas|Sweden|Cloudflare|deploy/homelab", neutral_values),
      f"{len(homelab_words(neutral_values))} homelab word(s)")
if distribution.NAME == "homelab":
    # The hashes above must catch something, or the check before this one is vacuous.
    _homelab_values = json.dumps({n: v for n, v in vars(distribution).items() if n.isupper()})
    check("the homelab-word hashes are not vacuous: the homelab copy's values contain words they match",
          len(homelab_words(_homelab_values)) >= 3, str(len(homelab_words(_homelab_values))))
if NEUTRAL_DIR != HERE:
    # Both copies are in this tree: they must define the same names, or one is missing a default.
    check("the homelab and neutral copies define the same names",
          g.get("names") == sorted(n for n in vars(distribution) if n.isupper()),
          str(sorted(set(g.get("names") or []) ^ {n for n in vars(distribution) if n.isupper()})))

# --------------------------------------------------------------------------- #
ENGINE.dispose()
print("=" * 78)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASSED")
