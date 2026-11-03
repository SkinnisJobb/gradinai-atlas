#!/usr/bin/env python3
"""Generate the baseload (backend/baseload_data.py): the world model a fresh install ships with.

The baseload describes Atlas's own functions as world nodes under `atlas-system`, beside the seven
handbook nodes of `backend/system_handbook.py`: the compose services, the capability areas, every
MCP tool, every API area, the board's views and the admin CLI (agent/plans/oss-baseload.md). Where
the code is the truth the nodes are GENERATED from it, so they cannot drift; prose a person writes
is AUTHORED in `tools/baseload/authored.json` and `tools/baseload/from_self_seed.json`.

THE SOURCES, and how each is read
  MCP tools          the live FastMCP registry, read the way tools/gen_tool_ref.py reads it (that
                     module's `registered_tools()`, in a child process: pinned environment, the
                     delete tool on, no feature disabled, httpx refusing every request). Groups are
                     descriptions.py's `# ---- <group>` comments (gen_tool_ref.groups_from_descriptions).
  tool -> API area   AST over mcp/app/mcp_server.py and mcp/app/tools_*.py: every string that starts
                     `/api/` in a tool's function and the module functions it reaches, matched to the
                     live route table. A path that matches no route stops the run.
  API areas          the live route table with each route's declaring module, from
                     tools/gen_api_ref.py's child (`load()`): once with the defaults, once per setting
                     that adds routes, and once with `github_link` off. One node per module.
  board views        the entries of frontend/v2/views/ (directories, and .js files that are not tests);
                     the panel's tabs from the `TABS` line of views/panel/panel.js.
  admin CLI          `admin.build_parser()`, introspected in a child process.
  compose services   the service keys and `profiles:` of deploy/oss/docker-compose.yml.
  configuration      `settings_catalog.all_settings()`, grouped per capability by authored globs.
  guides             docs/oss/** and clients/**, copied into node documents; relative links are
                     rewritten to "the document on node X", or to plain text.

THE CHECKS it runs (agent/plans/oss-baseload.md section 5.2)
  C1  `--check`: exit 1 when the committed module differs from a fresh render.
  C5  every text in the module and in tools/baseload/ is held to tools/export_self_seed.py's
      forbidden patterns (the scrub gate's generic ones, its denylist when `--denylist` or
      SCRUB_DENYLIST_FILE names one, home and secrets paths); exit 2 on a hit, naming the node and
      the pattern, never the text, and nothing is written.

Needs both requirement locks (backend and mcp), as the docs-freshness step in scrub.yml installs.

    python tools/gen_baseload.py                 # write backend/baseload_data.py
    python tools/gen_baseload.py --check         # exit 1 if it is stale (C1), 2 on a forbidden hit (C5)
    python tools/gen_baseload.py candidates      # refresh the paragraph candidates in from_self_seed.json
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
BACKEND = ROOT / "backend"
MCP_APP = ROOT / "mcp" / "app"
BASELOAD = TOOLS / "baseload"
AUTHORED_FILE = BASELOAD / "authored.json"
FROM_SELF_FILE = BASELOAD / "from_self_seed.json"
OUT = BACKEND / "baseload_data.py"
VIEWS = ROOT / "frontend" / "v2" / "views"
PANEL_JS = VIEWS / "panel" / "panel.js"
COMPOSE = ROOT / "deploy" / "oss" / "docker-compose.yml"
SELF_SEED = BACKEND / "self_seed_data.py"
COMMAND = "python tools/gen_baseload.py"
REL_OUT = "backend/baseload_data.py"

sys.path.insert(0, str(TOOLS))
import export_self_seed as ess  # noqa: E402  (forbidden patterns, the literal renderer, the profile reader)

PREFIX = "atlas-system"
ROOT_ID = "atlas-system"
HANDBOOK_IDS = ("atlas-system", "atlas-system-api", "atlas-system-mcp", "atlas-system-postgres",
                "atlas-system-gateway", "atlas-system-search", "atlas-system-retrieval")
MUTATING = ("POST", "PUT", "PATCH", "DELETE")
METHOD_ORDER = {m: i for i, m in enumerate(("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"))}

#: Route-adding settings, each one child run of gen_api_ref with only that switch on. The routes a
#: run adds over the default table carry the setting on their area's route table.
ROUTE_SWITCHES = [
    ("ATLAS_CONFIG_WRITES", "1"),
    ("ATLAS_RETRIEVAL", "on"),
    ("ATLAS_LANDING", "v2"),
]
#: Routes present by default that a feature switch removes.
FEATURE_SWITCHES = [("github_link", "off when `github_link` is in ATLAS_DISABLED_FEATURES")]


class GenError(SystemExit):
    def __init__(self, msg: str):
        super().__init__(f"gen_baseload: {msg}")


# --------------------------------------------------------------------------- #
# children: the MCP registry and the backend CLI/settings, each in its own process
# --------------------------------------------------------------------------- #
def _child_mcp() -> int:
    import gen_tool_ref  # noqa: PLC0415
    import contextlib
    with contextlib.redirect_stdout(sys.stderr):
        tools = gen_tool_ref.registered_tools()
    out = [{"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema or {}}
           for t in tools]
    sys.stdout.write(json.dumps(out, sort_keys=True))
    return 0


def _child_backend() -> int:
    import contextlib
    import socket
    import gen_api_ref  # noqa: PLC0415
    socket.socket.connect = gen_api_ref._no_network       # type: ignore[assignment]
    socket.create_connection = gen_api_ref._no_network    # type: ignore[assignment]
    socket.getaddrinfo = gen_api_ref._no_network          # type: ignore[assignment]
    sys.path.insert(0, str(BACKEND))
    with contextlib.redirect_stdout(sys.stderr):
        import admin  # noqa: PLC0415
        import settings_catalog  # noqa: PLC0415
        parser = admin.build_parser()
        groups = []
        for action in parser._actions:  # argparse has no public walk of subcommands
            if not getattr(action, "choices", None) or not hasattr(action, "_choices_actions"):
                continue
            helps = {a.dest: a.help or "" for a in action._choices_actions}
            for name, sub in action.choices.items():
                cmds = []
                for sa in sub._actions:
                    if getattr(sa, "choices", None) and hasattr(sa, "_choices_actions"):
                        shelps = {a.dest: a.help or "" for a in sa._choices_actions}
                        cmds = [{"name": n, "help": shelps.get(n, "")} for n in sa.choices]
                groups.append({"name": name, "help": helps.get(name, ""),
                               "description": sub.description or "", "commands": cmds})
        settings = [{"name": s.name, "summary": s.summary or "", "kind": s.kind,
                     "services": list(getattr(s, "services", ()) or ())}
                    for s in settings_catalog.all_settings()]
    sys.stdout.write(json.dumps({"cli": groups, "settings": settings}, sort_keys=True))
    return 0


def _run_child(flag: str, env_extra: dict | None = None) -> object:
    import gen_api_ref  # noqa: PLC0415
    env = {k: v for k, v in os.environ.items() if not k.startswith("ATLAS_")}
    env.update(gen_api_ref.THROWAWAY_ENV)
    env.update(env_extra or {})
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), flag], env=env,
                          cwd=str(BACKEND), capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:])
        raise GenError(f"the {flag} child failed (exit {proc.returncode}); install "
                       "backend/requirements-lock.txt and mcp/requirements-lock.txt")
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------- #
# the route table, per module
# --------------------------------------------------------------------------- #
def _area_key(module: str) -> str:
    """`routers.graph` -> `graph`, `auth` -> `auth`; anything else stops the run."""
    if module.startswith("routers."):
        return module[len("routers."):]
    if module == "auth":
        return "auth"
    raise GenError(f"a route is declared in module {module!r}; give it an API area (only "
                   "backend/routers/*.py and auth.py declare routes today)")


def collect_routes() -> dict:
    """{"routes": {(method, path): {"module", "condition"}}, "public", "write_scope"}."""
    import gen_api_ref  # noqa: PLC0415
    base = gen_api_ref.load()
    routes: dict = {}
    for key, mod in base["modules"].items():
        m, p = key.split(" ", 1)
        routes[(m, p)] = {"module": _area_key(mod), "condition": ""}
    write_scope = {(m, p): s for m, p, s in base["write_scope"]}
    for name, value in ROUTE_SWITCHES:
        run = gen_api_ref.load({name: value})
        for key, mod in run["modules"].items():
            m, p = key.split(" ", 1)
            if (m, p) not in routes:
                routes[(m, p)] = {"module": _area_key(mod), "condition": f"{name}={value}"}
        for m, p, s in run["write_scope"]:
            write_scope.setdefault((m, p), s)
    for feature, label in FEATURE_SWITCHES:
        run = gen_api_ref.load({"ATLAS_DISABLED_FEATURES": feature})
        present = {tuple(k.split(" ", 1)) for k in run["modules"]}
        for k, r in routes.items():
            if not r["condition"] and k not in present:
                r["condition"] = label
    return {"routes": routes, "public": set(base["public"]), "write_scope": write_scope}


def _segments(path: str) -> list[str]:
    return [s for s in path.split("?")[0].split("/") if s != ""]


def path_matches(template: str, route: str) -> bool:
    """`/api/node/{}` against `/api/node/{node_id}`; a `{name:path}` route segment takes the rest."""
    t, r = _segments(template), _segments(route)
    for i, seg in enumerate(r):
        if seg.startswith("{") and seg.endswith(":path}"):
            return len(t) >= i
        if i >= len(t):
            return False
        if seg.startswith("{") or t[i] == "{}":
            continue
        if seg != t[i]:
            return False
    return len(t) == len(r)


# --------------------------------------------------------------------------- #
# tool -> routes, by AST
# --------------------------------------------------------------------------- #
_METHOD_CALLS = {"get": "GET", "post": "POST", "patch": "PATCH", "put": "PUT", "delete": "DELETE",
                 "_post": "POST", "_patch": "PATCH", "_put": "PUT", "_delete": "DELETE"}


def _template(node) -> str | None:
    """The literal path of a str constant, f-string or `+` chain; a non-literal part is `{}`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _template(node.left), _template(node.right)
        return (left if left is not None else "{}") + (right if right is not None else "{}")
    return None


