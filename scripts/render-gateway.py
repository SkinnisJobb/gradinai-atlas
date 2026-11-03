#!/usr/bin/env python3
"""render-gateway.py: the OSS gateway profile's AgentGateway configuration (STORY-oss-15).

Reads the server list (deploy/gateway/gateway.yaml by default) and three names from ./.env
(ATLAS_PUBLIC_URL, ATLAS_PUBLIC_HOST and ATLAS_GATEWAY_SESSION_KEY_FILE; nothing else is read
from it, and .env holds no secret), fills deploy/gateway/agentgateway.yaml.tmpl, and writes
./gateway/agentgateway.yaml, the file deploy/oss/docker-compose.yml mounts into the
atlas-gateway service. Run it from deploy/oss/:

    python3 ../../scripts/render-gateway.py [--servers FILE] [--env FILE] [--out FILE]
    python3 ../../scripts/render-gateway.py --self-test

It REFUSES to write (exit 1, naming the server and the reason):
  * an http:// server not marked `carries_credentials: false`, or one so marked that would
    still be handed the caller's token (`forward_authorization: true`);
  * a server whose URL is not http(s)://host[:port]/path, has user info, a query or fragment;
  * an empty or malformed `tools` allow-list, a duplicate or malformed server name;
  * a CORS origin of "*" (or anything that is not scheme://host[:port]); ATLAS_PUBLIC_URL that
    is not https://;
  * any option it does not know (a typo must not silently drop a guarantee);
  * a session key file (ATLAS_GATEWAY_SESSION_KEY_FILE) that is missing, readable by group or
    others, or does not hold exactly 64 hex characters (a 32-byte AES-256-GCM key).

MCP SESSION-ID ENCRYPTION (TASK-1460). Unset (the default), the rendered file says
`config.session.key: disabled`, byte for byte what it said before the setting existed. Set to a
file path (relative to the .env file's directory), the key in that file is written into the
rendered file as config.session.key, and the rendered file is then written 0600. Why inline:
AgentGateway v1.7.0 takes the key only as an inline string (config.session.key is a plain
String, not the `file:` form some other fields accept) or as the SESSION_KEY environment
variable, and its image has no shell that could read a file into either. An environment
variable shows in `docker inspect` and the container's /proc; the rendered file does not, and
the gateway's own config dump redacts the key. So the key lives in two operator-owned 0600
files (the key file and the rendered file, both git-ignored), and never in .env, argv, a log or
this script's output: no message here prints it, and a refusal names only its length.

The template's header lists what the rendered file guarantees. Every value is written as a
JSON string (a valid YAML double-quoted scalar), so no value can change the file's structure.

Exit 0 written, 1 refused, 2 usage error. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SERVERS = REPO / "deploy" / "gateway" / "gateway.yaml"
DEFAULT_TEMPLATE = REPO / "deploy" / "gateway" / "agentgateway.yaml.tmpl"
INSTALL_CA = "/certs/ca.crt"

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
TOOL_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$|^\[[0-9A-Fa-f:.]+\]$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]*$")
CA_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
HEX64_RE = re.compile(r"^[0-9A-Fa-f]{64}$")
#: The .env setting that turns MCP session-ID encryption on: a path to a file holding the key.
SESSION_KEY_ENV = "ATLAS_GATEWAY_SESSION_KEY_FILE"
SERVER_KEYS = {"name", "url", "tools", "forward_authorization", "ca", "tls_hostname",
               "carries_credentials"}
TOP_KEYS = {"servers", "cors_origins", "hostnames"}


class Refused(Exception):
    """The input would produce a gateway that breaks a guarantee."""


# --------------------------------------------------------------------------- #
# A small YAML subset: block mappings, block lists, flow lists of scalars, scalars.
# --------------------------------------------------------------------------- #
def _strip_comment(line: str) -> str:
    out, quote = [], ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _scalar(text: str, where: str):
    t = text.strip()
    if t.startswith("[") and t.endswith("]"):
        inner = t[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part, where) for part in _split_flow(inner, where)]
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        return t[1:-1]
    if t in ("true", "True"):
        return True
    if t in ("false", "False"):
        return False
    if t in ("", "~", "null"):
        return None
    if t[0] in "{&*!|>%@`":
        raise Refused(f"{where}: unsupported YAML ({t[:20]!r}); use plain or quoted scalars")
    return t


def _split_flow(inner: str, where: str) -> list:
    parts, cur, quote = [], [], ""
    for ch in inner:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch == ",":
            parts.append("".join(cur))
            cur = []
        elif ch in "[]{}":
            raise Refused(f"{where}: nested flow collections are not supported")
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p for p in parts if p.strip()]


def parse_yaml(text: str, source: str):
    lines = []
    for n, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise Refused(f"{source}:{n}: tab indentation")
        stripped = _strip_comment(raw)
        if stripped.strip():
            lines.append((n, len(stripped) - len(stripped.lstrip()), stripped.strip()))
    pos = 0

    def block(indent: int):
        nonlocal pos
        if pos >= len(lines):
            return None
        if lines[pos][2].startswith("- ") or lines[pos][2] == "-":
            return seq(lines[pos][1])
        return mapping(lines[pos][1])

    def mapping(indent: int) -> dict:
        nonlocal pos
        out: dict = {}
        while pos < len(lines) and lines[pos][1] == indent and not lines[pos][2].startswith("-"):
            n, _, text = lines[pos]
            key, sep, rest = text.partition(":")
            if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()):
                raise Refused(f"{source}:{n}: expected `key: value`, got {text[:40]!r}")
            key = key.strip()
            if key in out:
                raise Refused(f"{source}:{n}: duplicate key {key!r}")
            pos += 1
            if rest.strip():
                out[key] = _scalar(rest, f"{source}:{n}")
            elif pos < len(lines) and lines[pos][1] > indent:
                out[key] = block(lines[pos][1])
            elif pos < len(lines) and lines[pos][1] == indent and lines[pos][2].startswith("-"):
                out[key] = seq(indent)
            else:
                out[key] = None
        if pos < len(lines) and lines[pos][1] > indent:
            raise Refused(f"{source}:{lines[pos][0]}: unexpected indentation")
        return out

    def seq(indent: int) -> list:
        nonlocal pos
        out: list = []
        while pos < len(lines) and lines[pos][1] == indent and (lines[pos][2].startswith("- ") or lines[pos][2] == "-"):
            n, _, text = lines[pos]
            item = text[1:].strip()
            if not item:
                pos += 1
                out.append(block(lines[pos][1]) if pos < len(lines) and lines[pos][1] > indent else None)
                continue
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*:(\s|$)", item):
                # `- key: value` opens a mapping whose keys sit at the item's column.
                col = indent + (len(text) - len(item))
                lines[pos] = (n, col, item)
                out.append(mapping(col))
            else:
                pos += 1
                out.append(_scalar(item, f"{source}:{n}"))
        return out

    doc = block(0) if lines else {}
    if pos < len(lines):
        raise Refused(f"{source}:{lines[pos][0]}: could not parse from here ({lines[pos][2][:40]!r})")
    return doc


# --------------------------------------------------------------------------- #
# .env: only the two public names
# --------------------------------------------------------------------------- #
def read_env(path: Path) -> dict:
    wanted = {"ATLAS_PUBLIC_URL", "ATLAS_PUBLIC_HOST", SESSION_KEY_ENV}
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(ATLAS_PUBLIC_URL|ATLAS_PUBLIC_HOST|ATLAS_GATEWAY_SESSION_KEY_FILE)\s*=\s*(.*?)\s*$",
                     raw)
        if m and m.group(1) in wanted:
            val = m.group(2)
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            else:
                val = re.sub(r"\s+#.*$", "", val)
            out[m.group(1)] = val
    return out


# --------------------------------------------------------------------------- #
# The session key (TASK-1460): read from the operator's file, never printed
# --------------------------------------------------------------------------- #
def read_session_key(setting: str, env_dir: Path):
    """The key named by ATLAS_GATEWAY_SESSION_KEY_FILE, or None when the setting is unset.

    A refusal names the file and what is wrong with it, never any of its content."""
    if not setting:
        return None
    path = Path(setting)
    if not path.is_absolute():
        path = env_dir / path
    if not path.is_file():
        raise Refused(f"{SESSION_KEY_ENV}={setting}: no such file "
                      "(create it with `( umask 077; openssl rand -hex 32 > FILE )`)")
    if os.name == "posix":
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise Refused(f"{SESSION_KEY_ENV}={setting}: mode {mode:04o} lets group or others "
                          "read the key; chmod 600 it")
    key = path.read_text(encoding="utf-8", errors="replace").strip()
    if not HEX64_RE.match(key):
        raise Refused(f"{SESSION_KEY_ENV}={setting}: the file must hold exactly 64 hex characters "
                      f"(a 32-byte AES-256-GCM key, `openssl rand -hex 32`); it holds {len(key)} "
                      "characters" + ("" if re.fullmatch(r"[0-9A-Fa-f]*", key) else ", not all hex"))
    return key.lower()


# --------------------------------------------------------------------------- #
# Validation and rendering
# --------------------------------------------------------------------------- #
def _origin(value, where: str) -> str:
    if not isinstance(value, str) or value.strip() == "*" or "*" in value:
        raise Refused(f"{where}: CORS origin {value!r}: a wildcard is never allowed")
    parts = urlsplit(value)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.path not in ("", "/") \
            or parts.query or parts.fragment or parts.username:
        raise Refused(f"{where}: CORS origin {value!r} is not scheme://host[:port]")
    return f"{parts.scheme}://{parts.netloc}"


def _bool(server: dict, key: str, default: bool, where: str) -> bool:
    val = server.get(key, default)
    if not isinstance(val, bool):
        raise Refused(f"{where}: {key} must be true or false, got {val!r}")
    return val


def validate(doc, env: dict) -> dict:
    if not isinstance(doc, dict):
        raise Refused("the server list must be a mapping with a `servers:` list")
    unknown = set(doc) - TOP_KEYS
    if unknown:
        raise Refused(f"unknown top-level key(s) {sorted(unknown)} (known: {sorted(TOP_KEYS)})")

    url = env.get("ATLAS_PUBLIC_URL", "")
    if not url.startswith("https://"):
        raise Refused(f"ATLAS_PUBLIC_URL must be https:// (got {url!r}); set it in .env")
    origins = [_origin(url, "ATLAS_PUBLIC_URL")]
    for o in doc.get("cors_origins") or []:
        o = _origin(o, "cors_origins")
        if o not in origins:
            origins.append(o)

    host = env.get("ATLAS_PUBLIC_HOST", "")
    hostnames = doc.get("hostnames")
    if hostnames is None:
        if not host:
            raise Refused("ATLAS_PUBLIC_HOST is not set in .env")
        hostnames = [host]
    if not isinstance(hostnames, list) or not hostnames:
        raise Refused("hostnames must be a non-empty list")
    for h in hostnames:
        if not isinstance(h, str) or not HOST_RE.match(h) or "*" in h:
            raise Refused(f"hostnames: {h!r} is not a host name")

    servers = doc.get("servers")
    if not isinstance(servers, list) or not servers:
        raise Refused("`servers:` must list at least one server")
    seen, out = set(), []
    for i, s in enumerate(servers):
        where = f"servers[{i}]"
        if not isinstance(s, dict):
            raise Refused(f"{where}: must be a mapping")
        unknown = set(s) - SERVER_KEYS
        if unknown:
            raise Refused(f"{where}: unknown option(s) {sorted(unknown)} (known: {sorted(SERVER_KEYS)})")
        name = s.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise Refused(f"{where}: name {name!r} must be lowercase letters, digits and '-'")
        where = f"server {name!r}"
        if name in seen:
            raise Refused(f"{where}: duplicate name")
        seen.add(name)

        raw = s.get("url")
        if not isinstance(raw, str):
            raise Refused(f"{where}: url is required")
        parts = urlsplit(raw)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise Refused(f"{where}: url {raw!r} must be https://host[:port]/path")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise Refused(f"{where}: url {raw!r} must not carry user info, a query or a fragment")
        if not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", parts.hostname):
            raise Refused(f"{where}: url host in {raw!r} is not a host name")
        try:
            port = parts.port
        except ValueError:
            raise Refused(f"{where}: url {raw!r} has a bad port") from None
        path = parts.path or "/"
        if not PATH_RE.match(path):
            raise Refused(f"{where}: url path {path!r} has unsupported characters")

        forward = _bool(s, "forward_authorization", False, where)
        if parts.scheme == "http":
            if s.get("carries_credentials", True) is not False:
                raise Refused(
                    f"{where}: http:// to {parts.hostname} would carry callers' credentials in "
                    "plain text. Use https://, or mark the server `carries_credentials: false` "
                    "(it then receives no Authorization header)")
            if forward:
                raise Refused(f"{where}: an http:// server never receives the caller's token "
                              "(forward_authorization: true with carries_credentials: false)")
            for k in ("ca", "tls_hostname"):
                if k in s:
                    raise Refused(f"{where}: {k} means nothing for an http:// server")
        elif "carries_credentials" in s:
            _bool(s, "carries_credentials", True, where)

        ca = s.get("ca", INSTALL_CA)
        if parts.scheme == "https" and (not isinstance(ca, str) or not CA_RE.match(ca)):
            raise Refused(f"{where}: ca {ca!r} must be an absolute file path inside the container")
        sni = s.get("tls_hostname", parts.hostname)
        if parts.scheme == "https" and (not isinstance(sni, str) or not HOST_RE.match(sni)):
            raise Refused(f"{where}: tls_hostname {sni!r} is not a host name")

        tools = s.get("tools")
        if isinstance(tools, str):
            tools = [tools]
        if not isinstance(tools, list) or not tools:
            raise Refused(f"{where}: tools must be a non-empty list (or [\"*\"] for all)")
        if "*" in tools and tools != ["*"]:
            raise Refused(f"{where}: \"*\" stands alone in tools")
        for t in tools:
            if t != "*" and (not isinstance(t, str) or not TOOL_RE.match(t)):
                raise Refused(f"{where}: tool name {t!r} is not a tool name")

        out.append({"name": name, "url": parts._replace(path=path).geturl(), "scheme": parts.scheme,
                    "port": port, "forward": forward, "ca": ca, "sni": sni, "tools": tools})
    return {"origins": origins, "hostnames": hostnames, "servers": out}


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _flow(values: list) -> str:
    return "[" + ", ".join(_q(v) for v in values) + "]"


def rules(servers: list) -> list:
    out = []
    for s in servers:
        n = _q(s["name"])
        if s["tools"] == ["*"]:
            # Everything this server offers. One rule per kind: a rule that names a kind the
            # request is not (mcp.prompt on a tool call) fails on its own, without masking another.
            for kind in ("tool", "prompt", "resource"):
                out.append(f"mcp.{kind}.target == {n}")
        else:
            out.append(f"mcp.tool.target == {n} && mcp.tool.name in {_flow(s['tools'])}")
    return out


def targets(servers: list) -> list:
    lines = []
    for s in servers:
        lines += [f"- name: {_q(s['name'])}",
                  "  mcp:"]
        exempt = "  # tls-exempt: marked carries_credentials: false, no Authorization or Cookie" if s["scheme"] == "http" else ""
        lines.append(f"    host: {_q(s['url'])}{exempt}")
        lines.append("  policies:")
        if s["scheme"] == "https":
            lines += ["    backendTLS:",
                      f"      root: {_q(s['ca'])}",
                      f"      hostname: {_q(s['sni'])}"]
        if not s["forward"]:
            lines += ["    requestHeaderModifier:",
                      "      remove:",
                      "        - authorization",
                      "        - cookie"]
    return lines


#: The template's session-off text, exactly. Unset, the template is rendered as it stands (so the
#: output is byte-identical to the file before TASK-1460); with a key, these two passages are
#: replaced by the SESSION_ON_* ones. Edit the template's passages and these together.
SESSION_OFF_BULLET = """\
#   * MCP session-ID encryption is explicitly off (config.session.key: disabled), so the
#     gateway neither generates a key nor tries to write one into this read-only file.
"""
SESSION_OFF_BLOCK = """\
  session:
    # Decision 2026-10-31: off on purpose. The gateway is optional and LAN-only, and an MCP
    # session ID is a routing handle, not a secret: every call still carries its own Bearer
    # token, checked by atlas-mcp. Without this line AgentGateway v1.7.0 tries to write a
    # generated key back into this file (mounted read-only) and warns on every start.
    # Turning encryption on would need a SESSION_KEY secret (a 32-byte hex key) passed to the
    # container, not a key written here. `disabled` is the literal v1.7.0 accepts for this.
    key: disabled
