"""Prove the work tools read the workitem types from GET /api/vocabulary (TASK-1390).

Same stubbed harness as verify_update_work_acceptance.py: no network, no
database, no `mcp` package. The stub's vocabulary deliberately lists a type
the old hard-coded tuple did not have (`chore`, off the ladder), so a check
that passes can only have read the list from the API. That atlas-api's own
`/api/vocabulary` carries `workitem_types` and `workitem_ladder` is proven by
backend/verify_object_graph.py §7j; real filing through the MCP by
verify_work_priority_labels_api.py.

    python mcp/verify_work_vocabulary.py      # needs only httpx installed
"""

import re
import sys
from pathlib import Path

from verify_atlas_tree import FakeMCP, check, failures  # noqa: E402  (sets the stub env)
from app import tools_work  # noqa: E402
from app.descriptions import DESCRIPTIONS  # noqa: E402
from app.envelope import AtlasError  # noqa: E402

VOCAB = {"workitem_types": ["epic", "story", "task", "chore"],
         "workitem_ladder": ["epic", "story", "task"]}


class StubClient:
    def __init__(self, vocab=VOCAB, fail=0):
        self.vocab, self.fail, self.calls = vocab, fail, []

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        if path == "/api/vocabulary":
            if self.fail:
                self.fail -= 1
                raise AtlasError("unreachable", "GET /api/vocabulary failed: connection refused")
            return self.vocab
        return {"id": "TASK-1", "body": "b", "acceptance": [], "dependencies": [], "bindings": [],
                "result": None,
                "parents": [{"id": "STORY-a", "edge_type": "contains", "type": "story"},
                            {"id": "CHORE-x", "edge_type": "contains", "type": "chore"},
                            {"id": "some-project", "edge_type": "contains", "type": "project"}]}

    def post(self, path, json=None):
        self.calls.append(("POST", path, json))
        return {"id": "TASK-9"}

    def patch(self, path, json=None):
        self.calls.append(("PATCH", path, json))
        return {"ok": True}

    def vocab_reads(self):
        return sum(1 for c in self.calls if c[1] == "/api/vocabulary")

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


def setup(client):
    tools_work.client = client
    tools_work._vocabulary = None
    fake_mcp = FakeMCP()
    tools_work.register(fake_mcp)
    return fake_mcp.tools["atlas_file_work"], fake_mcp.tools["atlas_update_work"]


def main() -> None:
    print("-- A. no hard-coded workitem type tuple remains in mcp/app --")
    app_dir = Path(__file__).resolve().parent / "app"
    tuple_re = re.compile(r"""\(\s*["']epic["']\s*,\s*["']story["']\s*,\s*["']task["']""")
    hits = [p.name for p in sorted(app_dir.glob("*.py")) if tuple_re.search(p.read_text(encoding="utf-8"))]
    check("no ('epic', 'story', 'task') literal in mcp/app", hits == [], str(hits))
    check("tools_work no longer defines WORKITEM_TYPES", not hasattr(tools_work, "WORKITEM_TYPES"))
    stale = [k for k, v in DESCRIPTIONS.items() if re.search(r"epic,? story,? (or )?task|epic/story/task", v)]
    check("no description spells out the type list", stale == [], str(stale))
    desc = DESCRIPTIONS["atlas_file_work"]
    check("atlas_file_work's description says decisions are not filed here and their tool is not built",
          "Decisions are not filed here; their tool is not built yet." in desc, desc)
    check("...and no longer points to a dedicated decision tool that does not exist",
          "dedicated tool" not in desc, desc)

    print("-- B. an unknown type is refused with the list read from the API --")
    stub = StubClient()
    file_work, update_work = setup(stub)
    out = file_work(type="bug", name="x")
    msg = out.get("error", {}).get("message", "")
    check("type='bug' is refused as invalid_type",
          out.get("ok") is False and out["error"]["type"] == "invalid_type", str(out))
    check("...naming the API's list, chore included",
          str(VOCAB["workitem_types"]) in msg and "/api/vocabulary" in msg, msg)
    check("...and nothing was written", stub.writes() == [], str(stub.calls))

    print("-- C. epic/story/task file as before; a type only the API knows passes --")
    out = file_work(type="task", name="t")
    check("a task with no id is POSTed", out.get("ok") is True
          and stub.writes()[-1][:2] == ("POST", "/api/workitem")
          and stub.writes()[-1][2]["type"] == "task", str(stub.writes()))
    out = file_work(type="task", name="t", id="TASK-5")
    check("a task with an id is still id_is_minted", out.get("error", {}).get("type") == "id_is_minted", str(out))
    out = file_work(type="story", name="s")
    check("a story with no id is still id_required", out.get("error", {}).get("type") == "id_required", str(out))
    out = file_work(type="epic", name="e", id="EPIC-e")
    check("an epic with a slug id is POSTed", out.get("ok") is True
          and stub.writes()[-1][2] == {"type": "epic", "name": "e", "body": None, "acceptance": [],
                                       "depends_on": [], "status": "pending", "id": "EPIC-e"},
          str(stub.writes()[-1]))
    out = file_work(type="chore", name="c", id="CHORE-c")
    check("type='chore', listed only by the API, passes the local check",
          out.get("ok") is True and stub.writes()[-1][2]["type"] == "chore", str(out))
    check("the vocabulary was read once for all of the above", stub.vocab_reads() == 1,
          str(stub.vocab_reads()))
    check("...with schemas=none, the small form",
          [c[2] for c in stub.calls if c[1] == "/api/vocabulary"] == [{"schemas": "none"}], str(stub.calls))

    print("-- D. the parent filter keeps ladder types only --")
    out = update_work(id="TASK-1", parent="STORY-b")
    check("the before-image's parent is the story, not the off-ladder chore or the project",
          out.get("ok") is True and out["result"]["before"]["parent"] == ["STORY-a"], str(out)[:300])
    check("still one vocabulary read", stub.vocab_reads() == 1, str(stub.vocab_reads()))

    print("-- E. unreachable vocabulary fails closed, and is not cached --")
    stub = StubClient(fail=1)
    file_work, _ = setup(stub)
    out = file_work(type="task", name="t")
    check("refused as vocabulary_unavailable",
          out.get("ok") is False and out["error"]["type"] == "vocabulary_unavailable", str(out))
    check("...the cause is named", "unreachable" in out.get("error", {}).get("message", ""), str(out))
    check("...and nothing was written", stub.writes() == [], str(stub.calls))
    out = file_work(type="task", name="t")
    check("the next call reads it again and files", out.get("ok") is True and stub.vocab_reads() == 2,
          str(out))
    stub = StubClient(vocab={"node_types": {}})
    file_work, _ = setup(stub)
    out = file_work(type="task", name="t")
    check("a vocabulary without workitem_types is vocabulary_unavailable, nothing written",
          out.get("error", {}).get("type") == "vocabulary_unavailable" and stub.writes() == [], str(out))

    print("-- F. an API without workitem_ladder (before TASK-1389) reads the ladder as the types --")
    stub = StubClient(vocab={"workitem_types": ["epic", "story", "task"]})
    _, update_work = setup(stub)
    out = update_work(id="TASK-1", parent="STORY-b")
    check("the before-image still names the story parent",
          out.get("ok") is True and out["result"]["before"]["parent"] == ["STORY-a"], str(out)[:300])

    print(f"\n{'FAILED:' + str(len(failures)) if failures else 'ALL PASSED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
