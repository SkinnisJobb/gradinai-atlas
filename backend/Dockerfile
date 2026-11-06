# THREE STAGES, ONE RECIPE (STORY-oss-18). `base` is the whole image as it always was; `hardened`
# adds the OSS install's runtime hardening on top of it; the LAST stage (unnamed) is `base`
# unchanged, so a build without `--target` (the homelab, tools/deploy.sh, tools/stage.sh) still
# produces exactly today's image: root, no HEALTHCHECK, same layers. `--target hardened` (the
# OSS compose file, .github/workflows/images.yml) is the only way to get the hardened one. The
# hardened stage has no COPY of its own, so verify_deploy_status.py's COPY-to-spec map and
# deploy_status.py's fingerprint of what the image carries are the same for both.
# A fourth stage, `backup` (STORY-atlas-crunch-mode), is `hardened` plus the Postgres client, built
# only by the OSS compose file's opt-in `backup` profile; it has no COPY either.
# A fifth, `hardened-ubi` (TASK-1476), is `hardened` on Red Hat UBI 9 for OpenShift; it copies /app
# from `base` (`COPY --from`), so it adds no source COPY either; its one other COPY puts LICENSE
# and NOTICE in /licenses (TASK-1483), outside /app.

# Pinned tag@digest (TASK-1365); digest bumped in LCM patch round 1 (2026-10-25): python 3.12.15, trixie.
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS base

WORKDIR /app
# The hashed lock (STORY-atlas-lcm-plan item 3), not requirements.txt: every package, the
# transitive ones too, at an exact version and checked against its sha256. The direct pins are
# requirements.txt's, unchanged; tools/ci/lock_requirements.sh writes the lock and
# tools/ci/check_locks.py keeps the two in step.
COPY backend/requirements-lock.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements-lock.txt

