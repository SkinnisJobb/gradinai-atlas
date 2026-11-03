"""
Verification for STORY-oss-09: `atlas-migrate upgrade` (`migrate_cli.py`).

The command is a wrapper around today's schema path, not a second one (full Alembic is deferred
by owner decision, 2026-10-14). These are the four decided criteria, and the section that
proves each:

  1. On an EMPTY database it produces the same schema as application startup.       -> B
  2. Run twice, the second run changes nothing (schema or data).                     -> A
  3. On a database built by today's startup, with data in it, it changes no data.    -> C
  4. Startup's schema path is unchanged, and the command calls that same path.       -> E
     (plus D: the case the command exists for, a populated database missing a column
     and an index, is brought to the startup schema without touching the rows it had)
  F. On an OSS install with SSO on (TASK-1457): the command runs with the OIDC settings set and
     their files absent, as in the atlas-migrate container, while `import main` still refuses.

The command is run the way an operator runs it, as a separate process
(`python migrate_cli.py upgrade`), and startup is run as a separate process too
(`import app; app.startup()`), so no module state leaks from one into the other. The schema is
read from the Postgres catalogue (columns, indexes, constraints, sequences), and the data as a
digest of every row of every table plus every sequence position.

Run:
    python verify_upgrade_command.py
"""
from __future__ import annotations

import ast
import base64
import os
import re
import subprocess
import sys
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

# The environment the child processes boot with: the same throwaway peppers every other verify
# script mints, as plain env vars (settings.read_secret accepts those by default).
CHILD_ENV = dict(os.environ)
CHILD_ENV.pop("ATLAS_RESEED", None)
CHILD_ENV["ATLAS_DB_URL"] = DB.url
CHILD_ENV.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
CHILD_ENV.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
CHILD_ENV.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")

