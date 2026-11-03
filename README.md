# Atlas

Atlas is a shared map of a body of work, for the people and the AI agents doing it. It keeps one graph
of everything being built (projects, systems, decisions and the epics, stories and tasks that change
them) and shows it as a board in the browser.

- **One graph in PostgreSQL.** Nodes, edges, documents, intents and comments are rows, behind a
  FastAPI API. There is no file backlog: the database is the record.
- **A board** at `/board`: a radial graph of the world, a work board scoped by it, and a panel for
  each node with its documents, acceptance, comments and change history.
- **Accounts and access.** People sign in with a password or, if the install configures one, an
  organisation's identity provider. Private projects are visible only to the people granted them,
  and every change is recorded with who made it.
- **Agents as first-class callers.** Each agent gets its own credential, linked to the person it
  works for. The `atlas` MCP service (`mcp/`) gives Claude Code and other MCP clients the same
  graph through the same access rules; [`clients/claude-code/`](clients/claude-code/README.md) is
  the client pack.
- **A handbook** served by the install at `/docs`, and a privacy notice at `/auth/privacy`.

Atlas is in **alpha**. Expect configuration and APIs to change between releases; the release notes
in [RELEASES.md](docs/oss/RELEASES.md) say what changed and how to upgrade.

## Getting started

Atlas runs as a set of containers behind a TLS ingress, from the compose project in
[`deploy/oss/`](deploy/oss/). Check the [server requirements](docs/oss/guides/server-requirements.md),
then follow [INSTALL.md](docs/oss/INSTALL.md): it takes a clean VM to a running install with a
signed-in owner, and then connects Claude Code to it over MCP.

## Documentation

Start with [`docs/oss/`](docs/oss/README.md); its index gives a reading order.

- Using Atlas: [concepts](docs/oss/guides/concepts.md) and [connecting agents](docs/oss/guides/agents.md).
- Running Atlas: [INSTALL.md](docs/oss/INSTALL.md), [operations](docs/oss/guides/operations.md),
  [identity and access](docs/oss/guides/identity-and-access.md). Every setting is in
  [CONFIG.md](docs/oss/CONFIG.md).
- How it fits together: [ARCHITECTURE.md](docs/oss/ARCHITECTURE.md), [API.md](docs/oss/API.md) and
  [MCP-TOOLS.md](docs/oss/MCP-TOOLS.md).
- Decisions: [`docs/oss/adr/`](docs/oss/adr/).
- The security model: [docs/oss/SECURITY.md](docs/oss/SECURITY.md).

## Contributing

Issues and pull requests are welcome from anyone. [CONTRIBUTING.md](CONTRIBUTING.md) has the terms,
and [the development guide](docs/oss/CONTRIBUTING.md) has the setup, the tests and the CI checks a
pull request must pass. Coding agents start from [AGENTS.md](AGENTS.md).

To report a vulnerability, do not open an issue: see [SECURITY.md](SECURITY.md).

## Layout

```
backend/    the API: main.py (app, startup), routers/<area>.py, auth.py (the gate)
frontend/   the board, with no build step
mcp/        the atlas MCP service (its own compose project)
clients/    client packs, such as the one for Claude Code
deploy/oss/ the compose file, configuration and scripts INSTALL.md uses
docs/oss/   the product documentation and the ADRs
tools/      the reference-page generators, the scrub gate and the CI checks
tests/      the install smoke, end-to-end and contract tests
```

## Licence

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
