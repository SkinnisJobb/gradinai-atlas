"""Every setting Atlas reads, described once (STORY-atlas-config-panel, phase 1). READ-ONLY.

`settings.py` reads the configuration; this module says, for each variable, WHICH PART of the
install it belongs to, WHAT KIND of setting it is, and HOW ITS SOURCE IS DETERMINED. Its one
reader is `GET /api/config` (routers/config.py), which the board's node panel shows on the
`atlas-system*` part nodes (system_handbook.py).

It covers every `ATLAS_*` variable the API reads ANYWHERE in its code, not only in `settings.py`:
the retrieval add-on (`retrieval/`), the password module, the gate, the deploy status reader.

WHO READS IT (`services`, TASK-1433). The catalogue also describes what the API does NOT read, so
one table names every variable an install sets: atlas-mcp's own (`mcp/app/*.py`: ATLAS_MCP_*,
ATLAS_BASE_URL, ATLAS_CA_BUNDLE, ATLAS_TOKEN) and the ones only docker compose reads (the `${...}`
the OSS compose file and its overlays interpolate, e.g. ATLAS_PUBLIC_HOST, ATLAS_TLS_*,
ATLAS_GATEWAY_*, ATLAS_ORIGIN_SUBNET, and COMPOSE_PROFILES). Every entry's `services` says which of
`atlas-api`, `atlas-mcp` and `compose` reads it (a setting both processes read, such as
ATLAS_DISABLED_FEATURES, names both); a compose entry's `compose_use` says what compose does with
it. tools/gen_config_ref.py prints both on docs/oss/CONFIG.md, and tools/check_examples.py accepts
a key only when `is_described` knows it.

  CATALOG  what atlas-api reads, as before. `BY_NAME` indexes it, and it alone is the panel's.
  OTHER    what only atlas-mcp or compose reads: `other_services()`, from
           data/config/other-services.json. Kept as data, not as literals here, so the scans that
           find what the code READS (names_in_api below, tools/ci/check_env_example.py) do not
           count a description as a read. Read on first use, never at import (boot opens no file).
           `lookup(name)` and `all_settings()` cover both.

THE PANEL SHOWS ONLY WHAT atlas-api READS. `describe()` (GET /api/config) evaluates CATALOG, exactly
the set it always did. An atlas-mcp or compose-only entry is left out: atlas-api cannot see
atlas-mcp's process environment (another container; the homelab's is another compose project with
its own env file, and the OSS install's overrides .env with its own `environment:`), nor what
compose interpolated on the host, so any value it showed would be a guess presented as the
effective one. Leaving them out also keeps the homelab's /api/config body byte for byte what it was.

It changes nothing: no value, default or name moves anywhere. The effective values come from the
SAME code the app uses: `settings.load()`, `load_rbac()`, ..., `retrieval.load_config()`, and,
for a value a module computes at import or per call (`auth.MACHINE_GRACE_HOSTS`, `deps.FRONTEND`,
`github_link.repo()`, ...), the rule that module applies, which lives in settings.py since
TASK-1433 (`settings.load_machine_grace_hosts`, `load_frontend_dir`, ...). EVERY value is computed
from the mapping `describe(env)` is given, never from `os.environ` behind its back, and nothing
here imports a module that needs the peppers (auth, passwords, deps, github_link, identity). A loader that
refuses the current environment would have stopped the boot; if it ever does here, the setting
says so and shows no value. The error is reported against the variable it names (the exception's
`variable`, set where it is raised, TASK-1439): that setting carries `error`, and the loader's
other settings carry `blocked_by` with that name instead. An error naming no catalogued plain
setting is reported against every setting of the loader, as before. Three one-line rules that live inline in a function with no parser
to call (ATLAS_BREACH_CHECK, ATLAS_RESEED, ATLAS_ADMIN_ACTOR) are restated beside their loaders.

THE THREE KINDS

  plain      shown with its effective value and its source.
  secret     shown ONLY as `set from file`, `set from env` or `not set`. Never the value, a prefix,
             a hash or a length. The state is decided WITHOUT opening the file: a configured path
             that names a readable, non-empty file is "set from file". Nothing here reads a secret.
  bootstrap  needed before the app can start (the database URL, the two peppers, the login
             origins). Read-only in every mode, including phase 2's. A bootstrap setting that
             holds a credential (`secret=True`) is shown exactly like a secret.

SOURCES (`source`)

  env        the variable itself is set (non-blank).
  file       the `NAME_FILE` variable names a file (secrets only).
  derived    unset, and computed from another variable (`derived_from`), e.g. the login origins
             from ATLAS_PUBLIC_URL.
  default    unset; the code's default applies.
  None       a secret that is not set.

COMPLETENESS. `undescribed()` lists every `ATLAS_*` name written in a module of the API (test
harness excluded) that has no entry here; `backend/verify_config_panel.py` fails on any. Adding a
variable anywhere in backend/ therefore means adding its line below, with its part node.

PHASE 2: WHICH SETTINGS THE BOARD MAY CHANGE (`changeable`)

Only with `ATLAS_CONFIG_WRITES=1` (default off; routers/config.py has the whole design and the
precedence). A setting is changeable only when ALL of these hold, and `changeable=True` below is
the claim that they do:

  * it is `plain`: never a secret, never a bootstrap setting (in any mode, forever);
  * the code that uses it RE-READS it from the process environment on every use, so a board
    value applied at boot, or changed while the API runs, really takes effect. A setting read
    once at import (route registration, the gate's host lists, audit_log's switch) cannot be
    honoured by a value that lives in the database, because the database is not reachable
    until after the import; marking one changeable would make the board silently do nothing;
  * it is not a trust boundary: no RBAC mode, identity provider, OAuth, origin or host setting,
    no audit switch, no rail that guards a human approval. Those are excluded deliberately
    (`EXCLUDED`, with the reason a refusal names), not merely not yet reached.

The first set is two settings: ATLAS_STRICT_API (read per request by
`payload_fields.strict_api()`) and ATLAS_RETRIEVAL_TOP_K (read per query by
`retrieval.query.top_k()`). Each carries `validate`, which returns the canonical stored text or
raises ValueError naming the problem, by the same rule its own reader applies.
"""
from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import settings as S

