#!/usr/bin/env bash
# oss_install_tls.sh: the TLS leg of the install smoke (STORY-oss-17, OSS-24 criterion 6).
#
# Runs against the LIVE OSS stack (deploy/oss/, `docker compose up` done), from that directory,
# and proves on the running install what tools/ci/oss_tls_front.sh proves on a stub:
#
#   T1 client -> ingress   https://$SMOKE_HOST verifies against the install CA (curl --cacert,
#                          openssl s_client); the system trust store refuses it (no silent
#                          fallback to an unverified hop).
#   T2 atlas-mcp -> API    from INSIDE atlas-mcp, with its own CA bundle (/run/secrets/atlas_ca):
#                          the TLS handshake to atlas-api:9443 verifies, and an authenticated
#                          GET /api/me through it is 200; ATLAS_BASE_URL is https://; the API's
#                          plain listener is unreachable from there (no name, no route); plain
#                          http:// to :9443 is answered by the proxy, never by the API.
#   T3 published ports     every port any container of the project publishes is probed with
#                          plain HTTP and a Bearer header; it fails if one answers as the API or
#                          the MCP would. The only published port must be the TLS ingress.
#   T4 static rules        tools/ci/check_tls.sh over the tracked tree, and its rules over the
#                          RENDERED compose config (`docker compose config`) and Caddy's adapted
#                          JSON config: listeners exactly :8443, :9443, :9444 and the healthcheck's
#                          127.0.0.1:8081 site, which must be that site and nothing else (TASK-1470).
#   T5 the gateway hop     (STORY-oss-15, the `gateway` profile; run with COMPOSE_PROFILES=gateway
#                          and atlas-gateway up) client -> gateway: https on ATLAS_GATEWAY_PORT
#                          verifies against the install CA, the system store refuses it, plain
#                          HTTP there gets no MCP answer. gateway -> atlas-mcp: the rendered
#                          gateway config passes check_tls.sh R1 + R4 and names an https://
#                          upstream verified against /certs/ca.crt, which IS the install CA; the
#                          hop's certificate (atlas-tls :9444) verifies as `atlas-mcp` with that
#                          CA; plain HTTP to :9444 is refused by the proxy; the gateway shares no
#                          network with atlas-mcp or atlas-api.
#   T6 the ingress's legs  (OSS-26 D) atlas-tls -> atlas-api and atlas-tls -> atlas-mcp are TLS
#                          against the install CA: Caddy's adapted config dials exactly
#                          https://atlas-api-origin:8443 and https://atlas-mcp-origin:8493, each
#                          with a TLS transport verified against /certs/origin-ca.crt under that
#                          name, and that file IS the install CA, byte for byte; each listener's
#                          certificate verifies as its name with the install CA (from inside the
#                          service, over the compose network); plain HTTP to each TLS port gets no
#                          HTTP answer; the old plain ports (atlas-api-origin:8000,
#                          atlas-mcp-origin:8092) are closed, from the services and from atlas-tls;
#                          and a request through the public door reaches each (so the TLS leg is
#                          the one in use).
#
# Env: SMOKE_HOST, SMOKE_CA (absolute), SMOKE_DIR (the agent token for T2 is read from
# $SMOKE_DIR/alice.token and passed on stdin, never on a command line); ATLAS_GATEWAY_PORT
# (default 8444) for T5. Exit 0 all passed.

set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
: "${SMOKE_HOST:?}" "${SMOKE_CA:?}" "${SMOKE_DIR:?}"
PORT=${ATLAS_TLS_PORT:-443}
fail=0
ok()  { echo "[PASS] $*"; }
bad() { echo "[FAIL] $*"; fail=1; }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

echo "-- T1 client -> ingress --"
if out=$(curl -sS --max-time 10 --cacert "$SMOKE_CA" -o "$TMP/h" -w '%{http_code} %{ssl_verify_result}' "https://$SMOKE_HOST:$PORT/healthz"); then
    case "$out" in "200 0") ok "curl --cacert ca.crt https://$SMOKE_HOST/healthz: HTTP 200, certificate verified" ;; *) bad "ingress /healthz: $out" ;; esac
else
    bad "curl --cacert to the ingress failed"
