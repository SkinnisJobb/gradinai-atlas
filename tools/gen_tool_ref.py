#!/usr/bin/env python3
"""Generate the MCP tool reference (docs/oss/MCP-TOOLS.md) from the tools atlas-mcp registers.

THE SOURCE is the running server's own tool list, not a re-parse of the code: this script imports
`mcp/app/mcp_server.py` in-process, exactly as the container does, and asks FastMCP for its tools
with `FastMCP.list_tools()`, the call that answers an MCP client's `tools/list`. So every name,
description and input schema on the page is what a connected agent is sent. Descriptions come
from `mcp/app/descriptions.py` (mcp_server.py refuses to register a tool without one); the page
groups the tools the way that file's `# ---- <group>` comments do, in that file's order.

THE ENVIRONMENT is pinned, so the page does not depend on the shell that runs it: every `ATLAS_*`
variable is dropped, then `ATLAS_BASE_URL` gets a placeholder, `ATLAS_MCP_ENABLE_DELETE` is set
and no feature is disabled, so the conditional tools (`atlas_delete`, `atlas_save_work`) are
listed too, with a note saying when they are offered. `httpx` is patched to refuse any request,
which proves registration talks to nobody.

Needs what mcp/ already depends on (`pip install -r mcp/requirements.txt`); nothing else.

    python tools/gen_tool_ref.py           # write docs/oss/MCP-TOOLS.md
    python tools/gen_tool_ref.py --check   # exit 1 if it is stale
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = ROOT / "mcp"
DESCRIPTIONS_FILE = MCP_DIR / "app" / "descriptions.py"
OUT = ROOT / "docs" / "oss" / "MCP-TOOLS.md"
COMMAND = "python tools/gen_tool_ref.py"

#: Tools the server offers only under a condition. Rendered under the tool's heading. A key
#: that is not a registered tool fails the run, so a renamed tool cannot leave a stale note.
CONDITIONS = {
    "atlas_delete": "Offered only when `ATLAS_MCP_ENABLE_DELETE` is set on atlas-mcp. Off by "
                    "default; the caller also needs the `admin` scope.",
    "atlas_save_work": "Offered unless the `save_work` feature is listed in "
                       "`ATLAS_DISABLED_FEATURES`.",
}

_GROUP_RE = re.compile(r"^\s*#\s*-{2,}\s*(.+?)\s*$")
_KEY_RE = re.compile(r'^\s*"([a-z_][a-z0-9_]*)"\s*:')


def groups_from_descriptions() -> list[tuple[str, list[str]]]:
    """[(group title, [tool names])] in descriptions.py's order, from its `# ---- x` comments."""
    groups: list[tuple[str, list[str]]] = []
    for line in DESCRIPTIONS_FILE.read_text(encoding="utf-8").splitlines():
        g = _GROUP_RE.match(line)
        if g:
            title = g.group(1).split(" (")[0].strip()
            groups.append((title[:1].upper() + title[1:], []))
            continue
        k = _KEY_RE.match(line)
        if k and groups:
            groups[-1][1].append(k.group(1))
    return groups


def registered_tools() -> list:
    """The server's tools as an MCP client sees them (`mcp.types.Tool`), via FastMCP itself."""
    for name in [n for n in os.environ if n.startswith("ATLAS_")]:
        del os.environ[name]
    os.environ["ATLAS_BASE_URL"] = "https://atlas.invalid"
    os.environ["ATLAS_MCP_ENABLE_DELETE"] = "1"
    sys.path.insert(0, str(MCP_DIR))
    try:
        import httpx
    except ImportError as e:  # pragma: no cover - environment problem, not a stale page
        sys.exit(f"gen_tool_ref: {e}; install mcp/requirements.txt first")

    def _refuse(*_a, **_k):
        raise RuntimeError("gen_tool_ref: registering the tools must not make a network call")

    httpx.Client.request = _refuse
    httpx.AsyncClient.request = _refuse
    with warnings.catch_warnings():
        # A sentinel default (omitted != null) cannot go into JSON Schema; pydantic says so.
        warnings.simplefilter("ignore")
        try:
            from app import mcp_server
        except ImportError as e:
            sys.exit(f"gen_tool_ref: {e}; install mcp/requirements.txt first")
        return asyncio.run(mcp_server.mcp.list_tools())


def _type(schema: dict) -> str:
    if "anyOf" in schema:
        return " | ".join(_type(s) for s in schema["anyOf"])
    t = schema.get("type")
    if t == "array":
        items = schema.get("items")
        return f"array of {_type(items)}" if items else "array"
    if "enum" in schema:
        return " | ".join(f"`{v!r}`" if not isinstance(v, str) else f'`"{v}"`' for v in schema["enum"])
    if t is None and "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    return t or "any"


