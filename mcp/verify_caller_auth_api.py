"""Prove caller auth end to end against the REAL atlas-api and a real Postgres.

verify_caller_auth.py proves what atlas-mcp SENDS, against a fake API. This proves
what happens when it arrives: the whole MCP server (gate, FastMCP, tools, client)
is driven over HTTP, and its calls land in the real `backend/app.py`, in process,
against a disposable Postgres from `backend/testdb.py`. So every refusal below is
atlas-api's own `WRITE_SCOPE_ROUTES` answering, and every attribution is a real
`graph_event` row. Nothing about scopes is simulated.

  A. no token, or a token atlas-api never issued: HTTP 401 at the MCP, no tool ran.
  B. a read-scoped agent credential can read, and cannot write or delete.
  C. a write-scoped credential's write is attributed, in graph_event, to THAT
     credential and, through ADR 0034's link, to the person behind it -- not to a
     shared bot.
  D. delete needs `admin`, from the caller: write is refused, admin succeeds.
  E. a token revoked INSIDE the door's 60 s cache (caller_auth.py) still passes
     the door, and atlas-api refuses its very next read and write: both come
     back `unauthorized`, and no graph_event row is written (TASK-1347).

Needs Docker (for the disposable Postgres) and both requirement files:
    pip install -r backend/requirements.txt -r mcp/requirements.txt
    python mcp/verify_caller_auth_api.py

Same convention as the other verify_*.py: one [PASS]/[FAIL] line per claim, exit 1
if any failed.
"""

import base64
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
sys.path.insert(0, str(BACKEND))

import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:  # no skip path, ever
    print(f"FATAL: {e}")
    sys.exit(1)

TEST_ORIGIN = "https://testserver"
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", TEST_ORIGIN)
# atlas-mcp's side: it calls the API at an origin the API accepts Bearer on.
os.environ["ATLAS_BASE_URL"] = TEST_ORIGIN
os.environ["ATLAS_MCP_ENABLE_DELETE"] = "1"
os.environ.pop("ATLAS_TOKEN", None)
os.environ.pop("ATLAS_MCP_ALLOW_SHARED_TOKEN", None)

import time  # noqa: E402

from sqlalchemy import func, select  # noqa: E402

import app as api_app  # noqa: E402  (backend/app.py: it owns the name `app`)
import auth  # noqa: E402
from models_auth import AuthServiceToken, AuthUser  # noqa: E402
from models_graph import GraphEvent  # noqa: E402


def _load_mcp_package():
    """mcp/app/ under another name: backend/app.py already holds `app`."""
    spec = importlib.util.spec_from_file_location(
        "atlas_mcp_app", HERE / "app" / "__init__.py",
        submodule_search_locations=[str(HERE / "app")])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["atlas_mcp_app"] = pkg
    spec.loader.exec_module(pkg)
    import importlib as il
    return (il.import_module("atlas_mcp_app.main"), il.import_module("atlas_mcp_app.client"),
            il.import_module("atlas_mcp_app.caller_auth"))


mcp_main, mcp_client, mcp_gate = _load_mcp_package()

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

failures: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


ALEX_ID = "u-ca-alex"          # a person, member
AGENT_ID = "u-ca-alex-agent"    # alex's agent (ADR 0034)
OWNER_ID = "u-ca-owner"          # a person, owner
OWNER_AGENT_ID = "u-ca-owner-agent"


def mk_user(uid, username, role="member", is_service=False, person=None) -> None:
    with api_app.session() as s:
        if s.get(AuthUser, uid) is None:
            s.add(AuthUser(id=uid, subject=username, name=username.title(), role=role,
                           is_service=is_service, must_change_password=False,
                           password_hash=None, person_user_id=person,
                           created_at=datetime.now(timezone.utc)))
            s.commit()


def mint(owner_id: str, scopes: list, label: str):
    with api_app.session() as s:
        raw, row = auth.mint_service_token(s, owner=s.get(AuthUser, owner_id),
                                           scopes=scopes, label=label)
        s.commit()
        return raw, row.id


