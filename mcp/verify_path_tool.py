"""Prove atlas_path's pass-through: one route, both ids, the knobs only when asked.

Same stubbed harness as verify_atlas_tree.py: no network, no database, no `mcp`
package. What `/api/graph/path` returns -- the search, the caps, and the walk over
the caller's visible graph only -- is proven against a real Postgres by
backend/verify_graph_path.py.

    python mcp/verify_path_tool.py      # needs only httpx installed
"""

import json
import sys

from verify_atlas_tree import FakeClient, FakeMCP, check, failures, tools_read
from app.envelope import AtlasError  # noqa: E402


def main() -> None:
    fake_mcp, fake_client = FakeMCP(), FakeClient()
    tools_read.client = fake_client
    tools_read.register(fake_mcp)
    tool = fake_mcp.tools.get("atlas_path")

    print("-- A. registered --")
    check("atlas_path is registered", tool is not None, sorted(fake_mcp.tools))
    if tool is None:
        sys.exit(1)

    print("-- B. one GET of the path route, nothing reshaped --")
    payload = {"from": "atlas-backend", "to": "atlas-mcp", "edge_types": None, "hops_max": 8,
               "paths": [{"length": 1, "hops": [{"from": "atlas-backend", "to": "atlas-mcp",
                                                  "edge_type": "uses", "direction": "in",
                                                  "edge": 42}]}],
               "nodes": {"atlas-backend": {"type": "service", "name": "API"},
                         "atlas-mcp": {"type": "service", "name": "MCP"}}}
    fake_client.payload = payload
    out = tool(from_id="atlas-backend", to_id="atlas-mcp")
    path, params = fake_client.calls[-1]
    check("exactly one call", len(fake_client.calls) == 1, fake_client.calls)
    check("GET /api/graph/path", path == "/api/graph/path", path)
    check("both ids are sent under the API's names, and nothing else unless asked",
          params == {"from": "atlas-backend", "to": "atlas-mcp"}, params)
    check("the answer is the API's, unreshaped", out == {"ok": True, "result": payload}, out)
    print(f"   a one-hop answer through the envelope: {len(json.dumps(out))} B")

    print("-- C. the knobs pass through --")
    tool(from_id="a", to_id="b", max=5, edges="contains,uses", hops=4)
    path, params = fake_client.calls[-1]
    check("max, edges and hops pass through",
          params == {"from": "a", "to": "b", "max": 5, "edges": "contains,uses", "hops": 4}, params)

    print("-- D. a 404 is the envelope's error, not a crash --")
    fake_client.raises = AtlasError("not_found", "unknown node 'nope'")
    out = tool(from_id="nope", to_id="b")
    check("ok is False", out.get("ok") is False, out)
    check("the API's message is kept", "unknown node" in str(out.get("error", {}).get("message")), out)

    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
