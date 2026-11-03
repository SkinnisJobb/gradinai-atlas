"""
Strip the retired markdown-era leftovers off live node rows (2026-09-15).

Two things the deleted markdown backlog left behind, on rows that are otherwise
Postgres-authoritative:

  1. `attributes.frontmatter_raw` — the verbatim YAML header of the file a
     workitem was imported from. RETIRED: nothing reads or writes it, and it
     disagrees with the row it sits on (it still says `status: pending` on items
     that are cancelled). It was a standing instruction in CLAUDE.md — "never
     read or write it, but live rows still carry it" — and it rides along in
     every `GET /api/workitems` listing, which is what an agent pays for.
  2. `node.source` values starting `backlog/` — the path of a file that no
     longer exists anywhere but git history.

WHAT IT DOES NOT TOUCH. Any other attribute; `source` values that are not a
`backlog/` path (they are reported, not changed); `node.origin`; any table other
than `node`. It records no `graph_event`: this is a one-time cleanup of dead
provenance, not an edit anyone made to the graph, and a `graph_event` per row
would bury the real history under hundreds of machine rows.

It is idempotent — a second `--apply` finds nothing — and DRY-RUN BY DEFAULT,
the idiom of every `migrate_*.py` here. The report counts before and after, so
the effect is measured rather than assumed.

Usage:
    python migrate_strip_frontmatter_raw.py                 # dry run
    python migrate_strip_frontmatter_raw.py --apply
    python migrate_strip_frontmatter_raw.py --db-url postgresql+psycopg2://... --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings  # noqa: E402

COUNT_SQL = {
    "rows_with_frontmatter_raw": "SELECT COUNT(*) FROM node WHERE attributes ? 'frontmatter_raw'",
    "rows_with_backlog_source": "SELECT COUNT(*) FROM node WHERE source LIKE 'backlog/%'",
    "rows_with_other_source (not touched)":
        "SELECT COUNT(*) FROM node WHERE source IS NOT NULL AND source NOT LIKE 'backlog/%'",
}


def _counts(s: Session) -> dict:
    return {k: s.execute(text(q)).scalar_one() for k, q in COUNT_SQL.items()}


def plan(db_url: str, apply: bool) -> dict:
    engine = create_engine(db_url, pool_pre_ping=True)
    s = Session(engine)
    try:
        try:
            s.execute(text("SELECT id FROM node LIMIT 1"))
        except Exception as e:  # noqa: BLE001 — a clear fatal, not a traceback
            print(f"FATAL: no Atlas 'node' table at {_redact(db_url)}: {e}", file=sys.stderr)
            sys.exit(2)
        before = _counts(s)
        report = {"mode": "APPLY" if apply else "DRY RUN (pass --apply to write)", "before": before}
        if not apply:
            return report
        stripped = s.execute(text(
            "UPDATE node SET attributes = attributes - 'frontmatter_raw' "
            "WHERE attributes ? 'frontmatter_raw'")).rowcount
        cleared = s.execute(text(
            "UPDATE node SET source = NULL WHERE source LIKE 'backlog/%'")).rowcount
        s.commit()
        report.update({"frontmatter_raw_stripped": stripped, "backlog_source_cleared": cleared,
                       "after": _counts(s)})
        return report
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
        engine.dispose()


def _redact(db_url: str) -> str:
    """Strip the password from the URL before printing — stdout lands in
    transcripts. Copied, not shared, like every migrate_*.py (see
    migrate_drop_status_override.py for why)."""
    try:
        from sqlalchemy.engine import make_url
        return make_url(db_url).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable db url; not printed>"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db-url", default=None,
                    help="falls back to ATLAS_DB_URL, or the file ATLAS_DB_URL_FILE names")
    ap.add_argument("--apply", action="store_true", help="write; without it, dry run only")
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
    print("=" * 78)
    print("strip attributes.frontmatter_raw and backlog/ source paths")
    print(f"DB:   {_redact(args.db_url)}")
    print(f"mode: {'APPLY' if args.apply else 'DRY RUN (pass --apply to write)'}")
    print("=" * 78)
    report = plan(args.db_url, args.apply)
    print(json.dumps(report, indent=2))
    if args.apply and (report["after"]["rows_with_frontmatter_raw"]
                       or report["after"]["rows_with_backlog_source"]):
        sys.exit(1)  # applied, yet something remains: say so with the exit code


if __name__ == "__main__":
    main()