fi
rc=0; curl -sS --max-time 10 -o /dev/null "https://$SMOKE_HOST:$PORT/healthz" 2>"$TMP/e" || rc=$?
if [ "$rc" = 60 ]; then ok "without the install CA the certificate is refused (curl exit 60: $(head -c 120 "$TMP/e"))"
else bad "the system store should refuse the install CA (curl exit $rc)"; fi
s_out=$(openssl s_client -connect "127.0.0.1:$PORT" -servername "$SMOKE_HOST" -CAfile "$SMOKE_CA" \
        -verify_hostname "$SMOKE_HOST" -verify_return_error </dev/null 2>&1)
if grep -q 'Verify return code: 0 (ok)' <<<"$s_out"; then
    ok "openssl s_client: $(grep -m1 -E '^ *Protocol *:|New, TLS' <<<"$s_out" | xargs), $(grep -m1 'subject=' <<<"$s_out"), verify 0 (ok)"
else
    bad "openssl s_client to the ingress did not verify: $(grep -m1 'Verify return code' <<<"$s_out")"
fi

echo "-- T2 atlas-mcp -> atlas-api --"
base=$(docker compose exec -T atlas-mcp printenv ATLAS_BASE_URL | tr -d '\r')
case "$base" in https://atlas-api:9443) ok "atlas-mcp ATLAS_BASE_URL=$base" ;; *) bad "atlas-mcp ATLAS_BASE_URL=$base" ;; esac
probe=$(docker compose exec -T atlas-mcp python - 2>&1 <<'PY'
import socket, ssl
ctx = ssl.create_default_context(cafile="/run/secrets/atlas_ca")
with socket.create_connection(("atlas-api", 9443), timeout=5) as raw:
    with ctx.wrap_socket(raw, server_hostname="atlas-api") as s:
        subj = dict(x[0] for x in s.getpeercert()["subject"])
        print("HANDSHAKE", s.version(), "subject CN=" + subj.get("commonName", "?"), "verified against /run/secrets/atlas_ca")
try:
    socket.getaddrinfo("atlas-api-origin", 8000)
    print("ORIGIN_RESOLVES")
except OSError as e:
    print("ORIGIN_UNRESOLVABLE", type(e).__name__)
try:
    socket.create_connection(("atlas-api", 8000), timeout=3).close()
    print("PLAIN8000_OPEN")
except OSError as e:
    print("PLAIN8000_CLOSED", type(e).__name__)
PY
)
echo "$probe" | sed 's/^/       /'
grep -q '^HANDSHAKE TLSv1\.[23] ' <<<"$probe" && ok "atlas-mcp -> atlas-api:9443: TLS handshake verified with the install CA" || bad "atlas-mcp -> atlas-api:9443 handshake"
grep -q '^ORIGIN_UNRESOLVABLE' <<<"$probe" && ok "atlas-mcp cannot even resolve the API's plain listener (atlas-api-origin)" || bad "atlas-mcp resolves atlas-api-origin"
grep -q '^PLAIN8000_CLOSED' <<<"$probe" && ok "atlas-mcp has no plain-HTTP route to :8000" || bad "atlas-mcp reaches a plain :8000"

