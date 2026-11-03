"""Upgrade from the previous release (install smoke, job `upgrade`; OSS-26 C, TASK-1416).

Two halves, run by the workflow at two different times against the SAME database:

  test_seed_on_the_previous_release      against the previous release, run from its published
                                         images: a project with both people bound, and a write
                                         by alice's agent through MCP. The text written is kept
                                         in SMOKE_DIR/upgrade.mark.
  test_data_survived_the_upgrade         after the upgrade to this checkout: the project and the
                                         write are there, the passwords and the agent token made
                                         under the previous release still work, and the write is
                                         still attributed to alice's agent.

Between them the workflow takes a backup, switches to this checkout, runs the schema step and
compares every table's row count. See .github/workflows/install-smoke.yml.
"""
from __future__ import annotations

import json
import secrets

import pytest

import atlas_smoke as smoke

PROJECT = "upgrade-smoke"
MARK = "upgrade.mark"


def _mark_file():
    return smoke.DIR / MARK


def test_seed_on_the_previous_release(owner, ids):
    # prove_creation=False: this half runs against the PREVIOUS release, which may predate
    # ADR 0051 (the creator owns a new project root); leg 4 proves the creation rules.
    smoke.ensure_project(owner, PROJECT, "Upgrade smoke project",
                         [(ids[smoke.OWNER], "owner"), (ids[smoke.MEMBER], "editor")],
                         prove_creation=False)
    mark = f"written by alice's agent before the upgrade, nonce {secrets.token_hex(4)}"
    res = smoke.mcp(smoke.OWNER, ("atlas_node_write", {"id": PROJECT, "summary": mark}))["results"][0]
    assert res["ok"], f"alice's agent's write on the previous release: {res}"
    r = owner.get(f"/api/node/{PROJECT}")
    assert r.status_code == 200 and r.json().get("summary") == mark, "the write did not land"
    _mark_file().write_text(mark, encoding="utf-8")
    print(f"seeded {PROJECT} (alice owner, bob editor) and a write by alice's agent")


def test_data_survived_the_upgrade(owner, ids):
    if not _mark_file().exists():
        pytest.fail("no upgrade.mark: the seed half did not run on the previous release")
    mark = _mark_file().read_text(encoding="utf-8")

    # The password set under the previous release signs alice in (the `owner` fixture) and bob.
    r = owner.get(f"/api/node/{PROJECT}")
    assert r.status_code == 200, f"GET /api/node/{PROJECT} as alice after the upgrade: {r.status_code}"
    assert r.json().get("summary") == mark, "the write made before the upgrade is not there"
    bob = smoke.signed_in(smoke.MEMBER)
    r = bob.get(f"/api/node/{PROJECT}")
    assert r.status_code == 200, f"bob's editor binding did not survive: {r.status_code}"

    # The write is still attributed: alice's agent, accountable alice.
    r = owner.get("/api/events", entity_id=PROJECT, action="node.update", page_size=20)
    assert r.status_code == 200, r.text[:300]
    ev = next((e for e in r.json()["items"] if (e.get("actor") or {}).get("subject") ==f"{smoke.OWNER}-agent"), None)
    assert ev is not None, f"no node.update by {smoke.OWNER}-agent after the upgrade: {r.json()['items']}"
    assert ev["responsible_user_id"] == ids[smoke.OWNER], ev

    # The agent token minted under the previous release still works through MCP.
    res = smoke.mcp(smoke.OWNER, ("atlas_get", {"id": PROJECT}))["results"][0]
    assert res["ok"], f"alice's agent token from the previous release, after the upgrade: {res}"
    assert mark in json.dumps(res["result"]), "atlas_get does not return the write made before the upgrade"
    print(f"{PROJECT}: content, bindings, attribution, both passwords and the old agent token "
          "all survived the upgrade")