KINDS = ("plain", "secret", "bootstrap")

#: The part nodes, in system_handbook.NODES order. A part with no entry still gets a
#: (empty) section, so the panel can say so instead of hiding it.
PARTS = ("atlas-system", "atlas-system-api", "atlas-system-mcp", "atlas-system-postgres",
         "atlas-system-gateway", "atlas-system-search", "atlas-system-retrieval")

ROOT, API, POSTGRES = "atlas-system", "atlas-system-api", "atlas-system-postgres"
RETRIEVAL = "atlas-system-retrieval"
MCP, GATEWAY = "atlas-system-mcp", "atlas-system-gateway"

#: Who reads a variable (TASK-1433): the API process, the MCP process, or docker compose itself
#: (a `${...}` in deploy/oss/*.yml, resolved on the host before any container starts).
API_SVC, MCP_SVC, COMPOSE_SVC = "atlas-api", "atlas-mcp", "compose"
SERVICES = (API_SVC, MCP_SVC, COMPOSE_SVC)
BOTH = (API_SVC, MCP_SVC)

#: The three states a secret may be shown in. Nothing else about a secret leaves this module.
SECRET_FROM_FILE, SECRET_FROM_ENV, SECRET_NOT_SET = "set from file", "set from env", "not set"

#: What a part with no entry says, so an empty section is an answer rather than a gap.
EMPTY_PART_NOTES = {
    "atlas-system-mcp": "The MCP service is its own process and reads its own settings "
                        "(mcp/app/settings.py); none of them is read by the API.",
    "atlas-system-gateway": "The gateway is configured outside the API; the API reads none "
                            "of its settings.",
    "atlas-system-search": "Full-text search has no setting of its own: it is part of the "
                           "API and the database.",
}


@dataclass(frozen=True)
class Setting:
    """One variable. `value` is for plain settings only: (loaded) -> JSON-able value."""

    name: str
    part: str
    kind: str
    summary: str
    #: secret-shaped: the value is never shown (every `secret`, and the credential bootstraps).
    secret: bool = False
    #: secrets: is the plain `NAME` honoured (outside strict mode), or only `NAME_FILE`?
    allow_env: bool = True
    #: plain: the loader the value comes from, and how to pick the value out of its result.
    loader: Optional[str] = None
    value: Optional[Callable[[Any], Any]] = None
    #: when unset, the value is computed from these variables if any of them is set.
    derived_from: tuple = field(default_factory=tuple)
    #: phase 2: may an owner change it from the board (with ATLAS_CONFIG_WRITES on)? See the
    #: module header for what this mark claims. Changeable settings take effect at once.
    changeable: bool = False
    #: changeable only: (candidate value from the request) -> canonical text, or ValueError.
    validate: Optional[Callable[[Any], str]] = None
    #: who reads it (TASK-1433): a subset of SERVICES. Only atlas-api's are on the panel.
    services: tuple = ("atlas-api",)
    #: compose entries: what compose does with the value (which service or setting gets it).
    compose_use: str = ""
    #: compose entries: a deploy script that reads the value from .env itself, in place of a
    #: ${...} interpolation (render-gateway.py, TASK-1460); compose_use then says what it does.
    reader: str = ""


def _plain(name, part, loader, value, summary, derived_from=(), kind="plain", validate=None,
           services=(API_SVC,)):
    return Setting(name=name, part=part, kind=kind, summary=summary, loader=loader, value=value,
                   derived_from=tuple(derived_from), changeable=validate is not None,
                   validate=validate, services=tuple(services))


def _bool_text(name: str):
    """A changeable on/off setting: True/False, or a word its own loader accepts -> "1"/"0"."""
    def check(value):
        if isinstance(value, bool):
            return "1" if value else "0"
        word = value.strip().lower() if isinstance(value, str) else None
        if word in S._TRUE:
            return "1"
        if word in S._FALSE:
            return "0"
        raise ValueError("%s takes on or off (true/false, 1/0); got %r." % (name, value))
    return check


def _int_text(name: str, low: int, high: int):
    """A changeable whole number in low..high -> its decimal text. Stricter than a reader that
    clamps or falls back: a value the board stores is one the reader uses as given."""
    def check(value):
        if isinstance(value, bool):
            value = None
        try:
            n = int(value.strip()) if isinstance(value, str) else (
                value if isinstance(value, int) else None)
        except ValueError:
            n = None
        if n is None or not low <= n <= high:
            raise ValueError("%s takes a whole number from %d to %d; got %r." % (name, low, high, value))
        return str(n)
    return check


def _secret(name, part, summary, allow_env=True, kind="secret", services=(API_SVC,)):
    return Setting(name=name, part=part, kind=kind, summary=summary, secret=True,
                   allow_env=allow_env, services=tuple(services))


def _hint(var):
    table, component = next((t, c) for t, c, v in S.DEPLOY_HINT_ENVS if v == var)
    return lambda h: getattr(h, table)[component]


