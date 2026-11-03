"""Prove atlas-mcp's client hop to atlas-api against REAL TLS servers and real certificates (OSS-24).

verify_tls.py checks the policy (https only, the bundle replaces the system store). This one
runs the actual client, `app.client.client.get(...)` as a caller, in a fresh process per case,
against a local HTTPS server that stands in for atlas-api's TLS front:

  A. install CA: certificates from scripts/init-certs.sh. With ATLAS_CA_BUNDLE=ca.crt the
     call succeeds and the caller's Bearer token arrives.
  B. a different CA: a server certificate from a SECOND init-certs run. The call FAILS CLOSED
     (`unreachable`, a certificate verify error), and nothing reaches the server's handler.
     The system store does not rescue it either; no code path skips verification.
  C. a simulated enterprise PKI: root -> intermediate -> leaf, the server sending leaf +
     intermediate. ATLAS_CA_BUNDLE = the root alone is honoured; without it (system store)
     the same server is refused.
  D. the mTLS leg: a server that REQUIRES a client certificate from the install's client CA.
     Without ATLAS_MCP_CLIENT_CERT_FILE/KEY_FILE the connection is refused; with init-certs'
     mcp-client pair it is accepted and the server sees CN=atlas-mcp; with a client
     certificate from another CA it is refused.
  E. the client-certificate settings fail at startup when half-set, blank or pointing at a
     missing file, and are absent (no client certificate, today's behaviour) when unset.

Every certificate is generated at run time (openssl, through init-certs.sh); nothing is
committed. Needs bash and openssl on PATH, as CI's ubuntu runner has.

    pip install -r mcp/requirements.txt && python mcp/verify_tls_client.py

One [PASS]/[FAIL] line per claim, exit 1 if any failed (the mcp/verify_*.py convention).
"""

import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
INIT_CERTS = HERE.parent / "scripts" / "init-certs.sh"
TOKEN = "atlas_svc_verifytlsclient_" + "a" * 32

failures: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


