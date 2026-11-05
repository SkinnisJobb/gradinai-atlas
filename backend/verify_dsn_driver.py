"""
Verification for TASK-1501 (ARO 2.3): a database URL that names no driver gets psycopg2.

SQLAlchemy 2.1 resolves `postgresql://...` to psycopg 3, which the image does not ship, and
`postgres://...` to no dialect at all. CloudNativePG's `fqdn-uri` is such a URL. `dsn.py` maps
both to `postgresql+psycopg2`, once, wherever `ATLAS_DB_URL(_FILE)` is read.

  A. `postgresql://` and `postgres://` come back as `postgresql+psycopg2://`, and the engine built
     from them uses the psycopg2 dialect.
  B. A URL that names a driver comes back as the same string: `+psycopg2` (every working install
     today), and any other driver.
  C. Nothing else in the URL changes: role, a password full of URL-special characters, host, port,
     database and every query parameter (`sslmode`, `sslrootcert`, ...).
  D. A URL that does not parse fails with a message that holds neither the URL nor the password.
  E. End to end against the disposable test database, as separate processes the way the services
     run: `import db` with a bare `postgresql://` URL (in ATLAS_DB_URL, and as a `postgres://` URL
     in ATLAS_DB_URL_FILE) connects through psycopg2 as a role with a random password, and that
     password appears in no output. `db.py` refusing an unparsable URL leaks nothing either.
  F. The other readers: `crunch_backup.db_url()` (backups, and `backup_restore.py` through it)
     returns the psycopg2 form while the pg_dump conninfo carries no password and no driver;
     `admin._db_url()` the same; a one-off `migrate_*.py` given an unparsable `--db-url` exits 2
     without printing it.

Run:
    python verify_dsn_driver.py
"""
from __future__ import annotations

import base64
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:
    print(f"FATAL: {e}")
    sys.exit(1)

from psycopg2 import sql  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

import dsn  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


# A password that only this run knows, and one with every character a URL gives a meaning to.
SECRET_PW = "s3cr3t-" + secrets.token_hex(12)
SPECIAL_PW = "p@ss:w/rd?#%&=+ " + secrets.token_hex(4)
SPECIAL_PW_ENC = "".join(c if c.isalnum() or c in "-._~" else "%%%02X" % ord(c) for c in SPECIAL_PW)


def section_a() -> None:
    print("\n== A. a URL with no driver gets psycopg2")
    for scheme in ("postgresql", "postgres"):
        url = f"{scheme}://atlas:{SECRET_PW}@db.example.com:5432/atlas"
        out = dsn.with_default_driver(url)
        u = make_url(out)
        check(f"A {scheme}:// -> drivername postgresql+psycopg2", u.drivername == "postgresql+psycopg2",
              u.drivername)
        engine = create_engine(out)  # builds the dialect; connects to nothing
        check(f"A {scheme}:// -> the engine's dialect is postgresql/psycopg2",
              (engine.dialect.name, engine.dialect.driver) == ("postgresql", "psycopg2"),
              f"{engine.dialect.name}/{engine.dialect.driver}")
        engine.dispose()
    # Context, not an assertion of SQLAlchemy's own default: what a bare URL resolved to before.
    try:
        bare = make_url("postgresql://u@h/d").get_dialect().driver
    except Exception as exc:  # noqa: BLE001
        bare = f"(unresolvable: {type(exc).__name__})"
    print(f"  (for reference: this SQLAlchemy resolves a bare postgresql:// to driver {bare!r})")


def section_b() -> None:
    print("\n== B. a URL that names a driver is returned unchanged")
    for url in (
        f"postgresql+psycopg2://atlas:{SECRET_PW}@atlas-postgres:5432/atlas",
        f"postgresql+psycopg2://atlas:{SPECIAL_PW_ENC}@db.example.com/atlas?sslmode=verify-full",
        f"postgresql+psycopg://atlas:{SECRET_PW}@db.example.com/atlas",
        f"postgresql+asyncpg://atlas:{SECRET_PW}@db.example.com/atlas",
        f"postgresql+pg8000://atlas:{SECRET_PW}@db.example.com/atlas",
        "sqlite:///tmp/x.db",
    ):
        out = dsn.with_default_driver(url)
        check(f"B {make_url(url).drivername} unchanged (the same string)", out == url)


