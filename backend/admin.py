"""
Atlas accounts — the operator's command line (TASK-970).

This is the whole account-administration surface. There is no invite flow, no
self-registration route, no reset link and no second approver: **the operator is
the trust root**, and every verb here is one the operator runs from a shell on
the server. That is the ADR 0025 decision, not an interim state, and the
proportionate control for it is that every action leaves a row in `auth_event`
naming the operator.

    docker exec -it atlas-api python -m admin user add alice --role member

The image also puts this module on PATH as `atlas-admin` (STORY-oss-07), so the first
owner of a fresh install is

    docker exec -it atlas-api atlas-admin user add <username> --role owner

`user add` creates the auth tables first if they are missing (idempotent, as `init-db`), so
that first account can be minted whether or not the app has booted on the database yet.

(The container's WORKDIR is `/app` with every backend module flat inside it, so
the in-container module path is `admin`, not `backend.admin`. From a checkout,
`python -m backend.admin …` works too — the `sys.path` shim below makes both
spellings resolve the same flat sibling modules that `app.py` imports.)

────────────────────────────────────────────────────────────────────────────────
1. THE OPERATOR CANNOT CHOOSE A PASSWORD (ASVS 6.4.6)
────────────────────────────────────────────────────────────────────────────────
There is no flag anywhere in this file that accepts a credential value, and
there is exactly ONE code path that produces one:
`passwords.generate_temp_password()` — `secrets.token_urlsafe(16)`, 128 bits.
`_mint_temp_credential()` below is the only caller, and both `user add` and
`user reset-password` route through it (reset is literally add minus the
insert). `token add` is the same shape for the machine half:
`auth.mint_service_token()` generates, this file only relays.

`user add --password …` is the single most likely convenience someone adds at
23:00, and it converts an ASVS-compliant system into one where the operator
knows every user's password. If you are here to add it: don't. Read TASK-970
§"The CLI surface".

────────────────────────────────────────────────────────────────────────────────
2. Shown once, then unrecoverable
────────────────────────────────────────────────────────────────────────────────
A minted secret is printed by `_announce_secret()` and then dropped — except a
service token, which since 2026-09-15 goes only where `token add --out`/`--print`
says, because its usual caller is an agent whose stdout is a transcript. It is
hashed at rest in the same `password_hash` column as any other password, so
there is no code path anywhere — not `user show`, not `user list`, not the API —
that can reveal it afterwards. That is what makes "the operator only relays it"
substantially true rather than aspirational.

**Nothing here ever prints a password hash, a raw session id, or a stored token
digest.** `user show` reports `password_set: yes|no`; `token list` reports the
clear `prefix` (which is a lookup handle, not a secret) and never `token_hash`.

Handover is out of band and NOT on the same channel as the username: a KeePassXC
entry passed in person or over Signal, never a chat line that persists. Say
which channel was used in the ticket's Result — identity proofing at five users
is a phone call, not a workflow.

────────────────────────────────────────────────────────────────────────────────
3. Reset and disable are different verbs and behave differently
────────────────────────────────────────────────────────────────────────────────
    reset-password ->  sessions die, service tokens LIVE
    disable        ->  sessions die AND every token owned by the user stops

A user forgetting their browser password says nothing about a machine
credential. If `reset-password` revoked `atlas_svc_` tokens as a tidy-up, every
scripted caller starts getting 401s — and the failure this repo has actually
lived is the silent kind: the bridge script that once ran here printed
`done — board is serving up to …` and exited 0 on a 401 while the board served
stale rows and `/api/health` stayed green. (That script is deleted as of
TASK-1057; the lesson it taught is why both halves are still asserted in both
directions by the acceptance criteria.)

`disable` does NOT stamp `revoked_at` on the user's tokens, and that is
deliberate: `auth._resolve_token()` re-reads the owning `auth_user` row on every
request and refuses a token whose owner is disabled, so revocation is immediate
with no container restart — and `user enable` restores the machine half without
the operator having to re-mint every credential. Revocation of an *individual*
token is `token revoke`, which is permanent.

`disabled_at`, never a row DELETE. `auth_user.id` is an opaque uuid4 that is
never reissued precisely so a revoked user's id cannot be handed to the next
account and inherit their grants when per-node visibility lands (TASK-972 §1).

────────────────────────────────────────────────────────────────────────────────
4. What this file deliberately does NOT contain
────────────────────────────────────────────────────────────────────────────────
`invite` and `reconcile`. Both existed to keep two allow-lists (Cloudflare
Access + Atlas) from drifting; after ADR 0025 there is exactly one list, so the
drift problem is eliminated rather than solved. Do not reintroduce either.

Nothing here talks to Cloudflare. Retiring the Access application is a dashboard
action with its own ordering hazard (delete it only AFTER the gate is live) and
is not scriptable from this repo.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

# `app.py`, `auth.py` and `passwords.py` all import their siblings FLAT
# (`import models_auth`), because the container has them side by side in /app.
# Putting this directory on the path first means `python -m backend.admin` from
# a checkout, `python -m admin` inside the container and `python admin.py` from
# `backend/` all resolve the same modules — and, importantly, the SAME module
# objects the app itself loads, so there is no second copy of the gate's state.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


# --------------------------------------------------------------------------- #
# `atlas-admin backup …` reads no sign-in setting (OSS-26 L, TASK-1437).
#
# `auth`, imported below for the account verbs, configures the identity
# providers AT IMPORT: with ATLAS_AUTH_PROVIDERS naming oidc it reads the OIDC
# group map and client secret (or key) from the files ATLAS_OIDC_*_FILE name,
# and stops on one it cannot read. The `atlas-backup` container reads the same
# `.env` as atlas-api (env_file), so it gets those settings too, while the
# files themselves are mounted into atlas-api only (deploy/oss/oidc.yml). The
# result was that every `atlas-admin backup` verb in `atlas-backup` failed at
# import on an install with SSO on, before it touched the database.
#
# The backup verbs (`cmd_backup_*`) use nothing from identity: they dump,
# verify, restore and roll back through crunch_backup / backup_restore. So for
# that verb group only, run as a program, the identity settings are dropped
# from THIS process's environment before `auth` is imported, and auth comes up
# as the default (`local`) and opens no OIDC file. Chosen over mounting the
# OIDC credentials into atlas-backup: a container that never signs anyone in
# should not hold the IdP client secret, and this works whichever overlay an
# install mounts its secrets with. atlas-api and every other verb are
# unchanged: atlas-api never runs this module as __main__, and `user …`,
# `token …` and the rest still configure identity exactly as before.
# The names, and the drop itself, are shared with `atlas-migrate` (identity_env.py).
from identity_env import drop_identity_env as _drop_identity_env  # noqa: E402


def _verb_group(argv: Sequence[str]) -> Optional[str]:
    """The first positional argument (`user`, `backup`, …), skipping the global
    options. `--actor` is the only global option that takes a value."""
    args = iter(argv)
    for arg in args:
        if arg == "--actor":
            next(args, None)
        elif not arg.startswith("-"):
            return arg
    return None


if __name__ == "__main__" and _verb_group(sys.argv[1:]) == "backup":
    _drop_identity_env(os.environ)

from sqlalchemy import create_engine, delete, select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

try:  # noqa: E402 - the path shim above has to run first
    import models_auth
    import passwords
    import auth
    import identity
    import dsn
    import settings
    from models_auth import (
        AuthServiceToken,
        AuthSession,
        AuthUser,
        resolve_role,
    )
except RuntimeError as exc:  # pragma: no cover - a config fault, not a bug
    # `passwords.MissingPepperError` and `auth.MissingSecretError` both subclass
    # RuntimeError and both fire AT IMPORT, naming the env var that is missing
    # (ADR 0015, fail closed). Catching them only reformats the message so the
    # operator gets one line instead of a traceback — the refusal itself stands,
    # and there is no generated-if-missing fallback here or anywhere.
    print("atlas admin: %s" % exc, file=sys.stderr)
    raise SystemExit(2) from None


# --------------------------------------------------------------------------- #
# Audit. Reuse the gate's writer rather than growing a second one.
#
# `auth._event()` runs `_clean()` over every free-text field before it is
# stored: a username or a label reaching this file is operator-supplied but a
# label can also be pasted from somewhere hostile, and a raw newline in `reason`
# or `detail` is log injection (ASVS 16.2.5 / TASK-966 §auth_event). Writing the
# rows by hand here would duplicate that encoding, and duplicated encoding is
# encoding that drifts.
# --------------------------------------------------------------------------- #
_event = auth._event      # noqa: SLF001 - deliberate reuse; see above
_clean = auth._clean      # noqa: SLF001

#: `models_auth.EVENTS` is the vocabulary, and every name below is now in it.
#: (`user_enabled` was not, for weeks: the column carries no CHECK by design, so
#: writing an absent name is safe and the list is what needed the one-line
#: addition. It got it alongside the two link events — TASK-1112.)
EVENT_USER_CREATED = "user_created"
EVENT_USER_DISABLED = "user_disabled"
EVENT_USER_ENABLED = "user_enabled"
EVENT_PASSWORD_RESET = "password_reset_by_operator"
EVENT_TOKEN_REVOKED = "token_revoked"
EVENT_BOT_LINKED = "bot_person_linked"
EVENT_BOT_UNLINKED = "bot_person_unlinked"

#: The handle rule lives in `passwords.py` now (TASK-1115): the self-service
#: credential route creates accounts too, and two copies of a validation rule is
#: a rule that will eventually disagree with itself.

_MAX_MINT_ATTEMPTS = 8


class AdminError(Exception):
    """Anything the operator can fix by re-running with different arguments."""


# --------------------------------------------------------------------------- #
# Engine + small formatting helpers
# --------------------------------------------------------------------------- #

def _db_url() -> str:
    """The same env var as `app.py`, read as a secret (it carries the DB password).

    `ATLAS_DB_URL_FILE` wins; the plain `ATLAS_DB_URL` is honoured as before
    unless ATLAS_REQUIRE_SECRET_FILES=1 (settings.py, STORY-oss-03). Neither set
    keeps the old local default (outside strict mode). `ATLAS_DB_URL` SET but
    empty or whitespace fails, naming the variable, rather than selecting that
    default in silence (TASK-1348).

    No `--db-url` flag: two ways to name the database is how an operator resets
    a password on staging and wonders why live still refuses the old one.
    """
    try:
        url = settings.read_secret(
            "ATLAS_DB_URL", default="postgresql+psycopg2://atlas:atlas@atlas-postgres:5432/atlas"
        )
        # TASK-1501: a URL with no driver (`postgresql://`) gets psycopg2; see dsn.py.
        return dsn.with_default_driver(url)
    except (settings.SecretError, dsn.DsnError) as exc:
        # Configured but refused or unreadable: say so, never fall back to the default.
        raise SystemExit(str(exc)) from None


def _engine():
    return create_engine(_db_url(), pool_pre_ping=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: Optional[datetime]) -> Optional[datetime]:
    """Normalise to UTC-aware before any comparison (Postgres already returns
    aware values for a `DateTime(timezone=True)` column — this is a defensive
    no-op on the one dialect this runs against, not a dialect workaround)."""
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _fmt_time(moment: Optional[datetime]) -> str:
    aware = _aware(moment)
    return "-" if aware is None else aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(moment: Optional[datetime]) -> Optional[str]:
    aware = _aware(moment)
    return None if aware is None else aware.astimezone(timezone.utc).isoformat()


def _yn(value: object) -> str:
    return "yes" if value else "no"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()
    out = [line, "  ".join("-" * widths[i] for i in range(len(headers))).rstrip()]
    for row in rows:
        out.append("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(out)


def _operator(args: argparse.Namespace) -> str:
    """Who is running this. Lands in `auth_event.actor` on every admin verb.

    Not an authorization decision — anyone with a shell in the container is
    already the trust root — purely provenance, so the audit trail can answer
    "who reset that password" six weeks later.
    """
    if getattr(args, "actor", None):
        return str(args.actor)
    from_env = os.environ.get("ATLAS_ADMIN_ACTOR", "").strip()
    if from_env:
        return from_env
    try:
        return getpass.getuser() or "unknown"
    except Exception:  # noqa: BLE001 - no controlling terminal, no passwd entry
        return "unknown"


# --------------------------------------------------------------------------- #
# Lookup
# --------------------------------------------------------------------------- #

def _find_user(s: Session, handle: str, *, service: Optional[bool] = None) -> AuthUser:
    subject = passwords.normalise_username(handle)
    user = s.scalar(select(AuthUser).where(
        AuthUser.idp == "local", AuthUser.subject == subject
    ))
    if user is None:
        # An IdP-backed account (STORY-oss-13) has no username: its subject is the
        # IdP's opaque id. Name it `<idp>:<subject>` or by its email. Neither form
        # can shadow a local handle (HANDLE_RE allows no `:` and no `@`), so every
        # local lookup above is unchanged.
        user = _find_idp_user(s, handle.strip())
    if user is None and subject and s.scalar(select(AuthUser.id).where(
            AuthUser.idp != "local", AuthUser.is_service.is_(False)).limit(1)) is not None:
        # A linked account's local sign-in name (TASK-1454). Asked only after
        # both lookups above missed, so it can never shadow a local account, and
        # only where an IdP-backed person exists, so an install without one
        # reads nothing new.
        user = identity.account_by_local_name(s, subject)
    if user is None:
        raise AdminError("no account with username %r (`user list` shows them all)" % subject)
    if service is True and not user.is_service:
        raise AdminError("%r is a person, not a bot; service tokens belong to `bot add` rows" % subject)
    if service is False and user.is_service:
        raise AdminError("%r is a bot row: it holds no password and cannot sign in" % subject)
    return user


def _find_idp_user(s: Session, handle: str) -> Optional[AuthUser]:
    idp, sep, subject = handle.partition(":")
    if sep and idp and subject and idp != "local":
        return s.scalar(select(AuthUser).where(AuthUser.idp == idp, AuthUser.subject == subject))
    if "@" in handle:
        rows = s.scalars(select(AuthUser).where(
            AuthUser.idp != "local", AuthUser.is_service.is_(False),
            AuthUser.email == handle)).all()
        if len(rows) > 1:
            raise AdminError("%d IdP accounts carry the email %r; name one as <idp>:<subject> "
                             "(`user list` shows them)" % (len(rows), handle))
        return rows[0] if rows else None
    return None


def _display(user: AuthUser) -> str:
    """How a command names an account: the username, or `<idp>:<subject>`."""
    return user.subject if (user.idp or "local") == "local" else "%s:%s" % (user.idp, user.subject)


def _validated_handle(raw: str) -> str:
    """The shared rule, reported as an `AdminError` so the CLI still prints one
    line instead of a traceback."""
    try:
        return passwords.validated_handle(raw)
    except ValueError as exc:
        raise AdminError(str(exc)) from None


def _person_of(s: Session, user: AuthUser) -> Optional[AuthUser]:
    """The person an agent belongs to (ADR 0034), or None.

    None covers two different states on purpose and the caller renders them the
    same way: no link recorded, and a link naming an account that is not there.
    Neither grants anything, so neither is worth a distinct symbol in a table —
    but `user show --json` still reports the stored `person_user_id`, so a
    dangling link is diagnosable rather than invisible.
    """
    if not user.person_user_id:
        return None
    return s.get(AuthUser, user.person_user_id)


def _agents_of(s: Session, person: AuthUser) -> list[AuthUser]:
    """Every agent account linked to this person, in username order.

    Empty for a bot (a bot has no agents of its own) and for anybody nobody has
    linked, which is what keeps a person's disable from touching the four
    pre-existing bots.
    """
    if person.is_service:
        return []
    return list(s.scalars(
        select(AuthUser).where(AuthUser.person_user_id == person.id)
        .order_by(AuthUser.subject)
    ).all())


def _live_token_count(s: Session, user_id: str) -> int:
    return len(s.scalars(select(AuthServiceToken.id).where(
        AuthServiceToken.owner_user_id == user_id,
        AuthServiceToken.revoked_at.is_(None),
    )).all())


def _kill_sessions(s: Session, user_id: str) -> int:
    """Delete every session row for a user. Effective on their next request.

    Deleting sessions WITHOUT disabling the account just means they sign in
    again with the password they still know — which is why `reset-password`
    replaces the verifier in the same transaction and `disable` stamps
    `disabled_at`.
    """
    result = s.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
    return int(result.rowcount or 0)


# --------------------------------------------------------------------------- #
# THE ONLY CODE PATH IN THIS FILE THAT PRODUCES A CREDENTIAL VALUE
# --------------------------------------------------------------------------- #

def _mint_temp_credential(user: AuthUser) -> str:
    """Generate a handover secret, stamp the row, return the value ONCE.

    `secrets.token_urlsafe(16)` — 128 bits, 22 URL-safe characters, so it clears
    the 15 code point minimum by construction and needs no special case. Shared
    by `user add` and `user reset-password`; reset is the same code path as add,
    minus the insert, which is the entire reason there is no second generator.

    Deliberately NOT breach-screened: `check_password_policy(breach_check=True)`
    would put an outbound HIBP call in the mint path, and a third-party outage
    must never block the operator creating an account (passwords.py §4). A
    128-bit CSPRNG value cannot appear in a breach corpus. `hash_password()`
    still applies the structural policy as a backstop, and the retry loop covers
    the astronomically unlikely case where a random token happens to contain a
    blocked context word.

    The returned value is not stored, not logged and not retained: the caller
    prints it and drops it.
    """
    for _ in range(_MAX_MINT_ATTEMPTS):
        secret = passwords.generate_temp_password()
        try:
            user.password_hash = passwords.hash_password(secret)
        except passwords.PasswordPolicyError:
            continue  # regenerate; never fall back to a weaker value
        user.hash_scheme = passwords.HASH_SCHEME
        # Forced change is enforced AT THE GATE, re-read from this row on every
        # request (auth.py §5). Never a claim in a cookie.
        user.must_change_password = True
        user.temp_expires_at = passwords.temp_credential_expires_at()
        user.password_changed_at = None
        return secret
    raise AdminError("could not generate a policy-clean temp credential; re-run")  # pragma: no cover


def _announce_secret(kind: str, handle: str, secret: str, *, expires: Optional[datetime] = None,
                     note: str = "", lifetime: str = "") -> None:
    """Print a generated secret EXACTLY ONCE, and say so.

    Nothing else in this file — and nothing in the app — can reveal it again.
    """
    print()
    print("  %s for %s" % (kind, handle))
    print("  " + "-" * (len(kind) + len(handle) + 5))
    print()
    print("      %s" % secret)
    print()
    # The LIFETIME SENTENCE IS THE CALLER'S, not this function's. It used to be
    # hardcoded to the temp-password rules — "(72h), single use: it dies the
    # moment it successfully signs in once" — which this function then printed
    # over every secret it was given. A minted browser session is neither 72h
    # nor single-use, so the block cheerfully told the operator two false things
    # about the credential in their hand. A secret announcer that describes the
    # wrong secret is worse than one that says nothing.
    if lifetime:
        for line in lifetime.splitlines():
            print("  %s" % line)
    elif expires is not None:
        print("  Valid until %s." % _fmt_time(expires))
    if note:
        print("  %s" % note)
    print("  SHOWN ONCE. It is hashed at rest and cannot be printed again - if it")
    print("  scrolls away, re-run the command and hand over the new value.")
    print("  Hand it over out of band (KeePassXC entry, in person, Signal) and NOT")
    print("  on the same channel as the username.")
    print()


# --------------------------------------------------------------------------- #
# user add / reset-password / disable / enable / list / show
# --------------------------------------------------------------------------- #

def cmd_user_add(args: argparse.Namespace) -> int:
    handle = _validated_handle(args.username)
    operator = _operator(args)
    break_glass = bool(getattr(args, "break_glass", False))
    if break_glass and (resolve_role(args.role) != models_auth.ROLE_OWNER
                        or getattr(args, "no_password", False)):
        # ADR 0041 §7: a break-glass account exists to administer the install when
        # the IdP is down, so it is an owner with a password, or it is nothing.
        raise AdminError("--break-glass needs --role owner and a password (not --no-password)")
    # --out PATH (TASK-1470): the temporary password goes to a new 0600 file and stdout keeps
    # only the lines that are no secret, as `token mint --out` does. Without it, the default
    # is unchanged: the password is shown once on this terminal. Refused BEFORE anything is
    # created, so a bad destination leaves no account behind.
    out = getattr(args, "out", None)
    if out is not None:
        if out == "-" or not out:
            raise AdminError("user add --out takes a file: --out PATH (a new 0600 file). "
                             "Leave --out off to have the password shown here instead.")
        if getattr(args, "no_password", False):
            raise AdminError("--out has nothing to write with --no-password")
        if os.path.exists(out):
            raise AdminError("%s already exists; refusing to overwrite a credential file" % out)
    engine = _engine()
    # First-admin bootstrap (STORY-oss-07): on a database the app has never booted on, the
    # auth tables do not exist yet. Create them if missing, exactly as `init-db` does
    # (CREATE TABLE only for an absent table; nothing is dropped or altered).
    models_auth.create_all(engine)
    with Session(engine) as s:
        existing = s.scalar(select(AuthUser).where(
            AuthUser.idp == "local", AuthUser.subject == handle
        ))
        if existing is not None:
            raise AdminError(
                "%r already exists (created %s). Accounts are never recreated - use "
                "`user reset-password %s` to hand over a new credential, or "
                "`user enable %s` if it is disabled."
                % (handle, _fmt_time(existing.created_at), handle, handle)
            )
        taken = identity.local_name_taken(s, handle)
        if taken is not None:
            # One namespace with the linked accounts' local sign-in names (TASK-1454).
            raise AdminError("%s; pick another username (`user list` shows them)" % taken)
        user = AuthUser(
            id=str(uuid.uuid4()),
            idp="local",
            subject=handle,
            email=_clean(args.email, 320),
            name=_clean(args.name, 120),
            role=resolve_role(args.role),
            is_service=False,
            created_at=_now(),
        )
        # --no-password: a session-capable account with NO credential at all.
        #
        # `ck_auth_user_service_has_no_password` is `NOT is_service OR
        # password_hash IS NULL` — it forbids a password on a SERVICE row, and
        # says nothing about a human one being empty. So this row is a normal
        # human principal (sessions resolve for it) that can never pass the
        # login form: `passwords.verify_password(None, ...)` runs the dummy
        # verify and returns `no_credential`, always. The only way in is
        # `session mint`, which is itself off outside staging.
        #
        # That is the point. A shared test account with a standing password is
        # the thing that leaks; this one has no password to leak.
        secret = None
        if getattr(args, "no_password", False):
            user.password_hash = None
            user.must_change_password = False   # nothing to change
        else:
            secret = _mint_temp_credential(user)
        s.add(user)
        if break_glass:
            # The flag lives in the side table (ADR 0041 Adoption §1), never on auth_user.
            s.add(models_auth.AuthUserIdpState(user_id=user.id, break_glass=True))
        _event(s, EVENT_USER_CREATED, user_id=user.id, actor=operator,
               reason="role=%s%s%s" % (user.role, ":no_password" if secret is None else "",
                                       ":break_glass" if break_glass else ""),
               detail="username=%s" % handle)
        if out is not None:
            # Write the file BEFORE committing, as `token mint` does: if it cannot be
            # written, no account exists either.
            try:
                _deliver_token(secret, out)
            except OSError as exc:
                s.rollback()
                raise AdminError("could not create %s (%s); no account was created"
                                 % (out, type(exc).__name__)) from None
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            raise AdminError("%r was created by someone else while this ran" % handle) from None
        # Read what is printed WHILE the session is open: `expire_on_commit`
        # sends every attribute back to the database, and the instance is
        # detached once the block exits.
        user_id, role, expires = user.id, user.role, _aware(user.temp_expires_at)

    print("created %s  id=%s  role=%s%s" % (handle, user_id, role,
                                            "  break-glass" if break_glass else ""))
    if break_glass:
        print("  BREAK-GLASS: this password signs in while ATLAS_LOCAL_LOGIN=break-glass, at the")
        print("  unlinked /auth/login?break_glass=1, and every use is a `login_break_glass`")
        print("  auth_event. Keep it in the enterprise vault, not with a person.")
    if secret is None:
        print("  It holds NO password and can never pass the login form.")
        print("  Give it a browser session with:")
        print("      session mint %s" % handle)
        return 0
    lifetime = ("Valid until %s (%dh), single use: it dies the moment it "
                "successfully signs in once, whether or not the change is finished."
                % (_fmt_time(expires), passwords.TEMP_CREDENTIAL_TTL_HOURS))
    if out is not None:
        del secret
        print("  Temporary password written to %s (0600), and nowhere else." % out)
        print("  %s" % lifetime)
        print("  Hand it over out of band and NOT on the same channel as the username;")
        print("  then delete the file. Lost: `user reset-password %s` issues a new one." % handle)
        return 0
    _announce_secret("Temporary password", handle, secret, expires=expires, lifetime=lifetime)
    return 0


def cmd_user_reset_password(args: argparse.Namespace) -> int:
    operator = _operator(args)
    with Session(_engine()) as s:
        user = _find_user(s, args.username, service=False)
        secret = _mint_temp_credential(user)
        killed = _kill_sessions(s, user.id)
        # Service tokens are LEFT STANDING on purpose — see module docstring §3.
        tokens = _live_token_count(s, user.id)
        _event(s, EVENT_PASSWORD_RESET, user_id=user.id, actor=operator,
               reason="operator_reset",
               detail="username=%s sessions_deleted=%d tokens_left=%d"
                      % (user.subject, killed, tokens))
        s.commit()
        handle = user.subject
        expires = _aware(user.temp_expires_at)
        still_disabled = user.disabled_at is not None

    print("reset %s  sessions deleted: %d  service tokens left standing: %d"
          % (handle, killed, tokens))
    if still_disabled:
        print("  NOTE: this account is still disabled - `user enable %s` to let it sign in."
              % handle)
    _announce_secret("Temporary password", handle, secret, expires=expires,
                     lifetime=("Valid until %s (%dh), single use: it dies the moment it "
                               "successfully signs in once, whether or not the change is finished."
                               % (_fmt_time(expires), passwords.TEMP_CREDENTIAL_TTL_HOURS)))
    return 0


def cmd_user_disable(args: argparse.Namespace) -> int:
    operator = _operator(args)
    with Session(_engine()) as s:
        user = _find_user(s, args.username)
        if user.disabled_at is not None:
            print("%s was already disabled at %s" % (user.subject, _fmt_time(user.disabled_at)))
            return 0
        # Only meaningful when the account being revoked is itself an owner:
        # disabling the last one leaves nobody with the owner role, which is
        # recoverable from this shell but worth saying out loud first.
        last_owner = resolve_role(user.role) == models_auth.ROLE_OWNER and not s.scalars(
            select(AuthUser.id).where(
                AuthUser.role == models_auth.ROLE_OWNER,
                AuthUser.is_service.is_(False),
                AuthUser.disabled_at.is_(None),
                AuthUser.id != user.id,
            )
        ).all()
        user.disabled_at = _now()
        killed = _kill_sessions(s, user.id)
        # NOT `revoked_at` on the tokens: the gate re-reads the owner row on
        # every request and refuses a disabled owner's token immediately, with
        # no restart, and `user enable` then restores the machine half without
        # re-minting. `token revoke` is the permanent, per-credential lever.
        tokens = _live_token_count(s, user.id)

        # OFFBOARDING HAS TO REACH MACHINES (ADR 0034 / TASK-1113). An agent's
        # token hangs off its OWN bot row, so disabling a person used to end
        # their sessions and leave their agent reading the board. That criterion
        # ("revocation is one command, and it reaches machines too") passed only
        # because every bot on live belonged to one person.
        #
        # DISABLED HERE, as rows, rather than only being refused at
        # authentication time. Both exist — `auth._resolve_token` also refuses
        # an agent whose person is disabled, which catches the routes that never
        # come through this command (the login-throttle hard-disable is one) —
        # but the row is what makes `user enable` NON-symmetric, and the
        # asymmetry is the point: re-enabling a person must not silently re-arm
        # an agent that was taken down for its own reasons. An auth-time-only
        # check would re-arm every one of them the moment the person came back.
        #
        # An agent already disabled keeps its original `disabled_at`. Stamping
        # it again would rewrite when it was taken down, which is the one fact
        # somebody reconstructing an incident actually wants.
        cascaded = []
        for bot in _agents_of(s, user):
            if bot.disabled_at is not None:
                continue
            bot.disabled_at = _now()
            bot_tokens = _live_token_count(s, bot.id)
            _event(s, EVENT_USER_DISABLED, user_id=bot.id, actor=operator,
                   reason="person_disabled",
                   detail="agent=%s person=%s tokens_blocked=%d"
                          % (bot.subject, user.subject, bot_tokens))
            cascaded.append((bot.subject, bot_tokens))

        _event(s, EVENT_USER_DISABLED, user_id=user.id, actor=operator,
               reason="operator_disable",
               detail="username=%s sessions_deleted=%d tokens_blocked=%d agents_disabled=%d"
                      % (user.subject, killed, tokens, len(cascaded)))
        s.commit()
        handle = user.subject
        already = [b.subject for b in _agents_of(s, user)
                   if b.subject not in {c[0] for c in cascaded}]

    print("disabled %s  sessions deleted: %d  service tokens now refused: %d"
          % (handle, killed, tokens))
    print("  The account row is kept - revocation is `disabled_at`, never a DELETE, so")
    print("  the id is never reissued to somebody else.")
    # NAMED, not counted. "2 agents disabled" is a number to skim past; an
    # operator who does not know WHICH machines just stopped cannot go and
    # explain the failures to anyone.
    if cascaded:
        print("  Agents taken down with them:")
        for name, count in cascaded:
            print("      %-24s %d service token(s) now refused" % (name, count))
        print("  `user enable %s` does NOT bring these back - re-enable each one" % handle)
        print("  deliberately with `user enable <agent>`.")
    if already:
        print("  Already disabled, left as they were: %s" % ", ".join(already))
    if not cascaded and not already:
        print("  No agents are linked to this person, so nothing machine-side changed.")
    if last_owner and not args.force:
        print("  WARNING: that was the last enabled owner account.")
    return 0


def cmd_user_enable(args: argparse.Namespace) -> int:
    operator = _operator(args)
    with Session(_engine()) as s:
        user = _find_user(s, args.username)
        if user.disabled_at is None:
            print("%s is not disabled" % user.subject)
            return 0
        was = user.disabled_at
        user.disabled_at = None
        _event(s, EVENT_USER_ENABLED, user_id=user.id, actor=operator,
               reason="operator_enable",
               detail="username=%s disabled_since=%s" % (user.subject, _fmt_time(was)))
        s.commit()
        handle, is_service, must_change = user.subject, user.is_service, user.must_change_password
        # DELIBERATELY NOT SYMMETRIC (TASK-1113). `user disable` takes a
        # person's agents down with them; `user enable` does NOT bring them
        # back, because an agent may have been disabled for its own reasons --
        # a leaked token, a misbehaving process -- and re-arming it as a side
        # effect of somebody's return is a silent re-grant of machine access.
        # The cost of the asymmetry is one command per agent; the cost of the
        # symmetry is not knowing what just started running again.
        still_down = [b.subject for b in _agents_of(s, user) if b.disabled_at is not None]

    print("enabled %s (disabled since %s)" % (handle, _fmt_time(was)))
    if is_service:
        print("  Its service tokens are live again; nothing was re-minted.")
    else:
        print("  No password was restored. A re-enable is normally followed by")
        print("  `user reset-password %s` - the old credential still works otherwise." % handle)
        if must_change:
            print("  This account still has must_change_password set.")
    if still_down:
        print("  THEIR AGENTS ARE STILL DISABLED, on purpose - a re-enable never")
        print("  re-arms a machine as a side effect. Bring each back deliberately:")
        for name in still_down:
            print("      user enable %s" % name)
    return 0


_USER_COLUMNS = ("username", "role", "is_service", "person", "disabled_at",
                 "must_change_password", "last_login_at")


def cmd_user_link_idp(args: argparse.Namespace) -> int:
    """Point an existing LOCAL person at an IdP identity (ADR 0041 Adoption §4).

    The account keeps its id, so its history, grants, group memberships and agents
    stay theirs: one person, one account, instead of a second one from JIT
    provisioning. What changes is the row's `(idp, subject)` key, from
    `('local', <username>)` to `(<idp>, <IdP subject>)` — the composite 0025 made
    for exactly this. Afterwards:

      * the pre-link username is KEPT as the account's local sign-in name
        (TASK-1454, `auth_user_idp_state.local_name`), unless `--no-local-name`:
        with `local` and `oidc` in ATLAS_AUTH_PROVIDERS the password form accepts
        it, so a personal password is a fallback for when the IdP is down, under
        ATLAS_LOCAL_LOGIN (`all`: any such account with a password; `break-glass`:
        a flagged one only). Otherwise, as before, the password does not sign it
        in. The stored verifier is left untouched; a temporary one still forces
        the change on a password sign-in;
      * their sessions are ended, so the next one is a new sign-in (an SSO one is
        bounded by ATLAS_OIDC_MAX_SESSION_HOURS);
      * their agents' tokens are SUSPENDED until their first OIDC login
        (ADR 0041 §6: an IdP-backed person's agents need a recent IdP login);
      * other commands name the account `<idp>:<subject>` or by its email.

    The subject is what the IdP puts in the token: Keycloak's user id (`sub`), or
    for the entra preset the user's Object ID (`oid`).
    """
    operator = _operator(args)
    idp = args.idp.strip().lower()
    subject = args.subject.strip()
    if idp == "local" or not idp or len(idp) > 32:
        raise AdminError("--idp names the identity provider (e.g. oidc), never local")
    if not subject or len(subject) > 64 or any(c.isspace() or not c.isprintable() for c in subject):
        raise AdminError("--subject must be 1-64 printable characters without spaces")
    if len(subject) == 36 and not set(subject.lower()) - set("0123456789abcdef-"):
        # A GUID (an Entra oid, a Keycloak id): the login stores it lowercased.
        subject = subject.lower()
    with Session(_engine()) as s:
        user = _find_user(s, args.username, service=False)
        if (user.idp or "local") != "local":
            raise AdminError("%r is already linked (%s:%s)" % (args.username, user.idp, user.subject))
        taken = s.scalar(select(AuthUser).where(AuthUser.idp == idp, AuthUser.subject == subject))
        if taken is not None:
            raise AdminError(
                "%s:%s already belongs to another account (%s, created %s), most likely made "
                "by JIT provisioning at a first SSO login. Accounts are never merged: disable "
                "that one (`user disable %s:%s`) and ask the operator of record what to keep."
                % (idp, subject, taken.id, _fmt_time(taken.created_at), idp, subject))
        old = user.subject
        keep_name = not getattr(args, "no_local_name", False)
        user.idp = idp
        user.subject = subject
        if keep_name:
            # The name was this row's own `idp='local'` subject until this moment, so
            # no local account holds it; the unique index refuses a linked one that does.
            _set_local_name(s, user, old)
        killed = _kill_sessions(s, user.id)
        _event(s, "user_idp_linked", user_id=user.id, actor=operator,
               reason="%s" % idp, detail="was=local:%s now=%s:%s%s" % (
                   old, idp, subject, (" local_name=%s" % old) if keep_name else ""))
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            raise AdminError("%s:%s (or the local name %r) was taken while this ran"
                             % (idp, subject, old)) from None
        agents = len(_agents_of(s, user))
        user_id = user.id
    print("linked %s (id=%s) to %s:%s" % (old, user_id, idp, subject))
    print("  %d session(s) ended." % killed)
    if keep_name:
        print("  Local sign-in name kept: %s. %s" % (old, _fallback_sentence()))
    else:
        print("  No local sign-in name (--no-local-name): the password does not sign this "
              "account in. `user set-local-name` gives it one later.")
    if agents:
        print("  %d agent(s) kept. Their tokens are SUSPENDED until this person's first SSO "
              "login." % agents)
    print("  Name it from now on as %s:%s (or by its email)." % (idp, subject))
    return 0


def _set_local_name(s: Session, user: AuthUser, name: Optional[str]) -> None:
    """Write `auth_user_idp_state.local_name` for `user` (the side table, ADR 0041
    Adoption §1), creating the row when the account has none yet."""
    state = s.get(models_auth.AuthUserIdpState, user.id)
    if state is None:
        if name is None:
            return
        state = models_auth.AuthUserIdpState(user_id=user.id, break_glass=False)
        s.add(state)
    state.local_name = name


def _fallback_sentence() -> str:
    """What a local sign-in name does under THIS process's settings (TASK-1454)."""
    if not identity.linked_password_login_enabled():
        return ("It is not used now: the password form accepts it only with local and oidc "
                "both in ATLAS_AUTH_PROVIDERS.")
    if identity.SETTINGS.break_glass_only:
        return ("With ATLAS_LOCAL_LOGIN=break-glass it signs in only if the account is "
                "flagged break-glass, at /auth/login?break_glass=1, each use audited.")
    return ("With ATLAS_LOCAL_LOGIN=all its password signs in under that name as a fallback "
            "for when the IdP is down.")


def cmd_user_set_local_name(args: argparse.Namespace) -> int:
    """Give an IdP-backed account its local sign-in name, or take it away (TASK-1454).

    For an account linked before `link-idp` kept the pre-link username, or one made
    by a first SSO login (which never had a username). The name is in the local
    namespace: refused when a local account or agent holds it, or another linked
    account has it as its local name (`identity.local_name_taken`). `--clear`
    removes it, after which the password does not sign the account in. Neither
    sets a password: a person sets their own (`POST /api/me/password` after an IdP
    step-up, or `/auth/password/change`), or the operator hands over a temporary
    one with `user reset-password`, which forces the change at the next password
    sign-in. Both are `user_local_name` auth_events naming the operator.
    """
    operator = _operator(args)
    clear = bool(getattr(args, "clear", False))
    if clear == bool(args.name):
        raise AdminError("give the local name, or --clear (not both)")
    name = None if clear else _validated_handle(args.name)
    with Session(_engine()) as s:
        user = _find_user(s, args.account, service=False)
        if identity.is_local(user):
            raise AdminError("%s is a local account: its username is its sign-in name"
                             % _display(user))
        before = identity.local_name_of(s, user)
        if name is not None:
            taken = identity.local_name_taken(s, name, except_user_id=user.id)
            if taken is not None:
                raise AdminError("%s; choose another name" % taken)
        if before == name:
            print("%s already has %s" % (_display(user), ("local name %s" % name) if name
                                         else "no local name"))
            return 0
        _set_local_name(s, user, name)
        _event(s, "user_local_name", user_id=user.id, actor=operator,
               reason="cleared" if name is None else "set",
               detail="account=%s was=%s now=%s" % (_display(user), before or "-", name or "-"))
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            raise AdminError("%r was taken while this ran" % name) from None
        display, has_password = _display(user), bool(user.password_hash)
    if name is None:
        print("%s: local name %s removed; the password does not sign this account in" %
              (display, before))
        return 0
    print("%s: local sign-in name %s" % (display, name))
    print("  %s" % _fallback_sentence())
    if not has_password:
        print("  It holds no password yet: the person sets one after an IdP step-up, or")
        print("  `user reset-password %s` hands over a temporary one (changed at first use)." % name)
    return 0


def _user_row(user: AuthUser, person: Optional[str] = None) -> list[str]:
    return [
        _display(user),   # the username; `<idp>:<subject>` for an IdP-backed account
        resolve_role(user.role),
        _yn(user.is_service),
        person or "-",
        _fmt_time(user.disabled_at),
        _yn(user.must_change_password),
        _fmt_time(user.last_login_at),
    ]


def _user_json(user: AuthUser, person: Optional[str] = None,
               local_name: Optional[str] = None) -> dict:
    """Everything `user show` may disclose. NO `password_hash`, ever.

    An IdP-backed account also carries `local_name` (TASK-1454), null when it has
    none; a local account's object is unchanged (its username IS its name)."""
    out = {
        "id": user.id,
        "idp": user.idp,
        "username": user.subject,
        "name": user.name,
        "email": user.email,
        "role": resolve_role(user.role),
        "role_stored": user.role,
        "is_service": bool(user.is_service),
        "label": user.label,
        # The STORED id and the RESOLVED username, both, and for the reason
        # `_person_of` gives: an id pointing at nothing must stay readable.
        "person_user_id": user.person_user_id,
        "person": person,
        "disabled_at": _iso(user.disabled_at),
        "must_change_password": bool(user.must_change_password),
        "password_set": bool(user.password_hash),
        "hash_scheme": user.hash_scheme,
        "password_changed_at": _iso(user.password_changed_at),
        "temp_expires_at": _iso(user.temp_expires_at),
        "created_at": _iso(user.created_at),
        "last_login_at": _iso(user.last_login_at),
    }
    if not identity.is_local(user):
        out["local_name"] = local_name
    return out


def cmd_user_list(args: argparse.Namespace) -> int:
    with Session(_engine()) as s:
        users = s.scalars(select(AuthUser).order_by(
            AuthUser.is_service, AuthUser.subject
        )).all()
        # ONE lookup for the whole listing, not one per row: a `person` column
        # that costs a query per account is a column somebody deletes again.
        name_of = {u.id: u.subject for u in users}
        # The linked accounts' local sign-in names (TASK-1454): one read, and none
        # at all on an install with no IdP-backed person.
        linked = [u.id for u in users if not identity.is_local(u) and not u.is_service]
        local_names = dict(s.execute(select(
            models_auth.AuthUserIdpState.user_id, models_auth.AuthUserIdpState.local_name).where(
            models_auth.AuthUserIdpState.user_id.in_(linked),
            models_auth.AuthUserIdpState.local_name.is_not(None))).all()) if linked else {}

        def who(u: AuthUser) -> Optional[str]:
            return name_of.get(u.person_user_id) if u.person_user_id else None
        if args.json:
            print(json.dumps([_user_json(u, who(u), local_names.get(u.id)) for u in users],
                             indent=2))
            return 0
        if not users:
            print("no accounts. `user add <username>` creates the first one - the app "
                  "serves the login page and refuses everything until then.")
            return 0
        rows = [_user_row(u, who(u)) for u in users]
        columns = _USER_COLUMNS
        if local_names:
            # A column of its own, shown only once some account has a local name, so
            # an install without one prints today's table (TASK-1454).
            columns = columns[:1] + ("local_name",) + columns[1:]
            rows = [r[:1] + [local_names.get(u.id, "-")] + r[1:] for r, u in zip(rows, users)]
        print(_table(columns, rows))
        print()
        print("%d account(s)" % len(users))
    return 0


def cmd_user_show(args: argparse.Namespace) -> int:
    with Session(_engine()) as s:
        user = _find_user(s, args.username)
        person = _person_of(s, user)
        data = _user_json(user, person.subject if person else None,
                          identity.local_name_of(s, user))
        live_sessions = len(s.scalars(select(AuthSession.id_hash).where(
            AuthSession.user_id == user.id
        )).all())
        live_tokens = _live_token_count(s, user.id)
    if args.json:
        data["live_sessions"] = live_sessions
        data["live_service_tokens"] = live_tokens
        print(json.dumps(data, indent=2))
        return 0
    # A minted secret is NOT here and cannot be: it is hashed at rest in the
    # same column as any other password, so `password_set` is the whole of what
    # this command knows about it.
    width = max(len(k) for k in data)
    for key, value in data.items():
        print("%s : %s" % (key.ljust(width), "-" if value is None else value))
    print("%s : %d" % ("live_sessions".ljust(width), live_sessions))
    print("%s : %d" % ("live_service_tokens".ljust(width), live_tokens))
    return 0


# --------------------------------------------------------------------------- #
# bot add — the machine principal
# --------------------------------------------------------------------------- #

def cmd_session_mint(args: argparse.Namespace) -> int:
    """Hand an agent a real browser session without a password existing.

    See auth.mint_browser_session for why this is safe and what it refuses. It
    is gated on ATLAS_ALLOW_SESSION_MINT, set only in the staging overlay.
    """
    handle = _validated_handle(args.username)
    operator = _operator(args)
    with Session(_engine()) as s:
        user = s.scalar(select(AuthUser).where(
            AuthUser.idp == "local", AuthUser.subject == handle
        ))
        if user is None:
            raise AdminError(
                "no such account %r. Create a password-free one with:\n"
                "      user add %s --no-password" % (handle, handle)
            )
        try:
            sid, expires = auth.mint_browser_session(
                s, user=user, minutes=args.minutes, actor=operator,
                allow_owner=args.allow_owner,
            )
        except ValueError as exc:
            raise AdminError(str(exc)) from None
        s.commit()

    print("minted a browser session for %s, valid until %s" % (handle, _fmt_time(expires)))
    _announce_secret(
        "Session cookie value", handle, sid,
        lifetime=("Valid until %s (%d minutes), and REUSABLE until then — it is an\n"
                  "ordinary session, not a one-shot credential. It also dies on\n"
                  "`user disable %s`, which is how to end it early."
                  % (_fmt_time(_aware(expires)), args.minutes, handle)))
    print()
    print("  Use it by setting the cookie on the board's own origin. The `__Host-`")
    print("  prefix means it MUST be set with Secure and Path=/ and no Domain, and")
    print("  only over https — a plain-http origin will silently drop it:")
    print()
    print('      document.cookie = "%s=" + sid + "; Secure; Path=/; SameSite=Lax";'
          % auth.SESSION_COOKIE)
    print()
    print("  Then reload. GET /api/me returns the CSRF token the board needs for")
    print("  any state-changing POST. Revoke early by disabling the account, or")
    print("  just let it expire.")
    return 0


def cmd_bot_add(args: argparse.Namespace) -> int:
    """An `is_service` `auth_user` with NO usable password.

    Bots and humans share one table and one `subject` namespace, told apart by
    `is_service`: a DB CHECK stops a service row ever holding a password, and
    `_do_login()` refuses one by name, so a bot can never reach the login form.
    One bot per caller, never shared — a shared credential means the audit log
    says "atlas" for every call and revoking one caller revokes all of them.

    `must_change_password` is set FALSE explicitly. It defaults TRUE at the
    database level (correct for people), and a bot row that inherits it mints
    tokens the gate then refuses on every request — a silent ingest freeze.
    """
    handle = _validated_handle(args.label)
    operator = _operator(args)
    person_handle = None
    with Session(_engine()) as s:
        existing = s.scalar(select(AuthUser).where(
            AuthUser.idp == "local", AuthUser.subject == handle
        ))
        if existing is not None:
            raise AdminError("%r already exists; usernames and bot labels share one namespace"
                             % handle)
        taken = identity.local_name_taken(s, handle)
        if taken is not None:
            # The linked accounts' local sign-in names are in that namespace too (TASK-1454).
            raise AdminError("%s; usernames, bot labels and local sign-in names share one "
                             "namespace" % taken)
        person = _find_user(s, args.person, service=False) if args.person else None
        bot = AuthUser(
            id=str(uuid.uuid4()),
            idp="local",
            subject=handle,
            name=_clean(args.name, 120),
            role=models_auth.ROLE_MEMBER,
            is_service=True,
            label=_clean(args.label, 64),
            password_hash=None,        # CHECK ck_auth_user_service_has_no_password
            must_change_password=False,
            # OPTIONAL, and left NULL when unstated (ADR 0034). Demanding it here
            # would be the eager-creation shape that ADR rejected wearing another
            # name: it forces an answer at the moment the operator knows least.
            person_user_id=person.id if person else None,
            created_at=_now(),
        )
        s.add(bot)
        _event(s, EVENT_USER_CREATED, user_id=bot.id, actor=operator,
               reason="service_principal", detail="label=%s" % handle)
        if person is not None:
            person_handle = person.subject
            _event(s, EVENT_BOT_LINKED, user_id=bot.id, actor=operator,
                   reason="bot_add", detail="person=%s" % person.subject)
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            raise AdminError("%r was created by someone else while this ran" % handle) from None
        bot_id = bot.id

    print("created bot %s  id=%s" % (handle, bot_id))
    if person_handle:
        print("  Accountable person: %s" % person_handle)
    else:
        print("  No person recorded. `bot link %s <person>` says who is accountable." % handle)
    print("  It holds no password and cannot sign in. Give it a credential with:")
    print("      token add %s --scopes read --out - > ~/.%s-token" % (handle, handle))
    return 0


# --------------------------------------------------------------------------- #
# bot link / unlink / list - who is accountable for an agent (ADR 0034, TASK-1112)
#
# THIS RECORDS A FACT AND GRANTS NOTHING, TODAY. The link is what TASK-1114 will
# read to make an agent inherit its person's GRANTS (never their ROLE), and what
# TASK-1113 will read to disable an agent along with its person. Until those land
# it is an accountability record and an ordering key for the settings grid --
# which is exactly why setting one is safe to do now, ahead of both.
#
# Deliberately NOT refused: linking to a DISABLED person. The link states who was
# accountable, and that stays true after they leave; refusing it would mean an
# offboarded person's agents could be attributed to nobody, which is the opposite
# of the point. So this warns instead of blocking.
# --------------------------------------------------------------------------- #

def cmd_bot_link(args: argparse.Namespace) -> int:
    operator = _operator(args)
    with Session(_engine()) as s:
        bot = _find_user(s, args.bot, service=True)
        person = _find_user(s, args.person, service=False)
        refusal = models_auth.refuse_person_link(bot, person)
        if refusal is not None:
            # Unreachable while `_find_user` above filters on `service=`, and
            # kept anyway: this is the ONE rule the credential screen will also
            # call, and a guard that only exists at the caller is a guard the
            # second caller does not have.
            raise AdminError(refusal)
        previous = _person_of(s, bot)
        if previous is not None and previous.id != person.id:
            # Re-pointing an agent at a different person is a real operation (a
            # handover) and it MOVES what that agent may see once grants
            # inherit. Say what is being replaced rather than doing it quietly.
            print("was: %s  ->  now: %s" % (previous.subject, person.subject))
        bot.person_user_id = person.id
        _event(s, EVENT_BOT_LINKED, user_id=bot.id, actor=operator,
               reason="bot_link", detail="person=%s" % person.subject)
        s.commit()
        disabled = person.disabled_at is not None
        bot_handle, person_handle = bot.subject, person.subject

    print("%s belongs to %s" % (bot_handle, person_handle))
    # THE SEMANTICS, not the task numbers. The first version of this message
    # named two unbuilt tasks and was wrong within the day TASK-1114 landed --
    # caught by running the command, not by reading the code. What a link MEANS
    # does not go stale; what is not built yet always does.
    print("  It now sees what %s is granted, and nothing more: an agent inherits" % person_handle)
    print("  GRANTS, never ROLE, so this never makes it an owner.")
    if disabled:
        print("  WARNING: %s is disabled, so this agent's tokens are REFUSED from now" % person_handle)
        print("  on. The link is still recorded - it states who was accountable - but if")
        print("  the agent is meant to outlive them, `bot unlink %s`." % bot_handle)
    print("  Disabling %s will disable this agent too." % person_handle)
    return 0


def cmd_bot_unlink(args: argparse.Namespace) -> int:
    operator = _operator(args)
    with Session(_engine()) as s:
        bot = _find_user(s, args.bot, service=True)
        previous = _person_of(s, bot)
        stored = bot.person_user_id
        if not stored:
            print("%s has no person recorded; nothing to clear" % bot.subject)
            return 0
        bot.person_user_id = None
        _event(s, EVENT_BOT_UNLINKED, user_id=bot.id, actor=operator,
               reason="bot_unlink",
               detail="person=%s" % (previous.subject if previous else stored))
        s.commit()
        handle = bot.subject
    print("%s belongs to nobody now" % handle)
    print("  Its own grants are untouched - this clears the accountability record,")
    print("  not access. `bot list` shows it as unlinked.")
    return 0


_BOT_COLUMNS = ("bot", "person", "disabled_at", "live_tokens", "created_at")

#: What `bot list` prints in the `person` column when the linked person is
#: disabled. The agent's OWN row may still look enabled — a bot linked to an
#: already-disabled person never went through the `user disable` cascade — and
#: its tokens are refused anyway, because `auth._resolve_token` checks the
#: person too. Without this the listing would show a working agent that 401s on
#: every call, which is the invisible state this whole story exists to remove.
_PERSON_DISABLED_SUFFIX = " (disabled)"


def cmd_bot_list(args: argparse.Namespace) -> int:
    with Session(_engine()) as s:
        bots = s.scalars(select(AuthUser).where(AuthUser.is_service.is_(True))
                         .order_by(AuthUser.subject)).all()
        everyone = list(s.scalars(select(AuthUser)).all())
        name_of = {u.id: u.subject for u in everyone}
        person_down = {u.id for u in everyone if u.disabled_at is not None}
        rows = []
        payload = []
        for b in bots:
            person = name_of.get(b.person_user_id) if b.person_user_id else None
            down = bool(b.person_user_id) and b.person_user_id in person_down
            tokens = _live_token_count(s, b.id)
            rows.append([b.subject,
                         (person + _PERSON_DISABLED_SUFFIX) if (person and down) else (person or "-"),
                         _fmt_time(b.disabled_at), str(tokens), _fmt_time(b.created_at)])
            payload.append({
                "username": b.subject,
                "id": b.id,
                "label": b.label,
                "person_user_id": b.person_user_id,
                "person": person,
                "person_disabled": down,
                "disabled_at": _iso(b.disabled_at),
                "live_service_tokens": tokens,
                "created_at": _iso(b.created_at),
            })
        unlinked = [b.subject for b in bots if not b.person_user_id]
        blocked = [b.subject for b in bots
                   if b.disabled_at is None and b.person_user_id in person_down]

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    if not bots:
        print("no agent accounts. `bot add <label>` creates one.")
        return 0
    print(_table(_BOT_COLUMNS, rows))
    print()
    print("%d agent account(s)" % len(bots))
    if unlinked:
        # NAMED, not counted. "3 unlinked" is a number to skim past; a list is a
        # worklist, and the whole point of the column is that somebody decides.
        print("no person recorded for: %s" % ", ".join(unlinked))
        print("  `bot link <bot> <person>` records one. Nothing guesses it from a name.")
    if blocked:
        print("enabled, but REFUSED because their person is disabled: %s" % ", ".join(blocked))
        print("  Re-enable the person, or `bot unlink` if the agent should outlive them.")
    return 0


# --------------------------------------------------------------------------- #
# token add / list / revoke
# --------------------------------------------------------------------------- #

def _deliver_token(raw: str, out: str) -> None:
    """Put a raw token where `--out` said, and nowhere else.

    `-` means stdout carrying the raw value ALONE, for a redirect that stays on
    the machine running the command: `... token add X --scopes read --out - >
    ~/.x-token`. Any other value is a file created 0600, refusing to overwrite.
    """
    if out == "-":
        sys.stdout.write(raw + "\n")
        return
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(raw + "\n")


def cmd_token_add(args: argparse.Namespace) -> int:
    # A raw token never reaches stdout by DEFAULT (2026-09-15). The old default
    # printed it, and the command is almost always run by an agent over ssh, so
    # "printed once" meant "written into a session transcript" -- a token that
    # then has to be rotated, and one that works from outside the LAN because
    # Bearer is honoured on the public host. AGENTS.md carried that as a trap to
    # remember; this refusal is the fix, and it happens BEFORE anything is minted.
    if bool(args.print_token) == bool(args.out):
        raise AdminError(
            "choose where the raw token goes: --out - (the raw value alone on "
            "stdout, for a redirect such as `--out - > ~/.x-token` on the server) "
            "or --out PATH (a new 0600 file), or --print to show it here. A token "
            "printed into an agent's transcript has to be rotated.")
    if args.out and args.out != "-" and os.path.exists(args.out):
        raise AdminError("%s already exists; refusing to overwrite a credential file" % args.out)
    scopes = [chunk.strip() for chunk in args.scopes.split(",") if chunk.strip()]
    operator = _operator(args)
    with Session(_engine()) as s:
        owner = _find_user(s, args.owner, service=True)
        if owner.disabled_at is not None:
            raise AdminError("%r is disabled; `user enable %s` first" % (owner.subject, owner.subject))
        try:
            # auth.py owns generation, hashing and the scope domain. Only
            # HMAC(ATLAS_TOKEN_PEPPER, token) is stored; the raw value is
            # returned once and never written anywhere.
            raw, row = auth.mint_service_token(
                s, owner=owner, scopes=scopes, label=args.label or owner.label or owner.subject,
                expires_days=args.days,
            )
        except ValueError as exc:
            s.rollback()
            raise AdminError(str(exc)) from None
        # `mint_service_token()` writes its own `token_created` row and has no
        # actor parameter, so the operator would be NULL on the one admin verb
        # that mints a machine credential. Name them on the pending row rather
        # than adding a second, duplicate audit row. Only ever FILLS a null, so
        # it stays correct if auth.py grows an `actor=` argument later.
        for pending in list(s.new):
            if (isinstance(pending, models_auth.AuthEvent)
                    and pending.event == "token_created" and pending.actor is None):
                pending.actor = _clean(operator, 64)
        s.commit()
        token_id, prefix, expires = row.id, row.prefix, row.expires_at
        scope_text, handle = row.scopes, owner.subject

    note = ("Revoke it with `token revoke %s`. Rotation is mint -> deploy -> confirm "
            "last_used_at moves -> revoke." % token_id)
    expiry = ("  WARNING: no expiry. A credential with no deadline is one nobody rotates."
              if expires is None else "  Expires %s." % _fmt_time(expires))
    if args.print_token:
        print("minted service token for %s  id=%s  prefix=%s  scopes=%s"
              % (handle, token_id, prefix, scope_text))
        _announce_secret("Service token", handle, raw, note=note)
        print(expiry)
        return 0
    # --out: every human-readable line goes to stderr, so `--out -` leaves the
    # raw value as the ONLY thing on stdout.
    _deliver_token(raw, args.out)
    where = "stdout" if args.out == "-" else args.out
    print("minted service token for %s  id=%s  prefix=%s  scopes=%s  written to %s"
          % (handle, token_id, prefix, scope_text, where), file=sys.stderr)
    print("  %s" % note, file=sys.stderr)
    print(expiry, file=sys.stderr)
    return 0


def _agent_base(person: AuthUser) -> str:
    """The prefix of a person's agent names: their username, or for an IdP-backed
    person (whose subject is an opaque id) the local part of their email."""
    base = passwords.agent_base(person)   # the rule the self-service route shares
    if not base:
        raise AdminError("%s has no email to name an agent after; it cannot be given one by "
                         "`token mint` until it has signed in once" % _display(person))
    return base


def _tokens_of_user(s: Session, user: AuthUser) -> list[str]:
    """Owner ids whose tokens belong to `user`: the bot itself, or a person's agents."""
    if user.is_service:
        return [user.id]
    return [a.id for a in _agents_of(s, user)]


def cmd_token_mint(args: argparse.Namespace) -> int:
    """Mint an agent token FOR A PERSON, written only to --out (STORY-oss-13).

    The operator's equivalent of Settings -> Agent credentials, for the people who
    cannot use that screen: it re-asks the person's password, and an IdP-backed
    person has none. The same rules apply as there (routers/me.py):

      * the agent is `<person>-<purpose>`, created on first use and linked to the
        person (ADR 0034); minting again for the same purpose adds a token to the
        same agent (rotation: mint, deploy, confirm, revoke the old one);
      * the scopes are capped by the PERSON'S role (`self_mintable_scopes`);
      * it always expires (1..365 days).

    THE RAW TOKEN GOES TO --out AND NOWHERE ELSE: a new 0600 file, never stdout
    (`-` is refused), never stderr, never a log line. The command is usually run
    by an agent over ssh, and stdout is a transcript.
    """
    out = args.out
    if not out or out == "-":
        raise AdminError("token mint writes the raw token only to a file: --out PATH (a new "
                         "0600 file). It never prints it.")
    if os.path.exists(out):
        raise AdminError("%s already exists; refusing to overwrite a credential file" % out)
    if not 1 <= args.days <= auth.SERVICE_TOKEN_DEFAULT_DAYS:
        raise AdminError("--days must be 1..%d: an agent token always expires"
                         % auth.SERVICE_TOKEN_DEFAULT_DAYS)
    scopes = list(dict.fromkeys(c.strip() for c in args.scopes.split(",") if c.strip()))
    operator = _operator(args)
    with Session(_engine()) as s:
        person = _find_user(s, args.user, service=False)
        if person.disabled_at is not None:
            raise AdminError("%s is disabled" % _display(person))
        allowed = models_auth.self_mintable_scopes(person.role)
        over = [sc for sc in scopes if sc not in allowed]
        if not scopes or over:
            raise AdminError("%s may hold an agent with %s; refused: %s"
                             % (_display(person), ",".join(allowed), ",".join(over) or "(none)"))
        handle = _validated_handle("%s-%s" % (_agent_base(person), args.purpose))
        bot = s.scalar(select(AuthUser).where(AuthUser.idp == "local", AuthUser.subject == handle))
        if bot is not None and (not bot.is_service or bot.person_user_id != person.id):
            raise AdminError("%r already exists and is not %s's agent; choose another --purpose"
                             % (handle, _display(person)))
        if bot is not None and bot.disabled_at is not None:
            raise AdminError("%r is disabled; `user enable %s` first" % (handle, handle))
        if bot is None:
            bot = AuthUser(id=str(uuid.uuid4()), idp="local", subject=handle, name=None,
                           role=models_auth.ROLE_MEMBER, is_service=True, label=handle,
                           password_hash=None, must_change_password=False,
                           person_user_id=person.id, created_at=_now())
            blocked = models_auth.refuse_person_link(bot, person)
            if blocked:  # pragma: no cover - _find_user(service=False) precedes it
                raise AdminError(blocked)
            s.add(bot)
            s.flush()
            _event(s, EVENT_USER_CREATED, user_id=bot.id, actor=operator,
                   reason="operator_agent", detail="agent=%s person=%s" % (handle, _display(person)))
            _event(s, EVENT_BOT_LINKED, user_id=bot.id, actor=operator,
                   reason="token_mint", detail="person=%s" % _display(person))
        try:
            raw, row = auth.mint_service_token(s, owner=bot, scopes=scopes, label=handle,
                                               expires_days=args.days)
        except ValueError as exc:
            s.rollback()
            raise AdminError(str(exc)) from None
        for pending in list(s.new):
            if (isinstance(pending, models_auth.AuthEvent)
                    and pending.event == "token_created" and pending.actor is None):
                pending.actor = _clean(operator, 64)
        # Write the file BEFORE committing: if it cannot be written, no token exists.
        try:
            _deliver_token(raw, out)
        except OSError as exc:
            s.rollback()
            raise AdminError("could not create %s (%s); no token was minted"
                             % (out, type(exc).__name__)) from None
        del raw
        who = _display(person)
        token_id, prefix, scope_text, expires = row.id, row.prefix, row.scopes, row.expires_at
        s.commit()
    print("minted a token for %s's agent %s  id=%s  prefix=%s  scopes=%s  written to %s"
          % (who, handle, token_id, prefix, scope_text, out))
    print("  Expires %s. Revoke it with `token revoke %s`." % (_fmt_time(expires), token_id))
    return 0


def cmd_token_list(args: argparse.Namespace) -> int:
    with Session(_engine()) as s:
        stmt = select(AuthServiceToken).order_by(AuthServiceToken.created_at)
        if getattr(args, "user", None):
            stmt = stmt.where(AuthServiceToken.owner_user_id.in_(
                _tokens_of_user(s, _find_user(s, args.user)) or ["-"]))
        if not args.all:
            stmt = stmt.where(AuthServiceToken.revoked_at.is_(None))
        tokens = s.scalars(stmt).all()
        owners = {u.id: u.subject for u in s.scalars(select(AuthUser)).all()}
        records = []
        for row in tokens:
            records.append({
                "id": row.id,
                "prefix": row.prefix,          # a lookup handle, not a secret
                "owner": owners.get(row.owner_user_id, row.owner_user_id),
                "label": row.label,
                "scopes": row.scopes,
                "created_at": _iso(row.created_at),
                "expires_at": _iso(row.expires_at),
                "last_used_at": _iso(row.last_used_at),
                "revoked_at": _iso(row.revoked_at),
            })
        warnings = auth.token_warnings(s)

    if args.json:
        print(json.dumps({"tokens": records, "warnings": warnings}, indent=2))
        return 0
    if not records:
        print("no service tokens" + ("" if args.all else " (add --all to include revoked ones)"))
        return 0
    headers = ("id", "prefix", "owner", "label", "scopes", "expires_at",
               "last_used_at", "revoked_at")
    rows = [[r["id"], r["prefix"], r["owner"], r["label"] or "-", r["scopes"],
             (r["expires_at"] or "-")[:19], (r["last_used_at"] or "never")[:19],
             (r["revoked_at"] or "-")[:19]] for r in records]
    print(_table(headers, rows))
    print()
    print("%d token(s)" % len(records))
    for warning in warnings:
        print("  ! %s (%s): %s" % (warning["prefix"], warning["label"] or "-",
                                   ", ".join(warning["reasons"])))
    return 0


def _revoke_user_tokens(args: argparse.Namespace) -> int:
    """`token revoke --user X [TOKEN]`: every live token of X's agents (or of bot X),
    or just TOKEN after checking it is one of them."""
    operator = _operator(args)
    with Session(_engine()) as s:
        user = _find_user(s, args.user)
        owners = _tokens_of_user(s, user)
        who = _display(user)
        stmt = select(AuthServiceToken).where(
            AuthServiceToken.owner_user_id.in_(owners or ["-"]),
            AuthServiceToken.revoked_at.is_(None))
        rows = s.scalars(stmt).all()
        if args.token:
            handle = args.token.strip()
            rows = [r for r in rows if handle in (r.id, r.prefix)]
            if not rows:
                raise AdminError("no live token %r belongs to %s (`token list --user %s`)"
                                 % (handle, who, args.user))
        now = _now()
        done = []
        for row in rows:
            row.revoked_at = now
            _event(s, EVENT_TOKEN_REVOKED, user_id=row.owner_user_id, actor=operator,
                   reason=row.prefix, detail="label=%s scopes=%s user=%s"
                   % (row.label or "-", row.scopes, who))
            done.append((row.id, row.prefix))
        s.commit()
    if not done:
        print("%s has no live agent token" % who)
        return 0
    for token_id, prefix in done:
        print("revoked %s (prefix %s)" % (token_id, prefix))
    print("%d token(s) of %s revoked. Each fails on its very next request." % (len(done), who))
    return 0


def cmd_token_revoke(args: argparse.Namespace) -> int:
    if getattr(args, "user", None):
        return _revoke_user_tokens(args)
    if not args.token:
        raise AdminError("name the token (its id or prefix), or --user to revoke a person's")
    operator = _operator(args)
    handle = args.token.strip()
    with Session(_engine()) as s:
        row = s.get(AuthServiceToken, handle)
        if row is None:
            # `token list` prints the prefix beside the id, so accept either —
            # but never the token value itself, which nothing stores and which
            # would put a live credential in shell history.
            row = s.scalar(select(AuthServiceToken).where(AuthServiceToken.prefix == handle))
        if row is None:
            raise AdminError("no service token with id or prefix %r (`token list --all`)" % handle)
        if row.revoked_at is not None:
            print("token %s (prefix %s) was already revoked at %s"
                  % (row.id, row.prefix, _fmt_time(row.revoked_at)))
            return 0
        row.revoked_at = _now()
        _event(s, EVENT_TOKEN_REVOKED, user_id=row.owner_user_id, actor=operator,
               reason=row.prefix, detail="label=%s scopes=%s" % (row.label or "-", row.scopes))
        s.commit()
        token_id, prefix = row.id, row.prefix

    print("revoked %s (prefix %s). It fails on the very next request, with no restart."
          % (token_id, prefix))
    print("  Revoked, expired and never-existed are byte-identical to the caller.")
    return 0


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# rbac plan (ADR 0041 Adoption §3, STORY-oss-11)
#
# READ-ONLY. It answers "what changes if I set ATLAS_RBAC=projects now?" for
# every live account and every project, with the configured knobs
# (ATLAS_DEFAULT_PROJECT_VISIBILITY, ATLAS_SHARED_PROJECT_ROLE,
# ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER) applied, and writes nothing:
# nothing is converted, so switching RBAC off again is today exactly.
#
# A permission is one of none < read < write < delete, per project:
#   today    read   = the project root is in `_visible_node_ids` (RBAC off);
#            write  = read, and for an agent the token holds `write`;
#            delete = read, and for an agent the token holds `admin`
#                     (a person's session deletes whatever it can see today).
#   rbac-on  the effective project role: viewer=read, editor=write,
#            maintainer and owner=delete (an agent's role already capped by
#            its token's scopes, ADR 0041 §4).
# An agent is reported once per live token, because its scopes ARE its reach.
# --------------------------------------------------------------------------- #
PERMISSION_LEVELS = ("none", "read", "write", "delete")
_ROLE_LEVEL = {None: "none", "viewer": "read", "editor": "write",
               "maintainer": "delete", "owner": "delete"}


def rbac_plan_rows(s: Session) -> list[dict]:
    """The plan, as rows. Pure over the session; `verify_project_roles.py` calls it."""
    import dataclasses

    import deps  # the app's resolver, so the plan cannot disagree with the API
    import models_graph
    import rbac

    configured = rbac.SETTINGS
    off = dataclasses.replace(configured, mode="off")
    on = dataclasses.replace(configured, mode="projects")

    pmap = rbac.project_map(s)
    fam_of = {nt.id: nt.family for nt in s.scalars(select(models_graph.NodeType)).all()}
    type_of = dict(s.execute(select(models_graph.Node.id, models_graph.Node.type)).all())
    bound = set(s.scalars(select(models_auth.ProjectRoleBinding.project_id)).all())
    projects = sorted(p for p in rbac.project_ids(pmap)
                      if fam_of.get(type_of.get(p)) != "workitem" or p in bound)

    principals: list[tuple[AuthUser, str, auth.Principal]] = []
    now = _now()
    for u in s.scalars(select(AuthUser).where(AuthUser.disabled_at.is_(None))
                       .order_by(AuthUser.subject)).all():
        if not u.is_service:
            principals.append((u, "person", auth.Principal(
                kind="session", user_id=u.id, subject=u.subject, role=resolve_role(u.role),
                is_service=False, must_change_password=False)))
            continue
        for t in s.scalars(select(AuthServiceToken).where(
                AuthServiceToken.owner_user_id == u.id,
                AuthServiceToken.revoked_at.is_(None)).order_by(AuthServiceToken.prefix)).all():
            exp = _aware(t.expires_at)
            if exp is not None and exp <= now:
                continue
            scopes = tuple(sc for sc in (t.scopes or "").split(",") if sc)
            principals.append((u, "agent token %s (%s)" % (t.prefix, ",".join(scopes)),
                               auth.Principal(
                                   kind="token", user_id=u.id, subject=u.subject,
                                   role=resolve_role(u.role), is_service=True,
                                   must_change_password=False, scopes=scopes,
                                   person_user_id=u.person_user_id)))

    rows: list[dict] = []
    previous = rbac.use(off)
    try:
        for user, kind, p in principals:
            tok = auth.current_principal.set(p)
            try:
                rbac.use(off)
                vis = deps._visible_node_ids(s)
            finally:
                auth.current_principal.reset(tok)
            rbac.use(on)
            for pid in projects:
                if vis is not None and pid not in vis:
                    today = "none"
                elif not p.is_service:
                    today = "delete"
                else:
                    today = ("delete" if p.has_scope("admin") else
                             "write" if p.has_scope("write") else "read")
                role = rbac.role_on_project(s, p, pid)
                after = _ROLE_LEVEL.get(role, "none")
                rows.append({"account": user.subject, "kind": kind, "project": pid,
                             "today": today, "rbac_role": role, "rbac": after,
                             "changed": today != after})
    finally:
        rbac.use(previous)
    return rows


def cmd_rbac_plan(args: argparse.Namespace) -> int:
    """Print today's versus RBAC-on effective permission. Writes nothing."""
    import rbac

    engine = _engine()
    models_auth.create_all(engine)  # the RBAC tables, if this database predates them
    with Session(engine) as s:
        rows = rbac_plan_rows(s)
    shown = rows if args.all else [r for r in rows if r["changed"]]
    cfg = rbac.SETTINGS
    if args.json:
        print(json.dumps({"rbac": {"mode": cfg.mode,
                                   "default_project_visibility": cfg.default_project_visibility,
                                   "shared_project_role": cfg.shared_project_role,
                                   "instance_admin_implicit_project_owner":
                                       cfg.instance_admin_implicit_project_owner},
                          "rows": shown,
                          "changed": sum(1 for r in rows if r["changed"]),
                          "total": len(rows)}, indent=2))
        return 0
    print("ATLAS_RBAC is %r now. This is what ATLAS_RBAC=projects would change, with "
          "ATLAS_DEFAULT_PROJECT_VISIBILITY=%s, ATLAS_SHARED_PROJECT_ROLE=%s, "
          "ATLAS_INSTANCE_ADMIN_IMPLICIT_PROJECT_OWNER=%s. Nothing is written."
          % (cfg.mode, cfg.default_project_visibility, cfg.shared_project_role,
             "1" if cfg.instance_admin_implicit_project_owner else "0"))
    print()
    if shown:
        print(_table(["account", "as", "project", "today", "rbac on", "role"],
                     [[r["account"], r["kind"], r["project"], r["today"], r["rbac"],
                       r["rbac_role"] or "-"] for r in shown]))
        print()
    changed = sum(1 for r in rows if r["changed"])
    print("%d of %d (account, project) permissions change%s." % (
        changed, len(rows), "" if args.all else " (shown above; --all lists every one)"))
    return 0


# --------------------------------------------------------------------------- #
# audit purge (STORY-oss-16-audit-export)
#
# ATLAS_AUDIT_RETENTION_DAYS is enforced HERE, by a command an operator schedules
# (cron, a systemd timer, a Kubernetes CronJob), rather than by a loop inside
# atlas-api. That is the simpler of the two on every axis that matters here:
#   * boot stays a read and a request path stays a request path: nothing in the API
#     process deletes rows on a timer;
#   * uvicorn may run several workers, and a background loop would run in each of
#     them; a cron line runs once;
#   * it is one idempotent DELETE that an operator can run by hand, see the count of,
#     and prove in a verify script without waiting for a clock.
# Unset retention = keep forever (today): the command deletes nothing and says so.
# It touches `audit_read` only; graph_event and auth_event are never purged.
#
#   0 3 * * *  docker exec atlas-api atlas-admin audit purge
# --------------------------------------------------------------------------- #

def cmd_audit_purge(args: argparse.Namespace) -> int:
    """Delete audit_read rows older than ATLAS_AUDIT_RETENTION_DAYS."""
    import audit_log

    retention = settings.load_audit().retention_days
    if retention is None:
        print("ATLAS_AUDIT_RETENTION_DAYS is not set: audit_read rows are kept forever; "
              "nothing purged.")
        return 0
    engine = _engine()
    models_auth.create_all(engine)  # audit_read, if this database predates it
    deleted = audit_log.purge_reads(engine, retention)
    print("purged %d audit_read row(s) older than %d day(s); graph_event and auth_event "
          "are never purged." % (deleted, retention))
    return 0


def cmd_init_db(args: argparse.Namespace) -> int:
    """Create the auth + visibility tables if they are missing.

    Idempotent and additive — `models_auth.create_all()` only ever issues CREATE
    TABLE for a table that does not exist, and there is deliberately no
    `drop_all` counterpart anywhere. `app.py` does this at startup; the verb
    exists so the first account can be minted against a database the app has
    never booted on.
    """
    engine = _engine()
    models_auth.create_all(engine)
    print("auth schema present (%d metadata set(s))" % len(models_auth.metadatas()))
    return 0


# --------------------------------------------------------------------------- #
# guardrails (STORY-atlas-guardrails-per-env). The matrix, its format, what apply
# writes and why, and the exports are all in `guardrails.py`'s docstring; these
# verbs only parse arguments and relay. Opt-in: nothing applies a matrix but an
# operator running `guardrails apply`.
# --------------------------------------------------------------------------- #

def _guardrails_matrix(path: str):
    import guardrails
    try:
        return guardrails.load_file(path)
    except OSError as exc:
        raise AdminError("cannot read %s: %s" % (path, exc.strerror or exc)) from None
    except guardrails.MatrixError as exc:
        raise AdminError(str(exc)) from None


def cmd_guardrails_validate(args: argparse.Namespace) -> int:
    import guardrails
    m = _guardrails_matrix(args.file)
    print("valid: version %d, %d environment(s): %s; sha256 %s" % (
        m["version"], len(m["environments"]), ", ".join(e["name"] for e in m["environments"]),
        guardrails.digest(m)[:12]))
    return 0


def cmd_guardrails_apply(args: argparse.Namespace) -> int:
    import guardrails
    m = _guardrails_matrix(args.file)
    with Session(_engine()) as s:
        try:
            changes = guardrails.apply(s, m, filename=args.file, dry_run=args.dry_run,
                                       operator=_operator(args), adr_prefix=args.adr_prefix)
        except guardrails.GuardrailsError as exc:
            raise AdminError(str(exc)) from None
    if not changes:
        print("guardrails: %s already matches this matrix; nothing to do" % guardrails.SYSTEM_NODE)
        return 0
    head = "would" if args.dry_run else "applied"
    for c in changes:
        print("%s: %s" % (head, c))
    if args.dry_run:
        print("(dry run; nothing written)")
    return 0


def _guardrails_stored():
    import guardrails
    with Session(_engine()) as s:
        try:
            m = guardrails.stored_matrix(s)
        except guardrails.GuardrailsError as exc:
            raise AdminError(str(exc)) from None
    if m is None:
        raise AdminError("no guardrails matrix is applied to this install "
                         "(apply one with `guardrails apply FILE`, or export with --from FILE)")
    return m


def _crunch_config(args: argparse.Namespace):
    """The crunch-week config `--crunch FILE` / `--crunch-from-db` name, or None (STORY-atlas-
    crunch-mode). From the database it is `crunch.current(s)`: None when crunch week is off or
    has expired, and then the verb behaves exactly as without the flag (a note on stderr says
    so). The file is a crunch config (crunch.CONFIG_SCHEMA), JSON or YAML; the release path
    reads its `skip_envs` and `backup.policy` and refuses a key it does not know."""
    import guardrails
    if getattr(args, "crunch_file", None):
        try:
            with open(args.crunch_file, encoding="utf-8") as fh:
                return guardrails.parse_text(fh.read(), args.crunch_file)
        except OSError as exc:
            raise AdminError("cannot read %s: %s" % (args.crunch_file, exc.strerror or exc)) from None
        except guardrails.MatrixError as exc:
            raise AdminError("%s: %s" % (args.crunch_file, exc)) from None
    if not getattr(args, "crunch_from_db", False):
        return None
    import crunch  # backend/crunch.py; imported only when asked for
    with Session(_engine()) as s:
        state = crunch.current(s)
    if state is None:
        print("note: crunch week is off; nothing is overlaid", file=sys.stderr)
    return state


def _crunch_overlaid(m, cfg):
    import guardrails
    if cfg is None:
        return m
    try:
        return guardrails.crunch_overlay(m, cfg)
    except guardrails.MatrixError as exc:
        raise AdminError(str(exc)) from None


def _add_crunch_flags(p: argparse.ArgumentParser, what: str) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument("--crunch", dest="crunch_file", default=None, metavar="FILE",
                   help="%s as crunch week runs it, from a crunch config file (JSON or YAML)" % what)
    g.add_argument("--crunch-from-db", action="store_true", dest="crunch_from_db",
                   help="%s as crunch week runs it when crunch week is on (crunch.current); "
                        "unchanged when it is off" % what)


def cmd_guardrails_show(args: argparse.Namespace) -> int:
    import guardrails
    m = _guardrails_stored()
    sys.stdout.write(guardrails.dump(_crunch_overlaid(m, _crunch_config(args))))
    return 0


def cmd_guardrails_export(args: argparse.Namespace) -> int:
    import guardrails
    m = _guardrails_matrix(args.from_file) if args.from_file else _guardrails_stored()
    m = _crunch_overlaid(m, _crunch_config(args))
    try:
        out = guardrails.dump(guardrails.export(m, args.format, args.env))
    except guardrails.GuardrailsError as exc:
        raise AdminError(str(exc)) from None
    if args.out == "-":
        sys.stdout.write(out)
    else:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out)
        print("wrote %s (%s)" % (args.out, args.format))
    return 0


