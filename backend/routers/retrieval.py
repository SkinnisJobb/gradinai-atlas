"""`/api/retrieval/*` (ADR 0044 §4): the retrieval add-on's status and its forced reindex.

MOUNTED ONLY WHEN `ATLAS_RETRIEVAL=on` (main.py). With the add-on off these paths do not exist,
so the default route table (`routes.snapshot.txt`) and `auth.WRITE_SCOPE_ROUTES` are unchanged;
`main.py` adds the reindex route's scope in the same branch that mounts it.

BOTH ARE OWNER-ONLY, 403 like access administration (`deps._require_owner`): the status counts
are over the whole index, private nodes included, and a reindex costs the embedder minutes of
work. Reindex is also a write: `auth.guard_write` first (CSRF for a session, the `admin` scope
for a Bearer), then the owner check. It records no `graph_event`, because it changes no node
or edge, only derived rows.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import auth
from deps import _require_owner
from retrieval import service

router = APIRouter()


class ReindexBody(BaseModel):
    rebuild: bool = False
    csrf_token: Optional[str] = None


@router.get("/api/retrieval/status")
def retrieval_status() -> JSONResponse:
    refused = _require_owner()
    if refused is not None:
        return refused
    return JSONResponse(service.status())


@router.post("/api/retrieval/reindex")
def retrieval_reindex(body: ReindexBody, request: Request) -> JSONResponse:
    refused = auth.guard_write(request, body.csrf_token) or _require_owner()
    if refused is not None:
        return refused
    code, payload = service.reindex(body.rebuild)
    return JSONResponse(payload, status_code=code)
