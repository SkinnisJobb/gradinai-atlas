// @ts-check
// ui/panes.js — the panes and the bars between them (TASK-1308, TASK-1443).
//
// Every pane has a bar 20 px wide on its left edge, full height. A click on
// the bar collapses the pane to the bar alone and a second click expands it;
// dragging the same bar resizes the pane (TASK-1443: one bar does both). A
// collapsed pane's bar shows the pane's name, rotated to run along the bar.
//
// The graph takes whatever width is left. The work board and the node panel
// have widths you drag, clamped so no pane can be squeezed below a usable size.
// When the graph is collapsed, the first expanded pane takes the rest.
// `columns` is the arithmetic and is pure (tested in panes.test.js). `mountPanes`
// wires it to the DOM.
//
// A pane is one entry in `PANES` plus one in `MIN`, and a bar + section pair in
// index.html, so another pane is added there and nowhere else here.
//
// The dashboard (TASK-1447) is the leftmost pane and hidden until the person
// opens it from the top bar. The graph still flexes while it is expanded, so the
// dashboard has a width of its own. Its own bar sits on the stage's left edge,
// where a drag can only narrow it, so the graph's bar, which is the edge between
// the two, resizes the pane on its left (`dragTarget`).

/** The panes, left to right. The graph is the flexible one while it is expanded. */
export const PANES = Object.freeze(["dashboard", "graph", "board", "panel"]);
export const MIN = Object.freeze({ dashboard: 420, graph: 360, board: 320, panel: 340 });
export const BAR = 20;
/** The old name for the bar's width, from when it was only a splitter. */
export const SPLIT = BAR;
export const DEFAULT_PANEL = 480;
/** Room for two ~400 px dashboard boxes side by side. */
export const DEFAULT_DASHBOARD = 860;
export const DEFAULT_BOARD_SHARE = 0.36;
/** A pointer that moves less than this between down and up is a click. */
export const CLICK_SLOP = 4;

/**
 * Clamp the requested widths to what `total` can hold, and return the
 * `grid-template-columns` value. A hidden pane contributes nothing, neither
 * its bar nor its width. A collapsed pane contributes its bar only.
 *
 * `want` names each pane's state by `<name>On` (shown; the graph is always
 * shown unless `graphOn` is false), `<name>` (its width) and `collapsed[name]`.
 * @param {number} total  the stage's width in px
 * @param {{[k: string]: unknown, collapsed?: Record<string, boolean>}} want
 * @returns {{board: number, panel: number, dashboard: number, flex: string|null, open: string[], on: string[], widths: Record<string, number>, template: string}}
 */
export function columns(total, want) {
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const collapsed = want.collapsed || {};
  const on = PANES.filter((n) => (n === "graph" ? want.graphOn !== false : !!want[n + "On"]));
  const open = on.filter((n) => !collapsed[n]);
  // The graph flexes; failing that, the first expanded pane does; failing that, none.
  const flex = open.includes("graph") ? "graph" : open[0] || null;
  /** @type {Record<string, number>} */
  const widths = {};
  for (const n of PANES) {
    if (n === "graph") continue;
    widths[n] = Number(want[n]) || 0;
  }
  if (!widths.board) widths.board = Math.round(total * DEFAULT_BOARD_SHARE);
  if (!widths.dashboard) widths.dashboard = DEFAULT_DASHBOARD;
  // Clamp right to left, as before: each fixed pane gets at most what the bars,
  // the flexible pane's minimum and the other fixed panes leave it.
  const fixed = open.filter((n) => n !== flex);
  const bars = on.length * BAR;
  const flexMin = flex ? MIN[/** @type {keyof typeof MIN} */ (flex)] : 0;
  /** @type {Set<string>} */
  const done = new Set();
  for (const n of [...fixed].reverse()) {
    const lo = MIN[/** @type {keyof typeof MIN} */ (n)];
    let others = 0;
    for (const m of fixed) {
      if (m === n) continue;
      others += done.has(m) ? widths[m] : MIN[/** @type {keyof typeof MIN} */ (m)];
    }
    widths[n] = clamp(widths[n], lo, Math.max(lo, total - bars - flexMin - others));
    done.add(n);
  }
  const cols = [];
  for (const n of on) {
    cols.push(`${BAR}px`);
    if (collapsed[n]) continue;
    cols.push(n === flex ? `minmax(${flexMin}px, 1fr)` : `${widths[n]}px`);
  }
  return { board: widths.board, panel: widths.panel, dashboard: widths.dashboard, flex, open, on, widths, template: cols.join(" ") };
}

