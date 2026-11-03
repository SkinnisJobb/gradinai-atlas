"""Agent guardrails per branch and environment, as data (STORY-atlas-guardrails-per-env).

An install states what agents may do in each of ITS OWN environments in one file, the matrix.
This module validates it, applies it to the install's `atlas-system` node, renders it for agents
(one `constraint` intent per environment, which `atlas_get` returns on the node and, inherited,
on every part below it), records it as a Proposed Atlas-native ADR, and exports it as agent
policy files. Nothing here runs at boot and nothing applies the example: an install that never
applies a matrix has no guardrail document, intent or ADR, exactly as before.

    atlas-admin guardrails validate FILE
    atlas-admin guardrails apply FILE [--dry-run] [--adr-prefix SYS]
    atlas-admin guardrails show [--crunch FILE | --crunch-from-db]
    atlas-admin guardrails export --format claude-code|github-branch-protection
                                  [--env NAME ...] [--from FILE] --out PATH|-
                                  [--crunch FILE | --crunch-from-db]

`--crunch FILE` / `--crunch-from-db` show or export the matrix as crunch week runs it
(`crunch_overlay`, below the exports); with crunch week off, `--crunch-from-db` changes nothing.

WHERE IT LIVES (decided 2026-10-26). On `atlas-system`, the node every install has for itself
(system_node.py), never on a homelab's own `atlas` project. Apply refuses when `atlas-system`
is missing and says how to add it.

────────────────────────────────────────────────────────────────────────────────
THE FORMAT (JSON or YAML; `version: 1`)
────────────────────────────────────────────────────────────────────────────────
    version: 1                      required; the only version this module reads
    name: "..."                     optional, a label for the ADR and the document
    custom_actions:                 optional: actions outside the vocabulary, name -> meaning
      run_migration: "apply a database migration"
    environments:                   required, ORDERED (first = closest to the developer)
      - name: dev                   lowercase kebab, unique, at most 40 characters
        branches: [dev, "feature/*"]   at least one; a pattern may end in or contain `*`;
                                    no pattern may appear in two environments
        production: false           at least ONE environment must be marked production
        protected: true             default: true unless agents may `merge` here
        agent: [branch, commit]     what an agent may do here on its own
        human: [merge]              what needs a human here (an agent may only propose it)
        step_up: [deploy]           of `human`: the ones that need a step-up sign-in too
        required_reviews: 1         default 1 when protected, else 0 (0..6, GitHub's range)
        required_checks: []         CI check names a protected branch requires
        commands:                   optional: command patterns per (vocabulary) action, which
          deploy: ["tools/deploy.sh prod*"]   the Claude Code export allows or denies
        notes: "..."                optional free text, carried into the intent

An action that is in neither `agent` nor `human` is not done in that environment at all; for
an agent that is the same as denied.

THE ACTION VOCABULARY (`ACTIONS`): branch, commit, push, open_pr, merge, deploy,
deploy_via_pipeline, read_logs, propose, approve_deploy.

CUSTOM ACTIONS are allowed, declared under `custom_actions` (an undeclared unknown name is an
error, so a typo such as `deploi` never passes as a new action). They are carried into the
intents, the document and the ADR, so agents read them; the EXPORTS IGNORE THEM, because an
export can only translate an action it knows the meaning of, and so `commands` may not name one.

REFUSALS THAT ENCODE A STANDING DECISION, not just shape:
  * an agent never holds deploy rights in a production environment: `deploy`,
    `deploy_via_pipeline` and `approve_deploy` may not be in a production environment's `agent`;
  * approving a deploy is a human act: `approve_deploy` is never in any `agent` list;
  * a protected production environment requires at least one review.

────────────────────────────────────────────────────────────────────────────────
APPLY, AND WHAT IT WRITES (all on `atlas-system`, each change one `graph_event`)
────────────────────────────────────────────────────────────────────────────────
  * the document `docs/nodes/atlas-system/guardrails.md`, listed in the node's `docs[]`: a
    readable table plus the normalised matrix as a fenced JSON block, which `show` and `export`
    read back (docs[] formats are mermaid/openapi/md/svg, so the data rides inside markdown);
  * one `constraint` intent per environment, slug `guardrail-<env>`, its statement generated
    from the matrix and its source naming the matrix version, digest and file; an environment
    removed from the matrix has its `guardrail-` intent deleted;
  * a Proposed Atlas-native ADR (`source = "atlas"`), slug `agent-guardrails-<digest>`.

IDEMPOTENT. The digest is the sha256 of the normalised matrix. Re-applying the same file
compares every row with what it would write and writes nothing (no row, no event). A changed
matrix updates the document and the intents and creates a NEW Proposed ADR that `supersedes`
the previous guardrails ADR; the previous one gets `superseded_by`, and if it was still
`proposed` its status becomes `superseded` (an accepted one keeps `accepted` until a human
accepts the new one: accepting is not this command's act).

THE ACTOR. A CLI write has no request principal, so the one writer
(`deps.record_graph_event`) records it as `actor_kind = "system"`, the documented value for a
principal-less write. The operator (`--actor`, `ATLAS_ADMIN_ACTOR`, else the OS user) and the
verb travel in each event's `detail` as `operator` and `via`.

THE ADR ID. `atlas-system`'s ADR prefix if it has ADRs already, else `--adr-prefix`
(default `SYS`), allocated by the same code `PUT /api/adrs/new` uses (`adr_alloc.py`): lock
the project row, then the project's highest sequence + 1, and the same insert and event.

────────────────────────────────────────────────────────────────────────────────
EXPORT
────────────────────────────────────────────────────────────────────────────────
claude-code: a settings fragment `{"permissions": {"allow": [...], "deny": [...]}}` to merge
into a `.claude/settings.json`. Deny rules: pushes to the branches of every protected
environment where agents may not push; force pushes wherever any branch is protected;
`gh pr merge` unless agents may merge in every selected environment; and each `commands`
pattern of an action agents may NOT take in that environment (an allowed one becomes an allow
rule). Deny wins over allow in Claude Code. These rules are defence in depth for an agent's
own session: the boundary is the branch protection, which a local setting cannot weaken.

github-branch-protection: per protected branch pattern, the JSON body for
`PUT /repos/{owner}/{repo}/branches/{branch}/protection` (required reviews, no force push, no
deletion, admins included on production, the required checks; an empty list is the
placeholder to fill). A pattern with `*` cannot go through that endpoint, which takes one
branch name, and is marked for a ruleset or GraphQL `createBranchProtectionRule` instead.

`--env` restricts either export to the named environments.

────────────────────────────────────────────────────────────────────────────────
A MATRIX FROM ANSWERS (`matrix_from_answers`) — a proposal for the setup guide
────────────────────────────────────────────────────────────────────────────────
A pure function: answers in, a matrix (validated) out. The keys it reads:

    environments      list of names, in order (required, at least one)
    production        the production environment's name (default: the last one)
    branches          {env: [patterns]} (default: [env]; the production one ["main"])
    agent_deploys     envs where an agent deploys directly (default: the first, unless it is
                      the production one)
    human_approves    envs where agents only propose and a human approves the deploy
                      (default: the one before production, when there are three or more)
    pipeline_deploys  envs where agents deploy only through the pipeline after a merge
                      (default: every remaining non-production env)
    unprotected       envs whose branches are not protected (default: agent_deploys)
    step_up           a production deploy needs a step-up sign-in (default true)
    required_reviews  reviews on a protected branch (default 1)
    required_checks   CI checks a protected branch requires (default [])
    deploy_command    a pattern with `{env}`, e.g. "tools/deploy.sh {env}*": added as the
                      `deploy` command of every env where a human deploys (default none)
    name              the matrix's label

An environment named in several lists takes the first of: production, agent_deploys,
human_approves, pipeline_deploys. With the example's five names, its name,
`branches: {dev: [dev, "feature/*"]}` and `deploy_command: "tools/deploy.sh {env}*"`, the
result equals `data/guardrails/example-default.yaml` (verify_guardrails.py checks it).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

MATRIX_VERSION = 1
SYSTEM_NODE = "atlas-system"
DOC_PATH = f"docs/nodes/{SYSTEM_NODE}/guardrails.md"
DOC_ENTRY = {"path": DOC_PATH, "kind": "design", "title": "Agent guardrails matrix", "format": "md"}
INTENT_PREFIX = "guardrail-"
ADR_SLUG_PREFIX = "agent-guardrails-"
DEFAULT_ADR_PREFIX = "SYS"
EXAMPLE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "guardrails",
                            "example-default.yaml")

#: The action vocabulary, with what each means. The exports understand exactly these.
ACTIONS: dict[str, str] = {
    "branch": "create a branch",
    "commit": "commit",
    "push": "push to the environment's branches",
    "open_pr": "open a pull request",
    "merge": "merge a pull request into the environment's branches",
    "deploy": "deploy directly",
    "deploy_via_pipeline": "deploy only through the pipeline, after a merge",
    "read_logs": "read logs",
    "propose": "propose a change (an issue, a comment, a draft) without making it",
    "approve_deploy": "approve a deploy",
}
DEPLOY_ACTIONS = ("deploy", "deploy_via_pipeline", "approve_deploy")
FORMATS = ("claude-code", "github-branch-protection")

_TOP_KEYS = {"version", "name", "description", "custom_actions", "environments"}
_ENV_KEYS = {"name", "branches", "production", "protected", "agent", "human", "step_up",
             "required_reviews", "required_checks", "commands", "notes"}
_ENV_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_ACTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/*-]{1,200}$")


class MatrixError(ValueError):
    """An invalid matrix. `errors` lists every problem found, each naming where it is."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("invalid guardrails matrix:\n  - " + "\n  - ".join(self.errors))


