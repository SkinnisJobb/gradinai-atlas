#!/usr/bin/env python3
"""Generate the REST API reference (docs/oss/API.md) from the backend's own OpenAPI schema.

THE SOURCE is the FastAPI app in `backend/main.py`. The schema is built IN A CHILD PROCESS with
`app.openapi()`, the same call FastAPI's /openapi.json route would make. That route does not
exist and stays that way: `openapi_url`, `docs_url` and `redoc_url` are None (TASK-967), and
nothing here changes `backend/main.py` or opens a port. Calling the method needs no route.

THE CHILD PROCESS exists so that the environment the app needs at import never touches the caller:

- `backend/auth.py` and `backend/db.py` fail closed at import without `ATLAS_DB_URL`, the two
  peppers and `ATLAS_LOGIN_ORIGINS`. The child sets throwaway values for exactly those. The DB
  URL points at port 1 on the loopback address and is never used: `create_engine` does not
  connect, and `startup()` is never called.
- Every other `ATLAS_*` variable is REMOVED from the child's environment, so the page always
  shows the default route table and gate, whatever the shell that runs this has set.
- Before the import, the child replaces socket connect and name lookup with a function that
  raises. A future import-time connection fails this generator loudly instead of reaching out.

Some routes exist only when a setting turns them on. Each such setting gets one more child run
with that switch on, and the routes it adds are listed in their own section.

WHAT EACH ROUTE SHOWS: method, path, summary, the first paragraph of the handler's docstring,
parameters, request body, responses, and what the gate (backend/auth.py) asks of the caller:
public (`auth.PUBLIC_ROUTES`), signed in, and for a write the scope a token needs
(`auth.WRITE_SCOPE_ROUTES`) or the CSRF token a session needs (`auth.guard_write`). Routes are
grouped by the module that declares them (`backend/routers/<area>.py`, or `auth` for the
gate's own routes).

PERSONAL REFERENCES. Handler docstrings are written for maintainers and can name people, hosts
or addresses. Only the FIRST PARAGRAPH of a docstring is used, and the whole page is then
checked with the scrub gate's own generic patterns (`tools/scrub/scan.py`), plus its denylist
when `--denylist` or `SCRUB_DENYLIST_FILE` names one. Any match stops the run (exit 2) naming
the route and the pattern name, never the matched text, and nothing is written. The fix then
belongs in the docstring, not here, so the scrub gate is never the first to notice.

DETERMINISM. Routes, parameters, schemas and properties are sorted, the text is written with
`\\n` line endings in UTF-8, and `--check` normalises CRLF before comparing, so a Windows
checkout with `core.autocrlf=true` is not "stale". The output does depend on the FastAPI and
Pydantic versions, so run it with `backend/requirements-lock.txt` installed, as CI does.

    python tools/gen_api_ref.py                         # write docs/oss/API.md
    python tools/gen_api_ref.py --check                 # exit 1 if it is stale
    python tools/gen_api_ref.py --check --denylist F    # also check against a denylist file
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
OUT = ROOT / "docs" / "oss" / "API.md"
SCAN = ROOT / "tools" / "scrub" / "scan.py"
REL_OUT = "docs/oss/API.md"

# Throwaway values for the fail-closed imports. Never a real deployment's: nothing listens on
# port 1, and the peppers are 32 zero bytes. They change nothing on the page.
_ZERO_PEPPER = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
THROWAWAY_ENV = {
    "ATLAS_DB_URL": "postgresql+psycopg2://atlas:unused@127.0.0.1:1/unused",
    "ATLAS_PASSWORD_PEPPER": _ZERO_PEPPER,
    "ATLAS_TOKEN_PEPPER": _ZERO_PEPPER,
    "ATLAS_LOGIN_ORIGINS": "https://atlas.example.com",
    "ATLAS_BREACH_CHECK": "bundled",  # the bundled list: no network at import
}

# Settings that add routes. Each gets a child run with only that switch on; the routes it adds
# beyond the default table are documented in their own section.
OPT_IN = [
    ("ATLAS_CONFIG_WRITES", "1", "the configuration panel's write route"),
    ("ATLAS_RETRIEVAL", "on", "the semantic retrieval add-on (ADR 0044)"),
]

MUTATING = ("POST", "PUT", "PATCH", "DELETE")
METHOD_ORDER = {m: i for i, m in enumerate(("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"))}


# --------------------------------------------------------------------------- #
# the child: import the app, build the schema, print one JSON document
# --------------------------------------------------------------------------- #
def _no_network(*_a, **_k):
    raise RuntimeError("gen_api_ref: the app tried to open a network connection at import")


def child() -> int:
    import contextlib
    import socket

    socket.socket.connect = _no_network          # type: ignore[assignment]
    socket.socket.connect_ex = _no_network       # type: ignore[assignment]
    socket.create_connection = _no_network       # type: ignore[assignment]
    socket.getaddrinfo = _no_network             # type: ignore[assignment]

    sys.path.insert(0, str(BACKEND))
    # Import-time prints (startup notes, warnings) go to stderr; stdout carries only the JSON.
    with contextlib.redirect_stdout(sys.stderr):
        import main  # noqa: PLC0415
        import auth  # noqa: PLC0415
        from route_table import live_routes  # noqa: PLC0415

        app = main.app
        if app.openapi_url is not None or app.docs_url is not None or app.redoc_url is not None:
            raise SystemExit("gen_api_ref: backend/main.py exposes an OpenAPI or docs endpoint; "
                             "TASK-967 requires openapi_url, docs_url and redoc_url to be None")
        schema = app.openapi()

        modules: dict[str, str] = {}
        routes: list[list[str]] = []
        for r in live_routes(app):
            path = getattr(r, "path", None)
            if path is None:
                continue
            endpoint = getattr(r, "endpoint", None)
            mod = getattr(endpoint, "__module__", "") or ""
            for m in sorted(getattr(r, "methods", None) or ["*"]):
                modules[f"{m} {path}"] = mod
                routes.append([m, path])

    doc = {
        "schema": schema,
        "modules": modules,
        "routes": sorted(routes),
        "public": sorted(auth.PUBLIC_ROUTES),
        "write_scope": sorted([m, p, s] for (m, p), s in auth.WRITE_SCOPE_ROUTES.items()),
        "must_change": sorted(auth.MUST_CHANGE_ALLOWLIST),
    }
    sys.stdout.write(json.dumps(doc, sort_keys=True))
    return 0


def load(extra: dict[str, str] | None = None) -> dict:
    """Run the child with a clean `ATLAS_*` environment plus `extra`, and return its JSON."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_")}
    env.update(THROWAWAY_ENV)
    env.update(extra or {})
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_child"],
                          env=env, cwd=str(BACKEND), capture_output=True, text=True,
                          encoding="utf-8")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"gen_api_ref: importing the backend app failed (exit {proc.returncode})"
                         + (f" with {extra}" if extra else ""))
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def first_paragraph(text: str | None) -> str:
    """The first paragraph of a docstring, on one line. Nothing past the first blank line."""
    if not text:
        return ""
    para = re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0]
    return " ".join(para.split())


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def type_of(s: dict | None) -> str:
    """A short, readable type for a JSON schema fragment."""
    if not s:
        return "any"
    if "$ref" in s:
        return f"[{_ref_name(s['$ref'])}](#{_ref_name(s['$ref']).lower()})"
    if "const" in s:
        return f"`{json.dumps(s['const'])}`"
    if "enum" in s:
        return "one of " + ", ".join(f"`{json.dumps(v)}`" for v in s["enum"])
    for key in ("anyOf", "oneOf", "allOf"):
        if key in s:
            parts = []
            for sub in s[key]:
                t = type_of(sub)
                if t not in parts:
                    parts.append(t)
            return " or ".join(parts)
    t = s.get("type")
    if isinstance(t, list):
        return " or ".join(t)
    if t == "array":
        return f"array of {type_of(s.get('items'))}"
    if t == "object" and "additionalProperties" in s and isinstance(s["additionalProperties"], dict):
        return f"object of {type_of(s['additionalProperties'])}"
    if t:
        fmt = s.get("format")
        return f"{t} ({fmt})" if fmt else t
    return "any"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _default(s: dict) -> str:
    if "default" not in s:
        return ""
    return f"`{json.dumps(s['default'], sort_keys=True)}`"


