// @ts-check
// views/panel/history.js — the History tab: who changed this node, and on
// whose authority (`GET /api/events?node_id=`). Fetched on first open, never
// on selection, and paged honestly: the label is always the server's `total`,
// never a count of what happened to load. These are the legacy panel's rules
// (panel.js renderHistory), and its wording.

import { fetchJSON } from "../../core/api.js";
import { el, button } from "../../ui/dom.js";

const ACTION = /** @type {Record<string, string>} */ ({
  "node.create": "Created", "node.update": "Updated", "node.status": "Status changed", "node.assignee": "Assignee changed",
  "node.delete": "Deleted", "edge.create": "Link added", "edge.delete": "Link removed",
  "node.doc.write": "Document saved", "node.doc.delete": "Document removed",
  "node.intent.create": "Intent added", "node.intent.update": "Intent updated", "node.intent.delete": "Intent removed",
  "node.acceptance.attested": "Acceptance changed on a person's approval", "node.acceptance.propose": "Acceptance proposed",
  "node.acceptance.approve": "Acceptance proposal approved", "node.acceptance.reject": "Acceptance proposal rejected",
  "node.comment.create": "Comment added", "node.comment.update": "Comment edited", "node.comment.delete": "Comment removed",
  "node.config.set": "Setting changed",
});

const person = (/** @type {any} */ a) => (a ? a.name || a.username || a.id || null : null);
const fmt = (/** @type {any} */ v) => (v === null || v === undefined || v === "" ? "(none)" : typeof v === "object" ? person(v) || "(unknown account)" : String(v));

/** `detail` is not a diff: read the shapes that occur, as legacy does. @param {any} d @param {string} action */
export function detailText(d, action) {
  if (!d || typeof d !== "object") return null;
  const parts = [];
  // node.config.set: which setting, and where its value came from before and after. The
  // event never carries the values themselves (routers/config.py).
  if (d.setting) parts.push(String(d.setting));
  if ("from_source" in d || "to_source" in d) parts.push(`${fmt(d.from_source)} → ${fmt(d.to_source)}`);
  if ("from" in d || "to" in d) {
    parts.push(action === "node.assignee"
      ? `${d.from == null ? "(unassigned)" : fmt(d.from)} → ${d.to == null ? "(unassigned)" : fmt(d.to)}`
      : `${fmt(d.from)} → ${fmt(d.to)}`);
  }
  if (d.fields && d.fields.length) parts.push("fields: " + d.fields.join(", "));
  // The acceptance actions carry lists (`old`/`new`) and, when attested, who approved and their words.
  if (d.approver) parts.push(`approved by ${fmt(d.approver)}`);
  if (d.quote) parts.push(`“${d.quote}”`);
  if (Array.isArray(d.new)) parts.push(`${Array.isArray(d.old) ? d.old.length + " → " : ""}${d.new.length} criteria`);
  if (d.proposed_by) parts.push("proposed by " + d.proposed_by);
  if (d.edge_type) parts.push(`${d.edge_type}: ${fmt(d.src)} → ${fmt(d.dst)}`);
  if (d.type) parts.push("type: " + d.type);
  if (d.name) parts.push("name: " + d.name);
  if (d.via) parts.push("via " + d.via);
  if (d.cascade_of) parts.push("cascade of " + d.cascade_of);
  if (typeof d.edges_deleted === "number") parts.push(`${d.edges_deleted} ${d.edges_deleted === 1 ? "edge" : "edges"} removed`);
  return parts.length ? parts.join(" · ") : null;
}

/**
 * @param {HTMLElement} body @param {{id: string}} ctx @param {(total: number|null) => void} onTotal
 */
export function renderHistory(body, ctx, onTotal) {
  /** @type {any[]} */ let items = [];
  let total = 0, page = 0;
  body.append(el("div", "empty", "Loading history…"));

  function paint() {
    body.replaceChildren();
    if (!items.length) { body.append(el("div", "empty", "No recorded history for this node.")); return; }
    const list = el("div", "events");
    for (const ev of items) list.append(row(ev));
    body.append(list, el("div", "events-status", items.length < total ? `Showing ${items.length} of ${total} events` : `${total} ${total === 1 ? "event" : "events"}`));
    if (items.length < total) body.append(button("Load more", () => load(page + 1), "btn"));
  }
  /** @param {number} p */
  function load(p) {
    fetchJSON(`/api/events?node_id=${encodeURIComponent(ctx.id)}&page=${p}`).then((d) => {
      total = d.total || 0; page = d.page || p;
      items = p === 1 ? d.items || [] : items.concat(d.items || []);
      onTotal(total || null);
      paint();
    }).catch(() => { body.replaceChildren(el("div", "empty", "Could not load history.")); });
  }
  load(1);
}

/** @param {any} ev */
function row(ev) {
  const r = el("div", "event");
  const when = el("div", "ev-when", ev.at ? new Date(ev.at).toLocaleString() : "unknown time");
  if (ev.at) when.title = ev.at;
  const actor = ev.actor || {}, agent = actor.kind === "token";
  const who = el("div", "ev-who");
  who.append(el("span", "ev-actor" + (agent ? " agent" : ""), (person(actor.account) || actor.subject || (actor.kind === "system" ? "system" : "unknown caller")) + (agent ? " (agent)" : "")));
  if (agent) {
    const resp = person(ev.responsible);
    who.append(el("span", resp ? "ev-behalf" : "ev-nobody", resp ? "on behalf of " + resp : "no responsible person recorded"));
  }
  r.append(when, el("div", "ev-action", ACTION[ev.action] || String(ev.action || "change")), who);
  const d = detailText(ev.detail, ev.action);
  if (d) r.append(el("div", "ev-detail", d));
  return r;
}
