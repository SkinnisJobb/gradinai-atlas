"""Prove atlas_save_work's pass-through: one route, the caller's own writes, a pointer to the procedure.

Same stubbed harness as verify_atlas_tree.py: no network, no database, no `mcp`
package. What `/api/events` does with `actor=me` and `since` -- the filter, the
newest-first order, and that a member never sees an event about a node hidden
from them -- is proven against a real Postgres by backend/verify_graph_event.py.

    python mcp/verify_save_work.py      # needs only httpx installed
"""

import sys

from verify_atlas_tree import FakeClient, FakeMCP, check, failures

from app import tools_session  # noqa: E402
from app.envelope import AtlasError  # noqa: E402


class FakeMCPWithPrompts(FakeMCP):
    def __init__(self):
        super().__init__()
        self.prompts = {}

    def prompt(self, name=None, description=None):
        def deco(fn):
            self.prompts[name or fn.__name__] = fn
            return fn
        return deco


def main() -> None:
    fake_mcp, fake_client = FakeMCPWithPrompts(), FakeClient()
    tools_session.client = fake_client
    tools_session.register(fake_mcp)
    save = fake_mcp.tools.get("atlas_save_work")

    print("-- A. registered --")
    check("atlas_save_work is registered", save is not None, sorted(fake_mcp.tools))
    if save is None:
        sys.exit(1)

    print("-- B. one GET: the caller's own writes since the time given, a page at a time --")
    events = {"total": 2, "count": 2, "page": 1, "page_size": 50,
              "items": [{"id": 9, "action": "node.prose", "node_id": "paper-btc"},
                        {"id": 8, "action": "edge.create", "node_id": "example-mcp"}]}
    fake_client.payload = events
    out = save(since="2026-10-01T08:00:00Z")
    path, params = fake_client.calls[-1]
    check("exactly one API call", len(fake_client.calls) == 1, fake_client.calls)
    check("it is GET /api/events", path == "/api/events", path)
    check("filtered to this credential's own writes", params.get("actor") == "me", params)
    check("since is passed through untouched", params.get("since") == "2026-10-01T08:00:00Z", params)
    check("a budget by default: 50 a page", params.get("page_size") == 50, params)
    check("no page unless asked", "page" not in params, params)

    print("-- C. the answer is the API's, plus a pointer --")
    check("ok envelope", out.get("ok") is True, out)
    result = out.get("result", {})
    check("writes are the API's response, unreshaped", result.get("writes") == events, result)
    check("procedure points at the sign-off doc in Atlas",
          result.get("procedure") == {"node_id": "skill-signoff", "path": "docs/nodes/skill-signoff/procedure.md"},
          result.get("procedure"))

    print("-- D. paging and budget are the caller's --")
    save(since="2026-10-01", page=2, page_size=10)
    _, params = fake_client.calls[-1]
    check("page and page_size pass through", params.get("page") == 2 and params.get("page_size") == 10, params)

    print("-- E. an API refusal comes back as the envelope's error, not a crash --")
    fake_client.raises = AtlasError("bad_request", "since: not an ISO-8601 time")
    out = save(since="yesterday")
    check("ok is False", out.get("ok") is False, out)
    check("the API's message is kept", "ISO-8601" in str(out.get("error", {}).get("message")), out)

    print("-- F. the signoff prompt serves the procedure from Atlas, not a copy --")
    fake_client.raises = None
    signoff = fake_mcp.prompts.get("signoff")
    check("the signoff prompt is registered", signoff is not None, sorted(fake_mcp.prompts))
    fake_client.payload = {"path": "docs/nodes/skill-signoff/procedure.md",
                           "content": "# Sign-off procedure\n## 1. Ask first"}
    fake_client.calls.clear()
    text = signoff()
    path, params = fake_client.calls[-1]
    check("one GET of the procedure document", path == "/api/node/skill-signoff/doc"
          and params == {"path": "docs/nodes/skill-signoff/procedure.md"}, fake_client.calls)
    check("the prompt carries the document's text", "## 1. Ask first" in text, text[:200])
    check("and tells the agent to start the sign-off", text.startswith("Run the sign-off"), text[:80])

    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
