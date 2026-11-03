"""Prove ATLAS_DISABLED_FEATURES in atlas-mcp (STORY-oss-08-plugins).

  A. THE SETTING. `app/settings.py` parses the variable like backend/settings.py
     (unset/empty = everything on, trimmed, case-folded), refuses an unknown
     name, and carries the SAME feature list as the backend copy (no drift).
  B. WHAT IS REGISTERED, one child process per configuration (the flags are
     read at import: one process, one configuration, as in a container). The
     real `app.mcp_server` is imported with every HTTP call made fatal, and the
     child reports FastMCP's own `list_tools()` / `list_prompts()` (what
     tools/list and prompts/list answer) and what `atlas_capabilities` returns:
       - default: `atlas_save_work` and the `signoff` prompt are there;
       - every feature off: neither is, nothing else changed, and
         `atlas_capabilities.tools` equals tools/list in both configurations;
       - an unknown name stops the process, naming it.
  C. THE ENVELOPE. `deploy_warning` off: no field and no check at all (not even
     the `/api/health` read); `deploy_status` off: atlas-mcp's own image check
     is skipped while atlas-api's warnings still ride.

    python mcp/verify_feature_flags.py      # needs the mcp requirements; no network
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = "ATLAS_DISABLED_FEATURES"
ALL = "github_link,save_work,deploy_status,deploy_warning,attention_probes"

os.environ.setdefault("ATLAS_BASE_URL", "https://atlas.invalid")
os.environ.pop(ENV, None)                 # this process: the default; children set their own
sys.path.insert(0, str(HERE))

failures: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


# --------------------------------------------------------------------------- #
# A. the setting
# --------------------------------------------------------------------------- #
from app import settings  # noqa: E402

print("-- A. app/settings.py --")
check("unset: nothing disabled", settings.parse_disabled({}) == frozenset())
check("empty and blank: nothing disabled",
      settings.parse_disabled({ENV: ""}) == frozenset() == settings.parse_disabled({ENV: " , "}))
check("trimmed and case-folded",
      settings.parse_disabled({ENV: " Save_Work ,deploy_warning"}) == {"save_work", "deploy_warning"})
check("every name at once", settings.parse_disabled({ENV: ALL}) == set(settings.FEATURES))
try:
    settings.parse_disabled({ENV: "save_work,sav_work"})
    check("an unknown name is refused", False, "nothing raised")
except settings.FeatureError as exc:
    check("an unknown name is refused, naming it and the variable",
          "sav_work" in str(exc) and ENV in str(exc), str(exc))
try:
    settings.feature_enabled("no_such_feature")
    check("asking for a feature that does not exist is an error in code", False)
except KeyError:
    check("asking for a feature that does not exist is an error in code", True)
check("this process (unset) has every feature on",
      all(settings.feature_enabled(n) for n in settings.FEATURES), str(settings.DISABLED))

# The backend copy, read as a module from its file (standard library only).
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("_backend_settings", HERE.parent / "backend" / "settings.py")
backend_settings = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backend_settings   # dataclasses resolves annotations through sys.modules
spec.loader.exec_module(backend_settings)
check("the feature list is the backend's, name for name and in order",
      settings.FEATURES == backend_settings.FEATURES,
      f"mcp {settings.FEATURES} vs backend {backend_settings.FEATURES}")
check("the variable is the backend's", settings.DISABLED_FEATURES_ENV == backend_settings.DISABLED_FEATURES_ENV)

# --------------------------------------------------------------------------- #
# B. what is registered, per configuration
# --------------------------------------------------------------------------- #
CHILD = r'''
import asyncio, json, os, sys
sys.path.insert(0, os.getcwd())
import httpx
def _no_network(self, method, url, *a, **kw):
    raise AssertionError(f"unexpected HTTP call: {method} {url}")
httpx.Client.request = _no_network
from app import mcp_server
from app.client import client
client.get = lambda path, **k: {"vocabulary": "stub"} if path == "/api/vocabulary" else {}
real = mcp_server.mcp._real
tools = sorted(t.name for t in asyncio.run(real.list_tools()))
prompts = sorted(p.name for p in asyncio.run(real.list_prompts()))
caps = mcp_server.mcp._tool_manager.get_tool("atlas_capabilities").fn()
print("REGISTERED " + json.dumps({"tools": tools, "prompts": prompts, "caps": caps}))
'''


def registered(value):
    env = {k: v for k, v in os.environ.items() if k != ENV}
    env["ATLAS_BASE_URL"] = "https://atlas.invalid"
    if value is not None:
        env[ENV] = value
    proc = subprocess.run([sys.executable, "-c", CHILD], cwd=HERE, env=env,
                          capture_output=True, text=True, timeout=120)
    for line in proc.stdout.splitlines():
        if line.startswith("REGISTERED "):
            return json.loads(line[len("REGISTERED "):]), proc
    return None, proc


print("\n-- B. tools/list, prompts/list and atlas_capabilities per configuration --")
on, p = registered(None)
check("default: the server imports and registers", on is not None, p.stderr[-1500:])
off, p2 = registered(ALL)
check("every feature off: the server imports and registers", off is not None, p2.stderr[-1500:])
if on and off:
    check("default: atlas_save_work is in tools/list", "atlas_save_work" in on["tools"], on["tools"])
    check("default: the signoff prompt is in prompts/list", "signoff" in on["prompts"], on["prompts"])
    check("default: atlas_capabilities.tools equals tools/list",
          on["caps"].get("ok") is True and on["caps"]["result"]["tools"] == on["tools"], str(on["caps"]))
    check("every feature off: no atlas_save_work in tools/list", "atlas_save_work" not in off["tools"],
          off["tools"])
    check("every feature off: no signoff prompt", "signoff" not in off["prompts"], off["prompts"])
    check("every feature off: atlas_capabilities.tools equals tools/list",
          off["caps"].get("ok") is True and off["caps"]["result"]["tools"] == off["tools"], str(off["caps"]))
    check("every feature off: atlas_capabilities.tools has no atlas_save_work",
          "atlas_save_work" not in (off["caps"].get("result") or {}).get("tools", ["?"]))
    check("every feature off: the ONLY tool removed is atlas_save_work",
          sorted(set(on["tools"]) - set(off["tools"])) == ["atlas_save_work"]
          and not set(off["tools"]) - set(on["tools"]),
          f"removed {sorted(set(on['tools']) - set(off['tools']))}")
    check("every feature off: no deploy_warning on a response (both images unknown either way)",
          "deploy_warning" not in off["caps"])
    empty, _ = registered("")
    check("empty is the default", empty is not None and empty["tools"] == on["tools"]
          and empty["prompts"] == on["prompts"])
bad, p3 = registered("save_work,sav_work")
check("an unknown name stops the process, naming it",
      bad is None and p3.returncode != 0 and "sav_work" in p3.stderr and ENV in p3.stderr,
      f"rc={p3.returncode} {p3.stderr[-400:]}")

# --------------------------------------------------------------------------- #
# C. the envelope (in process: the guard reads settings.DISABLED on every call)
# --------------------------------------------------------------------------- #
print("\n-- C. deploy_warning and deploy_status in the envelope --")
from app import client as client_mod, deploy_status, deploy_warning  # noqa: E402
from app.envelope import envelope  # noqa: E402

calls = {"own": 0, "health": 0}


def fake_check(*a, **k):
    calls["own"] += 1
    return {"warnings": ["atlas-mcp stale ... Rebuild: X"]}


def fake_get(path, **k):
    calls["health"] += 1
    return {"deploy": {"warnings": ["atlas-api stale ... Deploy: Y"]}}


deploy_status.check = fake_check
client_mod.client.get = fake_get


def run(disabled):
    settings.DISABLED = frozenset(disabled)
    deploy_warning._api["at"] = 0.0           # expire the cache
    calls.update(own=0, health=0)
    return envelope(lambda: {"answer": 42})()


out = run(())
check("all on (perturbation): both warnings ride, both checks ran",
      len(out.get("deploy_warning", [])) == 2 and calls == {"own": 1, "health": 1}, f"{out} {calls}")
out = run(("deploy_warning",))
check("deploy_warning off: no field, the result untouched",
      "deploy_warning" not in out and out.get("result") == {"answer": 42}, str(out))
check("deploy_warning off: neither check ran (no /api/health read)",
      calls == {"own": 0, "health": 0}, str(calls))
out = run(("deploy_status",))
check("deploy_status off: atlas-mcp's own check is skipped, atlas-api's warning still rides",
      out.get("deploy_warning") == ["atlas-api stale ... Deploy: Y"] and calls["own"] == 0, f"{out} {calls}")
settings.DISABLED = frozenset()

print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAIL'}")
sys.exit(1 if failures else 0)
