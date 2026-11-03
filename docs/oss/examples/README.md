---
audience: operations-security
---

# Configuration examples

Ready-made configuration for common install shapes. Copy what fits, change the placeholder names
(everything under `example.org`) to your own, and apply it as described below. None of these
files holds a secret: a secret is always a file under `deploy/oss/secrets/`, and the examples name
only its path.

## How the files relate

- The **`.env` profiles** in `env/` are fragments. Each one is layered over
  `deploy/oss/.env.example`, the shipped work-install configuration. Always apply
  `env/minimal.env` first, then any of the others. They can be combined, except that only one
  profile may set `COMPOSE_PROFILES` (write `COMPOSE_PROFILES=gateway,backup` for both).
- The **guardrails matrix** in `guardrails/` says what agents may do in each of your environments.
  The **CI/CD answers** in `cicd/` and the matrix together generate your first pipeline. The
  **crunch-week configuration** in `crunch/` names environments of the matrix, so it is checked
  against it.
- The file in `sso/` belongs to `env/sso-rbac.env`: the group allowlist it names. The compose
  side is not copied here: each profile that needs more than `docker-compose.yml` names, in its
  first comment, the shipped overlays in `deploy/oss` it starts with (`oidc.yml`,
  `retrieval.yml`, `retrieval-workload-identity.yml`), and the check runs exactly those.

To layer `.env` fragments, run this in `deploy/oss` on a fresh copy of the example:

```sh
cp .env.example .env
python3 ../../tools/check_examples.py apply ../../docs/oss/examples/env/minimal.env \
    ../../docs/oss/examples/env/sso-rbac.env --env .env
```

`apply` replaces a line that already sets the same name and adds the new names at the end. It
needs nothing beyond Python. You can also do the same by hand: for each line of the fragment,
change that setting in `.env` or add it.

Every file here is validated on each change by `.github/workflows/examples.yml`, using the same
functions Atlas uses to read it (`tools/check_examples.py` explains each check). CI also boots
the minimal profile and signs in.

## The `.env` profiles

### `env/minimal.env`

**When:** every install. It sets the public name, the URL, the names `atlas-mcp` answers on, and
the privacy contact. Everything else stays as `.env.example` ships it: project roles, private by
default, local accounts, secrets as files only.

**Apply:** layer it over `.env.example`, replace `atlas.example.org` and
`privacy@example.org` with your own, then run `bash ../../scripts/init-secrets.sh` and
`docker compose up -d` as [Install](../INSTALL.md) describes.

### `env/sso-rbac.env`

**When:** people sign in through your identity provider (OIDC, here Keycloak; Entra ID works the
same way with its preset) and access is granted per project. Password login is left for
break-glass accounts only.

**Apply:** register Atlas in the provider first (redirect URI
`https://<your host>/auth/oidc/callback`). Layer the profile after the minimal one and set your
issuer, client id and group names. Write the client secret to
`deploy/oss/secrets/oidc_client_secret`, copy `sso/oidc-group-map.yml` to
`deploy/oss/secrets/oidc_group_map.yml` and edit it, then start with the shipped SSO overlay:
`docker compose -f docker-compose.yml -f oidc.yml up -d`. The overlay mounts the group map at
`/run/secrets/oidc_group_map` (no `.yml`), which is what the profile names, and gives
`atlas-api` its route to the provider. The profile also turns on step-up
(`ATLAS_OIDC_STEP_UP=on`): an SSO person creates their own agent credentials by signing in again
at the provider, within `ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS` (60-900, default 300).
Create a break-glass owner with `atlas-admin user add` before you switch, as
[Identity and access](../guides/identity-and-access.md) explains.

### `env/retrieval-hosted.env`

**When:** you want semantic search and use a hosted embeddings service (Azure OpenAI or Azure AI
Foundry).

