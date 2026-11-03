# THREE STAGES, ONE RECIPE (STORY-oss-18): `base` is the image as it always was, `hardened`
# (`--target hardened`: the OSS install and .github/workflows/images.yml) adds a non-root,
# UID-agnostic runtime on top, and the LAST stage is `base` unchanged, so a build without
# `--target` (the homelab) produces today's image. See backend/Dockerfile.

# Pinned tag@digest (TASK-1365); digest bumped in LCM patch round 1 (2026-10-25): python 3.12.15, trixie.
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS base

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


# --- the default (no --target): today's image, unchanged ---------------------------------------
# MUST stay the last stage, and empty: an instruction here changes the homelab's image.
FROM base
