"""
Verification for STORY-atlas-system-node-handbook: the install's own system node set, its
handbook documents, and the /docs site generated from the same text.

The board's criteria, and the section that proves each:

  1. A fresh install with ATLAS_SEED=none has the atlas system node and one child per part,
     each with handbook docs (also under ATLAS_SEED=demo).                              -> B
  2. Seeding an instance that already has these nodes changes nothing (homelab untouched):
     a second boot, a second seed() and a second CLI --apply write nothing and record no
     graph_event; a homelab-shaped database (its own `atlas`, `atlas-api`, `atlas-mcp`,
     `atlas-postgres`, `atlas-retrieval`) is byte-identical after every profile's boot, and
     the opt-in CLI adds only `atlas-system*` rows.                                     -> C
  3. A static /docs site is generated from the same documents; it is reachable without
     login only when ATLAS_PUBLIC_DOCS=1, exact paths only; the staleness check fails on a
     changed document.                                                                  -> A, D
  4. The handbook covers concepts, configuration, operations, security and agents, and the
     scrub gate (enforce mode) passes on it.                                            -> A

Since the baseload (STORY-oss-23), `system_node.py` seeds the handbook set and then the generated
`baseload_data.py` set: the seeding checks (B, C) compare against both, the handbook checks (A)
against the handbook alone. The baseload itself is `verify_baseload.py`'s.

Every boot is a separate process (`import app; app.startup()`), as in verify_fresh_install.py:
settings are read at import, so one process is one configuration.

Run:
    python verify_system_node.py
"""
from __future__ import annotations

import base64
import copy
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools"))
import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:
    print(f"FATAL: {e}")
    sys.exit(1)

from sqlalchemy import create_engine, text  # noqa: E402

import docs_site  # noqa: E402  (tools/docs_site.py)
import system_handbook as H  # noqa: E402

import baseload_data as BL  # noqa: E402  (generated pure data; the baseload, STORY-oss-23)
import distribution  # noqa: E402  (pure data: which copy, and its default seed profile)

#: STORY-oss-23 A3b: whether the distribution's DEFAULT profile seeds the system node set on a
#: fresh graph. The homelab copy defaults to `homelab`, which never does, so section C's
#: homelab-shaped database is its default boot; every other profile does, so there that
#: database is built from a boot with the set's seeding stubbed out (see section C).
DEFAULT_SEEDS_SET = distribution.DEFAULT_SEED_PROFILE != "homelab"

ENGINE = create_engine(DB.url)
SYSTEM_IDS = [n["id"] for n in H.NODES]
# The full set system_node.py seeds since the baseload: the handbook nodes, then the baseload's.
# The handbook checks (A) read H; the seeding checks (B, C) compare against the full set.
ALL_IDS = SYSTEM_IDS + [n["id"] for n in BL.NODES]
ALL_TYPES = sorted([(n["id"], n["type"]) for n in H.NODES] + [(n["id"], n["type"]) for n in BL.NODES])
ALL_EDGES = ({(n["parent"], "contains", n["id"]) for n in H.NODES if n["parent"]}
             | {(n["parent"], "contains", n["id"]) for n in BL.NODES}
             | {(e["src"], e["edge_type"], e["dst"]) for e in BL.EDGES})
ALL_DOCS = ({(n["id"], H.doc_path(n["id"], d)): d["content"]
             for n in H.NODES for d in list(n["docs"]) + list(BL.HANDBOOK_EXTRAS.get(n["id"], {}).get("docs", []))}
            | {(n["id"], H.doc_path(n["id"], d)): d["content"] for n in BL.NODES for d in n["docs"]})
#: The homelab's own Atlas nodes the set must never meet (tools/seed-intents/atlas_nodes.txt
#: lists the rest; it is read below too).
HOMELAB_ATLAS = {"atlas": "project", "atlas-api": "service", "atlas-mcp": "service",
                 "atlas-postgres": "db", "atlas-retrieval": "capability"}
FLAGS = ("ATLAS_SEED", "ATLAS_LEGACY_V0", "ATLAS_RESEED", "ATLAS_PUBLIC_DOCS")

