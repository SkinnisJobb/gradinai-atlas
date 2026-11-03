#!/usr/bin/env python3
"""Generate docs/oss/CONFIG.md: every ATLAS_* variable Atlas reads, with what it does (OSS-20 T2).

THE SOURCES, and what is taken from each:

  the variable set   the AST collector in tools/ci/check_env_example.py (imported, not copied):
                     every ATLAS_* name the shipped code reads. Plus the ATLAS_* names
                     deploy/oss/.env.example assigns that only the compose file reads (the TLS
                     door, the gateway), in their own section.
  descriptions       the comments in deploy/oss/.env.example: the comment paragraph directly
                     above a line (or a group of lines) describes it. A variable with none gets a
                     visible "no description" marker, never an invented text.
  allowed, default   constants in backend/settings.py, mcp/app/settings.py and
                     backend/retrieval/__init__.py, read with `ast` (nothing is imported or run).
  read by            the settings catalogue (backend/settings_catalog.py `lookup`, TASK-1433):
                     atlas-api, atlas-mcp, or docker compose and what it does with the value. A
                     variable the catalogue does not know stops the generator. The compose
                     section also lists the catalogue's compose-only names that are not ATLAS_*
                     (COMPOSE_PROFILES, ...), described by the catalogue's summary.

THE SCRUB RULE. The settings modules carry the original install's own values in comments,
docstrings and string defaults. None of that is copied: no comment or docstring is read at all,
and a code default is shown only when it is an enum, boolean or integer value. For a string
setting the page shows the value deploy/oss/.env.example gives (the OSS install's value), and
says so. `_code_default` enforces it.

STALENESS. The page is committed, so `--check` renders it in memory from the current sources and
fails on any difference (content only, like tools/docs_site.py). A change to a variable, an
allowed value, a default or a description changes the page; a comment-only edit to a settings
module does not, and passes.

Standard library only. Run from anywhere:

    python tools/gen_config_ref.py           # write docs/oss/CONFIG.md
    python tools/gen_config_ref.py --check   # exit 1 if it is stale
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "ci"))
import check_env_example as cee  # noqa: E402  (the collector; do not fork it)
sys.path.insert(0, str(ROOT / "backend"))
import settings_catalog as catalogue  # noqa: E402  (standard library only: settings + json)

OUT = ROOT / "docs" / "oss" / "CONFIG.md"
ENV_EXAMPLE = ROOT / cee.ENV_EXAMPLE
API_SETTINGS = "backend/settings.py"
MCP_SETTINGS = "mcp/app/settings.py"
RETRIEVAL = "backend/retrieval/__init__.py"
#: The estate-valued defaults (STORY-oss-23 A1): settings.py reads them from here, so this
#: page does too. The public export overlays a neutral copy, and its CONFIG.md follows it.
DIST = "backend/distribution.py"
#: The files a rendered value or description comes from (listed on the page).
SOURCES = (cee.ENV_EXAMPLE.as_posix(), API_SETTINGS, DIST, MCP_SETTINGS, RETRIEVAL,
           "backend/settings_catalog.py", "backend/data/config/other-services.json")

NO_DESCRIPTION = "**No description yet.** deploy/oss/.env.example has no comment for this variable."


def read_by(name: str) -> str:
    """Who reads `name`, from the settings catalogue. A name it does not know stops the run."""
    item = catalogue.lookup(name)
    if item is None and name.endswith("_FILE"):
        base = catalogue.lookup(name[: -len("_FILE")])
        item = base if base is not None and base.secret else None
    if item is None:
        raise SystemExit(f"gen_config_ref: {name} is not in the settings catalogue "
                         "(backend/settings_catalog.py, data/config/other-services.json); add it")
    if catalogue.COMPOSE_SVC in item.services and item.reader:
        return f"{item.reader} (not docker compose): {item.compose_use}"
    if catalogue.COMPOSE_SVC in item.services:
        return f"docker compose: {item.compose_use}"
    return ", ".join(item.services)


# --------------------------------------------------------------------------- constants (AST)

_TREES: dict = {}


def _tree(rel: str) -> ast.Module:
    if rel not in _TREES:
        _TREES[rel] = ast.parse((ROOT / rel).read_text(encoding="utf-8"), filename=rel)
    return _TREES[rel]


def _literal(node: ast.AST):
    """ast.literal_eval, plus integer products (`14 * 24`) as the settings modules write them."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left, right = _literal(node.left), _literal(node.right)
        if isinstance(left, int) and isinstance(right, int):
            return left * right
    return ast.literal_eval(node)


