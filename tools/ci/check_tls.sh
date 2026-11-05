#!/usr/bin/env bash
# check_tls.sh: no token-carrying hop is plain HTTP past loopback (OSS-24).
#
# Every request to atlas-api and atlas-mcp carries a Bearer token, so on any wire it is HTTPS
# with the certificate verified. Plain http:// is allowed only where the traffic never leaves
# the machine: a loopback host (localhost, 127.0.0.0/8, ::1). Six rules over the tracked tree:
#
#   R1 references  In configuration (compose files, deploy/**/*.env, *.env.example, Caddy
#                  files, .mcp.json "url" values) every http:// URL on a non-comment line
#                  names a loopback host.
#                    ATLAS_BASE_URL=https://atlas.example.com:8443        ok
#                    test: curl -fsS http://localhost:8092/health         ok   (loopback)
#                    "url": "http://192.0.2.10:3000/mcp"                  FAIL
#                    ATLAS_BASE_URL=http://atlas-api:8000                 FAIL
#   R2 publishes   No compose port entry publishes a plain-HTTP token port (container port
#                  8000 = atlas-api, 8092 = atlas-mcp) on anything but loopback. Resolved the
#                  way check_binds.sh resolves it. A `bind-ok` marker does NOT excuse this:
#                  the LAN reaches those services through a TLS proxy (caddy-lan,
#                  atlas-mcp-tls), never directly.
#   R3 bind vars   ATLAS_BIND / ATLAS_MCP_BIND, which set the host of exactly those plain-HTTP
#                  publishes, are assigned only loopback values in deploy/**/*.env and tools/*.sh.
#   R4 no opt-out  No TLS verification switched off in code or config: verify=False,
#                  CERT_NONE, check_hostname=False, insecure: true, insecureHost: true,
#                  tls_insecure_skip_verify, NODE_TLS_REJECT_UNAUTHORIZED=0, curl -k/--insecure.
#   R5 OSS layout  The OSS install (compose files under deploy/oss/) publishes NO plain-HTTP
#                  token port at all, not even on loopback: there, atlas-api and atlas-mcp are
#                  reachable only through its TLS ingress (deploy/oss/caddy/Caddyfile), and
#                  atlas-mcp calls the API at https://. R2 still applies to every other file.
#                    - "127.0.0.1:8095:8000"   under deploy/oss/                    FAIL
#                    ATLAS_BASE_URL: http://atlas-api:8000                         FAIL (R1)
#   R6 OSS ingress The OSS ingress's own legs (OSS-26 D): in a Caddy file under deploy/oss/,
#                  every `reverse_proxy` names an https:// upstream and its block imports
#                  `origin_tls` (the transport verified against /certs/origin-ca.crt). A bare
#                  `host:port` upstream is plain HTTP to Caddy, and R1 cannot see it, since it
#                  has no `http://` in it. The homelab's Caddy files (deploy/caddy/) proxy to
#                  loopback and are not under this rule.
#                    reverse_proxy https://atlas-api-origin:8443 { import origin_tls ... }   ok
#                    reverse_proxy atlas-api-origin:8000 {                                 FAIL
#
# R2 and R5 read compose files only: a YAML file without a top-level `services:` key (a
# Kubernetes manifest under deploy/openshift/) is skipped by them, as check_binds.sh skips it;
# its `ports:` lists publish nothing on a host. R1 and R4 still read it.
#
# THE ONE ESCAPE HATCH is a trailing `# tls-exempt: <reason>` on the line, honoured ONLY for R1
# and R3, ONLY in deploy/ overlays and tools/ scripts, never in a base file, never for
# ATLAS_MCP_BIND, never for R2 or R4. Every exemption is printed on every run, so the list of
# them is the documented list of plaintext hops still standing (each `tls-exempt:` reason says
# why; an install's own deploy notes, outside this repo, say when each one goes).
#
# Usage:
#   tools/ci/check_tls.sh               check the tracked tree
#   tools/ci/check_tls.sh --self-test   prove each rule fails on its bad case and passes its good one
#
# Exit 0 when clean, 1 on a violation, 2 on a usage error. Needs bash, awk, sed, grep, git.

