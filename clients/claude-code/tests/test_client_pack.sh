#!/usr/bin/env bash
# The client pack, end to end against a stub (no Atlas, no network beyond loopback).
#
#   bash clients/claude-code/tests/test_client_pack.sh
#
#   1. static: check_pack.py (templates, plugin manifest, marketplace, skills).
#   2. helper (sh): a 0600 token file gives exactly {"Authorization":"Bearer <token>"}; a missing,
#      group-readable, empty or non-token file is refused with nothing on stdout.
#   3. helper (PowerShell), when pwsh is installed: the same cases.
#   4. the header on the wire: a TLS stub MCP endpoint signed by a throwaway CA. The template's
#      URL is pointed at it exactly as README step 2 says; the template's own headersHelper
#      command is run through a shell with HOME at a scratch home (README step 1's layout); the
#      header it printed is sent from a FILE (curl -H @file) so the token is on no command line.
#      No header -> 401 + WWW-Authenticate: Bearer; the helper's header -> initialize and
#      tools/list answered.
#   5. Claude Code itself, only when CLIENT_PACK_E2E_CLAUDE=1 and `claude` is on PATH: the
#      template is added at user scope in a throwaway CLAUDE_CONFIG_DIR, NODE_EXTRA_CA_CERTS
#      names the stub CA (README "trust the install CA"), and `claude mcp list` must report
#      the server connected, with the stub logging an authorised initialize.
#
# The token is generated here, lives in a 0600 file under a 0700 scratch directory, and is
# never printed: every output line that could carry it is redacted or reduced to a count.
set -euo pipefail
trap 'echo "test_client_pack: command failed at line $LINENO" >&2' ERR

HERE=$(cd "$(dirname "$0")" && pwd)
PACK=$(cd "$HERE/.." && pwd)
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || PY=python

fails=0
pass() { printf 'ok   %s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*"; fails=$((fails + 1)); }
redact() { sed -E 's/(Bearer )[A-Za-z0-9._~+\/=-]+/\1<redacted>/g'; }

WORK=$(mktemp -d)
chmod 700 "$WORK"
STUB_PID=
cleanup() {
    [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null || true
    rm -rf "$WORK"
}
trap cleanup EXIT

case "$(uname -s)" in MINGW* | MSYS* | CYGWIN*) ntfs=1 ;; *) ntfs=0 ;; esac

# ---------------------------------------------------------------- 1. static
if "$PY" "$HERE/check_pack.py"; then pass "check_pack.py"; else fail "check_pack.py"; fi

# ---------------------------------------------------------------- the token, as README step 1 leaves it
H="$WORK/home"
mkdir -p "$H/.config/atlas"
chmod 700 "$H/.config/atlas"
TOKEN_FILE="$H/.config/atlas/token"
( umask 077; "$PY" -c "import secrets; print('atlas_svc_' + secrets.token_hex(24))" > "$TOKEN_FILE" )
cp "$PACK/helpers/atlas-mcp-headers.sh" "$PACK/helpers/atlas-mcp-headers.ps1" "$H/.config/atlas/"
EXPECT="$WORK/expect.json"
( umask 077; "$PY" - "$TOKEN_FILE" > "$EXPECT" <<'PY'
import json, sys
tok = open(sys.argv[1]).readline().strip()
print(json.dumps({"Authorization": "Bearer " + tok}, separators=(",", ":")))
PY
)

same_json() {  # same_json FILE_A FILE_B -> 0 if both hold the same JSON object
    "$PY" - "$1" "$2" <<'PY'
import json, sys
a, b = (json.load(open(p)) for p in sys.argv[1:3])
sys.exit(0 if a == b and isinstance(a, dict) and all(isinstance(v, str) for v in a.values()) else 1)
PY
}

