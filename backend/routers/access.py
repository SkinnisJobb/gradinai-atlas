"""`access` routes, split out of `app.py` (STORY-oss-04); the code is moved, not changed.
Registered on the app by `main.py` via `include_router`.
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

import models_graph  # Atlas v2 object-graph schema (ADR 0019); coexists with Component below
import models_auth   # auth_* + node_visibility, own Bases (ADR 0025/0026, TASK-966/973)
import auth         # THE GATE (TASK-967) + service tokens (TASK-968)
import github_link  # GitHub OAuth identity + repo-access invitations (secrets are files)
import rbac         # project RBAC (ADR 0041): groups and role bindings, below
from models_graph import Edge as GraphEdge, Node as GraphNode
from db import session
from deps import (
    _visible_node_ids,
    _guard_node_access,
    _require_owner,
    _acting_user_id,
    _GH_OFF,
    CsrfOnly,
)

router = APIRouter()


class AccessWrite(BaseModel):
    """One node's access state. Both fields are optional and `exclude_unset`
    decides which are applied, so the GUI can save a visibility flip without
    resending the grant list, and vice versa.

    `grants` is the WHOLE list, not a delta: the settings grid holds every
    checkbox for a node on screen at once, so it always knows the full answer,
    and a replace has no ordering hazard between two admins editing at the same
    moment. An add/remove delta pair would merge two conflicting edits into a
    union — for an access control, that is the wrong direction to fail.
    """
    visibility: str | None = None
    grants: list[str] | None = None
    csrf_token: str | None = None


@router.get("/api/people")
def list_people(For: str | None = Query(None, alias="for")) -> JSONResponse:
    """The accounts a workitem can be assigned to (TASK-1119).

    `{id, username, name}` per row, and DELIBERATELY nothing else — no email,
    no role, no grants, no last-login. Widening this is a disclosure decision,
    not a convenience: it is readable by every signed-in account, which is the
    minimum the assignee picker needs (you cannot hand work to a person you
    cannot name) and is already more than any other route tells a member about
    who else exists.

    WHY NOT `/api/access`: that route is owner-only and 403s a member, and it
    returns the access matrix rather than a roster. The picker is used by
    members, so it needs its own, much narrower, surface.

    PEOPLE ONLY — `is_service = false`, active. Agents are excluded by decision
    (owner decision, 2026-08-17): under ADR 0034 an agent's work is its person's
    accountability, so the assignee is the human behind it. `_resolve_assignee`
    enforces the same rule on the write side; this endpoint is what stops the
    picker from offering the choice in the first place.

    `?for=<workitem-id>` narrows to accounts that can SEE that item, because
    offering to assign somebody work they cannot open is a handoff that
    silently goes nowhere — it would not appear in their Mine view either. The
    narrowing reuses `_visible_node_ids`, the same resolver everything else
    uses, rather than a second copy of the grant logic. An unknown id and one
    the CALLER cannot see return the same 404 the rest of the API gives: an
    empty list would confirm the item exists.
    """
    with session() as s:
        if For is not None:
            n = s.get(GraphNode, For)
            denied = _guard_node_access(s, For) if n is not None else None
            if n is None or denied is not None:
                return JSONResponse({"error": f"unknown node {For!r}"}, status_code=404)

        rows = s.scalars(
            select(models_auth.AuthUser)
            .where(models_auth.AuthUser.is_service.is_(False))
            .where(models_auth.AuthUser.disabled_at.is_(None))
            .order_by(models_auth.AuthUser.subject)
        ).all()

        if For is not None:
            # Who can see THIS item. `_visible_node_ids` answers for the
            # CURRENT principal, so it is asked once per candidate with that
            # candidate installed as the principal — the honest way to reuse
            # the resolver rather than reimplementing its ALL-over-parents,
            # fail-closed rule a second time and having the two drift.
            keep = []
            for u in rows:
                probe = auth.Principal(
                    kind="probe", user_id=u.id, subject=u.subject, role=u.role,
                    is_service=False, must_change_password=False,
                )
                tok = auth.current_principal.set(probe)
                try:
                    vis = _visible_node_ids(s)
                finally:
                    auth.current_principal.reset(tok)
                if vis is None or For in vis:
                    keep.append(u)
            rows = keep

        return JSONResponse({
            "people": [{"id": u.id, "username": u.subject, "name": u.name} for u in rows],
        })


@router.get("/api/access")
def get_access() -> JSONResponse:
    """The whole access matrix in one read — accounts down, nodes across.

    NODES RETURNED: every root (a node with no incoming `contains`), plus any
    node that already carries a visibility mark or a grant. Roots are the unit
    an owner actually administers, because visibility inherits down `contains`
    — marking a root private hides that whole project. The second half of the
    rule means a node marked private deeper in the tree can never become
    unreachable from the admin surface just for not being a root.

    ACCOUNTS RETURNED: every account, people and agents alike, each carrying
    `person_user_id` / `person` — the person an agent belongs to (ADR 0034).
    Agents are not filtered out and must not be: `admin bot add` gives each one
    its own `auth_user` row and grants resolve against THAT row, so an agent
    column is the only way an owner can let their own agent back into a project
    they have just marked private.
    """
    denied = _require_owner()
    if denied is not None:
        return denied

    with session() as s:
        users = s.scalars(
            select(models_auth.AuthUser).order_by(models_auth.AuthUser.subject)
        ).all()
        # WHO IS ACCOUNTABLE FOR AN AGENT (ADR 0034 / TASK-1112). `person_user_id`
        # is the stored fact; `person` is that account's username, resolved HERE
        # so the grid can group an agent under its operator without a second
        # request and without the frontend keeping its own id->username map.
        #
        # A link naming an account that no longer exists resolves to `person:
        # null` while `person_user_id` still reports the stored id. Falling back
        # to the raw id in the name field would put a uuid where the column
        # header says username, and it would read as one.
        name_of = {u.id: u.subject for u in users}
        gh_of = {g.user_id: g for g in s.scalars(select(models_auth.GithubLink)).all()}
        accounts = [
            {
                "github_login": gh_of[u.id].github_login if u.id in gh_of else None,
                # "not_linked" is the absence of a github_link row.
                "repo_access": gh_of[u.id].repo_access if u.id in gh_of else "not_linked",
                "user_id": u.id,
                "username": u.subject,
                "name": u.name,
                "role": models_auth.resolve_role(u.role),
                "is_service": bool(u.is_service),
                "disabled": u.disabled_at is not None,
                "person_user_id": u.person_user_id,
                "person": name_of.get(u.person_user_id) if u.person_user_id else None,
            }
            for u in users
        ]

        vis = {r.node_id: r.visibility
               for r in s.scalars(select(models_auth.NodeVisibility)).all()}
        grants: dict[str, list[str]] = {}
        for g in s.scalars(select(models_auth.NodeGrant)).all():
            grants.setdefault(g.node_id, []).append(g.user_id)

        has_parent = set(s.scalars(
            select(GraphEdge.dst).where(GraphEdge.edge_type == "contains")).all())
        # WORLD NODES ONLY. "A root" is derived as "no incoming `contains`", and
        # every EPIC satisfies that — the work hierarchy shares the edge type
        # with the world one until `decomposes_into` lands (TASK-1030). Without
        # this the grid offers a visibility switch per epic, under a column
        # headed "Project", which is both noise and a control nobody wants.
        # Access is administered over the world tree; work reaches it by
        # binding, and a workitem bound to a hidden node is already hidden with
        # it (proven in verify_node_access.py section C).
        fam_of = {nt.id: nt.family for nt in s.scalars(select(models_graph.NodeType)).all()}
        marked = set(vis) | set(grants)
        known: set[str] = set()
        nodes = []
        for n in s.scalars(select(GraphNode).order_by(GraphNode.id)).all():
            known.add(n.id)
            if n.id in marked:
                pass                       # an existing mark is always administrable
            elif fam_of.get(n.type) == "workitem":
                continue
            elif n.id in has_parent:
                continue
            nodes.append({
                "id": n.id,
                "name": n.name,
                "type": n.type,
                "is_root": n.id not in has_parent,
                "visibility": vis.get(n.id, models_auth.VISIBILITY_SHARED),
                "grants": sorted(grants.get(n.id, [])),
            })

        # Orphan marks — a visibility or grant row whose node is gone. REPORTED,
        # never cleaned up: the mark deliberately outlives the node it marks (see
        # NodeVisibility's comment), so deleting one here would undo the exact
        # property that makes a boot ingest safe. Surfacing them is the job the
        # never-built `admin visibility list` was meant to do.
        return JSONResponse({
            "accounts": accounts,
            "github": {"configured": github_link.configured(),
                       "repo": github_link.status()["repo"]},
            "nodes": nodes,
            "orphan_marks": sorted(marked - known),
            "visibility_values": list(models_auth.VISIBILITY_VALUES),
        })


@router.put("/api/access/node/{node_id}")
def put_access(node_id: str, body: AccessWrite, request: Request) -> JSONResponse:
    """Set one node's visibility and/or its complete grant list."""
    denied = _require_owner()
    if denied is not None:
        return denied
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused

    updates = body.model_dump(exclude_unset=True, exclude={"csrf_token"})
    setter = _acting_user_id()

    # `actor` is WHO DID IT, as a readable handle, matching every other
    # `auth._event` call site in this file. `_acting_user_id()` above is the
    # id that goes on the stored row; this is the name that goes in the log.
    _principal = auth.current_principal.get()
    actor = _principal.subject if _principal is not None else None
    ip, ua = auth._client_ip(request), auth._user_agent(request)

    with session() as s:
        if s.get(GraphNode, node_id) is None:
            return JSONResponse({"error": f"unknown node {node_id!r}"}, status_code=404)

        # Read the BEFORE state now, while it is still the before state. Both
        # branches below mutate in place, so a comparison taken afterwards
        # would compare a row with itself.
        _vis_before = s.get(models_auth.NodeVisibility, node_id)
        visibility_was = _vis_before.visibility if _vis_before else models_auth.VISIBILITY_SHARED
        grants_was = set(s.scalars(
            select(models_auth.NodeGrant.user_id).where(
                models_auth.NodeGrant.node_id == node_id)).all())

        if updates.get("visibility") is not None:
            wanted = str(updates["visibility"]).strip().lower()
            if wanted not in models_auth.VISIBILITY_VALUES:
                return JSONResponse(
                    {"error": "unknown_visibility",
                     "detail": "{!r} is not one of {}".format(
                         wanted, list(models_auth.VISIBILITY_VALUES)),
                     "field": "visibility"},
                    status_code=400,
                )
            row = s.get(models_auth.NodeVisibility, node_id)
            if wanted == models_auth.VISIBILITY_SHARED:
                # Shared is the ABSENCE of a mark, not a stored value. A row
                # saying "shared" would be a second representation of the same
                # state, and `_visible_node_ids`' fast path ("nothing is marked
                # private") would stop firing on a board where nothing is.
                if row is not None:
                    s.delete(row)
            elif row is None:
                s.add(models_auth.NodeVisibility(
                    node_id=node_id, visibility=wanted, set_by=setter))
            else:
                row.visibility = wanted
                row.set_by = setter

        if updates.get("grants") is not None:
            wanted_users = {str(u) for u in updates["grants"]}
            real_users = set(s.scalars(select(models_auth.AuthUser.id)).all())
            unknown = sorted(wanted_users - real_users)
            if unknown:
                return JSONResponse(
                    {"error": "unknown_account",
                     "detail": "unknown account id(s): {}".format(unknown),
                     "field": "grants"},
                    status_code=400,
                )
            existing = {g.user_id: g for g in s.scalars(
                select(models_auth.NodeGrant).where(
                    models_auth.NodeGrant.node_id == node_id)).all()}
            for uid in wanted_users - set(existing):
                s.add(models_auth.NodeGrant(
                    node_id=node_id, user_id=uid, granted_by=setter))
            for uid in set(existing) - wanted_users:
                s.delete(existing[uid])

        # WHO CHANGED WHO MAY SEE THIS — and why it is `auth_event` rather than
        # `graph_event`. `graph_event` has a read route, and that route serves an
        # event to every caller who can still see the node it names. An event
        # saying "avery was granted `property`" would therefore be readable by
        # every OTHER account granted `property` — a new disclosure, not a
        # re-serving of something those callers already had. `auth_event` has no
        # read route at all, which is the correct shape for an operator log.
        #
        # ONE EVENT PER AFFECTED ACCOUNT, not one per request carrying a list:
        # `user_id` is then the account whose access moved, so "everything that
        # ever changed for this person" is one indexed query on a column that
        # already exists. A list crammed into `detail` is a string nobody can
        # query.
        vis_now = s.get(models_auth.NodeVisibility, node_id)
        visibility_is = vis_now.visibility if vis_now else models_auth.VISIBILITY_SHARED
        if visibility_is != visibility_was:
            auth._event(s, "node_visibility_changed", actor=actor, ip=ip, user_agent=ua,
                        detail="node=%s from=%s to=%s" % (node_id, visibility_was, visibility_is))
        grants_now = set(s.scalars(
            select(models_auth.NodeGrant.user_id).where(
                models_auth.NodeGrant.node_id == node_id)).all())
        for uid in sorted(grants_now - grants_was):
            auth._event(s, "node_grant_added", user_id=uid, actor=actor, ip=ip,
                        user_agent=ua, detail="node=%s" % node_id)
        for uid in sorted(grants_was - grants_now):
            auth._event(s, "node_grant_removed", user_id=uid, actor=actor, ip=ip,
                        user_agent=ua, detail="node=%s" % node_id)

        # COMMIT, not flush — `session()` does not commit, and the only commit
        # otherwise reachable from a request path here is `recompute_derived`'s.
        # An access change alters no derived value (not health, not attention,
        # not a work-load count), so calling that to persist a row would be a
        # lie about what changed. Same choice, same reason, as the intent routes.
        #
        # The events above are added BEFORE this commit deliberately: this route
        # commits itself, and a row added afterwards is never written and never
        # raises.
        s.commit()

        vis_row = s.get(models_auth.NodeVisibility, node_id)
        current = sorted(s.scalars(
            select(models_auth.NodeGrant.user_id).where(
                models_auth.NodeGrant.node_id == node_id)).all())
        return JSONResponse({
            "id": node_id,
            "visibility": vis_row.visibility if vis_row else models_auth.VISIBILITY_SHARED,
            "grants": current,
        })