def section_c() -> None:
    print("\n== C. every other part of the URL is carried over")
    q = "sslmode=verify-full&sslrootcert=/run/secrets/db-ca/ca.crt&application_name=atlas&options=-c%20x%3D1"
    url = f"postgresql://atlas_app:{SPECIAL_PW_ENC}@db.example.com:6543/atlas_db?{q}"
    before, after = make_url(url), make_url(dsn.with_default_driver(url))
    for part in ("username", "password", "host", "port", "database", "query"):
        check(f"C {part} unchanged", getattr(before, part) == getattr(after, part))
    check("C the special-character password survives the round trip", after.password == SPECIAL_PW)
    # A URL with no password, no port and no host (a Unix socket) is still fine.
    for url in ("postgresql:///atlas", "postgres://atlas@/atlas?host=/var/run/postgresql"):
        after = make_url(dsn.with_default_driver(url))
        check(f"C {url} -> psycopg2, rest unchanged",
              after.drivername == "postgresql+psycopg2"
              and after.set(drivername="x") == make_url(url).set(drivername="x"))


def section_d() -> None:
    print("\n== D. an unparsable URL fails without showing it")
    for bad in (f"atlas:{SECRET_PW}@db.example.com/atlas", f"not a url {SECRET_PW}",
                f"postgresql://atlas:{SECRET_PW}@db.example.com:notaport/atlas"):
        try:
            dsn.with_default_driver(bad)
            raised, msg = False, ""
        except dsn.DsnError as exc:
            raised, msg = True, str(exc) + repr(exc.__cause__) + repr(exc.__context__)
        check("D DsnError raised", raised)
        check("D the message holds neither the URL nor the password",
              SECRET_PW not in msg and bad not in msg)


