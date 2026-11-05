"""atlas-mcp's feature flags: `ATLAS_DISABLED_FEATURES` (STORY-oss-08-plugins).

The same setting atlas-api reads in `backend/settings.py` (`load_features`), with
the same names and the same rule. atlas-mcp cannot import that module (its image
holds only mcp/), so the list is repeated here; `mcp/verify_feature_flags.py`
fails if the two copies drift.

ADDITIVE, OPT-IN. Unset or empty (the default) means every feature is on and
atlas-mcp registers exactly the tools and prompts it always did. An unknown
name stops the process at import, naming it, the way `client._require_env` does
for a missing variable: a typo must not leave a feature on in silence.

What each name switches off in atlas-mcp:

  * save_work      -> `atlas_save_work` and the `signoff` prompt are not
                      registered (mcp_server.py), so tools/list and
                      atlas_capabilities.tools do not name them;
  * deploy_warning -> no `deploy_warning` field on any response (envelope.py),
                      and no `/api/health` read to build one;
  * deploy_status  -> atlas-mcp's own image check is skipped (deploy_warning.py);
                      atlas-api's warnings, if it still reports them, still ride;
  * github_link, attention_probes -> nothing here; they live in atlas-api.

Deliberately NOT `from __future__ import annotations` (the package rule).
"""

import os
import sys
from typing import Mapping, Optional


def _load_distribution():
    """The estate-valued defaults (mcp/app/distribution.py, STORY-oss-23 A2).

    Imported as this package's sibling; when this module is itself loaded by path, with no
    package (backend/verify_deploy_status.py does that), the sibling file is loaded by path.
    """
    try:
        from . import distribution as dist
        return dist
    except ImportError:
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "_atlas_mcp_distribution", Path(__file__).resolve().with_name("distribution.py"))
        dist = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dist)
        return dist


#: Every default below that names an installation comes from here; the values in
#: project-atlas are the homelab's, exactly as they were written inline before.
distribution = _load_distribution()

DISABLED_FEATURES_ENV = "ATLAS_DISABLED_FEATURES"

#: Mirrors backend/settings.py FEATURES, in the same order.
FEATURES = ("github_link", "save_work", "deploy_status", "deploy_warning", "attention_probes")


class FeatureError(ValueError):
    """ATLAS_DISABLED_FEATURES names something that is not a feature."""


def parse_disabled(environ: Optional[Mapping[str, str]] = None) -> frozenset:
    """The disabled feature names; raises FeatureError on an unknown one."""
    env = os.environ if environ is None else environ
    raw = env.get(DISABLED_FEATURES_ENV, "")
    names = frozenset(chunk.strip().lower() for chunk in raw.split(",") if chunk.strip())
    unknown = sorted(names - set(FEATURES))
    if unknown:
        raise FeatureError(
            f"{DISABLED_FEATURES_ENV}={raw!r} names unknown feature(s) {', '.join(unknown)}; "
            f"the features are {', '.join(FEATURES)}."
        )
    return names


def _load() -> frozenset:
    try:
        return parse_disabled()
    except FeatureError as exc:
        sys.exit(f"atlas-mcp refuses to start: {exc}")


#: Read once, at import, like ATLAS_MCP_ENABLE_DELETE: one process, one configuration.
DISABLED = _load()


def feature_enabled(name: str) -> bool:
    if name not in FEATURES:
        raise KeyError(f"{name!r} is not a feature (one of {', '.join(FEATURES)}).")
    return name not in DISABLED


# --------------------------------------------------------------------------- #
# Deploy hint commands (TASK-1356)
# --------------------------------------------------------------------------- #
# The same ATLAS_DEPLOY_HINT_* settings atlas-api reads in backend/settings.py
# (`load_deploy_hints`; the rationale is there), repeated because atlas-mcp
# cannot import that module. backend/verify_deploy_status.py fails if the names,
# the defaults or the answers drift. Unset (the default): the homelab commands,
# byte for byte. A set-but-blank value stops the process at import, naming it.

