"""
Project-scoped RBAC (ADR 0041 §1-§4, STORY-oss-11-project-tenancy).

OPT-IN. `ATLAS_RBAC=projects` switches it on; the default `off` is today. While
it is off NOTHING HERE RUNS: `deps._visible_node_ids` checks `enabled()` first
and takes today's path unchanged, and `require()` returns None before it reads
a row. `verify_rbac_off_unchanged.py` proves both by breaking this module's
readers on purpose and requiring today's answers anyway.

THE MODEL, in the order a request meets it:

  1. THE PROJECT is the authorization unit (§1). A node's project is the nearest
     `contains` ancestor of type `project`, the node itself included; a root of
     any other type (an `org`, an unbound epic) is its own project. A workitem
     follows what it WORKS ON (the effective binding, `attention.
     effective_bindings`), exactly as visibility already does. Several
     `contains` parents give several projects, and every check is ALL over
     them, the same rule `_visible_node_ids` applies to parents. A cycle, or a
     parent that does not resolve, gives NO project, and no project means no
     role: fail closed.

  2. FOUR ORDERED ROLES (§2): viewer < editor < maintainer < owner. An
     unrecognised stored role resolves to NO role (`resolve_project_role`).

  3. A PERSON'S ROLE on a project (§3) is the maximum of
       * their direct `project_role_binding` rows,
       * the bindings of every group they belong to (`auth_group_member`),
       * `editor`, if they hold a `node_grant` on the project ROOT (today's
         grant keeps meaning "may work in that project"),
       * `ATLAS_SHARED_PROJECT_ROLE` (default `editor`), if the project is
         shared. A project's visibility is its root's `node_visibility` mark,
         and with no mark `ATLAS_DEFAULT_PROJECT_VISIBILITY` (default `shared`).
     An instance `owner` is `owner` on every project while
     `ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER` is on (the default, §8).

  4. AN AGENT (§4, ADR 0034) inherits its PERSON's direct and group bindings and
     grants, plus its own direct bindings and grants, and NEVER the person's
     instance role: the role is read off `Principal.is_owner`, which is the
     bot's own (always member). Its token's scopes cap the role:
     read -> viewer, write -> editor, admin -> maintainer. No token exercises
     owner. Effective = min(cap, inherited role), per project, per request. An
     unlinked agent has its own bindings only, and no group membership at all.

  5. VISIBILITY with RBAC on = at least `viewer` on every project of the node,
     AND today's narrowing walk over the private marks that are NOT project
     roots (an inner private subtree still needs a grant, exactly as today).

  6. WRITES (`require`): `editor` to create or change, `maintainer` to delete
     (each removed row's project; both ends of an edge), `owner` to delete a
     project root. A node the caller cannot see is still the route's own 404,
     decided before this runs; this only ever answers 403, because the caller
     can already see the node and the code tells them nothing new.

  7. A NEW PROJECT ROOT IS OWNED BY THE PERSON WHO CREATED IT (ADR 0051,
     TASK-1468). Creating a node that is a project root the moment it exists
     binds the creating PERSON `owner` on it, in the same transaction: a person
     is bound themself, an agent its linked person (`person_user_id`, ADR 0034),
     never the agent. An unlinked agent has no person to bind and is refused
     (`root_creator`). Without this, the work install (private by default, no
     implicit instance owner) answered 201 and then 404 on the next read: the
     root was its own private project and nobody held a role on it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

import models_auth
import settings
from models_graph import Edge as GraphEdge, Node as GraphNode

#: Read once, at import: a malformed value stops the app booting (deps imports
#: this module), naming the variable. Tests replace it with `use()`.
SETTINGS: settings.RbacSettings = settings.load_rbac()

VIEWER, EDITOR, MAINTAINER, OWNER = "viewer", "editor", "maintainer", "owner"
PROJECT_ROLES = (VIEWER, EDITOR, MAINTAINER, OWNER)
_RANK = {r: i + 1 for i, r in enumerate(PROJECT_ROLES)}

#: §4: what each token scope lets an agent exercise. `admin` is the most a token
#: ever reaches; `owner` is a human act.
SCOPE_CAP = {"read": VIEWER, "write": EDITOR, "admin": MAINTAINER}

PROJECT_NODE_TYPE = "project"


def enabled() -> bool:
    return SETTINGS.enabled


def use(cfg: settings.RbacSettings) -> settings.RbacSettings:
    """Swap the settings in process (verify scripts). Returns the previous ones."""
    global SETTINGS
    old, SETTINGS = SETTINGS, cfg
    return old


def resolve_project_role(value: Optional[str]) -> Optional[str]:
    """Fail closed: anything that is not exactly a known role is NO role."""
    return value if value in _RANK else None


def rank(role: Optional[str]) -> int:
    return _RANK.get(role or "", 0)


def _max(*roles: Optional[str]) -> Optional[str]:
    best = None
    for r in roles:
        if rank(r) > rank(best):
            best = r
    return best


def _min(a: Optional[str], b: Optional[str]) -> Optional[str]:
    return a if rank(a) <= rank(b) else b


# --------------------------------------------------------------------------- #
# 1. Which project each node belongs to
# --------------------------------------------------------------------------- #
def project_map(s: Session) -> dict[str, Optional[frozenset]]:
    """`{node_id: frozenset(project ids)}`, or None for a node whose project
    cannot be resolved (a `contains` cycle, a parent that is not a node). Built
    from a raw edge scan, for `_visible_node_ids`' reason."""
    from attention import effective_bindings

    type_of = dict(s.execute(select(GraphNode.id, GraphNode.type)).all())
    parents_of: dict[str, list[str]] = {}
    for src, dst in s.execute(
        select(GraphEdge.src, GraphEdge.dst).where(GraphEdge.edge_type == "contains")
    ).all():
        parents_of.setdefault(dst, []).append(src)
    binding_of = effective_bindings(s)

    memo: dict[str, Optional[frozenset]] = {}

    def walk(nid: str, path: frozenset) -> Optional[frozenset]:
        if nid in memo:
            return memo[nid]
        if nid in path or nid not in type_of:
            return None  # a cycle, or a parent that is not a node: fail closed
        bound = binding_of.get(nid)
        if bound is not None and bound != nid:
            out = walk(bound, path | {nid})
        elif type_of[nid] == PROJECT_NODE_TYPE:
            out = frozenset({nid})
        else:
            parents = parents_of.get(nid) or []
            if not parents:
                out = frozenset({nid})  # a root of another type is its own project
            else:
                acc: set[str] = set()
                for p in parents:
                    got = walk(p, path | {nid})
                    if got is None:
                        break
                    acc |= got
                else:
                    out = frozenset(acc)
                    memo[nid] = out
                    return out
                out = None
        # A resolved answer never touched `path`, so it holds for every path; a
        # None may be this path's cycle only, so it is kept for the top level.
        if out is not None or not path:
            memo[nid] = out
        return out

    return {nid: walk(nid, frozenset()) for nid in type_of}


