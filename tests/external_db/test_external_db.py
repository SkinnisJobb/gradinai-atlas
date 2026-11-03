"""The OSS install on an external PostgreSQL (install smoke, job `external-db`; TASK-1471).

Run by the workflow after the stack is up with deploy/oss/external-db.yml, against a PostgreSQL
16 that is not part of the install (tests/external_db/docker-compose.extdb.yml), reached over
TLS with sslmode=verify-full. One write through the API and one through MCP, each read back
through the API. The text written is kept in SMOKE_DIR/external-db.mark, so the workflow can
then look for it IN the external server with psql: that is what proves the data went there and
not to some other database. See .github/workflows/install-smoke.yml.
"""
from __future__ import annotations

import secrets

import atlas_smoke as smoke

PROJECT = "external-db-smoke"
MARK = "external-db.mark"


def test_write_then_read_through_the_api(owner, ids):
    # A write by alice's browser session (POST /api/node, the role bindings), read back.
    smoke.ensure_project(owner, PROJECT, "External database smoke project",
                         [(ids[smoke.OWNER], "owner"), (ids[smoke.MEMBER], "editor")])
    r = owner.get(f"/api/node/{PROJECT}")
    assert r.status_code == 200, f"GET /api/node/{PROJECT} after creating it: {r.status_code}"
    assert r.json().get("name") == "External database smoke project", r.json()

    # A write by alice's agent through MCP (atlas-mcp -> atlas-api -> the external database),
    # read back through the API by alice.
    mark = f"written on the external database, nonce {secrets.token_hex(4)}"
    res = smoke.mcp(smoke.OWNER, ("atlas_node_write", {"id": PROJECT, "summary": mark}))["results"][0]
    assert res["ok"], f"alice's agent's write: {res}"
    r = owner.get(f"/api/node/{PROJECT}")
    assert r.status_code == 200 and r.json().get("summary") == mark, "the MCP write did not land"
    (smoke.DIR / MARK).write_text(mark, encoding="utf-8")
    print(f"{PROJECT}: created, bound, written by alice's agent and read back through the API")
