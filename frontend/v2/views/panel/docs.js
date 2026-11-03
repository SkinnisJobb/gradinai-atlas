// @ts-check
// views/panel/docs.js — the Docs tab. One row per document in the node's
// `docs[]`, each fetched from `/api/node/{id}/doc?path=` when it is opened and
// rendered as markdown, with diagrams. A document declared external is labelled
// as such: its content lives outside Atlas and is not served.

import { fetchJSON } from "../../core/api.js";
import { el } from "../../ui/dom.js";
import { md, runDiagrams } from "../../ui/md.js";

/** @param {HTMLElement} body @param {{id: string, node: any, openDoc: string|null}} ctx */
export function renderDocs(body, ctx) {
  const docs = (ctx.node && ctx.node.docs) || [];
  if (!docs.length) { body.append(el("div", "empty", "No docs attached.")); return; }
  for (const d of docs) {
    const row = /** @type {HTMLDetailsElement} */ (el("details", "docrow"));
    const sm = el("summary");
    sm.append(el("span", "dkind", d.kind || "doc"), el("b", null, d.title || d.path), el("span", "pid", d.path));
    const holder = el("div", "docbody");
    row.append(sm, holder);
    let loaded = false;
    row.addEventListener("toggle", () => {
      if (!row.open) return;
      if (loaded) { runDiagrams(holder); return; }
      loaded = true;
      holder.append(el("div", "empty", "Loading…"));
      fetchJSON(`/api/node/${encodeURIComponent(ctx.id)}/doc?path=${encodeURIComponent(d.path)}`).then((r) => {
        holder.replaceChildren();
        if (r.external) holder.append(el("div", "note", "Declared external: the content lives outside Atlas and is not served."));
        else if (!r.content) holder.append(el("div", "empty", "This document is listed but has no stored content."));
        else holder.append(md(r.content));
        runDiagrams(holder);
      }).catch((err) => { holder.replaceChildren(el("div", "empty", "Could not load this document: " + (err && err.message))); });
    });
    body.append(row);
    if (docs.length === 1 || ctx.openDoc === d.path) row.open = true;
  }
  if (ctx.openDoc) {
    const target = [...body.querySelectorAll("details.docrow")].find((r) => r.querySelector(".pid")?.textContent === ctx.openDoc);
    if (target) target.scrollIntoView({ block: "nearest" });
    ctx.openDoc = null;
  }
}
