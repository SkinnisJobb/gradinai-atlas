#!/usr/bin/env bash
# Regenerate the hashed lock files the images install from (STORY-atlas-lcm-plan, item 3):
#
#   backend/requirements-lock.txt   <- backend/requirements.txt
#   mcp/requirements-lock.txt       <- mcp/requirements.txt
#
# requirements.txt stays the file people edit: the direct pins, exactly as ADR 0006 has them.
# The lock adds every transitive dependency at an exact version, with the sha256 of every
# published distribution, and backend/Dockerfile and mcp/Dockerfile install it with
# `pip install --require-hashes`. A tampered or substituted wheel on the index is then a failed
# build, not a running container.
#
# DEPENDABOT. The lock is named requirements-*.txt on purpose: Dependabot's pip ecosystem reads
# every requirements file in the directory, so a bump of a direct pin edits requirements.txt AND
# the lock (hashes included) in the same PR, and a security update can move a transitive pin
# that only the lock names. When such a PR is red because a transitive requirement moved too,
# run this script on the PR branch and push.
#
# ONE RESOLUTION FOR BOTH. CI installs backend and mcp into one environment, so a package both
# need must be at the same version in both locks. The two files are first resolved together
# (no hashes, a throwaway constraint file), and each lock is then compiled under it.
# tools/ci/check_locks.py fails when they disagree.
#
# UNIVERSAL. `--universal --python-version 3.12` writes one file valid on every platform and
# Python >= 3.12 (markers for the Windows-only and Linux-only packages), so the same lock
# installs in python:3.12-slim, on the CI runner and on a developer machine.
#
#   bash tools/ci/lock_requirements.sh [--uv PATH] [--upgrade]
#
# Without --upgrade every pin already in a lock that still satisfies is kept. --upgrade moves
# the transitive pins to the newest versions the direct pins allow: a deliberate refresh that
# changes what the homelab image installs on its next build.
set -euo pipefail

UV_VERSION=0.13.0
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"

uv_bin=""
upgrade=()
while [ $# -gt 0 ]; do
  case "$1" in
    --uv) uv_bin=$2; shift 2 ;;
    --upgrade) upgrade=(--upgrade); shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$uv_bin" ] || uv_bin=$(command -v uv || true)
[ -n "$uv_bin" ] || { echo "uv not found: python -m pip install uv==$UV_VERSION" >&2; exit 2; }
have=$("$uv_bin" --version | awk '{print $2}')
[ "$have" = "$UV_VERSION" ] || { echo "uv $have found, this script is pinned to $UV_VERSION" >&2; exit 2; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
common=(--universal --python-version 3.12 --quiet)

# The existing locks seed the joint resolution as preferences (uv keeps a pin already present
# in the -o file when it still satisfies), so a rerun without --upgrade is a no-op.
for c in backend mcp; do
  [ -f "$c/requirements-lock.txt" ] && grep -E '^[A-Za-z0-9._-]+==' "$c/requirements-lock.txt" | sed 's/ .*//' >> "$tmp/seed.txt"
done
[ -f "$tmp/seed.txt" ] && sort -u "$tmp/seed.txt" > "$tmp/joint.txt"
"$uv_bin" pip compile "${common[@]}" "${upgrade[@]}" --no-header --no-annotate \
  backend/requirements.txt mcp/requirements.txt -o "$tmp/joint.txt"

for c in backend mcp; do
  "$uv_bin" pip compile "${common[@]}" "${upgrade[@]}" --generate-hashes --no-annotate \
    --custom-compile-command "bash tools/ci/lock_requirements.sh" \
    --constraint "$tmp/joint.txt" "$c/requirements.txt" -o "$c/requirements-lock.txt"
  echo "wrote $c/requirements-lock.txt ($(grep -cE '^[A-Za-z0-9._-]+==' "$c/requirements-lock.txt") packages)"
done
