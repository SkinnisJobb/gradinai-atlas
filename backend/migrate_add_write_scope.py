"""
TASK-1086 — widen `ck_auth_service_token_scopes` to admit the `write` scope.

TASK-1085 decided the design and TASK-1086 code adds `"write"` to
`models_auth.SCOPES` — which widens the CHECK regex Python computes
(`_SCOPES_REGEX`) — but that does nothing to a database that was migrated
before this deploy. `Base.metadata.create_all()` never ALTERs an existing
constraint, and `_reconcile_graph_schema()` (app.py) only reconciles
`models_graph` tables; there is no equivalent for `models_auth`. So a live
or staging Postgres keeps enforcing the OLD regex
(`^(read|ingest|admin|private)(,(read|ingest|admin|private))*$`) until this
script runs: `admin.py token add --scopes write` would pass Python-side
validation (which reads `models_auth.SCOPES` dynamically and already accepts
`write`) and then fail at the INSERT with a CHECK violation. This script is
what makes the two agree.

WHAT THIS DOES: `ALTER TABLE auth_service_token DROP CONSTRAINT
ck_auth_service_token_scopes`, then `ADD CONSTRAINT` of the same name with
whatever `models_auth._SCOPES_REGEX` computes RIGHT NOW — so this script
needs no edit if `SCOPES` changes again later, only a re-run. Widening an
alternation only GROWS the accepted set, so no existing row can become
invalid; the "no row violates the new check" query below is a paranoid
belt, not a load-bearing guard.

*** DEPLOY ORDER — READ BEFORE RUNNING THIS ANYWHERE BUT A DISPOSABLE DB ***
`ATLAS_SYNC_TOKEN` is scoped `read` and writes CONSTANTLY — it filed all 31
workitems of EPIC-atlas-mcp on 2026-08-15. If write-scope ENFORCEMENT
(`auth._check_write_scope`, wired into `auth.guard_write` this same task)
ships in the SAME deploy that first makes `write` mintable, ticket filing
breaks for the width of that window — the token would need `write` before
anyone has re-minted it. The order is:

  1. Deploy this migration and run it with --apply. `write` merely becomes
     a MINTABLE scope; nothing changes about what is ENFORCED yet, because
     the enforcement code and this migration are independent deploys.
  2. Re-mint `ATLAS_SYNC_TOKEN` as `read,write` (`admin.py token add`, then
     update the server's `.env` and restart `atlas-api`), and mint the
     MCP's own token with whatever scopes TASK-1087+ decides it needs.
  3. Deploy the code that turns enforcement on (`auth.guard_write` calling
     `_check_write_scope` for every non-session principal). Only NOW does a
     caller presenting a `read`-only token get refused on a write route.

No feature flag guards enforcement. The three steps above are already
independently deployable — a migration, an `admin token add` run, and a
code deploy — so a flag would add a SECOND on/off switch for a sequencing
problem the three-step order already solves, and a flag nobody remembers to
flip is worse than an order nobody skips.

Same idiom as `migrate_drop_status_override.py` / `migrate_result_split.py`:
standalone, dry-run by default, `--apply` required, refuses surprising
states rather than guessing what to do with them.

ONLY RUN AGAINST A LOCAL DISPOSABLE POSTGRES from an agent. Staging and live
are the owner's to run this against themselves.

Usage:
    python migrate_add_write_scope.py                  # dry run (default; writes nothing)
    python migrate_add_write_scope.py --apply           # widens the constraint
    python migrate_add_write_scope.py --db-url postgresql+psycopg2://... --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import models_auth  # noqa: E402
import dsn  # noqa: E402
import settings  # noqa: E402

TABLE_NAME = "auth_service_token"
CONSTRAINT_NAME = "ck_auth_service_token_scopes"

# The regex this constraint carried BEFORE TASK-1085/1086 added `write` to
# `models_auth.SCOPES`. Frozen here on purpose, not derived from SCOPES —
# SCOPES has already moved on by the time this script runs, so this is the
# one place that remembers what "not yet migrated" looks like.
_OLD_SCOPES = ("read", "ingest", "admin", "private")
_OLD_REGEX = "^({alts})(,({alts}))*$".format(alts="|".join(_OLD_SCOPES))


def _constraint_def(engine, name: str) -> str | None:
    """The exact `CHECK (...)` text Postgres reports for a constraint, or
    None if it does not exist. Comparing this string (not re-deriving SQL
    text ourselves) is what lets the OLD/NEW/surprising branches below be a
    plain equality check rather than a regex-on-a-regex."""
    with engine.connect() as c:
        row = c.execute(
            text(
                # TABLE_NAME is this module's own constant, never caller
                # input — safe to interpolate into the regclass cast, and
                # simpler than fighting SQLAlchemy's bind-param tokenizer
                # over the `::` cast operator.
                f"SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                f"WHERE conname = :name AND conrelid = '{TABLE_NAME}'::regclass"
            ),
            {"name": name},
        ).first()
    return row[0] if row else None


def _check_sql(regex: str) -> str:
    """The exact `ALTER TABLE ... ADD CONSTRAINT` clause for a given regex,
    in the same shape SQLAlchemy emits from `CheckConstraint` in
    `models_auth.py` (verified against a real Postgres: `pg_get_constraintdef`
    reports it back as `CHECK (((scopes)::text ~ '<regex>'::text))`)."""
    return "CHECK (((scopes)::text ~ '%s'::text))" % regex


def plan(db_url: str, apply: bool) -> dict:
    """Inspect the database and, if `apply`, widen the CHECK constraint when
    it is safe to. Returns a report dict (see `main()` for the printed
    shape). Fails loudly on a database with no Atlas auth schema at all —
    same discipline as `migrate_drop_status_override.py`.
    """
    engine = create_engine(db_url, pool_pre_ping=True)
    s = Session(engine)
    try:
        s.execute(text("SELECT id FROM auth_user LIMIT 1"))
    except Exception as e:  # noqa: BLE001 — turn "no schema here" into a clear fatal, not a trace
        print(f"FATAL: could not query the 'auth_user' table at {db_url}: {e}\n"
              f"This DB has no Atlas auth schema (an unmigrated/empty Postgres) — point "
              f"--db-url at the real deployment's database, not a bare one.", file=sys.stderr)
        s.close()
        engine.dispose()
        sys.exit(2)

    try:
        if TABLE_NAME not in inspect(engine).get_table_names():
            print(f"FATAL: '{TABLE_NAME}' does not exist, but 'auth_user' does — a database "
                  f"with half the auth schema is a state this script refuses to guess about.",
                  file=sys.stderr)
            sys.exit(2)

        current = _constraint_def(engine, CONSTRAINT_NAME)
        new_regex = models_auth._SCOPES_REGEX  # dynamic — this script never goes stale on SCOPES
        old_def = _check_sql(_OLD_REGEX)
        new_def = _check_sql(new_regex)

        report = {
            "mode": "APPLY" if apply else "DRY RUN (pass --apply to write)",
            "current_constraint": current,
            "target_constraint": new_def,
            "already_widened": current == new_def,
            "altered": False,
            "detail": None,
        }

        if current is None:
            report["detail"] = (
                f"REFUSED: '{CONSTRAINT_NAME}' does not exist on '{TABLE_NAME}' at all — "
                f"surprising for a migrated database. Not guessing; look by hand."
            )
            return report

        if current == new_def:
            report["detail"] = f"'{CONSTRAINT_NAME}' already accepts 'write' — nothing to do."
            return report

        if current != old_def:
            report["detail"] = (
                f"REFUSED: '{CONSTRAINT_NAME}' matches neither the expected OLD definition "
                f"nor the target NEW one. Refusing to touch a constraint in an unexpected "
                f"shape — read current_constraint above and decide by hand.\n"
                f"  expected OLD: {old_def}\n"
                f"  got:          {current}"
            )
            return report

        # Paranoid belt, not load-bearing: widening an alternation only GROWS
        # the accepted set, so no row that satisfied the OLD check can fail
        # the NEW one. Checked anyway before touching DDL.
        violators = s.execute(
            text(f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE NOT (scopes ~ :regex)"),
            {"regex": new_regex},
        ).scalar_one()
        if violators:
            report["detail"] = (
                f"REFUSED: {violators} row(s) would violate even the WIDENED check — "
                f"that should be structurally impossible and means something upstream "
                f"of this script already put an invalid value in 'scopes'. Not touching DDL."
            )
            return report

        if not apply:
            report["detail"] = "dry run: constraint would be widened to admit 'write'. Re-run with --apply."
            return report

        s.execute(text(f"ALTER TABLE {TABLE_NAME} DROP CONSTRAINT {CONSTRAINT_NAME}"))
        s.execute(text(f"ALTER TABLE {TABLE_NAME} ADD CONSTRAINT {CONSTRAINT_NAME} "
                       f"CHECK (scopes ~ '{new_regex}')"))
        s.commit()
        report["altered"] = True
        report["detail"] = f"widened '{CONSTRAINT_NAME}' to admit 'write' ({violators} pre-existing rows, all still valid)."
        return report
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
        engine.dispose()


def _redact(db_url: str) -> str:
    """Strip the password out of a SQLAlchemy URL before printing it.

    These scripts print their banner to stdout, and stdout ends up in session
    transcripts, CI logs and pasted terminal output. `ATLAS_DB_URL` carries the
    Postgres password inline, so an unredacted banner publishes a live
    credential every run -- this repo has already had to rotate one secret that
    reached a transcript that way. Same defect exists in
    migrate_drop_status_override.py, migrate_mark_docs_external.py and
    migrate_result_split.py; all three have already run, so they are left alone
    rather than edited mid-deploy.
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
                     help="write changes (widen the constraint if safe). Without this flag: dry-run only.")
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
    print("TASK-1086 — widen ck_auth_service_token_scopes to admit 'write'")
    print(f"DB:   {_redact(args.db_url)}")
    print(f"mode: {'APPLY' if args.apply else 'DRY RUN (pass --apply to write)'}")
    print("=" * 78)

    report = plan(args.db_url, args.apply)
    print(json.dumps(report, indent=2, default=str))

    if not report["altered"] and not report["already_widened"]:
        # Either a dry run (expected, exit 0) or a REFUSED surprising state
        # (must exit non-zero so a caller scripting this notices). Tell them
        # apart by whether --apply was even requested.
        if args.apply:
            sys.exit(1)


if __name__ == "__main__":
    main()
