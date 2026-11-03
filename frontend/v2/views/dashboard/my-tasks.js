// @ts-check
// views/dashboard/my-tasks.js — the My tasks box (TASK-1450): the signed-in
// person's open workitems, in_progress first, then by priority. 7 rows show,
// then the list scrolls inside the box; the title carries the count.
//
// Data: one `GET /api/workitems?assignee=me` read, open statuses only, without
// prose (the listing's default). Measured 2026-10-31 for 50 open items:
// 34 136 bytes raw, 7 792 gzipped, so no endpoint of its own (ADR 0040).
//
// Priority: the `priority` field (STORY-oss-14), and a `CRITICAL:` name prefix
// counts as critical, since that prefix was the priority before the field.
//
// A click selects the item in the board. A box cannot reach the store, so it
// asks by event, as a pane asks for a pop-out: app.js listens for
// `atlas:select`, selects the id and expands the node panel.

import { defineBox } from "../../core/dashboard.js";
import { workStatus } from "../../core/graph-model.js";
import { el, statusPill, priorityTag } from "../../ui/dom.js";

/** Rows visible before the list scrolls (the story). */
export const VISIBLE_ROWS = 7;
/** The statuses asked for: every open bucket the API filters on (attention.SETTABLE_STATUSES
 * without done/cancelled, plus the in-progress synonyms that are buckets of their own). */
export const OPEN_STATUSES = Object.freeze(["pending", "analyze", "in_progress", "blocked", "review", "in_review", "analysis"]);
/** Highest first; no priority sorts after low. */
const PRIORITY_ORDER = Object.freeze(["critical", "high", "medium", "low"]);
/** The board's event for "select this node". */
export const SELECT_EVENT = "atlas:select";

/**
 * A workitem's priority for this box: its field, or critical for a `CRITICAL:` name.
 * @param {{priority?: string|null, name?: string}} item
 * @returns {string|null}
 */
export function effectivePriority(item) {
  if (/^CRITICAL:/.test(item.name || "")) return "critical";
  return item.priority || null;
}

/**
 * The box's rows: open, not a decision, in_progress first, then by priority, then by id.
 * The status is the one the board draws (`workStatus`), so `wip` reads as in_progress.
 * @param {Array<any>} items  `/api/workitems` items
 * @returns {Array<{id: string, name: string, status: string, priority: string|null}>}
 */
export function taskRows(items) {
  const rank = (/** @type {string|null} */ p) => { const i = p ? PRIORITY_ORDER.indexOf(p) : -1; return i < 0 ? PRIORITY_ORDER.length : i; };
  return (items || [])
    .filter((i) => i && i.type !== "decision")
    .map((i) => ({ id: String(i.id), name: String(i.name || i.id), status: workStatus(i.status), priority: effectivePriority(i) }))
    .filter((r) => r.status !== "done" && r.status !== "cancelled")
    .sort((a, b) => (Number(b.status === "in_progress") - Number(a.status === "in_progress"))
      || (rank(a.priority) - rank(b.priority))
      || a.id.localeCompare(b.id, undefined, { numeric: true }));
}

/**
 * What `load` returns, turned into rows and a count. The count is the API's
 * `total` less what this page dropped, so it holds past one page.
 * @param {{items?: any[], total?: number}} data
 */
export function model(data) {
  const items = (data && data.items) || [];
  const rows = taskRows(items);
  const total = data && typeof data.total === "number" ? data.total : items.length;
  return { rows, count: Math.max(rows.length, total - (items.length - rows.length)) };
}

/** The read `load` makes. */
export const LOAD_URL = "/api/workitems?assignee=me&page_size=200&status=" + OPEN_STATUSES.join(",");

export default defineBox({
  id: "my-tasks",
  title: "My tasks",
  load: (api) => api.fetchJSON(LOAD_URL).then(model),
  count: (m) => m.count,
  render: (m, body) => {
    if (!m.rows.length) { body.append(el("p", "dash-none", "Nothing open is assigned to you.")); return; }
    const list = el("ul", "mt-list");
    list.style.setProperty("--mt-visible", String(VISIBLE_ROWS));
    for (const r of m.rows) {
      const li = el("li");
      const row = /** @type {HTMLButtonElement} */ (el("button", "mt-row"));
      row.type = "button";
      row.title = `${r.id}: ${r.name}`;
      row.append(el("span", "mt-id", r.id), el("span", "mt-name", r.name));
      const prio = priorityTag(r.priority); if (prio) row.append(prio);
      row.append(statusPill(r.status));
      row.addEventListener("click", () => window.dispatchEvent(new CustomEvent(SELECT_EVENT, { detail: r.id })));
      li.append(row);
      list.append(li);
    }
    body.append(list);
  },
});