def revoke(token_id: str) -> None:
    """Stamp revoked_at, as `admin token revoke` does (backend/admin.py)."""
    with api_app.session() as s:
        s.get(AuthServiceToken, token_id).revoked_at = auth._now()
        s.commit()


def event_count() -> int:
    with api_app.session() as s:
        return s.scalar(select(func.count()).select_from(GraphEvent))


def api_me_status(token: str) -> int:
    """atlas-api's own verdict on a token, asked directly (not through the door)."""
    # No `with`: entering a TestClient runs the app's startup, and ATLAS_RESEED=1
    # would then reseed the graph mid-test.
    api = TestClient(api_app.app, base_url=TEST_ORIGIN)
    return api.get("/api/me", headers={"Authorization": "Bearer " + token}).status_code


def events_for(entity_id: str, action: str) -> list:
    with api_app.session() as s:
        return list(s.scalars(select(GraphEvent).where(
            GraphEvent.entity_id == entity_id, GraphEvent.action == action)
            .order_by(GraphEvent.id)).all())


# ---- MCP over HTTP ------------------------------------------------------- #
def rpc(http, token, method, params=None, sid=None, rid=1):
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if sid:
        headers["mcp-session-id"] = sid
    body = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        body["id"] = rid
    if params is not None:
        body["params"] = params
    r = http.post("/mcp/", json=body, headers=headers)
    msg = None
    if r.status_code == 200 and r.text.strip():
        if r.headers.get("content-type", "").startswith("text/event-stream"):
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    msg = json.loads(line[5:].strip())
        else:
            msg = r.json()
    return r, msg


