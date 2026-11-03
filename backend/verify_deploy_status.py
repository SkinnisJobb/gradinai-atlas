"""
Verification for the stale-deploy signal (TASK-1154): `deploy_status.py` and `tools/deploy-watch.sh`.

Two incidents shaped it. On 2026-08-28 atlas-mcp had served an image twelve days older than the
fix already pulled onto the server (A: pulled, not rebuilt). On 2026-09-14 a merged fix sat on
GitHub for four days because the server was never pulled (B: merged, not pulled). Both read as a
broken tool. This script proves each half says the right thing in every state, and says NOTHING
where it cannot see -- a check that is red wherever it is blind is one people learn to ignore.

NO DATABASE. The route-level check (that /api/health carries `deploy`) is in
verify_object_graph.py, which already has an authenticated client.

  A. The two copies of deploy_status.py (backend/, mcp/app/) are byte-identical.
  B. The specs match reality: every spec entry is a COPY line in that image's Dockerfile and every
     COPY line is a spec entry; the checkout paths equal tools/deploy-targets' pathspecs.
  C. Image vs checkout fingerprint: current, stale (naming the file), __pycache__ ignored, unknown
     with no checkout and with an empty one -- never stale.
  D. The deploy-watch status file: unknown, current, recent (inside the 24h grace, no warning),
     pending (warning naming the deploy command), and watch_broken for a stale, errored or
     unreadable file.
  E. check() never raises into the route or tool it rides on.
  F. tools/deploy-watch.sh against real throwaway git repos: a merge touching mcp/app counts for
     atlas-mcp and not atlas-api, a docs-only merge counts for neither, and a failed fetch is
     WRITTEN as an error rather than leaving an old file looking current.
  G. The atlas-mcp rebuild hints carry the TLS overlay (TASK-1351). Since OSS-24 atlas-mcp is
     deployed as `-f docker-compose.yml -f docker-compose.tls.yml`; a hint without the overlay
     recreates atlas-mcp alone and drops the :8446 listener. Both hints name it, and both run the
     same compose command that tools/check-deployed.sh and agent/procedures/release.md print.
  H. The hint commands are configurable (TASK-1356): unset, every command and sentence is
     byte-identical to the literal homelab strings; set, deploy_status (read at import, in a child
     process) names the configured commands and host and never the default host; host-only moves the
     default commands with it; set-but-blank is an error naming the variable, and routers/health.py
     reads them at import; mcp/app/settings.py carries the same names, defaults and answers.

Run:
    python verify_deploy_status.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

# The hint commands at their defaults (TASK-1356): deploy_status reads ATLAS_DEPLOY_HINT_* at
# import, and every section but H proves the homelab strings. H's children set their own.
HINT_VARS = ("ATLAS_DEPLOY_HINT_HOST", "ATLAS_DEPLOY_HINT_REBUILD_API", "ATLAS_DEPLOY_HINT_REBUILD_MCP",
             "ATLAS_DEPLOY_HINT_PULL_REBUILD_API", "ATLAS_DEPLOY_HINT_PULL_REBUILD_MCP")
for _name in HINT_VARS:
    os.environ.pop(_name, None)

import deploy_status as ds  # noqa: E402
import settings  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("[PASS] " if ok else "[FAIL] ") + label + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def absent(what: str, *rels: str) -> bool:
    """True, with a printed SKIP, when a homelab-only file `what` reads is not in this tree.
    The deploy tooling (tools/deploy*, check-deployed.sh, agent/) is not in every checkout."""
    missing = [rel for rel in rels if not (ROOT / rel).exists()]
    for rel in missing:
        print(f"SKIP {what}: {rel} is not in this tree")
    return bool(missing)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- A
def section_a() -> None:
    print("\n-- A. one module, two copies --")
    a = (HERE / "deploy_status.py").read_bytes()
    b = (ROOT / "mcp" / "app" / "deploy_status.py").read_bytes()
    check("backend/deploy_status.py and mcp/app/deploy_status.py are byte-identical", a == b,
          "copy the one you edited over the other")


# --------------------------------------------------------------------------- B
def copy_lines(dockerfile: Path) -> set[tuple[str, str]]:
    """(source, dest) for every COPY, dest normalised: '.'/'./' -> '' and a file copied into ''
    takes its basename, so it compares against a spec's image path."""
    out = set()
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*COPY\s+(\S+)\s+(\S+)\s*$", line)
        if not m:
            continue
        src, dest = m.group(1).rstrip("/"), m.group(2)
        dest = dest[2:] if dest.startswith("./") else dest
        dest = "" if dest in (".", "") else dest.rstrip("/")
        if dest == "" and "*" not in src and "." in os.path.basename(src):
            dest = os.path.basename(src)
        out.add((src, dest))
    return out


