"""
Verification for the crunch-week backups (STORY-atlas-crunch-mode, `crunch_backup.py`,
`atlas-admin backup run`, the OSS compose `backup` profile).

  A. The image and the compose file: backend/Dockerfile's `backup` stage carries the Postgres
     client and is not the last stage (the homelab's default image is unchanged); the
     `atlas-backup` service is in profile `backup` only, builds that stage, is on `data`
     only, publishes nothing; no homelab compose file names it.
  B. A backup is taken and verified: `atlas-admin backup run` on a homelab-shaped database
     writes the dump, its sha256 and a manifest whose counts equal the source's node, edge
     and graph_event; reported through `crunch.record_backup`: one `crunch_history` backup row
     and one `node.crunch.backup` graph_event on atlas-system, labelled with the file NAME;
     `crunch.last_backup()` returns it; no temporary database is left behind; a perturbed
     expectation (one count off) is a RESTORE MISMATCH.
  C. A corrupted dump is detected, loudly: a flipped byte fails the checksum, a truncated
     dump with a matching checksum fails the restore; both exit non-zero naming why; the
     intact copy passes `--verify-only`; an empty directory is a failure, not a pass.
  D. Retention deletes only older files of this module's pattern, never another file, never
     after a failed backup, and nothing when retention is unset.
  E. Crunch off, or a policy that is not the caller's (none, per_deploy for the loop, ...):
     no backup, no file, no table, and the database byte-identical; with a policy on, the
     loop backs up when due (hourly: at activation, then not again within the hour; once:
     exactly once) and backs off after a failure.
  G. Restore (OSS-26 C, `atlas-admin backup restore` / `rollback`, backup_restore.py): a backup,
     then data changed and deleted. A flipped byte and a missing manifest are refused before any
     database is touched; --dry-run restores into staging, counts, and changes nothing; with
     another session connected a dry run says a real one would refuse and a real one does; a
     failing pg_restore leaves the live database untouched and drops the staging one; the real
     restore makes the database byte-identical to the moment of the backup (identical counts to
     the manifest), keeps the old one as a safety copy, and rollback puts that back.
  H. Routine backups (OSS-26 C, ATLAS_BACKUP_SCHEDULE / ATLAS_BACKUP_KEEP): unset, the loop's
     answers are the crunch path's exactly; set, a `routine` backup when due, not before;
     KEEP prunes only routine dumps; a due crunch backup wins the tick; a failed attempt backs
     off; `backup run --reason routine` keeps KEEP; a bad value stops the loop at start.
  I. SSO on (OSS-26 L): with .env's OIDC sign-in settings set and the files they name absent
     (what the `atlas-backup` container sees), `backup run`, `--verify-only`, `restore
     --dry-run` and `rollback` succeed, while any other verb is still refused at import
     naming the unreadable group map (admin.py drops the identity settings for `backup` only).
  F. No credential anywhere (run last, over every section's output): the DB URL is given ONLY
     as ATLAS_DB_URL_FILE with a role whose password is random, and neither the password nor
     the URL appears in any output, file, row or event.

The Postgres client tools run as the stack runs them, but from the test Postgres image: the
runner may have no pg_dump, so `--pg-bin` points at wrappers that `docker run` the same
postgres:16-alpine testdb uses, on the host network, streaming the files (see WRAPPER).

Run:
    python verify_crunch_backup.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:
    print(f"FATAL: {e}")
    sys.exit(1)

import yaml  # noqa: E402
from sqlalchemy import (Text, cast, create_engine, func, literal_column,  # noqa: E402
                        select, table, text)
from sqlalchemy.engine import make_url  # noqa: E402

ENGINE = create_engine(DB.url)
TMP = Path(tempfile.mkdtemp(prefix="verify-crunch-backup-"))
(TMP / "tmp").mkdir()
BIN = TMP / "bin"
BIN.mkdir()

# A role of our own with a random password, so "no credential in the output" is a real test
# (the test database's own password is a dictionary word that occurs in its name).
ROLE = "atlas_bk_" + secrets.token_hex(4)
ROLE_PW = "pw" + secrets.token_hex(16)
_u = make_url(DB.url)
ROLE_URL = _u.set(username=ROLE, password=ROLE_PW).render_as_string(hide_password=False)
URL_FILE = TMP / "db_url"
URL_FILE.write_text(ROLE_URL + "\n", encoding="utf-8")

FLAGS = ("ATLAS_SEED", "ATLAS_LEGACY_V0", "ATLAS_RESEED", "ATLAS_DB_URL", "ATLAS_DB_URL_FILE",
         "ATLAS_ADMIN_ACTOR")
BASE_ENV = {k: v for k, v in os.environ.items() if k not in FLAGS}
BASE_ENV.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
BASE_ENV.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
BASE_ENV.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
BASE_ENV.setdefault("ATLAS_BREACH_CHECK", "bundled")
BASE_ENV.setdefault("ATLAS_FRONTEND_DIR", str(REPO / "frontend"))
BASE_ENV["ATLAS_SEED"] = "none"
BASE_ENV["ATLAS_LEGACY_V0"] = "0"
BASE_ENV["TMPDIR"] = str(TMP / "tmp")
BOOT_ENV = {**BASE_ENV, "ATLAS_DB_URL": DB.url}
BK_ENV = {**BASE_ENV, "ATLAS_DB_URL_FILE": str(URL_FILE)}

failures: list[str] = []
OUTPUTS: list[str] = []   # every child's stdout+stderr, for F


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def run_child(args: list[str], env: dict, quiet_fail: bool = False) -> subprocess.CompletedProcess:
    proc = subprocess.run([sys.executable, *args], cwd=HERE, env=env, input="",
                          capture_output=True, text=True, timeout=600)
    OUTPUTS.append(proc.stdout + proc.stderr)
    if proc.returncode != 0 and not quiet_fail:
        print(f"  child {args[:4]!r} exited {proc.returncode}")
        print("  stdout: " + proc.stdout[-1500:].replace("\n", "\n  "))
        print("  stderr: " + proc.stderr[-1500:].replace("\n", "\n  "))
    return proc


def backup(*args: str, quiet_fail: bool = False) -> subprocess.CompletedProcess:
    return run_child(["admin.py", "--actor", "verify-crunch", "backup", "run",
                      "--pg-bin", str(BIN), *args], BK_ENV, quiet_fail=quiet_fail)


def boot() -> None:
    run_child(["-c", "import app; app.startup()"], BOOT_ENV)


def reset() -> None:
    with ENGINE.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    # models_crunch caches "the crunch tables exist" for the life of the process (they are never
    # dropped in production). This script drops the whole schema between sections, so a fresh
    # schema must also mean a fresh cache in this process, as it would be in a new one.
    if "models_crunch" in sys.modules:
        sys.modules["models_crunch"]._EXIST = False


def data() -> dict:
    """(row count, sha256 of every row) per table, plus every sequence position."""
    out: dict = {}
    with ENGINE.connect() as conn:
        tables = sorted(r[0] for r in conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")))
        for t in tables:
            got = sorted(r for (r,) in conn.execute(
                select(cast(func.to_jsonb(literal_column("t")), Text))
                .select_from(table(t).alias("t"))))
            out[t] = (len(got), hashlib.sha256("\n".join(got).encode("utf-8")).hexdigest())
        for name, last in conn.execute(text(
                "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public'")):
            out[f"sequence:{name}"] = (-1, str(last))
    return out


def ddl(conn, statement: str, name: str, password: str | None = None) -> None:
    """DDL with the identifier (and a password literal) quoted by psycopg2.sql."""
    from psycopg2 import sql as psql
    parts = [psql.Identifier(name)] + ([psql.Literal(password)] if password is not None else [])
    cur = conn.connection.dbapi_connection.cursor()
    try:
        cur.execute(psql.SQL(statement).format(*parts))
    finally:
        cur.close()


def rows(sql: str, **params) -> list[tuple]:
    with ENGINE.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), params)]


def source_counts() -> dict:
    with ENGINE.connect() as conn:
        return {t: conn.execute(select(func.count()).select_from(table(t))).scalar()
                for t in ("node", "edge", "graph_event")}


def verify_dbs() -> list:
    return [r[0] for r in rows("SELECT datname FROM pg_database WHERE datname LIKE 'atlas_verify_%'")]


def homelab() -> None:
    """The homelab's shape: the default boot, the test fixture, its `atlas` project node."""
    reset()
    boot()
    seed = "\n".join([
        "import app, testseed",
        "from db import session",
        "from models_graph import Node, Edge",
        "from attention import recompute_derived",
        "with session() as s:",
        "    testseed.build(s)",
        "    for nid, t in [('atlas', 'project'), ('atlas-api', 'service')]:",
        "        s.add(Node(id=nid, type=t, name=nid, attributes={}, body='homelab', source=None, origin='atlas'))",
        "    s.flush()",
        "    s.add(Edge(src='atlas', dst='atlas-api', edge_type='contains', origin='atlas'))",
        "    s.flush()",
        "    recompute_derived(s)",
        "    s.commit()",
    ])
    check("homelab-shaped database built", run_child(["-c", seed], BOOT_ENV).returncode == 0)
    boot()