**Apply:** layer it after the minimal profile, set your endpoint URL, model and dimension, write
the key to `deploy/oss/secrets/embed_key`, and start with the retrieval overlay:
`docker compose -f docker-compose.yml -f retrieval.yml up -d`. `/api/health` then reports
`retrieval: on`. See [Retrieval](../guides/retrieval.md).

### `env/retrieval-entra.env`

**When:** your hosted embeddings endpoint accepts only Entra ID tokens, and your host gets them
through a federated workload identity (AKS or another federated host).

**Apply:** layer it after the minimal profile, set your endpoint, tenant id, client id and the
host directory that holds the federated token, and start with both overlays:
`docker compose -f docker-compose.yml -f retrieval.yml -f retrieval-workload-identity.yml up -d`.
No key file: `ATLAS_EMBED_KEY_SOURCE=/dev/null` makes the key secret an empty mount. With a
managed identity instead (an Azure VM, App Service, Container Apps), leave out the
workload-identity overlay and the three `AZURE_*` lines.

### `env/retrieval-local.env`

**When:** you want semantic search and run your own OpenAI-compatible embedding server on your
network, with a certificate from this install's CA.

**Apply:** as the hosted profile: layer it, set your server's URL, model and dimension, write its
key to `deploy/oss/secrets/embed_key`, and start with `retrieval.yml`. Keep
`ATLAS_EMBED_CA_BUNDLE` when the server's certificate comes from the install CA; remove it when
the certificate comes from a public CA.

### `env/gateway.env`

**When:** agents should reach Atlas through the MCP gateway on its own port, for example to put
other MCP servers behind the same door.

**Apply:** layer it after the minimal profile, run `python3 ../../scripts/render-gateway.py` in
`deploy/oss`, then `docker compose up -d`. Agents connect to `https://<your host>:8444/mcp`.

### `env/backup.env`

**When:** you want a routine backup (here daily, keeping the last 7), and backups during a crunch
week with a backup policy of `once`, `hourly` or `daily`.

**Apply:** layer it after the minimal profile and `docker compose up -d`. Backups land in the
`atlas-backups` volume, or in a host directory once you set `ATLAS_BACKUP_HOST_DIR` (commented in
the profile). `ATLAS_BACKUP_SCHEDULE` takes `off`, `hourly`, `daily` or `weekly`;
`ATLAS_BACKUP_KEEP` is 1-1000 (default 7) and deletes only older routine backups. Restore with
`docker compose run --rm --no-deps atlas-backup atlas-admin backup restore latest` (`--dry-run`
first; `atlas-admin backup rollback` undoes it), as [Operations](../guides/operations.md)
describes. For the crunch `per_deploy` policy the pipeline runs the backup instead.

## Guardrails, CI/CD and crunch week

### `guardrails/dev-staging-prod.yaml`

**When:** your work moves through three environments, dev, staging and production, and you want
agents to work freely in dev, propose in staging and only read in production.

**Apply:** rename the environments and branches to yours, copy the file into the `atlas-api`
container and run `atlas-admin guardrails validate`, then `atlas-admin guardrails apply` (with
`--dry-run` first). Agents then read one rule per environment on the install's system node.

### `cicd/answers.yaml`

**When:** you want the first CI/CD pipeline generated for you instead of writing one by hand.
These are the answers the setup guide asks for: which checks run, which ones block a deploy, and
when production deploys.

**Apply:** with the matrix above,
`atlas-admin cicd generate --platform github --matrix dev-staging-prod.yaml --answers answers.yaml --out ./generated`
(or `--platform gitlab` or `azure`). Review the generated files, configure the platform settings
the generated settings file lists, and commit them.

### `crunch/crunch-week.yaml`

**When:** for a time-boxed push in which production deploys straight after green CI, without the
staging step, with a backup before every production deploy.

**Apply:** apply the guardrails matrix first; crunch week checks the environments it skips
against the matrix in force. An instance owner then switches it on from the board, with their
password. The fields of this file are what the board sends. It starts at once and ends by itself
after `duration_hours`.
