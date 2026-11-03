"""
Crunch-week backups (STORY-atlas-crunch-mode): what `crunch.backup.policy` asks for.

OFF UNLESS ASKED. Nothing here runs on its own. A backup is taken only when

  * an operator runs `atlas-admin backup run` (reason `manual`, the default: always runs), or
  * the CI/CD per-deploy step runs `atlas-admin backup run --reason per_deploy` and the active
    crunch says `per_deploy`, or
  * the opt-in `backup` compose profile (deploy/oss/docker-compose.yml, service atlas-backup)
    is switched on, its loop (`python -m crunch_backup`) asks `crunch.current()` every tick,
    and the active crunch says `hourly`, `daily` or `once` (one backup at activation), or
  * that loop runs with ATLAS_BACKUP_SCHEDULE set (OSS-26 C: a `routine` backup outside crunch,
    see ROUTINE BACKUPS below), or an operator's cron runs `backup run --reason routine`.

With crunch off, a policy of `none`, or a policy that is not the caller's, every entry point
says so and writes nothing: no file, no history row, no event. The homelab's `no-backup` intent on
its `atlas` node stands; this is an install's opt-in.

WHAT A BACKUP IS. A logical `pg_dump --format=custom` of the Atlas database, and it is not
called a backup until it has been RESTORED and COUNTED:

  1. One REPEATABLE READ transaction exports its snapshot (`pg_export_snapshot()`) and counts
     node, edge and graph_event in it; pg_dump is run with `--snapshot`, so the dump and the
     counts are the same instant, however busy the database is.
  2. The file's sha256 is written beside it (`<file>.sha256`, `sha256sum -c` format).
  3. Verification: the checksum is re-read and compared, then the dump is restored with
     `pg_restore --exit-on-error` into a TEMPORARY database in the same Postgres
     (`CREATE DATABASE atlas_verify_<ts>_<rand>`), the three tables are counted there, and the
     database is dropped (`WITH (FORCE)`, in a `finally`). Any difference is a failure.
  4. A manifest (`<file>.json`: counts, sha256, size, reason, versions) is written last.

WHY THE SAME POSTGRES, not a disposable one (tools/snapshot.sh starts a throwaway container,
which it can because it runs on the host; a container in this stack cannot start containers):
the restore runs against the exact server, extensions (pgvector) and collation the backup
would be restored to, it needs no Docker socket (which would be root on the host) and no
second database image. The cost is a database that exists for the length of the restore and
the CREATEDB right; the OSS install's `atlas` role is the image's superuser, so it has it.

A dump that fails verification is DELETED (with its checksum): a file that does not restore is
not a backup, and keeping it would make "the newest file" a lie. Its failure is recorded.

RETENTION. After a VERIFIED backup only, files of this module's naming pattern
(`atlas-<UTC>-<reason>.dump` and their `.sha256`/`.json`) older than `retention_days` (from the
active crunch's config) are deleted. Nothing else in the directory is touched, nothing is
pruned after a failure (the last good backup is never thrown away for a broken new one), and
with no retention (crunch off, a manual run) nothing is pruned.

WHERE THE RESULT GOES. Each attempt, ok or not, is reported through `crunch.record_backup`
(agent 1's half of the contract): one `crunch_history` row (action `backup`) and one
`node.crunch.backup` graph_event on `atlas-system`, detail `{ok, verified, label}`. The label is
the dump's file NAME (`atlas-<UTC>-<reason>.dump`, `... (failed)` for a failed attempt): never a
path, never a credential. GET /api/crunch's `last_backup` is `crunch.last_backup`. The
sha256, the counts and a failure's reason are in the manifest and in this module's output (the
loop's log, the CLI's stderr), not in the event. The scheduler reads its own past attempts back
from those history rows (`_attempts`).

ROUTINE BACKUPS OUTSIDE CRUNCH (TASK-1416, OSS-26 C; opt-in). `ATLAS_BACKUP_SCHEDULE` (off |
hourly | daily | weekly, default off) and `ATLAS_BACKUP_KEEP` (default 7), read through
`settings.load_backup_schedule`. Unset or `off`, the loop is exactly what it was. Set, every tick
FIRST does what it always did (asks the crunch, backs up when the crunch policy is due); only on a
tick where the crunch path took no backup does it look at the routine schedule: a `routine`
backup (`atlas-<UTC>-routine.dump`) is due when the newest routine dump in the directory is older
than the interval, and a failed attempt is retried after RETRY_AFTER, read back from the history
rows like the crunch's. After a VERIFIED routine backup, routine dumps beyond the newest
`ATLAS_BACKUP_KEEP` are deleted with their sidecars (`prune_keep`); no other file is touched.
`atlas-admin backup run --reason routine` is the same backup and prune for a host cron. A routine
attempt is recorded like a manual one (crunch.record_backup), so `last_backup` shows it.

RESTORE. `backup_restore.py` (`atlas-admin backup restore`) restores one of these dumps over the
live database, after checking it against its checksum and manifest; its docstring has the design.

CREDENTIALS. The DB URL is read through `settings.read_secret("ATLAS_DB_URL")`, i.e. from
`ATLAS_DB_URL_FILE` in the OSS install. The password never reaches an argv, an environment
value or the output: pg_dump/pg_restore get a conninfo WITHOUT it and a PGPASSFILE (0600, in a
private temp dir, removed in a `finally`). Every message that leaves this module is passed
through `_scrub`, which removes the password and the URL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import create_engine, func, select, table, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import NullPool

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import settings  # noqa: E402

DEFAULT_DIR = "/var/lib/atlas/backups"
#: The tables whose row counts must survive the round trip.
COUNTED = ("node", "edge", "graph_event")
#: `manual` always runs; the others run only when the active crunch's policy is theirs.
REASONS = ("manual", "per_deploy", "once", "scheduled", "routine")
POLICY_OF_REASON = {"per_deploy": ("per_deploy",), "once": ("once",),
                    "scheduled": ("hourly", "daily")}
INTERVAL = {"hourly": timedelta(hours=1), "daily": timedelta(days=1)}
#: The routine schedule's intervals (ATLAS_BACKUP_SCHEDULE; `off` has none).
ROUTINE_INTERVAL = {"hourly": timedelta(hours=1), "daily": timedelta(days=1),
                    "weekly": timedelta(days=7)}
#: A failed scheduled/once attempt is retried after this, not every tick.
RETRY_AFTER = timedelta(minutes=15)
DEFAULT_TICK_S = 60
TOOL_TIMEOUT_S = 6 * 3600

NAME_RE = re.compile(r"^atlas-(\d{8}T\d{6}Z)-(manual|per_deploy|once|scheduled|routine)\.dump$")
#: The reason, read back off a recorded label (a file name, or one marked failed).
LABEL_REASON_RE = re.compile(r"^atlas-\d{8}T\d{6}Z-(manual|per_deploy|once|scheduled|routine)\.dump")
SIDE_SUFFIXES = (".sha256", ".json")

class BackupError(Exception):
    """A backup or a verification failed; the message is already scrubbed."""


# --------------------------------------------------------------------------- #
# The database URL, and keeping it out of every output
# --------------------------------------------------------------------------- #

_SECRETS_SEEN: list[str] = []


def db_url() -> str:
    try:
        url = settings.read_secret(settings.DB_URL_ENV)
    except settings.SecretError as exc:
        raise BackupError(str(exc)) from None
    _remember_secret(url)
    return url


def _remember_secret(url: str) -> None:
    if url and url not in _SECRETS_SEEN:
        _SECRETS_SEEN.append(url)
    try:
        pw = make_url(url).password
    except Exception:  # noqa: BLE001 - an unparsable URL is still scrubbed whole
        pw = None
    if pw and pw not in _SECRETS_SEEN:
        _SECRETS_SEEN.append(str(pw))


def _scrub(message: object) -> str:
    out = str(message)
    # Longest first, so the URL goes as a whole before its password is looked for in it.
    for secret in sorted(_SECRETS_SEEN, key=len, reverse=True):
        if secret:
            out = out.replace(secret, "***")
    # Belt and braces: any user:password@ form left in a message.
    return re.sub(r"(://[^:/@\s]+:)[^@\s]+@", r"\1***@", out)


def _conninfo_value(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _conninfo(url: str, dbname: Optional[str] = None) -> str:
    """A libpq conninfo for the URL WITHOUT its password (that goes in the PGPASSFILE)."""
    u = make_url(url)
    parts = {"dbname": dbname or u.database or ""}
    if u.host:
        parts["host"] = u.host
    if u.port:
        parts["port"] = str(u.port)
    if u.username:
        parts["user"] = u.username
    for key, value in (u.query or {}).items():
        if key.lower() == "password":
            continue
        parts[key] = value if isinstance(value, str) else value[-1]
    return " ".join(f"{k}={_conninfo_value(v)}" for k, v in parts.items())


class _PassFile:
    """A private PGPASSFILE for one run: 0600 in a 0700 temp dir, removed on exit."""

    def __init__(self, url: str) -> None:
        self.password = make_url(url).password
        self.dir: Optional[str] = None
        self.path: Optional[str] = None

    def __enter__(self) -> "_PassFile":
        self.dir = tempfile.mkdtemp(prefix="atlas-backup-")
        if self.password is not None:
            self.path = os.path.join(self.dir, "pgpass")
            esc = str(self.password).replace("\\", "\\\\").replace(":", "\\:")
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"*:*:*:*:{esc}\n")
        return self

    def env(self) -> dict:
        # No ATLAS_* (the peppers, a DB URL) and no inherited PG* (a stray PGPASSWORD) reaches
        # the client tools: their credential is the PGPASSFILE and nothing else.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "ATLAS_"))}
        if self.path:
            env["PGPASSFILE"] = self.path
        return env

    def __exit__(self, *exc) -> None:
        if self.dir:
            shutil.rmtree(self.dir, ignore_errors=True)


def _tool(name: str, pg_bin: Optional[str]) -> str:
    if pg_bin:
        path = os.path.join(pg_bin, name)
        if os.path.isfile(path):
            return path
        raise BackupError(f"{name} not found in {pg_bin}")
    found = shutil.which(name)
    if not found:
        raise BackupError(
            f"{name} is not on PATH. The Postgres client ships in backend/Dockerfile's `backup` "
            f"stage (compose service atlas-backup, profile `backup`); run the backup there.")
    return found


def _run_tool(cmd: list[str], pw: _PassFile, what: str) -> str:
    try:
        proc = subprocess.run(cmd, env=pw.env(), capture_output=True, text=True,
                              timeout=TOOL_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise BackupError(f"{what} timed out after {TOOL_TIMEOUT_S}s") from None
    except OSError as exc:
        raise BackupError(f"{what} could not start: {exc.strerror or exc}") from None
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise BackupError(f"{what} failed (exit {proc.returncode}): " + " | ".join(tail))
    return proc.stdout


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _side(dump: Path, suffix: str) -> Path:
    return dump.with_name(dump.name + suffix)


def backup_files(directory: Path) -> list[tuple[datetime, Path]]:
    """(created, path) of every dump of this module's naming pattern, oldest first."""
    out = []
    if not directory.is_dir():
        return out
    for p in directory.iterdir():
        m = NAME_RE.match(p.name)
        if m and p.is_file():
            out.append((datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), p))
    return sorted(out)