# An authenticated call over that hop, as atlas-mcp's client makes it. The token arrives on
# stdin and goes only into the Authorization header.
authed=$(docker compose exec -T atlas-mcp python -c '
import sys, ssl, http.client
tok = sys.stdin.readline().strip()
ctx = ssl.create_default_context(cafile="/run/secrets/atlas_ca")
c = http.client.HTTPSConnection("atlas-api", 9443, context=ctx, timeout=10)
c.request("GET", "/api/me", headers={"Authorization": "Bearer " + tok})
r = c.getresponse(); body = r.read()
print("STATUS", r.status, "is_service" in body.decode("utf-8", "replace"))
c2 = http.client.HTTPConnection("atlas-api", 9443, timeout=10)
c2.request("GET", "/api/me", headers={"Authorization": "Bearer " + tok})
r2 = c2.getresponse(); b2 = r2.read().decode("utf-8", "replace")
print("PLAIN", r2.status, "API" if "is_service" in b2 or "unauthorized" in b2 else "PROXY", b2[:80].replace("\n", " "))
' < "$SMOKE_DIR/alice.token" 2>&1)
echo "$authed" | sed 's/^/       /'
grep -q '^STATUS 200 True' <<<"$authed" && ok "GET https://atlas-api:9443/api/me with alice's agent token: 200 (Bearer admitted on the internal host, over TLS)" || bad "authenticated call over atlas-mcp's hop"
grep -q '^PLAIN 400 PROXY' <<<"$authed" && ok "plain http:// to :9443 is refused by the proxy (400), never reaches the API" || bad "plain http to :9443: $(grep '^PLAIN' <<<"$authed")"

echo "-- T3 every published port, plain HTTP --"
pubs=$(docker compose ps --format json | python3 -c '
import json, sys
rows = [json.loads(l) for l in sys.stdin if l.strip()]
if rows and isinstance(rows[0], list): rows = rows[0]
for r in rows:
    for p in r.get("Publishers") or []:
        if p.get("PublishedPort"):
            print(r["Service"], p.get("URL") or "0.0.0.0", p["PublishedPort"], p["TargetPort"], p.get("Protocol"))
' | sort -u)
echo "$pubs" | sed 's/^/       published: /'
n=0
while read -r svc url pport tport proto; do
    [ -n "$svc" ] || continue
    n=$((n + 1))
    case "$svc:$tport" in
        atlas-tls:8443|atlas-gateway:8443) ;;
        *) bad "$svc publishes $url:$pport -> $tport (only atlas-tls :8443 and atlas-gateway :8443 may publish)" ;;
    esac
    for path in /api/me /mcp/ /healthz; do
        code=$(curl -s --max-time 5 -o "$TMP/b" -w '%{http_code}' -H "Authorization: Bearer atlas_svc_${RANDOM}x" "http://127.0.0.1:$pport$path" || true)
        if grep -qiE '"(error|ok|jsonrpc)"' "$TMP/b" 2>/dev/null; then
            bad "plain http://127.0.0.1:$pport$path answered as the application (HTTP $code)"
        else
            ok "plain http://127.0.0.1:$pport$path -> HTTP $code from the TLS listener ($(head -c 60 "$TMP/b" | tr '\n' ' '))"
        fi
    done
done <<<"$pubs"
[ "$n" -ge 1 ] || bad "no published port found (the probe would pass vacuously)"
listen=$(ss -ltnH 2>/dev/null | awk '{print $4}' | grep -E ':(8000|8092|9443|5432)$' || true)
[ -z "$listen" ] && ok "nothing on the host listens on 8000, 8092, 9443 or 5432" || bad "host listens on: $listen"

echo "-- T4 static TLS rules: the tree, the rendered compose config, Caddy's adapted config --"
(cd "$ROOT" && bash tools/ci/check_tls.sh >"$TMP/tree.log" 2>&1) && ok "tools/ci/check_tls.sh over the tracked tree: $(tail -1 "$TMP/tree.log")" \
    || { cat "$TMP/tree.log"; bad "tools/ci/check_tls.sh over the tree"; }
mkdir -p "$TMP/deploy/oss"
docker compose config > "$TMP/deploy/oss/docker-compose.rendered.yml"
# `config` renders ports in the long syntax, which check_tls.sh's port reader (check_binds.sh's
# extract) does not parse; restate each rendered publish in the short form it reads.
docker compose config --format json | python3 -c '
import json, sys
c = json.load(sys.stdin)
print("services:")
for name, s in c["services"].items():
    ports = s.get("ports") or []
    if not ports: continue
    print(f"  {name}:\n    ports:")
    for p in ports:
        entry = "%s:%s:%s" % (p.get("host_ip") or "0.0.0.0", p.get("published"), p["target"])
        print("      - " + json.dumps(entry))
' > "$TMP/deploy/oss/ports.rendered.yml"
sed 's/^/       /' "$TMP/deploy/oss/ports.rendered.yml"
if (cd "$TMP" && . "$ROOT/tools/ci/check_tls.sh" && r=deploy/oss/docker-compose.rendered.yml p=deploy/oss/ports.rendered.yml \
        && check_refs "$r" && check_insecure "$r" && check_publishes "$p" && check_oss_publishes "$p") >"$TMP/r.log" 2>&1; then
    ok "check_tls.sh R1 + R4 over the rendered compose config, R2 + R5 over its rendered publishes"
else
    cat "$TMP/r.log"; bad "check_tls.sh over the rendered compose config"
fi
docker compose exec -T atlas-tls caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile > "$TMP/caddy.json" 2>"$TMP/caddy.err" \
    || { cat "$TMP/caddy.err"; bad "caddy adapt"; }