DEPLOY_HINT_HOST_ENV = "ATLAS_DEPLOY_HINT_HOST"
#: From distribution.py (the homelab: its deploy host).
DEFAULT_DEPLOY_HINT_HOST = distribution.DEFAULT_DEPLOY_HINT_HOST

#: Mirrors backend/settings.py DEPLOY_HINT_ENVS, in the same order.
DEPLOY_HINT_ENVS = (
    ("rebuild", "atlas-api", "ATLAS_DEPLOY_HINT_REBUILD_API"),
    ("rebuild", "atlas-mcp", "ATLAS_DEPLOY_HINT_REBUILD_MCP"),
    ("pull_and_rebuild", "atlas-api", "ATLAS_DEPLOY_HINT_PULL_REBUILD_API"),
    ("pull_and_rebuild", "atlas-mcp", "ATLAS_DEPLOY_HINT_PULL_REBUILD_MCP"),
)

#: Mirrors backend/settings.py DEFAULT_DEPLOY_HINTS; the values are distribution.py's.
#: A variable with no entry has no default command (a neutral distribution ships none):
#: `load_deploy_hints` then answers with `NO_DEPLOY_HINT`, naming the variable to set.
DEFAULT_DEPLOY_HINTS = dict(distribution.DEFAULT_DEPLOY_HINTS)

#: The answer for a hint with neither a value nor a default.
NO_DEPLOY_HINT = "(no command configured: set {name})"


class DeployHintError(ValueError):
    """An ATLAS_DEPLOY_HINT_* variable is set but blank."""


class DeployHints:
    """The resolved hints: `host`, and `rebuild` / `pull_and_rebuild` (component -> command)."""

    def __init__(self, host: str, rebuild: dict, pull_and_rebuild: dict) -> None:
        self.host = host
        self.rebuild = rebuild
        self.pull_and_rebuild = pull_and_rebuild


def _deploy_hint_value(env: Mapping[str, str], name: str) -> Optional[str]:
    raw = env.get(name)
    if raw is None:
        return None
    if not raw.strip():
        raise DeployHintError(f"{name} is set but empty. Give it a value, or unset it to use the default.")
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


try:
    load_deploy_hints()      # at import, like DISABLED: a blank value stops the boot, not a tool call
except DeployHintError as _exc:
    sys.exit(f"atlas-mcp refuses to start: {_exc}")


# --------------------------------------------------------------------------- #
# Allowed Host / Origin headers: DNS-rebinding protection (TASK-1366)
# --------------------------------------------------------------------------- #
# mcp 1.30 checks every streamable-HTTP request's Host header against an allow-list
# and answers 421 to anything else, and an Origin header (only when one is sent)
# against a second list, 403 otherwise. The owner's decision (2026-10-25): protection ON,
# with an EXPLICIT list, never disabled. mcp_server.py passes both to FastMCP.
#
# Unset (the default): the hosts that reach atlas-mcp today, so the homelab needs no
# configuration. Set: a comma list that REPLACES the default (list the defaults again
# if you still want them). A set-but-blank value, or one with no entries, stops the
# process at import, naming it: an empty list would answer 421 to every caller, which
# is an outage, not a configuration. An entry may end in `:*` for any port (the SDK's
# own syntax).
#
# The defaults, and where each comes from, are in distribution.py.

ALLOWED_HOSTS_ENV = "ATLAS_MCP_ALLOWED_HOSTS"
ALLOWED_ORIGINS_ENV = "ATLAS_MCP_ALLOWED_ORIGINS"

DEFAULT_ALLOWED_HOSTS = tuple(distribution.DEFAULT_ALLOWED_HOSTS)

#: Checked only when a request CARRIES an Origin header. MCP clients that are not
#: browsers (Claude Code, agentgateway, the Python SDK) send none, and pass.
DEFAULT_ALLOWED_ORIGINS = tuple(distribution.DEFAULT_ALLOWED_ORIGINS)


class AllowedHostsError(ValueError):
    """ATLAS_MCP_ALLOWED_HOSTS or ATLAS_MCP_ALLOWED_ORIGINS is set but names nothing."""


