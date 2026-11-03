"""Verify the ADR registry (ADR 0045): `adr_record`, `/api/adrs*`, search's sixth source, and
`tools/adr_sync.py`'s parser and id assignment.

    A. the parser: every ADR file in THIS repo parses (count derived from disk, printed), and
       fixture headers cover a superseded status, a wrapped Amends, a cross-repo link and a
       malformed file; ids are per project and an applied id is never reassigned
    B. the empty registry (AC5): /api/adrs is an empty 200, and /api/search serves exactly
       the item shape it served before, byte for byte, before and after unrelated ADRs exist
    C. two projects holding the same legacy number are two records with two ids; a bare number
       is a 409 naming both, never a guess; ?project= or the id resolves it (AC2)
    D. search by id, legacy number, title words and status names the project (AC3)
    E. a private project's ADR is absent for a non-grantee from list, total, get, 409 and
       search, and its 404 is the one an unknown id gets; a write to it 404s too
    F. the write gate: CSRF, scope, 400s naming the field, prefix ownership, idempotence (an
       unchanged PUT writes no event) and the event a change does write
    G. links: superseded-by is derived from the other side; a dangling link says `missing`
    H. no foreign key: deleting a project node still works, and its ADRs become unreachable
    I. two sources (TASK-1354): the `source` column (added by the reconcile to a populated
       table, NULL on every git row); Atlas-native records created by PUT /api/adrs/new with
       an allocated id, Proposed by default, searchable, and refused every git-only field
    J. allocation is atomic: a create waits on the project lock and reads after it, and eight
       concurrent creates in one project get eight distinct, consecutive ids
    K. an Atlas-native ADR in a private project is invisible to a non-grantee, like a git one
    L. adr_sync against the real API: it steps over atlas ids, never creates, changes or
       orphans an atlas row, refuses a planned id an atlas row holds, and a git write to one
       is refused by the API; an older server's listing (no `source`) reads as all git
    M. one numbering rule, proven from the source: tokenised, the backend reads a project's
       highest ADR sequence in exactly one place (`adr_alloc.allocate`), builds an Atlas-native
       row only in `adr_alloc.add_atlas_adr`, and takes no ADR lock of its own in
       `routers/adrs.py` or `guardrails.py`; a planted second site is caught, a comment is not
"""
from __future__ import annotations

import base64
import contextlib
import io
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tools"))
import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:  # pragma: no cover - no skip path, ever
    print(f"FATAL: {e}")
    sys.exit(1)

os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
TEST_ORIGIN = "https://testserver"

from sqlalchemy import func, inspect as sa_inspect, select  # noqa: E402

import adr_sync  # noqa: E402  (tools/)
import app  # noqa: E402
import auth  # noqa: E402
import passwords  # noqa: E402
from models_adr import AdrRecord  # noqa: E402
from models_auth import AuthUser, NodeVisibility  # noqa: E402
from models_graph import GraphEvent, Node  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


OWNER_ID, MEMBER_ID = "u-adr-owner", "u-adr-member"
PW = "Correct-Horse-Battery-Adr-9!"


def mk_user(uid: str, username: str, role: str) -> None:
    with app.session() as s:
        s.add(AuthUser(id=uid, subject=username, name=username, role=role, is_service=False,
                       must_change_password=False, password_hash=passwords.hash_password(PW),
                       created_at=datetime.now(timezone.utc)))
        s.commit()


def mk_node(nid: str, ntype: str = "project", private: bool = False) -> None:
    with app.session() as s:
        s.add(Node(id=nid, type=ntype, name=nid, attributes={}, attention_count=0,
                   completion=0.0, origin="atlas"))
        s.flush()
        if private:
            s.add(NodeVisibility(node_id=nid, visibility="private"))
        s.commit()


def sign_in(username: str):
    from fastapi.testclient import TestClient
    c = TestClient(app.app, base_url=TEST_ORIGIN)
    page = c.get("/auth/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": username, "password": PW, "csrf_token": token},
           headers={"Origin": TEST_ORIGIN}, follow_redirects=False)
    return c, c.get("/api/me").json()["csrf_token"]


_BOTS: list[str] = []


def bearer(scopes: list[str]):
    from fastapi.testclient import TestClient
    uid = f"u-adr-bot-{len(_BOTS)}"
    _BOTS.append(uid)
    with app.session() as s:
        # A service token belongs to its own is_service account, one per caller.
        s.add(AuthUser(id=uid, subject=uid, name=uid, role="member", is_service=True,
                       must_change_password=False, password_hash=None,
                       created_at=datetime.now(timezone.utc)))
        s.flush()
        raw, _ = auth.mint_service_token(s, owner=s.get(AuthUser, uid), scopes=scopes,
                                         label=f"adr-{'-'.join(scopes)}")
        s.commit()
    return TestClient(app.app, base_url=TEST_ORIGIN, headers={"Authorization": f"Bearer {raw}"})


def adr_body(project: str, title: str, number: int | None, **kw) -> dict:
    b = {"project": project, "title": title, "legacy_number": number, "slug": "s",
         "status": "proposed", "date": "2026-10-24", "content": f"# {title}\n\nBody text."}
    b.update(kw)
    return b


def events(action: str = "node.adr.write") -> int:
    with app.session() as s:
        return s.scalar(select(func.count()).select_from(GraphEvent)
                        .where(GraphEvent.action == action)) or 0


# --------------------------------------------------------------------------- #
FIXTURE_SUPERSEDED = """# 0001. Mono-repo strategy

- **Status:** Superseded by [0031](0031-one-repo-per-world-root.md)
- **Date:** 2026-06-09

## Context
"""
FIXTURE_AMENDS = """# 0027. The work is a commons

- **Status:** Accepted — owner decision, 2026-08-11
- **Date:** 2026-08-06 · accepted later
- **Amends:** [ADR 0023](https://github.com/o/project-example/blob/master/docs/adr/0023-x.md) — and
  continues on a second line naming [0019](0019-y.md)

## Context
"""