def _paths_in(fn) -> list[tuple[str, str]]:
    """[(method or '*', template)] for every `/api/` path passed as an argument in a function.

    Only call ARGUMENTS count, so a docstring, a comment-like message ("check on PUT /api/...")
    or the literal head of an f-string is never read as a call. The method is the HTTP verb of
    the call (`client.get`, `_post`, ...; `_raw("DELETE", path)`), or `*` when the callee is
    something else, which then matches the path under any method."""
    found: list[tuple[str, str]] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for i, arg in enumerate(node.args):
            t = _template(arg)
            if not (t and t.startswith("/api/")):
                continue
            if name in _METHOD_CALLS and i == 0:
                found.append((_METHOD_CALLS[name], t))
            elif name == "_raw" and i == 1 and isinstance(node.args[0], ast.Constant):
                found.append((str(node.args[0].value), t))
            else:
                found.append(("*", t))
    return found


def tool_paths() -> dict[str, list[tuple[str, str]]]:
    """{tool name: [(method, template)]} for every function decorated `@mcp.tool()`."""
    out: dict[str, list] = {}
    files = [MCP_APP / "mcp_server.py"] + sorted(MCP_APP.glob("tools_*.py"))
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for name, fn in funcs.items():
            if not any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                       and d.func.attr == "tool" for d in fn.decorator_list):
                continue
            seen, todo, paths = set(), [fn], []
            while todo:
                cur = todo.pop()
                if cur.name in seen:
                    continue
                seen.add(cur.name)
                paths += _paths_in(cur)
                for n in ast.walk(cur):
                    if isinstance(n, ast.Name) and n.id in funcs and n.id not in seen:
                        todo.append(funcs[n.id])
            out[name] = sorted(set(paths))
    return out


