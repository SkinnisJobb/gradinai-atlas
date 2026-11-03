"""
The one place Atlas reads a secret (STORY-oss-03-secret-files).

ADDITIVE, OPT-IN. Every secret Atlas read from a plain environment variable
before this story is still read from that variable, unchanged, by default — a
deployment that sets no new configuration behaves exactly as before. What this
module ADDS is the Docker-secrets convention (`NAME_FILE` -> a file such as
`/run/secrets/...`, which keeps the value out of `docker inspect`, out of
`/proc/<pid>/environ` and out of every child process's environment), plus an
opt-in strict mode that refuses the plain env var.

Concretely, `read_secret("ATLAS_TOKEN_PEPPER")`:

  1. `ATLAS_TOKEN_PEPPER_FILE` is set -> read that file. The file WINS: when the
     plain `ATLAS_TOKEN_PEPPER` is ALSO set it is ignored and a warning naming
     both variables (never the value) is logged, so a stale env value can never
     shadow a rotated file. A set-but-unreadable path is an error, never a
     silent fall-through to the env var.
  2. Otherwise `ATLAS_TOKEN_PEPPER` is set -> accepted (the pre-existing
     behaviour), UNLESS strict mode is on: `ATLAS_REQUIRE_SECRET_FILES=1`
     refuses a plain env secret, so an install that opts in cannot drift back
     into the env-var shape by accident.
  3. Neither -> missing (or `default`, outside strict mode).

The value is stripped of surrounding whitespace, so the trailing newline that
`echo ... > file` and most editors leave is not part of the secret. An empty
value (empty file, whitespace-only file) counts as missing. A plain `NAME`
that is SET but blank (`NAME=`, whitespace) is an error for a required secret,
naming the variable, and never selects `default` (TASK-1348); an optional one
still reads it as missing.

A REQUIRED secret that is missing raises `SecretError`, which the callers turn
into their own fail-at-import error (auth, passwords) — ADR 0015's "a missing
required secret fails fast". An OPTIONAL one (`required=False`) returns `None`
instead, so a feature can switch itself off cleanly (github_link).

`allow_env=False` is for secrets that were only ever read from a file (the
GitHub ones): their plain name was never honoured, so it is not started now.

NO VALUE EVER LEAVES THIS MODULE EXCEPT AS THE RETURN VALUE. `SecretError`
messages and the one warning name the variable and, for a file, its path —
never its contents.

Non-secret configuration is read by `load()`, the second half of this module
(STORY-oss-06): one typed `Settings` object.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import urlsplit

# The estate-valued defaults (STORY-oss-23 A1): the seed profile, the v0 switch, the privacy
# mailbox and hosting text, the deploy hint host and commands. They live in `distribution.py`,
# which the public export replaces with a neutral copy; this module keeps its own names for
# them below, so no caller changes. Standard library only, like this module.


def _load_distribution():
    """`distribution.py`, this module's sibling (STORY-oss-23 A1).

    Imported by name, so whatever is first on sys.path wins (backend/, or the neutral
    overlay a verify script puts in front of it). When this module is itself loaded BY
    PATH from somewhere backend/ is not on sys.path (mcp/verify_feature_flags.py does
    that), the name does not resolve; the sibling file is then loaded by path, the way
    mcp/app/settings.py loads its own copy.
    """
    try:
        import distribution as dist
        return dist
    except ModuleNotFoundError as exc:
        if exc.name != "distribution":
            raise
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_atlas_backend_distribution", Path(__file__).resolve().with_name("distribution.py"))
        dist = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dist)
        return dist


distribution = _load_distribution()

__all__ = [
    "STRICT_ENV", "SecretError", "strict_mode", "read_secret",
    "Settings", "SettingsError", "load", "origin_host_forms",
]

logger = logging.getLogger("atlas.settings")

STRICT_ENV = "ATLAS_REQUIRE_SECRET_FILES"
_TRUE = ("1", "true", "yes", "on")


class _NamedError(RuntimeError):
    """A configuration error that knows WHICH variable it is about (TASK-1439).

    `variable` is set where the error is raised, by the code that knows what it was
    reading, never recovered from the message text. The configuration catalogue
    (settings_catalog.describe) reports the error against that variable rather than
    against whichever setting happened to ask for the loader first. None when no one
    variable is at fault. The message (`str(exc)`) is unchanged by it."""

    def __init__(self, *args, variable: Optional[str] = None):
        super().__init__(*args)
        self.variable = variable


class SecretError(_NamedError):
    """A secret is missing, empty, unreadable or refused. The message names the
    variable (and a file path), never the value. `variable` is the secret's name
    (without `_FILE`)."""


def strict_mode() -> bool:
    """Is the opt-in strict mode on? Read on every call, like the secret."""
    return os.environ.get(STRICT_ENV, "").strip().lower() in _TRUE


def read_secret(
    name: str,
    *,
    required: bool = True,
    default: Optional[str] = None,
    allow_env: bool = True,
) -> Optional[str]:
    """Return the secret `name`, from `name_FILE` or (outside strict mode) `name`.

    A required secret raises `SecretError` when it is missing, empty, refused
    (env-only in strict mode) or when `name_FILE` names an unreadable file.
    An optional secret returns `None` in every one of those cases.

    `default` is returned only when NEITHER variable is set and strict mode is
    off (a local-dev fallback); a configured-but-broken secret still raises, so
    a typo in a path, or a `NAME` set to an empty value, can never silently
    select the default.
    """
    file_var = name + "_FILE"
    strict = strict_mode()
    path = os.environ.get(file_var, "").strip()
    if path:
        if allow_env and os.environ.get(name, "").strip():
            logger.warning(
                "%s and %s are both set; using the file named by %s and ignoring %s.",
                file_var, name, file_var, name,
            )
        try:
            with open(path, "r", encoding="utf-8") as fh:
                value = fh.read().strip()
        except OSError as exc:
            if not required:
                return None
            # The path is configuration, not the secret; the errno name says why.
            raise SecretError(
                "%s points at %r, which cannot be read (%s)." % (file_var, path, type(exc).__name__), variable=name
            ) from None
        except UnicodeDecodeError:
            if not required:
                return None
            raise SecretError("%s points at %r, which is not UTF-8 text." % (file_var, path), variable=name) from None
        if value:
            return value
        if not required:
            return None
        raise SecretError("%s points at %r, which is empty." % (file_var, path), variable=name)

    raw = os.environ.get(name) if allow_env else None
    if raw is not None and raw.strip():
        if not strict:
            return raw.strip()
        if not required:
            return None
        raise SecretError(
            "%s is set as a plain environment variable, which %s=1 refuses. Put the "
            "value in a file and set %s to its path (e.g. a Docker secret under "
            "/run/secrets/), or unset %s."
            % (name, STRICT_ENV, file_var, STRICT_ENV), variable=name
        )

    # Set but blank (`NAME=` or whitespace) is a configuration mistake, not "unset":
    # for a required secret it raises naming the variable, and in particular it never
    # selects `default` (TASK-1348 — a blank ATLAS_DB_URL used to pick the local
    # default database in silence). Strict mode keeps its own message below; an
    # optional secret still returns None, so a feature can still be switched off.
    if raw is not None and required and not strict:
        raise SecretError(
            "%s is set but empty. Give it a value, or unset it%s (or set %s to the "
            "path of a file holding it)."
            % (name, " to use the default" if default is not None else "", file_var), variable=name
        )

    if default is not None and not strict:
        return default
    if not required:
        return None
    if strict:
        raise SecretError(
            "%s is not set: %s=1 requires %s (the path of a file holding it)."
            % (file_var, STRICT_ENV, file_var), variable=name
        )
    raise SecretError(
        "%s is not set (set it, or set %s to the path of a file holding it)." % (name, file_var), variable=name
    )


# --------------------------------------------------------------------------- #
# Central settings (STORY-oss-06-neutral-settings)
# --------------------------------------------------------------------------- #
#
# ADDITIVE, OPT-IN, like the secret reader above. `load()` gathers the
# non-secret configuration `auth.py` and `db.py` used to read for themselves
# into ONE typed, frozen object. With no new variable set, every value it
# yields is exactly what the backend computed before this story:
#
#   * login origins   = `ATLAS_LOGIN_ORIGINS`, still REQUIRED (auth refuses to
#                       boot without it; there is still no permissive default);
#   * bearer hosts    = `ATLAS_BEARER_HOSTS`, unset meaning "every allowed host";
#   * internal hosts  = empty, so the 421 Host check is exactly as strict;
#   * privacy contact = `DEFAULT_PRIVACY_CONTACT`, today's literal (from
#                       `distribution.py`, see below);
#   * privacy hosting = `DEFAULT_PRIVACY_HOSTING`, today's paragraph (TASK-1459);
#   * product name    = `DEFAULT_PRODUCT_NAME`;
#   * DB URL          = `ATLAS_DB_URL`, read exactly as `db.py` read it (moving
#                       it onto `read_secret` is TASK-1349, deliberately not here).
#
# What it ADDS, each one inert until its variable is set:
#
#   * `ATLAS_PUBLIC_URL`: the one absolute URL an install is reached on. When
#     set, the login origins and the bearer hosts are DERIVED from it, but only
#     where their own variable is unset. An explicit `ATLAS_LOGIN_ORIGINS` or
#     `ATLAS_BEARER_HOSTS` always wins, so a deployment that already sets them
#     (every homelab compose override does) is unchanged by adding it.
#   * `ATLAS_PRIVACY_CONTACT`: overrides the mailbox on the privacy page.
#   * `ATLAS_PRIVACY_HOSTING`: overrides the privacy page's "Where it lives"
#     paragraph, plain text (auth escapes it). The default describes the original
#     install's hosting, so every other install must describe its own.
#   * `ATLAS_PRODUCT_NAME`: the product's display name. Not rendered anywhere
#     yet; carried so the carve-out has one place to change it.
#   * `ATLAS_INTERNAL_HOSTS`: comma-separated hosts (e.g. `atlas-api`, the
#     compose service name the MCP can reach without the public hostname) on
#     which a BEARER request is admitted although the host is not a login
#     origin. A cookie or anonymous request to such a host still gets 421: the
#     browser Host check is not relaxed at all. An entry without a port matches
#     any port; `atlas-api:8000` matches that port only.
#
# The personal domain and mailbox stay in code as today's DEFAULTS on purpose
# (the additive rule; the owner's option (a) on the story): the OSS carve-out
# substitutes neutral defaults on export instead of this repo changing what the
# homelab runs. Since STORY-oss-23 A1 that substitution has ONE place to happen:
# every estate-valued default (the seed profile, the v0 switch, the privacy
# mailbox and hosting paragraph, the deploy hint host and commands) is read from
# `distribution.py`, whose project-atlas values are exactly the literals that
# stood here, and the export overlays a neutral copy of that one file
# (`tools/carve/overlay/backend/distribution.py`). The names below stay, so every
# caller and every verify script that reads `settings.DEFAULT_*` is unchanged.

PUBLIC_URL_ENV = "ATLAS_PUBLIC_URL"
LOGIN_ORIGINS_ENV = "ATLAS_LOGIN_ORIGINS"
BEARER_HOSTS_ENV = "ATLAS_BEARER_HOSTS"
INTERNAL_HOSTS_ENV = "ATLAS_INTERNAL_HOSTS"
PRIVACY_CONTACT_ENV = "ATLAS_PRIVACY_CONTACT"
PRIVACY_HOSTING_ENV = "ATLAS_PRIVACY_HOSTING"
PRODUCT_NAME_ENV = "ATLAS_PRODUCT_NAME"
DB_URL_ENV = "ATLAS_DB_URL"
SEED_ENV = "ATLAS_SEED"
LEGACY_V0_ENV = "ATLAS_LEGACY_V0"
ACCEPTANCE_AGENT_WRITE_ENV = "ATLAS_ACCEPTANCE_AGENT_WRITE"

#: STORY-atlas-acceptance-human-approved. `off` is today's rail and the default.
ACCEPTANCE_AGENT_WRITE_MODES = ("off", "attested")
DEFAULT_ACCEPTANCE_AGENT_WRITE = "off"

#: STORY-oss-07: what startup seeds into an EMPTY database. `homelab` is today's
#: behaviour and the default (`seed.py`, the v0 Component tree); `demo` is
#: `demo_seed.py` (one neutral project, one epic, two stories); `none` seeds no data;
#: `self` is `self_seed.py` (TASK-1461: Atlas's own world model, no work), opt-in only.
#: Both come from `distribution.py` (A1): the neutral copy drops `homelab`, whose
#: `seed.py` the export does not ship, and defaults to `none`.
SEED_PROFILES = distribution.SEED_PROFILES
DEFAULT_SEED_PROFILE = distribution.DEFAULT_SEED_PROFILE
#: What an unset ATLAS_LEGACY_V0 means: True here (today), False in the neutral copy.
DEFAULT_LEGACY_V0 = distribution.DEFAULT_LEGACY_V0
_FALSE = ("0", "false", "no", "off")

#: Today's values, from `distribution.py`. `auth.PRIVACY_CONTACT`'s comment says
#: why this mailbox.
DEFAULT_PRIVACY_CONTACT = distribution.DEFAULT_PRIVACY_CONTACT
#: The privacy page's "Where it lives" paragraph, plain text. Today's install's
#: hosting (TASK-1459); an install hosted anywhere else sets ATLAS_PRIVACY_HOSTING.
DEFAULT_PRIVACY_HOSTING = distribution.DEFAULT_PRIVACY_HOSTING
DEFAULT_PRODUCT_NAME = "Atlas"


class SettingsError(_NamedError):
    """A non-secret setting is malformed. The message names the variable, and so does
    `variable` (the one at fault, where a rule spans two)."""


def origin_host_forms(origin: str) -> list[str]:
    """The Host header values an absolute origin can arrive as.

    `https://h` arrives as `h` or `h:443`, and so does `https://h:443`; any
    other port arrives only with that port. This is the rule `auth` has always
    used to build its Host allow-list, moved here so a derived bearer list is
    built by the same rule.
    """
    parts = urlsplit(origin)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    forms = [netloc]
    host_only, _, port = netloc.rpartition(":")
    if host_only and port.isdigit():
        if (scheme == "https" and port == "443") or (scheme == "http" and port == "80"):
            forms.append(host_only)
    else:
        forms.append("%s:%s" % (netloc, "443" if scheme == "https" else "80"))
    return forms


def _strip_port(host: str) -> str:
    if host.startswith("["):  # [v6]:port
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    name, sep, port = host.rpartition(":")
    return name if sep and port.isdigit() else host


def _csv_hosts(raw: str) -> frozenset[str]:
    return frozenset(chunk.strip().lower() for chunk in raw.split(",") if chunk.strip())


@dataclass(frozen=True)
class Settings:
    """Atlas's non-secret configuration. See the block above."""

    #: `scheme://host[:port]`, normalised; None when ATLAS_PUBLIC_URL is unset.
    public_url: Optional[str]
    #: The raw comma-separated origin list `auth` parses; "" when neither
    #: ATLAS_LOGIN_ORIGINS nor ATLAS_PUBLIC_URL is set (auth refuses to boot).
    login_origins: str
    #: The variable `login_origins` came from, for error messages; "" if unset.
    login_origins_source: str
    #: Hosts on which Bearer is honoured; empty = every allowed host (today).
    bearer_hosts: frozenset[str]
    #: Hosts admitted for Bearer only, past the login-origin Host check.
    internal_hosts: frozenset[str]
    privacy_contact: str
    #: The privacy page's "Where it lives" paragraph, plain text.
    privacy_hosting: str
    product_name: str
    #: ATLAS_DB_URL exactly as given (not stripped), or None.
    db_url: Optional[str]
    #: ATLAS_SEED (STORY-oss-07): one of SEED_PROFILES; unset = DEFAULT_SEED_PROFILE
    #: ("homelab", today's seed, in this repo's distribution.py).
    seed: str = DEFAULT_SEED_PROFILE
    #: ATLAS_LEGACY_V0 (STORY-oss-07): create the v0 Component/Gate/Requirement/Decision
    #: tables and serve /api/tree, /api/summary, /api/priority, /api/goals. Unset =
    #: DEFAULT_LEGACY_V0 (True, today, in this repo's distribution.py).
    legacy_v0: bool = DEFAULT_LEGACY_V0
    #: ATLAS_ACCEPTANCE_AGENT_WRITE: one of ACCEPTANCE_AGENT_WRITE_MODES; unset = "off".
    acceptance_agent_write: str = DEFAULT_ACCEPTANCE_AGENT_WRITE

    def is_internal_host(self, host: str) -> bool:
        """Is `host`, a Host header value, one of ATLAS_INTERNAL_HOSTS?"""
        host = (host or "").strip().lower()
        if not host or not self.internal_hosts:
            return False
        return host in self.internal_hosts or _strip_port(host) in self.internal_hosts


