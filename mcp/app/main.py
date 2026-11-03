"""FastAPI host for the `atlas` MCP server.

Mounts the FastMCP streamable-HTTP app at /mcp/, copied from the pattern of an earlier
FastMCP service (TASK-601):

  - no `from __future__ import annotations` in any module where a tool function is
    defined (mcp_server.py) — it breaks FastMCP's signature introspection,
  - FastMCP built with streamable_http_path="/" so the effective endpoint is /mcp/
    and not /mcp/mcp/ (see mcp_server.py),
  - the session manager is started in the FastAPI lifespan below, else MCP requests
    fail with "Task group is not initialized."

EPIC-atlas-mcp, TASK-1077 (this skeleton) and TASK-1078 (app/client.py, the
atlas-api client). Ships exactly one tool, atlas_health — later tasks in the epic
own the rest of the Atlas verb surface.

Caller auth (2026-10): the MCP mount is wrapped in `caller_auth.RequireCallerToken`,
so every caller authenticates with its own `atlas_svc_` Bearer token and acts as
itself against atlas-api (see caller_auth.py and client.py).
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import oauth_resource
from .caller_auth import RequireCallerToken
from .mcp_server import mcp

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
log = logging.getLogger("atlas-mcp")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Atlas MCP", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# STORY-oss-25: with ATLAS_OAUTH_RESOURCE set, the RFC 9728 metadata an MCP client reads to
# find the IdP (app/oauth_resource.py). Unset (the default): no route is added at all.
oauth_resource.install(app)

# Every request under /mcp/ passes the caller gate first: no valid Bearer token, a
# 401 and no MCP session, no tool. /health above stays open for the healthcheck.
app.mount("/mcp", RequireCallerToken(mcp.streamable_http_app()))


if __name__ == "__main__":
    import uvicorn

    log.info("Starting atlas-mcp on 0.0.0.0:8092")
    uvicorn.run(app, host="0.0.0.0", port=8092)
