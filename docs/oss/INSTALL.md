---
audience: operations-security
---

# Installing Atlas

This guide takes you from a clean Linux VM to a running Atlas install that people can sign in to and
Claude Code can connect to. The last section covers upgrading an existing install.

Everything here uses the files in `deploy/oss/`: one compose file (`docker-compose.yml`), one
configuration file (`.env`, copied from `.env.example`), and the secrets that
`scripts/init-secrets.sh` writes under `deploy/oss/secrets/`. Every step below is a step the install
smoke runs (`.github/workflows/install-smoke.yml`) on a clean runner for every change to these files.
The [table at the end](#how-this-guide-maps-to-the-install-smoke) shows which part of the smoke
proves each section.

- Every setting is described in [CONFIG.md](CONFIG.md). This guide names only the ones you must set.
- How big the VM should be is in [Server requirements](guides/server-requirements.md).

## 1. Prerequisites

On the VM that will run Atlas:

- **A VM or host of its own.** Do not run Atlas in a WSL distro next to another Docker daemon
  (Docker Desktop, or Docker in a second distro): every WSL 2 distro shares one network stack, so
  the two daemons' firewall and bridge rules interfere with each other.
- **Linux with Docker Engine and the Docker Compose v2 plugin.** `docker compose version` must work,
  and `docker compose up` must accept `--wait` and `--wait-timeout`: Compose v2.17.0 or later
  (`--wait` came in v2.1.1, `--wait-timeout` in v2.17.0).
- **`git`, `bash`, `openssl` and `curl`.** `init-secrets.sh` uses `bash` and `openssl`; `curl`
  checks the install.
- **`python3`** (standard library only), if you will use the optional MCP gateway: its
  configuration is rendered by `scripts/render-gateway.py`.
- **A DNS name** (or a fixed IP address) that people and agents will use to reach the VM. The
  certificate is made for this name. This guide writes it as `atlas.yourcompany.example`:
  wherever that appears below, put your name.
- **Port 443 free** on the VM (or another port, see `ATLAS_TLS_PORT`), and 8444 if you will use the
  gateway.
- **Network access while building.** The images are built from the checkout, so the first
  `docker compose up --build` pulls base images and packages. Once running, no Atlas container
  needs a route out of the VM: only the TLS ingress is published, and every other network is
  internal.

Size the VM from [Server requirements](guides/server-requirements.md).

On the other machines:

- **Claude Code runs on each user's own machine**, not on the VM (it can be the same machine, as in
  section 7). That machine needs the `claude` CLI, `python3` (section 7 uses it to
  pick the server entry out of a JSON file) and a copy of `secrets/tls/ca.crt`.
- **The machine with the browser** needs a copy of `secrets/tls/ca.crt` too. On Linux with
  Chromium the CA goes into the NSS database with `certutil`, from the package `libnss3-tools`
  (Debian, Ubuntu) or `nss-tools` (Fedora, RHEL).

## 2. Clone and configure `.env`

```sh
git clone <the Atlas repository URL> atlas
cd atlas/deploy/oss
cp .env.example .env
```

All later commands run in `deploy/oss/` unless they say otherwise. Compose reads `.env` and resolves
`./secrets/` relative to this directory.

Open `.env` and set the public name. `.env.example` uses `atlas.example.com` as its placeholder;
replace **every** occurrence with your name (`atlas.yourcompany.example` in this guide), and set
the privacy contact:

| Variable | Set it to |
|---|---|
| `ATLAS_PUBLIC_HOST` | The DNS name, without scheme or port: `atlas.yourcompany.example` |
| `ATLAS_PUBLIC_URL` | The one absolute URL Atlas is reached on: `https://atlas.yourcompany.example`, plus `:<port>` if `ATLAS_TLS_PORT` is not 443 |
| `ATLAS_MCP_ALLOWED_HOSTS` | Your name, and your name with the port: `atlas.yourcompany.example,atlas.yourcompany.example:443,...`. Keep the other entries (`localhost:*`, `127.0.0.1:*`, `atlas-mcp:9444`) |
| `ATLAS_MCP_ALLOWED_ORIGINS` | `https://atlas.yourcompany.example` |
| `ATLAS_PRIVACY_CONTACT` | The mailbox your privacy page names, for example `privacy@yourcompany.example` |

One substitution does all of it, which is what the smoke does (with its own test name). Put your
own name and mailbox on the right-hand side of each expression:

```sh
sed -i -e 's/atlas\.example\.com/atlas.yourcompany.example/g' \
       -e 's/privacy@example\.com/privacy@yourcompany.example/' .env
diff .env.example .env    # the whole of what your install changed
```

`ATLAS_PRIVACY_HOSTING`, where your privacy page says the data is hosted, has a usable default:
one PostgreSQL database on your own server, not copied to a third party. That is true of this
install as it stands, so change it (one plain-text paragraph) only if it is not true for you.

`ATLAS_PUBLIC_URL` matters most: the login origins, the hosts on which an agent's Bearer token is
honoured, and the OIDC redirect URI are all derived from it. Keep it in step with
`ATLAS_PUBLIC_HOST` and `ATLAS_TLS_PORT`.

