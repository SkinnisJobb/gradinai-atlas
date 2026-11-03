#!/usr/bin/env bash
# test_init_certs.sh: scripts/init-certs.sh takes the public name from ./.env when no --host is
# given, so a renewal with --force keeps it (TASK-1419, OSS-26 F); and it makes the origin
# listeners' certificates, in a full run and with --origin-only (OSS-26 D). Real openssl, no Docker.
#
#   bash tools/ci/test_init_certs.sh
#
# Each case runs init-certs.sh in a fresh sandbox outside any git work tree, with a .env that
# holds only the lines the case needs (no secret: ATLAS_PUBLIC_HOST is configuration), and
# reads the issued server.crt's subjectAltName back with openssl. Run by oss.yml's `config` job.
#
# Exit 0 when every case passes, 1 otherwise.
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
SCRIPT="$ROOT/scripts/init-certs.sh"
SB=$(mktemp -d "${TMPDIR:-/tmp}/init-certs-test.XXXXXX")
trap 'rm -rf "$SB"' EXIT
fails=0
n=0

pass() { echo "[PASS] $1"; }
fail() { echo "[FAIL] $1${2:+ — $2}"; fails=$((fails + 1)); }

# fresh <name> <.env content or "-" for no .env>: a new sandbox directory; prints its path.
fresh() {
    n=$((n + 1))
    local d="$SB/$n-$1"
    mkdir -p "$d"
    [ "$2" = "-" ] || printf '%s\n' "$2" > "$d/.env"
    printf '%s' "$d"
}
# run <dir> [args...]: init-certs.sh in <dir>; output in <dir>/out, exit code in RC.
run() {
    local d=$1; shift
    (cd "$d" && bash "$SCRIPT" "$@") > "$d/out" 2>&1
    RC=$?
}
san() { openssl x509 -noout -ext subjectAltName -in "$1/secrets/tls/server.crt" 2>/dev/null | tail -n +2 | tr -d ' '; }
serial() { openssl x509 -noout -serial -in "$1/secrets/tls/server.crt" 2>/dev/null; }
has_name() { case ",$(san "$1")," in *",$2,"*) return 0 ;; *) return 1 ;; esac; }

ENV_BASE='# a stranger'"'"'s .env, trimmed to what the case needs
ATLAS_PUBLIC_URL=https://atlas.example.test
ATLAS_PUBLIC_HOST=atlas.example.test
ATLAS_TLS_PORT=443'

# 1. first run, no --host: the name comes from .env, and the script says so.
d=$(fresh env-first "$ENV_BASE")
run "$d"
if [ "$RC" = 0 ] && has_name "$d" DNS:atlas.example.test; then
    pass "no --host: the certificate names ATLAS_PUBLIC_HOST from .env"
else fail "no --host: the certificate names ATLAS_PUBLIC_HOST from .env" "rc=$RC san=$(san "$d") $(cat "$d/out")"; fi
grep -qF "init-certs: public name: atlas.example.test (from ATLAS_PUBLIC_HOST in ./.env)" "$d/out" \
    && pass "it prints the name and that it came from .env" \
    || fail "it prints the name and that it came from .env" "$(cat "$d/out")"
for internal in DNS:atlas-api DNS:atlas-mcp DNS:localhost IPAddress:127.0.0.1; do
    has_name "$d" "$internal" || fail "the internal names are still there ($internal)" "$(san "$d")"
done

# 2. the renewal: --force, no --host. A NEW certificate, the public name kept. This is the bug.
before=$(serial "$d")
run "$d" --force
if [ "$RC" = 0 ] && has_name "$d" DNS:atlas.example.test && [ "$(serial "$d")" != "$before" ]; then
    pass "renewal (--force, no --host): a new certificate that keeps the public name"
else fail "renewal (--force, no --host): a new certificate that keeps the public name" "rc=$RC san=$(san "$d") $(cat "$d/out")"; fi

# 3. without --force, an existing install is refused as before.
run "$d"
[ "$RC" = 1 ] && grep -q "refusing to overwrite" "$d/out" \
    && pass "a second run without --force still refuses" \
    || fail "a second run without --force still refuses" "rc=$RC $(cat "$d/out")"

# 4. --host wins over .env, unchanged behaviour: only the names given.
run "$d" --force --host other.example.test
if [ "$RC" = 0 ] && has_name "$d" DNS:other.example.test && ! has_name "$d" DNS:atlas.example.test; then
    pass "--host is used as given and .env is not read"
else fail "--host is used as given and .env is not read" "rc=$RC san=$(san "$d")"; fi
grep -qF "public name: other.example.test (from --host)" "$d/out" \
    && pass "it says the name came from --host" || fail "it says the name came from --host" "$(cat "$d/out")"

# 5. no .env, no --host: internal names only, and it says so.
d=$(fresh no-env -)
run "$d"
if [ "$RC" = 0 ] && ! has_name "$d" DNS:atlas.example.test && grep -qF "public name: none" "$d/out"; then
    pass "no .env and no --host: internal names only, and it says so"
else fail "no .env and no --host: internal names only, and it says so" "rc=$RC $(cat "$d/out")"; fi

