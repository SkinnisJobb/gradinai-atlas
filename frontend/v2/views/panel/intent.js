// @ts-check
// views/panel/intent.js — the Intent tab. A node's own intents come first, then
// what it inherits, grouped by the node each statement comes from.
// Inheritance is the API's answer (`/api/node/{id}?intents=all`), never
// recomputed here (ADR 0032).

import { el } from "../../ui/dom.js";
import { md } from "../../ui/md.js";
import { section } from "./overview.js";

/**
 * @param {HTMLElement} body @param {{full: any}} ctx
 * @returns {number|null} the count, for the tab badge
 */
export function renderIntent(body, ctx) {
  const own = (ctx.full.intents && ctx.full.intents.own) || [];
  const inh = (ctx.full.intents && ctx.full.intents.inherited) || [];
  if (!own.length && !inh.length) {
    body.append(el("div", "empty", "No intent is stated here, or on anything containing it."));
    return null;
  }
  if (own.length) {
    const s = section(body, "Stated on this node");
    for (const it of own) s.append(card(it));
  }
  /** @type {Map<string, any[]>} */
  const groups = new Map();
  for (const it of inh) {
    const k = it.owner_name || it.node_id || "an ancestor";
    if (!groups.has(k)) groups.set(k, []);
    /** @type {any[]} */ (groups.get(k)).push(it);
  }
  for (const [from, list] of groups) {
    const s = section(body, "Inherited from " + from);
    for (const it of list) s.append(card(it));
  }
  return own.length + inh.length;
}

/** @param {any} it */
function card(it) {
  const c = el("div", "intent k-" + String(it.kind || "x").replace(/[^a-z-]/g, ""));
  const top = el("div", "itop");
  top.append(el("span", "ikind", it.kind || "intent"), el("b", null, it.title || it.slug || ""));
  c.append(top, md(it.statement));
  if (it.source) c.append(el("div", "isrc", it.source));
  return c;
}
