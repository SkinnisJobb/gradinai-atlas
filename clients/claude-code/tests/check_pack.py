#!/usr/bin/env python3
"""Static checks of the Claude Code client pack (clients/claude-code/). Standard library only.

  * every *.json parses;
  * each MCP template is an `mcpServers` map of exactly one `http` server whose `url` is an
    absolute https URL ending in /mcp/, that authenticates through `headersHelper` and carries
    NO static `headers` and no token-shaped string anywhere (criterion 1: never inline);
  * the plugin manifest and the marketplace file have the fields Claude Code requires
    (`name` kebab-case; marketplace `owner.name`, `plugins[].name/source`), every relative
    source resolves inside the pack, and no name is one Claude Code reserves;
  * every skill is `skills/<name>/SKILL.md` with `name` (matching its folder) and
    `description` in its front matter, and the plugin ships atlas-retrieval, file-task and setup-guide.

Exit 0 when clean, 1 with one line per problem. The authoritative manifest check is
`claude plugin validate --strict clients/claude-code`; this one needs no Claude Code install.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

PACK = Path(__file__).resolve().parent.parent
PLUGIN = PACK / "plugin"
TEMPLATES = ["mcp.template.json", "mcp.template.windows.json"]
REQUIRED_SKILLS = {"atlas-retrieval", "file-task", "setup-guide"}
KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Anything that looks like a credential value: an Atlas service token, a Bearer value, a JWT.
TOKENISH = re.compile(r"atlas_svc_[A-Za-z0-9]{8,}|Bearer\s+[A-Za-z0-9._~+/=-]{8,}|eyJ[A-Za-z0-9_-]{10,}\.")
RESERVED_PREFIXES = ("claude-", "anthropic-", "anthropics-", "cc-plugin-", "claudeai-")

problems: list[str] = []


def bad(msg: str) -> None:
    problems.append(msg)


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        bad(f"{path.relative_to(PACK)}: not valid JSON ({e})")
        return None


def check_name(where: str, name) -> None:
    if not isinstance(name, str) or not KEBAB.match(name):
        bad(f"{where}: name {name!r} is not kebab-case")
    elif name.startswith(RESERVED_PREFIXES) or name in ("claude", "anthropic", "claude-code"):
        bad(f"{where}: name {name!r} is reserved by Claude Code")


def check_template(rel: str) -> None:
    path = PACK / rel
    text = path.read_text(encoding="utf-8")
    if TOKENISH.search(text):
        bad(f"{rel}: contains a token-shaped value; the token must never be inline")
    doc = load(path)
    if doc is None:
        return
    servers = doc.get("mcpServers") if isinstance(doc, dict) else None
    if not isinstance(servers, dict) or len(servers) != 1:
        bad(f"{rel}: expected an mcpServers map with exactly one server")
        return
    for name, cfg in servers.items():
        where = f"{rel}: mcpServers.{name}"
        if not isinstance(cfg, dict):
            bad(f"{where}: not an object")
            continue
        unknown = set(cfg) - {"type", "url", "headersHelper"}
        if unknown:
            bad(f"{where}: unexpected keys {sorted(unknown)} (static headers are not allowed)")
        if cfg.get("type") != "http":
            bad(f"{where}: type must be \"http\" (a url with no type is read as stdio)")
        u = urlparse(str(cfg.get("url", "")))
        if u.scheme != "https" or not u.netloc or not u.path.endswith("/mcp/"):
            bad(f"{where}: url must be an absolute https URL ending in /mcp/")
        helper = cfg.get("headersHelper")
        if not isinstance(helper, str) or "atlas-mcp-headers" not in helper:
            bad(f"{where}: headersHelper must run the atlas-mcp-headers helper")
        elif not (PACK / "helpers" / ("atlas-mcp-headers.ps1" if ".ps1" in helper
                                      else "atlas-mcp-headers.sh")).is_file():
            bad(f"{where}: the helper it names is not in helpers/")


def front_matter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep and not line.startswith((" ", "\t")):
            out[k.strip()] = v.strip()
    return out


def check_plugin() -> None:
    manifest = load(PLUGIN / ".claude-plugin" / "plugin.json")
    if isinstance(manifest, dict):
        check_name("plugin/.claude-plugin/plugin.json", manifest.get("name"))
        for key in ("version", "description"):
            if not isinstance(manifest.get(key), str) or not manifest[key]:
                bad(f"plugin.json: {key} is missing")
        author = manifest.get("author")
        if not (isinstance(author, dict) and author.get("name")):
            bad("plugin.json: author.name is missing")
    skills_dir = PLUGIN / "skills"
    found = set()
    for skill in sorted(p for p in skills_dir.iterdir() if p.is_dir()) if skills_dir.is_dir() else []:
        md = skill / "SKILL.md"
        rel = md.relative_to(PACK).as_posix()
        if not md.is_file():
            bad(f"{rel}: missing")
            continue
        fm = front_matter(md)
        if fm.get("name") != skill.name:
            bad(f"{rel}: front-matter name {fm.get('name')!r} does not match its folder")
        if len(fm.get("description", "")) < 40:
            bad(f"{rel}: front-matter description is missing or too short to trigger on")
        found.add(skill.name)
    for missing in sorted(REQUIRED_SKILLS - found):
        bad(f"plugin/skills/{missing}/SKILL.md: required skill is missing")


def check_marketplace() -> None:
    rel = ".claude-plugin/marketplace.json"
    doc = load(PACK / rel)
    if not isinstance(doc, dict):
        return
    check_name(rel, doc.get("name"))
    if not (isinstance(doc.get("owner"), dict) and doc["owner"].get("name")):
        bad(f"{rel}: owner.name is missing")
    plugins = doc.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        bad(f"{rel}: plugins is empty")
        return
    for i, entry in enumerate(plugins):
        src = entry.get("source") if isinstance(entry, dict) else None
        check_name(f"{rel}: plugins[{i}]", entry.get("name") if isinstance(entry, dict) else None)
        if not isinstance(src, str) or not src.startswith("./") or ".." in src:
            bad(f"{rel}: plugins[{i}].source must be a ./ path inside the pack")
        elif not (PACK / src / ".claude-plugin" / "plugin.json").is_file():
            bad(f"{rel}: plugins[{i}].source {src} has no .claude-plugin/plugin.json")


def main() -> int:
    for path in sorted(PACK.rglob("*.json")):
        load(path)
    for rel in TEMPLATES:
        check_template(rel)
    check_plugin()
    check_marketplace()
    for p in problems:
        print("check_pack: " + p)
    print(f"check_pack: {'FAIL' if problems else 'ok'} ({len(problems)} problem(s))")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