/**
 * Which pane a drag on `name`'s bar resizes, and which way. A bar sits on its
 * pane's left edge, so moving it right by dx changes the target's width by
 * `sign * dx`: -1 for the bar's own pane of fixed width, +1 for the pane on the
 * left of the flexible pane (the bar between them is the flexible pane's).
 * null: that bar only clicks.
 * @param {string} name
 * @param {{flex: string|null, open: string[], on: string[]}} layout  from `columns`
 * @returns {{pane: string, sign: 1 | -1} | null}
 */
export function dragTarget(name, layout) {
  if (!layout.open.includes(name)) return null;
  if (name !== layout.flex) return { pane: name, sign: -1 };
  const left = layout.on[layout.on.indexOf(name) - 1];
  return left && layout.open.includes(left) ? { pane: left, sign: 1 } : null;
}

/** The prefs key that keeps a pane's collapsed state. @param {string} name */
export const collapsedKey = (name) => `pane.${name}.collapsed`;

/**
 * Whether a pointer that went down at `x0` and moved at most `farthest` px
 * from it was a click, not a drag.
 * @param {number} farthest
 */
export const isClick = (farthest) => farthest < CLICK_SLOP;

/**
 * @param {HTMLElement} stage  a grid holding, in order, a bar then a pane for each of `PANES`
 * @param {ReturnType<typeof import("../core/prefs.js").createPrefs>} prefs
 * @param {{onSettle?: () => void}} [opts]  called when a resize ends (the graph re-fits)
 */
