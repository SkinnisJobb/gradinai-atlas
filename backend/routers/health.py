"""`health` routes, split out of `app.py` (STORY-oss-04); the code is moved, not changed.
Registered on the app by `main.py` via `include_router`.
"""
from __future__ import annotations

import re

from fastapi import APIRouter
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

import models_graph  # Atlas v2 object-graph schema (ADR 0019); coexists with Component below
import auth         # THE GATE (TASK-967) + service tokens (TASK-968)
import db
import settings     # ATLAS_DISABLED_FEATURES (STORY-oss-08)
import retrieval    # ATLAS_RETRIEVAL (ADR 0044): only `enabled()` is read here
import cert_expiry  # ATLAS_CERT_EXPIRY_FILE (TASK-1419): the server certificate's expiry
from db import Component, session
from routers.assets import ASSET_FILES

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    """AUTHENTICATED liveness + a few numbers worth having (TASK-967).

    **This route is NOT on the exempt list and must never be added to it.**
    Infrastructure uses `/healthz` (liveness, no DB) and `/readyz` (readiness,
    DB) — which exist precisely so that nothing ever needs a healthcheck on
    this route.

    CHANGED (TASK-1058, STORY-atlas-retire-status-roundtrip): `ingest_ok`,
    `ingest_problems`, `ingest`, `last_successful_ingest_at` and `ingest_stale`
    are GONE, not renamed. They reported the outcome of the two boot-time
    reconcilers (backlog markdown, world manifest); both are deleted — nothing
    at boot reads a file any more, so there is no ingest verdict to carry and
    no staleness clock to compare against. Leaving those fields in place with
    nothing to feed them would have meant either a permanent, meaningless
    `ingest_ok: true` (a false all-clear) or a permanent `ingest_stale: true`
    (a health signal that is always red, which people learn to ignore — the
    exact failure TASK-1057 already named once) — this route asserts only
    what can still be true or false. `node_count`/`edge_count` replace them as
    the "is there data in here at all" signal: not a health verdict, just a
    number a human or a script can sanity-check against what they expect.
    """
    from node_types import edge_type_warnings, name_register_warnings  # local import: see startup()

    with session() as s:
        # ATLAS_LEGACY_V0=0 (STORY-oss-07) creates no `component` table: nothing v0 is seeded.
        n = s.scalar(select(Component).limit(1)) if db.LEGACY_V0 else None
        node_count = s.scalar(select(func.count()).select_from(models_graph.Node))
        edge_count = s.scalar(select(func.count()).select_from(models_graph.Edge))
        # `graph_event` grows and is never pruned, ON PURPOSE — retention was
        # considered and declined, because this database is single-digit
        # megabytes and the real write rate is one person plus a few agents.
        # A decision to let something grow forever is only safe while somebody
        # can see how fast: this is that number, and it is here rather than in
        # any prose because prose goes stale and a query does not.
        event_count = s.scalar(select(func.count()).select_from(models_graph.GraphEvent))
        warnings = auth.token_warnings(s)
        undocumented = undocumented_assets(s)
        names = name_register_warnings(s)
        edge_type_drift = edge_type_warnings(s)
    return {
        "ok": True,
        "seeded": bool(n),
        "node_count": node_count,
        "edge_count": edge_count,
        "event_count": event_count,
        # Near-expiry / long-unused service tokens. Expiry that is surfaced is
        # safe; expiry that is silent is how the board freezes.
        "token_warnings": warnings,
        # First-party assets the board serves that no node document mentions.
        # See undocumented_assets() for why this is a query and not a habit.
        "undocumented_assets": undocumented,
        # World nodes whose type carries a PROSE name but whose name starts
        # lowercase — a slug that leaked into a human label. Normally empty,
        # like `token_warnings`; it is a report and not a transform because
        # uppercasing the identifier-register types would break them. See
        # `node_types.PROSE_NAME_TYPES`.
        "name_register_warnings": names,
        # Drift between the `edge_type` registry and what `edge` actually
        # holds — used-but-undeclared, declared-active-with-zero-rows,
        # (the hazard) declared-dead-with-rows, and pair-not-declared (a
        # (src_family, dst_family) combination in real use that the type's
        # declared `pairs` does not list — the family-level counterpart to
        # the first three, which only compare edge_type STRINGS). Normally
        # empty, same shape as `token_warnings`/`name_register_warnings`.
        # See `node_types.edge_type_warnings`.
        "edge_type_warnings": edge_type_drift,
        # Is this container running the source on the box, and is the box running master?
        # `warnings` is empty when both are true, and each warning names the command that fixes
        # it. atlas-mcp reads this field and repeats it on every tool response while it is
        # non-empty. See deploy_status.py for the two halves and why each is answered where it is.
        # ATLAS_DISABLED_FEATURES=deploy_status (STORY-oss-08) leaves the field out: it is
        # logic inside this route, not a route of its own, so this is the narrowest guard.
        **({"deploy": _deploy_status()} if settings.feature_enabled("deploy_status") else {}),
        # The retrieval add-on (ADR 0044): `on`, `degraded: …` or `unavailable: …`. Informational,
        # never a verdict on Atlas. Absent with the add-on off, so the response is unchanged.
        **({"retrieval": _retrieval_health()} if retrieval.enabled() else {}),
        # The install's server certificate (TASK-1419): near-expiry, expired or unreadable,
        # same shape as `token_warnings` and normally empty. Present only when
        # ATLAS_CERT_EXPIRY_FILE names a certificate (the OSS compose file sets it); unset,
        # as on the homelab, the field is absent and the response is exactly as before.
        **({"cert_warnings": cert_expiry.warnings(cert_cfg)}
           if (cert_cfg := settings.load_cert_expiry()).enabled else {}),
    }


