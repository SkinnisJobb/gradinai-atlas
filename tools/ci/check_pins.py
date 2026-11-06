#!/usr/bin/env python3
"""check_pins.py: list every image and action reference that is not pinned (TASK-1357).

THE RULE (owner decision, 2026-10-25). A container image is referenced by tag PLUS digest,
`python:3.12-slim@sha256:...`: the digest makes the build reproducible, the tag tells
Dependabot (.github/dependabot.yml) which line to follow when it opens the PR that moves the
digest. Never `latest`, never a bare tag. A workflow `uses:` is pinned to a full commit SHA,
or at least to a version tag (`@v4`), never a branch.

FIRST-PARTY TAGS, THIRD-PARTY SHAS (TASK-1420, 2026-10-30). A version tag is accepted only for
an action owned by GitHub itself (FIRST_PARTY_OWNERS: `actions/`, `github/`). Those run on
GitHub's runners under GitHub's token anyway: whoever can move `actions/checkout@v7` can already
change the runner, so a SHA buys nothing against that party. Every other owner (`docker/`, ...)
must be pinned to a full commit SHA, with the version as a trailing comment for Dependabot
(`docker/build-push-action@<sha> # v7.4.0`): a third-party tag is moved by whoever holds that
repo (the tj-actions/changed-files compromise of 2025 moved every tag at once), and `--enforce`
fails on it as `third-party-tag`.

WHAT IS SCANNED. Every tracked file (git ls-files; a directory walk outside a checkout):
  - Dockerfiles / Containerfiles: each `FROM` (stage aliases and `scratch` skipped) and each
    `COPY --from=<image>` that names an image rather than a stage. A NAMED BUILD CONTEXT
    (`docker build --build-context repo=.`, build-push-action's `build-contexts:`) is a local
    directory, not an image: the Dockerfile declares it in a comment, `# build-context: repo`,
    and `--from=repo` / `FROM repo` are then skipped like a stage (TASK-1483: mcp/ and
    deploy/caddy/ reach the repository's LICENSE and NOTICE that way);
  - compose files: any tracked .yml/.yaml outside .github/ with a top-level `services:` key,
    so `docker-compose*.yml`, `mcp/docker-compose*.yml` and the `deploy/*.yml` overrides alike;
    each `image:` line (a `build:`-only service has no reference to pin);
  - OpenShift manifests: every .yml/.yaml under deploy/openshift/, with or without a top-level
    `services:` key (a Kubernetes manifest has none); each `image:` line, containers and
    initContainers alike, read the same way as in a compose file (TASK-1481);
  - workflows: each `uses:` in .github/workflows/*.yml (`./local` actions skipped).

STATUSES.
  image   ok           tag + digest: reproducible, and the bot can follow it
          digest-only  pinned, but no tag: Dependabot cannot tell which line to follow
          unpinned     a tag and no digest: whatever the registry serves that day
          floating     no tag at all, or `latest`
          variable     built from `${...}`: not checkable statically
  action  sha          a full 40-hex commit SHA
          tag          a version tag (`v4`, `v4.1.2`) on a first-party action: acceptable
          third-party-tag  a version tag on any other owner's action: a SHA is required
          unpinned     a branch or anything else

ZERO-VER (0.x) RULE. Every pin `name==0.x...` in a tracked requirements*.txt must be named in
the `patterns` of the ZEROVER_GROUP group in .github/dependabot.yml, so its minor bumps (which
may break, under semver) get their own PR instead of riding the routine pip-minor-patch one,
AND in the `exclude-patterns` of every group in EXCLUDING_GROUPS: groups that overlap by name
let an open PR of one claim the package before ZEROVER_GROUP runs (TASK-1361; the comment in
dependabot.yml has the log). Listed as `zerover` rows: `ok` when named everywhere, `missing`
when not in ZEROVER_GROUP's patterns, `not-excluded` when an excluding group still contains it.
Report only: --enforce ignores it.

MODES. Report mode (the default) prints the table, writes it to $GITHUB_STEP_SUMMARY when
set, and exits 0 whatever it found. `--enforce` exits 1 on any `unpinned`, `floating` or
`third-party-tag` row: pins.yml runs it that way since TASK-1420, so an unpinned reference
fails the build. `--self-test` proves the classifier and, end to end, that `--enforce` fails a
tree with an unpinned image or third-party action and passes a pinned one.

Usage:
  python tools/ci/check_pins.py [--enforce] [--root DIR]
  python tools/ci/check_pins.py --self-test

Stdlib only. Exit 0 report / clean, 1 enforce with findings or a failed self-test, 2 usage.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DOCKERFILE_RE = re.compile(r"(dockerfile|containerfile)", re.IGNORECASE)
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_TAG_RE = re.compile(r"^v?\d+(\.\d+)*$")
# Owners whose actions may be pinned to a version tag; everyone else needs a commit SHA.
FIRST_PARTY_OWNERS = frozenset({"actions", "github"})
TOP_SERVICES_RE = re.compile(r"^services:\s*(#.*)?$", re.MULTILINE)
IMAGE_LINE_RE = re.compile(r"^\s*-?\s*image:\s*(.+?)\s*$")
USES_LINE_RE = re.compile(r"^\s*-?\s*uses:\s*(.+?)\s*$")
REQ_PIN_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*0\.")
PATTERNS_RE = re.compile(r"^\s*patterns:\s*\[(.*)\]\s*(#.*)?$")
EXCLUDE_PATTERNS_RE = re.compile(r"^\s*exclude-patterns:\s*\[(.*)\]\s*(#.*)?$")

DEPENDABOT_FILE = ".github/dependabot.yml"
# Kubernetes manifests have no top-level `services:`; every YAML here is scanned for `image:`.
OPENSHIFT_DIR = "deploy/openshift/"
ZEROVER_GROUP = "pip-zerover-minor-maybe-breaking"
EXCLUDING_GROUPS = ("pip-minor-patch", "pip-major")

BAD_IMAGE = {"unpinned", "floating"}
BAD_ACTION = {"unpinned", "third-party-tag"}


@dataclass
class Ref:
    kind: str  # "image" or "action"
    path: str
    line: int
    ref: str
    status: str


def _strip_value(raw: str) -> str:
    """A YAML scalar as written: trailing comment off, surrounding quotes off."""
    value = raw.strip()
    if value[:1] in ("'", '"'):
        end = value.find(value[0], 1)
        return value[1:end] if end > 0 else value[1:]
    return re.sub(r"\s+#.*$", "", value).strip()


def classify_image(ref: str) -> str:
    if "$" in ref:
        return "variable"
    if DIGEST_RE.search(ref):
        name = ref.split("@", 1)[0]
        last = name.rsplit("/", 1)[-1]
        return "ok" if ":" in last else "digest-only"
    last = ref.rsplit("/", 1)[-1]
    if ":" not in last or last.endswith(":latest"):
        return "floating"
    return "unpinned"


def classify_action(ref: str) -> str:
    if "@" not in ref:
        return "unpinned"
    version = ref.rsplit("@", 1)[1]
    if SHA_RE.match(version):
        return "sha"
    if VERSION_TAG_RE.match(version):
        owner = ref.split("/", 1)[0].lower()
        return "tag" if owner in FIRST_PARTY_OWNERS else "third-party-tag"
    return "unpinned"


BUILD_CONTEXT_RE = re.compile(r"^\s*#\s*build-context:\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*$", re.MULTILINE)


def scan_dockerfile(path: str, text: str) -> list[Ref]:
    refs: list[Ref] = []
    # Declared named build contexts count as stages: a directory the build is handed, no image.
    stages: set[str] = {m.group(1).lower() for m in BUILD_CONTEXT_RE.finditer(text)}
    # Join backslash continuations, remembering the line each instruction starts on.
    logical: list[tuple[int, str]] = []
    buf, start = "", 0
    for n, line in enumerate(text.splitlines(), 1):
        if not buf:
            start = n
            if line.lstrip().startswith("#"):
                continue
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        logical.append((start, buf + line))
        buf = ""
    if buf:
        logical.append((start, buf))

    for n, line in logical:
        words = line.split()
        if not words:
            continue
        instr = words[0].upper()
        if instr == "FROM":
            args = [w for w in words[1:] if not w.startswith("--")]
            if not args:
                continue
            image = args[0]
            if len(args) >= 3 and args[1].upper() == "AS":
                stages.add(args[2].lower())
            if image.lower() == "scratch" or image.lower() in stages:
                continue
            refs.append(Ref("image", path, n, image, classify_image(image)))
        elif instr == "COPY":
            for w in words[1:]:
                if w.startswith("--from="):
                    src = w.split("=", 1)[1]
                    if src.lower() in stages or src.isdigit():
                        continue
                    refs.append(Ref("image", path, n, src, classify_image(src)))
    return refs


def scan_compose(path: str, text: str) -> list[Ref]:
    refs = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        m = IMAGE_LINE_RE.match(line)
        if m:
            image = _strip_value(m.group(1))
            if image:
                refs.append(Ref("image", path, n, image, classify_image(image)))
    return refs


def scan_workflow(path: str, text: str) -> list[Ref]:
    refs = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        m = USES_LINE_RE.match(line)
        if not m:
            continue
        uses = _strip_value(m.group(1))
        if uses.startswith("./"):
            continue
        if uses.startswith("docker://"):
            image = uses[len("docker://"):]
            refs.append(Ref("image", path, n, image, classify_image(image)))
        else:
            refs.append(Ref("action", path, n, uses, classify_action(uses)))
    return refs


def _norm(name: str) -> str:
    """PEP 503 name normalisation: `Foo_Bar` and `foo-bar` are one package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def zerover_pins(path: str, text: str) -> list[Ref]:
    """Each `name==0.x` pin in a requirements file (extras such as `[standard]` dropped)."""
    refs = []
    for n, line in enumerate(text.splitlines(), 1):
        m = REQ_PIN_RE.match(line)
        if m:
            refs.append(Ref("zerover", path, n, _norm(m.group(1)), ""))
    return refs


