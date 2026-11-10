---
audience: operations-security
---

# Operations

This guide covers running an Atlas install day to day: backups and restores, upgrades, rotating
credentials, renewing certificates, the MCP gateway, logs and health, the audit export, feature
switches and keeping up with patches.

It assumes the install described in [INSTALL.md](../INSTALL.md): the files in `deploy/oss/`, a
`.env` copied from `.env.example`, and the secrets under `deploy/oss/secrets/`. Every setting named
here is described in [CONFIG.md](../CONFIG.md). The security model behind tokens, peppers and the
audit trail is in [SECURITY.md](../SECURITY.md).

**Where commands run.** Every command below runs in `deploy/oss/` on the VM, unless it says
otherwise.

**Overlays.** If you added `retrieval.yml` ([INSTALL section 8](../INSTALL.md#8-optional-semantic-retrieval))
or the OIDC overlay `oidc.yml` ([INSTALL section 9](../INSTALL.md#9-optional-single-sign-on-oidc)),
pass the same `-f` files to every `docker compose` command in this guide, or list them once in
`COMPOSE_FILE` in `.env`, for example `COMPOSE_FILE=docker-compose.yml:retrieval.yml:oidc.yml`. A
command run without them recreates `atlas-api` without its key, its route out or its sign-in
secrets.

**What is tested.** Each section says what CI proves and what it does not. A step marked
*untested* has no tooling in the repository and no CI job behind it: it is a careful generic
procedure, and you should rehearse it on a copy before you rely on it. The
[summary at the end](#what-ci-tests-and-what-it-does-not) lists both.

## 1. Backup

### What to back up

A backup of Atlas is two things, and they are only useful together:

| What | Where | Why |
|---|---|---|
| The database | the `atlas-pgdata` volume, taken as a `pg_dump` | all nodes, edges, work items, documents, accounts and the audit trail |
| The secrets | `deploy/oss/secrets/` (all of it, including `tls/`) | the two peppers verify every password, session and token. A restored database without them cannot sign anyone in, and no token works |

Keep these with them:

- `deploy/oss/.env`, and any override you wrote or changed (`gateway/servers.yaml`, an edited `oidc.yml`). `.env` holds
  no secret, but it is your configuration.
- The `atlas-tls-data` volume, only if `ATLAS_TLS_MODE` is `internal` or `acme-cloudflare`: it holds
  Caddy's own CA or the issued certificates. In the default `files` mode the certificates are in
  `secrets/tls/`.

The secrets are credentials. Store the copy encrypted, in a vault or an encrypted backup target,
and never in a repository. Keep the backups you rely on off this VM: a backup on the same disk does
not survive the disk.

Size the backup space from [Server requirements](server-requirements.md): room for at least two
dumps at the size of the live database.

### A verified backup: `atlas-admin backup run`

The repository ships a backup command that does more than dump. It runs `pg_dump --format=custom`,
writes a sha256 beside the file, **restores the dump into a temporary database in the same
Postgres and compares the row counts** of `node`, `edge` and `graph_event` with the source, then
drops the temporary database. A dump that does not restore is deleted and the command exits
non-zero. A manifest (`<file>.json`: counts, sha256, size, versions) is written last.

It runs in the `atlas-backup` service (the `backup` stage of `backend/Dockerfile`, which adds the
Postgres client tools that `atlas-api` does not carry). Files land in `/var/lib/atlas/backups`: the
`atlas-backups` volume, or a host directory if you set `ATLAS_BACKUP_HOST_DIR`
([below](#backups-in-a-host-directory)):

```sh
docker compose run --rm atlas-backup atlas-admin backup run
```

The default reason, `manual`, always runs. To check existing backups again without taking a new
one:

```sh
docker compose run --rm atlas-backup atlas-admin backup run --verify-only /var/lib/atlas/backups
```

`--verify-only` checks each file's sha256, restores it into a temporary database and counts it. An
empty directory is a failure, not a pass.

Things to know:

- **A `manual` run is never pruned.** Every file it writes stays until you delete it. For backups
  that keep themselves in check, use routine backups (next).
- **Credentials.** The command reads the database URL from the same secret file as `atlas-api`
  (`/run/secrets/db_url`). The password never reaches a command line, an environment value or its
  output.
- **With single sign-on on.** `atlas-backup` reads `.env` like `atlas-api`, so it sees the
  `ATLAS_OIDC_*` sign-in settings, but `oidc.yml` mounts the client secret and group map into
  `atlas-api` only. The `backup` verbs (`run`, `restore`, `rollback`) ignore every sign-in setting
  (`ATLAS_AUTH_PROVIDERS`, `ATLAS_LOCAL_LOGIN`, `ATLAS_OIDC_*`, `ATLAS_OAUTH_*`), so they work
  there unchanged; the backup container is never given an IdP credential. Run the other
  `atlas-admin` verbs in `atlas-api` as before: in `atlas-backup` they stop at start, naming the
  file they cannot read.

### Routine backups: `ATLAS_BACKUP_SCHEDULE` and `ATLAS_BACKUP_KEEP`

Off by default. To take a backup on a schedule, set in `.env`:

```sh
ATLAS_BACKUP_SCHEDULE=daily     # off (default), hourly, daily or weekly
ATLAS_BACKUP_KEEP=7             # routine backups kept (1 to 1000, default 7)
```

and start the `backup` profile's loop (or put `COMPOSE_PROFILES=backup` in `.env`):

```sh
docker compose --profile backup up -d atlas-backup
docker compose --profile backup logs atlas-backup | tail
```

The loop checks once a minute. A routine backup is due when the newest routine backup in the
directory is older than the interval; it is the same verified backup as a manual one, named
`atlas-<UTC time>-routine.dump`. After each verified routine backup, routine backups beyond the
newest `ATLAS_BACKUP_KEEP` are deleted with their `.sha256` and `.json`. **Nothing else is
deleted**: manual, per-deploy and crunch backups, and any other file in the directory, stay. A
failed attempt is retried after 15 minutes, and nothing is pruned after a failure. A bad value
stops the loop at start, naming the variable.

Prefer cron on the VM to a long-running container? The same backup and the same pruning, once:

```sh
docker compose run --rm --no-deps atlas-backup atlas-admin backup run --reason routine
```

**Crunch week is unchanged.** The loop asks the crunch first, every tick, exactly as before
([Crunch week](crunch-week.md#backups)): while a crunch period with a backup policy (`hourly`,
`daily` or `once`) is active, its backups are taken and pruned by its `retention_days`. A routine
backup is taken only on a tick where the crunch took none, and a crunch's `retention_days` never
deletes routine backups. With `ATLAS_BACKUP_SCHEDULE` unset the loop does what it always did: with
no crunch it does nothing and says so once.

### Backups in a host directory

The `atlas-backups` volume is not a host directory. To have the files on the host, where your backup
tooling copies them off the VM, set an absolute path in `.env` and create the directory writable
by the container's group 0 (the backup image runs as UID 10001 in group 0):

```sh
sudo install -d -m 0770 -g 0 /srv/atlas-backups
# .env
ATLAS_BACKUP_HOST_DIR=/srv/atlas-backups
```

Every `atlas-backup` command then reads and writes there: the loop, `backup run`, `--verify-only`
and `backup restore`. Unset, the named volume is used as before. Files already in the volume are not
moved; copy them out first if you want them (next).

To copy a file out of the volume instead:

```sh
docker compose run --rm -T --no-deps atlas-backup ls -l /var/lib/atlas/backups
docker compose run --rm -T --no-deps atlas-backup cat /var/lib/atlas/backups/<file>.dump > <file>.dump
```

Copy the matching `.sha256` and `.json` the same way; a restore needs all three.

**Tested:** `backend/verify_crunch_backup.py`, in the backend suite that `verify.yml` runs on every
change: a backup is taken and verified against a test database, a corrupted or truncated dump is
detected, retention deletes only its own older files, a crunch that is off takes no backup, and no
credential appears in any output. Its section H covers routine backups: with no schedule the loop
answers exactly as before; with one, a routine backup when due and not before, `ATLAS_BACKUP_KEEP`
deleting only older routine backups, a due crunch backup winning the tick, the back-off after a
failure, `backup run --reason routine`, and a bad value stopping the loop. It checks the compose
service statically (profile, stage, network, no published port, the volume or host directory).
Install smoke leg 8d runs `atlas-admin backup run` in the `atlas-backup` image on the OSS stack,
and leg 9f runs it again, with `restore`, after single sign-on is turned on. Section I covers the
backup verbs with the OIDC settings set and their files absent.
**Not tested:** the loop as a long-running compose service on the OSS stack, the
`ATLAS_BACKUP_HOST_DIR` bind mount, and copying files out of the volume.

### A plain dump

[INSTALL.md](../INSTALL.md#10-upgrading) uses the simplest form before an upgrade, a plain SQL dump
through the Postgres container:

```sh
( umask 077; mkdir -p ~/atlas-backups &&
  docker compose exec -T atlas-postgres pg_dump -U atlas -d atlas > ~/atlas-backups/atlas-$(date +%F).sql )
```

The dump holds every password verifier and token digest, so it is written readable by you alone
(`umask 077`) and outside the checkout. This needs nothing beyond the running stack, but nothing checks that the file restores. Prefer
`atlas-admin backup run` when you can. **Not tested** by CI.

### Back up the secrets

*Untested, generic.* Copy the whole `secrets/` directory with the tool your backup system uses,
preserving the files as they are, at the same time as the database. For example, an archive that
you then encrypt and move off the VM, made from `deploy/oss/`:

```sh
( umask 077; mkdir -p ~/atlas-backups &&
  tar -czf ~/atlas-backups/atlas-secrets-$(date +%F).tgz secrets .env )
```

The archive holds the peppers and the database password: treat it as a credential. `umask 077`
makes it readable by you alone, and it goes outside the checkout, where a `git add` cannot pick it
up. It is still an unencrypted copy of the peppers: do not leave it on the VM.

## 2. Restore

`atlas-admin backup restore` restores a backup from `atlas-admin backup run` over the live
database. It runs in the `atlas-backup` image, like the backup:

1. **Put the secrets back** if this is a new VM or they changed. Restore `secrets/` (and `.env`)
   **from the same backup** as the database, into `deploy/oss/`. The peppers must be the ones the
   database was made with. Keep the modes `init-secrets.sh` uses: the directory `0700`, the files in
   it `0644` except `secrets/tls/server.key`, which is `0600`. Put the backup files (the `.dump`,
   its `.sha256` and its `.json`) back in the backup directory.

2. **Stop what is connected to the database**, leaving Postgres running. The restore refuses while
   any other session is connected, and says which:

   ```sh
   docker compose stop atlas-api
   ```

   Add `atlas-backup` if the `backup` profile's loop runs. `atlas-mcp`, `atlas-tls` and the gateway
   do not connect to the database and can stay up; they answer errors until `atlas-api` is back.

3. **Rehearse it.** A dry run does everything except replace the database:

   ```sh
   docker compose run --rm --no-deps atlas-backup atlas-admin backup restore latest --dry-run
   ```

   `latest` is the newest backup in the directory; give a file name instead
   (`atlas-<UTC time>-<reason>.dump`) for another. The dry run checks the file's sha256 against its
   `.sha256` and its manifest, restores it into a staging database, counts `node`, `edge` and
   `graph_event` there against the manifest, drops the staging database, and says whether a real
   run would refuse because something is connected. It changes nothing.

4. **Restore:**

   ```sh
   docker compose run --rm --no-deps atlas-backup atlas-admin backup restore latest
   ```

   In order: the checksum is checked before any database is opened; anything connected is a
   refusal; the dump is restored into a staging database with the live database's encoding,
   collation and owner, and counted against the manifest. Only then is the live database
   **renamed** to `atlas_prerestore_<UTC time>` (the safety copy) and the staging database renamed
   to `atlas`, and the counts are checked once more. A failure before the swap leaves the live
   database untouched and drops the staging one. The output names the safety copy and the rollback
   command.

5. **Start `atlas-api`:**

   ```sh
   docker compose start atlas-api
   ```

   If the backup is from an older release, run the schema step too:
   `docker compose exec -T atlas-api atlas-migrate upgrade`.

6. **Check it.** `curl --cacert secrets/tls/ca.crt https://atlas.example.com/readyz`, sign in, and
   compare `node_count`, `edge_count` and `event_count` on `/api/health` with the manifest. A
   sign-in that fails for everyone means the peppers do not match the database.

7. **Roll back, or clean up.** To go back to the database as it was before the restore, stop
   `atlas-api` again and:

   ```sh
   docker compose run --rm --no-deps atlas-backup atlas-admin backup rollback          # lists the copies
   docker compose run --rm --no-deps atlas-backup atlas-admin backup rollback --to atlas_prerestore_<UTC time>
   docker compose start atlas-api
   ```

   The rollback renames the current database aside to `atlas_rolledback_<UTC time>` and the safety
   copy back to `atlas`; it drops nothing. Once you are satisfied, drop the copies to free their
   space (each holds the full size of the database):

   ```sh
   docker compose exec -T atlas-postgres psql -U atlas -d postgres -c 'DROP DATABASE "atlas_prerestore_<UTC time>"'
   ```

Things to know:

- **Why a rename, not a dump, for the safety copy.** A rename is instant and needs no space at
  restore time whatever the size; the copy is the original database itself, not a logical copy
  that could itself fail; and rolling back is another rename. The cost is that the copy occupies its
  full size until you drop it. Plan the volume for two databases during a restore.
- **Nothing is written into the restored database**, not even a record of the restore: it would
  then differ from the backup it was counted against. The command's output is the record.
- **Not carried over:** database-level settings (`ALTER DATABASE ... SET`) and grants on the
  database itself; a dump does not hold them. Atlas sets neither; the command says so when the old
  database had any, and the safety copy keeps them.
- **A plain SQL dump** (the `pg_dump` form in [section 1](#a-plain-dump)) is not something this
  command restores. *Untested, generic:* with `atlas-api` stopped, rename the database aside
  (`ALTER DATABASE atlas RENAME TO atlas_before_restore` in `psql -d postgres`), `createdb -U atlas
  -T template0 atlas`, load it with
  `docker compose exec -T atlas-postgres psql -U atlas -d atlas -v ON_ERROR_STOP=1 < atlas-backup.sql`,
  and start `atlas-api`.

**Tested:** `backend/verify_crunch_backup.py` section G, against a test database: a flipped byte
and a missing manifest are refused before any database is touched; `--dry-run` changes nothing;
with another session connected a dry run says a real run would refuse, and a real run refuses; a
failing `pg_restore` leaves the live database untouched and no staging database behind; the restore
makes the database byte-identical to the moment of the backup, keeps the safety copy, and
`rollback` puts the safety copy back. Install smoke leg 8d, on the OSS stack through
`docker compose run`: `atlas-api` stopped, `backup run`, data changed and deleted, `restore
--dry-run` changes nothing, a session held open is refused, `restore`, then **every table's row
count identical** to before the backup, `atlas-api` healthy again on the restored database, and
the safety copy listed and dropped. **Not tested:** restoring onto a new VM (putting the secrets
back, step 1), a full restore of a backup from an older release (the install smoke's `upgrade`
job dry-runs one with the new backup image), and the plain SQL dump.

Restoring onto a different Postgres base image than the one the dump was taken on is fine for a
logical dump: the indexes are rebuilt by the load. Restoring the **volume** itself (a file-level
copy of `atlas-pgdata`) onto an image with a different C library is not; see the collation check in
[section 3](#the-collation-check).

## 3. Upgrade

[INSTALL section 10](../INSTALL.md#10-upgrading) is the upgrade procedure: back up, check out the
new release tag, compare `.env` with the new `.env.example` and read the release notes, rebuild (or
pull the released images) and restart, and run the schema step once more by hand. This section
explains the parts of it. Releases, their notes and their images are described in
[RELEASES.md](../RELEASES.md).

### The schema step: `atlas-migrate upgrade`

The `atlas-migrate` one-shot runs `atlas-migrate upgrade` on every `docker compose up`, and
`atlas-api` does not start until it has exited 0. It runs the schema steps that application startup
runs: create every missing table, add missing columns and indexes, create the full-text indexes. It
only creates what is missing; it never changes or deletes data, and a second run changes nothing.
It reads the same `.env` as `atlas-api` but ignores the sign-in settings (`ATLAS_AUTH_PROVIDERS`,
`ATLAS_LOCAL_LOGIN`, `ATLAS_OIDC_*`), so on an install with SSO on it needs none of the OIDC files
that `oidc.yml` mounts into `atlas-api`. To run it by hand:

```sh
docker compose exec -T atlas-api atlas-migrate upgrade
docker compose logs atlas-migrate | tail -20
```

It is not a general migration framework. A change it cannot make (a column type, a rename) ships
as a separate one-time script, named in the release notes.

**Tested:** `backend/verify_upgrade_command.py` (the command is idempotent and builds the same
schema as startup), and install smoke leg 2 (`atlas-migrate` exits 0, and `atlas-migrate upgrade`
runs twice more on the live stack), install smoke leg 9h (on the stack with SSO on, `atlas-migrate`
and `atlas-api` recreated: the one-shot exits 0 without the OIDC files and `atlas-api` comes back
healthy), and the install smoke's `upgrade` job: the previous release,
installed from its published images and seeded, is backed up with `atlas-admin backup run`, then
this checkout is built over the same `.env`, `secrets/` and volumes; `atlas-migrate` exits 0 on the
old database and once more by hand, every table keeps its row count (the `node_type` and
`edge_type` registries may only grow), the old backup passes a dry-run restore with the new backup
image, and the old passwords, agent token and data still work. **Not tested:** an upgrade that
skips releases, and rolling back. Take the backup in [section 1](#1-backup) first, every time.

### The origin certificates: one step for an install made before them

Since OSS-26 D, `atlas-api` and `atlas-mcp` serve TLS themselves on the ingress's legs to them
(`https://atlas-api-origin:8443`, `https://atlas-mcp-origin:8493`), and the compose file reads five
files that an older install does not have: `secrets/tls/api-origin.crt`, `api-origin.key`,
`mcp-origin.crt`, `mcp-origin.key` and `origin-ca.crt`. Without them `docker compose up` stops on
the missing secret file. **Once, before the first `up` of the new version** (INSTALL section 10,
between steps 3 and 4), from `deploy/oss/`:

```sh
ls secrets/tls/origin-ca.crt 2>/dev/null || bash ../../scripts/init-certs.sh --origin-only
# init-certs: wrote origin-ca.crt api-origin.crt api-origin.key mcp-origin.crt mcp-origin.key under ./secrets/tls (the CA key was discarded)
```

`--origin-only` writes exactly those five files and nothing else. They are signed by a new,
separate CA (`origin-ca.crt`) whose key is discarded at once, and only the ingress trusts it, so
`ca.crt`, `server.crt` and every client keep working unchanged: nobody re-imports anything. It
refuses to overwrite the files once they exist (`--force` replaces them). An install made with your
own PKI does the same. There is no new setting in `.env`. A new install gets the files from
`init-secrets.sh`, signed by the install CA itself.

**Tested:** the install smoke's `upgrade` job runs this step on the previous release's `secrets/`
before bringing up this checkout; `tools/ci/test_init_certs.sh` proves that `--origin-only` leaves
`ca.crt` and `server.crt` untouched and that the new certificates verify against `origin-ca.crt`.

### Images: build from the checkout, or pull the released images

Every release is a `vX.Y.Z` tag, and every tag publishes the images the install builds.
[RELEASES.md](../RELEASES.md) has the versioning scheme, what each release publishes, the release
notes and how a release is cut. An install runs a release one of two ways; either way the checkout
stays at the release tag, because the compose file, the Caddyfile and the scripts come from it.

**Build from the checkout** (the default). Every base image in the Dockerfiles is pinned by tag and
`@sha256` digest (`backend/Dockerfile`, `mcp/Dockerfile`, `deploy/caddy/Dockerfile`,
`deploy/postgres/pgvector.Dockerfile`), and so is the gateway image in `docker-compose.yml`.
Rebuilding the same checkout therefore gives the same bases: patches arrive as new commits, not as
a newer `latest` pulled on rebuild. Upgrading is checking out the new tag and
`docker compose up -d --build` ([INSTALL section 10](../INSTALL.md#10-upgrading)).

**Pull the released images.** The `images` workflow (`.github/workflows/images.yml`) runs on every
`v*` tag: it builds all five images, checks them, runs the same vulnerability gate as every pull
request, and only then publishes them, tagged with the release:

| Service | Image |
|---|---|
| `atlas-api`, `atlas-migrate`, `atlas-retrieval` | `ghcr.io/<owner>/gradinai-atlas-api` |
| `atlas-mcp` | `ghcr.io/<owner>/gradinai-atlas-mcp` |
| `atlas-backup` | `ghcr.io/<owner>/gradinai-atlas-backup` |
| `atlas-postgres` | `ghcr.io/<owner>/gradinai-atlas-postgres` |
| `atlas-tls` | `ghcr.io/<owner>/gradinai-atlas-tls` |
| `atlas-gateway` | not republished: the upstream AgentGateway image, pinned by digest in `docker-compose.yml` |

Each release carries `release-images.env`: the five references, tag and digest, as the
`ATLAS_IMAGE_*` lines of `.env.example`. With those lines in `.env`, the overlay `release.yml`
replaces every `build:` with its image and changes nothing else:

```sh
docker compose -f docker-compose.yml -f release.yml pull
docker compose -f docker-compose.yml -f release.yml up -d --wait --wait-timeout 600
```

or `COMPOSE_FILE=docker-compose.yml:release.yml` in `.env`, before any other overlay. The upgrade
is then INSTALL section 10 with two differences: step 4 replaces the five `ATLAS_IMAGE_*` lines with
the new release's and runs `pull` and `up` without `--build`
([RELEASES.md, "Upgrading with released images"](../RELEASES.md#upgrading-with-released-images)).
The references are pinned by digest, not by tag alone: a tag can be moved, a digest cannot. A
missing `ATLAS_IMAGE_*` line stops compose with its name rather than building something else.
`release.yml` needs Docker Compose 2.24.4 or later.

**Tested:** the `images` workflow's image checks and gate on every pull request that touches an
image's inputs; `release.yml` renders, refuses a missing image, and every service it touches is a
pinned pull (`oss.yml`, `check_oss_compose.py` S3 and S7); the install smoke's `upgrade` job runs
the previous release from its published images (pulled by digest with `release.yml`, every service
checked to run a reference from `release-images.env`) and then upgrades it to the checkout built
from source. *Untested:* moving from one released image set to the next (a newer
`release-images.env` with `release.yml` kept).

### The collation check

`atlas-postgres` sorts text indexes with the C library of its base image. When an upgrade moves the
Postgres image to a new Debian release, the database still opens, but a text index made under the
old library can be out of order: lookups miss rows, a unique index can admit a duplicate. Postgres
only logs a warning.

After any upgrade that changed `deploy/postgres/pgvector.Dockerfile`, and always for an install
whose database was first created before 2026-10-26 (it ran the Debian 12 image), run the check:

```sh
bash ../../tools/db_collation_check.sh "$(docker compose ps -q atlas-postgres)"
```

It lists every database, its recorded and actual collation version, and `OK` or `MISMATCH`. It
reads no credential: it runs `psql` inside the container over the local socket. Exit 0 means every
database is OK; exit 1 means a mismatch is left.

On a mismatch, take a backup ([section 1](#1-backup)) and then repair:

```sh
bash ../../tools/db_collation_check.sh --fix "$(docker compose ps -q atlas-postgres)"
```

`--fix` rebuilds every index (`REINDEX DATABASE CONCURRENTLY`), then records the new version, then
checks again. `--blocking` uses a plain `REINDEX`, which takes locks but is faster on a quiet
install. If a rebuild fails, for example a unique index that now finds a duplicate, the script
stops, lists the invalid indexes and leaves the version unrecorded, so the warning stays until the
data is repaired by hand. Read the script's header for the details.

**Tested:** the install smoke's `upgrade` job runs the check in report mode on the upgraded OSS
install, whose database was created by the previous release's `atlas-postgres` image, and requires
exit 0. When `deploy/postgres/` changed since that release, a mismatch is repaired with `--fix` and
checked again. **Not tested:** a real move to a new Debian release (no release has made one yet),
so the `--fix` path has not run against an OSS install.

## 4. Rotating credentials

### Per-user agent tokens

Every agent has its own `atlas_svc_` token, tied to a person. Tokens always expire (at most 365
days). `/api/health` lists tokens that are close to expiry or long unused under `token_warnings`.

**Rotate without an outage.** Issuing a new token does not revoke the old one:

1. Mint a new token for the same person and purpose. It is added to the same agent and written
   only to the `--out` file in the container:

   ```sh
   docker compose exec -T atlas-api atlas-admin \
     token mint --user <name> --purpose claude-code --scopes read,write --out /tmp/claude-code.token
   ```

2. Move it to the client and delete the copy in the container, as in
   [INSTALL section 7](../INSTALL.md#7-connect-claude-code-clientsclaude-code).
3. Confirm the client works with the new token.
4. Revoke the old one, by its id or prefix (never the value):

   ```sh
   docker compose exec -T atlas-api atlas-admin token list --user <name>
   docker compose exec -T atlas-api atlas-admin token revoke <id or prefix>
   ```

Revocation takes effect on the next request. People can also rotate their own agent credentials
from the board (Settings, Agent credentials). The client side is in
[the Claude Code client pack](../../../clients/claude-code/README.md#rotating-and-revoking).

**Revoke everything of one person:** `atlas-admin token revoke --user <name>` revokes every live
token of that person's agents. `atlas-admin user disable <name>` ends their sessions and stops their
agents' tokens at once, and `user enable` restores them. `user reset-password` ends sessions but
leaves tokens working, on purpose.

`atlas-admin` records the shell user as the operator, or `--actor <name>` placed before the
subcommand.

**Tested:** the backend suite (`verify_identity_seam.py`, `verify_agent_identity.py` and others)
covers `token mint`, `token list`, `token revoke` and the user verbs; install smoke leg 8 mints a
token with `token mint --out` and connects Claude Code with it.

### Secrets files

The files under `secrets/` are compose secrets: each is mounted into the container when the
container is created. After replacing a file, **recreate** the containers that read it; a restart
alone may keep the old file.

| File | To rotate | Recreate |
|---|---|---|
| `password_pepper`, `token_pepper` | **Do not.** A new pepper invalidates every password, session and token at once, permanently. There is no rotation path for them. | - |
| `db_password`, `db_url` | **Never by hand.** The database keeps the password it was created with: changing the files alone breaks the install. Run `scripts/rotate-db-password.sh`, below. | the script does it |
| `embed_key` (retrieval) | Write the new key into the file (one line, `0644`). | `atlas-api`, with `retrieval.yml` |
| `oidc_client_secret` (OIDC) | Create the new secret in the IdP, write it to the file, recreate, then remove the old secret in the IdP. | `atlas-api`, with `oidc.yml` |
| `tls/*` | See [section 5](#5-certificates). | |

To recreate `atlas-api` after replacing a file (with your overlays, as everywhere):

```sh
docker compose up -d --force-recreate --no-deps --wait atlas-api
```

Never run `init-secrets.sh --force` on an install with data. It replaces the peppers and the
database password files, which breaks sign-in for everyone and the database connection.

### The database password: `rotate-db-password.sh`

The database is reachable only on the internal `data` network, by `atlas-migrate`, `atlas-api`
and `atlas-backup`, so rotating its password is a policy matter rather than a fix. When your
policy requires it, run, with the stack up and your overlays passed as `-f` (or in `COMPOSE_FILE`):

```sh
bash ../../scripts/rotate-db-password.sh -f docker-compose.yml -f oidc.yml   # your -f files
```

```text
rotate-db-password: the current password signs in
rotate-db-password: services to recreate: atlas-api
rotate-db-password: the role has the new password, and it signs in
rotate-db-password: wrote ./secrets/db_password and ./secrets/db_url
rotate-db-password: recreated and healthy: atlas-api
rotate-db-password: atlas-api reads the database
rotate-db-password: the old password is refused
rotate-db-password: done. Back up ./secrets/ again (...)
```

What it does, in order:

1. Checks that `secrets/db_password` signs in to the database as it is, and that `secrets/db_url`
   carries the same password and points at this install's `atlas-postgres`. It refuses, changing
   nothing, if either is not so. It also finds every container that mounts `secrets/db_url`.
2. Generates a new password with `openssl` and writes the new `db_password` and `db_url` (the old
   URL with only the password replaced) to a `0700` work directory under `secrets/`, beside a copy
   of the old two files.
3. Sets the new password on the role with `ALTER ROLE` inside `atlas-postgres`, over its local
   socket, and checks that the new password signs in over TCP to `atlas-postgres`, as `atlas-api`
   connects (not over loopback, which the postgres image trusts without a password).
4. Moves both new files into `secrets/` (`0644`, as `init-secrets.sh` writes them).
5. Recreates every **running** container that mounts `db_url` (`atlas-api`, and `atlas-backup`
   when its profile runs), waits until they are healthy, then reads the database from inside
   `atlas-api`. A consumer that is not running reads the new file when it next starts; that
   includes the `atlas-migrate` one-shot, which a password change does not need to run again.
6. Checks that the old password is now refused, and deletes the old copies.

**A failure rolls back.** If any step after the `ALTER ROLE` fails, the script puts the old files
back, sets the old password on the role again, recreates the same services on it and exits 1 with
`rolled back; the install runs on the old password, as before`. Only if that rollback itself fails
does it keep the old files in `secrets/.rotate-db-password.*` and print the steps to finish by hand.
A second run while one is in progress is refused (`secrets/.rotate-db-password.lock`).

**The password is never printed** and never on a command line or in an environment variable: the
shell reads it from the file, `psql` gets the `ALTER ROLE` statement on stdin (with
`log_statement` off for that session, so the server log does not keep it), and the sign-in checks
use a passfile inside the container that is deleted after each check.

**What to expect.** `atlas-postgres` keeps running and is not recreated: it never reads
`db_password` again once the volume is initialised. Requests `atlas-api` serves on connections it
already holds keep working; a new connection fails for the few seconds between the `ALTER ROLE`
and the recreated `atlas-api`, so run it at a quiet time. No data is touched.

**Afterwards, back up `secrets/` again** ([Back up the secrets](#back-up-the-secrets)). An older
archive holds the old password, and the role's password lives in the Postgres volume, not in a
database dump: putting the old `db_password` and `db_url` back onto this volume breaks the
connection.

The script only rotates the password of this install's own `atlas-postgres`. With an external
database (`db_url` pointing elsewhere) it refuses: change the password there, then write both files
and recreate `atlas-api` as above.

**Tested:** install smoke leg 10, on the stack with the SSO overlay: a marker row written first; a
rotation made to fail after the services were recreated rolls back to the old files and password
with every service healthy; then the rotation itself: both files changed, `atlas-api`
recreated and healthy, an agent token served, the marker row and every table's row count
identical, the new password accepted and the old one refused (`password authentication failed`),
and neither password in the script's output or the `atlas-postgres` log. **Not tested:** a
rotation with `atlas-backup` running as a service, and with `retrieval.yml`.

## 5. Certificates

The default install uses a throwaway **install CA** made by `scripts/init-certs.sh` (run for you by
`init-secrets.sh`). The server certificate lasts 397 days by default (`--days`) and the CA ten
years, but **the CA key is deleted as soon as it has signed**. Nothing can be signed with that CA
again, so renewing means a new CA and new certificates, and every client trusts the new `ca.crt`.

### Renewing the install CA's certificates

*Untested as an end-to-end procedure*; the script itself is tested (below).

1. Check when the current certificate expires. `/api/health` warns on its own (see
   [The expiry warning](#the-expiry-warning) below); by hand, with a generic `openssl` command:

   ```sh
   openssl x509 -in secrets/tls/server.crt -noout -enddate
   ```

2. Back up `secrets/tls/`, then make a new CA and new certificates. `--force` is required to
   replace the existing files. Without `--host`, `init-certs.sh` takes the public name from
   `ATLAS_PUBLIC_HOST` in `.env`, as `init-secrets.sh` does, so a renewal keeps it:

   ```sh
   bash ../../scripts/init-certs.sh --force
   # init-certs: public name: atlas.example.com (from ATLAS_PUBLIC_HOST in ./.env)
   ```

   It prints which public name it used and where it came from; check that line before going on.
   To give other or more names, pass each with `--host` (then `.env` is not read):
   `init-certs.sh --force --host atlas.example.com --host 192.0.2.10`. It always adds
   `atlas-api`, `atlas-mcp`, `gateway`, `localhost`, `127.0.0.1` and `::1` to the server
   certificate, and prints every name it used. It also renews the two origin listeners'
   certificates (`api-origin.crt`, SAN `atlas-api-origin`; `mcp-origin.crt`, SAN
   `atlas-mcp-origin`; both also `localhost` and `127.0.0.1`) and `origin-ca.crt`, under the new CA.

3. Recreate everything that reads the files: `atlas-tls` (the server certificate, and
   `origin-ca.crt` for its legs to the two services), `atlas-mcp` (it trusts exactly `ca.crt`,
   holds the client certificate for the optional mTLS leg, and serves `mcp-origin.crt`),
   `atlas-api` (it serves `api-origin.crt` and reads `server.crt` for the expiry warning), and the
   gateway if you run it:

   ```sh
   docker compose up -d --force-recreate --wait atlas-tls atlas-mcp atlas-api
   docker compose --profile gateway up -d --force-recreate atlas-gateway
   ```

4. Give clients the new `secrets/tls/ca.crt` (public; safe to copy): every browser or trust store
   that imported the old one, every Claude Code machine (`NODE_EXTRA_CA_CERTS`), and anything that
   uses `curl --cacert`. If your retrieval endpoint's certificate was issued by this install's CA,
   it needs a new one as well.
5. Check the door: `curl --cacert secrets/tls/ca.crt https://atlas.example.com/healthz`.

Plan the renewal before the expiry date: after it, browsers and agents refuse the connection, and
`atlas-mcp` cannot reach the API.

### The expiry warning

`/api/health` ([section 7](#health)) carries `cert_warnings`, shaped like `token_warnings`: a list
that is empty while the server certificate has more than `ATLAS_CERT_EXPIRY_WARN_DAYS` left
(default 30, set it in `.env`), and otherwise holds one entry:

```json
"cert_warnings": [
  {"file": "/run/secrets/tls_cert", "subject": "CN=atlas-api",
   "not_after": "2027-11-30T09:12:44Z", "reasons": ["expires_in_days=12"]}
]
```

`reasons` is `expires_in_days=N` inside the window, `expired` after the date, `unreadable` when
atlas-api cannot open the file, and `not_a_certificate` when it holds no PEM certificate. The
compose file mounts `secrets/tls/server.crt` (the public certificate, never the key) into
atlas-api as `/run/secrets/tls_cert` and sets `ATLAS_CERT_EXPIRY_FILE` to it. atlas-api reads the
file on every call, but a recreated file is only seen after the container is recreated (step 3
above), which is also how the warning clears after a renewal.

The warning is about `server.crt`, which every mode uses for atlas-mcp's hop to the API. In
`internal` and `acme-cloudflare` mode Caddy renews the public door's certificate itself and that
one is not watched. Without `ATLAS_CERT_EXPIRY_FILE` (an install that terminates TLS elsewhere)
`/api/health` has no `cert_warnings` field at all.

### Your own PKI, Caddy's CA, or ACME

- **Your own PKI** (`ATLAS_TLS_MODE=files` with your files): replace `secrets/tls/server.crt`,
  `server.key` and, if the root changed, `ca.crt`, keeping the SANs listed in
  [INSTALL section 3](../INSTALL.md#3-secrets-and-certificates-init-secretssh), then recreate as in
  step 3. For the mTLS leg alone, `init-certs.sh --client-only --force` makes a new client CA and
  `atlas-mcp`'s client certificate without touching the server certificate. The origin listeners'
  certificates are the install's own in every mode: `init-certs.sh --origin-only --force` renews
  them under a new origin CA without touching anything else (recreate `atlas-tls`, `atlas-api`
  and `atlas-mcp`). To issue them from your PKI instead, put `api-origin.crt`/`.key` (SAN
  `atlas-api-origin`, plus `127.0.0.1` for the healthcheck), `mcp-origin.crt`/`.key` (SAN
  `atlas-mcp-origin`, plus `127.0.0.1`) and their root as `origin-ca.crt` at the same paths.
- **`internal` or `acme-cloudflare`**: Caddy renews its own certificates and keeps them in the
  `atlas-tls-data` volume. There is nothing to run; back up the volume ([section 1](#what-to-back-up)).

**Tested:** `tools/ci/oss_tls_front.sh` (the `oss.yml` workflow) makes certificates with
`init-certs.sh`, runs the real Caddy front with them, and proves the install CA path, that another
CA is refused, and the mTLS leg, and that its leg to the upstream is TLS and fails closed with
another CA's `origin-ca.crt` (HTTP 502, nothing forwarded). Install smoke leg 1 issues the first
certificates through `init-secrets.sh`, and leg 6 checks the door, and both origin legs, with the
install CA. `tools/ci/test_init_certs.sh`
(the `oss.yml` workflow) proves that a run without `--host` takes `ATLAS_PUBLIC_HOST` from `.env`
and that a `--force` renewal keeps it, and `backend/verify_cert_expiry.py` (the backend suite)
proves the warning for a certificate near expiry, far from it, and none configured. **Not
tested:** `--force` on a running install, and the recreate and redistribution steps.

## 6. The MCP gateway (`--profile gateway`)

Setting the gateway up is [INSTALL section 6](../INSTALL.md#6-optional-the-mcp-gateway---profile-gateway).
Running it:

- **After changing** `ATLAS_PUBLIC_URL`, `ATLAS_PUBLIC_HOST` or the server list, render the
  configuration again, have the pinned release parse it, and recreate:

  ```sh
  python3 ../../scripts/render-gateway.py
  docker compose --profile gateway run --rm --no-deps atlas-gateway -f /etc/agentgateway/config.yaml --validate-only
  docker compose --profile gateway up -d --force-recreate atlas-gateway
  ```

  With your own server list, pass `--servers gateway/servers.yaml` to `render-gateway.py` every
  time. It refuses an unsafe configuration (a token sent over `http://`, a CORS origin of `*`, an
  unknown option) and writes nothing.
- **After an upgrade** that moved the pinned gateway image, pull it, then render and recreate as
  above: `docker compose --profile gateway pull atlas-gateway`.
- **After renewing certificates**, recreate it ([section 5](#5-certificates)): it serves
  `server.crt` and trusts `ca.crt`.
- **Health.** The gateway image has no shell, so it has no container healthcheck. Check its door
  from outside; any HTTP status means it is up:

  ```sh
  curl --cacert secrets/tls/ca.crt -o /dev/null -w '%{http_code}\n' https://atlas.example.com:8444/mcp
  ```

- **Logs:** `docker compose --profile gateway logs atlas-gateway`. A warning that no session key
  is configured, or that a generated one could not be written, means the configuration was not
  rendered by this release's `render-gateway.py`, which turns MCP session-ID encryption off
  (`config.session.key: disabled`); render it again.
- **Always on:** set `COMPOSE_PROFILES=gateway` in `.env`, and every `docker compose up` starts it.
  Remove it from `COMPOSE_PROFILES` and stop the service (`docker compose --profile gateway stop
  atlas-gateway`) to turn it off.

Every call through the gateway is made with the caller's own token, so revoking a token
([section 4](#per-user-agent-tokens)) works the same there.

**Tested:** install smoke leg 5b (render, `config --quiet`, `pull`, `--validate-only`, `up`, a
restricted allow-list and a wrong CA refused) and leg 6 (the TLS door on 8444).

## 7. Logs and health

### Health

| Check | Who may call it | What it says |
|---|---|---|
| `docker compose ps -a` | the VM | each service's state and container healthcheck. `atlas-migrate` should be `exited (0)`; the others `running` and `healthy` (the gateway has no healthcheck) |
| `GET /healthz` | anyone | liveness: `atlas-api` answers. No database call |
| `GET /readyz` | anyone | readiness: `atlas-api` can query the database. `503` when it cannot |
| `GET /api/health` | a signed-in person or a token | node, edge and event counts, `token_warnings` (tokens near expiry or long unused), `cert_warnings` (the server certificate near expiry, [section 5](#the-expiry-warning)), `retrieval` when the add-on is on (`on`, `degraded: ...` or `unavailable: ...`) |

From outside, through the TLS door, with the install CA:

```sh
curl --cacert secrets/tls/ca.crt https://atlas.example.com/healthz
curl --cacert secrets/tls/ca.crt https://atlas.example.com/readyz
```

Point an external monitor at `/healthz` and `/readyz`. They carry no information beyond the status,
so they are safe to expose. `/api/health` needs a credential on purpose: it is a reconnaissance
document. Read it in a signed-in browser, or give a monitor a `read`-scoped agent token.

`token_warnings` is the early warning for agent tokens. An expired token only shows up as `401`s at
the agent, so look at the list regularly and rotate before the date.

### Logs

Every service logs to standard output, so `docker compose logs` reads them:

```sh
docker compose logs atlas-api
docker compose logs atlas-migrate | tail -20
```

`atlas-api` and `atlas-mcp` name the reason when they refuse to start: a missing or malformed
setting, an unreadable secret file, or an unknown feature name. Every boot failure is fail-closed,
so a service that is not healthy has a message in its log saying why.

Docker keeps container logs on the VM. Its default logging driver does not rotate them, so
configure rotation for the Docker engine on the VM (the engine's log options; *not part of the
install*). Atlas is written not to log passwords, tokens or session ids, but logs do contain
usernames, request paths and error details, so treat them as internal.

**Tested:** install smoke legs 1, 2 and 6 (every service healthy, `atlas-migrate` exits 0, `/healthz`
through the door with the install CA).

## 8. Audit export

Atlas keeps three audit trails: `graph_event` (every graph change), `auth_event` (sign-ins, account
and token changes) and, if you turn it on, `audit_read` (what agents read). [SECURITY.md](../SECURITY.md#audit-mutations-logins-and-reads)
describes what each holds and what it never holds.

**Read audit.** Off by default. Set `ATLAS_AUDIT_READS=1` in `.env` and recreate `atlas-api`. To
limit how long `audit_read` rows are kept, set `ATLAS_AUDIT_RETENTION_DAYS` and run the purge from
cron on the VM:

```sh
docker compose exec -T atlas-api atlas-admin audit purge
```

Without `ATLAS_AUDIT_RETENTION_DAYS` it deletes nothing. `graph_event` and `auth_event` are never
purged.

**Export to a SIEM.** `GET /api/audit/export` streams all three trails as JSON Lines, oldest
first. Parameters: `since` and `until` (ISO-8601, `until` exclusive; a date alone is read as UTC
midnight) and `kinds` (a comma-separated subset of `graph_event,auth_event,audit_read`). Only two
callers get anything but `403`: an owner in a browser session, or an agent token with the `admin`
scope whose person is a live owner, checked again on every call.

For a collector, mint an owner's token for that purpose (an owner may hold `admin`), and move it to
the collector as in [INSTALL section 7](../INSTALL.md#7-connect-claude-code-clientsclaude-code):

```sh
docker compose exec -T atlas-api atlas-admin \
  token mint --user <owner> --purpose siem --scopes read,admin --days 90 --out /tmp/siem.token
```

*Untested, generic.* On the collector, keep the token out of the command line by putting the
header in a curl configuration file readable only by you, then call the export:

```sh
( umask 077; { printf 'header = "Authorization: Bearer '; tr -d '\n' < ~/.config/atlas/siem.token; printf '"\n'; } > ~/.config/atlas/siem.curl )
curl --cacert ca.crt -K ~/.config/atlas/siem.curl "https://atlas.example.com/api/audit/export?since=2026-10-01" > atlas-audit.jsonl
```

Rotate this token like any other ([section 4](#per-user-agent-tokens)). If the owner is disabled or
demoted, the export stops for that token at once.

**Tested:** `backend/verify_audit_export.py` (the export's line format, order, filters and `400`s,
`403` for members and non-admin tokens, `200` for an owner's admin token, no secret in it, and
`audit purge` deleting only old `audit_read` rows). The curl call above is not run by CI.

## 9. Feature switches: `ATLAS_DISABLED_FEATURES`

A few features exist for one particular install and have no use elsewhere. `ATLAS_DISABLED_FEATURES`
switches them off; the OSS `.env.example` switches all of them off:

```sh
ATLAS_DISABLED_FEATURES=github_link,save_work,deploy_status,deploy_warning,attention_probes
```

| Name | What it removes |
|---|---|
| `github_link` | the GitHub-linking routes (they answer `404`) |
| `save_work` | the `atlas_save_work` MCP tool and the `signoff` prompt |
| `deploy_status` | the `deploy` field of `/api/health`, and `atlas-mcp`'s own image check |
| `deploy_warning` | the `deploy_warning` field on MCP responses |
| `attention_probes` | reserved: nothing is guarded by it yet; accepted so it can be set now |

The list is comma-separated; case and spaces are ignored. **An unknown name stops the boot** with an
error naming it, so a typo cannot leave a feature on unnoticed. After changing it, recreate both
services that read it:

```sh
docker compose up -d --force-recreate --wait atlas-api atlas-mcp
```

[CONFIG.md](../CONFIG.md#atlas_disabled_features) is the reference entry.

**Tested:** `verify.yml` runs the whole backend suite a second time with every feature disabled, and
every `mcp/verify_*.py` likewise; `backend/verify_feature_flags.py` and `mcp/verify_feature_flags.py`
check each switch, and that `atlas-api` and `atlas-mcp` agree on the list of names.

## 10. Keeping up with patches

The repository's CI watches for vulnerable and outdated components. An install benefits only by
taking new releases: the images are built from pinned bases, so nothing on the VM updates by
itself.

What CI does upstream ([SECURITY.md](../SECURITY.md#supply-chain) has the full table):

- **SCA.** Trivy scans the images, the Python locks and the vendored browser JavaScript on every
  pull request and weekly (`sbom-sca.yml`, `tools/ci/sca_gate.py`). A critical or high
  vulnerability that has a fix fails the build, except on a pull request that changes no
  dependency: there it is a warning, because the change did not bring it in, and the fix comes
  as a Dependabot security pull request, which meets the full gate. Master, the weekly run and a
  release always fail on it. The released images go through the same gate before they are
  published (`images.yml`).
- **EOL gate.** `tools/ci/check_eol.py` checks every runtime the tree names (Python, Postgres,
  Caddy, Node) against a dated end-of-life table (`tools/ci/eol.json`) and fails within six months
  of end of support, or when the table is stale. The weekly run compares the table with
  endoflife.date.
- **Dependabot** proposes new base image digests and dependency versions, which land as ordinary
  commits.
- **SBOMs** (CycloneDX) for each image and lock are produced by `sbom-sca.yml`.

What you do on the install:

1. **Follow the releases** of the repository you install from (`vX.Y.Z` tags; a PATCH release is
   fixes and patched bases only), and read the release notes in [RELEASES.md](../RELEASES.md) for
   new settings and one-time steps.
2. **Upgrade** with [INSTALL section 10](../INSTALL.md#10-upgrading) and [section 3](#3-upgrade) of
   this guide: back up, check out, compare `.env`, rebuild (or pull the released images), run the
   collation check when the Postgres image changed.
3. **Check your checkout** if you want the same answer locally. The EOL gate needs only Python's
   standard library; from the repository root:

   ```sh
   python3 tools/ci/check_eol.py
   ```

   It exits 1 and names the runtime when one is near or past its end of support in the version you
   run.
4. **Patch the VM itself**: the operating system and the Docker engine are outside Atlas and its
   CI.

## What CI tests and what it does not

| Area | Tested by CI | Not tested |
|---|---|---|
| Backup | `atlas-admin backup run`, `--verify-only`, corruption detection, retention, routine schedule and `ATLAS_BACKUP_KEEP`, no credential in output, the backup verbs with SSO settings but no OIDC files (`verify_crunch_backup.py`, against a test database); the `atlas-backup` compose service, statically; `backup run` on the OSS stack (smoke leg 8d), and with SSO on (leg 9f) | the loop as a long-running service on the OSS stack; the `ATLAS_BACKUP_HOST_DIR` bind mount; the plain `pg_dump`; copying files out of the volume; backing up `secrets/` |
| Restore | `atlas-admin backup restore` and `rollback`: checksum first, dry run, in-use refusal, staging, safety copy (`verify_crunch_backup.py`); backup, change, restore, identical counts in every table on the OSS stack (smoke leg 8d), and the same with SSO on, its accounts and IdP memberships included (leg 9f) | restoring onto a new VM with the secrets put back; a full restore of a backup from an older release (the smoke's `upgrade` job dry-runs one); restoring a plain SQL dump |
| Upgrade | `atlas-migrate upgrade` idempotent and equal to startup's schema (`verify_upgrade_command.py`, smoke leg 2), also with the SSO settings set and no OIDC files (the same script, smoke leg 9h); the released images' checks and gate, `release.yml` rendered and pinned (`images.yml`, `oss.yml`); the previous release run from its published images, backed up, upgraded to the checkout, every table's count kept, old passwords, token and data working (smoke `upgrade` job) | an upgrade that skips releases; moving from one released image set to the next; rolling back |
| Collation | `db_collation_check.sh` in report mode on the upgraded OSS install (smoke `upgrade` job) | `--fix` against an OSS install after a real Debian change |
| Tokens | `token mint`, `list`, `revoke`, the user verbs (backend suite); `token mint --out` and a connected client (smoke leg 8) | - |
| Secrets files | first issue by `init-secrets.sh` (smoke leg 1); the database password rotated by `rotate-db-password.sh` with data kept, the old password refused, and a failed rotation rolled back (smoke leg 10) | replacing another file by hand and recreating; a rotation with `atlas-backup` running or `retrieval.yml` |
| Certificates | `init-certs.sh` and the TLS front, other CA refused, mTLS (`oss_tls_front.sh`); the public name from `.env` and kept on `--force` (`test_init_certs.sh`); the expiry warning in `/api/health` (`verify_cert_expiry.py`); first issue and the door (smoke legs 1 and 6) | `init-certs.sh --force` on a running install; redistributing `ca.crt` |
| Gateway | render, validate, start, allow-list, wrong CA refused, the TLS door (smoke legs 5b and 6) | - |
| Health and logs | every service healthy, `/healthz` through the door (smoke legs 1, 2 and 6) | log rotation (outside Atlas) |
| Audit export | format, filters, access rule, purge (`verify_audit_export.py`) | the curl call from a collector |
| Feature switches | the backend and MCP suites with every feature off; `verify_feature_flags.py` in both | - |
| Patches | SCA, EOL gate, SAST, DAST, image gate (`sbom-sca.yml`, `sast.yml`, `dast.yml`, `images.yml`) | - |