# helper_cases NAME CMD... : runs the refusal and success cases against one helper.
helper_cases() {
    local name=$1; shift
    local out="$WORK/out.json" err="$WORK/err.txt"
    if "$@" "$TOKEN_FILE" > "$out" 2> "$err" && same_json "$out" "$EXPECT"; then
        pass "$name: 0600 token file -> {\"Authorization\":\"Bearer <token>\"}"
    else
        fail "$name: good token file ($(redact < "$err" | head -2))"
    fi
    # CRLF and a trailing blank line are tolerated (a file saved on Windows).
    local crlf="$WORK/crlf"
    ( umask 077; printf '%s\r\n\r\n' "$(head -1 "$TOKEN_FILE")" > "$crlf" )
    if "$@" "$crlf" > "$out" 2> "$err" && same_json "$out" "$EXPECT"; then
        pass "$name: CRLF token file accepted"
    else
        fail "$name: CRLF token file ($(redact < "$err" | head -2))"
    fi
    local label file
    for label in missing empty garbage; do
        file="$WORK/case-$label"
        rm -f "$file"
        case $label in
            empty) ( umask 077; : > "$file" ) ;;
            garbage) ( umask 077; printf 'x", "Evil": "1\n' > "$file" ) ;;
        esac
        if "$@" "$file" > "$out" 2> "$err"; then
            fail "$name: $label token file was accepted"
        elif [ -s "$out" ]; then
            fail "$name: $label token file refused but wrote to stdout"
        else
            pass "$name: $label token file refused ($(head -1 "$err" | sed 's/^atlas-mcp-headers: //' | cut -c1-60)...)"
        fi
    done
    if [ "$ntfs" = 0 ]; then
        file="$WORK/case-open"
        cp "$TOKEN_FILE" "$file"; chmod 644 "$file"
        if "$@" "$file" > "$out" 2> "$err"; then
            fail "$name: a group/other-readable token file was accepted"
        elif [ -s "$out" ]; then
            fail "$name: group-readable file refused but wrote to stdout"
        else
            pass "$name: group/other-readable token file refused"
        fi
    fi
}

# ---------------------------------------------------------------- 2. sh helper
helper_cases "sh helper" sh "$PACK/helpers/atlas-mcp-headers.sh"
# The default path: no argument, HOME decides.
if HOME="$H" sh "$PACK/helpers/atlas-mcp-headers.sh" > "$WORK/def.json" 2>/dev/null \
        && same_json "$WORK/def.json" "$EXPECT"; then
    pass "sh helper: default path \$HOME/.config/atlas/token"
else
    fail "sh helper: default path"
fi

# ---------------------------------------------------------------- 3. PowerShell helper
PWSH=$(command -v pwsh || true)
if [ -n "$PWSH" ]; then
    helper_cases "pwsh helper" "$PWSH" -NoProfile -File "$PACK/helpers/atlas-mcp-headers.ps1"
else
    printf 'skip pwsh helper (pwsh not installed)\n'
fi

# ---------------------------------------------------------------- 4. the header on the wire
command -v openssl >/dev/null || { fail "openssl is needed for the TLS stub"; exit 1; }
PKI="$WORK/pki"; mkdir "$PKI"
export MSYS2_ARG_CONV_EXCL="/CN="   # Git Bash would rewrite "/CN=..." as a Windows path
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=client-pack test CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout "$PKI/ca.key" -out "$PKI/ca.crt" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=localhost" \
    -keyout "$PKI/server.key" -out "$PKI/server.csr" 2>/dev/null
printf 'subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n' > "$PKI/ext.cnf"
openssl x509 -req -in "$PKI/server.csr" -CA "$PKI/ca.crt" -CAkey "$PKI/ca.key" -CAcreateserial \
    -days 1 -extfile "$PKI/ext.cnf" -out "$PKI/server.crt" 2>/dev/null

# Windows curl (schannel) cannot check revocation for a throwaway CA; Linux and macOS curl
# do not try.
CURL_NOREVOKE=()
[ "$ntfs" = 1 ] && CURL_NOREVOKE=(--ssl-no-revoke)
CURL_TLS=(--cacert "$PKI/ca.crt" "${CURL_NOREVOKE[@]}")
PORT=$("$PY" -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1])")
STUB_LOG="$WORK/stub.log"; : > "$STUB_LOG"
"$PY" "$HERE/stub_mcp.py" --token-file "$TOKEN_FILE" --port "$PORT" \
    --tls-cert "$PKI/server.crt" --tls-key "$PKI/server.key" --log "$STUB_LOG" 2>"$WORK/stub.err" &
