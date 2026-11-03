#!/usr/bin/env python
"""One command runs the whole verify suite (TASK-1069, STORY-atlas-suite-runs-itself).

    python backend/verify_all.py

Discovers every `backend/verify_*.py`, gives the RUN its own disposable
Postgres, executes them serially against it, and prints one verdict with a
per-script breakdown and timings. Exit code is 0 only if every script exited 0.

WHY THIS EXISTS, stated once. There is more verify code than application code
(7,252 lines across the scripts vs 2,935 in `app.py`) and until this file none
of it was wired to anything -- it ran exactly when somebody remembered to type
fourteen commands. That is the same defect the epic spent a day deleting from
the backlog side: `sync-backlog-to-board.sh` ran manually only, so the board was
as fresh as the last time anyone remembered.

THE RACE THIS IS SHAPED BY. On 2026-08-10 two subagents ran scripts concurrently
against ONE shared Postgres container. One dropped the schema mid-run of the
other; both then reported green on a re-run. The results were trustworthy only
because a human re-ran them serially by hand. Sharing a container is tempting
precisely because starting one is the slow part -- so this runner starts exactly
one, for itself, and never asks the caller to arrange isolation.

THREE THINGS THIS MUST NEVER DO, from the task's acceptance:

  1. **No skip path.** `testdb.py` fails loudly when it cannot reach Postgres,
     on purpose. A runner that turns an unreachable database into "0 scripts,
     all passed" is worse than no runner at all. So: an unreachable database is
     a hard exit, and discovering ZERO scripts is also a hard exit -- an empty
     suite must never render as green.
  2. **Serial by default.** Concurrency against one database is the bug being
     fixed here, not a feature to add later. If this ever parallelises, each
     worker needs its own database -- not a lock, not a schema prefix.
  3. **Own its database.** Two runs at once must not corrupt each other.
     `testdb` gives each container a uuid name on a free port, so two runners
     get two containers. The one way to break that is to set
     `ATLAS_TEST_DB_URL` yourself, which puts both runs in the same database --
     the exact 2026-08-10 configuration. Honoured (it is `testdb`'s documented
     contract) but warned about, loudly, every time.

HOW ISOLATION BETWEEN SCRIPTS WORKS, since it is not obvious. The runner passes
its DSN down as `ATLAS_TEST_DB_URL`, so each child takes branch 1 of `testdb`'s
three outcomes -- and `TestDatabase.start()` resets the `public` schema on
entry, `stop()` resets it again on exit. Every script therefore starts clean and
leaves nothing behind, inside one container, without the runner reaching into
their business.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import testdb

HERE = Path(__file__).resolve().parent

# NOT EVERY verify_*.py NEED BE A SUITE MEMBER. Anything named here is skipped,
# and the reason is PRINTED on every run — a suite that quietly drops things is
# how "all passed" stops meaning anything.
#
# Empty as of TASK-1059. It held `verify_arrival.py`, an operator gate that
# diffed a populated deployment database against the markdown backlog: pointed
# at this runner's disposable Postgres it would have reported the entire corpus
# missing. That gate authorised the deletion and was deleted by it, along with
# the six other scripts whose subject was the migration itself. What remains is
# the product's own test suite, and all of it runs unattended.
NOT_SUITE_MEMBERS: dict[str, str] = {}

# Generous, and not a performance target: the story puts "making the suite
# faster" explicitly out of scope. This exists so a hung script fails the run
# instead of hanging it forever, which matters the moment this runs unattended.
DEFAULT_TIMEOUT_S = 1800


def discover(only: str | None) -> tuple[list[Path], list[Path]]:
    """Returns (scripts to run, scripts deliberately excluded)."""
    found = sorted(p for p in HERE.glob("verify_*.py") if p.name != Path(__file__).name)
    if only:
        found = [p for p in found if only in p.name]
    scripts = [p for p in found if p.name not in NOT_SUITE_MEMBERS]
    excluded = [p for p in found if p.name in NOT_SUITE_MEMBERS]
    return scripts, excluded


def run_one(script: Path, env: dict[str, str], timeout_s: int) -> tuple[int, float, str]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, script.name],
            cwd=HERE,
            env=env,
            capture_output=True,
            text=True,
            # The scripts print box-drawing characters and em-dashes. On Windows
            # a piped child defaults to cp1252 and would die on its own output,
            # so the decode is pinned here and the encoding is pinned in the
            # child's environment below. A test suite must not fail for a
            # reason that has nothing to do with the code under test.
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, time.monotonic() - started, output
    except subprocess.TimeoutExpired as e:
        partial = (e.stdout or "") + (e.stderr or "")
        return 124, time.monotonic() - started, partial + f"\n[verify_all] TIMED OUT after {timeout_s}s\n"


def failure_digest(output: str, tail_lines: int = 12) -> list[str]:
    """What went wrong, without making anyone open a log.

    The scripts share one convention -- a `[FAIL] label — detail` line per
    failed check -- so those lines ARE the summary when they exist. When they
    do not (an import error, a crash before the first check), the tail is the
    only thing that says anything, so fall back to it rather than printing
    a confident "no failures found" over a red run.
    """
    fails = [l.rstrip() for l in output.splitlines() if l.startswith("[FAIL]")]
    if fails:
        return fails
    return [l.rstrip() for l in output.splitlines()[-tail_lines:] if l.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="Run every backend/verify_*.py against one disposable Postgres.")
    ap.add_argument("--only", metavar="SUBSTR", help="run only scripts whose filename contains SUBSTR")
    ap.add_argument("--verbose", action="store_true", help="print each script's full output, not just failures")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, metavar="SECONDS",
                    help=f"per-script timeout (default {DEFAULT_TIMEOUT_S})")
    args = ap.parse_args()

    scripts, excluded = discover(args.only)
    if not scripts:
        # Never green. An empty suite is a broken runner, not a passing one.
        print(f"FATAL: no verify_*.py scripts found in {HERE}"
              + (f" matching {args.only!r}" if args.only else ""))
        return 1

    print("=" * 78)
    print(f"Atlas verify suite — {len(scripts)} script(s)")
    for p in excluded:
        print(f"  excluded: {p.name} — {NOT_SUITE_MEMBERS[p.name]}")
    print("=" * 78)

    if os.environ.get("ATLAS_TEST_DB_URL"):
        print("!! ATLAS_TEST_DB_URL is set, so this run does NOT own its database.")
        print("!! Two runs against one database is exactly the 2026-08-10 race: one")
        print("!! dropped the other's schema mid-run and both reported green after.")
        print("!! Unset it to get an isolated container.\n")

    try:
        db = testdb.start()
    except testdb.TestDatabaseUnavailable as e:
        # Deliberately not caught into a skip. See the module docstring.
        print(f"FATAL: {e}")
        return 1

    child_env = dict(os.environ)
    child_env["ATLAS_TEST_DB_URL"] = db.url
    child_env["PYTHONIOENCODING"] = "utf-8"

    results: list[tuple[str, int, float]] = []
    for i, script in enumerate(scripts, 1):
        print(f"[{i}/{len(scripts)}] {script.name} ... ", end="", flush=True)
        rc, elapsed, output = run_one(script, child_env, args.timeout)
        results.append((script.name, rc, elapsed))
        print(f"{'PASS' if rc == 0 else 'FAIL'} ({elapsed:.1f}s)")
        if args.verbose:
            print(output)
        elif rc != 0:
            for line in failure_digest(output):
                print(f"      {line}")

    failed = [(n, rc) for n, rc, _ in results if rc != 0]
    total_s = sum(e for _, _, e in results)

    print("\n" + "=" * 78)
    width = max(len(n) for n, _, _ in results)
    for name, rc, elapsed in results:
        print(f"  {'PASS' if rc == 0 else 'FAIL'}  {name:{width}}  {elapsed:7.1f}s"
              + ("" if rc == 0 else f"  (exit {rc})"))
    print("-" * 78)
    if failed:
        print(f"RESULT: {len(failed)} of {len(results)} script(s) FAILED  ({total_s:.1f}s total)")
        for name, rc in failed:
            print(f"  - {name} (exit {rc})")
    else:
        print(f"RESULT: all {len(results)} script(s) passed  ({total_s:.1f}s total)")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
