"""
Verification for IdP access tokens at the API (STORY-oss-25, ADR 0041 §9), against a STUB
IdP with no network: `oidc_testkit.StubIdP` answers discovery and JWKS through an httpx
MockTransport, and every token here is signed in-process with a key generated at test time.

The app runs as the work install would: ATLAS_AUTH_PROVIDERS=local,oidc,
ATLAS_OAUTH_RESOURCE=https://atlas.test/mcp, a scope map, and project RBAC on
(ATLAS_RBAC=projects, private by default) so criterion 4 is measured where it bites.

  A. SETTINGS: the rules of `settings.load_oauth` (off by default; set-but-blank; https
     only, canonical form; needs oidc; never Atlas's own client id as an audience; the
     scope map's grammar; a half-configured block is an error) and atlas-mcp's copy of
     the canonical form agrees.
  B. ACCEPTED (board criterion 2): a token from the stub IdP for the resource is a token
     principal acting as an agent account of (person, client): idp `oauth`, a member, a
     service row, linked to the person, created once with `user_created` and
     `bot_person_linked`; its write is in graph_event as that agent AND that person; the
     same client again is the same agent, another client is another; an extra audience
     (Entra's GUID) is accepted when configured.
  C. REFUSED (board criterion 3), each a 401 with a `login_fail` naming why: wrong
     audience, wrong issuer, expired, not yet valid, unsigned (alg none), HS256, another
     key, an unknown kid, a tampered payload, no exp, an ID token (nonce), no client
     id, a person Atlas does not know (401 with an actionable detail, and no account
     created), a disabled person, a person whose last SSO login is too old, a disabled
     agent; junk that is not a JWT.
  D. SCOPES (board criterion 4): no mapped scope is read-only (a write is 403); a write
     scope of an EDITOR writes; a write scope of a VIEWER is 403 (the project role caps
     it); an admin scope of a MEMBER carries no admin (a member never self-mints it);
     an owner's admin token deletes only where the owner's agent holds maintainer.
  E. atlas_svc_ UNCHANGED (board criterion 5): a minted token of the same person still
     reads and writes, and a JWT-shaped bearer with the feature OFF takes today's path
     (401, reason `bearer_invalid`, the one body).
  F. KEY ROTATION: a token signed by a new key the IdP rotated to is accepted after the
     JWKS refetch, and the person key follows the SSO preset (Entra tid+oid, tenant pinned).
  G. A PENDING PASSWORD CHANGE (TASK-1455): an SSO person whose account still carries
     `must_change_password` gets their token accepted with the scopes a person without the
     flag gets for the same claims, and their agent writes where their role allows; the
     flag stays. The rule is `identity.forced_change_applies` (sso: not held; a local
     account: held); a disabled person with the flag is still refused, and a local account
     with the flag is not an IdP person (no_person), as before.

Run:  python verify_idp_access_token.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:  # pragma: no cover - never a skip
    print(f"FATAL: {e}")
    sys.exit(1)

TEST_ORIGIN = "https://testserver"
TMP = tempfile.mkdtemp(prefix="atlas-idp-at-")
RESOURCE = "https://atlas.test/mcp"
ENTRA_GUID_AUD = "11111111-2222-3333-4444-555555555555"

for _k in [k for k in os.environ if k.startswith(("ATLAS_AUTH_PROVIDERS", "ATLAS_LOCAL_LOGIN",
                                                    "ATLAS_OIDC_", "ATLAS_OAUTH_"))]:
    os.environ.pop(_k)
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", TEST_ORIGIN)
os.environ["ATLAS_RBAC"] = "projects"
os.environ["ATLAS_DEFAULT_PROJECT_VISIBILITY"] = "private"
os.environ["ATLAS_SHARED_PROJECT_ROLE"] = "viewer"
os.environ.pop("ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER", None)

import oidc_testkit  # noqa: E402

STUB = oidc_testkit.StubIdP()
os.environ.update(STUB.env(
    TMP, ATLAS_OAUTH_RESOURCE=RESOURCE + "/",          # canonicalised: the slash is dropped
    ATLAS_OAUTH_AUDIENCES=ENTRA_GUID_AUD,
    ATLAS_OAUTH_SCOPE_MAP="atlas.write=write,atlas.admin=admin,atlas.read=read"))

import identity.oidc  # noqa: E402

identity.oidc.use_transport(STUB.transport)

import jwt  # noqa: E402
from sqlalchemy import select  # noqa: E402

import app  # noqa: E402
import auth  # noqa: E402
import identity  # noqa: E402
import models_auth  # noqa: E402
import settings  # noqa: E402
from identity import access_token  # noqa: E402
from identity.base import LoginRefused  # noqa: E402
from models_auth import AuthEvent, AuthUser, AuthUserIdpState  # noqa: E402
from models_graph import GraphEvent  # noqa: E402
from rbac_testkit import bind, check, finish, mint, mk_edge, mk_node, mk_user  # noqa: E402


def client(token: str | None = None):
    from fastapi.testclient import TestClient
    c = TestClient(app.app, base_url=TEST_ORIGIN)
    if token:
        c.headers["Authorization"] = "Bearer " + token
    return c


def reset_limits() -> None:
    for ip in ("testclient", "127.0.0.1", ""):
        auth._IP_LIMITER.reset(auth._login_key(ip))
        auth._IP_LIMITER.reset(auth._bearer_key(ip))


def at(sub: str, *, client_id: str = "claude-code", scope: str | None = "atlas.write",
       drop: tuple = (), key=None, kid=None, alg: str = "RS256", **over) -> str:
    """An access token from the stub IdP for the resource, with overrides."""
    now = int(time.time())
    body = {"iss": STUB.issuer, "aud": RESOURCE, "sub": sub, "iat": now, "nbf": now,
            "exp": now + 300, "azp": client_id, "jti": str(uuid.uuid4())}
    if scope is not None:
        body["scope"] = scope
    body.update(over)
    for k in drop:
        body.pop(k, None)
    if alg == "none":
        # Built by hand, not with PyJWT: an UNSIGNED token, header + payload + empty signature,
        # exactly what an attacker would send.
        def seg(obj) -> str:
            return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
        return "%s.%s." % (seg({"alg": "none", "typ": "JWT", "kid": STUB.kid}), seg(body))
    return STUB.sign(body, key=key, kid=kid, alg=alg)


def mk_person(uid: str, sub: str, role: str = "member", *, login_days_ago: float = 0.0,
              must_change: bool = False) -> None:
    with app.session() as s:
        s.add(AuthUser(id=uid, idp="oidc", subject=sub, name=sub.title(), role=role,
                       is_service=False, must_change_password=must_change, password_hash=None,
                       created_at=datetime.now(timezone.utc)))
        s.flush()
        s.add(AuthUserIdpState(user_id=uid, break_glass=False, last_idp_login_at=(
            datetime.now(timezone.utc) - timedelta(days=login_days_ago))))
        s.commit()


def last_fail_reason() -> str:
    with app.session() as s:
        row = s.scalars(select(AuthEvent).where(AuthEvent.event == "login_fail")
                        .order_by(AuthEvent.id.desc()).limit(1)).first()
        return row.reason if row is not None else ""


def agents_of(person_id: str) -> list:
    with app.session() as s:
        return list(s.scalars(select(AuthUser).where(AuthUser.person_user_id == person_id,
                                                     AuthUser.idp == "oauth")).all())


P = {"erin": "u-at-erin", "vera": "u-at-vera", "olga": "u-at-olga", "dora": "u-at-dora",
     "stale": "u-at-stale", "pend": "u-at-pend", "dpend": "u-at-dpend", "lpend": "u-at-lpend"}


def build() -> None:
    mk_person(P["erin"], "kc-erin")                    # editor on pa
    mk_person(P["vera"], "kc-vera")                    # viewer on pa
    mk_person(P["olga"], "kc-olga", "owner")           # instance owner, agent bound maintainer
    mk_person(P["dora"], "kc-dora")                    # disabled below
    mk_person(P["stale"], "kc-stale", login_days_ago=400)
    mk_person(P["pend"], "kc-pend", must_change=True)     # editor on pa; flag set (TASK-1455)
    mk_person(P["dpend"], "kc-dpend", must_change=True)   # disabled below, flag set
    with app.session() as s:
        # A LOCAL account with the flag, whose username equals a token's `sub` below.
        s.add(AuthUser(id=P["lpend"], idp="local", subject="kc-lpend", name="Lpend",
                       role="member", is_service=False, must_change_password=True,
                       password_hash=None, created_at=datetime.now(timezone.utc)))
        s.commit()
    with app.session() as s:
        for nid, t in (("pa", "project"), ("pa-cap", "capability"), ("pa-del1", "capability"),
                       ("pa-del2", "capability")):
            mk_node(s, nid, t)
        s.flush()
        for child in ("pa-cap", "pa-del1", "pa-del2"):
            mk_edge(s, "pa", child)
        dora = s.get(AuthUser, P["dora"])
        dora.disabled_at = datetime.now(timezone.utc)
        s.get(AuthUser, P["dpend"]).disabled_at = datetime.now(timezone.utc)
        s.commit()
    bind("pa", "user", P["erin"], "editor")
    bind("pa", "user", P["vera"], "viewer")
    bind("pa", "user", P["dora"], "editor")
    bind("pa", "user", P["pend"], "editor")


# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. settings --")
    base = {"ATLAS_AUTH_PROVIDERS": "local,oidc", "ATLAS_OIDC_ISSUER": STUB.issuer,
            "ATLAS_OIDC_CLIENT_ID": STUB.client_id,
            "ATLAS_OIDC_CLIENT_SECRET_FILE": "/run/secrets/x",
            "ATLAS_OIDC_REDIRECT_URI": "https://testserver/auth/oidc/callback"}

    def err(env) -> str:
        try:
            settings.load_oauth(env)
        except settings.SettingsError as exc:
            return str(exc)
        return ""

    check("unset: off, today", settings.load_oauth({}) == settings.OAuthSettings()
          and not settings.load_oauth({}).enabled)
    on = settings.load_oauth(dict(base, ATLAS_OAUTH_RESOURCE="HTTPS://Atlas.Test:8443/mcp/"))
    check("on: canonical resource (lowercase scheme/host, no trailing slash), the only audience",
          on.resource == "https://atlas.test:8443/mcp" and on.audiences == (on.resource,), str(on))
    check("the running app canonicalised its resource too",
          identity.OAUTH.resource == RESOURCE and identity.OAUTH.audiences == (RESOURCE, ENTRA_GUID_AUD),
          str(identity.OAUTH))
    check("set-but-blank is an error naming it",
          "ATLAS_OAUTH_RESOURCE" in err(dict(base, ATLAS_OAUTH_RESOURCE="  ")))
    check("http is refused", "https" in err(dict(base, ATLAS_OAUTH_RESOURCE="http://atlas.test/mcp")))
    check("a query is refused", err(dict(base, ATLAS_OAUTH_RESOURCE="https://a.test/mcp?x=1")) != "")
    check("needs oidc in ATLAS_AUTH_PROVIDERS",
          "oidc" in err({"ATLAS_OAUTH_RESOURCE": RESOURCE}))
    check("Atlas's own OIDC client id is never an accepted audience",
          "ID token" in err(dict(base, ATLAS_OAUTH_RESOURCE=RESOURCE,
                                 ATLAS_OAUTH_AUDIENCES=STUB.client_id)))
    check("a scope map entry without a known tier is refused",
          "ATLAS_OAUTH_SCOPE_MAP" in err(dict(base, ATLAS_OAUTH_RESOURCE=RESOURCE,
                                              ATLAS_OAUTH_SCOPE_MAP="atlas.write=superuser")))
    check("the scope map parses in order",
          settings.load_oauth(dict(base, ATLAS_OAUTH_RESOURCE=RESOURCE,
                                   ATLAS_OAUTH_SCOPE_MAP="A.W=write, A.X = Admin")).scope_map
          == (("A.W", "write"), ("A.X", "admin")))
    check("audiences or a scope map without the resource are an error",
          "ATLAS_OAUTH_RESOURCE" in err(dict(base, ATLAS_OAUTH_AUDIENCES="x"))
          and "ATLAS_OAUTH_RESOURCE" in err(dict(base, ATLAS_OAUTH_SCOPE_MAP="a=read")))

    # atlas-mcp's copy of the canonical form (mcp/app/settings.py cannot import this module).
    # Loaded by path under another name: backend's `app` is the API, not atlas-mcp's package.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "atlas_mcp_settings", HERE.parent / "mcp" / "app" / "settings.py")
    mcp_settings = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mcp_settings)
    for raw in ("HTTPS://Atlas.Test:8443/mcp/", "https://a.test", "https://a.test/x/y/"):
        got = mcp_settings.load_oauth_resource({"ATLAS_OAUTH_RESOURCE": raw,
                                                "ATLAS_OIDC_ISSUER": STUB.issuer + "/"})
        check(f"atlas-mcp canonicalises {raw!r} as atlas-api does",
              got.resource == settings.canonical_resource(raw) and got.issuer == STUB.issuer,
              f"{got.resource} vs {settings.canonical_resource(raw)}")


def section_b() -> dict:
    print("\n-- B. a token from the IdP for this resource is accepted, as the person's agent --")
    reset_limits()
    tok = at("kc-erin")
    r = client(tok).get("/api/me")
    me = r.json() if r.status_code == 200 else {}
    check("GET /api/me with the IdP token: 200", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
    check("a token principal of a service account, member, scopes read+write",
          me.get("kind") == "token" and me.get("is_service") is True and me.get("role") == "member"
          and me.get("scopes") == ["read", "write"], str(me))
    agents = agents_of(P["erin"])
    check("exactly one agent account, created on first use", len(agents) == 1, str(len(agents)))
    agent = agents[0] if agents else None
    check("it is idp=oauth, a service row, member, no password, linked to the person, named for the client",
          agent is not None and agent.is_service and agent.role == "member" and agent.password_hash is None
          and agent.person_user_id == P["erin"] and agent.label == "oauth:claude-code"
          and agent.subject == access_token.agent_subject(P["erin"], "claude-code")
          and me.get("user_id") == agent.id, str(agent and (agent.subject, agent.label)))
    with app.session() as s:
        evs = [e.event for e in s.scalars(select(AuthEvent).where(
            AuthEvent.user_id == (agent.id if agent else ""))).all()]
    check("user_created and bot_person_linked recorded", {"user_created", "bot_person_linked"} <= set(evs),
          str(evs))

    r = client(tok).patch("/api/node/pa-cap", json={"summary": "written with an IdP token"})
    check("the agent of an editor writes in the project: 200", r.status_code == 200,
          f"{r.status_code} {r.text[:160]}")
    with app.session() as s:
        ev = s.scalars(select(GraphEvent).where(GraphEvent.node_id == "pa-cap")
                       .order_by(GraphEvent.id.desc()).limit(1)).first()
    check("graph_event attributes it to the agent account AND the person",
          ev is not None and ev.actor_kind == "token" and ev.actor_user_id == (agent.id if agent else None)
          and ev.responsible_user_id == P["erin"] and ev.actor_token_id is None,
          str(ev and (ev.actor_kind, ev.actor_user_id, ev.responsible_user_id)))

    client(at("kc-erin")).get("/api/me")
    check("a new token of the same client is the same agent", len(agents_of(P["erin"])) == 1)
    r = client(at("kc-erin", client_id="vscode")).get("/api/me")
    check("another OAuth client is another agent of the same person",
          r.status_code == 200 and len(agents_of(P["erin"])) == 2, f"{r.status_code}")
    r = client(at("kc-erin", aud=[ENTRA_GUID_AUD])).get("/api/me")
    check("an ATLAS_OAUTH_AUDIENCES value is accepted as the audience (Entra v2's GUID)",
          r.status_code == 200, f"{r.status_code}")
    r = client(at("kc-erin", aud=["https://other.test", RESOURCE])).get("/api/me")
    check("an aud list containing the resource is accepted", r.status_code == 200, f"{r.status_code}")
    r = client(at("kc-erin", drop=("azp",), client_id="x", appid="entra-v1-client")).get("/api/me")
    check("the client is read from client_id / azp / appid", r.status_code == 200, f"{r.status_code}")
    return {"agent": agent}


def refused(label: str, token: str, reason: str, *, detail_has: str | None = None) -> None:
    reset_limits()
    r = client(token).get("/api/me")
    www = r.headers.get("www-authenticate", "")
    ok = r.status_code == 401 and www.startswith('Bearer error="invalid_token"')
    if detail_has is None:
        ok = ok and r.json() == {"error": "unauthorized", "detail": "Authentication is required."}
    else:
        ok = ok and detail_has in r.json().get("detail", "") and detail_has in www
    got = last_fail_reason()
    check(f"{label}: 401, login_fail oauth:{reason}", ok and got == "oauth:" + reason,
          f"{r.status_code} {got} {r.text[:120]}")


def section_c() -> None:
    print("\n-- C. every broken token is refused with a 401 --")
    now = int(time.time())
    other_key = oidc_testkit.new_rsa_key()
    refused("wrong audience", at("kc-erin", aud="https://elsewhere.test/mcp"), "audience")
    refused("an ID token's audience (Atlas's client id)", at("kc-erin", aud=STUB.client_id), "audience")
    refused("wrong issuer", at("kc-erin", iss="https://evil.test/realms/atlas"), "issuer")
    refused("expired (past the leeway)", at("kc-erin", exp=now - 120, iat=now - 600, nbf=now - 600), "expired")
    refused("not yet valid", at("kc-erin", nbf=now + 600), "not_yet_valid")
    refused("unsigned (alg none)", at("kc-erin", alg="none"), "alg_refused")
    refused("HS256", at("kc-erin", alg="HS256", key=b"k" * 48), "alg_refused")
    refused("signed by another key, same kid", at("kc-erin", key=other_key), "signature")
    refused("an unknown kid", at("kc-erin", key=other_key, kid="nobody"), "jwks_no_matching_key")
    good = at("kc-erin")
    head, body, sig = good.split(".")
    other_body = at("kc-olga", scope="atlas.admin").split(".")[1]
    refused("a tampered payload under a genuine signature", ".".join((head, other_body, sig)),
            "signature")
    refused("no exp", at("kc-erin", drop=("exp",)), "invalid:MissingRequiredClaimError")
    refused("an ID token (nonce)", at("kc-erin", nonce="n-123"), "id_token_presented")
    refused("no client id", at("kc-erin", drop=("azp",)), "client_id_missing")

    n_before = len(agents_of("nobody"))
    with app.session() as s:
        users_before = len(s.scalars(select(AuthUser)).all())
    refused("a person Atlas has no account for", at("kc-stranger"), "no_person",
            detail_has="sign in to the board with SSO")
    with app.session() as s:
        users_after = len(s.scalars(select(AuthUser)).all())
    check("...and neither a person nor an agent was created", users_after == users_before
          and n_before == 0, f"{users_before} -> {users_after}")
    refused("a disabled person", at("kc-dora"), "person_disabled")
    refused("a person whose last SSO login is older than ATLAS_OIDC_REVALIDATE_DAYS",
            at("kc-stale"), "person_revalidation_due", detail_has="SSO again")
    reset_limits()
    client(at("kc-vera", client_id="to-disable")).get("/api/me")
    with app.session() as s:
        row = s.scalar(select(AuthUser).where(AuthUser.subject == access_token.agent_subject(
            P["vera"], "to-disable")))
        row.disabled_at = datetime.now(timezone.utc)
        s.commit()
    refused("a disabled agent (that client, for that person)", at("kc-vera", client_id="to-disable"),
            "agent_disabled")
    reset_limits()
    r = client(at("kc-vera", client_id="claude-code", scope=None)).get("/api/me")
    check("...while the same person's other client still works", r.status_code == 200, f"{r.status_code}")
    reset_limits()
    r = client("not.a-jwt").get("/api/me")
    check("a non-JWT, non-atlas_svc_ bearer: 401", r.status_code == 401, str(r.status_code))
    with app.session() as s:
        stored = [e.detail or "" for e in s.scalars(select(AuthEvent)).all()] + \
                 [e.reason or "" for e in s.scalars(select(AuthEvent)).all()]
    check("no token is written to auth_event", not any(good[:40] in x or sig[:20] in x for x in stored))


def section_d() -> None:
    print("\n-- D. effective permission = mapped scope cap AND the person's project role --")
    reset_limits()
    r = client(at("kc-erin", client_id="read-only", scope="openid profile")).patch(
        "/api/node/pa-cap", json={"summary": "x"})
    check("no mapped scope: read-only, a write is 403 missing scope write",
          r.status_code == 403 and "write" in r.text, f"{r.status_code} {r.text[:120]}")
    r = client(at("kc-erin", client_id="read-only", scope="openid")).get("/api/node/pa-cap")
    check("...and it reads", r.status_code == 200, f"{r.status_code}")
    r = client(at("kc-erin", client_id="scp-list", scope=None, scp="atlas.write")).patch(
        "/api/node/pa-cap", json={"summary": "via scp"})
    check("Entra's `scp` claim maps the same way: the editor's write scope writes", r.status_code == 200,
          f"{r.status_code} {r.text[:120]}")
    r = client(at("kc-vera", client_id="claude-code")).patch("/api/node/pa-cap", json={"summary": "v"})
    check("a VIEWER's IdP token with a write scope cannot write: 403 naming editor",
          r.status_code == 403 and r.json().get("needed_role") == "editor", f"{r.status_code} {r.text[:160]}")
    r = client(at("kc-vera", client_id="claude-code")).get("/api/node/pa-cap")
    check("...and still reads", r.status_code == 200, f"{r.status_code}")
    tok = at("kc-erin", client_id="admin-ask", scope="atlas.admin")
    me = client(tok).get("/api/me").json()
    check("an admin scope of a MEMBER carries no admin (a member never self-mints it)",
          me.get("scopes") == ["read", "write"], str(me.get("scopes")))
    r = client(tok).delete("/api/node/pa-del1?cascade=1")
    check("...so a delete is 403 missing scope admin", r.status_code == 403 and "admin" in r.text,
          f"{r.status_code} {r.text[:120]}")
    olga_tok = at("kc-olga", client_id="olga-cli", scope="atlas.admin")
    me = client(olga_tok).get("/api/me").json()
    check("an OWNER's admin scope carries read, write, admin", me.get("scopes") == ["read", "write", "admin"],
          str(me.get("scopes")))
    r = client(olga_tok).delete("/api/node/pa-del1?cascade=1")
    check("...but her agent inherits no role from her instance role: 404 on a private project",
          r.status_code == 404, f"{r.status_code} {r.text[:120]}")
    bind("pa", "user", me.get("user_id"), "maintainer")
    r = client(olga_tok).delete("/api/node/pa-del1?cascade=1")
    check("bound maintainer, the owner's admin-scoped agent deletes", r.status_code == 200,
          f"{r.status_code} {r.text[:120]}")


def section_e() -> None:
    print("\n-- E. atlas_svc_ tokens unchanged; feature off is today's path --")
    mk_user("u-at-erin-bot", "bot-at-erin", is_service=True, person=P["erin"])
    svc = mint("u-at-erin-bot", ["read", "write"], "at-erin-svc")
    reset_limits()
    r = client(svc).get("/api/me")
    check("a minted atlas_svc_ token of the same person reads", r.status_code == 200
          and r.json().get("username") == "bot-at-erin", f"{r.status_code}")
    r = client(svc).patch("/api/node/pa-cap", json={"summary": "via atlas_svc_"})
    check("...and writes", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    saved = identity.OAUTH
    identity.OAUTH = settings.OAuthSettings()
    try:
        reset_limits()
        r = client(at("kc-erin")).get("/api/me")
        check("feature OFF: a valid IdP token is today's 401, the one body, reason bearer_invalid",
              r.status_code == 401 and r.json() == {"error": "unauthorized",
                                                    "detail": "Authentication is required."}
              and r.headers.get("www-authenticate") == 'Bearer error="invalid_token"'
              and last_fail_reason() == "bearer_invalid", f"{r.status_code} {last_fail_reason()}")
        r = client(svc).get("/api/me")
        check("feature OFF: the atlas_svc_ token is untouched", r.status_code == 200)
    finally:
        identity.OAUTH = saved


def section_f() -> None:
    print("\n-- F. key rotation, and the person key follows the SSO preset --")
    provider = identity.provider("oidc")
    old_key, old_kid = STUB.key, STUB.kid
    STUB.key, STUB.kid = oidc_testkit.new_rsa_key(), "stub-key-2"
    try:
        provider._jwks_at -= 3600          # the minimum refresh interval has passed
        reset_limits()
        r = client(at("kc-erin")).get("/api/me")
        check("a token signed by the rotated-in key is accepted after one JWKS refetch",
              r.status_code == 200, f"{r.status_code} {last_fail_reason()}")
        reset_limits()
        r = client(at("kc-erin", key=old_key, kid=old_kid)).get("/api/me")
        check("the retired key no longer verifies", r.status_code == 401, f"{r.status_code}")
    finally:
        STUB.key, STUB.kid = old_key, old_kid
        provider._jwks_at -= 3600

    tid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    entra = settings.OidcSettings(issuer="https://login.microsoftonline.com/%s/v2.0" % tid,
                                  client_id="x", preset="entra", entra_tenant_id=tid)
    oid = "0F0F0F0F-1111-2222-3333-444444444444"
    check("entra: the access token's person is its oid, lowercased (as at SSO login)",
          identity.oidc.subject_of(entra, {"tid": tid, "oid": oid, "sub": "pairwise"}) == oid.lower())
    try:
        identity.oidc.subject_of(entra, {"tid": "ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee", "oid": oid})
        other_tenant = ""
    except LoginRefused as exc:
        other_tenant = exc.reason
    check("entra: another tenant's token is refused", other_tenant == "entra_tenant_mismatch", other_tenant)


def section_g() -> None:
    print("\n-- G. a pending password change does not refuse an IdP token (TASK-1455) --")
    reset_limits()
    with app.session() as s:
        pend = s.get(AuthUser, P["pend"])
        lpend = s.get(AuthUser, P["lpend"])
        rule = (identity.forced_change_applies(pend, identity.AUTH_SSO),
                identity.forced_change_applies(pend, identity.AUTH_PASSWORD),
                identity.forced_change_applies(lpend, identity.AUTH_SSO))
    check("the rule: an SSO credential of a flagged SSO person is not held; a password one "
          "is; a flagged local account always is", rule == (False, True, True), str(rule))
    twin = client(at("kc-erin", client_id="pend-twin")).get("/api/me")
    r = client(at("kc-pend", client_id="pend-twin")).get("/api/me")
    me = r.json() if r.status_code == 200 else {}
    check("an SSO person with must_change_password: the IdP token is accepted (200)",
          r.status_code == 200, f"{r.status_code} {r.text[:160]} {last_fail_reason()}")
    check("...as their agent, with the scopes a person without the flag gets for the same "
          "claims (read+write), never admin",
          twin.status_code == 200 and me.get("kind") == "token" and me.get("is_service") is True
          and me.get("scopes") == twin.json().get("scopes") == ["read", "write"], str(me))
    agents = agents_of(P["pend"])
    check("...one agent account, linked to the person", len(agents) == 1
          and agents[0].person_user_id == P["pend"] and me.get("user_id") == agents[0].id,
          str(len(agents)))
    w = client(at("kc-pend", client_id="pend-twin")).patch(
        "/api/node/pa-cap", json={"summary": "written by a person with a pending password"})
    check("...and it writes where the person's project role allows (editor: 200)",
          w.status_code == 200, f"{w.status_code} {w.text[:160]}")
    with app.session() as s:
        still = s.get(AuthUser, P["pend"]).must_change_password
    check("...the flag stays on the account (the token does not clear it)", still is True)
    refused("a DISABLED person with the flag: still person_disabled", at("kc-dpend"), "person_disabled")
    reset_limits()
    r = client(at("kc-lpend")).get("/api/me")
    check("a LOCAL account with the flag is not an IdP person: 401 no_person, as before",
          r.status_code == 401 and last_fail_reason() == "oauth:no_person",
          f"{r.status_code} {last_fail_reason()}")


def main() -> None:
    app.startup()
    build()
    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    section_f()
    section_g()
    finish()


if __name__ == "__main__":
    main()