Nothing else needs changing: with the substitution above (and `ATLAS_PRIVACY_HOSTING` if its
default is not true for you), `.env` is complete. The other values are the work install's defaults (project RBAC,
private by default, personal features off, secrets as files only); [CONFIG.md](CONFIG.md) explains
each one. `.env` must never hold a secret: secrets are files, made in the next step.

### Optional: start with Atlas's own world model (`ATLAS_SEED=self`)

`ATLAS_SEED` decides what an **empty** database starts with, once, at the first start. The
install default is `none`: an empty graph plus Atlas's handbook. Set `ATLAS_SEED=self` instead to
start with the world model Atlas keeps of itself: the `atlas` project and its parts (the API, the
MCP service, the board and its panel, the graph engine, search and retrieval, identity and
visibility, and so on), their documents, the edges between them, and a reviewed set of intents.
It carries **no work**: no epics, stories, tasks or decisions, no comments, accounts or grants.
You file your own work against these nodes, and change them as you like, by hand or with agents;
they are ordinary nodes.

```sh
sed -i 's/^ATLAS_SEED=none$/ATLAS_SEED=self/' .env    # before the first `docker compose up`
```

It changes nothing on a database that already holds a node, so setting it later does nothing.
To start over with it, the graph must be empty (a new volume). [CONFIG.md](CONFIG.md#atlas_seed)
lists the profiles.

The profile ships as data in the image (`backend/self_seed_data.py`). Maintainers regenerate it
from a running Atlas with `tools/export_self_seed.py`: `fetch` reads the `atlas` subtree over the
API with a read token passed as a file (`--token-file`), `build` writes the profile and two review
files under `tools/self_seed/` (which intents are seeded, and what was dropped), and `check`
fails on any forbidden pattern. Which nodes are left out is `tools/self_seed/scope.json`, and
which intents are seeded is `tools/self_seed/intents.json`; a change to either is reviewed in the
pull request like any other.

## 3. Secrets and certificates: `init-secrets.sh`

```sh
bash ../../scripts/init-secrets.sh
ls -l secrets secrets/tls
```

It writes, under `./secrets/` and nowhere else:

- `db_password` and `db_url`: the Postgres password, and the connection string that carries it;
- `password_pepper` and `token_pepper`: mixed into every password verifier and token digest.
- `tls/`: when there is no certificate there yet, it runs `scripts/init-certs.sh`, which makes a
  throwaway **install CA** and signs the certificates with it. The CA's private key is deleted
  as soon as it has signed, so nothing more can be signed with it. It writes:
  - `ca.crt`: the certificate every client must trust (browsers, `curl --cacert`, Claude Code);
  - `server.crt` and `server.key`: the certificate of the front door (`atlas-tls`);
  - `client-ca.crt`: the CA the API's TLS front trusts for client certificates (a copy of
    `ca.crt`), and `mcp-client.crt` with `mcp-client.key`: `atlas-mcp`'s client certificate for
    that leg;
  - `api-origin.crt`, `api-origin.key`, `mcp-origin.crt` and `mcp-origin.key`: the certificates
    `atlas-api` and `atlas-mcp` serve on the ingress's internal legs to them, and `origin-ca.crt`,
    which the ingress checks them against, so no hop in the install is plain HTTP.

`server.key` is `0600`; the other files are `0644` inside the `0700` directories `secrets/` and
`secrets/tls/`, because the containers that read them do not run as you. The script ends by
printing section 4's command.

It never prints a secret, and it refuses to overwrite an existing one.

**Back up `./secrets/` with the database.** New peppers sign everyone out and void every token for
good, and the database keeps the password it was first created with. Never run
`init-secrets.sh --force` on an install that has data.

**With your own PKI** (or to use Caddy's internal CA or ACME instead, see `ATLAS_TLS_MODE` in
[CONFIG.md](CONFIG.md)): put your files in place **before** running `init-secrets.sh`, and it uses
them as they are:

```text
secrets/tls/server.crt   full chain; SANs: ATLAS_PUBLIC_HOST and atlas-api (add gateway for the gateway profile)
secrets/tls/server.key   its key
secrets/tls/ca.crt       your root; atlas-mcp trusts exactly this file
```

`init-secrets.sh` then adds only what your PKI did not provide: `atlas-mcp`'s client certificate,
and the internal origin certificates under a separate CA of their own (`init-certs.sh
--origin-only`), which only the ingress trusts.