# --------------------------------------------------------------------------- #
# other sources
# --------------------------------------------------------------------------- #
def view_entries() -> list[str]:
    out = []
    for p in sorted(VIEWS.iterdir(), key=lambda p: p.name):
        if p.is_dir():
            out.append(p.name)
        elif p.suffix == ".js" and not p.name.endswith(".test.js"):
            out.append(p.stem)
    return out


def panel_tabs() -> list[str]:
    m = re.search(r"const TABS\b[^\[]*\[([^\]]*)\]", PANEL_JS.read_text(encoding="utf-8"))
    if not m:
        raise GenError("no `const TABS = [...]` line in frontend/v2/views/panel/panel.js")
    return re.findall(r'"([a-z0-9-]+)"', m.group(1))


def compose_services() -> list[tuple[str, list[str]]]:
    """[(service, profiles)] from the top-level `services:` block (two-space keys, `profiles:` lists)."""
    out: list[tuple[str, list[str]]] = []
    in_services = in_profiles = False
    for line in COMPOSE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\S", line):
            in_services = line.startswith("services:")
            continue
        if not in_services:
            continue
        m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if m:
            out.append((m.group(1), []))
            in_profiles = False
            continue
        if re.match(r"^    profiles:\s*$", line):
            in_profiles = True
            continue
        m = re.match(r"^      - ([A-Za-z0-9_-]+)\s*$", line)
        if in_profiles and m and out:
            out[-1][1].append(m.group(1))
            continue
        in_profiles = False
    return out


# --------------------------------------------------------------------------- #
# copied documents
# --------------------------------------------------------------------------- #
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n\n?", re.S)
_LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)")


def copy_doc(src_rel: str, located: dict[str, tuple[str, str]]) -> str:
    """A guide as a node document: frontmatter dropped, relative links rewritten.

    `located` maps a repo-relative source path to (node id, document title) for every copied
    file. A link to one of those becomes "<text> (document *Title* on node `id`)"; any other
    relative link becomes its text. `#anchor` and absolute http(s) links are kept. Fenced code
    is left alone."""
    src = ROOT / src_rel
    text = src.read_text(encoding="utf-8").replace("\r\n", "\n")
    text = _FRONTMATTER.sub("", text, count=1)
    base = Path(src_rel).parent

    def fix(m):
        bang, label, target = m.group(1), m.group(2), m.group(3)
        if target.startswith(("http://", "https://", "#", "mailto:")):
            return m.group(0) if not bang else label
        if bang:
            return label
        path = target.split("#", 1)[0]
        rel = os.path.normpath((base / path).as_posix()).replace("\\", "/")
        if rel in located:
            nid, title = located[rel]
            return f"{label} (document *{title}* on node `{nid}`)"
        return label

    out, fenced = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        out.append(line if fenced else _LINK.sub(fix, line))
    return "\n".join(out).rstrip("\n") + "\n"


def _file_for(title: str, used: set[str]) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "doc"
    name, n = f"{stem}.md", 2
    while name in used:
        name, n = f"{stem}-{n}.md", n + 1
    used.add(name)
    return name


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def _first_sentence(text: str) -> str:
    text = " ".join((text or "").split())
    m = re.match(r"(.+?[.?!])(\s|$)", text)
    return (m.group(1) if m else text)[:300]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def _attrs(source: str, frm: str, **extra) -> dict:
    a = {"baseload": {"source": source, "from": frm}}
    for k, v in extra.items():
        if v is None or v == "" or v == []:
            continue
        if k == "setting":
            a["setting"] = v
        else:
            a["baseload"][k] = v
    return a


