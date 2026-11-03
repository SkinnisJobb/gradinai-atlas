---
audience: operations-security
---

# Server requirements

What a host needs before you [install](../INSTALL.md) Atlas: memory and CPU, disk, and network.
The figures come from a measured run, not an estimate. The recommendations built on them are
labelled as recommendations, and this page says how each one was derived.

## Where the figures come from

Every install smoke run (`.github/workflows/install-smoke.yml`) installs the stack from
`deploy/oss/.env.example` on a clean CI runner and drives it through each documented feature, one
leg per step. A background sampler reads each container's cgroup about every 0.5 s for the whole
run, and `tools/ci/install_smoke_sizing.py` reduces the samples to a peak memory and a peak CPU per
service, overall and per leg.

The figures on this page are from **install-smoke run 40480995551, on `master`, 2026-10-27**: a green
scheduled run, 943 samples over 13 legs.

Two columns appear below:

- **Peak memory (sampled)** is memory in use minus inactive file cache, the way `docker stats`
  reports it, at the highest sample.
- **Peak memory (cgroup)** is the kernel's own high-water mark (`memory.peak`) for the container's
  whole life, page cache included. It is read once, just before teardown. It is missing for a
  one-shot container that has already exited. For `atlas-api` it covers only the last container,
  because the smoke recreates `atlas-api` for the retrieval and sign-in legs.

CPU is a percentage of one core between two consecutive samples, so 100% means one core fully busy
and 144% means about one and a half cores.

### What CI is not

A CI run is not a production host, and these figures are a floor, not a forecast:

- **The data is tiny.** The smoke starts from an empty database and adds two people, their
  tokens and a few work items. A real graph with months of history keeps more of Postgres in
  memory and makes every index pass and search do more work.
- **The load is one caller at a time.** Each leg is a test script, not a team. Concurrent people
  and agents raise `atlas-api` and `atlas-postgres` CPU at the same moment, where in the smoke
  their peaks mostly fall in different legs.
- **The run is short.** The legs take about two and a half minutes. Slow growth, such as a
  connection pool filling up or a cache warming, does not show.
