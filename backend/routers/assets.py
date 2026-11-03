"""`assets` routes, split out of `app.py` (STORY-oss-04); the code is moved, not changed.
Registered on the app by `main.py` via `include_router`.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

import auth         # THE GATE (TASK-967) + service tokens (TASK-968)
import settings
from deps import FRONTEND
from routers import landing as _landing  # the v2 website's headers (TASK-1463); inert under v1

router = APIRouter()

#: Sent on BOTH branches of `/` (ADR 0035) — the public landing page AND the
#: redirect to `/board` for a signed-in browser. `/` is one PATH now serving
#: TWO materially different responses keyed on a cookie, which is exactly the
#: condition that let Cloudflare serve a cached, authenticated
#: `/assets/board.css` to a caller with no session at all on 2026-08-18 (see
#: `ASSET_CACHE_CONTROL` below). `private` forbids a SHARED cache from storing
#: either variant; `Vary: Cookie` is the standard signal for "this response
#: depends on the cookie", even though Cloudflare's default cache key does not
#: reliably honour it — belt, not the only strap. `no-cache`, not `no-store`:
#: a browser may still keep its own copy, it just has to revalidate.
ROOT_CACHE_HEADERS = {"Cache-Control": "private, no-cache", "Vary": "Cookie"}

#: `ATLAS_LANDING` (TASK-1463), read once at import like every gate-shaping setting. "v1" (the
#: default) leaves the landing branch below exactly as it was: same file, same headers. "v2"
#: serves the product website instead, with ADR 0049's CSP (`routers/landing.PAGE_HEADERS`).
LANDING = settings.load_landing()


@router.get("/")
@router.head("/")
def root() -> Response:
    """The bare domain (ADR 0035, 2026-08-25). `/` is in `auth.PUBLIC_ROUTES`,
    so the gate never refuses it — but it is NOT a fixed response: this
    handler reads `auth.current_principal`, which the gate still sets on a
    public route whenever a credential WAS presented, and branches on it.

      * No principal at all -> the public informational landing page.
      * A signed-in BROWSER (`kind == "session"`) -> 302 to `/board`, which
        stays gated exactly as it always was and is NOT itself public.
      * A machine principal (Bearer / grace) -> served the board directly,
        UNCHANGED from before this ADR — a service token is not "logged out"
        in any sense that matters here, so nothing already scripted against
        `GET /` with a credential has to change.
    """
    principal = auth.current_principal.get()
    if principal is not None and principal.kind == "session":
        return RedirectResponse("/board", status_code=302, headers=ROOT_CACHE_HEADERS)
    if principal is not None:
        return FileResponse(os.path.join(FRONTEND, "board.html"))
    if LANDING == "v2":
        return FileResponse(_landing.INDEX, headers={**ROOT_CACHE_HEADERS, **_landing.PAGE_HEADERS})
    return FileResponse(os.path.join(FRONTEND, "landing.html"), headers=ROOT_CACHE_HEADERS)


@router.get("/board")
def board() -> FileResponse:
    """The board: board v2 (ADR 0043) since the cutover on 2026-10-22.
    Authenticated like everything else — `/board` is NOT in
    `auth.PUBLIC_ROUTES`, only `/` is (ADR 0035).
    """
    return FileResponse(os.path.join(FRONTEND, "v2", "index.html"))


@router.get("/board/legacy")
def board_legacy() -> FileResponse:
    """The previous board, frozen and unsupported (ADR 0043). Gated exactly as
    `/board` is. It builds its own links from `location.pathname`, so it works
    at this address unchanged.

    The first-generation tree view (frontend/index.html) is superseded and is no
    longer reachable AT ALL: the `/static` mount that used to serve it is gone
    (TASK-967). It still ships in the image; nothing routes to it.
    """
    return FileResponse(os.path.join(FRONTEND, "board.html"))


@router.get("/board/v2")
def board_v2(request: Request) -> RedirectResponse:
    """Board v2's address while it was being built. Kept so a bookmark or a
    link from that week still lands, with its query, on `/board`."""
    q = request.url.query
    return RedirectResponse("/board" + ("?" + q if q else ""), status_code=301)


# --------------------------------------------------------------------------- #
# Static assets — an explicit per-file allowlist, NOT a mount (TASK-967).
#
# **A FILE MISSING FROM `ASSET_FILES` 404s.** That is the whole point and it is
# also the way a newly added frontend module fails, so adding one means adding
# it here. `.github/workflows/verify.yml` carries a job that fails when any
# `frontend/*.js`, `frontend/*.css` or `frontend/board.html` is absent from
# this dict, and so does any file under `frontend/v2/`.
#
# BOARD V2 (ADR 0043) is keyed by its path under `frontend/`, so the key and
# the URL agree: `"v2/core/store.js"` is served at `/assets/v2/core/store.js`.
# The legacy board's flat keys are untouched.
#
# `app.mount("/static", StaticFiles(directory=FRONTEND))` is DELETED, not
# narrowed. A mount is an ASGI sub-app that route-level dependencies cannot see,
# and narrowing it would have left two things standing:
#
#   * /static/board.html  — a byte-identical UNAUTHENTICATED copy of the board;
#   * /static/index.html  — the superseded v0 view, whose fetch(...).then(r =>
#     r.json()) has no `r.ok` check anywhere, so it would render a 401 error
#     body as data.
#
# The files below are everything board.html actually loads. AuthGate still
# covers /assets/, so static is defended twice rather than by the mount alone;
# `/assets/*` is NOT public and NOT reachable by a must_change principal.
#
# NOTE FOR TASK-969 (frontend, not this task's file): board.html:558 still pulls
# D3 from cdn.jsdelivr.net and :561-562 still reference /static/vendor/*.js.
# Those three lines must become /assets/d3.min.js, /assets/marked.min.js and
# /assets/mermaid.min.js, and D3 must be vendored to frontend/vendor/d3.min.js.
# --------------------------------------------------------------------------- #
ASSET_FILES = {
    "d3.min.js": "vendor/d3.min.js",
    "marked.min.js": "vendor/marked.min.js",
    "mermaid.min.js": "vendor/mermaid.min.js",
    "favicon.ico": "favicon.ico",
    "board.css": "board.css",
    "state.js": "state.js",
    "util.js": "util.js",
    "glyph.js": "glyph.js",
    "map.js": "map.js",
    "panel.js": "panel.js",
    "mine.js": "mine.js",
    "api.js": "api.js",
    "settings.js": "settings.js",
    # Owned by a sibling builder (frontend/compose.js); listed here because a
    # module missing from this allowlist 404s and only backend/app.py may add
    # to it — see this iteration's coordination note.
    "compose.js": "compose.js",
    # ---- board v2 (ADR 0043): keyed by path, so the key IS the URL tail.
    # `*.test.js` and `package.json` sit in the same tree and are deliberately
    # NOT listed: they are node --test material, not things a browser loads.
    "v2/app.js": "v2/app.js",
    "v2/prepaint.js": "v2/prepaint.js",
    "v2/core/acceptance.js": "v2/core/acceptance.js",
    "v2/core/access-model.js": "v2/core/access-model.js",
    "v2/core/api.js": "v2/core/api.js",
    "v2/core/graph-model.js": "v2/core/graph-model.js",
    "v2/core/prefs.js": "v2/core/prefs.js",
    "v2/core/router.js": "v2/core/router.js",
    "v2/core/shortlabel.js": "v2/core/shortlabel.js",
    "v2/core/store.js": "v2/core/store.js",
    "v2/core/sync.js": "v2/core/sync.js",
    "v2/core/vocabulary.js": "v2/core/vocabulary.js",
    "v2/styles/board.css": "v2/styles/board.css",
    "v2/styles/graph.css": "v2/styles/graph.css",
    "v2/styles/layout.css": "v2/styles/layout.css",
    "v2/styles/overlays.css": "v2/styles/overlays.css",
    "v2/styles/panel.css": "v2/styles/panel.css",
    "v2/styles/tokens.css": "v2/styles/tokens.css",
    "v2/ui/dom.js": "v2/ui/dom.js",
    "v2/ui/md.js": "v2/ui/md.js",
    "v2/ui/panes.js": "v2/ui/panes.js",
    "v2/ui/toast.js": "v2/ui/toast.js",
    "v2/ui/viewprefs.js": "v2/ui/viewprefs.js",
    "v2/views/search.js": "v2/views/search.js",
    "v2/views/access/access.css": "v2/views/access/access.css",
    "v2/views/access/access.js": "v2/views/access/access.js",
    "v2/views/graph/glyph.js": "v2/views/graph/glyph.js",
    "v2/views/graph/graph.js": "v2/views/graph/graph.js",
    "v2/views/graph/labels.js": "v2/views/graph/labels.js",
    "v2/views/graph/layout.js": "v2/views/graph/layout.js",
    "v2/views/panel/adrs.js": "v2/views/panel/adrs.js",
    "v2/views/panel/comments.js": "v2/views/panel/comments.js",
    "v2/views/panel/config.js": "v2/views/panel/config.js",
    "v2/views/panel/docs.js": "v2/views/panel/docs.js",
    "v2/views/panel/history.js": "v2/views/panel/history.js",
    "v2/views/panel/intent.js": "v2/views/panel/intent.js",
    "v2/views/panel/links.js": "v2/views/panel/links.js",
    "v2/views/panel/overview.js": "v2/views/panel/overview.js",
    "v2/views/panel/panel.js": "v2/views/panel/panel.js",
    "v2/views/work/board.js": "v2/views/work/board.js",
    # Crunch week (STORY-atlas-crunch-mode): the owner's Settings tab and the board banner.
    "v2/views/settings/crunch.css": "v2/views/settings/crunch.css",
    "v2/views/settings/crunch.js": "v2/views/settings/crunch.js",
    "v2/views/settings/crunch-banner.js": "v2/views/settings/crunch-banner.js",
    "v2/views/settings/crunch-model.js": "v2/views/settings/crunch-model.js",
    # "You have no personal password yet" for an SSO person (TASK-1442).
    "v2/views/settings/password-notice.css": "v2/views/settings/password-notice.css",
    "v2/views/settings/password-notice.js": "v2/views/settings/password-notice.js",
    "v2/views/settings/password-notice-model.js": "v2/views/settings/password-notice-model.js",
    # The dashboard pane and its box contract (TASK-1447); each box adds its own file here.
    "v2/core/dashboard.js": "v2/core/dashboard.js",
    "v2/styles/dashboard.css": "v2/styles/dashboard.css",
    "v2/views/dashboard/boxes.js": "v2/views/dashboard/boxes.js",
    "v2/views/dashboard/dashboard.js": "v2/views/dashboard/dashboard.js",
    # My tasks (TASK-1450).
    "v2/views/dashboard/my-tasks.js": "v2/views/dashboard/my-tasks.js",
}


#: Every asset response carries this, and BOTH halves are load-bearing
#: (2026-08-18, found on live).
#:
#: `private` — a SHARED cache must not store it. Without it, Cloudflare applied
#: its default 4-hour TTL to `/assets/board.css` on `atlas.example.com`,
#: cached an authenticated 200, and then served that file to callers with NO
#: session at all: `curl https://atlas.example.com/assets/board.css` returned
#: 200 and 66 KB while the same path on the LAN host correctly returned 401.
#: Everything behind the gate is supposed to answer 401 to a stranger (ADR
#: 0025). Nothing sensitive leaked — it is a stylesheet, and the modules
#: happened to be BYPASS rather than HIT — but the gate failed for a path
#: prefix, which is the same hazard `ASSET_FILES` itself was introduced to
#: close when a static mount was serving an unauthenticated copy of the board.
#:
#: `no-cache` — the browser MAY store it but must revalidate every time. These
#: URLs carry no cache-busting (`/assets/panel.js`, never `panel.abc123.js`),
#: so a stored copy is how an already-open tab keeps running last week's code
#: after a deploy. That trap cost time three separate times on 2026-08-17/18
#: alone, and it is why the old "hard-reload after every deploy" advice existed
#: — this header retires it. Revalidation is cheap: FileResponse
#: sends ETag and Last-Modified, so an unchanged file costs a 304.
#:
#: NOT `no-store`: that would refetch d3 and mermaid on every board load for
#: no benefit. The point is freshness, not starvation.
ASSET_CACHE_CONTROL = "private, no-cache"


def _asset_response(name: str) -> Response:
    rel = ASSET_FILES.get(name)
    if rel is None:
        # Not an allowlisted name: refuse without touching the filesystem.
        return JSONResponse({"error": "not found"}, status_code=404)
    full = os.path.join(FRONTEND, *rel.split("/"))
    if not os.path.isfile(full):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(full, headers={"Cache-Control": ASSET_CACHE_CONTROL})


# `{name:path}`, not `{name}`, so board v2's layered tree (`v2/core/store.js`,
# ADR 0043) can be served. It widens WHICH STRINGS reach the lookup, never what
# the lookup accepts: `name` is still matched EXACTLY against `ASSET_FILES`
# keys, so a nested path is served only when that whole path is listed. There
# is no directory walk, and a `..` segment can only ever be a dict miss.
# `verify_assets.py` holds both halves: a listed nested file is served, while an
# unlisted file that sits on disk in the same directory 404s.
@router.get("/assets/{name:path}")
def asset(name: str) -> Response:
    return _asset_response(name)


@router.get("/favicon.ico")
def favicon() -> Response:
    """Exempt (browsers request it unauthenticated, including on the login page).

    It is the ONLY asset the login and forced-change pages need — both are
    server-rendered with inline CSS and no JavaScript — which is why nothing
    under /assets/ has to be public.
    """
    return _asset_response("favicon.ico")