def spec_copies(spec, context_prefix: str) -> set[tuple[str, str]]:
    out = set()
    for entry in spec:
        _logical, image_rel, checkout_rel, kind = entry[:4]
        src = checkout_rel[len(context_prefix):] if checkout_rel.startswith(context_prefix) else checkout_rel
        if kind == "glob":
            src = src + "/" + entry[4]
        if src.endswith("Dockerfile") and image_rel == "Dockerfile.build":
            pass
        out.add((src, image_rel))
    return out


def section_b() -> None:
    print("\n-- B. the specs match the Dockerfiles and tools/deploy-targets --")
    api_docker = copy_lines(ROOT / "backend" / "Dockerfile")
    mcp_docker = copy_lines(ROOT / "mcp" / "Dockerfile")
    api_spec = spec_copies(ds.API_SPEC, "")
    mcp_spec = spec_copies(ds.MCP_SPEC, "mcp/")
    check("every API_SPEC entry is a COPY in backend/Dockerfile, and every COPY is in API_SPEC",
          api_spec == api_docker, f"only in spec {sorted(api_spec - api_docker)}; "
                                  f"only in Dockerfile {sorted(api_docker - api_spec)}")
    check("every MCP_SPEC entry is a COPY in mcp/Dockerfile, and every COPY is in MCP_SPEC",
          mcp_spec == mcp_docker, f"only in spec {sorted(mcp_spec - mcp_docker)}; "
                                  f"only in Dockerfile {sorted(mcp_docker - mcp_spec)}")

    if absent("the specs match tools/deploy-targets", "tools/deploy-targets"):
        return
    targets = {}
    for line in (ROOT / "tools" / "deploy-targets").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            name, _project, paths = line.split("|")
            targets[name] = set(paths.split())

    def pathspecs(spec):
        out = set()
        for entry in spec:
            _l, _i, checkout_rel, kind = entry[:4]
            out.add(":(glob)%s/%s" % (checkout_rel, entry[4]) if kind == "glob" else checkout_rel)
        return out

    for name, spec in (("atlas-api", ds.API_SPEC), ("atlas-mcp", ds.MCP_SPEC)):
        check(f"tools/deploy-targets' pathspecs for {name} equal its spec's checkout paths",
              targets.get(name) == pathspecs(spec),
              f"targets {sorted(targets.get(name) or [])} vs spec {sorted(pathspecs(spec))}")


# --------------------------------------------------------------------------- C
def section_c() -> None:
    print("\n-- C. image vs checkout fingerprint --")
    with tempfile.TemporaryDirectory() as d:
        image, checkout = Path(d) / "image", Path(d) / "checkout"
        for base, prefix in ((image, ""), (checkout, "backend/")):
            write(base / (prefix + "app.py"), "print('v1')\n")
            write(base / (prefix + "auth.py"), "x = 1\n")
        for base in (image, checkout):
            write(base / "frontend" / "board.html", "<html>v1</html>")
        spec = (("backend", "", "backend", "glob", "*.py"), ("frontend", "frontend", "frontend", "tree"))

        def state(component):
            ds._image_cache.pop(component, None)
            return ds.image_state(component, spec, str(image), str(checkout))

        s = state("t1")
        check("identical image and checkout -> current", s["state"] == "current", str(s))

        write(image / "__pycache__" / "app.cpython-312.pyc", "bytecode")
        write(image / "frontend" / "__pycache__" / "x.pyc", "bytecode")
        s = state("t2")
        check("__pycache__ written inside the image at runtime is not a difference", s["state"] == "current", str(s))

        write(checkout / "backend" / "auth.py", "x = 2\n")
        s = state("t3")
        check("a changed source file in the checkout -> stale, naming the file",
              s["state"] == "stale" and s["differs"] == ["backend/auth.py"], str(s))
        warn = ds.warnings_for("atlas-api", s, {"state": "unknown"})
        check("the stale warning names the rebuild command", len(warn) == 1 and ds.REBUILD["atlas-api"] in warn[0],
              warn[0] if warn else "no warning")

        write(checkout / "backend" / "auth.py", "x = 1\n")
        write(checkout / "backend" / "TESTING.md", "docs only")
        s = state("t4")
        check("a docs-only file the Dockerfile never copies is NOT a difference", s["state"] == "current", str(s))

        write(checkout / "frontend" / "new.js", "var x;")
        s = state("t5")
        check("a new file in a copied tree -> stale", s["state"] == "stale" and "frontend/new.js" in s["differs"], str(s))

        s = ds.image_state("t6", spec, str(image), str(Path(d) / "absent"))
        check("no checkout mounted -> unknown, never stale", s["state"] == "unknown", str(s))
        empty = Path(d) / "empty"
        empty.mkdir()
        ds._image_cache.pop("t7", None)
        s = ds.image_state("t7", spec, str(image), str(empty))
        check("a mount holding none of the image's files -> unknown, never stale", s["state"] == "unknown", str(s))
        check("unknown raises no warning", ds.warnings_for("atlas-api", s, {"state": "unknown"}) == [])


