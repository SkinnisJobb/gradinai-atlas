"""atlas-mcp's installation-specific defaults: the distribution seam.

Every default in mcp/app/settings.py that names a particular installation -- a host
name, an address, a deploy command -- is read from here, and from nowhere else. The
mechanism (the variable names, the parsing, the refusals) stays in settings.py; this
file holds only values.

This is the neutral distribution. It names no real host: the TLS door is an
example.test name, and there are no default deploy commands. An operator sets their
own through the environment (ATLAS_MCP_ALLOWED_HOSTS, ATLAS_MCP_ALLOWED_ORIGINS,
ATLAS_DEPLOY_HINT_*; docs/oss/CONFIG.md), or replaces this file in their own build.

backend/distribution.py is atlas-api's counterpart. The deploy-hint values here must
equal the ones there: backend/verify_deploy_status.py compares the two settings modules.

Standard library only, no imports at all: settings.py loads this file by path when it
is itself loaded by path. Deliberately NOT `from __future__ import annotations`.
"""

#: Which distribution this is. The verify scripts pin the original installation's
#: values only for "homelab", and print why they skip that pin for any other.
NAME = "neutral"

# --------------------------------------------------------------------------- #
# Deploy hints (settings.py `load_deploy_hints`)
# --------------------------------------------------------------------------- #
#: The host the deploy-warning sentences name, and `{host}` in any command below.
DEFAULT_DEPLOY_HINT_HOST = "atlas.example.test"

#: No default commands. For each ATLAS_DEPLOY_HINT_* variable left unset, settings.py
#: answers with a sentence naming the variable to set; the service runs normally.
DEFAULT_DEPLOY_HINTS = {}

# --------------------------------------------------------------------------- #
# Allowed Host / Origin headers (settings.py `load_allowed_hosts`)
# --------------------------------------------------------------------------- #
#: The public name and port MCP clients reach atlas-mcp at (the TLS front). The first
#: entry of both lists below, and the Host the verify scripts send as "the door".
DOOR_HOST = "atlas.example.test:8446"

# Where each default host comes from:
#   DOOR_HOST                    the TLS front, which forwards the client's Host unchanged.
#   atlas-mcp:8092               the service on its compose network, by container name.
#   localhost:*, 127.0.0.1:*, [::1]:*
#                                loopback: a local publish or `uvicorn ... --port 8092`,
#                                and the verify scripts' TestClient(base_url=...).
#   testserver                   Starlette's TestClient default Host.
DEFAULT_ALLOWED_HOSTS = (
    DOOR_HOST,
    "atlas-mcp:8092",
    "localhost:*",
    "127.0.0.1:*",
    "[::1]:*",
    "testserver",
)

#: Checked only when a request CARRIES an Origin header. MCP clients that are not
#: browsers send none, and pass.
DEFAULT_ALLOWED_ORIGINS = (
    "https://" + DOOR_HOST,
    "http://localhost:*",
    "http://127.0.0.1:*",
    "http://[::1]:*",
)
