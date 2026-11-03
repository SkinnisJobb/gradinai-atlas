// @ts-check
// views/dashboard/dashboard.js — the dashboard pane's DOM (TASK-1447).
//
// A grid of boxes about 400 px wide, as many columns as the pane's width holds
// (3, 2 or 1: `boxColumns`). Each box has a title, an optional count and a
// collapse toggle; the collapsed boxes are kept in core/prefs under
// `dashboard.collapsed`. What a box is, and which are drawn, is core/dashboard.js.

import { registry, boxColumns, readCollapsed, toggleCollapsed, storedCollapsed, loadBoxes, planBoxes, COLLAPSED_KEY, BOX_GAP } from "../../core/dashboard.js";
import { el } from "../../ui/dom.js";

/**
 * @param {HTMLElement} pane
 * @param {{boxes: import("../../core/dashboard.js").Box[], prefs: ReturnType<typeof import("../../core/prefs.js").createPrefs>, api: unknown}} opts
 * @returns {{refresh: () => Promise<void>}}
 */
export function mountDashboard(pane, opts) {
  const boxes = registry(opts.boxes);
  const head = el("div", "dash-head");
  head.append(el("h2", null, "Dashboard"));
  const grid = el("div", "dash-grid");
  grid.setAttribute("aria-busy", "true");
  pane.replaceChildren(head, grid);

  let collapsed = readCollapsed(opts.prefs.recall(COLLAPSED_KEY, []));
  /** @type {import("../../core/dashboard.js").Planned[]} */
  let plan = [];
  let loaded = false;

  const fit = () => {
    const cs = getComputedStyle(grid);
    const inner = grid.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0);
    if (inner > 0) grid.style.setProperty("--dash-cols", String(boxColumns(inner, { gap: BOX_GAP })));
  };
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(fit).observe(grid);
  else window.addEventListener("resize", fit);

  /** @param {string} title @param {string} what */
  const empty = (title, what) => {
    const box = el("div", "placeholder dash-empty");
    box.append(el("h2", null, title), el("p", null, what));
    return box;
  };

  function draw() {
    if (!loaded) return;
    if (!boxes.length) {
      grid.replaceChildren(empty("Your dashboard is empty", "Boxes for your decisions and your work will appear here."));
      return;
    }
    if (!plan.length) {
      grid.replaceChildren(empty("Nothing waits on you", "Every box is empty right now."));
      return;
    }
    grid.replaceChildren(...plan.map(drawBox));
    fit();
  }

  /** @param {import("../../core/dashboard.js").Planned} p */
  function drawBox(p) {
    const box = el("section", "dash-box");
    box.dataset.box = p.id;
    const bodyId = `dash-box-${p.id}`;
    // The accordion pattern: the heading holds the toggle, the toggle names the body.
    const heading = el("h3", "dash-box-head");
    const toggle = /** @type {HTMLButtonElement} */ (el("button", "dash-toggle"));
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", String(!p.collapsed));
    toggle.setAttribute("aria-controls", bodyId);
    toggle.title = p.collapsed ? `Expand ${p.title}` : `Collapse ${p.title}`;
    toggle.append(el("span", "dash-chev"), el("span", "dash-title", p.title));
    if (p.count != null) toggle.append(el("span", "dash-count", p.count));
    toggle.addEventListener("click", () => {
      collapsed = toggleCollapsed(collapsed, p.id);
      opts.prefs.remember(COLLAPSED_KEY, storedCollapsed(collapsed));
      plan = plan.map((q) => ({ ...q, collapsed: collapsed.includes(q.id) }));
      draw();
      const again = /** @type {HTMLElement|null} */ (grid.querySelector(`[data-box="${p.id}"] .dash-toggle`));
      if (again) again.focus();
    });
    heading.append(toggle);
    const body = el("div", "dash-box-body");
    body.id = bodyId;
    body.hidden = p.collapsed;
    box.toggleAttribute("data-collapsed", p.collapsed);
    if (!p.collapsed) {
      if (p.error) body.append(el("p", "dash-error", `Could not load: ${p.error}`));
      else {
        try { p.box.render(p.data, body); } catch (e) {
          body.replaceChildren(el("p", "dash-error", `Could not draw: ${String((e && e.message) || e)}`));
        }
      }
    }
    box.append(heading, body);
    return box;
  }

  async function refresh() {
    const results = await loadBoxes(boxes, opts.api);
    plan = planBoxes(results, collapsed);
    loaded = true;
    grid.removeAttribute("aria-busy");
    draw();
  }
  refresh();
  return { refresh };
}
