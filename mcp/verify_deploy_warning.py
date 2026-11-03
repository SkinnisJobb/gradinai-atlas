"""Prove every atlas-mcp tool response carries the stale-deploy warning, and only while it is true.

The states of the check itself are proven in backend/verify_deploy_status.py (the module is shared,
byte-identical). This proves the WIRING: `envelope()` adds `deploy_warning` from both sources,
adds nothing when both are current, and a broken check never breaks a tool call.

    python mcp/verify_deploy_warning.py      # needs only httpx installed; no network

Same convention as the other mcp/verify_*.py: one [PASS]/[FAIL] line per claim, exit 1 on failure.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("ATLAS_BASE_URL", "https://atlas.invalid")
# This script proves a feature that ATLAS_DISABLED_FEATURES can switch off, so it pins the
# default (every feature on); verify_feature_flags.py proves the switched-off side.
os.environ.pop("ATLAS_DISABLED_FEATURES", None)
for _name in [n for n in os.environ if n.startswith("ATLAS_DEPLOY_HINT_")]:
    os.environ.pop(_name)            # the hint commands at their defaults (TASK-1356; children set their own)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import client as client_mod, deploy_status, deploy_warning  # noqa: E402
from app.envelope import AtlasError, envelope  # noqa: E402

failures: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def set_state(own_warnings, api_health):
    """own: what atlas-mcp's own check returns; api_health: what GET /api/health returns (or an
    exception to raise)."""
    deploy_status.check = lambda *a, **k: {"warnings": list(own_warnings)}

    def fake_get(path, **kwargs):
        assert path == "/api/health", path
        if isinstance(api_health, Exception):
            raise api_health
        return api_health

    client_mod.client.get = fake_get
    deploy_warning._api["at"] = 0.0   # expire the cache


def ok_tool():
    return {"answer": 42}


def failing_tool():
    raise AtlasError("not_found", "no such node")


set_state([], {"deploy": {"warnings": []}})
out = envelope(ok_tool)()
check("both images current -> the response is unchanged, no deploy_warning key",
      out == {"ok": True, "result": {"answer": 42}}, str(out))

set_state(["atlas-mcp is running an image older than the source ... Rebuild: X"], {"deploy": {"warnings": []}})
out = envelope(ok_tool)()
check("atlas-mcp's own image stale -> deploy_warning carries it, result untouched",
      out.get("result") == {"answer": 42} and len(out.get("deploy_warning", [])) == 1
      and "atlas-mcp" in out["deploy_warning"][0], str(out))

set_state([], {"deploy": {"warnings": ["2 merged change(s) touching atlas-api ... Deploy: Y"]}})
out = envelope(ok_tool)()
check("atlas-api stale (read from /api/health) -> deploy_warning carries it",
      out.get("deploy_warning") == ["2 merged change(s) touching atlas-api ... Deploy: Y"], str(out))

out = envelope(failing_tool)()
check("the warning rides on a FAILED tool call too -- a missing tool is exactly when it matters",
      out.get("ok") is False and out.get("deploy_warning"), str(out))

calls = {"n": 0}
real_get = client_mod.client.get


def counting_get(path, **kwargs):
    calls["n"] += 1
    return real_get(path, **kwargs)


client_mod.client.get = counting_get
for _ in range(5):
    envelope(ok_tool)()
check("the atlas-api half is cached: five tool calls cost no extra /api/health request",
      calls["n"] == 0, f"{calls['n']} extra request(s)")

set_state([], RuntimeError("atlas-api unreachable"))
out = envelope(ok_tool)()
check("an unreachable /api/health yields no warning and does not break the tool",
      out == {"ok": True, "result": {"answer": 42}}, str(out))

deploy_status.check = lambda *a, **k: (_ for _ in ()).throw(OSError("mount vanished"))
deploy_warning._api["at"] = 0.0
client_mod.client.get = lambda path, **k: {"deploy": {"warnings": []}}
out = envelope(ok_tool)()
check("a check that raises yields no warning and does not break the tool",
      out == {"ok": True, "result": {"answer": 42}}, str(out))

# --------------------------------------------------------------------------- #
# The hint commands (TASK-1356): ATLAS_DEPLOY_HINT_*, read once at import, so one child
# process per configuration, each driving the real envelope over the real warnings_for.
# --------------------------------------------------------------------------- #
import json  # noqa: E402
import subprocess  # noqa: E402

HINT_VARS = ("ATLAS_DEPLOY_HINT_HOST", "ATLAS_DEPLOY_HINT_REBUILD_API", "ATLAS_DEPLOY_HINT_REBUILD_MCP",
             "ATLAS_DEPLOY_HINT_PULL_REBUILD_API", "ATLAS_DEPLOY_HINT_PULL_REBUILD_MCP")

# The defaults come from mcp/app/distribution.py (STORY-oss-23 A2); mcp/verify_distribution.py
# pins the homelab's values there byte for byte. Here they are built from distribution.py's
# templates directly, not through settings.load_deploy_hints, so the child's answer is checked
# against an independent computation.
from app import distribution, settings  # noqa: E402

HOST = distribution.DEFAULT_DEPLOY_HINT_HOST


def expected_hint(name, host=HOST):
    template = distribution.DEFAULT_DEPLOY_HINTS.get(name)
    return settings.NO_DEPLOY_HINT.format(name=name) if template is None else template.format(host=host)


TODAY = {
    "rebuild": expected_hint("ATLAS_DEPLOY_HINT_REBUILD_MCP"),
    "pull": expected_hint("ATLAS_DEPLOY_HINT_PULL_REBUILD_MCP"),
}
TODAY_SENTENCES = [
    "atlas-mcp is running an image older than the source on %s (1 file(s) differ, "
    "e.g. mcp/app/x.py). Rebuild: %s" % (HOST, TODAY["rebuild"]),
    "2 merged change(s) touching atlas-mcp have been on GitHub master for 30h without being "
    "pulled onto %s (oldest: 'Merge pull request #80'). Deploy, with the owner's OK: %s" % (HOST, TODAY["pull"]),
    "The deploy watch on %s is not reporting (stale), so merges waiting on GitHub "
    "would go unnoticed. Check the cron entry for tools/deploy-watch.sh." % HOST,
]

HINT_CHILD = r'''
import json, os, sys
sys.path.insert(0, os.getcwd())
from app import client as client_mod, deploy_status, deploy_warning
from app.envelope import envelope
stale = {"state": "stale", "differs_count": 1, "differs": ["mcp/app/x.py"]}
pending = {"state": "pending", "count": 2, "waited_h": 30, "oldest_subject": "Merge pull request #80"}
broken = {"state": "watch_broken", "reason": "stale"}
own = (deploy_status.warnings_for("atlas-mcp", stale, pending)
       + deploy_status.warnings_for("atlas-mcp", {}, broken))
deploy_status.check = lambda *a, **k: {"warnings": own}
client_mod.client.get = lambda path, **k: {"deploy": {"warnings": []}}
deploy_warning._api["at"] = 0.0
out = envelope(lambda: {"answer": 42})()
print("HINTS " + json.dumps({"rebuild": deploy_status.REBUILD, "pull": deploy_status.PULL_AND_REBUILD,
                             "warning": out.get("deploy_warning")}))
'''


def hint_child(extra):
    env = {k: v for k, v in os.environ.items() if k not in HINT_VARS and k != "ATLAS_DISABLED_FEATURES"}
    env["ATLAS_BASE_URL"] = "https://atlas.invalid"
    env.update(extra)
    proc = subprocess.run([sys.executable, "-c", HINT_CHILD], cwd=Path(__file__).resolve().parent, env=env,
                          capture_output=True, text=True, timeout=120)
    for line in proc.stdout.splitlines():
        if line.startswith("HINTS "):
            return json.loads(line[len("HINTS "):]), proc
    return None, proc


print("\n-- the hint commands (TASK-1356) --")
d, p = hint_child({})
check("default: the child ran", d is not None, p.stderr[-1500:])
if d:
    check("default: REBUILD['atlas-mcp'] is the distribution's default, byte for byte", d["rebuild"]["atlas-mcp"] == TODAY["rebuild"],
          d["rebuild"]["atlas-mcp"])
    check("default: PULL_AND_REBUILD['atlas-mcp'] is the distribution's default, byte for byte",
          d["pull"]["atlas-mcp"] == TODAY["pull"], d["pull"]["atlas-mcp"])
    check("default: the deploy_warning sentences name the distribution's host and commands, byte for byte", d["warning"] == TODAY_SENTENCES,
          json.dumps(d["warning"]))

CONFIGURED = {
    "ATLAS_DEPLOY_HINT_HOST": "deploy-box",
    "ATLAS_DEPLOY_HINT_REBUILD_API": "kubectl rollout restart deploy/atlas-api",
    "ATLAS_DEPLOY_HINT_REBUILD_MCP": "kubectl rollout restart deploy/atlas-mcp",
    "ATLAS_DEPLOY_HINT_PULL_REBUILD_API": "make deploy-api",
    "ATLAS_DEPLOY_HINT_PULL_REBUILD_MCP": "make deploy-mcp",
}
c, p = hint_child(CONFIGURED)
check("configured: the child ran", c is not None, p.stderr[-1500:])
if c:
    check("configured: every command is the configured one",
          c["rebuild"] == {"atlas-api": "kubectl rollout restart deploy/atlas-api",
                           "atlas-mcp": "kubectl rollout restart deploy/atlas-mcp"}
          and c["pull"] == {"atlas-api": "make deploy-api", "atlas-mcp": "make deploy-mcp"}, json.dumps(c))
    w = c["warning"] or []
    check("configured: deploy_warning names the configured commands and host",
          len(w) == 3 and w[0].endswith("Rebuild: kubectl rollout restart deploy/atlas-mcp")
          and w[1].endswith("Deploy, with the owner's OK: make deploy-mcp")
          and all("deploy-box" in s for s in w), json.dumps(w))
    check("configured: the default host appears nowhere in the output", HOST not in json.dumps(c), json.dumps(c))
h, p = hint_child({"ATLAS_DEPLOY_HINT_HOST": "deploy-box"})
if h:
    check("host only: the default commands follow it, and the default host is gone",
          h["rebuild"]["atlas-mcp"] == expected_hint("ATLAS_DEPLOY_HINT_REBUILD_MCP", "deploy-box")
          and h["pull"]["atlas-mcp"] == expected_hint("ATLAS_DEPLOY_HINT_PULL_REBUILD_MCP", "deploy-box")
          and HOST not in json.dumps(h), json.dumps(h))
else:
    check("host only: the child ran", False, p.stderr[-1500:])
b, p = hint_child({"ATLAS_DEPLOY_HINT_REBUILD_MCP": "  "})
check("a set-but-blank value stops the process, naming it",
      b is None and p.returncode != 0 and "ATLAS_DEPLOY_HINT_REBUILD_MCP" in p.stderr,
      f"rc={p.returncode} {p.stderr[-400:]}")

# --------------------------------------------------------------------------- #
# Every checkout path in the file tables is mounted under /deploy-src. A path the table compares
# but the compose file does not mount reads as "missing in the checkout", so a freshly rebuilt
# image is reported stale on every tool response -- which is what mcp/requirements-lock.txt did
# after PR #131 switched the Dockerfile to the lock. Static: the compose text, no Docker.
# --------------------------------------------------------------------------- #
import re  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
MOUNT_RE = re.compile(r"""^\s*-\s*["']?([^:"'\s]+):/deploy-src/([^:"'\s]+?)(:ro)?["']?\s*(#.*)?$""")
# (component, its spec, the compose file that mounts its checkout, the directory sources resolve
# against: compose resolves relative paths against the FIRST -f file's directory, which for the
# API's live overlay is the repo root, `docker compose -f docker-compose.yml -f deploy/prod.override.yml`).
MOUNTED_BY = (
    ("atlas-api", deploy_status.API_SPEC, REPO / "deploy" / "prod.override.yml", REPO),
    ("atlas-mcp", deploy_status.MCP_SPEC, REPO / "mcp" / "docker-compose.yml", REPO / "mcp"),
)