export function mountPanes(stage, prefs, opts = {}) {
  const el = (sel) => /** @type {HTMLElement} */ (stage.querySelector(sel));
  const pane = Object.fromEntries(PANES.map((n) => [n, el(`[data-pane=${n}]`)]));
  const bar = Object.fromEntries(PANES.map((n) => [n, el(`[data-split=${n}]`)]));
  /** @type {Record<string, number>} */
  const want = {
    board: prefs.recall("pane.board", 0),
    panel: prefs.recall("pane.panel", DEFAULT_PANEL),
    dashboard: prefs.recall("pane.dashboard", DEFAULT_DASHBOARD),
  };
  /** @type {Record<string, boolean>} */
  const collapsed = Object.fromEntries(PANES.map((n) => [n, !!prefs.recall(collapsedKey(n), false)]));
  const NAMES = { dashboard: "the dashboard", graph: "the graph", board: "the work board", panel: "the node panel" };
  /** @type {{flex: string|null, open: string[], on: string[]}} */
  let layout = { flex: "graph", open: [], on: [] };

  function apply() {
    // On a phone, and in a pop-out window, the stacking stays as it was: no bars, nothing collapsed.
    const narrow = window.matchMedia("(max-width: 760px)").matches || document.documentElement.hasAttribute("data-only");
    /** @type {Record<string, unknown>} */
    const state = { board: want.board, panel: want.panel, dashboard: want.dashboard, collapsed };
    for (const n of PANES) {
      const shown = !pane[n].hidden;
      state[n + "On"] = shown;
      bar[n].hidden = narrow || !shown;
      // `hidden` is the pane's owner's (app.js); collapsed is the bar's, and a separate attribute.
      pane[n].toggleAttribute("data-collapsed", !narrow && collapsed[n]);
      const label = NAMES[/** @type {keyof typeof NAMES} */ (n)] || n;
      bar[n].setAttribute("aria-expanded", String(!collapsed[n]));
      bar[n].setAttribute("aria-label", collapsed[n] ? `Expand ${label}` : `Collapse ${label}; drag to resize it`);
      bar[n].title = collapsed[n] ? `Expand ${label}` : `Click to collapse ${label}, drag to resize it`;
      // The name drawn along a collapsed bar (layout.css) is the pane's own aria-label.
      bar[n].dataset.label = pane[n].getAttribute("aria-label") || n;
    }
    if (narrow) { stage.style.gridTemplateColumns = ""; return; }
    const c = columns(stage.clientWidth, state);
    want.board = c.board; want.panel = c.panel; want.dashboard = c.dashboard; layout = c;
    for (const n of PANES) bar[n].classList.toggle("fixed", !!dragTarget(n, c));
    stage.style.gridTemplateColumns = c.template;
  }
  function save() {
    prefs.remember("pane.board", want.board);
    prefs.remember("pane.panel", want.panel);
    if (layout.on.includes("dashboard")) prefs.remember("pane.dashboard", want.dashboard);
    if (opts.onSettle) opts.onSettle();
  }
  /** @param {string} which */
  function toggle(which) {
    collapsed[which] = !collapsed[which];
    // Nothing stored is the default (expanded), so expanding clears the key.
    prefs.remember(collapsedKey(which), collapsed[which] ? true : undefined);
    apply();
    if (opts.onSettle) opts.onSettle();
  }
  /** What a drag on this bar resizes, if anything. @param {string} which */
  const target = (which) => dragTarget(which, layout);
  /** @param {string} n */
  const defaultWidth = (n) => (n === "panel" ? DEFAULT_PANEL : n === "dashboard" ? DEFAULT_DASHBOARD : Math.round(stage.clientWidth * DEFAULT_BOARD_SHARE));

  /** @param {HTMLElement} split @param {string} which */
  function wire(split, which) {
    split.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      split.setPointerCapture(e.pointerId);
      const t = target(which), x0 = e.clientX, w0 = t ? want[t.pane] : 0, drags = !!t;
      let farthest = 0;
      split.classList.add("drag");
      if (drags) document.body.classList.add("resizing");
      // The bar moves right: its own pane gets narrower, the one on its left wider.
      const move = (ev) => {
        farthest = Math.max(farthest, Math.abs(ev.clientX - x0));
        if (t && !isClick(farthest)) { want[t.pane] = w0 + t.sign * (ev.clientX - x0); apply(); }
      };
      const up = (ev) => {
        split.removeEventListener("pointermove", move);
        split.removeEventListener("pointerup", up);
        split.removeEventListener("pointercancel", up);
        split.classList.remove("drag"); document.body.classList.remove("resizing");
        if (ev.type === "pointerup" && isClick(farthest)) {
          if (t) want[t.pane] = w0;
          toggle(which);
        } else if (drags) save();
      };
      split.addEventListener("pointermove", move);
      split.addEventListener("pointerup", up);
      split.addEventListener("pointercancel", up);
    });
    split.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(which); return; }
      const t = target(which);
      if (!t) return;
      const step = e.shiftKey ? 96 : 24;
      if (e.key === "ArrowLeft") want[t.pane] -= t.sign * step;
      else if (e.key === "ArrowRight") want[t.pane] += t.sign * step;
      else return;
      e.preventDefault(); apply(); save();
    });
    split.addEventListener("dblclick", () => {
      // Two clicks have already collapsed and re-expanded the pane; this resets its width.
      const t = target(which);
      if (!t) return;
      want[t.pane] = defaultWidth(t.pane);
      apply(); save();
    });
  }
  for (const n of PANES) wire(bar[n], n);
  window.addEventListener("resize", apply);
  apply();
  /** Expand a collapsed pane (the top bar's Dashboard button). @param {string} which */
  const expand = (which) => { if (collapsed[which]) toggle(which); };
  return { apply, expand };
}
