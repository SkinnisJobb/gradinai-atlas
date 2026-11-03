"""The stale-deploy warning every atlas-mcp tool response carries, and only while there is one.

`envelope()` adds `deploy_warning: [...]` to a tool's result when either image is stale or merges
have waited on GitHub (see `deploy_status.py`). When everything is current, it adds nothing, so a
healthy deploy costs callers no tokens. It rides on EVERY response because the failure it names
(a stale image) shows up as a missing tool or a wrong answer from a tool the caller did call. A
separate status tool is exactly what nobody calls.

Two sources:
  - atlas-mcp's own image, checked in-process (`deploy_status.check`, cached 60s);
  - atlas-api's image, read from `GET /api/health`'s `deploy` field, cached API_RECHECK_S so a
    tool call does not cost a second request each time.

Nothing here may raise into a tool call: a broken check yields no warning, never a failed tool.
"""

import threading
import time

from . import deploy_status

API_RECHECK_S = 300

_lock = threading.Lock()
_api = {"at": 0.0, "warnings": []}


def _api_warnings():
    with _lock:
        if time.time() - _api["at"] < API_RECHECK_S:
            return list(_api["warnings"])
        _api["at"] = time.time()
    try:
        from .client import client  # late: client imports envelope, which imports this module
        health = client.get("/api/health") or {}
        warnings = list(((health.get("deploy") or {}).get("warnings")) or [])
    except Exception:  # noqa: BLE001 -- an unreachable API is reported by the tool itself
        warnings = []
    with _lock:
        _api["warnings"] = warnings
    return warnings


def current():
    """Every warning that applies right now; empty when both images are current."""
    own = []
    from . import settings  # ATLAS_DISABLED_FEATURES=deploy_status skips this image's own check
    if settings.feature_enabled("deploy_status"):
        try:
            own = deploy_status.check("atlas-mcp", deploy_status.MCP_SPEC)["warnings"]
        except Exception:  # noqa: BLE001
            own = []
    return own + _api_warnings()
