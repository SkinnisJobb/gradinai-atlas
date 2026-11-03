#!/usr/bin/env python3
"""The website's screenshots (TASK-1465), from the disposable demo install, with Playwright.

docs/site/content.md section 19 "Format": 1440 x 900 CSS pixels, captured at 2x, light theme,
cropped to the region named; each also gets a 390 px-wide crop for phones. The slugs are FIXED:
frontend/landing-v2 references img/shots/<slug>.png and <slug>-390.png.

  phase seed  fresh-seed-graph   the graph on the fresh install, BEFORE demo_work.py runs
  phase work  backlog-view, history-tab, proposal-overview, board-whole, my-tasks, docs-tab

Signs in as Alex through the real login form once, then opens each shot in a FRESH browser
context with only that session cookie, and the board's own per-viewer preferences set before
the page's first script runs (localStorage `atlas.theme` and `atlas.v2.*`, read by
frontend/v2/prepaint.js and core/prefs.js): which panes are open, their widths, the board view,
the panel tab. Each shot then WAITS ON THE CONDITION it shows (the rows, the diagram, the
selected node), never on a fixed delay alone. Motion is reduced, so the graph lays out without
its 450 ms transition.

Output, under --out (default shots/):
  <slug>.png        the named region at 2x, cropped to the landing page's frame: 16:10 for
                    the wide shots (backlog-view, board-whole, my-tasks, fresh-seed-graph),
                    4:5 for the panel shots (history-tab, proposal-overview, docs-tab); the
                    largest such rectangle in the region, centred on the focal element
  <slug>-390.png    3:4, 390 x 520 CSS px, made one of three ways per shot (Shot.phone):
                    narrow   the board's own phone layout (a 390 px window), cropped to the region
                    element  one element of the wide page (Shot.phone_focal, else the focal)
                             shown WHOLE, scaled down to fit, on the page's background
                    wide     the wide crop itself, scaled down to fit
  dom-text/<slug>.txt  the page's visible text at capture time, for the scrub scan
                    (<slug>-390.txt as well for a narrow phone page)

No crop edge cuts a line of text: a line the top or bottom edge would cut is left out of the
frame and its strip painted with the page's background (_trim); a line cut at the left or right
edge is reported as a warning, since that is a framing choice to fix in SHOTS.

    python tools/site/capture.py --dry-run                 # the shot list, no browser needed
    python tools/site/capture.py --phase seed
    python tools/site/capture.py --phase work --ids "$SMOKE_DIR/demo-ids.json"

Configuration is the install smoke's (tests/atlas_smoke.py): SMOKE_URL, SMOKE_CA, SMOKE_DIR.
Alex's password is read from SMOKE_DIR/alex.password when typed and never printed; no trace or
video is recorded, so no fill value is kept anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(HERE))

import demo_work  # noqa: E402  (data only; it imports nothing network-bound at module level)

VIEWPORT = {"width": 1440, "height": 900}
SCALE = 2
#: The landing page's fixed frames (frontend/landing-v2): width:height of each crop.
WIDE = (16, 10)                    # backlog-view, board-whole, my-tasks, fresh-seed-graph
PANEL = (4, 5)                     # history-tab, proposal-overview, docs-tab
PHONE = (3, 4)                     # every -390 crop
PHONE_W, PHONE_H = 390, 520        # 3:4 at 390 CSS px wide
#: A phone's window for Shot.phone "narrow": 60% of 867 is 520, so the panel's sheet
#: (styles/layout.css, max-width 760px) is exactly one 390 x 520 frame.
PHONE_VIEW = {"width": 390, "height": 867}
SEED_NODE = "atlas-backend"        # selected in board-whole; contains both demo epics' nodes
DOCS_NODE = "atlas-graph"          # one document, with a Mermaid diagram (self_seed_data.py)


@dataclass
class Shot:
    slug: str
    phase: str                      # seed | work
    query: str                      # /board?<query>; {T2}-style refs come from demo-ids.json
    prefs: dict
    region: str | None              # a CSS selector, or None for the whole viewport
    waits: list[tuple[str, int]]    # (selector, at least this many visible matches)
    focal: str | None = None        # what both crops centre on
    ratio: tuple[int, int] = WIDE   # the main crop's frame, width:height
    note: str = ""
    extra: dict = field(default_factory=dict)
    viewport: dict | None = None    # this shot's window, where VIEWPORT is too narrow for it
    hide: tuple[str, ...] = ()      # overlays kept out of frame (visibility: hidden, layout kept)
    pad: int | None = None          # crop to the focal box plus this padding, not the whole region
    top: bool = False               # the frame starts just above the focal, not centred on it
    phone: str = "element"          # how <slug>-390.png is made: narrow | element | wide
    phone_focal: str | None = None  # phone: the element to frame (narrow) or show whole (element);
                                    # "A | B" is the box from A's top to B's bottom
    phone_top: bool = False         # narrow: scroll the phone focal to the top, frame from there
    phone_pad: int | None = None    # element: padding around it (default: pad, else 12)


#: Prefs every shot starts from: light theme, nothing remembered from another shot.
BASE_PREFS = {"theme": "light", "board.on": False, "dashboard.on": False, "graph.work": False,
              "graph.links": "contains", "pane.panel.collapsed": None, "work.epic": None,
              "work.who": "all", "work.doneDays": 30, "work.fold": []}

SHOTS = [
    Shot("fresh-seed-graph", "seed", "focus=atlas&steps=1", {},
         "#paneGraph", [("#paneGraph svg.graph g.node", 7)], focal="#paneGraph g.node.focus",
         # The node counter (top left) and the legend (bottom left) sit at the pane's edges
         # and the crop cut them mid-word (views/graph/graph.js, styles/graph.css).
         hide=("#paneGraph .graph-status", "#paneGraph .graph-legend"), phone="narrow",
         note="the Atlas system node and its six parts, before any work exists"),
    Shot("backlog-view", "work", "focus=atlas",
         {"board.on": True, "work.view": "backlog", "work.epic": "{backlog-view}", "pane.board": 820},
         "#paneBoard", [("#paneBoard .backlog .brow", 8)], focal="#paneBoard .backlog .brow.type-epic",
         phone="narrow",
         note="Backlog view narrowed to one demo epic: epic > stories > tasks, each with its node chip"),
    Shot("history-tab", "work", "focus=atlas&node={history-tab}",
         {"panel.tab": "history", "pane.panel": 480},
         "#panePanel", [("#panePanel .events .event", 4), ("#panePanel .events .ev-behalf", 1)],
         focal="#panePanel .events .ev-behalf",
         # At 390 px the tab bar scrolls sideways and cuts "Docs": the wide page's tab bar
         # and history list instead, whole, scaled down.
         phone_focal="#panePanel .ptabs | #panePanel .events",
         ratio=PANEL, note="entries by Alex and by alex-claude, on behalf of Alex; panel 480 px"),
    Shot("proposal-overview", "work", "focus=atlas&node={proposal-overview}",
         {"panel.tab": "overview", "pane.panel": 480},
         "#panePanel", [("#panePanel .proposal .accdiff li", 4),
                        # Approve is the one primary button (views/panel/overview.js); plain
                        # CSS, since _wait_visible runs it through querySelectorAll.
                        ("#panePanel .proposal .pactions button.primary", 1)],
         focal="#panePanel .proposal",
         ratio=PANEL, note="a pending acceptance proposal: removed/added diff, Approve and Reject"),
    # Four kanban columns of two cards each need ~1300 px for a card to show its full id
    # (views/work/board.js: minmax(240px, 1fr) per column); 1760 x 1100 keeps 16:10 and
    # leaves the graph ~420 px on the left.
    Shot("board-whole", "work", f"focus=atlas&node={SEED_NODE}&steps=2",
         {"board.on": True, "work.view": "board", "pane.board": 1320, "pane.panel.collapsed": True},
         None, [("#paneGraph svg.graph g.node.selected", 1), ("#paneBoard .kanban .wcard", 10),
                ("#paneBoard .kanban .lane-head.epic", 2)],
         focal="#paneBoard .kanban .wcard.st-blocked",
         viewport={"width": 1760, "height": 1100}, phone="wide",
         note="graph with a seed node selected, Board view (two epic lanes), panel collapsed to its bar"),
    Shot("my-tasks", "work", "focus=atlas", {"dashboard.on": True},
         "#paneDashboard", [("#paneDashboard .dash-box[data-box='my-tasks'] .mt-row", 5)],
         focal="#paneDashboard .dash-box[data-box='my-tasks']", pad=32,
         note="the Dashboard pane at its default width, My tasks with six rows"),
    Shot("docs-tab", "work", f"focus=atlas&node={DOCS_NODE}", {"panel.tab": "docs", "pane.panel": 640},
         "#panePanel", [("#panePanel details.docrow[open] .docbody pre.mermaid svg", 1)],
         # The open document from its header row (kind, title, path) down: centred on the
         # diagram, the frame's top cut the doc's first heading.
         focal="#panePanel details.docrow[open]", top=True,
         # The phone sheet is too short for header and diagram: both, whole, from the wide page.
         phone_focal="#panePanel details.docrow[open] > summary | #panePanel details.docrow[open] pre.mermaid",
         phone_pad=4,                # 12 showed the top of the next line's code chip
         ratio=PANEL, note="a seed node's document with a rendered Mermaid diagram; panel 640 px"),
]


def resolve(text: str, targets: dict[str, str]) -> str:
    for k, v in targets.items():
        text = text.replace("{" + k + "}", v)
    return text


def storage_items(shot: Shot, targets: dict[str, str]) -> dict[str, str]:
    """localStorage as the board reads it: `atlas.theme` raw, `atlas.v2.<key>` as JSON."""
    out: dict[str, str] = {}
    for k, v in {**BASE_PREFS, **shot.prefs}.items():
        if v is None:
            continue
        if isinstance(v, str):
            v = resolve(v, targets)
        out["atlas.theme" if k == "theme" else "atlas.v2." + k] = v if k == "theme" else json.dumps(v)
    return out


def dry_run(phases: list[str], targets: dict[str, str]) -> int:
    for s in SHOTS:
        if s.phase not in phases:
            continue
        print(f"{s.slug}  [{s.phase}]  {s.note}")
        print(f"  open    /board?{resolve(s.query, targets)}")
        print(f"  prefs   {json.dumps(storage_items(s, targets))}")
        print(f"  region  {s.region or 'the whole viewport'}, cropped to {s.ratio[0]}:{s.ratio[1]} "
              f"around {s.focal or 'its top left'}")
        if s.viewport:
            print(f"  window  {s.viewport['width']} x {s.viewport['height']}")
        if s.hide:
            print(f"  hide    {', '.join(s.hide)}")
        if s.pad is not None:
            print(f"  crop    the focal box plus {s.pad} px, widened to {s.ratio[0]}:{s.ratio[1]}")
        if s.top:
            print("  frame   from just above the focal down")
        print(f"  phone   {s.phone}" + (f"  {s.phone_focal or s.focal}" if s.phone != "wide" else "")
              + ("  (from its top)" if s.phone_top else ""))
        for sel, n in s.waits:
            print(f"  wait    {n}+ visible  {sel}")
        print(f"  writes  {s.slug}.png ({s.ratio[0]}:{s.ratio[1]}, the largest that fits the region, @{SCALE}x), "
              f"{s.slug}-390.png ({PHONE[0]}:{PHONE[1]}, {PHONE_W}x{PHONE_H} css), dom-text/{s.slug}.txt")
    return 0


# --------------------------------------------------------------------------- #
# The browser
# --------------------------------------------------------------------------- #
def _on_board(url: str) -> bool:
    """The board itself, not the login page that names /board as where to go next."""
    from urllib.parse import urlparse
    return urlparse(url).path == "/board"


def _sign_in(browser, smoke) -> list[dict]:
    """Alex's session cookie, from the real login form."""
    ctx = browser.new_context(viewport=VIEWPORT)
    page = ctx.new_page()
    page.goto(smoke.URL + "/board")
    page.wait_for_url("**/auth/login**")
    page.fill("#username", demo_work.PERSON)
    page.fill("#password", smoke.secret(f"{demo_work.PERSON}.password"))
    page.click("button[type=submit]")
    page.wait_for_url(_on_board)
    cookies = ctx.cookies()
    ctx.close()
    print(f"signed in as {demo_work.PERSON}")
    return cookies