WRAPPER = r'''#!__PY__
# TEST-ONLY stand-in for __TOOL__: the client of the test Postgres image, run with `docker run`
# on the host network. Files are STREAMED (--file becomes stdout, pg_restore's archive argument
# stdin) rather than bind-mounted, because the runner may itself be a container whose paths
# the Docker host cannot see. The PGPASSFILE crunch_backup wrote is read here and handed to
# the container as an inherited PGPASSWORD (never on an argv); it is the throwaway role's.
import os, subprocess, sys
tool, image = "__TOOL__", "__IMAGE__"
args = sys.argv[1:]
out = inp = None
if "--file" in args:
    i = args.index("--file"); out = args[i + 1]; del args[i:i + 2]
if tool == "pg_restore" and args and not args[-1].startswith("-"):
    inp = args.pop()
env = dict(os.environ)
extra = []
if env.get("PGPASSFILE"):
    line = open(env["PGPASSFILE"], encoding="utf-8").read().rstrip("\n")
    raw, pw, esc = line.split(":", 4)[4], "", False
    for ch in raw:
        if esc: pw += ch; esc = False
        elif ch == "\\": esc = True
        else: pw += ch
    env["PGPASSWORD"] = pw
    extra = ["-e", "PGPASSWORD"]
cmd = ["docker", "run", "--rm", "-i", "--network", "host", *extra, image, tool, *args]
stdin = open(inp, "rb") if inp else subprocess.DEVNULL
stdout = open(out, "wb") if out else None
sys.exit(subprocess.run(cmd, stdin=stdin, stdout=stdout, env=env).returncode)
'''


def write_wrappers() -> None:
    for tool in ("pg_dump", "pg_restore"):
        p = BIN / tool
        p.write_text(WRAPPER.replace("__PY__", sys.executable).replace("__TOOL__", tool)
                     .replace("__IMAGE__", testdb.POSTGRES_IMAGE), encoding="utf-8")
        p.chmod(0o755)


def dumps(d: Path) -> list:
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def _raises(fn, *args) -> bool:
    """fn(*args) raises an error whose message names the variable it was given (OSS-26 C)."""
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001
        return any(k in str(exc) for k in args[0])
    return False


