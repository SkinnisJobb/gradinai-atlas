"""Prove atlas_structure's pass-through: one route, the caller's id, the cap only when asked.

Same stubbed harness as verify_atlas_tree.py: no network, no database, no `mcp`
package. What `/api/node/{id}/structure` returns -- the fields, the visibility
filter on every linked id, the cap -- is proven against a real Postgres by
backend/verify_node_structure.py.

    python mcp/verify_structure_tool.py      # needs only httpx installed
"""

import sys

from verify_atlas_tree import FakeClient, FakeMCP, check, failures, tools_read
from app.envelope import AtlasError  # noqa: E402


def main() -> None:
    fake_mcp, fake_client = FakeMCP(), FakeClient()
    tools_read.client = fake_client
    tools_read.register(fake_mcp)
    tool = fake_mcp.tools.get("atlas_structure")

    print("-- A. registered --")
    check("atlas_structure is registered", tool is not None, sorted(fake_mcp.tools))
    if tool is None:
        sys.exit(1)

    print("-- B. one GET of the structure route, nothing reshaped --")
    payload = {"node": {"id": "TASK-1", "type": "task", "name": "n", "status": "pending"},
               "ancestors": [], "children": [], "children_total": 0, "children_truncated": False,
               "dependencies": [], "dependants": [], "bindings": []}
    fake_client.payload = payload
    out = tool(node_id="TASK-1")
    path, params = fake_client.calls[-1]
    check("exactly one call", len(fake_client.calls) == 1, fake_client.calls)
    check("GET /api/node/{id}/structure", path == "/api/node/TASK-1/structure", path)
    check("no cap sent unless asked: the API's default applies", params == {}, params)
    check("the answer is the API's, unreshaped", out == {"ok": True, "result": payload}, out)

    print("-- C. the cap passes through --")
    tool(node_id="STORY-x", children_limit=5)
    path, params = fake_client.calls[-1]
    check("children_limit passes through", params == {"children_limit": 5}, params)

    print("-- D. a 404 is the envelope's error, not a crash --")
    fake_client.raises = AtlasError("not_found", "unknown node 'nope'")
    out = tool(node_id="nope")
    check("ok is False", out.get("ok") is False, out)
    check("the API's message is kept", "unknown node" in str(out.get("error", {}).get("message")), out)

    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
