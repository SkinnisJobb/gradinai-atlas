"""The Atlas system node set and its handbook (STORY-atlas-system-node-handbook). THE SOURCE.

Every install can describe itself: a `system` node `atlas-system` with one child per part, whose
node documents are the handbook. This module is the one place that text is written. It ships the
way seed content ships today, as Python data beside `demo_seed.py`, and has three readers:

  system_node.py       seeds the set into a fresh graph (node, `docs[]`, `node_doc`, `contains`).
                       Data in a module, not a file read at boot: boot opens no file
                       (backend/CLAUDE.md, "Boot is a read").
  tools/docs_site.py   renders the static /docs site into frontend/docs/ (generated, committed;
                       `--check` fails CI when it is stale against this module).
  auth.py              makes the site's exact paths public when ATLAS_PUBLIC_DOCS=1 (ADR 0047).

So the board, an agent's `atlas_doc_read` and `/docs` all read one text. Edit a document here,
then run `python tools/docs_site.py` and commit both.

WHY NOT `docs/nodes/<id>/*.md`. That directory was retired by TASK-1059: an install's node
documents live in Postgres (`node_doc`, ADR 0029) and are written through the API. The `path`
each document gets keeps that directory's name only because it is the key `node_doc` stores and
the prefix `GET /api/node/{id}/doc` serves (`routers/docs.DOCS_PREFIX`); there is no file at it.

IDS. Every id starts with `atlas-system`, so it cannot meet an install's own nodes (a homelab
already has `atlas`, `atlas-api`, `atlas-mcp`, `atlas-postgres`, `atlas-retrieval`).

CONTENT. Generic: no host, no address, no person; the scrub gate runs in enforce mode over this
file and frontend/docs/. A link between handbook pages is written as its site URL
(`/docs/<id>/<page>.html`), which works on the board and on the site alike. Every page is a
SKELETON for now (short and correct); STORY-oss-20 writes the full handbook.
"""
from __future__ import annotations

ROOT = "atlas-system"

