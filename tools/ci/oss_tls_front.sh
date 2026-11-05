#!/usr/bin/env bash
# oss_tls_front.sh: the OSS install's TLS front for atlas-api, run for real (OSS-24).
#
# Builds the repo's pinned Caddy image (deploy/caddy/Dockerfile), runs it with the OSS
# Caddyfile (deploy/oss/caddy/) and certificates from scripts/init-certs.sh, in front of a stub
# that stands in for atlas-api's own TLS listener (atlas-api-origin:8443, init-certs'
# api-origin.crt, OSS-26 D) on its own docker network, and proves:
#
#   off (default)  atlas-mcp's real client (mcp/app/client.py, ATLAS_CA_BUNDLE=ca.crt) gets a
#                  200 through https://atlas-api:9443, Bearer forwarded, CF-Connecting-IP set by
#                  the proxy (a forged one replaced); a certificate from another CA fails
#                  closed; plain http:// on the TLS port never reaches the upstream; the public
#                  door serves the API and routes /mcp elsewhere.
#   origin leg     (OSS-26 D) the proxy reaches the upstream over TLS (the stub sees a TLS
#                  connection), verified against /certs/origin-ca.crt: with another CA's
#                  origin-ca.crt mounted instead, nothing reaches the upstream (502); and the
#                  old plain port atlas-api-origin:8000 is closed.
#   health site    (TASK-1470) atlas-tls's compose healthcheck, run as the compose file writes
#                  it: it passes only while BOTH origins answer through the proxy (a second stub
#                  stands in for atlas-mcp-origin:8493); with that stub stopped, or another CA's
#                  origin-ca.crt, it fails. Its /healthz reaches the API with the public Host,
#                  and the loopback site is not reachable from another container.
#   require (mTLS) without a client certificate the handshake is refused (curl and atlas-mcp's
#                  client); with init-certs' mcp-client pair it is accepted; a client
#                  certificate from another CA is refused.
#
# Needs docker, curl, openssl, python with mcp/requirements.txt, and sudo to add `atlas-api`
# to /etc/hosts (CI's ubuntu runner has all of them). Every certificate is made at run time.
# Exit 0 when every check passed, 1 otherwise.

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
TMP=$(mktemp -d)
# Self-hosted runners share one Docker daemon, so two jobs can run this at once: every name
# carries the PID plus a random suffix (runners in separate PID namespaces can share a PID), and
# the host ports are ephemeral, read back from `docker port` by start_front, never fixed.
P=atlas-tlsfront-$$-$RANDOM
PUB=  # host port of the public door (container :8443), set by start_front
INT=  # host port of the internal door (container :9443), set by start_front
PY_IMAGE="python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54"  # = backend/Dockerfile
fail=0

cleanup() {
    docker rm -f "$P-tls" "$P-origin" "$P-mcp-origin" >/dev/null 2>&1 || true
    docker network rm "$P-origin" "$P-mcp" >/dev/null 2>&1 || true
    docker rmi "$P-caddy" >/dev/null 2>&1 || true  # per-run tag; the layers stay cached
    rm -rf "$TMP"
}
trap cleanup EXIT

ok()  { echo "[PASS] $*"; }
bad() { echo "[FAIL] $*"; fail=1; }

# --- certificates ---------------------------------------------------------------------------
mkdir -p "$TMP/install" "$TMP/other"
(cd "$TMP/install" && bash "$ROOT/scripts/init-certs.sh" --host localhost >/dev/null)
(cd "$TMP/other" && bash "$ROOT/scripts/init-certs.sh" >/dev/null)
PKI=$TMP/install/secrets/tls
OTHER=$TMP/other/secrets/tls

