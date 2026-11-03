"""Prove atlas-mcp's hop to atlas-api is HTTPS with verification on, and the CA is configurable (OSS-24).

  A. app/tls.py check_base_url: https:// passes; plain http:// passes ONLY to a
     loopback host; every other scheme or host is a startup exit naming the variable.
  B. ATLAS_CA_BUNDLE: unset means the system store; a missing file is an exit, not a
     silent fallback; a PEM file REPLACES the system store with exactly its CAs.
  C. startup, end to end in a subprocess: importing app.client with a plain-HTTP
     LAN URL or a bad bundle path exits non-zero, and with a bundle the shared
     httpx client and the caller_auth door check both verify against that bundle.

The test CA below is a CERTIFICATE ONLY. Its private key was discarded when it was
made, so nothing can be signed with it; it exists to be loaded, never trusted.

    pip install -r mcp/requirements.txt && python mcp/verify_tls.py

Same convention as the other mcp/verify_*.py: one [PASS]/[FAIL] line per claim,
exit 1 if any failed.
"""

import os
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("ATLAS_BASE_URL", "https://atlas.invalid")
os.environ.pop("ATLAS_CA_BUNDLE", None)
sys.path.insert(0, str(HERE))

from app import tls  # noqa: E402

failures: list = []

TEST_CA_PEM = """-----BEGIN CERTIFICATE-----
MIIBxjCCAWugAwIBAgIUG+PX9I6VzK9R9uj+FHJ9KaDTpjgwCgYIKoZIzj0EAwIw
NzE1MDMGA1UEAwwsYXRsYXMtbWNwIHZlcmlmeV90bHMgdGVzdCBDQSAobm8ga2V5
IGV4aXN0cykwIBcNMjYxMDExMTQyMTEzWhgPMjEyNjA5MTcxNDIxMTNaMDcxNTAz
BgNVBAMMLGF0bGFzLW1jcCB2ZXJpZnlfdGxzIHRlc3QgQ0EgKG5vIGtleSBleGlz
dHMpMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEcK/Saf/2E7ts2WtByj9h7zNH
0lwnm3jUHI5DPE1cICc/URELfU5iTB5LxC4a4PrpVN1X1uYN3PaDIwOPG0NJ4qNT
MFEwHQYDVR0OBBYEFDDiUdq9H8bLTkKXwsc/bGqaEA6uMB8GA1UdIwQYMBaAFDDi
Udq9H8bLTkKXwsc/bGqaEA6uMA8GA1UdEwEB/wQFMAMBAf8wCgYIKoZIzj0EAwID
SQAwRgIhANU0P7KxV5yEJj+8ItdL0pQvCygAtglx4VidMod+Ua4YAiEAie+NUq/f
Ai+Bd0r4LEel/eD/B+daWELYhtIteNS5sjM=
-----END CERTIFICATE-----
"""
TEST_CA_CN = "atlas-mcp verify_tls test CA (no key exists)"


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def exits(fn, *args) -> bool:
    try:
        fn(*args)
    except SystemExit:
        return True
    return False


def section_a() -> None:
    print("-- A. ATLAS_BASE_URL: https, or plain http to loopback only --")
    for url in ("https://atlas.example.com:8443", "https://atlas.example.internal",
                "https://198.51.100.5:8443"):
        check(f"accepted: {url}", not exits(tls.check_base_url, url))
    for url in ("http://localhost:8095", "http://127.0.0.1:8095", "http://127.8.9.1",
                "http://[::1]:8095", "HTTP://LOCALHOST:8095"):
        check(f"accepted (loopback exception): {url}", not exits(tls.check_base_url, url))
    for url in ("http://198.51.100.20:8095", "http://atlas-api:8000",
                "http://host.docker.internal:8095", "http://atlas.example.com:8443",
                "http://localhost.evil.example", "http://0.0.0.0:8095", "ftp://atlas.example",
                "https://", "atlas.example.com:8443"):
        check(f"refused: {url}", exits(tls.check_base_url, url))


