#!/usr/bin/env python3
"""check_env_example.py: every variable Atlas reads is in the OSS install's .env.example (OSS-10).

WHAT COUNTS AS "READ BY THE CODE". Every string literal that is exactly an ATLAS_* variable
name (the whole string, e.g. "ATLAS_RBAC", never a name inside a sentence) in the shipped
code: backend/*.py, backend/routers/*.py, backend/identity/*.py, backend/retrieval/*.py and mcp/app/*.py, minus the test-only modules
(verify_*, test_*, testdb, testseed, *testkit*). That catches every way the code names a variable:
os.environ.get("X"), read_secret("X"), and a constant X_ENV = "X" used later. backend/settings.py
and mcp/app/settings.py are in that set.

THE RULES, over deploy/oss/.env.example:

  1. present   every such variable appears in the file as a word, set or in a comment. A
               secret read through read_secret("X") may appear as its X_FILE form instead.
  2. real      every assignment, live or commented (`X=...` / `# X=...`), names a variable the
               code reads (or the X_FILE form of a secret it reads), a ${X} the OSS compose
               file or a shipped overlay beside it (deploy/oss/*.yml: retrieval.yml,
               oidc.yml, ...) interpolates, a name a DEPLOY SCRIPT reads from .env itself
               (DEPLOY_SCRIPTS below), or a PLANNED one below.
  3. planned   a PLANNED variable (documented in an ADR, not built yet) is never set live.
  4. no secret no live assignment of a secret (a name the code reads through a secret reader, or
               one ending in PEPPER, PASSWORD, TOKEN, SECRET, KEY or DB_URL); its *_FILE path
               form is fine: .env holds no secret.

Usage:
  python tools/ci/check_env_example.py              check the tree (run from the repo root)
  python tools/ci/check_env_example.py --self-test  prove each rule fails on its bad case

Exit 0 clean, 1 on a violation, 2 on a usage error. Standard library only.
"""

from __future__ import annotations

import ast
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = Path("deploy/oss/.env.example")
COMPOSE = Path("deploy/oss/docker-compose.yml")
CODE_GLOBS = ("backend/*.py", "backend/routers/*.py", "backend/identity/*.py", "backend/retrieval/*.py",
              "mcp/app/*.py")
TEST_ONLY = re.compile(r"(^|/)(verify_[^/]*|test_[^/]*|testdb|testseed|[^/]*testkit[^/]*)\.py$")

#: Deploy-side scripts that read named settings from deploy/oss/.env themselves. They are not the
#: Atlas code, so rule 1 does not demand their names; an exact ATLAS_* string literal in one
#: counts as a reader for rule 2. render-gateway.py reads ATLAS_GATEWAY_SESSION_KEY_FILE
#: (TASK-1460).
DEPLOY_SCRIPTS = ("scripts/render-gateway.py",)

#: Documented in an ADR, read by no code yet. May appear only commented out.
#: Empty now: the retrieval add-on's variables (ADR 0044), the last planned set, are read by
#: backend/retrieval/ since STORY-atlas-retrieval-indexing. The self-test keeps rule 3 proven.
PLANNED: set = set()

NAME = re.compile(r"ATLAS_[A-Z0-9_]*[A-Z0-9]")
ASSIGN = re.compile(r"^\s*(#)?\s*(ATLAS_[A-Z0-9_]*[A-Z0-9])=")
INTERP = re.compile(r"\$\{(ATLAS_[A-Z0-9_]*[A-Z0-9])")
SECRET_SHAPED = re.compile(r"(PEPPER|PASSWORD|TOKEN|SECRET|KEY|DB_URL)$")
SECRET_READERS = {"read_secret", "_require_secret"}


def code_names(root: Path) -> tuple[dict, set]:
    """({name: [files]}, {secret names passed to a secret reader as a literal or constant})."""
    names: dict = {}
    secrets: set = set()
    files = sorted({p for g in CODE_GLOBS for p in root.glob(g)})
    for path in files:
        rel = path.relative_to(root).as_posix()
        if TEST_ONLY.search(rel):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        consts = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and NAME.fullmatch(node.value):
                names.setdefault(node.value, []).append(rel)
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str) and NAME.fullmatch(node.value.value)):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        consts[t.id] = node.value.value
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and node.args:
                fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if fn in SECRET_READERS:
                    a = node.args[0]
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        secrets.add(a.value)
                    elif isinstance(a, ast.Attribute) and a.attr in consts:
                        secrets.add(consts[a.attr])
                    elif isinstance(a, ast.Name) and a.id in consts:
                        secrets.add(consts[a.id])
    return names, secrets


def script_names(root: Path) -> set:
    """The ATLAS_* names the DEPLOY_SCRIPTS name as an exact string literal."""
    out = set()
    for rel in DEPLOY_SCRIPTS:
        path = root / rel
        if path.exists():
            out |= {n.value for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and NAME.fullmatch(n.value)}
    return out


def compose_names(path: Path) -> set:
    """The ${ATLAS_*} names interpolated by the compose file and by every overlay shipped beside
    it (deploy/oss/*.yml): compose reads .env for every file on the command line alike."""
    if not path.exists():
        return set()
    files = [path] + sorted(p for p in path.parent.glob("*.yml") if p != path)
    return {n for f in files for n in INTERP.findall(f.read_text(encoding="utf-8"))}


