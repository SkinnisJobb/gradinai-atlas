---
audience: operations-security
---

# 0003. Identity: Atlas authenticates every request itself, and fails closed

- **Status:** Accepted
- **Tags:** identity, authentication, security

## Context and Problem Statement

Three kinds of caller reach Atlas: people in a browser, scripts with a service token, and AI agents
through the MCP server. All of them reach one FastAPI application. Atlas holds a team's plans,
decisions and audit trail, so an unauthenticated read is a leak, and an unauthenticated write is
worse.

Atlas is installed by different teams. Some have an identity provider (IdP) and want single sign-on.
Others have none and need accounts that work on their own. Every install needs a way in when the
IdP is down.

**Question:** where is authentication enforced, what credentials are accepted, and which login
methods does Atlas support?

## Decision Drivers

- A route added later, by someone who did not think about auth, must be protected anyway.
- No configuration mistake may leave an install open.
- A database leak must not yield live credentials.
- Machines must get errors they can act on, never a login page to parse.
- Teams with an IdP manage people there; teams without one must not need one.
- Administrators must be able to get in when the IdP is unavailable.

## Considered Options

1. **In-app identity, enforced by one ASGI middleware**, with local accounts and optional OIDC.
2. **Per-route FastAPI dependencies** (`Depends`) for authentication.
3. **Delegate authentication to a reverse proxy** and trust an identity header it sets.
4. **An external IdP only**, with no local accounts.

## Decision Outcome

Chosen option: **1, in-app identity enforced by one middleware, with local accounts and optional
OIDC.**

**The gate.**

- **One ASGI middleware, `AuthGate`, wraps the whole application.** Route-level `Depends` does not
  see mounted sub-applications or routes without the dependency, so a scheme built from it fails
  open for the next route someone forgets. Middleware sees every request, including mounts, 404s
  and routes added later, so it fails closed.
- **Deny by default.** A request without a principal is refused unless its path is on
  `PUBLIC_ROUTES`. That list is short (the landing page, sign-in, the privacy notice and the
  liveness probes, plus any routes an opt-in feature adds) and is pinned by a test, so an addition
  is reviewed.
- **Cookie or Bearer, never both.** A person uses the `__Host-` session cookie, and a machine or agent
  uses `Authorization: Bearer`. A request that carries both is refused with `400`.
- **A Bearer request is never redirected.** It gets `401` JSON. Only a browser asking for HTML
  without a Bearer header is redirected to sign in.
- **Writes are checked twice.** A cookie session must send an allow-listed `Origin` and a CSRF
  synchronizer token. A Bearer token is checked against the scope the route requires (`read`,
  `write` or `admin`). A write route with no scope entry is refused.

**Credentials at rest.**

- **Tokens and session ids are stored as keyed hashes** (HMAC-SHA256 with a server-side pepper).
  Passwords are stored as Argon2id over an HMAC with a separate pepper. A database dump contains
  no usable credential.
- **Missing secrets stop the import.** The peppers and the allowed login origins are read when the
  auth module is imported. If one is missing or malformed, the process stops before the server binds
  its port, and the error names the variable. Nothing is generated as a fallback, because a pepper
  that changed on every boot would log everyone out.
- **No auth-off setting and no default account.** No configuration turns the gate off, and there is
  no self-registration. The first owner is created with `atlas-admin user add`. Tests mint real
  credentials against the real gate.

**Login methods.**

- **Local accounts are always available**, created by an operator, with a forced password change at
  first sign-in.
- **OIDC is optional** (`ATLAS_AUTH_PROVIDERS`). Atlas is a confidential client of one issuer, using
  the authorization code flow with PKCE, `state` and `nonce`, and verifying the ID token's
  signature, issuer, audience and expiry. The IdP's tokens are used once and never stored; Atlas
  issues its own session. Group memberships are synchronised from an allow-listed group map at
  every SSO login.
- **Break-glass.** With `ATLAS_LOCAL_LOGIN=break-glass`, only local accounts flagged as break-glass
  can use a password. Their form is not linked from the login page, and every break-glass login is
  recorded separately in the auth log.
- **Agents are accounts.** Every agent has its own account and token, linked to the person it
  belongs to, so the audit trail always names a real caller and disabling a person disables their
  agents.

### Consequences

- **Positive:** a forgotten check fails closed. The unauthenticated surface is one short, reviewed
  list.
- **Positive:** an install with no IdP works on its own, and an install with one gets SSO, group
  sync and offboarding through the IdP, with a way in when the IdP is down.
- **Positive:** a leaked database yields no live password, token or session.
- **Negative:** Atlas implements and maintains authentication code itself, including the OIDC client,
  instead of relying on a framework. The design is documented in the header of `backend/auth.py`.
- **Negative:** the peppers are fixed for the life of the install. Losing them invalidates every
  password and token, so they must be backed up with the database.
- **Neutral:** only one OIDC issuer is supported per install.

## Pros and Cons of the Options

### Option 1: in-app identity, one middleware *(chosen)*

- Good: fails closed for every route, present and future.
- Good: one place to read and review.
- Bad: Atlas carries its own authentication code.

### Option 2: per-route dependencies

- Good: idiomatic FastAPI, and each route declares what it needs.
- Bad: fails open. A route or mount without the dependency is public, and nothing notices.

### Option 3: reverse-proxy authentication with an identity header

- Good: no login code in Atlas.
- Bad: anyone who can reach the application without passing the proxy can forge the header.
- Bad: ties Atlas to one proxy product, and gives machines no way to authenticate without it.

### Option 4: external IdP only

- Good: no password storage.
- Bad: an install without an IdP cannot run, and an IdP outage locks everyone out, administrators
  included.

## Links

- [Architecture: The auth gate](../ARCHITECTURE.md#the-auth-gate-middleware-that-fails-closed)
- [Security](../SECURITY.md)
- [Identity and access](../guides/identity-and-access.md)
- [0005 Behind a gateway](0005-gateway-behind.md): why a proxy cannot vouch for a caller.
