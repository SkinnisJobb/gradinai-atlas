"""`atlas-migrate upgrade`: build or bring up to date the database schema, then exit (STORY-oss-09).

HOW TO RUN IT

    In the image (the Dockerfile puts a two-line wrapper on PATH):
        docker exec atlas-api atlas-migrate upgrade
    which is exactly:
        docker exec atlas-api python /app/migrate_cli.py upgrade
    From a checkout, in `backend/`:
        python migrate_cli.py upgrade

It needs the same environment `atlas-api` boots with: `ATLAS_DB_URL`, and the fail-closed boot
secrets (`ATLAS_PASSWORD_PEPPER`, `ATLAS_TOKEN_PEPPER`, `ATLAS_LOGIN_ORIGINS`, or their `_FILE`
forms), because it imports `main` to reach `_reconcile_graph_schema` and importing `main` imports
the gate. Inside the running container that environment is already there. It does not need the
sign-in settings (ATLAS_AUTH_PROVIDERS, ATLAS_LOCAL_LOGIN, ATLAS_OIDC_*): run as a program it
ignores them, so it runs where the OIDC secret files are not mounted. Exit 0 on success, 1 on any
error, 2 on a usage error.

WHAT IT RUNS, AND WHAT IT DOES NOT

It is a WRAPPER around today's schema path, not a second one. Full Alembic was considered and
deferred (owner decision, 2026-10-14). `upgrade` runs, in startup's order, the schema steps of
`main.startup()`, by calling the same functions:

    db.Base.metadata.create_all            the v0 tables
    models_graph.Base.metadata.create_all  node/edge/node_type/... and task_id_seq
    models_auth.create_all                 auth_* + node_visibility
    main._reconcile_graph_schema()         ADD COLUMN / CREATE INDEX on tables that already exist
    models_graph.ensure_search_indexes     the full-text GIN indexes (DDL, IF NOT EXISTS)

Together those are every `metadatas()` base, then the reconcile, which is what startup does
before it touches any row. `ensure_search_indexes` is included because it is DDL that startup
runs, and without it an upgraded database would lack indexes a booted one has.

It does NOT run the parts of startup that write ROWS: the `ATLAS_RESEED` drop (never: an
upgrade must not destroy data), `_backfill_graph_origin`, the v0 seed, the node/edge type
registry seeding, and `_seed_task_id_sequence`. Those stay where they are and run on the next
boot, as they always have. So an upgraded database changes no data, and a boot afterwards still
does everything a boot did before.

Startup is not changed by this module. `main.startup()` keeps its own calls, its retry loop and
its order; this module adds an explicit way to run the schema half of it and nothing else. The
`migrate_*.py` scripts are separate and untouched: they are one-time, hand-run migrations for what
the reconcile cannot do (see the "WHAT THIS CANNOT DO" block in `main._reconcile_graph_schema`).

Idempotent: every step is create-if-missing or add-if-missing, so a second run changes nothing.
`verify_upgrade_command.py` proves that, and that the result equals the schema startup builds.
"""
from __future__ import annotations

import argparse
import os
import sys

# Run from anywhere (the PATH wrapper calls this by absolute path): the backend modules
# sit beside this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Run as a program, this command signs nobody in, so it drops the sign-in settings from its own
# environment before `main` (and with it `auth`) is imported (TASK-1457). On an OSS install with
# SSO on, atlas-migrate reads `.env`'s ATLAS_OIDC_* block but the OIDC files are mounted into
# atlas-api only; `auth` would stop at import on the unreadable group map, and atlas-api, which
# waits for this one-shot to succeed, would not start. The schema it builds is the same either
# way: identity_env.py's docstring says why.
if __name__ == "__main__":
    from identity_env import drop_identity_env
    drop_identity_env(os.environ)


def _schema_counts(engine) -> dict:
    """Tables, columns and indexes in the database's own schema, read from the catalogue.

    Only for the report line (TASK-1470): a first run on an empty database used to print the
    same "schema is up to date" as a re-run, so the log could not tell the two apart. Counts,
    not names, are compared: every step is add-if-missing, so a count that moved is something
    this run created.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        tables = conn.execute(text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'")).scalar_one()
        columns = conn.execute(text(
            "SELECT count(*) FROM information_schema.columns c "
            "JOIN information_schema.tables t ON t.table_schema = c.table_schema "
            "AND t.table_name = c.table_name AND t.table_type = 'BASE TABLE' "
            "WHERE c.table_schema = current_schema()")).scalar_one()
        indexes = conn.execute(text(
            "SELECT count(*) FROM pg_indexes WHERE schemaname = current_schema()")).scalar_one()
    return {"tables": int(tables), "columns": int(columns), "indexes": int(indexes)}


def report(before: dict, after: dict) -> str:
    """The one line `upgrade` prints: what this run created, or that nothing was missing.

    The re-run's line is unchanged ("schema is up to date"); install checks grep for it.
    """
    def counts(c: dict) -> str:
        names = (("tables", "table"), ("columns", "column"), ("indexes", "index"))
        return ", ".join("%d %s" % (c[k], one if c[k] == 1 else k) for k, one in names)

    if after == before:
        return "[atlas-migrate] upgrade: schema is up to date"
    if before["tables"] == 0:
        return "[atlas-migrate] upgrade: empty database, schema created (%s)" % counts(after)
    made = {k: after[k] - before[k] for k in after}
    return ("[atlas-migrate] upgrade: created what was missing (%s); schema is now up to date"
            % counts(made))


def upgrade() -> str:
    """Run startup's schema steps once against `ATLAS_DB_URL`; return the report line.

    Raises on failure.
    """
    # Imported here, not at the top, so `--help` works without a database or boot secrets.
    import main
    import models_auth
    import models_graph
    from db import Base, engine

    before = _schema_counts(engine)
    Base.metadata.create_all(engine)
    models_graph.Base.metadata.create_all(engine)
    models_auth.create_all(engine)
    main._reconcile_graph_schema()
    models_graph.ensure_search_indexes(engine)
    return report(before, _schema_counts(engine))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="atlas-migrate",
        description="Atlas schema command. 'upgrade' runs the schema steps application startup "
                    "runs (create_all for every base, then the additive reconcile) and exits.",
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("upgrade", help="create missing tables, columns and indexes; change no data")
    return p


def cli(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "upgrade":
        try:
            line = upgrade()
        except Exception as e:  # noqa: BLE001 — a CLI reports and exits non-zero
            print(f"[atlas-migrate] upgrade FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        print(line)
        return 0
    return 2  # unreachable: argparse rejects an unknown command first


if __name__ == "__main__":
    sys.exit(cli())
