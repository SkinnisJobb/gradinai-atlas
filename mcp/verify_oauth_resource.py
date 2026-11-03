"""Prove atlas-mcp's OAuth protected-resource surface (STORY-oss-25, ADR 0041 §9).

No database, no network and no real atlas-api: atlas-mcp validates nothing itself (the API
does, proven by backend/verify_idp_access_token.py against a stub IdP), so the API here is a
fake that accepts one JWT-shaped string and refuses another with the API's own reason.

  A. FEATURE OFF (a fresh interpreter, ATLAS_OAUTH_RESOURCE unset): the 401 is today's,
     byte for byte (`Bearer realm="atlas-mcp"`, the same body); no metadata route exists;
     a JWT-shaped bearer is refused at the door and never sent to atlas-api.
  B. SETTINGS: set-but-blank, http, a resource without an issuer, scopes without a
     resource each stop the process at import, naming the variable; ATLAS_OIDC_ISSUER
     alone (SSO login only) leaves the feature off.
  C. BOARD CRITERION 1, over HTTP: an MCP request without a token gets 401 with
     `WWW-Authenticate: Bearer resource_metadata="..."` (and the configured scope); that
     URL is served (RFC 9728 §3.1 path insertion, built by the SDK) and names the
     configured issuer in `authorization_servers`, with `resource` the canonical URI; the
     root and trailing-slash well-known paths serve the same document; the SDK's
     ProtectedResourceMetadata model accepts it.
  D. THE GATE with the feature on: a JWT-shaped token the API accepts passes and is the
     token forwarded to the API (never another); one the API refuses is a 401 with
     `error="invalid_token"` and the metadata pointer, carrying the API's actionable
     reason ("sign in with SSO once") but never its generic one; an atlas_svc_ token still
     passes; the shared fallback still never takes a JWT.

    pip install -r mcp/requirements.txt && python mcp/verify_oauth_resource.py
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

RESOURCE_RAW = "https://Atlas.Test:8446/mcp/"
RESOURCE = "https://atlas.test:8446/mcp"
ISSUER = "https://idp.test/realms/atlas"
SCOPES = "atlas.write"
META_URL = "https://atlas.test:8446/.well-known/oauth-protected-resource/mcp"



def _jwt_shaped(sub: str) -> str:
    """A JWT-SHAPED string, built at run time (no token literal in the repo): atlas-mcp only
    checks the shape; whether it is valid is the fake API's answer below."""
    import base64

    def seg(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return ".".join((seg(json.dumps({"alg": "RS256"}).encode()),
                     seg(json.dumps({"sub": sub}).encode()), seg(b"not-a-signature-" + sub.encode())))


JWT_OK = _jwt_shaped("kc-erin")
JWT_NOBODY = _jwt_shaped("kc-stranger")
JWT_BAD = _jwt_shaped("kc-bad")
SVC = "atlas_svc_aaaaaa_" + "r" * 43
NO_PERSON = "No Atlas account for this IdP identity yet: sign in to the board with SSO once, then retry."

failures: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def _env(extra: dict) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ATLAS_OAUTH_", "ATLAS_OIDC_", "ATLAS_MCP_OAUTH_"))}
    env.update({"ATLAS_BASE_URL": "https://atlas.invalid", "PYTHONIOENCODING": "utf-8"})
    env.pop("ATLAS_TOKEN", None)
    env.pop("ATLAS_MCP_ALLOW_SHARED_TOKEN", None)
    env.update(extra)
    return env


def _child(extra: dict, code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], cwd=str(HERE), env=_env(extra),
                          capture_output=True, text=True, timeout=120)


# --------------------------------------------------------------------------- #
# A. Feature off
# --------------------------------------------------------------------------- #
_OFF_CODE = r"""
import json, httpx
from fastapi.testclient import TestClient
from app import caller_auth, main
sent = []
def api(req):
    sent.append(req.headers.get("authorization"))
    return httpx.Response(200, json={})
caller_auth.API_TRANSPORT = httpx.MockTransport(api)
c = TestClient(main.app, base_url="http://localhost:8092")
r = c.post("/mcp/", json={})
r2 = c.post("/mcp/", json={}, headers={"Authorization": "Bearer %s"})
out = {"status": r.status_code, "www": r.headers.get("www-authenticate"), "body": r.content.decode(),
       "jwt_status": r2.status_code, "jwt_www": r2.headers.get("www-authenticate"), "sent": sent,
       "meta": [c.get(p).status_code for p in ("/.well-known/oauth-protected-resource",
                                               "/.well-known/oauth-protected-resource/mcp")]}
print("RESULT " + json.dumps(out))
""" % JWT_OK


