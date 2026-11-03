"""
GitHub identity and repository access (feat/github-link-repo-access, 2026-10-04).

WHAT THIS IS. A signed-in Atlas user proves they own a GitHub account by
signing in with GitHub (OAuth); Atlas stores the verified numeric id + login.
Linking grants NOTHING. The owner then approves repo access per account at
Settings -> Access & sharing, which makes Atlas send a collaborator invitation
for that login to ONE configured repository. Unticking removes the collaborator
or cancels the pending invitation. The person accepts on GitHub themselves.

The routes live in `app.py` (beside `GET /api/access`, whose owner-only +
`guard_write` + WRITE_SCOPE_ROUTES mechanism the approve route copies). This
module holds everything that is not HTTP-routing: the configuration, the state
token, and the GitHub calls.

SECRETS ARE FILES. Three files, named by environment variables, read on every
use through `settings.read_secret` (never at import, never copied into an env
var or a log; the plain `ATLAS_GITHUB_*` names without `_FILE` were never
read and still are not):

    ATLAS_GITHUB_OAUTH_CLIENT_ID_FILE      the OAuth app's client id
    ATLAS_GITHUB_OAUTH_CLIENT_SECRET_FILE  the OAuth app's client secret
    ATLAS_GITHUB_INVITE_TOKEN_FILE         a fine-grained PAT (or GitHub App
                                           installation token) with ONLY
                                           "Administration: read & write" on
                                           the one repository

and one plain setting, `ATLAS_GITHUB_REPO` (`owner/name`). If any file is
missing/empty or the repo is unset, `status()["configured"]` is False and the
feature is cleanly OFF: the routes answer 404 `github_not_configured`, and the
Settings panes hide the controls. Nothing here raises at import or at boot.

WHY A PERSONAL-ACCOUNT REPO MATTERS. Every collaborator on a personal repo gets
push; there is no read-only role. That is why identity is OAuth (a typed
username could be anyone's) and why the owner approves each one.

THE LOGIN IS NOT THE KEY. Logins can be renamed and a freed login can be taken
by someone else. The id is the key: before inviting or removing, `current_login`
re-resolves the login from the stored id (`GET /user/{id}`) so an invitation can
never go to whoever now holds an old name.

NO USER TOKEN IS RETAINED. The OAuth access token is used once, in memory, to
read `/user`, and dropped. It is never stored, logged or returned.

THE NETWORK IS ONE SEAM. Every request goes through `transport`; tests replace
it, so `verify_all.py` makes no network call (verify_github_link.py).
"""
from __future__ import annotations

import os
import re
import secrets
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import passwords
import settings

ENV_CLIENT_ID_FILE = "ATLAS_GITHUB_OAUTH_CLIENT_ID_FILE"
ENV_CLIENT_SECRET_FILE = "ATLAS_GITHUB_OAUTH_CLIENT_SECRET_FILE"
ENV_INVITE_TOKEN_FILE = "ATLAS_GITHUB_INVITE_TOKEN_FILE"
ENV_REPO = "ATLAS_GITHUB_REPO"

STATE_TTL_SECONDS = 600
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")

API = "https://api.github.com"
OAUTH = "https://github.com"