To use a PostgreSQL you already run (a managed service or a database team's server) instead of the
bundled atlas-postgres, read [External or managed PostgreSQL](guides/external-postgres.md) first.

## 4. Start the stack and check its health

```sh
docker compose up -d --build --wait --wait-timeout 600
docker compose ps -a
```

`--build` builds the images from your checkout; `--wait` returns once every service is healthy.
The first build takes several minutes. What starts, by compose **service** name (the SERVICE
column of `docker compose ps -a`; the container names, in the NAME column, carry the project
prefix and a number, such as `atlas-atlas-api-1`):

| Service | What it is | Healthy means |
|---|---|---|
| `atlas-postgres` | Postgres 16 | `running`, `healthy` |
| `atlas-migrate` | A one-shot `atlas-migrate upgrade`: builds the schema, then exits | `exited (0)`; `atlas-api` does not start until it has |
| `atlas-api` | The board and the API (not published) | `running`, `healthy` |
| `atlas-mcp` | The MCP server (not published) | `running`, `healthy` |
| `atlas-tls` | The TLS ingress, the one published port | `running`, `healthy` |

The optional services do not start unless you ask for them: `atlas-gateway` (section 6),
`atlas-backup` (the backup loop, see [Operations](guides/operations.md)), and `atlas-retrieval`,
a one-shot that only checks the retrieval configuration and exits. Retrieval itself runs inside
`atlas-api` (section 8).

Then check the front door from outside, with the install CA:

```sh
curl --cacert secrets/tls/ca.crt https://atlas.yourcompany.example/healthz
```

An HTTP 200 (`{"ok":true}`) means the ingress answers, the certificate verifies for your name, and
`atlas-api` is alive behind it. Without `--cacert` the same call fails with a certificate error
(curl exit 60): that is correct, and means nothing falls back to an unverified connection.

The schema step can be re-run at any time and changes nothing on a database that is up to date:

```sh
docker compose logs atlas-migrate | tail -20
docker compose exec -T atlas-api atlas-migrate upgrade
```

On a new, empty database the log of the first run says
`[atlas-migrate] upgrade: empty database, schema created (...)`, with the number of tables,
columns and indexes it made. A re-run that finds nothing missing prints
`[atlas-migrate] upgrade: schema is up to date`, and one that adds what a new release needs says
`created what was missing (...)`. A failure prints `[atlas-migrate] upgrade FAILED: ...` and exits
1, and then `atlas-api` does not start.

If a service is not healthy, `docker compose logs <service>` names the reason. The usual ones are a
value left at `atlas.example.com` in `.env`, or a `secrets/` file missing because `init-secrets.sh`
was run from another directory.

**After a host restart**, run `docker compose ps -a` and the `/healthz` check above again.
`atlas-tls` is healthy only while it reaches both `atlas-api` and `atlas-mcp` through its own
legs, so an `unhealthy` `atlas-tls` with the other services healthy means the front door is down.
A 502 from the `curl`, with `i/o timeout` to the origin in `docker compose logs atlas-tls`, means
Docker's networking for the stack was lost in the restart. Recreate the containers; the volumes,
and with them the data, are kept:

```sh
docker compose down
docker compose up -d --wait --wait-timeout 600
```

## 5. The first owner and the first sign-in

Create the first owner. Choose a username; `--break-glass` marks the account as the one that can
still sign in with a password if you later switch sign-in to SSO only (section 8):

```sh
docker compose exec -T atlas-api atlas-admin \
  user add <name> --role owner --break-glass --out /tmp/first-owner.password
```

It prints `created <name> ...` and writes a **temporary password** to the `--out` file inside the
container, a new `0600` file, as `token mint` does in section 7; the terminal shows no secret.
Nobody, the operator included, chooses a password. Move the file out without printing it, and
delete the copy in the container:

```sh
( umask 077; docker compose exec -T atlas-api cat /tmp/first-owner.password > ~/first-owner.password )
docker compose exec -T atlas-api rm /tmp/first-owner.password
```

Hand it over out of band (not on the same channel as the username), then delete
`~/first-owner.password`. The temporary password is single use: the first sign-in with it goes
straight to a forced change (below), and from then on it signs nothing in.

- **Without `--out`, the password is printed on your terminal**, once. Then run the command in a
  terminal that is not logged: not inside `script`, a recorded CI job, a shared tmux scrollback,
  or an agent session (Claude Code or another), because the password is part of the command's
  output and lands wherever that output is kept.
- **For the break-glass account, the vault holds the password set at the forced change**, not the
  temporary one, which is void after that first sign-in. Whoever looks after the vault
  does that first sign-in and stores the new password: it is how you get in when the identity
  provider is down.
- **If the temporary password is lost** (it scrolled away, or it expired unused), issue a new one
  with `docker compose exec -T atlas-api atlas-admin user reset-password <name>`. It prints the
  new one on the terminal, so the same care applies. Running `user add` again for the same name is
  refused: accounts are never recreated.

`atlas-admin` records the operator who ran it as the shell user, or `--actor <name>` (before the
subcommand) if you pass one. More people are added the same way, without `--break-glass` and with
`--role member` (or another role; see [Identity and access](guides/identity-and-access.md)).

**Sign in.** The browser must trust the install CA first (skip this if the certificate comes from
your PKI or ACME):

- Copy `secrets/tls/ca.crt` (the certificate only; it is public) to the machine with the browser,
  if that is not the VM.
- Import it as a trusted certificate authority in the browser or the operating system's trust
  store. On Linux with Chromium it goes into the NSS database (`certutil` is in `libnss3-tools`);
  run this where the copy is, with the path to it:
  `certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n atlas-install-ca -i secrets/tls/ca.crt`.
- Open `https://atlas.yourcompany.example/` and sign in with the username and temporary password.
- You are sent to the forced password change (`/auth/password/change`). Until you have changed it,
  the session reaches that form and nothing else.
- After the change you land on the board. File a first story to see that writes work.

## 6. Optional: the MCP gateway (`--profile gateway`)

The gateway is [AgentGateway](https://agentgateway.dev) in front of `atlas-mcp` (and any other MCP
servers you list), on its own HTTPS port. Agents can connect to it instead of to `/mcp` on the main
door; each call is still made with the agent's own token. Skip this section if you do not need it.

```sh
python3 ../../scripts/render-gateway.py         # writes ./gateway/agentgateway.yaml
docker compose --profile gateway config --quiet
docker compose --profile gateway pull atlas-gateway
docker compose --profile gateway run --rm --no-deps atlas-gateway -f /etc/agentgateway/config.yaml --validate-only
docker compose --profile gateway up -d
curl --cacert secrets/tls/ca.crt -o /dev/null -w '%{http_code}\n' https://atlas.yourcompany.example:8444/mcp
# 406
```

- `render-gateway.py` reads the server list from `deploy/gateway/gateway.yaml` and the public
  names from `.env`, and refuses an unsafe configuration (an `http://` server that would be given a
  token, a CORS origin of `*`, an unknown option). To change the server list or the tool
  allow-list, copy `../gateway/gateway.yaml` to `gateway/servers.yaml`, edit the copy, and pass
  `--servers gateway/servers.yaml`. The header of `deploy/gateway/gateway.yaml` explains each field.
- `--validate-only` has the pinned gateway release parse the rendered file before it serves.
- MCP session-ID encryption is off by default (`config.session.key: disabled` in the rendered
  file): the gateway is optional and LAN-only, and a session ID is not a secret, since every call
  carries its own token. To turn it on, create a key file and name it in `.env`, then render and
  recreate:

  ```sh
  ( umask 077; openssl rand -hex 32 > secrets/gateway_session_key )
  echo 'ATLAS_GATEWAY_SESSION_KEY_FILE=./secrets/gateway_session_key' >> .env
  python3 ../../scripts/render-gateway.py         # "session IDs encrypted ... written 0600"
  docker compose --profile gateway up -d --force-recreate atlas-gateway
  ```

  The file must hold exactly 64 hex characters (a 32-byte AES-256-GCM key) and be readable by you
  alone; `render-gateway.py` refuses it otherwise, without printing it. AgentGateway v1.7.0 takes
  the key only inline in its configuration or as an environment variable, so the script writes it
  into `gateway/agentgateway.yaml` and writes that file `0600`: from then on the rendered file is a
  secret too (it is git-ignored; do not print or copy it). The key never goes in `.env`, on a
  command line or into the container's environment. A new key ends every open MCP session through
  the gateway; to turn encryption off again, remove the line from `.env` and render again.
- The `curl` gets an HTTP answer once the gateway's TLS door is up. `406` is the expected answer
  to a plain GET: the door works, and an MCP client, which sends the right `Accept` header, gets
  further. A certificate error (curl exit 60) means the CA is not trusted. No answer (`000`), or
  `SSL_ERROR_SYSCALL` (curl exit 35), means it is not up yet: `atlas-gateway` has no healthcheck
  (its image has no shell to run one), so `up -d` returns as soon as the container starts, and
  `--wait` would not wait any longer. Run the `curl` again after a few seconds.
- Agents then use `https://atlas.yourcompany.example:8444/mcp` (port `ATLAS_GATEWAY_PORT`) with their own
  token and the same CA.
- After changing `ATLAS_PUBLIC_URL`, `ATLAS_PUBLIC_HOST` or the server list, render again and
  recreate: `docker compose --profile gateway up -d --force-recreate atlas-gateway`.
- To start the gateway with every `docker compose up`, set `COMPOSE_PROFILES=gateway` in `.env`.

## 7. Connect Claude Code (`clients/claude-code`)

`clients/claude-code/README.md` is the full guide, including Windows and the plugin. On Linux or
macOS it comes to four steps. The token is never printed, never on a command line and never in an
environment variable: it lives in one file readable only by you.

**Step 1: a token into `~/.config/atlas/token`.** There are two ways to get one:

- **The person makes their own.** Anyone who signs in to the board with a password opens
  **Settings -> Agent credentials** and creates the token there, re-entering their password. The
  board shows it once; save it straight into `~/.config/atlas/token`, readable by you alone, with
  the commands below (paste the token after the `cat` line, press Enter, then Ctrl-D), and go on at
  the `cp` of the helper further down. [Agents](guides/agents.md#1-get-a-token) has the details.

  ```sh
  mkdir -p ~/.config/atlas && chmod 700 ~/.config/atlas
  ( umask 077; cat > ~/.config/atlas/token )
  chmod 600 ~/.config/atlas/token    # umask applies to a new file only; this covers an existing one
  stat -c %a ~/.config/atlas/token   # 600 (on macOS: stat -f %Lp)
  ```

  The token goes through the terminal into the file, so it is not on a command line and not in
  your shell history.
- **An operator mints it** on the VM, in `deploy/oss/`. It is written only to the `--out` file
  inside the container:

```sh
docker compose exec -T atlas-api atlas-admin \
  token mint --user <name> --purpose claude-code --scopes read,write --out /tmp/claude-code.token
```

On the machine that runs Claude Code (here the same machine; from another one, run the `cat` over
`ssh` with the redirect on your side), move the file out and delete the copy in the container:

```sh
mkdir -p ~/.config/atlas && chmod 700 ~/.config/atlas
( umask 077; docker compose exec -T atlas-api cat /tmp/claude-code.token > ~/.config/atlas/token )
docker compose exec -T atlas-api rm /tmp/claude-code.token
chmod 600 ~/.config/atlas/token    # umask applies to a new file only; this covers an existing one
cp ../../clients/claude-code/helpers/atlas-mcp-headers.sh ~/.config/atlas/
sh ~/.config/atlas/atlas-mcp-headers.sh | sed -E 's/(Bearer )[^"]+/\1<redacted>/'
# {"Authorization":"Bearer <redacted>"}
```

The first-run setup guide (`atlas_setup_guide`, the plugin's `setup-guide` skill) needs more: an
owner's token with the scopes `read,write,admin`, made by the owner under Settings -> Agent
credentials, or by an operator with `token mint --user <owner> ... --scopes read,write,admin`. A
`read,write` token gets 403 from it. Keep it in its own file next to the normal one, saved the same
way:

```sh
( umask 077; cat > ~/.config/atlas/setup-token )    # paste, Enter, Ctrl-D
chmod 600 ~/.config/atlas/setup-token
stat -c %a ~/.config/atlas/setup-token              # 600
```

The helper takes the token file as its argument, so after step 4 add a second server, `atlas-setup`,
that passes this file (the single quotes keep `$HOME` for the helper's shell to expand):

```sh
claude mcp add-json atlas-setup '{"type":"http","url":"https://atlas.yourcompany.example/mcp/","headersHelper":"sh \"$HOME/.config/atlas/atlas-mcp-headers.sh\" \"$HOME/.config/atlas/setup-token\""}' --scope user
claude mcp list
# atlas-setup: https://atlas.yourcompany.example/mcp/ (HTTP) - ✔ Connected
```

**Restart Claude Code** before you use it: a running session does not load the new server's tools.
When the setup is done, remove the server (`claude mcp remove atlas-setup --scope user`), revoke the
token on the board or with `atlas-admin token revoke <id>`, and delete `~/.config/atlas/setup-token`.

**Step 2: the template, pointed at your install.** Keep the `/mcp/` path and its trailing slash.
The file goes next to the token, not into the checkout:

```sh
sed 's#https://atlas.example.com#https://atlas.yourcompany.example#' \
  ../../clients/claude-code/mcp.template.json > ~/.config/atlas/atlas.mcp.json
```

**Step 3: trust the install CA.** Copy `secrets/tls/ca.crt` (the certificate only) to the Claude
Code machine, for example to `~/.config/atlas/ca.crt`, and point `NODE_EXTRA_CA_CERTS` at it in the
`env` block of `~/.claude/settings.json` (merge it into the file if it already has an `env` block;
the path must be absolute, `$HOME` is not expanded there):

```json
{ "env": { "NODE_EXTRA_CA_CERTS": "/home/you/.config/atlas/ca.crt" } }
```

Then **restart Claude Code**. Node reads this variable only when the process starts, so `/mcp`
reconnect in a running session is not enough. (Exporting it in the shell you start `claude` from
also works, but only for that shell.)

**Step 4: add it to Claude Code and check it.** `python3` picks the server entry out of the file:

```sh
claude mcp add-json atlas "$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["mcpServers"]["atlas"]))' ~/.config/atlas/atlas.mcp.json)" --scope user
claude mcp list
# atlas: https://atlas.yourcompany.example/mcp/ (HTTP) - ✔ Connected
docker compose exec -T atlas-api atlas-admin token list --user <name>
```

To test before you restart Claude Code, run the check with the variable set for that one command:
`NODE_EXTRA_CA_CERTS="$HOME/.config/atlas/ca.crt" claude mcp list`.

Check that `claude mcp get atlas` (or `/mcp` in a session) shows **your install's URL**. A
project's `.mcp.json` that defines a server with the same name takes precedence over the
user-scope one, with no warning, so in that project you would reach another server. Start
`claude` outside such a project, or add yours under another name.

A certificate error means the CA step is missing (or Claude Code was not restarted after it); a
401 means the token file is wrong, revoked or expired. Rotation and revocation are in the client
pack's README.

## 8. Optional: semantic retrieval

The base install has full-text search only, and `atlas-api` has no route out of the VM. The
retrieval add-on runs inside `atlas-api` and calls an OpenAI-compatible embeddings endpoint that you
provide; no model ships with Atlas. [Retrieval](guides/retrieval.md) explains it; turning it on is:

1. Put the endpoint's key in `./secrets/embed_key` (one line; `chmod 0644`, inside the `0700`
   `secrets/` directory, for the same reason `init-secrets.sh` writes its files that way: the
   container's user is not you). An Azure endpoint that takes Entra ID tokens has no key: set
   `ATLAS_EMBED_AUTH_HEADER=entra` and `ATLAS_EMBED_KEY_SOURCE=/dev/null` instead, leave
   `ATLAS_EMBED_KEY_FILE` unset, and see
   [Retrieval](guides/retrieval.md#entra-id-managed-identity-or-workload-identity) for the
   workload-identity overlay.
2. Fill in the commented "Retrieval" block of `.env`: `ATLAS_RETRIEVAL=on`, `ATLAS_EMBED_URL`
   (`https://` is required), `ATLAS_EMBED_MODEL`, `ATLAS_EMBED_DIM`,
   `ATLAS_EMBED_KEY_FILE=/run/secrets/embed_key`, `ATLAS_EMBED_AUTH_HEADER`, and
   `ATLAS_EMBED_CA_BUNDLE=/run/secrets/atlas_ca` if the endpoint's certificate comes from this
   install's CA. Never put the key itself in `.env`. Before step 3,
   `docker compose --profile retrieval run --rm atlas-retrieval` checks the block: it reads it,
   refuses what could not be right, and exits, without contacting the endpoint. That one-shot is
   all the `atlas-retrieval` service does.
3. Recreate `atlas-api` with the overlay, which gives it its route out and the key:

   ```sh
   docker compose -f docker-compose.yml -f retrieval.yml up -d --no-deps --wait atlas-api
   ```

From then on, pass both files to every `docker compose` command for this install (or set
`COMPOSE_FILE=docker-compose.yml:retrieval.yml` in `.env`). A broken configuration never stops
Atlas: health reports `retrieval: unavailable: <reason>` or `degraded`, and search stays full-text.
To turn it off, remove the block from `.env` and recreate `atlas-api` on `docker-compose.yml` alone.

## 9. Optional: single sign-on (OIDC)

Until you do this, sign-in is local accounts with passwords. With OIDC, people sign in through your
identity provider (Entra ID, Keycloak or another OpenID provider), groups come from the IdP, and the
break-glass owner from section 5 is the only password login left.

**Register Atlas in the IdP** as a confidential client (authorization code with PKCE), with the
redirect URI `https://atlas.yourcompany.example/auth/oidc/callback`.

**Before switching**, move any existing local account that should become an SSO account, keeping
its history and its agents:

```sh
docker compose exec -T atlas-api atlas-admin user link-idp <name> --subject <the IdP's id for them>
```

A new SSO person is `user add <name> --no-password` then `link-idp` (SSO-only), or `user add <name>`
then `link-idp`, which lets them set a personal password later from the board. Either way they
reach the board through SSO at once; see
[SSO-only, or SSO with a personal password](guides/identity-and-access.md#single-sign-on-with-oidc).
`link-idp` keeps the old username as the account's local sign-in name. With `local,oidc` in
`ATLAS_AUTH_PROVIDERS`, a personal password then signs in under that name when the IdP is down. With
the `oidc`-only block below, it does not; see
[the fallback](guides/identity-and-access.md#a-personal-password-as-a-fallback-when-sso-is-down).

**The secrets**, as files under `./secrets/` (`chmod 0644`, as in section 8):

- `secrets/oidc_client_secret`: the client secret (or use a key: `ATLAS_OIDC_TOKEN_AUTH=private_key_jwt`
  and `ATLAS_OIDC_CLIENT_KEY_FILE`, see [CONFIG.md](CONFIG.md));
- `secrets/oidc_group_map.yml`: which IdP groups reach Atlas, and as which Atlas group:

  ```yaml
  groups:
    <IdP group value>: <Atlas group name>
  ```

**The overlay `oidc.yml`**, shipped in `deploy/oss/`, gives `atlas-api` those secrets and its
route to the IdP. In the base layout `atlas-api` sits on internal networks only, and it calls the
IdP itself (discovery, the token endpoint, the signing keys), so without the overlay "Sign in with
SSO" answers *Single sign-on unavailable*. The overlay adds two things and nothing else:

- `atlas-api` joins the `egress` network, its route out of the host;
- the compose secrets `oidc_client_secret` (from `./secrets/oidc_client_secret`), `oidc_group_map`
  (from `./secrets/oidc_group_map.yml`) and `oidc_client_key` (empty unless you name a key file),
  each mounted at `/run/secrets/<name>`.

Other host files are named in `.env` with `ATLAS_OIDC_CLIENT_SECRET_SOURCE`,
`ATLAS_OIDC_CLIENT_KEY_SOURCE` and `ATLAS_OIDC_GROUP_MAP_SOURCE`
([CONFIG.md](CONFIG.md#read-by-the-compose-file-only)). With `private_key_jwt`, set
`ATLAS_OIDC_CLIENT_KEY_SOURCE=./secrets/oidc_client_key` and
`ATLAS_OIDC_CLIENT_SECRET_SOURCE=/dev/null`. If the IdP's certificate comes from a private CA (an
internal Keycloak, for example), uncomment the `idp_ca` lines in `oidc.yml`; Entra ID needs none.

**The settings.** Fill in the commented "Sign-in" block of `.env`, with the paths where `oidc.yml`
mounts the secrets:

```sh
ATLAS_AUTH_PROVIDERS=oidc
ATLAS_LOCAL_LOGIN=break-glass
ATLAS_OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
ATLAS_OIDC_PRESET=entra
ATLAS_OIDC_CLIENT_ID=<application (client) id>
ATLAS_OIDC_TOKEN_AUTH=client_secret_basic
ATLAS_OIDC_CLIENT_SECRET_FILE=/run/secrets/oidc_client_secret
ATLAS_OIDC_GROUP_MAP_FILE=/run/secrets/oidc_group_map
ATLAS_OIDC_REQUIRED_GROUPS=<the IdP group everyone who may sign in is in>
ATLAS_OIDC_MAX_SESSION_HOURS=12
```

**Recreate `atlas-api`** with the overlay:

```sh
docker compose -f docker-compose.yml -f oidc.yml up -d --no-deps --wait atlas-api
```

As with retrieval, pass both files to every later `docker compose` command (or set
`COMPOSE_FILE=docker-compose.yml:oidc.yml` in `.env`; with retrieval too,
`docker-compose.yml:retrieval.yml:oidc.yml`).

The login page now shows "Sign in with SSO". An account is created at a person's first login, as a
member, only if they are in one of `ATLAS_OIDC_REQUIRED_GROUPS`, and every later login checks that
again. The break-glass owner signs in with their password at the unlinked
`https://atlas.yourcompany.example/auth/login?break_glass=1`; every such sign-in is recorded as a
`login_break_glass` event. [Identity and access](guides/identity-and-access.md) covers group
mapping, offboarding (`ATLAS_OIDC_REVALIDATE_DAYS`) and letting Claude Code sign in with an IdP
token instead of a minted one.

**Agents for SSO people.** On an SSO install, signing in through the IdP is the default way to
connect an agent: set `ATLAS_OAUTH_RESOURCE` (and its companions in the commented block below the
sign-in block of `.env`) and Claude Code signs in through your IdP with no minted token. For clients
that need a minted token, add `ATLAS_OIDC_STEP_UP=on` to `.env`: an SSO person then creates their
own agent credential in Settings, Agent credentials, after a fresh sign-in at the IdP, instead of
the password they do not have. With Entra ID, add the optional ID-token claim `auth_time` in the app
registration first. Without either, section 7's `atlas-admin token mint --user <idp>:<subject>` is
the way. [Identity and access](guides/identity-and-access.md#agent-credentials-for-sso-people) has
the details.

## 10. Upgrading

An upgrade is a new checkout, a rebuild, and the schema step. The schema step (`atlas-migrate
upgrade`) only creates what is missing; it never changes or deletes data, and running it twice
changes nothing.

Releases are `vX.Y.Z` tags; [RELEASES.md](RELEASES.md) has the versioning scheme and each release's
notes. Every release also publishes its images: to pull them instead of building, follow
[Upgrading with released images](RELEASES.md#upgrading-with-released-images), which is these same
steps with the images pinned by digest in `.env`.

1. **Back up** the database and `./secrets/` together (see [Operations](guides/operations.md)),
   for example:

   ```sh
   ( umask 077; mkdir -p ~/atlas-backups &&
     docker compose exec -T atlas-postgres pg_dump -U atlas -d atlas > ~/atlas-backups/atlas-$(date +%F).sql )
   ( umask 077; tar -czf ~/atlas-backups/atlas-secrets-$(date +%F).tgz secrets .env )
   ```

   The dump holds every account's password verifier and every token digest, and the archive holds
   the peppers and the database password, so treat both like `secrets/`: `umask 077` makes them
   readable by you alone, and they go outside the checkout, where a `git add` cannot pick them up.
   The archive is another unencrypted copy of the peppers on the VM: encrypt it and move it off
   the VM, or delete it once the upgrade is done
   ([Back up the secrets](guides/operations.md#back-up-the-secrets)).

2. **Get the new version** from the repository root, then return to `deploy/oss/`:

   ```sh
   git rev-parse HEAD    # note it: the commit you upgrade from, for step 3
   git fetch --tags
   git checkout <the release tag>
   cd deploy/oss
   ```

   If `git tag` lists no release tags (a repository that has not published one yet, or a copy made
   without its tags), there is no release to check out: upgrade to a newer commit of the default
   branch instead, with `git pull --ff-only`, and read `git log` since your old commit in place of
   the release notes.

3. **Compare your `.env` with the new `.env.example`** and add any new settings the release's notes
   in [RELEASES.md](RELEASES.md) ask for: `diff .env.example .env`. That diff also shows your own
   settings; to see only what the release changed in the example, use
   `git diff <old commit> -- .env.example` with the commit you noted in step 2. (After
   `git pull --ff-only`, `ORIG_HEAD` names it too; `git checkout` of a tag does not set it.)
   **An install made before the
   origin listeners** (no `secrets/tls/origin-ca.crt`) adds their certificates once, with no change
   to `ca.crt` or to any client:
   `ls secrets/tls/origin-ca.crt 2>/dev/null || bash ../../scripts/init-certs.sh --origin-only`
   ([Operations, section 3](guides/operations.md#the-origin-certificates-one-step-for-an-install-made-before-them)).
4. **Rebuild and restart.** The `atlas-migrate` one-shot runs the schema step before `atlas-api`
   starts, exactly as on the first install:

   ```sh
   docker compose up -d --build --wait --wait-timeout 600
   docker compose ps -a
   ```

   With the retrieval or OIDC overrides, pass the same `-f` files as before (or rely on
   `COMPOSE_FILE` in `.env`).
5. **Run the schema step once more by hand**, and check the door:

   ```sh
   docker compose exec -T atlas-api atlas-migrate upgrade
   curl --cacert secrets/tls/ca.crt https://atlas.yourcompany.example/healthz
   ```

6. **With the gateway**, render its configuration again and recreate it:
   `python3 ../../scripts/render-gateway.py` and
   `docker compose --profile gateway up -d --force-recreate atlas-gateway`.

An install whose database was first created before 2026-10-26 ran an older Postgres base image;
after upgrading, run the collation check from `deploy/oss/`:
`bash ../../tools/db_collation_check.sh "$(docker compose ps -q atlas-postgres)"`.

## How this guide maps to the install smoke

The install smoke (`.github/workflows/install-smoke.yml`) runs these steps on a clean runner, in
this order. Each leg is its own CI step, so a red run names the section that broke.

| Guide section | Smoke leg | What the smoke runs there |
|---|---|---|
| 1. Prerequisites | *prepare the runner* | Python, Node, `docker compose version`, the test name in `/etc/hosts` |
| 2. Clone and `.env` | leg 1 | `cp .env.example .env`; `sed` of `atlas.example.com` and the privacy contact; `diff .env.example .env` |
| 3. `init-secrets.sh` | leg 1 | `bash ../../scripts/init-secrets.sh`; `ls -l secrets secrets/tls` |
| 4. Start and health | leg 1, leg 2, leg 6 | `docker compose up -d --build --wait --wait-timeout 600`; `docker compose ps -a` and every service's health; `atlas-migrate` exited 0, `atlas-migrate upgrade` twice more (leg 2); `curl --cacert ca.crt https://<host>/healthz` (leg 6, `tools/ci/oss_install_tls.sh`). The *after a host restart* note is not run |
| 5. First owner and sign-in | leg 3, leg 7, leg 9 | `atlas-admin user add <name> --role owner` and the forced change through the real form (leg 3, `tests/bootstrap_users.py`); `--break-glass` (leg 9); the install CA into the browser's NSS database, sign in, file a story, see it on the board (leg 7) |
| 6. Gateway | leg 5b, leg 6 | `render-gateway.py`; `--profile gateway config --quiet`, `pull`, `run ... --validate-only`, `up`; the TLS door on 8444 (leg 6, T5) |
| 7. Claude Code | leg 8 | `token mint --out`; the file moved without printing; the helper and the template installed; `claude mcp add-json ... --scope user` (the smoke picks the server entry out with `jq`, the guide with `python3`); `claude mcp list` reports Connected with `NODE_EXTRA_CA_CERTS` set for the command; `token list` |
| 8. Retrieval | leg 8b | `secrets/embed_key`; the Retrieval block in `.env`; `-f docker-compose.yml -f retrieval.yml up -d --no-deps --wait atlas-api`; back to the base file |
| 9. OIDC | leg 9 | `user add ... --break-glass`; `user link-idp`; the Sign-in block in `.env`; the client secret and group map as compose secrets; `up -d --no-deps --wait atlas-api` with the override |
| 10. Upgrading | leg 2, `upgrade` job | `docker compose exec -T atlas-api atlas-migrate upgrade`, idempotent (leg 2). The `upgrade` job: the previous release from its published images, a backup, then this checkout over the same `.env`, `secrets/` and volumes, `diff .env.example .env`, `up -d --build --wait`, the schema step by hand, the door, the collation check |

**What the smoke does that is not an install step**, and so is not in the guide:

- **Test scaffolding.** The test name `atlas.test` in `/etc/hosts`; a resource sampler for sizing;
  a compose override that starts `atlas-migrate`, `atlas-api` and `atlas-mcp` under an arbitrary
  UID (`1000770000:0`), proving the hardened images run as any user in group 0, the way OpenShift
  starts them; firewall rules that drop and count any connection leaving the stack's networks;
  the assertions themselves (`pytest` under `tests/`, the database checks in leg 9).
- **Accounts the tests need.** A second person, agents made with `atlas-admin bot add` and
  `token add --out -`, and an admin-scoped token for the RBAC tests (legs 3 to 5). An install
  makes agent tokens with `token mint`, as in section 7.
- **Test-only services.** Leg 5b also renders a restricted allow-list and a wrong CA, to prove the
  gateway enforces them, and ends on the shipped configuration. Leg 8b points retrieval at a stub
  embeddings endpoint (`tests/retrieval/`), never a real provider; leg 8c does the same in Entra
  mode, with the stub also standing in for the Entra token endpoint. Leg 9 runs a throwaway OpenID
  provider with its own CA (`tests/sso/docker-compose.idp.yml`) on a network shared with
  `atlas-api`. A real IdP is outside the VM, so leg 9b moves the same IdP onto the `egress` network
  only and shows sign-in failing without `oidc.yml` and working with it.