def _wait_visible(page, sel: str, n: int, timeout_ms: int = 45_000) -> None:
    """At least `n` VISIBLE matches of `sel`: the condition the shot shows."""
    page.wait_for_function(
        """([sel, n]) => [...document.querySelectorAll(sel)].filter((e) => {
             const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0;
           }).length >= n""",
        arg=[sel, n], timeout=timeout_ms)


def _settle(page, region: str | None) -> None:
    """Fonts loaded, no VISIBLE "Loading…" left in the region, two frames painted. The panel
    seeds every tab body with one and builds only the open tab; the others stay hidden
    (views/panel/panel.js), so a hidden one never clears and is not part of the shot."""
    page.evaluate("document.fonts.ready.then(() => true)")
    scope = region or "body"
    page.wait_for_function(
        """(sel) => { const r = document.querySelector(sel); if (!r) return false;
             return ![...r.querySelectorAll('.empty')].some((e) => {
               const b = e.getBoundingClientRect();
               return b.width > 0 && b.height > 0 && /Loading/.test(e.textContent || ''); }); }""",
        arg=scope, timeout=30_000)
    page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(true))))")
    # The graph places its labels on a 60 ms timer after layout (views/graph/graph.js).
    page.wait_for_timeout(300)


def _box(page, region: str | None, vp: dict = VIEWPORT) -> dict:
    if region is None:
        return {"x": 0, "y": 0, "width": vp["width"], "height": vp["height"]}
    b = page.locator(region).first.bounding_box()
    if not b:
        raise SystemExit(f"region {region} has no box")
    # Clip to the viewport: the shot is of what the window (1440 x 900 unless the shot says) shows.
    x0, y0 = max(0, b["x"]), max(0, b["y"])
    x1, y1 = min(vp["width"], b["x"] + b["width"]), min(vp["height"], b["y"] + b["height"])
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def fit_clip(box: dict, ratio: tuple[int, int], focal: dict | None,
             max_w: float | None = None, max_h: float | None = None, top: bool = False) -> dict:
    """The largest `ratio` rectangle inside `box` (and inside max_w x max_h), centred on
    `focal`'s box where there is one (or starting just above it, `top`), else at the box's
    top left; never outside `box`."""
    rw, rh = ratio
    w = min(box["width"], max_w or box["width"])
    h = min(box["height"], max_h or box["height"])
    if w * rh > h * rw:
        w = h * rw / rh
    else:
        h = w * rh / rw
    fx, fy = box["x"], box["y"]
    if focal:
        fx = focal["x"] + focal["width"] / 2 - w / 2
        fy = focal["y"] - 12 if top else focal["y"] + focal["height"] / 2 - h / 2
    x = min(max(fx, box["x"]), box["x"] + box["width"] - w)
    y = min(max(fy, box["y"]), box["y"] + box["height"] - h)
    return {"x": round(x, 2), "y": round(y, 2), "width": round(w, 2), "height": round(h, 2)}