def const(rel: str, name: str):
    """The literal value of module-level constant `name` in `rel` (a tuple, int or str)."""
    for node in _tree(rel).body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) and node.value is not None else [])
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name:
                return _literal(node.value)
    raise SystemExit(f"gen_config_ref: {rel} has no module constant {name}; update the SPEC")


def call_arg(rel: str, fn: str, var: str, index: int):
    """The literal argument `index` of the call `fn(..., <var>, ...)` in `rel`, where <var> is the
    variable's name as a literal or as a module constant holding it. A constant argument (a
    `DEFAULT_*` name) is resolved too."""
    names = {n.targets[0].id for n in _tree(rel).body
             if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
             and isinstance(n.value, ast.Constant) and n.value.value == var}
    for node in ast.walk(_tree(rel)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if f != fn or len(node.args) <= index:
            continue
        if any((isinstance(a, ast.Constant) and a.value == var) or (isinstance(a, ast.Name) and a.id in names)
               for a in node.args):
            a = node.args[index]
            return const(rel, a.id) if isinstance(a, ast.Name) else ast.literal_eval(a)
    raise SystemExit(f"gen_config_ref: no call {fn}(..., {var}, ...) in {rel}; update the SPEC")


def in_source(rel: str, literal: str) -> str:
    """`literal`, after checking `rel` still has it as a string constant (a default written in
    a function body, not a module constant). A default that moved fails the generator."""
    for node in ast.walk(_tree(rel)):
        if isinstance(node, ast.Constant) and node.value == literal:
            return literal
    raise SystemExit(f"gen_config_ref: {rel} no longer has the literal {literal!r}; update the SPEC")


# --------------------------------------------------------------------------- the spec
# kind: enum | list | bool | flag | int | string | url | path | secret
#   bool  strict: 1/0 (also true/false, yes/no, on/off); anything else stops the process
#   flag  lenient: 1/true/yes/on is on, anything else is off
# allowed / default are evaluated lazily so a moved constant fails loudly at generation.
# A default of None means "unset": nothing applies, or the value is derived (see the text).

A, M, R = API_SETTINGS, MCP_SETTINGS, RETRIEVAL
STRICT_BOOL = lambda: "1 or 0 (also " + ", ".join(  # noqa: E731
    f"{t}/{f}" for t, f in zip(const(A, "_TRUE")[1:], const(A, "_FALSE")[1:])) + ")"
LENIENT = "on for " + "/".join(("1", "true", "yes", "on")) + "; anything else is off"

AREAS = (
    ("core", "Core: names, the front door and the install"),
    ("secrets", "Secrets"),
    ("auth", "Sign-in: local accounts, OIDC and OAuth"),
    ("rbac", "Access control (RBAC)"),
    ("tls", "TLS and the internal hop"),
    ("retrieval", "Retrieval add-on"),
    ("features", "Optional features"),
    ("mcp", "atlas-mcp"),
    ("audit", "Read audit"),
    ("backup", "Backups"),
    ("dev", "Not for an install"),
    ("compose", "Read by the compose file only"),
)

SPEC: dict = {
    # core
    "ATLAS_PUBLIC_URL": ("core", "url", None, None),
    "ATLAS_LOGIN_ORIGINS": ("core", "list", None, None),
    "ATLAS_BEARER_HOSTS": ("core", "list", None, None),
    "ATLAS_INTERNAL_HOSTS": ("core", "list", None, None),
    "ATLAS_TRUSTED_PROXY_CIDRS": ("core", "list", None, None),
    "ATLAS_PRIVACY_CONTACT": ("core", "string", None, None),
    "ATLAS_PRIVACY_HOSTING": ("core", "string", None, None),
    "ATLAS_PRODUCT_NAME": ("core", "string", None, None),
    "ATLAS_SEED": ("core", "enum", lambda: const(DIST, "SEED_PROFILES"), lambda: const(DIST, "DEFAULT_SEED_PROFILE")),
    "ATLAS_LEGACY_V0": ("core", "bool", STRICT_BOOL, lambda: "1" if const(DIST, "DEFAULT_LEGACY_V0") else "0"),
    "ATLAS_PUBLIC_DOCS": ("core", "bool", STRICT_BOOL, lambda: "0"),
    "ATLAS_LANDING": ("core", "enum", lambda: const(A, "LANDING_VERSIONS"), lambda: const(A, "DEFAULT_LANDING")),
    "ATLAS_SETUP_QUESTIONS_FILE": ("core", "path", None, None),
    "ATLAS_STRICT_API": ("core", "bool", STRICT_BOOL, lambda: "0"),
    "ATLAS_CONFIG_WRITES": ("core", "bool", STRICT_BOOL, lambda: "0"),
    "ATLAS_ACCEPTANCE_AGENT_WRITE": ("core", "enum", lambda: const(A, "ACCEPTANCE_AGENT_WRITE_MODES"),
                                     lambda: const(A, "DEFAULT_ACCEPTANCE_AGENT_WRITE")),
    # secrets
    "ATLAS_REQUIRE_SECRET_FILES": ("secrets", "flag", lambda: LENIENT, lambda: "0"),
    "ATLAS_DB_URL": ("secrets", "secret", None, None),
    "ATLAS_PASSWORD_PEPPER": ("secrets", "secret", None, None),
    "ATLAS_TOKEN_PEPPER": ("secrets", "secret", None, None),
    # auth
    "ATLAS_AUTH_PROVIDERS": ("auth", "list", lambda: const(A, "BUILTIN_AUTH_PROVIDERS"),
                             lambda: in_source(A, "local")),
    "ATLAS_LOCAL_LOGIN": ("auth", "enum", lambda: const(A, "LOCAL_LOGIN_MODES"), lambda: in_source(A, "all")),
    "ATLAS_BREACH_CHECK": ("auth", "enum", lambda: (in_source("backend/settings_catalog.py", "remote"),
                                                    in_source("backend/passwords.py", "bundled")),
                           lambda: in_source("backend/settings_catalog.py", "remote")),
    "ATLAS_OIDC_ISSUER": ("auth", "url", None, None),
    "ATLAS_OIDC_PRESET": ("auth", "enum", lambda: const(A, "OIDC_PRESETS"), lambda: in_source(A, "generic")),
    "ATLAS_OIDC_CLIENT_ID": ("auth", "string", None, None),
    "ATLAS_OIDC_TOKEN_AUTH": ("auth", "enum", lambda: const(A, "OIDC_TOKEN_AUTH_METHODS"),
                              lambda: in_source(A, "client_secret_basic")),
    "ATLAS_OIDC_CLIENT_SECRET": ("auth", "secret", None, None),
    "ATLAS_OIDC_CLIENT_KEY": ("auth", "secret", None, None),
    "ATLAS_OIDC_CLIENT_KEY_ID": ("auth", "string", None, None),
    "ATLAS_OIDC_REDIRECT_URI": ("auth", "url", None, None),
    "ATLAS_OIDC_SCOPES": ("auth", "string", None, None),
    "ATLAS_OIDC_NAME_CLAIM": ("auth", "string", None, None),
    "ATLAS_OIDC_EMAIL_CLAIM": ("auth", "string", None, None),
    "ATLAS_OIDC_GROUPS_CLAIM": ("auth", "string", None, None),
    "ATLAS_OIDC_GROUP_MAP_FILE": ("auth", "path", None, None),
    "ATLAS_OIDC_REQUIRED_GROUPS": ("auth", "list", None, None),
    "ATLAS_OIDC_MAX_SESSION_HOURS": ("auth", "int",
                                     lambda: f"1 to {const(A, 'MAX_OIDC_SESSION_HOURS')}",
                                     lambda: const(A, "DEFAULT_OIDC_MAX_SESSION_HOURS")),
    "ATLAS_OIDC_REVALIDATE_DAYS": ("auth", "int",
                                   lambda: f"1 to {call_arg(A, '_ident_int', 'ATLAS_OIDC_REVALIDATE_DAYS', 4)}",
                                   lambda: const(A, "DEFAULT_OIDC_REVALIDATE_DAYS")),
    "ATLAS_OIDC_STEP_UP": ("auth", "enum", lambda: const(A, "OIDC_STEP_UP_MODES"),
                           lambda: in_source(A, "off")),
    "ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS": ("auth", "int",
                                           lambda: "%d to %d" % const(A, "OIDC_STEP_UP_MAX_AGE_BOUNDS"),
                                           lambda: const(A, "DEFAULT_OIDC_STEP_UP_MAX_AGE")),
    "ATLAS_OAUTH_RESOURCE": ("auth", "url", None, None),
    "ATLAS_OAUTH_AUDIENCES": ("auth", "list", None, None),
    "ATLAS_OAUTH_SCOPE_MAP": ("auth", "list",
                              lambda: "IdP scope=tier pairs; tiers: " + ", ".join(const(A, "OAUTH_TIERS")), None),
    "ATLAS_MCP_OAUTH_SCOPES": ("auth", "list", None, None),
    # rbac
    "ATLAS_RBAC": ("rbac", "enum", lambda: const(A, "RBAC_MODES"), lambda: call_arg(A, "_choice", "ATLAS_RBAC", 3)),
    "ATLAS_DEFAULT_PROJECT_VISIBILITY": ("rbac", "enum", lambda: const(A, "PROJECT_VISIBILITIES"),
                                         lambda: call_arg(A, "_choice", "ATLAS_DEFAULT_PROJECT_VISIBILITY", 3)),
    "ATLAS_SHARED_PROJECT_ROLE": ("rbac", "enum", lambda: const(A, "SHARED_PROJECT_ROLES"),
                                  lambda: call_arg(A, "_choice", "ATLAS_SHARED_PROJECT_ROLE", 3)),
    "ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER": ("rbac", "bool", STRICT_BOOL, lambda: "1"),
    # tls
    "ATLAS_BASE_URL": ("tls", "url", None, None),
    "ATLAS_CA_BUNDLE": ("tls", "path", None, None),
    "ATLAS_MCP_CLIENT_CERT_FILE": ("tls", "path", None, None),
    "ATLAS_MCP_CLIENT_KEY_FILE": ("tls", "path", None, None),
    # TASK-1419 (OSS-26 F): the server certificate's expiry in /api/health
    "ATLAS_CERT_EXPIRY_FILE": ("tls", "path", None, None),
    "ATLAS_CERT_EXPIRY_WARN_DAYS": ("tls", "int", lambda: f"1 to {const(A, 'CERT_EXPIRY_WARN_DAYS_MAX')} days",
                                    lambda: const(A, "DEFAULT_CERT_EXPIRY_WARN_DAYS")),
    # retrieval
    "ATLAS_RETRIEVAL": ("retrieval", "enum", lambda: (in_source(R, "off"), in_source(R, "on")),
                        lambda: in_source(R, "off")),
    "ATLAS_EMBED_URL": ("retrieval", "url", None, None),
    "ATLAS_EMBED_MODEL": ("retrieval", "string", None, None),
    "ATLAS_EMBED_DIM": ("retrieval", "int", lambda: "a positive whole number", None),
    "ATLAS_EMBED_KEY": ("retrieval", "secret", None, None),
    "ATLAS_EMBED_KEY_FILE": ("retrieval", "path", None, None),
    "ATLAS_EMBED_AUTH_HEADER": ("retrieval", "enum", lambda: const(R, "AUTH_HEADERS"), lambda: in_source(R, "bearer")),
    "ATLAS_EMBED_ENTRA_SCOPE": ("retrieval", "string", None, None),
    "ATLAS_EMBED_CA_BUNDLE": ("retrieval", "path", None, None),
    "ATLAS_EMBED_SCOPE": ("retrieval", "enum", lambda: const(R, "SCOPES"), lambda: in_source(R, "shared")),
    "ATLAS_EMBED_DOC_PREFIX": ("retrieval", "string", None, None),
    "ATLAS_EMBED_QUERY_PREFIX": ("retrieval", "string", None, None),
    "ATLAS_EMBED_BATCH": ("retrieval", "int", None, lambda: call_arg(R, "_int", "ATLAS_EMBED_BATCH", 2)),
    "ATLAS_RETRIEVAL_INTERVAL_S": ("retrieval", "int", None,
                                   lambda: call_arg(R, "_int", "ATLAS_RETRIEVAL_INTERVAL_S", 2)),
    "ATLAS_RETRIEVAL_TOP_K": ("retrieval", "int", lambda: "{} to {}".format(
        call_arg("backend/settings_catalog.py", "_int_text", "ATLAS_RETRIEVAL_TOP_K", 1),
        call_arg("backend/settings_catalog.py", "_int_text", "ATLAS_RETRIEVAL_TOP_K", 2)),
                              lambda: int(in_source("backend/retrieval/query.py", "30"))),
    # features
    "ATLAS_DISABLED_FEATURES": ("features", "list", lambda: const(A, "FEATURES"), lambda: ""),
    "ATLAS_GITHUB_REPO": ("features", "string", None, None),
    "ATLAS_GITHUB_OAUTH_CLIENT_ID_FILE": ("features", "path", None, None),
    "ATLAS_GITHUB_OAUTH_CLIENT_SECRET_FILE": ("features", "path", None, None),
    "ATLAS_GITHUB_INVITE_TOKEN_FILE": ("features", "path", None, None),
    "ATLAS_DEPLOY_HINT_HOST": ("features", "string", None, None),
    "ATLAS_DEPLOY_HINT_REBUILD_API": ("features", "string", None, None),
    "ATLAS_DEPLOY_HINT_REBUILD_MCP": ("features", "string", None, None),
    "ATLAS_DEPLOY_HINT_PULL_REBUILD_API": ("features", "string", None, None),
    "ATLAS_DEPLOY_HINT_PULL_REBUILD_MCP": ("features", "string", None, None),
    "ATLAS_DEPLOY_SRC": ("features", "path", None, None),
    "ATLAS_DEPLOY_WATCH_FILE": ("features", "path", None, None),
    # mcp
    "ATLAS_MCP_ALLOWED_HOSTS": ("mcp", "list", None, None),
    "ATLAS_MCP_ALLOWED_ORIGINS": ("mcp", "list", None, None),
    "ATLAS_MCP_ENABLE_DELETE": ("mcp", "flag", lambda: LENIENT, lambda: "0"),
    "ATLAS_MCP_AUTH_CACHE_S": ("mcp", "int", lambda: "seconds",
                               lambda: int(in_source("mcp/app/caller_auth.py", "60"))),
    "ATLAS_MCP_ALLOW_SHARED_TOKEN": ("mcp", "flag", lambda: LENIENT, lambda: "0"),
    "ATLAS_TOKEN": ("mcp", "secret", None, None),
    # audit
    "ATLAS_AUDIT_READS": ("audit", "bool", STRICT_BOOL, lambda: "0"),
    "ATLAS_AUDIT_RETENTION_DAYS": ("audit", "int", lambda: f"1 to {const(A, 'AUDIT_RETENTION_MAX_DAYS')} days",
                                   None),
    # backup (OSS-26 C: the backup profile's routine schedule)
    "ATLAS_BACKUP_SCHEDULE": ("backup", "enum", lambda: const(A, "BACKUP_SCHEDULES"),
                              lambda: const(A, "DEFAULT_BACKUP_SCHEDULE")),
    "ATLAS_BACKUP_KEEP": ("backup", "int", lambda: f"1 to {const(A, 'BACKUP_KEEP_MAX')}",
                          lambda: const(A, "DEFAULT_BACKUP_KEEP")),
    # not for an install
    "ATLAS_ALLOW_SESSION_MINT": ("dev", "flag", lambda: LENIENT, lambda: "0"),
    "ATLAS_MACHINE_GRACE_HOSTS": ("dev", "list", None, None),
    "ATLAS_RESEED": ("dev", "flag", lambda: "on for 1/true/yes; anything else is off", lambda: "0"),
    "ATLAS_ADMIN_ACTOR": ("dev", "string", None, None),
    "ATLAS_FRONTEND_DIR": ("dev", "path", None, None),
}

#: Plain variables (not in the collector's secret set) whose value is a credential or the path to one.
SECRET_FILES = {"ATLAS_TLS_CF_TOKEN_FILE", "ATLAS_EMBED_KEY_FILE", "ATLAS_MCP_CLIENT_KEY_FILE", "ATLAS_GITHUB_OAUTH_CLIENT_ID_FILE",
                "ATLAS_GITHUB_OAUTH_CLIENT_SECRET_FILE", "ATLAS_GITHUB_INVITE_TOKEN_FILE",
                "ATLAS_GATEWAY_SESSION_KEY_FILE"}
#: Secrets read from a file only: the plain variable is refused (or makes the feature unavailable).
FILE_ONLY = {"ATLAS_OIDC_CLIENT_SECRET", "ATLAS_OIDC_CLIENT_KEY", "ATLAS_EMBED_KEY"}


# --------------------------------------------------------------------------- .env.example

ASSIGN = re.compile(r"^#?\s*(ATLAS_[A-Z0-9_]*[A-Z0-9])=(.*)$")
#: `#   NAME=value   what it is`: an indented list line inside a comment; the value is one word.
#: Any other `# NAME=value` (COMPOSE_PROFILES=gateway): not ours, but it ends the paragraph above it.
OTHER_ASSIGN = re.compile(r"^#?\s*[A-Z][A-Z0-9_]*=")
LISTED = re.compile(r"^#\s{2,}(ATLAS_[A-Z0-9_]*[A-Z0-9])=(\S*)\s*(.*)$")


def prose_continues(prose: list) -> bool:
    """The comment above is mid-sentence (does not end in . : or a closing bracket)."""
    return bool(prose) and not re.search(r"[.:)\]]$", prose[-1])


def _assignment(line: str, prose: list):
    """(name, value, trailing text) when `line` sets a variable (live or commented), else None.
    A `# NAME=value` line that continues a sentence (the prose above it does not end in . : or
    a closing bracket) is prose, not an assignment."""
    m = LISTED.match(line)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m = ASSIGN.match(line.strip())
    if not m:
        return None
    if line.startswith("#") and prose_continues(prose):
        return None
    return m.group(1), m.group(2).strip(), ""


def parse_env_example(text: str) -> tuple[dict, dict, set]:
    """({name: {"desc": [paragraphs], "values": [(value, live)], "group": [names]}},
        {name: [paragraphs that mention it]}, {names assigned live}).

    A comment paragraph (lines up to a blank line or a bare `#`) describes the run of
    assignments directly under it; a paragraph that follows an assignment starts a new one."""
    entries: dict = {}
    mentions: dict = {}
    prose: list = []
    run: list = []
    after_rule = False
    just_titled = False  # the previous line was a section title (a closing rule follows it)

    def flush_run():
        nonlocal run
        if len(run) > 1:
            for n in run:
                entries[n]["group"] = list(dict.fromkeys(run))
        run = []

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("# ====") or line.startswith("#===="):
            flush_run()
            prose, after_rule = [], not just_titled
            just_titled = False
            continue
        just_titled = False
        if not line.strip() or line.strip() == "#":
            flush_run()
            prose, after_rule = [], False
            continue
        if after_rule:  # the section title line
            after_rule, just_titled = False, True
            continue
        a = _assignment(line, prose)
        if a:
            name, value, trailing = a
            live = not line.lstrip().startswith("#")
            e = entries.setdefault(name, {"desc": [], "values": [], "group": []})
            text_ = re.sub(r"\s+", " ", " ".join(prose)).strip()
            if trailing:
                text_ = (text_ + " " if text_ else "") + trailing.strip()
            if text_ and text_ not in e["desc"]:
                e["desc"].append(text_)
            if value:
                e["values"].append((value, live))
            run.append(name)
            continue
        if OTHER_ASSIGN.match(line) and not prose_continues(prose):
            flush_run()
            prose = []
            continue
        if line.startswith("#"):
            if run:  # prose after an assignment: a new paragraph
                flush_run()
                prose = []
            body = line[1:].strip()
            prose.append(body)
            for n in cee.NAME.findall(body):
                mentions.setdefault(n, [])
            continue
        flush_run()
        prose = []
    flush_run()
    # mentions: whole paragraphs, re-read for the names they name (used only as a fallback)
    para: list = []
    for raw in text.splitlines() + [""]:
        s = raw.strip()
        if s.startswith("#") and s not in ("#",) and not s.startswith("# ====") and not ASSIGN.match(s):
            para.append(s[1:].strip())
            continue
        if para:
            joined = " ".join(para)
            for n in set(cee.NAME.findall(joined)):
                mentions.setdefault(n, []).append(joined)
        para = []
    live = {n for n, e in entries.items() if any(v[1] for v in e["values"])}
    return entries, mentions, live


# --------------------------------------------------------------------------- rendering

#: A private (RFC 1918) address. The page is scrub-enforced, so such a value from .env.example
#: (the pinned compose subnet) is named, not printed.
PRIVATE_V4 = re.compile(r"\b(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)(?:\.\d{1,3}){2,3}(?:/\d{1,2})?\b")

def _fmt_values(v) -> str:
    if isinstance(v, (tuple, list, frozenset, set)):
        return ", ".join(f"`{x}`" for x in v)
    return str(v)


def _code_default(kind: str, value):
    """The code default, only for enum/bool/flag/int values (the scrub rule). Anything else is
    refused here, so a string default from a settings module can never reach the page."""
    if value is None:
        return None
    if kind in ("enum", "bool", "flag") and isinstance(value, str):
        return value
    if kind == "list" and isinstance(value, str):  # a default list of enum members
        return value
    if kind == "int" and isinstance(value, int) and not isinstance(value, bool):
        return value
    raise SystemExit(f"gen_config_ref: a {kind} default {value!r} would be shown; the scrub rule forbids it")


def _md(text: str) -> str:
    """Escape what would break markdown in a sentence copied from a comment."""
    text = PRIVATE_V4.sub("(a private address)", text)
    return text.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def _anchor(name: str) -> str:
    return name.lower()


# The audience frontmatter every docs/oss page starts with (TASK-1429; checked by
# tools/check_docs_audience.py). Emitted here so --check compares the whole page.
FRONTMATTER = ["---", "audience: operations-security", "---", ""]


def render() -> str:
    names, secrets = cee.code_names(cee.ROOT)
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    entries, mentions, live = parse_env_example(text)

    missing = sorted(set(names) - set(SPEC))
    if missing:
        raise SystemExit("gen_config_ref: the code reads variables the SPEC does not place: "
                         + ", ".join(missing) + " (add each to SPEC with its area and kind)")
    for s in secrets:
        if SPEC[s][1] != "secret":
            raise SystemExit(f"gen_config_ref: {s} is read as a secret; give it kind 'secret' in SPEC")

    # compose-only names: assigned in .env.example, read by no code, not the _FILE form of a secret
    compose_only = sorted(n for n in entries if n not in names
                          and not (n.endswith("_FILE") and n[:-5] in secrets))
    # ...and the catalogue's compose-only names the .env.example parser cannot see (not ATLAS_*).
    compose_only += sorted(s.name for s in catalogue.other_services()
                           if catalogue.COMPOSE_SVC in s.services and not s.name.startswith("ATLAS_"))

    undescribed: list = []

    def describe(name: str) -> tuple[list, list]:
        e = entries.get(name)
        if e and e["desc"]:
            return e["desc"], e["group"]
        if name in secrets and entries.get(name + "_FILE", {}).get("desc"):
            f = entries[name + "_FILE"]
            return f["desc"], f["group"]
        if mentions.get(name):
            return mentions[name][:1], []
        return [], []

    def oss_value(name: str):
        for key in (name, name + "_FILE"):
            e = entries.get(key)
            if e and e["values"]:
                vals = list(dict.fromkeys(v for v, _ in e["values"]))
                return key, vals, any(lv for _, lv in e["values"])
        return None, [], False

    def entry(name: str, area_spec) -> list:
        _area, kind, allowed_f, default_f = area_spec
        out = [f"### `{name}`", ""]
        desc, group = describe(name)
        if not desc and not name.startswith("ATLAS_"):
            desc = [catalogue.lookup(name).summary + " *(From the settings catalogue.)*"]
        if desc:
            out += [_md(d) for d in desc]
            others = [g for g in group if g != name]
            if others:
                out[-1] += " *(This comment covers " + ", ".join(f"`{g}`" for g in others) + " too.)*"
        else:
            out.append(NO_DESCRIPTION)
            undescribed.append(name)
        out.append("")
        # allowed
        if kind == "secret":
            allowed = "a secret, given as the path of a file holding it"
        elif allowed_f is not None:
            allowed = _fmt_values(allowed_f())
        else:
            allowed = {"compose": "see the description", "url": "an absolute URL", "path": "a file path", "list": "comma-separated values",
                       "string": "text", "int": "a whole number"}.get(kind, "")
        out.append(f"- **Allowed:** {allowed}")
        # default
        code_default = _code_default(kind, default_f() if default_f else None)
        key, vals, is_live = oss_value(name)
        if vals:
            oss = ", ".join("a private address range (see `.env.example`)" if PRIVATE_V4.search(v)
                            else f"`{v}`" for v in vals)
            via = "" if key == name else f" (as `{key}`)"
            oss_line = (f"- **OSS install:** {oss}{via}, set in `.env.example`" if is_live else
                        f"- **OSS install:** not set; `.env.example` shows {oss}{via} commented out")
        else:
            oss_line = "- **OSS install:** not set"
        if code_default == "":
            out.append("- **Default:** empty")
        elif code_default is not None:
            out.append(f"- **Default:** `{code_default}`")
        elif kind == "compose":
            out.append("- **Default:** the compose file's; not read by the Atlas code")
        elif kind in ("enum", "bool", "flag", "int"):
            out.append("- **Default:** none (unset)")
        elif vals:
            out.append("- **Default:** not shown for a text setting; see the OSS install's value")
        else:
            out.append("- **Default:** not shown for a text setting")
        out.append(oss_line)
        # secret
        if kind == "secret":
            sec = f"yes: set `{name}_FILE` to the path of a file"
            sec += "; the plain variable is never read" if name in FILE_ONLY else (
                "; the plain variable is refused when `ATLAS_REQUIRE_SECRET_FILES` is on")
        elif name in SECRET_FILES:
            sec = "yes: this is the path of a file holding a credential"
        else:
            sec = "no"
        who = read_by(name)
        out += [f"- **Secret:** {sec}", f"- **Read by:** {who}", ""]
        summary = ("empty" if code_default == "" else f"`{code_default}`" if code_default is not None else "-",
                   ("set" if is_live else "commented out") if vals else "-",
                   sec.split(":")[0], "compose" if who.startswith("docker compose") else
                   who.split(" ")[0].rsplit("/", 1)[-1] if "(not docker compose)" in who else who)
        return out, summary

    by_area: dict = {a: [] for a, _ in AREAS}
    for n in sorted(names):
        by_area[SPEC[n][0]].append(n)
    by_area["compose"] = compose_only

    lines = [
        *FRONTMATTER,
        "# Configuration reference",
        "",
        "*Generated by `tools/gen_config_ref.py`. Do not edit this page: change the code or",
        "`deploy/oss/.env.example`, then run `python tools/gen_config_ref.py`.*",
        "",
        "Atlas is configured with `ATLAS_*` environment variables. In the OSS install they live in",
        "`deploy/oss/.env` (copied from `deploy/oss/.env.example`), which compose reads for its own",
        "`${...}` and passes to atlas-api and atlas-mcp. This page lists every variable the code",
        f"reads ({len(names)}), grouped by area, and then the ones only the compose file reads.",
        "",
        "How to read an entry:",
        "",
        "- The description is the comment in `deploy/oss/.env.example`. Where several lines share",
        "  one comment, the entry says which.",
        "- **Allowed** comes from the constants in the code.",
        "- **Default** is what the code uses when the variable is unset. It is shown only for a",
        "  choice, a switch or a number; the code's text defaults suit the original install, not",
        "  yours, so for a text setting use the OSS install's value.",
        "- **OSS install** is the value `deploy/oss/.env.example` gives: set, or shown commented out",
        "  as an example you can turn on.",
        "- **Secret** says whether the value is a credential. A secret is never put in `.env`: set",
        "  its `*_FILE` form to the path of a file holding it (`ATLAS_REQUIRE_SECRET_FILES=1`",
        "  refuses the plain form).",
        "- **Read by** is the process that reads it: atlas-api, atlas-mcp, or docker compose itself",
        "  (and then what compose does with it), from the settings catalogue",
        "  (`backend/settings_catalog.py`).",
        "",
        "## Areas",
        "",
    ]
    for a, title in AREAS:
        if by_area[a]:
            lines.append(f"- [{title}](#{_slug(title)}) ({len(by_area[a])})")
    lines.append("")
    for a, title in AREAS:
        if not by_area[a]:
            continue
        lines += [f"## {title}", ""]
        if a == "compose":
            lines += ["These are set in `deploy/oss/.env.example` (or an example profile under",
                      "`docs/oss/examples/env/`) and used by `deploy/oss/docker-compose.yml`, an",
                      "overlay beside it or docker compose itself (or, where **Read by** says so, by a",
                      "deploy script that reads `.env` itself); the Atlas code does not read them.", ""]
        lines += ["| Variable | Code default | OSS .env.example | Secret | Read by |", "|---|---|---|---|---|"]
        rendered: list = []
        for n in by_area[a]:
            spec = SPEC.get(n) or ("compose", "compose", None, None)
            block, (d, o, sec, who) = entry(n, spec)
            rendered += block
            lines.append(f"| [`{n}`](#{_anchor(n)}) | {d} | {o} | {sec} | {who} |")
        lines += [""] + rendered
    if undescribed:
        lines += ["## Variables without a description", "",
                  "These have no comment in `deploy/oss/.env.example` yet:", ""]
        lines += [f"- `{n}`" for n in undescribed] + [""]
    lines += ["## Sources", "",
              "Rendered from these files and the code the variable set is collected from;",
              "`python tools/gen_config_ref.py --check` fails when the page no longer matches them:", ""]
    lines += [f"- `{rel}`" for rel in SOURCES] + [""]
    return "\n".join(lines)


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9 -]", "", title.lower())
    return s.replace(" ", "-")


# --------------------------------------------------------------------------- check / write

def _read(p: Path):
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="fail if docs/oss/CONFIG.md is stale")
    args = ap.parse_args(argv)
    want = render()
    have = _read(OUT)
    rel = OUT.relative_to(ROOT).as_posix()
    if args.check:
        if have is not None:
            have = have.replace("\r\n", "\n")
        if have != want:
            print(f"gen_config_ref: {rel} is stale or missing; run `python tools/gen_config_ref.py` "
                  "and commit the result", file=sys.stderr)
            return 1
        print(f"gen_config_ref: up to date ({rel})")
        return 0
    if have != want:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT, "w", encoding="utf-8", newline="\n") as f:
            f.write(want)
        print(f"gen_config_ref: wrote {rel}")
    else:
        print(f"gen_config_ref: {rel} already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