BASE_ENV = {k: v for k, v in os.environ.items() if k not in FLAGS}
BASE_ENV["ATLAS_DB_URL"] = DB.url
BASE_ENV.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
BASE_ENV.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
BASE_ENV.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
BASE_ENV.setdefault("ATLAS_BREACH_CHECK", "bundled")
BASE_ENV.setdefault("ATLAS_FRONTEND_DIR", str(REPO / "frontend"))
NONE = {"ATLAS_SEED": "none", "ATLAS_LEGACY_V0": "0"}

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


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


def boot(env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return run_child(["-c", "import app; app.startup()"], env)


def reset() -> None:
    with ENGINE.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def data() -> dict[str, tuple[int, str]]:
    """(row count, md5 of every row) per table, plus every sequence position."""
    out: dict[str, tuple[int, str]] = {}
    with ENGINE.connect() as conn:
        tables = sorted(r[0] for r in conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")))
        for t in tables:
            out[t] = tuple(conn.execute(text(
                f'SELECT count(*), coalesce(md5(string_agg(r, E\'\\n\' ORDER BY r)), \'\') '
                f'FROM (SELECT (to_jsonb(t))::text AS r FROM "{t}" t) x')).one())
        for name, last in conn.execute(text(
                "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public'")):
            out[f"sequence:{name}"] = (-1, str(last))
    return out


def rows(sql: str, **params) -> list[tuple]:
    with ENGINE.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), params)]


def other_rows() -> list[tuple]:
    """Every node and edge row that is not the system set's, as text: what 'unchanged' means."""
    return (rows("SELECT (to_jsonb(n))::text FROM node n WHERE id NOT LIKE 'atlas-system%' ORDER BY id")
            + rows("SELECT (to_jsonb(e))::text FROM edge e WHERE src NOT LIKE 'atlas-system%' "
                   "AND dst NOT LIKE 'atlas-system%' ORDER BY id")
            + rows("SELECT (to_jsonb(d))::text FROM node_doc d WHERE node_id NOT LIKE 'atlas-system%' "
                   "ORDER BY id"))


def events() -> int:
    return rows("SELECT count(*) FROM graph_event")[0][0]


# Boots, mints an owner, signs in through the real login and forced change, reads `paths`.
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
buf = io.StringIO()
with redirect_stdout(buf):
    admin.main(["user", "add", cfg["username"], "--role", "owner"])
m = re.search(r"Temporary password for \S+\n\s+-+\n\s*\n\s+(\S+)", buf.getvalue())
pw = m.group(1) if m else ""
c = TestClient(app.app, base_url=ORIGIN, follow_redirects=False)
r = c.get("/auth/login")
m = CSRF.search(r.text)
c.post("/auth/login", data={"username": cfg["username"], "password": pw,
                            "csrf_token": m.group(1) if m else ""}, headers={"Origin": ORIGIN})
r = c.get("/auth/password/change")
m = CSRF.search(r.text)
new = secrets.token_urlsafe(24)
c.post("/auth/password/change",
       data={"current_password": pw, "new_password": new, "confirm_password": new,
             "csrf_token": m.group(1) if m else ""}, headers={"Origin": ORIGIN})
out = {}
for p in cfg["paths"]:
    r = c.get(p, headers={"Accept": "application/json"})
    try:
        body = r.json()
    except Exception:
        body = r.text
    out[p] = [r.status_code, body]
print("PROBE " + json.dumps(out))
'''

# Anonymous requests through the real gate. Output: one line "ANON {json}".
ANON = r'''
import json, sys
cfg = json.loads(sys.stdin.read())
import app, auth, system_handbook
app.startup()
from fastapi.testclient import TestClient
c = TestClient(app.app, base_url="https://testserver", follow_redirects=False)
out = {"public": sorted(auth.PUBLIC_ROUTES), "req": []}
for method, path, headers in cfg["reqs"]:
    r = c.request(method, path, headers=headers)
    out["req"].append([method, path, r.status_code, r.headers.get("content-type", ""),
                       r.headers.get("content-security-policy", ""),
                       r.headers.get("cache-control", ""), r.text if r.status_code == 200 else ""])
