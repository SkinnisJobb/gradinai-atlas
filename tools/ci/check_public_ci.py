#!/usr/bin/env python3
"""check_public_ci.py: refuse a workflow set that is unsafe on a public repository (STORY-oss-23, A11).

WHY. On a public repository anyone can fork and open a pull request, and that pull request runs
the workflows. A self-hosted runner would then execute a stranger's code on the owner's machine,
so the public copy must run only on GitHub-hosted runners, never on a trigger that hands a fork
run a write token or secrets, and with the narrowest token by default. This check proves that
statically, file by file. It is NOT wired into this repository's own workflows: they use the
self-hosted pool on purpose (`vars.CI_RUNNER`) and fail it. The export wires it into the public
copy.

RULES (each finding carries one of these ids).
  runs-on-not-hosted     a `runs-on` label that is not a literal GitHub-hosted label
                         (`ubuntu-*`, `windows-*`, `macos-*`), a `group:` (runner groups select
                         self-hosted or organisation pools), or a job with neither `runs-on` nor
                         a reusable-workflow `uses:`.
  self-hosted            the label `self-hosted` anywhere in a `runs-on` or in a matrix value
                         that feeds one.
  runs-on-expression     any `${{ ... }}` in `runs-on`, `vars.*` included: one repository
                         variable set later would move fork runs to a self-hosted runner.
                         The one exception is exactly `${{ matrix.<key> }}` when the matrix
                         proves every value literal (next rule).
  runs-on-matrix         a `${{ matrix.<key> }}` runs-on that cannot be proved literal: the
                         matrix (or the key's list, or an `include` entry) is an expression,
                         a value is not a hosted label, or some combination has no value for
                         the key.
  trigger                a `pull_request_target` or `workflow_run` trigger. Both run in the
                         base repository's context with secrets and a write-capable token, on
                         events a fork can cause.
  permissions-missing    no top-level `permissions:` block, so the workflow inherits the
                         repository's default token, which may be write-all.
  permissions-broad      a `permissions:` broader than `contents: read`. Narrow means `{}`, or a
                         mapping whose every scope is `none` except `contents`, which may be
                         `read`. `read-all` and `write-all` are broad. At the top level a broad
                         block always fails (write access belongs on the one job that needs it).
                         On a job it passes only with the justification marker below.
  unpinned-action        a `uses:` that tools/ci/check_pins.py classes as not pinned: a
                         third-party action or reusable workflow not on a full commit SHA, a
                         branch ref, or a `docker://` image without tag + digest. The
                         classification is imported from check_pins.py, so both checks always
                         agree (first-party `actions/` and `github/` may use a version tag).
  local-action-missing   a `uses: ./path` whose target (an `action.yml` / `action.yaml`, or a
                         workflow file) does not exist under the root, so it cannot be checked.
  parse-error            a file that is not valid YAML, or not a mapping.

THE JUSTIFICATION MARKER. A job that really needs more than `contents: read` (a release job that
needs `contents: write`, a publish job that needs `packages: write`) carries a comment on its
`permissions:` line or in the comment lines directly above it:

    publish:
      # public-ci: allow-permissions publishes the images to GHCR, tag pushes only
      permissions:
        packages: write

The text after `allow-permissions` is the reason and must not be empty. The marker is read from
the raw text (YAML drops comments), only for the job whose `permissions:` key it sits on. It is
not honoured on the top-level block.

REUSABLE WORKFLOWS AND COMPOSITE ACTIONS.
  - A job that calls a reusable workflow (`jobs.<id>.uses:`) has no `runs-on` of its own: the
    called workflow's jobs choose the runner. A local call (`./.github/workflows/x.yml`) is
    resolved and that file is checked like any other workflow (once). A remote call is checked
    for its pin; its runners are outside this repository and are the caller's trust decision.
  - A workflow triggered only by `workflow_call` is still a workflow: every rule applies.
  - Composite actions (`action.yml` / `action.yaml` with `runs.using: composite`) are found
    under `.github/actions/` and through each `uses: ./path` reference; their step `uses:` are
    checked for pins (they run on the caller's runner with the caller's token). A Docker action's
    `runs.image: docker://...` is checked as an image pin; `runs.image: Dockerfile` is left to
    check_pins.py's Dockerfile scan. A JavaScript action has nothing to check here.

INPUT. With no PATH arguments: every `*.yml` / `*.yaml` in `<root>/.github/workflows/` and every
`action.yml` / `action.yaml` under `<root>/.github/actions/`. A PATH may be a directory (its
workflow files) or one file. `--root` is the base that `uses: ./...` resolves against (default:
the current directory).

Usage:
  python tools/ci/check_public_ci.py [--root DIR] [PATH ...]
  python tools/ci/check_public_ci.py --self-test

Needs PyYAML (the public workflow installs a pinned `pyyaml`). Exit 0 clean, 1 findings or a
failed self-test, 2 usage or PyYAML missing. The table also goes to $GITHUB_STEP_SUMMARY when set.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_pins  # noqa: E402  (same directory; reused so the pin rules never diverge)

try:
    import yaml
except ImportError:  # reported in main(), so --help still works
    yaml = None

HOSTED_LABEL_RE = re.compile(r"^(ubuntu|windows|macos)-[A-Za-z0-9][A-Za-z0-9._-]*$")
MATRIX_REF_RE = re.compile(r"^\$\{\{\s*matrix\.([A-Za-z_][A-Za-z0-9_-]*)\s*\}\}$")
MARKER_RE = re.compile(r"#\s*public-ci:\s*allow-permissions\b(.*)$")
FORBIDDEN_TRIGGERS = ("pull_request_target", "workflow_run")
WORKFLOW_SUFFIXES = (".yml", ".yaml")
ACTION_NAMES = ("action.yml", "action.yaml")


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    detail: str


# --- YAML with line numbers -------------------------------------------------------------------

class LineDict(dict):
    """A mapping that remembers the 1-based line of itself and of each key."""
    line = 0

    def __init__(self):
        super().__init__()
        self.lines: dict = {}


def _load(text: str):
    class Loader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        loader.flatten_mapping(node)
        out = LineDict()
        out.line = node.start_mark.line + 1
        for k_node, v_node in node.value:
            key = loader.construct_object(k_node, deep=True)
            out[key] = loader.construct_object(v_node, deep=True)
            out.lines[key] = k_node.start_mark.line + 1
        return out

    Loader.add_constructor("tag:yaml.org,2002:map", construct_mapping)
    return yaml.load(text, Loader=Loader)  # noqa: S506 (SafeLoader subclass)


def _line(d, key, default=1) -> int:
    return getattr(d, "lines", {}).get(key, getattr(d, "line", default) or default)


# --- the rules ----------------------------------------------------------------------------------

def is_hosted(label) -> bool:
    return isinstance(label, str) and bool(HOSTED_LABEL_RE.match(label.strip()))


def _has_self_hosted(value) -> bool:
    if isinstance(value, str):
        return "self-hosted" in value.lower()
    if isinstance(value, dict):
        return any(_has_self_hosted(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_self_hosted(v) for v in value)
    return False


def matrix_values(job, key: str) -> tuple[list[str] | None, str]:
    """Every value `matrix.<key>` can take in `job`, or (None, why) when that cannot be proved."""
    strategy = job.get("strategy")
    if not isinstance(strategy, dict):
        return None, "the job has no literal `strategy`"
    matrix = strategy.get("matrix")
    if not isinstance(matrix, dict):
        return None, "`strategy.matrix` is not a literal mapping (an expression or fromJSON?)"
    include = matrix.get("include", [])
    if not isinstance(include, list) or not all(isinstance(e, dict) for e in include):
        return None, "`matrix.include` is not a literal list of mappings"
    values: list = []
    if key in matrix:
        base = matrix[key]
        if not isinstance(base, list) or not base:
            return None, f"`matrix.{key}` is not a literal, non-empty list"
        values += base
    else:
        others = [k for k in matrix if k not in ("include", "exclude")]
        if others or not include:
            return None, f"some matrix combination has no `{key}`"
        if any(key not in e for e in include):
            return None, f"an `include` entry has no `{key}`"
    values += [e[key] for e in include if key in e]
    bad = [v for v in values if not isinstance(v, str) or "${{" in v]
    if bad:
        return None, f"`matrix.{key}` holds a non-literal value {bad[0]!r}"
    return values, ""


def check_runs_on(path: str, job_id: str, job) -> list[Finding]:
    out: list[Finding] = []
    line = _line(job, "runs-on")
    where = f"job `{job_id}`"
    if "runs-on" not in job:
        if "uses" in job:  # a reusable-workflow call: the called jobs pick the runner
            return out
        return [Finding(path, getattr(job, "line", 1), "runs-on-not-hosted",
                        f"{where} has no `runs-on`")]
    runs_on = job["runs-on"]
    if _has_self_hosted(runs_on):
        out.append(Finding(path, line, "self-hosted", f"{where}: `runs-on: {runs_on}`"))
    if isinstance(runs_on, dict):
        out.append(Finding(path, line, "runs-on-not-hosted",
                           f"{where}: a `group:`/`labels:` runs-on selects a runner pool, "
                           "not a GitHub-hosted label"))
        return out
    labels = runs_on if isinstance(runs_on, list) else [runs_on]
    if not labels:
        out.append(Finding(path, line, "runs-on-not-hosted", f"{where}: empty `runs-on`"))
    for label in labels:
        if not isinstance(label, str):
            out.append(Finding(path, line, "runs-on-not-hosted",
                               f"{where}: runs-on label {label!r} is not a string"))
            continue
        if "${{" in label:
            m = MATRIX_REF_RE.match(label.strip())
            if not m:
                kind = "uses `vars.`" if "vars." in label else "is an expression"
                out.append(Finding(path, line, "runs-on-expression",
                                   f"{where}: `runs-on: {label}` {kind}"))
                continue
            values, why = matrix_values(job, m.group(1))
            if values is None:
                out.append(Finding(path, line, "runs-on-matrix", f"{where}: {why}"))
                continue
            for v in values:
                if "self-hosted" in v.lower():
                    out.append(Finding(path, line, "self-hosted",
                                       f"{where}: matrix value {v!r}"))
                elif not is_hosted(v):
                    out.append(Finding(path, line, "runs-on-matrix",
                                       f"{where}: matrix value {v!r} is not a hosted label"))
        elif "self-hosted" in label.lower():
            continue  # already reported as self-hosted
        elif not is_hosted(label):
            out.append(Finding(path, line, "runs-on-not-hosted",
                               f"{where}: `{label}` is not a GitHub-hosted label"))
    return out


def triggers(doc) -> tuple[list[str], int]:
    key = "on" if "on" in doc else (True if True in doc else None)  # YAML 1.1 reads `on` as true
    if key is None:
        return [], 1
    on = doc[key]
    line = _line(doc, key)
    if isinstance(on, str):
        return [on], line
    if isinstance(on, list):
        return [t for t in on if isinstance(t, str)], line
    if isinstance(on, dict):
        return [str(t) for t in on], line
    return [], line


def is_narrow(perms) -> bool:
    if perms == {} or perms is None:
        return perms == {}
    if isinstance(perms, str):
        return False  # read-all / write-all, or anything else
    if not isinstance(perms, dict):
        return False
    for scope, level in perms.items():
        if level == "none":
            continue
        if scope == "contents" and level == "read":
            continue
        return False
    return True


def has_marker(lines: list[str], key_line: int) -> bool:
    """The justification marker on line `key_line` (1-based) or in the comment block above it."""
    def reason(text: str) -> bool:
        m = MARKER_RE.search(text)
        return bool(m and m.group(1).strip())

    if 1 <= key_line <= len(lines) and reason(lines[key_line - 1]):
        return True
    n = key_line - 1
    while n >= 1 and lines[n - 1].lstrip().startswith("#"):
        if reason(lines[n - 1]):
            return True
        n -= 1
    return False


def check_pins_in(path: str, text: str) -> list[Finding]:
    out = []
    for r in check_pins.scan_workflow(path, text):
        if check_pins.is_bad(r):
            out.append(Finding(path, r.line, "unpinned-action", f"`{r.ref}` is {r.status}"))
    return out


def local_uses(text: str) -> list[tuple[int, str]]:
    refs = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        m = check_pins.USES_LINE_RE.match(line)
        if m:
            uses = check_pins._strip_value(m.group(1))
            if uses.startswith("./"):
                refs.append((n, uses))
    return refs


def check_workflow(path: str, text: str, doc) -> list[Finding]:
    out: list[Finding] = []
    lines = text.splitlines()
    names, on_line = triggers(doc)
    for t in names:
        if t in FORBIDDEN_TRIGGERS:
            out.append(Finding(path, on_line, "trigger", f"`{t}` trigger"))
    if "permissions" not in doc:
        out.append(Finding(path, 1, "permissions-missing", "no top-level `permissions:` block"))
    elif not is_narrow(doc["permissions"]):
        out.append(Finding(path, _line(doc, "permissions"), "permissions-broad",
                           f"top-level `permissions: {doc['permissions']}`; grant more only on "
                           "the job that needs it"))
    jobs = doc.get("jobs")
    if isinstance(jobs, dict):
        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                out.append(Finding(path, _line(jobs, job_id), "parse-error",
                                   f"job `{job_id}` is not a mapping"))
                continue
            out += check_runs_on(path, str(job_id), job)
            if "permissions" in job and not is_narrow(job["permissions"]):
                pline = _line(job, "permissions")
                if not has_marker(lines, pline):
                    out.append(Finding(
                        path, pline, "permissions-broad",
                        f"job `{job_id}`: `permissions: {dict(job['permissions']) if isinstance(job['permissions'], dict) else job['permissions']}` "
                        "without a `# public-ci: allow-permissions <reason>` marker"))
    out += check_pins_in(path, text)
    return out


def check_action(path: str, text: str, doc) -> list[Finding]:
    runs = doc.get("runs")
    if not isinstance(runs, dict):
        return [Finding(path, 1, "parse-error", "action file has no `runs:` mapping")]
    using = str(runs.get("using", ""))
    out: list[Finding] = []
    if using == "composite":
        out += check_pins_in(path, text)
    elif using == "docker":
        image = runs.get("image")
        if isinstance(image, str) and image.startswith("docker://"):
            ref = image[len("docker://"):]
            status = check_pins.classify_image(ref)
            if status in check_pins.BAD_IMAGE:
                out.append(Finding(path, _line(runs, "image"), "unpinned-action",
                                   f"`{image}` is {status}"))
    return out


# --- driver -------------------------------------------------------------------------------------

def default_targets(root: Path) -> list[Path]:
    files: list[Path] = []
    wf = root / ".github" / "workflows"
    if wf.is_dir():
        files += sorted(p for p in wf.iterdir() if p.suffix in WORKFLOW_SUFFIXES and p.is_file())
    acts = root / ".github" / "actions"
    if acts.is_dir():
        files += sorted(p for p in acts.rglob("*") if p.name in ACTION_NAMES)
    return files


def expand(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix in WORKFLOW_SUFFIXES and f.is_file())
            files += sorted(f for f in p.rglob("*") if f.name in ACTION_NAMES and f.parent != p)
        else:
            files.append(p)
    return files


def _rel(p: Path, root: Path) -> str:
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def run(root: Path, files: list[Path]) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    queue = list(files)
    seen: set[Path] = set()
    while queue:
        f = queue.pop(0)
        key = f.resolve()
        if key in seen:
            continue
        seen.add(key)
        rel = _rel(f, root)
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as e:
            findings.append(Finding(rel, 1, "parse-error", f"cannot read: {e}"))
            continue
        try:
            doc = _load(text)
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            findings.append(Finding(rel, mark.line + 1 if mark else 1, "parse-error", str(e).splitlines()[0]))
            continue
        if not isinstance(doc, dict):
            findings.append(Finding(rel, 1, "parse-error", "not a YAML mapping"))
            continue
        is_action = f.name in ACTION_NAMES or ("runs" in doc and "jobs" not in doc)
        findings += check_action(rel, text, doc) if is_action else check_workflow(rel, text, doc)
        for n, uses in local_uses(text):
            target = (root / uses.split("@", 1)[0]).resolve()
            if target.is_file():
                queue.append(target)
            elif target.is_dir() and any((target / a).is_file() for a in ACTION_NAMES):
                queue += [target / a for a in ACTION_NAMES if (target / a).is_file()]
            else:
                findings.append(Finding(rel, n, "local-action-missing",
                                        f"`{uses}` does not resolve under the root"))
    return findings, len(seen)


def render(findings: list[Finding], n_files: int) -> str:
    out = ["## Public CI check (STORY-oss-23, A11)", ""]
    out.append(f"{n_files} file(s) checked; **{len(findings)} finding(s)**.")
    out.append("")
    if findings:
        out.append("| rule | where | detail |")
        out.append("|---|---|---|")
        for f in sorted(findings, key=lambda f: (f.path, f.line, f.rule)):
            detail = f.detail.replace("|", "\\|")
            out.append(f"| {f.rule} | {f.path}:{f.line} | {detail} |")
        out.append("")
    return "\n".join(out)


def self_test() -> int:
    import contextlib
    import io
    import tempfile

    failures: list[str] = []
    sha = "e" * 40
    head = "on:\n  pull_request:\npermissions:\n  contents: read\n"
    steps = "    steps:\n      - run: echo ok\n"

    def wf(jobs: str, top: str = head) -> str:
        return top + "jobs:\n" + jobs

    hosted = wf("  a:\n    runs-on: ubuntu-24.04\n" + steps)
    # rule id -> list of (fixture name, {path: body}, want rule set). Each rule has at least one
    # passing and one failing fixture; the passing fixture must produce no finding at all.
    cases = [
        # runs-on-not-hosted
        ("hosted label passes", {"w.yml": hosted}, set()),
        ("windows/macos pass", {"w.yml": wf("  a:\n    runs-on: windows-2025\n" + steps
                                            + "  b:\n    runs-on: [macos-15]\n" + steps)}, set()),
        ("custom label fails", {"w.yml": wf("  a:\n    runs-on: linux-big\n" + steps)},
         {"runs-on-not-hosted"}),
        ("runner group fails", {"w.yml": wf("  a:\n    runs-on:\n      group: pool\n" + steps)},
         {"runs-on-not-hosted"}),
        ("missing runs-on fails", {"w.yml": wf("  a:\n" + steps)}, {"runs-on-not-hosted"}),
        # self-hosted
        ("self-hosted fails", {"w.yml": wf("  a:\n    runs-on: [self-hosted, linux]\n" + steps)},
         {"self-hosted", "runs-on-not-hosted"}),
        ("self-hosted inside an expression fails", {"w.yml": wf(
            "  a:\n    runs-on: ${{ github.event_name == 'push' && 'self-hosted' || 'ubuntu-latest' }}\n"
            + steps)}, {"self-hosted", "runs-on-expression"}),
        # runs-on-expression
        ("vars fallback fails", {"w.yml": wf(
            "  a:\n    runs-on: ${{ vars.CI_RUNNER || 'ubuntu-latest' }}\n" + steps)},
         {"runs-on-expression"}),
        ("other expression fails", {"w.yml": wf(
            "  a:\n    runs-on: ${{ inputs.runner }}\n" + steps)}, {"runs-on-expression"}),
        # runs-on-matrix
        ("literal matrix passes", {"w.yml": wf(
            "  a:\n    strategy:\n      matrix:\n        os: [ubuntu-latest, windows-latest]\n"
            "        include:\n          - os: macos-15\n"
            "    runs-on: ${{ matrix.os }}\n" + steps)}, set()),
        ("include-only matrix passes", {"w.yml": wf(
            "  a:\n    strategy:\n      matrix:\n        include:\n          - os: ubuntu-latest\n"
            "    runs-on: ${{ matrix.os }}\n" + steps)}, set()),
        ("fromJSON matrix fails", {"w.yml": wf(
            "  a:\n    strategy:\n      matrix: ${{ fromJSON(vars.M) }}\n"
            "    runs-on: ${{ matrix.os }}\n" + steps)}, {"runs-on-matrix"}),
        ("matrix with a custom label fails", {"w.yml": wf(
            "  a:\n    strategy:\n      matrix:\n        os: [ubuntu-latest, big-box]\n"
            "    runs-on: ${{ matrix.os }}\n" + steps)}, {"runs-on-matrix"}),
        ("matrix with self-hosted fails", {"w.yml": wf(
            "  a:\n    strategy:\n      matrix:\n        os: [self-hosted]\n"
            "    runs-on: ${{ matrix.os }}\n" + steps)}, {"self-hosted"}),
        ("matrix combination without the key fails", {"w.yml": wf(
            "  a:\n    strategy:\n      matrix:\n        py: ['3.12']\n"
            "        include:\n          - os: ubuntu-latest\n"
            "    runs-on: ${{ matrix.os }}\n" + steps)}, {"runs-on-matrix"}),
        # trigger
        ("pull_request_target fails", {"w.yml": hosted.replace("pull_request:", "pull_request_target:")},
         {"trigger"}),
        ("workflow_run list form fails", {"w.yml": hosted.replace(
            "on:\n  pull_request:\n", "on: [push, workflow_run]\n")}, {"trigger"}),
        ("push string trigger passes", {"w.yml": hosted.replace(
            "on:\n  pull_request:\n", "on: push\n")}, set()),
        # permissions-missing / permissions-broad
        ("no top-level permissions fails", {"w.yml": wf(
            "  a:\n    runs-on: ubuntu-latest\n" + steps, top="on: push\n")},
         {"permissions-missing"}),
        ("empty permissions passes", {"w.yml": wf(
            "  a:\n    runs-on: ubuntu-latest\n" + steps, top="on: push\npermissions: {}\n")}, set()),
        ("top-level write-all fails", {"w.yml": wf(
            "  a:\n    runs-on: ubuntu-latest\n" + steps, top="on: push\npermissions: write-all\n")},
         {"permissions-broad"}),
        ("job write without marker fails", {"w.yml": wf(
            "  a:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: write\n" + steps)},
         {"permissions-broad"}),
        ("job marker without a reason fails", {"w.yml": wf(
            "  a:\n    runs-on: ubuntu-latest\n    # public-ci: allow-permissions\n"
            "    permissions:\n      packages: write\n" + steps)}, {"permissions-broad"}),
        ("job write with marker passes", {"w.yml": wf(
            "  a:\n    runs-on: ubuntu-latest\n"
            "    # public-ci: allow-permissions publishes release assets on tag pushes\n"
            "    # (a second comment line)\n"
            "    permissions:\n      contents: write\n" + steps)}, set()),
        ("inline marker passes", {"w.yml": wf(
            "  a:\n    runs-on: ubuntu-latest\n"
            "    permissions:  # public-ci: allow-permissions needs to read PRs\n"
            "      pull-requests: read\n" + steps)}, set()),
        ("job contents read passes", {"w.yml": wf(
            "  a:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n"
            "      issues: none\n" + steps)}, set()),
        # unpinned-action (check_pins.py's classifier)
        ("pinned actions pass", {"w.yml": wf(
            "  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v7\n"
            f"      - uses: docker/login-action@{sha} # v4\n")}, set()),
        ("third-party tag fails", {"w.yml": wf(
            "  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: docker/login-action@v4\n")},
         {"unpinned-action"}),
        ("branch ref fails", {"w.yml": wf(
            "  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@main\n")},
         {"unpinned-action"}),
        # reusable workflows
        ("local reusable workflow is followed and passes", {
            "w.yml": wf("  call:\n    uses: ./.github/workflows/r.yml\n"),
            ".github/workflows/r.yml": "on:\n  workflow_call:\npermissions:\n  contents: read\n"
                                       "jobs:\n  a:\n    runs-on: ubuntu-latest\n" + steps}, set()),
        ("local reusable workflow with a self-hosted job fails", {
            "w.yml": wf("  call:\n    uses: ./.github/workflows/r.yml\n"),
            ".github/workflows/r.yml": "on:\n  workflow_call:\npermissions:\n  contents: read\n"
                                       "jobs:\n  a:\n    runs-on: self-hosted\n" + steps},
         {"self-hosted"}),
        ("remote reusable workflow on a SHA passes", {"w.yml": wf(
            f"  call:\n    uses: example/ci/.github/workflows/r.yml@{sha}\n")}, set()),
        ("remote reusable workflow on a tag fails", {"w.yml": wf(
            "  call:\n    uses: example/ci/.github/workflows/r.yml@v1\n")}, {"unpinned-action"}),
        # composite actions
        ("composite action, pinned, passes", {
            "w.yml": wf("  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ./.github/actions/x\n"),
            ".github/actions/x/action.yml": "name: x\nruns:\n  using: composite\n  steps:\n"
                                            f"    - uses: example/tool@{sha}\n"}, set()),
        ("composite action, third-party tag, fails", {
            "w.yml": wf("  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ./.github/actions/x\n"),
            ".github/actions/x/action.yml": "name: x\nruns:\n  using: composite\n  steps:\n"
                                            "    - uses: example/tool@v2\n"}, {"unpinned-action"}),
        ("docker action, floating image, fails", {
            "w.yml": wf("  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ./.github/actions/d\n"),
            ".github/actions/d/action.yml": "name: d\nruns:\n  using: docker\n"
                                            "  image: docker://alpine\n"}, {"unpinned-action"}),
        # local-action-missing / parse-error
        ("missing local action fails", {"w.yml": wf(
            "  a:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ./nowhere\n")},
         {"local-action-missing"}),
        ("invalid YAML fails", {"w.yml": "on: [push\n"}, {"parse-error"}),
    ]

    saved_summary = os.environ.pop("GITHUB_STEP_SUMMARY", None)
    try:
        for name, files, want in cases:
            with tempfile.TemporaryDirectory() as tmp:
                for rel, body in files.items():
                    p = Path(tmp, rel)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(body, encoding="utf-8")
                found, _ = run(Path(tmp), [Path(tmp, "w.yml")])
                got = {f.rule for f in found}
                if got != want:
                    failures.append(f"{name}: got {sorted(got)}, want {sorted(want)} "
                                    f"({[f.detail for f in found]})")
                with contextlib.redirect_stdout(io.StringIO()):
                    code = main(["--root", tmp, str(Path(tmp, "w.yml"))])
                if code != (1 if want else 0):
                    failures.append(f"{name}: exit {code}, want {1 if want else 0}")
        # The default target set: <root>/.github/workflows plus <root>/.github/actions.
        with tempfile.TemporaryDirectory() as tmp:
            for rel, body in {".github/workflows/a.yml": hosted,
                              ".github/actions/x/action.yml":
                                  "runs:\n  using: composite\n  steps:\n    - uses: example/t@v1\n"}.items():
                p = Path(tmp, rel)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
            found, n = run(Path(tmp), default_targets(Path(tmp)))
            if n != 2 or {f.rule for f in found} != {"unpinned-action"}:
                failures.append(f"default targets: {n} file(s), {[f.rule for f in found]}")
        # Line numbers point at the offending key.
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "w.yml").write_text(wf("  a:\n    runs-on: big-box\n" + steps),
                                          encoding="utf-8")
            found, _ = run(Path(tmp), [Path(tmp, "w.yml")])
        if [f.line for f in found] != [7]:
            failures.append(f"line number: got {[f.line for f in found]}, want [7]")
    finally:
        if saved_summary is not None:
            os.environ["GITHUB_STEP_SUMMARY"] = saved_summary

    for f in failures:
        print("SELF-TEST FAIL:", f)
    print("self-test:", "FAILED" if failures else "ok",
          f"({len(cases)} fixture(s), {len(failures)} failure(s))")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("paths", nargs="*", help="workflow directories or files "
                    "(default: <root>/.github/workflows and <root>/.github/actions)")
    ap.add_argument("--root", default=".", help="base that `uses: ./...` resolves against")
    ap.add_argument("--self-test", action="store_true", help="run every rule on fixtures")
    args = ap.parse_args(argv)
    if yaml is None:
        print("check_public_ci.py needs PyYAML (pip install pyyaml)", file=sys.stderr)
        return 2
    if args.self_test:
        return self_test()
    root = Path(args.root)
    files = expand(args.paths) if args.paths else default_targets(root)
    if not files:
        print(f"no workflow files found under {root}", file=sys.stderr)
        return 2
    findings, n = run(root, files)
    report = render(findings, n)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
