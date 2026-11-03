"""
Identity providers (STORY-oss-13-identity-seam, ADR 0041 §5-§7).

OPT-IN. `ATLAS_AUTH_PROVIDERS` names the providers an install offers; the default
`local` is today, byte for byte: the password form, `POST /auth/login` as it was,
no OIDC route, no new table read. `verify_identity_seam.py` proves it.

    local          LocalPasswordProvider (identity/local.py): today's password
                   code, wrapped, never replaced.
    oidc           OidcProvider (identity/oidc.py): authorization code + PKCE.
    local,oidc     both; the login page shows the form and "Sign in with SSO".

THE REGISTRY. A provider is registered by name with a factory. `local` and
`oidc` are built in; `register()` adds another (a test registers a fake one and
logs in through the real routes, `verify_identity_seam.py` §C). The names in
ATLAS_AUTH_PROVIDERS are checked against the registry when this module is
imported, so an unknown one stops the boot, naming the variable.

WHAT THIS MODULE DECIDES for `auth.py`, each a no-op under the defaults:

  * `public_routes()`        the start/callback paths of each redirect provider,
                             added to `auth.PUBLIC_ROUTES` (empty by default);
  * `password_login_allowed` ATLAS_LOCAL_LOGIN=break-glass: only accounts flagged
                             with `atlas-admin user add --role owner --break-glass`
                             may use a password (`all`, the default, reads nothing);
  * `agent_tokens_suspended` ADR 0041 §6: an IdP-backed person's agent tokens stop
                             working ATLAS_OIDC_REVALIDATE_DAYS after their last
                             OIDC login, until they log in again. Read only for a
                             person whose `idp` is not `local`, so an install
                             without IdP accounts never reads the side table.
  * `oauth_enabled`          ADR 0041 §9 (STORY-oss-25): ATLAS_OAUTH_RESOURCE is set,
                             and the Bearer branch also accepts an IdP access token
                             (`identity/access_token.py`). False by default.
  * `step_up_required`       TASK-1415, ATLAS_OIDC_STEP_UP=on: an IdP-backed person
                             proves a fresh IdP sign-in, not a password, to create
                             their own agent credential. False by default and for
                             every local person, so the password prompt is unchanged.
  * `forced_change_applies`  THE rule for `must_change_password` (TASK-1442, TASK-1454):
                             it holds a credential established with a PASSWORD, and
                             never one the IdP vouched for. The session gate and
                             (TASK-1455) the IdP access token both ask this.
  * `linked_password_login_enabled`
                             TASK-1454: an IdP-backed person's personal password
                             signs in under their LOCAL NAME (`local_name` in the
                             side table) as a fallback for when the IdP is down.
                             Only with `local` AND `oidc` in ATLAS_AUTH_PROVIDERS;
                             ATLAS_LOCAL_LOGIN then says who (`all`: anyone with a
                             password; `break-glass`: flagged accounts only). False
                             by default, and then the login reads nothing new.

HOW A CREDENTIAL WAS ESTABLISHED (TASK-1454). Until then "a session is an OIDC one"
was read off the account (`idp != 'local'`), because a linked account could not sign
in with a password. Now it can, so the session row says it (`auth_session.auth_method`:
AUTH_PASSWORD, AUTH_SSO, AUTH_MINTED) and `forced_change_applies` takes it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

import settings
from models_auth import AuthUser, AuthUserIdpState

from .base import Identity, IdentityProvider, LoginRefused, RedirectProvider
from .local import LOCAL_IDP, LocalPasswordProvider, is_local

__all__ = [
    "Identity", "IdentityProvider", "RedirectProvider", "LoginRefused",
    "LocalPasswordProvider", "LOCAL_IDP", "is_local",
    "SETTINGS", "register", "configure", "providers", "provider", "redirect_providers",
    "public_routes", "password_form_offered", "password_login_allowed", "is_break_glass",
    "agent_tokens_suspended", "OAUTH", "oauth_enabled",
    "step_up_enabled", "step_up_required", "step_up_path", "step_up_provider",
    "AUTH_PASSWORD", "AUTH_SSO", "AUTH_MINTED", "forced_change_applies",
    "linked_password_login_enabled", "local_name_of", "account_by_local_name",
    "local_name_taken",
]

#: How a credential was established (`auth_session.auth_method`, TASK-1454).
AUTH_PASSWORD = "password"   # the password form, or the change that rotated such a session
AUTH_SSO = "sso"             # a redirect provider's callback; also an IdP access token
AUTH_MINTED = "minted"       # `atlas-admin session mint` (staging only)

#: name -> factory(IdentitySettings, context) -> provider. `context` carries what
#: a provider may need from auth without importing it (the keyed digest, the engine).
_FACTORIES: dict = {}


def register(name: str, factory: Callable) -> None:
    """Make provider `name` available to ATLAS_AUTH_PROVIDERS. Call before `configure`."""
    _FACTORIES[name] = factory


def _oidc_factory(cfg, context):
    from .oidc import OidcProvider   # imports PyJWT/httpx only when OIDC is configured
    return OidcProvider(cfg, state_digest=context["digest"], engine_getter=context["engine"])


register("local", lambda cfg, context: LocalPasswordProvider())
register("oidc", _oidc_factory)

#: The parsed settings. Read by `configure()`, which `auth` calls at import, so a
#: provider registered before the app is imported is a known name.
SETTINGS: settings.IdentitySettings = settings.IdentitySettings()
_PROVIDERS: dict = {}


#: ATLAS_OAUTH_* (STORY-oss-25): IdP access tokens at the API. Off by default.
OAUTH: settings.OAuthSettings = settings.OAuthSettings()


def configure(context: dict) -> settings.IdentitySettings:
    """Build the configured providers. `auth` calls this at import (a bad setting
    stops the boot). `context`: {"digest": raw -> hex, "engine": () -> Engine}."""
    global SETTINGS, _PROVIDERS, OAUTH
    SETTINGS = settings.load_identity(known=tuple(_FACTORIES))
    _PROVIDERS = {name: _FACTORIES[name](SETTINGS, context) for name in SETTINGS.providers}
    OAUTH = settings.load_oauth(identity=SETTINGS)
    return SETTINGS


def oauth_enabled() -> bool:
    """Does the API accept IdP access tokens (ATLAS_OAUTH_RESOURCE)? False by default,
    and then `auth` takes today's Bearer path without reading anything here."""
    return OAUTH.enabled