def session(http, token):
    r, _ = rpc(http, token, "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "verify_caller_auth_api", "version": "0"}})
    sid = r.headers.get("mcp-session-id")
    if r.status_code == 200:
        rpc(http, token, "notifications/initialized", sid=sid, rid=None)
    return r, sid


def call(http, token, sid, name, arguments):
    r, msg = rpc(http, token, "tools/call", {"name": name, "arguments": arguments}, sid=sid, rid=9)
    if msg is None:
        return r, None
    content = (msg.get("result") or {}).get("content") or [{}]
    try:
        return r, json.loads(content[0].get("text") or "null")
    except ValueError:
        return r, {"raw": content}


DENIED = ("forbidden", "http_403")


def err_type(out) -> str:
    return ((out or {}).get("error") or {}).get("type") or ""


def main() -> None:
    api_app.startup()  # schema + seed, the way the other backend verify scripts boot it
    mk_user(ALEX_ID, "ca-alex")
    mk_user(AGENT_ID, "bot-ca-alex", is_service=True, person=ALEX_ID)
    mk_user(OWNER_ID, "ca-owner", role="owner")
    mk_user(OWNER_AGENT_ID, "bot-ca-owner", is_service=True, person=OWNER_ID)
    t_read, _ = mint(AGENT_ID, ["read"], "ca-read")
    t_write, t_write_id = mint(AGENT_ID, ["read", "write"], "ca-write")
    t_admin, _ = mint(OWNER_AGENT_ID, ["read", "write", "admin"], "ca-admin")
    t_forged = "atlas_svc_0a0a0a_" + "x" * 43

    # Point atlas-mcp at the in-process API: the door check (async) and the tools
    # (sync) both, so no call leaves the process and none is simulated.
    mcp_gate.API_TRANSPORT = httpx.ASGITransport(app=api_app.app)
    mcp_client.client._client._transport = TestClient(api_app.app, base_url=TEST_ORIGIN)._transport

    with TestClient(mcp_main.app, base_url="http://localhost:8092") as http:
        print("-- A. no token, or a forged one: 401 at the MCP, nothing runs --")
        r, _ = session(http, None)
        check("initialize with no token: HTTP 401", r.status_code == 401, str(r.status_code))
        r, _ = session(http, t_forged)
        check("initialize with a token atlas-api never issued: HTTP 401",
              r.status_code == 401, f"{r.status_code} {r.text[:120]}")
        r, _ = rpc(http, None, "tools/call", {"name": "atlas_node_write",
                                              "arguments": {"id": "CA-NOAUTH", "type": "project",
                                                            "name": "x"}})
        check("a tools/call with no token: HTTP 401", r.status_code == 401, str(r.status_code))
        check("...and nothing was written", events_for("CA-NOAUTH", "node.create") == [])

        print("-- B. a read-scoped credential reads, and cannot write or delete --")
        r, sid = session(http, t_read)
        check("initialize with a real read token: 200", r.status_code == 200 and bool(sid),
              f"{r.status_code} {r.text[:160]}")
        r, out = call(http, t_read, sid, "atlas_health", {})
        check("atlas_health as the read token: ok", (out or {}).get("ok") is True, str(out)[:200])
        r, out = call(http, t_read, sid, "atlas_node_write",
                      {"id": "CA-BY-READER", "type": "project", "name": "Should not exist"})
        check("atlas_node_write with read scope: forbidden by atlas-api",
              err_type(out) == "forbidden", str(out)[:240])
        check("...and no node.create event exists", events_for("CA-BY-READER", "node.create") == [])

        print("-- C. a write is attributed to the caller's credential and its person --")
        r, sid_w = session(http, t_write)
        r, out = call(http, t_write, sid_w, "atlas_node_write",
                      {"id": "CA-BY-ALEX-AGENT", "type": "project", "name": "Alex's agent wrote this"})
        check("atlas_node_write with write scope: ok", (out or {}).get("ok") is True, str(out)[:240])
        evs = events_for("CA-BY-ALEX-AGENT", "node.create")
        e = evs[0] if evs else None
        check("one node.create event was recorded", len(evs) == 1, str(len(evs)))
        if e is not None:
            check("actor is the caller's agent account, not a shared bot",
                  e.actor_user_id == AGENT_ID and e.actor_kind == "token", f"{e.actor_user_id} {e.actor_kind}")
            check("actor_token_id is the very credential the caller sent",
                  e.actor_token_id == t_write_id, f"{e.actor_token_id} != {t_write_id}")
            check("responsible_user_id is the PERSON behind it (ADR 0034)",
                  e.responsible_user_id == ALEX_ID, str(e.responsible_user_id))

        r, out = call(http, t_read, sid_w, "atlas_node_write",
                      {"id": "CA-BY-ALEX-AGENT", "summary": "reader on the writer's session"})
        check("identity is per message: the read token on the writer's session is refused",
              err_type(out) == "forbidden", str(out)[:240])

        print("-- D. delete takes the caller's admin scope --")
        r, out = call(http, t_read, sid, "atlas_delete",
                      {"kind": "node", "node_id": "CA-BY-ALEX-AGENT", "expected_edge_count": 0})
        # The node delete reads the raw status (tools_graph._raw), so atlas-api's 403
        # arrives as `http_403` rather than `forbidden`; either is the API refusing.
        check("atlas_delete with read scope: refused by atlas-api (403)",
              err_type(out) in DENIED, str(out)[:240])
        r, out = call(http, t_write, sid_w, "atlas_delete",
                      {"kind": "node", "node_id": "CA-BY-ALEX-AGENT", "expected_edge_count": 0})
        check("atlas_delete with write but not admin: refused by atlas-api (403)",
              err_type(out) in DENIED, str(out)[:240])
        check("...the node is still there", events_for("CA-BY-ALEX-AGENT", "node.delete") == [])
        r, sid_a = session(http, t_admin)
        r, out = call(http, t_admin, sid_a, "atlas_delete",
                      {"kind": "node", "node_id": "CA-BY-ALEX-AGENT", "expected_edge_count": 0})
        check("atlas_delete with an admin-scoped credential: ok", (out or {}).get("ok") is True,
              str(out)[:240])
        evs = events_for("CA-BY-ALEX-AGENT", "node.delete")
        check("the delete is attributed to the admin's credential and its person",
              len(evs) == 1 and evs[0].actor_user_id == OWNER_AGENT_ID
              and evs[0].responsible_user_id == OWNER_ID,
              str([(x.actor_user_id, x.responsible_user_id) for x in evs]))

        print("-- E. a token revoked inside the door's cache is refused by atlas-api --")
        t_rev, t_rev_id = mint(AGENT_ID, ["read", "write"], "ca-revoked-in-cache")
        r, sid_r = session(http, t_rev)
        t_cached = time.monotonic()
        check("initialize with the soon-revoked token: 200 (the door has now cached it)",
              r.status_code == 200 and bool(sid_r), f"{r.status_code} {r.text[:160]}")
        r, out = call(http, t_rev, sid_r, "atlas_node_write",
                      {"id": "CA-REVOKE-TARGET", "type": "project", "name": "Written before revocation"})
        check("before revocation, its write: ok", (out or {}).get("ok") is True, str(out)[:240])
        r, out = call(http, t_rev, sid_r, "atlas_get", {"id": "CA-REVOKE-TARGET", "intents": "none"})
        check("before revocation, its read: ok", (out or {}).get("ok") is True, str(out)[:240])

        revoke(t_rev_id)
        print(f"     revoked {t_rev_id}")
        # Preconditions, so what follows tests atlas-api and not the door: the
        # API now refuses the token, but the door's cache still lets it through.
        check("precondition: the door's cache still holds the revoked token",
              mcp_gate._ok_cache.hit(mcp_gate._digest(t_rev)))
        me = api_me_status(t_rev)
        check("precondition: atlas-api itself now refuses it (GET /api/me: 401)", me == 401, str(me))
        before = event_count()
        print(f"     graph_event rows before the revoked calls: {before}")

        r, out = call(http, t_rev, sid_r, "atlas_get", {"id": "CA-REVOKE-TARGET", "intents": "none"})
        print(f"     read   -> MCP HTTP {r.status_code}, tool result {str(out)[:200]}")
        check("read tool (atlas_get) with the revoked token: refused, `unauthorized` from atlas-api",
              (out or {}).get("ok") is False and err_type(out) == "unauthorized", str(out)[:240])
        r, out = call(http, t_rev, sid_r, "atlas_node_write",
                      {"id": "CA-REVOKED-WRITE", "type": "project", "name": "Must not exist"})
        print(f"     create -> MCP HTTP {r.status_code}, tool result {str(out)[:200]}")
        check("write tool (atlas_node_write, create) with the revoked token: refused, `unauthorized`",
              (out or {}).get("ok") is False and err_type(out) == "unauthorized", str(out)[:240])
        r, out = call(http, t_rev, sid_r, "atlas_node_write",
                      {"id": "CA-REVOKE-TARGET", "summary": "update after revocation"})
        print(f"     update -> MCP HTTP {r.status_code}, tool result {str(out)[:200]}")
        check("write tool (atlas_node_write, update of its own node): refused, `unauthorized`",
              (out or {}).get("ok") is False and err_type(out) == "unauthorized", str(out)[:240])
        after = event_count()
        print(f"     graph_event rows after the revoked calls: {after} (delta {after - before})")
        check("no graph_event row was written by the revoked token", after == before,
              f"{before} -> {after}")
        late = [e for e in events_for("CA-REVOKED-WRITE", "node.create")
                + events_for("CA-REVOKE-TARGET", "node.update") if e.actor_token_id == t_rev_id]
        check("...and none names its credential", late == [], str(len(late)))
        elapsed = time.monotonic() - t_cached
        check(f"all of it inside the door's cache window ({elapsed:.2f} s < {mcp_gate.CACHE_SECONDS:.0f} s)",
              elapsed < mcp_gate.CACHE_SECONDS, f"{elapsed:.2f}")

    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()  # testdb registers its own atexit stop