def _focal_box(page, focal: str | None) -> dict | None:
    if not focal or not page.locator(focal).count():
        return None
    return page.locator(focal).first.bounding_box()


def _span_box(page, spec: str) -> dict | None:
    """The box of `spec`, or for "A | B" the box from A's top to B's bottom, over both widths."""
    boxes = [_focal_box(page, sel.strip()) for sel in spec.split("|")]
    if not all(boxes):
        return None
    x0, y0 = min(b["x"] for b in boxes), boxes[0]["y"]
    x1 = max(b["x"] + b["width"] for b in boxes)
    y1 = boxes[-1]["y"] + boxes[-1]["height"]
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def _inside(inner: dict, outer: dict) -> bool:
    return (inner["x"] >= outer["x"] and inner["y"] >= outer["y"]
            and inner["x"] + inner["width"] <= outer["x"] + outer["width"]
            and inner["y"] + inner["height"] <= outer["y"] + outer["height"])


def _scroll_focal_in(page, focal: str | None, box: dict, top: bool = False) -> None:
    """A focal not wholly in the region's visible box (the proposal, low in the Overview
    tab's scrolling body) is scrolled to the middle (or the top) of its scroll container,
    then painted."""
    fb = _focal_box(page, focal)
    if not fb or _inside(fb, box):
        return
    # To the top: clear the panel's sticky tab bar, which would otherwise cover the focal.
    page.locator(focal).first.evaluate(
        "(e, b) => { if (b === 'start') e.style.scrollMarginTop = '64px';"
        " e.scrollIntoView({block: b, inline: 'nearest'}); }", "start" if top else "center")
    page.evaluate("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(true))))")