set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# resolve() and extract() from the bind check, so both rules read ports identically.
# shellcheck source=check_binds.sh
. "$HERE/check_binds.sh"

TOKEN_PORTS="8000 8092"

is_loopback() {  # is_loopback <host>
    local h=${1#[}; h=${h%]}; h=$(printf '%s' "$h" | tr '[:upper:]' '[:lower:]')
    case "$h" in
        localhost|localhost.localdomain|ip6-localhost|::1) return 0 ;;
        127.*) [[ "$h" =~ ^127\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] && return 0 ;;
    esac
    return 1
}

in_exemptable() { case "$1" in deploy/*|tools/*) return 0 ;; *) return 1 ;; esac; }
has_marker()    { [[ "$1" =~ \#[[:space:]]*tls-exempt:[[:space:]]*[^[:space:]] ]]; }

# Strip a whole-line comment (-> empty) or a trailing " # ..." comment.
code_part() {
    local line=$1
    [[ "$line" =~ ^[[:space:]]*# ]] && { printf ''; return; }
    printf '%s' "$line" | sed -E 's/[[:space:]]+#.*$//'
}

# R1 ----------------------------------------------------------------------------------------
check_refs() {  # check_refs <file>
    local f=$1 bad=0 n=0 line code url host
    local json=0; case "$f" in *.json) json=1 ;; esac
    while IFS= read -r line || [ -n "$line" ]; do
        n=$((n + 1))
        if [ $json = 1 ]; then
            code=$(printf '%s' "$line" | grep -oE '"url"[[:space:]]*:[[:space:]]*"[^"]*"' || true)
        else
            code=$(code_part "$line")
        fi
        [ -n "$code" ] || continue
        while IFS= read -r url; do
            [ -n "$url" ] || continue
            host=$(printf '%s' "$url" | sed -E 's#^[hH][tT][tT][pP]://##; s#^([^@/]*@)?##; s#^(\[[^]]*\]|[^:/?\#,"'\'' ]*).*#\1#')
            if is_loopback "$host"; then continue; fi
            if has_marker "$line" && in_exemptable "$f"; then
                echo "EXEMPT $f:$n $url  (tls-exempt)"
            else
                echo "FAIL   $f:$n $url  plain http:// to non-loopback host '${host:-<none>}'"
                bad=1
            fi
        done < <(printf '%s\n' "$code" | grep -oiE 'http://[^[:space:]"'\'',}]*' || true)
    done < "$f"
    return $bad
}

# R2 ----------------------------------------------------------------------------------------
check_publishes() {  # check_publishes <compose file>
    local f=$1 bad=0 lineno raw entry resolved host target fields
    is_compose "$f" || return 0   # not a compose file (check_binds.sh)
    while IFS=$'\t' read -r lineno raw; do
        case "$raw" in NETWORK_MODE_HOST|FLOW:*) continue ;; esac   # check_binds fails these
        entry=$(printf '%s' "$raw" | sed -E 's/^[[:space:]]*-[[:space:]]*//; s/[[:space:]]+#.*$//; s/^["'\'']//; s/["'\'']$//')
        resolved=$(resolve "$entry")
        target=${resolved##*:}; target=${target%%/*}
        if [[ "$resolved" == \[* ]]; then
            host=${resolved%%]*}]
        else
            fields=$(printf '%s' "$resolved" | awk -F: '{print NF}')
            if [ "$fields" -lt 3 ]; then host=""; else host=${resolved%%:*}; fi
        fi
        case " $TOKEN_PORTS " in *" $target "*) ;; *) continue ;; esac
        if is_loopback "$host"; then
            echo "ok     $f:$lineno $entry  (plain-HTTP token port $target on loopback)"
        else
            echo "FAIL   $f:$lineno $entry  publishes plain-HTTP token port $target on '${host:-<every interface>}'"
            echo "       put a TLS proxy in front and bind this to loopback (mcp/docker-compose.tls.yml)"
            bad=1
        fi
    done < <(extract "$f")
    return $bad
}

