"""The retrieval add-on on the live OSS install (install smoke, leg 8b; STORY-oss-17 criterion 7).

The workflow runs these in three steps around what it does to the stack, by test id:

  test_indexes_through_the_stub      atlas-api recreated with deploy/oss/retrieval.yml,
                                     ATLAS_RETRIEVAL=on and the stub endpoint
                                     (tests/retrieval/embed_stub.py) over TLS against the install
                                     CA: the index fills (pending_sources 0, chunks > 0), health
                                     says `on`, a hybrid search has semantic hits, and the status
                                     route is the owner's only
  test_stub_down_degrades_to_keyword the stub's endpoint stopped, then a reindex: health says
                                     `degraded: …`, the index is kept, and search answers 200 by
                                     the API and by MCP (atlas_node_search), full text, with
                                     `degraded` on a hybrid query
  test_base_install_restored         atlas-api back on the shipped compose file alone: no
                                     `retrieval` field on /api/health, so leg 9 starts from the
                                     install every earlier leg tested

Signs in as alice (owner) through the real form: the status and reindex routes are owner-only,
and an agent token is never an owner's (a bot's own role is always member).
"""
from __future__ import annotations

import json
import time

import atlas_smoke as smoke

STUB_HOST = "gateway"
#: A word the shipped system nodes (atlas-system-postgres and its handbook) carry, so the
#: keyword half always has hits and the stub's bag-of-words vectors have something to match.
QUERY = "postgres"
INDEX_WAIT_S = 240
DEGRADE_WAIT_S = 90


def _status(s: smoke.Session) -> dict:
    r = s.get("/api/retrieval/status")
    assert r.status_code == 200, f"GET /api/retrieval/status: {r.status_code} {r.text[:300]}"
    return r.json()


def _brief(st: dict) -> str:
    keys = ("state", "passes", "sources", "chunks", "nodes", "pending_sources", "last_error")
    return ", ".join(f"{k}={st.get(k)!r}" for k in keys)


def _health(s: smoke.Session) -> dict:
    r = s.get("/api/health")
    assert r.status_code == 200, f"GET /api/health: {r.status_code}"
    return r.json()


def _search(s: smoke.Session, **params) -> dict:
    r = s.get("/api/search", q=QUERY, **params)
    assert r.status_code == 200, f"GET /api/search {params}: {r.status_code} {r.text[:300]}"
    return r.json()


def _node_search(**args) -> dict:
    out = smoke.mcp(smoke.OWNER, ("atlas_node_search", {"q": QUERY, **args}))
    env = out["results"][0]
    assert env["ok"], f"atlas_node_search {args}: {json.dumps(env)[:400]}"
    assert "atlas_node_search" in out["tools"]
    return env["result"]


def _semantic(items: list[dict]) -> list[str]:
    return [i["id"] for i in items if "semantic" in (i.get("matched_in") or [])]


