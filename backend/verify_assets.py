"""
Verification for `/assets/{name:path}` — the allowlist that serves the board's
files, widened on 2026-10-22 so board v2's layered tree can be served (ADR 0043,
TASK-1303).

The widening is safe only if it changes WHICH STRINGS reach the lookup and not
what the lookup accepts. So the claims are about the lookup, both ways round:

  A. THE LEGACY BOARD IS UNCHANGED. A flat key is still served, with the
     cache header that `verify_node_access.py` F asserts on the constant.
  B. A LISTED NESTED PATH IS SERVED. `v2/core/probe.js` registered in
     `ASSET_FILES` answers 200 with its bytes.
  C. AN UNLISTED NESTED PATH 404s, EVEN WHEN THE FILE IS ON DISK. This is the
     half that proves it is an allowlist and not a directory: a file sitting in
     the very directory a listed file comes from is still refused.
  D. TRAVERSAL GOES NOWHERE. Plain, percent-encoded and backslash `..` forms
     never return the secret placed one level ABOVE the frontend directory.
  E. THE GATE STILL COVERS IT. A signed-out caller gets the same refusal for a
     nested asset as for a flat one.
  G. THE CUTOVER (2026-10-22). `/board` serves `v2/index.html`, `/board/legacy`
     the legacy page, both gated; `/board/v2` redirects to `/board` with its
     query.
  F. EVERY FILE UNDER `frontend/v2/` IS LISTED. This mirrors the CI job, so a
     run on the workstation catches the 404-on-live defect without CI. The file
     list is DERIVED from the disk, never pinned.

A, B, C and D run against a throwaway frontend directory, so no fixture file
ever lands in the repo. F reads the real `frontend/` beside this file.

Run (from a venv with the backend requirements installed):
    python verify_assets.py
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb  # noqa: E402

REPO_FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

# A throwaway frontend, with the secret one level ABOVE it, which is where a
# successful `../` would land.
ROOT = tempfile.mkdtemp(prefix="atlas-assets-")
FRONT = os.path.join(ROOT, "frontend")
os.makedirs(os.path.join(FRONT, "v2", "core"))
SECRET = "SECRET-must-never-be-served"
PROBE = "export const probe = 'v2-probe';\n"
with open(os.path.join(ROOT, "secret.txt"), "w", encoding="utf-8", newline="") as fh:
    fh.write(SECRET)
with open(os.path.join(FRONT, "board.css"), "w", encoding="utf-8", newline="") as fh:
    fh.write("/* flat */\n")
with open(os.path.join(FRONT, "v2", "core", "probe.js"), "w", encoding="utf-8", newline="") as fh:
    fh.write(PROBE)
with open(os.path.join(FRONT, "v2", "core", "unlisted.js"), "w", encoding="utf-8", newline="") as fh:
    fh.write("export const unlisted = 1;\n")
with open(os.path.join(FRONT, "board.html"), "w", encoding="utf-8", newline="") as fh:
    fh.write("<!-- legacy board -->\n")
with open(os.path.join(FRONT, "v2", "index.html"), "w", encoding="utf-8", newline="") as fh:
    fh.write('<!-- board v2 --><script type="module" src="/assets/v2/app.js"></script>\n')

DB = testdb.start()
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ["ATLAS_FRONTEND_DIR"] = FRONT
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")

from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthUser  # noqa: E402

failures: list[str] = []
ORIGIN = "https://testserver"
PW = "Correct-Horse-Battery-Assets-7!"


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def sign_in() -> TestClient:
    c = TestClient(app.app, base_url=ORIGIN)
    page = c.get("/auth/login")
    tok = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": "a-member", "password": PW, "csrf_token": tok},
           headers={"Origin": ORIGIN}, follow_redirects=False)
    return c


def main() -> int:
    app.startup()
    with app.session() as s:
        s.add(AuthUser(id="u-a-member", subject="a-member", name="a-member", role="member",
                       is_service=False, must_change_password=False,
                       password_hash=passwords.hash_password(PW),
                       created_at=datetime.now(timezone.utc)))
        s.commit()
    c = sign_in()
    check("(setup) the member is signed in", c.get("/api/me").status_code == 200)

    # The probe is registered for this run only. Nothing in the repo lists it.
    app.ASSET_FILES["v2/core/probe.js"] = "v2/core/probe.js"

    print("\n-- A. the legacy board's flat keys are unchanged --")
    r = c.get("/assets/board.css")
    check("a flat key is still served", r.status_code == 200 and "flat" in r.text, f"{r.status_code}")
    check("...with the shared-cache-forbidding header",
          r.headers.get("cache-control") == app.ASSET_CACHE_CONTROL, str(r.headers.get("cache-control")))

    print("\n-- B. a listed nested path is served --")
    r = c.get("/assets/v2/core/probe.js")
    check("a listed nested path answers 200 with its bytes", r.status_code == 200 and r.text == PROBE,
          f"{r.status_code} {r.text[:60]!r}")
    check("...with the same cache header", r.headers.get("cache-control") == app.ASSET_CACHE_CONTROL,
          str(r.headers.get("cache-control")))

    print("\n-- C. an unlisted nested path 404s, even when the file is on disk --")
    r = c.get("/assets/v2/core/unlisted.js")
    check("a file beside a listed one, but itself unlisted, is refused", r.status_code == 404,
          f"{r.status_code} {r.text[:60]!r}")
    r = c.get("/assets/v2/core/")
    check("a directory is not listed and not served", r.status_code == 404, f"{r.status_code}")
    r = c.get("/assets/v2/core/missing.js")
    check("a name on no list and no disk 404s", r.status_code == 404, f"{r.status_code}")

    print("\n-- D. traversal goes nowhere --")
    for path in ("/assets/../secret.txt", "/assets/v2/../../secret.txt",
                 "/assets/%2e%2e/secret.txt", "/assets/v2/core/%2e%2e%2f%2e%2e%2f%2e%2e%2fsecret.txt",
                 "/assets/..%5csecret.txt", "/assets/v2%2f..%2f..%2fsecret.txt"):
        r = c.get(path)
        check(f"no secret through {path}", r.status_code != 200 and SECRET not in r.text,
              f"{r.status_code} {r.text[:60]!r}")

    print("\n-- E. the gate still covers nested assets --")
    anon = TestClient(app.app, base_url=ORIGIN)
    flat = anon.get("/assets/board.css", headers={"Accept": "application/json"})
    nested = anon.get("/assets/v2/core/probe.js", headers={"Accept": "application/json"})
    check("a signed-out caller is refused a nested asset", nested.status_code == 401, f"{nested.status_code}")
    check("...exactly as for a flat one", (nested.status_code, nested.json()) == (flat.status_code, flat.json()),
          f"{nested.status_code} vs {flat.status_code}")
    html = anon.get("/assets/v2/core/probe.js", headers={"Accept": "text/html"}, follow_redirects=False)
    check("...and a browser is sent to the login page", html.status_code == 302
          and (html.headers.get("location") or "").startswith("/auth/login"), f"{html.status_code}")

    print("\n-- G. the cutover: /board is v2, /board/legacy the old board --")
    r = c.get("/board")
    check("/board serves v2/index.html to a signed-in caller", r.status_code == 200 and "board v2" in r.text,
          f"{r.status_code} {r.text[:60]!r}")
    r = c.get("/board/legacy")
    check("/board/legacy serves the legacy page", r.status_code == 200 and "legacy board" in r.text,
          f"{r.status_code} {r.text[:60]!r}")
    r = c.get("/board/v2?node=x&pane=board", follow_redirects=False)
    check("/board/v2 redirects to /board and keeps its query", r.status_code == 301
          and r.headers.get("location") == "/board?node=x&pane=board", f"{r.status_code} {r.headers.get('location')}")
    r = c.get("/board/v2", follow_redirects=False)
    check("...and without a query, to bare /board", r.headers.get("location") == "/board", f"{r.headers.get('location')}")
    import auth
    for path in ("/board", "/board/legacy", "/board/v2"):
        r = anon.get(path, headers={"Accept": "text/html"}, follow_redirects=False)
        check(f"{path} sends a signed-out browser to the login page", r.status_code == 302
              and (r.headers.get("location") or "").startswith("/auth/login"), f"{r.status_code}")
        check(f"{path} is not a public route", path not in auth.PUBLIC_ROUTES)

    print("\n-- F. every file under frontend/v2/ is listed --")
    del app.ASSET_FILES["v2/core/probe.js"]
    listed = set(app.ASSET_FILES.values())
    v2 = os.path.join(REPO_FRONTEND, "v2")
    on_disk = []
    for dirpath, _dirs, files in os.walk(v2):
        for f in files:
            # index.html is served by `/board/v2`, not by the allowlist (G above).
            # test-support/ and *.test.js are node --test material, never served.
            if "test-support" in dirpath.replace(os.sep, "/").split("/"):
                continue
            if f.endswith((".js", ".css")) and not f.endswith(".test.js"):
                on_disk.append(os.path.relpath(os.path.join(dirpath, f), REPO_FRONTEND).replace(os.sep, "/"))
    missing = sorted(p for p in on_disk if p not in listed)
    check("every served v2 file is in ASSET_FILES", not missing,
          ("MISSING: " + ", ".join(missing)) if missing else f"{len(on_disk)} file(s) under frontend/v2/")
    stale = sorted(k for k, p in app.ASSET_FILES.items()
                   if p.startswith("v2/") and not os.path.isfile(os.path.join(REPO_FRONTEND, *p.split("/"))))
    check("no v2 ASSET_FILES entry names a file that does not exist", not stale,
          ("STALE: " + ", ".join(stale)) if stale else "")
    keyed = sorted(k for k, p in app.ASSET_FILES.items() if p.startswith("v2/") and k != p)
    check("every v2 key equals its path, so the URL and the file agree", not keyed, ", ".join(keyed))

    print()
    if failures:
        print(f"FAILED: {len(failures)} claim(s)")
        for f in failures:
            print("  - " + f)
        return 1
    print("ALL CLAIMS PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        DB.stop()
