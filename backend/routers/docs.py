"""`docs` routes, split out of `app.py` (STORY-oss-04); the code is moved, not changed.
Registered on the app by `main.py` via `include_router`.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

import models_graph  # Atlas v2 object-graph schema (ADR 0019); coexists with Component below
import auth         # THE GATE (TASK-967) + service tokens (TASK-968)
from models_graph import Node as GraphNode
from db import session
from deps import (record_graph_event, EdgeDelete, _may_see_node, _guard_node_access,
                  _require_project_role)
import rbac
import audit_log  # STORY-oss-16: the read audit (off unless ATLAS_AUDIT_READS=1)

router = APIRouter()


# --------------------------------------------------------------------------- #
# Node doc content (TASK-871; content store moved to Postgres by ADR 0029,
# amending ADR 0020's "artifact-file directory convention" clause): serve the
# node document behind an allowlisted `attributes.docs[].path` so the board
# can render it (incl. Mermaid diagrams) in-panel instead of just showing the
# path as text.
#
# Security boundary — UNCHANGED, and still the FIRST check: `path` must be an
# EXACT match against one of THIS node's own `docs[].path` entries — never an
# arbitrary read of another node's row. That is the property ADR 0029
# requires any replacement to inherit, and it does: a node may still only
# read its own documents.
#
# WHAT'S GONE, AND WHY IT'S GONE RATHER THAN JUST UNUSED. `DOCS_ROOT` /
# `ATLAS_DOCS_DIR` and the `..`-normalization guard existed to defend a
# filesystem `open()` against a path that escaped `DOCS_ROOT`. There is no
# `open()` any more — content is a `(node_id, path)` lookup into `node_doc` —
# so there is no path traversal into a table for that guard to defend
# against. Leaving the dead defence in "just in case" would misstate what the
# code actually does; ADR 0029 says to name that in the comment instead, so:
# named here, deleted below.
#
# `external: true` (ADR 0029's marking for the 5 `docs[]` entries that point
# outside `docs/nodes/` — 4 correctly, at artifacts this ADR keeps in git; one,
# the `README.md` entry, an anomaly ADR 0029 also resolves by marking it) gets its
# own response, DISTINGUISHABLE from "not found" — that is the whole point of
# marking them, so the board can render "declared external, not served"
# instead of a 404 that looks identical to a broken link. A `docs[]` entry
# whose path is not rooted at `docs/nodes/` but ALSO isn't marked `external`
# is treated the same way (belt-and-braces — see ADR 0029's own "the second
# check becomes a plain namespacing convention on the key").


# --------------------------------------------------------------------------- #
DOCS_PREFIX = "docs/nodes/"


@router.get("/api/node/{node_id}/doc")
def node_doc(node_id: str, path: str, request: Request) -> JSONResponse:
    with session() as s:
        n = s.get(GraphNode, node_id)
        # ACCESS FILTER (STORY-atlas-access-admin). A node's documentation is
        # its most descriptive content, so this route must refuse exactly what
        # `/api/node/{id}` refuses, with the SAME 404 message — a doc route
        # that answers differently is a working oracle for whether a guessed
        # node id exists.
        if n is None or not _may_see_node(s, node_id):
            return JSONResponse({"error": f"unknown node {node_id!r}"}, status_code=404)

        docs = (n.attributes or {}).get("docs") or []
        entry = next((d for d in docs if isinstance(d, dict) and d.get("path") == path), None)
        if entry is None:
            # Do NOT look up content — reject before ever touching the table.
            return JSONResponse(
                {"error": f"{path!r} is not in node {node_id!r}'s docs[] allowlist"},
                status_code=404,
            )

        if entry.get("external") or not path.startswith(DOCS_PREFIX):
            # Declared external (or not rooted at docs/nodes/, which is the
            # same thing under the older, unmarked entries) — content lives
            # outside this repo/container and is deliberately not served.
            # Distinguishable from 404 so the board can render it as a real
            # external reference rather than a broken link.
            audit_log.record_read(request, "GET /api/node/{node_id}/doc", [node_id])
            return JSONResponse(
                {"path": path, "external": True, "detail": "declared external, not served"},
                status_code=200,
            )

        row = s.scalar(
            select(models_graph.NodeDoc).where(
                models_graph.NodeDoc.node_id == node_id, models_graph.NodeDoc.path == path
            )
        )
        if row is None:
            return JSONResponse({"error": f"doc content not found in database: {path!r}"}, status_code=404)

        # STORY-oss-16: one boolean when ATLAS_AUDIT_READS is off (the default).
        audit_log.record_read(request, "GET /api/node/{node_id}/doc", [node_id])
        return JSONResponse({"path": path, "content": row.content})


class NodeDocWrite(BaseModel):
    """TASK-1076 — the authoring half of ADR 0029.

    ADR 0029 moved node documentation into Postgres. Storage moved and the read
    route above moved; authoring did not. Until this route, the ONLY writer of a
    `node_doc` row was `import_node_docs.py`, a one-time importer that scans
    `docs/nodes/<id>/*.md`, refuses to run unless forced, and is deleted by
    TASK-1059 along with the directory it reads. Deleting that left nothing able
    to add a document or fix a typo in an existing one, while reading carried on
    working — a half-migration that only became visible when someone tried to
    write the 42nd document.
    """
    path: str
    content: str
    csrf_token: str | None = None


@router.put("/api/node/{node_id}/doc")
def put_node_doc(node_id: str, body: NodeDocWrite, request: Request) -> JSONResponse:
    """Upsert one document. Same guard as every other write route.

    STORING IS NOT SERVING, and this route deliberately does not conflate them.
    It does NOT require a `docs[]` allowlist entry: 11 rows on live are stored
    without one ("expected-not-served"), which is a deliberate state ADR 0029
    describes, not a defect. `GET` stays gated on the allowlist exactly as
    before, so publishing a stored document remains a separate, visible act
    (a PATCH on the node's attributes).

    The `docs/nodes/` prefix IS enforced, because the read route serves nothing
    else — content stored outside it could never be read back, and a write that
    silently produces unreadable content is worse than a refusal.
    """
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused

    if not body.path.startswith(DOCS_PREFIX):
        return JSONResponse(
            {"error": "path_outside_docs_root", "field": "path",
             "detail": f"a document path must start with {DOCS_PREFIX!r}; "
                       f"the read route serves nothing else, so {body.path!r} "
                       f"could never be read back"},
            status_code=400,
        )

    with session() as s:
        n = s.get(GraphNode, node_id)
        if n is None:
            return JSONResponse({"error": f"unknown node {node_id!r}"}, status_code=404)
        denied = _guard_node_access(s, node_id) or _require_project_role(s, [node_id], rbac.EDITOR)
        if denied is not None:
            return denied

        row = s.scalar(
            select(models_graph.NodeDoc).where(
                models_graph.NodeDoc.node_id == node_id,
                models_graph.NodeDoc.path == body.path,
            )
        )
        created = row is None
        if created:
            s.add(models_graph.NodeDoc(node_id=node_id, path=body.path, content=body.content))
        else:
            row.content = body.content

        # TASK-1045. `entity_id` is the OWNING NODE, not the path: a `node_doc`
        # row has no id of its own on any route, and this is what makes a
        # node's document history part of the one query that answers
        # "everything that happened to this node". `node_id` therefore defaults
        # to the same value, which is the column `GET /api/events` filters on —
        # so a document written on a private project is exactly as invisible as
        # the project. LENGTH, NOT CONTENT: the same rule `edit_node` follows
        # for `body`. This table says the document changed; prose history is a
        # separate, unbuilt thing.
        record_graph_event(s, "node.doc.write", entity_type="node", entity_id=node_id,
                           detail={"path": body.path, "created": created,
                                   "bytes": len(body.content)})

        # COMMIT, not just flush. Every other write route in this file happens
        # to end in recompute_derived(), which commits as its last act -- a
        # document write changes no derived value, so it has to say so itself.
        # The first version of this route flushed and returned 201 while the
        # row rolled back on session exit; section J caught it.
        s.commit()

        docs = (n.attributes or {}).get("docs") or []
        allowlisted = any(isinstance(d, dict) and d.get("path") == body.path for d in docs)
        return JSONResponse(
            {"node_id": node_id, "path": body.path, "bytes": len(body.content),
             "created": created,
             "served": allowlisted,
             "detail": None if allowlisted else
                       "stored, but not in this node's docs[] allowlist — GET will 404 "
                       "until an entry is added (expected-not-served)"},
            status_code=201 if created else 200,
        )


@router.delete("/api/node/{node_id}/doc")
def delete_node_doc(node_id: str, path: str, request: Request,
                    body: EdgeDelete = EdgeDelete()) -> JSONResponse:
    """Remove one document. Exists for the same reason the PUT does.

    A document that can be created but not removed is half an authoring path:
    the only way to retract one would be a hand-written SQL statement against
    live, which is the state TASK-1076 exists to end.

    Leaves the node's `docs[]` allowlist alone. The entry is a separate
    assertion ("this node has a document here") and removing content without
    being asked to touch the attribute would be this route quietly editing
    something the caller did not name.
    """
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused

    with session() as s:
        denied = _guard_node_access(s, node_id) or _require_project_role(s, [node_id], rbac.MAINTAINER)
        if denied is not None:
            return denied
        row = s.scalar(
            select(models_graph.NodeDoc).where(
                models_graph.NodeDoc.node_id == node_id,
                models_graph.NodeDoc.path == path,
            )
        )
        if row is None:
            return JSONResponse(
                {"error": f"no stored document for node {node_id!r} at {path!r}"},
                status_code=404,
            )
        # TASK-1045 — the `bytes` are read while the row is still readable,
        # same discipline as the edge snapshots in `delete_node`. A deletion
        # that leaves no trace of how much was deleted is the one a reader most
        # wants sized.
        gone = {"path": path, "bytes": len(row.content or "")}
        s.delete(row)
        record_graph_event(s, "node.doc.delete", entity_type="node", entity_id=node_id,
                           detail=gone)
        s.commit()   # same reason as the PUT above
        return JSONResponse({"deleted": {"node_id": node_id, "path": path}})
