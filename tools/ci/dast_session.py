#!/usr/bin/env python3
"""A signed-in session for the DAST API scan (STORY-atlas-lcm-plan item 6, .github/workflows/dast.yml).

Reuses the install smoke's helpers (tests/atlas_smoke.py): the user's password is the one
tests/bootstrap_users.py set at the first sign-in, read from SMOKE_DIR. Signs in through the real
login form and writes the session's Cookie header value to OUT, one line, for ZAP's hook
(tools/ci/zap_auth_hook.py) to send with every request. The value is masked in the Actions log
and never printed; the file is the only place it exists, and the stack it opens is torn down
with the job.

  python tools/ci/dast_session.py USER OUT
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
import atlas_smoke as smoke  # noqa: E402


def main(user: str, out: str) -> int:
    s = smoke.signed_in(user)
    jar = {c.name: c.value for c in s.http.cookies.jar}
    if "__Host-atlas_session" not in jar:
        print("no session cookie after sign-in", file=sys.stderr)
        return 1
    header = "; ".join(f"{k}={v}" for k, v in sorted(jar.items()))
    smoke.mask(header)
    for v in jar.values():
        smoke.mask(v)
    # Read by the ZAP container's own user (uid 1000), hence 0644 inside a job-private directory.
    # fchmod as well: the step's `umask 077` would otherwise turn the 0644 into 0600.
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.fchmod(fd, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(header + "\n")
    r = s.get("/api/me")
    print(f"{user}: signed in (/api/me {r.status_code}, role={s.me.get('role')}); "
          f"cookie names: {', '.join(sorted(jar))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
