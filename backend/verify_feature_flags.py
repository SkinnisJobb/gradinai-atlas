"""
Verification for STORY-oss-08-plugins: `ATLAS_DISABLED_FEATURES`.

The personal/homelab features stay where they are and gain an off switch, on by
default. The claims, in the order a reviewer would doubt them:

  A. THE SETTING. `settings.load_features()`: unset or empty is "everything on";
     names are trimmed and case-folded; an unknown name raises `SettingsError`
     naming it; asking code for a feature that does not exist is a KeyError.
  B. THE ROUTE TABLE, one child process per configuration (main.py reads the
     flags at import, so one process is one configuration, as in a container):
       - unset, and empty, register exactly `routes.snapshot.txt`;
       - `github_link` off registers the snapshot minus exactly the GitHub
         routes, and nothing else moves; every feature off is the same table;
       - an unknown name stops the import, naming it (no half-booted app).
  C. THROUGH THE REAL GATE, every feature off (this process): a signed-in
     owner gets 404 from every `/api/me/github*` and `/api/access/github/*`
     route, identical to a path that never existed; the rest of `/api/me` and
     `/api/access` still answers; `/api/health` answers without `deploy` and with
     every other field.

With the default configuration, the existing suite IS the proof that nothing
changed (`verify_routes.py`, `verify_github_link.py`, `verify_deploy_status.py`,
`verify_object_graph.py`'s health section); this script does not repeat it.

    python verify_feature_flags.py
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ALL = "github_link,save_work,deploy_status,deploy_warning,attention_probes"
ENV = "ATLAS_DISABLED_FEATURES"
GITHUB_PREFIXES = ("/api/me/github", "/api/access/github/")

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


# --------------------------------------------------------------------------- #
# A. the setting
# --------------------------------------------------------------------------- #
import settings  # noqa: E402

print("\n-- A. ATLAS_DISABLED_FEATURES through settings.load_features --")
check("FEATURES is the story's five names",
      settings.FEATURES == ("github_link", "save_work", "deploy_status", "deploy_warning",
                            "attention_probes"), str(settings.FEATURES))
for label, env in (("unset", {}), ("empty", {ENV: ""}), ("blank", {ENV: " , ,"})):
    f = settings.load_features(env)
    check(f"{label}: every feature is on",
          not f.disabled and all(f.enabled(n) for n in settings.FEATURES), str(f))
f = settings.load_features({ENV: " GitHub_Link , deploy_warning,"})
check("names are trimmed and case-folded; only the named ones are off",
      f.disabled == {"github_link", "deploy_warning"} and f.enabled("save_work")
      and not f.enabled("github_link"), str(f))
f = settings.load_features({ENV: ALL})
check("every name at once: every feature is off",
      all(not f.enabled(n) for n in settings.FEATURES), str(f))
check("feature_enabled() is the same answer", settings.feature_enabled("save_work", {ENV: ALL}) is False
      and settings.feature_enabled("save_work", {}) is True)
try:
    settings.load_features({ENV: "github_link,githb_link"})
    check("an unknown name raises", False, "nothing raised")
except settings.SettingsError as exc:
    check("an unknown name raises SettingsError naming it and the variable",
          "githb_link" in str(exc) and ENV in str(exc) and "github_link" in str(exc), str(exc))
# Perturbation: the check above must fail for a name that IS valid.
try:
    settings.load_features({ENV: "github_link"})
    check("(perturbation) a valid name does not raise", True)
except settings.SettingsError as exc:
    check("(perturbation) a valid name does not raise", False, str(exc))
try:
    settings.Features().enabled("no_such_feature")
    check("asking for a feature that does not exist is an error in code", False, "nothing raised")
except KeyError:
    check("asking for a feature that does not exist is an error in code", True)

# --------------------------------------------------------------------------- #
# B. the route table, one child per configuration
# --------------------------------------------------------------------------- #
CHILD_ENV = {k: v for k, v in os.environ.items() if k != ENV}
CHILD_ENV.update({
    "ATLAS_DB_URL": "postgresql+psycopg2://u:p@127.0.0.1:1/none",   # create_engine does not connect
    "ATLAS_PASSWORD_PEPPER": base64.b64encode(os.urandom(32)).decode(),
    "ATLAS_TOKEN_PEPPER": base64.b64encode(os.urandom(32)).decode(),
    "ATLAS_BREACH_CHECK": "bundled",
    "ATLAS_LOGIN_ORIGINS": "https://testserver",
})
ROUTES = r'''
import json, app
from route_table import live_routes, route_class
out = []
for r in live_routes(app.app):
    path = getattr(r, "path", None)
    if path is None:
        continue
    for m in sorted(getattr(r, "methods", None) or ["*"]):
        out.append(f"{m} {path} {getattr(r, 'name', '')} {route_class(r)}")
print("ROUTES " + json.dumps(sorted(out)))
'''


def routes_for(value: str | None) -> tuple[list[str] | None, subprocess.CompletedProcess]:
    env = dict(CHILD_ENV)
    if value is not None:
        env[ENV] = value
    proc = subprocess.run([sys.executable, "-c", ROUTES], cwd=HERE, env=env,
                          capture_output=True, text=True, timeout=120)
    for line in proc.stdout.splitlines():
        if line.startswith("ROUTES "):
            return json.loads(line[len("ROUTES "):]), proc
    return None, proc


print("\n-- B. the route table per configuration --")
snapshot = (HERE / "routes.snapshot.txt").read_text(encoding="utf-8").splitlines()
github_lines = sorted(l for l in snapshot if l.split(" ")[1].startswith(GITHUB_PREFIXES))
check("the snapshot carries the GitHub routes this story can switch off (6)",
      len(github_lines) == 6, "\n".join(github_lines))

default, p = routes_for(None)
check("unset: the table is routes.snapshot.txt, line for line", default == snapshot,
      p.stderr[-800:] if default is None else
      f"missing {sorted(set(snapshot) - set(default))}; extra {sorted(set(default) - set(snapshot))}")
empty, p = routes_for("")
check("empty: the same table as unset", empty == snapshot, p.stderr[-800:])

off, p = routes_for("github_link")
want = sorted(set(snapshot) - set(github_lines))
check("github_link off: the snapshot minus exactly the GitHub routes", off == want,
      p.stderr[-800:] if off is None else
      f"missing {sorted(set(want) - set(off))}; extra {sorted(set(off) - set(want))}")
alloff, p = routes_for(ALL)
check("every feature off: the same table as github_link off (no other feature is a route)",
      alloff == want, p.stderr[-800:])
others, p = routes_for("save_work,deploy_status,deploy_warning,attention_probes")
check("every feature but github_link off: the full snapshot", others == snapshot, p.stderr[-800:])

bad, p = routes_for("github_link,typo_feature")
check("an unknown name stops the import with SettingsError naming it",
      bad is None and p.returncode != 0 and "SettingsError" in p.stderr and "typo_feature" in p.stderr,
      f"rc={p.returncode} {p.stderr[-400:]}")

# --------------------------------------------------------------------------- #
# C. through the real gate, every feature off
# --------------------------------------------------------------------------- #
print("\n-- C. through the real gate, ATLAS_DISABLED_FEATURES = every feature --")
import testdb  # noqa: E402

DB = testdb.start()
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
os.environ[ENV] = ALL

from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthUser  # noqa: E402

ORIGIN = "https://testserver"
PW = "Correct-Horse-Battery-Flags-5!"


def sign_in(username: str):
    c = TestClient(app.app, base_url=ORIGIN)
    page = c.get("/auth/login")
    tok = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": username, "password": PW, "csrf_token": tok},
           headers={"Origin": ORIGIN}, follow_redirects=False)
    return c, c.get("/api/me").json()["csrf_token"]


def main() -> int:
    app.startup()
    with app.session() as s:
        s.add(AuthUser(id="u-ff-owner", subject="ff-owner", name="ff-owner", role="owner",
                       is_service=False, must_change_password=False,
                       password_hash=passwords.hash_password(PW),
                       created_at=datetime.now(timezone.utc)))
        s.commit()
    owner, csrf = sign_in("ff-owner")
    check("the owner is signed in", bool(csrf))

    def never_existed(method: str, path: str):
        return owner.request(method, path, json={"csrf_token": csrf} if method != "GET" else None,
                             headers={"Origin": ORIGIN}, follow_redirects=False)

    ghost = never_existed("GET", "/api/me/no-such-route")
    check("baseline: a path that never existed is a 404", ghost.status_code == 404, ghost.text[:120])
    for method, path in (("GET", "/api/me/github"), ("GET", "/api/me/github/start"),
                         ("GET", "/api/me/github/callback"), ("POST", "/api/me/github/unlink"),
                         ("PUT", "/api/access/github/u-ff-owner"),
                         ("POST", "/api/access/github/u-ff-owner/refresh")):
        r = owner.request(method, path,
                          json=({"csrf_token": csrf, "repo_access": True} if method != "GET" else None),
                          headers={"Origin": ORIGIN}, follow_redirects=False)
        same = never_existed(method, path.replace("github", "no-such-route"))
        check(f"{method} {path}: 404, the same answer as a path that never existed",
              r.status_code == 404 and r.status_code == same.status_code and r.content == same.content,
              f"{r.status_code} {r.text[:120]} vs {same.status_code} {same.text[:120]}")

    r = owner.get("/api/me")
    check("the rest of /api/me still answers", r.status_code == 200, r.text[:120])
    r = owner.get("/api/access")
    check("the rest of /api/access still answers", r.status_code == 200, r.text[:120])

    r = owner.get("/api/health")
    body = r.json() if r.status_code == 200 else {}
    check("/api/health answers 200 without a `deploy` field",
          r.status_code == 200 and "deploy" not in body, r.text[:200])
    check("/api/health keeps every other field",
          {"ok", "node_count", "edge_count", "token_warnings", "edge_type_warnings"} <= set(body),
          str(sorted(body)))

    print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAIL'}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        DB.stop()
    sys.exit(rc)
