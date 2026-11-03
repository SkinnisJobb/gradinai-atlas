#!/usr/bin/env bash
# init-secrets.sh: every secret an Atlas OSS install needs, as files under ./secrets/ (OSS-10).
#
# Run once, from the directory holding the compose file (deploy/oss/), after `cp .env.example
# .env`. It writes, in ./secrets/ of the CURRENT directory and nowhere else:
#
#   db_password       the Postgres password (read by the postgres image's entrypoint)
#   db_url            the DSN atlas-api connects with, carrying that password
#   password_pepper   32 random bytes, base64: mixed into every password verifier
#   token_pepper      32 random bytes, base64: mixed into every service-token digest
#   tls/              when no server certificate is there yet, scripts/init-certs.sh makes a
#                     throwaway install CA and the certificates (see its header). When your own
#                     PKI's server.crt + server.key + ca.crt are already there, they are used as
#                     they are, and only atlas-mcp's client certificate and the two origin
#                     listeners' certificates (init-certs.sh --origin-only, OSS-26 D) are added
#                     if missing.
#
# Never prints a secret, never passes one on a command line or in the environment: openssl
# writes the random bytes, and the DSN is assembled by the shell builtin printf into the file.
# Refuses to overwrite any existing secret without --force, before writing anything.
#
#   --force        replace db_password, db_url and both peppers. ON AN INSTALL WITH DATA THIS
#                  BREAKS IT: the database keeps its first password, and new peppers invalidate
#                  every account and every token. It never touches ./secrets/tls/ (run
#                  init-certs.sh --force for that). To change the database password of an
#                  install, run rotate-db-password.sh instead (operations.md, section 4).
#   --host NAME    the public name for the certificate; repeatable. Default: ATLAS_PUBLIC_HOST
#                  from ./.env (a configuration file; nothing secret is read from it).
#
# Exit 0 on success, 1 on a refusal or failure, 2 on a usage error.

set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT=./secrets
FORCE=0
HOSTS=()

usage() { sed -n '2,31p' "$0"; }
die() { echo "init-secrets: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=1; shift ;;
        --host) [ $# -ge 2 ] || { usage >&2; exit 2; }; HOSTS+=("$2"); shift 2 ;;
        --host=*) HOSTS+=("${1#--host=}"); shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "init-secrets: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done
command -v openssl >/dev/null || die "openssl is not on PATH"

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git check-ignore -q "$OUT/.init-secrets-probe" || die "$OUT is inside a git work tree and NOT ignored; refusing to write secrets there"
fi

SECRETS=(db_password db_url password_pepper token_pepper)
if [ "$FORCE" = 0 ]; then
    existing=()
    for f in "${SECRETS[@]}"; do [ -e "$OUT/$f" ] && existing+=("$OUT/$f"); done
    [ ${#existing[@]} -eq 0 ] || die "refusing to overwrite ${existing[*]} (see --force in the header first)"
fi

# The public host, from .env when not given. Only that one line is looked at.
if [ ${#HOSTS[@]} -eq 0 ] && [ -f ./.env ]; then
    h=$(sed -n -E 's/^[[:space:]]*ATLAS_PUBLIC_HOST[[:space:]]*=[[:space:]]*"?([^"#[:space:]]*)"?.*$/\1/p' ./.env | tail -n 1)
    [ -n "$h" ] && HOSTS+=("$h")
fi

umask 077
mkdir -p "$OUT"
chmod 0700 "$OUT" 2>/dev/null || true
WORK=$(mktemp -d "$OUT/.init-secrets.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

openssl rand -hex 24 > "$WORK/db_password"
openssl rand -base64 32 > "$WORK/password_pepper"
openssl rand -base64 32 > "$WORK/token_pepper"
# The DSN, built by the shell builtin (no child process sees the password). Hex needs no
# URL-escaping.
IFS= read -r pw < "$WORK/db_password"
pw=${pw%$'\r'}   # a Windows openssl ends the line with CRLF
printf '%s\n' "$pw" > "$WORK/db_password"
printf 'postgresql+psycopg2://atlas:%s@atlas-postgres:5432/atlas\n' "$pw" > "$WORK/db_url"
unset pw

# All four are 0644 on purpose: none is read by root. The postgres image reads db_password
# AFTER dropping to its own user, and atlas-api / atlas-migrate run the hardened image
# (STORY-oss-18) as a non-root UID that may be ANY number (OpenShift assigns one), so neither
# can read a 0600 file owned by you. ./secrets/ itself is 0700, so nobody else on this host
# can reach them; inside a container each is a read-only bind mount of that one file.
chmod 0644 "$WORK/db_password" "$WORK/db_url" "$WORK/password_pepper" "$WORK/token_pepper"
for f in "${SECRETS[@]}"; do mv -f "$WORK/$f" "$OUT/$f"; done
echo "init-secrets: wrote ${SECRETS[*]} under $OUT"

TLS=$OUT/tls
have() { [ -e "$TLS/$1" ]; }
if have server.crt || have server.key || have ca.crt; then
    for f in server.crt server.key ca.crt; do have "$f" || die "$TLS/$f is missing: a PKI of your own needs server.crt, server.key and ca.crt (see .env.example, TLS)"; done
    echo "init-secrets: using the server certificate already in $TLS (your PKI)"
    if ! have mcp-client.crt && ! have mcp-client.key && ! have client-ca.crt; then
        bash "$HERE/init-certs.sh" --client-only
    fi
    if ! have origin-ca.crt && ! have api-origin.crt && ! have api-origin.key && ! have mcp-origin.crt && ! have mcp-origin.key; then
        bash "$HERE/init-certs.sh" --origin-only
    fi
else
    args=()
    for h in "${HOSTS[@]+"${HOSTS[@]}"}"; do args+=(--host "$h"); done
    bash "$HERE/init-certs.sh" "${args[@]+"${args[@]}"}"
fi
echo "init-secrets: done. Next (docs/oss/INSTALL.md, section 4): docker compose up -d --build --wait --wait-timeout 600"