def _around(fb: dict, pad: int, ratio: tuple[int, int]) -> tuple[float, float]:
    """The smallest `ratio` frame holding `fb` plus `pad` on every side."""
    rw, rh = ratio
    w, h = fb["width"] + 2 * pad, fb["height"] + 2 * pad
    return (h * rw / rh, h) if w * rh < h * rw else (w, w * rh / rw)


#: The visible part of every text line on the page (clipped by each overflow ancestor), then
#: the crop's top and bottom pulled in past any line they would cut, or that its container
#: already cuts at that edge. Returns the new top and height, and how many lines the left or
#: right edge cuts (those are reported, not moved: it is a framing fault).
_TRIM_JS = """([x, y, w, h]) => {
  const L = x, R = x + w; let T = y, B = y + h; const lines = [];
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = tw.nextNode());) {
    const el = n.parentElement;
    if (!el || !n.textContent.trim() || getComputedStyle(el).visibility !== 'visible') continue;
    let c = [-1e9, -1e9, 1e9, 1e9];
    for (let a = el; a && a !== document.documentElement; a = a.parentElement) {
      const st = getComputedStyle(a);
      if (st.overflowX === 'visible' && st.overflowY === 'visible') continue;
      const q = a.getBoundingClientRect();
      c = [Math.max(c[0], q.left), Math.max(c[1], q.top), Math.min(c[2], q.right), Math.min(c[3], q.bottom)];
    }
    const rg = document.createRange(); rg.selectNodeContents(n);
    for (const q of rg.getClientRects()) {
      const v = [Math.max(q.left, c[0]), Math.max(q.top, c[1]), Math.min(q.right, c[2]), Math.min(q.bottom, c[3])];
      if (v[2] - v[0] < 1 || v[3] - v[1] < 1) continue;
      const lh = q.bottom - q.top;
      lines.push({v, ct: q.top < c[1] - lh / 4, cb: q.bottom > c[3] + lh / 4,
                  cx: q.left < c[0] - 0.5 || q.right > c[2] + 0.5, t: n.textContent.trim().slice(0, 30)});
    }
  }
  for (let moved = true, i = 0; moved && i < 100; i++) {
    moved = false;
    for (const {v, ct, cb} of lines) {
      if (v[2] <= L || v[0] >= R || v[3] <= T || v[1] >= B) continue;
      // Cut by the crop edge, or by its own container (a scrolling body's end) in that half.
      if (v[1] < T - 0.5 || (ct && v[1] < (T + B) / 2)) { T = v[3]; moved = true; }
      else if (v[3] > B + 0.5 || (cb && v[3] > (T + B) / 2)) { B = v[1]; moved = true; }
    }
  }
  const cut = lines.filter(({v, cx}) => v[3] > T && v[1] < B && v[2] > L && v[0] < R
    && (v[0] < L - 0.5 || v[2] > R + 0.5 || (cx && (v[0] < L + 2 || v[2] > R - 2)))).map((l) => l.t);
  return {y: T, height: Math.max(0, B - T), cut};
}"""

