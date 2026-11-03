"""
The retrieval add-on (ADR 0044 §1-§4, STORY-atlas-retrieval-indexing): its configuration.

OPT-IN, AND ATLAS IS COMPLETE WITHOUT IT. `ATLAS_RETRIEVAL` unset or `off` is the default and
means NOTHING here runs: `main.py` reads `enabled()` (one environment read, no import of the
rest of this package), mounts no route, creates no extension and no table, starts no loop, and
`/api/health` carries no `retrieval` field. The schema and every response are what they were
before this package existed; `verify_retrieval.py` §A proves it in a separate process.

TURNED ON, IT NEVER BREAKS BOOT. Every configuration problem below that is not a typo in the
switch itself (no endpoint, a plain-http endpoint off loopback, a key in a plain variable, a
Postgres without pgvector, a dimension that does not match the stored vectors) leaves Atlas
booting normally. The add-on then reports `unavailable: <reason>` on `/api/health` and
`/api/retrieval/status` — informational, never red — and full-text search is unaffected.
Only `ATLAS_RETRIEVAL` set to something that is neither `on` nor `off` stops the boot, the
same way an unknown `ATLAS_DISABLED_FEATURES` name does: a typo in a switch is not a state.

THE VARIABLES (ADR 0044 §2, plus the TLS and header choices settled on 2026-10-25, owner decision):

  ATLAS_RETRIEVAL            off | on, default off
  ATLAS_EMBED_URL            an OpenAI-compatible endpoint: its base (`…/v1`, `/embeddings` is
                             appended) or the full embeddings URL, query string included (an
                             Azure OpenAI deployment URL with `?api-version=` is used as given).
                             https:// is required, except to a loopback host.
  ATLAS_EMBED_MODEL          sent as `model` and stored on every row; changing it re-indexes
  ATLAS_EMBED_DIM            the vector length the endpoint returns, and the column's size
  ATLAS_EMBED_KEY_FILE       a PATH, read on every call (so a rotated key needs no restart);
                             optional, for an endpoint that takes no key. The key is NEVER a
                             variable: a plain `ATLAS_EMBED_KEY` makes the add-on unavailable.
  ATLAS_EMBED_AUTH_HEADER    bearer (default: `Authorization: Bearer` with the key) | api-key
                             (Azure OpenAI's `api-key:` header) | entra (an Entra ID token from the
                             process's workload or managed identity, `retrieval/entra.py`; no key
                             file then, and setting one is a problem)
  ATLAS_EMBED_ENTRA_SCOPE    the token scope in entra mode, default Cognitive Services
  ATLAS_EMBED_CA_BUNDLE      a PATH to a PEM bundle the endpoint's certificate is checked
                             against, instead of the system trust store (a private CA)
  ATLAS_EMBED_DOC_PREFIX / ATLAS_EMBED_QUERY_PREFIX   e.g. nomic's "search_document: "
  ATLAS_EMBED_SCOPE          shared (default) | all. `shared` never sends the text of a node
                             under a private mark to the endpoint; see `reconciler.in_scope`.
  ATLAS_EMBED_BATCH          texts per embeddings call, default 32
  ATLAS_RETRIEVAL_INTERVAL_S seconds between reconciler passes, default 60

NO VALUE OF THE KEY EVER LEAVES `embedder.py` EXCEPT AS A REQUEST HEADER. Messages name the
variable and the path, never the contents (the rule `settings.read_secret` already keeps).
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import urlsplit

from settings import SettingsError

__all__ = ["enabled", "Config", "load_config"]

SWITCH = "ATLAS_RETRIEVAL"
SCOPES = ("shared", "all")
AUTH_HEADERS = ("bearer", "api-key", "entra")


def enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Is the add-on switched on? Raises `SettingsError` on a value that is neither on nor off.

    The one function `main.py` calls with the add-on off, so it imports nothing beyond this
    module and reads one variable."""
    env = os.environ if environ is None else environ
    raw = env.get(SWITCH, "").strip().lower()
    if raw in ("", "off"):
        return False
    if raw == "on":
        return True
    raise SettingsError(f"{SWITCH}={raw!r} is neither 'on' nor 'off' (unset means off).",
                        variable=SWITCH)