def _host_list(env: Mapping[str, str], name: str, default: tuple) -> tuple:
    raw = env.get(name)
    if raw is None:
        return default
    entries = tuple(chunk.strip() for chunk in raw.split(",") if chunk.strip())
    if not entries:
        raise AllowedHostsError(
            f"{name} is set but empty. Give it a comma list, or unset it to use the default "
            f"({', '.join(default)})."
        )
    return entries


def load_allowed_hosts(environ: Optional[Mapping[str, str]] = None) -> tuple:
    """(allowed_hosts, allowed_origins) from `environ` (default `os.environ`)."""
    env = os.environ if environ is None else environ
    return (_host_list(env, ALLOWED_HOSTS_ENV, DEFAULT_ALLOWED_HOSTS),
            _host_list(env, ALLOWED_ORIGINS_ENV, DEFAULT_ALLOWED_ORIGINS))


try:
    # Read once, at import, like DISABLED: one process, one configuration.
    ALLOWED_HOSTS, ALLOWED_ORIGINS = load_allowed_hosts()
except AllowedHostsError as _exc:
    sys.exit(f"atlas-mcp refuses to start: {_exc}")


# --------------------------------------------------------------------------- #
# Client certificate for the hop to atlas-api: the optional mTLS leg (OSS-24)
# --------------------------------------------------------------------------- #
# ADDITIVE, OPT-IN, OFF BY DEFAULT. When the TLS front in front of atlas-api asks for a
# client certificate (deploy/oss/Caddyfile, ATLAS_TLS_CLIENT_AUTH=require), atlas-mcp
# presents the one named here on every connection to ATLAS_BASE_URL (app/tls.py loads it
# into the one verifying SSL context). It is a second, network-level identity BESIDE the
# caller's Bearer token, never instead of it: every request still carries its caller's
# token, and atlas-api still decides on that.
#
#   ATLAS_MCP_CLIENT_CERT_FILE   PEM certificate (optionally followed by its chain)
#   ATLAS_MCP_CLIENT_KEY_FILE    PEM private key for it, unencrypted, readable by this process
#
# Both unset (the default, and the homelab): no client certificate, exactly as before. Both
# set: both files must exist and be readable at startup. One without the other, a set-but-
# blank value, or a missing file stops the process at import, naming the variable, like
# everything above: a half-configured mTLS leg must not quietly connect without it. The key
# is read by OpenSSL only (ssl.SSLContext.load_cert_chain); its contents never pass through
# Python strings, logs or error messages.

CLIENT_CERT_FILE_ENV = "ATLAS_MCP_CLIENT_CERT_FILE"
CLIENT_KEY_FILE_ENV = "ATLAS_MCP_CLIENT_KEY_FILE"


class ClientCertError(ValueError):
    """ATLAS_MCP_CLIENT_CERT_FILE / ATLAS_MCP_CLIENT_KEY_FILE are half-set, blank or unreadable."""


def _client_cert_path(env: Mapping[str, str], name: str) -> Optional[str]:
    raw = env.get(name)
    if raw is None:
        return None
    path = raw.strip()
    if not path:
        raise ClientCertError(f"{name} is set but empty. Give it a file path, or unset it.")
    if not os.path.isfile(path) or not os.access(path, os.R_OK):
        raise ClientCertError(f"{name}={path!r} is not a readable file.")
    return path


def load_client_cert(environ: Optional[Mapping[str, str]] = None) -> Optional[tuple]:
    """(cert_path, key_path) from `environ` (default `os.environ`), or None when neither is set."""
    env = os.environ if environ is None else environ
    cert = _client_cert_path(env, CLIENT_CERT_FILE_ENV)
    key = _client_cert_path(env, CLIENT_KEY_FILE_ENV)
    if (cert is None) != (key is None):
        missing = CLIENT_KEY_FILE_ENV if key is None else CLIENT_CERT_FILE_ENV
        raise ClientCertError(
            f"{CLIENT_CERT_FILE_ENV} and {CLIENT_KEY_FILE_ENV} go together; {missing} is not set.")
    return None if cert is None else (cert, key)


try:
    # Read once, at import, like DISABLED: one process, one configuration.
    CLIENT_CERT = load_client_cert()
except ClientCertError as _exc:
    sys.exit(f"atlas-mcp refuses to start: {_exc}")