def section_a() -> None:
    print("-- A. feature off: today's 401, byte for byte, and no metadata --")
    r = _child({"ATLAS_OIDC_ISSUER": ISSUER}, _OFF_CODE)   # SSO-only install: still off
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), None)
    check("the off-mode child ran", line is not None, (r.stderr or r.stdout)[-400:])
    if line is None:
        return
    out = json.loads(line[len("RESULT "):])
    today_body = json.dumps({"error": "unauthorized",
                             "detail": "send Authorization: Bearer atlas_svc_... (your own agent credential)"})
    check("no token: 401 with WWW-Authenticate exactly `Bearer realm=\"atlas-mcp\"`",
          out["status"] == 401 and out["www"] == 'Bearer realm="atlas-mcp"', str(out["www"]))
    check("...and today's body, byte for byte", out["body"] == today_body, out["body"])
    check("a JWT-shaped bearer is refused at the door with today's challenge",
          out["jwt_status"] == 401 and out["jwt_www"] == 'Bearer realm="atlas-mcp"', str(out))
    check("...and never reaches atlas-api", out["sent"] == [], str(out["sent"]))
    check("no metadata route: both well-known paths 404", out["meta"] == [404, 404], str(out["meta"]))


# --------------------------------------------------------------------------- #
# B. Settings
# --------------------------------------------------------------------------- #
def section_b() -> None:
    print("-- B. settings stop the process naming the variable --")
    code = "import app.settings as s; print('ON', s.OAUTH_RESOURCE is not None)"
    for label, extra, name in (
        ("set-but-blank resource", {"ATLAS_OAUTH_RESOURCE": " "}, "ATLAS_OAUTH_RESOURCE"),
        ("an http resource", {"ATLAS_OAUTH_RESOURCE": "http://a.test/mcp", "ATLAS_OIDC_ISSUER": ISSUER},
         "ATLAS_OAUTH_RESOURCE"),
        ("a resource without an issuer", {"ATLAS_OAUTH_RESOURCE": RESOURCE}, "ATLAS_OIDC_ISSUER"),
        ("an http issuer", {"ATLAS_OAUTH_RESOURCE": RESOURCE, "ATLAS_OIDC_ISSUER": "http://idp.test"},
         "ATLAS_OIDC_ISSUER"),
        ("scopes without a resource", {"ATLAS_MCP_OAUTH_SCOPES": "x"}, "ATLAS_MCP_OAUTH_SCOPES"),
        ("a blank scope list", {"ATLAS_OAUTH_RESOURCE": RESOURCE, "ATLAS_OIDC_ISSUER": ISSUER,
                                "ATLAS_MCP_OAUTH_SCOPES": ""}, "ATLAS_MCP_OAUTH_SCOPES"),
    ):
        r = _child(extra, code)
        check(f"{label}: refuses to start, naming {name}",
              r.returncode != 0 and "refuses to start" in r.stderr and name in r.stderr,
              (r.stderr or r.stdout)[-200:])
    r = _child({"ATLAS_OIDC_ISSUER": ISSUER}, code)
    check("ATLAS_OIDC_ISSUER alone leaves the feature off", r.returncode == 0 and "ON False" in r.stdout,
          r.stdout + r.stderr[-200:])
    r = _child({"ATLAS_OAUTH_RESOURCE": RESOURCE_RAW, "ATLAS_OIDC_ISSUER": ISSUER + "/"},
               "import app.settings as s; o = s.OAUTH_RESOURCE; print('R', o.resource, o.issuer, o.scopes)")
    check("on: the resource is canonicalised and the issuer kept as atlas-api keeps it",
          f"R {RESOURCE} {ISSUER} ()" in r.stdout, r.stdout + r.stderr[-200:])


# --------------------------------------------------------------------------- #
# C + D. Feature on, in this process
# --------------------------------------------------------------------------- #
def _load_on():
    os.environ.update(_env({"ATLAS_OAUTH_RESOURCE": RESOURCE_RAW, "ATLAS_OIDC_ISSUER": ISSUER,
                            "ATLAS_MCP_OAUTH_SCOPES": SCOPES}))
    for k in ("ATLAS_TOKEN", "ATLAS_MCP_ALLOW_SHARED_TOKEN"):
        os.environ.pop(k, None)
    sys.path.insert(0, str(HERE))
    import httpx
    from app import caller_auth, client as client_mod, main, oauth_resource
    return httpx, caller_auth, client_mod, main, oauth_resource


class FakeAPI:
    """atlas-api's answer to GET /api/me: one JWT accepted, one refused with the API's
    actionable reason, one with its generic body; the service token accepted."""

    def __init__(self, httpx):
        self.httpx = httpx
        self.seen = []

    def __call__(self, request):
        auth = request.headers.get("authorization", "")
        self.seen.append(auth)
        h = self.httpx
        if auth in ("Bearer " + JWT_OK, "Bearer " + SVC):
            return h.Response(200, json={"kind": "token"})
        if auth == "Bearer " + JWT_NOBODY:
            return h.Response(401, json={"error": "unauthorized", "detail": NO_PERSON})
        return h.Response(401, json={"error": "unauthorized", "detail": "Authentication is required."})