caddy_sum=$(python3 - "$TMP/caddy.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
srv = cfg["apps"]["http"]["servers"]
dials, listens, unverified = set(), set(), 0
def walk(o):
    global unverified
    if isinstance(o, dict):
        if "dial" in o: dials.add(o["dial"])
        if o.get("insecure_skip_verify"): unverified += 1
        for v in o.values(): walk(v)
    elif isinstance(o, list):
        for v in o: walk(v)
for s in srv.values():
    listens.update(s.get("listen", []))
walk(cfg)
# OSS-26 D: every reverse_proxy's transport is TLS, verified against the origin CA file under
# the name it dials. Each one is listed as dial|server_name|ca-file-present.
legs = set()
def proxies(o):
    if isinstance(o, dict):
        if o.get("handler") == "reverse_proxy":
            tr = o.get("transport") or {}
            tls = tr.get("tls")
            for u in o.get("upstreams") or []:
                legs.add("%s|%s|%s" % (u.get("dial"), (tls or {}).get("server_name", "-") if tls is not None else "PLAIN",
                                        "origin-ca" if "/certs/origin-ca.crt" in json.dumps(tls or {}) else "no-ca"))
        for v in o.values(): proxies(v)
    elif isinstance(o, list):
        for v in o: proxies(v)
proxies(cfg)
# TASK-1470: the healthcheck's loopback site (the last block of deploy/oss/caddy/Caddyfile) is the
# ONE plain-HTTP listener allowed, and only in exactly this shape: its own server, listening on
# 127.0.0.1:8081 and nothing else, path matchers exactly /healthz and /healthz/mcp, a proxy only
# under one of those paths and only to the two origins, and otherwise nothing but a 404.
HEALTH_LISTEN, HEALTH_PATHS = "127.0.0.1:8081", {"/healthz", "/healthz/mcp"}
HEALTH_DIALS = {"atlas-api-origin:8443", "atlas-mcp-origin:8493"}
HEALTH_HANDLERS = {"subroute", "reverse_proxy", "rewrite", "static_response"}
def health_site():
    on = [s for s in srv.values() if HEALTH_LISTEN in s.get("listen", [])]
    if not on:
        return "absent"
    if len(on) != 1 or on[0].get("listen") != [HEALTH_LISTEN]:
        return "shares-a-listener"
    paths, problems = set(), []
    def visit(o, under_path):
        if isinstance(o, dict):
            here = under_path
            for m in o.get("match") or []:
                for k, v in m.items():
                    if k == "path":
                        paths.update(v); here = True
                    elif k == "host":
                        if v != ["127.0.0.1"]: problems.append("host=" + ",".join(v))
                    else:
                        problems.append("matcher=" + k)
            h = o.get("handler")
            if h is not None:
                if h not in HEALTH_HANDLERS:
                    problems.append("handler=" + h)
                if h == "reverse_proxy":
                    ds = {u.get("dial") for u in o.get("upstreams") or []}
                    if not here or not ds or not ds <= HEALTH_DIALS:
                        problems.append("proxy=" + ",".join(sorted(map(str, ds))))
                if h == "static_response" and str(o.get("status_code")) != "404":
                    problems.append("response=%s" % o.get("status_code"))
            for k, v in o.items():
                if k != "match": visit(v, here)
        elif isinstance(o, list):
            for v in o: visit(v, under_path)
    visit(on[0].get("routes") or [], False)
    if paths != HEALTH_PATHS:
        problems.append("paths=" + ",".join(sorted(paths)))
    return "ok" if not problems else ";".join(problems)
print("listen=" + ",".join(sorted(listens)), "upstreams=" + ",".join(sorted(dials)), "unverified_upstreams=%d" % unverified,
      "health_site=" + health_site(), "legs=" + ",".join(sorted(legs)))
PY
)
echo "       caddy: $caddy_sum"
case "$caddy_sum" in
    *unverified_upstreams=0*) ok "Caddy: no upstream with TLS verification off" ;; *) bad "Caddy config: $caddy_sum" ;;
esac
case "$caddy_sum" in
    *"listen=127.0.0.1:8081,:8443,:9443,:9444 "*) ok "Caddy listens on :8443, :9443 and :9444 (TLS) and 127.0.0.1:8081 only (the loopback health site)" ;; *) bad "Caddy listeners: $caddy_sum" ;;
esac
case "$caddy_sum" in
    *" health_site=ok "*) ok "TASK-1470: 127.0.0.1:8081 is the health site alone: loopback, /healthz and /healthz/mcp to the two origins, else 404" ;;
    *) bad "TASK-1470: the plain-HTTP listener on 127.0.0.1:8081 is not exactly the health site: $caddy_sum" ;;
