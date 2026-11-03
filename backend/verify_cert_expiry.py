"""verify_cert_expiry.py: /api/health warns before the install's server certificate expires
(TASK-1419, OSS-26 F).

  A. THE SETTINGS. `settings.load_cert_expiry`: unset is no file and 30 days; a path and a
     day count are read; set-but-blank, a non-number and an out-of-range count raise
     SettingsError naming the variable.
  B. THE RULE. `cert_expiry.warnings` over real certificates made here: far from expiry is
     no warning, inside the window is `expires_in_days=N`, past is `expired`, a chain is read
     for its leaf, an unreadable file and a file with no certificate are warnings (never an
     exception), and the window follows ATLAS_CERT_EXPIRY_WARN_DAYS.
  C. THROUGH THE REAL GATE. A signed-in owner's `/api/health`: with ATLAS_CERT_EXPIRY_FILE
     unset (the homelab) there is no `cert_warnings` field at all; with a certificate far
     from expiry the field is `[]` and nothing else changed; near expiry it carries the
     warning.

    python verify_cert_expiry.py
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

import settings  # noqa: E402
import cert_expiry  # noqa: E402

FILE_ENV, DAYS_ENV = "ATLAS_CERT_EXPIRY_FILE", "ATLAS_CERT_EXPIRY_WARN_DAYS"
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def raises(env: dict, *needles: str) -> tuple[bool, str]:
    try:
        settings.load_cert_expiry(env)
    except settings.SettingsError as exc:
        return all(n in str(exc) for n in needles), str(exc)
    return False, "nothing raised"


NOW = datetime.now(timezone.utc)
TMP = Path(tempfile.mkdtemp(prefix="verify-cert-expiry-"))


def make_cert(name: str, not_after: datetime, cn: str = "atlas-api") -> x509.Certificate:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(min(NOW, not_after) - timedelta(days=400))
            .not_valid_after(not_after).sign(key, hashes.SHA256()))
    (TMP / name).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert


def cfg(name: str, days: int = settings.DEFAULT_CERT_EXPIRY_WARN_DAYS) -> settings.CertExpiry:
    return settings.CertExpiry(file=str(TMP / name), warn_days=days)


# --------------------------------------------------------------------------- #
# A. the settings
# --------------------------------------------------------------------------- #
print("\n-- A. settings.load_cert_expiry --")
c = settings.load_cert_expiry({})
check("unset: no certificate, the default window", c.file is None and not c.enabled
      and c.warn_days == settings.DEFAULT_CERT_EXPIRY_WARN_DAYS, str(c))
check("the default window is 30 days", settings.DEFAULT_CERT_EXPIRY_WARN_DAYS == 30)
c = settings.load_cert_expiry({FILE_ENV: " /run/secrets/tls_cert ", DAYS_ENV: " 45 "})
check("a path and a day count are read, stripped", c.enabled and c.file == "/run/secrets/tls_cert"
      and c.warn_days == 45, str(c))
for label, env, needles in (
        ("a blank file", {FILE_ENV: "  "}, (FILE_ENV, "empty")),
        ("blank days", {DAYS_ENV: ""}, (DAYS_ENV, "empty")),
        ("days not a number", {DAYS_ENV: "thirty"}, (DAYS_ENV, "whole number")),
        ("zero days", {DAYS_ENV: "0"}, (DAYS_ENV, "between 1 and")),
        ("too many days", {DAYS_ENV: "3651"}, (DAYS_ENV, "between 1 and"))):
    ok, msg = raises(env, *needles)
    check(f"{label} raises SettingsError naming the variable", ok, msg)
ok, msg = raises({DAYS_ENV: "3650"})
check("(perturbation) 3650 days is accepted", not ok and msg == "nothing raised", msg)

# --------------------------------------------------------------------------- #
# B. the rule
# --------------------------------------------------------------------------- #
print("\n-- B. cert_expiry.warnings --")
far = make_cert("far.crt", NOW + timedelta(days=200))
near = make_cert("near.crt", NOW + timedelta(days=10, hours=1))
make_cert("expired.crt", NOW - timedelta(days=1))
check("no certificate configured: no warnings",
      cert_expiry.warnings(settings.CertExpiry(), NOW) == [])
check("far from expiry (200 days): no warning", cert_expiry.warnings(cfg("far.crt"), NOW) == [])

w = cert_expiry.warnings(cfg("near.crt"), NOW)
check("near expiry (10 days): one warning, expires_in_days=10",
      len(w) == 1 and w[0]["reasons"] == ["expires_in_days=10"], str(w))
check("the warning names the file, the subject and notAfter (UTC)",
      w and w[0]["file"] == str(TMP / "near.crt") and w[0]["subject"] == "CN=atlas-api"
      and w[0]["not_after"] == near.not_valid_after_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), str(w))
check("the window is the setting: 5 days does not warn for a 10-day certificate",
      cert_expiry.warnings(cfg("near.crt", 5), NOW) == [])
# notAfter is stored to the second, so the 200-day certificate has 199 days and change left.
wide = [x["reasons"] for x in cert_expiry.warnings(cfg("far.crt", 250), NOW)]
check("(perturbation) a 250-day window warns for the 200-day certificate",
      wide in ([["expires_in_days=199"]], [["expires_in_days=200"]]), str(wide))
w = cert_expiry.warnings(cfg("expired.crt"), NOW)
check("past notAfter: expired", [x["reasons"] for x in w] == [["expired"]], str(w))

(TMP / "chain.crt").write_bytes((TMP / "near.crt").read_bytes() + (TMP / "far.crt").read_bytes())
w = cert_expiry.warnings(cfg("chain.crt"), NOW)
check("a chain is judged by its first certificate (the leaf)",
      [x["reasons"] for x in w] == [["expires_in_days=10"]], str(w))
check("an unreadable file is a warning, not an exception",
      cert_expiry.warnings(cfg("missing.crt"), NOW) == [{"file": str(TMP / "missing.crt"),
                                                        "reasons": ["unreadable"]}])
(TMP / "junk.crt").write_text("not a certificate\n", encoding="utf-8")
check("a file with no certificate is a warning, not an exception",
      [x["reasons"] for x in cert_expiry.warnings(cfg("junk.crt"), NOW)] == [["not_a_certificate"]])

# --------------------------------------------------------------------------- #
# C. through the real gate
# --------------------------------------------------------------------------- #
print("\n-- C. /api/health through the real gate --")
import testdb  # noqa: E402

DB = testdb.start()
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.pop(FILE_ENV, None)
os.environ.pop(DAYS_ENV, None)
os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")

from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthUser  # noqa: E402

ORIGIN = "https://testserver"
PW = "Correct-Horse-Battery-Certs-7!"


def main() -> int:
    app.startup()
    with app.session() as s:
        s.add(AuthUser(id="u-ce-owner", subject="ce-owner", name="ce-owner", role="owner",
                       is_service=False, must_change_password=False,
                       password_hash=passwords.hash_password(PW),
                       created_at=datetime.now(timezone.utc)))
        s.commit()
    c = TestClient(app.app, base_url=ORIGIN)
    tok = c.get("/auth/login").text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": "ce-owner", "password": PW, "csrf_token": tok},
           headers={"Origin": ORIGIN}, follow_redirects=False)

    def health() -> dict:
        r = c.get("/api/health")
        check("/api/health answers 200", r.status_code == 200, r.text[:200])
        return r.json() if r.status_code == 200 else {}

    # 1. No certificate configured: the homelab. No field at all.
    base = health()
    check("no certificate: there is no `cert_warnings` field", "cert_warnings" not in base, str(sorted(base)))
    check("no certificate: the other fields are all there",
          {"ok", "node_count", "edge_count", "event_count", "token_warnings"} <= set(base), str(sorted(base)))

    # 2. A certificate far from expiry: the field is an empty list, and nothing else changed.
    os.environ[FILE_ENV] = str(TMP / "far.crt")
    body = health()
    check("far from expiry: `cert_warnings` is []", body.get("cert_warnings") == [], str(body.get("cert_warnings")))
    check("far from expiry: exactly one field more than without a certificate",
          set(body) == set(base) | {"cert_warnings"}, str(sorted(set(body) ^ set(base))))

    # 3. Near expiry: the warning, through the route.
    os.environ[FILE_ENV] = str(TMP / "near.crt")
    body = health()
    w = body.get("cert_warnings") or []
    check("near expiry: `cert_warnings` carries expires_in_days=10",
          [x.get("reasons") for x in w] == [["expires_in_days=10"]], str(w))
    os.environ[DAYS_ENV] = "5"
    body = health()
    check("near expiry with ATLAS_CERT_EXPIRY_WARN_DAYS=5: no warning yet",
          body.get("cert_warnings") == [], str(body.get("cert_warnings")))

    # 4. Back to unset: the field goes again (it is read per call).
    os.environ.pop(FILE_ENV, None)
    os.environ.pop(DAYS_ENV, None)
    body = health()
    check("unset again: no `cert_warnings` field, the same keys as before",
          set(body) == set(base), str(sorted(set(body) ^ set(base))))

    print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAIL'}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        DB.stop()
    sys.exit(rc)
