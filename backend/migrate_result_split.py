"""
TASK-1065 — split the 306 bodies already in Postgres.

`import_workitems.py` (TASK-1052) ran BEFORE `workitem_result` existed
(TASK-1063), so every one of the 306 workitem rows it wrote still carries its
`## Result`/`## Outcome`/`## Results` section inline, inside `node.body`,
instead of in its own row. `import_workitems.py` is a SEED, not a reconciler,
for `atlas`-origin rows (see its own module docstring): its upsert loop only
ever writes a field that differs from what the FILE says, and the file on
disk was never rewritten to drop the section — so simply re-running it
reproduces the exact same (still-unsplit) `body` it wrote the first time. It
cannot fix these 306 rows, by design. This script is the one-time fix,
applied directly to what is already in Postgres.

Same spirit as `import_workitems.py` / `import_world.py`: a standalone,
one-time migration, deliberately NOT wired into `app.py`'s startup path, and
expected to be deleted once it has run once against the database that
matters (live).

WHAT IT DOES, PER WORKITEM-TYPE NODE (epic/story/task — `WORKITEM_TYPES`
below, same vocabulary as `import_workitems.BACKLOG_TYPES` / `app.
WORKITEM_TYPES`). `models_graph.split_result(node.body)` is the SAME function
`import_workitems.py` already uses for exactly this split — imported, not
re-derived:

  * returns a block -> `node.body` is rewritten to the body WITHOUT the
    section, and the (position, heading, text) block is upserted into
    `workitem_result`.
  * returns None -> the node is left COMPLETELY alone. Not even a no-op
    UPDATE is issued.

IDEMPOTENT — BY THE SHAPE OF THE DATA, NOT A MARKER COLUMN. A node this
script already split has had its result heading physically removed from
`node.body`. The very same call that decided to split it the first time
(`split_result(node.body)`) now returns None on a second run, for exactly
the reason a file with no `## Result` heading always returns None: there is
nothing left in the text to find. So "already split" is not tracked by a
flag anywhere — it is read directly off whether a `workitem_result` row
already exists for a node whose body currently has no result heading (see
`classify()` below). A node that never had a result section at all — no
row, no heading, ever — lands in a THIRD bucket, `no_result`, and is also
never touched. Proved, not assumed, by `verify_result_migration.py`, which
runs this script twice against the same populated database and asserts the
second run reports 0 to split and changes nothing.

HISTORICAL NOTE (TASK-1058, STORY-atlas-retire-status-roundtrip): this section
used to explain why rewriting `node.body` here could never produce a new
`protected_diverged` report at the next boot ingest — `ingest_graph.
_reconcile_nodes`'s PROTECT branch never included `body` in the fields it
diffed `node.git_snapshot` against. Both the boot-time reconciler and the
`git_snapshot` column are deleted; there is no PROTECT branch left to worry
about, and no snapshot column for this migration to leave stale. Rewriting
`node.body` here is now just an ordinary UPDATE like any other.

DRY-RUN BY DEFAULT. `--apply` is required to write anything. Backup is out
of scope by standing decision (AGENTS.md, "Backup is out of scope") — there
is no undo, so on a database with no copy anywhere the only safe default is
to look, not touch, until told twice.

ATOMICITY. Every workitem-type node is read, classified, and (if `--apply`)
written within ONE session, committed exactly once at the very end. Any
exception during the loop propagates before that commit is reached and the
session is rolled back explicitly in the `except` below, so a failure
part-way through can never leave the table half-migrated.

Usage:
    python migrate_result_split.py                 # dry run (default; writes nothing)
    python migrate_result_split.py --apply          # writes
    python migrate_result_split.py --db-url postgresql+psycopg2://... --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from models_graph import Node, WorkitemResult, split_result  # noqa: E402
import dsn  # noqa: E402
import settings  # noqa: E402

# Same vocabulary as import_workitems.BACKLOG_TYPES / app.WORKITEM_TYPES —
# restated here (not imported) so this standalone script has no dependency on
# either of those modules; it only ever needs the schema (models_graph).
WORKITEM_TYPES = ("epic", "story", "task")


def classify(node: Node, has_existing_row: bool):
    """(action, split_or_None) for one node.

    `action` is one of:
      "split"          — `node.body` still carries a result heading. This is
                          the state TASK-1065 exists to fix.
      "already_split"   — no heading left in the body, AND a `workitem_result`
                          row already accounts for it (a previous run of this
                          script, or — on live — none yet, since this is the
                          first run). Nothing to do.
      "no_result"       — no heading, and never had a `workitem_result` row.
                          One of the 85 files that never carried a result
                          section. Nothing to do, ever.
    """
    split = split_result(node.body) if node.body else None
    if split is not None:
        return "split", split
    if has_existing_row:
        return "already_split", None
    return "no_result", None


def migrate(s: Session, apply: bool) -> dict:
    """Classify every workitem-type node and, if `apply`, write the split.

    Read-then-write, but the write only ever happens for nodes classified
    "split" — "already_split" and "no_result" nodes are never touched, dry
    run or not. Returns a report dict (see `main()` for the printed shape).
    """
    nodes = s.scalars(select(Node).where(Node.type.in_(WORKITEM_TYPES))).all()
    existing_result_node_ids = set(s.scalars(select(WorkitemResult.node_id)).all())

    counts = {"split": 0, "already_split": 0, "no_result": 0}
    ids = {"split": [], "already_split": [], "no_result": []}

    for node in nodes:
        has_row = node.id in existing_result_node_ids
        action, split = classify(node, has_row)
        counts[action] += 1
        ids[action].append(node.id)

        if action != "split":
            continue  # "already_split" / "no_result": leave completely alone

        body_wo, heading, text, position = split
        if not apply:
            continue  # dry run: classified, but nothing written

        node.body = body_wo
        # Upsert, delete-then-insert — same idiom import_workitems.py's own
        # workitem_result loop uses. Defensive, not load-bearing: this
        # script's own idempotency rule (classify() above) means a node
        # classified "split" should never already carry a row — if `body`
        # still has a heading, nothing has split it yet — but a stray row
        # from outside this script's control (a bug, a manual edit) must not
        # produce a duplicate rather than a clean replace.
        for row in s.scalars(select(WorkitemResult).where(WorkitemResult.node_id == node.id)).all():
            s.delete(row)
        s.add(WorkitemResult(node_id=node.id, position=position, heading=heading, text=text))

    return {
        "mode": "APPLY (written)" if apply else "DRY RUN (nothing written — pass --apply to write)",
        "total_workitem_nodes": len(nodes),
        "split": counts["split"],
        "already_split": counts["already_split"],
        "no_result_section": counts["no_result"],
        "split_ids_sample": sorted(ids["split"])[:10],
        "already_split_ids_sample": sorted(ids["already_split"])[:10],
    }


def run(db_url: str, apply: bool) -> dict:
    """Open one session against `db_url`, classify + (maybe) migrate every
    workitem-type node, commit once iff `apply`, and hand back the report.

    Fails loudly — a clear message, not a traceback — if the `node` table
    is not there at all (same check `verify_arrival.py` uses): an unmigrated
    or bare Postgres is a caller error, not something to proceed against.
    """
    engine = create_engine(db_url, pool_pre_ping=True)
    s = Session(engine)
    try:
        s.execute(select(Node.id).limit(1))
    except Exception as e:  # noqa: BLE001 — turn "no schema here" into a clear fatal, not a trace
        print(f"FATAL: could not query the 'node' table at {db_url}: {e}\n"
              f"This DB has no Atlas schema (an unmigrated/empty Postgres) — point --db-url at "
              f"the real deployment's database, not a bare one.", file=sys.stderr)
        s.close()
        engine.dispose()
        sys.exit(2)

    try:
        report = migrate(s, apply)
        if apply:
            s.commit()
        else:
            s.rollback()  # belt-and-braces: a dry run must never leave anything pending
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
        engine.dispose()
    return report


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
                     help="write changes. Without this flag: dry-run only (the default).")
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
    print("TASK-1065 — split the 306 bodies already in Postgres")
    print(f"DB:   {_redact(args.db_url)}")
    print(f"mode: {'APPLY' if args.apply else 'DRY RUN (pass --apply to write)'}")
    print("=" * 78)

    report = run(args.db_url, args.apply)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
