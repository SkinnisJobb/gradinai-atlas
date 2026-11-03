---
audience: operations-security
---

# Agent tokens and read audit

An AI agent reaches Atlas with a bearer credential. There are two kinds:

- **Atlas agent tokens** (`atlas_svc_...`), minted by Atlas: by the person themselves on the board,
  or by an operator with `atlas-admin token mint`.
- **IdP access tokens** (opt-in): an OAuth 2.1 access token issued by your identity provider for the
  Atlas MCP resource.

Either way the agent acts as **its own account, linked to its person**, and never with its person's
instance role. What that account may see and do is in
[Identity and access](identity-and-access.md#what-an-agent-can-do-as-its-user). This page covers
how each credential is made, and the read audit that records what agents read.

## Atlas agent tokens

### Minted by the person (Settings, Agent credentials)

A signed-in person opens **Settings, Agent credentials**, or calls `POST /api/me/agent-credential`
with a purpose, scopes, an expiry and their own password. Atlas creates the agent account
`<person>-<purpose>`, links it to the person and returns the token **once**: only a keyed hash of it
is stored, and nothing returns it again.

- **Browser session only.** A token cannot mint another token, whatever its scopes
  (`403 session_required`): one leaked token must not become a key factory.
- **The person's password, every time.** A live session alone is not enough, because a token
  outlives the password change that would end a stolen session. A wrong password is
  `403 password_incorrect` and counts as a failed login.
- **An SSO person confirms at the IdP instead**, where the operator sets `ATLAS_OIDC_STEP_UP=on`:
  **Confirm with SSO** names the credential being approved and signs them in at the IdP again;
  that credential, and only that one, must then be created in the same browser session within
  `ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS` (300) of that sign-in. Without a step-up the mint is
  `403 step_up_required`; for another name, `403 step_up_agent_mismatch`. See
  [Agent credentials for SSO people](identity-and-access.md#agent-credentials-for-sso-people).
- **Scopes capped by the person's role:** a `member` may give `read` and `write`, an `owner` also
  `admin`. At least one scope is required.
- **Always expires:** 1 to 365 days. There is no never-expires option.
- **Names are permanent.** A purpose that is already used for one of the person's agents is
  `409 name_taken`.

The same screen lists the person's credentials, issues another credential for an existing agent
(rotation, with the same checks; the old credential keeps working until it is revoked), and revokes
one (`POST /api/me/agent-credential/{token_id}/revoke`, permanent, effective at once).

### Minted by an operator: `atlas-admin token mint`

The operator's equivalent of that screen, for people who cannot use it:

```sh
docker compose exec atlas-api atlas-admin token mint --user <person> --purpose claude-code \
    --scopes read --days 90 --out /path/to/new-token-file
```

- `--user` is a username, `<idp>:<subject>`, or an IdP account's email.
- The agent is `<person>-<purpose>`, created on first use and linked to the person. Minting again
  for the same purpose adds a token to the same agent, which is how you rotate: mint, deploy, confirm
  the new token is used, revoke the old one.
- The same rules as the screen: scopes capped by the person's role (default `read`), and an expiry
  of 1 to 365 days (default 365).
- **The raw token goes only to the new file named by `--out`**, created with mode 0600. It is never
  printed, and `--out -` is refused, because the command is often run by an agent whose output is a
  transcript. An existing file is never overwritten. If the file cannot be written, no token is
  minted.
- For an IdP-backed person, the agent is named after the local part of their email, so they must
  have signed in once.
- Each mint is an `auth_event` naming the operator (`--actor`, else `ATLAS_ADMIN_ACTOR`, else the
  OS user).

`atlas-admin token list [--user <person>]` lists live tokens by id and prefix, never the value or
its hash. `atlas-admin token revoke <id or prefix>` or `token revoke --user <person>` revokes them,
permanently, from the next request. For moving a token to a client and rotating it, see
[Operations](operations.md#per-user-agent-tokens).

### Current limitation: SSO-only people cannot mint their own

The password step on the Settings screen re-checks the person's Atlas password. A person who signs
in **only through single sign-on** has no Atlas password, so the check always fails and the request
is refused with `403 password_incorrect`. This is a known gap, tracked for a fix.

**Until then, either:**

- an operator mints the token for them with `atlas-admin token mint --user <idp>:<subject>` (or
  their email, once they have signed in), as above; or
- on an install that sets `ATLAS_OAUTH_RESOURCE`, their client uses an IdP access token instead
  (next section).

## IdP access tokens (opt-in)

With `ATLAS_OAUTH_RESOURCE` set, the API **also** accepts an OAuth 2.1 access token (a JWT) from the
OIDC issuer, issued for the Atlas MCP resource, beside `atlas_svc_` tokens, which are unchanged.
With it unset (the default), a bearer that is not an `atlas_svc_` token is refused exactly as
before, and the MCP service publishes no metadata.

### Settings

| Setting | Meaning |
|---|---|
| `ATLAS_OAUTH_RESOURCE` | The canonical URI of the protected resource: the MCP endpoint as clients name it. An `https` URL with no query or fragment; a trailing slash is dropped and scheme and host are lowercased. A token's `aud` must contain it. The MCP service reads the same variable. |
| `ATLAS_OAUTH_AUDIENCES` | Further accepted `aud` values, comma-separated, for an IdP that stamps something else (Entra v2 puts the resource app's client id in `aud`). |
| `ATLAS_OAUTH_SCOPE_MAP` | Comma-separated `<idp scope>=<read\|write\|admin>`. A token gets the highest tier any of its scopes maps to; `write` includes `read`, `admin` includes both. Unset, or nothing mapped: `read`. |

Each mistake stops the boot with a message naming the variable: a blank value, a non-`https`
resource, `ATLAS_OAUTH_RESOURCE` without `oidc` in `ATLAS_AUTH_PROVIDERS`, either of the other two
variables without `ATLAS_OAUTH_RESOURCE`, or an accepted audience equal to `ATLAS_OIDC_CLIENT_ID`
(that is the audience of Atlas's own ID tokens, and an ID token must never pass as an access token;
give the resource its own app registration).

### What the MCP service publishes

The MCP service serves OAuth protected resource metadata (RFC 9728) naming your OIDC issuer as the
authorization server, at the path-inserted well-known URI of the resource and at the root
`/.well-known/oauth-protected-resource`, and every `401` it sends carries a
`WWW-Authenticate: Bearer resource_metadata="..."` challenge pointing at it. `ATLAS_MCP_OAUTH_SCOPES`
adds a `scope` to the challenge. The MCP service does not validate the token itself: it forwards
each caller's token only to the API, which is the same protected resource.

### How the API checks a token

In order; each failure is a `401` and a `login_fail` auth event with reason `oauth:<why>`:

1. the shape: three base64url segments, at most 16 KiB;
2. the algorithm: in the OIDC provider's asymmetric allowlist and advertised by discovery (never
   `none`, never an HMAC algorithm);
3. the signature, with the issuer's key for `kid`, from the same cached key set the SSO login uses;
4. `iss` exactly the discovered issuer, `aud` containing the resource (or an accepted audience),
   `exp` present, and `exp`, `nbf` and `iat` within 60 seconds of leeway; a token with a `nonce`
   (an ID token) is refused;
5. **the person**: looked up by the SSO login's subject rule. No account is refused with a message
   telling them to sign in with SSO once: Atlas never creates a person from an access token, so SSO
   login stays the one door. A disabled person, one who must change a password, or one whose last
   SSO sign-in is older than `ATLAS_OIDC_REVALIDATE_DAYS` is refused;
6. **the client**: `client_id`, else `azp`, else `appid`. None is refused.

### The account it acts as

One agent account per person and client, created at the first accepted token (idp `oauth`, role
`member`, linked to the person), recorded by `user_created` and `bot_person_linked` auth events.
Its scopes are the mapped tier, narrowed to what the person's role may self-mint (a member's agent
never holds `admin`), and project roles narrow them again per project. Disabling that account with
`atlas-admin user disable` turns off that one client for that person; disabling the person turns
off all of them.

## Read audit

Writes are always recorded (`graph_event` for the graph, `auth_event` for sign-ins, accounts and
tokens). **Agent reads** are recorded only when you turn it on.

### Turning it on

| Setting | Meaning |
|---|---|
| `ATLAS_AUDIT_READS` | `1`, `true`, `yes` or `on`: every agent read writes an `audit_read` row. Unset or `0`: nothing is recorded. |
| `ATLAS_AUDIT_RETENTION_DAYS` | 1 to 36500. Unset: `audit_read` rows are kept forever. |

The switch is read once, at start-up; a bad value stops the boot. Recreate `atlas-api` after
changing it.

### What is logged

**Who:** token callers only, an Atlas agent token or an IdP access token. A person reading the board
in a browser session is not recorded.

**Which reads:** the API routes that serve nodes, which are the ones the MCP tools call:

`GET /api/node/{node_id}`, `GET /api/node/{node_id}/doc`, `GET /api/node/{node_id}/structure`,
`GET /api/search`, `GET /api/context`, `GET /api/graph`, `GET /api/graph/outline`,
`GET /api/graph/path`, `GET /api/workitems`, `GET /api/adrs`, `GET /api/adrs/{ref}`,
`GET /api/intents`.

Recording happens in the API, not in the MCP service, so a token that calls the API directly is
recorded too. A route that answers by calling another audited route in the same request writes one
row, not two.

**One row per read**, with:

| Column | Holds |
|---|---|
| `at` | the time |
| `user_id` | the person the agent belongs to |
| `actor_user_id`, `actor_subject` | the agent account and its name |
| `token_id` | which Atlas token; empty for an IdP access token, which has no Atlas token id |
| `tool` | the MCP tool, as the MCP service declares it (`X-Atlas-Tool`) |
| `request_id` | the per-call request id (`X-Request-ID`) |
| `route` | the route template, for example `GET /api/node/{node_id}` |
| `node_ids` | the ids of the nodes actually served, in order |

`tool` and `request_id` are labels the caller sends: they are validated and stored as given, and a
malformed one is dropped while the read is still recorded.

**Never recorded:** the content read, search strings, and refused reads (a `400`, `404` or `413`
serves no node and writes no row).

**Fail closed.** The row is committed before the response is sent. If it cannot be written, the
caller gets a `500` and no content: with the audit on, an unaudited read is what the operator asked
not to have.

### Retention

With `ATLAS_AUDIT_RETENTION_DAYS` set, run the purge from cron:

```sh
docker compose exec -T atlas-api atlas-admin audit purge
```

It deletes `audit_read` rows older than that many days, and only those. `graph_event` and
`auth_event` are never purged. Without the setting it deletes nothing and says so.

### Exporting

`GET /api/audit/export` streams `graph_event`, `auth_event` and `audit_read` as JSON Lines
(`application/x-ndjson`), oldest first across all three.

- `since`, `until`: ISO 8601, `since` inclusive and `until` exclusive; a time without a timezone is
  read as UTC.
- `kinds`: a comma-separated subset of `graph_event,auth_event,audit_read`, default all three. An
  unknown kind is a `400` naming the legal ones.

**Who:** the instance owner in a browser session, or a token with the `admin` scope whose linked
person is a live owner, re-checked on every call, so a demoted or disabled owner's token loses the
export at once. Everyone else gets `403`. A SIEM collector uses the second kind; minting it and
calling the export from a collector are in [Operations](operations.md#8-audit-export).

An `audit_read` line carries `kind`, `id`, `at`, `user_id`, `actor_user_id`, `actor_subject`,
`token_id`, `tool`, `route`, `node_ids` and `request_id`. No export line contains content, search
strings, passwords or their hashes, token values or digests, or session ids.