STUB_PID=$!
for _ in $(seq 1 50); do
    curl -s -o /dev/null "${CURL_TLS[@]}" "https://localhost:$PORT/mcp/" && break
    sleep 0.1
done
INSTALL_URL="https://localhost:$PORT"

# README step 2: copy the template, point it at the install's URL.
CFG="$WORK/atlas.mcp.json"
# Claude Code runs a headersHelper through sh on Linux and macOS and through cmd.exe on
# Windows (observed with Claude Code 2.1.310), hence one template per platform.
if [ "$ntfs" = 1 ]; then
    TEMPLATE=mcp.template.windows.json
    H_NATIVE=$(cygpath -w "$H")
else
    TEMPLATE=mcp.template.json
    H_NATIVE=$H
fi
sed "s#https://atlas.example.com#$INSTALL_URL#" "$PACK/$TEMPLATE" > "$CFG"
read_cfg() { "$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['mcpServers']['atlas'][sys.argv[2]])" "$CFG" "$1"; }
URL=$(read_cfg url)
[ "$URL" = "$INSTALL_URL/mcp/" ] && pass "$TEMPLATE: url -> $URL" || fail "$TEMPLATE: url is $URL"

# Run the template's helper command the way Claude Code does: through the platform shell
# (subprocess shell=True: /bin/sh, or cmd.exe on Windows), from another directory, with the
# user's home at the scratch home and nothing else of this environment.
HDR_JSON="$WORK/hdr.json"
if "$PY" - "$CFG" "$H_NATIVE" > "$HDR_JSON" 2>"$WORK/hdr.err" <<'PY' && same_json "$HDR_JSON" "$EXPECT"; then
import json, os, subprocess, sys, tempfile
cmd = json.load(open(sys.argv[1]))["mcpServers"]["atlas"]["headersHelper"]
env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "SystemRoot", "COMSPEC", "PATHEXT", "WINDIR")
       if k in os.environ}
env.update(HOME=sys.argv[2], USERPROFILE=sys.argv[2])
r = subprocess.run(cmd, shell=True, env=env, cwd=tempfile.gettempdir(), capture_output=True,
                   text=True, timeout=10)
sys.stdout.write(r.stdout); sys.stderr.write(r.stderr)
sys.exit(r.returncode)
PY
    pass "$TEMPLATE: its headersHelper command, run through the platform shell, prints the Bearer header"
else
    fail "$TEMPLATE: headersHelper command ($(redact < "$WORK/hdr.err" | head -2))"
fi
# JSON object -> curl header file ("Name: value" lines), never argv.
HDR_FILE="$WORK/hdr.txt"
( umask 077; "$PY" -c "import json,sys; [print(f'{k}: {v}') for k,v in json.load(open(sys.argv[1])).items()]" "$HDR_JSON" > "$HDR_FILE" )

INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"client-pack-test","version":"1"}}}'
LIST='{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
post() {  # post BODY [header-file] -> prints the status; body in $WORK/body, headers in resp.h
    local extra=()
    [ -n "${2:-}" ] && extra=(-H "@$2")
    curl -sS "${CURL_TLS[@]}" -o "$WORK/body" -D "$WORK/resp.h" -w '%{http_code}' \
        -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
        "${extra[@]}" --data "$1" "$URL"
}
code=$(post "$INIT")
if [ "$code" = 401 ] && grep -qi '^www-authenticate: bearer' "$WORK/resp.h"; then
    pass "no Authorization -> 401 WWW-Authenticate: Bearer"
else
    fail "no Authorization -> $code"
fi
code=$(post "$INIT" "$HDR_FILE")
if [ "$code" = 200 ] && grep -q '"serverInfo"' "$WORK/body"; then
    pass "helper's header -> initialize 200"
else
    fail "helper's header -> initialize $code"
fi
code=$(post "$LIST" "$HDR_FILE")
if [ "$code" = 200 ] && grep -q '"atlas_file_work"' "$WORK/body"; then
    pass "helper's header -> tools/list 200"
else
    fail "helper's header -> tools/list $code"
fi
# Without the install CA the connection must fail: the CA step in the README is not optional.
if curl -sS -o /dev/null "${CURL_NOREVOKE[@]}" "$URL" 2>/dev/null; then
    fail "TLS without the install CA succeeded (the stub cert should not be publicly trusted)"
