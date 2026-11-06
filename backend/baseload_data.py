"""The baseload: the world model a fresh install ships with. GENERATED, DO NOT EDIT.

Rendered by `tools/gen_baseload.py` from the code (the MCP tool registry, the route table, the board's
views, the admin CLI, the OSS compose file, the settings catalogue, the OSS guides) and from the
reviewed data in `tools/baseload/`. Regenerate with `python tools/gen_baseload.py`; CI runs
`--check`. Python data rather than a file read at boot: boot opens no file (backend/CLAUDE.md).

`system_node.py` seeds it after the seven handbook nodes of `system_handbook.py`, with the same
rule: a node that exists is never touched. Every node's `attributes.baseload` names its source.

  NODES            the nodes after the handbook set, parents first; each has its `contains` parent.
  EDGES            the `requires` edges (object -> object, a declared pair).
  INTENTS          the intents that ship (tools/baseload/from_self_seed.json).
  HANDBOOK_EXTRAS  documents and a setting for handbook nodes, written only with that node.
  COMPOSE          every deploy/oss/docker-compose.yml service and the node that describes it.
"""

NODES = [
    {
        "id": "atlas-system-tls",
        "type": "service",
        "parent": "atlas-system",
        "name": "atlas-tls",
        "summary": "The TLS front: the one published port, the internal origin the MCP calls the API through, the install's certificate authority and optional client certificates.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
                "compose": [
                    {
                        "service": "atlas-tls",
                        "profiles": [],
                    },
                ],
            },
        },
        "docs": [
            {
                "file": "configuration.md",
                "title": "Configuration",
                "kind": "runbook",
                "content": (
                    "# Configuration\n"
                    "\n"
                    "The settings of atlas-tls, from `backend/settings_catalog.py`. Every setting, with its default and where it is read, is in the configuration reference (CONFIG.md); an owner sees the effective values in the board's Config tab.\n"
                    "\n"
                    "| Setting | Kind | Read by | What it is |\n"
                    "|---|---|---|---|\n"
                    "| `ATLAS_TLS_MODE` | plain | compose | Where the TLS front's certificate comes from. |\n"
                    "| `ATLAS_TLS_CLIENT_AUTH` | plain | compose | Whether the TLS front asks for a client certificate. |\n"
                    "| `ATLAS_TLS_BIND` | plain | compose | The host address the TLS front publishes on. |\n"
                    "| `ATLAS_TLS_PORT` | plain | compose | The host port the TLS front publishes on. |\n"
                    "| `ATLAS_TLS_CLIENT_CA_FILE` | plain | compose | The CA that client certificates are checked against. |\n"
                    "| `ATLAS_TLS_CF_TOKEN_FILE` | plain | compose | The file holding the DNS token for ACME over Cloudflare. |\n"
                    "| `ATLAS_PUBLIC_HOST` | plain | compose | The host name the install is reached on. |\n"
                    "| `ATLAS_ORIGIN_SUBNET` | plain | compose | The subnet of the network between the TLS front and the API. |\n"
                ),
            },
        ],
        "abstract": "atlas-tls is the only service an install publishes. Browsers and agent clients reach the board, the API and the MCP through it over TLS. It also serves the API to atlas-mcp on an internal listener that is not published, so the MCP never talks to the API in plain text. Where its certificate comes from is a setting (`ATLAS_TLS_MODE`); whether it asks for a client certificate is another (`ATLAS_TLS_CLIENT_AUTH`).",
    },
    {
        "id": "atlas-system-migrate",
        "type": "service",
        "parent": "atlas-system",
        "name": "atlas-migrate",
        "summary": "A one-shot schema upgrade that runs before atlas-api starts: `atlas-migrate upgrade`.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
                "compose": [
                    {
                        "service": "atlas-migrate",
                        "profiles": [],
                    },
                ],
            },
        },
        "docs": [],
        "abstract": "atlas-migrate runs the same image as atlas-api with one command, `atlas-migrate upgrade`, and exits. atlas-api starts only after it has succeeded, so an upgraded image never serves against a schema it does not expect. It changes the schema only additively; a destructive step is a separate, deliberate command.",
    },
    {
        "id": "atlas-system-backup",
        "type": "service",
        "parent": "atlas-system",
        "name": "atlas-backup",
        "summary": "Scheduled, verified logical backups of the Atlas database (compose profile `backup`).",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
                "compose": [
                    {
                        "service": "atlas-backup",
                        "profiles": [
                            "backup",
                        ],
                    },
                ],
            },
            "setting": "COMPOSE_PROFILES=backup",
        },
        "docs": [
            {
                "file": "configuration.md",
                "title": "Configuration",
                "kind": "runbook",
                "content": (
                    "# Configuration\n"
                    "\n"
                    "The settings of atlas-backup, from `backend/settings_catalog.py`. Every setting, with its default and where it is read, is in the configuration reference (CONFIG.md); an owner sees the effective values in the board's Config tab.\n"
                    "\n"
                    "| Setting | Kind | Read by | What it is |\n"
                    "|---|---|---|---|\n"
                    "| `ATLAS_BACKUP_SCHEDULE` | plain | atlas-api | How often the backup profile takes a routine backup outside crunch week: off, hourly, daily or weekly. |\n"
                    "| `ATLAS_BACKUP_KEEP` | plain | atlas-api | How many routine backups are kept; older routine backups are deleted. |\n"
                    "| `ATLAS_BACKUP_HOST_DIR` | plain | compose | Where the backup profile keeps its dumps on the host. |\n"
                ),
            },
        ],
        "abstract": "atlas-backup takes a logical dump of the Atlas database on a schedule, restores it into a scratch database to prove it can be read back, and keeps a bounded number of copies. It talks to Postgres and nothing else and publishes no port. `atlas-admin backup run|restore|rollback` drive the same code by hand.",
    },
    {
        "id": "atlas-system-retrieval-check",
        "type": "service",
        "parent": "atlas-system-retrieval",
        "name": "atlas-retrieval",
        "summary": "A one-shot check of the retrieval add-on's configuration (compose profile `retrieval`); the indexing itself runs inside atlas-api.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
                "compose": [
                    {
                        "service": "atlas-retrieval",
                        "profiles": [
                            "retrieval",
                        ],
                    },
                ],
            },
            "setting": "COMPOSE_PROFILES=retrieval",
        },
        "docs": [],
        "abstract": "atlas-retrieval reads the retrieval add-on's configuration, refuses what could not be right (no endpoint, a key that is not a file, a vector length that does not fit) and exits. The add-on itself, the reconciler that keeps embeddings current and the vector half of search, runs inside atlas-api when `ATLAS_RETRIEVAL=on`.",
    },
    {
        "id": "atlas-system-api-access",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Access routes",
        "summary": "Visibility marks, grants, groups, project role bindings and the people list.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/access.py",
                "module": "access",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Access routes\n"
                    "\n"
                    "Declared in `backend/routers/access.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/access` | signed in |  |\n"
                    "| PUT | `/api/access/github/{user_id}` | write (`admin` scope) | off when `github_link` is in ATLAS_DISABLED_FEATURES |\n"
                    "| POST | `/api/access/github/{user_id}/refresh` | write (`admin` scope) | off when `github_link` is in ATLAS_DISABLED_FEATURES |\n"
                    "| GET | `/api/access/groups` | signed in |  |\n"
                    "| POST | `/api/access/groups` | write (`admin` scope) |  |\n"
                    "| DELETE | `/api/access/groups/{group_id}` | write (`admin` scope) |  |\n"
                    "| PUT | `/api/access/groups/{group_id}/members` | write (`admin` scope) |  |\n"
                    "| PUT | `/api/access/node/{node_id}` | write (`admin` scope) |  |\n"
                    "| PUT | `/api/access/project/{project_id}/bindings` | write (`admin` scope) |  |\n"
                    "| GET | `/api/access/projects` | signed in |  |\n"
                    "| GET | `/api/people` | signed in |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-adrs",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "ADR routes",
        "summary": "The architecture decision record registry: search, read and record.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/adrs.py",
                "module": "adrs",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# ADR routes\n"
                    "\n"
                    "Declared in `backend/routers/adrs.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/adrs` | signed in |  |\n"
                    "| PUT | `/api/adrs/{adr_id}` | write (`write` scope) |  |\n"
                    "| GET | `/api/adrs/{ref}` | signed in |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-assets",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Board and asset routes",
        "summary": "The landing page, the board pages and their static assets.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/assets.py",
                "module": "assets",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Board and asset routes\n"
                    "\n"
                    "Declared in `backend/routers/assets.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/` | public |  |\n"
                    "| HEAD | `/` | public |  |\n"
                    "| GET | `/assets/{name:path}` | signed in |  |\n"
                    "| GET | `/board` | signed in |  |\n"
                    "| GET | `/board/legacy` | signed in |  |\n"
                    "| GET | `/board/v2` | signed in |  |\n"
                    "| GET | `/favicon.ico` | public |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-audit",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Audit export route",
        "summary": "The read audit as JSON Lines, for an owner.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/audit.py",
                "module": "audit",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Audit export route\n"
                    "\n"
                    "Declared in `backend/routers/audit.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/audit/export` | signed in |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-auth",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Sign-in routes",
        "summary": "Sign-in, sign-out, password change, the privacy page and the single sign-on callbacks, served by the gate itself.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/auth.py",
                "module": "auth",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Sign-in routes\n"
                    "\n"
                    "Declared in `backend/auth.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/me` | signed in |  |\n"
                    "| GET | `/auth/denied` | public |  |\n"
                    "| GET | `/auth/login` | public |  |\n"
                    "| POST | `/auth/login` | public |  |\n"
                    "| POST | `/auth/logout` | write (session only) |  |\n"
                    "| GET | `/auth/password/change` | public |  |\n"
                    "| POST | `/auth/password/change` | public |  |\n"
                    "| GET | `/auth/privacy` | public |  |\n"
                    "| GET | `/healthz` | public |  |\n"
                    "| GET | `/readyz` | public |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-comments",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Comment routes",
        "summary": "Comments on a workitem: list, add, edit and delete.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/comments.py",
                "module": "comments",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Comment routes\n"
                    "\n"
                    "Declared in `backend/routers/comments.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| PATCH | `/api/comment/{comment_id}` | write (`write` scope) |  |\n"
                    "| DELETE | `/api/comment/{comment_id}` | write (`admin` scope) |  |\n"
                    "| GET | `/api/workitem/{workitem_id}/comments` | signed in |  |\n"
                    "| POST | `/api/workitem/{workitem_id}/comments` | write (`write` scope) |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-config",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Configuration routes",
        "summary": "Every setting with its effective value and source, for an owner; one write route when board writes are switched on.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/config.py",
                "module": "config",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Configuration routes\n"
                    "\n"
                    "Declared in `backend/routers/config.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/config` | signed in |  |\n"
                    "| PUT | `/api/config/{name}` | write (`admin` scope) | ATLAS_CONFIG_WRITES=1 |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-context",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Context route",
        "summary": "The context answer: the best-matching nodes, their neighbours, intents, decisions and documents, within budgets.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/context.py",
                "module": "context",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Context route\n"
                    "\n"
                    "Declared in `backend/routers/context.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/context` | signed in |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-crunch",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Crunch week routes",
        "summary": "Read, start and end crunch week, and the banner the board shows during it.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/crunch.py",
                "module": "crunch",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Crunch week routes\n"
                    "\n"
                    "Declared in `backend/routers/crunch.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/crunch` | signed in |  |\n"
                    "| PUT | `/api/crunch` | write (`admin` scope) |  |\n"
                    "| GET | `/api/crunch/banner` | signed in |  |\n"
                    "| POST | `/api/crunch/end` | write (`admin` scope) |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-docs",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Document routes",
        "summary": "Read, write and delete a node's documents, served only when listed in the node's docs[].",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/docs.py",
                "module": "docs",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Document routes\n"
                    "\n"
                    "Declared in `backend/routers/docs.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/node/{node_id}/doc` | signed in |  |\n"
                    "| PUT | `/api/node/{node_id}/doc` | write (`write` scope) |  |\n"
                    "| DELETE | `/api/node/{node_id}/doc` | write (`admin` scope) |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-edges",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Edge routes",
        "summary": "Create and delete edges.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/edges.py",
                "module": "edges",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Edge routes\n"
                    "\n"
                    "Declared in `backend/routers/edges.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| POST | `/api/edge` | write (`write` scope) |  |\n"
                    "| DELETE | `/api/edge/{edge_id}` | write (`admin` scope) |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-events",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Event routes",
        "summary": "The graph event log and the sign-in event log.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/events.py",
                "module": "events",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Event routes\n"
                    "\n"
                    "Declared in `backend/routers/events.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/auth-events` | signed in |  |\n"
                    "| GET | `/api/events` | signed in |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-graph",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Graph routes",
        "summary": "The graph read: the whole graph or a neighbourhood, the outline, the path between two nodes, the tree.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/graph.py",
                "module": "graph",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Graph routes\n"
                    "\n"
                    "Declared in `backend/routers/graph.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/graph` | signed in |  |\n"
                    "| GET | `/api/graph/outline` | signed in |  |\n"
                    "| GET | `/api/graph/path` | signed in |  |\n"
                    "| GET | `/api/graph/tree` | signed in |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-handbook",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Handbook site routes",
        "summary": "The static handbook at /docs, rendered from the system node set's documents.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/handbook.py",
                "module": "handbook",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Handbook site routes\n"
                    "\n"
                    "Declared in `backend/routers/handbook.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/docs` | signed in |  |\n"
                    "| GET | `/docs/{name:path}` | signed in |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-health",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Health routes",
        "summary": "Liveness, readiness and the API's health report with its drift warnings.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/health.py",
                "module": "health",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Health routes\n"
                    "\n"
                    "Declared in `backend/routers/health.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/health` | signed in |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-intents",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Intent routes",
        "summary": "List, create, edit and delete node intents.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/intents.py",
                "module": "intents",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Intent routes\n"
                    "\n"
                    "Declared in `backend/routers/intents.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| PATCH | `/api/intent/{intent_id}` | write (`write` scope) |  |\n"
                    "| DELETE | `/api/intent/{intent_id}` | write (`admin` scope) |  |\n"
                    "| GET | `/api/intents` | signed in |  |\n"
                    "| POST | `/api/node/{node_id}/intent` | write (`write` scope) |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-landing",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Product site routes",
        "summary": "The product website's own files under /site.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/landing.py",
                "module": "landing",
            },
            "setting": "ATLAS_LANDING=v2",
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Product site routes\n"
                    "\n"
                    "Declared in `backend/routers/landing.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/site/{name:path}` | signed in | ATLAS_LANDING=v2 |\n"
                    "| HEAD | `/site/{name:path}` | signed in | ATLAS_LANDING=v2 |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-legacy-v0",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "First-generation routes",
        "summary": "The first-generation tree, summary, priority and goals routes, kept for installs that still read them.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/legacy_v0.py",
                "module": "legacy_v0",
            },
            "setting": "ATLAS_LEGACY_V0=1",
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# First-generation routes\n"
                    "\n"
                    "Declared in `backend/routers/legacy_v0.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/goals` | signed in |  |\n"
                    "| GET | `/api/priority` | signed in |  |\n"
                    "| GET | `/api/summary` | signed in |  |\n"
                    "| GET | `/api/tree` | signed in |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-me",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Account routes",
        "summary": "The signed-in account: who it is, its password, its agent credentials.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/me.py",
                "module": "me",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Account routes\n"
                    "\n"
                    "Declared in `backend/routers/me.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| POST | `/api/me/agent-credential` | write (`admin` scope) |  |\n"
                    "| POST | `/api/me/agent-credential/{token_id}/revoke` | write (`admin` scope) |  |\n"
                    "| GET | `/api/me/agent-credentials` | signed in |  |\n"
                    "| POST | `/api/me/agent/{agent}/credential` | write (`admin` scope) |  |\n"
                    "| GET | `/api/me/github` | signed in | off when `github_link` is in ATLAS_DISABLED_FEATURES |\n"
                    "| GET | `/api/me/github/callback` | signed in | off when `github_link` is in ATLAS_DISABLED_FEATURES |\n"
                    "| GET | `/api/me/github/start` | signed in | off when `github_link` is in ATLAS_DISABLED_FEATURES |\n"
                    "| POST | `/api/me/github/unlink` | write (`admin` scope) | off when `github_link` is in ATLAS_DISABLED_FEATURES |\n"
                    "| POST | `/api/me/password` | write (`admin` scope) |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-nodes",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Node routes",
        "summary": "Read and write one node, its structure and its status.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/nodes.py",
                "module": "nodes",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Node routes\n"
                    "\n"
                    "Declared in `backend/routers/nodes.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| POST | `/api/node` | write (`write` scope) |  |\n"
                    "| GET | `/api/node/{node_id}` | signed in |  |\n"
                    "| PATCH | `/api/node/{node_id}` | write (`write` scope) |  |\n"
                    "| DELETE | `/api/node/{node_id}` | write (`admin` scope) |  |\n"
                    "| POST | `/api/node/{node_id}/status` | write (`write` scope) |  |\n"
                    "| GET | `/api/node/{node_id}/structure` | signed in |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-retrieval",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Retrieval routes",
        "summary": "The retrieval add-on's status and a re-index request.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/retrieval.py",
                "module": "retrieval",
            },
            "setting": "ATLAS_RETRIEVAL=on",
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Retrieval routes\n"
                    "\n"
                    "Declared in `backend/routers/retrieval.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| POST | `/api/retrieval/reindex` | write (`admin` scope) | ATLAS_RETRIEVAL=on |\n"
                    "| GET | `/api/retrieval/status` | signed in | ATLAS_RETRIEVAL=on |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-search",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Search route",
        "summary": "Search over names, prose, documents, acceptance, results, comments, ADRs and labels; returns pointers.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/search.py",
                "module": "search",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Search route\n"
                    "\n"
                    "Declared in `backend/routers/search.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/search` | signed in |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-setup",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Setup guide routes",
        "summary": "The first-run setup guide: where it stands, recorded answers, and accepting the decisions they produce.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/setup.py",
                "module": "setup",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Setup guide routes\n"
                    "\n"
                    "Declared in `backend/routers/setup.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/setup` | signed in |  |\n"
                    "| POST | `/api/setup/adrs/{adr_id}/accept` | write (`admin` scope) |  |\n"
                    "| POST | `/api/setup/answers` | write (`admin` scope) |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-stats",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Personal statistics route",
        "summary": "The signed-in account's own work counts.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/stats.py",
                "module": "stats",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Personal statistics route\n"
                    "\n"
                    "Declared in `backend/routers/stats.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/me/stats` | signed in |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-api-workitems",
        "type": "component",
        "parent": "atlas-system-api",
        "name": "Workitem routes",
        "summary": "Workitems: create, update, list, acceptance proposals, and the vocabulary an install allows.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/routers/workitems.py",
                "module": "workitems",
            },
        },
        "docs": [
            {
                "file": "routes.md",
                "title": "Routes",
                "kind": "api",
                "content": (
                    "# Workitem routes\n"
                    "\n"
                    "Declared in `backend/routers/workitems.py`; generated from the live route table. Access: *public* needs no sign-in; *signed in* is any session or token; a write needs a session with its CSRF token, or a token with the scope named.\n"
                    "\n"
                    "| Method | Path | Access | Condition |\n"
                    "|---|---|---|---|\n"
                    "| GET | `/api/vocabulary` | signed in |  |\n"
                    "| POST | `/api/workitem` | write (`write` scope) |  |\n"
                    "| PATCH | `/api/workitem/{workitem_id}` | write (`write` scope) |  |\n"
                    "| GET | `/api/workitem/{workitem_id}/acceptance-proposal` | signed in |  |\n"
                    "| POST | `/api/workitem/{workitem_id}/acceptance-proposal` | write (`write` scope) |  |\n"
                    "| POST | `/api/workitem/{workitem_id}/acceptance-proposal/{proposal_id}/approve` | write (`admin` scope) |  |\n"
                    "| POST | `/api/workitem/{workitem_id}/acceptance-proposal/{proposal_id}/reject` | write (`admin` scope) |  |\n"
                    "| GET | `/api/workitems` | signed in |  |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-mcp-read",
        "type": "component",
        "parent": "atlas-system-mcp",
        "name": "Read tools",
        "summary": "The tools that read: health, outline, context, search, structure, nodes, trees, paths, intents, documents, ADRs and the vocabulary.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "group": "Read",
                "tools": [
                    "atlas_health",
                    "atlas_outline",
                    "atlas_context",
                    "atlas_node_search",
                    "atlas_search",
                    "atlas_structure",
                    "atlas_get",
                    "atlas_tree",
                    "atlas_path",
                    "atlas_intents",
                    "atlas_doc_read",
                    "atlas_adr_search",
                    "atlas_adr_get",
                    "atlas_capabilities",
                ],
            },
        },
        "docs": [
            {
                "file": "tools.md",
                "title": "Tools",
                "kind": "api",
                "content": (
                    "# Read tools\n"
                    "\n"
                    "Generated from the tools atlas-mcp registers (`mcp/app/descriptions.py`). The full reference is the MCP tools page (MCP-TOOLS.md).\n"
                    "\n"
                    "| Tool | What it is for |\n"
                    "|---|---|\n"
                    "| `atlas_health` | Is atlas-api up? |\n"
                    "| `atlas_outline` | The contains tree as indented text, one line per node: what exists. |\n"
                    "| `atlas_context` | Tell me about X, in one call: the best-matching nodes, their neighbours, what depends on them, the intents that apply, related decisions and docs to read next. |\n"
                    "| `atlas_node_search` | Which world node knows about X? |\n"
                    "| `atlas_search` | List and filter workitems (the types atlas_capabilities lists) by type, status, parent, binding, priority, label or q. |\n"
                    "| `atlas_structure` | Where a node sits, without prose: ancestors, children with their child counts, depends_on both ways, binding. |\n"
                    "| `atlas_get` | One node, complete: prose, acceptance, result, parents, bindings, dependencies and intents, own and inherited (the constraints on changing it). |\n"
                    "| `atlas_tree` | A bounded neighbourhood around one node (focus is required). |\n"
                    "| `atlas_path` | How two nodes connect: up to `max` shortest paths (default 3), each hop with its edge_type and direction. |\n"
                    "| `atlas_intents` | Intents where they are authored (own only). |\n"
                    "| `atlas_doc_read` | Read a node's document; `path` must be one of the node's attributes.docs[].path, which atlas_get lists. |\n"
                    "| `atlas_adr_search` | Find architecture decision records by q (words, an id like AT001, or a legacy number), project, status or number. |\n"
                    "| `atlas_adr_get` | One decision record by id (AT001), with status, date, source (a git file, or recorded in Atlas), links both ways and the markdown. |\n"
                    "| `atlas_capabilities` | This deployment's legal node types, edge types, statuses, workitem types and intent kinds, and this server's tool list. |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-health",
        "type": "component",
        "parent": "atlas-system-mcp-read",
        "name": "atlas_health",
        "summary": "Is atlas-api up?",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_health",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_health`\n"
                    "\n"
                    "Is atlas-api up? Returns node and edge counts as a plausibility check.\n"
                    "\n"
                    "No parameters.\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/health` (`atlas-system-api-health`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-outline",
        "type": "component",
        "parent": "atlas-system-mcp-read",
        "name": "atlas_outline",
        "summary": "The contains tree as indented text, one line per node: what exists.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_outline",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_outline`\n"
                    "\n"
                    "The contains tree as indented text, one line per node: what exists. Defaults to the world (families=object,bridge); scope with root=, add names=true. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `families` | string \\| null | no | `null` |\n"
                    "| `max_depth` | integer \\| null | no | `null` |\n"
                    "| `names` | boolean \\| null | no | `null` |\n"
                    "| `root` | string \\| null | no | `null` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/graph/outline` (`atlas-system-api-graph`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-context",
        "type": "component",
        "parent": "atlas-system-mcp-read",
        "name": "atlas_context",
        "summary": "Tell me about X, in one call: the best-matching nodes, their neighbours, what depends on them, the intents that apply, related decisions and docs to read next.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_context",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_context`\n"
                    "\n"
                    "Tell me about X, in one call: the best-matching nodes, their neighbours, what depends on them, the intents that apply, related decisions and docs to read next. Pointers within budgets; each part says when it was cut. Ask in your own words, Swedish works. Start here for an open question. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `about` | string | yes |  |\n"
                    "| `decisions` | integer \\| null | no | `null` |\n"
                    "| `docs` | integer \\| null | no | `null` |\n"
                    "| `families` | string \\| null | no | `null` |\n"
                    "| `impact` | integer \\| null | no | `null` |\n"
                    "| `impact_depth` | integer \\| null | no | `null` |\n"
                    "| `intents` | integer \\| null | no | `null` |\n"
                    "| `intents_full` | boolean \\| null | no | `null` |\n"
                    "| `mode` | string \\| null | no | `null` |\n"
                    "| `neighbours` | integer \\| null | no | `null` |\n"
                    "| `seeds` | integer \\| null | no | `null` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/context` (`atlas-system-api-context`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-node-search",
        "type": "component",
        "parent": "atlas-system-mcp-read",
        "name": "atlas_node_search",
        "summary": "Which world node knows about X?",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_node_search",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_node_search`\n"
                    "\n"
                    "Which world node knows about X? Semantic + full-text search (mode=\"hybrid\", the default) over names, prose and served docs: ask in your own words or in Swedish, or give an exact name. Returns pointers (id, summary, snippet), not content. mode=\"fulltext\" for word matching only; expand=true adds graph neighbours. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `expand` | boolean \\| null | no | `null` |\n"
                    "| `families` | string \\| null | no | `null` |\n"
                    "| `mode` | string \\| null | no | `null` |\n"
                    "| `page` | integer \\| null | no | `null` |\n"
                    "| `page_size` | integer \\| null | no | `null` |\n"
                    "| `q` | string | yes |  |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/search` (`atlas-system-api-search`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-search",
        "type": "component",
        "parent": "atlas-system-mcp-read",
        "name": "atlas_search",
        "summary": "List and filter workitems (the types atlas_capabilities lists) by type, status, parent, binding, priority, label or q.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_search",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_search`\n"
                    "\n"
                    "List and filter workitems (the types atlas_capabilities lists) by type, status, parent, binding, priority, label or q. A listing without acceptance or bindings; page until you have `total`. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `binding` | string \\| null | no | `null` |\n"
                    "| `label` | string \\| null | no | `null` |\n"
                    "| `owner` | string \\| null | no | `null` |\n"
                    "| `page` | integer \\| null | no | `null` |\n"
                    "| `page_size` | integer \\| null | no | `null` |\n"
                    "| `parent` | string \\| null | no | `null` |\n"
                    "| `priority` | string \\| null | no | `null` |\n"
                    "| `q` | string \\| null | no | `null` |\n"
                    "| `status` | string \\| null | no | `null` |\n"
                    "| `type` | string \\| null | no | `null` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/workitems` (`atlas-system-api-workitems`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-structure",
        "type": "component",
        "parent": "atlas-system-mcp-read",
        "name": "atlas_structure",
        "summary": "Where a node sits, without prose: ancestors, children with their child counts, depends_on both ways, binding.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_structure",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_structure`\n"
                    "\n"
                    "Where a node sits, without prose: ancestors, children with their child counts, depends_on both ways, binding. Read it before atlas_get. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `children_limit` | integer \\| null | no | `null` |\n"
                    "| `node_id` | string | yes |  |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/node/{node_id}/structure` (`atlas-system-api-nodes`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-get",
        "type": "component",
        "parent": "atlas-system-mcp-read",
        "name": "atlas_get",
        "summary": "One node, complete: prose, acceptance, result, parents, bindings, dependencies and intents, own and inherited (the constraints on changing it).",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_get",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_get`\n"
                    "\n"
                    "One node, complete: prose, acceptance, result, parents, bindings, dependencies and intents, own and inherited (the constraints on changing it). To only orient, ask for less: prose=none|summary|abstract, intents=own|none. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `id` | string | yes |  |\n"
                    "| `intents` | string \\| null | no | `null` |\n"
                    "| `prose` | string \\| null | no | `null` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/node/{node_id}` (`atlas-system-api-nodes`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-tree",
        "type": "component",
        "parent": "atlas-system-mcp-read",
        "name": "atlas_tree",
        "summary": "A bounded neighbourhood around one node (focus is required).",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_tree",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_tree`\n"
                    "\n"
                    "A bounded neighbourhood around one node (focus is required). Minimal by default: depth=1, edges=contains, prose=none, work=none. Raise one at a time. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `depth` | integer \\| null | no | `null` |\n"
                    "| `edges` | string \\| null | no | `null` |\n"
                    "| `focus` | string \\| null | no | `null` |\n"
                    "| `meta` | boolean \\| null | no | `null` |\n"
                    "| `prose` | string \\| null | no | `null` |\n"
                    "| `work` | string \\| null | no | `null` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/graph` (`atlas-system-api-graph`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-path",
        "type": "component",
        "parent": "atlas-system-mcp-read",
        "name": "atlas_path",
        "summary": "How two nodes connect: up to `max` shortest paths (default 3), each hop with its edge_type and direction.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_path",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_path`\n"
                    "\n"
                    "How two nodes connect: up to `max` shortest paths (default 3), each hop with its edge_type and direction. `edges` limits which edge types are walked. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `edges` | string \\| null | no | `null` |\n"
                    "| `from_id` | string | yes |  |\n"
                    "| `hops` | integer \\| null | no | `null` |\n"
                    "| `max` | integer \\| null | no | `null` |\n"
                    "| `to_id` | string | yes |  |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/graph/path` (`atlas-system-api-graph`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-intents",
        "type": "component",
        "parent": "atlas-system-mcp-read",
        "name": "atlas_intents",
        "summary": "Intents where they are authored (own only).",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_intents",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_intents`\n"
                    "\n"
                    "Intents where they are authored (own only). A node's inherited intents come from atlas_get. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `kind` | string \\| null | no | `null` |\n"
                    "| `node_id` | string \\| null | no | `null` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/intents` (`atlas-system-api-intents`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-doc-read",
        "type": "component",
        "parent": "atlas-system-mcp-read",
        "name": "atlas_doc_read",
        "summary": "Read a node's document; `path` must be one of the node's attributes.docs[].path, which atlas_get lists.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_doc_read",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_doc_read`\n"
                    "\n"
                    "Read a node's document; `path` must be one of the node's attributes.docs[].path, which atlas_get lists. The tools guide, every tool in detail: node atlas-mcp, path docs/nodes/atlas-mcp/tools.md.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `node_id` | string | yes |  |\n"
                    "| `path` | string | yes |  |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/node/{node_id}/doc` (`atlas-system-api-docs`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-adr-search",
        "type": "component",
        "parent": "atlas-system-mcp-read",
        "name": "atlas_adr_search",
        "summary": "Find architecture decision records by q (words, an id like AT001, or a legacy number), project, status or number.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_adr_search",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_adr_search`\n"
                    "\n"
                    "Find architecture decision records by q (words, an id like AT001, or a legacy number), project, status or number. Pointers with id, project and source (git or atlas), not the text. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `number` | integer \\| null | no | `null` |\n"
                    "| `project` | string \\| null | no | `null` |\n"
                    "| `q` | string \\| null | no | `null` |\n"
                    "| `status` | string \\| null | no | `null` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/adrs` (`atlas-system-api-adrs`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-adr-get",
        "type": "component",
        "parent": "atlas-system-mcp-read",
        "name": "atlas_adr_get",
        "summary": "One decision record by id (AT001), with status, date, source (a git file, or recorded in Atlas), links both ways and the markdown.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_adr_get",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_adr_get`\n"
                    "\n"
                    "One decision record by id (AT001), with status, date, source (a git file, or recorded in Atlas), links both ways and the markdown. A legacy number held by two projects is refused with the candidates; add project=. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `id` | string | yes |  |\n"
                    "| `project` | string \\| null | no | `null` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/adrs/{ref}` (`atlas-system-api-adrs`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-capabilities",
        "type": "component",
        "parent": "atlas-system-mcp-read",
        "name": "atlas_capabilities",
        "summary": "This deployment's legal node types, edge types, statuses, workitem types and intent kinds, and this server's tool list.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_capabilities",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_capabilities`\n"
                    "\n"
                    "This deployment's legal node types, edge types, statuses, workitem types and intent kinds, and this server's tool list. About 25 KB. More: tools guide.\n"
                    "\n"
                    "No parameters.\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/vocabulary` (`atlas-system-api-workitems`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-mcp-work",
        "type": "component",
        "parent": "atlas-system-mcp",
        "name": "Work tools",
        "summary": "File a workitem and update one.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "group": "Work",
                "tools": [
                    "atlas_file_work",
                    "atlas_update_work",
                ],
            },
        },
        "docs": [
            {
                "file": "tools.md",
                "title": "Tools",
                "kind": "api",
                "content": (
                    "# Work tools\n"
                    "\n"
                    "Generated from the tools atlas-mcp registers (`mcp/app/descriptions.py`). The full reference is the MCP tools page (MCP-TOOLS.md).\n"
                    "\n"
                    "| Tool | What it is for |\n"
                    "|---|---|\n"
                    "| `atlas_file_work` | Create a workitem (a type atlas_capabilities lists) with its acceptance, parent, binding, depends_on, priority (low\\|medium\\|high\\|critical) and labels in one transaction. |\n"
                    "| `atlas_update_work` | Update a workitem. |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-file-work",
        "type": "component",
        "parent": "atlas-system-mcp-work",
        "name": "atlas_file_work",
        "summary": "Create a workitem (a type atlas_capabilities lists) with its acceptance, parent, binding, depends_on, priority (low|medium|high|critical) and labels in one transaction.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_file_work",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_file_work`\n"
                    "\n"
                    "Create a workitem (a type atlas_capabilities lists) with its acceptance, parent, binding, depends_on, priority (low|medium|high|critical) and labels in one transaction. A task's id is minted; an epic or story needs a slug id. `body` is the spec, never a result. Suggest a binding, do not guess one. Decisions are raised with a dedicated tool, not here. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `acceptance` | array of string \\| null | no | `null` |\n"
                    "| `binding` | string \\| null | no | `null` |\n"
                    "| `body` | string \\| null | no | `null` |\n"
                    "| `depends_on` | array of string \\| null | no | `null` |\n"
                    "| `id` | string \\| null | no | `null` |\n"
                    "| `labels` | array of string \\| null | no | `null` |\n"
                    "| `name` | string | yes |  |\n"
                    "| `parent` | string \\| null | no | `null` |\n"
                    "| `priority` | string \\| null | no | `null` |\n"
                    "| `status` | string | no | `\"pending\"` |\n"
                    "| `type` | string | yes |  |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/vocabulary` (`atlas-system-api-workitems`)\n"
                    "- POST `/api/workitem` (`atlas-system-api-workitems`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-update-work",
        "type": "component",
        "parent": "atlas-system-mcp-work",
        "name": "atlas_update_work",
        "summary": "Update a workitem.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_update_work",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_update_work`\n"
                    "\n"
                    "Update a workitem. status, result, priority, labels and parent (moves it) are plain (null clears, omitted leaves). body, acceptance, depends_on and binding REPLACE WHOLESALE and need set_\\<field>=True. Returns the prior values. Acceptance needs a person: propose_acceptance=True (approved on the board), or approval={by,quote} where the install allows attested writes. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `acceptance` | array of string \\| null | no | `null` |\n"
                    "| `approval` | object \\| null | no | `null` |\n"
                    "| `binding` | string \\| null | no | `null` |\n"
                    "| `body` | string \\| null | no | `null` |\n"
                    "| `depends_on` | array of string \\| null | no | `null` |\n"
                    "| `id` | string | yes |  |\n"
                    "| `labels` | array of string \\| null | no | *omitted* |\n"
                    "| `parent` | string \\| null | no | *omitted* |\n"
                    "| `priority` | string \\| null | no | *omitted* |\n"
                    "| `proposal_note` | string \\| null | no | `null` |\n"
                    "| `propose_acceptance` | boolean | no | `false` |\n"
                    "| `result` | string \\| null | no | *omitted* |\n"
                    "| `set_acceptance` | boolean | no | `false` |\n"
                    "| `set_binding` | boolean | no | `false` |\n"
                    "| `set_body` | boolean | no | `false` |\n"
                    "| `set_depends_on` | boolean | no | `false` |\n"
                    "| `status` | string \\| null | no | `null` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/node/{node_id}` (`atlas-system-api-nodes`)\n"
                    "- GET `/api/vocabulary` (`atlas-system-api-workitems`)\n"
                    "- PATCH `/api/workitem/{workitem_id}` (`atlas-system-api-workitems`)\n"
                    "- POST `/api/workitem/{workitem_id}/acceptance-proposal` (`atlas-system-api-workitems`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-mcp-graph-write",
        "type": "component",
        "parent": "atlas-system-mcp",
        "name": "Graph-write tools",
        "summary": "Write nodes, edges, documents and intents, and the permanent delete.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "group": "Graph writes",
                "tools": [
                    "atlas_node_write",
                    "atlas_edge_write",
                    "atlas_edge_delete",
                    "atlas_doc_write",
                    "atlas_intent_write",
                    "atlas_intent_delete",
                    "atlas_delete",
                ],
            },
        },
        "docs": [
            {
                "file": "tools.md",
                "title": "Tools",
                "kind": "api",
                "content": (
                    "# Graph-write tools\n"
                    "\n"
                    "Generated from the tools atlas-mcp registers (`mcp/app/descriptions.py`). The full reference is the MCP tools page (MCP-TOOLS.md).\n"
                    "\n"
                    "| Tool | What it is for |\n"
                    "|---|---|\n"
                    "| `atlas_node_write` | Create a node (needs type and name) or patch the fields given. |\n"
                    "| `atlas_edge_write` | Create one edge between two existing nodes. |\n"
                    "| `atlas_edge_delete` | Delete one edge by id. |\n"
                    "| `atlas_doc_write` | Write a node document (content=) and/or publish it (publish=True); pass both to publish safely. |\n"
                    "| `atlas_intent_write` | Create an intent (node_id, slug, kind, title, statement) or patch one (intent_id). |\n"
                    "| `atlas_intent_delete` | Delete one node intent by its integer id. |\n"
                    "| `atlas_delete` | PERMANENT delete of a node, edge, doc or intent: no undo. |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-node-write",
        "type": "component",
        "parent": "atlas-system-mcp-graph-write",
        "name": "atlas_node_write",
        "summary": "Create a node (needs type and name) or patch the fields given.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_node_write",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_node_write`\n"
                    "\n"
                    "Create a node (needs type and name) or patch the fields given. `attributes` merge by default; attributes_mode=\"replace\" overwrites them. key= sets a project's task-id prefix. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `abstract` | string \\| null | no | `null` |\n"
                    "| `attributes` | object \\| null | no | `null` |\n"
                    "| `attributes_mode` | string | no | `\"merge\"` |\n"
                    "| `detail` | string \\| null | no | `null` |\n"
                    "| `id` | string | yes |  |\n"
                    "| `key` | string \\| null | no | *omitted* |\n"
                    "| `name` | string \\| null | no | `null` |\n"
                    "| `owner` | string \\| null | no | `null` |\n"
                    "| `source` | string \\| null | no | `null` |\n"
                    "| `summary` | string \\| null | no | `null` |\n"
                    "| `type` | string \\| null | no | `null` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- POST `/api/node` (`atlas-system-api-nodes`)\n"
                    "- GET `/api/node/{node_id}` (`atlas-system-api-nodes`)\n"
                    "- PATCH `/api/node/{node_id}` (`atlas-system-api-nodes`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-edge-write",
        "type": "component",
        "parent": "atlas-system-mcp-graph-write",
        "name": "atlas_edge_write",
        "summary": "Create one edge between two existing nodes.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_edge_write",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_edge_write`\n"
                    "\n"
                    "Create one edge between two existing nodes. Not for nesting workitems: that is atlas_file_work. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `directed` | boolean | no | `true` |\n"
                    "| `dst` | string | yes |  |\n"
                    "| `edge_type` | string | yes |  |\n"
                    "| `src` | string | yes |  |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- POST `/api/edge` (`atlas-system-api-edges`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-edge-delete",
        "type": "component",
        "parent": "atlas-system-mcp-graph-write",
        "name": "atlas_edge_delete",
        "summary": "Delete one edge by id.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_edge_delete",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_edge_delete`\n"
                    "\n"
                    "Delete one edge by id. Nothing else changes.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `edge_id` | integer | yes |  |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- DELETE `/api/edge/{edge_id}` (`atlas-system-api-edges`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-doc-write",
        "type": "component",
        "parent": "atlas-system-mcp-graph-write",
        "name": "atlas_doc_write",
        "summary": "Write a node document (content=) and/or publish it (publish=True); pass both to publish safely.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_doc_write",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_doc_write`\n"
                    "\n"
                    "Write a node document (content=) and/or publish it (publish=True); pass both to publish safely. `path` starts with docs/nodes/. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `content` | string \\| null | no | `null` |\n"
                    "| `node_id` | string | yes |  |\n"
                    "| `path` | string | yes |  |\n"
                    "| `publish` | boolean | no | `false` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/node/{node_id}` (`atlas-system-api-nodes`)\n"
                    "- PATCH `/api/node/{node_id}` (`atlas-system-api-nodes`)\n"
                    "- PUT `/api/node/{node_id}/doc` (`atlas-system-api-docs`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-intent-write",
        "type": "component",
        "parent": "atlas-system-mcp-graph-write",
        "name": "atlas_intent_write",
        "summary": "Create an intent (node_id, slug, kind, title, statement) or patch one (intent_id).",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_intent_write",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_intent_write`\n"
                    "\n"
                    "Create an intent (node_id, slug, kind, title, statement) or patch one (intent_id). An intent is still true when all open work is done; if not, file a workitem. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `intent_id` | integer \\| null | no | `null` |\n"
                    "| `kind` | string \\| null | no | `null` |\n"
                    "| `node_id` | string \\| null | no | `null` |\n"
                    "| `position` | integer \\| null | no | `null` |\n"
                    "| `slug` | string \\| null | no | `null` |\n"
                    "| `source` | string \\| null | no | `null` |\n"
                    "| `statement` | string \\| null | no | `null` |\n"
                    "| `title` | string \\| null | no | `null` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- PATCH `/api/intent/{intent_id}` (`atlas-system-api-intents`)\n"
                    "- POST `/api/node/{node_id}/intent` (`atlas-system-api-intents`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-intent-delete",
        "type": "component",
        "parent": "atlas-system-mcp-graph-write",
        "name": "atlas_intent_delete",
        "summary": "Delete one node intent by its integer id.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_intent_delete",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_intent_delete`\n"
                    "\n"
                    "Delete one node intent by its integer id.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `intent_id` | integer | yes |  |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- DELETE `/api/intent/{intent_id}` (`atlas-system-api-intents`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-delete",
        "type": "component",
        "parent": "atlas-system-mcp-graph-write",
        "name": "atlas_delete",
        "summary": "PERMANENT delete of a node, edge, doc or intent: no undo.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_delete",
            },
            "setting": "ATLAS_MCP_ENABLE_DELETE=1",
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_delete`\n"
                    "\n"
                    "> Offered only when `ATLAS_MCP_ENABLE_DELETE` is set on atlas-mcp. Off by default; the caller also needs the `admin` scope.\n"
                    "\n"
                    "PERMANENT delete of a node, edge, doc or intent: no undo. A node delete cascades and needs expected_edge_count; call once without it and the refusal gives it. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `edge_id` | integer \\| null | no | `null` |\n"
                    "| `expected_edge_count` | integer \\| null | no | `null` |\n"
                    "| `intent_id` | integer \\| null | no | `null` |\n"
                    "| `kind` | string | yes |  |\n"
                    "| `node_id` | string \\| null | no | `null` |\n"
                    "| `path` | string \\| null | no | `null` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- DELETE `/api/edge/{edge_id}` (`atlas-system-api-edges`)\n"
                    "- GET `/api/graph` (`atlas-system-api-graph`)\n"
                    "- DELETE `/api/intent/{intent_id}` (`atlas-system-api-intents`)\n"
                    "- GET `/api/node/{node_id}` (`atlas-system-api-nodes`)\n"
                    "- PATCH `/api/node/{node_id}` (`atlas-system-api-nodes`)\n"
                    "- DELETE `/api/node/{node_id}` (`atlas-system-api-nodes`)\n"
                    "- DELETE `/api/node/{node_id}/doc` (`atlas-system-api-docs`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-mcp-comments",
        "type": "component",
        "parent": "atlas-system-mcp",
        "name": "Comment tools",
        "summary": "Read and write comments on a workitem.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "group": "Comments",
                "tools": [
                    "atlas_comments",
                    "atlas_comment_write",
                ],
            },
        },
        "docs": [
            {
                "file": "tools.md",
                "title": "Tools",
                "kind": "api",
                "content": (
                    "# Comment tools\n"
                    "\n"
                    "Generated from the tools atlas-mcp registers (`mcp/app/descriptions.py`). The full reference is the MCP tools page (MCP-TOOLS.md).\n"
                    "\n"
                    "| Tool | What it is for |\n"
                    "|---|---|\n"
                    "| `atlas_comments` | The comments on one workitem, oldest first, each with its author and id. |\n"
                    "| `atlas_comment_write` | Add a comment to a workitem (id, body; markdown), or edit one of your own (comment_id, body). |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-comments",
        "type": "component",
        "parent": "atlas-system-mcp-comments",
        "name": "atlas_comments",
        "summary": "The comments on one workitem, oldest first, each with its author and id.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_comments",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_comments`\n"
                    "\n"
                    "The comments on one workitem, oldest first, each with its author and id.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `id` | string | yes |  |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/workitem/{workitem_id}/comments` (`atlas-system-api-comments`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-comment-write",
        "type": "component",
        "parent": "atlas-system-mcp-comments",
        "name": "atlas_comment_write",
        "summary": "Add a comment to a workitem (id, body; markdown), or edit one of your own (comment_id, body).",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_comment_write",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_comment_write`\n"
                    "\n"
                    "Add a comment to a workitem (id, body; markdown), or edit one of your own (comment_id, body). The comment names you as the author. Deleting is done on the board. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `body` | string | yes |  |\n"
                    "| `comment_id` | integer \\| null | no | `null` |\n"
                    "| `id` | string \\| null | no | `null` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- PATCH `/api/comment/{comment_id}` (`atlas-system-api-comments`)\n"
                    "- POST `/api/workitem/{workitem_id}/comments` (`atlas-system-api-comments`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-mcp-setup",
        "type": "component",
        "parent": "atlas-system-mcp",
        "name": "Setup-guide tool",
        "summary": "The first-run setup guide's interview.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "group": "The first-run setup guide",
                "tools": [
                    "atlas_setup_guide",
                ],
            },
        },
        "docs": [
            {
                "file": "tools.md",
                "title": "Tools",
                "kind": "api",
                "content": (
                    "# Setup-guide tool\n"
                    "\n"
                    "Generated from the tools atlas-mcp registers (`mcp/app/descriptions.py`). The full reference is the MCP tools page (MCP-TOOLS.md).\n"
                    "\n"
                    "| Tool | What it is for |\n"
                    "|---|---|\n"
                    "| `atlas_setup_guide` | Setup guide: no answers gives the next topic; topic+answers records a Proposed ADR. |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-setup-guide",
        "type": "component",
        "parent": "atlas-system-mcp-setup",
        "name": "atlas_setup_guide",
        "summary": "Setup guide: no answers gives the next topic; topic+answers records a Proposed ADR.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_setup_guide",
            },
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_setup_guide`\n"
                    "\n"
                    "Setup guide: no answers gives the next topic; topic+answers records a Proposed ADR. Ask the person; never answer for them. More: tools guide.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `answers` | object \\| null | no | `null` |\n"
                    "| `prefix` | string \\| null | no | `null` |\n"
                    "| `project` | string \\| null | no | `null` |\n"
                    "| `topic` | string \\| null | no | `null` |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/setup` (`atlas-system-api-setup`)\n"
                    "- POST `/api/setup/answers` (`atlas-system-api-setup`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-mcp-session",
        "type": "component",
        "parent": "atlas-system-mcp",
        "name": "Session tool",
        "summary": "Sign-off: an agent's own writes in a session and the procedure for recording them.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "group": "Session",
                "tools": [
                    "atlas_save_work",
                ],
            },
        },
        "docs": [
            {
                "file": "tools.md",
                "title": "Tools",
                "kind": "api",
                "content": (
                    "# Session tool\n"
                    "\n"
                    "Generated from the tools atlas-mcp registers (`mcp/app/descriptions.py`). The full reference is the MCP tools page (MCP-TOOLS.md).\n"
                    "\n"
                    "| Tool | What it is for |\n"
                    "|---|---|\n"
                    "| `atlas_save_work` | Sign-off: your writes since `since`, and the procedure for recording this conversation's work in Atlas. |\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-tool-save-work",
        "type": "component",
        "parent": "atlas-system-mcp-session",
        "name": "atlas_save_work",
        "summary": "Sign-off: your writes since `since`, and the procedure for recording this conversation's work in Atlas.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "mcp/app/descriptions.py",
                "tool": "atlas_save_work",
            },
            "setting": "ATLAS_DISABLED_FEATURES (save_work)",
        },
        "docs": [
            {
                "file": "reference.md",
                "title": "Reference",
                "kind": "api",
                "content": (
                    "# `atlas_save_work`\n"
                    "\n"
                    "> Offered unless the `save_work` feature is listed in `ATLAS_DISABLED_FEATURES`.\n"
                    "\n"
                    "Sign-off: your writes since `since`, and the procedure for recording this conversation's work in Atlas.\n"
                    "\n"
                    "| Parameter | Type | Required | Default |\n"
                    "|---|---|---|---|\n"
                    "| `page` | integer \\| null | no | `null` |\n"
                    "| `page_size` | integer \\| null | no | `null` |\n"
                    "| `since` | string | yes |  |\n"
                    "\n"
                    "## Routes it calls\n"
                    "\n"
                    "- GET `/api/events` (`atlas-system-api-events`)\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-board",
        "type": "component",
        "parent": "atlas-system",
        "name": "Board",
        "summary": "The browser board at /board: a radial graph of the world, a work board scoped by it, and a node panel.",
        "abstract": "The board is a static page with no build step that reads and writes only through /api/*. The graph shows what exists; selecting a node scopes the work board to the work bound there and below; the node panel shows one node's overview, intents, comments, history, documents, links, decisions and configuration.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-access",
        "type": "component",
        "parent": "atlas-system-board",
        "name": "Access administration",
        "summary": "Visibility marks, groups and project roles, for an owner.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/access/",
                "view": "access",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-dashboard",
        "type": "component",
        "parent": "atlas-system-board",
        "name": "Dashboard",
        "summary": "The signed-in person's own view: boxes of what needs attention and their tasks.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/dashboard/",
                "view": "dashboard",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-graph",
        "type": "component",
        "parent": "atlas-system-board",
        "name": "Graph view",
        "summary": "The radial graph of the world: nodes by family and type, contains as the spokes.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/graph/",
                "view": "graph",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-panel",
        "type": "component",
        "parent": "atlas-system-board",
        "name": "Node panel",
        "summary": "One node in full, one tab per aspect.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/panel/",
                "view": "panel",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-panel-overview",
        "type": "component",
        "parent": "atlas-system-board-panel",
        "name": "Overview tab",
        "summary": "The node's prose, type and place in the graph.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/panel/panel.js",
                "tab": "overview",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-panel-intent",
        "type": "component",
        "parent": "atlas-system-board-panel",
        "name": "Intent tab",
        "summary": "The intents that apply to the node, its own and inherited.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/panel/panel.js",
                "tab": "intent",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-panel-comments",
        "type": "component",
        "parent": "atlas-system-board-panel",
        "name": "Comments tab",
        "summary": "The comments on a workitem.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/panel/panel.js",
                "tab": "comments",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-panel-history",
        "type": "component",
        "parent": "atlas-system-board-panel",
        "name": "History tab",
        "summary": "The graph events recorded on the node.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/panel/panel.js",
                "tab": "history",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-panel-docs",
        "type": "component",
        "parent": "atlas-system-board-panel",
        "name": "Docs tab",
        "summary": "The node's documents.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/panel/panel.js",
                "tab": "docs",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-panel-links",
        "type": "component",
        "parent": "atlas-system-board-panel",
        "name": "Links tab",
        "summary": "The node's edges other than contains.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/panel/panel.js",
                "tab": "links",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-panel-decisions",
        "type": "component",
        "parent": "atlas-system-board-panel",
        "name": "Decisions tab",
        "summary": "The ADRs recorded on the node's project.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/panel/panel.js",
                "tab": "decisions",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-panel-config",
        "type": "component",
        "parent": "atlas-system-board-panel",
        "name": "Config tab",
        "summary": "The settings of a system part, with their effective values.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/panel/panel.js",
                "tab": "config",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-search",
        "type": "component",
        "parent": "atlas-system-board",
        "name": "Search box",
        "summary": "Search from the board, over the same sources as /api/search.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/search.js",
                "view": "search",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-settings",
        "type": "component",
        "parent": "atlas-system-board",
        "name": "Settings",
        "summary": "Personal settings, agent credentials, crunch week and the password notice.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/settings/",
                "view": "settings",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-work",
        "type": "component",
        "parent": "atlas-system-board",
        "name": "Work board",
        "summary": "The workitems bound to the selected node and everything it contains, by status.",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "frontend/v2/views/work/",
                "view": "work",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-board-legacy",
        "type": "component",
        "parent": "atlas-system-board",
        "name": "Legacy board",
        "summary": "The first board, frozen, at /board/legacy: world tree, work tree and Mine.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-cli",
        "type": "component",
        "parent": "atlas-system",
        "name": "Admin CLI",
        "summary": "atlas-admin, the operator's command line inside the atlas-api image: accounts, tokens, guardrails, backups.",
        "abstract": "atlas-admin runs inside the atlas-api container (`docker compose exec atlas-api atlas-admin ...`) and writes the database directly. It never accepts a credential as an argument: the system generates every secret and shows it once.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-cli-user",
        "type": "component",
        "parent": "atlas-system-cli",
        "name": "atlas-admin user",
        "summary": "Human accounts",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/admin.py",
                "command": "user",
            },
        },
        "docs": [
            {
                "file": "commands.md",
                "title": "Commands",
                "kind": "runbook",
                "content": (
                    "# atlas-admin user\n"
                    "\n"
                    "human accounts.\n"
                    "\n"
                    "| Command | What it does |\n"
                    "|---|---|\n"
                    "| `atlas-admin user add` | create an account and print a generated temp password once (or --out it) |\n"
                    "| `atlas-admin user link-idp` | point an existing local person at an IdP identity, keeping their id, history and agents; the username is kept as their local sign-in name |\n"
                    "| `atlas-admin user set-local-name` | give an IdP-backed account the local sign-in name its personal password signs in with when the IdP is down, or --clear it |\n"
                    "| `atlas-admin user reset-password` | generate a new temp password, delete that user's sessions, leave service tokens |\n"
                    "| `atlas-admin user disable` | set disabled_at, delete sessions, refuse that user's tokens AND disable every agent linked to them |\n"
                    "| `atlas-admin user enable` | clear disabled_at (restores no password). NOT symmetric with `disable`: a person's agents are NOT re-enabled with them, because one may have been taken down for its own reasons. Re-enable each agent deliberately. |\n"
                    "| `atlas-admin user list` | every account |\n"
                    "| `atlas-admin user show` | one account; never prints a secret |\n"
                    "\n"
                    "Every option: `atlas-admin user --help`, inside the atlas-api container.\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-cli-session",
        "type": "component",
        "parent": "atlas-system-cli",
        "name": "atlas-admin session",
        "summary": "Browser sessions (staging only; see ATLAS_ALLOW_SESSION_MINT)",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/admin.py",
                "command": "session",
            },
        },
        "docs": [
            {
                "file": "commands.md",
                "title": "Commands",
                "kind": "runbook",
                "content": (
                    "# atlas-admin session\n"
                    "\n"
                    "browser sessions (staging only; see ATLAS_ALLOW_SESSION_MINT).\n"
                    "\n"
                    "| Command | What it does |\n"
                    "|---|---|\n"
                    "| `atlas-admin session mint` | mint a real browser session for an account, with no password involved |\n"
                    "\n"
                    "Every option: `atlas-admin session --help`, inside the atlas-api container.\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-cli-bot",
        "type": "component",
        "parent": "atlas-system-cli",
        "name": "atlas-admin bot",
        "summary": "Machine principals",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/admin.py",
                "command": "bot",
            },
        },
        "docs": [
            {
                "file": "commands.md",
                "title": "Commands",
                "kind": "runbook",
                "content": (
                    "# atlas-admin bot\n"
                    "\n"
                    "machine principals.\n"
                    "\n"
                    "| Command | What it does |\n"
                    "|---|---|\n"
                    "| `atlas-admin bot add` | an is_service account with no usable password |\n"
                    "| `atlas-admin bot link` | record the person an agent belongs to |\n"
                    "| `atlas-admin bot unlink` | clear the person an agent belongs to |\n"
                    "| `atlas-admin bot list` | agent accounts and who is accountable for each |\n"
                    "\n"
                    "Every option: `atlas-admin bot --help`, inside the atlas-api container.\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-cli-token",
        "type": "component",
        "parent": "atlas-system-cli",
        "name": "atlas-admin token",
        "summary": "Atlas_svc_ service tokens",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/admin.py",
                "command": "token",
            },
        },
        "docs": [
            {
                "file": "commands.md",
                "title": "Commands",
                "kind": "runbook",
                "content": (
                    "# atlas-admin token\n"
                    "\n"
                    "atlas_svc_ service tokens.\n"
                    "\n"
                    "| Command | What it does |\n"
                    "|---|---|\n"
                    "| `atlas-admin token add` | mint one; the raw value is shown once |\n"
                    "| `atlas-admin token mint` | mint an agent token for a PERSON; written only to --out, never printed |\n"
                    "| `atlas-admin token list` | live tokens; never prints a digest |\n"
                    "| `atlas-admin token revoke` | permanent, effective next request |\n"
                    "\n"
                    "Every option: `atlas-admin token --help`, inside the atlas-api container.\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-cli-rbac",
        "type": "component",
        "parent": "atlas-system-cli",
        "name": "atlas-admin rbac",
        "summary": "Project RBAC (ADR 0041); read-only planning",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/admin.py",
                "command": "rbac",
            },
        },
        "docs": [
            {
                "file": "commands.md",
                "title": "Commands",
                "kind": "runbook",
                "content": (
                    "# atlas-admin rbac\n"
                    "\n"
                    "project RBAC (ADR 0041); read-only planning.\n"
                    "\n"
                    "| Command | What it does |\n"
                    "|---|---|\n"
                    "| `atlas-admin rbac plan` | today's vs ATLAS_RBAC=projects effective permission per account and project; writes nothing |\n"
                    "\n"
                    "Every option: `atlas-admin rbac --help`, inside the atlas-api container.\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-cli-audit",
        "type": "component",
        "parent": "atlas-system-cli",
        "name": "atlas-admin audit",
        "summary": "The read audit (audit_read)",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/admin.py",
                "command": "audit",
            },
        },
        "docs": [
            {
                "file": "commands.md",
                "title": "Commands",
                "kind": "runbook",
                "content": (
                    "# atlas-admin audit\n"
                    "\n"
                    "the read audit (audit_read).\n"
                    "\n"
                    "| Command | What it does |\n"
                    "|---|---|\n"
                    "| `atlas-admin audit purge` | delete audit_read rows older than ATLAS_AUDIT_RETENTION_DAYS (unset: keep forever, delete nothing); run it from cron |\n"
                    "\n"
                    "Every option: `atlas-admin audit --help`, inside the atlas-api container.\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-cli-guardrails",
        "type": "component",
        "parent": "atlas-system-cli",
        "name": "atlas-admin guardrails",
        "summary": "Agent guardrails per environment, as data (guardrails.py)",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/admin.py",
                "command": "guardrails",
            },
        },
        "docs": [
            {
                "file": "commands.md",
                "title": "Commands",
                "kind": "runbook",
                "content": (
                    "# atlas-admin guardrails\n"
                    "\n"
                    "agent guardrails per environment, as data (guardrails.py).\n"
                    "\n"
                    "| Command | What it does |\n"
                    "|---|---|\n"
                    "| `atlas-admin guardrails validate` | check a matrix file; writes nothing |\n"
                    "| `atlas-admin guardrails apply` | store the matrix on atlas-system: its document, one constraint intent per environment and a Proposed ADR (idempotent) |\n"
                    "| `atlas-admin guardrails show` | print the applied matrix (normalised JSON) |\n"
                    "| `atlas-admin guardrails export` | agent policy files from the matrix |\n"
                    "\n"
                    "Every option: `atlas-admin guardrails --help`, inside the atlas-api container.\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-cli-cicd",
        "type": "component",
        "parent": "atlas-system-cli",
        "name": "atlas-admin cicd",
        "summary": "A first CI/CD pipeline from the guardrails matrix (cicd.py); writes files, pushes nothing",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/admin.py",
                "command": "cicd",
            },
        },
        "docs": [
            {
                "file": "commands.md",
                "title": "Commands",
                "kind": "runbook",
                "content": (
                    "# atlas-admin cicd\n"
                    "\n"
                    "a first CI/CD pipeline from the guardrails matrix (cicd.py); writes files, pushes nothing.\n"
                    "\n"
                    "| Command | What it does |\n"
                    "|---|---|\n"
                    "| `atlas-admin cicd generate` | write the pipeline, its settings notes (and on GitHub the branch protection export) under --out |\n"
                    "\n"
                    "Every option: `atlas-admin cicd --help`, inside the atlas-api container.\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-cli-backup",
        "type": "component",
        "parent": "atlas-system-cli",
        "name": "atlas-admin backup",
        "summary": "Verified logical backups of the Atlas database (crunch_backup.py)",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/admin.py",
                "command": "backup",
            },
        },
        "docs": [
            {
                "file": "commands.md",
                "title": "Commands",
                "kind": "runbook",
                "content": (
                    "# atlas-admin backup\n"
                    "\n"
                    "verified logical backups of the Atlas database (crunch_backup.py).\n"
                    "\n"
                    "| Command | What it does |\n"
                    "|---|---|\n"
                    "| `atlas-admin backup run` | pg_dump, checksum, restore into a temporary database and compare counts; or --verify-only an existing backup directory |\n"
                    "| `atlas-admin backup restore` | restore a backup over the live database: checksum, restore into a staging database and count, then swap it in; the old database is kept as a safety copy. Refuses while anything is connected (stop atlas-api) |\n"
                    "| `atlas-admin backup rollback` | list the safety copies a restore left, or --to one to put it back (the current database is set aside, nothing is dropped) |\n"
                    "\n"
                    "Every option: `atlas-admin backup --help`, inside the atlas-api container.\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-cli-init-db",
        "type": "component",
        "parent": "atlas-system-cli",
        "name": "atlas-admin init-db",
        "summary": "Create missing auth tables (idempotent)",
        "attributes": {
            "baseload": {
                "source": "generated",
                "from": "backend/admin.py",
                "command": "init-db",
            },
        },
        "docs": [
            {
                "file": "commands.md",
                "title": "Commands",
                "kind": "runbook",
                "content": (
                    "# atlas-admin init-db\n"
                    "\n"
                    "create missing auth tables (idempotent).\n"
                    "\n"
                    "`atlas-admin init-db` takes no subcommand.\n"
                    "\n"
                    "Every option: `atlas-admin init-db --help`, inside the atlas-api container.\n"
                ),
            },
        ],
    },
    {
        "id": "atlas-system-clients",
        "type": "component",
        "parent": "atlas-system",
        "name": "Client packs",
        "summary": "How agent clients connect to the MCP: a configuration template, helpers and skills per client.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [],
    },
    {
        "id": "atlas-system-clients-claude-code",
        "type": "component",
        "parent": "atlas-system-clients",
        "name": "Claude Code client pack",
        "summary": "The pack in clients/claude-code: an MCP configuration template, credential helpers and a plugin with Atlas skills.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "claude-code-client-pack.md",
                "title": "Claude Code client pack",
                "kind": "integration",
                "content": (
                    "# Atlas for Claude Code\n"
                    "\n"
                    "Connect [Claude Code](https://code.claude.com/docs) to your Atlas install with **your own** agent\n"
                    "token, and give it three skills for working with Atlas well.\n"
                    "\n"
                    "| File | What it is |\n"
                    "|---|---|\n"
                    "| `mcp.template.json` | MCP server entry for Linux and macOS (helper run through `sh`) |\n"
                    "| `mcp.template.windows.json` | The same for Windows (helper run through PowerShell) |\n"
                    "| `helpers/atlas-mcp-headers.sh`, `helpers/atlas-mcp-headers.ps1` | The `headersHelper`: reads your token from a file and prints the `Authorization` header |\n"
                    "| `plugin/` | The `atlas` plugin: the `atlas-retrieval`, `file-task` and `setup-guide` skills |\n"
                    "| `.claude-plugin/marketplace.json` | Lets Claude Code install the plugin from this directory |\n"
                    "| `tests/` | What CI runs against this pack (a stub MCP endpoint; no Atlas needed) |\n"
                    "\n"
                    "**The token is never written into a JSON file, a command line or an environment variable.** It\n"
                    "lives in one file readable only by you. Claude Code runs the helper on every connection\n"
                    "(`headersHelper`), and the helper reads the file and prints `{\"Authorization\":\"Bearer ...\"}`.\n"
                    "Claude Code also strips credential-looking environment variables (`*TOKEN*`, `*KEY*`, ...)\n"
                    "before it runs a helper from a project `.mcp.json`, so a file is the mechanism that works.\n"
                    "\n"
                    "You need: Claude Code, your install's public URL (`ATLAS_PUBLIC_URL` in its `.env`, below\n"
                    "`https://atlas.example.com`), an Atlas account, and - if the install uses its own certificate\n"
                    "authority - the install's CA certificate.\n"
                    "\n"
                    "## Three steps\n"
                    "\n"
                    "### 1. Get a token into `~/.config/atlas/token`\n"
                    "\n"
                    "An operator mints an agent token for you on the machine that runs the stack, from the\n"
                    "install's `deploy/oss` directory. The token is written only to the `--out` file, never printed:\n"
                    "\n"
                    "```sh\n"
                    "docker compose exec atlas-api \\\n"
                    "  atlas-admin token mint --user <you> --purpose claude-code --scopes read,write --out /tmp/claude-code.token\n"
                    "```\n"
                    "\n"
                    "- `--user` is your Atlas username (or `<idp>:<subject>`, or your email for an SSO account that\n"
                    "  has signed in once). The agent is named `<you>-claude-code` and is linked to you, so\n"
                    "  everything it writes is attributed to it and accountable to you.\n"
                    "- `--scopes` defaults to `read`; filing work (the `file-task` skill) needs `write`. Scopes are\n"
                    "  capped by your role. The token expires (`--days`, at most 365).\n"
                    "- Signed in to the board with a password? You can mint your own instead, under\n"
                    "  *Settings -> Agent credentials*.\n"
                    "\n"
                    "Move the file to the machine you run Claude Code on without printing it, then delete the\n"
                    "copy in the container. On the same machine:\n"
                    "\n"
                    "```sh\n"
                    "mkdir -p ~/.config/atlas && chmod 700 ~/.config/atlas\n"
                    "( umask 077; docker compose exec -T atlas-api cat /tmp/claude-code.token > ~/.config/atlas/token )\n"
                    "docker compose exec -T atlas-api rm /tmp/claude-code.token\n"
                    "```\n"
                    "\n"
                    "From another machine, run the same `cat` over ssh with the redirect on your side\n"
                    "(`( umask 077; ssh <stack-host> 'cd <install>/deploy/oss && docker compose exec -T atlas-api cat /tmp/claude-code.token' > ~/.config/atlas/token )`).\n"
                    "\n"
                    "Then put the helper next to it:\n"
                    "\n"
                    "```sh\n"
                    "# Linux, macOS\n"
                    "cp helpers/atlas-mcp-headers.sh ~/.config/atlas/\n"
                    "chmod 600 ~/.config/atlas/token    # umask applies to a new file only; this covers an existing one\n"
                    "```\n"
                    "\n"
                    "```powershell\n"
                    "# Windows (PowerShell)\n"
                    "New-Item -ItemType Directory -Force \"$HOME\\.config\\atlas\" | Out-Null\n"
                    "Copy-Item helpers\\atlas-mcp-headers.ps1 \"$HOME\\.config\\atlas\\\"\n"
                    "# Only you may read the token: drop inherited access, grant yourself full control.\n"
                    "icacls \"$HOME\\.config\\atlas\\token\" /inheritance:r /grant:r \"$($env:USERNAME):(F)\"\n"
                    "```\n"
                    "\n"
                    "The helper refuses a token file that group or others can read (Windows: Everyone, Users or\n"
                    "Authenticated Users), and anything on its first line that is not a token.\n"
                    "\n"
                    "Check it (prints the header shape, not the token):\n"
                    "\n"
                    "```sh\n"
                    "sh ~/.config/atlas/atlas-mcp-headers.sh | sed -E 's/(Bearer )[^\"]+/\\1<redacted>/'\n"
                    "# {\"Authorization\":\"Bearer <redacted>\"}\n"
                    "```\n"
                    "\n"
                    "### 2. Point the template at your install\n"
                    "\n"
                    "Copy the template for your platform to `~/.config/atlas/atlas.mcp.json`, next to the token and\n"
                    "outside any checkout, and replace `https://atlas.example.com` with your install's URL. Keep the\n"
                    "`/mcp/` path and the trailing slash.\n"
                    "\n"
                    "```sh\n"
                    "sed 's#https://atlas.example.com#https://atlas.yourcompany.example#' mcp.template.json > ~/.config/atlas/atlas.mcp.json\n"
                    "```\n"
                    "\n"
                    "```json\n"
                    "{\n"
                    "  \"mcpServers\": {\n"
                    "    \"atlas\": {\n"
                    "      \"type\": \"http\",\n"
                    "      \"url\": \"https://atlas.yourcompany.example/mcp/\",\n"
                    "      \"headersHelper\": \"sh \\\"$HOME/.config/atlas/atlas-mcp-headers.sh\\\"\"\n"
                    "    }\n"
                    "  }\n"
                    "}\n"
                    "```\n"
                    "\n"
                    "The helper path is absolute on purpose: Claude Code runs a helper from a directory that depends\n"
                    "on where the server is configured. On Windows Claude Code runs the helper through `cmd.exe`,\n"
                    "which is why the Windows template says `%USERPROFILE%` rather than `$HOME`. Your token is used\n"
                    "for one server only; to reach a second install, mint a second token into a second file and pass\n"
                    "its path as the helper's argument (`... atlas-mcp-headers.sh\" \"$HOME/.config/atlas/other.token\"`).\n"
                    "\n"
                    "**If the install uses its own CA** (the default `deploy/oss` setup: `scripts/init-certs.sh`\n"
                    "makes one at `deploy/oss/secrets/tls/ca.crt`), Claude Code must trust it - see\n"
                    "[Trusting the install's CA](#trusting-the-installs-ca) below. A publicly trusted certificate\n"
                    "(for example one from ACME) needs nothing.\n"
                    "\n"
                    "### 3. Add it to Claude Code\n"
                    "\n"
                    "Either for one project - copy it in as that project's `.mcp.json` (Claude Code asks you to trust\n"
                    "the folder before it runs the helper):\n"
                    "\n"
                    "```sh\n"
                    "cp ~/.config/atlas/atlas.mcp.json /path/to/your/project/.mcp.json\n"
                    "```\n"
                    "\n"
                    "or for every project, at user scope:\n"
                    "\n"
                    "```sh\n"
                    "claude mcp add-json atlas \"$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))[\"mcpServers\"][\"atlas\"]))' ~/.config/atlas/atlas.mcp.json)\" --scope user\n"
                    "```\n"
                    "\n"
                    "```powershell\n"
                    "# Windows, PowerShell 7.3 or later (earlier versions mangle the quotes)\n"
                    "claude mcp add-json atlas ((Get-Content \"$HOME\\.config\\atlas\\atlas.mcp.json\" -Raw | ConvertFrom-Json).mcpServers.atlas | ConvertTo-Json -Compress) --scope user\n"
                    "```\n"
                    "\n"
                    "Check the connection:\n"
                    "\n"
                    "```sh\n"
                    "claude mcp list\n"
                    "# atlas: https://atlas.yourcompany.example/mcp/ (HTTP) - ✔ Connected\n"
                    "```\n"
                    "\n"
                    "In a session, `/mcp` shows the server and its tools. Check that `claude mcp get atlas` (or `/mcp`)\n"
                    "shows **your install's URL**: a project's `.mcp.json` that defines a server with the same name\n"
                    "takes precedence over the user-scope one, with no warning. Start `claude` outside such a project,\n"
                    "or add yours under another name.\n"
                    "\n"
                    "`Failed to connect` with a certificate error means the CA step is missing, or Claude Code was not\n"
                    "restarted after it; a 401 means the token file is wrong, revoked or expired (run the helper by\n"
                    "hand, as in step 1).\n"
                    "\n"
                    "## The plugin\n"
                    "\n"
                    "The `atlas` plugin adds three skills:\n"
                    "\n"
                    "- **`atlas-retrieval`** - how to read context out of Atlas cheaply: outline and search for\n"
                    "  pointers first, `atlas_structure` for the shape, `atlas_get` with `prose=summary` for detail,\n"
                    "  `atlas_doc_read` only to follow a procedure; and which calls return tens of kilobytes.\n"
                    "- **`file-task`** - file a workitem through `atlas_file_work`: find the parent cheaply, draft the\n"
                    "  acceptance for you to approve, never invent ids, read back the id the server minted.\n"
                    "- **`setup-guide`** - run the first-run setup guide with an install's owner through\n"
                    "  `atlas_setup_guide`: ask every question, never answer for them, record each topic as a\n"
                    "  Proposed ADR that only the owner, signed in, can accept. It needs an owner's token minted\n"
                    "  with `--scopes read,write,admin`.\n"
                    "\n"
                    "Install it from a checkout of this repository:\n"
                    "\n"
                    "```sh\n"
                    "claude plugin marketplace add ./clients/claude-code\n"
                    "claude plugin install atlas@atlas-client\n"
                    "```\n"
                    "\n"
                    "or try it for one session without installing: `claude --plugin-dir ./clients/claude-code/plugin`.\n"
                    "\n"
                    "## Trusting the install's CA\n"
                    "\n"
                    "Copy the CA certificate (only the certificate, `ca.crt`, never `ca.key`) to the machine that runs\n"
                    "Claude Code, for example to `~/.config/atlas/ca.crt`. Then either:\n"
                    "\n"
                    "- **Tell Claude Code about it** (recommended; affects nothing else): set `NODE_EXTRA_CA_CERTS` in\n"
                    "  the `env` block of `~/.claude/settings.json`, which also reaches background sessions:\n"
                    "\n"
                    "  ```json\n"
                    "  { \"env\": { \"NODE_EXTRA_CA_CERTS\": \"/home/you/.config/atlas/ca.crt\" } }\n"
                    "  ```\n"
                    "\n"
                    "  Then restart Claude Code: Node reads the variable only when the process starts, so `/mcp`\n"
                    "  reconnect in a running session is not enough. Or export it in the shell you start `claude`\n"
                    "  from (that shell only). `claude --debug` logs\n"
                    "  `CA certs: Appended extra certificates from NODE_EXTRA_CA_CERTS (...)` when it loaded.\n"
                    "- **Or add it to the operating system's trust store** (Claude Code reads the system store by\n"
                    "  default): Debian/Ubuntu `sudo cp ca.crt /usr/local/share/ca-certificates/atlas.crt && sudo\n"
                    "  update-ca-certificates`; macOS `sudo security add-trusted-cert -d -r trustRoot -k\n"
                    "  /Library/Keychains/System.keychain ca.crt`; Windows `Import-Certificate -FilePath ca.crt\n"
                    "  -CertStoreLocation Cert:\\CurrentUser\\Root`. This makes every program on the machine trust\n"
                    "  certificates that CA signs, so prefer the first option for a CA you do not control.\n"
                    "\n"
                    "Never turn certificate checking off (`NODE_TLS_REJECT_UNAUTHORIZED=0`): the token would then go\n"
                    "to whoever answers.\n"
                    "\n"
                    "## Using your IdP sign-in instead of a minted token\n"
                    "\n"
                    "Where the install has turned it on (`ATLAS_OAUTH_RESOURCE`, ADR 0041 §9), Claude Code can sign\n"
                    "in to Atlas with an access token from your company IdP (Entra ID at work) instead of the\n"
                    "`atlas_svc_` token in step 1, which keeps working beside it. Add the server WITHOUT\n"
                    "`headersHelper` or `headers`: on the first 401 Claude Code reads the server's\n"
                    "`/.well-known/oauth-protected-resource`, which names the IdP, and `/mcp` -> Authenticate opens\n"
                    "the IdP's sign-in in your browser. Three things to know:\n"
                    "\n"
                    "- **Sign in to the board with SSO once first.** A token for someone Atlas has no account for\n"
                    "  is refused with that instruction; Atlas never creates an account from a token.\n"
                    "- **The IdP must know Claude Code as a client** (Entra has no dynamic registration): your IdP\n"
                    "  admin registers a public client allowed to request the Atlas API's scope, and you give\n"
                    "  Claude Code that client id with its OAuth options for the server (`claude mcp add --help`).\n"
                    "- **What it may do** is the scope you were granted mapped by the install\n"
                    "  (`ATLAS_OAUTH_SCOPE_MAP`, read-only by default), never more than your role and your project\n"
                    "  roles allow. Atlas records it as an agent of yours, one per OAuth client, so its writes are\n"
                    "  attributed to you as they are with a minted token. To turn one client off, an operator\n"
                    "  disables that agent; disabling you turns off all of them.\n"
                    "\n"
                    "## Rotating and revoking\n"
                    "\n"
                    "Mint a new token with the same `--purpose` (it is added to the same agent), replace the file,\n"
                    "run `claude mcp list`, then revoke the old one: `atlas-admin token list --user <you>` and\n"
                    "`atlas-admin token revoke <id>`. Claude Code re-runs the helper on every connection and after a\n"
                    "401, so a replaced file takes effect on the next reconnect (`/mcp` -> reconnect).\n"
                    "\n"
                    "## What CI checks\n"
                    "\n"
                    "`tests/test_client_pack.sh` (the `client-pack` job in `.github/workflows/tools.yml`) checks the\n"
                    "JSON and the manifests (`tests/check_pack.py`, and `claude plugin validate --strict`), runs both\n"
                    "helpers against good and bad token files, starts a TLS stub of the MCP endpoint signed by a\n"
                    "throwaway CA, and connects the real Claude Code CLI to it with the template, the helper and\n"
                    "`NODE_EXTRA_CA_CERTS`. The stub is not Atlas: the connect against a live install is what the\n"
                    "steps above do. The scrub gate (`.github/workflows/scrub.yml`) is enforced on `clients/**`.\n"
                ),
                "from": "clients/claude-code/README.md",
            },
            {
                "file": "agents-guide.md",
                "title": "Agents guide",
                "kind": "integration",
                "content": (
                    "# Agents\n"
                    "\n"
                    "This guide takes you from an Atlas account to an AI agent that reads and writes Atlas as you, and\n"
                    "then explains how that agent is expected to work with the graph. It is written for Claude Code,\n"
                    "which has a ready-made client pack, but any MCP client that can send an `Authorization: Bearer`\n"
                    "header works the same way.\n"
                    "\n"
                    "Related pages:\n"
                    "\n"
                    "- MCP tools lists every tool, its parameters and its description. *Generated.*\n"
                    "- Install, section 7 (document *Install* on node `atlas-system-install`) is the same connection\n"
                    "  seen from the operator's side, right after an install.\n"
                    "- Identity and access (document *Identity and access guide* on node `atlas-system-identity`) says what an\n"
                    "  agent may see and do as its person.\n"
                    "- `clients/claude-code/README.md` (document *Claude Code client pack* on node `atlas-system-clients-claude-code`) is the client pack's own\n"
                    "  guide, with every Windows variant.\n"
                    "\n"
                    "## How an agent connects\n"
                    "\n"
                    "```\n"
                    "agent (Claude Code) --https, Bearer token--> atlas-mcp /mcp/ --same token--> atlas-api\n"
                    "```\n"
                    "\n"
                    "- **An agent is its own account, linked to you.** It authenticates with its own token\n"
                    "  (`atlas_svc_...`), never with your password or your browser session. Everything it writes is\n"
                    "  recorded against the agent, and so against you.\n"
                    "- **atlas-mcp holds no rules.** It checks each caller's token with atlas-api at the door and then\n"
                    "  makes every API call with that same token. atlas-api decides, on every call, what the token may\n"
                    "  read and write.\n"
                    "- **The agent sees at most what you see.** Its scopes (`read`, `write`, `admin`) cap it, your role\n"
                    "  caps the scopes, and with project RBAC on it gets the lower of its scope and your project role.\n"
                    "  The details are in Identity and access (document *Identity and access guide* on node `atlas-system-identity`).\n"
                    "\n"
                    "## From token to working session\n"
                    "\n"
                    "You need Claude Code, the install's public URL (below `https://atlas.example.com`), an Atlas\n"
                    "account, and, if the install uses its own certificate authority, the install's CA certificate\n"
                    "(`ca.crt` only, never `ca.key`).\n"
                    "\n"
                    "### 1. Get a token\n"
                    "\n"
                    "There are three ways. Pick the first one that applies to you.\n"
                    "\n"
                    "**You sign in through SSO.** On an install with single sign-on, signing in through your IdP is the\n"
                    "default way to connect an agent, and there are two forms of it:\n"
                    "\n"
                    "- **IdP sign-in in the client (the default).** Where the install has turned on IdP access tokens\n"
                    "  (`ATLAS_OAUTH_RESOURCE`), Claude Code signs in through your IdP and needs no minted token at\n"
                    "  all. Skip to Using your IdP sign-in (document *Claude Code client pack* on node `atlas-system-clients-claude-code`)\n"
                    "  in the client pack; *IdP access tokens* in\n"
                    "  Identity and access (document *Identity and access guide* on node `atlas-system-identity`) explains what the\n"
                    "  agent may then do.\n"
                    "- **A minted token, confirmed at your IdP.** For a client that cannot sign in itself, mint one in\n"
                    "  **Settings**, **Agent credentials**, as below, with one difference: there is no password to\n"
                    "  re-enter. Type the purpose first; the screen shows the name you are approving (*You are creating\n"
                    "  an agent credential named `<name>` for `<you>`*). Press **Confirm with SSO**, check that name on\n"
                    "  the page that follows, sign in at your IdP again (it asks even if you are signed in there), and\n"
                    "  come back to the screen. Create **that** credential within the time it shows (5 minutes by\n"
                    "  default); a different name is refused (`step_up_agent_mismatch`), and after one credential you\n"
                    "  confirm again. Your agent is named after your email (`<name before the @>-<purpose>`), the same\n"
                    "  name an operator's `token mint` gives. This needs the\n"
                    "  operator to have set `ATLAS_OIDC_STEP_UP=on`\n"
                    "  (Identity and access (document *Identity and access guide* on node `atlas-system-identity`)). Without it the\n"
                    "  board refuses an SSO account with `password_incorrect`, and an operator mints the token for you\n"
                    "  (below; `--user` takes `<idp>:<subject>`, or your email once you have signed in with SSO).\n"
                    "\n"
                    "**You sign in to the board with a password.** Mint your own: profile menu, **Settings**, **Agent\n"
                    "credentials**, *Create an agent credential*.\n"
                    "\n"
                    "- Name it for its purpose (`claude-code`), not for yourself. Your username is prefixed\n"
                    "  automatically, so the agent becomes `<you>-claude-code`. Agent names are permanent.\n"
                    "- Choose scopes: `read` is the default; filing and updating work needs `write`. Take the least you\n"
                    "  need. You can issue a replacement with more later.\n"
                    "- Choose an expiry. Every token expires.\n"
                    "- Re-enter your password. A signed-in browser alone cannot mint a long-lived key.\n"
                    "- The token is shown **once**. Save it straight into the file in step 2, then confirm.\n"
                    "\n"
                    "**An operator mints it.** On the machine that runs the stack, in `deploy/oss/`. The token is\n"
                    "written only to the `--out` file inside the container and never printed:\n"
                    "\n"
                    "```sh\n"
                    "docker compose exec -T atlas-api atlas-admin \\\n"
                    "  token mint --user <you> --purpose claude-code --scopes read,write --out /tmp/claude-code.token\n"
                    "```\n"
                    "\n"
                    "`--scopes` defaults to `read` and is capped by your role; `--days` sets the expiry (at most 365).\n"
                    "The agent is named `<you>-claude-code` and linked to you. Moving the file to your machine without\n"
                    "printing it is step 1 of the client pack README (document *Claude Code client pack* on node `atlas-system-clients-claude-code`).\n"
                    "\n"
                    "### 2. Store it in a file only you can read\n"
                    "\n"
                    "The token lives in exactly one place: `~/.config/atlas/token`, one line, readable by you alone.\n"
                    "\n"
                    "```sh\n"
                    "mkdir -p ~/.config/atlas && chmod 700 ~/.config/atlas\n"
                    "# put the token in ~/.config/atlas/token (paste it in an editor, or move the operator's file)\n"
                    "chmod 600 ~/.config/atlas/token\n"
                    "cp clients/claude-code/helpers/atlas-mcp-headers.sh ~/.config/atlas/\n"
                    "```\n"
                    "\n"
                    "On Windows, `chmod` does nothing real on NTFS. Use `icacls` and the PowerShell helper, as the\n"
                    "client pack README (document *Claude Code client pack* on node `atlas-system-clients-claude-code`)\n"
                    "shows.\n"
                    "\n"
                    "Never put the token where it gets copied:\n"
                    "\n"
                    "| Do not | Because |\n"
                    "|---|---|\n"
                    "| type it at a shell prompt or `export` it | it lands in your shell history |\n"
                    "| pass it as a command-line argument | every user on the machine can see it in the process list |\n"
                    "| write it into `.mcp.json` or `claude mcp add --header` | it is stored in plain text in a config file, often inside a repository |\n"
                    "| paste it into a chat with the agent | the conversation is stored, and the model never needs it |\n"
                    "| put it in a URL | query strings reach server logs |\n"
                    "\n"
                    "Claude Code reads the token through a **headers helper**: a small script it runs on every\n"
                    "connection, which reads the file and prints `{\"Authorization\":\"Bearer ...\"}` to Claude Code only.\n"
                    "The helper refuses a token file that others can read. Check it without printing the token:\n"
                    "\n"
                    "```sh\n"
                    "sh ~/.config/atlas/atlas-mcp-headers.sh | sed -E 's/(Bearer )[^\"]+/\\1<redacted>/'\n"
                    "# {\"Authorization\":\"Bearer <redacted>\"}\n"
                    "```\n"
                    "\n"
                    "### 3. Point the template at your install\n"
                    "\n"
                    "The pack has `clients/claude-code/mcp.template.json` (Linux, macOS) and\n"
                    "`mcp.template.windows.json`. Write your copy next to the token, outside any checkout, replace the\n"
                    "example URL with yours and keep the `/mcp/` path with its trailing slash:\n"
                    "\n"
                    "```sh\n"
                    "sed 's#https://atlas.example.com#https://atlas.yourcompany.example#' \\\n"
                    "  clients/claude-code/mcp.template.json > ~/.config/atlas/atlas.mcp.json\n"
                    "```\n"
                    "\n"
                    "```json\n"
                    "{\n"
                    "  \"mcpServers\": {\n"
                    "    \"atlas\": {\n"
                    "      \"type\": \"http\",\n"
                    "      \"url\": \"https://atlas.yourcompany.example/mcp/\",\n"
                    "      \"headersHelper\": \"sh \\\"$HOME/.config/atlas/atlas-mcp-headers.sh\\\"\"\n"
                    "    }\n"
                    "  }\n"
                    "}\n"
                    "```\n"
                    "\n"
                    "### 4. Trust the install's CA\n"
                    "\n"
                    "The default install makes its own certificate authority (`deploy/oss/secrets/tls/ca.crt`). Claude\n"
                    "Code must trust it, or the connection fails with a certificate error. A publicly trusted\n"
                    "certificate (for example from ACME) needs nothing here.\n"
                    "\n"
                    "Copy `ca.crt` to your machine, for example `~/.config/atlas/ca.crt`, and set `NODE_EXTRA_CA_CERTS`\n"
                    "in the `env` block of `~/.claude/settings.json`, which also reaches background sessions:\n"
                    "\n"
                    "```json\n"
                    "{ \"env\": { \"NODE_EXTRA_CA_CERTS\": \"/home/you/.config/atlas/ca.crt\" } }\n"
                    "```\n"
                    "\n"
                    "Then **restart Claude Code**: Node reads the variable only when the process starts, so `/mcp`\n"
                    "reconnect in a running session is not enough. Exporting it in the shell you start `claude` from\n"
                    "also works, for that shell only. This adds the CA for Claude Code only. Adding\n"
                    "it to the operating system's trust store also works, but then every program on the machine trusts\n"
                    "that CA; the client pack README (document *Claude Code client pack* on node `atlas-system-clients-claude-code`)\n"
                    "has the commands.\n"
                    "\n"
                    "**Never turn certificate checking off** (`NODE_TLS_REJECT_UNAUTHORIZED=0`). The token would go to\n"
                    "whoever answers.\n"
                    "\n"
                    "### 5. Add the server and check it\n"
                    "\n"
                    "For every project, at user scope:\n"
                    "\n"
                    "```sh\n"
                    "claude mcp add-json atlas \"$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))[\"mcpServers\"][\"atlas\"]))' ~/.config/atlas/atlas.mcp.json)\" --scope user\n"
                    "claude mcp list\n"
                    "# atlas: https://atlas.yourcompany.example/mcp/ (HTTP) - ✔ Connected\n"
                    "```\n"
                    "\n"
                    "Or for one project: copy `~/.config/atlas/atlas.mcp.json` in as that project's `.mcp.json` (Claude\n"
                    "Code asks you to trust the folder before it runs the helper). The Windows command is in the client\n"
                    "pack README.\n"
                    "\n"
                    "Check that `claude mcp get atlas` (or `/mcp` in a session) shows **your install's URL**. A\n"
                    "project's `.mcp.json` that defines a server with the same name takes precedence over the\n"
                    "user-scope one, with no warning; start `claude` outside such a project, or add yours under another\n"
                    "name.\n"
                    "\n"
                    "Then, in a session, `/mcp` shows the server and its tools. Ask the agent to call `atlas_health`;\n"
                    "an answer with node and edge counts means the session works end to end.\n"
                    "\n"
                    "| You see | It means |\n"
                    "|---|---|\n"
                    "| `Failed to connect`, certificate error | step 4 is missing, points at the wrong file, or Claude Code was not restarted after it |\n"
                    "| 401 | the token file is wrong, revoked or expired; run the helper by hand as in step 2 |\n"
                    "| 403 naming a scope | the token lacks it, for example `write`; issue a replacement with it |\n"
                    "| 503 | atlas-mcp could not reach atlas-api; it fails closed. Try again shortly |\n"
                    "| 404 on something you can see on the board | a missing grant, not a bug (see [Guardrails](#guardrails-and-what-is-refused)) |\n"
                    "\n"
                    "### 6. Install the plugin (recommended)\n"
                    "\n"
                    "The pack's `atlas` plugin adds three skills that carry the working model below into every session:\n"
                    "\n"
                    "- **`atlas-retrieval`**: the reading order, and which calls return tens of kilobytes.\n"
                    "- **`file-task`**: filing a workitem with `atlas_file_work`, with acceptance the person approves.\n"
                    "- **`setup-guide`**: running the first-run setup interview with an install's owner. It needs an\n"
                    "  owner's token with `admin`.\n"
                    "\n"
                    "```sh\n"
                    "claude plugin marketplace add ./clients/claude-code\n"
                    "claude plugin install atlas@atlas-client\n"
                    "```\n"
                    "\n"
                    "### Other MCP clients\n"
                    "\n"
                    "Any client that speaks MCP over streamable HTTP works: the endpoint is `https://<host>/mcp/` and\n"
                    "each request carries `Authorization: Bearer <token>`. Prefer a client that reads the header from a\n"
                    "file or a command at connect time. If the client is a Node program, `NODE_EXTRA_CA_CERTS` is the CA\n"
                    "setting; otherwise use the client's own CA option, or the system trust store.\n"
                    "\n"
                    "## Behind a gateway\n"
                    "\n"
                    "An install can put [AgentGateway](https://agentgateway.dev) in front of atlas-mcp and other MCP\n"
                    "servers (the `gateway` profile, set up in\n"
                    "Install, section 6 (document *Install* on node `atlas-system-install`)). For the agent,\n"
                    "only the URL changes:\n"
                    "\n"
                    "```json\n"
                    "\"url\": \"https://atlas.example.com:8444/mcp\"\n"
                    "```\n"
                    "\n"
                    "(port `ATLAS_GATEWAY_PORT`). Everything else stays the same:\n"
                    "\n"
                    "- **Same token, same CA.** The gateway forwards your own `Authorization` header to atlas-mcp\n"
                    "  (`forward_authorization: true` in `deploy/gateway/gateway.yaml`), so atlas-api still decides\n"
                    "  every call as you. A gateway that strips the header gets a 401 for every caller.\n"
                    "- **Same tool names.** The gateway does not prefix them: through it the tools are still\n"
                    "  `atlas_get`, `atlas_file_work` and so on.\n"
                    "- **The gateway may show fewer tools.** Each server has a tool allow-list in the gateway\n"
                    "  configuration. A tool not on it is absent from the tool list and refused when called.\n"
                    "\n"
                    "If you run your own gateway instead, it must forward the client's `Authorization` header to\n"
                    "atlas-mcp unchanged and send a Host name that atlas-mcp accepts (`ATLAS_MCP_ALLOWED_HOSTS`;\n"
                    "anything else gets 421).\n"
                    "\n"
                    "## How an agent should work with the graph\n"
                    "\n"
                    "Atlas holds the context: **world nodes** say what each part of your estate is, **workitems** (epic,\n"
                    "story, task) say what is being done about it, **intents** are the standing constraints, and\n"
                    "**documents** on nodes hold procedures. The tools return far more than needed if you let them, so\n"
                    "the working model is mostly about asking the narrowest question first.\n"
                    "\n"
                    "### Orient first\n"
                    "\n"
                    "1. **An open question: `atlas_context(about=...)`.** One bounded call, in your own words: the\n"
                    "   best-matching nodes, their neighbours, what depends on them, the intents that apply, related\n"
                    "   decisions and the documents to read next. It returns pointers, and each part says when its\n"
                    "   budget cut it.\n"
                    "2. **What exists: `atlas_outline`.** One line per node. It defaults to the world; scope it with\n"
                    "   `root=`, add `names=true`.\n"
                    "3. **Which node knows about X: `atlas_node_search(q=...)`.** Pointers, not prose. For the backlog\n"
                    "   use `atlas_search` with filters (`type`, `status`, `parent`, `binding`, `q`) and a small\n"
                    "   `page_size`, and page until you have `total`.\n"
                    "4. **Where it sits: `atlas_structure(id)`.** Ancestors, children, dependencies and binding, without\n"
                    "   prose. It is cheaper than one full read.\n"
                    "5. **Detail: `atlas_get(id)`** on the one or two nodes that matter. When only orienting, pass\n"
                    "   `prose=summary` and `intents=own`.\n"
                    "6. **Procedure: `atlas_doc_read(node_id, path)`** when you are about to follow what the document\n"
                    "   says. `path` must be one the node lists in `attributes.docs[]`.\n"
                    "\n"
                    "`atlas_tree` is for a neighbourhood around one known node, not for discovery; raise its knobs one\n"
                    "at a time. `atlas_capabilities` is about 25 KB; the tool list already arrives with the connection.\n"
                    "\n"
                    "### Read before you write\n"
                    "\n"
                    "- **Read the node full before you change it.** `atlas_get` with its defaults returns the node's own\n"
                    "  and inherited intents, and those are the constraints on the change.\n"
                    "- **Prose fields are replaced whole.** `abstract`, `detail` and a workitem's `body` are overwritten\n"
                    "  by a write, so read what is there first.\n"
                    "- **Check status claims against the data.** Prose can drift; a workitem's `summary` can be stale.\n"
                    "  Read `status`, and check the item or the code before acting on \"this is not built yet\".\n"
                    "- **Read back what you wrote.** Write tools echo the stored row. A write that sent a field the API\n"
                    "  ignores still succeeds, with a `warnings` list naming the field: that is a change that did not\n"
                    "  happen.\n"
                    "\n"
                    "### File work as epic > story > task\n"
                    "\n"
                    "- **Work is rows, not files.** File it with `atlas_file_work`: the item, its parent edge, binding\n"
                    "  and dependencies in one transaction. A markdown task file is invisible to Atlas.\n"
                    "- **The hierarchy is epic > story > task.** A task hangs off a story, a story off an epic. Levels\n"
                    "  may be skipped, never repeated or inverted. Only `atlas_file_work` and `atlas_update_work(parent=)`\n"
                    "  make or move these edges.\n"
                    "- **Find the parent cheaply** (`atlas_search(type=\"story\", q=...)`, then `atlas_structure`). If\n"
                    "  several fit, ask the person. If none fits, say so.\n"
                    "- **Never invent an id.** A task's id is minted by the server; read it off the response. An epic or\n"
                    "  story takes a slug id (`EPIC-search-rework`) that you propose to the person. `parent`, `binding`\n"
                    "  and `depends_on` must be ids an Atlas tool returned in this session.\n"
                    "- **`body` is the spec, not the outcome.** Record outcomes later with\n"
                    "  `atlas_update_work(result=...)`.\n"
                    "- **Suggest a binding, do not guess one.** Which world node the work is about is the person's call.\n"
                    "- **Mind null versus omitted.** In `atlas_update_work`, some fields are cleared by `null` and left\n"
                    "  alone when omitted, and `body`, `acceptance`, `depends_on` and `binding` replace wholesale behind\n"
                    "  a `set_<field>=True` flag. MCP tools explains how the tables mark this; leave\n"
                    "  out a parameter you do not mean to change.\n"
                    "\n"
                    "### Intents are constraints\n"
                    "\n"
                    "An intent is a standing statement on a node that is still true when all open work is done:\n"
                    "a `constraint` (a bug when violated), a `principle` (a defect), a `non-goal` (a removal) or an\n"
                    "`outcome` (direction). It applies to the node and everything below it.\n"
                    "\n"
                    "- Treat every intent `atlas_get` returns, inherited ones included, as a constraint on your change.\n"
                    "- Correcting a fact inside an intent is fine. Changing what it requires is the owning person's\n"
                    "  decision: propose it, do not write it.\n"
                    "- Something that stops being true once the work is done is not an intent; file it as a workitem.\n"
                    "\n"
                    "### Acceptance needs a person\n"
                    "\n"
                    "Acceptance criteria are what a workitem is judged against, so the agent doing the work does not\n"
                    "decide them.\n"
                    "\n"
                    "- **When filing:** draft 2-4 checkable criteria and show them to the person before you call\n"
                    "  `atlas_file_work`.\n"
                    "- **On an existing item:** `atlas_update_work(id=..., acceptance=[...], propose_acceptance=True,\n"
                    "  proposal_note=\"why\")`. This stores a proposal that the board shows as a diff; only a person,\n"
                    "  signed in, can approve or reject it.\n"
                    "- **A plain `set_acceptance=True` is refused** for every token (`acceptance_is_not_yours_to_write`).\n"
                    "  An install that sets `ATLAS_ACCEPTANCE_AGENT_WRITE=attested` also accepts a write carrying\n"
                    "  `approval={by, quote}`: a real person's username and their own words approving this exact\n"
                    "  change, given in the conversation. Never author that quote yourself.\n"
                    "- The same rule covers decisions: an ADR recorded through `atlas_setup_guide` stays Proposed until\n"
                    "  the owner accepts it on the board.\n"
                    "\n"
                    "### Writing prose others will read\n"
                    "\n"
                    "Board content is visible to everyone with access to the project. Keep credentials, private hostnames\n"
                    "and anything confidential out of node prose, documents and workitems. Write what a thing *is* on the\n"
                    "node the work binds to; leave counts and other items' status to the data.\n"
                    "\n"
                    "## Guardrails and what is refused\n"
                    "\n"
                    "The API enforces these; the agent cannot talk its way past them.\n"
                    "\n"
                    "| Guardrail | What the agent sees |\n"
                    "|---|---|\n"
                    "| A hidden node and a missing node look the same | 404 for both, everywhere. A node you cannot see is absent from every search, total and path. If the agent says a project does not exist and you can see it, check the agent's grants |\n"
                    "| Scopes gate writes | 403 naming the missing scope; a `read` token cannot write, and deletes and access administration need `admin` |\n"
                    "| No token exercises `owner` | granting roles and other owner acts are always done by a person |\n"
                    "| A token cannot mint tokens | creating an agent credential needs a signed-in person and their password, or for an SSO person a fresh IdP sign-in (`session_required`, `password_incorrect`, `step_up_required`) |\n"
                    "| Task ids are minted | `id_is_minted` on a task with an `id`; `id_required` on an epic or story without one |\n"
                    "| Outcomes are not spec | `result_in_body` when `body` has a `## Result` or `## Outcome` section |\n"
                    "| Hierarchy edges only through work tools | `atlas_node_write` and `atlas_edge_write` cannot make an epic > story > task edge; illegal moves are refused before anything is written (`parent_order`, `parent_cycle`, ...) |\n"
                    "| Acceptance needs a person | `acceptance_is_not_yours_to_write` |\n"
                    "| Decisions need a person | `decision_needs_a_person` when a token tries to accept a setup-guide ADR |\n"
                    "| Deletes are off by default | `atlas_delete` is not in the tool list unless the operator sets `ATLAS_MCP_ENABLE_DELETE`, and then needs `admin`. It is permanent; a node delete demands `expected_edge_count` |\n"
                    "| Strict installs refuse unknown fields | with `ATLAS_STRICT_API=1`, a 422 naming the field instead of a `warnings` entry |\n"
                    "| Browser origins | atlas-mcp answers 421 to an unknown Host and 403 to an unknown `Origin`, against DNS rebinding |\n"
                    "\n"
                    "`atlas_save_work` (the end-of-session sign-off: your writes since a time, plus the procedure for\n"
                    "recording the session's work) is offered unless the operator lists `save_work` in\n"
                    "`ATLAS_DISABLED_FEATURES`.\n"
                    "\n"
                    "## Rotating and revoking\n"
                    "\n"
                    "- **Rotate without downtime:** issue a replacement (board: *Issue a replacement credential*;\n"
                    "  operator: `token mint` with the same `--purpose`), replace the file, reconnect (`/mcp`, then\n"
                    "  reconnect), check `claude mcp list`, and only then revoke the old token.\n"
                    "- **Revoke:** on the board under Agent credentials, or `atlas-admin token list --user <you>` and\n"
                    "  `atlas-admin token revoke <id>`. atlas-api checks the token on every call, so a revoked token\n"
                    "  stops reading and writing at once. atlas-mcp caches a positive check at its door for up to 60\n"
                    "  seconds (`ATLAS_MCP_AUTH_CACHE_S`), so a revoked token may still open a session in that window,\n"
                    "  but every tool call it makes fails.\n"
                    "- Revoking a token does not delete the agent. Disabling a person disables every agent linked to\n"
                    "  them, and an SSO person's agent tokens are suspended once that person goes too long without an\n"
                    "  SSO login (see Identity and access (document *Identity and access guide* on node `atlas-system-identity`)).\n"
                    "\n"
                    "## Every tool\n"
                    "\n"
                    "MCP tools lists all 25 tools by group (read, work, graph writes, the setup\n"
                    "guide, session), with the description the server sends and each parameter's type and default.\n"
                    "Many descriptions end in *More: tools guide.*: the longer guide for every tool is a document the\n"
                    "agent reads with `atlas_doc_read`, as the `atlas_doc_read` entry there says.\n"
                ),
                "from": "docs/oss/guides/agents.md",
            },
        ],
    },
    {
        "id": "atlas-system-graph",
        "type": "capability",
        "parent": "atlas-system",
        "name": "Graph and world model",
        "summary": "One graph of nodes and typed edges: world nodes for what exists, workitems for what is being done, intents for what must stay true.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "overview.md",
                "title": "Overview",
                "kind": "architecture",
                "content": (
                    "# Graph and world model\n"
                    "\n"
                    "One graph of nodes and typed edges: world nodes for what exists, workitems for what is being done, intents for what must stay true.\n"
                    "\n"
                    "Everything Atlas holds is rows in one graph. World nodes (`org`, `project`, `system`, `service`, `component`, `db`, `host`, `capability`, `agent`) are arranged by `contains`, which means *is part of* and nothing else. Workitems attach to the world by `works_on`. Edge types are a registry with declared family pairs, and drift is reported, not refused. Intents are statements on a node that stay true when all open work is done; they inherit downwards.\n"
                    "\n"
                    "## Surface\n"
                    "\n"
                    "Generated: the parts of Atlas that serve this capability.\n"
                    "\n"
                    "- API area `atlas-system-api-edges` (Edge routes)\n"
                    "- API area `atlas-system-api-graph` (Graph routes)\n"
                    "- API area `atlas-system-api-intents` (Intent routes)\n"
                    "- API area `atlas-system-api-legacy-v0` (First-generation routes)\n"
                    "- API area `atlas-system-api-nodes` (Node routes)\n"
                ),
            },
            {
                "file": "concepts-guide.md",
                "title": "Concepts guide",
                "kind": "architecture",
                "content": (
                    "# Concepts\n"
                    "\n"
                    "This page covers the ideas Atlas is built on and the words the rest of the documentation uses\n"
                    "for them. It is about the model, not the machinery. For the services and how requests travel\n"
                    "through them, see Architecture (document *Architecture* on node `atlas-system-graph`).\n"
                    "\n"
                    "## One graph\n"
                    "\n"
                    "Atlas keeps one **graph** in Postgres: **nodes** joined by typed, directed **edges**. Everything\n"
                    "else in Atlas reads and writes that graph through the same API: the board, the documentation\n"
                    "site, the API and every MCP tool an agent calls. Nothing is imported from files and nothing is\n"
                    "synced in. The database is the source of truth.\n"
                    "\n"
                    "### Nodes\n"
                    "\n"
                    "A node has a stable `id` (for example `billing-api` or `TASK-1042`), a `type`, a `name` and an\n"
                    "`attributes` object. Its text comes in three lengths, from short to long:\n"
                    "\n"
                    "- `summary`: one line that says what it is;\n"
                    "- `abstract`: a paragraph;\n"
                    "- `detail`: everything else you need to work on it.\n"
                    "\n"
                    "Read the shortest text that answers your question. Agents are expected to do the same.\n"
                    "\n"
                    "Every node type belongs to one of three **families**:\n"
                    "\n"
                    "| Family | Types | What it describes |\n"
                    "|---|---|---|\n"
                    "| object (the *world*) | `org`, `project`, `system`, `service`, `component`, `capability`, `agent`, `db`, `host` | things that exist and outlive any piece of work |\n"
                    "| workitem (the *work*) | `epic`, `story`, `task` | things being done, with a status |\n"
                    "| bridge | `skill`, `doc`, `vision` | material attached to the world: practices, documents and design intent |\n"
                    "\n"
                    "The types and their families are in a registry table, `node_type`, which is filled in every time\n"
                    "Atlas starts.\n"
                    "\n"
                    "### Edges\n"
                    "\n"
                    "An edge goes from one node (`src`) to another (`dst`) and has a type. The types in use:\n"
                    "\n"
                    "| Edge | Means | Between |\n"
                    "|---|---|---|\n"
                    "| `contains` | *is part of*. This is the decomposition, not ownership or permissions. | object → object, object → bridge, and workitem → workitem (epic → story → task) |\n"
                    "| `works_on` | this work is about that world object. **At most one per workitem.** | workitem → object |\n"
                    "| `depends_on` | this work waits on that work | workitem → workitem |\n"
                    "| `uses` | one object stands on another without containing it | object → object |\n"
                    "| `hosted_on` | where something **runs**, as opposed to what it is part of | object → object |\n"
                    "| `instance_of` | a deployment points at what it deploys | object → object |\n"
                    "| `contributes_to` | a party contributes to a project; many-to-many | object → object |\n"
                    "\n"
                    "Edge types also have a registry, `edge_type`, which you can read at `GET /api/vocabulary`. Each\n"
                    "entry lists the family pairs that type connects, and has a status of `active` or `dead`.\n"
                    "`/api/health` reports drift: a type in use that the registry does not declare, a pair the\n"
                    "registry does not list, or a `dead` type that has gained rows. One retired type, `serves`, is\n"
                    "declared `dead` on purpose. It meant the same as `works_on`, and a workitem bound by both edges\n"
                    "would be counted twice in every roll-up.\n"
                    "\n"
                    "A node can have more than one `contains` parent. Atlas allows that, and every roll-up below is\n"
                    "written so that a shared child is counted only once.\n"
                    "\n"
                    "**Roots** are not configured. A root is any node with no incoming `contains` edge, so the list of\n"
                    "roots is computed from the graph, not set anywhere.\n"
                    "\n"
                    "## The world and the work\n"
                    "\n"
                    "Atlas keeps two kinds of thing in the same graph, and the difference between them matters.\n"
                    "\n"
                    "**World nodes** describe what exists: an organisation, its projects, the systems in them, the\n"
                    "services, databases and hosts they run on, and the capabilities that span them. They are arranged\n"
                    "with `contains`. A world node has no lifecycle status, though it can carry a `health` hint (for\n"
                    "example `attributes.health: green`). To decide whether something is a world node or a piece of\n"
                    "work, use this test: **a world node outlives the work.** If it is finished when the epic is\n"
                    "finished, it is an epic.\n"
                    "\n"
                    "**Workitems** describe what is being done. They form their own hierarchy with `contains`:\n"
                    "\n"
                    "```\n"
                    "epic            a body of work with one outcome\n"
                    " └─ story       a deliverable slice of it\n"
                    "     └─ task    one unit an agent or a person picks up\n"
                    "```\n"
                    "\n"
                    "A workitem is attached to the world by **`works_on`**, never by `contains`. `contains` asks \"what\n"
                    "is this part of?\" and `works_on` asks \"what does this work change?\". Keeping them separate means\n"
                    "work can be finished and cancelled without moving any part of the world.\n"
                    "\n"
                    "A workitem's binding is **inherited down the work hierarchy**. Its own `works_on` edge wins. If it\n"
                    "has none, it takes the binding of its nearest ancestor that has one, so a task under a story that\n"
                    "works on `billing-api` also works on `billing-api` without its own edge. Only the work hierarchy\n"
                    "inherits this way; a world node never inherits a binding.\n"
                    "\n"
                    "**Where to bind:** bind work to the node whose files or configuration the change touches. When the\n"
                    "change is about policy (who may do something, or what must always be true), bind it to the\n"
                    "`capability` node that holds that policy.\n"
                    "\n"
                    "### What a workitem carries\n"
                    "\n"
                    "- a **status**. The values the board can set are `pending`, `analyze`, `in_progress`, `blocked`,\n"
                    "  `done` and `cancelled`. `done` and `cancelled` both end the lifecycle, and both set\n"
                    "  `completed_at`;\n"
                    "- **acceptance criteria**: an ordered list of the statements that must be true for the item to be\n"
                    "  done;\n"
                    "- a **result**: what was actually delivered, recorded separately from the specification so that\n"
                    "  recording an outcome can never overwrite the spec;\n"
                    "- an **assignee**, which is always a person. An agent works on behalf of a person and is not\n"
                    "  assigned work itself;\n"
                    "- `priority`, `labels` and `depends_on` edges.\n"
                    "\n"
                    "Every change to the graph is recorded in `graph_event`, with the account that made it.\n"
                    "\n"
                    "## Roll-up: work rolls up, intent rolls down\n"
                    "\n"
                    "**Work rolls up.** The signals the board draws on a node are computed from the node and everything\n"
                    "`contains` puts below it, so a `system` usually shows the state of the work under its parts, not\n"
                    "work bound to it directly. Work is a quantity: rolling it *down* would count the same task again at\n"
                    "every level.\n"
                    "\n"
                    "**Intent rolls down.** An intent stated on a node binds that node and everything it contains (see\n"
                    "[Intents](#intents)). Intent is a constraint: a rule on the whole also binds each part, which is\n"
                    "exactly what `contains` says.\n"
                    "\n"
                    "These are not two contradictory rules. Each one follows from what is being moved.\n"
                    "\n"
                    "Roll-ups that combine values use either `max` or a union, both of which give the same answer when\n"
                    "a child is reached twice. Roll-ups that count first collect the ids into a **set**, so that a node\n"
                    "reached through two parents is counted once.\n"
                    "\n"
                    "## Attention: where to look\n"
                    "\n"
                    "Every node carries three derived signals. They are computed again in the same transaction as any\n"
                    "write that changes a status or an edge, and they are never set by hand. There is no scheduler and\n"
                    "no stored value that can go stale.\n"
                    "\n"
                    "| Signal | Question it answers | How it is derived |\n"
                    "|---|---|---|\n"
                    "| **attention**: the node's colour | *Do I need to look at this?* | the worst of the node's own state, its urgency and its children's attention |\n"
                    "| **completion**: the ring | *How done is it?* | done leaves ÷ all leaves of the `contains` subtree |\n"
                    "| **attention count**: the badge | *How many things below need a look?* | the number of red and orange descendants |\n"
                    "\n"
                    "Attention is a red / amber / green style signal with one extra colour:\n"
                    "\n"
                    "| Colour | Means |\n"
                    "|---|---|\n"
                    "| **red** | blocked, or the next actionable bottleneck on work that a goal waits on |\n"
                    "| **orange** | waiting on someone's attention (in review), or stuck behind a red bottleneck |\n"
                    "| **yellow** | worth watching. Only a world node's `health` hint produces it; no workitem status does. |\n"
                    "| **green** | in progress or done; nobody needs to look |\n"
                    "| **grey** | no signal: not started, nothing waits on it, or no health hint |\n"
                    "\n"
                    "A sixth colour, `purple` (awaiting a human decision), is reserved in the schema but not produced\n"
                    "yet.\n"
                    "\n"
                    "Two rules make the colours useful:\n"
                    "\n"
                    "- **Grey is excluded from \"worst\".** Grey means *no signal*, not *healthy* and not *bad*. A node\n"
                    "  with one grey child and one green child is green, not grey.\n"
                    "- **Urgency flows backward along `depends_on`.** A task that has not started is grey only while\n"
                    "  nothing important waits on it. If not-done work under an epic depends on it, it lights up. Only\n"
                    "  the *actionable* bottleneck (the one whose own dependencies are all done) is red; the items\n"
                    "  queued behind it are orange. A chain of dependencies therefore shows one red item, the one to\n"
                    "  do next, instead of turning entirely red.\n"
                    "\n"
                    "Attention and completion are independent. A green node can be half done.\n"
                    "\n"
                    "## Documents\n"
                    "\n"
                    "A node can carry **documents**: markdown pages stored with the node (in the `node_doc` table) and\n"
                    "listed in its `attributes.docs`. Each entry has a `path`, a `title` and a `kind` such as\n"
                    "`architecture`, `runbook`, `design` or `integration`. Documents hold what is too long for\n"
                    "`detail`: how a part works, how to operate it, why it was built the way it was. Mermaid diagrams\n"
                    "render on the board.\n"
                    "\n"
                    "Agents read a document with `atlas_doc_read` and write one with `atlas_doc_write`. Search covers\n"
                    "the documents a node serves, and returns a pointer to where the match is, not the text of the\n"
                    "document.\n"
                    "\n"
                    "## Intents\n"
                    "\n"
                    "An **intent** is a lasting statement of purpose attached to a node. It has a `slug`, a `kind`, a\n"
                    "title, the statement itself and an optional source. There are four kinds:\n"
                    "\n"
                    "| Kind | Example |\n"
                    "|---|---|\n"
                    "| `constraint` | \"Every hop that carries a token is TLS.\" |\n"
                    "| `principle` | \"Prefer adding a setting over changing a default.\" |\n"
                    "| `non-goal` | \"This service does not store files.\" |\n"
                    "| `outcome` | \"An operator can restore the install from one backup.\" |\n"
                    "\n"
                    "Intents are **inherited down `contains`**. A node is bound by its own intents plus those of every\n"
                    "ancestor. A child overrides an inherited intent by stating its own with the **same slug**, and\n"
                    "from that node downward the child's version applies. `atlas_get` returns a node's intents split\n"
                    "into `own` and `inherited`. The rule for anyone changing something, person or agent, is to read\n"
                    "the node's intents first and follow them. `GET /api/intents` searches intents across the whole\n"
                    "graph, for example to list every `constraint`.\n"
                    "\n"
                    "Intents are stored one row per statement, not as a list inside the node. That way two people (or\n"
                    "two agent sessions) editing different intents on the same node cannot overwrite each other's\n"
                    "changes.\n"
                    "\n"
                    "## Finding things: search, retrieval and context\n"
                    "\n"
                    "- **Search** is built in: keyword search in Postgres over names, the three text lengths,\n"
                    "  acceptance criteria, results, labels, decision records and served documents. It answers with\n"
                    "  **pointers**: the node, where the match is and a short snippet.\n"
                    "- **Retrieval** is an optional add-on that adds semantic search (search by meaning, using\n"
                    "  embeddings) alongside keyword search. You point it at an embeddings endpoint of your choice. It\n"
                    "  is off by default, and Atlas works fully without it.\n"
                    "- **Context** (`GET /api/context`, the `atlas_context` tool) answers \"tell me about X\" in one\n"
                    "  bounded response. It returns the nodes that best match, what sits around them, what would be\n"
                    "  affected by a change, the intents that constrain them, the decisions that bear on them and the\n"
                    "  documents to read next. Each part is a pointer and each part has a size limit. This is how an\n"
                    "  agent gathers context for a task cheaply: it reads the pointers first and opens the full text\n"
                    "  only for what it needs.\n"
                    "\n"
                    "In every case, the caller's visibility filter is part of the query. A node you may not see is\n"
                    "never returned, never counted and never used as a path to reach another node.\n"
                    "\n"
                    "## The board and `/docs`\n"
                    "\n"
                    "- **`/board`** is where people work with the graph. It has three parts: a radial **graph** of\n"
                    "  the world, a **work board** filtered to whatever part of the world you select, and a **node\n"
                    "  panel** that shows one node's text, documents, intents, acceptance criteria and links. The\n"
                    "  colours, rings and badges are the attention signals described above. The previous board stays\n"
                    "  available, frozen, at `/board/legacy`.\n"
                    "- **`/docs`** is the install's own handbook, served as a static site. Every install has an\n"
                    "  `atlas-system` node with one child per part (`atlas-api`, `atlas-mcp`, the database, the\n"
                    "  gateway, search, retrieval), and the handbook is those nodes' documents. The same text appears\n"
                    "  on the board, through `atlas_doc_read` and on `/docs`. `/docs` needs sign-in unless\n"
                    "  `ATLAS_PUBLIC_DOCS=1`.\n"
                    "- **Agents** see the same graph through `atlas-mcp`, with their own token. They see what that\n"
                    "  token may see, and every write is attributed to them. See Agents (document *Agents guide* on node `atlas-system-clients-claude-code`).\n"
                    "\n"
                    "## What a new install contains\n"
                    "\n"
                    "A fresh install that sets `ATLAS_SEED=none`, `demo` or `self` starts with Atlas's own world model\n"
                    "under the `atlas-system` node: its services, its capability areas, every MCP tool, every API area,\n"
                    "the board's views and the admin CLI, each with its documents. `demo` adds a sample project, and\n"
                    "`self` adds the `atlas` project. The `homelab` profile seeds none of it, and nothing is written\n"
                    "into a database that already has nodes. An existing install adds the missing nodes with\n"
                    "`python /app/system_node.py --apply`; nothing that exists is changed.\n"
                ),
                "from": "docs/oss/guides/concepts.md",
            },
            {
                "file": "architecture.md",
                "title": "Architecture",
                "kind": "architecture",
                "content": (
                    "# Architecture\n"
                    "\n"
                    "This page covers the parts of an Atlas install, how a request travels through them, where the\n"
                    "security boundaries are and where data is stored. It describes the install in `deploy/oss/`. Read\n"
                    "Concepts (document *Concepts guide* on node `atlas-system-graph`) first if the words *node*, *workitem* or *intent* are new to you.\n"
                    "\n"
                    "Each section ends with the decisions it rests on and the reasons for them. The\n"
                    "architecture decision records record the same decisions with\n"
                    "the alternatives that were considered.\n"
                    "\n"
                    "## The services\n"
                    "\n"
                    "An install is one compose project (`deploy/oss/docker-compose.yml`) on one host. Five services run\n"
                    "by default, and two more are optional.\n"
                    "\n"
                    "| Service | What it is | Published port | Networks |\n"
                    "|---|---|---|---|\n"
                    "| `atlas-postgres` | PostgreSQL 16 with the pgvector extension available. Holds all of Atlas's data. | none | `data` |\n"
                    "| `atlas-migrate` | A one-shot container that runs `atlas-migrate upgrade` to create or update the schema, then exits. `atlas-api` starts only after it succeeds. | none | `data` |\n"
                    "| `atlas-api` | One FastAPI process. It serves the JSON API under `/api`, the board at `/board`, the sign-in pages under `/auth` and the handbook at `/docs`. It listens on HTTPS `:8443` inside the stack, with its own certificate, and serves no plain HTTP. | none | `data`, `origin` |\n"
                    "| `atlas-mcp` | The MCP (Model Context Protocol) server that agents connect to, at `/mcp/`. It stores nothing. Each tool call becomes an API call made with the agent's own token. It listens on HTTPS `:8493` inside the stack, with its own certificate, and serves no plain HTTP. | none | `mcp` |\n"
                    "| `atlas-tls` | The TLS ingress, a Caddy server. It is the only service that publishes a port by default. | `443` (configurable) | `edge`, `origin`, `mcp`, `gateway` |\n"
                    "| `atlas-gateway` *(optional, profile `gateway`)* | An MCP gateway (AgentGateway) in front of `atlas-mcp`. Use it when you want one MCP address for several MCP servers, with a list of the tools each one exposes. | `8444` (configurable), TLS | `gateway`, `gateway-edge` |\n"
                    "| retrieval add-on *(optional)* | Semantic search alongside keyword search. It is **not a separate service**: it runs inside `atlas-api` when `ATLAS_RETRIEVAL=on`, and the `retrieval.yml` overlay gives `atlas-api` its one route out to an embeddings endpoint. The `atlas-retrieval` profile adds only a one-shot check of that configuration. | none | `egress` |\n"
                    "\n"
                    "No model and no embedding service ship with Atlas. The retrieval add-on calls an\n"
                    "OpenAI-compatible embeddings endpoint that you choose, either a hosted one or a model you run on\n"
                    "another machine over TLS.\n"
                    "\n"
                    "The two command-line tools ship in the `atlas-api` image. `atlas-admin` manages people, agents and\n"
                    "tokens. `atlas-migrate` manages the schema.\n"
                    "\n"
                    "### The networks\n"
                    "\n"
                    "Compose networks are what keep the services apart. Most of them are `internal`, which means they\n"
                    "have no route out of the host, and each one connects only the services that need to reach each\n"
                    "other.\n"
                    "\n"
                    "| Network | Members | Internal | Purpose |\n"
                    "|---|---|---|---|\n"
                    "| `data` | postgres, migrate, api | yes | the database, with no route out |\n"
                    "| `origin` | api, tls | yes | the ingress reaching the API's TLS listener (`atlas-api-origin:8443`). Its subnet is pinned so that `ATLAS_TRUSTED_PROXY_CIDRS` is correct by construction. |\n"
                    "| `mcp` | mcp, tls | yes | the ingress reaching the MCP's TLS listener (`atlas-mcp-origin:8493`). On this network `atlas-tls` also answers to the name `atlas-api` (see the TLS hops below). |\n"
                    "| `gateway` | gateway, tls | yes | the gateway reaching `atlas-mcp` through `atlas-tls`, which answers here to the name `atlas-mcp` |\n"
                    "| `edge` | tls | no | the published port, plus ACME traffic when certificates are issued that way |\n"
                    "| `gateway-edge` | gateway | no | the gateway's published port |\n"
                    "| `egress` | api *(with `retrieval.yml` or `oidc.yml`)*, the retrieval check | no | the route to an embeddings endpoint or the identity provider |\n"
                    "\n"
                    "`atlas-api` and `atlas-mcp` share no network. The MCP server has no plain-HTTP path to the API at\n"
                    "all, so the only way it can reach the API is through TLS.\n"
                    "\n"
                    "In the base install, `atlas-api` sits only on internal networks, so it has no route out of the\n"
                    "host. Any feature that makes it call an outside service needs that route added on purpose. The\n"
                    "`retrieval.yml` overlay does this for the embeddings endpoint, and the `oidc.yml` overlay for the\n"
                    "identity provider that OIDC single sign-on needs `atlas-api` to reach; both join `atlas-api` to\n"
                    "`egress`.\n"
                    "\n"
                    "**Decisions**\n"
                    "\n"
                    "- **One ingress, one published port.** Every other service publishes nothing, and CI enforces\n"
                    "  this (`tools/ci/check_tls.sh` rule R5 forbids publishing a plain-HTTP port that carries\n"
                    "  tokens). Everything that faces the network is TLS and passes through one place, where the\n"
                    "  security headers are set.\n"
                    "- **Separate services for the API and the MCP server.** The MCP server is a thin facade that\n"
                    "  holds no data or logic of its own and has no credential of its own. Keeping it separate means\n"
                    "  the graph and its rules exist in exactly one place, the API, and the MCP layer can never become\n"
                    "  a second, weaker copy of them.\n"
                    "- **Retrieval runs inside the API, and Atlas is complete without it.** When the add-on is off,\n"
                    "  nothing of it runs and the schema is exactly what it would be without it. When it is on but\n"
                    "  misconfigured, it reports `unavailable: <reason>` on `/api/health` and never stops Atlas from\n"
                    "  starting. Search falls back to keyword search.\n"
                    "\n"
                    "## How a request travels\n"
                    "\n"
                    "```mermaid\n"
                    "flowchart LR\n"
                    "    subgraph clients[\"clients\"]\n"
                    "        B[\"browser<br/>session cookie + CSRF token\"]\n"
                    "        A[\"agent<br/>its own Bearer token\"]\n"
                    "        S[\"script<br/>service token\"]\n"
                    "    end\n"
                    "\n"
                    "    subgraph host[\"the install (one compose project)\"]\n"
                    "        GW[\"atlas-gateway :8444<br/>optional, profile gateway\"]\n"
                    "        subgraph tls[\"atlas-tls (Caddy)\"]\n"
                    "            PUB[\":8443 public door<br/>board, API, /mcp\"]\n"
                    "            F1[\":9443 'atlas-api'<br/>API front for atlas-mcp\"]\n"
                    "            F2[\":9444 'atlas-mcp'<br/>MCP front for the gateway\"]\n"
                    "        end\n"
                    "        MCP[\"atlas-mcp :8493 TLS<br/>caller gate, no data\"]\n"
                    "        API[\"atlas-api :8443 TLS<br/>AuthGate middleware, deny by default\"]\n"
                    "        PG[(\"atlas-postgres\")]\n"
                    "        EMB[\"embeddings endpoint<br/>optional, outside the install\"]\n"
                    "    end\n"
                    "\n"
                    "    B -->|HTTPS| PUB\n"
                    "    S -->|HTTPS| PUB\n"
                    "    A -->|HTTPS /mcp| PUB\n"
                    "    A -.->|HTTPS /mcp| GW\n"
                    "    GW -.->|HTTPS| F2\n"
                    "    PUB -->|HTTPS, origin net| API\n"
                    "    PUB -->|HTTPS, mcp net| MCP\n"
                    "    F2 -.->|HTTPS, mcp net| MCP\n"
                    "    MCP -->|HTTPS + caller's Bearer| F1\n"
                    "    F1 -->|HTTPS, origin net| API\n"
                    "    API --> PG\n"
                    "    API -.->|HTTPS, retrieval.yml only| EMB\n"
                    "```\n"
                    "\n"
                    "### An agent's tool call, step by step\n"
                    "\n"
                    "1. **Agent → gateway** *(optional)*. The agent connects over HTTPS to\n"
                    "   `https://<host>:<gateway port>/mcp` and sends `Authorization: Bearer <its own token>`. The\n"
                    "   gateway forwards that header unchanged to `atlas-mcp`. It keeps tool names exactly as the\n"
                    "   server defines them (`atlas_get`, never `atlas_atlas_get`). It allows CORS (cross-origin\n"
                    "   requests from a web page) from the install's own URL only, never from `*`.\n"
                    "   Without the gateway, the agent connects to `https://<host>/mcp/` on the main door instead.\n"
                    "2. **Gateway → MCP.** The gateway reaches `https://atlas-mcp:9444/mcp/`. That name is\n"
                    "   `atlas-tls`, whose certificate the gateway checks against the install CA. `atlas-tls` then\n"
                    "   forwards over HTTPS to `atlas-mcp-origin:8493` on the internal `mcp` network, checking\n"
                    "   `atlas-mcp`'s own certificate against the origin CA.\n"
                    "3. **MCP caller gate.** Before the MCP library sees the request, `mcp/app/caller_auth.py` checks\n"
                    "   it. A request with no token or an invalid token gets a `401` with a `WWW-Authenticate: Bearer`\n"
                    "   challenge, and no MCP session is created. The gate does not decide on its own: it asks the API\n"
                    "   (`GET /api/me`) with the caller's token. It caches a positive answer for a short time (60 s by\n"
                    "   default), keyed by a hash of the token. If the API cannot be reached, the answer is `503`, not\n"
                    "   a pass. The MCP server also checks the `Host` and `Origin` headers against\n"
                    "   `ATLAS_MCP_ALLOWED_HOSTS`/`_ORIGINS`, to block DNS rebinding.\n"
                    "4. **MCP → API.** The tool runs and calls `https://atlas-api:9443` with the **caller's** Bearer\n"
                    "   token (`mcp/app/client.py`). The MCP server holds no token of its own, and it refuses to send\n"
                    "   a request without a caller credential.\n"
                    "5. **API gate.** The request passes `AuthGate`, the middleware described below. On that internal\n"
                    "   name the gate admits only a Bearer request that carries no cookie.\n"
                    "6. **Route → database.** The route reads or writes Postgres. The caller's visibility filter is\n"
                    "   part of the SQL query itself, so a node the caller may not see is neither returned nor\n"
                    "   counted. Every graph write is recorded in `graph_event` by a single writer function, with the\n"
                    "   actor taken from the authenticated principal.\n"
                    "7. The answer returns along the same path. The MCP server wraps it in a fixed envelope\n"
                    "   (`{\"ok\": …, \"result\": …}`).\n"
                    "\n"
                    "A browser takes the short path: public door → `atlas-api`. Its cookie never reaches the MCP\n"
                    "server, and the API's internal names never serve a cookie.\n"
                    "\n"
                    "**Decisions**\n"
                    "\n"
                    "- **Each agent calls as itself.** The caller's own token is forwarded at every hop, so the API\n"
                    "  applies that caller's scopes, grants and audit trail. A shared service identity would make\n"
                    "  the audit log say \"atlas-mcp\" for every call and would make one revocation cut off everyone.\n"
                    "- **The MCP server asks the API, and does not implement auth.** Its gate is the API's answer,\n"
                    "  asked once at the door, so that an unauthenticated caller gets a real `401` instead of a session\n"
                    "  in which every tool fails.\n"
                    "\n"
                    "## The auth gate: middleware that fails closed\n"
                    "\n"
                    "All authentication and authorization in `atlas-api` starts in one ASGI middleware, `AuthGate` in\n"
                    "`backend/auth.py`. It is installed right after the app is created\n"
                    "(`auth.install(app, …)` in `backend/main.py`).\n"
                    "\n"
                    "**Why middleware, and not per-route dependencies.** FastAPI's route-level `Depends` does not see\n"
                    "mounted sub-applications or routes that have no dependency attached. A scheme built from those\n"
                    "**fails open**: the next route someone adds without the dependency is public. Middleware wraps\n"
                    "the whole application, including every mount, every 404 and every route added later, so a\n"
                    "forgotten check **fails closed**.\n"
                    "\n"
                    "What the gate does, in order:\n"
                    "\n"
                    "1. **Host allow-list.** A `Host` header that is not one of the install's origins (derived from\n"
                    "   `ATLAS_PUBLIC_URL`) gets `421 misdirected_request` before any handler or database work runs.\n"
                    "   This blocks host-header injection and DNS rebinding. The one exception is\n"
                    "   `ATLAS_INTERNAL_HOSTS`: an internal name such as `atlas-api:9443` is admitted, but only for a\n"
                    "   Bearer request that carries no cookie.\n"
                    "2. **One credential.** A request may carry `Authorization: Bearer …` (a machine or agent) or the\n"
                    "   `__Host-atlas_session` cookie (a person in a browser). If it carries both, it gets `400`.\n"
                    "3. **Resolve the principal.** Tokens are stored as keyed hashes (HMAC with a server-side pepper),\n"
                    "   and so are session ids. An invalid Bearer always gets `401`, and repeated failures from one\n"
                    "   address are rate limited. An expired cookie is treated as no credential.\n"
                    "4. **Deny by default.** A request without a principal is refused unless its path is on\n"
                    "   `PUBLIC_ROUTES`. That list is short: the landing page, sign-in, the privacy notice, the\n"
                    "   liveness probes, and `/docs` when `ATLAS_PUBLIC_DOCS=1`. A browser asking for HTML is\n"
                    "   redirected to sign in. Every other caller gets `401` JSON, because a script that follows a\n"
                    "   redirect would parse a login page as data and report success.\n"
                    "5. **Forced password change.** The flag is read from the database on every request, so a person\n"
                    "   who must change their password reaches only the change page.\n"
                    "\n"
                    "After the gate, write routes call `auth.guard_write`. For a cookie session it checks the `Origin`\n"
                    "header and a CSRF synchronizer token (a per-session token that a forged cross-site request cannot\n"
                    "know). For a Bearer token it checks the token's scope (`read`, `write`, `admin`). A write route\n"
                    "with no scope entry fails closed.\n"
                    "\n"
                    "**Failing closed at every level**\n"
                    "\n"
                    "- If the gate itself raises an exception, the answer is `503`, never \"let it through\".\n"
                    "- Importing `auth` reads the required secrets (`ATLAS_TOKEN_PEPPER`, `ATLAS_PASSWORD_PEPPER`,\n"
                    "  `ATLAS_LOGIN_ORIGINS`) and raises **before the server binds its port** when one is missing.\n"
                    "  Nothing generates a value as a fallback, because a pepper that changed at every boot would log\n"
                    "  everyone out.\n"
                    "- At startup, if the gate cannot read its own tables, the startup sequence fails and the API does\n"
                    "  not serve.\n"
                    "- There is no setting that disables authentication, no default account and no self-registration.\n"
                    "  The first owner is created with `atlas-admin user add <name> --role owner`.\n"
                    "- `ATLAS_REQUIRE_SECRET_FILES=1` makes the services refuse a secret passed as a plain environment\n"
                    "  variable. Secrets are files, passed as compose `secrets:` and read through `*_FILE` settings.\n"
                    "\n"
                    "## The TLS hops\n"
                    "\n"
                    "Every hop that carries a token or a cookie is HTTPS with the certificate verified, the ingress's\n"
                    "own legs to its upstreams included. No service in the stack serves plain HTTP on any network:\n"
                    "`atlas-api` and `atlas-mcp` terminate TLS themselves on their one internal network, and nothing\n"
                    "listens on their old plain ports `8000` and `8092`.\n"
                    "\n"
                    "| Hop | Protocol | Certificate checked against |\n"
                    "|---|---|---|\n"
                    "| client → `atlas-tls` public door (`:443` → `:8443`) | HTTPS | the install's certificate. Its source is `ATLAS_TLS_MODE`: `files` (from `init-certs.sh` or your own PKI), `internal` (Caddy's own CA) or `acme-cloudflare` (Let's Encrypt via DNS-01). |\n"
                    "| `atlas-tls` → `atlas-api` (`atlas-api-origin:8443`, `origin` network) | HTTPS | **only** `secrets/tls/origin-ca.crt`, under the name `atlas-api-origin`. `atlas-api` serves `api-origin.crt` from `init-certs.sh`. |\n"
                    "| `atlas-tls` → `atlas-mcp` (`atlas-mcp-origin:8493`, `mcp` network) | HTTPS | **only** `origin-ca.crt`, under the name `atlas-mcp-origin`. `atlas-mcp` serves `mcp-origin.crt`. |\n"
                    "| `atlas-mcp` → `atlas-tls :9443` (name `atlas-api`) | HTTPS | **only** the install CA (`ATLAS_CA_BUNDLE`). Optional mutual TLS (both sides present a certificate) with `ATLAS_TLS_CLIENT_AUTH=require`. |\n"
                    "| agent → `atlas-gateway` *(optional)* | HTTPS | the install's certificate |\n"
                    "| `atlas-gateway` → `atlas-tls :9444` (name `atlas-mcp`) | HTTPS | the install CA |\n"
                    "| `atlas-api` → embeddings endpoint *(optional)* | HTTPS | the system trust store, or `ATLAS_EMBED_CA_BUNDLE` |\n"
                    "\n"
                    "`origin-ca.crt` is the install CA (a copy of `ca.crt`) on an install made by `init-certs.sh`. With\n"
                    "your own PKI, or on an install made before these listeners existed, `init-certs.sh --origin-only`\n"
                    "makes the two certificates under a separate CA that only the ingress trusts, so `ca.crt` and every\n"
                    "client stay as they are. Each service holds only its own key; neither sees the public door's.\n"
                    "\n"
                    "`mcp/app/tls.py` enforces the MCP hop when the service starts. `ATLAS_BASE_URL` must be\n"
                    "`https://`, and the only exception is a loopback host, which never leaves the machine. Certificate\n"
                    "verification cannot be turned off. CI rejects a client built without verification\n"
                    "(`tools/ci/check_tls.sh` rule R4), an ingress upstream in the OSS Caddyfile that is not `https://`\n"
                    "with the origin-CA transport (rule R6), and an OSS compose file in which `atlas-api` or `atlas-mcp`\n"
                    "does not serve TLS itself (`tools/ci/check_oss_compose.py` rule S8). Install smoke leg 6 checks\n"
                    "every hop of the running install. The retrieval add-on applies the same rule to its endpoint.\n"
                    "\n"
                    "Browser cookies are `Secure` and `__Host-`-prefixed, so a browser session cannot work over plain\n"
                    "HTTP at all. That is intended.\n"
                    "\n"
                    "**Decision.** Atlas does not terminate the public TLS. It runs behind a TLS-terminating ingress,\n"
                    "and every internal hop that carries a token is re-encrypted, the ingress's own legs included: on\n"
                    "those, `atlas-api` and `atlas-mcp` terminate TLS with the install's internal certificates\n"
                    "(ADR 0005). The bundled ingress is one pinned Caddy\n"
                    "image. It has its admin API off, no plain-HTTP listener, and it sets the browser security headers\n"
                    "for every response in one place, including a Content-Security-Policy with no inline script. This\n"
                    "keeps the certificate handling out of the application, and an operator can swap in their own PKI\n"
                    "or certificate source without changing Atlas.\n"
                    "\n"
                    "## Atlas behind any gateway\n"
                    "\n"
                    "Atlas assumes something else sits at the edge, and it makes few assumptions about what that is.\n"
                    "\n"
                    "**HTTP reverse proxies and load balancers.** The bundled `atlas-tls` is one option. You can put\n"
                    "your own reverse proxy, API gateway or load balancer in front of it, or replace it (it reads its\n"
                    "upstreams from `CADDY_API_UPSTREAM` and `CADDY_MCP_UPSTREAM`). Whatever sits in front must:\n"
                    "\n"
                    "- terminate TLS for clients, because cookies are `Secure`;\n"
                    "- forward the `Host` header unchanged, or present a host that `ATLAS_PUBLIC_URL` names, because\n"
                    "  any other host gets `421`;\n"
                    "- forward `Authorization` unchanged;\n"
                    "- not buffer `/mcp/` responses, because MCP's streamable HTTP uses server-sent events;\n"
                    "- route every `/mcp/` request of one MCP session to the same `atlas-mcp` replica, because its\n"
                    "  sessions live in that process's memory; or set `ATLAS_MCP_STATELESS_HTTP=1` on `atlas-mcp`,\n"
                    "  which keeps no session, so any replica answers any request;\n"
                    "- set the real client address in `CF-Connecting-IP`, from an address range listed in\n"
                    "  `ATLAS_TRUSTED_PROXY_CIDRS`, so per-address sign-in throttling sees the client and not the\n"
                    "  proxy. Atlas ignores that header from anywhere else.\n"
                    "\n"
                    "Atlas reads no identity headers from the proxy. It authenticates every request itself, so a proxy\n"
                    "cannot vouch for a caller, and a header a client forges has no effect.\n"
                    "\n"
                    "**MCP gateways.** Any MCP gateway works as long as it passes the caller's `Authorization` header\n"
                    "through to `atlas-mcp`. The bundled one is AgentGateway (profile `gateway`, configured from\n"
                    "`deploy/gateway/gateway.yaml`). One limit applies to every gateway: **a gateway authorizes per\n"
                    "route, not per caller.** A tool federated through it is offered to every client of that route,\n"
                    "and only the API's token check tells callers apart. So the gateway configuration lists the tools\n"
                    "each server exposes (`tools:` in `deploy/gateway/gateway.yaml`). And `atlas-mcp`'s destructive\n"
                    "`atlas_delete` tool is not registered at all unless `ATLAS_MCP_ENABLE_DELETE` is set.\n"
                    "\n"
                    "**Decision.** Atlas sits behind a gateway; it is not one. Identity, scopes and visibility are\n"
                    "enforced in the API on every call, so changing or adding a proxy or gateway cannot widen access.\n"
                    "The worst a misconfigured edge can do is refuse requests (`421`, `401`).\n"
                    "\n"
                    "## Persistence\n"
                    "\n"
                    "**Postgres is the only place Atlas keeps data.** That means nodes and edges, workitem prose,\n"
                    "acceptance criteria and results, node documents, intents, the `graph_event` history, the decision\n"
                    "records, accounts, sessions, tokens and the `auth_event` log. When the retrieval add-on is on,\n"
                    "its chunks and vectors are stored there too, in tables only the add-on creates. `atlas-api`,\n"
                    "`atlas-mcp` and the gateway keep no data of their own and can be restarted at any time. The\n"
                    "board is static files served from an explicit allow-list.\n"
                    "\n"
                    "What an install stores on disk:\n"
                    "\n"
                    "- the volume `atlas-pgdata`: the database;\n"
                    "- `deploy/oss/secrets/`: the database password and URL, the two peppers, the TLS material and any\n"
                    "  key files. These are written by `scripts/init-secrets.sh` and are never committed;\n"
                    "- the volumes `atlas-tls-data` and `atlas-tls-config`: certificates Caddy has issued, so that a\n"
                    "  recreated container keeps them.\n"
                    "\n"
                    "**Back up the database and `secrets/` together.** The peppers are fixed for the life of the\n"
                    "install. Every password verifier and token digest is computed with them, so a database restored\n"
                    "without its peppers has no working password or token, and a new pepper invalidates all of them\n"
                    "at once.\n"
                    "\n"
                    "**Starting up reads the graph; it does not import it.** Atlas reads no data from files at\n"
                    "startup. There is no markdown backlog and no manifest to sync. Startup creates the schema (see\n"
                    "below), seeds the `node_type` and `edge_type` registries (these define the vocabulary, not\n"
                    "content), and, **only into an empty graph**, writes what `ATLAS_SEED` asks for: `none` gives an\n"
                    "empty install with its own `atlas-system` handbook nodes, and `demo` adds one example project. A\n"
                    "second startup changes no row. The test suite proves this by taking a snapshot of the graph,\n"
                    "running startup again and comparing.\n"
                    "\n"
                    "## Migrations\n"
                    "\n"
                    "The schema is managed by SQLAlchemy models plus a small additive reconciler, not by a migration\n"
                    "framework.\n"
                    "\n"
                    "- **`atlas-migrate upgrade`** (`backend/migrate_cli.py`) runs the schema half of startup and\n"
                    "  exits. It runs `create_all` for every model base (this creates missing tables), then\n"
                    "  `_reconcile_graph_schema` (this adds missing nullable columns and missing indexes to tables\n"
                    "  that already exist), then the full-text search indexes. It is idempotent and changes no data.\n"
                    "  In `deploy/oss` it runs as the `atlas-migrate` one-shot before `atlas-api` starts, and you can\n"
                    "  run it by hand after pulling a new release.\n"
                    "- **`atlas-api` startup** runs the same steps again, then the row-writing parts: registry\n"
                    "  seeding, the seed profile and the task-id sequence. Startup retries while Postgres comes up, and\n"
                    "  fails if the schema step fails.\n"
                    "- **One at a time.** Startup and `atlas-migrate upgrade` both hold a Postgres advisory lock\n"
                    "  (`main.boot_lock`) for their whole run, so several `atlas-api` replicas starting together, or\n"
                    "  a replica and the migrate command, never reconcile or seed at the same time: the next one waits,\n"
                    "  then finds nothing to add. A single process never waits. The retrieval add-on takes a different\n"
                    "  lock of its own.\n"
                    "- **Anything the reconciler cannot do** is a one-time script, `backend/migrate_*.py`. The\n"
                    "  reconciler cannot add a `NOT NULL` column, add a `CHECK` to an existing table, change a type,\n"
                    "  rename, drop or transform data. These scripts run in dry-run mode unless you pass `--apply`,\n"
                    "  and a destructive one refuses a table that has rows unless you also pass `--force`. Nothing\n"
                    "  destructive ever runs because a container restarted.\n"
                    "\n"
                    "**Decision, and its limit.** Every schema change so far has been a new table or a new nullable\n"
                    "column. The reconciler handles those with no deploy step, so a full migration framework such as\n"
                    "Alembic was considered and deferred. The cost is that the reconciler only ever **adds**. If you\n"
                    "remove or retype a column in a model, the live table keeps the old one and nothing reports the\n"
                    "mismatch, so that change needs a written migration script. Revisit the decision when changes the\n"
                    "reconciler cannot make stop being one-offs.\n"
                    "\n"
                    "## Where to read the code\n"
                    "\n"
                    "| Path | What |\n"
                    "|---|---|\n"
                    "| `backend/main.py` | the app, the order middleware is installed in, startup, and the router table |\n"
                    "| `backend/auth.py` | the gate, sessions, service tokens, scopes and CSRF. Its header comment is the design. |\n"
                    "| `backend/routers/` | the routes, one module per area |\n"
                    "| `backend/migrate_cli.py` | `atlas-migrate` |\n"
                    "| `backend/retrieval/` | the retrieval add-on |\n"
                    "| `mcp/app/` | `atlas-mcp`: `caller_auth.py` (the caller gate), `client.py` (the API client), `tls.py` (the TLS rules for its outbound hop), `tools_*.py` (the tools) |\n"
                    "| `deploy/oss/` | the compose file, the `retrieval.yml` overlay, `caddy/Caddyfile` and `.env.example` |\n"
                    "| `deploy/gateway/` | the gateway configuration that `scripts/render-gateway.py` renders |\n"
                ),
                "from": "docs/oss/ARCHITECTURE.md",
            },
        ],
        "abstract": "Everything Atlas holds is rows in one graph. World nodes (`org`, `project`, `system`, `service`, `component`, `db`, `host`, `capability`, `agent`) are arranged by `contains`, which means *is part of* and nothing else. Workitems attach to the world by `works_on`. Edge types are a registry with declared family pairs, and drift is reported, not refused. Intents are statements on a node that stay true when all open work is done; they inherit downwards.",
    },
    {
        "id": "atlas-system-work",
        "type": "capability",
        "parent": "atlas-system",
        "name": "Work store",
        "summary": "Workitems with acceptance criteria, results, comments, priority, labels and per-project keys.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "overview.md",
                "title": "Overview",
                "kind": "architecture",
                "content": (
                    "# Work store\n"
                    "\n"
                    "Workitems with acceptance criteria, results, comments, priority, labels and per-project keys.\n"
                    "\n"
                    "A workitem's spec, its acceptance criteria and its recorded result are separate stores, so recording an outcome never overwrites what it was measured against. Acceptance criteria are changed by a person, or by an agent with a person's recorded approval where an install allows it. Tasks get ids from a sequence, or from a project's own key.\n"
                    "\n"
                    "## Surface\n"
                    "\n"
                    "Generated: the parts of Atlas that serve this capability.\n"
                    "\n"
                    "- API area `atlas-system-api-comments` (Comment routes)\n"
                    "- API area `atlas-system-api-workitems` (Workitem routes)\n"
                ),
            },
            {
                "file": "configuration.md",
                "title": "Configuration",
                "kind": "runbook",
                "content": (
                    "# Configuration\n"
                    "\n"
                    "The settings of Work store, from `backend/settings_catalog.py`. Every setting, with its default and where it is read, is in the configuration reference (CONFIG.md); an owner sees the effective values in the board's Config tab.\n"
                    "\n"
                    "| Setting | Kind | Read by | What it is |\n"
                    "|---|---|---|---|\n"
                    "| `ATLAS_ACCEPTANCE_AGENT_WRITE` | plain | atlas-api | Whether an agent may change acceptance criteria with a person's attested approval. |\n"
                    "| `ATLAS_STRICT_API` | plain | atlas-api | Refuse a write that carries a field the route ignores, instead of warning. |\n"
                ),
            },
        ],
        "abstract": "A workitem's spec, its acceptance criteria and its recorded result are separate stores, so recording an outcome never overwrites what it was measured against. Acceptance criteria are changed by a person, or by an agent with a person's recorded approval where an install allows it. Tasks get ids from a sequence, or from a project's own key.",
    },
    {
        "id": "atlas-system-context",
        "type": "capability",
        "parent": "atlas-system",
        "name": "Context answer",
        "summary": "\"Tell me about X\" in one call: the best-matching nodes, their neighbours, dependants, intents, decisions and documents to read next.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "overview.md",
                "title": "Overview",
                "kind": "architecture",
                "content": (
                    "# Context answer\n"
                    "\n"
                    "\"Tell me about X\" in one call: the best-matching nodes, their neighbours, dependants, intents, decisions and documents to read next.\n"
                    "\n"
                    "The context answer is assembled from the graph by atlas-api and returned as pointers within budgets: every part is bounded by a parameter and says when it was cut. The full text is one node read or one document read away.\n"
                    "\n"
                    "## Surface\n"
                    "\n"
                    "Generated: the parts of Atlas that serve this capability.\n"
                    "\n"
                    "- API area `atlas-system-api-context` (Context route)\n"
                ),
            },
        ],
        "abstract": "The context answer is assembled from the graph by atlas-api and returned as pointers within budgets: every part is bounded by a parameter and says when it was cut. The full text is one node read or one document read away.",
    },
    {
        "id": "atlas-system-identity",
        "type": "capability",
        "parent": "atlas-system",
        "name": "Identity and sign-in",
        "summary": "Accounts, passwords, single sign-on, agent accounts linked to the person accountable for them, and their tokens.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "overview.md",
                "title": "Overview",
                "kind": "architecture",
                "content": (
                    "# Identity and sign-in\n"
                    "\n"
                    "Accounts, passwords, single sign-on, agent accounts linked to the person accountable for them, and their tokens.\n"
                    "\n"
                    "People sign in with a password or through an OIDC identity provider. Every agent has its own account, linked to the person who is accountable for it, and acts with its own token; tokens carry scopes (read, write, admin) that bound what they may do. Identity-provider access tokens can act as an agent of the person they were issued to.\n"
                    "\n"
                    "## Surface\n"
                    "\n"
                    "Generated: the parts of Atlas that serve this capability.\n"
                    "\n"
                    "- API area `atlas-system-api-auth` (Sign-in routes)\n"
                    "- API area `atlas-system-api-me` (Account routes)\n"
                    "- command group `atlas-admin bot` (`atlas-system-cli-bot`)\n"
                    "- command group `atlas-admin session` (`atlas-system-cli-session`)\n"
                    "- command group `atlas-admin token` (`atlas-system-cli-token`)\n"
                    "- command group `atlas-admin user` (`atlas-system-cli-user`)\n"
                ),
            },
            {
                "file": "configuration.md",
                "title": "Configuration",
                "kind": "runbook",
                "content": (
                    "# Configuration\n"
                    "\n"
                    "The settings of Identity and sign-in, from `backend/settings_catalog.py`. Every setting, with its default and where it is read, is in the configuration reference (CONFIG.md); an owner sees the effective values in the board's Config tab.\n"
                    "\n"
                    "| Setting | Kind | Read by | What it is |\n"
                    "|---|---|---|---|\n"
                    "| `ATLAS_AUTH_PROVIDERS` | plain | atlas-api | The sign-in providers offered. |\n"
                    "| `ATLAS_LOCAL_LOGIN` | plain | atlas-api | Who may sign in with a password: everyone, or break-glass accounts only. |\n"
                    "| `ATLAS_OIDC_ISSUER` | plain | atlas-api, atlas-mcp | The OIDC issuer URL. |\n"
                    "| `ATLAS_OIDC_CLIENT_ID` | plain | atlas-api | The OIDC client id of this install's app registration. |\n"
                    "| `ATLAS_OIDC_CLIENT_SECRET` | secret | atlas-api | The OIDC client secret. Read from a file only. |\n"
                    "| `ATLAS_OIDC_CLIENT_KEY` | secret | atlas-api | The private key for private_key_jwt. Read from a file only. |\n"
                    "| `ATLAS_OIDC_CLIENT_KEY_ID` | plain | atlas-api | The key id sent with private_key_jwt. |\n"
                    "| `ATLAS_OIDC_TOKEN_AUTH` | plain | atlas-api | How the client authenticates at the token endpoint. |\n"
                    "| `ATLAS_OIDC_REDIRECT_URI` | plain | atlas-api | The OIDC callback URL. |\n"
                    "| `ATLAS_OIDC_SCOPES` | plain | atlas-api | The scopes requested at sign-in. |\n"
                    "| `ATLAS_OIDC_PRESET` | plain | atlas-api | The identity provider preset: generic, entra or keycloak. |\n"
                    "| `ATLAS_OIDC_NAME_CLAIM` | plain | atlas-api | Overrides the preset's name claim. |\n"
                    "| `ATLAS_OIDC_EMAIL_CLAIM` | plain | atlas-api | Overrides the preset's email claim. |\n"
                    "| `ATLAS_OIDC_GROUPS_CLAIM` | plain | atlas-api | Overrides the preset's groups claim. |\n"
                    "| `ATLAS_OIDC_GROUP_MAP_FILE` | plain | atlas-api | The path of the allowlist mapping identity-provider groups to Atlas groups. |\n"
                    "| `ATLAS_OIDC_REQUIRED_GROUPS` | plain | atlas-api | Identity-provider groups of which a person must hold one to sign in with SSO. |\n"
                    "| `ATLAS_OIDC_MAX_SESSION_HOURS` | plain | atlas-api | The absolute lifetime of an SSO session, in hours. |\n"
                    "| `ATLAS_OIDC_REVALIDATE_DAYS` | plain | atlas-api | Days after the last SSO sign-in before that person's agent tokens stop working. |\n"
                    "| `ATLAS_OIDC_STEP_UP` | plain | atlas-api | Whether an SSO person creates their own agent credential after a fresh sign-in at the identity provider instead of a password. |\n"
                    "| `ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS` | plain | atlas-api | How old, in seconds, that fresh sign-in may be when the credential is created. |\n"
                    "| `ATLAS_OIDC_CLIENT_SECRET_SOURCE` | plain | compose | The host file holding the OIDC client secret. |\n"
                    "| `ATLAS_OIDC_CLIENT_KEY_SOURCE` | plain | compose | The host file holding the private_key_jwt key. |\n"
                    "| `ATLAS_OIDC_GROUP_MAP_SOURCE` | plain | compose | The host file holding the OIDC group map. |\n"
                    "| `ATLAS_OAUTH_RESOURCE` | plain | atlas-api, atlas-mcp | The protected resource identity-provider access tokens are issued for. |\n"
                    "| `ATLAS_OAUTH_AUDIENCES` | plain | atlas-api | Every accepted access-token audience, the resource first. |\n"
                    "| `ATLAS_OAUTH_SCOPE_MAP` | plain | atlas-api | Identity-provider scopes mapped to read, write or admin. |\n"
                    "| `ATLAS_LOGIN_ORIGINS` | bootstrap | atlas-api | The origins a browser may sign in from. Required to start. |\n"
                    "| `ATLAS_PASSWORD_PEPPER` | bootstrap | atlas-api | The password pepper. Fixed for the life of the install. |\n"
                    "| `ATLAS_TOKEN_PEPPER` | bootstrap | atlas-api | The service-token pepper. Fixed for the life of the install. |\n"
                    "| `ATLAS_BREACH_CHECK` | plain | atlas-api | How a new password is screened against breached ones: remote or bundled. |\n"
                    "| `ATLAS_ALLOW_SESSION_MINT` | plain | atlas-api | Allow minting a short browser session for a verification pass (off by default). |\n"
                    "| `ATLAS_BEARER_HOSTS` | plain | atlas-api | Hosts on which a Bearer token is honoured; empty means every allowed host. |\n"
                    "| `ATLAS_INTERNAL_HOSTS` | plain | atlas-api | Hosts admitted for Bearer requests only, such as a compose service name. |\n"
                    "| `ATLAS_REQUIRE_SECRET_FILES` | plain | atlas-api, atlas-mcp | Refuse a secret given as a plain environment variable; require NAME_FILE. |\n"
                ),
            },
            {
                "file": "identity-and-access-guide.md",
                "title": "Identity and access guide",
                "kind": "runbook",
                "content": (
                    "# Identity and access\n"
                    "\n"
                    "This guide covers who can sign in to Atlas, how they prove it, and what each person and agent can\n"
                    "see and change. The security controls underneath are described in Security. The\n"
                    "decision record behind most of this page is ADR 0041 (project-scoped RBAC, IdP groups, OIDC login).\n"
                    "\n"
                    "Everything on this page beyond local accounts is **opt-in**. With none of the settings below, an\n"
                    "install has local accounts only, two instance roles, and projects shared by default. The OSS\n"
                    "`deploy/oss/.env.example` opts in to the team settings, and each section says which variable does\n"
                    "what.\n"
                    "\n"
                    "| Setting | Default | Team install | Section |\n"
                    "|---|---|---|---|\n"
                    "| `ATLAS_AUTH_PROVIDERS` | `local` | `oidc` or `local,oidc` | [OIDC](#single-sign-on-with-oidc) |\n"
                    "| `ATLAS_LOCAL_LOGIN` | `all` | `break-glass` | [Break-glass](#break-glass-accounts) |\n"
                    "| `ATLAS_RBAC` | `off` | `projects` | [RBAC](#roles-on-projects-rbac) |\n"
                    "| `ATLAS_DEFAULT_PROJECT_VISIBILITY` | `shared` | `private` | [Visibility](#project-visibility) |\n"
                    "| `ATLAS_SHARED_PROJECT_ROLE` | `editor` | `viewer` | [Visibility](#project-visibility) |\n"
                    "| `ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER` | `true` | `false` | [Instance roles](#instance-roles) |\n"
                    "\n"
                    "A malformed value in any of these stops the boot, and the error names the variable.\n"
                    "\n"
                    "## Accounts\n"
                    "\n"
                    "There are two kinds of account, both in one table:\n"
                    "\n"
                    "- **People.** A person signs in with a password (a local account) or through the identity provider\n"
                    "  (an OIDC account).\n"
                    "- **Agents.** An agent is an `is_service` account with no usable password. It authenticates with an\n"
                    "  `atlas_svc_` token and normally belongs to a person (see\n"
                    "  [What an agent can do](#what-an-agent-can-do-as-its-user)).\n"
                    "\n"
                    "Each account is keyed on the pair (identity provider, subject), for example `local` plus a\n"
                    "username, or `oidc` plus the IdP's stable id. It is never keyed on an email address or a display\n"
                    "name. Usernames are permanent.\n"
                    "\n"
                    "### Local accounts\n"
                    "\n"
                    "Local accounts are made by an operator. There is no self-registration and no reset-by-email.\n"
                    "\n"
                    "```bash\n"
                    "docker compose exec atlas-api atlas-admin user add example-user --role member --email user@example.com\n"
                    "```\n"
                    "\n"
                    "The command prints a temporary password once. At first sign-in the person must choose their own\n"
                    "password before they can do anything else. That requirement is enforced at the gate, on every\n"
                    "request. Passwords must be at least 8 characters, and they are checked against a bundled list of\n"
                    "breached passwords.\n"
                    "\n"
                    "Other `atlas-admin user` commands:\n"
                    "\n"
                    "- `reset-password` issues a new temporary password and ends that person's sessions. It does not\n"
                    "  touch their agents' tokens.\n"
                    "- `disable` stops the person and every agent linked to them. `enable` deliberately does not\n"
                    "  re-enable the agents.\n"
                    "- `list` and `show` print accounts and never print a secret.\n"
                    "\n"
                    "### Instance roles\n"
                    "\n"
                    "Every account has one instance role:\n"
                    "\n"
                    "- **`owner`** administers the instance: accounts, groups, access, and the audit export.\n"
                    "- **`member`** confers nothing beyond what visibility and project roles give.\n"
                    "\n"
                    "An unrecognised stored value resolves to `member`. Accounts created through OIDC are always\n"
                    "`member`. Nobody becomes an owner through the identity provider.\n"
                    "\n"
                    "With project RBAC on, an owner is also the instance admin, who may bind roles on any project. While\n"
                    "`ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER=true` (the default), an owner is also treated as\n"
                    "`owner` on every project. Set it to `false` and an admin who needs to read a project binds\n"
                    "themselves first, and that binding is audited.\n"
                    "\n"
                    "## Single sign-on with OIDC\n"
                    "\n"
                    "With `oidc` in `ATLAS_AUTH_PROVIDERS`, Atlas is a confidential OpenID Connect client of one issuer\n"
                    "(`backend/identity/oidc.py`). The login page shows **Sign in with SSO**. With `local,oidc` it also\n"
                    "shows the password form.\n"
                    "\n"
                    "**The flow** is the authorization code flow with PKCE (S256), `state` and `nonce`:\n"
                    "\n"
                    "1. `GET /auth/oidc/start` stores the flow server-side: a digest of `state`, the nonce and the PKCE\n"
                    "   verifier. The flow is bound to the browser's pre-login session and lives for ten minutes.\n"
                    "2. The IdP redirects back to `/auth/oidc/callback`. The flow row is deleted on first sight, so a\n"
                    "   replayed callback finds nothing, and a callback in another browser finds no flow.\n"
                    "3. Atlas redeems the code with the verifier and its client credential: `client_secret_basic`,\n"
                    "   `client_secret_post`, or `private_key_jwt`.\n"
                    "4. The ID token is verified:\n"
                    "   - The signature must be from the issuer's JWKS, with an asymmetric algorithm only (never `none`,\n"
                    "     never HS*).\n"
                    "   - `iss` must equal the issuer exactly.\n"
                    "   - `aud` must contain the client id, and `azp` must match it when present.\n"
                    "   - `exp`, `iat` and `sub` must be present, with 60 seconds of leeway.\n"
                    "   - The nonce is compared in constant time.\n"
                    "5. Atlas issues its own `__Host-` session, as a password login does. The IdP's tokens are used\n"
                    "   once, in memory, and never stored.\n"
                    "\n"
                    "Every refusal gives the caller the same generic failure. The reason goes to `auth_event`. Discovery\n"
                    "is fetched lazily and cached, so Atlas boots, and break-glass login works, while the IdP is down.\n"
                    "\n"
                    "**Settings.** The full list, with validation rules, is in Configuration.\n"
                    "\n"
                    "| Variable | Meaning |\n"
                    "|---|---|\n"
                    "| `ATLAS_OIDC_ISSUER` | the `https` issuer URL; discovery is read from it |\n"
                    "| `ATLAS_OIDC_CLIENT_ID` | Atlas's client id at the IdP |\n"
                    "| `ATLAS_OIDC_CLIENT_SECRET_FILE` | the client secret, read **only** from a file |\n"
                    "| `ATLAS_OIDC_CLIENT_KEY_FILE`, `ATLAS_OIDC_CLIENT_KEY_ID` | the private key and its id for `private_key_jwt`, instead of a secret |\n"
                    "| `ATLAS_OIDC_TOKEN_AUTH` | `client_secret_basic`, `client_secret_post` or `private_key_jwt` |\n"
                    "| `ATLAS_OIDC_REDIRECT_URI` | `https://<host>/auth/oidc/callback`; derived from `ATLAS_PUBLIC_URL` if unset |\n"
                    "| `ATLAS_OIDC_PRESET` | `generic`, `entra` or `keycloak`: which claims hold the subject, name, email and groups |\n"
                    "| `ATLAS_OIDC_NAME_CLAIM`, `_EMAIL_CLAIM`, `_GROUPS_CLAIM` | override one claim name of the preset |\n"
                    "| `ATLAS_OIDC_REQUIRED_GROUPS` | IdP group values; see below |\n"
                    "| `ATLAS_OIDC_GROUP_MAP_FILE` | the group allowlist; see [IdP group sync](#idp-group-sync) |\n"
                    "| `ATLAS_OIDC_MAX_SESSION_HOURS` | absolute lifetime of an SSO session (12) |\n"
                    "| `ATLAS_OIDC_REVALIDATE_DAYS` | how long an SSO person's agent tokens keep working without a fresh login (30) |\n"
                    "| `ATLAS_OIDC_STEP_UP`, `ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS` | SSO people create their own agent credentials, and SSO owners change configuration, after a fresh IdP sign-in (off; 300 s); see [Agent credentials for SSO people](#agent-credentials-for-sso-people) and [Configuration changes by SSO owners](#configuration-changes-by-sso-owners) |\n"
                    "\n"
                    "**Required groups and just-in-time accounts.** Once `ATLAS_OIDC_REQUIRED_GROUPS` is set, every SSO\n"
                    "login, not only the first, needs at least one of those groups in the token. A leaver removed from\n"
                    "the group is refused at their next login. A person with no Atlas account who passes that check is\n"
                    "created on the spot as a `member`. If `ATLAS_OIDC_REQUIRED_GROUPS` is unset, no account is ever\n"
                    "created by a login, and only existing linked accounts can sign in through SSO.\n"
                    "\n"
                    "**Moving a local person to SSO.** `atlas-admin user link-idp <username> --subject <id>` points an\n"
                    "existing local account at an IdP identity. The person keeps their history and agents. Their old\n"
                    "username is kept as the account's **local sign-in name**, which only matters for the\n"
                    "[password fallback](#a-personal-password-as-a-fallback-when-sso-is-down). Add `--no-local-name`\n"
                    "for an account that must never sign in with a password.\n"
                    "\n"
                    "**SSO-only, or SSO with a personal password.** An account for someone who will sign in through SSO\n"
                    "is made in one of two ways:\n"
                    "\n"
                    "- **SSO-only:** `atlas-admin user add <name> --no-password`, then `user link-idp`. The account has\n"
                    "  no Atlas password at all and never gets one.\n"
                    "- **SSO with a personal password:** `atlas-admin user add <name>` as usual, then `user link-idp`.\n"
                    "  The temporary password the first command printed is never used: hand it to nobody. The account\n"
                    "  keeps the \"must choose a password\" flag, and `link-idp` does not clear it.\n"
                    "\n"
                    "The forced password change applies to **password sign-ins only**. Atlas records how each session\n"
                    "began (`auth_session.auth_method`: `password`, `sso`, or `minted` for `session mint`). The gate\n"
                    "reads that, not the kind of account. A session made through the IdP\n"
                    "reaches the board even while the flag is set. In that session the board shows a notice, *You have\n"
                    "no personal password yet*, which can be dismissed. **Set a password** sends the person through a\n"
                    "fresh IdP sign-in (the [step-up](#agent-credentials-for-sso-people), so `ATLAS_OIDC_STEP_UP=on` is\n"
                    "needed; without it the notice says the board cannot set one), and back on the board they choose the\n"
                    "password without the temporary one. See\n"
                    "[A personal password for SSO people](#a-personal-password-for-sso-people).\n"
                    "\n"
                    "An account that signs in with its temporary password is still sent to the change page first. That\n"
                    "holds for a local account, as before, and for a linked account signing in under its local name (see\n"
                    "the fallback below). Installs without OIDC are unchanged.\n"
                    "\n"
                    "### A personal password as a fallback when SSO is down\n"
                    "\n"
                    "A linked account whose person has set a personal password (see\n"
                    "[A personal password for SSO people](#a-personal-password-for-sso-people)) can sign in with its\n"
                    "**local sign-in name** and that password. This is the fallback for when the IdP is down, and for\n"
                    "testing without SSO. Whether it is allowed depends on the providers and on `ATLAS_LOCAL_LOGIN`:\n"
                    "\n"
                    "| `ATLAS_AUTH_PROVIDERS` | `ATLAS_LOCAL_LOGIN` | A linked account's password |\n"
                    "|---|---|---|\n"
                    "| `local,oidc` | `all` (the default with `local`) | signs in under its local name, if the account has a password |\n"
                    "| `local,oidc` | `break-glass` | signs in only if the account is flagged break-glass, at `/auth/login?break_glass=1`; each use writes its own `login_break_glass` row |\n"
                    "| `oidc` | `break-glass` (implied) | does not sign in |\n"
                    "| `local` (no OIDC) | `all` | does not sign in; nothing changes |\n"
                    "\n"
                    "- **The local name.** `user link-idp` keeps the username the account had before it was linked. An\n"
                    "  account linked before this existed, or created by a first SSO login, gets one with\n"
                    "  `atlas-admin user set-local-name <idp>:<subject> <name>`. `user set-local-name <account> --clear`\n"
                    "  removes it. Both are `user_local_name` rows in `auth_event`.\n"
                    "- **One namespace.** A local name is a username. `set-local-name` refuses a name that a local account or an\n"
                    "  agent already has, or that another linked account uses as its local name. `user add` and\n"
                    "  `bot add` refuse a linked account's local name. If a local account and a local name ever clash\n"
                    "  anyway, the local account wins its own name, and the linked account cannot use the fallback.\n"
                    "- **What it is.** A password sign-in. The session is a `password` session, so a temporary password\n"
                    "  forces the change first, and an SSO-only account (`--no-password`) still has no password at all.\n"
                    "  `login_ok` carries `local_name=<name>` in its detail.\n"
                    "- **Offboarding.** Removing the person at the IdP does not stop a password. Disable the account in\n"
                    "  Atlas too (`user disable`), or clear its local name. A password sign-in is not an IdP login, so it\n"
                    "  does not lift the [agent token suspension](#what-an-agent-can-do-as-its-user).\n"
                    "- **Where it shows.** `user list` adds a `local_name` column once any account has one. For an\n"
                    "  IdP-backed account, `user show --json` has a `local_name` key. Admin verbs also accept the local\n"
                    "  name to find the account.\n"
                    "\n"
                    "### Microsoft Entra ID\n"
                    "\n"
                    "- Set `ATLAS_OIDC_PRESET=entra`. The issuer must be a **tenant** issuer,\n"
                    "  `https://login.microsoftonline.com/<tenant-id>/v2.0`. The multi-tenant `common` and\n"
                    "  `organizations` issuers are refused, because they would let another tenant's users in.\n"
                    "- The subject is the user's object id (`oid`). The token's tenant id (`tid`) must equal the\n"
                    "  issuer's tenant, or the login is refused. For `link-idp`, pass the user's Object ID.\n"
                    "- With `private_key_jwt`, set `ATLAS_OIDC_CLIENT_KEY_ID` to the certificate's base64url SHA-1\n"
                    "  thumbprint. Atlas sends it as `x5t`.\n"
                    "- With `ATLAS_OIDC_STEP_UP=on`, add the optional claim `auth_time` to the ID token in the app\n"
                    "  registration (Token configuration). Without it every step-up is refused with\n"
                    "  `step_up_auth_time_missing` (see [Agent credentials for SSO people](#agent-credentials-for-sso-people)).\n"
                    "- **Group overage.** Past about 200 groups, Entra leaves the groups claim out and sends a pointer\n"
                    "  instead. Atlas cannot tell which groups that hides, so it refuses the login (fail closed). In the\n"
                    "  app registration, emit only the groups assigned to the application.\n"
                    "- In the group map, key Entra groups by their object id.\n"
                    "\n"
                    "### Keycloak\n"
                    "\n"
                    "- Set `ATLAS_OIDC_PRESET=keycloak` and use the realm issuer,\n"
                    "  `https://<keycloak-host>/realms/<realm>`.\n"
                    "- The subject is `sub`. The name comes from `name`, falling back to `preferred_username`. The\n"
                    "  email comes from `email`.\n"
                    "- Add a group membership mapper so the ID token carries a `groups` claim. The map file keys\n"
                    "  Keycloak groups by the values that claim carries, typically the group path, such as\n"
                    "  `/atlas/maintainers`.\n"
                    "\n"
                    "### Break-glass accounts\n"
                    "\n"
                    "With SSO on, local password login stays open to every local account by default\n"
                    "(`ATLAS_LOCAL_LOGIN=all`). For a team install, set `ATLAS_LOCAL_LOGIN=break-glass`. Then only\n"
                    "local accounts created with the break-glass flag can use a password:\n"
                    "\n"
                    "```bash\n"
                    "docker compose exec atlas-api atlas-admin user add breakglass --role owner --break-glass\n"
                    "```\n"
                    "\n"
                    "- The password form is no longer linked from the login page. It is reachable only at\n"
                    "  `/auth/login?break_glass=1`.\n"
                    "- Every break-glass login writes its own `login_break_glass` row to `auth_event`.\n"
                    "- Keep the credential in your organisation's vault. It is how you administer Atlas when the IdP is\n"
                    "  down or misconfigured.\n"
                    "\n"
                    "If `local` is not listed in `ATLAS_AUTH_PROVIDERS`, `ATLAS_LOCAL_LOGIN` defaults to `break-glass`.\n"
                    "\n"
                    "A break-glass owner who is later linked keeps the flag. With `local,oidc` and\n"
                    "`ATLAS_LOCAL_LOGIN=break-glass`, it signs in under its local name\n"
                    "([the fallback](#a-personal-password-as-a-fallback-when-sso-is-down)).\n"
                    "\n"
                    "## IdP group sync\n"
                    "\n"
                    "Groups are where access is managed at scale (`backend/identity/groups.py`).\n"
                    "\n"
                    "- **Local groups** are made by an instance owner, in the access screen or with\n"
                    "  `POST /api/access/groups` and `PUT /api/access/groups/{id}/members`. They suit installs without an\n"
                    "  IdP, and exceptions the IdP does not model.\n"
                    "- **IdP groups** are synchronised **at every SSO login**. The person's IdP memberships are replaced\n"
                    "  with what the token's groups claim says. Local memberships are never touched by sync.\n"
                    "- **The allowlist** in `ATLAS_OIDC_GROUP_MAP_FILE` is the only way an IdP group reaches Atlas. A\n"
                    "  claim value that is not in the map is ignored and never created. The file is YAML (or JSON):\n"
                    "\n"
                    "  ```yaml\n"
                    "  # IdP group value -> Atlas group name\n"
                    "  groups:\n"
                    "    \"3f6c1c1e-6f4d-4a59-9a7e-0f1c2d3e4f50\": team-alpha      # Entra: the group's object id\n"
                    "    \"/atlas/maintainers\": maintainers                       # Keycloak: the group path\n"
                    "  ```\n"
                    "\n"
                    "  A mapped Atlas group that does not exist yet is created. A mapping onto an existing group adds\n"
                    "  IdP memberships to it. With no map file, nothing is synchronised, and a person's IdP memberships\n"
                    "  are emptied at their next login.\n"
                    "- Every membership added or removed is written to `auth_event`.\n"
                    "- Sync is part of the login. A membership change in the IdP takes effect at the person's next\n"
                    "  login, so it is bounded by `ATLAS_OIDC_MAX_SESSION_HOURS`.\n"
                    "\n"
                    "Sync creates memberships, not access. Bind the group to projects as described in the next section.\n"
                    "\n"
                    "## Roles on projects (RBAC)\n"
                    "\n"
                    "Set `ATLAS_RBAC=projects` to turn on project-scoped roles (`backend/rbac.py`). With it `off`, the\n"
                    "default, none of this code runs. The instance roles and per-user grants then work as described under\n"
                    "[Project visibility](#project-visibility).\n"
                    "\n"
                    "**The project is the unit.** A node's project is its nearest `contains` ancestor of type `project`,\n"
                    "counting the node itself. A root of any other type is its own project. A work item follows the node\n"
                    "it works on. A node with several parents belongs to all their projects, and every check must pass\n"
                    "on all of them. A node whose project cannot be resolved has no project, and so no role on it: it is\n"
                    "refused.\n"
                    "\n"
                    "**Four ordered roles**, `viewer < editor < maintainer < owner`:\n"
                    "\n"
                    "| Role | May |\n"
                    "|---|---|\n"
                    "| `viewer` | read every node, document, intent, event and search result in the project |\n"
                    "| `editor` | also create and change nodes, work items, edges, documents, intents, acceptance and results |\n"
                    "| `maintainer` | also **delete** nodes and edges, and grant roles up to `maintainer` |\n"
                    "| `owner` | also grant `owner`, and delete the project root |\n"
                    "\n"
                    "An unrecognised stored role resolves to no role, not to `viewer`.\n"
                    "\n"
                    "**Roles are granted to groups**, and to individual people, with\n"
                    "`PUT /api/access/project/{project_id}/bindings` or the access screen. Only a signed-in person can do\n"
                    "this, never a token. Who may grant:\n"
                    "\n"
                    "- an instance owner may grant anything;\n"
                    "- a project maintainer may grant up to `maintainer`;\n"
                    "- a project owner may grant up to `owner`.\n"
                    "\n"
                    "**A person's role on a project** is the highest of:\n"
                    "\n"
                    "- their own bindings on it;\n"
                    "- the bindings of every group they belong to;\n"
                    "- `editor`, if they hold a per-user grant (`node_grant`) on the project root;\n"
                    "- `ATLAS_SHARED_PROJECT_ROLE`, if the project is shared.\n"
                    "\n"
                    "### Who owns a new project\n"
                    "\n"
                    "**The person who creates a project root owns it.** With RBAC on, creating a node that is a project\n"
                    "root binds its creator `owner` on that project, in the same transaction. A person is bound themself.\n"
                    "An agent's **linked person** is bound, never the agent, so the person sees what their agent made and\n"
                    "the agent works there within its token's cap. Two creations make a root:\n"
                    "\n"
                    "- `POST /api/node`, which creates no edge, so the node is a root until a `contains` edge gives it a\n"
                    "  parent;\n"
                    "- `POST /api/workitem` with neither `parent` nor `binding`, because an unbound work item is its own\n"
                    "  project. This is how the board files work and how an agent's `atlas_file_work` does.\n"
                    "\n"
                    "**An agent with no linked person cannot create a project root.** It gets a `403` with\n"
                    "`\"reason\": \"agent_not_linked_to_a_person\"` and nothing is written. Link it to a person\n"
                    "(`atlas-admin bot link`). A work item can instead be filed under a parent or a binding, and is then\n"
                    "not a root. `POST /api/node` takes no parent, so for a node linking the agent is the only way.\n"
                    "\n"
                    "Each of these bindings is recorded in `auth_event` as `project_role_bound`, the same entry the\n"
                    "bindings screen writes, with the caller (the agent, if an agent created it) as the actor. A node\n"
                    "that becomes a root later, because its last parent edge was deleted, gets no automatic owner: an\n"
                    "instance admin binds one. With RBAC off, creation binds nobody, as before.\n"
                    "\n"
                    "The reason: a root is its own project, and on a private-by-default install a project nobody is bound\n"
                    "to is unreachable, so without this rule a create would answer `201` and the next read of the same\n"
                    "node `404`. The person, not the agent, is bound because the person answers for what their agent\n"
                    "makes, and no token ever exercises `owner`: the agent stays capped by its token's scope.\n"
                    "\n"
                    "**Deleting needs `maintainer`** on the project of every row removed, and on both ends of an edge.\n"
                    "Deleting a project root needs `owner`. A token's `admin` scope alone is not enough.\n"
                    "\n"
                    "A refused write is a `403` naming the role needed and the role held. A node the caller cannot see is\n"
                    "still a `404`.\n"
                    "\n"
                    "**Turning it on.** Run `atlas-admin rbac plan` first. It writes nothing and prints, for every\n"
                    "principal and project, today's effective permission next to the one RBAC would give. The usual\n"
                    "difference is that agents with an `admin` token can delete only where their person is a\n"
                    "`maintainer`. Add those bindings, then set `ATLAS_RBAC=projects`. Turning it off again restores the\n"
                    "previous behaviour, because nothing was converted.\n"
                    "\n"
                    "## Project visibility\n"
                    "\n"
                    "**RBAC off (the default).** Every node is shared unless a subtree is marked private. A private\n"
                    "subtree is visible to:\n"
                    "\n"
                    "- instance owners;\n"
                    "- people granted it by name (`node_grant`);\n"
                    "- those people's agents.\n"
                    "\n"
                    "An instance owner sets visibility and grants in the access screen, or with\n"
                    "`PUT /api/access/node/{node_id}`, which also needs an `admin` scope from a token.\n"
                    "\n"
                    "**RBAC on.** A project's visibility is the mark on its root, or `ATLAS_DEFAULT_PROJECT_VISIBILITY`\n"
                    "when its root has no mark.\n"
                    "\n"
                    "- **Private project:** only principals with at least `viewer` on it, through a binding, a group or\n"
                    "  a root grant, can see it.\n"
                    "- **Shared project:** every active account gets the role `ATLAS_SHARED_PROJECT_ROLE` there. That is\n"
                    "  `editor` by default, which matches the RBAC-off behaviour. A team install sets `viewer`, so that\n"
                    "  editing needs an explicit binding.\n"
                    "- **Inner subtrees:** a private mark on a subtree inside a project still narrows visibility exactly\n"
                    "  as with RBAC off. Even a project viewer needs a grant to see inside that subtree.\n"
                    "\n"
                    "**A refusal looks like an absence.** A node you may not see answers `404`, the same as an id that\n"
                    "does not exist. Search, listings, counts and rollups are computed over the caller's visible set, so\n"
                    "they do not leak hidden nodes. If an agent insists a project does not exist that its person can see,\n"
                    "check the agent's access before suspecting a bug.\n"
                    "\n"
                    "## What an agent can do as its user\n"
                    "\n"
                    "An agent is its own account, linked to the person it belongs to (`person_user_id`). Every write it\n"
                    "makes is recorded against the agent, and so against that person.\n"
                    "\n"
                    "**Creating one.** A signed-in person opens Settings, Agent credentials, or calls\n"
                    "`POST /api/me/agent-credential`. This creates the agent account, links it to them and mints its\n"
                    "first token, under these rules:\n"
                    "\n"
                    "- It works only from a browser session. A token cannot mint tokens.\n"
                    "- The person must re-enter their password, or, for an SSO person on an install with\n"
                    "  `ATLAS_OIDC_STEP_UP=on`, sign in at the IdP again (below).\n"
                    "- The scopes are capped by the person's instance role: a `member` gets at most `read` and `write`,\n"
                    "  an `owner` can also have `admin`.\n"
                    "- The token must have an expiry. There is no never-expires option.\n"
                    "\n"
                    "### Agent credentials for SSO people\n"
                    "\n"
                    "**IdP sign-in is the default for an SSO install.** With `ATLAS_OAUTH_RESOURCE` set, an MCP client\n"
                    "such as Claude Code signs in through the IdP and needs no minted token (see IdP access tokens\n"
                    "below). For a client that needs a minted `atlas_svc_` token, an SSO person mints it themselves after\n"
                    "a fresh IdP sign-in, once the operator sets `ATLAS_OIDC_STEP_UP=on`.\n"
                    "\n"
                    "**Why a step-up.** Minting re-authenticates the person, because a token minted from a browser\n"
                    "session alone would let a stolen session create a key that outlives it. A local account re-enters\n"
                    "its password. An SSO account has no Atlas password, so without step-up the screen refuses it with\n"
                    "`403 password_incorrect`. With step-up on, it signs in at the IdP again instead:\n"
                    "\n"
                    "1. In Settings, Agent credentials, the person names the credential first: the purpose of a new\n"
                    "   agent, or the existing agent to rotate. The screen says what they are about to approve: *You\n"
                    "   are creating an agent credential named `<name>` for `<account>`*. **Confirm with SSO** goes to\n"
                    "   `GET /auth/oidc/step-up?purpose=<purpose>` (or `?agent=<name>`). It needs a signed-in browser\n"
                    "   session; any token is refused with `session_required`.\n"
                    "2. That route answers a page that names the credential and the account again, with a link to the\n"
                    "   IdP. Nothing is sent to the IdP until the person follows it. Behind the link is the same\n"
                    "   authorization code flow as a login, with the same `state`, nonce and PKCE checks, plus\n"
                    "   `prompt=login` and `max_age`, so the IdP asks for the person's credentials even if they are\n"
                    "   signed in there. The flow is bound to the person's Atlas session, not to a pre-login one, and\n"
                    "   Atlas records the name being approved beside it.\n"
                    "3. The IdP returns to the usual `/auth/oidc/callback`, so no second redirect URI is registered.\n"
                    "   Atlas checks everything a login checks, and also:\n"
                    "   - the ID token carries `auth_time` (refused otherwise, with the error\n"
                    "     `step_up_auth_time_missing`; see below);\n"
                    "   - `auth_time` is not older than the start of this flow, so a sign-in the IdP answered from an\n"
                    "     older IdP session is refused;\n"
                    "   - `auth_time` is at most `ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS` old (300 by default, 60 to 900);\n"
                    "   - the person who signed in is the person whose session this is, and is still in a required\n"
                    "     group;\n"
                    "   - no later step-up was started in the same session (`step_up_superseded` otherwise).\n"
                    "4. Atlas makes the step-up live (`auth_step_up`; no IdP token is kept) and answers a page that\n"
                    "   says once more what was approved, with a link back to the board. No new session is issued.\n"
                    "5. The credential with **that name**, created in that session, spends it: a first issue or a\n"
                    "   replacement, once, until `auth_time` plus the window. A credential with any other name is\n"
                    "   refused with `403 step_up_agent_mismatch` (the step-up is kept for the approved one).\n"
                    "\n"
                    "**Why the name is part of the approval.** Someone who has stolen a session cookie could send its\n"
                    "owner the step-up link. The pages before and after the IdP both say which credential, for which\n"
                    "account, is being approved, and the mint cannot be turned to another name, so the person is never\n"
                    "asked to approve something they cannot see.\n"
                    "\n"
                    "**Agent names.** A new agent is called `<base>-<purpose>`. For a local account the base is the\n"
                    "username. For an SSO account it is the local part of the person's email, so an Entra or Keycloak\n"
                    "person whose subject is an opaque id still gets a readable name, and the same name that\n"
                    "`atlas-admin token mint` gives. An SSO account with no email yet falls back to its subject.\n"
                    "\n"
                    "**Errors.** A refused step-up is a `login_fail` in `auth_event` with reason `oidc+step-up:<why>`; a\n"
                    "success is `step_up_ok`, naming the agent. A mint without a live step-up is `403\n"
                    "step_up_required`; one for another name is `403 step_up_agent_mismatch`.\n"
                    "\n"
                    "**`step_up_auth_time_missing`.** The IdP's ID token has no `auth_time` claim, so Atlas cannot tell\n"
                    "that the sign-in is fresh, and every step-up is refused. The person sees a page naming this error,\n"
                    "and atlas-api logs a warning telling the operator what to change. Fix it at the IdP: with Entra\n"
                    "ID, add the optional claim `auth_time` to the ID token (app registration, Token configuration);\n"
                    "Keycloak sends it by default. The `auth_event` reason is `oidc+step-up:auth_time_missing`.\n"
                    "\n"
                    "Local accounts, including break-glass owners, keep the password prompt unchanged. An SSO account\n"
                    "that still holds a password from before `link-idp` is asked for the IdP sign-in, not the password.\n"
                    "\n"
                    "### A personal password for SSO people\n"
                    "\n"
                    "An account made with `atlas-admin user add` (a temporary password) and then `user link-idp` signs\n"
                    "in through SSO with the \"must choose a password\" flag still set. With `ATLAS_OIDC_STEP_UP=on`:\n"
                    "\n"
                    "1. `GET /api/me` carries `password_setup` (`step_up: \"sso\"`, the step-up route, the time left on a\n"
                    "   confirmed step-up, and the window). It is absent for everyone else, including SSO-only accounts.\n"
                    "2. **Set a password**, then **Confirm with SSO**, goes to `GET /auth/oidc/step-up?password=1`. The\n"
                    "   page says *You are setting a personal Atlas password for `<account>`, without the temporary\n"
                    "   one*, with the link to the IdP. An account with nothing pending (SSO-only, or one that already\n"
                    "   set its password) is refused with `400 password_not_pending` before any sign-in.\n"
                    "3. Back on the board, within the window, `POST /api/me/password` with `new_password` and\n"
                    "   `confirm_password` sets it and spends the step-up. The usual password rules apply (`400\n"
                    "   password_policy`, `400 password_mismatch`; neither spends the step-up). The flag is cleared, the\n"
                    "   account's other sessions are ended, and `auth_event` records `password_changed` with reason\n"
                    "   `sso_step_up`.\n"
                    "\n"
                    "**Refusals.** Without a live step-up the answer is `403 step_up_required`, naming `step_up_url`,\n"
                    "recorded as a `login_fail` with reason `password_set:step_up_required`. A step-up approved for an\n"
                    "agent credential or a setting cannot set a password, and one approved for a password cannot do\n"
                    "either of those (`403 step_up_required`, reason `<route>:step_up_purpose_mismatch`, the step-up kept\n"
                    "for what it approved). Only the session that made the step-up can spend it, and a token, whatever\n"
                    "its scopes, is refused with `403 session_required`. A local account changes its password at\n"
                    "`/auth/password/change`, with the current one (`400 step_up_not_applicable` here).\n"
                    "\n"
                    "The password then signs the account in under its local name where the install allows it. See\n"
                    "[A personal password as a fallback when SSO is down](#a-personal-password-as-a-fallback-when-sso-is-down).\n"
                    "\n"
                    "### Configuration changes by SSO owners\n"
                    "\n"
                    "With `ATLAS_CONFIG_WRITES=1`, an owner changes a few settings from the board's Configuration tab\n"
                    "(see The configuration panel (document *Configuration panel guide* on node `atlas-system-config`)), and every change re-authenticates: a local\n"
                    "owner re-enters their password each time. An owner who signs in only through SSO has no Atlas\n"
                    "password. Nobody becomes an owner through the IdP, so such an owner is a local owner moved to SSO\n"
                    "with `atlas-admin user link-idp`. With `ATLAS_OIDC_STEP_UP=on`, they confirm each change through\n"
                    "the same step-up as an agent credential:\n"
                    "\n"
                    "1. **Change**, then **Confirm with SSO**, goes to `GET /auth/oidc/step-up?config=<setting>`. Atlas\n"
                    "   first refuses what the change itself would refuse: a member (`403 forbidden`), an unknown\n"
                    "   setting (`404`), a bootstrap setting, a secret, or one not marked changeable (`403`, each with\n"
                    "   its named error), and `config_writes_off` while `ATLAS_CONFIG_WRITES` is off.\n"
                    "2. The page says *You are changing the setting `<setting>` of this Atlas install, as `<account>`*,\n"
                    "   with the link to the IdP. The flow, its checks and the window are the ones above.\n"
                    "3. Back on the board, `GET /api/config` reports `writes.step_up: \"sso\"`, the confirmed setting and\n"
                    "   the time left. `PUT /api/config/<setting>` then needs no password, and spends the step-up.\n"
                    "\n"
                    "**The step-up is bound to one setting and one purpose.** It approves a change of the setting it\n"
                    "names, once, in the session that made it. A change of another setting is refused with `403\n"
                    "step_up_setting_mismatch`. A step-up approved for an agent credential cannot change a setting, and\n"
                    "one approved for a setting cannot create an agent credential: each is refused with `403\n"
                    "step_up_required`, recorded with the reason `<route>:step_up_purpose_mismatch`. In every refusal the\n"
                    "step-up is kept for what it approved. A change that leaves the value as it was spends nothing.\n"
                    "\n"
                    "**Errors.** A change without a live step-up is `403 step_up_required`, naming `step_up_url`, and is\n"
                    "a `login_fail` in `auth_event` with reason `config_write:step_up_required`. A success is\n"
                    "`step_up_ok` with detail `config=<setting>`, and the change itself is a `node.config.set` event as\n"
                    "for any owner. A password an SSO owner sends is ignored.\n"
                    "\n"
                    "Without step-up, an SSO owner cannot pass the password prompt and changes configuration in the\n"
                    "server's environment instead. Local owners, including break-glass owners, keep the password prompt unchanged.\n"
                    "\n"
                    "**Without step-up**, or for a person who cannot use the board, an operator mints the token:\n"
                    "\n"
                    "```bash\n"
                    "docker compose exec atlas-api atlas-admin token mint --user <idp>:<subject> \\\n"
                    "    --purpose claude-code --scopes read --out /path/to/new-token-file\n"
                    "```\n"
                    "\n"
                    "Once the person has signed in through SSO at least once, `--user` also accepts their email. The same\n"
                    "rules apply as in Settings: the scopes are capped by the person's role, and the token must have an\n"
                    "expiry. The raw token is written only to the new file named by `--out`, never printed.\n"
                    "\n"
                    "Agents (document *Agents guide* on node `atlas-system-clients-claude-code`) covers the client side.\n"
                    "\n"
                    "Operators can also create agents with `atlas-admin bot add`, `bot link` and `token add`.\n"
                    "\n"
                    "**What it may see** (RBAC off): its person's grants plus its own. It never gets its person's\n"
                    "instance role. An owner's agent therefore sees shared projects and whatever has been granted\n"
                    "explicitly, not everything its owner sees. This is deliberate: linking an agent to an owner makes\n"
                    "the agent less capable, not more.\n"
                    "\n"
                    "**What it may do** (RBAC on): `min(scope cap, person's project role)`, decided per project on every\n"
                    "request.\n"
                    "\n"
                    "| Token scope | Caps the agent at |\n"
                    "|---|---|\n"
                    "| `read` | `viewer` |\n"
                    "| `write` | `editor` |\n"
                    "| `admin` | `maintainer` |\n"
                    "\n"
                    "The agent inherits its person's direct and group bindings and grants, and never their instance role.\n"
                    "**No token ever exercises `owner`**, so granting roles is always a human act. An agent with an\n"
                    "`admin` token deletes only where its person is a maintainer. An agent with no linked person has its\n"
                    "own direct bindings only, and no group membership.\n"
                    "\n"
                    "**Scopes still gate writes in both modes.** A `read` token cannot write, and access administration\n"
                    "and deletes always need `admin`.\n"
                    "\n"
                    "**When the person leaves:**\n"
                    "\n"
                    "- Disabling a person disables every agent linked to them.\n"
                    "- For an SSO person, the agents' tokens are also suspended once the person has gone\n"
                    "  `ATLAS_OIDC_REVALIDATE_DAYS` without an SSO login. The tokens are not deleted, and the person's\n"
                    "  next login lifts the suspension.\n"
                    "- Removing the person from the required group in the IdP stops their next login. That stops the\n"
                    "  revalidation, so their agents' tokens lapse too.\n"
                    "\n"
                    "**IdP access tokens (opt-in).** With `ATLAS_OAUTH_RESOURCE` set, which needs OIDC, an MCP client may\n"
                    "present an access token issued by the IdP instead of an `atlas_svc_` token. Atlas validates it in\n"
                    "the API: signature, issuer, an audience that is the MCP resource, and expiry. It maps the IdP scopes\n"
                    "to `read`, `write` or `admin` with `ATLAS_OAUTH_SCOPE_MAP`, capped by the person's role. It acts\n"
                    "through one agent account per person and OAuth client, created on first use and linked to the\n"
                    "person. The same rules apply: scope cap ∩ project role, and the same offboarding. A token for\n"
                    "someone who has never signed in with SSO is refused. A pending password change does not refuse\n"
                    "it. The IdP has verified the person, so their token follows the rule of their SSO session, with\n"
                    "the scopes it would otherwise get.\n"
                    "\n"
                    "**What is recorded.** Every write is in `graph_event` under the agent's account. With\n"
                    "`ATLAS_AUDIT_READS=1`, every node an agent reads is in `audit_read`, with the person, the agent, the\n"
                    "token and the MCP tool. See Security.\n"
                ),
                "from": "docs/oss/guides/identity-and-access.md",
            },
        ],
        "abstract": "People sign in with a password or through an OIDC identity provider. Every agent has its own account, linked to the person who is accountable for it, and acts with its own token; tokens carry scopes (read, write, admin) that bound what they may do. Identity-provider access tokens can act as an agent of the person they were issued to.",
    },
    {
        "id": "atlas-system-access",
        "type": "capability",
        "parent": "atlas-system",
        "name": "Visibility and project roles",
        "summary": "Who sees what: private and shared marks, grants, groups, and project roles when they are switched on.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "overview.md",
                "title": "Overview",
                "kind": "architecture",
                "content": (
                    "# Visibility and project roles\n"
                    "\n"
                    "Who sees what: private and shared marks, grants, groups, and project roles when they are switched on.\n"
                    "\n"
                    "Visibility is resolved inside every query, so a hidden node is absent rather than filtered after the fact, and rollups never count what the caller cannot see. With project roles on, each project is shared or private and a person holds viewer, editor, maintainer or owner on it, directly or through a group.\n"
                    "\n"
                    "## Surface\n"
                    "\n"
                    "Generated: the parts of Atlas that serve this capability.\n"
                    "\n"
                    "- API area `atlas-system-api-access` (Access routes)\n"
                    "- command group `atlas-admin rbac` (`atlas-system-cli-rbac`)\n"
                ),
            },
            {
                "file": "configuration.md",
                "title": "Configuration",
                "kind": "runbook",
                "content": (
                    "# Configuration\n"
                    "\n"
                    "The settings of Visibility and project roles, from `backend/settings_catalog.py`. Every setting, with its default and where it is read, is in the configuration reference (CONFIG.md); an owner sees the effective values in the board's Config tab.\n"
                    "\n"
                    "| Setting | Kind | Read by | What it is |\n"
                    "|---|---|---|---|\n"
                    "| `ATLAS_RBAC` | plain | atlas-api | Project roles: off or projects. |\n"
                    "| `ATLAS_DEFAULT_PROJECT_VISIBILITY` | plain | atlas-api | Whether a new project is shared or private under project roles. |\n"
                    "| `ATLAS_SHARED_PROJECT_ROLE` | plain | atlas-api | The role every account holds on a shared project under project roles. |\n"
                    "| `ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER` | plain | atlas-api | Whether an instance owner is implicitly owner of every project. |\n"
                ),
            },
            {
                "file": "identity-and-access-guide.md",
                "title": "Identity and access guide",
                "kind": "runbook",
                "content": (
                    "# Identity and access\n"
                    "\n"
                    "This guide covers who can sign in to Atlas, how they prove it, and what each person and agent can\n"
                    "see and change. The security controls underneath are described in Security. The\n"
                    "decision record behind most of this page is ADR 0041 (project-scoped RBAC, IdP groups, OIDC login).\n"
                    "\n"
                    "Everything on this page beyond local accounts is **opt-in**. With none of the settings below, an\n"
                    "install has local accounts only, two instance roles, and projects shared by default. The OSS\n"
                    "`deploy/oss/.env.example` opts in to the team settings, and each section says which variable does\n"
                    "what.\n"
                    "\n"
                    "| Setting | Default | Team install | Section |\n"
                    "|---|---|---|---|\n"
                    "| `ATLAS_AUTH_PROVIDERS` | `local` | `oidc` or `local,oidc` | [OIDC](#single-sign-on-with-oidc) |\n"
                    "| `ATLAS_LOCAL_LOGIN` | `all` | `break-glass` | [Break-glass](#break-glass-accounts) |\n"
                    "| `ATLAS_RBAC` | `off` | `projects` | [RBAC](#roles-on-projects-rbac) |\n"
                    "| `ATLAS_DEFAULT_PROJECT_VISIBILITY` | `shared` | `private` | [Visibility](#project-visibility) |\n"
                    "| `ATLAS_SHARED_PROJECT_ROLE` | `editor` | `viewer` | [Visibility](#project-visibility) |\n"
                    "| `ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER` | `true` | `false` | [Instance roles](#instance-roles) |\n"
                    "\n"
                    "A malformed value in any of these stops the boot, and the error names the variable.\n"
                    "\n"
                    "## Accounts\n"
                    "\n"
                    "There are two kinds of account, both in one table:\n"
                    "\n"
                    "- **People.** A person signs in with a password (a local account) or through the identity provider\n"
                    "  (an OIDC account).\n"
                    "- **Agents.** An agent is an `is_service` account with no usable password. It authenticates with an\n"
                    "  `atlas_svc_` token and normally belongs to a person (see\n"
                    "  [What an agent can do](#what-an-agent-can-do-as-its-user)).\n"
                    "\n"
                    "Each account is keyed on the pair (identity provider, subject), for example `local` plus a\n"
                    "username, or `oidc` plus the IdP's stable id. It is never keyed on an email address or a display\n"
                    "name. Usernames are permanent.\n"
                    "\n"
                    "### Local accounts\n"
                    "\n"
                    "Local accounts are made by an operator. There is no self-registration and no reset-by-email.\n"
                    "\n"
                    "```bash\n"
                    "docker compose exec atlas-api atlas-admin user add example-user --role member --email user@example.com\n"
                    "```\n"
                    "\n"
                    "The command prints a temporary password once. At first sign-in the person must choose their own\n"
                    "password before they can do anything else. That requirement is enforced at the gate, on every\n"
                    "request. Passwords must be at least 8 characters, and they are checked against a bundled list of\n"
                    "breached passwords.\n"
                    "\n"
                    "Other `atlas-admin user` commands:\n"
                    "\n"
                    "- `reset-password` issues a new temporary password and ends that person's sessions. It does not\n"
                    "  touch their agents' tokens.\n"
                    "- `disable` stops the person and every agent linked to them. `enable` deliberately does not\n"
                    "  re-enable the agents.\n"
                    "- `list` and `show` print accounts and never print a secret.\n"
                    "\n"
                    "### Instance roles\n"
                    "\n"
                    "Every account has one instance role:\n"
                    "\n"
                    "- **`owner`** administers the instance: accounts, groups, access, and the audit export.\n"
                    "- **`member`** confers nothing beyond what visibility and project roles give.\n"
                    "\n"
                    "An unrecognised stored value resolves to `member`. Accounts created through OIDC are always\n"
                    "`member`. Nobody becomes an owner through the identity provider.\n"
                    "\n"
                    "With project RBAC on, an owner is also the instance admin, who may bind roles on any project. While\n"
                    "`ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER=true` (the default), an owner is also treated as\n"
                    "`owner` on every project. Set it to `false` and an admin who needs to read a project binds\n"
                    "themselves first, and that binding is audited.\n"
                    "\n"
                    "## Single sign-on with OIDC\n"
                    "\n"
                    "With `oidc` in `ATLAS_AUTH_PROVIDERS`, Atlas is a confidential OpenID Connect client of one issuer\n"
                    "(`backend/identity/oidc.py`). The login page shows **Sign in with SSO**. With `local,oidc` it also\n"
                    "shows the password form.\n"
                    "\n"
                    "**The flow** is the authorization code flow with PKCE (S256), `state` and `nonce`:\n"
                    "\n"
                    "1. `GET /auth/oidc/start` stores the flow server-side: a digest of `state`, the nonce and the PKCE\n"
                    "   verifier. The flow is bound to the browser's pre-login session and lives for ten minutes.\n"
                    "2. The IdP redirects back to `/auth/oidc/callback`. The flow row is deleted on first sight, so a\n"
                    "   replayed callback finds nothing, and a callback in another browser finds no flow.\n"
                    "3. Atlas redeems the code with the verifier and its client credential: `client_secret_basic`,\n"
                    "   `client_secret_post`, or `private_key_jwt`.\n"
                    "4. The ID token is verified:\n"
                    "   - The signature must be from the issuer's JWKS, with an asymmetric algorithm only (never `none`,\n"
                    "     never HS*).\n"
                    "   - `iss` must equal the issuer exactly.\n"
                    "   - `aud` must contain the client id, and `azp` must match it when present.\n"
                    "   - `exp`, `iat` and `sub` must be present, with 60 seconds of leeway.\n"
                    "   - The nonce is compared in constant time.\n"
                    "5. Atlas issues its own `__Host-` session, as a password login does. The IdP's tokens are used\n"
                    "   once, in memory, and never stored.\n"
                    "\n"
                    "Every refusal gives the caller the same generic failure. The reason goes to `auth_event`. Discovery\n"
                    "is fetched lazily and cached, so Atlas boots, and break-glass login works, while the IdP is down.\n"
                    "\n"
                    "**Settings.** The full list, with validation rules, is in Configuration.\n"
                    "\n"
                    "| Variable | Meaning |\n"
                    "|---|---|\n"
                    "| `ATLAS_OIDC_ISSUER` | the `https` issuer URL; discovery is read from it |\n"
                    "| `ATLAS_OIDC_CLIENT_ID` | Atlas's client id at the IdP |\n"
                    "| `ATLAS_OIDC_CLIENT_SECRET_FILE` | the client secret, read **only** from a file |\n"
                    "| `ATLAS_OIDC_CLIENT_KEY_FILE`, `ATLAS_OIDC_CLIENT_KEY_ID` | the private key and its id for `private_key_jwt`, instead of a secret |\n"
                    "| `ATLAS_OIDC_TOKEN_AUTH` | `client_secret_basic`, `client_secret_post` or `private_key_jwt` |\n"
                    "| `ATLAS_OIDC_REDIRECT_URI` | `https://<host>/auth/oidc/callback`; derived from `ATLAS_PUBLIC_URL` if unset |\n"
                    "| `ATLAS_OIDC_PRESET` | `generic`, `entra` or `keycloak`: which claims hold the subject, name, email and groups |\n"
                    "| `ATLAS_OIDC_NAME_CLAIM`, `_EMAIL_CLAIM`, `_GROUPS_CLAIM` | override one claim name of the preset |\n"
                    "| `ATLAS_OIDC_REQUIRED_GROUPS` | IdP group values; see below |\n"
                    "| `ATLAS_OIDC_GROUP_MAP_FILE` | the group allowlist; see [IdP group sync](#idp-group-sync) |\n"
                    "| `ATLAS_OIDC_MAX_SESSION_HOURS` | absolute lifetime of an SSO session (12) |\n"
                    "| `ATLAS_OIDC_REVALIDATE_DAYS` | how long an SSO person's agent tokens keep working without a fresh login (30) |\n"
                    "| `ATLAS_OIDC_STEP_UP`, `ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS` | SSO people create their own agent credentials, and SSO owners change configuration, after a fresh IdP sign-in (off; 300 s); see [Agent credentials for SSO people](#agent-credentials-for-sso-people) and [Configuration changes by SSO owners](#configuration-changes-by-sso-owners) |\n"
                    "\n"
                    "**Required groups and just-in-time accounts.** Once `ATLAS_OIDC_REQUIRED_GROUPS` is set, every SSO\n"
                    "login, not only the first, needs at least one of those groups in the token. A leaver removed from\n"
                    "the group is refused at their next login. A person with no Atlas account who passes that check is\n"
                    "created on the spot as a `member`. If `ATLAS_OIDC_REQUIRED_GROUPS` is unset, no account is ever\n"
                    "created by a login, and only existing linked accounts can sign in through SSO.\n"
                    "\n"
                    "**Moving a local person to SSO.** `atlas-admin user link-idp <username> --subject <id>` points an\n"
                    "existing local account at an IdP identity. The person keeps their history and agents. Their old\n"
                    "username is kept as the account's **local sign-in name**, which only matters for the\n"
                    "[password fallback](#a-personal-password-as-a-fallback-when-sso-is-down). Add `--no-local-name`\n"
                    "for an account that must never sign in with a password.\n"
                    "\n"
                    "**SSO-only, or SSO with a personal password.** An account for someone who will sign in through SSO\n"
                    "is made in one of two ways:\n"
                    "\n"
                    "- **SSO-only:** `atlas-admin user add <name> --no-password`, then `user link-idp`. The account has\n"
                    "  no Atlas password at all and never gets one.\n"
                    "- **SSO with a personal password:** `atlas-admin user add <name>` as usual, then `user link-idp`.\n"
                    "  The temporary password the first command printed is never used: hand it to nobody. The account\n"
                    "  keeps the \"must choose a password\" flag, and `link-idp` does not clear it.\n"
                    "\n"
                    "The forced password change applies to **password sign-ins only**. Atlas records how each session\n"
                    "began (`auth_session.auth_method`: `password`, `sso`, or `minted` for `session mint`). The gate\n"
                    "reads that, not the kind of account. A session made through the IdP\n"
                    "reaches the board even while the flag is set. In that session the board shows a notice, *You have\n"
                    "no personal password yet*, which can be dismissed. **Set a password** sends the person through a\n"
                    "fresh IdP sign-in (the [step-up](#agent-credentials-for-sso-people), so `ATLAS_OIDC_STEP_UP=on` is\n"
                    "needed; without it the notice says the board cannot set one), and back on the board they choose the\n"
                    "password without the temporary one. See\n"
                    "[A personal password for SSO people](#a-personal-password-for-sso-people).\n"
                    "\n"
                    "An account that signs in with its temporary password is still sent to the change page first. That\n"
                    "holds for a local account, as before, and for a linked account signing in under its local name (see\n"
                    "the fallback below). Installs without OIDC are unchanged.\n"
                    "\n"
                    "### A personal password as a fallback when SSO is down\n"
                    "\n"
                    "A linked account whose person has set a personal password (see\n"
                    "[A personal password for SSO people](#a-personal-password-for-sso-people)) can sign in with its\n"
                    "**local sign-in name** and that password. This is the fallback for when the IdP is down, and for\n"
                    "testing without SSO. Whether it is allowed depends on the providers and on `ATLAS_LOCAL_LOGIN`:\n"
                    "\n"
                    "| `ATLAS_AUTH_PROVIDERS` | `ATLAS_LOCAL_LOGIN` | A linked account's password |\n"
                    "|---|---|---|\n"
                    "| `local,oidc` | `all` (the default with `local`) | signs in under its local name, if the account has a password |\n"
                    "| `local,oidc` | `break-glass` | signs in only if the account is flagged break-glass, at `/auth/login?break_glass=1`; each use writes its own `login_break_glass` row |\n"
                    "| `oidc` | `break-glass` (implied) | does not sign in |\n"
                    "| `local` (no OIDC) | `all` | does not sign in; nothing changes |\n"
                    "\n"
                    "- **The local name.** `user link-idp` keeps the username the account had before it was linked. An\n"
                    "  account linked before this existed, or created by a first SSO login, gets one with\n"
                    "  `atlas-admin user set-local-name <idp>:<subject> <name>`. `user set-local-name <account> --clear`\n"
                    "  removes it. Both are `user_local_name` rows in `auth_event`.\n"
                    "- **One namespace.** A local name is a username. `set-local-name` refuses a name that a local account or an\n"
                    "  agent already has, or that another linked account uses as its local name. `user add` and\n"
                    "  `bot add` refuse a linked account's local name. If a local account and a local name ever clash\n"
                    "  anyway, the local account wins its own name, and the linked account cannot use the fallback.\n"
                    "- **What it is.** A password sign-in. The session is a `password` session, so a temporary password\n"
                    "  forces the change first, and an SSO-only account (`--no-password`) still has no password at all.\n"
                    "  `login_ok` carries `local_name=<name>` in its detail.\n"
                    "- **Offboarding.** Removing the person at the IdP does not stop a password. Disable the account in\n"
                    "  Atlas too (`user disable`), or clear its local name. A password sign-in is not an IdP login, so it\n"
                    "  does not lift the [agent token suspension](#what-an-agent-can-do-as-its-user).\n"
                    "- **Where it shows.** `user list` adds a `local_name` column once any account has one. For an\n"
                    "  IdP-backed account, `user show --json` has a `local_name` key. Admin verbs also accept the local\n"
                    "  name to find the account.\n"
                    "\n"
                    "### Microsoft Entra ID\n"
                    "\n"
                    "- Set `ATLAS_OIDC_PRESET=entra`. The issuer must be a **tenant** issuer,\n"
                    "  `https://login.microsoftonline.com/<tenant-id>/v2.0`. The multi-tenant `common` and\n"
                    "  `organizations` issuers are refused, because they would let another tenant's users in.\n"
                    "- The subject is the user's object id (`oid`). The token's tenant id (`tid`) must equal the\n"
                    "  issuer's tenant, or the login is refused. For `link-idp`, pass the user's Object ID.\n"
                    "- With `private_key_jwt`, set `ATLAS_OIDC_CLIENT_KEY_ID` to the certificate's base64url SHA-1\n"
                    "  thumbprint. Atlas sends it as `x5t`.\n"
                    "- With `ATLAS_OIDC_STEP_UP=on`, add the optional claim `auth_time` to the ID token in the app\n"
                    "  registration (Token configuration). Without it every step-up is refused with\n"
                    "  `step_up_auth_time_missing` (see [Agent credentials for SSO people](#agent-credentials-for-sso-people)).\n"
                    "- **Group overage.** Past about 200 groups, Entra leaves the groups claim out and sends a pointer\n"
                    "  instead. Atlas cannot tell which groups that hides, so it refuses the login (fail closed). In the\n"
                    "  app registration, emit only the groups assigned to the application.\n"
                    "- In the group map, key Entra groups by their object id.\n"
                    "\n"
                    "### Keycloak\n"
                    "\n"
                    "- Set `ATLAS_OIDC_PRESET=keycloak` and use the realm issuer,\n"
                    "  `https://<keycloak-host>/realms/<realm>`.\n"
                    "- The subject is `sub`. The name comes from `name`, falling back to `preferred_username`. The\n"
                    "  email comes from `email`.\n"
                    "- Add a group membership mapper so the ID token carries a `groups` claim. The map file keys\n"
                    "  Keycloak groups by the values that claim carries, typically the group path, such as\n"
                    "  `/atlas/maintainers`.\n"
                    "\n"
                    "### Break-glass accounts\n"
                    "\n"
                    "With SSO on, local password login stays open to every local account by default\n"
                    "(`ATLAS_LOCAL_LOGIN=all`). For a team install, set `ATLAS_LOCAL_LOGIN=break-glass`. Then only\n"
                    "local accounts created with the break-glass flag can use a password:\n"
                    "\n"
                    "```bash\n"
                    "docker compose exec atlas-api atlas-admin user add breakglass --role owner --break-glass\n"
                    "```\n"
                    "\n"
                    "- The password form is no longer linked from the login page. It is reachable only at\n"
                    "  `/auth/login?break_glass=1`.\n"
                    "- Every break-glass login writes its own `login_break_glass` row to `auth_event`.\n"
                    "- Keep the credential in your organisation's vault. It is how you administer Atlas when the IdP is\n"
                    "  down or misconfigured.\n"
                    "\n"
                    "If `local` is not listed in `ATLAS_AUTH_PROVIDERS`, `ATLAS_LOCAL_LOGIN` defaults to `break-glass`.\n"
                    "\n"
                    "A break-glass owner who is later linked keeps the flag. With `local,oidc` and\n"
                    "`ATLAS_LOCAL_LOGIN=break-glass`, it signs in under its local name\n"
                    "([the fallback](#a-personal-password-as-a-fallback-when-sso-is-down)).\n"
                    "\n"
                    "## IdP group sync\n"
                    "\n"
                    "Groups are where access is managed at scale (`backend/identity/groups.py`).\n"
                    "\n"
                    "- **Local groups** are made by an instance owner, in the access screen or with\n"
                    "  `POST /api/access/groups` and `PUT /api/access/groups/{id}/members`. They suit installs without an\n"
                    "  IdP, and exceptions the IdP does not model.\n"
                    "- **IdP groups** are synchronised **at every SSO login**. The person's IdP memberships are replaced\n"
                    "  with what the token's groups claim says. Local memberships are never touched by sync.\n"
                    "- **The allowlist** in `ATLAS_OIDC_GROUP_MAP_FILE` is the only way an IdP group reaches Atlas. A\n"
                    "  claim value that is not in the map is ignored and never created. The file is YAML (or JSON):\n"
                    "\n"
                    "  ```yaml\n"
                    "  # IdP group value -> Atlas group name\n"
                    "  groups:\n"
                    "    \"3f6c1c1e-6f4d-4a59-9a7e-0f1c2d3e4f50\": team-alpha      # Entra: the group's object id\n"
                    "    \"/atlas/maintainers\": maintainers                       # Keycloak: the group path\n"
                    "  ```\n"
                    "\n"
                    "  A mapped Atlas group that does not exist yet is created. A mapping onto an existing group adds\n"
                    "  IdP memberships to it. With no map file, nothing is synchronised, and a person's IdP memberships\n"
                    "  are emptied at their next login.\n"
                    "- Every membership added or removed is written to `auth_event`.\n"
                    "- Sync is part of the login. A membership change in the IdP takes effect at the person's next\n"
                    "  login, so it is bounded by `ATLAS_OIDC_MAX_SESSION_HOURS`.\n"
                    "\n"
                    "Sync creates memberships, not access. Bind the group to projects as described in the next section.\n"
                    "\n"
                    "## Roles on projects (RBAC)\n"
                    "\n"
                    "Set `ATLAS_RBAC=projects` to turn on project-scoped roles (`backend/rbac.py`). With it `off`, the\n"
                    "default, none of this code runs. The instance roles and per-user grants then work as described under\n"
                    "[Project visibility](#project-visibility).\n"
                    "\n"
                    "**The project is the unit.** A node's project is its nearest `contains` ancestor of type `project`,\n"
                    "counting the node itself. A root of any other type is its own project. A work item follows the node\n"
                    "it works on. A node with several parents belongs to all their projects, and every check must pass\n"
                    "on all of them. A node whose project cannot be resolved has no project, and so no role on it: it is\n"
                    "refused.\n"
                    "\n"
                    "**Four ordered roles**, `viewer < editor < maintainer < owner`:\n"
                    "\n"
                    "| Role | May |\n"
                    "|---|---|\n"
                    "| `viewer` | read every node, document, intent, event and search result in the project |\n"
                    "| `editor` | also create and change nodes, work items, edges, documents, intents, acceptance and results |\n"
                    "| `maintainer` | also **delete** nodes and edges, and grant roles up to `maintainer` |\n"
                    "| `owner` | also grant `owner`, and delete the project root |\n"
                    "\n"
                    "An unrecognised stored role resolves to no role, not to `viewer`.\n"
                    "\n"
                    "**Roles are granted to groups**, and to individual people, with\n"
                    "`PUT /api/access/project/{project_id}/bindings` or the access screen. Only a signed-in person can do\n"
                    "this, never a token. Who may grant:\n"
                    "\n"
                    "- an instance owner may grant anything;\n"
                    "- a project maintainer may grant up to `maintainer`;\n"
                    "- a project owner may grant up to `owner`.\n"
                    "\n"
                    "**A person's role on a project** is the highest of:\n"
                    "\n"
                    "- their own bindings on it;\n"
                    "- the bindings of every group they belong to;\n"
                    "- `editor`, if they hold a per-user grant (`node_grant`) on the project root;\n"
                    "- `ATLAS_SHARED_PROJECT_ROLE`, if the project is shared.\n"
                    "\n"
                    "### Who owns a new project\n"
                    "\n"
                    "**The person who creates a project root owns it.** With RBAC on, creating a node that is a project\n"
                    "root binds its creator `owner` on that project, in the same transaction. A person is bound themself.\n"
                    "An agent's **linked person** is bound, never the agent, so the person sees what their agent made and\n"
                    "the agent works there within its token's cap. Two creations make a root:\n"
                    "\n"
                    "- `POST /api/node`, which creates no edge, so the node is a root until a `contains` edge gives it a\n"
                    "  parent;\n"
                    "- `POST /api/workitem` with neither `parent` nor `binding`, because an unbound work item is its own\n"
                    "  project. This is how the board files work and how an agent's `atlas_file_work` does.\n"
                    "\n"
                    "**An agent with no linked person cannot create a project root.** It gets a `403` with\n"
                    "`\"reason\": \"agent_not_linked_to_a_person\"` and nothing is written. Link it to a person\n"
                    "(`atlas-admin bot link`). A work item can instead be filed under a parent or a binding, and is then\n"
                    "not a root. `POST /api/node` takes no parent, so for a node linking the agent is the only way.\n"
                    "\n"
                    "Each of these bindings is recorded in `auth_event` as `project_role_bound`, the same entry the\n"
                    "bindings screen writes, with the caller (the agent, if an agent created it) as the actor. A node\n"
                    "that becomes a root later, because its last parent edge was deleted, gets no automatic owner: an\n"
                    "instance admin binds one. With RBAC off, creation binds nobody, as before.\n"
                    "\n"
                    "The reason: a root is its own project, and on a private-by-default install a project nobody is bound\n"
                    "to is unreachable, so without this rule a create would answer `201` and the next read of the same\n"
                    "node `404`. The person, not the agent, is bound because the person answers for what their agent\n"
                    "makes, and no token ever exercises `owner`: the agent stays capped by its token's scope.\n"
                    "\n"
                    "**Deleting needs `maintainer`** on the project of every row removed, and on both ends of an edge.\n"
                    "Deleting a project root needs `owner`. A token's `admin` scope alone is not enough.\n"
                    "\n"
                    "A refused write is a `403` naming the role needed and the role held. A node the caller cannot see is\n"
                    "still a `404`.\n"
                    "\n"
                    "**Turning it on.** Run `atlas-admin rbac plan` first. It writes nothing and prints, for every\n"
                    "principal and project, today's effective permission next to the one RBAC would give. The usual\n"
                    "difference is that agents with an `admin` token can delete only where their person is a\n"
                    "`maintainer`. Add those bindings, then set `ATLAS_RBAC=projects`. Turning it off again restores the\n"
                    "previous behaviour, because nothing was converted.\n"
                    "\n"
                    "## Project visibility\n"
                    "\n"
                    "**RBAC off (the default).** Every node is shared unless a subtree is marked private. A private\n"
                    "subtree is visible to:\n"
                    "\n"
                    "- instance owners;\n"
                    "- people granted it by name (`node_grant`);\n"
                    "- those people's agents.\n"
                    "\n"
                    "An instance owner sets visibility and grants in the access screen, or with\n"
                    "`PUT /api/access/node/{node_id}`, which also needs an `admin` scope from a token.\n"
                    "\n"
                    "**RBAC on.** A project's visibility is the mark on its root, or `ATLAS_DEFAULT_PROJECT_VISIBILITY`\n"
                    "when its root has no mark.\n"
                    "\n"
                    "- **Private project:** only principals with at least `viewer` on it, through a binding, a group or\n"
                    "  a root grant, can see it.\n"
                    "- **Shared project:** every active account gets the role `ATLAS_SHARED_PROJECT_ROLE` there. That is\n"
                    "  `editor` by default, which matches the RBAC-off behaviour. A team install sets `viewer`, so that\n"
                    "  editing needs an explicit binding.\n"
                    "- **Inner subtrees:** a private mark on a subtree inside a project still narrows visibility exactly\n"
                    "  as with RBAC off. Even a project viewer needs a grant to see inside that subtree.\n"
                    "\n"
                    "**A refusal looks like an absence.** A node you may not see answers `404`, the same as an id that\n"
                    "does not exist. Search, listings, counts and rollups are computed over the caller's visible set, so\n"
                    "they do not leak hidden nodes. If an agent insists a project does not exist that its person can see,\n"
                    "check the agent's access before suspecting a bug.\n"
                    "\n"
                    "## What an agent can do as its user\n"
                    "\n"
                    "An agent is its own account, linked to the person it belongs to (`person_user_id`). Every write it\n"
                    "makes is recorded against the agent, and so against that person.\n"
                    "\n"
                    "**Creating one.** A signed-in person opens Settings, Agent credentials, or calls\n"
                    "`POST /api/me/agent-credential`. This creates the agent account, links it to them and mints its\n"
                    "first token, under these rules:\n"
                    "\n"
                    "- It works only from a browser session. A token cannot mint tokens.\n"
                    "- The person must re-enter their password, or, for an SSO person on an install with\n"
                    "  `ATLAS_OIDC_STEP_UP=on`, sign in at the IdP again (below).\n"
                    "- The scopes are capped by the person's instance role: a `member` gets at most `read` and `write`,\n"
                    "  an `owner` can also have `admin`.\n"
                    "- The token must have an expiry. There is no never-expires option.\n"
                    "\n"
                    "### Agent credentials for SSO people\n"
                    "\n"
                    "**IdP sign-in is the default for an SSO install.** With `ATLAS_OAUTH_RESOURCE` set, an MCP client\n"
                    "such as Claude Code signs in through the IdP and needs no minted token (see IdP access tokens\n"
                    "below). For a client that needs a minted `atlas_svc_` token, an SSO person mints it themselves after\n"
                    "a fresh IdP sign-in, once the operator sets `ATLAS_OIDC_STEP_UP=on`.\n"
                    "\n"
                    "**Why a step-up.** Minting re-authenticates the person, because a token minted from a browser\n"
                    "session alone would let a stolen session create a key that outlives it. A local account re-enters\n"
                    "its password. An SSO account has no Atlas password, so without step-up the screen refuses it with\n"
                    "`403 password_incorrect`. With step-up on, it signs in at the IdP again instead:\n"
                    "\n"
                    "1. In Settings, Agent credentials, the person names the credential first: the purpose of a new\n"
                    "   agent, or the existing agent to rotate. The screen says what they are about to approve: *You\n"
                    "   are creating an agent credential named `<name>` for `<account>`*. **Confirm with SSO** goes to\n"
                    "   `GET /auth/oidc/step-up?purpose=<purpose>` (or `?agent=<name>`). It needs a signed-in browser\n"
                    "   session; any token is refused with `session_required`.\n"
                    "2. That route answers a page that names the credential and the account again, with a link to the\n"
                    "   IdP. Nothing is sent to the IdP until the person follows it. Behind the link is the same\n"
                    "   authorization code flow as a login, with the same `state`, nonce and PKCE checks, plus\n"
                    "   `prompt=login` and `max_age`, so the IdP asks for the person's credentials even if they are\n"
                    "   signed in there. The flow is bound to the person's Atlas session, not to a pre-login one, and\n"
                    "   Atlas records the name being approved beside it.\n"
                    "3. The IdP returns to the usual `/auth/oidc/callback`, so no second redirect URI is registered.\n"
                    "   Atlas checks everything a login checks, and also:\n"
                    "   - the ID token carries `auth_time` (refused otherwise, with the error\n"
                    "     `step_up_auth_time_missing`; see below);\n"
                    "   - `auth_time` is not older than the start of this flow, so a sign-in the IdP answered from an\n"
                    "     older IdP session is refused;\n"
                    "   - `auth_time` is at most `ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS` old (300 by default, 60 to 900);\n"
                    "   - the person who signed in is the person whose session this is, and is still in a required\n"
                    "     group;\n"
                    "   - no later step-up was started in the same session (`step_up_superseded` otherwise).\n"
                    "4. Atlas makes the step-up live (`auth_step_up`; no IdP token is kept) and answers a page that\n"
                    "   says once more what was approved, with a link back to the board. No new session is issued.\n"
                    "5. The credential with **that name**, created in that session, spends it: a first issue or a\n"
                    "   replacement, once, until `auth_time` plus the window. A credential with any other name is\n"
                    "   refused with `403 step_up_agent_mismatch` (the step-up is kept for the approved one).\n"
                    "\n"
                    "**Why the name is part of the approval.** Someone who has stolen a session cookie could send its\n"
                    "owner the step-up link. The pages before and after the IdP both say which credential, for which\n"
                    "account, is being approved, and the mint cannot be turned to another name, so the person is never\n"
                    "asked to approve something they cannot see.\n"
                    "\n"
                    "**Agent names.** A new agent is called `<base>-<purpose>`. For a local account the base is the\n"
                    "username. For an SSO account it is the local part of the person's email, so an Entra or Keycloak\n"
                    "person whose subject is an opaque id still gets a readable name, and the same name that\n"
                    "`atlas-admin token mint` gives. An SSO account with no email yet falls back to its subject.\n"
                    "\n"
                    "**Errors.** A refused step-up is a `login_fail` in `auth_event` with reason `oidc+step-up:<why>`; a\n"
                    "success is `step_up_ok`, naming the agent. A mint without a live step-up is `403\n"
                    "step_up_required`; one for another name is `403 step_up_agent_mismatch`.\n"
                    "\n"
                    "**`step_up_auth_time_missing`.** The IdP's ID token has no `auth_time` claim, so Atlas cannot tell\n"
                    "that the sign-in is fresh, and every step-up is refused. The person sees a page naming this error,\n"
                    "and atlas-api logs a warning telling the operator what to change. Fix it at the IdP: with Entra\n"
                    "ID, add the optional claim `auth_time` to the ID token (app registration, Token configuration);\n"
                    "Keycloak sends it by default. The `auth_event` reason is `oidc+step-up:auth_time_missing`.\n"
                    "\n"
                    "Local accounts, including break-glass owners, keep the password prompt unchanged. An SSO account\n"
                    "that still holds a password from before `link-idp` is asked for the IdP sign-in, not the password.\n"
                    "\n"
                    "### A personal password for SSO people\n"
                    "\n"
                    "An account made with `atlas-admin user add` (a temporary password) and then `user link-idp` signs\n"
                    "in through SSO with the \"must choose a password\" flag still set. With `ATLAS_OIDC_STEP_UP=on`:\n"
                    "\n"
                    "1. `GET /api/me` carries `password_setup` (`step_up: \"sso\"`, the step-up route, the time left on a\n"
                    "   confirmed step-up, and the window). It is absent for everyone else, including SSO-only accounts.\n"
                    "2. **Set a password**, then **Confirm with SSO**, goes to `GET /auth/oidc/step-up?password=1`. The\n"
                    "   page says *You are setting a personal Atlas password for `<account>`, without the temporary\n"
                    "   one*, with the link to the IdP. An account with nothing pending (SSO-only, or one that already\n"
                    "   set its password) is refused with `400 password_not_pending` before any sign-in.\n"
                    "3. Back on the board, within the window, `POST /api/me/password` with `new_password` and\n"
                    "   `confirm_password` sets it and spends the step-up. The usual password rules apply (`400\n"
                    "   password_policy`, `400 password_mismatch`; neither spends the step-up). The flag is cleared, the\n"
                    "   account's other sessions are ended, and `auth_event` records `password_changed` with reason\n"
                    "   `sso_step_up`.\n"
                    "\n"
                    "**Refusals.** Without a live step-up the answer is `403 step_up_required`, naming `step_up_url`,\n"
                    "recorded as a `login_fail` with reason `password_set:step_up_required`. A step-up approved for an\n"
                    "agent credential or a setting cannot set a password, and one approved for a password cannot do\n"
                    "either of those (`403 step_up_required`, reason `<route>:step_up_purpose_mismatch`, the step-up kept\n"
                    "for what it approved). Only the session that made the step-up can spend it, and a token, whatever\n"
                    "its scopes, is refused with `403 session_required`. A local account changes its password at\n"
                    "`/auth/password/change`, with the current one (`400 step_up_not_applicable` here).\n"
                    "\n"
                    "The password then signs the account in under its local name where the install allows it. See\n"
                    "[A personal password as a fallback when SSO is down](#a-personal-password-as-a-fallback-when-sso-is-down).\n"
                    "\n"
                    "### Configuration changes by SSO owners\n"
                    "\n"
                    "With `ATLAS_CONFIG_WRITES=1`, an owner changes a few settings from the board's Configuration tab\n"
                    "(see The configuration panel (document *Configuration panel guide* on node `atlas-system-config`)), and every change re-authenticates: a local\n"
                    "owner re-enters their password each time. An owner who signs in only through SSO has no Atlas\n"
                    "password. Nobody becomes an owner through the IdP, so such an owner is a local owner moved to SSO\n"
                    "with `atlas-admin user link-idp`. With `ATLAS_OIDC_STEP_UP=on`, they confirm each change through\n"
                    "the same step-up as an agent credential:\n"
                    "\n"
                    "1. **Change**, then **Confirm with SSO**, goes to `GET /auth/oidc/step-up?config=<setting>`. Atlas\n"
                    "   first refuses what the change itself would refuse: a member (`403 forbidden`), an unknown\n"
                    "   setting (`404`), a bootstrap setting, a secret, or one not marked changeable (`403`, each with\n"
                    "   its named error), and `config_writes_off` while `ATLAS_CONFIG_WRITES` is off.\n"
                    "2. The page says *You are changing the setting `<setting>` of this Atlas install, as `<account>`*,\n"
                    "   with the link to the IdP. The flow, its checks and the window are the ones above.\n"
                    "3. Back on the board, `GET /api/config` reports `writes.step_up: \"sso\"`, the confirmed setting and\n"
                    "   the time left. `PUT /api/config/<setting>` then needs no password, and spends the step-up.\n"
                    "\n"
                    "**The step-up is bound to one setting and one purpose.** It approves a change of the setting it\n"
                    "names, once, in the session that made it. A change of another setting is refused with `403\n"
                    "step_up_setting_mismatch`. A step-up approved for an agent credential cannot change a setting, and\n"
                    "one approved for a setting cannot create an agent credential: each is refused with `403\n"
                    "step_up_required`, recorded with the reason `<route>:step_up_purpose_mismatch`. In every refusal the\n"
                    "step-up is kept for what it approved. A change that leaves the value as it was spends nothing.\n"
                    "\n"
                    "**Errors.** A change without a live step-up is `403 step_up_required`, naming `step_up_url`, and is\n"
                    "a `login_fail` in `auth_event` with reason `config_write:step_up_required`. A success is\n"
                    "`step_up_ok` with detail `config=<setting>`, and the change itself is a `node.config.set` event as\n"
                    "for any owner. A password an SSO owner sends is ignored.\n"
                    "\n"
                    "Without step-up, an SSO owner cannot pass the password prompt and changes configuration in the\n"
                    "server's environment instead. Local owners, including break-glass owners, keep the password prompt unchanged.\n"
                    "\n"
                    "**Without step-up**, or for a person who cannot use the board, an operator mints the token:\n"
                    "\n"
                    "```bash\n"
                    "docker compose exec atlas-api atlas-admin token mint --user <idp>:<subject> \\\n"
                    "    --purpose claude-code --scopes read --out /path/to/new-token-file\n"
                    "```\n"
                    "\n"
                    "Once the person has signed in through SSO at least once, `--user` also accepts their email. The same\n"
                    "rules apply as in Settings: the scopes are capped by the person's role, and the token must have an\n"
                    "expiry. The raw token is written only to the new file named by `--out`, never printed.\n"
                    "\n"
                    "Agents (document *Agents guide* on node `atlas-system-clients-claude-code`) covers the client side.\n"
                    "\n"
                    "Operators can also create agents with `atlas-admin bot add`, `bot link` and `token add`.\n"
                    "\n"
                    "**What it may see** (RBAC off): its person's grants plus its own. It never gets its person's\n"
                    "instance role. An owner's agent therefore sees shared projects and whatever has been granted\n"
                    "explicitly, not everything its owner sees. This is deliberate: linking an agent to an owner makes\n"
                    "the agent less capable, not more.\n"
                    "\n"
                    "**What it may do** (RBAC on): `min(scope cap, person's project role)`, decided per project on every\n"
                    "request.\n"
                    "\n"
                    "| Token scope | Caps the agent at |\n"
                    "|---|---|\n"
                    "| `read` | `viewer` |\n"
                    "| `write` | `editor` |\n"
                    "| `admin` | `maintainer` |\n"
                    "\n"
                    "The agent inherits its person's direct and group bindings and grants, and never their instance role.\n"
                    "**No token ever exercises `owner`**, so granting roles is always a human act. An agent with an\n"
                    "`admin` token deletes only where its person is a maintainer. An agent with no linked person has its\n"
                    "own direct bindings only, and no group membership.\n"
                    "\n"
                    "**Scopes still gate writes in both modes.** A `read` token cannot write, and access administration\n"
                    "and deletes always need `admin`.\n"
                    "\n"
                    "**When the person leaves:**\n"
                    "\n"
                    "- Disabling a person disables every agent linked to them.\n"
                    "- For an SSO person, the agents' tokens are also suspended once the person has gone\n"
                    "  `ATLAS_OIDC_REVALIDATE_DAYS` without an SSO login. The tokens are not deleted, and the person's\n"
                    "  next login lifts the suspension.\n"
                    "- Removing the person from the required group in the IdP stops their next login. That stops the\n"
                    "  revalidation, so their agents' tokens lapse too.\n"
                    "\n"
                    "**IdP access tokens (opt-in).** With `ATLAS_OAUTH_RESOURCE` set, which needs OIDC, an MCP client may\n"
                    "present an access token issued by the IdP instead of an `atlas_svc_` token. Atlas validates it in\n"
                    "the API: signature, issuer, an audience that is the MCP resource, and expiry. It maps the IdP scopes\n"
                    "to `read`, `write` or `admin` with `ATLAS_OAUTH_SCOPE_MAP`, capped by the person's role. It acts\n"
                    "through one agent account per person and OAuth client, created on first use and linked to the\n"
                    "person. The same rules apply: scope cap ∩ project role, and the same offboarding. A token for\n"
                    "someone who has never signed in with SSO is refused. A pending password change does not refuse\n"
                    "it. The IdP has verified the person, so their token follows the rule of their SSO session, with\n"
                    "the scopes it would otherwise get.\n"
                    "\n"
                    "**What is recorded.** Every write is in `graph_event` under the agent's account. With\n"
                    "`ATLAS_AUDIT_READS=1`, every node an agent reads is in `audit_read`, with the person, the agent, the\n"
                    "token and the MCP tool. See Security.\n"
                ),
                "from": "docs/oss/guides/identity-and-access.md",
            },
        ],
        "abstract": "Visibility is resolved inside every query, so a hidden node is absent rather than filtered after the fact, and rollups never count what the caller cannot see. With project roles on, each project is shared or private and a person holds viewer, editor, maintainer or owner on it, directly or through a group.",
    },
    {
        "id": "atlas-system-audit",
        "type": "capability",
        "parent": "atlas-system",
        "name": "Audit trail",
        "summary": "Every graph write as an event with its actor, sign-in events, and an optional audit of agent reads.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "overview.md",
                "title": "Overview",
                "kind": "architecture",
                "content": (
                    "# Audit trail\n"
                    "\n"
                    "Every graph write as an event with its actor, sign-in events, and an optional audit of agent reads.\n"
                    "\n"
                    "Graph writes are recorded by one writer as graph events naming the person and the agent. Sign-in and grant changes are recorded separately. With read auditing on, every agent read of a node, a search or a document is recorded too, and an owner exports it as JSON Lines.\n"
                    "\n"
                    "## Surface\n"
                    "\n"
                    "Generated: the parts of Atlas that serve this capability.\n"
                    "\n"
                    "- API area `atlas-system-api-audit` (Audit export route)\n"
                    "- API area `atlas-system-api-events` (Event routes)\n"
                    "- command group `atlas-admin audit` (`atlas-system-cli-audit`)\n"
                ),
            },
            {
                "file": "configuration.md",
                "title": "Configuration",
                "kind": "runbook",
                "content": (
                    "# Configuration\n"
                    "\n"
                    "The settings of Audit trail, from `backend/settings_catalog.py`. Every setting, with its default and where it is read, is in the configuration reference (CONFIG.md); an owner sees the effective values in the board's Config tab.\n"
                    "\n"
                    "| Setting | Kind | Read by | What it is |\n"
                    "|---|---|---|---|\n"
                    "| `ATLAS_AUDIT_READS` | plain | atlas-api | Record every agent read of a node, a search or a document. |\n"
                    "| `ATLAS_AUDIT_RETENTION_DAYS` | plain | atlas-api | Days an agent-read audit row is kept; none means forever. |\n"
                ),
            },
            {
                "file": "tokens-and-audit-guide.md",
                "title": "Tokens and audit guide",
                "kind": "runbook",
                "content": (
                    "# Agent tokens and read audit\n"
                    "\n"
                    "An AI agent reaches Atlas with a bearer credential. There are two kinds:\n"
                    "\n"
                    "- **Atlas agent tokens** (`atlas_svc_...`), minted by Atlas: by the person themselves on the board,\n"
                    "  or by an operator with `atlas-admin token mint`.\n"
                    "- **IdP access tokens** (opt-in): an OAuth 2.1 access token issued by your identity provider for the\n"
                    "  Atlas MCP resource.\n"
                    "\n"
                    "Either way the agent acts as **its own account, linked to its person**, and never with its person's\n"
                    "instance role. What that account may see and do is in\n"
                    "Identity and access (document *Identity and access guide* on node `atlas-system-identity`). This page covers\n"
                    "how each credential is made, and the read audit that records what agents read.\n"
                    "\n"
                    "## Atlas agent tokens\n"
                    "\n"
                    "### Minted by the person (Settings, Agent credentials)\n"
                    "\n"
                    "A signed-in person opens **Settings, Agent credentials**, or calls `POST /api/me/agent-credential`\n"
                    "with a purpose, scopes, an expiry and their own password. Atlas creates the agent account\n"
                    "`<person>-<purpose>`, links it to the person and returns the token **once**: only a keyed hash of it\n"
                    "is stored, and nothing returns it again.\n"
                    "\n"
                    "- **Browser session only.** A token cannot mint another token, whatever its scopes\n"
                    "  (`403 session_required`): one leaked token must not become a key factory.\n"
                    "- **The person's password, every time.** A live session alone is not enough, because a token\n"
                    "  outlives the password change that would end a stolen session. A wrong password is\n"
                    "  `403 password_incorrect` and counts as a failed login.\n"
                    "- **An SSO person confirms at the IdP instead**, where the operator sets `ATLAS_OIDC_STEP_UP=on`:\n"
                    "  **Confirm with SSO** names the credential being approved and signs them in at the IdP again;\n"
                    "  that credential, and only that one, must then be created in the same browser session within\n"
                    "  `ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS` (300) of that sign-in. Without a step-up the mint is\n"
                    "  `403 step_up_required`; for another name, `403 step_up_agent_mismatch`. See\n"
                    "  Agent credentials for SSO people (document *Identity and access guide* on node `atlas-system-identity`).\n"
                    "- **Scopes capped by the person's role:** a `member` may give `read` and `write`, an `owner` also\n"
                    "  `admin`. At least one scope is required.\n"
                    "- **Always expires:** 1 to 365 days. There is no never-expires option.\n"
                    "- **Names are permanent.** A purpose that is already used for one of the person's agents is\n"
                    "  `409 name_taken`.\n"
                    "\n"
                    "The same screen lists the person's credentials, issues another credential for an existing agent\n"
                    "(rotation, with the same checks; the old credential keeps working until it is revoked), and revokes\n"
                    "one (`POST /api/me/agent-credential/{token_id}/revoke`, permanent, effective at once).\n"
                    "\n"
                    "### Minted by an operator: `atlas-admin token mint`\n"
                    "\n"
                    "The operator's equivalent of that screen, for people who cannot use it:\n"
                    "\n"
                    "```sh\n"
                    "docker compose exec atlas-api atlas-admin token mint --user <person> --purpose claude-code \\\n"
                    "    --scopes read --days 90 --out /path/to/new-token-file\n"
                    "```\n"
                    "\n"
                    "- `--user` is a username, `<idp>:<subject>`, or an IdP account's email.\n"
                    "- The agent is `<person>-<purpose>`, created on first use and linked to the person. Minting again\n"
                    "  for the same purpose adds a token to the same agent, which is how you rotate: mint, deploy, confirm\n"
                    "  the new token is used, revoke the old one.\n"
                    "- The same rules as the screen: scopes capped by the person's role (default `read`), and an expiry\n"
                    "  of 1 to 365 days (default 365).\n"
                    "- **The raw token goes only to the new file named by `--out`**, created with mode 0600. It is never\n"
                    "  printed, and `--out -` is refused, because the command is often run by an agent whose output is a\n"
                    "  transcript. An existing file is never overwritten. If the file cannot be written, no token is\n"
                    "  minted.\n"
                    "- For an IdP-backed person, the agent is named after the local part of their email, so they must\n"
                    "  have signed in once.\n"
                    "- Each mint is an `auth_event` naming the operator (`--actor`, else `ATLAS_ADMIN_ACTOR`, else the\n"
                    "  OS user).\n"
                    "\n"
                    "`atlas-admin token list [--user <person>]` lists live tokens by id and prefix, never the value or\n"
                    "its hash. `atlas-admin token revoke <id or prefix>` or `token revoke --user <person>` revokes them,\n"
                    "permanently, from the next request. For moving a token to a client and rotating it, see\n"
                    "Operations (document *Operations guide* on node `atlas-system-install`).\n"
                    "\n"
                    "### Current limitation: SSO-only people cannot mint their own\n"
                    "\n"
                    "The password step on the Settings screen re-checks the person's Atlas password. A person who signs\n"
                    "in **only through single sign-on** has no Atlas password, so the check always fails and the request\n"
                    "is refused with `403 password_incorrect`. This is a known gap, tracked for a fix.\n"
                    "\n"
                    "**Until then, either:**\n"
                    "\n"
                    "- an operator mints the token for them with `atlas-admin token mint --user <idp>:<subject>` (or\n"
                    "  their email, once they have signed in), as above; or\n"
                    "- on an install that sets `ATLAS_OAUTH_RESOURCE`, their client uses an IdP access token instead\n"
                    "  (next section).\n"
                    "\n"
                    "## IdP access tokens (opt-in)\n"
                    "\n"
                    "With `ATLAS_OAUTH_RESOURCE` set, the API **also** accepts an OAuth 2.1 access token (a JWT) from the\n"
                    "OIDC issuer, issued for the Atlas MCP resource, beside `atlas_svc_` tokens, which are unchanged.\n"
                    "With it unset (the default), a bearer that is not an `atlas_svc_` token is refused exactly as\n"
                    "before, and the MCP service publishes no metadata.\n"
                    "\n"
                    "### Settings\n"
                    "\n"
                    "| Setting | Meaning |\n"
                    "|---|---|\n"
                    "| `ATLAS_OAUTH_RESOURCE` | The canonical URI of the protected resource: the MCP endpoint as clients name it. An `https` URL with no query or fragment; a trailing slash is dropped and scheme and host are lowercased. A token's `aud` must contain it. The MCP service reads the same variable. |\n"
                    "| `ATLAS_OAUTH_AUDIENCES` | Further accepted `aud` values, comma-separated, for an IdP that stamps something else (Entra v2 puts the resource app's client id in `aud`). |\n"
                    "| `ATLAS_OAUTH_SCOPE_MAP` | Comma-separated `<idp scope>=<read\\|write\\|admin>`. A token gets the highest tier any of its scopes maps to; `write` includes `read`, `admin` includes both. Unset, or nothing mapped: `read`. |\n"
                    "\n"
                    "Each mistake stops the boot with a message naming the variable: a blank value, a non-`https`\n"
                    "resource, `ATLAS_OAUTH_RESOURCE` without `oidc` in `ATLAS_AUTH_PROVIDERS`, either of the other two\n"
                    "variables without `ATLAS_OAUTH_RESOURCE`, or an accepted audience equal to `ATLAS_OIDC_CLIENT_ID`\n"
                    "(that is the audience of Atlas's own ID tokens, and an ID token must never pass as an access token;\n"
                    "give the resource its own app registration).\n"
                    "\n"
                    "### What the MCP service publishes\n"
                    "\n"
                    "The MCP service serves OAuth protected resource metadata (RFC 9728) naming your OIDC issuer as the\n"
                    "authorization server, at the path-inserted well-known URI of the resource and at the root\n"
                    "`/.well-known/oauth-protected-resource`, and every `401` it sends carries a\n"
                    "`WWW-Authenticate: Bearer resource_metadata=\"...\"` challenge pointing at it. `ATLAS_MCP_OAUTH_SCOPES`\n"
                    "adds a `scope` to the challenge. The MCP service does not validate the token itself: it forwards\n"
                    "each caller's token only to the API, which is the same protected resource.\n"
                    "\n"
                    "### How the API checks a token\n"
                    "\n"
                    "In order; each failure is a `401` and a `login_fail` auth event with reason `oauth:<why>`:\n"
                    "\n"
                    "1. the shape: three base64url segments, at most 16 KiB;\n"
                    "2. the algorithm: in the OIDC provider's asymmetric allowlist and advertised by discovery (never\n"
                    "   `none`, never an HMAC algorithm);\n"
                    "3. the signature, with the issuer's key for `kid`, from the same cached key set the SSO login uses;\n"
                    "4. `iss` exactly the discovered issuer, `aud` containing the resource (or an accepted audience),\n"
                    "   `exp` present, and `exp`, `nbf` and `iat` within 60 seconds of leeway; a token with a `nonce`\n"
                    "   (an ID token) is refused;\n"
                    "5. **the person**: looked up by the SSO login's subject rule. No account is refused with a message\n"
                    "   telling them to sign in with SSO once: Atlas never creates a person from an access token, so SSO\n"
                    "   login stays the one door. A disabled person, one who must change a password, or one whose last\n"
                    "   SSO sign-in is older than `ATLAS_OIDC_REVALIDATE_DAYS` is refused;\n"
                    "6. **the client**: `client_id`, else `azp`, else `appid`. None is refused.\n"
                    "\n"
                    "### The account it acts as\n"
                    "\n"
                    "One agent account per person and client, created at the first accepted token (idp `oauth`, role\n"
                    "`member`, linked to the person), recorded by `user_created` and `bot_person_linked` auth events.\n"
                    "Its scopes are the mapped tier, narrowed to what the person's role may self-mint (a member's agent\n"
                    "never holds `admin`), and project roles narrow them again per project. Disabling that account with\n"
                    "`atlas-admin user disable` turns off that one client for that person; disabling the person turns\n"
                    "off all of them.\n"
                    "\n"
                    "## Read audit\n"
                    "\n"
                    "Writes are always recorded (`graph_event` for the graph, `auth_event` for sign-ins, accounts and\n"
                    "tokens). **Agent reads** are recorded only when you turn it on.\n"
                    "\n"
                    "### Turning it on\n"
                    "\n"
                    "| Setting | Meaning |\n"
                    "|---|---|\n"
                    "| `ATLAS_AUDIT_READS` | `1`, `true`, `yes` or `on`: every agent read writes an `audit_read` row. Unset or `0`: nothing is recorded. |\n"
                    "| `ATLAS_AUDIT_RETENTION_DAYS` | 1 to 36500. Unset: `audit_read` rows are kept forever. |\n"
                    "\n"
                    "The switch is read once, at start-up; a bad value stops the boot. Recreate `atlas-api` after\n"
                    "changing it.\n"
                    "\n"
                    "### What is logged\n"
                    "\n"
                    "**Who:** token callers only, an Atlas agent token or an IdP access token. A person reading the board\n"
                    "in a browser session is not recorded.\n"
                    "\n"
                    "**Which reads:** the API routes that serve nodes, which are the ones the MCP tools call:\n"
                    "\n"
                    "`GET /api/node/{node_id}`, `GET /api/node/{node_id}/doc`, `GET /api/node/{node_id}/structure`,\n"
                    "`GET /api/search`, `GET /api/context`, `GET /api/graph`, `GET /api/graph/outline`,\n"
                    "`GET /api/graph/path`, `GET /api/workitems`, `GET /api/adrs`, `GET /api/adrs/{ref}`,\n"
                    "`GET /api/intents`.\n"
                    "\n"
                    "Recording happens in the API, not in the MCP service, so a token that calls the API directly is\n"
                    "recorded too. A route that answers by calling another audited route in the same request writes one\n"
                    "row, not two.\n"
                    "\n"
                    "**One row per read**, with:\n"
                    "\n"
                    "| Column | Holds |\n"
                    "|---|---|\n"
                    "| `at` | the time |\n"
                    "| `user_id` | the person the agent belongs to |\n"
                    "| `actor_user_id`, `actor_subject` | the agent account and its name |\n"
                    "| `token_id` | which Atlas token; empty for an IdP access token, which has no Atlas token id |\n"
                    "| `tool` | the MCP tool, as the MCP service declares it (`X-Atlas-Tool`) |\n"
                    "| `request_id` | the per-call request id (`X-Request-ID`) |\n"
                    "| `route` | the route template, for example `GET /api/node/{node_id}` |\n"
                    "| `node_ids` | the ids of the nodes actually served, in order |\n"
                    "\n"
                    "`tool` and `request_id` are labels the caller sends: they are validated and stored as given, and a\n"
                    "malformed one is dropped while the read is still recorded.\n"
                    "\n"
                    "**Never recorded:** the content read, search strings, and refused reads (a `400`, `404` or `413`\n"
                    "serves no node and writes no row).\n"
                    "\n"
                    "**Fail closed.** The row is committed before the response is sent. If it cannot be written, the\n"
                    "caller gets a `500` and no content: with the audit on, an unaudited read is what the operator asked\n"
                    "not to have.\n"
                    "\n"
                    "### Retention\n"
                    "\n"
                    "With `ATLAS_AUDIT_RETENTION_DAYS` set, run the purge from cron:\n"
                    "\n"
                    "```sh\n"
                    "docker compose exec -T atlas-api atlas-admin audit purge\n"
                    "```\n"
                    "\n"
                    "It deletes `audit_read` rows older than that many days, and only those. `graph_event` and\n"
                    "`auth_event` are never purged. Without the setting it deletes nothing and says so.\n"
                    "\n"
                    "### Exporting\n"
                    "\n"
                    "`GET /api/audit/export` streams `graph_event`, `auth_event` and `audit_read` as JSON Lines\n"
                    "(`application/x-ndjson`), oldest first across all three.\n"
                    "\n"
                    "- `since`, `until`: ISO 8601, `since` inclusive and `until` exclusive; a time without a timezone is\n"
                    "  read as UTC.\n"
                    "- `kinds`: a comma-separated subset of `graph_event,auth_event,audit_read`, default all three. An\n"
                    "  unknown kind is a `400` naming the legal ones.\n"
                    "\n"
                    "**Who:** the instance owner in a browser session, or a token with the `admin` scope whose linked\n"
                    "person is a live owner, re-checked on every call, so a demoted or disabled owner's token loses the\n"
                    "export at once. Everyone else gets `403`. A SIEM collector uses the second kind; minting it and\n"
                    "calling the export from a collector are in Operations (document *Operations guide* on node `atlas-system-install`).\n"
                    "\n"
                    "An `audit_read` line carries `kind`, `id`, `at`, `user_id`, `actor_user_id`, `actor_subject`,\n"
                    "`token_id`, `tool`, `route`, `node_ids` and `request_id`. No export line contains content, search\n"
                    "strings, passwords or their hashes, token values or digests, or session ids.\n"
                ),
                "from": "docs/oss/guides/tokens-and-audit.md",
            },
        ],
        "abstract": "Graph writes are recorded by one writer as graph events naming the person and the agent. Sign-in and grant changes are recorded separately. With read auditing on, every agent read of a node, a search or a document is recorded too, and an owner exports it as JSON Lines.",
    },
    {
        "id": "atlas-system-signals",
        "type": "capability",
        "parent": "atlas-system",
        "name": "Attention and rollup",
        "summary": "Derived state on every node: attention, completion and open work, rolled up the contains tree.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "overview.md",
                "title": "Overview",
                "kind": "architecture",
                "content": (
                    "# Attention and rollup\n"
                    "\n"
                    "Derived state on every node: attention, completion and open work, rolled up the contains tree.\n"
                    "\n"
                    "Work rolls up: an item bound to a node shows there and on every node containing it. Attention and completion are recomputed after each write, and a rollup takes the caller's visible set as an input so it never leaks a count.\n"
                    "\n"
                    "## Surface\n"
                    "\n"
                    "Generated: the parts of Atlas that serve this capability.\n"
                    "\n"
                    "- API area `atlas-system-api-stats` (Personal statistics route)\n"
                ),
            },
        ],
        "abstract": "Work rolls up: an item bound to a node shows there and on every node containing it. Attention and completion are recomputed after each write, and a rollup takes the caller's visible set as an input so it never leaks a count.",
    },
    {
        "id": "atlas-system-docs",
        "type": "capability",
        "parent": "atlas-system",
        "name": "Node documents and the handbook",
        "summary": "Markdown documents on any node, served only when listed in the node's docs[]; the handbook is the system node set's documents.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "overview.md",
                "title": "Overview",
                "kind": "architecture",
                "content": (
                    "# Node documents and the handbook\n"
                    "\n"
                    "Markdown documents on any node, served only when listed in the node's docs[]; the handbook is the system node set's documents.\n"
                    "\n"
                    "A node's documents are rows, written and read through the API and listed in the node's `attributes.docs`. The handbook is the documents of the `atlas-system` set, also rendered as a static site at /docs.\n"
                    "\n"
                    "## Surface\n"
                    "\n"
                    "Generated: the parts of Atlas that serve this capability.\n"
                    "\n"
                    "- API area `atlas-system-api-docs` (Document routes)\n"
                    "- API area `atlas-system-api-handbook` (Handbook site routes)\n"
                ),
            },
            {
                "file": "configuration.md",
                "title": "Configuration",
                "kind": "runbook",
                "content": (
                    "# Configuration\n"
                    "\n"
                    "The settings of Node documents and the handbook, from `backend/settings_catalog.py`. Every setting, with its default and where it is read, is in the configuration reference (CONFIG.md); an owner sees the effective values in the board's Config tab.\n"
                    "\n"
                    "| Setting | Kind | Read by | What it is |\n"
                    "|---|---|---|---|\n"
                    "| `ATLAS_PUBLIC_DOCS` | plain | atlas-api | Serve the /docs handbook without sign-in. |\n"
                ),
            },
        ],
        "abstract": "A node's documents are rows, written and read through the API and listed in the node's `attributes.docs`. The handbook is the documents of the `atlas-system` set, also rendered as a static site at /docs.",
    },
    {
        "id": "atlas-system-adrs",
        "type": "capability",
        "parent": "atlas-system",
        "name": "ADR registry",
        "summary": "Architecture decision records per project, with ids, status and links, from git or recorded in Atlas.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "overview.md",
                "title": "Overview",
                "kind": "architecture",
                "content": (
                    "# ADR registry\n"
                    "\n"
                    "Architecture decision records per project, with ids, status and links, from git or recorded in Atlas.\n"
                    "\n"
                    "Each project has its own ADR prefix and numbering. A record either mirrors a file in a repository or is recorded in Atlas directly, and the registry is searched and read through the API and the MCP.\n"
                    "\n"
                    "## Surface\n"
                    "\n"
                    "Generated: the parts of Atlas that serve this capability.\n"
                    "\n"
                    "- API area `atlas-system-api-adrs` (ADR routes)\n"
                ),
            },
        ],
        "abstract": "Each project has its own ADR prefix and numbering. A record either mirrors a file in a repository or is recorded in Atlas directly, and the registry is searched and read through the API and the MCP.",
    },
    {
        "id": "atlas-system-setup",
        "type": "capability",
        "parent": "atlas-system",
        "name": "Setup guide",
        "summary": "The first-run interview: topics asked of a person, each answered topic recorded as a proposed decision.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "overview.md",
                "title": "Overview",
                "kind": "architecture",
                "content": (
                    "# Setup guide\n"
                    "\n"
                    "The first-run interview: topics asked of a person, each answered topic recorded as a proposed decision.\n"
                    "\n"
                    "## Surface\n"
                    "\n"
                    "Generated: the parts of Atlas that serve this capability.\n"
                    "\n"
                    "- API area `atlas-system-api-setup` (Setup guide routes)\n"
                ),
            },
            {
                "file": "configuration.md",
                "title": "Configuration",
                "kind": "runbook",
                "content": (
                    "# Configuration\n"
                    "\n"
                    "The settings of Setup guide, from `backend/settings_catalog.py`. Every setting, with its default and where it is read, is in the configuration reference (CONFIG.md); an owner sees the effective values in the board's Config tab.\n"
                    "\n"
                    "| Setting | Kind | Read by | What it is |\n"
                    "|---|---|---|---|\n"
                    "| `ATLAS_SETUP_QUESTIONS_FILE` | plain | atlas-api | An extra question file for the setup guide, asked after the shipped topics. |\n"
                ),
            },
            {
                "file": "setup-guide.md",
                "title": "Setup guide",
                "kind": "runbook",
                "content": (
                    "# The setup guide\n"
                    "\n"
                    "The setup guide interviews whoever installed Atlas about how their organisation works: guardrails\n"
                    "for agents, CI/CD, environments, identity, secrets handling and the work hierarchy. It records each\n"
                    "answered topic as a **Proposed** decision record (an Atlas-native ADR), which becomes the\n"
                    "organisation's decision only when a person accepts it. Its answers also feed the\n"
                    "first CI/CD pipeline (document *First CI/CD guide* on node `atlas-system-guardrails`).\n"
                    "\n"
                    "An install that never runs the guide has an empty `setup_answer` table, no guide-made ADR, and\n"
                    "behaves exactly as before.\n"
                    "\n"
                    "## Running it\n"
                    "\n"
                    "Today the guide has one client: an **AI agent interview** through the MCP tool\n"
                    "`atlas_setup_guide`. The Claude Code client pack has a `setup-guide` skill that carries how to run\n"
                    "the interview: the agent asks the person each question and never answers for them. There is no\n"
                    "board wizard yet; it would be a later client of the same API.\n"
                    "\n"
                    "**Who may run it:** the instance owner in a browser session, or an agent token that holds the\n"
                    "`admin` scope **and** is linked to a live owner. Everyone else gets `403 forbidden`. In practice the\n"
                    "owner mints their own agent token with `read,write,admin` and runs the interview with it. The\n"
                    "answers describe how the whole install is governed, which is the owner's to state.\n"
                    "\n"
                    "The MCP tool has two forms:\n"
                    "\n"
                    "| Call | Does |\n"
                    "|---|---|\n"
                    "| `atlas_setup_guide()`, or with `topic=` | Reads where the guide stands, and the topic to ask next in full (`GET /api/setup`). |\n"
                    "| `atlas_setup_guide(topic=..., answers={...}, project=..., prefix=...)` | Records one topic's answers as a Proposed ADR (`POST /api/setup/answers`). |\n"
                    "\n"
                    "`answers` without `topic` is refused before any call. There is deliberately **no accept** in the\n"
                    "tool (see [Accepting](#accepting-an-adr)). On a live install the tool must also be on your MCP\n"
                    "gateway's allow-list, if you run one.\n"
                    "\n"
                    "### What the state shows\n"
                    "\n"
                    "`GET /api/setup` returns the question set's version, every topic with whether it is answered, its\n"
                    "ADR and that ADR's status, who answered and when, the next unanswered question (`next`), the topic\n"
                    "to ask in full (`ask`), `answered` and `total`, `complete`, and the ADRs still awaiting acceptance.\n"
                    "The guide can be stopped and resumed at any time, by any client.\n"
                    "\n"
                    "## What it asks\n"
                    "\n"
                    "The shipped question set is version `1`, in `backend/data/setup_guide/questions.v1.json`. Every\n"
                    "question is required except `guardrails.notes`. Answers use the option **values** below, never the\n"
                    "labels.\n"
                    "\n"
                    "| Topic | Question | Type | Values |\n"
                    "|---|---|---|---|\n"
                    "| `guardrails` | `agent_actions` | multi | `read`, `file_work`, `write_docs`, `open_pr`, `merge`, `deploy_nonprod`, `deploy_prod`, `change_config` |\n"
                    "| | `approval_gates` | multi | `merge`, `deploy_nonprod`, `deploy_prod`, `schema_change`, `acceptance_change`, `secret_change`, `access_change` |\n"
                    "| | `who_merges` | choice | `human_only`, `agent_after_approval`, `agent_when_green` |\n"
                    "| | `who_deploys` | choice | `human_only`, `agent_after_approval`, `pipeline_on_merge` |\n"
                    "| | `notes` | text | optional |\n"
                    "| `cicd` | `ci_platform` | choice | `github_actions`, `gitlab_ci`, `jenkins`, `azure_pipelines`, `other`, `none` |\n"
                    "| | `checks` | multi | `build`, `unit_tests`, `integration_tests`, `lint`, `type_check`, `sast`, `dependency_scan`, `secret_scan` |\n"
                    "| | `gates` | multi | the same values as `checks` |\n"
                    "| | `deploy_trigger` | choice | `manual`, `on_merge`, `on_tag`, `scheduled` |\n"
                    "| `environments` | `names` | text | comma-separated, least to most protected |\n"
                    "| | `production` | text | one of the names |\n"
                    "| | `promotion` | choice | `sequential`, `independent` |\n"
                    "| `identity` | `login` | choice | `local`, `oidc`, `both` |\n"
                    "| | `rbac` | choice | `off`, `projects` |\n"
                    "| | `default_visibility` | choice | `shared`, `private` |\n"
                    "| `secrets` | `storage` | choice | `files`, `vault`, `cloud_secret_manager`, `ci_secrets`, `other` |\n"
                    "| | `agent_access` | choice | `never`, `path_only`, `scoped` |\n"
                    "| | `rotation` | choice | `manual`, `scheduled`, `on_incident` |\n"
                    "| `work_hierarchy` | `levels` | choice | `epic_story_task`, `story_task`, `task_only` |\n"
                    "| | `who_files` | multi | `people`, `agents` |\n"
                    "| | `acceptance` | choice | `people`, `agent_proposes` |\n"
                    "\n"
                    "A wrong answer is a `400` that names the field, for example `answers.who_merges`.\n"
                    "\n"
                    "**The answer keys are a contract.** Other parts of Atlas read answers by `<topic>.<question>` and the\n"
                    "option values. A new meaning gets a new id or a new question-set version, never a reused one.\n"
                    "\n"
                    "### Adding your own topics\n"
                    "\n"
                    "Name a second file of the same shape in `ATLAS_SETUP_QUESTIONS_FILE`. Its topics are asked after the\n"
                    "shipped ones. A topic id that repeats one already loaded is an error, so an install can add topics\n"
                    "but never silently redefine one. Both files are read and validated each time the guide is used,\n"
                    "never at boot. A bad file makes the guide's routes answer `503 bad_question_set`, naming the file\n"
                    "and the place in it. Check a file before you set it:\n"
                    "\n"
                    "```sh\n"
                    "docker compose exec atlas-api python setup_guide.py --check FILE\n"
                    "```\n"
                    "\n"
                    "Each topic has an `adr` block (`title`, `context`, `decision`, `consequences`) whose\n"
                    "`{question_id}` placeholders are filled with the answers as a person reads them.\n"
                    "\n"
                    "## What it produces\n"
                    "\n"
                    "**One Proposed ADR per answered topic**, written through the ADR registry's own write, so it gets\n"
                    "the registry's id allocation and its `node.adr.write` event, attributed to the caller (an agent's\n"
                    "event also names the person it belongs to). The ADR's sections come from the topic's `adr` block,\n"
                    "and its content ends with an `## Answers` table of the raw keys, so the exact values can be read\n"
                    "back from the record itself.\n"
                    "\n"
                    "- **Where.** The project named in `project`, else the project the guide already records into, else\n"
                    "  `atlas-system`, the install's own system node.\n"
                    "- **Prefix.** The first ADR in a project with none fixes its prefix: `prefix`, else `SYS`.\n"
                    "- **The same answer again** writes nothing: no ADR, no event, no row.\n"
                    "- **A changed answer** writes a new Proposed ADR that supersedes the topic's previous one, which is\n"
                    "  left untouched. A changed answer must go to the project the topic was first recorded in.\n"
                    "- **Project roles.** With `ATLAS_RBAC=projects`, recording on `atlas-system` does not need a role\n"
                    "  binding there, for the callers allowed above. Any other project keeps the registry's role check.\n"
                    "\n"
                    "The guide also keeps its own state row per answer (`setup_answer`), with the question-set version,\n"
                    "the answers, the ADR, and who answered.\n"
                    "\n"
                    "**Feeding other tools.** `atlas-admin cicd generate --from-guide` reads the newest answers of the\n"
                    "`environments`, `guardrails` and `cicd` topics to build a guardrail matrix and the pipeline input;\n"
                    "see First CI/CD (document *First CI/CD guide* on node `atlas-system-guardrails`). The answers of the other topics are recorded\n"
                    "in their ADRs; nothing changes the install's configuration from them.\n"
                    "\n"
                    "## Accepting an ADR\n"
                    "\n"
                    "Accepting is **a person's act, from a browser session**: `POST /api/setup/adrs/{adr_id}/accept`\n"
                    "by the instance owner, with the session's CSRF token. Every token is refused, whatever its scopes\n"
                    "and whoever it is linked to, and a member gets `403 forbidden`. The ADR's status becomes Accepted,\n"
                    "with \"Accepted by\" the owner and the date.\n"
                    "\n"
                    "- An ADR that a later answer superseded is refused (`409 superseded`), naming the newer one.\n"
                    "- An ADR that is not Proposed is refused (`409 not_proposed`).\n"
                    "- An ADR the guide did not record is `404 unknown_guide_adr`.\n"
                    "\n"
                    "The registry's generic ADR edit refuses to change a guide-made ADR, so this route is the one way to\n"
                    "accept one, and a new answer the one way to replace one. There is no reject: answer again to\n"
                    "supersede. The board has no accept button yet, so the owner calls the route from a signed-in\n"
                    "browser session.\n"
                ),
                "from": "docs/oss/guides/setup-guide.md",
            },
        ],
    },
    {
        "id": "atlas-system-guardrails",
        "type": "capability",
        "parent": "atlas-system",
        "name": "Agent guardrails and first CI/CD",
        "summary": "A matrix of what agents may do, applied to the install and exported as agent policy files, and a first CI/CD pipeline generated from it.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "overview.md",
                "title": "Overview",
                "kind": "architecture",
                "content": (
                    "# Agent guardrails and first CI/CD\n"
                    "\n"
                    "A matrix of what agents may do, applied to the install and exported as agent policy files, and a first CI/CD pipeline generated from it.\n"
                    "\n"
                    "## Surface\n"
                    "\n"
                    "Generated: the parts of Atlas that serve this capability.\n"
                    "\n"
                    "- command group `atlas-admin cicd` (`atlas-system-cli-cicd`)\n"
                    "- command group `atlas-admin guardrails` (`atlas-system-cli-guardrails`)\n"
                ),
            },
            {
                "file": "guardrails-guide.md",
                "title": "Guardrails guide",
                "kind": "runbook",
                "content": (
                    "# Agent guardrails per environment\n"
                    "\n"
                    "The guardrail matrix states, as data, what AI agents may do in each of **your own** environments:\n"
                    "which branches belong to each environment, which actions an agent takes on its own, which need a\n"
                    "person, and which need a person with a step-up sign-in. Atlas validates the matrix, stores it on\n"
                    "the install's system node where agents read it, and exports it as policy files for your agents and\n"
                    "your repository host.\n"
                    "\n"
                    "Nothing applies a matrix by itself. An install that never applies one has no guardrail document,\n"
                    "intent or ADR, and behaves exactly as before.\n"
                    "\n"
                    "```sh\n"
                    "docker compose exec atlas-api atlas-admin guardrails validate FILE\n"
                    "docker compose exec atlas-api atlas-admin guardrails apply FILE [--dry-run] [--adr-prefix SYS]\n"
                    "docker compose exec atlas-api atlas-admin guardrails show [--crunch FILE | --crunch-from-db]\n"
                    "docker compose exec atlas-api atlas-admin guardrails export \\\n"
                    "    --format claude-code|github-branch-protection [--env NAME ...] [--from FILE] --out PATH|- \\\n"
                    "    [--crunch FILE | --crunch-from-db]\n"
                    "```\n"
                    "\n"
                    "The file is read inside the container, so put it where the container can see it (for example with\n"
                    "`docker compose cp`).\n"
                    "\n"
                    "## The matrix format\n"
                    "\n"
                    "A matrix is a JSON or YAML file. The shipped example, `backend/data/guardrails/example-default.yaml`,\n"
                    "is a starting point to copy and edit; nothing reads it unless you name it.\n"
                    "\n"
                    "```yaml\n"
                    "version: 1                       # required; the only version Atlas reads\n"
                    "name: \"Example default\"          # optional label, carried into the document and the ADR\n"
                    "description: \"...\"               # optional\n"
                    "custom_actions:                  # optional: actions outside the vocabulary, name -> meaning\n"
                    "  run_migration: \"apply a database migration\"\n"
                    "environments:                    # required, ORDERED: first is closest to the developer\n"
                    "  - name: dev\n"
                    "    branches: [dev, \"feature/*\"]\n"
                    "    protected: false\n"
                    "    agent: [branch, commit, push, open_pr, deploy, read_logs, propose]\n"
                    "    human: [merge]\n"
                    "  - name: preprod\n"
                    "    branches: [preprod]\n"
                    "    agent: [read_logs, propose]\n"
                    "    human: [merge, approve_deploy, deploy]\n"
                    "    commands:\n"
                    "      deploy: [\"tools/deploy.sh preprod*\"]\n"
                    "  - name: prod\n"
                    "    branches: [main]\n"
                    "    production: true\n"
                    "    agent: [read_logs]\n"
                    "    human: [merge, approve_deploy, deploy]\n"
                    "    step_up: [deploy]\n"
                    "    required_reviews: 1\n"
                    "    commands:\n"
                    "      deploy: [\"tools/deploy.sh prod*\"]\n"
                    "```\n"
                    "\n"
                    "Each environment has these keys, and no others:\n"
                    "\n"
                    "| Key | Meaning | Default |\n"
                    "|---|---|---|\n"
                    "| `name` | Lowercase kebab-case, 1 to 40 characters, unique. | required |\n"
                    "| `branches` | At least one branch or pattern; `*` may appear anywhere. No pattern may belong to two environments. | required |\n"
                    "| `production` | Marks a production environment. At least one environment must be production. | `false` |\n"
                    "| `protected` | Whether its branches are protected. | `true`, unless agents may `merge` there |\n"
                    "| `agent` | Actions an agent may take here on its own. | `[]` |\n"
                    "| `human` | Actions that need a person here; an agent may only propose them. | `[]` |\n"
                    "| `step_up` | Of `human`: those that also need a step-up sign-in. Each must be in `human`. | `[]` |\n"
                    "| `required_reviews` | Reviews on a protected branch, 0 to 6. | 1 when protected, else 0 |\n"
                    "| `required_checks` | CI check names a protected branch requires. | `[]` |\n"
                    "| `commands` | Command patterns per vocabulary action, which the Claude Code export allows or denies. | none |\n"
                    "| `notes` | Free text, carried into the environment's intent. | none |\n"
                    "\n"
                    "An action that is in neither `agent` nor `human` is not done in that environment at all, which for\n"
                    "an agent is the same as denied. An action may not be in both lists.\n"
                    "\n"
                    "### The action vocabulary\n"
                    "\n"
                    "| Action | Meaning |\n"
                    "|---|---|\n"
                    "| `branch` | create a branch |\n"
                    "| `commit` | commit |\n"
                    "| `push` | push to the environment's branches |\n"
                    "| `open_pr` | open a pull request |\n"
                    "| `merge` | merge a pull request into the environment's branches |\n"
                    "| `deploy` | deploy directly |\n"
                    "| `deploy_via_pipeline` | deploy only through the pipeline, after a merge |\n"
                    "| `read_logs` | read logs |\n"
                    "| `propose` | propose a change (an issue, a comment, a draft) without making it |\n"
                    "| `approve_deploy` | approve a deploy |\n"
                    "\n"
                    "**Custom actions** are allowed when declared under `custom_actions` with a description. An\n"
                    "undeclared unknown name is an error, so a typo never passes as a new action. Custom actions are\n"
                    "carried into the intents, the document and the ADR, so agents read them. The exports ignore them,\n"
                    "because an export can only translate an action whose meaning it knows, so `commands` may not name\n"
                    "one.\n"
                    "\n"
                    "### Rules the matrix enforces\n"
                    "\n"
                    "These encode standing decisions, not only the shape:\n"
                    "\n"
                    "- An agent never holds deploy rights in a production environment: `deploy`, `deploy_via_pipeline`\n"
                    "  and `approve_deploy` may not be in a production environment's `agent` list.\n"
                    "- Approving a deploy is a human act: `approve_deploy` is never in any `agent` list.\n"
                    "- A protected production environment needs at least one review.\n"
                    "\n"
                    "`validate` reports every problem at once, each naming the environment and the key.\n"
                    "\n"
                    "## Applying it\n"
                    "\n"
                    "`apply` writes to the system node `atlas-system`, never to a project of your own. If that node is\n"
                    "missing, it refuses and says how to add it. Each change is one graph event. It writes:\n"
                    "\n"
                    "- the document `docs/nodes/atlas-system/guardrails.md` on the node: a readable table, plus the\n"
                    "  normalised matrix as a fenced JSON block that `show` and `export` read back;\n"
                    "- **one `constraint` intent per environment**, slug `guardrail-<env>`. Its statement is generated\n"
                    "  from the matrix (what an agent may do, what needs a person, what is not an agent action, the\n"
                    "  review rule, the production rule and the notes), and its source names the matrix version, its\n"
                    "  digest and the file. An environment you remove has its intent deleted;\n"
                    "- a **Proposed** ADR (an Atlas-native ADR), slug `agent-guardrails-<digest>`, numbered with\n"
                    "  `atlas-system`'s prefix if it has ADRs already, else `--adr-prefix` (default `SYS`).\n"
                    "\n"
                    "**Agents read it** with `atlas_get`: the intents are returned on `atlas-system` and, inherited, on\n"
                    "every node below it.\n"
                    "\n"
                    "**Idempotent.** The digest is the sha256 of the normalised matrix. Applying the same file again\n"
                    "writes nothing. A changed matrix updates the document and the intents and writes a **new** Proposed\n"
                    "ADR that supersedes the previous guardrails ADR. If the previous one was still Proposed it becomes\n"
                    "Superseded; an Accepted one stays Accepted until a person accepts the new one.\n"
                    "\n"
                    "`--dry-run` lists what would change and writes nothing.\n"
                    "\n"
                    "**Who it is attributed to.** A command-line write has no signed-in principal, so its events carry\n"
                    "`actor_kind = \"system\"`. The operator name (`--actor`, else `ATLAS_ADMIN_ACTOR`, else the OS user)\n"
                    "and the command travel in each event's detail as `operator` and `via`.\n"
                    "\n"
                    "## The exports\n"
                    "\n"
                    "`export` reads the applied matrix, or a file with `--from FILE`. `--env NAME` (repeatable)\n"
                    "restricts it to those environments. `--out -` writes to standard output.\n"
                    "\n"
                    "### `claude-code`\n"
                    "\n"
                    "A settings fragment, `{\"permissions\": {\"allow\": [...], \"deny\": [...]}}`, to merge into a Claude Code\n"
                    "`.claude/settings.json`. It denies:\n"
                    "\n"
                    "- pushes to the branches of every protected environment where agents may not `push`;\n"
                    "- force pushes wherever any selected branch is protected;\n"
                    "- `gh pr merge`, unless agents may `merge` in every selected environment;\n"
                    "- each `commands` pattern of an action agents may **not** take in that environment.\n"
                    "\n"
                    "A `commands` pattern of an action agents **may** take becomes an allow rule. Deny wins over allow,\n"
                    "both in the fragment and in Claude Code. These rules are defence in depth for an agent's own\n"
                    "session: the boundary is the branch protection on your repository host, which a local setting\n"
                    "cannot weaken.\n"
                    "\n"
                    "### `github-branch-protection`\n"
                    "\n"
                    "For each protected branch pattern, the JSON body for GitHub's\n"
                    "`PUT /repos/{owner}/{repo}/branches/{branch}/protection`: the required reviews, stale reviews\n"
                    "dismissed, no force push, no deletion, the required checks, and on production environments admins\n"
                    "included and the last push approved. An empty check list is marked as a TODO to fill. A pattern\n"
                    "containing `*` cannot go through that endpoint, which takes one branch name, and is marked for a\n"
                    "repository ruleset or GraphQL `createBranchProtectionRule` instead. Atlas calls no GitHub API; you\n"
                    "apply the bodies.\n"
                    "\n"
                    "### During crunch week\n"
                    "\n"
                    "`--crunch FILE` or `--crunch-from-db` shows or exports the matrix as crunch week (document *Crunch week guide* on node `atlas-system-crunch`)\n"
                    "runs it: the skipped environments lose their deploy actions, and production is unchanged. See\n"
                    "What it changes in the guardrail matrix (document *Crunch week guide* on node `atlas-system-crunch`),\n"
                    "and the known limitation of `--crunch-from-db` described there.\n"
                    "\n"
                    "## A matrix from the setup guide\n"
                    "\n"
                    "`guardrails.matrix_from_answers` builds a matrix from a short set of answers. The setup guide's\n"
                    "answers feed it when you generate a pipeline with `atlas-admin cicd generate --from-guide`; see\n"
                    "First CI/CD (document *First CI/CD guide* on node `atlas-system-guardrails`). That matrix is used for the generated pipeline\n"
                    "only: it is not applied to the install. To store it, write it as a matrix file and `apply` it.\n"
                ),
                "from": "docs/oss/guides/guardrails.md",
            },
            {
                "file": "first-ci-cd-guide.md",
                "title": "First CI/CD guide",
                "kind": "runbook",
                "content": (
                    "# A first CI/CD pipeline\n"
                    "\n"
                    "Atlas can generate a first pipeline for **GitHub Actions**, **GitLab CI** or **Azure Pipelines**\n"
                    "from your guardrail matrix (document *Guardrails guide* on node `atlas-system-guardrails`) and the CI/CD answers of the\n"
                    "setup guide (document *Setup guide* on node `atlas-system-setup`). The output is **files** that you review, complete and commit. Atlas\n"
                    "touches no repository, no network and no git command: the generator is a pure function, and the\n"
                    "command only writes the files under `--out`. An install that never runs it has nothing new\n"
                    "anywhere.\n"
                    "\n"
                    "```sh\n"
                    "docker compose exec atlas-api atlas-admin cicd generate --platform github|gitlab|azure \\\n"
                    "    (--from-guide | --matrix FILE --answers FILE) \\\n"
                    "    --out DIR [--force] [--record-adr] [--adr-prefix SYS] \\\n"
                    "    [--crunch FILE | --crunch-from-db]\n"
                    "```\n"
                    "\n"
                    "The files are written on the machine where the command runs. Run inside the container, copy `DIR`\n"
                    "out afterwards (for example with `docker compose cp`).\n"
                    "\n"
                    "## The inputs\n"
                    "\n"
                    "**A guardrail matrix.** Either `--matrix FILE` (any matrix file, such as\n"
                    "`backend/data/guardrails/example-default.yaml`), or the matrix built from the setup guide's answers\n"
                    "with `--from-guide`. The generator does not read the matrix applied to the install.\n"
                    "\n"
                    "**The CI/CD answers.** With `--matrix`, `--answers FILE` names them; the shipped example is\n"
                    "`backend/data/cicd/example-answers.yaml`:\n"
                    "\n"
                    "```yaml\n"
                    "cicd:\n"
                    "  checks: [build, unit_tests, lint, sast, secret_scan]\n"
                    "  gates: [build, unit_tests, lint]\n"
                    "  deploy_trigger: on_merge\n"
                    "environments:\n"
                    "  promotion: sequential\n"
                    "```\n"
                    "\n"
                    "The file may also hold the `cicd` answers alone, at the top level. The answers are checked against\n"
                    "the setup guide's own question set, so a typo is named rather than ignored.\n"
                    "\n"
                    "| Answer | Values | Meaning |\n"
                    "|---|---|---|\n"
                    "| `checks` | `build`, `unit_tests`, `integration_tests`, `lint`, `type_check`, `sast`, `dependency_scan`, `secret_scan` | One CI job per check. At least one is needed. |\n"
                    "| `gates` | the same values | Checks that must pass before anything deploys and before a merge. A gate is also a check. |\n"
                    "| `deploy_trigger` | `manual` (default), `on_merge`, `on_tag`, `scheduled` | When production deploys. |\n"
                    "| `promotion` | `sequential` (default), `independent` | Whether a change moves through the environments in order. |\n"
                    "\n"
                    "`--platform` decides the platform. With `--from-guide`, if the guide's `cicd.ci_platform` names a\n"
                    "different one, a note says so and the file is generated for the platform you asked for.\n"
                    "\n"
                    "### From the setup guide\n"
                    "\n"
                    "`--from-guide` reads the newest recorded answers of the guide's `environments`, `guardrails` and\n"
                    "`cicd` topics, and fails, naming the topic, if one is not answered. It builds the matrix like this:\n"
                    "\n"
                    "- the environments and the production environment come from `environments.names` and\n"
                    "  `environments.production`;\n"
                    "- if `guardrails.approval_gates` includes `deploy_nonprod`, a person approves every non-production\n"
                    "  deploy;\n"
                    "- otherwise, if `guardrails.agent_actions` includes `deploy_nonprod`, the first non-production\n"
                    "  environment is the agents' to deploy, the one before production (with three or more\n"
                    "  environments) needs a person's approval, and the rest deploy through the pipeline after a merge;\n"
                    "- otherwise no agent deploys directly anywhere;\n"
                    "- `cicd.gates` become each protected branch's required checks.\n"
                    "\n"
                    "What the matrix cannot say is reported as a note, not dropped in silence. If the answers let an\n"
                    "agent deploy to production, production's deploy stays a person's. If they let agents merge, the\n"
                    "matrix still keeps merging with a person; edit a matrix file by hand for that.\n"
                    "\n"
                    "## What is generated\n"
                    "\n"
                    "| Platform | Files |\n"
                    "|---|---|\n"
                    "| `github` | `.github/workflows/atlas-pipeline.yml`, `atlas-cicd/github-settings.md`, `atlas-cicd/github-branch-protection.json` |\n"
                    "| `gitlab` | `.gitlab-ci.yml`, `atlas-cicd/gitlab-settings.md` |\n"
                    "| `azure` | `azure-pipelines.yml`, `atlas-cicd/azure-settings.md` |\n"
                    "\n"
                    "Existing files under `--out` are not overwritten without `--force`.\n"
                    "\n"
                    "**The pipeline:**\n"
                    "\n"
                    "- One job per check. A check that is not a gate may fail without stopping anything (GitHub\n"
                    "  `continue-on-error`, GitLab `allow_failure`, Azure `continueOnError`).\n"
                    "- One deploy stage per environment whose matrix entry names a deploy action (`deploy`,\n"
                    "  `deploy_via_pipeline` or `approve_deploy`), and none for any other environment.\n"
                    "- A deploy **waits for a person** when the environment is production, when `approve_deploy` is a\n"
                    "  human action there, or when no agent deploy action is allowed there. An environment where agents\n"
                    "  may deploy deploys on a push to its branches.\n"
                    "- Production deploys on the `deploy_trigger` (a push to its branch, a `v*` tag, a run started by\n"
                    "  hand, or a weekday schedule) and always after a person's approval. Other environments deploy on\n"
                    "  a push to their branches.\n"
                    "- Every deploy uses a `DEPLOY_CREDENTIAL` that the platform holds **for that environment**: a\n"
                    "  GitHub environment secret, a GitLab variable scoped to the environment, or an Azure variable\n"
                    "  group `deploy-<env>`. No credential is repository-wide, and no deploy runs for a pull or merge\n"
                    "  request.\n"
                    "- **Every command is a placeholder that fails** (`TODO(atlas)`, exit 1) until you replace it, so\n"
                    "  nothing deploys from an unfilled pipeline. The generated GitHub workflow pins each action by its\n"
                    "  full commit sha.\n"
                    "\n"
                    "**The settings file** lists what makes the gates hold, because anyone who can push can edit a\n"
                    "pipeline file: per environment, its trigger, whether a person approves and where its credential\n"
                    "lives, and then the platform settings to configure (environments and required reviewers, protected\n"
                    "environments and variables, or variable groups and approvals; branch protection or policies). On\n"
                    "GitHub, the branch protection bodies are in `github-branch-protection.json`, in the same format as\n"
                    "`guardrails export --format github-branch-protection`.\n"
                    "\n"
                    "**Checked before it is written.** `generate` re-reads its own output and lists every way it breaks\n"
                    "the rules above. If anything is listed, no file is written, so an edited template that drops a gate\n"
                    "cannot produce a pipeline.\n"
                    "\n"
                    "## Recording it\n"
                    "\n"
                    "`--record-adr` records one **Proposed** Atlas-native ADR for the generated pipeline on\n"
                    "`atlas-system`, slug `first-cicd-<platform>-<digest of the files>`, numbered with that node's\n"
                    "prefix or `--adr-prefix` (default `SYS`). The same files again write nothing. Changed files for the\n"
                    "same platform write a new ADR that supersedes the previous pipeline ADR for that platform. If\n"
                    "`atlas-system` is missing, the command refuses before writing any file.\n"
                    "\n"
                    "When it finishes, the command reminds you to review the files, fill every `TODO(atlas)` and commit\n"
                    "them yourself. Nothing is pushed.\n"
                    "\n"
                    "## The crunch-week variant\n"
                    "\n"
                    "With `--crunch FILE` or `--crunch-from-db`, the generator produces the variant that\n"
                    "crunch week (document *Crunch week guide* on node `atlas-system-crunch`) runs:\n"
                    "\n"
                    "- The matrix goes through the crunch overlay first, so the environments in `skip_envs` have no\n"
                    "  deploy job.\n"
                    "- Production's deploy job depends on the checks alone, and keeps exactly the gate the matrix gives\n"
                    "  production: a person still approves each deploy.\n"
                    "- With backup policy `per_deploy`, production's deploy job runs\n"
                    "  `atlas-admin backup run --reason per_deploy` as a step **before** the deploy step, in the same\n"
                    "  job, after the approval. A failed backup fails the job, and the deploy step does not run. Wire\n"
                    "  the command to where your database is; until then it fails, which also stops the deploy. The\n"
                    "  other policies (`once`, `hourly`, `daily`) are not the pipeline's; see\n"
                    "  Backups (document *Crunch week guide* on node `atlas-system-crunch`).\n"
                    "- A comment at the top of the file and a section in the settings file say it is the crunch\n"
                    "  variant, until when, which steps it skips, and to generate again without `--crunch` when crunch\n"
                    "  week ends.\n"
                    "- `--record-adr` is refused with a crunch flag: the variant is temporary, and crunch week records\n"
                    "  its own ADR.\n"
                    "\n"
                    "Without a crunch flag, or with `--crunch-from-db` while crunch week is off, the output is byte for\n"
                    "byte the normal pipeline.\n"
                ),
                "from": "docs/oss/guides/first-cicd.md",
            },
        ],
    },
    {
        "id": "atlas-system-crunch",
        "type": "capability",
        "parent": "atlas-system",
        "name": "Crunch week",
        "summary": "A release mode for a fixed period: releases go straight out, with a verified backup at the start and the end.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "overview.md",
                "title": "Overview",
                "kind": "architecture",
                "content": (
                    "# Crunch week\n"
                    "\n"
                    "A release mode for a fixed period: releases go straight out, with a verified backup at the start and the end.\n"
                    "\n"
                    "## Surface\n"
                    "\n"
                    "Generated: the parts of Atlas that serve this capability.\n"
                    "\n"
                    "- API area `atlas-system-api-crunch` (Crunch week routes)\n"
                ),
            },
            {
                "file": "crunch-week-guide.md",
                "title": "Crunch week guide",
                "kind": "runbook",
                "content": (
                    "# Crunch week\n"
                    "\n"
                    "Crunch week is a release mode that runs for a set time. While it is on, a change that passes CI\n"
                    "deploys to production **without the staging and pre-production steps** that your\n"
                    "guardrail matrix (document *Guardrails guide* on node `atlas-system-guardrails`) normally puts in front of production. Use it for a release push,\n"
                    "when an hour in staging costs more than it protects. It ends by itself at the end time you set.\n"
                    "\n"
                    "It is **off by default**. An install that never turns it on has no crunch tables in its database,\n"
                    "and nothing behaves differently.\n"
                    "\n"
                    "## What it does, and what it does not\n"
                    "\n"
                    "Crunch week is a **recorded decision with a time limit**, not a switch inside your CI system.\n"
                    "Atlas stores the window, shows it, records every change to it, and feeds it to the tools that\n"
                    "produce your release path. Atlas never deploys anything, never pushes to a repository, and never\n"
                    "edits your pipeline in place.\n"
                    "\n"
                    "While it is on:\n"
                    "\n"
                    "- `atlas-admin cicd generate --crunch...` produces the **crunch variant** of your pipeline. In it,\n"
                    "  the environments you chose to skip have no deploy job, and production deploys after the checks\n"
                    "  alone. See First CI/CD (document *First CI/CD guide* on node `atlas-system-guardrails`).\n"
                    "- `atlas-admin guardrails show --crunch...` and `guardrails export --crunch...` show and export the\n"
                    "  matrix as crunch week runs it (see [below](#what-it-changes-in-the-guardrail-matrix)).\n"
                    "- With a backup policy, backups are taken and verified (see [Backups](#backups)).\n"
                    "- Every signed-in person sees a banner on the board.\n"
                    "\n"
                    "It does **not**:\n"
                    "\n"
                    "- **Remove CI.** The production deploy job still depends on the checks.\n"
                    "- **Remove production's gate.** Production keeps exactly the gate the matrix gives it. In the\n"
                    "  generated pipeline a person still approves each production deploy.\n"
                    "- **Give agents deploy rights.** An agent never holds deploy rights in a production environment,\n"
                    "  crunch week or not. The matrix refuses it before and after the crunch overlay.\n"
                    "- **Change the matrix stored on the install.** The guardrail document, the `guardrail-<env>`\n"
                    "  intents that agents read with `atlas_get`, and the guardrails ADR stay as they were. The overlay\n"
                    "  is a view that the `--crunch` flags compute; nothing applies it.\n"
                    "- **Change your repository.** You generate the crunch variant, review it and commit it yourself.\n"
                    "  When crunch week ends, generate again without `--crunch` and commit that.\n"
                    "- **Run backups by itself**, unless you turned a backup source on (see [Backups](#backups)).\n"
                    "\n"
                    "## Who can turn it on\n"
                    "\n"
                    "Only the **instance owner** (the `owner` role), **in a browser session**, and **with their password\n"
                    "on every change**: activate, change, extend and end.\n"
                    "\n"
                    "- The control is a tab in **Settings**, labelled *Crunch*. The board builds it only for an owner\n"
                    "  in a browser session. For everyone else it is not in the page at all.\n"
                    "- **No token can read or change it**, whatever its scopes, an owner's `admin` token included. The\n"
                    "  routes answer every token with `403 session_required`. A member's session gets `403 forbidden`.\n"
                    "- **No MCP tool exists for it**, and no agent path reaches it.\n"
                    "- A wrong password is `403 password_incorrect`. It is recorded as a failed login (`login_fail`, reason\n"
                    "  `crunch:...`) and counts against the login rate limiter, exactly like a failed sign-in.\n"
                    "- An owner who signs in **only through single sign-on** has no Atlas password, so the step-up\n"
                    "  cannot pass and that owner cannot change crunch week. Use a local owner account for it.\n"
                    "\n"
                    "The routes, for reference (all browser-session only, `Cache-Control: no-store`):\n"
                    "\n"
                    "| Route | Who | What |\n"
                    "|---|---|---|\n"
                    "| `GET /api/crunch` | owner | state, last 10 history entries, the matrix's environments, last backup |\n"
                    "| `PUT /api/crunch` | owner, password | activate, change or extend: `{config, password, csrf_token}` |\n"
                    "| `POST /api/crunch/end` | owner, password | end now: `{password, csrf_token}` |\n"
                    "| `GET /api/crunch/banner` | any signed-in person | `{active, ends_at}` and nothing else |\n"
                    "\n"
                    "Atlas records every act on the system node `atlas-system`. If that node is missing, every act is\n"
                    "refused with `409 system_node_missing` before anything is written, and the message says how to add\n"
                    "it (`python system_node.py --apply` in the API image).\n"
                    "\n"
                    "## The settings\n"
                    "\n"
                    "The page in Settings explains the mode with a diagram of the normal path beside the crunch path,\n"
                    "then shows the settings, then an Activate button. Activate opens a confirmation dialog that\n"
                    "restates everything and asks for your password.\n"
                    "\n"
                    "The request body is the `config` object below. Unknown fields are refused. Every refusal is\n"
                    "`400 bad_config` and names the field (for example `backup.policy` or `skip_envs[1]`), and a typo is\n"
                    "caught before the password is checked, so it costs no password attempt.\n"
                    "\n"
                    "| Field | Meaning |\n"
                    "|---|---|\n"
                    "| `starts_at` | Leave it out to start **now**, by the server's clock. A time you send is a **scheduled** start: it must be in the server's future, at most 31 days ahead. |\n"
                    "| `ends_at` | The end. After the start and after now, at most 31 days after the start. |\n"
                    "| `duration_hours` | Instead of `ends_at`: a whole number of hours, 1 to 744 (31 days). Send exactly one of the two. |\n"
                    "| `backup.policy` | `none` (default), `once`, `per_deploy`, `hourly` or `daily`. |\n"
                    "| `backup.retention_days` | `0` with policy `none`; otherwise 1 to 365, default 7. |\n"
                    "| `skip_envs` | The environments of the guardrail matrix in force whose step is skipped. Never a production environment. |\n"
                    "\n"
                    "Every timestamp must carry a timezone; a time without one is refused, not guessed.\n"
                    "\n"
                    "### Duration and the server's clock\n"
                    "\n"
                    "**The server's clock decides.** No client can move a window with its own clock:\n"
                    "\n"
                    "- *Start now* means the request has no `starts_at`. The server sets the start at the moment it\n"
                    "  writes, the same instant as the activation time.\n"
                    "- A start in the past is refused. A start is never backdated, and a change never moves the start\n"
                    "  earlier.\n"
                    "- With `duration_hours`, the server computes the end. For a new window the end is the start plus\n"
                    "  the length. For a window that is already set (running or scheduled) the length is **added to its\n"
                    "  current end**: that is how the Extend box on the page works.\n"
                    "- The stored state, every event and every ADR carry `ends_at` only, never `duration_hours`.\n"
                    "\n"
                    "A scheduled window is set but not yet in force: there is no banner, and the release tools treat\n"
                    "crunch week as off until the start. You can still change it or end it.\n"
                    "\n"
                    "### Extend, change and end\n"
                    "\n"
                    "On a window that is set, a request is an **extend** when the only difference is a later end, and a\n"
                    "**change** otherwise. The same settings again are no act at all: nothing is written. Ending\n"
                    "(`POST /api/crunch/end`) works on a running or a scheduled window. Each of these needs the\n"
                    "confirmation and the password again.\n"
                    "\n"
                    "## Expiry\n"
                    "\n"
                    "Crunch week is off from the moment `now >= ends_at`: every reader of the state treats it as off at\n"
                    "once. The **first read after the end** also records the end, in its own transaction: the state is\n"
                    "marked ended with reason `expired`, and one history row, one graph event and one ADR are written.\n"
                    "A read is anything that asks for the state, such as the board's banner (it asks every five minutes\n"
                    "for every signed-in person), the Settings page, the backup loop or a `--crunch-from-db` command.\n"
                    "The write is conditioned on the revision it read, so concurrent readers record the end exactly\n"
                    "once. Nobody acted, so it is recorded as `system`.\n"
                    "\n"
                    "## What it records\n"
                    "\n"
                    "Every act (activate, change, extend, end, by a person or by expiry) writes, in one transaction:\n"
                    "\n"
                    "1. **The state row**, with its revision increased by one, and one row in `crunch_history`. That\n"
                    "   table is append-only by convention and feeds the history on the Settings page.\n"
                    "2. **One graph event** on `atlas-system`: `node.crunch.activate`, `node.crunch.change`,\n"
                    "   `node.crunch.extend` or `node.crunch.end`. Its detail holds the ADR id, the revision, the start\n"
                    "   and end, the backup policy and retention, the skipped environments and, for an end, the reason\n"
                    "   (`manual` or `expired`). It holds no password, pepper or token.\n"
                    "3. **One ADR** on `atlas-system`, status **Accepted**: the password step-up on the change is the\n"
                    "   owner's act of deciding, and an expiry is the end date that was decided at activation taking\n"
                    "   effect. It is numbered with `atlas-system`'s ADR prefix (`SYS` when that node has no ADRs yet),\n"
                    "   states the act, who, when, the window, the backup policy and the skipped environments, and an\n"
                    "   ADR for a change, extend or end **amends** the previous one. If the prefix belongs to another\n"
                    "   project, the act is refused with `409 adr_prefix_taken`.\n"
                    "\n"
                    "A backup that a pipeline or the backup loop reports adds a `node.crunch.backup` event and a history\n"
                    "row, with detail `{ok, verified, label}`. The label is the dump's file name, never a path or a\n"
                    "credential.\n"
                    "\n"
                    "## The banner\n"
                    "\n"
                    "While crunch week is in force, every signed-in person sees a line across the top of the board:\n"
                    "*Crunch week: production deploys directly until* followed by the end time. It reads\n"
                    "`GET /api/crunch/banner`, which tells any signed-in browser session whether crunch week is on and\n"
                    "until when, and nothing else. It reads again every five minutes and whenever the Settings page\n"
                    "changes the state, and it hides itself when the end time passes. If the request fails, no banner is\n"
                    "drawn.\n"
                    "\n"
                    "## What it changes in the guardrail matrix\n"
                    "\n"
                    "The crunch overlay (`guardrails.crunch_overlay`) is a pure function. For each environment in\n"
                    "`skip_envs` it removes the deploy actions (`deploy`, `deploy_via_pipeline`, `approve_deploy`) from\n"
                    "`agent`, `human` and `step_up`, and adds a note. That environment is then no deploy step at all,\n"
                    "and the pipeline generator leaves it out of the path to production. Its branches, their\n"
                    "protection, its other actions and its command patterns stay. A command pattern of a deploy action\n"
                    "the environment no longer holds is exported as a **deny**, so the overlay can only narrow what an\n"
                    "agent may do there.\n"
                    "\n"
                    "Production environments are never changed: a `skip_envs` naming one is refused, and the overlay\n"
                    "checks afterwards that every production environment is byte for byte what it was. The result is\n"
                    "validated again, so the standing rules hold after the overlay exactly as before it.\n"
                    "\n"
                    "```sh\n"
                    "docker compose exec atlas-api atlas-admin guardrails show --crunch-from-db\n"
                    "docker compose exec atlas-api atlas-admin guardrails export --format claude-code \\\n"
                    "    --crunch-from-db --out -\n"
                    "```\n"
                    "\n"
                    "`--crunch FILE` reads a crunch config from a JSON or YAML file instead of the database: the same\n"
                    "fields as the [configuration](#the-settings), with `ends_at` or `duration_hours`. The release path\n"
                    "reads only `skip_envs` and `backup`, and refuses a key it does not know, so a misspelt `skip_env`\n"
                    "cannot quietly skip nothing. With a `duration_hours` file no end time is known, so the crunch\n"
                    "variant's header names none. With crunch week off, `--crunch-from-db` changes nothing and prints a\n"
                    "note saying so.\n"
                    "\n"
                    "### With no guardrail matrix\n"
                    "\n"
                    "Crunch week works without a matrix, but it skips nothing:\n"
                    "\n"
                    "- `skip_envs` must be empty. A non-empty list is refused (`400 bad_config`, field `skip_envs`),\n"
                    "  and the message says to apply a matrix first (`atlas-admin guardrails apply`).\n"
                    "- The Settings page offers no environment to skip.\n"
                    "- The window, the banner, the history, the events, the ADRs and the backups work as usual.\n"
                    "- `guardrails show` and `guardrails export` without `--from FILE` stop with \"no guardrails matrix\n"
                    "  is applied\", crunch flag or not.\n"
                    "\n"
                    "The environments are checked against the matrix **in force at the time of the request**. The\n"
                    "release tools check `skip_envs` again against the matrix they are given, so a pipeline generated\n"
                    "from a matrix whose environment names differ is refused.\n"
                    "\n"
                    "## Backups\n"
                    "\n"
                    "Backups are an install's choice, and the default policy is `none`. Atlas only records the policy;\n"
                    "a backup runs when one of three things asks for it:\n"
                    "\n"
                    "| Policy | What takes the backup |\n"
                    "|---|---|\n"
                    "| `none` | Nothing. |\n"
                    "| `once` | The `backup` compose profile's loop: one backup per activation. |\n"
                    "| `per_deploy` | A step in the crunch variant of the pipeline, before each production deploy. |\n"
                    "| `hourly`, `daily` | The `backup` compose profile's loop, on that interval while crunch week runs. |\n"
                    "\n"
                    "An operator can also take a backup at any time with `atlas-admin backup run` (reason `manual`).\n"
                    "\n"
                    "**The loop** is the `atlas-backup` service, in the opt-in `backup` profile of\n"
                    "`deploy/oss/docker-compose.yml`. It is not started by default:\n"
                    "\n"
                    "```sh\n"
                    "docker compose --profile backup up -d        # or COMPOSE_PROFILES=backup in .env\n"
                    "```\n"
                    "\n"
                    "Every 60 seconds it asks whether crunch week is in force. It acts only when the policy is `once`,\n"
                    "`hourly` or `daily`; otherwise it says so once and waits. A failed attempt is retried after 15\n"
                    "minutes, not every tick.\n"
                    "\n"
                    "**The per-deploy step** runs `atlas-admin backup run --reason per_deploy` in the production deploy\n"
                    "job, after the approval and before the deploy step. A failed backup fails the job, so the deploy\n"
                    "does not run. The command is a placeholder you wire to where your database is; until then it\n"
                    "fails, which also stops the deploy. When crunch week is off, or its policy is not `per_deploy`, the\n"
                    "command says it skipped and exits 0.\n"
                    "\n"
                    "A reason that is not the active policy's writes nothing and says so:\n"
                    "\n"
                    "```sh\n"
                    "docker compose run --rm atlas-backup atlas-admin backup run --reason per_deploy\n"
                    "docker compose run --rm atlas-backup atlas-admin backup run            # manual, always runs\n"
                    "docker compose run --rm atlas-backup atlas-admin backup run --verify-only /var/lib/atlas/backups\n"
                    "```\n"
                    "\n"
                    "### How a backup is verified\n"
                    "\n"
                    "A file is not called a backup until it has been restored and counted:\n"
                    "\n"
                    "1. A logical `pg_dump --format=custom` of the Atlas database is taken from one database snapshot,\n"
                    "   and the rows of `node`, `edge` and `graph_event` are counted in that same snapshot.\n"
                    "2. The file's sha256 is written beside it.\n"
                    "3. The checksum is checked again, the dump is restored with `pg_restore --exit-on-error` into a\n"
                    "   **temporary database in the same Postgres**, the three tables are counted there, and the\n"
                    "   temporary database is dropped. Any difference is a failure.\n"
                    "4. A manifest (`<file>.json`: counts, sha256, size, reason, versions) is written last.\n"
                    "\n"
                    "A dump that fails verification is deleted with its checksum, and the failure is recorded. Files\n"
                    "are named `atlas-<UTC time>-<reason>.dump` and land in the `atlas-backups` volume.\n"
                    "\n"
                    "**Retention** is the crunch config's `retention_days`. After a verified backup only, older files\n"
                    "of that naming pattern are deleted. Nothing else in the directory is touched, nothing is pruned\n"
                    "after a failure, and a `manual` run with crunch week off prunes nothing.\n"
                    "\n"
                    "**Credentials.** The database URL is read from the same secret file as `atlas-api`. The password\n"
                    "never reaches a command line, an environment value or any output.\n"
                    "\n"
                    "For routine backups outside crunch week, see Operations (document *Operations guide* on node `atlas-system-install`).\n"
                    "\n"
                    "## Risks\n"
                    "\n"
                    "- **Less testing before production.** Skipped environments catch nothing while crunch week is on.\n"
                    "  Keep the window short, and use a backup policy.\n"
                    "- **A stale crunch pipeline.** The crunch variant is a file you committed. Atlas does not remove\n"
                    "  it when crunch week ends; regenerate without `--crunch` and commit that.\n"
                    "- **Backups need a source.** `once`, `hourly` and `daily` do nothing unless the `backup` profile\n"
                    "  runs. `per_deploy` does nothing unless the crunch variant is committed and its backup step is\n"
                    "  wired.\n"
                ),
                "from": "docs/oss/guides/crunch-week.md",
            },
        ],
    },
    {
        "id": "atlas-system-config",
        "type": "capability",
        "parent": "atlas-system",
        "name": "Configuration and feature flags",
        "summary": "Every setting described once, shown to an owner with its effective value and source; optional features switched off by name.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "overview.md",
                "title": "Overview",
                "kind": "architecture",
                "content": (
                    "# Configuration and feature flags\n"
                    "\n"
                    "Every setting described once, shown to an owner with its effective value and source; optional features switched off by name.\n"
                    "\n"
                    "## Surface\n"
                    "\n"
                    "Generated: the parts of Atlas that serve this capability.\n"
                    "\n"
                    "- API area `atlas-system-api-config` (Configuration routes)\n"
                ),
            },
            {
                "file": "configuration.md",
                "title": "Configuration",
                "kind": "runbook",
                "content": (
                    "# Configuration\n"
                    "\n"
                    "The settings of Configuration and feature flags, from `backend/settings_catalog.py`. Every setting, with its default and where it is read, is in the configuration reference (CONFIG.md); an owner sees the effective values in the board's Config tab.\n"
                    "\n"
                    "| Setting | Kind | Read by | What it is |\n"
                    "|---|---|---|---|\n"
                    "| `ATLAS_CONFIG_WRITES` | plain | atlas-api | Let an owner change the changeable settings from the board, with their password. |\n"
                    "| `ATLAS_DISABLED_FEATURES` | plain | atlas-api, atlas-mcp | Optional features switched off by name; empty means every feature is on. |\n"
                    "| `ATLAS_PUBLIC_URL` | plain | atlas-api | The one absolute URL the install is reached on. |\n"
                    "| `ATLAS_PRODUCT_NAME` | plain | atlas-api | The product's display name. |\n"
                    "| `ATLAS_PRIVACY_CONTACT` | plain | atlas-api | The mailbox the privacy page names. |\n"
                    "| `ATLAS_PRIVACY_HOSTING` | plain | atlas-api | Where the privacy page says the data is hosted, as plain text. |\n"
                    "| `ATLAS_LANDING` | plain | atlas-api | Which public landing page / serves: v1 (today's) or v2 (the product website). |\n"
                    "| `ATLAS_FRONTEND_DIR` | plain | atlas-api | The directory the board and its assets are served from. |\n"
                ),
            },
            {
                "file": "configuration-panel-guide.md",
                "title": "Configuration panel guide",
                "kind": "runbook",
                "content": (
                    "# The configuration panel\n"
                    "\n"
                    "Atlas describes its own parts as nodes: the system node `atlas-system` and one child per part\n"
                    "(`atlas-system-api`, `atlas-system-mcp`, `atlas-system-postgres`, `atlas-system-gateway`,\n"
                    "`atlas-system-search`, `atlas-system-retrieval`). On each of these nodes the board's node panel has a\n"
                    "**Configuration** tab for the instance owner. It lists every setting that part reads, its effective\n"
                    "value, and where that value comes from.\n"
                    "\n"
                    "Showing is always on. **Changing** a setting from the board is a second phase that is off unless the\n"
                    "install turns it on with `ATLAS_CONFIG_WRITES=1`. Every setting itself is documented in\n"
                    "Configuration.\n"
                    "\n"
                    "## Seeing the settings\n"
                    "\n"
                    "**Who.** The instance owner in a browser session, or an agent token that holds the `admin` scope and\n"
                    "is linked to a live owner. Everyone else gets `403 forbidden`, and the board asks only for an owner,\n"
                    "so nobody else sees the tab or makes a request that fails. The configuration names hosts, origins,\n"
                    "identity-provider details and which secrets exist, which is operator knowledge.\n"
                    "\n"
                    "**Which nodes.** A node gets the tab when the configuration response has a part with its id: the\n"
                    "system node and its six part nodes. A part with no setting of its own still has a section, with a\n"
                    "note saying why (the MCP service and the gateway read their own settings; search has none of its\n"
                    "own).\n"
                    "\n"
                    "**What each row shows.** The data is `GET /api/config` (`Cache-Control: no-store`), one section per\n"
                    "part node:\n"
                    "\n"
                    "- **A plain setting** shows its effective value and its source:\n"
                    "\n"
                    "  | Source | Meaning |\n"
                    "  |---|---|\n"
                    "  | `env` | the variable is set in the API process's environment |\n"
                    "  | `derived` | unset, and computed from another variable that is set (for example the sign-in origins from `ATLAS_PUBLIC_URL`) |\n"
                    "  | `board` | set from the board (phase 2 only) |\n"
                    "  | `default` | unset; the code's default applies |\n"
                    "\n"
                    "- **A secret** shows only its state: `set from file`, `set from env` or `not set`. Never a value, a\n"
                    "  prefix, a hash or a length. The state is decided without opening the file: a configured path that\n"
                    "  names a readable, non-empty file counts as set from file.\n"
                    "- **A bootstrap setting** is one the API needs before it can start: the database URL, the password\n"
                    "  pepper, the token pepper and the sign-in origins. A bootstrap setting that holds a credential is\n"
                    "  shown exactly like a secret.\n"
                    "\n"
                    "The values are the running API process's own, read through the same code the API uses. A setting of\n"
                    "the retrieval add-on that is not read while the add-on is off says so instead of showing a value, and\n"
                    "the retrieval part carries the add-on's own status (off, on, or unavailable with the first problem\n"
                    "found). If a setting's current value does not parse, the row says so and shows no value.\n"
                    "\n"
                    "## Changing settings from the board (opt-in)\n"
                    "\n"
                    "**Off by default.** With `ATLAS_CONFIG_WRITES` unset or off, the write route is not registered at\n"
                    "all: `PUT /api/config/<name>` answers exactly like a path that never existed, the table that would\n"
                    "hold board values is never created or read, and the tab is read-only.\n"
                    "\n"
                    "With `ATLAS_CONFIG_WRITES=1`, a row that can be changed gets a **Change** control:\n"
                    "\n"
                    "- **Only the owner, in a browser session, with their password on every change.** No token can change\n"
                    "  configuration, whatever its scopes, an owner's `admin` token included (`403 session_required`). A\n"
                    "  member gets `403 forbidden`. A missing password is `403 step_up_required`, and a wrong one\n"
                    "  `403 password_incorrect`, recorded as a failed login (`login_fail`, reason `config_write:...`) that\n"
                    "  counts against the login rate limiter.\n"
                    "- **An owner who signs in only through single sign-on** has no Atlas password. With\n"
                    "  `ATLAS_OIDC_STEP_UP=on`, **Confirm with SSO** sends them through a fresh sign-in at the identity\n"
                    "  provider that names the setting, and the change is then saved without a password, once, within\n"
                    "  the step-up window. See\n"
                    "  Configuration changes by SSO owners (document *Identity and access guide* on node `atlas-system-identity`).\n"
                    "  Without step-up, such an owner changes configuration in the environment instead.\n"
                    "- **Only settings marked changeable.** Today these are `ATLAS_STRICT_API` (on or off) and\n"
                    "  `ATLAS_RETRIEVAL_TOP_K` (a whole number from 1 to 200). A setting is changeable only if it is a\n"
                    "  plain setting, its reader re-reads it on every use (so a board value really takes effect), and it is\n"
                    "  not a trust boundary. Every other setting is refused, each with a named reason:\n"
                    "\n"
                    "  | Refusal | When |\n"
                    "  |---|---|\n"
                    "  | `404 unknown_setting` | no such setting |\n"
                    "  | `403 bootstrap_read_only` | a bootstrap setting, in every mode, forever |\n"
                    "  | `403 secret_not_changeable` | a secret |\n"
                    "  | `403 not_changeable` | any other setting not marked changeable, with the reason |\n"
                    "  | `409 environment_wins` | the environment sets it (see below) |\n"
                    "  | `400 bad_value` | the value does not validate |\n"
                    "\n"
                    "- **Effect: at once.** Both changeable settings take effect immediately, and the response says\n"
                    "  `effect: \"now\"`. A value of `null` removes the board's value and returns the setting to its default.\n"
                    "\n"
                    "### Precedence: the environment wins\n"
                    "\n"
                    "1. **Environment.** A variable set in the API process's environment always wins. A board change to a\n"
                    "   setting the environment sets is refused with `409 environment_wins`, rather than stored and\n"
                    "   ignored. If the environment later starts setting a name the board had set, the environment wins\n"
                    "   from the next start; the board's value stays stored and unread until the variable is removed.\n"
                    "2. **Board.** A value set from the board is stored in the database (`config_value`) and applied\n"
                    "   again at every start, wherever the environment does not set that name.\n"
                    "3. **Default.** Neither sets it: the code's default.\n"
                    "\n"
                    "Board values assume one API process, which is how the image runs it. With several workers, a change\n"
                    "would reach only the worker that served it until the others restart.\n"
                    "\n"
                    "Turning the flag off later leaves the stored values in place and unread: every setting is then the\n"
                    "environment's or the default.\n"
                    "\n"
                    "### What a change records\n"
                    "\n"
                    "Every change is one `node.config.set` graph event on the setting's part node, by the person who made\n"
                    "it. Its detail names the setting, where its value came from before and after (for example `default`\n"
                    "to `board`) and when it takes effect. It **never holds the values**, old or new, because a node's\n"
                    "history is readable by more people than its configuration. Setting the same value again records\n"
                    "nothing. The value itself is in `config_value` and in `GET /api/config`.\n"
                    "\n"
                    "A malformed setting in the environment stops the API at start-up, with a message that names the\n"
                    "variable.\n"
                ),
                "from": "docs/oss/guides/configuration-panel.md",
            },
        ],
    },
    {
        "id": "atlas-system-install",
        "type": "capability",
        "parent": "atlas-system",
        "name": "Install, upgrade and releases",
        "summary": "The bundled install, its upgrade path, released images, certificates, backups and restore.",
        "attributes": {
            "baseload": {
                "source": "authored",
                "from": "tools/baseload/authored.json",
            },
        },
        "docs": [
            {
                "file": "overview.md",
                "title": "Overview",
                "kind": "architecture",
                "content": (
                    "# Install, upgrade and releases\n"
                    "\n"
                    "The bundled install, its upgrade path, released images, certificates, backups and restore.\n"
                    "\n"
                    "## Surface\n"
                    "\n"
                    "Generated: the parts of Atlas that serve this capability.\n"
                    "\n"
                    "- API area `atlas-system-api-assets` (Board and asset routes)\n"
                    "- API area `atlas-system-api-health` (Health routes)\n"
                    "- API area `atlas-system-api-landing` (Product site routes)\n"
                    "- command group `atlas-admin backup` (`atlas-system-cli-backup`)\n"
                    "- command group `atlas-admin init-db` (`atlas-system-cli-init-db`)\n"
                ),
            },
            {
                "file": "configuration.md",
                "title": "Configuration",
                "kind": "runbook",
                "content": (
                    "# Configuration\n"
                    "\n"
                    "The settings of Install, upgrade and releases, from `backend/settings_catalog.py`. Every setting, with its default and where it is read, is in the configuration reference (CONFIG.md); an owner sees the effective values in the board's Config tab.\n"
                    "\n"
                    "| Setting | Kind | Read by | What it is |\n"
                    "|---|---|---|---|\n"
                    "| `ATLAS_SEED` | plain | atlas-api | What startup seeds into an empty database: homelab, demo, none or self. |\n"
                    "| `ATLAS_LEGACY_V0` | plain | atlas-api | Create the first-generation tables and serve their routes. |\n"
                    "| `ATLAS_RESEED` | plain | atlas-api | Wipe and reseed the graph (never the accounts) at the next start. |\n"
                    "| `ATLAS_DB_URL` | bootstrap | atlas-api | The database URL, which carries its password. Required to start. |\n"
                    "| `ATLAS_CERT_EXPIRY_FILE` | plain | atlas-api | The PEM certificate whose expiry /api/health warns about; none means no field. |\n"
                    "| `ATLAS_CERT_EXPIRY_WARN_DAYS` | plain | atlas-api | How many days before that certificate expires /api/health starts to warn. |\n"
                    "| `ATLAS_IMAGE_API` | plain | compose | The released atlas-api image (tag@digest) to run instead of building it. |\n"
                    "| `ATLAS_IMAGE_MCP` | plain | compose | The released atlas-mcp image (tag@digest) to run instead of building it. |\n"
                    "| `ATLAS_IMAGE_BACKUP` | plain | compose | The released backup image (tag@digest) to run instead of building it. |\n"
                    "| `ATLAS_IMAGE_POSTGRES` | plain | compose | The released Postgres image (tag@digest) to run instead of building it. |\n"
                    "| `ATLAS_IMAGE_TLS` | plain | compose | The released TLS front image (tag@digest) to run instead of building it. |\n"
                    "| `COMPOSE_PROFILES` | plain | compose | The optional services to start (the profiles of deploy/oss/docker-compose.yml). |\n"
                    "| `COMPOSE_FILE` | plain | compose | The compose files docker compose reads, instead of -f options. |\n"
                    "| `ATLAS_TRUSTED_PROXY_CIDRS` | plain | atlas-api | Proxy networks whose client-address header is believed; empty means none. |\n"
                    "| `ATLAS_ADMIN_ACTOR` | plain | atlas-api | The operator name the admin command line records; none means the OS user. |\n"
                ),
            },
            {
                "file": "install.md",
                "title": "Install",
                "kind": "runbook",
                "content": (
                    "# Installing Atlas\n"
                    "\n"
                    "This guide takes you from a clean Linux VM to a running Atlas install that people can sign in to and\n"
                    "Claude Code can connect to. The last section covers upgrading an existing install.\n"
                    "\n"
                    "Everything here uses the files in `deploy/oss/`: one compose file (`docker-compose.yml`), one\n"
                    "configuration file (`.env`, copied from `.env.example`), and the secrets that\n"
                    "`scripts/init-secrets.sh` writes under `deploy/oss/secrets/`. Every step below is a step the install\n"
                    "smoke runs (`.github/workflows/install-smoke.yml`) on a clean runner for every change to these files.\n"
                    "The [table at the end](#how-this-guide-maps-to-the-install-smoke) shows which part of the smoke\n"
                    "proves each section.\n"
                    "\n"
                    "- Every setting is described in CONFIG.md. This guide names only the ones you must set.\n"
                    "- How big the VM should be is in Server requirements (document *Server requirements* on node `atlas-system-install`).\n"
                    "\n"
                    "## 1. Prerequisites\n"
                    "\n"
                    "On the VM that will run Atlas:\n"
                    "\n"
                    "- **A VM or host of its own.** Do not run Atlas in a WSL distro next to another Docker daemon\n"
                    "  (Docker Desktop, or Docker in a second distro): every WSL 2 distro shares one network stack, so\n"
                    "  the two daemons' firewall and bridge rules interfere with each other.\n"
                    "- **Linux with Docker Engine and the Docker Compose v2 plugin.** `docker compose version` must work,\n"
                    "  and `docker compose up` must accept `--wait` and `--wait-timeout`: Compose v2.17.0 or later\n"
                    "  (`--wait` came in v2.1.1, `--wait-timeout` in v2.17.0).\n"
                    "- **`git`, `bash`, `openssl` and `curl`.** `init-secrets.sh` uses `bash` and `openssl`; `curl`\n"
                    "  checks the install.\n"
                    "- **`python3`** (standard library only), if you will use the optional MCP gateway: its\n"
                    "  configuration is rendered by `scripts/render-gateway.py`.\n"
                    "- **A DNS name** (or a fixed IP address) that people and agents will use to reach the VM. The\n"
                    "  certificate is made for this name. This guide writes it as `atlas.yourcompany.example`:\n"
                    "  wherever that appears below, put your name.\n"
                    "- **Port 443 free** on the VM (or another port, see `ATLAS_TLS_PORT`), and 8444 if you will use the\n"
                    "  gateway.\n"
                    "- **Network access while building.** The images are built from the checkout, so the first\n"
                    "  `docker compose up --build` pulls base images and packages. Once running, no Atlas container\n"
                    "  needs a route out of the VM: only the TLS ingress is published, and every other network is\n"
                    "  internal.\n"
                    "\n"
                    "Size the VM from Server requirements (document *Server requirements* on node `atlas-system-install`).\n"
                    "\n"
                    "On the other machines:\n"
                    "\n"
                    "- **Claude Code runs on each user's own machine**, not on the VM (it can be the same machine, as in\n"
                    "  section 7). That machine needs the `claude` CLI, `python3` (section 7 uses it to\n"
                    "  pick the server entry out of a JSON file) and a copy of `secrets/tls/ca.crt`.\n"
                    "- **The machine with the browser** needs a copy of `secrets/tls/ca.crt` too. On Linux with\n"
                    "  Chromium the CA goes into the NSS database with `certutil`, from the package `libnss3-tools`\n"
                    "  (Debian, Ubuntu) or `nss-tools` (Fedora, RHEL).\n"
                    "\n"
                    "## 2. Clone and configure `.env`\n"
                    "\n"
                    "```sh\n"
                    "git clone <the Atlas repository URL> atlas\n"
                    "cd atlas/deploy/oss\n"
                    "cp .env.example .env\n"
                    "```\n"
                    "\n"
                    "All later commands run in `deploy/oss/` unless they say otherwise. Compose reads `.env` and resolves\n"
                    "`./secrets/` relative to this directory.\n"
                    "\n"
                    "Open `.env` and set the public name. `.env.example` uses `atlas.example.com` as its placeholder;\n"
                    "replace **every** occurrence with your name (`atlas.yourcompany.example` in this guide), and set\n"
                    "the privacy contact:\n"
                    "\n"
                    "| Variable | Set it to |\n"
                    "|---|---|\n"
                    "| `ATLAS_PUBLIC_HOST` | The DNS name, without scheme or port: `atlas.yourcompany.example` |\n"
                    "| `ATLAS_PUBLIC_URL` | The one absolute URL Atlas is reached on: `https://atlas.yourcompany.example`, plus `:<port>` if `ATLAS_TLS_PORT` is not 443 |\n"
                    "| `ATLAS_MCP_ALLOWED_HOSTS` | Your name, and your name with the port: `atlas.yourcompany.example,atlas.yourcompany.example:443,...`. Keep the other entries (`localhost:*`, `127.0.0.1:*`, `atlas-mcp:9444`) |\n"
                    "| `ATLAS_MCP_ALLOWED_ORIGINS` | `https://atlas.yourcompany.example` |\n"
                    "| `ATLAS_PRIVACY_CONTACT` | The mailbox your privacy page names, for example `privacy@yourcompany.example` |\n"
                    "\n"
                    "One substitution does all of it, which is what the smoke does (with its own test name). Put your\n"
                    "own name and mailbox on the right-hand side of each expression:\n"
                    "\n"
                    "```sh\n"
                    "sed -i -e 's/atlas\\.example\\.com/atlas.yourcompany.example/g' \\\n"
                    "       -e 's/privacy@example\\.com/privacy@yourcompany.example/' .env\n"
                    "diff .env.example .env    # the whole of what your install changed\n"
                    "```\n"
                    "\n"
                    "`ATLAS_PRIVACY_HOSTING`, where your privacy page says the data is hosted, has a usable default:\n"
                    "one PostgreSQL database on your own server, not copied to a third party. That is true of this\n"
                    "install as it stands, so change it (one plain-text paragraph) only if it is not true for you.\n"
                    "\n"
                    "`ATLAS_PUBLIC_URL` matters most: the login origins, the hosts on which an agent's Bearer token is\n"
                    "honoured, and the OIDC redirect URI are all derived from it. Keep it in step with\n"
                    "`ATLAS_PUBLIC_HOST` and `ATLAS_TLS_PORT`.\n"
                    "\n"
                    "Nothing else needs changing: with the substitution above (and `ATLAS_PRIVACY_HOSTING` if its\n"
                    "default is not true for you), `.env` is complete. The other values are the work install's defaults (project RBAC,\n"
                    "private by default, personal features off, secrets as files only); CONFIG.md explains\n"
                    "each one. `.env` must never hold a secret: secrets are files, made in the next step.\n"
                    "\n"
                    "### Optional: start with Atlas's own world model (`ATLAS_SEED=self`)\n"
                    "\n"
                    "`ATLAS_SEED` decides what an **empty** database starts with, once, at the first start. The\n"
                    "install default is `none`: an empty graph plus Atlas's handbook. Set `ATLAS_SEED=self` instead to\n"
                    "start with the world model Atlas keeps of itself: the `atlas` project and its parts (the API, the\n"
                    "MCP service, the board and its panel, the graph engine, search and retrieval, identity and\n"
                    "visibility, and so on), their documents, the edges between them, and a reviewed set of intents.\n"
                    "It carries **no work**: no epics, stories, tasks or decisions, no comments, accounts or grants.\n"
                    "You file your own work against these nodes, and change them as you like, by hand or with agents;\n"
                    "they are ordinary nodes.\n"
                    "\n"
                    "```sh\n"
                    "sed -i 's/^ATLAS_SEED=none$/ATLAS_SEED=self/' .env    # before the first `docker compose up`\n"
                    "```\n"
                    "\n"
                    "It changes nothing on a database that already holds a node, so setting it later does nothing.\n"
                    "To start over with it, the graph must be empty (a new volume). CONFIG.md\n"
                    "lists the profiles.\n"
                    "\n"
                    "The profile ships as data in the image (`backend/self_seed_data.py`). Maintainers regenerate it\n"
                    "from a running Atlas with `tools/export_self_seed.py`: `fetch` reads the `atlas` subtree over the\n"
                    "API with a read token passed as a file (`--token-file`), `build` writes the profile and two review\n"
                    "files under `tools/self_seed/` (which intents are seeded, and what was dropped), and `check`\n"
                    "fails on any forbidden pattern. Which nodes are left out is `tools/self_seed/scope.json`, and\n"
                    "which intents are seeded is `tools/self_seed/intents.json`; a change to either is reviewed in the\n"
                    "pull request like any other.\n"
                    "\n"
                    "## 3. Secrets and certificates: `init-secrets.sh`\n"
                    "\n"
                    "```sh\n"
                    "bash ../../scripts/init-secrets.sh\n"
                    "ls -l secrets secrets/tls\n"
                    "```\n"
                    "\n"
                    "It writes, under `./secrets/` and nowhere else:\n"
                    "\n"
                    "- `db_password` and `db_url`: the Postgres password, and the connection string that carries it;\n"
                    "- `password_pepper` and `token_pepper`: mixed into every password verifier and token digest.\n"
                    "- `tls/`: when there is no certificate there yet, it runs `scripts/init-certs.sh`, which makes a\n"
                    "  throwaway **install CA** and signs the certificates with it. The CA's private key is deleted\n"
                    "  as soon as it has signed, so nothing more can be signed with it. It writes:\n"
                    "  - `ca.crt`: the certificate every client must trust (browsers, `curl --cacert`, Claude Code);\n"
                    "  - `server.crt` and `server.key`: the certificate of the front door (`atlas-tls`);\n"
                    "  - `client-ca.crt`: the CA the API's TLS front trusts for client certificates (a copy of\n"
                    "    `ca.crt`), and `mcp-client.crt` with `mcp-client.key`: `atlas-mcp`'s client certificate for\n"
                    "    that leg;\n"
                    "  - `api-origin.crt`, `api-origin.key`, `mcp-origin.crt` and `mcp-origin.key`: the certificates\n"
                    "    `atlas-api` and `atlas-mcp` serve on the ingress's internal legs to them, and `origin-ca.crt`,\n"
                    "    which the ingress checks them against, so no hop in the install is plain HTTP.\n"
                    "\n"
                    "`server.key` is `0600`; the other files are `0644` inside the `0700` directories `secrets/` and\n"
                    "`secrets/tls/`, because the containers that read them do not run as you. The script ends by\n"
                    "printing section 4's command.\n"
                    "\n"
                    "It never prints a secret, and it refuses to overwrite an existing one.\n"
                    "\n"
                    "**Back up `./secrets/` with the database.** New peppers sign everyone out and void every token for\n"
                    "good, and the database keeps the password it was first created with. Never run\n"
                    "`init-secrets.sh --force` on an install that has data.\n"
                    "\n"
                    "**With your own PKI** (or to use Caddy's internal CA or ACME instead, see `ATLAS_TLS_MODE` in\n"
                    "CONFIG.md): put your files in place **before** running `init-secrets.sh`, and it uses\n"
                    "them as they are:\n"
                    "\n"
                    "```text\n"
                    "secrets/tls/server.crt   full chain; SANs: ATLAS_PUBLIC_HOST and atlas-api (add gateway for the gateway profile)\n"
                    "secrets/tls/server.key   its key\n"
                    "secrets/tls/ca.crt       your root; atlas-mcp trusts exactly this file\n"
                    "```\n"
                    "\n"
                    "`init-secrets.sh` then adds only what your PKI did not provide: `atlas-mcp`'s client certificate,\n"
                    "and the internal origin certificates under a separate CA of their own (`init-certs.sh\n"
                    "--origin-only`), which only the ingress trusts.\n"
                    "\n"
                    "To use a PostgreSQL you already run (a managed service or a database team's server) instead of the\n"
                    "bundled atlas-postgres, read External or managed PostgreSQL (document *External Postgres* on node `atlas-system-postgres`) first.\n"
                    "\n"
                    "## 4. Start the stack and check its health\n"
                    "\n"
                    "```sh\n"
                    "docker compose up -d --build --wait --wait-timeout 600\n"
                    "docker compose ps -a\n"
                    "```\n"
                    "\n"
                    "`--build` builds the images from your checkout; `--wait` returns once every service is healthy.\n"
                    "The first build takes several minutes. What starts, by compose **service** name (the SERVICE\n"
                    "column of `docker compose ps -a`; the container names, in the NAME column, carry the project\n"
                    "prefix and a number, such as `atlas-atlas-api-1`):\n"
                    "\n"
                    "| Service | What it is | Healthy means |\n"
                    "|---|---|---|\n"
                    "| `atlas-postgres` | Postgres 16 | `running`, `healthy` |\n"
                    "| `atlas-migrate` | A one-shot `atlas-migrate upgrade`: builds the schema, then exits | `exited (0)`; `atlas-api` does not start until it has |\n"
                    "| `atlas-api` | The board and the API (not published) | `running`, `healthy` |\n"
                    "| `atlas-mcp` | The MCP server (not published) | `running`, `healthy` |\n"
                    "| `atlas-tls` | The TLS ingress, the one published port | `running`, `healthy` |\n"
                    "\n"
                    "The optional services do not start unless you ask for them: `atlas-gateway` (section 6),\n"
                    "`atlas-backup` (the backup loop, see Operations (document *Operations guide* on node `atlas-system-install`)), and `atlas-retrieval`,\n"
                    "a one-shot that only checks the retrieval configuration and exits. Retrieval itself runs inside\n"
                    "`atlas-api` (section 8).\n"
                    "\n"
                    "Then check the front door from outside, with the install CA:\n"
                    "\n"
                    "```sh\n"
                    "curl --cacert secrets/tls/ca.crt https://atlas.yourcompany.example/healthz\n"
                    "```\n"
                    "\n"
                    "An HTTP 200 (`{\"ok\":true}`) means the ingress answers, the certificate verifies for your name, and\n"
                    "`atlas-api` is alive behind it. Without `--cacert` the same call fails with a certificate error\n"
                    "(curl exit 60): that is correct, and means nothing falls back to an unverified connection.\n"
                    "\n"
                    "The schema step can be re-run at any time and changes nothing on a database that is up to date:\n"
                    "\n"
                    "```sh\n"
                    "docker compose logs atlas-migrate | tail -20\n"
                    "docker compose exec -T atlas-api atlas-migrate upgrade\n"
                    "```\n"
                    "\n"
                    "On a new, empty database the log of the first run says\n"
                    "`[atlas-migrate] upgrade: empty database, schema created (...)`, with the number of tables,\n"
                    "columns and indexes it made. A re-run that finds nothing missing prints\n"
                    "`[atlas-migrate] upgrade: schema is up to date`, and one that adds what a new release needs says\n"
                    "`created what was missing (...)`. A failure prints `[atlas-migrate] upgrade FAILED: ...` and exits\n"
                    "1, and then `atlas-api` does not start.\n"
                    "\n"
                    "If a service is not healthy, `docker compose logs <service>` names the reason. The usual ones are a\n"
                    "value left at `atlas.example.com` in `.env`, or a `secrets/` file missing because `init-secrets.sh`\n"
                    "was run from another directory.\n"
                    "\n"
                    "**After a host restart**, run `docker compose ps -a` and the `/healthz` check above again.\n"
                    "`atlas-tls` is healthy only while it reaches both `atlas-api` and `atlas-mcp` through its own\n"
                    "legs, so an `unhealthy` `atlas-tls` with the other services healthy means the front door is down.\n"
                    "A 502 from the `curl`, with `i/o timeout` to the origin in `docker compose logs atlas-tls`, means\n"
                    "Docker's networking for the stack was lost in the restart. Recreate the containers; the volumes,\n"
                    "and with them the data, are kept:\n"
                    "\n"
                    "```sh\n"
                    "docker compose down\n"
                    "docker compose up -d --wait --wait-timeout 600\n"
                    "```\n"
                    "\n"
                    "## 5. The first owner and the first sign-in\n"
                    "\n"
                    "Create the first owner. Choose a username; `--break-glass` marks the account as the one that can\n"
                    "still sign in with a password if you later switch sign-in to SSO only (section 8):\n"
                    "\n"
                    "```sh\n"
                    "docker compose exec -T atlas-api atlas-admin \\\n"
                    "  user add <name> --role owner --break-glass --out /tmp/first-owner.password\n"
                    "```\n"
                    "\n"
                    "It prints `created <name> ...` and writes a **temporary password** to the `--out` file inside the\n"
                    "container, a new `0600` file, as `token mint` does in section 7; the terminal shows no secret.\n"
                    "Nobody, the operator included, chooses a password. Move the file out without printing it, and\n"
                    "delete the copy in the container:\n"
                    "\n"
                    "```sh\n"
                    "( umask 077; docker compose exec -T atlas-api cat /tmp/first-owner.password > ~/first-owner.password )\n"
                    "docker compose exec -T atlas-api rm /tmp/first-owner.password\n"
                    "```\n"
                    "\n"
                    "Hand it over out of band (not on the same channel as the username), then delete\n"
                    "`~/first-owner.password`. The temporary password is single use: the first sign-in with it goes\n"
                    "straight to a forced change (below), and from then on it signs nothing in.\n"
                    "\n"
                    "- **Without `--out`, the password is printed on your terminal**, once. Then run the command in a\n"
                    "  terminal that is not logged: not inside `script`, a recorded CI job, a shared tmux scrollback,\n"
                    "  or an agent session (Claude Code or another), because the password is part of the command's\n"
                    "  output and lands wherever that output is kept.\n"
                    "- **For the break-glass account, the vault holds the password set at the forced change**, not the\n"
                    "  temporary one, which is void after that first sign-in. Whoever looks after the vault\n"
                    "  does that first sign-in and stores the new password: it is how you get in when the identity\n"
                    "  provider is down.\n"
                    "- **If the temporary password is lost** (it scrolled away, or it expired unused), issue a new one\n"
                    "  with `docker compose exec -T atlas-api atlas-admin user reset-password <name>`. It prints the\n"
                    "  new one on the terminal, so the same care applies. Running `user add` again for the same name is\n"
                    "  refused: accounts are never recreated.\n"
                    "\n"
                    "`atlas-admin` records the operator who ran it as the shell user, or `--actor <name>` (before the\n"
                    "subcommand) if you pass one. More people are added the same way, without `--break-glass` and with\n"
                    "`--role member` (or another role; see Identity and access (document *Identity and access guide* on node `atlas-system-identity`)).\n"
                    "\n"
                    "**Sign in.** The browser must trust the install CA first (skip this if the certificate comes from\n"
                    "your PKI or ACME):\n"
                    "\n"
                    "- Copy `secrets/tls/ca.crt` (the certificate only; it is public) to the machine with the browser,\n"
                    "  if that is not the VM.\n"
                    "- Import it as a trusted certificate authority in the browser or the operating system's trust\n"
                    "  store. On Linux with Chromium it goes into the NSS database (`certutil` is in `libnss3-tools`);\n"
                    "  run this where the copy is, with the path to it:\n"
                    "  `certutil -d sql:$HOME/.pki/nssdb -A -t \"C,,\" -n atlas-install-ca -i secrets/tls/ca.crt`.\n"
                    "- Open `https://atlas.yourcompany.example/` and sign in with the username and temporary password.\n"
                    "- You are sent to the forced password change (`/auth/password/change`). Until you have changed it,\n"
                    "  the session reaches that form and nothing else.\n"
                    "- After the change you land on the board. File a first story to see that writes work.\n"
                    "\n"
                    "## 6. Optional: the MCP gateway (`--profile gateway`)\n"
                    "\n"
                    "The gateway is [AgentGateway](https://agentgateway.dev) in front of `atlas-mcp` (and any other MCP\n"
                    "servers you list), on its own HTTPS port. Agents can connect to it instead of to `/mcp` on the main\n"
                    "door; each call is still made with the agent's own token. Skip this section if you do not need it.\n"
                    "\n"
                    "```sh\n"
                    "python3 ../../scripts/render-gateway.py         # writes ./gateway/agentgateway.yaml\n"
                    "docker compose --profile gateway config --quiet\n"
                    "docker compose --profile gateway pull atlas-gateway\n"
                    "docker compose --profile gateway run --rm --no-deps atlas-gateway -f /etc/agentgateway/config.yaml --validate-only\n"
                    "docker compose --profile gateway up -d\n"
                    "curl --cacert secrets/tls/ca.crt -o /dev/null -w '%{http_code}\\n' https://atlas.yourcompany.example:8444/mcp\n"
                    "# 406\n"
                    "```\n"
                    "\n"
                    "- `render-gateway.py` reads the server list from `deploy/gateway/gateway.yaml` and the public\n"
                    "  names from `.env`, and refuses an unsafe configuration (an `http://` server that would be given a\n"
                    "  token, a CORS origin of `*`, an unknown option). To change the server list or the tool\n"
                    "  allow-list, copy `../gateway/gateway.yaml` to `gateway/servers.yaml`, edit the copy, and pass\n"
                    "  `--servers gateway/servers.yaml`. The header of `deploy/gateway/gateway.yaml` explains each field.\n"
                    "- `--validate-only` has the pinned gateway release parse the rendered file before it serves.\n"
                    "- MCP session-ID encryption is off by default (`config.session.key: disabled` in the rendered\n"
                    "  file): the gateway is optional and LAN-only, and a session ID is not a secret, since every call\n"
                    "  carries its own token. To turn it on, create a key file and name it in `.env`, then render and\n"
                    "  recreate:\n"
                    "\n"
                    "  ```sh\n"
                    "  ( umask 077; openssl rand -hex 32 > secrets/gateway_session_key )\n"
                    "  echo 'ATLAS_GATEWAY_SESSION_KEY_FILE=./secrets/gateway_session_key' >> .env\n"
                    "  python3 ../../scripts/render-gateway.py         # \"session IDs encrypted ... written 0600\"\n"
                    "  docker compose --profile gateway up -d --force-recreate atlas-gateway\n"
                    "  ```\n"
                    "\n"
                    "  The file must hold exactly 64 hex characters (a 32-byte AES-256-GCM key) and be readable by you\n"
                    "  alone; `render-gateway.py` refuses it otherwise, without printing it. AgentGateway v1.7.0 takes\n"
                    "  the key only inline in its configuration or as an environment variable, so the script writes it\n"
                    "  into `gateway/agentgateway.yaml` and writes that file `0600`: from then on the rendered file is a\n"
                    "  secret too (it is git-ignored; do not print or copy it). The key never goes in `.env`, on a\n"
                    "  command line or into the container's environment. A new key ends every open MCP session through\n"
                    "  the gateway; to turn encryption off again, remove the line from `.env` and render again.\n"
                    "- The `curl` gets an HTTP answer once the gateway's TLS door is up. `406` is the expected answer\n"
                    "  to a plain GET: the door works, and an MCP client, which sends the right `Accept` header, gets\n"
                    "  further. A certificate error (curl exit 60) means the CA is not trusted. No answer (`000`), or\n"
                    "  `SSL_ERROR_SYSCALL` (curl exit 35), means it is not up yet: `atlas-gateway` has no healthcheck\n"
                    "  (its image has no shell to run one), so `up -d` returns as soon as the container starts, and\n"
                    "  `--wait` would not wait any longer. Run the `curl` again after a few seconds.\n"
                    "- Agents then use `https://atlas.yourcompany.example:8444/mcp` (port `ATLAS_GATEWAY_PORT`) with their own\n"
                    "  token and the same CA.\n"
                    "- After changing `ATLAS_PUBLIC_URL`, `ATLAS_PUBLIC_HOST` or the server list, render again and\n"
                    "  recreate: `docker compose --profile gateway up -d --force-recreate atlas-gateway`.\n"
                    "- To start the gateway with every `docker compose up`, set `COMPOSE_PROFILES=gateway` in `.env`.\n"
                    "\n"
                    "## 7. Connect Claude Code (`clients/claude-code`)\n"
                    "\n"
                    "`clients/claude-code/README.md` is the full guide, including Windows and the plugin. On Linux or\n"
                    "macOS it comes to four steps. The token is never printed, never on a command line and never in an\n"
                    "environment variable: it lives in one file readable only by you.\n"
                    "\n"
                    "**Step 1: a token into `~/.config/atlas/token`.** There are two ways to get one:\n"
                    "\n"
                    "- **The person makes their own.** Anyone who signs in to the board with a password opens\n"
                    "  **Settings -> Agent credentials** and creates the token there, re-entering their password. The\n"
                    "  board shows it once; save it straight into `~/.config/atlas/token`, readable by you alone, with\n"
                    "  the commands below (paste the token after the `cat` line, press Enter, then Ctrl-D), and go on at\n"
                    "  the `cp` of the helper further down. Agents (document *Agents guide* on node `atlas-system-clients-claude-code`) has the details.\n"
                    "\n"
                    "  ```sh\n"
                    "  mkdir -p ~/.config/atlas && chmod 700 ~/.config/atlas\n"
                    "  ( umask 077; cat > ~/.config/atlas/token )\n"
                    "  chmod 600 ~/.config/atlas/token    # umask applies to a new file only; this covers an existing one\n"
                    "  stat -c %a ~/.config/atlas/token   # 600 (on macOS: stat -f %Lp)\n"
                    "  ```\n"
                    "\n"
                    "  The token goes through the terminal into the file, so it is not on a command line and not in\n"
                    "  your shell history.\n"
                    "- **An operator mints it** on the VM, in `deploy/oss/`. It is written only to the `--out` file\n"
                    "  inside the container:\n"
                    "\n"
                    "```sh\n"
                    "docker compose exec -T atlas-api atlas-admin \\\n"
                    "  token mint --user <name> --purpose claude-code --scopes read,write --out /tmp/claude-code.token\n"
                    "```\n"
                    "\n"
                    "On the machine that runs Claude Code (here the same machine; from another one, run the `cat` over\n"
                    "`ssh` with the redirect on your side), move the file out and delete the copy in the container:\n"
                    "\n"
                    "```sh\n"
                    "mkdir -p ~/.config/atlas && chmod 700 ~/.config/atlas\n"
                    "( umask 077; docker compose exec -T atlas-api cat /tmp/claude-code.token > ~/.config/atlas/token )\n"
                    "docker compose exec -T atlas-api rm /tmp/claude-code.token\n"
                    "chmod 600 ~/.config/atlas/token    # umask applies to a new file only; this covers an existing one\n"
                    "cp ../../clients/claude-code/helpers/atlas-mcp-headers.sh ~/.config/atlas/\n"
                    "sh ~/.config/atlas/atlas-mcp-headers.sh | sed -E 's/(Bearer )[^\"]+/\\1<redacted>/'\n"
                    "# {\"Authorization\":\"Bearer <redacted>\"}\n"
                    "```\n"
                    "\n"
                    "The first-run setup guide (`atlas_setup_guide`, the plugin's `setup-guide` skill) needs more: an\n"
                    "owner's token with the scopes `read,write,admin`, made by the owner under Settings -> Agent\n"
                    "credentials, or by an operator with `token mint --user <owner> ... --scopes read,write,admin`. A\n"
                    "`read,write` token gets 403 from it. Keep it in its own file next to the normal one, saved the same\n"
                    "way:\n"
                    "\n"
                    "```sh\n"
                    "( umask 077; cat > ~/.config/atlas/setup-token )    # paste, Enter, Ctrl-D\n"
                    "chmod 600 ~/.config/atlas/setup-token\n"
                    "stat -c %a ~/.config/atlas/setup-token              # 600\n"
                    "```\n"
                    "\n"
                    "The helper takes the token file as its argument, so after step 4 add a second server, `atlas-setup`,\n"
                    "that passes this file (the single quotes keep `$HOME` for the helper's shell to expand):\n"
                    "\n"
                    "```sh\n"
                    "claude mcp add-json atlas-setup '{\"type\":\"http\",\"url\":\"https://atlas.yourcompany.example/mcp/\",\"headersHelper\":\"sh \\\"$HOME/.config/atlas/atlas-mcp-headers.sh\\\" \\\"$HOME/.config/atlas/setup-token\\\"\"}' --scope user\n"
                    "claude mcp list\n"
                    "# atlas-setup: https://atlas.yourcompany.example/mcp/ (HTTP) - ✔ Connected\n"
                    "```\n"
                    "\n"
                    "**Restart Claude Code** before you use it: a running session does not load the new server's tools.\n"
                    "When the setup is done, remove the server (`claude mcp remove atlas-setup --scope user`), revoke the\n"
                    "token on the board or with `atlas-admin token revoke <id>`, and delete `~/.config/atlas/setup-token`.\n"
                    "\n"
                    "**Step 2: the template, pointed at your install.** Keep the `/mcp/` path and its trailing slash.\n"
                    "The file goes next to the token, not into the checkout:\n"
                    "\n"
                    "```sh\n"
                    "sed 's#https://atlas.example.com#https://atlas.yourcompany.example#' \\\n"
                    "  ../../clients/claude-code/mcp.template.json > ~/.config/atlas/atlas.mcp.json\n"
                    "```\n"
                    "\n"
                    "**Step 3: trust the install CA.** Copy `secrets/tls/ca.crt` (the certificate only) to the Claude\n"
                    "Code machine, for example to `~/.config/atlas/ca.crt`, and point `NODE_EXTRA_CA_CERTS` at it in the\n"
                    "`env` block of `~/.claude/settings.json` (merge it into the file if it already has an `env` block;\n"
                    "the path must be absolute, `$HOME` is not expanded there):\n"
                    "\n"
                    "```json\n"
                    "{ \"env\": { \"NODE_EXTRA_CA_CERTS\": \"/home/you/.config/atlas/ca.crt\" } }\n"
                    "```\n"
                    "\n"
                    "Then **restart Claude Code**. Node reads this variable only when the process starts, so `/mcp`\n"
                    "reconnect in a running session is not enough. (Exporting it in the shell you start `claude` from\n"
                    "also works, but only for that shell.)\n"
                    "\n"
                    "**Step 4: add it to Claude Code and check it.** `python3` picks the server entry out of the file:\n"
                    "\n"
                    "```sh\n"
                    "claude mcp add-json atlas \"$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))[\"mcpServers\"][\"atlas\"]))' ~/.config/atlas/atlas.mcp.json)\" --scope user\n"
                    "claude mcp list\n"
                    "# atlas: https://atlas.yourcompany.example/mcp/ (HTTP) - ✔ Connected\n"
                    "docker compose exec -T atlas-api atlas-admin token list --user <name>\n"
                    "```\n"
                    "\n"
                    "To test before you restart Claude Code, run the check with the variable set for that one command:\n"
                    "`NODE_EXTRA_CA_CERTS=\"$HOME/.config/atlas/ca.crt\" claude mcp list`.\n"
                    "\n"
                    "Check that `claude mcp get atlas` (or `/mcp` in a session) shows **your install's URL**. A\n"
                    "project's `.mcp.json` that defines a server with the same name takes precedence over the\n"
                    "user-scope one, with no warning, so in that project you would reach another server. Start\n"
                    "`claude` outside such a project, or add yours under another name.\n"
                    "\n"
                    "A certificate error means the CA step is missing (or Claude Code was not restarted after it); a\n"
                    "401 means the token file is wrong, revoked or expired. Rotation and revocation are in the client\n"
                    "pack's README.\n"
                    "\n"
                    "## 8. Optional: semantic retrieval\n"
                    "\n"
                    "The base install has full-text search only, and `atlas-api` has no route out of the VM. The\n"
                    "retrieval add-on runs inside `atlas-api` and calls an OpenAI-compatible embeddings endpoint that you\n"
                    "provide; no model ships with Atlas. Retrieval (document *Retrieval add-on guide* on node `atlas-system-retrieval`) explains it; turning it on is:\n"
                    "\n"
                    "1. Put the endpoint's key in `./secrets/embed_key` (one line; `chmod 0644`, inside the `0700`\n"
                    "   `secrets/` directory, for the same reason `init-secrets.sh` writes its files that way: the\n"
                    "   container's user is not you). An Azure endpoint that takes Entra ID tokens has no key: set\n"
                    "   `ATLAS_EMBED_AUTH_HEADER=entra` and `ATLAS_EMBED_KEY_SOURCE=/dev/null` instead, leave\n"
                    "   `ATLAS_EMBED_KEY_FILE` unset, and see\n"
                    "   Retrieval (document *Retrieval add-on guide* on node `atlas-system-retrieval`) for the\n"
                    "   workload-identity overlay.\n"
                    "2. Fill in the commented \"Retrieval\" block of `.env`: `ATLAS_RETRIEVAL=on`, `ATLAS_EMBED_URL`\n"
                    "   (`https://` is required), `ATLAS_EMBED_MODEL`, `ATLAS_EMBED_DIM`,\n"
                    "   `ATLAS_EMBED_KEY_FILE=/run/secrets/embed_key`, `ATLAS_EMBED_AUTH_HEADER`, and\n"
                    "   `ATLAS_EMBED_CA_BUNDLE=/run/secrets/atlas_ca` if the endpoint's certificate comes from this\n"
                    "   install's CA. Never put the key itself in `.env`. Before step 3,\n"
                    "   `docker compose --profile retrieval run --rm atlas-retrieval` checks the block: it reads it,\n"
                    "   refuses what could not be right, and exits, without contacting the endpoint. That one-shot is\n"
                    "   all the `atlas-retrieval` service does.\n"
                    "3. Recreate `atlas-api` with the overlay, which gives it its route out and the key:\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose -f docker-compose.yml -f retrieval.yml up -d --no-deps --wait atlas-api\n"
                    "   ```\n"
                    "\n"
                    "From then on, pass both files to every `docker compose` command for this install (or set\n"
                    "`COMPOSE_FILE=docker-compose.yml:retrieval.yml` in `.env`). A broken configuration never stops\n"
                    "Atlas: health reports `retrieval: unavailable: <reason>` or `degraded`, and search stays full-text.\n"
                    "To turn it off, remove the block from `.env` and recreate `atlas-api` on `docker-compose.yml` alone.\n"
                    "\n"
                    "## 9. Optional: single sign-on (OIDC)\n"
                    "\n"
                    "Until you do this, sign-in is local accounts with passwords. With OIDC, people sign in through your\n"
                    "identity provider (Entra ID, Keycloak or another OpenID provider), groups come from the IdP, and the\n"
                    "break-glass owner from section 5 is the only password login left.\n"
                    "\n"
                    "**Register Atlas in the IdP** as a confidential client (authorization code with PKCE), with the\n"
                    "redirect URI `https://atlas.yourcompany.example/auth/oidc/callback`.\n"
                    "\n"
                    "**Before switching**, move any existing local account that should become an SSO account, keeping\n"
                    "its history and its agents:\n"
                    "\n"
                    "```sh\n"
                    "docker compose exec -T atlas-api atlas-admin user link-idp <name> --subject <the IdP's id for them>\n"
                    "```\n"
                    "\n"
                    "A new SSO person is `user add <name> --no-password` then `link-idp` (SSO-only), or `user add <name>`\n"
                    "then `link-idp`, which lets them set a personal password later from the board. Either way they\n"
                    "reach the board through SSO at once; see\n"
                    "SSO-only, or SSO with a personal password (document *Identity and access guide* on node `atlas-system-identity`).\n"
                    "`link-idp` keeps the old username as the account's local sign-in name. With `local,oidc` in\n"
                    "`ATLAS_AUTH_PROVIDERS`, a personal password then signs in under that name when the IdP is down. With\n"
                    "the `oidc`-only block below, it does not; see\n"
                    "the fallback (document *Identity and access guide* on node `atlas-system-identity`).\n"
                    "\n"
                    "**The secrets**, as files under `./secrets/` (`chmod 0644`, as in section 8):\n"
                    "\n"
                    "- `secrets/oidc_client_secret`: the client secret (or use a key: `ATLAS_OIDC_TOKEN_AUTH=private_key_jwt`\n"
                    "  and `ATLAS_OIDC_CLIENT_KEY_FILE`, see CONFIG.md);\n"
                    "- `secrets/oidc_group_map.yml`: which IdP groups reach Atlas, and as which Atlas group:\n"
                    "\n"
                    "  ```yaml\n"
                    "  groups:\n"
                    "    <IdP group value>: <Atlas group name>\n"
                    "  ```\n"
                    "\n"
                    "**The overlay `oidc.yml`**, shipped in `deploy/oss/`, gives `atlas-api` those secrets and its\n"
                    "route to the IdP. In the base layout `atlas-api` sits on internal networks only, and it calls the\n"
                    "IdP itself (discovery, the token endpoint, the signing keys), so without the overlay \"Sign in with\n"
                    "SSO\" answers *Single sign-on unavailable*. The overlay adds two things and nothing else:\n"
                    "\n"
                    "- `atlas-api` joins the `egress` network, its route out of the host;\n"
                    "- the compose secrets `oidc_client_secret` (from `./secrets/oidc_client_secret`), `oidc_group_map`\n"
                    "  (from `./secrets/oidc_group_map.yml`) and `oidc_client_key` (empty unless you name a key file),\n"
                    "  each mounted at `/run/secrets/<name>`.\n"
                    "\n"
                    "Other host files are named in `.env` with `ATLAS_OIDC_CLIENT_SECRET_SOURCE`,\n"
                    "`ATLAS_OIDC_CLIENT_KEY_SOURCE` and `ATLAS_OIDC_GROUP_MAP_SOURCE`\n"
                    "(CONFIG.md). With `private_key_jwt`, set\n"
                    "`ATLAS_OIDC_CLIENT_KEY_SOURCE=./secrets/oidc_client_key` and\n"
                    "`ATLAS_OIDC_CLIENT_SECRET_SOURCE=/dev/null`. If the IdP's certificate comes from a private CA (an\n"
                    "internal Keycloak, for example), uncomment the `idp_ca` lines in `oidc.yml`; Entra ID needs none.\n"
                    "\n"
                    "**The settings.** Fill in the commented \"Sign-in\" block of `.env`, with the paths where `oidc.yml`\n"
                    "mounts the secrets:\n"
                    "\n"
                    "```sh\n"
                    "ATLAS_AUTH_PROVIDERS=oidc\n"
                    "ATLAS_LOCAL_LOGIN=break-glass\n"
                    "ATLAS_OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0\n"
                    "ATLAS_OIDC_PRESET=entra\n"
                    "ATLAS_OIDC_CLIENT_ID=<application (client) id>\n"
                    "ATLAS_OIDC_TOKEN_AUTH=client_secret_basic\n"
                    "ATLAS_OIDC_CLIENT_SECRET_FILE=/run/secrets/oidc_client_secret\n"
                    "ATLAS_OIDC_GROUP_MAP_FILE=/run/secrets/oidc_group_map\n"
                    "ATLAS_OIDC_REQUIRED_GROUPS=<the IdP group everyone who may sign in is in>\n"
                    "ATLAS_OIDC_MAX_SESSION_HOURS=12\n"
                    "```\n"
                    "\n"
                    "**Recreate `atlas-api`** with the overlay:\n"
                    "\n"
                    "```sh\n"
                    "docker compose -f docker-compose.yml -f oidc.yml up -d --no-deps --wait atlas-api\n"
                    "```\n"
                    "\n"
                    "As with retrieval, pass both files to every later `docker compose` command (or set\n"
                    "`COMPOSE_FILE=docker-compose.yml:oidc.yml` in `.env`; with retrieval too,\n"
                    "`docker-compose.yml:retrieval.yml:oidc.yml`).\n"
                    "\n"
                    "The login page now shows \"Sign in with SSO\". An account is created at a person's first login, as a\n"
                    "member, only if they are in one of `ATLAS_OIDC_REQUIRED_GROUPS`, and every later login checks that\n"
                    "again. The break-glass owner signs in with their password at the unlinked\n"
                    "`https://atlas.yourcompany.example/auth/login?break_glass=1`; every such sign-in is recorded as a\n"
                    "`login_break_glass` event. Identity and access (document *Identity and access guide* on node `atlas-system-identity`) covers group\n"
                    "mapping, offboarding (`ATLAS_OIDC_REVALIDATE_DAYS`) and letting Claude Code sign in with an IdP\n"
                    "token instead of a minted one.\n"
                    "\n"
                    "**Agents for SSO people.** On an SSO install, signing in through the IdP is the default way to\n"
                    "connect an agent: set `ATLAS_OAUTH_RESOURCE` (and its companions in the commented block below the\n"
                    "sign-in block of `.env`) and Claude Code signs in through your IdP with no minted token. For clients\n"
                    "that need a minted token, add `ATLAS_OIDC_STEP_UP=on` to `.env`: an SSO person then creates their\n"
                    "own agent credential in Settings, Agent credentials, after a fresh sign-in at the IdP, instead of\n"
                    "the password they do not have. With Entra ID, add the optional ID-token claim `auth_time` in the app\n"
                    "registration first. Without either, section 7's `atlas-admin token mint --user <idp>:<subject>` is\n"
                    "the way. Identity and access (document *Identity and access guide* on node `atlas-system-identity`) has\n"
                    "the details.\n"
                    "\n"
                    "## 10. Upgrading\n"
                    "\n"
                    "An upgrade is a new checkout, a rebuild, and the schema step. The schema step (`atlas-migrate\n"
                    "upgrade`) only creates what is missing; it never changes or deletes data, and running it twice\n"
                    "changes nothing.\n"
                    "\n"
                    "Releases are `vX.Y.Z` tags; RELEASES.md (document *Releases* on node `atlas-system-install`) has the versioning scheme and each release's\n"
                    "notes. Every release also publishes its images: to pull them instead of building, follow\n"
                    "Upgrading with released images (document *Releases* on node `atlas-system-install`), which is these same\n"
                    "steps with the images pinned by digest in `.env`.\n"
                    "\n"
                    "1. **Back up** the database and `./secrets/` together (see Operations (document *Operations guide* on node `atlas-system-install`)),\n"
                    "   for example:\n"
                    "\n"
                    "   ```sh\n"
                    "   ( umask 077; mkdir -p ~/atlas-backups &&\n"
                    "     docker compose exec -T atlas-postgres pg_dump -U atlas -d atlas > ~/atlas-backups/atlas-$(date +%F).sql )\n"
                    "   ( umask 077; tar -czf ~/atlas-backups/atlas-secrets-$(date +%F).tgz secrets .env )\n"
                    "   ```\n"
                    "\n"
                    "   The dump holds every account's password verifier and every token digest, and the archive holds\n"
                    "   the peppers and the database password, so treat both like `secrets/`: `umask 077` makes them\n"
                    "   readable by you alone, and they go outside the checkout, where a `git add` cannot pick them up.\n"
                    "   The archive is another unencrypted copy of the peppers on the VM: encrypt it and move it off\n"
                    "   the VM, or delete it once the upgrade is done\n"
                    "   (Back up the secrets (document *Operations guide* on node `atlas-system-install`)).\n"
                    "\n"
                    "2. **Get the new version** from the repository root, then return to `deploy/oss/`:\n"
                    "\n"
                    "   ```sh\n"
                    "   git rev-parse HEAD    # note it: the commit you upgrade from, for step 3\n"
                    "   git fetch --tags\n"
                    "   git checkout <the release tag>\n"
                    "   cd deploy/oss\n"
                    "   ```\n"
                    "\n"
                    "   If `git tag` lists no release tags (a repository that has not published one yet, or a copy made\n"
                    "   without its tags), there is no release to check out: upgrade to a newer commit of the default\n"
                    "   branch instead, with `git pull --ff-only`, and read `git log` since your old commit in place of\n"
                    "   the release notes.\n"
                    "\n"
                    "3. **Compare your `.env` with the new `.env.example`** and add any new settings the release's notes\n"
                    "   in RELEASES.md (document *Releases* on node `atlas-system-install`) ask for: `diff .env.example .env`. That diff also shows your own\n"
                    "   settings; to see only what the release changed in the example, use\n"
                    "   `git diff <old commit> -- .env.example` with the commit you noted in step 2. (After\n"
                    "   `git pull --ff-only`, `ORIG_HEAD` names it too; `git checkout` of a tag does not set it.)\n"
                    "   **An install made before the\n"
                    "   origin listeners** (no `secrets/tls/origin-ca.crt`) adds their certificates once, with no change\n"
                    "   to `ca.crt` or to any client:\n"
                    "   `ls secrets/tls/origin-ca.crt 2>/dev/null || bash ../../scripts/init-certs.sh --origin-only`\n"
                    "   (Operations, section 3 (document *Operations guide* on node `atlas-system-install`)).\n"
                    "4. **Rebuild and restart.** The `atlas-migrate` one-shot runs the schema step before `atlas-api`\n"
                    "   starts, exactly as on the first install:\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose up -d --build --wait --wait-timeout 600\n"
                    "   docker compose ps -a\n"
                    "   ```\n"
                    "\n"
                    "   With the retrieval or OIDC overrides, pass the same `-f` files as before (or rely on\n"
                    "   `COMPOSE_FILE` in `.env`).\n"
                    "5. **Run the schema step once more by hand**, and check the door:\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose exec -T atlas-api atlas-migrate upgrade\n"
                    "   curl --cacert secrets/tls/ca.crt https://atlas.yourcompany.example/healthz\n"
                    "   ```\n"
                    "\n"
                    "6. **With the gateway**, render its configuration again and recreate it:\n"
                    "   `python3 ../../scripts/render-gateway.py` and\n"
                    "   `docker compose --profile gateway up -d --force-recreate atlas-gateway`.\n"
                    "\n"
                    "An install whose database was first created before 2026-10-26 ran an older Postgres base image;\n"
                    "after upgrading, run the collation check from `deploy/oss/`:\n"
                    "`bash ../../tools/db_collation_check.sh \"$(docker compose ps -q atlas-postgres)\"`.\n"
                    "\n"
                    "## How this guide maps to the install smoke\n"
                    "\n"
                    "The install smoke (`.github/workflows/install-smoke.yml`) runs these steps on a clean runner, in\n"
                    "this order. Each leg is its own CI step, so a red run names the section that broke.\n"
                    "\n"
                    "| Guide section | Smoke leg | What the smoke runs there |\n"
                    "|---|---|---|\n"
                    "| 1. Prerequisites | *prepare the runner* | Python, Node, `docker compose version`, the test name in `/etc/hosts` |\n"
                    "| 2. Clone and `.env` | leg 1 | `cp .env.example .env`; `sed` of `atlas.example.com` and the privacy contact; `diff .env.example .env` |\n"
                    "| 3. `init-secrets.sh` | leg 1 | `bash ../../scripts/init-secrets.sh`; `ls -l secrets secrets/tls` |\n"
                    "| 4. Start and health | leg 1, leg 2, leg 6 | `docker compose up -d --build --wait --wait-timeout 600`; `docker compose ps -a` and every service's health; `atlas-migrate` exited 0, `atlas-migrate upgrade` twice more (leg 2); `curl --cacert ca.crt https://<host>/healthz` (leg 6, `tools/ci/oss_install_tls.sh`). The *after a host restart* note is not run |\n"
                    "| 5. First owner and sign-in | leg 3, leg 7, leg 9 | `atlas-admin user add <name> --role owner` and the forced change through the real form (leg 3, `tests/bootstrap_users.py`); `--break-glass` (leg 9); the install CA into the browser's NSS database, sign in, file a story, see it on the board (leg 7) |\n"
                    "| 6. Gateway | leg 5b, leg 6 | `render-gateway.py`; `--profile gateway config --quiet`, `pull`, `run ... --validate-only`, `up`; the TLS door on 8444 (leg 6, T5) |\n"
                    "| 7. Claude Code | leg 8 | `token mint --out`; the file moved without printing; the helper and the template installed; `claude mcp add-json ... --scope user` (the smoke picks the server entry out with `jq`, the guide with `python3`); `claude mcp list` reports Connected with `NODE_EXTRA_CA_CERTS` set for the command; `token list` |\n"
                    "| 8. Retrieval | leg 8b | `secrets/embed_key`; the Retrieval block in `.env`; `-f docker-compose.yml -f retrieval.yml up -d --no-deps --wait atlas-api`; back to the base file |\n"
                    "| 9. OIDC | leg 9 | `user add ... --break-glass`; `user link-idp`; the Sign-in block in `.env`; the client secret and group map as compose secrets; `up -d --no-deps --wait atlas-api` with the override |\n"
                    "| 10. Upgrading | leg 2, `upgrade` job | `docker compose exec -T atlas-api atlas-migrate upgrade`, idempotent (leg 2). The `upgrade` job: the previous release from its published images, a backup, then this checkout over the same `.env`, `secrets/` and volumes, `diff .env.example .env`, `up -d --build --wait`, the schema step by hand, the door, the collation check |\n"
                    "\n"
                    "**What the smoke does that is not an install step**, and so is not in the guide:\n"
                    "\n"
                    "- **Test scaffolding.** The test name `atlas.test` in `/etc/hosts`; a resource sampler for sizing;\n"
                    "  a compose override that starts `atlas-migrate`, `atlas-api` and `atlas-mcp` under an arbitrary\n"
                    "  UID (`1000770000:0`), proving the hardened images run as any user in group 0, the way OpenShift\n"
                    "  starts them; firewall rules that drop and count any connection leaving the stack's networks;\n"
                    "  the assertions themselves (`pytest` under `tests/`, the database checks in leg 9).\n"
                    "- **Accounts the tests need.** A second person, agents made with `atlas-admin bot add` and\n"
                    "  `token add --out -`, and an admin-scoped token for the RBAC tests (legs 3 to 5). An install\n"
                    "  makes agent tokens with `token mint`, as in section 7.\n"
                    "- **Test-only services.** Leg 5b also renders a restricted allow-list and a wrong CA, to prove the\n"
                    "  gateway enforces them, and ends on the shipped configuration. Leg 8b points retrieval at a stub\n"
                    "  embeddings endpoint (`tests/retrieval/`), never a real provider; leg 8c does the same in Entra\n"
                    "  mode, with the stub also standing in for the Entra token endpoint. Leg 9 runs a throwaway OpenID\n"
                    "  provider with its own CA (`tests/sso/docker-compose.idp.yml`) on a network shared with\n"
                    "  `atlas-api`. A real IdP is outside the VM, so leg 9b moves the same IdP onto the `egress` network\n"
                    "  only and shows sign-in failing without `oidc.yml` and working with it.\n"
                ),
                "from": "docs/oss/INSTALL.md",
            },
            {
                "file": "releases.md",
                "title": "Releases",
                "kind": "runbook",
                "content": (
                    "# Releases\n"
                    "\n"
                    "This page is how Atlas is versioned and released, what a release publishes, how to run an install\n"
                    "from the published images, and the notes for each release, newest first. The upgrade procedure\n"
                    "itself is INSTALL section 10 (document *Install* on node `atlas-system-install`); Operations section 3 (document *Operations guide* on node `atlas-system-install`)\n"
                    "explains its parts.\n"
                    "\n"
                    "## Versioning\n"
                    "\n"
                    "A release is a git tag `vMAJOR.MINOR.PATCH` ([Semantic Versioning](https://semver.org/)) on a commit\n"
                    "of the default branch. The first release is `v0.0.1-alpha`, a pre-release: the first public\n"
                    "version is an alpha.\n"
                    "\n"
                    "- **PATCH** (`v0.1.1`): fixes and security patches, typically new base image digests. No new\n"
                    "  setting is required and no one-time step is needed: back up, check out, rebuild or pull, done.\n"
                    "- **MINOR** (`v0.2.0`): new features. It may add settings (with defaults that keep today's\n"
                    "  behaviour) and, while the major version is 0, it may change a default or need a one-time step.\n"
                    "  Its notes below say which.\n"
                    "- **MAJOR** (`v1.0.0`): from 1.0 on, a change that breaks an existing install (a removed setting,\n"
                    "  a changed default, a data change the schema step cannot make) comes only with a new major\n"
                    "  version.\n"
                    "- **Pre-releases** (`v0.2.0-rc.1`): published the same way and marked as a pre-release. Do not\n"
                    "  run one in production.\n"
                    "\n"
                    "A tag names one commit, and that commit is the release: its checkout, its images and its notes\n"
                    "belong together. A tag is never moved or reused. A broken release is fixed by the next PATCH.\n"
                    "\n"
                    "## What a release publishes\n"
                    "\n"
                    "Pushing a `v*` tag runs the `images` workflow (`.github/workflows/images.yml`). It builds every\n"
                    "image the OSS install builds, from the same recipe, checks them and scans them with the same\n"
                    "vulnerability gate as every pull request (a fixable critical or high finding stops the release),\n"
                    "and only then publishes them to the GitHub container registry, each tagged with the release:\n"
                    "\n"
                    "| Image | Built from | Used by (`deploy/oss/docker-compose.yml`) |\n"
                    "|---|---|---|\n"
                    "| `ghcr.io/<owner>/gradinai-atlas-api` | `backend/Dockerfile`, target `hardened` | `atlas-api`, `atlas-migrate`, `atlas-retrieval` |\n"
                    "| `ghcr.io/<owner>/gradinai-atlas-mcp` | `mcp/Dockerfile`, target `hardened` | `atlas-mcp` |\n"
                    "| `ghcr.io/<owner>/gradinai-atlas-backup` | `backend/Dockerfile`, target `backup` (`hardened` plus `pg_dump` 16) | `atlas-backup` (profile `backup`) |\n"
                    "| `ghcr.io/<owner>/gradinai-atlas-postgres` | `deploy/postgres/pgvector.Dockerfile` (pgvector on PostgreSQL 16, `gosu` replaced, no `apt`) | `atlas-postgres` |\n"
                    "| `ghcr.io/<owner>/gradinai-atlas-tls` | `deploy/caddy/Dockerfile` (Caddy with the Cloudflare DNS plugin) | `atlas-tls` |\n"
                    "\n"
                    "From `v0.0.2-alpha` on, a release also publishes the **OpenShift images**, on Red Hat's Universal\n"
                    "Base Image 9 (UBI). The Compose install does not use them; the OpenShift manifests do\n"
                    "(deploy/openshift/README.md):\n"
                    "\n"
                    "| Image | Built from | Used by (`deploy/openshift`) |\n"
                    "|---|---|---|\n"
                    "| `ghcr.io/<owner>/gradinai-atlas-api-ubi` | `backend/Dockerfile`, target `hardened-ubi` (`ubi9/python-312-minimal`) | `atlas-api`, the migrate Job, the audit CronJob |\n"
                    "| `ghcr.io/<owner>/gradinai-atlas-mcp-ubi` | `mcp/Dockerfile`, target `hardened-ubi` (`ubi9/python-312-minimal`) | `atlas-mcp` |\n"
                    "| `ghcr.io/<owner>/gradinai-atlas-tls-ubi` | `deploy/caddy/Dockerfile.ubi` (stock Caddy built on `ubi9/go-toolset`, run on `ubi9/ubi-micro`; no plugin) | `atlas-tls` |\n"
                    "\n"
                    "Each carries its licence terms in `/licenses` and its own `name`, `vendor`, `version` (the\n"
                    "release tag), `release` (the commit), `summary` and `description` labels. Besides the\n"
                    "vulnerability gate, these three go through Red Hat's `preflight check container` on every pull\n"
                    "request that touches them and on the tag: a failure of the checks that matter for running on\n"
                    "OpenShift (`RunAsNonRoot`, `BasedOnUbi`, `HasModifiedFiles`, `HasNoProhibitedPackages`,\n"
                    "`HasLicense`, `HasRequiredLabel`) stops the release, and the others are reported (the comment\n"
                    "on that step in `images.yml` says why). Preflight runs locally with no Red Hat account; nothing\n"
                    "is submitted for certification.\n"
                    "\n"
                    "**Not republished:** the gateway profile's `atlas-gateway` runs the upstream AgentGateway image,\n"
                    "which `docker-compose.yml` already pins by tag and `@sha256` digest. Pulling it by digest gives\n"
                    "every install the same bytes, so a copy under the Atlas name would add nothing. The upstream bases\n"
                    "of the images above (Python, pgvector, Caddy, UBI) are pinned the same way in their Dockerfiles.\n"
                    "\n"
                    "The workflow then writes a **draft GitHub release** for the tag: the tag's section of this page as\n"
                    "its notes, the image references (tag and digest), and the file `release-images.env`. Its first\n"
                    "five lines are the ones a Compose install pastes into its `.env`; the three after them,\n"
                    "`ATLAS_IMAGE_API_UBI`, `ATLAS_IMAGE_MCP_UBI` and `ATLAS_IMAGE_TLS_UBI`, are where an OpenShift\n"
                    "overlay takes its digests from, and Compose reads none of them. A maintainer publishes the draft,\n"
                    "and publishing it attaches the SBOMs (`sbom-sca.yml`), the three UBI images' included.\n"
                    "\n"
                    "The images are built for `linux/amd64`.\n"
                    "\n"
                    "## Two ways to run a release\n"
                    "\n"
                    "Either way, the checkout stays at the release tag: the compose file, the Caddyfile, the scripts\n"
                    "and the retrieval check are read from it.\n"
                    "\n"
                    "1. **Build from the checkout** (the default, what INSTALL (document *Install* on node `atlas-system-install`) describes). `git checkout\n"
                    "   <tag>`, then `docker compose up -d --build`. Every base image is pinned by digest, so rebuilding\n"
                    "   the same tag gives the same bases.\n"
                    "2. **Pull the released images** with the overlay `deploy/oss/release.yml`. It replaces each\n"
                    "   service's `build:` with the published image, pinned by tag and digest, and changes nothing else:\n"
                    "   networks, secrets, volumes, healthchecks and commands are the base file's. Nothing is built on\n"
                    "   the VM. It needs Docker Compose 2.24.4 or later (`!reset`).\n"
                    "\n"
                    "## Upgrading with released images\n"
                    "\n"
                    "The steps of INSTALL section 10 (document *Install* on node `atlas-system-install`), with the images pulled instead of\n"
                    "built. From `deploy/oss/`:\n"
                    "\n"
                    "1. **Back up** (Operations section 1 (document *Operations guide* on node `atlas-system-install`)).\n"
                    "2. **Check out the release** from the repository root: `git fetch --tags && git checkout <tag>`,\n"
                    "   then return to `deploy/oss/`.\n"
                    "3. **Compare `.env` with the new `.env.example`** and read the release's notes below for new\n"
                    "   settings and one-time steps: `diff .env.example .env`.\n"
                    "4. **Replace the five `ATLAS_IMAGE_*` lines** in `.env` with those from the release's\n"
                    "   `release-images.env` (attached to the GitHub release, and printed in its notes). Replace all\n"
                    "   five together: they are one release. The `ATLAS_IMAGE_*_UBI` lines are for OpenShift; pasting\n"
                    "   them too does no harm, nothing in Compose reads them.\n"
                    "5. **Pull and restart.** Add `release.yml` once to `COMPOSE_FILE` in `.env`, before any other\n"
                    "   overlay (`COMPOSE_FILE=docker-compose.yml:release.yml:retrieval.yml`), or pass it with `-f` every\n"
                    "   time:\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose -f docker-compose.yml -f release.yml pull\n"
                    "   docker compose -f docker-compose.yml -f release.yml up -d --wait --wait-timeout 600\n"
                    "   ```\n"
                    "\n"
                    "   Do not add `--build`: with the overlay there is nothing to build. A missing `ATLAS_IMAGE_*`\n"
                    "   line stops compose with that variable's name instead of building something else.\n"
                    "6. **Run the schema step once more by hand**, check the door, and run the collation check when the\n"
                    "   notes say the Postgres image changed, as in INSTALL section 10.\n"
                    "\n"
                    "To go back to building from the checkout, drop `release.yml` from `COMPOSE_FILE` (or the `-f`) and\n"
                    "run `docker compose up -d --build`. The `ATLAS_IMAGE_*` lines are then read by nothing.\n"
                    "\n"
                    "**Rolling back** a release is restoring the backup taken in step 1 with the previous tag's checkout\n"
                    "and images: the schema step only adds, so a newer schema under older code is not a tested state.\n"
                    "\n"
                    "**Tested:** the install smoke's `upgrade` job, on every pull request that runs the smoke. It\n"
                    "installs the previous release from its published images (that release's `deploy/oss/`,\n"
                    "`release.yml` and `release-images.env`, every service checked to run a released reference), adds\n"
                    "people, an agent token and data, takes a backup with `atlas-admin backup run`, then brings up the\n"
                    "new checkout built from source on the same `.env`, `secrets/` and volumes, with `release.yml`\n"
                    "dropped from `COMPOSE_FILE`. It checks the schema step, every table's row count against the\n"
                    "count before the backup, a dry-run restore of the old backup with the new backup image, the\n"
                    "collation check, and that the old passwords, agent token and data still work. With no previous\n"
                    "release the job is skipped. *Untested:* upgrading from one released image set to the next (step 4\n"
                    "with a newer `release-images.env`) and rolling back; rehearse those on a copy first.\n"
                    "\n"
                    "## Cutting a release\n"
                    "\n"
                    "For maintainers of the repository the install is made from. Nothing creates a tag automatically.\n"
                    "\n"
                    "1. **Write the notes.** Add a `## vX.Y.Z` section at the top of [Release notes](#release-notes)\n"
                    "   below, in the same pull request as the last change or in its own: what changed for an install,\n"
                    "   new settings, one-time steps, and whether the Postgres image changed. The workflow refuses a tag\n"
                    "   with no section here.\n"
                    "2. **Merge it**, and wait for the default branch to be green.\n"
                    "3. **Tag the merged commit and push the tag:**\n"
                    "\n"
                    "   ```sh\n"
                    "   git fetch origin\n"
                    "   git tag -a vX.Y.Z -m \"Atlas vX.Y.Z\" origin/master\n"
                    "   git push origin vX.Y.Z\n"
                    "   ```\n"
                    "\n"
                    "4. **Watch the `images` run** for the tag. `build-scan` builds, scans and preflights the images;\n"
                    "   `publish` checks the tag (semver, notes present), pushes the eight images and prints their\n"
                    "   references; `release` writes the draft release. If a job fails\n"
                    "   for a reason outside the tag (a registry hiccup), run it again on the same tag:\n"
                    "   `gh run rerun <run-id> --failed`, or `gh workflow run images.yml --ref vX.Y.Z`. If the images\n"
                    "   themselves are wrong, do not move the tag: fix it and release the next PATCH.\n"
                    "5. **Publish the draft** when its notes read right: `gh release edit vX.Y.Z --draft=false`. That\n"
                    "   attaches the SBOMs.\n"
                    "6. **A release that publishes a new package** (the first release; `v0.0.2-alpha`, which adds the\n"
                    "   three `-ubi` packages): the registry creates each new package private. Make it public in the\n"
                    "   package's settings if installs should pull without signing in, and check that an anonymous\n"
                    "   `docker pull` of its reference from `release-images.env` works.\n"
                    "\n"
                    "## Release notes\n"
                    "\n"
                    "Newest first. Each section is what the GitHub release shows for that tag.\n"
                    "\n"
                    "## v0.0.2-alpha\n"
                    "\n"
                    "A pre-release, the second alpha. For a Compose install it is a patch: no new required setting\n"
                    "and no one-time step. Most of the work is the groundwork for running Atlas on OpenShift (Azure\n"
                    "Red Hat OpenShift), which is not finished yet.\n"
                    "\n"
                    "- **OpenShift images:** `gradinai-atlas-api-ubi`, `gradinai-atlas-mcp-ubi` and\n"
                    "  `gradinai-atlas-tls-ubi`, on Red Hat UBI 9, checked with Red Hat's preflight, published with\n"
                    "  this release ([What a release publishes](#what-a-release-publishes)). `release-images.env` has\n"
                    "  three more lines for them, `ATLAS_IMAGE_*_UBI`, which Compose does not read.\n"
                    "- **OpenShift manifests, in progress:** `deploy/openshift/` (Kustomize): the base (atlas-api, the\n"
                    "  migrate Job, atlas-mcp, atlas-tls with the re-encrypt Route, the audit purge CronJob), the\n"
                    "  CloudNativePG database as components, and an example overlay with a step-by-step first install\n"
                    "  (`deploy/openshift/FIRST-INSTALL.md`). Its overlay still names this release's Debian-based\n"
                    "  images; moving it to the `-ubi` ones is the next step\n"
                    "  (deploy/openshift/README.md).\n"
                    "- **Startup:** the schema step and the boot seeding hold a PostgreSQL advisory lock, so several\n"
                    "  replicas, or a replica and `atlas-migrate`, starting together no longer race. A single process\n"
                    "  never waits on it. A `postgresql://` database URL with no driver now means psycopg2.\n"
                    "- **New optional setting:** `ATLAS_MCP_STATELESS_HTTP=1` makes the MCP transport stateless, for\n"
                    "  more than one `atlas-mcp` replica behind a load balancer. Unset, nothing changes.\n"
                    "- **Security patches:** CVE-2026-109518 (apt): newer `python:3.12-slim` and `postgres:16-trixie`\n"
                    "  digests, and `apt` removed from the pgvector image.\n"
                    "- **Settings:** `ATLAS_MCP_STATELESS_HTTP` (optional, off).\n"
                    "- **One-time steps:** none.\n"
                    "- **Postgres image:** changed: the same pgvector 0.8.7 on PostgreSQL 16, Debian 13, on a newer\n"
                    "  base digest and without `apt`. Run the collation check after the upgrade, as\n"
                    "  INSTALL section 10 (document *Install* on node `atlas-system-install`) says.\n"
                    "\n"
                    "## v0.0.1-alpha\n"
                    "\n"
                    "The first public release, an alpha: the first tagged version of this repository. It is a\n"
                    "pre-release, so do not run it in production yet.\n"
                    "\n"
                    "- **What is in it:** the single-node install described in INSTALL (document *Install* on node `atlas-system-install`): Postgres with\n"
                    "  pgvector, the schema one-shot, the API and board, the MCP server, the TLS ingress with the\n"
                    "  install CA (or your PKI, or ACME), and the optional gateway, retrieval, single sign-on and backup\n"
                    "  profiles and overlays. A fresh install seeds Atlas's own system nodes and its world model\n"
                    "  (`ATLAS_SEED=none`, the default).\n"
                    "- **Released images** for every service the install builds, and the overlay `release.yml` with the\n"
                    "  `ATLAS_IMAGE_*` settings in `.env.example` to run them pinned by digest instead of building (this\n"
                    "  page).\n"
                    "- **Settings:** `ATLAS_IMAGE_API`, `ATLAS_IMAGE_MCP`, `ATLAS_IMAGE_BACKUP`,\n"
                    "  `ATLAS_IMAGE_POSTGRES`, `ATLAS_IMAGE_TLS`, read only with `release.yml`.\n"
                    "- **One-time steps:** none; this is the first release.\n"
                    "- **Postgres image:** pgvector 0.8.7 on PostgreSQL 16, Debian 13.\n"
                ),
                "from": "docs/oss/RELEASES.md",
            },
            {
                "file": "operations-guide.md",
                "title": "Operations guide",
                "kind": "runbook",
                "content": (
                    "# Operations\n"
                    "\n"
                    "This guide covers running an Atlas install day to day: backups and restores, upgrades, rotating\n"
                    "credentials, renewing certificates, the MCP gateway, logs and health, the audit export, feature\n"
                    "switches and keeping up with patches.\n"
                    "\n"
                    "It assumes the install described in INSTALL.md (document *Install* on node `atlas-system-install`): the files in `deploy/oss/`, a\n"
                    "`.env` copied from `.env.example`, and the secrets under `deploy/oss/secrets/`. Every setting named\n"
                    "here is described in CONFIG.md. The security model behind tokens, peppers and the\n"
                    "audit trail is in SECURITY.md.\n"
                    "\n"
                    "**Where commands run.** Every command below runs in `deploy/oss/` on the VM, unless it says\n"
                    "otherwise.\n"
                    "\n"
                    "**Overlays.** If you added `retrieval.yml` (INSTALL section 8 (document *Install* on node `atlas-system-install`))\n"
                    "or the OIDC overlay `oidc.yml` (INSTALL section 9 (document *Install* on node `atlas-system-install`)),\n"
                    "pass the same `-f` files to every `docker compose` command in this guide, or list them once in\n"
                    "`COMPOSE_FILE` in `.env`, for example `COMPOSE_FILE=docker-compose.yml:retrieval.yml:oidc.yml`. A\n"
                    "command run without them recreates `atlas-api` without its key, its route out or its sign-in\n"
                    "secrets.\n"
                    "\n"
                    "**What is tested.** Each section says what CI proves and what it does not. A step marked\n"
                    "*untested* has no tooling in the repository and no CI job behind it: it is a careful generic\n"
                    "procedure, and you should rehearse it on a copy before you rely on it. The\n"
                    "[summary at the end](#what-ci-tests-and-what-it-does-not) lists both.\n"
                    "\n"
                    "## 1. Backup\n"
                    "\n"
                    "### What to back up\n"
                    "\n"
                    "A backup of Atlas is two things, and they are only useful together:\n"
                    "\n"
                    "| What | Where | Why |\n"
                    "|---|---|---|\n"
                    "| The database | the `atlas-pgdata` volume, taken as a `pg_dump` | all nodes, edges, work items, documents, accounts and the audit trail |\n"
                    "| The secrets | `deploy/oss/secrets/` (all of it, including `tls/`) | the two peppers verify every password, session and token. A restored database without them cannot sign anyone in, and no token works |\n"
                    "\n"
                    "Keep these with them:\n"
                    "\n"
                    "- `deploy/oss/.env`, and any override you wrote or changed (`gateway/servers.yaml`, an edited `oidc.yml`). `.env` holds\n"
                    "  no secret, but it is your configuration.\n"
                    "- The `atlas-tls-data` volume, only if `ATLAS_TLS_MODE` is `internal` or `acme-cloudflare`: it holds\n"
                    "  Caddy's own CA or the issued certificates. In the default `files` mode the certificates are in\n"
                    "  `secrets/tls/`.\n"
                    "\n"
                    "The secrets are credentials. Store the copy encrypted, in a vault or an encrypted backup target,\n"
                    "and never in a repository. Keep the backups you rely on off this VM: a backup on the same disk does\n"
                    "not survive the disk.\n"
                    "\n"
                    "Size the backup space from Server requirements (document *Server requirements* on node `atlas-system-install`): room for at least two\n"
                    "dumps at the size of the live database.\n"
                    "\n"
                    "### A verified backup: `atlas-admin backup run`\n"
                    "\n"
                    "The repository ships a backup command that does more than dump. It runs `pg_dump --format=custom`,\n"
                    "writes a sha256 beside the file, **restores the dump into a temporary database in the same\n"
                    "Postgres and compares the row counts** of `node`, `edge` and `graph_event` with the source, then\n"
                    "drops the temporary database. A dump that does not restore is deleted and the command exits\n"
                    "non-zero. A manifest (`<file>.json`: counts, sha256, size, versions) is written last.\n"
                    "\n"
                    "It runs in the `atlas-backup` service (the `backup` stage of `backend/Dockerfile`, which adds the\n"
                    "Postgres client tools that `atlas-api` does not carry). Files land in `/var/lib/atlas/backups`: the\n"
                    "`atlas-backups` volume, or a host directory if you set `ATLAS_BACKUP_HOST_DIR`\n"
                    "([below](#backups-in-a-host-directory)):\n"
                    "\n"
                    "```sh\n"
                    "docker compose run --rm atlas-backup atlas-admin backup run\n"
                    "```\n"
                    "\n"
                    "The default reason, `manual`, always runs. To check existing backups again without taking a new\n"
                    "one:\n"
                    "\n"
                    "```sh\n"
                    "docker compose run --rm atlas-backup atlas-admin backup run --verify-only /var/lib/atlas/backups\n"
                    "```\n"
                    "\n"
                    "`--verify-only` checks each file's sha256, restores it into a temporary database and counts it. An\n"
                    "empty directory is a failure, not a pass.\n"
                    "\n"
                    "Things to know:\n"
                    "\n"
                    "- **A `manual` run is never pruned.** Every file it writes stays until you delete it. For backups\n"
                    "  that keep themselves in check, use routine backups (next).\n"
                    "- **Credentials.** The command reads the database URL from the same secret file as `atlas-api`\n"
                    "  (`/run/secrets/db_url`). The password never reaches a command line, an environment value or its\n"
                    "  output.\n"
                    "- **With single sign-on on.** `atlas-backup` reads `.env` like `atlas-api`, so it sees the\n"
                    "  `ATLAS_OIDC_*` sign-in settings, but `oidc.yml` mounts the client secret and group map into\n"
                    "  `atlas-api` only. The `backup` verbs (`run`, `restore`, `rollback`) ignore every sign-in setting\n"
                    "  (`ATLAS_AUTH_PROVIDERS`, `ATLAS_LOCAL_LOGIN`, `ATLAS_OIDC_*`, `ATLAS_OAUTH_*`), so they work\n"
                    "  there unchanged; the backup container is never given an IdP credential. Run the other\n"
                    "  `atlas-admin` verbs in `atlas-api` as before: in `atlas-backup` they stop at start, naming the\n"
                    "  file they cannot read.\n"
                    "\n"
                    "### Routine backups: `ATLAS_BACKUP_SCHEDULE` and `ATLAS_BACKUP_KEEP`\n"
                    "\n"
                    "Off by default. To take a backup on a schedule, set in `.env`:\n"
                    "\n"
                    "```sh\n"
                    "ATLAS_BACKUP_SCHEDULE=daily     # off (default), hourly, daily or weekly\n"
                    "ATLAS_BACKUP_KEEP=7             # routine backups kept (1 to 1000, default 7)\n"
                    "```\n"
                    "\n"
                    "and start the `backup` profile's loop (or put `COMPOSE_PROFILES=backup` in `.env`):\n"
                    "\n"
                    "```sh\n"
                    "docker compose --profile backup up -d atlas-backup\n"
                    "docker compose --profile backup logs atlas-backup | tail\n"
                    "```\n"
                    "\n"
                    "The loop checks once a minute. A routine backup is due when the newest routine backup in the\n"
                    "directory is older than the interval; it is the same verified backup as a manual one, named\n"
                    "`atlas-<UTC time>-routine.dump`. After each verified routine backup, routine backups beyond the\n"
                    "newest `ATLAS_BACKUP_KEEP` are deleted with their `.sha256` and `.json`. **Nothing else is\n"
                    "deleted**: manual, per-deploy and crunch backups, and any other file in the directory, stay. A\n"
                    "failed attempt is retried after 15 minutes, and nothing is pruned after a failure. A bad value\n"
                    "stops the loop at start, naming the variable.\n"
                    "\n"
                    "Prefer cron on the VM to a long-running container? The same backup and the same pruning, once:\n"
                    "\n"
                    "```sh\n"
                    "docker compose run --rm --no-deps atlas-backup atlas-admin backup run --reason routine\n"
                    "```\n"
                    "\n"
                    "**Crunch week is unchanged.** The loop asks the crunch first, every tick, exactly as before\n"
                    "(Crunch week (document *Crunch week guide* on node `atlas-system-crunch`)): while a crunch period with a backup policy (`hourly`,\n"
                    "`daily` or `once`) is active, its backups are taken and pruned by its `retention_days`. A routine\n"
                    "backup is taken only on a tick where the crunch took none, and a crunch's `retention_days` never\n"
                    "deletes routine backups. With `ATLAS_BACKUP_SCHEDULE` unset the loop does what it always did: with\n"
                    "no crunch it does nothing and says so once.\n"
                    "\n"
                    "### Backups in a host directory\n"
                    "\n"
                    "The `atlas-backups` volume is not a host directory. To have the files on the host, where your backup\n"
                    "tooling copies them off the VM, set an absolute path in `.env` and create the directory writable\n"
                    "by the container's group 0 (the backup image runs as UID 10001 in group 0):\n"
                    "\n"
                    "```sh\n"
                    "sudo install -d -m 0770 -g 0 /srv/atlas-backups\n"
                    "# .env\n"
                    "ATLAS_BACKUP_HOST_DIR=/srv/atlas-backups\n"
                    "```\n"
                    "\n"
                    "Every `atlas-backup` command then reads and writes there: the loop, `backup run`, `--verify-only`\n"
                    "and `backup restore`. Unset, the named volume is used as before. Files already in the volume are not\n"
                    "moved; copy them out first if you want them (next).\n"
                    "\n"
                    "To copy a file out of the volume instead:\n"
                    "\n"
                    "```sh\n"
                    "docker compose run --rm -T --no-deps atlas-backup ls -l /var/lib/atlas/backups\n"
                    "docker compose run --rm -T --no-deps atlas-backup cat /var/lib/atlas/backups/<file>.dump > <file>.dump\n"
                    "```\n"
                    "\n"
                    "Copy the matching `.sha256` and `.json` the same way; a restore needs all three.\n"
                    "\n"
                    "**Tested:** `backend/verify_crunch_backup.py`, in the backend suite that `verify.yml` runs on every\n"
                    "change: a backup is taken and verified against a test database, a corrupted or truncated dump is\n"
                    "detected, retention deletes only its own older files, a crunch that is off takes no backup, and no\n"
                    "credential appears in any output. Its section H covers routine backups: with no schedule the loop\n"
                    "answers exactly as before; with one, a routine backup when due and not before, `ATLAS_BACKUP_KEEP`\n"
                    "deleting only older routine backups, a due crunch backup winning the tick, the back-off after a\n"
                    "failure, `backup run --reason routine`, and a bad value stopping the loop. It checks the compose\n"
                    "service statically (profile, stage, network, no published port, the volume or host directory).\n"
                    "Install smoke leg 8d runs `atlas-admin backup run` in the `atlas-backup` image on the OSS stack,\n"
                    "and leg 9f runs it again, with `restore`, after single sign-on is turned on. Section I covers the\n"
                    "backup verbs with the OIDC settings set and their files absent.\n"
                    "**Not tested:** the loop as a long-running compose service on the OSS stack, the\n"
                    "`ATLAS_BACKUP_HOST_DIR` bind mount, and copying files out of the volume.\n"
                    "\n"
                    "### A plain dump\n"
                    "\n"
                    "INSTALL.md (document *Install* on node `atlas-system-install`) uses the simplest form before an upgrade, a plain SQL dump\n"
                    "through the Postgres container:\n"
                    "\n"
                    "```sh\n"
                    "( umask 077; mkdir -p ~/atlas-backups &&\n"
                    "  docker compose exec -T atlas-postgres pg_dump -U atlas -d atlas > ~/atlas-backups/atlas-$(date +%F).sql )\n"
                    "```\n"
                    "\n"
                    "The dump holds every password verifier and token digest, so it is written readable by you alone\n"
                    "(`umask 077`) and outside the checkout. This needs nothing beyond the running stack, but nothing checks that the file restores. Prefer\n"
                    "`atlas-admin backup run` when you can. **Not tested** by CI.\n"
                    "\n"
                    "### Back up the secrets\n"
                    "\n"
                    "*Untested, generic.* Copy the whole `secrets/` directory with the tool your backup system uses,\n"
                    "preserving the files as they are, at the same time as the database. For example, an archive that\n"
                    "you then encrypt and move off the VM, made from `deploy/oss/`:\n"
                    "\n"
                    "```sh\n"
                    "( umask 077; mkdir -p ~/atlas-backups &&\n"
                    "  tar -czf ~/atlas-backups/atlas-secrets-$(date +%F).tgz secrets .env )\n"
                    "```\n"
                    "\n"
                    "The archive holds the peppers and the database password: treat it as a credential. `umask 077`\n"
                    "makes it readable by you alone, and it goes outside the checkout, where a `git add` cannot pick it\n"
                    "up. It is still an unencrypted copy of the peppers: do not leave it on the VM.\n"
                    "\n"
                    "## 2. Restore\n"
                    "\n"
                    "`atlas-admin backup restore` restores a backup from `atlas-admin backup run` over the live\n"
                    "database. It runs in the `atlas-backup` image, like the backup:\n"
                    "\n"
                    "1. **Put the secrets back** if this is a new VM or they changed. Restore `secrets/` (and `.env`)\n"
                    "   **from the same backup** as the database, into `deploy/oss/`. The peppers must be the ones the\n"
                    "   database was made with. Keep the modes `init-secrets.sh` uses: the directory `0700`, the files in\n"
                    "   it `0644` except `secrets/tls/server.key`, which is `0600`. Put the backup files (the `.dump`,\n"
                    "   its `.sha256` and its `.json`) back in the backup directory.\n"
                    "\n"
                    "2. **Stop what is connected to the database**, leaving Postgres running. The restore refuses while\n"
                    "   any other session is connected, and says which:\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose stop atlas-api\n"
                    "   ```\n"
                    "\n"
                    "   Add `atlas-backup` if the `backup` profile's loop runs. `atlas-mcp`, `atlas-tls` and the gateway\n"
                    "   do not connect to the database and can stay up; they answer errors until `atlas-api` is back.\n"
                    "\n"
                    "3. **Rehearse it.** A dry run does everything except replace the database:\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose run --rm --no-deps atlas-backup atlas-admin backup restore latest --dry-run\n"
                    "   ```\n"
                    "\n"
                    "   `latest` is the newest backup in the directory; give a file name instead\n"
                    "   (`atlas-<UTC time>-<reason>.dump`) for another. The dry run checks the file's sha256 against its\n"
                    "   `.sha256` and its manifest, restores it into a staging database, counts `node`, `edge` and\n"
                    "   `graph_event` there against the manifest, drops the staging database, and says whether a real\n"
                    "   run would refuse because something is connected. It changes nothing.\n"
                    "\n"
                    "4. **Restore:**\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose run --rm --no-deps atlas-backup atlas-admin backup restore latest\n"
                    "   ```\n"
                    "\n"
                    "   In order: the checksum is checked before any database is opened; anything connected is a\n"
                    "   refusal; the dump is restored into a staging database with the live database's encoding,\n"
                    "   collation and owner, and counted against the manifest. Only then is the live database\n"
                    "   **renamed** to `atlas_prerestore_<UTC time>` (the safety copy) and the staging database renamed\n"
                    "   to `atlas`, and the counts are checked once more. A failure before the swap leaves the live\n"
                    "   database untouched and drops the staging one. The output names the safety copy and the rollback\n"
                    "   command.\n"
                    "\n"
                    "5. **Start `atlas-api`:**\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose start atlas-api\n"
                    "   ```\n"
                    "\n"
                    "   If the backup is from an older release, run the schema step too:\n"
                    "   `docker compose exec -T atlas-api atlas-migrate upgrade`.\n"
                    "\n"
                    "6. **Check it.** `curl --cacert secrets/tls/ca.crt https://atlas.example.com/readyz`, sign in, and\n"
                    "   compare `node_count`, `edge_count` and `event_count` on `/api/health` with the manifest. A\n"
                    "   sign-in that fails for everyone means the peppers do not match the database.\n"
                    "\n"
                    "7. **Roll back, or clean up.** To go back to the database as it was before the restore, stop\n"
                    "   `atlas-api` again and:\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose run --rm --no-deps atlas-backup atlas-admin backup rollback          # lists the copies\n"
                    "   docker compose run --rm --no-deps atlas-backup atlas-admin backup rollback --to atlas_prerestore_<UTC time>\n"
                    "   docker compose start atlas-api\n"
                    "   ```\n"
                    "\n"
                    "   The rollback renames the current database aside to `atlas_rolledback_<UTC time>` and the safety\n"
                    "   copy back to `atlas`; it drops nothing. Once you are satisfied, drop the copies to free their\n"
                    "   space (each holds the full size of the database):\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose exec -T atlas-postgres psql -U atlas -d postgres -c 'DROP DATABASE \"atlas_prerestore_<UTC time>\"'\n"
                    "   ```\n"
                    "\n"
                    "Things to know:\n"
                    "\n"
                    "- **Why a rename, not a dump, for the safety copy.** A rename is instant and needs no space at\n"
                    "  restore time whatever the size; the copy is the original database itself, not a logical copy\n"
                    "  that could itself fail; and rolling back is another rename. The cost is that the copy occupies its\n"
                    "  full size until you drop it. Plan the volume for two databases during a restore.\n"
                    "- **Nothing is written into the restored database**, not even a record of the restore: it would\n"
                    "  then differ from the backup it was counted against. The command's output is the record.\n"
                    "- **Not carried over:** database-level settings (`ALTER DATABASE ... SET`) and grants on the\n"
                    "  database itself; a dump does not hold them. Atlas sets neither; the command says so when the old\n"
                    "  database had any, and the safety copy keeps them.\n"
                    "- **A plain SQL dump** (the `pg_dump` form in [section 1](#a-plain-dump)) is not something this\n"
                    "  command restores. *Untested, generic:* with `atlas-api` stopped, rename the database aside\n"
                    "  (`ALTER DATABASE atlas RENAME TO atlas_before_restore` in `psql -d postgres`), `createdb -U atlas\n"
                    "  -T template0 atlas`, load it with\n"
                    "  `docker compose exec -T atlas-postgres psql -U atlas -d atlas -v ON_ERROR_STOP=1 < atlas-backup.sql`,\n"
                    "  and start `atlas-api`.\n"
                    "\n"
                    "**Tested:** `backend/verify_crunch_backup.py` section G, against a test database: a flipped byte\n"
                    "and a missing manifest are refused before any database is touched; `--dry-run` changes nothing;\n"
                    "with another session connected a dry run says a real run would refuse, and a real run refuses; a\n"
                    "failing `pg_restore` leaves the live database untouched and no staging database behind; the restore\n"
                    "makes the database byte-identical to the moment of the backup, keeps the safety copy, and\n"
                    "`rollback` puts the safety copy back. Install smoke leg 8d, on the OSS stack through\n"
                    "`docker compose run`: `atlas-api` stopped, `backup run`, data changed and deleted, `restore\n"
                    "--dry-run` changes nothing, a session held open is refused, `restore`, then **every table's row\n"
                    "count identical** to before the backup, `atlas-api` healthy again on the restored database, and\n"
                    "the safety copy listed and dropped. **Not tested:** restoring onto a new VM (putting the secrets\n"
                    "back, step 1), a full restore of a backup from an older release (the install smoke's `upgrade`\n"
                    "job dry-runs one with the new backup image), and the plain SQL dump.\n"
                    "\n"
                    "Restoring onto a different Postgres base image than the one the dump was taken on is fine for a\n"
                    "logical dump: the indexes are rebuilt by the load. Restoring the **volume** itself (a file-level\n"
                    "copy of `atlas-pgdata`) onto an image with a different C library is not; see the collation check in\n"
                    "[section 3](#the-collation-check).\n"
                    "\n"
                    "## 3. Upgrade\n"
                    "\n"
                    "INSTALL section 10 (document *Install* on node `atlas-system-install`) is the upgrade procedure: back up, check out the\n"
                    "new release tag, compare `.env` with the new `.env.example` and read the release notes, rebuild (or\n"
                    "pull the released images) and restart, and run the schema step once more by hand. This section\n"
                    "explains the parts of it. Releases, their notes and their images are described in\n"
                    "RELEASES.md (document *Releases* on node `atlas-system-install`).\n"
                    "\n"
                    "### The schema step: `atlas-migrate upgrade`\n"
                    "\n"
                    "The `atlas-migrate` one-shot runs `atlas-migrate upgrade` on every `docker compose up`, and\n"
                    "`atlas-api` does not start until it has exited 0. It runs the schema steps that application startup\n"
                    "runs: create every missing table, add missing columns and indexes, create the full-text indexes. It\n"
                    "only creates what is missing; it never changes or deletes data, and a second run changes nothing.\n"
                    "It reads the same `.env` as `atlas-api` but ignores the sign-in settings (`ATLAS_AUTH_PROVIDERS`,\n"
                    "`ATLAS_LOCAL_LOGIN`, `ATLAS_OIDC_*`), so on an install with SSO on it needs none of the OIDC files\n"
                    "that `oidc.yml` mounts into `atlas-api`. To run it by hand:\n"
                    "\n"
                    "```sh\n"
                    "docker compose exec -T atlas-api atlas-migrate upgrade\n"
                    "docker compose logs atlas-migrate | tail -20\n"
                    "```\n"
                    "\n"
                    "It is not a general migration framework. A change it cannot make (a column type, a rename) ships\n"
                    "as a separate one-time script, named in the release notes.\n"
                    "\n"
                    "**Tested:** `backend/verify_upgrade_command.py` (the command is idempotent and builds the same\n"
                    "schema as startup), and install smoke leg 2 (`atlas-migrate` exits 0, and `atlas-migrate upgrade`\n"
                    "runs twice more on the live stack), install smoke leg 9h (on the stack with SSO on, `atlas-migrate`\n"
                    "and `atlas-api` recreated: the one-shot exits 0 without the OIDC files and `atlas-api` comes back\n"
                    "healthy), and the install smoke's `upgrade` job: the previous release,\n"
                    "installed from its published images and seeded, is backed up with `atlas-admin backup run`, then\n"
                    "this checkout is built over the same `.env`, `secrets/` and volumes; `atlas-migrate` exits 0 on the\n"
                    "old database and once more by hand, every table keeps its row count (the `node_type` and\n"
                    "`edge_type` registries may only grow), the old backup passes a dry-run restore with the new backup\n"
                    "image, and the old passwords, agent token and data still work. **Not tested:** an upgrade that\n"
                    "skips releases, and rolling back. Take the backup in [section 1](#1-backup) first, every time.\n"
                    "\n"
                    "### The origin certificates: one step for an install made before them\n"
                    "\n"
                    "Since OSS-26 D, `atlas-api` and `atlas-mcp` serve TLS themselves on the ingress's legs to them\n"
                    "(`https://atlas-api-origin:8443`, `https://atlas-mcp-origin:8493`), and the compose file reads five\n"
                    "files that an older install does not have: `secrets/tls/api-origin.crt`, `api-origin.key`,\n"
                    "`mcp-origin.crt`, `mcp-origin.key` and `origin-ca.crt`. Without them `docker compose up` stops on\n"
                    "the missing secret file. **Once, before the first `up` of the new version** (INSTALL section 10,\n"
                    "between steps 3 and 4), from `deploy/oss/`:\n"
                    "\n"
                    "```sh\n"
                    "ls secrets/tls/origin-ca.crt 2>/dev/null || bash ../../scripts/init-certs.sh --origin-only\n"
                    "# init-certs: wrote origin-ca.crt api-origin.crt api-origin.key mcp-origin.crt mcp-origin.key under ./secrets/tls (the CA key was discarded)\n"
                    "```\n"
                    "\n"
                    "`--origin-only` writes exactly those five files and nothing else. They are signed by a new,\n"
                    "separate CA (`origin-ca.crt`) whose key is discarded at once, and only the ingress trusts it, so\n"
                    "`ca.crt`, `server.crt` and every client keep working unchanged: nobody re-imports anything. It\n"
                    "refuses to overwrite the files once they exist (`--force` replaces them). An install made with your\n"
                    "own PKI does the same. There is no new setting in `.env`. A new install gets the files from\n"
                    "`init-secrets.sh`, signed by the install CA itself.\n"
                    "\n"
                    "**Tested:** the install smoke's `upgrade` job runs this step on the previous release's `secrets/`\n"
                    "before bringing up this checkout; `tools/ci/test_init_certs.sh` proves that `--origin-only` leaves\n"
                    "`ca.crt` and `server.crt` untouched and that the new certificates verify against `origin-ca.crt`.\n"
                    "\n"
                    "### Images: build from the checkout, or pull the released images\n"
                    "\n"
                    "Every release is a `vX.Y.Z` tag, and every tag publishes the images the install builds.\n"
                    "RELEASES.md (document *Releases* on node `atlas-system-install`) has the versioning scheme, what each release publishes, the release\n"
                    "notes and how a release is cut. An install runs a release one of two ways; either way the checkout\n"
                    "stays at the release tag, because the compose file, the Caddyfile and the scripts come from it.\n"
                    "\n"
                    "**Build from the checkout** (the default). Every base image in the Dockerfiles is pinned by tag and\n"
                    "`@sha256` digest (`backend/Dockerfile`, `mcp/Dockerfile`, `deploy/caddy/Dockerfile`,\n"
                    "`deploy/postgres/pgvector.Dockerfile`), and so is the gateway image in `docker-compose.yml`.\n"
                    "Rebuilding the same checkout therefore gives the same bases: patches arrive as new commits, not as\n"
                    "a newer `latest` pulled on rebuild. Upgrading is checking out the new tag and\n"
                    "`docker compose up -d --build` (INSTALL section 10 (document *Install* on node `atlas-system-install`)).\n"
                    "\n"
                    "**Pull the released images.** The `images` workflow (`.github/workflows/images.yml`) runs on every\n"
                    "`v*` tag: it builds all five images, checks them, runs the same vulnerability gate as every pull\n"
                    "request, and only then publishes them, tagged with the release:\n"
                    "\n"
                    "| Service | Image |\n"
                    "|---|---|\n"
                    "| `atlas-api`, `atlas-migrate`, `atlas-retrieval` | `ghcr.io/<owner>/gradinai-atlas-api` |\n"
                    "| `atlas-mcp` | `ghcr.io/<owner>/gradinai-atlas-mcp` |\n"
                    "| `atlas-backup` | `ghcr.io/<owner>/gradinai-atlas-backup` |\n"
                    "| `atlas-postgres` | `ghcr.io/<owner>/gradinai-atlas-postgres` |\n"
                    "| `atlas-tls` | `ghcr.io/<owner>/gradinai-atlas-tls` |\n"
                    "| `atlas-gateway` | not republished: the upstream AgentGateway image, pinned by digest in `docker-compose.yml` |\n"
                    "\n"
                    "Each release carries `release-images.env`: the five references, tag and digest, as the\n"
                    "`ATLAS_IMAGE_*` lines of `.env.example`. With those lines in `.env`, the overlay `release.yml`\n"
                    "replaces every `build:` with its image and changes nothing else:\n"
                    "\n"
                    "```sh\n"
                    "docker compose -f docker-compose.yml -f release.yml pull\n"
                    "docker compose -f docker-compose.yml -f release.yml up -d --wait --wait-timeout 600\n"
                    "```\n"
                    "\n"
                    "or `COMPOSE_FILE=docker-compose.yml:release.yml` in `.env`, before any other overlay. The upgrade\n"
                    "is then INSTALL section 10 with two differences: step 4 replaces the five `ATLAS_IMAGE_*` lines with\n"
                    "the new release's and runs `pull` and `up` without `--build`\n"
                    "(RELEASES.md, \"Upgrading with released images\" (document *Releases* on node `atlas-system-install`)).\n"
                    "The references are pinned by digest, not by tag alone: a tag can be moved, a digest cannot. A\n"
                    "missing `ATLAS_IMAGE_*` line stops compose with its name rather than building something else.\n"
                    "`release.yml` needs Docker Compose 2.24.4 or later.\n"
                    "\n"
                    "**Tested:** the `images` workflow's image checks and gate on every pull request that touches an\n"
                    "image's inputs; `release.yml` renders, refuses a missing image, and every service it touches is a\n"
                    "pinned pull (`oss.yml`, `check_oss_compose.py` S3 and S7); the install smoke's `upgrade` job runs\n"
                    "the previous release from its published images (pulled by digest with `release.yml`, every service\n"
                    "checked to run a reference from `release-images.env`) and then upgrades it to the checkout built\n"
                    "from source. *Untested:* moving from one released image set to the next (a newer\n"
                    "`release-images.env` with `release.yml` kept).\n"
                    "\n"
                    "### The collation check\n"
                    "\n"
                    "`atlas-postgres` sorts text indexes with the C library of its base image. When an upgrade moves the\n"
                    "Postgres image to a new Debian release, the database still opens, but a text index made under the\n"
                    "old library can be out of order: lookups miss rows, a unique index can admit a duplicate. Postgres\n"
                    "only logs a warning.\n"
                    "\n"
                    "After any upgrade that changed `deploy/postgres/pgvector.Dockerfile`, and always for an install\n"
                    "whose database was first created before 2026-10-26 (it ran the Debian 12 image), run the check:\n"
                    "\n"
                    "```sh\n"
                    "bash ../../tools/db_collation_check.sh \"$(docker compose ps -q atlas-postgres)\"\n"
                    "```\n"
                    "\n"
                    "It lists every database, its recorded and actual collation version, and `OK` or `MISMATCH`. It\n"
                    "reads no credential: it runs `psql` inside the container over the local socket. Exit 0 means every\n"
                    "database is OK; exit 1 means a mismatch is left.\n"
                    "\n"
                    "On a mismatch, take a backup ([section 1](#1-backup)) and then repair:\n"
                    "\n"
                    "```sh\n"
                    "bash ../../tools/db_collation_check.sh --fix \"$(docker compose ps -q atlas-postgres)\"\n"
                    "```\n"
                    "\n"
                    "`--fix` rebuilds every index (`REINDEX DATABASE CONCURRENTLY`), then records the new version, then\n"
                    "checks again. `--blocking` uses a plain `REINDEX`, which takes locks but is faster on a quiet\n"
                    "install. If a rebuild fails, for example a unique index that now finds a duplicate, the script\n"
                    "stops, lists the invalid indexes and leaves the version unrecorded, so the warning stays until the\n"
                    "data is repaired by hand. Read the script's header for the details.\n"
                    "\n"
                    "**Tested:** the install smoke's `upgrade` job runs the check in report mode on the upgraded OSS\n"
                    "install, whose database was created by the previous release's `atlas-postgres` image, and requires\n"
                    "exit 0. When `deploy/postgres/` changed since that release, a mismatch is repaired with `--fix` and\n"
                    "checked again. **Not tested:** a real move to a new Debian release (no release has made one yet),\n"
                    "so the `--fix` path has not run against an OSS install.\n"
                    "\n"
                    "## 4. Rotating credentials\n"
                    "\n"
                    "### Per-user agent tokens\n"
                    "\n"
                    "Every agent has its own `atlas_svc_` token, tied to a person. Tokens always expire (at most 365\n"
                    "days). `/api/health` lists tokens that are close to expiry or long unused under `token_warnings`.\n"
                    "\n"
                    "**Rotate without an outage.** Issuing a new token does not revoke the old one:\n"
                    "\n"
                    "1. Mint a new token for the same person and purpose. It is added to the same agent and written\n"
                    "   only to the `--out` file in the container:\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose exec -T atlas-api atlas-admin \\\n"
                    "     token mint --user <name> --purpose claude-code --scopes read,write --out /tmp/claude-code.token\n"
                    "   ```\n"
                    "\n"
                    "2. Move it to the client and delete the copy in the container, as in\n"
                    "   INSTALL section 7 (document *Install* on node `atlas-system-install`).\n"
                    "3. Confirm the client works with the new token.\n"
                    "4. Revoke the old one, by its id or prefix (never the value):\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose exec -T atlas-api atlas-admin token list --user <name>\n"
                    "   docker compose exec -T atlas-api atlas-admin token revoke <id or prefix>\n"
                    "   ```\n"
                    "\n"
                    "Revocation takes effect on the next request. People can also rotate their own agent credentials\n"
                    "from the board (Settings, Agent credentials). The client side is in\n"
                    "the Claude Code client pack (document *Claude Code client pack* on node `atlas-system-clients-claude-code`).\n"
                    "\n"
                    "**Revoke everything of one person:** `atlas-admin token revoke --user <name>` revokes every live\n"
                    "token of that person's agents. `atlas-admin user disable <name>` ends their sessions and stops their\n"
                    "agents' tokens at once, and `user enable` restores them. `user reset-password` ends sessions but\n"
                    "leaves tokens working, on purpose.\n"
                    "\n"
                    "`atlas-admin` records the shell user as the operator, or `--actor <name>` placed before the\n"
                    "subcommand.\n"
                    "\n"
                    "**Tested:** the backend suite (`verify_identity_seam.py`, `verify_agent_identity.py` and others)\n"
                    "covers `token mint`, `token list`, `token revoke` and the user verbs; install smoke leg 8 mints a\n"
                    "token with `token mint --out` and connects Claude Code with it.\n"
                    "\n"
                    "### Secrets files\n"
                    "\n"
                    "The files under `secrets/` are compose secrets: each is mounted into the container when the\n"
                    "container is created. After replacing a file, **recreate** the containers that read it; a restart\n"
                    "alone may keep the old file.\n"
                    "\n"
                    "| File | To rotate | Recreate |\n"
                    "|---|---|---|\n"
                    "| `password_pepper`, `token_pepper` | **Do not.** A new pepper invalidates every password, session and token at once, permanently. There is no rotation path for them. | - |\n"
                    "| `db_password`, `db_url` | **Never by hand.** The database keeps the password it was created with: changing the files alone breaks the install. Run `scripts/rotate-db-password.sh`, below. | the script does it |\n"
                    "| `embed_key` (retrieval) | Write the new key into the file (one line, `0644`). | `atlas-api`, with `retrieval.yml` |\n"
                    "| `oidc_client_secret` (OIDC) | Create the new secret in the IdP, write it to the file, recreate, then remove the old secret in the IdP. | `atlas-api`, with `oidc.yml` |\n"
                    "| `tls/*` | See [section 5](#5-certificates). | |\n"
                    "\n"
                    "To recreate `atlas-api` after replacing a file (with your overlays, as everywhere):\n"
                    "\n"
                    "```sh\n"
                    "docker compose up -d --force-recreate --no-deps --wait atlas-api\n"
                    "```\n"
                    "\n"
                    "Never run `init-secrets.sh --force` on an install with data. It replaces the peppers and the\n"
                    "database password files, which breaks sign-in for everyone and the database connection.\n"
                    "\n"
                    "### The database password: `rotate-db-password.sh`\n"
                    "\n"
                    "The database is reachable only on the internal `data` network, by `atlas-migrate`, `atlas-api`\n"
                    "and `atlas-backup`, so rotating its password is a policy matter rather than a fix. When your\n"
                    "policy requires it, run, with the stack up and your overlays passed as `-f` (or in `COMPOSE_FILE`):\n"
                    "\n"
                    "```sh\n"
                    "bash ../../scripts/rotate-db-password.sh -f docker-compose.yml -f oidc.yml   # your -f files\n"
                    "```\n"
                    "\n"
                    "```text\n"
                    "rotate-db-password: the current password signs in\n"
                    "rotate-db-password: services to recreate: atlas-api\n"
                    "rotate-db-password: the role has the new password, and it signs in\n"
                    "rotate-db-password: wrote ./secrets/db_password and ./secrets/db_url\n"
                    "rotate-db-password: recreated and healthy: atlas-api\n"
                    "rotate-db-password: atlas-api reads the database\n"
                    "rotate-db-password: the old password is refused\n"
                    "rotate-db-password: done. Back up ./secrets/ again (...)\n"
                    "```\n"
                    "\n"
                    "What it does, in order:\n"
                    "\n"
                    "1. Checks that `secrets/db_password` signs in to the database as it is, and that `secrets/db_url`\n"
                    "   carries the same password and points at this install's `atlas-postgres`. It refuses, changing\n"
                    "   nothing, if either is not so. It also finds every container that mounts `secrets/db_url`.\n"
                    "2. Generates a new password with `openssl` and writes the new `db_password` and `db_url` (the old\n"
                    "   URL with only the password replaced) to a `0700` work directory under `secrets/`, beside a copy\n"
                    "   of the old two files.\n"
                    "3. Sets the new password on the role with `ALTER ROLE` inside `atlas-postgres`, over its local\n"
                    "   socket, and checks that the new password signs in over TCP to `atlas-postgres`, as `atlas-api`\n"
                    "   connects (not over loopback, which the postgres image trusts without a password).\n"
                    "4. Moves both new files into `secrets/` (`0644`, as `init-secrets.sh` writes them).\n"
                    "5. Recreates every **running** container that mounts `db_url` (`atlas-api`, and `atlas-backup`\n"
                    "   when its profile runs), waits until they are healthy, then reads the database from inside\n"
                    "   `atlas-api`. A consumer that is not running reads the new file when it next starts; that\n"
                    "   includes the `atlas-migrate` one-shot, which a password change does not need to run again.\n"
                    "6. Checks that the old password is now refused, and deletes the old copies.\n"
                    "\n"
                    "**A failure rolls back.** If any step after the `ALTER ROLE` fails, the script puts the old files\n"
                    "back, sets the old password on the role again, recreates the same services on it and exits 1 with\n"
                    "`rolled back; the install runs on the old password, as before`. Only if that rollback itself fails\n"
                    "does it keep the old files in `secrets/.rotate-db-password.*` and print the steps to finish by hand.\n"
                    "A second run while one is in progress is refused (`secrets/.rotate-db-password.lock`).\n"
                    "\n"
                    "**The password is never printed** and never on a command line or in an environment variable: the\n"
                    "shell reads it from the file, `psql` gets the `ALTER ROLE` statement on stdin (with\n"
                    "`log_statement` off for that session, so the server log does not keep it), and the sign-in checks\n"
                    "use a passfile inside the container that is deleted after each check.\n"
                    "\n"
                    "**What to expect.** `atlas-postgres` keeps running and is not recreated: it never reads\n"
                    "`db_password` again once the volume is initialised. Requests `atlas-api` serves on connections it\n"
                    "already holds keep working; a new connection fails for the few seconds between the `ALTER ROLE`\n"
                    "and the recreated `atlas-api`, so run it at a quiet time. No data is touched.\n"
                    "\n"
                    "**Afterwards, back up `secrets/` again** ([Back up the secrets](#back-up-the-secrets)). An older\n"
                    "archive holds the old password, and the role's password lives in the Postgres volume, not in a\n"
                    "database dump: putting the old `db_password` and `db_url` back onto this volume breaks the\n"
                    "connection.\n"
                    "\n"
                    "The script only rotates the password of this install's own `atlas-postgres`. With an external\n"
                    "database (`db_url` pointing elsewhere) it refuses: change the password there, then write both files\n"
                    "and recreate `atlas-api` as above.\n"
                    "\n"
                    "**Tested:** install smoke leg 10, on the stack with the SSO overlay: a marker row written first; a\n"
                    "rotation made to fail after the services were recreated rolls back to the old files and password\n"
                    "with every service healthy; then the rotation itself: both files changed, `atlas-api`\n"
                    "recreated and healthy, an agent token served, the marker row and every table's row count\n"
                    "identical, the new password accepted and the old one refused (`password authentication failed`),\n"
                    "and neither password in the script's output or the `atlas-postgres` log. **Not tested:** a\n"
                    "rotation with `atlas-backup` running as a service, and with `retrieval.yml`.\n"
                    "\n"
                    "## 5. Certificates\n"
                    "\n"
                    "The default install uses a throwaway **install CA** made by `scripts/init-certs.sh` (run for you by\n"
                    "`init-secrets.sh`). The server certificate lasts 397 days by default (`--days`) and the CA ten\n"
                    "years, but **the CA key is deleted as soon as it has signed**. Nothing can be signed with that CA\n"
                    "again, so renewing means a new CA and new certificates, and every client trusts the new `ca.crt`.\n"
                    "\n"
                    "### Renewing the install CA's certificates\n"
                    "\n"
                    "*Untested as an end-to-end procedure*; the script itself is tested (below).\n"
                    "\n"
                    "1. Check when the current certificate expires. `/api/health` warns on its own (see\n"
                    "   [The expiry warning](#the-expiry-warning) below); by hand, with a generic `openssl` command:\n"
                    "\n"
                    "   ```sh\n"
                    "   openssl x509 -in secrets/tls/server.crt -noout -enddate\n"
                    "   ```\n"
                    "\n"
                    "2. Back up `secrets/tls/`, then make a new CA and new certificates. `--force` is required to\n"
                    "   replace the existing files. Without `--host`, `init-certs.sh` takes the public name from\n"
                    "   `ATLAS_PUBLIC_HOST` in `.env`, as `init-secrets.sh` does, so a renewal keeps it:\n"
                    "\n"
                    "   ```sh\n"
                    "   bash ../../scripts/init-certs.sh --force\n"
                    "   # init-certs: public name: atlas.example.com (from ATLAS_PUBLIC_HOST in ./.env)\n"
                    "   ```\n"
                    "\n"
                    "   It prints which public name it used and where it came from; check that line before going on.\n"
                    "   To give other or more names, pass each with `--host` (then `.env` is not read):\n"
                    "   `init-certs.sh --force --host atlas.example.com --host 192.0.2.10`. It always adds\n"
                    "   `atlas-api`, `atlas-mcp`, `gateway`, `localhost`, `127.0.0.1` and `::1` to the server\n"
                    "   certificate, and prints every name it used. It also renews the two origin listeners'\n"
                    "   certificates (`api-origin.crt`, SAN `atlas-api-origin`; `mcp-origin.crt`, SAN\n"
                    "   `atlas-mcp-origin`; both also `localhost` and `127.0.0.1`) and `origin-ca.crt`, under the new CA.\n"
                    "\n"
                    "3. Recreate everything that reads the files: `atlas-tls` (the server certificate, and\n"
                    "   `origin-ca.crt` for its legs to the two services), `atlas-mcp` (it trusts exactly `ca.crt`,\n"
                    "   holds the client certificate for the optional mTLS leg, and serves `mcp-origin.crt`),\n"
                    "   `atlas-api` (it serves `api-origin.crt` and reads `server.crt` for the expiry warning), and the\n"
                    "   gateway if you run it:\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose up -d --force-recreate --wait atlas-tls atlas-mcp atlas-api\n"
                    "   docker compose --profile gateway up -d --force-recreate atlas-gateway\n"
                    "   ```\n"
                    "\n"
                    "4. Give clients the new `secrets/tls/ca.crt` (public; safe to copy): every browser or trust store\n"
                    "   that imported the old one, every Claude Code machine (`NODE_EXTRA_CA_CERTS`), and anything that\n"
                    "   uses `curl --cacert`. If your retrieval endpoint's certificate was issued by this install's CA,\n"
                    "   it needs a new one as well.\n"
                    "5. Check the door: `curl --cacert secrets/tls/ca.crt https://atlas.example.com/healthz`.\n"
                    "\n"
                    "Plan the renewal before the expiry date: after it, browsers and agents refuse the connection, and\n"
                    "`atlas-mcp` cannot reach the API.\n"
                    "\n"
                    "### The expiry warning\n"
                    "\n"
                    "`/api/health` ([section 7](#health)) carries `cert_warnings`, shaped like `token_warnings`: a list\n"
                    "that is empty while the server certificate has more than `ATLAS_CERT_EXPIRY_WARN_DAYS` left\n"
                    "(default 30, set it in `.env`), and otherwise holds one entry:\n"
                    "\n"
                    "```json\n"
                    "\"cert_warnings\": [\n"
                    "  {\"file\": \"/run/secrets/tls_cert\", \"subject\": \"CN=atlas-api\",\n"
                    "   \"not_after\": \"2027-11-30T09:12:44Z\", \"reasons\": [\"expires_in_days=12\"]}\n"
                    "]\n"
                    "```\n"
                    "\n"
                    "`reasons` is `expires_in_days=N` inside the window, `expired` after the date, `unreadable` when\n"
                    "atlas-api cannot open the file, and `not_a_certificate` when it holds no PEM certificate. The\n"
                    "compose file mounts `secrets/tls/server.crt` (the public certificate, never the key) into\n"
                    "atlas-api as `/run/secrets/tls_cert` and sets `ATLAS_CERT_EXPIRY_FILE` to it. atlas-api reads the\n"
                    "file on every call, but a recreated file is only seen after the container is recreated (step 3\n"
                    "above), which is also how the warning clears after a renewal.\n"
                    "\n"
                    "The warning is about `server.crt`, which every mode uses for atlas-mcp's hop to the API. In\n"
                    "`internal` and `acme-cloudflare` mode Caddy renews the public door's certificate itself and that\n"
                    "one is not watched. Without `ATLAS_CERT_EXPIRY_FILE` (an install that terminates TLS elsewhere)\n"
                    "`/api/health` has no `cert_warnings` field at all.\n"
                    "\n"
                    "### Your own PKI, Caddy's CA, or ACME\n"
                    "\n"
                    "- **Your own PKI** (`ATLAS_TLS_MODE=files` with your files): replace `secrets/tls/server.crt`,\n"
                    "  `server.key` and, if the root changed, `ca.crt`, keeping the SANs listed in\n"
                    "  INSTALL section 3 (document *Install* on node `atlas-system-install`), then recreate as in\n"
                    "  step 3. For the mTLS leg alone, `init-certs.sh --client-only --force` makes a new client CA and\n"
                    "  `atlas-mcp`'s client certificate without touching the server certificate. The origin listeners'\n"
                    "  certificates are the install's own in every mode: `init-certs.sh --origin-only --force` renews\n"
                    "  them under a new origin CA without touching anything else (recreate `atlas-tls`, `atlas-api`\n"
                    "  and `atlas-mcp`). To issue them from your PKI instead, put `api-origin.crt`/`.key` (SAN\n"
                    "  `atlas-api-origin`, plus `127.0.0.1` for the healthcheck), `mcp-origin.crt`/`.key` (SAN\n"
                    "  `atlas-mcp-origin`, plus `127.0.0.1`) and their root as `origin-ca.crt` at the same paths.\n"
                    "- **`internal` or `acme-cloudflare`**: Caddy renews its own certificates and keeps them in the\n"
                    "  `atlas-tls-data` volume. There is nothing to run; back up the volume ([section 1](#what-to-back-up)).\n"
                    "\n"
                    "**Tested:** `tools/ci/oss_tls_front.sh` (the `oss.yml` workflow) makes certificates with\n"
                    "`init-certs.sh`, runs the real Caddy front with them, and proves the install CA path, that another\n"
                    "CA is refused, and the mTLS leg, and that its leg to the upstream is TLS and fails closed with\n"
                    "another CA's `origin-ca.crt` (HTTP 502, nothing forwarded). Install smoke leg 1 issues the first\n"
                    "certificates through `init-secrets.sh`, and leg 6 checks the door, and both origin legs, with the\n"
                    "install CA. `tools/ci/test_init_certs.sh`\n"
                    "(the `oss.yml` workflow) proves that a run without `--host` takes `ATLAS_PUBLIC_HOST` from `.env`\n"
                    "and that a `--force` renewal keeps it, and `backend/verify_cert_expiry.py` (the backend suite)\n"
                    "proves the warning for a certificate near expiry, far from it, and none configured. **Not\n"
                    "tested:** `--force` on a running install, and the recreate and redistribution steps.\n"
                    "\n"
                    "## 6. The MCP gateway (`--profile gateway`)\n"
                    "\n"
                    "Setting the gateway up is INSTALL section 6 (document *Install* on node `atlas-system-install`).\n"
                    "Running it:\n"
                    "\n"
                    "- **After changing** `ATLAS_PUBLIC_URL`, `ATLAS_PUBLIC_HOST` or the server list, render the\n"
                    "  configuration again, have the pinned release parse it, and recreate:\n"
                    "\n"
                    "  ```sh\n"
                    "  python3 ../../scripts/render-gateway.py\n"
                    "  docker compose --profile gateway run --rm --no-deps atlas-gateway -f /etc/agentgateway/config.yaml --validate-only\n"
                    "  docker compose --profile gateway up -d --force-recreate atlas-gateway\n"
                    "  ```\n"
                    "\n"
                    "  With your own server list, pass `--servers gateway/servers.yaml` to `render-gateway.py` every\n"
                    "  time. It refuses an unsafe configuration (a token sent over `http://`, a CORS origin of `*`, an\n"
                    "  unknown option) and writes nothing.\n"
                    "- **After an upgrade** that moved the pinned gateway image, pull it, then render and recreate as\n"
                    "  above: `docker compose --profile gateway pull atlas-gateway`.\n"
                    "- **After renewing certificates**, recreate it ([section 5](#5-certificates)): it serves\n"
                    "  `server.crt` and trusts `ca.crt`.\n"
                    "- **Health.** The gateway image has no shell, so it has no container healthcheck. Check its door\n"
                    "  from outside; any HTTP status means it is up:\n"
                    "\n"
                    "  ```sh\n"
                    "  curl --cacert secrets/tls/ca.crt -o /dev/null -w '%{http_code}\\n' https://atlas.example.com:8444/mcp\n"
                    "  ```\n"
                    "\n"
                    "- **Logs:** `docker compose --profile gateway logs atlas-gateway`. A warning that no session key\n"
                    "  is configured, or that a generated one could not be written, means the configuration was not\n"
                    "  rendered by this release's `render-gateway.py`, which turns MCP session-ID encryption off\n"
                    "  (`config.session.key: disabled`); render it again.\n"
                    "- **Always on:** set `COMPOSE_PROFILES=gateway` in `.env`, and every `docker compose up` starts it.\n"
                    "  Remove it from `COMPOSE_PROFILES` and stop the service (`docker compose --profile gateway stop\n"
                    "  atlas-gateway`) to turn it off.\n"
                    "\n"
                    "Every call through the gateway is made with the caller's own token, so revoking a token\n"
                    "([section 4](#per-user-agent-tokens)) works the same there.\n"
                    "\n"
                    "**Tested:** install smoke leg 5b (render, `config --quiet`, `pull`, `--validate-only`, `up`, a\n"
                    "restricted allow-list and a wrong CA refused) and leg 6 (the TLS door on 8444).\n"
                    "\n"
                    "## 7. Logs and health\n"
                    "\n"
                    "### Health\n"
                    "\n"
                    "| Check | Who may call it | What it says |\n"
                    "|---|---|---|\n"
                    "| `docker compose ps -a` | the VM | each service's state and container healthcheck. `atlas-migrate` should be `exited (0)`; the others `running` and `healthy` (the gateway has no healthcheck) |\n"
                    "| `GET /healthz` | anyone | liveness: `atlas-api` answers. No database call |\n"
                    "| `GET /readyz` | anyone | readiness: `atlas-api` can query the database. `503` when it cannot |\n"
                    "| `GET /api/health` | a signed-in person or a token | node, edge and event counts, `token_warnings` (tokens near expiry or long unused), `cert_warnings` (the server certificate near expiry, [section 5](#the-expiry-warning)), `retrieval` when the add-on is on (`on`, `degraded: ...` or `unavailable: ...`) |\n"
                    "\n"
                    "From outside, through the TLS door, with the install CA:\n"
                    "\n"
                    "```sh\n"
                    "curl --cacert secrets/tls/ca.crt https://atlas.example.com/healthz\n"
                    "curl --cacert secrets/tls/ca.crt https://atlas.example.com/readyz\n"
                    "```\n"
                    "\n"
                    "Point an external monitor at `/healthz` and `/readyz`. They carry no information beyond the status,\n"
                    "so they are safe to expose. `/api/health` needs a credential on purpose: it is a reconnaissance\n"
                    "document. Read it in a signed-in browser, or give a monitor a `read`-scoped agent token.\n"
                    "\n"
                    "`token_warnings` is the early warning for agent tokens. An expired token only shows up as `401`s at\n"
                    "the agent, so look at the list regularly and rotate before the date.\n"
                    "\n"
                    "### Logs\n"
                    "\n"
                    "Every service logs to standard output, so `docker compose logs` reads them:\n"
                    "\n"
                    "```sh\n"
                    "docker compose logs atlas-api\n"
                    "docker compose logs atlas-migrate | tail -20\n"
                    "```\n"
                    "\n"
                    "`atlas-api` and `atlas-mcp` name the reason when they refuse to start: a missing or malformed\n"
                    "setting, an unreadable secret file, or an unknown feature name. Every boot failure is fail-closed,\n"
                    "so a service that is not healthy has a message in its log saying why.\n"
                    "\n"
                    "Docker keeps container logs on the VM. Its default logging driver does not rotate them, so\n"
                    "configure rotation for the Docker engine on the VM (the engine's log options; *not part of the\n"
                    "install*). Atlas is written not to log passwords, tokens or session ids, but logs do contain\n"
                    "usernames, request paths and error details, so treat them as internal.\n"
                    "\n"
                    "**Tested:** install smoke legs 1, 2 and 6 (every service healthy, `atlas-migrate` exits 0, `/healthz`\n"
                    "through the door with the install CA).\n"
                    "\n"
                    "## 8. Audit export\n"
                    "\n"
                    "Atlas keeps three audit trails: `graph_event` (every graph change), `auth_event` (sign-ins, account\n"
                    "and token changes) and, if you turn it on, `audit_read` (what agents read). SECURITY.md\n"
                    "describes what each holds and what it never holds.\n"
                    "\n"
                    "**Read audit.** Off by default. Set `ATLAS_AUDIT_READS=1` in `.env` and recreate `atlas-api`. To\n"
                    "limit how long `audit_read` rows are kept, set `ATLAS_AUDIT_RETENTION_DAYS` and run the purge from\n"
                    "cron on the VM:\n"
                    "\n"
                    "```sh\n"
                    "docker compose exec -T atlas-api atlas-admin audit purge\n"
                    "```\n"
                    "\n"
                    "Without `ATLAS_AUDIT_RETENTION_DAYS` it deletes nothing. `graph_event` and `auth_event` are never\n"
                    "purged.\n"
                    "\n"
                    "**Export to a SIEM.** `GET /api/audit/export` streams all three trails as JSON Lines, oldest\n"
                    "first. Parameters: `since` and `until` (ISO-8601, `until` exclusive; a date alone is read as UTC\n"
                    "midnight) and `kinds` (a comma-separated subset of `graph_event,auth_event,audit_read`). Only two\n"
                    "callers get anything but `403`: an owner in a browser session, or an agent token with the `admin`\n"
                    "scope whose person is a live owner, checked again on every call.\n"
                    "\n"
                    "For a collector, mint an owner's token for that purpose (an owner may hold `admin`), and move it to\n"
                    "the collector as in INSTALL section 7 (document *Install* on node `atlas-system-install`):\n"
                    "\n"
                    "```sh\n"
                    "docker compose exec -T atlas-api atlas-admin \\\n"
                    "  token mint --user <owner> --purpose siem --scopes read,admin --days 90 --out /tmp/siem.token\n"
                    "```\n"
                    "\n"
                    "*Untested, generic.* On the collector, keep the token out of the command line by putting the\n"
                    "header in a curl configuration file readable only by you, then call the export:\n"
                    "\n"
                    "```sh\n"
                    "( umask 077; { printf 'header = \"Authorization: Bearer '; tr -d '\\n' < ~/.config/atlas/siem.token; printf '\"\\n'; } > ~/.config/atlas/siem.curl )\n"
                    "curl --cacert ca.crt -K ~/.config/atlas/siem.curl \"https://atlas.example.com/api/audit/export?since=2026-10-01\" > atlas-audit.jsonl\n"
                    "```\n"
                    "\n"
                    "Rotate this token like any other ([section 4](#per-user-agent-tokens)). If the owner is disabled or\n"
                    "demoted, the export stops for that token at once.\n"
                    "\n"
                    "**Tested:** `backend/verify_audit_export.py` (the export's line format, order, filters and `400`s,\n"
                    "`403` for members and non-admin tokens, `200` for an owner's admin token, no secret in it, and\n"
                    "`audit purge` deleting only old `audit_read` rows). The curl call above is not run by CI.\n"
                    "\n"
                    "## 9. Feature switches: `ATLAS_DISABLED_FEATURES`\n"
                    "\n"
                    "A few features exist for one particular install and have no use elsewhere. `ATLAS_DISABLED_FEATURES`\n"
                    "switches them off; the OSS `.env.example` switches all of them off:\n"
                    "\n"
                    "```sh\n"
                    "ATLAS_DISABLED_FEATURES=github_link,save_work,deploy_status,deploy_warning,attention_probes\n"
                    "```\n"
                    "\n"
                    "| Name | What it removes |\n"
                    "|---|---|\n"
                    "| `github_link` | the GitHub-linking routes (they answer `404`) |\n"
                    "| `save_work` | the `atlas_save_work` MCP tool and the `signoff` prompt |\n"
                    "| `deploy_status` | the `deploy` field of `/api/health`, and `atlas-mcp`'s own image check |\n"
                    "| `deploy_warning` | the `deploy_warning` field on MCP responses |\n"
                    "| `attention_probes` | reserved: nothing is guarded by it yet; accepted so it can be set now |\n"
                    "\n"
                    "The list is comma-separated; case and spaces are ignored. **An unknown name stops the boot** with an\n"
                    "error naming it, so a typo cannot leave a feature on unnoticed. After changing it, recreate both\n"
                    "services that read it:\n"
                    "\n"
                    "```sh\n"
                    "docker compose up -d --force-recreate --wait atlas-api atlas-mcp\n"
                    "```\n"
                    "\n"
                    "CONFIG.md is the reference entry.\n"
                    "\n"
                    "**Tested:** `verify.yml` runs the whole backend suite a second time with every feature disabled, and\n"
                    "every `mcp/verify_*.py` likewise; `backend/verify_feature_flags.py` and `mcp/verify_feature_flags.py`\n"
                    "check each switch, and that `atlas-api` and `atlas-mcp` agree on the list of names.\n"
                    "\n"
                    "## 10. Keeping up with patches\n"
                    "\n"
                    "The repository's CI watches for vulnerable and outdated components. An install benefits only by\n"
                    "taking new releases: the images are built from pinned bases, so nothing on the VM updates by\n"
                    "itself.\n"
                    "\n"
                    "What CI does upstream (SECURITY.md has the full table):\n"
                    "\n"
                    "- **SCA.** Trivy scans the images, the Python locks and the vendored browser JavaScript on every\n"
                    "  pull request and weekly (`sbom-sca.yml`, `tools/ci/sca_gate.py`). A critical or high\n"
                    "  vulnerability that has a fix fails the build. The released images go through the same gate\n"
                    "  before they are published (`images.yml`).\n"
                    "- **EOL gate.** `tools/ci/check_eol.py` checks every runtime the tree names (Python, Postgres,\n"
                    "  Caddy, Node) against a dated end-of-life table (`tools/ci/eol.json`) and fails within six months\n"
                    "  of end of support, or when the table is stale. The weekly run compares the table with\n"
                    "  endoflife.date.\n"
                    "- **Dependabot** proposes new base image digests and dependency versions, which land as ordinary\n"
                    "  commits.\n"
                    "- **SBOMs** (CycloneDX) for each image and lock are produced by `sbom-sca.yml`.\n"
                    "\n"
                    "What you do on the install:\n"
                    "\n"
                    "1. **Follow the releases** of the repository you install from (`vX.Y.Z` tags; a PATCH release is\n"
                    "   fixes and patched bases only), and read the release notes in RELEASES.md (document *Releases* on node `atlas-system-install`) for\n"
                    "   new settings and one-time steps.\n"
                    "2. **Upgrade** with INSTALL section 10 (document *Install* on node `atlas-system-install`) and [section 3](#3-upgrade) of\n"
                    "   this guide: back up, check out, compare `.env`, rebuild (or pull the released images), run the\n"
                    "   collation check when the Postgres image changed.\n"
                    "3. **Check your checkout** if you want the same answer locally. The EOL gate needs only Python's\n"
                    "   standard library; from the repository root:\n"
                    "\n"
                    "   ```sh\n"
                    "   python3 tools/ci/check_eol.py\n"
                    "   ```\n"
                    "\n"
                    "   It exits 1 and names the runtime when one is near or past its end of support in the version you\n"
                    "   run.\n"
                    "4. **Patch the VM itself**: the operating system and the Docker engine are outside Atlas and its\n"
                    "   CI.\n"
                    "\n"
                    "## What CI tests and what it does not\n"
                    "\n"
                    "| Area | Tested by CI | Not tested |\n"
                    "|---|---|---|\n"
                    "| Backup | `atlas-admin backup run`, `--verify-only`, corruption detection, retention, routine schedule and `ATLAS_BACKUP_KEEP`, no credential in output, the backup verbs with SSO settings but no OIDC files (`verify_crunch_backup.py`, against a test database); the `atlas-backup` compose service, statically; `backup run` on the OSS stack (smoke leg 8d), and with SSO on (leg 9f) | the loop as a long-running service on the OSS stack; the `ATLAS_BACKUP_HOST_DIR` bind mount; the plain `pg_dump`; copying files out of the volume; backing up `secrets/` |\n"
                    "| Restore | `atlas-admin backup restore` and `rollback`: checksum first, dry run, in-use refusal, staging, safety copy (`verify_crunch_backup.py`); backup, change, restore, identical counts in every table on the OSS stack (smoke leg 8d), and the same with SSO on, its accounts and IdP memberships included (leg 9f) | restoring onto a new VM with the secrets put back; a full restore of a backup from an older release (the smoke's `upgrade` job dry-runs one); restoring a plain SQL dump |\n"
                    "| Upgrade | `atlas-migrate upgrade` idempotent and equal to startup's schema (`verify_upgrade_command.py`, smoke leg 2), also with the SSO settings set and no OIDC files (the same script, smoke leg 9h); the released images' checks and gate, `release.yml` rendered and pinned (`images.yml`, `oss.yml`); the previous release run from its published images, backed up, upgraded to the checkout, every table's count kept, old passwords, token and data working (smoke `upgrade` job) | an upgrade that skips releases; moving from one released image set to the next; rolling back |\n"
                    "| Collation | `db_collation_check.sh` in report mode on the upgraded OSS install (smoke `upgrade` job) | `--fix` against an OSS install after a real Debian change |\n"
                    "| Tokens | `token mint`, `list`, `revoke`, the user verbs (backend suite); `token mint --out` and a connected client (smoke leg 8) | - |\n"
                    "| Secrets files | first issue by `init-secrets.sh` (smoke leg 1); the database password rotated by `rotate-db-password.sh` with data kept, the old password refused, and a failed rotation rolled back (smoke leg 10) | replacing another file by hand and recreating; a rotation with `atlas-backup` running or `retrieval.yml` |\n"
                    "| Certificates | `init-certs.sh` and the TLS front, other CA refused, mTLS (`oss_tls_front.sh`); the public name from `.env` and kept on `--force` (`test_init_certs.sh`); the expiry warning in `/api/health` (`verify_cert_expiry.py`); first issue and the door (smoke legs 1 and 6) | `init-certs.sh --force` on a running install; redistributing `ca.crt` |\n"
                    "| Gateway | render, validate, start, allow-list, wrong CA refused, the TLS door (smoke legs 5b and 6) | - |\n"
                    "| Health and logs | every service healthy, `/healthz` through the door (smoke legs 1, 2 and 6) | log rotation (outside Atlas) |\n"
                    "| Audit export | format, filters, access rule, purge (`verify_audit_export.py`) | the curl call from a collector |\n"
                    "| Feature switches | the backend and MCP suites with every feature off; `verify_feature_flags.py` in both | - |\n"
                    "| Patches | SCA, EOL gate, SAST, DAST, image gate (`sbom-sca.yml`, `sast.yml`, `dast.yml`, `images.yml`) | - |\n"
                ),
                "from": "docs/oss/guides/operations.md",
            },
            {
                "file": "server-requirements.md",
                "title": "Server requirements",
                "kind": "runbook",
                "content": (
                    "# Server requirements\n"
                    "\n"
                    "What a host needs before you install (document *Install* on node `atlas-system-install`) Atlas: memory and CPU, disk, and network.\n"
                    "The figures come from a measured run, not an estimate. The recommendations built on them are\n"
                    "labelled as recommendations, and this page says how each one was derived.\n"
                    "\n"
                    "## Where the figures come from\n"
                    "\n"
                    "Every install smoke run (`.github/workflows/install-smoke.yml`) installs the stack from\n"
                    "`deploy/oss/.env.example` on a clean CI runner and drives it through each documented feature, one\n"
                    "leg per step. A background sampler reads each container's cgroup about every 0.5 s for the whole\n"
                    "run, and `tools/ci/install_smoke_sizing.py` reduces the samples to a peak memory and a peak CPU per\n"
                    "service, overall and per leg.\n"
                    "\n"
                    "The figures on this page are from **install-smoke run 40480995551, on `master`, 2026-10-27**: a green\n"
                    "scheduled run, 943 samples over 13 legs.\n"
                    "\n"
                    "Two columns appear below:\n"
                    "\n"
                    "- **Peak memory (sampled)** is memory in use minus inactive file cache, the way `docker stats`\n"
                    "  reports it, at the highest sample.\n"
                    "- **Peak memory (cgroup)** is the kernel's own high-water mark (`memory.peak`) for the container's\n"
                    "  whole life, page cache included. It is read once, just before teardown. It is missing for a\n"
                    "  one-shot container that has already exited. For `atlas-api` it covers only the last container,\n"
                    "  because the smoke recreates `atlas-api` for the retrieval and sign-in legs.\n"
                    "\n"
                    "CPU is a percentage of one core between two consecutive samples, so 100% means one core fully busy\n"
                    "and 144% means about one and a half cores.\n"
                    "\n"
                    "### What CI is not\n"
                    "\n"
                    "A CI run is not a production host, and these figures are a floor, not a forecast:\n"
                    "\n"
                    "- **The data is tiny.** The smoke starts from an empty database and adds two people, their\n"
                    "  tokens and a few work items. A real graph with months of history keeps more of Postgres in\n"
                    "  memory and makes every index pass and search do more work.\n"
                    "- **The load is one caller at a time.** Each leg is a test script, not a team. Concurrent people\n"
                    "  and agents raise `atlas-api` and `atlas-postgres` CPU at the same moment, where in the smoke\n"
                    "  their peaks mostly fall in different legs.\n"
                    "- **The run is short.** The legs take about two and a half minutes. Slow growth, such as a\n"
                    "  connection pool filling up or a cache warming, does not show.\n"
                    "- **Image builds are not measured.** The sampler starts after the runner is prepared. Building the\n"
                    "  images on the host (`docker compose up --build`) needs memory and disk of its own (see\n"
                    "  [Disk](#disk)).\n"
                    "\n"
                    "## Base install\n"
                    "\n"
                    "The base install is `deploy/oss/docker-compose.yml` alone: keyword search, no retrieval add-on, no\n"
                    "gateway. These figures are from the legs before the retrieval leg, `1-install` to `8-client-pack`.\n"
                    "Leg 1 of the smoke fails if the retrieval service would start by default, so these legs never run\n"
                    "an embedding service.\n"
                    "\n"
                    "| Service | Role | Peak memory (sampled) | Peak memory (cgroup) | Peak CPU (one core = 100%) |\n"
                    "|---|---|---:|---:|---:|\n"
                    "| `atlas-api` | the board and the REST API | 172.7 MiB | 144.4 MiB¹ | 144% |\n"
                    "| `atlas-postgres` | Postgres 16 with pgvector | 52.9 MiB | 101.7 MiB | 35%² |\n"
                    "| `atlas-mcp` | the MCP server | 67.3 MiB | 68.8 MiB | 32% |\n"
                    "| `atlas-tls` | the TLS ingress (Caddy) | 24.2 MiB | 85.3 MiB | 10% |\n"
                    "| `atlas-migrate` | one-shot schema upgrade, then exits | 64.9 MiB | n/a (exited) | 98% |\n"
                    "\n"
                    "¹ The last `atlas-api` container only (see above). The sampled column covers every leg.\n"
                    "² The overall peak, reached in the sign-in leg. In the base legs `atlas-postgres` peaked at 22%.\n"
                    "\n"
                    "`atlas-api` peaked at 172.7 MiB during leg `2-migrate`, when the smoke runs `atlas-migrate upgrade`\n"
                    "twice more inside it. Its steady level across the other base legs was 70 to 104 MiB. Its CPU\n"
                    "peak, 144%, came in leg `4-mcp-contract`, the MCP contract tests.\n"
                    "\n"
                    "`atlas-migrate` runs before `atlas-api` starts and then exits, so its memory is never held at the\n"
                    "same time as the API's peak.\n"
                    "\n"
                    "### The optional gateway\n"
                    "\n"
                    "The `gateway` profile adds `atlas-gateway` (AgentGateway in front of `atlas-mcp`). It was started in\n"
                    "leg `5b-gateway` and stayed up to the end of the run.\n"
                    "\n"
                    "| Service | Peak memory (sampled) | Peak memory (cgroup) | Peak CPU |\n"
                    "|---|---:|---:|---:|\n"
                    "| `atlas-gateway` | 20.6 MiB | 16.4 MiB | 5% |\n"
                    "\n"
                    "### Not part of Atlas\n"
                    "\n"
                    "The run also measured two test-only containers. Neither is part of an install, and neither is\n"
                    "counted in any total on this page:\n"
                    "\n"
                    "- `atlas-idp` (261.2 MiB, 228% CPU) is a throwaway OpenID provider for the sign-in leg. With\n"
                    "  single sign-on, your identity provider runs somewhere else and costs this host nothing.\n"
                    "- `atlas-embed-stub` (14.7 MiB, 6% CPU) is a stub embedding endpoint for the retrieval leg. It\n"
                    "  stands in for the real embedding service (see below).\n"
                    "\n"
                    "## Retrieval add-on\n"
                    "\n"
                    "The retrieval add-on (semantic search) runs **inside `atlas-api`**. The overlay\n"
                    "`deploy/oss/retrieval.yml` gives `atlas-api` a route out to an embedding endpoint and a key. No model\n"
                    "and no embedding service ships with Atlas. In the smoke, leg `8b-retrieval` recreates `atlas-api`\n"
                    "with the overlay and points it at the stub. It indexes the graph, stops the stub to check that\n"
                    "search degrades to keyword search, and then puts `atlas-api` back on the base file. The table below\n"
                    "compares that leg with the base legs before it, so the difference is the add-on.\n"
                    "\n"
                    "| Service | Base legs, peak memory / CPU | Leg `8b-retrieval`, peak memory / CPU |\n"
                    "|---|---:|---:|\n"
                    "| `atlas-api` | 172.7 MiB / 144% (steady 70-104 MiB) | 81.2 MiB / 103% |\n"
                    "| `atlas-postgres` | 52.9 MiB / 22% | 52.7 MiB / 15% |\n"
                    "| `atlas-mcp` | 67.3 MiB / 32% | 54.1 MiB / 10% |\n"
                    "| `atlas-tls` | 23.7 MiB / 10% | 24.2 MiB / 3% |\n"
                    "\n"
                    "**On the Atlas host, the add-on's memory cost was too small for this run to measure.** `atlas-api`\n"
                    "with retrieval on stayed inside the base legs' range, and so did `atlas-postgres`. Indexing used\n"
                    "about one core while it ran. The run does not show how the add-on scales, because the graph was\n"
                    "almost empty. What it does show is where the real cost of retrieval is. It is not in Atlas.\n"
                    "It is in two other places:\n"
                    "\n"
                    "1. **The embedding endpoint.** If it is a hosted service, it costs this host nothing but network.\n"
                    "   If it is a **local embedding model, it needs its own memory and usually a GPU**, sized from that\n"
                    "   model's documentation, not from this page. Run it on a separate node, reached over TLS, as\n"
                    "   `retrieval.yml` describes. If you put it on the same machine, add its full requirement on top of\n"
                    "   the recommendation below. CI never runs a real model, so no figure here covers one.\n"
                    "2. **The database.** Each indexed chunk stores one vector. See [Disk](#disk).\n"
                    "\n"
                    "Indexing CPU grows with the amount of text, and it comes in bursts: one pass every\n"
                    "`ATLAS_RETRIEVAL_INTERVAL_S` seconds (60 by default), sending `ATLAS_EMBED_BATCH` texts per call.\n"
                    "The first full index of a large existing graph is the heaviest pass. Later passes embed only what\n"
                    "changed.\n"
                    "\n"
                    "## Recommended VM\n"
                    "\n"
                    "| | Base (with or without the gateway) | Base + retrieval, hosted or remote embedding endpoint |\n"
                    "|---|---|---|\n"
                    "| vCPU | 2 | 2 |\n"
                    "| Memory | 4 GiB (2 GiB minimum) | 4 GiB |\n"
                    "| Disk | 20 GiB for system, images and data, plus backup space | the same, plus the vector storage below |\n"
                    "\n"
                    "Add a local embedding model's own requirement on top of this, or give it its own node.\n"
                    "\n"
                    "**How the headroom was chosen.** The rule is: memory = (sum of the per-service peaks) x 2 + 1 GiB\n"
                    "for the operating system and the Docker engine, then rounded up to a common VM size.\n"
                    "\n"
                    "- The sum takes each long-running service at the higher of its two peak columns: `atlas-api`\n"
                    "  172.7 + `atlas-postgres` 101.7 + `atlas-mcp` 68.8 + `atlas-tls` 85.3 + `atlas-gateway` 20.6 =\n"
                    "  **449.1 MiB**. That is an upper bound, because the peaks did not happen at the same moment.\n"
                    "  `atlas-migrate` is left out because it exits before the API starts.\n"
                    "- **x 2** covers what CI does not show: a real graph in Postgres's cache, several people and agents\n"
                    "  at once, and longer uptime. 449 MiB x 2 + 1 GiB is about 1.9 GiB, so **2 GiB is the minimum**.\n"
                    "  The **4 GiB recommendation** doubles that again for Postgres. Postgres serves reads from the\n"
                    "  operating system's page cache, and on a 2 GiB host the cache is the first thing to go as the\n"
                    "  database grows.\n"
                    "- **CPU:** `atlas-api` alone burst to 1.4 cores. In the busiest base leg (`4-mcp-contract`),\n"
                    "  the per-service peaks of `atlas-api`, `atlas-postgres`, `atlas-tls` and `atlas-mcp` add up to\n"
                    "  about 1.7 cores. **Two vCPU** keep a burst\n"
                    "  from stalling the board, the MCP server and the database at the same time. If many agents call\n"
                    "  MCP at once, add cores before you add memory.\n"
                    "\n"
                    "These are starting points. Check them against your own host after a week of use: run\n"
                    "`docker stats --no-stream` for the services, and `SELECT pg_size_pretty(pg_database_size('atlas'))`\n"
                    "in `atlas-postgres` for the database.\n"
                    "\n"
                    "## Disk\n"
                    "\n"
                    "**Volumes.** The install keeps its state in three named volumes:\n"
                    "\n"
                    "| Volume | What it holds | Growth |\n"
                    "|---|---|---|\n"
                    "| `atlas-pgdata` | the whole database | grows with the graph |\n"
                    "| `atlas-tls-data` | issued certificates, and Caddy's own CA in `internal` TLS mode | small, steady |\n"
                    "| `atlas-tls-config` | Caddy's runtime configuration | small, steady |\n"
                    "\n"
                    "`deploy/oss/secrets/` on the host holds the database password, the peppers and the install CA. It\n"
                    "is a few kilobytes, but you cannot rebuild it: without it, existing passwords and tokens stop\n"
                    "working.\n"
                    "\n"
                    "**Database growth.** The smoke's database is too small to give a growth rate, so measure your own.\n"
                    "Check `pg_database_size('atlas')` once a week for the first month, then size the volume for at\n"
                    "least a year at that rate with room to spare. Everything in Atlas is rows in Postgres. Nodes,\n"
                    "edges, documents and work items grow with use, and the event history only ever grows.\n"
                    "\n"
                    "**Vector storage (retrieval add-on).** Each chunk stores one `vector(ATLAS_EMBED_DIM)`: 4 bytes per\n"
                    "dimension plus a small header, on top of the chunk's text. At 1536 dimensions that is about 6 KiB\n"
                    "per chunk. At 1024 dimensions it is about 4 KiB. Multiply by the number of chunks in your graph:\n"
                    "long documents make many chunks, and short nodes make one each. The retrieval tables can be\n"
                    "rebuilt from the graph, so a lost index costs a reindex, not data.\n"
                    "\n"
                    "**Images.** Building on the host (`--build`) needs space for the build cache as well as the\n"
                    "images. The smoke did not measure image sizes. After installing, `docker system df` shows them.\n"
                    "Prune old images after an upgrade.\n"
                    "\n"
                    "**Backups.** A database dump (`pg_dump` in custom format) is usually much smaller than the\n"
                    "`atlas-pgdata` volume. Leave space for at least two dumps at the size of the live database: the\n"
                    "dump being written and the last good one. Keep the backups you rely on off this host. A backup on\n"
                    "the same disk does not survive the disk failing. Back up `deploy/oss/secrets/` and `atlas-tls-data`\n"
                    "with the database: a restored database without the peppers cannot check a password or a token.\n"
                    "Operations (document *Operations guide* on node `atlas-system-install`) has the backup and restore procedure.\n"
                    "\n"
                    "## Network\n"
                    "\n"
                    "**Inbound.** The install publishes one port, and it is TLS:\n"
                    "\n"
                    "| Port (default) | Service | Setting | When |\n"
                    "|---|---|---|---|\n"
                    "| 443 | `atlas-tls`: board, API and `/mcp`, HTTPS | `ATLAS_TLS_PORT`, `ATLAS_TLS_BIND` | always |\n"
                    "| 8444 | `atlas-gateway`: the gateway's MCP door, HTTPS | `ATLAS_GATEWAY_PORT`, `ATLAS_GATEWAY_BIND` | `gateway` profile only |\n"
                    "\n"
                    "Nothing else is published. `atlas-api` (HTTPS on 8443), `atlas-mcp` (HTTPS on 8493) and\n"
                    "`atlas-postgres` sit on internal Docker networks with no published port and no route out, and\n"
                    "`tools/ci/check_tls.sh` fails the build if a plain-HTTP port is ever published. No port that\n"
                    "carries a token or a password is published on all interfaces (`0.0.0.0`) without TLS.\n"
                    "\n"
                    "The two TLS doors themselves **are** bound to all interfaces by default (`ATLAS_TLS_BIND=0.0.0.0`\n"
                    "and `ATLAS_GATEWAY_BIND=0.0.0.0` in `.env.example`), because they are the doors people and agents\n"
                    "use. To narrow that, set the bind to one address: `127.0.0.1` behind a reverse proxy on the same\n"
                    "host, or the address of the interface your users reach. Put a firewall in front of the host in\n"
                    "any case, and leave the gateway's port closed unless you run the `gateway` profile.\n"
                    "\n"
                    "**TLS.** Every hop that carries a token is HTTPS with the certificate verified, including\n"
                    "`atlas-mcp` to `atlas-api` and the gateway to `atlas-mcp` inside the host. `ATLAS_TLS_MODE` sets\n"
                    "where the public certificate comes from: your own PKI or the generated install CA (`files`),\n"
                    "Caddy's own CA (`internal`), or Let's Encrypt by DNS challenge (`acme-cloudflare`). Clients that do\n"
                    "not use a public CA must trust `deploy/oss/secrets/tls/ca.crt` or your PKI's root. The DNS name in\n"
                    "`ATLAS_PUBLIC_HOST` must resolve to this host for everyone who uses it.\n"
                    "\n"
                    "**Outbound.** Once the stack is up, the base install opens no connection out of its own networks.\n"
                    "Leg 1b of the smoke drops and counts any such connection for the rest of the run, and the run fails\n"
                    "if one was attempted. You only need outbound access for:\n"
                    "\n"
                    "- installing and upgrading: the container registries and package mirrors the image builds use, or\n"
                    "  your registry if you pull released images;\n"
                    "- `ATLAS_TLS_MODE=acme-cloudflare`: Let's Encrypt and the Cloudflare API;\n"
                    "- the retrieval add-on: the embedding endpoint in `ATLAS_EMBED_URL`, over HTTPS;\n"
                    "- single sign-on: your identity provider;\n"
                    "- an external or managed PostgreSQL (the overlay `external-db.yml`): the database server's port,\n"
                    "  from `atlas-migrate`, `atlas-api` and `atlas-backup`\n"
                    "  (External or managed PostgreSQL (document *External Postgres* on node `atlas-system-postgres`)).\n"
                    "\n"
                    "## Host software\n"
                    "\n"
                    "- 64-bit Linux with Docker Engine and the Docker Compose v2 plugin (`docker compose`, v2.17.0 or\n"
                    "  later for `up --wait --wait-timeout`). The smoke runs on a cgroup v2 host.\n"
                    "- A VM or host of its own: not a WSL distro beside another Docker daemon, since WSL 2 distros\n"
                    "  share one network stack.\n"
                    "- Python 3 on the host only if you use the gateway profile (`scripts/render-gateway.py`).\n"
                    "- The images run as a non-root numeric UID, and under an arbitrary UID in group 0 as well, so a\n"
                    "  platform that assigns UIDs (OpenShift-style) works unchanged.\n"
                ),
                "from": "docs/oss/guides/server-requirements.md",
            },
        ],
    },
]

EDGES = [
    {
        "src": "atlas-system-api",
        "edge_type": "requires",
        "dst": "atlas-system-postgres",
    },
    {
        "src": "atlas-system-backup",
        "edge_type": "requires",
        "dst": "atlas-system-postgres",
    },
    {
        "src": "atlas-system-board",
        "edge_type": "requires",
        "dst": "atlas-system-api",
    },
    {
        "src": "atlas-system-cli",
        "edge_type": "requires",
        "dst": "atlas-system-postgres",
    },
    {
        "src": "atlas-system-clients-claude-code",
        "edge_type": "requires",
        "dst": "atlas-system-mcp",
    },
    {
        "src": "atlas-system-gateway",
        "edge_type": "requires",
        "dst": "atlas-system-mcp",
    },
    {
        "src": "atlas-system-mcp",
        "edge_type": "requires",
        "dst": "atlas-system-api",
    },
    {
        "src": "atlas-system-mcp",
        "edge_type": "requires",
        "dst": "atlas-system-tls",
    },
    {
        "src": "atlas-system-migrate",
        "edge_type": "requires",
        "dst": "atlas-system-postgres",
    },
    {
        "src": "atlas-system-tls",
        "edge_type": "requires",
        "dst": "atlas-system-api",
    },
    {
        "src": "atlas-system-tool-adr-get",
        "edge_type": "requires",
        "dst": "atlas-system-api-adrs",
    },
    {
        "src": "atlas-system-tool-adr-search",
        "edge_type": "requires",
        "dst": "atlas-system-api-adrs",
    },
    {
        "src": "atlas-system-tool-capabilities",
        "edge_type": "requires",
        "dst": "atlas-system-api-workitems",
    },
    {
        "src": "atlas-system-tool-comment-write",
        "edge_type": "requires",
        "dst": "atlas-system-api-comments",
    },
    {
        "src": "atlas-system-tool-comments",
        "edge_type": "requires",
        "dst": "atlas-system-api-comments",
    },
    {
        "src": "atlas-system-tool-context",
        "edge_type": "requires",
        "dst": "atlas-system-api-context",
    },
    {
        "src": "atlas-system-tool-delete",
        "edge_type": "requires",
        "dst": "atlas-system-api-docs",
    },
    {
        "src": "atlas-system-tool-delete",
        "edge_type": "requires",
        "dst": "atlas-system-api-edges",
    },
    {
        "src": "atlas-system-tool-delete",
        "edge_type": "requires",
        "dst": "atlas-system-api-graph",
    },
    {
        "src": "atlas-system-tool-delete",
        "edge_type": "requires",
        "dst": "atlas-system-api-intents",
    },
    {
        "src": "atlas-system-tool-delete",
        "edge_type": "requires",
        "dst": "atlas-system-api-nodes",
    },
    {
        "src": "atlas-system-tool-doc-read",
        "edge_type": "requires",
        "dst": "atlas-system-api-docs",
    },
    {
        "src": "atlas-system-tool-doc-write",
        "edge_type": "requires",
        "dst": "atlas-system-api-docs",
    },
    {
        "src": "atlas-system-tool-doc-write",
        "edge_type": "requires",
        "dst": "atlas-system-api-nodes",
    },
    {
        "src": "atlas-system-tool-edge-delete",
        "edge_type": "requires",
        "dst": "atlas-system-api-edges",
    },
    {
        "src": "atlas-system-tool-edge-write",
        "edge_type": "requires",
        "dst": "atlas-system-api-edges",
    },
    {
        "src": "atlas-system-tool-file-work",
        "edge_type": "requires",
        "dst": "atlas-system-api-workitems",
    },
    {
        "src": "atlas-system-tool-get",
        "edge_type": "requires",
        "dst": "atlas-system-api-nodes",
    },
    {
        "src": "atlas-system-tool-health",
        "edge_type": "requires",
        "dst": "atlas-system-api-health",
    },
    {
        "src": "atlas-system-tool-intent-delete",
        "edge_type": "requires",
        "dst": "atlas-system-api-intents",
    },
    {
        "src": "atlas-system-tool-intent-write",
        "edge_type": "requires",
        "dst": "atlas-system-api-intents",
    },
    {
        "src": "atlas-system-tool-intents",
        "edge_type": "requires",
        "dst": "atlas-system-api-intents",
    },
    {
        "src": "atlas-system-tool-node-search",
        "edge_type": "requires",
        "dst": "atlas-system-api-search",
    },
    {
        "src": "atlas-system-tool-node-write",
        "edge_type": "requires",
        "dst": "atlas-system-api-nodes",
    },
    {
        "src": "atlas-system-tool-outline",
        "edge_type": "requires",
        "dst": "atlas-system-api-graph",
    },
    {
        "src": "atlas-system-tool-path",
        "edge_type": "requires",
        "dst": "atlas-system-api-graph",
    },
    {
        "src": "atlas-system-tool-save-work",
        "edge_type": "requires",
        "dst": "atlas-system-api-events",
    },
    {
        "src": "atlas-system-tool-search",
        "edge_type": "requires",
        "dst": "atlas-system-api-workitems",
    },
    {
        "src": "atlas-system-tool-setup-guide",
        "edge_type": "requires",
        "dst": "atlas-system-api-setup",
    },
    {
        "src": "atlas-system-tool-structure",
        "edge_type": "requires",
        "dst": "atlas-system-api-nodes",
    },
    {
        "src": "atlas-system-tool-tree",
        "edge_type": "requires",
        "dst": "atlas-system-api-graph",
    },
    {
        "src": "atlas-system-tool-update-work",
        "edge_type": "requires",
        "dst": "atlas-system-api-nodes",
    },
    {
        "src": "atlas-system-tool-update-work",
        "edge_type": "requires",
        "dst": "atlas-system-api-workitems",
    },
]

INTENTS = [
    {
        "node_id": "atlas-system",
        "slug": "agents-propose-humans-commit",
        "kind": "principle",
        "title": "Agents propose; humans commit",
        "statement": "An agent may read anything Atlas holds and propose any change to it. Committing a change that alters what other work is measured against — an intent, a binding, a scope decision, acceptance criteria — stays a human act. A human commits by doing it, by approving an agent's proposal (for acceptance criteria: one click on the board), or, where an install opts in (`ATLAS_ACCEPTANCE_AGENT_WRITE=attested`), by an approval the agent must cite by name and quote, which Atlas records with the change. Read-only is the default for any credential an agent carries.",
    },
    {
        "node_id": "atlas-system",
        "slug": "work-rolls-up",
        "kind": "principle",
        "title": "Work rolls up, never down",
        "statement": "A workitem bound to a node appears there and on every node containing it, and on nothing inside it. The invariant this buys: if a node's board shows a containment-derived item, that node's numbers count it. A fourth `above` bucket pushing an ancestor's work down onto descendants was proposed and rejected.",
    },
    {
        "node_id": "atlas-system",
        "slug": "oss-work-graph",
        "kind": "outcome",
        "title": "A self-hostable, open-source work and context layer for humans and their agents",
        "statement": "Atlas is self-hostable and open source. The core is the work: the work hierarchy (Epic > Feature > Story > Task > Sub-task), the board, a REST API and an MCP server, so an agent reads and writes the same work a person sees, within limits a person sets. Everything else is an add-on an install turns on when it needs it: context retrieval, single sign-on, guardrails, CI integration. The source is open and Atlas documents itself, so a team can change Atlas with its own agents. The product ships without anyone's world in it: an instance is configuration and data, never code.",
    },
    {
        "node_id": "atlas-system-postgres",
        "slug": "postgres-is-authoritative",
        "kind": "constraint",
        "title": "Nothing Atlas owns lives outside the Atlas database",
        "statement": "The Atlas database (PostgreSQL, hosted wherever the install chooses: the bundled container or a managed service) is the source of truth for everything Atlas owns. Node documentation, the backlog, its acceptance criteria and its recorded results are all rows. The three importers were deleted because a second run did not top the database up — it reverted the database to the files. Nothing in this repo can now overwrite the database from a file, and nothing added should be able to. An add-on may keep a derived copy elsewhere (a vector index, a hosted model's index) only if it can be rebuilt from the database and Atlas works without it.",
    },
    {
        "node_id": "atlas-system-context",
        "slug": "pointers-within-budgets",
        "kind": "constraint",
        "title": "The context answer is pointers within budgets",
        "statement": "The context answer is pointers within budgets: every part is bounded by a parameter and says when it was cut; the full text is one atlas_get or atlas_doc_read away.",
    },
    {
        "node_id": "atlas-system-identity",
        "slug": "token-belongs-to-the-human",
        "kind": "principle",
        "title": "A token belongs to a person, who stays accountable",
        "statement": "A service token belongs to an agent's OWN account, and that account records the person accountable for it. Both halves matter: \"belongs to the human\" is a claim about ACCOUNTABILITY, never about identity. `admin bot add` creates a distinct `auth_user` per agent and refuses a person outright; `auth.py` refuses any Bearer token whose owning account is not `is_service`. `auth_user.person_user_id` records who is answerable, grants inherit from person to bot, ROLE never does, and disabling a person disables their agents. An owner holds no grants, so an owner's agent inherits an EMPTY set — linking makes it less capable, not more. Two independent bounds apply to every machine caller: scopes bound what it may DO (enforced for every non-session principal, an unmapped route failing closed), and grants bound what it may SEE. Neither removes the accountability of the person who minted it.",
    },
    {
        "node_id": "atlas-system-mcp",
        "slug": "facade-not-second-brain",
        "kind": "constraint",
        "title": "The MCP holds no business logic and no copy of the graph",
        "statement": "Every verb atlas-mcp exposes is a call to atlas-api. No validation rule, no rollup, no projection and no cached copy of the graph lives in this service. If a tool needs something the API cannot answer, that is a missing endpoint and it gets built in atlas-api -- not worked around here. A facade that starts deciding things becomes a second implementation of the graph, and the two will disagree.",
    },
    {
        "node_id": "atlas-system-retrieval",
        "slug": "atlas-complete-without-it",
        "kind": "constraint",
        "title": "Atlas is complete without it",
        "statement": "Semantic retrieval is an add-on, never a requirement. With it off, or on but broken (no pgvector, no endpoint, the embedder down), Atlas boots, serves and searches exactly as it does without it; nothing in the core imports, calls or requires it, and its state is reported as information, never as red. Default off; an instance enables it by configuration only.",
    },
    {
        "node_id": "atlas-system-graph",
        "slug": "contains-is-part-of",
        "kind": "principle",
        "title": "`contains` means \"is part of\" — nothing else",
        "statement": "It is the product decomposition. Not ownership, not a permission grammar, and not deployment — where something runs is `hosted_on`'s job. A thing that is not a part of the product does not get a `contains` edge, however convenient the nesting looks.",
    },
    {
        "node_id": "atlas-system-graph",
        "slug": "node-holds-open-work",
        "kind": "constraint",
        "title": "Count OPEN workitems, and never let a count trigger a split",
        "statement": (
            "Status not `done` or `cancelled` -- the same lens the board's age-out filter uses. **Never count lifetime**: a lifetime count only rises, so a threshold on it fires in every node eventually just from work being finished, splitting the tree forever while findability never actually got worse.\n"
            "\n"
            "**The 5-25 ceiling this constraint used to state was RETIRED on 2026-08-15** (ADR 0030 amended). It fired on `atlas-mcp` the moment an epic was filed against it, which is the failure mode rather than the rule working: a node acquires work because work exists, not because it became hard to read. A number cannot tell you whether work is findable.\n"
            "\n"
            "So: split a node when finding the work in it actually gets hard, and say what got hard. A count is a description, never a trigger."
        ),
    },
    {
        "node_id": "atlas-system-graph",
        "slug": "ask-before-binding",
        "kind": "constraint",
        "title": "Ask before assigning a binding",
        "statement": "The pause on being asked to map things by hand ended with ADR 0030; the ask-first rule did not. A binding decides where work shows up for everyone who reads the board, so it is not an agent's call to make on someone else's behalf.",
    },
]

HANDBOOK_EXTRAS = {
    "atlas-system-gateway": {
        "docs": [],
        "setting": "COMPOSE_PROFILES=gateway",
    },
    "atlas-system-postgres": {
        "docs": [
            {
                "file": "external-postgres.md",
                "title": "External Postgres",
                "kind": "runbook",
                "content": (
                    "# External or managed PostgreSQL\n"
                    "\n"
                    "The OSS install runs its own database, the `atlas-postgres` container. This page is for an install\n"
                    "that should use a PostgreSQL you already run instead: a managed service from your cloud provider, or\n"
                    "a server your database team looks after.\n"
                    "\n"
                    "Atlas supports **PostgreSQL only**, hosted anywhere. There is no other database backend. The\n"
                    "overlay `deploy/oss/external-db.yml` runs the install against such a server instead of\n"
                    "`atlas-postgres`. Everything below was checked against the code and, where it says **Tested**,\n"
                    "run by CI's install smoke; what still does not work is listed as such, not described as working.\n"
                    "\n"
                    "It assumes the install described in INSTALL.md (document *Install* on node `atlas-system-install`). Every setting named here is in\n"
                    "CONFIG.md. Backup, restore and upgrade for the bundled database are in\n"
                    "Operations (document *Operations guide* on node `atlas-system-install`).\n"
                    "\n"
                    "## Read this first: what works\n"
                    "\n"
                    "**Add the overlay `external-db.yml`, put your URL in `secrets/db_url` and, for a verified\n"
                    "certificate, your database's CA in `secrets/db-ca.crt`.** `atlas-postgres` then never starts.\n"
                    "\n"
                    "| Part | Supported | How, or why not |\n"
                    "|---|---|---|\n"
                    "| `atlas-api`, `atlas-migrate` and `atlas-backup` reading a URL that names another host | yes | they read the URL from `ATLAS_DB_URL_FILE`; nothing in the code assumes `atlas-postgres` |\n"
                    "| Reaching a database outside the compose project | yes, with `external-db.yml` | it puts those three services on the `egress` network (see [Running the install on it](#running-the-install-on-it)) |\n"
                    "| Running without the `atlas-postgres` container | yes, with `external-db.yml` | it moves `atlas-postgres` to a profile nothing turns on, and removes `atlas-migrate`'s dependency on it |\n"
                    "| TLS with `sslmode=require` | yes | the URL's query parameters reach libpq (see [TLS](#tls-to-the-database)) |\n"
                    "| `sslmode=verify-full` (or `verify-ca`) against your database's CA | yes, with `external-db.yml` | it mounts the CA as the secret `db_ca`; the URL names it with `sslrootcert=/run/secrets/db_ca` |\n"
                    "| `atlas-admin backup run`, `backup restore`, `backup rollback` | yes, with conditions | a role with `CREATEDB` that owns the database, and PostgreSQL 16 or older (see [Backup and restore](#backup-and-restore)) |\n"
                    "| Client certificates (`sslcert`, `sslkey`) | **no** | no secret for them is mounted; password authentication only |\n"
                    "| `rotate-db-password.sh` | **no**, by design | it refuses a URL that does not point at `atlas-postgres` |\n"
                    "| `tools/db_collation_check.sh` | **no** | it runs `psql` inside the `atlas-postgres` container |\n"
                    "| PostgreSQL 17 or later | **untested**; backup **no** | Atlas is tested on 16; the backup image's `pg_dump` 16 refuses a newer server |\n"
                    "\n"
                    "## When to use it\n"
                    "\n"
                    "Use an external PostgreSQL when:\n"
                    "\n"
                    "- your organisation requires databases to run on a managed service or a database team's servers,\n"
                    "  with their backups, monitoring, patching and failover;\n"
                    "- you need high availability or point-in-time recovery, which the single `atlas-postgres`\n"
                    "  container does not give you;\n"
                    "- policy keeps data off the application host.\n"
                    "\n"
                    "Keep the bundled `atlas-postgres` when none of that applies. It is what CI installs and tests, and\n"
                    "everything in Operations (document *Operations guide* on node `atlas-system-install`) is written for it.\n"
                    "\n"
                    "## What the database needs\n"
                    "\n"
                    "### Version\n"
                    "\n"
                    "**PostgreSQL 16.** That is the version `atlas-postgres` runs (`deploy/postgres/pgvector.Dockerfile`,\n"
                    "on `pgvector/pgvector:...-pg16-...`), the version CI's disposable test databases run\n"
                    "(`postgres:16-alpine`), and the version the backup image's client tools are copied from.\n"
                    "\n"
                    "- The application itself has not been tested on any other major version.\n"
                    "- The backup image carries `pg_dump` and `pg_restore` **16** (`backend/Dockerfile`, stage\n"
                    "  `backup`), and checks that at build time. `pg_dump` refuses to dump a server of a newer major\n"
                    "  version, so `atlas-admin backup run` does not work against PostgreSQL 17 or later.\n"
                    "- `atlas-admin backup restore` drops temporary databases with `DROP DATABASE ... WITH (FORCE)`,\n"
                    "  which needs PostgreSQL 13 or later.\n"
                    "\n"
                    "### Extensions\n"
                    "\n"
                    "**The base install needs no extension.** Full-text search uses the built-in `english` text search\n"
                    "configuration and GIN indexes, which every PostgreSQL has.\n"
                    "\n"
                    "The retrieval add-on (document *Retrieval add-on guide* on node `atlas-system-retrieval`) needs **pgvector**. When it is turned on, `atlas-api` checks\n"
                    "`pg_available_extensions` for `vector` and runs `CREATE EXTENSION IF NOT EXISTS vector`\n"
                    "(`backend/retrieval/setup.py`). On a managed service:\n"
                    "\n"
                    "- the provider must offer pgvector (most do, under the name `vector`), and\n"
                    "- either the Atlas role may create it, or your database team creates it in the Atlas database\n"
                    "  first. The `IF NOT EXISTS` means an extension that is already there is used as it is.\n"
                    "\n"
                    "Without pgvector the add-on reports itself unavailable and search falls back to keywords; the rest\n"
                    "of Atlas is unaffected.\n"
                    "\n"
                    "### A database and a role\n"
                    "\n"
                    "Ask for **one database** (the bundled install calls it `atlas`) and **one login role** for Atlas.\n"
                    "Atlas shares the database with nothing else and keeps all its tables in the `public` schema: the\n"
                    "backup verifier counts `public.node`, `public.edge` and `public.graph_event` by that name.\n"
                    "\n"
                    "What the role does, from the code:\n"
                    "\n"
                    "| Operation | Who runs it | What it needs |\n"
                    "|---|---|---|\n"
                    "| `CREATE TABLE`, `CREATE SEQUENCE`, `CREATE INDEX`, `ALTER TABLE ... ADD COLUMN` in `public` | `atlas-migrate upgrade`, and `atlas-api` at startup | `CREATE` on schema `public` |\n"
                    "| reads and writes on its own tables | `atlas-api` | owner of the tables (it created them) |\n"
                    "| `CREATE EXTENSION vector` | `atlas-api`, retrieval add-on only | the right to create that extension, or it already exists |\n"
                    "| `CREATE DATABASE`, `DROP DATABASE` (a temporary copy, to verify each backup) | `atlas-admin backup run` | `CREATEDB` |\n"
                    "| `CREATE DATABASE ... OWNER`, `ALTER DATABASE ... RENAME` | `atlas-admin backup restore` and `rollback` | `CREATEDB`, ownership of the Atlas database, and a connection to the `postgres` (or `template1`) database |\n"
                    "\n"
                    "The simplest grant that covers all of it is a role that **owns the Atlas database** and has\n"
                    "**`CREATEDB`**. Owning the database is enough for `CREATE` on `public` on PostgreSQL 15 and later,\n"
                    "where `public` belongs to the database owner. The role needs no superuser right: nothing in the\n"
                    "backend runs `ALTER SYSTEM` or creates roles, and the only sessions it ends are those on its own\n"
                    "temporary databases, which it drops `WITH (FORCE)`.\n"
                    "\n"
                    "If your database team will not grant `CREATEDB`, the database itself still works; only Atlas's\n"
                    "own backup and restore commands do not, and you rely on the team's backups instead (see\n"
                    "[Backup and restore](#backup-and-restore)).\n"
                    "\n"
                    "In the bundled install that role is `atlas`, the image's superuser, which is why none of this comes\n"
                    "up there.\n"
                    "\n"
                    "### Encoding and collation\n"
                    "\n"
                    "Create the database with **UTF8** encoding. Atlas stores free text in many languages in `text` and\n"
                    "`jsonb` columns. The bundled database is created by the postgres image's `initdb` with its defaults,\n"
                    "UTF8 and the `en_US.utf8` locale.\n"
                    "\n"
                    "Text indexes are sorted by the collation library of the server's operating system. When that\n"
                    "library changes under the same data, a text index can end up out of order: lookups miss rows, and a\n"
                    "unique index can admit a duplicate. The bundled install guards against this with\n"
                    "`tools/db_collation_check.sh` (Operations, the collation check (document *Operations guide* on node `atlas-system-install`)).\n"
                    "**That script does not work against an external server**: it runs `psql` through `docker exec`\n"
                    "inside `atlas-postgres`. On a managed service the provider changes the library, so ask your\n"
                    "provider or database team how they handle collation version changes. To look for yourself, on\n"
                    "PostgreSQL 15 or later, run this in the Atlas database as any role:\n"
                    "\n"
                    "```sql\n"
                    "SELECT datname, datcollversion, pg_database_collation_actual_version(oid) AS actual\n"
                    "FROM pg_database WHERE datname = current_database();\n"
                    "```\n"
                    "\n"
                    "If the two versions differ, the repair is `REINDEX DATABASE`, then\n"
                    "`ALTER DATABASE <name> REFRESH COLLATION VERSION`, in that order, as the script does.\n"
                    "\n"
                    "A restore with `atlas-admin backup restore` creates its new database with the live database's\n"
                    "encoding, collation, ctype and locale provider (`backend/backup_restore.py`), so a restore keeps\n"
                    "whatever you chose here.\n"
                    "\n"
                    "## Pointing the install at it\n"
                    "\n"
                    "### The URL, in the secret file\n"
                    "\n"
                    "Atlas reads the database URL from **one file**, `deploy/oss/secrets/db_url`. The compose file mounts\n"
                    "it as the secret `db_url` into `atlas-migrate`, `atlas-api` and `atlas-backup`, and fixes\n"
                    "`ATLAS_DB_URL_FILE=/run/secrets/db_url` in their environment, over anything `.env` says. The\n"
                    "install sets `ATLAS_REQUIRE_SECRET_FILES=1`, so a plain `ATLAS_DB_URL` in `.env` is refused at\n"
                    "startup.\n"
                    "\n"
                    "**Never put the URL in `.env`, and never type it, with its password, on a command line**: argv is\n"
                    "visible to other users of the host and stays in shell history.\n"
                    "\n"
                    "The URL has this form:\n"
                    "\n"
                    "```text\n"
                    "postgresql+psycopg2://ROLE:PASSWORD@HOST:5432/DATABASE?sslmode=verify-full&sslrootcert=/run/secrets/db_ca\n"
                    "```\n"
                    "\n"
                    "- The `postgresql+psycopg2://` scheme names the driver Atlas ships. A URL with **no driver**,\n"
                    "  `postgresql://` or `postgres://` (such as CloudNativePG's `fqdn-uri`, or what a managed service\n"
                    "  hands you), works as it is: Atlas reads it as `postgresql+psycopg2://` (`backend/dsn.py`). A URL\n"
                    "  that names another driver is used unchanged, and that driver is not in the image.\n"
                    "- **Percent-encode** any character in the role or password that has a meaning in a URL (`@ : / ? #\n"
                    "  % &` and others). `init-secrets.sh` uses a hex password for exactly that reason. A long random\n"
                    "  password made only of letters and digits needs no encoding.\n"
                    "\n"
                    "`init-secrets.sh` writes a `db_url` pointing at `atlas-postgres`, and it refuses to overwrite an\n"
                    "existing secret. Run it first as INSTALL section 3 (document *Install* on node `atlas-system-install`)\n"
                    "describes, so it also writes the peppers and certificates, and then replace `db_url`. One way that\n"
                    "keeps the password off the command line and out of history, from `deploy/oss/`:\n"
                    "\n"
                    "```sh\n"
                    "umask 077\n"
                    "IFS= read -rs -p 'Atlas database password: ' pw; echo\n"
                    "printf 'postgresql+psycopg2://atlas_app:%s@db.internal.example:5432/atlas?sslmode=verify-full&sslrootcert=/run/secrets/db_ca\\n' \\\n"
                    "  \"$pw\" > secrets/db_url\n"
                    "unset pw\n"
                    "chmod 0644 secrets/db_url\n"
                    "```\n"
                    "\n"
                    "`read` and `printf` are shell builtins, so the password reaches no other process. Replace the role,\n"
                    "host, database and `sslmode` with yours. The file must be **`0644`** inside the `0700` `secrets/`\n"
                    "directory, as `init-secrets.sh` writes it: the containers run as a non-root UID that is not yours\n"
                    "and cannot read a `0600` file you own.\n"
                    "\n"
                    "`init-secrets.sh` also writes `secrets/db_password`. Leave it there: the compose file declares it as\n"
                    "a secret for `atlas-postgres`. It is not used to reach your database.\n"
                    "\n"
                    "Back up the new `secrets/db_url` with the rest of `secrets/` ([Operations, back up the\n"
                    "secrets](operations.md#back-up-the-secrets)).\n"
                    "\n"
                    "### TLS to the database\n"
                    "\n"
                    "The bundled database is reached over an internal Docker network and the URL it gets has no\n"
                    "`sslmode`. An external database is reached over your network, so **use TLS**, with `sslmode` in the\n"
                    "URL.\n"
                    "\n"
                    "The query parameters of the URL are passed to libpq, PostgreSQL's client library, both by the\n"
                    "application (SQLAlchemy with psycopg2) and by the backup tools (`backend/crunch_backup.py` turns\n"
                    "every query parameter except a password into the conninfo it gives `pg_dump` and `pg_restore`). So\n"
                    "the libpq parameters work as libpq documents them:\n"
                    "\n"
                    "| `sslmode` | What it gives | Works |\n"
                    "|---|---|---|\n"
                    "| `require` | encryption, but the server's certificate is **not** checked | yes |\n"
                    "| `verify-ca` | encryption, and the certificate must be signed by the CA in `sslrootcert` | yes, with the CA as below |\n"
                    "| `verify-full` | as `verify-ca`, and the certificate must name the host in the URL | yes, with the CA as below; **use this one** |\n"
                    "\n"
                    "**`verify-ca` and `verify-full` need the CA file inside the container**, named by `sslrootcert=`\n"
                    "in the URL (libpq's default, `~/.postgresql/root.crt`, does not exist in the images).\n"
                    "`external-db.yml` mounts it as the compose secret `db_ca`, at `/run/secrets/db_ca`, into\n"
                    "`atlas-migrate`, `atlas-api` and `atlas-backup`, from the host file `.env` names:\n"
                    "\n"
                    "```sh\n"
                    "cp /path/to/your-database-ca.pem secrets/db-ca.crt\n"
                    "chmod 0644 secrets/db-ca.crt\n"
                    "echo 'ATLAS_DB_CA_SOURCE=./secrets/db-ca.crt' >> .env\n"
                    "```\n"
                    "\n"
                    "The file is a PEM certificate, or several in one file (a chain, or the provider's bundle; libpq\n"
                    "accepts any of them as a trust anchor). It is not a secret, but keep it beside the others so it is\n"
                    "backed up with them. Unset, `ATLAS_DB_CA_SOURCE` is `/dev/null`: an empty mount nothing reads, which\n"
                    "is what `sslmode=require` needs. The CA is named in the URL rather than through `PGSSLROOTCERT` on\n"
                    "purpose: libpq treats a root certificate file that exists as a request to verify, and would apply\n"
                    "it to `sslmode=require` too.\n"
                    "\n"
                    "`sslrootcert=system` (libpq 16's \"use the operating system's trust store\", for a provider whose\n"
                    "certificate chains to a public CA) is not tested here; mounting the provider's CA bundle as above\n"
                    "works the same everywhere.\n"
                    "\n"
                    "**Use the host name the certificate names.** With `verify-full` the host in the URL must match the\n"
                    "certificate. A managed service names its own DNS name; connect by that, not by an IP address. The\n"
                    "containers resolve names through Docker's resolver, which forwards to the host's DNS.\n"
                    "\n"
                    "The install's own CA, `secrets/tls/ca.crt`, is not your database's CA; never point\n"
                    "`ATLAS_DB_CA_SOURCE` at it. The install smoke proves that `verify-full` refuses exactly that\n"
                    "mistake.\n"
                    "\n"
                    "Client certificates (`sslcert`, `sslkey`) need files in the container in the same way. No secret\n"
                    "for them is mounted: they are **not supported**, and password authentication (scram-sha-256) is\n"
                    "what is tested.\n"
                    "\n"
                    "## Running the install on it\n"
                    "\n"
                    "The base compose file keeps the database on the node: `atlas-migrate`, `atlas-api` and\n"
                    "`atlas-backup` are on internal networks only, and `atlas-migrate` waits for `atlas-postgres`. The\n"
                    "overlay **`deploy/oss/external-db.yml`** changes exactly that, and nothing else:\n"
                    "\n"
                    "- `atlas-postgres` moves to the profile `bundled-postgres`, which nothing turns on, so `up` never\n"
                    "  starts it; `atlas-migrate`'s `depends_on` on it is removed with `!reset`, which needs **Docker\n"
                    "  Compose 2.24.4 or later** (the same as `release.yml`);\n"
                    "- `atlas-migrate`, `atlas-api` and `atlas-backup` join the `egress` network, their route to your\n"
                    "  database server. They stay on `data` too;\n"
                    "- the database CA becomes the secret `db_ca` (see [TLS](#tls-to-the-database)).\n"
                    "\n"
                    "It publishes nothing. `atlas-mcp`, `atlas-tls` and the gateway never connect to the database and\n"
                    "are not touched.\n"
                    "\n"
                    "**The steps**, from `deploy/oss/`, after INSTALL sections 1 to 3 (document *Install* on node `atlas-system-install`) (`.env` and\n"
                    "`init-secrets.sh`) and with the database and role made as in [What the database\n"
                    "needs](#what-the-database-needs):\n"
                    "\n"
                    "1. Write `secrets/db_url` as in [The URL, in the secret file](#the-url-in-the-secret-file), with\n"
                    "   `sslmode=verify-full&sslrootcert=/run/secrets/db_ca`.\n"
                    "2. Put the database's CA at `secrets/db-ca.crt` (`0644`) and add\n"
                    "   `ATLAS_DB_CA_SOURCE=./secrets/db-ca.crt` to `.env` (see [TLS](#tls-to-the-database)).\n"
                    "3. Add the overlay to every `docker compose` command, most simply once in `.env`:\n"
                    "\n"
                    "   ```sh\n"
                    "   echo 'COMPOSE_FILE=docker-compose.yml:external-db.yml' >> .env\n"
                    "   ```\n"
                    "\n"
                    "   With other overlays, list them after it (`docker-compose.yml:external-db.yml:oidc.yml`, and\n"
                    "   `release.yml` for published images).\n"
                    "4. Check what compose will run, then start it:\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose config --services      # no atlas-postgres in the list\n"
                    "   docker compose up -d --build --wait    # atlas-migrate runs against your server first\n"
                    "   docker compose logs atlas-migrate      # \"[atlas-migrate] upgrade: schema is up to date\"\n"
                    "   ```\n"
                    "\n"
                    "   The rest of INSTALL.md (the first owner, signing in) is unchanged.\n"
                    "\n"
                    "If `atlas-migrate` fails, its log says why: `certificate verify failed` is the wrong CA file or a\n"
                    "certificate that does not name the host in the URL; `no pg_hba.conf entry` or `password\n"
                    "authentication failed` is your server's access rules or the password; a timeout is a firewall\n"
                    "between this host and the server.\n"
                    "\n"
                    "**What leaves the node.** With the overlay, those three services can open connections out of the\n"
                    "node through `egress`, as `atlas-api` already can with `retrieval.yml` or `oidc.yml`. Docker does\n"
                    "not limit them to your database's address: if your policy requires that, enforce it on the host\n"
                    "or the network (a firewall rule allowing only the database's address and port from the `egress`\n"
                    "bridge).\n"
                    "\n"
                    "**Going back** to the bundled database means a new, empty `atlas-postgres`, not your data: remove\n"
                    "the overlay from `COMPOSE_FILE`, restore `secrets/db_url` to the `atlas-postgres` form\n"
                    "`init-secrets.sh` writes, and restore a backup into it. Nothing moves data between the two for you.\n"
                    "\n"
                    "`secrets/db_password` and the `atlas-pgdata` volume are only used by `atlas-postgres`; with the\n"
                    "overlay they are declared and unused. Leave `db_password` in place, as above.\n"
                    "\n"
                    "## Backup and restore\n"
                    "\n"
                    "With an external database, **the database is backed up where it lives**. The secrets are still\n"
                    "yours to back up.\n"
                    "\n"
                    "### The database team's backups\n"
                    "\n"
                    "Use the backups, snapshots or point-in-time recovery your provider or database team already runs.\n"
                    "Agree with them how far back they keep, and how long a restore takes.\n"
                    "\n"
                    "Whatever restores the database, it **must be restored together with the `secrets/` it was made\n"
                    "with**: the two peppers in `secrets/` verify every password and token, and a restored database\n"
                    "without them signs nobody in (Operations, what to back up (document *Operations guide* on node `atlas-system-install`)). Back\n"
                    "up `secrets/` on your side whenever it changes, and keep it encrypted, off the host.\n"
                    "\n"
                    "To restore from the team's backup: stop `atlas-api` (and `atlas-backup` if its profile runs), have\n"
                    "the database restored, put back the matching `secrets/`, and start `atlas-api` again. Nothing in\n"
                    "Atlas has to run first; if the restored database is from an older release, `atlas-migrate upgrade`\n"
                    "brings its schema up to date on the next `docker compose up`. *Untested with Atlas.*\n"
                    "\n"
                    "### Atlas's own backup commands\n"
                    "\n"
                    "`atlas-admin backup run`, `backup restore` and `backup rollback` (Operations (document *Operations guide* on node `atlas-system-install`))\n"
                    "read the same `db_url` and, with `external-db.yml`, reach the server over `egress` and verify its\n"
                    "certificate with the same `db_ca`. They work against an external server **on these conditions**:\n"
                    "\n"
                    "- the role needs `CREATEDB`: every backup is verified by restoring it into a temporary database\n"
                    "  **on the same server** (`atlas_verify_...`), which is then dropped, and a restore creates a staging\n"
                    "  database and renames databases. Without `CREATEDB`, `backup run` fails at that step and says so;\n"
                    "  rely on the team's backups instead;\n"
                    "- the role must own the Atlas database (a restore creates the replacement with the same owner, and\n"
                    "  renames it);\n"
                    "- the server must be PostgreSQL 16 or older, because the image's `pg_dump` is 16;\n"
                    "- a restore connects to the server's `postgres` database (or `template1`) for its maintenance\n"
                    "  statements, and refuses while any other session is connected to the Atlas database, which on a\n"
                    "  shared server includes your team's monitoring if it connects to that database. Stop `atlas-api`\n"
                    "  first, as Operations (document *Operations guide* on node `atlas-system-install`) says;\n"
                    "- the dumps land in the `atlas-backups` volume (or `ATLAS_BACKUP_HOST_DIR`) on **this** host, not on\n"
                    "  the database server;\n"
                    "- a managed service may not let a role create databases at all, or may count them against a quota;\n"
                    "  ask before relying on these commands.\n"
                    "\n"
                    "*Untested:* a dump made with the retrieval add-on on holds `CREATE EXTENSION vector`, so restoring\n"
                    "it into the staging database needs the right to create that extension there.\n"
                    "\n"
                    "**Tested:** install smoke job `external-db`: `backup run` (pg_dump 16 over `verify-full`, verified\n"
                    "in a temporary database that is then gone), `backup restore --dry-run` changing nothing, `backup\n"
                    "restore` with every table's row count identical to before the backup and the database still owned\n"
                    "by the Atlas role, then `atlas-api` healthy on the restored database. The role there owns the\n"
                    "database and has `CREATEDB`, nothing more.\n"
                    "\n"
                    "### A plain dump against the external host\n"
                    "\n"
                    "For a copy of your own, for example before an upgrade, run `pg_dump` from any machine with a\n"
                    "PostgreSQL 16 client that can reach the server. Give it the password through a passfile, not on the\n"
                    "command line or in `PGPASSWORD`:\n"
                    "\n"
                    "```sh\n"
                    "pg_dump --format=custom --file=atlas-before-upgrade.dump \\\n"
                    "  \"host=db.internal.example port=5432 dbname=atlas user=atlas_app sslmode=verify-full \\\n"
                    "   sslrootcert=deploy/oss/secrets/db-ca.crt passfile=$HOME/.pgpass-atlas\"\n"
                    "```\n"
                    "\n"
                    "The passfile is one line, `host:port:database:user:password`, mode `0600`. Nothing checks that the\n"
                    "dump restores; restore it into a scratch database to be sure. *Untested with Atlas.*\n"
                    "\n"
                    "## Upgrades\n"
                    "\n"
                    "**Atlas upgrades** follow INSTALL section 10 (document *Install* on node `atlas-system-install`) unchanged in what they do\n"
                    "to the database: `atlas-migrate upgrade` runs on every `docker compose up`, creates whatever tables,\n"
                    "columns and indexes are missing, and never changes or deletes data. It needs the `CREATE` right on\n"
                    "`public` described above. Take your backup first; with an external database that means a fresh\n"
                    "backup or snapshot from your team, or a plain dump as above.\n"
                    "\n"
                    "Ignore the steps that are specific to `atlas-postgres`: the collation check script and the\n"
                    "`atlas-postgres` image in the release overlay do not apply to an external server. Keep\n"
                    "`external-db.yml` in `COMPOSE_FILE` across the upgrade (with `release.yml` after it, if you run\n"
                    "published images); `release.yml` still asks for `ATLAS_IMAGE_POSTGRES`, which is then pulled by\n"
                    "nothing. *Untested:* the smoke's `upgrade` job runs the bundled database only.\n"
                    "\n"
                    "**PostgreSQL upgrades** are your provider's or database team's. Atlas has nothing to run for a minor\n"
                    "version. For a major version: Atlas is tested on 16 only, and `atlas-admin backup run` stops working\n"
                    "on 17 or later because of its `pg_dump` 16. Check the collation versions after any upgrade that\n"
                    "changed the server's operating system (see [Encoding and collation](#encoding-and-collation)).\n"
                    "\n"
                    "**The database password** is rotated where the database lives. `rotate-db-password.sh` refuses a\n"
                    "`db_url` that does not point at `atlas-postgres`. Change the password on your server, write the new\n"
                    "`secrets/db_url` as in [The URL, in the secret file](#the-url-in-the-secret-file), and recreate\n"
                    "`atlas-api` (and `atlas-backup` if its profile runs) so it reads the new file.\n"
                    "\n"
                    "## The privacy page\n"
                    "\n"
                    "`.env.example` sets `ATLAS_PRIVACY_HOSTING` to \"One PostgreSQL database on this organisation's own\n"
                    "server. It is not copied to a third party.\" That sentence is shown to everyone on the privacy page.\n"
                    "If your database is a managed service, it is no longer true: say where the database is hosted and\n"
                    "who operates it.\n"
                    "\n"
                    "## What is tested\n"
                    "\n"
                    "**Tested** by the install smoke's `external-db` job (`.github/workflows/install-smoke.yml`), on\n"
                    "every pull request that touches the install. A PostgreSQL 16 that is not part of the install\n"
                    "(`tests/external_db/`: its own compose project and network, nothing published, a certificate for\n"
                    "`db.test` from a throwaway CA, plain TCP refused by its `pg_hba.conf`), a role that owns the\n"
                    "`atlas` database and has `CREATEDB`, and a stranger's install with `external-db.yml` and the URL\n"
                    "written as above, with `sslmode=verify-full&sslrootcert=/run/secrets/db_ca`:\n"
                    "\n"
                    "- the rendered shipped files pass `tools/ci/check_oss_compose.py --external-db` (S9: no\n"
                    "  `atlas-postgres`, the route out, the secrets, the file modes, no published 5432), and no\n"
                    "  `atlas-postgres` container exists;\n"
                    "- every service healthy, the `atlas-migrate` one-shot exited 0 against the external server, and\n"
                    "  `atlas-migrate upgrade` run twice more is idempotent; `/healthz` through the ingress;\n"
                    "- a write through the API (a project and its role bindings) and one through MCP, both read back\n"
                    "  through the API, and the MCP write found in the external server's own `node` table; every public\n"
                    "  table owned by the Atlas role; every connection the role holds is TLS (`pg_stat_ssl`);\n"
                    "- `verify-full` with the wrong CA (the install's own) refused with `certificate verify failed`, and\n"
                    "  the password in none of the output;\n"
                    "- backup and restore as in [Atlas's own backup commands](#atlass-own-backup-commands);\n"
                    "- the stack's bridges drop every new connection out, as in the main smoke, and the job fails if\n"
                    "  one was attempted.\n"
                    "\n"
                    "The test server is in its own compose project and on its own network, and is also attached to\n"
                    "the stack's `egress` network under the name `db.test`. That is how the stack reaches it: over\n"
                    "`egress`, the route the overlay adds, without crossing between Docker bridges, which the CI\n"
                    "runners' firewall does not allow.\n"
                    "\n"
                    "**Not tested:** a real managed service, or a server on another host across a routed network (only\n"
                    "a PostgreSQL container on the runner, reached on `egress` by a Docker DNS alias); a host firewall\n"
                    "that allows only the database's address; PostgreSQL other than 16; `sslmode=verify-ca` and `sslrootcert=system`;\n"
                    "the retrieval add-on, single sign-on or `release.yml` together with the overlay (they render and\n"
                    "pass the compose check, `oss.yml`, but are not run); upgrading an install that uses the overlay.\n"
                    "\n"
                    "What the statements above rest on, in the code:\n"
                    "\n"
                    "- the URL from `ATLAS_DB_URL_FILE`, with the plain form refused in strict mode:\n"
                    "  `backend/settings.py` (`read_secret`), `backend/db.py`;\n"
                    "- a URL with no driver read as psycopg2, by every reader of it: `backend/dsn.py`;\n"
                    "- the URL's query parameters reaching the backup tools: `backend/crunch_backup.py` (`_conninfo`);\n"
                    "- the rights the backup and restore need: the headers of `backend/crunch_backup.py` and\n"
                    "  `backend/backup_restore.py`;\n"
                    "- the schema steps: `backend/migrate_cli.py`; pgvector: `backend/retrieval/setup.py`;\n"
                    "- the networks, dependencies and secrets: `deploy/oss/docker-compose.yml`, `external-db.yml`,\n"
                    "  `retrieval.yml`, `oidc.yml`, and `tools/ci/check_oss_compose.py` (S3, S9).\n"
                ),
                "from": "docs/oss/guides/external-postgres.md",
            },
        ],
    },
    "atlas-system-retrieval": {
        "docs": [
            {
                "file": "retrieval-add-on-guide.md",
                "title": "Retrieval add-on guide",
                "kind": "runbook",
                "content": (
                    "# Retrieval add-on\n"
                    "\n"
                    "Atlas always has keyword (full-text) search. The **retrieval add-on** adds semantic search on top:\n"
                    "questions in your own words, in another language, or matching a paragraph deep inside a document.\n"
                    "It works by sending node text to an **embedding endpoint** and storing the vectors it returns in\n"
                    "Postgres.\n"
                    "\n"
                    "The add-on is optional and off by default. Atlas is complete without it, and a broken add-on never\n"
                    "stops Atlas: it reports itself as degraded or unavailable, and search falls back to keywords.\n"
                    "\n"
                    "This page covers the three modes, how to turn each one on, how Atlas authenticates to the endpoint,\n"
                    "the network requirement for a hosted provider, and **exactly which text leaves Atlas**. Every\n"
                    "variable named here is described in the configuration reference.\n"
                    "For memory, CPU and disk sizing, see Server requirements (document *Server requirements* on node `atlas-system-install`).\n"
                    "\n"
                    "## How it works\n"
                    "\n"
                    "- The add-on runs **inside `atlas-api`**. There is no separate retrieval service, and **no model\n"
                    "  ships with Atlas**: you bring the embedding endpoint.\n"
                    "- The endpoint is any **OpenAI-compatible `POST …/embeddings`** API. Atlas names no provider in its\n"
                    "  code (`backend/retrieval/embedder.py`), so a hosted service and a model on your own hardware differ\n"
                    "  only in configuration.\n"
                    "- A background loop in `atlas-api` (the reconciler, `backend/retrieval/reconciler.py`) compares what\n"
                    "  the graph holds with what is indexed, and embeds only what is new or changed. Writes to the graph\n"
                    "  never wait for it: the index can lag by up to one interval.\n"
                    "- Vectors are stored in two tables of derived data, `retrieval_source` and `retrieval_chunk`\n"
                    "  (`retrieval_chunk.embedding` is `vector(ATLAS_EMBED_DIM)`, from `backend/retrieval/setup.py`).\n"
                    "  There is no approximate (ANN) index: the scan is exact. Both tables can be rebuilt from the graph\n"
                    "  at any time, so losing them costs a reindex, not data.\n"
                    "- Search uses the vectors only when a caller asks for `mode=semantic` or `mode=hybrid` on\n"
                    "  `/api/search`, and for `/api/context` (which defaults to `hybrid`). The MCP tools\n"
                    "  `atlas_node_search` and `atlas_context` ask for `hybrid` by default.\n"
                    "\n"
                    "## The three modes\n"
                    "\n"
                    "| Mode | What it is | Text leaves the Atlas host? |\n"
                    "|---|---|---|\n"
                    "| **Off** (default) | Keyword search only. | No. |\n"
                    "| **Hosted provider** | An OpenAI-compatible embeddings API run by a cloud provider, for example an Azure OpenAI deployment in Azure AI Foundry. | Yes, to the provider, through a private endpoint (see [Network](#network-requirement-for-a-hosted-provider)). |\n"
                    "| **Local model** | An embedding model on hardware you control, behind an OpenAI-compatible API (for example Ollama, vLLM or Text Embeddings Inference), on a separate node over TLS. | It leaves the Atlas host, but stays on your network. |\n"
                    "\n"
                    "### Off\n"
                    "\n"
                    "Nothing to do: `ATLAS_RETRIEVAL` unset or `off` is the default. With the add-on off, `atlas-api`\n"
                    "reads only the switch at boot (`enabled()` in `backend/retrieval/__init__.py`), mounts no `/api/retrieval/*`\n"
                    "route, creates no table and starts no loop, and `/api/health` has no `retrieval` field. A search\n"
                    "that asks for `mode=semantic` or `hybrid` still answers, with keyword results and a `degraded` note\n"
                    "saying retrieval is off.\n"
                    "\n"
                    "The only value that stops `atlas-api` from starting is a typo in the switch itself:\n"
                    "`ATLAS_RETRIEVAL` must be `on` or `off`.\n"
                    "\n"
                    "To turn the add-on off again after using it, set `ATLAS_RETRIEVAL=off` and recreate `atlas-api` from\n"
                    "`docker-compose.yml` alone. The two retrieval tables stay in the database, unread. You can drop them\n"
                    "(`DROP TABLE retrieval_chunk; DROP TABLE retrieval_source;`) if you want the space back.\n"
                    "\n"
                    "### Turning it on: the common steps\n"
                    "\n"
                    "These apply to both the hosted and the local mode. Work in `deploy/oss/`.\n"
                    "\n"
                    "1. **Fill in the Retrieval block of `.env`** (copied from `.env.example`):\n"
                    "\n"
                    "   ```sh\n"
                    "   ATLAS_RETRIEVAL=on\n"
                    "   ATLAS_EMBED_URL=https://<endpoint>/...      # see the mode sections below\n"
                    "   ATLAS_EMBED_MODEL=<model name>\n"
                    "   ATLAS_EMBED_DIM=<the vector length the model returns>\n"
                    "   # authentication: see \"Authentication\" below\n"
                    "   ```\n"
                    "\n"
                    "   - `ATLAS_EMBED_URL` must be `https://`. Plain `http://` is accepted only to a loopback host, and\n"
                    "     inside the `atlas-api` container loopback is the container itself, so in practice every\n"
                    "     endpoint is HTTPS. A base URL ending in `/v1` gets `/embeddings` appended; a URL whose path\n"
                    "     already ends in `/embeddings` is used as given, query string included\n"
                    "     (`embeddings_url()` in `backend/retrieval/embedder.py`).\n"
                    "   - `ATLAS_EMBED_MODEL` is sent as `model` in every request and stored on every vector. Changing it\n"
                    "     re-embeds everything.\n"
                    "   - `ATLAS_EMBED_DIM` must equal the length of the vectors the endpoint returns; a mismatch is\n"
                    "     reported, never stored. pgvector's limit is 16,000.\n"
                    "   - `ATLAS_EMBED_DOC_PREFIX` and `ATLAS_EMBED_QUERY_PREFIX` are prepended verbatim to every indexed\n"
                    "     text and every search query. Set them only if your model expects them (for example\n"
                    "     `nomic-embed-text` expects `search_document: ` and `search_query: `, trailing space included).\n"
                    "     Changing the document prefix re-embeds everything.\n"
                    "\n"
                    "2. **Check the configuration** with the one-shot check under the `retrieval` profile. It reads the\n"
                    "   same variables, refuses what cannot be right (an unknown switch value, plain `http://` off\n"
                    "   loopback, a key in a plain variable), never contacts the endpoint and never opens the key file\n"
                    "   (`deploy/oss/retrieval_check.py`):\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose --profile retrieval run --rm atlas-retrieval\n"
                    "   ```\n"
                    "\n"
                    "3. **Start `atlas-api` with the overlay** `retrieval.yml`:\n"
                    "\n"
                    "   ```sh\n"
                    "   docker compose -f docker-compose.yml -f retrieval.yml up -d\n"
                    "   ```\n"
                    "\n"
                    "   The overlay adds two things and nothing else: `atlas-api` joins the `egress` network, its only\n"
                    "   route out of the host, and receives the key file as the compose secret `embed_key`\n"
                    "   (`./secrets/embed_key`, mounted at `/run/secrets/embed_key`). It also gives `atlas-api` the\n"
                    "   install CA as `/run/secrets/atlas_ca`, for an endpoint whose certificate that CA issued. The base\n"
                    "   install has neither the route nor the secret. Keep using both `-f` files for every later\n"
                    "   `docker compose` command on this install, or `atlas-api` goes back to the base file.\n"
                    "\n"
                    "4. **Check that it is working.** `GET /api/health` now carries a `retrieval` field:\n"
                    "\n"
                    "   | Value | Meaning |\n"
                    "   |---|---|\n"
                    "   | `on` | Configured, storage ready, the last indexing pass succeeded. |\n"
                    "   | `degraded: <last error>` | Configured, but the last pass could not reach or use the endpoint. The index is stale, not wrong; the loop retries with a growing wait (up to 10 minutes). |\n"
                    "   | `unavailable: <reason>` | A configuration or storage problem (no endpoint, a key in a plain variable, a dimension that does not match the stored vectors, no pgvector). No indexing runs. |\n"
                    "\n"
                    "   All three are informational: none of them fails a health check or a request. An owner can see\n"
                    "   more at `GET /api/retrieval/status`: the state, the endpoint's host (never the key or a path's\n"
                    "   contents), the model and dimension, the last pass, and how many sources and chunks are indexed or\n"
                    "   still pending. The first pass runs one interval (`ATLAS_RETRIEVAL_INTERVAL_S`, default 60 s)\n"
                    "   after `atlas-api` starts.\n"
                    "\n"
                    "**When the endpoint is down**, `/api/health` reports `degraded: …`, and searches still answer\n"
                    "HTTP 200: `hybrid` returns the keyword results and `semantic` returns none, each with a `degraded`\n"
                    "field that says why. The install smoke checks exactly this in leg `8b-retrieval`\n"
                    "(`.github/workflows/install-smoke.yml`): it indexes against a stub endpoint, stops the stub, and\n"
                    "checks that search through the API and MCP still answers 200.\n"
                    "\n"
                    "**Changing the dimension** (a new model with a different vector length) needs the tables\n"
                    "recreated. An owner calls `POST /api/retrieval/reindex` with `{\"rebuild\": true}`; the next pass\n"
                    "re-embeds everything. Without `rebuild`, the same route re-embeds everything at the current\n"
                    "dimension.\n"
                    "\n"
                    "### Hosted provider (Azure AI Foundry)\n"
                    "\n"
                    "An Azure OpenAI embedding deployment in Azure AI Foundry, reached through a private endpoint:\n"
                    "\n"
                    "```sh\n"
                    "ATLAS_RETRIEVAL=on\n"
                    "ATLAS_EMBED_URL=https://<resource>.openai.azure.com/openai/deployments/<deployment>/embeddings?api-version=2024-10-21\n"
                    "ATLAS_EMBED_MODEL=text-embedding-3-small\n"
                    "ATLAS_EMBED_DIM=1536\n"
                    "# then either a key file:\n"
                    "ATLAS_EMBED_AUTH_HEADER=api-key\n"
                    "ATLAS_EMBED_KEY_FILE=/run/secrets/embed_key\n"
                    "# or Entra ID (no key file; see \"Entra ID\" below):\n"
                    "# ATLAS_EMBED_AUTH_HEADER=entra\n"
                    "# ATLAS_EMBED_KEY_SOURCE=/dev/null\n"
                    "```\n"
                    "\n"
                    "The full deployment URL, `?api-version=` included, is used as given. Azure selects the model by\n"
                    "the deployment in the URL; `ATLAS_EMBED_MODEL` is still sent and is what Atlas records on each\n"
                    "vector, so name the deployed model there. Any other OpenAI-compatible hosted API works the same way\n"
                    "with its own base URL (`https://<host>/v1`) and, usually, `ATLAS_EMBED_AUTH_HEADER=bearer`.\n"
                    "\n"
                    "Leave `ATLAS_EMBED_SCOPE` at its default `shared` unless you have decided that private projects may\n"
                    "be sent to this provider (see [What is sent](#exactly-which-text-is-sent)).\n"
                    "\n"
                    "### Local model\n"
                    "\n"
                    "A model on hardware you control, behind any server that answers OpenAI-compatible\n"
                    "`POST /v1/embeddings`. Run it on a **separate node, reached over TLS**, as\n"
                    "Server requirements (document *Server requirements* on node `atlas-system-install`) recommends: a local embedding model\n"
                    "needs its own memory and usually a GPU, sized from that model's documentation.\n"
                    "\n"
                    "```sh\n"
                    "ATLAS_RETRIEVAL=on\n"
                    "ATLAS_EMBED_URL=https://embed.example.com/v1\n"
                    "ATLAS_EMBED_MODEL=<the model name your server expects>\n"
                    "ATLAS_EMBED_DIM=<its vector length>\n"
                    "ATLAS_EMBED_CA_BUNDLE=/run/secrets/atlas_ca     # only for a certificate from a private CA\n"
                    "# a key file only if the server or a proxy in front of it asks for one:\n"
                    "# ATLAS_EMBED_AUTH_HEADER=bearer\n"
                    "# ATLAS_EMBED_KEY_FILE=/run/secrets/embed_key\n"
                    "```\n"
                    "\n"
                    "- Most model servers speak plain HTTP. Put a TLS proxy in front of the model on its node, with a\n"
                    "  certificate from your PKI or from this install's CA. Atlas verifies the certificate against the\n"
                    "  system trust store, or against the PEM bundle in `ATLAS_EMBED_CA_BUNDLE`. There is no setting that\n"
                    "  turns verification off.\n"
                    "- `ATLAS_EMBED_DIM` is the model's vector length (for example 768 for `nomic-embed-text`, 1024 for\n"
                    "  `bge-m3`). Check your model's documentation.\n"
                    "- Because the text stays on your network, you may decide that `ATLAS_EMBED_SCOPE=all` is acceptable\n"
                    "  here. That is your decision; the default stays `shared`.\n"
                    "\n"
                    "## Authentication\n"
                    "\n"
                    "`ATLAS_EMBED_AUTH_HEADER` chooses how Atlas proves itself to the endpoint (`_headers()` in\n"
                    "`backend/retrieval/embedder.py`):\n"
                    "\n"
                    "| Value | Header sent | Credential |\n"
                    "|---|---|---|\n"
                    "| `bearer` (default) | `Authorization: Bearer <key>` | the key file |\n"
                    "| `api-key` | `api-key: <key>` (Azure OpenAI's header) | the key file |\n"
                    "| `entra` | `Authorization: Bearer <Entra ID token>` | the identity the container runs as; **no key file** |\n"
                    "\n"
                    "With `bearer` and no `ATLAS_EMBED_KEY_FILE`, no authorization header is sent, for an endpoint that\n"
                    "needs none.\n"
                    "\n"
                    "### Key file\n"
                    "\n"
                    "- **The key is only ever a file.** Write it to `./secrets/embed_key` (the overlay mounts it as\n"
                    "  `/run/secrets/embed_key`) and set `ATLAS_EMBED_KEY_FILE=/run/secrets/embed_key`. A key given as a\n"
                    "  plain `ATLAS_EMBED_KEY` variable makes the add-on `unavailable`, and the configuration check\n"
                    "  refuses it.\n"
                    "- The file is read on **every** call, never cached. Rotating the key is replacing the file; no\n"
                    "  restart is needed. Surrounding whitespace is stripped; an empty or unreadable file fails the call\n"
                    "  with a message that names the path, never the contents.\n"
                    "- Like the other secrets in `./secrets/`, the file must be readable by the container's user. The\n"
                    "  hardened image runs under an arbitrary UID in group 0, which is why `init-secrets.sh` writes\n"
                    "  secret files `0644` inside the `0700` directory. Do the same:\n"
                    "  `chmod 0644 secrets/embed_key`.\n"
                    "- The key appears in one request header and nowhere else: not in a log line, an error message or\n"
                    "  `/api/retrieval/status`.\n"
                    "\n"
                    "### Entra ID: managed identity or workload identity\n"
                    "\n"
                    "For an Azure OpenAI / Azure AI Foundry resource that accepts only Microsoft Entra ID tokens (key\n"
                    "authentication disabled), set `ATLAS_EMBED_AUTH_HEADER=entra` and leave `ATLAS_EMBED_KEY_FILE`\n"
                    "unset; setting both makes the add-on `unavailable`. Grant the identity a data-plane role on the\n"
                    "resource that allows inference, such as *Cognitive Services OpenAI User*.\n"
                    "\n"
                    "Atlas gets the token from the identity the process already runs as (`backend/retrieval/entra.py`),\n"
                    "trying the same sources, in the same order, as Azure's `DefaultAzureCredential`:\n"
                    "\n"
                    "1. **Workload identity** (AKS and other federated workloads): when `AZURE_FEDERATED_TOKEN_FILE`,\n"
                    "   `AZURE_CLIENT_ID` and `AZURE_TENANT_ID` are set, the federated token file is exchanged at\n"
                    "   `AZURE_AUTHORITY_HOST` (default `https://login.microsoftonline.com/`). The file is read again on\n"
                    "   every exchange, since the platform rotates it.\n"
                    "2. **Managed identity on App Service or Container Apps**: when `IDENTITY_ENDPOINT` and\n"
                    "   `IDENTITY_HEADER` are set (the platform sets them).\n"
                    "3. **Managed identity on a VM or VM scale set**: the instance metadata service at\n"
                    "   `169.254.169.254`. With more than one user-assigned identity, set `AZURE_CLIENT_ID` to choose\n"
                    "   one.\n"
                    "\n"
                    "The token scope is `https://cognitiveservices.azure.com/.default`; set `ATLAS_EMBED_ENTRA_SCOPE`\n"
                    "for a sovereign cloud. The token is cached in memory and fetched again five minutes before it\n"
                    "expires. It is never logged, never written to disk and never put in an error message.\n"
                    "\n"
                    "On the compose install:\n"
                    "\n"
                    "- **No key file.** Set `ATLAS_EMBED_KEY_SOURCE=/dev/null` in `.env`. `retrieval.yml` makes the\n"
                    "  `embed_key` secret from that host file instead of `./secrets/embed_key`, so the secret is an empty\n"
                    "  mount that nothing reads and `./secrets/embed_key` does not have to exist. Leave\n"
                    "  `ATLAS_EMBED_KEY_FILE` unset.\n"
                    "- **Managed identity** needs nothing more: `atlas-api` reaches the identity endpoint through the\n"
                    "  `egress` network that `retrieval.yml` gives it.\n"
                    "- **Workload identity** needs `AZURE_TENANT_ID` and `AZURE_CLIENT_ID` in `.env` (they reach\n"
                    "  `atlas-api` through `env_file`) and the federated token file inside the container. The shipped\n"
                    "  overlay `retrieval-workload-identity.yml` mounts the **directory** that holds the token,\n"
                    "  read-only, at `/var/run/secrets/azure/tokens`, and sets `AZURE_FEDERATED_TOKEN_FILE` to the file\n"
                    "  in it. It mounts the directory rather than the file because a rotation that replaces the file\n"
                    "  would leave a single-file mount showing the old token. Name the host directory in `.env`:\n"
                    "\n"
                    "  ```sh\n"
                    "  ATLAS_EMBED_AUTH_HEADER=entra\n"
                    "  ATLAS_EMBED_KEY_SOURCE=/dev/null\n"
                    "  AZURE_TENANT_ID=<tenant id>\n"
                    "  AZURE_CLIENT_ID=<the federated identity's client id>\n"
                    "  AZURE_FEDERATED_TOKEN_DIR=/path/on/the/host/to/the/token/directory\n"
                    "  # AZURE_FEDERATED_TOKEN_NAME=azure-identity-token   # if the file has another name\n"
                    "  ```\n"
                    "\n"
                    "  and start with all three files:\n"
                    "\n"
                    "  ```sh\n"
                    "  docker compose -f docker-compose.yml -f retrieval.yml -f retrieval-workload-identity.yml up -d\n"
                    "  ```\n"
                    "\n"
                    "  The container's user is not yours, so the directory must be readable (`0755`) and the file too\n"
                    "  (`0644`).\n"
                    "\n"
                    "The install smoke's leg 8c runs this setup with no `./secrets/embed_key` at all: the token exchange\n"
                    "with the mounted federated token, then the index filling with Entra tokens.\n"
                    "\n"
                    "## Network requirement for a hosted provider\n"
                    "\n"
                    "**Reach a hosted provider through a private endpoint, not over the public internet.** For Azure AI\n"
                    "Foundry / Azure OpenAI that means:\n"
                    "\n"
                    "- a **private endpoint** (Azure Private Link) for the resource, in a virtual network the Atlas host\n"
                    "  can route to (the same VNet, a peered one, or over VPN or ExpressRoute from your site);\n"
                    "- **public network access disabled** on the resource, so the private endpoint is the only way in;\n"
                    "- **private DNS** for the resource's host name (the `privatelink` zone linked to the Atlas host's\n"
                    "  network), so the normal host name in `ATLAS_EMBED_URL` resolves to the private address and the\n"
                    "  TLS certificate still matches.\n"
                    "\n"
                    "Atlas cannot check this for you. It enforces HTTPS with certificate verification, and the overlay\n"
                    "gives `atlas-api` exactly one route out (the `egress` network), but it cannot tell a private address\n"
                    "from a public one. Before turning the add-on on, confirm from the Atlas host that the endpoint's\n"
                    "host name resolves to a private address.\n"
                    "\n"
                    "Allow the Atlas host outbound HTTPS to that endpoint only. With `entra`, also allow the token source\n"
                    "the identity uses: the instance metadata service (`169.254.169.254`, on the VM itself), the platform's\n"
                    "identity endpoint, or `login.microsoftonline.com` for workload identity.\n"
                    "\n"
                    "A local model on a separate node needs the same thing on a smaller scale: a route from the Atlas\n"
                    "host to that node, and TLS on it.\n"
                    "\n"
                    "## Exactly which text is sent\n"
                    "\n"
                    "This section is taken from the code, not from other documentation. File and function names are\n"
                    "given so you can check it.\n"
                    "\n"
                    "### The request\n"
                    "\n"
                    "Every call is one `POST` to the embeddings URL with this JSON body and nothing else\n"
                    "(`embed()` in `backend/retrieval/embedder.py`):\n"
                    "\n"
                    "```json\n"
                    "{\"model\": \"<ATLAS_EMBED_MODEL>\", \"input\": [\"<prefix><text 1>\", \"<prefix><text 2>\", \"…\"]}\n"
                    "```\n"
                    "\n"
                    "plus the authentication header above. No node id, user name, account or token of the person\n"
                    "searching is part of the request.\n"
                    "\n"
                    "### What is sent for indexing\n"
                    "\n"
                    "The reconciler (`collect_sources()` in `backend/retrieval/reconciler.py`) indexes two kinds of\n"
                    "source, and only for nodes whose type is in the `object` or `bridge` family (`WORLD_FAMILIES`): the\n"
                    "world nodes, not work items.\n"
                    "\n"
                    "**1. The node card**, one per world node (`node_card()` in `backend/retrieval/chunking.py`):\n"
                    "\n"
                    "```text\n"
                    "<node name> [<node type>]\n"
                    "\n"
                    "<summary>\n"
                    "\n"
                    "<abstract>\n"
                    "\n"
                    "<detail>\n"
                    "```\n"
                    "\n"
                    "Empty fields are left out. A card is one text when it is at most about 1,500 tokens\n"
                    "(`CARD_MAX_TOKENS`). A longer card is split between paragraphs into texts of up to about 1,500\n"
                    "tokens, each starting again with the `<name> [<type>]` line, with no overlap.\n"
                    "\n"
                    "**2. Each served document** of a world node (`doc_chunks()` in `backend/retrieval/chunking.py`). A\n"
                    "document is *served* when its path is listed in the node's `docs[]` and not marked `external`\n"
                    "(`models_graph.NODE_DOC_SERVED_SQL`, the same rule that decides whether `GET /api/node/{id}/doc`\n"
                    "returns it). The document is split:\n"
                    "\n"
                    "- on markdown headings, then between paragraphs, into texts of roughly 300 to 600 tokens\n"
                    "  (`DOC_MIN_TOKENS`, `DOC_MAX_TOKENS`). Sections shorter than 300 tokens are merged with the next\n"
                    "  section under the same top-level heading;\n"
                    "- the last paragraph of one text is repeated at the start of the next when it is shorter than\n"
                    "  300 tokens, so a passage is not cut off from its context;\n"
                    "- each text starts with a line `<node name> › <document title> › <heading path>`. The title is the\n"
                    "  `title` of the `docs[]` entry, or the path when there is none;\n"
                    "- every fenced block (code, mermaid and so on) is **replaced** by the line\n"
                    "  `[<label> block omitted]`. Its contents are never sent.\n"
                    "\n"
                    "Tokens are estimated as characters divided by 4 (`tokens()`); the bounds are soft targets. A single\n"
                    "paragraph longer than the bound becomes one text on its own and is never cut mid-sentence, so a text\n"
                    "can be longer than the bound.\n"
                    "\n"
                    "`ATLAS_EMBED_DOC_PREFIX` is prepended to every one of these texts.\n"
                    "\n"
                    "### What is sent for a search\n"
                    "\n"
                    "When someone searches with `mode=semantic` or `mode=hybrid` (including `/api/context`, and the MCP\n"
                    "tools `atlas_node_search` and `atlas_context`, which default to `hybrid`), **the search text itself**\n"
                    "is sent as a single input, with `ATLAS_EMBED_QUERY_PREFIX` in front (`vector_list()` in\n"
                    "`backend/retrieval/query.py`). This happens whatever `ATLAS_EMBED_SCOPE` says: the scope controls\n"
                    "the indexed text, not what people type into search. A search with `mode=fulltext`, or with no\n"
                    "`mode`, sends nothing.\n"
                    "\n"
                    "### When\n"
                    "\n"
                    "- The loop's first pass runs one `ATLAS_RETRIEVAL_INTERVAL_S` (default 60 s) after `atlas-api`\n"
                    "  starts, then one pass per interval (`backend/retrieval/service.py`). After a failed pass the wait\n"
                    "  doubles, up to 10 minutes, and resets after a success.\n"
                    "- A pass sends only the sources whose content hash changed (`source_hash()`): a new or edited node\n"
                    "  card, a new or edited served document. The hash covers the chunk texts, the model, the document\n"
                    "  prefix and the chunker version, so changing the model or the document prefix sends everything\n"
                    "  again. So does an owner's `POST /api/retrieval/reindex`.\n"
                    "- Texts are sent at most `ATLAS_EMBED_BATCH` (default 32) per request. Whole sources are grouped\n"
                    "  into a request; a source with more texts than a batch is sent over several requests.\n"
                    "- Writing to the graph never sends anything by itself. Booting, `/api/health` and\n"
                    "  `/api/retrieval/status` never call the endpoint.\n"
                    "\n"
                    "### `ATLAS_EMBED_SCOPE`: private projects\n"
                    "\n"
                    "- **`shared`** (the default): the text of a node is sent only if a person holding **no grant at\n"
                    "  all** could see it, that is, the node is not marked private and is not under a node that is\n"
                    "  (`in_scope()` in `backend/retrieval/reconciler.py`, using the same walk as Atlas's own visibility\n"
                    "  check). Private projects are then found by keyword search only. An unknown value is treated as\n"
                    "  `shared`, so a typo never widens what is sent.\n"
                    "- **`all`**: every world node and served document is sent, private ones included.\n"
                    "\n"
                    "Search results are filtered by the caller's own visibility either way. What is indexed and who may\n"
                    "see a result are two separate controls.\n"
                    "\n"
                    "When a node becomes private, or a document stops being served, the next pass deletes its vectors\n"
                    "from Atlas. Text that was already sent to the provider cannot be called back from Atlas; that is\n"
                    "the provider's retention policy.\n"
                    "\n"
                    "### What is never sent\n"
                    "\n"
                    "- **Work items** (tasks, stories, epics and every other `workitem`-family node), and so their\n"
                    "  acceptance criteria and results.\n"
                    "- A node's **`body`** field, its **attributes** (other than the `docs[]` titles used in the chunk\n"
                    "  header line), its **id**, its **edges** and its **intents**.\n"
                    "- **Stored documents that are not served**: no `docs[]` entry, or marked `external`.\n"
                    "- **The contents of fenced code and diagram blocks** in documents.\n"
                    "- Under `ATLAS_EMBED_SCOPE=shared`, **anything private** or under a private node.\n"
                    "- Accounts, sessions, tokens, groups, grants, audit events and the graph's event history: the\n"
                    "  reconciler reads only `node`, `node_type`, `node_doc`, the private marks and the edges the\n"
                    "  visibility walk follows.\n"
                    "- The embedding key and any Entra token, other than as the request's authentication header.\n"
                    "\n"
                    "### What Atlas stores\n"
                    "\n"
                    "Each chunk's text and its vector are stored in `retrieval_chunk`, in your own Postgres, next to the\n"
                    "node and document they came from (`_write_source()` in `backend/retrieval/reconciler.py`). The\n"
                    "vector costs about 4 bytes per dimension per chunk; see\n"
                    "Server requirements (document *Server requirements* on node `atlas-system-install`) for the disk estimate.\n"
                    "\n"
                    "## Tuning\n"
                    "\n"
                    "| Variable | Default | Effect |\n"
                    "|---|---|---|\n"
                    "| `ATLAS_RETRIEVAL_INTERVAL_S` | `60` | Seconds between indexing passes. Lower means fresher results and more frequent calls. |\n"
                    "| `ATLAS_EMBED_BATCH` | `32` | Texts per embeddings request. Lower it if the endpoint limits the request size. |\n"
                    "| `ATLAS_RETRIEVAL_TOP_K` | `30` | How many nodes the vector search contributes to a `semantic` or `hybrid` search (1 to 200). |\n"
                    "\n"
                    "`hybrid` merges the keyword ranking and the vector ranking by Reciprocal Rank Fusion (k = 60), at\n"
                    "node level (`fuse()` in `backend/retrieval/query.py`). See the\n"
                    "configuration reference for every retrieval variable.\n"
                ),
                "from": "docs/oss/guides/retrieval.md",
            },
        ],
        "setting": "ATLAS_RETRIEVAL=on",
    },
}

COMPOSE = {
    "atlas-postgres": "atlas-system-postgres",
    "atlas-migrate": "atlas-system-migrate",
    "atlas-api": "atlas-system-api",
    "atlas-mcp": "atlas-system-mcp",
    "atlas-tls": "atlas-system-tls",
    "atlas-retrieval": "atlas-system-retrieval-check",
    "atlas-gateway": "atlas-system-gateway",
    "atlas-backup": "atlas-system-backup",
}