def providers() -> dict:
    return dict(_PROVIDERS)


def provider(name: str):
    return _PROVIDERS.get(name)


def redirect_providers() -> list:
    """The providers the browser is sent away to, in ATLAS_AUTH_PROVIDERS order."""
    return [p for p in _PROVIDERS.values() if getattr(p, "kind", None) == "redirect"]


def start_path(name: str) -> str:
    return "/auth/%s/start" % name


def callback_path(name: str) -> str:
    return "/auth/%s/callback" % name


def step_up_path(name: str) -> str:
    """Where a signed-in person starts a step-up (TASK-1415). NOT public: the gate
    requires a session before the route runs, and the route refuses any other kind."""
    return "/auth/%s/step-up" % name


def step_up_enabled() -> bool:
    """ATLAS_OIDC_STEP_UP=on (settings refuses it without the oidc provider)."""
    return SETTINGS.oidc_enabled and SETTINGS.oidc.step_up


def step_up_provider():
    """The provider a step-up goes through, or None while step-up is off."""
    return _PROVIDERS.get("oidc") if step_up_enabled() else None


def step_up_required(person: AuthUser) -> bool:
    """Does this person re-authenticate at the IdP instead of with a password?

    Only an IdP-backed person, and only with step-up on. A local account (the
    homelab's every account, and a break-glass owner) always answers with its
    password, exactly as before. An IdP-backed account that still holds a local
    verifier (kept by `link-idp`) is asked for the IdP sign-in too: the IdP is
    where that person is now authenticated, and offboarding happens there.
    """
    return step_up_enabled() and not is_local(person)


def public_routes() -> frozenset:
    """The unauthenticated paths the enabled redirect providers need. Empty by default.

    An addition to `auth.PUBLIC_ROUTES` needs an ADR note (auth.py §2): this one is
    ADR 0041 §5, and it exists only while a redirect provider is configured.
    """
    out = set()
    for p in redirect_providers():
        out.add(start_path(p.name))
        out.add(callback_path(p.name))
    return frozenset(out)


def password_form_offered() -> bool:
    """Is the password form shown on /auth/login? Today: yes. With break-glass, or
    without `local`, it is reachable only at the unlinked /auth/login?break_glass=1."""
    return "local" in SETTINGS.providers and not SETTINGS.break_glass_only


def is_break_glass(s: Session, user: AuthUser) -> bool:
    state = s.get(AuthUserIdpState, user.id)
    return bool(state is not None and state.break_glass)


