#!/usr/bin/env bash
# check_binds.sh: no compose file publishes a port to every interface by accident (OSS-02).
#
# The rule. Every `ports:` entry must name a host address, and that address must not be an
# all-interfaces one. Concretely, each short-syntax entry is resolved the way compose resolves
# it (`${VAR:-default}` becomes its default, `${VAR}` becomes empty) and then:
#
#   "127.0.0.1:8095:8000"                       ok    (loopback)
#   "${ATLAS_BIND:-127.0.0.1}:8095:8000"        ok    (loopback unless the deployer opts in)
#   "0.0.0.0:8092:8092"                         FAIL  (every interface)
#   "8443:8443", "8443"                         FAIL  (bare: docker publishes on every interface)
#   "${ATLAS_BIND}:8095:8000"                   FAIL  (no default: empty host, every interface)
#   long syntax, flow lists, network_mode: host FAIL  (not checkable here; use the short form)
#
# Base files (every compose file outside deploy/) admit NO exception: a fresh install binds
# loopback or the compose network only. Overlays under deploy/ are deployment-specific and may
# publish to the LAN on purpose, but only on a line that says so with a trailing
#   # bind-ok: <reason>
# marker, so a LAN publish is always a reviewed, greppable decision.
#
# Usage:
#   tools/ci/check_binds.sh               check every tracked compose file in the repo
#   tools/ci/check_binds.sh FILE...       check these files (a path under deploy/ is an overlay)
#   tools/ci/check_binds.sh --self-test   prove the check fails on the cases above
#
# Exit 0 when clean, 1 on a violation, 2 on a usage error. Needs only bash, awk, sed, git.

set -euo pipefail

# Resolve compose interpolation to what an unset environment would produce: innermost first,
# so `${A:-${B:-127.0.0.1}}` becomes 127.0.0.1.
resolve() {
    local s=$1 prev=
    while [ "$s" != "$prev" ]; do
        prev=$s
        s=$(printf '%s' "$s" | sed -E 's/\$\{[A-Za-z_][A-Za-z0-9_]*:?[-=]([^{}]*)\}/\1/g; s/\$\{[A-Za-z_][A-Za-z0-9_]*(:?[?+][^{}]*)?\}//g')
    done
    printf '%s' "$s"
}