class GithubAccessWrite(BaseModel):
    repo_access: bool
    csrf_token: str | None = None


def _gh_target(s, user_id: str):
    """`(row, account, None)` or `(None, None, error response)`."""
    row = s.get(models_auth.GithubLink, user_id)
    acct = s.get(models_auth.AuthUser, user_id)
    if row is None or acct is None:
        return None, None, JSONResponse(
            {"error": "not_linked",
             "detail": "That account has not linked a GitHub account."}, status_code=404)
    return row, acct, None


def _gh_row_json(row) -> dict:
    return {"user_id": row.user_id, "github_login": row.github_login,
            "repo_access": row.repo_access}


@router.put("/api/access/github/{user_id}")
def put_github_access(user_id: str, body: GithubAccessWrite,
                      request: Request) -> JSONResponse:
    """Approve (invite) or revoke (remove / cancel) one account's repo access.
    OWNER-ONLY + session CSRF + `admin` scope - the same mechanism as
    `PUT /api/access/node/{id}`, so no token of any scope can reach it."""
    denied = _require_owner()
    if denied is not None:
        return denied
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    if not github_link.configured():
        return JSONResponse(_GH_OFF, status_code=404)

    principal = auth.current_principal.get()
    ip, ua = auth._client_ip(request), auth._user_agent(request)
    with session() as s:
        row, acct, err = _gh_target(s, user_id)
        if err is not None:
            return err
        try:
            if body.repo_access:
                if acct.disabled_at is not None or acct.is_service:
                    return JSONResponse(
                        {"error": "not_eligible",
                         "detail": "Only an active person account can be given repo access."},
                        status_code=409)
                got = github_link.invite(row.github_id, row.github_login)
                row.github_login = got["login"]
                row.repo_access = got["state"]
                row.invitation_id = got["invitation_id"]
                event = "github_repo_invited"
            else:
                login = github_link.revoke(row.github_id, row.github_login,
                                           row.invitation_id)
                row.github_login = login
                row.repo_access = models_auth.GITHUB_ACCESS_LINKED
                row.invitation_id = None
                event = "github_repo_revoked"
        except github_link.GithubError as exc:
            return JSONResponse(
                {"error": "github_failed",
                 "detail": "GitHub did not complete that (%s). Nothing was changed in Atlas."
                           % (("HTTP %d" % exc.status) if exc.status else "unreachable")},
                status_code=502)
        row.repo_access_by = principal.user_id
        row.repo_access_at = datetime.now(timezone.utc)
        auth._event(s, event, user_id=user_id, actor=principal.subject, ip=ip,
                    user_agent=ua, detail="github_id=%d login=%s state=%s" % (
                        row.github_id, row.github_login, row.repo_access))
        s.commit()
        return JSONResponse(_gh_row_json(row))


