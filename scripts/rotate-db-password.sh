#!/usr/bin/env bash
# rotate-db-password.sh: rotate the Postgres password of an Atlas OSS install (OSS-26 J, TASK-1423).
#
# Run from the directory holding the compose file (deploy/oss/), with the stack up, and with the
# same -f files every other `docker compose` command of the install takes (or COMPOSE_FILE in
# .env). The postgres image reads ./secrets/db_password only when it FIRST initialises the volume,
# so replacing the files alone breaks the install; this does the whole sequence instead:
#
#   1. checks that ./secrets/db_password signs in to the database as it stands (nothing changed
#      yet if it does not), and finds every container that mounts ./secrets/db_url;
#   2. generates a new password with openssl, and writes the new db_password and db_url (the old
#      URL with only its password replaced) to a 0700 work directory under ./secrets/, beside a
#      copy of the old two files;
#   3. ALTER ROLE ... PASSWORD inside atlas-postgres, over its local socket, and proves the new
#      password signs in over TCP (scram-sha-256) the way atlas-api connects: to the service
#      name, not to loopback, which the postgres image's pg_hba.conf trusts without a password;
#   4. swaps both files into ./secrets/ (a rename each, 0644 as init-secrets.sh writes them);
#   5. recreates every RUNNING container that mounts db_url (atlas-api, and atlas-backup when its
#      profile runs), waits for them to be healthy, then reads the database from inside atlas-api.
#      A consumer that is not running (the atlas-migrate one-shot, which has exited) mounts the
#      new file when it next starts. atlas-migrate is not rerun: a password change needs no
#      schema step, and on an install whose .env carries SSO settings it cannot start without
#      the OIDC files only atlas-api mounts;
#   6. checks that the old password is now refused, and deletes the old copies.
#
# A failure at any step after 3 rolls back: the old files go back, the role gets the old
# password again, and the same services are recreated on it, so the install keeps working on
# the old password. The script then exits 1. Only if the rollback itself fails are the old
# files left in ./secrets/.rotate-db-password.* and the steps to finish by hand printed.
#
# The password never reaches a command line, an environment variable or the output: openssl
# writes it to a file, the shell builtins read it and assemble the SQL and the DSN, and psql
# gets it on stdin (the ALTER ROLE statement) or in a 0600 passfile inside the container (the
# sign-in checks). The ALTER ROLE runs with log_statement=none and
# log_min_error_statement=panic, so the server log does not keep it either.
#
# atlas-postgres itself is not recreated: it never re-reads db_password on an initialised
# volume, and keeping it running is what makes the swap safe. Connections atlas-api already
# holds keep working between steps 3 and 5; a new one opened in that window fails until
# atlas-api is recreated, so expect a few seconds of errors, not an outage.
#
#   -f, --file FILE       a compose file, as `docker compose -f`; repeatable, in order.
#   --wait-timeout SECS   how long to wait for the recreated services to be healthy (300).
#
# Exit 0 on success, 1 on a refusal or a failure (rolled back), 2 on a usage error.
# Test hook (install smoke only): ATLAS_ROTATE_TEST_FAIL=after-alter|after-swap|after-recreate
# makes the run fail at that point, to prove the rollback. It carries no secret.

set -euo pipefail

OUT=./secrets
FILES=()
WAIT=300

usage() { sed -n '2,47p' "$0"; }
log() { echo "rotate-db-password: $*"; }
fail() { echo "rotate-db-password: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        -f|--file) [ $# -ge 2 ] || { usage >&2; exit 2; }; FILES+=("$2"); shift 2 ;;
        --file=*) FILES+=("${1#--file=}"); shift ;;
        --wait-timeout) [ $# -ge 2 ] || { usage >&2; exit 2; }; WAIT=$2; shift 2 ;;
        --wait-timeout=*) WAIT=${1#--wait-timeout=}; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "rotate-db-password: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done
case "$WAIT" in ''|*[!0-9]*) echo "rotate-db-password: --wait-timeout takes seconds" >&2; exit 2 ;; esac
command -v openssl >/dev/null || fail "openssl is not on PATH"
command -v docker >/dev/null || fail "docker is not on PATH"

DC=(docker compose)
for f in "${FILES[@]+"${FILES[@]}"}"; do DC+=(-f "$f"); done

[ -f "$OUT/db_password" ] && [ -f "$OUT/db_url" ] \
    || fail "$OUT/db_password and $OUT/db_url not found: run this in deploy/oss/ of an install made by init-secrets.sh"

