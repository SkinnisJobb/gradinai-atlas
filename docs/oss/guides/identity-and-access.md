---
audience: operations-security
---

# Identity and access

This guide covers who can sign in to Atlas, how they prove it, and what each person and agent can
see and change. The security controls underneath are described in [Security](../SECURITY.md). The
decision record behind most of this page is ADR 0041 (project-scoped RBAC, IdP groups, OIDC login).

Everything on this page beyond local accounts is **opt-in**. With none of the settings below, an
install has local accounts only, two instance roles, and projects shared by default. The OSS
`deploy/oss/.env.example` opts in to the team settings, and each section says which variable does
what.

| Setting | Default | Team install | Section |
|---|---|---|---|
| `ATLAS_AUTH_PROVIDERS` | `local` | `oidc` or `local,oidc` | [OIDC](#single-sign-on-with-oidc) |
| `ATLAS_LOCAL_LOGIN` | `all` | `break-glass` | [Break-glass](#break-glass-accounts) |
| `ATLAS_RBAC` | `off` | `projects` | [RBAC](#roles-on-projects-rbac) |
| `ATLAS_DEFAULT_PROJECT_VISIBILITY` | `shared` | `private` | [Visibility](#project-visibility) |
| `ATLAS_SHARED_PROJECT_ROLE` | `editor` | `viewer` | [Visibility](#project-visibility) |
| `ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER` | `true` | `false` | [Instance roles](#instance-roles) |

A malformed value in any of these stops the boot, and the error names the variable.

## Accounts

There are two kinds of account, both in one table:

- **People.** A person signs in with a password (a local account) or through the identity provider
  (an OIDC account).
- **Agents.** An agent is an `is_service` account with no usable password. It authenticates with an
  `atlas_svc_` token and normally belongs to a person (see
  [What an agent can do](#what-an-agent-can-do-as-its-user)).

Each account is keyed on the pair (identity provider, subject), for example `local` plus a
username, or `oidc` plus the IdP's stable id. It is never keyed on an email address or a display
name. Usernames are permanent.

### Local accounts

Local accounts are made by an operator. There is no self-registration and no reset-by-email.

```bash
docker compose exec atlas-api atlas-admin user add example-user --role member --email user@example.com
```

The command prints a temporary password once. At first sign-in the person must choose their own
password before they can do anything else. That requirement is enforced at the gate, on every
request. Passwords must be at least 8 characters, and they are checked against a bundled list of
breached passwords.

Other `atlas-admin user` commands:

- `reset-password` issues a new temporary password and ends that person's sessions. It does not
  touch their agents' tokens.
- `disable` stops the person and every agent linked to them. `enable` deliberately does not
  re-enable the agents.
- `list` and `show` print accounts and never print a secret.

### Instance roles

Every account has one instance role:

- **`owner`** administers the instance: accounts, groups, access, and the audit export.
- **`member`** confers nothing beyond what visibility and project roles give.

An unrecognised stored value resolves to `member`. Accounts created through OIDC are always
`member`. Nobody becomes an owner through the identity provider.

With project RBAC on, an owner is also the instance admin, who may bind roles on any project. While
`ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER=true` (the default), an owner is also treated as
`owner` on every project. Set it to `false` and an admin who needs to read a project binds
themselves first, and that binding is audited.

## Single sign-on with OIDC

With `oidc` in `ATLAS_AUTH_PROVIDERS`, Atlas is a confidential OpenID Connect client of one issuer
(`backend/identity/oidc.py`). The login page shows **Sign in with SSO**. With `local,oidc` it also
shows the password form.

**The flow** is the authorization code flow with PKCE (S256), `state` and `nonce`:

1. `GET /auth/oidc/start` stores the flow server-side: a digest of `state`, the nonce and the PKCE
   verifier. The flow is bound to the browser's pre-login session and lives for ten minutes.
2. The IdP redirects back to `/auth/oidc/callback`. The flow row is deleted on first sight, so a
   replayed callback finds nothing, and a callback in another browser finds no flow.
3. Atlas redeems the code with the verifier and its client credential: `client_secret_basic`,
   `client_secret_post`, or `private_key_jwt`.
4. The ID token is verified:
   - The signature must be from the issuer's JWKS, with an asymmetric algorithm only (never `none`,
     never HS*).
   - `iss` must equal the issuer exactly.
   - `aud` must contain the client id, and `azp` must match it when present.
   - `exp`, `iat` and `sub` must be present, with 60 seconds of leeway.
   - The nonce is compared in constant time.
5. Atlas issues its own `__Host-` session, as a password login does. The IdP's tokens are used
   once, in memory, and never stored.

Every refusal gives the caller the same generic failure. The reason goes to `auth_event`. Discovery
is fetched lazily and cached, so Atlas boots, and break-glass login works, while the IdP is down.

**Settings.** The full list, with validation rules, is in [Configuration](../CONFIG.md).

| Variable | Meaning |
|---|---|
| `ATLAS_OIDC_ISSUER` | the `https` issuer URL; discovery is read from it |
| `ATLAS_OIDC_CLIENT_ID` | Atlas's client id at the IdP |
| `ATLAS_OIDC_CLIENT_SECRET_FILE` | the client secret, read **only** from a file |
| `ATLAS_OIDC_CLIENT_KEY_FILE`, `ATLAS_OIDC_CLIENT_KEY_ID` | the private key and its id for `private_key_jwt`, instead of a secret |
| `ATLAS_OIDC_TOKEN_AUTH` | `client_secret_basic`, `client_secret_post` or `private_key_jwt` |
| `ATLAS_OIDC_REDIRECT_URI` | `https://<host>/auth/oidc/callback`; derived from `ATLAS_PUBLIC_URL` if unset |
| `ATLAS_OIDC_PRESET` | `generic`, `entra` or `keycloak`: which claims hold the subject, name, email and groups |
| `ATLAS_OIDC_NAME_CLAIM`, `_EMAIL_CLAIM`, `_GROUPS_CLAIM` | override one claim name of the preset |
| `ATLAS_OIDC_REQUIRED_GROUPS` | IdP group values; see below |
| `ATLAS_OIDC_GROUP_MAP_FILE` | the group allowlist; see [IdP group sync](#idp-group-sync) |
| `ATLAS_OIDC_MAX_SESSION_HOURS` | absolute lifetime of an SSO session (12) |
| `ATLAS_OIDC_REVALIDATE_DAYS` | how long an SSO person's agent tokens keep working without a fresh login (30) |
| `ATLAS_OIDC_STEP_UP`, `ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS` | SSO people create their own agent credentials, and SSO owners change configuration, after a fresh IdP sign-in (off; 300 s); see [Agent credentials for SSO people](#agent-credentials-for-sso-people) and [Configuration changes by SSO owners](#configuration-changes-by-sso-owners) |

**Required groups and just-in-time accounts.** Once `ATLAS_OIDC_REQUIRED_GROUPS` is set, every SSO
login, not only the first, needs at least one of those groups in the token. A leaver removed from
the group is refused at their next login. A person with no Atlas account who passes that check is
created on the spot as a `member`. If `ATLAS_OIDC_REQUIRED_GROUPS` is unset, no account is ever
created by a login, and only existing linked accounts can sign in through SSO.

**Moving a local person to SSO.** `atlas-admin user link-idp <username> --subject <id>` points an
existing local account at an IdP identity. The person keeps their history and agents. Their old
username is kept as the account's **local sign-in name**, which only matters for the
[password fallback](#a-personal-password-as-a-fallback-when-sso-is-down). Add `--no-local-name`
for an account that must never sign in with a password.

**SSO-only, or SSO with a personal password.** An account for someone who will sign in through SSO
is made in one of two ways:

- **SSO-only:** `atlas-admin user add <name> --no-password`, then `user link-idp`. The account has
  no Atlas password at all and never gets one.
- **SSO with a personal password:** `atlas-admin user add <name>` as usual, then `user link-idp`.
  The temporary password the first command printed is never used: hand it to nobody. The account
  keeps the "must choose a password" flag, and `link-idp` does not clear it.

The forced password change applies to **password sign-ins only**. Atlas records how each session
began (`auth_session.auth_method`: `password`, `sso`, or `minted` for `session mint`). The gate
reads that, not the kind of account. A session made through the IdP
reaches the board even while the flag is set. In that session the board shows a notice, *You have
no personal password yet*, which can be dismissed. **Set a password** sends the person through a
fresh IdP sign-in (the [step-up](#agent-credentials-for-sso-people), so `ATLAS_OIDC_STEP_UP=on` is
needed; without it the notice says the board cannot set one), and back on the board they choose the
password without the temporary one. See
[A personal password for SSO people](#a-personal-password-for-sso-people).

An account that signs in with its temporary password is still sent to the change page first. That
holds for a local account, as before, and for a linked account signing in under its local name (see
the fallback below). Installs without OIDC are unchanged.

### A personal password as a fallback when SSO is down

A linked account whose person has set a personal password (see
[A personal password for SSO people](#a-personal-password-for-sso-people)) can sign in with its
**local sign-in name** and that password. This is the fallback for when the IdP is down, and for
testing without SSO. Whether it is allowed depends on the providers and on `ATLAS_LOCAL_LOGIN`:

| `ATLAS_AUTH_PROVIDERS` | `ATLAS_LOCAL_LOGIN` | A linked account's password |
|---|---|---|
| `local,oidc` | `all` (the default with `local`) | signs in under its local name, if the account has a password |
| `local,oidc` | `break-glass` | signs in only if the account is flagged break-glass, at `/auth/login?break_glass=1`; each use writes its own `login_break_glass` row |
| `oidc` | `break-glass` (implied) | does not sign in |
| `local` (no OIDC) | `all` | does not sign in; nothing changes |

- **The local name.** `user link-idp` keeps the username the account had before it was linked. An
  account linked before this existed, or created by a first SSO login, gets one with
  `atlas-admin user set-local-name <idp>:<subject> <name>`. `user set-local-name <account> --clear`
  removes it. Both are `user_local_name` rows in `auth_event`.
- **One namespace.** A local name is a username. `set-local-name` refuses a name that a local account or an
  agent already has, or that another linked account uses as its local name. `user add` and
  `bot add` refuse a linked account's local name. If a local account and a local name ever clash
  anyway, the local account wins its own name, and the linked account cannot use the fallback.
- **What it is.** A password sign-in. The session is a `password` session, so a temporary password
  forces the change first, and an SSO-only account (`--no-password`) still has no password at all.
  `login_ok` carries `local_name=<name>` in its detail.
- **Offboarding.** Removing the person at the IdP does not stop a password. Disable the account in
  Atlas too (`user disable`), or clear its local name. A password sign-in is not an IdP login, so it
  does not lift the [agent token suspension](#what-an-agent-can-do-as-its-user).
- **Where it shows.** `user list` adds a `local_name` column once any account has one. For an
  IdP-backed account, `user show --json` has a `local_name` key. Admin verbs also accept the local
  name to find the account.

### Microsoft Entra ID

- Set `ATLAS_OIDC_PRESET=entra`. The issuer must be a **tenant** issuer,
  `https://login.microsoftonline.com/<tenant-id>/v2.0`. The multi-tenant `common` and
  `organizations` issuers are refused, because they would let another tenant's users in.
- The subject is the user's object id (`oid`). The token's tenant id (`tid`) must equal the
  issuer's tenant, or the login is refused. For `link-idp`, pass the user's Object ID.
- With `private_key_jwt`, set `ATLAS_OIDC_CLIENT_KEY_ID` to the certificate's base64url SHA-1
  thumbprint. Atlas sends it as `x5t`.
- With `ATLAS_OIDC_STEP_UP=on`, add the optional claim `auth_time` to the ID token in the app
  registration (Token configuration). Without it every step-up is refused with
  `step_up_auth_time_missing` (see [Agent credentials for SSO people](#agent-credentials-for-sso-people)).
- **Group overage.** Past about 200 groups, Entra leaves the groups claim out and sends a pointer
  instead. Atlas cannot tell which groups that hides, so it refuses the login (fail closed). In the
  app registration, emit only the groups assigned to the application.
- In the group map, key Entra groups by their object id.

### Keycloak

- Set `ATLAS_OIDC_PRESET=keycloak` and use the realm issuer,
  `https://<keycloak-host>/realms/<realm>`.
- The subject is `sub`. The name comes from `name`, falling back to `preferred_username`. The
  email comes from `email`.
- Add a group membership mapper so the ID token carries a `groups` claim. The map file keys
  Keycloak groups by the values that claim carries, typically the group path, such as
  `/atlas/maintainers`.

### Break-glass accounts

With SSO on, local password login stays open to every local account by default
(`ATLAS_LOCAL_LOGIN=all`). For a team install, set `ATLAS_LOCAL_LOGIN=break-glass`. Then only
local accounts created with the break-glass flag can use a password:

```bash
docker compose exec atlas-api atlas-admin user add breakglass --role owner --break-glass
```

- The password form is no longer linked from the login page. It is reachable only at
  `/auth/login?break_glass=1`.
- Every break-glass login writes its own `login_break_glass` row to `auth_event`.
- Keep the credential in your organisation's vault. It is how you administer Atlas when the IdP is
  down or misconfigured.

If `local` is not listed in `ATLAS_AUTH_PROVIDERS`, `ATLAS_LOCAL_LOGIN` defaults to `break-glass`.

A break-glass owner who is later linked keeps the flag. With `local,oidc` and
`ATLAS_LOCAL_LOGIN=break-glass`, it signs in under its local name
([the fallback](#a-personal-password-as-a-fallback-when-sso-is-down)).

## IdP group sync

Groups are where access is managed at scale (`backend/identity/groups.py`).

- **Local groups** are made by an instance owner, in the access screen or with
  `POST /api/access/groups` and `PUT /api/access/groups/{id}/members`. They suit installs without an
  IdP, and exceptions the IdP does not model.
- **IdP groups** are synchronised **at every SSO login**. The person's IdP memberships are replaced
  with what the token's groups claim says. Local memberships are never touched by sync.
- **The allowlist** in `ATLAS_OIDC_GROUP_MAP_FILE` is the only way an IdP group reaches Atlas. A
  claim value that is not in the map is ignored and never created. The file is YAML (or JSON):

  ```yaml
  # IdP group value -> Atlas group name
  groups:
    "3f6c1c1e-6f4d-4a59-9a7e-0f1c2d3e4f50": team-alpha      # Entra: the group's object id
    "/atlas/maintainers": maintainers                       # Keycloak: the group path
  ```

  A mapped Atlas group that does not exist yet is created. A mapping onto an existing group adds
  IdP memberships to it. With no map file, nothing is synchronised, and a person's IdP memberships
  are emptied at their next login.
- Every membership added or removed is written to `auth_event`.
- Sync is part of the login. A membership change in the IdP takes effect at the person's next
  login, so it is bounded by `ATLAS_OIDC_MAX_SESSION_HOURS`.

Sync creates memberships, not access. Bind the group to projects as described in the next section.

## Roles on projects (RBAC)

Set `ATLAS_RBAC=projects` to turn on project-scoped roles (`backend/rbac.py`). With it `off`, the
default, none of this code runs. The instance roles and per-user grants then work as described under
[Project visibility](#project-visibility).

**The project is the unit.** A node's project is its nearest `contains` ancestor of type `project`,
counting the node itself. A root of any other type is its own project. A work item follows the node
it works on. A node with several parents belongs to all their projects, and every check must pass
on all of them. A node whose project cannot be resolved has no project, and so no role on it: it is
refused.

**Four ordered roles**, `viewer < editor < maintainer < owner`:

| Role | May |
|---|---|
| `viewer` | read every node, document, intent, event and search result in the project |
| `editor` | also create and change nodes, work items, edges, documents, intents, acceptance and results |
| `maintainer` | also **delete** nodes and edges, and grant roles up to `maintainer` |
| `owner` | also grant `owner`, and delete the project root |

An unrecognised stored role resolves to no role, not to `viewer`.

**Roles are granted to groups**, and to individual people, with
`PUT /api/access/project/{project_id}/bindings` or the access screen. Only a signed-in person can do
this, never a token. Who may grant:

- an instance owner may grant anything;
- a project maintainer may grant up to `maintainer`;
- a project owner may grant up to `owner`.

**A person's role on a project** is the highest of:

- their own bindings on it;
- the bindings of every group they belong to;
- `editor`, if they hold a per-user grant (`node_grant`) on the project root;
- `ATLAS_SHARED_PROJECT_ROLE`, if the project is shared.

### Who owns a new project

**The person who creates a project root owns it.** With RBAC on, creating a node that is a project
root binds its creator `owner` on that project, in the same transaction. A person is bound themself.
An agent's **linked person** is bound, never the agent, so the person sees what their agent made and
the agent works there within its token's cap. Two creations make a root:

- `POST /api/node`, which creates no edge, so the node is a root until a `contains` edge gives it a
  parent;
- `POST /api/workitem` with neither `parent` nor `binding`, because an unbound work item is its own
  project. This is how the board files work and how an agent's `atlas_file_work` does.

**An agent with no linked person cannot create a project root.** It gets a `403` with
`"reason": "agent_not_linked_to_a_person"` and nothing is written. Link it to a person
(`atlas-admin bot link`). A work item can instead be filed under a parent or a binding, and is then
not a root. `POST /api/node` takes no parent, so for a node linking the agent is the only way.

Each of these bindings is recorded in `auth_event` as `project_role_bound`, the same entry the
bindings screen writes, with the caller (the agent, if an agent created it) as the actor. A node
that becomes a root later, because its last parent edge was deleted, gets no automatic owner: an
instance admin binds one. With RBAC off, creation binds nobody, as before.

The reason: a root is its own project, and on a private-by-default install a project nobody is bound
to is unreachable, so without this rule a create would answer `201` and the next read of the same
node `404`. The person, not the agent, is bound because the person answers for what their agent
makes, and no token ever exercises `owner`: the agent stays capped by its token's scope.

**Deleting needs `maintainer`** on the project of every row removed, and on both ends of an edge.
Deleting a project root needs `owner`. A token's `admin` scope alone is not enough.

A refused write is a `403` naming the role needed and the role held. A node the caller cannot see is
still a `404`.

**Turning it on.** Run `atlas-admin rbac plan` first. It writes nothing and prints, for every
principal and project, today's effective permission next to the one RBAC would give. The usual
difference is that agents with an `admin` token can delete only where their person is a
`maintainer`. Add those bindings, then set `ATLAS_RBAC=projects`. Turning it off again restores the
previous behaviour, because nothing was converted.

## Project visibility

**RBAC off (the default).** Every node is shared unless a subtree is marked private. A private
subtree is visible to:

- instance owners;
- people granted it by name (`node_grant`);
- those people's agents.

An instance owner sets visibility and grants in the access screen, or with
`PUT /api/access/node/{node_id}`, which also needs an `admin` scope from a token.

**RBAC on.** A project's visibility is the mark on its root, or `ATLAS_DEFAULT_PROJECT_VISIBILITY`
when its root has no mark.

- **Private project:** only principals with at least `viewer` on it, through a binding, a group or
  a root grant, can see it.
- **Shared project:** every active account gets the role `ATLAS_SHARED_PROJECT_ROLE` there. That is
  `editor` by default, which matches the RBAC-off behaviour. A team install sets `viewer`, so that
  editing needs an explicit binding.
- **Inner subtrees:** a private mark on a subtree inside a project still narrows visibility exactly
  as with RBAC off. Even a project viewer needs a grant to see inside that subtree.

**A refusal looks like an absence.** A node you may not see answers `404`, the same as an id that
does not exist. Search, listings, counts and rollups are computed over the caller's visible set, so
they do not leak hidden nodes. If an agent insists a project does not exist that its person can see,
check the agent's access before suspecting a bug.

## What an agent can do as its user

An agent is its own account, linked to the person it belongs to (`person_user_id`). Every write it
makes is recorded against the agent, and so against that person.

**Creating one.** A signed-in person opens Settings, Agent credentials, or calls
`POST /api/me/agent-credential`. This creates the agent account, links it to them and mints its
first token, under these rules:

- It works only from a browser session. A token cannot mint tokens.
- The person must re-enter their password, or, for an SSO person on an install with
  `ATLAS_OIDC_STEP_UP=on`, sign in at the IdP again (below).
- The scopes are capped by the person's instance role: a `member` gets at most `read` and `write`,
  an `owner` can also have `admin`.
- The token must have an expiry. There is no never-expires option.

### Agent credentials for SSO people

**IdP sign-in is the default for an SSO install.** With `ATLAS_OAUTH_RESOURCE` set, an MCP client
such as Claude Code signs in through the IdP and needs no minted token (see IdP access tokens
below). For a client that needs a minted `atlas_svc_` token, an SSO person mints it themselves after
a fresh IdP sign-in, once the operator sets `ATLAS_OIDC_STEP_UP=on`.

**Why a step-up.** Minting re-authenticates the person, because a token minted from a browser
session alone would let a stolen session create a key that outlives it. A local account re-enters
its password. An SSO account has no Atlas password, so without step-up the screen refuses it with
`403 password_incorrect`. With step-up on, it signs in at the IdP again instead:

1. In Settings, Agent credentials, the person names the credential first: the purpose of a new
   agent, or the existing agent to rotate. The screen says what they are about to approve: *You
   are creating an agent credential named `<name>` for `<account>`*. **Confirm with SSO** goes to
   `GET /auth/oidc/step-up?purpose=<purpose>` (or `?agent=<name>`). It needs a signed-in browser
   session; any token is refused with `session_required`.
2. That route answers a page that names the credential and the account again, with a link to the
   IdP. Nothing is sent to the IdP until the person follows it. Behind the link is the same
   authorization code flow as a login, with the same `state`, nonce and PKCE checks, plus
   `prompt=login` and `max_age`, so the IdP asks for the person's credentials even if they are
   signed in there. The flow is bound to the person's Atlas session, not to a pre-login one, and
   Atlas records the name being approved beside it.
3. The IdP returns to the usual `/auth/oidc/callback`, so no second redirect URI is registered.
   Atlas checks everything a login checks, and also:
   - the ID token carries `auth_time` (refused otherwise, with the error
     `step_up_auth_time_missing`; see below);
   - `auth_time` is not older than the start of this flow, so a sign-in the IdP answered from an
     older IdP session is refused;
   - `auth_time` is at most `ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS` old (300 by default, 60 to 900);
   - the person who signed in is the person whose session this is, and is still in a required
     group;
   - no later step-up was started in the same session (`step_up_superseded` otherwise).
4. Atlas makes the step-up live (`auth_step_up`; no IdP token is kept) and answers a page that
   says once more what was approved, with a link back to the board. No new session is issued.
5. The credential with **that name**, created in that session, spends it: a first issue or a
   replacement, once, until `auth_time` plus the window. A credential with any other name is
   refused with `403 step_up_agent_mismatch` (the step-up is kept for the approved one).

**Why the name is part of the approval.** Someone who has stolen a session cookie could send its
owner the step-up link. The pages before and after the IdP both say which credential, for which
account, is being approved, and the mint cannot be turned to another name, so the person is never
asked to approve something they cannot see.

**Agent names.** A new agent is called `<base>-<purpose>`. For a local account the base is the
username. For an SSO account it is the local part of the person's email, so an Entra or Keycloak
person whose subject is an opaque id still gets a readable name, and the same name that
`atlas-admin token mint` gives. An SSO account with no email yet falls back to its subject.

**Errors.** A refused step-up is a `login_fail` in `auth_event` with reason `oidc+step-up:<why>`; a
success is `step_up_ok`, naming the agent. A mint without a live step-up is `403
step_up_required`; one for another name is `403 step_up_agent_mismatch`.

**`step_up_auth_time_missing`.** The IdP's ID token has no `auth_time` claim, so Atlas cannot tell
that the sign-in is fresh, and every step-up is refused. The person sees a page naming this error,
and atlas-api logs a warning telling the operator what to change. Fix it at the IdP: with Entra
ID, add the optional claim `auth_time` to the ID token (app registration, Token configuration);
Keycloak sends it by default. The `auth_event` reason is `oidc+step-up:auth_time_missing`.

Local accounts, including break-glass owners, keep the password prompt unchanged. An SSO account
that still holds a password from before `link-idp` is asked for the IdP sign-in, not the password.

### A personal password for SSO people

An account made with `atlas-admin user add` (a temporary password) and then `user link-idp` signs
in through SSO with the "must choose a password" flag still set. With `ATLAS_OIDC_STEP_UP=on`:

1. `GET /api/me` carries `password_setup` (`step_up: "sso"`, the step-up route, the time left on a
   confirmed step-up, and the window). It is absent for everyone else, including SSO-only accounts.
2. **Set a password**, then **Confirm with SSO**, goes to `GET /auth/oidc/step-up?password=1`. The
   page says *You are setting a personal Atlas password for `<account>`, without the temporary
   one*, with the link to the IdP. An account with nothing pending (SSO-only, or one that already
   set its password) is refused with `400 password_not_pending` before any sign-in.
3. Back on the board, within the window, `POST /api/me/password` with `new_password` and
   `confirm_password` sets it and spends the step-up. The usual password rules apply (`400
   password_policy`, `400 password_mismatch`; neither spends the step-up). The flag is cleared, the
   account's other sessions are ended, and `auth_event` records `password_changed` with reason
   `sso_step_up`.

**Refusals.** Without a live step-up the answer is `403 step_up_required`, naming `step_up_url`,
recorded as a `login_fail` with reason `password_set:step_up_required`. A step-up approved for an
agent credential or a setting cannot set a password, and one approved for a password cannot do
either of those (`403 step_up_required`, reason `<route>:step_up_purpose_mismatch`, the step-up kept
for what it approved). Only the session that made the step-up can spend it, and a token, whatever
its scopes, is refused with `403 session_required`. A local account changes its password at
`/auth/password/change`, with the current one (`400 step_up_not_applicable` here).

The password then signs the account in under its local name where the install allows it. See
[A personal password as a fallback when SSO is down](#a-personal-password-as-a-fallback-when-sso-is-down).

### Configuration changes by SSO owners

With `ATLAS_CONFIG_WRITES=1`, an owner changes a few settings from the board's Configuration tab
(see [The configuration panel](configuration-panel.md)), and every change re-authenticates: a local
owner re-enters their password each time. An owner who signs in only through SSO has no Atlas
password. Nobody becomes an owner through the IdP, so such an owner is a local owner moved to SSO
with `atlas-admin user link-idp`. With `ATLAS_OIDC_STEP_UP=on`, they confirm each change through
the same step-up as an agent credential:

1. **Change**, then **Confirm with SSO**, goes to `GET /auth/oidc/step-up?config=<setting>`. Atlas
   first refuses what the change itself would refuse: a member (`403 forbidden`), an unknown
   setting (`404`), a bootstrap setting, a secret, or one not marked changeable (`403`, each with
   its named error), and `config_writes_off` while `ATLAS_CONFIG_WRITES` is off.
2. The page says *You are changing the setting `<setting>` of this Atlas install, as `<account>`*,
   with the link to the IdP. The flow, its checks and the window are the ones above.
3. Back on the board, `GET /api/config` reports `writes.step_up: "sso"`, the confirmed setting and
   the time left. `PUT /api/config/<setting>` then needs no password, and spends the step-up.

**The step-up is bound to one setting and one purpose.** It approves a change of the setting it
names, once, in the session that made it. A change of another setting is refused with `403
step_up_setting_mismatch`. A step-up approved for an agent credential cannot change a setting, and
one approved for a setting cannot create an agent credential: each is refused with `403
step_up_required`, recorded with the reason `<route>:step_up_purpose_mismatch`. In every refusal the
step-up is kept for what it approved. A change that leaves the value as it was spends nothing.

**Errors.** A change without a live step-up is `403 step_up_required`, naming `step_up_url`, and is
a `login_fail` in `auth_event` with reason `config_write:step_up_required`. A success is
`step_up_ok` with detail `config=<setting>`, and the change itself is a `node.config.set` event as
for any owner. A password an SSO owner sends is ignored.

Without step-up, an SSO owner cannot pass the password prompt and changes configuration in the
server's environment instead. Local owners, including break-glass owners, keep the password prompt unchanged.

**Without step-up**, or for a person who cannot use the board, an operator mints the token:

```bash
docker compose exec atlas-api atlas-admin token mint --user <idp>:<subject> \
    --purpose claude-code --scopes read --out /path/to/new-token-file
```

Once the person has signed in through SSO at least once, `--user` also accepts their email. The same
rules apply as in Settings: the scopes are capped by the person's role, and the token must have an
expiry. The raw token is written only to the new file named by `--out`, never printed.

[Agents](agents.md) covers the client side.

Operators can also create agents with `atlas-admin bot add`, `bot link` and `token add`.

**What it may see** (RBAC off): its person's grants plus its own. It never gets its person's
instance role. An owner's agent therefore sees shared projects and whatever has been granted
explicitly, not everything its owner sees. This is deliberate: linking an agent to an owner makes
the agent less capable, not more.

**What it may do** (RBAC on): `min(scope cap, person's project role)`, decided per project on every
request.

| Token scope | Caps the agent at |
|---|---|
| `read` | `viewer` |
| `write` | `editor` |
| `admin` | `maintainer` |

The agent inherits its person's direct and group bindings and grants, and never their instance role.
**No token ever exercises `owner`**, so granting roles is always a human act. An agent with an
`admin` token deletes only where its person is a maintainer. An agent with no linked person has its
own direct bindings only, and no group membership.

**Scopes still gate writes in both modes.** A `read` token cannot write, and access administration
and deletes always need `admin`.

**When the person leaves:**

- Disabling a person disables every agent linked to them.
- For an SSO person, the agents' tokens are also suspended once the person has gone
  `ATLAS_OIDC_REVALIDATE_DAYS` without an SSO login. The tokens are not deleted, and the person's
  next login lifts the suspension.
- Removing the person from the required group in the IdP stops their next login. That stops the
  revalidation, so their agents' tokens lapse too.

**IdP access tokens (opt-in).** With `ATLAS_OAUTH_RESOURCE` set, which needs OIDC, an MCP client may
present an access token issued by the IdP instead of an `atlas_svc_` token. Atlas validates it in
the API: signature, issuer, an audience that is the MCP resource, and expiry. It maps the IdP scopes
to `read`, `write` or `admin` with `ATLAS_OAUTH_SCOPE_MAP`, capped by the person's role. It acts
through one agent account per person and OAuth client, created on first use and linked to the
person. The same rules apply: scope cap ∩ project role, and the same offboarding. A token for
someone who has never signed in with SSO is refused. A pending password change does not refuse
it. The IdP has verified the person, so their token follows the rule of their SSO session, with
the scopes it would otherwise get.

**What is recorded.** Every write is in `graph_event` under the agent's account. With
`ATLAS_AUDIT_READS=1`, every node an agent reads is in `audit_read`, with the person, the agent, the
token and the MCP tool. See [Security](../SECURITY.md#audit-mutations-logins-and-reads).
