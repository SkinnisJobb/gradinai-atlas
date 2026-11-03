"""Prove an agent read THROUGH THE MCP is recorded by atlas-api's read audit (STORY-oss-16).

The whole MCP server (gate, FastMCP, tools, client) is driven over HTTP, and its calls
land in the real `backend/app.py`, in process, against a disposable Postgres from
`backend/testdb.py`, the way verify_caller_auth_api.py does it. The rows checked are
real `audit_read` rows written by the API.

  A. atlas-mcp labels every API request a tool call makes: `X-Atlas-Tool` is the tool
     name the client called, `X-Request-ID` one random id per tool call; a request made
     outside a tool call carries neither.
  B. ATLAS_AUDIT_READS=1: atlas_get, atlas_doc_read and atlas_node_search each leave
     one audit_read row naming the person behind the agent, the agent, the token id,
     the TOOL, the node ids served and the per-call request id; two tool calls get two
     different request ids.
  C. with auditing off, the same tool calls leave no row.
  D. TASK-1382: atlas_tree, atlas_outline, atlas_structure, atlas_path, atlas_search,
     atlas_adr_search, atlas_adr_get and atlas_intents each leave ONE row naming the
     tool and its route; atlas_context leaves ONE row (GET /api/context), not two.

Needs Docker (for the disposable Postgres) and both requirement files:
    pip install -r backend/requirements.txt -r mcp/requirements.txt
    python mcp/verify_audit_reads_api.py

One [PASS]/[FAIL] line per claim, exit 1 if any failed.
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
os.environ["ATLAS_AUDIT_READS"] = "1"   # the subject: the API boots with the read audit on
os.environ.pop("ATLAS_AUDIT_RETENTION_DAYS", None)
os.environ["ATLAS_BASE_URL"] = TEST_ORIGIN
os.environ.pop("ATLAS_TOKEN", None)
os.environ.pop("ATLAS_MCP_ALLOW_SHARED_TOKEN", None)

import httpx  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

import app as api_app  # noqa: E402  (backend/app.py: it owns the name `app`)
import audit_log  # noqa: E402
import auth  # noqa: E402
from models_audit import AuditRead  # noqa: E402
from models_auth import AuthUser  # noqa: E402
from models_adr import AdrRecord  # noqa: E402
from models_graph import Edge, Node, NodeDoc, NodeIntent  # noqa: E402


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

from fastapi.testclient import TestClient  # noqa: E402

failures: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


PERSON, AGENT = "u-ar-person", "u-ar-agent"
NODE, DOC_PATH, WORD = "ar-node", "docs/nodes/ar-node/notes.md", "quokkazanzibar"
PROJ, TASK, ADR = "ar-proj", "TASK-AR-1", "AR001"


def setup():
    with api_app.session() as s:
        s.add(AuthUser(id=PERSON, subject="ar-person", name="Ar Person", role="member",
                       is_service=False, must_change_password=False,
                       created_at=datetime.now(timezone.utc)))
        s.add(AuthUser(id=AGENT, subject="bot-ar-agent", name="Ar Agent", role="member",
                       is_service=True, must_change_password=False, person_user_id=PERSON,
                       created_at=datetime.now(timezone.utc)))
        s.add(Node(id=NODE, type="service", name="Audited node", origin="atlas",
                   body=f"Mentions {WORD}.",
                   attributes={"docs": [{"path": DOC_PATH, "kind": "runbook", "title": "Notes"}]}))
        s.flush()
        s.add(NodeDoc(node_id=NODE, path=DOC_PATH, content="# Notes"))
        # D (TASK-1382): a project around the node, with an intent, an ADR and a task.
        s.add_all([Node(id=PROJ, type="project", name="Audited project", origin="atlas"),
                   Node(id=TASK, type="task", name="Audited task", origin="atlas",
                        attributes={"status": "todo"})])
        s.flush()
        s.add_all([
            Edge(src=PROJ, dst=NODE, edge_type="contains", directed=True, origin="atlas"),
            Edge(src=TASK, dst=NODE, edge_type="works_on", directed=True, origin="atlas"),
            NodeIntent(node_id=PROJ, slug="ar-rule", kind="constraint", title="Ar rule",
                       statement="Ar rule, in full.", position=0),
            AdrRecord(adr_id=ADR, project_node_id=PROJ, prefix="AR", seq=1, slug="s",
                      title="Ar decision", status="accepted", content="Decided.", source="atlas"),
        ])
        s.commit()
        raw, row = auth.mint_service_token(s, owner=s.get(AuthUser, AGENT), scopes=["read"],
                                           label="ar-read")
        s.commit()
        return raw, row.id


def reads():
    with api_app.session() as s:
        rows = list(s.scalars(select(AuditRead).order_by(AuditRead.id)).all())
        for r in rows:
            s.expunge(r)
        return rows


def clear():
    with api_app.session() as s:
        s.execute(delete(AuditRead))
        s.commit()


def rpc(http, token, method, params=None, sid=None, rid=1):
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "Authorization": "Bearer " + token}
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


def open_session(http, token):
    r, _ = rpc(http, token, "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "verify_audit_reads_api", "version": "0"}})
    sid = r.headers.get("mcp-session-id")
    rpc(http, token, "notifications/initialized", sid=sid, rid=None)
    return sid


def call(http, token, sid, name, arguments):
    _, msg = rpc(http, token, "tools/call", {"name": name, "arguments": arguments}, sid=sid, rid=7)
    content = ((msg or {}).get("result") or {}).get("content") or [{}]
    try:
        return json.loads(content[0].get("text") or "null")
    except ValueError:
        return {"raw": content}


def section_a(token):
    print("-- A. atlas-mcp labels the API requests of a tool call --")
    seen = []

    def fake(request):
        seen.append(dict(request.headers))
        return httpx.Response(200, json={"id": "x"})

    c = mcp_client.AtlasClient()
    c._client._transport = httpx.MockTransport(fake)
    with mcp_client.as_caller(token):
        c.get("/api/node/x")
        with mcp_client.tool_call("atlas_get") as rid:
            c.get("/api/node/x")
            c.get("/api/node/y")
    check("outside a tool call: no X-Atlas-Tool, no X-Request-ID",
          "x-atlas-tool" not in seen[0] and "x-request-id" not in seen[0])
    check("inside: X-Atlas-Tool is the tool name", seen[1].get("x-atlas-tool") == "atlas_get")
    check("inside: X-Request-ID is the call's id, shared by every request of the call",
          seen[1].get("x-request-id") == rid == seen[2].get("x-request-id") and len(rid) == 32)
    check("the label never carries the credential",
          token not in (seen[1].get("x-atlas-tool", "") + seen[1].get("x-request-id", "")))


def main():
    api_app.startup()
    token, token_id = setup()
    section_a(token)
    check("the API booted with the read audit on (ATLAS_AUDIT_READS=1)", audit_log.READS_ENABLED)

    mcp_gate.API_TRANSPORT = httpx.ASGITransport(app=api_app.app)
    mcp_client.client._client._transport = TestClient(api_app.app, base_url=TEST_ORIGIN)._transport

    with TestClient(mcp_main.app, base_url="http://localhost:8092") as http:
        sid = open_session(http, token)
        clear()
        print("-- B. ATLAS_AUDIT_READS=1: each read tool leaves one attributed row --")
        outs = [call(http, token, sid, "atlas_get", {"id": NODE}),
                call(http, token, sid, "atlas_doc_read", {"node_id": NODE, "path": DOC_PATH}),
                call(http, token, sid, "atlas_node_search", {"q": WORD})]
        check("the three tool calls succeed", all((o or {}).get("ok") for o in outs), str(outs)[:300])
        rows = reads()
        check("three audit_read rows", len(rows) == 3, str(len(rows)))
        tools = {r.tool: r for r in rows}
        check("the tools recorded are the ones called",
              set(tools) == {"atlas_get", "atlas_doc_read", "atlas_node_search"}, str(sorted(tools)))
        for r in rows:
            check(f"{r.tool}: user is the person, actor the agent, token the credential",
                  r.user_id == PERSON and r.actor_user_id == AGENT and r.token_id == token_id,
                  f"{r.user_id} {r.actor_user_id} {r.token_id}")
            check(f"{r.tool}: node ids served", r.node_ids == [NODE], str(r.node_ids))
            check(f"{r.tool}: a 32-hex request id", bool(r.request_id) and len(r.request_id) == 32)
        check("each tool call has its own request id", len({r.request_id for r in rows}) == 3)
        routes = {r.tool: r.route for r in rows}
        check("and the route each tool used", routes == {
            "atlas_get": "GET /api/node/{node_id}",
            "atlas_doc_read": "GET /api/node/{node_id}/doc",
            "atlas_node_search": "GET /api/search"}, str(routes))

        print("-- C. auditing off: the same calls leave nothing --")
        clear()
        audit_log.READS_ENABLED = False
        try:
            call(http, token, sid, "atlas_get", {"id": NODE})
            call(http, token, sid, "atlas_node_search", {"q": WORD})
        finally:
            audit_log.READS_ENABLED = True
        check("no audit_read row", reads() == [], str(len(reads())))

        print("-- D. TASK-1382: the other read tools, one row each --")
        calls = (
            ("atlas_tree", {"focus": PROJ}, "GET /api/graph"),
            ("atlas_outline", {"root": PROJ, "families": "all"}, "GET /api/graph/outline"),
            ("atlas_structure", {"node_id": NODE}, "GET /api/node/{node_id}/structure"),
            ("atlas_path", {"from_id": NODE, "to_id": PROJ}, "GET /api/graph/path"),
            ("atlas_search", {"binding": NODE}, "GET /api/workitems"),
            ("atlas_adr_search", {"project": PROJ}, "GET /api/adrs"),
            ("atlas_adr_get", {"id": ADR}, "GET /api/adrs/{ref}"),
            ("atlas_intents", {"node_id": PROJ}, "GET /api/intents"),
            ("atlas_context", {"about": WORD, "families": "all", "mode": "fulltext"},
             "GET /api/context"),
        )
        for tool, args, route in calls:
            clear()
            out = call(http, token, sid, tool, args)
            rows = reads()
            check(f"{tool}: ok, and ONE audit_read row for route {route}",
                  (out or {}).get("ok") is True and [(r.tool, r.route) for r in rows] == [(tool, route)],
                  f"{str(out)[:200]} {[(r.tool, r.route) for r in rows]}")
            if len(rows) == 1:
                r = rows[0]
                check(f"{tool}: person, agent, token, a request id and served node ids",
                      r.user_id == PERSON and r.actor_user_id == AGENT and r.token_id == token_id
                      and bool(r.request_id) and len(r.request_id) == 32 and len(r.node_ids) > 0,
                      f"{r.user_id} {r.actor_user_id} {r.request_id} {r.node_ids}")

    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()  # testdb registers its own atexit stop
