"""
Verification for TASK-1025 (single binding plus inherited effective binding).

No pytest suite exists in this repo yet (see verify_object_graph.py's
docstring) — this follows the same standalone, run-it-and-read-it convention.

THREE SECTIONS, proving different things:

  A. `attention.effective_bindings()` against a real, disposable Postgres
     (`testdb`) with a hand-built graph: own binding wins, an unbound task
     inherits its story's, a chain inherits from the nearest bound ancestor,
     an unbound item under an unbound chain stays absent, and a `contains`
     CYCLE terminates instead of hanging the read path.

  B. THE ACCEPTANCE TEST FOR THE WIRING. `work_load()` now derives its
     per-object counts from effective bindings instead of raw `works_on`
     edges. When every workitem carries its own binding those two must agree
     EXACTLY, node for node — which is the state ADR 0030's binding pass left
     live in. This section builds both and asserts they are identical, so the
     change is provably a no-op today and only ever adds coverage later.

  C. The constraint is GONE (TASK-1025 step 5): `models_graph` exposes no
     `validate_required_world_link`, the seeds set no `requires_edge`, and a
     brand-new workitem with no world link persists without complaint. The
     COLUMN must still exist — dropping it was explicitly out of scope.

Run (from an environment with SQLAlchemy/psycopg2/jsonschema installed):
    python verify_effective_binding.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb  # noqa: E402

from collections import defaultdict  # noqa: E402

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import attention  # noqa: E402
import models_graph  # noqa: E402
from models_graph import Edge, Node, NodeType  # noqa: E402
from node_types import ensure_all_node_types  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def mk_node(s: Session, nid: str, ntype: str) -> None:
    s.add(Node(id=nid, type=ntype, name=nid, attributes={}, attention_count=0,
               completion=0.0, origin="atlas"))


def mk_edge(s: Session, src: str, dst: str, etype: str) -> None:
    s.add(Edge(src=src, dst=dst, edge_type=etype, directed=True, origin="atlas"))


CONTAINS = models_graph.WORK_HIERARCHY_EDGE_TYPE


def eng(db):
    """testdb hands back a DSN (`db.url`), not an Engine — see testdb.py."""
    return create_engine(db.url, future=True)


def section_a(db) -> None:
    print("\n-- A. effective_bindings(): own wins, else nearest bound ancestor --")
    db.reset_schema()
    models_graph.Base.metadata.create_all(eng(db))
    with Session(eng(db), future=True) as s:
        ensure_all_node_types(s)

        for nid in ("svc-a", "svc-b"):
            mk_node(s, nid, "service")
        for nid, t in (("EPIC-x", "epic"), ("STORY-x", "story"), ("TASK-own", "task"),
                       ("TASK-inherit", "task"), ("TASK-deep", "task"),
                       ("EPIC-orphan", "epic"), ("STORY-orphan", "story"),
                       ("TASK-orphan", "task")):
            mk_node(s, nid, t)
        s.flush()

        # EPIC-x -> STORY-x -> {TASK-own, TASK-inherit -> TASK-deep}
        mk_edge(s, "EPIC-x", "STORY-x", CONTAINS)
        mk_edge(s, "STORY-x", "TASK-own", CONTAINS)
        mk_edge(s, "STORY-x", "TASK-inherit", CONTAINS)
        mk_edge(s, "TASK-inherit", "TASK-deep", CONTAINS)
        mk_edge(s, "EPIC-orphan", "STORY-orphan", CONTAINS)
        mk_edge(s, "STORY-orphan", "TASK-orphan", CONTAINS)
        # Only the story and the one task are bound.
        mk_edge(s, "STORY-x", "svc-a", "works_on")
        mk_edge(s, "TASK-own", "svc-b", "works_on")
        s.commit()   # A2 opens its own session; without this the setup vanishes

        eff = attention.effective_bindings(s)
        check("a workitem's OWN binding wins over its ancestor's",
              eff.get("TASK-own") == "svc-b", f"got {eff.get('TASK-own')!r}")
        check("an unbound task inherits its story's binding",
              eff.get("TASK-inherit") == "svc-a", f"got {eff.get('TASK-inherit')!r}")
        check("inheritance walks past an unbound parent to the nearest bound ancestor",
              eff.get("TASK-deep") == "svc-a", f"got {eff.get('TASK-deep')!r}")
        check("the bound story keeps its own binding",
              eff.get("STORY-x") == "svc-a", f"got {eff.get('STORY-x')!r}")
        check("an epic above the binding does NOT inherit upward from its child",
              "EPIC-x" not in eff,
              "inheritance runs down the work chain only — work rolls UP over "
              "`contains` in the WORLD tree (ADR 0028), never down into the backlog")
        for nid in ("EPIC-orphan", "STORY-orphan", "TASK-orphan"):
            check(f"{nid} with no bound ancestor is absent, not present-with-None",
                  nid not in eff, f"got {eff.get(nid)!r}")
        check("an OBJECT never appears in the map",
              "svc-a" not in eff and "svc-b" not in eff)

    print("\n-- A2. a `contains` cycle terminates instead of hanging --")
    with Session(eng(db), future=True) as s:
        for nid in ("TASK-c1", "TASK-c2"):
            mk_node(s, nid, "task")
        s.flush()
        mk_edge(s, "TASK-c1", "TASK-c2", CONTAINS)
        mk_edge(s, "TASK-c2", "TASK-c1", CONTAINS)
        s.flush()
        # If the cycle guard is wrong this recurses forever and the suite hangs,
        # which is exactly the failure mode worth having a test for: every
        # /api/graph call goes through work_load, so a hang here is the board.
        eff = attention.effective_bindings(s)
        check("a cycle resolves to 'no binding' rather than recursing forever",
              "TASK-c1" not in eff and "TASK-c2" not in eff)


def section_b(db) -> None:
    print("\n-- B. wiring is a NO-OP while every workitem is explicitly bound --")
    db.reset_schema()
    models_graph.Base.metadata.create_all(eng(db))
    with Session(eng(db), future=True) as s:
        ensure_all_node_types(s)
        for nid in ("svc-a", "svc-b"):
            mk_node(s, nid, "service")
        items = [("EPIC-b", "epic", "svc-a"), ("STORY-b", "story", "svc-a"),
                 ("TASK-b1", "task", "svc-a"), ("TASK-b2", "task", "svc-b"),
                 ("TASK-b3", "task", "svc-b")]
        for nid, t, _ in items:
            mk_node(s, nid, t)
        s.flush()
        mk_edge(s, "EPIC-b", "STORY-b", CONTAINS)
        for nid, _, target in items:
            mk_edge(s, nid, target, "works_on")
        s.commit()   # B2 opens its own session and builds on this data

        raw: dict[str, list[str]] = defaultdict(list)
        for e in s.scalars(select(Edge).where(Edge.edge_type == "works_on")).all():
            raw[e.dst].append(e.src)
        loads = attention.work_load(s)

        check("every workitem is explicitly bound (the precondition this proves under)",
              len(attention.effective_bindings(s)) == len(items))
        check("work_load covers exactly the objects the raw edges name",
              set(loads) == set(raw), f"work_load={sorted(loads)} raw={sorted(raw)}")
        same = all(sorted(loads[o]["workitem_ids"]) == sorted(raw[o]) for o in raw)
        check("per-object workitem sets are IDENTICAL to the raw-edge computation",
              same, str({o: (sorted(loads[o]['workitem_ids']), sorted(raw[o])) for o in raw}))

    print("\n-- B2. ...and it ADDS coverage as soon as something is unbound --")
    with Session(eng(db), future=True) as s:
        mk_node(s, "TASK-b4", "task")
        s.flush()
        mk_edge(s, "STORY-b", "TASK-b4", CONTAINS)   # unbound child of a bound story
        s.commit()
        loads = attention.work_load(s)
        check("an unbound task now counts against its story's object",
              "TASK-b4" in loads["svc-a"]["workitem_ids"],
              f"svc-a holds {sorted(loads['svc-a']['workitem_ids'])}")
        check("...and against nothing else",
              all("TASK-b4" not in v["workitem_ids"] for k, v in loads.items() if k != "svc-a"))


def section_c(db) -> None:
    print("\n-- C. the required-world-link constraint is gone (step 5) --")
    check("models_graph exposes no validate_required_world_link",
          not hasattr(models_graph, "validate_required_world_link"))

    from node_types import NODE_TYPE_SEEDS
    check("no backlog seed sets requires_edge",
          all(not seed.get("requires_edge") for seed in NODE_TYPE_SEEDS.values()),
          str({k: v.get("requires_edge") for k, v in NODE_TYPE_SEEDS.items()}))
    check("the requires_edge COLUMN still exists (dropping it was out of scope)",
          hasattr(NodeType, "requires_edge"))

    db.reset_schema()
    models_graph.Base.metadata.create_all(eng(db))
    with Session(eng(db), future=True) as s:
        ensure_all_node_types(s)
        mk_node(s, "svc-c", "service")   # an object family node exists — the old gate
        s.flush()
        seeded = s.get(NodeType, "task")
        check("the live task node_type row carries no requires_edge after seeding",
              not seeded.requires_edge, f"got {seeded.requires_edge!r}")
        mk_node(s, "TASK-unlinked", "task")
        s.flush()                        # would have printed an orphan line before
        check("a brand-new workitem with NO world link persists",
              s.get(Node, "TASK-unlinked") is not None)


def report() -> None:
    """Step 4 of the task: how many unbound workitems inheritance would rescue.

    Runs against the REAL database if ATLAS_DB_URL points at one, because the
    number is only interesting for live data. Skipped otherwise — a disposable
    Postgres has nothing to count.
    """
    live = os.environ.get("ATLAS_DB_URL")
    print("\n-- step 4 report --")
    if not live:
        print("  ATLAS_DB_URL not set — skipping the live count (set it to measure).")
        return
    with Session(create_engine(live, future=True), future=True) as s:
        fam = {nt.id: nt.family for nt in s.scalars(select(NodeType)).all()}
        items = [n.id for n in s.scalars(select(Node)).all() if fam.get(n.type) == "workitem"]
        own = {e.src for e in s.scalars(select(Edge).where(Edge.edge_type == "works_on")).all()}
        eff = attention.effective_bindings(s)
        unbound = [i for i in items if i not in own]
        rescued = [i for i in unbound if i in eff]
        print(f"  workitems {len(items)} · own binding {len(own)} · "
              f"no own binding {len(unbound)} · of those, inherited {len(rescued)} "
              f"· still nothing {len(unbound) - len(rescued)}")


def main() -> None:
    db = testdb.start()
    try:
        section_a(db)
        section_b(db)
        section_c(db)
    finally:
        db.stop()
    report()

    print("\n" + "=" * 78)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