def section_a() -> None:
    print("\n-- A. the parser and the id scheme --")
    adr_dir = HERE.parent / "docs" / "adr"
    files = [f for f in sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md")) if not f.name.startswith("0000-")]
    parsed, bad = [], []
    for f in files:
        try:
            parsed.append(adr_sync.parse_adr(f.read_text(encoding="utf-8"), f.name))
        except ValueError as e:
            bad.append(str(e))
    if not adr_dir.is_dir():
        # A tree without this repo's own ADRs has none to parse; the fixtures below still run.
        print("SKIP every ADR file in this repo parses: docs/adr/ is not in this tree")
    else:
        check(f"every ADR file in this repo parses ({len(files)} on disk)", bool(files) and not bad,
              "; ".join(bad))
        check("...each with a title, a known status and a date",
              all(a.title and a.status != "unknown" and a.date for a in parsed),
              str([(a.number, a.status, a.date) for a in parsed if a.status == "unknown" or not a.date]))

    a = adr_sync.parse_adr(FIXTURE_SUPERSEDED, "0001-mono-repo-strategy.md")
    check("'Superseded by [0031]' normalises to superseded and links superseded_by",
          a.status == "superseded" and a.superseded_by == [(None, 31)], f"{a.status} {a.superseded_by}")
    b = adr_sync.parse_adr(FIXTURE_AMENDS, "0027-work-is-a-commons.md")
    check("free text after the status word keeps the raw text and the leading word",
          b.status == "accepted" and b.status_raw.startswith("Accepted —"), b.status_raw)
    check("a wrapped Amends line is joined, and a GitHub link names its repo",
          b.amends == [("project-example", 23), (None, 19)], str(b.amends))
    try:
        adr_sync.parse_adr("no heading here\n", "0005-x.md")
        check("a file without the MADR heading is refused", False)
    except ValueError as e:
        check("a file without the MADR heading is refused, naming the file", "0005-x.md" in str(e))

    adrs = [adr_sync.Adr(number=n, slug="s", title="t", status="proposed", status_raw="Proposed",
                         date=None, path="p", content="") for n in (17, 19, 41)]
    first = adr_sync.assign_ids("AT", adrs, {})
    check("ids follow the legacy order inside a project: 0017 -> AT001, 0041 -> AT003",
          first == {17: "AT001", 19: "AT002", 41: "AT003"}, str(first))
    adrs.insert(1, adr_sync.Adr(number=18, slug="s", title="t", status="proposed",
                                status_raw="Proposed", date=None, path="p", content=""))
    again = adr_sync.assign_ids("AT", adrs, first)
    check("an applied id is never reassigned: a later file takes the NEXT sequence",
          again == {17: "AT001", 19: "AT002", 41: "AT003", 18: "AT004"}, str(again))

    with tempfile.TemporaryDirectory() as root:
        for d in ("repo-a", "repo-b"):
            p = Path(root, d, "docs", "adr")
            p.mkdir(parents=True)
            p.joinpath("0012-same-number.md").write_text(
                f"# 0012. Same number in {d}\n\n- **Status:** Proposed\n- **Date:** 2026-01-01\n",
                encoding="utf-8")
        cfg = {"repos": [{"dir": "repo-a", "project": "p-a", "prefix": "AA", "github": "o/repo-a"},
                         {"dir": "repo-b", "project": "p-b", "prefix": "BB", "github": "o/repo-b"}]}
        records, warnings, errors = adr_sync.plan(cfg, Path(root))
        check("the sync plans two records for one number in two repos",
              sorted(r["adr_id"] for r in records) == ["AA001", "BB001"], str(records))
        check("...and WARNS about it rather than refusing", not errors and
              any("0012" in w for w in warnings), str(warnings))
        cfg["repos"][1]["prefix"] = "AA"
        _, _, errors = adr_sync.plan(cfg, Path(root))
        check("a prefix used twice in adr_sources.json is an error", bool(errors), str(errors))
        cfg["repos"][1]["prefix"] = "BB"
        Path(root, "repo-a", "docs", "adr", "0013-amends-outside.md").write_text(
            "# 0013. Amends outside\n\n- **Status:** Proposed\n- **Date:** 2026-01-01\n"
            "- **Amends:** [ADR 0012](https://github.com/o/repo-out/blob/master/docs/adr/0012-z.md)\n",
            encoding="utf-8")
        records, _, _ = adr_sync.plan(cfg, Path(root))
        out = [r for r in records if r.get("legacy_number") == 13]
        check("a link into a repo not in adr_sources.json stays unresolved, not this repo's 0012",
              [r["amends"] for r in out] == [["ADR-0012"]], str(out and out[0]["amends"]))

    import json
    for rel in ("deploy/homelab/adr_sources.json", "tools/adr_sources.example.json"):
        if rel.startswith("deploy/homelab/") and not (HERE.parent / rel).exists():
            print(f"SKIP {rel} has unique prefixes: {rel} is not in this tree")
            continue
        real = json.loads((HERE.parent / rel).read_text(encoding="utf-8"))
        prefixes = [r["prefix"] for r in real["repos"]]
        check(f"{rel} has unique prefixes", len(set(prefixes)) == len(prefixes), str(prefixes))
    check("no repo config ships as a product default in tools/",
          not (HERE.parent / "tools" / "adr_sources.json").exists())

    import contextlib
    import io
    saved = os.environ.pop(adr_sync.CONFIG_ENV, None)
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = adr_sync.main(["--root", str(HERE.parent)])
        said = out.getvalue()
        check("with no --config and no ATLAS_ADR_SOURCES the sync says how and exits 0, planning nothing",
              code == 0 and "--config" in said and adr_sync.CONFIG_ENV in said
              and "per project" not in said, f"{code} {said!r}")
        os.environ[adr_sync.CONFIG_ENV] = str(HERE.parent / "tools" / "adr_sources.example.json")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = adr_sync.main(["--root", str(HERE.parent), "--json"])
        check("ATLAS_ADR_SOURCES names the config when --config is absent",
              code == 0 and '"records": []' in out.getvalue(), f"{code} {out.getvalue()[:200]!r}")
    finally:
        os.environ.pop(adr_sync.CONFIG_ENV, None)
        if saved is not None:
            os.environ[adr_sync.CONFIG_ENV] = saved


# --------------------------------------------------------------------------- #
def section_b(owner) -> None:
    print("\n-- B. the empty registry behaves as before (AC5) --")
    insp = sa_inspect(app.engine)
    check("adr_record exists after startup", "adr_record" in insp.get_table_names())
    idx = {i["name"] for i in insp.get_indexes("adr_record")}
    check("unique on adr_id and on (project, seq)",
          {"ux_adr_record_adr_id", "ux_adr_record_project_seq"} <= idx, str(sorted(idx)))
    check("no foreign key on adr_record", insp.get_foreign_keys("adr_record") == [])

    r = owner.get("/api/adrs")
    check("GET /api/adrs on an empty registry is a 200 with total 0",
          r.status_code == 200 and r.json()["total"] == 0, f"{r.status_code} {r.text[:120]}")

    mk_node("b-doc-node", "capability")
    with app.session() as s:
        s.get(Node, "b-doc-node").body = "Zanzibar quokka migration notes"
        s.commit()
    before = owner.get("/api/search?q=quokka")
    keys = set(before.json()["items"][0]) if before.json().get("items") else set()
    check("a search hit carries exactly the pre-registry item keys",
          keys == {"id", "type", "name", "summary", "matched_in", "doc_paths", "snippet", "rank"},
          str(sorted(keys)))
    mk_node("b-proj")
    owner_put = owner.put("/api/adrs/BP001", json={**adr_body("b-proj", "Unrelated wombat", 900),
                                                   "csrf_token": OWNER_CSRF},
                          headers={"Origin": TEST_ORIGIN})
    after = owner.get("/api/search?q=quokka")
    check("...and an ADR that does not match leaves that response byte-identical",
          owner_put.status_code == 201 and before.content == after.content,
          f"put={owner_put.status_code}")


# --------------------------------------------------------------------------- #
def section_c(owner) -> None:
    print("\n-- C. one number in two projects: two records, unambiguous ids (AC2) --")
    mk_node("p-alpha")
    mk_node("p-beta")
    for pid, prefix in (("p-alpha", "AA"), ("p-beta", "BB")):
        r = owner.put(f"/api/adrs/{prefix}001",
                      json={**adr_body(pid, f"Context scoping in {pid}", 12), "csrf_token": OWNER_CSRF},
                      headers={"Origin": TEST_ORIGIN})
        check(f"PUT {prefix}001 (legacy 0012) in {pid} creates", r.status_code == 201, r.text[:160])
    a, b = owner.get("/api/adrs/AA001"), owner.get("/api/adrs/BB001")
    check("each id resolves to its own project",
          a.status_code == 200 and b.status_code == 200 and a.json()["project"] == "p-alpha"
          and b.json()["project"] == "p-beta", f"{a.status_code} {b.status_code}")
    check("...and both keep the legacy alias", a.json().get("legacy") == "ADR 0012"
          and b.json().get("legacy") == "ADR 0012")
    amb = owner.get("/api/adrs/0012")
    check("a bare legacy number held twice is a 409 listing both, never a guess",
          amb.status_code == 409 and sorted(c["id"] for c in amb.json()["candidates"]) == ["AA001", "BB001"],
          f"{amb.status_code} {amb.text[:200]}")
    for ref in ("ADR-0012", "adr 0012", "12"):
        check(f"...{ref!r} is the same alias", owner.get(f"/api/adrs/{ref}").status_code == 409)
    one = owner.get("/api/adrs/0012?project=p-beta")
    check("?project= disambiguates a legacy number",
          one.status_code == 200 and one.json()["id"] == "BB001", one.text[:120])
    check("an unknown id is a 404", owner.get("/api/adrs/AA999").status_code == 404)
    check("a malformed ref is a 400 naming the field",
          owner.get("/api/adrs/not-an-adr").json().get("field") == "ref")
    with app.session() as s:
        n = s.scalar(select(func.count()).select_from(AdrRecord).where(AdrRecord.legacy_number == 12))
    check("the database holds two rows for legacy 0012", n == 2, str(n))


# --------------------------------------------------------------------------- #
def section_d(owner) -> None:
    print("\n-- D. finding an ADR by number, title words and status (AC3) --")
    owner.put("/api/adrs/AA002", json={**adr_body("p-alpha", "Kestrel retention policy", 77,
                                                  status="accepted"), "csrf_token": OWNER_CSRF},
              headers={"Origin": TEST_ORIGIN})
    for q, label in (("kestrel retention", "title words"), ("AA002", "the id"),
                     ("ADR 0077", "the legacy number")):
        r = owner.get("/api/search", params={"q": q})
        hit = next((i for i in r.json().get("items", []) if i["id"] == "p-alpha"), None)
        check(f"/api/search by {label} returns the PROJECT node, matched_in adr, with adr_ids",
              hit is not None and "adr" in hit["matched_in"] and "AA002" in hit.get("adr_ids", []),
              str(hit))
    r = owner.get("/api/adrs", params={"q": "kestrel"})
    items = r.json()["items"]
    check("/api/adrs?q= by title words names the project",
          [(i["id"], i["project"]) for i in items] == [("AA002", "p-alpha")], str(items))
    r = owner.get("/api/adrs", params={"status": "accepted"})
    check("/api/adrs?status=accepted finds it, with its project",
          any(i["id"] == "AA002" and i["project"] == "p-alpha" for i in r.json()["items"]))
    check("...and no proposed record", all(i["status"] == "accepted" for i in r.json()["items"]))
    r = owner.get("/api/adrs", params={"number": 12})
    check("/api/adrs?number=12 returns both projects' records",
          sorted((i["id"], i["project"]) for i in r.json()["items"])
          == [("AA001", "p-alpha"), ("BB001", "p-beta")], r.text[:200])
    r = owner.get("/api/adrs", params={"status": "pending"})
    check("an unknown status is a 400 naming the field",
          r.status_code == 400 and r.json().get("field") == "status")
    r = owner.get("/api/adrs", params={"project": "p-alpha"})
    check("a listing is pointers: no content", r.json()["items"] and
          all("content" not in i for i in r.json()["items"]))


# --------------------------------------------------------------------------- #
def section_e(owner, member, member_csrf) -> None:
    print("\n-- E. a private project's ADR is invisible to a non-grantee --")
    m_total = member.get("/api/adrs").json()["total"]
    mk_node("p-secret", private=True)
    r = owner.put("/api/adrs/CC001", json={**adr_body("p-secret", "Narwhal secret plan", 12),
                                           "csrf_token": OWNER_CSRF}, headers={"Origin": TEST_ORIGIN})
    check("the owner can write to the private project", r.status_code == 201, r.text[:120])
    check("the owner sees it", owner.get("/api/adrs/CC001").status_code == 200)
    check("a member's /api/adrs total does not move",
          member.get("/api/adrs").json()["total"] == m_total)
    check("...and the listing does not name it",
          all(i["id"] != "CC001" for i in member.get("/api/adrs?page_size=200").json()["items"]))
    hidden, absent = member.get("/api/adrs/CC001"), member.get("/api/adrs/CC002")
    check("its 404 is the one an id that never existed gets",
          hidden.status_code == absent.status_code == 404
          and hidden.json()["error"].replace("CC001", "X") == absent.json()["error"].replace("CC002", "X"),
          f"{hidden.status_code} {hidden.text} / {absent.text}")
    amb = member.get("/api/adrs/0012")
    check("the 409 for legacy 0012 lists only the two visible candidates",
          amb.status_code == 409 and len(amb.json()["candidates"]) == 2, amb.text[:200])
    check("...while the owner's lists three", len(owner.get("/api/adrs/0012").json()["candidates"]) == 3)
    s = member.get("/api/search?q=narwhal").json()
    check("search does not surface the private project", all(i["id"] != "p-secret" for i in s["items"])
          and s["total"] == 0, str(s["items"]))
    check("...but finds it for the owner",
          any(i["id"] == "p-secret" for i in owner.get("/api/search?q=narwhal").json()["items"]))
    w = member.put("/api/adrs/CC009", json={**adr_body("p-secret", "x", None), "csrf_token": member_csrf},
                   headers={"Origin": TEST_ORIGIN})
    u = member.put("/api/adrs/CC009", json={**adr_body("p-nowhere", "x", None), "csrf_token": member_csrf},
                   headers={"Origin": TEST_ORIGIN})
    check("a member's write to the hidden project 404s like one to a project that does not exist",
          w.status_code == u.status_code == 404
          and w.json()["error"].replace("p-secret", "X") == u.json()["error"].replace("p-nowhere", "X"),
          f"{w.status_code} {w.text} / {u.status_code} {u.text}")


# --------------------------------------------------------------------------- #
def section_f(owner) -> None:
    print("\n-- F. the write gate and the refusals --")
    body = adr_body("p-alpha", "Gate test", 50)
    r = owner.put("/api/adrs/AA003", json=body, headers={"Origin": TEST_ORIGIN})
    check("a session PUT without the CSRF token is refused", r.status_code == 403, str(r.status_code))
    ro = bearer(["read"]).put("/api/adrs/AA003", json=body)
    check("a read-scoped token is refused", ro.status_code == 403, str(ro.status_code))
    rw = bearer(["read", "write"])
    r = rw.put("/api/adrs/AA003", json=body)
    check("a write-scoped token may write", r.status_code == 201, r.text[:120])
    n0 = events()
    r = rw.put("/api/adrs/AA003", json=body)
    check("an unchanged PUT is a 200 with nothing changed", r.status_code == 200
          and r.json()["changed"] == [], r.text[:120])
    check("...and writes no event", events() == n0, f"{n0} -> {events()}")
    r = rw.put("/api/adrs/AA003", json={**body, "status": "accepted"})
    check("a changed PUT names the field", r.json().get("changed") == ["status"], r.text[:120])
    with app.session() as s:
        ev = s.scalars(select(GraphEvent).where(GraphEvent.action == "node.adr.write")
                       .order_by(GraphEvent.id.desc())).first()
    check("...and records node.adr.write on the PROJECT node, naming the ADR",
          ev is not None and ev.node_id == "p-alpha" and ev.detail.get("adr_id") == "AA003",
          str(ev and (ev.node_id, ev.detail)))
    for adr_id, patch, field, why in (
            ("aa004", {}, "adr_id", "a malformed id"),
            ("AA004", {"status": "maybe"}, "status", "an unknown status"),
            ("AA004", {"date": "24/10/2026"}, "date", "a non-ISO date"),
            ("AA004", {"title": "  "}, "title", "an empty title"),
            ("AA004", {"supersedes": ["0031"]}, "supersedes", "a link that is not an id"),
            ("AA004", {"legacy_number": 12}, "legacy_number", "a legacy number this project already holds"),
            ("BB004", {}, "adr_id", "a prefix another project owns"),
            ("AB004", {}, "adr_id", "a second prefix for one project"),
            ("BB001", {}, "adr_id", "moving an ADR to another project (its prefix is that project's)")):
        r = rw.put(f"/api/adrs/{adr_id}", json={**body, **patch})
        check(f"{why} is a 400 naming {field}",
              r.status_code == 400 and r.json().get("field") == field, f"{r.status_code} {r.text[:160]}")


# --------------------------------------------------------------------------- #
def section_g(owner) -> None:
    print("\n-- G. supersedes / superseded-by --")
    rw = bearer(["read", "write"])
    rw.put("/api/adrs/BB002", json=adr_body("p-beta", "Replaces the first", 60,
                                            supersedes=["BB001"], amends=["ADR-0999"]))
    old = owner.get("/api/adrs/BB001").json()
    check("superseded_by is derived from the record that supersedes it",
          [x["id"] for x in old["superseded_by"]] == ["BB002"], str(old["superseded_by"]))
    new = owner.get("/api/adrs/BB002").json()
    check("supersedes resolves to {id, title, status, project}",
          new["supersedes"] and new["supersedes"][0].get("title", "").startswith("Context scoping"),
          str(new["supersedes"]))
    check("a dangling link comes back as missing, not as an error",
          new["amends"] == [{"id": "ADR-0999", "missing": True}], str(new["amends"]))


# --------------------------------------------------------------------------- #
def section_h(owner) -> None:
    print("\n-- H. no foreign key: a project node can still be deleted --")
    mk_node("p-gone")
    bearer(["read", "write"]).put("/api/adrs/GG001", json=adr_body("p-gone", "Ephemeral", None))
    d = owner.request("DELETE", "/api/node/p-gone", json={"csrf_token": OWNER_CSRF},
                      headers={"Origin": TEST_ORIGIN})
    check("DELETE /api/node on a project with ADRs succeeds", d.status_code == 200,
          f"{d.status_code} {d.text[:160]}")
    check("...and its ADR is unreachable afterwards, owner included",
          owner.get("/api/adrs/GG001").status_code == 404
          and all(i["id"] != "GG001" for i in owner.get("/api/adrs?page_size=200").json()["items"]))


# --------------------------------------------------------------------------- #
def create(client, project: str, title: str, csrf: str | None = None, **kw):
    b = {"project": project, "title": title, "content": f"# {title}\n\nAtlas-native body.", **kw}
    if csrf is not None:
        return client.put("/api/adrs/new", json={**b, "csrf_token": csrf},
                          headers={"Origin": TEST_ORIGIN})
    return client.put("/api/adrs/new", json=b)


def row_snapshot(adr_id: str):
    with app.session() as s:
        r = s.scalar(select(AdrRecord).where(AdrRecord.adr_id == adr_id))
        return None if r is None else tuple(getattr(r, c.name) for c in AdrRecord.__table__.columns)


def section_i(owner) -> None:
    print("\n-- I. two sources: git rows unchanged, Atlas-native rows created by the API --")
    from sqlalchemy import text as sql_text
    import main
    cols = {c["name"]: c for c in sa_inspect(app.engine).get_columns("adr_record")}
    check("adr_record has a nullable `source` column", "source" in cols and cols["source"]["nullable"])
    with app.session() as s:
        git_rows = s.scalar(select(func.count()).select_from(AdrRecord)) or 0
        non_null = s.scalar(select(func.count()).select_from(AdrRecord)
                            .where(AdrRecord.source.is_not(None))) or 0
    check("every row written by the git path (sections B-H) stores source NULL",
          git_rows > 0 and non_null == 0, f"{git_rows} rows, {non_null} non-null")
    items = owner.get("/api/adrs?page_size=200").json()["items"]
    check("...and the API reports each as source git",
          items and all(i["source"] == "git" for i in items), str({i["source"] for i in items}))
    check("the detail of a git record says git too", owner.get("/api/adrs/AA001").json().get("source") == "git")

    # The reconcile adds the column to a populated table that predates it, NULL on every row.
    with app.engine.begin() as conn:
        conn.execute(sql_text("ALTER TABLE adr_record DROP COLUMN source"))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main._reconcile_graph_schema()
    with app.engine.connect() as conn:
        n = conn.execute(sql_text("SELECT count(*) FROM adr_record WHERE source IS NOT NULL")).scalar()
        total = conn.execute(sql_text("SELECT count(*) FROM adr_record")).scalar()
    check("_reconcile_graph_schema adds adr_record.source to a populated table",
          "added column adr_record.source" in out.getvalue(), out.getvalue().strip()[:200])
    check("...and every existing row reads NULL (git), none rewritten", n == 0 and total == git_rows,
          f"{n} non-null of {total}")

    rw = bearer(["read", "write"])
    today = datetime.now(timezone.utc).date().isoformat()
    n0 = events()
    r = create(rw, "p-alpha", "Ocelot cache invalidation")
    j = r.json()
    check("PUT /api/adrs/new allocates the project's next id (AA004 after AA003)",
          r.status_code == 201 and j.get("id") == "AA004" and j.get("source") == "atlas",
          f"{r.status_code} {r.text[:200]}")
    check("...Proposed by default", j.get("status") == "proposed", str(j))
    check("...and records node.adr.write naming source atlas", events() == n0 + 1)
    d = owner.get("/api/adrs/AA004").json()
    check("the record says source atlas, has its markdown, and no file provenance",
          d.get("source") == "atlas" and "Atlas-native body" in (d.get("content") or "")
          and d.get("repo") is None and d.get("path") is None and d.get("commit") is None
          and d.get("legacy") is None and d.get("number") is None, str({k: d.get(k) for k in
                                                                         ("source", "repo", "legacy")}))
    check("...dated today, with a slug from its title",
          d.get("date") == today and d.get("slug") == "ocelot-cache-invalidation", f"{d.get('date')} {d.get('slug')}")
    with app.session() as s:
        stored = s.scalar(select(AdrRecord.source).where(AdrRecord.adr_id == "AA004"))
    check("the database stores source 'atlas'", stored == "atlas", str(stored))
    hit = next((i for i in owner.get("/api/search", params={"q": "ocelot"}).json().get("items", [])
                if i["id"] == "p-alpha"), None)
    check("/api/search finds it like a git one: the project node, matched_in adr, adr_ids",
          hit is not None and "adr" in hit["matched_in"] and "AA004" in hit.get("adr_ids", []), str(hit))
    lst = owner.get("/api/adrs", params={"q": "ocelot"}).json()["items"]
    check("/api/adrs?q= lists it with source atlas",
          [(i["id"], i["source"]) for i in lst] == [("AA004", "atlas")], str(lst))
    check("/api/adrs?status=proposed includes it",
          any(i["id"] == "AA004" for i in owner.get("/api/adrs?status=proposed&page_size=200").json()["items"]))

    mk_node("p-native")
    r = create(rw, "p-native", "First decision")
    check("a project with no ADRs yet must name its prefix: 400 naming prefix",
          r.status_code == 400 and r.json().get("field") == "prefix", r.text[:160])
    r = create(rw, "p-native", "First decision", prefix="NV")
    check("...and with one, its first id is NV001", r.status_code == 201 and r.json()["id"] == "NV001", r.text[:160])
    r = create(rw, "p-native", "Second decision", status="accepted")
    check("the next takes NV002 without restating the prefix, and keeps an explicit status",
          r.status_code == 201 and r.json()["id"] == "NV002" and r.json()["status"] == "accepted", r.text[:160])

    mk_node("p-empty")
    for patch, field, why in (
            ({"legacy_number": 300}, "legacy_number", "a legacy number"),
            ({"repo": "o/r"}, "repo", "a repo"),
            ({"path": "docs/adr/x.md"}, "path", "a path"),
            ({"commit": "abc"}, "commit", "a commit"),
            ({"orphaned": True}, "orphaned", "orphaned"),
            ({"source": "git"}, "source", "source git (a git id is named, never allocated)"),
            ({"source": "svn"}, "source", "an unknown source"),
            ({"prefix": "ZZ"}, "prefix", "a prefix other than the project's own"),
            ({"prefix": "BB"}, "prefix", "a prefix another project owns (first ADR of p-empty)"),
            ({"prefix": "ADR"}, "prefix", "the prefix ADR"),
            ({"status": "maybe"}, "status", "an unknown status")):
        r = create(rw, "p-empty" if patch.get("prefix") == "BB" else "p-native", "Refused", **patch)
        check(f"a create with {why} is a 400 naming {field}",
              r.status_code == 400 and r.json().get("field") == field, f"{r.status_code} {r.text[:160]}")
    r = rw.put("/api/adrs/NV099", json={"project": "p-native", "title": "Chosen", "source": "atlas"})
    check("an atlas id is never chosen: PUT /api/adrs/NV099 with source atlas is a 400 naming adr_id",
          r.status_code == 400 and r.json().get("error") == "atlas_id_not_allocated", r.text[:160])
    r = rw.put("/api/adrs/AA001", json={**adr_body("p-alpha", "x", None, repo=None), "source": "atlas"})
    check("an atlas write to a git row is refused, naming source",
          r.status_code == 400 and r.json().get("field") == "source", r.text[:160])

    # A human accepts it: an update through its own id, with source atlas.
    n1 = events()
    up = rw.put("/api/adrs/AA004", json={"project": "p-alpha", "title": "Ocelot cache invalidation",
                                         "slug": "ocelot-cache-invalidation", "source": "atlas",
                                         "status": "accepted",
                                         "content": "# Ocelot cache invalidation\n\nAtlas-native body."})
    check("accepting it is a 200 naming status only (an omitted date keeps the date)",
          up.status_code == 200 and up.json().get("changed") == ["status"], up.text[:200])
    again = rw.put("/api/adrs/AA004", json={"project": "p-alpha", "title": "Ocelot cache invalidation",
                                            "slug": "ocelot-cache-invalidation", "source": "atlas",
                                            "status": "accepted",
                                            "content": "# Ocelot cache invalidation\n\nAtlas-native body."})
    check("...and the same write again changes nothing and writes no event",
          again.status_code == 200 and again.json()["changed"] == [] and events() == n1 + 1, again.text[:160])
    before = row_snapshot("AA004")
    g = rw.put("/api/adrs/AA004", json=adr_body("p-alpha", "Overwritten by git", None))
    check("a git write to an id an atlas row holds is a 400 held_by_atlas, naming adr_id",
          g.status_code == 400 and g.json().get("error") == "held_by_atlas"
          and g.json().get("field") == "adr_id", g.text[:160])
    check("...and the row is untouched", row_snapshot("AA004") == before)
    ro = create(bearer(["read"]), "p-alpha", "Read-only")
    check("a read-scoped token cannot create one", ro.status_code == 403, str(ro.status_code))
    nocsrf = owner.put("/api/adrs/new", json={"project": "p-alpha", "title": "x"},
                       headers={"Origin": TEST_ORIGIN})
    check("a session create without the CSRF token is refused", nocsrf.status_code == 403,
          str(nocsrf.status_code))
    ok = create(owner, "p-alpha", "Created from the board", csrf=OWNER_CSRF)
    check("a signed-in person with the CSRF token can create one (AA005)",
          ok.status_code == 201 and ok.json()["id"] == "AA005", ok.text[:160])


def section_j() -> None:
    print("\n-- J. id allocation is atomic --")
    import threading
    import time
    from sqlalchemy import text as sql_text
    mk_node("p-lock")
    rw = bearer(["read", "write"])
    check("the first create in p-lock is LK001",
          create(rw, "p-lock", "Lock one", prefix="LK").json().get("id") == "LK001")
    result: dict = {}

    def worker() -> None:
        r = create(rw, "p-lock", "Lock waiter")
        result["r"] = (r.status_code, r.json())

    conn = app.engine.connect()
    trans = conn.begin()
    conn.execute(sql_text("SELECT id FROM node WHERE id = 'p-lock' FOR UPDATE"))
    t = threading.Thread(target=worker)
    t.start()
    time.sleep(1.0)
    check("a create waits while another transaction holds the project's lock", t.is_alive())
    # While it waits, a write that committed first takes LK002.
    conn.execute(sql_text(
        "INSERT INTO adr_record (adr_id, project_node_id, prefix, seq, slug, title, status, synced_at) "
        "VALUES ('LK002', 'p-lock', 'LK', 2, 's', 'Committed first', 'proposed', now())"))
    trans.commit()
    conn.close()
    t.join(30)
    check("...and once it is released, it reads after the lock and takes LK003, not LK002",
          result.get("r", (0, {}))[0] == 201 and result["r"][1].get("id") == "LK003", str(result))

    mk_node("p-race")
    clients = [bearer(["read", "write"]) for _ in range(8)]
    barrier = threading.Barrier(len(clients))
    got: list = []

    def racer(c) -> None:
        barrier.wait()
        r = create(c, "p-race", "Racing", prefix="RC")
        got.append((r.status_code, r.json().get("id")))

    threads = [threading.Thread(target=racer, args=(c,)) for c in clients]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)
    ids = sorted(i for _, i in got)
    check("eight concurrent creates in one project all succeed",
          len(got) == 8 and all(code == 201 for code, _ in got), str(got))
    check("...with eight distinct, consecutive ids RC001..RC008",
          ids == [f"RC{n:03d}" for n in range(1, 9)], str(ids))