def _default(prop: dict, required: bool) -> str:
    if required:
        return ""
    if "default" not in prop:
        return "*omitted*"
    v = prop["default"]
    if v is None:
        return "`null`"
    if isinstance(v, bool):
        return f"`{str(v).lower()}`"
    if isinstance(v, str):
        return f'`"{v}"`'
    return f"`{v}`"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_tool(tool) -> list[str]:
    out = [f"### `{tool.name}`", ""]
    if tool.name in CONDITIONS:
        out += [f"> {CONDITIONS[tool.name]}", ""]
    # `<` would open an HTML tag in markdown (set_<field> would lose "<field>").
    out += [(tool.description or "").replace("<", "\\<"), ""]
    schema = tool.inputSchema or {}
    props: dict = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    if not props:
        out += ["No parameters.", ""]
        return out
    out += ["| Parameter | Type | Required | Default |", "|---|---|---|---|"]
    for name, prop in props.items():
        req = name in required
        out.append(f"| `{name}` | {_cell(_type(prop))} | {'yes' if req else 'no'} | "
                   f"{_cell(_default(prop, req))} |")
    out.append("")
    return out


# The audience frontmatter every docs/oss page starts with (TASK-1429; checked by
# tools/check_docs_audience.py). Emitted here so --check compares the whole page.
FRONTMATTER = ["---", "audience: user", "---", ""]


def render() -> str:
    tools = {t.name: t for t in registered_tools()}
    unknown = sorted(set(CONDITIONS) - set(tools))
    if unknown:
        sys.exit(f"gen_tool_ref: CONDITIONS names unregistered tool(s): {', '.join(unknown)}")
    groups = groups_from_descriptions()
    placed = {n for _, names in groups for n in names}
    rest = [n for n in tools if n not in placed]
    if rest:
        groups.append(("Other", rest))

    lines = [
        *FRONTMATTER,
        "<!-- GENERATED by tools/gen_tool_ref.py from the tools atlas-mcp registers. DO NOT EDIT. -->",
        f"<!-- Regenerate: `{COMMAND}`. Check: `{COMMAND} --check`. -->",
        "",
        "# MCP tools",
        "",
        f"> **Generated, do not edit.** This page is produced by `{COMMAND}` from the tools the "
        "atlas-mcp server registers with FastMCP, read back the way an MCP client's `tools/list` "
        "sees them. To change a tool, change `mcp/app/` (descriptions live in "
        "`mcp/app/descriptions.py`) and regenerate.",
        "",
        "For how an agent connects to Atlas and the conventions it is expected to follow, see "
        "[Agents](guides/agents.md). This page is the reference: each tool's name, the description "
        "the server sends, and its input schema.",
        "",
        "Every tool returns the same envelope: `{\"ok\": true, \"result\": ...}` on success, "
        "`{\"ok\": false, \"error\": {\"type\": ..., \"message\": ...}}` on failure. A description "
        "ending in *More: tools guide.* points to the detailed tools guide, which an agent reads "
        "with `atlas_doc_read` (see that tool).",
        "",
        "In the tables, *omitted* means the parameter has no default in the schema: leaving it out "
        "is not the same as passing `null` (for example, `null` clears a field, leaving it out "
        "leaves the field alone).",
        "",
        f"{len(tools)} tools.",
        "",
    ]
    for title, names in groups:
        present = [n for n in names if n in tools]
        if present:
            lines.append(f"- [{title}](#{title.lower().replace(' ', '-')}): "
                         + ", ".join(f"`{n}`" for n in present))
    lines.append("")
    for title, names in groups:
        present = [n for n in names if n in tools]
        if not present:
            continue
        lines += [f"## {title}", ""]
        for n in present:
            lines += render_tool(tools[n])
    return "\n".join(lines).rstrip("\n") + "\n"


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="fail if docs/oss/MCP-TOOLS.md is stale")
    args = ap.parse_args(argv)
    text = render()
    rel = OUT.relative_to(ROOT).as_posix()
    if args.check:
        if _read(OUT) != text:
            print(f"gen_tool_ref: stale or missing: {rel}", file=sys.stderr)
            print(f"gen_tool_ref: {rel} is stale; run `{COMMAND}` and commit the result",
                  file=sys.stderr)
            return 1
        print(f"gen_tool_ref: up to date ({rel})")
        return 0
    if _read(OUT) == text:
        print(f"gen_tool_ref: {rel} unchanged")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"gen_tool_ref: wrote {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
