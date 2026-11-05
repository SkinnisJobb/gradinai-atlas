"""
ADR 0029 — mark the 5 `attributes.docs[]` entries that do not resolve under
`docs/nodes/` as `"external": true`, on the LIVE `node` rows.

>>> SPENT AS OF 2026-08-13. Every one of the five `TARGETS` below is now
>>> unreachable and this script is a no-op against live: the three `doc-*`
>>> nodes were DELETED when the `doc` type was retired (their pointers moved
>>> onto `atlas-docs` / `atlas` / the organisation node), and the two surviving entries
>>> were repointed off the `AgentPlanningSetup/` prefix that ADR 0031 split
>>> apart. It is kept as the record of a migration that already ran.
>>>
>>> It is SAFE to run — it matches on `(node_id, path)` and only ever SETS
>>> `external: true`, so it can neither resurrect a deleted node nor write a
>>> stale path back. A re-run reports five misses and changes nothing. Do not
>>> "fix" the paths below to make it work again; there is nothing left to mark.

THESE ARE TWO STORES NOW. `backend/world_manifest.yaml` is the historical
record — nothing reads it at runtime any more (TASK-1058 deleted the world
ingest), so editing the yaml alone (already done, same commit as this script)
changes NOTHING anybody sees. The 42 world nodes the board actually reads
live in Postgres, imported once by `import_world.py` and never re-synced from
the yaml since. This script is the other half: it PATCHes the five live
`node.attributes` rows directly, the same content the yaml now carries.

THE FIVE, AND WHY EACH ONE (ADR 0029 "The 5 entries that do not resolve"):
  - skill-homelab-ops     -> AgentPlanningSetup/.claude/agents/homelab-ops.md
  - goal-live-object-graph -> AgentPlanningSetup/docs/adr/0019-...md
  - doc-node-doc-standard -> AgentPlanningSetup/docs/adr/0020-...md
  - doc-company-bridge    -> C:/Projects/bridge/company-atlas-bridge.md
  - doc-atlas-readme      -> README.md (the one genuine anomaly — an in-repo
    file the `docs/nodes/` convention excludes; ADR 0029 resolves it by
    marking external rather than moving it)

SAME IDIOM AS `migrate_drop_status_override.py` / `migrate_result_split.py`:
standalone, DRY-RUN BY DEFAULT, `--apply` required to write anything, fails
loudly (not with a traceback) against a database with no Atlas schema at all.
IDEMPOTENT: an entry already carrying `external: true` is reported unchanged,
never double-set or duplicated — a second run against an already-patched
database reports 0 changes.

WHY A DIRECT ROW WRITE, NOT `PATCH /api/node` OVER HTTP. This is a one-time,
five-row data fix of the same shape `migrate_drop_status_override.py` already
sets the precedent for (a script the owner runs directly against a database,
not a client of the running app), and it still goes through the same
`before_flush` validator (`models_graph.validate_node`) as every other write
to this column — reassigning `n.attributes` on a real ORM `Session` fires it
exactly the way the write API's session does, so an entry that somehow failed
`_DOCS_ITEM_SCHEMA` would still be caught here, not silently written.

ONLY RUN AGAINST A LOCAL DISPOSABLE POSTGRES FROM AN AGENT. Staging and live
are the owner's to run this against themselves — see the task's own instruction
("I will run that against live myself. Do not run it.").

Usage:
    python migrate_mark_docs_external.py                 # dry run (default; writes nothing)
    python migrate_mark_docs_external.py --apply          # writes the 5 rows
    python migrate_mark_docs_external.py --db-url postgresql+psycopg2://... --apply
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from models_graph import Node  # noqa: E402
import dsn  # noqa: E402
import settings  # noqa: E402

# node_id -> the docs[].path this entry must carry `external: true` on.
# One row per target — never pattern-matched against "any external-looking
# path", so a future genuinely-external entry someone forgets to add here
# is left alone rather than silently swept up.
TARGETS: dict[str, str] = {
    "skill-homelab-ops": "AgentPlanningSetup/.claude/agents/homelab-ops.md",
    "goal-live-object-graph": "AgentPlanningSetup/docs/adr/0019-atlas-typed-object-graph-world-model.md",
    "doc-node-doc-standard": "AgentPlanningSetup/docs/adr/0020-atlas-node-documentation-standard.md",
    "doc-company-bridge": "C:/Projects/bridge/company-atlas-bridge.md",
    "doc-atlas-readme": "README.md",
}


def plan(db_url: str, apply: bool) -> dict:
    engine = create_engine(db_url, pool_pre_ping=True)
    s = Session(engine)
    try:
        s.execute(select(Node.id).limit(1))
    except Exception as e:  # noqa: BLE001 — clear fatal, not a traceback
        print(f"FATAL: could not query the 'node' table at {db_url}: {e}\n"
              f"This DB has no Atlas schema (an unmigrated/empty Postgres) — point --db-url at "
              f"the real deployment's database, not a bare one.", file=sys.stderr)
        s.close()
        engine.dispose()
        sys.exit(2)

    report = {
        "mode": "APPLY" if apply else "DRY RUN (pass --apply to write)",
        "rows": [],
    }

    try:
        for node_id, path in TARGETS.items():
            node = s.get(Node, node_id)
            row = {"node_id": node_id, "path": path}
            if node is None:
                row["status"] = "NODE NOT FOUND — nothing to patch"
                report["rows"].append(row)
                continue

            attrs = node.attributes or {}
            docs = attrs.get("docs") or []
            entry = next((d for d in docs if isinstance(d, dict) and d.get("path") == path), None)
            if entry is None:
                row["status"] = "docs[] ENTRY NOT FOUND — nothing to patch"
                report["rows"].append(row)
                continue
            if entry.get("external") is True:
                row["status"] = "already external — no change"
                report["rows"].append(row)
                continue

            row["status"] = "would mark external" if not apply else "marked external"
            report["rows"].append(row)
            if apply:
                # Reassign the WHOLE attributes dict — mutating the nested
                # list in place would not mark the JSONB column dirty (no
                # Mutable wrapper on this column; see models_graph.Node).
                new_attrs = copy.deepcopy(attrs)
                for d in new_attrs.get("docs") or []:
                    if isinstance(d, dict) and d.get("path") == path:
                        d["external"] = True
                node.attributes = new_attrs

        if apply:
            s.commit()
        changed = sum(1 for r in report["rows"] if r["status"] == "marked external")
        report["changed"] = changed
        return report
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
        engine.dispose()


def _redact(db_url: str) -> str:
    """Strip the password out of a SQLAlchemy URL before printing it.

    These scripts print a banner to stdout, and stdout lands in session
    transcripts, CI logs and pasted terminal output. `ATLAS_DB_URL` carries the
    Postgres password inline, so an unredacted banner publishes a live
    credential on every run. This project has already had to rotate two secrets
    that reached a transcript that way (a Cloudflare tunnel token, and a service
    token on 2026-08-15), so this is a demonstrated failure mode rather than a
    theoretical one.

    Copied rather than shared (TASK-1102): these migrations are deliberately
    standalone single-file scripts with no local imports, and a shared helper
    would be the one dependency that breaks running one from a checkout. The
    duplication is the cheaper mistake. If you add a migrate_ script, copy this
    too.
    """
    try:
        from sqlalchemy.engine import make_url
        return make_url(db_url).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable db url; not printed>"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db-url", default=None,
                     help="falls back to ATLAS_DB_URL, or the file ATLAS_DB_URL_FILE names")
    ap.add_argument("--apply", action="store_true",
                     help="write changes. Without this flag: dry-run only.")
    args = ap.parse_args()

    if not args.db_url:
        # Through the one secret reader (TASK-1349): ATLAS_DB_URL_FILE works, a blank
        # value fails as "set but empty", ATLAS_REQUIRE_SECRET_FILES=1 refuses the env var.
        try:
            args.db_url = settings.read_secret("ATLAS_DB_URL")
        except settings.SecretError as exc:
            print(f"FATAL: no --db-url given, and {exc} Refusing to run "
                  "(no default DSN, by design).", file=sys.stderr)
            sys.exit(2)

    try:
        args.db_url = dsn.with_default_driver(args.db_url)  # TASK-1501: no driver -> psycopg2
    except dsn.DsnError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(2)

    print("=" * 78)
    print("ADR 0029 — mark the 5 external docs[] entries on live node rows")
    print(f"DB:   {_redact(args.db_url)}")
    print(f"mode: {'APPLY' if args.apply else 'DRY RUN (pass --apply to write)'}")
    print("=" * 78)

    report = plan(args.db_url, args.apply)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
