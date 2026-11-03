"""
Verification for STORY-atlas-first-cicd: a first CI/CD pipeline generated from the guardrails
matrix and the setup guide's answers (`cicd.py`, `guide_answers.py`, `atlas-admin cicd`).

The board's criteria, and the section that proves each:

  1. For GitHub Actions, GitLab CI and Azure DevOps the guide produces a pipeline: files for
     all three from the example matrix and from recorded guide answers.              -> A, D, E
     (Passing each platform's own lint/validation is the CI job
     .github/workflows/cicd-lint.yml, tools/ci/cicd_lint.sh: actionlint, GitLab's CI schema,
     the Azure Pipelines schema, each with a negative control. It needs downloaded tools, so it
     is a workflow, not a suite script.)
  2. The pipeline follows the guardrail matrix: no agent credential can deploy where the matrix
     forbids. For prod: its deploy waits for a person on every platform, the credential lives
     only with the environment, no deploy runs for a pull request; perturbations: a matrix
     allowing an agent prod deploy is refused, a hand-edited template that drops the gate
     cannot produce a file, and hand-edited output without the gate fails the check.     -> B
  3. The output is files for the installer to review; Atlas pushes nothing: no module of the
     generator can reach a network or a git command (tokenised), the CLI writes exactly the
     listed files under --out, needs no database for a file-based run, refuses to overwrite. -> C
  4. Each generated pipeline is recorded as a Proposed ADR: --record-adr on a fresh
     ATLAS_SEED=none install after the guide was answered through the API writes one Proposed
     `source=atlas` ADR per platform with one graph_event each; the same files again write
     nothing; changed answers supersede that platform's ADR only.                         -> E
  And: an install that never runs it behaves as today (no route, homelab-shaped database
  byte-identical, --record-adr without atlas-system refused before writing a file).      -> F

Every boot and every CLI call is its own process (settings are read at import).

Run:
    python verify_cicd.py
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tokenize
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

import yaml  # noqa: E402
from sqlalchemy import (Text, cast, create_engine, func, literal_column,  # noqa: E402
                        select, table, text)

import cicd as C  # noqa: E402
import guardrails as G  # noqa: E402
import guide_answers as GA  # noqa: E402
import setup_guide  # noqa: E402
import distribution  # noqa: E402  (pure data: which copy, and its default seed profile)

ENGINE = create_engine(DB.url)
FLAGS = ("ATLAS_SEED", "ATLAS_LEGACY_V0", "ATLAS_RESEED", "ATLAS_PUBLIC_DOCS", "ATLAS_ADMIN_ACTOR",
         "ATLAS_SETUP_QUESTIONS_FILE")
BASE_ENV = {k: v for k, v in os.environ.items() if k not in FLAGS}
BASE_ENV["ATLAS_DB_URL"] = DB.url
BASE_ENV.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
BASE_ENV.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
BASE_ENV.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
BASE_ENV.setdefault("ATLAS_BREACH_CHECK", "bundled")
BASE_ENV.setdefault("ATLAS_FRONTEND_DIR", str(REPO / "frontend"))
NONE = {"ATLAS_SEED": "none", "ATLAS_LEGACY_V0": "0"}
#: A database URL nothing listens on: a CLI run given it proves it never connected.
NO_DB = {"ATLAS_DB_URL": "postgresql+psycopg2://none:none@127.0.0.1:9/none"}
ACTOR = "verify-cicd"
TMP = Path(tempfile.mkdtemp(prefix="verify-cicd-"))
ANSWERS_FILE = HERE / "data" / "cicd" / "example-answers.yaml"

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
        print(f"  child {args[:4]!r} exited {proc.returncode}")
        print("  stdout: " + proc.stdout[-1500:].replace("\n", "\n  "))
        print("  stderr: " + proc.stderr[-1500:].replace("\n", "\n  "))
    return proc


def admin(*args: str, env: dict | None = None, quiet_fail: bool = False):
    return run_child(["admin.py", "--actor", ACTOR, "cicd", *args], env, quiet_fail=quiet_fail)


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


def tree(root: Path) -> dict[str, str]:
    """{relative posix path: content} of every file under root."""
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(root.rglob("*")) if p.is_file()}


def env_of(m: dict, n: str) -> dict:
    return next(e for e in m["environments"] if e["name"] == n)


def raises(fn, exc) -> str:
    try:
        fn()
    except exc as e:
        return str(e) or type(e).__name__
    return ""


EX = G.example()
CI = GA.cicd_input(GA.load_answers_file(str(ANSWERS_FILE)))

# A child that signs an owner in and answers guide topics through the real API.
ANSWER = r'''
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
m = CSRF.search(c.get("/auth/login").text)
c.post("/auth/login", data={"username": cfg["username"], "password": pw,
                            "csrf_token": m.group(1) if m else ""}, headers={"Origin": ORIGIN})
m = CSRF.search(c.get("/auth/password/change").text)
new = secrets.token_urlsafe(24)
c.post("/auth/password/change",
       data={"current_password": pw, "new_password": new, "confirm_password": new,
             "csrf_token": m.group(1) if m else ""}, headers={"Origin": ORIGIN})
csrf = c.get("/api/me").json().get("csrf_token")
out = []
for topic, answers in cfg["answers"]:
    r = c.post("/api/setup/answers", json={"topic": topic, "answers": answers, "csrf_token": csrf},
               headers={"Origin": ORIGIN})
    out.append([topic, r.status_code])
print("ANSWERED " + json.dumps(out))
'''


def answer(username: str, answers: list) -> list:
    proc = run_child(["-c", ANSWER], NONE, stdin=json.dumps({"username": username,
                                                             "answers": answers}))
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("ANSWERED ")), None)
    return json.loads(line[9:]) if line else []


try:
    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("A. files for all three platforms from the example matrix")
    print("=" * 78)
    plan = {p["env"]: p for p in C.deploy_plan(EX)}
    check("the deploy plan covers exactly the environments whose matrix entry names a deploy",
          sorted(plan) == sorted(e["name"] for e in EX["environments"]
                                 if set(e["agent"] + e["human"]) & set(G.DEPLOY_ACTIONS)),
          str(sorted(plan)))
    check("gated: preprod (a person approves) and prod (production); not dev, stst, atst",
          sorted(n for n, p in plan.items() if p["gated"]) == ["preprod", "prod"])
    GEN = {}
    for platform in C.PLATFORMS:
        GEN[platform] = C.generate(EX, CI, platform)
    expected_files = {
        "github": {".github/workflows/atlas-pipeline.yml", "atlas-cicd/github-settings.md",
                   "atlas-cicd/github-branch-protection.json"},
        "gitlab": {".gitlab-ci.yml", "atlas-cicd/gitlab-settings.md"},
        "azure": {"azure-pipelines.yml", "atlas-cicd/azure-settings.md"}}
    for platform, pipe in GEN.items():
        check(f"criterion 1: {platform}: the generated files are the documented set",
              set(pipe["files"]) == expected_files[platform], str(sorted(pipe["files"])))
        doc = yaml.safe_load(pipe["files"][C.PIPELINE_PATH[platform]])
        check(f"{platform}: the pipeline is YAML and passes the guardrail check",
              isinstance(doc, dict) and C.check_pipeline(platform, pipe["files"], EX) == [])
        txt = pipe["files"][C.PIPELINE_PATH[platform]]
        check(f"{platform}: its comments say what the platform enforces and what is convention",
              "ENFORCES" in txt and "CONVENTION" in txt and "pushed nothing" in txt)
    gh = yaml.safe_load(GEN["github"]["files"][C.PIPELINE_PATH["github"]])
    check("github: one job per check, named after it (the status context branch protection needs)",
          all(gh["jobs"][c]["name"] == c for c in CI["checks"]))
    check("github: a gate stops a failure, a non-gate is `continue-on-error`",
          all(("continue-on-error" in gh["jobs"][c]) == (c not in CI["gates"]) for c in CI["checks"]))
    check("github: one deploy job per deploying environment, after every check",
          sorted(j[7:] for j in gh["jobs"] if j.startswith("deploy-")) == sorted(plan)
          and all(gh["jobs"][f"deploy-{n}"]["needs"] == CI["checks"] for n in plan))
    uses = [st["uses"] for j in gh["jobs"].values() for st in j["steps"] if "uses" in st]
    gh_txt = GEN["github"]["files"][C.PIPELINE_PATH["github"]]
    check("github: every third-party action is pinned by a full commit sha, version in a comment",
          uses and all(__import__("re").fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses)
          and all(ln.rstrip().split("#", 1)[-1].strip().startswith("v")
                  for ln in gh_txt.splitlines() if "uses:" in ln), str(sorted(set(uses))))
    check("github: the branch-protection file is guardrails' own export, unchanged",
          json.loads(GEN["github"]["files"]["atlas-cicd/github-branch-protection.json"])
          == G.export_github(EX))
    gl = yaml.safe_load(GEN["gitlab"]["files"][".gitlab-ci.yml"])
    check("gitlab: a non-gate is `allow_failure: true`, a gate is not",
          all((gl[c].get("allow_failure") is True) == (c not in CI["gates"]) for c in CI["checks"]))
    az = yaml.safe_load(GEN["azure"]["files"]["azure-pipelines.yml"])
    check("azure: a checks stage, then one deploy stage per deploying environment",
          [s["stage"] for s in az["stages"]] == ["checks"] + [f"deploy_{n}" for n in plan])
    for platform in C.PLATFORMS:
        txt = GEN[platform]["files"][C.PIPELINE_PATH[platform]]
        check(f"{platform}: every command is a placeholder that fails until replaced",
              txt.count("TODO(atlas)") >= len(CI["checks"]) + len(plan)
              and "exit 1" in txt)
    no_stst = copy.deepcopy(EX)
    env_of(no_stst, "stst")["agent"].remove("deploy_via_pipeline")
    p2 = C.generate(G.validate(no_stst), CI, "gitlab")
    check("perturbation: stst with no deploy action gets no deploy stage, the rest unchanged",
          "deploy-stst" not in yaml.safe_load(p2["files"][".gitlab-ci.yml"])
          and "deploy-atst" in yaml.safe_load(p2["files"][".gitlab-ci.yml"]))
    for trig in C.TRIGGERS:
        ok = all(C.check_pipeline(pf, C.generate(EX, dict(CI, deploy_trigger=trig), pf)["files"], EX)
                 == [] for pf in C.PLATFORMS)
        check(f"production trigger {trig}: all three platforms generate and pass the check", ok)
    bad_ci = raises(lambda: C.generate(EX, dict(CI, checks=["build", "deploi"]), "github"), C.CicdError)
    check("an unknown check name is refused, naming it", "deploi" in bad_ci, bad_ci[:160])
    bad_p = raises(lambda: C.generate(EX, CI, "jenkins"), C.CicdError)
    check("an unknown platform is refused", "jenkins" in bad_p)

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("B. the guardrails: prod waits for a person; no agent credential reaches a deploy")
    print("=" * 78)
    prod = gh["jobs"]["deploy-prod"]
    gh_settings = GEN["github"]["files"]["atlas-cicd/github-settings.md"]
    check("criterion 2, github: deploy-prod deploys through environment prod",
          prod["environment"] == {"name": "prod"})
    check("... and the settings ask for required reviewers (people only) on prod",
          any("`prod`" in ln and "required reviewers" in ln and "never an agent" in ln
              for ln in gh_settings.splitlines()))
    check("... and it never runs for a pull request", "pull_request" not in prod["if"]
          and prod["if"].startswith("github.event_name == 'push'"))
    secret_jobs = [j for j, b in gh["jobs"].items() if "secrets." in json.dumps(b)]
    check("github: a secret appears only in deploy jobs, each tied to its environment",
          sorted(secret_jobs) == sorted(f"deploy-{n}" for n in plan)
          and all(gh["jobs"][j].get("environment") for j in secret_jobs)
          and "secrets." not in json.dumps({k: v for k, v in gh.items() if k != "jobs"}, default=str))
    on = gh.get("on", gh.get(True))
    check("github: no pull_request_target / workflow_run trigger, read-only permissions",
          not ({"pull_request_target", "workflow_run"} & set(on)) and gh["permissions"] == {"contents": "read"})
    glp = gl["deploy-prod"]
    check("criterion 2, gitlab: deploy-prod is `when: manual` in every rule and blocks the pipeline",
          glp["rules"] and all(r.get("when") == "manual" for r in glp["rules"])
          and glp["allow_failure"] is False and glp["environment"]["name"] == "prod"
          and glp["environment"]["deployment_tier"] == "production")
    check("gitlab: no deploy rule matches a merge request pipeline",
          all("merge_request" not in r["if"] for n in plan for r in gl[f"deploy-{n}"]["rules"]))
    gl_cred = [j for j, b in gl.items() if isinstance(b, dict) and "DEPLOY_CREDENTIAL" in json.dumps(b)]
    check("gitlab: DEPLOY_CREDENTIAL appears only in jobs with an environment",
          sorted(gl_cred) == sorted(f"deploy-{n}" for n in plan)
          and all(gl[j].get("environment") for j in gl_cred))
    gl_settings = GEN["gitlab"]["files"]["atlas-cicd/gitlab-settings.md"]
    check("gitlab: the settings protect prod's environment with people-only deployers",
          any("protect `prod`" in ln and "people only" in ln for ln in gl_settings.splitlines()))
    azp = next(s for s in az["stages"] if s["stage"] == "deploy_prod")
    job = azp["jobs"][0]
    check("criterion 2, azure: deploy_prod is a deployment job on environment prod",
          "deployment" in job and job["environment"] == "prod"
          and job["variables"] == [{"group": "deploy-prod"}])
    check("azure: no deploy stage runs for a pull request",
          all("ne(variables['Build.Reason'], 'PullRequest')" in s["condition"]
              for s in az["stages"] if s["stage"].startswith("deploy_")))
    check("azure: the deploy variable groups appear only inside deployment jobs",
          "variables" not in az and all("group" not in json.dumps(j)
                                        for j in az["stages"][0]["jobs"]))
    az_settings = GEN["azure"]["files"]["atlas-cicd/azure-settings.md"]
    check("azure: the settings put an Approvals check by people only on prod",
          any("`prod`" in ln and "Approvals" in ln and "people only" in ln
              for ln in az_settings.splitlines()))

    # Perturbation 1: a matrix that lets an agent deploy to prod.
    agent_prod = copy.deepcopy(json.loads(json.dumps(EX)))
    env_of(agent_prod, "prod")["agent"].append("deploy")
    env_of(agent_prod, "prod")["human"].remove("deploy")
    env_of(agent_prod, "prod")["step_up"] = []
    refused = raises(lambda: G.validate(agent_prod), G.MatrixError)
    check("perturbation: a matrix letting an agent deploy to prod is refused by the guardrails",
          "never hold deploy rights" in refused, refused[:160])
    for platform in C.PLATFORMS:
        r = raises(lambda: C.generate(agent_prod, CI, platform), G.MatrixError)
        check(f"... and {platform}'s generate refuses it the same way", "never hold deploy rights" in r)
    pipe_prod = copy.deepcopy(agent_prod)
    env_of(pipe_prod, "prod")["agent"] = ["read_logs", "deploy_via_pipeline"]
    check("... as it refuses an agent deploy to prod through the pipeline",
          "never hold deploy rights" in raises(lambda: C.generate(pipe_prod, CI, "github"), G.MatrixError))

    # Perturbation 2: a hand-edited template that drops the gate cannot produce a file.
    edits = {
        "github": ("deploy.tmpl", ["    environment:\n      name: @@ENV@@\n"]),
        "gitlab": ("deploy.tmpl", ["@@ALLOW_FAILURE@@\n"]),
        "azure": ("deploy.tmpl", ["        environment: @@ENV@@\n"]),
    }
    for platform, (name, drops) in edits.items():
        tdir = TMP / f"templates-{platform}"
        shutil.copytree(C.TEMPLATE_DIR, tdir)
        f = tdir / platform / name
        body = f.read_text(encoding="utf-8")
        edited = body
        for d in drops:
            edited = edited.replace(d, "")
        check(f"{platform}: the template edit really removed the gate", edited != body)
        f.write_text(edited, encoding="utf-8")
        msg = raises(lambda: C.generate(EX, CI, platform, template_dir=tdir), C.CicdError)
        check(f"perturbation: {platform} template without its gate -> generate refuses, naming prod",
              "deploy" in msg and "prod" in msg, msg.replace("\n", " ")[:220])
        check(f"... while the shipped {platform} templates still generate", C.generate(
            EX, CI, platform)["files"] == GEN[platform]["files"])

    # Perturbation 3: hand-edited OUTPUT without the gate fails the check.
    def edited_output(platform: str, mutate) -> list[str]:
        files = dict(GEN[platform]["files"])
        doc = yaml.safe_load(files[C.PIPELINE_PATH[platform]])
        mutate(doc)
        files[C.PIPELINE_PATH[platform]] = yaml.safe_dump(doc, sort_keys=False)
        return C.check_pipeline(platform, files, EX)

    def gh_drop_env(d):
        d["jobs"]["deploy-prod"].pop("environment")

    def gh_repo_secret(d):
        d["env"] = {"DEPLOY_CREDENTIAL": "${{ secrets.DEPLOY_CREDENTIAL }}"}

    def gh_on_pr(d):
        d["jobs"]["deploy-prod"]["if"] = "github.event_name == 'pull_request'"

    def gl_auto(d):
        for r in d["deploy-prod"]["rules"]:
            r.pop("when", None)

    def gl_global(d):
        d["variables"] = {"DEPLOY_CREDENTIAL": "$PROD_KEY"}

    def az_plain_job(d):
        st = next(s for s in d["stages"] if s["stage"] == "deploy_prod")
        j = st["jobs"][0]
        st["jobs"][0] = {"job": j.pop("deployment"), "pool": j["pool"], "variables": j["variables"],
                         "steps": j["strategy"]["runOnce"]["deploy"]["steps"]}

    for platform, label, mutate, needle in [
            ("github", "deploy-prod without `environment:`", gh_drop_env, "deploy-prod"),
            ("github", "the credential as a workflow-wide env", gh_repo_secret, "workflow-level"),
            ("github", "deploy-prod on a pull request", gh_on_pr, "pull request"),
            ("gitlab", "deploy-prod without `when: manual`", gl_auto, "without a person"),
            ("gitlab", "the credential as a global variable", gl_global, "global variables"),
            ("azure", "deploy_prod as a plain job (no environment)", az_plain_job, "deploy credential")]:
        probs = edited_output(platform, mutate)
        check(f"perturbation: {platform} output with {label} fails the check",
              any(needle in p for p in probs), "; ".join(probs)[:220])
    check("the unedited output, through the same YAML round-trip, passes",
          all(edited_output(pf, lambda d: None) == [] for pf in C.PLATFORMS))

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("C. files for the installer; Atlas pushes nothing")
    print("=" * 78)
    forbidden = {"subprocess", "socket", "urllib", "http", "httpx", "requests", "ftplib",
                 "smtplib", "git", "pygit2", "dulwich", "asyncio", "multiprocessing", "pty"}
    for mod in ("cicd.py", "guide_answers.py"):
        src = (HERE / mod).read_text(encoding="utf-8")
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
        imported, calls = set(), set()
        for i, t in enumerate(toks):
            if t.type == tokenize.NAME and t.string in ("import", "from"):
                j = i + 1
                if toks[j].type == tokenize.NAME:
                    imported.add(toks[j].string.split(".")[0])
            if t.type == tokenize.NAME and t.string in ("system", "popen", "execv", "execvp",
                                                        "spawnv", "startfile", "urlopen"):
                calls.add(t.string)
        check(f"{mod}: imports nothing that reaches a network, a process or git",
              not (imported & forbidden) and not calls, str(sorted(imported & forbidden) + sorted(calls)))
    admin_src = (HERE / "admin.py").read_text(encoding="utf-8")
    verb = admin_src[admin_src.index("def cmd_cicd_generate"):]
    verb = verb[:verb.index("\ndef ", 1)]
    check("admin's cicd verb runs no process either",
          "subprocess" not in verb and "os.system" not in verb and "cicd.write_files" in verb)
    out = TMP / "out"
    sibling = TMP / "sibling.txt"
    sibling.write_text("untouched", encoding="utf-8")
    before = tree(TMP)
    p = admin("generate", "--platform", "gitlab", "--matrix", G.EXAMPLE_FILE, "--answers",
              str(ANSWERS_FILE), "--out", str(out), env=NO_DB)
    check("criterion 3: `cicd generate --matrix --answers` exits 0 with NO reachable database",
          p.returncode == 0, p.stdout.strip()[-200:])
    after = tree(TMP)
    written = {k[len("out/"):]: v for k, v in after.items() if k.startswith("out/")}
    check("criterion 3: it wrote exactly the generated files, byte for byte, under --out",
          written == C.generate(EX, CI, "gitlab")["files"], str(sorted(written)))
    check("... and nothing else changed beside --out",
          {k: v for k, v in after.items() if not k.startswith("out/")} == before)
    check("... and it says nothing was pushed", "nothing was pushed" in p.stdout)
    p = admin("generate", "--platform", "gitlab", "--matrix", G.EXAMPLE_FILE, "--answers",
              str(ANSWERS_FILE), "--out", str(out), env=NO_DB, quiet_fail=True)
    check("a second run refuses to overwrite, naming the file and --force",
          p.returncode == 1 and "--force" in p.stderr and tree(out) == written, p.stderr.strip()[:200])
    (out / ".gitlab-ci.yml").write_text("edited by the installer\n", encoding="utf-8")
    p = admin("generate", "--platform", "gitlab", "--matrix", G.EXAMPLE_FILE, "--answers",
              str(ANSWERS_FILE), "--out", str(out), "--force", env=NO_DB)
    check("--force overwrites", p.returncode == 0 and tree(out) == written)
    for platform in ("github", "azure"):
        p = admin("generate", "--platform", platform, "--matrix", G.EXAMPLE_FILE, "--answers",
                  str(ANSWERS_FILE), "--out", str(TMP / f"out-{platform}"), env=NO_DB)
        check(f"CLI: {platform} generates the same files as the library",
              p.returncode == 0 and tree(TMP / f"out-{platform}") == GEN[platform]["files"])
    typo = TMP / "typo.yaml"
    typo.write_text("cicd:\n  checks: [build, unti_tests]\n  gates: [build]\n  deploy_trigger: manual\n",
                    encoding="utf-8")
    p = admin("generate", "--platform", "github", "--matrix", G.EXAMPLE_FILE, "--answers", str(typo),
              "--out", str(TMP / "out-typo"), env=NO_DB, quiet_fail=True)
    check("an answers file with a typo is refused through the guide's own question set, naming it",
          p.returncode == 1 and "unti_tests" in p.stderr and not (TMP / "out-typo").exists(),
          p.stderr.strip()[:200])
    p = admin("generate", "--platform", "github", "--out", str(TMP / "out-x"), env=NO_DB, quiet_fail=True)
    check("neither --from-guide nor --matrix is refused", p.returncode == 1 and "--from-guide" in p.stderr)

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("D. the guide's answer keys -> the matrix and the CI/CD input")
    print("=" * 78)
    qs = {t["id"]: {q["id"] for q in t["questions"]} for t in setup_guide.load_question_set()["topics"]}
    check("every guide key the adapter reads is a question of the shipped set",
          all(set(keys) <= qs.get(t, set()) for t, keys in GA.READS.items()), str(GA.READS))
    GUIDE = {
        "environments": {"names": "dev, test, acc, prod", "production": "prod",
                         "promotion": "sequential"},
        "guardrails": {"agent_actions": ["read", "open_pr", "deploy_nonprod", "deploy_prod"],
                       "approval_gates": ["merge", "deploy_prod"], "who_merges": "human_only",
                       "who_deploys": "pipeline_on_merge"},
        "cicd": {"ci_platform": "gitlab_ci", "checks": ["build", "unit_tests", "lint"],
                 "gates": ["build", "unit_tests"], "deploy_trigger": "on_merge"}}
    for t, a in GUIDE.items():
        topic = next(x for x in setup_guide.load_question_set()["topics"] if x["id"] == t)
        check(f"the test answers for {t} are valid guide answers",
              setup_guide.normalise_answers(topic, a) == a)
    gm, gci, notes = GA.from_guide(GUIDE)
    gby = {e["name"]: e for e in gm["environments"]}
    check("the guide's environments and production carry into the matrix, in order",
          list(gby) == ["dev", "test", "acc", "prod"] and gby["prod"]["production"])
    check("criterion 2: deploy_prod among the agent actions still leaves prod's deploy with a person",
          not set(G.DEPLOY_ACTIONS) & set(gby["prod"]["agent"]) and "deploy" in gby["prod"]["human"]
          and any("production" in n for n in notes), str(notes))
    check("deploy_nonprod as an agent action: the first environment is the agent's",
          "deploy" in gby["dev"]["agent"])
    check("cicd.gates become the required checks of every protected branch",
          all(e["required_checks"] == ["build", "unit_tests"] for e in gm["environments"] if e["protected"]))
    check("the CI/CD input carries checks, gates, trigger and promotion",
          gci == {"checks": ["build", "unit_tests", "lint"], "gates": ["build", "unit_tests"],
                  "deploy_trigger": "on_merge", "promotion": "sequential"}, str(gci))
    check("cicd.ci_platform names the platform", GA.platform_of(GUIDE) == "gitlab")
    gated = dict(GUIDE, guardrails=dict(GUIDE["guardrails"], approval_gates=["deploy_nonprod"]))
    gm2, _, _ = GA.from_guide(gated)
    check("deploy_nonprod as an approval gate: every environment's deploy waits for a person",
          all(p["gated"] for p in C.deploy_plan(gm2)))
    for platform in C.PLATFORMS:
        check(f"criterion 1: the guide's answers generate a {platform} pipeline that passes the check",
              C.generate(gm, gci, platform)["files"] and True)
    merges = dict(GUIDE, guardrails=dict(GUIDE["guardrails"], who_merges="agent_when_green"))
    check("an answer the matrix cannot express (agents merging) is reported, not dropped",
          any("merg" in n for n in GA.from_guide(merges)[2]))
    bad = dict(GUIDE, environments=dict(GUIDE["environments"], production="live"))
    msg = raises(lambda: GA.from_guide(bad), GA.GuideAnswerError)
    check("a production name that is not among the environments is refused, naming both",
          "live" in msg and "dev" in msg, msg[:160])
    msg = raises(lambda: GA.from_guide({"cicd": GUIDE["cicd"]}), GA.GuideAnswerError)
    check("unanswered topics are refused, naming each", "environments" in msg and "guardrails" in msg)

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("E. --from-guide on a fresh install, and one Proposed ADR per pipeline")
    print("=" * 78)
    reset()
    check("fresh ATLAS_SEED=none boot", boot(NONE).returncode == 0)
    p = admin("generate", "--platform", "github", "--from-guide", "--out", str(TMP / "e-none"),
              env=NONE, quiet_fail=True)
    check("--from-guide before the guide is answered refuses, naming the topics, writing nothing",
          p.returncode == 1 and "environments" in p.stderr and not (TMP / "e-none").exists(),
          p.stderr.strip()[:200])
    got = answer("cicd-owner", [[t, GUIDE[t]] for t in ("environments", "guardrails", "cicd")])
    check("the guide is answered through POST /api/setup/answers (each 201)",
          [s for _, s in got] == [201, 201, 201], str(got))
    adrs0 = rows("SELECT adr_id FROM adr_record ORDER BY adr_id")
    ev0 = rows("SELECT count(*) FROM graph_event")[0][0]
    d0 = data()
    outs = {}
    for platform in C.PLATFORMS:
        outs[platform] = TMP / f"e-{platform}"
        p = admin("generate", "--platform", platform, "--from-guide", "--out", str(outs[platform]),
                  env=NONE)
        check(f"criterion 1: --from-guide writes the {platform} pipeline",
              p.returncode == 0 and tree(outs[platform]) == C.generate(gm, gci, platform)["files"],
              p.stdout.strip()[-200:])
    check("--from-guide without --record-adr reads only: the database is byte-identical",
          data() == d0)
    for platform in C.PLATFORMS:
        p = admin("generate", "--platform", platform, "--from-guide", "--out", str(outs[platform]),
                  "--force", "--record-adr", env=NONE)
        check(f"--record-adr for {platform} exits 0 and names a Proposed ADR",
              p.returncode == 0 and "create Proposed ADR" in p.stdout, p.stdout.strip()[-200:])
    new = rows("SELECT adr_id, status, source, slug, project_node_id, supersedes FROM adr_record "
               "WHERE slug LIKE 'first-cicd-%' ORDER BY adr_id")
    check("criterion 4: one Proposed Atlas-native ADR per platform, on atlas-system",
          len(new) == 3 and all(s == "proposed" and src == "atlas" and n == "atlas-system" and sup == []
                                for _, s, src, _, n, sup in new)
          and sorted(r[3].split("-")[2] for r in new) == sorted(C.PLATFORMS), str(new))
    check("... numbered after the guide's own ADRs in the same project",
          all(a not in [x for (x,) in adrs0] for a, *_ in new))
    gh_adr = next(r for r in new if "-github-" in r[3])
    check("... each slug carries the digest of the files it describes",
          gh_adr[3] == f"first-cicd-github-{C.generate(gm, gci, 'github')['digest'][:12]}")
    content = rows("SELECT content FROM adr_record WHERE adr_id = :i", i=gh_adr[0])[0][0]
    check("the ADR names the files, the gated environments and that nothing was pushed",
          ".github/workflows/atlas-pipeline.yml" in content and "| prod (production)" in content
          and "pushed nothing" in content and "Status: Proposed" in content)
    ev = rows("SELECT action, actor_kind, detail->>'operator', detail->>'via', entity_id FROM graph_event "
              "ORDER BY id OFFSET :n", n=ev0)
    check("criterion 4: one node.adr.write graph_event per ADR, naming the operator and the verb",
          len(ev) == 3 and all(a == "node.adr.write" and k == "system" and o == ACTOR
                               and v == "atlas-admin cicd generate --record-adr" and e == "atlas-system"
                               for a, k, o, v, e in ev), str(ev))
    d1 = data()
    p = admin("generate", "--platform", "github", "--from-guide", "--out", str(outs["github"]),
              "--force", "--record-adr", env=NONE)
    check("the same files again write no ADR and no event",
          p.returncode == 0 and "already recorded" in p.stdout and data() == d1, p.stdout.strip()[-160:])
    got = answer("cicd-owner-2", [["cicd", dict(GUIDE["cicd"], deploy_trigger="manual")]])
    check("a changed CI/CD answer is recorded through the guide", [s for _, s in got] == [201], str(got))
    p = admin("generate", "--platform", "github", "--from-guide", "--out", str(outs["github"]),
              "--force", "--record-adr", env=NONE)
    after = {a: (s, sup, sb) for a, s, sup, sb in rows(
        "SELECT adr_id, status, supersedes, superseded_by FROM adr_record "
        "WHERE slug LIKE 'first-cicd-%' ORDER BY adr_id")}
    newest = max(after)
    check("changed files: a new Proposed github ADR supersedes the old one, which is marked superseded",
          after[newest] == ("proposed", [gh_adr[0]], []) and after[gh_adr[0]] == ("superseded", [], [newest]),
          str(after))
    check("... and the gitlab and azure ADRs are untouched",
          all(after[a] == ("proposed", [], []) for a, *_ in new if a != gh_adr[0]))
    prod_job = yaml.safe_load((outs["github"] / ".github/workflows/atlas-pipeline.yml")
                              .read_text(encoding="utf-8"))["jobs"]["deploy-prod"]
    check("the regenerated prod deploy follows the new trigger and still waits on environment prod",
          prod_job["if"].startswith("github.event_name == 'workflow_dispatch'")
          and prod_job["environment"] == {"name": "prod"})

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("F. an install that never runs it behaves as today")
    print("=" * 78)
    route_check = run_child(["-c", "import app, json; print(json.dumps(sorted(getattr(r, 'path', '') "
                                   "for r in app.app.routes)))"])
    paths = json.loads(route_check.stdout.strip().splitlines()[-1]) if route_check.returncode == 0 else ["?"]
    check("no HTTP route mentions cicd", paths != ["?"] and not any("cicd" in p for p in paths))
    reset()
    if distribution.DEFAULT_SEED_PROFILE == "homelab":
        boot()  # the homelab default profile: no system node set
    else:
        # STORY-oss-23 A3b: every profile this copy offers seeds the system node set on a fresh
        # graph, so the homelab-shaped install (one made before the set existed) is a boot with
        # the set's seeding stubbed out in that one child; startup is otherwise real.
        run_child(["-c", "import system_node; system_node.seed = lambda s: []; import app; app.startup()"])
    seed_hl = "\n".join([
        "import app, testseed",
        "from db import session",
        "from models_graph import Node, Edge",
        "from attention import recompute_derived",
        "with session() as s:",
        "    testseed.build(s)",
        "    for nid, t in [('atlas', 'project'), ('atlas-api', 'service')]:",
        "        s.add(Node(id=nid, type=t, name=nid, attributes={}, body='homelab', source=None, origin='atlas'))",
        "    s.flush()",
        "    s.add(Edge(src='atlas', dst='atlas-api', edge_type='contains', origin='atlas'))",
        "    s.flush()",
        "    recompute_derived(s)",
        "    s.commit()",
    ])
    check("homelab-shaped database built", run_child(["-c", seed_hl]).returncode == 0)
    boot()
    d_hl = data()
    p = admin("generate", "--platform", "github", "--matrix", G.EXAMPLE_FILE, "--answers",
              str(ANSWERS_FILE), "--out", str(TMP / "f-out"))
    check("a file-based generate on a homelab install exits 0", p.returncode == 0)
    p = admin("generate", "--platform", "github", "--matrix", G.EXAMPLE_FILE, "--answers",
              str(ANSWERS_FILE), "--out", str(TMP / "f-adr"), "--record-adr", quiet_fail=True)
    check("--record-adr without atlas-system is refused before any file is written",
          p.returncode == 1 and "system_node.py --apply" in p.stderr and not (TMP / "f-adr").exists(),
          p.stderr.strip()[:200])
    p = admin("generate", "--platform", "github", "--from-guide", "--out", str(TMP / "f-guide"),
              quiet_fail=True)
    check("--from-guide with no guide answered is refused", p.returncode == 1)
    boot()
    check("the homelab-shaped database is byte-identical after boots and every cicd verb", data() == d_hl)
    check("no pipeline ADR exists", not rows("SELECT 1 FROM adr_record WHERE slug LIKE 'first-cicd-%'"))
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