_O = lambda attr: (lambda ident: getattr(ident.oidc, attr))  # noqa: E731


def _file_only(file_var: str) -> str:
    """The secret a file-only `NAME_FILE` variable holds. The GitHub secrets are named here by
    their `_FILE` form, as github_link.py and the OSS .env.example name them, because only that
    form was ever read (github_link reads them with allow_env=False)."""
    return file_var[: -len("_FILE")]

CATALOG: tuple = (
    # ---- the install as a whole
    _plain(S.PUBLIC_URL_ENV, ROOT, "settings", lambda c: c.public_url,
           "The one absolute URL the install is reached on."),
    _plain(S.PRODUCT_NAME_ENV, ROOT, "settings", lambda c: c.product_name,
           "The product's display name."),
    _plain(S.PRIVACY_CONTACT_ENV, ROOT, "settings", lambda c: c.privacy_contact,
           "The mailbox the privacy page names."),
    _plain(S.PRIVACY_HOSTING_ENV, ROOT, "settings", lambda c: c.privacy_hosting,
           "Where the privacy page says the data is hosted, as plain text."),
    _plain(S.DISABLED_FEATURES_ENV, ROOT, "features", lambda f: sorted(f.disabled),
           "Optional features switched off by name; empty means every feature is on.",
           services=BOTH),
    _plain(S.PUBLIC_DOCS_ENV, ROOT, "public_docs", lambda v: v,
           "Serve the /docs handbook without sign-in."),
    _plain(S.LANDING_ENV, ROOT, "landing", lambda v: v,
           "Which public landing page / serves: v1 (today's) or v2 (the product website)."),
    _plain("ATLAS_CONFIG_WRITES", ROOT, "config_writes", lambda v: v,
           "Let an owner change the changeable settings from the board, with their password."),
    _plain(S.DEPLOY_HINT_HOST_ENV, ROOT, "deploy_hints", lambda h: h.host,
           "The host the deploy hint commands name.", services=BOTH),
    *(_plain(var, ROOT, "deploy_hints", _hint(var),
             "The deploy hint command for %s (%s)." % (component, table.replace("_", " ")),
             derived_from=(S.DEPLOY_HINT_HOST_ENV,), services=BOTH)
      for table, component, var in S.DEPLOY_HINT_ENVS),

    # ---- the API: who may reach it, and how it behaves
    _plain(S.LOGIN_ORIGINS_ENV, API, "settings", lambda c: c.login_origins,
           "The origins a browser may sign in from. Required to start.",
           derived_from=(S.PUBLIC_URL_ENV,), kind="bootstrap"),
    _plain(S.BEARER_HOSTS_ENV, API, "settings", lambda c: sorted(c.bearer_hosts),
           "Hosts on which a Bearer token is honoured; empty means every allowed host.",
           derived_from=(S.PUBLIC_URL_ENV,)),
    _plain(S.INTERNAL_HOSTS_ENV, API, "settings", lambda c: sorted(c.internal_hosts),
           "Hosts admitted for Bearer requests only, such as a compose service name."),
    _plain(S.STRICT_ENV, API, "strict_secrets", lambda v: v,
           "Refuse a secret given as a plain environment variable; require NAME_FILE.",
           services=BOTH),
    _secret("ATLAS_PASSWORD_PEPPER", API,
            "The password pepper. Fixed for the life of the install.", kind="bootstrap"),
    _secret("ATLAS_TOKEN_PEPPER", API,
            "The service-token pepper. Fixed for the life of the install.", kind="bootstrap"),
    _plain(S.ACCEPTANCE_AGENT_WRITE_ENV, API, "settings", lambda c: c.acceptance_agent_write,
           "Whether an agent may change acceptance criteria with a person's attested approval."),
    _plain(S.STRICT_API_ENV, API, "strict_api", lambda v: v,
           "Refuse a write that carries a field the route ignores, instead of warning.",
           validate=_bool_text(S.STRICT_API_ENV)),
    _plain(S.RBAC_ENV, API, "rbac", lambda r: r.mode, "Project roles: off or projects."),
    _plain(S.DEFAULT_PROJECT_VISIBILITY_ENV, API, "rbac", lambda r: r.default_project_visibility,
           "Whether a new project is shared or private under project roles."),
    _plain(S.SHARED_PROJECT_ROLE_ENV, API, "rbac", lambda r: r.shared_project_role,
           "The role every account holds on a shared project under project roles."),
    _plain(S.INSTANCE_ADMIN_IMPLICIT_OWNER_ENV, API, "rbac",
           lambda r: r.instance_admin_implicit_project_owner,
           "Whether an instance owner is implicitly owner of every project."),
    _plain(S.AUTH_PROVIDERS_ENV, API, "identity", lambda i: list(i.providers),
           "The sign-in providers offered."),
    _plain(S.LOCAL_LOGIN_ENV, API, "identity", lambda i: i.local_login,
           "Who may sign in with a password: everyone, or break-glass accounts only."),
    _plain(S.OIDC_ISSUER_ENV, API, "identity", _O("issuer"), "The OIDC issuer URL.",
           services=BOTH),
    _plain(S.OIDC_CLIENT_ID_ENV, API, "identity", _O("client_id"),
           "The OIDC client id of this install's app registration."),
    _secret(S.OIDC_CLIENT_SECRET_ENV, API, "The OIDC client secret. Read from a file only.",
            allow_env=False),
    _secret(S.OIDC_CLIENT_KEY_ENV, API,
            "The private key for private_key_jwt. Read from a file only.", allow_env=False),
    _plain(S.OIDC_CLIENT_KEY_ID_ENV, API, "identity", _O("client_key_id"),
           "The key id sent with private_key_jwt."),
    _plain(S.OIDC_TOKEN_AUTH_ENV, API, "identity", _O("token_auth"),
           "How the client authenticates at the token endpoint."),
    _plain(S.OIDC_REDIRECT_URI_ENV, API, "identity", _O("redirect_uri"),
           "The OIDC callback URL.", derived_from=(S.PUBLIC_URL_ENV,)),
    _plain(S.OIDC_SCOPES_ENV, API, "identity", lambda i: list(i.oidc.scopes),
           "The scopes requested at sign-in."),
    _plain(S.OIDC_PRESET_ENV, API, "identity", _O("preset"),
           "The identity provider preset: generic, entra or keycloak."),
    _plain(S.OIDC_NAME_CLAIM_ENV, API, "identity", _O("name_claim"),
           "Overrides the preset's name claim."),
    _plain(S.OIDC_EMAIL_CLAIM_ENV, API, "identity", _O("email_claim"),
           "Overrides the preset's email claim."),
    _plain(S.OIDC_GROUPS_CLAIM_ENV, API, "identity", _O("groups_claim"),
           "Overrides the preset's groups claim."),
    _plain(S.OIDC_GROUP_MAP_FILE_ENV, API, "group_map", lambda path: path,
           "The path of the allowlist mapping identity-provider groups to Atlas groups."),
    _plain(S.OIDC_REQUIRED_GROUPS_ENV, API, "identity", lambda i: sorted(i.oidc.required_groups),
           "Identity-provider groups of which a person must hold one to sign in with SSO."),
    _plain(S.OIDC_MAX_SESSION_HOURS_ENV, API, "identity", _O("max_session_hours"),
           "The absolute lifetime of an SSO session, in hours."),
    _plain(S.OIDC_REVALIDATE_DAYS_ENV, API, "identity", _O("revalidate_days"),
           "Days after the last SSO sign-in before that person's agent tokens stop working."),
    _plain(S.OIDC_STEP_UP_ENV, API, "identity", _O("step_up"),
           "Whether an SSO person creates their own agent credential after a fresh sign-in "
           "at the identity provider instead of a password."),
    _plain(S.OIDC_STEP_UP_MAX_AGE_ENV, API, "identity", _O("step_up_max_age"),
           "How old, in seconds, that fresh sign-in may be when the credential is created."),
    _plain(S.OAUTH_RESOURCE_ENV, API, "oauth", lambda o: o.resource,
           "The protected resource identity-provider access tokens are issued for.",
           services=BOTH),
    _plain(S.OAUTH_AUDIENCES_ENV, API, "oauth", lambda o: list(o.audiences),
           "Every accepted access-token audience, the resource first."),
    _plain(S.OAUTH_SCOPE_MAP_ENV, API, "oauth",
           lambda o: ["%s=%s" % pair for pair in o.scope_map],
           "Identity-provider scopes mapped to read, write or admin."),
    _plain(S.AUDIT_READS_ENV, API, "audit", lambda a: a.reads,
           "Record every agent read of a node, a search or a document."),
    _plain(S.AUDIT_RETENTION_DAYS_ENV, API, "audit", lambda a: a.retention_days,
           "Days an agent-read audit row is kept; none means forever."),
    _plain(S.SETUP_QUESTIONS_FILE_ENV, API, "setup_questions", lambda v: v,
           "An extra question file for the setup guide, asked after the shipped topics."),
    # TASK-1419 (OSS-26 F): the server certificate whose expiry /api/health reports.
    _plain(S.CERT_EXPIRY_FILE_ENV, API, "cert_expiry", lambda c: c.file,
           "The PEM certificate whose expiry /api/health warns about; none means no field."),
    _plain(S.CERT_EXPIRY_WARN_DAYS_ENV, API, "cert_expiry", lambda c: c.warn_days,
           "How many days before that certificate expires /api/health starts to warn."),
    _secret(_file_only("ATLAS_GITHUB_OAUTH_CLIENT_ID_FILE"), API,
            "The GitHub OAuth app's client id. Read from a file only.", allow_env=False),
    _secret(_file_only("ATLAS_GITHUB_OAUTH_CLIENT_SECRET_FILE"), API,
            "The GitHub OAuth app's client secret. Read from a file only.", allow_env=False),
    _secret(_file_only("ATLAS_GITHUB_INVITE_TOKEN_FILE"), API,
            "The GitHub token that sends repository invitations. Read from a file only.",
            allow_env=False),
    _plain("ATLAS_GITHUB_REPO", API, "github_repo", lambda v: v,
           "The owner/name of the repository GitHub linking grants access to."),

    _plain("ATLAS_ALLOW_SESSION_MINT", API, "session_mint", lambda v: v,
           "Allow minting a short browser session for a verification pass (off by default)."),
    _plain("ATLAS_MACHINE_GRACE_HOSTS", API, "machine_grace", lambda v: v,
           "Hosts with the staged-rollout grace for machine callers; empty means none."),
    _plain("ATLAS_TRUSTED_PROXY_CIDRS", API, "trusted_proxies", lambda v: v,
           "Proxy networks whose client-address header is believed; empty means none."),
    _plain("ATLAS_BREACH_CHECK", API, "breach_check", lambda v: v,
           "How a new password is screened against breached ones: remote or bundled."),
    _plain("ATLAS_FRONTEND_DIR", API, "frontend_dir", lambda v: v,
           "The directory the board and its assets are served from."),
    _plain("ATLAS_ADMIN_ACTOR", API, "admin_actor", lambda v: v,
           "The operator name the admin command line records; none means the OS user."),
    _plain("ATLAS_DEPLOY_SRC", ROOT, "deploy_status", lambda d: d[0],
           "The checkout the deploy status compares the running image against.", services=BOTH),
    _plain("ATLAS_DEPLOY_WATCH_FILE", ROOT, "deploy_status", lambda d: d[1],
           "The status file the deploy watcher writes.", services=BOTH),

    # ---- the retrieval add-on (retrieval/__init__.py is the parser)
    _plain("ATLAS_RETRIEVAL", RETRIEVAL, "retrieval_switch", lambda v: v,
           "The semantic retrieval add-on: on or off."),
    _plain("ATLAS_EMBED_URL", RETRIEVAL, "retrieval", lambda c: c.url or None,
           "The OpenAI-compatible embeddings endpoint."),
    _plain("ATLAS_EMBED_MODEL", RETRIEVAL, "retrieval", lambda c: c.model or None,
           "The embedding model; changing it re-indexes."),
    _plain("ATLAS_EMBED_DIM", RETRIEVAL, "retrieval", lambda c: c.dim or None,
           "The vector length the endpoint returns."),
    _secret("ATLAS_EMBED_KEY", RETRIEVAL,
            "The endpoint's key. Read from a file only, on every call.", allow_env=False),
    _plain("ATLAS_EMBED_AUTH_HEADER", RETRIEVAL, "retrieval", lambda c: c.auth_header,
           "How the endpoint is authenticated: bearer, api-key or entra."),
    _plain("ATLAS_EMBED_ENTRA_SCOPE", RETRIEVAL, "retrieval_entra_scope", lambda v: v,
           "The token scope in entra mode."),
    _plain("ATLAS_EMBED_CA_BUNDLE", RETRIEVAL, "retrieval", lambda c: c.ca_bundle,
           "The path of a PEM bundle the endpoint's certificate is checked against."),
    _plain("ATLAS_EMBED_DOC_PREFIX", RETRIEVAL, "retrieval", lambda c: c.doc_prefix,
           "Text put before every document sent to be embedded."),
    _plain("ATLAS_EMBED_QUERY_PREFIX", RETRIEVAL, "retrieval", lambda c: c.query_prefix,
           "Text put before every query sent to be embedded."),
    _plain("ATLAS_EMBED_SCOPE", RETRIEVAL, "retrieval", lambda c: c.scope,
           "Which nodes' text may be sent: shared (never a privately marked node) or all."),
    _plain("ATLAS_EMBED_BATCH", RETRIEVAL, "retrieval", lambda c: c.batch,
           "Texts per embeddings call."),
    _plain("ATLAS_RETRIEVAL_INTERVAL_S", RETRIEVAL, "retrieval", lambda c: c.interval_s,
           "Seconds between indexing passes."),
    _plain("ATLAS_RETRIEVAL_TOP_K", RETRIEVAL, "retrieval_top_k", lambda v: v,
           "How many nodes the vector list contributes to a search.",
           validate=_int_text("ATLAS_RETRIEVAL_TOP_K", 1, 200)),

    # ---- the database
    _secret(S.DB_URL_ENV, POSTGRES,
            "The database URL, which carries its password. Required to start.", kind="bootstrap"),
    _plain(S.SEED_ENV, POSTGRES, "settings", lambda c: c.seed,
           "What startup seeds into an empty database: homelab, demo, none or self."),
    _plain(S.LEGACY_V0_ENV, POSTGRES, "settings", lambda c: c.legacy_v0,
           "Create the first-generation tables and serve their routes."),
    _plain("ATLAS_RESEED", POSTGRES, "reseed", lambda v: v,
           "Wipe and reseed the graph (never the accounts) at the next start."),
    # OSS-26 C: routine backups by the `backup` profile's loop, outside crunch week.
    _plain(S.BACKUP_SCHEDULE_ENV, POSTGRES, "backup_schedule", lambda b: b.schedule,
           "How often the backup profile takes a routine backup outside crunch week: off, "
           "hourly, daily or weekly."),
    _plain(S.BACKUP_KEEP_ENV, POSTGRES, "backup_schedule", lambda b: b.keep,
           "How many routine backups are kept; older routine backups are deleted."),
)

