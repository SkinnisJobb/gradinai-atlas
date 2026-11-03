// @ts-check
// core/router.js — the URL is a view of the store, and a deep link is a way in.
//
// `?node=<id>` means the same thing in both boards: open this node. Legacy also
// writes `?tab=`, which v2 has no use for. `format` leaves every parameter it
// does not own exactly where it was, so a link copied from one board opens the
// same node in the other (EPIC-atlas-board-cutover's acceptance).
//
// `parse` and `format` are pure. Only `bindUrl` touches `window`.

const STEPS_MIN = 1, STEPS_MAX = 4;

/**
 * @param {string} search  e.g. location.search
 * @returns {{node: string|null, focus: string|null, steps: number|null}}
 */
export function parse(search) {
  const p = new URLSearchParams(search);
  const steps = Number.parseInt(p.get("steps") || "", 10);
  return {
    node: p.get("node") || null,
    focus: p.get("focus") || null,
    steps: Number.isInteger(steps) && steps >= STEPS_MIN && steps <= STEPS_MAX ? steps : null,
  };
}

/**
 * The query string for `view`, keeping every parameter v2 does not own.
 * @param {string} search
 * @param {{node?: string|null, focus?: string|null, steps?: number|null}} view
 * @returns {string}  "" or "?..."
 */
export function format(search, view) {
  const p = new URLSearchParams(search);
  /** @type {Array<[string, string|null|undefined]>} */
  const owned = [["node", view.node], ["focus", view.focus],
    ["steps", view.steps == null ? null : String(view.steps)]];
  for (const [k, v] of owned) {
    if (v) p.set(k, v); else p.delete(k);
  }
  const qs = p.toString();
  return qs ? "?" + qs : "";
}

/**
 * Seed the store from the URL, then keep the URL in step with it. Uses
 * `replaceState`, as legacy's updateUrl does: selecting nodes is browsing a
 * view, not navigation, and Back should leave the board rather than walk back
 * through every click.
 * @param {import("./store.js").Store} store
 * @param {Window} [win]
 */
export function bindUrl(store, win = window) {
  const first = parse(win.location.search);
  store.set({
    selected: first.node,
    focus: first.focus,
    ...(first.steps ? { steps: first.steps } : {}),
  });
  return store.subscribe(["selected", "focus", "steps"], (s) => {
    const loc = win.location;
    const qs = format(loc.search, {
      node: /** @type {string|null} */ (s.selected),
      focus: /** @type {string|null} */ (s.focus),
      steps: /** @type {number|null} */ (s.steps),
    });
    win.history.replaceState(null, "", loc.pathname + qs + loc.hash);
  });
}
