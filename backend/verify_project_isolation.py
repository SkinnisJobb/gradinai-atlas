"""
STORY-oss-11 / ADR 0041 §1 and §3: with `ATLAS_RBAC=projects`, a project is
sealed from an account that holds no role on it, on every read path — and two
claims about what that took.

Configuration: the work install's (`ATLAS_RBAC=projects`,
`ATLAS_DEFAULT_PROJECT_VISIBILITY=private`, `ATLAS_SHARED_PROJECT_ROLE=viewer`),
set before `app` is imported, as a deployment would.

  A. ISOLATION over the API. Two private projects, iso-a and iso-b; Ann is
     `editor` of A, Ben of B. Project A holds a capability, a bound task, a
     served document, an intent, an ADR and graph events, all carrying one
     unusual word. For Ben every one of them is ABSENT: `GET /api/node/{id}`,
     `/structure` and `/doc` 404 exactly like an id that never existed;
     `/api/search` (atlas_node_search) and `/api/workitems?q=` (atlas_search)
     return no hit and a `total` that does not count them; `/api/graph/outline`
     and `/api/graph/tree` do not list them; `/api/events` serves none of A's
     events and its `total` does not move when A changes; `/api/intents` and
     `/api/adrs` (list and get) hide A's. Ann sees every one — the control that
     makes Ben's empty answers mean something.
  B. INHERITED, NOT WRITTEN. The ADR registry and search filter through
     `_visible_node_ids` and contain no RBAC code (asserted on their source),
     and flipping RBAC off in process makes Ben's ADR read succeed: their
     isolation is the resolver's, which is what "one resolver" promised.
  C. SHARED BY DEFAULT IS TODAY'S REACH (board criterion 4). With every other
     setting at its default (`shared`, `editor`, implicit owner), RBAC on gives
     every principal of today's model exactly the visible set it has today
     (against the frozen oracle), and the same write reach; the one expected
     difference (ADR 0041 Adoption §3) is DELETE, which now needs `maintainer`,
     and is printed rather than hidden.

    python verify_project_isolation.py
"""
from __future__ import annotations

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb  # noqa: E402

DB = testdb.start()
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.setdefault(
    "ATLAS_FRONTEND_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend"),
)
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
os.environ["ATLAS_RBAC"] = "projects"
os.environ["ATLAS_DEFAULT_PROJECT_VISIBILITY"] = "private"
os.environ["ATLAS_SHARED_PROJECT_ROLE"] = "viewer"
os.environ.pop("ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER", None)

import dataclasses  # noqa: E402

from sqlalchemy import select  # noqa: E402

import app  # noqa: E402
from models_graph import Node  # noqa: E402
import deps  # noqa: E402
import rbac  # noqa: E402
import settings  # noqa: E402
from rbac_testkit import (  # noqa: E402
    acting, bind, build_today_model, check, finish, mk_edge, mk_node, mk_user,
    oracle_visible, resolver, sign_in,
)

WORD = "zirconquartz"
OWNER, ANN, BEN = "u-iso-owner", "u-iso-ann", "u-iso-ben"
A_IDS = ("iso-a", "iso-a-cap", "iso-a-epic")
DOC = "docs/nodes/iso-a-cap/notes.md"


