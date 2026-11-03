"""The first sign-in of each smoke user, through the real login form (install smoke, leg 3).

`atlas-admin user add` prints a one-time temporary password (the operator can never choose one,
ADR 0025); the workflow redirects that output into SMOKE_DIR/<user>.add without showing it.
This reads the temp password out of that file, signs in with it, is sent to the forced
password change as a new account is, sets a fresh random password, and writes that one to
SMOKE_DIR/<user>.password (0600). Nothing secret is printed; every value is masked.

    python tests/bootstrap_users.py alice bob
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas_smoke as smoke  # noqa: E402


def temp_password(user: str) -> str:
    lines = (smoke.DIR / f"{user}.add").read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("Temporary password for"):
            for cand in lines[i + 1:]:
                c = cand.strip()
                if c and not set(c) <= {"-"}:
                    smoke.mask(c)
                    return c
    raise SystemExit(f"no temporary password in {user}.add (did `user add` succeed?)")


def main(users: list[str]) -> int:
    for user in users:
        temp = temp_password(user)
        new = secrets.token_urlsafe(24)
        smoke.mask(new)
        s = smoke.Session()
        r = s.login(user, temp)
        if r.status_code != 303 or r.headers.get("location") != "/auth/password/change":
            print(f"{user}: first sign-in did not lead to the forced change "
                  f"({r.status_code} -> {r.headers.get('location')})")
            return 1
        # Until the change, a temp-password session reaches the change form and nothing else.
        r = s.http.get("/api/me")
        assert r.status_code == 403, f"{user}: /api/me before the forced change: {r.status_code}"
        r = s.change_password(temp, new)
        if r.status_code != 303:
            print(f"{user}: password change refused ({r.status_code})")
            return 1
        path = smoke.DIR / f"{user}.password"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new + "\n")
        # The new password works on the login form, and the account is now a normal one.
        s2 = smoke.signed_in(user)
        print(f"{user}: temp password -> forced change -> signed in with the new password "
              f"(role={s2.me['role']}, must_change_password={s2.me['must_change_password']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or [smoke.OWNER, smoke.MEMBER]))
