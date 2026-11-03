#!/usr/bin/env bash
# Collation-version check for a Postgres container, and the repair when it is off.
#
#   bash tools/db_collation_check.sh [--fix] [--blocking] [--user U] [CONTAINER ...]
#
#   CONTAINER   docker container name or id; default atlas-postgres (the homelab's live one).
#               Staging: atlas-postgres-staging, the fork: atlas-postgres-test. An OSS install:
#               "$(docker compose -f deploy/oss/docker-compose.yml ps -q atlas-postgres)".
#   --user U    the database superuser (default atlas, as POSTGRES_USER everywhere here).
#   --fix       for each database whose recorded collation version differs from the one the
#               C library in the running image provides: REINDEX DATABASE CONCURRENTLY, then
#               ALTER DATABASE ... REFRESH COLLATION VERSION, then check again.
#   --blocking  with --fix: plain REINDEX DATABASE (takes locks) instead of CONCURRENTLY.
#
# Run it on the host that runs the containers:  ssh <host> 'cd ~/project-atlas && bash tools/db_collation_check.sh'
#
# WHY. Text indexes are sorted by the C library's collation rules (glibc, for the default libc
# provider). A new Debian release under the same Postgres major brings a new glibc: Debian 12
# (bookworm) has 2.36, Debian 13 (trixie) 2.41, and the rules for some strings changed between
# them. A data directory initialised under one and opened under the other still works, but a
# btree index on text can then be out of order: lookups miss rows, a unique index can admit a
# duplicate. Postgres records the version each database was created or last refreshed under
# (pg_database.datcollversion) and compares it with what the library reports
# (pg_database_collation_actual_version()); on a mismatch it only logs a WARNING on connect.
# The repair is: rebuild every index (REINDEX), then record the new version (REFRESH
# COLLATION VERSION), in that order. Refreshing without reindexing only silences the warning.
#
# WHAT IT REPORTS, per container: every database (template0 included; it allows no connection,
# so its actual version is read from another database), recorded vs actual, OK / MISMATCH /
# n/a (no version recorded: the C or POSIX locale, or template0, which initdb leaves
# unversioned and Postgres therefore never compares); and, per connectable
# database, the collations in pg_collation whose recorded version is off AND that something
# (a column, an index) depends on. The hundreds of predefined ICU collations nothing uses are
# left alone: a stale version there sorts nothing and warns only when used. With --fix those are
# refreshed too, after the reindex of their database rebuilt every index that uses them. A
# template0 WITH a recorded version that is off gets REFRESH only: it holds no user data and
# takes no connection.
#
# CREDENTIALS. None are read or passed. Like tools/snapshot.sh, every statement goes through
# `docker exec <container> psql -U atlas`, the local Unix socket inside the container, which
# the official image trusts. No password in argv, in the environment or in a file here.
#
# FAILURE. A REINDEX ... CONCURRENTLY that fails (e.g. a unique index that now finds a
# duplicate, which is exactly the corruption this guards against) leaves an INVALID index
# named *_ccnew; the script stops, lists the invalid indexes and does NOT refresh the version,
# so the warning stays until the data is repaired by hand. System catalogs are skipped by
# CONCURRENTLY (Postgres says so with a WARNING); their text-like columns are of type `name`,
# which sorts with the C collation, so no glibc change reaches them.
#
# Exit: 0 every database OK (or fixed and checked again), 1 a mismatch left (report mode) or a
# failed fix, 2 usage or a container that cannot be reached.
set -euo pipefail