# --- the stub upstream, alone on the "origin" network ---------------------------------------
# It serves TLS only, on :8443, with init-certs' api-origin pair, as atlas-api does in the OSS
# compose (uvicorn --ssl-certfile/--ssl-keyfile); nothing listens on the old plain :8000.
STUB='
import json, os, ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        b = json.dumps({"stub": os.environ.get("STUB_NAME", "atlas-api-origin"), "path": self.path, "host": self.headers.get("Host"),
                        "bearer": (self.headers.get("Authorization") or "").startswith("Bearer "),
                        "cf_ip": self.headers.get("CF-Connecting-IP"),
                        "tls": getattr(self.connection, "version", lambda: None)()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
srv = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("STUB_PORT", "8443"))), H)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain("/certs/origin.crt", "/certs/origin.key")
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
srv.serve_forever()
'
docker network create --internal "$P-origin" >/dev/null
docker network create "$P-mcp" >/dev/null
docker run -d --name "$P-origin" --network "$P-origin" --network-alias atlas-api-origin \
    -v "$PKI/api-origin.crt:/certs/origin.crt:ro" -v "$PKI/api-origin.key:/certs/origin.key:ro" \
    "$PY_IMAGE" python -c "$STUB" >/dev/null
# The same stub as atlas-mcp's own TLS listener (:8493, mcp-origin.crt), for the health site.
docker run -d --name "$P-mcp-origin" --network "$P-origin" --network-alias atlas-mcp-origin \
    -e STUB_PORT=8493 -e STUB_NAME=atlas-mcp-origin \
    -v "$PKI/mcp-origin.crt:/certs/origin.crt:ro" -v "$PKI/mcp-origin.key:/certs/origin.key:ro" \
    "$PY_IMAGE" python -c "$STUB" >/dev/null

echo "building deploy/caddy/Dockerfile ..."
docker build -q -t "$P-caddy" "$ROOT/deploy/caddy" >/dev/null

host_port() {  # host_port <container port> -> the ephemeral 127.0.0.1 port docker gave $P-tls
    local hp
    hp=$(docker port "$P-tls" "$1/tcp" | grep -m1 '^127\.0\.0\.1:' || true)
    hp=${hp##*:}
    case "$hp" in ''|*[!0-9]*) echo "no host port for $P-tls:$1" >&2; exit 1 ;; esac
    echo "$hp"
}

start_front() {  # start_front <off|require> [origin CA file, default the install's]
    local origin_ca=${2:-$PKI/origin-ca.crt}
    docker rm -f "$P-tls" >/dev/null 2>&1 || true
    docker run -d --name "$P-tls" --network "$P-mcp" --network-alias atlas-api \
        -p "127.0.0.1::8443" -p "127.0.0.1::9443" \
        -e CADDY_PUBLIC_HOST=localhost -e CADDY_TLS_MODE=files -e CADDY_CLIENT_AUTH="$1" \
        -v "$ROOT/deploy/oss/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" \
        -v "$ROOT/deploy/oss/caddy:/etc/caddy/oss:ro" \
        -v "$ROOT/deploy/caddy/tls:/etc/caddy/tls:ro" \
        -v "$PKI/server.crt:/certs/tls.crt:ro" -v "$PKI/server.key:/certs/tls.key:ro" \
        -v "$PKI/client-ca.crt:/certs/client-ca.crt:ro" \
        -v "$origin_ca:/certs/origin-ca.crt:ro" \
        "$P-caddy" >/dev/null
    docker network connect "$P-origin" "$P-tls"
    PUB=$(host_port 8443)
    INT=$(host_port 9443)
    local i
    for i in $(seq 1 30); do
        if curl -s -o /dev/null --cacert "$PKI/ca.crt" --resolve "atlas-api:$INT:127.0.0.1" \
                "https://atlas-api:$INT/" --max-time 2; then return 0; fi
        # In require mode a certificate-less probe is refused; any TLS answer means it is up.
        if [ "$1" = require ] && curl -s -o /dev/null --cacert "$PKI/ca.crt" --cert "$PKI/mcp-client.crt" \
                --key "$PKI/mcp-client.key" --resolve "atlas-api:$INT:127.0.0.1" "https://atlas-api:$INT/" --max-time 2; then
            return 0
        fi
        sleep 1
    done
    docker logs "$P-tls" | tail -30
    echo "the TLS front did not come up" >&2
    exit 1
}

icurl() {  # curl to the internal door as atlas-mcp would address it; prints the body
    curl -sS --max-time 5 --resolve "atlas-api:$INT:127.0.0.1" "$@"
}

