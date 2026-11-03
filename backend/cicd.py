"""A first CI/CD pipeline from the guardrails matrix and the setup guide (STORY-atlas-first-cicd).

Input: a validated guardrails matrix (`guardrails.py`) and the guide's CI/CD answers (normalised
by `guide_answers.cicd_input`). Output: FILES, as a {repo-relative path: text} mapping, which
the installer reviews and commits. Nothing here touches a repository, a network or a git
command: `generate` is a pure function, and the CLI (`atlas-admin cicd generate`) only writes
the mapping under `--out`. An install that never runs it has nothing new anywhere.

    atlas-admin cicd generate --platform github|gitlab|azure
                              (--from-guide | --matrix FILE --answers FILE)
                              --out DIR [--force] [--record-adr] [--adr-prefix SYS]
                              [--crunch FILE | --crunch-from-db]

PLATFORMS AND FILES
    github  .github/workflows/atlas-pipeline.yml, atlas-cicd/github-settings.md,
            atlas-cicd/github-branch-protection.json (guardrails.export_github, unchanged)
    gitlab  .gitlab-ci.yml, atlas-cicd/gitlab-settings.md
    azure   azure-pipelines.yml, atlas-cicd/azure-settings.md

THE TEMPLATES are `data/cicd/<platform>/{pipeline.yml,check,deploy}.tmpl`, filled by replacing
`@@NAME@@` placeholders (a placeholder alone on its line, given an empty value, removes the
line). Each template's comments say which part the PLATFORM enforces (an environment's
approvals and its scoped credential, branch protection) and which part is CONVENTION (the
file's own `needs`, `when: manual`, triggers): the file can be edited by whoever can push, so
the guarantee rests on the settings, and the settings file lists each one to configure.

WHAT THE PIPELINE CONTAINS
  * one job per check (`cicd.checks`); a check that is not a gate (`cicd.gates`) may fail
    without stopping anything (GitHub `continue-on-error`, GitLab `allow_failure`, Azure
    `continueOnError`). Every command is a placeholder that FAILS until replaced, so nothing
    deploys from an unfilled pipeline.
  * one deploy stage per environment whose matrix entry names a deploy action (`deploy`,
    `deploy_via_pipeline` or `approve_deploy`, in `agent` or `human`), and none for any other.
  * a deploy is GATED (waits for a person) when the environment is production, when
    `approve_deploy` is a human action there, or when no agent deploy action is allowed there.
    An environment where agents may deploy (`deploy`/`deploy_via_pipeline` in `agent`, never
    production: guardrails.validate refuses that) deploys on a push to its branches.
  * production deploys on `cicd.deploy_trigger` (on_merge: a push to its branch; on_tag: a
    `v*` tag; manual: a run started by hand; scheduled: a weekday schedule), and always after
    a person's approval. Other environments deploy on a push to their branches.
  * every deploy uses DEPLOY_CREDENTIAL held by the platform FOR THAT ENVIRONMENT (GitHub
    environment secret, GitLab protected variable scoped to the environment, Azure variable
    group `deploy-<env>` with the environment's checks). No credential is repository-wide, and
    no deploy runs for a pull/merge request, so nothing an agent holds or pushes reaches one.

`check_pipeline(platform, files, matrix)` re-reads generated files and lists every way they
break those rules; `generate` runs it on its own output and raises if anything is listed, so
an edited template that drops a gate cannot produce a file.

RECORDING (`record_adr`, CLI `--record-adr`): one Proposed Atlas-native ADR per generated
pipeline on `atlas-system`, slug `first-cicd-<platform>-<digest of the files>`, allocated and
written through `adr_alloc` like every Atlas-native ADR (`lock_project`, `allocate` with the
project's prefix or `--adr-prefix`, `add_atlas_adr` with its `node.adr.write`). The
same files again write nothing; changed files for the same platform supersede the previous
pipeline ADR for that platform.

THE CRUNCH-WEEK VARIANT (STORY-atlas-crunch-mode; `generate(..., crunch=CONFIG)`, CLI `--crunch
FILE` or `--crunch-from-db`, which reads `crunch.current`). The matrix goes through
`guardrails.crunch_overlay` first, so the environments in `skip_envs` deploy nowhere and have no
job; production's deploy job depends on the checks alone (it always did: no deploy job needs
another), and keeps exactly the gate the matrix gives production. When `backup.policy` is
`per_deploy`, production's deploy job runs `atlas-admin backup run --reason per_deploy` as a step
BEFORE the deploy step, in the same job, after the approval; a failed backup fails the job and
the deploy step never runs. The command is a placeholder the installer wires (atlas-admin runs
where the install's database is), and until it is wired it fails, which also stops the deploy.
The other policies (`once`, `hourly`, `daily`) are not the pipeline's: the install runs them.
`check_crunch` re-reads the variant and lists every way it breaks that; `generate` refuses it.
With `crunch=None` (the default) every byte of the output is what it was before this existed:
the templates' crunch placeholders sit alone on their lines and an empty value removes the line.
The variant is temporary, so `--record-adr` refuses it (crunch week records its own ADR).
"""
from __future__ import annotations

import hashlib
import json
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import guardrails

PLATFORMS = ("github", "gitlab", "azure")
PIPELINE_PATH = {"github": ".github/workflows/atlas-pipeline.yml",
                 "gitlab": ".gitlab-ci.yml",
                 "azure": "azure-pipelines.yml"}
PLATFORM_TITLE = {"github": "GitHub Actions", "gitlab": "GitLab CI", "azure": "Azure Pipelines"}
TEMPLATE_DIR = Path(__file__).resolve().parent / "data" / "cicd"
CHECK_VALUES = ("build", "unit_tests", "integration_tests", "lint", "type_check", "sast",
                "dependency_scan", "secret_scan")
CHECK_LABELS = {"build": "build", "unit_tests": "unit tests",
                "integration_tests": "integration tests", "lint": "lint and formatting",
                "type_check": "type checking", "sast": "static security analysis",
                "dependency_scan": "dependency vulnerability scan",
                "secret_scan": "secret scanning"}
TRIGGERS = ("manual", "on_merge", "on_tag", "scheduled")
PROMOTIONS = ("sequential", "independent")
CREDENTIAL = "DEPLOY_CREDENTIAL"
SCHEDULE_CRON = "0 6 * * 1-5"
TAG_PATTERN = "v*"
ADR_SLUG_PREFIX = "first-cicd-"
#: crunch.CONFIG_SCHEMA's backup policies. Only `per_deploy` puts a step in the pipeline.
BACKUP_POLICIES = ("none", "once", "per_deploy", "hourly", "daily")
BACKUP_COMMAND = "atlas-admin backup run --reason per_deploy"


