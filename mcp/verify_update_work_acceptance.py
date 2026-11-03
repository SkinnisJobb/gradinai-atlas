"""Prove atlas_update_work's two acceptance paths (STORY-atlas-acceptance-human-approved).

Same stubbed harness as verify_atlas_tree.py: no network, no database, no `mcp`
package. What atlas-api does with the requests -- the rail, the attested write
and the proposal routes -- is proven against a real Postgres, in CI, by
backend/verify_acceptance_approval.py and backend/verify_loop_rails.py. This
script proves only what the facade SENDS, and what it refuses locally -- and,
since TASK-1355, that `parent` is passed through for a reparent.

    python mcp/verify_update_work_acceptance.py      # needs only httpx installed
"""

import inspect
import sys

from verify_atlas_tree import FakeMCP, check, failures  # noqa: E402  (sets the stub env)
from app import tools_work  # noqa: E402
from app.descriptions import DESCRIPTIONS  # noqa: E402


class RecordingClient:
    def __init__(self):
        self.calls = []

    def _rec(self, method, path, json=None, params=None):
        if path == "/api/vocabulary":  # TASK-1390: the workitem types, read once per process
            return {"workitem_types": ["epic", "story", "task"],
                    "workitem_ladder": ["epic", "story", "task"]}
        self.calls.append((method, path, json))
        if method == "GET":
            return {"id": "TASK-1", "acceptance": ["old"], "body": "b", "dependencies": [],
                    "bindings": [], "result": None,
                    "parents": [{"id": "STORY-a", "edge_type": "contains", "type": "story"},
                                {"id": "some-project", "edge_type": "contains", "type": "project"}]}
        return {"ok": method, "path": path}

    def get(self, path, params=None):
        return self._rec("GET", path, params=params)

    def post(self, path, json=None):
        return self._rec("POST", path, json=json)

    def patch(self, path, json=None):
        return self._rec("PATCH", path, json=json)


def main() -> None:
    fake_mcp, fake = FakeMCP(), RecordingClient()
    tools_work.client = fake
    tools_work.register(fake_mcp)
    update = fake_mcp.tools["atlas_update_work"]

    print("-- A. the tool exposes both, and no new tool was added --")
    params = inspect.signature(update).parameters
    check("atlas_update_work takes approval, propose_acceptance and proposal_note",
          {"approval", "propose_acceptance", "proposal_note"} <= set(params), sorted(params))
    check("the registered work tools are still exactly two",
          sorted(fake_mcp.tools) == ["atlas_file_work", "atlas_update_work"], sorted(fake_mcp.tools))
    desc = DESCRIPTIONS["atlas_update_work"]
    check("the description says when each applies",
          "propose_acceptance" in desc and "approval" in desc and "attested" in desc, desc)

    print("-- B. an attested write sends approval on the PATCH --")
    fake.calls.clear()
    approval = {"by": "alex", "quote": "yes, that list"}
    out = update(id="TASK-1", acceptance=["a", "b"], set_acceptance=True, approval=approval)
    patches = [c for c in fake.calls if c[0] == "PATCH"]
    check("one PATCH carrying acceptance and approval", out.get("ok") is True and len(patches) == 1
          and patches[0][2] == {"acceptance": ["a", "b"], "approval": approval}, str(fake.calls))
    fake.calls.clear()
    update(id="TASK-1", acceptance=["a"], set_acceptance=True)
    check("without approval no approval key is sent (the API's default rail answers)",
          "approval" not in [c for c in fake.calls if c[0] == "PATCH"][0][2], str(fake.calls))

    print("-- C. a proposal goes to the proposal route and PATCHes nothing --")
    fake.calls.clear()
    out = update(id="TASK-1", acceptance=["p1"], propose_acceptance=True, proposal_note="why")
    check("one POST to /api/workitem/TASK-1/acceptance-proposal with acceptance and note",
          out.get("ok") is True
          and [c for c in fake.calls if c[0] != "GET"] == [
              ("POST", "/api/workitem/TASK-1/acceptance-proposal", {"acceptance": ["p1"], "note": "why"})],
          str(fake.calls))
    check("the result carries the proposal and the before-image",
          "proposal" in out.get("result", {}) and out["result"].get("before", {}).get("acceptance") == ["old"],
          str(out)[:200])
    fake.calls.clear()
    update(id="TASK-1", status="in_progress", acceptance=["p2"], propose_acceptance=True)
    sent = [c for c in fake.calls if c[0] != "GET"]
    check("other fields still PATCH, and the acceptance is only proposed",
          sent[0] == ("PATCH", "/api/workitem/TASK-1", {"status": "in_progress"})
          and sent[1][0] == "POST" and sent[1][2] == {"acceptance": ["p2"]}, str(sent))

    print("-- D. local refusals, before any network call --")
    for label, kwargs, err in (
        ("approval without set_acceptance", {"acceptance": ["x"], "approval": approval},
         "unflagged_wholesale_field"),
        ("approval with no acceptance at all", {"approval": approval}, "approval_without_acceptance"),
        ("propose and set_acceptance together", {"acceptance": ["x"], "set_acceptance": True,
                                                 "propose_acceptance": True}, "conflicting_acceptance_mode"),
        ("propose with approval", {"acceptance": ["x"], "approval": approval, "propose_acceptance": True},
         "conflicting_acceptance_mode"),
        ("propose with no list", {"propose_acceptance": True}, "nothing_to_propose"),
        ("a note without a proposal", {"status": "done", "proposal_note": "n"}, "note_without_proposal"),
    ):
        fake.calls.clear()
        out = update(id="TASK-1", **kwargs)
        check(f"{label}: refused as {err}, nothing sent",
              out.get("ok") is False and out.get("error", {}).get("type") == err and fake.calls == [],
              str(out)[:160])

    print("-- E. parent is passed through to the PATCH (TASK-1355) --")
    check("atlas_update_work takes parent", "parent" in params, sorted(params))
    check("the description names parent", "parent" in desc, desc)
    fake.calls.clear()
    out = update(id="TASK-1", parent="STORY-b")
    sent = [c for c in fake.calls if c[0] != "GET"]
    check("parent=STORY-b is one PATCH carrying only parent",
          out.get("ok") is True and sent == [("PATCH", "/api/workitem/TASK-1", {"parent": "STORY-b"})], str(sent))
    check("...and the before-image names the old work parent only",
          out["result"].get("before", {}).get("parent") == ["STORY-a"], str(out)[:240])
    fake.calls.clear()
    update(id="TASK-1", parent=None)
    check("parent=null is sent as null (detach)",
          [c for c in fake.calls if c[0] == "PATCH"][0][2] == {"parent": None}, str(fake.calls))
    fake.calls.clear()
    update(id="TASK-1", status="done")
    check("an update without parent sends no parent key",
          "parent" not in [c for c in fake.calls if c[0] == "PATCH"][0][2], str(fake.calls))

    print(f"\n{'FAILED:' + str(len(failures)) if failures else 'ALL PASSED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