@router.post("/api/access/github/{user_id}/refresh")
def refresh_github_access(user_id: str, body: CsrfOnly, request: Request) -> JSONResponse:
    """Re-read from GitHub whether an invitation was accepted (or withdrawn).
    Owner-only, same mechanism as the PUT; it changes only Atlas's cached view."""
    denied = _require_owner()
    if denied is not None:
        return denied
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    if not github_link.configured():
        return JSONResponse(_GH_OFF, status_code=404)
    with session() as s:
        row, _acct, err = _gh_target(s, user_id)
        if err is not None:
            return err
        try:
            seen = github_link.observe(row.github_id, row.github_login)
        except github_link.GithubError as exc:
            return JSONResponse(
                {"error": "github_failed",
                 "detail": "GitHub did not answer (%s)." % (
                     ("HTTP %d" % exc.status) if exc.status else "unreachable")},
                status_code=502)
        row.github_login = seen["login"]
        row.repo_access = (models_auth.GITHUB_ACCESS_LINKED if seen["state"] == "none"
                           else seen["state"])
        row.invitation_id = seen["invitation_id"]
        s.commit()
        return JSONResponse(_gh_row_json(row))


# --------------------------------------------------------------------------- #
# Groups and project role bindings (ADR 0041 §3, STORY-oss-11-project-tenancy).
#
# ADMINISTRABLE WITH RBAC OFF, AND INERT THEN. The ADR's adoption path is "add
# the bindings, look at `atlas-admin rbac plan`, then flip the switch", so these
# routes work whatever `ATLAS_RBAC` says, and nothing reads what they write
# until it says `projects`.
#
# WHO. Groups are instance administration: owner-only, like the grant matrix
# above. A binding may also be changed by a project's own maintainer or owner
# once RBAC is on (§2: a maintainer grants up to maintainer, an owner grants
# owner). Every write here is a BROWSER SESSION act, never a token's, whatever
# its scopes — "no token ever exercises owner" (§4), and granting is the act
# that would let one leaked token widen itself. The `admin` entries in
# `auth.WRITE_SCOPE_ROUTES` are the same refusal on the scope axis.
#
# AUDIT. One `auth_event` per changed membership or binding, never
# `graph_event`, for `PUT /api/access/node`'s reason: `graph_event` is readable
# by everyone who can see the node, and who was given a project is not.
# --------------------------------------------------------------------------- #
class GroupCreate(BaseModel):
    name: str
    csrf_token: str | None = None