# R3 ----------------------------------------------------------------------------------------
check_bindvars() {  # check_bindvars <file>
    local f=$1 bad=0 n=0 line code var val
    while IFS= read -r line || [ -n "$line" ]; do
        n=$((n + 1))
        code=$(code_part "$line")
        [ -n "$code" ] || continue
        while IFS= read -r m; do
            [ -n "$m" ] || continue
            var=${m%%=*}; val=${m#*=}; val=${val//\"/}; val=${val//\'/}
            is_loopback "$val" && continue
            if [ "$var" != ATLAS_MCP_BIND ] && has_marker "$line" && in_exemptable "$f"; then
                echo "EXEMPT $f:$n $var=$val  (tls-exempt)"
            else
                echo "FAIL   $f:$n $var=$val  publishes a plain-HTTP token port beyond loopback"
                bad=1
            fi
        done < <(printf '%s\n' "$code" | grep -oE '\bATLAS(_MCP)?_BIND=("[^"]*"|'\''[^'\'']*'\''|[^[:space:];&|]*)' || true)
    done < "$f"
    return $bad
}

# R4 ----------------------------------------------------------------------------------------
INSECURE_RE='verify[[:space:]]*=[[:space:]]*False|CERT_NONE|check_hostname[[:space:]]*=[[:space:]]*False|insecure(Host)?:[[:space:]]*true|tls_insecure_skip_verify|NODE_TLS_REJECT_UNAUTHORIZED[[:space:]]*=[[:space:]]*["'\'']?0|curl[^|;&]*[[:space:]](-k|--insecure)([[:space:]]|$)'
check_insecure() {  # check_insecure <file>
    local f=$1 hits
    hits=$(grep -nE "$INSECURE_RE" "$f" || true)
    [ -z "$hits" ] && return 0
    printf '%s\n' "$hits" | sed "s#^#FAIL   $f:#; s#\$#  (TLS verification switched off)#"
    return 1
}