def gate_text(method: str, path: str, gate: dict) -> str:
    if path in gate["public"]:
        text = ("Public: the gate admits a caller with no credential. A credential that is "
                "presented and invalid is still refused with 401.")
    else:
        text = "Signed in: a session cookie or an `Authorization: Bearer` token."
    if method in MUTATING:
        scope = gate["write_scope"].get((method, path))
        if scope:
            text += (f" Write: a token needs the `{scope}` scope; a session needs the CSRF "
                     "token from `GET /api/me` and an allowed Origin.")
        elif path.startswith("/auth/"):
            text += " The handler checks Origin and CSRF for this sign-in step itself."
        else:
            text += " No token scope is mapped for this write, so the gate refuses token callers."
    return text


def _group_name(module: str, path: str) -> str:
    if module.startswith("routers."):
        return module.split(".", 1)[1]
    if module:
        return module
    return path.strip("/").split("/", 1)[0] or "root"


def render_ops(doc: dict, keys: list[tuple[str, str]], gate: dict, heading: str) -> tuple[list[str], set[str]]:
    """Markdown for the given (method, path) operations, grouped by declaring module."""
    paths = doc["schema"].get("paths", {})
    groups: dict[str, list[tuple[str, str]]] = {}
    for m, p in keys:
        groups.setdefault(_group_name(doc["modules"].get(f"{m} {p}", ""), p), []).append((m, p))
    used_schemas: set[str] = set()
    out: list[str] = []
    for g in sorted(groups):
        out += [f"{heading} `{g}`", ""]
        for m, p in sorted(groups[g], key=lambda k: (k[1], METHOD_ORDER.get(k[0], 99), k[0])):
            op = paths.get(p, {}).get(m.lower())
            out.append(f"{heading}# `{m} {p}`")
            out.append("")
            if op is None:
                out += ["Not in the OpenAPI schema (`include_in_schema=False`).", "", f"**Auth:** {gate_text(m, p, gate)}", ""]
                continue
            out.append(f"**{op.get('summary', '')}.** {first_paragraph(op.get('description'))}".rstrip())
            out.append("")
            out.append(f"**Auth:** {gate_text(m, p, gate)}")
            out.append("")
            params = sorted(op.get("parameters", []),
                            key=lambda x: ({"path": 0, "query": 1, "header": 2, "cookie": 3}.get(x["in"], 9), x["name"]))
            if params:
                out += ["| Parameter | In | Type | Required | Default | Description |",
                        "|---|---|---|---|---|---|"]
                for x in params:
                    s = x.get("schema", {})
                    out.append("| `{}` | {} | {} | {} | {} | {} |".format(
                        x["name"], x["in"], _cell(type_of(s)), "yes" if x.get("required") else "no",
                        _cell(_default(s)), _cell(first_paragraph(x.get("description") or s.get("description")))))
                out.append("")
            body = op.get("requestBody")
            if body:
                for ctype, c in sorted(body.get("content", {}).items()):
                    s = c.get("schema", {})
                    for ref in re.findall(r'"\$ref": "([^"]+)"', json.dumps(s)):
                        used_schemas.add(_ref_name(ref))
                    req = "required" if body.get("required") else "optional"
                    out.append(f"**Request body** ({ctype}, {req}): {type_of(s)}")
                out.append("")
            out.append("**Responses:**")
            out.append("")
            for code in sorted(op.get("responses", {})):
                r = op["responses"][code]
                kinds = []
                for ctype, c in sorted(r.get("content", {}).items()):
                    s = c.get("schema", {})
                    for ref in re.findall(r'"\$ref": "([^"]+)"', json.dumps(s)):
                        used_schemas.add(_ref_name(ref))
                    kinds.append(f"{ctype}: {type_of(s)}" if s else ctype)
                tail = f" ({'; '.join(kinds)})" if kinds else ""
                out.append(f"- `{code}` {r.get('description', '')}{tail}")
            out.append("")
    return out, used_schemas


