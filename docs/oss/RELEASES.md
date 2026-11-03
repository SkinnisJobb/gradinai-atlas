---
audience: operations-security
---

# Releases

This page is how Atlas is versioned and released, what a release publishes, how to run an install
from the published images, and the notes for each release, newest first. The upgrade procedure
itself is [INSTALL section 10](INSTALL.md#10-upgrading); [Operations section 3](guides/operations.md#3-upgrade)
explains its parts.

## Versioning

A release is a git tag `vMAJOR.MINOR.PATCH` ([Semantic Versioning](https://semver.org/)) on a commit
of the default branch. The first release is `v0.0.1-alpha`, a pre-release: the first public
version is an alpha.

- **PATCH** (`v0.1.1`): fixes and security patches, typically new base image digests. No new
  setting is required and no one-time step is needed: back up, check out, rebuild or pull, done.
- **MINOR** (`v0.2.0`): new features. It may add settings (with defaults that keep today's
  behaviour) and, while the major version is 0, it may change a default or need a one-time step.
  Its notes below say which.
- **MAJOR** (`v1.0.0`): from 1.0 on, a change that breaks an existing install (a removed setting,
  a changed default, a data change the schema step cannot make) comes only with a new major
  version.
- **Pre-releases** (`v0.2.0-rc.1`): published the same way and marked as a pre-release. Do not
  run one in production.

A tag names one commit, and that commit is the release: its checkout, its images and its notes
belong together. A tag is never moved or reused. A broken release is fixed by the next PATCH.

## What a release publishes

Pushing a `v*` tag runs the `images` workflow (`.github/workflows/images.yml`). It builds every
image the OSS install builds, from the same recipe, checks them and scans them with the same
vulnerability gate as every pull request (a fixable critical or high finding stops the release),
and only then publishes them to the GitHub container registry, each tagged with the release:

| Image | Built from | Used by (`deploy/oss/docker-compose.yml`) |
|---|---|---|
| `ghcr.io/<owner>/gradinai-atlas-api` | `backend/Dockerfile`, target `hardened` | `atlas-api`, `atlas-migrate`, `atlas-retrieval` |
| `ghcr.io/<owner>/gradinai-atlas-mcp` | `mcp/Dockerfile`, target `hardened` | `atlas-mcp` |
| `ghcr.io/<owner>/gradinai-atlas-backup` | `backend/Dockerfile`, target `backup` (`hardened` plus `pg_dump` 16) | `atlas-backup` (profile `backup`) |
| `ghcr.io/<owner>/gradinai-atlas-postgres` | `deploy/postgres/pgvector.Dockerfile` (pgvector on PostgreSQL 16, `gosu` replaced) | `atlas-postgres` |
| `ghcr.io/<owner>/gradinai-atlas-tls` | `deploy/caddy/Dockerfile` (Caddy with the Cloudflare DNS plugin) | `atlas-tls` |

**Not republished:** the gateway profile's `atlas-gateway` runs the upstream AgentGateway image,
which `docker-compose.yml` already pins by tag and `@sha256` digest. Pulling it by digest gives
every install the same bytes, so a copy under the Atlas name would add nothing. The upstream bases
of the five images above (Python, pgvector, Caddy) are pinned the same way in their Dockerfiles.

The workflow then writes a **draft GitHub release** for the tag: the tag's section of this page as
its notes, the five image references (tag and digest), and the file `release-images.env` with the
five lines an install pastes into its `.env`. A maintainer publishes the draft, and publishing it
attaches the SBOMs (`sbom-sca.yml`).

The images are built for `linux/amd64`.

## Two ways to run a release

Either way, the checkout stays at the release tag: the compose file, the Caddyfile, the scripts
and the retrieval check are read from it.

1. **Build from the checkout** (the default, what [INSTALL](INSTALL.md) describes). `git checkout
   <tag>`, then `docker compose up -d --build`. Every base image is pinned by digest, so rebuilding
   the same tag gives the same bases.
2. **Pull the released images** with the overlay `deploy/oss/release.yml`. It replaces each
   service's `build:` with the published image, pinned by tag and digest, and changes nothing else:
   networks, secrets, volumes, healthchecks and commands are the base file's. Nothing is built on
   the VM. It needs Docker Compose 2.24.4 or later (`!reset`).

## Upgrading with released images

The steps of [INSTALL section 10](INSTALL.md#10-upgrading), with the images pulled instead of
built. From `deploy/oss/`:

1. **Back up** ([Operations section 1](guides/operations.md#1-backup)).
2. **Check out the release** from the repository root: `git fetch --tags && git checkout <tag>`,
   then return to `deploy/oss/`.
3. **Compare `.env` with the new `.env.example`** and read the release's notes below for new
   settings and one-time steps: `diff .env.example .env`.
4. **Replace the five `ATLAS_IMAGE_*` lines** in `.env` with those from the release's
   `release-images.env` (attached to the GitHub release, and printed in its notes). Replace all
   five together: they are one release.
5. **Pull and restart.** Add `release.yml` once to `COMPOSE_FILE` in `.env`, before any other
   overlay (`COMPOSE_FILE=docker-compose.yml:release.yml:retrieval.yml`), or pass it with `-f` every
   time:

   ```sh
   docker compose -f docker-compose.yml -f release.yml pull
   docker compose -f docker-compose.yml -f release.yml up -d --wait --wait-timeout 600
   ```

   Do not add `--build`: with the overlay there is nothing to build. A missing `ATLAS_IMAGE_*`
   line stops compose with that variable's name instead of building something else.
6. **Run the schema step once more by hand**, check the door, and run the collation check when the
   notes say the Postgres image changed, as in INSTALL section 10.

To go back to building from the checkout, drop `release.yml` from `COMPOSE_FILE` (or the `-f`) and
run `docker compose up -d --build`. The `ATLAS_IMAGE_*` lines are then read by nothing.

**Rolling back** a release is restoring the backup taken in step 1 with the previous tag's checkout
and images: the schema step only adds, so a newer schema under older code is not a tested state.

**Tested:** the install smoke's `upgrade` job, on every pull request that runs the smoke. It
installs the previous release from its published images (that release's `deploy/oss/`,
`release.yml` and `release-images.env`, every service checked to run a released reference), adds
people, an agent token and data, takes a backup with `atlas-admin backup run`, then brings up the
new checkout built from source on the same `.env`, `secrets/` and volumes, with `release.yml`
dropped from `COMPOSE_FILE`. It checks the schema step, every table's row count against the
count before the backup, a dry-run restore of the old backup with the new backup image, the
collation check, and that the old passwords, agent token and data still work. With no previous
release the job is skipped. *Untested:* upgrading from one released image set to the next (step 4
with a newer `release-images.env`) and rolling back; rehearse those on a copy first.

## Cutting a release

For maintainers of the repository the install is made from. Nothing creates a tag automatically.

1. **Write the notes.** Add a `## vX.Y.Z` section at the top of [Release notes](#release-notes)
   below, in the same pull request as the last change or in its own: what changed for an install,
   new settings, one-time steps, and whether the Postgres image changed. The workflow refuses a tag
   with no section here.
2. **Merge it**, and wait for the default branch to be green.
3. **Tag the merged commit and push the tag:**

   ```sh
   git fetch origin
   git tag -a vX.Y.Z -m "Atlas vX.Y.Z" origin/master
   git push origin vX.Y.Z
   ```

4. **Watch the `images` run** for the tag. `publish` checks the tag (semver, notes present), pushes
   the five images and prints their references; `release` writes the draft release. If a job fails
   for a reason outside the tag (a registry hiccup), run it again on the same tag:
   `gh run rerun <run-id> --failed`, or `gh workflow run images.yml --ref vX.Y.Z`. If the images
   themselves are wrong, do not move the tag: fix it and release the next PATCH.
5. **Publish the draft** when its notes read right: `gh release edit vX.Y.Z --draft=false`. That
   attaches the SBOMs.
6. **First release only:** the registry creates each package private. Make each of the five public
   in the package's settings if installs should pull without signing in, and check that an
   anonymous `docker pull` of one reference from `release-images.env` works.

## Release notes

Newest first. Each section is what the GitHub release shows for that tag.

## v0.0.1-alpha

The first public release, an alpha: the first tagged version of this repository. It is a
pre-release, so do not run it in production yet.

- **What is in it:** the single-node install described in [INSTALL](INSTALL.md): Postgres with
  pgvector, the schema one-shot, the API and board, the MCP server, the TLS ingress with the
  install CA (or your PKI, or ACME), and the optional gateway, retrieval, single sign-on and backup
  profiles and overlays. A fresh install seeds Atlas's own system nodes and its world model
  (`ATLAS_SEED=none`, the default).
- **Released images** for every service the install builds, and the overlay `release.yml` with the
  `ATLAS_IMAGE_*` settings in `.env.example` to run them pinned by digest instead of building (this
  page).
- **Settings:** `ATLAS_IMAGE_API`, `ATLAS_IMAGE_MCP`, `ATLAS_IMAGE_BACKUP`,
  `ATLAS_IMAGE_POSTGRES`, `ATLAS_IMAGE_TLS`, read only with `release.yml`.
- **One-time steps:** none; this is the first release.
- **Postgres image:** pgvector 0.8.7 on PostgreSQL 16, Debian 13.
