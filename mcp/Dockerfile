# THREE STAGES, ONE RECIPE (STORY-oss-18): `base` is the image as it always was, `hardened`
# (`--target hardened`: the OSS install and .github/workflows/images.yml) adds a non-root,
# UID-agnostic runtime on top, and the LAST stage is `base` unchanged, so a build without
# `--target` (the homelab) produces today's image. See backend/Dockerfile.
# A fourth, `hardened-ubi` (TASK-1477), is `hardened` on Red Hat UBI 9 for OpenShift; it copies
# /app from `base` (`COPY --from`), so it adds no source COPY.

# Pinned tag@digest (TASK-1365); digest bumped in LCM patch round 1 (2026-10-25): python 3.12.15, trixie.
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# The hashed lock (STORY-atlas-lcm-plan item 3): see backend/Dockerfile and
# tools/ci/lock_requirements.sh.
COPY requirements-lock.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements-lock.txt

COPY app/ ./app/
# Fingerprinted by app/deploy_status.py alongside the source: see that module.
COPY Dockerfile ./Dockerfile.build

EXPOSE 8092

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8092/health || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8092"]


# --- hardened (STORY-oss-18): `docker build --target hardened` ---------------------------------
# Non-root, any UID in group 0 (OpenShift's arbitrary UID): files group 0 with the owner's
# permissions minus write, HOME group-writable, numeric USER. The MCP server writes no file at
# run time; the CA bundle and the client certificate are read-only mounts.
FROM base AS hardened
ENV HOME=/home/atlas \
    PYTHONDONTWRITEBYTECODE=1
RUN mkdir -p /home/atlas \
 && chgrp -R 0 /app /home/atlas \
 && chmod -R g=u,g-w,o-w /app \
 && chmod 0775 /home/atlas
USER 10001:0
# base's probe, with python instead of curl, so both hardened images probe the same way.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8092/health', timeout=4)"]


# --- hardened-ubi (TASK-1477, ARO 1.2): `docker build --target hardened-ubi` --------------------
# `hardened` on Red Hat's Universal Base Image, for OpenShift (docs/oss/research/openshift-aro.md,
# question 1); the same shape as backend/Dockerfile's `hardened-ubi`. Opt-in: only images.yml
# builds it. /app (code, the hashed lock, Dockerfile.build) comes from `base`, so
# app/deploy_status.py fingerprints the same files; the lock goes into the image's venv
# (/opt/app-root, first on PATH) with --require-hashes; every file group 0 and not
# group-writable (the venv too), HOME=/home/atlas, numeric USER, the python HEALTHCHECK of
# `hardened` (the image has no curl to need). Pinned tag@digest; Dependabot follows `9.8` (/mcp).
FROM registry.access.redhat.com/ubi9/python-312-minimal:9.8@sha256:d0ac54e77cd0be473b22fb274493526027f2746ec503b4a97ca08257813f0d4b AS hardened-ubi
USER 0
WORKDIR /app
COPY --from=base /app /app
RUN pip install --no-cache-dir --require-hashes -r requirements-lock.txt  && mkdir -p /home/atlas  && chgrp -R 0 /app /home/atlas  && chmod -R g=u,g-w,o-w /app /opt/app-root  && chmod 0775 /home/atlas
ENV HOME=/home/atlas     PYTHONDONTWRITEBYTECODE=1
USER 10001:0
EXPOSE 8092
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3     CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8092/health', timeout=4)"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8092"]


# --- the default (no --target): today's image, unchanged ---------------------------------------
# MUST stay the last stage, and empty: an instruction here changes the homelab's image.
FROM base
