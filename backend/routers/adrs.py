"""`adrs` routes: the ADR registry (ADR 0045). The model's header (`models_adr.py`) has the design.

    GET /api/adrs              list, as POINTERS: id, project, numbers, title, status, date, source
    GET /api/adrs/{ref}        one record, by its id (`AT001`) or by its legacy number
                               (`0041`, `ADR-0041`): 409 with the candidates when that
                               number is held in more than one project, never a guess
    PUT /api/adrs/{adr_id}     idempotent upsert; the one writer of git rows is tools/adr_sync.py
    PUT /api/adrs/new          create an Atlas-native record (source atlas, TASK-1354): the API
                               allocates the project's next id, Proposed unless told otherwise

TWO SOURCES (TASK-1354). A git row (`source` NULL, reported "git") indexes a file; an atlas row
is the record itself. They share the id space of their project, the visibility rule and search.
The write rules that keep them apart, each a 400 naming the field:
  * an atlas id is ALLOCATED, never chosen: `PUT /api/adrs/new`, never a new `PUT /api/adrs/AT017`
    with source atlas;
  * a git write to an id an atlas row holds is refused (`held_by_atlas`), so the sync can never
    overwrite or orphan one, even if its own plan were wrong;
  * an atlas write carries no legacy number, repo, path or commit, and is never orphaned.
Updating an atlas row (a human accepting it) is `PUT /api/adrs/{its id}` with source atlas.

ATOMIC ALLOCATION. `PUT /api/adrs/new` takes `SELECT ... FOR UPDATE` on the project's node row
and only then reads the project's highest sequence, so two concurrent creates in one project
serialise and cannot compute the same id. An explicit-id PUT takes the same lock, so a sync
write and an allocation in one project serialise too. The unique indexes stay as the last
line: a collision they catch is a 409, never a 500. The lock, the allocation and the atlas
insert are `adr_alloc.py`, shared with `atlas-admin guardrails apply`, so there is one
numbering rule; `verify_adr_records.py` fails on a second allocation site.

ACCESS. An ADR is governed by its PROJECT's visibility, and the filter is applied INSIDE every
query (`project_node_id IN visible`) before anything is counted, the same rule as
`/api/workitems`. A hidden project's ADR 404s exactly as an id that never existed, is absent
from a 409's candidate list, and does not move a `total`. Every read also INNER JOINs `node`,
so an ADR whose project node is gone is unreachable for everyone, owner included. Source does
not enter into any of this.
"""
from __future__ import annotations

import re
from datetime import date as Date, datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import adr_alloc  # the ONE allocation + insert of an Atlas-native ADR (guardrails.py shares it)
import auth
from models_adr import (ADR_ID_PATTERN, ADR_SEARCH_SQL, ADR_SOURCE_ATLAS, ADR_SOURCES,
                        ADR_STATUSES, AdrRecord)
from models_graph import Node as GraphNode
from db import session
import audit_log  # STORY-oss-16 / TASK-1382: the read audit (off unless ATLAS_AUDIT_READS=1)
from deps import (
    WORKITEM_LIST_DEFAULT_PAGE_SIZE,
    WORKITEM_LIST_MAX_PAGE_SIZE,
    _guard_node_access,
    _require_project_role_unless_setup_guide,
    _visible_node_ids,
    record_graph_event,
)
import rbac

router = APIRouter()

_ID_RE = re.compile(ADR_ID_PATTERN)
#: `0041`, `41`, `ADR 0041`, `ADR-0041`, `adr0041`: the legacy shared number.
_LEGACY_RE = re.compile(r"^(?:ADR[\s_-]*)?(\d{1,5})$", re.IGNORECASE)
_PREFIX_RE = re.compile(r"^[A-Z]{2,4}$")
#: The path segment that asks the API to allocate an id (`PUT /api/adrs/new`). Lower case, so
#: it can never be an ADR id, which is upper case.
NEW_ADR = "new"


def parse_ref(ref: str) -> tuple[str, str | int] | None:
    """`("id", "AT001")`, `("legacy", 41)` or None. Pure; the search source uses it too."""
    r = (ref or "").strip()
    # Legacy FIRST: `ADR0041` would otherwise read as prefix `ADR`, sequence 0041.
    m = _LEGACY_RE.match(r)
    if m:
        return ("legacy", int(m.group(1)))
    if _ID_RE.match(r.upper()):
        return ("id", r.upper())
    return None


