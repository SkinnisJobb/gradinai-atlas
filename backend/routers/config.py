"""`/api/config` (STORY-atlas-config-panel): the install's configuration, and (phase 2, behind a
flag) the one route that changes a safe setting from the board.

=============================================================================================
PRECEDENCE: environment variable > board-set value > default
=============================================================================================

  1. ENVIRONMENT. A variable set (non-blank) in the API process's environment always wins. A
     board change to a setting the environment sets is REFUSED with 409 `environment_wins`,
     naming the variable, so the board never stores a value that silently does nothing. If the
     environment starts setting a name the board had set, the environment wins from the next
     boot on; the board's row stays and is unread until the variable is removed again.
  2. BOARD. A value an owner set through `PUT /api/config/{name}`, stored in `config_value`
     (models_config.py) and shown with source `board`. It takes effect AT ONCE and again at
     every boot: boot reads the table (no file is opened) and puts each value into the process
     environment where the real environment does not set it. Only settings whose reader
     re-reads the environment on every use are changeable, which is why "at once" is true
     (settings_catalog's header has the condition; each response says `effect: "now"`).
  3. DEFAULT. Neither sets it: the code's default. `PUT` with `"value": null` removes the
     board's value and returns the setting to its default.

=============================================================================================

THE FLAG. `ATLAS_CONFIG_WRITES=1` turns phase 2 on; unset (the default, and the homelab) it is
off, and off is phase 1 exactly:

  * the write route is NOT REGISTERED (main.py), and its `auth.WRITE_SCOPE_ROUTES` entry is not
    added, so the route table equals `routes.snapshot.txt`, `verify_object_graph`'s pinned
    write-scope table holds, and a `PUT /api/config/<name>` answers byte for byte what a PUT to
    a path that never existed answers (the gate's own 404). Not registering it, rather than
    registering it and answering 404, is the choice that keeps `verify_node_access.py` §F and
    the snapshot honest: neither has to carry a route that does not exist on the install.
    It is the retrieval add-on's pattern (main.py), copied on purpose.
  * `config_value` is never created or read, and `GET /api/config` is phase 1's body.

WHO MAY CHANGE A SETTING (flag on), refused in this order, each with a named error:

  * `auth.guard_write` first, as every write route: a cookie session needs Origin + the CSRF
    token; a token is scope-checked (`admin`, the belt).
  * an OWNER'S BROWSER SESSION ONLY. No token may change configuration, an owner's admin-scoped
    agent token included (403 `session_required`), the rule setup-ADR accept applies: an agent
    may read the configuration, a person changes it.
  * a member: 403 `forbidden`.
  * a setting that is unknown (404), bootstrap (403 `bootstrap_read_only`, in every mode,
    forever), a secret (403 `secret_not_changeable`) or not marked changeable (403
    `not_changeable`, with the reason), then one the environment sets (409), then a value that
    does not validate (400 naming `value`); settings_catalog.change_refusal decides the first four.
  * STEP-UP: the owner's own password in the request (`password`), checked by
    `auth.reauthenticate`, the mechanism the agent-credential routes already use (TASK-1115):
    a failure counts against the login limiter and is an `auth_event` `login_fail` with reason
    `config_write:...`. Missing is 403 `step_up_required`, wrong is 403 `password_incorrect`.
    Per request, not a "fresh login" window on the session: one prompt per change, no new
    session state.
  * STEP-UP FOR AN SSO OWNER (TASK-1436). An IdP-backed owner has no Atlas password. With
    ATLAS_OIDC_STEP_UP=on (and only then; off, the paragraph above is the whole rule) such an
    owner confirms the change with a fresh sign-in at the IdP: `GET /auth/oidc/step-up
    ?config=<NAME>`, the agent-credential step-up of TASK-1415 (auth.py §14c), approving a
    change of THAT setting (grant `config:<NAME>`). The PUT spends it (`auth.consume_step_up`,
    in this write's transaction, so a refused or unchanged write keeps it). Without one it is
    403 `step_up_required` naming `step_up_url`; with one for another setting 403
    `step_up_setting_mismatch`; one approved for an agent credential is no step-up here
    (`step_up_required`, kept for its credential). A password such an owner sends is ignored.
    Single use: one sign-in, one change. A local owner, break-glass included, is asked for
    the password exactly as above. `GET /api/config`'s `writes.step_up` says which
    (`password` or `sso`, with the step-up's state for the tab).

EVERY CHANGE IS A graph_event: `node.config.set` on the setting's PART node (`atlas-system*`),
through `deps.record_graph_event` (the one writer), recorded before the commit, with detail
{setting, from_source, to_source, effect}: WHICH setting changed, where its value came from
before and after (env/board/default), and when it applies. NEVER THE VALUES, old or new (owner decision,
2026-10-26): `GET /api/events` shows an event on a part node to whoever can see that node, which
is wider than the owner-only `GET /api/config`, so a value in the event would be a disclosure the
configuration read itself refuses. The value is in `config_value` and in `GET /api/config`. No
changeable setting is a secret either, and the route checks that before writing.

WHO MAY READ IT (`GET /api/config`). The same two callers as `GET /api/audit/export`, and
everybody else gets a 403:

  * an OWNER's browser session;
  * a service token that holds the `admin` scope AND is linked to a person who is a live owner
    (an operator's agent). Role does not inherit (ADR 0034); this is the export's explicit
    two-fact rule, reused rather than restated, so the two owner-only reads cannot drift apart.

The instance's configuration names hosts, origins, identity-provider details and which secrets
exist; that is operator knowledge, not something every member needs. 403 rather than 404, as
`deps._require_owner` argues: the existence of an admin surface is no secret.

A SECRET IS NEVER SERVED. A secret's entry is `set from file`, `set from env` or `not set`, decided
without opening the file. No value, prefix, hash or length of one is in the response, and
`verify_config_panel.py` sets every secret to a known value and searches the body for it.

NOT A NODE READ. `GET` reads the process environment, never a node row, so
`verify_node_access.py` §F classifies it as reading no node data, and it is not in
`audit_log.AUDITED_READ_ROUTES`. The PUT writes no node row either; its event names a part node.

The values are this API process's: the environment it started with, through the same loaders the
app itself uses. The identity and OAuth settings are the ones the app built at import
(`identity.SETTINGS`, `identity.OAUTH`), so a provider registered in code is reflected too.

`verify_config_panel.py` proves phase 1 (and that it holds with the flag off);
`verify_config_writes.py` proves phase 2.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import auth
import identity
import models_auth
import settings_catalog
from db import session
from routers.audit import _may_export as _owner_or_owners_admin_token

router = APIRouter()
#: Registered by main.py only when ATLAS_CONFIG_WRITES is on (see the header).
write_router = APIRouter()

_FORBIDDEN = {"error": "forbidden",
              "detail": "The configuration is limited to owner accounts and to admin-scoped "
                        "agent tokens of an owner."}


#: Set True by main.py, at import, when ATLAS_CONFIG_WRITES is on; never re-parsed per request.
WRITES_ON = False


def _writes_on() -> bool:
    return WRITES_ON


def _describe() -> dict:
    if not _writes_on():
        return settings_catalog.describe(identity_settings=identity.SETTINGS,
                                         oauth_settings=identity.OAUTH)
    import models_config
    return settings_catalog.describe(identity_settings=identity.SETTINGS,
                                     oauth_settings=identity.OAUTH, writes=True,
                                     board=models_config.board_applied())


@router.get("/api/config")
def config_get():
    """Every setting, per part node, with its effective value and source. Owner-only."""
    if not _owner_or_owners_admin_token():
        return JSONResponse(_FORBIDDEN, status_code=403)
    body = _describe()
    sso = _sso_step_up_status() if "writes" in body else None
    if sso is not None:
        body["writes"].update(sso)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


def _sso_step_up_status() -> Optional[dict]:
    """The step-up state for an IdP-backed owner's session (TASK-1436), or None: step-up
    off, a token, or a local owner (whose `step_up` stays "password")."""
    if not identity.step_up_enabled():
        return None
    principal = auth.current_principal.get()
    if principal is None or principal.kind != "session" or not principal.user_id:
        return None
    with session() as s:
        person = s.get(models_auth.AuthUser, principal.user_id)
        if person is None or not auth.step_up_required(person):
            return None
        return auth.config_step_up_status(s, principal, person)


class ConfigWrite(BaseModel):
    #: The new value, or null to remove the board's value (back to the default).
    value: Any = None
    #: Step-up: the owner's own password, checked on every change.
    password: Optional[str] = None
    csrf_token: Optional[str] = None


def _refuse(status: int, error: str, detail: str, field: Optional[str] = None) -> JSONResponse:
    body = {"error": error, "detail": detail}
    if field:
        body["field"] = field
    return JSONResponse(body, status_code=status)


def _entry(name: str) -> dict:
    return next(e for p in _describe()["parts"] for e in p["settings"] if e["name"] == name)


@write_router.put("/api/config/{name}")
def config_put(name: str, body: ConfigWrite, request: Request):
    """Change one changeable setting (or reset it with null). Owner session + password only."""
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    principal = auth.current_principal.get()
    if principal is None or principal.kind != "session":
        return _refuse(403, "session_required",
                       "Configuration is changed by a person in a browser session; no token "
                       "may change it, an owner's admin token included.")
    if not principal.is_owner:
        return JSONResponse(_FORBIDDEN, status_code=403)

    no = settings_catalog.change_refusal(name)
    if no is not None:
        code, reason = no
        return _refuse(404 if code == "unknown_setting" else 403, code, reason, "name")
    item = settings_catalog.BY_NAME[name]
    # Belt: change_refusal already refused every secret; a secret's value must never be stored
    # or reach an event's detail, so this is asserted at the write, not only decided above.
    if item.secret or item.kind != "plain" or not item.changeable:  # pragma: no cover - belt
        raise RuntimeError("settings_catalog marks a secret or non-plain setting changeable")

    import models_config
    if models_config.environment_sets(name):
        return _refuse(409, "environment_wins",
                       "%s is set in the server's environment, and the environment wins over "
                       "the board; remove it there first to manage it from here." % name, "name")

    new: Optional[str] = None
    if body.value is not None:
        try:
            new = item.validate(body.value)
        except ValueError as exc:
            return _refuse(400, "bad_value", str(exc), "value")

    ip, ua = auth._client_ip(request), auth._user_agent(request)
    if identity.step_up_enabled():
        # TASK-1436: an IdP-backed owner confirms at the IdP, not with a password.
        with session() as s:
            person = s.get(models_auth.AuthUser, principal.user_id)
            sso = person is not None and auth.step_up_required(person)
        if sso:
            return _sso_put(principal, name, item, new, ip=ip, ua=ua)

    if not body.password:
        return _refuse(403, "step_up_required",
                       "Changing configuration needs your password, every time.", "password")
    with session() as s:
        person = s.get(models_auth.AuthUser, principal.user_id)
        if person is None or person.disabled_at is not None or person.is_service:
            return JSONResponse(_FORBIDDEN, status_code=403)
        if not auth.reauthenticate(s, person, body.password, ip=ip, user_agent=ua,
                                   reason="config_write"):
            s.commit()   # the failure event and the throttle row are the point
            return _refuse(403, "password_incorrect",
                           "That password did not match. Configuration is not changed from a "
                           "session alone.", "password")
        return _store(s, person, name, item, new)


def _sso_put(principal, name: str, item, new: Optional[str], *, ip, ua) -> JSONResponse:
    """The PUT for an IdP-backed owner with step-up on: spend the step-up that approved THIS
    setting (the header's STEP-UP FOR AN SSO OWNER), then the same store as after a password."""
    with session() as s:
        person = s.get(models_auth.AuthUser, principal.user_id)
        if person is None or person.disabled_at is not None or person.is_service:
            return JSONResponse(_FORBIDDEN, status_code=403)
        outcome = auth.consume_step_up(s, principal, person, agent=auth.config_write_grant(name),
                                       ip=ip, user_agent=ua, reason="config_write")
        if outcome != "ok":
            status = auth.config_step_up_status(s, principal, person)
            s.commit()   # the failure event is the point; a grant, if any, is kept
            if outcome == "mismatch":
                return _refuse(403, "step_up_setting_mismatch",
                               "Your sign-in at your identity provider approved a change of %s, "
                               "not %s. Change that one, or confirm again for this one."
                               % (status.get("step_up_setting"), name), "name")
            return JSONResponse(
                {"error": "step_up_required", "step_up_url": status.get("step_up_url"),
                 "detail": "Confirm this change with a fresh sign-in at your identity provider "
                           "first, then save it within %d seconds. Configuration is not "
                           "changed from a session alone." % status.get("step_up_max_age", 0)},
                status_code=403)
        return _store(s, person, name, item, new)


def _store(s, person, name: str, item, new: Optional[str]) -> JSONResponse:
    """Store the re-authenticated change, record its event and commit; an unchanged value
    is rolled back, which also keeps a step-up grant this request would have spent."""
    import models_config
    from deps import record_graph_event
    before = _entry(name)
    previous = models_config.store(s, name, new, user_id=person.id, subject=person.subject)
    if previous == new:
        s.rollback()
        return JSONResponse({"setting": before, "changed": False, "effect": "now"})
    record_graph_event(s, "node.config.set", entity_type="node", entity_id=item.part,
                       # No value, old or new: see the header (EVERY CHANGE IS A graph_event).
                       detail={"setting": name, "from_source": before.get("source"),
                               "to_source": "board" if new is not None else "default",
                               "effect": "now"})
    s.commit()
    models_config.apply_now(name, new)
    return JSONResponse({"setting": _entry(name), "changed": True, "effect": "now"},
                        headers={"Cache-Control": "no-store"})