def _child(code: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for k in ("ATLAS_DB_URL", "ATLAS_DB_URL_FILE", "ATLAS_REQUIRE_SECRET_FILES"):
        env.pop(k, None)
    env.update(env_extra)
    env["PYTHONUNBUFFERED"] = "1"
    return subprocess.run([sys.executable, "-c", code], cwd=str(HERE), env=env,
                          capture_output=True, text=True, timeout=120)


def _role_url(scheme: str, role: str) -> str:
    u = make_url(DB.url)
    return f"{scheme}://{role}:{SECRET_PW}@{u.host}:{u.port}/{u.database}"


CONNECT = (
    "import db\n"
    "from sqlalchemy import text\n"
    "with db.engine.connect() as c:\n"
    "    who = c.execute(text('select current_user')).scalar()\n"
    "print('DIALECT', db.engine.dialect.name, db.engine.dialect.driver)\n"
    "print('USER', who)\n"
)


def _admin_sql(engine, statement: "sql.Composed") -> None:
    """DDL with a quoted identifier and literal (CREATE ROLE takes no bind parameters)."""
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(statement)
    finally:
        raw.close()


def section_e() -> None:
    print("\n== E. end to end: db.py connects with a bare URL, through psycopg2")
    role = "dsn_" + secrets.token_hex(4)
    admin = create_engine(DB.url, isolation_level="AUTOCOMMIT")
    _admin_sql(admin, sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
        sql.Identifier(role), sql.Literal(SECRET_PW)))
    try:
        r = _child(CONNECT, {"ATLAS_DB_URL": _role_url("postgresql", role)})
        out = r.stdout + r.stderr
        check("E postgresql:// in ATLAS_DB_URL: import db connects", r.returncode == 0,
              "" if r.returncode == 0 else out[-600:].replace(SECRET_PW, "***"))
        check("E ... with the psycopg2 dialect", "DIALECT postgresql psycopg2" in r.stdout)
        check("E ... as the role whose password is in the URL", f"USER {role}" in r.stdout)
        check("E ... and the password appears in no output", SECRET_PW not in out)

        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "db_url"
            f.write_text(_role_url("postgres", role) + "\n", encoding="utf-8")
            r = _child(CONNECT, {"ATLAS_DB_URL_FILE": str(f), "ATLAS_REQUIRE_SECRET_FILES": "1"})
        out = r.stdout + r.stderr
        check("E postgres:// in ATLAS_DB_URL_FILE (strict mode): import db connects", r.returncode == 0,
              "" if r.returncode == 0 else out[-600:].replace(SECRET_PW, "***"))
        check("E ... with the psycopg2 dialect", "DIALECT postgresql psycopg2" in r.stdout)
        check("E ... and the password appears in no output", SECRET_PW not in out)

        r = _child("import db", {"ATLAS_DB_URL": f"atlas:{SECRET_PW}@nowhere/atlas"})
        out = r.stdout + r.stderr
        check("E an unparsable ATLAS_DB_URL fails import db", r.returncode != 0)
        check("E ... naming the variable", "ATLAS_DB_URL" in out)
        check("E ... without the password", SECRET_PW not in out)
    finally:
        _admin_sql(admin, sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
        admin.dispose()


def section_f() -> None:
    print("\n== F. the other readers of ATLAS_DB_URL")
    bare = f"postgresql://atlas:{SECRET_PW}@db.example.com:5432/atlas?sslmode=verify-full"
    code = (
        "import crunch_backup as cb\n"
        "u = cb.db_url()\n"
        "print('URL', u.startswith('postgresql+psycopg2://'))\n"
        "ci = cb._conninfo(u)\n"
        "print('CONNINFO_PW', " + repr(SECRET_PW) + " in ci)\n"
        "print('CONNINFO_DRIVER', 'psycopg2' in ci)\n"
        "print('SCRUB', cb._scrub('x ' + u + ' y').count('***') >= 1 and "
        + repr(SECRET_PW) + " not in cb._scrub('x ' + u + ' ' + " + repr(bare) + "))\n"
    )
    r = _child(code, {"ATLAS_DB_URL": bare})
    out = r.stdout + r.stderr
    check("F crunch_backup.db_url() runs", r.returncode == 0,
          "" if r.returncode == 0 else out[-600:].replace(SECRET_PW, "***"))
    check("F crunch_backup.db_url() is the psycopg2 form", "URL True" in r.stdout)
    check("F the pg_dump conninfo has no password", "CONNINFO_PW False" in r.stdout)
    check("F the pg_dump conninfo has no driver name", "CONNINFO_DRIVER False" in r.stdout)
    check("F both forms of the URL are scrubbed", "SCRUB True" in r.stdout)
    check("F ... and the password appears in no output", SECRET_PW not in out)

    peppers = {k: base64.b64encode(os.urandom(32)).decode()
               for k in ("ATLAS_PASSWORD_PEPPER", "ATLAS_TOKEN_PEPPER")}  # admin imports the gate
    r = _child("import admin\nprint('URL', admin._db_url().startswith('postgresql+psycopg2://'))",
               {"ATLAS_DB_URL": bare, "ATLAS_LOGIN_ORIGINS": "https://testserver", **peppers})
    out = r.stdout + r.stderr
    check("F admin._db_url() is the psycopg2 form", r.returncode == 0 and "URL True" in r.stdout,
          "" if r.returncode == 0 else out[-600:].replace(SECRET_PW, "***"))
    check("F ... and the password appears in no output", SECRET_PW not in out)

    bad = f"atlas:{SECRET_PW}@nowhere/atlas"
    r = subprocess.run([sys.executable, "migrate_strip_frontmatter_raw.py", "--db-url", bad],
                       cwd=str(HERE), capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    check("F a migrate_*.py given an unparsable --db-url exits 2", r.returncode == 2, str(r.returncode))
    check("F ... without printing the password", SECRET_PW not in out)


def main() -> None:
    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    section_f()
    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL PASS: verify_dsn_driver")


if __name__ == "__main__":
    main()