def build(tools: list[dict], routes: dict, backend: dict) -> dict:
    A = json.loads(AUTHORED_FILE.read_text(encoding="utf-8"))
    F = json.loads(FROM_SELF_FILE.read_text(encoding="utf-8"))
    D = A["decisions"]
    nodes: list[dict] = []
    requires: list[list[str]] = []
    extras: dict[str, dict] = {}

    def add(n: dict) -> dict:
        n.setdefault("docs", [])
        nodes.append(n)
        return n

    def ident(kind: str, name: str) -> str:
        if D["identifier_names"] == "identifier":
            return name
        if D["identifier_names"] == "prose":
            return {"tool": "Tool ", "cli": "Command group "}[kind] + name
        raise GenError(f"decisions.identifier_names must be identifier or prose, not {D['identifier_names']!r}")

    # Every copied guide, so links between them can point at the node that holds the target.
    located: dict[str, tuple[str, str]] = {}
    for nid, ex in A["handbook_extras"].items():
        if nid.startswith("_"):
            continue
        for src, _k, title in ex.get("guides", []):
            located.setdefault(src, (nid, title))
    for c in A["capabilities"]:
        for src, _k, title in c.get("guides", []):
            located.setdefault(src, (c["id"], title))
    for pk in A["clients"]["packs"]:
        for src, _k, title in pk.get("guides", []):
            located.setdefault(src, (pk["id"], title))

    def guide_docs(nid: str, guides: list, used: set[str]) -> list[dict]:
        return [{"file": _file_for(title, used), "title": title, "kind": kind,
                 "content": copy_doc(src, located), "from": src} for src, kind, title in guides]

    settings = backend["settings"]

    def config_doc(globs: list[str], title_of: str) -> dict | None:
        if not globs:
            return None
        picked, seen = [], set()
        for g in globs:
            hits = [s for s in settings if fnmatch.fnmatchcase(s["name"], g)]
            if not hits:
                raise GenError(f"{title_of}: settings glob {g!r} matches no setting in settings_catalog")
            for s in hits:
                if s["name"] not in seen:
                    seen.add(s["name"])
                    picked.append(s)
        lines = ["# Configuration", "",
                 f"The settings of {title_of}, from `backend/settings_catalog.py`. Every setting, with "
                 "its default and where it is read, is in the configuration reference (CONFIG.md); "
                 "an owner sees the effective values in the board's Config tab.", "",
                 "| Setting | Kind | Read by | What it is |", "|---|---|---|---|"]
        for s in picked:
            lines.append(f"| `{s['name']}` | {s['kind']} | {', '.join(s['services'])} | {_cell(s['summary'])} |")
        return {"file": "configuration.md", "title": "Configuration", "kind": "runbook",
                "content": "\n".join(lines) + "\n"}

    # ---- handbook extras (documents, a setting) -------------------------------------------- #
    for nid, ex in A["handbook_extras"].items():
        if nid.startswith("_"):
            continue
        if nid not in HANDBOOK_IDS:
            raise GenError(f"handbook_extras names {nid!r}, which is not a handbook node")
        used: set[str] = set()
        e = {"docs": guide_docs(nid, ex.get("guides", []), used)}
        if ex.get("setting"):
            e["setting"] = ex["setting"]
        extras[nid] = e

    # ---- new services ------------------------------------------------------------------------ #
    for s in A["services"]:
        used: set[str] = set()
        docs = []
        cd = config_doc(s.get("settings", []), s["name"])
        if cd:
            docs.append(cd)
        n = {"id": s["id"], "type": s["type"], "parent": s["parent"], "name": s["name"],
             "summary": s["summary"], "attributes": _attrs("authored", "tools/baseload/authored.json",
                                                         setting=s.get("setting")),
             "docs": docs}
        if s.get("abstract"):
            n["abstract"] = s["abstract"]
        add(n)

    # ---- compose services must all be described -------------------------------------------- #
    svc_nodes = {k: v for k, v in A["compose_services"].items() if not k.startswith("_")}
    compose = compose_services()
    for svc, _profiles in compose:
        if svc not in svc_nodes:
            raise GenError(f"deploy/oss/docker-compose.yml service {svc!r} has no node: add it to "
                           "compose_services in tools/baseload/authored.json")
    stale = sorted(set(svc_nodes) - {s for s, _ in compose})
    if stale:
        raise GenError(f"compose_services names services the compose file does not have: {stale}")
    compose_of = {}
    for svc, profiles in compose:
        compose_of.setdefault(svc_nodes[svc], []).append(
            {"service": svc, "profiles": profiles})

    # ---- API areas ---------------------------------------------------------------------------- #
    by_area: dict[str, list] = {}
    for (m, p), r in routes["routes"].items():
        by_area.setdefault(r["module"], []).append((m, p, r["condition"]))
    areas_cfg = {k: v for k, v in A["api"]["areas"].items() if not k.startswith("_")}
    stale = sorted(set(areas_cfg) - set(by_area))
    if stale:
        raise GenError(f"api.areas names modules that declare no route: {stale}")
    area_id = {k: f"{PREFIX}-api-{k.replace('_', '-')}" for k in by_area}
    surface: dict[str, list[str]] = {}
    for key in sorted(by_area):
        cfg = areas_cfg.get(key, {})
        src = "backend/auth.py" if key == "auth" else f"backend/routers/{key}.py"
        rs = sorted(by_area[key], key=lambda x: (x[1], METHOD_ORDER.get(x[0], 9)))
        lines = [f"# {cfg.get('name') or 'Routes of ' + src}", "",
                 f"Declared in `{src}`; generated from the live route table. Access: *public* needs "
                 "no sign-in; *signed in* is any session or token; a write needs a session with its "
                 "CSRF token, or a token with the scope named.", "",
                 "| Method | Path | Access | Condition |", "|---|---|---|---|"]
        for m, p, cond in rs:
            if p in routes["public"]:
                access = "public"
            elif m in MUTATING:
                scope = routes["write_scope"].get((m, p))
                access = f"write (`{scope}` scope)" if scope else "write (session only)"
            else:
                access = "signed in"
            lines.append(f"| {m} | `{p}` | {access} | {cond} |")
        conds = {c for _, _, c in rs}
        only = next(iter(conds)) if len(conds) == 1 else ""
        # A setting is written as the assignment that switches the part on (`ATLAS_RETRIEVAL=on`),
        # never as a bare `ATLAS_*` name: a bare name in a backend module reads, to
        # settings_catalog.names_in_api, as the API reading that variable.
        setting = cfg.get("setting") or (only if only.startswith("ATLAS_") else None)
        name = cfg.get("name") or f"Routes of {src}"
        summary = cfg.get("summary") or f"The routes declared in {src}."
        add({"id": area_id[key], "type": "component", "parent": "atlas-system-api", "name": name,
             "summary": summary,
             "attributes": _attrs("generated", src, module=key, setting=setting),
             "docs": [{"file": "routes.md", "title": "Routes", "kind": "api",
                       "content": "\n".join(lines) + "\n"}]})
        if cfg.get("capability"):
            surface.setdefault(cfg["capability"], []).append(f"API area `{area_id[key]}` ({name})")

    # ---- MCP tool groups and tools ------------------------------------------------------------ #
    import gen_tool_ref  # noqa: PLC0415  (stdlib at import; render_tool and the group parser)
    reg = {t["name"]: t for t in tools}
    groups = gen_tool_ref.groups_from_descriptions()
    placed = {n for _, names in groups for n in names}
    rest = [n for n in reg if n not in placed]
    if rest:
        groups.append(("Other", rest))
    gcfg = A["mcp"]["groups"]
    tpaths = tool_paths()
    missing_ast = sorted(set(reg) - set(tpaths))
    if missing_ast:
        raise GenError(f"registered tools with no `@mcp.tool()` function found by AST: {missing_ast}")
    tool_areas: dict[str, list[str]] = {}
    tool_calls: dict[str, list[tuple[str, str, str]]] = {}
    all_routes = list(routes["routes"].items())
    for tname in reg:
        areas, calls = [], []
        for method, tmpl in tpaths[tname]:
            hits = [(m, p, r["module"]) for (m, p), r in all_routes
                    if path_matches(tmpl, p) and (method == "*" or m == method)]
            if not hits:
                raise GenError(f"tool {tname} calls {method} {tmpl}, which matches no route")
            for m, p, mod in hits:
                calls.append((m, p, mod))
                if area_id[mod] not in areas:
                    areas.append(area_id[mod])
        tool_areas[tname] = sorted(areas)
        tool_calls[tname] = sorted(set(calls), key=lambda c: (c[1], METHOD_ORDER.get(c[0], 9)))
    per_tool = D["tool_nodes"] == "per_tool"
    if D["tool_nodes"] not in ("per_tool", "per_group"):
        raise GenError(f"decisions.tool_nodes must be per_tool or per_group, not {D['tool_nodes']!r}")
    for title, names in groups:
        present = [n for n in names if n in reg]
        if not present:
            continue
        g = gcfg.get(title, {})
        gid = f"{PREFIX}-mcp-{g.get('slug') or _slug(title)}"
        lines = [f"# {g.get('name', title + ' tools')}", "",
                 "Generated from the tools atlas-mcp registers (`mcp/app/descriptions.py`). The full "
                 "reference is the MCP tools page (MCP-TOOLS.md).", "",
                 "| Tool | What it is for |", "|---|---|"]
        for n in present:
            lines.append(f"| `{n}` | {_cell(_first_sentence(reg[n]['description']))} |")
        gattrs = _attrs("generated", "mcp/app/descriptions.py", group=title, tools=present)
        add({"id": gid, "type": "component", "parent": "atlas-system-mcp",
             "name": g.get("name", f"{title} tools"),
             "summary": g.get("summary") or f"The {title.lower()} tools.",
             "attributes": gattrs,
             "docs": [{"file": "tools.md", "title": "Tools", "kind": "api", "content": "\n".join(lines) + "\n"}]})
        if not per_tool:
            for a in sorted({a for n in present for a in tool_areas[n]}):
                requires.append([gid, a])
            continue
        for n in present:
            t = reg[n]
            ref = gen_tool_ref.render_tool(types.SimpleNamespace(
                name=n, description=t["description"], inputSchema=t["inputSchema"]))
            ref[0] = f"# `{n}`"
            ref += ["## Routes it calls", ""]
            ref += [f"- {m} `{p}` (`{area_id[mod]}`)" for m, p, mod in tool_calls[n]] or ["None."]
            tid = f"{PREFIX}-tool-{n.removeprefix('atlas_').replace('_', '-')}"
            add({"id": tid, "type": "component", "parent": gid, "name": ident("tool", n),
                 "summary": _first_sentence(t["description"]),
                 "attributes": _attrs("generated", "mcp/app/descriptions.py", tool=n,
                                      setting=A["mcp"]["settings"].get(n)),
                 "docs": [{"file": "reference.md", "title": "Reference", "kind": "api",
                           "content": "\n".join(ref).rstrip("\n") + "\n"}]})
            for a in tool_areas[n]:
                requires.append([tid, a])

    # ---- the board ------------------------------------------------------------------------------ #
    B = A["board"]
    add({"id": B["node"]["id"], "type": "component", "parent": ROOT_ID, "name": B["node"]["name"],
         "summary": B["node"]["summary"], "abstract": B["node"]["abstract"],
         "attributes": _attrs("authored", "tools/baseload/authored.json")})
    vcfg = {k: v for k, v in B["views"].items() if not k.startswith("_")}
    entries = view_entries()
    stale = sorted(set(vcfg) - set(entries))
    if stale:
        raise GenError(f"board.views names entries frontend/v2/views/ does not have: {stale}")
    for e in entries:
        cfg = vcfg.get(e, {})
        vid = f"{PREFIX}-board-{e}"
        is_dir = (VIEWS / e).is_dir()
        frm = f"frontend/v2/views/{e}/" if is_dir else f"frontend/v2/views/{e}.js"
        add({"id": vid, "type": "component", "parent": B["node"]["id"],
             "name": cfg.get("name") or e.replace("-", " ").capitalize(),
             "summary": cfg.get("summary") or f"The board view in {frm}.",
             "attributes": _attrs("generated", frm, view=e)})
        if e == "panel":
            tabs = panel_tabs()
            stale = sorted(set(B["tabs"]) - set(tabs))
            if stale:
                raise GenError(f"board.tabs names tabs panel.js's TABS does not have: {stale}")
            for tab in tabs:
                add({"id": f"{vid}-{tab}", "type": "component", "parent": vid,
                     "name": f"{tab.replace('-', ' ').capitalize()} tab",
                     "summary": B["tabs"].get(tab) or f"The {tab} tab of the node panel.",
                     "attributes": _attrs("generated", "frontend/v2/views/panel/panel.js", tab=tab)})
    add({"id": f"{PREFIX}-board-legacy", "type": "component", "parent": B["node"]["id"],
         "name": B["legacy"]["name"], "summary": B["legacy"]["summary"],
         "attributes": _attrs("authored", "tools/baseload/authored.json")})

    # ---- the admin CLI -------------------------------------------------------------------------- #
    C = A["cli"]
    add({"id": C["node"]["id"], "type": "component", "parent": ROOT_ID, "name": C["node"]["name"],
         "summary": C["node"]["summary"], "abstract": C["node"]["abstract"],
         "attributes": _attrs("authored", "tools/baseload/authored.json")})
    cli_names = [g["name"] for g in backend["cli"]]
    stale = sorted(set(C["groups"]) - set(cli_names))
    if stale:
        raise GenError(f"cli.groups names atlas-admin groups build_parser() does not have: {stale}")
    for g in backend["cli"]:
        cid = f"{C['node']['id']}-{g['name']}"
        lines = [f"# atlas-admin {g['name']}", "", g["help"] + ("." if g["help"] and not g["help"].endswith(".") else ""), ""]
        if g["commands"]:
            lines += ["| Command | What it does |", "|---|---|"]
            lines += [f"| `atlas-admin {g['name']} {c['name']}` | {_cell(c['help'])} |" for c in g["commands"]]
        else:
            lines += [f"`atlas-admin {g['name']}` takes no subcommand."]
        lines += ["", f"Every option: `atlas-admin {g['name']} --help`, inside the atlas-api container."]
        add({"id": cid, "type": "component", "parent": C["node"]["id"],
             "name": ident("cli", f"atlas-admin {g['name']}"),
             "summary": (g["help"][:1].upper() + g["help"][1:]) if g["help"] else f"atlas-admin {g['name']}.",
             "attributes": _attrs("generated", "backend/admin.py", command=g["name"]),
             "docs": [{"file": "commands.md", "title": "Commands", "kind": "runbook",
                       "content": "\n".join(lines) + "\n"}]})
        if C["groups"].get(g["name"]):
            surface.setdefault(C["groups"][g["name"]], []).append(
                f"command group `atlas-admin {g['name']}` (`{cid}`)")

    # ---- client packs --------------------------------------------------------------------------- #
    K = A["clients"]
    add({"id": K["node"]["id"], "type": "component", "parent": ROOT_ID, "name": K["node"]["name"],
         "summary": K["node"]["summary"], "attributes": _attrs("authored", "tools/baseload/authored.json")})
    for pk in K["packs"]:
        used: set[str] = set()
        add({"id": pk["id"], "type": "component", "parent": K["node"]["id"], "name": pk["name"],
             "summary": pk["summary"], "attributes": _attrs("authored", "tools/baseload/authored.json"),
             "docs": guide_docs(pk["id"], pk.get("guides", []), used)})

    # ---- capabilities ---------------------------------------------------------------------------- #
    for c in A["capabilities"]:
        used = {"overview.md", "configuration.md"}
        lines = [f"# {c['name']}", "", c["summary"], ""]
        if c.get("abstract"):
            lines += [c["abstract"], ""]
        lines += ["## Surface", "",
                  "Generated: the parts of Atlas that serve this capability.", ""]
        lines += [f"- {s}" for s in sorted(surface.get(c["id"], []))] or ["- None listed."]
        docs = [{"file": "overview.md", "title": "Overview", "kind": "architecture",
                 "content": "\n".join(lines) + "\n"}]
        cd = config_doc(c.get("settings", []), c["name"])
        if cd:
            docs.append(cd)
        docs += guide_docs(c["id"], c.get("guides", []), used)
        n = {"id": c["id"], "type": "capability", "parent": D["capability_parent"], "name": c["name"],
             "summary": c["summary"], "attributes": _attrs("authored", "tools/baseload/authored.json"),
             "docs": docs}
        if c.get("abstract"):
            n["abstract"] = c["abstract"]
        add(n)

    # ---- the compose service each node describes -------------------------------------------------- #
    for n in nodes:
        if n["id"] in compose_of:
            n["attributes"]["baseload"]["compose"] = compose_of[n["id"]]
    for nid in HANDBOOK_IDS:
        prof = [p for c in compose_of.get(nid, []) for p in c["profiles"]]
        if prof:
            extras.setdefault(nid, {"docs": []})
            extras[nid].setdefault("setting", "COMPOSE_PROFILES=" + ",".join(prof))

    # ---- authored requires edges ------------------------------------------------------------------- #
    for src, dst, _why in A["requires"]:
        requires.append([src, dst])

    # ---- intents (from the self seed, reviewed) ------------------------------------------------------ #
    profile = ess.read_profile(SELF_SEED.read_text(encoding="utf-8"))
    by_key = {f"{i['node_id']}:{i['slug']}": i for i in profile["intents"]}
    ship = set(F["ship_intent_groups"])
    intents = []
    for it in F["intents"]:
        src = by_key.get(it["from"])
        if src is None:
            raise GenError(f"from_self_seed.json intent {it['from']!r} is not in backend/self_seed_data.py")
        if it["group"] not in ship:
            continue
        statement = src["statement"]
        for old, new in it.get("edits", []):
            if old not in statement:
                raise GenError(f"intent {it['from']}: an edit no longer applies (the statement changed); "
                               "review it in from_self_seed.json")
            statement = statement.replace(old, new)
        intents.append({"node_id": it["to"], "slug": src["slug"], "kind": src["kind"],
                        "title": src["title"], "statement": statement})

    # ---- reviewed paragraphs ----------------------------------------------------------------------- #
    prose = _self_seed_paragraphs(profile, F)
    for p in F.get("paragraphs", []):
        if p.get("review") != "yes":
            continue
        text = prose.get(p["sha"])
        if text is None:
            raise GenError(f"paragraph {p['sha']} (yes) is no longer in the self seed; re-run candidates")
        target = next((n for n in nodes if n["id"] == p["to"]), None)
        if target is None:
            raise GenError(f"paragraph {p['sha']}: target {p['to']!r} is not a baseload node "
                           "(handbook nodes take no paragraphs)")
        field = p.get("into", "detail")
        target[field] = (target.get(field, "") + "\n\n" + text).strip()

    # ---- shape checks ------------------------------------------------------------------------------- #
    ids = list(HANDBOOK_IDS) + [n["id"] for n in nodes]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise GenError(f"duplicate node ids: {dup}")
    known = set(HANDBOOK_IDS)
    for n in nodes:
        if not n["id"].startswith(PREFIX):
            raise GenError(f"node id {n['id']!r} does not start with {PREFIX}")
        if n["parent"] not in known:
            raise GenError(f"node {n['id']}: parent {n['parent']!r} is not an earlier node")
        if not (n.get("summary") or "").strip():
            raise GenError(f"node {n['id']} has no summary")
        known.add(n["id"])
    req = sorted({(s, d) for s, d in requires})
    for s, d in req:
        if s not in known or d not in known:
            raise GenError(f"requires edge {s} -> {d} has an endpoint that is not a node")
    for it in intents:
        if it["node_id"] not in known:
            raise GenError(f"intent {it['slug']} lands on {it['node_id']!r}, not a node")

    return {
        "nodes": nodes,
        "edges": [{"src": s, "edge_type": "requires", "dst": d} for s, d in req],
        "intents": intents,
        "handbook_extras": {k: extras[k] for k in sorted(extras)},
        "compose": {svc: svc_nodes[svc] for svc, _p in compose},
    }