def build() -> dict:
    mk_user(OWNER, "iso-owner", "owner")
    mk_user(ANN, "iso-ann")
    mk_user(BEN, "iso-ben")
    with app.session() as s:
        mk_node(s, "iso-a", "project", f"Project A {WORD}")
        mk_node(s, "iso-a-cap", "capability", f"Capability {WORD}",
                summary=f"the {WORD} capability", attributes={"docs": [{"path": DOC, "kind": "architecture",
                                                                 "title": "Notes"}]})
        mk_node(s, "iso-b", "project", "Project B")
        mk_node(s, "iso-b-cap", "capability", "Capability B")
        s.flush()
        mk_edge(s, "iso-a", "iso-a-cap")
        mk_edge(s, "iso-b", "iso-b-cap")
        s.commit()
    bind("iso-a", "user", ANN, "editor")
    bind("iso-b", "user", BEN, "editor")

    owner, ocsrf = sign_in("iso-owner")
    out = {}
    r = owner.put(f"/api/node/iso-a-cap/doc", json={"path": DOC, "content": f"# Notes\n\n{WORD} lives here.",
                                                     "csrf_token": ocsrf})
    check("setup: the owner (implicit project owner) writes A's document", r.status_code in (200, 201),
          f"{r.status_code} {r.text[:160]}")
    r = owner.post("/api/workitem", json={"type": "task", "name": f"Task {WORD}", "binding": "iso-a-cap",
                                          "csrf_token": ocsrf})
    check("setup: a task bound to A's capability", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    out["task"] = r.json().get("id")
    r = owner.post("/api/workitem", json={"type": "epic", "id": "iso-a-epic", "name": f"Epic {WORD}",
                                          "binding": "iso-a", "csrf_token": ocsrf})
    check("setup: an epic bound to A", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    r = owner.post("/api/node/iso-a-cap/intent", json={"slug": "iso-intent", "kind": "principle",
                                                      "title": f"Intent {WORD}", "statement": WORD,
                                                      "csrf_token": ocsrf})
    check("setup: an intent on A", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    r = owner.put("/api/adrs/new", json={"project": "iso-a", "title": f"Decision {WORD}", "prefix": "ISA",
                                         "content": f"# Decision\n\n{WORD}", "csrf_token": ocsrf})
    check("setup: an ADR in A", r.status_code in (200, 201), f"{r.status_code} {r.text[:160]}")
    out["adr"] = (r.json() or {}).get("id")
    out["owner"], out["ocsrf"] = owner, ocsrf
    return out


def _ids_in(payload) -> str:
    return json.dumps(payload)


def section_a(ctx) -> None:
    print("\n-- A. project A is absent for Ben on every read path, present for Ann --")
    ann, _ = sign_in("iso-ann")
    ben, _ = sign_in("iso-ben")
    a_nodes = list(A_IDS) + [ctx["task"]]

    never = ben.get("/api/node/iso-never-existed")
    for nid in a_nodes:
        r = ben.get(f"/api/node/{nid}")
        check(f"GET /api/node/{nid}: Ben 404, same shape as a nonexistent id",
              r.status_code == 404 and set(r.json()) == set(never.json()), f"{r.status_code}")
        check(f"...Ann 200", ann.get(f"/api/node/{nid}").status_code == 200)
        r = ben.get(f"/api/node/{nid}/structure")
        check(f"GET /api/node/{nid}/structure: Ben 404", r.status_code == 404, str(r.status_code))
    r = ben.get("/api/node/iso-a-cap/doc", params={"path": DOC})
    check("GET /api/node/iso-a-cap/doc: Ben 404", r.status_code == 404, str(r.status_code))
    check("...Ann 200", ann.get("/api/node/iso-a-cap/doc", params={"path": DOC}).status_code == 200)

    for label, url in (("/api/search (atlas_node_search)", f"/api/search?q={WORD}"),
                       ("/api/workitems?q= (atlas_search)", f"/api/workitems?q={WORD}")):
        rb, ra = ben.get(url).json(), ann.get(url).json()
        check(f"{label}: Ben gets no hit and total 0", rb.get("total") == 0 and not rb.get("items"),
              f"total={rb.get('total')}")
        check(f"{label}: Ann finds them", (ra.get("total") or 0) > 0, f"total={ra.get('total')}")

    for url in ("/api/graph/outline?families=object,bridge,workitem", "/api/graph/tree"):
        tb, ta = ben.get(url), ann.get(url)
        check(f"{url}: no A node for Ben", tb.status_code == 200 and "iso-a" not in tb.text,
              str(tb.status_code))
        check(f"{url}: Ann sees A", "iso-a-cap" in ta.text)
        check(f"{url}: Ben still sees his own project", "iso-b-cap" in tb.text)

    ev_b, ev_a = ben.get("/api/events?page_size=200").json(), ann.get("/api/events?page_size=200").json()
    check("/api/events: none of A's events reach Ben",
          all(i not in _ids_in(ev_b.get("items")) for i in a_nodes), f"total={ev_b.get('total')}")
    check("/api/events: Ann reads them", "iso-a-cap" in _ids_in(ev_a.get("items")))
    before = ev_b.get("total")
    r = ctx["owner"].patch("/api/node/iso-a-cap", json={"name": f"Capability {WORD} renamed",
                                                        "csrf_token": ctx["ocsrf"]})
    after = ben.get("/api/events?page_size=200").json().get("total")
    check("/api/events: Ben's TOTAL does not move when A changes (not countable either)",
          r.status_code == 200 and before == after, f"{before} -> {after}")

    ib = ben.get("/api/intents").text
    check("/api/intents: A's intent hidden from Ben", "iso-intent" not in ib)
    check("/api/intents: Ann reads it", "iso-intent" in ann.get("/api/intents").text)

    lb = ben.get("/api/adrs?page_size=200").json()
    check("/api/adrs: A's ADR absent from Ben's list", ctx["adr"] and ctx["adr"] not in _ids_in(lb),
          f"total={lb.get('total')}")
    check("/api/adrs/{id}: Ben 404", ben.get(f"/api/adrs/{ctx['adr']}").status_code == 404)
    check("/api/adrs/{id}: Ann 200", ann.get(f"/api/adrs/{ctx['adr']}").status_code == 200)
    sb = ben.get(f"/api/search?q={ctx['adr']}").json()
    check("search by ADR id finds nothing for Ben", sb.get("total") == 0, f"total={sb.get('total')}")


def section_b(ctx) -> None:
    print("\n-- B. the ADR registry and search inherit RBAC from the resolver --")
    here = os.path.dirname(os.path.abspath(__file__))
    for mod in ("routers/adrs.py", "routers/search.py"):
        with open(os.path.join(here, mod), encoding="utf-8") as fh:
            src = fh.read()
        if mod.endswith("adrs.py"):
            # the one write route checks a role; the READ paths must not mention rbac at all
            src = src.split('@router.put("/api/adrs/{adr_id}")')[0]
        # The module imports rbac for its one write route; no READ code may USE it.
        uses = [ln.strip() for ln in src.splitlines()
                if ("rbac." in ln or "_require_project_role(" in ln) and not ln.lstrip().startswith("#")]
        check(f"{mod}: its read paths contain no RBAC code", not uses, "; ".join(uses)[:200])
        check(f"{mod}: ...and filter through _visible_node_ids", "_visible_node_ids" in src)
    ben, _ = sign_in("iso-ben")
    prev = rbac.use(dataclasses.replace(rbac.SETTINGS, mode="off"))
    try:
        off = ben.get(f"/api/adrs/{ctx['adr']}").status_code
        hits = ben.get(f"/api/search?q={WORD}").json().get("total")
    finally:
        rbac.use(prev)
    check("RBAC flipped OFF in process: Ben reads the ADR (nothing is marked private)", off == 200, str(off))
    check("...and search finds A's rows", (hits or 0) > 0, f"total={hits}")
    check("flipped back ON: 404 again", ben.get(f"/api/adrs/{ctx['adr']}").status_code == 404)


def section_c() -> None:
    print("\n-- C. RBAC on with shared-by-default = today's reach (board criterion 4) --")
    principals = build_today_model()
    defaults = settings.RbacSettings(mode="projects")  # shared, editor, implicit owner
    prev = rbac.use(defaults)
    try:
        with app.session() as s:
            all_ids = set(s.scalars(select(Node.id)).all())
        for name, p in principals.items():
            with app.session() as s:
                want = oracle_visible(s, p)
            got = resolver(p)
            norm = (lambda v: all_ids if v is None else v)
            # The isolation fixture above lives in the same database: its two
            # PRIVATE-by-binding projects are out of scope for this comparison.
            scope = {i for i in all_ids if not i.startswith("iso-") and not i.startswith("TASK-")}
            check(f"{name}: visible set equals today's", norm(got) & scope == norm(want) & scope,
                  f"diff {sorted((norm(got) ^ norm(want)) & scope)[:6]}")
        alice = principals["alice"]
        with acting(alice), app.session() as s:
            w = deps._require_project_role(s, ["t-shared-cap"], rbac.EDITOR)
            d = deps._require_project_role(s, ["t-shared-cap"], rbac.MAINTAINER)
            g = deps._require_project_role(s, ["t-private-cap"], rbac.EDITOR)
        check("a member writes in a shared project (shared role editor = today)", w is None)
        check("a member writes in a private project she is GRANTED (grant = editor)", g is None)
        check("THE EXPECTED DIFFERENCE: deleting now needs maintainer (403 with RBAC on)",
              d is not None and d.status_code == 403)
        bot = principals["alice-bot read+write"]
        with acting(bot), app.session() as s:
            w = deps._require_project_role(s, ["t-shared-cap"], rbac.EDITOR)
        check("her write-token agent writes there too", w is None)
    finally:
        rbac.use(prev)


if __name__ == "__main__":
    app.startup()
    check("this process runs with RBAC on (the env above reached settings)", rbac.enabled(),
          str(rbac.SETTINGS))
    ctx = build()
    section_a(ctx)
    section_b(ctx)
    section_c()
    finish()