# Glob all backend modules (not a hand-maintained list — a new module must not
# be silently left out of the image).
#
# TASK-1059: `world_manifest.yaml` and the ADR 0030 migration spec
# (`world_v2.yaml` + the two bind maps) used to be copied here. They are
# deleted — the world's shape has been Postgres-authoritative since ADR 0030,
# and both were one-time inputs whose job is finished.
COPY backend/*.py ./
# The route modules (STORY-oss-04). A subpackage, so the `*.py` glob above does
# not reach it; without this line the image has a main.py whose imports fail.
COPY backend/routers ./routers
# The identity-provider seam (STORY-oss-13): a subpackage too, imported by auth.py.
COPY backend/identity ./identity
# The opt-in retrieval add-on (ADR 0044): a subpackage, and main.py imports its switch at every
# boot, so the image must carry it even where the add-on is off.
COPY backend/retrieval ./retrieval
COPY backend/plans ./plans
# The bundled breach corpus (`passwords.BREACH_CORPUS_PATH`). NOT covered by the
# `backend/*.py` glob above, and its absence is silent: `_load_corpus` caches an
# EMPTY frozenset, `_check_bundled` then returns `breached=False`, and the
# password is ADMITTED. All three stacks set `ATLAS_BREACH_CHECK: bundled`, so
# that is the live path, not a fallback. `verify_object_graph.py` §21 pins this
# line by parsing this file, so removing it turns the suite red (TASK-1127).
COPY backend/data ./data
COPY frontend ./frontend
# Copied so deploy_status.py can fingerprint the build recipe too: a changed Dockerfile is a
# reason to rebuild even when no source file changed.
COPY backend/Dockerfile ./Dockerfile.build
# `docs/nodes` is NOT copied any more (TASK-1059). Node documentation lives in
# the `node_doc` table (ADR 0029) and `/api/node/{id}/doc` reads the row, never
# the filesystem — there has been no `DOCS_ROOT` since that route was repointed.
# Authoring goes through PUT /api/node/{id}/doc (TASK-1076).

# `atlas-migrate upgrade` (STORY-oss-09): runs startup's schema steps and exits. A wrapper on
# PATH, not a COPY, so deploy_status.py's spec of copied files is unchanged; the module it calls
# is already in the image through the `backend/*.py` glob. The CMD below is not affected.
RUN printf '#!/bin/sh\nexec python /app/migrate_cli.py "$@"\n' > /usr/local/bin/atlas-migrate \
 && chmod 0755 /usr/local/bin/atlas-migrate
# `atlas-admin` (STORY-oss-07): the account CLI (`admin.py`) under the name an install documents,
# e.g. `atlas-admin user add <name> --role owner` for the first owner. Same wrapper shape.
RUN printf '#!/bin/sh\nexec python /app/admin.py "$@"\n' > /usr/local/bin/atlas-admin \
 && chmod 0755 /usr/local/bin/atlas-admin

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]


# --- hardened (STORY-oss-18): `docker build --target hardened` ---------------------------------
# Non-root and UID-agnostic, for any runtime that assigns an arbitrary UID (OpenShift's
# restricted SCC runs the container as e.g. 1000770000 with group 0):
#   - every file the image carries is group 0 with the owner's permissions minus write: any UID
#     in group 0 reads and executes what root could, and writes none of it;
#   - the only writable places are /tmp, HOME (/home/atlas: group 0, g=u, so writable by any
#     UID in group 0) and whatever volumes the deployment mounts. The API writes no file at run
#     time (state is Postgres; secrets are read-only mounts);
#   - USER is numeric (10001:0), so `runAsNonRoot` can verify it without an /etc/passwd entry.
FROM base AS hardened
ENV HOME=/home/atlas \
    PYTHONDONTWRITEBYTECODE=1
RUN mkdir -p /home/atlas \
 && chgrp -R 0 /app /home/atlas /usr/local/bin/atlas-migrate /usr/local/bin/atlas-admin \
 && chmod -R g=u,g-w,o-w /app \
 && chmod 0775 /home/atlas
USER 10001:0
# Liveness from what the image has (python; there is no curl here). /healthz needs no database
# and no token, but the gate answers 421 for a Host it does not serve: the Host is
# ATLAS_PUBLIC_HOST, else ATLAS_PUBLIC_URL's, else the first ATLAS_LOGIN_ORIGINS entry's.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.parse as p, urllib.request as u; o = os.environ.get('ATLAS_PUBLIC_URL') or (os.environ.get('ATLAS_LOGIN_ORIGINS') or 'http://localhost').split(',')[0].strip(); h = os.environ.get('ATLAS_PUBLIC_HOST') or p.urlsplit(o).netloc; u.urlopen(u.Request('http://127.0.0.1:8000/healthz', headers={'Host': h}), timeout=4)"]


# --- backup (STORY-atlas-crunch-mode): `docker build --target backup` ---------------------------
# `hardened` plus the Postgres client (pg_dump, pg_restore) that crunch_backup.py drives. Only
# the OSS compose file's opt-in `backup` profile builds it (service atlas-backup); atlas-api,
# atlas-migrate and the homelab's default image carry no Postgres client, so neither changes.
# THE CLIENT IS THE SERVER'S MAJOR, 16 (OSS-26 C). Debian trixie's postgresql-client is 17, and
# pg_restore 17 writes `SET transaction_timeout = 0` into every restore, which a 16 server
# refuses ("unrecognized configuration parameter"): with --exit-on-error no 17-made backup
# restored into atlas-postgres, so every `backup run` on the OSS stack failed its own
# verification (found by install smoke leg 8d, the first CI run of it there). pg_dump and
# pg_restore are therefore copied from the SAME pinned image atlas-postgres is built from
# (deploy/postgres/pgvector.Dockerfile), with their shared libraries from Debian (libpq5 is
# ABI-stable across majors). The image is a named stage, `pgclient`, on a FROM line, and not a
# `COPY --from=<image>`: Dependabot's docker ecosystem (which watches /backend and
# /deploy/postgres, one grouped PR) updates FROM lines and does not parse COPY --from. Keep the
# two references equal; verify_crunch_backup.py §A fails a change that moves only one.
# /var/lib/atlas/backups exists in the image, group 0 and group-writable, so a named volume
# mounted there starts with that ownership and any UID in group 0 can write it.
FROM pgvector/pgvector:0.8.7-pg16-trixie@sha256:9cf8b511f0e9a2c209b61430fc139f1ab9c75edbb31fb1751db6617b246a897a AS pgclient

FROM hardened AS backup
USER 0
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 liblz4-1 libzstd1 zlib1g \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /var/lib/atlas/backups \
 && chgrp 0 /var/lib/atlas/backups \
 && chmod 0770 /var/lib/atlas/backups
COPY --from=pgclient /usr/lib/postgresql/16/bin/pg_dump /usr/lib/postgresql/16/bin/pg_restore /usr/local/bin/
RUN pg_dump --version | grep -q ' 16\.' && pg_restore --version | grep -q ' 16\.'
USER 10001:0


# --- hardened-ubi (TASK-1476, ARO 1.1): `docker build --target hardened-ubi` --------------------
# `hardened` on Red Hat's Universal Base Image, for OpenShift (docs/oss/research/openshift-aro.md,
# question 1). Opt-in: nothing builds it but images.yml, and the stages above and the default
# below are untouched. The same code and the same hashed lock as `hardened`:
#   - /app comes from `base` with `COPY --from`, so this stage copies no source of its own and
#     verify_deploy_status.py's COPY-to-spec map and deploy_status.py's fingerprint are the same
#     for every target; the two CLI wrappers come from `base` too;
#   - the lock is installed into the image's own venv (/opt/app-root, first on PATH) with
#     --require-hashes. The lock carries every platform's wheel hash, and every compiled
#     dependency has a manylinux wheel that UBI 9's glibc 2.34 satisfies, so nothing compiles;
#   - the same runtime contract as `hardened`: every file group 0 and not group-writable (the
#     venv too, which the base image ships group-writable), HOME=/home/atlas the one writable
#     directory besides /tmp, numeric USER, the same HEALTHCHECK. The base image's own user
#     (1001) is not used: OpenShift assigns its UID, and 10001:0 keeps the two hardened images alike.
# Pinned tag@digest like every FROM (tools/ci/check_pins.py): `9.8` is the RHEL minor, which
# Dependabot follows (the docker ecosystem watches /backend), refreshing the digest as Red Hat
# rebuilds the image for each erratum.
FROM registry.access.redhat.com/ubi9/python-312-minimal:9.8@sha256:d0ac54e77cd0be473b22fb274493526027f2746ec503b4a97ca08257813f0d4b AS hardened-ubi
USER 0
WORKDIR /app
COPY --from=base /app /app
COPY --from=base /usr/local/bin/atlas-migrate /usr/local/bin/atlas-admin /usr/local/bin/
RUN pip install --no-cache-dir --require-hashes -r requirements-lock.txt \
 && mkdir -p /home/atlas \
 && chgrp -R 0 /app /home/atlas /usr/local/bin/atlas-migrate /usr/local/bin/atlas-admin \
 && chmod -R g=u,g-w,o-w /app /opt/app-root \
 && chmod 0775 /home/atlas
# Red Hat's container certification policy (`preflight check container`, TASK-1483): the image's
# licence terms under /licenses, and its own name, vendor, version, release, summary and
# description labels instead of the ones inherited from the base. Not under /app, so neither
# deploy_status.py's fingerprint nor verify_deploy_status.py's COPY map is concerned. The ARGs
# come after the last RUN, so a new version re-labels the image without rebuilding a layer;
# images.yml passes the release tag and commit on publish, a local build says `dev`.
COPY LICENSE NOTICE /licenses/
ENV HOME=/home/atlas \
    PYTHONDONTWRITEBYTECODE=1
USER 10001:0
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.parse as p, urllib.request as u; o = os.environ.get('ATLAS_PUBLIC_URL') or (os.environ.get('ATLAS_LOGIN_ORIGINS') or 'http://localhost').split(',')[0].strip(); h = os.environ.get('ATLAS_PUBLIC_HOST') or p.urlsplit(o).netloc; u.urlopen(u.Request('http://127.0.0.1:8000/healthz', headers={'Host': h}), timeout=4)"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
ARG ATLAS_VERSION=dev
ARG ATLAS_RELEASE=dev
LABEL name="gradinai-atlas-api-ubi" \
      vendor="gradinai-atlas" \
      maintainer="gradinai-atlas" \
      version="${ATLAS_VERSION}" \
      release="${ATLAS_RELEASE}" \
      summary="Atlas API and board (atlas-api) on Red Hat UBI 9" \
      description="The Atlas API, its board and the atlas-migrate and atlas-admin commands, non-root under any UID in group 0, for OpenShift." \
      io.k8s.display-name="Atlas API (atlas-api)" \
      io.k8s.description="The Atlas API, its board and the atlas-migrate and atlas-admin commands, non-root under any UID in group 0, for OpenShift." \
      org.opencontainers.image.licenses="Apache-2.0"


# --- the default (no --target): today's image, unchanged ---------------------------------------
# MUST stay the last stage, and empty: an instruction here changes the homelab's image.
FROM base