# --------------------------------------------------------------------------- D
def section_d() -> None:
    print("\n-- D. the deploy-watch status file --")
    now = time.time()
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "status.json"

        def pend(body, component="atlas-mcp"):
            if body is None:
                f.unlink(missing_ok=True)
            else:
                f.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
            return ds.pending_state(component, str(f), now=now)

        p = pend(None)
        check("no status file (staging, fork, a laptop) -> unknown, no warning",
              p["state"] == "unknown" and ds.warnings_for("atlas-mcp", {}, p) == [], str(p))
        p = pend({"checked_at": now - 60, "pending": {"atlas-mcp": {"count": 0}}})
        check("fresh file, nothing waiting -> current", p["state"] == "current", str(p))
        p = pend({"checked_at": now - 60, "pending": {"atlas-mcp": {
            "count": 2, "oldest_merged_at": now - 3 * 3600, "oldest_subject": "Merge pull request #80"}}})
        check("merges waiting 3h -> recent, and NO warning (a same-day deploy is not a fault)",
              p["state"] == "recent" and ds.warnings_for("atlas-mcp", {}, p) == [], str(p))
        p = pend({"checked_at": now - 60, "pending": {"atlas-mcp": {
            "count": 2, "oldest_merged_at": now - 30 * 3600, "oldest_subject": "Merge pull request #80"}}})
        warn = ds.warnings_for("atlas-mcp", {}, p)
        check("merges waiting 30h -> pending", p["state"] == "pending" and p["waited_h"] == 30, str(p))
        check("the pending warning names the count, the oldest merge and the pull-and-rebuild command",
              len(warn) == 1 and "2 merged change(s)" in warn[0] and "#80" in warn[0]
              and ds.PULL_AND_REBUILD["atlas-mcp"] in warn[0], warn[0] if warn else "no warning")
        p = pend({"checked_at": now - 60, "pending": {"atlas-mcp": {"count": 1, "oldest_merged_at": now - 99 * 3600}}},
                 component="atlas-api")
        check("another component's pending merges do not count against this one", p["state"] == "current", str(p))
        p = pend({"checked_at": now - 5 * 3600, "pending": {}})
        check("a status file not refreshed for 5h -> watch_broken, with a warning",
              p["state"] == "watch_broken" and ds.warnings_for("atlas-mcp", {}, p), str(p))
        p = pend({"checked_at": now - 60, "error": "git fetch failed: Permission denied (publickey)"})
        check("an error written by the watcher -> watch_broken, carrying the error",
              p["state"] == "watch_broken" and "publickey" in p["reason"], str(p))
        p = pend("{not json")
        check("an unreadable status file -> watch_broken, not a crash", p["state"] == "watch_broken", str(p))


# --------------------------------------------------------------------------- E
def section_e() -> None:
    print("\n-- E. check() never raises --")
    real = ds.image_state
    ds.image_state = lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied"))
    try:
        ds._result_cache.clear()
        r = ds.check("atlas-api", ds.API_SPEC, status_file="/definitely/absent.json", max_age_s=0)
        check("a fingerprint that raises yields `unknown` and no warning, not an exception",
              r["image"]["state"] == "unknown" and r["warnings"] == [], str(r))
    except Exception as exc:  # noqa: BLE001
        check("a fingerprint that raises yields `unknown` and no warning, not an exception", False, repr(exc))
    finally:
        ds.image_state = real
        ds._result_cache.clear()


