"""TLS policy for atlas-mcp's outbound hop to atlas-api (OSS-24).

Every request this service sends to atlas-api carries a caller's Bearer token, so
that hop is HTTPS with certificate verification ON. Two rules, both checked at
IMPORT time so a misconfiguration is a startup failure naming the variable:

1. `ATLAS_BASE_URL` must be `https://`. The ONE exception is a loopback host
   (`localhost`, `127.0.0.0/8`, `::1`): a token that never leaves the machine's
   own network stack is not on any wire, and local development needs it. A
   plain-`http://` URL to anything else exits; there is no flag to allow it.

2. Which certificate authorities are trusted:
     - `ATLAS_CA_BUNDLE` unset (the default): the SYSTEM trust store, i.e.
       OpenSSL's default verify paths (`/etc/ssl/certs` in the container, so a
       CA added to the image's store is honoured). Publicly-issued certificates,
       such as the homelab's Let's Encrypt ones from caddy-lan, verify here.
     - `ATLAS_CA_BUNDLE=/path/ca.pem`: ONLY the CA certificates in that PEM file.
       This is where an enterprise plugs in its internal PKI root (and any
       intermediates), or the homelab plugs in Caddy's internal CA root. The file
       must exist and be readable at startup; a wrong path is not silently
       replaced by the system store.

3. Optionally, a CLIENT certificate (the mTLS leg, off by default):
   `ATLAS_MCP_CLIENT_CERT_FILE` + `ATLAS_MCP_CLIENT_KEY_FILE` (app/settings.py)
   are loaded into the same context, so atlas-mcp presents them when the TLS
   front in front of atlas-api requires one. Both unset: no client certificate.

There is no way to turn verification off: a client built without verification
is refused in review and by tools/ci/check_tls.sh (rule R4); a certificate that does not verify is a
configuration fault to fix (wrong name, missing CA), not a check to skip.
"""

from __future__ import annotations

import ipaddress
import os
import ssl
import sys
from typing import Optional
from urllib.parse import urlsplit

#: Hostnames that resolve to this machine's own network stack, and nothing else.
_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


def is_loopback_host(host: Optional[str]) -> bool:
    """True for localhost and for any IPv4 127.0.0.0/8 or IPv6 ::1 literal."""
    if not host:
        return False
    host = host.strip("[]").lower()
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_base_url(url: str, name: str = "ATLAS_BASE_URL") -> str:
    """Return `url` unchanged when it is HTTPS, or plain HTTP to loopback; exit otherwise.

    The message names the variable and the rule. It never needs to echo a secret:
    a base URL is configuration, not a credential.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme == "https" and parts.hostname:
        return url
    if scheme == "http" and is_loopback_host(parts.hostname):
        return url
    sys.exit(
        f"{name}={url!r} is refused: every request to atlas-api carries a caller's "
        "Bearer token, so it must be https:// (certificate verified). Plain http:// "
        "is accepted only to a loopback host (localhost, 127.0.0.1, ::1). See "
        "mcp/README.md, 'TLS'."
    )


def ca_bundle_path() -> Optional[str]:
    """The configured CA bundle path, or None for the system trust store. Exits on a bad path."""
    path = os.environ.get("ATLAS_CA_BUNDLE", "").strip()
    if not path:
        return None
    if not os.path.isfile(path) or not os.access(path, os.R_OK):
        sys.exit(
            f"ATLAS_CA_BUNDLE={path!r} is not a readable file. Point it at a PEM file "
            "of CA certificates, or unset it to use the system trust store."
        )
    return path


def ssl_context() -> ssl.SSLContext:
    """A verifying client context: hostname checked, certificate required, TLS 1.2+.

    With ATLAS_CA_BUNDLE, the bundle REPLACES the system store (the narrower trust
    an enterprise usually wants). Without it, the system store is loaded.
    """
    cafile = ca_bundle_path()
    try:
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile)
    except ssl.SSLError as e:
        sys.exit(f"ATLAS_CA_BUNDLE={cafile!r} could not be loaded as PEM CA certificates: {e}")
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    # The optional client certificate (OSS-24 mTLS leg). Read here, at context build time,
    # with the same validation settings.py ran at import.
    from .settings import ClientCertError, load_client_cert
    try:
        pair = load_client_cert()
    except ClientCertError as e:
        sys.exit(f"atlas-mcp refuses to start: {e}")
    if pair is not None:
        try:
            ctx.load_cert_chain(certfile=pair[0], keyfile=pair[1])
        except (ssl.SSLError, OSError) as e:
            # Names the files, never their contents (OpenSSL's message carries no key material).
            sys.exit(f"ATLAS_MCP_CLIENT_CERT_FILE / ATLAS_MCP_CLIENT_KEY_FILE ({pair[0]!r}, "
                     f"{pair[1]!r}) could not be loaded as a PEM certificate and its key: "
                     f"{type(e).__name__}")
    # create_default_context already sets both; asserted so a future refactor
    # cannot quietly weaken them.
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    return ctx
