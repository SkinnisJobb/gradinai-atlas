"""`GET /api/audit/export` (STORY-oss-16-audit-export): the audit trail for a SIEM.

Streams JSON Lines (`application/x-ndjson`), one object per line, oldest first, from
`graph_event`, `auth_event` and `audit_read`. The line shapes, the ordering and the
paging are `audit_log.export_lines`'s; this module is the access rule and the
parameters.

WHO MAY CALL IT. Two callers, and nobody else gets anything but a 403:

  * an OWNER's browser session (`Principal.is_owner`), the same rule as
    `GET /api/auth-events` and `GET /api/access`;
  * a service token that holds the `admin` scope AND is linked to a person who is a
    live owner. This is the SIEM's credential: a collector cannot hold a browser
    session. It is NOT role inheritance in general (ADR 0034: role never inherits,
    and `Principal.is_owner` stays false for every agent). It is one route's explicit
    rule, requiring two independent facts: a scope only an owner may self-mint
    (`models_auth.SELF_MINTABLE_SCOPES`) and an accountable owner standing behind
    the agent, re-read from `auth_user` on every call so a demoted or disabled person
    loses the export at once.

403, not 404: as `deps._require_owner` argues, there is nothing to hide about the
existence of an admin surface.

WHAT IT NEVER CONTAINS: content read, search strings, passwords or their hashes,
token values or digests, session ids or their digests. The line functions name every
key they emit; nothing is serialised from a row wholesale.

A READ, BUT NOT A NODE READ: it is not in `audit_log.AUDITED_READ_ROUTES` (an export
of the audit log is not an agent reading the graph, and logging it into the table it
exports would grow the log by reading it). It is a GET, so it has no entry in
`auth.WRITE_SCOPE_ROUTES`; the admin requirement is enforced here, like every other
owner-only read.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

import auth
import audit_log
import models_auth
from db import session
from routers.events import _event_time_bound

router = APIRouter()

_FORBIDDEN = {"error": "forbidden",
              "detail": "The audit export is limited to owner accounts and to admin-scoped "
                        "agent tokens of an owner."}


def _may_export() -> bool:
    principal = auth.current_principal.get()
    if principal is None:
        return False
    if principal.kind == "session":
        return principal.is_owner
    if principal.kind != "token" or not principal.has_scope("admin"):
        return False
    if not principal.person_user_id:
        return False
    with session() as s:
        person = s.get(models_auth.AuthUser, principal.person_user_id)
        return (person is not None and not person.is_service and person.disabled_at is None
                and models_auth.resolve_role(person.role) == models_auth.ROLE_OWNER)


@router.get("/api/audit/export")
def audit_export(since: str | None = None, until: str | None = None,
                 kinds: str | None = None):
    """The audit trail as JSON Lines, oldest first. Owner-only (see the module header).

    since/until — ISO-8601, inclusive lower bound and exclusive upper (naive = UTC),
                  the same parsing and the same 400 as `GET /api/events`.
    kinds       — comma-separated subset of graph_event,auth_event,audit_read;
                  default all three. An unknown kind is a 400 naming the legal ones.
    """
    if not _may_export():
        return JSONResponse(_FORBIDDEN, status_code=403)

    since_ts, refused = _event_time_bound(since, "since")
    if refused is not None:
        return refused
    until_ts, refused = _event_time_bound(until, "until")
    if refused is not None:
        return refused

    wanted = list(audit_log.EXPORT_KINDS)
    if kinds is not None:
        wanted = [k.strip() for k in kinds.split(",") if k.strip()]
        unknown = sorted(set(wanted) - set(audit_log.EXPORT_KINDS))
        if unknown or not wanted:
            return JSONResponse(
                {"error": "unknown_kind", "field": "kinds",
                 "detail": f"unknown kind(s) {', '.join(unknown) or '(none given)'} — legal: "
                           f"{', '.join(audit_log.EXPORT_KINDS)}."},
                status_code=400)

    return StreamingResponse(audit_log.export_lines(wanted, since_ts, until_ts),
                             media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store"})
