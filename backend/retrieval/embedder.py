"""
The embedder client: one OpenAI-compatible `POST …/embeddings`, configured entirely by env
(`retrieval.Config`). ADR 0044 §2: the product names no provider, so nothing here knows what
a model proxy, Ollama or Azure is — only the request shape they share.

THE KEY. Read from `Config.key_file` on EVERY call, never cached and never held in an env var,
so rotating it (TASK-1374) needs no restart, and a key rotated to an unreadable file fails the
next call instead of carrying on with the old one. The rules are `settings.read_secret`'s:
surrounding whitespace is stripped, an empty file is missing, and an error names the path,
never the contents. The value goes into one request header and nowhere else: not into an
exception, a log line or the status route. `EmbedError` messages carry the endpoint's HOST, an
HTTP status and at most 200 characters of the response body, none of which is the key.

TLS. httpx verifies against the system trust store, or `Config.ca_bundle` when set. There is
no switch that turns verification off.

ENTRA (`ATLAS_EMBED_AUTH_HEADER=entra`): no key file; `retrieval/entra.py` supplies a cached
Entra ID token for `Authorization: Bearer`, with the same never-printed rule.
"""
from __future__ import annotations

import math
from typing import Sequence
from urllib.parse import urlsplit, urlunsplit

import httpx

from retrieval import Config

TIMEOUT_S = 120.0  # one batch of 32 chunks takes ~6 s on a small consumer GPU; this is the ceiling


class EmbedError(RuntimeError):
    """The endpoint could not be reached, refused the call, or answered with the wrong shape.
    The message is safe to show and to store as the reconciler's last error."""


def embeddings_url(base: str) -> str:
    """`…/v1` gets `/embeddings` appended; a URL whose path already ends in `/embeddings` is
    used as given, query string included (Azure's `?api-version=`)."""
    parts = urlsplit(base)
    path = parts.path.rstrip("/")
    if not path.endswith("/embeddings"):
        path += "/embeddings"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _read_key(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            value = fh.read().strip()
    except OSError as exc:
        raise EmbedError(f"ATLAS_EMBED_KEY_FILE points at {path!r}, which cannot be read "
                         f"({type(exc).__name__})") from None
    except UnicodeDecodeError:
        raise EmbedError(f"ATLAS_EMBED_KEY_FILE points at {path!r}, which is not UTF-8 text") from None
    if not value:
        raise EmbedError(f"ATLAS_EMBED_KEY_FILE points at {path!r}, which is empty")
    return value


def _headers(cfg: Config) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if cfg.auth_header == "entra":
        from retrieval import entra  # only an Entra install pays for the import
        try:
            headers["Authorization"] = f"Bearer {entra.token()}"
        except entra.EntraError as exc:
            raise EmbedError(str(exc)) from None
    elif cfg.key_file:
        key = _read_key(cfg.key_file)
        if cfg.auth_header == "api-key":
            headers["api-key"] = key
        else:
            headers["Authorization"] = f"Bearer {key}"
    return headers


def embed(cfg: Config, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
    """Embed `texts` in ONE call, with the document (or query) prefix. Returns one vector per
    text, in order, each exactly `cfg.dim` long. Raises `EmbedError`; nothing else escapes."""
    if not texts:
        return []
    prefix = cfg.query_prefix if query else cfg.doc_prefix
    payload = {"model": cfg.model, "input": [prefix + t for t in texts]}
    host = cfg.endpoint_host
    try:
        headers = _headers(cfg)
        with httpx.Client(verify=cfg.ca_bundle or True, timeout=TIMEOUT_S) as client:
            resp = client.post(embeddings_url(cfg.url), json=payload, headers=headers)
    except EmbedError:
        raise
    except httpx.HTTPError as exc:
        raise EmbedError(f"embedder at {host} unreachable ({type(exc).__name__})") from None
    if resp.status_code != 200:
        # A model proxy without a database can answer a WRONG KEY with 400 "No connected db", not 401;
        # the body excerpt is what tells the two apart.
        raise EmbedError(f"embedder at {host} answered {resp.status_code}: {resp.text[:200]!r}")
    try:
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        vectors = [[float(x) for x in d["embedding"]] for d in data]
    except (ValueError, KeyError, TypeError):
        raise EmbedError(f"embedder at {host} answered 200 without an embeddings list") from None
    if len(vectors) != len(texts):
        raise EmbedError(f"embedder at {host} returned {len(vectors)} vectors for {len(texts)} texts")
    for v in vectors:
        if len(v) != cfg.dim:
            raise EmbedError(f"embedder at {host} returned {len(v)} dimensions; "
                             f"ATLAS_EMBED_DIM is {cfg.dim}")
        if not all(math.isfinite(x) for x in v):
            raise EmbedError(f"embedder at {host} returned a non-finite value")
    return vectors


def vector_literal(v: Sequence[float]) -> str:
    """pgvector's text form, `[0.1,0.2,…]`, cast with `::vector` in SQL. No client library is
    needed for it, so the add-on adds no dependency."""
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"
