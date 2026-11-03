"""A stub OpenAI-compatible embeddings endpoint for the install smoke's retrieval leg (leg 8b).

Test-only. It runs as the container `atlas-embed-stub` (tests/retrieval/docker-compose.stub.yml)
on the stack's `egress` network, the one network the overlay deploy/oss/retrieval.yml gives
atlas-api, so atlas-api reaches it the way an install reaches a real embedding node: HTTPS,
verified against a CA bundle (ATLAS_EMBED_CA_BUNDLE), with the key from a file. It never
calls anything itself, and no real provider is involved anywhere.

Lifted from backend/verify_retrieval.py (`_Handler`, `_vector`, `_start_fake`), which stays the
owner of the add-on's own checks. What is different here, and why:
  * leg 8b uses `bearer`. Leg 8c (OSS-26 A) uses `entra` through workload identity: the stub
    then also answers the token exchange, POST /<tenant>/oauth2/v2.0/token (AZURE_AUTHORITY_HOST
    points at it), checks the client assertion against the federated token file it is handed
    (STUB_FEDERATED_TOKEN_FILE, the same file atlas-api reads) and issues a random access token,
    which /embeddings then accepts as `Authorization: Bearer`. No managed-identity endpoints
    (do_GET, IMDS): they need no mount, and verify_retrieval.py covers them;
  * no throwaway CA (`_make_tls`). The leg is "TLS against the install CA", and init-certs.sh
    discards that CA's key the moment it has signed (by design: nothing can be signed with it
    afterwards). The one leaf it signed for a server is the TLS front's (./secrets/tls/server.crt,
    SANs atlas-api, atlas-mcp, gateway, localhost, the public host). The stub presents that leaf
    and is reached as `gateway`, a SAN that names nothing on the `egress` network (the
    gateway profile's container is `atlas-gateway`, on networks atlas-api does not join);
  * the key is compared with the compose secret /run/secrets/embed_key, the same file atlas-api
    reads through ATLAS_EMBED_KEY_FILE. A wrong or missing key gets the real embedding gateway's 400, as there.

SIGNALS. SIGUSR1 stops the endpoint: the listening socket is closed, so the next call is
refused at once (`embedder at gateway unreachable`), while the container and its name stay, so
nothing falls through to a resolver outside the project. SIGTERM exits.

One log line per request: the path, the model, how many texts, whether the key matched. Never
the key, never a text.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import signal
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

PORT = 8443
CERT = "/certs/tls.crt"
KEY = "/certs/tls.key"
EMBED_KEY = "/run/secrets/embed_key"
DIM = 32
#: Leg 8c only: the federated token atlas-api must present as its client assertion.
FEDERATED = os.environ.get("STUB_FEDERATED_TOKEN_FILE", "")
TOKEN_PATH = re.compile(r"^/([A-Za-z0-9._-]+)/oauth2/v2\.0/token$")
JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
#: Access tokens this stub issued (leg 8c). Never logged.
_issued: set = set()


def _vector(t: str, dim: int) -> list[float]:
    """A deterministic hashed bag of words, unit length (verify_retrieval.py's)."""
    v = [0.0] * dim
    for tok in re.findall(r"\w+", t.lower()):
        h = hashlib.sha256(tok.encode()).digest()
        v[h[0] % dim] += 1.0 if h[1] & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v] if any(v) else [1.0] + [0.0] * (dim - 1)


def _expected_key() -> str:
    """The key, or "" when there is none (leg 8c: embed_key is an empty /dev/null mount)."""
    try:
        with open(EMBED_KEY, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a) -> None:  # one line of our own per request instead
        pass

    def _reply(self, code: int, out: dict) -> None:
        raw = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _token(self, tenant: str, raw_body: bytes) -> None:
        """The Entra token exchange of the workload-identity flow (backend/retrieval/entra.py
        `_workload`): client credentials with a client assertion read from the federated file."""
        form = {k: v[0] for k, v in parse_qs(raw_body.decode("utf-8", "replace")).items()}
        problems = []
        if form.get("grant_type") != "client_credentials":
            problems.append("grant_type")
        if form.get("client_assertion_type") != JWT_BEARER:
            problems.append("client_assertion_type")
        if not form.get("client_id") or not form.get("scope"):
            problems.append("client_id/scope")
        try:
            with open(FEDERATED, encoding="utf-8") as fh:
                expected = fh.read().strip()
        except OSError:
            expected = ""
        if not expected or form.get("client_assertion", "") != expected:
            problems.append("client_assertion")
        if problems:
            print(f"stub: token tenant={tenant} client_id={form.get('client_id')!r} "
                  f"REFUSED ({', '.join(problems)}) -> 401", flush=True)
            self._reply(401, {"error": "invalid_client", "error_description": "refused by the stub"})
            return
        tok = secrets.token_urlsafe(32)
        _issued.add(tok)
        print(f"stub: token tenant={tenant} client_id={form.get('client_id')!r} "
              f"scope={form.get('scope')!r} assertion=ok -> 200", flush=True)
        self._reply(200, {"token_type": "Bearer", "expires_in": 3600, "access_token": tok})

    def do_POST(self) -> None:
        raw_body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        m = TOKEN_PATH.match(self.path.split("?", 1)[0])
        if m:
            self._token(m.group(1), raw_body)
            return
        if not self.path.split("?", 1)[0].endswith("/embeddings"):
            print(f"stub: POST {self.path} -> 404", flush=True)
            self._reply(404, {"error": "not found"})
            return
        try:
            body = json.loads(raw_body or b"{}")
        except ValueError:
            self._reply(400, {"error": "not json"})
            return
        auth_h = self.headers.get("Authorization") or ""
        presented = auth_h[7:] if auth_h.startswith("Bearer ") else None
        texts = list(body.get("input", []))
        expected = _expected_key()
        how = ("key" if presented and expected and presented == expected
               else "entra" if presented and presented in _issued else None)
        key_ok = how is not None
        if not key_ok:
            code, out = 400, {"error": {"message": "No connected db."}}  # the real gateway's wrong-key answer
        else:
            code, out = 200, {"object": "list", "model": body.get("model"),
                              "data": [{"object": "embedding", "index": i, "embedding": _vector(t, DIM)}
                                       for i, t in enumerate(texts)]}
        print(f"stub: POST {self.path} model={body.get('model')!r} texts={len(texts)} "
              f"key={how or 'REFUSED'} -> {code}", flush=True)
        self._reply(code, out)


def main() -> int:
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(CERT, KEY)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    done = threading.Event()

    def stop_endpoint(*_a) -> None:
        def _close() -> None:
            srv.shutdown()
            srv.server_close()
            print("stub: endpoint stopped (SIGUSR1); the port is closed", flush=True)
        threading.Thread(target=_close, daemon=True).start()

    signal.signal(signal.SIGUSR1, stop_endpoint)
    signal.signal(signal.SIGTERM, lambda *_a: done.set())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"stub: OpenAI-compatible embeddings on https://0.0.0.0:{PORT}/v1/embeddings, "
          f"{DIM} dimensions", flush=True)
    while not done.wait(1.0):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
