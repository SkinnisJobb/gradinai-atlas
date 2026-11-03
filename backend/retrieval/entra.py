"""
Entra ID tokens for the embedder (TASK-1375): `ATLAS_EMBED_AUTH_HEADER=entra`.

An Azure OpenAI / Azure AI Foundry deployment can refuse API keys and accept only a token from
Microsoft Entra ID for the Cognitive Services resource. This module gets one, from the identity the
process already runs as, and sends it as `Authorization: Bearer`. Nothing here runs unless that
mode is chosen, and nothing is imported by the add-on's other modules until a call needs a token.

NO NEW DEPENDENCY. The three flows are each one small HTTP exchange, done with httpx, which Atlas
already ships; the Azure SDK would add a dependency tree for the same three requests. Chosen in
the order the Azure SDK's DefaultAzureCredential tries them, so an install behaves as Azure's own
tooling would on the same host:

  1. WORKLOAD IDENTITY (AKS and other federated workloads): AZURE_FEDERATED_TOKEN_FILE,
     AZURE_CLIENT_ID and AZURE_TENANT_ID are set. The file's JWT is exchanged at
     {AZURE_AUTHORITY_HOST}{tenant}/oauth2/v2.0/token (client credentials with a client assertion).
     The file is read on every exchange: the platform rotates it.
  2. MANAGED IDENTITY, App Service / Container Apps: IDENTITY_ENDPOINT and IDENTITY_HEADER are set.
  3. MANAGED IDENTITY, IMDS (a VM, a VM scale set): http://169.254.169.254, with AZURE_CLIENT_ID
     naming a user-assigned identity when there is more than one.

THE TOKEN is cached in this process and fetched again `REFRESH_MARGIN_S` before it expires, under
a lock, so concurrent calls share one fetch. It is never logged, never stored on disk, never put in
an environment variable, and never part of an exception: `EntraError` names the flow, the host and
an HTTP status, nothing else.

The scope is the Cognitive Services resource, `ATLAS_EMBED_ENTRA_SCOPE` to override it (a sovereign
cloud uses its own).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Mapping, Optional
from urllib.parse import urlsplit

import httpx

DEFAULT_SCOPE = "https://cognitiveservices.azure.com/.default"
DEFAULT_AUTHORITY = "https://login.microsoftonline.com/"
#: The instance metadata endpoint. A module constant so a check can point it at a fake.
IMDS_URL = "http://169.254.169.254/metadata/identity/oauth2/token"
REFRESH_MARGIN_S = 300
TIMEOUT_S = 30.0

_lock = threading.Lock()
_cache: dict = {"token": None, "expires_at": 0.0, "scope": None}


class EntraError(RuntimeError):
    """No token could be obtained. Safe to show: never contains a token or an assertion."""


def flow(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Which flow this process would use, or None when no identity is configured."""
    env = os.environ if env is None else env
    if env.get("AZURE_FEDERATED_TOKEN_FILE") and env.get("AZURE_CLIENT_ID") and env.get("AZURE_TENANT_ID"):
        return "workload-identity"
    if env.get("IDENTITY_ENDPOINT") and env.get("IDENTITY_HEADER"):
        return "managed-identity"
    return "imds"


def _resource(scope: str) -> str:
    """Managed identity asks for a RESOURCE, not a scope: the scope minus `/.default`."""
    return scope[: -len("/.default")] if scope.endswith("/.default") else scope


def _workload(env: Mapping[str, str], scope: str) -> tuple[str, float]:
    path = env["AZURE_FEDERATED_TOKEN_FILE"]
    try:
        with open(path, "r", encoding="utf-8") as fh:
            assertion = fh.read().strip()
    except OSError as exc:
        raise EntraError(f"AZURE_FEDERATED_TOKEN_FILE points at {path!r}, which cannot be read "
                         f"({type(exc).__name__})") from None
    authority = (env.get("AZURE_AUTHORITY_HOST") or DEFAULT_AUTHORITY).rstrip("/") + "/"
    url = f"{authority}{env['AZURE_TENANT_ID']}/oauth2/v2.0/token"
    data = {
        "client_id": env["AZURE_CLIENT_ID"],
        "scope": scope,
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
    }
    body = _call("workload identity", "POST", url, data=data)
    return body["access_token"], time.time() + float(body.get("expires_in", 0))


def _managed(env: Mapping[str, str], scope: str) -> tuple[str, float]:
    params = {"resource": _resource(scope), "api-version": "2019-08-01"}
    if env.get("AZURE_CLIENT_ID"):
        params["client_id"] = env["AZURE_CLIENT_ID"]
    body = _call("managed identity", "GET", env["IDENTITY_ENDPOINT"], params=params,
                 headers={"X-IDENTITY-HEADER": env["IDENTITY_HEADER"]})
    return body["access_token"], _expiry(body)


def _imds(env: Mapping[str, str], scope: str) -> tuple[str, float]:
    params = {"resource": _resource(scope), "api-version": "2018-02-01"}
    if env.get("AZURE_CLIENT_ID"):
        params["client_id"] = env["AZURE_CLIENT_ID"]
    body = _call("IMDS", "GET", IMDS_URL, params=params, headers={"Metadata": "true"})
    return body["access_token"], _expiry(body)


def _expiry(body: dict) -> float:
    """Managed identity answers `expires_on` (epoch seconds, as a string); some hosts only
    `expires_in`. Either is accepted; neither means the token is treated as already stale."""
    if body.get("expires_on"):
        return float(body["expires_on"])
    return time.time() + float(body.get("expires_in", 0))


def _call(label: str, method: str, url: str, **kw) -> dict:
    host = urlsplit(url).hostname or "?"
    try:
        with httpx.Client(timeout=TIMEOUT_S) as client:
            resp = client.request(method, url, **kw)
    except httpx.HTTPError as exc:
        raise EntraError(f"Entra {label} at {host} unreachable ({type(exc).__name__})") from None
    if resp.status_code != 200:
        # The body of a refused token request names the error code (AADSTS…), never a secret.
        raise EntraError(f"Entra {label} at {host} answered {resp.status_code}: {resp.text[:200]!r}")
    try:
        body = resp.json()
        if not body.get("access_token"):
            raise ValueError
    except ValueError:
        raise EntraError(f"Entra {label} at {host} answered 200 without an access token") from None
    return body


def token(scope: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> str:
    """A current access token for `scope`, from the cache or from the identity this process has."""
    env = os.environ if env is None else env
    scope = scope or env.get("ATLAS_EMBED_ENTRA_SCOPE", "").strip() or DEFAULT_SCOPE
    with _lock:
        if (_cache["token"] and _cache["scope"] == scope
                and _cache["expires_at"] - REFRESH_MARGIN_S > time.time()):
            return _cache["token"]
        how = flow(env)
        fetch = {"workload-identity": _workload, "managed-identity": _managed, "imds": _imds}[how]
        try:
            value, expires_at = fetch(env, scope)
        except (KeyError, TypeError, ValueError):
            raise EntraError(f"Entra {how} answered a token response of an unexpected shape") from None
        _cache.update(token=value, expires_at=expires_at, scope=scope)
        return value


def reset() -> None:
    """Forget the cached token (a check uses this; so would a scope change)."""
    with _lock:
        _cache.update(token=None, expires_at=0.0, scope=None)
