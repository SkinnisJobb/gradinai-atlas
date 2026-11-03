#!/usr/bin/env bash
# The row count of every table in an OSS install's database, one line `<table>=<rows>` per table
# of the public schema, sorted by name (install smoke, OSS-26 C: leg 8d and the upgrade job).
#
#   cd deploy/oss && bash ../../tools/ci/oss_db_counts.sh > counts.txt
#
# Run from the compose directory of the install (its .env and COMPOSE_FILE apply). Every table is
# counted, not only the ones a backup manifest names, so "identical counts" means the whole
# database. Reads no credential: psql runs inside atlas-postgres over its local socket, as
# tools/db_collation_check.sh does.
set -euo pipefail

docker compose exec -T atlas-postgres psql -U atlas -d atlas -AtX -v ON_ERROR_STOP=1 -c \
  "SELECT t || '=' || (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM public.%I', t), false, true, '')))[1]::text FROM (SELECT tablename::text AS t FROM pg_tables WHERE schemaname = 'public') s ORDER BY t"