_BG_JS = """(sel) => { let e = sel ? document.querySelector(sel) : document.body;
  for (e = e && (e.parentElement || e); e; e = e.parentElement) {
    const c = getComputedStyle(e).backgroundColor;
    if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') return c; }
  return '#ffffff'; }"""


def _trim(page, slug: str, clip: dict) -> dict:
    """`clip` with its top and bottom moved in past every text line they would cut."""
    r = page.evaluate(_TRIM_JS, [clip["x"], clip["y"], clip["width"], clip["height"]])
    if r["cut"]:
        print(f"{slug}: WARNING {len(r['cut'])} text line(s) cut at the left or right edge: "
              f"{r['cut'][:5]}", file=sys.stderr)
    if r["height"] < clip["height"] * 0.5:
        print(f"{slug}: WARNING the no-cut trim would leave {r['height']:.0f} of {clip['height']:.0f} px; "
              "kept the crop as it was", file=sys.stderr)
        return clip
    return {**clip, "y": round(r["y"], 2), "height": round(r["height"], 2)}


def _intersect(a: dict, b: dict) -> dict:
    x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x1 = min(a["x"] + a["width"], b["x"] + b["width"])
    y1 = min(a["y"] + a["height"], b["y"] + b["height"])
    return {"x": x0, "y": y0, "width": max(0, x1 - x0), "height": max(0, y1 - y0)}