# --------------------------------------------------------------------------- #
# cicd (STORY-atlas-first-cicd). The generator, its templates and the guarantees
# are `cicd.py`'s docstring; the guide-answer mapping is `guide_answers.py`'s.
# `generate` writes files under --out and nothing else; only --record-adr (and
# --from-guide, which reads) touches the database. Atlas pushes nothing.
# --------------------------------------------------------------------------- #

def cmd_cicd_generate(args: argparse.Namespace) -> int:
    import cicd
    import guardrails
    import guide_answers
    if args.from_guide == bool(args.matrix):
        raise AdminError("give either --from-guide or --matrix FILE --answers FILE")
    if args.matrix and not args.answers:
        raise AdminError("--matrix needs --answers FILE (the guide's CI/CD answers)")
    if args.record_adr and (args.crunch_file or args.crunch_from_db):
        raise AdminError("--record-adr records a pipeline to keep; the crunch-week variant is "
                         "temporary and crunch week records its own ADR. Drop one of the two")
    crunch_cfg = _crunch_config(args)
    notes: list = []
    if args.record_adr:
        # Refuse before any file is written, not after: the ADR lives on atlas-system.
        from models_graph import Node as GraphNode
        with Session(_engine()) as s:
            if s.get(GraphNode, guardrails.SYSTEM_NODE) is None:
                raise AdminError("`%s` is not in this graph, and --record-adr records on it; add "
                                 "the system node set first (`python system_node.py --apply`)"
                                 % guardrails.SYSTEM_NODE)
    try:
        if args.from_guide:
            with Session(_engine()) as s:
                guide = guide_answers.latest_answers(s)
            matrix, ci, notes = guide_answers.from_guide(guide)
            asked = guide_answers.platform_of(guide)
            if asked and asked != args.platform:
                notes.append("the guide's cicd.ci_platform names %s; generating for %s as asked"
                             % (asked, args.platform))
        else:
            matrix = _guardrails_matrix(args.matrix)
            ci = guide_answers.cicd_input(guide_answers.load_answers_file(args.answers))
        pipeline = (cicd.generate(matrix, ci, args.platform) if crunch_cfg is None
                    else cicd.generate(matrix, ci, args.platform, crunch=crunch_cfg))
        if crunch_cfg is not None:
            notes.append("crunch-week variant: production deploys without %s; backup policy %s"
                         % (", ".join(pipeline["crunch"]["skip_envs"]) or "no skipped step",
                            pipeline["crunch"]["backup_policy"]))
        written = cicd.write_files(pipeline["files"], args.out, force=args.force)
    except OSError as exc:
        raise AdminError("cannot read or write: %s" % (exc.strerror or exc)) from None
    except (guardrails.MatrixError, cicd.CicdError, guide_answers.GuideAnswerError) as exc:
        raise AdminError(str(exc)) from None
    for path in written:
        print("wrote %s" % path)
    for n in notes:
        print("note: %s" % n)
    if args.record_adr:
        with Session(_engine()) as s:
            try:
                changes = cicd.record_adr(s, pipeline, matrix_label=matrix.get("name") or "",
                                          operator=_operator(args), adr_prefix=args.adr_prefix)
            except guardrails.GuardrailsError as exc:
                raise AdminError(str(exc)) from None
        for c in changes or ["this pipeline is already recorded; no ADR written"]:
            print("adr: %s" % c)
    print("review the files, fill every TODO(atlas), and commit them yourself; "
          "nothing was pushed")
    return 0