"""
SESSION_ON_BULLET = """\
#   * MCP session-ID encryption is ON (config.session.key below, AES-256-GCM), with the key
#     render-gateway.py read from the file ATLAS_GATEWAY_SESSION_KEY_FILE names. THIS FILE
#     THEN HOLDS A SECRET: it is written 0600; never print, copy or commit it. Unset the
#     setting and render again to go back to `disabled`.
"""
SESSION_ON_BLOCK = """\
  session:
    # TASK-1460: on, because ATLAS_GATEWAY_SESSION_KEY_FILE is set in .env. v1.7.0 takes the
    # key only inline here or as a SESSION_KEY environment variable (which docker inspect
    # shows), so it is written here. A new key ends every open MCP session through the gateway.
    key: @@SESSION_KEY@@
"""


def _session(body: str, key) -> str:
    if key is None:
        return body
    for off, on in ((SESSION_OFF_BULLET, SESSION_ON_BULLET), (SESSION_OFF_BLOCK, SESSION_ON_BLOCK)):
        if body.count(off) != 1:
            raise Refused("the template's session passages are not the ones this script knows; "
                          "update SESSION_OFF_* in render-gateway.py with the template")
        body = body.replace(off, on)
    return body.replace("@@SESSION_KEY@@", _q(key))


def render(template: str, cfg: dict, servers_file: str, session_key=None) -> str:
    ind_rules = " " * 18
    ind_targets = " " * 20
    body = _session(template, session_key)
    body = body.replace("@@SERVERS_FILE@@", servers_file)
    body = body.replace("@@HOSTNAMES@@", _flow(cfg["hostnames"]))
    body = body.replace("@@CORS_ORIGINS@@", _flow(cfg["origins"]))
    body = body.replace("@@RULES@@", "\n".join(f"{ind_rules}- {_q(r)}" for r in rules(cfg["servers"])))
    body = body.replace("@@TARGETS@@", "\n".join(ind_targets + l for l in targets(cfg["servers"])))
    if "@@" in body:
        raise Refused("the template has a placeholder this script does not fill")
    return body


def write_atomic(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".render-gateway.")  # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        # 0644 without a session key (configuration, no secret: the gateway's user must read
        # it); 0600 with one (the gateway runs as root with DAC_READ_SEARCH, so it still can).
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
ENV_OK = {"ATLAS_PUBLIC_URL": "https://atlas.example.com", "ATLAS_PUBLIC_HOST": "atlas.example.com"}


def self_test() -> int:
    rc = 0
    template = DEFAULT_TEMPLATE.read_text(encoding="utf-8")

    def case(want_ok: bool, label: str, text: str, env: dict = ENV_OK, expect: tuple = ()) -> None:
        nonlocal rc
        try:
            out = render(template, validate(parse_yaml(text, "case"), env), "case")
            got, why = True, ""
            for needle in expect:
                if needle not in out:
                    got, why = False, f"missing {needle!r}"
        except Refused as exc:
            got, why = False, str(exc)
        good = got == want_ok and not (want_ok and why)
        print(f"self-test {'ok  ' if good else 'FAIL'} {'renders' if got else 'refused'}  {label}"
              + (f"  ({why})" if why and not good else ""))
        rc |= 0 if good else 1

    shipped = DEFAULT_SERVERS.read_text(encoding="utf-8")
    case(True, "the shipped server list", shipped, expect=(
        '"https://atlas-mcp:9444/mcp/"', 'root: "/certs/ca.crt"', 'hostname: "atlas-mcp"',
        "prefixMode: never", 'allowOrigins: ["https://atlas.example.com"]',
        'hostnames: ["atlas.example.com"]', '- "mcp.tool.target == \\"atlas\\""',
        "protocol: HTTPS",
        # MCP session-ID encryption explicitly off (decision 2026-10-31): a top-level
        # config.session.key of `disabled`, so v1.7.0 does not try to write a key into the
        # read-only mounted file.
        "config:\n  session:\n", "    key: disabled\nbinds:"))
    case(False, "http:// without carries_credentials: false",
         "servers:\n  - name: t\n    url: http://tools.example:8080/mcp\n    tools: [a]\n")
    case(False, "http:// with carries_credentials: true",
         "servers:\n  - name: t\n    url: http://tools.example/mcp\n    carries_credentials: true\n    tools: [a]\n")
    case(False, "http:// marked no-credential but forwarding the token",
         "servers:\n  - name: t\n    url: http://tools.example/mcp\n    carries_credentials: false\n"
         "    forward_authorization: true\n    tools: [a]\n")
    case(True, "http:// marked no-credential: Authorization removed, tls-exempt marker",
         "servers:\n  - name: t\n    url: http://tools.example/mcp\n    carries_credentials: false\n    tools:\n      - a\n      - b\n",
         expect=("- authorization", "tls-exempt:", 'mcp.tool.name in [\\"a\\", \\"b\\"]'))
    case(True, "a second https server gets no token and its own allow-list",
         shipped.replace("servers:\n", "servers:\n  - name: tickets\n    url: https://tickets.example/mcp\n"
                         "    tools: [ticket_get]\n", 1),
         expect=('"https://tickets.example/mcp"', "- authorization", '\\"tickets\\" && mcp.tool.name in'))
    case(False, "CORS wildcard", "cors_origins: [\"*\"]\n" + shipped)
    case(False, "CORS origin with a path", "cors_origins: [https://x.example/app]\n" + shipped)
    case(False, "ATLAS_PUBLIC_URL over http", shipped,
         env={"ATLAS_PUBLIC_URL": "http://atlas.example.com", "ATLAS_PUBLIC_HOST": "atlas.example.com"})
    case(False, "an unknown option (typo)",
         "servers:\n  - name: t\n    url: https://t.example/mcp\n    forward_authorisation: true\n    tools: [a]\n")
    case(False, "empty tools", "servers:\n  - name: t\n    url: https://t.example/mcp\n    tools: []\n")
    case(False, "\"*\" mixed with names", "servers:\n  - name: t\n    url: https://t.example/mcp\n    tools: [\"*\", a]\n")
    case(False, "duplicate server names",
         "servers:\n  - name: t\n    url: https://t.example/mcp\n    tools: [a]\n"
         "  - name: t\n    url: https://u.example/mcp\n    tools: [b]\n")
    case(False, "a value that would inject YAML",
         "servers:\n  - name: t\n    url: \"https://t.example/mcp\\n    backendAuth: passthrough\"\n    tools: [a]\n")
    case(False, "user info in the URL", "servers:\n  - name: t\n    url: https://u:p@t.example/mcp\n    tools: [a]\n")
    case(False, "a tool name with a quote", "servers:\n  - name: t\n    url: https://t.example/mcp\n    tools: [\"a\\\"b\"]\n")
    case(False, "no servers", "servers: []\n")
    rc |= self_test_session_key(template, shipped)
    return rc


def self_test_session_key(template: str, shipped: str) -> int:
    """TASK-1460: ATLAS_GATEWAY_SESSION_KEY_FILE, through main() as an operator runs it."""
    import contextlib
    import io
    rc = 0

    def say(good: bool, label: str, why: str = "") -> None:
        nonlocal rc
        print(f"self-test {'ok  ' if good else 'FAIL'} session   {label}" + (f"  ({why})" if why and not good else ""))
        rc |= 0 if good else 1

    cfg = validate(parse_yaml(shipped, "case"), ENV_OK)
    off = render(template, cfg, "case")
    say(off == render(template, cfg, "case", session_key=None) and "    key: disabled\n" in off
        and SESSION_OFF_BULLET in off and SESSION_OFF_BLOCK in off,
        "unset: the template's own `key: disabled` text, untouched")
    key = secrets.token_hex(32)  # generated per run: no key-shaped literal in the repo
    on = render(template, cfg, "case", session_key=key)
    say(f'    key: "{key}"\n' in on and "key: disabled" not in on and SESSION_ON_BULLET in on
        and on.replace(SESSION_ON_BULLET, SESSION_OFF_BULLET).replace(SESSION_ON_BLOCK.replace(
            "@@SESSION_KEY@@", _q(key)), SESSION_OFF_BLOCK) == off,
        "set: the key inline as config.session.key, and nothing else changed")

    def run(env_lines: list, key_text, key_mode: int = 0o600):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".env").write_text("\n".join(list(ENV_LINES) + env_lines) + "\n", encoding="utf-8")
            (root / "servers.yaml").write_text(shipped, encoding="utf-8")
            if key_text is not None:
                (root / "secrets").mkdir()
                kf = root / "secrets" / "gateway_session_key"
                kf.write_text(key_text, encoding="utf-8")
                os.chmod(kf, key_mode)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["--env", str(root / ".env"), "--servers", str(root / "servers.yaml"),
                             "--out", str(root / "gateway" / "agentgateway.yaml")])
            rendered = root / "gateway" / "agentgateway.yaml"
            text = rendered.read_text(encoding="utf-8") if rendered.exists() else ""
            mode = rendered.stat().st_mode & 0o777 if rendered.exists() else None
            return code, text, mode, out.getvalue() + err.getvalue()

    posix = os.name == "posix"
    code, text, mode, said = run([], None)
    say(code == 0 and text == render(template, cfg, "servers.yaml") and (not posix or mode == 0o644), "unset in .env: `disabled`, written 0644", f"exit {code} mode {mode}")
    code, text, mode, said = run([f"{SESSION_KEY_ENV}="], None)
    say(code == 0 and "    key: disabled\n" in text, "set to empty: the same as unset", f"exit {code}")
    secret = secrets.token_hex(32)
    code, text, mode, said = run([f"{SESSION_KEY_ENV}=./secrets/gateway_session_key"], secret + "\n")
    say(code == 0 and f'key: "{secret}"' in text and (not posix or mode == 0o600) and secret not in said,
        "a relative path from .env: the key inline, written 0600, never printed", f"exit {code} mode {mode}")
    for label, content, kmode in (("a 31-byte key", secret[:62], 0o600),
                                  ("a key that is not hex", secret[:63] + "g", 0o600),
                                  ("a key file group/world readable", secret, 0o644)):
        if kmode != 0o600 and not posix:
            continue
        code, text, mode, said = run([f"{SESSION_KEY_ENV}=./secrets/gateway_session_key"], content, kmode)
        say(code == 1 and not text and content not in said and secret[:40] not in said,
            f"refused, nothing written, the key not echoed: {label}", f"exit {code}: {said.strip()}"[:200])
    code, text, mode, said = run([f"{SESSION_KEY_ENV}=./secrets/missing"], None)
    say(code == 1 and not text, "refused: a key file that does not exist", f"exit {code}")
    return rc


ENV_LINES = tuple(f"{k}={v}" for k, v in ENV_OK.items())


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--servers", type=Path, default=DEFAULT_SERVERS)
    ap.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    ap.add_argument("--env", type=Path, default=Path(".env"))
    ap.add_argument("--out", type=Path, default=Path("gateway") / "agentgateway.yaml")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    try:
        if not args.env.is_file():
            raise Refused(f"{args.env} not found: run from deploy/oss/ after `cp .env.example .env`")
        env = read_env(args.env)
        cfg = validate(parse_yaml(args.servers.read_text(encoding="utf-8"), str(args.servers)), env)
        key = read_session_key(env.get(SESSION_KEY_ENV, ""), args.env.resolve().parent)
        try:
            shown = args.servers.resolve().relative_to(REPO).as_posix()
        except ValueError:
            shown = args.servers.name
        text = render(args.template.read_text(encoding="utf-8"), cfg, shown, session_key=key)
    except Refused as exc:
        print(f"render-gateway: refused: {exc}", file=sys.stderr)
        return 1
    write_atomic(args.out, text, mode=0o644 if key is None else 0o600)
    session = ("session IDs unencrypted (session key disabled)" if key is None else
               f"session IDs encrypted, key from {SESSION_KEY_ENV}; {args.out} written 0600")
    print(f"render-gateway: wrote {args.out} ({len(cfg['servers'])} server(s): "
          + ", ".join(f"{s['name']} {s['url']} tools={'all' if s['tools'] == ['*'] else len(s['tools'])}"
                      f"{' +Authorization' if s['forward'] else ''}" for s in cfg["servers"])
          + f"; CORS {', '.join(cfg['origins'])}; {session})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