# --------------------------------------------------------------------------- #
# OAuth protected-resource metadata: IdP access tokens (STORY-oss-25, ADR 0041 §9)
# --------------------------------------------------------------------------- #
# ADDITIVE, OPT-IN, OFF BY DEFAULT. atlas-api validates IdP access tokens when
# ATLAS_OAUTH_RESOURCE is set (backend/settings.py `load_oauth`, the reasoning is
# there); atlas-mcp then tells an MCP client WHERE to get one, per the MCP
# authorization spec (RFC 9728): it serves /.well-known/oauth-protected-resource
# and its 401s carry `WWW-Authenticate: Bearer resource_metadata="..."`. It
# validates nothing itself (ADR 0033): the token goes to atlas-api, which decides.
#
#   ATLAS_OAUTH_RESOURCE     the resource URI, the SAME value atlas-api reads (one .env
#                            in the OSS install): the metadata's `resource`, and the
#                            audience the IdP must put in the token
#   ATLAS_OIDC_ISSUER        the authorization server, the SAME value atlas-api's SSO
#                            login reads: the metadata's `authorization_servers`
#   ATLAS_MCP_OAUTH_SCOPES   optional, space-separated: the scopes a client should ask
#                            for, verbatim as the IdP names them (Entra:
#                            api://<app>/Atlas.Write), as `scopes_supported` and as the
#                            401 challenge's `scope`. Unset: neither is sent.
#
# ATLAS_OAUTH_RESOURCE unset (the default, and the homelab): no metadata route, and
# the 401 is today's `Bearer realm="atlas-mcp"` byte for byte; ATLAS_OIDC_ISSUER alone
# (an install with SSO login only) changes nothing here. Set: the issuer is required,
# both must be https URLs without query or fragment, and a set-but-blank value stops
# the process at import, naming it. The resource is canonicalised exactly as
# backend/settings.py `canonical_resource` does (mcp/verify_oauth_resource.py checks the
# two agree): lowercase scheme and host, no trailing slash.

OAUTH_RESOURCE_ENV = "ATLAS_OAUTH_RESOURCE"
OAUTH_ISSUER_ENV = "ATLAS_OIDC_ISSUER"
OAUTH_SCOPES_ENV = "ATLAS_MCP_OAUTH_SCOPES"


class OAuthResourceError(ValueError):
    """ATLAS_OAUTH_RESOURCE / ATLAS_OIDC_ISSUER / ATLAS_MCP_OAUTH_SCOPES are unusable."""


class OAuthResource:
    """The resolved metadata settings: `resource`, `issuer`, `scopes` (a tuple)."""

    def __init__(self, resource: str, issuer: str, scopes: tuple) -> None:
        self.resource = resource
        self.issuer = issuer
        self.scopes = scopes


def _oauth_value(env: Mapping[str, str], name: str) -> Optional[str]:
    raw = env.get(name)
    if raw is None:
        return None
    if not raw.strip():
        raise OAuthResourceError(f"{name} is set but empty. Give it a value, or unset it.")
    return raw.strip()


