"""`setup` routes: the first-run setup guide's state API (STORY-atlas-setup-guide-adrs).

    GET  /api/setup                        where the guide stands: the question set's version,
                                           every topic (answered or not, its current ADR and
                                           that ADR's status), the next unanswered question,
                                           and the topic to ask next in full. `?topic=` asks
                                           for one topic in full instead.
    POST /api/setup/answers                answer ONE topic: {topic, answers, project?, prefix?}.
                                           Validated against the question set, then recorded as
                                           a Proposed Atlas-native ADR through the registry's own
                                           write (`routers.adrs.put_adr`, `PUT /api/adrs/new`).
                                           A changed answer makes a NEW ADR that supersedes the
                                           topic's previous one, which is left untouched; the
                                           same answer again writes nothing.
    POST /api/setup/adrs/{adr_id}/accept   a person accepts a guide-made ADR.

The question set, the answer keys and the rendering are `setup_guide.py`; the state is
`models_setup.SetupAnswer`. One API for every client: the MCP tool `atlas_setup_guide` (the
agent interview, the first client) and, later, a board wizard.

WHO MAY RUN THE GUIDE. The owner rule `GET /api/audit/export` and `GET /api/config` use,
reused rather than restated (`routers.audit._may_export`): an OWNER's browser session, or a
service token holding `admin` and linked to a live owner (an owner's agent, which is how the
MCP interview reaches it). Everyone else gets a 403. The answers describe how the whole
install is governed, which is the instance owner's to state. `auth.WRITE_SCOPE_ROUTES` maps
both POSTs to `admin`, and every POST calls `auth.guard_write` first (CSRF for a session, the
scope for a token), so a read-only token is refused on scope before anything else.

PROJECT RBAC. Under `ATLAS_RBAC=projects` the registry's write asks for `editor` on the target
project. For `atlas-system` only, and only for the caller admitted above, both POSTs skip that
check: `deps.setup_guide_rbac_exemption`, approved (owner decision, 2026-10-26), so that the owner (or
the owner's agent) need not bind themselves to the install's own node before the guide can
run. Any other project, and `PUT /api/adrs/*` called directly, keep the role check.

WHO MAY ACCEPT. A PERSON: an owner's browser session, never a token, whatever its scopes and
whoever it is linked to. The same session-only rule as approving an acceptance proposal
(STORY-atlas-acceptance-human-approved, `routers.workitems._session_only_refusal`): agents
propose, humans commit. The registry's generic `PUT /api/adrs/{id}` refuses to change a
guide-made ADR (`routers.adrs`), so this route is the one way one is accepted and a new
answer the one way one is replaced.

EVENTS. Each ADR the guide creates or accepts is written by the registry, which records its
own `node.adr.write` event on the project node, attributed to the caller (an agent's carries
the person it is linked to). The guide's state row carries the same actor.

WHERE THE ADRS GO. The project node `project` names, else the project the guide already
records into, else `atlas-system` (the install's own system node, seeded by `ATLAS_SEED=none`
and `demo`). The first ADR in a project with none fixes its prefix: `prefix`, else `SYS`.

AN INSTALL THAT NEVER RUNS THE GUIDE has an empty `setup_answer` table, no guide-made ADR, and
every other route answers as before: nothing outside these three routes reads the table (the
registry asks it one question, on a write to an atlas row through its generic PUT).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

import auth
import setup_guide
from db import session
from deps import _responsible_user_id, setup_guide_rbac_exemption
from models_adr import AdrRecord
from models_graph import Node as GraphNode
from models_setup import SetupAnswer
from routers.adrs import NEW_ADR, AdrWrite, put_adr
from routers.audit import _may_export as _owner_or_owners_admin_token

router = APIRouter()

DEFAULT_PROJECT = "atlas-system"
DEFAULT_PREFIX = "SYS"

_FORBIDDEN = {"error": "forbidden",
              "detail": "The setup guide is limited to owner accounts and to admin-scoped agent "
                        "tokens of an owner."}


def _bad(field: str, error: str, detail: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "field": field, "detail": detail}, status_code=status)


def _question_set():
    """(question set, None) or (None, the 503 naming the bad file and the place in it)."""
    try:
        return setup_guide.load_question_set(), None
    except setup_guide.QuestionSetError as e:
        return None, JSONResponse({"error": "bad_question_set", "detail": str(e)}, status_code=503)


def _current(s) -> dict[str, SetupAnswer]:
    """topic id -> its newest answer row."""
    out: dict[str, SetupAnswer] = {}
    for r in s.scalars(select(SetupAnswer).order_by(SetupAnswer.id)).all():
        out[r.topic_id] = r
    return out


def _latest_project(s) -> str | None:
    return s.scalar(select(SetupAnswer.project_node_id).order_by(SetupAnswer.id.desc()).limit(1))


def _topic_full(t: dict) -> dict:
    return {"id": t["id"], "title": t["title"], "summary": t["summary"], "source": t["source"],
            "questions": t["questions"]}


def _status(s, current: dict[str, SetupAnswer], qs: dict, ask: str | None) -> dict:
    ids = [r.adr_id for r in current.values()]
    statuses = dict(s.execute(
        select(AdrRecord.adr_id, AdrRecord.status)
        .join(GraphNode, GraphNode.id == AdrRecord.project_node_id)
        .where(AdrRecord.adr_id.in_(ids))).all()) if ids else {}
    topics, nxt = [], None
    for t in qs["topics"]:
        r = current.get(t["id"])
        topics.append({
            "id": t["id"], "title": t["title"], "source": t["source"],
            "questions": len(t["questions"]), "answered": r is not None,
            "adr_id": r.adr_id if r else None,
            "adr_status": statuses.get(r.adr_id) if r else None,
            "answered_at": r.answered_at.isoformat() if r else None,
            "answered_by": r.answered_by_subject if r else None,
            "answers": r.answers if r else None,
        })
        if r is None and nxt is None:
            nxt = {"topic": t["id"], "question": t["questions"][0]["id"]}
    listed = {t["id"] for t in qs["topics"]}
    project = _latest_project(s)
    if project is None and s.get(GraphNode, DEFAULT_PROJECT) is not None:
        project = DEFAULT_PROJECT
    ask_id = ask or (nxt["topic"] if nxt else None)
    asked = next((t for t in qs["topics"] if t["id"] == ask_id), None)
    return {
        "version": qs["version"], "shipped_version": qs["shipped_version"], "extra": qs["extra"],
        "project": project,
        "started": bool(current), "complete": nxt is None,
        "answered": sum(1 for t in topics if t["answered"]), "total": len(topics),
        "next": nxt,
        "ask": _topic_full(asked) if asked else None,
        "awaiting_acceptance": [t["adr_id"] for t in topics if t["adr_status"] == "proposed"],
        "topics": topics,
        # Answered under a question set that no longer lists them (an extra file removed).
        "unlisted_topics": sorted(set(current) - listed),
    }


@router.get("/api/setup")
def setup_get(topic: str | None = None) -> JSONResponse:
    """Where the guide stands, and what to ask next. Owner-only; reads, never writes."""
    if not _owner_or_owners_admin_token():
        return JSONResponse(_FORBIDDEN, status_code=403)
    qs, err = _question_set()
    if err is not None:
        return err
    if topic is not None and topic not in {t["id"] for t in qs["topics"]}:
        return _bad("topic", "unknown_topic", f"the question set has no topic {topic!r}")
    with session() as s:
        return JSONResponse(_status(s, _current(s), qs, topic), headers={"Cache-Control": "no-store"})


class SetupAnswerWrite(BaseModel):
    topic: str
    answers: dict[str, Any] = {}
    project: str | None = None
    prefix: str | None = None
    csrf_token: str | None = None


@router.post("/api/setup/answers")
def setup_answer(body: SetupAnswerWrite, request: Request) -> JSONResponse:
    """Record one topic's answers as a Proposed ADR (or supersede the topic's previous one)."""
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    if not _owner_or_owners_admin_token():
        return JSONResponse(_FORBIDDEN, status_code=403)
    qs, err = _question_set()
    if err is not None:
        return err
    topic = next((t for t in qs["topics"] if t["id"] == body.topic), None)
    if topic is None:
        return _bad("topic", "unknown_topic", f"the question set has no topic {body.topic!r}; "
                    f"its topics are {', '.join(t['id'] for t in qs['topics'])}")
    try:
        answers = setup_guide.normalise_answers(topic, body.answers)
    except setup_guide.AnswerError as e:
        return _bad(e.field, e.error, e.detail)

    with session() as s:
        prev = _current(s).get(topic["id"])
        if prev is not None and prev.answers == answers:
            # The same answer again changes nothing: no ADR, no event, no row.
            return JSONResponse({"topic": topic["id"], "unchanged": True, "adr_id": prev.adr_id,
                                 "project": prev.project_node_id,
                                 "next": _status(s, _current(s), qs, None)["next"]})
        if prev is not None and body.project and body.project != prev.project_node_id:
            return _bad("project", "project_mismatch",
                        f"topic {topic['id']!r} is recorded in {prev.project_node_id!r}; a changed "
                        f"answer supersedes its ADR there")
        project = (prev.project_node_id if prev is not None else None) or body.project \
            or _latest_project(s) or DEFAULT_PROJECT
        has_adrs = s.scalar(select(AdrRecord.id).where(
            AdrRecord.project_node_id == project).limit(1)) is not None
    prefix = body.prefix if body.prefix is not None else (None if has_adrs else DEFAULT_PREFIX)
    supersedes = prev.adr_id if prev is not None else None
    title, content = setup_guide.render_adr(topic, answers, version=qs["version"],
                                            supersedes=supersedes)
    # THE REGISTRY'S OWN WRITE, under the caller's own request: its visibility and project-role
    # checks, its atomic id allocation and its `node.adr.write` event are the ones every
    # Atlas-native ADR goes through. Its refusals (an unknown project, a taken prefix) are
    # returned to the caller as they are.
    # Under ATLAS_RBAC=projects, the one named exemption (deps.setup_guide_rbac_exemption,
    # owner decision, 2026-10-26): the caller admitted above records on atlas-system without a project
    # role binding. Any other project keeps the registry's role check.
    with setup_guide_rbac_exemption(project):
        made = put_adr(NEW_ADR, AdrWrite(
            project=project, title=title, content=content, source="atlas", status="proposed",
            supersedes=[supersedes] if supersedes else [], prefix=prefix,
            csrf_token=body.csrf_token), request)
    if made.status_code != 201:
        return made
    adr_id = json.loads(bytes(made.body))["id"]
    principal = auth.current_principal.get()
    with session() as s:
        s.add(SetupAnswer(
            topic_id=topic["id"], question_set_version=qs["version"], answers=answers,
            project_node_id=project, adr_id=adr_id, supersedes_adr_id=supersedes,
            answered_at=datetime.now(timezone.utc),
            answered_by_kind=principal.kind if principal else None,
            answered_by_user_id=principal.user_id if principal else None,
            answered_by_subject=principal.subject if principal else None,
            responsible_user_id=_responsible_user_id(principal)))
        s.commit()
        nxt = _status(s, _current(s), qs, None)["next"]
    return JSONResponse({"topic": topic["id"], "adr_id": adr_id, "status": "proposed",
                         "project": project, "supersedes": supersedes, "next": nxt},
                        status_code=201)


class SetupAccept(BaseModel):
    csrf_token: str | None = None


@router.post("/api/setup/adrs/{adr_id}/accept")
def setup_accept(adr_id: str, body: SetupAccept, request: Request) -> JSONResponse:
    """A person accepts a guide-made ADR: status proposed -> accepted, through the registry."""
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    principal = auth.current_principal.get()
    if principal is None or principal.kind != "session":
        return JSONResponse(
            {"error": "decision_needs_a_person",
             "detail": "Accepting a decision the setup guide recorded is a person's act, made "
                       "from a browser session. A token cannot make it, whatever its scopes.",
             "principal_kind": principal.kind if principal else None}, status_code=403)
    if not principal.is_owner:
        return JSONResponse(_FORBIDDEN, status_code=403)
    with session() as s:
        row = s.scalar(select(SetupAnswer).where(SetupAnswer.adr_id == adr_id))
        rec = s.scalar(select(AdrRecord).join(GraphNode, GraphNode.id == AdrRecord.project_node_id)
                       .where(AdrRecord.adr_id == adr_id))
        if row is None or rec is None:
            return JSONResponse({"error": "unknown_guide_adr",
                                 "detail": f"{adr_id!r} is not an ADR the setup guide recorded"},
                                status_code=404)
        topic_id = row.topic_id
        newest = _current(s).get(topic_id)
        if newest is not None and newest.adr_id != adr_id:
            return _bad("adr_id", "superseded",
                        f"{adr_id} was superseded by {newest.adr_id}, a later answer to "
                        f"{row.topic_id!r}; accept that one", status=409)
        if rec.status != "proposed":
            return _bad("adr_id", "not_proposed", f"{adr_id} is {rec.status}, not proposed",
                        status=409)
        today = datetime.now(timezone.utc).date().isoformat()
        write = AdrWrite(
            project=rec.project_node_id, title=rec.title, slug=rec.slug, status="accepted",
            status_raw=f"Accepted by {principal.subject}, {today}",
            supersedes=list(rec.supersedes or []), superseded_by=list(rec.superseded_by or []),
            amends=list(rec.amends or []), content=rec.content, source="atlas",
            csrf_token=body.csrf_token)
    # The same exemption as recording (above): an owner's session, atlas-system only.
    with setup_guide_rbac_exemption(write.project):
        done = put_adr(adr_id, write, request)
    if done.status_code != 200:
        return done
    return JSONResponse({"adr_id": adr_id, "status": "accepted", "accepted_by": principal.subject,
                         "topic": topic_id})