def project_ids(pmap: dict[str, Optional[frozenset]]) -> set[str]:
    out: set[str] = set()
    for projs in pmap.values():
        if projs:
            out |= projs
    return out


def _visibility_marks(s: Session) -> dict[str, str]:
    return dict(s.execute(select(models_auth.NodeVisibility.node_id,
                                 models_auth.NodeVisibility.visibility)).all())


def project_is_shared(project_id: str, marks: dict[str, str]) -> bool:
    """A project's visibility is its ROOT's mark; unmarked means the configured
    default. Any value other than `shared` is private (fail closed, as the
    resolver treats an unknown mark)."""
    mark = marks.get(project_id)
    if mark is None:
        return SETTINGS.default_project_visibility == "shared"
    return mark == models_auth.VISIBILITY_SHARED


# --------------------------------------------------------------------------- #
# 2. What one principal holds
# --------------------------------------------------------------------------- #
@dataclass
class Reach:
    unrestricted: bool = False
    bound: dict = field(default_factory=dict)        # project_id -> role
    granted: set = field(default_factory=set)        # node_grant node ids
    shared_role: Optional[str] = None
    cap: Optional[str] = None                        # agents only
    capped: bool = False
    nobody: bool = False                             # no principal: nothing

    def role_on(self, project_id: str, marks: dict[str, str]) -> Optional[str]:
        if self.nobody:
            return None
        if self.unrestricted:
            return OWNER
        role = self.bound.get(project_id)
        if project_id in self.granted:
            role = _max(role, EDITOR)
        if project_is_shared(project_id, marks):
            role = _max(role, self.shared_role)
        if self.capped:
            role = _min(role, self.cap)
        return role