def legacy_label(n: int | None) -> str | None:
    return None if n is None else f"ADR {n:04d}"


def source_of(r: AdrRecord) -> str:
    """The record's source as the API reports it: NULL is git."""
    return r.source or "git"


def _visible_stmt(s: Session):
    """`select(AdrRecord)` joined to `node` and narrowed to the caller's visible projects."""
    stmt = select(AdrRecord).join(GraphNode, GraphNode.id == AdrRecord.project_node_id)
    visible = _visible_node_ids(s)
    if visible is not None:
        stmt = stmt.where(AdrRecord.project_node_id.in_(visible or {"\x00"}))
    return stmt


def _pointer(r: AdrRecord) -> dict:
    return {
        "id": r.adr_id, "project": r.project_node_id, "seq": r.seq,
        "legacy": legacy_label(r.legacy_number), "number": r.legacy_number,
        "title": r.title, "status": r.status,
        "date": r.date.isoformat() if r.date else None,
        "orphaned": r.orphaned_at is not None,
        "source": source_of(r),
    }


def _bad(field: str, error: str, detail: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "field": field, "detail": detail}, status_code=status)


@router.get("/api/adrs")
def list_adrs(request: Request, project: str | None = None, q: str | None = None,
              status: str | None = None, number: int | None = None, page: int = 1,
              page_size: int = WORKITEM_LIST_DEFAULT_PAGE_SIZE) -> JSONResponse:
    """Filter by project, status, legacy `number`, and `q` (an id, a legacy number, or words
    in the title and body). Ordered by project, then the project's own sequence."""
    page = max(1, page)
    page_size = max(1, min(page_size, WORKITEM_LIST_MAX_PAGE_SIZE))
    filters = {"project": project, "q": q, "status": status, "number": number}
    if status is not None and status.lower() not in ADR_STATUSES:
        return _bad("status", "unknown_status",
                    f"unknown ADR status {status!r} — legal: {', '.join(ADR_STATUSES)}.")
    with session() as s:
        stmt = _visible_stmt(s)
        if project:
            stmt = stmt.where(AdrRecord.project_node_id == project)
        if status:
            stmt = stmt.where(AdrRecord.status == status.lower())
        if number is not None:
            stmt = stmt.where(AdrRecord.legacy_number == number)
        if q and q.strip():
            ors = [text(f"({ADR_SEARCH_SQL}) @@ plainto_tsquery('english', :q)").bindparams(q=q)]
            ref = parse_ref(q)
            if ref and ref[0] == "id":
                ors.append(AdrRecord.adr_id == ref[1])
            elif ref:
                ors.append(AdrRecord.legacy_number == ref[1])
            stmt = stmt.where(or_(*ors))
        rows = list(s.scalars(stmt.order_by(AdrRecord.project_node_id, AdrRecord.seq)).all())
        total = len(rows)
        start = (page - 1) * page_size
        items = [_pointer(r) for r in rows[start:start + page_size]]
        # TASK-1382: an ADR is governed by its PROJECT node, so the project ids are what
        # `node_ids` records (the column holds node ids, and the doc route records its node,
        # not the path, the same way). Which ADR was read is a decision left to the owner.
        audit_log.record_read(request, "GET /api/adrs", (i["project"] for i in items))
        return JSONResponse({"total": total, "count": len(items), "page": page,
                             "page_size": page_size, "max_page_size": WORKITEM_LIST_MAX_PAGE_SIZE,
                             "filters": filters, "items": items})


def _link(by_id: dict[str, AdrRecord], ref: str) -> dict:
    r = by_id.get(ref)
    if r is None:
        return {"id": ref, "missing": True}
    return {"id": r.adr_id, "project": r.project_node_id, "title": r.title, "status": r.status}


