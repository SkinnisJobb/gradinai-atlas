"""Prove atlas-mcp's distribution seam (STORY-oss-23, item A2): mcp/app/distribution.py.

Every estate-valued default in mcp/app/settings.py (the allowed Host and Origin lists, the
deploy-hint host and commands) is read from mcp/app/distribution.py. What this holds:

  A. the homelab pin: when distribution.NAME is "homelab", its values are exactly the ones
     settings.py carried inline before the seam, compared by sha256 of their canonical JSON
     (so this file names no host). Any other distribution prints why the pin is skipped.
  B. settings.py takes its defaults from distribution.py, and nowhere else: the module-level
     defaults are distribution.py's values, and nothing set means those values are in force.
  C. the neutral overlay (tools/carve/overlay/mcp/app/distribution.py, the export's version)
     swapped in: a copy of mcp/app with it in place boots in a child process; settings,
     deploy_status and (when the mcp package is installed) the whole server import; the
     defaults are the overlay's example.test values; with no deploy hint set every hint
     answers with the sentence naming its variable, and the warning sentences build; an
     operator's ATLAS_DEPLOY_HINT_* / ATLAS_MCP_ALLOWED_HOSTS still win; no estate value
     leaks. Skipped, with the reason printed, when the overlay is not in the tree (the
     exported tree, whose own distribution.py IS the overlay and is covered by B).
  D. the overlay defines the same public names as distribution.py, so neither can grow a
     setting the other lacks.

No network, no database. Same convention as the other mcp/verify_*.py: one [PASS]/[FAIL]
line per claim, exit 1 if any failed.

    python mcp/verify_distribution.py
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OVERLAY = ROOT / "tools" / "carve" / "overlay" / "mcp" / "app" / "distribution.py"

os.environ.setdefault("ATLAS_BASE_URL", "https://atlas.invalid")
for _name in [n for n in os.environ if n.startswith("ATLAS_DEPLOY_HINT_")
              or n in ("ATLAS_MCP_ALLOWED_HOSTS", "ATLAS_MCP_ALLOWED_ORIGINS")]:
    os.environ.pop(_name)
sys.path.insert(0, str(HERE))

from app import distribution, settings  # noqa: E402

failures: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def canonical(dist) -> dict:
    return {"hosts": list(dist.DEFAULT_ALLOWED_HOSTS), "origins": list(dist.DEFAULT_ALLOWED_ORIGINS),
            "hint_host": dist.DEFAULT_DEPLOY_HINT_HOST, "hints": dict(dist.DEFAULT_DEPLOY_HINTS)}


def digest(values: dict) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


#: sha256 of the homelab defaults as settings.py wrote them inline before the seam (origin/master
#: at b682216). A change to any homelab default must change this line too, in review.
HOMELAB_DIGEST = "30b450c262eb51503309901755d0f5e30d696c3a100532e7e5aec0f60b03774f"


def estate_markers() -> tuple:
    """This tree's own installation-specific values (its door's host name and its deploy-hint
    host), which a neutral boot must not carry. Read from distribution.py, not written here."""
    return (distribution.DOOR_HOST.rsplit(":", 1)[0], distribution.DEFAULT_DEPLOY_HINT_HOST)


def section_a() -> None:
    print("-- A. the homelab pin --")
    if distribution.NAME != "homelab":
        print(f"[SKIP] distribution.NAME is {distribution.NAME!r}, not 'homelab': the homelab values "
              "are not this tree's, so there is nothing of theirs to pin")
        return
    values = canonical(distribution)
    ok = digest(values) == HOMELAB_DIGEST
    check("distribution.py's values are the homelab's, byte for byte", ok, "" if ok else json.dumps(values))


def section_b() -> None:
    print("-- B. settings.py reads its defaults from distribution.py --")
    check("DEFAULT_ALLOWED_HOSTS", settings.DEFAULT_ALLOWED_HOSTS == tuple(distribution.DEFAULT_ALLOWED_HOSTS))
    check("DEFAULT_ALLOWED_ORIGINS", settings.DEFAULT_ALLOWED_ORIGINS == tuple(distribution.DEFAULT_ALLOWED_ORIGINS))
    check("DEFAULT_DEPLOY_HINT_HOST", settings.DEFAULT_DEPLOY_HINT_HOST == distribution.DEFAULT_DEPLOY_HINT_HOST)
    check("DEFAULT_DEPLOY_HINTS", settings.DEFAULT_DEPLOY_HINTS == dict(distribution.DEFAULT_DEPLOY_HINTS))
    check("the door is the first allowed host, and https:// it the first allowed origin",
          distribution.DEFAULT_ALLOWED_HOSTS[0] == distribution.DOOR_HOST
          and distribution.DEFAULT_ALLOWED_ORIGINS[0] == "https://" + distribution.DOOR_HOST)
    check("nothing set: the allowed lists in force are the defaults",
          (settings.ALLOWED_HOSTS, settings.ALLOWED_ORIGINS)
          == (settings.DEFAULT_ALLOWED_HOSTS, settings.DEFAULT_ALLOWED_ORIGINS))
    hints = settings.load_deploy_hints({})
    want = {name: (distribution.DEFAULT_DEPLOY_HINTS[name].format(host=distribution.DEFAULT_DEPLOY_HINT_HOST)
                   if name in distribution.DEFAULT_DEPLOY_HINTS else settings.NO_DEPLOY_HINT.format(name=name))
            for _t, _c, name in settings.DEPLOY_HINT_ENVS}
    got = {name: getattr(hints, table)[component] for table, component, name in settings.DEPLOY_HINT_ENVS}
    check("nothing set: the hints in force are the distribution's, host filled in",
          hints.host == distribution.DEFAULT_DEPLOY_HINT_HOST and got == want, json.dumps(got))
    # settings.py loaded by path, with no package, as backend/verify_deploy_status.py loads it.
    import importlib.util
    spec = importlib.util.spec_from_file_location("_mcp_settings_by_path", HERE / "app" / "settings.py")
    by_path = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(by_path)
    check("loaded by path (no package), settings.py still finds distribution.py",
          by_path.DEFAULT_DEPLOY_HINTS == settings.DEFAULT_DEPLOY_HINTS
          and by_path.DEFAULT_ALLOWED_HOSTS == settings.DEFAULT_ALLOWED_HOSTS)


NEUTRAL_CHILD = r'''
import json, os, sys
sys.path.insert(0, os.getcwd())
from app import distribution, settings, deploy_status
out = {"name": distribution.NAME, "door": distribution.DOOR_HOST,
       "hosts": list(settings.ALLOWED_HOSTS), "origins": list(settings.ALLOWED_ORIGINS),
       "host": deploy_status.HOST, "rebuild": deploy_status.REBUILD, "pull": deploy_status.PULL_AND_REBUILD,
       "no_hint": settings.NO_DEPLOY_HINT}
stale = {"state": "stale", "differs_count": 1, "differs": ["mcp/app/x.py"]}
pending = {"state": "pending", "count": 2, "waited_h": 30, "oldest_subject": "Merge pull request #80"}
out["warnings"] = (deploy_status.warnings_for("atlas-mcp", stale, pending)
                   + deploy_status.warnings_for("atlas-api", stale, {"state": "watch_broken", "reason": "stale"}))
try:
    from app import mcp_server
except ImportError as exc:
    out["server"] = "not installed: %s" % exc
else:
    ts = mcp_server.mcp._real.settings.transport_security
    out["server"] = {"hosts": list(ts.allowed_hosts), "origins": list(ts.allowed_origins),
                     "rebinding": ts.enable_dns_rebinding_protection}
print("NEUTRAL " + json.dumps(out))
'''


def boot(app_dir: Path, extra: dict):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("ATLAS_DEPLOY_HINT_") and k not in ("ATLAS_MCP_ALLOWED_HOSTS", "ATLAS_MCP_ALLOWED_ORIGINS")}
    env["ATLAS_BASE_URL"] = "https://atlas.invalid"
    env.update(extra)
    proc = subprocess.run([sys.executable, "-c", NEUTRAL_CHILD], cwd=str(app_dir.parent), env=env,
                          capture_output=True, text=True, timeout=120)
    for line in proc.stdout.splitlines():
        if line.startswith("NEUTRAL "):
            return json.loads(line[len("NEUTRAL "):]), proc
    return None, proc


def section_c_d() -> None:
    print("-- C. the neutral overlay swapped in --")
    if not OVERLAY.is_file():
        print(f"[SKIP] {OVERLAY.relative_to(ROOT).as_posix()} is not in this tree (an exported tree "
              "ships it as mcp/app/distribution.py, which section B covers)")
        return
    with tempfile.TemporaryDirectory(prefix="atlas-mcp-neutral-") as tmp:
        app_dir = Path(tmp) / "app"
        shutil.copytree(HERE / "app", app_dir, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(OVERLAY, app_dir / "distribution.py")

        n, p = boot(app_dir, {})
        check("the overlay boots: settings and deploy_status import", n is not None, p.stderr[-1500:])
        if n:
            check("it is the neutral distribution", n["name"] != "homelab", n["name"])
            check("the door is an example.test / example.com name",
                  n["door"].rsplit(":", 1)[0].endswith((".example.test", ".example.com")), n["door"])
            check("the allowed hosts are the overlay's, door first", n["hosts"][0] == n["door"]
                  and "localhost:*" in n["hosts"] and "testserver" in n["hosts"], str(n["hosts"]))
            check("the allowed origins are the overlay's", n["origins"][0] == "https://" + n["door"],
                  str(n["origins"]))
            want = {table: {component: n["no_hint"].format(name=name)
                            for t, component, name in settings.DEPLOY_HINT_ENVS if t == table}
                    for table in ("rebuild", "pull_and_rebuild")}
            check("no hint set: every command is the sentence naming its variable",
                  n["rebuild"] == want["rebuild"] and n["pull"] == want["pull_and_rebuild"],
                  json.dumps([n["rebuild"], n["pull"]]))
            check("the warning sentences build, and name the remedy variable",
                  len(n["warnings"]) == 4 and "ATLAS_DEPLOY_HINT_REBUILD_MCP" in n["warnings"][0]
                  and "ATLAS_DEPLOY_HINT_PULL_REBUILD_MCP" in n["warnings"][1], json.dumps(n["warnings"]))
            if isinstance(n["server"], dict):
                check("the whole server imports, with rebinding protection on the overlay's lists",
                      n["server"]["rebinding"] is True and n["server"]["hosts"] == n["hosts"]
                      and n["server"]["origins"] == n["origins"], json.dumps(n["server"]))
            else:
                print(f"[SKIP] the whole-server import: the mcp package is {n['server']}")
            blob = json.dumps(n)
            leaked = [m for m in estate_markers() if m in blob] if distribution.NAME != n["name"] else []
            check("no estate value anywhere in the neutral boot", not leaked, str(leaked))

        c, p = boot(app_dir, {"ATLAS_DEPLOY_HINT_HOST": "deploy.example.com",
                              "ATLAS_DEPLOY_HINT_REBUILD_MCP": "make deploy-mcp",
                              "ATLAS_MCP_ALLOWED_HOSTS": "mcp.example.com:443"})
        check("configured on the overlay: the child ran", c is not None, p.stderr[-1500:])
        if c:
            check("configured on the overlay: the operator's values win",
                  c["host"] == "deploy.example.com" and c["rebuild"]["atlas-mcp"] == "make deploy-mcp"
                  and c["hosts"] == ["mcp.example.com:443"], json.dumps(c))

        b, p = boot(app_dir, {"ATLAS_DEPLOY_HINT_HOST": " "})
        check("on the overlay a set-but-blank value still stops the process, naming it",
              b is None and p.returncode != 0 and "ATLAS_DEPLOY_HINT_HOST" in p.stderr, p.stderr[-400:])

    print("-- D. the overlay and distribution.py define the same names --")
    import importlib.util
    spec = importlib.util.spec_from_file_location("_neutral_distribution", OVERLAY)
    neutral = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(neutral)

    def public(mod):
        return sorted(k for k in vars(mod) if k.isupper())
    check("the same public names", public(neutral) == public(distribution),
          f"{public(neutral)} vs {public(distribution)}")
    check("and the same types", all(type(getattr(neutral, k)) is type(getattr(distribution, k))
                                    for k in public(distribution) if hasattr(neutral, k)))


def main() -> None:
    section_a()
    section_b()
    section_c_d()
    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
