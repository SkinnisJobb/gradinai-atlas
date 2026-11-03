"""Prove what atlas_comments and atlas_comment_write SEND, and what they refuse locally.

Same stubbed harness as verify_atlas_tree.py: no network, no database, no `mcp` package.
What atlas-api does with the requests (who may write, edit and see) is proven against a real
Postgres, in CI, by backend/verify_workitem_comments.py.

    python mcp/verify_comment_tools.py      # needs only httpx installed
"""

import sys

from verify_atlas_tree import FakeMCP, check, failures  # noqa: E402  (sets the stub env)
from app import tools_comments  # noqa: E402
from app.descriptions import DESCRIPTIONS  # noqa: E402


class RecordingClient:
    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("GET", path, None))
        return {"comments": [], "count": 0}

    def post(self, path, json=None):
        self.calls.append(("POST", path, json))
        return {"id": 1}

    def patch(self, path, json=None):
        self.calls.append(("PATCH", path, json))
        return {"id": 1}


def main() -> None:
    fake_mcp, fake = FakeMCP(), RecordingClient()
    tools_comments.client = fake
    tools_comments.register(fake_mcp)
    read, write = fake_mcp.tools["atlas_comments"], fake_mcp.tools["atlas_comment_write"]

    print("-- A. the two tools, each described --")
    check("exactly atlas_comments and atlas_comment_write are registered",
          sorted(fake_mcp.tools) == ["atlas_comment_write", "atlas_comments"], sorted(fake_mcp.tools))
    check("both have a description", {"atlas_comments", "atlas_comment_write"} <= set(DESCRIPTIONS))

    print("-- B. what is sent --")
    out = read(id="TASK-1")
    check("reading is one GET of the item's comments",
          out.get("ok") is True and fake.calls == [("GET", "/api/workitem/TASK-1/comments", None)], str(fake.calls))
    fake.calls.clear()
    out = write(id="TASK-1", body="Looks done to me.")
    check("adding is one POST carrying only the body",
          out.get("ok") is True
          and fake.calls == [("POST", "/api/workitem/TASK-1/comments", {"body": "Looks done to me."})], str(fake.calls))
    fake.calls.clear()
    out = write(comment_id=7, body="Edited.")
    check("editing is one PATCH of that comment",
          out.get("ok") is True and fake.calls == [("PATCH", "/api/comment/7", {"body": "Edited."})], str(fake.calls))

    print("-- C. local refusals, before any network call --")
    for label, kwargs in (("an empty body", {"id": "TASK-1", "body": "  "}),
                          ("neither id nor comment_id", {"body": "x"}),
                          ("both id and comment_id", {"id": "TASK-1", "comment_id": 7, "body": "x"})):
        fake.calls.clear()
        out = write(**kwargs)
        check(f"{label}: refused as invalid_request, nothing sent",
              out.get("ok") is False and out.get("error", {}).get("type") == "invalid_request"
              and fake.calls == [], str(out)[:160])

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
