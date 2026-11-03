"""Project isolation reaches the MCP plane unchanged (STORY-oss-11, ADR 0041 / 0033).

atlas-mcp holds no auth logic (ADR 0033): it forwards each caller's own token,
and atlas-api decides. So project RBAC needs NO MCP code, and this proves that
rather than assuming it, against the REAL atlas-api in process and a real
Postgres (the `verify_caller_auth_api.py` harness), with
`ATLAS_RBAC=projects` and `ATLAS_DEFAULT_PROJECT_VISIBILITY=private`.

Ann is `editor` of project A; Ben of project B. Each has an agent with its own
read+write token, and every call below goes through the MCP over HTTP.

  A. Ben's agent cannot reach A: `atlas_get` and `atlas_structure` are
     `not_found`, exactly like an id that never existed; `atlas_node_search`
     and `atlas_search` find nothing and count nothing; `atlas_outline` does
     not list it. Ann's agent reaches every one (the control).
  B. Events: while Ben is bound `editor` on A his agent writes there, and
     `atlas_save_work` lists that write. Bound `viewer`, the same write is
     atlas-api's 403, passed through as `forbidden`. Unbound, his own earlier
     write about A is gone from `atlas_save_work` — the events filter is the
     resolver's, on the MCP as on the API.
  C. No RBAC code in the MCP: `mcp/app/` never mentions rbac or a role name.

    pip install -r backend/requirements.txt -r mcp/requirements.txt
    python mcp/verify_isolation_passthrough.py
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
os.environ["ATLAS_RBAC"] = "projects"
os.environ["ATLAS_DEFAULT_PROJECT_VISIBILITY"] = "private"
os.environ["ATLAS_SHARED_PROJECT_ROLE"] = "viewer"
os.environ["ATLAS_BASE_URL"] = TEST_ORIGIN
os.environ.pop("ATLAS_TOKEN", None)
os.environ.pop("ATLAS_MCP_ALLOW_SHARED_TOKEN", None)

import app as api_app  # noqa: E402  (backend/app.py: it owns the name `app`)
import auth  # noqa: E402
import models_auth  # noqa: E402
import rbac  # noqa: E402
import settings  # noqa: E402
from models_auth import AuthUser  # noqa: E402
from models_graph import Edge, Node  # noqa: E402


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
WORD = "obsidianvortex"
ANN, ANN_BOT, BEN, BEN_BOT = "u-mi-ann", "u-mi-ann-bot", "u-mi-ben", "u-mi-ben-bot"


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def mk_user(uid, username, is_service=False, person=None) -> None:
    with api_app.session() as s:
        if s.get(AuthUser, uid) is None:
            s.add(AuthUser(id=uid, subject=username, name=username, role="member",
                           is_service=is_service, must_change_password=False,
                           password_hash=None, person_user_id=person,
                           created_at=datetime.now(timezone.utc)))
            s.commit()


def mint(owner_id: str, scopes: list, label: str) -> str:
    with api_app.session() as s:
        raw, _row = auth.mint_service_token(s, owner=s.get(AuthUser, owner_id),
                                            scopes=scopes, label=label)
        s.commit()
        return raw


def bind(project: str, user_id: str, role: str | None) -> None:
    with api_app.session() as s:
        row = s.get(models_auth.ProjectRoleBinding, (project, "user", user_id))
        if role is None:
            if row is not None:
                s.delete(row)
        elif row is None:
            s.add(models_auth.ProjectRoleBinding(project_id=project, principal_kind="user",
                                                 principal_id=user_id, role=role))
        else:
            row.role = role
        s.commit()


# ---- MCP over HTTP (the verify_caller_auth_api.py shape) ------------------ #
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


def session(http, token):
    r, _ = rpc(http, token, "initialize", {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "verify_isolation_passthrough", "version": "0"}})
    sid = r.headers.get("mcp-session-id")
    if r.status_code == 200:
        rpc(http, token, "notifications/initialized", sid=sid, rid=None)
    return sid


def call(http, token, sid, name, arguments):
    _r, msg = rpc(http, token, "tools/call", {"name": name, "arguments": arguments}, sid=sid, rid=9)
    if msg is None:
        return None
    content = (msg.get("result") or {}).get("content") or [{}]
    try:
        return json.loads(content[0].get("text") or "null")
    except ValueError:
        return {"raw": content}


def err_type(out) -> str:
    return ((out or {}).get("error") or {}).get("type") or ""


def text(out) -> str:
    return json.dumps(out)


def main() -> None:
    api_app.startup()
    check("atlas-api runs with RBAC on in this process", rbac.enabled(), str(rbac.SETTINGS))
    mk_user(ANN, "mi-ann")
    mk_user(ANN_BOT, "bot-mi-ann", is_service=True, person=ANN)
    mk_user(BEN, "mi-ben")
    mk_user(BEN_BOT, "bot-mi-ben", is_service=True, person=BEN)
    with api_app.session() as s:
        for nid, t, name in (("mi-a", "project", f"Project A {WORD}"),
                             ("mi-a-cap", "capability", f"Capability {WORD}"),
                             ("mi-b", "project", "Project B"), ("mi-b-cap", "capability", "Capability B")):
            s.add(Node(id=nid, type=t, name=name, attributes={}, attention_count=0,
                       completion=0.0, origin="atlas"))
        s.flush()
        for a, b in (("mi-a", "mi-a-cap"), ("mi-b", "mi-b-cap")):
            s.add(Edge(src=a, dst=b, edge_type="contains", directed=True, origin="atlas"))
        s.commit()
    bind("mi-a", ANN, "editor")
    bind("mi-b", BEN, "editor")
    t_ann = mint(ANN_BOT, ["read", "write"], "mi-ann")
    t_ben = mint(BEN_BOT, ["read", "write"], "mi-ben")

    mcp_gate.API_TRANSPORT = httpx.ASGITransport(app=api_app.app)
    mcp_client.client._client._transport = TestClient(api_app.app, base_url=TEST_ORIGIN)._transport

    with TestClient(mcp_main.app, base_url="http://localhost:8092") as http:
        sa_, sb_ = session(http, t_ann), session(http, t_ben)
        check("both agents open an MCP session", bool(sa_) and bool(sb_))

        print("-- A. Ben's agent cannot reach project A; Ann's can --")
        never = call(http, t_ben, sb_, "atlas_get", {"id": "mi-never-existed", "intents": "none"})
        for nid in ("mi-a", "mi-a-cap"):
            out = call(http, t_ben, sb_, "atlas_get", {"id": nid, "intents": "none"})
            check(f"atlas_get {nid} as Ben: not_found, like an id that never existed",
                  (out or {}).get("ok") is False and err_type(out) == err_type(never) == "not_found",
                  text(out)[:200])
            out = call(http, t_ann, sa_, "atlas_get", {"id": nid, "intents": "none"})
            check(f"atlas_get {nid} as Ann: ok", (out or {}).get("ok") is True, text(out)[:160])
        out = call(http, t_ben, sb_, "atlas_structure", {"node_id": "mi-a-cap"})
        check("atlas_structure as Ben: not_found", err_type(out) == "not_found", text(out)[:160])
        for tool, args in (("atlas_node_search", {"q": WORD, "families": "all"}),
                           ("atlas_search", {"q": WORD})):
            ob = call(http, t_ben, sb_, tool, args)
            oa = call(http, t_ann, sa_, tool, args)
            rb, ra = (ob or {}).get("result") or {}, (oa or {}).get("result") or {}
            check(f"{tool} as Ben: no hit and total 0",
                  (ob or {}).get("ok") is True and rb.get("total") == 0 and "mi-a" not in text(rb),
                  text(ob)[:200])
            if tool == "atlas_node_search":
                check(f"{tool} as Ann: finds A", (ra.get("total") or 0) > 0, text(oa)[:200])
        ob = call(http, t_ben, sb_, "atlas_outline", {})
        oa = call(http, t_ann, sa_, "atlas_outline", {})
        check("atlas_outline as Ben: lists B, not A",
              (ob or {}).get("ok") is True and "mi-b-cap" in text(ob) and "mi-a" not in text(ob),
              text(ob)[:200])
        check("atlas_outline as Ann: lists A", "mi-a-cap" in text(oa), text(oa)[:200])

        print("-- B. events and writes follow the binding, through the MCP --")
        since = "2000-01-01T00:00:00Z"
        bind("mi-a", BEN, "editor")
        out = call(http, t_ben, sb_, "atlas_node_write", {"id": "mi-a-cap", "summary": f"Ben was here {WORD}"})
        check("bound editor: Ben's agent writes in A", (out or {}).get("ok") is True, text(out)[:200])
        # STORY-oss-08: `save_work` can be switched off, and CI runs this script
        # that way too. Then there is no MCP events path at all, which is
        # asserted instead of skipped.
        save_work = settings.feature_enabled("save_work")
        out = call(http, t_ben, sb_, "atlas_save_work", {"since": since})
        if save_work:
            check("...and atlas_save_work lists that write", "mi-a-cap" in text(out), text(out)[:200])
        else:
            check("save_work disabled: the MCP has no events tool to leak through",
                  "Unknown tool" in text(out), text(out)[:200])
        bind("mi-a", BEN, "viewer")
        out = call(http, t_ben, sb_, "atlas_node_write", {"id": "mi-a-cap", "summary": "viewer write"})
        check("bound viewer: the write is atlas-api's 403, passed through as forbidden",
              err_type(out) == "forbidden", text(out)[:200])
        bind("mi-a", BEN, None)
        out = call(http, t_ben, sb_, "atlas_save_work", {"since": since})
        if save_work:
            check("unbound: his own earlier write about A is gone from atlas_save_work",
                  (out or {}).get("ok") is True and "mi-a-cap" not in text(out), text(out)[:200])
        out = call(http, t_ben, sb_, "atlas_get", {"id": "mi-a-cap", "intents": "none"})
        check("...and A is not_found again", err_type(out) == "not_found", text(out)[:160])

    print("-- C. the MCP holds no RBAC logic (ADR 0033) --")
    hits = []
    for f in sorted((HERE / "app").glob("*.py")):
        src = f.read_text(encoding="utf-8").lower()
        for needle in ("rbac", "maintainer", "project_role"):
            if needle in src:
                hits.append(f"{f.name}:{needle}")
    check("mcp/app/*.py never mentions rbac, maintainer or project_role", not hits, ", ".join(hits))

    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()  # testdb registers its own atexit stop
