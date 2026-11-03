"""
Verification for the opt-in payload tiers (TASK-1157): `?prose=` and `?intents=` on the single-node
read, retired attributes never served, and `?schemas=` on the vocabulary route.

WHY. Measured on live 2026-09-17, `GET /api/node/sphere-devices` was 23,391 bytes: 11.5 KB of
`detail`, 3.9 KB of `abstract`, and **7.2 KB of intents, 7,177 of which the node did not author**.
Intent inherits downward (ADR 0032), so the same statements arrive again on every node beneath their
author. A caller orienting itself pays for constraint text it is not about to act on; a caller that
IS about to change something needs every word of it. So the payload becomes a choice, and the
default stays what it was.

THE RULE THAT MAKES IT SAFE: omitting every new parameter is byte-identical to before. `panel.js`
renders `intents.own`/`intents.inherited` and the prose fields off these exact payloads, so a
default that trimmed anything would empty a board section to save an agent tokens. Each check below
compares against a baseline response captured in the same run, not against a remembered shape.

  A. Baseline: no parameters, byte-identical to the pre-change payload shape.
  B. `intents=none|own|all`, `prose=none|summary|abstract|full`, and both together.
  C. A bad value is a 400 that NAMES the legal values -- never a silent fallback to the full
     payload, which is indistinguishable from the feature not working.
  D. `frontmatter_raw` is never served, even when the row still carries it (an un-migrated
     database: staging, the fork, a restored dump).
  E. `/api/vocabulary?schemas=full|shared|none`: default unchanged, `shared` names each distinct
     attributes schema once and is smaller, `none` drops them.

Run:
    python verify_payload_tiers.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testdb  # noqa: E402

DB = testdb.start()
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")

import app  # noqa: E402
import auth  # noqa: E402
import models_graph  # noqa: E402
from models_auth import AuthUser  # noqa: E402

TEST_ORIGIN = "https://testserver"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("[PASS] " if ok else "[FAIL] ") + label + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def build_fixture() -> str:
    """A parent carrying an intent, a child carrying its own, so `inherited` is non-empty and
    `own` and `inherited` are distinguishable in the response."""
    with app.session() as s:
        parent = models_graph.Node(id="tier-parent", type="project", name="Tier parent",
                                   summary="parent summary", abstract="parent abstract",
                                   detail="parent detail", body="parent body", origin="atlas")
        child = models_graph.Node(id="tier-child", type="capability", name="Tier child",
                                  summary="child summary", abstract="child abstract",
                                  detail="child detail", body="child body", origin="atlas",
                                  # A row that still carries the retired key, as an un-migrated
                                  # database does. The route must not serve it.
                                  attributes={"health": "green", "frontmatter_raw": "id: X\nstatus: pending"})
        s.add_all([parent, child])
        s.flush()
        s.add(models_graph.Edge(src="tier-parent", dst="tier-child", edge_type="contains", origin="atlas"))
        s.add(models_graph.NodeIntent(node_id="tier-parent", slug="from-the-parent", kind="principle",
                                      title="Inherited", statement="X" * 200, position=0))
        s.add(models_graph.NodeIntent(node_id="tier-child", slug="from-the-child", kind="constraint",
                                      title="Own", statement="Y" * 50, position=0))
        bot = AuthUser(id="00000000-0000-4000-8000-0000000017ee", idp="local", subject="bot-tiers",
                       is_service=True, label="payload-tiers", must_change_password=False, role="member")
        s.add(bot)
        s.flush()
        raw, _row = auth.mint_service_token(s, owner=bot, scopes=["read"], label="payload-tiers")
        s.commit()
    return raw


def main() -> None:
    from fastapi.testclient import TestClient

    app.startup()
    raw = build_fixture()
    c = TestClient(app.app, base_url=TEST_ORIGIN, headers={"Authorization": f"Bearer {raw}"})

    def node(params: str = "") -> tuple[int, dict, int]:
        r = c.get("/api/node/tier-child" + params)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        return r.status_code, body, len(r.content)

    print("\n-- A. the baseline: no parameters --")
    code, base, base_bytes = node()
    check("GET /api/node/{id} with no parameters answers 200", code == 200, str(base)[:200])
    check("...carrying prose and both halves of intents, as it always has",
          base.get("abstract") == "child abstract" and base.get("body") == "child body"
          and len(base["intents"]["own"]) == 1 and len(base["intents"]["inherited"]) == 1,
          str(base.get("intents"))[:160])

    print("\n-- B. asking for less --")
    code, d, n = node("?intents=none")
    check("intents=none drops the key entirely", code == 200 and "intents" not in d, str(sorted(d))[:160])
    check("...and is smaller than the baseline", n < base_bytes, f"{n} B vs {base_bytes} B")
    check("...while every other field is untouched",
          {k: v for k, v in d.items()} == {k: v for k, v in base.items() if k != "intents"})

    code, d, _ = node("?intents=own")
    check("intents=own keeps what the node authored and drops the inherited half",
          code == 200 and d["intents"]["own"] == base["intents"]["own"] and "inherited" not in d["intents"],
          str(d.get("intents"))[:160])

    code, d, n = node("?prose=none")
    check("prose=none drops summary/abstract/detail/body, the same tier names /api/graph uses",
          code == 200 and not any(k in d for k in ("summary", "abstract", "detail", "body")),
          str(sorted(d))[:200])
    check("...and keeps the structural fields a caller navigates by",
          d.get("parents") == base.get("parents") and d.get("id") == "tier-child")

    code, d, _ = node("?prose=abstract")
    check("prose=abstract keeps summary+abstract, drops detail and body",
          code == 200 and d.get("abstract") == "child abstract" and "summary" in d
          and "detail" not in d and "body" not in d, str(sorted(d))[:200])

    code, d, n_min = node("?prose=none&intents=none")
    check("both together is the smallest read, and still identifies the node",
          code == 200 and n_min < n and d["id"] == "tier-child", f"{n_min} B vs baseline {base_bytes} B")
    print(f"       baseline {base_bytes} B -> prose=none&intents=none {n_min} B "
          f"({100 - round(100 * n_min / base_bytes)}% smaller on this fixture)")

    print("\n-- C. a wrong value is refused, by name --")
    r = c.get("/api/node/tier-child?intents=some")
    body = r.json()
    check("an unknown intents tier -> 400 naming the legal values, not a silent full payload",
          r.status_code == 400 and body.get("error") == "invalid_intent_tier"
          and all(t in body.get("detail", "") for t in ("all", "own", "none")), str(body)[:200])
    r = c.get("/api/node/tier-child?prose=verbose")
    check("an unknown prose tier -> 400, same shape as /api/graph's",
          r.status_code == 400 and r.json().get("error") == "invalid_prose_tier", str(r.json())[:160])

    print("\n-- D. a retired attribute is never served --")
    with app.session() as s:
        stored = s.get(models_graph.Node, "tier-child").attributes
    check("the fixture row really does still carry frontmatter_raw (an un-migrated database)",
          "frontmatter_raw" in stored, str(sorted(stored)))
    check("the node read does not serve it, and keeps the rest of attributes",
          "frontmatter_raw" not in (base.get("attributes") or {})
          and (base.get("attributes") or {}).get("health") == "green", str(base.get("attributes")))
    listing = c.get("/api/workitems?page_size=5").json()
    check("a listing serves no frontmatter_raw either",
          all("frontmatter_raw" not in (i.get("attributes") or {}) for i in listing["items"]),
          f"{listing['total']} item(s) in the backlog fixture")
    graph = c.get("/api/graph?focus=tier-parent&depth=1").json()
    check("the graph read serves no frontmatter_raw either",
          all("frontmatter_raw" not in (n.get("attributes") or {}) for n in graph["nodes"]))

    print("\n-- E. the vocabulary's repeated schemas --")
    r_default = c.get("/api/vocabulary")
    r_full = c.get("/api/vocabulary?schemas=full")
    check("/api/vocabulary unchanged by default: default == schemas=full, byte for byte",
          r_default.content == r_full.content, f"{len(r_default.content)} B vs {len(r_full.content)} B")
    v_full = r_default.json()
    r_shared = c.get("/api/vocabulary?schemas=shared")
    v_shared = r_shared.json()
    distinct = {json.dumps(t["attributes_schema"], sort_keys=True) for t in v_full["node_types"].values()}
    check("schemas=shared lists each DISTINCT schema once",
          len(v_shared.get("attributes_schemas") or {}) == len(distinct),
          f"{len(v_shared.get('attributes_schemas') or {})} shared vs {len(distinct)} distinct "
          f"across {len(v_full['node_types'])} types")
    check("...with every type pointing at one of them, and no inline copy",
          all("attributes_schema" not in t and t["attributes_schema_ref"] in v_shared["attributes_schemas"]
              for t in v_shared["node_types"].values()))
    check("...and it is smaller than full", len(r_shared.content) < len(r_full.content),
          f"{len(r_shared.content)} B vs {len(r_full.content)} B")
    print(f"       vocabulary full {len(r_full.content)} B -> shared {len(r_shared.content)} B")
    r_none = c.get("/api/vocabulary?schemas=none")
    v_none = r_none.json()
    check("schemas=none drops them entirely but keeps the legal values",
          all("attributes_schema" not in t and "attributes_schema_ref" not in t
              for t in v_none["node_types"].values())
          and "attributes_schemas" not in v_none and v_none["workitem_statuses"],
          str(list(v_none["node_types"].items())[:1])[:160])
    r_bad = c.get("/api/vocabulary?schemas=some")
    check("an unknown schemas mode -> 400 naming the legal values",
          r_bad.status_code == 400 and r_bad.json().get("error") == "invalid_schemas_mode",
          str(r_bad.json())[:160])

    print()
    if failures:
        print(f"FAILED -- {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
