"""Verify the first-run setup guide's shared core (STORY-atlas-setup-guide-adrs).

The story's five criteria, and the section that proves each:

  1. On an empty install the guide starts, walks every topic of the question set, and can be
     resumed (a new client, and a restarted server, carry on where it stood).          -> C, D
  2. Each answered topic produces ONE ADR in the registry: status Proposed, source atlas, a
     per-project id, the answer as its content, one `node.adr.write` attributed to the
     caller (an owner's agent's carries the person).                                    -> D
  3. A person accepts a guide-made ADR (owner session only; every token and a member are
     refused, and the registry's generic PUT will not change one); a changed answer creates
     a new ADR superseding the old, the old byte-identical; the same answer writes nothing. -> E, F
  4. The question set is data, versioned, its answer keys pinned; an extra file named by
     ATLAS_SETUP_QUESTIONS_FILE adds a topic with no code change, and a broken one fails
     naming the problem.                                                                  -> A, G
  5. An install that never runs the guide behaves as today: no row, no ADR, the existing
     routes' answers unchanged, the homelab-shaped database byte-identical across reads of
     the guide and a restart.                                                             -> B
  (H: who may call it. A member, a member's admin token, an owner's token without `admin`
  and an anonymous caller are refused, and nothing is written.)
  (I: project RBAC. With ATLAS_RBAC=projects and no role binding, the owner's session and the
  owner's admin agent record and accept guide ADRs on atlas-system (the named exemption,
  owner decision, 2026-10-26); the same callers are still refused on another project through the guide
  and on atlas-system through PUT /api/adrs/new; a member is refused; RBAC off is unchanged.)

Run:
    python verify_setup_guide.py
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testdb  # noqa: E402

try:
    DB = testdb.start()
except testdb.TestDatabaseUnavailable as e:  # pragma: no cover - no skip path, ever
    print(f"FATAL: {e}")
    sys.exit(1)

os.environ["ATLAS_DB_URL"] = DB.url
os.environ["ATLAS_RESEED"] = "1"
os.environ.pop("ATLAS_SEED", None)              # phase B is the homelab default
os.environ.pop("ATLAS_SETUP_QUESTIONS_FILE", None)
os.environ.pop("ATLAS_RBAC", None)
os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
ORIGIN = "https://testserver"

from sqlalchemy import MetaData, create_engine, func, select, text  # noqa: E402

import app  # noqa: E402
import auth  # noqa: E402
import passwords  # noqa: E402
import setup_guide  # noqa: E402
import testseed  # noqa: E402
from models_adr import AdrRecord  # noqa: E402
from models_auth import AuthUser  # noqa: E402
from models_graph import GraphEvent, Node  # noqa: E402
from models_setup import SetupAnswer  # noqa: E402
import distribution  # noqa: E402

#: Phase B boots the distribution's default profile (STORY-oss-23 A3b). Under the homelab's
#: copy that is `homelab`, which never seeds the system node set, so the install has no
#: `atlas-system` node and the guide names no project; any other default seeds the set on a
#: fresh graph, and the guide then names `atlas-system`, as phase C proves for `none`.
DEFAULT_SYSTEM_PROJECT = None if distribution.DEFAULT_SEED_PROFILE == "homelab" else "atlas-system"

ENGINE = create_engine(DB.url)
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


PW = "Correct-Horse-Battery-Setup-9!"
OWNER, MEMBER = "u-setup-owner", "u-setup-member"


def mk_user(uid, subject, role="member", is_service=False, person=None):
    with app.session() as s:
        s.add(AuthUser(id=uid, subject=subject, name=subject, role=role, is_service=is_service,
                       must_change_password=False,
                       password_hash=None if is_service else passwords.hash_password(PW),
                       person_user_id=person, created_at=datetime.now(timezone.utc)))
        s.commit()


def sign_in(username: str):
    from fastapi.testclient import TestClient
    c = TestClient(app.app, base_url=ORIGIN)
    page = c.get("/auth/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    c.post("/auth/login", data={"username": username, "password": PW, "csrf_token": token},
           headers={"Origin": ORIGIN}, follow_redirects=False)
    return c, c.get("/api/me").json()["csrf_token"]


def bearer(uid: str, scopes: list[str], person: str | None):
    from fastapi.testclient import TestClient
    mk_user(uid, uid, is_service=True, person=person)
    with app.session() as s:
        raw, _ = auth.mint_service_token(s, owner=s.get(AuthUser, uid), scopes=scopes,
                                         label=uid)
        s.commit()
    return TestClient(app.app, base_url=ORIGIN, headers={"Authorization": f"Bearer {raw}"})


def anon():
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url=ORIGIN)


def count(model) -> int:
    with app.session() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


def adr_events() -> int:
    with app.session() as s:
        return s.scalar(select(func.count()).select_from(GraphEvent)
                        .where(GraphEvent.action == "node.adr.write")) or 0


def last_event() -> GraphEvent:
    with app.session() as s:
        return s.scalars(select(GraphEvent).order_by(GraphEvent.id.desc()).limit(1)).first()


def row_json(adr_id: str) -> str:
    with ENGINE.connect() as conn:
        return conn.execute(text("SELECT to_jsonb(a)::text FROM adr_record a WHERE adr_id = :i"),
                            {"i": adr_id}).scalar() or ""


def digest() -> dict:
    """(rows, sha256 of every row) per table, auth_* excluded (a sign-in writes a session)."""
    meta = MetaData()
    meta.reflect(bind=ENGINE)
    out = {}
    with ENGINE.connect() as conn:
        for name, tbl in sorted(meta.tables.items()):
            if name.startswith("auth_") or name == "audit_read":
                continue
            rows = sorted(repr(tuple(r)) for r in conn.execute(select(tbl)).all())
            out[name] = (len(rows), hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest())
    return out


def post_answer(client, csrf, topic, answers, **kw):
    body = {"topic": topic, "answers": answers, **kw}
    if csrf is not None:
        body["csrf_token"] = csrf
    return client.post("/api/setup/answers", json=body, headers={"Origin": ORIGIN})


# --------------------------------------------------------------------------- #
print("=" * 78)
print("A. the question set is data, versioned, and its answer keys are pinned")
print("=" * 78)

#: THE CONTRACT the guardrail matrix and the first CI/CD flow read (setup_guide.py's header).
#: Written out here, not derived, on purpose: a rename or a dropped value must turn this red.
KEYS = {
    "guardrails.agent_actions": ("multi", "read file_work write_docs open_pr merge deploy_nonprod "
                                          "deploy_prod change_config"),
    "guardrails.approval_gates": ("multi", "merge deploy_nonprod deploy_prod schema_change "
                                           "acceptance_change secret_change access_change"),
    "guardrails.who_merges": ("choice", "human_only agent_after_approval agent_when_green"),
    "guardrails.who_deploys": ("choice", "human_only agent_after_approval pipeline_on_merge"),
    "guardrails.notes": ("text", ""),
    "cicd.ci_platform": ("choice", "github_actions gitlab_ci jenkins azure_pipelines other none"),
    "cicd.checks": ("multi", "build unit_tests integration_tests lint type_check sast "
                             "dependency_scan secret_scan"),
    "cicd.gates": ("multi", "build unit_tests integration_tests lint type_check sast "
                            "dependency_scan secret_scan"),
    "cicd.deploy_trigger": ("choice", "manual on_merge on_tag scheduled"),
    "environments.names": ("text", ""),
    "environments.production": ("text", ""),
    "environments.promotion": ("choice", "sequential independent"),
    "identity.login": ("choice", "local oidc both"),
    "identity.rbac": ("choice", "off projects"),
    "identity.default_visibility": ("choice", "shared private"),
    "secrets.storage": ("choice", "files vault cloud_secret_manager ci_secrets other"),
    "secrets.agent_access": ("choice", "never path_only scoped"),
    "secrets.rotation": ("choice", "manual scheduled on_incident"),
    "work_hierarchy.levels": ("choice", "epic_story_task story_task task_only"),
    "work_hierarchy.who_files": ("multi", "people agents"),
    "work_hierarchy.acceptance": ("choice", "people agent_proposes"),
}

QS = setup_guide.load_question_set({})
check("the shipped set loads, version 1, with no extra file", QS["version"] == "1"
      and QS["extra"] is None, f"{QS['version']} extra={QS['extra']}")
check("its topics are the story's six, in order",
      [t["id"] for t in QS["topics"]] == ["guardrails", "cicd", "environments", "identity",
                                          "secrets", "work_hierarchy"], [t["id"] for t in QS["topics"]])
live = {f"{t['id']}.{q['id']}": (q["type"], " ".join(o["value"] for o in q["options"]))
        for t in QS["topics"] for q in t["questions"]}
check("every answer key, type and option value equals the pinned contract",
      live == KEYS, str({k: live.get(k) for k in set(live) ^ set(KEYS) | {
          k for k in live if k in KEYS and live[k] != KEYS[k]}}))
shipped_text = setup_guide.SHIPPED_FILE.read_text(encoding="utf-8")
check("the shipped file is plain data with its own `version`",
      json.loads(shipped_text)["version"] == "1" and setup_guide.SHIPPED_FILE.suffix == ".json")

# A check that cannot fail proves nothing: perturb the shipped data and require the loader to
# notice, naming the place.
doc = json.loads(shipped_text)
bad = copy.deepcopy(doc)
bad["topics"][1]["questions"][0]["type"] = "choose"
try:
    setup_guide._validate(bad, "perturbed", set())
    check("a perturbed question type is refused", False)
except setup_guide.QuestionSetError as e:
    check("a perturbed question type is refused, naming topics[1].questions[0].type",
          "topics[1].questions[0].type" in str(e), str(e))
bad = copy.deepcopy(doc)
bad["topics"][0]["adr"]["decision"] += " {nonexistent}"
try:
    setup_guide._validate(bad, "perturbed", set())
    check("an ADR template naming an unknown question is refused", False)
except setup_guide.QuestionSetError as e:
    check("an ADR template naming an unknown question is refused, naming it",
          "nonexistent" in str(e) and "topics[0].adr.decision" in str(e), str(e))
check("answers_list splits a comma-separated text answer for every reader",
      setup_guide.answers_list(" dev, test ,, prod ") == ["dev", "test", "prod"])

# --------------------------------------------------------------------------- #
print("\n" + "=" * 78)
print("B. an install that never runs the guide behaves as today (homelab-shaped database)")
print("=" * 78)

app.startup()                                    # homelab default profile, fresh graph
os.environ["ATLAS_RESEED"] = "0"
mk_user(OWNER, "setup-owner", role="owner")
mk_user(MEMBER, "setup-member")
with app.session() as s:
    testseed.build(s)
    s.add(Node(id="p-home", type="project", name="p-home", attributes={}, attention_count=0,
               completion=0.0, origin="atlas"))
    s.commit()
owner, owner_csrf = sign_in("setup-owner")
member, member_csrf = sign_in("setup-member")
sync = bearer("u-setup-sync", ["read", "write"], None)
r = sync.put("/api/adrs/HM001", json={"project": "p-home", "title": "A git decision",
                                      "slug": "a-git-decision", "status": "accepted",
                                      "date": "2026-10-01", "content": "# A git decision\n"})
check("a git-synced ADR exists, as on the homelab", r.status_code == 201, r.text[:160])

check("the setup_answer table exists and is empty", count(SetupAnswer) == 0, str(count(SetupAnswer)))
ROUTES = ["/api/adrs", "/api/adrs/HM001", "/api/search?q=fixture", "/api/graph/outline",
          "/api/workitems", "/api/health"]


def answers_of(client) -> dict:
    out = {}
    for p in ROUTES:
        resp = client.get(p)
        body = resp.json()
        if p == "/api/health":  # carries timings and a clock; its shape is the claim
            body = sorted(body)
        out[p] = (resp.status_code, json.dumps(body, sort_keys=True))
    return out


before_routes, before_db = answers_of(owner), digest()
g = owner.get("/api/setup")
gj = g.json()
check("GET /api/setup on it answers 200 and reads as not started",
      g.status_code == 200 and gj["started"] is False and gj["answered"] == 0, g.text[:200])
with app.session() as s:
    has_system = s.get(Node, "atlas-system") is not None
check(f"...naming {DEFAULT_SYSTEM_PROJECT or 'no project'}: the default profile "
      f"({distribution.DEFAULT_SEED_PROFILE}) {'gave' if DEFAULT_SYSTEM_PROJECT else 'gave no'} atlas-system node",
      gj.get("project") == DEFAULT_SYSTEM_PROJECT and has_system == (DEFAULT_SYSTEM_PROJECT is not None),
      f"{gj.get('project')} node={has_system}")
owner.get("/api/setup?topic=cicd")
app.startup()                                    # a restart, RESEED off
check("after reading the guide and a restart, no setup row and no guide ADR exist",
      count(SetupAnswer) == 0 and count(AdrRecord) == 1, f"{count(SetupAnswer)} {count(AdrRecord)}")
check("criterion 5: every existing route answers exactly as before",
      answers_of(owner) == before_routes,
      str([p for p in ROUTES if answers_of(owner)[p] != before_routes[p]]))
after_db = digest()
check("criterion 5: the homelab-shaped database is byte-identical (every non-auth table)",
      after_db == before_db, str([t for t in after_db if after_db.get(t) != before_db.get(t)]))

# --------------------------------------------------------------------------- #
print("\n" + "=" * 78)
print("C. an empty install: the guide starts")
print("=" * 78)

os.environ["ATLAS_SEED"] = "none"
os.environ["ATLAS_RESEED"] = "1"
app.startup()                                    # wipes the graph, keeps the accounts
os.environ["ATLAS_RESEED"] = "0"
with app.session() as s:
    check("the empty install has its atlas-system node and no ADR",
          s.get(Node, "atlas-system") is not None and count(AdrRecord) == 0)
check("...and no guide state", count(SetupAnswer) == 0)

agent = bearer("u-setup-owner-agent", ["read", "write", "admin"], OWNER)
g = owner.get("/api/setup").json()
check("criterion 1: the guide starts at the first topic's first question",
      g["next"] == {"topic": "guardrails", "question": "agent_actions"} and g["started"] is False
      and g["total"] == 6 and g["project"] == "atlas-system", json.dumps(g)[:240])
check("...and hands over that topic in full, with every question and option",
      g["ask"]["id"] == "guardrails" and len(g["ask"]["questions"]) == 5
      and all("options" in q and "required" in q for q in g["ask"]["questions"]))
check("an owner's admin-scoped agent reads the same", agent.get("/api/setup").json()["next"]
      == g["next"])

# --------------------------------------------------------------------------- #
print("\n" + "=" * 78)
print("H. who may run it; validation refuses and writes nothing")
print("=" * 78)

GUARD = {"agent_actions": ["read", "file_work", "open_pr"], "approval_gates": ["merge", "deploy_prod"],
         "who_merges": "human_only", "who_deploys": "agent_after_approval"}
n_adr, n_ev = count(AdrRecord), adr_events()
member_admin = bearer("u-setup-member-agent", ["read", "write", "admin"], MEMBER)
owner_rw = bearer("u-setup-owner-rw", ["read", "write"], OWNER)
owner_ro = bearer("u-setup-owner-ro", ["read"], OWNER)
check("a member's session: GET 403", member.get("/api/setup").status_code == 403)
check("a member's session: POST 403",
      post_answer(member, member_csrf, "guardrails", GUARD).status_code == 403)
check("a member's admin token: 403", member_admin.get("/api/setup").status_code == 403
      and post_answer(member_admin, None, "guardrails", GUARD).status_code == 403)
r = owner_rw.get("/api/setup")
check("an owner's token without admin: GET 403", r.status_code == 403, r.text[:120])
r = post_answer(owner_rw, None, "guardrails", GUARD)
check("...and POST 403 naming the admin scope", r.status_code == 403 and "admin" in r.text, r.text[:160])
r = post_answer(owner_ro, None, "guardrails", GUARD)
check("an owner's read-only token: POST 403 on scope", r.status_code == 403 and "admin" in r.text)
check("anonymous: 401", anon().get("/api/setup", headers={"Accept": "application/json"}).status_code == 401)
r = owner.post("/api/setup/answers", json={"topic": "guardrails", "answers": GUARD},
               headers={"Origin": ORIGIN})
check("an owner session without the CSRF token: 403", r.status_code == 403, r.text[:120])

for label, topic, answers, field in [
    ("an unknown topic", "nope", GUARD, "topic"),
    ("an unknown question", "guardrails", {**GUARD, "colour": "red"}, "answers.colour"),
    ("a value that is not an option", "guardrails", {**GUARD, "who_merges": "anyone"},
     "answers.who_merges"),
    ("a missing required answer", "guardrails", {k: v for k, v in GUARD.items() if k != "who_deploys"},
     "answers.who_deploys"),
    ("a multi answer that is not a list", "guardrails", {**GUARD, "agent_actions": "read"},
     "answers.agent_actions"),
    ("a multi value that is not an option", "guardrails", {**GUARD, "agent_actions": ["fly"]},
     "answers.agent_actions"),
    ("a text answer over the limit", "guardrails", {**GUARD, "notes": "x" * 2001}, "answers.notes"),
]:
    r = post_answer(owner, owner_csrf, topic, answers)
    check(f"{label}: 400 naming {field}", r.status_code == 400 and r.json().get("field") == field,
          r.text[:160])
r = owner.get("/api/setup?topic=nope")
check("GET ?topic= an unknown topic: 400 naming topic",
      r.status_code == 400 and r.json().get("field") == "topic")
check("none of it wrote an ADR, an event or a guide row",
      (count(AdrRecord), adr_events(), count(SetupAnswer)) == (n_adr, n_ev, 0))

# --------------------------------------------------------------------------- #
print("\n" + "=" * 78)
print("D. walking every topic: one Proposed ADR per answer, resumable")
print("=" * 78)

ANSWERS = {
    "guardrails": GUARD,
    "cicd": {"ci_platform": "github_actions", "checks": ["lint", "build", "unit_tests"],
             "gates": ["unit_tests", "build"], "deploy_trigger": "manual"},
    "environments": {"names": "dev, test, prod", "production": "prod", "promotion": "sequential"},
    "identity": {"login": "local", "rbac": "off", "default_visibility": "shared"},
    "secrets": {"storage": "files", "agent_access": "path_only", "rotation": "manual"},
    "work_hierarchy": {"levels": "epic_story_task", "who_files": ["agents", "people"],
                       "acceptance": "agent_proposes"},
}
LABEL = {"guardrails": "only a person", "cicd": "GitHub Actions", "environments": "dev, test, prod",
         "identity": "Atlas's own accounts and passwords",
         "secrets": "pass a secret's path to a tool, never read the value",
         "work_hierarchy": "epics contain stories, stories contain tasks"}
RAW = {"guardrails": "| `guardrails.who_merges` | `human_only` |",
       "cicd": "| `cicd.gates` | `build`, `unit_tests` |",
       "environments": "| `environments.names` | dev, test, prod |",
       "identity": "| `identity.rbac` | `off` |",
       "secrets": "| `secrets.agent_access` | `path_only` |",
       "work_hierarchy": "| `work_hierarchy.who_files` | `people`, `agents` |"}

made: dict[str, str] = {}
client, csrf = owner, owner_csrf
for i, topic in enumerate(QS["topics"], start=1):
    tid = topic["id"]
    if tid == "environments":
        # RESUME: a new client (a fresh sign-in) and a restarted server both carry on here.
        client, csrf = sign_in("setup-owner")
        app.startup()
        g = client.get("/api/setup").json()
        check("criterion 1: after a client and a server restart the guide resumes at environments",
              g["next"] == {"topic": "environments", "question": "names"} and g["answered"] == 2,
              json.dumps(g["next"]))
        # ...and the owner's agent (the MCP interview's credential) answers this topic.
        client, csrf = agent, None
    elif tid == "identity":
        client, csrf = owner, owner_csrf
    ev0 = adr_events()
    r = post_answer(client, csrf, tid, ANSWERS[tid])
    j = r.json()
    want = f"SYS{i:03d}"
    check(f"{tid}: 201 with the allocated per-project id {want}, Proposed",
          r.status_code == 201 and j.get("adr_id") == want and j.get("status") == "proposed"
          and j.get("project") == "atlas-system", r.text[:200])
    made[tid] = j.get("adr_id")
    d = owner.get(f"/api/adrs/{want}").json()
    check(f"{tid}: the registry holds it, source atlas, status proposed, in atlas-system",
          d.get("source") == "atlas" and d.get("status") == "proposed"
          and d.get("project") == "atlas-system" and d.get("title") == topic["adr"]["title"], str(d)[:200])
    content = d.get("content") or ""
    check(f"{tid}: the answer is its content: MADR sections, the readable answer and the raw keys",
          all(h in content for h in ("## Context and problem statement", "## Decision",
                                     "## Consequences", "## Answers"))
          and LABEL[tid] in content and RAW[tid] in content, content[-300:])
    ev = last_event()
    check(f"{tid}: exactly one node.adr.write, on atlas-system, by the caller",
          adr_events() == ev0 + 1 and ev.action == "node.adr.write" and ev.node_id == "atlas-system"
          and (ev.detail or {}).get("adr_id") == want, f"{adr_events() - ev0} {ev.action}")
    if client is agent:
        check(f"{tid}: written by the owner's agent, accountable to the owner (ADR 0034)",
              ev.actor_kind == "token" and ev.actor_user_id == "u-setup-owner-agent"
              and ev.responsible_user_id == OWNER, f"{ev.actor_kind} {ev.actor_user_id} {ev.responsible_user_id}")
    else:
        check(f"{tid}: written by the owner's session", ev.actor_kind == "session"
              and ev.actor_user_id == OWNER, f"{ev.actor_kind} {ev.actor_user_id}")
    nxt = QS["topics"][i]["id"] if i < len(QS["topics"]) else None
    check(f"{tid}: the response names the next topic ({nxt})",
          (j.get("next") or {}).get("topic") == nxt, str(j.get("next")))

g = owner.get("/api/setup").json()
check("criterion 1: every topic answered, the guide complete, nothing next",
      g["complete"] is True and g["next"] is None and g["answered"] == 6 and g["ask"] is None)
check("every topic names its ADR, Proposed, awaiting acceptance",
      [t["adr_id"] for t in g["topics"]] == [made[t["id"]] for t in QS["topics"]]
      and g["awaiting_acceptance"] == [made[t["id"]] for t in QS["topics"]])
check("the stored answers are canonical: multi values in the options' order",
      next(t for t in g["topics"] if t["id"] == "cicd")["answers"]["checks"]
      == ["build", "unit_tests", "lint"])
with app.session() as s:
    rows = s.scalars(select(SetupAnswer).order_by(SetupAnswer.id)).all()
    check("one guide row per answer, each naming its ADR, the version and who answered",
          len(rows) == 6 and all(r.question_set_version == "1" and r.adr_id and r.answered_by_subject
                                 for r in rows), str(len(rows)))

# --------------------------------------------------------------------------- #
print("\n" + "=" * 78)
print("E. a person accepts a guide-made ADR; nobody else can")
print("=" * 78)

target = made["guardrails"]


def accept(client, adr_id, csrf):
    body = {} if csrf is None else {"csrf_token": csrf}
    return client.post(f"/api/setup/adrs/{adr_id}/accept", json=body, headers={"Origin": ORIGIN})


before = row_json(target)
r = accept(agent, target, None)
check("the owner's own admin-scoped agent is refused: a decision needs a person",
      r.status_code == 403 and r.json().get("error") == "decision_needs_a_person", r.text[:160])
r = accept(owner_ro, target, None)
check("a read-only token is refused on scope first", r.status_code == 403 and "admin" in r.text)
r = accept(member, target, member_csrf)
check("a member's session is refused", r.status_code == 403 and r.json().get("error") == "forbidden")
r = accept(owner, target, None)
check("an owner session without CSRF is refused", r.status_code == 403)
r = agent.put(f"/api/adrs/{target}", json={"project": "atlas-system", "title": "x", "source": "atlas",
                                           "status": "accepted"})
check("the registry's generic PUT will not change a guide-made ADR for a token (403 guide_adr)",
      r.status_code == 403 and r.json().get("error") == "guide_adr", r.text[:160])
r = owner.put(f"/api/adrs/{target}", json={"project": "atlas-system", "title": "x", "source": "atlas",
                                           "status": "accepted", "csrf_token": owner_csrf},
              headers={"Origin": ORIGIN})
check("...nor for a session: accepting goes through the guide's route",
      r.status_code == 403 and r.json().get("error") == "guide_adr", r.text[:160])
check("after all of that the ADR is unchanged", row_json(target) == before)

ev0 = adr_events()
r = accept(owner, target, owner_csrf)
check("an owner session accepts it", r.status_code == 200 and r.json().get("status") == "accepted",
      r.text[:160])
d = owner.get(f"/api/adrs/{target}").json()
check("the registry now reads accepted, by the person, content and title untouched",
      d.get("status") == "accepted" and (d.get("status_raw") or "").startswith("Accepted by setup-owner")
      and d.get("content") == json.loads(before)["content"] and d.get("title") == json.loads(before)["title"],
      str(d)[:200])
ev = last_event()
check("...recorded as one node.adr.write by the owner's session, fields status and status_raw",
      adr_events() == ev0 + 1 and ev.actor_kind == "session" and ev.actor_user_id == OWNER
      and (ev.detail or {}).get("fields") == ["status", "status_raw"], str(ev.detail))
r = accept(owner, target, owner_csrf)
check("accepting it again is a 409 not_proposed", r.status_code == 409
      and r.json().get("error") == "not_proposed")
r = accept(owner, "SYS999", owner_csrf)
check("an id the guide did not record is a 404", r.status_code == 404)
g = owner.get("/api/setup").json()
check("GET /api/setup shows it accepted and no longer awaiting",
      next(t for t in g["topics"] if t["id"] == "guardrails")["adr_status"] == "accepted"
      and target not in g["awaiting_acceptance"])

# The guard is scoped to guide-made ADRs: any other Atlas-native ADR is written as before.
r = agent.put("/api/adrs/new", json={"project": "atlas-system", "title": "Not from the guide"})
other = r.json().get("id")
r2 = agent.put(f"/api/adrs/{other}", json={"project": "atlas-system", "title": "Not from the guide",
                                           "source": "atlas", "status": "accepted"})
check("an Atlas-native ADR the guide did not make is still updated by PUT as before",
      r.status_code == 201 and r2.status_code == 200, f"{r.status_code} {r2.status_code} {r2.text[:120]}")

# --------------------------------------------------------------------------- #
print("\n" + "=" * 78)
print("F. a changed answer supersedes; the same answer writes nothing")
print("=" * 78)

old = made["environments"]
old_row = row_json(old)
n_adr, n_ev, n_rows = count(AdrRecord), adr_events(), count(SetupAnswer)
r = post_answer(owner, owner_csrf, "environments", ANSWERS["environments"])
check("the same answers again: 200 unchanged, the same ADR",
      r.status_code == 200 and r.json().get("unchanged") is True and r.json().get("adr_id") == old)
check("...and nothing written: no ADR, no event, no row",
      (count(AdrRecord), adr_events(), count(SetupAnswer)) == (n_adr, n_ev, n_rows))
r = post_answer(owner, owner_csrf, "environments", ANSWERS["environments"], project="p-other")
check("the same answers naming another project are still unchanged (nothing to move)",
      r.status_code == 200 and r.json().get("unchanged") is True)

changed = {"names": "dev, staging, prod", "production": "prod", "promotion": "sequential"}
r = post_answer(owner, owner_csrf, "environments", changed, project="p-other")
check("a changed answer naming another project is a 400 naming project",
      r.status_code == 400 and r.json().get("field") == "project", r.text[:160])
r = post_answer(owner, owner_csrf, "environments", changed)
j = r.json()
new = j.get("adr_id")
check("criterion 3: a changed answer makes a NEW Proposed ADR, the next id, superseding the old",
      r.status_code == 201 and new == f"SYS{n_adr + 1:03d}" and j.get("supersedes") == old
      and j.get("status") == "proposed", r.text[:200])
check("criterion 3: the old ADR is byte-identical", row_json(old) == old_row)
dn, do = owner.get(f"/api/adrs/{new}").json(), owner.get(f"/api/adrs/{old}").json()
check("the registry links them both ways through its supersedes mechanism",
      [x["id"] for x in dn.get("supersedes", [])] == [old]
      and new in [x["id"] for x in do.get("superseded_by", [])])
check("the new ADR's content carries the new answer and names what it supersedes",
      "dev, staging, prod" in (dn.get("content") or "") and f"- Supersedes: {old}" in dn["content"])
g = owner.get("/api/setup").json()
env = next(t for t in g["topics"] if t["id"] == "environments")
check("GET /api/setup now names the new ADR and answers for environments",
      env["adr_id"] == new and env["answers"]["names"] == "dev, staging, prod")
r = accept(owner, old, owner_csrf)
check("the superseded ADR can no longer be accepted: 409 naming its successor",
      r.status_code == 409 and r.json().get("error") == "superseded" and new in r.text, r.text[:160])
with app.session() as s:
    hist = s.scalars(select(SetupAnswer).where(SetupAnswer.topic_id == "environments")
                     .order_by(SetupAnswer.id)).all()
    check("the guide keeps both answers as history, the newer naming what it superseded",
          [h.adr_id for h in hist] == [old, new] and hist[-1].supersedes_adr_id == old)

# --------------------------------------------------------------------------- #
print("\n" + "=" * 78)
print("G. an install adds its own topic with no code change; a broken file fails clearly")
print("=" * 78)

EXTRA = {"version": "org-1", "topics": [{
    "id": "release_calendar", "title": "Release calendar", "summary": "When releases may happen.",
    "questions": [{"id": "freeze", "type": "choice", "prompt": "Is there a change freeze?",
                   "options": [{"value": "none", "label": "no freeze"},
                               {"value": "quarter_end", "label": "around quarter end"}]},
                  {"id": "window", "type": "text", "prompt": "When may production change?",
                   "required": False}],
    "adr": {"title": "Release calendar", "context": "Releases affect people outside the team.",
            "decision": "Change freeze: {freeze}. Production may change: {window}.",
            "consequences": "Deploys outside the window need a person's approval."}}]}

with tempfile.TemporaryDirectory(prefix="atlas-setup-guide-") as tmp:
    good = Path(tmp) / "extra.json"
    good.write_text(json.dumps(EXTRA), encoding="utf-8")
    os.environ["ATLAS_SETUP_QUESTIONS_FILE"] = str(good)
    g = owner.get("/api/setup").json()
    check("criterion 4: the extra topic is asked after the shipped ones, versioned 1+org-1",
          [t["id"] for t in g["topics"]][-1] == "release_calendar" and g["total"] == 7
          and g["version"] == "1+org-1" and g["topics"][-1]["source"] == "extra"
          and g["extra"] == {"file": "extra.json", "version": "org-1"}, json.dumps(g)[:200])
    check("...and is the next question, handed over in full",
          g["next"] == {"topic": "release_calendar", "question": "freeze"}
          and g["ask"]["id"] == "release_calendar")
    r = post_answer(agent, None, "release_calendar", {"freeze": "quarter_end"})
    check("answering it makes a Proposed ADR like any other",
          r.status_code == 201 and r.json().get("status") == "proposed", r.text[:160])
    xid = r.json().get("adr_id")
    xd = owner.get(f"/api/adrs/{xid}").json()
    check("...with its own template filled, the optional question not stated",
          "Change freeze: around quarter end. Production may change: not stated." in (xd.get("content") or ""))
    with app.session() as s:
        row = s.scalar(select(SetupAnswer).where(SetupAnswer.adr_id == xid))
        check("...recorded under question set 1+org-1", row is not None and row.question_set_version == "1+org-1")
    rc = subprocess.run([sys.executable, str(HERE / "setup_guide.py"), "--check", str(good)],
                        capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=HERE)
    check("`setup_guide.py --check` accepts the file", rc.returncode == 0, rc.stderr.strip())

    broken = {
        "not JSON": ("{not json", "not valid JSON"),
        "a topic that redefines a shipped one": (
            json.dumps({**EXTRA, "topics": [{**EXTRA["topics"][0], "id": "guardrails"}]}),
            "topics[0].id: topic 'guardrails' is already defined"),
        "a choice question with no options": (
            json.dumps({**EXTRA, "topics": [{**EXTRA["topics"][0], "questions": [
                {"id": "freeze", "type": "choice", "prompt": "?"}]}]}),
            "topics[0].questions[0].options"),
        "no version": (json.dumps({"topics": EXTRA["topics"]}), "version: must be a non-empty string"),
    }
    n_adr, n_rows = count(AdrRecord), count(SetupAnswer)
    for label, (content, needle) in broken.items():
        p = Path(tmp) / "broken.json"
        p.write_text(content, encoding="utf-8")
        os.environ["ATLAS_SETUP_QUESTIONS_FILE"] = str(p)
        r = owner.get("/api/setup")
        check(f"a broken extra file ({label}): GET is a 503 naming the file and the problem",
              r.status_code == 503 and r.json().get("error") == "bad_question_set"
              and "broken.json" in r.json().get("detail", "") and needle in r.json()["detail"],
              r.text[:200])
        r = post_answer(owner, owner_csrf, "cicd", ANSWERS["cicd"])
        check(f"...and POST too ({label})", r.status_code == 503)
        rc = subprocess.run([sys.executable, str(HERE / "setup_guide.py"), "--check", str(p)],
                            capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=HERE)
        check(f"...and `--check` exits 1 naming it ({label})", rc.returncode == 1 and needle in rc.stderr,
              rc.stderr.strip()[:160])
    os.environ["ATLAS_SETUP_QUESTIONS_FILE"] = str(Path(tmp) / "missing.json")
    r = owner.get("/api/setup")
    check("a file that does not exist: 503 saying it cannot be read",
          r.status_code == 503 and "cannot be read" in r.json().get("detail", ""), r.text[:160])
    check("no broken file wrote anything", (count(AdrRecord), count(SetupAnswer)) == (n_adr, n_rows))

os.environ.pop("ATLAS_SETUP_QUESTIONS_FILE", None)
g = owner.get("/api/setup").json()
check("unset again: the shipped set only, and the extra topic's answer is kept as unlisted",
      g["total"] == 6 and g["version"] == "1" and g["unlisted_topics"] == ["release_calendar"]
      and g["complete"] is True, json.dumps({k: g[k] for k in ("total", "version", "unlisted_topics")}))

# --------------------------------------------------------------------------- #
print("\n" + "=" * 78)
print("I. project RBAC: the guide's one exemption, atlas-system only")
print("=" * 78)

import rbac  # noqa: E402
import settings  # noqa: E402
from models_auth import ProjectRoleBinding  # noqa: E402

H = {"Origin": ORIGIN}
with app.session() as s:
    s.add(Node(id="p-rbac-other", type="project", name="p-rbac-other", attributes={},
               attention_count=0, completion=0.0, origin="atlas"))
    s.commit()
PROBE = {"version": "rbac-1", "topics": [{**EXTRA["topics"][0], "id": "rbac_probe",
                                          "title": "RBAC probe"}]}
WH2 = {"levels": "story_task", "who_files": ["people"], "acceptance": "people"}
SEC2 = {"storage": "vault", "agent_access": "scoped", "rotation": "scheduled"}
SEC3 = {"storage": "vault", "agent_access": "scoped", "rotation": "on_incident"}


def forbidden_on(r, project) -> bool:
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return (r.status_code == 403 and j.get("error") == "forbidden"
            and j.get("needed_role") == "editor" and j.get("project") == project)


def bindings() -> int:
    return count(ProjectRoleBinding)


check("nobody holds a project role binding: every role below is the shared default's",
      bindings() == 0, str(bindings()))

with tempfile.TemporaryDirectory(prefix="atlas-setup-rbac-") as tmp:
    probe = Path(tmp) / "probe.json"
    probe.write_text(json.dumps(PROBE), encoding="utf-8")
    os.environ["ATLAS_SETUP_QUESTIONS_FILE"] = str(probe)

    # RBAC on, shared projects read at `viewer`, and the instance owner NOT implicitly owner on
    # every project: the owner's session and the owner's agent both hold only `viewer` on
    # atlas-system, so every check below is decided by the exemption and nothing else.
    prev_rbac = rbac.use(settings.RbacSettings(mode="projects", shared_project_role="viewer",
                                               instance_admin_implicit_project_owner=False))
    try:
        n_adr, n_ev = count(AdrRecord), adr_events()
        r = owner.put("/api/adrs/new", json={"project": "atlas-system", "title": "Direct, no binding",
                                             "csrf_token": owner_csrf}, headers=H)
        check("RBAC on: the owner's session is refused on atlas-system by PUT /api/adrs/new "
              "(403 needs editor): the generic route has no exemption", forbidden_on(r, "atlas-system"),
              r.text[:200])
        r = agent.put("/api/adrs/new", json={"project": "atlas-system", "title": "Direct, no binding"})
        check("...and so is the owner's admin agent", forbidden_on(r, "atlas-system"), r.text[:200])
        check("...and neither wrote anything", (count(AdrRecord), adr_events()) == (n_adr, n_ev))

        prev_sec = owner.get("/api/setup").json()
        prev_sec = next(t for t in prev_sec["topics"] if t["id"] == "secrets")["adr_id"]
        r = post_answer(owner, owner_csrf, "secrets", SEC2)
        j = r.json()
        check("RBAC on: the owner's session records a guide ADR on atlas-system with no binding",
              r.status_code == 201 and j.get("project") == "atlas-system"
              and j.get("status") == "proposed" and j.get("supersedes") == prev_sec, r.text[:200])
        sec2 = j.get("adr_id")
        ev = last_event()
        check("...through the registry's one writer: one node.adr.write, by the owner's session",
              adr_events() == n_ev + 1 and ev.action == "node.adr.write" and ev.node_id == "atlas-system"
              and (ev.detail or {}).get("adr_id") == sec2 and ev.actor_kind == "session"
              and ev.actor_user_id == OWNER, f"{ev.action} {ev.detail}")

        r = post_answer(agent, None, "secrets", SEC3)
        j = r.json()
        check("RBAC on: the owner's admin agent (the MCP interview) records on atlas-system too",
              r.status_code == 201 and j.get("project") == "atlas-system" and j.get("supersedes") == sec2,
              r.text[:200])
        ev = last_event()
        check("...attributed to the agent, accountable to the owner",
              ev.actor_kind == "token" and ev.actor_user_id == "u-setup-owner-agent"
              and ev.responsible_user_id == OWNER, f"{ev.actor_kind} {ev.actor_user_id}")

        r = post_answer(owner, owner_csrf, "work_hierarchy", WH2)
        wh2 = r.json().get("adr_id")
        r2 = accept(owner, wh2, owner_csrf)
        check("RBAC on: the owner's session accepts a guide ADR on atlas-system with no binding",
              r.status_code == 201 and r2.status_code == 200 and r2.json().get("status") == "accepted",
              f"{r.status_code} {r2.status_code} {r2.text[:160]}")
        r = agent.put(f"/api/adrs/{wh2}", json={"project": "atlas-system", "title": "x",
                                                 "source": "atlas", "status": "proposed"})
        check("...while the generic PUT on it is still refused (role first, then guide_adr)",
              r.status_code == 403, r.text[:160])

        n_adr, n_ev, n_rows = count(AdrRecord), adr_events(), count(SetupAnswer)
        r = post_answer(owner, owner_csrf, "rbac_probe", {"freeze": "none"}, project="p-rbac-other",
                        prefix="PX")
        check("RBAC on: the same owner is refused on ANOTHER project through the guide "
              "(403 needs editor on p-rbac-other)", forbidden_on(r, "p-rbac-other"), r.text[:200])
        r = post_answer(agent, None, "rbac_probe", {"freeze": "none"}, project="p-rbac-other",
                        prefix="PX")
        check("...and so is the owner's admin agent", forbidden_on(r, "p-rbac-other"), r.text[:200])
        check("...and neither wrote an ADR, an event or a guide row",
              (count(AdrRecord), adr_events(), count(SetupAnswer)) == (n_adr, n_ev, n_rows))

        r = post_answer(member, member_csrf, "secrets", ANSWERS["secrets"])
        check("RBAC on: a member's session is refused by the guide (its owner rule, not RBAC)",
              r.status_code == 403 and r.json().get("error") == "forbidden"
              and "setup guide" in r.json().get("detail", ""), r.text[:160])
        r = post_answer(member_admin, None, "secrets", ANSWERS["secrets"])
        check("...and a member's admin token", r.status_code == 403, r.text[:160])
        r = member.put("/api/adrs/new", json={"project": "atlas-system", "title": "Member, direct",
                                              "csrf_token": member_csrf}, headers=H)
        check("...and a member on atlas-system through PUT /api/adrs/new",
              forbidden_on(r, "atlas-system"), r.text[:200])
        check("...none of it wrote anything",
              (count(AdrRecord), adr_events(), count(SetupAnswer)) == (n_adr, n_ev, n_rows))

        r = owner.put("/api/adrs/new", json={"project": "atlas-system", "title": "Direct, after the guide",
                                             "csrf_token": owner_csrf}, headers=H)
        check("after every guide call, the generic route still refuses the owner: the exemption "
              "does not outlive the guide's call", forbidden_on(r, "atlas-system"), r.text[:200])

        # The default instance-owner setting: the owner's session is owner everywhere, but the
        # owner's agent never inherits that (ADR 0034) and is capped, so it still needs the
        # exemption for the guide, and still has none on the generic route.
        rbac.use(settings.RbacSettings(mode="projects", shared_project_role="viewer"))
        r = agent.put("/api/adrs/new", json={"project": "atlas-system", "title": "Direct, no binding"})
        check("RBAC on, implicit instance owner (default): the owner's agent is still refused "
              "by PUT /api/adrs/new on atlas-system", forbidden_on(r, "atlas-system"), r.text[:200])
        r = post_answer(agent, None, "secrets", SEC2)
        check("...and records through the guide", r.status_code == 201
              and r.json().get("project") == "atlas-system", r.text[:200])
        check("no project role binding was created by any of it", bindings() == 0, str(bindings()))
    finally:
        rbac.use(prev_rbac)

    check("RBAC off again", not rbac.enabled())
    r = owner.put("/api/adrs/new", json={"project": "atlas-system", "title": "Direct, RBAC off",
                                         "csrf_token": owner_csrf}, headers=H)
    check("RBAC off is unchanged: PUT /api/adrs/new on atlas-system is a 201 with no binding",
          r.status_code == 201, r.text[:160])
    r = post_answer(owner, owner_csrf, "rbac_probe", {"freeze": "none"}, project="p-rbac-other",
                    prefix="PX")
    check("...and the guide records on another project with no binding (PX001), as before",
          r.status_code == 201 and r.json().get("adr_id") == "PX001"
          and r.json().get("project") == "p-rbac-other", r.text[:200])
os.environ.pop("ATLAS_SETUP_QUESTIONS_FILE", None)

# --------------------------------------------------------------------------- #
if failures:
    print(f"\n{len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nALL PASSED")