# --------------------------------------------------------------------------- #
# self-seed paragraphs (B5)
# --------------------------------------------------------------------------- #
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _self_seed_paragraphs(profile: dict, F: dict) -> dict[str, str]:
    """{sha: paragraph} for every abstract/detail paragraph of a mapped self-seed node."""
    out = {}
    for n in profile["nodes"]:
        if n["id"] not in F["node_map"]:
            continue
        for field in ("abstract", "detail"):
            for para in re.split(r"\n\s*\n", n.get(field) or ""):
                para = para.strip()
                if para:
                    out[_sha(para)] = para
    return out


def candidates() -> int:
    F = json.loads(FROM_SELF_FILE.read_text(encoding="utf-8"))
    profile = ess.read_profile(SELF_SEED.read_text(encoding="utf-8"))
    patterns, _state = ess.load_patterns(None)
    estate = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in F["estate_terms"]) + r")\b")
    decided = {p["sha"]: p for p in F.get("paragraphs", [])}
    out = []
    for n in profile["nodes"]:
        if n["id"] not in F["node_map"]:
            continue
        for field in ("abstract", "detail"):
            for para in re.split(r"\n\s*\n", n.get(field) or ""):
                para = para.strip()
                if not para or ess.hits(para, patterns) or estate.search(para):
                    continue
                sha = _sha(para)
                prev = decided.get(sha, {})
                words = " ".join(para.split())
                out.append({"sha": sha, "from": n["id"], "field": field, "to": F["node_map"][n["id"]],
                            "into": prev.get("into", field), "review": prev.get("review", "pending"),
                            "preview": (words[:90].rsplit(" ", 1)[0] + " ...") if len(words) > 90 else words})
    F["paragraphs"] = out
    with open(FROM_SELF_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(F, indent=2, ensure_ascii=False) + "\n")
    print(f"gen_baseload: {len(out)} candidate paragraph(s) in {FROM_SELF_FILE.relative_to(ROOT).as_posix()}; "
          f"{sum(1 for p in out if p['review'] == 'yes')} marked yes")
    return 0


