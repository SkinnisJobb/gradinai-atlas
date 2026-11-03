"""Prove a project key round-trips through `atlas_node_write(key=...)` (STORY-oss-12),
end to end: the whole MCP server driven over HTTP, its calls landing in the REAL
atlas-api in process against a disposable Postgres, with the harness
verify_caller_auth_api.py builds (imported, not copied).

  A. atlas_node_write creates a project WITH a key (POST, then PATCH for the key),
     and atlas_get reads it back.
  B. atlas_file_work under that project is minted KEY-1, KEY-2; outside it, TASK-.
  C. omitting `key` leaves it; once an id is minted, changing it is refused by the API.
  D. a token without the `admin` scope cannot set a key (RBAC off: owner or admin scope).

Needs Docker (for the disposable Postgres) and both requirement files:
    pip install -r backend/requirements.txt -r mcp/requirements.txt
    python mcp/verify_project_key_api.py
"""

import sys

import httpx
from fastapi.testclient import TestClient

import verify_caller_auth_api as h  # noqa: E402  (starts the disposable Postgres, loads both apps)

check, failures = h.check, h.failures
OWNER_ID, OWNER_AGENT_ID = "u-pk-owner", "u-pk-owner-agent"


def ok(out) -> bool:
    return (out or {}).get("ok") is True


def res(out) -> dict:
    return (out or {}).get("result") or {}


def main() -> None:
    h.api_app.startup()
    h.mk_user(OWNER_ID, "pk-owner", role="owner")
    h.mk_user(OWNER_AGENT_ID, "bot-pk-owner", is_service=True, person=OWNER_ID)
    tok_admin, _ = h.mint(OWNER_AGENT_ID, ["read", "write", "admin"], "pk-admin")
    tok_write, _ = h.mint(OWNER_AGENT_ID, ["read", "write"], "pk-write")

    h.mcp_gate.API_TRANSPORT = httpx.ASGITransport(app=h.api_app.app)
    h.mcp_client.client._client._transport = TestClient(h.api_app.app, base_url=h.TEST_ORIGIN)._transport

    with TestClient(h.mcp_main.app, base_url="http://localhost:8092") as http:
        _, sid = h.session(http, tok_admin)
        call = lambda name, args: h.call(http, tok_admin, sid, name, args)[1]  # noqa: E731

        print("-- A. create a project with a key --")
        out = call("atlas_node_write", {"id": "pk-mcp-proj", "type": "project",
                                        "name": "PK MCP project", "key": "PKM"})
        check("atlas_node_write(type=project, key=PKM): ok, created",
              ok(out) and res(out).get("_mcp_action") == "created" and res(out).get("key") == "PKM",
              str(out)[:240])
        check("atlas_get reads the key back",
              res(call("atlas_get", {"id": "pk-mcp-proj"})).get("key") == "PKM")
        out = call("atlas_node_write", {"id": "pk-mcp-other", "type": "project", "name": "PK other"})
        check("a project created without key= has none", ok(out) and "key" not in res(out), str(out)[:200])

        print("-- B. tasks under it are KEY-n --")
        ids = [res(call("atlas_file_work", {"type": "task", "name": f"pk {i}",
                                            "binding": "pk-mcp-proj"})).get("id") for i in range(2)]
        check("two tasks bound to the keyed project: PKM-1, PKM-2", ids == ["PKM-1", "PKM-2"], str(ids))
        other = res(call("atlas_file_work", {"type": "task", "name": "pk other",
                                             "binding": "pk-mcp-other"})).get("id") or ""
        check("a task in the unkeyed project: TASK-", other.startswith("TASK-"), other)

        print("-- C. omitted leaves it; a used key is fixed --")
        out = call("atlas_node_write", {"id": "pk-mcp-proj", "summary": "has a key"})
        check("a patch without key= leaves it", ok(out) and res(out).get("key") == "PKM", str(out)[:200])
        out = call("atlas_node_write", {"id": "pk-mcp-proj", "key": "PKN"})
        check("changing a key that has minted ids is refused", not ok(out)
              and "key_immutable" in str(out), str(out)[:240])

        print("-- D. without the admin scope --")
        _, sid_w = h.session(http, tok_write)
        out = h.call(http, tok_write, sid_w, "atlas_node_write", {"id": "pk-mcp-other", "key": "PKO"})[1]
        check("a write-only token cannot set a key", not ok(out), str(out)[:240])

    print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'ALL PASSED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