# --------------------------------------------------------------------------- #
# backup (STORY-atlas-crunch-mode). What a backup is, when one runs and where its
# result goes are `crunch_backup.py`'s docstring; these verbs parse and relay.
# `--reason manual` (the default) always runs; per_deploy / once / scheduled run
# only while the active crunch's policy is theirs, and otherwise say so and write
# nothing. `--reason routine` (OSS-26 C) always runs and keeps the newest
# ATLAS_BACKUP_KEEP routine backups. The DB URL comes from ATLAS_DB_URL(_FILE)
# and is never printed. `restore` and `rollback` (OSS-26 C) are
# `backup_restore.py`'s; its docstring has the design.
# --------------------------------------------------------------------------- #

def cmd_backup_run(args: argparse.Namespace) -> int:
    import crunch_backup
    from pathlib import Path
    try:
        if args.verify_only:
            url = crunch_backup.db_url()
            results = crunch_backup.verify_existing(Path(args.verify_only), url, pg_bin=args.pg_bin)
            if not results:
                raise AdminError("no backup in %s (files named atlas-<UTC>-<reason>.dump)"
                                 % args.verify_only)
            for name, ok, message in results:
                print("%s %s: %s" % ("ok    " if ok else "FAILED", name, message))
            failed = [r for r in results if not r[1]]
            if failed:
                raise AdminError("%d of %d backup(s) failed verification"
                                 % (len(failed), len(results)))
            return 0
        res, message = crunch_backup.run(args.reason, Path(args.dir), operator=_operator(args),
                                         pg_bin=args.pg_bin)
    except crunch_backup.BackupError as exc:
        raise AdminError(crunch_backup._scrub(exc)) from None  # noqa: SLF001
    if res is None:
        print("backup: skipped: %s" % message)
        return 0
    if not res.ok:
        raise AdminError(message)
    print(message)
    return 0