def zerover_group_patterns(text: str, group: str = ZEROVER_GROUP,
                           pattern_re: re.Pattern = PATTERNS_RE) -> set[str] | None:
    """The one-line `patterns: [...]` of `group` (ZEROVER_GROUP by default), or None when
    the group is absent. With EXCLUDE_PATTERNS_RE, its `exclude-patterns: [...]` instead."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() != f"{group}:":
            continue
        indent = len(line) - len(line.lstrip())
        for nxt in lines[i + 1:]:
            if nxt.strip() and not nxt.lstrip().startswith("#"):
                if len(nxt) - len(nxt.lstrip()) <= indent:
                    break
                m = pattern_re.match(nxt)
                if m:
                    return {_norm(_strip_value(p)) for p in m.group(1).split(",") if p.strip()}
        return set()
    return None


def zerover_exclusions(text: str) -> dict[str, set[str]]:
    """Each EXCLUDING_GROUPS group present in `text` -> its `exclude-patterns` (empty if none)."""
    out = {}
    for g in EXCLUDING_GROUPS:
        ex = zerover_group_patterns(text, g, EXCLUDE_PATTERNS_RE)
        if ex is not None:
            out[g] = ex
    return out


def check_zerover(pins: list[Ref], patterns: set[str] | None,
                  exclusions: dict[str, set[str]] | None = None) -> list[Ref]:
    named = patterns or set()
    for r in pins:
        if not (r.ref in named or "*" in named):
            r.status = "missing"
        elif any(r.ref not in ex for ex in (exclusions or {}).values()):
            r.status = "not-excluded"
        else:
            r.status = "ok"
    return pins


def tracked_files(root: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            check=True, capture_output=True, text=True,
        ).stdout
        return sorted(p for p in out.splitlines() if p)
    except (OSError, subprocess.CalledProcessError):
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
            for f in filenames:
                files.append(Path(dirpath, f).relative_to(root).as_posix())
        return sorted(files)


def scan(root: Path) -> list[Ref]:
    refs: list[Ref] = []
    pins: list[Ref] = []
    patterns: set[str] | None = None
    exclusions: dict[str, set[str]] = {}
    for rel in tracked_files(root):
        full = root / rel
        name = full.name
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if rel == DEPENDABOT_FILE:
            patterns = zerover_group_patterns(text)
            exclusions = zerover_exclusions(text)
        if name.startswith("requirements") and name.endswith(".txt"):
            pins += zerover_pins(rel, text)
        elif DOCKERFILE_RE.search(name) and not name.endswith((".md", ".py", ".sh")):
            refs += scan_dockerfile(rel, text)
        elif rel.startswith(".github/workflows/") and name.endswith((".yml", ".yaml")):
            refs += scan_workflow(rel, text)
        elif name.endswith((".yml", ".yaml")) and not rel.startswith(".github/"):
            if TOP_SERVICES_RE.search(text) or rel.startswith(OPENSHIFT_DIR):
                refs += scan_compose(rel, text)
    return refs + check_zerover(pins, patterns, exclusions)


def is_bad(r: Ref) -> bool:
    return r.status in (BAD_IMAGE if r.kind == "image" else BAD_ACTION)


def render(refs: list[Ref], enforce: bool = False) -> str:
    bad = [r for r in refs if is_bad(r)]
    images = [r for r in refs if r.kind == "image"]
    actions = [r for r in refs if r.kind == "action"]
    zerover = [r for r in refs if r.kind == "zerover"]
    missing = [r for r in zerover if r.status in ("missing", "not-excluded")]
    out = ["## Pin check (TASK-1357)", ""]
    out.append(
        f"{len(images)} image reference(s), {len(actions)} action reference(s); "
        f"**{len(bad)} not pinned**. "
        + ("Enforce mode: any of these fails the build." if enforce
           else "Report mode: this does not fail the build.")
    )
    out.append("")
    out.append(
        f"{len(zerover)} 0.x pip pin(s); **{len(missing)} not named** in the "
        f"`{ZEROVER_GROUP}` group of {DEPENDABOT_FILE}, or not excluded from "
        f"{', '.join(EXCLUDING_GROUPS)} (report only, never fails the build)."
    )
    out.append("")
    out.append("| kind | status | reference | where |")
    out.append("|---|---|---|---|")
    order = {"floating": 0, "unpinned": 1, "third-party-tag": 1, "missing": 1, "not-excluded": 1, "variable": 2, "digest-only": 3,
             "tag": 4, "ok": 5, "sha": 6}
    for r in sorted(refs, key=lambda r: (r.kind, order.get(r.status, 9), r.path, r.line)):
        flag = " **!**" if is_bad(r) or r.status in ("missing", "not-excluded") else ""
        out.append(f"| {r.kind} | {r.status}{flag} | `{r.ref}` | {r.path}:{r.line} |")
    out.append("")
    return "\n".join(out)


def self_test() -> int:
    failures = []

    def expect(got, want, what):
        if got != want:
            failures.append(f"{what}: got {got!r}, want {want!r}")

    d = "sha256:" + "a" * 64
    expect(classify_image(f"python:3.12-slim@{d}"), "ok", "tag+digest")
    expect(classify_image(f"caddy@{d}"), "digest-only", "digest no tag")
    expect(classify_image(f"localhost:5000/caddy@{d}"), "digest-only", "registry port, no tag")
    expect(classify_image("postgres:16"), "unpinned", "bare tag")
    expect(classify_image("localhost:5000/postgres:16"), "unpinned", "registry port + tag")
    expect(classify_image("postgres"), "floating", "no tag")
    expect(classify_image("ghcr.io/x/y:latest"), "floating", "latest")
    expect(classify_image("${IMG}:1"), "variable", "variable")
    expect(classify_action("actions/checkout@" + "b" * 40), "sha", "sha")
    expect(classify_action("actions/checkout@v4"), "tag", "major tag")
    expect(classify_action("actions/checkout@v4.1.2"), "tag", "full tag")
    expect(classify_action("actions/checkout@main"), "unpinned", "branch")
    expect(classify_action("github/codeql-action/init@v3"), "tag", "first-party github/ tag")
    expect(classify_action("docker/build-push-action@v7"), "third-party-tag", "third-party tag")
    expect(classify_action("docker/build-push-action@v7.4.0"), "third-party-tag",
           "third-party full tag")
    expect(classify_action("docker/build-push-action@" + "c" * 40), "sha", "third-party sha")
    expect(classify_action("docker/build-push-action@main"), "unpinned", "third-party branch")
    expect(classify_action("actions/checkout"), "unpinned", "no ref")

    df = (
        "# FROM ignored:comment\n"
        f"FROM --platform=linux/amd64 golang:1.22@{d} AS build\n"
        "FROM build AS second\n"
        "FROM scratch\n"
        "COPY --from=build /a /a\n"
        "COPY --from=nginx:1 /b /b\n"
        "FROM \\\n  python:3.12-slim\n"
    )
    got = [(r.line, r.ref, r.status) for r in scan_dockerfile("Dockerfile", df)]
    expect(got, [(2, f"golang:1.22@{d}", "ok"), (6, "nginx:1", "unpinned"),
                 (7, "python:3.12-slim", "unpinned")], "dockerfile scan")
    # A declared named build context is skipped; an undeclared bare name is still an image.
    df2 = (
        "# build-context: repo\n"
        f"FROM python:3.12-slim@{d}\n"
        "COPY --from=repo LICENSE NOTICE /licenses/\n"
        "COPY --from=other LICENSE /x/\n"
    )
    got = [(r.line, r.ref, r.status) for r in scan_dockerfile("Dockerfile", df2)]
    expect(got, [(2, f"python:3.12-slim@{d}", "ok"), (4, "other", "floating")],
           "dockerfile scan, named build context")

    compose = (
        "services:\n  db:\n    image: \"postgres:16\"  # comment\n"
        "  # image: commented:out\n  api:\n    build: .\n"
        f"  t:\n    image: cloudflare/cloudflared@{d}\n"
    )
    got = [(r.line, r.ref, r.status) for r in scan_compose("c.yml", compose)]
    expect(got, [(3, "postgres:16", "unpinned"), (8, f"cloudflare/cloudflared@{d}", "digest-only")],
           "compose scan")

    wf = (
        "steps:\n  - uses: actions/checkout@v4\n  - uses: ./local\n"
        "  - name: x\n    uses: 'foo/bar@main'\n  - uses: docker://alpine:3\n"
    )
    got = [(r.line, r.kind, r.status) for r in scan_workflow("w.yml", wf)]
    expect(got, [(2, "action", "tag"), (5, "action", "unpinned"), (6, "image", "unpinned")],
           "workflow scan")

    req = "# c\nfastapi==0.115.6\nuvicorn[standard] == 0.34.0\nmcp==1.12.4\nFoo_Bar==0.1\n"
    pins = zerover_pins("requirements.txt", req)
    expect([(r.line, r.ref) for r in pins], [(2, "fastapi"), (3, "uvicorn"), (5, "foo-bar")],
           "zerover pins")
    dep = (
        "    groups:\n"
        f"      {ZEROVER_GROUP}:\n        applies-to: version-updates\n"
        "        patterns: [\"fastapi\", \"uvicorn\"]  # c\n        update-types: [minor]\n"
        "      other:\n        patterns: [\"*\"]\n"
    )
    pats = zerover_group_patterns(dep)
    expect(pats, {"fastapi", "uvicorn"}, "zerover group patterns")
    expect(zerover_group_patterns("groups:\n  other:\n    patterns: [\"*\"]\n"), None, "no group")
    got = [(r.ref, r.status) for r in check_zerover(pins, pats)]
    expect(got, [("fastapi", "ok"), ("uvicorn", "ok"), ("foo-bar", "missing")], "zerover check")
    dep2 = dep + (
        "      pip-minor-patch:\n        patterns: [\"*\"]\n"
        "        exclude-patterns: [\"fastapi\"]\n        update-types: [minor, patch]\n"
        "      pip-major:\n        update-types: [major]\n"
    )
    exc = zerover_exclusions(dep2)
    expect(exc, {"pip-minor-patch": {"fastapi"}, "pip-major": set()}, "zerover exclusions")
    got = [(r.ref, r.status) for r in check_zerover(pins, pats, exc)]
    expect(got, [("fastapi", "not-excluded"), ("uvicorn", "not-excluded"), ("foo-bar", "missing")],
           "zerover exclusion check")
    exc = {"pip-minor-patch": {"fastapi", "uvicorn"}, "pip-major": {"fastapi", "uvicorn"}}
    got = [(r.ref, r.status) for r in check_zerover(pins, pats, exc)]
    expect(got, [("fastapi", "ok"), ("uvicorn", "ok"), ("foo-bar", "missing")],
           "zerover exclusion check, all excluded")

    # End to end: --enforce on a whole tree fails an unpinned image and an unpinned third-party
    # action, and passes the same kinds of reference once pinned. A temp dir is not a checkout,
    # so tracked_files() walks it.
    import contextlib
    import io
    import tempfile
    sha = "d" * 40
    trees = {
        "unpinned image": ({"Dockerfile": "FROM python:3.12-slim\n"}, 1),
        "floating compose image": (
            {"docker-compose.yml": "services:\n  db:\n    image: postgres\n"}, 1),
        "third-party action tag": (
            {".github/workflows/w.yml": "steps:\n  - uses: docker/login-action@v4\n"}, 1),
        "unpinned openshift image": (
            {"deploy/openshift/base/deployment.yaml": (
                "apiVersion: apps/v1\nkind: Deployment\nspec:\n  template:\n    spec:\n"
                "      containers:\n        - name: atlas-api\n"
                "          image: ghcr.io/example/atlas-api:1.0\n")}, 1),
        "unpinned openshift init image": (
            {"deploy/openshift/base/job.yml": (
                "kind: Job\nspec:\n  template:\n    spec:\n      initContainers:\n"
                "        - image: busybox:1.36\n      containers:\n"
                f"        - image: ghcr.io/example/atlas-api:1.0@{d}\n")}, 1),
        "third-party action branch": (
            {".github/workflows/w.yml": "steps:\n  - uses: docker/login-action@master\n"}, 1),
        "pinned": ({
            "Dockerfile": f"FROM python:3.12-slim@{d}\n",
            "docker-compose.yml": f"services:\n  db:\n    image: postgres:16@{d}\n",
            "deploy/openshift/base/deployment.yaml": (
                "kind: Deployment\nspec:\n  template:\n    spec:\n      containers:\n"
                f"        - name: atlas-api\n          image: ghcr.io/example/atlas-api:1.0@{d}\n"),
            ".github/workflows/w.yml": (
                "steps:\n  - uses: actions/checkout@v7\n"
                f"  - uses: docker/login-action@{sha} # v4.6.0\n"),
        }, 0),
    }
    saved_summary = os.environ.pop("GITHUB_STEP_SUMMARY", None)
    try:
        for what, (files, want) in trees.items():
            with tempfile.TemporaryDirectory() as tmp:
                for rel, body in files.items():
                    f = Path(tmp, rel)
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_text(body, encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    got = main(["--enforce", "--root", tmp])
                expect(got, want, f"--enforce exit code, {what}")
    finally:
        if saved_summary is not None:
            os.environ["GITHUB_STEP_SUMMARY"] = saved_summary

    for f in failures:
        print("SELF-TEST FAIL:", f)
    print("self-test:", "FAILED" if failures else "ok", f"({len(failures)} failure(s))")
    return 1 if failures else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--enforce", action="store_true", help="exit 1 on any unpinned or third-party-tag reference")
    ap.add_argument("--self-test", action="store_true", help="prove the classifier on known cases")
    ap.add_argument("--root", default=None, help="repo root (default: this script's repo)")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()

    root = Path(args.root) if args.root else Path(__file__).resolve().parents[2]
    refs = scan(root)
    report = render(refs, enforce=args.enforce)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    if args.enforce and any(is_bad(r) for r in refs):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
