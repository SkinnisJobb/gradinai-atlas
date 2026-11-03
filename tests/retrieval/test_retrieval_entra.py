"""The retrieval add-on in ENTRA mode on the live OSS install (install smoke, leg 8c; OSS-26 A).

atlas-api runs with deploy/oss/retrieval.yml and deploy/oss/retrieval-workload-identity.yml,
ATLAS_EMBED_AUTH_HEADER=entra, ATLAS_EMBED_KEY_SOURCE=/dev/null, no ATLAS_EMBED_KEY_FILE and no
./secrets/embed_key at all. Its token comes from the workload-identity flow
(backend/retrieval/entra.py): the federated token file the overlay mounts is exchanged at the
stub's /<tenant>/oauth2/v2.0/token, and the access token it gets back is what /embeddings
accepts (tests/retrieval/embed_stub.py). The workflow reads the stub's log for the exchange.

  test_entra_indexes_without_a_key_file  the index fills under a new model name (so every chunk
                                         is embedded again, with the token), health `on`, the
                                         status route says entra and no key file, and a hybrid
                                         search has semantic hits
"""
from __future__ import annotations

import time

import atlas_smoke as smoke

from test_retrieval import STUB_HOST, _brief, _health, _search, _semantic, _status

MODEL = "install-smoke-stub-entra"
INDEX_WAIT_S = 240


def test_entra_indexes_without_a_key_file():
    owner = smoke.signed_in(smoke.OWNER)
    deadline = time.monotonic() + INDEX_WAIT_S
    while True:
        st = _status(owner)
        print(f"status: {_brief(st)} model={st.get('model')!r}")
        if (st.get("state") == "on" and st.get("model") == MODEL and st.get("passes", 0) >= 1
                and st.get("pending_sources") == 0 and (st.get("chunks") or 0) > 0):
            break
        assert not str(st.get("state", "")).startswith("unavailable"), f"add-on unavailable: {st['state']}"
        assert time.monotonic() < deadline, f"the index did not fill within {INDEX_WAIT_S} s: {_brief(st)}"
        time.sleep(3)
    print(f"status wiring: endpoint_host={st['endpoint_host']} model={st['model']} "
          f"auth_header={st['auth_header']} key_file_configured={st['key_file_configured']} "
          f"ca_bundle_configured={st['ca_bundle_configured']}")
    assert st["endpoint_host"] == STUB_HOST
    assert st["auth_header"] == "entra"
    assert st["key_file_configured"] is False, "entra mode runs with no key file"
    assert st["last_ok_at"] and st["last_error"] is None
    print(f"ENTRA MET: pending_sources=0 with chunks={st['chunks']} over {st['nodes']} nodes, "
          f"model {MODEL}, no key file")

    health = _health(owner)
    print(f"/api/health retrieval: {health.get('retrieval')!r}")
    assert health.get("retrieval") == "on"

    hybrid = _search(owner, mode="hybrid")
    sem = _semantic(hybrid["items"])
    print(f"/api/search mode=hybrid: total={hybrid['total']} semantic hits={sem[:8]}")
    assert "degraded" not in hybrid and sem