esac

echo "-- T6 the ingress's own legs: atlas-tls -> atlas-api, atlas-tls -> atlas-mcp (OSS-26 D) --"
case "$caddy_sum" in
    *" upstreams=atlas-api-origin:8443,atlas-mcp-origin:8493 "*) ok "Caddy's upstreams are atlas-api-origin:8443 and atlas-mcp-origin:8493 only (no :8000, no :8092)" ;;
    *) bad "Caddy's upstreams: $caddy_sum" ;;
esac
case "$caddy_sum" in
    *" legs=atlas-api-origin:8443|atlas-api-origin|origin-ca,atlas-mcp-origin:8493|atlas-mcp-origin|origin-ca") \
        ok "every reverse_proxy is TLS, verified against /certs/origin-ca.crt as the name it dials" ;;
    *) bad "a reverse_proxy leg is not TLS verified against the origin CA: $caddy_sum" ;;
esac
tls_cid=$(docker compose ps -q atlas-tls)
docker cp "$tls_cid:/certs/origin-ca.crt" "$TMP/origin-ca.crt" >/dev/null 2>&1 || true
if [ -s "$TMP/origin-ca.crt" ] && cmp -s "$TMP/origin-ca.crt" "$SMOKE_CA"; then
    ok "atlas-tls's /certs/origin-ca.crt is the install CA, byte for byte"
else
    bad "atlas-tls's /certs/origin-ca.crt is not the install CA"
fi
for leg in "atlas-api atlas-api-origin 8443 8000 /healthz" "atlas-mcp atlas-mcp-origin 8493 8092 /health"; do
    read -r svc name port old path <<<"$leg"
    # The probe verifies with the INSTALL CA (its PEM, a public certificate, passed as an
    # argument), not with the file the service mounts.
    out=$(docker compose exec -T "$svc" python - "$name" "$port" "$old" "$path" "$(cat "$SMOKE_CA")" 2>&1 <<'PY'
import socket, ssl, sys
name, port, old, path = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
ctx = ssl.create_default_context(cadata=sys.argv[5])
with socket.create_connection((name, port), timeout=5) as raw:
    with ctx.wrap_socket(raw, server_hostname=name) as s:
        subj = dict(x[0] for x in s.getpeercert()["subject"])
        print("HANDSHAKE", s.version(), "CN=" + subj.get("commonName", "?"))
        s.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\n\r\n" % (path, name, port)).encode())
        print("TLS_ANSWER", s.recv(12).decode("latin-1").split(" ")[0])
try:
    with socket.create_connection((name, port), timeout=5) as raw:
        raw.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\n\r\n" % (path, name)).encode())
        raw.settimeout(5)
        try:
            first = raw.recv(12)
        except OSError:
            first = b""
    print("PLAIN_ON_TLS_PORT", "HTTP" if first.startswith(b"HTTP/") else "NO_HTTP", repr(first[:5]))
except OSError as e:
    print("PLAIN_ON_TLS_PORT NO_HTTP", type(e).__name__)
try:
    socket.create_connection((name, old), timeout=3).close()
    print("OLD_PORT_OPEN", old)
except OSError as e:
    print("OLD_PORT_CLOSED", old, type(e).__name__)
PY
)
    echo "$out" | sed 's/^/       /'
    grep -q "^HANDSHAKE TLSv1\.[23] CN=$name\$" <<<"$out" && ok "$svc: $name:$port presents a certificate valid for $name under the install CA" \
        || bad "$svc: $name:$port does not verify as $name with the install CA"
    grep -q '^TLS_ANSWER HTTP/1.1$' <<<"$out" && ok "$svc: GET $path over TLS on $name:$port answers HTTP" || bad "$svc: no HTTP answer over TLS on $name:$port"
    grep -q '^PLAIN_ON_TLS_PORT NO_HTTP' <<<"$out" && ok "$svc: plain HTTP to $name:$port gets no HTTP answer" || bad "$svc: plain HTTP to $name:$port was answered"
    grep -q "^OLD_PORT_CLOSED $old " <<<"$out" && ok "$svc: the old plain port $name:$old is closed" || bad "$svc: $name:$old still answers"
    if docker compose exec -T atlas-tls nc -z -w 3 "$name" "$old" >/dev/null 2>&1; then
        bad "atlas-tls reaches a plain $name:$old"
    else
        ok "atlas-tls: nothing listens on $name:$old"
    fi
    docker compose exec -T atlas-tls nc -z -w 3 "$name" "$port" >/dev/null 2>&1 \
        && ok "atlas-tls reaches $name:$port (the TLS leg)" || bad "atlas-tls cannot reach $name:$port"