# --- what the install holds now (read by shell builtins only) ---------------------------------
IFS= read -r OLD < "$OUT/db_password" || true
OLD=${OLD%$'\r'}
IFS= read -r URL < "$OUT/db_url" || true
URL=${URL%$'\r'}
[ -n "$OLD" ] || fail "$OUT/db_password is empty"
case "$URL" in *://*:*@*/*) ;; *) fail "$OUT/db_url is not of the form scheme://user:password@host/db" ;; esac
SCHEME=${URL%%://*}
REST=${URL#*://}
USERINFO=${REST%%@*}
HOSTPART=${REST#*@}
ROLE=${USERINFO%%:*}
DB=${HOSTPART#*/}
DB=${DB%%\?*}
[ "${USERINFO#*:}" = "$OLD" ] || fail "the password in $OUT/db_url is not the one in $OUT/db_password; nothing changed (they must match first)"
[[ "$ROLE" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail "the role name in $OUT/db_url is not a plain identifier"
[[ "$DB" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail "the database name in $OUT/db_url is not a plain identifier"
PGHOST_NAME=atlas-postgres PGPORT_NUM=5432
case "${HOSTPART%%/*}" in
    atlas-postgres|atlas-postgres:5432) ;;
    *) fail "$OUT/db_url points at ${HOSTPART%%/*}, not this install's atlas-postgres: rotate an external database's password there" ;;
esac

# --- lock, work directory, rollback on any exit that is not a success ------------------------
LOCK=$OUT/.rotate-db-password.lock
WORK= NEW=
ALTERED=0 SWAPPED=0 RECREATING=0 DONE=0 ROLLING=0 KEEP_WORK=0
RUNNING=()
mkdir "$LOCK" 2>/dev/null || fail "another rotation is running (or one died: remove $LOCK once you are sure)"

# Every line of psql's stderr, with either password replaced, should one ever appear in it.
redact() {
    local line
    while IFS= read -r line || [ -n "$line" ]; do
        line=${line//"$OLD"/<old password>}
        [ -z "$NEW" ] || line=${line//"$NEW"/<new password>}
        printf '%s\n' "$line"
    done
}

# set_password PW: ALTER ROLE over atlas-postgres's local socket, the statement on psql's stdin.
set_password() {
    if printf '%s\n' \
        '\set ON_ERROR_STOP on' \
        "SET log_statement = 'none';" \
        "SET log_min_error_statement = 'panic';" \
        "SET log_min_duration_statement = -1;" \
        "SET password_encryption = 'scram-sha-256';" \
        "ALTER ROLE \"$ROLE\" PASSWORD '$1';" \
        | "${DC[@]}" exec -T atlas-postgres psql -X -q -U "$ROLE" -d "$DB" >/dev/null 2>"$WORK/psql.err"; then
        return 0
    fi
    redact < "$WORK/psql.err" >&2
    return 1
}

# signs_in PW: does PW sign in over TCP, with a password, as atlas-api does? To the db_url host
# (atlas-postgres), never 127.0.0.1: the image's pg_hba.conf trusts loopback, so any password
# would "sign in" there. The passfile lives in the container for the length of one psql call;
# its path is the only thing in the env.
signs_in() {
    printf '*:*:*:%s:%s\n' "$ROLE" "$1" | "${DC[@]}" exec -T atlas-postgres sh -c '
        umask 077; f=$(mktemp) || exit 3; cat > "$f"
        PGPASSFILE="$f" psql -X -w -h "$3" -p "$4" -U "$1" -d "$2" -Atqc "SELECT 1" >/dev/null 2>&1
        rc=$?; rm -f "$f"; exit $rc' sh "$ROLE" "$DB" "$PGHOST_NAME" "$PGPORT_NUM"
}

# recreate: every running consumer, healthy, and atlas-api reading the database.
recreate() {
    if [ ${#RUNNING[@]} -gt 0 ]; then
        "${DC[@]}" up -d --no-deps --force-recreate --wait --wait-timeout "$WAIT" "${RUNNING[@]}" || return 1
        log "recreated and healthy: ${RUNNING[*]}"
    fi
    if printf ' %s ' "${RUNNING[@]+"${RUNNING[@]}"}" | grep -q ' atlas-api '; then
        "${DC[@]}" exec -T atlas-api atlas-admin user list >/dev/null || return 1
        log "atlas-api reads the database"
    fi
}

test_fail() {
    if [ "${ATLAS_ROTATE_TEST_FAIL:-}" = "$1" ]; then fail "test failure injected $1 (ATLAS_ROTATE_TEST_FAIL)"; fi
    return 0
}

rollback() {
    set +e
    ROLLING=1
    local ok=1
    echo "rotate-db-password: rolling back to the old password" >&2
    if [ "$SWAPPED" = 1 ]; then
        mv -f "$WORK/db_password.old" "$OUT/db_password" && mv -f "$WORK/db_url.old" "$OUT/db_url" || ok=0
        [ "$ok" = 1 ] && SWAPPED=0
    fi
    if [ "$ALTERED" = 1 ]; then
        set_password "$OLD" && signs_in "$OLD" || ok=0
    fi
    if [ "$RECREATING" = 1 ] && [ "$ok" = 1 ]; then
        recreate || ok=0
    fi
    if [ "$ok" = 1 ]; then
        echo "rotate-db-password: rolled back; the install runs on the old password, as before" >&2
    else
        KEEP_WORK=1
        cat >&2 <<EOF
rotate-db-password: THE ROLLBACK DID NOT COMPLETE. The old files are in $WORK
  (db_password.old, db_url.old) unless they are already back in $OUT. To finish by hand:
  put them back as $OUT/db_password and $OUT/db_url, set that password on the role with
  \`docker compose exec atlas-postgres psql -U $ROLE -d $DB\` and \\password $ROLE, then
  docker compose up -d --no-deps --force-recreate --wait ${RUNNING[*]-} (with your -f files).
  Then remove $LOCK.
EOF
    fi
}

on_exit() {
    local rc=$?
    if [ "$DONE" != 1 ] && [ "$ROLLING" != 1 ] && { [ "$ALTERED" = 1 ] || [ "$SWAPPED" = 1 ]; }; then
        rollback
        rc=1
    fi
    [ "$KEEP_WORK" = 1 ] || [ -z "$WORK" ] || rm -rf "$WORK"
    [ "$KEEP_WORK" = 1 ] || rmdir "$LOCK" 2>/dev/null || true
    exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT TERM
umask 077
WORK=$(mktemp -d "$OUT/.rotate-db-password.XXXXXX")

# --- 1. the install as it stands --------------------------------------------------------------
[ -n "$("${DC[@]}" ps -q atlas-postgres)" ] || fail "atlas-postgres is not running (docker compose up -d first)"
signs_in "$OLD" || fail "the password in $OUT/db_password does not sign in to the database; nothing changed"
log "the current password signs in"

while read -r svc oneoff status mounts; do
    [ "$oneoff" = True ] && continue
    case "$mounts" in *"/secrets/db_url|"*) ;; *) continue ;; esac
    if [ "$status" = running ]; then RUNNING+=("$svc"); else log "$svc is $status: it reads the new file when it next starts"; fi
done < <(for cid in $("${DC[@]}" ps -a -q); do
    docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}} {{index .Config.Labels "com.docker.compose.oneoff"}} {{.State.Status}} {{range .Mounts}}{{.Source}}|{{end}}' "$cid"
done)
[ ${#RUNNING[@]} -gt 0 ] || log "no running container reads db_url: nothing to recreate"
log "services to recreate: ${RUNNING[*]-}"

# --- 2. the new password and both files, beside a copy of the old ones ------------------------
openssl rand -hex 24 > "$WORK/db_password.new"
IFS= read -r NEW < "$WORK/db_password.new" || true
NEW=${NEW%$'\r'}   # a Windows openssl ends the line with CRLF
[[ "$NEW" =~ ^[0-9a-f]{48}$ ]] || fail "openssl did not produce a password"
printf '%s\n' "$NEW" > "$WORK/db_password.new"
printf '%s://%s:%s@%s\n' "$SCHEME" "$ROLE" "$NEW" "$HOSTPART" > "$WORK/db_url.new"
chmod 0644 "$WORK/db_password.new" "$WORK/db_url.new"   # init-secrets.sh's rule (see there)
cp -p "$OUT/db_password" "$WORK/db_password.old"
cp -p "$OUT/db_url" "$WORK/db_url.old"

# --- 3. the role's password -------------------------------------------------------------------
if ! set_password "$NEW"; then
    # A reply lost after the commit would leave the new password set: undo it in that case.
    if signs_in "$NEW"; then ALTERED=1; fi
    fail "ALTER ROLE failed (the role must be the superuser the postgres image made)"
fi
ALTERED=1
test_fail after-alter
signs_in "$NEW" || fail "the new password does not sign in"
log "the role has the new password, and it signs in"

# --- 4. the files -----------------------------------------------------------------------------
SWAPPED=1
mv -f "$WORK/db_password.new" "$OUT/db_password"
mv -f "$WORK/db_url.new" "$OUT/db_url"
log "wrote $OUT/db_password and $OUT/db_url"
test_fail after-swap

# --- 5. the services that read it -------------------------------------------------------------
RECREATING=1
recreate || fail "a service did not come back on the new password"
test_fail after-recreate

# --- 6. the old password is gone --------------------------------------------------------------
if signs_in "$OLD"; then fail "the old password still signs in"; fi
log "the old password is refused"
DONE=1
log "done. Back up ./secrets/ again (operations.md, Back up the secrets): an older copy holds the old password"