# R6 ----------------------------------------------------------------------------------------
check_oss_upstreams() {  # check_oss_upstreams <Caddy file under deploy/oss/>
    local f=$1 bad=0 n=0 line code target open=0 opened=0 tls=0 depth=0
    while IFS= read -r line || [ -n "$line" ]; do
        n=$((n + 1))
        code=$(code_part "$line")
        [ -n "$code" ] || continue
        if [[ "$code" =~ ^[[:space:]]*reverse_proxy([[:space:]]+([^[:space:]{]+))? ]]; then
            target=${BASH_REMATCH[2]}
            case "$target" in
                https://*) ;;
                *) echo "FAIL   $f:$n reverse_proxy ${target:-<none>}  the OSS ingress proxies only to https:// upstreams"; bad=1 ;;
            esac
            open=$n; tls=0; depth=0; opened=0
        fi
        if [ "$open" != 0 ]; then
            [[ "$code" =~ import[[:space:]]+origin_tls([[:space:]]|$) ]] && tls=1
            local o=${code//[^\{]/} c=${code//[^\}]/}
            depth=$((depth + ${#o} - ${#c}))
            [ ${#o} -gt 0 ] && opened=1
            if [ "$opened" = 1 ] && [ "$depth" -le 0 ]; then
                [ "$tls" = 1 ] || { echo "FAIL   $f:$open reverse_proxy without \`import origin_tls\` (the verified TLS transport)"; bad=1; }
                open=0
            fi
        fi
    done < "$f"
    if [ "$open" != 0 ]; then echo "FAIL   $f:$open reverse_proxy without a block importing origin_tls"; bad=1; fi
    return $bad
}

# R5 ----------------------------------------------------------------------------------------
check_oss_publishes() {  # check_oss_publishes <compose file under deploy/oss/>
    local f=$1 bad=0 lineno raw entry resolved target
    is_compose "$f" || return 0   # not a compose file (check_binds.sh)
    while IFS=$'\t' read -r lineno raw; do
        case "$raw" in NETWORK_MODE_HOST|FLOW:*) continue ;; esac   # check_binds fails these
        entry=$(printf '%s' "$raw" | sed -E 's/^[[:space:]]*-[[:space:]]*//; s/[[:space:]]+#.*$//; s/^["'\'']//; s/["'\'']$//')
        resolved=$(resolve "$entry")
        target=${resolved##*:}; target=${target%%/*}
        case " $TOKEN_PORTS " in
            *" $target "*)
                echo "FAIL   $f:$lineno $entry  the OSS layout publishes plain-HTTP token port $target"
                echo "       (even loopback): reach it through the TLS ingress instead"
                bad=1 ;;
        esac
    done < <(extract "$f")
    return $bad
}

# The file sets each rule reads. Paths relative to the repo root.
list_files() {  # list_files <rule>
    local all; all=$(git ls-files)
    case "$1" in
        refs)      printf '%s\n' "$all" | grep -E '(^|/)(docker-)?compose[^/]*\.ya?ml$|^deploy/.*\.(ya?ml|env)$|(^|/)\.env\.example$|^deploy/caddy/(Caddyfile[^/]*|tls/[^/]*\.caddy)$|^deploy/.*/(Caddyfile[^/]*|[^/]*\.caddy)$|(^|/)\.mcp\.json$|^agent/.*\.ya?ml$' | grep -v -E '(^|/)\.github/' | sort -u ;;
        publishes) printf '%s\n' "$all" | grep -E '(^|/)(docker-)?compose[^/]*\.ya?ml$|^deploy/.*\.ya?ml$' | grep -v -E '(^|/)\.github/' ;;
        bindvars)  printf '%s\n' "$all" | grep -E '^deploy/.*\.env$|^tools/[^/]*\.sh$|^scripts/[^/]*\.sh$|(^|/)\.env\.example$' ;;
        # backend/baseload_data.py is excluded for the reason *.md is never listed: it is
        # generated DATA (tools/gen_baseload.py) whose long strings are copies of docs/oss guides,
        # which warn against `NODE_TLS_REJECT_UNAUTHORIZED=0` by naming it. It holds no code and
        # configures no client; the guides it copies are prose, as every docs/oss page is.
        insecure)  printf '%s\n' "$all" | grep -E '^(mcp|backend|deploy|tools|agent|scripts)/.*\.(py|ya?ml|env|sh|json|caddy)$|^deploy/(.*/)?Caddyfile|(^|/)(docker-)?compose[^/]*\.ya?ml$|(^|/)\.mcp\.json$' \
                       | grep -v -E '^tools/ci/check_tls\.sh$|^tools/seed-node-docs/|^backend/baseload_data\.py$' ;;
        oss)       printf '%s\n' "$all" | grep -E '^deploy/oss/.*\.ya?ml$' ;;
        osscaddy)  printf '%s\n' "$all" | grep -E '^deploy/oss/.*/(Caddyfile[^/]*|[^/]*\.caddy)$' ;;
    esac
}

run_all() {
    local rc=0 f
    while IFS= read -r f; do [ -n "$f" ] && { check_refs "$f" || rc=1; }; done < <(list_files refs)
    while IFS= read -r f; do [ -n "$f" ] && { check_publishes "$f" || rc=1; }; done < <(list_files publishes)
    while IFS= read -r f; do [ -n "$f" ] && { check_bindvars "$f" || rc=1; }; done < <(list_files bindvars)
    while IFS= read -r f; do [ -n "$f" ] && { check_insecure "$f" || rc=1; }; done < <(list_files insecure)
    while IFS= read -r f; do [ -n "$f" ] && { check_oss_publishes "$f" || rc=1; }; done < <(list_files oss)
    while IFS= read -r f; do [ -n "$f" ] && { check_oss_upstreams "$f" || rc=1; }; done < <(list_files osscaddy)
    return $rc
}

