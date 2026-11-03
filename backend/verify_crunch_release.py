"""
Verification for STORY-atlas-crunch-mode, the release-path half: what crunch week CHANGES in
the guardrails matrix and the generated pipeline (`guardrails.crunch_overlay`,
`cicd.generate(..., crunch=...)`, `cicd.check_crunch`, the `--crunch FILE` / `--crunch-from-db`
flags on `atlas-admin guardrails show|export` and `atlas-admin cicd generate`).

The claims, and the section that proves each:

  A. The overlay removes EXACTLY `skip_envs` from the path to production: the deploy plan loses
     those environments and nothing else, every other environment is byte-identical,
     production included, a skipped environment keeps its branches, protection, other actions
     and commands; a `skip_envs` naming production, an unknown environment or an unknown key
     is refused; the input is not mutated.
  B. An agent production deploy is refused before AND after the overlay: `validate` and
     `crunch_overlay` refuse a matrix giving agents deploy / deploy_via_pipeline /
     approve_deploy in production, and an overlaid matrix edited to add one is refused again;
     the exported policies under the overlay still deny it (Claude Code: the prod deploy
     command denied and never allowed, `gh pr merge` denied; GitHub branch protection on prod's
     branch unchanged: a review, admins included).
  C. The crunch variant generates and passes `check_pipeline` and `check_crunch` on all three
     platforms and every production trigger: no job for a skipped environment, production's
     deploy depends on the checks alone, production still waits for a person (GitHub
     environment + required reviewers, GitLab `when: manual` + `allow_failure: false`, Azure a
     deployment job on the environment). Platform lint of the variant (actionlint, GitLab and
     Azure schemas) is tools/ci/cicd_lint.sh in .github/workflows/cicd-lint.yml.
  D. backup.policy per_deploy puts `atlas-admin backup run --reason per_deploy` in production's
     deploy job, before the deploy step, with nothing letting the deploy run after it fails;
     other policies put no step anywhere; perturbations (step removed, moved after the deploy,
     continue-on-error, a later step with its own condition, a template without the
     placeholder) are each refused.
  E. With crunch week off the output is byte-identical to today's: `generate(crunch=None)`
     equals `generate()`, and the shipped templates equal the pre-crunch ones with their crunch
     placeholder lines removed, for every platform, trigger and promotion; the CLI with
     `--crunch-from-db` while crunch.current() is None writes the same bytes as without it.
  F. The CLI: `--crunch FILE` generates the variant, and so does a file giving
     `duration_hours` instead of `ends_at` (as crunch.validate_config and PUT /api/crunch
     accept it), while a file with a misspelt key is refused; `--crunch-from-db` with crunch
     week on overlays (crunch.current() stubbed so this script needs no database, but the
     stub's state is the REAL shape: crunch._state over a CrunchState row, `revision`
     included, TASK-1432); `--record-adr` with a crunch flag is refused before a file is
     written; a crunch file skipping production is refused. The same commands against a real
     active crunch week in a real database are verify_crunch.py section C.

Needs no database: everything here is pure or a CLI run pointed at a URL nothing listens on.

Run:
    python verify_crunch_release.py
"""
from __future__ import annotations

import base64
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import yaml  # noqa: E402

import cicd as C  # noqa: E402
import crunch as CR  # noqa: E402  (pure to import: models_crunch is declarative only)
import guardrails as G  # noqa: E402
import guide_answers as GA  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="verify-crunch-release-"))
ANSWERS_FILE = HERE / "data" / "cicd" / "example-answers.yaml"
NO_DB = "postgresql+psycopg2://none:none@127.0.0.1:9/none"
ENV = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_")}
ENV.update({"ATLAS_DB_URL": NO_DB, "ATLAS_LOGIN_ORIGINS": "https://testserver",
            "ATLAS_PASSWORD_PEPPER": base64.b64encode(os.urandom(32)).decode(),
            "ATLAS_TOKEN_PEPPER": base64.b64encode(os.urandom(32)).decode()})

CRUNCH = {"starts_at": "2026-10-27T08:00:00+00:00", "ends_at": "2026-11-03T08:00:00+00:00",
          "backup": {"policy": "per_deploy", "retention_days": 14},
          "skip_envs": ["stst", "atst", "preprod"]}
