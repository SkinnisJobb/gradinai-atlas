"""OAuth 2.0 Protected Resource Metadata for atlas-mcp (RFC 9728; STORY-oss-25, ADR 0041 §9).

OPT-IN: everything here is inert while `settings.OAUTH_RESOURCE` is None (ATLAS_OAUTH_RESOURCE
unset, the default). Then main.py registers no route and caller_auth.py sends today's challenge.

WHAT THE MCP AUTHORIZATION SPEC ASKS OF A SERVER (revision 2026-07-28), and where each is met:

  * MUST implement RFC 9728 and its document MUST name at least one authorization server:
    `metadata()`, `authorization_servers` = [ATLAS_OIDC_ISSUER].
  * MUST offer discovery by the 401's `WWW-Authenticate: Bearer resource_metadata="..."` OR a
    well-known URI. Both are done: `challenge()` on every 401 from caller_auth.py, and the
    document at the path-inserted well-known URI of the resource (`/.well-known/
    oauth-protected-resource/mcp` for `https://host/mcp`, which clients try first) AND at the
    root `/.well-known/oauth-protected-resource` (their fallback).
  * SHOULD put `scope` in the challenge: only when ATLAS_MCP_OAUTH_SCOPES is set, because the
    scope string is the IdP's to name (Entra wants `api://<app>/<scope>`), not Atlas's.
  * MUST validate the token's audience and MUST NOT pass a token through to anyone else:
    atlas-api validates (backend/identity/access_token.py); atlas-mcp forwards the token only
    to atlas-api, which is the same protected resource (the token's audience), never elsewhere.

FROM THE mcp SDK (1.30). `mcp.server.auth.routes.build_resource_metadata_url` is used for the
well-known path (RFC 9728 §3.1 path insertion), and `mcp.shared.auth.ProtectedResourceMetadata`
validates the document at startup. The SDK's `create_protected_resource_routes` is NOT used to
SERVE it: its pydantic `AnyHttpUrl` rewrites a path-less resource `https://host` to
`https://host/`, and RFC 9728 §3.3 has the client compare `resource` to the URI it used, while
the API compares `aud` to it, exactly. The SDK's `RequireAuthMiddleware`/`TokenVerifier` are
not used either: they validate in this process, and ADR 0033 keeps validation in atlas-api.

Deliberately NOT `from __future__ import annotations` (the package rule).
"""

from typing import Optional

from . import settings

WELL_KNOWN_ROOT = "/.well-known/oauth-protected-resource"


def enabled() -> bool:
    return settings.OAUTH_RESOURCE is not None


def metadata_url(cfg=None) -> str:
    """The document's URL for the resource (RFC 9728 §3.1), as the 401 names it."""
    from mcp.server.auth.routes import build_resource_metadata_url
    cfg = cfg or settings.OAUTH_RESOURCE
    return str(build_resource_metadata_url(cfg.resource))


def metadata_paths(cfg=None) -> tuple:
    """The paths the document is served at: the path-inserted one (and its trailing-slash
    twin, for a client that probes from the mount URL `/mcp/`), then the root."""
    from urllib.parse import urlsplit
    path = urlsplit(metadata_url(cfg)).path
    twins = (path, path + "/") if path != WELL_KNOWN_ROOT else (path,)
    return tuple(dict.fromkeys(twins + (WELL_KNOWN_ROOT,)))


def metadata(cfg=None) -> dict:
    """The RFC 9728 document. `resource` is the configured string, unnormalised."""
    cfg = cfg or settings.OAUTH_RESOURCE
    doc = {
        "resource": cfg.resource,
        "authorization_servers": [cfg.issuer],
        "bearer_methods_supported": ["header"],
        "resource_name": "Atlas",
    }
    if cfg.scopes:
        doc["scopes_supported"] = list(cfg.scopes)
    return doc


def check_with_sdk(cfg=None) -> None:
    """Validate the document with the SDK's model; raises on an unusable one (at startup)."""
    from mcp.shared.auth import ProtectedResourceMetadata
    ProtectedResourceMetadata.model_validate(metadata(cfg))


def challenge(*, presented: bool, cfg=None) -> str:
    """The `WWW-Authenticate` value for a 401 while the feature is on.

    No credential: `Bearer resource_metadata="...", scope="..."` (the spec's example).
    A rejected one: the same with `error="invalid_token"` first (RFC 6750 §3.1).
    """
    cfg = cfg or settings.OAUTH_RESOURCE
    parts = []
    if presented:
        parts.append('error="invalid_token"')
    parts.append('resource_metadata="%s"' % metadata_url(cfg))
    if cfg.scopes:
        parts.append('scope="%s"' % " ".join(cfg.scopes))
    return "Bearer " + ", ".join(parts)


def install(app) -> Optional[tuple]:
    """Register the metadata routes on `app` when the feature is on. Returns the paths."""
    if not enabled():
        return None
    from fastapi.responses import JSONResponse

    check_with_sdk()
    doc = metadata()
    headers = {"Cache-Control": "public, max-age=3600", "Access-Control-Allow-Origin": "*"}

    def protected_resource_metadata() -> JSONResponse:
        return JSONResponse(doc, headers=headers)

    paths = metadata_paths()
    for path in paths:
        app.add_api_route(path, protected_resource_metadata, methods=["GET"],
                          include_in_schema=False)
    return paths
