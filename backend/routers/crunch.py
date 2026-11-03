"""`/api/crunch`: crunch week's controls and banner (STORY-atlas-crunch-mode).

crunch.py has the design (what the mode is, the config, what each act writes, expiry). These
four routes are the only way to read its controls or change it, and every one of them refuses
every token, whatever its scopes. There is no MCP tool for any of them.

    GET  /api/crunch          the owner's controls: state, history, the matrix's environments
    PUT  /api/crunch          activate, change or extend  {config, password, csrf_token}
    POST /api/crunch/end      end it now                  {password, csrf_token}
    GET  /api/crunch/banner   any signed-in browser session: {active, ends_at}, nothing else

The PUT's config is crunch.CONFIG_SCHEMA: no `starts_at` starts now by the server's clock (THE
CLOCK), and `duration_hours` may stand in for `ends_at`, exactly one of the two, the end then
computed by the server; with a window already set it extends that window from its current end
(THE LENGTH). Both headers are in crunch.py. The answer's state always carries `ends_at`.

REFUSALS, in order, each with a named error:

  * GET /api/crunch: a token (any scope, an owner's admin token included) 403
    `session_required`; a member's session 403 `forbidden`.
  * PUT / POST end: `auth.guard_write` first, as every write route (a cookie session needs
    Origin + the CSRF token, 403 `csrf_failed`; a token is scope-checked against `admin`, so a
    read or write token is 403 `forbidden` "missing required scope: admin", which is also what
    verify_object_graph §20b asserts for every mapped route); then any token that passed the
    scope check 403 `session_required`; a member 403 `forbidden`; for PUT a config that does not
    validate 400 `bad_config` naming the field (before the password, so a typo costs no
    password attempt); `atlas-system` missing 409 `system_node_missing`; no password 403
    `step_up_required`; a wrong one 403 `password_incorrect` (an `auth_event` `login_fail`
    `crunch:...`, counted by the login limiter, through `auth.reauthenticate`, the mechanism
    `PUT /api/config/{name}` uses); an end with nothing set 409 `not_active`; a concurrent write
    that won 409 `conflict`.
  * GET /api/crunch/banner: a token 403 `session_required`. Every signed-in person may see
    THAT crunch week is on and until when (the board's banner), and nothing else.

Every answer carries `Cache-Control: no-store`.

NOT A NODE READ. None of these reads a node row the caller names: the state is crunch.py's own
tables, the environments come from the guardrail matrix document on `atlas-system` (owner
only), and the events and ADRs are written on `atlas-system`. verify_node_access.py §F
classifies all four as reading no node data.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import auth
import crunch
import models_auth
from db import session

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store"}
_FORBIDDEN = {"error": "forbidden",
              "detail": "Crunch week is controlled by the instance owner only."}
_SESSION_REQUIRED = {"error": "session_required",
                     "detail": "Crunch week is read and changed by a person in a browser "
                               "session; no token may, whatever its scopes."}


def _refuse(status: int, error: str, detail: str, field: Optional[str] = None) -> JSONResponse:
    body = {"error": error, "detail": detail}
    if field:
        body["field"] = field
    return JSONResponse(body, status_code=status, headers=_NO_STORE)


def _session_principal():
    """(principal, refusal): the refusal for a non-session caller, else None."""
    p = auth.current_principal.get()
    if p is None or p.kind != "session":
        return p, JSONResponse(_SESSION_REQUIRED, status_code=403, headers=_NO_STORE)
    return p, None


def _matrix(s) -> Optional[dict]:
    """The guardrail matrix in force, or None (none applied, or its document unreadable)."""
    import guardrails
    try:
        return guardrails.stored_matrix(s)
    except (guardrails.GuardrailsError, guardrails.MatrixError, ValueError):
        return None


def _body(s) -> dict:
    state = crunch.current(s)
    if state is None:
        s.expire_all()
        state = crunch.scheduled(s)
    m = _matrix(s)
    envs = [e["name"] for e in (m or {}).get("environments", [])]
    return {"active": bool(state and state.get("active")), "state": state,
            "history": crunch.history(s, 10), "envs": envs,
            "production_envs": [e["name"] for e in (m or {}).get("environments", [])
                                if e.get("production")],
            "last_backup": crunch.last_backup(s)}


@router.get("/api/crunch")
def crunch_get():
    """The owner's controls. Owner browser session only."""
    p, refused = _session_principal()
    if refused is not None:
        return refused
    if not p.is_owner:
        return JSONResponse(_FORBIDDEN, status_code=403, headers=_NO_STORE)
    with session() as s:
        return JSONResponse(_body(s), headers=_NO_STORE)


