"""
Verification for TASK-1479 (ARO 2.1): the schema reconcile and boot seeding hold a Postgres
advisory lock (`main.boot_lock`, key `main.BOOT_LOCK_KEY`), in `main.startup()` and in
`atlas-migrate upgrade`.

Before this task two processes booting against one database at once could both decide a column,
an index or a row was missing; the loser raised and converged only through startup's retry loop.
The task's proof: two boots against one disposable database at the same time both succeed, no
retry is logged, and the schema equals a single boot.

  A. One boot on an empty database: the reference schema and row counts, and no retry logged.
  B. Two boots, made to contend: this script holds the lock, starts both, waits until Postgres
     shows both waiting on it, then lets go. Both succeed, neither retries, the schema and the
     row counts equal one boot's. This is the deterministic half: without it, two boots could
     simply not overlap and pass for the wrong reason.
  C. Two boots released at the same instant, no help from this script: the same assertions.
  D. `atlas-migrate upgrade` and a boot, made to contend the same way: both succeed.
  E. The lock is released when a boot ends, and its key differs from the retrieval reconciler's.

Every boot is a separate process (`import app; app.startup()`), as two replicas would be.

Run:
    python verify_boot_lock.py
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:
    print(f"FATAL: {e}")
    sys.exit(1)

from sqlalchemy import create_engine, text  # noqa: E402

ENGINE = create_engine(DB.url)


def _key(path: Path, name: str) -> int:
    """A module-level `NAME = 0x...` constant, read from source so that this script does not
    import `main` (and with it the gate) into its own process."""
    m = re.search(rf"^{name}\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*$", path.read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit(f"FATAL: {name} not found in {path.name}")
    return int(m.group(1), 0)


BOOT_KEY = _key(HERE / "main.py", "BOOT_LOCK_KEY")
RETRIEVAL_KEY = _key(HERE / "retrieval" / "reconciler.py", "LOCK_KEY")

CHILD_ENV = dict(os.environ)
CHILD_ENV.pop("ATLAS_RESEED", None)
CHILD_ENV["ATLAS_DB_URL"] = DB.url
CHILD_ENV["PYTHONUNBUFFERED"] = "1"
CHILD_ENV.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
CHILD_ENV.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
CHILD_ENV.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")

RETRY = "[atlas] startup: attempt"
WAITING = "[atlas] boot lock: another process is booting or migrating this database; waiting"
BOOT = "import app; app.startup()"
UPGRADE = ["migrate_cli.py", "upgrade"]

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def spawn(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, *args], cwd=HERE, env=CHILD_ENV,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def boot_args(at: float | None = None) -> list[str]:
    """A boot; with `at`, it imports first and then waits for that wall-clock instant, so that
    two boots reach `startup()` together however long their imports took."""
    if at is None:
        return ["-c", BOOT]
    return ["-c", f"import app, time; time.sleep(max(0.0, {at!r} - time.time())); app.startup()"]


def finish(p: subprocess.Popen, name: str) -> tuple[int, str]:
    try:
        out, _ = p.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
        return 124, out + "\n[verify] TIMED OUT"
    if p.returncode != 0:
        print(f"  {name} exited {p.returncode}; last output:")
        print("    " + out[-2500:].replace("\n", "\n    "))
    return p.returncode, out


def reset() -> None:
    with ENGINE.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def schema() -> dict[str, frozenset]:
    """The schema as the Postgres catalogue reports it, one set per kind (as in
    verify_upgrade_command.py)."""
    q = {
        "columns": """SELECT table_name, column_name, data_type, udt_name, is_nullable,
                             coalesce(column_default, ''), coalesce(character_maximum_length, -1)
                      FROM information_schema.columns WHERE table_schema = 'public'""",
        "indexes": """SELECT tablename, indexname, indexdef FROM pg_indexes
                      WHERE schemaname = 'public'""",
        "constraints": """SELECT c.relname, k.conname, k.contype, pg_get_constraintdef(k.oid)
                          FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
                          JOIN pg_namespace n ON n.oid = c.relnamespace
                          WHERE n.nspname = 'public'""",
        "sequences": """SELECT sequencename, data_type::text, start_value, increment_by
                        FROM pg_sequences WHERE schemaname = 'public'""",
    }
    with ENGINE.connect() as conn:
        return {k: frozenset(tuple(r) for r in conn.execute(text(sql))) for k, sql in q.items()}


def schema_diff(a: dict, b: dict) -> str:
    out = []
    for k in a:
        if a[k] != b[k]:
            out.append(f"{k}: only-left={sorted(a[k] - b[k])[:5]} only-right={sorted(b[k] - a[k])[:5]}")
    return "; ".join(out) or "identical"


def row_counts() -> dict[str, int]:
    """Rows per table: a seed that ran twice, or half-ran, shows here."""
    with ENGINE.connect() as conn:
        tables = [r[0] for r in conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"))]
        return {t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in tables}


def waiters() -> int:
    """Sessions waiting for the boot lock right now. A bigint key below 2**32 is reported as
    classid 0, objid = key, objsubid 1."""
    with ENGINE.connect() as conn:
        return conn.execute(text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND classid = 0 "
            "AND objid = :k AND objsubid = 1 AND NOT granted"), {"k": BOOT_KEY}).scalar_one()


def holders() -> int:
    with ENGINE.connect() as conn:
        return conn.execute(text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND classid = 0 "
            "AND objid = :k AND objsubid = 1 AND granted"), {"k": BOOT_KEY}).scalar_one()


def contend(args_a: list[str], args_b: list[str]) -> tuple[tuple[int, str], tuple[int, str], bool]:
    """Hold the boot lock, start both, wait until both wait on it, release, collect.
    Returns both results and whether both were seen waiting at once."""
    gate = ENGINE.connect()
    gate.execute(text("SELECT pg_advisory_lock(:k)"), {"k": BOOT_KEY})
    gate.commit()
    pa, pb = spawn(args_a), spawn(args_b)
    both_waiting = False
    try:
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if waiters() >= 2:
                both_waiting = True
                break
            if pa.poll() is not None or pb.poll() is not None:
                break  # one exited before it ever waited: reported below
            time.sleep(0.2)
    finally:
        gate.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": BOOT_KEY})
        gate.commit()
        gate.close()
    return finish(pa, "first"), finish(pb, "second"), both_waiting


# --------------------------------------------------------------------------- #
# A. one boot: the reference
# --------------------------------------------------------------------------- #
print("=" * 78)
print("A. one boot on an empty database (the reference)")
print("=" * 78)
reset()
rc, out = finish(spawn(boot_args()), "single boot")
check("a single boot exits 0", rc == 0)
check("a single boot logs no retry", RETRY not in out)
check("a single boot does not wait for the lock (uncontended)", WAITING not in out)
S_ONE, ROWS_ONE = schema(), row_counts()
check("a single boot built a schema and seeded rows",
      bool(S_ONE["columns"]) and sum(ROWS_ONE.values()) > 0,
      f"{len({r[0] for r in S_ONE['columns']})} tables, {sum(ROWS_ONE.values())} rows")

# --------------------------------------------------------------------------- #
# B. two boots, made to contend
# --------------------------------------------------------------------------- #
print("=" * 78)
print("B. two boots on an empty database, both waiting on the lock, then released together")
print("=" * 78)
reset()
(rc1, out1), (rc2, out2), seen = contend(boot_args(), boot_args())
check("both boots were waiting on the boot lock at the same moment", seen)
check("both boots exit 0", rc1 == 0 and rc2 == 0, f"exit codes {rc1}, {rc2}")
check("neither boot logs a retry", RETRY not in out1 and RETRY not in out2,
      "; ".join(l for l in (out1 + out2).splitlines() if RETRY in l)[:300])
check("both boots logged that they waited", WAITING in out1 and WAITING in out2)
check("the schema equals a single boot's", schema() == S_ONE, schema_diff(S_ONE, schema()))
check("the rows per table equal a single boot's (nothing seeded twice)", row_counts() == ROWS_ONE,
      str({t: (ROWS_ONE.get(t), n) for t, n in row_counts().items() if ROWS_ONE.get(t) != n}))

# --------------------------------------------------------------------------- #
# C. two boots released at the same instant
# --------------------------------------------------------------------------- #
print("=" * 78)
print("C. two boots on an empty database, started at the same instant")
print("=" * 78)
reset()
at = time.time() + 20  # both import first (several seconds each), then start together
pa, pb = spawn(boot_args(at)), spawn(boot_args(at))
(rc1, out1), (rc2, out2) = finish(pa, "first"), finish(pb, "second")
check("both boots exit 0", rc1 == 0 and rc2 == 0, f"exit codes {rc1}, {rc2}")
check("neither boot logs a retry", RETRY not in out1 and RETRY not in out2,
      "; ".join(l for l in (out1 + out2).splitlines() if RETRY in l)[:300])
print(f"  (one waited for the other: {WAITING in out1 or WAITING in out2})")
check("the schema equals a single boot's", schema() == S_ONE, schema_diff(S_ONE, schema()))
check("the rows per table equal a single boot's", row_counts() == ROWS_ONE)

# --------------------------------------------------------------------------- #
# D. atlas-migrate upgrade and a boot, made to contend
# --------------------------------------------------------------------------- #
print("=" * 78)
print("D. `atlas-migrate upgrade` and a boot on an empty database, both waiting, then released")
print("=" * 78)
reset()
(rc1, out1), (rc2, out2), seen = contend(UPGRADE, boot_args())
check("the upgrade and the boot were both waiting on the boot lock", seen)
check("the upgrade exits 0", rc1 == 0, out1.strip().splitlines()[-1] if out1.strip() else "")
check("the boot exits 0 and logs no retry", rc2 == 0 and RETRY not in out2)
check("the upgrade logged that it waited", WAITING in out1)
check("the schema equals a single boot's", schema() == S_ONE, schema_diff(S_ONE, schema()))

# --------------------------------------------------------------------------- #
# E. release, and the key
# --------------------------------------------------------------------------- #
print("=" * 78)
print("E. the lock is released after a boot; its key is its own")
print("=" * 78)
probe = ("import app; app.startup()\n"
         "from db import engine\n"
         "from sqlalchemy import text\n"
         "with engine.connect() as c:\n"
         "    n = c.execute(text(\"SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' \"\n"
         "                       \"AND classid = 0 AND objid = :k AND objsubid = 1\"), {'k': %d}).scalar_one()\n"
         "print('BOOT_LOCKS_AFTER_STARTUP=%%d' %% n)\n") % BOOT_KEY
rc, out = finish(spawn(["-c", probe]), "probe")
check("after startup() returns, in the same live process, no session holds or waits on the boot lock",
      rc == 0 and "BOOT_LOCKS_AFTER_STARTUP=0" in out,
      next((l for l in out.splitlines() if l.startswith("BOOT_LOCKS_AFTER_STARTUP=")), "no probe line"))
check("nothing holds the boot lock once every process has exited", holders() == 0)
check("the boot lock key differs from the retrieval reconciler's",
      BOOT_KEY != RETRIEVAL_KEY, f"boot {BOOT_KEY:#x}, retrieval {RETRIEVAL_KEY:#x}")

# --------------------------------------------------------------------------- #
ENGINE.dispose()
print("=" * 78)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASSED")
