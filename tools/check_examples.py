#!/usr/bin/env python3
"""check_examples.py: every configuration example in docs/oss/examples/ is read by the code that
reads it in production, and passes (TASK-1431, STORY-oss-20). Run by .github/workflows/examples.yml.

NOTHING HERE RE-IMPLEMENTS A RULE. Each kind of example goes through the production function
that reads that kind of file, imported from backend/ and mcp/:

  env/*.env              a fragment layered over deploy/oss/.env.example (`layer`, below, is
                         also what `apply` writes, so CI checks exactly what an operator gets).
                           * every key is a setting the install reads: in the settings
                             catalogue (`settings_catalog.is_described`), which since TASK-1433
                             describes atlas-mcp's variables and the ones only compose reads
                             (every `${...}` in deploy/oss/*.yml, COMPOSE_PROFILES) as well as
                             atlas-api's, or an AZURE_* name the retrieval add-on reads
                             (backend/retrieval/*.py); COMPOSE_PROFILES must also name profiles
                             that compose file defines;
                           * the compose files the profile is started with are the ones its
                             own comment names (`docker compose -f A -f B ... up`), each a
                             shipped file in deploy/oss; `compose-files` prints them for the
                             workflow's `docker compose config`, so CI runs that very command;
                           * every /run/secrets/<name> path the profile sets is a secret those
                             compose files mount into atlas-api (so a path the overlay does not
                             mount, e.g. a wrong suffix, fails here and not at first sign-in);
                           * no secret value: a catalogue secret appears only as its _FILE path;
                           * every value parses: in a CHILD PROCESS whose environment is the
                             layered .env plus the compose file's `x-api-environment` (exactly
                             what atlas-api is started with, plus the `environment:` the
                             profile's overlays give atlas-api), `settings_catalog.describe()`
                             must report no setting that "does not parse" (the catalogue's own
                             parsers: e.g. ATLAS_BACKUP_SCHEDULE's values, the step-up max age's
                             range), the retrieval add-on's
                             own verdict must be `on` or `off` (never `unavailable`), atlas-mcp's
                             settings module (mcp/app/settings.py, which validates at import and
                             exits naming the variable) must import, and a configured group map
                             must load through `identity.groups.load_group_map`.
                         Container paths (/run/secrets/...) are given stand-in files in a
                         temporary directory: throwaway peppers and an unreachable DSN made
                         here, the example group map for the group map, an empty file otherwise.
                         Nothing reads a real secret and nothing connects anywhere.
  guardrails/*.yaml      `guardrails.load_file` (what `atlas-admin guardrails validate|apply`
                         runs), then `guardrails.export` in both formats.
  cicd/*.yaml            `guide_answers.load_answers_file` + `guide_answers.cicd_input` (what
                         `atlas-admin cicd generate --answers` runs), then `cicd.generate` for
                         every platform against every guardrails example; generate re-checks its
                         own output (`check_pipeline`) and raises on a broken pipeline.
  crunch/*.yaml          `guardrails.parse_text` (the CLI's crunch-file parser) and then
                         `crunch.validate_config(cfg, matrix=...)`, the call PUT /api/crunch
                         makes, against every guardrails example as the matrix in force.
  sso/*.yml              the group map through `identity.groups.load_group_map`. Its compose
                         side is the shipped overlay deploy/oss/oidc.yml (no copy here), checked
                         with env/sso-rbac.env by `docker compose config` in the workflow's boot job.

Usage:
  python tools/check_examples.py                      check docs/oss/examples (exit 1 on a problem)
  python tools/check_examples.py --self-test          prove a broken copy of each kind fails
  python tools/check_examples.py apply FRAGMENT... --env FILE
                                                      layer fragments into an .env, in place
  python tools/check_examples.py compose-files FRAGMENT
                                                      the compose files the fragment names, one per
                                                      line, relative to deploy/oss

`apply` uses the standard library only; checking needs backend/requirements.txt installed.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
EXAMPLES = ROOT / "docs" / "oss" / "examples"
ENV_EXAMPLE = ROOT / "deploy" / "oss" / ".env.example"
DEPLOY = ROOT / "deploy" / "oss"
COMPOSE = DEPLOY / "docker-compose.yml"
MCP_SETTINGS = ROOT / "mcp" / "app" / "settings.py"

_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_SECRET_SHAPED = re.compile(r"(PEPPER|PASSWORD|TOKEN|SECRET|KEY|DB_URL)$")
#: The start command a fragment's comment gives: `docker compose -f A -f B ... up`.
_START = re.compile(r"docker compose((?:\s+-f\s+\S+)+)\s+up\b")
_AZURE = re.compile(r"^AZURE_[A-Z0-9_]*[A-Z0-9]$")

#: Container paths given a specific stand-in (relative to the examples directory) instead of an
#: empty file, because the code parses that file.
STAND_INS = {"/run/secrets/oidc_group_map": "sso/oidc-group-map.yml"}
#: Stand-ins made here, throwaway values that let the API's modules import. Never a real secret.
_MADE = {
    "/run/secrets/password_pepper": lambda: base64.b64encode(secrets.token_bytes(32)).decode(),
    "/run/secrets/token_pepper": lambda: base64.b64encode(secrets.token_bytes(32)).decode(),
    # Unreachable on purpose: nothing here connects; the engine is only created.
    "/run/secrets/db_url": lambda: "postgresql://atlas@127.0.0.1:9/atlas",
}


# --------------------------------------------------------------------------- #
# .env fragments: parse and layer (standard library; `apply` uses only this part)
# --------------------------------------------------------------------------- #

class ExampleError(ValueError):
    pass


def parse_env(text: str, where: str) -> list:
    """[(lineno, key, value)] for every assignment. Blank and `#` lines are skipped; anything
    else, a quoted value or an assignment with no comment line right above it, is refused."""
    out, prev_comment = [], False
    for n, raw in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        line = raw.strip()
        if not line:
            prev_comment = False
            continue
        if line.startswith("#"):
            prev_comment = True
            continue
        m = _LINE.match(raw)
        if not m:
            raise ExampleError(f"{where}:{n}: not a KEY=VALUE line")
        key, value = m.group(1), m.group(2)
        if value[:1] in ("'", '"'):
            raise ExampleError(f"{where}:{n}: {key}: quoted value (write it unquoted)")
        out.append((n, key, value, prev_comment))
        prev_comment = False
    return out


def layer(base: str, fragment: str, label: str) -> str:
    """`base` .env text with `fragment`'s assignments applied: a live `KEY=` line of the same key
    is replaced in place, a new key is appended under a `# from <label>` line. Comments in
    `base` (including commented-out `# KEY=` lines) are kept as they are."""
    pairs = [(k, v) for _, k, v, _ in parse_env(fragment, label)]
    want = dict(pairs)
    lines, done = base.replace("\r\n", "\n").rstrip("\n").split("\n"), set()
    for i, raw in enumerate(lines):
        m = _LINE.match(raw)
        if m and m.group(1) in want:
            lines[i] = f"{m.group(1)}={want[m.group(1)]}"
            done.add(m.group(1))
    new = [(k, v) for k, v in pairs if k not in done]
    if new:
        lines += ["", f"# from {label}"] + [f"{k}={v}" for k, v in new]
    return "\n".join(lines) + "\n"


def live(text: str) -> dict:
    return {k: v for _, k, v, _ in parse_env(text, "<layered .env>")}


def cmd_apply(argv: list) -> int:
    if "--env" not in argv or argv.index("--env") == len(argv) - 1:
        print("usage: check_examples.py apply FRAGMENT... --env FILE", file=sys.stderr)
        return 2
    i = argv.index("--env")
    target, fragments = Path(argv[i + 1]), argv[:i] + argv[i + 2:]
    if not fragments:
        print("apply: name at least one fragment", file=sys.stderr)
        return 2
    text = target.read_text(encoding="utf-8")
    for f in fragments:
        text = layer(text, Path(f).read_text(encoding="utf-8"), Path(f).name)
    target.write_text(text, encoding="utf-8")
    print(f"apply: {', '.join(Path(f).name for f in fragments)} -> {target}")
    return 0


def compose_files(fragment: str) -> list:
    """The compose files a fragment's comment starts the stack with, in order; the base file
    alone when it names none. Standard library only (the workflow calls it before pip)."""
    for raw in fragment.replace("\r\n", "\n").split("\n"):
        if raw.lstrip().startswith("#"):
            m = _START.search(raw)
            if m:
                return re.findall(r"-f\s+(\S+)", m.group(1))
    return ["docker-compose.yml"]


def cmd_compose_files(argv: list) -> int:
    if len(argv) != 1:
        print("usage: check_examples.py compose-files FRAGMENT", file=sys.stderr)
        return 2
    print("\n".join(compose_files(Path(argv[0]).read_text(encoding="utf-8"))))
    return 0


# --------------------------------------------------------------------------- #
# Production code, imported lazily
# --------------------------------------------------------------------------- #

def _backend():
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


def _compose(path: Path = COMPOSE):
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _azure_names() -> set:
    """The AZURE_* variables the retrieval add-on reads (entra mode), as literals in its code.
    Kept: the catalogue describes the AZURE_* names compose interpolates, but not these, which
    atlas-api's retrieval add-on reads and the configuration panel does not show."""
    import ast
    names = set()
    for p in sorted((BACKEND / "retrieval").glob("*.py")):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _AZURE.match(node.value):
                names.add(node.value)
    return names


