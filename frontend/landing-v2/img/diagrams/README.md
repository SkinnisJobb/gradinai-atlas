# Landing page diagrams

Hand-written, first-party SVG for the landing page (`docs/site/content.md` §19). Each diagram
teaches one mechanism and is drawn from the code, not from the prose: the code location behind
each one is a row in [`docs/site/sources.md`](../../../../docs/site/sources.md) (S78-S91).

| File | Section | Teaches |
|---|---|---|
| `hero-one-plan.svg` (800 wide), `hero-one-plan-narrow.svg` (400 wide, for phones) | 1 Hero, still frame | agents file into one board; a proposal waits on a person, who approves |
| `problem-before-after.svg` | 2 Problem | five chats versus one board |
| `hierarchy-tree.svg` | 3 Hierarchy | epic > story > task, and `works on` inherited down the work |
| `decide-approve.svg` | 5 Decisions | an agent proposes acceptance; only a signed-in person approves |
| `plan-on-top.svg` | 8 Starts with a map | the six-part map a fresh install starts with, and work planned on it |
| `context-ask-open.svg` | 9.1 Context, still frame | one capped answer of pointers, then open one document |
| `search-merge.svg` | 9.2 Search | hybrid ranking (RRF) and the keyword-only fallback |
| `rules-down-work-up.svg` | 9.3 Rules | intents inherited down; progress counted up |
| `agent-key.svg` | 10.1 Agent keys | the agent's own token, the pass-through MCP, the API's checks |
| `access-projects.svg` | 10.2 Roles | project roles, groups from sign-in, hidden means absent |
| `guardrails.svg` | 10.3 Guardrails | five refusals with their real codes |
| `how-it-works.svg` | 11 How it works | callers, a swappable front, one gate, one graph |
| `roadmap.svg` | 12 Roadmap | built today versus next |
| `getting-started.svg` | 16 Getting started | seven steps from `docs/oss/INSTALL.md` |

Only the hero needed a portrait variant. Every other diagram is drawn 400 units wide and
portrait, so the same file works on a phone and on a desktop.

## Use

Load with `<img src="img/diagrams/<slug>.svg" alt="">` plus a visible caption, or with the
diagram's own title as `alt`. Each file carries `<title>` and `<desc>` (`role="img"`,
`aria-labelledby`), and the `<desc>` states everything the picture shows, so it also serves as the
long description. Recommended display width: up to about 560 px for the 400-wide files and up to
800 px for the hero. Nothing has a fixed `width` or `height`, only a `viewBox`. Load them through
`<img>` (or `<picture>`), not inline: their `<style>` uses element selectors (`svg`, `text`) that
would leak onto the page if pasted into the HTML.

## Readability

The smallest text is 13 user units in a 400-unit viewBox, which is about 12 px when the image is
375 px wide. The 800-wide hero is meant for wide layouts only; below about 700 px use
`hero-one-plan-narrow.svg` (for example with `<picture>` and a `media` query).

## Palette

One internal `<style>` per file, identical across files: CSS custom properties on `svg`, redefined
under `@media (prefers-color-scheme: dark)`. That media query follows the viewer's system setting
when the SVG is loaded through `<img>`; a page-level theme toggle cannot reach inside an `<img>`.
Each diagram paints its own background (`--bg`) so its text stays readable on either page theme.

| Token | Light | Dark | Means |
|---|---|---|---|
| `--bg` | `#ffffff` | `#16191e` | background |
| `--surface` | `#f4f5f7` | `#21252c` | card fill |
| `--ink` | `#1d2127` | `#e8eaed` | text |
| `--muted` | `#58616c` | `#a6aeb8` | secondary text, arrows |
| `--line` | `#c5cbd3` | `#3d444f` | card borders, column rules |
| `--agent` / `--agent-soft` | `#2457c5` / `#e7eefc` | `#86abff` / `#1d2a45` | an agent |
| `--person` / `--person-soft` | `#0a7565` / `#e0f3ef` | `#55cdb8` / `#15322d` | a person, a part of the system |
| `--ok` / `--ok-soft` | `#1b7a34` / `#e3f3e7` | `#5cc777` / `#173020` | done, allowed, built |
| `--warn` / `--warn-soft` | `#a85300` / `#fdf0e0` | `#f2a457` / `#3a2a15` | a rule, an open question |
| `--bad` / `--bad-soft` | `#bf2a2a` / `#fbe9e9` | `#ff8585` / `#3d1d1d` | blocked, refused, removed |

Purple is not used: the board reserves it for "waiting on a human decision" (not built yet).

**Colour is never the only carrier.** Agents carry a robot glyph and people a person glyph; done
and allowed carry a tick, refused and removed a cross or a minus sign; blocked carries "!" and the
word; optional or not-yet-built things are dashed; every status is also written as text.

Type: `system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`, and
`ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace` for code. No web
font, no external reference, no script, no `<foreignObject>`.

## Editing

Change the SVG directly. Keep the `<style>` block identical across files (copy it from any one),
keep text at 13 units or larger, keep `<desc>` in step with the picture, and re-check the code row
in `sources.md` when the mechanism changes.