# --------------------------------------------------------------------------- #
# render and check
# --------------------------------------------------------------------------- #
HEADER = '''"""The baseload: the world model a fresh install ships with. GENERATED, DO NOT EDIT.

Rendered by `tools/gen_baseload.py` from the code (the MCP tool registry, the route table, the board's
views, the admin CLI, the OSS compose file, the settings catalogue, the OSS guides) and from the
reviewed data in `tools/baseload/`. Regenerate with `python tools/gen_baseload.py`; CI runs
`--check`. Python data rather than a file read at boot: boot opens no file (backend/CLAUDE.md).

`system_node.py` seeds it after the seven handbook nodes of `system_handbook.py`, with the same
rule: a node that exists is never touched. Every node's `attributes.baseload` names its source.

  NODES            the nodes after the handbook set, parents first; each has its `contains` parent.
  EDGES            the `requires` edges (object -> object, a declared pair).
  INTENTS          the intents that ship (tools/baseload/from_self_seed.json).
  HANDBOOK_EXTRAS  documents and a setting for handbook nodes, written only with that node.
  COMPOSE          every deploy/oss/docker-compose.yml service and the node that describes it.
"""
'''

SECTIONS = (("nodes", "NODES"), ("edges", "EDGES"), ("intents", "INTENTS"),
            ("handbook_extras", "HANDBOOK_EXTRAS"), ("compose", "COMPOSE"))


