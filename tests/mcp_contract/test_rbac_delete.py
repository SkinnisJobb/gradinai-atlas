"""The RBAC delete leg (install smoke, part of leg 5; ADR 0041 §2-§4).

A local group `smoke-maintainers` (bob) is bound `maintainer` on project A and `editor` on
project B. bob's agent, with an admin-scoped token (an agent's role is capped at maintainer by
`admin`, and is the minimum of that and bob's role), deletes a node in A and is refused 403
deleting one in B. The role comes ONLY through the group: bob holds no direct binding on
either project. Driven over the REST API through the ingress with the agent's Bearer token,
because the install leaves atlas_delete off (ATLAS_MCP_ENABLE_DELETE unset in .env.example).
"""
from __future__ import annotations

import httpx

import atlas_smoke as smoke

A, B = "smoke-del-a", "smoke-del-b"


def _child(owner: smoke.Session, project: str) -> str:
    """A task bound to (working on) `project`: it belongs to that project. (A bare node would
    be a new project root of its own, owned by its creator alone (ADR 0051), and so in neither
    A nor B.)"""
    r = owner.write("POST", "/api/workitem", {"type": "task", "name": "RBAC delete target in " + project,
                                              "binding": project, "status": "pending"})
    assert r.status_code == 201, r.text[:300]
    return r.json()["id"]


def test_group_maintainer_deletes_in_a_and_is_refused_in_b(owner, ids):
    r = owner.write("POST", "/api/access/groups", {"name": "smoke-maintainers"})
    assert r.status_code in (201, 409), r.text[:200]
    groups = owner.get("/api/access/groups").json()
    groups = groups.get("groups", groups) if isinstance(groups, dict) else groups
    gid = next(g["id"] for g in groups if g["name"] == "smoke-maintainers")
    r = owner.write("PUT", f"/api/access/groups/{gid}/members", {"members": [ids[smoke.MEMBER]]})
    assert r.status_code == 200, r.text[:200]

    for pid, group_role in ((A, "maintainer"), (B, "editor")):
        smoke.ensure_project(owner, pid, "RBAC delete " + pid, [(ids[smoke.OWNER], "owner")])
        r = owner.write("PUT", f"/api/access/project/{pid}/bindings", {"bindings": [
            {"principal_kind": "user", "principal_id": ids[smoke.OWNER], "role": "owner"},
            {"principal_kind": "group", "principal_id": gid, "role": group_role}]})
        assert r.status_code == 200, r.text[:300]
    leaf_a, leaf_b = _child(owner, A), _child(owner, B)
    print(f"group smoke-maintainers (bob): maintainer on {A}, editor on {B}; leaves {leaf_a}, {leaf_b}")

    agent = httpx.Client(base_url=smoke.URL, verify=smoke.ssl_ctx(), timeout=30,
                         headers={"Authorization": "Bearer " + smoke.secret(f"{smoke.MEMBER}.admin.token")})
    r_b = agent.delete(f"/api/node/{leaf_b}", params={"cascade": 1})
    print(f"bob's admin-scoped agent DELETE {leaf_b} (group editor) -> {r_b.status_code} {r_b.text[:160]}")
    assert r_b.status_code == 403 and r_b.json().get("needed_role") == "maintainer"
    r_a = agent.delete(f"/api/node/{leaf_a}", params={"cascade": 1})
    print(f"bob's admin-scoped agent DELETE {leaf_a} (group maintainer) -> {r_a.status_code}")
    assert r_a.status_code == 200, r_a.text[:300]
    assert owner.get(f"/api/node/{leaf_a}").status_code == 404
    assert owner.get(f"/api/node/{leaf_b}").status_code == 200