# What crunch.current() returns while crunch week is on, built by crunch's own `_state` over an
# (unsaved) state row, so a key the state gains reaches this script without anyone editing it.
# A hand-written stub without `revision` is how TASK-1432 got past CI.
STATE = CR._state(CR.CrunchState(
    id=CR.STATE_ID, active=True, config=CRUNCH, activated_by_subject="owner",
    activated_at=CR._parse_ts("starts_at", CRUNCH["starts_at"]), adr_id="SYS-0009",
    revision=3), True)
# A --crunch FILE giving a length instead of an end (crunch.CONFIG_KEYS, THE LENGTH).
CRUNCH_LEN = {"duration_hours": 72, "backup": {"policy": "per_deploy", "retention_days": 14},
              "skip_envs": ["stst", "atst", "preprod"]}
PLACEHOLDERS = ("@@BACKUP_STEP@@", "@@CRUNCH_NOTE@@")

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    detail = " ".join(detail.split())
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def raises(fn, exc) -> str:
    try:
        fn()
    except exc as e:  # noqa: BLE001
        return str(e)
    return ""


def env_of(m: dict, name: str) -> dict:
    return next(e for e in m["environments"] if e["name"] == name)


def admin_child(argv: list[str], state="unset") -> subprocess.CompletedProcess:
    """`admin.main(argv)` in its own process. With `state`, a stub `crunch` module answers
    `current(s)` with it (None: crunch week off) and touches no database."""
    pre = ""
    if state != "unset":
        pre = ("import sys, types, json\n"
               "m = types.ModuleType('crunch')\n"
               f"m.current = lambda s: json.loads({json.dumps(json.dumps(state))})\n"
               "sys.modules['crunch'] = m\n")
    code = pre + f"import sys, admin\nsys.exit(admin.main({argv!r}))\n"
    return subprocess.run([sys.executable, "-c", code], cwd=HERE, env=ENV, capture_output=True,
                          text=True, timeout=300)


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