def test_indexes_through_the_stub():
    owner = smoke.signed_in(smoke.OWNER)
    deadline = time.monotonic() + INDEX_WAIT_S
    while True:
        st = _status(owner)
        print(f"status: {_brief(st)}")
        if (st.get("state") == "on" and st.get("passes", 0) >= 1
                and st.get("pending_sources") == 0 and (st.get("chunks") or 0) > 0):
            break
        assert not str(st.get("state", "")).startswith("unavailable"), f"add-on unavailable: {st['state']}"
        assert time.monotonic() < deadline, f"the index did not fill within {INDEX_WAIT_S} s: {_brief(st)}"
        time.sleep(3)
    # What the status route says about the wiring: the stub's host, a key FILE, the CA bundle.
    print(f"status wiring: endpoint_host={st['endpoint_host']} model={st['model']} dim={st['dim']} "
          f"scope={st['scope']} auth_header={st['auth_header']} key_file_configured="
          f"{st['key_file_configured']} ca_bundle_configured={st['ca_bundle_configured']} "
          f"chunks_other_models={st['chunks_other_models']}")
    assert st["endpoint_host"] == STUB_HOST
    assert st["key_file_configured"] is True and st["ca_bundle_configured"] is True
    assert st["scope"] == "shared", "the leg runs the product default scope"
    assert st["last_ok_at"] and st["last_error"] is None
    print(f"ASSERT 1 MET: pending_sources=0 with chunks={st['chunks']} over {st['nodes']} nodes "
          f"after {st['passes']} pass(es)")

    health = _health(owner)
    print(f"/api/health retrieval: {health.get('retrieval')!r}")
    assert health.get("retrieval") == "on"
    print("ASSERT 2 MET: /api/health retrieval == 'on'")

    hybrid = _search(owner, mode="hybrid")
    sem = _semantic(hybrid["items"])
    print(f"/api/search mode=hybrid: total={hybrid['total']} degraded={hybrid.get('degraded')!r} "
          f"semantic hits={sem[:8]}")
    assert "degraded" not in hybrid and sem, "a hybrid search with the stub up has semantic hits"
    res = _node_search(mode="hybrid")
    print(f"MCP atlas_node_search mode=hybrid: total={res.get('total')} degraded={res.get('degraded')!r} "
          f"semantic hits={_semantic(res.get('items', []))[:8]}")
    assert "degraded" not in res and _semantic(res.get("items", []))

    member = smoke.signed_in(smoke.MEMBER)
    r = member.get("/api/retrieval/status")
    print(f"bob (member) GET /api/retrieval/status -> {r.status_code}")
    assert r.status_code == 403


def test_stub_down_degrades_to_keyword():
    owner = smoke.signed_in(smoke.OWNER)
    before = _status(owner)
    print(f"before the reindex: {_brief(before)}")
    r = owner.write("POST", "/api/retrieval/reindex", {"rebuild": False})
    print(f"POST /api/retrieval/reindex -> {r.status_code} {r.text[:200]}")
    assert r.status_code == 202, r.text[:300]
    deadline = time.monotonic() + DEGRADE_WAIT_S
    while True:
        st = _status(owner)
        print(f"status: {_brief(st)}")
        if st.get("passes", 0) > before.get("passes", 0) and str(st.get("state", "")).startswith("degraded: "):
            break
        assert time.monotonic() < deadline, f"not degraded within {DEGRADE_WAIT_S} s: {_brief(st)}"
        time.sleep(2)
    assert STUB_HOST in st["last_error"] and "unreachable" in st["last_error"], st["last_error"]
    assert (st.get("chunks") or 0) == before["chunks"], "a failed pass leaves the index as it was"

    health = _health(owner)
    print(f"/api/health retrieval: {health.get('retrieval')!r}")
    assert str(health.get("retrieval", "")).startswith("degraded: ")
    print("ASSERT 3a MET: /api/health retrieval is 'degraded: …' with the stub stopped")

    plain = _search(owner)
    print(f"/api/search (keyword): 200, total={plain['total']}, ids={[i['id'] for i in plain['items']][:8]}")
    assert plain["total"] > 0
    hybrid = _search(owner, mode="hybrid")
    print(f"/api/search mode=hybrid: 200, total={hybrid['total']}, degraded={hybrid.get('degraded')!r}, "
          f"semantic hits={_semantic(hybrid['items'])}")
    assert hybrid.get("degraded") and hybrid["total"] > 0 and not _semantic(hybrid["items"])

    res = _node_search()
    print(f"MCP atlas_node_search: ok, total={res.get('total')}")
    assert res.get("total", 0) > 0
    res = _node_search(mode="hybrid")
    print(f"MCP atlas_node_search mode=hybrid: ok, total={res.get('total')}, degraded={res.get('degraded')!r}")
    assert res.get("degraded") and res.get("total", 0) > 0 and not _semantic(res.get("items", []))
    print("ASSERT 3b MET: search answers 200 (API and MCP), full text, degraded on hybrid")


def test_base_install_restored():
    owner = smoke.signed_in(smoke.OWNER)
    health = _health(owner)
    print(f"/api/health keys include retrieval: {'retrieval' in health}")
    assert "retrieval" not in health
    assert _search(owner)["total"] > 0