print("ANON " + json.dumps(out))
'''


def child_json(script: str, tag: str, env: dict, payload: dict) -> dict:
    proc = run_child(["-c", script], env, stdin=json.dumps(payload))
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith(tag + " ")), None)
    return json.loads(line[len(tag) + 1:]) if line else {}


# --------------------------------------------------------------------------- #
print("=" * 78)
print("A. the source, the generated site, coverage and scrub")
print("=" * 78)
root = H.NODES[0]
check("the root is atlas-system, a `system` node, seeded first", root["id"] == H.ROOT == "atlas-system"
      and root["type"] == "system" and root["parent"] is None)
check("criterion 4: the root's handbook covers concepts, configuration, operations, security, agents",
      [d["file"] for d in root["docs"]]
      == ["concepts.md", "configuration.md", "operations.md", "security.md", "agents.md"])
children = {n["id"]: n["type"] for n in H.NODES[1:]}
check("one child per part, with the type that part is",
      children == {"atlas-system-api": "service", "atlas-system-mcp": "service",
                   "atlas-system-postgres": "db", "atlas-system-gateway": "service",
                   "atlas-system-search": "capability", "atlas-system-retrieval": "capability"},
      str(children))
check("every child is contained by the root and has at least one document",
      all(n["parent"] == H.ROOT and n["docs"] for n in H.NODES[1:]))
known = set(HOMELAB_ATLAS)
seed_list = REPO / "tools" / "seed-intents" / "atlas_nodes.txt"
if seed_list.is_file():
    known |= {ln.split("|")[0].strip() for ln in seed_list.read_text(encoding="utf-8").splitlines() if ln.strip()}
else:
    print(f"SKIP the live atlas tree's ids: {seed_list.relative_to(REPO).as_posix()} is not in this tree "
          "(the homelab ids above are still checked)")
check("no id meets a homelab node id (the live atlas tree, and the retrieval add-on's)",
      not (set(SYSTEM_IDS) & known) and all(i.startswith("atlas-system") for i in SYSTEM_IDS),
      str(sorted(set(SYSTEM_IDS) & known)))
check("nor does any baseload id, and every one starts atlas-system",
      not (set(ALL_IDS) & known) and all(i.startswith("atlas-system") for i in ALL_IDS),
      str(sorted(set(ALL_IDS) & known)))
blob = "\n".join(n["summary"] + "\n" + "\n".join(d["content"] for d in n["docs"]) for n in H.NODES)
check("the text names no mailbox, URL host, IPv4 address or .local host",
      not re.search(r"@|https?://|\b\d{1,3}(\.\d{1,3}){3}\b|\.local\b", blob))
check("every page says it is a skeleton (STORY-oss-20 writes the full handbook)",
      all("Skeleton page." in d["content"] for n in H.NODES for d in n["docs"]))

problems = docs_site.check()
check("criterion 3: frontend/docs/ is exactly what tools/docs_site.py renders from the module",
      not problems, "; ".join(problems[:4]))
check("SITE_FILES is every file under frontend/docs/",
      sorted(p.relative_to(REPO / "frontend" / "docs").as_posix()
             for p in (REPO / "frontend" / "docs").rglob("*") if p.is_file()) == list(H.SITE_FILES))
pages = [(REPO / "frontend" / "docs" / f).read_text(encoding="utf-8") for f in H.SITE_FILES
         if f.endswith(".html")]
check("no page carries a script, an inline style, an event handler or an external URL",
      not any(re.search(r"<script|<style|style=|\son[a-z]+=|https?://", pg, re.I) for pg in pages))

# The check must be able to fail: change one document, re-render, compare.
real_load = docs_site.load


def _changed():
    h = real_load()
    fake = types.SimpleNamespace(ROOT=h.ROOT, NODES=copy.deepcopy(h.NODES), page=h.page,
                                 SITE_FILES=h.SITE_FILES)
    fake.NODES[0]["docs"][0]["content"] += "\nOne more sentence.\n"
    return fake


docs_site.load = _changed
stale = docs_site.check()
docs_site.load = real_load
check("criterion 3: --check fails when a document changed and the site was not regenerated",
      stale == ["stale or missing: frontend/docs/atlas-system/concepts.html"], str(stale))
pc = subprocess.run([sys.executable, str(REPO / "tools" / "docs_site.py"), "--check"],
                    capture_output=True, text=True)
check("`python tools/docs_site.py --check` (what CI runs) exits 0 on the tree", pc.returncode == 0,
      (pc.stdout + pc.stderr).strip()[:200])

scrub_wf = (REPO / ".github" / "workflows" / "scrub.yml").read_text(encoding="utf-8")
paths_line = next((ln for ln in scrub_wf.splitlines() if ln.strip().startswith("SCRUB_PATHS:")), "")
# Either SCRUB_PATHS names both, or it is empty with SCRUB_MODE enforce: the whole tree is then
# enforced, which covers them (the public export rewrites scrub.yml that way, STORY-oss-23).
paths_value = paths_line.split(":", 1)[1].strip().strip("\"'") if paths_line else None
mode_line = next((ln for ln in scrub_wf.splitlines() if ln.strip().startswith("SCRUB_MODE:")), "")
whole_tree = paths_value == "" and mode_line.split(":", 1)[1].strip().strip("\"'") == "enforce"
check("scrub.yml enforces on the handbook module and the site (named, or the whole tree)",
      whole_tree or (paths_value is not None and "backend/system_handbook.py" in paths_value
                     and "frontend/docs/*" in paths_value), f"{paths_line.strip()} {mode_line.strip()}")
ps = subprocess.run([sys.executable, str(REPO / "tools" / "scrub" / "scan.py"), "--mode", "enforce",
                     "--path", "backend/system_handbook.py", "--path", "frontend/docs/*"],
                    cwd=REPO, capture_output=True, text=True,
                    env={k: v for k, v in os.environ.items() if k != "SCRUB_DENYLIST_FILE"})
check("criterion 4: the scrub scanner in enforce mode passes on them (generic patterns here; CI "
      "adds the denylist)", ps.returncode == 0, ps.stdout.strip().splitlines()[0][:160] if ps.stdout else "")

# --------------------------------------------------------------------------- #
print("=" * 78)
print("B. a fresh install seeds the set, each node with its documents")
print("=" * 78)
for env in (NONE, {"ATLAS_SEED": "none"}, {"ATLAS_SEED": "demo", "ATLAS_LEGACY_V0": "0"}):
    reset()
    check(f"startup with {env} exits 0", boot(env).returncode == 0)
    got = rows("SELECT id, type, name, summary FROM node WHERE id LIKE 'atlas-system%' ORDER BY id")
    check(f"criterion 1 ({env}): the system node and one child per part",
          {(n["id"], n["type"]) for n in H.NODES} <= {(i, t) for i, t, _, _ in got}, str(got[:8]))
    check(f"({env}): and exactly the baseload beside them, nothing else under atlas-system*",
          [(i, t) for i, t, _, _ in got] == ALL_TYPES, f"{len(got)} of {len(ALL_TYPES)}")
    edges = set(rows("SELECT src, edge_type, dst FROM edge WHERE dst LIKE 'atlas-system%'"))
    check("each handbook child is contained by the root",
          {(H.ROOT, "contains", n["id"]) for n in H.NODES[1:]} <= edges)
    check("and nothing else points into the set: the edges are exactly the set's contains and requires",
          edges == ALL_EDGES, f"{len(edges)} vs {len(ALL_EDGES)}; extra {sorted(edges - ALL_EDGES)[:3]}, "
          f"missing {sorted(ALL_EDGES - edges)[:3]}")
    docs = {(nid, p): c for nid, p, c in rows(
        "SELECT node_id, path, content FROM node_doc WHERE node_id LIKE 'atlas-system%'")}
    want = {(n["id"], H.doc_path(n["id"], d)): d["content"] for n in H.NODES for d in n["docs"]}
    check("criterion 1: every node has its handbook documents, stored as written",
          all(docs.get(k) == v for k, v in want.items()), f"{len(docs)} rows")
    check("and every baseload document, and no other", docs == ALL_DOCS, f"{len(docs)} of {len(ALL_DOCS)}")
    attrs = {i: a for i, a in rows("SELECT id, attributes FROM node WHERE id LIKE 'atlas-system%'")}
    check("every document is served: it is in its node's docs[] allowlist",
          all({d["path"] for d in (attrs[nid] or {}).get("docs", [])}
              == {p for (i, p) in ALL_DOCS if i == nid} for nid in ALL_IDS))
    check("a seed records no graph_event (it is not a user mutation)", events() == 0)
    check("the root is marked shared (and nothing else in the set is marked)",
          rows("SELECT node_id, visibility FROM node_visibility WHERE node_id LIKE 'atlas-system%'")
          == [(H.ROOT, "shared")])

# The OSS install's RBAC as shipped: private by default, no implicit owner. The handbook must
# still be readable by a person with no binding at all, through the shared mark.
reset()
RBAC_OSS = {**NONE, "ATLAS_RBAC": "projects", "ATLAS_DEFAULT_PROJECT_VISIBILITY": "private",
            "ATLAS_SHARED_PROJECT_ROLE": "viewer", "ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER": "false"}
r = child_json(PROBE, "PROBE", RBAC_OSS, {"username": "probe-owner", "paths": [
    "/api/node/atlas-system", "/api/node/atlas-system-search"]})
check("with the OSS RBAC settings, a person with no project role reads the system nodes",
      all(v[0] == 200 for v in r.values()) and len(r) == 2, str({k: v[0] for k, v in r.items()}))

reset()
first = H.doc_path(H.ROOT, root["docs"][0])
r = child_json(PROBE, "PROBE", NONE, {"username": "probe-owner", "paths": [
    "/api/node/atlas-system", f"/api/node/atlas-system/doc?path={first}",
    "/api/node/atlas-system-mcp/doc?path=" + H.doc_path("atlas-system-mcp", H.NODES[2]["docs"][0]),
    "/docs/", "/docs/atlas-system/security.html"]})
doc = r.get(f"/api/node/atlas-system/doc?path={first}", [0, {}])
check("through the API: GET /api/node/atlas-system answers 200",
      r.get("/api/node/atlas-system", [0])[0] == 200, str(r.get("/api/node/atlas-system", [None])[0]))
check("through the API: the read route (atlas_doc_read's) serves the handbook document",
      doc[0] == 200 and isinstance(doc[1], dict) and doc[1].get("content") == root["docs"][0]["content"],
      str(doc[0]))
check("a signed-in person reads /docs with ATLAS_PUBLIC_DOCS unset (gated, not absent)",
      r.get("/docs/", [0])[0] == 200 and r.get("/docs/atlas-system/security.html", [0])[0] == 200)

# --------------------------------------------------------------------------- #
print("=" * 78)
print("C. seeding again, or into a homelab-shaped database, changes nothing it should not")
print("=" * 78)
reset()
boot(NONE)
d1 = data()
boot(NONE)
check("criterion 2: a second boot under ATLAS_SEED=none writes nothing", data() == d1)
p = run_child(["-c", "import app, system_node\nfrom db import session\n"
                     "with session() as s:\n    print('ADDED', system_node.seed(s))\n    s.commit()\n"], NONE)
check("criterion 2: seed() on a graph that has the set returns [] ...", "ADDED []" in p.stdout, p.stdout[-200:])
check("... and writes nothing, not even a derived value", data() == d1)
p = run_child(["system_node.py", "--apply"], NONE)
check("the CLI with --apply says there is nothing to do, and writes nothing",
      p.returncode == 0 and "nothing to do" in p.stdout and data() == d1, p.stdout.strip()[:160])

# A homelab-shaped database: the default profile, the curated fixture, and the homelab's own
# Atlas nodes under their real ids.
reset()
if DEFAULT_SEEDS_SET:
    # Under a copy whose every profile seeds the set on a fresh graph, the homelab-shaped install
    # (one made before the set existed) is a boot with the set's seeding stubbed out in that one
    # child; startup is otherwise real, and every later boot here is the real one.
    run_child(["-c", "import system_node; system_node.seed = lambda s: []; import app; app.startup()"])
else:
    boot()
seed_hl = "\n".join([
    "import app, testseed",
    "from db import session",
    "from models_graph import Node, Edge",
    "from attention import recompute_derived",
    "with session() as s:",
    "    testseed.build(s)",
    f"    for nid, t in {sorted(HOMELAB_ATLAS.items())!r}:",
    "        s.add(Node(id=nid, type=t, name=nid, attributes={}, body='homelab', source=None, origin='atlas'))",
    "    s.flush()",
    "    for nid in ('atlas-api', 'atlas-mcp', 'atlas-postgres', 'atlas-retrieval'):",
    "        s.add(Edge(src='atlas', dst=nid, edge_type='contains', origin='atlas'))",
    "    s.flush()",
    "    recompute_derived(s)",
    "    s.commit()",
])
check("homelab-shaped database built (fixture + atlas, atlas-api, atlas-mcp, atlas-postgres, "
      "atlas-retrieval)", run_child(["-c", seed_hl]).returncode == 0)
boot()  # settle the task-id sequence, as verify_fresh_install.py §D does
d_hl = data()
check("it has no system node", not rows("SELECT id FROM node WHERE id LIKE 'atlas-system%'"))
C_ENVS = ({}, {"ATLAS_SEED": "homelab"}, {"ATLAS_SEED": "none"}, {"ATLAS_SEED": "demo"}, NONE)
if "homelab" not in distribution.SEED_PROFILES:  # the export: no seed.py, so no such profile
    C_ENVS = tuple(e for e in C_ENVS if e.get("ATLAS_SEED") != "homelab")
for env in C_ENVS:
    pb = boot(env)
    check(f"criterion 2: boot with {env or 'no new configuration'} leaves the homelab byte-identical",
          pb.returncode == 0 and data() == d_hl)
others = other_rows()
ev0 = events()
p = run_child(["system_node.py"], {})
check("the CLI's dry run names the seven ids and writes nothing",
      p.returncode == 0 and all(i in p.stdout for i in SYSTEM_IDS) and data() == d_hl, p.stdout.strip()[:200])
check("... and every baseload id", all(i in p.stdout for i in ALL_IDS))
p = run_child(["system_node.py", "--apply"], {})
added = [r[0] for r in rows("SELECT id FROM node WHERE id LIKE 'atlas-system%' ORDER BY id")]
check("the opt-in CLI --apply adds exactly the set (the handbook nodes and the baseload)",
      p.returncode == 0 and set(SYSTEM_IDS) <= set(added) and added == sorted(ALL_IDS),
      f"{len(added)} of {len(ALL_IDS)}")
check("criterion 2: and every other node, edge and document row is byte-identical "
      "(the homelab's own `atlas` tree included)", other_rows() == others)
check("no graph_event was recorded", events() == ev0)
d_after = data()
run_child(["system_node.py", "--apply"], {})
check("criterion 2: --apply again writes nothing", data() == d_after)
boot({"ATLAS_SEED": "none"})
check("and a boot afterwards writes nothing", data() == d_after)

# --------------------------------------------------------------------------- #
print("=" * 78)
print("D. /docs: gated by default, public only with ATLAS_PUBLIC_DOCS=1, exact paths only")
print("=" * 78)
JSON = {"Accept": "application/json"}
HTMLH = {"Accept": "text/html"}
site_reqs = [["GET", "/docs", JSON], ["GET", "/docs/", JSON]] + [["GET", "/docs/" + f, JSON]
                                                                   for f in H.SITE_FILES]
odd = [["GET", "/docs/not-a-page.html", JSON], ["GET", "/docs/atlas-system/../../backend/auth.py", JSON],
       ["GET", "/docs/atlas-system", JSON], ["POST", "/docs/index.html", JSON],
       ["GET", "/docs/index.html", {"Accept": "application/json", "Authorization": "Bearer not-a-token"}]]
reset()
off = child_json(ANON, "ANON", NONE, {"reqs": site_reqs + [["GET", "/docs/", HTMLH]]})
default_public = {"/", "/healthz", "/readyz", "/auth/login", "/auth/password/change", "/auth/denied",
                  "/auth/privacy", "/favicon.ico"}
check("unset: PUBLIC_ROUTES is the documented default set, no /docs path in it",
      set(off.get("public", [])) == default_public, str(off.get("public")))
check("unset: every /docs path answers an anonymous caller 401, like any gated route",
      all(r[2] == 401 for r in off.get("req", [])[:-1]), str([r[:3] for r in off.get("req", [])][:4]))
check("unset: an anonymous browser is sent to the login page", (off.get("req") or [[0] * 3])[-1][2] == 302)

for flag in ("1", "true"):
    reset()
    on = child_json(ANON, "ANON", {**NONE, "ATLAS_PUBLIC_DOCS": flag}, {"reqs": site_reqs + odd})
    added_paths = set(on.get("public", [])) - default_public
    check(f"ATLAS_PUBLIC_DOCS={flag}: PUBLIC_ROUTES gains exactly the site's paths",
          added_paths == set(H.public_paths()) and default_public <= set(on.get("public", [])),
          str(sorted(added_paths))[:200])
    reqs = on.get("req", [])
    served = reqs[:len(site_reqs)]
    check("criterion 3: every page, the index and the stylesheet answer an anonymous caller 200",
          len(served) == len(site_reqs) and all(r[2] == 200 for r in served),
          str([r[1:3] for r in served if r[2] != 200]))
    on_disk = all(r[6] == (REPO / "frontend" / "docs" / (r[1][len("/docs/"):] or "index.html")
                           ).read_text(encoding="utf-8")
                  for r in served if r[1] not in ("/docs",))
    check("each response is the generated file, byte for byte", on_disk)
    check("pages are text/html and the stylesheet text/css",
          all(("text/css" if r[1].endswith(".css") else "text/html") in r[3] for r in served))
    check("each carries its own CSP (no script source at all) and private, no-cache",
          all("default-src 'none'" in r[4] and "script-src" not in r[4] and r[5] == "private, no-cache"
              for r in served))
    rest = {r[1] + " " + r[0]: r[2] for r in reqs[len(site_reqs):]}
    check("a /docs path that is not a generated file stays gated (401), and so does a traversal",
          rest.get("/docs/not-a-page.html GET") == 401 and rest.get("/docs/atlas-system GET") == 401
          and rest.get("/docs/atlas-system/../../backend/auth.py GET") in (401, 404), str(rest))
    check("a POST is not served (405 or 401), and a bad Bearer on a public page is still 401",
          rest.get("/docs/index.html POST") in (401, 405)
          and list(rest.values())[-1] == 401, str(rest))

pf = run_child(["-c", "import app"], {**NONE, "ATLAS_PUBLIC_DOCS": "maybe"}, quiet_fail=True)
check("ATLAS_PUBLIC_DOCS=maybe refuses to boot, naming the variable",
      pf.returncode != 0 and "ATLAS_PUBLIC_DOCS" in pf.stderr)
env_example = (REPO / "deploy" / "oss" / ".env.example").read_text(encoding="utf-8")
check("the OSS .env.example turns it on", re.search(r"^ATLAS_PUBLIC_DOCS=1$", env_example, re.M) is not None)
# The live overlay opts in (owner's decision, 2026-11-01); every other compose file stays off.
PROD_OVERLAY = REPO / "deploy" / "prod.override.yml"
compose_files = [p for p in [REPO / "docker-compose.yml", *(REPO / "deploy").rglob("*.y*ml")]
                 if p.is_file() and p != PROD_OVERLAY]
if not compose_files:
    print("SKIP only the live overlay opts in: no other compose file or deploy override is in this tree")
others = [str(p) for p in compose_files if "ATLAS_PUBLIC_DOCS" in p.read_text(encoding="utf-8")]
check("only the live overlay opts in (ADR 0047 §3); no other homelab compose file or deploy override sets it",
      not others, str(others))
if PROD_OVERLAY.is_file():
    prod_lines = [ln.strip() for ln in PROD_OVERLAY.read_text(encoding="utf-8").splitlines()
                  if "ATLAS_PUBLIC_DOCS" in ln and not ln.lstrip().startswith("#")]
    check("deploy/prod.override.yml sets it only as ${ATLAS_PUBLIC_DOCS:-1}",
          prod_lines == ["ATLAS_PUBLIC_DOCS: ${ATLAS_PUBLIC_DOCS:-1}"], str(prod_lines))
else:
    print("SKIP deploy/prod.override.yml sets it only as ${ATLAS_PUBLIC_DOCS:-1}: "
          "deploy/prod.override.yml is not in this tree")

# --------------------------------------------------------------------------- #
ENGINE.dispose()
print("=" * 78)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASSED")