try:
    BASE = G.example()
    CI = GA.cicd_input(GA.load_answers_file(str(ANSWERS_FILE)))

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("A. the overlay removes exactly skip_envs from the path to production")
    print("=" * 78)
    before_input = copy.deepcopy(BASE)
    over = G.crunch_overlay(BASE, CRUNCH)
    check("the input matrix is not mutated", BASE == before_input)
    plan0 = [p["env"] for p in C.deploy_plan(BASE)]
    plan1 = [p["env"] for p in C.deploy_plan(over)]
    check("the deploy plan loses exactly skip_envs",
          plan1 == [n for n in plan0 if n not in CRUNCH["skip_envs"]], f"{plan0} -> {plan1}")
    check("the same environments, in the same order",
          [e["name"] for e in over["environments"]] == [e["name"] for e in BASE["environments"]])
    for e0 in BASE["environments"]:
        e1 = env_of(over, e0["name"])
        if e0["name"] not in CRUNCH["skip_envs"]:
            check(f"{e0['name']} (not skipped{', production' if e0['production'] else ''}) "
                  f"is byte-identical", e0 == e1)
            continue
        keep = ("branches", "production", "protected", "required_reviews", "required_checks",
                "commands")
        check(f"{e0['name']} (skipped) keeps its branches, protection, reviews, checks and "
              f"commands", all(e0[k] == e1[k] for k in keep))
        check(f"{e0['name']} (skipped) loses only its deploy actions",
              all(e1[k] == [a for a in e0[k] if a not in G.DEPLOY_ACTIONS]
                  for k in ("agent", "human", "step_up")))
        check(f"{e0['name']} (skipped) says so in its notes", "Crunch week" in e1["notes"])
    check("the overlaid matrix says it is an overlay, naming the base digest",
          G.digest(BASE)[:12] in over["description"] and "preprod" in over["description"])
    check("an empty skip_envs changes no environment",
          G.crunch_overlay(BASE, {**CRUNCH, "skip_envs": []})["environments"]
          == BASE["environments"])
    one = G.crunch_overlay(BASE, {**CRUNCH, "skip_envs": ["preprod"]})
    check("skipping one environment removes that one only",
          [p["env"] for p in C.deploy_plan(one)] == [n for n in plan0 if n != "preprod"])
    for label, cfg, needle in [
            ("skip_envs naming production", {**CRUNCH, "skip_envs": ["prod"]}, "is production"),
            ("an unknown environment", {**CRUNCH, "skip_envs": ["qa"]}, "not an environment"),
            ("an unknown key (misspelt skip_env)", {"skip_env": ["preprod"]}, "unknown key"),
            ("skip_envs not a list", {"skip_envs": "preprod"}, "list of non-empty strings"),
            ("not a mapping", ["preprod"], "must be a mapping")]:
        msg = raises(lambda c=cfg: G.crunch_overlay(BASE, c), G.MatrixError)
        check(f"refused: {label}", needle in msg, msg.replace("\n", " ")[:160])
    check("the real crunch.current() shape carries state-only keys (revision among them)",
          "revision" in STATE and bool(set(STATE) - set(CR.CONFIG_KEYS)), str(sorted(STATE)))
    msg = raises(lambda: G.crunch_overlay(BASE, STATE), G.MatrixError)
    check("accepted: the real crunch.current() state, overlaid exactly as its config",
          not msg and G.crunch_overlay(BASE, STATE) == over, msg[:160])
    msg = raises(lambda: G.crunch_overlay(BASE, CRUNCH_LEN), G.MatrixError)
    check("accepted: a config with duration_hours instead of ends_at (crunch.CONFIG_KEYS)",
          not msg and C.deploy_plan(G.crunch_overlay(BASE, CRUNCH_LEN)) == C.deploy_plan(over),
          msg[:160])
    check("every crunch.CONFIG_KEYS key is one the release path accepts",
          set(CR.CONFIG_KEYS) <= G.CRUNCH_KEYS, str(set(CR.CONFIG_KEYS) - G.CRUNCH_KEYS))
    msg = raises(lambda: G.crunch_overlay(BASE, {**STATE, "skip_env": ["preprod"]}), G.MatrixError)
    check("refused: the real state plus a misspelt skip_env", "unknown key(s): skip_env" in msg,
          msg.replace("\n", " ")[:160])
    check("refused before the generator writes anything (skip prod)",
          bool(raises(lambda: C.generate(BASE, CI, "github", crunch={"skip_envs": ["prod"]}),
                      G.MatrixError)))

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("B. an agent production deploy is refused before and after the overlay")
    print("=" * 78)
    for action in G.DEPLOY_ACTIONS:
        bad = copy.deepcopy(BASE)
        prod = env_of(bad, "prod")
        prod["agent"].append(action)
        if action in prod["human"]:
            prod["human"].remove(action)
        prod["step_up"] = [a for a in prod["step_up"] if a in prod["human"]]
        check(f"before: validate refuses agent `{action}` in production",
              bool(raises(lambda b=bad: G.validate(b), G.MatrixError)))
        check(f"overlay: crunch_overlay refuses a matrix with agent `{action}` in production",
              bool(raises(lambda b=bad: G.crunch_overlay(b, CRUNCH), G.MatrixError)))
        after = copy.deepcopy(over)
        p2 = env_of(after, "prod")
        p2["agent"].append(action)
        if action in p2["human"]:
            p2["human"].remove(action)
        p2["step_up"] = [a for a in p2["step_up"] if a in p2["human"]]
        check(f"after: an overlaid matrix edited to give agents `{action}` in production is "
              f"refused", bool(raises(lambda a=after: G.validate(a), G.MatrixError)))
        check(f"after: the generator refuses it too (`{action}`)",
              bool(raises(lambda b=bad: C.generate(b, CI, "github", crunch=CRUNCH),
                          G.MatrixError)))
    check("the overlaid production keeps every deploy action with a human",
          not set(env_of(over, "prod")["agent"]) & set(G.DEPLOY_ACTIONS)
          and {"deploy", "approve_deploy"} <= set(env_of(over, "prod")["human"])
          and env_of(over, "prod")["step_up"] == ["deploy"])
    for label, m in (("before", BASE), ("after", over)):
        cc = G.export_claude_code(m)["permissions"]
        check(f"claude-code export {label} the overlay: the prod deploy command is denied and "
              f"never allowed", "Bash(tools/deploy.sh prod*)" in cc["deny"]
              and not any("deploy.sh prod" in a for a in cc["allow"]))
        check(f"claude-code export {label} the overlay: `gh pr merge` and pushes to main denied",
              "Bash(gh pr merge*)" in cc["deny"] and "Bash(git push * main)" in cc["deny"])
    cc0, cc1 = G.export_claude_code(BASE)["permissions"], G.export_claude_code(over)["permissions"]
    check("claude-code export: the overlay adds no allow rule",
          set(cc1["allow"]) <= set(cc0["allow"]), str(sorted(set(cc1["allow"]) - set(cc0["allow"]))))
    check("claude-code export: the overlay removes no deny rule",
          set(cc0["deny"]) <= set(cc1["deny"]), str(sorted(set(cc0["deny"]) - set(cc1["deny"]))))
    gh0 = {b["branch"]: b for b in G.export_github(BASE)["branches"]}
    gh1 = {b["branch"]: b for b in G.export_github(over)["branches"]}
    check("branch-protection export: the same protected branches under the overlay",
          set(gh0) == set(gh1))
    check("branch-protection export: every body unchanged under the overlay (prod's included)",
          all(gh0[b]["body"] == gh1[b]["body"] for b in gh0))
    main = gh1["main"]["body"]
    check("branch-protection export: prod's branch still needs a review, admins included, "
          "last-push approval", main["required_pull_request_reviews"][
              "required_approving_review_count"] >= 1 and main["enforce_admins"] is True
          and main["required_pull_request_reviews"]["require_last_push_approval"] is True)

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("C. the crunch variant on every platform and trigger")
    print("=" * 78)
    variants: dict[tuple[str, str], dict] = {}
    for trig in C.TRIGGERS:
        for pf in C.PLATFORMS:
            ci = {**CI, "deploy_trigger": trig}
            try:
                g = C.generate(BASE, ci, pf, crunch=CRUNCH)
            except (C.CicdError, G.MatrixError) as exc:
                check(f"{pf}/{trig}: the crunch variant generates", False, str(exc)[:300])
                continue
            variants[(pf, trig)] = g
            files, path = g["files"], g["pipeline_path"]
            ov = G.crunch_overlay(BASE, CRUNCH)
            check(f"{pf}/{trig}: generates; check_pipeline and check_crunch find nothing",
                  C.check_pipeline(pf, files, ov) == []
                  and C.check_crunch(pf, files, ov, g["crunch"], g["cicd"]) == [])
            doc = yaml.safe_load(files[path])
            text = files[path]
            check(f"{pf}/{trig}: no job or stage for a skipped environment",
                  not any(f"deploy-{n}:" in text or f"deploy_{n}" in text
                          for n in CRUNCH["skip_envs"]))
            check(f"{pf}/{trig}: the header names the crunch-week variant and its end",
                  "CRUNCH WEEK VARIANT until 2026-11-03" in text)
            if pf == "github":
                job = doc["jobs"]["deploy-prod"]
                check(f"github/{trig}: deploy-prod needs exactly the checks",
                      job["needs"] == CI["checks"])
                check(f"github/{trig}: deploy-prod runs in environment prod, and the settings "
                      f"ask for required reviewers on it",
                      job["environment"]["name"] == "prod"
                      and "required reviewers" in files["atlas-cicd/github-settings.md"])
            elif pf == "gitlab":
                job = doc["deploy-prod"]
                check(f"gitlab/{trig}: stages are [check, deploy] and deploy-prod has no needs "
                      f"(it waits for the check stage)",
                      doc["stages"] == ["check", "deploy"] and "needs" not in job)
                check(f"gitlab/{trig}: deploy-prod is `when: manual` + `allow_failure: false`",
                      all(r.get("when") == "manual" for r in job["rules"])
                      and job["allow_failure"] is False)
            else:
                st = next(s for s in doc["stages"] if s["stage"] == "deploy_prod")
                check(f"azure/{trig}: deploy_prod dependsOn checks alone",
                      st["dependsOn"] == "checks")
                check(f"azure/{trig}: deploy_prod is a deployment job on environment prod",
                      st["jobs"][0].get("deployment") == "deploy_prod"
                      and st["jobs"][0]["environment"] == "prod")
            check(f"{pf}/{trig}: the settings file has a Crunch week section",
                  "## Crunch week" in files[f"atlas-cicd/{pf}-settings.md"])

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("D. backup.policy per_deploy: a step before production's deploy, which gates it")
    print("=" * 78)

    def prod_steps(pf: str, doc: dict) -> list:
        if pf == "github":
            return doc["jobs"]["deploy-prod"]["steps"]
        if pf == "gitlab":
            return doc["deploy-prod"]["script"]
        st = next(s for s in doc["stages"] if s["stage"] == "deploy_prod")
        return st["jobs"][0]["strategy"]["runOnce"]["deploy"]["steps"]

    def is_backup(st) -> bool:
        return C.BACKUP_COMMAND in (st if isinstance(st, str) else
                                    str(st.get("run") or st.get("script") or ""))

    for pf in C.PLATFORMS:
        g = variants.get((pf, "on_merge"))
        if g is None:
            check(f"{pf}: variant available", False)
            continue
        doc = yaml.safe_load(g["files"][g["pipeline_path"]])
        steps = prod_steps(pf, doc)
        bi = [i for i, s in enumerate(steps) if is_backup(s)]
        di = [i for i, s in enumerate(steps) if C.CREDENTIAL in json.dumps(s)]
        check(f"{pf}: production's deploy runs `{C.BACKUP_COMMAND}` once, before the deploy",
              len(bi) == 1 and di and bi[0] < min(di), f"backup at {bi}, deploy at {di}")
        check(f"{pf}: nothing after the backup may run once it failed (no continue-on-error, "
              f"no own condition)",
              all(not (isinstance(s, dict) and ({"if", "condition", "continue-on-error",
                                                 "continueOnError"} & set(s)))
                  for s in steps[bi[0]:]) if bi else False)
        check(f"{pf}: the backup step is in no other job",
              g["files"][g["pipeline_path"]].count(C.BACKUP_COMMAND) == 1)
        ov = G.crunch_overlay(BASE, CRUNCH)

        def mutated(fn, pf=pf, g=g, ov=ov) -> list[str]:
            d = yaml.safe_load(g["files"][g["pipeline_path"]])
            fn(prod_steps(pf, d))
            files = {**g["files"], g["pipeline_path"]: yaml.safe_dump(d, sort_keys=False)}
            return C.check_crunch(pf, files, ov, g["crunch"], g["cicd"])

        def drop(s):
            del s[[i for i, x in enumerate(s) if is_backup(x)][0]]

        def move_last(s):
            i = [i for i, x in enumerate(s) if is_backup(x)][0]
            s.append(s.pop(i))

        def soften(s):
            i = [i for i, x in enumerate(s) if is_backup(x)][0]
            if isinstance(s[i], str):
                s[i] = s[i] + " || true"
            else:
                s[i]["continue-on-error" if pf == "github" else "continueOnError"] = True

        def later_cond(s):
            for x in s[[i for i, x in enumerate(s) if is_backup(x)][0] + 1:]:
                if isinstance(x, dict):
                    x["if" if pf == "github" else "condition"] = "always()"

        perturb = [("the backup step removed", drop, "runs no"),
                   ("the backup moved after the deploy", move_last, "BEFORE"),
                   ("a failure of the backup swallowed", soften, "without stopping"
                    if pf != "gitlab" else "swallows")]
        if pf != "gitlab":
            perturb.append(("a later step with its own condition", later_cond, "own condition"))
        for label, fn, needle in perturb:
            probs = mutated(fn)
            check(f"perturbation: {pf} with {label} fails check_crunch",
                  any(needle in p for p in probs), "; ".join(probs)[:200])
        check(f"{pf}: the unedited variant, through the same YAML round-trip, passes",
              mutated(lambda s: None) == [])
        tdir = TMP / f"tmpl-nobackup-{pf}"
        shutil.copytree(C.TEMPLATE_DIR, tdir)
        dt = tdir / pf / "deploy.tmpl"
        dt.write_text(dt.read_text(encoding="utf-8").replace("@@BACKUP_STEP@@\n", ""),
                      encoding="utf-8", newline="\n")
        msg = raises(lambda: C.generate(BASE, CI, pf, crunch=CRUNCH, template_dir=tdir),
                     C.CicdError)
        check(f"perturbation: a {pf} template without the backup placeholder cannot produce a "
              f"per_deploy variant", "runs no" in msg, msg.replace("\n", " ")[:160])
    for policy in ("none", "once", "hourly", "daily"):
        cfg = {**CRUNCH, "backup": {"policy": policy, "retention_days": 7}}
        texts = [C.generate(BASE, CI, pf, crunch=cfg)["files"][C.PIPELINE_PATH[pf]]
                 for pf in C.PLATFORMS]
        check(f"policy {policy}: no backup step on any platform",
              not any(C.BACKUP_COMMAND in t for t in texts))
    msg = raises(lambda: C.generate(BASE, CI, "github",
                                    crunch={**CRUNCH, "backup": {"policy": "weekly"}}), C.CicdError)
    check("an unknown backup policy is refused", "backup.policy" in msg, msg[:120])

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("E. crunch off: the output is byte-identical to today's")
    print("=" * 78)
    pre = TMP / "tmpl-pre-crunch"
    shutil.copytree(C.TEMPLATE_DIR, pre)
    stripped = 0
    for f in pre.rglob("*.tmpl"):
        t = f.read_text(encoding="utf-8")
        u = "".join(ln for ln in t.splitlines(keepends=True) if ln.strip() not in PLACEHOLDERS)
        stripped += t != u
        f.write_text(u, encoding="utf-8", newline="\n")
    check("the crunch placeholders sit alone on their lines in six templates", stripped == 6,
          str(stripped))
    same = total = 0
    for trig in C.TRIGGERS:
        for prom in C.PROMOTIONS:
            for pf in C.PLATFORMS:
                ci = {**CI, "deploy_trigger": trig, "promotion": prom}
                a = C.generate(BASE, ci, pf)
                b = C.generate(BASE, ci, pf, crunch=None)
                c = C.generate(BASE, ci, pf, template_dir=pre)
                total += 1
                same += a == b and a["files"] == c["files"] and "crunch" not in a
                if a["files"] != c["files"]:
                    print(f"  differs: {pf}/{trig}/{prom}")
    check(f"generate(crunch=None) == generate() == the pre-crunch templates' output "
          f"({same}/{total} platform x trigger x promotion)", same == total == 24)
    check("no crunch text in the output when off",
          not any("CRUNCH" in t or "Crunch week" in t
                  for t in C.generate(BASE, CI, "azure")["files"].values()))

    gen = ["--actor", "verify-crunch", "cicd", "generate", "--matrix", G.EXAMPLE_FILE,
           "--answers", str(ANSWERS_FILE)]
    for pf in C.PLATFORMS:
        p0 = admin_child(gen + ["--platform", pf, "--out", str(TMP / f"off-plain-{pf}")])
        p1 = admin_child(gen + ["--platform", pf, "--out", str(TMP / f"off-db-{pf}"),
                                "--crunch-from-db"], state=None)
        check(f"CLI {pf}: --crunch-from-db with crunch week off writes the same bytes",
              p0.returncode == p1.returncode == 0
              and tree(TMP / f"off-plain-{pf}") == tree(TMP / f"off-db-{pf}")
              and "crunch week is off" in p1.stderr, (p0.stderr + p1.stderr).strip()[:200])
    exp = ["--actor", "verify-crunch", "guardrails", "export", "--format", "claude-code",
           "--from", G.EXAMPLE_FILE, "--out", "-"]
    e0, e1 = admin_child(exp), admin_child(exp + ["--crunch-from-db"], state=None)
    check("CLI: guardrails export --crunch-from-db with crunch week off prints the same",
          e0.returncode == e1.returncode == 0 and e0.stdout == e1.stdout and e0.stdout)

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("F. the CLI flags")
    print("=" * 78)
    cfile = TMP / "crunch.yaml"
    cfile.write_text(yaml.safe_dump(CRUNCH), encoding="utf-8")
    for pf in C.PLATFORMS:
        out = TMP / f"cli-file-{pf}"
        p = admin_child(gen + ["--platform", pf, "--out", str(out), "--crunch", str(cfile)])
        want = C.generate(BASE, CI, pf, crunch=CRUNCH)["files"]
        got = tree(out)
        check(f"CLI {pf}: --crunch FILE writes the crunch variant (the same bytes as generate)",
              p.returncode == 0 and got == {k: v.encode("utf-8") for k, v in want.items()},
              (p.stdout + p.stderr).strip()[-200:])
        out = TMP / f"cli-db-{pf}"
        p = admin_child(gen + ["--platform", pf, "--out", str(out), "--crunch-from-db"],
                        state=STATE)
        check(f"CLI {pf}: --crunch-from-db with crunch week on writes the crunch variant",
              p.returncode == 0 and tree(out) == {k: v.encode("utf-8") for k, v in want.items()},
              (p.stdout + p.stderr).strip()[-200:])
    lfile = TMP / "crunch-len.yaml"
    lfile.write_text(yaml.safe_dump(CRUNCH_LEN), encoding="utf-8")
    for pf in C.PLATFORMS:
        out = TMP / f"cli-len-{pf}"
        p = admin_child(gen + ["--platform", pf, "--out", str(out), "--crunch", str(lfile)])
        try:
            want_len = C.generate(BASE, CI, pf, crunch=CRUNCH_LEN)["files"]
        except G.MatrixError as exc:  # a refusal here is the failure, not a crash
            want_len = {}
            print(f"  generate refused the duration_hours config: {exc}")
        check(f"CLI {pf}: --crunch FILE with duration_hours writes the crunch variant",
              p.returncode == 0 and tree(out) == {k: v.encode("utf-8") for k, v in want_len.items()}
              and any("CRUNCH WEEK VARIANT" in v for v in want_len.values()),
              (p.stdout + p.stderr).strip()[-200:])
    e3 = admin_child(exp + ["--crunch", str(lfile)])
    check("CLI: guardrails export --crunch FILE with duration_hours succeeds",
          e3.returncode == 0 and "unknown key" not in e3.stderr, e3.stderr.strip()[:200])
    mfile = TMP / "crunch-misspelt.yaml"
    mfile.write_text(yaml.safe_dump({**CRUNCH_LEN, "skip_env": ["preprod"]}), encoding="utf-8")
    out = TMP / "cli-misspelt"
    p = admin_child(gen + ["--platform", "github", "--out", str(out), "--crunch", str(mfile)])
    check("CLI: a crunch file with a misspelt key (skip_env) is refused, nothing written",
          p.returncode == 1 and "unknown key(s): skip_env" in p.stderr and not out.exists(),
          p.stderr.strip()[:200])
    e4 = admin_child(exp + ["--crunch", str(mfile)])
    check("CLI: guardrails export --crunch FILE with a misspelt key is refused",
          e4.returncode == 1 and "unknown key(s): skip_env" in e4.stderr, e4.stderr.strip()[:200])
    e2 = admin_child(exp + ["--crunch-from-db"], state=STATE)
    cc = json.loads(e2.stdout or "{}").get("permissions", {})
    check("CLI: guardrails export --crunch-from-db with crunch week on still denies the agent "
          "prod deploy", e2.returncode == 0 and "Bash(tools/deploy.sh prod*)" in cc.get("deny", [])
          and not any("deploy.sh prod" in a for a in cc.get("allow", [])))
    out = TMP / "cli-adr"
    p = admin_child(gen + ["--platform", "github", "--out", str(out), "--crunch", str(cfile),
                           "--record-adr"])
    check("CLI: --record-adr with --crunch is refused before any file is written",
          p.returncode == 1 and "temporary" in p.stderr and not out.exists(), p.stderr.strip()[:200])
    bad = TMP / "crunch-prod.json"
    bad.write_text(json.dumps({**CRUNCH, "skip_envs": ["prod"]}), encoding="utf-8")
    out = TMP / "cli-bad"
    p = admin_child(gen + ["--platform", "gitlab", "--out", str(out), "--crunch", str(bad)])
    check("CLI: a crunch file skipping production is refused, nothing written",
          p.returncode == 1 and "is production" in p.stderr and not out.exists(),
          p.stderr.strip()[:200])
    p = admin_child(gen + ["--platform", "gitlab", "--out", str(TMP / "x"), "--crunch",
                           str(cfile), "--crunch-from-db"])
    check("CLI: --crunch and --crunch-from-db are mutually exclusive", p.returncode == 2)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("=" * 78)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASSED")