# --------------------------------------------------------------------------- F
def git(cwd, *args, check_rc=True):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if check_rc and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout.strip()


def section_f() -> None:
    print("\n-- F. tools/deploy-watch.sh against real git repos --")
    if absent("tools/deploy-watch.sh against real git repos", "tools/deploy-watch.sh", "tools/deploy-targets"):
        return
    bash = shutil.which("bash")
    if not bash or not shutil.which("git"):
        check("bash and git are available to run tools/deploy-watch.sh", False,
              "this section needs both; it is not skipped silently")
        return
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        env = dict(os.environ)
        if os.name == "nt" or not shutil.which("python3"):
            # The server has python3. On Windows `python3` is often the Microsoft Store stub, so
            # give the script the name it calls, pointing at the interpreter running this check.
            shim = d / "bin"
            shim.mkdir()
            exe = sys.executable.replace("\\", "/")
            (shim / "python3").write_text('#!/bin/sh\nexec "%s" "$@"\n' % exe, encoding="utf-8")
            os.chmod(shim / "python3", 0o755)
            env["PATH"] = str(shim) + os.pathsep + env["PATH"]
        origin, dev, server = d / "origin.git", d / "dev", d / "server"
        git(d, "init", "-q", "--bare", "-b", "master", str(origin))
        git(d, "clone", "-q", str(origin), str(dev))
        git(dev, "checkout", "-q", "-b", "master")
        write(dev / "tools" / "deploy-targets", (ROOT / "tools" / "deploy-targets").read_text(encoding="utf-8"))
        write(dev / "backend" / "app.py", "v1\n")
        write(dev / "mcp" / "app" / "tools.py", "v1\n")
        write(dev / "docs" / "notes.md", "v1\n")
        git(dev, "add", "-A")
        git(dev, "commit", "-q", "-m", "seed")
        git(dev, "push", "-q", "origin", "master")
        git(d, "clone", "-q", "-b", "master", str(origin), str(server))

        def merge(branch, path, subject):
            git(dev, "checkout", "-q", "-b", branch)
            write(dev / path, branch + "\n")
            git(dev, "commit", "-qam", "change " + path)
            git(dev, "checkout", "-q", "master")
            git(dev, "merge", "-q", "--no-ff", branch, "-m", subject)

        merge("fix-mcp", "mcp/app/tools.py", "Merge pull request #1 fix-mcp")
        merge("docs", "docs/notes.md", "Merge pull request #2 docs only")
        git(dev, "push", "-q", "origin", "master")

        script = ROOT / "tools" / "deploy-watch.sh"
        run_env = dict(env, ATLAS_REPO_DIR=str(server), GIT_CONFIG_GLOBAL=os.devnull)
        r = subprocess.run([bash, str(script)], env=run_env, capture_output=True, text=True)
        status_path = server / ".deploy-watch" / "status.json"
        check("deploy-watch.sh exits 0 and writes status.json", r.returncode == 0 and status_path.is_file(),
              (r.stderr or r.stdout)[-300:])
        if not status_path.is_file():
            return
        st = json.loads(status_path.read_text(encoding="utf-8"))
        mcp = (st.get("pending") or {}).get("atlas-mcp") or {}
        api = (st.get("pending") or {}).get("atlas-api") or {}
        check("a merge touching mcp/app counts for atlas-mcp, with its subject",
              mcp.get("count") == 1 and "fix-mcp" in (mcp.get("oldest_subject") or ""), str(mcp))
        check("...and not for atlas-api, and the docs-only merge counts for neither", api.get("count") == 0, str(api))
        check("the checkout was NOT pulled: HEAD still differs from origin/master, working tree untouched",
              st.get("checkout_head") != st.get("origin_master")
              and (server / "mcp" / "app" / "tools.py").read_text(encoding="utf-8") == "v1\n", str(st)[:200])
        check("status.json carries checked_at, so a dead watcher is detectable", abs(st.get("checked_at", 0) - time.time()) < 120)
        p = ds.pending_state("atlas-mcp", str(status_path))
        check("deploy_status reads the real file: one merge, inside the grace period -> recent", p["state"] == "recent", str(p))

        git(server, "remote", "set-url", "origin", str(d / "no-such-repo.git"))
        r = subprocess.run([bash, str(script)], env=run_env, capture_output=True, text=True)
        st = json.loads(status_path.read_text(encoding="utf-8"))
        check("a failed fetch exits non-zero AND overwrites the file with the error, not an old all-clear",
              r.returncode != 0 and "git fetch failed" in (st.get("error") or ""), str(st)[:200])