class CicdError(ValueError):
    """Bad input or a generated file that breaks the guardrails. `problems` lists each."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("cannot generate the pipeline:\n  - " + "\n  - ".join(self.problems))


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #

def normalise_cicd(raw: Any) -> dict:
    """The generator's CI/CD input, checked: {checks, gates, deploy_trigger, promotion}.

    Checks and gates keep the guide's option order; a gate is also a check."""
    if not isinstance(raw, dict):
        raise CicdError(["the CI/CD input must be a mapping"])
    problems = []
    unknown = sorted(set(raw) - {"checks", "gates", "deploy_trigger", "promotion"})
    if unknown:
        problems.append(f"unknown CI/CD key(s): {', '.join(unknown)}")

    def values(key: str) -> list[str]:
        got = raw.get(key) or []
        if not isinstance(got, list) or not all(isinstance(v, str) for v in got):
            problems.append(f"{key}: must be a list of check names")
            return []
        bad = [v for v in got if v not in CHECK_VALUES]
        if bad:
            problems.append(f"{key}: {', '.join(bad)} not a check (one of {', '.join(CHECK_VALUES)})")
        return [v for v in CHECK_VALUES if v in got]

    gates = values("gates")
    checks = [v for v in CHECK_VALUES if v in set(values("checks")) | set(gates)]
    if not checks and not problems:
        problems.append("checks: a first pipeline needs at least one check")
    trigger = raw.get("deploy_trigger", "manual")
    if trigger not in TRIGGERS:
        problems.append(f"deploy_trigger: {trigger!r} is not one of {', '.join(TRIGGERS)}")
    promotion = raw.get("promotion", "sequential")
    if promotion not in PROMOTIONS:
        problems.append(f"promotion: {promotion!r} is not one of {', '.join(PROMOTIONS)}")
    if problems:
        raise CicdError(problems)
    return {"checks": checks, "gates": gates, "deploy_trigger": trigger, "promotion": promotion}


def normalise_crunch(raw: Any, matrix: dict) -> dict:
    """What the release path reads from a crunch config (or crunch.current's state):
    {skip_envs, backup_policy, ends_at}. Checked against `matrix`; raises guardrails.MatrixError
    for a bad `skip_envs` and CicdError for a bad `backup`."""
    skip = guardrails.crunch_skip_envs(guardrails.validate(matrix), raw)
    backup = raw.get("backup") or {}
    if not isinstance(backup, dict):
        raise CicdError(["crunch config.backup: must be a mapping with `policy`"])
    policy = backup.get("policy", "none")
    if policy not in BACKUP_POLICIES:
        raise CicdError([f"crunch config.backup.policy: {policy!r} is not one of "
                         f"{', '.join(BACKUP_POLICIES)}"])
    ends = raw.get("ends_at")
    return {"skip_envs": skip, "backup_policy": policy,
            "ends_at": ends if isinstance(ends, str) else None}


def deploy_plan(matrix: dict) -> list[dict]:
    """One entry per environment that deploys at all, in matrix order:
    {env, production, gated, who, branches, protected}."""
    out = []
    for e in matrix["environments"]:
        acts = set(e["agent"]) | set(e["human"])
        if not acts & set(guardrails.DEPLOY_ACTIONS):
            continue
        agent_deploys = bool({"deploy", "deploy_via_pipeline"} & set(e["agent"]))
        gated = e["production"] or "approve_deploy" in e["human"] or not agent_deploys
        if e["production"]:
            who = "production: agents never deploy here; a person approves every deploy"
        elif gated:
            who = "a person approves each deploy here (the matrix keeps the deploy with a human)"
        elif "deploy" in e["agent"]:
            who = "agents may deploy here directly; the pipeline deploys each push to its branches"
        else:
            who = "agents deploy here only through the pipeline, after a merge to its branches"
        out.append({"env": e["name"], "production": e["production"], "gated": gated, "who": who,
                    "branches": list(e["branches"]), "protected": e["protected"]})
    return out


# --------------------------------------------------------------------------- #
# Templating
# --------------------------------------------------------------------------- #

_PLACEHOLDER = re.compile(r"@@([A-Z_]+)@@")


def _fill(text: str, values: dict[str, str], where: str) -> str:
    """Replace `@@NAME@@`. A placeholder alone on its line takes a value that carries its own
    indentation, and an empty value removes the line."""
    def line_sub(m: re.Match) -> str:
        if m.group(1) not in values:
            return m.group(0)
        v = values[m.group(1)]
        return "" if v == "" else v + "\n"
    text = re.sub(r"(?m)^@@([A-Z_]+)@@[ \t]*\n", line_sub, text)
    text = _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), text)
    left = sorted(set(_PLACEHOLDER.findall(text)))
    if left:
        raise CicdError([f"{where}: placeholder(s) with no value: {', '.join(left)}"])
    return text


def _template(template_dir: Path, platform: str, name: str) -> str:
    p = Path(template_dir) / platform / name
    try:
        return p.read_text(encoding="utf-8")
    except OSError as exc:
        raise CicdError([f"template {p}: cannot be read ({exc.strerror or exc})"]) from None


def _q(s: str) -> str:
    """A YAML double-quoted scalar (JSON's string syntax is a subset of YAML's)."""
    return json.dumps(s)


