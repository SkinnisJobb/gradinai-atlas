---
audience: operations-security
---

# Security

This page describes the Atlas security model: what Atlas protects, the controls in the code, and
what an operator is expected to configure. It ends with how to report a vulnerability. Accounts,
roles and access to projects are covered in more depth in
[Identity and access](guides/identity-and-access.md).

Where this page names a file, that file is the authority. If this page and the code disagree, the
code is right.

## Threat model, in brief

Atlas holds a team's project graph: nodes, work items, documents, decisions and the audit trail of
who changed what. It is reached by three kinds of caller: people in a browser, machines with a
service token, and AI agents through the MCP server (`atlas-mcp`), which calls the same API with
the agent's own token.

What Atlas defends against:

- **An unauthenticated caller on the network.** Every route is behind one authentication gate, and
  the gate denies by default. This includes routes that do not exist yet.
- **A signed-in user reaching beyond their access.** Visibility and write permission are decided
  per request on the server, by one resolver. A node the caller may not see answers exactly as a
  node that does not exist (404).
- **A leaked credential.** Tokens are stored only as keyed digests and are narrowed by scope. They
  expire, can be revoked, and are tied to an accountable person. An agent never exceeds the person
  it belongs to.
- **Credentials in transit.** Every hop that carries a token over a network is HTTPS with the
  certificate verified.
- **Secrets at rest in configuration.** Secrets can be delivered as files, and a strict mode refuses
  them as environment variables.
- **Browser attacks.** The defences are CSRF tokens, `__Host-` cookies, a strict Content Security
  Policy, a Host allow-list against DNS rebinding, and frame denial.
- **Vulnerable dependencies and images.** CI produces SBOMs and runs SCA, SAST, secret scanning and
  DAST, with gates that fail the build on new high-severity findings.

What Atlas does not defend against:

- **An attacker with root on the host or direct database access.** Session ids and tokens are
  stored only as keyed digests, so a database dump yields no live credential. The data itself is
  readable.
- **An operator who is careless with the boot secrets.** The two peppers (`ATLAS_PASSWORD_PEPPER`,
  `ATLAS_TOKEN_PEPPER`) and the database URL must be kept with the database backup and nowhere
  else.
- **An agent misusing access it was legitimately given.** An agent can do what its token scope and
  its person's role allow. The controls are narrow scopes, short expiry, revocation and the audit
  trail. Atlas does not inspect intent.

## The authentication gate fails closed

Authentication is one ASGI middleware (`backend/auth.py`, `AuthGate`). It wraps the whole
application: every route, every mount and every 404. It is deliberately not per-route
dependencies, because those fail open when someone adds a route and forgets the check.

- **Deny by default.** `auth.PUBLIC_ROUTES` is the whole unauthenticated surface: the login pages,
  liveness and readiness probes, and a few public pages. Everything else needs a principal,
  including the API's own health document. A CI check pins the list, so an addition is reviewed.
- **Host allow-list.** A `Host` header outside the configured origins gets `421` before any handler
  runs. This blocks host-header injection and DNS rebinding. `atlas-mcp` applies the same rule with
  its own list (`ATLAS_MCP_ALLOWED_HOSTS`).
- **Two credential branches, never both.** `Authorization: Bearer` is a machine. A
  `__Host-atlas_session` cookie is a person. A request that presents both is refused with `400`.
- **Machines are never redirected.** A caller with a Bearer header, or any `/api/` path, gets `401`
  JSON, never a `302` to a login page. A script that followed a redirect would parse a login page as
  data.
- **Uniform refusals.** A revoked, an expired and a never-existing token give byte-identical
  responses. Every refused login gives the same generic failure.
- **Boot fails closed.** `ATLAS_TOKEN_PEPPER`, `ATLAS_PASSWORD_PEPPER`, `ATLAS_LOGIN_ORIGINS` (or
  `ATLAS_PUBLIC_URL`, from which it is derived) and the database URL are checked at import. If one
  is missing or malformed, the process stops before it binds a port, and the error names the
  variable. Atlas has no generated-if-missing fallback, no default account and no
  self-registration. The first owner is created with `atlas-admin user add`.