BY_NAME: dict = {s.name: s for s in CATALOG}

#: The other services' variables (module header, OTHER). JSON beside the code, shipped in the image.
OTHER_FILE = Path(__file__).resolve().parent / "data" / "config" / "other-services.json"
_OTHER: Optional[tuple] = None


def other_services() -> tuple:
    """What atlas-mcp alone or docker compose alone reads, as Settings (never on the panel)."""
    global _OTHER
    if _OTHER is None:
        import json
        doc = json.loads(OTHER_FILE.read_text(encoding="utf-8"))
        out = [Setting(name=e["name"], part=MCP, kind="secret" if e.get("secret") else "plain",
                       summary=e["summary"], secret=bool(e.get("secret")), services=(MCP_SVC,))
               for e in doc[MCP_SVC]]
        out += [Setting(name=e["name"], part=e["part"], kind="plain", summary=e["summary"],
                        services=(COMPOSE_SVC,), compose_use=e["use"], reader=e.get("reader", ""))
                for e in doc[COMPOSE_SVC]]
        _OTHER = tuple(out)
    return _OTHER


def all_settings() -> tuple:
    """Every variable an install sets: CATALOG (atlas-api's) then other_services()."""
    return CATALOG + other_services()


def lookup(name: str) -> Optional[Setting]:
    """The entry for `name`, atlas-api's or another service's; None when nothing reads it."""
    return BY_NAME.get(name) or next((s for s in other_services() if s.name == name), None)

