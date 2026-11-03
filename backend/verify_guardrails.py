"""
Verification for STORY-atlas-guardrails-per-env: agent guardrails per branch and environment,
as data (`guardrails.py`, `atlas-admin guardrails`).

The board's criteria, and the section that proves each:

  1. The matrix is data: an install defines its own environments and actions with no code
     change. Validation names every problem; a custom action is carried to agents and
     ignored by the exports; the setup guide's answers build the same matrix.          -> A
  2. The matrix is stored as intents and an ADR on the system node and returned by
     atlas_get: apply on a fresh ATLAS_SEED=none install writes the document, one
     constraint intent per environment and one Proposed ADR, each as a graph_event; the
     node read returns the intents (own) and its parts inherit them; re-applying is a
     no-op; a changed file updates and supersedes; no atlas-system is a refusal.     -> C, D
  3. The export produces agent policy files and branch-protection settings; a prod deploy
     is denied in the exported Claude Code policy and prod's branch needs a review.    -> B
  4. An install that never defines a matrix behaves as today: nothing is added, and a
     homelab-shaped database is byte-identical after every guardrails verb that reads. -> E

Every boot and every CLI call is its own process (settings are read at import).

Run:
    python verify_guardrails.py
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:
    print(f"FATAL: {e}")
    sys.exit(1)

from sqlalchemy import (Text, cast, create_engine, func, literal_column,  # noqa: E402
                        select, table, text)

import baseload_data as BL  # noqa: E402  (generated pure data: the intents the system set ships)
import guardrails as G  # noqa: E402
import distribution  # noqa: E402  (pure data: which copy, and its default seed profile)

ENGINE = create_engine(DB.url)
FLAGS = ("ATLAS_SEED", "ATLAS_LEGACY_V0", "ATLAS_RESEED", "ATLAS_PUBLIC_DOCS", "ATLAS_ADMIN_ACTOR")
BASE_ENV = {k: v for k, v in os.environ.items() if k not in FLAGS}
BASE_ENV["ATLAS_DB_URL"] = DB.url
BASE_ENV.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
BASE_ENV.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
BASE_ENV.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
BASE_ENV.setdefault("ATLAS_BREACH_CHECK", "bundled")
BASE_ENV.setdefault("ATLAS_FRONTEND_DIR", str(REPO / "frontend"))
NONE = {"ATLAS_SEED": "none", "ATLAS_LEGACY_V0": "0"}
ACTOR = "verify-guardrails"
TMP = Path(tempfile.mkdtemp(prefix="verify-guardrails-"))

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def run_child(args: list[str], env: dict | None = None, stdin: str | None = None,
              quiet_fail: bool = False) -> subprocess.CompletedProcess:
    child_env = dict(BASE_ENV)
    child_env.update(env or {})
    proc = subprocess.run([sys.executable, *args], cwd=HERE, env=child_env,
                          input=stdin if stdin is not None else "",
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 and not quiet_fail:
        print(f"  child {args[:3]!r} env={env} exited {proc.returncode}")
        print("  stdout: " + proc.stdout[-1500:].replace("\n", "\n  "))
        print("  stderr: " + proc.stderr[-1500:].replace("\n", "\n  "))
    return proc


def admin(*args: str, env: dict | None = None, quiet_fail: bool = False):
    return run_child(["admin.py", "--actor", ACTOR, "guardrails", *args], env, quiet_fail=quiet_fail)


def boot(env: dict | None = None) -> subprocess.CompletedProcess:
    return run_child(["-c", "import app; app.startup()"], env)


def reset() -> None:
    with ENGINE.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def data() -> dict:
    """(row count, sha256 of every row) per table, plus every sequence position."""
    out: dict = {}
    with ENGINE.connect() as conn:
        tables = sorted(r[0] for r in conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")))
        for t in tables:
            # Built with SQLAlchemy Core (the compiler quotes the table name) and hashed here:
            # the same digest idea as verify_system_node.data(), with no SQL string assembled.
            got = sorted(r for (r,) in conn.execute(
                select(cast(func.to_jsonb(literal_column("t")), Text))
                .select_from(table(t).alias("t"))))
            out[t] = (len(got), hashlib.sha256("\n".join(got).encode("utf-8")).hexdigest())
        for name, last in conn.execute(text(
                "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public'")):
            out[f"sequence:{name}"] = (-1, str(last))
    return out


def rows(sql: str, **params) -> list[tuple]:
    with ENGINE.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), params)]


def guardrail_rows() -> dict:
    # The rows `guardrails.apply` writes, exactly: its document path, its intent slugs, its ADR
    # slugs. Not every document whose path mentions guardrails: the baseload (STORY-oss-23) ships
    # product documents on `atlas-system-guardrails` and `atlas-system-cli-guardrails`, which are
    # not a matrix and are not applied.
    return {
        "docs": rows("SELECT node_id, path FROM node_doc WHERE path = :p", p=G.DOC_PATH),
        "intents": rows("SELECT node_id, slug FROM node_intent WHERE slug LIKE 'guardrail-%'"),
        "adrs": rows("SELECT adr_id FROM adr_record WHERE slug LIKE 'agent-guardrails-%'"),
    }


def write(name: str, matrix: dict) -> str:
    p = TMP / name
    p.write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    return str(p)


def errors_of(raw) -> list[str]:
    try:
        G.validate(raw)
    except G.MatrixError as exc:
        return exc.errors
    return []


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


def probe(env: dict, username: str, paths: list[str]) -> dict:
    proc = run_child(["-c", PROBE], env, stdin=json.dumps({"username": username, "paths": paths}))
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE ")), None)
    return json.loads(line[6:]) if line else {}


try:
    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("A. the matrix is data: format, validation, custom environments and actions")
    print("=" * 78)
    ex = G.example()
    names = [e["name"] for e in ex["environments"]]
    check("the shipped example validates: dev / stst / atst / preprod / prod, in order",
          names == ["dev", "stst", "atst", "preprod", "prod"], str(names))
    by = {e["name"]: e for e in ex["environments"]}
    check("example: dev agents branch, commit, push, open PRs and deploy",
          {"branch", "commit", "push", "open_pr", "deploy"} <= set(by["dev"]["agent"]))
    check("example: stst/atst agents read logs, propose, and deploy only through the pipeline",
          all({"read_logs", "propose", "deploy_via_pipeline"} <= set(by[n]["agent"])
              and "deploy" not in by[n]["agent"] for n in ("stst", "atst")))
    check("example: preprod agents only propose (and read logs); a human approves the deploy",
          set(by["preprod"]["agent"]) == {"read_logs", "propose"}
          and "approve_deploy" in by["preprod"]["human"])
    check("example: prod agents only read; a human deploys with step-up",
          by["prod"]["agent"] == ["read_logs"] and by["prod"]["step_up"] == ["deploy"]
          and by["prod"]["production"])
    check("the example sits in data/ and nothing in the backend applies it",
          not [p.name for p in HERE.glob("*.py")
               # baseload_data.py is generated data that applies nothing: it carries a copy of
               # the guardrails guide (docs/oss/guides/guardrails.md), which names the example.
               if p.name not in ("guardrails.py", "verify_guardrails.py", "admin.py", "baseload_data.py")
               and "example-default" in p.read_text(encoding="utf-8")])
    check("a YAML file and its JSON twin give the same matrix",
          G.load_file(write("twin.json", json.loads(json.dumps(ex)))) == ex)
    check("the digest is stable, and moves when one value moves",
          G.digest(G.example()) == G.digest(ex)
          and G.digest(dict(ex, name=ex["name"] + ".")) != G.digest(ex))

    answers = {"environments": names, "name": ex["name"],
               "branches": {"dev": ["dev", "feature/*"]}, "deploy_command": "tools/deploy.sh {env}*"}
    check("criterion 1: matrix_from_answers(the example's answers) equals the example file",
          G.matrix_from_answers(answers) == ex)
    small = G.matrix_from_answers({"environments": ["work", "live"]})
    check("matrix_from_answers with two names: the first deploys, the last is production",
          [e["name"] for e in small["environments"]] == ["work", "live"]
          and "deploy" in small["environments"][0]["agent"] and small["environments"][1]["production"])
    try:
        G.matrix_from_answers({"environments": ["a"], "production": "b", "colour": 1})
        bad_answers = []
    except G.MatrixError as exc:
        bad_answers = exc.errors
    check("matrix_from_answers refuses an unknown key and an unknown environment, naming both",
          any("colour" in e for e in bad_answers) and any("'b'" in e for e in bad_answers),
          str(bad_answers))

    def broken(mutate) -> list[str]:
        m = copy.deepcopy(json.loads(json.dumps(ex)))
        mutate(m)
        return errors_of(m)

    def env_of(m, n):
        return next(e for e in m["environments"] if e["name"] == n)

    cases = [
        ("an action outside the vocabulary (a typo) is refused, naming env and key",
         lambda m: env_of(m, "dev")["agent"].append("deploi"), "environments[dev].agent: 'deploi'"),
        ("an agent deploy in production is refused",
         lambda m: env_of(m, "prod")["agent"].append("deploy"), "never hold deploy rights"),
        ("an agent deploy through the pipeline in production is refused",
         lambda m: env_of(m, "prod")["agent"].append("deploy_via_pipeline"), "never hold deploy rights"),
        ("approve_deploy in any agent list is refused",
         lambda m: env_of(m, "stst")["agent"].append("approve_deploy"),
         "approve_deploy is a human act"),
        ("no production environment is refused",
         lambda m: env_of(m, "prod").pop("production"), "production: true"),
        ("an environment name used twice is refused",
         lambda m: m["environments"][1].update(name="dev"), "'dev' is used twice"),
        ("a branch claimed by two environments is refused",
         lambda m: env_of(m, "stst")["branches"].append("main"), "'main' is already"),
        ("step_up outside human is refused",
         lambda m: env_of(m, "preprod").update(step_up=["read_logs"]),
         "must also be in human"),
        ("an action both agent and human is refused",
         lambda m: env_of(m, "dev")["human"].append("push"), "in both agent and human"),
        ("an unknown version is refused", lambda m: m.update(version=2), "version: must be 1"),
        ("an unknown key is refused", lambda m: env_of(m, "dev").update(colour="red"), "unknown key"),
        ("zero reviews on protected production is refused",
         lambda m: env_of(m, "prod").update(required_reviews=0), "at least one human review"),
        ("a bad environment name is refused",
         lambda m: m["environments"][0].update(name="Dev Env"), "lowercase kebab-case"),
    ]
    for label, mutate, needle in cases:
        errs = broken(mutate)
        check(label, any(needle in e for e in errs), "; ".join(errs)[:220])
    errs = broken(lambda m: (env_of(m, "dev")["agent"].append("deploi"),
                             env_of(m, "prod")["agent"].append("deploy")))
    check("every problem is reported at once, not only the first", len(errs) >= 2, str(len(errs)))
    p = admin("validate", write("bad.json", json.loads(json.dumps(dict(ex, version=9)))), quiet_fail=True)
    check("`guardrails validate` on a bad file exits non-zero and names the problem",
          p.returncode == 1 and "version" in p.stderr, p.stderr.strip()[:160])
    p = admin("validate", G.EXAMPLE_FILE)
    check("`guardrails validate` on the example exits 0", p.returncode == 0 and "valid" in p.stdout)

    own = {"version": 1, "name": "An install of its own",
           "custom_actions": {"run_migration": "apply a database migration"},
           "environments": [
               {"name": "local", "branches": ["work/*"], "protected": False,
                "agent": ["branch", "commit", "push", "run_migration"]},
               {"name": "qa", "branches": ["qa"], "agent": ["read_logs", "propose"],
                "human": ["merge", "run_migration"]},
               {"name": "live", "branches": ["trunk"], "production": True, "agent": [],
                "human": ["merge", "deploy", "run_migration"], "step_up": ["deploy", "run_migration"],
                "required_reviews": 2, "required_checks": ["build", "test"]}]}
    om = G.validate(own)
    check("criterion 1: an install's own environments and a custom action validate, no code change",
          [e["name"] for e in om["environments"]] == ["local", "qa", "live"]
          and om["custom_actions"] == {"run_migration": "apply a database migration"})
    it = G.intent_for(om, om["environments"][2], "src")
    check("a custom action is carried into the intent agents read",
          "`run_migration` (with step-up)" in it["statement"], it["statement"][:160])
    oc = G.export_claude_code(om)
    og = G.export_github(om)
    check("the exports ignore the custom action",
          "run_migration" not in json.dumps(oc) and "run_migration" not in json.dumps(og))
    check("a custom action may not carry export commands",
          any("cannot carry commands" in e for e in errors_of(
              dict(own, environments=[dict(own["environments"][0], commands={"run_migration": ["x"]}),
                                      *own["environments"][1:]]))))
    check("an undeclared non-vocabulary action is still refused",
          any("'run_migration'" in e for e in errors_of(dict(own, custom_actions={}))))
    check("the custom install's own exports follow its own branches and checks",
          "Bash(git push * trunk)" in oc["permissions"]["deny"]
          and [b["branch"] for b in og["branches"]] == ["qa", "trunk"]
          and og["branches"][1]["body"]["required_status_checks"]["contexts"] == ["build", "test"]
          and og["branches"][1]["body"]["required_pull_request_reviews"]["required_approving_review_count"] == 2)

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("B. export: Claude Code permissions and GitHub branch protection")
    print("=" * 78)
    cc = G.export_claude_code(ex)
    deny, allow = cc["permissions"]["deny"], cc["permissions"]["allow"]
    check("criterion 3: the prod deploy command is DENIED in the Claude Code policy",
          "Bash(tools/deploy.sh prod*)" in deny and not any("prod" in a for a in allow), str(deny)[:200])
    check("pushes to prod's branch and force pushes are denied",
          "Bash(git push * main)" in deny and "Bash(git push --force*)" in deny)
    check("dev's branches (unprotected, agents push there) are not denied",
          not any(r.endswith(" dev)") or "feature/" in r for r in deny))
    check("merging is denied (agents merge in no environment of the example)",
          "Bash(gh pr merge*)" in deny)
    check("the fragment is a settings shape: only `permissions` with `allow` and `deny`",
          set(cc) == {"permissions"} and set(cc["permissions"]) == {"allow", "deny"})
    pert = copy.deepcopy(ex)
    env_of(pert, "preprod")["agent"].append("deploy")
    env_of(pert, "preprod")["human"].remove("deploy")
    pc = G.export_claude_code(G.validate(pert))
    check("the export can fail: with agents allowed to deploy to preprod, its command becomes "
          "an allow rule", "Bash(tools/deploy.sh preprod*)" in pc["permissions"]["allow"]
          and "Bash(tools/deploy.sh preprod*)" not in pc["permissions"]["deny"])
    gh = G.export_github(ex)
    prod_b = [b for b in gh["branches"] if b["environment"] == "prod"]
    body = prod_b[0]["body"] if prod_b else {}
    check("criterion 3: prod's branch requires a human review, no force push, admins included",
          len(prod_b) == 1 and prod_b[0]["branch"] == "main"
          and body["required_pull_request_reviews"]["required_approving_review_count"] >= 1
          and body["allow_force_pushes"] is False and body["enforce_admins"] is True)
    check("one entry per protected branch, none for unprotected dev",
          [b["branch"] for b in gh["branches"]] == ["stst", "atst", "preprod", "main"])
    check("an empty required-checks list is marked as the placeholder to fill",
          all("todo" in b for b in gh["branches"]))
    check("the endpoint is named for a plain branch, a wildcard is sent to a ruleset",
          prod_b[0]["apply_with"] == "PUT /repos/{owner}/{repo}/branches/main/protection"
          and [(b["branch"], b["wildcard"], "ruleset" in b["apply_with"]) for b in G.export_github(
              G.validate(dict(own, environments=[own["environments"][0], dict(
                  own["environments"][1], branches=["qa", "release/*"]), own["environments"][2]])),
              ["qa"])["branches"]] == [("qa", False, False), ("release/*", True, True)])
    only = G.export_claude_code(ex, ["dev"])
    check("--env dev: no protected branch, so no push or force-push denial",
          not any("git push" in r for r in only["permissions"]["deny"]), str(only))
    try:
        G.export(ex, "claude-code", ["nope"])
        named = False
    except G.GuardrailsError as exc:
        named = "nope" in str(exc)
    check("--env with an unknown environment is refused, naming it", named)
    out = TMP / "cc.json"
    p = admin("export", "--format", "claude-code", "--from", G.EXAMPLE_FILE, "--env", "prod",
              "--out", str(out))
    check("CLI: export --from FILE --env prod --out PATH writes the fragment",
          p.returncode == 0 and json.loads(out.read_text(encoding="utf-8")) == G.export_claude_code(ex, ["prod"]))
    p = admin("export", "--format", "github-branch-protection", "--from", G.EXAMPLE_FILE, "--out", "-")
    check("CLI: --out - writes the JSON to stdout", p.returncode == 0 and json.loads(p.stdout) == gh)

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("C. apply on a fresh ATLAS_SEED=none install, read back, re-apply, change")
    print("=" * 78)
    reset()
    check("fresh ATLAS_SEED=none boot", boot(NONE).returncode == 0)
    check("criterion 4: a fresh install has atlas-system and no guardrail rows",
          rows("SELECT id FROM node WHERE id = 'atlas-system'") == [("atlas-system",)]
          and guardrail_rows() == {"docs": [], "intents": [], "adrs": []}, str(guardrail_rows()))
    d0 = data()
    p = admin("apply", G.EXAMPLE_FILE, "--dry-run")
    check("--dry-run lists the changes and writes nothing",
          p.returncode == 0 and "would: create intent guardrail-prod" in p.stdout and data() == d0,
          p.stdout.strip()[:200])
    p = admin("apply", G.EXAMPLE_FILE)
    check("apply exits 0", p.returncode == 0, p.stdout.strip()[:200])
    gr = guardrail_rows()
    check("criterion 2: the matrix document is stored on atlas-system",
          gr["docs"] == [("atlas-system", G.DOC_PATH)])
    attrs = rows("SELECT attributes FROM node WHERE id = 'atlas-system'")[0][0]
    check("... and listed in its docs[] (so the read route serves it)",
          any(d.get("path") == G.DOC_PATH for d in attrs.get("docs", [])))
    # atlas-system also carries the product intents the baseload ships (STORY-oss-23); apply
    # adds exactly one constraint per environment beside them and changes none of them.
    shipped = sorted((i["slug"], i["kind"]) for i in BL.INTENTS if i["node_id"] == "atlas-system")
    check("criterion 2: one constraint intent per environment, slugs guardrail-<env>",
          sorted(rows("SELECT slug, kind FROM node_intent WHERE node_id = 'atlas-system'"))
          == sorted([(f"guardrail-{n}", "constraint") for n in names] + shipped))
    src = rows("SELECT source FROM node_intent WHERE slug = 'guardrail-prod'")[0][0]
    check("each intent's source names the matrix version, digest and file",
          src == G.source_label(ex, G.EXAMPLE_FILE) and "example-default.yaml" in src, src)
    adrs = rows("SELECT adr_id, status, source, supersedes, project_node_id FROM adr_record")
    check("criterion 2: exactly one ADR, Proposed, Atlas-native, on atlas-system",
          adrs == [("SYS001", "proposed", "atlas", [], "atlas-system")], str(adrs))
    ev = rows("SELECT action, actor_kind, detail->>'operator', detail->>'via', entity_id "
              "FROM graph_event ORDER BY id")
    acts = sorted(a for a, *_ in ev)
    check("every change is a graph_event through the one writer: doc, docs[], five intents, the ADR",
          acts == sorted(["node.doc.write", "node.update", "node.adr.write"]
                         + ["node.intent.create"] * 5), str(acts))
    check("... each naming the operator and the verb, on atlas-system, as the system actor",
          all(k == "system" and o == ACTOR and v == "atlas-admin guardrails apply"
              and n == "atlas-system" for _, k, o, v, n in ev))
    child = rows("SELECT dst FROM edge WHERE src = 'atlas-system' AND edge_type = 'contains' LIMIT 1")[0][0]
    r = probe(NONE, "probe-owner", ["/api/node/atlas-system", f"/api/node/{child}",
                                    f"/api/node/atlas-system/doc?path={G.DOC_PATH}",
                                    "/api/adrs/SYS001"])
    node = r.get("/api/node/atlas-system", [0, {}])
    own_slugs = [i["slug"] for i in (node[1].get("intents", {}).get("own", []) if node[0] == 200 else [])]
    check("criterion 2: GET /api/node/atlas-system (what atlas_get reads) returns the intents as own",
          sorted(own_slugs) == sorted([f"guardrail-{n}" for n in names] + [s for s, _ in shipped]),
          str(own_slugs))
    kid = r.get(f"/api/node/{child}", [0, {}])
    inh = [i["slug"] for i in (kid[1].get("intents", {}).get("inherited", []) if kid[0] == 200 else [])]
    check(f"criterion 2: a part ({child}) inherits every guardrail intent",
          sorted(i for i in inh if i.startswith("guardrail-")) == sorted(f"guardrail-{n}" for n in names),
          str(inh))
    prod_txt = next((i["statement"] for i in node[1].get("intents", {}).get("own", [])
                     if i["slug"] == "guardrail-prod"), "") if node[0] == 200 else ""
    check("the prod intent tells an agent it may only read and never deploys",
          "**An agent may:** `read_logs`." in prod_txt and "never hold deploy rights" in prod_txt)
    doc = r.get(f"/api/node/atlas-system/doc?path={G.DOC_PATH}", [0, {}])
    check("the document is served through the read route (atlas_doc_read's)",
          doc[0] == 200 and G.matrix_from_doc(doc[1].get("content", "")) == ex)
    adr = r.get("/api/adrs/SYS001", [0, {}])
    check("the ADR is served by the registry, Proposed", adr[0] == 200
          and "proposed" in json.dumps(adr[1]).lower(), str(adr[0]))

    p = admin("show")
    check("`guardrails show` prints the stored matrix", p.returncode == 0 and json.loads(p.stdout) == ex)
    p = admin("export", "--format", "claude-code", "--out", "-")
    check("export from the stored matrix equals export from the file",
          p.returncode == 0 and json.loads(p.stdout) == cc)

    d1 = data()
    p = admin("apply", G.EXAMPLE_FILE)
    check("re-applying the same file changes nothing: no row, no event",
          p.returncode == 0 and "nothing to do" in p.stdout and data() == d1, p.stdout.strip()[:160])

    changed = copy.deepcopy(ex)
    changed["environments"] = [e for e in changed["environments"] if e["name"] != "atst"]
    env_of(changed, "prod")["required_reviews"] = 2
    cf = write("changed.json", changed)
    p = admin("apply", cf)
    check("a changed file applies", p.returncode == 0, p.stdout.strip()[:300])
    check("the document now holds the changed matrix",
          G.matrix_from_doc(rows("SELECT content FROM node_doc WHERE path = :p", p=G.DOC_PATH)[0][0])
          == G.validate(changed))
    check("the removed environment's intent is gone, the others remain",
          sorted(s for _, s in guardrail_rows()["intents"])
          == sorted(f"guardrail-{n}" for n in names if n != "atst"))
    check("prod's intent now states two reviews",
          "2 human review(s)" in rows("SELECT statement FROM node_intent WHERE slug = 'guardrail-prod'")[0][0])
    adrs = {a: (s, sup, sb) for a, s, sup, sb in rows(
        "SELECT adr_id, status, supersedes, superseded_by FROM adr_record")}
    check("a new Proposed ADR supersedes the previous one, which is marked superseded",
          adrs == {"SYS001": ("superseded", [], ["SYS002"]), "SYS002": ("proposed", ["SYS001"], [])},
          str(adrs))
    pos = [r[0] for r in rows("SELECT position FROM node_intent WHERE node_id = 'atlas-system' "
                              "ORDER BY position")]
    check("intent positions stay dense", pos == list(range(len(pos))), str(pos))
    d2 = data()
    p = admin("apply", cf)
    check("re-applying the changed file changes nothing", "nothing to do" in p.stdout and data() == d2)

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("D. no atlas-system: apply refuses and says how to add it")
    print("=" * 78)
    reset()
    if distribution.DEFAULT_SEED_PROFILE == "homelab":
        boot()  # the homelab default profile: no system node set
    else:
        # STORY-oss-23 A3b: every profile this copy offers seeds the system node set on a fresh
        # graph, so the install this section needs (one made before the set existed) is a boot
        # with the set's seeding stubbed out in that one child; startup is otherwise real.
        run_child(["-c", "import system_node; system_node.seed = lambda s: []; import app; app.startup()"])
    check("the default profile has no atlas-system", not rows("SELECT id FROM node WHERE id = 'atlas-system'"))
    d3 = data()
    p = admin("apply", G.EXAMPLE_FILE, quiet_fail=True)
    check("apply exits 1, naming `system_node.py --apply`",
          p.returncode == 1 and "system_node.py --apply" in p.stderr, p.stderr.strip()[:200])
    check("... and writes nothing", data() == d3)

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("E. an install that never defines a matrix behaves as today")
    print("=" * 78)
    reset()
    boot()
    seed_hl = "\n".join([
        "import app, testseed",
        "from db import session",
        "from models_graph import Node, Edge",
        "from attention import recompute_derived",
        "with session() as s:",
        "    testseed.build(s)",
        "    for nid, t in [('atlas', 'project'), ('atlas-api', 'service'), ('atlas-mcp', 'service')]:",
        "        s.add(Node(id=nid, type=t, name=nid, attributes={}, body='homelab', source=None, origin='atlas'))",
        "    s.flush()",
        "    for nid in ('atlas-api', 'atlas-mcp'):",
        "        s.add(Edge(src='atlas', dst=nid, edge_type='contains', origin='atlas'))",
        "    s.flush()",
        "    recompute_derived(s)",
        "    s.commit()",
    ])
    check("homelab-shaped database built", run_child(["-c", seed_hl]).returncode == 0)
    boot()
    d_hl = data()
    for verb in (["validate", G.EXAMPLE_FILE],
                 ["export", "--format", "claude-code", "--from", G.EXAMPLE_FILE, "--out", "-"],
                 ["apply", G.EXAMPLE_FILE, "--dry-run"]):
        admin(*verb, quiet_fail=True)
    p_show = admin("show", quiet_fail=True)
    p_exp = admin("export", "--format", "claude-code", "--out", "-", quiet_fail=True)
    check("with no matrix applied, `show` and `export` (no --from) refuse, saying none is applied",
          p_show.returncode == 1 and p_exp.returncode == 1 and "no guardrails matrix" in p_exp.stderr)
    boot()
    check("criterion 4: the homelab-shaped database is byte-identical after boots and every "
          "read-only guardrails verb", data() == d_hl)
    check("criterion 4: no guardrail document, intent or ADR exists",
          guardrail_rows() == {"docs": [], "intents": [], "adrs": []})
    homelab = rows("SELECT (to_jsonb(n))::text FROM node n WHERE id NOT LIKE 'atlas-system%' ORDER BY id")
    # The install's own intents: everything not on the system set. Under the homelab copy no
    # system node exists yet here, so this is every intent, as before; under a copy whose boot
    # seeds the set, its shipped intents are already there and are not the install's own.
    homelab_int = rows("SELECT (to_jsonb(i))::text FROM node_intent i WHERE node_id NOT LIKE 'atlas-system%' "
                       "ORDER BY id")
    run_child(["system_node.py", "--apply"])
    p = admin("apply", G.EXAMPLE_FILE)
    check("opted in (system node set added, matrix applied), the homelab's own nodes and intents "
          "are untouched", p.returncode == 0
          and rows("SELECT (to_jsonb(n))::text FROM node n WHERE id NOT LIKE 'atlas-system%' ORDER BY id") == homelab
          and rows("SELECT (to_jsonb(i))::text FROM node_intent i WHERE node_id NOT LIKE 'atlas-system%' "
                   "ORDER BY id") == homelab_int)
    check("nothing is written on the homelab `atlas` node",
          not rows("SELECT 1 FROM node_intent WHERE node_id = 'atlas'")
          and not rows("SELECT 1 FROM adr_record WHERE project_node_id = 'atlas'"))
finally:
    shutil.rmtree(TMP, ignore_errors=True)
    ENGINE.dispose()

print("=" * 78)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASSED")