def password_login_allowed(s: Session, user: AuthUser) -> bool:
    """ATLAS_LOCAL_LOGIN: `all` (default) reads nothing and allows every local account."""
    if not SETTINGS.break_glass_only:
        return True
    return is_break_glass(s, user)


def forced_change_applies(user: AuthUser, method: Optional[str]) -> bool:
    """Does `must_change_password` hold a credential of `user` established by `method`?

    THE SOURCE OF TRUTH for the forced-change interlock (auth.py §5). The flag means
    "the password this account signs in with is a temporary one", so it holds a
    PASSWORD sign-in and never one the IdP vouched for (TASK-1442):

      * a local account: always, whatever `method` says. It has no IdP, so no
        credential of it was ever vouched for by one; the homelab's rule, unchanged.
      * an IdP-backed account: unless `method` is AUTH_SSO. A password sign-in under
        its local name (TASK-1454) is held; so is a minted session, or a method this
        function does not know, because failing closed costs a change screen and
        failing open costs the interlock.

    `method` is what the credential row recorded; `auth.session_auth_method` reads
    a session's (a row older than the column means what it meant then).
    """
    if not user.must_change_password:
        return False
    if is_local(user):
        return True
    return method != AUTH_SSO


def linked_password_login_enabled() -> bool:
    """TASK-1454: may an IdP-backed person sign in with a password under their local name?

    Only with both `local` and `oidc` in ATLAS_AUTH_PROVIDERS. ATLAS_LOCAL_LOGIN then
    narrows it exactly as for a local account (`password_login_allowed`): `all`, any
    such account with a password; `break-glass`, a flagged one only. With `oidc`
    alone (break-glass implied) or `local` alone, False: no linked account signs in
    with a password, as before this task, and the login reads nothing new.
    """
    return SETTINGS.oidc_enabled and "local" in SETTINGS.providers


def local_name_of(s: Session, user: AuthUser) -> Optional[str]:
    """The local sign-in name of an IdP-backed person, or None (always None for a
    local account, whose name is its `subject`; no read for one)."""
    if is_local(user):
        return None
    state = s.get(AuthUserIdpState, user.id)
    return state.local_name if state is not None else None


def account_by_local_name(s: Session, handle: str) -> Optional[AuthUser]:
    """The IdP-backed account whose local sign-in name is `handle` (normalised), or None.

    Asked by the password login only after the `idp='local'` lookup missed, so a
    local account always wins its own name."""
    if not handle:
        return None
    return s.scalar(select(AuthUser).join(
        AuthUserIdpState, AuthUserIdpState.user_id == AuthUser.id).where(
        AuthUserIdpState.local_name == handle, AuthUser.idp != LOCAL_IDP,
        AuthUser.is_service.is_(False)))


def local_name_taken(s: Session, name: str, *, except_user_id: Optional[str] = None) -> Optional[str]:
    """Why `name` cannot be a local sign-in name (or a new local username), or None.

    One namespace: local usernames, agent labels (`idp='local'` rows, people and
    bots alike) and the local names of linked accounts. Reads the side table only
    when an IdP-backed person exists, so an install without any reads nothing new.
    """
    holder = s.scalar(select(AuthUser).where(AuthUser.idp == LOCAL_IDP, AuthUser.subject == name))
    if holder is not None and holder.id != except_user_id:
        return "%r is a local %s" % (name, "agent" if holder.is_service else "account")
    if s.scalar(select(AuthUser.id).where(AuthUser.idp != LOCAL_IDP,
                                          AuthUser.is_service.is_(False)).limit(1)) is None:
        return None
    other = s.scalar(select(AuthUserIdpState.user_id).where(AuthUserIdpState.local_name == name))
    if other is not None and other != except_user_id:
        return "%r is the local sign-in name of another linked account" % name
    return None


def revalidate_days() -> int:
    return SETTINGS.oidc.revalidate_days


def agent_tokens_suspended(s: Session, person: AuthUser, *, now: Optional[datetime] = None) -> bool:
    """ADR 0041 §6: has this IdP-backed person gone too long without an OIDC login?

    False for every local person without a read. For an IdP-backed one: no
    recorded OIDC login, or the last one older than ATLAS_OIDC_REVALIDATE_DAYS,
    suspends their agents' tokens (not revoked: the next login lifts it).
    """
    if is_local(person):
        return False
    state = s.get(AuthUserIdpState, person.id)
    last = state.last_idp_login_at if state is not None else None
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return now - last > timedelta(days=revalidate_days())