# --------------------------------------------------------------------------- G
MCP_COMPOSE = re.compile(r"docker compose [^'\"]*?up -d --build")


def section_g() -> None:
    print("\n-- G. the atlas-mcp rebuild hints carry the TLS overlay --")
    overlay = "-f docker-compose.yml -f docker-compose.tls.yml"
    check("mcp/docker-compose.tls.yml exists (the overlay the hints name)",
          (ROOT / "mcp" / "docker-compose.tls.yml").is_file())
    if distribution.NAME != "homelab":
        # The neutral copy ships no default command (each unset hint answers "no command
        # configured"); section H proves that answer. The TLS-overlay pins are the homelab's.
        print(f"[SKIP] distribution.NAME is {distribution.NAME!r}, not 'homelab': it carries no default "
              "atlas-mcp command, so there is no overlay or env file to pin (section H covers its answer)")
        return
    commands = {}
    for name, table in (("REBUILD", ds.REBUILD), ("PULL_AND_REBUILD", ds.PULL_AND_REBUILD)):
        hint = table["atlas-mcp"]
        check(f"{name}['atlas-mcp'] names the TLS overlay ({overlay})", overlay in hint, hint)
        check(f"{name}['atlas-mcp'] keeps the homelab env file",
              "--env-file ../deploy/homelab/atlas-mcp.env" in hint, hint)
        check(f"{name}['atlas-mcp'] runs in mcp/", "cd ~/project-atlas/mcp &&" in hint or "&& cd mcp &&" in hint, hint)
        m = MCP_COMPOSE.search(hint)
        commands[name] = m.group(0) if m else None
    check("REBUILD and PULL_AND_REBUILD run the same atlas-mcp compose command",
          commands["REBUILD"] is not None and commands["REBUILD"] == commands["PULL_AND_REBUILD"], str(commands))
    cmd = commands["REBUILD"] or "<none>"
    for rel in ("tools/check-deployed.sh", "agent/procedures/release.md", "tools/deploy.sh"):
        if absent(f"{rel} prints the same atlas-mcp compose command as the hint", rel):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8")
        check(f"{rel} prints the same atlas-mcp compose command as the hint", cmd in text, cmd)
    # tools/deploy.sh RUNS the default pull-and-rebuild commands (ADR 0046), so it must carry them
    # whole, quoted exactly as the hint quotes them after `ssh <host>`.
    if absent("tools/deploy.sh carries the pull-and-rebuild commands", "tools/deploy.sh"):
        return
    deploy_sh = (ROOT / "tools" / "deploy.sh").read_text(encoding="utf-8")
    for comp, var in (("atlas-api", "API_CMD"), ("atlas-mcp", "MCP_CMD")):
        inner = TODAY_PULL_AND_REBUILD[comp].split(" ", 2)[2]   # drop `ssh <host>`
        check(f"tools/deploy.sh {var} is the {comp} pull-and-rebuild command, byte for byte",
              f"{var}={inner}" in deploy_sh, inner)


# --------------------------------------------------------------------------- H
# The defaults come from backend/distribution.py (STORY-oss-23 A1); verify_settings.py pins the
# homelab's values there byte for byte. Here they are built from distribution.py's templates
# directly, not through settings.load_deploy_hints, so deploy_status's answer is checked against
# an independent computation (the same shape as mcp/verify_deploy_warning.py).
import distribution  # noqa: E402

HOST = distribution.DEFAULT_DEPLOY_HINT_HOST


def expected_hint(name: str, host: str = HOST) -> str:
    template = distribution.DEFAULT_DEPLOY_HINTS.get(name)
    return settings.NO_DEPLOY_HINT.format(name=name) if template is None else template.format(host=host)


def expected_hints(host: str = HOST) -> tuple[dict, dict]:
    return ({"atlas-api": expected_hint("ATLAS_DEPLOY_HINT_REBUILD_API", host),
             "atlas-mcp": expected_hint("ATLAS_DEPLOY_HINT_REBUILD_MCP", host)},
            {"atlas-api": expected_hint("ATLAS_DEPLOY_HINT_PULL_REBUILD_API", host),
             "atlas-mcp": expected_hint("ATLAS_DEPLOY_HINT_PULL_REBUILD_MCP", host)})