#: The changeable settings, in catalog order.
CHANGEABLE: tuple = tuple(s.name for s in CATALOG if s.changeable)

_BOUNDARY = ("a trust boundary (who may sign in, see or do what), and changing one stays a "
             "deliberate act on the server's environment")
_TRUST = "it is " + _BOUNDARY
#: Plain settings refused for change DELIBERATELY, with the reason the refusal names. Every
#: other non-changeable plain setting is refused with `_NOT_YET`; secrets and bootstrap
#: settings are refused by kind, before this table is consulted.
EXCLUDED: dict = {
    S.DISABLED_FEATURES_ENV: "read by atlas-mcp from its own environment as well, and its "
                             "github_link switch decides which routes are registered at import; "
                             "a board value would reach neither",
    "ATLAS_CONFIG_WRITES": "the switch for this change path itself",
    S.ACCEPTANCE_AGENT_WRITE_ENV: "it guards the rail that requires a person's approval for an "
                                  "agent's change to acceptance criteria",
    S.AUDIT_READS_ENV: "read once at import, and switching the read audit off crosses " + _BOUNDARY,
    S.AUDIT_RETENTION_DAYS_ENV: "it decides how long the read audit is kept",
    S.PUBLIC_DOCS_ENV: "read once at import into the gate's public routes, and it is " + _BOUNDARY,
    S.LANDING_ENV: "read once at import into the gate's public routes, and it is " + _BOUNDARY,
    "ATLAS_ALLOW_SESSION_MINT": _TRUST,
    "ATLAS_MACHINE_GRACE_HOSTS": _TRUST,
    "ATLAS_TRUSTED_PROXY_CIDRS": _TRUST,
    "ATLAS_BREACH_CHECK": "it decides how new passwords are screened, which is " + _BOUNDARY,
    S.STRICT_ENV: "it decides how secrets may be given, which is " + _BOUNDARY,
    S.BEARER_HOSTS_ENV: _TRUST,
    S.INTERNAL_HOSTS_ENV: _TRUST,
    **{n: _TRUST for n in (S.RBAC_ENV, S.DEFAULT_PROJECT_VISIBILITY_ENV,
                           S.SHARED_PROJECT_ROLE_ENV, S.INSTANCE_ADMIN_IMPLICIT_OWNER_ENV)},
    **{s.name: _TRUST for s in CATALOG if s.loader in ("identity", "group_map", "oauth")},
}
_NOT_YET = ("not in the first changeable set: it is read once when the API starts, or nobody "
            "has yet shown that its reader re-reads it; set it in the environment")