done
# Through the public door, each leg in use: the API's /healthz and atlas-mcp's 401 challenge on
# /mcp/ (only atlas-mcp answers that path with a Bearer challenge).
code=$(curl -s --max-time 10 --cacert "$SMOKE_CA" -o /dev/null -w '%{http_code}' "https://$SMOKE_HOST:$PORT/healthz" || true)
[ "$code" = 200 ] && ok "public door -> atlas-api over the TLS leg: /healthz 200" || bad "public door /healthz: HTTP $code"
hdr=$(curl -s --max-time 10 --cacert "$SMOKE_CA" -o /dev/null -D - -X POST -H 'Content-Type: application/json' \
      -H 'Accept: application/json, text/event-stream' --data '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
      "https://$SMOKE_HOST:$PORT/mcp/" | tr -d '\r' || true)
if grep -qiE '^HTTP/[0-9.]+ 401' <<<"$hdr" && grep -qi '^www-authenticate: Bearer' <<<"$hdr"; then
    ok "public door -> atlas-mcp over the TLS leg: /mcp/ without a token is atlas-mcp's 401 Bearer challenge"
else
    bad "public door /mcp/ did not reach atlas-mcp: $(head -c 200 <<<"$hdr" | tr '\n' ' ')"
fi

echo "-- T5 the gateway hop: client -> atlas-gateway -> atlas-tls :9444 -> atlas-mcp --"
GW=${ATLAS_GATEWAY_PORT:-8444}
gw_cid=$(docker compose ps -q atlas-gateway 2>/dev/null || true)
if [ -z "$gw_cid" ]; then
    bad "atlas-gateway is not running (run this leg with COMPOSE_PROFILES=gateway after the gateway leg)"
