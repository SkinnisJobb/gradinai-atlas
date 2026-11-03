"""Verify the context answer, `GET /api/context` (ADR 0044 §0, STORY-atlas-context-core).

Runs on a plain Postgres with the retrieval add-on OFF: the context answer is core and must work
without it (seeds then come from full text, and `mode=hybrid` says it degraded).

    A. the package: seeds, neighbourhood (parent, child, requires both ways), impact (transitive,
       depth-bounded), intents (own and inherited), decisions (ADRs ranked on the question's
       words), docs (search's document hits first); `bytes` is the real size
    B. budgets: every part capped by its parameter and saying `truncated`; refusals name the field
    C. DELETION-EQUIVALENCE for the whole package, for a person and an agent: a private project
       carries the question's words, a parent intent, an ADR, a node a public node requires, and
       a node in the middle of a public impact chain. Every answer equals the answer from the same
       database with the private rows physically removed, `bytes` included.
    D. bytes are bounded by the parameters on a wide fixture
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:  # pragma: no cover - no skip path, ever
    print(f"FATAL: {e}")
    sys.exit(1)

os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.pop("ATLAS_RETRIEVAL", None)  # core: proven with the add-on absent
os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
TEST_ORIGIN = "https://testserver"

from sqlalchemy import text  # noqa: E402

import app  # noqa: E402
import auth  # noqa: E402
import passwords  # noqa: E402
from models_adr import AdrRecord  # noqa: E402
from models_auth import AuthUser, NodeVisibility  # noqa: E402
from models_graph import Edge, Node, NodeDoc, NodeIntent  # noqa: E402

failures: list[str] = []
PW = "Correct-Horse-Battery-Context-9!"
Q = "boiler heating"


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def node(nid, ntype, summary, attrs=None):
    return Node(id=nid, type=ntype, name=nid, summary=summary, attributes=attrs or {},
                attention_count=0, completion=0.0, origin="atlas")


def edge(src, dst, t="contains"):
    return Edge(src=src, dst=dst, edge_type=t, origin="atlas")


def intent(nid, slug, title):
    return NodeIntent(node_id=nid, slug=slug, kind="constraint", title=title,
                      statement=f"{title}, stated in full.", position=0)


def adr(adr_id, project, seq, title, content):
    return AdrRecord(adr_id=adr_id, project_node_id=project, prefix=adr_id[:2], seq=seq, slug="s",
                     title=title, status="accepted", content=content, source="atlas")


PRIVATE_NODES = ["c-priv", "c-secret", "c-hid"]
PRIVATE_ADRS = ["CP001"]


def build() -> None:
    arch = {"path": "docs/nodes/c-boiler/arch.md", "kind": "architecture", "title": "Boiler architecture"}
    ext = {"path": "docs/adr/0001-x.md", "kind": "adr", "title": "An external ADR", "external": True}
    with app.session() as s:
        s.add_all([
            node("c-pub", "project", "the public house project"),
            node("c-boiler", "service", "boiler heating loop", {"docs": [arch, ext]}),
            node("c-valve", "service", "a valve with no matching words"),
            node("c-thermo", "service", "a thermostat"),
            node("c-app", "service", "an app on the thermostat"),
            node("c-far", "service", "three hops from the boiler"),
            node("c-pub-top", "service", "public, but reaches the boiler only through a hidden node"),
            node("c-priv", "project", "the private project"),
            node("c-secret", "service", "boiler heating secret"),
            node("c-hid", "service", "a hidden middle"),
        ])
        s.flush()
        s.add_all([
            edge("c-pub", "c-boiler"), edge("c-boiler", "c-valve"), edge("c-pub", "c-thermo"),
            edge("c-pub", "c-app"), edge("c-pub", "c-far"), edge("c-pub", "c-pub-top"),
            edge("c-thermo", "c-boiler", "requires"), edge("c-app", "c-thermo", "requires"),
            edge("c-far", "c-app", "requires"),
            edge("c-priv", "c-secret"), edge("c-priv", "c-hid"),
            edge("c-boiler", "c-secret", "requires"),      # a public node requires a hidden one
            edge("c-hid", "c-boiler", "requires"),         # hidden middle of a chain ...
            edge("c-pub-top", "c-hid", "requires"),        # ... whose far end is public
            NodeVisibility(node_id="c-priv", visibility="private"),
            NodeDoc(node_id="c-boiler", path=arch["path"], content="# Boiler\n\nThe boiler heating loop."),
            intent("c-pub", "house-rule", "The house rule"),
            intent("c-boiler", "boiler-rule", "The boiler rule"),
            intent("c-priv", "secret-rule", "The secret rule"),
            adr("CA001", "c-pub", 1, "Boiler heating decision", "We heat with a boiler."),
            # Shares no word with the question "thermostat"; it names the seed c-thermo only.
            adr("CA002", "c-pub", 2, "Replacement plan", "Applies to c-thermo."),
            adr("CP001", "c-priv", 1, "Private boiler heating decision", "Secret boiler heating."),
        ])
        s.commit()


def mk_user(uid, name, role, service=False):
    with app.session() as s:
        s.add(AuthUser(id=uid, subject=name, name=name, role=role, is_service=service,
                       must_change_password=False,
                       password_hash=None if service else passwords.hash_password(PW),
                       created_at=dt.datetime.now(dt.timezone.utc)))
        s.commit()


def sign_in(username):
    from fastapi.testclient import TestClient
    c = TestClient(app.app, base_url=TEST_ORIGIN)
    page = c.get("/auth/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": username, "password": PW, "csrf_token": token},
           headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
    return c


def bearer(uid):
    from fastapi.testclient import TestClient
    mk_user(uid, uid, "member", service=True)
    with app.session() as s:
        raw, _ = auth.mint_service_token(s, owner=s.get(AuthUser, uid), scopes=["read"], label=uid)
        s.commit()
    return TestClient(app.app, base_url=TEST_ORIGIN, headers={"Authorization": f"Bearer {raw}"})


def ids(part):
    return [i["id"] for i in part["items"]]


def section_a(owner) -> None:
    print("\n-- A. the package --")
    r = owner.get("/api/context", params={"about": Q})
    b = r.json()
    check("A: 200, with every part", r.status_code == 200 and all(
        k in b for k in ("seeds", "neighbourhood", "impact", "intents", "decisions", "docs", "bytes")),
          r.text[:300])
    check("A: add-on off: the default hybrid mode degrades to full text and says so",
          b.get("degraded") == "retrieval is off on this instance" and b.get("mode") == "hybrid")
    check("A: seeds are search's own hits (c-boiler first among the owner's)", "c-boiler" in ids(b["seeds"]),
          str(ids(b["seeds"])))
    rel = {i["id"]: i["via"]["relation"] for i in b["neighbourhood"]["items"]
           if i["via"]["from"] == "c-boiler"}
    seeds = set(ids(b["seeds"]))
    # A seed is never listed again as a neighbour. For the owner c-pub (its ADR matches) and
    # c-secret (its summary does) are seeds themselves, so each expected neighbour is either a
    # seed or present with the right relation.
    expected = {"c-pub": "parent", "c-valve": "child", "c-thermo": "required_by", "c-secret": "requires"}
    check("A: neighbourhood: parent, child and requires both ways, each with `via` (or already a seed)",
          all(n in seeds or rel.get(n) == r for n, r in expected.items())
          and not seeds & set(rel), f"seeds={sorted(seeds)} rel={rel}")
    imp = {i["id"]: i["depth"] for i in b["impact"]["items"]}
    check("A: impact: who requires the seed, transitively, to the default depth 2",
          imp.get("c-thermo") == 1 and imp.get("c-app") == 2 and "c-far" not in imp, str(imp))
    it = {i["slug"]: i for i in b["intents"]["items"]}
    check("A: intents: the seed's own and its inherited ones, as pointers without the statement",
          "boiler-rule" in it and it.get("house-rule", {}).get("inherited") is True
          and "statement" not in it["boiler-rule"], str(list(it)))
    check("A: decisions: the ADR whose text matches the question's words",
          "CA001" in [d["id"] for d in b["decisions"]["items"]], str(b["decisions"]))
    t = owner.get("/api/context", params={"about": "thermostat", "mode": "fulltext"}).json()
    check("A: decisions: an ADR that shares no word with the question is found through the seed it names",
          "c-thermo" in ids(t["seeds"]) and "CA002" in [d["id"] for d in t["decisions"]["items"]],
          f"seeds={ids(t['seeds'])} decisions={t['decisions']}")
    nb = ids(b["neighbourhood"])
    check("A: each neighbour is listed once, however many seeds it touches", len(nb) == len(set(nb)), str(nb))
    docs = b["docs"]["items"]
    check("A: docs: the seed's document hit first, marked matched; an external entry is marked",
          docs and docs[0]["path"] == "docs/nodes/c-boiler/arch.md" and docs[0]["matched"]
          and any(d.get("external") for d in docs), json.dumps(docs)[:300])
    raw = dict(b)
    n = raw.pop("bytes")
    check("A: `bytes` is the size of the rest of the package",
          n == len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()), str(n))
    full = owner.get("/api/context", params={"about": Q, "intents_full": 1}).json()
    check("A: intents_full=1 adds the statements",
          all("statement" in i for i in full["intents"]["items"]))
    ft = owner.get("/api/context", params={"about": Q, "mode": "fulltext"}).json()
    check("A: mode=fulltext does not degrade", "degraded" not in ft and ft["seeds"]["items"])


def section_b(owner) -> None:
    print("\n-- B. budgets and refusals --")
    b = owner.get("/api/context", params={"about": Q, "neighbours": 1, "impact_depth": 1}).json()
    check("B: neighbours=1 returns one and says truncated",
          len(b["neighbourhood"]["items"]) == 1 and b["neighbourhood"]["truncated"])
    check("B: impact_depth=1 stops at the direct dependants",
          ids(b["impact"]) == ["c-thermo"] or set(ids(b["impact"])) <= {"c-thermo", "c-pub-top", "c-hid"},
          str(ids(b["impact"])))
    for params, field in (({"about": ""}, "about"), ({"about": Q, "seeds": 0}, "seeds"),
                          ({"about": Q, "impact_depth": 9}, "impact_depth"),
                          ({"about": Q, "mode": "vector"}, "mode"), ({"about": Q, "intents_full": 2}, "intents_full")):
        r = owner.get("/api/context", params=params)
        check(f"B: a bad {field} is a 400 naming it", r.status_code == 400 and r.json().get("field") == field,
              r.text[:150])


def section_c(owner, member, agent) -> None:
    print("\n-- C. deletion-equivalence for the whole package --")
    o = owner.get("/api/context", params={"about": Q, "impact_depth": 3}).json()
    flat = json.dumps(o)
    check("C: the owner sees the private side (so the oracle is not vacuous)",
          "c-secret" in flat and "CP001" in flat and "c-pub-top" in ids(o["impact"]), str(ids(o["impact"])))
    requests = [{"about": Q}, {"about": Q, "impact_depth": 3, "intents_full": 1},
                {"about": Q, "mode": "fulltext", "families": "all"},
                {"about": "secret boiler", "neighbours": 100, "impact": 100},
                {"about": "private decision", "decisions": 20}]
    principals = {"member": member, "agent": agent}
    before = {(w, i): c.get("/api/context", params=p).json()
              for w, c in principals.items() for i, p in enumerate(requests)}
    leaked = [(k, x) for k, v in before.items() for x in PRIVATE_NODES + PRIVATE_ADRS + ["secret-rule"]
              if x in json.dumps(v)]
    check("C: a non-grantee's package names nothing private", not leaked, str(leaked[:5]))
    check("C: ...and the impact chain stops at the hidden node (c-pub-top is not reached through it)",
          not any("c-pub-top" in ids(v["impact"]) for v in before.values()))
    check("C: ...and the package is not empty", all(v["seeds"]["items"] for (_, i), v in before.items() if i < 3))
    with app.engine.begin() as c:
        p = {"ids": PRIVATE_NODES, "adrs": PRIVATE_ADRS}
        for stmt in ("DELETE FROM adr_record WHERE adr_id = ANY(:adrs)",
                     "DELETE FROM node_intent WHERE node_id = ANY(:ids)",
                     "DELETE FROM edge WHERE src = ANY(:ids) OR dst = ANY(:ids)",
                     "DELETE FROM node_visibility WHERE node_id = ANY(:ids)",
                     "DELETE FROM node WHERE id = ANY(:ids)"):
            c.execute(text(stmt), p)
    after = {(w, i): c.get("/api/context", params=p).json()
             for w, c in principals.items() for i, p in enumerate(requests)}
    diff = [k for k in before if before[k] != after[k]]
    check("C: DELETION-EQUIVALENCE: every package (all parts, truncation, bytes) equals the one with the "
          "private rows physically removed, for a person and an agent", not diff,
          f"{diff}: {json.dumps(before[diff[0]])[:250]} vs {json.dumps(after[diff[0]])[:250]}" if diff else "")


def section_d(owner) -> None:
    print("\n-- D. bytes are bounded by the parameters --")
    with app.session() as s:
        for i in range(120):
            s.add(node(f"c-wide-{i:03d}", "service", f"wide node {i} with a long summary " + "x" * 120))
        s.flush()
        for i in range(120):
            s.add(edge("c-boiler", f"c-wide-{i:03d}") if i < 60 else edge(f"c-wide-{i:03d}", "c-boiler", "requires"))
        s.commit()
    d = owner.get("/api/context", params={"about": Q}).json()
    m = owner.get("/api/context", params={"about": Q, "neighbours": 100, "impact": 100, "docs": 50,
                                          "intents": 100, "decisions": 20, "seeds": 20}).json()
    print(f"       default package {d['bytes']} B; all budgets at their maximum {m['bytes']} B")
    check("D: 180 neighbours and dependants exist, the default package holds its budgets and says truncated",
          len(d["neighbourhood"]["items"]) == 20 and d["neighbourhood"]["truncated"]
          and len(d["impact"]["items"]) == 20 and d["impact"]["truncated"])
    check("D: the default package stays under 16 KB", d["bytes"] < 16_000, str(d["bytes"]))
    check("D: raising the budgets raises the bytes, and the maxima still bound them (< 64 KB)",
          d["bytes"] < m["bytes"] < 64_000, f"{d['bytes']} -> {m['bytes']}")


if __name__ == "__main__":
    app.startup()
    build()
    mk_user("u-ctx-owner", "verify-ctx-owner", "owner")
    mk_user("u-ctx-member", "verify-ctx-member", "member")
    owner = sign_in("verify-ctx-owner")
    member = sign_in("verify-ctx-member")
    agent = bearer("u-ctx-agent")
    section_a(owner)
    section_b(owner)
    section_c(owner, member, agent)
    section_d(owner)
    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print("\nALL PASS")
