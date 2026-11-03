"""
IdP group sync (ADR 0041 §3, STORY-oss-13) over OSS-11's `auth_group` / `auth_group_member`.

AT EVERY OIDC LOGIN the user's `source='idp'` memberships are REPLACED by what the
token's groups claim says, restricted to the allowlist in ATLAS_OIDC_GROUP_MAP_FILE.
`source='local'` memberships are never read or written here: `source` is part of
`auth_group_member`'s key, so one person can hold a local and an IdP membership of
the same group, and sync removing the IdP one leaves the local one standing.

THE ALLOWLIST is the only way an IdP group reaches Atlas. An Entra tenant has
thousands of groups; a value not in the map is ignored, never materialised. The
file is YAML (JSON is YAML):

    # IdP group value -> Atlas group name
    groups:
      "3f6c1c1e-6f4d-4a59-9a7e-0f1c2d3e4f50": team-alpha      # Entra: the group's object id
      "/atlas/maintainers": maintainers                       # Keycloak: the group path

A mapped Atlas group that does not exist yet is created with `source='idp'`,
`idp='oidc'` and the first IdP value mapped to it as `external_id`. A mapping onto
an EXISTING group (local or idp) adds IdP memberships to it. Bindings are not made
here: an operator binds the group to projects (`PUT /api/access/project/{id}/bindings`).

NO MAP FILE means an empty allowlist: nothing is materialised, and a user's IdP
memberships are emptied at their next login.

ENTRA GROUP OVERAGE. Past ~200 groups Entra omits the claim and sends
`_claim_names: {"groups": ...}` (or `hasgroups: true`) instead. Atlas cannot tell
which groups that hides, so the login is REFUSED (fail closed, `oidc.map_claims`).
The cure is in the app registration: emit only the groups assigned to the
application ("Groups assigned to the application"), which keeps the claim short.
"""
from __future__ import annotations

import uuid
from typing import Iterable, Mapping, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import settings
from models_auth import GROUP_SOURCE_IDP, AuthEvent, AuthGroup, AuthGroupMember, AuthUser

#: `auth_group.name` is VARCHAR(128); `external_id` VARCHAR(256).
MAX_GROUP_NAME = settings.MAX_GROUP_NAME
MAX_GROUP_VALUE = settings.MAX_GROUP_VALUE


def load_group_map(path: Optional[str]) -> dict:
    """Read the allowlist file, or {} when none is configured. Raises SettingsError.

    The rule is `settings.load_group_map` (TASK-1433: the configuration catalogue applies it
    without importing this package, which needs the peppers)."""
    return settings.load_group_map(path)


def _event(s: Session, event: str, user_id: str, detail: str) -> None:
    s.add(AuthEvent(event=event, user_id=user_id, actor="oidc", reason="idp_sync",
                    detail=detail[:2000]))


def sync_idp_groups(s: Session, user: AuthUser, claim_values: Iterable[str],
                    group_map: Mapping[str, str], *, idp: str) -> tuple[list[str], list[str]]:
    """Replace `user`'s `source='idp'` memberships. Returns (added, removed) group names.

    Does not commit: it rides on the login's transaction, so a failed login
    changes no membership.
    """
    wanted: dict = {}                      # Atlas group name -> first IdP value naming it
    for value in claim_values:
        target = group_map.get(value)
        if target is not None:
            wanted.setdefault(target, value)

    groups = {g.name: g for g in s.scalars(
        select(AuthGroup).where(AuthGroup.name.in_(list(wanted)))).all()} if wanted else {}
    for name, value in wanted.items():
        if name not in groups:
            group = AuthGroup(id=str(uuid.uuid4()), name=name, source=GROUP_SOURCE_IDP,
                              idp=idp, external_id=value[:MAX_GROUP_VALUE], created_by=None)
            s.add(group)
            groups[name] = group
            s.add(AuthEvent(event="group_created", user_id=None, actor="oidc",
                            reason="idp_sync", detail="group=%s" % name))
    s.flush()
    wanted_ids = {groups[name].id for name in wanted}

    current = set(s.scalars(select(AuthGroupMember.group_id).where(
        AuthGroupMember.user_id == user.id,
        AuthGroupMember.source == GROUP_SOURCE_IDP)).all())
    names = {g.id: g.name for g in s.scalars(
        select(AuthGroup).where(AuthGroup.id.in_(list(current | wanted_ids)))).all()} \
        if (current | wanted_ids) else {}

    removed = sorted(current - wanted_ids)
    if removed:
        s.execute(delete(AuthGroupMember).where(
            AuthGroupMember.user_id == user.id,
            AuthGroupMember.source == GROUP_SOURCE_IDP,
            AuthGroupMember.group_id.in_(removed)))
    added = sorted(wanted_ids - current)
    for gid in added:
        s.add(AuthGroupMember(group_id=gid, user_id=user.id, source=GROUP_SOURCE_IDP,
                              added_by=None))
    for gid in added:
        _event(s, "group_member_added", user.id, "group=%s source=idp" % names.get(gid, gid))
    for gid in removed:
        _event(s, "group_member_removed", user.id, "group=%s source=idp" % names.get(gid, gid))
    return [names.get(g, g) for g in added], [names.get(g, g) for g in removed]
