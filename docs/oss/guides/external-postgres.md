---
audience: operations-security
---

# External or managed PostgreSQL

The OSS install runs its own database, the `atlas-postgres` container. This page is for an install
that should use a PostgreSQL you already run instead: a managed service from your cloud provider, or
a server your database team looks after.

Atlas supports **PostgreSQL only**, hosted anywhere. There is no other database backend. The
overlay `deploy/oss/external-db.yml` runs the install against such a server instead of
`atlas-postgres`. Everything below was checked against the code and, where it says **Tested**,
run by CI's install smoke; what still does not work is listed as such, not described as working.

It assumes the install described in [INSTALL.md](../INSTALL.md). Every setting named here is in
[CONFIG.md](../CONFIG.md). Backup, restore and upgrade for the bundled database are in
[Operations](operations.md).

## Read this first: what works

**Add the overlay `external-db.yml`, put your URL in `secrets/db_url` and, for a verified
certificate, your database's CA in `secrets/db-ca.crt`.** `atlas-postgres` then never starts.

| Part | Supported | How, or why not |
|---|---|---|
| `atlas-api`, `atlas-migrate` and `atlas-backup` reading a URL that names another host | yes | they read the URL from `ATLAS_DB_URL_FILE`; nothing in the code assumes `atlas-postgres` |
| Reaching a database outside the compose project | yes, with `external-db.yml` | it puts those three services on the `egress` network (see [Running the install on it](#running-the-install-on-it)) |
| Running without the `atlas-postgres` container | yes, with `external-db.yml` | it moves `atlas-postgres` to a profile nothing turns on, and removes `atlas-migrate`'s dependency on it |
| TLS with `sslmode=require` | yes | the URL's query parameters reach libpq (see [TLS](#tls-to-the-database)) |
| `sslmode=verify-full` (or `verify-ca`) against your database's CA | yes, with `external-db.yml` | it mounts the CA as the secret `db_ca`; the URL names it with `sslrootcert=/run/secrets/db_ca` |
| `atlas-admin backup run`, `backup restore`, `backup rollback` | yes, with conditions | a role with `CREATEDB` that owns the database, and PostgreSQL 16 or older (see [Backup and restore](#backup-and-restore)) |
| Client certificates (`sslcert`, `sslkey`) | **no** | no secret for them is mounted; password authentication only |
| `rotate-db-password.sh` | **no**, by design | it refuses a URL that does not point at `atlas-postgres` |
| `tools/db_collation_check.sh` | **no** | it runs `psql` inside the `atlas-postgres` container |
| PostgreSQL 17 or later | **untested**; backup **no** | Atlas is tested on 16; the backup image's `pg_dump` 16 refuses a newer server |

## When to use it

Use an external PostgreSQL when:

- your organisation requires databases to run on a managed service or a database team's servers,
  with their backups, monitoring, patching and failover;
- you need high availability or point-in-time recovery, which the single `atlas-postgres`
  container does not give you;
- policy keeps data off the application host.

Keep the bundled `atlas-postgres` when none of that applies. It is what CI installs and tests, and
everything in [Operations](operations.md) is written for it.

## What the database needs

### Version

**PostgreSQL 16.** That is the version `atlas-postgres` runs (`deploy/postgres/pgvector.Dockerfile`,
on `pgvector/pgvector:...-pg16-...`), the version CI's disposable test databases run
(`postgres:16-alpine`), and the version the backup image's client tools are copied from.

- The application itself has not been tested on any other major version.
- The backup image carries `pg_dump` and `pg_restore` **16** (`backend/Dockerfile`, stage
  `backup`), and checks that at build time. `pg_dump` refuses to dump a server of a newer major
  version, so `atlas-admin backup run` does not work against PostgreSQL 17 or later.
- `atlas-admin backup restore` drops temporary databases with `DROP DATABASE ... WITH (FORCE)`,
  which needs PostgreSQL 13 or later.

### Extensions

**The base install needs no extension.** Full-text search uses the built-in `english` text search
configuration and GIN indexes, which every PostgreSQL has.

The [retrieval add-on](retrieval.md) needs **pgvector**. When it is turned on, `atlas-api` checks
`pg_available_extensions` for `vector` and runs `CREATE EXTENSION IF NOT EXISTS vector`
(`backend/retrieval/setup.py`). On a managed service:

- the provider must offer pgvector (most do, under the name `vector`), and
- either the Atlas role may create it, or your database team creates it in the Atlas database
  first. The `IF NOT EXISTS` means an extension that is already there is used as it is.

Without pgvector the add-on reports itself unavailable and search falls back to keywords; the rest
of Atlas is unaffected.

### A database and a role

Ask for **one database** (the bundled install calls it `atlas`) and **one login role** for Atlas.
Atlas shares the database with nothing else and keeps all its tables in the `public` schema: the
backup verifier counts `public.node`, `public.edge` and `public.graph_event` by that name.

What the role does, from the code:

| Operation | Who runs it | What it needs |
|---|---|---|
| `CREATE TABLE`, `CREATE SEQUENCE`, `CREATE INDEX`, `ALTER TABLE ... ADD COLUMN` in `public` | `atlas-migrate upgrade`, and `atlas-api` at startup | `CREATE` on schema `public` |
| reads and writes on its own tables | `atlas-api` | owner of the tables (it created them) |
| `CREATE EXTENSION vector` | `atlas-api`, retrieval add-on only | the right to create that extension, or it already exists |
| `CREATE DATABASE`, `DROP DATABASE` (a temporary copy, to verify each backup) | `atlas-admin backup run` | `CREATEDB` |
| `CREATE DATABASE ... OWNER`, `ALTER DATABASE ... RENAME` | `atlas-admin backup restore` and `rollback` | `CREATEDB`, ownership of the Atlas database, and a connection to the `postgres` (or `template1`) database |

The simplest grant that covers all of it is a role that **owns the Atlas database** and has
**`CREATEDB`**. Owning the database is enough for `CREATE` on `public` on PostgreSQL 15 and later,
where `public` belongs to the database owner. The role needs no superuser right: nothing in the
backend runs `ALTER SYSTEM` or creates roles, and the only sessions it ends are those on its own
temporary databases, which it drops `WITH (FORCE)`.

If your database team will not grant `CREATEDB`, the database itself still works; only Atlas's
own backup and restore commands do not, and you rely on the team's backups instead (see
[Backup and restore](#backup-and-restore)).

In the bundled install that role is `atlas`, the image's superuser, which is why none of this comes
up there.

### Encoding and collation

Create the database with **UTF8** encoding. Atlas stores free text in many languages in `text` and
`jsonb` columns. The bundled database is created by the postgres image's `initdb` with its defaults,
UTF8 and the `en_US.utf8` locale.

Text indexes are sorted by the collation library of the server's operating system. When that
library changes under the same data, a text index can end up out of order: lookups miss rows, and a
unique index can admit a duplicate. The bundled install guards against this with
`tools/db_collation_check.sh` ([Operations, the collation check](operations.md#the-collation-check)).
**That script does not work against an external server**: it runs `psql` through `docker exec`
inside `atlas-postgres`. On a managed service the provider changes the library, so ask your
provider or database team how they handle collation version changes. To look for yourself, on
PostgreSQL 15 or later, run this in the Atlas database as any role:

```sql
SELECT datname, datcollversion, pg_database_collation_actual_version(oid) AS actual
FROM pg_database WHERE datname = current_database();
```

If the two versions differ, the repair is `REINDEX DATABASE`, then
`ALTER DATABASE <name> REFRESH COLLATION VERSION`, in that order, as the script does.

A restore with `atlas-admin backup restore` creates its new database with the live database's
encoding, collation, ctype and locale provider (`backend/backup_restore.py`), so a restore keeps
whatever you chose here.

## Pointing the install at it

### The URL, in the secret file

Atlas reads the database URL from **one file**, `deploy/oss/secrets/db_url`. The compose file mounts
it as the secret `db_url` into `atlas-migrate`, `atlas-api` and `atlas-backup`, and fixes
`ATLAS_DB_URL_FILE=/run/secrets/db_url` in their environment, over anything `.env` says. The
install sets `ATLAS_REQUIRE_SECRET_FILES=1`, so a plain `ATLAS_DB_URL` in `.env` is refused at
startup.

**Never put the URL in `.env`, and never type it, with its password, on a command line**: argv is
visible to other users of the host and stays in shell history.

The URL has this form:

```text
postgresql+psycopg2://ROLE:PASSWORD@HOST:5432/DATABASE?sslmode=verify-full&sslrootcert=/run/secrets/db_ca
```

- The `postgresql+psycopg2://` scheme names the driver Atlas ships. A URL with **no driver**,
  `postgresql://` or `postgres://` (such as CloudNativePG's `fqdn-uri`, or what a managed service
  hands you), works as it is: Atlas reads it as `postgresql+psycopg2://` (`backend/dsn.py`). A URL
  that names another driver is used unchanged, and that driver is not in the image.
- **Percent-encode** any character in the role or password that has a meaning in a URL (`@ : / ? #
  % &` and others). `init-secrets.sh` uses a hex password for exactly that reason. A long random
  password made only of letters and digits needs no encoding.

`init-secrets.sh` writes a `db_url` pointing at `atlas-postgres`, and it refuses to overwrite an
existing secret. Run it first as [INSTALL section 3](../INSTALL.md#3-secrets-and-certificates-init-secretssh)
describes, so it also writes the peppers and certificates, and then replace `db_url`. One way that
keeps the password off the command line and out of history, from `deploy/oss/`:

```sh
umask 077
IFS= read -rs -p 'Atlas database password: ' pw; echo
printf 'postgresql+psycopg2://atlas_app:%s@db.internal.example:5432/atlas?sslmode=verify-full&sslrootcert=/run/secrets/db_ca\n' \
  "$pw" > secrets/db_url
unset pw
chmod 0644 secrets/db_url
```

`read` and `printf` are shell builtins, so the password reaches no other process. Replace the role,
host, database and `sslmode` with yours. The file must be **`0644`** inside the `0700` `secrets/`
directory, as `init-secrets.sh` writes it: the containers run as a non-root UID that is not yours
and cannot read a `0600` file you own.

`init-secrets.sh` also writes `secrets/db_password`. Leave it there: the compose file declares it as
a secret for `atlas-postgres`. It is not used to reach your database.

Back up the new `secrets/db_url` with the rest of `secrets/` ([Operations, back up the
secrets](operations.md#back-up-the-secrets)).

### TLS to the database

The bundled database is reached over an internal Docker network and the URL it gets has no
`sslmode`. An external database is reached over your network, so **use TLS**, with `sslmode` in the
URL.

The query parameters of the URL are passed to libpq, PostgreSQL's client library, both by the
application (SQLAlchemy with psycopg2) and by the backup tools (`backend/crunch_backup.py` turns
every query parameter except a password into the conninfo it gives `pg_dump` and `pg_restore`). So
the libpq parameters work as libpq documents them:

| `sslmode` | What it gives | Works |
|---|---|---|
| `require` | encryption, but the server's certificate is **not** checked | yes |
| `verify-ca` | encryption, and the certificate must be signed by the CA in `sslrootcert` | yes, with the CA as below |
| `verify-full` | as `verify-ca`, and the certificate must name the host in the URL | yes, with the CA as below; **use this one** |

**`verify-ca` and `verify-full` need the CA file inside the container**, named by `sslrootcert=`
in the URL (libpq's default, `~/.postgresql/root.crt`, does not exist in the images).
`external-db.yml` mounts it as the compose secret `db_ca`, at `/run/secrets/db_ca`, into
`atlas-migrate`, `atlas-api` and `atlas-backup`, from the host file `.env` names:

```sh
cp /path/to/your-database-ca.pem secrets/db-ca.crt
chmod 0644 secrets/db-ca.crt
echo 'ATLAS_DB_CA_SOURCE=./secrets/db-ca.crt' >> .env
```

The file is a PEM certificate, or several in one file (a chain, or the provider's bundle; libpq
accepts any of them as a trust anchor). It is not a secret, but keep it beside the others so it is
backed up with them. Unset, `ATLAS_DB_CA_SOURCE` is `/dev/null`: an empty mount nothing reads, which
is what `sslmode=require` needs. The CA is named in the URL rather than through `PGSSLROOTCERT` on
purpose: libpq treats a root certificate file that exists as a request to verify, and would apply
it to `sslmode=require` too.

`sslrootcert=system` (libpq 16's "use the operating system's trust store", for a provider whose
certificate chains to a public CA) is not tested here; mounting the provider's CA bundle as above
works the same everywhere.

**Use the host name the certificate names.** With `verify-full` the host in the URL must match the
certificate. A managed service names its own DNS name; connect by that, not by an IP address. The
containers resolve names through Docker's resolver, which forwards to the host's DNS.

The install's own CA, `secrets/tls/ca.crt`, is not your database's CA; never point
`ATLAS_DB_CA_SOURCE` at it. The install smoke proves that `verify-full` refuses exactly that
mistake.

Client certificates (`sslcert`, `sslkey`) need files in the container in the same way. No secret
for them is mounted: they are **not supported**, and password authentication (scram-sha-256) is
what is tested.

## Running the install on it

The base compose file keeps the database on the node: `atlas-migrate`, `atlas-api` and
`atlas-backup` are on internal networks only, and `atlas-migrate` waits for `atlas-postgres`. The
overlay **`deploy/oss/external-db.yml`** changes exactly that, and nothing else:

- `atlas-postgres` moves to the profile `bundled-postgres`, which nothing turns on, so `up` never
  starts it; `atlas-migrate`'s `depends_on` on it is removed with `!reset`, which needs **Docker
  Compose 2.24.4 or later** (the same as `release.yml`);
- `atlas-migrate`, `atlas-api` and `atlas-backup` join the `egress` network, their route to your
  database server. They stay on `data` too;
- the database CA becomes the secret `db_ca` (see [TLS](#tls-to-the-database)).

It publishes nothing. `atlas-mcp`, `atlas-tls` and the gateway never connect to the database and
are not touched.

**The steps**, from `deploy/oss/`, after [INSTALL sections 1 to 3](../INSTALL.md) (`.env` and
`init-secrets.sh`) and with the database and role made as in [What the database
needs](#what-the-database-needs):

1. Write `secrets/db_url` as in [The URL, in the secret file](#the-url-in-the-secret-file), with
   `sslmode=verify-full&sslrootcert=/run/secrets/db_ca`.
2. Put the database's CA at `secrets/db-ca.crt` (`0644`) and add
   `ATLAS_DB_CA_SOURCE=./secrets/db-ca.crt` to `.env` (see [TLS](#tls-to-the-database)).
3. Add the overlay to every `docker compose` command, most simply once in `.env`:

   ```sh
   echo 'COMPOSE_FILE=docker-compose.yml:external-db.yml' >> .env
   ```

   With other overlays, list them after it (`docker-compose.yml:external-db.yml:oidc.yml`, and
   `release.yml` for published images).
4. Check what compose will run, then start it:

   ```sh
   docker compose config --services      # no atlas-postgres in the list
   docker compose up -d --build --wait    # atlas-migrate runs against your server first
   docker compose logs atlas-migrate      # "[atlas-migrate] upgrade: schema is up to date"
   ```

   The rest of INSTALL.md (the first owner, signing in) is unchanged.

If `atlas-migrate` fails, its log says why: `certificate verify failed` is the wrong CA file or a
certificate that does not name the host in the URL; `no pg_hba.conf entry` or `password
authentication failed` is your server's access rules or the password; a timeout is a firewall
between this host and the server.

**What leaves the node.** With the overlay, those three services can open connections out of the
node through `egress`, as `atlas-api` already can with `retrieval.yml` or `oidc.yml`. Docker does
not limit them to your database's address: if your policy requires that, enforce it on the host
or the network (a firewall rule allowing only the database's address and port from the `egress`
bridge).

**Going back** to the bundled database means a new, empty `atlas-postgres`, not your data: remove
the overlay from `COMPOSE_FILE`, restore `secrets/db_url` to the `atlas-postgres` form
`init-secrets.sh` writes, and restore a backup into it. Nothing moves data between the two for you.

`secrets/db_password` and the `atlas-pgdata` volume are only used by `atlas-postgres`; with the
overlay they are declared and unused. Leave `db_password` in place, as above.

## Backup and restore

With an external database, **the database is backed up where it lives**. The secrets are still
yours to back up.

### The database team's backups

Use the backups, snapshots or point-in-time recovery your provider or database team already runs.
Agree with them how far back they keep, and how long a restore takes.

Whatever restores the database, it **must be restored together with the `secrets/` it was made
with**: the two peppers in `secrets/` verify every password and token, and a restored database
without them signs nobody in ([Operations, what to back up](operations.md#what-to-back-up)). Back
up `secrets/` on your side whenever it changes, and keep it encrypted, off the host.

To restore from the team's backup: stop `atlas-api` (and `atlas-backup` if its profile runs), have
the database restored, put back the matching `secrets/`, and start `atlas-api` again. Nothing in
Atlas has to run first; if the restored database is from an older release, `atlas-migrate upgrade`
brings its schema up to date on the next `docker compose up`. *Untested with Atlas.*

### Atlas's own backup commands

`atlas-admin backup run`, `backup restore` and `backup rollback` ([Operations](operations.md#1-backup))
read the same `db_url` and, with `external-db.yml`, reach the server over `egress` and verify its
certificate with the same `db_ca`. They work against an external server **on these conditions**:

- the role needs `CREATEDB`: every backup is verified by restoring it into a temporary database
  **on the same server** (`atlas_verify_...`), which is then dropped, and a restore creates a staging
  database and renames databases. Without `CREATEDB`, `backup run` fails at that step and says so;
  rely on the team's backups instead;
- the role must own the Atlas database (a restore creates the replacement with the same owner, and
  renames it);
- the server must be PostgreSQL 16 or older, because the image's `pg_dump` is 16;
- a restore connects to the server's `postgres` database (or `template1`) for its maintenance
  statements, and refuses while any other session is connected to the Atlas database, which on a
  shared server includes your team's monitoring if it connects to that database. Stop `atlas-api`
  first, as [Operations](operations.md#1-backup) says;
- the dumps land in the `atlas-backups` volume (or `ATLAS_BACKUP_HOST_DIR`) on **this** host, not on
  the database server;
- a managed service may not let a role create databases at all, or may count them against a quota;
  ask before relying on these commands.

*Untested:* a dump made with the retrieval add-on on holds `CREATE EXTENSION vector`, so restoring
it into the staging database needs the right to create that extension there.

**Tested:** install smoke job `external-db`: `backup run` (pg_dump 16 over `verify-full`, verified
in a temporary database that is then gone), `backup restore --dry-run` changing nothing, `backup
restore` with every table's row count identical to before the backup and the database still owned
by the Atlas role, then `atlas-api` healthy on the restored database. The role there owns the
database and has `CREATEDB`, nothing more.

### A plain dump against the external host

For a copy of your own, for example before an upgrade, run `pg_dump` from any machine with a
PostgreSQL 16 client that can reach the server. Give it the password through a passfile, not on the
command line or in `PGPASSWORD`:

```sh
pg_dump --format=custom --file=atlas-before-upgrade.dump \
  "host=db.internal.example port=5432 dbname=atlas user=atlas_app sslmode=verify-full \
   sslrootcert=deploy/oss/secrets/db-ca.crt passfile=$HOME/.pgpass-atlas"
```

The passfile is one line, `host:port:database:user:password`, mode `0600`. Nothing checks that the
dump restores; restore it into a scratch database to be sure. *Untested with Atlas.*

## Upgrades

**Atlas upgrades** follow [INSTALL section 10](../INSTALL.md#10-upgrading) unchanged in what they do
to the database: `atlas-migrate upgrade` runs on every `docker compose up`, creates whatever tables,
columns and indexes are missing, and never changes or deletes data. It needs the `CREATE` right on
`public` described above. Take your backup first; with an external database that means a fresh
backup or snapshot from your team, or a plain dump as above.

Ignore the steps that are specific to `atlas-postgres`: the collation check script and the
`atlas-postgres` image in the release overlay do not apply to an external server. Keep
`external-db.yml` in `COMPOSE_FILE` across the upgrade (with `release.yml` after it, if you run
published images); `release.yml` still asks for `ATLAS_IMAGE_POSTGRES`, which is then pulled by
nothing. *Untested:* the smoke's `upgrade` job runs the bundled database only.

**PostgreSQL upgrades** are your provider's or database team's. Atlas has nothing to run for a minor
version. For a major version: Atlas is tested on 16 only, and `atlas-admin backup run` stops working
on 17 or later because of its `pg_dump` 16. Check the collation versions after any upgrade that
changed the server's operating system (see [Encoding and collation](#encoding-and-collation)).

**The database password** is rotated where the database lives. `rotate-db-password.sh` refuses a
`db_url` that does not point at `atlas-postgres`. Change the password on your server, write the new
`secrets/db_url` as in [The URL, in the secret file](#the-url-in-the-secret-file), and recreate
`atlas-api` (and `atlas-backup` if its profile runs) so it reads the new file.

## The privacy page

`.env.example` sets `ATLAS_PRIVACY_HOSTING` to "One PostgreSQL database on this organisation's own
server. It is not copied to a third party." That sentence is shown to everyone on the privacy page.
If your database is a managed service, it is no longer true: say where the database is hosted and
who operates it.

## What is tested

**Tested** by the install smoke's `external-db` job (`.github/workflows/install-smoke.yml`), on
every pull request that touches the install. A PostgreSQL 16 that is not part of the install
(`tests/external_db/`: its own compose project and network, nothing published, a certificate for
`db.test` from a throwaway CA, plain TCP refused by its `pg_hba.conf`), a role that owns the
`atlas` database and has `CREATEDB`, and a stranger's install with `external-db.yml` and the URL
written as above, with `sslmode=verify-full&sslrootcert=/run/secrets/db_ca`:

- the rendered shipped files pass `tools/ci/check_oss_compose.py --external-db` (S9: no
  `atlas-postgres`, the route out, the secrets, the file modes, no published 5432), and no
  `atlas-postgres` container exists;
- every service healthy, the `atlas-migrate` one-shot exited 0 against the external server, and
  `atlas-migrate upgrade` run twice more is idempotent; `/healthz` through the ingress;
- a write through the API (a project and its role bindings) and one through MCP, both read back
  through the API, and the MCP write found in the external server's own `node` table; every public
  table owned by the Atlas role; every connection the role holds is TLS (`pg_stat_ssl`);
- `verify-full` with the wrong CA (the install's own) refused with `certificate verify failed`, and
  the password in none of the output;
- backup and restore as in [Atlas's own backup commands](#atlass-own-backup-commands);
- the stack's bridges drop every new connection out, as in the main smoke, and the job fails if
  one was attempted.

The test server is in its own compose project and on its own network, and is also attached to
the stack's `egress` network under the name `db.test`. That is how the stack reaches it: over
`egress`, the route the overlay adds, without crossing between Docker bridges, which the CI
runners' firewall does not allow.

**Not tested:** a real managed service, or a server on another host across a routed network (only
a PostgreSQL container on the runner, reached on `egress` by a Docker DNS alias); a host firewall
that allows only the database's address; PostgreSQL other than 16; `sslmode=verify-ca` and `sslrootcert=system`;
the retrieval add-on, single sign-on or `release.yml` together with the overlay (they render and
pass the compose check, `oss.yml`, but are not run); upgrading an install that uses the overlay.

What the statements above rest on, in the code:

- the URL from `ATLAS_DB_URL_FILE`, with the plain form refused in strict mode:
  `backend/settings.py` (`read_secret`), `backend/db.py`;
- a URL with no driver read as psycopg2, by every reader of it: `backend/dsn.py`;
- the URL's query parameters reaching the backup tools: `backend/crunch_backup.py` (`_conninfo`);
- the rights the backup and restore need: the headers of `backend/crunch_backup.py` and
  `backend/backup_restore.py`;
- the schema steps: `backend/migrate_cli.py`; pgvector: `backend/retrieval/setup.py`;
- the networks, dependencies and secrets: `deploy/oss/docker-compose.yml`, `external-db.yml`,
  `retrieval.yml`, `oidc.yml`, and `tools/ci/check_oss_compose.py` (S3, S9).