@router.get("/api/adrs/{ref}")
def get_adr(ref: str, request: Request, project: str | None = None) -> JSONResponse:
    parsed = parse_ref(ref)
    if parsed is None:
        return _bad("ref", "bad_ref", f"{ref!r} is neither an ADR id (AT001) nor a legacy "
                                      f"number (0041, ADR-0041)")
    with session() as s:
        stmt = _visible_stmt(s)
        if project:
            stmt = stmt.where(AdrRecord.project_node_id == project)
        if parsed[0] == "id":
            stmt = stmt.where(AdrRecord.adr_id == parsed[1])
        else:
            stmt = stmt.where(AdrRecord.legacy_number == parsed[1])
        rows = list(s.scalars(stmt.order_by(AdrRecord.project_node_id)).all())
        if not rows:
            return JSONResponse({"error": f"unknown ADR {ref!r}"}, status_code=404)
        if len(rows) > 1:
            return JSONResponse({
                "error": "ambiguous", "field": "ref",
                "detail": f"{legacy_label(parsed[1])} is held by {len(rows)} projects; "
                          f"ask by id, or pass ?project=",
                "candidates": [_pointer(r) for r in rows]}, status_code=409)
        r = rows[0]
        # Every visible record, once, to resolve links both ways. Tens of rows.
        everyone = list(s.scalars(_visible_stmt(s)).all())
        by_id = {x.adr_id: x for x in everyone}

        def derived(attr: str) -> list[str]:
            return [x.adr_id for x in everyone if r.adr_id in (getattr(x, attr) or [])]

        superseded_by = list(dict.fromkeys((r.superseded_by or []) + derived("supersedes")))
        d = _pointer(r)
        d.update({
            "slug": r.slug, "status_raw": r.status_raw,
            "supersedes": [_link(by_id, x) for x in (r.supersedes or [])],
            "superseded_by": [_link(by_id, x) for x in superseded_by],
            "amends": [_link(by_id, x) for x in (r.amends or [])],
            "amended_by": [_link(by_id, x) for x in derived("amends")],
            "repo": r.repo, "path": r.path, "commit": r.commit,
            "synced_at": r.synced_at.isoformat() if r.synced_at else None,
            "orphaned_at": r.orphaned_at.isoformat() if r.orphaned_at else None,
            "content": r.content,
        })
        # TASK-1382: the project node of the record served (see `list_adrs`). A 404 and a
        # 409 serve no record and record nothing; linked ADRs are pointers, not content.
        audit_log.record_read(request, "GET /api/adrs/{ref}", (r.project_node_id,))
        return JSONResponse(d)


class AdrWrite(BaseModel):
    project: str
    title: str
    slug: str = ""
    legacy_number: int | None = None
    #: Omitted: "unknown" for a git row (as before), "proposed" for an atlas one.
    status: str | None = None
    status_raw: str | None = None
    date: str | None = None
    supersedes: list[str] = []
    superseded_by: list[str] = []
    amends: list[str] = []
    repo: str | None = None
    path: str | None = None
    commit: str | None = None
    content: str | None = None
    orphaned: bool = False
    #: "git" (the default, stored as NULL) or "atlas". `PUT /api/adrs/new` implies atlas.
    source: str | None = None
    #: Only for `PUT /api/adrs/new` in a project that has no ADRs yet: the prefix its ids take.
    prefix: str | None = None
    csrf_token: str | None = None


_FIELDS = ("title", "slug", "legacy_number", "status", "status_raw", "date", "supersedes",
           "superseded_by", "amends", "repo", "path", "commit", "content")


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:200]


def _via_generic_put(request: Request) -> bool:
    """True when this write arrived on `PUT /api/adrs/{adr_id}` itself, not through a caller."""
    return getattr(request.scope.get("route"), "path", None) == "/api/adrs/{adr_id}"


def _guide_made(s: Session, adr_id: str) -> bool:
    """Did the setup guide record this ADR? (`models_setup`; an empty table answers no.)"""
    from models_setup import SetupAnswer
    return s.scalar(select(SetupAnswer.id).where(SetupAnswer.adr_id == adr_id).limit(1)) is not None