def render_schemas(schemas: dict, names: set[str]) -> list[str]:
    # Close over references between schemas.
    todo, seen = sorted(names), set()
    while todo:
        n = todo.pop()
        if n in seen or n not in schemas:
            continue
        seen.add(n)
        for ref in re.findall(r'"\$ref": "([^"]+)"', json.dumps(schemas[n])):
            todo.append(_ref_name(ref))
    out: list[str] = []
    for n in sorted(seen):
        s = schemas[n]
        out += [f"### {n}", ""]
        desc = first_paragraph(s.get("description"))
        if desc:
            out += [desc, ""]
        props = s.get("properties", {})
        if not props:
            out += [f"Type: {type_of({k: v for k, v in s.items() if k != 'description'})}", ""]
            continue
        required = set(s.get("required", []))
        out += ["| Field | Type | Required | Default | Description |", "|---|---|---|---|---|"]
        for f in sorted(props):
            p = props[f]
            out.append("| `{}` | {} | {} | {} | {} |".format(
                f, _cell(type_of(p)), "yes" if f in required else "no", _cell(_default(p)),
                _cell(first_paragraph(p.get("description")))))
        out.append("")
    return out


# The audience frontmatter every docs/oss page starts with (TASK-1429; checked by
# tools/check_docs_audience.py). Emitted here so --check compares the whole page.
FRONTMATTER = ["---", "audience: user", "---", ""]


