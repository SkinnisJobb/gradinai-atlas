"""
TASK-1056 — drop the `status_override` table (STORY-atlas-retire-status-roundtrip).

The `StatusOverride` model, `base_status`, `reconcile_status_overrides` and
`/api/status/pending` are already deleted from the code — see models_graph.py's
history for the removed model and AGENTS.md's "Status" section for the story.
Removing the model means `Base.metadata.create_all()` stops CREATING this
table on a fresh database, but it does nothing to an EXISTING, already-
populated Postgres: the table just sits there, unreferenced by any code path,
until something actually issues the `DROP TABLE`. This script is that
something.

WHY THIS IS ITS OWN SCRIPT, NOT FOLDED INTO `_reconcile_graph_schema`.
`_reconcile_graph_schema` (app.py) is additive ONLY, by convention — it emits
`ALTER TABLE ... ADD COLUMN` and `CREATE INDEX IF NOT EXISTS`, and nothing
else. Dropping a table is destructive DDL against a database that (on live)
has never had a backup (AGENTS.md: "Backup is out of scope"), so the task
that names this drop is explicit that it is "deliberate, separate, and taken
with the owner" — not something that fires silently the next time `atlas-api`
boots. Same idiom as `migrate_result_split.py`: a standalone, one-time
migration, DRY-RUN BY DEFAULT, `--apply` required to write anything.

THE GUARD THAT IS THE WHOLE POINT. `status_override` was measured at 0 rows
on both live and staging on 2026-08-10, which is the precondition this whole
task rests on — but a precondition measured once is not a guarantee true
forever, and this script may be re-run later (against a different database,
or after time has passed). So it REFUSES to drop a non-empty table unless
`--force` is passed, and says exactly how many rows it found: a later re-run
against a database where somebody's clicks somehow landed must not silently
destroy them. (How rows could land there is unclear — the write path that
created them is deleted — but a precondition that used to hold is not the
same claim as "cannot ever hold again", and this script does not gamble on
it having stayed 0 forever, in every environment it might ever be pointed at.)

FAILS LOUDLY, NOT WITH A TRACEBACK, on a database with no Atlas schema at
all (an unmigrated/bare Postgres) — same check `migrate_result_split.py` and
`verify_arrival.py` use: a plain `SELECT` against `node` first, turned into a
clear fatal message if it raises, rather than an AttributeError three frames
deep into the drop logic.

ONLY RUN AGAINST A LOCAL DISPOSABLE POSTGRES from an agent. Staging and live
are the owner's to run this against themselves.

Usage:
    python migrate_drop_status_override.py                  # dry run (default; drops nothing)
    python migrate_drop_status_override.py --apply           # drops the table if it is empty
    python migrate_drop_status_override.py --apply --force   # drops it even if it is NOT empty
    python migrate_drop_status_override.py --db-url postgresql+psycopg2://... --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import dsn  # noqa: E402
import settings  # noqa: E402

TABLE_NAME = "status_override"


def _table_exists(engine) -> bool:
    return TABLE_NAME in inspect(engine).get_table_names()


def _row_count(s: Session) -> int:
    return s.execute(text(f"SELECT COUNT(*) FROM {TABLE_NAME}")).scalar_one()


def plan(db_url: str, apply: bool, force: bool) -> dict:
    """Inspect the database and, if `apply`, drop `status_override` when it is
    safe to. Returns a report dict (see `main()` for the printed shape).

    Fails loudly — a clear message, not a traceback — if the `node` table is
    not there at all (same check `migrate_result_split.py` /
    `verify_arrival.py` use): an unmigrated or bare Postgres is a caller
    error, not something to proceed against.
    """
    engine = create_engine(db_url, pool_pre_ping=True)
    s = Session(engine)
    try:
        s.execute(text("SELECT id FROM node LIMIT 1"))
    except Exception as e:  # noqa: BLE001 — turn "no schema here" into a clear fatal, not a trace
        print(f"FATAL: could not query the 'node' table at {db_url}: {e}\n"
              f"This DB has no Atlas schema (an unmigrated/empty Postgres) — point --db-url at "
              f"the real deployment's database, not a bare one.", file=sys.stderr)
        s.close()
        engine.dispose()
        sys.exit(2)

    try:
        if not _table_exists(engine):
            return {
                "mode": "APPLY" if apply else "DRY RUN (pass --apply to write)",
                "table_existed": False,
                "row_count": None,
                "dropped": False,
                "detail": f"'{TABLE_NAME}' does not exist — nothing to do "
                          f"(already dropped, or a fresh database that never created it).",
            }

        count = _row_count(s)
        report = {
            "mode": "APPLY" if apply else "DRY RUN (pass --apply to write)",
            "table_existed": True,
            "row_count": count,
            "dropped": False,
            "detail": None,
        }

        if not apply:
            report["detail"] = (
                f"dry run: '{TABLE_NAME}' exists with {count} row(s). "
                f"Re-run with --apply to drop it"
                + ("" if count == 0 else " (and --force, since it is not empty)") + "."
            )
            return report

        if count > 0 and not force:
            report["detail"] = (
                f"REFUSED: '{TABLE_NAME}' has {count} row(s) — these look like unlanded board "
                f"clicks (or something else nobody expected to still be writing this table). "
                f"Not dropping without --force."
            )
            return report

        s.execute(text(f"DROP TABLE {TABLE_NAME}"))
        s.commit()
        report["dropped"] = True
        report["detail"] = (
            f"dropped '{TABLE_NAME}' ({count} row(s) discarded)." if count
            else f"dropped '{TABLE_NAME}' (it was empty)."
        )
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
                     help="write changes (drop the table if safe). Without this flag: dry-run only.")
    ap.add_argument("--force", action="store_true",
                     help="drop the table even if it is NOT empty. Without --apply, this does nothing.")
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
    print("TASK-1056 — drop the status_override table")
    print(f"DB:   {_redact(args.db_url)}")
    print(f"mode: {'APPLY' if args.apply else 'DRY RUN (pass --apply to write)'}"
          + (" + FORCE" if args.force else ""))
    print("=" * 78)

    report = plan(args.db_url, args.apply, args.force)
    print(json.dumps(report, indent=2, default=str))

    if args.apply and report["table_existed"] and not report["dropped"]:
        # Apply was requested but the guard refused (non-empty, no --force).
        # Exit non-zero so a caller scripting this notices, same discipline
        # as migrate_result_split.py's own FATAL exits.
        sys.exit(1)


if __name__ == "__main__":
    main()