def _place(cp, page, clip: dict, frame: tuple[float, float], at: tuple[float, float],
           scale: float, bg: str, path: Path) -> None:
    """`clip` of `page`, scaled by `scale`, at `at` on a `frame`-sized canvas of `bg`."""
    import base64
    png = base64.b64encode(page.screenshot(clip=clip, animations="disabled")).decode("ascii")
    cp.set_viewport_size({"width": round(frame[0]), "height": round(frame[1])})
    cp.set_content(
        f'<html><body style="margin:0;overflow:hidden;background:{bg}">'
        f'<img id="i" alt="" src="data:image/png;base64,{png}" style="position:absolute;'
        f'left:{at[0]:.2f}px;top:{at[1]:.2f}px;width:{clip["width"] * scale:.2f}px;'
        f'height:{clip["height"] * scale:.2f}px"></body></html>')
    cp.evaluate("document.getElementById('i').decode().then(() => true)")
    cp.screenshot(path=str(path), animations="disabled")


def _whole(cp, page, slug: str, clip: dict, bg: str, path: Path) -> None:
    """`clip`, no line cut, scaled down (never up) to fit 390 x 520 and centred there."""
    clip = _trim(page, slug, clip)
    k = min(1.0, PHONE_W / clip["width"], PHONE_H / clip["height"])
    _place(cp, page, clip, (PHONE_W, PHONE_H),
           ((PHONE_W - clip["width"] * k) / 2, (PHONE_H - clip["height"] * k) / 2), k, bg, path)


