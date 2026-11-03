"""Prove atlas_node_search's defaults and pass-through (TASK-1156).

Same stubbed harness as verify_atlas_tree.py: no network, no database, no `mcp`
package. What `/api/search` does with the params -- the prose source, the
pointer shape, ranking, and that a member cannot find a private node -- is
proven against a real Postgres, in CI, by backend/verify_backlog_read_api.py §K2.

    python mcp/verify_node_search.py      # needs only httpx installed
"""

import sys

from verify_atlas_tree import FakeClient, FakeMCP, check, failures, tools_read


def main() -> None:
    fake_mcp, fake_client = FakeMCP(), FakeClient()
    tools_read.client = fake_client
    tools_read.register(fake_mcp)
    search = fake_mcp.tools.get("atlas_node_search")

    print("-- A. registered --")
    check("atlas_node_search is registered", search is not None, sorted(fake_mcp.tools))
    if search is None:
        sys.exit(1)

    print("-- B. defaults search the world, a page at a time --")
    fake_client.payload = {"total": 0, "count": 0, "items": []}
    out = search(q="radiator")
    path, params = fake_client.calls[-1]
    check("it reads /api/search", path == "/api/search", path)
    check("defaults: families=object,bridge, page_size=10, no page, mode=hybrid (the agent default)",
          params == {"q": "radiator", "families": "object,bridge", "page_size": 10, "mode": "hybrid"},
          str(params))
    check("the API's answer is passed through untouched", out.get("result") is fake_client.payload)

    print("-- C. every default can be overridden --")
    search(q="x", families="all", page=2, page_size=50)
    _, params = fake_client.calls[-1]
    check("families=all sends no families filter", "families" not in params, str(params))
    check("page and page_size pass through", (params["page"], params["page_size"]) == (2, 50), str(params))
    search(q="x", families="workitem")
    check("families=workitem passes through", fake_client.calls[-1][1]["families"] == "workitem")

    print("-- D. mode defaults to hybrid; mode and expand pass through (ADR 0044 §5) --")
    search(q="radiator")
    _, params = fake_client.calls[-1]
    check("without them: mode=hybrid, no expand", params.get("mode") == "hybrid" and "expand" not in params,
          str(params))
    search(q="radiator", mode="fulltext")
    _, params = fake_client.calls[-1]
    check("mode=fulltext asks for today's full-text search", params.get("mode") == "fulltext", str(params))
    search(q="hur fungerar värmen", mode="hybrid", expand=True)
    _, params = fake_client.calls[-1]
    check("mode=hybrid and expand=true reach the API as mode=hybrid, expand=1",
          (params.get("mode"), params.get("expand")) == ("hybrid", 1), str(params))
    search(q="x", mode="semantic", expand=False)
    _, params = fake_client.calls[-1]
    check("expand=false sends no expand", params.get("mode") == "semantic" and "expand" not in params,
          str(params))

    print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'ALL PASSED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