def reach_of(s: Session, principal) -> Reach:
    if principal is None:
        return Reach(nobody=True)
    agent = bool(principal.is_service)
    if (not agent and principal.is_owner
            and SETTINGS.instance_admin_implicit_project_owner):
        return Reach(unrestricted=True)

    holders = [uid for uid in (principal.user_id,
                               principal.person_user_id if agent else None) if uid]
    # Groups: a person's own; an agent's PERSON's, never the bot's own (§4: an
    # unlinked bot has its direct bindings only).
    group_holder = principal.person_user_id if agent else principal.user_id
    groups: list[str] = []
    if group_holder:
        groups = list(s.scalars(select(models_auth.AuthGroupMember.group_id).where(
            models_auth.AuthGroupMember.user_id == group_holder)).all())

    B = models_auth.ProjectRoleBinding
    conds = []
    if holders:
        conds.append((B.principal_kind == models_auth.PRINCIPAL_USER) & B.principal_id.in_(holders))
    if groups:
        conds.append((B.principal_kind == models_auth.PRINCIPAL_GROUP) & B.principal_id.in_(groups))
    bound: dict[str, str] = {}
    if conds:
        for pid, role in s.execute(select(B.project_id, B.role).where(or_(*conds))).all():
            bound[pid] = _max(bound.get(pid), resolve_project_role(role))
    granted: set[str] = set()
    if holders:
        granted = set(s.scalars(select(models_auth.NodeGrant.node_id).where(
            models_auth.NodeGrant.user_id.in_(holders))).all())

    cap = None
    if agent:
        # At least `viewer`: every live token READS today, whatever its scopes
        # (scopes gate writes only, `auth.WRITE_SCOPE_ROUTES`), and a token that
        # holds only a retired scope such as `ingest` must not lose that.
        cap = _max(VIEWER, *(SCOPE_CAP.get(sc) for sc in (principal.scopes or ())))
    return Reach(bound={k: v for k, v in bound.items() if v}, granted=granted,
                 shared_role=SETTINGS.shared_project_role, cap=cap, capped=agent)


# --------------------------------------------------------------------------- #
# 3. Visibility (the RBAC branch of deps._visible_node_ids)
# --------------------------------------------------------------------------- #
def visible_node_ids(s: Session, principal, walk_unblocked) -> Optional[set[str]]:
    """Ids the principal may see with RBAC on, or None for unrestricted.

    `walk_unblocked(s, blocked_ids)` is `deps._walk_unblocked`, today's
    ALL-over-parents containment walk, passed in rather than copied: the inner
    private-subtree narrowing is resolved by the same code as with RBAC off.
    """
    reach = reach_of(s, principal)
    if reach.nobody:
        return set()
    if reach.unrestricted:
        return None
    pmap = project_map(s)
    marks = _visibility_marks(s)
    projects = project_ids(pmap)

    # Inner private marks narrow, exactly as today; a mark on a project ROOT is
    # the project's visibility and is decided by the role instead.
    blocked = {nid for nid, v in marks.items()
               if v != models_auth.VISIBILITY_SHARED and nid not in projects} - reach.granted
    base = walk_unblocked(s, blocked) if blocked else set(pmap)

    ok: dict[str, bool] = {}

    def readable(pid: str) -> bool:
        if pid not in ok:
            ok[pid] = rank(reach.role_on(pid, marks)) >= rank(VIEWER)
        return ok[pid]

    return {nid for nid in base
            if pmap.get(nid) and all(readable(p) for p in pmap[nid])}


