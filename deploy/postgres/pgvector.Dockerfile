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
FROM pgvector/pgvector:0.8.7-pg16-trixie@sha256:40d828eda66737aaacb934387a337babf1455ebd56e9bdd13fc08e211257c972

# Delete first, in its own layer: a scanner (Trivy) reads each layer, and a file merely
# overwritten in a later layer still reports the Go binary of the earlier one; a deletion
# (whiteout) does not.
RUN rm /usr/local/bin/gosu
COPY gosu /usr/local/bin/gosu
RUN chmod 0755 /usr/local/bin/gosu \
 && gosu nobody true \
 && [ "$(gosu postgres id -un)" = postgres ]