def render(data: dict) -> str:
    text = HEADER
    for key, name in SECTIONS:
        text += f"\n{name} = " + ess._py(data[key], 0) + "\n"
    return text


def read_module(text: str) -> dict:
    """The module's assignments, as literals: nothing in it is executed."""
    out = {}
    for stmt in ast.parse(text).body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            out[getattr(stmt.targets[0], "id", "")] = ast.literal_eval(stmt.value)
    return out


def texts(data: dict):
    """(where, text, copied) for every string the module carries. `copied` marks a document copied
    from a file the scrub gate already enforces on (docs/oss/*, clients/*)."""
    for n in data["nodes"]:
        for f in ("name", "summary", "abstract", "detail"):
            if n.get(f):
                yield f"{n['id']}.{f}", n[f], False
        for d in n.get("docs", []):
            yield f"{n['id']}/{d['file']}", d["content"], bool(d.get("from"))
    for it in data["intents"]:
        yield f"intent {it['node_id']}:{it['slug']}", it["title"] + "\n" + it["statement"], False
    for nid, ex in data["handbook_extras"].items():
        for d in ex.get("docs", []):
            yield f"{nid}/{d['file']}", d["content"], bool(d.get("from"))


def forbidden(data: dict, denylist: str | None) -> list[str]:
    """C5: `where: pattern` for each forbidden-pattern hit in the module or tools/baseload/.

    Authored and generated text gets every pattern export_self_seed.py holds the self seed to
    (the scrub gate's, plus home-directory and secrets paths). A copied guide gets the scrub
    gate's own patterns only: it is product documentation that shows example paths such as
    `~/atlas` on purpose, and its source file is already scrub-enforced."""
    patterns, _state = ess.load_patterns(denylist)
    extra = set(ess.EXTRA_PATTERNS)
    scrub_only = [p for p in patterns if p.name not in extra]
    probs = [f"{where}: {p.name}" for where, text, copied in texts(data)
             for p in (scrub_only if copied else patterns) if p.regex.search(text or "")]
    for f in sorted(BASELOAD.glob("*")):
        if f.is_file():
            probs += [f"{f.relative_to(ROOT).as_posix()}: {name}"
                      for name in ess.hits(f.read_text(encoding="utf-8"), patterns)]
    return probs