def _open_mermaid_doc(page, smoke, node: str) -> None:
    """A node with several documents opens none of them: open the one with a diagram."""
    rows = page.locator("#panePanel details.docrow")
    _wait_visible(page, "#panePanel details.docrow", 1)
    if rows.count() == 1 or page.locator("#panePanel details.docrow[open]").count():
        return
    for i in range(rows.count()):
        path = rows.nth(i).locator("summary .pid").inner_text().strip()
        r = page.request.get(f"{smoke.URL}/api/node/{node}/doc", params={"path": path})
        if r.ok and "```mermaid" in (r.json().get("content") or ""):
            rows.nth(i).locator("summary").click()
            return
    raise SystemExit(f"{node}: no document with a Mermaid diagram")


def _open(browser, ctxs: list, cookies, smoke, s: Shot, targets: dict[str, str], vp: dict):
    """A fresh context at `vp` with only the session cookie and this shot's prefs, on the
    board, waited and settled: what the shot shows, with nothing hovered or focused."""
    ctx = browser.new_context(
        viewport=vp, device_scale_factor=SCALE, color_scheme="light",
        reduced_motion="reduce", locale="en-US", timezone_id="UTC",
        storage_state={"cookies": cookies, "origins": []})
    ctxs.append(ctx)
    items = storage_items(s, targets)
    # Before the board's first script (prepaint.js reads the theme in <head>).
    ctx.add_init_script(
        "(() => { try { const items = " + json.dumps(items) + ";"
        " for (const k of Object.keys(items)) localStorage.setItem(k, items[k]); }"
        " catch (e) { /* storage blocked: the shot fails its waits */ } })();")
    page = ctx.new_page()
    page.goto(f"{smoke.URL}/board?{resolve(s.query, targets)}")
    page.wait_for_url(_on_board, timeout=30_000)
    if s.hide:
        page.add_style_tag(content=", ".join(s.hide) + " { visibility: hidden !important; }")
    if s.slug == "docs-tab":
        _open_mermaid_doc(page, smoke, DOCS_NODE)
    for sel, n in s.waits:
        _wait_visible(page, sel, n)
    _settle(page, s.region)
    page.mouse.move(0, vp["height"] - 1)
    page.evaluate("document.activeElement && document.activeElement.blur && document.activeElement.blur()")
    page.wait_for_timeout(100)
    return page