def section_k(owner, member, member_csrf) -> None:
    print("\n-- K. an Atlas-native ADR in a private project is invisible to a non-grantee --")
    m_total = member.get("/api/adrs").json()["total"]
    r = create(owner, "p-secret", "Platypus covert decision", csrf=OWNER_CSRF)
    check("the owner creates one in the private project (CC002 after CC001)",
          r.status_code == 201 and r.json()["id"] == "CC002", r.text[:160])
    check("the owner sees it, source atlas", owner.get("/api/adrs/CC002").json().get("source") == "atlas")
    check("a member's /api/adrs total does not move", member.get("/api/adrs").json()["total"] == m_total)
    check("...and the listing does not name it",
          all(i["id"] != "CC002" for i in member.get("/api/adrs?page_size=200").json()["items"]))
    hidden, absent = member.get("/api/adrs/CC002"), member.get("/api/adrs/CC077")
    check("its 404 is the one an id that never existed gets",
          hidden.status_code == absent.status_code == 404
          and hidden.json()["error"].replace("CC002", "X") == absent.json()["error"].replace("CC077", "X"),
          f"{hidden.text} / {absent.text}")
    s = member.get("/api/search?q=platypus").json()
    check("search does not surface it for the member", s["total"] == 0, str(s["items"]))
    check("...but does for the owner",
          any(i["id"] == "p-secret" for i in owner.get("/api/search?q=platypus").json()["items"]))
    w = create(member, "p-secret", "x", csrf=member_csrf)
    u = create(member, "p-nowhere", "x", csrf=member_csrf)
    check("a member's create in the hidden project 404s like one in a project that does not exist",
          w.status_code == u.status_code == 404
          and w.json()["error"].replace("p-secret", "X") == u.json()["error"].replace("p-nowhere", "X"),
          f"{w.status_code} {w.text} / {u.status_code} {u.text}")
    with app.session() as s2:
        n = s2.scalar(select(func.count()).select_from(AdrRecord)
                      .where(AdrRecord.project_node_id == "p-secret")) or 0
    check("...and wrote nothing", n == 2, str(n))