def deploy_src_mounts(text):
    """[(host source, path under /deploy-src, read-only?)] from a compose file's text."""
    out = []
    for line in text.splitlines():
        m = MOUNT_RE.match(line)
        if m:
            out.append((m.group(1), m.group(2).rstrip("/"), bool(m.group(3))))
    return out


def unmounted(spec, text, base):
    """The spec's checkout paths no /deploy-src mount covers (or covers from the wrong source)."""
    mounts = deploy_src_mounts(text)
    missing = []
    for entry in spec:
        rel = entry[2]
        hit = None
        for src, target, ro in mounts:
            if rel == target or rel.startswith(target + "/"):
                hit = (src, target, ro)
                break
        if hit is None:
            missing.append(rel + " (no mount)")
            continue
        src, target, ro = hit
        host = (base / src / rel[len(target):].lstrip("/")).resolve()
        if host != (REPO / rel).resolve():
            missing.append(f"{rel} (mounted from {src}, which is not {rel})")
        elif not ro:
            missing.append(f"{rel} (mounted read-write)")
        elif not host.exists():
            missing.append(f"{rel} (not in the repo)")
    return missing


print("\n-- every compared source file is mounted under /deploy-src --")
for component, spec, compose, base in MOUNTED_BY:
    if not compose.exists():
        # The homelab overlays (deploy/prod.override.yml) are not in every tree: a checkout
        # without one has no mount list for this component to check.
        print(f"SKIP {component}'s /deploy-src mounts: {compose.relative_to(REPO).as_posix()} is not in this tree")
        continue
    text = compose.read_text(encoding="utf-8")
    found = deploy_src_mounts(text)
    check(f"{component}: {compose.relative_to(REPO).as_posix()} has /deploy-src mounts",
          bool(found), f"{len(found)} mount(s)" if found else "the parser found none -- the format moved")
    gaps = unmounted(spec, text, base)
    check(f"{component}: every checkout path in its spec is mounted read-only from the same path",
          not gaps, "; ".join(gaps))

# The check bites: the mcp compose as it was before the lock mount is caught.
if not (REPO / "mcp" / "docker-compose.yml").exists():
    print("SKIP the check catches a missing mount: mcp/docker-compose.yml is not in this tree")
else:
    _mcp_text = (REPO / "mcp" / "docker-compose.yml").read_text(encoding="utf-8")
    _before = "\n".join(l for l in _mcp_text.splitlines() if "/deploy-src/mcp/requirements-lock.txt" not in l)
    check("the check catches a missing mount (mcp compose without the lock mount)",
          unmounted(deploy_status.MCP_SPEC, _before, REPO / "mcp") == ["mcp/requirements-lock.txt (no mount)"],
          str(unmounted(deploy_status.MCP_SPEC, _before, REPO / "mcp")))

print()
if failures:
    print(f"FAILED — {len(failures)} check(s):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("All checks passed.")