# --------------------------------------------------------------------------- #
# Certificates
# --------------------------------------------------------------------------- #
def run(cmd: list, cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        sys.exit(f"setup failed: {cmd[0]} {cmd[1:3]} exited {r.returncode}: {r.stderr[-400:]}")


def init_certs(root: Path, *args: str) -> Path:
    """Run scripts/init-certs.sh in `root`; return root/secrets/tls."""
    root.mkdir(parents=True, exist_ok=True)
    run([shutil.which("bash") or "bash", str(INIT_CERTS), *args], root)
    return root / "secrets" / "tls"


def _cnf(path: Path, cn: str, ext: str) -> None:
    path.write_text(f"[req]\ndistinguished_name = dn\nprompt = no\n[dn]\nCN = {cn}\n[ext]\n{ext}\n",
                    encoding="ascii")


def enterprise_pki(d: Path) -> dict:
    """root -> intermediate -> leaf (SAN localhost), the way an enterprise CA issues one."""
    d.mkdir(parents=True, exist_ok=True)
    ossl = shutil.which("openssl") or "openssl"
    ca_ext = "basicConstraints = critical, CA:TRUE\nkeyUsage = critical, keyCertSign, cRLSign"
    leaf_ext = ("basicConstraints = critical, CA:FALSE\nkeyUsage = critical, digitalSignature\n"
                "extendedKeyUsage = serverAuth\nsubjectAltName = DNS:localhost, IP:127.0.0.1")
    for name in ("root", "inter", "leaf"):
        run([ossl, "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256",
             "-out", f"{name}.key"], d)
    _cnf(d / "root.cnf", "Example Enterprise Root CA", ca_ext)
    run([ossl, "req", "-new", "-x509", "-key", "root.key", "-config", "root.cnf", "-extensions", "ext",
         "-days", "30", "-out", "root.crt"], d)
    for name, cn, ext, issuer in (("inter", "Example Enterprise Issuing CA", ca_ext, "root"),
                                  ("leaf", "atlas-api.example.internal", leaf_ext, "inter")):
        _cnf(d / f"{name}.cnf", cn, ext)
        run([ossl, "req", "-new", "-key", f"{name}.key", "-config", f"{name}.cnf", "-out", f"{name}.csr"], d)
        run([ossl, "x509", "-req", "-in", f"{name}.csr", "-CA", f"{issuer}.crt", "-CAkey", f"{issuer}.key",
             "-CAcreateserial", "-days", "30", "-extfile", f"{name}.cnf", "-extensions", "ext",
             "-out", f"{name}.crt"], d)
    (d / "chain.crt").write_text((d / "leaf.crt").read_text() + (d / "inter.crt").read_text())
    return {"root": d / "root.crt", "chain": d / "chain.crt", "key": d / "leaf.key"}


# --------------------------------------------------------------------------- #
# A stand-in for atlas-api's TLS front
# --------------------------------------------------------------------------- #
class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server's name)
        peer = self.connection.getpeercert() or {}
        cn = dict(x[0] for x in peer.get("subject", ())).get("commonName")
        self.server.hits += 1
        body = json.dumps({
            "status": "ok",
            "bearer_seen": self.headers.get("Authorization") == "Bearer " + TOKEN,
            "client_cn": cn,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the CI log to the [PASS]/[FAIL] lines
        pass


class TLSServer:
    def __init__(self, cert: Path, key: Path, client_ca: Path = None):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        if client_ca is not None:
            ctx.verify_mode = ssl.CERT_REQUIRED
            ctx.load_verify_locations(cafile=str(client_ca))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.hits = 0
        self.httpd.handle_error = lambda *a: None   # a refused handshake is the expected case
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.url = f"https://localhost:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def hits(self) -> int:
        return self.httpd.hits

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# --------------------------------------------------------------------------- #
# The real client, in a fresh process (its SSL context is built once, at import)
# --------------------------------------------------------------------------- #
_PROBE = (
    "import json\n"
    "from app import client as c\n"
    "from app.envelope import AtlasError\n"
    f"with c.as_caller({TOKEN!r}):\n"
    "    try:\n"
    "        print('RESULT ' + json.dumps({'ok': True, 'body': c.client.get('/api/health')}))\n"
    "    except AtlasError as e:\n"
    "        print('RESULT ' + json.dumps({'ok': False, 'type': e.error_type, 'msg': str(e)}))\n"
)

_CLEAN = ("ATLAS_CA_BUNDLE", "ATLAS_MCP_CLIENT_CERT_FILE", "ATLAS_MCP_CLIENT_KEY_FILE",
          "ATLAS_TOKEN", "ATLAS_TOKEN_FILE", "ATLAS_MCP_ALLOW_SHARED_TOKEN", "SSL_CERT_FILE", "SSL_CERT_DIR")


def probe(base_url: str, **env_extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _CLEAN}
    env["ATLAS_BASE_URL"] = base_url
    env.update({k: str(v) for k, v in env_extra.items()})
    r = subprocess.run([sys.executable, "-c", _PROBE], cwd=str(HERE), env=env,
                       capture_output=True, text=True, timeout=60)
    for line in r.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    return {"ok": False, "type": "exit", "code": r.returncode, "msg": r.stderr[-300:]}


def startup(**env_extra: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in _CLEAN}
    env["ATLAS_BASE_URL"] = "https://atlas.invalid"
    env.update({k: str(v) for k, v in env_extra.items()})
    return subprocess.run([sys.executable, "-c", "import app.client"], cwd=str(HERE), env=env,
                          capture_output=True, text=True, timeout=60)


def _verify_failure(res: dict) -> bool:
    msg = res.get("msg", "").lower()
    return res.get("type") == "unreachable" and "certificate" in msg and "verify" in msg


def _refused(res: dict) -> bool:
    return not res.get("ok") and res.get("type") == "unreachable"


# --------------------------------------------------------------------------- #
def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pki = init_certs(tmp / "install", "--host", "atlas.example.test")
        other = init_certs(tmp / "other")
        ent = enterprise_pki(tmp / "enterprise")

        print("-- A. the install CA (init-certs.sh) --")
        srv = TLSServer(pki / "server.crt", pki / "server.key")
        res = probe(srv.url, ATLAS_CA_BUNDLE=pki / "ca.crt")
        check("with ATLAS_CA_BUNDLE=ca.crt, a call to the API's TLS front succeeds",
              res.get("ok") is True, json.dumps(res)[:300])
        check("... and the caller's Bearer token arrived over TLS",
              bool(res.get("body", {}).get("bearer_seen")), json.dumps(res)[:300])
        srv.close()

        print("-- B. a certificate from a different CA: fail closed --")
        srv = TLSServer(other / "server.crt", other / "server.key")
        res = probe(srv.url, ATLAS_CA_BUNDLE=pki / "ca.crt")
        check("another CA's server certificate is refused (certificate verify failed)",
              not res.get("ok") and _verify_failure(res), json.dumps(res)[:300])
        res = probe(srv.url)
        check("... and the system trust store does not accept it either",
              not res.get("ok") and _verify_failure(res), json.dumps(res)[:300])
        check("no request reached the server's handler (no data was sent past a failed check)",
              srv.hits == 0, f"hits={srv.hits}")
        srv.close()

        print("-- C. a simulated enterprise PKI (root -> intermediate -> leaf) --")
        srv = TLSServer(ent["chain"], ent["key"])
        res = probe(srv.url, ATLAS_CA_BUNDLE=ent["root"])
        check("ATLAS_CA_BUNDLE = the enterprise root alone is honoured (chain via the intermediate)",
              res.get("ok") is True, json.dumps(res)[:300])
        res = probe(srv.url)
        check("without the bundle (system store) the enterprise-issued server is refused",
              not res.get("ok") and _verify_failure(res), json.dumps(res)[:300])
        res = probe(srv.url, ATLAS_CA_BUNDLE=pki / "ca.crt")
        check("a bundle of the WRONG CA is not rescued by the system store",
              not res.get("ok") and _verify_failure(res), json.dumps(res)[:300])
        srv.close()

        print("-- D. the mTLS leg: the front requires a client certificate --")
        srv = TLSServer(pki / "server.crt", pki / "server.key", client_ca=pki / "client-ca.crt")
        res = probe(srv.url, ATLAS_CA_BUNDLE=pki / "ca.crt")
        check("without a client certificate the connection is refused", _refused(res),
              json.dumps(res)[:300])
        res = probe(srv.url, ATLAS_CA_BUNDLE=pki / "ca.crt",
                    ATLAS_MCP_CLIENT_CERT_FILE=pki / "mcp-client.crt",
                    ATLAS_MCP_CLIENT_KEY_FILE=pki / "mcp-client.key")
        check("with init-certs' mcp-client certificate it is accepted", res.get("ok") is True,
              json.dumps(res)[:300])
        check("... the server saw the client certificate (CN=atlas-mcp) and the Bearer token",
              res.get("body", {}).get("client_cn") == "atlas-mcp"
              and bool(res.get("body", {}).get("bearer_seen")), json.dumps(res)[:300])
        res = probe(srv.url, ATLAS_CA_BUNDLE=pki / "ca.crt",
                    ATLAS_MCP_CLIENT_CERT_FILE=other / "mcp-client.crt",
                    ATLAS_MCP_CLIENT_KEY_FILE=other / "mcp-client.key")
        check("a client certificate from another CA is refused", _refused(res), json.dumps(res)[:300])
        srv.close()

        print("-- E. client certificate settings at startup --")
        r = startup()
        check("unset: atlas-mcp starts with no client certificate (today's behaviour)",
              r.returncode == 0, r.stderr[-200:])
        r = startup(ATLAS_MCP_CLIENT_CERT_FILE=pki / "mcp-client.crt")
        check("cert without key: startup exits naming ATLAS_MCP_CLIENT_KEY_FILE",
              r.returncode != 0 and "ATLAS_MCP_CLIENT_KEY_FILE" in r.stderr, r.stderr[-200:])
        r = startup(ATLAS_MCP_CLIENT_CERT_FILE=pki / "nope.crt", ATLAS_MCP_CLIENT_KEY_FILE=pki / "mcp-client.key")
        check("a missing cert file: startup exits naming ATLAS_MCP_CLIENT_CERT_FILE",
              r.returncode != 0 and "ATLAS_MCP_CLIENT_CERT_FILE" in r.stderr, r.stderr[-200:])
        r = startup(ATLAS_MCP_CLIENT_CERT_FILE=" ", ATLAS_MCP_CLIENT_KEY_FILE=pki / "mcp-client.key")
        check("a blank value: startup exits naming the variable",
              r.returncode != 0 and "ATLAS_MCP_CLIENT_CERT_FILE" in r.stderr, r.stderr[-200:])
        r = startup(ATLAS_MCP_CLIENT_CERT_FILE=pki / "mcp-client.crt", ATLAS_MCP_CLIENT_KEY_FILE=other / "mcp-client.key")
        check("a key that does not match the certificate: startup exits",
              r.returncode != 0 and "ATLAS_MCP_CLIENT_CERT_FILE" in r.stderr, r.stderr[-200:])
        key_text = (pki / "mcp-client.key").read_text()
        body = key_text.split("-----")[2].strip().splitlines()[0]
        check("no key material in any startup error", body not in r.stderr)

    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