def section_l() -> None:
    print("\n-- L. adr_sync beside Atlas-native records, against the real API --")
    mk_node("p-sync")
    rw = bearer(["read", "write"])
    native = create(rw, "p-sync", "Born in Atlas", prefix="SY")
    check("an Atlas-native SY001 exists before any file is synced",
          native.status_code == 201 and native.json()["id"] == "SY001", native.text[:160])
    native_before = row_snapshot("SY001")

    class ApiOverTestClient:
        """adr_sync.Api, answering from this process's app through a write-scoped token."""
        def __init__(self, base_url, token_file):
            self.c = rw

        def call(self, method, path, body=None):
            r = self.c.request(method, path, json=body)
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, {}

    real_api = adr_sync.Api
    adr_sync.Api = ApiOverTestClient
    try:
        with tempfile.TemporaryDirectory() as root:
            adr_dir = Path(root, "repo-sync", "docs", "adr")
            adr_dir.mkdir(parents=True)
            for n, t in ((101, "First file"), (102, "Second file")):
                adr_dir.joinpath(f"0{n}-f.md").write_text(
                    f"# 0{n}. {t}\n\n- **Status:** Accepted\n- **Date:** 2026-01-01\n", encoding="utf-8")
            cfg = Path(root, "cfg.json")
            cfg.write_text('{"repos": [{"dir": "repo-sync", "project": "p-sync", "prefix": "SY", '
                           '"github": "o/repo-sync"}]}', encoding="utf-8")
            tok = Path(root, "unused.token")
            tok.write_text("unused", encoding="utf-8")
            argv = ["--config", str(cfg), "--root", root, "--apply", "--base-url", "http://unused",
                    "--token-file", str(tok)]

            def run() -> tuple[int, str]:
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = adr_sync.main(argv)
                return code, out.getvalue() + err.getvalue()

            code, said = run()
            items = {i["id"]: i for i in rw.get("/api/adrs?project=p-sync").json()["items"]}
            check("the sync applies, stepping over the atlas id: files take SY002 and SY003",
                  code == 0 and items.get("SY002", {}).get("number") == 101
                  and items.get("SY003", {}).get("number") == 102, f"{code} {said[-300:]} {sorted(items)}")
            check("...as git records", items.get("SY002", {}).get("source") == "git"
                  and items.get("SY003", {}).get("source") == "git")
            check("...and the atlas row is byte-identical", row_snapshot("SY001") == native_before)

            n0 = events()
            code, said = run()
            check("a second run changes nothing: no event, the atlas row untouched",
                  code == 0 and events() == n0 and row_snapshot("SY001") == native_before
                  and "SY001" not in said, said[-300:])

            adr_dir.joinpath("0102-f.md").unlink()
            code, said = run()
            gone = rw.get("/api/adrs/SY003").json()
            check("a vanished file orphans its git record (SY003)", code == 0 and gone.get("orphaned"), said[-200:])
            check("...and never the atlas row, which has no file",
                  row_snapshot("SY001") == native_before and "SY001" not in said, said[-300:])

            records, _, errors = adr_sync.plan(json_load(cfg), Path(root),
                                               existing={"p-sync": {101: "SY001"}},
                                               held={"p-sync": {"SY001"}})
            check("a planned write to an id an atlas row holds is an error, not a write",
                  any("SY001" in e and "Atlas-native" in e for e in errors), str(errors))
            r = rw.put("/api/adrs/SY001", json=adr_body("p-sync", "Overwrite", 101,
                                                         repo="o/repo-sync", path="docs/adr/0101-f.md"))
            check("...and the API refuses it too, as the sync would see (held_by_atlas)",
                  r.status_code == 400 and r.json().get("error") == "held_by_atlas", r.text[:160])
            check("the atlas row survived all of it", row_snapshot("SY001") == native_before)
    finally:
        adr_sync.Api = real_api

    class OldServer:
        def call(self, method, path, body=None):
            return 200, {"items": [{"id": "SY001", "number": 101}, {"id": "SY002", "number": None}]}
    existing, held = adr_sync.registry_ids(OldServer(), ["p-sync"])
    check("an older server's listing (no `source`) reads as all git, as before",
          existing == {"p-sync": {101: "SY001"}} and held == {"p-sync": set()}, f"{existing} {held}")
    check("assign_ids with no held ids is unchanged",
          adr_sync.assign_ids("AT", [], {17: "AT001"}) == {17: "AT001"})


