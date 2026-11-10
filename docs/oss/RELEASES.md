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
| `ghcr.io/<owner>/gradinai-atlas-postgres` | `deploy/postgres/pgvector.Dockerfile` (pgvector on PostgreSQL 16, `gosu` replaced, no `apt`) | `atlas-postgres` |
| `ghcr.io/<owner>/gradinai-atlas-tls` | `deploy/caddy/Dockerfile` (Caddy with the Cloudflare DNS plugin) | `atlas-tls` |

From `v0.0.2-alpha` on, a release also publishes the **OpenShift images**, on Red Hat's Universal
Base Image 9 (UBI). The Compose install does not use them; the OpenShift manifests do
([deploy/openshift/README.md](../../deploy/openshift/README.md#images-and-releases)):

| Image | Built from | Used by (`deploy/openshift`) |
|---|---|---|
| `ghcr.io/<owner>/gradinai-atlas-api-ubi` | `backend/Dockerfile`, target `hardened-ubi` (`ubi9/python-312-minimal`) | `atlas-api`, the migrate Job, the audit CronJob |
| `ghcr.io/<owner>/gradinai-atlas-mcp-ubi` | `mcp/Dockerfile`, target `hardened-ubi` (`ubi9/python-312-minimal`) | `atlas-mcp` |
| `ghcr.io/<owner>/gradinai-atlas-tls-ubi` | `deploy/caddy/Dockerfile.ubi` (stock Caddy built on `ubi9/go-toolset`, run on `ubi9/ubi-micro`; no plugin) | `atlas-tls` |

Each carries its licence terms in `/licenses` and its own `name`, `vendor`, `version` (the
release tag), `release` (the commit), `summary` and `description` labels. Besides the
vulnerability gate, these three go through Red Hat's `preflight check container` on every pull
request that touches them and on the tag: a failure of the checks that matter for running on
OpenShift (`RunAsNonRoot`, `BasedOnUbi`, `HasModifiedFiles`, `HasNoProhibitedPackages`,
`HasLicense`, `HasRequiredLabel`) stops the release, and the others are reported (the comment
on that step in `images.yml` says why). Preflight runs locally with no Red Hat account; nothing
is submitted for certification.

**Not republished:** the gateway profile's `atlas-gateway` runs the upstream AgentGateway image,
which `docker-compose.yml` already pins by tag and `@sha256` digest. Pulling it by digest gives
every install the same bytes, so a copy under the Atlas name would add nothing. The upstream bases
of the images above (Python, pgvector, Caddy, UBI) are pinned the same way in their Dockerfiles.

The workflow then writes a **draft GitHub release** for the tag: the tag's section of this page as
its notes, the image references (tag and digest), and the file `release-images.env`. Its first
five lines are the ones a Compose install pastes into its `.env`; the three after them,
`ATLAS_IMAGE_API_UBI`, `ATLAS_IMAGE_MCP_UBI` and `ATLAS_IMAGE_TLS_UBI`, are where an OpenShift
overlay takes its digests from, and Compose reads none of them. A maintainer publishes the draft,
and publishing it attaches the SBOMs (`sbom-sca.yml`), the three UBI images' included.

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
   five together: they are one release. The `ATLAS_IMAGE_*_UBI` lines are for OpenShift; pasting
   them too does no harm, nothing in Compose reads them.
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
collation check, and that the old passwords, agent token and data still work. The previous
release is the newest published release of this repository (pre-releases included, drafts
never), not the newest tag: its tree is fetched at that tag, its `release-images.env` read from
that release, and its public images pulled with no registry login. The repository variables
`UPGRADE_FROM_REPO` and `UPGRADE_FROM_TAG` change the repository (a fork pointing at the one it
follows) or pin a release. With no published release the job is skipped. *Untested:* upgrading from one
released image set to the next (step 4 with a newer `release-images.env`) and rolling back;
rehearse those on a copy first.

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

4. **Watch the `images` run** for the tag. `build-scan` builds, scans and preflights the images;
   `publish` checks the tag (semver, notes present), pushes the eight images and prints their
   references; `release` writes the draft release. If a job fails
   for a reason outside the tag (a registry hiccup), run it again on the same tag:
   `gh run rerun <run-id> --failed`, or `gh workflow run images.yml --ref vX.Y.Z`. If the images
   themselves are wrong, do not move the tag: fix it and release the next PATCH.
5. **Publish the draft** when its notes read right: `gh release edit vX.Y.Z --draft=false`. That
   attaches the SBOMs.
6. **A release that publishes a new package** (the first release; `v0.0.2-alpha`, which adds the
   three `-ubi` packages): the registry creates each new package private. Make it public in the
   package's settings if installs should pull without signing in, and check that an anonymous
   `docker pull` of its reference from `release-images.env` works.

## Release notes

Newest first. Each section is what the GitHub release shows for that tag.

## v0.0.3-alpha

A pre-release, the third alpha. For a Compose install it is a patch: no new setting, no schema
change and no one-time step. It brings the board's graph view and top bar up to date, a round of
review fixes, security patches for the TLS images, and the next steps of the OpenShift install.

- **Board, graph view:** a focused node shows its own branch and fades the way back up to the
  root; the Steps control runs from 1 to the graph's depth; nodes can be collapsed and views saved
  in a foldable Views box; hovering a node lights its path to the root. A click on a node now
  survives a small mouse movement instead of being read as a drag.
- **Board, top bar:** a user menu, the pane bars replace the pane buttons, and the Access and
  Crunch settings share one set of tabs.
- **API:** an intent whose `title` or `source` is longer than 200 characters is refused with
  `400` naming the field, instead of failing with `500`.
- **Frontend:** four orphaned pages are gone (`/login.html`, `/logout.html`,
  `/change-password.html`, `/index.html`); nothing in the board linked to them. Sign-in is
  unchanged.
- **MCP:** clearer descriptions for `atlas_file_work` and `atlas_health`, and the work hierarchy
  is described as Epic > Story > Task throughout.
- **Self seed:** the built-in profile of Atlas's own world model (`backend/self_seed_data.py`) is
  re-exported. It is only loaded into an empty database, so an existing install keeps its own
  nodes; a new install gets the new profile.
- **Security patches:** both Caddy builds (`gradinai-atlas-tls` and `gradinai-atlas-tls-ubi`)
  build with `golang.org/x/net` 0.62.0, which fixes CVE-2026-78661 and CVE-2026-78669. The
  OpenShift (UBI) images carry no fixable high finding. Caddy 2.11.9, newer UBI 9 base digests,
  and patch updates of pydantic, pydantic-core, SQLAlchemy, anyio, typing-inspection and
  python-multipart.
- **OpenShift install:** the base and the example overlay now use the `-ubi` images of
  `v0.0.2-alpha` by tag and digest, and the init container that rewrote the database URL is gone
  (`v0.0.2-alpha` reads CloudNativePG's URL itself). The database can be installed on its own from
  one plain YAML file (`deploy/openshift/database/atlas-db.yaml`), and
  `deploy/openshift/FIRST-INSTALL.md` is rewritten as a strict step-by-step guide with an
  expected output and an "If not" fix for each step. The migrate Job retries more times, so a
  first install no longer fails while the database is still starting. Moving the overlay to this
  release's images is the next step.
- **Install smoke:** the upgrade test now upgrades from the newest published release of this
  repository, pulled anonymously, to the checkout.
- **Settings:** none new.
- **One-time steps:** none.
- **Postgres image:** unchanged.

## v0.0.2-alpha

A pre-release, the second alpha. For a Compose install it is a patch: no new required setting
and no one-time step. Most of the work is the groundwork for running Atlas on OpenShift (Azure
Red Hat OpenShift), which is not finished yet.

- **OpenShift images:** `gradinai-atlas-api-ubi`, `gradinai-atlas-mcp-ubi` and
  `gradinai-atlas-tls-ubi`, on Red Hat UBI 9, checked with Red Hat's preflight, published with
  this release ([What a release publishes](#what-a-release-publishes)). `release-images.env` has
  three more lines for them, `ATLAS_IMAGE_*_UBI`, which Compose does not read.
- **OpenShift manifests, in progress:** `deploy/openshift/` (Kustomize): the base (atlas-api, the
  migrate Job, atlas-mcp, atlas-tls with the re-encrypt Route, the audit purge CronJob), the
  CloudNativePG database as components, and an example overlay with a step-by-step first install
  (`deploy/openshift/FIRST-INSTALL.md`). Its overlay still names this release's Debian-based
  images; moving it to the `-ubi` ones is the next step
  ([deploy/openshift/README.md](../../deploy/openshift/README.md#images-and-releases)).
- **OpenShift NetworkPolicies, opt-in:** `deploy/openshift/components/networkpolicies/`, default
  deny for every Atlas pod, then one allow per edge Atlas uses (atlas-mcp has no path to the
  database or to atlas-api). It works next to a project template's own policies and lets the
  CloudNativePG operator reach the database, which those templates block. The example overlay
  lists it commented out, and `deploy/openshift/FIRST-INSTALL.md` has a troubleshooting section
  for a cluster that denies traffic by default
  ([deploy/openshift/README.md](../../deploy/openshift/README.md#networkpolicies-task-34)). The
  router is admitted by its namespace label's key, whatever value the cluster gives it.
- **OpenShift first install, more clusters:** `deploy/openshift/FIRST-INSTALL.md` shows how to
  copy the images to a registry the cluster may pull from, by digest, and checks the
  CloudNativePG version and the project's quota first. The example overlay has optional,
  commented-out patches for a pull secret, for an older CloudNativePG operator, and for
  labels an organisation requires.
- **Startup:** the schema step and the boot seeding hold a PostgreSQL advisory lock, so several
  replicas, or a replica and `atlas-migrate`, starting together no longer race. A single process
  never waits on it. A `postgresql://` database URL with no driver now means psycopg2.
- **New optional setting:** `ATLAS_MCP_STATELESS_HTTP=1` makes the MCP transport stateless, for
  more than one `atlas-mcp` replica behind a load balancer. Unset, nothing changes.
- **Security patches:** CVE-2026-109518 (apt): newer `python:3.12-slim` and `postgres:16-trixie`
  digests, and `apt` removed from the pgvector image.
- **Settings:** `ATLAS_MCP_STATELESS_HTTP` (optional, off).
- **One-time steps:** none.
- **Postgres image:** changed: the same pgvector 0.8.7 on PostgreSQL 16, Debian 13, on a newer
  base digest and without `apt`. Run the collation check after the upgrade, as
  [INSTALL section 10](INSTALL.md#10-upgrading) says.

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