FIX=0
BLOCKING=0
PGUSER_=atlas
CONTAINERS=()

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --fix) FIX=1 ;;
    --blocking) BLOCKING=1 ;;
    --user) [ $# -ge 2 ] || { usage >&2; exit 2; }; PGUSER_=$2; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) CONTAINERS+=("$1") ;;
  esac
  shift
done
[ ${#CONTAINERS[@]} -gt 0 ] || CONTAINERS=(atlas-postgres)
if [ "$BLOCKING" = 1 ] && [ "$FIX" = 0 ]; then
  echo "--blocking only means something with --fix" >&2; exit 2
fi

# psql in the container: CONTAINER DB SQL. Unaligned, tuples only, no psqlrc, stop on error.
# A mismatched database WARNs (with DETAIL and HINT) on every connection, before any setting
# applies; this script says it once, in its table, so those three lines are dropped from
# stderr and everything else passes through. (`fix` runs under `if !`, where set -e is off, so
# every step there checks its own status.)
NOISE='^(WARNING:  database ".*" has a collation version mismatch|DETAIL:  The database was created using collation version|HINT:  Rebuild all objects in this database)'
q() {
  local errf rc=0
  errf=$(mktemp)
  docker exec "$1" psql -X -v ON_ERROR_STOP=1 -qtA -F '|' -U "$PGUSER_" -d "$2" -c "$3" 2>"$errf" || rc=$?
  grep -vE "$NOISE" "$errf" >&2 || true
  rm -f "$errf"
  return "$rc"
}

DB_SQL="select datname, datallowconn, coalesce(datcollversion, ''),
               coalesce(pg_database_collation_actual_version(oid), ''), datcollate
          from pg_database order by datname"
COLL_SQL="select format('%I.%I', n.nspname, c.collname), c.collversion,
                 pg_collation_actual_version(c.oid)
            from pg_collation c join pg_namespace n on n.oid = c.collnamespace
           where c.collversion is not null
             and c.collversion is distinct from pg_collation_actual_version(c.oid)
             and exists (select 1 from pg_depend d
                          where d.refclassid = 'pg_collation'::regclass and d.refobjid = c.oid)
           order by 1"

# check CONTAINER -> prints the report; sets MISMATCH_DBS (connectable) and MISMATCH_T0.
check() {
  local c=$1 line name conn rec act loc state colls n=0
  MISMATCH_DBS=(); MISMATCH_T0=0; MISMATCH_COLLS=()
  printf '  %-20s %-14s %-14s %-12s %s\n' database recorded actual locale state
  while IFS='|' read -r name conn rec act loc; do
    [ -n "$name" ] || continue
    # No recorded version: Postgres has nothing to compare (the C/POSIX locale, or template0,
    # which initdb leaves unversioned), so neither does this check.
    if [ -z "$rec" ]; then state=n/a
    elif [ "$rec" = "$act" ]; then state=OK
    else
      state=MISMATCH; n=$((n + 1))
      if [ "$conn" = t ]; then MISMATCH_DBS+=("$name"); else MISMATCH_T0=1; fi
    fi
    printf '  %-20s %-14s %-14s %-12s %s\n' "$name" "${rec:--}" "${act:--}" "$loc" "$state"
  done < <(q "$c" postgres "$DB_SQL")
  while IFS='|' read -r name conn; do
    [ "$conn" = t ] || continue
    colls=$(q "$c" "$name" "$COLL_SQL")
    if [ -n "$colls" ]; then
      while IFS='|' read -r cn rec act; do
        printf '  collation %s in %s: recorded %s, actual %s  MISMATCH\n' "$cn" "$name" "$rec" "$act"
        MISMATCH_COLLS+=("$name|$cn"); n=$((n + 1))
      done <<< "$colls"
    fi
  done < <(q "$c" postgres "select datname, datallowconn from pg_database order by 1")
  MISMATCHES=$n
}

invalid_indexes() {  # CONTAINER DB
  q "$1" "$2" "select format('%I.%I', n.nspname, c.relname)
                 from pg_index i join pg_class c on c.oid = i.indexrelid
                 join pg_namespace n on n.oid = c.relnamespace
                where not i.indisvalid order by 1"
}

fix() {
  local c=$1 db verb stmt bad entry
  local dbs=("${MISMATCH_DBS[@]}")
  for entry in "${MISMATCH_COLLS[@]}"; do  # a database with only a collation off is rebuilt too
    case " ${dbs[*]} " in *" ${entry%%|*} "*) ;; *) dbs+=("${entry%%|*}") ;; esac
  done
  if [ "$BLOCKING" = 1 ]; then verb="REINDEX DATABASE"; else verb="REINDEX DATABASE CONCURRENTLY"; fi
  for db in "${dbs[@]}"; do
    echo "  $db: $verb ..."
    # The database name reaches SQL through format('%I'), never through the shell.
    stmt=$(q "$c" "$db" "select format('$verb %I', current_database())")
    if ! q "$c" "$db" "$stmt"; then
      echo "  $db: REINDEX FAILED; collation version NOT refreshed" >&2
      bad=$(invalid_indexes "$c" "$db" || true)
      [ -z "$bad" ] || { echo "  invalid indexes left in $db (repair the data, drop them, rerun):" >&2; echo "$bad" | sed 's/^/    /' >&2; }
      return 1
    fi
    bad=$(invalid_indexes "$c" "$db")
    if [ -n "$bad" ]; then
      echo "  $db: invalid indexes after the reindex; collation version NOT refreshed:" >&2
      echo "$bad" | sed 's/^/    /' >&2
      return 1
    fi
    case " ${MISMATCH_DBS[*]} " in
      *" $db "*)
        stmt=$(q "$c" "$db" "select format('ALTER DATABASE %I REFRESH COLLATION VERSION', current_database())")
        q "$c" "$db" "$stmt" || { echo "  $db: REFRESH COLLATION VERSION failed" >&2; return 1; }
        echo "  $db: reindexed, database collation version refreshed" ;;
    esac
    for entry in "${MISMATCH_COLLS[@]}"; do
      [ "${entry%%|*}" = "$db" ] || continue
      q "$c" "$db" "ALTER COLLATION ${entry#*|} REFRESH VERSION"         || { echo "  $db: ALTER COLLATION ${entry#*|} REFRESH VERSION failed" >&2; return 1; }
      echo "  $db: collation ${entry#*|} refreshed"
    done
  done
  if [ "$MISMATCH_T0" = 1 ]; then
    # template0 takes no connection and holds no user index; refresh it so a database created
    # from it does not start with the old version.
    q "$c" postgres "ALTER DATABASE template0 REFRESH COLLATION VERSION"       || { echo "  template0: REFRESH COLLATION VERSION failed" >&2; return 1; }
    echo "  template0: collation version refreshed"
  fi
}

rc=0
for c in "${CONTAINERS[@]}"; do
  echo "== $c"
  if ! q "$c" postgres "select 1" >/dev/null 2>&1; then
    echo "  cannot reach Postgres in container $c (docker exec ... psql -U $PGUSER_)" >&2
    rc=2; continue
  fi
  echo "  $(q "$c" postgres "select version()")"
  echo "  C library: $(docker exec "$c" sh -c 'ldd --version 2>/dev/null | head -1' || echo unknown)"
  check "$c"
  if [ "$MISMATCHES" = 0 ]; then echo "  all collation versions match"; continue; fi
  if [ "$FIX" = 0 ]; then
    echo "  $MISMATCHES mismatch(es). Repair: rerun with --fix (after a snapshot; SECURITY.md)."
    [ "$rc" = 2 ] || rc=1; continue
  fi
  if ! fix "$c"; then rc=1; continue; fi
  echo "  -- after the fix:"
  check "$c"
  if [ "$MISMATCHES" != 0 ]; then echo "  still $MISMATCHES mismatch(es)" >&2; rc=1; fi
done
exit "$rc"