def generate() -> tuple[dict, str]:
    tools = _run_child("--_child-mcp")
    backend = _run_child("--_child-backend")
    routes = collect_routes()
    data = build(tools, routes, backend)
    text = render(data)
    sys.path.insert(0, str(BACKEND))
    import settings_catalog  # noqa: PLC0415  (standard library only, as gen_config_ref imports it)
    bare = settings_catalog.names_in_source(text)
    if bare:
        raise GenError(f"the module would carry bare ATLAS_* literals {sorted(bare)}, which "
                       "settings_catalog.names_in_api counts as the API reading them; write the "
                       "switching assignment instead (NAME=value)")
    if read_module(text) != {name: data[key] for key, name in SECTIONS}:
        raise GenError("the rendered module does not read back equal to the built data")
    return data, text


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8").replace("\r\n", "\n")
    except FileNotFoundError:
        return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--_child-mcp"]:
        return _child_mcp()
    if argv[:1] == ["--_child-backend"]:
        return _child_backend()
    if argv[:1] == ["candidates"]:
        return candidates()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="fail if backend/baseload_data.py is stale")
    ap.add_argument("--denylist", help="a scrub denylist file (default: SCRUB_DENYLIST_FILE)")
    args = ap.parse_args(argv)
    data, text = generate()
    probs = forbidden(data, args.denylist)
    if probs:
        for p in probs:
            print(f"gen_baseload: forbidden pattern: {p}", file=sys.stderr)
        print("gen_baseload: fix the source text (authored.json, the guide, the docstring); nothing written",
              file=sys.stderr)
        return 2
    if args.check:
        if _read(OUT) != text:
            print(f"gen_baseload: {REL_OUT} is stale; run `{COMMAND}` and commit the result", file=sys.stderr)
            return 1
        print(f"gen_baseload: up to date ({REL_OUT}, {len(data['nodes'])} nodes, "
              f"{len(data['edges'])} requires edges, {len(data['intents'])} intents)")
        return 0
    if _read(OUT) == text:
        print(f"gen_baseload: {REL_OUT} unchanged")
        return 0
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"gen_baseload: wrote {REL_OUT} ({len(data['nodes'])} nodes, {len(data['edges'])} requires "
          f"edges, {len(data['intents'])} intents)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
