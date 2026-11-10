"""FastMCP binding — the `atlas` MCP surface (stable tool names).

Copied from the pattern of an earlier FastMCP service (its mcp_server.py,
TASK-601). The same gotchas apply here:

  - no `from __future__ import annotations` in this module — FastMCP inspects a
    tool's real signature to build its JSON-Schema, and postponed evaluation of
    annotations breaks that introspection.
  - `FastMCP("atlas", streamable_http_path="/")` so the app mounts cleanly at
    `/mcp/` (main.py) instead of `/mcp/mcp/`.
  - the session manager is started in the FastAPI lifespan (main.py), else every
    MCP request fails with "Task group is not initialized."

Every tool returns the uniform envelope from envelope.py:
  success -> {"ok": True,  "result": <...>}
  failure -> {"ok": False, "error": {"type": <code>, "message": <str>}}

This module registers one tool itself, `atlas_health` (history: TASK-1077 shipped it
alone). The rest of the verb surface lives in the `tools_*.py` modules registered below,
one module per area; a new tool goes in the module for its area.
"""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
# Not used here: client._token_from_mcp_request reads the caller's Bearer token through
# it on every call. Imported at boot so an SDK that moved it fails the start, loudly,
# instead of every tool call (TASK-1366).
from mcp.server.lowlevel.server import request_ctx as _request_ctx  # noqa: F401

from . import settings                                    # allowed hosts; ATLAS_DISABLED_FEATURES; stateless
from . import client as client_mod
from .client import client
from .descriptions import DESCRIPTIONS
from .envelope import envelope


class _Described:
    """`FastMCP`, except `tool()` takes each tool's description from descriptions.py.

    FastMCP would send a tool's `__doc__` verbatim, and the descriptions used to
    live there, one per function across four files: hard to review together, and
    on the image's Python 3.12 they went out with their source indentation
    (1,179 characters across 18 tools on live, 2026-09-14). Since 2026-10-23 the
    text is descriptions.py's, looked up by the function's name, and a tool with
    no entry there is refused at import. Passing `description=` is FastMCP's
    public way to set it, so no internals are touched. Everything else is
    forwarded to the real instance.
    """

    def __init__(self, real: FastMCP):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def tool(self, *args, **kwargs):
        def decorator(fn):
            if kwargs.get("description") is None:
                if fn.__name__ not in DESCRIPTIONS:
                    raise KeyError(f"{fn.__name__} has no entry in app/descriptions.py")
                kwargs["description"] = DESCRIPTIONS[fn.__name__]
            return self._real.tool(*args, **kwargs)(fn)
        return decorator


# STREAMABLE-HTTP SESSION IDLE TIMEOUT: 4 HOURS (owner decision, 2026-10-25; TASK-1366).
# mcp 1.30 closes a stateful session after this long with no HTTP request on it (the fix
# for CVE-2026-59951, sessions never reclaimed); its default is 30 minutes. Four hours
# outlasts a working session with long pauses between Atlas calls, so a client does not
# have to re-initialize mid-task, and still bounds what an abandoned session holds.
SESSION_IDLE_TIMEOUT_S = 4 * 60 * 60

# DNS-rebinding protection ON, with an explicit allow-list (owner decision, 2026-10-25; the fix for
# CVE-2025-66416). A Host outside settings.ALLOWED_HOSTS is answered 421, an Origin outside
# settings.ALLOWED_ORIGINS 403. Passing this is also what keeps FastMCP from choosing for
# us: given no settings and its default host 127.0.0.1, 1.30 would allow loopback ONLY,
# and every call through the TLS door would be a 421. The lists and where each default
# host comes from are in settings.py (ATLAS_MCP_ALLOWED_HOSTS / _ORIGINS).
TRANSPORT_SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=list(settings.ALLOWED_HOSTS),
    allowed_origins=list(settings.ALLOWED_ORIGINS),
)


class _AuditedFastMCP(FastMCP):
    """`FastMCP`, except each tool call labels the API requests it makes.

    `client.tool_call(name)` puts the tool name and a per-call request id on every
    request the tool sends to atlas-api (`X-Atlas-Tool`, `X-Request-ID`), which the
    API's read audit records (STORY-oss-16). FastMCP registers `self.call_tool` as
    the protocol's tools/call handler, so overriding it here wraps every tool, sync
    or async, by the name the CLIENT called, without touching any tool's signature.
    A sync tool runs inline in this context, so the label reaches its requests.
    """

    async def call_tool(self, name, arguments, *args, **kwargs):
        with client_mod.tool_call(str(name)):
            return await super().call_tool(name, arguments, *args, **kwargs)


# STATELESS STREAMABLE HTTP: OPT-IN, OFF BY DEFAULT (TASK-1480, ARO 2.2). With
# ATLAS_MCP_STATELESS_HTTP on (settings.py has the why), FastMCP's own `stateless_http`
# mode: no session is kept, so any replica answers any request. Off, the keyword is the
# SDK's default (False) and the server is built exactly as before. The idle timeout is
# still passed: the SDK ignores it in stateless mode, and passing it either way keeps
# one construction instead of two. Auth is not touched: the gate in main.py and the
# per-call token in client.py run per HTTP request, which is what stateless mode serves.
mcp = _Described(_AuditedFastMCP(
    "atlas",
    streamable_http_path="/",
    session_idle_timeout=SESSION_IDLE_TIMEOUT_S,
    transport_security=TRANSPORT_SECURITY,
    stateless_http=settings.STATELESS_HTTP,
))


def _atlas_health() -> dict:
    return client.get("/api/health")


@mcp.tool()
def atlas_health() -> dict:
    return envelope(_atlas_health)()


# The Atlas verb surface lives in one module per area, each exposing
# `register(mcp)`. They are separate files because they were built in parallel
# and because a single tools file would be the one place every future change
# collides. Keep this block a flat list of registrations, one line each.
from . import tools_read; tools_read.register(mcp)          # TASK-1082/1083/1084
from . import tools_work; tools_work.register(mcp)          # TASK-1088/1089
from . import tools_graph; tools_graph.register(mcp)        # TASK-1090..1093
from . import tools_setup; tools_setup.register(mcp)        # the setup guide's interview
from . import tools_comments; tools_comments.register(mcp)  # comments on a workitem
if settings.feature_enabled("save_work"):
    from . import tools_session; tools_session.register(mcp)  # signoff: atlas_save_work