# The real atlas-mcp client, as the atlas-mcp container would run it.
grep -q ' atlas-api$' /etc/hosts || echo "127.0.0.1 atlas-api" | sudo tee -a /etc/hosts >/dev/null
mcp_probe() {  # mcp_probe [ENV=VAL ...] -> prints "OK <json>" or "ERR <type> <message>"
    (cd "$ROOT/mcp" && env -u ATLAS_CA_BUNDLE ATLAS_BASE_URL="https://atlas-api:$INT" "$@" python - <<'PY'
import json
from app import client as c
from app.envelope import AtlasError
with c.as_caller("atlas_svc_tlsfront_" + "b" * 32):
    try:
        print("OK " + json.dumps(c.client.get("/api/health")))
    except AtlasError as e:
        print("ERR " + e.error_type + " " + str(e))
PY
    )
}

# --- client auth off (the default) ----------------------------------------------------------
echo "-- CADDY_CLIENT_AUTH=off --"
start_front off

out=$(mcp_probe ATLAS_CA_BUNDLE="$PKI/ca.crt")
case "$out" in
    OK*'"stub": "atlas-api-origin"'*'"bearer": true'*) ok "atlas-mcp's client -> https://atlas-api:9443 with the install CA: 200, Bearer forwarded" ;;
    *) bad "atlas-mcp's client through the front: $out" ;;
esac
out=$(mcp_probe ATLAS_CA_BUNDLE="$OTHER/ca.crt")
case "$out" in
    "ERR unreachable"*CERTIFICATE_VERIFY_FAILED*) ok "a bundle of another CA: atlas-mcp fails closed (certificate verify failed)" ;;
    *) bad "another CA should fail closed: $out" ;;
esac
out=$(mcp_probe)
case "$out" in
    "ERR unreachable"*CERTIFICATE_VERIFY_FAILED*) ok "the system store does not trust the install CA (no silent fallback)" ;;
    *) bad "the system store should refuse the install CA: $out" ;;
esac

body=$(icurl --cacert "$PKI/ca.crt" -H "Authorization: Bearer x" -H "CF-Connecting-IP: 203.0.113.9" "https://atlas-api:$INT/api/health")
case "$body" in
    *'"cf_ip": "203.0.113.9"'*) bad "a client-sent CF-Connecting-IP reached the upstream: $body" ;;
    *'"cf_ip": "'*) ok "CF-Connecting-IP is set by the proxy, a forged one replaced" ;;
    *) bad "no CF-Connecting-IP at the upstream: $body" ;;
esac
case "$body" in *'"host": "atlas-api:'"$INT"'"'*) ok "the Host header reaches atlas-api unchanged" ;; *) bad "Host not forwarded: $body" ;; esac
case "$body" in
    *'"tls": "TLSv1.'[23]'"'*) ok "OSS-26 D: the proxy reached atlas-api-origin over TLS ($(grep -oE '"tls": "[^"]*"' <<<"$body"))" ;;
    *) bad "OSS-26 D: the leg to atlas-api-origin is not TLS: $body" ;;
esac

code=$(curl -s -o "$TMP/plain" -w '%{http_code}' --max-time 5 -H "Authorization: Bearer x" "http://127.0.0.1:$INT/api/health" || true)
if grep -q atlas-api-origin "$TMP/plain" 2>/dev/null; then
    bad "plain http:// to the TLS port reached the upstream (HTTP $code)"
else
    ok "plain http:// with a Bearer token on the TLS port never reaches atlas-api (HTTP $code from the proxy)"
fi

body=$(curl -sS --max-time 5 --cacert "$PKI/ca.crt" "https://localhost:$PUB/api/health")
case "$body" in *'"stub": "atlas-api-origin"'*'"tls": "TLSv1.'[23]'"'*) ok "the public door serves the API, over TLS to the upstream" ;; *) bad "public door: $body" ;; esac
if docker exec "$P-tls" nc -z -w 3 atlas-api-origin 8000 2>/dev/null; then
    bad "OSS-26 D: something answers on the old plain port atlas-api-origin:8000"
