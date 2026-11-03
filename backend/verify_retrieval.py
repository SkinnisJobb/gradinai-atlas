"""Verify the retrieval add-on's storage and indexing (ADR 0044 §1-§4, STORY-atlas-retrieval-indexing).

Runs on a REAL pgvector Postgres (`testdb.PGVECTOR_IMAGE`, its own container) against a FAKE
embedder: a local HTTPS server answering the OpenAI embeddings shape with a deterministic hashed
bag-of-words vector, behind a throwaway CA. So the suite never needs a model, and it exercises
the real client: TLS, the key header, batching.

    A. OFF (a child process, plain Postgres): no extension, no `retrieval_*` table, no route, no
       scope entry, no `retrieval` field on /api/health, and the add-on's modules never imported
    B. ON BUT UNAVAILABLE (a child process, plain Postgres): Atlas boots, /api/health and search
       answer, and the add-on says `unavailable: … pgvector …`; plus every configuration refusal
       (`load_config`), and a typo in the switch stops the boot
    C. the index follows the graph: world cards and SERVED documents only (never an unserved, an
       external or a workitem's text), idempotent, a change re-embeds one source, an unserved or
       deleted node's chunks are removed, and the advisory lock makes a second pass skip
    D. scope: `shared` never sends a private node's text to the endpoint; `all` does; going back
       to `shared` removes what was indexed
    E. a model change re-embeds everything and never leaves two models' vectors side by side;
       a dimension change is `unavailable` until `rebuild`
    F. the key: read from the file on every call (rotation needs no restart), the `api-key`
       header for Azure, never in a log line, the status route or an error; a wrong key is an
       error naming the status code
    G. degradation: embedder down or untrusted certificate -> `degraded: …`, the index untouched,
       and `on` again after the next good pass
    H. the routes: status and reindex are owner-only, reindex needs the `admin` scope and CSRF,
       and a reindex re-embeds every source
    I. the whole access suite (verify_node_access.py) and the write-scope section
       (verify_object_graph.py) pass with the add-on ON, so its routes are classified there
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import math
import os
import re
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testdb  # noqa: E402

CHILD = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].startswith("--child=") else None

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def _base_env() -> None:
    os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))
    os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
    os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
    os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
    os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
    os.environ["ATLAS_RESEED"] = "1"


TEST_ORIGIN = "https://testserver"
PW = "Correct-Horse-Battery-Retrieval-9!"
EMBED_VARS = ("ATLAS_EMBED_URL", "ATLAS_EMBED_MODEL", "ATLAS_EMBED_DIM", "ATLAS_EMBED_KEY_FILE",
              "ATLAS_EMBED_KEY", "ATLAS_EMBED_AUTH_HEADER", "ATLAS_EMBED_CA_BUNDLE",
              "ATLAS_EMBED_SCOPE", "ATLAS_EMBED_DOC_PREFIX", "ATLAS_EMBED_QUERY_PREFIX",
              "ATLAS_EMBED_BATCH", "ATLAS_RETRIEVAL_INTERVAL_S")


# --------------------------------------------------------------------------- #
# A and B: child processes, each on a plain Postgres (the core's image)
# --------------------------------------------------------------------------- #
def _child_off() -> None:
    from sqlalchemy import text
    import app
    import auth
    from route_table import live_routes
    from fastapi.testclient import TestClient
    app.startup()
    with app.engine.connect() as c:
        tables = c.execute(text("SELECT tablename FROM pg_tables WHERE tablename LIKE 'retrieval%'")).all()
        ext = c.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar()
    check("A: off creates no retrieval_* table", not tables, str(tables))
    check("A: off creates no vector extension", not ext)
    paths = {getattr(r, "path", "") for r in live_routes(app.app)}
    check("A: off mounts no /api/retrieval route", not any(p.startswith("/api/retrieval") for p in paths))
    check("A: off adds no write-scope entry", not any("retrieval" in p for _, p in auth.WRITE_SCOPE_ROUTES))
    tok = _mint_owner_token(app, auth)
    h = TestClient(app.app, base_url=TEST_ORIGIN,
                   headers={"Authorization": f"Bearer {tok}"}).get("/api/health")
    check("A: /api/health answers and carries no `retrieval` field",
          h.status_code == 200 and "retrieval" not in h.json(), f"{h.status_code} {sorted(h.json())}")
    loaded = sorted(m for m in sys.modules if m.startswith("retrieval.") or m == "routers.retrieval")
    check("A: off imports none of the add-on's modules (only the package's switch)", not loaded, str(loaded))
    # After the import check on purpose: asking for a mode is what imports the query module.
    c = TestClient(app.app, base_url=TEST_ORIGIN, headers={"Authorization": f"Bearer {tok}"})
    hy = c.get("/api/search", params={"q": "anything", "mode": "hybrid"})
    check("A: mode=hybrid with the add-on off answers 200 with full text, degraded: off",
          hy.status_code == 200 and hy.json().get("degraded") == "retrieval is off on this instance"
          and hy.json().get("mode") == "hybrid", hy.text[:200])
    se = c.get("/api/search", params={"q": "anything", "mode": "semantic"})
    check("A: mode=semantic with the add-on off is empty and says why",
          se.status_code == 200 and se.json()["items"] == [] and "off" in se.json().get("degraded", ""),
          se.text[:200])
    bad = c.get("/api/search", params={"q": "x", "mode": "vector"})
    check("A: an unknown mode is a 400 naming the field", bad.status_code == 400
          and bad.json().get("field") == "mode", bad.text[:200])


def _child_unavailable() -> None:
    import app
    import auth
    from fastapi.testclient import TestClient
    app.startup()  # must not raise: the add-on is on, but this Postgres has no pgvector
    tok = _mint_owner_token(app, auth)
    c = TestClient(app.app, base_url=TEST_ORIGIN, headers={"Authorization": f"Bearer {tok}"})
    h = c.get("/api/health")
    state = h.json().get("retrieval", "") if h.status_code == 200 else ""
    check("B: on without pgvector, Atlas boots and /api/health answers 200", h.status_code == 200)
    check("B: ...and reports `unavailable` naming pgvector, informational", state.startswith("unavailable:")
          and "pgvector" in state and h.json().get("ok") is True, state)
    s = c.get("/api/search", params={"q": "anything"})
    check("B: full-text search is unaffected", s.status_code == 200, str(s.status_code))
    st = c.get("/api/retrieval/status")
    check("B: the status route says the same", st.status_code == 200 and st.json()["state"] == state)
    r = c.post("/api/retrieval/reindex", json={})
    check("B: a reindex is refused 409 while unavailable", r.status_code == 409, f"{r.status_code} {r.text[:120]}")


def _mint_owner_token(app, auth) -> str:
    from models_auth import AuthUser
    with app.session() as s:
        u = AuthUser(id="u-ret-child-owner", subject="ret-child-owner", name="o", role="owner",
                     is_service=True, must_change_password=False, password_hash=None,
                     created_at=dt.datetime.now(dt.timezone.utc))
        s.add(u)
        s.flush()
        raw, _ = auth.mint_service_token(s, owner=u, scopes=["read", "write", "admin"], label="ret-child")
        s.commit()
    return raw


def _run_child(mode: str, extra_env: dict) -> None:
    env = {k: v for k, v in os.environ.items() if k not in EMBED_VARS and k != "ATLAS_RETRIEVAL"}
    env.update(extra_env)
    env.pop("ATLAS_DB_URL", None)
    r = subprocess.run([sys.executable, str(HERE / "verify_retrieval.py"), f"--child={mode}"],
                       env=env, capture_output=True, text=True, timeout=600)
    for line in r.stdout.splitlines():
        if line.startswith("[PASS]") or line.startswith("[FAIL]"):
            print(line)
    check(f"child `{mode}` exited 0", r.returncode == 0,
          (r.stdout[-1500:] + r.stderr[-1500:]) if r.returncode else "")


# --------------------------------------------------------------------------- #
# The fake embedder: HTTPS behind a throwaway CA
# --------------------------------------------------------------------------- #
class Fake:
    dim = 32
    key = ""
    fail_status = 0
    calls: list[dict] = []
    # The fake Entra side (§J): one token endpoint per flow, all answering `entra_token`.
    entra_token = ""
    entra_ttl = 3600
    token_fail = 0
    token_calls: list[dict] = []


def _vector(t: str, dim: int) -> list[float]:
    v = [0.0] * dim
    for tok in re.findall(r"\w+", t.lower()):
        h = hashlib.sha256(tok.encode()).digest()
        v[h[0] % dim] += 1.0 if h[1] & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v] if any(v) else [1.0] + [0.0] * (dim - 1)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a) -> None:  # keep the run's output to PASS/FAIL lines
        pass

    def _token_reply(self, flow: str, fields: dict, expiry: dict) -> None:
        Fake.token_calls.append({"flow": flow, **fields})
        if Fake.token_fail:
            code, out = Fake.token_fail, {"error": "invalid_client", "error_description": "AADSTS700016"}
        else:
            code, out = 200, {"access_token": Fake.entra_token, "token_type": "Bearer", **expiry}
        raw = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        # Managed identity: App Service style (/msi, X-IDENTITY-HEADER) or IMDS (/imds, Metadata).
        from urllib.parse import parse_qs, urlsplit
        parts = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        flow = "managed-identity" if parts.path == "/msi" else "imds"
        self._token_reply(flow, {"query": q, "identity_header": self.headers.get("X-IDENTITY-HEADER"),
                                 "metadata": self.headers.get("Metadata")},
                          {"expires_on": str(int(time.time()) + Fake.entra_ttl)})

    def do_POST(self) -> None:
        raw_body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.endswith("/oauth2/v2.0/token"):  # workload identity's token exchange
            from urllib.parse import parse_qs
            form = {k: v[0] for k, v in parse_qs(raw_body.decode()).items()}
            self._token_reply("workload-identity", {"path": self.path, "form": form},
                              {"expires_in": Fake.entra_ttl})
            return
        body = json.loads(raw_body or b"{}")
        auth_h, api_key = self.headers.get("Authorization"), self.headers.get("api-key")
        Fake.calls.append({"path": self.path, "authorization": auth_h, "api_key": api_key,
                           "model": body.get("model"), "input": list(body.get("input", []))})
        presented = api_key or (auth_h[7:] if auth_h and auth_h.startswith("Bearer ") else None)
        if Fake.fail_status:
            code, out = Fake.fail_status, {"error": "down"}
        elif presented != Fake.key:
            code, out = 400, {"error": {"message": "No connected db."}}  # a proxy's wrong-key answer
        else:
            code, out = 200, {"object": "list", "model": body.get("model"),
                              "data": [{"object": "embedding", "index": i, "embedding": _vector(t, Fake.dim)}
                                       for i, t in enumerate(body.get("input", []))]}
        raw = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _make_tls(tmp: Path) -> tuple[Path, Path, Path]:
    """A CA and a localhost leaf it signs. Returns (ca.pem, leaf.pem, leaf.key)."""
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "verify-retrieval CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    paths = (tmp / "ca.pem", tmp / "leaf.pem", tmp / "leaf.key")
    paths[0].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths[1].write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    paths[2].write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return paths


def _start_fake(tmp: Path) -> tuple[ThreadingHTTPServer, Path]:
    ca, leaf, key = _make_tls(tmp)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(leaf), str(key))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, ca


# --------------------------------------------------------------------------- #
# The main run: pgvector Postgres, the add-on on
# --------------------------------------------------------------------------- #
def main() -> None:
    from settings import SettingsError
    import retrieval

    print("\n-- B. configuration refusals (load_config; no database) --")
    good = {"ATLAS_EMBED_URL": "https://embed.example.test/v1", "ATLAS_EMBED_MODEL": "m",
            "ATLAS_EMBED_DIM": "8"}
    check("B: a complete configuration has no problem", retrieval.load_config(good).problem is None)
    for label, env, needle in [
        ("no endpoint", {k: v for k, v in good.items() if k != "ATLAS_EMBED_URL"}, "ATLAS_EMBED_URL is not set"),
        ("plain http off loopback", {**good, "ATLAS_EMBED_URL": "http://host.docker.internal:4000/v1"}, "plain http"),
        ("a key in a plain variable", {**good, "ATLAS_EMBED_KEY": "x"}, "ATLAS_EMBED_KEY is set as a variable"),
        ("no model", {k: v for k, v in good.items() if k != "ATLAS_EMBED_MODEL"}, "ATLAS_EMBED_MODEL"),
        ("no dimension", {k: v for k, v in good.items() if k != "ATLAS_EMBED_DIM"}, "ATLAS_EMBED_DIM"),
        ("an unknown header kind", {**good, "ATLAS_EMBED_AUTH_HEADER": "basic"}, "ATLAS_EMBED_AUTH_HEADER"),
        ("a CA bundle that is not a file", {**good, "ATLAS_EMBED_CA_BUNDLE": "/nope/ca.pem"}, "not a file"),
    ]:
        p = retrieval.load_config(env).problem or ""
        check(f"B: refused — {label}", needle in p, p)
    check("B: plain http to loopback is allowed",
          retrieval.load_config({**good, "ATLAS_EMBED_URL": "http://127.0.0.1:11434/v1"}).problem is None)
    bad_scope = retrieval.load_config({**good, "ATLAS_EMBED_SCOPE": "everything"})
    check("B: an unknown scope is a problem AND falls back to shared, never wider",
          bad_scope.problem and bad_scope.scope == "shared", str(bad_scope))
    check("B: the scope defaults to shared", retrieval.load_config(good).scope == "shared")
    try:
        retrieval.enabled({"ATLAS_RETRIEVAL": "yes"})
        check("B: ATLAS_RETRIEVAL=yes stops the boot", False, "no error")
    except SettingsError as e:
        check("B: ATLAS_RETRIEVAL=yes stops the boot, naming the variable", "ATLAS_RETRIEVAL" in str(e))
    from retrieval.embedder import embeddings_url
    check("B: a /v1 base gets /embeddings appended",
          embeddings_url("https://h/v1/") == "https://h/v1/embeddings")
    az = "https://h/openai/deployments/d/embeddings?api-version=2024-10-21"
    check("B: an Azure deployment URL is used as given, query included", embeddings_url(az) == az)

    print("\n-- A/B. child processes on a plain Postgres --")
    _run_child("off", {})
    _run_child("unavailable", {"ATLAS_RETRIEVAL": "on", "ATLAS_EMBED_URL": "https://127.0.0.1:9/v1",
                               "ATLAS_EMBED_MODEL": "m", "ATLAS_EMBED_DIM": "8"})

    print("\n-- I. the access suite and the write-scope section with the add-on ON --")
    on_env = {"ATLAS_RETRIEVAL": "on"}  # no endpoint: mounted, unavailable — the routes are what is checked
    for script in ("verify_node_access.py", "verify_object_graph.py"):
        env = {k: v for k, v in os.environ.items() if k not in EMBED_VARS}
        env.update(on_env)
        env.pop("ATLAS_DB_URL", None)
        r = subprocess.run([sys.executable, str(HERE / script)], env=env, capture_output=True,
                           text=True, timeout=900)
        fails = [ln for ln in r.stdout.splitlines() if ln.startswith("[FAIL]")]
        check(f"I: {script} passes with ATLAS_RETRIEVAL=on", r.returncode == 0 and not fails,
              "\n".join(fails[:10]) or r.stderr[-1500:])
        if script == "verify_node_access.py":
            check("I: ...and its route table names the retrieval routes",
                  "/api/retrieval/status" in r.stdout and "/api/retrieval/reindex" in r.stdout)
        else:
            check("I: ...and §20 refused a read-only Bearer on the reindex route",
                  "POST /api/retrieval/reindex refuses a read-only Bearer" in r.stdout)

    _main_pgvector()


def _main_pgvector() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="verify-retrieval-"))
    srv, ca = _start_fake(tmp)
    port = srv.server_address[1]
    key_file = tmp / "embed_key"
    Fake.key = "sk-verify-" + base64.b16encode(os.urandom(8)).decode().lower()
    key_file.write_text(Fake.key + "\n", encoding="utf-8")

    DB = testdb.start(image=testdb.PGVECTOR_IMAGE)
    os.environ["ATLAS_DB_URL"] = DB.url
    os.environ.update({
        "ATLAS_RETRIEVAL": "on",
        "ATLAS_EMBED_URL": f"https://localhost:{port}/v1",
        "ATLAS_EMBED_MODEL": "fake-a",
        "ATLAS_EMBED_DIM": str(Fake.dim),
        "ATLAS_EMBED_KEY_FILE": str(key_file),
        "ATLAS_EMBED_CA_BUNDLE": str(ca),
        "ATLAS_EMBED_SCOPE": "shared",
        "ATLAS_EMBED_BATCH": "4",
        "ATLAS_RETRIEVAL_INTERVAL_S": "3600",  # the loop never fires during the run; passes are driven here
    })

    log_lines: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record) -> None:
            log_lines.append(self.format(record))
    cap = _Capture(level=logging.DEBUG)
    logging.getLogger().addHandler(cap)
    logging.getLogger().setLevel(logging.DEBUG)

    from sqlalchemy import text
    import app
    import auth
    import passwords
    from fastapi.testclient import TestClient
    from models_auth import AuthUser, NodeVisibility
    from models_graph import Edge, Node, NodeDoc
    import retrieval
    from retrieval import reconciler, service, setup

    app.startup()

    def q(sql: str, **p):
        with app.engine.connect() as c:
            return c.execute(text(sql), p).all()

    def keys() -> set[tuple[str, str, str]]:
        return {(a, b, c) for a, b, c in q("SELECT node_id, source, coalesce(doc_path,'') FROM retrieval_source")}

    def sent() -> str:
        return "\n".join(t for call in Fake.calls for t in call["input"])

    def reindex(rebuild: bool = False) -> dict:
        """A reindex NUDGES THE LOOP, which then runs the pass in its own thread; calling
        run_once beside it would only find the advisory lock taken. So wait for the loop."""
        import time
        n = service._state["passes"]
        code, payload = service.reindex(rebuild)
        if code != 202:
            return {"error": f"reindex {code}: {payload}"}
        deadline = time.time() + 120
        while service._state["passes"] == n and time.time() < deadline:
            time.sleep(0.05)
        return dict(service._state["last_stats"] or {}, code=code)

    def use(**env) -> None:
        os.environ.update({k: str(v) for k, v in env.items()})
        service._set(cfg=retrieval.load_config())

    print("\n-- C. the index follows the graph --")
    check("C: boot with the add-on on reports `on`", service.health() == "on", service.health())
    check("C: the vector column has the configured dimension", setup.stored_dim(app.engine) == Fake.dim)

    docs_pub = [{"path": "docs/nodes/s-pub/arch.md", "kind": "architecture", "title": "Arch"},
                {"path": "docs/nodes/s-pub/ext.md", "kind": "architecture", "title": "Ext", "external": True}]
    long_doc = "# Overview\n\nalpha overview paragraph.\n\n```mermaid\ngraph TD; A-->B\n```\n\n" + "\n\n".join(
        f"## Part {i}\n\n" + ("publicdocmarker words " * 120) for i in range(4))
    with app.session() as s:
        for nid, ntype, summary, attrs in [
            ("p-pub", "project", "the public project", {}),
            ("s-pub", "service", "alpha public card marker", {"docs": docs_pub}),
            ("p-priv", "project", "the private project", {}),
            ("s-priv", "service", "secretcardmarker zeta",
             {"docs": [{"path": "docs/nodes/s-priv/a.md", "kind": "architecture", "title": "A"}]}),
            ("TASK-RET-1", "task", None, {}),
        ]:
            s.add(Node(id=nid, type=ntype, name=nid, summary=summary, attributes=attrs, attention_count=0,
                       completion=0.0, origin="atlas", body="workitemmarker" if ntype == "task" else None))
        s.flush()
        s.add_all([Edge(src="p-pub", dst="s-pub", edge_type="contains", origin="atlas"),
                   Edge(src="p-priv", dst="s-priv", edge_type="contains", origin="atlas"),
                   NodeVisibility(node_id="p-priv", visibility="private"),
                   NodeDoc(node_id="s-pub", path="docs/nodes/s-pub/arch.md", content=long_doc),
                   NodeDoc(node_id="s-pub", path="docs/nodes/s-pub/hidden.md", content="unservedmarker text"),
                   NodeDoc(node_id="s-pub", path="docs/nodes/s-pub/ext.md", content="externalmarker text"),
                   NodeDoc(node_id="s-priv", path="docs/nodes/s-priv/a.md", content="secretdocmarker text")])
        s.commit()

    stats = service.run_once()
    k = keys()
    check("C: a pass succeeds", not stats.get("error"), str(stats))
    check("C: world cards are indexed", {("p-pub", "prose", ""), ("s-pub", "prose", "")} <= k, str(k))
    check("C: the served document is indexed", ("s-pub", "doc", "docs/nodes/s-pub/arch.md") in k)
    check("C: a stored but unserved document is NOT indexed, and its text never sent",
          not any(p.endswith("hidden.md") for _, _, p in k) and "unservedmarker" not in sent())
    check("C: an external document is NOT indexed, and its text never sent",
          not any(p.endswith("ext.md") for _, _, p in k) and "externalmarker" not in sent())
    check("C: a workitem is NOT indexed (world families only), and its text never sent",
          not any(n == "TASK-RET-1" for n, _, _ in k) and "workitemmarker" not in sent())
    n_doc_chunks = q("SELECT count(*) FROM retrieval_chunk WHERE doc_path = 'docs/nodes/s-pub/arch.md'")[0][0]
    check("C: the long document is split into several chunks", n_doc_chunks >= 3, str(n_doc_chunks))
    first = q("SELECT content FROM retrieval_chunk WHERE doc_path = 'docs/nodes/s-pub/arch.md' ORDER BY ordinal")
    check("C: each chunk starts with `node › doc title`", all(c[0].startswith("s-pub › Arch") for c in first))
    check("C: a mermaid fence becomes a one-line placeholder",
          "[mermaid block omitted]" in sent() and "A-->B" not in sent())
    check("C: calls are batched (no call over ATLAS_EMBED_BATCH texts)",
          all(len(c["input"]) <= 4 for c in Fake.calls), str([len(c["input"]) for c in Fake.calls]))
    check("C: every stored vector has the configured dimension",
          q("SELECT count(*) FROM retrieval_chunk WHERE vector_dims(embedding) <> :d", d=Fake.dim)[0][0] == 0)

    before = len(Fake.calls)
    again = service.run_once()
    check("C: a second pass with nothing changed embeds nothing",
          again.get("embedded_sources") == 0 and len(Fake.calls) == before, str(again))

    with app.session() as s:
        s.get(Node, "s-pub").summary = "alpha public card marker, edited"
        s.commit()
    edit = service.run_once()
    check("C: editing one card re-embeds exactly that source", edit.get("embedded_sources") == 1, str(edit))

    with app.session() as s:
        n = s.get(Node, "s-pub")
        n.attributes = {"docs": [d for d in docs_pub if not d["path"].endswith("arch.md")]}
        s.commit()
    unserve = service.run_once()
    check("C: un-serving a document removes its source and chunks",
          ("s-pub", "doc", "docs/nodes/s-pub/arch.md") not in keys()
          and q("SELECT count(*) FROM retrieval_chunk WHERE doc_path LIKE '%arch.md'")[0][0] == 0, str(unserve))

    with app.session() as s:
        for d in s.query(NodeDoc).filter(NodeDoc.node_id == "s-pub"):
            s.delete(d)
        for e in s.query(Edge).filter(Edge.dst == "s-pub"):
            s.delete(e)
        s.flush()
        s.delete(s.get(Node, "s-pub"))
        s.commit()
    check("C: a deleted node's chunks are still there before the sweep (no FK, no cascade)",
          q("SELECT count(*) FROM retrieval_chunk WHERE node_id = 's-pub'")[0][0] > 0)
    check("C: ...and no join on `node` returns them",
          q("SELECT count(*) FROM retrieval_chunk c JOIN node n ON n.id = c.node_id "
            "WHERE c.node_id = 's-pub'")[0][0] == 0)
    service.run_once()
    check("C: the next pass sweeps a deleted node's source and chunks",
          q("SELECT count(*) FROM retrieval_chunk WHERE node_id = 's-pub'")[0][0] == 0
          and not any(n == "s-pub" for n, _, _ in keys()))

    holder = app.engine.connect()
    holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": reconciler.LOCK_KEY})
    holder.commit()
    skipped = reconciler.run_pass(app.engine, service._state["cfg"])
    holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": reconciler.LOCK_KEY})
    holder.commit()
    holder.close()
    check("C: a pass skips while another worker holds the lock", bool(skipped.get("skipped")), str(skipped))

    print("\n-- D. scope --")
    check("D: `shared` never sent a private node's card or document to the endpoint",
          "secretcardmarker" not in sent() and "secretdocmarker" not in sent())
    check("D: ...and indexed neither", not any(n in ("p-priv", "s-priv") for n, _, _ in keys()))
    use(ATLAS_EMBED_SCOPE="all")
    service.run_once()
    check("D: `all` indexes the private subtree (visibility is the query side's filter)",
          {("p-priv", "prose", ""), ("s-priv", "prose", ""), ("s-priv", "doc", "docs/nodes/s-priv/a.md")} <= keys())
    use(ATLAS_EMBED_SCOPE="shared")
    service.run_once()
    check("D: going back to `shared` removes the private sources and their chunks",
          not any(n in ("p-priv", "s-priv") for n, _, _ in keys())
          and q("SELECT count(*) FROM retrieval_chunk WHERE node_id IN ('p-priv','s-priv')")[0][0] == 0)

    print("\n-- E. model and dimension changes --")
    n_sources = len(keys())
    use(ATLAS_EMBED_MODEL="fake-b")
    changed = service.run_once()
    models = {m for (m,) in q("SELECT DISTINCT model FROM retrieval_chunk")}
    check("E: a model change re-embeds every source", changed.get("embedded_sources") == n_sources, str(changed))
    check("E: ...and only the new model's vectors remain", models == {"fake-b"}, str(models))
    check("E: ...and the endpoint was asked for the new model", Fake.calls[-1]["model"] == "fake-b")

    use(ATLAS_EMBED_DIM="16")
    problem = setup.ensure(app.engine, service._state["cfg"]) or ""
    check("E: a dimension change is a problem naming both numbers and the remedy",
          "32" in problem and "16" in problem and "rebuild" in problem, problem)
    service.boot(app.engine, start_loop=False)
    check("E: ...booting with it is `unavailable`, not a crash", service.health().startswith("unavailable:"))
    Fake.dim = 16
    reb = reindex(rebuild=True)
    check("E: reindex with rebuild recreates the tables for the new dimension, and the loop re-indexes",
          reb.get("code") == 202 and service.health() == "on" and setup.stored_dim(app.engine) == 16
          and reb.get("embedded_sources") == n_sources, f"{service.health()} {reb}")
    Fake.dim = 32
    use(ATLAS_EMBED_DIM="32", ATLAS_EMBED_MODEL="fake-a")
    reindex(rebuild=True)

    print("\n-- F. the key --")
    check("F: the Bearer header carried the key from the file",
          Fake.calls[-1]["authorization"] == f"Bearer {Fake.key}" and Fake.calls[-1]["api_key"] is None)
    old_key = Fake.key
    Fake.key = old_key + "-rotated"
    key_file.write_text(Fake.key, encoding="utf-8")
    rot = reindex()
    check("F: a rotated key file is used on the next call, no restart", not rot.get("error")
          and Fake.calls[-1]["authorization"] == f"Bearer {Fake.key}", str(rot))
    use(ATLAS_EMBED_AUTH_HEADER="api-key")
    az = reindex()
    check("F: `api-key` mode sends Azure's header and no Authorization", not az.get("error")
          and Fake.calls[-1]["api_key"] == Fake.key and Fake.calls[-1]["authorization"] is None, str(az))
    use(ATLAS_EMBED_AUTH_HEADER="bearer")
    key_file.write_text("sk-wrong", encoding="utf-8")
    wrong = reindex()
    check("F: a wrong key is an error naming the status code (a proxy may say 400, not 401)",
          "400" in (wrong.get("error") or ""), str(wrong))
    key_file.write_text(Fake.key, encoding="utf-8")
    key_file_missing = tmp / "nope"
    use(ATLAS_EMBED_KEY_FILE=str(key_file_missing))
    missing = reindex()
    check("F: an unreadable key file is an error naming the path, not a crash",
          repr(str(key_file_missing)) in (missing.get("error") or ""), str(missing))
    use(ATLAS_EMBED_KEY_FILE=str(key_file))
    reindex()

    print("\n-- G. degradation --")
    n_chunks = q("SELECT count(*) FROM retrieval_chunk")[0][0]
    Fake.fail_status = 503
    down = reindex()
    check("G: embedder down -> the pass reports an error and does not raise", bool(down.get("error")), str(down))
    check("G: ...health says `degraded`, never unavailable or red", service.health().startswith("degraded:"))
    check("G: ...and the index is untouched (stale, not wrong)",
          q("SELECT count(*) FROM retrieval_chunk")[0][0] == n_chunks)
    Fake.fail_status = 0
    use(ATLAS_EMBED_CA_BUNDLE="")  # empty is unset: the system trust store, which does not know this CA
    untrusted = service.run_once()
    check("G: without the CA bundle the certificate is refused (verification cannot be turned off)",
          "unreachable" in (untrusted.get("error") or ""), str(untrusted))
    use(ATLAS_EMBED_CA_BUNDLE=str(ca))
    back = service.run_once()
    check("G: the next good pass returns to `on`", not back.get("error") and service.health() == "on",
          f"{back} {service.health()}")

    print("\n-- H. the routes --")
    with app.session() as s:
        for uid, name, role in [("u-ret-owner", "verify-ret-owner", "owner"),
                                ("u-ret-member", "verify-ret-member", "member")]:
            s.add(AuthUser(id=uid, subject=name, name=name, role=role, is_service=False,
                           must_change_password=False, password_hash=passwords.hash_password(PW),
                           created_at=dt.datetime.now(dt.timezone.utc)))
        s.commit()

    def sign_in(username: str):
        c = TestClient(app.app, base_url=TEST_ORIGIN)
        page = c.get("/auth/login")
        token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
        c.post("/auth/login", data={"username": username, "password": PW, "csrf_token": token},
               headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
        return c, c.get("/api/me").json()["csrf_token"]

    def bearer(uid: str, role: str, scopes: list[str]) -> TestClient:
        with app.session() as s:
            s.add(AuthUser(id=uid, subject=uid, name=uid, role=role, is_service=True,
                           must_change_password=False, password_hash=None,
                           created_at=dt.datetime.now(dt.timezone.utc)))
            s.flush()
            raw, _ = auth.mint_service_token(s, owner=s.get(AuthUser, uid), scopes=scopes, label=uid)
            s.commit()
        return TestClient(app.app, base_url=TEST_ORIGIN, headers={"Authorization": f"Bearer {raw}"})

    owner, owner_csrf = sign_in("verify-ret-owner")
    member, member_csrf = sign_in("verify-ret-member")
    st = owner.get("/api/retrieval/status")
    body = st.json() if st.status_code == 200 else {}
    check("H: an owner reads the status", st.status_code == 200 and body.get("state") == "on", st.text[:200])
    check("H: ...with counts, the model and the endpoint host, and nothing pending",
          body.get("chunks", 0) > 0 and body.get("model") == "fake-a" and body.get("endpoint_host") == "localhost"
          and body.get("pending_sources") == 0 and body.get("chunks_other_models") == 0, json.dumps(body)[:300])
    check("H: a member is refused the status (403)", member.get("/api/retrieval/status").status_code == 403)
    r = member.post("/api/retrieval/reindex", json={"csrf_token": member_csrf}, headers={"Origin": TEST_ORIGIN})
    check("H: a member is refused a reindex (403)", r.status_code == 403, str(r.status_code))
    r = owner.post("/api/retrieval/reindex", json={}, headers={"Origin": TEST_ORIGIN})
    check("H: an owner session without the CSRF token is refused", r.status_code == 403, str(r.status_code))
    ro = bearer("u-ret-ro", "owner", ["read"])
    r = ro.post("/api/retrieval/reindex", json={})
    check("H: a read-only Bearer is refused, naming `admin`",
          r.status_code == 403 and "admin" in r.json().get("detail", ""), r.text[:200])
    bot = bearer("u-ret-bot", "member", ["read", "write", "admin"])
    check("H: an admin-scoped Bearer that is not an owner is refused (owner-only)",
          bot.post("/api/retrieval/reindex", json={}).status_code == 403)
    n_sources = len(keys())
    import time
    n_passes = service._state["passes"]
    r = owner.post("/api/retrieval/reindex", json={"csrf_token": owner_csrf}, headers={"Origin": TEST_ORIGIN})
    check("H: an owner's reindex is accepted (202)", r.status_code == 202, r.text[:200])
    deadline = time.time() + 120
    while service._state["passes"] == n_passes and time.time() < deadline:
        time.sleep(0.05)
    full = service._state["last_stats"] or {}
    check("H: ...the loop wakes at once and re-embeds every source", full.get("embedded_sources") == n_sources,
          str(full))
    tok_h = bearer("u-ret-health", "owner", ["read"]).get("/api/health")
    check("H: /api/health carries `retrieval: on`", tok_h.status_code == 200 and tok_h.json().get("retrieval") == "on",
          tok_h.text[:200])

    print("\n-- K. the query side: mode=semantic|hybrid, expand (STORY-atlas-retrieval-query) --")
    _section_k(app, owner, member, bearer, service, use, reindex, q)

    print("\n-- J. Entra ID tokens (TASK-1375) --")
    entra_secrets = _section_j(port, ca, key_file, tmp, reindex, use)

    print("\n-- F. the key never leaks --")
    haystack = "\n".join(log_lines) + json.dumps(owner.get("/api/retrieval/status").json())
    for k in (old_key, Fake.key, *entra_secrets):
        check(f"F: a key or token ({len(k)} chars) appears in no log line and not in the status",
              k not in haystack)

    logging.getLogger().removeHandler(cap)
    srv.shutdown()


def _section_k(app, owner, member, bearer, service, use, reindex, q) -> None:
    """The query side. The fixture plants the query's words in the PRIVATE nodes too, and gives a
    public node a `requires` edge to a private one, so a leak through the vector list or through
    expansion would show. Deletion-equivalence is then proven literally: the answers a member and
    an agent get equal the answers after the private rows are physically gone."""
    from sqlalchemy import text as sql_text
    from models_auth import NodeVisibility
    from models_graph import Edge, Node, NodeDoc

    def node(nid, ntype, summary, attrs=None):
        return Node(id=nid, type=ntype, name=nid, summary=summary, attributes=attrs or {},
                    attention_count=0, completion=0.0, origin="atlas")

    heat_doc = {"path": "docs/nodes/k-pub-a/heat.md", "kind": "architecture", "title": "Heat"}
    old_doc = {"path": "docs/nodes/k-pub-a/old.md", "kind": "architecture", "title": "Old"}
    with app.session() as s:
        s.add_all([
            node("k-pub-a", "service", "radiator boiler heating loop", {"docs": [heat_doc, old_doc]}),
            node("k-pub-c", "service", "a plain child with no matching words"),
            node("k-del", "service", "radiator boiler heating doomed"),
            node("k-priv-c", "service", "radiator boiler heating private child"),
        ])
        s.flush()
        p_priv = s.get(Node, "s-priv")
        p_priv.summary = "radiator boiler heating secret"
        s.add_all([
            Edge(src="p-pub", dst="k-pub-a", edge_type="contains", origin="atlas"),
            Edge(src="k-pub-a", dst="k-pub-c", edge_type="contains", origin="atlas"),
            Edge(src="p-pub", dst="k-del", edge_type="contains", origin="atlas"),
            Edge(src="s-priv", dst="k-priv-c", edge_type="contains", origin="atlas"),
            Edge(src="k-pub-a", dst="s-priv", edge_type="requires", origin="atlas"),
            NodeDoc(node_id="k-pub-a", path=heat_doc["path"],
                    content="# Heat\n\nThe radiator boiler keeps the heating loop at pressure."),
            NodeDoc(node_id="k-pub-a", path=old_doc["path"],
                    content="# Old\n\nobsoletewordx radiator notes that are no longer published."),
        ])
        s.commit()
    use(ATLAS_EMBED_SCOPE="all")
    service.run_once()
    private = {"p-priv", "s-priv", "k-priv-c"}
    Q = "radiator boiler heating"

    def ids(body):
        return [i["id"] for i in body.get("items", [])]

    # 1. The default is today's search, and never touches the query module.
    d = owner.get("/api/search", params={"q": Q}).json()
    check("K: a default search has exactly today's top-level keys",
          set(d) == {"total", "count", "page", "page_size", "filters", "max_page_size", "items"},
          str(sorted(d)))
    check("K: ...today's item keys, and today's filters",
          all(set(i) <= {"id", "type", "name", "summary", "matched_in", "doc_paths", "snippet", "rank",
                         "adr_ids"} for i in d["items"]) and d["filters"] == {"q": Q, "families": None},
          str(d["items"][:1]))
    saved = sys.modules.pop("retrieval.query", None)
    sys.modules["retrieval.query"] = None  # importing it now raises
    poisoned = owner.get("/api/search", params={"q": Q})
    del sys.modules["retrieval.query"]
    if saved is not None:
        sys.modules["retrieval.query"] = saved
    check("K: the default path works with the query module unimportable (it never imports it)",
          poisoned.status_code == 200 and poisoned.json() == d, str(poisoned.status_code))

    # 2. Hybrid and semantic answer, as pointers.
    h = owner.get("/api/search", params={"q": Q, "mode": "hybrid"}).json()
    sem_items = [i for i in h["items"] if "semantic" in i["matched_in"]]
    check("K: hybrid answers with mode, scores and semantic hits, no degradation",
          h.get("mode") == "hybrid" and "degraded" not in h and sem_items
          and all("score" in i for i in h["items"]), json.dumps(h)[:300])
    check("K: ...still pointers: no abstract, detail or body in any item",
          not any(k in i for i in h["items"] for k in ("abstract", "detail", "body")))
    check("K: the owner sees the private nodes (so the oracle below is not vacuous)",
          private & set(ids(h)), str(ids(h)))
    s_ = owner.get("/api/search", params={"q": Q, "mode": "semantic"}).json()
    check("K: semantic answers only from the vector list",
          s_["items"] and all(i["matched_in"] == ["semantic"] or "semantic" in i["matched_in"]
                               for i in s_["items"]), json.dumps(s_)[:300])
    # The fixture is small enough that a 30-node vector list holds EVERY node, and a node already
    # in the result is never added again as a neighbour. So expansion is shown where the
    # neighbour is genuinely missing: full text alone, and hybrid with a 2-node vector list.
    e = owner.get("/api/search", params={"q": Q, "mode": "fulltext", "expand": 1}).json()
    via = {i["id"]: i.get("via") for i in e["items"] if i.get("via")}
    check("K: expand=1 adds a missing neighbour with `via` (k-pub-c, contained by k-pub-a)",
          via.get("k-pub-c") == {"from": "k-pub-a", "edge": "contains", "direction": "out"},
          json.dumps(via))
    check("K: ...scored at half its seed's score",
          next(i["score"] for i in e["items"] if i["id"] == "k-pub-c")
          == round(next(i["score"] for i in e["items"] if i["id"] == "k-pub-a") * 0.5, 6),
          json.dumps(e["items"])[:300])
    os.environ["ATLAS_RETRIEVAL_TOP_K"] = "2"
    e2 = owner.get("/api/search", params={"q": Q, "mode": "hybrid", "expand": 1}).json()
    os.environ.pop("ATLAS_RETRIEVAL_TOP_K")
    check("K: hybrid with expand=1 adds neighbours beyond a short vector list",
          any(i.get("via") for i in e2["items"]), json.dumps([(i["id"], i.get("via")) for i in e2["items"]]))

    # 3. Deletion-equivalence, for a person and an agent.
    agent = bearer("u-ret-agent-k", "member", ["read"])
    principals = {"member": member, "agent": agent}
    requests = [{"q": Q, "mode": "semantic"}, {"q": Q, "mode": "hybrid"},
                {"q": Q, "mode": "hybrid", "expand": 1}, {"q": Q, "mode": "semantic", "expand": 1},
                {"q": Q, "mode": "hybrid", "families": "object,bridge"},
                {"q": "secret", "mode": "hybrid", "expand": 1}]
    before = {(who, i): c.get("/api/search", params=p).json()
              for who, c in principals.items() for i, p in enumerate(requests)}
    check("K: a non-grantee sees results, and none of them private",
          all(b.get("items") for (_, i), b in before.items() if i < 5)
          and not any(private & set(ids(b)) for b in before.values()),
          str({k: ids(v) for k, v in before.items()})[:400])
    check("K: ...and expansion never reached the private node through k-pub-a's requires edge",
          not any(i.get("via", {}).get("from") == "k-pub-a" and i["id"] == "s-priv"
                  for b in before.values() for i in b.get("items", [])))
    with app.engine.begin() as c:
        p = {"ids": sorted(private)}
        for stmt in ("DELETE FROM retrieval_chunk WHERE node_id = ANY(:ids)",
                     "DELETE FROM retrieval_source WHERE node_id = ANY(:ids)",
                     "DELETE FROM edge WHERE src = ANY(:ids) OR dst = ANY(:ids)",
                     "DELETE FROM node_doc WHERE node_id = ANY(:ids)",
                     "DELETE FROM node_grant WHERE node_id = ANY(:ids)",
                     "DELETE FROM node_visibility WHERE node_id = ANY(:ids)",
                     "DELETE FROM node WHERE id = ANY(:ids)"):
            c.execute(sql_text(stmt), p)
    after = {(who, i): c.get("/api/search", params=p).json()
             for who, c in principals.items() for i, p in enumerate(requests)}
    diff = [k for k in before if before[k] != after[k]]
    check("K: DELETION-EQUIVALENCE: every answer (items, total, snippets, scores) equals the answer "
          "with the private rows physically removed, for a person and an agent",
          not diff, f"differs: {diff}; e.g. {json.dumps(before[diff[0]])[:200]} vs "
                    f"{json.dumps(after[diff[0]])[:200]}" if diff else "")

    # 4. Served-at-query-time and deleted-before-sweep.
    with app.session() as s:
        s.get(Node, "k-pub-a").attributes = {"docs": [heat_doc]}  # un-publish old.md, no pass
        for n_ in s.query(Edge).filter((Edge.src == "k-del") | (Edge.dst == "k-del")):
            s.delete(n_)
        s.flush()
        s.delete(s.get(Node, "k-del"))  # deleted, chunks still there, no pass
        s.commit()
    check("K: (the un-published document's chunks are still stored)",
          q("SELECT count(*) FROM retrieval_chunk WHERE doc_path = 'docs/nodes/k-pub-a/old.md'")[0][0] > 0)
    o = owner.get("/api/search", params={"q": "obsoletewordx radiator", "mode": "hybrid"}).json()
    leaked = [i for i in o["items"] if "docs/nodes/k-pub-a/old.md" in i["doc_paths"]
              or "obsoletewordx" in (i.get("snippet") or "")]
    check("K: a stored but un-served document is never returned, before any pass removes it",
          not leaked, json.dumps(leaked)[:300])
    gone = owner.get("/api/search", params={"q": Q, "mode": "semantic"}).json()
    check("K: a node deleted before the sweep never surfaces", "k-del" not in ids(gone), str(ids(gone)))

    # 5. Degradation is reported, never silent.
    Fake.fail_status = 503
    dh = owner.get("/api/search", params={"q": Q, "mode": "hybrid"}).json()
    ds = owner.get("/api/search", params={"q": Q, "mode": "semantic"}).json()
    Fake.fail_status = 0
    check("K: embedder down: hybrid returns the full-text result and says why",
          "503" in dh.get("degraded", "") and dh["items"]
          and not any("semantic" in i["matched_in"] for i in dh["items"]), json.dumps(dh)[:300])
    check("K: embedder down: semantic is empty and says why",
          ds["items"] == [] and "503" in ds.get("degraded", ""), json.dumps(ds)[:200])
    service._set(unavailable="test: storage gone")
    du = owner.get("/api/search", params={"q": Q, "mode": "hybrid"}).json()
    service._set(unavailable=None)
    check("K: unavailable: hybrid degrades and names the reason",
          du.get("degraded", "").startswith("unavailable: test") and du["items"], json.dumps(du)[:200])
    bad = owner.get("/api/search", params={"q": Q, "expand": 2})
    check("K: expand outside 0/1 is a 400 naming the field",
          bad.status_code == 400 and bad.json().get("field") == "expand", bad.text[:200])
    use(ATLAS_EMBED_SCOPE="shared")
    service.run_once()


def _section_j(port: int, ca: Path, key_file: Path, tmp: Path, reindex, use) -> list[str]:
    """Entra mode against fake token endpoints on the same HTTPS fake. Returns every secret it
    used (the key it replaced, the tokens, the assertion) for §F's leak check."""
    import retrieval
    from retrieval import entra, service
    secrets = [Fake.key]
    base = f"https://localhost:{port}"
    # The token client verifies with the default trust store, as it would against Microsoft;
    # SSL_CERT_FILE is how a test (or a proxy) adds its CA to that, for this process only.
    os.environ["SSL_CERT_FILE"] = str(ca)
    assertion_file = tmp / "federated-token"
    assertion = "eyJ.fake-federated-assertion." + base64.b16encode(os.urandom(6)).decode()
    assertion_file.write_text(assertion, encoding="utf-8")
    secrets.append(assertion)

    both = retrieval.load_config({"ATLAS_EMBED_URL": f"{base}/v1", "ATLAS_EMBED_MODEL": "m",
                                  "ATLAS_EMBED_DIM": "8", "ATLAS_EMBED_AUTH_HEADER": "entra",
                                  "ATLAS_EMBED_KEY_FILE": str(key_file)})
    check("J: entra together with a key file is refused (two credentials, one call)",
          "must not be set as well" in (both.problem or ""), str(both.problem))

    def tokens(token: str, ttl: int) -> None:
        Fake.entra_token, Fake.entra_ttl, Fake.key = token, ttl, token
        secrets.append(token)

    # 1. Workload identity.
    entra.reset()
    Fake.token_calls.clear()
    tokens("entra-tok-" + base64.b16encode(os.urandom(6)).decode(), 3600)
    use(ATLAS_EMBED_AUTH_HEADER="entra", ATLAS_EMBED_KEY_FILE="",
        AZURE_FEDERATED_TOKEN_FILE=str(assertion_file), AZURE_CLIENT_ID="client-1",
        AZURE_TENANT_ID="tenant-x", AZURE_AUTHORITY_HOST=base + "/")
    check("J: the configuration is valid in entra mode", service._state["cfg"].problem is None,
          str(service._state["cfg"].problem))
    first = reindex()
    tc = Fake.token_calls[0] if Fake.token_calls else {}
    form = tc.get("form", {})
    check("J: workload identity: the pass succeeds with the Entra token as Bearer",
          not first.get("error") and Fake.calls[-1]["authorization"] == f"Bearer {Fake.entra_token}", str(first))
    check("J: ...exchanged at {authority}{tenant}/oauth2/v2.0/token with the federated assertion",
          tc.get("path") == "/tenant-x/oauth2/v2.0/token" and form.get("client_assertion") == assertion
          and form.get("client_id") == "client-1" and form.get("grant_type") == "client_credentials"
          and form.get("scope") == entra.DEFAULT_SCOPE, json.dumps({k: v for k, v in tc.items() if k != "form"}))
    n = len(Fake.token_calls)
    reindex()
    check("J: a valid token is cached (no second exchange)", len(Fake.token_calls) == n, str(len(Fake.token_calls)))

    entra.reset()
    tokens("entra-tok-short-" + base64.b16encode(os.urandom(6)).decode(), 100)  # inside the refresh margin
    reindex()
    n, n_embed = len(Fake.token_calls), len(Fake.calls)
    again = reindex()
    # Every embeddings call of the pass is one use of the token, and a token inside the margin is
    # fetched again before EACH use. How many calls a pass makes is how many sources are in
    # scope (ATLAS_EMBED_SCOPE=shared, batch 4): under the homelab copy a handful; under a
    # distribution whose boot seeds the system node set (marked shared) also all of that set
    # (STORY-oss-23 A3b). So the claim is one exchange per call, counted, rather than "one".
    uses = len(Fake.calls) - n_embed
    check("J: a token inside the refresh margin is fetched again before use (once per embeddings call)",
          uses >= 1 and len(Fake.token_calls) == n + uses and not again.get("error"),
          f"{len(Fake.token_calls) - n} exchanges for {uses} calls {again}")

    # 2. Managed identity, App Service / Container Apps style.
    entra.reset()
    tokens("entra-tok-msi-" + base64.b16encode(os.urandom(6)).decode(), 3600)
    use(AZURE_FEDERATED_TOKEN_FILE="", IDENTITY_ENDPOINT=f"{base}/msi", IDENTITY_HEADER="idh-1")
    msi = reindex()
    tc = Fake.token_calls[-1]
    check("J: managed identity: the pass succeeds, asking for the resource with X-IDENTITY-HEADER",
          not msi.get("error") and tc["flow"] == "managed-identity" and tc["identity_header"] == "idh-1"
          and tc["query"].get("resource") == "https://cognitiveservices.azure.com"
          and tc["query"].get("client_id") == "client-1", f"{msi} {tc}")

    # 3. IMDS.
    entra.reset()
    tokens("entra-tok-imds-" + base64.b16encode(os.urandom(6)).decode(), 3600)
    use(IDENTITY_ENDPOINT="", IDENTITY_HEADER="")
    real_imds, entra.IMDS_URL = entra.IMDS_URL, f"{base}/imds"
    imds = reindex()
    tc = Fake.token_calls[-1]
    check("J: IMDS: the pass succeeds, with Metadata: true", not imds.get("error") and tc["flow"] == "imds"
          and tc["metadata"] == "true", f"{imds} {tc}")

    # A refused token request.
    entra.reset()
    Fake.token_fail = 401
    refused = reindex()
    err = refused.get("error") or ""
    check("J: a refused token request is an error naming Entra, the flow and the status",
          "Entra" in err and "IMDS" in err and "401" in err, err)
    check("J: ...and it never carries a token or the assertion",
          not any(s in err for s in secrets), err)
    Fake.token_fail = 0
    entra.IMDS_URL = real_imds

    # Back to the key file for anything after this section.
    for v in ("AZURE_FEDERATED_TOKEN_FILE", "AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_AUTHORITY_HOST",
              "IDENTITY_ENDPOINT", "IDENTITY_HEADER", "SSL_CERT_FILE"):
        os.environ.pop(v, None)
    Fake.key = secrets[0]
    use(ATLAS_EMBED_AUTH_HEADER="bearer", ATLAS_EMBED_KEY_FILE=str(key_file))
    entra.reset()
    return secrets


if __name__ == "__main__":
    _base_env()
    try:
        if CHILD:
            DB = testdb.start()
            os.environ["ATLAS_DB_URL"] = DB.url
            {"--child=off": _child_off, "--child=unavailable": _child_unavailable}[CHILD]()
        else:
            main()
    except testdb.TestDatabaseUnavailable as e:
        print(f"FATAL: {e}")
        sys.exit(1)
    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nALL PASS")
