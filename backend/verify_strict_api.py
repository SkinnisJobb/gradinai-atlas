"""
Verification for STORY-oss-14-strict-api: workitem `priority` and `labels`, and the opt-in
strict API (`ATLAS_STRICT_API`), through the REAL routes and the REAL auth gate, against a
disposable Postgres (`testdb`) seeded from `testseed`.

  A. settings: `settings.load_strict_api` — unset is today (False), 1/true/yes/on is strict,
     0/false/no/off is today, set-but-blank and any other word raise naming the variable;
     and a blank value stops `import app` in a child process (main.py loads it at import).
  B. DEFAULT (unset): today's payloads are accepted unchanged. A clean request's response has
     no `warnings` key at all; a request carrying a field the route ignores is still a 2xx,
     writes what it wrote before, and names the field in `warnings` — on POST/PATCH
     /api/node and POST/PATCH /api/workitem, plus the two DECLARED fields a workitem route
     reads and does not apply (`attributes.depends_on`, `approval` without a rail).
  C. STRICT (ATLAS_STRICT_API=1, read per request): the same requests are a 422 naming the
     field, and nothing was written (no node row, no change, no task id spent on a create).
     A clean request is unaffected.
  D. priority and labels round-trip: create, read back through GET /api/node and the
     listing, PATCH replace, omit leaves, null clears; refusals are 400s naming the field;
     the PATCH is recorded in graph_event like any field.
  E. searchable: GET /api/workitems?priority= (incl. `none`) and ?label= (any of), an
     unknown priority a 400 naming the field; GET /api/search finds a labelled item by its
     label, with `labels` in `matched_in`, and finds nothing for a label nobody carries.
  F. the columns arrive on a POPULATED table: drop them, run the real
     `_reconcile_graph_schema`, and every existing row reads `priority: null, labels: []`,
     with both indexes back.

Run (from a venv with the backend requirements installed):
    python verify_strict_api.py
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, inspect as sa_inspect, select, text

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:
    print(f"FATAL: {e}")
    sys.exit(1)
os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
# The subject of this script is the setting: start from its default, whatever the runner set.
os.environ.pop("ATLAS_STRICT_API", None)

TEST_ORIGIN = "https://testserver"
H = {"Origin": TEST_ORIGIN}

import app  # noqa: E402
import main  # noqa: E402
import passwords  # noqa: E402
import settings  # noqa: E402
from models_auth import AuthUser  # noqa: E402
from models_graph import GraphEvent, Node  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def _client():
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=TEST_ORIGIN)


def _sign_in(client) -> str:
    username, password = "verify-strict-api", "Correct-Horse-Battery-9!"
    with app.session() as s:
        if not s.scalar(select(AuthUser).where(AuthUser.subject == username)):
            s.add(AuthUser(
                id="user-verify-strict-api", subject=username, name="Verify Strict API",
                role="owner", is_service=False, must_change_password=False,
                password_hash=passwords.hash_password(password),
                created_at=datetime.now(timezone.utc),
            ))
            s.commit()
    page = client.get("/auth/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    client.post("/auth/login",
                data={"username": username, "password": password, "csrf_token": token},
                headers=H, follow_redirects=False)
    return client.get("/api/me").json()["csrf_token"]


def _strict(on: bool) -> None:
    if on:
        os.environ["ATLAS_STRICT_API"] = "1"
    else:
        os.environ.pop("ATLAS_STRICT_API", None)


def _node(node_id: str):
    with app.session() as s:
        n = s.get(Node, node_id)
        if n is not None:
            s.expunge(n)
        return n


def _count_named(name: str) -> int:
    with app.session() as s:
        return s.scalar(select(func.count()).select_from(Node).where(Node.name == name))


def _warned(resp, field: str, code: str) -> bool:
    return any(w.get("field") == field and w.get("code") == code
               for w in (resp.json().get("warnings") or []))


# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. ATLAS_STRICT_API through settings.load_strict_api --")
    check("unset -> False (today)", settings.load_strict_api({}) is False)
    for v in ("1", "true", "YES", " on "):
        check(f"{v!r} -> True", settings.load_strict_api({"ATLAS_STRICT_API": v}) is True)
    for v in ("0", "false", "no", "off"):
        check(f"{v!r} -> False", settings.load_strict_api({"ATLAS_STRICT_API": v}) is False)
    for v in ("", "   ", "strict", "2"):
        try:
            settings.load_strict_api({"ATLAS_STRICT_API": v})
            check(f"{v!r} raises SettingsError naming the variable", False, "nothing raised")
        except settings.SettingsError as exc:
            check(f"{v!r} raises SettingsError naming the variable", "ATLAS_STRICT_API" in str(exc),
                  str(exc))

    env = {k: v for k, v in os.environ.items() if k != "ATLAS_STRICT_API"}
    env.update({"ATLAS_DB_URL": "postgresql+psycopg2://u:p@127.0.0.1:1/none",
                "ATLAS_STRICT_API": ""})
    proc = subprocess.run([sys.executable, "-c", "import app"], cwd=HERE, env=env,
                          capture_output=True, text=True, timeout=120)
    check("a set-but-blank ATLAS_STRICT_API stops `import app` (the boot), naming it",
          proc.returncode != 0 and "ATLAS_STRICT_API" in (proc.stderr + proc.stdout),
          f"exit {proc.returncode}: {(proc.stderr or proc.stdout)[-200:]}")


# --------------------------------------------------------------------------- #
def section_b(client, csrf) -> dict:
    print("\n-- B. default: today's payloads unchanged, an ignored field is a warning --")
    _strict(False)

    def post(path, payload):
        return client.post(path, json=dict(payload, csrf_token=csrf), headers=H)

    def patch(path, payload):
        return client.patch(path, json=dict(payload, csrf_token=csrf), headers=H)

    # Clean requests, exactly as callers send them today: no `warnings` key at all.
    r = post("/api/node", {"id": "strict-b-proj", "type": "project", "name": "strict b project"})
    check("POST /api/node, clean -> 201 with NO warnings key", r.status_code == 201
          and "warnings" not in r.json(), f"{r.status_code} {sorted(r.json())[:30]}")
    r = patch("/api/node/strict-b-proj", {"summary": "clean"})
    check("PATCH /api/node, clean -> 200 with NO warnings key", r.status_code == 200
          and "warnings" not in r.json(), f"{r.status_code} {r.text[:150]}")
    r = post("/api/workitem", {"type": "task", "name": "strict b clean"})
    clean_id = r.json().get("id")
    check("POST /api/workitem, clean -> 201 with NO warnings key", r.status_code == 201
          and "warnings" not in r.json(), f"{r.status_code} {r.text[:150]}")
    r = patch(f"/api/workitem/{clean_id}", {"status": "in_progress"})
    check("PATCH /api/workitem, clean -> 200 with NO warnings key", r.status_code == 200
          and "warnings" not in r.json(), f"{r.status_code} {r.text[:150]}")

    # Unknown fields: accepted, the known part applied, the unknown one named.
    r = post("/api/node", {"id": "strict-b-typo", "type": "project", "name": "typo node",
                           "summray": "misspelt", "priority": "high"})
    check("POST /api/node with unknown keys -> still 201", r.status_code == 201, f"{r.status_code}")
    check("...warnings name `summray` and `priority` as unknown_field",
          _warned(r, "summray", "unknown_field") and _warned(r, "priority", "unknown_field"),
          str(r.json().get("warnings")))
    check("...and the node was created as before", _node("strict-b-typo") is not None)
    r = patch("/api/node/strict-b-typo", {"name": "typo node renamed", "status": "done"})
    check("PATCH /api/node with an unknown `status` -> 200, warning names it",
          r.status_code == 200 and _warned(r, "status", "unknown_field"), r.text[:200])
    n = _node("strict-b-typo")
    check("...the known field was applied", n is not None and n.name == "typo node renamed")

    r = post("/api/workitem", {"type": "task", "name": "strict b unknown", "colour": "red"})
    b_id = r.json().get("id")
    check("POST /api/workitem with an unknown key -> 201, warning names it",
          r.status_code == 201 and _warned(r, "colour", "unknown_field"), r.text[:200])
    r = patch(f"/api/workitem/{b_id}", {"name": "strict b renamed", "colour": "blue"})
    check("PATCH /api/workitem with an unknown key -> 200, warning names it",
          r.status_code == 200 and _warned(r, "colour", "unknown_field"), r.text[:200])
    check("...and the known field was applied", (_node(b_id) or Node()).name == "strict b renamed")

    # Declared but not applied.
    r = post("/api/workitem", {"type": "task", "name": "strict b mirror",
                               "attributes": {"depends_on": [clean_id]}})
    mirror_id = r.json().get("id")
    check("POST /api/workitem with attributes.depends_on -> 201, ignored_field warning",
          r.status_code == 201 and _warned(r, "attributes.depends_on", "ignored_field"), r.text[:200])
    check("...and the mirror was still stripped, as before",
          "depends_on" not in ((_node(mirror_id) or Node()).attributes or {}))
    r = patch(f"/api/workitem/{b_id}", {"acceptance": ["a criterion"],
                                        "approval": {"by": "someone", "quote": "fine"}})
    check("a session's PATCH with acceptance + approval -> 200, `approval` is an ignored_field",
          r.status_code == 200 and _warned(r, "approval", "ignored_field"), r.text[:240])
    check("...and the acceptance was written, as before", r.json().get("acceptance") == ["a criterion"])
    check("csrf_token is never reported (transport, declared on every body)",
          not _warned(r, "csrf_token", "unknown_field"))
    return {"clean_id": clean_id, "b_id": b_id}


# --------------------------------------------------------------------------- #
def section_c(client, csrf, ids) -> None:
    print("\n-- C. strict: the same requests are a 422 naming the field, nothing written --")
    _strict(True)
    try:
        def post(path, payload):
            return client.post(path, json=dict(payload, csrf_token=csrf), headers=H)

        def patch(path, payload):
            return client.patch(path, json=dict(payload, csrf_token=csrf), headers=H)

        def refused(r, field):
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            return r.status_code == 422 and body.get("field") == field and field in body.get("fields", [])

        r = post("/api/node", {"id": "strict-c-typo", "type": "project", "name": "c", "summray": "x"})
        check("POST /api/node with `summray` -> 422 naming it", refused(r, "summray"), r.text[:200])
        check("...and no node was created", _node("strict-c-typo") is None)

        r = patch(f"/api/node/strict-b-proj", {"name": "should not land", "status": "done"})
        check("PATCH /api/node with `status` -> 422 naming it", refused(r, "status"), r.text[:200])
        check("...and the name did not change", (_node("strict-b-proj") or Node()).name == "strict b project")

        with app.session() as s:
            seq_before = s.scalar(text("SELECT last_value FROM task_id_seq"))
        r = post("/api/workitem", {"type": "task", "name": "strict c unknown", "colour": "red"})
        check("POST /api/workitem with `colour` -> 422 naming it", refused(r, "colour"), r.text[:200])
        check("...no node row", _count_named("strict c unknown") == 0)
        with app.session() as s:
            seq_after = s.scalar(text("SELECT last_value FROM task_id_seq"))
        check("...and no task id was spent", seq_before == seq_after, f"{seq_before} -> {seq_after}")

        r = patch(f"/api/workitem/{ids['b_id']}", {"name": "strict c renamed", "colour": "blue"})
        check("PATCH /api/workitem with `colour` -> 422 naming it", refused(r, "colour"), r.text[:200])
        check("...and the name did not change", (_node(ids["b_id"]) or Node()).name == "strict b renamed")

        r = post("/api/workitem", {"type": "task", "name": "strict c mirror",
                                   "attributes": {"depends_on": [ids["clean_id"]]}})
        check("attributes.depends_on -> 422 naming it", refused(r, "attributes.depends_on"), r.text[:200])
        check("...no node row", _count_named("strict c mirror") == 0)

        r = patch(f"/api/workitem/{ids['b_id']}", {"acceptance": ["other"],
                                                   "approval": {"by": "x", "quote": "y"}})
        check("an approval the rail did not need -> 422 naming `approval`", refused(r, "approval"),
              r.text[:200])
        check("...and the acceptance did not change",
              client.get(f"/api/node/{ids['b_id']}").json().get("acceptance") == ["a criterion"])

        r = post("/api/node", {"id": "strict-c-two", "type": "project", "name": "c", "a1": 1, "a2": 2})
        check("several unknown fields: all listed in `fields`",
              r.status_code == 422 and r.json().get("fields") == ["a1", "a2"], r.text[:200])

        r = post("/api/workitem", {"type": "task", "name": "strict c clean", "priority": "low"})
        check("a clean request is unaffected by strict mode -> 201, no warnings",
              r.status_code == 201 and "warnings" not in r.json(), r.text[:200])
        r = patch(f"/api/workitem/{r.json().get('id')}", {"labels": ["x"]})
        check("...and a clean PATCH too", r.status_code == 200 and "warnings" not in r.json(), r.text[:200])
    finally:
        _strict(False)
    r = client.post("/api/node", json={"id": "strict-c-after", "type": "project", "name": "c",
                                       "summray": "x", "csrf_token": csrf}, headers=H)
    check("strict off again (read per request) -> the same body is a 201 with a warning",
          r.status_code == 201 and _warned(r, "summray", "unknown_field"), r.text[:200])


# --------------------------------------------------------------------------- #
def section_d(client, csrf) -> str:
    print("\n-- D. priority and labels round-trip --")

    def post(payload):
        return client.post("/api/workitem", json=dict(payload, csrf_token=csrf), headers=H)

    def patch(wid, payload):
        return client.patch(f"/api/workitem/{wid}", json=dict(payload, csrf_token=csrf), headers=H)

    r = post({"type": "task", "name": "strict d tagged", "priority": "High",
              "labels": ["Backend", " auth ", "backend", "good  first issue"]})
    wid = r.json().get("id")
    check("create with priority and labels -> 201", r.status_code == 201, r.text[:200])
    check("...priority stored lowercase", r.json().get("priority") == "high", str(r.json().get("priority")))
    check("...labels stripped, lowercased, de-duplicated in order",
          r.json().get("labels") == ["backend", "auth", "good first issue"], str(r.json().get("labels")))
    got = client.get(f"/api/node/{wid}").json()
    check("GET /api/node returns both", got.get("priority") == "high"
          and got.get("labels") == ["backend", "auth", "good first issue"], str(got)[:200])

    r = post({"type": "task", "name": "strict d plain"})
    plain = r.json()
    check("an item without them reads priority null, labels []",
          plain.get("priority") is None and plain.get("labels") == [], str(plain)[:200])
    other = client.get("/api/node/seed-world-service").json()
    check("...and so does a world node (the keys are always present)",
          other.get("priority") is None and other.get("labels") == [], str(other)[:200])

    r = patch(wid, {"priority": "critical", "labels": ["auth", "infra"]})
    check("PATCH replaces both", r.status_code == 200 and r.json().get("priority") == "critical"
          and r.json().get("labels") == ["auth", "infra"], r.text[:200])
    r = patch(wid, {"name": "strict d tagged renamed"})
    check("PATCH that omits them leaves both", r.json().get("priority") == "critical"
          and r.json().get("labels") == ["auth", "infra"], r.text[:200])
    with app.session() as s:
        ev = s.scalars(select(GraphEvent).where(GraphEvent.entity_id == wid,
                                                GraphEvent.action == "node.update")
                       .order_by(GraphEvent.id)).all()
    check("the PATCH is recorded in graph_event, naming both fields",
          any({"priority", "labels"} <= set((e.detail or {}).get("fields", [])) for e in ev),
          str([e.detail for e in ev]))

    r = patch(plain["id"], {"priority": "low", "labels": ["temp"]})
    r = patch(plain["id"], {"priority": None, "labels": []})
    check("priority null and labels [] clear them", r.json().get("priority") is None
          and r.json().get("labels") == [], r.text[:200])
    with app.session() as s:
        row = s.get(Node, plain["id"])
        check("...stored as NULL, not an empty array", row.priority is None and row.labels is None,
              f"{row.priority!r} {row.labels!r}")
    patch(plain["id"], {"labels": ["temp"]})
    r = patch(plain["id"], {"labels": None})
    check("labels null clears too", r.json().get("labels") == [], r.text[:200])

    for label, payload, field in (
        ("an unknown priority", {"priority": "urgent"}, "priority"),
        ("an empty label", {"labels": ["ok", "  "]}, "labels"),
        ("a label with a comma", {"labels": ["a,b"]}, "labels"),
        ("a label over 40 characters", {"labels": ["x" * 41]}, "labels"),
        ("more than 20 labels", {"labels": [f"l{i}" for i in range(21)]}, "labels"),
    ):
        r = patch(wid, payload)
        check(f"{label} -> 400 naming `{field}`",
              r.status_code == 400 and r.json().get("field") == field, r.text[:200])
    check("...and none of them changed the item",
          client.get(f"/api/node/{wid}").json().get("labels") == ["auth", "infra"])
    with app.session() as s:
        seq_before = s.scalar(text("SELECT last_value FROM task_id_seq"))
    r = post({"type": "task", "name": "strict d bad", "priority": "p0"})
    with app.session() as s:
        seq_after = s.scalar(text("SELECT last_value FROM task_id_seq"))
    check("a create with a bad priority -> 400, no row, no task id spent",
          r.status_code == 400 and _count_named("strict d bad") == 0 and seq_before == seq_after,
          f"{r.status_code} {seq_before}->{seq_after}")

    vocab = client.get("/api/vocabulary").json()
    check("/api/vocabulary lists the priorities, lowest first",
          vocab.get("workitem_priorities") == ["low", "medium", "high", "critical"],
          str(vocab.get("workitem_priorities")))
    return wid


# --------------------------------------------------------------------------- #
def section_e(client, csrf, wid) -> None:
    print("\n-- E. searchable: list filters and /api/search --")

    def post(payload):
        return client.post("/api/workitem", json=dict(payload, csrf_token=csrf), headers=H).json()["id"]

    low = post({"type": "task", "name": "strict e low", "priority": "low", "labels": ["infra"]})
    none_ = post({"type": "task", "name": "strict e none", "labels": ["docs"]})

    def ids(**params):
        r = client.get("/api/workitems", params=dict(params, page_size=200))
        return r, {i["id"] for i in r.json().get("items", [])}

    r, got = ids(priority="critical")
    check("?priority=critical finds the critical item only among these",
          wid in got and low not in got and none_ not in got, str(sorted(got))[:200])
    _, got = ids(priority="critical,low")
    check("?priority=critical,low finds both", {wid, low} <= got and none_ not in got)
    _, got = ids(priority="none")
    check("?priority=none finds an item with no priority", none_ in got and wid not in got and low not in got)
    _, got = ids(label="INFRA")
    check("?label=INFRA (any case) finds both items labelled infra", {wid, low} <= got and none_ not in got,
          str(sorted(got))[:200])
    _, got = ids(label="docs,auth")
    check("?label=docs,auth is any-of", {wid, none_} <= got and low not in got)
    _, got = ids(label="infra", priority="low")
    check("the two compose (AND)", got == {low}, str(sorted(got)))
    r, _ = ids(priority="urgent")
    check("an unknown priority is a 400 naming the field, not an empty page",
          r.status_code == 400 and r.json().get("field") == "priority", r.text[:200])
    r, _ = ids(priority="high")
    check("the response echoes the filters", r.json()["filters"].get("priority") == "high")

    r = client.get("/api/search", params={"q": "infra"})
    hit = next((i for i in r.json().get("items", []) if i["id"] == low), None)
    check("GET /api/search?q=infra finds the item by its label", hit is not None, str(r.json())[:240])
    check("...with `labels` in matched_in", hit is not None and "labels" in hit["matched_in"],
          str(hit))
    r = client.get("/api/search", params={"q": "auth infra"})
    check("a two-word query matches an item carrying both labels",
          wid in {i["id"] for i in r.json().get("items", [])}, str(r.json())[:200])
    r = client.get("/api/workitems", params={"q": "docs"})
    check("GET /api/workitems?q= reaches the label source too",
          none_ in {i["id"] for i in r.json().get("items", [])})
    r = client.get("/api/search", params={"q": "zzlabelnobodycarries"})
    check("a label nobody carries finds nothing", r.json().get("total") == 0, str(r.json())[:200])


# --------------------------------------------------------------------------- #
def section_f() -> None:
    print("\n-- F. the columns arrive on a populated table --")
    with app.engine.begin() as conn:
        conn.execute(text("ALTER TABLE node DROP COLUMN priority"))
        conn.execute(text("ALTER TABLE node DROP COLUMN labels"))
    insp = sa_inspect(app.engine)
    cols = {c["name"] for c in insp.get_columns("node")}
    check("fixture: both columns are gone from a table with rows",
          "priority" not in cols and "labels" not in cols)
    main._reconcile_graph_schema()
    insp = sa_inspect(app.engine)
    cols = {c["name"]: c for c in insp.get_columns("node")}
    check("_reconcile_graph_schema added both, nullable",
          cols.get("priority", {}).get("nullable") is True and cols.get("labels", {}).get("nullable") is True,
          str({k: v for k, v in cols.items() if k in ("priority", "labels")}))
    idx = {i["name"] for i in insp.get_indexes("node")}
    check("...and both indexes", {"ix_node_priority", "ix_node_labels_gin"} <= idx, str(sorted(idx)))
    client = _client()
    _sign_in(client)
    r = client.get("/api/workitems", params={"page_size": 200})
    items = r.json().get("items", [])
    check("every existing row reads priority null and labels []",
          r.status_code == 200 and items and all(i["priority"] is None and i["labels"] == [] for i in items),
          f"{r.status_code} {len(items)} items")


def main_() -> None:
    print("=" * 78)
    print("Atlas strict API + priority/labels verification (STORY-oss-14)")
    print("=" * 78)
    app.startup()
    import testseed
    with app.session() as s:
        testseed.build(s)
    with app.session() as s:
        testseed.self_check(s)
    os.environ["ATLAS_RESEED"] = ""
    app.startup()  # re-seed the task id sequence against the fixture's ids

    section_a()
    client = _client()
    csrf = _sign_in(client)
    check("signed in and hold a CSRF token", bool(csrf))
    ids = section_b(client, csrf)
    section_c(client, csrf, ids)
    wid = section_d(client, csrf)
    section_e(client, csrf, wid)
    section_f()

    print("\n" + "=" * 78)
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL CHECKS PASSED")
    print("=" * 78)


if __name__ == "__main__":
    main_()
