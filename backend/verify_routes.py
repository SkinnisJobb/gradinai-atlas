"""The route table is a contract: this pins it to `routes.snapshot.txt`.

Written for STORY-oss-04 (split `app.py` into routers), where the whole claim
is "nothing a caller can reach moved". It stays afterwards because the claim
outlives the refactor: a route that disappears, changes method or changes its
endpoint name should be a deliberate edit to the snapshot in the same commit,
never a side effect somebody notices on the board.

Two checks, both against the LIVE app object (the same `app.app` uvicorn
serves), not against the source:

  A. Every (method, path, endpoint name) the app registers equals the committed
     snapshot, line for line. The snapshot is SORTED, so moving a route between
     modules does not show up as a diff; adding, removing or renaming one does.

  B. Sorting hides order, and order is behaviour: Starlette answers with the
     FIRST route that matches. So for every route, a request built from its own
     path template must be answered by that route and not by an earlier one.
     That is the property order exists to give, checked directly, so it holds
     however the routers are arranged.

No database is touched: `create_engine` does not connect, and nothing here
calls `startup()`. The env defaults below exist only so the fail-closed
imports in `auth`/`passwords` can load; they never reach a real deployment.

    python verify_routes.py            # check (what CI and verify_all.py run)
    python verify_routes.py --write    # regenerate the snapshot after a deliberate change
"""
from __future__ import annotations

import base64
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / "routes.snapshot.txt"

os.environ.setdefault("ATLAS_DB_URL", "postgresql+psycopg2://u:p@127.0.0.1:1/none")
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
sys.path.insert(0, str(HERE))

# STORY-oss-08: this script pins the DEFAULT route table (routes.snapshot.txt),
# so it runs with every feature on whatever the caller set; verify_feature_flags.py
# proves the switched-off side. Before the app import: main.py reads the flags then.
os.environ.pop("ATLAS_DISABLED_FEATURES", None)
import app  # noqa: E402  — must follow the env defaults above

from starlette.routing import Match  # noqa: E402

from route_table import live_routes, route_class  # noqa: E402  (TASK-1361: app.routes is no longer flat)

FAILS: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


def route_lines() -> list[str]:
    out = []
    for r in live_routes(app.app):
        path = getattr(r, "path", None)
        if path is None:
            continue
        methods = sorted(getattr(r, "methods", None) or ["*"])
        for m in methods:
            out.append(f"{m} {path} {getattr(r, 'name', '')} {route_class(r)}")
    return sorted(out)


def _sample(path: str) -> str:
    # `{x:path}` may hold slashes; every other param is one segment.
    path = re.sub(r"\{[^}:]+:path\}", "a/b", path)
    return re.sub(r"\{[^}]+\}", "x1", path)


def shadowed() -> list[str]:
    bad = []
    # Flattened in dispatch order (route_table.py): Starlette answers with the first
    # full match, and FastAPI resolves an included router's routes in place, in order.
    routes = [r for r in live_routes(app.app) if getattr(r, "path", None) is not None]
    for r in routes:
        for method in sorted(getattr(r, "methods", None) or ["GET"]):
            scope = {"type": "http", "path": _sample(r.path), "method": method,
                     "root_path": "", "headers": []}
            winner = next((c for c in routes if c.matches(scope)[0] == Match.FULL), None)
            if winner is not r:
                bad.append(f"{method} {r.path} -> answered by "
                           f"{getattr(winner, 'name', None)} {getattr(winner, 'path', None)}")
    return bad


def main() -> int:
    lines = route_lines()
    if "--write" in sys.argv:
        SNAPSHOT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {len(lines)} routes to {SNAPSHOT.name}")
        return 0

    print("\n-- A. the route table equals routes.snapshot.txt --")
    want = SNAPSHOT.read_text(encoding="utf-8").splitlines()
    missing = sorted(set(want) - set(lines))
    extra = sorted(set(lines) - set(want))
    check(f"{len(lines)} registered routes, {len(want)} in the snapshot, no difference",
          lines == want, f"missing {missing}; unexpected {extra}")

    print("\n-- B. no route is shadowed by an earlier one --")
    bad = shadowed()
    check("every route answers a request built from its own path template", not bad, "; ".join(bad))

    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAIL'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
