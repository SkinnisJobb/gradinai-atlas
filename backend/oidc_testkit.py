"""
A stub OpenID provider for the OIDC verifies (STORY-oss-13). NO NETWORK.

`StubIdP` answers discovery, JWKS and the token endpoint through an
`httpx.MockTransport`, which `identity.oidc.use_transport` routes every IdP call
through. Its RSA signing key is generated when the stub is built, so no key is
ever committed. It behaves like a strict IdP where that is what is being tested:
the token endpoint checks the client credential, the redirect URI and the PKCE
verifier against the challenge from the authorization request, and a code is
single use.

A test plays the browser and the user:

    stub = StubIdP()
    env = stub.env(tmpdir)                 # the ATLAS_* settings, secret file written
    ... set env, import app, identity.oidc.use_transport(stub.transport) ...
    r = client.get("/auth/oidc/start")     # 303 to stub.authorization_endpoint?...
    cb = stub.authorize(r.headers["location"], {"sub": "u1", "groups": [...]})
    r = client.get(cb)                     # the callback; 303 + session on success

Tampering knobs (`authorize(..., id_token_overrides=..., sign_with=..., alg=...)`,
`stub.refuse_token_endpoint`, `authorize(..., tamper_challenge=True)`) make each
refusal case one line in the verify.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.test/realms/atlas"
CLIENT_ID = "atlas-test-client"
REDIRECT_URI = "https://testserver/auth/oidc/callback"


def _b64url_uint(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def new_rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk_of(key, kid: str) -> dict:
    pub = key.public_key().public_numbers()
    return {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256",
            "n": _b64url_uint(pub.n), "e": _b64url_uint(pub.e)}


class StubIdP:
    def __init__(self, issuer: str = ISSUER, client_id: str = CLIENT_ID,
                 redirect_uri: str = REDIRECT_URI) -> None:
        self.issuer = issuer
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.client_secret = secrets.token_urlsafe(32)
        self.key = new_rsa_key()
        self.kid = "stub-key-1"
        self.authorization_endpoint = issuer + "/protocol/openid-connect/auth"
        self.token_endpoint = issuer + "/protocol/openid-connect/token"
        self.jwks_uri = issuer + "/protocol/openid-connect/certs"
        self.codes: dict = {}
        self.refuse_token_endpoint = False
        self.discovery_up = True
        self.token_requests: list = []
        self.transport = httpx.MockTransport(self._handle)

    # -- configuration for the app under test ------------------------------ #

    def env(self, tmpdir: str, **extra: str) -> dict:
        secret_path = os.path.join(tmpdir, "oidc_client_secret")
        with open(secret_path, "w", encoding="utf-8") as fh:
            fh.write(self.client_secret + "\n")
        env = {
            "ATLAS_AUTH_PROVIDERS": "local,oidc",
            "ATLAS_OIDC_ISSUER": self.issuer,
            "ATLAS_OIDC_CLIENT_ID": self.client_id,
            "ATLAS_OIDC_CLIENT_SECRET_FILE": secret_path,
            "ATLAS_OIDC_REDIRECT_URI": self.redirect_uri,
        }
        env.update(extra)
        return env

    # -- the IdP's HTTP surface --------------------------------------------- #

    def discovery(self) -> dict:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
            "jwks_uri": self.jwks_uri,
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
        }

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == self.issuer + "/.well-known/openid-configuration" and request.method == "GET":
            if not self.discovery_up:
                return httpx.Response(503)
            return httpx.Response(200, json=self.discovery())
        if url == self.jwks_uri and request.method == "GET":
            return httpx.Response(200, json={"keys": [jwk_of(self.key, self.kid)]})
        if url == self.token_endpoint and request.method == "POST":
            return self._token(request)
        return httpx.Response(404, json={"error": "not_found"})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.token_requests.append(form)
        if self.refuse_token_endpoint:
            return httpx.Response(500, json={"error": "server_error"})
        expected = "Basic " + base64.b64encode(
            ("%s:%s" % (self.client_id, self.client_secret)).encode()).decode()
        if request.headers.get("authorization") != expected:
            return httpx.Response(401, json={"error": "invalid_client"})
        if form.get("grant_type") != "authorization_code":
            return httpx.Response(400, json={"error": "unsupported_grant_type"})
        grant = self.codes.pop(form.get("code", ""), None)   # single use
        if grant is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if form.get("redirect_uri") != grant["redirect_uri"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        verifier = form.get("code_verifier", "")
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not verifier or challenge != grant["code_challenge"]:
            return httpx.Response(400, json={"error": "invalid_grant",
                                             "error_description": "PKCE verification failed"})
        return httpx.Response(200, json={
            "access_token": "stub-access-" + secrets.token_urlsafe(8),
            "token_type": "Bearer", "expires_in": 300,
            "refresh_token": "stub-refresh-" + secrets.token_urlsafe(8),
            "id_token": grant["id_token"]})

    # -- the user, at the IdP ----------------------------------------------- #

    def sign(self, claims: dict, *, key=None, kid: Optional[str] = None, alg: str = "RS256") -> str:
        headers = {"kid": kid if kid is not None else self.kid}
        signing_key = key if key is not None else self.key
        return jwt.encode(claims, signing_key, algorithm=alg, headers=headers)

    def authorize(self, location: str, claims: dict, *, id_token_overrides: Optional[dict] = None,
                  drop_nonce: bool = False, key=None, kid: Optional[str] = None, alg: str = "RS256",
                  raw_id_token: Optional[str] = None, tamper_challenge: bool = False,
                  state_override: Optional[str] = None) -> str:
        """Approve the authorization request at `location`; return the callback path."""
        parts = urlsplit(location)
        assert location.startswith(self.authorization_endpoint), location
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        now = int(time.time())
        body: dict[str, Any] = {"iss": self.issuer, "aud": self.client_id, "iat": now,
                                "exp": now + 300, "nonce": q.get("nonce")}
        body.update(claims)
        if drop_nonce:
            body.pop("nonce", None)
        if id_token_overrides:
            for k, v in id_token_overrides.items():
                if v is None:
                    body.pop(k, None)
                else:
                    body[k] = v
        token = raw_id_token if raw_id_token is not None else self.sign(body, key=key, kid=kid, alg=alg)
        code = secrets.token_urlsafe(24)
        challenge = q.get("code_challenge", "")
        if tamper_challenge:
            challenge = challenge[::-1]
        self.codes[code] = {"code_challenge": challenge, "redirect_uri": q.get("redirect_uri"),
                            "id_token": token}
        self.last_request = q
        state = q.get("state") if state_override is None else state_override
        return "/auth/oidc/callback?" + urlencode({"code": code, "state": state})


def write_group_map(tmpdir: str, mapping: dict) -> str:
    path = os.path.join(tmpdir, "group-map.yml")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"groups": mapping}, fh)   # JSON is YAML
    return path
