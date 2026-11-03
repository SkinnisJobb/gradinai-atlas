"""Prove priority and labels round-trip through the MCP work tools (STORY-oss-14),
end to end: the whole MCP server driven over HTTP, its calls landing in the REAL
atlas-api in process against a disposable Postgres, with the harness
verify_caller_auth_api.py builds (imported, not copied).

  A. atlas_file_work sets priority and labels; atlas_get reads them back.
  B. atlas_update_work replaces them, leaves them when omitted, clears them with null.
  C. atlas_search filters by priority and label.
  D. a bad priority is refused locally, before any call reaches the API.
  F. atlas_update_work moves an item to another parent, leaves it when omitted,
     detaches it with null, and passes the API's refusal of an unknown parent through
     (TASK-1355).
  E. with ATLAS_STRICT_API=1 on the API, the work tools still succeed: everything they
     send is a field the routes read, so strict mode cannot break the MCP.

Needs Docker (for the disposable Postgres) and both requirement files:
    pip install -r backend/requirements.txt -r mcp/requirements.txt
    python mcp/verify_work_priority_labels_api.py
"""

import os
import sys

import httpx
from fastapi.testclient import TestClient

import verify_caller_auth_api as h  # noqa: E402  (starts the disposable Postgres, loads both apps)

check, failures = h.check, h.failures
OWNER_ID, OWNER_AGENT_ID = "u-pl-owner", "u-pl-owner-agent"


def ok(out) -> bool:
    return (out or {}).get("ok") is True


