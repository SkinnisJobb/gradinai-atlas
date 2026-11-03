"""Project isolation through the MCP, with the install's own ATLAS_RBAC=projects (leg 5, OSS-11).

alice (instance owner) holds a private project. bob has no role on it, so to bob's agent it
does not exist: atlas_get is not_found and search finds nothing, the same answer as for an id
that was never created. After alice binds bob as `viewer`, bob's agent reads it, and a write
is refused with 403 `forbidden` (viewer < editor), never silently applied.
"""
from __future__ import annotations

import json

import pytest

import atlas_smoke as smoke

WORD = "zebrafinchsmoke"   # a search term that appears only in the private project


@pytest.fixture(scope="module")
def private_project(owner, ids):
    smoke.ensure_project(owner, smoke.PRIVATE_PROJECT, f"Private project {WORD}",
                         [(ids[smoke.OWNER], "owner")])
    # alice's own agent sees it (inherits her binding), so the search term is indexed and findable.
    out = smoke.mcp(smoke.OWNER, ("atlas_get", {"id": smoke.PRIVATE_PROJECT}),
                    ("atlas_node_search", {"q": WORD}))
    got, found = out["results"]
    assert got["ok"], f"alice's agent cannot read her own project: {got}"
    assert smoke.PRIVATE_PROJECT in json.dumps(found), f"alice's search misses it: {found}"
    print(f"alice's agent: atlas_get {smoke.PRIVATE_PROJECT} ok; atlas_node_search {WORD!r} finds it")
    return smoke.PRIVATE_PROJECT


def test_member_without_a_role_cannot_see_it(private_project):
    out = smoke.mcp(smoke.MEMBER, ("atlas_get", {"id": private_project}),
                    ("atlas_get", {"id": "smoke-never-created"}),
                    ("atlas_node_search", {"q": WORD}))
    got, never, found = out["results"]
    print(f"bob's agent, no role: atlas_get -> {got.get('error')}; "
          f"a never-created id -> {never.get('error')}; search -> {json.dumps(found)[:200]}")
    assert not got["ok"] and got["error"]["type"] == "not_found"
    assert not never["ok"] and never["error"]["type"] == "not_found"
    # The response echoes the query in `filters`, so look at the hits, not the whole body.
    hits = (found.get("result") or {}).get("items")
    assert found["ok"] and hits == [] and found["result"].get("total") == 0, found


def test_viewer_binding_shows_it_and_refuses_a_write(owner, ids, private_project):
    r = owner.write("PUT", f"/api/access/project/{private_project}/bindings", {"bindings": [
        {"principal_kind": "user", "principal_id": ids[smoke.OWNER], "role": "owner"},
        {"principal_kind": "user", "principal_id": ids[smoke.MEMBER], "role": "viewer"}]})
    assert r.status_code == 200, r.text[:300]
    print(f"alice binds bob as viewer on {private_project}: {r.status_code}")

    out = smoke.mcp(smoke.MEMBER, ("atlas_get", {"id": private_project}),
                    ("atlas_node_search", {"q": WORD}),
                    ("atlas_node_write", {"id": private_project, "summary": "bob was here"}))
    got, found, wrote = out["results"]
    print(f"bob's agent, viewer: atlas_get ok={got['ok']}; search finds it="
          f"{private_project in json.dumps(found)}; write -> {wrote.get('error')}")
    assert got["ok"], got
    assert private_project in json.dumps(found)
    assert not wrote["ok"] and wrote["error"]["type"] == "forbidden", wrote

    # The refused write changed nothing.
    r = owner.get(f"/api/node/{private_project}")
    assert r.status_code == 200 and "bob was here" not in r.text