class GroupMembers(BaseModel):
    """The group's whole LOCAL membership. IdP memberships (OSS-13's sync) are
    left exactly as they are."""
    members: list[str]
    csrf_token: str | None = None


class BindingItem(BaseModel):
    principal_kind: str
    principal_id: str
    role: str


class ProjectBindings(BaseModel):
    """The project's whole binding list, replaced, for `AccessWrite.grants`'
    reason: two admins editing at once must not merge into a union."""
    bindings: list[BindingItem]
    csrf_token: str | None = None


_SESSION_ONLY = {"error": "forbidden",
                 "detail": "Groups and role bindings are changed by a person, from a browser "
                           "session. A token cannot change them, whatever its scopes."}


def _session_only() -> JSONResponse | None:
    principal = auth.current_principal.get()
    if principal is not None and principal.kind == "session":
        return None
    return JSONResponse(_SESSION_ONLY, status_code=403)


def _audit(request: Request):
    principal = auth.current_principal.get()
    actor = principal.subject if principal is not None else None
    ip, ua = auth._client_ip(request), auth._user_agent(request)

    def write(s, event: str, *, user_id: str | None = None, detail: str) -> None:
        auth._event(s, event, user_id=user_id, actor=actor, ip=ip, user_agent=ua, detail=detail)
    return write


