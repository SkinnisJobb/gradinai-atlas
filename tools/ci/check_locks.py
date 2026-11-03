#!/usr/bin/env python3
"""The hashed Python locks are in step with requirements.txt (STORY-atlas-lcm-plan, item 3).

backend/ and mcp/ each have requirements.txt (the direct pins, ADR 0006) and
requirements-lock.txt (every package, exact version, sha256 hashes), written by
tools/ci/lock_requirements.sh. The Dockerfiles install the LOCK with --require-hashes, so a
lock that disagrees with requirements.txt would ship something nobody reviewed. This fails when:

  1. a direct pin `name==version` in requirements.txt is not in the lock at that version
     (the typical cause: requirements.txt edited by hand, lock not regenerated);
  2. a lock entry carries no `--hash=sha256:` (pip would refuse the whole file);
  3. a package is in both locks at different versions (CI installs both into one environment);
  4. a Dockerfile stopped installing the lock with --require-hashes.

No network. The installability of each lock (all hashes present for the platform, the pins
mutually consistent) is proved by the image builds in security.yml, which run that very pip.

Usage: python tools/ci/check_locks.py [--self-test]      Exit 0 ok, 1 findings.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENTS = {"backend": "backend/Dockerfile", "mcp": "mcp/Dockerfile"}
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*==\s*([^\s;\\#]+)")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def direct_pins(text: str) -> dict[str, str]:
    return {norm(m.group(1)): m.group(2) for line in text.splitlines() if (m := PIN.match(line))}


def lock_entries(text: str) -> dict[str, tuple[str, int]]:
    """{name: (version, number of hashes)}; an entry spans its `\\`-continued lines."""
    out: dict[str, tuple[str, int]] = {}
    current = None
    for line in text.splitlines():
        m = PIN.match(line)
        if m:
            current = norm(m.group(1))
            out[current] = (m.group(2), line.count("--hash=sha256:"))
        elif current and line.startswith((" ", "\t")) and "--hash=sha256:" in line:
            ver, n = out[current]
            out[current] = (ver, n + line.count("--hash=sha256:"))
        elif not line.startswith((" ", "\t")):
            current = None
    return out


def check(read) -> list[str]:
    errors: list[str] = []
    locks: dict[str, dict[str, tuple[str, int]]] = {}
    for comp, dockerfile in COMPONENTS.items():
        req, lock = f"{comp}/requirements.txt", f"{comp}/requirements-lock.txt"
        entries = lock_entries(read(lock))
        locks[comp] = entries
        for name, ver in direct_pins(read(req)).items():
            got = entries.get(name)
            if got is None:
                errors.append(f"{lock}: {name} is pinned in {req} but missing from the lock")
            elif got[0] != ver:
                errors.append(f"{lock}: {name}=={got[0]}, but {req} pins {name}=={ver}")
        for name, (ver, hashes) in entries.items():
            if hashes == 0:
                errors.append(f"{lock}: {name}=={ver} has no --hash")
        df = read(dockerfile)
        if not re.search(r"^RUN pip install .*--require-hashes -r requirements-lock\.txt", df, re.M):
            errors.append(f"{dockerfile}: no `pip install --require-hashes -r requirements-lock.txt`")
    shared = set(locks["backend"]) & set(locks["mcp"])
    for name in sorted(shared):
        a, b = locks["backend"][name][0], locks["mcp"][name][0]
        if a != b:
            errors.append(f"{name}: {a} in backend's lock, {b} in mcp's (they share one CI environment)")
    return errors


def self_test() -> int:
    files = {
        "backend/requirements.txt": "# c\nfastapi==1.0\nuvicorn[standard]==0.5\n",
        "backend/requirements-lock.txt": "fastapi==1.0 \\\n    --hash=sha256:aa\nuvicorn==0.4 \\\n"
                                         "    --hash=sha256:bb\nh11==1 ; sys_platform != 'x'\n",
        "backend/Dockerfile": "RUN pip install --no-cache-dir --require-hashes -r requirements-lock.txt\n",
        "mcp/requirements.txt": "mcp==2\n",
        "mcp/requirements-lock.txt": "mcp==2 \\\n    --hash=sha256:cc\nfastapi==1.1 \\\n    --hash=sha256:dd\n",
        "mcp/Dockerfile": "RUN pip install -r requirements.txt\n",
    }
    errs = check(files.__getitem__)
    want = ["uvicorn==0.4, but", "h11==1 has no --hash", "mcp/Dockerfile", "fastapi: 1.0 in backend"]
    ok = len(errs) == len(want) and all(any(w in e for e in errs) for w in want)
    print("self-test", "passed" if ok else f"FAILED: {errs}")
    return 0 if ok else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    errors = check(lambda rel: (ROOT / rel).read_text(encoding="utf-8"))
    for e in errors:
        print(f"::error::{e}")
    if errors:
        print("\nRegenerate the locks: python -m pip install uv==0.13.0 && bash tools/ci/lock_requirements.sh")
        return 1
    print("locks: every direct pin matches, every entry is hashed, the two locks agree, "
          "both Dockerfiles install with --require-hashes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