# --------------------------------------------------------------------------- #
# M. One numbering rule (tokenised, like verify_graph_event.py §B)
# --------------------------------------------------------------------------- #
def _code_tokens(path: Path) -> list:
    """(string, line) of every CODE token: comments, strings and layout dropped, so a comment
    that names the pattern cannot turn this red, and nobody learns to edit around the check."""
    import tokenize
    skip = {tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
            tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER}
    for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):  # 3.12+
        if hasattr(tokenize, name):
            skip.add(getattr(tokenize, name))
    with open(path, "rb") as fh:
        return [(t.string, t.start[0]) for t in tokenize.tokenize(fh.readline) if t.type not in skip]


def _sites(path: Path, pattern: tuple) -> list:
    """Line numbers where the token sequence `pattern` occurs as code."""
    toks = _code_tokens(path)
    n = len(pattern)
    return [toks[i][1] for i in range(len(toks) - n + 1)
            if tuple(t for t, _ in toks[i:i + n]) == pattern]


#: `max(AdrRecord.seq)`: reading a project's highest sequence, the step that numbers an ADR.
_ALLOC = ("max", "(", "AdrRecord", ".", "seq", ")")
#: `AdrRecord(`: building a row.
_BUILD = ("AdrRecord", "(")
#: `.with_for_update(`: taking a row lock.
_LOCK = (".", "with_for_update", "(")