def section_b(bundle: str) -> None:
    print("-- B. ATLAS_CA_BUNDLE --")
    os.environ.pop("ATLAS_CA_BUNDLE", None)
    check("unset: no bundle path (system trust store)", tls.ca_bundle_path() is None)
    ctx = tls.ssl_context()
    check("unset: the context verifies certificates and hostnames",
          ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname)
    check("unset: TLS 1.2 is the floor", ctx.minimum_version == ssl.TLSVersion.TLSv1_2)

    os.environ["ATLAS_CA_BUNDLE"] = bundle + ".missing"
    check("a missing bundle file is an exit, not a fallback", exits(tls.ca_bundle_path))
    check("... and ssl_context() exits too", exits(tls.ssl_context))

    os.environ["ATLAS_CA_BUNDLE"] = bundle
    ctx = tls.ssl_context()
    cas = ctx.get_ca_certs()
    cns = [dict(x[0] for x in c.get("subject", ())).get("commonName") for c in cas]
    check("a bundle REPLACES the system store with exactly its CAs",
          cns == [TEST_CA_CN], str(cns[:3]))
    check("with a bundle, verification is still on",
          ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname)
    os.environ.pop("ATLAS_CA_BUNDLE", None)


def _import(env_extra: dict, code: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in ("ATLAS_TOKEN", "ATLAS_MCP_ALLOW_SHARED_TOKEN", "ATLAS_CA_BUNDLE")}
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", code], cwd=str(HERE), env=env,
                          capture_output=True, text=True, timeout=60)


def section_c(bundle: str) -> None:
    print("-- C. startup: app.client refuses a plaintext LAN hop and a bad bundle --")
    r = _import({"ATLAS_BASE_URL": "http://198.51.100.20:8095"}, "import app.client")
    check("ATLAS_BASE_URL=http://<LAN> exits non-zero naming the variable",
          r.returncode != 0 and "ATLAS_BASE_URL" in r.stderr, r.stderr[-200:])
    r = _import({"ATLAS_BASE_URL": "http://localhost:8095"}, "import app.client")
    check("ATLAS_BASE_URL=http://localhost starts (documented loopback exception)",
          r.returncode == 0, r.stderr[-200:])
    r = _import({"ATLAS_BASE_URL": "https://atlas.invalid", "ATLAS_CA_BUNDLE": bundle + ".nope"},
                "import app.client")
    check("ATLAS_CA_BUNDLE pointing nowhere exits naming the variable",
          r.returncode != 0 and "ATLAS_CA_BUNDLE" in r.stderr, r.stderr[-200:])

    # The shared client and the door check both use the SAME verifying context.
    # httpx 0.28 keeps it on the transport's connection pool (pinned in requirements.txt).
    code = (
        "import asyncio, httpx\n"
        "import app.client as c, app.caller_auth as g\n"
        "pool = c.client._client._transport._pool\n"
        "print('SAME', pool._ssl_context is c.SSL_CONTEXT)\n"
        "print('CAS', [dict(x[0] for x in a['subject']).get('commonName') for a in c.SSL_CONTEXT.get_ca_certs()])\n"
        "seen = {}\n"
        "orig = httpx.AsyncClient.__init__\n"
        "def spy(self, *a, **k):\n"
        "    seen['verify'] = k.get('verify')\n"
        "    orig(self, *a, **k)\n"
        "httpx.AsyncClient.__init__ = spy\n"
        "async def run():\n"
        "    try:\n"
        "        await g.verify_with_api('atlas_svc_aaaaaa_' + 'x' * 43)\n"
        "    except Exception:\n"
        "        pass\n"
        "asyncio.run(run())\n"
        "print('GATE', seen.get('verify') is c.SSL_CONTEXT)\n"
    )
    r = _import({"ATLAS_BASE_URL": "https://atlas.invalid", "ATLAS_CA_BUNDLE": bundle}, code)
    out = r.stdout
    check("the shared httpx client verifies with the configured context",
          "SAME True" in out, (r.stderr or out)[-300:])
    check("... which trusts exactly the bundle", f"CAS ['{TEST_CA_CN}']" in out, out[-300:])
    check("caller_auth's door check verifies with the same context",
          "GATE True" in out, (r.stderr or out)[-300:])


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        bundle = str(Path(d) / "ca.pem")
        Path(bundle).write_text(TEST_CA_PEM, encoding="ascii")
        section_a()
        section_b(bundle)
        section_c(bundle)
    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
