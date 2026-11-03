"""
Verification for comments on workitems (`routers/comments.py`, `models_graph.WorkitemComment`).

Standalone run-it-and-read-it, against a real, disposable Postgres via `testdb`, through the REAL
auth gate with signed-in clients — never a principal installed directly.

FOUR SECTIONS:

  A. The table: it exists, and the author columns are there.
  B. The write API: create, list oldest-first with the author, every refusal NAMING its field,
     a write with no CSRF token refused, a non-workitem refused, and the authorship rules —
     only the author edits, the author or an owner deletes. Each act is in `graph_event`.
     An edit keeps the text it replaced in `workitem_comment_revision`. A delete HIDES: the row and its text stay with who deleted it, and the deleted comment is
     gone from the list, from search, and from a second edit or delete (404).
  C. Privacy: on work bound to a private project, a member without a grant gets the same 404
     on the list, the edit and the delete as for something that does not exist; a granted
     member reads the conversation.
  D. `DELETE /api/node/{id}` removes the item's comments and their revisions
     (`_delete_node_dependents`).
  E. Search: a word that is only in a comment finds the item on `/api/search` (matched_in
     `comment`, snippet from the comment) and on `/api/workitems?q=`, and a comment on private
     work finds nothing for an outsider, not even a count.

Run (from a venv with the backend requirements installed):
    python verify_workitem_comments.py
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
except testdb.TestDatabaseUnavailable as e:  # pragma: no cover - no skip path, ever
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
H = {"Origin": TEST_ORIGIN}

from sqlalchemy import inspect as sa_inspect, select  # noqa: E402

import app  # noqa: E402
import passwords  # noqa: E402
from models_auth import AuthUser, NodeGrant, NodeVisibility  # noqa: E402
from models_graph import GraphEvent, Node, WorkitemComment, WorkitemCommentRevision  # noqa: E402
from routers.comments import COMMENT_MAX  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


PW = "Correct-Horse-Battery-Comments-9!"
OWNER_ID, MEMBER_ID, OUTSIDER_ID = "u-c-owner", "u-c-member", "u-c-outsider"


def mk_user(uid: str, username: str, role: str = "member") -> None:
    with app.session() as s:
        if s.get(AuthUser, uid) is None:
            s.add(AuthUser(id=uid, subject=username, name=username.title(), role=role,
                           is_service=False, must_change_password=False,
                           password_hash=passwords.hash_password(PW),
                           created_at=datetime.now(timezone.utc)))
            s.commit()


def sign_in(username: str):
    from fastapi.testclient import TestClient
    c = TestClient(app.app, base_url=TEST_ORIGIN)
    page = c.get("/auth/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": username, "password": PW, "csrf_token": token},
           headers=H, follow_redirects=False)
    return c, c.get("/api/me").json()["csrf_token"]


def mk_project(nid: str) -> None:
    with app.session() as s:
        if s.get(Node, nid) is None:
            s.add(Node(id=nid, type="project", name=nid, attributes={}, attention_count=0,
                       completion=0.0, origin="atlas"))
            s.commit()


def mk_task(client, csrf: str, binding: str, name: str) -> str:
    r = client.post("/api/workitem", json={"type": "task", "name": name, "binding": binding,
                                           "status": "pending", "csrf_token": csrf}, headers=H)
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    return r.json()["id"]


def events(node_id: str, action: str) -> list[GraphEvent]:
    with app.session() as s:
        return list(s.scalars(select(GraphEvent).where(
            GraphEvent.entity_id == node_id, GraphEvent.action == action)).all())


# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. the table --")
    insp = sa_inspect(app.engine)
    check("workitem_comment exists", "workitem_comment" in insp.get_table_names())
    check("workitem_comment_revision exists", "workitem_comment_revision" in insp.get_table_names())
    cols = {c["name"] for c in insp.get_columns("workitem_comment")}
    for name in ("id", "node_id", "body", "created_at", "created_by_user_id",
                 "created_by_subject", "edited_at"):
        check(f"column {name} exists", name in cols, str(sorted(cols)))


def section_b(owner, ocsrf, member, mcsrf) -> None:
    print("\n-- B. the write API, and who may do what --")
    mk_project("C-PUB")
    task = mk_task(owner, ocsrf, "C-PUB", "Commented task")
    url = f"/api/workitem/{task}/comments"

    r = owner.get(url)
    check("an item with no comments lists an empty 200", r.status_code == 200
          and r.json() == {"comments": [], "count": 0}, f"{r.status_code} {r.text}")

    r = owner.post(url, json={"body": "First, from the **owner**.", "csrf_token": ocsrf}, headers=H)
    check("the owner comments: 201", r.status_code == 201, f"{r.status_code} {r.text}")
    first = r.json()
    check("...the author is the signed-in person, not anything in the body",
          first.get("author", {}).get("username") == "c-owner"
          and first["author"].get("id") == OWNER_ID, str(first.get("author")))
    r = member.post(url, json={"body": "Second, from a member.", "csrf_token": mcsrf}, headers=H)
    check("a member comments: 201", r.status_code == 201, f"{r.status_code} {r.text}")
    second = r.json()

    listed = owner.get(url).json()
    check("the list is oldest first, with the author's name",
          [c["id"] for c in listed["comments"]] == [first["id"], second["id"]]
          and listed["comments"][1]["author"]["name"] == "C-Member", str(listed))

    for label, payload in (("an empty body", {"body": "   "}),
                           ("a body over the limit", {"body": "x" * (COMMENT_MAX + 1)})):
        r = owner.post(url, json={**payload, "csrf_token": ocsrf}, headers=H)
        check(f"{label} is a 400 naming `body`",
              r.status_code == 400 and r.json().get("field") == "body", f"{r.status_code} {r.text}")
    r = owner.post(url, json={"body": "no token"}, headers=H)
    check("a cookie write with no CSRF token is refused", r.status_code == 403, str(r.status_code))
    r = owner.post("/api/workitem/C-PUB/comments", json={"body": "hi", "csrf_token": ocsrf}, headers=H)
    check("a project is not a workitem: 409", r.status_code == 409, f"{r.status_code} {r.text}")
    r = owner.post("/api/workitem/TASK-NOPE/comments", json={"body": "hi", "csrf_token": ocsrf}, headers=H)
    check("an unknown item: 404", r.status_code == 404, str(r.status_code))

    r = owner.patch(f"/api/comment/{second['id']}", json={"body": "rewritten", "csrf_token": ocsrf}, headers=H)
    check("nobody edits another's comment, not even an owner: 403",
          r.status_code == 403 and r.json().get("error") == "not_your_comment", f"{r.status_code} {r.text}")
    r = member.patch(f"/api/comment/{second['id']}", json={"body": "Second, edited.", "csrf_token": mcsrf}, headers=H)
    check("the author edits: 200, body changed, edited_at set",
          r.status_code == 200 and r.json()["body"] == "Second, edited." and r.json()["edited_at"],
          f"{r.status_code} {r.text}")
    r = member.patch(f"/api/comment/{second['id']}", json={"body": "Second, edited.", "csrf_token": mcsrf}, headers=H)
    check("an edit to the same text: 200, and no revision for it", r.status_code == 200, f"{r.status_code} {r.text}")
    r = member.patch(f"/api/comment/{second['id']}", json={"body": "Second, edited twice.", "csrf_token": mcsrf}, headers=H)
    with app.session() as s:
        revs = s.scalars(select(WorkitemCommentRevision)
                         .where(WorkitemCommentRevision.comment_id == second["id"])
                         .order_by(WorkitemCommentRevision.id)).all()
    check("every replaced text is kept, oldest first: 2 revisions",
          [v.body for v in revs] == ["Second, from a member.", "Second, edited."],
          str([v.body for v in revs]))
    check("...each says when it was written, when and by whom it was replaced",
          all(v.written_at and v.replaced_at and v.replaced_by_subject == "c-member" for v in revs)
          and revs[0].written_at <= revs[1].written_at <= revs[1].replaced_at,
          str([(v.written_at, v.replaced_at, v.replaced_by_subject) for v in revs]))
    check("...and the list shows only the current text",
          [c["body"] for c in owner.get(url).json()["comments"]][1] == "Second, edited twice.",
          owner.get(url).text[:300])
    r = member.request("DELETE", f"/api/comment/{first['id']}", json={"csrf_token": mcsrf}, headers=H)
    check("a member cannot delete the owner's comment: 403", r.status_code == 403, f"{r.status_code} {r.text}")
    r = owner.request("DELETE", f"/api/comment/{second['id']}", json={"csrf_token": ocsrf}, headers=H)
    check("an owner may delete anybody's comment (moderation): 200", r.status_code == 200, f"{r.status_code} {r.text}")
    r = owner.request("DELETE", f"/api/comment/{first['id']}", json={"csrf_token": ocsrf}, headers=H)
    check("the author deletes their own: 200", r.status_code == 200, f"{r.status_code} {r.text}")
    check("...and the list is empty again", owner.get(url).json()["count"] == 0)
    with app.session() as s:
        kept = {c.id: c for c in s.scalars(select(WorkitemComment).where(WorkitemComment.node_id == task))}
    check("a delete hides, never removes: both rows remain with their text",
          set(kept) == {first["id"], second["id"]} and kept[second["id"]].body == "Second, edited twice.",
          str({k: c.body for k, c in kept.items()}))
    check("...and each says when and by whom it was deleted",
          all(c.deleted_at is not None for c in kept.values())
          and kept[second["id"]].deleted_by_subject == "c-owner"
          and kept[first["id"]].deleted_by_subject == "c-owner",
          str({k: (c.deleted_at, c.deleted_by_subject) for k, c in kept.items()}))
    r = member.patch(f"/api/comment/{second['id']}", json={"body": "back", "csrf_token": mcsrf}, headers=H)
    check("a deleted comment cannot be edited: 404", r.status_code == 404, f"{r.status_code} {r.text}")
    r = owner.request("DELETE", f"/api/comment/{first['id']}", json={"csrf_token": ocsrf}, headers=H)
    check("...nor deleted twice: 404", r.status_code == 404, f"{r.status_code} {r.text}")

    created, updated, deleted = (events(task, "node.comment." + a) for a in ("create", "update", "delete"))
    check("each act is in graph_event on the item: 2 creates, 2 updates (not the no-op), 2 deletes",
          (len(created), len(updated), len(deleted)) == (2, 2, 2),
          str((len(created), len(updated), len(deleted))))
    check("...and the event names the comment, never its text",
          all(set(e.detail) <= {"comment_id", "author"} for e in created + updated + deleted),
          str([e.detail for e in created + updated + deleted]))


def section_c(owner, ocsrf, member, outsider, xcsrf) -> None:
    print("\n-- C. a comment on private work is as hidden as the work --")
    mk_project("C-PRIV")
    with app.session() as s:
        s.add(NodeVisibility(node_id="C-PRIV", visibility="private",
                             set_by=OWNER_ID, set_at=datetime.now(timezone.utc)))
        s.add(NodeGrant(node_id="C-PRIV", user_id=MEMBER_ID,
                        granted_by=OWNER_ID, granted_at=datetime.now(timezone.utc)))
        s.commit()
    task = mk_task(owner, ocsrf, "C-PRIV", "Hidden task")
    r = owner.post(f"/api/workitem/{task}/comments", json={"body": "secret", "csrf_token": ocsrf}, headers=H)
    cid = r.json()["id"]

    r = outsider.get(f"/api/workitem/{task}/comments")
    check("an outsider listing them gets 404, as for an absent item", r.status_code == 404, str(r.status_code))
    r = outsider.patch(f"/api/comment/{cid}", json={"body": "x", "csrf_token": xcsrf}, headers=H)
    check("...editing one by id: 404, not 403", r.status_code == 404, str(r.status_code))
    r = outsider.request("DELETE", f"/api/comment/{cid}", json={"csrf_token": xcsrf}, headers=H)
    check("...deleting one by id: 404, not 403", r.status_code == 404, str(r.status_code))
    r = outsider.post(f"/api/workitem/{task}/comments", json={"body": "x", "csrf_token": xcsrf}, headers=H)
    check("...commenting on it: 404", r.status_code == 404, str(r.status_code))
    r = member.get(f"/api/workitem/{task}/comments")
    check("the granted member reads the conversation", r.status_code == 200
          and r.json()["count"] == 1, f"{r.status_code} {r.text}")


def section_d(owner, ocsrf) -> None:
    print("\n-- D. deleting the item deletes its comments --")
    task = mk_task(owner, ocsrf, "C-PUB", "Doomed task")
    cid = owner.post(f"/api/workitem/{task}/comments", json={"body": "bye", "csrf_token": ocsrf},
                     headers=H).json()["id"]
    owner.patch(f"/api/comment/{cid}", json={"body": "bye now", "csrf_token": ocsrf}, headers=H)
    r = owner.request("DELETE", f"/api/node/{task}?cascade=1", json={"csrf_token": ocsrf}, headers=H)
    check("DELETE /api/node on a commented, edited item succeeds", r.status_code == 200, f"{r.status_code} {r.text}")
    with app.session() as s:
        left = s.scalars(select(WorkitemComment).where(WorkitemComment.node_id == task)).all()
        revs = s.scalars(select(WorkitemCommentRevision)
                         .where(WorkitemCommentRevision.comment_id == cid)).all()
    check("...and leaves no comment behind", not left, str(len(left)))
    check("...nor a revision of one", not revs, str(len(revs)))


def section_e(owner, ocsrf, outsider) -> None:
    print("\n-- E. search reads comments, through the same access filter --")
    task = mk_task(owner, ocsrf, "C-PUB", "Searchable task")
    owner.post(f"/api/workitem/{task}/comments",
               json={"body": "the quokkaflange is misaligned", "csrf_token": ocsrf}, headers=H)
    r = owner.get("/api/search", params={"q": "quokkaflange", "families": "workitem"})
    items = r.json().get("items", [])
    hit = next((i for i in items if i["id"] == task), None)
    check("/api/search finds the item by a word only its comment holds",
          r.status_code == 200 and hit is not None, f"{r.status_code} {r.text[:300]}")
    check("...matched_in names `comment`", hit is not None and hit["matched_in"] == ["comment"], str(hit))
    check("...and the snippet comes from the comment",
          hit is not None and "quokkaflange" in (hit.get("snippet") or "").lower(), str(hit))
    r = owner.get("/api/workitems", params={"q": "quokkaflange"})
    check("/api/workitems?q= finds it too", r.status_code == 200
          and [i["id"] for i in r.json().get("items", [])] == [task], r.text[:300])

    hidden = mk_task(owner, ocsrf, "C-PRIV", "Hidden searchable task")
    owner.post(f"/api/workitem/{hidden}/comments",
               json={"body": "the zebrawrench is secret", "csrf_token": ocsrf}, headers=H)
    r = owner.get("/api/search", params={"q": "zebrawrench", "families": "workitem"})
    check("the owner finds the private item by its comment", r.json().get("total") == 1, r.text[:200])
    r = outsider.get("/api/search", params={"q": "zebrawrench", "families": "workitem"})
    check("an outsider finds nothing, and the total does not count it",
          r.status_code == 200 and r.json().get("total") == 0 and not r.json().get("items"), r.text[:200])
    r = outsider.get("/api/workitems", params={"q": "zebrawrench"})
    check("...on /api/workitems?q= as well", r.status_code == 200 and r.json().get("total") == 0, r.text[:200])

    gone = mk_task(owner, ocsrf, "C-PUB", "Retracted task")
    cid = owner.post(f"/api/workitem/{gone}/comments",
                     json={"body": "the wombatgasket leaks", "csrf_token": ocsrf}, headers=H).json()["id"]
    owner.request("DELETE", f"/api/comment/{cid}", json={"csrf_token": ocsrf}, headers=H)
    r = owner.get("/api/search", params={"q": "wombatgasket", "families": "workitem"})
    check("a deleted comment is no longer found by search", r.status_code == 200
          and r.json().get("total") == 0, r.text[:200])


def main() -> None:
    print("=" * 78)
    print("Atlas workitem-comment verification")
    print(f"DB:      {os.environ['ATLAS_DB_URL']}")
    print("=" * 78)

    app.startup()
    mk_user(OWNER_ID, "c-owner", role="owner")
    mk_user(MEMBER_ID, "c-member")
    mk_user(OUTSIDER_ID, "c-outsider")
    owner, ocsrf = sign_in("c-owner")
    member, mcsrf = sign_in("c-member")
    outsider, xcsrf = sign_in("c-outsider")

    try:
        section_a()
        section_b(owner, ocsrf, member, mcsrf)
        section_c(owner, ocsrf, member, outsider, xcsrf)
        section_d(owner, ocsrf)
        section_e(owner, ocsrf, outsider)
    finally:
        DB.stop()

    print("\n" + "=" * 78)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