else
    if out=$(curl -sS --max-time 10 --cacert "$SMOKE_CA" -o /dev/null -w '%{http_code} %{ssl_verify_result}' "https://$SMOKE_HOST:$GW/mcp"); then
        case "$out" in *" 0") ok "curl --cacert ca.crt https://$SMOKE_HOST:$GW/mcp: HTTP ${out% *}, certificate verified" ;; *) bad "gateway door: $out" ;; esac
    else
        bad "curl --cacert to the gateway failed"
    fi
    rc=0; curl -sS --max-time 10 -o /dev/null "https://$SMOKE_HOST:$GW/mcp" 2>"$TMP/e" || rc=$?
    if [ "$rc" = 60 ]; then ok "gateway: without the install CA the certificate is refused (curl exit 60)"
    else bad "gateway: the system store should refuse the install CA (curl exit $rc)"; fi
    s_out=$(openssl s_client -connect "127.0.0.1:$GW" -servername "$SMOKE_HOST" -CAfile "$SMOKE_CA" \
            -verify_hostname "$SMOKE_HOST" -verify_return_error </dev/null 2>&1)
    if grep -q 'Verify return code: 0 (ok)' <<<"$s_out"; then
        ok "gateway openssl s_client: $(grep -m1 -E '^ *Protocol *:|New, TLS' <<<"$s_out" | xargs), verify 0 (ok)"
    else
        bad "gateway openssl s_client did not verify: $(grep -m1 'Verify return code' <<<"$s_out")"
    fi
    rc=0; code=$(curl -sS --max-time 5 -o "$TMP/gp" -w '%{http_code}' -H "Authorization: Bearer atlas_svc_${RANDOM}x" \
                 -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
                 --data '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' "http://127.0.0.1:$GW/mcp" 2>"$TMP/ge") || rc=$?
    if [ "$rc" != 0 ] || ! grep -qiE '"(jsonrpc|result|tools)"' "$TMP/gp" 2>/dev/null; then
        ok "plain http://127.0.0.1:$GW/mcp is refused (curl exit $rc, HTTP ${code:-none}: $(head -c 80 "$TMP/ge" "$TMP/gp" 2>/dev/null | tr '\n' ' '))"
    else
        bad "plain HTTP to the gateway port got an MCP answer"
    fi

    gw_cfg=deploy/oss/gateway/agentgateway.yaml
    if (cd "$ROOT" && . tools/ci/check_tls.sh && check_refs "$gw_cfg" && check_insecure "$gw_cfg") >"$TMP/g.log" 2>&1; then
        ok "check_tls.sh R1 + R4 over the rendered gateway config ($gw_cfg)"
    else
        cat "$TMP/g.log"; bad "check_tls.sh over the rendered gateway config"
    fi
    g_sum=$(python3 - "$ROOT/$gw_cfg" <<'PY'
import re, sys
t = open(sys.argv[1], encoding="utf-8").read()
hosts = re.findall(r'^\s*host:\s*"([^"]+)"', t, re.M)
print("listener=" + ("HTTPS" if re.search(r"^\s*protocol: HTTPS$", t, re.M) else "?"),
      "upstreams=" + ",".join(hosts),
      "roots=" + ",".join(sorted(set(re.findall(r'^\s*root:\s*"([^"]+)"', t, re.M)))),
      "cors=" + ",".join(re.findall(r'^\s*allowOrigins:\s*\[(.*)\]', t, re.M)),
      "prefix=" + ",".join(re.findall(r"^\s*prefixMode:\s*(\S+)", t, re.M)))
PY
)
    echo "       gateway: $g_sum"
    case "$g_sum" in
        "listener=HTTPS upstreams=https://atlas-mcp:9444/mcp/ roots=/certs/ca.crt "*) ok "gateway: HTTPS listener, upstream https://atlas-mcp:9444 verified against /certs/ca.crt" ;;
        *) bad "gateway config: $g_sum" ;;
    esac
    case "$g_sum" in *'cors="https://'*) [[ "$g_sum" == *'*'* ]] && bad "gateway CORS has a wildcard" || ok "gateway CORS: named origins only" ;; *) bad "gateway CORS: $g_sum" ;; esac
    docker cp "$gw_cid:/certs/ca.crt" "$TMP/gw-ca.crt" >/dev/null 2>&1 || true
    if [ -s "$TMP/gw-ca.crt" ] && cmp -s "$TMP/gw-ca.crt" "$SMOKE_CA"; then
        ok "the gateway's /certs/ca.crt is the install CA, byte for byte"
    else
        bad "the gateway's /certs/ca.crt is not the install CA"
    fi
    hop=$(docker compose exec -T atlas-mcp python - 2>&1 <<'PY'
import socket, ssl
ctx = ssl.create_default_context(cafile="/run/secrets/atlas_ca")
# atlas-tls's :9444, reached here by its `atlas-api` alias on the mcp network; verified under the
# name the gateway uses for it, `atlas-mcp`.
with socket.create_connection(("atlas-api", 9444), timeout=5) as raw:
    with ctx.wrap_socket(raw, server_hostname="atlas-mcp") as s:
        print("HOP", s.version(), "verified as atlas-mcp against the install CA")
PY
)
    echo "$hop" | sed 's/^/       /'
    grep -q '^HOP TLSv1\.[23] ' <<<"$hop" && ok "atlas-tls :9444 (the gateway's upstream) presents a certificate valid for atlas-mcp under the install CA" \
        || bad "the :9444 hop's certificate does not verify as atlas-mcp"
    plain=$(docker compose exec -T atlas-tls wget -q -T 5 -O - http://127.0.0.1:9444/mcp/ 2>&1 || true)
    case "$plain" in
        *400*|*"HTTP request to an HTTPS server"*) ok "plain http:// to :9444 is refused by the proxy ($(head -c 80 <<<"$plain" | tr '\n' ' '))" ;;
        *) bad "plain http to :9444: $(head -c 120 <<<"$plain")" ;;
    esac
    gnets=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$gw_cid")
    shared=""
    for svc in atlas-mcp atlas-api; do
        cid=$(docker compose ps -q "$svc")
        for n in $(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$cid"); do
            case " $gnets " in *" $n "*) shared="$shared $svc:$n" ;; esac
        done
    done
    [ -z "$shared" ] && ok "atlas-gateway is on [$(xargs <<<"$gnets")], none shared with atlas-mcp or atlas-api" \
        || bad "atlas-gateway shares a network with:$shared"
fi

if [ "$fail" = 0 ]; then echo "oss_install_tls: all checks passed"; else echo "oss_install_tls: FAILED"; exit 1; fi
