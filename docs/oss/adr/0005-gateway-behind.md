---
audience: operations-security
---

# 0005. Atlas runs behind a gateway, and is not one

- **Status:** Accepted (amended 2026-10-30, OSS-26 D: the ingress's own legs are TLS)
- **Tags:** network, TLS, gateway, deployment

## Context and Problem Statement

An Atlas install has to be reachable over the network by browsers, scripts and agents, and every
one of those requests carries a credential. Organisations already have opinions about their edge:
a reverse proxy, a load balancer, an API gateway, an MCP gateway, their own PKI or ACME
certificates. Atlas cannot assume any one of them.

Inside the install, `atlas-api` and `atlas-mcp` talk to each other, and the MCP hop carries the
caller's token. A token on a plain-HTTP hop that crosses a network can be read by anything on that
network.

**Question:** how does Atlas meet the network: does it terminate TLS and act as a gateway itself,
or sit behind one, and what does it require of whatever is in front?

## Decision Drivers

- Every hop that carries a token or cookie across a network is encrypted and verified.
- One published port, so the exposed surface is small and easy to reason about.
- Operators can use their own proxy, gateway and certificates without changing Atlas.
- Nothing at the edge may be able to widen access. The edge can refuse, never vouch.
- No identity can be forged by sending a header.

## Considered Options

1. **Atlas behind a TLS-terminating ingress**, with token-carrying internal hops re-encrypted, and
   authentication done by Atlas on every request.
2. **Atlas terminates TLS itself** and publishes its services directly.
3. **Atlas builds its own gateway**, with routing, per-caller policy and MCP federation.
4. **Trust the proxy for identity**: the edge authenticates and passes the identity in a header.

## Decision Outcome

Chosen option: **1, behind a TLS-terminating ingress, with Atlas authenticating every request.**

- **Atlas does not build a gateway.** It ships one bundled TLS ingress (a pinned Caddy image) so an
  install works on its own, and an optional bundled MCP gateway profile. Either can be replaced
  or fronted by the operator's own.
- **One published TLS port.** In the bundled install only the ingress publishes a port. `atlas-api`
  and `atlas-mcp` publish nothing. CI rejects a published plain-HTTP port that carries tokens. The
  ingress sets the browser security headers, including a Content-Security-Policy, in one place.
- **Token-carrying hops between services are HTTPS against the install CA.** `atlas-mcp` reaches the
  API only through the ingress over HTTPS, with the certificate verified against the install CA
  (`ATLAS_CA_BUNDLE`). The optional MCP gateway reaches `atlas-mcp` the same way. Mutual TLS on the
  MCP-to-API hop is optional (`ATLAS_TLS_CLIENT_AUTH=require` on the ingress, with a client
  certificate on `atlas-mcp`). Verification cannot be turned off, and a plain `http://` API URL is
  refused at startup unless it points at a loopback host.
- **Every token-carrying hop is TLS, the ingress's own legs included.** The ingress forwards to
  `atlas-api` at `https://atlas-api-origin:8443` on the internal `origin` network, and to
  `atlas-mcp` at `https://atlas-mcp-origin:8493` on the internal `mcp` network, verifying each
  certificate under that name against the origin CA (`secrets/tls/origin-ca.crt`: the install CA,
  or a separate CA from `init-certs.sh --origin-only`). On these legs `atlas-api` and `atlas-mcp`
  terminate TLS themselves, each with its own certificate and key; neither serves plain HTTP, and
  nothing listens on the old plain ports `8000` and `8092`. Each network is still internal (no
  route out of the host) with exactly two members, and `atlas-mcp` and `atlas-api` share no
  network at all. Until 2026-10-30 these two legs were plain HTTP inside those two-member
  networks; that was accepted for go-live and closed by OSS-26 D.
- **Atlas reads no identity headers from a proxy.** Every request is authenticated by Atlas from its
  own cookie or Bearer token (see [0003](0003-identity.md)). A proxy cannot vouch for a caller, and a
  forged header has no effect. The only header taken from a proxy is the client address, and only
  from address ranges listed in `ATLAS_TRUSTED_PROXY_CIDRS`, for sign-in throttling.
- **What a proxy in front must do:** terminate TLS for clients, forward `Host` unchanged (or present
  a host the install's public URL names, since any other host gets `421`), forward `Authorization`
  unchanged, and not buffer server-sent events on `/mcp/`.
- **An MCP gateway authorizes per route, not per caller.** A tool federated through a gateway is
  offered to every client of that route; only the API's token check tells callers apart. So a
  gateway must pass the caller's `Authorization` header through, its configuration lists the tools
  each federated server exposes, and the destructive delete tool is not registered unless
  explicitly enabled.

### Consequences

- **Positive:** certificate handling stays out of the application, and an operator can bring their
  own PKI, ACME setup, proxy or gateway without changing Atlas.
- **Positive:** changing or adding an edge component cannot widen access. The worst a misconfigured
  edge can do is refuse requests (`421`, `401`).
- **Positive:** a token never crosses a network in clear, not even a two-member internal one. No
  hop in the install is plain HTTP.
- **Negative:** the MCP-to-API path goes through the ingress, which adds a TLS hop and makes the
  ingress a dependency of every tool call.
- **Negative:** an operator replacing the bundled ingress must reproduce its internal TLS fronts and
  its security headers, or lose them, and must trust `origin-ca.crt` for the upstream legs.
- **Negative:** two more certificates to issue and renew (`api-origin.crt`, `mcp-origin.crt`), and
  an install made before them runs `init-certs.sh --origin-only` once on upgrade.
- **Neutral:** browser cookies are `Secure` and `__Host-` prefixed, so a browser session cannot work
  over plain HTTP at all, even in testing.

## Pros and Cons of the Options

### Option 1: behind a TLS-terminating ingress *(chosen)*

- Good: one exposed port, encryption on every token-carrying hop (the ingress's upstream legs
  included), and any edge the operator prefers.
- Good: identity stays in Atlas, so the edge cannot be the weak point for access.
- Bad: more moving parts in the bundled install (the ingress and its internal fronts).

### Option 2: Atlas terminates TLS itself

- Good: fewer containers.
- Bad: certificate issuance and renewal, and the security headers, move into the application.
- Bad: each service would publish its own port, and the MCP-to-API hop would still need its own TLS.

### Option 3: Atlas builds its own gateway

- Good: per-route policy under Atlas's control.
- Bad: duplicates what dedicated gateways already do well, and competes with whatever the
  organisation already runs.
- Bad: a gateway still cannot tell callers apart better than the API's own token check.

### Option 4: trust the proxy for identity

- Good: single sign-on handled entirely at the edge.
- Bad: any path that reaches Atlas without the proxy can forge the identity header.
- Bad: machines and agents need a separate mechanism anyway.

## Links

- [Architecture: The TLS hops](../ARCHITECTURE.md#the-tls-hops)
- [Architecture: Atlas behind any gateway](../ARCHITECTURE.md#atlas-behind-any-gateway)
- [Security: TLS on every token-carrying hop](../SECURITY.md#tls-on-every-token-carrying-hop)
- [0002 MCP facade](0002-mcp-facade.md)
- [0003 Identity](0003-identity.md)
