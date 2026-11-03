#!/usr/bin/env bash
# init-certs.sh: a throwaway install CA and the TLS files an Atlas install needs (OSS-24).
#
# For an install with no PKI of its own. Every hop that carries a Bearer token is HTTPS with
# the certificate verified, so the services need certificates and a CA to check them against.
# This makes both, with openssl and nothing else, under ./secrets/tls/ of the CURRENT
# directory, and nowhere else:
#
#   ca.crt           the install CA's certificate. Public. Clients trust it: atlas-mcp
#                    (ATLAS_CA_BUNDLE), curl --cacert, NODE_EXTRA_CA_CERTS for Claude Code.
#   server.crt/.key  the TLS front's certificate (serverAuth). SANs: atlas-api, atlas-mcp,
#                    gateway, localhost, 127.0.0.1, ::1, and every --host you pass.
#   client-ca.crt    the CA the API's TLS front trusts for client certificates when mTLS is
#                    switched on (a copy of ca.crt here; --client-only makes a separate one).
#   mcp-client.crt/.key  atlas-mcp's client certificate (clientAuth, CN atlas-mcp), for the
#                    optional mTLS leg. Unused unless mTLS is switched on.
#   api-origin.crt/.key  atlas-api's own TLS listener (serverAuth, OSS-26 D): the leg from the
#                    ingress to the API. SANs: atlas-api-origin, localhost, 127.0.0.1.
#   mcp-origin.crt/.key  atlas-mcp's own TLS listener, the leg from the ingress to the MCP
#                    server. SANs: atlas-mcp-origin, localhost, 127.0.0.1.
#   origin-ca.crt    the CA the ingress checks those two listeners against (a copy of ca.crt
#                    here; --origin-only makes a separate one).
#
# THE CA KEY IS NEVER WRITTEN TO ./secrets/. It lives in a private temporary directory for the
# few seconds it takes to sign, and is deleted on exit, so nothing can be signed with this CA
# afterwards. To add a name or renew, run again with --force: a new CA and new certificates,
# and every client re-trusts the new ca.crt.
#
# Usage (from the directory holding the compose file, e.g. deploy/oss/):
#   ../../scripts/init-certs.sh [--host NAME]... [--days N] [--force]
#   ../../scripts/init-certs.sh --client-only [--force]
#   ../../scripts/init-certs.sh --origin-only [--force]
#
#   --host NAME    the public name clients use (DNS name or IP address). Repeatable. Default:
#                  ATLAS_PUBLIC_HOST from ./.env, as init-secrets.sh reads it, so a renewal
#                  with --force keeps the public name. The script says which name it used.
#   --days N       leaf certificate lifetime in days (default 397; the CA gets 10 years).
#   --force        overwrite existing files. Without it, the script refuses to touch any file
#                  that already exists and exits 1 before writing anything.
#   --client-only  for an install whose server certificate comes from its own PKI: make only
#                  a separate client CA (client-ca.crt) and atlas-mcp's client certificate.
#   --origin-only  make only the two origin listeners' certificates and a separate CA for them
#                  (origin-ca.crt, trusted by the ingress alone). For an install whose server
#                  certificate comes from its own PKI, and for an install made before OSS-26 D,
#                  which has every other file already: its ca.crt, and what clients trust, stay
#                  as they are.
#
# Never prints a key. Never commits anything: ./secrets/ is gitignored, and the script refuses
# to write inside a git work tree unless that path is ignored.
#
# Exit 0 on success, 1 on a refusal or an openssl failure, 2 on a usage error.

set -euo pipefail

OUT="./secrets/tls"
DAYS=397
CA_DAYS=3650
FORCE=0
CLIENT_ONLY=0
ORIGIN_ONLY=0
HOSTS=()

usage() { sed -n '2,52p' "$0"; }
die() { echo "init-certs: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --host) [ $# -ge 2 ] || { usage >&2; exit 2; }; HOSTS+=("$2"); shift 2 ;;
        --host=*) HOSTS+=("${1#--host=}"); shift ;;
        --days) [ $# -ge 2 ] || { usage >&2; exit 2; }; DAYS=$2; shift 2 ;;
        --force) FORCE=1; shift ;;
        --client-only) CLIENT_ONLY=1; shift ;;
        --origin-only) ORIGIN_ONLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "init-certs: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done
[[ "$DAYS" =~ ^[1-9][0-9]{0,4}$ ]] || { echo "init-certs: --days wants a positive number" >&2; exit 2; }
[ "$CLIENT_ONLY$ORIGIN_ONLY" != 11 ] || { echo "init-certs: --client-only and --origin-only are separate runs" >&2; exit 2; }

