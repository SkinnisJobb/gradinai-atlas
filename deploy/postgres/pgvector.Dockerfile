# atlas-postgres for deploy/oss: pgvector/pgvector 0.8.7 on PostgreSQL 16 (Debian 13), with
# gosu replaced by the setpriv shim. Why, and what that changes: ./Dockerfile (the homelab's
# image, the same change on postgres:16).
#
# Debian 13 rather than the pgvector default (bookworm): on bookworm the image also carries
# Perl 5.36 with three fixable CRITICALs and 16 HIGHs (Trivy 0.75.0, 2026-10-25); trixie's
# Perl 5.40 has none. An install initialised on the earlier bookworm image moves glibc
# 2.36 -> 2.41 with this image: run tools/db_collation_check.sh after the upgrade (SECURITY.md).
#
# Tag plus digest, followed by Dependabot (docker, directory /deploy/postgres). The extension
# is only CREATEd by the retrieval add-on; the base install does not use it.
FROM pgvector/pgvector:0.8.7-pg16-trixie@sha256:9cf8b511f0e9a2c209b61430fc139f1ab9c75edbb31fb1751db6617b246a897a

# Delete first, in its own layer: a scanner (Trivy) reads each layer, and a file merely
# overwritten in a later layer still reports the Go binary of the earlier one; a deletion
# (whiteout) does not.
RUN rm /usr/local/bin/gosu
COPY gosu /usr/local/bin/gosu
RUN chmod 0755 /usr/local/bin/gosu \
 && gosu nobody true \
 && [ "$(gosu postgres id -un)" = postgres ]

# No package manager in the running image. CVE-2026-109518 (HIGH, apt/libapt-pkg7.0 3.0.3,
# fixed in 3.0.4, 2026-11-05) is in this base and pgvector had not published a fixed digest of
# the tag; postgres:16-trixie and python:3.12-slim had (./Dockerfile, backend/Dockerfile). Nothing
# in this image uses apt at runtime: the entrypoint, pg_isready, the gosu shim and the vector
# extension don't. Purging it (dpkg, offline, so the same base digest still gives the same files)
# closes this finding and every later one in apt; upgrading from trixie-security instead would
# need the network at build time and a version pin that disappears from the mirror. Neither
# package is Essential or Protected, and dpkg refuses if anything installed still depends on them.
RUN dpkg --purge apt libapt-pkg7.0 \
 && ! dpkg -s apt >/dev/null 2>&1 \
 && ! dpkg -s libapt-pkg7.0 >/dev/null 2>&1 \
 && pg_config --version | grep -q ' 16\.'