# 6. a quoted IP address, a trailing comment, and the LAST assignment wins (init-secrets' rule).
d=$(fresh quoted-ip 'ATLAS_PUBLIC_HOST=first.example.test
  ATLAS_PUBLIC_HOST = "192.0.2.10"   # the LAN address')
run "$d"
if [ "$RC" = 0 ] && has_name "$d" IPAddress:192.0.2.10 && ! has_name "$d" DNS:first.example.test; then
    pass "a quoted IP with a comment, last assignment wins: IP SAN"
else fail "a quoted IP with a comment, last assignment wins: IP SAN" "rc=$RC san=$(san "$d") $(cat "$d/out")"; fi

# 7. perturbation: a commented-out line is not a setting.
d=$(fresh commented '# ATLAS_PUBLIC_HOST=atlas.example.test
ATLAS_PUBLIC_URL=https://atlas.example.test')
run "$d"
if [ "$RC" = 0 ] && ! has_name "$d" DNS:atlas.example.test && grep -qF "public name: none" "$d/out"; then
    pass "(perturbation) a commented ATLAS_PUBLIC_HOST is not read"
else fail "(perturbation) a commented ATLAS_PUBLIC_HOST is not read" "rc=$RC san=$(san "$d")"; fi

# 8. a malformed name in .env is a usage error naming .env, and writes nothing.
d=$(fresh malformed 'ATLAS_PUBLIC_HOST=bad/name')
run "$d"
if [ "$RC" = 2 ] && grep -q "ATLAS_PUBLIC_HOST 'bad/name' in ./.env" "$d/out" && [ ! -e "$d/secrets" ]; then
    pass "a malformed ATLAS_PUBLIC_HOST is refused (exit 2) before anything is written"
else fail "a malformed ATLAS_PUBLIC_HOST is refused (exit 2) before anything is written" "rc=$RC $(cat "$d/out")"; fi

# 9. --client-only ignores the name (it issues no server certificate) and still works.
d=$(fresh client-only "$ENV_BASE")
run "$d" --client-only
[ "$RC" = 0 ] && [ -e "$d/secrets/tls/mcp-client.crt" ] && [ ! -e "$d/secrets/tls/server.crt" ] \
    && pass "--client-only is unchanged" || fail "--client-only is unchanged" "rc=$RC $(cat "$d/out")"

# 11. OSS-26 D: a full run also makes the origin listeners' certificates, under the install CA.
d=$(fresh origin-full "$ENV_BASE")
run "$d"
T=$d/secrets/tls
osan() { openssl x509 -noout -ext subjectAltName -in "$T/$1" 2>/dev/null | tail -n +2 | tr -d ' '; }
if [ "$RC" = 0 ] && cmp -s "$T/origin-ca.crt" "$T/ca.crt" \
    && openssl verify -CAfile "$T/ca.crt" "$T/api-origin.crt" "$T/mcp-origin.crt" >/dev/null 2>&1; then
    pass "a full run: api-origin.crt and mcp-origin.crt chain to the install CA, origin-ca.crt is ca.crt"
else fail "a full run: origin certificates under the install CA" "rc=$RC $(cat "$d/out")"; fi
[ "$(osan api-origin.crt)" = "DNS:atlas-api-origin,DNS:localhost,IPAddress:127.0.0.1" ] \
    && [ "$(osan mcp-origin.crt)" = "DNS:atlas-mcp-origin,DNS:localhost,IPAddress:127.0.0.1" ] \
    && pass "each origin certificate names only its own service (and loopback, for the healthcheck)" \
    || fail "origin certificate names" "api=$(osan api-origin.crt) mcp=$(osan mcp-origin.crt)"
modes=$(stat -c '%a' "$T/api-origin.key" "$T/mcp-origin.key" 2>/dev/null | tr '\n' ' ')
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) pass "(key modes not checked on Windows: $modes)" ;;
    *) [ "$modes" = "644 644 " ] && pass "origin keys are 0644 (read by the services' arbitrary UID)" || fail "origin key modes" "$modes" ;;
esac
cmp -s "$T/api-origin.key" "$T/server.key" && fail "atlas-api's origin key is the public door's key" \
    || pass "the origin keys are not server.key (the services never hold the public door's key)"

# 12. --origin-only on an install made before OSS-26 D: adds exactly the five files, under a
# SEPARATE CA, and leaves ca.crt (what clients trust) and server.crt alone; then refuses.
for f in origin-ca.crt api-origin.crt api-origin.key mcp-origin.crt mcp-origin.key; do rm -f "$T/$f"; done
ca_before=$(openssl x509 -noout -fingerprint -sha256 -in "$T/ca.crt")
srv_before=$(serial "$d")
run "$d" --origin-only
if [ "$RC" = 0 ] && [ -e "$T/origin-ca.crt" ] && ! cmp -s "$T/origin-ca.crt" "$T/ca.crt" \
    && openssl verify -CAfile "$T/origin-ca.crt" "$T/api-origin.crt" "$T/mcp-origin.crt" >/dev/null 2>&1 \
    && [ "$(openssl x509 -noout -fingerprint -sha256 -in "$T/ca.crt")" = "$ca_before" ] && [ "$(serial "$d")" = "$srv_before" ]; then
    pass "--origin-only: the origin certificates under a separate CA; ca.crt and server.crt unchanged"
else fail "--origin-only on an existing install" "rc=$RC $(cat "$d/out")"; fi
run "$d" --origin-only
[ "$RC" = 1 ] && grep -q "refusing to overwrite" "$d/out" \
    && pass "--origin-only refuses a second run without --force" || fail "--origin-only second run" "rc=$RC $(cat "$d/out")"
run "$d" --origin-only --client-only
[ "$RC" = 2 ] && pass "--origin-only with --client-only is a usage error" || fail "--origin-only --client-only" "rc=$RC"

# 10. no key is ever printed.
if cat "$SB"/*/out | grep -q "PRIVATE KEY"; then fail "no output carries a key"; else pass "no output carries a key"; fi

echo
if [ "$fails" = 0 ]; then echo "test_init_certs: ALL PASS"; exit 0; fi
echo "test_init_certs: $fails FAIL"; exit 1
