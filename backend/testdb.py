"""
Shared disposable-Postgres helper for every `backend/verify_*.py` script
(TASK-1044 — "run the verify scripts on Postgres and delete the SQLite
shims"). Atlas runs on Postgres everywhere; the verify harness now does too.

DESIGN CONSTRAINT, stated once here rather than six times: a verify run must
NEVER report success while a section was skipped for lack of a real
database. Before this task, several scripts fell back to a SQLite scratch
file (or skipped whole sections) when no Postgres answered, and still
printed something that reads as green. That failure mode is deliberately
impossible to reach through this module — there are exactly three outcomes:

  1. `ATLAS_TEST_DB_URL` is set -> use it. Reachability is checked; an
     unreachable override is a hard error, not a silent fallback to
     anything else.
  2. Otherwise, start a disposable `postgres:16-alpine` container via
     `docker run`, wait for it to accept connections, hand back its DSN,
     and stop the container when the caller is done — on success AND on
     failure (registered with `atexit`, not left to the caller to remember).
  3. Neither works (no override, and Docker itself is missing or the `run`
     fails) -> raise `TestDatabaseUnavailable`. The caller's `if __name__ ==
     "__main__":` block turns that into a clear message and `sys.exit(1)`.
     There is no fourth path back to SQLite. It does not exist any more.

Usage (the shape every verify_*.py now follows — note `ATLAS_DB_URL` must be
set BEFORE `import app`, since app.py builds its module-level `engine` at
import time):

    import testdb
    DB = testdb.start()
    os.environ["ATLAS_DB_URL"] = DB.url
    import app  # noqa: E402
    ...
    if __name__ == "__main__":
        try:
            main()
        except testdb.TestDatabaseUnavailable as e:
            print(f"FATAL: {e}")
            sys.exit(1)

`DB.reset_schema()` is exposed separately (not just run once at startup)
because a couple of scripts deliberately want a second clean schema
mid-run, to prove something about a database that starts EMPTY partway
through a section (e.g. "what happens when a sequence is created on a
Postgres that already holds TASK-001..TASK-1062").
"""
from __future__ import annotations

import atexit
import os
import socket
import subprocess
import time
import uuid

POSTGRES_IMAGE = "postgres:16-alpine"
# The retrieval add-on's suite (ADR 0044 §1) asks for this image; every other script keeps the
# plain one, so the core is still proven on a Postgres WITHOUT pgvector. The same base as
# deploy/postgres/pgvector.Dockerfile (which deploy/oss and deploy/retrieval.override.yml build),
# pulled as-is: the gosu shim that Dockerfile adds changes nothing a test can observe.
PGVECTOR_IMAGE = ("pgvector/pgvector:0.8.7-pg16-trixie@sha256:"
                  "40d828eda66737aaacb934387a337babf1455ebd56e9bdd13fc08e211257c972")
POSTGRES_PASSWORD = "test"
POSTGRES_DB = "atlas_test"
STARTUP_TIMEOUT_S = 30


class TestDatabaseUnavailable(RuntimeError):
    """No real Postgres could be reached, and none could be started either.
    There is no fallback dialect any more — this is always fatal to the
    caller, never something to catch and downgrade to a skip."""