self_test() {
    local tmp rc=0
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' RETURN
    mkdir -p "$tmp/deploy/homelab" "$tmp/deploy/oss/caddy" "$tmp/deploy/openshift" "$tmp/tools" "$tmp/mcp/app"
    # t <0|1> <rule fn> <path relative to tmp> <content>
    t() {
        printf '%s\n' "$4" > "$tmp/$3"
        local got=0
        (cd "$tmp" && "$2" "$3" >/dev/null) || got=1
        if [ "$got" = "$1" ]; then echo "self-test ok   exit=$got  $2 $3"
        else echo "self-test FAIL expected exit=$1 got=$got  $2 $3"; rc=1; fi
    }
    # R1
    t 1 check_refs .mcp.json              $'{"mcpServers": {"atlas": {"type": "http", "url": "http://192.0.2.10:3000/mcp"}}}'
    t 0 check_refs .mcp.json              $'{"mcpServers": {"atlas": {"type": "http", "url": "https://atlas.example.com:3443/mcp"}}}'
    t 0 check_refs a.json                 $'{"_comment": ["once it was http://192.0.2.10:8092/mcp/"], "url": "https://x.example"}'
    t 1 check_refs deploy/homelab/a.env   $'ATLAS_BASE_URL=http://atlas-api:8000'
    t 1 check_refs deploy/homelab/b.env   $'ATLAS_BASE_URL=HTTP://Atlas.Example.com:8443'
    t 0 check_refs deploy/homelab/c.env   $'# http://192.0.2.10:8095 in a comment is prose\nATLAS_BASE_URL=https://atlas.example.com:8443'
    t 1 check_refs compose.yml            $'services:\n  a:\n    environment:\n      ORIGINS: https://a.example,http://192.0.2.5:8096'
    t 1 check_refs compose2.yml           $'services:\n  a:\n    environment:\n      ORIGINS: http://192.0.2.5:8096  # tls-exempt: not allowed in a base file'
    t 0 check_refs deploy/over.yml        $'services:\n  a:\n    environment:\n      ORIGINS: http://192.0.2.5:8096  # tls-exempt: staging tunnel, follow-up'
    t 1 check_refs deploy/over2.yml       $'services:\n  a:\n    environment:\n      ORIGINS: http://192.0.2.5:8096  # tls-exempt:'
    t 0 check_refs compose3.yml           $'services:\n  a:\n    healthcheck:\n      test: ["CMD", "curl", "-fsS", "http://localhost:8092/health"]\n    environment:\n      O: http://127.0.0.1:8095,http://[::1]:8095'
    t 1 check_refs compose4.yml           $'services:\n  a:\n    environment:\n      O: http://localhost.evil.example:8095'
    # R2
    t 1 check_publishes deploy/p1.yml     $'services:\n  a:\n    ports:\n      - "0.0.0.0:8092:8092"  # bind-ok: does not excuse plaintext'
    t 1 check_publishes p2.yml            $'services:\n  a:\n    ports:\n      - "192.0.2.10:8095:8000"'
    t 1 check_publishes p3.yml            $'services:\n  a:\n    ports:\n      - "8092:8092"'
    t 0 check_publishes p4.yml            $'services:\n  a:\n    ports:\n      - "${ATLAS_MCP_BIND:-${ATLAS_BIND:-127.0.0.1}}:8092:8092"\n      - "${ATLAS_MCP_TLS_BIND:-0.0.0.0}:8446:8446"'
    t 0 check_publishes deploy/p5.yml     $'services:\n  a:\n    ports:\n      - "8443:8443"  # bind-ok: TLS front door'
    # A Kubernetes list is not a compose publish; the same item in a compose file still fails.
    t 0 check_publishes deploy/openshift/k.yaml $'apiVersion: v1\nkind: Service\nspec:\n  ports:\n    - "8000"\n    - port: 8092'
    t 1 check_publishes deploy/p6.yml     $'services:\n  a:\n    ports:\n      - "8000"'
    # R3
    t 1 check_bindvars deploy/homelab/m.env   $'ATLAS_MCP_BIND=0.0.0.0'
    t 1 check_bindvars deploy/homelab/m2.env  $'ATLAS_MCP_BIND=0.0.0.0  # tls-exempt: never honoured for atlas-mcp'
    t 0 check_bindvars deploy/homelab/m3.env  $'ATLAS_MCP_BIND=127.0.0.1\nATLAS_MCP_TLS_BIND=0.0.0.0'
    t 1 check_bindvars tools/s.sh             $'ssh h "cd d && ATLAS_PORT=1 ATLAS_BIND=0.0.0.0 docker compose up -d"'
    t 0 check_bindvars tools/s2.sh            $'ssh h "cd d && ATLAS_BIND=0.0.0.0 docker compose up -d"  # tls-exempt: staging, follow-up'
    t 0 check_bindvars tools/s3.sh            $'# ATLAS_BIND=0.0.0.0 in a comment is prose'
    # R4
    t 1 check_insecure mcp/app/x.py       $'c = httpx.Client(verify=False)'
    t 1 check_insecure mcp/app/y.py       $'ctx.verify_mode = ssl.CERT_NONE'
    t 1 check_insecure tools/z.sh         $'curl -fsS -k https://atlas.example.com:8446/health'
    t 1 check_insecure deploy/g.yaml      $'policies:\n  backendTLS:\n    insecure: true'
    t 0 check_insecure mcp/app/ok.py      $'c = httpx.Client(verify=SSL_CONTEXT)'
    t 0 check_insecure tools/ok.sh        $'curl -fsS --cacert ca.pem https://atlas.example.com:8446/health'
    t 1 check_insecure deploy/oss/caddy/x.caddy $'reverse_proxy https://atlas-api:8000 {\n\ttransport http {\n\t\ttls_insecure_skip_verify\n\t}\n}'
    # R5
    t 1 check_oss_publishes deploy/oss/o1.yml $'services:\n  atlas-api:\n    ports:\n      - "127.0.0.1:8095:8000"'
    t 1 check_oss_publishes deploy/oss/o2.yml $'services:\n  atlas-mcp:\n    ports:\n      - "${ATLAS_MCP_BIND:-127.0.0.1}:8092:8092"'
    t 0 check_oss_publishes deploy/oss/o3.yml $'services:\n  atlas-tls:\n    ports:\n      - "${ATLAS_TLS_BIND:-0.0.0.0}:${ATLAS_TLS_PORT:-443}:8443"  # bind-ok: the TLS ingress'
    t 0 check_oss_publishes deploy/oss/o4.yml $'services:\n  atlas-api:\n    expose:\n      - "8000"'
    t 0 check_oss_publishes deploy/oss/o6.yaml $'apiVersion: v1\nkind: Service\nspec:\n  ports:\n    - "8000"'
    t 1 check_refs deploy/oss/o5.yml         $'services:\n  atlas-mcp:\n    environment:\n      ATLAS_BASE_URL: http://atlas-api:8000'
    # R6
    t 0 check_oss_upstreams deploy/oss/caddy/u1 $'a:8443 {\n\treverse_proxy https://atlas-api-origin:8443 {\n\t\timport origin_tls atlas-api-origin\n\t\timport upstream_headers\n\t}\n}'
    t 1 check_oss_upstreams deploy/oss/caddy/u2 $'a:8443 {\n\treverse_proxy atlas-api-origin:8000 {\n\t\timport upstream_headers\n\t}\n}'
    t 1 check_oss_upstreams deploy/oss/caddy/u3 $'a:8443 {\n\treverse_proxy {$UP:atlas-api-origin:8000} {\n\t\timport origin_tls x\n\t}\n}'
    t 1 check_oss_upstreams deploy/oss/caddy/u4 $'a:8443 {\n\treverse_proxy https://atlas-api-origin:8443 {\n\t\timport upstream_headers\n\t}\n}'
    t 1 check_oss_upstreams deploy/oss/caddy/u5 $'a:8443 {\n\treverse_proxy https://atlas-api-origin:8443\n}'
    t 1 check_oss_upstreams deploy/oss/caddy/u6 $'a:8443 {\n\treverse_proxy http://atlas-api-origin:8000 {\n\t\timport origin_tls x\n\t}\n}'
    t 0 check_oss_upstreams deploy/oss/caddy/u7 $'# reverse_proxy atlas-api-origin:8000 in a comment is prose\na:8443 {\n\trespond 404\n}'
    return $rc
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    case "${1:-}" in
        --self-test) self_test; exit $? ;;
        -h|--help) sed -n '2,54p' "$0"; exit 0 ;;
        "") ;;
        *) echo "usage: $0 [--self-test]" >&2; exit 2 ;;
    esac
    cd "$(git rev-parse --show-toplevel)"
    if run_all; then
        echo "check_tls: clean"
    else
        echo "check_tls: FAILED. Token-carrying hops are HTTPS past loopback; see the header of $0"
        exit 1
    fi
fi