def change_refusal(name: str) -> Optional[tuple]:
    """(error code, reason) when the board may not change `name`, or None when it may.

    The order is the guarantee: an unknown name, then BOOTSTRAP (refused in every mode,
    forever), then secret, then everything not marked changeable."""
    item = BY_NAME.get(name)
    if item is None:
        return "unknown_setting", "%s is not a setting this API reads." % name
    if item.kind == "bootstrap":
        return ("bootstrap_read_only",
                "%s is needed before the API can start, so it is read-only in every mode; "
                "change it in the server's environment." % name)
    if item.secret:
        return "secret_not_changeable", "%s is a secret; secrets are never set from the board." % name
    if not item.changeable:
        return "not_changeable", "%s cannot be changed from the board: %s." % (
            name, EXCLUDED.get(name, _NOT_YET))
    return None


#: ATLAS_CONFIG_WRITES (STORY-atlas-config-panel, phase 2). Here rather than in settings.py so
#: the whole change path is in this module and routers/config.py; parsed like its neighbours.
CONFIG_WRITES_ENV = "ATLAS_CONFIG_WRITES"


def load_config_writes(environ: Optional[Mapping[str, str]] = None) -> bool:
    """`ATLAS_CONFIG_WRITES`: False when unset (today), True for 1/true/yes/on.

    Set but blank, or any other value, raises `settings.SettingsError` naming the variable;
    main.py reads it at import, so a typo stops the boot."""
    env = os.environ if environ is None else environ
    raw = env.get(CONFIG_WRITES_ENV)
    if raw is None:
        return False
    value = raw.strip().lower()
    if not value:
        raise S.SettingsError("%s is set but empty. Give it a value (1 or 0), or unset it."
                              % CONFIG_WRITES_ENV, variable=CONFIG_WRITES_ENV)
    if value in S._TRUE:
        return True
    if value in S._FALSE:
        return False
    raise S.SettingsError("%s=%r is not one of 1, 0 (or true/false, yes/no, on/off)."
                          % (CONFIG_WRITES_ENV, raw), variable=CONFIG_WRITES_ENV)

_ATLAS_NAME = re.compile(r"^ATLAS_[A-Z0-9_]+$")


def names_in_source(source: str) -> set:
    """Every `ATLAS_*` string literal in a module's source, exactly as written."""
    return {node.value for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and _ATLAS_NAME.match(node.value)}


def is_described(name: str, *, api_only: bool = False) -> bool:
    """Is `name` described (in CATALOG, or with `api_only` False in other_services() too), or the
    `NAME_FILE` form of a secret that is?"""
    find = BY_NAME.get if api_only else lookup
    if find(name) is not None:
        return True
    base = name[: -len("_FILE")] if name.endswith("_FILE") else None
    item = find(base) if base else None
    return bool(item is not None and item.secret)