def _groups_json(s) -> list[dict]:
    members: dict[str, list[dict]] = {}
    for m in s.scalars(select(models_auth.AuthGroupMember)).all():
        members.setdefault(m.group_id, []).append({"user_id": m.user_id, "source": m.source})
    return [{"id": g.id, "name": g.name, "source": g.source, "idp": g.idp,
             "external_id": g.external_id,
             "members": sorted(members.get(g.id, []), key=lambda r: (r["user_id"], r["source"]))}
            for g in s.scalars(select(models_auth.AuthGroup).order_by(models_auth.AuthGroup.name)).all()]


def _rbac_json() -> dict:
    cfg = rbac.SETTINGS
    return {"mode": cfg.mode, "enabled": cfg.enabled,
            "default_project_visibility": cfg.default_project_visibility,
            "shared_project_role": cfg.shared_project_role,
            "instance_admin_implicit_project_owner": cfg.instance_admin_implicit_project_owner}


@router.get("/api/access/groups")
def list_groups() -> JSONResponse:
    """Every group with its members (owner-only). Reads no node data."""
    denied = _require_owner()
    if denied is not None:
        return denied
    with session() as s:
        return JSONResponse({"groups": _groups_json(s), "rbac": _rbac_json()})


@router.post("/api/access/groups")
def create_group(body: GroupCreate, request: Request) -> JSONResponse:
    """Create a LOCAL group. 400 naming `name` if it is blank or too long, 409 if
    a group of that name exists."""
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    denied = _require_owner() or _session_only()
    if denied is not None:
        return denied
    name = (body.name or "").strip()
    if not name or len(name) > 128 or any(not ch.isprintable() for ch in name):
        return JSONResponse({"error": "invalid_name", "field": "name",
                             "detail": "A group name is 1-128 printable characters."},
                            status_code=400)
    audit = _audit(request)
    with session() as s:
        if s.scalar(select(models_auth.AuthGroup).where(models_auth.AuthGroup.name == name)):
            return JSONResponse({"error": "group_exists", "field": "name",
                                 "detail": "A group named %r already exists." % name},
                                status_code=409)
        g = models_auth.AuthGroup(id=str(_uuid.uuid4()), name=name,
                                  source=models_auth.GROUP_SOURCE_LOCAL,
                                  created_by=_acting_user_id())
        s.add(g)
        audit(s, "group_created", detail="group=%s name=%s" % (g.id, name))
        s.commit()
        return JSONResponse({"id": g.id, "name": g.name, "source": g.source, "members": []},
                            status_code=201)


