"""
Verification for backend/nul_guard.py (LCM patch round 1): a NUL byte in the request URL is a
400 naming where it was, from ONE middleware, instead of the 500 Postgres's "A string literal
cannot contain NUL" used to produce on search, workitems and adrs. Through the REAL gate,
against a disposable Postgres (`testdb`).

  A. offending(): path, query value, query key, `+`/percent decoding, and `%2500` (the TEXT
     "%00", which is not a NUL) passes. Pure function, no app.
  B. Signed in: GET /api/search, /api/workitems, /api/adrs and /api/audit/export with a NUL
     in a parameter (q, label, status, since, kinds) are each a 400 whose detail names the
     parameter; a NUL in a path segment (/api/adrs/%00, /api/node/%00[/structure]) is a 400; the same routes with a clean value, and with
     `%2500`, still answer 200 (nothing else changed).
  C. Signed out: the gate still answers first. `/api/search?q=%00` gets exactly the status
     the gate gives `/api/search?q=x` (401), never this 400, so the guard discloses nothing
     to a caller the gate refused.
  D. The guard is installed once, in main.py, inside the gate: NulGuard sits after AuthGate
     in the middleware stack (Starlette lists the outermost first).

Run (from a venv with the backend requirements installed):
    python verify_nul_bytes.py
"""
from __future__ import annotations

import base64
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:
    print(f"FATAL: {e}")
    sys.exit(1)
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.setdefault(
    "ATLAS_FRONTEND_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend"),
)
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")

TEST_ORIGIN = "https://testserver"

from sqlalchemy import select  # noqa: E402

import app  # noqa: E402
import auth  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthUser  # noqa: E402
from nul_guard import NulGuard, offending  # noqa: E402

failures: list[str] = []
OWNER_PW = "Correct-Horse-Battery-NulGuard-9!"


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def client():
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN, raise_server_exceptions=False)


def sign_in(c, username: str, password: str) -> None:
    page = c.get("/auth/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": username, "password": password, "csrf_token": token},
           headers={"Origin": TEST_ORIGIN}, follow_redirects=False)


def main() -> None:
    print("-- A. offending() --")
    q = lambda s: {"type": "http", "path": "/api/search", "query_string": s}  # noqa: E731
    check("a NUL in a query value is found and the parameter named",
          "'q'" in (offending(q(b"q=%00")) or ""), str(offending(q(b"q=%00"))))
    check("a raw NUL byte in the query string is found", offending(q(b"q=a\x00b")) is not None)
    check("a NUL in a query KEY is found", "name" in (offending(q(b"a%00b=1")) or ""))
    check("a later parameter is checked too", "'label'" in (offending(q(b"q=x&label=y%00")) or ""))
    check("`%2500` (the text \"%00\") is not a NUL", offending(q(b"q=%2500")) is None)
    check("a clean query passes", offending(q(b"q=hello+world&page=2")) is None)
    check("a NUL in the decoded path is found",
          offending({"type": "http", "path": "/api/adrs/\x00", "query_string": b""}) is not None)

    app.startup()
    with app.session() as s:
        if not s.scalar(select(AuthUser).where(AuthUser.subject == "verify-nul-owner")):
            s.add(AuthUser(id="u-nul-owner", subject="verify-nul-owner", name="verify-nul-owner",
                           role="owner", is_service=False, must_change_password=False,
                           password_hash=passwords.hash_password(OWNER_PW),
                           created_at=datetime.now(timezone.utc)))
            s.commit()
    owner = client()
    sign_in(owner, "verify-nul-owner", OWNER_PW)

    print("\n-- B. signed in --")
    for url, param in (("/api/search?q=%00", "q"), ("/api/workitems?q=%00", "q"),
                       ("/api/workitems?label=%00", "label"), ("/api/workitems?status=open%00", "status"),
                       ("/api/adrs?q=%00", "q"),
                       # The audit export (#137/#139): its query reaches Postgres the same way.
                       ("/api/audit/export?since=%00", "since"),
                       ("/api/audit/export?kinds=graph_event%00", "kinds")):
        r = owner.get(url)
        detail = r.json().get("detail", "") if r.headers.get("content-type", "").startswith("application/json") else ""
        check(f"{url} is a 400 naming '{param}'", r.status_code == 400 and f"'{param}'" in str(detail),
              f"{r.status_code} {r.text[:120]}")
    for url in ("/api/adrs/%00", "/api/node/%00", "/api/node/%00/structure"):
        r = owner.get(url)
        check(f"{url} (NUL in the path) is a 400", r.status_code == 400, f"{r.status_code} {r.text[:120]}")
    for url in ("/api/search?q=atlas", "/api/search?q=%2500", "/api/workitems?q=x", "/api/adrs?q=x",
                "/api/adrs?q=%2500"):
        r = owner.get(url)
        check(f"{url} still answers 200", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

    print("\n-- C. signed out: the gate answers first --")
    anon = client()
    clean = anon.get("/api/search?q=x", follow_redirects=False).status_code
    nul = anon.get("/api/search?q=%00", follow_redirects=False).status_code
    check("an unauthenticated NUL request gets the gate's answer, not the guard's 400",
          nul == clean and nul != 400, f"clean {clean}, nul {nul}")

    print("\n-- D. installed once, inside the gate --")
    kinds = [m.cls for m in app.app.user_middleware]
    check("NulGuard is installed exactly once", kinds.count(NulGuard) == 1, str(kinds))
    gate = getattr(auth, "AuthGate", None)
    check("NulGuard sits inside AuthGate (after it in user_middleware)",
          gate in kinds and NulGuard in kinds and kinds.index(gate) < kinds.index(NulGuard), str(kinds))

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("verify_nul_bytes: all checks passed")


if __name__ == "__main__":
    main()