def capture(phases: list[str], targets: dict[str, str], out: Path) -> int:
    from playwright.sync_api import sync_playwright
    import atlas_smoke as smoke

    host = smoke.URL.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
    (out / "dom-text").mkdir(parents=True, exist_ok=True)
    made: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=[f"--host-resolver-rules=MAP {host} 127.0.0.1"])
        try:
            cookies = _sign_in(browser, smoke)
            # The canvas _place draws a trimmed or scaled crop on: same scale, nothing else on it.
            cp = browser.new_context(device_scale_factor=SCALE, color_scheme="light").new_page()
            for s in SHOTS:
                if s.phase not in phases:
                    continue
                ctxs = []
                page = None
                try:
                    vp = s.viewport or VIEWPORT
                    page = _open(browser, ctxs, cookies, smoke, s, targets, vp)
                    box = _box(page, s.region, vp)
                    _scroll_focal_in(page, s.focal, box, s.top)
                    fb = _focal_box(page, s.focal)
                    main = fit_clip(box, s.ratio, fb, *(_around(fb, s.pad, s.ratio) if fb and s.pad is not None else ()),
                                    top=s.top)
                    if fb and not _inside(fb, main):
                        print(f"{s.slug}: WARNING the focal {s.focal} does not fit whole in the crop", file=sys.stderr)
                    bg = page.evaluate(_BG_JS, s.region)
                    kept = _trim(page, s.slug, main)
                    if kept == main:
                        page.screenshot(path=str(out / f"{s.slug}.png"), clip=main, animations="disabled")
                    else:
                        _place(cp, page, kept, (main["width"], main["height"]),
                               (0, kept["y"] - main["y"]), 1.0, bg, out / f"{s.slug}.png")
                    (out / "dom-text" / f"{s.slug}.txt").write_text(page.inner_text("body") + "\n", encoding="utf-8")
                    phone = out / f"{s.slug}-390.png"
                    if s.phone == "wide":
                        _whole(cp, page, s.slug + "-390", main, bg, phone)
                    elif s.phone == "element":
                        sel = s.phone_focal or s.focal
                        eb = _span_box(page, sel)
                        if not eb:
                            raise SystemExit(f"{s.slug}: phone element {sel} has no box")
                        pad = next(v for v in (s.phone_pad, s.pad, 12) if v is not None)
                        eb = {"x": eb["x"] - pad, "y": eb["y"] - pad,
                              "width": eb["width"] + 2 * pad, "height": eb["height"] + 2 * pad}
                        _whole(cp, page, s.slug + "-390", _intersect(eb, box),
                               page.evaluate(_BG_JS, sel.split("|")[0].strip()), phone)
                    else:
                        # The board's own phone layout: a 390 px window, the panel a sheet over the graph.
                        pp = page = _open(browser, ctxs, cookies, smoke, s, targets, PHONE_VIEW)
                        pbox = _box(pp, s.region, PHONE_VIEW)
                        sel = s.phone_focal or s.focal
                        _scroll_focal_in(pp, sel, pbox, s.phone_top)
                        pf = _focal_box(pp, sel)
                        # The region's whole width, never narrowed to keep 3:4 (that cut both
                        # sides); up to 520 px of its height around the focal, padded to 3:4.
                        h = min(pbox["height"], PHONE_H)
                        y = pbox["y"]
                        if pf:
                            y = pf["y"] - 12 if s.phone_top else pf["y"] + pf["height"] / 2 - h / 2
                            y = min(max(y, pbox["y"]), pbox["y"] + pbox["height"] - h)
                        crop = {"x": pbox["x"], "y": round(y, 2), "width": min(pbox["width"], PHONE_W),
                                "height": round(h, 2)}
                        kept = _trim(pp, s.slug + "-390", crop)
                        _place(cp, pp, kept, (PHONE_W, PHONE_H),
                               ((PHONE_W - crop["width"]) / 2, (PHONE_H - crop["height"]) / 2 + kept["y"] - crop["y"]),
                               1.0, pp.evaluate(_BG_JS, s.region), phone)
                        (out / "dom-text" / f"{s.slug}-390.txt").write_text(pp.inner_text("body") + "\n", encoding="utf-8")
                    made.append(s.slug)
                    print(f"{s.slug}: {int(main['width'])}x{int(main['height'])} css -> {s.slug}.png, "
                          f"{s.slug}-390.png ({s.phone})")
                except Exception:
                    if page is not None:
                        page.screenshot(path=str(out / f"failed-{s.slug}.png"))
                    print(f"{s.slug}: FAILED (failed-{s.slug}.png is the page at that moment)", file=sys.stderr)
                    raise
                finally:
                    for c in ctxs:
                        c.close()
        finally:
            browser.close()
    print(f"{len(made)} shots: {', '.join(made)}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--phase", choices=("seed", "work", "all"), default="all")
    ap.add_argument("--ids", default=None, help="demo_work.py's ids file (needed for phase work)")
    ap.add_argument("--out", default="shots")
    ap.add_argument("--dry-run", action="store_true", help="print the shot list; open no browser")
    a = ap.parse_args(argv)
    phases = ["seed", "work"] if a.phase == "all" else [a.phase]
    targets: dict[str, str] = {k: f"<{v}>" for k, v in demo_work.SHOT_TARGETS.items()}
    if a.ids and Path(a.ids).exists():
        targets = json.loads(Path(a.ids).read_text(encoding="utf-8"))["targets"]
    elif "work" in phases and not a.dry_run:
        ap.error("phase work needs --ids (demo_work.py work writes it)")
    if a.dry_run:
        return dry_run(phases, targets)
    return capture(phases, targets, Path(a.out))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
