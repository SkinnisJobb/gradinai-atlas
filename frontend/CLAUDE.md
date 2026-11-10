# frontend/ — the board's house rules

Loads when Claude reads anything in this directory, which is exactly when these fire. The root
`AGENTS.md` keeps only what is needed before the first Atlas read; what the board IS lives on the
`atlas-frontend` node and its children, and what can and cannot be measured headless is in the
`verify-board` skill. This file is the detail you need while editing.

## Two boards: `v2/` is where new work goes, everything else is legacy

ADR 0043 (Accepted 2026-10-22) froze the files in this directory as the **legacy board**. Since the
cutover (2026-10-22) it is reached at `/board/legacy`. Change it only to keep it loading.
New board work goes in **`frontend/v2/`**, served at `/board` (`/board/v2` redirects there), and its
rules differ from everything below:

- **The entry is `v2/app.js`, a real module.** Modules import each other by RELATIVE path
  (`./core/store.js`), so the same file runs in the browser and under `node --test`. There is no
  `hooks` object: panes subscribe to `core/store.js`.
- **Modern JS is allowed** (`const`/`let`, arrows, template literals); still no build step, no
  framework, nothing installed. `v2/package.json` exists only to mark the tree as ES modules for node.
- **`core/` touches no DOM.** Logic that can be tested belongs there, or as a pure export beside
  its DOM code (`ui/panes.js`'s `columns`), with a `*.test.js` beside it. `backend/verify_frontend_v2.py`
  runs every such file inside `verify_all.py`, and fails when node is absent.
- **Every served v2 file is listed in `ASSET_FILES` under its path** (`"v2/core/store.js"`).
  `backend/verify_assets.py` fails on a file that is missing from the list.
- v2 prefs live under `atlas.v2.*`. Only the theme and text size are shared with legacy, by their
  legacy keys (`core/prefs.js` `SHARED`).

## board.html is a module shell

**It is no longer one file.** It was — inline `<style>`, one inline `<script>` IIFE — until
`STORY-atlas-board-modularize` (TASK-1023) split it into native ES modules: `board.html` is the shell
that wires them, with `board.css`, `panel.js`, `map.js`, `mine.js`, `api.js`, `state.js`,
`glyph.js`, `settings.js`, `compose.js` and `util.js` beside it — **the live list is `ASSET_FILES` in
`backend/routers/assets.py`, and a module missing from that allowlist 404s**, which is the first thing to check when a new
one does not load (`.github/workflows/verify.yml` fails on it too). `mine.js` owns the Mine view,
including its **people picker** — the "Showing …" control is a button (`#mineWho`, formerly a
`<select>`) that opens a filterable overlay built in that same module, because the roster grows and
a dropdown has no filter. Like `compose.js` it borrows the modal chrome rather than copying it:
`.settings-overlay`/`.settings-sheet`/`.settings-head`/`.settings-close`/`.settings-body` are
**generic despite the name**, and `dismissOnScrim` (`util.js`) is the shared backdrop-click fix — a
fourth overlay should reuse both rather than growing a third near-identical CSS block.
**`compose.js` owns the "File work" form** — the first frontend caller of `POST /api/workitem`, CREATE
only, never a `PATCH`. Like `settings.js` it is **lazy-imported on click**, owns its own DOM (board.html
holds no markup for it) and calls back through a `hooks` object, because an inline `<script
type="module">` cannot be imported from. `settings.js` owns
the whole Settings overlay, which since 2026-08-25 is a **left rail of named panes** rather
than one scrolling page — Account and Agent credentials for everyone, Access & sharing and Audit
log for an owner. **A member's rail simply does not show the owner-only panes**, rather than showing
them and refusing: the rail is built from `me.role`, so a member never issues a request that 403s.
**Each pane fetches on first reveal,
not on open**, so opening Settings now costs zero API calls; the rail is an ARIA `tablist` and the
chosen pane persists under `atlas.settingsPane`. D3 v7 + marked + mermaid stay vendored
under `frontend/vendor/`. Still no build step, no bundler, no framework — that part never changed,
and the rules below all still hold.

**Three top-level views: `[World tree] [Work tree] [Mine]` — and only two of them are maps.** World
and Work are both D3 tidy-trees off the same `buildTree`; **Mine is a list**. That asymmetry is
wired into the tab switch: `switchTab("mine")` hides `.canvas-wrap`, shows `#mineWrap` and
**returns early** — before `buildTree`, `selectNode` and `renderFilterHint`, every one of which
operates on a hierarchy Mine does not have. Anyone adding something map-shaped needs to know that
early return exists, because code placed after it simply never runs for the third view.

- **ES5-flavoured vanilla JS.** `var`, `function` declarations. No arrow functions, no
  `const`/`let`, no template literals, no optional chaining.
- **State is ONE exported object mutated by property assignment** — `state.TREE = x`. ES
  module bindings are live but not assignable across modules, so follow the existing idiom
  rather than adding setters.
- **A module that must call back into `board.html` exposes a `hooks` object the shell fills
  in**, because an inline `<script type="module">` cannot be imported from. `map.js`,
  `panel.js`, `mine.js`, `settings.js` and `compose.js` all use that seam; it is the
  sanctioned pattern, not a workaround.
- **NO MODULE MAY ENCODE THE GRAPH.** Read it from `/api/*`. A type vocabulary is defensible
  presentation config; a node id is not. If a module needs to name a node in order to work,
  that is a missing endpoint.
- **The panel renders markdown** (`renderMarkdown` in `panel.js`) for Description, the More
  disclosure, Acceptance, the Result block and intent statements — not just the Docs tab.
  It mattered little while the backlog files were readable and matters a great deal now
  that they are not.
- **Ids are a contract.** Element ids are reached by `getElementById` from all over the
  script (`tab-world`, `zoom-fit`, `canvasWrap`, `nodeSearch`, `detailPanel`,
  `panelResizer`, `scaleUp`, `overviewToggle`, … — `grep -o 'getElementById("[^"]*"' frontend/*.js
  frontend/board.html` is the list). Move them, restyle them, re-parent
  them — never rename or drop them. Moving an element can still break a *selector*:
  moving the hint text out of `.map-ctl` orphaned `document.querySelector(".map-ctl
  .hint-text")`.
- **Every font size must scale**: `font-size: calc(<N>px * var(--ui-scale))`. A plain
  `px` font size silently opts out of the text-size control.
- **So must the map's grid.** `map.js`'s `dx`/`dy` (row gap, depth-column width) are SVG
  user units, so `var(--ui-scale)` cannot reach them — `rescaleLayout()` multiplies them
  by it and `applyUiScale` calls that. Scaling the type without scaling the grid is what
  put labels into the next column's labels. It deliberately does NOT re-fit: a re-fit
  would zoom back out by the factor the type grew by, and the control would look inert.
- **A node label is fitted by MEASUREMENT, never by character count** (`fitLabel`). The
  label is a name tspan plus trailing tspans — the world tree's `· N tasks · X%`, the work
  tree's `(N)` and `· @someone` — and those run 76–132 user units, so a budget that counts
  only the name is short by up to half the label. The readout is never truncated; the name
  is fitted to what the column has left. `dataset.name` holds the untruncated name so a
  scale change or a Detail-slider move can widen a name back out, not only cut it further.
- **Persisted view state** goes through `remember(key, value)` / `recall(key)` (safe
  localStorage wrappers) under the `atlas.*` namespace: `atlas.theme`, `atlas.tab`,
  `atlas.detail`, `atlas.panelWidth`, `atlas.uiScale`, `atlas.overview`,
  `atlas.statusFilter`, `atlas.typeFilter`. Anything that should survive a reload without
  flashing is also applied by the small pre-paint `<script>` at the top of the file.
- **Layout maths is load-bearing.** `.wrap` is centred, so changing the page's width
  moves the canvas's *left* edge; `withMapAnchored()` compensates the d3 pan by exactly
  that movement so nodes hold their screen position. Never use CSS `zoom` or
  `transform: scale()` on the page — the pan/zoom and the panel-resize arithmetic both
  read `getBoundingClientRect()` and a page zoom corrupts them silently.
- Themes are CSS custom properties (light/dark); mobile breaks at `max-width: 760px`.

## The three filter axes

**The board filters CLIENT-SIDE, on three composing axes** (`state.FILTER` in
`frontend/state.js`: `doneAfterDays`, `statuses`, `types`). Age ("hide finished > N days
ago"), status and node type each prune a COPY of whichever tree is on screen (`pruneTree`
in `board.html`) and never touch `workData`/`byId`/`NODE_INDEX` — filtering is about what
is DRAWN, not about what exists, so search, a deep link and the detail panel can still
reach a node the map is hiding, and asking for one by name reveals it (`revealFiltered`).
An ancestor of a matching descendant stays on screen too, on every axis, so a filtered map
never orphans a node above what it kept.

- **Status** offers `STATUS_CHOICES` (`state.js`) — the same fixed vocabulary the panel's
  status control writes with, bucketed through `statusBucket`/`STATUS_EQUIVALENT` so a
  `planned` item matches the `pending` filter the same way it lights the `pending` button.
- **Type** offers whatever is actually in the tree ON SCREEN, collected fresh by
  `collectTypes()` every rebuild rather than a hand-written list (the root file's own "never
  write a count or a list a query could answer" rule) — which is also why the vocabulary
  differs between the World tree (object/bridge types) and the Work tree (epic/story/task).
- A node with none of the property being filtered on (a world object has no workflow
  status) is **never hidden by that axis** — same principle as the age
  filter never hiding a node with no completion date.
- **Persisted, not server-side.** Both axes ride `atlas.statusFilter`/`atlas.typeFilter`
  (see the persisted-state bullet above) but neither is a filter `/api/workitems` or
  `/api/graph/tree` ever sees — a node hidden by these controls is still counted everywhere
  the API counts it, only not drawn.

## The depth-2 collapse

- **`map.js` collapses everything below depth 2 — on BOTH trees.** `buildTree` does
  `if (d.depth >= 2) d.children = null;`, and the comment beside it reasons only about the WORK
  tree ("a story hides its tasks"). It is one shared function, so the WORLD tree gets the same cut
  — and there level 2 is the layer words, which puts the capability nodes and every leaf under
  them behind a click on first paint. This is why *"I can't see node X on the world view"* almost
  always means *"it is connected, and collapsed"*, and why a `g.node` count that looks like a
  regression usually is not: deep-linking expands the target's ancestors, so the count legitimately
  rises.

## What the root file used to carry

Relocated out of `AGENTS.md` on 2026-08-28, because each of these only fires while editing this
directory. Nothing here was rewritten on the way.

- **Multi-root is the normal case, and the one starting point a layout needs is a *rendering
  anchor*** — invisible, unselectable, out of the tab order and out of `NODE_INDEX`, because a
  visible hub above the roots re-asserts the ownership the data stopped claiming. That defect shipped
  once already. See `map.js`'s `_anchor` handling and `board.html`'s `indexTreeNode`.
- **`node.name` carries two registers — prose and identifier — and the board renders it VERBATIM.**
  The obvious fix for the mixed capitalisation is an uppercase-first transform in `map.js`, and it is
  WRONG twice over: `node_types.PROSE_NAME_TYPES` says why, and `name_register_warnings()` **reports**
  on `/api/health` rather than transforming.
- The orphaned `login.html`, `logout.html`, `change-password.html` and `index.html` were removed on 2026-11-10; the auth pages are `_PAGE` in `auth.py`.
- **`navigator.clipboard` needs a secure context, and both copy paths need genuine user activation**,
  so a scripted `.click()` reports a false failure. See the copy handler in `frontend/settings.js`.