TODAY_REBUILD, TODAY_PULL_AND_REBUILD = expected_hints()
STALE = {"state": "stale", "differs_count": 1, "differs": ["backend/auth.py"]}
PENDING = {"state": "pending", "count": 2, "waited_h": 30, "oldest_subject": "Merge pull request #80"}
BROKEN = {"state": "watch_broken", "reason": "stale"}
CONFIGURED = {
    "ATLAS_DEPLOY_HINT_HOST": "deploy-box",
    "ATLAS_DEPLOY_HINT_REBUILD_API": "kubectl rollout restart deploy/atlas-api",
    "ATLAS_DEPLOY_HINT_REBUILD_MCP": "kubectl rollout restart deploy/atlas-mcp",
    "ATLAS_DEPLOY_HINT_PULL_REBUILD_API": "make deploy-api",
    "ATLAS_DEPLOY_HINT_PULL_REBUILD_MCP": "make deploy-mcp",
}
HINT_CHILD = r'''
import json, sys
sys.path.insert(0, ".")
import deploy_status as ds
stale = {"state": "stale", "differs_count": 1, "differs": ["backend/auth.py"]}
pending = {"state": "pending", "count": 2, "waited_h": 30, "oldest_subject": "Merge pull request #80"}
w = []
for c in ("atlas-api", "atlas-mcp"):
    w += ds.warnings_for(c, stale, pending) + ds.warnings_for(c, {}, {"state": "watch_broken", "reason": "x"})
print("HINTS " + json.dumps({"rebuild": ds.REBUILD, "pull": ds.PULL_AND_REBUILD, "warnings": w}))
'''


def hint_child(extra):
    env = {k: v for k, v in os.environ.items() if k not in HINT_VARS}
    env.update(extra)
    proc = subprocess.run([sys.executable, "-c", HINT_CHILD], cwd=HERE, env=env,
                          capture_output=True, text=True, timeout=60)
    for line in proc.stdout.splitlines():
        if line.startswith("HINTS "):
            return json.loads(line[len("HINTS "):]), proc
    return None, proc