def _reachable(url: str, timeout_s: float = 2.0) -> bool:
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(url, connect_args={"connect_timeout": max(1, int(timeout_s))})
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:  # noqa: BLE001 — this IS the reachability probe
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestDatabase:
    """A real Postgres, either handed to us via `ATLAS_TEST_DB_URL` or a
    disposable container this class owns. See the module docstring."""

    def __init__(self, image: str | None = None) -> None:
        #: None is the default image AND honours ATLAS_TEST_DB_URL. A named image always gets
        #: its own container: the shared DSN verify_all hands down is a plain Postgres.
        self.image = image
        self.url: str | None = None
        self._container: str | None = None
        self._owns_shared_db = False  # True when using ATLAS_TEST_DB_URL — clean up data, not a container

    # ------------------------------------------------------------------ #
    def start(self) -> "TestDatabase":
        override = os.environ.get("ATLAS_TEST_DB_URL") if self.image is None else None
        if self.image is not None and os.environ.get("ATLAS_TEST_DB_URL"):
            print(f"[testdb] ATLAS_TEST_DB_URL is set but this script needs {self.image}; "
                  "starting its own container instead")
        if override:
            print(f"[testdb] ATLAS_TEST_DB_URL is set — using {override}")
            if not _reachable(override):
                raise TestDatabaseUnavailable(
                    f"ATLAS_TEST_DB_URL={override!r} is set but not reachable. This harness "
                    "no longer falls back to SQLite or anything else — fix the DSN, start the "
                    "database it points at, or unset the variable to get a disposable container."
                )
            self.url = override
            self._owns_shared_db = True
        else:
            self._start_container()

        self.reset_schema()  # acceptance: "each run starts from a clean schema"
        atexit.register(self.stop)
        return self

    def stop(self) -> None:
        if self._container:
            name, self._container = self._container, None
            print(f"[testdb] tearing down {name}")
            # `-v`: the postgres image declares a VOLUME for its data directory, so every run
            # created an anonymous volume, and `rm -f` alone left it behind. On a workstation
            # that ran the suite often they had reached 596 volumes and 34 GB (2026-10-26).
            subprocess.run(["docker", "rm", "-fv", name], capture_output=True, text=True)
        elif self._owns_shared_db and self.url:
            # No container to remove — this is someone else's Postgres. Leave
            # nothing behind in it rather than leaving test data sitting on a
            # shared/persistent database (acceptance: "leaves nothing behind").
            try:
                self.reset_schema()
            except Exception as e:  # noqa: BLE001 — best-effort teardown, never fatal
                print(f"[testdb] WARNING: could not clean {self.url} at teardown: {e}")
        self.url = None

    def reset_schema(self) -> None:
        """DROP/CREATE the `public` schema — the only structurally-different
        part of moving off SQLite (SQLite's teardown was `os.remove(path)`;
        Postgres's is this). Idempotent, safe to call more than once per run."""
        from sqlalchemy import create_engine, text
        eng = create_engine(self.url)
        with eng.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        eng.dispose()

    # ------------------------------------------------------------------ #
    def _start_container(self) -> None:
        name = f"atlas-verify-pg-{uuid.uuid4().hex[:10]}"
        port = _free_port()
        image = self.image or POSTGRES_IMAGE
        print(f"[testdb] starting disposable {name} ({image}) on port {port}")
        # The data directory is a tmpfs and durability is off: the database is thrown away when the
        # run ends, so writing it to disk only costs I/O and, through the image's anonymous VOLUME,
        # disk that outlives the run (docker_data.vhdx grew to 112 GB on 2026-10-26 that way).
        # Both images keep PGDATA at /var/lib/postgresql/data. Nothing here reads these settings.
        try:
            subprocess.run(
                ["docker", "run", "-d", "--name", name,
                 "-e", f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
                 "-e", f"POSTGRES_DB={POSTGRES_DB}",
                 "--tmpfs", "/var/lib/postgresql/data",
                 "-p", f"{port}:5432", image,
                 "-c", "fsync=off", "-c", "synchronous_commit=off",
                 "-c", "full_page_writes=off"],
                check=True, capture_output=True, text=True, timeout=300,
            )
        except FileNotFoundError as e:
            raise TestDatabaseUnavailable(
                "docker is not on PATH — cannot start a disposable Postgres, and "
                "ATLAS_TEST_DB_URL is not set. Install/start Docker, or set "
                "ATLAS_TEST_DB_URL to point at a real Postgres."
            ) from e
        except subprocess.CalledProcessError as e:
            raise TestDatabaseUnavailable(
                f"`docker run` failed starting a disposable Postgres: "
                f"{(e.stderr or str(e)).strip()}"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise TestDatabaseUnavailable(f"`docker run` timed out: {e}") from e

        self._container = name
        url = f"postgresql+psycopg2://postgres:{POSTGRES_PASSWORD}@localhost:{port}/{POSTGRES_DB}"

        deadline = time.time() + STARTUP_TIMEOUT_S
        while time.time() < deadline:
            if _reachable(url):
                self.url = url
                print(f"[testdb] {name} is up: {url}")
                return
            time.sleep(0.5)

        # Never leave a half-started container behind just because it never
        # came up — stop() before raising, same as any other failure path.
        self.stop()
        raise TestDatabaseUnavailable(
            f"disposable Postgres container {name!r} did not become reachable within "
            f"{STARTUP_TIMEOUT_S}s. `docker logs {name}` (before it was removed) would have "
            "shown why; run this script again with ATLAS_TEST_DB_URL pointed at a Postgres "
            "you already know works if this recurs."
        )


def start(image: str | None = None) -> TestDatabase:
    """`db = testdb.start()`; `db.url` is a real, reachable Postgres DSN with
    a clean `public` schema. Raises `TestDatabaseUnavailable` — uncaught by
    design — if that could not be arranged. See the module docstring."""
    return TestDatabase(image).start()