else
    ok "OSS-26 D: atlas-api-origin:8000 (the old plain port) is closed, seen from the proxy"
fi

# Browser security headers (deploy/oss/caddy/Caddyfile `security_headers`; values checked
# statically by tools/ci/check_oss_headers.py). This run's host is localhost, so no HSTS.
hdr() { curl -sS -o /dev/null -D - --max-time 5 --cacert "$PKI/ca.crt" "https://localhost:$PUB$1" | tr -d '\r'; }
h=$(hdr /board)
for want in "content-security-policy: default-src 'self'; script-src 'self';" "x-frame-options: DENY" \
            "x-content-type-options: nosniff" "referrer-policy: same-origin"; do
    if grep -qiF "$want" <<<"$h"; then ok "public door /board sends ${want%%:*}"; else bad "public door /board lacks: $want"; fi
done
if grep -qi '^strict-transport-security:' <<<"$h"; then bad "HSTS sent for localhost"
else ok "no HSTS for localhost (it would pin every local dev server in that browser)"; fi
hl=$(hdr /board/legacy)
if grep -qi "^content-security-policy: .*script-src 'self' 'sha256-" <<<"$hl"; then
    ok "/board/legacy gets the hash-pinned CSP"
else bad "/board/legacy does not get the hash-pinned CSP"; fi
code=$(curl -s -o "$TMP/mcp" -w '%{http_code}' --max-time 5 --cacert "$PKI/ca.crt" "https://localhost:$PUB/mcp/" || true)
if grep -q atlas-api-origin "$TMP/mcp" 2>/dev/null; then bad "/mcp/ was sent to the API upstream"
else ok "/mcp/ on the public door is routed to atlas-mcp, not the API (HTTP $code)"; fi

# --- the health site: atlas-tls's compose healthcheck, through to both origins (TASK-1470) ----
# The command is read from the compose file, so this runs what the stack runs.
HC=$(grep -oE 'wget -q [^"]*healthz/mcp' "$ROOT/deploy/oss/docker-compose.yml" | head -1 || true)
[ -n "$HC" ] || bad "TASK-1470: no atlas-tls wget healthcheck in deploy/oss/docker-compose.yml"
hc() { [ -n "$HC" ] && docker exec "$P-tls" sh -c "$HC" >/dev/null 2>&1; }
if hc; then ok "TASK-1470: the healthcheck passes with both origins answering through the proxy"
else bad "TASK-1470: the healthcheck fails with both origins up ($HC)"; fi
body=$(docker exec "$P-tls" wget -q -T 4 -O - http://127.0.0.1:8081/healthz 2>/dev/null || true)
case "$body" in
    *'"stub": "atlas-api-origin"'*'"path": "/healthz"'*'"host": "localhost"'*'"tls": "TLSv1.'[23]'"'*)
        ok "TASK-1470: the health site's /healthz reaches atlas-api-origin over TLS, with the public Host" ;;
    *) bad "TASK-1470: the health site's /healthz: $body" ;;
esac
body=$(docker exec "$P-tls" wget -q -T 4 -O - http://127.0.0.1:8081/healthz/mcp 2>/dev/null || true)
case "$body" in
    *'"stub": "atlas-mcp-origin"'*'"path": "/health"'*'"tls": "TLSv1.'[23]'"'*)
        ok "TASK-1470: the health site's /healthz/mcp reaches atlas-mcp-origin's /health over TLS" ;;
    *) bad "TASK-1470: the health site's /healthz/mcp: $body" ;;
esac
if docker exec "$P-tls" wget -q -T 4 -O /dev/null http://127.0.0.1:8081/api/health 2>/dev/null; then
    bad "TASK-1470: the health site answers a path other than its two"
else ok "TASK-1470: any other path on the health site is a 404"; fi
# The control first: the same container, from the same peer, does answer on its TLS port.
reach() { docker exec "$P-origin" python -c "import socket; socket.create_connection(('$P-tls', $1), 3)" 2>/dev/null; }
if ! reach 8443; then
    bad "TASK-1470: the peer cannot reach $P-tls:8443 either, so the loopback check proves nothing"
elif reach 8081; then
    bad "TASK-1470: the health site is reachable from another container"