failures: list[str] = []
UP_TO_DATE = "[atlas-migrate] upgrade: schema is up to date"


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def run_child(args: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run([sys.executable, *args], cwd=HERE, env=CHILD_ENV,
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        print(f"  child {args!r} exited {proc.returncode}")
        print("  stdout: " + proc.stdout[-2000:].replace("\n", "\n  "))
        print("  stderr: " + proc.stderr[-2000:].replace("\n", "\n  "))
    return proc


def upgrade() -> subprocess.CompletedProcess:
    return run_child(["migrate_cli.py", "upgrade"])


def startup() -> subprocess.CompletedProcess:
    return run_child(["-c", "import app; app.startup()"])


def seed_data() -> subprocess.CompletedProcess:
    return run_child(["-c", (
        "import app, testseed\n"
        "from db import session\n"
        "with session() as s:\n"
        "    testseed.build(s)\n"
        "    s.commit()\n"
    )])


def reset() -> None:
    with ENGINE.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def schema() -> dict[str, frozenset]:
    """The schema as the Postgres catalogue reports it, one set per kind."""
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


def data(exclude: dict[str, set[str]] | None = None) -> dict[str, tuple[int, str]]:
    """(row count, md5 of every row as canonical jsonb) per table, plus every sequence's
    position. `exclude` drops named columns from a table's rows before hashing."""
    exclude = exclude or {}
    out: dict[str, tuple[int, str]] = {}
    with ENGINE.connect() as conn:
        tables = [r[0] for r in conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"))]
        for t in tables:
            row = "to_jsonb(t)" + "".join(f" - '{c}'" for c in sorted(exclude.get(t, ())))
            n, digest = conn.execute(text(
                f'SELECT count(*), coalesce(md5(string_agg(r, E\'\\n\' ORDER BY r)), \'\') '
                f'FROM (SELECT ({row})::text AS r FROM "{t}" t) x')).one()
            out[t] = (n, digest)
        for name, last in conn.execute(text(
                "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public'")):
            out[f"sequence:{name}"] = (-1, str(last))
    return out


def total_rows(d: dict) -> int:
    return sum(n for k, (n, _) in d.items() if not k.startswith("sequence:"))


# --------------------------------------------------------------------------- #
# A. empty database, twice
# --------------------------------------------------------------------------- #
print("=" * 78)
print("A. `atlas-migrate upgrade` on an EMPTY database, then again")
print("=" * 78)
reset()
check("the database starts with no tables", not schema()["columns"])
p1 = upgrade()
check("first upgrade exits 0", p1.returncode == 0, p1.stdout.strip().splitlines()[-1] if p1.stdout else "")
s_up1, d_up1 = schema(), data()
tables_up = {r[0] for r in s_up1["columns"]}
check("first upgrade created tables", len(tables_up) > 0, f"{len(tables_up)} tables")
check("first upgrade wrote no rows (schema only, no seed, no registry)", total_rows(d_up1) == 0,
      f"{total_rows(d_up1)} rows")
p2 = upgrade()
check("second upgrade exits 0", p2.returncode == 0)
s_up2, d_up2 = schema(), data()
check("criterion 2: second run changes no schema", s_up2 == s_up1, schema_diff(s_up1, s_up2))
check("criterion 2: second run changes no data", d_up2 == d_up1)
check("second run reports nothing added by the reconcile",
      "schema reconcile: added" not in p2.stdout, p2.stdout.strip()[:200])


def last_line(p) -> str:
    lines = p.stdout.strip().splitlines()
    return lines[-1] if lines else ""


# TASK-1470: the two runs used to print the same line, so a log could not tell a first build
# from a re-run. The re-run's line is unchanged; install checks grep for it.
check("the first run says it created the schema on an empty database",
      last_line(p1).startswith("[atlas-migrate] upgrade: empty database, schema created (")
      and f"({len(tables_up)} tables," in last_line(p1), last_line(p1))
check("the second run's line is unchanged: schema is up to date",
      last_line(p2) == UP_TO_DATE, last_line(p2))

# --------------------------------------------------------------------------- #
# B. the same schema as application startup
# --------------------------------------------------------------------------- #
print("=" * 78)
print("B. upgrade's schema == the schema application startup builds on an empty database")
print("=" * 78)
reset()
ps = startup()
check("startup on an empty database exits 0", ps.returncode == 0)
s_boot = schema()
check("criterion 1: upgrade on an empty DB == startup on an empty DB", s_up1 == s_boot,
      schema_diff(s_up1, s_boot))
for kind in ("columns", "indexes", "constraints", "sequences"):
    print(f"  {kind}: {len(s_boot[kind])}")
check("the comparison covers the full-text GIN indexes startup creates",
      any(r[1] == "ix_node_body_search_gin" for r in s_boot["indexes"]))
check("the comparison covers the task id sequence", any(r[0] == "task_id_seq" for r in s_boot["sequences"]))

# A check that cannot fail proves nothing: perturb the schema by one index and require the
# comparison to notice, then put it back.
with ENGINE.begin() as conn:
    conn.execute(text("CREATE INDEX ix_upgrade_probe ON node (name)"))
check("the schema comparison notices one extra index", schema() != s_boot)
with ENGINE.begin() as conn:
    conn.execute(text("DROP INDEX ix_upgrade_probe"))
check("and is equal again once it is dropped", schema() == s_boot)

# --------------------------------------------------------------------------- #
# C. a database built by today's startup, with data
# --------------------------------------------------------------------------- #
print("=" * 78)
print("C. upgrade on a database built by startup and holding data changes no data")
print("=" * 78)
pd = seed_data()
check("fixture data loaded through the real models", pd.returncode == 0)
d_before, s_before = data(), schema()
check("the database is populated (so 'no change' is not vacuous)", total_rows(d_before) > 0,
      f"{total_rows(d_before)} rows across {sum(1 for k in d_before if not k.startswith('sequence:'))} tables")
pc1 = upgrade()
check("upgrade on the populated database exits 0", pc1.returncode == 0)
check("criterion 3: no row changed, none added or removed, no sequence moved", data() == d_before)
check("and the schema is unchanged (startup had already built all of it)", schema() == s_before,
      schema_diff(s_before, schema()))
pc2 = upgrade()
check("criterion 2 on the populated database: a second run changes no data either",
      pc2.returncode == 0 and data() == d_before)
check("on a database startup built, upgrade says the schema is up to date",
      last_line(pc1) == UP_TO_DATE and last_line(pc2) == UP_TO_DATE, last_line(pc1))

# The data digest must be able to fail too.
with ENGINE.begin() as conn:
    conn.execute(text("UPDATE node SET name = name || 'x' WHERE id = (SELECT min(id) FROM node)"))
check("the data comparison notices one changed character in one row", data() != d_before)
with ENGINE.begin() as conn:
    conn.execute(text("UPDATE node SET name = left(name, -1) WHERE id = (SELECT min(id) FROM node)"))
check("and is equal again once it is reverted", data() == d_before)

# --------------------------------------------------------------------------- #
# D. the case the command exists for: a populated DB that predates a column and an index
# --------------------------------------------------------------------------- #
print("=" * 78)
print("D. a populated database missing a nullable column and an index is brought up to date")
print("=" * 78)
with ENGINE.begin() as conn:
    conn.execute(text("ALTER TABLE node_type DROP COLUMN agent_instructions"))
    conn.execute(text("DROP INDEX ix_node_completed_at"))
d_old = data()
check("the simulated older schema differs from startup's", schema() != s_boot)
pd1 = upgrade()
check("upgrade exits 0", pd1.returncode == 0)
check("the reconcile reported the column and the index it added",
      "added column node_type.agent_instructions" in pd1.stdout
      and "added index node.ix_node_completed_at" in pd1.stdout, pd1.stdout.strip()[:300])
check("its report line names what it created (1 column, 1 index), not 'up to date'",
      last_line(pd1) == "[atlas-migrate] upgrade: created what was missing (0 tables, 1 column, "
                        "1 index); schema is now up to date", last_line(pd1))
check("the schema now equals the one startup builds", schema() == s_boot, schema_diff(s_boot, schema()))
check("every pre-existing row is unchanged (the new column ignored)",
      data(exclude={"node_type": {"agent_instructions"}}) == d_old)
with ENGINE.connect() as conn:
    nonnull = conn.execute(text("SELECT count(*) FROM node_type WHERE agent_instructions IS NOT NULL")).scalar()
check("the added column is NULL on every existing row (nullable, no default)", nonnull == 0)

# --------------------------------------------------------------------------- #
# E. startup's schema path is unchanged, and the command calls that same path
# --------------------------------------------------------------------------- #
print("=" * 78)
print("E. startup is unchanged, and upgrade runs the same schema calls in the same order")
print("=" * 78)


def _calls(body: list[ast.stmt]) -> list[str]:
    return [ast.unparse(st.value) for st in body
            if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call)]


def _func(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


boot = _func(HERE / "main.py", "startup")
boot_try = next(n for n in ast.walk(boot) if isinstance(n, ast.Try))
boot_calls = _calls(boot_try.body)
# Startup's schema steps are the leading calls of its retry body, up to the first one that is
# not schema: `_backfill_graph_origin()` writes rows (NULL -> 'git'), and the `print` after it is
# where the gate check and the row seeding begin. A new schema step added to startup lands in
# this prefix, and then the comparison below fails until upgrade gets it too.
ROW_WRITERS = {"_backfill_graph_origin()"}
boot_schema: list[str] = []
for c in boot_calls:
    if c in ROW_WRITERS:
        continue
    if c.startswith("print("):
        break
    boot_schema.append(c)
up_calls = [c.removeprefix("main.") for c in _calls(_func(HERE / "migrate_cli.py", "upgrade").body)]
print(f"  startup schema steps: {boot_schema}")
print(f"  upgrade steps:        {up_calls}")
check("criterion 4: upgrade makes exactly startup's schema calls, in startup's order",
      up_calls == boot_schema)
check("startup still makes all five schema calls itself",
      boot_schema == ["Base.metadata.create_all(engine)",
                      "models_graph.Base.metadata.create_all(engine)",
                      "models_auth.create_all(engine)",
                      "_reconcile_graph_schema()",
                      "models_graph.ensure_search_indexes(engine)"], str(boot_schema))
def _non_docstring_strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef)) and n.body and isinstance(n.body[0], ast.Expr) \
                and isinstance(n.body[0].value, ast.Constant):
            docs.add(id(n.body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


ddl = [s for s in _non_docstring_strings(HERE / "migrate_cli.py")
       if re.search(r"\b(ALTER|CREATE|DROP)\s+(TABLE|INDEX|SEQUENCE|SCHEMA)\b", s, re.I)]
check("migrate_cli.py emits no DDL of its own (no ALTER/CREATE/DROP in its code)", not ddl, str(ddl))

reset()
pe1, pe2 = upgrade(), startup()
check("startup on a database upgrade built boots and leaves the schema as startup builds it",
      pe1.returncode == 0 and pe2.returncode == 0 and schema() == s_boot, schema_diff(s_boot, schema()))

dockerfile = (HERE / "Dockerfile").read_text(encoding="utf-8")
check("the image puts `atlas-migrate` on PATH, calling migrate_cli.py",
      "/usr/local/bin/atlas-migrate" in dockerfile and "/app/migrate_cli.py" in dockerfile)
check("the image's CMD is still uvicorn app:app",
      'CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]' in dockerfile)
ph = run_child(["migrate_cli.py", "--help"])
check("`atlas-migrate --help` works", ph.returncode == 0 and "upgrade" in ph.stdout)
pu = run_child(["migrate_cli.py", "frobnicate"])
check("an unknown command is a usage error (exit 2), not a silent success", pu.returncode == 2)

# --------------------------------------------------------------------------- #
# F. On an SSO install (TASK-1457): atlas-migrate reads the OSS `.env` (env_file), so it has the
# sign-in block, but the OIDC files it names are mounted into atlas-api only (deploy/oss/oidc.yml).
# The values are valid ones; only the files are absent, as in the atlas-migrate container.
# --------------------------------------------------------------------------- #
print("=" * 78)
print("F. `atlas-migrate upgrade` with the OIDC settings set and their files absent")
print("=" * 78)
_missing = HERE / ".verify-upgrade-not-mounted"   # never created
OIDC_ENV = {"ATLAS_AUTH_PROVIDERS": "oidc", "ATLAS_LOCAL_LOGIN": "break-glass",
            "ATLAS_OIDC_ISSUER": "https://idp.invalid/atlas", "ATLAS_OIDC_CLIENT_ID": "atlas",
            "ATLAS_OIDC_REDIRECT_URI": "https://testserver/auth/oidc/callback",
            "ATLAS_OIDC_CLIENT_SECRET_FILE": str(_missing / "oidc_client_secret"),
            "ATLAS_OIDC_GROUP_MAP_FILE": str(_missing / "oidc_group_map"),
            "ATLAS_OIDC_REQUIRED_GROUPS": "atlas-users"}
_saved_env = dict(CHILD_ENV)
CHILD_ENV.update(OIDC_ENV)
try:
    # The trigger, kept visible: anything else that imports the gate in this environment still
    # stops at import, naming the unreadable group map. Only the migrate command is exempt.
    pf0 = subprocess.run([sys.executable, "-c", "import main"], cwd=HERE, env=CHILD_ENV,
                         capture_output=True, text=True, timeout=300)
    check("`import main` with the OIDC files absent: still refused, naming ATLAS_OIDC_GROUP_MAP_FILE",
          pf0.returncode != 0 and "ATLAS_OIDC_GROUP_MAP_FILE" in pf0.stderr, pf0.stderr.strip()[-200:])
    reset()
    pf1 = upgrade()
    check("`atlas-migrate upgrade` on an empty database: exit 0 without the OIDC files",
          pf1.returncode == 0 and "empty database, schema created" in pf1.stdout, pf1.stderr.strip()[-300:])
    check("... and builds the schema startup builds", schema() == s_boot, schema_diff(s_boot, schema()))
    pf2 = upgrade()
    check("`atlas-migrate upgrade` again (the re-run on an upgrade or a re-create): exit 0",
          pf2.returncode == 0, pf2.stderr.strip()[-300:])
finally:
    CHILD_ENV.clear()
    CHILD_ENV.update(_saved_env)

# --------------------------------------------------------------------------- #
ENGINE.dispose()
print("=" * 78)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASSED")