def section_c(httpx, caller_auth, main, oauth_resource) -> None:
    print("-- C. criterion 1: the 401 points at metadata that names the issuer --")
    from fastapi.testclient import TestClient
    api = FakeAPI(httpx)
    caller_auth.API_TRANSPORT = httpx.MockTransport(api)
    c = TestClient(main.app, base_url="http://localhost:8092")
    r = c.post("/mcp/", json={})
    www = r.headers.get("www-authenticate", "")
    check("no token: 401", r.status_code == 401, str(r.status_code))
    check("WWW-Authenticate: Bearer resource_metadata=<the RFC 9728 URL>, scope=<configured>",
          www == f'Bearer resource_metadata="{META_URL}", scope="{SCOPES}"', www)
    check("nothing was sent to atlas-api for a request with no token", api.seen == [], str(api.seen))

    url = www.split('resource_metadata="', 1)[-1].split('"', 1)[0]
    path = url.split("atlas.test:8446", 1)[-1]
    m = c.get(path)
    doc = m.json() if m.status_code == 200 else {}
    check("the advertised metadata URL is served: 200 JSON", m.status_code == 200, f"{path} {m.status_code}")
    check("it names the configured issuer as the authorization server",
          doc.get("authorization_servers") == [ISSUER], str(doc))
    check("resource is the canonical URI (exact string), bearer in the header only, scopes listed",
          doc.get("resource") == RESOURCE and doc.get("bearer_methods_supported") == ["header"]
          and doc.get("scopes_supported") == [SCOPES], str(doc))
    check("the same document at the root well-known path and the trailing-slash twin",
          c.get("/.well-known/oauth-protected-resource").json() == doc
          and c.get("/.well-known/oauth-protected-resource/mcp/").json() == doc)
    from mcp.server.auth.routes import build_resource_metadata_url
    from mcp.shared.auth import ProtectedResourceMetadata
    check("the URL is the SDK's own RFC 9728 path insertion", str(build_resource_metadata_url(RESOURCE)) == META_URL)
    try:
        ProtectedResourceMetadata.model_validate(doc)
        valid = True
    except Exception as exc:  # noqa: BLE001
        valid = repr(exc)
    check("the SDK's ProtectedResourceMetadata model accepts the document", valid is True, str(valid))
    check("the metadata is public and cacheable", m.headers.get("cache-control", "").startswith("public"))
    check("/health is untouched", c.get("/health").json() == {"status": "ok"})


def section_d(httpx, caller_auth, client_mod) -> None:
    print("-- D. the gate with the feature on --")
    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    api = FakeAPI(httpx)
    caller_auth.API_TRANSPORT = httpx.MockTransport(api)
    caller_auth._ok_cache.clear()
    gate = caller_auth.RequireCallerToken(inner)

    async def call(token):
        msgs = []
        scope = {"type": "http", "path": "/", "headers": [] if token is None else
                 [(b"authorization", ("Bearer " + token).encode())]}

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(m):
            msgs.append(m)
        await gate(scope, receive, send)
        start = msgs[0]
        headers = {k.decode(): v.decode() for k, v in start.get("headers", [])}
        body = msgs[1].get("body", b"").decode() if len(msgs) > 1 else ""
        return start["status"], headers, body

    status, _h, _b = asyncio.run(call(JWT_OK))
    check("a JWT the API accepts passes the door", status == 200 and calls == ["/"], f"{status} {calls}")
    check("...and the token checked is that caller's own", api.seen[-1] == "Bearer " + JWT_OK)
    with client_mod.as_caller(JWT_OK):
        forwarded = client_mod.resolve_credential("POST")
    check("the client forwards the JWT, unchanged, to atlas-api", forwarded == JWT_OK)

    status, h, body = asyncio.run(call(JWT_NOBODY))
    www = h.get("www-authenticate", "")
    check("a JWT the API refuses: 401 with error=invalid_token and the metadata pointer",
          status == 401 and www == f'Bearer error="invalid_token", resource_metadata="{META_URL}", scope="{SCOPES}"',
          www)
    check("...carrying the API's actionable reason", json.loads(body).get("detail") == NO_PERSON, body)
    status, h, body = asyncio.run(call(JWT_BAD))
    check("the API's generic refusal is not repeated: the gate's own sentence",
          status == 401 and "did not accept this token" in json.loads(body).get("detail", ""), body)
    n = len(api.seen)
    status, h, _b = asyncio.run(call("not-a-token"))
    check("junk is refused at the door, with the pointer, and never sent",
          status == 401 and "resource_metadata=" in h.get("www-authenticate", "") and len(api.seen) == n)
    status, _h, _b = asyncio.run(call(SVC))
    check("an atlas_svc_ token still passes", status == 200, str(status))
    check("the shared fallback token is still only ever a service token",
          client_mod.token_shape_ok(SVC) and not client_mod.token_shape_ok(JWT_OK))


def main() -> None:
    section_a()
    section_b()
    httpx, caller_auth, client_mod, main_mod, oauth_resource = _load_on()
    section_c(httpx, caller_auth, main_mod, oauth_resource)
    section_d(httpx, caller_auth, client_mod)
    print()
    if failures:
        print(f"FAILED — {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