def cmd_backup_restore(args: argparse.Namespace) -> int:
    import backup_restore
    import crunch_backup
    from pathlib import Path
    try:
        dump = backup_restore.resolve_dump(args.dump, Path(args.dir))
        url = crunch_backup.db_url()
        backup_restore.restore(dump, url, dry_run=args.dry_run, pg_bin=args.pg_bin)
    except (backup_restore.RestoreError, crunch_backup.BackupError) as exc:
        raise AdminError(crunch_backup._scrub(exc)) from None  # noqa: SLF001
    return 0


def cmd_backup_rollback(args: argparse.Namespace) -> int:
    import backup_restore
    import crunch_backup
    try:
        url = crunch_backup.db_url()
        if not args.to:
            found = backup_restore.copies(url)
            if not found:
                print("no safety copy: no restore has set a database aside on this server")
                return 0
            for name, kind, size in found:
                print("%-12s %s  (%d MB)" % (kind, name, size // (1024 * 1024)))
            print("roll back with `atlas-admin backup rollback --to <a prerestore name>` "
                  "(atlas-api stopped)")
            return 0
        backup_restore.rollback(url, args.to)
    except (backup_restore.RestoreError, crunch_backup.BackupError) as exc:
        raise AdminError(crunch_backup._scrub(exc)) from None  # noqa: SLF001
    return 0


# --------------------------------------------------------------------------- #
# Argument parsing
#
# NOTE FOR ANY FUTURE READER: no parser below accepts a credential value, and
# none ever should (ASVS 6.4.6). The system generates; the operator relays.
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-admin",
        description=(
            "Atlas accounts. The operator mints, resets and disables; the system "
            "generates every secret and shows it exactly once."
        ),
        epilog=(
            "There is no `invite` and no `reconcile` (ADR 0025 left exactly one "
            "allow-list), and no flag anywhere accepts a password value."
        ),
    )
    parser.add_argument(
        "--actor", default=None,
        help="operator name recorded in auth_event.actor (default: $ATLAS_ADMIN_ACTOR, "
             "else the OS user)",
    )
    top = parser.add_subparsers(dest="group", required=True)

    # -- user ---------------------------------------------------------------- #
    user = top.add_parser("user", help="human accounts")
    user_sub = user.add_subparsers(dest="command", required=True)

    add = user_sub.add_parser(
        "add", help="create an account and print a generated temp password once (or --out it)")
    add.add_argument("username", help="permanent lowercase handle; never renamed")
    add.add_argument("--role", choices=list(models_auth.ROLES), default=models_auth.ROLE_MEMBER)
    add.add_argument("--email", default=None, help="contact only; never an authorization key")
    add.add_argument("--name", default=None, help="display name")
    add.add_argument(
        "--no-password", action="store_true", dest="no_password",
        help="create it with NO credential at all. It can never pass the login form; "
             "the only way in is `session mint`. For agent/test accounts.")
    add.add_argument(
        "--break-glass", action="store_true", dest="break_glass",
        help="with --role owner: this account's password still signs in while "
             "ATLAS_LOCAL_LOGIN=break-glass (ADR 0041 §7). Keep it in the vault.")
    add.add_argument("--out", default=None, metavar="PATH",
                     help="write the temporary password to a new 0600 file instead of "
                          "printing it (never `-`); stdout then carries no secret")
    add.set_defaults(func=cmd_user_add)

    link = user_sub.add_parser(
        "link-idp",
        help="point an existing local person at an IdP identity, keeping their id, "
             "history and agents; the username is kept as their local sign-in name")
    link.add_argument("username", help="the local username")
    link.add_argument("--subject", required=True,
                      help="the IdP's id for them: `sub` (Keycloak), the Object ID (entra preset)")
    link.add_argument("--idp", default="oidc", help="the provider (default oidc)")
    link.add_argument(
        "--no-local-name", action="store_true", dest="no_local_name",
        help="do not keep the username as a local sign-in name: the password then never "
             "signs this account in (SSO only)")
    link.set_defaults(func=cmd_user_link_idp)

    local_name = user_sub.add_parser(
        "set-local-name",
        help="give an IdP-backed account the local sign-in name its personal password "
             "signs in with when the IdP is down, or --clear it")
    local_name.add_argument("account", help="<idp>:<subject>, its email, or its current local name")
    local_name.add_argument("name", nargs="?", default=None,
                            help="the local sign-in name (a username: lowercase, permanent)")
    local_name.add_argument("--clear", action="store_true",
                            help="remove the local name; the password stops signing it in")
    local_name.set_defaults(func=cmd_user_set_local_name)

    reset = user_sub.add_parser(
        "reset-password",
        help="generate a new temp password, delete that user's sessions, leave service tokens")
    reset.add_argument("username")
    reset.set_defaults(func=cmd_user_reset_password)

    # The help text carries the asymmetry because that is where an operator
    # looks when they are surprised by it (TASK-1113's own acceptance).
    disable = user_sub.add_parser(
        "disable",
        help="set disabled_at, delete sessions, refuse that user's tokens AND "
             "disable every agent linked to them")
    disable.add_argument("username")
    disable.add_argument("--force", action="store_true",
                         help="suppress the last-owner warning")
    disable.set_defaults(func=cmd_user_disable)

    enable = user_sub.add_parser(
        "enable",
        help="clear disabled_at (restores no password). NOT symmetric with "
             "`disable`: a person's agents are NOT re-enabled with them, because "
             "one may have been taken down for its own reasons. Re-enable each "
             "agent deliberately.")
    enable.add_argument("username")
    enable.set_defaults(func=cmd_user_enable)

    listing = user_sub.add_parser("list", help="every account")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_user_list)

    show = user_sub.add_parser("show", help="one account; never prints a secret")
    show.add_argument("username")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_user_show)

    # -- bot ----------------------------------------------------------------- #
    # -- session ------------------------------------------------------------- #
    session_p = top.add_parser(
        "session", help="browser sessions (staging only; see ATLAS_ALLOW_SESSION_MINT)")
    session_sub = session_p.add_subparsers(dest="command", required=True)
    mint = session_sub.add_parser(
        "mint",
        help="mint a real browser session for an account, with no password involved")
    mint.add_argument("username", help="the account to sign in as")
    mint.add_argument("--minutes", type=int, default=auth.SESSION_MINT_DEFAULT_MINUTES,
                      help="lifetime in minutes (default %d, max %d)"
                           % (auth.SESSION_MINT_DEFAULT_MINUTES, auth.SESSION_MINT_MAX_MINUTES))
    mint.add_argument("--allow-owner", action="store_true", dest="allow_owner",
                      help="permit an owner account — an owner session sees the private set")
    mint.set_defaults(func=cmd_session_mint)

    bot = top.add_parser("bot", help="machine principals")
    bot_sub = bot.add_subparsers(dest="command", required=True)
    bot_add = bot_sub.add_parser("add", help="an is_service account with no usable password")
    bot_add.add_argument("label", help="names the agent, e.g. sync-backlog")
    bot_add.add_argument("--name", default=None, help="display name")
    bot_add.add_argument("--person", default=None,
                         help="username of the person accountable for this agent "
                              "(ADR 0034). Optional; `bot link` sets it later.")
    bot_add.set_defaults(func=cmd_bot_add)

    bot_link = bot_sub.add_parser("link", help="record the person an agent belongs to")
    bot_link.add_argument("bot", help="the `bot add` label")
    bot_link.add_argument("person", help="the person's username")
    bot_link.set_defaults(func=cmd_bot_link)

    bot_unlink = bot_sub.add_parser("unlink", help="clear the person an agent belongs to")
    bot_unlink.add_argument("bot", help="the `bot add` label")
    bot_unlink.set_defaults(func=cmd_bot_unlink)

    bot_list = bot_sub.add_parser("list", help="agent accounts and who is accountable for each")
    bot_list.add_argument("--json", action="store_true")
    bot_list.set_defaults(func=cmd_bot_list)

    # -- token --------------------------------------------------------------- #
    token = top.add_parser("token", help="atlas_svc_ service tokens")
    token_sub = token.add_subparsers(dest="command", required=True)

    token_add = token_sub.add_parser("add", help="mint one; the raw value is shown once")
    token_add.add_argument("owner", help="the `bot add` label that owns it")
    token_add.add_argument(
        "--scopes", required=True,
        help="comma-separated, from %s. Required on purpose: an unscoped token is a "
             "broken token, not a superuser." % (",".join(models_auth.SCOPES),))
    token_add.add_argument("--label", default=None, help="what uses it")
    token_add.add_argument("--days", type=int, default=auth.SERVICE_TOKEN_DEFAULT_DAYS,
                           help="lifetime in days (0 = never expires, discouraged)")
    token_add.add_argument("--out", default=None, metavar="PATH|-",
                           help="write the raw token to a new 0600 file, or `-` for the raw "
                                "value alone on stdout (redirect it into a file)")
    token_add.add_argument("--print", dest="print_token", action="store_true",
                           help="show the raw token on this terminal; never from an agent")
    token_add.set_defaults(func=cmd_token_add)

    token_mint = token_sub.add_parser(
        "mint", help="mint an agent token for a PERSON; written only to --out, never printed")
    token_mint.add_argument("--user", required=True,
                            help="the person: a username, <idp>:<subject>, or an IdP account's email")
    token_mint.add_argument("--purpose", required=True,
                            help="names the agent <person>-<purpose>, e.g. claude-code")
    token_mint.add_argument("--scopes", default="read",
                            help="comma-separated, capped by the person's role (default read)")
    token_mint.add_argument("--days", type=int, default=auth.SERVICE_TOKEN_DEFAULT_DAYS,
                            help="lifetime, 1..%d" % auth.SERVICE_TOKEN_DEFAULT_DAYS)
    token_mint.add_argument("--out", required=True, metavar="PATH",
                            help="a new 0600 file to write the raw token to (never `-`)")
    token_mint.set_defaults(func=cmd_token_mint)

    token_list = token_sub.add_parser("list", help="live tokens; never prints a digest")
    token_list.add_argument("--all", action="store_true", help="include revoked tokens")
    token_list.add_argument("--json", action="store_true")
    token_list.add_argument("--user", default=None,
                            help="only this person's agents' tokens (or this bot's)")
    token_list.set_defaults(func=cmd_token_list)

    token_revoke = token_sub.add_parser("revoke", help="permanent, effective next request")
    token_revoke.add_argument("token", nargs="?", default=None,
                              help="the token id or its prefix (never the value)")
    token_revoke.add_argument("--user", default=None,
                              help="revoke every live token of this person's agents "
                                   "(or only TOKEN, checked to be one of them)")
    token_revoke.set_defaults(func=cmd_token_revoke)

    # -- rbac ---------------------------------------------------------------- #
    rbac_p = top.add_parser("rbac", help="project RBAC (ADR 0041); read-only planning")
    rbac_sub = rbac_p.add_subparsers(dest="command", required=True)
    plan = rbac_sub.add_parser(
        "plan", help="today's vs ATLAS_RBAC=projects effective permission per account and "
                     "project; writes nothing")
    plan.add_argument("--all", action="store_true", help="list unchanged rows too")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=cmd_rbac_plan)

    # -- audit (STORY-oss-16) ------------------------------------------------ #
    audit_p = top.add_parser("audit", help="the read audit (audit_read)")
    audit_sub = audit_p.add_subparsers(dest="command", required=True)
    purge = audit_sub.add_parser(
        "purge", help="delete audit_read rows older than ATLAS_AUDIT_RETENTION_DAYS "
                      "(unset: keep forever, delete nothing); run it from cron")
    purge.set_defaults(func=cmd_audit_purge)

    # -- guardrails (STORY-atlas-guardrails-per-env) -------------------------- #
    gr_p = top.add_parser("guardrails",
                          help="agent guardrails per environment, as data (guardrails.py)")
    gr_sub = gr_p.add_subparsers(dest="command", required=True)
    gr_val = gr_sub.add_parser("validate", help="check a matrix file; writes nothing")
    gr_val.add_argument("file", help="the matrix, JSON or YAML")
    gr_val.set_defaults(func=cmd_guardrails_validate)
    gr_apply = gr_sub.add_parser(
        "apply", help="store the matrix on atlas-system: its document, one constraint intent "
                      "per environment and a Proposed ADR (idempotent)")
    gr_apply.add_argument("file", help="the matrix, JSON or YAML")
    gr_apply.add_argument("--dry-run", action="store_true", dest="dry_run",
                          help="list what would change and write nothing")
    gr_apply.add_argument("--adr-prefix", default="SYS", dest="adr_prefix",
                          help="ADR id prefix when atlas-system has no ADRs yet (default SYS)")
    gr_apply.set_defaults(func=cmd_guardrails_apply)
    gr_show = gr_sub.add_parser("show", help="print the applied matrix (normalised JSON)")
    _add_crunch_flags(gr_show, "the matrix")
    gr_show.set_defaults(func=cmd_guardrails_show)
    gr_exp = gr_sub.add_parser("export", help="agent policy files from the matrix")
    gr_exp.add_argument("--format", required=True,
                        choices=["claude-code", "github-branch-protection"])
    gr_exp.add_argument("--env", action="append", default=None,
                        help="only this environment (repeatable)")
    gr_exp.add_argument("--from", dest="from_file", default=None, metavar="FILE",
                        help="read the matrix from FILE instead of the applied one")
    gr_exp.add_argument("--out", required=True, metavar="PATH|-",
                        help="the file to write, or - for stdout")
    _add_crunch_flags(gr_exp, "export the matrix")
    gr_exp.set_defaults(func=cmd_guardrails_export)

    # -- cicd (STORY-atlas-first-cicd) ---------------------------------------- #
    ci_p = top.add_parser("cicd", help="a first CI/CD pipeline from the guardrails matrix "
                                       "(cicd.py); writes files, pushes nothing")
    ci_sub = ci_p.add_subparsers(dest="command", required=True)
    ci_gen = ci_sub.add_parser(
        "generate", help="write the pipeline, its settings notes (and on GitHub the branch "
                         "protection export) under --out")
    ci_gen.add_argument("--platform", required=True, choices=["github", "gitlab", "azure"])
    ci_gen.add_argument("--from-guide", action="store_true", dest="from_guide",
                        help="build the matrix and the CI/CD input from the recorded setup guide")
    ci_gen.add_argument("--matrix", default=None, metavar="FILE",
                        help="a guardrails matrix file (JSON or YAML) instead of the guide")
    ci_gen.add_argument("--answers", default=None, metavar="FILE",
                        help="with --matrix: the guide's CI/CD answers (data/cicd/example-answers.yaml)")
    ci_gen.add_argument("--out", required=True, metavar="DIR",
                        help="the folder to write into (files at their repository paths)")
    ci_gen.add_argument("--force", action="store_true", help="overwrite files that exist in --out")
    ci_gen.add_argument("--record-adr", action="store_true", dest="record_adr",
                        help="also record one Proposed ADR for this pipeline on atlas-system")
    ci_gen.add_argument("--adr-prefix", default="SYS", dest="adr_prefix",
                        help="ADR id prefix when atlas-system has no ADRs yet (default SYS)")
    _add_crunch_flags(ci_gen, "the pipeline")
    ci_gen.set_defaults(func=cmd_cicd_generate)

    # -- backup (STORY-atlas-crunch-mode) ------------------------------------- #
    bk_p = top.add_parser("backup", help="verified logical backups of the Atlas database "
                                         "(crunch_backup.py)")
    bk_sub = bk_p.add_subparsers(dest="command", required=True)
    bk_run = bk_sub.add_parser(
        "run", help="pg_dump, checksum, restore into a temporary database and compare counts; "
                    "or --verify-only an existing backup directory")
    bk_run.add_argument("--reason", default="manual",
                        choices=["manual", "per_deploy", "once", "scheduled", "routine"],
                        help="manual (default) and routine always run (routine keeps the newest "
                             "ATLAS_BACKUP_KEEP routine backups); the others only while the "
                             "active crunch's backup policy is theirs")
    bk_run.add_argument("--verify-only", default=None, metavar="DIR", dest="verify_only",
                        help="take nothing: verify every backup in DIR (or one .dump file)")
    bk_run.add_argument("--dir", default="/var/lib/atlas/backups",
                        help="where backups are written (default /var/lib/atlas/backups)")
    bk_run.add_argument("--pg-bin", default=None, dest="pg_bin", metavar="DIR",
                        help="directory holding pg_dump and pg_restore (default: PATH)")
    bk_run.set_defaults(func=cmd_backup_run)
    # OSS-26 C: restore and rollback (backup_restore.py).
    bk_restore = bk_sub.add_parser(
        "restore", help="restore a backup over the live database: checksum, restore into a "
                        "staging database and count, then swap it in; the old database is kept "
                        "as a safety copy. Refuses while anything is connected (stop atlas-api)")
    bk_restore.add_argument("dump", help="`latest`, a file name in --dir, or a path to a .dump")
    bk_restore.add_argument("--dir", default="/var/lib/atlas/backups",
                            help="where backups are (default /var/lib/atlas/backups)")
    bk_restore.add_argument("--dry-run", action="store_true", dest="dry_run",
                            help="checksum, restore into a staging database and count, report "
                                 "what a real run would do; change nothing")
    bk_restore.add_argument("--pg-bin", default=None, dest="pg_bin", metavar="DIR",
                            help="directory holding pg_restore (default: PATH)")
    bk_restore.set_defaults(func=cmd_backup_restore)
    bk_rollback = bk_sub.add_parser(
        "rollback", help="list the safety copies a restore left, or --to one to put it back "
                         "(the current database is set aside, nothing is dropped)")
    bk_rollback.add_argument("--to", default=None, metavar="NAME",
                             help="the safety copy (<db>_prerestore_<UTC>) to put back")
    bk_rollback.set_defaults(func=cmd_backup_rollback)

    # -- schema -------------------------------------------------------------- #
    init = top.add_parser("init-db", help="create missing auth tables (idempotent)")
    init.set_defaults(func=cmd_init_db)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except AdminError as exc:
        print("atlas admin: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        print("atlas admin: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
