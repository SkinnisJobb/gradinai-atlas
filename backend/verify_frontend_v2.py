"""
Runs board v2's `node --test` suite as a member of `verify_all.py` (ADR 0043,
TASK-1304/1305).

Board v2 keeps its pure logic in modules with no DOM (`frontend/v2/core/`, and
the arithmetic in `ui/`) precisely so it can be tested here, without a browser.
An agent cannot judge layout in a browser (the `atlas-frontend` intent), but it
can test the functions that compute it.

THE SUITE IS DISCOVERED, NEVER LISTED: every `*.test.js` under `frontend/v2/`.
A pinned list is a second census to keep true, which TESTING.md warns against.

NO SKIP PATH. If node is missing, this FAILS. A frontend suite that reports
green because it never ran is the same lie the SQLite fallback told (TESTING.md,
"How a test gets a database"). It also fails on zero test files: an empty glob
must never read as green.

Needs no database. It runs under the runner like every other script, and
ignores the DSN it is handed.

Run:
    python verify_frontend_v2.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

V2 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "v2")
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def main() -> int:
    node = shutil.which("node")
    check("node is on PATH", bool(node), node or "install Node 20+; this suite never skips")
    tests = sorted(
        os.path.join(d, f) for d, _dirs, files in os.walk(V2) for f in files if f.endswith(".test.js")
    )
    check("board v2 has test files", bool(tests), f"{len(tests)} under frontend/v2/")
    if failures:
        return 1

    # Every module, test or not, must at least parse. It is what CI's
    # `node --check` job does, repeated here so a local run catches it too.
    modules = sorted(os.path.join(d, f) for d, _dirs, files in os.walk(V2) for f in files if f.endswith(".js"))
    bad = []
    for m in modules:
        r = subprocess.run([node, "--check", m], capture_output=True, text=True)
        if r.returncode != 0:
            bad.append(os.path.relpath(m, V2) + ": " + (r.stderr.strip().splitlines() or ["?"])[-1])
    check("every v2 module parses", not bad, "; ".join(bad) if bad else f"{len(modules)} modules")

    r = subprocess.run([node, "--test", *tests], cwd=V2, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    summary = {}
    for line in r.stdout.splitlines():
        for key in ("tests", "pass", "fail"):
            if line.startswith(f"ℹ {key} ") or line.startswith(f"# {key} "):
                summary[key] = line.split()[-1]
    check("node --test passes", r.returncode == 0,
          f"{summary.get('pass', '?')} of {summary.get('tests', '?')} passed")
    if r.returncode != 0:
        # The failing test's own output, so a red run says what broke.
        print(r.stdout[-4000:])
        print(r.stderr[-2000:])

    print()
    if failures:
        print(f"FAILED: {len(failures)} claim(s)")
        for f in failures:
            print("  - " + f)
        return 1
    print("ALL CLAIMS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
