// @ts-check
// ui/dom.js — the small pieces every pane draws with: an element helper, the
// type pictograms, and the status / attention / assignee marks. They are the
// same in the graph, on the work board and in the panel, so they are written
// once, here.

const SVG = "http://www.w3.org/2000/svg";

/**
 * @param {string} tag @param {string|null} [cls] @param {string|number|null} [text]
 * @returns {HTMLElement}
 */
export function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = String(text);
  return e;
}
/** @param {string} tag @param {Record<string, string|number>} [attrs] */
export function svgEl(tag, attrs = {}) {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  return e;
}
/** A button that does `fn` on click. @param {string} label @param {() => void} fn @param {string} [cls] */
export function button(label, fn, cls = "btn") {
  const b = /** @type {HTMLButtonElement} */ (el("button", cls, label));
  b.type = "button";
  b.addEventListener("click", fn);
  return b;
}

/** Type pictograms in a 24-unit box centred on 12,12. Presentation config: a type vocabulary, never a node id. */
export const ICON = Object.freeze({
  project: "M3 7h6l2 2h10v10H3z",
  component: "M12 3l8 4.5v9L12 21l-8-4.5v-9z M4 7.5l8 4.5 8-4.5 M12 12v9",
  capability: "M4 4h7v7H4z M13 4h7v7h-7z M4 13h7v7H4z M13 13h7v7h-7z",
  service: "M4 4h16v6H4z M4 14h16v6H4z M7.5 7h.01 M7.5 17h.01",
  db: "M5 6c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3z M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6 M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3",
  host: "M7 7h10v10H7z M10 3v4 M14 3v4 M10 17v4 M14 17v4 M3 10h4 M3 14h4 M17 10h4 M17 14h4",
  system: "M12 3l9 5-9 5-9-5z M3 13l9 5 9-5",
  org: "M5 21V4h10v17 M15 9h4v12 M8 8h1 M11 8h1 M8 12h1 M11 12h1 M8 16h1 M11 16h1 M3 21h18",
  agent: "M6 9h12v10H6z M12 5v4 M9.5 13h.01 M14.5 13h.01 M10 16h4",
  skill: "M12 3v6 M12 15v6 M3 12h6 M15 12h6 M6 6l3 3 M15 15l3 3 M18 6l-3 3 M9 15l-3 3",
  doc: "M6 3h8l4 4v14H6z M14 3v4h4 M9 12h6 M9 16h6",
  epic: "M6 21V4 M6 4h12l-3 4 3 4H6",
  story: "M7 3h10v18l-5-4-5 4z",
  task: "M4 4h16v16H4z M8 12l3 3 5-6",
  _: "M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0",
});
export const PIP_INTENT = "M12 12m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0 M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0-6 0";
export const PIP_DOC = "M7 3h7l4 4v14H7z M14 3v4h4 M10 12h5 M10 16h5";

/** @param {string} type */
export function iconPath(type) { return /** @type {Record<string,string>} */ (ICON)[type] || ICON._; }
/** A small inline icon, styled by CSS. @param {string} type @param {string} [cls] */
export function iconSvg(type, cls = "ico") {
  const s = svgEl("svg", { viewBox: "0 0 24 24", "aria-hidden": "true", class: cls });
  s.appendChild(svgEl("path", { d: iconPath(type) }));
  return s;
}

/** Attention colour as a CSS value. @param {string} a */
export const attColor = (a) => `var(--att-${["green", "orange", "red", "purple"].includes(a) ? a : "grey"})`;

/** @param {string} status */
export function statusPill(status) {
  const s = status || "pending";
  return el("span", "pill st-" + s.replace(/[^a-z_]/g, ""), s.replace(/_/g, " "));
}
/** A workitem's priority (STORY-oss-14), or null when it has none. @param {string|null} priority */
export function priorityTag(priority) {
  if (!priority) return null;
  const t = el("span", "prio p-" + priority.replace(/[^a-z]/g, ""), priority);
  t.title = "Priority: " + priority;
  return t;
}
/** A workitem's labels as small chips, or null when it has none. @param {string[]} labels */
export function labelChips(labels) {
  if (!labels || !labels.length) return null;
  const row = el("span", "wlabels");
  for (const l of labels) row.append(el("span", "wlabel", l));
  return row;
}
/** A coloured chip: attention, state. @param {string} label @param {string} att */
export function attChip(label, att) {
  const c = el("span", "chip att", label);
  c.style.background = attColor(att);
  return c;
}
/**
 * The round mark for who has a piece of work. Yours is filled with the accent.
 * @param {{username?: string, name?: string}|null} who @param {string|null} me
 */
export function avatar(who, me) {
  const mine = !!who && !!me && who.username === me;
  const a = el("span", "ava" + (mine ? " me" : "") + (who ? "" : " none"), who ? (who.name || who.username || "?").slice(0, 1) : "–");
  a.title = who ? `Assigned to ${who.name || who.username}${mine ? " (you)" : ""}` : "Unassigned";
  return a;
}