def _public_url(raw: str) -> Optional[str]:
    raw = raw.strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if (
        parts.scheme.lower() not in ("http", "https")
        or not parts.netloc
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise SettingsError(
            "%s=%r is not an absolute origin like https://atlas.example.com[:port] "
            "(no path, query or fragment)." % (PUBLIC_URL_ENV, raw), variable=PUBLIC_URL_ENV
        )
    return "%s://%s" % (parts.scheme.lower(), parts.netloc.lower())


def load(environ: Optional[Mapping[str, str]] = None) -> Settings:
    """Read the settings from `environ` (default: `os.environ`).

    Reads nothing but the mapping, opens no file, logs nothing. Raises
    `SettingsError` only for a malformed NEW variable (ATLAS_PUBLIC_URL,
    ATLAS_INTERNAL_HOSTS, ATLAS_SEED, ATLAS_LEGACY_V0, ATLAS_ACCEPTANCE_AGENT_WRITE); the pre-existing ones are still validated where they
    always were, so their error messages are unchanged.
    """
    env = os.environ if environ is None else environ

    public_url = _public_url(env.get(PUBLIC_URL_ENV, ""))

    origins = env.get(LOGIN_ORIGINS_ENV, "").strip()
    origins_source = LOGIN_ORIGINS_ENV if origins else ""
    if not origins and public_url:
        origins, origins_source = public_url, PUBLIC_URL_ENV

    bearer = _csv_hosts(env.get(BEARER_HOSTS_ENV, ""))
    if not bearer and public_url:
        bearer = frozenset(origin_host_forms(public_url))

    internal = _csv_hosts(env.get(INTERNAL_HOSTS_ENV, ""))
    for entry in sorted(internal):
        if any(c in entry for c in "/*@ "):
            raise SettingsError(
                "%s entry %r is not a host[:port] (e.g. atlas-api or atlas-api:8000)."
                % (INTERNAL_HOSTS_ENV, entry), variable=INTERNAL_HOSTS_ENV
            )

    seed = env.get(SEED_ENV, "").strip().lower() or DEFAULT_SEED_PROFILE
    if seed not in SEED_PROFILES:
        raise SettingsError(
            "%s=%r is not one of %s." % (SEED_ENV, env.get(SEED_ENV), ", ".join(SEED_PROFILES)), variable=SEED_ENV
        )
    legacy_raw = env.get(LEGACY_V0_ENV, "").strip().lower()
    if legacy_raw == "":
        legacy_v0 = DEFAULT_LEGACY_V0  # unset: the distribution's default (True here, today)
    elif legacy_raw in _TRUE:
        legacy_v0 = True
    elif legacy_raw in _FALSE:
        legacy_v0 = False
    else:
        raise SettingsError("%s=%r is not 1 or 0." % (LEGACY_V0_ENV, env.get(LEGACY_V0_ENV)), variable=LEGACY_V0_ENV)
    if seed == "homelab" and not legacy_v0:
        # The homelab seed IS v0 data (seed.py writes only Component/Gate/...), so it
        # cannot run without the v0 tables. Refuse rather than boot with a seed skipped.
        # "(the default)" only where it is: in this repo the message is today's, byte for
        # byte, and under another distribution.py it does not misname the default.
        raise SettingsError(
            "%s=0 needs %s=demo or %s=none (or self): the %s seed%s writes the v0 "
            "tables that %s=0 does not create."
            % (LEGACY_V0_ENV, SEED_ENV, SEED_ENV, "homelab",
               " (the default)" if DEFAULT_SEED_PROFILE == "homelab" else "", LEGACY_V0_ENV),
            variable=LEGACY_V0_ENV
        )

    acceptance_raw = env.get(ACCEPTANCE_AGENT_WRITE_ENV, "")
    acceptance_agent_write = acceptance_raw.strip().lower() or DEFAULT_ACCEPTANCE_AGENT_WRITE
    if acceptance_agent_write not in ACCEPTANCE_AGENT_WRITE_MODES:
        raise SettingsError(
            "%s=%r is not one of %s." % (ACCEPTANCE_AGENT_WRITE_ENV, acceptance_raw,
                                         ", ".join(ACCEPTANCE_AGENT_WRITE_MODES)), variable=ACCEPTANCE_AGENT_WRITE_ENV
        )

    return Settings(
        public_url=public_url,
        login_origins=origins,
        login_origins_source=origins_source,
        bearer_hosts=bearer,
        internal_hosts=internal,
        privacy_contact=env.get(PRIVACY_CONTACT_ENV, "").strip() or DEFAULT_PRIVACY_CONTACT,
        privacy_hosting=env.get(PRIVACY_HOSTING_ENV, "").strip() or DEFAULT_PRIVACY_HOSTING,
        product_name=env.get(PRODUCT_NAME_ENV, "").strip() or DEFAULT_PRODUCT_NAME,
        db_url=env.get(DB_URL_ENV),
        seed=seed,
        legacy_v0=legacy_v0,
        acceptance_agent_write=acceptance_agent_write,
    )


# --------------------------------------------------------------------------- #
# Project RBAC (STORY-oss-11-project-tenancy, ADR 0041 §0)
# --------------------------------------------------------------------------- #
#
# ADDITIVE, OPT-IN, and one appended block on purpose: a sibling story appends
# its own block to this file, and keeping each story's settings in one place
# keeps those edits apart. `RbacSettings` is therefore a second typed, frozen
# object beside `Settings` rather than four more fields on it; `load_rbac()`
# keeps `load()`'s contract (reads only the mapping, opens no file, logs
# nothing, raises `SettingsError` naming the variable).
#
# The defaults ARE today (ADR 0041 §0): with none of these set, `rbac.enabled()`
# is false and `deps._visible_node_ids` never reaches the RBAC branch, so the
# new tables are never read. `backend/verify_rbac_off_unchanged.py` proves it.
#
#   ATLAS_RBAC                                   off | projects           (off)
#   ATLAS_DEFAULT_PROJECT_VISIBILITY             shared | private         (shared)
#   ATLAS_SHARED_PROJECT_ROLE                    viewer|editor|maintainer (editor)
#   ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER  1 | 0                    (1)
#
# The last three mean nothing while ATLAS_RBAC=off and are validated anyway, so
# a typo is found the day it is written and not the day RBAC is switched on.
# `owner` is not an accepted shared role: giving every account the right to
# grant `owner` on every shared project is not a setting anyone means to make.

RBAC_ENV = "ATLAS_RBAC"
DEFAULT_PROJECT_VISIBILITY_ENV = "ATLAS_DEFAULT_PROJECT_VISIBILITY"
SHARED_PROJECT_ROLE_ENV = "ATLAS_SHARED_PROJECT_ROLE"
INSTANCE_ADMIN_IMPLICIT_OWNER_ENV = "ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER"

RBAC_MODES = ("off", "projects")
PROJECT_VISIBILITIES = ("shared", "private")
SHARED_PROJECT_ROLES = ("viewer", "editor", "maintainer")


@dataclass(frozen=True)
class RbacSettings:
    """The project-RBAC switch and its three knobs. Defaults are today."""

    mode: str = "off"
    default_project_visibility: str = "shared"
    shared_project_role: str = "editor"
    instance_admin_implicit_project_owner: bool = True

    @property
    def enabled(self) -> bool:
        return self.mode == "projects"


def _choice(env: Mapping[str, str], name: str, allowed: tuple, default: str) -> str:
    raw = env.get(name, "")
    value = raw.strip().lower() or default
    if value not in allowed:
        raise SettingsError("%s=%r is not one of %s." % (name, raw, ", ".join(allowed)), variable=name)
    return value


def load_rbac(environ: Optional[Mapping[str, str]] = None) -> RbacSettings:
    """Read the project-RBAC settings from `environ` (default: `os.environ`)."""
    env = os.environ if environ is None else environ
    implicit_raw = env.get(INSTANCE_ADMIN_IMPLICIT_OWNER_ENV, "").strip().lower()
    if implicit_raw in ("",) + _TRUE:
        implicit = True
    elif implicit_raw in _FALSE:
        implicit = False
    else:
        raise SettingsError("%s=%r is not 1 or 0." % (
            INSTANCE_ADMIN_IMPLICIT_OWNER_ENV, env.get(INSTANCE_ADMIN_IMPLICIT_OWNER_ENV)), variable=INSTANCE_ADMIN_IMPLICIT_OWNER_ENV)
    return RbacSettings(
        mode=_choice(env, RBAC_ENV, RBAC_MODES, "off"),
        default_project_visibility=_choice(
            env, DEFAULT_PROJECT_VISIBILITY_ENV, PROJECT_VISIBILITIES, "shared"),
        shared_project_role=_choice(env, SHARED_PROJECT_ROLE_ENV, SHARED_PROJECT_ROLES, "editor"),
        instance_admin_implicit_project_owner=implicit,
    )


# --------------------------------------------------------------------------- #
# Feature flags (STORY-oss-08-plugins)
# --------------------------------------------------------------------------- #
#
# ADDITIVE, OPT-IN, like everything above. The personal/homelab features stay
# exactly where they are; each one only gains an off switch. With
# `ATLAS_DISABLED_FEATURES` unset or empty (the default) every feature is on and
# the app is byte for byte what it was.
#
#   ATLAS_DISABLED_FEATURES=github_link,save_work,deploy_status,deploy_warning,attention_probes
#
# The names, and where each one is guarded (one `if` at its existing site):
#
#   * github_link      -> main.py: the /api/me/github* and /api/access/github/*
#                         routes are not registered (a 404, like any unknown path).
#   * save_work        -> mcp/app/mcp_server.py: `atlas_save_work` and the
#                         `signoff` prompt are not registered. Nothing in the API.
#   * deploy_status    -> routers/health.py: `/api/health` carries no `deploy`
#                         field; in atlas-mcp, its own image check is skipped.
#   * deploy_warning   -> mcp/app/envelope.py: no `deploy_warning` on responses.
#   * attention_probes -> reserved. No probe runner exists yet (`probe` and
#                         `probe_result` are empty tables, attention reads a
#                         static hint), so there is nothing to guard today; the
#                         name is accepted so an install can switch it off now and
#                         the runner (STORY-atlas-node-types-probes) must honour it.
#
# An unknown name raises `SettingsError` naming it: main.py evaluates the flags
# at import, so a typo stops the boot instead of leaving a feature on in
# silence. atlas-mcp cannot import this module (its image holds only mcp/), so
# `mcp/app/settings.py` carries the same list; mcp/verify_feature_flags.py fails
# if the two drift.
#
# A separate typed object rather than a field on `Settings`, so this block stays
# self-contained beside other stories appending their own settings.

DISABLED_FEATURES_ENV = "ATLAS_DISABLED_FEATURES"

#: Every feature that can be switched off. Default: none is.
FEATURES = ("github_link", "save_work", "deploy_status", "deploy_warning", "attention_probes")


@dataclass(frozen=True)
class Features:
    """`ATLAS_DISABLED_FEATURES`, parsed. See the block above."""

    disabled: frozenset[str] = frozenset()

    def enabled(self, name: str) -> bool:
        if name not in FEATURES:
            # A typo in CODE, not in configuration: fail where it was written.
            raise KeyError("%r is not a feature (one of %s)." % (name, ", ".join(FEATURES)))
        return name not in self.disabled


def load_features(environ: Optional[Mapping[str, str]] = None) -> Features:
    """Read `ATLAS_DISABLED_FEATURES` from `environ` (default `os.environ`).

    Comma-separated, whitespace and case ignored, empty entries skipped. An
    unknown name raises `SettingsError` naming it and listing the valid ones.
    """
    env = os.environ if environ is None else environ
    raw = env.get(DISABLED_FEATURES_ENV, "")
    names = frozenset(chunk.strip().lower() for chunk in raw.split(",") if chunk.strip())
    unknown = sorted(names - set(FEATURES))
    if unknown:
        raise SettingsError(
            "%s=%r names unknown feature(s) %s; the features are %s."
            % (DISABLED_FEATURES_ENV, raw, ", ".join(unknown), ", ".join(FEATURES)), variable=DISABLED_FEATURES_ENV
        )
    return Features(disabled=names)


def feature_enabled(name: str, environ: Optional[Mapping[str, str]] = None) -> bool:
    """Is feature `name` on? True for every feature unless ATLAS_DISABLED_FEATURES names it."""
    return load_features(environ).enabled(name)


# --------------------------------------------------------------------------- #
# Deploy hint commands (TASK-1356)
# --------------------------------------------------------------------------- #
#
# ADDITIVE, OPT-IN. `deploy_status.py` tells an agent how to deploy a stale
# component: the REBUILD and PULL_AND_REBUILD commands in /api/health
# `deploy.warnings` and in atlas-mcp's `deploy_warning`. Those were literal
# homelab strings (`ssh <the homelab host> 'cd ~/project-atlas && ...'`), so another
# install was told to ssh to a host it does not have. With none of the
# variables below set (the default) every command and every sentence is byte
# for byte what it was.
#
#   ATLAS_DEPLOY_HINT_HOST=deploy-box        # the host the sentences and the
#                                            # DEFAULT commands name
#   ATLAS_DEPLOY_HINT_REBUILD_API=...        # each command, verbatim: replaces
#   ATLAS_DEPLOY_HINT_REBUILD_MCP=...        # the default whole (no templating,
#   ATLAS_DEPLOY_HINT_PULL_REBUILD_API=...   # so braces and quotes are safe)
#   ATLAS_DEPLOY_HINT_PULL_REBUILD_MCP=...
#
# Two layers, so the common case is one variable: an install with the same
# checkout layout on another host sets only ATLAS_DEPLOY_HINT_HOST and every
# default command follows it; an install that deploys differently (no ssh, a
# different path, Kubernetes) sets the commands it needs. A set-but-blank value
# raises `SettingsError` naming it, as everywhere in this module.
#
# Read where they are used: `deploy_status.py` loads them at import (both
# copies; the file must stay byte-identical, so it imports whichever `settings`
# its package has), and routers/health.py loads them at import too, so a blank
# value stops the boot instead of 500ing /api/health. atlas-mcp cannot import
# this module, so `mcp/app/settings.py` repeats it; backend/verify_deploy_status.py
# fails if the two drift. atlas-api has no `env_file:`, so an install passes the
# variables through its own compose override's `environment:`; this repo's
# compose files pass none of them, so the homelab sees the defaults.
#
# The defaults themselves (the host and the four commands) are estate values, so
# they come from `distribution.py` (STORY-oss-23 A1), unchanged; the neutral copy
# the export overlays names atlas.example.test and has no default command, so an
# unset variable answers NO_DEPLOY_HINT, naming itself.
#
# Self-contained, like the feature-flag block above.

DEPLOY_HINT_HOST_ENV = "ATLAS_DEPLOY_HINT_HOST"
DEFAULT_DEPLOY_HINT_HOST = distribution.DEFAULT_DEPLOY_HINT_HOST

#: (table, component, variable). `table` is the deploy_status.py name, lowercased.
DEPLOY_HINT_ENVS = (
    ("rebuild", "atlas-api", "ATLAS_DEPLOY_HINT_REBUILD_API"),
    ("rebuild", "atlas-mcp", "ATLAS_DEPLOY_HINT_REBUILD_MCP"),
    ("pull_and_rebuild", "atlas-api", "ATLAS_DEPLOY_HINT_PULL_REBUILD_API"),
    ("pull_and_rebuild", "atlas-mcp", "ATLAS_DEPLOY_HINT_PULL_REBUILD_MCP"),
)

#: The homelab commands, `{host}` filled from ATLAS_DEPLOY_HINT_HOST, from `distribution.py`.
#: The atlas-mcp ones carry the TLS overlay (OSS-24); verify_deploy_status.py section G pins
#: them against tools/check-deployed.sh.
DEFAULT_DEPLOY_HINTS = distribution.DEFAULT_DEPLOY_HINTS

#: The answer for a hint with neither a value nor a default. Never reached with this repo's
#: distribution.py, which has a default for every variable; the neutral copy has none, so an
#: unset variable names itself instead of raising. Mirrors mcp/app/settings.py NO_DEPLOY_HINT.
NO_DEPLOY_HINT = "(no command configured: set {name})"


@dataclass(frozen=True)
class DeployHints:
    """The deploy hint commands, resolved. See the block above."""

    host: str
    rebuild: Mapping[str, str]            # component -> command
    pull_and_rebuild: Mapping[str, str]   # component -> command


def _deploy_hint_value(env: Mapping[str, str], name: str) -> Optional[str]:
    raw = env.get(name)
    if raw is None:
        return None
    if not raw.strip():
        raise SettingsError("%s is set but empty. Give it a value, or unset it to use the default." % name, variable=name)
    return raw.strip()


def load_deploy_hints(environ: Optional[Mapping[str, str]] = None) -> DeployHints:
    """Read the ATLAS_DEPLOY_HINT_* variables from `environ` (default `os.environ`)."""
    env = os.environ if environ is None else environ
    host = _deploy_hint_value(env, DEPLOY_HINT_HOST_ENV) or DEFAULT_DEPLOY_HINT_HOST
    tables: dict = {"rebuild": {}, "pull_and_rebuild": {}}
    for table, component, name in DEPLOY_HINT_ENVS:
        value = _deploy_hint_value(env, name)
        if value is None:
            template = DEFAULT_DEPLOY_HINTS.get(name)
            value = NO_DEPLOY_HINT.format(name=name) if template is None else template.format(host=host)
        tables[table][component] = value
    return DeployHints(host=host, rebuild=tables["rebuild"], pull_and_rebuild=tables["pull_and_rebuild"])


# --------------------------------------------------------------------------- #
# Identity providers and OIDC login (STORY-oss-13-identity-seam, ADR 0041 §5-§7)
# --------------------------------------------------------------------------- #
#
# ADDITIVE, OPT-IN, one appended block like the ones above. With none of these
# set the answer is today's: `local` is the only provider, every local account
# may sign in with its password, no OIDC route is registered and nothing here
# opens a file. `backend/verify_identity_seam.py` proves it.
#
#   ATLAS_AUTH_PROVIDERS          local | oidc | local,oidc              (local)
#   ATLAS_LOCAL_LOGIN             all | break-glass             (all; see below)
#   ATLAS_OIDC_ISSUER             https issuer URL; discovery is read from it
#   ATLAS_OIDC_CLIENT_ID          the client id of Atlas's app registration
#   ATLAS_OIDC_CLIENT_SECRET_FILE the client secret, FROM A FILE ONLY
#   ATLAS_OIDC_CLIENT_KEY_FILE    a PEM private key for private_key_jwt, FROM A FILE ONLY
#   ATLAS_OIDC_CLIENT_KEY_ID      that key's `kid` (with the entra preset it is sent as
#                                 `x5t`: the certificate's base64url SHA-1 thumbprint)
#   ATLAS_OIDC_TOKEN_AUTH         client_secret_basic | client_secret_post | private_key_jwt
#   ATLAS_OIDC_REDIRECT_URI       https://<host>/auth/oidc/callback (default: derived
#                                 from ATLAS_PUBLIC_URL)
#   ATLAS_OIDC_SCOPES             space-separated, must include openid
#                                 ("openid profile email")
#   ATLAS_OIDC_PRESET             generic | entra | keycloak             (generic)
#   ATLAS_OIDC_NAME_CLAIM / ATLAS_OIDC_EMAIL_CLAIM / ATLAS_OIDC_GROUPS_CLAIM
#                                 override one claim name of the preset
#   ATLAS_OIDC_GROUP_MAP_FILE     YAML/JSON allowlist: IdP group value -> Atlas group
#   ATLAS_OIDC_REQUIRED_GROUPS    comma-separated IdP group values; JIT provisioning
#                                 (and every OIDC login) needs at least one. Unset =
#                                 no JIT provisioning at all (linked accounts only).
#   ATLAS_OIDC_MAX_SESSION_HOURS  absolute lifetime of an OIDC session         (12)
#   ATLAS_OIDC_REVALIDATE_DAYS    an IdP-backed person's agent tokens stop working
#                                 this long after their last OIDC login        (30)
#   ATLAS_OIDC_STEP_UP            off | on: an IdP-backed person creates their own
#                                 agent credential after a FRESH IdP sign-in (an
#                                 OIDC step-up), not a password they do not have
#                                 (TASK-1415)                                  (off)
#   ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS
#                                 how old that sign-in (`auth_time`) may be when
#                                 the credential is minted, 60..900            (300)
#
# Rules, each raising `SettingsError` naming the variable (main imports auth,
# auth imports identity, identity calls this at import: a bad value stops the boot):
#
#   * set-but-blank is an error, everywhere in this block (TASK-1348's rule);
#   * an unknown provider name stops the boot (`known` lists the registered
#     ones; a verify registers a fake provider before importing the app);
#   * ATLAS_LOCAL_LOGIN unset means `all` when `local` is a provider and
#     `break-glass` when it is not; an explicit `all` without `local` is a
#     contradiction, and `break-glass` with no other provider would leave an
#     install with no way in but its break-glass accounts;
#   * the OIDC variables are validated whenever they are set, and the issuer,
#     client id, redirect URI and a credential are required once `oidc` is a provider;
#   * the plain ATLAS_OIDC_CLIENT_SECRET / ATLAS_OIDC_CLIENT_KEY are refused: only
#     their _FILE form is read;
#   * the entra preset needs a TENANT issuer (https://login.microsoftonline.com/
#     <tenant-id>/v2.0): the subject is the `tid`+`oid` pair, the issuer pins the
#     `tid`, and `common`/`organizations` would let another tenant's `oid` in.
#
# This function reads only the mapping and opens no file: the secret files and
# the group map are read by `identity` at import, and only when OIDC is enabled.

AUTH_PROVIDERS_ENV = "ATLAS_AUTH_PROVIDERS"
LOCAL_LOGIN_ENV = "ATLAS_LOCAL_LOGIN"
OIDC_ISSUER_ENV = "ATLAS_OIDC_ISSUER"
OIDC_CLIENT_ID_ENV = "ATLAS_OIDC_CLIENT_ID"
OIDC_CLIENT_SECRET_ENV = "ATLAS_OIDC_CLIENT_SECRET"
OIDC_CLIENT_KEY_ENV = "ATLAS_OIDC_CLIENT_KEY"
OIDC_CLIENT_KEY_ID_ENV = "ATLAS_OIDC_CLIENT_KEY_ID"
OIDC_TOKEN_AUTH_ENV = "ATLAS_OIDC_TOKEN_AUTH"
OIDC_REDIRECT_URI_ENV = "ATLAS_OIDC_REDIRECT_URI"
OIDC_SCOPES_ENV = "ATLAS_OIDC_SCOPES"
OIDC_PRESET_ENV = "ATLAS_OIDC_PRESET"
OIDC_NAME_CLAIM_ENV = "ATLAS_OIDC_NAME_CLAIM"
OIDC_EMAIL_CLAIM_ENV = "ATLAS_OIDC_EMAIL_CLAIM"
OIDC_GROUPS_CLAIM_ENV = "ATLAS_OIDC_GROUPS_CLAIM"
OIDC_GROUP_MAP_FILE_ENV = "ATLAS_OIDC_GROUP_MAP_FILE"
OIDC_REQUIRED_GROUPS_ENV = "ATLAS_OIDC_REQUIRED_GROUPS"
OIDC_MAX_SESSION_HOURS_ENV = "ATLAS_OIDC_MAX_SESSION_HOURS"
OIDC_REVALIDATE_DAYS_ENV = "ATLAS_OIDC_REVALIDATE_DAYS"
OIDC_STEP_UP_ENV = "ATLAS_OIDC_STEP_UP"
OIDC_STEP_UP_MAX_AGE_ENV = "ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS"

BUILTIN_AUTH_PROVIDERS = ("local", "oidc")
LOCAL_LOGIN_MODES = ("all", "break-glass")
OIDC_TOKEN_AUTH_METHODS = ("client_secret_basic", "client_secret_post", "private_key_jwt")
OIDC_PRESETS = ("generic", "entra", "keycloak")
DEFAULT_OIDC_SCOPES = "openid profile email"
DEFAULT_OIDC_MAX_SESSION_HOURS = 12
DEFAULT_OIDC_REVALIDATE_DAYS = 30
#: Five minutes from the IdP sign-in to the mint (TASK-1415). Long enough to come back
#: from the IdP, choose scopes and press the button; short enough that a sign-in is a
#: deliberate act for THIS credential, not one left over from earlier in the day. The
#: same order as the re-authentication windows the common IdPs use for sensitive
#: actions, and well inside the 10-minute life of a login flow (identity/flows.py).
DEFAULT_OIDC_STEP_UP_MAX_AGE = 300
OIDC_STEP_UP_MAX_AGE_BOUNDS = (60, 900)
OIDC_STEP_UP_MODES = ("off", "on")
#: 14 days: today's absolute ceiling for every session (`auth.SESSION_ABSOLUTE_SECONDS`).
MAX_OIDC_SESSION_HOURS = 14 * 24
OIDC_CALLBACK_PATH = "/auth/oidc/callback"


@dataclass(frozen=True)
class OidcSettings:
    """ATLAS_OIDC_*, parsed. Meaningful only while `oidc` is a provider."""

    issuer: Optional[str] = None
    client_id: Optional[str] = None
    token_auth: str = "client_secret_basic"
    client_key_id: Optional[str] = None
    redirect_uri: Optional[str] = None
    scopes: tuple = tuple(DEFAULT_OIDC_SCOPES.split())
    preset: str = "generic"
    name_claim: Optional[str] = None
    email_claim: Optional[str] = None
    groups_claim: Optional[str] = None
    group_map_file: Optional[str] = None
    required_groups: frozenset = frozenset()
    max_session_hours: int = DEFAULT_OIDC_MAX_SESSION_HOURS
    revalidate_days: int = DEFAULT_OIDC_REVALIDATE_DAYS
    #: ATLAS_OIDC_STEP_UP: self-service agent credentials for an IdP-backed person
    #: after a fresh IdP sign-in (TASK-1415). Off: today, a password re-entry for all.
    step_up: bool = False
    step_up_max_age: int = DEFAULT_OIDC_STEP_UP_MAX_AGE
    #: entra only: the tenant id the issuer pins (lowercase GUID).
    entra_tenant_id: Optional[str] = None


@dataclass(frozen=True)
class IdentitySettings:
    """Which identity providers are on, and how local password login behaves."""

    providers: tuple = ("local",)
    local_login: str = "all"
    oidc: OidcSettings = OidcSettings()

    @property
    def oidc_enabled(self) -> bool:
        return "oidc" in self.providers

    @property
    def break_glass_only(self) -> bool:
        return self.local_login == "break-glass"


def _ident_value(env: Mapping[str, str], name: str) -> Optional[str]:
    """None when unset; the stripped value when set; an error when set but blank."""
    raw = env.get(name)
    if raw is None:
        return None
    if not raw.strip():
        raise SettingsError("%s is set but empty. Give it a value, or unset it." % name, variable=name)
    return raw.strip()


def _ident_int(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = _ident_value(env, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SettingsError("%s=%r is not a whole number." % (name, raw), variable=name) from None
    if not low <= value <= high:
        raise SettingsError("%s=%r is outside %d..%d." % (name, raw, low, high), variable=name)
    return value


def _https_url(name: str, raw: str) -> str:
    parts = urlsplit(raw)
    if parts.scheme.lower() != "https" or not parts.netloc or parts.query or parts.fragment:
        raise SettingsError(
            "%s=%r is not an https URL without query or fragment (OIDC requires TLS, "
            "ADR 0041 §5)." % (name, raw), variable=name)
    return raw


_GUID_CHARS = frozenset("0123456789abcdef-")


def _entra_tenant(issuer: str) -> str:
    """The tenant GUID in an Entra v2 issuer, or a SettingsError."""
    segments = [seg for seg in urlsplit(issuer).path.split("/") if seg]
    tid = segments[0].lower() if segments else ""
    if len(segments) != 2 or segments[1] != "v2.0" or len(tid) != 36 or set(tid) - _GUID_CHARS:
        raise SettingsError(
            "%s=%r: the entra preset needs a tenant issuer of the form "
            "https://login.microsoftonline.com/<tenant-id>/v2.0 (a GUID, not common, "
            "organizations or a domain name): the tenant id is half of the subject and "
            "the issuer is what pins it." % (OIDC_ISSUER_ENV, issuer), variable=OIDC_ISSUER_ENV)
    return tid


def _claim_name(env: Mapping[str, str], name: str) -> Optional[str]:
    value = _ident_value(env, name)
    if value is not None and (len(value) > 128 or any(c.isspace() for c in value)):
        raise SettingsError("%s=%r is not a claim name." % (name, value), variable=name)
    return value


def load_identity(environ: Optional[Mapping[str, str]] = None, *,
                  known: tuple = BUILTIN_AUTH_PROVIDERS) -> IdentitySettings:
    """Read the identity settings from `environ` (default `os.environ`).

    `known` is the provider names that exist (identity's registry). Reads only
    the mapping; opens no file; logs nothing.
    """
    env = os.environ if environ is None else environ

    raw = _ident_value(env, AUTH_PROVIDERS_ENV)
    if raw is None:
        providers: tuple = ("local",)
    else:
        names = [chunk.strip().lower() for chunk in raw.split(",")]
        if any(not n for n in names):
            raise SettingsError("%s=%r has an empty entry." % (AUTH_PROVIDERS_ENV, raw), variable=AUTH_PROVIDERS_ENV)
        unknown = sorted(set(names) - set(known))
        if unknown:
            raise SettingsError(
                "%s=%r names unknown provider(s) %s; the providers are %s."
                % (AUTH_PROVIDERS_ENV, raw, ", ".join(unknown), ", ".join(known)), variable=AUTH_PROVIDERS_ENV)
        providers = tuple(dict.fromkeys(names))

    login_raw = _ident_value(env, LOCAL_LOGIN_ENV)
    if login_raw is None:
        local_login = "all" if "local" in providers else "break-glass"
    else:
        local_login = login_raw.lower()
        if local_login not in LOCAL_LOGIN_MODES:
            raise SettingsError("%s=%r is not one of %s."
                                % (LOCAL_LOGIN_ENV, login_raw, ", ".join(LOCAL_LOGIN_MODES)), variable=LOCAL_LOGIN_ENV)
        if local_login == "all" and "local" not in providers:
            raise SettingsError(
                "%s=all needs local in %s (it is %r). Set %s=break-glass, or add local."
                % (LOCAL_LOGIN_ENV, AUTH_PROVIDERS_ENV, ",".join(providers), LOCAL_LOGIN_ENV), variable=LOCAL_LOGIN_ENV)
    if local_login == "break-glass" and providers == ("local",):
        raise SettingsError(
            "%s=break-glass limits password login to break-glass accounts, and %s=local "
            "offers no other way in. Add another provider (oidc), or set %s=all."
            % (LOCAL_LOGIN_ENV, AUTH_PROVIDERS_ENV, LOCAL_LOGIN_ENV), variable=LOCAL_LOGIN_ENV)

    oidc = _load_oidc(env, required="oidc" in providers)
    return IdentitySettings(providers=providers, local_login=local_login, oidc=oidc)


def _load_oidc(env: Mapping[str, str], *, required: bool) -> OidcSettings:
    issuer = _ident_value(env, OIDC_ISSUER_ENV)
    if issuer is not None:
        issuer = _https_url(OIDC_ISSUER_ENV, issuer).rstrip("/")
    client_id = _ident_value(env, OIDC_CLIENT_ID_ENV)

    token_auth_raw = _ident_value(env, OIDC_TOKEN_AUTH_ENV)
    token_auth = (token_auth_raw or "client_secret_basic").lower()
    if token_auth not in OIDC_TOKEN_AUTH_METHODS:
        raise SettingsError("%s=%r is not one of %s." % (
            OIDC_TOKEN_AUTH_ENV, token_auth_raw, ", ".join(OIDC_TOKEN_AUTH_METHODS)), variable=OIDC_TOKEN_AUTH_ENV)
    # The plain secret variables are never honoured, only their _FILE form: a
    # client secret in an environment variable is in `docker inspect`.
    for plain in (OIDC_CLIENT_SECRET_ENV, OIDC_CLIENT_KEY_ENV):
        if env.get(plain) is not None:
            raise SettingsError(
                "%s is set as a plain variable; it is read only from a file. Set %s_FILE "
                "to the path of a file holding it, and unset %s." % (plain, plain, plain), variable=plain)
    secret_file = _ident_value(env, OIDC_CLIENT_SECRET_ENV + "_FILE")
    key_file = _ident_value(env, OIDC_CLIENT_KEY_ENV + "_FILE")
    key_id = _ident_value(env, OIDC_CLIENT_KEY_ID_ENV)

    redirect = _ident_value(env, OIDC_REDIRECT_URI_ENV)
    if redirect is not None:
        redirect = _https_url(OIDC_REDIRECT_URI_ENV, redirect)
        if urlsplit(redirect).path != OIDC_CALLBACK_PATH:
            raise SettingsError("%s=%r must end in %s, the route Atlas serves."
                                % (OIDC_REDIRECT_URI_ENV, redirect, OIDC_CALLBACK_PATH), variable=OIDC_REDIRECT_URI_ENV)
    else:
        public = env.get(PUBLIC_URL_ENV, "").strip()
        if public:
            public_url = _public_url(public)
            if public_url and public_url.startswith("https://"):
                redirect = public_url + OIDC_CALLBACK_PATH

    scopes_raw = _ident_value(env, OIDC_SCOPES_ENV)
    scopes = tuple(dict.fromkeys((scopes_raw or DEFAULT_OIDC_SCOPES).split()))
    if "openid" not in scopes:
        raise SettingsError("%s=%r must include openid." % (OIDC_SCOPES_ENV, scopes_raw), variable=OIDC_SCOPES_ENV)

    preset_raw = _ident_value(env, OIDC_PRESET_ENV)
    preset = (preset_raw or "generic").lower()
    if preset not in OIDC_PRESETS:
        raise SettingsError("%s=%r is not one of %s." % (
            OIDC_PRESET_ENV, preset_raw, ", ".join(OIDC_PRESETS)), variable=OIDC_PRESET_ENV)
    tenant = _entra_tenant(issuer) if (preset == "entra" and issuer) else None

    group_map_file = _ident_value(env, OIDC_GROUP_MAP_FILE_ENV)
    required_raw = _ident_value(env, OIDC_REQUIRED_GROUPS_ENV)
    required_groups = frozenset(
        chunk.strip() for chunk in (required_raw or "").split(",") if chunk.strip())
    if required_raw is not None and not required_groups:
        raise SettingsError("%s=%r names no group." % (OIDC_REQUIRED_GROUPS_ENV, required_raw), variable=OIDC_REQUIRED_GROUPS_ENV)

    step_up_raw = _ident_value(env, OIDC_STEP_UP_ENV)
    if step_up_raw is not None and step_up_raw.lower() not in OIDC_STEP_UP_MODES:
        raise SettingsError("%s=%r is not one of %s." % (
            OIDC_STEP_UP_ENV, step_up_raw, ", ".join(OIDC_STEP_UP_MODES)), variable=OIDC_STEP_UP_ENV)
    step_up = (step_up_raw or "off").lower() == "on"

    oidc = OidcSettings(
        issuer=issuer, client_id=client_id, token_auth=token_auth, client_key_id=key_id,
        redirect_uri=redirect, scopes=scopes, preset=preset,
        name_claim=_claim_name(env, OIDC_NAME_CLAIM_ENV),
        email_claim=_claim_name(env, OIDC_EMAIL_CLAIM_ENV),
        groups_claim=_claim_name(env, OIDC_GROUPS_CLAIM_ENV),
        group_map_file=group_map_file, required_groups=required_groups,
        max_session_hours=_ident_int(env, OIDC_MAX_SESSION_HOURS_ENV,
                                     DEFAULT_OIDC_MAX_SESSION_HOURS, 1, MAX_OIDC_SESSION_HOURS),
        revalidate_days=_ident_int(env, OIDC_REVALIDATE_DAYS_ENV,
                                   DEFAULT_OIDC_REVALIDATE_DAYS, 1, 3650),
        step_up=step_up,
        step_up_max_age=_ident_int(env, OIDC_STEP_UP_MAX_AGE_ENV, DEFAULT_OIDC_STEP_UP_MAX_AGE,
                                   *OIDC_STEP_UP_MAX_AGE_BOUNDS),
        entra_tenant_id=tenant,
    )
    if step_up and not required:
        # On with no OIDC provider would be a setting that silently does nothing:
        # every person is local and keeps the password prompt.
        raise SettingsError("%s=on needs oidc in %s." % (OIDC_STEP_UP_ENV, AUTH_PROVIDERS_ENV), variable=OIDC_STEP_UP_ENV)
    if not required:
        return oidc
    if issuer is None:
        raise SettingsError("%s is required when %s includes oidc." % (
            OIDC_ISSUER_ENV, AUTH_PROVIDERS_ENV), variable=OIDC_ISSUER_ENV)
    if client_id is None:
        raise SettingsError("%s is required when %s includes oidc." % (
            OIDC_CLIENT_ID_ENV, AUTH_PROVIDERS_ENV), variable=OIDC_CLIENT_ID_ENV)
    if redirect is None:
        raise SettingsError(
            "%s is required when %s includes oidc and %s is not an https URL."
            % (OIDC_REDIRECT_URI_ENV, AUTH_PROVIDERS_ENV, PUBLIC_URL_ENV), variable=OIDC_REDIRECT_URI_ENV)
    if token_auth == "private_key_jwt":
        if key_file is None:
            raise SettingsError("%s=private_key_jwt needs %s_FILE." % (
                OIDC_TOKEN_AUTH_ENV, OIDC_CLIENT_KEY_ENV), variable=OIDC_TOKEN_AUTH_ENV)
    elif secret_file is None:
        raise SettingsError("%s=%s needs %s_FILE (the client secret, in a file)." % (
            OIDC_TOKEN_AUTH_ENV, token_auth, OIDC_CLIENT_SECRET_ENV), variable=OIDC_TOKEN_AUTH_ENV)
    return oidc


# --------------------------------------------------------------------------- #
# Strict API (STORY-oss-14-strict-api)
# --------------------------------------------------------------------------- #
#
# ADDITIVE, OPT-IN. `POST`/`PATCH /api/node` and `/api/workitem` used to drop a
# field they do not read in silence: Pydantic ignores an undeclared key, so a
# caller that misspells `summray`, or sends `priority` to `/api/node`, gets a
# 200 and believes the write took. With `ATLAS_STRICT_API` unset (the default)
# every payload accepted before is accepted unchanged, and the response gains a
# `warnings` list naming each field the route ignored (only when there is one,
# so a clean request answers byte for byte what it did). Set to 1, the same
# request is a 422 naming the field, and nothing is written.
#
#   ATLAS_STRICT_API=1        # 1/true/yes/on = strict; 0/false/no/off = today
#
# A set-but-blank value raises `SettingsError` naming it, as everywhere in this
# module, and so does any other word. main.py loads it at import, so a typo
# stops the boot; the routes read it per request through
# `payload_fields.strict_api()`, the way `ATLAS_ACCEPTANCE_AGENT_WRITE` is read.
# The OSS install sets it through its `env_file:`; this repo's homelab compose
# files pass nothing, so the homelab keeps today's behaviour.
#
# Self-contained, like the blocks above.

STRICT_API_ENV = "ATLAS_STRICT_API"


def load_strict_api(environ: Optional[Mapping[str, str]] = None) -> bool:
    """`ATLAS_STRICT_API`, parsed: False when unset (today), True for 1/true/yes/on.

    Set but blank, or any other value, raises `SettingsError` naming the variable."""
    env = os.environ if environ is None else environ
    raw = env.get(STRICT_API_ENV)
    if raw is None:
        return False
    value = raw.strip().lower()
    if not value:
        raise SettingsError("%s is set but empty. Give it a value (1 or 0), or unset it."
                            % STRICT_API_ENV, variable=STRICT_API_ENV)
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise SettingsError("%s=%r is not one of 1, 0 (or true/false, yes/no, on/off)."
                        % (STRICT_API_ENV, raw), variable=STRICT_API_ENV)


# --------------------------------------------------------------------------- #
# Read audit and audit export (STORY-oss-16-audit-export)
# --------------------------------------------------------------------------- #
#
# ADDITIVE, OPT-IN. Mutations were already audited (`graph_event`, `auth_event`);
# agent READS were not. With neither variable set nothing changes: no
# `audit_read` row is written and nothing is ever purged.
#
#   ATLAS_AUDIT_READS=1             # 1/true/yes/on = every agent (service-token)
#                                   # read of a node, a search or a document writes
#                                   # an `audit_read` row; 0/false/no/off or unset
#                                   # = today
#   ATLAS_AUDIT_RETENTION_DAYS=365  # unset = keep forever (today). When set,
#                                   # `atlas-admin audit purge` (run by cron)
#                                   # deletes audit_read rows older than this, and
#                                   # ONLY audit_read rows: graph_event and
#                                   # auth_event are the system's history.
#
# A set-but-blank value raises `SettingsError` naming it, as everywhere in this
# module, and so does any other word, or a retention outside 1..36500. main.py
# loads it at import (through `audit_log`), so a typo stops the boot. The OSS
# install sets both through its `env_file:`; this repo's homelab compose files
# pass neither, so the homelab keeps today's behaviour.
#
# Self-contained, like the blocks above.

AUDIT_READS_ENV = "ATLAS_AUDIT_READS"
AUDIT_RETENTION_DAYS_ENV = "ATLAS_AUDIT_RETENTION_DAYS"
AUDIT_RETENTION_MAX_DAYS = 36500


@dataclass(frozen=True)
class AuditSettings:
    """The read-audit knobs. The defaults ARE today: off, keep forever."""
    reads: bool = False
    retention_days: Optional[int] = None


def load_audit(environ: Optional[Mapping[str, str]] = None) -> AuditSettings:
    """`ATLAS_AUDIT_READS` and `ATLAS_AUDIT_RETENTION_DAYS`, parsed and validated.

    Set but blank, an unknown word, or a retention that is not a whole number of
    days in 1..36500 raises `SettingsError` naming the variable."""
    env = os.environ if environ is None else environ

    reads = False
    raw = env.get(AUDIT_READS_ENV)
    if raw is not None:
        value = raw.strip().lower()
        if not value:
            raise SettingsError("%s is set but empty. Give it a value (1 or 0), or unset it."
                                % AUDIT_READS_ENV, variable=AUDIT_READS_ENV)
        if value in _TRUE:
            reads = True
        elif value not in _FALSE:
            raise SettingsError("%s=%r is not one of 1, 0 (or true/false, yes/no, on/off)."
                                % (AUDIT_READS_ENV, raw), variable=AUDIT_READS_ENV)

    retention: Optional[int] = None
    raw = env.get(AUDIT_RETENTION_DAYS_ENV)
    if raw is not None:
        value = raw.strip()
        if not value:
            raise SettingsError("%s is set but empty. Give it a number of days, or unset it "
                                "to keep audit_read rows forever." % AUDIT_RETENTION_DAYS_ENV, variable=AUDIT_RETENTION_DAYS_ENV)
        try:
            retention = int(value)
        except ValueError:
            raise SettingsError("%s=%r is not a whole number of days."
                                % (AUDIT_RETENTION_DAYS_ENV, raw), variable=AUDIT_RETENTION_DAYS_ENV) from None
        if not 1 <= retention <= AUDIT_RETENTION_MAX_DAYS:
            raise SettingsError("%s=%r must be between 1 and %d days."
                                % (AUDIT_RETENTION_DAYS_ENV, raw, AUDIT_RETENTION_MAX_DAYS), variable=AUDIT_RETENTION_DAYS_ENV)
    return AuditSettings(reads=reads, retention_days=retention)


# --------------------------------------------------------------------------- #
# The public handbook (STORY-atlas-system-node-handbook)
# --------------------------------------------------------------------------- #
#
#   ATLAS_PUBLIC_DOCS   1 | 0   (0)
#
# 1 serves the static handbook at /docs to anyone, without sign-in: `auth.PUBLIC_ROUTES` gains
# exactly `system_handbook.public_paths()`. Unset or 0 is today's surface: /docs exists but is
# behind the gate like every other route. Off by default because it widens the unauthenticated
# surface of an install that never asked for it (the homelab); the OSS .env.example turns it on,
# because an open-source install's handbook is the same text as its public repository.
# Appended as its own block, like the RBAC and audit blocks, so it never edits another story's.

PUBLIC_DOCS_ENV = "ATLAS_PUBLIC_DOCS"


def load_public_docs(environ: Optional[Mapping[str, str]] = None) -> bool:
    """`ATLAS_PUBLIC_DOCS`, parsed: False when unset or empty (today), True for 1/true/yes/on,
    False for 0/false/no/off. Any other value raises `SettingsError` naming the variable."""
    env = os.environ if environ is None else environ
    raw = env.get(PUBLIC_DOCS_ENV, "")
    value = raw.strip().lower()
    if value in ("",) + _FALSE:
        return False
    if value in _TRUE:
        return True
    raise SettingsError("%s=%r is not 1 or 0." % (PUBLIC_DOCS_ENV, raw), variable=PUBLIC_DOCS_ENV)


# --------------------------------------------------------------------------- #
# Which public landing page `/` serves (TASK-1463, ADR 0035 as amended by ADR 0049)
# --------------------------------------------------------------------------- #
#
#   ATLAS_LANDING   v1 | v2   (v1)
#
# v1, or unset, is today's page: `frontend/landing.html`, byte for byte, with today's headers,
# and nothing else becomes public. v2 is the product website (`frontend/landing-v2/`): `/` then
# serves its `index.html` with the strict CSP of ADR 0049, and `auth.PUBLIC_ROUTES` gains exactly
# `landing_site.public_paths()` (its stylesheet, its script and its fixed list of images, never a
# prefix). Off by default on every install until the page is published; appended as its own
# block, like the public-docs block above, so it edits no other story's.

LANDING_ENV = "ATLAS_LANDING"
LANDING_VERSIONS = ("v1", "v2")
DEFAULT_LANDING = "v1"


def load_landing(environ: Optional[Mapping[str, str]] = None) -> str:
    """`ATLAS_LANDING`, parsed: "v1" when unset or empty (today), else one of LANDING_VERSIONS
    (case and surrounding space ignored). Any other value raises `SettingsError` naming it."""
    env = os.environ if environ is None else environ
    raw = env.get(LANDING_ENV, "")
    value = raw.strip().lower() or DEFAULT_LANDING
    if value in LANDING_VERSIONS:
        return value
    raise SettingsError("%s=%r is not one of %s." % (LANDING_ENV, raw, ", ".join(LANDING_VERSIONS)),
                        variable=LANDING_ENV)


# --------------------------------------------------------------------------- #
# IdP access tokens at the MCP and the API (STORY-oss-25, ADR 0041 §9)
# --------------------------------------------------------------------------- #
#
# ADDITIVE, OPT-IN. With ATLAS_OAUTH_RESOURCE unset (the default) nothing changes:
# a Bearer that is not `atlas_svc_...` is refused exactly as before, atlas-mcp
# serves no metadata and its 401 is byte for byte today's. Set, atlas-api ALSO
# accepts an OAuth 2.1 access token (a JWT) from the OIDC issuer, issued for this
# resource, beside the Atlas-minted `atlas_svc_` tokens, which are untouched.
#
#   ATLAS_OAUTH_RESOURCE     the canonical URI of the protected resource: the MCP
#                            endpoint as clients name it (RFC 8707 / RFC 9728),
#                            e.g. https://atlas.example.com:8446/mcp. A token's
#                            `aud` must contain it. atlas-mcp reads the same name
#                            (one .env in the OSS install) for its metadata.
#   ATLAS_OAUTH_AUDIENCES    comma list of FURTHER accepted `aud` values, for an
#                            IdP that stamps something else: Entra v2 puts the
#                            resource app's client id (a GUID) in `aud`, not the
#                            URI the client asked for. Unset = the resource only.
#   ATLAS_OAUTH_SCOPE_MAP    comma list of <idp scope>=<read|write|admin>. A token
#                            gets the highest tier any of its `scp`/`scope` values
#                            maps to; write includes read, admin includes both.
#                            Unset, or no value mapped: read only.
#
# Rules, each raising `SettingsError` naming the variable (identity calls this
# at import, so a bad value stops the boot):
#
#   * set-but-blank is an error, everywhere in this block;
#   * ATLAS_OAUTH_RESOURCE is an https URL with no query or fragment; a trailing
#     slash is dropped and scheme and host are lowercased (the MCP spec's
#     canonical form);
#   * it needs `oidc` in ATLAS_AUTH_PROVIDERS: the issuer, its discovery and
#     JWKS, and the person key (ATLAS_OIDC_PRESET) are the SSO login's, so the
#     person an access token names is the account their SSO login made;
#   * an accepted audience may never be ATLAS_OIDC_CLIENT_ID: that is the `aud`
#     of Atlas's own ID tokens, and an ID token is not an access token;
#   * the other two variables are an error without ATLAS_OAUTH_RESOURCE (a
#     half-configured feature is a typo, not a choice).
#
# Reads only the mapping; opens no file.

OAUTH_RESOURCE_ENV = "ATLAS_OAUTH_RESOURCE"
OAUTH_AUDIENCES_ENV = "ATLAS_OAUTH_AUDIENCES"
OAUTH_SCOPE_MAP_ENV = "ATLAS_OAUTH_SCOPE_MAP"

#: The tiers an IdP scope can map to, narrowest first, and the Atlas scopes each
#: one carries (Atlas scopes do not imply each other: `models_auth` §scopes).
OAUTH_TIERS = ("read", "write", "admin")
OAUTH_TIER_SCOPES = {"read": ("read",), "write": ("read", "write"),
                     "admin": ("read", "write", "admin")}


@dataclass(frozen=True)
class OAuthSettings:
    """ATLAS_OAUTH_*, parsed. `resource` None = the feature is off (today)."""

    resource: Optional[str] = None
    audiences: tuple = ()
    scope_map: tuple = ()          # ((idp_scope, tier), ...), in the order given

    @property
    def enabled(self) -> bool:
        return self.resource is not None


def canonical_resource(raw: str, name: str = OAUTH_RESOURCE_ENV) -> str:
    """An https resource URI in canonical form: lowercase scheme and host, no
    trailing slash. atlas-mcp's copy (mcp/app/settings.py) must agree."""
    parts = urlsplit(_https_url(name, raw))
    return "%s://%s%s" % (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"))


def load_oauth(environ: Optional[Mapping[str, str]] = None, *,
               identity: Optional[IdentitySettings] = None) -> OAuthSettings:
    """Read ATLAS_OAUTH_* from `environ` (default `os.environ`).

    `identity` is the parsed identity settings (default: parsed from the same
    mapping), for the rule that the feature rides on the OIDC provider."""
    env = os.environ if environ is None else environ
    resource = _ident_value(env, OAUTH_RESOURCE_ENV)
    audiences_raw = _ident_value(env, OAUTH_AUDIENCES_ENV)
    map_raw = _ident_value(env, OAUTH_SCOPE_MAP_ENV)
    if resource is None:
        for name, raw in ((OAUTH_AUDIENCES_ENV, audiences_raw), (OAUTH_SCOPE_MAP_ENV, map_raw)):
            if raw is not None:
                raise SettingsError("%s is set but %s is not, and means nothing without it. "
                                    "Set %s, or unset %s." % (name, OAUTH_RESOURCE_ENV,
                                                              OAUTH_RESOURCE_ENV, name), variable=name)
        return OAuthSettings()

    resource = canonical_resource(resource)
    ident = identity if identity is not None else load_identity(env)
    if not ident.oidc_enabled:
        raise SettingsError(
            "%s needs oidc in %s: access tokens are checked against the OIDC issuer, and the "
            "person a token names is the account their SSO login made." % (
                OAUTH_RESOURCE_ENV, AUTH_PROVIDERS_ENV), variable=OAUTH_RESOURCE_ENV)

    audiences = [resource]
    if audiences_raw is not None:
        extra = [chunk.strip() for chunk in audiences_raw.split(",")]
        if any(not a or any(c.isspace() for c in a) or len(a) > 256 for a in extra):
            raise SettingsError("%s=%r has an empty or unusable entry." % (
                OAUTH_AUDIENCES_ENV, audiences_raw), variable=OAUTH_AUDIENCES_ENV)
        audiences.extend(extra)
    if ident.oidc.client_id and ident.oidc.client_id in audiences:
        raise SettingsError(
            "%s/%s accept %s (%r) as an audience: that is the audience of Atlas's own ID "
            "tokens, and an ID token must never pass as an access token. Give the resource "
            "its own app registration (Entra: an exposed API) and use its audience." % (
                OAUTH_RESOURCE_ENV, OAUTH_AUDIENCES_ENV, OIDC_CLIENT_ID_ENV,
                ident.oidc.client_id), variable=OAUTH_RESOURCE_ENV)

    scope_map: list = []
    if map_raw is not None:
        for chunk in map_raw.split(","):
            idp_scope, sep, tier = (part.strip() for part in chunk.partition("="))
            if not sep or not idp_scope or any(c.isspace() for c in idp_scope) \
                    or tier.lower() not in OAUTH_TIERS:
                raise SettingsError(
                    "%s=%r: each entry is <idp scope>=<%s>, e.g. Atlas.Write=write." % (
                        OAUTH_SCOPE_MAP_ENV, map_raw, "|".join(OAUTH_TIERS)), variable=OAUTH_SCOPE_MAP_ENV)
            scope_map.append((idp_scope, tier.lower()))
    return OAuthSettings(resource=resource, audiences=tuple(dict.fromkeys(audiences)),
                         scope_map=tuple(scope_map))


# --------------------------------------------------------------------------- #
# Describing a new setting (STORY-atlas-config-panel)
# --------------------------------------------------------------------------- #
#
# Every `ATLAS_*` name written in this module is shown on the board's
# configuration panel, through `settings_catalog.CATALOG`: its part node, its
# kind (plain / secret / bootstrap) and how its source is decided. A setting
# added here needs its line there too; `verify_config_panel.py` fails until it has one.


# --------------------------------------------------------------------------- #
# The setup guide's extra question file (STORY-atlas-setup-guide-adrs)
# --------------------------------------------------------------------------- #
#
# ADDITIVE, OPT-IN. The guide ships one question set (backend/data/setup_guide/);
# ATLAS_SETUP_QUESTIONS_FILE names a further JSON file of the same shape whose topics
# are asked AFTER the shipped ones, so an install adds its own questions without a code
# change. Unset or empty = the shipped set only (today).
#
# This loader reads only the mapping and opens nothing, so boot stays a read. The FILE
# is read and validated by `setup_guide.load_question_set` each time the guide is used;
# a bad file is an error naming the file and the place in it, answered by the guide's
# routes (nothing else in Atlas reads it). Appended as its own block.

SETUP_QUESTIONS_FILE_ENV = "ATLAS_SETUP_QUESTIONS_FILE"


def load_setup_questions_file(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """`ATLAS_SETUP_QUESTIONS_FILE`, stripped: the path, or None when unset or empty."""
    env = os.environ if environ is None else environ
    raw = (env.get(SETUP_QUESTIONS_FILE_ENV) or "").strip()
    return raw or None


# --------------------------------------------------------------------------- #
# Routine backups outside crunch week (TASK-1416, OSS-26 C)
# --------------------------------------------------------------------------- #
#
# ADDITIVE, OPT-IN. The `backup` compose profile's loop (`crunch_backup.py`) acts
# only while a crunch with a backup policy is on. These two variables give an
# install a routine backup of its own, outside any crunch:
#
#   ATLAS_BACKUP_SCHEDULE   off | hourly | daily | weekly        (off)
#   ATLAS_BACKUP_KEEP       how many routine backups to keep, 1..1000    (7)
#
# With ATLAS_BACKUP_SCHEDULE unset or `off` (the default) nothing changes: the
# loop does exactly what it did, and the homelab, which runs no backup loop and
# sets neither variable, is untouched. Crunch behaviour is unchanged either way:
# the crunch policy is asked first, every tick, as before; a routine backup is
# taken only on a tick where the crunch path took none.
#
# A routine backup is the same verified dump as every other (`atlas-<UTC>-routine.dump`,
# its sha256 and manifest), due when the newest routine dump in the directory is
# older than the interval. After a VERIFIED routine backup, routine dumps beyond
# the newest ATLAS_BACKUP_KEEP are deleted (with their sidecars); nothing else in
# the directory is touched: manual, per-deploy and crunch backups, and the
# pre-restore safety copies, are never pruned by this rule.
#
# Read by `crunch_backup` only (atlas-backup's process). atlas-api never acts on
# them; it reads them only to show them on the configuration panel. Set-but-blank
# is an error naming the variable (TASK-1348's rule).

BACKUP_SCHEDULE_ENV = "ATLAS_BACKUP_SCHEDULE"
BACKUP_KEEP_ENV = "ATLAS_BACKUP_KEEP"
BACKUP_SCHEDULES = ("off", "hourly", "daily", "weekly")
DEFAULT_BACKUP_SCHEDULE = "off"
DEFAULT_BACKUP_KEEP = 7
BACKUP_KEEP_MAX = 1000


@dataclass(frozen=True)
class BackupSchedule:
    """`ATLAS_BACKUP_SCHEDULE` and `ATLAS_BACKUP_KEEP`, parsed. See the block above."""

    schedule: str = DEFAULT_BACKUP_SCHEDULE
    keep: int = DEFAULT_BACKUP_KEEP

    @property
    def enabled(self) -> bool:
        return self.schedule != "off"


def load_backup_schedule(environ: Optional[Mapping[str, str]] = None) -> BackupSchedule:
    """Read the routine-backup variables from `environ` (default `os.environ`)."""
    env = os.environ if environ is None else environ
    schedule = DEFAULT_BACKUP_SCHEDULE
    raw = env.get(BACKUP_SCHEDULE_ENV)
    if raw is not None:
        value = raw.strip().lower()
        if not value:
            raise SettingsError("%s is set but empty. Set one of %s, or unset it (off)."
                                % (BACKUP_SCHEDULE_ENV, ", ".join(BACKUP_SCHEDULES)), variable=BACKUP_SCHEDULE_ENV)
        if value not in BACKUP_SCHEDULES:
            raise SettingsError("%s=%r is not one of %s."
                                % (BACKUP_SCHEDULE_ENV, raw, ", ".join(BACKUP_SCHEDULES)), variable=BACKUP_SCHEDULE_ENV)
        schedule = value
    keep = DEFAULT_BACKUP_KEEP
    raw = env.get(BACKUP_KEEP_ENV)
    if raw is not None:
        value = raw.strip()
        if not value:
            raise SettingsError("%s is set but empty. Give a number of backups, or unset it "
                                "(%d)." % (BACKUP_KEEP_ENV, DEFAULT_BACKUP_KEEP), variable=BACKUP_KEEP_ENV)
        try:
            keep = int(value)
        except ValueError:
            raise SettingsError("%s=%r is not a whole number." % (BACKUP_KEEP_ENV, raw), variable=BACKUP_KEEP_ENV) from None
        if not 1 <= keep <= BACKUP_KEEP_MAX:
            raise SettingsError("%s=%r must be between 1 and %d."
                                % (BACKUP_KEEP_ENV, raw, BACKUP_KEEP_MAX), variable=BACKUP_KEEP_ENV)
    return BackupSchedule(schedule=schedule, keep=keep)


# --------------------------------------------------------------------------- #
# Certificate expiry in /api/health (TASK-1419, OSS-26 F)
# --------------------------------------------------------------------------- #
#
# ADDITIVE, OPT-IN. Nothing warned before an install's server certificate ran out
# (init-certs.sh issues 397 days). These two variables let `/api/health` say so,
# the same way it reports `token_warnings`:
#
#   ATLAS_CERT_EXPIRY_FILE       path of a PEM certificate to watch        (unset)
#   ATLAS_CERT_EXPIRY_WARN_DAYS  warn this many days before it expires, 1..3650  (30)
#
# With ATLAS_CERT_EXPIRY_FILE unset (the homelab, whose TLS is terminated by its
# own Caddy and Cloudflare, outside this process) `/api/health` has no
# `cert_warnings` field at all: the response is byte-for-byte what it was. The
# OSS compose file sets it to the install's server certificate, mounted into
# atlas-api read-only (a public certificate, never its key).
#
# This loader reads only the mapping and opens nothing; the FILE is read by
# `cert_expiry.warnings` on each `/api/health` call, so a renewed certificate
# is seen without a restart. Set-but-blank is an error naming the variable
# (TASK-1348's rule), and so is a warn-days value that is not a whole number in
# range. routers/health.py loads it at import, so a typo stops the boot.

CERT_EXPIRY_FILE_ENV = "ATLAS_CERT_EXPIRY_FILE"
CERT_EXPIRY_WARN_DAYS_ENV = "ATLAS_CERT_EXPIRY_WARN_DAYS"
DEFAULT_CERT_EXPIRY_WARN_DAYS = 30
CERT_EXPIRY_WARN_DAYS_MAX = 3650


@dataclass(frozen=True)
class CertExpiry:
    """`ATLAS_CERT_EXPIRY_FILE` and `ATLAS_CERT_EXPIRY_WARN_DAYS`, parsed. See the block above."""

    #: The certificate to watch; None = no certificate configured, no field in /api/health.
    file: Optional[str] = None
    warn_days: int = DEFAULT_CERT_EXPIRY_WARN_DAYS

    @property
    def enabled(self) -> bool:
        return self.file is not None


def load_cert_expiry(environ: Optional[Mapping[str, str]] = None) -> CertExpiry:
    """Read the certificate-expiry variables from `environ` (default `os.environ`)."""
    env = os.environ if environ is None else environ
    path: Optional[str] = None
    raw = env.get(CERT_EXPIRY_FILE_ENV)
    if raw is not None:
        path = raw.strip()
        if not path:
            raise SettingsError("%s is set but empty. Give the path of a PEM certificate, or "
                                "unset it (no certificate is watched)." % CERT_EXPIRY_FILE_ENV, variable=CERT_EXPIRY_FILE_ENV)
    days = DEFAULT_CERT_EXPIRY_WARN_DAYS
    raw = env.get(CERT_EXPIRY_WARN_DAYS_ENV)
    if raw is not None:
        value = raw.strip()
        if not value:
            raise SettingsError("%s is set but empty. Give a number of days, or unset it (%d)."
                                % (CERT_EXPIRY_WARN_DAYS_ENV, DEFAULT_CERT_EXPIRY_WARN_DAYS), variable=CERT_EXPIRY_WARN_DAYS_ENV)
        try:
            days = int(value)
        except ValueError:
            raise SettingsError("%s=%r is not a whole number of days."
                                % (CERT_EXPIRY_WARN_DAYS_ENV, raw), variable=CERT_EXPIRY_WARN_DAYS_ENV) from None
        if not 1 <= days <= CERT_EXPIRY_WARN_DAYS_MAX:
            raise SettingsError("%s=%r must be between 1 and %d days."
                                % (CERT_EXPIRY_WARN_DAYS_ENV, raw, CERT_EXPIRY_WARN_DAYS_MAX), variable=CERT_EXPIRY_WARN_DAYS_ENV)
    return CertExpiry(file=path, warn_days=days)


# --------------------------------------------------------------------------- #
# Rules other modules apply at import or per call (TASK-1433)
# --------------------------------------------------------------------------- #
#
# NOTHING NEW IS READ HERE, AND NO RULE CHANGES. Each function below is the rule a
# module already applied to `os.environ` (github_link.repo, auth.session_mint_enabled,
# auth.MACHINE_GRACE_HOSTS, passwords.trusted_proxy_networks, deps.FRONTEND), moved here
# unchanged so that module and the configuration catalogue (settings_catalog.describe)
# apply the SAME rule, the catalogue to the mapping it is given. Those modules need the
# peppers to import; this one does not. Each module now calls these with no argument,
# which reads `os.environ` exactly as it did.

GITHUB_REPO_ENV = "ATLAS_GITHUB_REPO"
SESSION_MINT_ENV = "ATLAS_ALLOW_SESSION_MINT"
MACHINE_GRACE_ENV = "ATLAS_MACHINE_GRACE_HOSTS"
TRUSTED_PROXY_ENV = "ATLAS_TRUSTED_PROXY_CIDRS"
FRONTEND_DIR_ENV = "ATLAS_FRONTEND_DIR"
DEFAULT_FRONTEND_DIR = "/app/frontend"

_GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


def load_github_repo(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """`ATLAS_GITHUB_REPO` when it is an `owner/name`, else None (github_link.repo)."""
    env = os.environ if environ is None else environ
    value = env.get(GITHUB_REPO_ENV, "").strip()
    return value if _GITHUB_REPO_RE.match(value) else None


def load_session_mint(environ: Optional[Mapping[str, str]] = None) -> bool:
    """`ATLAS_ALLOW_SESSION_MINT`: on for 1/true/yes/on, off otherwise (auth.session_mint_enabled)."""
    env = os.environ if environ is None else environ
    return env.get(SESSION_MINT_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def load_machine_grace_hosts(environ: Optional[Mapping[str, str]] = None) -> frozenset:
    """`ATLAS_MACHINE_GRACE_HOSTS`, lowercased, blanks dropped (auth.MACHINE_GRACE_HOSTS)."""
    env = os.environ if environ is None else environ
    return frozenset(
        chunk.strip().lower() for chunk in env.get(MACHINE_GRACE_ENV, "").split(",") if chunk.strip()
    )


def parse_networks(raw: str, on_bad=None) -> tuple:
    """The networks in a comma list; an unparseable entry is skipped, after `on_bad(entry)`
    when given (passwords logs it). The rule of passwords.trusted_proxy_networks."""
    import ipaddress
    networks = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            networks.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            if on_bad is not None:
                on_bad(chunk)
    return tuple(networks)


def load_trusted_proxy_networks(environ: Optional[Mapping[str, str]] = None, on_bad=None) -> tuple:
    """`ATLAS_TRUSTED_PROXY_CIDRS` as networks; empty by default."""
    env = os.environ if environ is None else environ
    return parse_networks(env.get(TRUSTED_PROXY_ENV, ""), on_bad)


def load_frontend_dir(environ: Optional[Mapping[str, str]] = None) -> str:
    """`ATLAS_FRONTEND_DIR`, as given (set but blank stays blank), else the image's path (deps.FRONTEND)."""
    env = os.environ if environ is None else environ
    return env.get(FRONTEND_DIR_ENV, DEFAULT_FRONTEND_DIR)


def load_group_map(path: Optional[str]) -> dict:
    """The OIDC group-map allowlist file, or {} when none is configured. Raises SettingsError
    naming ATLAS_OIDC_GROUP_MAP_FILE. The rule of identity.groups.load_group_map, which calls
    this; here so the catalogue can check the file without importing the identity package
    (which needs the peppers)."""
    if path is None:
        return {}
    import yaml  # only when a group map is configured
    name = OIDC_GROUP_MAP_FILE_ENV
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
    except OSError as exc:
        raise SettingsError("%s=%r cannot be read (%s)." % (
            name, path, type(exc).__name__), variable=name) from None
    except yaml.YAMLError:
        raise SettingsError("%s=%r is not valid YAML or JSON." % (name, path), variable=name) from None
    if not isinstance(doc, dict) or not isinstance(doc.get("groups"), dict) or set(doc) - {"groups"}:
        raise SettingsError(
            "%s=%r must hold one mapping, `groups:`, of IdP group value -> Atlas group name."
            % (name, path), variable=name)
    out: dict = {}
    for key, value in doc["groups"].items():
        if not isinstance(key, str) or not key.strip() or len(key) > MAX_GROUP_VALUE:
            raise SettingsError("%s=%r: %r is not an IdP group value." % (name, path, key), variable=name)
        if (not isinstance(value, str) or not value.strip() or len(value) > MAX_GROUP_NAME):
            raise SettingsError("%s=%r: %r -> %r is not an Atlas group name."
                                % (name, path, key, value), variable=name)
        out[key.strip()] = value.strip()
    return out


#: `auth_group.name` is VARCHAR(128); `external_id` VARCHAR(256) (identity.groups re-exports these).
MAX_GROUP_NAME = 128
MAX_GROUP_VALUE = 256