class GithubError(Exception):
    """A GitHub call failed or answered something unusable. Carries only a
    short, secret-free description; the HTTP status is for the log line."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def _read_file(env: str) -> Optional[str]:
    """One optional secret, through `settings.read_secret` (the one reader).

    `env` is the `*_FILE` variable; the secret's own name is it without the
    suffix. Missing, empty or unreadable is `None` — the feature is off. These
    secrets were only ever files, so the plain env var is not consulted.
    """
    return settings.read_secret(env[: -len("_FILE")], required=False, allow_env=False)


def repo() -> Optional[str]:
    # Read per call from os.environ, by the rule in settings.py (TASK-1433).
    return settings.load_github_repo()


def status() -> dict:
    """What the UI may be told. Names no path and no secret."""
    missing = []
    if _read_file(ENV_CLIENT_ID_FILE) is None:
        missing.append("oauth_client_id")
    if _read_file(ENV_CLIENT_SECRET_FILE) is None:
        missing.append("oauth_client_secret")
    if _read_file(ENV_INVITE_TOKEN_FILE) is None:
        missing.append("invite_token")
    if repo() is None:
        missing.append("repo")
    return {"configured": not missing, "repo": repo() if not missing else None,
            "missing": missing}


def configured() -> bool:
    return status()["configured"]


# --------------------------------------------------------------------------- #
# Transport — the one seam tests replace
# --------------------------------------------------------------------------- #
def _httpx_transport(method: str, url: str, headers: dict, form: dict | None,
                     json_body: dict | None) -> tuple[int, Any]:
    import httpx  # imported here: nothing at import time touches the network layer
    try:
        r = httpx.request(method, url, headers=headers, data=form, json=json_body,
                          timeout=10.0, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise GithubError("could not reach GitHub (%s)" % type(exc).__name__) from None
    try:
        body = r.json() if r.content else None
    except ValueError:
        body = None
    return r.status_code, body


#: transport(method, url, headers, form, json_body) -> (status, parsed json | None)
transport: Callable[..., tuple[int, Any]] = _httpx_transport


def _headers(token: str | None = None) -> dict:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "project-atlas",
         "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = "Bearer " + token
    return h


# --------------------------------------------------------------------------- #
# OAuth: state, authorize URL, code exchange, identity
# --------------------------------------------------------------------------- #
def make_state(session_id: str, pepper: bytes, now: float | None = None) -> str:
    """`nonce.expiry.mac`, the mac keyed by the token pepper and BOUND TO THE
    SESSION: a state minted for one browser session verifies for no other, so
    an attacker cannot plant their own GitHub callback in a victim's session
    (login CSRF). Stateless, so there is nothing to store or clean up."""
    exp = int((time.time() if now is None else now) + STATE_TTL_SECONDS)
    msg = "%s.%d" % (secrets.token_urlsafe(16), exp)
    return "%s.%s" % (msg, _mac(msg, session_id, pepper))


def _mac(msg: str, session_id: str, pepper: bytes) -> str:
    return passwords.digest_secret(msg + "|" + session_id,
                                   label="github-link-state-v1", key=pepper)


def verify_state(state: str, session_id: str, pepper: bytes,
                 now: float | None = None) -> bool:
    if not state or not session_id:
        return False
    parts = state.split(".")
    if len(parts) != 3:
        return False
    msg = parts[0] + "." + parts[1]
    if not passwords.constant_time_equals(parts[2], _mac(msg, session_id, pepper)):
        return False
    try:
        exp = int(parts[1])
    except ValueError:
        return False
    return (time.time() if now is None else now) <= exp


def authorize_url(redirect_uri: str, state: str) -> str:
    client_id = _read_file(ENV_CLIENT_ID_FILE)
    if client_id is None:
        raise GithubError("not configured")
    # No scope: login and numeric id are public profile data, so the access
    # token this yields can read nothing private.
    return OAUTH + "/login/oauth/authorize?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "state": state,
        "allow_signup": "false"})


def identity_from_code(code: str, redirect_uri: str) -> tuple[int, str]:
    """Exchange the code server-side, read `/user`, return `(id, login)`.
    The access token never leaves this function."""
    client_id = _read_file(ENV_CLIENT_ID_FILE)
    client_secret = _read_file(ENV_CLIENT_SECRET_FILE)
    if client_id is None or client_secret is None:
        raise GithubError("not configured")
    st, body = transport(
        "POST", OAUTH + "/login/oauth/access_token",
        {"Accept": "application/json", "User-Agent": "project-atlas"},
        {"client_id": client_id, "client_secret": client_secret, "code": code,
         "redirect_uri": redirect_uri}, None)
    token = body.get("access_token") if isinstance(body, dict) else None
    if st != 200 or not token:
        raise GithubError("GitHub did not accept the sign-in code", st)
    st, user = transport("GET", API + "/user", _headers(token), None, None)
    del token
    return _parse_user(st, user)


def _parse_user(st: int, user: Any) -> tuple[int, str]:
    if st != 200 or not isinstance(user, dict):
        raise GithubError("GitHub did not return the account", st)
    gid, login = user.get("id"), user.get("login")
    if (not isinstance(gid, int) or isinstance(gid, bool)
            or not isinstance(login, str) or not _LOGIN_RE.match(login)):
        raise GithubError("GitHub returned an unusable account", st)
    return gid, login


# --------------------------------------------------------------------------- #
# Repository access — every call uses the invite credential
# --------------------------------------------------------------------------- #
def _api(method: str, path: str, json_body: dict | None = None) -> tuple[int, Any]:
    token = _read_file(ENV_INVITE_TOKEN_FILE)
    if token is None or repo() is None:
        raise GithubError("not configured")
    return transport(method, API + path, _headers(token), None, json_body)


def current_login(github_id: int, stored_login: str) -> str:
    """The login that id holds NOW (renames), falling back to the stored one
    only when GitHub says the id no longer exists."""
    st, body = _api("GET", "/user/%d" % github_id)
    if st == 404:
        return stored_login
    return _parse_user(st, body)[1]


def invite(github_id: int, stored_login: str) -> dict:
    """Send (or confirm) a collaborator invitation.
    Returns `{"state": "invited"|"collaborator", "invitation_id": int|None,
    "login": str}`. `permission: push` is the only level a personal repo has."""
    login = current_login(github_id, stored_login)
    st, body = _api("PUT", "/repos/%s/collaborators/%s" % (repo(), login),
                    {"permission": "push"})
    if st == 201:
        inv = body.get("id") if isinstance(body, dict) else None
        return {"state": "invited", "invitation_id": inv if isinstance(inv, int) else None,
                "login": login}
    if st == 204:
        return {"state": "collaborator", "invitation_id": None, "login": login}
    raise GithubError("GitHub refused the invitation", st)


def revoke(github_id: int, stored_login: str, invitation_id: int | None) -> str:
    """Cancel a pending invitation and remove the collaborator. Both are
    attempted; a 404 on either means there was nothing to remove. Returns the
    login acted on."""
    login = current_login(github_id, stored_login)
    if invitation_id is not None:
        st, _ = _api("DELETE", "/repos/%s/invitations/%d" % (repo(), invitation_id))
        if st not in (204, 404):
            raise GithubError("GitHub would not cancel the invitation", st)
    st, _ = _api("DELETE", "/repos/%s/collaborators/%s" % (repo(), login))
    if st not in (204, 404):
        raise GithubError("GitHub would not remove the collaborator", st)
    return login


def observe(github_id: int, stored_login: str) -> dict:
    """What GitHub says now: `{"state": "collaborator"|"invited"|"none",
    "invitation_id": int|None, "login": str}`."""
    login = current_login(github_id, stored_login)
    st, _ = _api("GET", "/repos/%s/collaborators/%s" % (repo(), login))
    if st == 204:
        return {"state": "collaborator", "invitation_id": None, "login": login}
    if st != 404:
        raise GithubError("GitHub would not say", st)
    st, body = _api("GET", "/repos/%s/invitations?per_page=100" % repo())
    if st != 200 or not isinstance(body, list):
        raise GithubError("GitHub would not list invitations", st)
    for inv in body:
        invitee = inv.get("invitee") if isinstance(inv, dict) else None
        if isinstance(invitee, dict) and invitee.get("id") == github_id:
            iid = inv.get("id")
            return {"state": "invited", "invitation_id": iid if isinstance(iid, int) else None,
                    "login": login}
    return {"state": "none", "invitation_id": None, "login": login}