- **No authentication bypass switch.** No setting turns the gate off. Tests mint real credentials
  against the real gate.
- **`atlas-mcp` asks the API.** The MCP server's caller gate (`mcp/app/caller_auth.py`) lets a
  request through only if `atlas-api` accepts the caller's token (`GET /api/me`). If the API cannot
  be reached, the answer is `503`, not a pass. A positive answer is cached for
  `ATLAS_MCP_AUTH_CACHE_S` (60 seconds by default), keyed by a SHA-256 of the token. Every tool call
  is still checked by the API with the same token, so a revoked token fails on its next call even
  inside that window.

### Writes

Every state-changing route goes through `auth.guard_write`:

- **Cookie sessions** must send an allow-listed `Origin` and a synchronizer CSRF token, which is
  derived from the session under the server-side pepper. `SameSite=Lax` is kept as defence in
  depth, not as the control. There are no state-changing `GET` routes.
- **Bearer tokens** are exempt from CSRF (an `Authorization` header is never ambient) and are
  scope-checked instead. `auth.WRITE_SCOPE_ROUTES` prices each mutating route at `write` or
  `admin`. A mutating route missing from that map is refused.

With project RBAC on, the caller's project role is checked as well. See
[Identity and access](guides/identity-and-access.md#roles-on-projects-rbac).

## Passwords, sessions and tokens

**Passwords** (`backend/passwords.py`) are stored as Argon2id over an HMAC-SHA256 of the password
under `ATLAS_PASSWORD_PEPPER`. The Argon2id parameters are RFC 9106's second recommended set:
64 MiB, t=3, p=4. Candidate passwords are checked against a bundled list of breached passwords.
Logins are throttled per account and per source address, and every success and failure is written
to `auth_event`.

**Sessions** use a 256-bit random id. The database stores only `HMAC(pepper, id)`, so a database
leak yields no live session. The cookie is `__Host-atlas_session`, with `Secure`, `HttpOnly`,
`SameSite=Lax` and no `Domain`. Expiry is enforced on the server: 24 hours idle and 14 days
absolute. An OIDC session is shorter, `ATLAS_OIDC_MAX_SESSION_HOURS` (12 by default). The session id
is rotated at login and again at a password change. A forced password change is re-read from the
database on every request, never cached.

**Per-user tokens.** Every machine and agent has its own account (`is_service`) and its own
`atlas_svc_` token. Tokens are never shared, so the audit trail always names a real caller.

- The token is `atlas_svc_<6 hex>_<43 url-safe characters>`. The secret part is 256 bits from a
  CSPRNG. The fixed prefix lets secret scanners recognise a leaked token.
- **Hashing:** the token is stored as `HMAC-SHA256(ATLAS_TOKEN_PEPPER, token)`, under a
  domain-separation label, and looked up by that digest with a constant-time compare. It is a keyed
  fast hash on purpose. Against a 256-bit random secret a slow KDF adds no protection, and running
  one per request on an internet-facing endpoint would invite denial of service. The raw value is
  shown once at creation and can never be read back.
- **Scopes** are `read`, `write` and `admin`. `has_scope` checks plain membership, with no implied
  ordering. `admin` covers every delete and all access administration.
- **Self-service credentials.** A signed-in person can create an agent credential for themselves
  (Settings, Agent credentials, or `POST /api/me/agent-credential`). The rules:
  - Only from a browser session, so a leaked token cannot mint more tokens.
  - Only after re-entering the password, or, for an SSO person with `ATLAS_OIDC_STEP_UP=on`,
    after a fresh sign-in at the IdP (`prompt=login`, an ID-token `auth_time` no older than the
    step-up flow and at most `ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS`, 300 by default). That
    sign-in is bound to the browser session it was made in and to the one credential it names
    (shown to the person before and after the IdP), buys only that credential, and cannot be
    started or spent by a token. See
    [Identity and access](guides/identity-and-access.md#agent-credentials-for-sso-people).
  - The scopes are capped by the person's role: a `member` can mint `read` and `write`, an `owner`
    can also mint `admin`.
  - The credential must expire, with no never-expires option.
- **Rotation without an outage.** Issuing a replacement does not revoke the old token. Revoke the
  old one once the new one is in use. Revoking takes effect on the next request. Disabling a person
  disables their agents' tokens. A password change does not revoke tokens.

The two peppers are fixed secrets. Regenerating one invalidates every password or every token and
session at once.

## Secrets as files

`backend/settings.py` (`read_secret`) is the one place Atlas reads a secret. It follows the Docker
secrets convention:

1. `NAME_FILE` set: the file is read. The file wins over a plain `NAME`, which is then ignored with
   a warning that names both variables, never the value. A set but unreadable path is an error,
   never a silent fall back to the environment.
2. Otherwise a plain `NAME` is accepted, **unless strict mode is on**:
   `ATLAS_REQUIRE_SECRET_FILES=1` refuses any secret given as a plain environment variable, in both
   `atlas-api` and `atlas-mcp`.
3. Neither: a required secret stops the boot, naming the variable.

Values are stripped of surrounding whitespace, and an empty file counts as missing. Error messages
name the variable and the path, never the contents.

Some secrets are read only from a file in every mode. The OIDC client secret
(`ATLAS_OIDC_CLIENT_SECRET_FILE`) and the OIDC private key (`ATLAS_OIDC_CLIENT_KEY_FILE`) are
examples: their plain forms are refused at boot.

The OSS install (`deploy/oss/`) sets `ATLAS_REQUIRE_SECRET_FILES=1` and passes every secret as a
compose `secret:` under `/run/secrets/`. Its `.env` holds no secret. Keep the `secrets/` directory
backed up together with the database: without the peppers, the stored passwords and tokens cannot
be verified.

## TLS on every token-carrying hop

Every request to `atlas-api` or `atlas-mcp` carries a credential, so every hop that carries a token
or a session cookie is HTTPS with the certificate verified. In the OSS layout there is no exception:
the ingress's own legs to `atlas-api` and `atlas-mcp` are TLS too.

- **`atlas-mcp` to `atlas-api`** (`mcp/app/tls.py`): `ATLAS_BASE_URL` must be `https://`, except to
  a loopback host. The check runs at startup, and a plain `http://` URL to anything else stops the
  process. Verification is always on (hostname checked, certificate required, TLS 1.2 or later),
  and no setting turns it off.
- **Configurable CA.** By default the system trust store is used. `ATLAS_CA_BUNDLE=/path/ca.pem`
  replaces it with exactly the CAs in that file, which is where an internal PKI root goes. A wrong
  path stops the process; it never falls back to the system store.
- **Optional mutual TLS** on that hop: `ATLAS_MCP_CLIENT_CERT_FILE` and `ATLAS_MCP_CLIENT_KEY_FILE`
  on `atlas-mcp`, and `ATLAS_TLS_CLIENT_AUTH=require` on the OSS ingress.
- **Atlas to the identity provider** (OIDC discovery, token and JWKS endpoints) is HTTPS. Discovery
  URLs that are not `https` are refused, and certificates are verified. An internal CA is trusted
  through `SSL_CERT_FILE`.
- **The OSS layout** (`deploy/oss/docker-compose.yml`) publishes one port, the TLS ingress
  (`atlas-tls`, Caddy). `atlas-api` and `atlas-mcp` publish nothing, and neither serves plain HTTP:
  each terminates TLS itself, and nothing listens on the old plain ports `8000` and `8092`.
  - **The ingress to `atlas-api`**: `https://atlas-api-origin:8443` on the internal `origin`
    network (the ingress and `atlas-api` only).
  - **The ingress to `atlas-mcp`**: `https://atlas-mcp-origin:8493` on the internal `mcp` network
    (the ingress and `atlas-mcp` only).

  Each listener has its own certificate (`secrets/tls/api-origin.crt`, `mcp-origin.crt`), and the
  ingress verifies it under that name against `secrets/tls/origin-ca.crt` only, never the system
  store. `origin-ca.crt` is the install CA (a copy of `ca.crt`), or a separate CA made by
  `init-certs.sh --origin-only` when the server certificate comes from your PKI or the install
  predates these listeners. Each service holds only its own key, never the public door's.
  `atlas-mcp` reaches the API back through the ingress over HTTPS, checked against the install CA.
  The public certificate comes from your PKI (`files`), Caddy's internal CA (`internal`), or ACME;
  the internal legs use the install's own certificates in every mode.
- **CI enforces it.** `tools/ci/check_tls.sh` fails the build on any of these:
  - a plain `http://` reference to a non-loopback host in configuration;
  - a published plain-HTTP token port outside loopback;
  - any switch that disables verification (`verify=False`, `CERT_NONE`, `curl -k` and similar).

  In the OSS layout, any plain-HTTP token port published at all is a failure, and so is an ingress
  upstream that is not `https://` with the origin-CA transport (rule R6). R6 also covers the
  OpenShift front (`deploy/openshift/base/caddy/Caddyfile`), whose legs use the service CA.
  `tools/ci/check_oss_compose.py` (rule S8) fails a compose file in which `atlas-api` or `atlas-mcp`
  does not serve TLS itself. Install smoke leg 6 checks every hop on the running install: the
  ingress, `atlas-mcp` to the API, the gateway, and both ingress legs (TLS verified against the
  install CA, plain HTTP refused, the old plain ports closed).

## Content Security Policy and browser headers

The OSS ingress (`deploy/oss/caddy/Caddyfile`, the `security_headers` snippet) sets these headers on
every response from the public door:

- `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
  img-src 'self' data: https:; font-src 'self' data:; connect-src 'self'; object-src 'none';
  base-uri 'self'; form-action 'self'; frame-ancestors 'none'`. The policy allows no inline script
  and no `eval`. The frozen legacy board, at `/board/legacy`, has two inline scripts, and they are
  allowed only by their SHA-256 hashes, never by `'unsafe-inline'`.
- `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`.
- `Strict-Transport-Security: max-age=31536000`, not sent for localhost.

`tools/ci/check_oss_headers.py` fails the build if any of these headers changes, if a CSP allows
`'unsafe-inline'` or `'unsafe-eval'` in `script-src`, or if the legacy board's hashes no longer
match its scripts. The OpenShift front (`deploy/openshift/base/caddy/Caddyfile`) carries the same
snippet, and the check fails if the two copies differ. The public handbook pages also carry their own CSP from the API, with no script
source at all, so they stay locked down behind a front that sets no headers.

No CORS middleware is installed: every browser caller is same-origin.

## Audit: mutations, logins and reads

- **`graph_event`** records every graph mutation, with the calling principal taken from the
  authenticated identity, never from the request body.
- **`auth_event`** records logins and failures, password changes, account and token changes, group
  sync, access changes and every break-glass login. Values a client controls are encoded before
  they are written, to prevent log injection. Passwords, tokens and session ids are never written.
- **Read audit (opt-in).** With `ATLAS_AUDIT_READS=1`, every node read by an agent writes one
  `audit_read` row through the API routes that serve nodes. An agent is a service token or an IdP
  access token. The row holds:
  - the time;
  - the person behind the agent, the agent itself and the token id;
  - the MCP tool name and request id;
  - the route;
  - the ids of the nodes actually served.

  Search text and content are never stored, and refused reads write no row. Recording is **fail
  closed**: if the row cannot be written, the caller gets a `500` and no content. Browser sessions
  are not recorded.
- **Retention.** With `ATLAS_AUDIT_RETENTION_DAYS=N`, `atlas-admin audit purge`, run from cron,
  deletes `audit_read` rows older than N days. `graph_event` and `auth_event` are never purged.
  Unset means keep forever.
- **Export.** `GET /api/audit/export?since=&until=&kinds=` streams all three trails as JSON Lines,
  oldest first, for a SIEM. Two callers may use it: an owner's browser session, or an `admin`-scoped
  token whose linked person is a live owner. That owner is re-checked on every call. Everyone else
  gets `403`. The export never contains content, search strings, passwords, hashes, tokens or
  session ids.

## Supply chain

The CI workflows under `.github/workflows/` and the gates under `tools/ci/`:

| Control | What it does | Fails the build on |
|---|---|---|
| **SBOM** (`sbom-sca.yml`, `vendor_sbom.py`) | CycloneDX SBOMs for each image, each Python lock and the vendored browser JavaScript. The board has no build step, so its vendored libraries are named from their version banners. | a vendored file the SBOM list does not know |
| **SCA** (Trivy, `sca_gate.py`) | scans the images, the locks and the vendored-JS SBOM, on pull requests and weekly | a critical or high vulnerability that has a fix |
| **SAST and secrets** (Semgrep, Bandit, gitleaks; `sast_gate.py`) | static analysis of Python and JavaScript, and credential scanning of git history, compared with a triaged baseline | a new high finding |
| **DAST** (OWASP ZAP, `dast_gate.py`) | a baseline scan of the public pages and an authenticated API scan of a running OSS stack, on a schedule | a high-risk alert |
| **Hashed locks** (`check_locks.py`) | the images install `requirements-lock.txt` with `--require-hashes` | a lock out of step with `requirements.txt`, or a missing hash |
| **Pinned digests** (`check_pins.py`) | images referenced by tag plus `@sha256` digest; GitHub's own actions (`actions/`, `github/`) by commit SHA or version tag, every other action by commit SHA; Dependabot moves the digests and SHAs | an image without a digest, or an action not pinned that way |
| **EOL gate** (`check_eol.py`) | every runtime the tree names (Python, Postgres, Caddy, Node) checked against a dated end-of-life table, refreshed weekly against endoflife.date | a runtime within six months of end of support, or a stale table |

An accepted finding (SCA, DAST) or a waiver (EOL) must carry a reason and an `until` date. After
that date it fails again. No exception is open-ended. An SCA acceptance also names the artefacts
it holds for (`in`): the same finding in any other image still fails, and an acceptance that no
longer matches a finding fails the gate until it is removed.

`check_pins.py` runs with `--enforce` on every pull request, so image digests and action pins are
enforced: an unpinned reference fails the build. A version tag is accepted only on GitHub's own
actions, which run on GitHub's runners anyway. A third-party tag can be moved by whoever controls
that repository, so third-party actions need a commit SHA. Its `--self-test` proves that the gate
fails an unpinned image and an unpinned third-party action, and passes pinned ones.

## Hardening checklist for an install

- Set `ATLAS_REQUIRE_SECRET_FILES=1` and deliver every secret as a file. The OSS `.env.example`
  already does.
- Keep `ATLAS_TOKEN_PEPPER`, `ATLAS_PASSWORD_PEPPER` and the database password with the database
  backup, in a vault.
- Expose only the TLS ingress. Do not publish the API or MCP ports.
- Point `ATLAS_CA_BUNDLE` at your internal root if your certificates come from a private CA.
- For a team install, set `ATLAS_RBAC=projects` and `ATLAS_DEFAULT_PROJECT_VISIBILITY=private`, use
  OIDC with `ATLAS_LOCAL_LOGIN=break-glass`, and keep the break-glass credential in the vault. See
  [Identity and access](guides/identity-and-access.md).
- Turn on `ATLAS_AUDIT_READS=1` if you need to know what agents read, and set a retention period.
- Leave `ATLAS_MCP_ALLOW_SHARED_TOKEN`, `ATLAS_MACHINE_GRACE_HOSTS` and `ATLAS_ALLOW_SESSION_MINT`
  unset. They are migration and test aids, all off by default.
- Give agents `read` unless they need to write, and the shortest expiry you will tolerate.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it privately through GitHub's private vulnerability reporting: open the repository's
**Security** tab and choose **Report a vulnerability**. The report is visible only to you and the
maintainer. There is no security mailbox. The root [SECURITY.md](../../SECURITY.md) has the
supported versions.

Include:

- the affected version or commit;
- the component (`atlas-api`, `atlas-mcp`, the board or the install);
- steps to reproduce, and what an attacker gains;
- whether the problem needs a non-default setting.

Do not include real tokens, passwords or production data. If a credential was exposed while you were
investigating, say so, so it can be revoked.

You will get an acknowledgement, and a fix or mitigation will be coordinated with you before
anything is disclosed publicly.