@dataclass(frozen=True)
class Config:
    url: str
    model: str
    dim: int
    key_file: Optional[str]
    auth_header: str
    ca_bundle: Optional[str]
    doc_prefix: str
    query_prefix: str
    scope: str
    batch: int
    interval_s: float
    #: Why the add-on cannot run with this configuration, or None. Never contains a secret.
    problem: Optional[str]

    @property
    def endpoint_host(self) -> str:
        return urlsplit(self.url).hostname or "?"


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _int(env: Mapping[str, str], name: str, default: int, problems: list[str]) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        problems.append(f"{name}={raw!r} is not a whole number")
        return default
    if value <= 0:
        problems.append(f"{name} must be positive")
        return default
    return value


def load_config(environ: Optional[Mapping[str, str]] = None) -> Config:
    """Read the add-on's configuration. Never raises for a configuration problem: the first
    one found is `Config.problem`, which the caller reports as `unavailable: <problem>`."""
    env = os.environ if environ is None else environ
    problems: list[str] = []

    url = env.get("ATLAS_EMBED_URL", "").strip()
    if not url:
        problems.append("ATLAS_EMBED_URL is not set")
    else:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            problems.append("ATLAS_EMBED_URL is not an http(s) URL")
        elif parts.scheme == "http" and not _loopback(parts.hostname):
            # Owner decision, 2026-10-25: TLS to the embedder even inside one host. Plain http is
            # for a model on the same machine as atlas-api, and nothing else.
            problems.append("ATLAS_EMBED_URL is plain http:// to a host that is not loopback; "
                            "use https://")

    model = env.get("ATLAS_EMBED_MODEL", "").strip()
    if not model:
        problems.append("ATLAS_EMBED_MODEL is not set")
    dim_raw = env.get("ATLAS_EMBED_DIM", "").strip()
    dim = _int(env, "ATLAS_EMBED_DIM", 0, problems)
    if not dim_raw:
        problems.append("ATLAS_EMBED_DIM is not set")
    elif dim > 16000:
        # pgvector's `vector` type stores at most 16,000 dimensions.
        problems.append("ATLAS_EMBED_DIM is above pgvector's limit of 16000")

    if env.get("ATLAS_EMBED_KEY", "").strip():
        problems.append("ATLAS_EMBED_KEY is set as a variable; the key is read only from the "
                        "file named by ATLAS_EMBED_KEY_FILE")
    key_file = env.get("ATLAS_EMBED_KEY_FILE", "").strip() or None

    auth_header = env.get("ATLAS_EMBED_AUTH_HEADER", "").strip().lower() or "bearer"
    if auth_header not in AUTH_HEADERS:
        problems.append(f"ATLAS_EMBED_AUTH_HEADER={auth_header!r} is not one of "
                        f"{', '.join(AUTH_HEADERS)}")
    elif auth_header == "entra" and key_file:
        # Two credentials for one call is a configuration that cannot say which one it means.
        problems.append("ATLAS_EMBED_AUTH_HEADER=entra authenticates with a token; "
                        "ATLAS_EMBED_KEY_FILE must not be set as well")

    ca_bundle = env.get("ATLAS_EMBED_CA_BUNDLE", "").strip() or None
    if ca_bundle and not os.path.isfile(ca_bundle):
        problems.append(f"ATLAS_EMBED_CA_BUNDLE points at {ca_bundle!r}, which is not a file")

    scope = env.get("ATLAS_EMBED_SCOPE", "").strip().lower() or "shared"
    if scope not in SCOPES:
        problems.append(f"ATLAS_EMBED_SCOPE={scope!r} is not one of {', '.join(SCOPES)}")
        scope = "shared"  # fail closed: never widen what is sent because of a typo

    return Config(
        url=url, model=model, dim=dim, key_file=key_file, auth_header=auth_header,
        ca_bundle=ca_bundle,
        # Prefixes are taken verbatim: a trailing space is part of nomic's convention.
        doc_prefix=env.get("ATLAS_EMBED_DOC_PREFIX", ""),
        query_prefix=env.get("ATLAS_EMBED_QUERY_PREFIX", ""),
        scope=scope,
        batch=_int(env, "ATLAS_EMBED_BATCH", 32, problems),
        interval_s=float(_int(env, "ATLAS_RETRIEVAL_INTERVAL_S", 60, problems)),
        problem=problems[0] if problems else None,
    )