class GuardrailsError(RuntimeError):
    """A refusal the operator can act on (no atlas-system, no stored matrix, a taken prefix)."""


# --------------------------------------------------------------------------- #
# Loading and validation (pure)
# --------------------------------------------------------------------------- #

def parse_text(text: str, filename: str = "") -> Any:
    """JSON if it parses as JSON (or the file is .json), else YAML."""
    if filename.lower().endswith(".json"):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise MatrixError([f"not valid JSON: {exc}"]) from None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    import yaml
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MatrixError([f"not valid JSON or YAML: {exc}"]) from None


def load_file(path: str) -> dict:
    """Read, parse and validate a matrix file; returns the normalised matrix."""
    with open(path, encoding="utf-8") as fh:
        return validate(parse_text(fh.read(), path))


def _str_list(errors: list[str], where: str, value: Any, *, allow_empty: bool = True) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        errors.append(f"{where}: must be a list of non-empty strings")
        return []
    if not allow_empty and not value:
        errors.append(f"{where}: must name at least one")
    dup = sorted({v for v in value if value.count(v) > 1})
    if dup:
        errors.append(f"{where}: listed twice: {', '.join(dup)}")
    return [v.strip() for v in value]


def validate(raw: Any) -> dict:
    """Check a parsed matrix and return it NORMALISED (every default filled in, lists in the
    order given). Raises MatrixError listing every problem, not only the first."""
    errors: list[str] = []
    if not isinstance(raw, dict):
        raise MatrixError(["the matrix must be a mapping with `version` and `environments`"])
    extra = sorted(set(raw) - _TOP_KEYS)
    if extra:
        errors.append(f"unknown top-level key(s): {', '.join(extra)} "
                      f"(allowed: {', '.join(sorted(_TOP_KEYS))})")
    version = raw.get("version")
    if version != MATRIX_VERSION:
        errors.append(f"version: must be {MATRIX_VERSION} (got {version!r})")
    name = raw.get("name") or ""
    if not isinstance(name, str):
        errors.append("name: must be a string")
        name = ""
    description = raw.get("description") or ""
    if not isinstance(description, str):
        errors.append("description: must be a string")
        description = ""

    custom: dict[str, str] = {}
    craw = raw.get("custom_actions") or {}
    if not isinstance(craw, dict):
        errors.append("custom_actions: must be a mapping of action name to what it means")
        craw = {}
    for k, v in craw.items():
        if not isinstance(k, str) or not _ACTION_NAME.match(k):
            errors.append(f"custom_actions: {k!r} is not a valid action name "
                          f"(lowercase, digits and _, starting with a letter, at most 40)")
        elif k in ACTIONS:
            errors.append(f"custom_actions.{k}: is already in the vocabulary; do not redeclare it")
        elif not isinstance(v, str) or not v.strip():
            errors.append(f"custom_actions.{k}: needs a description (what the action means)")
        else:
            custom[k] = v.strip()
    known = set(ACTIONS) | set(custom)

    envs_raw = raw.get("environments")
    envs: list[dict] = []
    if not isinstance(envs_raw, list) or not envs_raw:
        errors.append("environments: must be a non-empty list")
        envs_raw = []
    seen_names: set[str] = set()
    branch_owner: dict[str, str] = {}
    for i, e in enumerate(envs_raw):
        where = f"environments[{i}]"
        if not isinstance(e, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        ename = e.get("name")
        if not isinstance(ename, str) or not _ENV_NAME.match(ename):
            errors.append(f"{where}.name: {ename!r} must be lowercase kebab-case, 1-40 characters")
            ename = f"#{i}"
        elif ename in seen_names:
            errors.append(f"{where}.name: {ename!r} is used twice")
        seen_names.add(ename)
        where = f"environments[{ename}]"
        extra = sorted(set(e) - _ENV_KEYS)
        if extra:
            errors.append(f"{where}: unknown key(s): {', '.join(extra)} "
                          f"(allowed: {', '.join(sorted(_ENV_KEYS))})")
        branches = _str_list(errors, f"{where}.branches", e.get("branches"), allow_empty=False)
        if e.get("branches") is None:
            errors.append(f"{where}.branches: required (at least one branch or pattern)")
        for b in branches:
            if not _BRANCH.match(b):
                errors.append(f"{where}.branches: {b!r} has a character a branch name cannot hold")
            elif b in branch_owner and branch_owner[b] != ename:
                errors.append(f"{where}.branches: {b!r} is already {branch_owner[b]!r}'s")
            else:
                branch_owner[b] = ename
        lists = {}
        for key in ("agent", "human", "step_up"):
            lists[key] = _str_list(errors, f"{where}.{key}", e.get(key))
            for a in lists[key]:
                if a not in known:
                    errors.append(f"{where}.{key}: {a!r} is not an action (vocabulary: "
                                  f"{', '.join(ACTIONS)}; declare others under custom_actions)")
        both = sorted(set(lists["agent"]) & set(lists["human"]))
        if both:
            errors.append(f"{where}: in both agent and human: {', '.join(both)} "
                          f"(an action is the agent's or needs a human, not both)")
        loose = sorted(set(lists["step_up"]) - set(lists["human"]))
        if loose:
            errors.append(f"{where}.step_up: {', '.join(loose)} must also be in human "
                          f"(step-up is how a human signs in for it)")
        production = e.get("production", False)
        if not isinstance(production, bool):
            errors.append(f"{where}.production: must be true or false")
            production = False
        if "approve_deploy" in lists["agent"]:
            errors.append(f"{where}.agent: approve_deploy is a human act; move it to human")
        if production:
            held = [a for a in DEPLOY_ACTIONS if a in lists["agent"]]
            if held:
                errors.append(f"{where}.agent: agents never hold deploy rights in a production "
                              f"environment; remove {', '.join(held)}")
        protected = e.get("protected", "merge" not in lists["agent"])
        if not isinstance(protected, bool):
            errors.append(f"{where}.protected: must be true or false")
            protected = True
        reviews = e.get("required_reviews", 1 if protected else 0)
        if not isinstance(reviews, int) or isinstance(reviews, bool) or not 0 <= reviews <= 6:
            errors.append(f"{where}.required_reviews: must be a whole number 0..6")
            reviews = 1
        if production and protected and reviews < 1:
            errors.append(f"{where}.required_reviews: a protected production environment needs "
                          f"at least one human review")
        checks = _str_list(errors, f"{where}.required_checks", e.get("required_checks"))
        commands: dict[str, list[str]] = {}
        craw_env = e.get("commands") or {}
        if not isinstance(craw_env, dict):
            errors.append(f"{where}.commands: must be a mapping of action to command patterns")
            craw_env = {}
        for a, pats in craw_env.items():
            if a in custom:
                errors.append(f"{where}.commands.{a}: a custom action is ignored by the exports, "
                              f"so it cannot carry commands")
            elif a not in ACTIONS:
                errors.append(f"{where}.commands.{a}: {a!r} is not an action")
            else:
                got = _str_list(errors, f"{where}.commands.{a}", pats, allow_empty=False)
                if got:
                    commands[a] = got
        notes = e.get("notes") or ""
        if not isinstance(notes, str):
            errors.append(f"{where}.notes: must be a string")
            notes = ""
        if len(INTENT_PREFIX + ename) > 64:
            errors.append(f"{where}.name: too long for an intent slug")
        envs.append({
            "name": ename, "branches": branches, "production": production,
            "protected": protected, "agent": lists["agent"], "human": lists["human"],
            "step_up": lists["step_up"], "required_reviews": reviews,
            "required_checks": checks, "commands": commands, "notes": notes.strip(),
        })
    if envs_raw and not any(e.get("production") is True for e in envs_raw if isinstance(e, dict)):
        errors.append("environments: mark the production environment with `production: true` "
                      "(at least one is required)")
    if errors:
        raise MatrixError(errors)
    return {"version": MATRIX_VERSION, "name": name.strip(), "description": description.strip(),
            "custom_actions": custom, "environments": envs}


def canonical(matrix: dict) -> str:
    """The normalised matrix as canonical JSON: what the digest is taken over."""
    return json.dumps(matrix, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(matrix: dict) -> str:
    return hashlib.sha256(canonical(matrix).encode("utf-8")).hexdigest()


def _env(matrix: dict, name: str) -> dict:
    for e in matrix["environments"]:
        if e["name"] == name:
            return e
    raise GuardrailsError(f"no environment {name!r} in the matrix "
                          f"(it has: {', '.join(e['name'] for e in matrix['environments'])})")


def select_envs(matrix: dict, names: Optional[Iterable[str]]) -> list[dict]:
    names = list(names or [])
    return [_env(matrix, n) for n in names] if names else list(matrix["environments"])


# --------------------------------------------------------------------------- #
# Rendering: intents, the document, the ADR (pure)
# --------------------------------------------------------------------------- #

def _code(items: Iterable[str]) -> str:
    items = list(items)
    return ", ".join(f"`{i}`" for i in items) if items else "nothing"


def intent_for(matrix: dict, env: dict, source: str) -> dict:
    """The `constraint` intent stating one environment's guardrails."""
    known = list(ACTIONS) + list(matrix["custom_actions"])
    denied = [a for a in known if a not in env["agent"] and a not in env["human"]]
    human = [f"`{a}`" + (" (with step-up)" if a in env["step_up"] else "") for a in env["human"]]
    kind_bits = []
    if env["production"]:
        kind_bits.append("production")
    kind_bits.append("protected branches" if env["protected"] else "unprotected branches")
    lines = [
        f"In environment `{env['name']}` (branches {_code(env['branches'])}; "
        f"{', '.join(kind_bits)}):",
        "",
        f"- **An agent may:** {_code(env['agent'])}.",
        f"- **Needs a human** (an agent does not do it): "
        f"{', '.join(human) if human else 'nothing'}.",
        f"- **Not an agent action here:** {_code(denied)}.",
    ]
    if env["protected"]:
        lines.append(f"- A change reaches these branches through a pull request with "
                     f"{env['required_reviews']} human review(s), never a direct or force push.")
    if env["production"]:
        lines.append("- Agents never hold deploy rights here.")
    if env["notes"]:
        lines += ["", env["notes"]]
    lines += ["", f"Generated from the guardrails matrix (`{DOC_PATH}`); change the matrix, "
                  f"not this text."]
    return {"slug": INTENT_PREFIX + env["name"], "kind": "constraint",
            "title": f"Agent guardrails: {env['name']}"[:200],
            "statement": "\n".join(lines), "source": source[:200]}


def source_label(matrix: dict, filename: str | None) -> str:
    base = os.path.basename(filename) if filename else "matrix"
    return f"guardrails matrix v{matrix['version']} sha256:{digest(matrix)[:12]} ({base})"[:200]


def _table(matrix: dict) -> list[str]:
    out = ["| environment | branches | agent may | needs a human | step-up | reviews |",
           "|---|---|---|---|---|---|"]
    for e in matrix["environments"]:
        name = e["name"] + (" (production)" if e["production"] else "")
        out.append(f"| {name} | {', '.join(e['branches'])} | {', '.join(e['agent']) or '-'} | "
                   f"{', '.join(e['human']) or '-'} | {', '.join(e['step_up']) or '-'} | "
                   f"{e['required_reviews'] if e['protected'] else 'unprotected'} |")
    return out


def render_doc(matrix: dict, source: str) -> str:
    """The node document: a table for people, the normalised matrix as JSON for tools."""
    lines = ["# Agent guardrails matrix", "",
             f"{matrix['name'] or 'This install'}: what agents may do per environment and branch. "
             f"Applied by `atlas-admin guardrails apply`; edit the matrix file and apply it again "
             f"rather than editing this document. One `guardrail-<env>` intent per environment "
             f"states the same rules on this node, and every part below it inherits them.", "",
             f"Source: {source}", ""]
    lines += _table(matrix)
    if matrix["custom_actions"]:
        lines += ["", "Custom actions (carried to agents, ignored by the exports):", ""]
        lines += [f"- `{k}`: {v}" for k, v in matrix["custom_actions"].items()]
    lines += ["", "## The matrix", "", "```json guardrails",
              json.dumps(matrix, indent=2, ensure_ascii=False), "```", ""]
    return "\n".join(lines)


_FENCE = re.compile(r"```json guardrails\n(.*?)\n```", re.S)


def matrix_from_doc(content: str) -> dict:
    m = _FENCE.search(content or "")
    if not m:
        raise GuardrailsError(f"{DOC_PATH} holds no matrix block")
    return validate(json.loads(m.group(1)))


def render_adr(matrix: dict, source: str, supersedes: Optional[str]) -> tuple[str, str, str]:
    """(title, slug, markdown) of the Proposed ADR describing the matrix."""
    d = digest(matrix)[:12]
    title = f"Agent guardrails per environment (matrix {d})"
    prods = [e["name"] for e in matrix["environments"] if e["production"]]
    lines = [f"# {title}", "", "Status: Proposed. A human accepts it; applying a matrix never does.",
             "", "## Context", "",
             "Agents work in this install's environments. What they may do in each one, and what "
             "needs a human, is stated as data so it can be read by agents, reviewed, and exported "
             "into the tools that enforce it.", "",
             "## Decision", "",
             f"The guardrails matrix ({source}) applies:", ""]
    lines += _table(matrix)
    lines += ["", f"Agents never hold deploy rights in production ({', '.join(prods)}).", "",
              "## Consequences", "",
              f"- One `guardrail-<env>` constraint intent per environment on `{SYSTEM_NODE}`, "
              f"inherited by every part below it, so `atlas_get` returns the rules to an agent.",
              f"- The matrix is stored in `{DOC_PATH}` and exported by "
              f"`atlas-admin guardrails export` as Claude Code permission rules and GitHub branch "
              f"protection settings.",
              "- Changing the matrix means applying a new file, which proposes a new ADR that "
              "supersedes this one."]
    if supersedes:
        lines += [f"- Supersedes {supersedes}."]
    return title, ADR_SLUG_PREFIX + d, "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Export (pure)
# --------------------------------------------------------------------------- #

def export_claude_code(matrix: dict, envs: Optional[Iterable[str]] = None) -> dict:
    """A Claude Code settings fragment: `permissions.allow` / `permissions.deny`."""
    selected = select_envs(matrix, envs)
    allow: set[str] = set()
    deny: set[str] = set()
    for e in selected:
        if e["protected"] and "push" not in e["agent"]:
            for b in e["branches"]:
                deny.add(f"Bash(git push * {b})")
                deny.add(f"Bash(git push * *:{b})")
        for action, patterns in e["commands"].items():
            for p in patterns:
                (allow if action in e["agent"] else deny).add(f"Bash({p})")
    if any(e["protected"] for e in selected):
        deny |= {"Bash(git push --force*)", "Bash(git push -f*)",
                 "Bash(git push * --force*)", "Bash(git push * -f*)"}
    if not all("merge" in e["agent"] for e in selected):
        deny.add("Bash(gh pr merge*)")
    allow -= deny
    return {"permissions": {"allow": sorted(allow), "deny": sorted(deny)}}


def export_github(matrix: dict, envs: Optional[Iterable[str]] = None) -> dict:
    """Branch-protection request bodies, one per protected branch pattern."""
    out = []
    for e in select_envs(matrix, envs):
        if not e["protected"]:
            continue
        for b in e["branches"]:
            wildcard = "*" in b
            body = {
                "required_status_checks": {"strict": True, "contexts": list(e["required_checks"])},
                "enforce_admins": bool(e["production"]),
                "required_pull_request_reviews": {
                    "dismiss_stale_reviews": True,
                    "require_code_owner_reviews": False,
                    "required_approving_review_count": e["required_reviews"],
                    "require_last_push_approval": bool(e["production"]),
                },
                "restrictions": None,
                "required_linear_history": False,
                "allow_force_pushes": False,
                "allow_deletions": False,
            }
            item = {"environment": e["name"], "branch": b, "wildcard": wildcard, "body": body}
            item["apply_with"] = (
                "a repository ruleset or GraphQL createBranchProtectionRule(pattern) — the REST "
                "endpoint takes one branch name" if wildcard
                else f"PUT /repos/{{owner}}/{{repo}}/branches/{b}/protection")
            if not e["required_checks"]:
                item["todo"] = "required_status_checks.contexts is empty: name your CI checks"
            out.append(item)
    return {"matrix": {"version": matrix["version"], "sha256": digest(matrix)},
            "branches": out}


def export(matrix: dict, fmt: str, envs: Optional[Iterable[str]] = None) -> dict:
    if fmt == "claude-code":
        return export_claude_code(matrix, envs)
    if fmt == "github-branch-protection":
        return export_github(matrix, envs)
    raise GuardrailsError(f"unknown format {fmt!r} (one of {', '.join(FORMATS)})")


# --------------------------------------------------------------------------- #
# The crunch-week overlay (pure) — STORY-atlas-crunch-mode
# --------------------------------------------------------------------------- #
#
# Crunch week (backend/crunch.py) lets an installation's administrator switch on, for a set
# time, a release mode where production deploys after green CI without the staging /
# pre-production steps named in its `skip_envs`. This is what that does to the MATRIX, as a
# view: `crunch_overlay` never writes, and nothing applies its result (`guardrails show/export
# --crunch-from-db` and `cicd generate --crunch...` read it).
#
# WHAT IT CHANGES, and nothing else: each environment in `skip_envs` loses its deploy actions
# (`deploy`, `deploy_via_pipeline`, `approve_deploy`, from `agent`, `human` and `step_up`), so
# it is no deploy step at all and `cicd.deploy_plan` leaves it out of the path to production.
# Its branches, their protection, its other actions and its `commands` stay: a `commands`
# pattern of a deploy action it no longer holds is exported as a DENY (no action, no allow), so
# the overlay can only narrow what an agent may do there, never widen it.
#
# WHAT IT NEVER CHANGES: every production environment, byte for byte. Production's "deploy
# waits for a person" rule is the matrix's, crunch or not, and a `skip_envs` naming a
# production environment is refused. The result goes through `validate` again, so the standing
# refusals (no agent deploy rights in production, approve_deploy never an agent's) hold after
# the overlay exactly as before it; an input matrix that breaks them is refused first.

#: The keys of a crunch config (crunch.CONFIG_KEYS, `duration_hours` included: a `--crunch FILE`
#: may give a length instead of an end, and the release path reads neither) and of the state
#: crunch.current returns (crunch._state: the config plus `active`, `activated_by`,
#: `activated_at`, `adr_id` and `revision`). Only `skip_envs` and `backup` are read here; the
#: rest are accepted so `--crunch-from-db` works while crunch week is on (TASK-1432). Anything
#: else is refused, so a misspelt `skip_env` cannot quietly skip nothing.
CRUNCH_KEYS = {"starts_at", "ends_at", "duration_hours", "backup", "skip_envs",
               "active", "activated_by", "activated_at", "adr_id", "revision"}


def crunch_skip_envs(matrix: dict, crunch_config: Any) -> list[str]:
    """The `skip_envs` of a crunch config, checked against `matrix` (normalised). Raises
    MatrixError naming each problem: not a mapping, an unknown key, a name not in the matrix,
    a production environment."""
    if not isinstance(crunch_config, dict):
        raise MatrixError(["crunch config: must be a mapping (crunch.CONFIG_SCHEMA)"])
    errors = []
    extra = sorted(set(crunch_config) - CRUNCH_KEYS)
    if extra:
        errors.append(f"crunch config: unknown key(s): {', '.join(map(str, extra))} "
                      f"(allowed: {', '.join(sorted(CRUNCH_KEYS))})")
    skip = _str_list(errors, "crunch config.skip_envs", crunch_config.get("skip_envs"))
    names = {e["name"]: e for e in matrix["environments"]}
    for n in skip:
        if n not in names:
            errors.append(f"crunch config.skip_envs: {n!r} is not an environment of the matrix "
                          f"(it has: {', '.join(names)})")
        elif names[n]["production"]:
            errors.append(f"crunch config.skip_envs: {n!r} is production; crunch week skips the "
                          f"steps BEFORE production, never production itself")
    if errors:
        raise MatrixError(errors)
    return skip


def crunch_overlay(matrix: dict, crunch_config: dict) -> dict:
    """`matrix` as crunch week runs it: the environments in `skip_envs` removed from the path
    to production (their deploy actions dropped), production untouched. Pure; validates the
    input and the result (MatrixError). See the block above for what it may and may not do."""
    base = validate(matrix)
    skip = crunch_skip_envs(base, crunch_config)
    out = json.loads(json.dumps(base))
    for e in out["environments"]:
        if e["name"] not in skip:
            continue
        for key in ("agent", "human", "step_up"):
            e[key] = [a for a in e[key] if a not in DEPLOY_ACTIONS]
        note = ("Crunch week: not deployed while crunch week is on; production deploys "
                "without this step.")
        e["notes"] = f"{e['notes']}\n\n{note}" if e["notes"] else note
    if skip:
        ends = crunch_config.get("ends_at")
        bit = (f"Crunch week overlay of matrix sha256:{digest(base)[:12]}: production deploys "
               f"without {', '.join(skip)}" + (f" until {ends}" if isinstance(ends, str) else "")
               + ".")
        out["description"] = f"{out['description']}\n\n{bit}" if out["description"] else bit
    result = validate(out)
    for before, after in zip(base["environments"], result["environments"]):
        # The invariant, checked rather than trusted: production is the matrix's, unchanged.
        if before["production"] and before != after:
            raise MatrixError([f"environments[{before['name']}]: the crunch overlay changed a "
                               f"production environment (a bug; nothing was produced)"])
    return result


# --------------------------------------------------------------------------- #
# A matrix from answers (pure) — see the module docstring for the keys
# --------------------------------------------------------------------------- #

_ANSWER_KEYS = {"environments", "production", "branches", "agent_deploys", "human_approves",
                "pipeline_deploys", "unprotected", "step_up", "required_reviews",
                "required_checks", "deploy_command", "name"}

_TIERS = {
    "agent_deploys": {"agent": ["branch", "commit", "push", "open_pr", "deploy", "read_logs",
                                "propose"], "human": ["merge"]},
    "pipeline_deploys": {"agent": ["read_logs", "propose", "open_pr", "deploy_via_pipeline"],
                         "human": ["merge"]},
    "human_approves": {"agent": ["read_logs", "propose"],
                       "human": ["merge", "approve_deploy", "deploy"]},
    "production": {"agent": ["read_logs"], "human": ["merge", "approve_deploy", "deploy"]},
}


def matrix_from_answers(answers: dict) -> dict:
    """Build and validate a matrix from a setup guide's answers. Pure; raises MatrixError."""
    if not isinstance(answers, dict):
        raise MatrixError(["answers must be a mapping"])
    errors = [f"unknown answer key {k!r}" for k in sorted(set(answers) - _ANSWER_KEYS)]
    names = answers.get("environments")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise MatrixError(errors + ["environments: a non-empty list of names is required"])
    prod = answers.get("production", names[-1])

    def named(key: str, default: list[str]) -> list[str]:
        got = answers.get(key, default)
        if not isinstance(got, list):
            errors.append(f"{key}: must be a list of environment names")
            return []
        for n in got:
            if n not in names:
                errors.append(f"{key}: {n!r} is not one of the environments")
        return list(got)

    if prod not in names:
        errors.append(f"production: {prod!r} is not one of the environments")
    rest = [n for n in names if n != prod]
    agent_deploys = named("agent_deploys", rest[:1])
    default_approve = [rest[-1]] if len(names) >= 3 and rest and rest[-1] not in agent_deploys else []
    human_approves = named("human_approves", default_approve)
    pipeline = named("pipeline_deploys", [n for n in rest if n not in agent_deploys
                                          and n not in human_approves])
    unprotected = named("unprotected", agent_deploys)
    branches = answers.get("branches") or {}
    if not isinstance(branches, dict):
        errors.append("branches: must map an environment name to its branch patterns")
        branches = {}
    for k in branches:
        if k not in names:
            errors.append(f"branches: {k!r} is not one of the environments")
    step_up = answers.get("step_up", True)
    reviews = answers.get("required_reviews", 1)
    checks = answers.get("required_checks", [])
    command = answers.get("deploy_command")
    if command is not None and (not isinstance(command, str) or "{env}" not in command):
        errors.append("deploy_command: must be a string containing {env}")
        command = None
    if errors:
        raise MatrixError(errors)

    envs = []
    for n in names:
        if n == prod:
            tier = "production"
        elif n in agent_deploys:
            tier = "agent_deploys"
        elif n in human_approves:
            tier = "human_approves"
        else:
            tier = "pipeline_deploys"  # named in pipeline_deploys, or in no list at all
        t = _TIERS[tier]
        e: dict[str, Any] = {"name": n,
                             "branches": list(branches.get(n) or (["main"] if n == prod else [n])),
                             "agent": list(t["agent"]), "human": list(t["human"])}
        if n in unprotected:
            e["protected"] = False
        else:
            e["required_reviews"] = reviews
            if checks:
                e["required_checks"] = list(checks)
        if n == prod:
            e["production"] = True
            if step_up:
                e["step_up"] = ["deploy"]
        if command and "deploy" in e["human"]:
            e["commands"] = {"deploy": [command.replace("{env}", n)]}
        envs.append(e)
    raw = {"version": MATRIX_VERSION, "environments": envs}
    if answers.get("name"):
        raw["name"] = answers["name"]
    return validate(raw)


# --------------------------------------------------------------------------- #
# Reading and applying against the graph
# --------------------------------------------------------------------------- #

def stored_matrix(s) -> Optional[dict]:
    """The matrix applied to this install, or None when none ever was."""
    from sqlalchemy import select
    from models_graph import NodeDoc
    row = s.scalar(select(NodeDoc).where(NodeDoc.node_id == SYSTEM_NODE, NodeDoc.path == DOC_PATH))
    return None if row is None else matrix_from_doc(row.content)


def _latest_guardrails_adr(s):
    from sqlalchemy import select
    from models_adr import ADR_SOURCE_ATLAS, AdrRecord
    return s.scalar(select(AdrRecord).where(
        AdrRecord.project_node_id == SYSTEM_NODE, AdrRecord.source == ADR_SOURCE_ATLAS,
        AdrRecord.slug.like(ADR_SLUG_PREFIX + "%")).order_by(AdrRecord.seq.desc()).limit(1))


def apply(s, matrix: dict, *, filename: str | None = None, dry_run: bool = False,
          operator: str = "unknown", adr_prefix: str = DEFAULT_ADR_PREFIX) -> list[str]:
    """Bring `atlas-system` in line with `matrix`. Returns one line per change (empty: nothing
    to do). With `dry_run`, computes the same list and writes nothing. Commits on success."""
    from sqlalchemy import func, select
    import adr_alloc
    import deps
    from models_adr import ADR_SOURCE_ATLAS, AdrRecord
    from models_graph import Node, NodeDoc, NodeIntent

    node = s.get(Node, SYSTEM_NODE)
    if node is None:
        raise GuardrailsError(
            f"`{SYSTEM_NODE}` is not in this graph, and the matrix lives on it. Add the system "
            f"node set first: `python system_node.py --apply` (in the image: "
            f"`python /app/system_node.py --apply`), then apply again.")
    src = source_label(matrix, filename)
    via = "atlas-admin guardrails apply"
    changes: list[str] = []

    def event(action: str, **detail) -> None:
        if not dry_run:
            deps.record_graph_event(s, action, entity_type="node", entity_id=SYSTEM_NODE,
                                    detail={**detail, "operator": operator, "via": via})

    # 1. The document, and its docs[] entry.
    content = render_doc(matrix, src)
    doc = s.scalar(select(NodeDoc).where(NodeDoc.node_id == SYSTEM_NODE, NodeDoc.path == DOC_PATH))
    if doc is None or doc.content != content:
        changes.append(f"{'create' if doc is None else 'update'} document {DOC_PATH}")
        if not dry_run:
            if doc is None:
                s.add(NodeDoc(node_id=SYSTEM_NODE, path=DOC_PATH, content=content))
            else:
                doc.content = content
        event("node.doc.write", path=DOC_PATH, created=doc is None, bytes=len(content))
    attrs = dict(node.attributes or {})
    docs = list(attrs.get("docs") or [])
    if not any(isinstance(d, dict) and d.get("path") == DOC_PATH for d in docs):
        changes.append(f"list {DOC_PATH} in {SYSTEM_NODE}'s docs[]")
        if not dry_run:
            node.attributes = {**attrs, "docs": docs + [dict(DOC_ENTRY)]}
        event("node.update", fields=["attributes"])

    # 2. One constraint intent per environment; a removed environment's intent goes.
    want = {i["slug"]: i for i in (intent_for(matrix, e, src) for e in matrix["environments"])}
    have = {r.slug: r for r in s.scalars(select(NodeIntent).where(
        NodeIntent.node_id == SYSTEM_NODE, NodeIntent.slug.like(INTENT_PREFIX + "%")))}
    for slug in sorted(set(have) - set(want)):
        r = have[slug]
        changes.append(f"delete intent {slug} (its environment left the matrix)")
        if not dry_run:
            gone = {"intent_id": r.id, "slug": r.slug, "kind": r.kind}
            s.delete(r)
            s.flush()
            event("node.intent.delete", **gone)
    next_pos = s.scalar(select(func.coalesce(func.max(NodeIntent.position), -1)).where(
        NodeIntent.node_id == SYSTEM_NODE)) + 1
    for slug, w in want.items():
        r = have.get(slug)
        if r is None:
            changes.append(f"create intent {slug}")
            if not dry_run:
                row = NodeIntent(node_id=SYSTEM_NODE, position=next_pos, **w)
                next_pos += 1
                s.add(row)
                s.flush()
                event("node.intent.create", intent_id=row.id, slug=slug, kind=w["kind"])
            continue
        fields = sorted(f for f in ("kind", "title", "statement", "source") if getattr(r, f) != w[f])
        if fields:
            changes.append(f"update intent {slug} ({', '.join(fields)})")
            if not dry_run:
                for f in fields:
                    setattr(r, f, w[f])
                event("node.intent.update", intent_id=r.id, slug=slug, fields=fields)
    if not dry_run:
        # Dense positions, as routers/intents._renumber_intents keeps them (same order rule).
        rows = sorted(s.scalars(select(NodeIntent).where(NodeIntent.node_id == SYSTEM_NODE)),
                      key=lambda r: (r.position, r.id))
        for i, r in enumerate(rows):
            if r.position != i:
                r.position = i

    # 3. The Proposed ADR, unless the latest guardrails ADR already describes this matrix.
    prev = _latest_guardrails_adr(s)
    _, slug, _ = render_adr(matrix, src, None)
    if prev is None or prev.slug != slug:
        if not dry_run:
            # Serialise ADR allocation in this project, as PUT /api/adrs/new does (adr_alloc).
            adr_alloc.lock_project(s, SYSTEM_NODE)
        own = s.scalar(select(AdrRecord.prefix).where(
            AdrRecord.project_node_id == SYSTEM_NODE).limit(1))
        prefix = own or adr_prefix
        if not re.fullmatch(r"[A-Z]{2,4}", prefix) or prefix == "ADR":
            raise GuardrailsError(f"ADR prefix {prefix!r} must be 2-4 capital letters, not ADR")
        taken = s.scalar(select(AdrRecord.project_node_id).where(
            AdrRecord.prefix == prefix, AdrRecord.project_node_id != SYSTEM_NODE).limit(1))
        if taken is not None:
            raise GuardrailsError(f"ADR prefix {prefix!r} already belongs to {taken!r}; "
                                  f"pass --adr-prefix with another")
        seq, adr_id = adr_alloc.allocate(s, SYSTEM_NODE, prefix)
        title, slug, md = render_adr(matrix, src, prev.adr_id if prev else None)
        changes.append(f"create Proposed ADR {adr_id}"
                       + (f", superseding {prev.adr_id}" if prev else ""))
        if not dry_run:
            now = datetime.now(timezone.utc)
            # The row and its node.adr.write, through the insert PUT /api/adrs/new uses.
            adr_alloc.add_atlas_adr(
                s, adr_id=adr_id, project=SYSTEM_NODE, prefix=prefix, seq=seq, title=title,
                slug=slug, status="proposed", status_raw=None, date=now.date(), content=md,
                now=now, supersedes=[prev.adr_id] if prev else [], superseded_by=[], amends=[],
                extra_detail={"operator": operator, "via": via})
        if prev is not None:
            fields = ["superseded_by"] + (["status"] if prev.status == "proposed" else [])
            changes.append(f"mark {prev.adr_id} superseded_by {adr_id}"
                           + (" (status superseded)" if "status" in fields else
                              f" (status stays {prev.status})"))
            if not dry_run:
                prev.superseded_by = list(prev.superseded_by or []) + [adr_id]
                if prev.status == "proposed":
                    prev.status = "superseded"
                prev.synced_at = datetime.now(timezone.utc)
                event("node.adr.write", adr_id=prev.adr_id, created=False, fields=fields,
                      bytes=len(prev.content or ""), source=ADR_SOURCE_ATLAS)
    if dry_run:
        s.rollback()
    elif changes:
        s.commit()
    return changes


def dump(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def example() -> dict:
    """The shipped example, normalised. Never applied by anything but an operator."""
    return load_file(EXAMPLE_FILE)


__all__ = ["ACTIONS", "MatrixError", "GuardrailsError", "validate", "load_file", "digest",
           "export", "export_claude_code", "export_github", "matrix_from_answers", "apply",
           "stored_matrix", "intent_for", "render_doc", "render_adr", "example", "crunch_overlay",
           "crunch_skip_envs"]