else
    pass "without the install CA the TLS handshake is refused"
fi

# ---------------------------------------------------------------- 5. Claude Code itself (opt-in)
if [ "${CLIENT_PACK_E2E_CLAUDE:-0}" = 1 ]; then
    if ! command -v claude >/dev/null; then
        fail "CLIENT_PACK_E2E_CLAUDE=1 but claude is not on PATH"
    else
        CC="$WORK/claude-config"; mkdir "$CC"
        : > "$STUB_LOG"
        server_json=$("$PY" -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1]))['mcpServers']['atlas']))" "$CFG")
        # README step 3, user scope. HOME (USERPROFILE on Windows) is the scratch home the
        # helper reads the token from; CLAUDE_CONFIG_DIR keeps the real config untouched.
        # From a scratch directory, so no project .mcp.json is picked up.
        CA_NATIVE="$PKI/ca.crt"
        [ "$ntfs" = 1 ] && CA_NATIVE=$(cygpath -w "$PKI/ca.crt")
        cd "$WORK"
        HOME="$H" USERPROFILE="$H_NATIVE" CLAUDE_CONFIG_DIR="$CC" \
            claude mcp add-json atlas "$server_json" --scope user > "$WORK/add.out" 2>&1 || true
        HOME="$H" USERPROFILE="$H_NATIVE" CLAUDE_CONFIG_DIR="$CC" NODE_EXTRA_CA_CERTS="$CA_NATIVE" \
            timeout 90 claude mcp list > "$WORK/list.out" 2>&1 || true
        redact < "$WORK/list.out" | sed 's/^/     claude mcp list: /'
        # A variable, never `grep | grep -qi` under pipefail: the first grep can die of SIGPIPE
        # once grep -q has matched, and the pipeline then reports a miss (TASK-1504).
        atlas_line=$(grep -E '^atlas:' "$WORK/list.out" || true)
        if grep -qi 'connected' <<< "$atlas_line" \
                && ! grep -qi 'failed' <<< "$atlas_line" \
                && grep -q '^initialize 200' "$STUB_LOG"; then
            pass "claude mcp list: atlas connected with the helper's token (stub: $(grep -c '^initialize 200' "$STUB_LOG") authorised initialize)"
        else
            fail "claude mcp list did not connect (stub log: $(tr '\n' ' ' < "$STUB_LOG"))"
        fi
        # README "the plugin": add the pack as a local marketplace and install from it.
        CLAUDE_CONFIG_DIR="$CC" claude plugin validate --strict "$PACK" > "$WORK/v1.out" 2>&1 \
            && pass "claude plugin validate --strict (marketplace)" \
            || { fail "claude plugin validate --strict (marketplace)"; cat "$WORK/v1.out"; }
        CLAUDE_CONFIG_DIR="$CC" claude plugin validate --strict "$PACK/plugin" > "$WORK/v2.out" 2>&1 \
            && pass "claude plugin validate --strict (plugin)" \
            || { fail "claude plugin validate --strict (plugin)"; cat "$WORK/v2.out"; }
        CLAUDE_CONFIG_DIR="$CC" claude plugin marketplace add "$PACK" > "$WORK/m.out" 2>&1 || true
        CLAUDE_CONFIG_DIR="$CC" claude plugin install atlas@atlas-client > "$WORK/i.out" 2>&1 || true
        n=$(find "$CC/plugins" -path '*atlas*' -name SKILL.md 2>/dev/null \
            | grep -c -E '/skills/(atlas-retrieval|file-task)/SKILL\.md$' || true)
        if [ "$n" = 2 ]; then
            pass "claude plugin install atlas@atlas-client: both skills installed"
        else
            fail "plugin install: $n of 2 skills found"; cat "$WORK/m.out" "$WORK/i.out"
        fi
    fi
else
    printf 'skip Claude Code end to end (set CLIENT_PACK_E2E_CLAUDE=1 with claude on PATH)\n'
fi

echo
if [ "$fails" -gt 0 ]; then
    echo "client pack: $fails check(s) failed"
    exit 1
fi
echo "client pack: all checks passed"
