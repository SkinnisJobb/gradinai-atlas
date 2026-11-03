"""Prove atlas_context's pass-through (ADR 0044 §0, STORY-atlas-context-core).

Same stubbed harness as verify_node_search.py: no network, no database, no `mcp` package. What
`/api/context` does with the parameters (the parts, the budgets, deletion-equivalence) is proven
against a real Postgres by backend/verify_context.py.

    python mcp/verify_context_tool.py      # needs only httpx installed
"""

import sys

from verify_atlas_tree import FakeClient, FakeMCP, check, failures, tools_read


def main() -> None:
    fake_mcp, fake_client = FakeMCP(), FakeClient()
    tools_read.client = fake_client
    tools_read.register(fake_mcp)
    ctx = fake_mcp.tools.get("atlas_context")

    print("-- A. registered --")
    check("atlas_context is registered", ctx is not None, sorted(fake_mcp.tools))
    if ctx is None:
        sys.exit(1)

    print("-- B. only what was given reaches the API --")
    fake_client.payload = {"seeds": {"items": [], "truncated": False}}
    out = ctx(about="hur fungerar värmen")
    path, params = fake_client.calls[-1]
    check("it reads /api/context", path == "/api/context", path)
    check("with no options it sends only `about`: the API owns the defaults",
          params == {"about": "hur fungerar värmen"}, str(params))
    check("the API's answer is passed through untouched", out.get("result") is fake_client.payload)

    print("-- C. every option passes through --")
    ctx(about="x", mode="fulltext", families="all", seeds=3, neighbours=0, impact_depth=1, impact=5,
        intents=2, intents_full=True, decisions=1, docs=4)
    _, params = fake_client.calls[-1]
    check("all eleven reach the API; neighbours=0 is sent, not dropped; intents_full is 1",
          params == {"about": "x", "mode": "fulltext", "families": "all", "seeds": 3, "neighbours": 0,
                     "impact_depth": 1, "impact": 5, "intents": 2, "intents_full": 1, "decisions": 1,
                     "docs": 4}, str(params))
    ctx(about="x", intents_full=False)
    check("intents_full=false is sent as 0", fake_client.calls[-1][1].get("intents_full") == 0,
          str(fake_client.calls[-1][1]))

    print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'ALL PASSED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