def _ident(name: str) -> str:
    """An identifier Azure accepts for a stage or job (letters, digits, underscore)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _glob_regex(pattern: str) -> str:
    """A branch glob as a GitLab regex literal body: `*` matches anything."""
    return "^" + ".*".join(re.escape(p).replace("/", r"\/") for p in pattern.split("*")) + "$"


def _all_branches(matrix: dict) -> list[str]:
    out: list[str] = []
    for e in matrix["environments"]:
        out += [b for b in e["branches"] if b not in out]
    return out


def _promotion_note(cicd: dict, plan: list[dict]) -> str:
    order = " -> ".join(p["env"] for p in plan) or "none"
    if cicd["promotion"] == "sequential":
        return (f"Promotion is sequential ({order}): a change reaches the next environment by a "
                f"merge into that environment's branch, never by skipping one.")
    return f"Each environment is deployed on its own ({order})."


def _check_role(c: str, cicd: dict) -> str:
    return ("GATE: must pass before anything deploys and before a merge (branch protection)"
            if c in cicd["gates"] else "information, not a gate: a failure is reported and "
                                       "stops nothing")


# -- GitHub ------------------------------------------------------------------ #

def _gh_ref_cond(branches: list[str]) -> str:
    # Expressions have no glob: a pattern becomes a prefix test on the part before its first
    # `*`, which is the pattern itself for `name/*` and broader for `a*b` (the push trigger's
    # own branch filter, a real glob, still narrows it).
    parts = []
    for b in branches:
        if "*" in b:
            prefix = b.split("*", 1)[0]
            parts.append(f"startsWith(github.ref, 'refs/heads/{prefix}')")
        else:
            parts.append(f"github.ref == 'refs/heads/{b}'")
    return parts[0] if len(parts) == 1 else "(" + " || ".join(parts) + ")"


def _gh_if(p: dict, trigger: str) -> str:
    ref = _gh_ref_cond(p["branches"])
    if not p["production"]:
        return f"github.event_name == 'push' && {ref}"
    return {
        "on_merge": f"github.event_name == 'push' && {ref}",
        "on_tag": "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')",
        "manual": f"github.event_name == 'workflow_dispatch' && {ref}",
        "scheduled": f"github.event_name == 'schedule' && {ref}",
    }[trigger]


_CRUNCH_STEP_NOTE = ("CRUNCH WEEK, backup.policy per_deploy: back up this install's Atlas "
                     "database before every production deploy. A failed backup fails this job, "
                     "so the deploy step below never runs. TODO(atlas): wire the command to "
                     "reach the install (atlas-admin runs where its database is).")


def _backup_step(platform: str, p: dict, crunch: Optional[dict]) -> str:
    """The production deploy job's backup step, or "" (which removes the template's line)."""
    if not crunch or crunch["backup_policy"] != "per_deploy" or not p["production"]:
        return ""
    ind = {"github": "      ", "gitlab": "    ", "azure": "                "}[platform]
    note = "\n".join(ind + "# " + ln for ln in textwrap.wrap(_CRUNCH_STEP_NOTE, 84))
    name = f"backup before the deploy to {p['env']} (crunch week)"
    if platform == "github":
        return f"{note}\n{ind}- name: {_q(name)}\n{ind}  run: {BACKUP_COMMAND}"
    if platform == "gitlab":
        return f"{note}\n{ind}- {BACKUP_COMMAND}"
    return f"{note}\n{ind}- script: {BACKUP_COMMAND}\n{ind}  displayName: {_q(name)}"


def _crunch_note(crunch: Optional[dict]) -> str:
    """The header comment of the crunch variant, or "" (which removes the template's line)."""
    if not crunch:
        return ""
    skipped = ", ".join(crunch["skip_envs"]) or "no environment"
    until = f" until {crunch['ends_at']}" if crunch["ends_at"] else ""
    text = (f"CRUNCH WEEK VARIANT{until}: production deploys after the checks without "
            f"{skipped} (they have no job here). Production keeps its gate: a person approves "
            f"each deploy, exactly as the matrix says. Backup policy: {crunch['backup_policy']}"
            + ("; a backup runs before each production deploy and a failed backup fails it"
               if crunch["backup_policy"] == "per_deploy" else "")
            + ". When crunch week ends, generate again without --crunch and commit that.")
    return "\n".join("# " + ln for ln in textwrap.wrap(text, 94))


def _render_github(matrix, cicd, plan, tdir, crunch=None) -> str:
    check_t = _template(tdir, "github", "check.tmpl")
    deploy_t = _template(tdir, "github", "deploy.tmpl")
    jobs = []
    for c in cicd["checks"]:
        jobs.append(_fill(check_t, {
            "ID": c, "LABEL": CHECK_LABELS[c], "LABEL_Q": _q(CHECK_LABELS[c]),
            "ROLE": _check_role(c, cicd),
            "CONTINUE": "" if c in cicd["gates"] else "    continue-on-error: true"},
            "github/check.tmpl"))
    needs = "    needs: [" + ", ".join(cicd["checks"]) + "]" if cicd["checks"] else ""
    for p in plan:
        gate = ("\n    # GATED: environment " + p["env"] + " must have required reviewers (and "
                "\"prevent self-review\"); see atlas-cicd/github-settings.md." if p["gated"] else "")
        jobs.append(_fill(deploy_t, {
            "ENV": p["env"], "WHO": p["who"], "NEEDS": needs, "IF": _gh_if(p, cicd["deploy_trigger"]),
            "GATE_NOTE": gate, "BACKUP_STEP": _backup_step("github", p, crunch)},
            "github/deploy.tmpl"))
    trig = cicd["deploy_trigger"]
    return _fill(_template(tdir, "github", "pipeline.yml.tmpl"), {
        **_common(matrix, cicd, plan, crunch),
        "PUSH_BRANCHES": "\n".join(f"      - {_q(b)}" for b in _all_branches(matrix)),
        "PUSH_TAGS": f"    tags:\n      - {_q(TAG_PATTERN)}" if trig == "on_tag" else "",
        "SCHEDULE": f"  schedule:\n    - cron: {_q(SCHEDULE_CRON)}" if trig == "scheduled" else "",
        "JOBS": "\n".join(j.rstrip("\n") + "\n" for j in jobs).rstrip("\n"),
    }, "github/pipeline.yml.tmpl")


# -- GitLab ------------------------------------------------------------------ #

def _gl_branch_rules(branches: list[str], extra: str = "") -> list[str]:
    out = []
    for b in branches:
        cond = (f"$CI_COMMIT_BRANCH =~ /{_glob_regex(b)}/" if "*" in b
                else f'$CI_COMMIT_BRANCH == "{b}"')
        out.append(cond + extra)
    return out


def _gl_rules(p: dict, trigger: str) -> list[str]:
    if not p["production"]:
        return _gl_branch_rules(p["branches"], ' && $CI_PIPELINE_SOURCE == "push"')
    return {
        "on_merge": _gl_branch_rules(p["branches"], ' && $CI_PIPELINE_SOURCE == "push"'),
        "on_tag": [f"$CI_COMMIT_TAG =~ /{_glob_regex(TAG_PATTERN)}/"],
        "manual": _gl_branch_rules(p["branches"], ' && $CI_PIPELINE_SOURCE == "web"'),
        "scheduled": _gl_branch_rules(p["branches"], ' && $CI_PIPELINE_SOURCE == "schedule"'),
    }[trigger]


def _gl_tier(p: dict) -> str:
    return "production" if p["production"] else ("staging" if p["gated"] else "testing")


def _yaml_single(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _render_gitlab(matrix, cicd, plan, tdir, crunch=None) -> str:
    check_t = _template(tdir, "gitlab", "check.tmpl")
    deploy_t = _template(tdir, "gitlab", "deploy.tmpl")
    jobs = []
    for c in cicd["checks"]:
        jobs.append(_fill(check_t, {
            "ID": c, "LABEL": CHECK_LABELS[c], "ROLE": _check_role(c, cicd),
            "ALLOW_FAILURE": "" if c in cicd["gates"] else "  allow_failure: true"},
            "gitlab/check.tmpl"))
    for p in plan:
        rules = []
        for cond in _gl_rules(p, cicd["deploy_trigger"]):
            rules.append(f"    - if: {_yaml_single(cond)}")
            if p["gated"]:
                rules.append("      when: manual")
        gate = ("\n  # GATED: `when: manual` makes the job wait for a person (convention); the "
                "protected\n  # environment's deployers and approvals make it a rule "
                "(see atlas-cicd/gitlab-settings.md)." if p["gated"] else "")
        jobs.append(_fill(deploy_t, {
            "ENV": p["env"], "WHO": p["who"], "TIER": _gl_tier(p), "RULES": "\n".join(rules),
            "GATE_NOTE": gate, "BACKUP_STEP": _backup_step("gitlab", p, crunch),
            # A manual job is optional by default; `allow_failure: false` makes the pipeline
            # wait (blocked) for the person instead of passing without the deploy.
            "ALLOW_FAILURE": "  allow_failure: false" if p["gated"] else ""}, "gitlab/deploy.tmpl"))
    # Pipelines started by hand ("web") or by a schedule carry CI_COMMIT_BRANCH, so the
    # template's workflow rules already admit them.
    return _fill(_template(tdir, "gitlab", "pipeline.yml.tmpl"), {
        **_common(matrix, cicd, plan, crunch),
        "JOBS": "\n".join(j.rstrip("\n") + "\n" for j in jobs).rstrip("\n"),
    }, "gitlab/pipeline.yml.tmpl")


# -- Azure ------------------------------------------------------------------- #

def _az_ref_cond(branches: list[str]) -> str:
    parts = []
    for b in branches:
        if "*" in b:
            parts.append(f"startsWith(variables['Build.SourceBranch'], "
                         f"'refs/heads/{b.split('*', 1)[0]}')")
        else:
            parts.append(f"eq(variables['Build.SourceBranch'], 'refs/heads/{b}')")
    return parts[0] if len(parts) == 1 else "or(" + ", ".join(parts) + ")"


def _az_cond(p: dict, trigger: str) -> str:
    ref = _az_ref_cond(p["branches"])
    push = "in(variables['Build.Reason'], 'IndividualCI', 'BatchedCI')"
    if not p["production"]:
        return f"{push}, {ref}"
    return {
        "on_merge": f"{push}, {ref}",
        "on_tag": f"{push}, startsWith(variables['Build.SourceBranch'], 'refs/tags/v')",
        "manual": f"eq(variables['Build.Reason'], 'Manual'), {ref}",
        "scheduled": f"eq(variables['Build.Reason'], 'Schedule'), {ref}",
    }[trigger]


def _render_azure(matrix, cicd, plan, tdir, crunch=None) -> str:
    check_t = _template(tdir, "azure", "check.tmpl")
    deploy_t = _template(tdir, "azure", "deploy.tmpl")
    checks = [_fill(check_t, {
        "JOB": _ident(c), "ID": c, "LABEL": CHECK_LABELS[c], "LABEL_Q": _q(CHECK_LABELS[c]),
        "ROLE": _check_role(c, cicd),
        "CONTINUE": "" if c in cicd["gates"] else "            continueOnError: \"true\""},  # the schema types it as a string
        "azure/check.tmpl") for c in cicd["checks"]]
    stages = []
    for p in plan:
        gate = ("\n      # GATED: environment " + p["env"] + " must have an Approvals check "
                "(see atlas-cicd/azure-settings.md)." if p["gated"] else "")
        stages.append(_fill(deploy_t, {
            "ENV": p["env"], "JOB": _ident(p["env"]), "WHO": p["who"],
            "COND": _az_cond(p, cicd["deploy_trigger"]), "GATE_NOTE": gate,
            "BACKUP_STEP": _backup_step("azure", p, crunch)}, "azure/deploy.tmpl"))
    trig = cicd["deploy_trigger"]
    prod_branches = [b for p in plan if p["production"] for b in p["branches"]]
    schedule = ""
    if trig == "scheduled":
        schedule = ("\nschedules:\n  - cron: " + _q(SCHEDULE_CRON) + "\n    displayName: "
                    "production deploy window\n    branches:\n      include:\n"
                    + "\n".join(f"        - {_q(b)}" for b in prod_branches) + "\n    always: \"true\"")
    return _fill(_template(tdir, "azure", "pipeline.yml.tmpl"), {
        **_common(matrix, cicd, plan, crunch),
        "PUSH_BRANCHES": "\n".join(f"      - {_q(b)}" for b in _all_branches(matrix)),
        "PUSH_TAGS": (f"  tags:\n    include:\n      - {_q(TAG_PATTERN)}" if trig == "on_tag" else ""),
        "SCHEDULE": schedule.lstrip("\n"),
        "CHECK_JOBS": "\n".join(c.rstrip("\n") for c in checks),
        "DEPLOY_STAGES": "\n".join(s.rstrip("\n") for s in stages),
    }, "azure/pipeline.yml.tmpl")


def _common(matrix: dict, cicd: dict, plan: list[dict], crunch: Optional[dict] = None) -> dict:
    prods = [p["env"] for p in plan if p["production"]] or [
        e["name"] for e in matrix["environments"] if e["production"]]
    gated = [p["env"] for p in plan if p["gated"]]
    return {"MATRIX_LABEL": (matrix.get("name") or "unnamed").replace('"', "'"),
            "MATRIX_DIGEST": guardrails.digest(matrix)[:12],
            "PROD_ENVS": ", ".join(prods), "GATED_ENVS": ", ".join(gated) or "no environment",
            "PROMOTION": "\n# ".join(textwrap.wrap(
                _promotion_note(cicd, plan) if not crunch else
                f"Promotion during crunch week: {' and '.join(p['env'] for p in plan)} each "
                f"deploy on their own after the checks; production is not reached through "
                f"the skipped environments.", 94)),
            "CRUNCH_NOTE": _crunch_note(crunch)}


# --------------------------------------------------------------------------- #
# The settings file: what the platform must be told, which the file cannot say
# --------------------------------------------------------------------------- #

TRIGGER_TEXT = {"manual": "a run started by hand", "on_merge": "a merge to its branch",
                "on_tag": f"a `{TAG_PATTERN}` tag", "scheduled": f"the schedule `{SCHEDULE_CRON}`"}


def _gl_var_kind(p: dict) -> str:
    # A PROTECTED variable reaches only pipelines on protected branches and tags, so an
    # environment whose branches are unprotected (agents push there) cannot use one; its
    # credential is still masked and scoped to that one environment.
    return "protected, masked" if p["protected"] else "masked (its branches are unprotected)"


def _settings(platform: str, matrix: dict, cicd: dict, plan: list[dict],
              crunch: Optional[dict] = None) -> str:
    title = PLATFORM_TITLE[platform]
    gates = ", ".join(f"`{g}`" for g in cicd["gates"]) or "none (consider naming some)"
    lines = [f"# {title}: settings this pipeline relies on", "",
             f"Generated by Atlas with `{PIPELINE_PATH[platform]}`. The pipeline file states the "
             f"flow; the settings below are what makes its gates hold, because anyone who can "
             f"push can edit a pipeline file. Configure them before the first deploy.", "",
             f"Gating checks (required before a merge): {gates}.", "",
             "## Environments", "",
             "| environment | deploys | a person approves | credential |", "|---|---|---|---|"]
    for p in plan:
        cred = {"github": f"environment secret `{CREDENTIAL}` on `{p['env']}`",
                "gitlab": f"variable `{CREDENTIAL}`, {_gl_var_kind(p)}, environment scope `{p['env']}`",
                "azure": f"variable group `deploy-{p['env']}` (secret `{CREDENTIAL}`)"}[platform]
        trig = (TRIGGER_TEXT[cicd["deploy_trigger"]] if p["production"]
                else "a push to " + ", ".join(p["branches"]))
        lines.append(f"| {p['env']}{' (production)' if p['production'] else ''} | {trig} | "
                     f"{'yes' if p['gated'] else 'no'} | {cred} |")
    lines += ["", "No deploy credential is ever a repository-wide secret, and none is given to an "
                  "agent: each lives with its environment and is released only to that "
                  "environment's deploy job.", "", "## To configure", ""]
    protected = [e for e in matrix["environments"] if e["protected"]]
    if platform == "github":
        for p in plan:
            lines.append(f"- Settings > Environments > `{p['env']}`: add secret `{CREDENTIAL}`; "
                         f"deployment branches: {', '.join(f'`{b}`' for b in p['branches'])}"
                         + ("; **required reviewers** (people only, never an agent account) and "
                            "**prevent self-review**." if p["gated"] else "."))
        lines.append("- Branch protection: `atlas-cicd/github-branch-protection.json` holds the "
                     "request body per protected branch (`guardrails export` format).")
    elif platform == "gitlab":
        for p in plan:
            lines.append(f"- Settings > CI/CD > Variables: `{CREDENTIAL}`, {_gl_var_kind(p)}, "
                         f"environment scope `{p['env']}`.")
            if p["gated"]:
                lines.append(f"- Settings > CI/CD > Protected environments: protect `{p['env']}`; "
                             f"\"Allowed to deploy\" names people only (never a bot or an agent's "
                             f"account); add required approvals where the tier has them.")
        for e in protected:
            lines.append(f"- Settings > Repository > Protected branches: "
                         f"{', '.join(f'`{b}`' for b in e['branches'])} ({e['name']}): allowed to "
                         f"push: no one; allowed to merge: maintainers; force push off; "
                         f"{e['required_reviews']} approval(s) on merge requests; "
                         f"\"Pipelines must succeed\".")
    else:
        for p in plan:
            lines.append(f"- Pipelines > Library: variable group `deploy-{p['env']}` with secret "
                         f"`{CREDENTIAL}`; authorize only this pipeline"
                         + ("; add the same Approvals check as the environment." if p["gated"]
                            else "."))
            if p["gated"]:
                lines.append(f"- Pipelines > Environments > `{p['env']}` > Approvals and checks: "
                             f"**Approvals** by people only (never an agent's account), approver "
                             f"may not approve their own run.")
        for e in protected:
            lines.append(f"- Repos > Branches > {', '.join(f'`{b}`' for b in e['branches'])} "
                         f"({e['name']}): branch policies: minimum {e['required_reviews']} "
                         f"reviewer(s), build validation with this pipeline, no direct push.")
    lines += ["", "## Agents", "",
              "An agent's own credentials (its Atlas token, its git access) are never given a "
              "deploy role: it may open pull requests, it may not approve a deploy, and it is "
              "never a required reviewer or an allowed deployer. Production (" +
              ", ".join(p["env"] for p in plan if p["production"]) + ") is deployed only after a "
              "person approves.", ""]
    if crunch:
        lines += ["## Crunch week", "",
                  "This is the CRUNCH WEEK VARIANT" + (f", until {crunch['ends_at']}"
                                                       if crunch["ends_at"] else "") + ". "
                  "Production deploys after the checks without "
                  + (", ".join(f"`{n}`" for n in crunch["skip_envs"]) or "any skipped step")
                  + "; those environments have no deploy job here, and their branch settings "
                    "above are unchanged. Production's approval stays exactly as configured.", ""]
        if crunch["backup_policy"] == "per_deploy":
            lines += [f"- Backup policy `per_deploy`: production's deploy job runs "
                      f"`{BACKUP_COMMAND}` before the deploy step. Wire it to reach this "
                      f"install (atlas-admin runs where its database is); until then it fails, "
                      f"and so does every production deploy.", ""]
        else:
            lines += [f"- Backup policy `{crunch['backup_policy']}`: not a pipeline step; the "
                      f"install runs it.", ""]
        lines += ["- When crunch week ends, generate again without `--crunch` and commit the "
                  "normal pipeline.", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Generate, and check what was generated
# --------------------------------------------------------------------------- #

def generate(matrix: dict, cicd: dict, platform: str, *,
             template_dir: Optional[Path] = None, crunch: Optional[dict] = None) -> dict:
    """{platform, files: {path: text}, pipeline_path, plan, matrix_sha256, cicd, digest}, plus
    `crunch` (normalised) for the crunch-week variant.

    Pure: reads only the templates. Raises guardrails.MatrixError for a matrix the guardrails
    refuse (an agent deploy in production among them) and CicdError for anything else,
    including a generated file that `check_pipeline` (or, with `crunch`, `check_crunch`)
    rejects. `crunch` is a crunch config or crunch.current's state; None is today's pipeline."""
    if platform not in PLATFORMS:
        raise CicdError([f"platform {platform!r} is not one of {', '.join(PLATFORMS)}"])
    matrix = guardrails.validate(matrix)
    crunch_n = None
    if crunch is not None:
        crunch_n = normalise_crunch(crunch, matrix)
        matrix = guardrails.crunch_overlay(matrix, crunch)
    cicd = normalise_cicd(cicd)
    plan = deploy_plan(matrix)
    if not plan:
        raise CicdError(["the matrix allows a deploy in no environment, so there is nothing to "
                         "deploy; add deploy actions to the matrix"])
    tdir = Path(template_dir) if template_dir else TEMPLATE_DIR
    render = {"github": _render_github, "gitlab": _render_gitlab, "azure": _render_azure}[platform]
    if crunch_n is None:  # exactly the calls made before the crunch variant existed
        files = {PIPELINE_PATH[platform]: render(matrix, cicd, plan, tdir),
                 f"atlas-cicd/{platform}-settings.md": _settings(platform, matrix, cicd, plan)}
    else:
        files = {PIPELINE_PATH[platform]: render(matrix, cicd, plan, tdir, crunch_n),
                 f"atlas-cicd/{platform}-settings.md": _settings(platform, matrix, cicd, plan,
                                                                 crunch_n)}
    if platform == "github":
        files["atlas-cicd/github-branch-protection.json"] = guardrails.dump(
            guardrails.export_github(matrix))
    problems = check_pipeline(platform, files, matrix)
    if crunch_n is not None:
        problems += check_crunch(platform, files, matrix, crunch_n, cicd)
    if problems:
        raise CicdError(problems)
    out = {"platform": platform, "files": files, "pipeline_path": PIPELINE_PATH[platform],
           "plan": plan, "matrix_sha256": guardrails.digest(matrix), "cicd": cicd,
           "digest": files_digest(files)}
    if crunch_n is not None:
        out["crunch"] = crunch_n
    return out


def files_digest(files: dict[str, str]) -> str:
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(path.encode("utf-8") + b"\0" + files[path].encode("utf-8") + b"\0")
    return h.hexdigest()


def _env_name(v: Any) -> Optional[str]:
    if isinstance(v, str):
        return v
    if isinstance(v, dict) and isinstance(v.get("name"), str):
        return v["name"]
    return None


def _mentions(obj: Any, needle: str) -> bool:
    return needle in json.dumps(obj, default=str)


def check_pipeline(platform: str, files: dict[str, str], matrix: dict) -> list[str]:
    """Every way `files` break the guardrails, as sentences (empty: none).

    A deploy stage exists exactly for each environment that deploys at all; every deploy is
    tied to its environment (where the platform keeps the credential and the approval); a
    gated environment's deploy waits for a person; the credential appears nowhere but in such
    a deploy; and no deploy can run for a pull/merge request."""
    import yaml
    plan = {p["env"]: p for p in deploy_plan(matrix)}
    path = PIPELINE_PATH[platform]
    problems: list[str] = []
    try:
        doc = yaml.safe_load(files.get(path, ""))
    except yaml.YAMLError as exc:
        return [f"{path}: not valid YAML ({exc})"]
    if not isinstance(doc, dict):
        return [f"{path}: not a mapping"]
    found: dict[str, dict] = {}

    if platform == "github":
        on = doc.get("on", doc.get(True))  # YAML 1.1 reads a bare `on` key as true
        if isinstance(on, dict) and ({"pull_request_target", "workflow_run"} & set(on)):
            problems.append(f"{path}: pull_request_target/workflow_run hand secrets to code "
                            f"from a pull request; not allowed")
        perms = doc.get("permissions")
        if perms == "write-all" or (isinstance(perms, dict) and "write" in perms.values()):
            problems.append(f"{path}: workflow permissions must not grant write")
        if _mentions(doc.get("env"), "secrets."):
            problems.append(f"{path}: a secret in the workflow-level env reaches every job")
        for jid, job in (doc.get("jobs") or {}).items():
            env = _env_name((job or {}).get("environment"))
            if _mentions(job, "secrets.") and env is None:
                problems.append(f"{path}: job {jid} uses a secret outside an environment")
            if jid.startswith("deploy-"):
                found[jid[len("deploy-"):]] = {"env": env, "job": job}
        for name, f in found.items():
            job, p = f["job"], plan.get(name)
            if f["env"] != name:
                problems.append(f"{path}: deploy-{name} does not deploy through environment "
                                f"{name!r} (`environment:` missing or different), so no "
                                f"approval and no environment secret apply to it")
            cond = str(job.get("if", ""))
            if not cond or "pull_request" in cond or "github.event_name ==" not in cond:
                problems.append(f"{path}: deploy-{name} needs an `if:` that limits it to a push, "
                                f"a dispatch or a schedule, never a pull request")
            if p and p["gated"]:
                settings_md = files.get("atlas-cicd/github-settings.md", "")
                if f"`{name}`" not in settings_md or "required reviewers" not in settings_md:
                    problems.append(f"github-settings.md: environment {name} is gated but the "
                                    f"settings do not ask for required reviewers on it")

    elif platform == "gitlab":
        reserved = {"workflow", "stages", "default", "variables", "include", "image", "services",
                    "before_script", "after_script", "cache"}
        if _mentions(doc.get("variables"), CREDENTIAL):
            problems.append(f"{path}: {CREDENTIAL} in the global variables reaches every job")
        for jid, job in doc.items():
            if jid in reserved or not isinstance(job, dict):
                continue
            env = _env_name(job.get("environment"))
            if _mentions(job, CREDENTIAL) and env is None:
                problems.append(f"{path}: job {jid} uses {CREDENTIAL} without an environment")
            if jid.startswith("deploy-"):
                found[jid[len("deploy-"):]] = {"env": env, "job": job}
        for name, f in found.items():
            job, p = f["job"], plan.get(name)
            if f["env"] != name:
                problems.append(f"{path}: deploy-{name} does not deploy to environment {name!r}")
            rules = job.get("rules")
            if not isinstance(rules, list) or not rules:
                problems.append(f"{path}: deploy-{name} has no rules, so it runs in every "
                                f"pipeline, merge request pipelines included")
                rules = []
            for r in rules:
                if "merge_request" in str(r.get("if", "")):
                    problems.append(f"{path}: deploy-{name} runs for a merge request")
            if p and p["gated"]:
                if job.get("when") not in (None, "manual") or not rules or any(
                        r.get("when") not in ("manual", "never") for r in rules):
                    problems.append(f"{path}: deploy-{name} is gated but a rule starts it "
                                    f"without a person (`when: manual` missing)")
                if job.get("allow_failure") is not False:
                    problems.append(f"{path}: deploy-{name} is a manual gate that the pipeline "
                                    f"may skip (`allow_failure: false` missing)")

    else:  # azure
        if _mentions(doc.get("variables"), "deploy-") or _mentions(doc.get("variables"), CREDENTIAL):
            problems.append(f"{path}: a deploy variable group at the top level reaches every job")
        for st in doc.get("stages") or []:
            for job in (st or {}).get("jobs") or []:
                if not isinstance(job, dict):
                    continue
                is_dep = "deployment" in job
                env = _env_name(job.get("environment")) if is_dep else None
                jid = job.get("deployment") or job.get("job") or "?"
                if (_mentions(job.get("variables"), "deploy-") or _mentions(job, "$(" + CREDENTIAL))\
                        and env is None:
                    problems.append(f"{path}: job {jid} uses a deploy credential outside a "
                                    f"deployment job with an environment")
                stage_id = str(st.get("stage", ""))
                if stage_id.startswith("deploy_"):
                    cond = str(st.get("condition", ""))
                    if "'PullRequest'" not in cond or "ne(variables['Build.Reason']" not in cond:
                        problems.append(f"{path}: stage {stage_id} may run for a pull request")
                    found[stage_id[len("deploy_"):]] = {"env": env, "job": job}
        by_ident = {_ident(n): n for n in plan}
        found = {by_ident.get(k, k): v for k, v in found.items()}
        for name, f in found.items():
            if f["env"] != name:
                problems.append(f"{path}: deploy_{_ident(name)} is not a deployment job on "
                                f"environment {name!r}, so its approvals do not apply")

    for name in sorted(set(plan) - set(found)):
        problems.append(f"{path}: no deploy stage for {name}, which the matrix lets deploy")
    for name in sorted(set(found) - set(plan)):
        problems.append(f"{path}: a deploy stage for {name}, which the matrix does not let deploy")
    return problems


def _is_backup(text: Any) -> bool:
    return BACKUP_COMMAND in str(text or "")


def check_crunch(platform: str, files: dict[str, str], matrix: dict, crunch: dict,
                 cicd: dict) -> list[str]:
    """Every way a crunch-week variant breaks its rules, as sentences (empty: none). `matrix`
    is the OVERLAID matrix, `crunch` the normalised config (`normalise_crunch`).

    No job or stage for a skipped environment; each production deploy depends on the checks
    and nothing else; with `per_deploy`, each production deploy job runs the backup command as
    its own step before the step that deploys, in the same job, and neither may continue on
    error; without `per_deploy`, no backup step anywhere. Production's gate is
    `check_pipeline`'s, run on the same files."""
    import yaml
    path = PIPELINE_PATH[platform]
    try:
        doc = yaml.safe_load(files.get(path, ""))
    except yaml.YAMLError as exc:
        return [f"{path}: not valid YAML ({exc})"]
    if not isinstance(doc, dict):
        return [f"{path}: not a mapping"]
    problems: list[str] = []
    prods = {e["name"] for e in matrix["environments"] if e["production"]}
    skipped = set(crunch["skip_envs"])
    per_deploy = crunch["backup_policy"] == "per_deploy"
    checks = set(cicd["checks"])
    # (env name, the job, its ordered steps or script lines, what it waits for)
    deploys: list[tuple[str, dict, list, Any]] = []

    if platform == "github":
        for jid, job in (doc.get("jobs") or {}).items():
            if jid.startswith("deploy-") and isinstance(job, dict):
                needs = job.get("needs")
                deploys.append((jid[len("deploy-"):], job, job.get("steps") or [],
                                [needs] if isinstance(needs, str) else needs))
    elif platform == "gitlab":
        for jid, job in doc.items():
            if isinstance(jid, str) and jid.startswith("deploy-") and isinstance(job, dict):
                # A job with no `needs` waits for every job of the earlier stages: the checks.
                deploys.append((jid[len("deploy-"):], job, job.get("script") or [],
                                job.get("needs", list(checks))))
        stages = doc.get("stages") or []
        if stages != ["check", "deploy"]:
            problems.append(f"{path}: stages must be [check, deploy] (got {stages!r}), so "
                            f"production's deploy waits for the checks and nothing else")
    else:
        by_ident = {_ident(e["name"]): e["name"] for e in matrix["environments"]}
        for st in doc.get("stages") or []:
            sid = str((st or {}).get("stage", ""))
            if not sid.startswith("deploy_"):
                continue
            name = by_ident.get(sid[len("deploy_"):], sid[len("deploy_"):])
            dep = st.get("dependsOn")
            for job in st.get("jobs") or []:
                if isinstance(job, dict) and "deployment" in job:
                    steps = (((job.get("strategy") or {}).get("runOnce") or {}).get("deploy")
                             or {}).get("steps") or []
                    deploys.append((name, job, steps, ["checks"] if dep == "checks" else dep))
            if not any(d[0] == name for d in deploys):
                deploys.append((name, {}, [], dep))

    for name, job, steps, waits in deploys:
        if name in skipped:
            problems.append(f"{path}: a deploy for {name}, which crunch week skips")
        if name not in prods:
            continue
        allowed = {"checks"} if platform == "azure" else checks
        if not isinstance(waits, list) or set(map(str, waits)) - allowed:
            problems.append(f"{path}: production's deploy ({name}) must depend on the checks "
                            f"alone during crunch week (it waits for {waits!r})")
        if job.get("continue-on-error") or job.get("continueOnError") not in (None, False, "false"):
            problems.append(f"{path}: production's deploy ({name}) may continue on error")
        backup_at = [i for i, st in enumerate(steps) if _is_backup(
            st if isinstance(st, str) else (st or {}).get("run") or (st or {}).get("script"))]
        deploy_at = [i for i, st in enumerate(steps) if CREDENTIAL in json.dumps(st, default=str)]
        if not per_deploy:
            if backup_at:
                problems.append(f"{path}: a backup step in {name}'s deploy, but the backup "
                                f"policy is {crunch['backup_policy']}, not per_deploy")
            continue
        if not backup_at:
            problems.append(f"{path}: backup.policy is per_deploy but production's deploy "
                            f"({name}) runs no `{BACKUP_COMMAND}` step")
            continue
        if not deploy_at or min(backup_at) > min(deploy_at):
            problems.append(f"{path}: production's deploy ({name}) must run the backup BEFORE "
                            f"the deploy step, so a failed backup stops it")
        for st in steps[min(backup_at) + 1:]:
            # A later step with its own `if:`/`condition:` (always(), failure()...) could run
            # after a failed backup; without one, a failed step skips every later step.
            if isinstance(st, dict) and ("if" in st or "condition" in st):
                problems.append(f"{path}: a step after the backup in {name}'s deploy has its "
                                f"own condition, so it may run after a failed backup")
        for i in backup_at:
            st = steps[i]
            if isinstance(st, dict) and (st.get("continue-on-error") or st.get("condition")
                                         or st.get("continueOnError") not in (None, False, "false")):
                problems.append(f"{path}: the backup step in {name}'s deploy may fail without "
                                f"stopping the deploy (continue-on-error / condition)")
            elif isinstance(st, str) and ("||" in st or st.rstrip().endswith("; true")):
                problems.append(f"{path}: the backup line in {name}'s deploy swallows a failure")
    if platform == "gitlab" and per_deploy:
        for name, job, _, _ in deploys:
            if name in prods and job.get("allow_failure") is not False:
                problems.append(f"{path}: production's deploy ({name}) may fail without "
                                f"stopping the pipeline (`allow_failure: false` missing)")
    for name in sorted(prods - {d[0] for d in deploys}):
        problems.append(f"{path}: no production deploy for {name} in the crunch variant")
    return problems


def write_files(files: dict[str, str], out_dir: str, *, force: bool = False) -> list[str]:
    """Write the mapping under `out_dir` and nothing else. Refuses a path that leaves it, and
    an existing file unless `force`. Returns the paths written."""
    root = Path(out_dir).resolve()
    targets = []
    for rel in sorted(files):
        t = (root / rel).resolve()
        if root != t and root not in t.parents:
            raise CicdError([f"{rel}: outside {out_dir}"])
        if t.exists() and not force:
            raise CicdError([f"{t} exists; pass --force to overwrite it"])
        targets.append((rel, t))
    for rel, t in targets:
        t.parent.mkdir(parents=True, exist_ok=True)
        with open(t, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(files[rel])
    return [str(t) for _, t in targets]


# --------------------------------------------------------------------------- #
# The Proposed ADR (allocated and written through adr_alloc)
# --------------------------------------------------------------------------- #

def render_adr(pipeline: dict, matrix_label: str, supersedes: Optional[str]) -> tuple[str, str, str]:
    """(title, slug, markdown) of the Proposed ADR describing one generated pipeline."""
    platform, d = pipeline["platform"], pipeline["digest"][:12]
    c = pipeline["cicd"]
    title = f"First CI/CD pipeline for {PLATFORM_TITLE[platform]} (files {d})"
    lines = [f"# {title}", "",
             "Status: Proposed. A human accepts it; generating a pipeline never does.", "",
             "## Context", "",
             f"The setup guide's CI/CD answers and the guardrails matrix \"{matrix_label}\" "
             f"(sha256 {pipeline['matrix_sha256'][:12]}) describe how changes are checked and "
             f"deployed. `atlas-admin cicd generate --platform {platform}` turned them into files "
             f"for the installer to review and commit; Atlas pushed nothing to any repository.", "",
             "## Decision", "",
             f"Checks: {', '.join(c['checks'])}. Gates: {', '.join(c['gates']) or 'none'}. "
             f"Production deploys on: {c['deploy_trigger']}. Promotion: {c['promotion']}.", "",
             "| environment | deploys | a person approves |", "|---|---|---|"]
    for p in pipeline["plan"]:
        lines.append(f"| {p['env']}{' (production)' if p['production'] else ''} | "
                     f"{TRIGGER_TEXT[c['deploy_trigger']] if p['production'] else 'a push to ' + ', '.join(p['branches'])} | "
                     f"{'yes' if p['gated'] else 'no'} |")
    lines += ["", "Agents never hold a deploy credential and never deploy to production: every "
                  "credential is held by the platform for one environment, and a production "
                  "deploy waits for a person.", "",
              "## Consequences", "",
              f"- The files, with their sha256: " + "; ".join(
                  f"`{p}` {hashlib.sha256(t.encode('utf-8')).hexdigest()[:12]}"
                  for p, t in sorted(pipeline["files"].items())) + ".",
              f"- The gates hold only once the settings in `atlas-cicd/{platform}-settings.md` "
              f"exist on the platform; the file alone is convention.",
              "- Generating again from changed answers or a changed matrix proposes a new ADR "
              "that supersedes this one."]
    if supersedes:
        lines.append(f"- Supersedes {supersedes}.")
    return title, f"{ADR_SLUG_PREFIX}{platform}-{d}", "\n".join(lines) + "\n"


def record_adr(s, pipeline: dict, *, matrix_label: str = "", operator: str = "unknown",
               adr_prefix: str = guardrails.DEFAULT_ADR_PREFIX) -> list[str]:
    """One Proposed ADR for `pipeline` on atlas-system. Returns one line per change (empty:
    this pipeline is already recorded). Commits on success."""
    from sqlalchemy import select
    import adr_alloc
    import deps
    from models_adr import ADR_SOURCE_ATLAS, AdrRecord
    from models_graph import Node

    node_id = guardrails.SYSTEM_NODE
    if s.get(Node, node_id) is None:
        raise guardrails.GuardrailsError(
            f"`{node_id}` is not in this graph, and pipeline ADRs are recorded on it. Add the "
            f"system node set first: `python system_node.py --apply`, then run again.")
    via = "atlas-admin cicd generate --record-adr"

    def event(**detail) -> None:
        deps.record_graph_event(s, "node.adr.write", entity_type="node", entity_id=node_id,
                                detail={**detail, "operator": operator, "via": via})

    platform = pipeline["platform"]
    prev = s.scalar(select(AdrRecord).where(
        AdrRecord.project_node_id == node_id, AdrRecord.source == ADR_SOURCE_ATLAS,
        AdrRecord.slug.like(f"{ADR_SLUG_PREFIX}{platform}-%")).order_by(AdrRecord.seq.desc()).limit(1))
    _, slug, _ = render_adr(pipeline, matrix_label, None)
    if prev is not None and prev.slug == slug:
        return []
    # Serialise ADR allocation in this project, as PUT /api/adrs/new does (adr_alloc).
    adr_alloc.lock_project(s, node_id)
    own = s.scalar(select(AdrRecord.prefix).where(AdrRecord.project_node_id == node_id).limit(1))
    prefix = own or adr_prefix
    if not re.fullmatch(r"[A-Z]{2,4}", prefix) or prefix == "ADR":
        raise guardrails.GuardrailsError(f"ADR prefix {prefix!r} must be 2-4 capital letters, not ADR")
    taken = s.scalar(select(AdrRecord.project_node_id).where(
        AdrRecord.prefix == prefix, AdrRecord.project_node_id != node_id).limit(1))
    if taken is not None:
        raise guardrails.GuardrailsError(f"ADR prefix {prefix!r} already belongs to {taken!r}; "
                                         f"pass --adr-prefix with another")
    seq, adr_id = adr_alloc.allocate(s, node_id, prefix)
    title, slug, md = render_adr(pipeline, matrix_label, prev.adr_id if prev else None)
    now = datetime.now(timezone.utc)
    # The row and its node.adr.write, through the insert PUT /api/adrs/new uses.
    adr_alloc.add_atlas_adr(
        s, adr_id=adr_id, project=node_id, prefix=prefix, seq=seq, title=title, slug=slug,
        status="proposed", status_raw=None, date=now.date(), content=md, now=now,
        supersedes=[prev.adr_id] if prev else [], superseded_by=[], amends=[],
        extra_detail={"operator": operator, "via": via})
    changes = [f"create Proposed ADR {adr_id} ({title})"
               + (f", superseding {prev.adr_id}" if prev else "")]
    if prev is not None:
        fields = ["superseded_by"] + (["status"] if prev.status == "proposed" else [])
        prev.superseded_by = list(prev.superseded_by or []) + [adr_id]
        if prev.status == "proposed":
            prev.status = "superseded"
        prev.synced_at = now
        event(adr_id=prev.adr_id, created=False, fields=fields, bytes=len(prev.content or ""),
              source=ADR_SOURCE_ATLAS)
        changes.append(f"mark {prev.adr_id} superseded_by {adr_id}")
    s.commit()
    return changes


__all__ = ["PLATFORMS", "PIPELINE_PATH", "CicdError", "normalise_cicd", "deploy_plan",
           "generate", "check_pipeline", "write_files", "files_digest", "render_adr", "record_adr",
           "normalise_crunch", "check_crunch", "BACKUP_COMMAND"]