def _api_service(files: list) -> tuple:
    """(secret names mounted into atlas-api, the `environment:` the overlays give atlas-api)
    over `files` (relative to deploy/oss), merged as compose merges them: secrets are unioned,
    a later environment entry wins."""
    mounted, environment = set(), {}
    for f in files:
        svc = ((_compose(DEPLOY / f).get("services") or {}).get("atlas-api") or {})
        for s in svc.get("secrets") or []:
            if isinstance(s, dict):
                mounted.add(s.get("target") or s.get("source"))
            else:
                mounted.add(s)
        env = svc.get("environment") or {}
        if isinstance(env, list):
            env = dict(e.split("=", 1) for e in env if "=" in e)
        if f != "docker-compose.yml":
            environment.update({k: str(v) for k, v in env.items()})
    return mounted, environment


def known_key(key: str) -> bool:
    """In the settings catalogue (atlas-api, atlas-mcp and compose, TASK-1433), or an AZURE_*
    name the retrieval add-on reads. No fallback to mcp/app/*.py or the compose files:
    backend/verify_config_catalogue.py fails when either names a variable the catalogue lacks."""
    _backend()
    import settings_catalog
    return settings_catalog.is_described(key) or key in _azure_names()


def _api_environment(env: dict, files: list = ("docker-compose.yml",)) -> dict:
    """The compose file's `x-api-environment`, plus the `environment:` the overlays in `files`
    give atlas-api, each `${X:?...}` / `${X:-d}` filled from `env`."""
    block = dict(_compose().get("x-api-environment") or {})
    block.update(_api_service(list(files))[1])

    def fill(v):
        def sub(m):
            name, rest = m.group(1), m.group(2) or ""
            if name in env and env[name] != "":
                return env[name]
            return rest[2:] if rest.startswith(":-") else ""
        return re.sub(r"\$\{([A-Z_][A-Z0-9_]*)((?::[-?])[^}]*)?\}", sub, str(v))
    return {k: fill(v) for k, v in block.items()}