@router.put("/api/access/groups/{group_id}/members")
def put_group_members(group_id: str, body: GroupMembers, request: Request) -> JSONResponse:
    """Replace a group's LOCAL members. People only: an agent takes its PERSON's
    groups (ADR 0041 §4), so a membership on the agent itself would be read by
    nothing — refused with a 400 rather than stored to mislead."""
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    denied = _require_owner() or _session_only()
    if denied is not None:
        return denied
    audit = _audit(request)
    with session() as s:
        g = s.get(models_auth.AuthGroup, group_id)
        if g is None:
            return JSONResponse({"error": f"unknown group {group_id!r}"}, status_code=404)
        wanted = {str(u) for u in body.members}
        accounts = {u.id: u for u in s.scalars(select(models_auth.AuthUser).where(
            models_auth.AuthUser.id.in_(wanted))).all()} if wanted else {}
        unknown = sorted(wanted - set(accounts))
        if unknown:
            return JSONResponse({"error": "unknown_account", "field": "members",
                                 "detail": "unknown account id(s): %s" % unknown},
                                status_code=400)
        agents = sorted(uid for uid, u in accounts.items() if u.is_service)
        if agents:
            return JSONResponse({"error": "agent_not_groupable", "field": "members",
                                 "detail": "agents take their person's groups (ADR 0041 §4); "
                                           "add the person instead: %s" % agents},
                                status_code=400)
        existing = {m.user_id: m for m in s.scalars(select(models_auth.AuthGroupMember).where(
            models_auth.AuthGroupMember.group_id == group_id,
            models_auth.AuthGroupMember.source == models_auth.GROUP_SOURCE_LOCAL)).all()}
        for uid in sorted(wanted - set(existing)):
            s.add(models_auth.AuthGroupMember(group_id=group_id, user_id=uid,
                                              source=models_auth.GROUP_SOURCE_LOCAL,
                                              added_by=_acting_user_id()))
            audit(s, "group_member_added", user_id=uid, detail="group=%s" % group_id)
        for uid in sorted(set(existing) - wanted):
            s.delete(existing[uid])
            audit(s, "group_member_removed", user_id=uid, detail="group=%s" % group_id)
        s.commit()
        return JSONResponse(next(x for x in _groups_json(s) if x["id"] == group_id))


@router.delete("/api/access/groups/{group_id}")
def delete_group(group_id: str, request: Request, body: CsrfOnly = CsrfOnly()) -> JSONResponse:
    """Delete a group, every membership in it and every binding naming it."""
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    denied = _require_owner() or _session_only()
    if denied is not None:
        return denied
    audit = _audit(request)
    with session() as s:
        g = s.get(models_auth.AuthGroup, group_id)
        if g is None:
            return JSONResponse({"error": f"unknown group {group_id!r}"}, status_code=404)
        B = models_auth.ProjectRoleBinding
        bindings = s.scalars(select(B).where(B.principal_kind == models_auth.PRINCIPAL_GROUP,
                                             B.principal_id == group_id)).all()
        for b in bindings:
            audit(s, "project_role_unbound",
                  detail="project=%s group=%s role=%s" % (b.project_id, group_id, b.role))
            s.delete(b)
        members = s.scalars(select(models_auth.AuthGroupMember).where(
            models_auth.AuthGroupMember.group_id == group_id)).all()
        for m in members:
            audit(s, "group_member_removed", user_id=m.user_id, detail="group=%s" % group_id)
            s.delete(m)
        audit(s, "group_deleted", detail="group=%s name=%s" % (group_id, g.name))
        s.delete(g)
        s.commit()
        return JSONResponse({"deleted": group_id, "members_removed": len(members),
                             "bindings_removed": len(bindings)})


def _bindings_of(s, project_id: str) -> list[dict]:
    B = models_auth.ProjectRoleBinding
    return [{"principal_kind": b.principal_kind, "principal_id": b.principal_id, "role": b.role}
            for b in s.scalars(select(B).where(B.project_id == project_id)
                               .order_by(B.principal_kind, B.principal_id)).all()]


