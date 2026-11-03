"""
`local`: today's password login, expressed through the seam (STORY-oss-13).

THIS WRAPS THE PASSWORD CODE; IT DOES NOT REPLACE IT. `POST /auth/login` still
runs `auth._do_login` exactly as before (CSRF, the two throttles, the dummy verify
on a miss, the forced change, rotation), so the default install executes the same
statements it did before this story. What this class adds is the same three
answers in the provider shape, built from the same functions `_do_login` calls:

  * authenticate -> `passwords.normalise_username` + `passwords.verify_password`
  * lookup       -> the `idp='local' AND subject=<handle>` query `_do_login` runs
                    first (on a miss, and only with local AND oidc on, `_do_login`
                    then asks `identity.account_by_local_name`, TASK-1454)
  * provision    -> refused, always: local accounts are made by the operator
                    (`atlas-admin user add`, ADR 0025), never by a login.

`verify_identity_seam.py` holds the two to the same answers on a fixture, so they
cannot drift apart silently.

Local accounts are `idp='local'` by construction: the column is NOT NULL with a
server default of 'local' (models_auth.AuthUser.idp), so no row can lack it and
no backfill is needed. `is_local` still reads a NULL as local, should a hand-made
row ever carry one.
"""
from __future__ import annotations

from typing import Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

import passwords
from models_auth import AuthUser

from .base import Identity, LoginRefused

LOCAL_IDP = "local"


def is_local(user: AuthUser) -> bool:
    """Is this a local account? NULL counts as local (it cannot occur: NOT NULL)."""
    return (user.idp or LOCAL_IDP) == LOCAL_IDP


class LocalPasswordProvider:
    name = "local"
    kind = "password"

    def authenticate(self, s: Session, credentials: Mapping[str, str], *,
                     flow_binding: Optional[str] = None) -> Identity:
        handle = passwords.normalise_username(credentials.get("username", ""))
        user = self.lookup(s, Identity(provider=self.name, idp=LOCAL_IDP, subject=handle))
        if user is None or user.is_service or user.disabled_at is not None:
            passwords.verify_password(None, credentials.get("password", ""))  # real cost
            raise LoginRefused("unknown_user" if user is None else
                               "service_principal" if user.is_service else "disabled")
        result = passwords.verify_password(user.password_hash, credentials.get("password", ""))
        if not result.ok:
            raise LoginRefused("bad_password:%s" % result.reason)
        return Identity(provider=self.name, idp=LOCAL_IDP, subject=handle,
                        name=user.name, email=user.email)

    def lookup(self, s: Session, identity: Identity) -> Optional[AuthUser]:
        return s.scalar(select(AuthUser).where(
            AuthUser.idp == LOCAL_IDP, AuthUser.subject == identity.subject))

    def provision(self, s: Session, identity: Identity) -> AuthUser:
        raise LoginRefused("local_accounts_are_operator_made")