def prune(directory: Path, retention_days: Optional[int], now: Optional[datetime] = None) -> list[str]:
    """Delete this module's dumps (and their sidecars) older than `retention_days`. Returns
    the deleted names. None or < 1 keeps everything. Only names matching NAME_RE are seen, and
    `routine` dumps are not: they have their own count rule (`prune_keep`, ATLAS_BACKUP_KEEP), so
    a crunch's retention never thins an install's routine series."""
    if not retention_days or int(retention_days) < 1:
        return []
    cutoff = (now or _now()) - timedelta(days=int(retention_days))
    gone = []
    for created, dump in backup_files(directory):
        if created < cutoff and NAME_RE.match(dump.name).group(2) != "routine":
            for p in (dump, *(_side(dump, s) for s in SIDE_SUFFIXES)):
                if p.exists():
                    p.unlink()
                    gone.append(p.name)
    return gone


def prune_keep(directory: Path, reason: str, keep: Optional[int]) -> list[str]:
    """Keep the newest `keep` dumps of `reason` (and their sidecars); delete the older ones.
    Returns the deleted names. None or < 1 keeps everything. Only names matching NAME_RE with
    this reason are seen, so no other backup and no other file is ever touched."""
    if not keep or int(keep) < 1:
        return []
    mine = [p for _, p in backup_files(directory) if NAME_RE.match(p.name).group(2) == reason]
    gone = []
    for dump in mine[: max(0, len(mine) - int(keep))]:
        for p in (dump, *(_side(dump, s) for s in SIDE_SUFFIXES)):
            if p.exists():
                p.unlink()
                gone.append(p.name)
    return gone