# --------------------------------------------------------------------------- #
# 4. Writes
# --------------------------------------------------------------------------- #
def require(s: Session, principal, node_ids: Iterable[Optional[str]], need: str,
            *, root_needs: Optional[str] = None) -> Optional[JSONResponse]:
    """None if `principal` holds at least `need` on the project of every node in
    `node_ids`, else the 403 to return. RBAC off: always None, reading nothing.

    `root_needs` raises the bar for a node that IS a project root (deleting a
    project root takes `owner`, §2)."""
    if not enabled():
        return None
    ids = [n for n in dict.fromkeys(node_ids) if n]
    if not ids:
        return None
    reach = reach_of(s, principal)
    if reach.unrestricted:
        return None
    pmap = project_map(s)
    marks = _visibility_marks(s)
    work_roots: set[str] = set()
    if root_needs:
        # A WORLD root is a project in the full sense (§2: deleting it takes
        # `owner`). A work item that is its own project only because it is
        # unbound (an epic, a loose task) is not: `need` is enough there.
        import models_graph
        fam = dict(s.execute(select(models_graph.NodeType.id, models_graph.NodeType.family)).all())
        types = dict(s.execute(select(GraphNode.id, GraphNode.type).where(GraphNode.id.in_(ids))).all())
        work_roots = {n for n, t in types.items() if fam.get(t) == "workitem"}
    for nid in ids:
        projs = pmap.get(nid)
        if not projs:
            return _forbidden(need, None, None)
        wanted = root_needs if (root_needs and nid in projs and nid not in work_roots) else need
        for pid in sorted(projs):
            have = reach.role_on(pid, marks)
            if rank(have) < rank(wanted):
                return _forbidden(wanted, pid, have)
    return None


def _forbidden(need: str, project_id: Optional[str], have: Optional[str]) -> JSONResponse:
    return JSONResponse({
        "error": "forbidden",
        "detail": ("This needs the project role %r on %r; you hold %s there."
                   % (need, project_id, repr(have) if have else "no role")
                   if project_id else "This needs a project role on a node with no project."),
        "needed_role": need,
        "project": project_id,
        "role": have,
    }, status_code=403)


# --------------------------------------------------------------------------- #
# 5. Who owns a new project root (header §7, ADR 0051)
# --------------------------------------------------------------------------- #
#: The `reason` on the 403 an unlinked agent gets for creating a project root.
REASON_UNLINKED_AGENT = "agent_not_linked_to_a_person"


def root_creator(principal, node_id: str, *,
                 work_item: bool = False) -> tuple[Optional[str], Optional[JSONResponse]]:
    """`(person_id, None)`: the person to bind `owner` on the project root
    `node_id` that `principal` is about to create. `(None, refusal)`: the 403 to
    return instead, BEFORE the row exists. RBAC off: `(None, None)`, reading
    nothing and deciding nothing, so the route is today's.

    `work_item` only changes the refusal's advice: a work item can be filed
    under a parent or a binding instead, and then it is no root. `POST
    /api/node` takes no parent, so for a node linking the agent is the only way.

    Pure over the principal, deliberately: the caller already knows the new node
    is a root (it has no `contains` parent and no binding), and the refusal must
    be decidable before the insert, so a refused workitem never burns an id.

    The PERSON, not the agent (ADR 0051). The agent's role is min(cap, its
    person's role) anyway (§4), so binding the person gives the agent exactly
    what its token allows, and the accountable person sees what their agent
    made. Binding the agent instead would hide the project from that person.
    """
    if not enabled():
        return None, None
    if principal is not None and principal.is_service:
        if principal.person_user_id:
            return principal.person_user_id, None
        return None, _no_creator(node_id, REASON_UNLINKED_AGENT,
                                 "An agent with no linked person cannot create a project root: "
                                 "the new project would have no owner. Link the agent to a person "
                                 "(atlas-admin bot link)"
                                 + (", or file it under a parent or binding." if work_item else "."))
    if principal is not None and principal.user_id:
        return principal.user_id, None
    # No account behind the request (the read-only grace principal, which a
    # write never reaches): fail closed rather than create an ownerless root.
    return None, _no_creator(node_id, "no_account",
                             "This request has no account to own the new project root.")


def _no_creator(node_id: str, reason: str, detail: str) -> JSONResponse:
    """`_forbidden`'s shape, plus `reason`: there is no role the caller could be
    granted to pass, so `role` is None and `needed_role` names the one the
    creator would have been given."""
    return JSONResponse({
        "error": "forbidden",
        "reason": reason,
        "detail": detail,
        "needed_role": OWNER,
        "project": node_id,
        "role": None,
    }, status_code=403)


def role_on_project(s: Session, principal, project_id: str) -> Optional[str]:
    """One principal's effective role on one project (the access routes and
    `atlas-admin rbac plan`). Ignores `enabled()`: it answers what the role
    WOULD be, which is what an operator asks before switching it on."""
    return reach_of(s, principal).role_on(project_id, _visibility_marks(s))
