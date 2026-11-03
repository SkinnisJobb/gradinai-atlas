"""The product website's files: the one list of what `/` (v2) may load (TASK-1463).

Pure data; imports nothing from the app. `auth.py` adds `public_paths()` to `PUBLIC_ROUTES`
only when `ATLAS_LANDING=v2`, `routers/landing.py` serves exactly `SITE_FILES` and nothing
else, and `verify_landing_v2.py` checks that the page references nothing outside this list.

The site lives in `frontend/landing-v2/`. Its URL prefix is `SITE_PREFIX`, so
`frontend/landing-v2/img/diagrams/roadmap.svg` is served at `/site/img/diagrams/roadmap.svg`.
`index.html` is NOT in the list: it is served at `/` by `routers/assets.root`, never at a
second address.

WHY A FIXED LIST, NOT A DIRECTORY (ADR 0049 §"What changes in code", the shape ADR 0047 used
for `/docs`): every path a stranger can reach is named here and reviewed. An image that has
not been drawn yet is still listed; its path answers 404 until the file exists, and the page
keeps its caption and its reserved space.
"""
from __future__ import annotations

#: The URL prefix the site's own files are served under.
SITE_PREFIX = "/site/"

#: Diagrams: first-party SVG, one per section (docs/site/content.md §19). All are drawn 400 units
#: wide and portrait, except the hero (800 wide), which has a 400-wide variant for phones.
DIAGRAMS = (
    "hero-one-plan", "hero-one-plan-narrow", "problem-before-after", "hierarchy-tree", "decide-approve", "plan-on-top",
    "context-ask-open", "search-merge", "rules-down-work-up", "agent-key", "access-projects",
    "guardrails", "how-it-works", "roadmap", "getting-started",
)

#: Screenshots from the seeded demo install: `<slug>.png` (wide) and `<slug>-390.png` (phone).
SHOTS = (
    "backlog-view", "history-tab", "proposal-overview", "board-whole", "my-tasks",
    "fresh-seed-graph", "docs-tab",
)

#: Every file the site serves besides index.html, relative to frontend/landing-v2/.
SITE_FILES = (
    ("landing.css", "landing.js")
    + tuple("img/diagrams/%s.svg" % s for s in DIAGRAMS)
    + tuple("img/shots/%s%s.png" % (s, w) for s in SHOTS for w in ("", "-390"))
)

#: Media types by extension, set explicitly so no platform's mimetypes table decides them.
MEDIA_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


def public_paths() -> frozenset:
    """The exact URL paths `auth.PUBLIC_ROUTES` gains under ATLAS_LANDING=v2."""
    return frozenset(SITE_PREFIX + f for f in SITE_FILES)