def _oauth_https(name: str, raw: str) -> str:
    from urllib.parse import urlsplit
    parts = urlsplit(raw)
    if parts.scheme.lower() != "https" or not parts.netloc or parts.query or parts.fragment:
        raise OAuthResourceError(f"{name}={raw!r} is not an https URL without query or fragment.")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def load_oauth_resource(environ: Optional[Mapping[str, str]] = None) -> Optional[OAuthResource]:
    """The metadata settings from `environ` (default `os.environ`), or None when off."""
    env = os.environ if environ is None else environ
    resource = _oauth_value(env, OAUTH_RESOURCE_ENV)
    scopes_raw = _oauth_value(env, OAUTH_SCOPES_ENV)
    if resource is None:
        if scopes_raw is not None:
            raise OAuthResourceError(
                f"{OAUTH_SCOPES_ENV} is set but {OAUTH_RESOURCE_ENV} is not, and means nothing "
                f"without it. Set {OAUTH_RESOURCE_ENV}, or unset {OAUTH_SCOPES_ENV}.")
        return None
    resource = _oauth_https(OAUTH_RESOURCE_ENV, resource)
    issuer = _oauth_value(env, OAUTH_ISSUER_ENV)
    if issuer is None:
        raise OAuthResourceError(
            f"{OAUTH_RESOURCE_ENV} is set and {OAUTH_ISSUER_ENV} is not: the metadata must name "
            f"the authorization server (the OIDC issuer atlas-api checks tokens against).")
    from urllib.parse import urlsplit
    if urlsplit(issuer).scheme.lower() != "https" or urlsplit(issuer).query or urlsplit(issuer).fragment:
        raise OAuthResourceError(f"{OAUTH_ISSUER_ENV}={issuer!r} is not an https URL without query or fragment.")
    scopes = tuple(dict.fromkeys((scopes_raw or "").split()))
    if any('"' in sc or "\\" in sc for sc in scopes):
        raise OAuthResourceError(f"{OAUTH_SCOPES_ENV}={scopes_raw!r}: a scope may not contain a quote or backslash.")
    # The issuer is kept as atlas-api keeps it (backend/settings.py `_load_oidc`: rstrip "/"),
    # so the metadata names the very string the API matches `iss` against.
    return OAuthResource(resource=resource, issuer=issuer.rstrip("/"), scopes=scopes)


try:
    # Read once, at import, like DISABLED: one process, one configuration.
    OAUTH_RESOURCE = load_oauth_resource()
except OAuthResourceError as _exc:
    sys.exit(f"atlas-mcp refuses to start: {_exc}")


# --------------------------------------------------------------------------- #
# Stateless Streamable HTTP: more than one atlas-mcp replica (TASK-1480, ARO 2.2)
# --------------------------------------------------------------------------- #
# ADDITIVE, OPT-IN, OFF BY DEFAULT. atlas-mcp runs the MCP SDK's stateful Streamable
# HTTP transport: `initialize` opens a session held in this process's memory
# (mcp_server.py, 4 h idle timeout), and every later request names it in
# `Mcp-Session-Id`. A request that lands on another replica names a session that
# replica never saw and is refused, so behind a load balancer only one replica works.
#
# ATLAS_MCP_STATELESS_HTTP=1 runs FastMCP's `stateless_http` mode instead: each POST
# gets a fresh transport and server session, no session id is issued or required,
# and any replica can answer any request. What it does NOT change: the caller gate
# (caller_auth.RequireCallerToken) still runs on every request, and each tool call
# still forwards that request's own Bearer token to atlas-api (client.py); atlas-mcp
# holds no token of its own either way. The Host / Origin allow-list still applies
# (the SDK hands the same TransportSecuritySettings to each per-request transport).
# What a client loses: server-to-client requests and notifications on a standalone
# GET stream, and resumability; Atlas's tools use neither. The idle timeout means
# nothing here, because nothing outlives its request.
#
# Unset, empty, 0/false/no/off: the stateful transport, exactly as before. 1/true/
# yes/on: stateless. Anything else stops the process at import, naming the variable:
# a typo must not leave a multi-replica install half-working in silence.

STATELESS_HTTP_ENV = "ATLAS_MCP_STATELESS_HTTP"
_ON = ("1", "true", "yes", "on")
_OFF = ("", "0", "false", "no", "off")


class StatelessHttpError(ValueError):
    """ATLAS_MCP_STATELESS_HTTP is neither a true nor a false value."""


def load_stateless_http(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when `environ` (default `os.environ`) switches the stateless transport on."""
    env = os.environ if environ is None else environ
    raw = env.get(STATELESS_HTTP_ENV, "")
    value = raw.strip().lower()
    if value in _ON:
        return True
    if value in _OFF:
        return False
    raise StatelessHttpError(
        f"{STATELESS_HTTP_ENV}={raw!r} is not a switch value; use 1 or 0 "
        f"(also true/false, yes/no, on/off), or unset it.")


try:
    # Read once, at import, like DISABLED: one process, one configuration.
    STATELESS_HTTP = load_stateless_http()
except StatelessHttpError as _exc:
    sys.exit(f"atlas-mcp refuses to start: {_exc}")