else ok "TASK-1470: the health site is loopback only (a peer reaches :8443, not :8081)"; fi
docker stop "$P-mcp-origin" >/dev/null
if hc; then bad "TASK-1470: the healthcheck passes with atlas-mcp-origin down"
else ok "TASK-1470: with atlas-mcp-origin down the healthcheck fails (a dead leg is an unhealthy atlas-tls)"; fi
docker start "$P-mcp-origin" >/dev/null

# --- the origin leg fails closed on another CA (OSS-26 D) ------------------------------------
echo "-- origin-ca.crt of another CA --"
start_front off "$OTHER/origin-ca.crt"
if hc; then bad "TASK-1470: the healthcheck passes while the proxy refuses the origins' certificates"
else ok "TASK-1470: with another CA's origin-ca.crt the healthcheck fails"; fi
code=$(curl -s -o "$TMP/wrongca" -w '%{http_code}' --max-time 5 --cacert "$PKI/ca.crt" -H "Authorization: Bearer x" \
       "https://localhost:$PUB/api/health" || true)
if grep -q atlas-api-origin "$TMP/wrongca" 2>/dev/null; then
    bad "OSS-26 D: with another CA's origin-ca.crt the request still reached the upstream (HTTP $code)"
elif [ "$code" = 502 ]; then
    ok "OSS-26 D: with another CA's origin-ca.crt the proxy refuses the upstream's certificate (HTTP 502, nothing forwarded)"
else
    bad "OSS-26 D: with another CA's origin-ca.crt, HTTP $code (want 502)"
fi
if docker logs "$P-tls" 2>&1 | grep -qiE 'x509|certificate signed by unknown authority|tls: '; then
    ok "OSS-26 D: Caddy logged the verification failure ($(docker logs "$P-tls" 2>&1 | grep -oiE 'x509: [^"]{0,60}|certificate signed by unknown authority' | head -1))"
else
    bad "OSS-26 D: no certificate error in Caddy's log"
fi

# --- client auth require (the mTLS leg) -----------------------------------------------------
echo "-- CADDY_CLIENT_AUTH=require --"
start_front require

if icurl -o /dev/null --cacert "$PKI/ca.crt" "https://atlas-api:$INT/api/health" 2>/dev/null; then
    bad "mTLS: a connection without a client certificate was accepted"
else
    ok "mTLS: curl without a client certificate is refused in the handshake"
fi
body=$(icurl --cacert "$PKI/ca.crt" --cert "$PKI/mcp-client.crt" --key "$PKI/mcp-client.key" "https://atlas-api:$INT/api/health" || true)
case "$body" in *atlas-api-origin*) ok "mTLS: curl with init-certs' mcp-client certificate is accepted" ;; *) bad "mTLS with the client cert: $body" ;; esac
if icurl -o /dev/null --cacert "$PKI/ca.crt" --cert "$OTHER/mcp-client.crt" --key "$OTHER/mcp-client.key" "https://atlas-api:$INT/api/health" 2>/dev/null; then
    bad "mTLS: a client certificate from another CA was accepted"
else
    ok "mTLS: a client certificate from another CA is refused"
fi
out=$(mcp_probe ATLAS_CA_BUNDLE="$PKI/ca.crt")
case "$out" in "ERR unreachable"*) ok "mTLS: atlas-mcp without ATLAS_MCP_CLIENT_CERT_FILE is refused" ;; *) bad "mTLS without a client cert: $out" ;; esac
out=$(mcp_probe ATLAS_CA_BUNDLE="$PKI/ca.crt" ATLAS_MCP_CLIENT_CERT_FILE="$PKI/mcp-client.crt" ATLAS_MCP_CLIENT_KEY_FILE="$PKI/mcp-client.key")
case "$out" in OK*'"bearer": true'*) ok "mTLS: atlas-mcp with its client certificate is accepted, Bearer forwarded" ;; *) bad "mTLS with the client cert: $out" ;; esac

if [ "$fail" = 0 ]; then echo "oss_tls_front: all checks passed"; else echo "oss_tls_front: FAILED"; exit 1; fi
