# atlas-postgres for the homelab (docker-compose.yml): the official postgres:16 image with its
# gosu binary replaced by a setpriv shim. pgvector.Dockerfile beside it is the same change on
# the pgvector image, for deploy/oss.
#
# WHY A DERIVED IMAGE (LCM, 2026-10-25). Trivy 0.75.0 on every upstream Postgres image, the
# majors and Debian releases alike (postgres:16/17/18, bookworm, trixie, alpine, pgvector
# pg16/17/18), finds ONE fixable CRITICAL (CVE-2025-68121) and 25 fixable HIGHs in the same
# place: /usr/local/bin/gosu, a static Go binary built with Go 1.24.6 that the entrypoint uses
# once, to drop root. No upstream tag fixes it, so no tag or Debian bump could. The shim
# (./gosu) does the same with setpriv, which the image already ships; with no Go binary left,
# those findings are gone. Nothing else changes: same entrypoint, same PGDATA, same data
# directory format, same Postgres binaries. Built offline (no apt, no download), so a rebuild
# from the same base digest gives the same files.
#
# THE BASE. Tag plus digest (tools/ci/check_pins.py), followed by Dependabot (docker, directory
# /deploy/postgres). The tag names the Debian release on purpose: `postgres:16` moved from
# bookworm to trixie once already, and a glibc change underneath a live data directory can
# change collation order (tools/db_collation_check.sh, SECURITY.md). A new Debian release must
# be a deliberate tag change, not a digest refresh. Postgres 16 -> 17 is a dump/restore and is
# excluded from Dependabot (semver-major).
#
# The live volume was initialised by `postgres:16` and is read unchanged by this image (same
# major, same Debian 13 the homelab already runs, so no collation change is expected; the
# collation check confirms it after the swap).
FROM postgres:16-trixie@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97

# Delete first, in its own layer: a scanner (Trivy) reads each layer, and a file merely
# overwritten in a later layer still reports the Go binary of the earlier one; a deletion
# (whiteout) does not.
RUN rm /usr/local/bin/gosu
COPY gosu /usr/local/bin/gosu
# Same smoke test as upstream's Dockerfile runs on its gosu, plus the user the entrypoint needs.
RUN chmod 0755 /usr/local/bin/gosu \
 && gosu nobody true \
 && [ "$(gosu postgres id -un)" = postgres ]
