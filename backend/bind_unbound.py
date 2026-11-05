"""Give every unbound workitem a world binding (ADR 0030 follow-up).

    python bind_unbound.py                # DRY RUN — prints the plan, writes nothing
    python bind_unbound.py --apply        # execute, in ONE transaction

Reads `world_v2_bind.tsv` (workitem -> node) and creates the missing `works_on`
edges. The owner's instruction, 2026-08-12: *"bind as you please... I will once
working on it ask to move it."*

REFUSES TO OVERWRITE. A workitem that already has a `works_on` or `serves` edge
is skipped and reported, never re-pointed — this script only fills holes. Moving
an existing binding is `migrate_world_v2.py`'s job (it verifies the old target
first) or a board edit. That distinction is the whole safety property here: run
this twice and the second run does nothing.

Also refuses a target that is not an object/bridge node, because `works_on` is
declared `("workitem", "object")` in `app.py`'s EDGE_FAMILY_RULES and an edge
that violates it would be rejected by the write API but NOT by a direct insert.
Checking it here keeps the two paths honest.

One-time, not wired into startup — same discipline as the importers. Delete it
with `world_v2_bind.tsv` once the bindings are in.
"""
from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import models_graph  # noqa: E402
from models_graph import Edge as GraphEdge, Node as GraphNode  # noqa: E402
import dsn  # noqa: E402
import settings  # noqa: E402

MAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "world_v2_bind.tsv")


def load_map() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    with open(MAP, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p for p in line.split("\t") if p]
            if len(parts) != 2:
                raise SystemExit(f"{MAP}:{lineno}: want 'workitem<TAB>node', got {line!r}")
            item, node = parts
            if item in seen:
                raise SystemExit(f"{MAP}:{lineno}: {item} appears twice")
            seen.add(item)
            rows.append((item, node))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="commit; without it the run rolls back")
    ap.add_argument("--db", default=None,
                    help="falls back to ATLAS_DB_URL, or the file ATLAS_DB_URL_FILE names")
    args = ap.parse_args()
    if not args.db:
        # Through the one secret reader (TASK-1349): ATLAS_DB_URL_FILE works, a blank
        # value fails as "set but empty", ATLAS_REQUIRE_SECRET_FILES=1 refuses the env var.
        try:
            args.db = settings.read_secret("ATLAS_DB_URL")
        except settings.SecretError as exc:
            print(f"no database: pass --db, or {exc}", file=sys.stderr)
            return 2

    try:
        args.db = dsn.with_default_driver(args.db)  # TASK-1501: no driver named -> psycopg2
    except dsn.DsnError as exc:
        print(f"no database: {exc}", file=sys.stderr)
        return 2

    rows = load_map()
    engine = create_engine(args.db, future=True)
    made: list[str] = []
    skipped: list[str] = []
    problems: list[str] = []

    print("=" * 78)
    print(f"ADR 0030 binding pass — {'APPLY' if args.apply else 'DRY RUN (rolls back)'}")
    print(f"DB:  {args.db.split('@')[-1]}   map: {len(rows)} rows")
    print("=" * 78)

    with Session(engine, future=True) as s:
        fam = {nt.id: nt.family for nt in s.scalars(select(models_graph.NodeType)).all()}

        def unbound_count() -> int:
            bound = set(s.scalars(select(GraphEdge.src).where(
                GraphEdge.edge_type.in_(("works_on", "serves")))).all())
            return sum(1 for n in s.scalars(select(GraphNode)).all()
                       if fam.get(n.type) == "workitem" and n.id not in bound)

        before = unbound_count()

        for item, node in rows:
            src = s.get(GraphNode, item)
            dst = s.get(GraphNode, node)
            if src is None:
                problems.append(f"{item}: workitem does not exist")
                continue
            if dst is None:
                problems.append(f"{item}: target {node!r} does not exist")
                continue
            if fam.get(src.type) != "workitem":
                problems.append(f"{item}: not a workitem (type {src.type!r})")
                continue
            if fam.get(dst.type) not in ("object", "bridge"):
                problems.append(f"{item}: target {node!r} is family {fam.get(dst.type)!r}, "
                                f"and works_on requires ('workitem','object')")
                continue
            existing = s.scalars(select(GraphEdge).where(
                GraphEdge.src == item,
                GraphEdge.edge_type.in_(("works_on", "serves")))).first()
            if existing is not None:
                skipped.append(f"{item}: already bound to {existing.dst} "
                               f"({existing.edge_type}) — left alone")
                continue
            s.add(GraphEdge(src=item, dst=node, edge_type="works_on",
                            directed=True, origin="atlas"))
            made.append(f"{item} --works_on--> {node}")
        s.flush()

        for line in made:
            print(f"  [bind] {line}")
        for line in skipped:
            print(f"  [skip] {line}")
        if problems:
            print("\n-- PROBLEMS --")
            for line in problems:
                print(f"  !! {line}")

        after = unbound_count()
        loads: dict[str, int] = {}
        for e in s.scalars(select(GraphEdge).where(GraphEdge.edge_type == "works_on")).all():
            loads[e.dst] = loads.get(e.dst, 0) + 1
        top = sorted(loads.items(), key=lambda kv: -kv[1])[:8]

        print(f"\n  bound {len(made)}   skipped {len(skipped)}   problems {len(problems)}")
        print(f"  unbound workitems: {before} -> {after}")
        print(f"  nodes carrying work: {len(loads)}   heaviest: "
              f"{', '.join(f'{k}={v}' for k, v in top)}")

        if args.apply:
            s.commit()
            print("\nCOMMITTED.")
        else:
            s.rollback()
            print("\nrolled back — nothing was written. Re-run with --apply to execute.")

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