def check(root: Path, env_example: Path, compose: Path) -> list:
    problems = []
    names, secrets = code_names(root)
    text = (root / env_example).read_text(encoding="utf-8")
    words = set(NAME.findall(text))
    interp = compose_names(root / compose) | script_names(root)

    # 1. present
    for name in sorted(names):
        if name in words or (name in secrets and name + "_FILE" in words):
            continue
        problems.append(f"{env_example}: {name} is read by {', '.join(sorted(set(names[name])))} "
                        f"but is not in .env.example (add it, set or commented, with a comment)")
    # 2-4, line by line
    for n, line in enumerate(text.splitlines(), 1):
        m = ASSIGN.match(line)
        if not m:
            continue
        commented, name = bool(m.group(1)), m.group(2)
        file_form = name.endswith("_FILE") and name[: -len("_FILE")] in secrets
        if name not in names and name not in interp and name not in PLANNED and not file_form:
            problems.append(f"{env_example}:{n}: {name} is read by no code and no ${{...}} in {compose} "
                            f"(a typo, or a variable that does not exist)")
        if name in PLANNED and not commented:
            problems.append(f"{env_example}:{n}: {name} is not built yet (planned); keep it commented")
        if not commented and (name in secrets or SECRET_SHAPED.search(name)) and not name.endswith("_FILE"):
            problems.append(f"{env_example}:{n}: {name} looks like a secret; .env holds none "
                            f"(use a file under ./secrets/ and {name}_FILE)")
    return problems


def self_test() -> int:
    rc = 0
    PLANNED.add("ATLAS_PLANNED_THING")  # a fixture-only planned name, so rule 3 stays proven
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "backend").mkdir()
        (root / "mcp" / "app").mkdir(parents=True)
        (root / "deploy" / "oss").mkdir(parents=True)
        (root / "backend" / "settings.py").write_text(
            'import os\nRBAC_ENV = "ATLAS_RBAC"\nPEPPER_ENV = "ATLAS_TOKEN_PEPPER"\n'
            'def f():\n    os.environ.get(RBAC_ENV)\n    read_secret(PEPPER_ENV)\n'
            '"""prose naming ATLAS_NOT_A_SETTING is ignored"""\n', encoding="utf-8")
        (root / "backend" / "verify_x.py").write_text('X = "ATLAS_TEST_ONLY"\n', encoding="utf-8")
        (root / "mcp" / "app" / "settings.py").write_text('E = "ATLAS_MCP_THING"\n', encoding="utf-8")
        (root / "scripts").mkdir()
        (root / DEPLOY_SCRIPTS[0]).write_text('K = "ATLAS_SCRIPT_THING_FILE"\n', encoding="utf-8")
        (root / COMPOSE).write_text("x: ${ATLAS_PUBLIC_HOST:?set it}\n", encoding="utf-8")
        (root / COMPOSE.parent / "overlay.yml").write_text("y: ${ATLAS_OVERLAY_SOURCE:-/dev/null}\n",
                                                          encoding="utf-8")
        good = ("ATLAS_RBAC=projects\n# ATLAS_TOKEN_PEPPER_FILE=/run/secrets/token_pepper\n"
                "# ATLAS_MCP_THING=1\nATLAS_PUBLIC_HOST=a.example.com\n# ATLAS_PLANNED_THING=off\n")
        cases = [
            (0, "everything present (commented counts; a secret by its _FILE form)", good),
            (1, "a variable the code reads is missing", good.replace("# ATLAS_MCP_THING=1\n", "")),
            (1, "an assignment of a variable nothing reads", good + "ATLAS_RBAC_TYPO=1\n"),
            (1, "a planned variable set live", good + "ATLAS_PLANNED_THING=on\n"),
            (1, "a secret assigned as a value", good + "ATLAS_TOKEN_PEPPER=abc\n"),
            (0, "a secret named only in a comment", good + "# the plain ATLAS_TOKEN_PEPPER is refused\n"),
            (0, "a name only a shipped overlay interpolates", good + "# ATLAS_OVERLAY_SOURCE=/dev/null\n"),
            (0, "a name only a deploy script reads", good + "# ATLAS_SCRIPT_THING_FILE=./secrets/x\n"),
        ]
        for want, label, text in cases:
            (root / ENV_EXAMPLE).write_text(text, encoding="utf-8")
            got = 1 if check(root, ENV_EXAMPLE, COMPOSE) else 0
            ok = got == want
            print(f"self-test {'ok  ' if ok else 'FAIL'} exit={got} (want {want})  {label}")
            rc |= 0 if ok else 1
    return rc


def main(argv: list) -> int:
    if argv[1:] == ["--self-test"]:
        return self_test()
    if argv[1:]:
        print(__doc__.split("Usage:")[1].split("Exit")[0], file=sys.stderr)
        return 2
    problems = check(ROOT, ENV_EXAMPLE, COMPOSE)
    names, _ = code_names(ROOT)
    for p in problems:
        print("FAIL " + p)
    if problems:
        print(f"check_env_example: FAILED ({len(problems)}). See the header of {Path(__file__).name}.")
        return 1
    print(f"check_env_example: clean ({len(names)} variables read by the code, all in {ENV_EXAMPLE})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