# --------------------------------------------------------------------------- #
# Counting, dumping, restoring
# --------------------------------------------------------------------------- #

def _count(conn) -> dict:
    out = {}
    for t in COUNTED:
        present = conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{t}"}).scalar()
        out[t] = None if present is None else int(conn.execute(
            select(func.count()).select_from(table(t, schema="public"))).scalar())
    return out


def _ddl(conn, statement: str, name: str) -> None:
    """CREATE/DROP DATABASE on an AUTOCOMMIT connection, the name quoted by the driver
    (psycopg2.sql.Identifier); no SQL string is assembled by hand. The name is generated here
    from a timestamp and random hex in any case: no input reaches it."""
    from psycopg2 import sql
    cur = conn.connection.dbapi_connection.cursor()
    try:
        cur.execute(sql.SQL(statement).format(sql.Identifier(name)))
    finally:
        cur.close()


def _pg_version(tool: str, pw: _PassFile) -> str:
    return _run_tool([tool, "--version"], pw, f"{os.path.basename(tool)} --version").strip()


def verify_dump(dump: Path, expected: dict, url: str, *, pg_bin: Optional[str] = None) -> dict:
    """Checksum, then restore into a temporary database in the same server and count.
    Returns the restored counts; raises BackupError on any difference or failure."""
    dump = Path(dump)
    sidecar = _side(dump, ".sha256")
    if not dump.is_file():
        raise BackupError(f"{dump.name}: the dump is missing")
    if not sidecar.is_file():
        raise BackupError(f"{dump.name}: no checksum file {sidecar.name}")
    recorded = sidecar.read_text(encoding="utf-8").split()
    if not recorded or not re.fullmatch(r"[0-9a-f]{64}", recorded[0]):
        raise BackupError(f"{dump.name}: {sidecar.name} holds no sha256")
    actual = sha256_of(dump)
    if actual != recorded[0]:
        raise BackupError(f"{dump.name}: CHECKSUM MISMATCH (recorded {recorded[0][:12]}, "
                          f"file {actual[:12]}): the file changed after it was written")
    pg_restore = _tool("pg_restore", pg_bin)
    name = f"atlas_verify_{_stamp(_now()).lower()}_{secrets.token_hex(3)}"
    admin_engine = create_engine(url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    created = False
    try:
        with admin_engine.connect() as conn:
            _ddl(conn, "CREATE DATABASE {} TEMPLATE template0", name)
            created = True
        with _PassFile(url) as pw:
            _run_tool([pg_restore, "--exit-on-error", "--no-owner", "--no-privileges", "--no-password",
                       "--dbname", _conninfo(url, name), str(dump)],
                      pw, f"{dump.name}: pg_restore into a temporary database")
        verify_engine = create_engine(make_url(url).set(database=name), poolclass=NullPool)
        try:
            with verify_engine.connect() as conn:
                restored = _count(conn)
        finally:
            verify_engine.dispose()
    except BackupError:
        raise
    except Exception as exc:  # noqa: BLE001 - every failure is a loud, scrubbed BackupError
        raise BackupError(f"{dump.name}: verification failed: {_scrub(exc)}") from None
    finally:
        if created:
            try:
                with admin_engine.connect() as conn:
                    _ddl(conn, "DROP DATABASE IF EXISTS {} WITH (FORCE)", name)
            except Exception as exc:  # noqa: BLE001
                print(_scrub(f"warning: could not drop temporary database {name}: {exc}"),
                      file=sys.stderr)
        admin_engine.dispose()
    if restored != expected:
        raise BackupError(f"{dump.name}: RESTORE MISMATCH: restored {restored}, source {expected}")
    return restored


@dataclass
class Result:
    ok: bool
    reason: str
    started_at: datetime
    finished_at: datetime
    file: Optional[str] = None
    sha256: Optional[str] = None
    bytes: Optional[int] = None
    counts: Optional[dict] = None
    verified: bool = False
    pruned: list = field(default_factory=list)
    error: Optional[str] = None

    def line(self) -> str:
        if not self.ok:
            return f"backup FAILED ({self.reason}): {self.error}"
        counts = ", ".join(f"{k}={v}" for k, v in (self.counts or {}).items())
        extra = f"; pruned {len(self.pruned)} old file(s)" if self.pruned else ""
        return (f"backup ok ({self.reason}): {self.file}, {self.bytes} bytes, sha256 "
                f"{(self.sha256 or '')[:12]}; restored into a temporary database and counted "
                f"equal ({counts}){extra}")


def take_backup(url: str, directory: Path, reason: str, *, retention_days: Optional[int] = None,
                pg_bin: Optional[str] = None, keep: Optional[int] = None) -> Result:
    """Dump, checksum, verify, manifest, prune. Never raises: the Result says what happened.
    `retention_days` is the crunch's rule (every dump of this module older than it); `keep` is
    the routine rule (only dumps of THIS reason beyond the newest `keep`)."""
    started = _now()
    directory = Path(directory)
    final = directory / f"atlas-{_stamp(started)}-{reason}.dump"
    partial = final.with_name(final.name + ".partial")
    res = Result(ok=False, reason=reason, started_at=started, finished_at=started, file=final.name)
    wrote = False  # only files this run created are ever removed on failure
    try:
        directory.mkdir(parents=True, exist_ok=True)
        for _ in range(5):  # two backups in one second: take the next second's name
            if not (final.exists() or partial.exists()):
                break
            time.sleep(1)
            final = directory / f"atlas-{_stamp(_now())}-{reason}.dump"
            partial = final.with_name(final.name + ".partial")
            res.file = final.name
        else:
            raise BackupError(f"{final.name} already exists")
        pg_dump = _tool("pg_dump", pg_bin)
        _tool("pg_restore", pg_bin)  # refuse before dumping if the restore cannot run
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
                with conn.begin():
                    snapshot = conn.execute(text("SELECT pg_export_snapshot()")).scalar()
                    counts = _count(conn)
                    server = conn.execute(text("SHOW server_version")).scalar()
                    with _PassFile(url) as pw:
                        dump_version = _pg_version(pg_dump, pw)
                        _run_tool([pg_dump, "--format=custom", "--no-password",
                                   f"--snapshot={snapshot}", "--file", str(partial),
                                   "--dbname", _conninfo(url)], pw, "pg_dump")
        finally:
            engine.dispose()
        digest = sha256_of(partial)
        size = partial.stat().st_size
        os.replace(partial, final)
        wrote = True
        _side(final, ".sha256").write_text(f"{digest}  {final.name}\n", encoding="utf-8")
        res.sha256, res.bytes, res.counts = digest, size, counts
        verify_dump(final, counts, url, pg_bin=pg_bin)
        res.verified = True
        _side(final, ".json").write_text(json.dumps({
            "file": final.name, "created_at": started.isoformat(), "reason": reason,
            "sha256": digest, "bytes": size, "counts": counts, "verified": True,
            "server_version": server, "pg_dump": dump_version, "format": "custom",
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        res.ok = True
        res.pruned = prune(directory, retention_days, _now())
        res.pruned += prune_keep(directory, reason, keep)
    except BackupError as exc:
        res.error = _scrub(exc)
    except Exception as exc:  # noqa: BLE001 - reported, never a traceback with a DSN in it
        res.error = _scrub(f"{type(exc).__name__}: {exc}")
    finally:
        if not res.ok:
            # A dump that did not verify is not a backup: do not leave it to be trusted.
            mine = [partial] + ([final, _side(final, ".sha256"), _side(final, ".json")] if wrote else [])
            for p in mine:
                try:
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass
        res.finished_at = _now()
    return res


def verify_existing(target: Path, url: str, *, pg_bin: Optional[str] = None) -> list[tuple[str, bool, str]]:
    """`--verify-only`: every dump in a directory (or one dump), against its manifest's counts.
    Writes no row and no event. Returns (name, ok, message) per dump."""
    target = Path(target)
    if target.is_file():
        dumps = [target]
    else:
        dumps = [p for _, p in backup_files(target)]
    out = []
    for dump in dumps:
        try:
            manifest_path = _side(dump, ".json")
            if not manifest_path.is_file():
                raise BackupError(f"{dump.name}: no manifest {manifest_path.name}")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            restored = verify_dump(dump, manifest.get("counts"), url, pg_bin=pg_bin)
            out.append((dump.name, True, "checksum ok; restored and counted equal ("
                        + ", ".join(f"{k}={v}" for k, v in restored.items()) + ")"))
        except BackupError as exc:
            out.append((dump.name, False, _scrub(exc)))
        except Exception as exc:  # noqa: BLE001
            out.append((dump.name, False, _scrub(f"{dump.name}: {type(exc).__name__}: {exc}")))
    return out


# --------------------------------------------------------------------------- #
# Recording, and what GET /api/crunch reads
# --------------------------------------------------------------------------- #

def label_of(res: "Result") -> str:
    """What the history row and the event name the attempt by: the file NAME only."""
    return res.file if res.ok else f"{res.file} (failed)"


def record(engine: Engine, res: "Result", *, operator: str) -> None:
    """Report one attempt through `crunch.record_backup` (a crunch_history row and one
    `node.crunch.backup` event on atlas-system), in one commit. crunch.py owns both."""
    import crunch
    from sqlalchemy.orm import Session

    with Session(engine) as s:
        crunch.record_backup(s, ok=res.ok, verified=res.verified, label=label_of(res),
                             subject=operator[:200])
        s.commit()


# --------------------------------------------------------------------------- #
# Policy: when does a backup run
# --------------------------------------------------------------------------- #

def crunch_state(engine: Engine) -> Optional[dict]:
    """`crunch.current(s)`: the active crunch, or None (off, not started, expired). The first
    read after expiry records the end in its own session (crunch.py); this one writes nothing."""
    import crunch
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        state = crunch.current(s)
        s.commit()
    return state


def policy_of(state: Optional[dict]) -> Optional[str]:
    if not state:
        return None
    return ((state.get("backup") or {}).get("policy")) or "none"


def retention_of(state: Optional[dict]) -> Optional[int]:
    if not state:
        return None
    value = (state.get("backup") or {}).get("retention_days")
    return int(value) if isinstance(value, int) and value >= 1 else None


def refusal(reason: str, state: Optional[dict]) -> Optional[str]:
    """Why a backup of `reason` must NOT run now, or None when it should. `manual` and
    `routine` (an operator's or a host cron's) always run; the others follow the crunch."""
    if reason in ("manual", "routine"):
        return None
    if not state:
        return f"crunch is off: no {reason} backup (only `manual` runs without a crunch)"
    policy = policy_of(state)
    if policy not in POLICY_OF_REASON[reason]:
        return f"the active crunch's backup policy is {policy!r}, not {reason}: nothing to do"
    return None


def _attempts(engine: Engine, since: Optional[str], reason: str) -> list:
    """This crunch's earlier attempts of `reason`, newest first, as (at, ok): the
    `crunch_history` backup rows since `activated_at`, the reason read off their label."""
    import models_crunch
    from models_crunch import CrunchHistory
    from sqlalchemy.orm import Session

    with Session(engine) as s:
        if not models_crunch.tables_exist(s):
            return []
        q = (select(CrunchHistory.at, CrunchHistory.detail)
             .where(CrunchHistory.action == "backup").order_by(CrunchHistory.id.desc()))
        if since:
            q = q.where(CrunchHistory.at >= datetime.fromisoformat(since))
        out = []
        for at, detail in s.execute(q):
            m = LABEL_REASON_RE.match(str((detail or {}).get("label") or ""))
            if m and m.group(1) == reason:
                out.append((at if at.tzinfo else at.replace(tzinfo=timezone.utc),
                            bool((detail or {}).get("ok"))))
        return out


def due(engine: Engine, state: dict, now: Optional[datetime] = None) -> Optional[str]:
    """For the loop: the reason to run now (`once`/`scheduled`), or None."""
    now = now or _now()
    policy = policy_of(state)
    if policy == "once":
        reason, interval = "once", None
    elif policy in INTERVAL:
        reason, interval = "scheduled", INTERVAL[policy]
    else:
        return None
    attempts = _attempts(engine, state.get("activated_at"), reason)
    good = [at for at, ok in attempts if ok]
    if attempts and not attempts[0][1] and now - attempts[0][0] < RETRY_AFTER:
        return None
    if interval is None:
        return None if good else reason
    if good and now - good[0] < interval:
        return None
    return reason


def run(reason: str, directory: Path, *, operator: str,
        pg_bin: Optional[str] = None, state_fn: Optional[Callable] = None,
        engine: Optional[Engine] = None,
        schedule: Optional["settings.BackupSchedule"] = None) -> tuple[Optional[Result], str]:
    """One policy-checked backup. Returns (Result or None when it did not run, message)."""
    if reason not in REASONS:
        raise BackupError(f"unknown reason {reason!r} (one of {', '.join(REASONS)})")
    url = db_url()
    own = engine is None
    engine = engine or create_engine(url, pool_pre_ping=True)
    try:
        state = (state_fn or crunch_state)(engine)
        why_not = refusal(reason, state)
        if why_not:
            return None, why_not
        keep = None
        if reason == "routine":
            keep = (schedule or settings.load_backup_schedule()).keep
        res = take_backup(url, directory, reason, retention_days=retention_of(state), pg_bin=pg_bin,
                          keep=keep)
        try:
            record(engine, res, operator=operator)
        except Exception as exc:  # noqa: BLE001 - the file exists; say the record failed
            res.ok = False
            res.error = _scrub(f"backup taken but not recorded: {type(exc).__name__}: {exc}")
        return res, res.line()
    finally:
        if own:
            engine.dispose()


def routine_due(engine: Engine, directory: Path, schedule: "settings.BackupSchedule",
                now: Optional[datetime] = None) -> bool:
    """Is a routine backup due? The newest `routine` dump on disk (a dump that exists was
    verified: a failed one is deleted) against the interval; a failed attempt (from the history
    rows) holds the next one back for RETRY_AFTER."""
    if not schedule.enabled:
        return False
    now = now or _now()
    attempts = _attempts(engine, None, "routine")
    if attempts and not attempts[0][1] and now - attempts[0][0] < RETRY_AFTER:
        return False
    mine = [created for created, p in backup_files(directory)
            if NAME_RE.match(p.name).group(2) == "routine"]
    return not mine or now - mine[-1] >= ROUTINE_INTERVAL[schedule.schedule]


def tick(directory: Path, *, pg_bin: Optional[str] = None, state_fn: Optional[Callable] = None,
         engine: Optional[Engine] = None, now: Optional[datetime] = None,
         schedule: Optional["settings.BackupSchedule"] = None) -> tuple[Optional[Result], str]:
    """One pass of the loop: ask crunch, back up when the policy says it is due. Then, only when
    the crunch path took nothing and a routine schedule is set (ATLAS_BACKUP_SCHEDULE), the
    routine backup when it is due. With no schedule the result is exactly the crunch path's."""
    url = db_url()
    own = engine is None
    engine = engine or create_engine(url, pool_pre_ping=True)
    try:
        state = (state_fn or crunch_state)(engine)
        reason = None
        if not state:
            quiet = "crunch is off: nothing to do"
        else:
            reason = due(engine, state, now)
            quiet = f"policy {policy_of(state)!r}: no backup due"
        if reason is None:
            schedule = schedule or settings.load_backup_schedule()
            if not schedule.enabled:
                return None, quiet
            if not routine_due(engine, directory, schedule, now):
                return None, f"{quiet}; routine backup ({schedule.schedule}): not due"
            res = take_backup(url, directory, "routine", pg_bin=pg_bin, keep=schedule.keep)
            try:
                record(engine, res, operator="atlas-backup")
            except Exception as exc:  # noqa: BLE001
                res.ok = False
                res.error = _scrub(f"backup taken but not recorded: {type(exc).__name__}: {exc}")
            return res, res.line()
        res = take_backup(url, directory, reason, retention_days=retention_of(state), pg_bin=pg_bin)
        try:
            record(engine, res, operator="atlas-backup")
        except Exception as exc:  # noqa: BLE001
            res.ok = False
            res.error = _scrub(f"backup taken but not recorded: {type(exc).__name__}: {exc}")
        return res, res.line()
    finally:
        if own:
            engine.dispose()


# --------------------------------------------------------------------------- #
# The loop: `python -m crunch_backup` (compose service atlas-backup, profile `backup`)
# --------------------------------------------------------------------------- #

_STOP = False


def _stop(*_):
    global _STOP
    _STOP = True


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="crunch_backup",
                                description="The crunch backup loop: acts while a crunch with "
                                            "policy hourly, daily or once is active, and on the "
                                            "routine schedule when ATLAS_BACKUP_SCHEDULE sets one.")
    p.add_argument("--dir", default=DEFAULT_DIR, help=f"backup directory (default {DEFAULT_DIR})")
    p.add_argument("--tick", type=int, default=DEFAULT_TICK_S, help="seconds between checks")
    p.add_argument("--pg-bin", default=None, help="directory of pg_dump/pg_restore (default PATH)")
    p.add_argument("--one-tick", action="store_true", help="run one check and exit")
    args = p.parse_args(argv)
    try:
        # Read once, before the first tick: a bad ATLAS_BACKUP_SCHEDULE / ATLAS_BACKUP_KEEP stops
        # the loop at start, naming the variable, instead of failing every minute.
        schedule = settings.load_backup_schedule()
    except settings.SettingsError as exc:
        print(f"{_now().isoformat()} configuration error: {exc}", file=sys.stderr, flush=True)
        return 2
    if schedule.enabled:
        print(f"{_now().isoformat()} routine backups: {schedule.schedule}, keeping the newest "
              f"{schedule.keep} (ATLAS_BACKUP_SCHEDULE, ATLAS_BACKUP_KEEP)", flush=True)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    last_msg = None
    while True:
        try:
            res, msg = tick(Path(args.dir), pg_bin=args.pg_bin, schedule=schedule)
        except Exception as exc:  # noqa: BLE001 - the loop survives; the message is scrubbed
            res, msg = None, _scrub(f"tick failed: {type(exc).__name__}: {exc}")
        if res is not None or msg != last_msg:  # say a quiet state once, not every minute
            print(f"{_now().isoformat()} {msg}", flush=True)
            last_msg = msg
        if args.one_tick:
            return 1 if (res is not None and not res.ok) else 0
        for _ in range(max(1, args.tick)):
            if _STOP:
                return 0
            time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
