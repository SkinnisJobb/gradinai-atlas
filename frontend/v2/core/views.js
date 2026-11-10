// @ts-check
// core/views.js — saved graph views: a name for a way of looking at the graph.
//
// A view holds the focus, the step settings, the hidden types and which nodes
// are collapsed, so swapping views swaps all of it. Views are a per-viewer
// convenience, kept with the other graph prefs (`atlas.v2.graph.views`), like
// the stars. No DOM here; views/graph/graph.js draws the menu.

/** The store keys a view captures, with what each falls back to. */
export const VIEW_KEYS = Object.freeze({ focus: null, steps: 2, links: "contains", work: false, hideTypes: [], collapsed: [] });

/**
 * @typedef {{name: string, focus: string|null, steps: number, links: string, work: boolean, hideTypes: string[], collapsed: string[]}} View
 */

/** A fresh, sorted copy of a list, so two views compare by content. @param {unknown} v */
const sortedList = (v) => (Array.isArray(v) ? [...new Set(v.map(String))].sort() : []);

/**
 * The current state as a view called `name`.
 * @param {Record<string, unknown>} state @param {string} name
 * @returns {View}
 */
export function snapshotView(state, name) {
  return {
    name: name.trim(),
    focus: state.focus == null ? null : String(state.focus),
    steps: Number(state.steps) || VIEW_KEYS.steps,
    links: state.links === "all" ? "all" : "contains",
    work: !!state.work,
    hideTypes: sortedList(state.hideTypes),
    collapsed: sortedList(state.collapsed),
  };
}

/** Whether the state shows exactly this view. @param {View} view @param {Record<string, unknown>} state */
export function viewMatches(view, state) {
  const now = snapshotView(state, view.name);
  return JSON.stringify(now) === JSON.stringify(normalise(view));
}

/** A stored view read back, tolerant of an older or hand-edited entry. @param {any} v @returns {View} */
function normalise(v) {
  return snapshotView({ ...VIEW_KEYS, ...(v && typeof v === "object" ? v : {}) }, String(v?.name ?? ""));
}

/**
 * The store patch that shows `view`. A focus the graph no longer holds falls
 * back to the roots, as a stale `?focus=` does.
 * @param {View} view @param {(id: string) => boolean} exists
 */
export function viewPatch(view, exists) {
  const v = normalise(view);
  const focus = v.focus && exists(v.focus) ? v.focus : null;
  return { focus, selected: focus, scope: focus, path: null, steps: v.steps, links: v.links, work: v.work, hideTypes: v.hideTypes, collapsed: v.collapsed };
}

/**
 * The list with `view` saved: replacing one of the same name (ignoring case),
 * else added at the end. Unnamed views are refused.
 * @param {unknown} list @param {View} view @returns {View[]}
 */
export function saveView(list, view) {
  const all = readViews(list);
  if (!view.name) return all;
  const at = all.findIndex((v) => v.name.toLowerCase() === view.name.toLowerCase());
  if (at >= 0) { all[at] = view; return all; }
  return [...all, view];
}

/** The list without the view called `name`. @param {unknown} list @param {string} name */
export function deleteView(list, name) {
  return readViews(list).filter((v) => v.name !== name);
}

/** Stored views, each normalised; anything unreadable is dropped. @param {unknown} list @returns {View[]} */
export function readViews(list) {
  return (Array.isArray(list) ? list : []).map(normalise).filter((v) => v.name);
}

/** A name not yet taken: "View 1", "View 2", … @param {unknown} list */
export function nextViewName(list) {
  const taken = new Set(readViews(list).map((v) => v.name.toLowerCase()));
  for (let i = 1; ; i++) if (!taken.has(`view ${i}`)) return `View ${i}`;
}