@router.put("/api/adrs/{adr_id}")
def put_adr(adr_id: str, body: AdrWrite, request: Request) -> JSONResponse:
    """Upsert one ADR, or allocate one with `adr_id` = `new`. A write that changes nothing
    writes nothing, not even an event, so a nightly sync over unchanged files leaves no trace.
    Refusals are 400s naming the field."""
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    creating = adr_id == NEW_ADR
    source = (body.source or (ADR_SOURCE_ATLAS if creating else "git")).lower()
    if source not in ADR_SOURCES:
        return _bad("source", "unknown_source",
                    f"unknown ADR source {body.source!r} — legal: {', '.join(ADR_SOURCES)}.")
    atlas = source == ADR_SOURCE_ATLAS
    if creating and not atlas:
        return _bad("source", "git_id_not_allocated",
                    "a git record names its id (PUT /api/adrs/AT001); only an atlas record "
                    "is allocated by PUT /api/adrs/new")
    prefix = seq = None
    if not creating:
        m = _ID_RE.match(adr_id)
        if not m or m.group(1) == "ADR":  # `ADR0041` is the legacy alias, never an id
            return _bad("adr_id", "bad_adr_id", f"{adr_id!r} is not an ADR id: 2-4 capital "
                                                f"letters then at least three digits, e.g. AT001")
        prefix, seq = m.group(1), int(m.group(2))
    elif body.prefix is not None and (not _PREFIX_RE.match(body.prefix) or body.prefix == "ADR"):
        return _bad("prefix", "bad_prefix", f"{body.prefix!r} is not a prefix: 2-4 capital "
                                            f"letters, and not ADR")
    if atlas:
        # An atlas record is the record itself: it indexes no file and holds no legacy number.
        for f in ("legacy_number", "repo", "path", "commit"):
            if getattr(body, f) is not None:
                return _bad(f, "not_for_atlas_source",
                            f"an Atlas-native ADR has no {f}; it is not a git file")
        if body.orphaned:
            return _bad("orphaned", "not_for_atlas_source",
                        "an Atlas-native ADR has no file to lose, so it is never orphaned")
    status = (body.status or ("proposed" if atlas else "unknown")).lower()
    if status not in ADR_STATUSES:
        return _bad("status", "unknown_status",
                    f"unknown ADR status {body.status!r} — legal: {', '.join(ADR_STATUSES)}.")
    when = None
    if body.date:
        try:
            when = Date.fromisoformat(body.date)
        except ValueError:
            return _bad("date", "bad_date", f"{body.date!r} is not an ISO date (YYYY-MM-DD)")
    if not body.title.strip():
        return _bad("title", "missing_title", "an ADR needs a title")
    for f in ("supersedes", "superseded_by", "amends"):
        bad = [x for x in getattr(body, f) if not (_ID_RE.match(x) or re.match(r"^ADR-\d{4,5}$", x))]
        if bad:
            return _bad(f, "bad_link", f"links are ADR ids (AT001) or unresolved legacy refs "
                                       f"(ADR-0041); got {bad}")

    with session() as s:
        # The project must exist AND be visible: hidden and absent are one 404.
        if s.get(GraphNode, body.project) is None:
            return JSONResponse({"error": f"unknown node {body.project!r}"}, status_code=404)
        # The project-role check, with the setup guide's one named exemption (deps, owner decision
        # 2026-10-26): armed only by a guide route, only for atlas-system.
        denied = _guard_node_access(s, body.project) or _require_project_role_unless_setup_guide(
            s, body.project, rbac.EDITOR)
        if denied is not None:
            return denied
        adr_alloc.lock_project(s, body.project)

        own = s.scalar(select(AdrRecord.prefix).where(
            AdrRecord.project_node_id == body.project).limit(1))
        row = None
        if creating:
            if own is not None and body.prefix is not None and body.prefix != own:
                return _bad("prefix", "prefix_mismatch",
                            f"{body.project!r} already numbers its ADRs with prefix {own!r}")
            prefix = own or body.prefix
            if prefix is None:
                return _bad("prefix", "missing_prefix",
                            f"{body.project!r} has no ADRs yet; name the prefix its ids take")
            # Read AFTER the lock: every write in this project that committed before is seen.
            seq, adr_id = adr_alloc.allocate(s, body.project, prefix)
        other = s.scalar(select(AdrRecord.project_node_id).where(
            AdrRecord.prefix == prefix, AdrRecord.project_node_id != body.project).limit(1))
        if other is not None:
            return _bad("prefix" if creating else "adr_id", "prefix_taken",
                        f"prefix {prefix!r} already belongs to another project")
        if own is not None and own != prefix:
            return _bad("adr_id", "prefix_mismatch",
                        f"{body.project!r} already numbers its ADRs with prefix {own!r}")

        if not creating:
            row = s.scalar(select(AdrRecord).where(AdrRecord.adr_id == adr_id))
            if row is not None and row.project_node_id != body.project:
                return _bad("project", "project_mismatch",
                            f"{adr_id} belongs to {row.project_node_id!r}; an ADR does not move")
            if row is not None and row.source == ADR_SOURCE_ATLAS and not atlas:
                return _bad("adr_id", "held_by_atlas",
                            f"{adr_id} is recorded in Atlas, not synced from git; a git write "
                            f"never replaces it")
            if row is not None and row.source != ADR_SOURCE_ATLAS and atlas:
                return _bad("source", "source_mismatch",
                            f"{adr_id} is synced from git; edit its file, not the record")
            if row is None and atlas:
                return _bad("adr_id", "atlas_id_not_allocated",
                            f"an Atlas-native ADR's id is allocated: PUT /api/adrs/{NEW_ADR}")
            if row is not None and row.source == ADR_SOURCE_ATLAS and _via_generic_put(request) \
                    and _guide_made(s, adr_id):
                # STORY-atlas-setup-guide-adrs: a guide-made ADR is accepted by a person through
                # POST /api/setup/adrs/{id}/accept and replaced by answering again, never edited
                # here; that route calls this function itself, which is why the check is on the
                # route template. Every other ADR is untouched by this.
                return _bad("adr_id", "guide_adr",
                            f"{adr_id} was recorded by the setup guide: a person accepts it with "
                            f"POST /api/setup/adrs/{adr_id}/accept, and a changed answer "
                            f"supersedes it", status=403)
        if body.legacy_number is not None:
            holder = s.scalar(select(AdrRecord.adr_id).where(
                AdrRecord.project_node_id == body.project,
                AdrRecord.legacy_number == body.legacy_number,
                AdrRecord.adr_id != adr_id))
            if holder is not None:
                return _bad("legacy_number", "legacy_number_taken",
                            f"{legacy_label(body.legacy_number)} is already {holder} in this project")

        now = datetime.now(timezone.utc)
        if atlas and when is None:
            # An atlas record is dated when it is born, and an update that omits the date keeps it.
            when = row.date if row is not None else now.date()
        slug = body.slug or (_slugify(body.title) if atlas else body.slug)
        want = {"title": body.title.strip(), "slug": slug, "legacy_number": body.legacy_number,
                "status": status, "status_raw": body.status_raw, "date": when,
                "supersedes": body.supersedes, "superseded_by": body.superseded_by,
                "amends": body.amends, "repo": body.repo, "path": body.path,
                "commit": body.commit, "content": body.content}
        created = row is None
        if created and atlas:
            # Only `new` reaches here: an atlas write to an unheld id was refused above.
            # adr_alloc records this create's node.adr.write itself, in the shape below.
            row = adr_alloc.add_atlas_adr(
                s, adr_id=adr_id, project=body.project, prefix=prefix, seq=seq, now=now, **{
                    f: want[f] for f in ("title", "slug", "status", "status_raw", "date",
                                         "supersedes", "superseded_by", "amends", "content")})
            changed = list(want)
        elif created:
            row = AdrRecord(adr_id=adr_id, project_node_id=body.project, prefix=prefix, seq=seq,
                            synced_at=now, source=None, **want)  # a git row
            if body.orphaned:
                row.orphaned_at = now
            s.add(row)
            changed = list(want)
        else:
            changed = [f for f in _FIELDS if getattr(row, f) != want[f]]
            for f in changed:
                setattr(row, f, want[f])
            if body.orphaned != (row.orphaned_at is not None):
                row.orphaned_at = now if body.orphaned else None
                changed.append("orphaned")
            if changed:
                row.synced_at = now
        if changed and not (created and atlas):
            detail = {"adr_id": adr_id, "created": created,
                      "fields": sorted(changed) if not created else None,
                      "bytes": len(body.content or "")}
            if atlas:
                detail["source"] = ADR_SOURCE_ATLAS
            record_graph_event(s, "node.adr.write", entity_type="node", entity_id=body.project,
                               detail=detail)
        try:
            s.commit()
        except IntegrityError:
            # The lock makes this unreachable for writes through this route; a unique index is
            # still the last line, and its refusal is a conflict to retry, not a server error.
            s.rollback()
            return _bad("adr_id", "id_conflict",
                        f"{adr_id} was taken by a concurrent write; retry", status=409)
        out = {"id": adr_id, "project": body.project, "created": created,
               "changed": sorted(changed) if not created else [],
               "orphaned": body.orphaned}
        if atlas:
            out.update({"source": ADR_SOURCE_ATLAS, "status": status})
        return JSONResponse(out, status_code=201 if created else 200)
