"""`/site/*`: the product website's own files, and the headers `/` sends with it (TASK-1463).

ONLY WITH `ATLAS_LANDING=v2`. Unset (or `v1`), `main.py` does not include this router at all,
`/` serves `frontend/landing.html` exactly as before (`routers/assets.root`) and nothing below
is reachable by anyone. With v2, `/` serves `frontend/landing-v2/index.html` with `PAGE_HEADERS`,
and this router serves exactly `landing_site.SITE_FILES` under `/site/`, matched as whole
strings like `ASSET_FILES` and the handbook's list: no directory walk, no mount, a `..` segment
is only ever a dict miss. Those exact paths are public (`auth.PUBLIC_ROUTES`, ADR 0049); the
files are read from the image, GET/HEAD only, touching no table and reading no principal.

THE CSP is ADR 0049 rule 2 verbatim, set in the API so an install without the OSS ingress's
header snippet gets it too. `connect-src 'none'` is what makes "the page calls no API" a
browser rule rather than a review rule; `script-src 'self'` with no hash, nonce or
`'unsafe-*'` is what makes "no inline script" one.
"""
from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response

import landing_site
from deps import FRONTEND

router = APIRouter()

SITE_DIR = os.path.join(FRONTEND, "landing-v2")
INDEX = os.path.join(SITE_DIR, "index.html")

#: ADR 0049 rule 2, exactly.
PAGE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "font-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'")

#: Sent on the v2 landing branch of `/` together with `assets.ROOT_CACHE_HEADERS` (ADR 0049
#: §"What changes in code"). The 302 and the machine-principal branches are untouched.
PAGE_HEADERS = {
    "Content-Security-Policy": PAGE_CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}

#: The site's files. `private, no-cache` like every other file this app serves (the
#: 2026-08-18 `board.css` incident in `routers/assets.py`): public files have nothing to lose by
#: revalidating, and a shared cache then never decides what a stranger gets.
FILE_HEADERS = {
    "Cache-Control": "private, no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}

#: A diagram opened on its own (not through the page's <img>, where an SVG never runs script)
#: is a document of its own, so it gets a policy of its own: nothing may load or run, and only
#: the drawing's own inline <style> applies. No script source exists in it at all.
SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"

_FILES = frozenset(landing_site.SITE_FILES)


def _site_file(name: str) -> Response:
    if name not in _FILES:
        return JSONResponse({"error": "not found"}, status_code=404)
    full = os.path.join(SITE_DIR, *name.split("/"))
    if not os.path.isfile(full):
        return JSONResponse({"error": "not found"}, status_code=404)
    ext = os.path.splitext(name)[1]
    headers = dict(FILE_HEADERS)
    if ext == ".svg":
        headers["Content-Security-Policy"] = SVG_CSP
    return FileResponse(full, headers=headers, media_type=landing_site.MEDIA_TYPES[ext])


@router.get(landing_site.SITE_PREFIX + "{name:path}")
@router.head(landing_site.SITE_PREFIX + "{name:path}")
def site_file(name: str) -> Response:
    return _site_file(name)