#: The test harness, which sets variables for the code under test rather than reading them.
_HARNESS = re.compile(r"(^|/)(verify_[^/]*|test_[^/]*|testdb|testseed|[^/]*testkit[^/]*)\.py$")


def api_modules(root: Optional[Path] = None) -> list:
    """Every module of the API under backend/, the test harness excluded."""
    root = root or Path(__file__).resolve().parent
    return [p for p in sorted(root.rglob("*.py"))
            if not _HARNESS.search(p.relative_to(root).as_posix())
            and not any(part.startswith(".") for part in p.relative_to(root).parts)]


def names_in_api(root: Optional[Path] = None) -> dict:
    """name -> the modules (relative paths) that write it as an `ATLAS_*` string literal."""
    root = root or Path(__file__).resolve().parent
    out: dict = {}
    for p in api_modules(root):
        for name in names_in_source(p.read_text(encoding="utf-8")):
            out.setdefault(name, []).append(p.relative_to(root).as_posix())
    return out


def undescribed(source: Optional[str] = None) -> list:
    """The `ATLAS_*` names the API's modules (or one `source` text) write that CATALOG lacks."""
    names = names_in_source(source) if source is not None else set(names_in_api())
    return sorted(n for n in names if not is_described(n, api_only=True))


# --------------------------------------------------------------------------- #
# Evaluation: effective value and source, against an environment mapping
# --------------------------------------------------------------------------- #

def _set(env: Mapping[str, str], name: str) -> bool:
    return bool((env.get(name) or "").strip())


def secret_state(env: Mapping[str, str], item: Setting) -> tuple:
    """(state, source) for a secret, by `settings.read_secret`'s precedence, WITHOUT reading it.

    The file wins when `NAME_FILE` names a readable, non-empty regular file; otherwise the plain
    `NAME` counts where it is honoured (`allow_env`, and not in strict mode); otherwise not set.
    """
    path = (env.get(item.name + "_FILE") or "").strip()
    if path:
        try:
            ok = os.path.isfile(path) and os.access(path, os.R_OK) and os.path.getsize(path) > 0
        except OSError:
            ok = False
        return (SECRET_FROM_FILE, "file") if ok else (SECRET_NOT_SET, None)
    strict = (env.get(S.STRICT_ENV) or "").strip().lower() in S._TRUE
    if item.allow_env and not strict and _set(env, item.name):
        return SECRET_FROM_ENV, "env"
    return SECRET_NOT_SET, None


class _Inactive(Exception):
    """A loader that is not run in this configuration (the add-on is off)."""


def _loaders(env: Mapping[str, str], identity_settings=None, oauth_settings=None) -> dict:
    """name -> zero-argument callable returning the loader's parsed result for `env`."""
    def ident():
        return identity_settings if identity_settings is not None else S.load_identity(env)

    def oauth():
        return oauth_settings if oauth_settings is not None else S.load_oauth(env, identity=ident())

    def github_repo():
        # github_link.repo()'s rule (settings.py); github_link itself needs the peppers.
        return S.load_github_repo(env)

    def group_map():
        # The OIDC provider loads the group map when it is built, so a broken file stops the
        # boot (TASK-1439 names the variable). The same rule, the same condition, here.
        cfg = ident()
        if "oidc" in cfg.providers:
            S.load_group_map(cfg.oidc.group_map_file)
        return cfg.oidc.group_map_file

    def addon_on():
        # With the add-on off, main.py imports nothing of it beyond its switch; reading a value
        # must not change that, so its other modules are imported only when it is on.
        import retrieval
        if not retrieval.enabled(env):
            raise _Inactive()

    def retrieval_switch():
        import retrieval
        return retrieval.enabled(env)

    def retrieval_config():
        import retrieval
        return retrieval.load_config(env)

    def retrieval_top_k():
        addon_on()
        from retrieval import query
        return query.top_k(env)

    def entra_scope():
        addon_on()
        from retrieval import entra
        return (env.get("ATLAS_EMBED_ENTRA_SCOPE") or "").strip() or entra.DEFAULT_SCOPE

    # The rules auth, passwords and deps apply to os.environ, applied to `env` (settings.py,
    # TASK-1433); those modules need the peppers to import, and read os.environ, not `env`.
    def session_mint():
        return S.load_session_mint(env)

    def machine_grace():
        return sorted(S.load_machine_grace_hosts(env))

    def trusted_proxies():
        return [str(n) for n in S.load_trusted_proxy_networks(env)]

    def breach_check():
        # passwords.check_breached's inline rule: unset or blank means remote.
        return (env.get("ATLAS_BREACH_CHECK") or "remote").strip().lower() or "remote"

    def frontend_dir():
        return S.load_frontend_dir(env)

    def deploy_paths():
        import deploy_status   # standard library only; its paths() is the rule it applies at import
        return deploy_status.paths(env)

    def admin_actor():
        # admin's rule: the stripped value, else the OS user (shown here as none).
        return (env.get("ATLAS_ADMIN_ACTOR") or "").strip() or None

    def reseed():
        # main.startup's inline rule.
        return (env.get("ATLAS_RESEED") or "").lower() in ("1", "true", "yes")

    return {
        "retrieval_switch": retrieval_switch,
        "retrieval": retrieval_config,
        "retrieval_top_k": retrieval_top_k,
        "retrieval_entra_scope": entra_scope,
        "session_mint": session_mint,
        "machine_grace": machine_grace,
        "trusted_proxies": trusted_proxies,
        "breach_check": breach_check,
        "frontend_dir": frontend_dir,
        "deploy_status": deploy_paths,
        "admin_actor": admin_actor,
        "reseed": reseed,
        "settings": lambda: S.load(env),
        "rbac": lambda: S.load_rbac(env),
        "features": lambda: S.load_features(env),
        "deploy_hints": lambda: S.load_deploy_hints(env),
        "identity": ident,
        "group_map": group_map,
        "oauth": oauth,
        "strict_api": lambda: S.load_strict_api(env),
        "audit": lambda: S.load_audit(env),
        "public_docs": lambda: S.load_public_docs(env),
        "landing": lambda: S.load_landing(env),
        "config_writes": lambda: load_config_writes(env),
        "setup_questions": lambda: S.load_setup_questions_file(env),
        "backup_schedule": lambda: S.load_backup_schedule(env),
        "cert_expiry": lambda: S.load_cert_expiry(env),
        "strict_secrets": lambda: (env.get(S.STRICT_ENV) or "").strip().lower() in S._TRUE,
        "github_repo": github_repo,
    }