def render(base: dict, extras: list[tuple[str, str, str, dict]]) -> str:
    def gate_of(doc: dict) -> dict:
        return {"public": set(doc["public"]),
                "write_scope": {(m, p): s for m, p, s in doc["write_scope"]}}

    base_keys = [(m, p) for m, p in base["routes"]]
    lines = [
        *FRONTMATTER,
        "# REST API reference",
        "",
        "<!-- GENERATED by tools/gen_api_ref.py from the backend's OpenAPI schema. "
        "Do not edit by hand: change the code and run `python tools/gen_api_ref.py`. -->",
        "",
        "> **Generated.** This page is produced by `tools/gen_api_ref.py` from the FastAPI app in "
        "`backend/main.py`. Edit the route, not this page. `python tools/gen_api_ref.py --check` "
        "fails when the page is out of date.",
        "",
        f"The default install serves **{len(base_keys)} routes** (method and path pairs). The app "
        "publishes no OpenAPI document, Swagger UI or ReDoc page: those endpoints are switched off, "
        "and this page is built from the schema in-process instead.",
        "",
        "## How access works",
        "",
        "One gate, an ASGI middleware in `backend/auth.py`, sits in front of every route, including "
        "paths that do not exist. It decides before any handler runs:",
        "",
        "- **Public routes** answer a caller with no credential. Every other route needs a signed-in "
        "browser session (cookie) or an `Authorization: Bearer` token. A caller with neither gets "
        "401 JSON, or a redirect to the login page if it is a browser asking for HTML.",
        "- **Never both.** A request carrying both a session cookie and a Bearer header is refused "
        "with 400.",
        "- **Writes.** A session caller must send the CSRF token from `GET /api/me` and an allowed "
        "Origin. A token caller is checked against the scope the route needs (`write` or `admin`); "
        "a write route with no scope mapped refuses tokens.",
        "- **Forced password change.** An account that must change its password reaches only the "
        "sign-in pages, the privacy page and the health probes until it does: "
        + ", ".join(f"`{p}`" for p in base["must_change"]) + ".",
        "- **Host allow-list.** A request for a host the install does not answer for is refused with "
        "421 on every path.",
        "",
        "The **Auth** line on each route states what the gate asks. A handler can refuse more: many "
        "routes check node visibility, and some are owner-only or refuse every token. The route "
        "description says so where it applies.",
        "",
        "Validation errors are 422 with an `HTTPValidationError` body. The response types are "
        "the ones FastAPI declares, which is JSON by default: routes that answer with an HTML "
        "page, a file or a redirect (the board, the sign-in pages, `/assets`, `/docs`) still show "
        "JSON here. The routes in the `legacy_v0` group answer 404 when `ATLAS_LEGACY_V0=0`; "
        "they stay registered, so they are listed.",
        "",
        "## Routes",
        "",
        "Grouped by the module that declares them: `backend/routers/<group>.py`, or `auth` for "
        "the sign-in routes the gate itself adds.",
        "",
    ]
    body, used = render_ops(base, base_keys, gate_of(base), "###")
    lines += body
    if extras:
        lines += ["## Routes that a setting turns on", "",
                  "These routes do not exist in a default install. Each appears only when its "
                  "setting is on.", ""]
        for name, value, why, doc in extras:
            have = set(base_keys)
            keys = [(m, p) for m, p in doc["routes"] if (m, p) not in have]
            lines += [f"### `{name}={value}`: {why}", ""]
            if not keys:
                lines += ["Adds no route.", ""]
                continue
            sub, sub_used = render_ops(doc, keys, gate_of(doc), "####")
            lines += sub
            used |= sub_used
            for n in sub_used:
                base["schema"].setdefault("components", {}).setdefault("schemas", {}).setdefault(
                    n, doc["schema"]["components"]["schemas"][n])
        lines += ["Sign-in providers configured with `ATLAS_AUTH_PROVIDERS` add "
                  "`GET /auth/<provider>/start` and `GET /auth/<provider>/callback` for each "
                  "redirect (OIDC) provider. Both are public.", ""]
    schemas = base["schema"].get("components", {}).get("schemas", {})
    if used:
        lines += ["## Schemas", "",
                  "The request and response bodies named above.", ""]
        lines += render_schemas(schemas, used)
    text = "\n".join(lines).rstrip("\n") + "\n"
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