def main() -> None:
    os.environ.pop("ATLAS_STRICT_API", None)
    h.api_app.startup()
    h.mk_user(OWNER_ID, "pl-owner", role="owner")
    h.mk_user(OWNER_AGENT_ID, "bot-pl-owner", is_service=True, person=OWNER_ID)
    tok, _ = h.mint(OWNER_AGENT_ID, ["read", "write"], "pl-write")

    h.mcp_gate.API_TRANSPORT = httpx.ASGITransport(app=h.api_app.app)
    h.mcp_client.client._client._transport = TestClient(h.api_app.app, base_url=h.TEST_ORIGIN)._transport

    with TestClient(h.mcp_main.app, base_url="http://localhost:8092") as http:
        _, sid = h.session(http, tok)
        call = lambda name, args: h.call(http, tok, sid, name, args)[1]  # noqa: E731

        print("-- A. atlas_file_work sets them --")
        out = call("atlas_file_work", {"type": "task", "name": "pl tagged", "priority": "high",
                                       "labels": ["Backend", "auth"]})
        res = (out or {}).get("result") or {}
        wid = res.get("id")
        check("atlas_file_work with priority and labels: ok", ok(out) and bool(wid), str(out)[:240])
        check("...the API stored them (normalised)", res.get("priority") == "high"
              and res.get("labels") == ["backend", "auth"], str(res)[:200])
        check("...and no warnings: every field the tool sends is one the route reads",
              "warnings" not in res, str(res.get("warnings")))
        got = (call("atlas_get", {"id": wid}) or {}).get("result") or {}
        check("atlas_get reads them back", got.get("priority") == "high"
              and got.get("labels") == ["backend", "auth"], str(got)[:200])
        out = call("atlas_file_work", {"type": "task", "name": "pl plain"})
        plain = ((out or {}).get("result") or {})
        check("a file_work call without them is unchanged: priority null, labels []",
              ok(out) and plain.get("priority") is None and plain.get("labels") == [], str(out)[:200])

        print("-- B. atlas_update_work replaces, leaves, clears --")
        out = call("atlas_update_work", {"id": wid, "priority": "critical", "labels": ["infra"]})
        after = ((out or {}).get("result") or {}).get("after") or {}
        check("update replaces both", ok(out) and after.get("priority") == "critical"
              and after.get("labels") == ["infra"], str(out)[:240])
        out = call("atlas_update_work", {"id": wid, "status": "in_progress"})
        after = ((out or {}).get("result") or {}).get("after") or {}
        check("an update that omits them leaves both", after.get("priority") == "critical"
              and after.get("labels") == ["infra"], str(after)[:200])
        out = call("atlas_update_work", {"id": wid, "priority": None, "labels": None})
        after = ((out or {}).get("result") or {}).get("after") or {}
        check("priority=null and labels=null clear them", ok(out) and after.get("priority") is None
              and after.get("labels") == [], str(out)[:240])
        call("atlas_update_work", {"id": wid, "priority": "low", "labels": ["infra", "docs"]})

        print("-- C. atlas_search filters by them --")
        res = (call("atlas_search", {"priority": "low", "page_size": 200}) or {}).get("result") or {}
        got_ids = {i["id"] for i in res.get("items", [])}
        check("atlas_search priority=low finds it", wid in got_ids and plain.get("id") not in got_ids,
              str(sorted(got_ids))[:200])
        res = (call("atlas_search", {"label": "docs", "page_size": 200}) or {}).get("result") or {}
        check("atlas_search label=docs finds it", wid in {i["id"] for i in res.get("items", [])},
              str(res)[:200])

        print("-- D. a bad priority is refused before the API --")
        out = call("atlas_update_work", {"id": wid, "priority": "urgent"})
        check("priority='urgent' -> invalid_priority locally",
              (out or {}).get("ok") is False and h.err_type(out) == "invalid_priority", str(out)[:200])

        print("-- F. atlas_update_work moves a workitem to another parent (TASK-1355) --")
        sa = (call("atlas_file_work", {"type": "story", "id": "STORY-rp-from", "name": "rp from"})
              or {}).get("result") or {}
        sb = (call("atlas_file_work", {"type": "story", "id": "STORY-rp-to", "name": "rp to"})
              or {}).get("result") or {}
        out = call("atlas_file_work", {"type": "task", "name": "rp mover", "parent": "STORY-rp-from"})
        mover = ((out or {}).get("result") or {}).get("id")
        check("two stories and a task under the first", bool(sa.get("id") and sb.get("id") and mover),
              str(out)[:240])
        out = call("atlas_update_work", {"id": mover, "parent": "STORY-rp-to"})
        res = (out or {}).get("result") or {}
        check("parent=STORY-rp-to moves it: the API answers with the new parent",
              ok(out) and (res.get("after") or {}).get("parent") == "STORY-rp-to", str(out)[:240])
        check("...and the before-image names the old parent, to undo from",
              (res.get("before") or {}).get("parent") == ["STORY-rp-from"], str(res.get("before"))[:200])
        got = (call("atlas_get", {"id": mover}) or {}).get("result") or {}
        parents = sorted(p["id"] for p in (got.get("parents") or []) if p.get("edge_type") == "contains")
        check("atlas_get shows one contains parent, the new one", parents == ["STORY-rp-to"], str(parents))
        out = call("atlas_update_work", {"id": mover, "status": "in_progress"})
        check("an update that omits parent leaves it",
              ((out or {}).get("result") or {}).get("after", {}).get("parent") == "STORY-rp-to", str(out)[:240])
        out = call("atlas_update_work", {"id": mover, "parent": "STORY-rp-nowhere"})
        check("an unknown parent is the API's refusal, passed through, and nothing moves",
              (out or {}).get("ok") is False and "unknown_parent" in str(out), str(out)[:240])
        got = (call("atlas_get", {"id": mover}) or {}).get("result") or {}
        check("...the item is still under STORY-rp-to",
              [p["id"] for p in (got.get("parents") or []) if p.get("edge_type") == "contains"] == ["STORY-rp-to"],
              str(got.get("parents"))[:200])
        out = call("atlas_update_work", {"id": "STORY-rp-from", "parent": mover})
        check("a story under a task is the API's parent_order 400, passed through (TASK-1378)",
              (out or {}).get("ok") is False and "parent_order" in str(out), str(out)[:240])
        got = (call("atlas_get", {"id": "STORY-rp-from"}) or {}).get("result") or {}
        check("...and the story did not move",
              [p["id"] for p in (got.get("parents") or []) if p.get("edge_type") == "contains"] == [],
              str(got.get("parents"))[:200])
        out = call("atlas_file_work", {"type": "story", "id": "STORY-rp-bad", "name": "rp bad", "parent": mover})
        check("atlas_file_work: a story filed under a task is the same parent_order 400 (TASK-1378)",
              (out or {}).get("ok") is False and "parent_order" in str(out), str(out)[:240])
        out = call("atlas_update_work", {"id": mover, "parent": None})
        check("parent=null detaches it",
              ok(out) and ((out or {}).get("result") or {}).get("after", {}).get("parent") is None, str(out)[:240])

        print("-- E. ATLAS_STRICT_API=1: the work tools still succeed --")
        os.environ["ATLAS_STRICT_API"] = "1"
        try:
            out = call("atlas_file_work", {"type": "task", "name": "pl strict", "priority": "medium",
                                           "labels": ["x"], "body": "spec", "acceptance": ["a"]})
            sid2 = ((out or {}).get("result") or {}).get("id")
            check("atlas_file_work under strict: ok", ok(out) and bool(sid2), str(out)[:240])
            out = call("atlas_update_work", {"id": sid2, "status": "done", "result": "did it",
                                             "priority": "high", "labels": []})
            check("atlas_update_work under strict: ok", ok(out), str(out)[:240])
            out = call("atlas_node_write", {"id": sid2, "summary": "a summary"})
            check("atlas_node_write (PATCH /api/node) under strict: ok", ok(out), str(out)[:240])
        finally:
            os.environ.pop("ATLAS_STRICT_API", None)

    print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'ALL PASSED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
