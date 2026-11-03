// @ts-check
// views/search.js — find a node by name or id (parity with legacy's node
// search). It searches the graph already loaded, so it answers as you type and
// cannot show a node this caller may not see. Choosing a world node focuses
// it; choosing work selects it and opens its panel.

import { el } from "../ui/dom.js";
import { cmpNodes } from "../core/graph-model.js";

const MAX = 12;

/**
 * Pure ranking, so it is tested: an exact id first, then names that start with
 * the query, then names or ids that contain it. World nodes come before work
 * within each band.
 * @param {import("../core/graph-model.js").Index} ix @param {string} q
 */
export function findNodes(ix, q) {
  const t = q.trim().toLowerCase();
  if (!t) return [];
  const cmp = cmpNodes(ix), bands = [[], [], []];
  for (const n of ix.byId.values()) {
    const name = n.name.toLowerCase(), id = n.id.toLowerCase();
    if (id === t) bands[0].push(n.id);
    else if (name.startsWith(t) || id.startsWith(t)) bands[1].push(n.id);
    else if (name.includes(t) || id.includes(t)) bands[2].push(n.id);
  }
  return bands.flatMap((b) => b.sort(cmp)).slice(0, MAX);
}

/**
 * @param {import("../core/store.js").Store} store
 * @param {import("../core/graph-model.js").Index} ix
 * @param {{id?: string, placeholder?: string, onPick?: (id: string) => void}} [opts]
 *   with `onPick`, a picker: choosing calls it instead of focusing the node, and
 *   the "/" shortcut stays with the main search
 */
export function searchBox(store, ix, opts = {}) {
  const box = el("div", "search");
  const sid = opts.id || "nodeSearch";
  const input = /** @type {HTMLInputElement} */ (el("input", "bsearch"));
  input.type = "search"; input.id = sid; input.placeholder = opts.placeholder || "Find a node…"; input.autocomplete = "off";
  input.setAttribute("role", "combobox"); input.setAttribute("aria-expanded", "false"); input.setAttribute("aria-controls", sid + "List");
  const list = el("ul", "search-list"); list.id = sid + "List"; list.setAttribute("role", "listbox"); list.hidden = true;
  let hits = /** @type {string[]} */ ([]), active = 0;

  /** @param {string} id */
  function choose(id) {
    const n = ix.byId.get(id);
    if (!n) return;
    if (opts.onPick) { opts.onPick(id); input.value = ""; close(); return; }
    if (n.isWork) store.set({ selected: id });
    else store.set({ focus: id, selected: id, scope: id });
    input.value = ""; close();
  }
  function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); }
  function paint() {
    list.replaceChildren();
    hits.forEach((id, i) => {
      const n = /** @type {any} */ (ix.byId.get(id));
      const li = el("li", "search-hit" + (i === active ? " active" : ""));
      li.setAttribute("role", "option"); li.id = sid + "Hit" + i; li.setAttribute("aria-selected", String(i === active));
      li.append(el("span", "sname", n.name), el("span", "stype", n.type), el("span", "sid", id));
      li.addEventListener("mousedown", (e) => { e.preventDefault(); choose(id); });
      list.append(li);
    });
    list.hidden = !hits.length;
    input.setAttribute("aria-expanded", String(!!hits.length));
    if (hits.length) input.setAttribute("aria-activedescendant", sid + "Hit" + active); else input.removeAttribute("aria-activedescendant");
  }
  input.addEventListener("input", () => { hits = findNodes(ix, input.value); active = 0; paint(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && hits.length) { e.preventDefault(); active = (active + 1) % hits.length; paint(); }
    else if (e.key === "ArrowUp" && hits.length) { e.preventDefault(); active = (active - 1 + hits.length) % hits.length; paint(); }
    else if (e.key === "Enter" && hits.length) { e.preventDefault(); choose(hits[active]); }
    else if (e.key === "Escape") { input.value = ""; close(); }
  });
  input.addEventListener("blur", () => setTimeout(close, 100));
  // "/" anywhere outside a field jumps to the search, a common board shortcut.
  if (!opts.onPick) document.addEventListener("keydown", (e) => {
    const t = /** @type {HTMLElement} */ (e.target);
    if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) && !t.isContentEditable) { e.preventDefault(); input.focus(); }
  });
  box.append(input, list);
  return box;
}