#: The node set in seeding order, the root first. Each document: `file` (its page is
#: `<id>/<stem>.html`, its node_doc path `docs/nodes/<id>/<file>`), `title`, `kind`, `content`.
NODES = (
    {
        "id": "atlas-system", "type": "system", "parent": None,
        "name": "Atlas",
        "summary": "This Atlas install, described by itself: one child per part, and the handbook as its documents.",
        "docs": (
            {"file": "concepts.md", "title": "Concepts", "kind": "architecture",
             "content": """\
# Concepts

> Skeleton page. The full handbook is written in a later pass; what is here is short and correct.

Atlas keeps one **graph** in Postgres: **nodes** joined by typed **edges**. Everything the board
shows and every tool an agent calls reads and writes that graph through the same API.

## Two families of node

- **World nodes** describe what exists: an `org`, a `project`, a `system`, a `service`, a `db`, a
  `host`, a `component`, a `capability`, an `agent`. They are arranged by `contains` edges.
- **Workitems** describe what is being done: an `epic` contains `story` items, and a story
  contains `task` items. A workitem is attached to the world by a `works_on` edge, never by
  `contains`.

Work rolls **up**: a `system` node is usually not worked on directly; the work bound to its
children shows on it by containment.

## What a node carries

- A `summary`, an `abstract` and a `detail`: the short to long description of the node.
- **Documents**: markdown pages stored with the node and listed in its `docs` attribute. This
  handbook is the documents of the `atlas-system` node and its children.
- **Intents**: durable statements of purpose (a constraint, a principle, a non-goal or an
  outcome) that bind the node and everything below it.

## Where you see it

- `/board` is the board: a graph of the world, a work board scoped by it, and a node panel.
- `/docs` is this handbook as a static site, generated from the same documents.
- Agents reach the same graph through the MCP server; see [Agents](/docs/atlas-system/agents.html).
"""},
            {"file": "configuration.md", "title": "Configuration", "kind": "runbook",
             "content": """\
# Configuration

> Skeleton page. The full handbook is written in a later pass; what is here is short and correct.

Atlas is configured by environment variables. An install made from `deploy/oss` keeps them in
one `.env` file, copied from `deploy/oss/.env.example`, whose comments explain every setting.

## Secrets are files

Credentials are never plain environment values. Each one is a file under `deploy/oss/secrets/`
(written by `scripts/init-secrets.sh`), and the setting names the file: `ATLAS_DB_URL_FILE`,
`ATLAS_PASSWORD_PEPPER_FILE`, `ATLAS_TOKEN_PEPPER_FILE`. The two peppers are fixed for the life of
the install: a new pepper invalidates every password and every token at once.

## The settings you choose first

- `ATLAS_PUBLIC_URL`: the address people and agents use.
- `ATLAS_SEED`: what an empty database starts with. `none` is an empty instance, `demo` adds
  one example project, and `self` adds Atlas's own world model (its parts, their documents and
  a set of intents, with no work); all three add this system node and its handbook.
- `ATLAS_LEGACY_V0`: `0` leaves out the frozen first-generation tables and routes.
- `ATLAS_RBAC`: `projects` puts roles on projects, with private projects by default.
- `ATLAS_PUBLIC_DOCS`: `1` serves `/docs` without sign-in; see [Security](/docs/atlas-system/security.html).
- `ATLAS_DISABLED_FEATURES`: switches optional features off by name.

A malformed setting stops the API at start-up with a message that names the variable.
"""},
            {"file": "operations.md", "title": "Operations", "kind": "runbook",
             "content": """\
# Operations

> Skeleton page. The full handbook is written in a later pass; what is here is short and correct.

## Install and start

In `deploy/oss`, copy `.env.example` to `.env` and fill it in. Run `scripts/init-secrets.sh` and
`scripts/init-certs.sh`, then `docker compose up -d`. The `atlas-migrate` one-shot prepares the
database before `atlas-api` starts.

## The first owner

```sh
docker compose exec atlas-api atlas-admin user add <name> --role owner
```

The command prints a temporary password once, and the first sign-in forces a change. Further
people are added the same way with `--role member`.

## Upgrade

Pull the new release, rebuild and start again. `atlas-migrate upgrade` applies schema changes
and is safe to run more than once:

```sh
docker compose exec atlas-api atlas-migrate upgrade
```

## Health

`/healthz` answers without a token or a database, and is what container health checks use.
`/api/health` needs a signed-in caller and reports the database state.

## Back up

Back up the Postgres volume **and** `deploy/oss/secrets/` together. A database restored without
its peppers has no working password or token.
"""},
            {"file": "security.md", "title": "Security", "kind": "design",
             "content": """\
# Security

> Skeleton page. The full handbook is written in a later pass; what is here is short and correct.

## One gate, deny by default

Every request to `atlas-api` passes one authorization middleware. A route is closed unless it is
on a short, fixed list of public paths: the landing page, sign-in, the privacy notice, the
liveness probes, and the pages of `/docs` when `ATLAS_PUBLIC_DOCS=1`. A machine caller gets
`401`, never a redirect to a login page.

## People and agents

- People sign in with a password, or with single sign-on through OpenID Connect.
- Agents use **their own** bearer token, minted with `atlas-admin` or under Settings. A token
  carries scopes (`read`, `write`, `admin`) and is linked to the person accountable for it.
- With `ATLAS_RBAC=projects`, a project is visible only to the people and groups bound to it. A
  hidden node and a missing one answer the same `404`.

## The public handbook

`/docs` serves only the files generated from this handbook into the image: a fixed list of
pages read from disk, with no database access and nothing a user can edit. The documents stored
on these nodes in the database are separate copies; editing them changes the board, not `/docs`.

## At the edge

The ingress in `deploy/oss` terminates TLS and sets the browser security headers, including a
Content-Security-Policy that allows no inline script.

## Reporting a problem

See `SECURITY.md` in the repository.
"""},
            {"file": "agents.md", "title": "Agents", "kind": "integration",
             "content": """\
# Agents

> Skeleton page. The full handbook is written in a later pass; what is here is short and correct.

Agents work with Atlas through **atlas-mcp**, an MCP server at `/mcp/` on the install's public
address. It holds no data of its own: every tool call becomes a call to `atlas-api` made with the
agent's own token, so the agent sees exactly what its token may see and every write is
attributed to it.

## Connect an agent

1. Get a token: an operator runs `atlas-admin token mint` for you, or you create one under
   Settings, Agent credentials.
2. Keep the token in a file only you can read. Never put it on a command line or in an
   environment variable.
3. Point your MCP client at `/mcp/` with an `Authorization: Bearer` header read from that file.
   `clients/claude-code/` in the repository is a ready pack for Claude Code.

## The tools, in the order to use them

- Find: `atlas_node_search`, `atlas_search`, `atlas_outline`, `atlas_tree`.
- Read: `atlas_get`, `atlas_structure`, `atlas_intents`, `atlas_doc_read`.
- Write: `atlas_file_work`, `atlas_update_work`, `atlas_node_write`, `atlas_doc_write`.

Read the smallest thing that answers the question first. A full document read is the last step,
not the first.
"""},
        ),
    },
    {
        "id": "atlas-system-api", "type": "service", "parent": "atlas-system",
        "name": "atlas-api",
        "summary": "The API and the board: one FastAPI process behind one authorization gate.",
        "docs": (
            {"file": "overview.md", "title": "atlas-api", "kind": "architecture",
             "content": """\
# atlas-api

> Skeleton page. The full handbook is written in a later pass; what is here is short and correct.

`atlas-api` is one FastAPI process. It serves the JSON API under `/api`, the board at `/board`,
the sign-in pages under `/auth`, and this handbook at `/docs`.

Every route sits behind one authorization middleware that is deny-by-default; see
[Security](/docs/atlas-system/security.html). The process keeps no state of its own: everything
is in `atlas-postgres`, so it can be restarted at any time.

The image also carries two command-line tools: `atlas-admin` (people, agents and tokens) and
`atlas-migrate` (schema upgrades).
"""},
            {"file": "crunch-week.md", "title": "Crunch week", "kind": "runbook",
             "content": """\
# Crunch week

> Skeleton page. The full handbook is written in a later pass; what is here is short and correct.
> The full guide is `docs/oss/guides/crunch-week.md` in the Atlas repository.

Crunch week is a release mode for a set time. While it is on, a change that passes CI deploys to
production without the staging and pre-production steps chosen from the install's guardrail
matrix. It is off by default, and it ends by itself at the end time.

## Who can turn it on

Only the instance owner, in a browser session, entering their password on every change. The
control is a tab in Settings. No token, whatever its scopes, and no MCP tool can read or change it.

## What you set

- **When:** start now or at a scheduled time, and an end time or a length. The server's clock
  decides; at most 31 days.
- **Backups:** none (the default), once at the start, before every production deploy, hourly or
  daily, with a retention in days. Each backup is restored into a temporary database and counted
  before it counts as a backup.
- **Skipped environments:** non-production environments of the guardrail matrix in force. With
  no matrix applied, nothing is skipped.

## What still holds

- CI is still required, and production keeps the gate the matrix gives it.
- Agents never hold deploy rights in production.
- Atlas deploys nothing itself: you generate the crunch variant of the pipeline with
  `atlas-admin cicd generate --crunch ...`, commit it, and generate again without it afterwards.

## What it records

Activating, changing, extending and ending, by hand or by expiry, each write one graph event and
one Accepted ADR on the `atlas-system` node. While it is on, every signed-in person sees a banner
on the board saying until when.
"""},
        ),
    },
    {
        "id": "atlas-system-mcp", "type": "service", "parent": "atlas-system",
        "name": "atlas-mcp",
        "summary": "The MCP server agents connect to; it holds no data and calls atlas-api with the agent's own token.",
        "docs": (
            {"file": "overview.md", "title": "atlas-mcp", "kind": "architecture",
             "content": """\
# atlas-mcp

> Skeleton page. The full handbook is written in a later pass; what is here is short and correct.

`atlas-mcp` is the Model Context Protocol server agents connect to, at `/mcp/`. It is a
translator, not a store: each tool call is forwarded to `atlas-api` with the calling agent's own
bearer token, and the answer comes back in a fixed envelope.

Because it holds no data and no credential of its own, an agent can do through it exactly what
its token allows on the API. The tools, and the order to use them in, are on the
[Agents](/docs/atlas-system/agents.html) page.
"""},
        ),
    },
    {
        "id": "atlas-system-postgres", "type": "db", "parent": "atlas-system",
        "name": "atlas-postgres",
        "summary": "The one database: the graph, the work, the documents, accounts and the audit trail.",
        "docs": (
            {"file": "overview.md", "title": "atlas-postgres", "kind": "architecture",
             "content": """\
# atlas-postgres

> Skeleton page. The full handbook is written in a later pass; what is here is short and correct.

`atlas-postgres` is the only place Atlas keeps state: nodes, edges, workitem prose and results,
node documents, intents, the event history, accounts, sessions and tokens.

It is reachable only by the services that need it, on an internal network. Schema changes are
applied by `atlas-migrate`. Back it up together with the install's secrets directory; see
[Operations](/docs/atlas-system/operations.html).
"""},
        ),
    },
    {
        "id": "atlas-system-gateway", "type": "service", "parent": "atlas-system",
        "name": "atlas-gateway",
        "summary": "Optional MCP gateway in front of atlas-mcp, for installs that federate several MCP servers.",
        "docs": (
            {"file": "overview.md", "title": "atlas-gateway", "kind": "architecture",
             "content": """\
# atlas-gateway

> Skeleton page. The full handbook is written in a later pass; what is here is short and correct.

`atlas-gateway` is optional: an MCP gateway in front of `atlas-mcp`, for installs that want one
MCP address for several servers and an allow-list of the tools it exposes.

It is started only with the compose profile `gateway`. Agents still present their own token,
which the gateway passes through, so attribution and access are the same as without it.
"""},
        ),
    },
    {
        "id": "atlas-system-search", "type": "capability", "parent": "atlas-system",
        "name": "Search",
        "summary": "Keyword search over nodes, work and documents, filtered by what the caller may see.",
        "docs": (
            {"file": "overview.md", "title": "Search", "kind": "architecture",
             "content": """\
# Search

> Skeleton page. The full handbook is written in a later pass; what is here is short and correct.

Search is built in: keyword search in Postgres over node names and descriptions, workitem prose,
acceptance criteria, results, labels, decision records and the documents a node serves.

It answers with pointers (the node, where it matched and a short snippet), never the full text.
The filter for what the caller may see is applied inside the query, so a hidden node is neither
counted nor shown. The route is `/api/search`; agents use `atlas_node_search` and `atlas_search`.
"""},
        ),
    },
    {
        "id": "atlas-system-retrieval", "type": "capability", "parent": "atlas-system",
        "name": "Retrieval add-on",
        "summary": "Opt-in semantic retrieval beside keyword search, through an embeddings endpoint the install chooses.",
        "docs": (
            {"file": "overview.md", "title": "Retrieval add-on", "kind": "architecture",
             "content": """\
# Retrieval add-on

> Skeleton page. The full handbook is written in a later pass; what is here is short and correct.

The retrieval add-on is opt-in and off by default. It adds semantic retrieval beside keyword
search: it runs inside `atlas-api`, text is turned into embeddings by an OpenAI-compatible
endpoint the install chooses, and the vectors are kept in Postgres.

No model and no embedding service ship with Atlas. It is turned on with `ATLAS_RETRIEVAL` and the
settings beside it in `.env`, and the `retrieval.yml` compose overlay; the key is a secret file. A
broken configuration never stops Atlas: search stays keyword search (see
[Search](/docs/atlas-system-search/overview.html)), and `/api/health` says why.
"""},
        ),
    },
)


def doc_path(node_id: str, doc: dict) -> str:
    """The document's `node_doc.path` (and `docs[].path`): the key the read route serves."""
    return f"docs/nodes/{node_id}/{doc['file']}"


def page(node_id: str, doc: dict) -> str:
    """The document's page on the site, below /docs/."""
    return f"{node_id}/{doc['file'][:-3]}.html"


#: Every file under frontend/docs/, by its path below /docs/: the ONLY names /docs serves.
SITE_FILES = tuple(sorted(["docs.css", "index.html"]
                          + [page(n["id"], d) for n in NODES for d in n["docs"]]))


def public_paths() -> frozenset:
    """The exact request paths of the site: /docs, /docs/ and /docs/<file> for each file."""
    return frozenset({"/docs", "/docs/"} | {"/docs/" + f for f in SITE_FILES})