def _retrieval_health() -> str:
    from retrieval import service  # imported only with the add-on on
    return service.health()


# TASK-1356: deploy_status.py reads ATLAS_DEPLOY_HINT_* when the route first imports it. Read
# them here too, at import, so a set-but-blank value stops the boot (SettingsError naming it)
# instead of turning /api/health into a 500 later.
settings.load_deploy_hints()
# TASK-1419: the same for ATLAS_CERT_EXPIRY_FILE / ATLAS_CERT_EXPIRY_WARN_DAYS. The route
# re-reads them on each call (cheap, and it is what a test changes), but a typo stops the boot.
settings.load_cert_expiry()


def _deploy_status() -> dict:
    import deploy_status  # local: standard library only, and a check must not break the route
    return deploy_status.check("atlas-api", deploy_status.API_SPEC)


# Third-party payloads and the favicon are SERVED but not AUTHORED here, so
# "is it documented" is not a question about them. Everything else in
# ASSET_FILES is a module this project wrote and is expected to describe.
VENDOR_PREFIX = "vendor/"

# A node_doc row registered on its owning node as an ARCHITECTURE document.
# Same correlated-EXISTS shape as models_graph.NODE_DOC_SERVED_SQL (a doc is
# only real when its path is in the owning node's `docs[]`), plus the `kind`
# narrowing that keeps generated logs out of the corpus.
ARCHITECTURE_DOC_SQL = (
    "EXISTS (SELECT 1 FROM node n, "
    "jsonb_array_elements(coalesce(n.attributes -> 'docs', '[]'::jsonb)) AS d "
    "WHERE n.id = node_doc.node_id AND d ->> 'path' = node_doc.path "
    "AND coalesce((d ->> 'external')::boolean, false) = false "
    "AND d ->> 'kind' = 'architecture')"
)


def undocumented_assets(s: Session) -> list[str]:
    """Assets the board serves that appear in NO node document.

    WHY THIS EXISTS. `atlas-board-shell`'s architecture document carries a
    mermaid graph enumerating the board's modules. On 2026-08-24 it named
    seven of ten: `mine.js` and `settings.js` had shipped months earlier and
    `compose.js` that same day, and none of the three had been added. Nothing
    was broken and nothing complained — the diagram simply stopped being true,
    which is what a hand-maintained enumeration does.

    The lesson AGENTS.md already draws about census figures applies exactly:
    *never write a count a query could answer*. A list of modules IS a census.
    So this asks the question instead of trusting anyone to remember it, and
    the answer moves on its own when a module is added or documented.

    ARCHITECTURE DOCUMENTS ONLY, and that narrowing was not a preference —
    the first version asked "mentioned in ANY node document" and was
    immediately defeated. `mine.js` and `compose.js` came back clean because
    they appear in `atlas-run-log`'s run-log.md, a document this project's own
    tooling publishes on every loop run: the check was reading its own exhaust
    as documentation. Only `settings.js` was caught, and only because nobody
    had happened to type it. A `docs[]` entry carries a `kind`, so the corpus
    is the entries whose kind is `architecture` — the register that is
    supposed to describe how the thing is built — which excludes generated
    logs for a reason rather than by filename.

    WHAT IT IS NOT. It does not check that a document is CORRECT, or that a
    module is documented in the RIGHT architecture document — no query can. A
    module named in some other node's architecture document still passes.
    That is the honest limit of the check rather than a gap in it.

    It is a WARNING, never a failure. A fresh database has no `node_doc` rows,
    so everything is undocumented and that is correct rather than alarming;
    the field is a list a human reads, and an empty list is the good case.
    """
    first_party = sorted(
        name for name, path in ASSET_FILES.items()
        if not path.startswith(VENDOR_PREFIX) and not name.endswith(".ico")
    )
    if not first_party:
        return []
    # One pass over the corpus rather than a LIKE per asset: node_doc is small
    # and this keeps the check O(docs) instead of O(docs x assets) round trips.
    rows = s.scalars(
        select(models_graph.NodeDoc.content).where(text(ARCHITECTURE_DOC_SQL))
    ).all()
    corpus = "\n".join(rows or [])
    return [name for name in first_party if not _asset_mentioned(name, corpus)]


# Board v2's tree (ADR 0043) reuses legacy basenames: `v2/core/api.js` beside
# `api.js`. Plain substring matching would let a document naming only the v2
# module count as documentation of the legacy one, and the warning would go
# quiet for exactly the module nobody described. So a FLAT name is not counted
# where it appears as the last segment of a `v2/...` path. A nested name is a
# full path, specific enough that substring matching is right for it.
_UNDER_V2 = re.compile(r"(?:^|[^\w.-])v2/(?:[\w.-]+/)*$")


def _asset_mentioned(name: str, corpus: str) -> bool:
    if "/" in name:
        return name in corpus
    for m in re.finditer(re.escape(name), corpus):
        if not _UNDER_V2.search(corpus[max(0, m.start() - 200):m.start()]):
            return True
    return False