@router.get("/api/crunch/banner")
def crunch_banner():
    """Whether crunch week is on, and until when. Any signed-in browser session."""
    _p, refused = _session_principal()
    if refused is not None:
        return refused
    with session() as s:
        state = crunch.current(s)
    return JSONResponse({"active": state is not None,
                         "ends_at": state["ends_at"] if state else None}, headers=_NO_STORE)


class CrunchPut(BaseModel):
    #: The config (crunch.CONFIG_SCHEMA). Optional here so a missing one is our named 400,
    #: not FastAPI's 422.
    config: Any = None
    #: Step-up: the owner's own password, on every change.
    password: Optional[str] = None
    csrf_token: Optional[str] = None


class CrunchEnd(BaseModel):
    password: Optional[str] = None
    csrf_token: Optional[str] = None


def _write_guard(request: Request, csrf_token: Optional[str]):
    """(principal, refusal) for the two write routes: guard_write, session, owner."""
    refused = auth.guard_write(request, csrf_token)
    if refused is not None:
        return None, refused
    p, refused = _session_principal()
    if refused is not None:
        return None, refused
    if not p.is_owner:
        return None, JSONResponse(_FORBIDDEN, status_code=403, headers=_NO_STORE)
    return p, None


def _step_up(s, request: Request, principal, password: Optional[str]):
    """(person, refusal): the owner's row once their password checks out."""
    if not password:
        return None, _refuse(403, "step_up_required",
                             "Changing crunch week needs your password, every time.", "password")
    person = s.get(models_auth.AuthUser, principal.user_id)
    if person is None or person.disabled_at is not None or person.is_service:
        return None, JSONResponse(_FORBIDDEN, status_code=403, headers=_NO_STORE)
    if not auth.reauthenticate(s, person, password, ip=auth._client_ip(request),
                               user_agent=auth._user_agent(request), reason="crunch"):
        s.commit()   # the failure event and the throttle row are the point
        return None, _refuse(403, "password_incorrect",
                             "That password did not match. Crunch week is not changed from a "
                             "session alone.", "password")
    return person, None


@router.put("/api/crunch")
def crunch_put(body: CrunchPut, request: Request):
    """Activate, change or extend crunch week. Owner session + password only."""
    principal, refused = _write_guard(request, body.csrf_token)
    if refused is not None:
        return refused
    with session() as s:
        try:
            # The server's clock and the set window's start (crunch.py THE CLOCK).
            cfg = crunch.validate_config(body.config, matrix=_matrix(s),
                                         current_start=crunch.window_start(s))
        except crunch.CrunchConfigError as exc:
            return _refuse(400, "bad_config", str(exc), exc.field)
        try:
            crunch._require_system_node(s)
        except crunch.CrunchRefused as exc:
            return _refuse(409, exc.code, str(exc))
        person, refused = _step_up(s, request, principal, body.password)
        if refused is not None:
            return refused
        who = (person.id, person.subject)
        s.rollback()   # end the read transaction; put() runs its own
        try:
            act = crunch.put(s, cfg, user_id=who[0], subject=who[1])
        except crunch.CrunchConfigError as exc:   # re-checked against the row put() read
            return _refuse(400, "bad_config", str(exc), exc.field)
        except crunch.CrunchRefused as exc:
            return _refuse(409, exc.code, str(exc))
        except crunch.Conflict as exc:
            return _refuse(409, "conflict", str(exc))
        s.expire_all()
        return JSONResponse({**_body(s), "action": act, "changed": act is not None},
                            headers=_NO_STORE)


@router.post("/api/crunch/end")
def crunch_end(body: CrunchEnd, request: Request):
    """End crunch week now. Owner session + password only."""
    principal, refused = _write_guard(request, body.csrf_token)
    if refused is not None:
        return refused
    with session() as s:
        if crunch.current(s) is None:
            s.expire_all()
            if crunch.scheduled(s) is None:
                return _refuse(409, "not_active", "Crunch week is not on.")
        try:
            crunch._require_system_node(s)
        except crunch.CrunchRefused as exc:
            return _refuse(409, exc.code, str(exc))
        person, refused = _step_up(s, request, principal, body.password)
        if refused is not None:
            return refused
        who = (person.id, person.subject)
        s.rollback()
        try:
            crunch.end(s, user_id=who[0], subject=who[1])
        except crunch.NotActive:
            return _refuse(409, "not_active", "Crunch week is not on.")
        except crunch.CrunchRefused as exc:
            return _refuse(409, exc.code, str(exc))
        except crunch.Conflict as exc:
            return _refuse(409, "conflict", str(exc))
        s.expire_all()
        return JSONResponse({**_body(s), "action": "end", "changed": True}, headers=_NO_STORE)
