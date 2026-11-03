"""
Restoring a backup over the live database (TASK-1416, OSS-26 C): `atlas-admin backup restore`,
`atlas-admin backup rollback`. The backups are `crunch_backup.py`'s (pg_dump custom format, a
`.sha256` and a `.json` manifest beside each); this module only restores them, and reuses that
module's credential handling (PGPASSFILE, conninfo without the password, `_scrub`) unchanged.

NOTHING HERE RUNS ON ITS OWN. An operator runs it, in the `backup` image (it carries pg_restore):

    docker compose stop atlas-api
    docker compose run --rm --no-deps atlas-backup atlas-admin backup restore latest --dry-run
    docker compose run --rm --no-deps atlas-backup atlas-admin backup restore latest
    docker compose start atlas-api

THE ORDER, and why the live database is the LAST thing touched:

  1. CHECKSUM FIRST. The dump's sha256 is recomputed and compared with its `.sha256` file and
     with the manifest's `sha256`; the manifest must name this file and carry the counts. Any
     difference stops the run before a database is opened.
  2. IN USE? Any other session connected to the target database (pg_stat_activity) is a
     refusal, naming how many and which applications: the operator stops atlas-api (and the
     `backup` profile's loop, if it runs) first. There is no override flag. A restore under a
     running API would hand it a database swapped beneath its connection pool, and its writes
     between the check and the swap would be lost without a word. A session is looked at three
     times a second apart, so a probe that connects for a moment (a health check's pg_isready)
     is not mistaken for a user and a real one is not missed. Postgres enforces the same
     rule a second time: ALTER DATABASE ... RENAME fails while anyone else is connected, so a
     session that arrives after the check still stops the swap rather than being cut off.
  3. RESTORE ASIDE. A STAGING database (`atlas_restore_<UTC>_<hex>`) is created with the live
     database's encoding, collation, locale provider and owner, the dump is restored into it
     with `pg_restore --exit-on-error`, and node, edge and graph_event are counted there against
     the manifest. A failure or a difference drops the staging database; the live one has not
     been touched.
  4. SWAP. The live database is RENAMED to `<db>_prerestore_<UTC>` (the safety copy) and the
     staging database renamed into its place. If the second rename fails, the first is undone.
  5. COUNT AGAIN, on the database now under the live name, against the manifest.

`--dry-run` does 1 to 3, reports what 2 found instead of refusing, drops the staging database,
and says what a real run would do. It changes nothing.

WHY RENAME ASIDE, not a dump of the current database, for the safety copy: a rename is instant
and costs no disk at restore time whatever the size; the copy is the ORIGINAL database itself,
byte for byte (not a logical copy that could fail to dump or restore in its own right); and
rolling back is another rename, which cannot fail half-way. The cost is that the copy takes its
full size on disk until it is dropped; the output names it and the command that drops it.

ROLLBACK. `atlas-admin backup rollback` lists the safety copies; `backup rollback --to
<db>_prerestore_<UTC>` (API stopped, the same in-use refusal) renames the current database
aside to `<db>_rolledback_<UTC>` and the safety copy back to the live name. Nothing is dropped by
either command: dropping a copy is the operator's (`DROP DATABASE`), once they are satisfied.

WHAT IS NOT CARRIED OVER. pg_dump without --create holds no database-level settings (ALTER
DATABASE ... SET) and no grants ON the database. The new database has the old one's owner;
when the old one had settings or extra grants the run says so, and the safety copy still has
them. Atlas sets neither.

NOTHING IS RECORDED IN THE DATABASE. The restored database is exactly the backup: writing a
history row or an event into it would make it differ from the manifest it was counted against.
The run's output is its record.

The maintenance connection (CREATE/ALTER/DROP DATABASE, pg_stat_activity) goes to the server's
`postgres` database (`template1` when there is none), never to the target.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import NullPool

import crunch_backup as cb

STAGE_PREFIX = "atlas_restore_"
SAFETY_TAG = "_prerestore_"
ROLLEDBACK_TAG = "_rolledback_"
COPY_RE = re.compile(r"^(?P<db>.+)_(?P<kind>prerestore|rolledback)_(?P<ts>\d{8}t\d{6}z)$")


class RestoreError(Exception):
    """A restore or a rollback refused or failed; the message is already scrubbed."""


# --------------------------------------------------------------------------- #
# The dump and its manifest
# --------------------------------------------------------------------------- #

def resolve_dump(spec: str, directory: Path) -> Path:
    """`latest` (the newest dump in `directory`), a file name in `directory`, or a path."""
    if spec == "latest":
        found = cb.backup_files(Path(directory))
        if not found:
            raise RestoreError(f"no backup in {directory} (files named atlas-<UTC>-<reason>.dump)")
        return found[-1][1]
    p = Path(spec)
    if not p.is_file() and not p.is_absolute() and (Path(directory) / spec).is_file():
        p = Path(directory) / spec
    if not p.is_file():
        raise RestoreError(f"{spec}: no such dump (a path, a file name in {directory}, or `latest`)")
    return p


def check_dump(dump: Path) -> dict:
    """Checksum and manifest, before anything else. Returns the manifest."""
    dump = Path(dump)
    sidecar = dump.with_name(dump.name + ".sha256")
    manifest_path = dump.with_name(dump.name + ".json")
    if not sidecar.is_file():
        raise RestoreError(f"{dump.name}: no checksum file {sidecar.name}; refusing to restore it")
    if not manifest_path.is_file():
        raise RestoreError(f"{dump.name}: no manifest {manifest_path.name}; refusing to restore it")
    recorded = sidecar.read_text(encoding="utf-8").split()
    if not recorded or not re.fullmatch(r"[0-9a-f]{64}", recorded[0]):
        raise RestoreError(f"{dump.name}: {sidecar.name} holds no sha256")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RestoreError(f"{dump.name}: {manifest_path.name} is not JSON ({exc})") from None
    counts = manifest.get("counts") if isinstance(manifest, dict) else None
    if not isinstance(counts, dict) or set(counts) != set(cb.COUNTED):
        raise RestoreError(f"{dump.name}: the manifest has no counts for {', '.join(cb.COUNTED)}")
    if manifest.get("file") != dump.name:
        raise RestoreError(f"{dump.name}: the manifest is for {manifest.get('file')!r}, not this file")
    actual = cb.sha256_of(dump)
    if actual != recorded[0] or actual != manifest.get("sha256"):
        raise RestoreError(f"{dump.name}: CHECKSUM MISMATCH (file {actual[:12]}, checksum file "
                           f"{recorded[0][:12]}, manifest {str(manifest.get('sha256'))[:12]}): "
                           f"the file changed after it was written; nothing was restored")
    return manifest


# --------------------------------------------------------------------------- #
# The server
# --------------------------------------------------------------------------- #

def _sql(conn, statement: str, *parts) -> None:
    """DDL on an AUTOCOMMIT connection, every name and literal quoted by psycopg2.sql."""
    from psycopg2 import sql
    cur = conn.connection.dbapi_connection.cursor()
    try:
        cur.execute(sql.SQL(statement).format(*parts))
    finally:
        cur.close()


def _ident(name: str):
    from psycopg2 import sql
    return sql.Identifier(name)


def _lit(value: str):
    from psycopg2 import sql
    return sql.Literal(value)


def maintenance_engine(url: str) -> Engine:
    """AUTOCOMMIT, no pool, on `postgres` (or `template1`): never on the target database."""
    last = None
    for name in ("postgres", "template1"):
        engine = create_engine(make_url(url).set(database=name), isolation_level="AUTOCOMMIT",
                               poolclass=NullPool)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return engine
        except Exception as exc:  # noqa: BLE001 - tried in turn, reported scrubbed
            last = exc
            engine.dispose()
    raise RestoreError(cb._scrub(f"cannot open a maintenance connection (postgres, template1): {last}"))


def sessions(conn, dbname: str) -> list[tuple]:
    """Every OTHER client session on `dbname`: (pid, application_name, user). Background
    workers (autovacuum) are not clients; Postgres itself stops them for the rename."""
    return [tuple(r) for r in conn.execute(text(
        "SELECT pid, coalesce(nullif(application_name, ''), '(no name)'), usename "
        "FROM pg_stat_activity WHERE datname = :d AND pid <> pg_backend_pid() "
        "AND backend_type = 'client backend' ORDER BY pid"),
        {"d": dbname})]


#: A session seen this many times, a second apart, is someone connected; one that is gone by
#: then was a probe (a health check's pg_isready, a psql that just ended), not a reason to refuse.
SETTLE_TRIES = 3


def settled_sessions(conn, dbname: str) -> list[tuple]:
    busy = sessions(conn, dbname)
    for _ in range(SETTLE_TRIES - 1):
        if not busy:
            break
        time.sleep(1)
        busy = sessions(conn, dbname)
    return busy


def _rename_retrying(conn, old: str, new: str) -> None:
    """ALTER DATABASE ... RENAME, which Postgres refuses while any other session is connected
    (the second guard). A probe that connects for a moment is waited out; anything else is
    raised to the caller."""
    for attempt in range(SETTLE_TRIES):
        try:
            _rename(conn, old, new)
            return
        except Exception:  # noqa: BLE001 - re-raised on the last attempt
            if attempt == SETTLE_TRIES - 1:
                raise
            time.sleep(1)


def db_info(conn, dbname: str) -> Optional[dict]:
    row = conn.execute(text(
        "SELECT to_jsonb(d) AS j, pg_encoding_to_char(d.encoding) AS enc, "
        "pg_get_userbyid(d.datdba) AS owner, "
        "(SELECT count(*) FROM pg_db_role_setting s WHERE s.setdatabase = d.oid) AS settings "
        "FROM pg_database d WHERE d.datname = :d"), {"d": dbname}).mappings().first()
    if row is None:
        return None
    info = dict(row["j"])
    info.update(encoding_name=row["enc"], owner=row["owner"], settings=int(row["settings"]))
    return info


def exists(conn, dbname: str) -> bool:
    return bool(conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": dbname}).first())


def create_like(conn, new: str, info: dict) -> None:
    """CREATE DATABASE `new` with `info`'s encoding, collation, locale provider and owner."""
    stmt = "CREATE DATABASE {} TEMPLATE template0 ENCODING {} LC_COLLATE {} LC_CTYPE {}"
    parts = [_ident(new), _lit(info["encoding_name"]), _lit(info["datcollate"]), _lit(info["datctype"])]
    provider = info.get("datlocprovider") or "c"
    locale = info.get("datlocale") or info.get("daticulocale")
    if provider == "i":
        stmt += " LOCALE_PROVIDER icu ICU_LOCALE {}"
        parts.append(_lit(locale))
    elif provider == "b":
        stmt += " LOCALE_PROVIDER builtin BUILTIN_LOCALE {}"
        parts.append(_lit(locale))
    stmt += " OWNER {}"
    parts.append(_ident(info["owner"]))
    _sql(conn, stmt, *parts)