try:
    with ENGINE.begin() as conn:
        ddl(conn, "CREATE ROLE {} LOGIN SUPERUSER PASSWORD {}", ROLE, password=ROLE_PW)
    write_wrappers()

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("A. the image and the compose profile")
    print("=" * 78)
    dockerfile = (HERE / "Dockerfile").read_text(encoding="utf-8")
    stages = re.findall(r"^FROM\s+(\S+)(?:\s+AS\s+(\S+))?", dockerfile, re.MULTILINE | re.IGNORECASE)
    names = [s[1] for s in stages]
    check("backend/Dockerfile has a `backup` stage FROM hardened",
          ("hardened", "backup") in stages, str(stages))
    tail = dockerfile[dockerfile.rindex("\nFROM "):]
    check("the last stage is still bare `FROM base` (the homelab's image is unchanged)",
          stages[-1] == ("base", "") and not [ln for ln in tail.splitlines()[2:]
                                              if ln.strip() and not ln.lstrip().startswith("#")],
          repr(tail[:80]))
    bstage = dockerfile[dockerfile.index("AS backup"):dockerfile.rindex("\nFROM ")]
    # OSS-26 C: the client is the server's major (16), copied from the image atlas-postgres is
    # built from. pg_restore 17 writes `SET transaction_timeout`, which a 16 server refuses.
    # The image is a named stage on a FROM line (Dependabot updates FROM lines, not
    # COPY --from=<image>), and must stay the one deploy/postgres/pgvector.Dockerfile builds on:
    # a Dependabot PR that moves only one of the two fails here.
    pg_stage = dict((alias, image) for image, alias in stages if alias).get("pgclient")
    pg_copy = re.search(r"COPY --from=pgclient /usr/lib/postgresql/16/bin/pg_dump "
                        r"/usr/lib/postgresql/16/bin/pg_restore ", bstage)
    pg_server = re.search(r"^FROM\s+(\S+)", (REPO / "deploy" / "postgres" / "pgvector.Dockerfile")
                          .read_text(encoding="utf-8"), re.MULTILINE)
    check("the backup stage carries pg_dump/pg_restore 16 from stage `pgclient`, FROM the SAME "
          "pinned image as atlas-postgres; no Debian postgresql-client (17); non-root user",
          bool(pg_stage and pg_copy and pg_server) and pg_stage == pg_server.group(1)
          and "@sha256:" in pg_stage and "postgresql-client" not in bstage
          and "USER 10001:0" in bstage, f"{pg_stage} vs {pg_server and pg_server.group(1)}")
    check("... and no COPY --from names an image directly (Dependabot would not update it)",
          not re.search(r"^\s*COPY\s+--from=\S*[/:@]", dockerfile, re.MULTILINE))
    # Instructions only: the backup stage's own comment block sits above its FROM line.
    before_backup = "\n".join(ln for ln in dockerfile[:dockerfile.index("AS backup")].splitlines()
                              if ln.strip() and not ln.lstrip().startswith("#"))
    check("base and hardened carry no Postgres client (atlas-api's image is unchanged)",
          "AS hardened" in before_backup and "postgresql-client" not in before_backup
          and "pg_dump" not in before_backup)
    compose = yaml.safe_load((REPO / "deploy" / "oss" / "docker-compose.yml").read_text(encoding="utf-8"))
    svc = compose["services"].get("atlas-backup") or {}
    check("atlas-backup is in profile `backup` only (not started by default)",
          svc.get("profiles") == ["backup"], str(svc.get("profiles")))
    check("... builds the `backup` stage, runs `python -m crunch_backup`",
          (svc.get("build") or {}).get("target") == "backup"
          and svc.get("command", [])[:3] == ["python", "-m", "crunch_backup"])
    check("... is on `data` only and publishes nothing",
          svc.get("networks") == ["data"] and "ports" not in svc)
    check("... reads the DB URL from the secret file, never a value",
          (svc.get("environment") or {}).get("ATLAS_DB_URL_FILE") == "/run/secrets/db_url"
          and "ATLAS_DB_URL" not in (svc.get("environment") or {})
          and "db_url" in (svc.get("secrets") or []))
    check("... its volume is declared, the named volume unless ATLAS_BACKUP_HOST_DIR names a "
          "host directory (OSS-26 C)", "atlas-backups" in (compose.get("volumes") or {})
          and "${ATLAS_BACKUP_HOST_DIR:-atlas-backups}:/var/lib/atlas/backups" in (svc.get("volumes") or []))
    others = [n for n, s in compose["services"].items() if n != "atlas-backup"
              and "crunch_backup" in json.dumps(s)]
    homelab_files = [REPO / "docker-compose.yml", *sorted((REPO / "deploy").glob("*.yml"))]
    # A tree without the homelab compose files (the public export ships only the root one) has
    # nothing to scan for them: say so instead of passing in silence.
    if not (REPO / "docker-compose.yml").exists():
        print("SKIP the root compose file does not run atlas-backup: docker-compose.yml is not in this tree")
    if not sorted((REPO / "deploy").glob("*.yml")):
        print("SKIP no homelab overlay runs atlas-backup: deploy/*.yml is not in this tree")
    named = [str(p.name) for p in homelab_files
             if p.exists() and re.search(r"crunch_backup|atlas-backup|target:\s*backup", p.read_text(encoding="utf-8"))]
    check("no other service and no homelab compose file runs it (the `no-backup` intent stands)",
          not others and not named, f"{others} {named}")

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("B. a backup is taken and verified")
    print("=" * 78)
    homelab()
    out_b = TMP / "backups-b"
    before = source_counts()
    p = backup("--dir", str(out_b))
    check("`atlas-admin backup run` (manual) exits 0 and says it restored and counted",
          p.returncode == 0 and "backup ok (manual)" in p.stdout
          and "counted equal" in p.stdout, p.stdout.strip()[-300:])
    files = dumps(out_b)
    dump_names = [f for f in files if f.endswith(".dump")]
    check("one dump, its .sha256 and its .json, nothing else",
          len(dump_names) == 1 and sorted(files) == sorted([dump_names[0], dump_names[0] + ".sha256",
                                                             dump_names[0] + ".json"]), str(files))
    dump = out_b / dump_names[0] if dump_names else out_b / "missing"
    manifest = json.loads((out_b / (dump.name + ".json")).read_text(encoding="utf-8")) if dump_names else {}
    check("the manifest's counts are the source's node, edge and graph_event at dump time",
          manifest.get("counts") == before and before["node"] > 0 and before["edge"] > 0,
          f"{manifest.get('counts')} vs {before}")
    side = (out_b / (dump.name + ".sha256")).read_text(encoding="utf-8").split() if dump_names else []
    digest = hashlib.sha256(dump.read_bytes()).hexdigest() if dump_names else ""
    check("the checksum file is `sha256sum -c` format and matches the file",
          side == [digest, dump.name] and manifest.get("sha256") == digest)
    row = rows("SELECT by_subject, detail::text FROM crunch_history WHERE action = 'backup'")
    check("one crunch_history backup row (crunch.record_backup): ok, verified, labelled with the "
          "file name, by the operator",
          len(row) == 1 and row[0][0] == "verify-crunch"
          and json.loads(row[0][1]) == {"ok": True, "verified": True, "label": dump.name}, str(row))
    ev = rows("SELECT node_id, entity_type, entity_id, detail::text FROM graph_event "
              "WHERE action = 'node.crunch.backup'")
    detail = json.loads(ev[0][3]) if ev else {}
    check("one node.crunch.backup graph_event on atlas-system with the same detail",
          len(ev) == 1 and ev[0][:3] == ("atlas-system", "node", "atlas-system")
          and detail == {"ok": True, "verified": True, "label": dump.name}, str(ev)[:300])
    check("the event's own row is not in the dump's counts (it was written after the snapshot)",
          source_counts()["graph_event"] == before["graph_event"] + 1)
    check("no temporary database is left behind", verify_dbs() == [], str(verify_dbs()))

    os.environ.update({k: v for k, v in BK_ENV.items() if k.startswith("ATLAS_")})
    os.environ.pop("ATLAS_DB_URL", None)
    tempfile.tempdir = str(TMP / "tmp")
    import crunch  # noqa: E402
    import crunch_backup as CB  # noqa: E402
    from sqlalchemy.orm import Session  # noqa: E402

    with Session(ENGINE) as s:
        last = crunch.last_backup(s)
    check("crunch.last_backup() (GET /api/crunch's last_backup) returns that attempt, no path",
          last is not None and last.get("ok") is True and last.get("label") == dump.name
          and str(out_b) not in json.dumps(last), str(last)[:200])
    wrong = dict(before, edge=before["edge"] + 1)
    try:
        CB.verify_dump(dump, wrong, ROLE_URL, pg_bin=str(BIN))
        mismatch = ""
    except CB.BackupError as exc:
        mismatch = str(exc)
    check("perturbation: an expectation one edge off is a RESTORE MISMATCH",
          "RESTORE MISMATCH" in mismatch, mismatch[:200])
    check("... and its temporary database is dropped too", verify_dbs() == [])

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("C. a corrupted dump is detected, loudly")
    print("=" * 78)
    good = TMP / "c-good"
    good.mkdir()
    for f in files:
        shutil.copy2(out_b / f, good / f)
    p = backup("--verify-only", str(good))
    check("--verify-only on the intact backup exits 0", p.returncode == 0 and "ok" in p.stdout,
          p.stdout.strip()[-200:])

    flip = TMP / "c-flip"
    shutil.copytree(good, flip)
    raw = bytearray((flip / dump.name).read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    (flip / dump.name).write_bytes(bytes(raw))
    p = backup("--verify-only", str(flip), quiet_fail=True)
    check("one flipped byte: exit 1, CHECKSUM MISMATCH", p.returncode == 1
          and "CHECKSUM MISMATCH" in p.stdout + p.stderr, (p.stdout + p.stderr).strip()[-200:])

    trunc = TMP / "c-trunc"
    shutil.copytree(good, trunc)
    short = (trunc / dump.name).read_bytes()[: max(64, len(raw) // 3)]
    (trunc / dump.name).write_bytes(short)
    (trunc / (dump.name + ".sha256")).write_text(
        f"{hashlib.sha256(short).hexdigest()}  {dump.name}\n", encoding="utf-8")
    p = backup("--verify-only", str(trunc), quiet_fail=True)
    check("a truncated dump whose checksum matches: exit 1, the restore fails and says so",
          p.returncode == 1 and "pg_restore" in p.stdout + p.stderr and "failed" in p.stdout + p.stderr,
          (p.stdout + p.stderr).strip()[-200:])
    check("... and no temporary database is left behind", verify_dbs() == [], str(verify_dbs()))
    nos = TMP / "c-nosum"
    shutil.copytree(good, nos)
    (nos / (dump.name + ".sha256")).unlink()
    p = backup("--verify-only", str(nos), quiet_fail=True)
    check("a dump without its checksum file: exit 1", p.returncode == 1
          and "no checksum file" in p.stdout + p.stderr)
    empty = TMP / "c-empty"
    empty.mkdir()
    p = backup("--verify-only", str(empty), quiet_fail=True)
    check("an empty directory: exit 1 (nothing verified is not a pass)", p.returncode == 1
          and "no backup in" in p.stderr)
    # A backup whose restore fails is not kept: drive take_backup with a pg_restore that fails.
    broken_bin = TMP / "bin-broken"
    broken_bin.mkdir()
    shutil.copy2(BIN / "pg_dump", broken_bin / "pg_dump")
    (broken_bin / "pg_restore").write_text("#!/bin/sh\necho 'simulated restore failure' >&2\nexit 3\n",
                                           encoding="utf-8")
    (broken_bin / "pg_restore").chmod(0o755)
    out_fail = TMP / "backups-fail"
    p = run_child(["admin.py", "backup", "run", "--dir", str(out_fail), "--pg-bin", str(broken_bin)],
                  BK_ENV, quiet_fail=True)
    check("a backup whose verification fails: exit 1, saying why", p.returncode == 1
          and "simulated restore failure" in p.stderr, p.stderr.strip()[-200:])
    check("... its dump is removed (an unverified file is not a backup)", dumps(out_fail) == [],
          str(dumps(out_fail)))
    check("... and the failure is recorded (row and event, ok false)",
          rows("SELECT (detail->>'ok'), (detail->>'verified'), (detail->>'label') FROM crunch_history "
               "WHERE action = 'backup' ORDER BY id DESC LIMIT 1")[0][:2] == ("false", "false")
          and rows("SELECT (detail->>'ok') FROM graph_event WHERE action = 'node.crunch.backup' "
                   "ORDER BY id DESC LIMIT 1") == [("false",)])

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("D. retention deletes only older files")
    print("=" * 78)
    rd = TMP / "retention"
    rd.mkdir()
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=10)).strftime("%Y%m%dT%H%M%SZ")
    new = (now - timedelta(days=2)).strftime("%Y%m%dT%H%M%SZ")
    keep = ["notes.txt", "atlas-old.dump", f"atlas-{old}-scheduled.dump.partial",
            f"atlas-{new}-scheduled.dump", f"atlas-{new}-scheduled.dump.sha256",
            f"atlas-{new}-scheduled.dump.json"]
    gone = [f"atlas-{old}-scheduled.dump", f"atlas-{old}-scheduled.dump.sha256",
            f"atlas-{old}-scheduled.dump.json", f"atlas-{old}-per_deploy.dump"]
    for f in keep + gone:
        (rd / f).write_text("x", encoding="utf-8")
        os.utime(rd / f, (0, 0))  # every mtime ancient: the rule reads the name, not the clock
    check("unset retention deletes nothing", CB.prune(rd, None, now) == [] and CB.prune(rd, 0, now) == []
          and len(dumps(rd)) == len(keep + gone))
    deleted = CB.prune(rd, 7, now)
    check("retention 7 days deletes exactly the older dumps and their sidecars",
          sorted(deleted) == sorted(gone) and sorted(dumps(rd)) == sorted(keep), str(deleted))

    def state(policy, retention=7, activated=None):
        # The scheduler reads attempts recorded at or after `activated_at`, so a new crunch is
        # "activated" now, after every earlier attempt.
        activated = activated or "2000-01-01T00:00:00+00:00"
        return {"starts_at": activated, "ends_at": "2099-01-01T00:00:00+00:00",
                "backup": {"policy": policy, "retention_days": retention}, "skip_envs": [],
                "active": True, "activated_by": "owner", "activated_at": activated, "adr_id": "SYS9"}

    rd2 = TMP / "retention-run"
    rd2.mkdir()
    (rd2 / f"atlas-{old}-per_deploy.dump").write_text("x", encoding="utf-8")
    (rd2 / "unrelated.sql").write_text("x", encoding="utf-8")
    res, msg = CB.run("per_deploy", rd2, operator="verify", pg_bin=str(BIN),
                      state_fn=lambda e: state("per_deploy"))
    check("a per_deploy backup under a per_deploy crunch runs and then prunes the older file only",
          res is not None and res.ok and f"atlas-{old}-per_deploy.dump" not in dumps(rd2)
          and "unrelated.sql" in dumps(rd2) and res.file in dumps(rd2), msg[:200])
    (rd2 / f"atlas-{old}-per_deploy.dump").write_text("x", encoding="utf-8")
    res, msg = CB.run("per_deploy", rd2, operator="verify", pg_bin=str(broken_bin),
                      state_fn=lambda e: state("per_deploy"))
    check("a failed backup prunes nothing (the last good one is never thrown away)",
          res is not None and not res.ok and f"atlas-{old}-per_deploy.dump" in dumps(rd2))

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("E. crunch off, or not this policy: nothing; on: the loop acts when due")
    print("=" * 78)
    homelab()
    d0 = data()
    off_dir = TMP / "off"
    for reason in ("per_deploy", "once", "scheduled"):
        p = backup("--reason", reason, "--dir", str(off_dir))
        check(f"crunch off: `backup run --reason {reason}` exits 0 and says it skipped",
              p.returncode == 0 and "skipped" in p.stdout and "crunch is off" in p.stdout,
              p.stdout.strip()[-200:])
    p = run_child(["crunch_backup.py", "--one-tick", "--dir", str(off_dir), "--pg-bin", str(BIN)],
                  BK_ENV)
    check("crunch off: one tick of the loop exits 0 and says crunch is off",
          p.returncode == 0 and "crunch is off" in p.stdout, p.stdout.strip()[-200:])
    with Session(ENGINE) as s:
        check("crunch.last_backup() with no backup ever is None", crunch.last_backup(s) is None)
    for policy in ("none", "per_deploy"):
        res, msg = CB.tick(off_dir, pg_bin=str(BIN), state_fn=lambda e, p=policy: state(p))
        check(f"policy {policy!r}: the loop does nothing", res is None, msg)
    for reason, policy in (("per_deploy", "none"), ("per_deploy", "hourly"), ("once", "daily"),
                           ("scheduled", "per_deploy")):
        res, msg = CB.run(reason, off_dir, operator="verify", pg_bin=str(BIN),
                          state_fn=lambda e, p=policy: state(p))
        check(f"--reason {reason} under policy {policy!r}: refused, nothing run", res is None, msg)
    check("no file was written", dumps(off_dir) == [], str(dumps(off_dir)))
    check("no backup was recorded (no crunch_history backup row, no node.crunch.backup event)",
          not rows("SELECT 1 FROM graph_event WHERE action = 'node.crunch.backup'")
          and (not rows("SELECT to_regclass('public.crunch_history')")[0][0]
               or not rows("SELECT 1 FROM crunch_history WHERE action = 'backup'")))
    check("the database is byte-identical (every row and sequence)", data() == d0)
    with Session(ENGINE) as s:
        real = crunch.current(s)
    check("crunch.current() on this install is None (never activated)", real is None, str(real))
    check("... and the database is still byte-identical after that read", data() == d0)

    loop_dir = TMP / "loop"
    t0 = datetime.now(timezone.utc)
    act1 = t0.isoformat()
    res, msg = CB.tick(loop_dir, pg_bin=str(BIN), state_fn=lambda e: state("hourly", activated=act1), now=t0)
    check("hourly: the first tick of a crunch backs up (reason scheduled)",
          res is not None and res.ok and res.reason == "scheduled", msg[:200])
    res, msg = CB.tick(loop_dir, pg_bin=str(BIN), state_fn=lambda e: state("hourly", activated=act1),
                       now=t0 + timedelta(minutes=30))
    check("... 30 minutes later: not due", res is None, msg)
    res, msg = CB.tick(loop_dir, pg_bin=str(BIN), state_fn=lambda e: state("daily", activated=act1),
                       now=t0 + timedelta(hours=2))
    check("daily, 2 hours after the last: not due", res is None, msg)
    res, msg = CB.tick(loop_dir, pg_bin=str(BIN), state_fn=lambda e: state("hourly", activated=act1),
                       now=t0 + timedelta(minutes=61))
    check("hourly, 61 minutes later: due", res is not None and res.ok, msg[:200])
    act2 = datetime.now(timezone.utc).isoformat()
    res1, _ = CB.tick(loop_dir, pg_bin=str(BIN), state_fn=lambda e: state("once", activated=act2))
    res2, msg2 = CB.tick(loop_dir, pg_bin=str(BIN), state_fn=lambda e: state("once", activated=act2),
                         now=datetime.now(timezone.utc) + timedelta(days=3))
    check("once: one backup at activation, and never a second for that crunch",
          res1 is not None and res1.ok and res1.reason == "once" and res2 is None, msg2)
    act3 = datetime.now(timezone.utc).isoformat()
    resf, _ = CB.tick(loop_dir, pg_bin=str(broken_bin), state_fn=lambda e: state("once", activated=act3))
    resb, msgb = CB.tick(loop_dir, pg_bin=str(BIN), state_fn=lambda e: state("once", activated=act3))
    resr, _ = CB.tick(loop_dir, pg_bin=str(BIN), state_fn=lambda e: state("once", activated=act3),
                      now=datetime.now(timezone.utc) + CB.RETRY_AFTER + timedelta(minutes=1))
    check("a failed attempt is retried after the back-off, not on the next tick",
          resf is not None and not resf.ok and resb is None and resr is not None and resr.ok, msgb)
    n_ev = rows("SELECT count(*) FROM graph_event WHERE action = 'node.crunch.backup'")[0][0]
    n_rows = rows("SELECT count(*) FROM crunch_history WHERE action = 'backup'")[0][0]
    check("every attempt is one row and one event", n_ev == n_rows == 5, f"{n_ev} events, {n_rows} rows")

    # ----------------------------------------------------------------------- #
    # --- OSS-26 C: restore, rollback and routine backups (TASK-1416) ---------
    print("=" * 78)
    print("G. restore: checksum first, staging, in-use refusal, swap, safety copy, rollback")
    print("=" * 78)
    import settings as S  # noqa: E402

    def release() -> None:
        """This process's own pools would count as "in use" (and the refusal is right to say
        so): the test's ENGINE, and db.engine, which crunch.record_backup used in sections D and
        E. Both are emptied before every restore or rollback."""
        ENGINE.dispose()
        if "db" in sys.modules:
            sys.modules["db"].engine.dispose()

    def restore(*args: str, quiet_fail: bool = False) -> subprocess.CompletedProcess:
        release()
        return run_child(["admin.py", "--actor", "verify-crunch", "backup", "restore",
                          "--pg-bin", str(BIN), *args], BK_ENV, quiet_fail=quiet_fail)

    def rollback(*args: str, quiet_fail: bool = False) -> subprocess.CompletedProcess:
        release()
        return run_child(["admin.py", "--actor", "verify-crunch", "backup", "rollback", *args],
                         BK_ENV, quiet_fail=quiet_fail)

    def copies_of(kind: str) -> list:
        return [r[0] for r in rows("SELECT datname FROM pg_database WHERE datname LIKE :p "
                                   "ORDER BY datname", p=f"%\\_{kind}\\_%")]

    def staging_dbs() -> list:
        return [r[0] for r in rows("SELECT datname FROM pg_database WHERE datname LIKE 'atlas\\_restore\\_%'")]

    def counts_in(dbname: str) -> dict:
        eng = create_engine(make_url(DB.url).set(database=dbname))
        try:
            with eng.connect() as conn:
                return {t: conn.execute(select(func.count()).select_from(table(t))).scalar()
                        for t in ("node", "edge", "graph_event")}
        finally:
            eng.dispose()

    homelab()
    g_dir = TMP / "restore"
    d_backup = data()      # what the backup captures (its own record is written after the snapshot)
    p = backup("--dir", str(g_dir))
    g_dumps = [f for f in dumps(g_dir) if f.endswith(".dump")]
    check("a verified backup to restore from", p.returncode == 0 and len(g_dumps) == 1, p.stdout[-200:])
    g_dump = g_dir / (g_dumps[0] if g_dumps else "missing")
    g_counts = json.loads((g_dir / (g_dump.name + ".json")).read_text(encoding="utf-8"))["counts"] if g_dumps else {}
    with ENGINE.begin() as conn:  # change and delete data after the backup
        conn.execute(text("DELETE FROM edge WHERE ctid IN (SELECT ctid FROM edge LIMIT 3)"))
        conn.execute(text("DELETE FROM graph_event WHERE id IN (SELECT id FROM graph_event ORDER BY id LIMIT 5)"))
        conn.execute(text("UPDATE node SET name = name || ' (changed)' WHERE id IN "
                          "(SELECT id FROM node ORDER BY id LIMIT 2)"))
    d_changed = data()
    check("the data changed after the backup (edges and events deleted, nodes renamed)",
          d_changed != d_backup and source_counts() != g_counts, f"{source_counts()} vs {g_counts}")

    flip_dir = TMP / "g-flip"
    shutil.copytree(g_dir, flip_dir)
    raw = bytearray((flip_dir / g_dump.name).read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    (flip_dir / g_dump.name).write_bytes(bytes(raw))
    p = restore(str(flip_dir / g_dump.name), quiet_fail=True)
    check("a flipped byte: exit 1, CHECKSUM MISMATCH, before any database is touched",
          p.returncode == 1 and "CHECKSUM MISMATCH" in p.stderr and "checksum ok" not in p.stdout,
          (p.stdout + p.stderr).strip()[-200:])
    nomani = TMP / "g-nomanifest"
    shutil.copytree(g_dir, nomani)
    (nomani / (g_dump.name + ".json")).unlink()
    p = restore(str(nomani / g_dump.name), quiet_fail=True)
    check("no manifest: exit 1 (no counts to compare against is no restore)",
          p.returncode == 1 and "no manifest" in p.stderr, p.stderr.strip()[-200:])
    check("... the database is unchanged, no staging database, no safety copy",
          data() == d_changed and staging_dbs() == [] and copies_of("prerestore") == [])

    p = restore(g_dump.name, "--dir", str(g_dir), "--dry-run")
    check("--dry-run: exit 0, checksum ok, restored into staging and counted equal, says it "
          "changed nothing", p.returncode == 0 and "checksum ok" in p.stdout
          and "counted equal to the manifest" in p.stdout and "nothing was changed" in p.stdout
          and "would REFUSE" not in p.stdout,
          p.stdout.strip()[-300:])
    check("... and nothing was changed (every row and sequence), no staging, no safety copy",
          data() == d_changed and staging_dbs() == [] and copies_of("prerestore") == [])

    release()
    hold_engine = create_engine(DB.url)
    hold = hold_engine.connect()  # someone still connected, as atlas-api would be
    try:
        hold.execute(text("SELECT 1"))
        p_dry = run_child(["admin.py", "backup", "restore", "latest", "--dir", str(g_dir),
                           "--pg-bin", str(BIN), "--dry-run"], BK_ENV)
        p_real = run_child(["admin.py", "backup", "restore", "latest", "--dir", str(g_dir),
                            "--pg-bin", str(BIN)], BK_ENV, quiet_fail=True)
    finally:
        hold.close()
        hold_engine.dispose()
    check("in use, --dry-run: exit 0 and says a real run would refuse",
          p_dry.returncode == 0 and "would REFUSE" in p_dry.stdout, p_dry.stdout.strip()[-200:])
    check("in use, a real run: exit 1, 'is in use', naming how to stop it",
          p_real.returncode == 1 and "is in use" in p_real.stderr and "docker compose stop atlas-api" in p_real.stderr,
          p_real.stderr.strip()[-300:])
    check("... nothing was changed", data() == d_changed and staging_dbs() == []
          and copies_of("prerestore") == [])

    p = restore("latest", "--dir", str(g_dir), "--pg-bin", str(broken_bin), quiet_fail=True)
    check("pg_restore fails: exit 1 saying so; the live database untouched, the staging one dropped",
          p.returncode == 1 and "simulated restore failure" in p.stderr and data() == d_changed
          and staging_dbs() == [] and copies_of("prerestore") == [], p.stderr.strip()[-200:])

    p = restore("latest", "--dir", str(g_dir))
    safety = copies_of("prerestore")
    check("restore: exit 0, restored and counted equal to the manifest, names the safety copy "
          "and the rollback", p.returncode == 0 and "restored:" in p.stdout
          and len(safety) == 1 and safety[0] in p.stdout and "backup rollback --to" in p.stdout,
          p.stdout.strip()[-400:])
    check("the database is the backup again: identical counts to the manifest",
          source_counts() == g_counts, f"{source_counts()} vs {g_counts}")
    check("... and byte-identical to the moment of the backup (every row and sequence)",
          data() == d_backup)
    check("the safety copy is the database as it was before the restore",
          bool(safety) and counts_in(safety[0]) == {t: d_changed[t][0] for t in ("node", "edge", "graph_event")})
    check("no staging database is left", staging_dbs() == [], str(staging_dbs()))
    p = rollback()
    check("`backup rollback` lists the safety copy", p.returncode == 0 and bool(safety)
          and safety[0] in p.stdout, p.stdout.strip()[-200:])
    p = rollback("--to", "atlas_test_prerestore_19990101t000000z", quiet_fail=True)
    check("rollback to a copy that does not exist: exit 1", p.returncode == 1)
    p = rollback("--to", "postgres", quiet_fail=True)
    check("rollback to a database that is not a safety copy: exit 1", p.returncode == 1
          and "not a safety copy" in p.stderr)
    p = rollback("--to", safety[0] if safety else "missing")
    check("rollback: exit 0; the database is the one the restore replaced (every row and sequence)",
          p.returncode == 0 and data() == d_changed, p.stdout.strip()[-200:])
    rolled = copies_of("rolledback")
    check("... the restored one is set aside, not dropped", len(rolled) == 1
          and counts_in(rolled[0]) == g_counts and copies_of("prerestore") == [])
    for name in rolled:
        with ENGINE.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
            ddl(conn, "DROP DATABASE IF EXISTS {} WITH (FORCE)", name)

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("H. routine backups outside crunch (ATLAS_BACKUP_SCHEDULE / ATLAS_BACKUP_KEEP)")
    print("=" * 78)
    homelab()
    check("unset: schedule off, keep 7; every bad value names the variable",
          S.load_backup_schedule({}) == S.BackupSchedule("off", 7)
          and all(_raises(S.load_backup_schedule, bad) for bad in (
              {"ATLAS_BACKUP_SCHEDULE": ""}, {"ATLAS_BACKUP_SCHEDULE": "monthly"},
              {"ATLAS_BACKUP_KEEP": "0"}, {"ATLAS_BACKUP_KEEP": "many"})))
    h_dir = TMP / "routine"
    off = S.load_backup_schedule({})
    res1, msg1 = CB.tick(h_dir, pg_bin=str(BIN), state_fn=lambda e: None, schedule=off)
    res2, msg2 = CB.tick(h_dir, pg_bin=str(BIN), state_fn=lambda e: state("none"), schedule=off)
    check("no schedule: the loop's answers are exactly the crunch path's, and nothing is written",
          res1 is None and msg1 == "crunch is off: nothing to do" and res2 is None
          and msg2 == "policy 'none': no backup due" and dumps(h_dir) == [], f"{msg1!r} {msg2!r}")
    h_dir.mkdir()
    old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y%m%dT%H%M%SZ")
    bystanders = ["notes.txt", f"atlas-{old}-manual.dump", f"atlas-{old}-manual.dump.sha256",
                  f"atlas-{old}-scheduled.dump"]
    for f in bystanders:
        (h_dir / f).write_text("x", encoding="utf-8")
    daily = S.BackupSchedule("daily", 1)
    t0 = datetime.now(timezone.utc)
    res, msg = CB.tick(h_dir, pg_bin=str(BIN), state_fn=lambda e: None, schedule=daily, now=t0)
    check("daily, crunch off, no routine backup yet: a verified `routine` backup",
          res is not None and res.ok and res.reason == "routine" and res.file.endswith("-routine.dump"),
          msg[:200])
    first = res.file if res is not None else None
    res, msg = CB.tick(h_dir, pg_bin=str(BIN), state_fn=lambda e: None, schedule=daily,
                       now=t0 + timedelta(hours=12))
    check("... 12 hours later: not due", res is None and "not due" in msg, msg)
    res, msg = CB.tick(h_dir, pg_bin=str(BIN), state_fn=lambda e: None, schedule=daily,
                       now=t0 + timedelta(hours=25))
    check("... 25 hours later: due, taken", res is not None and res.ok, msg[:200])
    routine = [f for f in dumps(h_dir) if "-routine." in f]
    check("keep 1: two routine backups taken, the older deleted with its sidecars",
          len(routine) == 3 and first not in routine and res is not None
          and sorted(routine) == sorted([res.file, res.file + ".json", res.file + ".sha256"]),
          str(dumps(h_dir)))
    check("... and no other file touched (manual, crunch, unrelated)",
          all((h_dir / f).exists() for f in bystanders))
    act = datetime.now(timezone.utc).isoformat()
    res, msg = CB.tick(h_dir, pg_bin=str(BIN), state_fn=lambda e: state("hourly", activated=act),
                       schedule=daily, now=t0 + timedelta(hours=80))
    check("a crunch due at the same tick: the crunch's backup is taken, not a routine one",
          res is not None and res.ok and res.reason == "scheduled", msg[:200])
    res, msg = CB.tick(h_dir, pg_bin=str(BIN), state_fn=lambda e: state("per_deploy"),
                       schedule=daily, now=t0 + timedelta(hours=81))
    check("a crunch whose policy is not the loop's: its answer, then the routine schedule",
          res is not None and res.ok and res.reason == "routine", msg[:200])

    h2 = TMP / "routine-fail"
    hourly = S.BackupSchedule("hourly", 3)
    resf, _ = CB.tick(h2, pg_bin=str(broken_bin), state_fn=lambda e: None, schedule=hourly)
    resb, msgb = CB.tick(h2, pg_bin=str(BIN), state_fn=lambda e: None, schedule=hourly)
    resr, _ = CB.tick(h2, pg_bin=str(BIN), state_fn=lambda e: None, schedule=hourly,
                      now=datetime.now(timezone.utc) + CB.RETRY_AFTER + timedelta(minutes=1))
    check("a failed routine attempt is recorded and retried after the back-off, not next tick",
          resf is not None and not resf.ok and resb is None and resr is not None and resr.ok, msgb)

    cron = TMP / "routine-cron"
    keep1 = {**BK_ENV, "ATLAS_BACKUP_KEEP": "1"}
    p1 = run_child(["admin.py", "backup", "run", "--reason", "routine", "--dir", str(cron),
                    "--pg-bin", str(BIN)], keep1)
    p2 = run_child(["admin.py", "backup", "run", "--reason", "routine", "--dir", str(cron),
                    "--pg-bin", str(BIN)], keep1)
    check("`backup run --reason routine` (a host cron): runs with crunch off and keeps "
          "ATLAS_BACKUP_KEEP=1", p1.returncode == 0 and p2.returncode == 0
          and len([f for f in dumps(cron) if f.endswith(".dump")]) == 1, str(dumps(cron)))
    loop_env = {**BK_ENV, "ATLAS_BACKUP_SCHEDULE": "monthly"}
    p = run_child(["crunch_backup.py", "--one-tick", "--dir", str(TMP / "routine-loop"),
                   "--pg-bin", str(BIN)], loop_env, quiet_fail=True)
    check("the loop with a bad ATLAS_BACKUP_SCHEDULE: exit 2 at start, naming it",
          p.returncode == 2 and "ATLAS_BACKUP_SCHEDULE" in p.stderr, p.stderr.strip()[-200:])
    loop_env["ATLAS_BACKUP_SCHEDULE"] = "weekly"
    p = run_child(["crunch_backup.py", "--one-tick", "--dir", str(TMP / "routine-loop"),
                   "--pg-bin", str(BIN)], loop_env)
    check("the loop (`python -m crunch_backup`), schedule weekly, crunch off: one routine backup",
          p.returncode == 0 and "routine backups: weekly" in p.stdout and "backup ok (routine)" in p.stdout
          and len([f for f in dumps(TMP / "routine-loop") if f.endswith("-routine.dump")]) == 1,
          p.stdout.strip()[-300:])
    # --- end OSS-26 C ---------------------------------------------------------

    # --- OSS-26 L: the backup verbs on an install with SSO on (TASK-1437) -----
    print("=" * 78)
    print("I. `atlas-admin backup` with the OIDC settings set and their files absent (atlas-backup)")
    print("=" * 78)
    # What atlas-backup sees on an SSO install: .env's sign-in block (env_file), but none of the
    # files it names, which are mounted into atlas-api only. Every value is a valid one.
    missing = TMP / "not-mounted"
    oidc_env = {**BK_ENV,
                "ATLAS_AUTH_PROVIDERS": "oidc", "ATLAS_LOCAL_LOGIN": "break-glass",
                "ATLAS_OIDC_ISSUER": "https://idp.invalid/atlas", "ATLAS_OIDC_CLIENT_ID": "atlas",
                "ATLAS_OIDC_REDIRECT_URI": "https://testserver/auth/oidc/callback",
                "ATLAS_OIDC_CLIENT_SECRET_FILE": str(missing / "oidc_client_secret"),
                "ATLAS_OIDC_GROUP_MAP_FILE": str(missing / "oidc_group_map"),
                "ATLAS_OIDC_REQUIRED_GROUPS": "atlas-users", "ATLAS_OIDC_STEP_UP": "on"}
    for argv in (["--actor", "verify-crunch", "user", "list"], ["--actor", "backup", "user", "list"]):
        p = run_child(["admin.py", *argv], oidc_env, quiet_fail=True)
        check(f"`atlas-admin {' '.join(argv)}`: still refused at import (exit 2), naming the "
              "unreadable group map", p.returncode == 2 and "ATLAS_OIDC_GROUP_MAP_FILE" in p.stderr,
              p.stderr.strip()[-200:])
    i_dir = TMP / "oidc-on"
    p = run_child(["admin.py", "--actor", "verify-crunch", "backup", "run", "--dir", str(i_dir),
                   "--pg-bin", str(BIN)], oidc_env)
    check("`backup run`: exit 0 and a verified backup, without the OIDC files",
          p.returncode == 0 and len([f for f in dumps(i_dir) if f.endswith("-manual.dump")]) == 1,
          p.stdout.strip()[-200:])
    p = run_child(["admin.py", "backup", "run", "--verify-only", str(i_dir), "--pg-bin", str(BIN)],
                  oidc_env)
    check("`backup run --verify-only`: exit 0", p.returncode == 0, p.stdout.strip()[-200:])
    before = data()
    release()
    p = run_child(["admin.py", "--actor", "verify-crunch", "backup", "restore", "latest",
                   "--dir", str(i_dir), "--dry-run", "--pg-bin", str(BIN)], oidc_env)
    check("`backup restore --dry-run`: exit 0, nothing changed", p.returncode == 0 and data() == before,
          p.stdout.strip()[-200:])
    release()
    p = run_child(["admin.py", "backup", "rollback"], oidc_env)
    check("`backup rollback` (list): exit 0", p.returncode == 0, p.stdout.strip()[-200:])
    # --- end OSS-26 L ---------------------------------------------------------

    # ----------------------------------------------------------------------- #
    print("=" * 78)
    print("F. no credential anywhere")
    print("=" * 78)
    blob = "\n".join(OUTPUTS)
    check(f"no child output ({len(OUTPUTS)} runs) holds the password or the URL",
          ROLE_PW not in blob and ROLE_URL not in blob)
    written = "".join(p.read_text(encoding="utf-8", errors="replace")
                      for p in TMP.rglob("*") if p.is_file() and p.suffix in (".json", ".sha256"))
    check("no manifest or checksum file holds it", ROLE_PW not in written)
    stored = "".join(str(r) for r in rows("SELECT * FROM crunch_history")) + "".join(
        str(r) for r in rows("SELECT detail::text FROM graph_event WHERE action = 'node.crunch.backup'"))
    check("no crunch_history row and no event detail holds it", bool(stored) and ROLE_PW not in stored)
    check("the scrubber removes it from a message that carries it",
          ROLE_PW not in CB._scrub(f"could not connect to {ROLE_URL} with {ROLE_PW}"))
    check("no PGPASSFILE is left in the temp dir", not list((TMP / "tmp").glob("atlas-backup-*")))
finally:
    try:
        with ENGINE.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
            # OSS-26 C: the restore's staging databases and copies too (they hold objects the
            # throwaway role owns, which DROP ROLE below would otherwise refuse).
            for name in [r[0] for r in conn.execute(text(
                    "SELECT datname FROM pg_database WHERE datname LIKE 'atlas\\_verify\\_%' "
                    "OR datname LIKE 'atlas\\_restore\\_%' OR datname LIKE '%\\_prerestore\\_%' "
                    "OR datname LIKE '%\\_rolledback\\_%'"))]:
                ddl(conn, "DROP DATABASE IF EXISTS {} WITH (FORCE)", name)
            ddl(conn, "DROP OWNED BY {}", ROLE)
            ddl(conn, "DROP ROLE IF EXISTS {}", ROLE)
    except Exception as exc:  # noqa: BLE001
        print(f"  cleanup: {exc}")
    shutil.rmtree(TMP, ignore_errors=True)
    ENGINE.dispose()

print("=" * 78)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASSED")