# Print "<line>\t<raw entry text>" for every item of every `ports:` block, and
# "<line>\tNETWORK_MODE_HOST" for `network_mode: host`. Comment lines are skipped.
extract() {
    awk '
    function ind(s) { match(s, /^ */); return RLENGTH }
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    {
        if (inports) {
            if (ind($0) <= pind) inports = 0
            else { if ($0 ~ /^[[:space:]]*- /) print FNR "\t" $0; next }
        }
        if ($0 ~ /^[[:space:]]*network_mode:[[:space:]]*"?host"?[[:space:]]*(#.*)?$/) print FNR "\tNETWORK_MODE_HOST"
        if ($0 ~ /^[[:space:]]*ports:[[:space:]]*(#.*)?$/) { inports = 1; pind = ind($0) }
        else if ($0 ~ /^[[:space:]]*ports:[[:space:]]*[^[:space:]#]/) print FNR "\tFLOW:" $0
    }' "$1"
}

is_overlay() { case "$1" in deploy/*|./deploy/*|*/deploy/*) return 0 ;; *) return 1 ;; esac; }

check_file() {
    local f=$1 bad=0 lineno raw entry marker resolved fields host
    while IFS=$'\t' read -r lineno raw; do
        marker=
        case "$raw" in *"# bind-ok:"*) marker=1 ;; esac
        if [ "$raw" = NETWORK_MODE_HOST ]; then
            echo "FAIL $f:$lineno network_mode: host publishes every port the container opens"; bad=1; continue
        fi
        case "$raw" in FLOW:*)
            echo "FAIL $f:$lineno inline ports list; use one '- \"HOST_IP:PUBLISHED:TARGET\"' per line"; bad=1; continue ;;
        esac
        # Strip the list dash, an inline comment and the quotes.
        entry=$(printf '%s' "$raw" | sed -E 's/^[[:space:]]*-[[:space:]]*//; s/[[:space:]]+#.*$//; s/^["'\'']//; s/["'\'']$//')
        case "$entry" in *:\ *|target:*|published:*|host_ip:*|mode:*)
            echo "FAIL $f:$lineno long-syntax port entry; use the short form \"HOST_IP:PUBLISHED:TARGET\""; bad=1; continue ;;
        esac
        resolved=$(resolve "$entry")
        fields=$(printf '%s' "$resolved" | awk -F: '{print NF}')
        if [ "$fields" -lt 3 ]; then
            host=""   # bare "PUBLISHED:TARGET" or "TARGET": docker binds every interface
        else
            host=${resolved%%:*}
        fi
        case "$host" in
            ""|0.0.0.0|"[::]"|::)
                if [ -n "$marker" ] && is_overlay "$f"; then
                    echo "ok   $f:$lineno $entry  (all interfaces, opted in: bind-ok)"
                else
                    echo "FAIL $f:$lineno $entry  resolves to host '${host:-<none>}', i.e. every interface"
                    if is_overlay "$f"; then
                        echo "     an overlay may publish to the LAN only with a trailing '# bind-ok: <reason>'"
                    else
                        echo "     a base compose file must default to loopback: \"\${ATLAS_BIND:-127.0.0.1}:PUBLISHED:TARGET\""
                    fi
                    bad=1
                fi ;;
            *)  echo "ok   $f:$lineno $entry  (host $host)" ;;
        esac
    done < <(extract "$f")
    return $bad
}

run() {
    local rc=0 f
    for f in "$@"; do
        [ -f "$f" ] || { echo "no such file: $f" >&2; return 2; }
        check_file "$f" || rc=1
    done
    return $rc
}

self_test() {
    local tmp rc=0
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' RETURN
    mkdir -p "$tmp/deploy"
    expect() {  # expect <0|1> <path relative to tmp> <yaml>
        printf '%s\n' "$3" > "$tmp/$2"
        local got=0
        (cd "$tmp" && check_file "$2" >/dev/null) || got=1
        if [ "$got" = "$1" ]; then echo "self-test ok   exit=$got  $2"; else echo "self-test FAIL expected exit=$1 got=$got  $2"; rc=1; fi
    }
    expect 1 zero.yml           $'services:\n  a:\n    ports:\n      - "0.0.0.0:8092:8092"'
    expect 1 bare.yml           $'services:\n  a:\n    ports:\n      - "8443:8443"'
    expect 1 bare1.yml          $'services:\n  a:\n    ports:\n      - 8443'
    expect 1 nodefault.yml      $'services:\n  a:\n    ports:\n      - "${ATLAS_BIND}:8095:8000"'
    expect 1 zerodefault.yml    $'services:\n  a:\n    ports:\n      - "${ATLAS_BIND:-0.0.0.0}:8095:8000"'
    expect 1 long.yml           $'services:\n  a:\n    ports:\n      - target: 80\n        published: 80'
    expect 1 flow.yml           $'services:\n  a:\n    ports: ["80:80"]'
    expect 1 hostnet.yml        $'services:\n  a:\n    network_mode: host'
    expect 1 basemarker.yml     $'services:\n  a:\n    ports:\n      - "8443:8443"  # bind-ok: not allowed in a base file'
    expect 1 deploy/nomark.yml  $'services:\n  a:\n    ports:\n      - "8443:8443"'
    expect 0 deploy/marked.yml  $'services:\n  a:\n    ports:\n      - "8443:8443"  # bind-ok: LAN front door'
    expect 0 loop.yml           $'services:\n  a:\n    ports:\n      - "127.0.0.1:8095:8000"'
    expect 0 envloop.yml        $'services:\n  a:\n    ports:\n      # 0.0.0.0 in a comment is fine\n      - "${ATLAS_BIND:-127.0.0.1}:${ATLAS_PORT:-8095}:8000"'
    expect 0 nested.yml         $'services:\n  a:\n    ports:\n      - "${ATLAS_MCP_BIND:-${ATLAS_BIND:-127.0.0.1}}:8092:8092"\n    environment:\n      - X=1'
    expect 0 noports.yml        $'services:\n  a:\n    image: postgres:16'
    return $rc
}

# Sourced (tools/ci/check_tls.sh reuses resolve and extract): define the functions only.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    case "${1:-}" in
        --self-test) self_test; exit $? ;;
        -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    esac

    if [ $# -gt 0 ]; then
        files=("$@")
    else
        root=$(git rev-parse --show-toplevel)
        cd "$root"
        mapfile -t files < <(git ls-files | grep -E '(^|/)(docker-)?compose[^/]*\.ya?ml$|^deploy/.*\.ya?ml$' | grep -v -E '(^|/)\.github/')
        [ ${#files[@]} -gt 0 ] || { echo "no compose files found; the glob is wrong, or the tree moved" >&2; exit 2; }
    fi

    if run "${files[@]}"; then
        echo "check_binds: clean (${#files[@]} files)"
    else
        rc=$?
        [ $rc = 2 ] && exit 2
        echo "check_binds: FAILED. Published ports must default to loopback; see the header of $0"
        exit 1
    fi
fi
