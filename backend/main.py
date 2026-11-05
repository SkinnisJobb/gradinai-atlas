"""Project Atlas API: the FastAPI app, the gate, startup, and the router table.

This was `app.py` until STORY-oss-04 split it into `db.py`, `deps.py` and `routers/`.
`app.py` is now a compatibility shim, so `uvicorn app:app` and `import app` keep working.
Routes live in `routers/<area>.py`; every one of them is still behind `auth.install`'s
middleware, which wraps the whole app rather than any one router.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager

from fastapi import FastAPI
from sqlalchemy import select, text

import models_graph  # Atlas v2 object-graph schema (ADR 0019); coexists with Component below
import models_auth   # auth_* + node_visibility, own Bases (ADR 0025/0026, TASK-966/973)
# `auth` MUST be imported before anything serves: importing it (and, through it,
# `passwords`) is what makes ATLAS_TOKEN_PEPPER / ATLAS_PASSWORD_PEPPER /
# ATLAS_LOGIN_ORIGINS fail CLOSED — a missing key raises here, before uvicorn
# binds, naming the key. It must never fall back to a generated value: a
# per-boot random pepper logs every user out on every unattended reboot.
import auth         # THE GATE (TASK-967) + service tokens (TASK-968)
import passwords    # handle + password rules shared with admin.py (TASK-1115)
import github_link  # GitHub OAuth identity + repo-access invitations (secrets are files)
import settings     # ATLAS_SEED (STORY-oss-07); the other settings are read where they always were
from db import Base, Component, engine, session
from deps import FRONTEND
from nul_guard import NulGuard  # a NUL byte in the URL is a 400, not Postgres's 500
# NB: `attention` (two-axis derived values, TASK-854/855) is imported LAZILY
# inside the graph handlers, kept that way after TASK-1058 removed the
# `ingest_backlog` cycle that originally motivated it — `attention` itself
# only imports `models_graph`, so the cycle risk is gone, but nothing forces
# eager import back on and the lazy form costs nothing.


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
# `docs_url` / `redoc_url` / `openapi_url` are None: FastAPI's /docs, /redoc and
# /openapi.json do not exist. (`/docs` is the handbook site now, routers/handbook.py.) The gate would answer them 401 anyway — this is
# defence in depth, and it means an AUTHENTICATED caller gets 404 rather than a
# schema dump of every route (TASK-967).
app = FastAPI(title="Project Atlas", version="0.1.0",
              docs_url=None, redoc_url=None, openapi_url=None)

# THE GATE, installed immediately after the constructor. `add_middleware` wraps
# the whole router — every route below, and every route anyone adds later. That
# is the property route-level `Depends` and `FastAPI(dependencies=[...])` cannot
# give: neither sees a mount, and a router-based scheme fails OPEN the moment a
# route is added without one. See backend/auth.py §1.
#
# Added FIRST so that it sits INSIDE the gate (the last middleware added is the outermost):
# a NUL byte in the URL is a 400 for a caller the gate admitted, instead of Postgres's 500;
# everyone else still meets the gate first. backend/nul_guard.py has the why.
app.add_middleware(NulGuard)
auth.install(app, engine, FRONTEND)


#: The advisory lock key every boot and `atlas-migrate upgrade` hold (TASK-1479): 'ATLB', for
#: "Atlas boot". It must differ from every other advisory lock key taken on this database; the
#: only other one is the retrieval reconciler's 'ATLR' (`retrieval/reconciler.py`, LOCK_KEY).
#: `verify_boot_lock.py` checks the two differ.
BOOT_LOCK_KEY = 0x41544C42


@contextmanager
def boot_lock(eng):
    """Hold the boot advisory lock for the body: one schema reconcile and boot seed at a time.

    WHY (TASK-1479, docs/oss/research/openshift-aro.md question 5). The schema reconcile reads the
    catalogue and then issues `ALTER TABLE ... ADD COLUMN` / `CREATE INDEX`, and boot then upserts
    the type registries and seeds an empty graph. Two processes doing that at once (two
    `atlas-api` replicas, or a replica and `atlas-migrate`) can both decide something is missing;
    the loser got a duplicate-object or unique-key error and converged only through startup's
    retry loop, which was written for "Postgres is not up yet". Under this lock the second
    process waits, then finds everything already there.

    NO SWITCH (owner decision, 2026-11-05). With one process the lock is never contended: it is
    one round trip at boot and nothing else changes.

    HOW. A session-level `pg_advisory_lock` on a connection of its own, released in `finally`
    and, should the process die instead, by Postgres when that connection closes. Blocking, not
    `pg_try_advisory_lock` like the retrieval reconciler: a boot that skipped its turn would
    serve before its schema exists. It tries once without blocking only to log that it waits.
    The body uses other connections from the pool; nothing in it takes this key, so it cannot
    wait on itself. A database that is not Postgres has no advisory locks and gets no lock.
    """
    if eng.dialect.name != "postgresql":
        yield
        return
    conn = eng.connect()
    try:
        got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": BOOT_LOCK_KEY}).scalar()
        if not got:
            print("[atlas] boot lock: another process is booting or migrating this database; waiting")
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": BOOT_LOCK_KEY})
        conn.commit()  # the lock is session-level; do not sit idle in a transaction holding it
        try:
            yield
        finally:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": BOOT_LOCK_KEY})
                conn.commit()
            except Exception:  # noqa: BLE001 — a dead session holds no lock; never mask the body's error
                conn.invalidate()
    finally:
        conn.close()


@app.on_event("startup")
def startup() -> None:
    """Boot is now a READ against the graph (TASK-1058,
    STORY-atlas-retire-status-roundtrip). Until this task, `startup()` ran two
    reconcilers here — `_maybe_ingest_backlog()` and `_maybe_ingest_world()` —
    that read the AgentPlanningSetup markdown and `world_manifest.yaml` and
    upserted/deleted `node`/`edge` rows to match what was on disk. Both are
    GONE, not merely disabled: the backlog and the world manifest have been
    Postgres-authoritative since TASK-1052/1053 (see AGENTS.md's "Shipped
    2026-08-10: the backlog lives in Postgres"), and TASK-1056/1057 already
    retired the status round-trip and the transport those reconcilers used.
    With no reconciler left, nothing at boot touches the filesystem for DATA —
    `node_types.ensure_backlog_node_types`/`ensure_world_node_types`/
    `ensure_edge_types` below seed the `node_type`/`edge_type` REGISTRIES
    (schema, not data; see that module's docstring), the one thing boot still
    writes unconditionally.

    `verify_object_graph.py`'s "boot is a read" check (TASK-1058 acceptance)
    snapshots every node id/status/body and the edge set, calls this function
    again, and asserts byte-identical — this docstring is the promise that
    check enforces.
    """
    reseed = os.environ.get("ATLAS_RESEED", "").lower() in ("1", "true", "yes")
    last_err = None
    for attempt in range(1, 31):
        try:
            # TASK-1479: one boot at a time per database. See `boot_lock` for the why.
            with boot_lock(engine):
                if reseed:
                    # DELIBERATELY ASYMMETRIC — DO NOT "FIX" THIS (TASK-966).
                    # ATLAS_RESEED wipes the GRAPH, never the ACCOUNTS. models_auth's
                    # Bases are not dropped: dropping them destroys every account and
                    # every service token, which freezes the ingest.
                    Base.metadata.drop_all(engine)
                    models_graph.Base.metadata.drop_all(engine)
                Base.metadata.create_all(engine)
                models_graph.Base.metadata.create_all(engine)  # node/edge/node_type/probe (ADR 0019, TASK-850)
                models_auth.create_all(engine)  # auth_* + node_visibility (TASK-966/973)
                _reconcile_graph_schema()  # add columns create_all can't (e.g. node_type.family on an existing DB)
                if _CONFIG_WRITES_ON:
                    # STORY-atlas-config-panel phase 2: its own table, only with the flag on, so an
                    # install with it off keeps today's schema exactly (models_config.py).
                    import models_config
                    models_config.Base.metadata.create_all(engine)
                    _reconcile_graph_schema([models_config.Base.metadata])
                models_graph.ensure_search_indexes(engine)  # TASK-1050 full-text GIN indexes — Postgres-only, see its docstring
                _backfill_graph_origin()  # NULL -> 'git' for node/edge.origin (legacy rows only — see docstring)
                # FAIL CLOSED: if the gate cannot read the tables it enforces from,
                # this raises and the retry loop eventually takes uvicorn down. An
                # app that cannot authenticate must refuse to start, never serve open.
                print(f"[atlas] auth: {auth.verify_ready(engine)}")
                with session() as s:
                    # ATLAS_SEED (STORY-oss-07): `homelab`, the default, is exactly the branch
                    # below as it always was; `demo` seeds after the registries; `none` seeds no data;
                    # `self` (TASK-1461) seeds Atlas's own world model, also after the registries.
                    seed_profile = settings.load().seed
                    if seed_profile == "homelab" and (reseed or not s.scalar(select(Component).limit(1))):
                        from seed import seed_all
                        seed_all(s)
                    # Type-registry seeding (TASK-1058) — schema, not data ingest.
                    # Unconditional, every boot, unlike the v0 Component seed just
                    # above: a fresh, empty database must get a usable node_type
                    # registry even when it already has Component rows (or none at
                    # all yet). See node_types.py's own docstring for why this
                    # lives there and not in seed.py.
                    from node_types import ensure_backlog_node_types, ensure_edge_types, ensure_world_node_types
                    ensure_backlog_node_types(s)
                    ensure_world_node_types(s)
                    ensure_edge_types(s)  # edge_type registry (TASK-edge-type-registry) — schema, not enforcement
                    # STORY-atlas-system-node-handbook: asked BEFORE the demo seed writes anything.
                    fresh_graph = s.scalar(select(models_graph.Node.id).limit(1)) is None
                    if seed_profile == "demo":
                        import demo_seed  # only into a graph with no node at all; see its docstring
                        demo_seed.seed_if_empty(s)
                    if seed_profile == "self":
                        import self_seed  # TASK-1461: Atlas's own world model; see its docstring
                        self_seed.seed_if_empty(s)
                    if fresh_graph and seed_profile in ("none", "demo", "self"):
                        import system_node  # the install's own node set + handbook; see its docstring
                        system_node.seed(s)
                    s.commit()
                if _CONFIG_WRITES_ON:
                    # Board-set settings take effect at boot: a READ of config_value (no row written,
                    # no file opened), applied where the real environment does not set the name.
                    import models_config
                    with session() as s:
                        print(f"[atlas] config: board values {models_config.apply_at_boot(s)}")
                _seed_task_id_sequence()  # TASK-1047, see its docstring
                if _RETRIEVAL_ON:
                    # ADR 0044: the opt-in add-on's own tables and loop, after the core schema.
                    # `boot` never raises; a broken add-on reports `unavailable`, never stops Atlas.
                    from retrieval import service as _retrieval_service
                    _retrieval_service.boot(engine)
                return
        except Exception as e:  # noqa: BLE001 — Postgres not up yet, or a real failure
            last_err = e
            # Logged since TASK-1479: a retry used to be silent, so a log could not tell a boot
            # that waited for Postgres from one that lost a race and tried again.
            print(f"[atlas] startup: attempt {attempt} failed ({type(e).__name__}: "
                  f"{(str(e).splitlines() or [''])[0][:200]}); retrying in 2 s")
            time.sleep(2)
    raise RuntimeError(f"DB not ready after retries: {last_err}")


def _reconcile_graph_schema(metadatas: list | None = None) -> None:
    """create_all() creates missing TABLES but never adds COLUMNS — or INDEXES
    — to a table that already exists. On a persistent Postgres deploy predating
    a new column (e.g. node_type.family / .requires_edge — ADR 0019 Addendum),
    the ORM would then SELECT a column the DB lacks and every graph ingest/read
    would fail.

    Bridge the gap idempotently: for each graph table that already exists, ADD
    any column the model declares but the live table is missing, THEN any
    index the model declares but the live table is missing (see the INDEXES
    block below). Additive only — never drops or alters an existing column or
    index. New columns are added nullable (so the ALTER can't fail on
    populated tables); the startup ingest that runs immediately after
    populates them. A lightweight stand-in for Alembic, matching this repo's
    create_all convention (TASK-850 note).

    Generalised in TASK-966 to cover EVERY metadata in the process, not just
    models_graph's: the v0 Component Base, the graph Base, and both of
    models_auth's. `models_auth.metadatas()` is asked rather than the Bases
    being named individually, so adding a Base later needs no edit here.

    ---------------------------------------------------------------------
    WHAT THIS CANNOT DO — the limits, written where they get hit (TASK-1015)
    ---------------------------------------------------------------------
    The owner decided (2026-08-16) to KEEP this shim rather than adopt Alembic.
    The reasoning: every schema change so far has been a new table or a new
    nullable column, which this handles with zero deploy steps; the four
    exceptions were each one-offs with their own script (`migrate_*.py` in
    this directory); and Alembic's headline benefit is a reversible upgrade
    path, which is worth less here than anywhere else because backup is out
    of scope by decision (AGENTS.md) — there is nothing to roll back TO.

    So this is the whole DDL vocabulary, and everything outside it needs a
    hand-written `migrate_*.py` run once against live:

        CREATE TABLE          via create_all, for a table that is missing
        ALTER TABLE ADD COLUMN   nullable, no default, NO CHECK
        CREATE INDEX          see the INDEXES block below

    NOT possible here, in rough order of how likely you are to want it:

      * A NOT NULL column. There is nothing to backfill existing rows with,
        so the ALTER would fail on a populated table.
      * A CHECK constraint on an existing table. Postgres refuses to add one
        that any current row violates, and this loop never emits CHECK at
        all — which is why `node_visibility.visibility` HAS a CHECK (it was
        present at CREATE TABLE) and any column added later would not.
      * Changing a column's type, renaming, or dropping anything.
      * Transforming existing data.

    THE FAILURE MODE THIS SHIM HAS AND ALEMBIC DOES NOT: it only ever ADDS.
    Delete a column from a model and the live table keeps it; change a type
    and the live table keeps the old one. The model and the database then
    disagree SILENTLY — nothing here compares them for equality, so the
    first symptom is a runtime error or a wrong value written, not a failed
    boot. If you remove or retype a model column, write the migration.

    REVISIT THIS DECISION when a change outside the vocabulary above stops
    being a one-off — i.e. the fifth `migrate_*.py`, or the first time two
    of them have to run in a defined order.
    """
    from sqlalchemy import inspect as sa_inspect, text
    if metadatas is None:
        # models_crunch (STORY-atlas-crunch-mode): its tables are created on first activation,
        # never at boot; listed here so that, once they exist, startup and `atlas-migrate
        # upgrade` reconcile their columns like every other table. A missing table is skipped.
        import models_crunch
        metadatas = ([Base.metadata, models_graph.Base.metadata] + models_auth.metadatas()
                     + [models_crunch.Base.metadata])
    insp = sa_inspect(engine)
    tables = set(insp.get_table_names())
    for md in metadatas:
        for table in md.sorted_tables:
            if table.name not in tables:
                continue  # brand-new table — create_all handled it, indexes included
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                coltype = col.type.compile(dialect=engine.dialect)
                with engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {coltype}'))
                print(f"[atlas] schema reconcile: added column {table.name}.{col.name} {coltype}")

            # INDEXES (TASK-1046). Verified against SQLAlchemy 2.0.51's own
            # source before writing this: `create_all` only ever emits an
            # index's DDL as part of that SAME CREATE TABLE statement (the
            # `if table.name not in tables: continue` above is what skips a
            # brand-new table's indexes here — they already exist, created a
            # moment ago by `create_all` itself, not by this loop). A table
            # that ALREADY EXISTED gets no such thing: `create_all` never
            # revisits it, so an `Index(...)` added to a model AFTER that
            # table's first deploy is created on every FRESH test database
            # (where the table and the index are born together) and silently
            # NEVER on the one Postgres that matters. This is the exact gap
            # `verify_workitem_record.py` section D proves shut — a synthetic
            # index added to an already-`create_all`'d table.
            #
            # `checkfirst=True` on `Index.create` is what makes this safe to
            # run every boot: it calls `dialect.has_index(...)` (reflection by
            # name) before emitting `CREATE INDEX`, so an index this loop
            # already created on a previous boot is a no-op here, not an
            # error. Additive only, same as the column loop above — an
            # existing index is never dropped or replaced even if its
            # definition in the model has since changed (that would need a
            # migration, not this reconciler).
            have_idx = {i["name"] for i in insp.get_indexes(table.name)}
            for idx in table.indexes:
                if idx.name in have_idx:
                    continue
                idx.create(bind=engine, checkfirst=True)
                print(f"[atlas] schema reconcile: added index {table.name}.{idx.name}")


def _backfill_graph_origin(eng=None) -> None:
    """Turn `node.origin`/`edge.origin` NULL into `'git'` (TASK-1016).

    WHY THIS EXISTS SEPARATELY FROM THE ORM DEFAULT. `models_graph.Node.origin`
    and `.Edge.origin` both declare `default="git"`, but a SQLAlchemy column
    `default` is a PYTHON-SIDE default: it only fires when the ORM itself
    builds an INSERT. It does nothing whatsoever to rows that already exist in
    the table. On this repo's live, already-populated Postgres, `origin` is a
    BRAND NEW column that `_reconcile_graph_schema()` just above adds via a
    bare `ALTER TABLE ... ADD COLUMN origin VARCHAR(16)` — nullable, no SQL
    `DEFAULT`, on purpose (an ALTER with a DEFAULT can lock/rewrite a large
    populated table on some Postgres versions; nullable-no-default is what
    that helper has always done, see its docstring). The net effect: the
    instant this deploys, every one of the ~39 world nodes, ~140 workitems and
    ~83 edges gets `origin = NULL`, NOT `'git'` — acceptance criterion 1
    ("defaulting to `git` for every existing row") is NOT satisfied by the
    schema change alone.

    So: backfill explicitly, here, right after the schema reconcile, on every
    boot. `WHERE origin IS NULL` makes it idempotent and cheap once caught up
    (a no-op UPDATE touching zero rows on every later boot). Inserts always set
    `origin` explicitly (`"atlas"` from the write API/importers, `"git"` — now
    only ever historical — from the retired boot ingests), so this only ever
    touches a NULL left over from before the column existed; nothing since has
    a reason to leave one.

    KEPT AFTER TASK-1058 EVEN THOUGH THE RECONCILERS THAT MOTIVATED IT ARE
    GONE. This is data hygiene (a NULL is not a value anything should have to
    special-case), not reconciliation — `origin` is not branched on by
    anything at boot any more (see `models_graph.Node.origin`'s docstring for
    what the column means now), so there is no "immortal row" risk left to
    defend against. Removing this would leave any pre-2026-08-10 row that
    never got backfilled carrying NULL forever for no reason; keeping it is
    free and closes that out on the next boot.

    `eng` defaults to this module's own `engine`; verify_object_graph.py passes
    an isolated throwaway one so its provenance case exercises this exact
    function against test data instead of re-implementing its SQL.
    """
    from sqlalchemy import text
    eng = eng or engine
    with eng.begin() as conn:
        conn.execute(text("UPDATE node SET origin = 'git' WHERE origin IS NULL"))
        conn.execute(text("UPDATE edge SET origin = 'git' WHERE origin IS NULL"))


# --------------------------------------------------------------------------- #
# Ingest status — REMOVED (TASK-1058, STORY-atlas-retire-status-roundtrip).
#
# `_INGEST`, `_LAST_INGEST_OK_AT`, `INGEST_STALE_AFTER_SECONDS`,
# `_INGEST_STATES`, `_record_ingest()`, `_ingest_is_stale()`,
# `ingest_problems()`, `_maybe_ingest_backlog()` and `_maybe_ingest_world()`
# all existed to report the outcome of the two boot-time reconcilers (TASK-928:
# "a broken manifest must not stop the board from serving, but the failure
# must not be silent either"). Both reconcilers are gone — see `startup()`'s
# docstring — so there is no outcome left to report. Keeping this machinery
# around with nothing to feed it would have left `/api/health` reporting
# `ingest_ok: false` (or some frozen stale-but-not-really state) FOREVER,
# which is exactly the "a health endpoint that is always red is one people
# learn to ignore" failure TASK-1057 already named once for the backlog half
# alone (`retired` vs `unconfigured`) — TASK-1058 finishes that by removing
# the fields rather than inventing a THIRD state for "there is no ingest at
# all any more". See `health()` below for what `/api/health` reports instead.
# --------------------------------------------------------------------------- #


def _seed_task_id_sequence() -> None:
    """Advance `models_graph.task_id_seq` above every `TASK-` id this boot can
    see (TASK-1047), so the create route (`POST /api/workitem`) can never mint
    an id that collides with one already in the graph.

    Runs every boot, unconditionally. `node` is now the ONLY source of
    existing `TASK-` ids (TASK-1058): there is no boot-time ingest any more to
    have raced with, and no backlog directory to scan defensively for ids that
    "exist on disk but didn't reach `node` yet" — the backlog is Postgres, full
    stop (see AGENTS.md's "Shipped 2026-08-10: the backlog lives in
    Postgres"). `extra_ids` is passed empty; the parameter stays (rather than
    changing `models_graph.seed_task_id_sequence`'s signature) because
    `verify_task_id_allocation.py` exercises it directly with a synthetic set.

    Deliberately NOT wrapped in a broad try/except. A sequence that silently
    failed to seed is a SILENT COLLISION RISK for the next workitem anyone
    creates — so this is allowed to raise into `startup()`'s existing
    retry-then-fail-closed loop, same as `create_all`/`_reconcile_graph_schema`/
    `_backfill_graph_origin` above it.
    """
    with session() as s:
        new_value = models_graph.seed_task_id_sequence(s, ())
        s.commit()
    if new_value is not None:
        print(f"[atlas] task_id_seq: seeded (sequence last_value={new_value})")
    else:
        print("[atlas] task_id_seq: nothing to seed (genuinely fresh install, no TASK- id anywhere yet)")


# --------------------------------------------------------------------------- #
# The router table (STORY-oss-04). Each area's routes live in routers/<area>.py
# and are registered here, in the order they used to appear in app.py. Order is
# only behaviour where two paths can match one request; verify_routes.py checks
# that no route is shadowed by an earlier one, however this list is arranged.
# --------------------------------------------------------------------------- #
from routers import health as _r_health  # noqa: E402
from routers import legacy_v0 as _r_legacy_v0  # noqa: E402
from routers import graph as _r_graph  # noqa: E402
from routers import nodes as _r_nodes  # noqa: E402
from routers import edges as _r_edges  # noqa: E402
from routers import workitems as _r_workitems  # noqa: E402
from routers import intents as _r_intents  # noqa: E402
from routers import comments as _r_comments  # noqa: E402
from routers import search as _r_search  # noqa: E402
from routers import events as _r_events  # noqa: E402
from routers import docs as _r_docs  # noqa: E402
from routers import access as _r_access  # noqa: E402
from routers import me as _r_me  # noqa: E402
from routers import assets as _r_assets  # noqa: E402
from routers import adrs as _r_adrs  # noqa: E402  (ADR 0045; registers adr_record on models_graph.Base)
from routers import audit as _r_audit  # noqa: E402  (STORY-oss-16; loads ATLAS_AUDIT_* at import)
from routers import context as _r_context  # noqa: E402  (ADR 0044 §0: "tell me about X"; core, no add-on)

app.include_router(_r_health.router)
app.include_router(_r_legacy_v0.router)
app.include_router(_r_graph.router)
app.include_router(_r_nodes.router)
app.include_router(_r_edges.router)
app.include_router(_r_workitems.router)
app.include_router(_r_intents.router)
app.include_router(_r_comments.router)
app.include_router(_r_search.router)
app.include_router(_r_events.router)
app.include_router(_r_docs.router)
# ATLAS_DISABLED_FEATURES (STORY-oss-08). Read here, at import, so an unknown
# feature name stops the boot (settings.SettingsError). `github_link` has no
# router of its own: its six routes sit inside `access` and `me`, so when it is
# off those two routers are registered WITHOUT the GitHub routes, which then
# 404 like any path that never existed. On (the default) is the line as it was.
_features = settings.load_features()
# ATLAS_STRICT_API (STORY-oss-14): read per request by `payload_fields.strict_api()`;
# loaded here once so a blank or unknown value stops the boot instead.
settings.load_strict_api()
_GITHUB_ROUTE_PREFIXES = ("/api/me/github", "/api/access/github/")


def _without_github(router):
    from fastapi import APIRouter
    kept = APIRouter()
    kept.routes.extend(r for r in router.routes
                       if not getattr(r, "path", "").startswith(_GITHUB_ROUTE_PREFIXES))
    return kept


if _features.enabled("github_link"):
    app.include_router(_r_access.router)
    app.include_router(_r_me.router)
else:
    app.include_router(_without_github(_r_access.router))
    app.include_router(_without_github(_r_me.router))
app.include_router(_r_assets.router)
app.include_router(_r_adrs.router)
app.include_router(_r_audit.router)  # GET /api/audit/export, owner-only
app.include_router(_r_context.router)  # GET /api/context
from routers import stats as _r_stats; app.include_router(_r_stats.router)  # noqa: E402,E702  GET /api/me/stats (TASK-1444)
# The handbook site at /docs (STORY-atlas-system-node-handbook): static files only, gated
# unless ATLAS_PUBLIC_DOCS=1 (auth.py §2). FastAPI's own /docs stays off (docs_url=None above).
from routers import handbook as _r_handbook  # noqa: E402
app.include_router(_r_handbook.router)
# The product website's own files at /site/* (TASK-1463): registered ONLY with ATLAS_LANDING=v2,
# so under the default (v1) the route table is unchanged and `/` serves today's page.
if _r_assets.LANDING == "v2":
    from routers import landing as _r_landing  # noqa: E402
    app.include_router(_r_landing.router)
# The configuration panel (STORY-atlas-config-panel): GET /api/config, owner-only. Phase 2's one
# write route, PUT /api/config/{name}, exists only with ATLAS_CONFIG_WRITES on (default off), read
# here at import like the feature switches, so a typo stops the boot. Off, no route, no scope
# entry and no table: the route table is routes.snapshot.txt and the panel is read-only. The scope
# entry is added here, not in auth.py's table, so the default table stays what
# verify_object_graph §20 pins (the retrieval add-on's pattern below). routers/config.py has the
# design and the precedence.
from routers import config as _r_config  # noqa: E402
import settings_catalog as _settings_catalog  # noqa: E402
app.include_router(_r_config.router)
_CONFIG_WRITES_ON = _settings_catalog.load_config_writes()
if _CONFIG_WRITES_ON:
    _r_config.WRITES_ON = True
    auth.WRITE_SCOPE_ROUTES[("PUT", "/api/config/{name}")] = "admin"
    app.include_router(_r_config.write_router)
from routers import setup as _r_setup; app.include_router(_r_setup.router)  # noqa: E402,E702  the setup guide (STORY-atlas-setup-guide-adrs); registers setup_answer
# Crunch week (STORY-atlas-crunch-mode): four routes, always registered, each refusing every
# token; owner session + password step-up to change it. Off by default, and an install that
# never activates it has no crunch table (models_crunch.py). crunch.py has the design.
from routers import crunch as _r_crunch; app.include_router(_r_crunch.router)  # noqa: E402,E702

# ATLAS_RETRIEVAL (ADR 0044, STORY-atlas-retrieval-indexing). OFF BY DEFAULT, and off means
# this branch is the whole cost: no route, no scope entry, nothing more imported. Read at
# import like the feature switches above, so a value that is neither on nor off stops the boot.
import retrieval as _retrieval  # noqa: E402  (the package's __init__ only: one env read)
_RETRIEVAL_ON = _retrieval.enabled()
if _RETRIEVAL_ON:
    from routers import retrieval as _r_retrieval  # noqa: E402
    # Added here, not in auth.py's table, so the default table stays exactly what
    # verify_object_graph §20 pins; verify_retrieval.py proves the scope with the add-on on.
    auth.WRITE_SCOPE_ROUTES[("POST", "/api/retrieval/reindex")] = "admin"
    app.include_router(_r_retrieval.router)
