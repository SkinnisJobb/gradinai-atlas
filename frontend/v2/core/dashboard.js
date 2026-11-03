// @ts-check
// core/dashboard.js — the dashboard's box contract and its arithmetic
// (TASK-1447, STORY-atlas-dashboard-shell). No DOM, so `node --test` covers it.
//
// A BOX is one small module that the dashboard pane draws in its grid:
//
//   defineBox({
//     id: "my-tasks",                     // unique, stable: it keys the collapsed state
//     title: "Mina tasks",
//     load: (api) => api.fetchJSON(...),  // returns the data, or a promise of it
//     count: (data) => data.items.length, // optional: the number beside the title
//     drawn: (data) => data.items.length > 0, // optional: false = the box is not drawn
//     render: (data, el) => { ... },      // draws into `el`, its body
//   })
//
// A box whose `drawn` is false is not drawn at all (empty boxes are not drawn);
// a box that wants an empty state leaves `drawn` out and draws one in `render`.
// A box whose load fails is drawn with the error, never silently dropped.
// The boxes live in views/dashboard/boxes.js; this file knows none of them.

/** About 400 px per box, up to 3 × 3 (the story). */
export const BOX_WIDTH = 400;
export const BOX_GAP = 12;
export const MAX_COLUMNS = 3;
export const MAX_ROWS = 3;
export const MAX_BOXES = MAX_COLUMNS * MAX_ROWS;
/** The prefs key holding the ids of the collapsed boxes (core/prefs: `atlas.v2.dashboard.collapsed`). */
export const COLLAPSED_KEY = "dashboard.collapsed";
/** The prefs key holding whether the dashboard pane is shown. Nothing stored: hidden. */
export const SHOWN_KEY = "dashboard.on";

/**
 * @typedef {object} Box
 * @property {string} id
 * @property {string} title
 * @property {(api: any) => unknown} load
 * @property {(data: any, el: any) => void} render
 * @property {((data: any) => number | null | undefined)} [count]
 * @property {((data: any) => boolean)} [drawn]
 */

/**
 * Check a box against the contract and freeze it. Throws a TypeError naming
 * what is wrong, so a broken box fails in its test rather than on the page.
 * @param {Box} spec
 * @returns {Readonly<Box>}
 */
export function defineBox(spec) {
  if (!spec || typeof spec !== "object") throw new TypeError("a box is an object");
  const name = typeof spec.id === "string" && spec.id ? spec.id : "?";
  if (name === "?" || !/^[a-z0-9][a-z0-9-]*$/.test(name)) throw new TypeError(`box id must be lowercase words joined by '-', got ${JSON.stringify(spec.id)}`);
  if (typeof spec.title !== "string" || !spec.title.trim()) throw new TypeError(`box ${name}: title must be a non-empty string`);
  for (const f of ["load", "render"]) {
    if (typeof spec[f] !== "function") throw new TypeError(`box ${name}: ${f} must be a function`);
  }
  for (const f of ["count", "drawn"]) {
    if (spec[f] != null && typeof spec[f] !== "function") throw new TypeError(`box ${name}: ${f}, when given, must be a function`);
  }
  return Object.freeze({ ...spec });
}

/**
 * The boxes in drawing order, each checked, with no id twice.
 * @param {Box[]} boxes
 * @returns {ReadonlyArray<Readonly<Box>>}
 */
export function registry(boxes) {
  const seen = new Set();
  const out = [];
  for (const b of boxes) {
    const box = defineBox(b);
    if (seen.has(box.id)) throw new TypeError(`box ${box.id} is registered twice`);
    seen.add(box.id);
    out.push(box);
  }
  return Object.freeze(out);
}

/**
 * How many columns of boxes a grid `width` px wide holds: 3, 2 or 1.
 * @param {number} width  the grid's inner width
 * @param {{box?: number, gap?: number, max?: number}} [opts]
 */
export function boxColumns(width, opts = {}) {
  const box = opts.box || BOX_WIDTH, gap = opts.gap ?? BOX_GAP, max = opts.max || MAX_COLUMNS;
  const fit = Math.floor((Number(width) + gap) / (box + gap));
  return Math.max(1, Math.min(max, Number.isFinite(fit) ? fit : 1));
}

/**
 * What is stored under COLLAPSED_KEY, made safe: a list of distinct ids.
 * Anything else (nothing stored, a hand-edited value) reads as none collapsed.
 * @param {unknown} raw
 * @returns {string[]}
 */
export function readCollapsed(raw) {
  if (!Array.isArray(raw)) return [];
  return [...new Set(raw.filter((x) => typeof x === "string" && x))];
}

/**
 * The collapsed list with `id` flipped.
 * @param {string[]} list @param {string} id
 * @returns {string[]}
 */
export function toggleCollapsed(list, id) {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
}

/**
 * What to store for a collapsed list: nothing when none is collapsed, so the
 * default leaves no key behind.
 * @param {string[]} list
 */
export const storedCollapsed = (list) => (list.length ? list : undefined);

/**
 * Load every box at once. One box failing never stops the others.
 * @param {ReadonlyArray<Readonly<Box>>} boxes
 * @param {unknown} api
 * @returns {Promise<Array<{box: Readonly<Box>, data?: unknown, error?: string}>>}
 */
export async function loadBoxes(boxes, api) {
  const settled = await Promise.allSettled(boxes.map((b) => Promise.resolve().then(() => b.load(api))));
  return settled.map((r, i) => (r.status === "fulfilled"
    ? { box: boxes[i], data: r.value }
    : { box: boxes[i], error: String((r.reason && r.reason.message) || r.reason || "could not load") }));
}

/**
 * @typedef {{id: string, title: string, box: Readonly<Box>, data: unknown, error: string|null, count: number|null, collapsed: boolean}} Planned
 */

/**
 * Which loaded boxes are drawn, in order, with their count and collapsed state.
 * A box whose `drawn(data)` is false is left out; a failed box is kept with its
 * error. At most MAX_BOXES are drawn.
 * @param {Array<{box: Readonly<Box>, data?: unknown, error?: string}>} loaded
 * @param {string[]} collapsed
 * @returns {Planned[]}
 */
export function planBoxes(loaded, collapsed) {
  /** @type {Planned[]} */
  const out = [];
  for (const { box, data, error } of loaded) {
    let err = error || null;
    if (!err && box.drawn) {
      try { if (box.drawn(data) === false) continue; } catch (e) { err = String((e && e.message) || e); }
    }
    let count = null;
    if (!err && box.count) {
      try {
        const n = box.count(data);
        count = typeof n === "number" && Number.isFinite(n) ? n : null;
      } catch { count = null; }
    }
    out.push({ id: box.id, title: box.title, box, data, error: err, count, collapsed: collapsed.includes(box.id) });
    if (out.length === MAX_BOXES) break;
  }
  return out;
}