def section_h() -> None:
    print("\n-- H. the hint commands are configurable (TASK-1356) --")
    check("default: REBUILD is byte-identical to today's", ds.REBUILD == TODAY_REBUILD, str(ds.REBUILD))
    check("default: PULL_AND_REBUILD is byte-identical to today's",
          ds.PULL_AND_REBUILD == TODAY_PULL_AND_REBUILD, str(ds.PULL_AND_REBUILD))
    want = [
        "atlas-api is running an image older than the source on %s (1 file(s) differ, "
        "e.g. backend/auth.py). Rebuild: %s" % (HOST, TODAY_REBUILD["atlas-api"]),
        "2 merged change(s) touching atlas-api have been on GitHub master for 30h without being "
        "pulled onto %s (oldest: 'Merge pull request #80'). Deploy, with the owner's OK: %s"
        % (HOST, TODAY_PULL_AND_REBUILD["atlas-api"]),
        "The deploy watch on %s is not reporting (stale), so merges waiting on GitHub "
        "would go unnoticed. Check the cron entry for tools/deploy-watch.sh." % HOST,
    ]
    got = ds.warnings_for("atlas-api", STALE, PENDING) + ds.warnings_for("atlas-api", {}, BROKEN)
    check("default: the warning sentences are byte-identical to today's", got == want, json.dumps(got))

    d, p = hint_child({})
    check("default, read at import in a fresh process: today's commands",
          d is not None and d["rebuild"] == TODAY_REBUILD and d["pull"] == TODAY_PULL_AND_REBUILD,
          (json.dumps(d) if d else p.stderr[-800:]))

    c, p = hint_child(CONFIGURED)
    check("configured, read at import: every command is the configured one",
          c is not None and c["rebuild"] == {"atlas-api": CONFIGURED["ATLAS_DEPLOY_HINT_REBUILD_API"],
                                             "atlas-mcp": CONFIGURED["ATLAS_DEPLOY_HINT_REBUILD_MCP"]}
          and c["pull"] == {"atlas-api": CONFIGURED["ATLAS_DEPLOY_HINT_PULL_REBUILD_API"],
                            "atlas-mcp": CONFIGURED["ATLAS_DEPLOY_HINT_PULL_REBUILD_MCP"]},
          (json.dumps(c) if c else p.stderr[-800:]))
    if c:
        w = c["warnings"]
        check("configured: the warnings name the configured commands and host",
              len(w) == 6 and w[0].endswith("Rebuild: kubectl rollout restart deploy/atlas-api")
              and w[1].endswith("Deploy, with the owner's OK: make deploy-api")
              and w[3].endswith("Rebuild: kubectl rollout restart deploy/atlas-mcp")
              and w[4].endswith("Deploy, with the owner's OK: make deploy-mcp")
              and all("deploy-box" in s for s in w), json.dumps(w))
        check("configured: the default host appears nowhere in the output", HOST not in json.dumps(c),
              json.dumps(c))

    h, p = hint_child({"ATLAS_DEPLOY_HINT_HOST": "deploy-box"})
    check("host only: the default commands follow the host, and the default host is gone",
          h is not None
          and (h["rebuild"], h["pull"]) == expected_hints("deploy-box")
          and HOST not in json.dumps(h), (json.dumps(h) if h else p.stderr[-800:]))

    for name in HINT_VARS:
        try:
            settings.load_deploy_hints({name: " "})
            check(f"set-but-blank {name} is an error", False, "nothing raised")
        except settings.SettingsError as exc:
            check(f"set-but-blank {name} is a SettingsError naming it", name in str(exc), str(exc))
    b, p = hint_child({"ATLAS_DEPLOY_HINT_REBUILD_API": ""})
    check("set-but-blank stops the import of deploy_status, naming the variable",
          b is None and p.returncode != 0 and "ATLAS_DEPLOY_HINT_REBUILD_API" in p.stderr,
          f"rc={p.returncode} {(p.stderr.strip().splitlines() or [''])[-1]}")
    health = (HERE / "routers" / "health.py").read_text(encoding="utf-8")
    check("routers/health.py reads the hints at import (a blank value stops the boot, not the route)",
          re.search(r"^settings\.load_deploy_hints\(\)", health, re.M) is not None)

    # The atlas-mcp copy, loaded from its file (standard library only), must not drift.
    import importlib.util
    spec = importlib.util.spec_from_file_location("_mcp_settings", ROOT / "mcp" / "app" / "settings.py")
    mcp_settings = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcp_settings)
    check("mcp/app/settings.py: the same variables, in the same order",
          mcp_settings.DEPLOY_HINT_ENVS == settings.DEPLOY_HINT_ENVS
          and mcp_settings.DEPLOY_HINT_HOST_ENV == settings.DEPLOY_HINT_HOST_ENV)
    check("mcp/app/settings.py: the same defaults",
          mcp_settings.DEFAULT_DEPLOY_HINTS == settings.DEFAULT_DEPLOY_HINTS
          and mcp_settings.DEFAULT_DEPLOY_HINT_HOST == settings.DEFAULT_DEPLOY_HINT_HOST)
    for label, env in (("unset", {}), ("configured", CONFIGURED), ("host only", {"ATLAS_DEPLOY_HINT_HOST": "h"}),
                       ("one command", {"ATLAS_DEPLOY_HINT_PULL_REBUILD_MCP": " make it "})):
        a, m = settings.load_deploy_hints(env), mcp_settings.load_deploy_hints(env)
        check(f"mcp/app/settings.py: the same answer ({label})",
              (a.host, dict(a.rebuild), dict(a.pull_and_rebuild)) == (m.host, m.rebuild, m.pull_and_rebuild),
              f"{a} vs {vars(m)}")
    try:
        mcp_settings.load_deploy_hints({"ATLAS_DEPLOY_HINT_HOST": ""})
        check("mcp/app/settings.py: set-but-blank is an error too", False, "nothing raised")
    except mcp_settings.DeployHintError as exc:
        check("mcp/app/settings.py: set-but-blank is an error too, naming it", "ATLAS_DEPLOY_HINT_HOST" in str(exc))


if __name__ == "__main__":
    print("verify_deploy_status -- the stale-deploy signal (TASK-1154)")
    print("=" * 70)
    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    section_f()
    section_g()
    section_h()
    print()
    if failures:
        print(f"FAILED -- {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")
