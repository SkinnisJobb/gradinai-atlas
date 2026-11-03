"""The `retrieval` profile's one-shot (deploy/oss/docker-compose.yml): configuration wiring only.

The retrieval add-on (ADR 0044) runs inside atlas-api (backend/retrieval/). This reads the
settings it reads and reports what it sees, before the stack is started with it:

  ATLAS_RETRIEVAL      off | on (default off)
  ATLAS_EMBED_URL      the OpenAI-compatible embeddings endpoint (Azure AI Foundry, a model on
                       another node over TLS)
  ATLAS_EMBED_MODEL, ATLAS_EMBED_DIM, ATLAS_EMBED_SCOPE, ATLAS_EMBED_DOC_PREFIX / _QUERY_PREFIX
  ATLAS_EMBED_KEY_FILE a PATH to the key; the key is never a variable and is never read here

It never contacts the endpoint and never opens the key file. It exits 1 on a configuration
that could not be right (an unknown ATLAS_RETRIEVAL, a plain-http:// endpoint to anything but
loopback, a key given as a variable), and 0 otherwise.
"""

import ipaddress
import os
import sys
from urllib.parse import urlsplit


def _loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def main() -> int:
    bad = []
    mode = os.environ.get("ATLAS_RETRIEVAL", "").strip().lower() or "off"
    if mode not in ("off", "on"):
        bad.append(f"ATLAS_RETRIEVAL={mode!r} is not off or on")
    url = os.environ.get("ATLAS_EMBED_URL", "").strip()
    if url:
        parts = urlsplit(url)
        if parts.scheme == "http" and not _loopback(parts.hostname or ""):
            bad.append("ATLAS_EMBED_URL is plain http:// to a non-loopback host; use https://")
        elif parts.scheme not in ("http", "https") or not parts.hostname:
            bad.append("ATLAS_EMBED_URL is not an http(s) URL")
    if os.environ.get("ATLAS_EMBED_KEY", "").strip():
        bad.append("ATLAS_EMBED_KEY is set as a variable; the key is a file (ATLAS_EMBED_KEY_FILE)")

    print("atlas-retrieval: the add-on runs inside atlas-api when ATLAS_RETRIEVAL=on and the stack is "
          "started with the overlay retrieval.yml; /api/health reports its state.")
    print(f"atlas-retrieval: ATLAS_RETRIEVAL={mode}, endpoint "
          f"{'host ' + (urlsplit(url).hostname or '?') if url else 'not set'}, "
          f"model {os.environ.get('ATLAS_EMBED_MODEL', '').strip() or 'not set'}, "
          f"key file {'set' if os.environ.get('ATLAS_EMBED_KEY_FILE', '').strip() else 'not set'}")
    for b in bad:
        print(f"atlas-retrieval: REFUSED: {b}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
