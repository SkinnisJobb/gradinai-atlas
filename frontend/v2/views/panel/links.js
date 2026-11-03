// @ts-check
// views/panel/links.js — the Links tab. Every connection, for world nodes too.
// The legacy panel said world objects carry no links, while their hosted_on and
// uses edges were in the graph all along.
// - Dependencies first, for work: what blocks this, and what it blocks.
// - Then every other edge, grouped by type and direction.
// A world node's work is on the work board, so it is not listed again here.

import { el, button, attColor } from "../../ui/dom.js";
import { cmpNodes } from "../../core/graph-model.js";
import { section } from "./overview.js";

const STATUS_COLOR = /** @type {Record<string,string>} */ ({ done: "var(--att-green)", in_progress: "var(--accent)", blocked: "var(--att-red)", proposed: "var(--att-purple)" });

/**
 * @param {HTMLElement} body @param {{ix: import("../../core/graph-model.js").Index, id: string, node: any, store: any}} ctx
 * @returns {number} how many links are listed, for the tab badge
 */
export function renderLinks(body, ctx) {
  const { ix, id, store } = ctx, isWork = !!(ctx.node && ctx.node.isWork), cmp = cmpNodes(ix);
  const adj = ix.adj.get(id) || [];
  const out = adj.filter((x) => x.t === "depends_on" && x.out).map((x) => x.o);
  const inn = adj.filter((x) => x.t === "depends_on" && !x.out).map((x) => x.o);
  let total = 0;
  /** @param {string} o */
  const go = (o) => { const n = ix.byId.get(o); if (n && n.isWork) store.set({ selected: o }); else store.set({ focus: o, selected: o, scope: o }); };

  if (out.length || inn.length) {
    const s = section(body, "Dependencies");
    for (const [title, list] of [["Blocked by (depends on)", out], ["Blocks (depended on by)", inn]]) {
      s.append(el("div", "sub", `${title} · ${list.length}`));
      if (!list.length) s.append(el("div", "empty", "Nothing."));
      for (const o of /** @type {string[]} */ (list).sort(cmp)) {
        const n = ix.byId.get(o), r = button("", () => go(o), "deprow"), dot = el("span", "dot");
        dot.style.background = n && n.isWork ? STATUS_COLOR[n.status] || "var(--att-grey)" : attColor(n ? n.attention : "grey");
        r.append(dot, el("span", null, n ? n.name : o));
        s.append(r);
      }
      total += list.length;
    }
  }

  /** @type {Map<string, Set<string>>} */
  const groups = new Map();
  for (const y of adj) {
    if (y.t === "depends_on") continue;
    if (!isWork && ix.byId.get(y.o)?.isWork) continue;
    const label = y.t === "requires" ? (y.out ? "requires →" : "← required by")
      : `${y.out ? "" : "← "}${y.t.replace(/_/g, " ")}${y.out ? " →" : ""}`;
    if (!groups.has(label)) groups.set(label, new Set());
    /** @type {Set<string>} */ (groups.get(label)).add(y.o);
  }
  if (groups.size) {
    const s = section(body, "Connections"), list = el("div", "conn");
    for (const g of [...groups.keys()].sort()) {
      const ids = [.../** @type {Set<string>} */ (groups.get(g))].sort(cmp);
      list.append(el("h4", null, `${g} · ${ids.length}`));
      for (const o of ids.slice(0, 30)) {
        const n = ix.byId.get(o), b = button(n ? n.name : o, () => go(o), "connrow");
        b.title = n ? `${n.name} (${n.type})` : o; list.append(b);
      }
      if (ids.length > 30) list.append(el("div", "pid", `+ ${ids.length - 30} more`));
      total += ids.length;
    }
    s.append(list);
  }
  if (!total) body.append(el("div", "empty", "No links."));
  return total;
}