def _is_instance_admin(principal) -> bool:
    """An instance owner in a browser session: the instance admin of ADR 0041 §8,
    who "may bind roles on any project". Never a token, whatever its account.

    ADMINISTERING A PROJECT IS NOT SEEING IT (TASK-1367). With
    `ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER=false` an instance owner holds no
    role on a project it has not bound itself to, so `_visible_node_ids` hides the
    project and everything in it, as it should. The two access routes below make
    exactly one exception for this caller: they list a project ROOT (id, name,
    type, visibility, bindings, never its prose or children) and accept a
    bindings PUT on it. Everything else, `/api/node/{id}` included, still goes
    through the visibility resolver, so reading the content still takes a role
    or a grant, and binding oneself to get one is audited (`project_role_bound`).
    With RBAC off, or with the implicit-owner default, the owner already sees
    every node and the exception changes nothing."""
    return principal is not None and principal.kind == "session" and principal.is_owner


def _administrable_role(s, principal, project_id: str) -> str | None:
    """The highest role `principal` may grant on `project_id`, or None.

    An instance owner (session) grants anything, RBAC on or off. Anyone else
    only with RBAC on and only as a project maintainer (up to maintainer) or
    project owner (up to owner) there (ADR 0041 §2). Never a token."""
    if principal is None or principal.kind != "session":
        return None
    if principal.is_owner:
        return rbac.OWNER
    if not rbac.enabled():
        return None
    role = rbac.role_on_project(s, principal, project_id)
    if rbac.rank(role) >= rbac.rank(rbac.OWNER):
        return rbac.OWNER
    if rbac.rank(role) >= rbac.rank(rbac.MAINTAINER):
        return rbac.MAINTAINER
    return None


@router.get("/api/access/projects")
def list_projects() -> JSONResponse:
    """The projects the caller may administer, each with its bindings, plus the
    people and groups a binding can name.

    An instance owner sees every project, including one whose content it cannot
    read (`_is_instance_admin`). With RBAC on, a project maintainer or
    owner sees the projects they hold that role on, and only those; the roster
    then is people only (the same disclosure as `GET /api/people`). Anyone else
    gets the owner-only 403. A project is what `rbac.project_map` says one is: a
    `project` node, or a root of another type; work-item roots are listed only
    when something is already bound to them."""
    principal = auth.current_principal.get()
    with session() as s:
        is_admin = principal is not None and principal.is_owner
        if not is_admin and not (rbac.enabled() and principal is not None
                                 and principal.kind == "session"):
            return JSONResponse(_NOT_OWNER_BODY, status_code=403)
        pmap = rbac.project_map(s)
        pids = rbac.project_ids(pmap)
        visible = None if _is_instance_admin(principal) else _visible_node_ids(s)
        if visible is not None:
            pids &= visible
        B = models_auth.ProjectRoleBinding
        bound = set(s.scalars(select(B.project_id)).all())
        fam_of = {nt.id: nt.family for nt in s.scalars(select(models_graph.NodeType)).all()}
        marks = {r.node_id: r.visibility for r in s.scalars(select(models_auth.NodeVisibility)).all()}
        nodes = {n.id: n for n in s.scalars(select(GraphNode).where(GraphNode.id.in_(pids))).all()} if pids else {}
        projects = []
        for pid in sorted(pids):
            n = nodes.get(pid)
            if n is None:
                continue
            if fam_of.get(n.type) == "workitem" and pid not in bound:
                continue
            grantable = _administrable_role(s, principal, pid)
            if grantable is None:
                continue
            projects.append({
                "id": pid, "name": n.name, "type": n.type,
                "visibility": "shared" if rbac.project_is_shared(pid, marks) else "private",
                "visibility_marked": pid in marks,
                "grantable_up_to": grantable,
                "bindings": _bindings_of(s, pid),
            })
        if not projects and not is_admin:
            return JSONResponse(_NOT_OWNER_BODY, status_code=403)
        users = s.scalars(select(models_auth.AuthUser).order_by(models_auth.AuthUser.subject)).all()
        accounts = [{"user_id": u.id, "username": u.subject, "name": u.name,
                     "is_service": bool(u.is_service), "disabled": u.disabled_at is not None,
                     "person_user_id": u.person_user_id}
                    for u in users if is_admin or (not u.is_service and u.disabled_at is None)]
        groups = [{"id": g["id"], "name": g["name"], "source": g["source"]} for g in _groups_json(s)]
        return JSONResponse({"rbac": _rbac_json(), "roles": list(rbac.PROJECT_ROLES),
                             "projects": projects, "accounts": accounts, "groups": groups})


_NOT_OWNER_BODY = {"error": "forbidden",
                   "detail": "Access administration is limited to owner accounts and, with "
                             "project RBAC on, to a project's maintainers and owners."}


