"""
Verification for TASK-1461: the `self` seed profile (`ATLAS_SEED=self`), Atlas's own world model.

  A. The committed profile passes the exporter's self-check (`tools/export_self_seed.py check`:
     the scrub gate's generic patterns, the home-path patterns, the denylist when
     SCRUB_DENYLIST_FILE names one, no dangling edge or intent), and holds no workitem.
  B. A fresh `ATLAS_SEED=self ATLAS_LEGACY_V0=0` boot seeds exactly the profile plus the
     `atlas-system` handbook set: every node, edge, `docs[]` entry, `node_doc` row and intent,
     no workitem, no account, no grant, no `graph_event`, the root marked `shared`.
  C. A second boot writes nothing (every row and sequence unchanged).
  D. A first owner signs in, reads a seeded node, its document and its intents, and files a
     piece of work against a seeded node (`POST /api/workitem` with `binding`), which then reads
     back bound to it and is listed by `GET /api/workitems?binding=`.
  E. Opt-in and additive: a populated database is unchanged by a boot under `self`, and a fresh
     boot with no ATLAS_SEED (the homelab default) seeds none of the profile.

Every boot is its own process (`import app; app.startup()`), as `verify_fresh_install.py` does.

Run:
    python verify_self_seed.py
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

from sqlalchemy import MetaData, Table, create_engine, func, select, text  # noqa: E402

import self_seed_data  # noqa: E402  (pure data)
import system_handbook  # noqa: E402  (pure data)

ENGINE = create_engine(DB.url)
PROFILE = self_seed_data.PROFILE
ROOT = PROFILE["root"]
FLAGS = ("ATLAS_SEED", "ATLAS_LEGACY_V0", "ATLAS_RESEED", "ATLAS_RBAC")
SELF = {"ATLAS_SEED": "self", "ATLAS_LEGACY_V0": "0"}

BASE_ENV = {k: v for k, v in os.environ.items() if k not in FLAGS}
BASE_ENV["ATLAS_DB_URL"] = DB.url
BASE_ENV.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
BASE_ENV.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
BASE_ENV.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
BASE_ENV.setdefault("ATLAS_BREACH_CHECK", "bundled")
BASE_ENV.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def run_child(args: list[str], env: dict[str, str] | None = None, stdin: str | None = None,
              cwd: Path = HERE) -> subprocess.CompletedProcess:
    child_env = dict(BASE_ENV)
    child_env.update(env or {})
    proc = subprocess.run([sys.executable, *args], cwd=cwd, env=child_env, input=stdin,
                          capture_output=True, text=True, timeout=300, encoding="utf-8")
    if proc.returncode != 0:
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


def rows(sql: str, **params) -> list[tuple]:
    """A fixed SQL string (a literal at every call site) with bound parameters; no SQL here is
    ever built by interpolation."""
    with ENGINE.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), params)]


def table_count(name: str) -> int:
    """Row count of one table, through a Core construct (the name is a reflected identifier,
    quoted by SQLAlchemy, never spliced into SQL text)."""
    with ENGINE.connect() as conn:
        t = Table(name, MetaData(), autoload_with=conn)
        return int(conn.execute(select(func.count()).select_from(t)).scalar() or 0)


def digest() -> dict[str, tuple[int, str]]:
    """(row count, md5 of every row) per table, plus every sequence position. Each table is
    read with a reflected Core `select`, and the rows are hashed in Python, so no SQL text is
    assembled from a table name."""
    out: dict[str, tuple[int, str]] = {}
    with ENGINE.connect() as conn:
        md = MetaData()
        md.reflect(bind=conn, schema=None)
        for name, t in sorted(md.tables.items()):
            lines = sorted(json.dumps([str(v) for v in r], ensure_ascii=False)
                           for r in conn.execute(select(t)))
            out[name] = (len(lines), hashlib.md5("\n".join(lines).encode("utf-8"),
                                                  usedforsecurity=False).hexdigest())
        for name, last in conn.execute(text(
                "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname='public'")):
            out[f"sequence:{name}"] = (-1, str(last))
    return out


import baseload_data  # noqa: E402  (generated pure data: the rest of the system set, STORY-oss-23)

SYSTEM_IDS = {n["id"] for n in system_handbook.NODES} | {n["id"] for n in baseload_data.NODES}
SYSTEM_EDGES = ({(n["parent"], "contains", n["id"]) for n in system_handbook.NODES if n["parent"]}
                | {(n["parent"], "contains", n["id"]) for n in baseload_data.NODES}
                | {(e["src"], e["edge_type"], e["dst"]) for e in baseload_data.EDGES})
PROFILE_IDS = {n["id"] for n in PROFILE["nodes"]}
PROFILE_EDGES = {(e["src"], e["edge_type"], e["dst"]) for e in PROFILE["edges"]}
PROFILE_DOCS = {(n["id"], d["path"]) for n in PROFILE["nodes"] for d in n["docs"]}
WORK_TYPES = {"epic", "feature", "story", "task", "subtask", "bug", "decision"}

# --------------------------------------------------------------------------- #
print("=" * 78)
print("A. the committed profile: self-check, shape, no work")
print("=" * 78)
pc = run_child([str(HERE.parent / "tools" / "export_self_seed.py"), "check"], cwd=HERE.parent)
check("tools/export_self_seed.py check exits 0 (forbidden patterns, dangling refs)", pc.returncode == 0,
      (pc.stdout + pc.stderr).strip().splitlines()[-1] if (pc.stdout + pc.stderr).strip() else "")
check("the profile holds no workitem type", not {n["type"] for n in PROFILE["nodes"]} & WORK_TYPES)
check("the profile root is the `atlas` project",
      ROOT == "atlas" and any(n["id"] == ROOT and n["type"] == "project" for n in PROFILE["nodes"]))
check("no profile id meets the atlas-system handbook set", not PROFILE_IDS & SYSTEM_IDS)
check("every profile node but the root has a `contains` parent in the profile",
      {d for s, t, d in PROFILE_EDGES if t == "contains"} == PROFILE_IDS - {ROOT})
check("the profile is non-trivial (nodes, edges, documents, intents all present)",
      len(PROFILE_IDS) > 10 and PROFILE_EDGES and PROFILE_DOCS and PROFILE["intents"],
      f"{len(PROFILE_IDS)} nodes, {len(PROFILE_EDGES)} edges, {len(PROFILE_DOCS)} docs, "
      f"{len(PROFILE['intents'])} intents")

# --------------------------------------------------------------------------- #
print("=" * 78)
print("B. a fresh ATLAS_SEED=self boot seeds exactly the profile plus the handbook set")
print("=" * 78)
reset()
check("fresh ATLAS_SEED=self ATLAS_LEGACY_V0=0 boot", boot(SELF).returncode == 0)
ids = {r[0] for r in rows("SELECT id FROM node")}
check("nodes == profile + atlas-system set", ids == PROFILE_IDS | SYSTEM_IDS,
      f"missing {sorted((PROFILE_IDS | SYSTEM_IDS) - ids)[:5]}, extra {sorted(ids - PROFILE_IDS - SYSTEM_IDS)[:5]}")
edges = set(rows("SELECT src, edge_type, dst FROM edge"))
sys_edges = SYSTEM_EDGES
check("edges == profile edges + the system set's own edges", edges == PROFILE_EDGES | sys_edges,
      f"{len(edges)} vs {len(PROFILE_EDGES | sys_edges)}")
docs = set(rows("SELECT node_id, path FROM node_doc WHERE node_id NOT LIKE 'atlas-system%'"))
check("every profile document is a node_doc row", docs == PROFILE_DOCS, f"{len(docs)} vs {len(PROFILE_DOCS)}")
listed = {(nid, d["path"]) for nid, attrs in rows("SELECT id, attributes FROM node WHERE id NOT LIKE 'atlas-system%'")
          for d in (attrs or {}).get("docs", [])}
check("and each is listed in its node's docs[] (the read route's allowlist)", listed == PROFILE_DOCS)
intents = set(rows("SELECT node_id, slug, kind, statement FROM node_intent WHERE node_id NOT LIKE 'atlas-system%'"))
want = {(i["node_id"], i["slug"], i["kind"], i["statement"]) for i in PROFILE["intents"]}
check("intents == the profile's included intents", intents == want, f"{len(intents)} vs {len(want)}")
types = {r[0] for r in rows("SELECT type FROM node")}
check("no workitem was seeded", not types & WORK_TYPES, str(sorted(types)))
check("no graph_event, no account, no grant, no role binding",
      all(table_count(tbl) == 0
          for tbl in ("graph_event", "auth_user", "node_grant", "project_role_binding")))
check("the root is marked shared",
      rows("SELECT visibility FROM node_visibility WHERE node_id = :root", root=ROOT) == [("shared",)])

# --------------------------------------------------------------------------- #
print("=" * 78)
print("C. a second boot writes nothing")
print("=" * 78)
before = digest()
check("second ATLAS_SEED=self boot", boot(SELF).returncode == 0)
after = digest()
diff = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
check("every row and sequence unchanged", not diff, f"changed: {diff[:6]}")

# --------------------------------------------------------------------------- #
print("=" * 78)
print("D. a first owner reads the seeded world model and files work against it")
print("=" * 78)
FIRST = r'''
import io, json, re, secrets, sys
from contextlib import redirect_stdout
cfg = json.loads(sys.stdin.read())
import app
app.startup()
import admin
from fastapi.testclient import TestClient
ORIGIN = "https://testserver"
H = {"Origin": ORIGIN, "Accept": "application/json"}
CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')
buf = io.StringIO()
with redirect_stdout(buf):
    admin.main(["user", "add", "first-owner", "--role", "owner"])
pw = re.search(r"Temporary password for \S+\n\s+-+\n\s*\n\s+(\S+)", buf.getvalue()).group(1)
c = TestClient(app.app, base_url=ORIGIN, follow_redirects=False)
m = CSRF.search(c.get("/auth/login").text)
c.post("/auth/login", data={"username": "first-owner", "password": pw, "csrf_token": m.group(1)}, headers=H)
m = CSRF.search(c.get("/auth/password/change").text)
new = secrets.token_urlsafe(24)
c.post("/auth/password/change", data={"current_password": pw, "new_password": new,
       "confirm_password": new, "csrf_token": m.group(1)}, headers=H)
csrf = c.get("/api/me").json()["csrf_token"]
out = {}
r = c.get("/api/node/" + cfg["node"]); out["node"] = [r.status_code, r.json() if r.status_code == 200 else None]
r = c.get("/api/node/" + cfg["doc_node"] + "/doc", params={"path": cfg["doc_path"]})
out["doc"] = [r.status_code, (r.json().get("content") or "")[:80] if r.status_code == 200 else None]
r = c.get("/api/intents"); out["intents"] = [r.status_code, r.json().get("count") if r.status_code == 200 else None]
r = c.post("/api/workitem", json={"type": "epic", "id": "EPIC-first-own-work", "name": "Our first epic",
           "binding": cfg["node"], "status": "pending", "csrf_token": csrf}, headers=H)
out["file"] = [r.status_code, r.json()]
r = c.get("/api/node/EPIC-first-own-work"); out["epic"] = [r.status_code, r.json() if r.status_code == 200 else None]
r = c.get("/api/workitems", params={"binding": cfg["node"]})
out["root_work"] = [r.status_code, r.json() if r.status_code == 200 else r.text[:200]]
print("PROBE " + json.dumps(out))
'''
doc_node = next(n for n in PROFILE["nodes"] if n["docs"])
target = "atlas-api" if "atlas-api" in PROFILE_IDS else sorted(PROFILE_IDS - {ROOT})[0]
proc = run_child(["-c", FIRST], SELF, stdin=json.dumps({
    "node": target, "root": ROOT, "doc_node": doc_node["id"], "doc_path": doc_node["docs"][0]["path"]}))
line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE ")), None)
out = json.loads(line[len("PROBE "):]) if line else {}
node = (out.get("node") or [0, None])
check(f"GET /api/node/{target}: 200, with its seeded abstract",
      node[0] == 200 and bool((node[1] or {}).get("abstract") or (node[1] or {}).get("summary")))
check(f"GET /api/node/{doc_node['id']}/doc serves a seeded document",
      (out.get("doc") or [0])[0] == 200 and bool((out.get("doc") or [0, ""])[1]))
check("GET /api/intents serves the seeded intents (the profile's and the baseload's)",
      (out.get("intents") or [0])[0] == 200
      and (out.get("intents") or [0, 0])[1] == len(PROFILE["intents"]) + len(baseload_data.INTENTS))
fil = out.get("file") or [0, {}]
check(f"POST /api/workitem files an epic bound to {target}: 201", fil[0] == 201, str(fil)[:200])
epic = out.get("epic") or [0, None]
check("the epic reads back bound to the seeded node",
      epic[0] == 200 and any(b.get("id") == target for b in (epic[1] or {}).get("bindings", [])),
      str((epic[1] or {}).get("bindings"))[:160])
rw = out.get("root_work") or [0, None]
body = json.dumps(rw[1]) if rw[0] == 200 else str(rw)
check(f"and GET /api/workitems?binding={target} lists it", rw[0] == 200 and "EPIC-first-own-work" in body,
      body[:160])
check("filing work recorded a graph_event (the seed itself recorded none)",
      rows("SELECT count(*) FROM graph_event")[0][0] > 0)

# --------------------------------------------------------------------------- #
print("=" * 78)
print("E. opt-in and additive: a populated database and the default profile are untouched")
print("=" * 78)
reset()
check("fresh boot with no ATLAS_SEED (the homelab default)", boot().returncode == 0)
check("the default seeds none of the profile", not ({r[0] for r in rows("SELECT id FROM node")} & PROFILE_IDS))
pd = run_child(["-c", "import app, testseed\nfrom db import session\nwith session() as s:\n"
                "    testseed.build(s)\n    s.commit()\n"])
check("fixture data loaded on top of a default boot", pd.returncode == 0)
boot()  # settle task_id_seq, as verify_fresh_install.py §D does
pop = digest()
check("a boot with ATLAS_SEED=self on the populated database", boot({"ATLAS_SEED": "self"}).returncode == 0)
after = digest()
diff = sorted(k for k in set(pop) | set(after) if pop.get(k) != after.get(k))
check("leaves every row and sequence unchanged", not diff, f"changed: {diff[:6]}")

ENGINE.dispose()
print("=" * 78)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASSED")
