"""The install's own system node and its handbook, on the live OSS install (install smoke, leg 4).

STORY-atlas-system-node-handbook: `.env.example` sets ATLAS_SEED=none and ATLAS_PUBLIC_DOCS=1, so
a fresh install carries `atlas-system` and one child per part, each with handbook documents, and
serves /docs to a stranger through the ingress, under the ingress's own CSP.
"""
from __future__ import annotations

import json

import httpx

import atlas_smoke as smoke

CHILDREN = ("atlas-system-api", "atlas-system-mcp", "atlas-system-postgres",
            "atlas-system-gateway", "atlas-system-search", "atlas-system-retrieval")


def test_each_person_sees_the_system_node_and_reads_its_handbook():
    for user in (smoke.OWNER, smoke.MEMBER):
        out = smoke.mcp(user, ("atlas_get", {"id": "atlas-system"}),
                        ("atlas_doc_read", {"node_id": "atlas-system",
                                            "path": "docs/nodes/atlas-system/concepts.md"}),
                        *[("atlas_get", {"id": c}) for c in CHILDREN])
        root, doc, *kids = out["results"]
        print(f"{user}'s agent: atlas_get atlas-system ok={root['ok']}; concepts doc ok={doc['ok']}; "
              f"children ok={[k['ok'] for k in kids]}")
        assert root["ok"], root
        assert doc["ok"] and "# Concepts" in json.dumps(doc["result"]), doc
        assert all(k["ok"] for k in kids), kids


def test_the_handbook_is_public_through_the_ingress():
    with httpx.Client(base_url=smoke.URL, verify=smoke.ssl_ctx(), timeout=30,
                      follow_redirects=False) as c:
        for path in ("/docs/", "/docs/atlas-system/security.html", "/docs/docs.css"):
            r = c.get(path)
            csp = r.headers.get("content-security-policy", "")
            print(f"anonymous GET {path} -> {r.status_code}, {len(r.content)} bytes")
            assert r.status_code == 200, (path, r.status_code)
            directives = {d.strip().split(" ")[0]: d.strip() for d in csp.split(";") if d.strip()}
            script = directives.get("script-src", directives.get("default-src", ""))
            assert csp and "'unsafe-inline'" not in script, (path, csp)
        assert "Atlas handbook" in c.get("/docs/").text
        r = c.get("/docs/not-a-page.html", headers={"Accept": "application/json"})
        print(f"anonymous GET /docs/not-a-page.html -> {r.status_code}")
        assert r.status_code == 401
        r = c.get("/board", headers={"Accept": "application/json"})
        assert r.status_code == 401, "the board must stay gated"
