"""Prove atlas_setup_guide is a thin pass-through over the setup guide's API.

Same stubbed harness as verify_atlas_tree.py: no network, no database, no `mcp` package. What
the routes answer (the question set, the owner rule, the Proposed ADR, supersede, accept for a
person only) is proven against a real Postgres by backend/verify_setup_guide.py.

    python mcp/verify_setup_guide_tool.py      # needs only httpx installed
"""

import sys

from verify_atlas_tree import FakeMCP, check, failures  # noqa: E402  (sets the stub env and path)
from app import tools_setup  # noqa: E402
from app.envelope import AtlasError  # noqa: E402


class FakeClient:
    def __init__(self):
        self.calls = []
        self.payload = {"next": {"topic": "guardrails", "question": "agent_actions"}}
        self.raises = None

    def _call(self, method, path, params=None, json=None):
        self.calls.append((method, path, dict(params or {}), json))
        if self.raises:
            raise self.raises
        return self.payload

    def get(self, path, params=None):
        return self._call("GET", path, params=params)

    def post(self, path, json=None):
        return self._call("POST", path, json=json)


def main() -> None:
    fake_mcp, fake_client = FakeMCP(), FakeClient()
    tools_setup.client = fake_client
    tools_setup.register(fake_mcp)
    tool = fake_mcp.tools.get("atlas_setup_guide")

    print("-- A. registered --")
    check("atlas_setup_guide is registered", tool is not None, sorted(fake_mcp.tools))
    check("...and nothing else: there is no accept tool (accepting is a person's act)",
          sorted(fake_mcp.tools) == ["atlas_setup_guide"], sorted(fake_mcp.tools))
    if tool is None:
        sys.exit(1)

    print("-- B. no answers: one GET /api/setup --")
    out = tool()
    check("GET /api/setup with no params", fake_client.calls[-1] == ("GET", "/api/setup", {}, None),
          fake_client.calls[-1])
    check("the answer is the API's, unreshaped", out == {"ok": True, "result": fake_client.payload}, out)
    tool(topic="cicd")
    check("topic alone asks for that topic in full",
          fake_client.calls[-1] == ("GET", "/api/setup", {"topic": "cicd"}, None), fake_client.calls[-1])

    print("-- C. answers: one POST /api/setup/answers --")
    answers = {"who_merges": "human_only"}
    tool(topic="guardrails", answers=answers)
    check("POST /api/setup/answers with topic and answers only",
          fake_client.calls[-1] == ("POST", "/api/setup/answers", {},
                                    {"topic": "guardrails", "answers": answers}), fake_client.calls[-1])
    tool(topic="guardrails", answers={}, project="p-x", prefix="PX")
    check("project and prefix pass through, an empty answers object is still a POST",
          fake_client.calls[-1][3] == {"topic": "guardrails", "answers": {}, "project": "p-x",
                                       "prefix": "PX"}, fake_client.calls[-1])

    print("-- D. refusals --")
    n = len(fake_client.calls)
    out = tool(answers=answers)
    check("answers without a topic is refused locally, before any call",
          out.get("ok") is False and out["error"]["type"] == "invalid_request"
          and len(fake_client.calls) == n, out)
    fake_client.raises = AtlasError("forbidden", "The setup guide is limited to owner accounts")
    out = tool()
    check("an API refusal is the envelope's error, message kept",
          out.get("ok") is False and "owner" in str(out.get("error", {}).get("message")), out)

    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
