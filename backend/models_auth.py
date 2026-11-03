"""
Atlas auth + visibility schema — NEW TABLES ONLY (TASK-966, TASK-973).

Two stories' tables land in this ONE module on purpose, because they land in ONE
migration:

  * STORY-atlas-app-login  / TASK-966 -> `auth_user`, `auth_session`,
    `auth_login_throttle`, `auth_service_token`, `auth_event`   (ADR 0025)
  * STORY-atlas-node-visibility / TASK-973 -> `node_visibility`  (ADR 0026)

There is no Alembic in this repo (Data-132100 stays owed) and the deployed
Postgres is persistent and populated. `create_all()` creates MISSING TABLES on a
populated Postgres perfectly well; what it cannot do is add a COLUMN to a table
that already has rows (AGENTS.md:92-93, and the shim `_reconcile_graph_schema()`
in app.py exists because that has already bitten). So:

  1. **Nothing here touches an existing table.** No column is added to
     `component`, `gate`, `requirement`, `decision`, `node`, `edge`, `probe` or
     `probe_result`. Every table below is brand new.
  2. **Every constraint that can only be created WITH the table is created with
     the table.** Postgres cannot add a CHECK once a violating row exists
     (TASK-972 item 3), so the `auth_service_token.scopes` domain, the
     bots-hold-no-password rule and the session `kind` interlock are all in the
     CREATE TABLE, not in Python.
  3. **Future columns must be additive and NULLABLE.** That is the rule this
     module lives under from here on: `_reconcile_graph_schema()` in app.py can
     add a nullable column to a populated table and nothing else. A NOT NULL or
     a narrowed CHECK added later is a hand-run `ALTER TABLE` against production.

Deliberate non-relationships
----------------------------
**No foreign key points from any table here into `node`, `component`, or either
existing metadata.** Postgres enforces foreign keys natively, so a cross-Base FK
would turn every fixture in `verify_object_graph.py` into a minefield the
moment it creates an auth row without a matching graph row. FKs BETWEEN the
auth tables are expected and used.

`node_visibility.node_id` is bare text for a second, stronger reason:
`ingest_backlog._purge_workgraph()` and `ingest_graph._reconcile_nodes()` delete
and rebuild nodes on every boot. **A marking must survive the ingest wiping the
node it marks.** An orphan row is the correct, fail-safe outcome and is never
garbage-collected automatically.

The reseed asymmetry (read this before "fixing" app.py)
-------------------------------------------------------
`ATLAS_RESEED=1` drops `app.Base` and `models_graph.Base` and MUST NOT drop the
Bases here. Accounts, service tokens and privacy markings survive a graph
reseed; the graph does not. That is asymmetric with everything else in
`startup()` and therefore needs an explicit comment at the drop_all calls, or
someone "fixes" it and destroys every account and every service token — freezing
the ingest — on the next reseed. The inverse was also wrong: putting auth under
an existing Base means a reseed deletes all accounts.

One hazard worth knowing about
------------------------------
`models_graph.py:296-301` registers a **global** `before_flush` listener on the
SQLAlchemy `Session` class. It fires for any session opened anywhere in-process,
including every auth write. It filters on `isinstance(obj, Node)` so rows here
pass through harmlessly — but a failed login is a WRITE (the throttle row), so
that hook now runs on unauthenticated traffic whose rate an attacker controls.
Measure it under the TASK-965 rate-limit test rather than assuming it is free.

This module ships TABLES AND NOTHING THAT READS THEM. Hashing parameters and the
breach-list are TASK-965; the gate and the routes are TASK-967; minting is
TASK-970; the visibility resolver is TASK-974.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Engine,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    """Timezone-aware UTC. Every timestamp column here is tz-aware."""
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Base #3 — the auth tables. Alongside `app.Base` (v0 Component/Gate/...) and
# `models_graph.Base` (ADR 0019 Node/Edge). Separate metadata is the whole point:
# it is what makes the reseed asymmetry above expressible at all.
# --------------------------------------------------------------------------- #
class Base(DeclarativeBase):
    metadata = MetaData()


# --------------------------------------------------------------------------- #
# Base #4 — the ONE visibility table, on its own metadata.
#
# TASK-973 requires `node_visibility` to sit under its OWN DeclarativeBase,
# separately addressable from the auth metadata. It is declared HERE rather than
# in its own module only because the DDL must reach the populated Postgres in a
# single pass — "the schema moves once" (TASK-966 §"The schema moves ONCE").
# `backend/models_visibility.py` can later be a three-line re-export
# (`from models_auth import VisibilityBase as Base, NodeVisibility`) without any
# schema change at all, because the metadata it names already exists.
# --------------------------------------------------------------------------- #
class VisibilityBase(DeclarativeBase):
    metadata = MetaData()


# --------------------------------------------------------------------------- #
# The role domain, and the fail-closed rule that goes with it.
#
# DELIBERATELY NOT A CHECK CONSTRAINT. TASK-972 item 2 fixes the domain at
# exactly {owner, member} AND says any unrecognised value resolves to `member`.
# Those two sentences are only compatible if an unrecognised value can be
# STORED and then resolved down — a DB CHECK would instead make a third role
# added later a hand-run ALTER TABLE against a populated Postgres, which is the
# exact failure mode this whole task exists to avoid. So the domain is enforced
# in code, fail-closed, and the DB stays widenable.
# --------------------------------------------------------------------------- #
ROLE_OWNER = "owner"
ROLE_MEMBER = "member"
ROLES = (ROLE_OWNER, ROLE_MEMBER)


def resolve_role(value: str | None) -> str:
    """Fail closed: anything that is not exactly `owner` is a `member`."""
    return ROLE_OWNER if value == ROLE_OWNER else ROLE_MEMBER


# --------------------------------------------------------------------------- #
# Who may be linked to whom (ADR 0034, TASK-1112).
#
# ONE rule in ONE place: `admin bot link` calls it today, and the self-service
# credential screen (STORY-atlas-agent-credentials) sets the same link tomorrow.
# A second copy is a second thing to drift, and this decides who inherits whose
# grants once TASK-1114 lands - so a wrong answer here is an access decision,
# not a cosmetic one.
#
# ROLE IS NOT INHERITED and this function is not where that is enforced; it
# cannot be, because the link is only a recorded fact. The resolver is what must
# read grants and never `role`. Said here because this is where somebody looking
# for the rule will land first.
# --------------------------------------------------------------------------- #
def refuse_person_link(bot: "AuthUser", person: "AuthUser") -> str | None:
    """Why `bot` may not be linked to `person` - or None if it may."""
    if not bot.is_service:
        return ("%r is a person, not an agent: this records which person an AGENT "
                "belongs to, and a person belongs to nobody" % bot.subject)
    if person.is_service:
        return ("%r is an agent, not a person: an agent may not stand behind "
                "another agent, or a disabled person's reach would survive one "
                "hop away from them" % person.subject)
    return None


# --------------------------------------------------------------------------- #
# Service-token scopes.
#
# The private literal is `private`. There is no `bypass` and never will be.
# Absent `private` => member-equivalent visible set; present => owner-equivalent.
# An UNSCOPED token is a BROKEN token, not a superuser — hence `scopes <> ''`.
#
# `write` was added by TASK-1085/1086 (2026-08-15) — the tier for the nine
# mutating routes that create or edit a row (`auth.WRITE_SCOPE_ROUTES` is the
# one authoritative map of which route needs which tier). `admin` covers every
# DELETE. `has_scope` is plain literal membership, on purpose — there is no
# `read < write < admin` implication, so a caller needing both is minted
# `write,admin` explicitly.
#
# `ingest` stays in the tuple, UNUSED. It is named for the boot-time
# reconciler TASK-1058 deleted; repurposing it for `write` would enshrine a
# name for something that no longer exists. Removing it outright is a
# separate, riskier migration (existing tokens may carry it) and not this
# task's job.
#
# The domain CHECK must exist AT TABLE-CREATION TIME (TASK-972 item 3: Postgres
# cannot add a CHECK once a violating row exists). A plain `~` regex match —
# used to compile per-dialect against a portable `replace()`-based fallback
# while the verify harness also ran on SQLite (TASK-1044 removed that
# fallback: Postgres is the only dialect this ever needs to satisfy).
#
# Widening `SCOPES` does NOT widen the live database's CHECK constraint —
# `_reconcile_graph_schema()` only reconciles `models_graph` tables, and
# there is no equivalent for `models_auth`. See `migrate_add_write_scope.py`,
# and read its docstring's deploy ORDER before touching either.
# --------------------------------------------------------------------------- #
SCOPES = ("read", "write", "ingest", "admin", "private")
SCOPE_PRIVATE = "private"
SCOPE_WRITE = "write"

_SCOPES_REGEX = "^({alts})(,({alts}))*$".format(alts="|".join(SCOPES))


# --------------------------------------------------------------------------- #
# What a person may mint for their OWN agent (TASK-1115).
#
# `STORY-atlas-agent-credentials` asks for "a token cannot be minted with scopes
# the creating user does not hold". THAT IS UNIMPLEMENTABLE AS WRITTEN, and
# finding out why is the same shape of premise error that cost this epic six
# days: a human holds NO scopes at all. `auth._resolve_session` builds every
# session principal with `scopes=()`, and live `/api/me` for the owner returned
# `[]` on 2026-08-16. Read literally, nobody could mint anything.
#
# So the ceiling is derived from ROLE, which is the only capability axis a human
# actually has (owner decision, 2026-08-16):
#
#     member  ->  read, write        an agent that reads the board and files work
#     owner   ->  read, write, admin
#
# `admin` covers every DELETE in `auth.WRITE_SCOPE_ROUTES`, so a member cannot
# self-promote by minting a token more capable than themselves. `ingest` is
# absent from both tiers deliberately — it names a boot-time reconciler that
# TASK-1058 deleted, and handing out a scope for something that does not exist
# is how dead vocabulary becomes live permission.
#
# THIS IS THE "MAY DO" AXIS ONLY. What that agent may SEE comes from grant
# inheritance (ADR 0034 / TASK-1114) and needs no ceiling of its own here: an
# agent inherits exactly its person's grants, so it can never be given
# visibility its operator lacks. The two axes stay independent (ADR 0033), and
# the grant half is asserted by test rather than assumed.
# --------------------------------------------------------------------------- #
SELF_MINTABLE_SCOPES: dict[str, tuple[str, ...]] = {
    ROLE_MEMBER: ("read", "write"),
    ROLE_OWNER: ("read", "write", "admin"),
}


def self_mintable_scopes(role: str | None) -> tuple[str, ...]:
    """The scopes an account of this role may put on its own agent's token.

    Fail closed through `resolve_role`: an unrecognised role resolves to member
    and therefore to the narrower tier, never to the wider one.
    """
    return SELF_MINTABLE_SCOPES[resolve_role(role)]


# --------------------------------------------------------------------------- #
# auth_user — the principal. Humans and bots in ONE table, told apart by
# `is_service`, which is also what stops a bot row ever reaching /auth/login.
# --------------------------------------------------------------------------- #
class AuthUser(Base):
    __tablename__ = "auth_user"

    # uuid4 as text, opaque, stable, NEVER REISSUED (TASK-972 item 1). Not an
    # autoincrement integer: an id that can be reissued after a delete hands a
    # revoked invitee's grants to the next invitee. The corollary is enforced by
    # convention, not by DDL — revocation is `disabled_at`, never a row DELETE.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # (idp, subject) is composite and never bare `subject` (ASVS 6.8.1): a `sub`
    # from a future provider can then never collide with a local username. Today
    # the pair is ('local', 'alex'). This one column is the entire reason a
    # second credential type stays cheap to add later.
    idp: Mapped[str] = mapped_column(String(32), nullable=False, default="local",
                                     server_default="local")
    # THE USERNAME. Lowercased, and IMMUTABLE — a permanent identifier, not a
    # display name. Renames are NOT supported: a rename is a new row plus a
    # re-grant, because `id` (not `subject`) is what the visibility tables store.
    subject: Mapped[str] = mapped_column(String(64), nullable=False)

    # Contact/display only. NEVER an authorization key, never looked up at login
    # — the login form does not accept an email at all.
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Domain {owner, member}; unknown resolves to member. See resolve_role().
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_MEMBER,
                                      server_default=ROLE_MEMBER)

    is_service: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                             server_default=false())
    # Names the agent, for is_service rows.
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # THE PERSON ACCOUNTABLE FOR AN AGENT (ADR 0034, TASK-1112). Set only on
    # `is_service` rows. NULL on a bot means "nobody has said yet", never
    # "nobody": the bots that predate this column must be linked by a judgement
    # call, and a migration guessing an operator from a bot's name would write
    # an accountability record nobody decided.
    #
    # NOT a ForeignKey, and the reason is this repo's migration vocabulary
    # rather than taste. `_reconcile_graph_schema()` (app.py) is the only thing
    # that reaches the populated live Postgres, and it emits exactly
    # `ALTER TABLE auth_user ADD COLUMN person_user_id VARCHAR(36)` - no
    # DEFAULT, no CHECK, no REFERENCES. Declaring the FK here would create it on
    # every fresh test database and never on the one deployment that matters, so
    # the suite would prove a constraint live does not have. A dangling link is
    # fail-safe anyway, for the same reason `NodeGrant.user_id` is bare text:
    # resolution is by id, and an id matching no row yields nothing - which,
    # once TASK-1114 lands, means an empty inherited grant set.
    #
    # "Only a service row may carry this, and only pointing at a person" is
    # likewise enforced in code (`refuse_person_link` below), not as a CHECK:
    # Postgres cannot add a CHECK to a populated table, so a constraint written
    # here would exist on fresh databases only. Same trap, one line down.
    person_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # HMAC(pepper, argon2id PHC string) — the shape is TASK-965's to decide; this
    # column only has to be wide enough and to never hold anything in clear.
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    hash_scheme: Mapped[str | None] = mapped_column(String(48), nullable=True)

    # Defaults TRUE at the DATABASE level, on purpose. A row minted by any path
    # — `admin.py user add`, `admin.py user reset-password`, a future bootstrap,
    # a hand-typed INSERT — starts forced. Defaulting the other way means one
    # forgotten flag is a permanent temp password. Read PER REQUEST by the gate
    # (TASK-967); never a claim inside a cookie, because a copied flag is a flag
    # that cannot be revoked.
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                                       server_default=true())
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                                 nullable=True)
    # Absolute deadline of a minted temp credential (72 h, TASK-965/970), checked
    # server-side and cleared on a successful change. There is no "pending
    # invite" state — the row and its credential exist together.
    temp_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                             nullable=True)
    # Revocation. Set, never DELETE the row.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=_utcnow, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("idp", "subject", name="uq_auth_user_idp_subject"),
        # Bots never hold a password. This is what stops a service row ever
        # reaching POST /auth/login.
        CheckConstraint("is_service = false OR password_hash IS NULL",
                        name="ck_auth_user_service_has_no_password"),
        # Local human usernames are stored lowercased, so the unique constraint
        # is a real one and not a case-folding accident.
        CheckConstraint("is_service OR idp <> 'local' OR subject = lower(subject)",
                        name="ck_auth_user_local_subject_lowercase"),
    )


# --------------------------------------------------------------------------- #
# auth_session — the pre-login CSRF session and the real session are ONE
# mechanism, told apart by `kind`. That makes "rotate the session at login" a
# delete-and-insert in one place.
#
# The row holds NOTHING about must_change_password and NOTHING about role. Both
# are re-read from auth_user on every request: a stale copy of
# must_change_password=false is a complete bypass of the forced-change interlock.
# --------------------------------------------------------------------------- #
class AuthSession(Base):
    __tablename__ = "auth_session"

    # hmac(sid). THE RAW SESSION ID IS NEVER STORED.
    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    # No ON DELETE clause on purpose: users are never deleted, so a DELETE that
    # somehow happens should be REFUSED by the FK rather than silently cascade.
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("auth_user.id", name="fk_auth_session_user"), nullable=True,
    )
    # hmac of the synchronizer token. SameSite alone cannot be the CSRF control
    # here — sibling subdomains on one domain are same-site.
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=_utcnow, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                   default=_utcnow, server_default=func.now())
    # NOT NULL by choice: a session with no absolute deadline is a session that
    # never expires.
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # HOW THIS SESSION WAS ESTABLISHED (TASK-1454): 'password' (the login form, or
    # the change that rotated such a session), 'sso' (a redirect provider's
    # callback) or 'minted' (`session mint`, staging only). NULL on a pre-session
    # and on every row older than this column. Read only by
    # `auth.session_auth_method`, which says what a NULL means; the forced-change
    # gate keys on it once a linked account can sign in with a password. Nullable,
    # no default, no CHECK: `main._reconcile_graph_schema` adds it to a populated
    # table and can emit nothing else.
    auth_method: Mapped[str | None] = mapped_column(String(16), nullable=True)

    __table_args__ = (
        # Also pins the `kind` domain to exactly {pre, full}.
        CheckConstraint(
            "(kind = 'pre' AND user_id IS NULL) OR (kind = 'full' AND user_id IS NOT NULL)",
            name="ck_auth_session_kind_user",
        ),
        # "kill every session for this user" (password change, disable) and the
        # story's `select count(*) from auth_session where user_id=...` both
        # scan this.
        Index("ix_auth_session_user_id", "user_id"),
        Index("ix_auth_session_absolute_expires_at", "absolute_expires_at"),
    )


# --------------------------------------------------------------------------- #
# auth_login_throttle — keyed on hmac(lower(SUBMITTED username)), NOT on
# auth_user.id, and deliberately so. If the counter lived on the user row, a
# submitted username with no matching row would take a visibly different code
# path from one with a row, and the throttle itself becomes the enumeration
# oracle TASK-965's constant-time work was spent closing. Keying on the
# submitted string means an unknown username is throttled exactly like a known
# one, writes exactly one row, and costs exactly the same.
#
# Rows are self-healing: `retry_after` is an ABSOLUTE timestamp, and a row whose
# `last_failed_at` is older than the window is treated as absent and may be
# pruned by any writer. No FK, by construction — the key is not a user.
# --------------------------------------------------------------------------- #
class AuthLoginThrottle(Base):
    __tablename__ = "auth_login_throttle"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                              server_default=text("0"))
    first_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                      default=_utcnow, server_default=func.now())
    last_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                     default=_utcnow, server_default=func.now())
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("failed_count >= 0", name="ck_auth_login_throttle_count_nonneg"),
        Index("ix_auth_login_throttle_last_failed_at", "last_failed_at"),
    )


# --------------------------------------------------------------------------- #
# auth_service_token — the machine credential. `prefix` is stored in clear and
# is how a presented token is located; `token_hash` is what is compared. THE RAW
# TOKEN IS NEVER STORED.
# --------------------------------------------------------------------------- #
class AuthServiceToken(Base):
    __tablename__ = "auth_service_token"

    # Not in the spec's column list; added for the same reason auth_user has one
    # — an opaque, never-reissued handle for `admin.py token revoke <id>` that
    # does not force the operator to paste a credential prefix.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[str] = mapped_column(String(64), nullable=False)

    # One bot user per caller, never shared. FK BETWEEN auth tables — expected.
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("auth_user.id", name="fk_auth_service_token_owner"),
        nullable=False,
    )
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=_utcnow, server_default=func.now())
    # Nullable, but TASK-968 mints a default 365-day value. Expiry that is
    # silent is how the board freezes, so `last_used_at` exists to drive a
    # board-visible near-expiry / long-unused warning.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("prefix", name="uq_auth_service_token_prefix"),
        # NOW OR NEVER — Postgres cannot add a CHECK once a violating row exists.
        CheckConstraint(f"scopes ~ '{_SCOPES_REGEX}'", name="ck_auth_service_token_scopes"),
        CheckConstraint("scopes <> ''", name="ck_auth_service_token_scopes_nonempty"),
        Index("ix_auth_service_token_owner_user_id", "owner_user_id"),
    )


# --------------------------------------------------------------------------- #
# auth_event — append-only audit.
#
# NEVER LOG (ASVS 16.2.5): the submitted password, the minted temp password, the
# raw session cookie, a raw service token. A submitted username lands in this
# table and is attacker-controlled, so `reason` and `detail` MUST be encoded on
# write — a raw newline in one of them is log injection.
#
# `user_id` is bare text with NO foreign key: an audit row must never be blocked,
# rewritten or cascaded by referential state, and a failed login very often has
# no user at all.
# --------------------------------------------------------------------------- #
EVENTS = (
    "user_created",
    "user_disabled",
    "user_enabled",
    "bot_person_linked",
    "bot_person_unlinked",
    # Access administration. These live HERE and not in `graph_event`
    # deliberately: `graph_event` has a read route (`GET /api/events`) whose
    # access filter serves an event to everyone who can still see the node it
    # names — so recording "X was granted the property project" there would
    # make that fact readable by every OTHER grantee. Who may see what is an
    # operator question, and `auth_event` is the operator's log: no read
    # route, no filter to get wrong.
    "node_visibility_changed",
    "node_grant_added",
    "node_grant_removed",
    "password_reset_by_operator",
    "login_ok",
    "login_fail",
    "forced_change_completed",
    "password_changed",
    "throttle_engaged",
    "lockout_engaged",
    "lockout_released",
    "breach_list_reject",
    "breach_list_unavailable",
    "token_created",
    "token_used_first_time",
    "token_revoked",
    "csrf_reject",
    "bad_origin",
    # GitHub identity + repo access (github_link.py). Who linked which GitHub
    # account, and who approved or revoked repo access for it, is an operator
    # question like the grant events above: no read route, no filter to get wrong.
    "github_linked",
    "github_unlinked",
    "github_repo_invited",
    "github_repo_revoked",
    # Project RBAC (ADR 0041 §3, STORY-oss-11). Who was put in which group and
    # who was bound to which project is an operator question for the same reason
    # as the grant events above.
    "group_created",
    "group_deleted",
    "group_member_added",
    "group_member_removed",
    "project_role_bound",
    "project_role_unbound",
    # Identity providers (ADR 0041 §5-§7, STORY-oss-13). A password login while
    # ATLAS_LOCAL_LOGIN=break-glass writes this beside `login_ok`; linking a local
    # account to an IdP identity is an operator act like the ones above. An OIDC
    # login is `login_ok`/`login_fail` with reason `provider=oidc` / `oidc:<why>`.
    "login_break_glass",
    "user_idp_linked",
    # A linked account's local sign-in name set or cleared by the operator
    # (`atlas-admin user set-local-name`, TASK-1454).
    "user_local_name",
    # A fresh IdP sign-in for a self-service agent credential (TASK-1415,
    # ATLAS_OIDC_STEP_UP). Its failures are `login_fail` with reason
    # `oidc+step-up:<why>`, and a mint without one `<route>:step_up_required`.
    "step_up_ok",
)


class AuthEvent(Base):
    __tablename__ = "auth_event"

    # A surrogate autoincrement is correct HERE (and only here): an audit log
    # wants a monotonic insertion order, and nothing grants rights off this id.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                         default=_utcnow, server_default=func.now())
    # Vocabulary in EVENTS above. NOT a CHECK: a new event name must never be an
    # ALTER TABLE against a populated Postgres, and an unrecognised event name
    # is a logging bug, not a security decision.
    event: Mapped[str] = mapped_column(String(48), nullable=False)

    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # WHO DID IT, for operator actions — distinct from the subject `user_id`.
    actor: Mapped[str | None] = mapped_column(String(64), nullable=True)

    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_auth_event_at", "at"),
        Index("ix_auth_event_event", "event"),
        Index("ix_auth_event_user_id", "user_id"),
    )


# --------------------------------------------------------------------------- #
# github_link — one verified GitHub identity per Atlas account, and the state of
# that account's access to the project's repository.
#
# A NEW TABLE, NOT NEW COLUMNS on auth_user, on purpose: `create_all` creates a
# missing table on the populated deployment, whereas a column on `auth_user`
# would depend on `_reconcile_graph_schema`. Nothing here needs a migration
# script.
#
# `github_id` is the stable key (a login can be renamed, an id cannot); the
# login is stored for display and for the invitation call, and is refreshed on
# every relink. UNIQUE on github_id so two Atlas accounts can never claim one
# GitHub identity. `user_id` is bare text, no FK (same reason as NodeGrant: this
# Base's rows must not be cascaded or blocked by referential state; disabling an
# account never deletes the row).
#
# NO GitHub TOKEN IS STORED. The OAuth access token that proves the identity is
# used once, in memory, to read `/user`, and dropped.
#
# `repo_access` is Atlas's last known view, written by the owner's approve /
# revoke / refresh calls: linked (nothing granted), invited (invitation sent,
# not yet accepted), collaborator. "Not linked" is the absence of a row.
# Not a CHECK, for the usual reason; the vocabulary is below and enforced in code.
# --------------------------------------------------------------------------- #
GITHUB_ACCESS_LINKED = "linked"
GITHUB_ACCESS_INVITED = "invited"
GITHUB_ACCESS_COLLABORATOR = "collaborator"
GITHUB_ACCESS_VALUES = (GITHUB_ACCESS_LINKED, GITHUB_ACCESS_INVITED,
                        GITHUB_ACCESS_COLLABORATOR)


class GithubLink(Base):
    __tablename__ = "github_link"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    github_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    github_login: Mapped[str] = mapped_column(String(64), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                default=_utcnow, server_default=func.now())
    repo_access: Mapped[str] = mapped_column(String(16), nullable=False,
                                             default=GITHUB_ACCESS_LINKED,
                                             server_default=GITHUB_ACCESS_LINKED)
    # GitHub's invitation id while `invited`, so a revoke can cancel it directly.
    invitation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    repo_access_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    repo_access_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                            nullable=True)


# --------------------------------------------------------------------------- #
# node_visibility (TASK-973 / ADR 0026) — under VisibilityBase, NOT under the
# auth Base.
#
# `node_id` is bare text and NOT a ForeignKey("node.id"): the mark must OUTLIVE
# the node it marks, because the ingest deletes and rebuilds nodes on every
# boot. With an FK and ON DELETE CASCADE a transient ingest failure silently
# unmarks a private subtree and the next request serves it. With a bare string
# the row sits there and re-binds when the node comes back. Orphan rows are
# harmless, are asserted rather than cleaned up, and `admin visibility list`
# flags them for a human.
#
# `set_by` holds an auth_user.id as bare text — also not an FK, because this
# Base must not depend on the auth Base.
# --------------------------------------------------------------------------- #
VISIBILITY_PRIVATE = "private"
VISIBILITY_SHARED = "shared"
VISIBILITY_PRIVATE_SPLICE = "private-splice"
VISIBILITY_VALUES = (VISIBILITY_PRIVATE, VISIBILITY_SHARED, VISIBILITY_PRIVATE_SPLICE)


class NodeVisibility(VisibilityBase):
    __tablename__ = "node_visibility"

    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False)
    set_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                             default=_utcnow, server_default=func.now())

    __table_args__ = (
        # 'private-splice' is included NOW even though TASK-972's A-or-B choice
        # is the owner's and is not yet recorded. Widening a CHECK later is a
        # hand-run DROP CONSTRAINT + ADD CONSTRAINT against a populated
        # Postgres — precisely the migration this pass exists to avoid — while
        # an unused third literal costs nothing and grants nothing. Under
        # option A it is simply never written; under option B the schema is
        # already right. Unknown values are still rejected by the DB, and the
        # TASK-974 resolver must treat any value it does not recognise as
        # private (fail closed).
        CheckConstraint(
            "visibility in ('{}')".format("','".join(VISIBILITY_VALUES)),
            name="ck_node_visibility_visibility",
        ),
    )


# --------------------------------------------------------------------------- #
# node_grant — the per-account half of the access model (STORY-atlas-access-admin).
#
# `node_visibility` answers "is this node private?"; it CANNOT answer "private
# to whom?". One flag per node has exactly two outcomes — owner-only, or
# everyone — so the moment a second account needs one project and not another,
# the flag has no way to say it. This table is that missing half: a row here
# means "this ACCOUNT may see this PRIVATE node", and it is consulted only for
# nodes `node_visibility` marks private. A grant on a shared node is harmless
# and meaningless (everyone already sees it); it is kept rather than rejected
# so that flipping a node shared -> private -> shared does not silently discard
# the grants an owner set up in between.
#
# Both columns are bare text and NOT ForeignKeys, for the two different reasons
# the sibling table's comment gives:
#   * `node_id`  — the mark must OUTLIVE the node, because a boot ingest that
#                  deletes and rebuilds a node must not silently un-grant it.
#                  Same argument as NodeVisibility.node_id, same conclusion.
#   * `user_id`  — this Base must not depend on the auth Base. A deleted
#                  account leaves an orphan row, which grants nothing to
#                  nobody: `_visible_node_ids` resolves grants by the CURRENT
#                  principal's id, so a row naming a user that no longer
#                  exists can never match a caller.
#
# The primary key is the pair, so granting twice is idempotent and revoking is
# a delete by pair — no surrogate id to look up first.
# --------------------------------------------------------------------------- #
class NodeGrant(VisibilityBase):
    __tablename__ = "node_grant"

    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    granted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=_utcnow, server_default=func.now())


# --------------------------------------------------------------------------- #
# Project RBAC (ADR 0041 §1-§4, STORY-oss-11-project-tenancy) — NEW TABLES ONLY.
#
# Four tables, on the auth Base so `create_all` (startup, `admin init-db`)
# creates them on a populated database with no migration. Nothing existing is
# altered: `node_grant`, `auth_user.role` and token scopes keep their meaning.
#
# THEY ARE READ ONLY WHEN `ATLAS_RBAC=projects`. With the default `off` the
# tables exist and are empty, and no code path selects from them
# (`verify_rbac_off_unchanged.py` breaks every one of them on purpose to prove
# it). `rbac.py` is the one reader.
#
# Every id column is bare text with no FK, for NodeGrant's reasons: a project
# id must outlive a node rebuild, and a row naming an account or group that is
# gone grants nothing because it can never match a caller.
#
# Roles and sources are vocabularies enforced IN CODE and fail closed (an
# unrecognised role resolves to NO role, `rbac.resolve_project_role`), not
# CHECKs — the `resolve_role` reasoning above: a CHECK makes widening the
# vocabulary a hand-run ALTER on a populated database.
# --------------------------------------------------------------------------- #
GROUP_SOURCE_LOCAL = "local"
GROUP_SOURCE_IDP = "idp"
GROUP_SOURCES = (GROUP_SOURCE_LOCAL, GROUP_SOURCE_IDP)
PRINCIPAL_USER = "user"
PRINCIPAL_GROUP = "group"
PRINCIPAL_KINDS = (PRINCIPAL_USER, PRINCIPAL_GROUP)


class AuthGroup(Base):
    """A named set of accounts. `source='local'` is administered in Atlas;
    `source='idp'` is materialised from an OIDC groups claim (OSS-13), with
    `idp`/`external_id` naming it there."""
    __tablename__ = "auth_group"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=GROUP_SOURCE_LOCAL)
    idp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=_utcnow, server_default=func.now())


class AuthGroupMember(Base):
    """One membership. `source` is part of the key: IdP sync (OSS-13) replaces a
    user's `idp` rows at login and never touches their `local` ones, so the
    same person may hold both for one group."""
    __tablename__ = "auth_group_member"

    group_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), primary_key=True, default=GROUP_SOURCE_LOCAL)
    added_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                               default=_utcnow, server_default=func.now())

    __table_args__ = (Index("ix_auth_group_member_user_id", "user_id"),)


class ProjectRoleBinding(Base):
    """`principal` holds `role` on the project rooted at `project_id`. One role
    per principal per project: the key is the triple without the role, so a
    re-bind replaces rather than accumulates."""
    __tablename__ = "project_role_binding"

    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    principal_kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    granted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=_utcnow, server_default=func.now())

    __table_args__ = (Index("ix_project_role_binding_principal", "principal_kind", "principal_id"),)


class AuthUserIdpState(Base):
    """ADR 0041 Adoption §1: a side table rather than new columns on `auth_user`.
    Read by `identity` (STORY-oss-13): `break_glass` only while
    ATLAS_LOCAL_LOGIN=break-glass, `last_idp_login_at` only for an IdP-backed
    (`idp <> 'local'`) person. An install with neither never reads it."""
    __tablename__ = "auth_user_idp_state"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    break_glass: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                              server_default=false())
    last_idp_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                               nullable=True)
    # THE LOCAL SIGN-IN NAME of an IdP-backed person (TASK-1454): the username the
    # password form accepts for them, so a personal password is a fallback when the
    # IdP is down. `user link-idp` records the pre-link username here; `user
    # set-local-name` gives one to an account linked before that. NULL for every
    # local account (whose name is `auth_user.subject`) and for an SSO-only one.
    # Read only while `identity.linked_password_login_enabled()`. Stored lowercased,
    # in the local namespace: `identity.local_name_taken` refuses a name a local
    # account (person or agent) holds, the unique index below refuses two linked
    # accounts sharing one, and the login looks up a local account FIRST, so a
    # collision that slipped past both can only refuse the fallback, never sign
    # anybody into the wrong account. Nullable with no CHECK, for the reason
    # `main._reconcile_graph_schema` gives; the index arrives through its index loop.
    local_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("uq_auth_user_idp_state_local_name", "local_name", unique=True),
    )


# --------------------------------------------------------------------------- #
# Initialiser — owned by THIS module, deliberately not wired here.
#
# app.py is another task's file; see the report / TASK-966 for the exact three
# lines it needs. Keeping the call here means "which Bases exist" is one fact in
# one place, so adding a Base later does not need an app.py edit.
# --------------------------------------------------------------------------- #
def metadatas() -> list[MetaData]:
    """Every MetaData this module owns, in creation order.

    Feed this into the generalised `_reconcile_graph_schema()` so a future
    NULLABLE column is added automatically to the populated Postgres instead of
    by hand.
    """
    # STORY-oss-16: `audit_read` lives in its own module on THIS Base; importing it
    # here registers it everywhere this metadata is created or reconciled.
    import models_audit  # noqa: F401
    return [Base.metadata, VisibilityBase.metadata]


def create_all(engine: Engine) -> None:
    """Create the auth + visibility tables if they are missing.

    Idempotent, additive, and safe against the populated deployment: it only
    ever issues CREATE TABLE for a table that does not exist. There is
    deliberately NO drop_all counterpart — see the reseed note in the module
    docstring.
    """
    for md in metadatas():
        md.create_all(engine)
