"""The backend verify suite under pytest (STORY-oss-17): one test per `verify_*.py`.

    cd backend && python -m pytest test_verify_all.py          # the whole suite
    cd backend && python -m pytest test_verify_all.py -k rbac  # the scripts matching "rbac"

A wrapper, not a second suite. It discovers the scripts exactly as `verify_all.py` does
(`verify_all.discover`, so the skip list and its printed reasons are the same), starts ONE
disposable Postgres for the session through `testdb` exactly as `verify_all.py` does, and runs
each script as its own process with `verify_all.run_one`, serially, against that database. Each
script resets the schema on entry and exit, as under `verify_all.py`. A failing script's
`[FAIL]` lines are the assertion message.

The same three refusals hold: no Docker (and no `ATLAS_TEST_DB_URL`) is a hard error, never a
skip; zero discovered scripts fails; nothing runs in parallel (do not add `-n`: concurrency
against one database is the race `verify_all.py`'s docstring describes).

`verify_all.py` stays the command CI's `verify` workflow and AGENTS.md's done check run; this
file exists so a contributor who types `pytest` gets the same verdict.
"""
from __future__ import annotations

import os

import pytest

import testdb
import verify_all

SCRIPTS, EXCLUDED = verify_all.discover(None)


def test_suite_is_not_empty():
    assert SCRIPTS, f"no verify_*.py found in {verify_all.HERE}: the runner is broken, not green"
    for p in EXCLUDED:
        print(f"excluded: {p.name} - {verify_all.NOT_SUITE_MEMBERS[p.name]}")


@pytest.fixture(scope="session")
def suite_env():
    if os.environ.get("ATLAS_TEST_DB_URL"):
        print("ATLAS_TEST_DB_URL is set: this run does NOT own its database (see verify_all.py)")
    try:
        db = testdb.start()
    except testdb.TestDatabaseUnavailable as e:
        pytest.fail(f"no database for the suite (never a skip): {e}", pytrace=False)
    env = dict(os.environ)
    env["ATLAS_TEST_DB_URL"] = db.url
    env["PYTHONIOENCODING"] = "utf-8"
    yield env
    db.stop()


@pytest.mark.parametrize("script", SCRIPTS, ids=[p.name for p in SCRIPTS])
def test_verify_script(script, suite_env):
    rc, elapsed, output = verify_all.run_one(script, suite_env, verify_all.DEFAULT_TIMEOUT_S)
    if rc != 0:
        digest = "\n".join(verify_all.failure_digest(output))
        pytest.fail(f"{script.name} exited {rc} after {elapsed:.1f}s:\n{digest}", pytrace=False)