@router.put("/api/access/project/{project_id}/bindings")
def put_project_bindings(project_id: str, body: ProjectBindings, request: Request) -> JSONResponse:
    """Replace the role bindings on one project. 400s name the field; a binding
    the caller may not grant, or may not remove, is a 403 naming the role."""
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    denied = _session_only()
    if denied is not None:
        return denied
    principal = auth.current_principal.get()
    audit = _audit(request)
    with session() as s:
        if s.get(GraphNode, project_id) is None:
            return JSONResponse({"error": f"unknown node {project_id!r}"}, status_code=404)
        is_project = project_id in rbac.project_ids(rbac.project_map(s))
        # The instance admin binds on any project ROOT it cannot see (ADR 0041
        # §8, `_is_instance_admin`); any other node, and any other caller, keeps
        # the visibility guard's 404.
        if not (is_project and _is_instance_admin(principal)):
            hidden = _guard_node_access(s, project_id)
            if hidden is not None:
                return hidden
        grantable = _administrable_role(s, principal, project_id)
        if grantable is None:
            return JSONResponse(_NOT_OWNER_BODY, status_code=403)
        if not is_project:
            return JSONResponse({"error": "not_a_project", "field": "project_id",
                                 "detail": "%r is not a project root (a `project` node, or a root "
                                           "of another type)." % project_id}, status_code=400)

        wanted: dict[tuple[str, str], str] = {}
        for i, b in enumerate(body.bindings):
            kind = (b.principal_kind or "").strip().lower()
            role = rbac.resolve_project_role((b.role or "").strip().lower())
            if kind not in models_auth.PRINCIPAL_KINDS:
                return JSONResponse({"error": "invalid_principal_kind",
                                     "field": "bindings[%d].principal_kind" % i,
                                     "detail": "%r is not one of %s" % (
                                         b.principal_kind, list(models_auth.PRINCIPAL_KINDS))},
                                    status_code=400)
            if role is None:
                return JSONResponse({"error": "invalid_role", "field": "bindings[%d].role" % i,
                                     "detail": "%r is not one of %s" % (b.role, list(rbac.PROJECT_ROLES))},
                                    status_code=400)
            key = (kind, b.principal_id)
            if key in wanted:
                return JSONResponse({"error": "duplicate_binding", "field": "bindings[%d]" % i,
                                     "detail": "%s %r is listed twice" % key}, status_code=400)
            exists = (s.get(models_auth.AuthUser, b.principal_id) if kind == models_auth.PRINCIPAL_USER
                      else s.get(models_auth.AuthGroup, b.principal_id))
            if exists is None:
                return JSONResponse({"error": "unknown_principal",
                                     "field": "bindings[%d].principal_id" % i,
                                     "detail": "unknown %s %r" % key}, status_code=400)
            wanted[key] = role

        B = models_auth.ProjectRoleBinding
        existing = {(b.principal_kind, b.principal_id): b
                    for b in s.scalars(select(B).where(B.project_id == project_id)).all()}
        # Every role this request adds, removes or replaces must be one the caller
        # may grant: a maintainer can neither create nor remove an owner.
        touched = [r for k, r in wanted.items() if existing.get(k) is None or existing[k].role != r]
        touched += [existing[k].role for k in existing
                    if k not in wanted or wanted[k] != existing[k].role]
        too_high = [r for r in touched if rbac.rank(r) > rbac.rank(grantable)]
        if too_high:
            return JSONResponse({"error": "forbidden", "needed_role": rbac.OWNER,
                                 "detail": "You may grant roles up to %r on %r; this change touches %r."
                                           % (grantable, project_id, sorted(set(too_high)))},
                                status_code=403)

        def who(kind: str, pid: str) -> dict:
            return {"user_id": pid} if kind == models_auth.PRINCIPAL_USER else {}

        for key, role in sorted(wanted.items()):
            row = existing.get(key)
            if row is None:
                s.add(B(project_id=project_id, principal_kind=key[0], principal_id=key[1],
                        role=role, granted_by=_acting_user_id()))
            elif row.role == role:
                continue
            else:
                row.role, row.granted_by = role, _acting_user_id()
            audit(s, "project_role_bound", **who(*key),
                  detail="project=%s %s=%s role=%s" % (project_id, key[0], key[1], role))
        for key, row in sorted(existing.items()):
            if key not in wanted:
                audit(s, "project_role_unbound", **who(*key),
                      detail="project=%s %s=%s role=%s" % (project_id, key[0], key[1], row.role))
                s.delete(row)
        s.commit()
        return JSONResponse({"id": project_id, "bindings": _bindings_of(s, project_id)})