def _backend_modules() -> list:
    """Every production module. models_adr.py DECLARES the class; the verify scripts read these
    patterns back. Neither is a writer."""
    return sorted(p for p in HERE.rglob("*.py")
                  if not p.name.startswith(("verify_", "test_")) and p.name != "models_adr.py"
                  and not any(part.startswith(".") or part in ("__pycache__", "node_modules")
                              for part in p.relative_to(HERE).parts))


def _span(path: Path, start: str, end_marker) -> tuple:
    src = path.read_text(encoding="utf-8")
    first = src[:src.index(start)].count("\n") + 1
    last = (src[:src.index(end_marker)].count("\n") + 1) if end_marker else src.count("\n") + 2
    return first, last


def section_m() -> None:
    print("\n-- M. one numbering rule, proven from the source --")
    alloc_mod = HERE / "adr_alloc.py"
    by = {p.relative_to(HERE).as_posix(): p for p in _backend_modules()}
    alloc = {n: v for n, v in ((n, _sites(p, _ALLOC)) for n, p in by.items()) if v}
    check("the backend reads a project's highest ADR sequence in exactly ONE place, adr_alloc.py "
          "(a second `max(AdrRecord.seq)` is a second numbering rule)",
          list(alloc) == ["adr_alloc.py"] and len(alloc["adr_alloc.py"]) == 1, str(alloc))
    lo, hi = _span(alloc_mod, "def allocate(", "def add_atlas_adr(")
    check("...and that one site is inside adr_alloc.allocate",
          all(lo < ln < hi for ln in alloc.get("adr_alloc.py", [0])), f"{alloc} vs {lo}..{hi}")

    build = {n: v for n, v in ((n, _sites(p, _BUILD)) for n, p in by.items()) if v}
    check("an AdrRecord row is built only in adr_alloc.py (the atlas insert) and routers/adrs.py "
          "(its one git-row branch); guardrails.py builds none",
          sorted(build) == ["adr_alloc.py", "routers/adrs.py"]
          and len(build["adr_alloc.py"]) == 1 and len(build["routers/adrs.py"]) == 1, str(build))
    lo, hi = _span(alloc_mod, "def add_atlas_adr(", None)
    check("...and adr_alloc.py's one is inside add_atlas_adr",
          all(lo < ln < hi for ln in build.get("adr_alloc.py", [0])), f"{build} vs {lo}..{hi}")
    adrs_src = (HERE / "routers" / "adrs.py").read_text(encoding="utf-8")
    check("routers/adrs.py's own AdrRecord( is the git branch (source=None), never an atlas row",
          "synced_at=now, source=None, **want)" in adrs_src)

    locks = {n: _sites(by[n], _LOCK) for n in ("routers/adrs.py", "guardrails.py")}
    check("neither routers/adrs.py nor guardrails.py takes a row lock of its own: both lock "
          "through adr_alloc.lock_project", not any(locks.values()), str(locks))
    check("both writers call the three shared functions",
          all(f"adr_alloc.{fn}(" in (HERE / n).read_text(encoding="utf-8")
              for n in ("routers/adrs.py", "guardrails.py")
              for fn in ("lock_project", "allocate", "add_atlas_adr")))

    # Proof the check can fail: a planted second site is counted, the same text in a comment
    # or a string is not.
    with tempfile.TemporaryDirectory() as d:
        planted = Path(d) / "planted.py"
        planted.write_text(
            "# a comment saying max(AdrRecord.seq) is not code\n"
            "NOTE = 'nor is max(AdrRecord.seq) in a string'\n"
            "seq = (s.scalar(select(func.max(AdrRecord . seq))) or 0) + 1\n"
            "row = AdrRecord(adr_id='X')\n", encoding="utf-8")
        check("a planted allocation and a planted row are caught; the comment and string are not",
              _sites(planted, _ALLOC) == [3] and _sites(planted, _BUILD) == [4],
              f"{_sites(planted, _ALLOC)} {_sites(planted, _BUILD)}")


def json_load(p: Path) -> dict:
    import json
    return json.loads(p.read_text(encoding="utf-8"))


if __name__ == "__main__":
    app.startup()
    mk_user(OWNER_ID, "verify-adr-owner", "owner")
    mk_user(MEMBER_ID, "verify-adr-member", "member")
    owner, OWNER_CSRF = sign_in("verify-adr-owner")
    member, member_csrf = sign_in("verify-adr-member")

    section_a()
    section_b(owner)
    section_c(owner)
    section_d(owner)
    section_e(owner, member, member_csrf)
    section_f(owner)
    section_g(owner)
    section_h(owner)
    section_i(owner)
    section_j()
    section_k(owner, member, member_csrf)
    section_l()
    section_m()

    print()
    if failures:
        print(f"FAILED — {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")
