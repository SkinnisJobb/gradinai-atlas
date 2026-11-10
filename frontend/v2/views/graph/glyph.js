// @ts-check
// views/graph/glyph.js — the rich node mark (TASK-1291), replacing the legacy
// tree's attachment icons.
// - The disc's fill is attention.
// - A pictogram inside it shows the type.
// - A ring shows how much of the node's rolled-up work is done.
// - A red badge counts what needs attention.
// - Pips mark intents (purple), docs and skills.
// What shows at each size is CSS, keyed on the lod1..3 class labels.js decides
// (styles/graph.css), so zooming changes classes, never the DOM.

import { svgEl, iconPath, PIP_INTENT, PIP_DOC, attColor } from "../../ui/dom.js";

/**
 * @param {SVGGElement} g  emptied and refilled
 * @param {import("../../core/graph-model.js").GNode} n
 * @param {number} r  the disc's radius
 * @param {{completion: number, hasWork: boolean, skills: number, hub: boolean}} extra
 */
export function drawGlyph(g, n, r, extra) {
  for (const c of [...g.childNodes]) if (c.nodeName !== "title") g.removeChild(c);
  if (extra.hub) g.appendChild(svgEl("circle", { class: "hubmark", r: r + 9 }));

  const rw = Math.max(2.5, r * 0.22);
  if (extra.hasWork) {
    g.appendChild(svgEl("circle", { class: "track", r: r + rw / 2 + 1, "stroke-width": rw }));
    const f = Math.max(0, Math.min(1, extra.completion));
    if (f > 0) {
      // An arc from 12 o'clock clockwise; a full ring is a circle, since an arc's two ends cannot meet.
      const R = r + 1 + rw / 2;
      if (f >= 0.9999) g.appendChild(svgEl("circle", { class: "arc", r: R, "stroke-width": rw }));
      else {
        const a = f * 2 * Math.PI, x = R * Math.sin(a), y = -R * Math.cos(a);
        g.appendChild(svgEl("path", { class: "arc", d: `M0,${-R}A${R},${R} 0 ${a > Math.PI ? 1 : 0} 1 ${x},${y}`, "stroke-width": rw }));
      }
    }
  }
  const fill = attColor(n.attention);
  if (n.isWork) {
    g.appendChild(svgEl("rect", { class: "disc", x: -r * 0.86, y: -r * 0.86, width: r * 1.72, height: r * 1.72, rx: r * 0.3, fill }));
  } else {
    g.appendChild(svgEl("circle", { class: "disc", r, fill }));
  }
  const s = (r * 1.15) / 24;
  g.appendChild(svgEl("path", { class: "ico", d: iconPath(n.type), transform: `scale(${s}) translate(-12,-12)`, "stroke-width": 2.1 }));

  if (n.attentionCount > 0) {
    const br = Math.max(5.5, r * 0.45), b = svgEl("g", { class: "badge", transform: `translate(${-r * 0.78},${-r * 0.78})` });
    b.appendChild(svgEl("circle", { r: br }));
    const t = svgEl("text", { dy: "0.35em", "font-size": br * 1.15 });
    t.textContent = n.attentionCount > 99 ? "99+" : String(n.attentionCount);
    b.appendChild(t);
    g.appendChild(b);
  }
  const pr = Math.max(4.5, r * 0.36);
  /** @param {string} cls @param {string} d @param {number} x @param {number} y */
  const pip = (cls, d, x, y) => {
    const p = svgEl("g", { class: "pip " + cls, transform: `translate(${x},${y})` });
    p.appendChild(svgEl("circle", { r: pr }));
    p.appendChild(svgEl("path", { d, transform: `scale(${(pr * 1.4) / 24}) translate(-12,-12)` }));
    g.appendChild(p);
  };
  if (n.intentCount > 0) pip("intent", PIP_INTENT, -r * 0.92, r * 0.78);
  if (n.docs.length > 0) pip("doc", PIP_DOC, r * 0.92, r * 0.78);
  if (extra.skills > 0) pip("skill", iconPath("skill"), 0, r + pr * 0.6);
  g.appendChild(svgEl("circle", { class: "sel", r: r + rw + 5 }));
}