def _jsonable(value):
    if isinstance(value, (frozenset, set, tuple)):
        return sorted(value) if isinstance(value, (frozenset, set)) else list(value)
    return value


def describe(environ: Optional[Mapping[str, str]] = None, *, identity_settings=None,
             oauth_settings=None, writes: bool = False,
             board: Optional[Mapping[str, str]] = None) -> dict:
    """The configuration panel's data: one section per part node, each setting with its
    effective value (plain) or its state (secret), and its source.

    A secret's entry carries `state` and `source` and NO `value` key, ever.

    `writes` (phase 2, ATLAS_CONFIG_WRITES on): the body says `read_only: false` and carries a
    `writes` block, and every entry says whether it is `changeable`. `board` names the settings
    whose effective value is a board-set one (models_config.board_applied()); those read source
    `board`. With `writes` False the output is exactly phase 1's, and `board` is ignored.
    """
    env = os.environ if environ is None else environ
    makers = _loaders(env, identity_settings, oauth_settings)
    cache: dict = {}
    #: loader -> the variable its error names (the exception's `variable`, set where it was
    #: raised; TASK-1439), when that variable is a plain setting of this catalogue.
    culprit: dict = {}

    def loaded(name):
        if name not in cache:
            try:
                cache[name] = (True, makers[name]())
            except _Inactive:
                cache[name] = (None, None)
            except Exception as exc:  # noqa: BLE001 — named in the entry, never raised
                cache[name] = (False, type(exc).__name__)
                blamed = getattr(exc, "variable", None)
                if blamed in BY_NAME and not BY_NAME[blamed].secret:
                    culprit[name] = blamed
        return cache[name]

    def unparsed(type_name):
        return "the current environment does not parse (%s)" % type_name

    entries: dict = {}
    parts = {p: [] for p in PARTS}
    for item in CATALOG:
        entry = {"name": item.name, "kind": item.kind, "secret": item.secret,
                 "summary": item.summary}
        if writes:
            entry["changeable"] = item.changeable
        if item.secret:
            entry["state"], entry["source"] = secret_state(env, item)
        else:
            if writes and board and item.name in board:
                entry["source"] = "board"
            elif _set(env, item.name):
                entry["source"] = "env"
            elif any(_set(env, d) for d in item.derived_from):
                entry["source"] = "derived"
                entry["derived_from"] = [d for d in item.derived_from if _set(env, d)]
            else:
                entry["source"] = "default"
            ok, result = loaded(item.loader)
            if ok:
                entry["value"] = _jsonable(item.value(result))
            elif ok is None:
                entry["value"] = None
                entry["inactive"] = "not read while the retrieval add-on is off"
            elif item.loader in culprit and culprit[item.loader] != item.name:
                # Another variable of this loader is the one at fault: it carries the error,
                # and this one is merely not read until that one parses.
                entry["value"] = None
                entry["blocked_by"] = culprit[item.loader]
            else:
                entry["value"] = None
                entry["error"] = unparsed(result)
        entries[item.name] = entry
        parts[item.part].append(entry)
    # A loader's error can name a setting another loader reads (a broken OIDC group map
    # stops the import of `auth`, which ATLAS_ALLOW_SESSION_MINT's reader needs): the named
    # setting carries that error too, so it is reported where the operator must change it.
    for loader, name in culprit.items():
        entry = entries[name]
        if "error" not in entry:
            entry.pop("blocked_by", None)
            entry.pop("inactive", None)
            entry["value"] = None
            entry["error"] = unparsed(cache[loader][1])
    out = []
    for p in PARTS:
        part = {"id": p, "settings": parts[p]}
        if not parts[p] and p in EMPTY_PART_NOTES:
            part["note"] = EMPTY_PART_NOTES[p]
        if p == RETRIEVAL:
            part["status"] = _retrieval_status(loaded)
        out.append(part)
    if not writes:
        return {"read_only": True, "parts": out}
    return {"read_only": False, "parts": out,
            "writes": {"enabled": True, "step_up": "password",
                       "precedence": ["env", "board", "default"],
                       "changeable": list(CHANGEABLE), "effect": "now"}}


def _retrieval_status(loaded) -> str:
    """The add-on's own verdict on this configuration: off, on, or unavailable with the first
    problem `retrieval.load_config` found (its messages never contain a secret)."""
    ok, on = loaded("retrieval_switch")
    if not ok:
        return "the switch does not parse (%s)" % on
    if not on:
        return "off"
    ok, cfg = loaded("retrieval")
    if not ok:
        return "unavailable: the configuration does not parse (%s)" % cfg
    return "unavailable: %s" % cfg.problem if cfg.problem else "on"
