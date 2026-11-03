"""`me` routes, split out of `app.py` (STORY-oss-04); the code is moved, not changed.
Registered on the app by `main.py` via `include_router`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

import models_auth   # auth_* + node_visibility, own Bases (ADR 0025/0026, TASK-966/973)
import auth         # THE GATE (TASK-967) + service tokens (TASK-968)
import passwords    # handle + password rules shared with admin.py (TASK-1115)
import identity     # ATLAS_OIDC_STEP_UP (TASK-1415), configured when `auth` was imported
import github_link  # GitHub OAuth identity + repo-access invitations (secrets are files)
from db import session
from deps import _GH_OFF, CsrfOnly

router = APIRouter()


def _session_principal_or_refusal():
    """`(principal, None)` for a cookie session, else `(None, response)`."""
    principal = auth.current_principal.get()
    if principal is None or principal.kind != "session":
        return None, JSONResponse(
            {"error": "forbidden",
             "detail": "GitHub linking needs a signed-in browser session."},
            status_code=403)
    return principal, None


def _github_redirect_uri(request: Request) -> str | None:
    """The callback URL for THIS host, only if it is an allow-listed origin.
    Built from the (already Host-allow-listed) request host, because live is
    reachable at two names and each needs its own callback registered."""
    host = (request.headers.get("host") or "").strip().lower()
    for origin in auth.ALLOWED_ORIGINS:
        if origin.split("://", 1)[-1].lower() == host:
            return origin.rstrip("/") + "/api/me/github/callback"
    return None


def _github_view(row) -> dict:
    return {
        "configured": github_link.configured(),
        "repo": github_link.status()["repo"],
        "linked": row is not None,
        "login": row.github_login if row else None,
        "repo_access": row.repo_access if row else "not_linked",
        "linked_at": row.linked_at.isoformat() if row and row.linked_at else None,
    }


@router.get("/api/me/github")
def get_my_github() -> JSONResponse:
    """The caller's own link state. Always 200 for a session, so the Settings
    pane can tell 'off' (`configured: false`) from 'not linked'."""
    principal, refused = _session_principal_or_refusal()
    if refused is not None:
        return refused
    with session() as s:
        row = s.get(models_auth.GithubLink, principal.user_id)
        return JSONResponse(_github_view(row))


@router.get("/api/me/github/start")
def start_github_link(request: Request):
    """Begin 'Sign in with GitHub'. A navigation (GET -> 302) that changes no
    state: it mints a `state` bound to this session and sends the browser to
    GitHub. The state check at the callback is the CSRF defence."""
    principal, refused = _session_principal_or_refusal()
    if refused is not None:
        return refused
    if not github_link.configured():
        return JSONResponse(_GH_OFF, status_code=404)
    redirect_uri = _github_redirect_uri(request)
    if redirect_uri is None:
        return JSONResponse({"error": "bad_host"}, status_code=400)
    sid = request.cookies.get(auth.SESSION_COOKIE) or ""
    state = github_link.make_state(sid, auth._TOKEN_PEPPER)
    return RedirectResponse(github_link.authorize_url(redirect_uri, state),
                            status_code=302)


def _gh_back(result: str, reason: str | None = None) -> RedirectResponse:
    q = "settings=account&github=" + result
    if reason:
        q += "&reason=" + reason
    return RedirectResponse("/board?" + q, status_code=303)


@router.get("/api/me/github/callback")
def github_link_callback(request: Request, code: str | None = Query(None),
                         state: str | None = Query(None),
                         error: str | None = Query(None)):
    """GitHub sends the browser back here. The cookie session identifies WHO is
    linking; `state` proves this callback answers a flow THIS session started.
    The code is exchanged server-side; the user's access token is used once to
    read `/user` and never stored. The stable key is the numeric id."""
    principal, refused = _session_principal_or_refusal()
    if refused is not None:
        return refused
    if not github_link.configured():
        return JSONResponse(_GH_OFF, status_code=404)
    sid = request.cookies.get(auth.SESSION_COOKIE) or ""
    if not github_link.verify_state(state or "", sid, auth._TOKEN_PEPPER):
        return JSONResponse(
            {"error": "bad_state",
             "detail": "That sign-in did not start from this session. Start again from Settings."},
            status_code=400)
    if error or not code:
        return _gh_back("error", "denied")
    redirect_uri = _github_redirect_uri(request)
    if redirect_uri is None:
        return JSONResponse({"error": "bad_host"}, status_code=400)
    try:
        gid, login = github_link.identity_from_code(code, redirect_uri)
    except github_link.GithubError:
        return _gh_back("error", "github")

    ip, ua = auth._client_ip(request), auth._user_agent(request)
    with session() as s:
        mine = s.get(models_auth.GithubLink, principal.user_id)
        if mine is not None and mine.github_id != gid:
            return _gh_back("error", "already_linked")
        other = s.scalar(select(models_auth.GithubLink).where(
            models_auth.GithubLink.github_id == gid))
        if other is not None and other.user_id != principal.user_id:
            return _gh_back("error", "taken")
        if mine is None:
            s.add(models_auth.GithubLink(user_id=principal.user_id, github_id=gid,
                                         github_login=login))
        else:
            mine.github_login = login      # a rename; the id is the key
        auth._event(s, "github_linked", user_id=principal.user_id,
                    actor=principal.subject, ip=ip, user_agent=ua,
                    detail="github_id=%d login=%s" % (gid, login))
        s.commit()
    return _gh_back("linked")


@router.post("/api/me/github/unlink")
def unlink_my_github(body: CsrfOnly, request: Request) -> JSONResponse:
    """Remove the caller's own link. If repo access was granted it is revoked
    on GitHub FIRST and the link stays if that fails - a link removed while the
    collaborator remains would leave access the owner can no longer see."""
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    principal, refused = _session_principal_or_refusal()
    if refused is not None:
        return refused
    ip, ua = auth._client_ip(request), auth._user_agent(request)
    with session() as s:
        row = s.get(models_auth.GithubLink, principal.user_id)
        if row is None:
            return JSONResponse(_github_view(None))
        if row.repo_access != models_auth.GITHUB_ACCESS_LINKED:
            if not github_link.configured():
                return JSONResponse(
                    {"error": "github_not_configured",
                     "detail": "Repo access is granted and cannot be removed while GitHub "
                               "linking is unconfigured. Ask the owner."}, status_code=409)
            try:
                github_link.revoke(row.github_id, row.github_login, row.invitation_id)
            except github_link.GithubError:
                return JSONResponse(
                    {"error": "github_unreachable",
                     "detail": "GitHub did not confirm removing your repo access, so the "
                               "link was kept. Try again."}, status_code=502)
            auth._event(s, "github_repo_revoked", user_id=principal.user_id,
                        actor=principal.subject, ip=ip, user_agent=ua,
                        detail="unlink github_id=%d" % row.github_id)
        auth._event(s, "github_unlinked", user_id=principal.user_id,
                    actor=principal.subject, ip=ip, user_agent=ua,
                    detail="github_id=%d login=%s" % (row.github_id, row.github_login))
        s.delete(row)
        s.commit()
        return JSONResponse(_github_view(None))


# --------------------------------------------------------------------------- #
# A personal password for an SSO person (TASK-1442)
# --------------------------------------------------------------------------- #
class PasswordSet(BaseModel):
    """The new password, twice. No current password: the person has only the
    temporary one, never meant for use, and a fresh IdP step-up proves them instead."""
    new_password: str = Field(min_length=1)
    confirm_password: str = Field(min_length=1)
    csrf_token: str | None = None


@router.post("/api/me/password")
def set_my_password(body: PasswordSet, request: Request) -> JSONResponse:
    """Set a personal password for an SSO person who has none yet.

    An account made with a temporary password and then linked to the IdP
    (`user add`, `user link-idp`) keeps `must_change_password`. Its SSO session is
    not held by the forced-change gate (auth §5); `/api/me` says `password_setup`
    and the board offers this. It needs a fresh IdP sign-in approving exactly this
    (`GET /auth/oidc/step-up?password=1`, auth §14c), spent here once; without one
    it is 403 `step_up_required`. Session principals only: a token, whatever its
    scopes, is 403 `session_required` (`admin` in WRITE_SCOPE_ROUTES is the belt).
    The rest is `auth.set_pending_password`.
    """
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    principal = auth.current_principal.get()
    if principal is None or principal.kind != "session" or not principal.user_id:
        return JSONResponse(
            {"error": "session_required",
             "detail": "A password is set by the signed-in person, in a browser."},
            status_code=403)
    ip, ua = auth._client_ip(request), auth._user_agent(request)
    with session() as s:
        person = s.get(models_auth.AuthUser, principal.user_id)
        if person is None or person.disabled_at is not None or person.is_service:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        status, payload = auth.set_pending_password(
            s, principal, person, new=body.new_password, confirm=body.confirm_password,
            ip=ip, user_agent=ua)
    return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- #
# Self-service agent credentials (STORY-atlas-agent-credentials / TASK-1115)
# --------------------------------------------------------------------------- #
#: Bounds on the expiry a contributor may ask for. Finite ALWAYS: the CLI's
#: `--days 0` (never expires) has no equivalent here, because an agent
#: credential that never expires is one nobody ever revisits. The ceiling is the
#: same 365 the CLI defaults to; the floor exists so a typo cannot mint a token
#: that is already dead.
AGENT_CREDENTIAL_MIN_DAYS = 1
AGENT_CREDENTIAL_MAX_DAYS = auth.SERVICE_TOKEN_DEFAULT_DAYS


class AgentCredentialCreate(BaseModel):
    """What a contributor sends to create their own agent credential.

    `password` is their OWN password, re-entered. It is never stored, never
    logged and never echoed back — see `auth.reauthenticate` for why a live
    session is not sufficient on its own.

    REQUIRED, EXACTLY AS BEFORE, unless ATLAS_OIDC_STEP_UP=on (TASK-1415). The
    field is declared from the setting at import, so with step-up off (the
    default, the homelab) the schema, the 422 and the order it comes in are
    today's, byte for byte. With step-up on it is optional in the schema, because
    an IdP-backed person has no Atlas password and proves a fresh IdP sign-in
    instead (`auth` §14c); `_require_password_unless_step_up` then gives every
    other caller the same 422 FastAPI would have, before anything else runs. A
    password an IdP-backed person sends is ignored, never checked against a
    verifier `link-idp` may have kept.
    """
    purpose: str = Field(min_length=1, max_length=64)
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    expires_days: int = AGENT_CREDENTIAL_MAX_DAYS
    if identity.step_up_enabled():
        password: str | None = Field(default=None, min_length=1)
    else:
        password: str = Field(min_length=1)
    name: str | None = None
    csrf_token: str | None = None


class _PasswordRequired(AgentCredentialCreate):
    """The schema with the password required: what every caller but an IdP-backed
    person with step-up on is validated against (`_require_password_unless_step_up`)."""
    password: str = Field(min_length=1)


def _require_password_unless_step_up(body: "AgentCredentialCreate") -> None:
    """With step-up on, refuse a missing password for everyone who still needs one,
    with FastAPI's own 422 body (pydantic's errors under `body`, the same input),
    and FIRST, before the write guard, as schema validation was. Raises; returns
    None when the caller may go on. A no-op with step-up off: the schema did it."""
    if not identity.step_up_enabled() or body.password is not None:
        return
    principal = auth.current_principal.get()
    if principal is not None and principal.kind == "session" and principal.user_id:
        with session() as s:
            person = s.get(models_auth.AuthUser, principal.user_id)
            if person is not None and auth.step_up_required(person):
                return
    try:
        _PasswordRequired.model_validate(body.model_dump(exclude_unset=True))
    except ValidationError as exc:
        raise RequestValidationError(
            [{**e, "loc": ("body", *e["loc"])} for e in exc.errors(include_url=False)]) from None


def _agent_credential_refusal(principal, body: "AgentCredentialCreate"):
    """The rules EVERY path to a credential must satisfy, or the response to
    return. `None` means proceed.

    ONE implementation because there are two doors — first issue (TASK-1115) and
    re-issue for an existing agent (TASK-1116) — and a rotation endpoint that
    skips a check is a bypass of the endpoint that does not. That is not
    hypothetical: rotation is the path somebody reaches for in a hurry, after a
    leak, which is exactly when a weaker check costs most.
    """
    if principal is None or principal.kind != "session" or not principal.user_id:
        # NOT 401: the caller may be perfectly well authenticated as a machine.
        # It is the KIND that is refused, and this is unconditional — an
        # `admin`-scoped token satisfies `guard_write` and still stops here,
        # because a Bearer caller minting another Bearer caller is one leaked
        # token becoming a key factory.
        return JSONResponse(
            {"error": "session_required",
             "detail": "An agent credential is created by a signed-in person, from the "
                       "board. A service token cannot mint another one."},
            status_code=403)

    allowed = models_auth.self_mintable_scopes(principal.role)
    wanted = tuple(dict.fromkeys(sc.strip() for sc in body.scopes if sc and sc.strip()))
    if not wanted:
        return JSONResponse(
            {"error": "no_scope", "field": "scopes",
             "detail": "A token with no scope is a broken token, not a safe one. "
                       "Choose from %s." % list(allowed)},
            status_code=400)
    over = [sc for sc in wanted if sc not in allowed]
    if over:
        return JSONResponse(
            {"error": "scope_not_permitted", "field": "scopes",
             "detail": "Your role may create an agent with %s. Refused: %s."
                       % (list(allowed), over)},
            status_code=400)

    if not (AGENT_CREDENTIAL_MIN_DAYS <= body.expires_days <= AGENT_CREDENTIAL_MAX_DAYS):
        return JSONResponse(
            {"error": "bad_expiry", "field": "expires_days",
             "detail": "An agent credential must expire, between %d and %d days. "
                       "There is no never-expires option here."
                       % (AGENT_CREDENTIAL_MIN_DAYS, AGENT_CREDENTIAL_MAX_DAYS)},
            status_code=400)
    return None


def _reauth_refusal(s, principal, person, body: "AgentCredentialCreate", *, ip, ua,
                    reason: str, password_detail: str, agent_name):
    """Re-authentication, the second of the four rules. `None` means proceed.

    ONE implementation for both doors, for the reason `_agent_credential_refusal`
    gives. A local person re-enters their password (`auth.reauthenticate`), byte
    for byte as before TASK-1415. An IdP-backed person on an install with
    ATLAS_OIDC_STEP_UP=on spends the fresh IdP sign-in their session holds
    (`auth.consume_step_up`), and only on the agent name it approved
    (`agent_name()`, called only on this path so a local caller's refusals keep
    their order); without one the answer names where to get it. Either way the
    caller commits on refusal: the failure event is the point.
    """
    if auth.step_up_required(person):
        try:
            agent = agent_name()
        except ValueError as exc:
            return JSONResponse(
                {"error": "bad_purpose", "field": "purpose", "detail": str(exc)},
                status_code=400)
        outcome = auth.consume_step_up(s, principal, person, agent=agent, ip=ip,
                                       user_agent=ua, reason=reason)
        if outcome == "ok":
            return None
        status = auth.step_up_status(s, principal, person)
        if outcome == "mismatch":
            return JSONResponse(
                {"error": "step_up_agent_mismatch", "field": "purpose",
                 "approved_agent": status.get("step_up_agent"),
                 "detail": "Your sign-in at your identity provider approved the credential "
                           "for %r, not %r. Create that one, or confirm again for this one."
                           % (status.get("step_up_agent"), agent)},
                status_code=403)
        return JSONResponse(
            {"error": "step_up_required",
             "step_up_url": status.get("step_up_url"),
             "detail": "Confirm with a fresh sign-in at your identity provider first, then "
                       "create the credential within %d seconds. A credential is not created "
                       "from a session alone." % status.get("step_up_max_age", 0)},
            status_code=403)
    if not auth.reauthenticate(s, person, body.password, ip=ip, user_agent=ua, reason=reason):
        return JSONResponse(
            {"error": "password_incorrect", "field": "password", "detail": password_detail},
            status_code=403)
    return None


def _agent_scopes(body: "AgentCredentialCreate") -> list[str]:
    """The de-duplicated scope list, after `_agent_credential_refusal` passed."""
    return list(dict.fromkeys(sc.strip() for sc in body.scopes if sc and sc.strip()))


# AGENT NAMES: `auth.self_service_agent_handle(person, purpose)`, `<base>-<purpose>`
# with the base `atlas-admin token mint` uses too (`passwords.agent_base`, TASK-1415).
#
# THE PERSON'S NAME IS PREFIXED RATHER THAN ASKED FOR. Accounts share ONE
# namespace — `bot add` says so in its own refusal — so two contributors both
# naming an agent "claude-code" would collide, and the second would be told
# their choice was taken by an account they cannot see. Prefixing removes the
# collision and makes the Security & access grid readable, at the cost of the
# contributor not choosing the whole string. That is the right trade: the
# story asks them to name a PURPOSE (`claude-code`), not an identity.


@router.post("/api/me/agent-credential")
def create_agent_credential(body: AgentCredentialCreate, request: Request) -> JSONResponse:
    """Mint a service token for a new agent belonging to the caller.

    This is the route that makes the epic's title true — a contributor points
    an agent at Atlas without a shell. It replaces three operator-only commands
    (`ssh` to the server, `admin bot add`, `admin token add`) with one call the
    person who wants the credential can make themselves.

    FOUR THINGS IT MUST NOT SOFTEN, all from the story:

    1. **Session principals only.** A Bearer caller cannot mint another
       credential — one leaked token must not become a key factory.
    2. **Re-authentication.** See `auth.reauthenticate`; for an IdP-backed
       person with ATLAS_OIDC_STEP_UP=on, a fresh IdP sign-in instead
       (`_reauth_refusal`, `auth` §14c).
    3. **A ceiling from ROLE, not from the caller's scopes**, because a human
       holds none. See `models_auth.self_mintable_scopes`.
    4. **Shown once.** The raw value is returned by this call and by nothing
       else, ever.

    The bot account is created HERE, at first credential, invisibly (ADR 0034):
    usernames are permanent, so naming an agent before anyone knows its purpose
    locks in a bad name, and an unused bot row would be a permanent column in
    the access grid. What the agent may SEE needs no administration afterwards —
    it inherits its person's grants (TASK-1114).
    """
    # GUARD FIRST, exactly as every other mutating route does — `guard_write` is
    # their first statement. It costs a read-scoped Bearer a 403 naming `admin`
    # (WRITE_SCOPE_ROUTES) and a cookie session the Origin + synchronizer-token
    # check. Putting the kind refusal below it means the two layers report in a
    # fixed order: a caller learns it lacks the scope before it learns the kind
    # is wrong, and neither answer is more permissive than the other.
    _require_password_unless_step_up(body)
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused

    principal = auth.current_principal.get()
    refused = _agent_credential_refusal(principal, body)
    if refused is not None:
        return refused
    wanted = _agent_scopes(body)

    ip, ua = auth._client_ip(request), auth._user_agent(request)

    with session() as s:
        person = s.get(models_auth.AuthUser, principal.user_id)
        if person is None or person.disabled_at is not None or person.is_service:
            return JSONResponse({"error": "forbidden"}, status_code=403)

        refused = _reauth_refusal(
            s, principal, person, body, ip=ip, ua=ua, reason="agent_credential",
            password_detail="That password did not match. A credential is not created "
                            "from a session alone.",
            agent_name=lambda: auth.self_service_agent_handle(person, body.purpose))
        if refused is not None:
            s.commit()   # the failure event and the throttle row are the point
            return refused

        try:
            # The same name `atlas-admin token mint` gives (`passwords.agent_base`):
            # a local person's username is unchanged; an IdP-backed person's email
            # local part, not their opaque subject (TASK-1415).
            handle = auth.self_service_agent_handle(person, body.purpose)
        except ValueError as exc:
            return JSONResponse(
                {"error": "bad_purpose", "field": "purpose", "detail": str(exc)},
                status_code=400)

        if s.scalar(select(models_auth.AuthUser).where(
                models_auth.AuthUser.idp == "local",
                models_auth.AuthUser.subject == handle)) is not None:
            return JSONResponse(
                {"error": "name_taken", "field": "purpose",
                 "detail": "You already have an agent called %r. Names are permanent, "
                           "so pick a different purpose rather than reusing one." % handle},
                status_code=409)

        bot = models_auth.AuthUser(
            id=str(uuid.uuid4()),
            idp="local",
            subject=handle,
            name=(body.name or "").strip()[:120] or None,
            role=models_auth.ROLE_MEMBER,   # NEVER the person's role — ADR 0034
            is_service=True,
            label=handle,
            password_hash=None,             # CHECK ck_auth_user_service_has_no_password
            must_change_password=False,     # a bot inheriting this mints a dead token
            person_user_id=person.id,
        )
        blocked = models_auth.refuse_person_link(bot, person)
        if blocked is not None:  # pragma: no cover — unreachable via this route
            return JSONResponse({"error": "cannot_link", "detail": blocked}, status_code=400)
        s.add(bot)
        s.flush()

        auth._event(s, "user_created", user_id=bot.id, actor=person.subject,
                    ip=ip, user_agent=ua, reason="self_service_agent",
                    detail="agent=%s person=%s" % (handle, person.subject))
        auth._event(s, "bot_person_linked", user_id=bot.id, actor=person.subject,
                    ip=ip, user_agent=ua, reason="self_service_agent",
                    detail="person=%s" % person.subject)
        try:
            raw, row = auth.mint_service_token(
                s, owner=bot, scopes=wanted, label=handle,
                expires_days=body.expires_days)
        except ValueError as exc:  # pragma: no cover — the checks above precede it
            s.rollback()
            return JSONResponse({"error": "mint_failed", "detail": str(exc)}, status_code=400)
        s.commit()

        payload = {
            "agent": handle,
            "scopes": wanted,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "person": person.subject,
            # SHOWN ONCE. Nothing else returns this, and it is not stored in
            # clear anywhere — only HMAC(pepper, token) is in the row.
            "token": raw,
            "shown_once": True,
        }
    return JSONResponse(payload, status_code=201)

def _token_json(row, *, now) -> dict:
    """One credential row, as its OWNER may see it.

    THE PREFIX IS INCLUDED, and that is a deliberate reading of "never return
    anything a caller could present". A presented token is
    `atlas_svc_<prefix>_<secret>`; the prefix is stored in CLEAR precisely
    because it is the lookup key, and it authenticates nothing on its own — the
    secret does, and only `HMAC(pepper, token)` of that is stored. Withholding
    it would leave somebody holding two credentials for one agent unable to tell
    which of them is deployed, which is the question the screen exists to
    answer, and `admin token list` already shows it to an operator.
    """
    expires = row.expires_at
    used = row.last_used_at
    return {
        "id": row.id,
        "prefix": row.prefix,
        "label": row.label,
        "scopes": [sc for sc in (row.scopes or "").split(",") if sc],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": expires.isoformat() if expires else None,
        "last_used_at": used.isoformat() if used else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        # DERIVED HERE so every surface agrees. "Never used" and "not used for
        # months" are the two rows worth revoking, and the story says neither is
        # visible from a shell to the person who owns them.
        "never_used": used is None,
        "expired": bool(expires and expires <= now),
        "days_unused": (now - used).days if used else None,
        "live": row.revoked_at is None and not (expires and expires <= now),
    }


@router.get("/api/me/agent-credentials")
def list_agent_credentials() -> JSONResponse:
    """Every credential the caller holds, grouped by the agent that owns it.

    SCOPED IN THE QUERY, not filtered afterwards: the agents are selected by
    `person_user_id == caller`, so this can never load somebody else's rows and
    then rely on remembering to drop them. Same rule, same reason, as
    `_visible_node_ids` filtering inside the query rather than over the result.
    """
    principal = auth.current_principal.get()
    if principal is None or not principal.user_id:  # pragma: no cover — gated
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    now = datetime.now(timezone.utc)
    with session() as s:
        person = s.get(models_auth.AuthUser, principal.user_id)
        # How this person re-authenticates to create one: `password` (every local
        # account, and everyone by default) or `sso` with the step-up route and how
        # long the fresh sign-in this session holds is still good for (TASK-1415).
        reauth = (auth.step_up_status(s, principal, person) if person is not None
                  else {"reauth": "password"})
        agents = s.scalars(
            select(models_auth.AuthUser)
            .where(models_auth.AuthUser.person_user_id == principal.user_id,
                   models_auth.AuthUser.is_service.is_(True))
            .order_by(models_auth.AuthUser.subject)).all()
        out = []
        for bot in agents:
            rows = s.scalars(
                select(models_auth.AuthServiceToken)
                .where(models_auth.AuthServiceToken.owner_user_id == bot.id)
                .order_by(models_auth.AuthServiceToken.created_at)).all()
            out.append({
                "agent": bot.subject,
                "name": bot.name,
                "disabled": bot.disabled_at is not None,
                "credentials": [_token_json(r, now=now) for r in rows],
            })
    return JSONResponse({
        "agents": out,
        # What a member may ask for, so the screen offers exactly that and no
        # control that is guaranteed to fail.
        "scopes_available": list(models_auth.self_mintable_scopes(principal.role)),
        "expiry_days": {"min": AGENT_CREDENTIAL_MIN_DAYS, "max": AGENT_CREDENTIAL_MAX_DAYS},
        # ONLY for an IdP-backed person with step-up on: everyone else gets
        # today's response, byte for byte, and the screen's password field.
        **({} if reauth.get("reauth") == "password" else reauth),
    })


@router.post("/api/me/agent/{agent}/credential")
def reissue_agent_credential(agent: str, body: AgentCredentialCreate,
                             request: Request) -> JSONResponse:
    """Issue ANOTHER credential for an agent the caller already has — rotation.

    WHY THIS EXISTS AT ALL (owner decision, 2026-08-16): usernames are permanent, so
    `POST /api/me/agent-credential` 409s once the agent exists. Without this
    route, revoking a credential leaves a contributor with two bad options —
    invent a different purpose name, creating a second permanent account and a
    second column in the access grid, or ask an operator, which is the thing
    this epic exists to remove. Rotation is the missing half of revoke, not a
    later refinement.

    IT DOES NOT REVOKE THE OLD TOKEN. `mint_service_token`'s docstring is
    explicit that multiple live tokens per principal exist for exactly this:
    revoke-then-mint is an outage, and a credential that cannot be rotated
    without one is a credential nobody rotates. The order is mint → deploy →
    confirm `last_used_at` moves → revoke, and the API must not collapse it.

    `purpose` is ignored here — the agent is named by the path.
    """
    _require_password_unless_step_up(body)
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    principal = auth.current_principal.get()
    refused = _agent_credential_refusal(principal, body)
    if refused is not None:
        return refused
    wanted = _agent_scopes(body)
    ip, ua = auth._client_ip(request), auth._user_agent(request)

    with session() as s:
        person = s.get(models_auth.AuthUser, principal.user_id)
        if person is None or person.disabled_at is not None or person.is_service:
            return JSONResponse({"error": "forbidden"}, status_code=403)

        bot = s.scalar(select(models_auth.AuthUser).where(
            models_auth.AuthUser.idp == "local",
            models_auth.AuthUser.subject == passwords.normalise_username(agent)))
        # SOMEBODY ELSE'S AGENT AND A NONEXISTENT ONE ARE THE SAME 404, the same
        # rule ADR 0026 applies to nodes: a distinguishable refusal confirms a
        # guessed name, and agent names are guessable by construction because
        # they are `<person>-<purpose>`.
        if bot is None or not bot.is_service or bot.person_user_id != person.id:
            return JSONResponse(
                {"error": "not_found",
                 "detail": "No agent of yours called %r." % agent}, status_code=404)
        if bot.disabled_at is not None:
            return JSONResponse(
                {"error": "agent_disabled",
                 "detail": "%s is disabled. Re-enable it before issuing a credential; "
                           "a token minted now would be refused on every call."
                           % bot.subject}, status_code=409)

        refused = _reauth_refusal(
            s, principal, person, body, ip=ip, ua=ua, reason="agent_credential_reissue",
            password_detail="That password did not match. Rotating a credential is no "
                            "softer a path than creating one.",
            agent_name=lambda: bot.subject)
        if refused is not None:
            s.commit()
            return refused

        try:
            raw, row = auth.mint_service_token(
                s, owner=bot, scopes=wanted, label=bot.subject,
                expires_days=body.expires_days)
        except ValueError as exc:  # pragma: no cover
            s.rollback()
            return JSONResponse({"error": "mint_failed", "detail": str(exc)}, status_code=400)
        auth._event(s, "token_created", user_id=bot.id, actor=person.subject,
                    ip=ip, user_agent=ua, reason="self_service_reissue",
                    detail="agent=%s person=%s" % (bot.subject, person.subject))
        s.commit()
        live = s.scalars(select(models_auth.AuthServiceToken).where(
            models_auth.AuthServiceToken.owner_user_id == bot.id,
            models_auth.AuthServiceToken.revoked_at.is_(None))).all()
        payload = {
            "agent": bot.subject,
            "scopes": wanted,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "person": person.subject,
            "token": raw,
            "shown_once": True,
            # SAY WHAT WAS NOT DONE. The old credential still works, on purpose;
            # somebody who assumes otherwise deploys the new one and never
            # revokes the old, which is the failure this ordering exists to
            # avoid being forced into.
            "live_credentials": len(live),
            "old_still_valid": len(live) > 1,
        }
    return JSONResponse(payload, status_code=201)


class CredentialRevoke(BaseModel):
    csrf_token: str | None = None


@router.post("/api/me/agent-credential/{token_id}/revoke")
def revoke_agent_credential(token_id: str, body: CredentialRevoke,
                            request: Request) -> JSONResponse:
    """Revoke one of the caller's own credentials. Permanent, effective at once.

    Session-only like its siblings: a leaked token must not be able to revoke
    the credentials of the account it was leaked from, which would turn one
    stolen key into a denial of service on every other agent that person runs.
    """
    refused = auth.guard_write(request, body.csrf_token)
    if refused is not None:
        return refused
    principal = auth.current_principal.get()
    if principal is None or principal.kind != "session" or not principal.user_id:
        return JSONResponse(
            {"error": "session_required",
             "detail": "Revoking a credential is done by a signed-in person."},
            status_code=403)

    ip, ua = auth._client_ip(request), auth._user_agent(request)
    with session() as s:
        row = s.get(models_auth.AuthServiceToken, token_id)
        bot = s.get(models_auth.AuthUser, row.owner_user_id) if row is not None else None
        # Not mine, not a bot's, or nonexistent — all the same 404.
        if row is None or bot is None or bot.person_user_id != principal.user_id:
            return JSONResponse(
                {"error": "not_found",
                 "detail": "No credential of yours with that id."}, status_code=404)
        if row.revoked_at is not None:
            return JSONResponse({"agent": bot.subject, "id": row.id,
                                 "revoked_at": row.revoked_at.isoformat(),
                                 "already": True})
        row.revoked_at = datetime.now(timezone.utc)
        auth._event(s, "token_revoked", user_id=bot.id, actor=principal.subject,
                    ip=ip, user_agent=ua, reason="self_service_revoke",
                    detail="agent=%s prefix=%s" % (bot.subject, row.prefix))
        s.commit()
        remaining = len(s.scalars(select(models_auth.AuthServiceToken).where(
            models_auth.AuthServiceToken.owner_user_id == bot.id,
            models_auth.AuthServiceToken.revoked_at.is_(None))).all())
        out = {
            "agent": bot.subject,
            "id": row.id,
            "revoked_at": row.revoked_at.isoformat(),
            "already": False,
            "live_credentials_left": remaining,
            # WHAT A REVOKE DID NOT DO. The account is the identity and the
            # token is the credential, so revoking the last one leaves the agent
            # in place, still linked, still a column in the access grid — and
            # still able to be given a new credential. Surprising unless stated.
            "agent_still_exists": True,
        }
    return JSONResponse(out)
