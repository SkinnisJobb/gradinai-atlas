---
audience: operations-security
---

# 0002. The MCP server is a facade over the REST API

- **Status:** Accepted
- **Tags:** MCP, agents, security

## Context and Problem Statement

AI agents reach Atlas through the Model Context Protocol (MCP). They need task-shaped tools ("read
this node", "file this work", "save what I did"), not raw REST calls. The REST API already holds the
graph, the access rules, the scopes and the audit trail, and browsers and scripts use it directly.

An MCP server can be built in two ways. It can be a second application with its own database
access and rules, or a thin layer that turns each tool call into API calls. It also needs an
identity to call with: its own, or the caller's.

**Question:** what does `atlas-mcp` own, and whose identity does it use when it calls the API?

## Decision Drivers

- The access rules must exist in exactly one place, so they cannot diverge.
- The audit trail must name the agent and the person behind every write.
- Revoking one agent must not cut off the others.
- An unauthenticated caller must be refused before any MCP session exists.
- The MCP server should be cheap to restart, replace or run more than once.

## Considered Options

1. **A stateless facade that forwards the caller's own token.**
2. **A facade that calls with one service token of its own.**
3. **A second application** with direct database access and its own copy of the rules.

## Decision Outcome

Chosen option: **1, a stateless facade that forwards the caller's own token.**

- **`atlas-mcp` holds no data, no logic and no credential of its own.** It stores nothing, and
  each tool is a mapping onto one or more API calls. A tool that needs something the API cannot
  answer means the API is missing an endpoint; the query is not written in the MCP server.
- **Every call is made with the caller's own Bearer token.** The token on the incoming MCP request
  is the token on every API request that tool call makes. The API therefore applies that caller's
  scopes, grants and project roles, and records that caller in its audit trail. The MCP server
  refuses to call the API without a caller credential.
- **The caller gate asks the API.** Before the MCP library sees a request, the gate checks the
  Bearer token by calling `GET /api/me` with it. A missing or rejected token gets `401` with a
  `WWW-Authenticate: Bearer` challenge, and no session is created. A positive answer is cached for
  60 seconds by default (`ATLAS_MCP_AUTH_CACHE_S`), keyed by a SHA-256 hash of the token, never the
  token itself. Negative answers are not cached. If the API cannot be reached, the answer is `503`,
  never a pass. The cache only saves a round trip at the door: every tool call is still checked by
  the API, so a revoked token fails on its next call even inside the cache window.
- **There is no plain-HTTP route from the MCP server to the API.** The two services share no
  network. `atlas-mcp` reaches the API only over HTTPS through the TLS ingress, with the
  certificate verified against the install CA (see [0005](0005-gateway-behind.md)).
- **Destructive tools are opt-in.** The delete tool is not registered unless
  `ATLAS_MCP_ENABLE_DELETE` is set, and the API still requires the `admin` scope for it.

`ATLAS_MCP_ALLOW_SHARED_TOKEN=1` exists as a migration aid for clients that do not yet send their
own token. It is off by default, warns at startup, never sends a delete, and does not change how a
request that presents a token is handled. It is not part of this decision's design and should stay
unset.

### Consequences

- **Positive:** one implementation of authentication, authorization and visibility, in the API. The
  MCP layer cannot become a second, weaker copy of the rules.
- **Positive:** the audit trail names the real agent and its person for every MCP write, and one
  agent's token can be revoked without affecting anyone else.
- **Positive:** `atlas-mcp` can be restarted or scaled freely, because it has no state.
- **Negative:** every tool call costs at least one HTTPS round trip to the API, plus a `/api/me`
  check at the door when the cache is cold.
- **Negative:** when the API is down, the MCP server is down too (`503`). That is intended.
- **Neutral:** the MCP server may choose cheaper defaults for agents (for example, asking for less
  prose), as long as it only passes parameters the API owns and computes nothing itself.

## Pros and Cons of the Options

### Option 1: stateless facade, caller's token *(chosen)*

- Good: rules in one place, per-caller audit and revocation.
- Good: an unauthenticated caller gets a real `401`, not a session in which every tool fails.
- Bad: an extra hop per call.

### Option 2: facade with its own service token

- Good: clients need no credential of their own.
- Bad: every agent becomes the same principal. The audit trail says "the MCP server" for every write,
  and access cannot differ per agent.
- Bad: the MCP server's token becomes the most valuable credential in the install, reachable by
  anyone who can reach the MCP port.
- Bad: revoking it cuts off every agent at once.

### Option 3: a second application with database access

- Good: no extra hop.
- Bad: the access rules, visibility filter and audit writer would be duplicated, and would diverge.
- Bad: a second component holding database credentials.

## Links

- [Architecture: How a request travels](../ARCHITECTURE.md#how-a-request-travels)
- [Security](../SECURITY.md)
- [MCP tools](../MCP-TOOLS.md)
- [0003 Identity](0003-identity.md): what the forwarded token is.