def check_env(path: Path, examples: Path) -> list:
    where = "examples/" + path.relative_to(examples).as_posix()
    try:
        frag = path.read_text(encoding="utf-8")
        rows = parse_env(frag, where)
    except ExampleError as exc:
        return [str(exc)]
    problems = []
    if not rows:
        problems.append(f"{where}: sets nothing")
    _backend()
    import settings_catalog
    profiles = set()
    for svc in (_compose().get("services") or {}).values():
        profiles |= set((svc or {}).get("profiles") or [])
    for n, key, value, commented in rows:
        if not commented:
            problems.append(f"{where}:{n}: {key} has no comment line above it saying what it does")
        if key == "COMPOSE_PROFILES":
            bad = [p for p in value.split(",") if p.strip() not in profiles]
            if bad or not value.strip():
                problems.append(f"{where}:{n}: COMPOSE_PROFILES names {bad or [value]}, not a profile "
                                f"of deploy/oss/docker-compose.yml ({', '.join(sorted(profiles))})")
            continue
        if not known_key(key):
            problems.append(f"{where}:{n}: {key} is read by nothing: not in the settings catalogue "
                            f"(atlas-api, atlas-mcp and compose) and not an AZURE_* name the "
                            f"retrieval add-on reads")
        item = settings_catalog.lookup(key)
        if (item is not None and item.secret) or (_SECRET_SHAPED.search(key) and not key.endswith("_FILE")):
            problems.append(f"{where}:{n}: {key} is a secret; give its PATH as {key}_FILE")
    files = compose_files(frag)
    missing = [f for f in files if not (DEPLOY / f).is_file()]
    if missing or files[0] != "docker-compose.yml":
        problems.append(f"{where}: its start command names {missing or files}, not "
                        f"deploy/oss/docker-compose.yml followed by shipped overlays")
        return problems
    mounted = _api_service(files)[0]
    for n, key, value, _ in rows:
        if value.startswith("/run/secrets/") and value[len("/run/secrets/"):] not in mounted:
            problems.append(f"{where}:{n}: {key}={value}, but {' + '.join(files)} mount no secret "
                            f"{value[len('/run/secrets/'):]!r} into atlas-api "
                            f"(mounted: {', '.join(sorted(mounted))})")
    if problems:
        return problems
    # Every profile sits on the minimal one (the README's order): .env.example, minimal, this.
    layered = layer(ENV_EXAMPLE.read_text(encoding="utf-8"),
                    (examples / "env" / "minimal.env").read_text(encoding="utf-8"), "minimal.env")
    layered = layer(layered, frag, path.name)
    return [f"{where}: {p}" for p in evaluate(live(layered), examples, path.name, files)]


