"""Server certificate expiry, for `/api/health` (TASK-1419, OSS-26 F).

WHY. `scripts/init-certs.sh` issues the install's server certificate for 397 days, and
nothing said anything before it ran out: the first sign was atlas-mcp failing its TLS
check. This turns that into a warning in `/api/health`, shaped like `token_warnings`
(a list a human or a script reads; empty is the good case), because expiry that is
surfaced is safe and expiry that is silent is an outage.

WHICH CERTIFICATE. The one `ATLAS_CERT_EXPIRY_FILE` names (settings.load_cert_expiry).
atlas-api itself terminates no TLS: in the OSS install the TLS front is atlas-tls
(Caddy), and the OSS compose file mounts the same public `server.crt` into atlas-api
read-only so this module can read it. A file holding a chain is read for its FIRST
certificate, the leaf. Unset (the homelab) means this module is never called and the
`cert_warnings` field is absent.

THE ENTRIES. One per problem, each `{"file", "reasons", ...}`:

  expires_in_days=N    the certificate expires within the warn window (N >= 0)
  expired              its notAfter is in the past
  unreadable           the configured file cannot be opened (a warning, never a 500)
  not_a_certificate    the file holds no PEM certificate

`subject` and `not_after` (ISO 8601, UTC) are added whenever the certificate parsed.

The file is read on every call, so a renewed certificate clears the warning without a
restart. It is a public certificate; nothing here ever reads a key.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import settings


def _leaf(path: str):
    """The first certificate in the PEM file at `path`, or a reason string."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return "unreadable"
    from cryptography import x509  # local: only an install that configured a file pays for it
    try:
        certs = x509.load_pem_x509_certificates(data)
    except ValueError:
        return "not_a_certificate"
    return certs[0] if certs else "not_a_certificate"


def warnings(cfg: Optional[settings.CertExpiry] = None,
             now: Optional[datetime] = None) -> list[dict]:
    """The certificate warnings for `cfg` (default: the environment's) at `now` (default: now).

    Empty when the certificate is valid for longer than `cfg.warn_days`, or when no
    certificate is configured (the caller leaves the field out in that case)."""
    cfg = cfg if cfg is not None else settings.load_cert_expiry()
    if not cfg.enabled:
        return []
    now = now or datetime.now(timezone.utc)
    cert = _leaf(cfg.file)
    if isinstance(cert, str):
        return [{"file": cfg.file, "reasons": [cert]}]
    not_after = cert.not_valid_after_utc
    left = not_after - now
    if left.total_seconds() <= 0:
        reasons = ["expired"]
    elif left <= timedelta(days=cfg.warn_days):  # the rule auth.token_warnings uses
        reasons = ["expires_in_days=%d" % left.days]
    else:
        return []
    return [{
        "file": cfg.file,
        "subject": cert.subject.rfc4514_string(),
        "not_after": not_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reasons": reasons,
    }]