def _drop(conn, name: str) -> None:
    _sql(conn, "DROP DATABASE IF EXISTS {} WITH (FORCE)", _ident(name))


def _rename(conn, old: str, new: str) -> None:
    _sql(conn, "ALTER DATABASE {} RENAME TO {}", _ident(old), _ident(new))


def count_db(url: str, dbname: str) -> dict:
    engine = create_engine(make_url(url).set(database=dbname), poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return cb._count(conn)
    finally:
        engine.dispose()


def _fmt(counts: Optional[dict]) -> str:
    return ", ".join(f"{k}={v}" for k, v in (counts or {}).items())


def _busy_message(dbname: str, busy: list) -> str:
    apps = sorted({app for _, app, _ in busy})
    return (f"the database {dbname!r} is in use: {len(busy)} other session(s) "
            f"({', '.join(apps)}). Stop what is connected first, e.g. `docker compose stop "
            f"atlas-api` (and atlas-backup, if the backup profile's loop runs), then run this "
            f"again; start atlas-api after the restore. Nothing was changed.")


def _copy_name(target: str, tag: str) -> str:
    # 63 bytes is Postgres's identifier limit: the database name part is cut, never the stamp.
    return f"{target[:30]}{tag}{cb._stamp(cb._now()).lower()}"


# --------------------------------------------------------------------------- #
# Restore
# --------------------------------------------------------------------------- #

def restore(dump: Path, url: str, *, dry_run: bool = False, pg_bin: Optional[str] = None,
            say: Callable[[str], None] = print) -> dict:
    """See the module docstring. Returns {target, safety_copy, counts, dry_run}; raises
    RestoreError (scrubbed) on any refusal or failure, after cleaning up its staging database."""
    dump = Path(dump)
    manifest = check_dump(dump)
    expected = manifest["counts"]
    say(f"checksum ok: {dump.name} (sha256 {manifest['sha256'][:12]}, taken "
        f"{manifest.get('created_at', '?')}, {_fmt(expected)})")
    pg_restore = cb._tool("pg_restore", pg_bin)
    target = make_url(url).database
    if not target:
        raise RestoreError("the database URL names no database")
    stage = f"{STAGE_PREFIX}{cb._stamp(cb._now()).lower()}_{secrets.token_hex(3)}"
    admin = maintenance_engine(url)
    staged = False
    try:
        with admin.connect() as conn:
            info = db_info(conn, target)
            if info is None:
                raise RestoreError(f"the database {target!r} does not exist on this server")
            busy = settled_sessions(conn, target)
        if busy and not dry_run:
            raise RestoreError(_busy_message(target, busy))
        before = count_db(url, target)
        say(f"live database {target!r} now: {_fmt(before)}")

        with admin.connect() as conn:
            create_like(conn, stage, info)
            staged = True
        with cb._PassFile(url) as pw:
            cb._run_tool([pg_restore, "--exit-on-error", "--no-owner", "--no-privileges",
                          "--no-password", "--dbname", cb._conninfo(url, stage), str(dump)],
                         pw, f"{dump.name}: pg_restore into the staging database")
        restored = count_db(url, stage)
        if restored != expected:
            raise RestoreError(f"{dump.name}: RESTORE MISMATCH in the staging database: restored "
                               f"{_fmt(restored)}, manifest {_fmt(expected)}; the live database "
                               f"was not touched")
        say(f"restored into a staging database and counted equal to the manifest ({_fmt(restored)})")
        safety = _copy_name(target, SAFETY_TAG)

        if dry_run:
            if busy:
                say("DRY RUN: a real run would REFUSE now: " + _busy_message(target, busy))
            else:
                say(f"DRY RUN: nothing is connected to {target!r}; a real run would rename it to "
                    f"{safety!r} (the safety copy) and put the restored copy in its place")
            if info["settings"] or info.get("datacl"):
                say(f"note: {target!r} has database-level settings or grants; a dump does not "
                    f"carry them, and a real run would leave them on the safety copy only")
            say("DRY RUN: nothing was changed")
            return {"target": target, "safety_copy": None, "counts": restored, "dry_run": True}

        with admin.connect() as conn:
            busy = settled_sessions(conn, target)
            if busy:
                raise RestoreError(_busy_message(target, busy))
            try:
                _rename_retrying(conn, target, safety)
            except Exception as exc:  # noqa: BLE001 - someone connected after the check
                raise RestoreError(cb._scrub(f"could not set {target!r} aside ({exc}); nothing "
                                             f"was changed")) from None
            try:
                _rename(conn, stage, target)
                staged = False
            except Exception as exc:  # noqa: BLE001 - put the original back, then say so
                try:
                    _rename(conn, safety, target)
                    undone = f"{target!r} is back as it was"
                except Exception as exc2:  # noqa: BLE001
                    undone = (f"AND putting it back failed ({exc2}): the original database is "
                              f"{safety!r}; rename it to {target!r} by hand")
                raise RestoreError(cb._scrub(f"could not move the restored copy into place "
                                             f"({exc}); {undone}")) from None
        final = count_db(url, target)
        if final != expected:
            raise RestoreError(f"after the swap {target!r} counts {_fmt(final)}, manifest "
                               f"{_fmt(expected)}. Roll back with `atlas-admin backup rollback "
                               f"--to {safety}`")
        say(f"restored: {target!r} is now {dump.name} ({_fmt(final)}), counted equal to its manifest")
        say(f"safety copy: the database as it was is {safety!r} ({_fmt(before)})")
        if info["settings"] or info.get("datacl"):
            say(f"note: {target!r} had database-level settings or grants; they are on "
                f"{safety!r} only (a dump does not carry them)")
        say(f"roll back: `atlas-admin backup rollback --to {safety}` (atlas-api stopped)")
        say(f"when satisfied, drop the copy: `DROP DATABASE \"{safety}\";` (it holds its full size on disk)")
        return {"target": target, "safety_copy": safety, "counts": final, "dry_run": False}
    except RestoreError:
        raise
    except cb.BackupError as exc:
        raise RestoreError(cb._scrub(exc)) from None
    except Exception as exc:  # noqa: BLE001 - every failure is a loud, scrubbed RestoreError
        raise RestoreError(cb._scrub(f"{dump.name}: restore failed: {type(exc).__name__}: {exc}")) from None
    finally:
        if staged:
            try:
                with admin.connect() as conn:
                    _drop(conn, stage)
            except Exception as exc:  # noqa: BLE001
                say(cb._scrub(f"warning: could not drop the staging database {stage}: {exc}"))
        admin.dispose()


# --------------------------------------------------------------------------- #
# Rollback, and the copies
# --------------------------------------------------------------------------- #

def copies(url: str) -> list[tuple[str, str, int]]:
    """(name, kind, bytes) of every safety and rolled-back copy of the target database."""
    target = make_url(url).database or ""
    admin = maintenance_engine(url)
    try:
        with admin.connect() as conn:
            rows = conn.execute(text(
                "SELECT datname, pg_database_size(datname) FROM pg_database ORDER BY datname")).all()
    finally:
        admin.dispose()
    out = []
    for name, size in rows:
        m = COPY_RE.match(name)
        if m and m.group("db") == target[:30]:
            out.append((name, m.group("kind"), int(size)))
    return out


def rollback(url: str, to: str, *, say: Callable[[str], None] = print) -> dict:
    """Put safety copy `to` back under the live name; the current database is set aside as
    `<db>_rolledback_<UTC>`. Refuses while the live database is in use. Drops nothing."""
    target = make_url(url).database or ""
    m = COPY_RE.match(to or "")
    if not m or m.group("kind") != "prerestore" or m.group("db") != target[:30]:
        raise RestoreError(f"{to!r} is not a safety copy of {target!r} "
                           f"(`atlas-admin backup rollback` lists them)")
    aside = _copy_name(target, ROLLEDBACK_TAG)
    admin = maintenance_engine(url)
    try:
        with admin.connect() as conn:
            if not exists(conn, to):
                raise RestoreError(f"there is no database {to!r} (`atlas-admin backup rollback` lists them)")
            busy = settled_sessions(conn, target) + settled_sessions(conn, to)
            if busy:
                raise RestoreError(_busy_message(target, busy))
            try:
                _rename_retrying(conn, target, aside)
            except Exception as exc:  # noqa: BLE001
                raise RestoreError(cb._scrub(f"could not set {target!r} aside ({exc}); nothing "
                                             f"was changed")) from None
            try:
                _rename(conn, to, target)
            except Exception as exc:  # noqa: BLE001
                _rename(conn, aside, target)
                raise RestoreError(cb._scrub(f"could not put {to!r} back ({exc}); {target!r} "
                                             f"is as it was")) from None
    finally:
        admin.dispose()
    counts = count_db(url, target)
    say(f"rolled back: {target!r} is the safety copy {to!r} again ({_fmt(counts)})")
    say(f"the database it replaced is {aside!r}; drop it when satisfied: `DROP DATABASE \"{aside}\";`")
    return {"target": target, "set_aside": aside, "counts": counts}