# The public host, from .env when no --host was given (TASK-1419): the same one-line parse as
# init-secrets.sh. The file is never sourced, and only that one line is looked at. Without
# this, a renewal (--force) run by hand silently dropped the public name from the certificate.
HOST_SOURCE="--host"
if [ ${#HOSTS[@]} -eq 0 ]; then
    HOST_SOURCE=""
    if [ -f ./.env ]; then
        h=$(sed -n -E 's/^[[:space:]]*ATLAS_PUBLIC_HOST[[:space:]]*=[[:space:]]*"?([^"#[:space:]]*)"?.*$/\1/p' ./.env | tail -n 1)
        if [ -n "$h" ]; then HOSTS+=("$h"); HOST_SOURCE="ATLAS_PUBLIC_HOST in ./.env"; fi
    fi
fi
for h in "${HOSTS[@]+"${HOSTS[@]}"}"; do
    [[ "$h" =~ ^[A-Za-z0-9.:-]+$ ]] || {
        if [ "$HOST_SOURCE" = "--host" ]; then echo "init-certs: --host '$h' is not a DNS name or IP address" >&2
        else echo "init-certs: ATLAS_PUBLIC_HOST '$h' in ./.env is not a DNS name or IP address" >&2; fi
        exit 2; }
done
command -v openssl >/dev/null || die "openssl is not on PATH"

# Never write into a tracked tree unless git ignores the path (it does: .gitignore `secrets/`).
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    probe="$OUT/.init-certs-probe"
    git check-ignore -q "$probe" || die "$OUT is inside a git work tree and NOT ignored; refusing to write keys there"
fi

ORIGIN_FILES=(origin-ca.crt api-origin.crt api-origin.key mcp-origin.crt mcp-origin.key)
if [ "$CLIENT_ONLY" = 1 ]; then
    FILES=(client-ca.crt mcp-client.crt mcp-client.key)
elif [ "$ORIGIN_ONLY" = 1 ]; then
    FILES=("${ORIGIN_FILES[@]}")
else
    FILES=(ca.crt server.crt server.key client-ca.crt mcp-client.crt mcp-client.key "${ORIGIN_FILES[@]}")
fi
if [ "$FORCE" = 0 ]; then
    existing=()
    for f in "${FILES[@]}"; do [ -e "$OUT/$f" ] && existing+=("$OUT/$f"); done
    if [ ${#existing[@]} -gt 0 ]; then
        die "refusing to overwrite ${existing[*]} (pass --force to replace them, which makes a new CA)"
    fi
fi

umask 077
mkdir -p "$OUT"
chmod 0700 "$(dirname "$OUT")" "$OUT" 2>/dev/null || true
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

ossl() {  # run openssl quietly; on failure show its stderr (which never carries a key)
    if ! openssl "$@" 2>"$WORK/err"; then cat "$WORK/err" >&2; die "openssl $1 failed"; fi
}
newkey() { ossl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$1"; }

dn_cnf() {  # dn_cnf <file> <cn>: a request config naming the subject. A file rather than
    # -subj "/CN=...", which the Windows shell would rewrite into a path.
    printf '[req]\ndistinguished_name = dn\nprompt = no\n[dn]\nCN = %s\n' "$2" > "$1"
}

make_ca() {  # make_ca <cn> -> $WORK/ca.key, $WORK/ca.crt
    newkey "$WORK/ca.key"
    dn_cnf "$WORK/ca.cnf" "$1"
    cat >> "$WORK/ca.cnf" <<EOF
[v3]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
EOF
    ossl req -new -x509 -key "$WORK/ca.key" -days "$CA_DAYS" -sha256 -config "$WORK/ca.cnf" -extensions v3 -out "$WORK/ca.crt"
}

sign() {  # sign <name> <cn> <eku> [san] -> $WORK/<name>.crt, $WORK/<name>.key
    local name=$1 cn=$2 eku=$3 san=${4:-}
    newkey "$WORK/$name.key"
    {
        echo "basicConstraints = critical, CA:FALSE"
        echo "keyUsage = critical, digitalSignature"
        echo "extendedKeyUsage = $eku"
        echo "subjectKeyIdentifier = hash"
        echo "authorityKeyIdentifier = keyid"
        [ -n "$san" ] && echo "subjectAltName = $san"
    } > "$WORK/$name.ext"
    dn_cnf "$WORK/$name.cnf" "$cn"
    ossl req -new -key "$WORK/$name.key" -config "$WORK/$name.cnf" -out "$WORK/$name.csr"
    ossl x509 -req -in "$WORK/$name.csr" -CA "$WORK/ca.crt" -CAkey "$WORK/ca.key" -CAcreateserial \
        -days "$DAYS" -sha256 -extfile "$WORK/$name.ext" -out "$WORK/$name.crt"
}

san_list() {
    local san="DNS:atlas-api,DNS:atlas-mcp,DNS:gateway,DNS:localhost,IP:127.0.0.1,IP:::1" h
    for h in "${HOSTS[@]+"${HOSTS[@]}"}"; do
        if [[ "$h" =~ ^[0-9.]+$ || "$h" == *:* ]]; then san="$san,IP:$h"; else san="$san,DNS:$h"; fi
    done
    printf '%s' "$san"
}

place() {  # place <src> <dest name> <mode>
    cp "$1" "$WORK/.place"
    chmod "$3" "$WORK/.place"
    mv -f "$WORK/.place" "$OUT/$2"
}

# mcp-client.key is 0644 on purpose: atlas-mcp runs the hardened image (STORY-oss-18) as a
# non-root UID that may be any number, and cannot read a 0600 file owned by you. This directory
# and its parent are 0700, so nobody else on this host can reach it. server.key stays 0600:
# atlas-tls (Caddy) reads it as root. api-origin.key and mcp-origin.key are 0644 for the same
# reason as mcp-client.key: atlas-api and atlas-mcp read them under that arbitrary UID. Each
# service gets its own key, and neither ever sees server.key (the public door's).

# The origin listeners (OSS-26 D): the ingress -> atlas-api and ingress -> atlas-mcp legs.
# localhost / 127.0.0.1 so each container's healthcheck verifies its own listener too.
sign_origins() {
    sign api-origin atlas-api-origin serverAuth "DNS:atlas-api-origin,DNS:localhost,IP:127.0.0.1"
    sign mcp-origin atlas-mcp-origin serverAuth "DNS:atlas-mcp-origin,DNS:localhost,IP:127.0.0.1"
}
place_origins() {
    place "$WORK/ca.crt" origin-ca.crt 0644
    place "$WORK/api-origin.crt" api-origin.crt 0644
    place "$WORK/api-origin.key" api-origin.key 0644
    place "$WORK/mcp-origin.crt" mcp-origin.crt 0644
    place "$WORK/mcp-origin.key" mcp-origin.key 0644
}

if [ "$CLIENT_ONLY" = 1 ]; then
    make_ca "Atlas install client CA ($(date -u +%Y-%m-%d))"
    sign mcp-client atlas-mcp clientAuth
    place "$WORK/ca.crt" client-ca.crt 0644
    place "$WORK/mcp-client.crt" mcp-client.crt 0644
    place "$WORK/mcp-client.key" mcp-client.key 0644
elif [ "$ORIGIN_ONLY" = 1 ]; then
    make_ca "Atlas install origin CA ($(date -u +%Y-%m-%d))"
    sign_origins
    place_origins
else
    make_ca "Atlas install CA ($(date -u +%Y-%m-%d))"
    sign server atlas-api serverAuth "$(san_list)"
    sign mcp-client atlas-mcp clientAuth
    sign_origins
    place "$WORK/ca.crt" ca.crt 0644
    place "$WORK/ca.crt" client-ca.crt 0644
    place "$WORK/server.crt" server.crt 0644
    place "$WORK/server.key" server.key 0600
    place "$WORK/mcp-client.crt" mcp-client.crt 0644
    place "$WORK/mcp-client.key" mcp-client.key 0644
    place_origins
fi

echo "init-certs: wrote ${FILES[*]} under $OUT (the CA key was discarded)"
if [ "$ORIGIN_ONLY" = 1 ]; then
    echo "init-certs: origin listeners: atlas-api-origin, atlas-mcp-origin, trusted by the ingress through $OUT/origin-ca.crt (a separate CA; clients keep trusting ca.crt)"
elif [ "$CLIENT_ONLY" = 0 ]; then
    if [ -n "$HOST_SOURCE" ]; then
        echo "init-certs: public name: ${HOSTS[*]} (from $HOST_SOURCE)"
    else
        echo "init-certs: public name: none (no --host, and no ATLAS_PUBLIC_HOST in ./.env); the certificate names only the internal services"
    fi
    echo "init-certs: server certificate names: $(san_list | sed 's/DNS://g; s/IP://g')"
    echo "init-certs: clients trust $OUT/ca.crt (a public certificate; safe to copy to each client)"
fi