# --------------------------------------------------------------------------- #
# personal-reference check, with the scrub gate's own patterns
# --------------------------------------------------------------------------- #
def scrub_check(text: str, denylist: str | None) -> list[str]:
    spec = importlib.util.spec_from_file_location("scrub_scan", SCAN)
    scan = importlib.util.module_from_spec(spec)
    sys.modules["scrub_scan"] = scan
    spec.loader.exec_module(scan)
    patterns = scan.generic_patterns()
    if denylist and Path(denylist).is_file() and Path(denylist).stat().st_size:
        try:
            patterns += scan.load_denylist(Path(denylist))
        except scan.ConfigError as exc:
            raise SystemExit(f"gen_api_ref: {exc}")
    allow = scan.load_allowlist(scan.DEFAULT_ALLOWLIST)
    problems = []
    route = "(page header)"
    for lineno, line in enumerate(text.split("\n"), 1):
        m = re.match(r"#{3,5} `([A-Z]+ [^`]+)`", line)
        if m:
            route = m.group(1)
        elif line.startswith("### "):
            route = line[4:]
        for p in patterns:
            if p.regex.search(line) and not scan.is_allowed(REL_OUT, p.name, allow):
                # Name the route and the pattern, never the matched text.
                problems.append(f"line {lineno} ({route}): matches {p.name}")
    return problems


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def build() -> str:
    base = load()
    extras = [(name, value, why, load({name: value})) for name, value, why in OPT_IN]
    return render(base, extras)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="fail if docs/oss/API.md is stale")
    ap.add_argument("--denylist", default=os.environ.get("SCRUB_DENYLIST_FILE") or None,
                    help="also check the page against this scrub denylist (default: $SCRUB_DENYLIST_FILE)")
    ap.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args._child:
        return child()

    text = build()
    problems = scrub_check(text, args.denylist)
    if problems:
        for p in problems:
            print(f"gen_api_ref: {p}", file=sys.stderr)
        print("gen_api_ref: personal references in the generated page. Fix the route's docstring "
              "(only its first paragraph is used); nothing was written.", file=sys.stderr)
        return 2

    if args.check:
        current = OUT.read_bytes().decode("utf-8").replace("\r\n", "\n") if OUT.exists() else None
        if current != text:
            print(f"gen_api_ref: {REL_OUT} is "
                  + ("missing" if current is None else "stale")
                  + "; run `python tools/gen_api_ref.py` and commit the result", file=sys.stderr)
            return 1
        print(f"gen_api_ref: up to date ({text.count(chr(10))} lines)")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"gen_api_ref: wrote {REL_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
