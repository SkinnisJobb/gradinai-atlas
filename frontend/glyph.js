// glyph.js — the node glyph (fill = attention, ring = completion, badge =
// attention_count) and its supporting label lookups. Moved out of board.html's
// inline script verbatim (TASK-1022, step 3 of STORY-atlas-board-modularize).
//
// Pure rendering: every function here takes a plain node-data object and
// returns/mutates only what it was given (an SVG element it built, or a
// string). Nothing here reads board.html's fetch state, D3 hierarchy or
// selection — that is exactly why it was the easy half of this split.
//
// ATT_LABEL is exported alongside attentionOf/tipText/buildGlyph rather than
// left private: tipText() needs it internally, and board.html's detail-panel
// chips (renderPanel) need the same label strings for the same attention
// values — importing one shared table beats declaring the five-entry map
// twice and letting the two drift.

import { svgEl } from "/assets/util.js";

var ATT_LABEL = { grey: "idle", green: "healthy", orange: "warning", red: "attention", purple: "decision" };

// The badge number is drawn with an SVG font-size ATTRIBUTE (below), not a CSS
// font-size:, so it can't pick up var(--ui-scale) on its own — this reads the
// live custom property so newly-built glyphs start at the right size.
function currentUiScale() {
  var v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--ui-scale"));
  return isFinite(v) && v > 0 ? v : 1;
}

export function attentionOf(n) { return (n && n.attention) || "grey"; }

// ---------------------------------------------------------------- glyph (fill=attention, ring=completion, badge=attention_count)
export function buildGlyph(node, size) {
  var cc = node.completion_counts || { done: 0, in_progress: 0, todo: 0 };
  var total = (cc.done || 0) + (cc.in_progress || 0) + (cc.todo || 0);
  var doneF = total ? cc.done / total : (node.completion || 0);
  var inpF = total ? cc.in_progress / total : 0;
  var r = size / 2, sw = Math.max(2, size * 0.11), ringR = r - sw / 2 - 1;
  var fillR = ringR - sw * 0.85, C = 2 * Math.PI * ringR;
  var att = "var(--att-" + attentionOf(node) + ")";
  var g = svgEl("g", { class: "glyph" });
  // ring = completion track + progress arcs; class g-ring lets the Detail slider hide it (level 1)
  g.appendChild(svgEl("circle", { class: "g-ring", cx: r, cy: r, r: ringR, fill: "none", stroke: "var(--ring-track)", "stroke-width": sw }));
  var rot = svgEl("g", { class: "g-ring", transform: "rotate(-90 " + r + " " + r + ")" });
  if (inpF > 0) rot.appendChild(svgEl("circle", {
    cx: r, cy: r, r: ringR, fill: "none", stroke: "var(--accent)", "stroke-width": sw, "stroke-opacity": ".4",
    "stroke-dasharray": (inpF * C) + " " + C, "stroke-dashoffset": (-doneF * C), "stroke-linecap": "butt"
  }));
  if (doneF > 0) rot.appendChild(svgEl("circle", {
    cx: r, cy: r, r: ringR, fill: "none", stroke: "var(--accent)", "stroke-width": sw,
    "stroke-dasharray": (doneF * C) + " " + C, "stroke-linecap": "butt"
  }));
  g.appendChild(rot);
  g.appendChild(svgEl("circle", { cx: r, cy: r, r: fillR, fill: att }));
  if (node.attention_count > 0 && size >= 24) {
    var bR = Math.max(8, size * 0.22), bx = size - bR * 0.72, by = bR * 0.72;
    // badge = attention_count; class g-badge lets the Detail slider hide it (levels 1 and 2)
    g.appendChild(svgEl("circle", {
      class: "g-badge", cx: bx, cy: by, r: bR, fill: "var(--att-red)", stroke: "var(--panel)",
      "stroke-width": Math.max(1.5, size * 0.03)
    }));
    // data-basefs = the unscaled size; the text-size control rescales every
    // already-drawn badge in place (see applyUiScale) since this isn't CSS.
    var badgeBaseFs = bR * 1.05;
    var t = svgEl("text", {
      class: "g-badge", x: bx, y: by + bR * 0.02, "text-anchor": "middle", "dominant-baseline": "central",
      fill: "#fff", "font-size": badgeBaseFs * currentUiScale(), "font-family": "var(--font-mono)", "font-weight": "700",
      "data-basefs": badgeBaseFs
    });
    t.textContent = node.attention_count;
    g.appendChild(t);
  }
  return g;
}

export function standaloneGlyph(node, size) {
  var svg = svgEl("svg", { width: size, height: size, viewBox: "0 0 " + size + " " + size });
  svg.style.display = "block"; svg.style.overflow = "visible"; svg.style.flex = "none";
  svg.appendChild(buildGlyph(node, size));
  return svg;
}

export function tipText(node) {
  var cc = node.completion_counts || {};
  var total = (cc.done || 0) + (cc.in_progress || 0) + (cc.todo || 0);
  var s = node.id + "  —  " + node.name + "\n" + ATT_LABEL[attentionOf(node)];
  if (total) s += "  ·  " + cc.done + "/" + total + " done";
  if (node.urgency && node.urgency !== "none") s += "  ·  urgency: " + node.urgency;
  if (node.attention_count) s += "  ·  " + node.attention_count + " lit below";
  return s;
}

export { ATT_LABEL };