def evaluate(env: dict, examples: Path, label: str, files: list = ("docker-compose.yml",)) -> list:
    """Run `--_eval` in a child whose whole environment is `env` (+ the compose API block and
    the overlays' atlas-api environment), with every container path pointed at a stand-in.
    Returns the child's problems."""
    full = dict(env)
    full.update(_api_environment(env, files))
    with tempfile.TemporaryDirectory() as tmp:
        for k, v in list(full.items()):
            if not v.startswith(("/run/", "/var/run/")):
                continue
            stand = Path(tmp) / v.strip("/").replace("/", "_")
            if v in STAND_INS:
                shutil.copyfile(examples / STAND_INS[v], stand)
            elif v in _MADE:
                stand.write_text(_MADE[v](), encoding="utf-8")
            else:
                stand.write_text("stand-in\n", encoding="utf-8")
            full[k] = str(stand)
        # What the interpreter itself needs to start and find its packages; no ATLAS_* is kept.
        for keep in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "LANG", "APPDATA",
                     "LOCALAPPDATA", "USERPROFILE", "VIRTUAL_ENV", "LD_LIBRARY_PATH"):
            if os.environ.get(keep):
                full.setdefault(keep, os.environ[keep])
        # The child imports exactly what this process can (a runner may install the packages
        # in a user site the child would not otherwise find).
        full["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p and os.path.isdir(p))
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_eval"],
                           env=full, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [f"the evaluation of {label} crashed: {r.stderr.strip()[-800:]}"]


def _eval_child() -> int:
    """In the child: the process environment IS the install's. Prints a JSON list of problems."""
    problems = []
    _backend()
    try:
        import settings_catalog
        out = settings_catalog.describe()
        for part in out["parts"]:
            for s in part["settings"]:
                if "error" in s:
                    problems.append(f"{s['name']}: {s['error']}")
            st = part.get("status")
            if st is not None and st not in ("on", "off"):
                problems.append(f"retrieval add-on: {st}")
        if (os.environ.get("ATLAS_RETRIEVAL") or "").strip().lower() == "on":
            st = next(p.get("status") for p in out["parts"] if p["id"] == settings_catalog.RETRIEVAL)
            if st != "on":
                problems.append(f"ATLAS_RETRIEVAL=on but the add-on says {st!r}")
        gm = os.environ.get("ATLAS_OIDC_GROUP_MAP_FILE")
        if gm:
            from identity import groups
            if not groups.load_group_map(gm) and os.environ.get("CHECK_EXAMPLES_GROUP_MAP_NONEMPTY"):
                problems.append(f"{gm}: maps no group")
    except Exception as exc:  # noqa: BLE001 — reported, never raised
        problems.append(f"{type(exc).__name__}: {exc}")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("atlas_mcp_settings", MCP_SETTINGS)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
    except SystemExit as exc:
        problems.append(f"atlas-mcp's settings refuse this environment: {exc}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"atlas-mcp's settings: {type(exc).__name__}: {exc}")
    print(json.dumps(problems))
    return 0


def _matrices(examples: Path) -> list:
    return sorted((examples / "guardrails").glob("*.y*ml")) + sorted((examples / "guardrails").glob("*.json"))


def check_guardrails(path: Path) -> list:
    _backend()
    import guardrails
    try:
        m = guardrails.load_file(str(path))
        for fmt in guardrails.FORMATS:
            guardrails.export(m, fmt)
    except (guardrails.MatrixError, guardrails.GuardrailsError) as exc:
        return [f"{path.name}: {exc}"]
    return []


def check_cicd(path: Path, examples: Path) -> list:
    _backend()
    import cicd
    import guardrails
    import guide_answers
    try:
        ci = guide_answers.cicd_input(guide_answers.load_answers_file(str(path)))
    except (guide_answers.GuideAnswerError, guardrails.MatrixError) as exc:
        return [f"{path.name}: {exc}"]
    problems, mats = [], _matrices(examples)
    if not mats:
        return [f"{path.name}: no guardrails example to generate a pipeline from"]
    for mp in mats:
        try:
            m = guardrails.load_file(str(mp))
        except guardrails.MatrixError as exc:
            problems.append(f"{path.name} with {mp.name}: the matrix: {exc}")
            continue
        for platform in cicd.PLATFORMS:
            try:
                out = cicd.generate(m, ci, platform)
                if out["pipeline_path"] not in out["files"]:
                    problems.append(f"{path.name} with {mp.name} on {platform}: no pipeline file")
            except (cicd.CicdError, guardrails.MatrixError) as exc:
                problems.append(f"{path.name} with {mp.name} on {platform}: {exc}")
    return problems


def check_crunch(path: Path, examples: Path) -> list:
    _backend()
    import crunch
    import guardrails
    try:
        raw = guardrails.parse_text(path.read_text(encoding="utf-8"), str(path))
    except guardrails.MatrixError as exc:
        return [f"{path.name}: {exc}"]
    problems, mats = [], _matrices(examples)
    if not mats:
        return [f"{path.name}: no guardrails example to check skip_envs against"]
    for mp in mats:
        try:
            crunch.validate_config(raw, matrix=guardrails.load_file(str(mp)))
        except (crunch.CrunchConfigError, guardrails.MatrixError) as exc:
            problems.append(f"{path.name} against {mp.name}: {exc}")
    return problems


def check_group_map(path: Path, examples: Path) -> list:
    """The group map through `identity.groups.load_group_map`, in the same child process as the
    env profiles (the identity package needs the API's environment to import)."""
    env = live(layer(layer(ENV_EXAMPLE.read_text(encoding="utf-8"),
                           (examples / "env" / "minimal.env").read_text(encoding="utf-8"),
                           "minimal.env"),
                     "# the map under test\nATLAS_OIDC_GROUP_MAP_FILE=%s\n"
                     % path.resolve().as_posix(), "group map"))
    env["CHECK_EXAMPLES_GROUP_MAP_NONEMPTY"] = "1"
    return [f"{path.name}: {p}" for p in evaluate(env, examples, path.name)]


KINDS = ("env", "guardrails", "cicd", "crunch", "sso")


def check_all(examples: Path, quiet: bool = False) -> list:
    problems = []
    found = {k: [] for k in KINDS}
    for p in sorted(examples.rglob("*")):
        if not p.is_file() or p.name == "README.md":
            continue
        kind = p.relative_to(examples).parts[0]
        if kind not in found:
            problems.append(f"{p.relative_to(examples).as_posix()}: not in a known example folder "
                            f"({', '.join(KINDS)}); nothing would check it")
            continue
        found[kind].append(p)
    for kind in KINDS:
        if not found[kind]:
            problems.append(f"examples/{kind}/: no example")
    for p in found["env"]:
        got = check_env(p, examples) if p.suffix == ".env" else [f"{p.name}: an env example ends in .env"]
        _report(quiet, "env", p, got)
        problems += got
    for p in found["guardrails"]:
        got = check_guardrails(p)
        _report(quiet, "guardrails.load_file + export", p, got)
        problems += got
    for p in found["cicd"]:
        got = check_cicd(p, examples)
        _report(quiet, "guide_answers + cicd.generate x3", p, got)
        problems += got
    for p in found["crunch"]:
        got = check_crunch(p, examples)
        _report(quiet, "crunch.validate_config", p, got)
        problems += got
    for p in found["sso"]:
        got = check_group_map(p, examples)
        _report(quiet, "identity.groups.load_group_map", p, got)
        problems += got
    return problems


def _report(quiet: bool, how: str, p: Path, got: list) -> None:
    if not quiet:
        print(f"{'FAIL' if got else 'ok  '} {p.parent.name}/{p.name}  ({how})")


# --------------------------------------------------------------------------- #
# Self-test: a broken copy of each kind must fail
# --------------------------------------------------------------------------- #

def _sub(text: str, old: str, new: str) -> str:
    if old not in text:
        raise AssertionError(f"self-test fixture drifted: {old!r} is not in the example")
    return text.replace(old, new, 1)


BREAKS = [
    ("env: a key nothing reads", "env/minimal.env",
     lambda t: t + "# a typo\nATLAS_PUBLIC_HOSTNAME=atlas.example.org\n"),
    ("env: a value the loader refuses", "env/sso-rbac.env",
     lambda t: _sub(t, "ATLAS_RBAC=projects", "ATLAS_RBAC=everyone")),
    ("env: a secret given as a value", "env/retrieval-hosted.env",
     lambda t: t + "# the key itself\nATLAS_EMBED_KEY=abc\n"),
    ("env: the retrieval add-on unavailable", "env/retrieval-local.env",
     lambda t: _sub(t, "ATLAS_EMBED_URL=https://embed.example.org/v1",
                    "ATLAS_EMBED_URL=http://embed.example.org/v1")),
    ("env: an atlas-mcp setting refused", "env/minimal.env",
     lambda t: _sub(t, "ATLAS_MCP_ALLOWED_ORIGINS=https://atlas.example.org",
                    "ATLAS_MCP_ALLOWED_ORIGINS=,")),
    ("env: a profile compose does not define", "env/gateway.env",
     # The live line, not the comment that mentions it.
     lambda t: _sub(t, "\nCOMPOSE_PROFILES=gateway\n", "\nCOMPOSE_PROFILES=gatewy\n")),
    ("env: a line with no comment", "env/backup.env",
     lambda t: t + "\nATLAS_PRODUCT_NAME=Atlas\n"),
    ("env: a backup schedule the loop does not know", "env/backup.env",
     lambda t: _sub(t, "ATLAS_BACKUP_SCHEDULE=daily", "ATLAS_BACKUP_SCHEDULE=monthly")),
    ("env: a step-up max age out of range", "env/sso-rbac.env",
     lambda t: _sub(t, "ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS=300", "ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS=3600")),
    ("env: a secret path the overlay does not mount", "env/sso-rbac.env",
     lambda t: _sub(t, "ATLAS_OIDC_GROUP_MAP_FILE=/run/secrets/oidc_group_map\n",
                    "ATLAS_OIDC_GROUP_MAP_FILE=/run/secrets/oidc_group_map.yml\n")),
    ("env: started without the overlay that mounts its secret", "env/sso-rbac.env",
     lambda t: _sub(t, " -f oidc.yml up", " up")),
    ("env: an overlay that is not shipped", "env/retrieval-entra.env",
     lambda t: _sub(t, "-f retrieval-workload-identity.yml", "-f retrieval-wi.yml")),
    ("guardrails: an agent deploys to production", "guardrails/dev-staging-prod.yaml",
     lambda t: _sub(t, "agent: [read_logs]\n", "agent: [read_logs, deploy]\n")),
    ("cicd: a check that does not exist", "cicd/answers.yaml",
     lambda t: _sub(t, "checks: [build,", "checks: [biuld, build,")),
    ("crunch: skips production", "crunch/crunch-week.yaml",
     lambda t: _sub(t, "skip_envs: [staging]", "skip_envs: [prod]")),
    ("crunch: never ends", "crunch/crunch-week.yaml",
     lambda t: _sub(t, "duration_hours: 168\n", "")),
    ("sso: a group map of the wrong shape", "sso/oidc-group-map.yml",
     lambda t: _sub(t, "groups:\n", "group:\n")),
]


def self_test() -> int:
    rc = 0
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp) / "docs" / "oss" / "examples"
        shutil.copytree(EXAMPLES, clean)
        got = check_all(clean, quiet=True)
        baseline = set(got)
        print(f"self-test {'ok  ' if not got else 'FAIL'} the examples as committed pass")
        for g in got:
            print("    " + g)
        rc |= 1 if got else 0
        for label, rel, brk in BREAKS:
            work = Path(tmp) / "case" / "docs" / "oss" / "examples"
            if work.exists():
                shutil.rmtree(work)
            shutil.copytree(EXAMPLES, work)
            f = work / rel
            f.write_text(brk(f.read_text(encoding="utf-8")), encoding="utf-8")
            got = check_all(work, quiet=True)
            # Caught, and caught naming the broken file (or a profile built on it).
            # A problem the clean copy does not have proves nothing if the clean copy fails too.
            ok = any(Path(rel).name in g for g in got if g not in baseline) and not baseline
            print(f"self-test {'ok  ' if ok else 'FAIL'} caught: {label}"
                  + (f"  -> {got[0][:160]}" if got else "  (passed; it must fail)"))
            rc |= 0 if ok else 1
    print("self-test: " + ("ok" if rc == 0 else "FAILED"))
    return rc


def main(argv: list) -> int:
    if argv[1:2] == ["--_eval"]:
        return _eval_child()
    if argv[1:2] == ["apply"]:
        return cmd_apply(argv[2:])
    if argv[1:2] == ["compose-files"]:
        return cmd_compose_files(argv[2:])
    if argv[1:] == ["--self-test"]:
        return self_test()
    if argv[1:]:
        print(__doc__.split("Usage:")[1].split("`apply`")[0], file=sys.stderr)
        return 2
    problems = check_all(EXAMPLES)
    for p in problems:
        print("FAIL " + p)
    if problems:
        print(f"check_examples: FAILED ({len(problems)})")
        return 1
    print("check_examples: every example passes the code that reads it in production")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