- **Image builds are not measured.** The sampler starts after the runner is prepared. Building the
  images on the host (`docker compose up --build`) needs memory and disk of its own (see
  [Disk](#disk)).

## Base install

The base install is `deploy/oss/docker-compose.yml` alone: keyword search, no retrieval add-on, no
gateway. These figures are from the legs before the retrieval leg, `1-install` to `8-client-pack`.
Leg 1 of the smoke fails if the retrieval service would start by default, so these legs never run
an embedding service.

| Service | Role | Peak memory (sampled) | Peak memory (cgroup) | Peak CPU (one core = 100%) |
|---|---|---:|---:|---:|
| `atlas-api` | the board and the REST API | 172.7 MiB | 144.4 MiB¹ | 144% |
| `atlas-postgres` | Postgres 16 with pgvector | 52.9 MiB | 101.7 MiB | 35%² |
| `atlas-mcp` | the MCP server | 67.3 MiB | 68.8 MiB | 32% |
| `atlas-tls` | the TLS ingress (Caddy) | 24.2 MiB | 85.3 MiB | 10% |
| `atlas-migrate` | one-shot schema upgrade, then exits | 64.9 MiB | n/a (exited) | 98% |

¹ The last `atlas-api` container only (see above). The sampled column covers every leg.
² The overall peak, reached in the sign-in leg. In the base legs `atlas-postgres` peaked at 22%.

`atlas-api` peaked at 172.7 MiB during leg `2-migrate`, when the smoke runs `atlas-migrate upgrade`
twice more inside it. Its steady level across the other base legs was 70 to 104 MiB. Its CPU
peak, 144%, came in leg `4-mcp-contract`, the MCP contract tests.

`atlas-migrate` runs before `atlas-api` starts and then exits, so its memory is never held at the
same time as the API's peak.

### The optional gateway

The `gateway` profile adds `atlas-gateway` (AgentGateway in front of `atlas-mcp`). It was started in
leg `5b-gateway` and stayed up to the end of the run.

| Service | Peak memory (sampled) | Peak memory (cgroup) | Peak CPU |
|---|---:|---:|---:|
| `atlas-gateway` | 20.6 MiB | 16.4 MiB | 5% |

### Not part of Atlas

The run also measured two test-only containers. Neither is part of an install, and neither is
counted in any total on this page:

- `atlas-idp` (261.2 MiB, 228% CPU) is a throwaway OpenID provider for the sign-in leg. With
  single sign-on, your identity provider runs somewhere else and costs this host nothing.
- `atlas-embed-stub` (14.7 MiB, 6% CPU) is a stub embedding endpoint for the retrieval leg. It
  stands in for the real embedding service (see below).

## Retrieval add-on

The retrieval add-on (semantic search) runs **inside `atlas-api`**. The overlay
`deploy/oss/retrieval.yml` gives `atlas-api` a route out to an embedding endpoint and a key. No model
and no embedding service ships with Atlas. In the smoke, leg `8b-retrieval` recreates `atlas-api`
with the overlay and points it at the stub. It indexes the graph, stops the stub to check that
search degrades to keyword search, and then puts `atlas-api` back on the base file. The table below
compares that leg with the base legs before it, so the difference is the add-on.

| Service | Base legs, peak memory / CPU | Leg `8b-retrieval`, peak memory / CPU |
|---|---:|---:|
| `atlas-api` | 172.7 MiB / 144% (steady 70-104 MiB) | 81.2 MiB / 103% |
| `atlas-postgres` | 52.9 MiB / 22% | 52.7 MiB / 15% |
| `atlas-mcp` | 67.3 MiB / 32% | 54.1 MiB / 10% |
| `atlas-tls` | 23.7 MiB / 10% | 24.2 MiB / 3% |

**On the Atlas host, the add-on's memory cost was too small for this run to measure.** `atlas-api`
with retrieval on stayed inside the base legs' range, and so did `atlas-postgres`. Indexing used
about one core while it ran. The run does not show how the add-on scales, because the graph was
almost empty. What it does show is where the real cost of retrieval is. It is not in Atlas.
It is in two other places:

1. **The embedding endpoint.** If it is a hosted service, it costs this host nothing but network.
   If it is a **local embedding model, it needs its own memory and usually a GPU**, sized from that
   model's documentation, not from this page. Run it on a separate node, reached over TLS, as
   `retrieval.yml` describes. If you put it on the same machine, add its full requirement on top of
   the recommendation below. CI never runs a real model, so no figure here covers one.
2. **The database.** Each indexed chunk stores one vector. See [Disk](#disk).

Indexing CPU grows with the amount of text, and it comes in bursts: one pass every
`ATLAS_RETRIEVAL_INTERVAL_S` seconds (60 by default), sending `ATLAS_EMBED_BATCH` texts per call.
The first full index of a large existing graph is the heaviest pass. Later passes embed only what
changed.

## Recommended VM

| | Base (with or without the gateway) | Base + retrieval, hosted or remote embedding endpoint |
|---|---|---|
| vCPU | 2 | 2 |
| Memory | 4 GiB (2 GiB minimum) | 4 GiB |
| Disk | 20 GiB for system, images and data, plus backup space | the same, plus the vector storage below |

Add a local embedding model's own requirement on top of this, or give it its own node.

**How the headroom was chosen.** The rule is: memory = (sum of the per-service peaks) x 2 + 1 GiB
for the operating system and the Docker engine, then rounded up to a common VM size.

- The sum takes each long-running service at the higher of its two peak columns: `atlas-api`
  172.7 + `atlas-postgres` 101.7 + `atlas-mcp` 68.8 + `atlas-tls` 85.3 + `atlas-gateway` 20.6 =
  **449.1 MiB**. That is an upper bound, because the peaks did not happen at the same moment.
  `atlas-migrate` is left out because it exits before the API starts.
- **x 2** covers what CI does not show: a real graph in Postgres's cache, several people and agents
  at once, and longer uptime. 449 MiB x 2 + 1 GiB is about 1.9 GiB, so **2 GiB is the minimum**.
  The **4 GiB recommendation** doubles that again for Postgres. Postgres serves reads from the
  operating system's page cache, and on a 2 GiB host the cache is the first thing to go as the
  database grows.
- **CPU:** `atlas-api` alone burst to 1.4 cores. In the busiest base leg (`4-mcp-contract`),
  the per-service peaks of `atlas-api`, `atlas-postgres`, `atlas-tls` and `atlas-mcp` add up to
  about 1.7 cores. **Two vCPU** keep a burst
  from stalling the board, the MCP server and the database at the same time. If many agents call
  MCP at once, add cores before you add memory.

These are starting points. Check them against your own host after a week of use: run
`docker stats --no-stream` for the services, and `SELECT pg_size_pretty(pg_database_size('atlas'))`
in `atlas-postgres` for the database.

## Disk

**Volumes.** The install keeps its state in three named volumes:

| Volume | What it holds | Growth |
|---|---|---|
| `atlas-pgdata` | the whole database | grows with the graph |
| `atlas-tls-data` | issued certificates, and Caddy's own CA in `internal` TLS mode | small, steady |
| `atlas-tls-config` | Caddy's runtime configuration | small, steady |

`deploy/oss/secrets/` on the host holds the database password, the peppers and the install CA. It
is a few kilobytes, but you cannot rebuild it: without it, existing passwords and tokens stop
working.

**Database growth.** The smoke's database is too small to give a growth rate, so measure your own.
Check `pg_database_size('atlas')` once a week for the first month, then size the volume for at
least a year at that rate with room to spare. Everything in Atlas is rows in Postgres. Nodes,
edges, documents and work items grow with use, and the event history only ever grows.

**Vector storage (retrieval add-on).** Each chunk stores one `vector(ATLAS_EMBED_DIM)`: 4 bytes per
dimension plus a small header, on top of the chunk's text. At 1536 dimensions that is about 6 KiB
per chunk. At 1024 dimensions it is about 4 KiB. Multiply by the number of chunks in your graph:
long documents make many chunks, and short nodes make one each. The retrieval tables can be
rebuilt from the graph, so a lost index costs a reindex, not data.

**Images.** Building on the host (`--build`) needs space for the build cache as well as the
images. The smoke did not measure image sizes. After installing, `docker system df` shows them.
Prune old images after an upgrade.

**Backups.** A database dump (`pg_dump` in custom format) is usually much smaller than the
`atlas-pgdata` volume. Leave space for at least two dumps at the size of the live database: the
dump being written and the last good one. Keep the backups you rely on off this host. A backup on
the same disk does not survive the disk failing. Back up `deploy/oss/secrets/` and `atlas-tls-data`
with the database: a restored database without the peppers cannot check a password or a token.
[Operations](operations.md) has the backup and restore procedure.

## Network

**Inbound.** The install publishes one port, and it is TLS:

| Port (default) | Service | Setting | When |
|---|---|---|---|
| 443 | `atlas-tls`: board, API and `/mcp`, HTTPS | `ATLAS_TLS_PORT`, `ATLAS_TLS_BIND` | always |
| 8444 | `atlas-gateway`: the gateway's MCP door, HTTPS | `ATLAS_GATEWAY_PORT`, `ATLAS_GATEWAY_BIND` | `gateway` profile only |

Nothing else is published. `atlas-api` (HTTPS on 8443), `atlas-mcp` (HTTPS on 8493) and
`atlas-postgres` sit on internal Docker networks with no published port and no route out, and
`tools/ci/check_tls.sh` fails the build if a plain-HTTP port is ever published. No port that
carries a token or a password is published on all interfaces (`0.0.0.0`) without TLS.

The two TLS doors themselves **are** bound to all interfaces by default (`ATLAS_TLS_BIND=0.0.0.0`
and `ATLAS_GATEWAY_BIND=0.0.0.0` in `.env.example`), because they are the doors people and agents
use. To narrow that, set the bind to one address: `127.0.0.1` behind a reverse proxy on the same
host, or the address of the interface your users reach. Put a firewall in front of the host in
any case, and leave the gateway's port closed unless you run the `gateway` profile.

**TLS.** Every hop that carries a token is HTTPS with the certificate verified, including
`atlas-mcp` to `atlas-api` and the gateway to `atlas-mcp` inside the host. `ATLAS_TLS_MODE` sets
where the public certificate comes from: your own PKI or the generated install CA (`files`),
Caddy's own CA (`internal`), or Let's Encrypt by DNS challenge (`acme-cloudflare`). Clients that do
not use a public CA must trust `deploy/oss/secrets/tls/ca.crt` or your PKI's root. The DNS name in
`ATLAS_PUBLIC_HOST` must resolve to this host for everyone who uses it.

**Outbound.** Once the stack is up, the base install opens no connection out of its own networks.
Leg 1b of the smoke drops and counts any such connection for the rest of the run, and the run fails
if one was attempted. You only need outbound access for:

- installing and upgrading: the container registries and package mirrors the image builds use, or
  your registry if you pull released images;
- `ATLAS_TLS_MODE=acme-cloudflare`: Let's Encrypt and the Cloudflare API;
- the retrieval add-on: the embedding endpoint in `ATLAS_EMBED_URL`, over HTTPS;
- single sign-on: your identity provider;
- an external or managed PostgreSQL (the overlay `external-db.yml`): the database server's port,
  from `atlas-migrate`, `atlas-api` and `atlas-backup`
  ([External or managed PostgreSQL](external-postgres.md)).

## Host software

- 64-bit Linux with Docker Engine and the Docker Compose v2 plugin (`docker compose`, v2.17.0 or
  later for `up --wait --wait-timeout`). The smoke runs on a cgroup v2 host.
- A VM or host of its own: not a WSL distro beside another Docker daemon, since WSL 2 distros
  share one network stack.
- Python 3 on the host only if you use the gateway profile (`scripts/render-gateway.py`).
- The images run as a non-root numeric UID, and under an arbitrary UID in group 0 as well, so a
  platform that assigns UIDs (OpenShift-style) works unchanged.
