# AGENTS.md

Briefing for any coding agent working in this repository (Claude Code, Codex, Cursor and others
read it). It holds what you need before your first change, and points at the page that carries the
detail. If this file and the code disagree, the code is right: fix this file in the same pull
request. `backend/verify_docs.py` checks this file's length and fails on a sentence copied from
another briefing file.

## What this is

Atlas keeps one graph of a body of work in PostgreSQL (projects, systems, decisions and the work
items that change them) behind a FastAPI API, and shows it as a board in the browser. An MCP server
gives AI agents the same graph through the same access rules. Everything is rows in the database;
no file feeds it.

You do not need a running Atlas to work here. The repository is self-describing: read its docs, not
an install's.

## Where the knowledge is

| To learn about | Read |
|---|---|
| the documentation as a whole, with a reading order | `docs/oss/README.md` |
| nodes, edges, work items, intents and the vocabulary | `docs/oss/guides/concepts.md` |
| components, request paths, where data lives, migrations | `docs/oss/ARCHITECTURE.md` |
| every setting and its default | `docs/oss/CONFIG.md` (generated) |
| the REST API and the MCP tools | `docs/oss/API.md`, `docs/oss/MCP-TOOLS.md` (generated) |
| accounts, tokens, roles, private projects | `docs/oss/guides/identity-and-access.md` |
| the security model and the CI security checks | `docs/oss/SECURITY.md` |
| setup, tests, CI jobs, the scrub gate, proposing a change | `docs/oss/CONTRIBUTING.md` |
| the decisions that shape the code | `docs/oss/adr/` |
| what an install serves at `/docs` (the handbook) | `backend/system_handbook.py`, the one source |

## Before you change something

- Read the nested briefing for the directory you are editing: `backend/CLAUDE.md` before a route,
  mutation, column or migration, `backend/TESTING.md` before a check, `frontend/CLAUDE.md` before
  the board. They load only when needed, which is why the detail lives there and not here.
- The design reasoning sits in the comments beside the code it explains. Read the comment at the
  symbol you are about to change before you change it.
- New behaviour is opt-in: a setting switches it on, and with the setting unset an existing install
  behaves exactly as before. Schema changes are additive. `docs/oss/CONTRIBUTING.md` spells out the
  pattern.

## Never

- Commit a secret, a token, a real hostname, a private address or a personal name, including in a
  test or a fixture. Use `example.com`, `atlas.example.com` and the documentation IP ranges.
  `tools/scrub/scan.py` enforces this in CI.
- Weaken the gate to make a test pass. There is no setting that turns authentication off, and tests
  mint their own credentials against the real gate.
- Edit a generated page by hand. Change its source and rerun the generator.
- Edit an accepted ADR to change a decision. Write a new one that supersedes it.

## What "done" means here

1. `cd backend && python verify_all.py` passes. It starts its own disposable PostgreSQL, so Docker
   must be running; there is no database to set up.
2. Every board module passes `node --check`, and a new one is listed in `ASSET_FILES`.
3. The `mcp/verify_*.py` scripts pass if you touched `mcp/`.
4. Each reference generator you affected passes with `--check` (`tools/gen_api_ref.py`,
   `tools/gen_config_ref.py`, `tools/gen_tool_ref.py`, `tools/docs_site.py`).
5. The documentation page that describes what you changed says what is now true.

If you could not run one of these, say so plainly in the pull request rather than implying it was
checked.

## The repository

| Path | What |
|---|---|
| `backend/` | `atlas-api`: `main.py` (app, startup), `auth.py` (the gate), `routers/`, the `verify_*.py` suite |
| `frontend/` | the board, plain ES modules with no build step; new work goes in `frontend/v2/` |
| `mcp/` | `atlas-mcp`, the MCP server, its own compose project |
| `clients/` | client packs, such as the one for Claude Code |
| `deploy/oss/` | the install: compose file, ingress, `.env.example` |
| `tools/` | the reference generators, the scrub gate and the CI checks |
| `docs/oss/` | the product documentation and the ADRs |
