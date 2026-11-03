// map.js — the D3 tidy-tree map: hierarchy, layout, zoom/pan, collapse state,
// cross-edge overlay and the panel-resize anchoring arithmetic. Moved out of
// board.html's inline script (TASK-1022, step 3 of STORY-atlas-board-modularize).
// Out of scope for this move: no layout maths changed. See AGENTS.md
// "board.html — the rules that matter" for why withMapAnchored()'s arithmetic
// is load-bearing and must not be touched.
//
// TWO DELIBERATE SEAMS, both forced by the module boundary (an inline
// <script type="module"> — board.html — has no URL, so nothing can import
// FROM it; only it can import from files like this one):
//
// SEAM 1 — the D3 bookkeeping (d3root/d3ById/d3selected/svgSel/zoomBehavior/
// doUpdate/gZoom/gLink/gNode/gXEdge/…) stays PRIVATE to this module, plain
// `var`, never exported. board.html used to reach into these directly; it
// can't any more the way state.js's `state` object can be reached (property
// assignment on an imported binding is fine — REASSIGNING the imported
// binding itself is not), and several of these get reassigned wholesale
// (`d3root = d3.hierarchy(...)`, `svgSel = d3.select(...)`, …), not just
// mutated in place. Rather than wrap them in a mutable object purely to dodge
// that rule, this module exports small FUNCTIONS instead — hasNode(),
// getSelectedId(), clearSelection(), currentTransform(), restoreTransform(),
// collapseUnboundBucket() — because nothing outside this file actually needs
// the raw hierarchy, only specific facts about it or actions on it.
//
// SEAM 2 — selecting a node also has to open the detail panel, sync the URL,
// and (for the age filter) briefly disable itself to reveal a hidden node.
// All three are board.html's job, and board.html cannot export renderPanel /
// updateUrl / revealFiltered for this module to import. `hooks` below is a
// plain object board.html fills in once at boot by property assignment —
// `hooks.renderPanel = renderPanel;` etc — the exact idiom state.js already
// uses for shared data, applied here to callbacks instead.
import { state, isAgedOut, BRIDGE_TYPES, statusBucket, STATUS_CHOICES } from "/assets/state.js";
import { svgEl, remember, recall, cleanName } from "/assets/util.js";
import { buildGlyph, tipText, ATT_LABEL, attentionOf } from "/assets/glyph.js";

export var hooks = {
  renderPanel: function () {},
  updateUrl: function () {},
  revealFiltered: function () { return false; }
};

// ================================================================== D3 tidy tree
// Horizontal collapsible tidy tree (modelled on index.html renderTree): the 6
// epic roots are wrapped in a synthetic "Backlog" hub so d3.hierarchy sees one
// tree; each node's mark is the reusable attention/completion glyph.
var NODE_SIZE = 24;               // glyph diameter used as each node mark

// TIDY-TREE SPACING SCALES WITH THE TEXT-SIZE CONTROL, and it has to.
// `dx` is the vertical gap between sibling rows, `dy` the width of a depth
// column. Both used to be fixed numbers while every font-size: in board.css is
// `calc(<N>px * var(--ui-scale))` — so pressing A+ grew the labels and left the
// grid they sit in exactly where it was, and the text overflowed into the
// neighbouring column. Measured on live at 2026-08-28 (work tree, 20 labels):
// the widest label is 283 units at scale 1.0 against a 284-unit column and 425
// units at scale 1.5 — 141 units deep into the child column's own labels.
//
// These are SVG user units, not CSS px, so they are untouched by the map's own
// zoom: scaling them here spreads the tree by the same factor the type grew by,
// which is what keeps the fit below stable across every stop of the control.
var DX_BASE = 34, DY_BASE = 300;
var dx = DX_BASE, dy = DY_BASE;
var diagonal = null, treeLayout = null;

// The one reader of --ui-scale in this module. glyph.js reads the same property
// for the same reason (an SVG font-size= attribute cannot see a CSS variable);
// this is the layout half of that problem.
function uiScale() {
  var v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--ui-scale"));
  return isFinite(v) && v > 0 ? v : 1;
}

// Where a label starts relative to its glyph, and how much clear space is kept
// between the end of one label and the next column's glyph. LABEL_GUTTER is the
// margin that makes "does not overlap" visible rather than merely true.
var LABEL_X = 16, LABEL_GUTTER = 18;
// A name is never truncated below this, even when the trailing readout alone
// would eat the column. Losing the name entirely is worse than a few units of
// overhang, and the readout is bounded in practice (measured: 76-132 units).
var MIN_NAME_WIDTH = 56;

function labelBudget() { return dy - LABEL_X - LABEL_GUTTER; }

function textLen(el) {
  try { return el.getComputedTextLength(); } catch (e) { return 0; }
}

// Applies the current --ui-scale to the layout grid and redraws in place.
// Deliberately does NOT re-fit: a re-fit would zoom back out by the same factor
// the type grew by, and the text-size control would appear to do nothing.
export function rescaleLayout() {
  if (!treeLayout || !d3root) return;
  dx = DX_BASE * uiScale();
  dy = DY_BASE * uiScale();
  treeLayout.nodeSize([dx, dy]);
  update(d3root);
}

// attachment icons — extensible: add a {type, icon, cls} row (+ optional CSS class).
// `intent` is FIRST and is not a bridge type: it is a count of rows on the
// node itself (ADR 0032), fed through `_attach.intent` by projectWorld. It
// inherits `◎` from the retired `goal` type, which is the honest glyph for
// it — that type existed to say "this node has a stated purpose", and this
// says the same thing without a node to navigate to.
//
// OWN intent only. Every node below an authored one inherits something, so a
// glyph that counted inherited statements would light up nearly the whole
// tree and stop distinguishing anything.
var ATTACH = [
  { type: "intent", icon: "◎", cls: "ic-intent" },
  { type: "skill", icon: "✦", cls: "ic-skill" },
  { type: "doc", icon: "▤", cls: "ic-doc" }
  // future: { type: "vision", icon: "✧", cls: "ic-vision" }
];

// Per-object work-load as the filter leaves it. The World tree draws no
// workitems, so its "· N tasks · X%" readout is the only place finished work
// shows up there — this is what the filter has to reach.
//
// The PERCENTAGE stays over ALL the work, not the visible subset: completion
// is a property of everything attached to the object, and recomputing it over
// "what's left after hiding the done ones" would pin every filtered object at
// 0% — a number that looks like failure and means nothing.
function visibleWorkLoad(wl) {
  var items = (wl && wl.workitems) || [];
  var total = (wl && wl.count) || 0;
  if (!state.FILTER.doneAfterDays || !items.length) {
    return { count: total, total: total, hidden: 0, completion: (wl && wl.completion) || 0 };
  }
  var shown = 0;
  items.forEach(function (w) { if (!(w.done && isAgedOut(w))) shown++; });
  return { count: shown, total: total, hidden: total - shown,
           completion: (wl && wl.completion) || 0 };
}

// D3 tidy-tree state (private — see SEAM 1 above)
var d3root = null;      // d3.hierarchy root for the CURRENT tree
var d3ById = {};        // node id -> d3 hierarchy node (current tree, incl. collapsed)
var d3selected = null;  // currently selected d3 node
var gZoom = null, gLink = null, gNode = null, svgSel = null, zoomBehavior = null, doUpdate = null;
var gXEdge = null;
// Non-`contains` world edges — works_on/serves/for/hosted_on/contributes_to.
// Half the world graph lives here and until now none of it reached the screen.
// Populated from outside via setWorldXEdges() — board.html's boot fetch is the
// only writer, once per successful world-graph load.
var WORLD_XEDGES = [];
var XEDGE_MODES = ["off", "selected", "all"];
// DEFAULT IS `selected`, not `off`. Drawing all 19 hosted_on edges at once is a
// near-complete bipartite fan onto one host — technically correct and unreadable.
// "What is THIS connected to?" is the question people actually ask, and with
// nothing selected it draws nothing, so the default costs no ink.
// Migrates the earlier boolean: "1" -> all, "0" -> off.
var xedgeMode = (function () {
  var v = recall("atlas.xedges");
  if (v === "1") return "all";
  if (v === "0") return "off";
  return XEDGE_MODES.indexOf(v) === -1 ? "selected" : v;
})();
// "" = every type. VALIDATED against the #xedgeType select's actual <option>
// values (owned by board.html, not duplicated here — see AGENTS.md "No module
// may encode the graph") rather than trusted blindly: a persisted value for an
// option that has since been removed from the markup must not survive, because
// `sel.value = xedgeType` (syncXEdgeControls, below) silently sets
// selectedIndex -1 for a value with no matching <option> — the control renders
// BLANK and drawXEdges' `e.edge_type !== xedgeType` matches nothing, so the
// whole Links overlay goes dark with nothing on screen to explain it. This is
// deliberately general: it revalidates against whatever options exist NOW, so
// the next time an option is added or removed this does not have to be
// remembered and re-applied.
var xedgeType = validateXEdgeType(recall("atlas.xedgeType"));

function validateXEdgeType(v) {
  v = v || "";
  if (v === "") return v;
  var sel = document.getElementById("xedgeType");
  var opts = sel && sel.options;
  if (!opts) return v; // select not in the DOM (e.g. a test harness) — nothing to validate against
  for (var i = 0; i < opts.length; i++) {
    if (opts[i].value === v) return v;
  }
  // stale value naming a type that no longer exists: fall back to "all kinds"
  // and correct the stored value too, so a reload doesn't keep re-discovering
  // the same staleness.
  remember("atlas.xedgeType", "");
  return "";
}
var lastVisibleById = {};

var canvasWrap = document.getElementById("canvasWrap");
var svgRoot = document.getElementById("mapsvg");
var zoomIndicator = document.getElementById("zoomIndicator");
var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function nodeClass(d) {
  return "node " + (d.data.type || "node") +
    (d._children && !d.children ? " collapsed" : "") +
    (d === d3selected ? " selected" : "");
}

// value -> label, built from STATUS_CHOICES (state.js) rather than a second
// hand-written table, so the wording can never drift from the panel's own
// status control.
var STATUS_LABEL = {};
STATUS_CHOICES.forEach(function (c) { STATUS_LABEL[c.value] = c.label; });

// A real node's accessible name. A sighted user reads a node's TYPE off its
// CSS class (nodeClass, above) and its STATUS off the glyph — the fill colour
// for attention (buildGlyph in glyph.js) where the node carries one, else the
// workflow status a workitem's status control writes (bucketed the same way
// the status FILTER buckets it, so "planned" reads as "pending" here too).
// Neither of those reaches a screen reader on their own, so both are spelled
// out ahead of the name. The name itself is never touched — `d.name` is
// pushed verbatim, because AGENTS.md's "node.name carries two registers"
// rule applies to every renderer of it, aria-label included.
//
// ATTENTION WINS, deliberately, and "carries one" means `d.attention` is
// PRESENT on the payload, not merely that attentionOf() defaulted it to
// "grey" — attentionOf() always returns something, so a truthiness check on
// its RESULT would make the status fallback below unreachable. Attention is
// what buildGlyph actually paints (the fill colour, on every node, workitem
// or object) and what tipText's hover already announces — status is not
// drawn on the map at all, only read out by the panel's own status control
// elsewhere in the DOM. A red/orange glyph is exactly the information sight
// supplies and a screen reader otherwise misses; shadowing it behind status
// text (the previous code's behaviour: `bucket ? status : attention`) meant a
// blocked-and-urgent workitem announced "blocked" and never "attention",
// because statusBucket() returns the raw value for anything outside its
// small equivalence map, so almost every real status is truthy and would win
// every time.
function ariaLabelFor(d) {
  var bits = [];
  if (d.type) bits.push(d.type);
  var bucket = statusBucket(d.status);
  bits.push(d.attention ? ATT_LABEL[attentionOf(d)] : (bucket ? (STATUS_LABEL[bucket] || bucket) : ATT_LABEL[attentionOf(d)]));
  bits.push(d.name);
  return bits.join(", ");
}

// rootNode is a plain {id,name,type,children:[...]} hierarchy root — the
// synthetic "Backlog" wrapper for the work tree, or the org node for the world tree.
export function buildTree(rootNode) {
  d3selected = null;

  svgRoot.innerHTML = "";
  svgSel = d3.select(svgRoot).attr("font-family", "inherit");
  gZoom = svgSel.append("g").attr("class", "gzoom");
  // Cross-edges sit BENEATH the containment links and the nodes, so the tree
  // stays the thing you read and the web is an overlay on it.
  gXEdge = gZoom.append("g").attr("class", "gxedge");
  gLink = gZoom.append("g").attr("class", "glink");
  gNode = gZoom.append("g").attr("class", "gnode");

  // Pick up whatever the text-size control is currently set to; a reload
  // restores --ui-scale from atlas.uiScale before this ever runs.
  dx = DX_BASE * uiScale();
  dy = DY_BASE * uiScale();
  treeLayout = d3.tree().nodeSize([dx, dy]);
  diagonal = d3.linkHorizontal().x(function (d) { return d.y; }).y(function (d) { return d.x; });

  d3root = d3.hierarchy(rootNode);
  d3root.x0 = 0; d3root.y0 = 0;
  d3ById = {};
  // capture the FULL tree BEFORE collapsing so navigation can reach hidden nodes
  d3root.descendants().forEach(function (d, i) {
    d.uid = i;
    d._children = d.children;
    d3ById[d.data.id] = d;
    // Start collapsed at depth 2. On the WORK tree that reads as intended: a
    // story (depth 2) hides its tasks by default.
    //
    // buildTree is ONE function shared by BOTH trees, so the WORLD tree gets
    // the identical cut — and there depth 2 is the layer words
    // (atlas-frontend, atlas-backend, …), which puts every capability node and
    // every leaf beneath them behind a click on first paint. Consequences:
    //   * "I can't see node X on the world view" almost always means "it is
    //     connected, and collapsed" — not that an edge is missing.
    //   * A `g.node` count is a count of what is EXPANDED, not a census of the
    //     tree. Deep-linking expands the target's ancestors, so the number
    //     legitimately rises with nothing having changed. Compare like with
    //     like before reporting a regression.
    // Changing this threshold changes both trees; there is no per-tree depth.
    if (d.depth >= 2) d.children = null;
  });

  zoomBehavior = d3.zoom().scaleExtent([0.05, 5]).clickDistance(20).on("zoom", function (e) {
    gZoom.attr("transform", e.transform);
    zoomIndicator.textContent = Math.round(e.transform.k * 100) + "%";
    canvasWrap.classList.toggle("far", e.transform.k < 0.35);
  });
  svgSel.call(zoomBehavior);
  // let the wheel zoom without also scrolling the page
  svgSel.on("wheel", function (e) { e.preventDefault(); }, { passive: false });

  doUpdate = update;
  update(d3root);
  fit(false);
}

// ---- cross-edges (TASK-987) -------------------------------------------------
// The world is a graph; the layout is a tree. Rather than swap in a force
// simulation — which would move nodes after paint and corrupt withMapAnchored()'s
// getBoundingClientRect() arithmetic — the tidy-tree positions are kept and the
// non-containment edges are drawn as arcs BETWEEN them.
//
// BOTH ENDPOINTS MUST BE VISIBLE. A collapsed subtree or a node filtered away by
// visibility must not leave a line pointing at where it would have been: that is
// an id leak (STORY-atlas-node-visibility LEAK TEST 4) and it is also just wrong.
function drawXEdges(visibleById) {
  if (!gXEdge) return;
  lastVisibleById = visibleById;
  var rows = [];
  var focusId = d3selected && d3selected.data ? d3selected.data.id : null;
  if (xedgeMode !== "off" && state.currentTree === "world") {
    WORLD_XEDGES.forEach(function (e) {
      if (xedgeType && e.edge_type !== xedgeType) return;
      // `selected` scope: only edges incident to the focused node. Both ends are
      // still required to be visible — the scope narrows what is asked for, it
      // never relaxes the rule that keeps a filtered-away node from leaving a stub.
      if (xedgeMode === "selected" && e.src !== focusId && e.dst !== focusId) return;
      var s = visibleById[e.src], t = visibleById[e.dst];
      if (s && t) rows.push({ s: s, t: t, type: e.edge_type, key: e.src + "|" + e.dst + "|" + e.edge_type });
    });
  }
  var sel = gXEdge.selectAll("path.xedge").data(rows, function (d) { return d.key; });
  sel.exit().remove();
  sel.enter().append("path")
    .attr("class", function (d) { return "xedge xe-" + d.type; })
    .merge(sel)
    .attr("d", function (d) {
      // Arc bowed perpendicular to the run, so an edge between two nodes in the
      // same column is still visible instead of collapsing onto a straight line.
      var x1 = d.s.y, y1 = d.s.x, x2 = d.t.y, y2 = d.t.x;
      var mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
      var ddx = x2 - x1, ddy = y2 - y1;
      var len = Math.sqrt(ddx * ddx + ddy * ddy) || 1;
      var bow = Math.min(120, len * 0.22);
      return "M" + x1 + "," + y1 + "Q" + (mx - (ddy / len) * bow) + "," +
             (my + (ddx / len) * bow) + " " + x2 + "," + y2;
    })
    .each(function (d) {
      var t = this.querySelector("title") || this.appendChild(svgEl("title", {}));
      t.textContent = d.s.data.name + " — " + d.type.replace(/_/g, " ") + " → " + d.t.data.name;
    });
}

// Redraw the overlay WITHOUT re-running the tree layout or its transitions —
// selecting a node must not make the whole map animate.
function refreshXEdges() { drawXEdges(lastVisibleById); }

function syncXEdgeControls() {
  var btn = document.getElementById("xedgeToggle");
  if (btn) {
    btn.textContent = "Links: " + xedgeMode;
    btn.setAttribute("aria-pressed", xedgeMode === "off" ? "false" : "true");
  }
  var sel = document.getElementById("xedgeType");
  if (sel) { sel.value = xedgeType; sel.disabled = (xedgeMode === "off"); }
  document.body.classList.toggle("xedges-on", xedgeMode !== "off");
}

function setXEdgeMode(mode) {
  xedgeMode = mode;
  remember("atlas.xedges", mode);
  syncXEdgeControls();
  refreshXEdges();
}

function setXEdgeType(t) {
  xedgeType = t || "";
  remember("atlas.xedgeType", xedgeType);
  syncXEdgeControls();
  refreshXEdges();
}

// Non-`contains` world edges, set once per successful world-graph load
// (board.html's boot fetch). See the WORLD_XEDGES declaration above.
export function setWorldXEdges(edges) { WORLD_XEDGES = edges || []; }

function update(source) {
  var duration = reduceMotion ? 0 : 280;
  var nodes = d3root.descendants().reverse();
  // Links OUT OF the world anchor are dropped. Drawing them would put four lines
  // converging on an invisible point above the roots — the ownership picture the
  // anchor exists to avoid, minus only the dot. The roots render unconnected,
  // which is what "nothing owns these" looks like.
  var links = d3root.links().filter(function (l) { return !(l.source.data && l.source.data._anchor); });
  treeLayout(d3root);

  // ---- nodes ----
  var node = gNode.selectAll("g.node").data(nodes, function (d) { return d.uid; });

  var nodeEnter = node.enter().append("g")
    .attr("transform", function () { return "translate(" + source.y0 + "," + source.x0 + ")"; })
    // The rendering anchor (d.data._anchor, handled in the .each() below) is
    // invisible and unselectable by design — it must also be OUT of the tab
    // order and OUT of the accessibility tree, or a keyboard/screen-reader
    // user lands on an element with nothing to announce and nothing to do.
    // tabindex=-1 removes it from sequential Tab navigation without making it
    // wholly unfocusable (some AT still needs that); aria-hidden removes it
    // from the accessibility tree entirely. `role`/`aria-label` are left
    // UNSET for it (a function returning null removes the attribute) rather
    // than given empty values, which would still announce as an unlabelled
    // button. Every real node is unchanged: tabindex=0, role=button, and an
    // aria-label enriched below (b) instead of the bare name it used to carry.
    .attr("tabindex", function (d) { return d.data._anchor ? -1 : 0; })
    .attr("aria-hidden", function (d) { return d.data._anchor ? "true" : null; })
    .attr("role", function (d) { return d.data._anchor ? null : "button"; })
    .attr("aria-label", function (d) { return d.data._anchor ? null : ariaLabelFor(d.data); })
    .on("click", function (e, d) { onNodeActivate(d); })
    .on("keydown", function (e, d) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onNodeActivate(d); }
    });

  nodeEnter.each(function (d) {
    var g = this;
    // The WORLD multi-root anchor renders NOTHING — no mark, no label, no tooltip,
    // no hit target. It exists only so d3.hierarchy has a single starting point
    // for a world that genuinely has several roots.
    //
    // This is not cosmetic. The whole point of multi-root is that a project is
    // owned by nobody; drawing a visible node above the roots re-asserts on screen
    // exactly the ownership the data stopped claiming. The WORK tab uses the same
    // anchor for the same reason now that its epics are bucketed by project — the
    // single "Backlog" hub above everything was the ownership claim in miniature,
    // and it survives only as the fallback for a missing world tree.
    if (d.data._anchor) { return; }
    var titleEl = svgEl("title", {});
    // A root-typed node is a BUCKET, and the buckets are no longer all one
    // thing: "Not bound to a project (3)" and the by-object view's unbound
    // bucket both land here and say what they are in their own name. Only the
    // nameless fallback hub needs the words supplied.
    titleEl.textContent = d.data.type === "root" ? (d.data.name || "Backlog — all epics") : tipText(d.data);
    g.appendChild(titleEl);
    if (d.data.type === "root") {
      g.appendChild(svgEl("circle", { class: "hub", r: 7 }));
    } else {
      var glyph = buildGlyph(d.data, NODE_SIZE);
      glyph.setAttribute("transform", "translate(" + (-NODE_SIZE / 2) + "," + (-NODE_SIZE / 2) + ")");
      g.appendChild(glyph);
      g.appendChild(svgEl("circle", {
        class: "select-ring", r: NODE_SIZE / 2 + 4, fill: "none",
        stroke: "var(--accent)", "stroke-width": 2.5, "stroke-dasharray": "3 3"
      }));
    }
    // label always to the RIGHT of the node — consistent regardless of children;
    // full name on hover. The name is truncated to FIT THE COLUMN, by measured
    // width, in fitLabel() below — never here and never by character count.
    var hasKids = d.children || d._children;
    var titleEl = svgEl("title"); titleEl.textContent = d.data.name; g.appendChild(titleEl);
    var label = svgEl("text", {
      class: "node-label", dy: "0.32em", x: LABEL_X, "text-anchor": "start"
    });
    // The UNTRUNCATED name. fitLabel() is the only thing that shortens it, and
    // it re-derives from this every time, so a scale change or a Detail-slider
    // move can widen a name back out instead of only ever cutting it further.
    label.dataset.name = cleanName(d.data.name, d.data.type);
    // name lives in its own tspan so setting it never wipes the trailing tspans
    var nameSpan = svgEl("tspan", { class: "lbl-name" });
    nameSpan.textContent = label.dataset.name;
    label.appendChild(nameSpan);
    if (state.currentTree === "world" && d.data.type !== "root") {
      // (a) attachment icons for what this object has (intent/skill/doc). The
      //     bridge-typed ones come from bridge CHILDREN; `intent` comes from the
      //     node's own intent count, because an intent is a row on the node and
      //     never a node of its own (ADR 0032 — that is the whole reason `goal`
      //     is retired). Same glyph the goal type used: `◎` was vacated by it.
      ATTACH.forEach(function (a) {
        if (d.data._attach && d.data._attach[a.type]) {
          var ic = svgEl("tspan", { class: "attach-icon " + a.cls, dx: 6 });
          ic.textContent = a.icon; label.appendChild(ic);
        }
      });
      // (b) work-load readout to the right of the name, or a type tag for bridge leaves
      var wl = d.data.work_load;
      if (wl && wl.count > 0) {
        // "· 12 tasks · 75%" normally; "· 3 of 12 tasks · 75%" while the age
        // filter is hiding finished ones, so the number that shrank is
        // explained on the spot rather than looking like work went missing.
        var vw = visibleWorkLoad(wl);
        var lr = svgEl("tspan", { class: "load-readout", dx: 8 });
        lr.textContent = "· " + (vw.hidden ? vw.count + " of " + vw.total : vw.total) +
          (vw.total === 1 ? " task · " : " tasks · ") + Math.round(vw.completion * 100) + "%";
        label.appendChild(lr);
      } else if (BRIDGE_TYPES[d.data.type]) {
        var tt = svgEl("tspan", { class: "type-tag", dx: 8 });
        tt.textContent = "· " + d.data.type;
        label.appendChild(tt);
      }
    } else if (!d.data._anchor) {
      var kidCount = (d.data.children || []).length;
      if (hasKids && kidCount) {
        var sub = svgEl("tspan", { class: "subcount", dx: 6 });
        sub.textContent = "(" + kidCount + ")";
        label.appendChild(sub);
      }
      // Who owns this — the work tree's counterpart to the world tree's
      // work-load readout above. `assignee` is resolved server-side to
      // {id,username,name} or omitted/null for an unassigned workitem
      // (atlas-work-store node, "Assignment": an ACCOUNT, never a string), so
      // the presence check IS the "unassigned renders nothing" rule — same
      // shape as the load-readout and the subcount right above, both of which
      // already render nothing rather than a zero.
      if (d.data.assignee) {
        var asg = svgEl("tspan", { class: "type-tag", dx: 6 });
        asg.textContent = "· @" + (d.data.assignee.name || d.data.assignee.username);
        label.appendChild(asg);
      }
    }
    g.appendChild(label);
    // invisible background rect = the single click target, spanning the glyph + its
    // label. Sized in sizeHit(), which fitLabel() re-runs after every truncation —
    // the label's width is not final at this point and a hit target measured here
    // would stay at the untruncated width for the life of the node.
    var hit = svgEl("rect", {
      class: "node-hit", x: -(NODE_SIZE / 2 + 6), y: -(NODE_SIZE / 2 + 5),
      height: NODE_SIZE + 10, rx: 4
    });
    g.insertBefore(hit, g.firstChild);
    sizeHit(label);
  });

  var nodeAll = node.merge(nodeEnter);
  nodeAll.attr("class", nodeClass);
  nodeAll.transition().duration(duration)
    .attr("transform", function (d) { return "translate(" + d.y + "," + d.x + ")"; });

  node.exit().transition().duration(duration)
    .attr("transform", function () { return "translate(" + source.y + "," + source.x + ")"; })
    .remove();

  // ---- links ----
  var link = gLink.selectAll("path.link").data(links, function (d) { return d.target.uid; });

  var linkEnter = link.enter().append("path").attr("class", "link")
    .attr("d", function () { var o = { x: source.x0, y: source.y0 }; return diagonal({ source: o, target: o }); });

  link.merge(linkEnter).transition().duration(duration).attr("d", diagonal);

  // Cross-edges are re-drawn from the SAME positions the tree just used, after
  // the layout has run — never from a separate simulation.
  var visibleById = {};
  nodes.forEach(function (n) { if (n.data && n.data.id) visibleById[n.data.id] = n; });
  drawXEdges(visibleById);

  link.exit().transition().duration(duration)
    .attr("d", function () { var o = { x: source.x, y: source.y }; return diagonal({ source: o, target: o }); })
    .remove();

  d3root.eachBefore(function (d) { d.x0 = d.x; d.y0 = d.y; });
  applyDetail();  // newly-entered labels get the correct truncation for the current level
}

// ---------------------------------------------------------------- Detail slider
// A manual info-density control, INDEPENDENT of zoom (zoom's far-fade stays as-is).
//   1 = compact: fill circle only (ring + badge hidden via CSS), label ≤15 chars
//   2 = standard: fill + completion ring (badge hidden via CSS), full label
//   3 = full (default): fill + ring + attention-count badge, full label
// Ring/badge visibility is pure CSS keyed off #canvasWrap[data-detail]; only the
// label text is swapped here (short vs full).
// TODO detail=4 (future): + vision/acceptance icons & headers on each node —
//   needs a `has_acceptance` flag on node data, not yet available. Don't fake it.
//   (The intent half of this landed with ADR 0032: `intent_count` on /api/graph.)
var detailSlider = document.getElementById("detailSlider");
function applyDetail() {
  var lvl = detailSlider ? detailSlider.value : (canvasWrap.dataset.detail || "3");
  canvasWrap.dataset.detail = lvl;
  fitLabels();
}

// ---------------------------------------------------------------- label fitting
// WHY THIS MEASURES INSTEAD OF COUNTING CHARACTERS.
// The old rule was `name.slice(0, 33) + "…"`, and it was wrong twice over:
//
//   * it counted the NAME only, while the label also carries trailing tspans —
//     the world tree's "· 12 tasks · 75%", the work tree's "(3)" and
//     "· @someone". Measured on live at scale 1.0 those trailing spans are
//     76–132 user units, up to HALF the label, and none of it was budgeted for.
//     That is why the world tree overflowed its column at the default text size
//     with nobody having touched the control.
//   * 34 characters is a width only at one font size. The control has five
//     stops; at 1.5 the same 34 characters are half as wide again.
//
// So: fit the NAME to whatever the column has left after the trailing readout,
// by measurement, at whatever scale is current. The readout is never truncated —
// it is short, fixed-shape and the reason the label is worth reading.
//
// Cost is one getComputedTextLength() per label plus two or three more for each
// label that is actually too long (the first guess is proportional, so it lands
// within a character or two). Text measurement forces layout; this runs once per
// update(), not per frame.
function clipName(s, n) {
  if (s.length <= n) return s;
  return s.slice(0, Math.max(1, n - 1)).replace(/\s+$/, "") + "…";
}

// The click target spans the glyph and whatever the label ENDED UP being.
function sizeHit(label) {
  var hit = label.parentNode && label.parentNode.querySelector("rect.node-hit");
  if (!hit) return;
  var w = textLen(label) || 180;
  var x = -(NODE_SIZE / 2 + 6);
  hit.setAttribute("width", Math.max(0, (LABEL_X + w + 6) - x));
}

function fitLabel(lab, compact) {
  var nameSpan = lab.querySelector(".lbl-name");
  if (!nameSpan) return;
  var raw = lab.dataset.name || "";
  // Detail level 1 is a deliberate density choice, not a fit — it caps the name
  // short and the width fit below then applies on top of that cap.
  var base = compact ? clipName(raw, 15) : raw;
  nameSpan.textContent = base;

  var budget = labelBudget();
  var total = textLen(lab);
  if (total > budget) {
    // What the trailing tspans take is not negotiable; the name gets the rest.
    var room = Math.max(MIN_NAME_WIDTH, budget - (total - textLen(nameSpan)));
    var n = base.length;
    for (var pass = 0; pass < 8; pass++) {
      var w = textLen(nameSpan);
      if (w <= room || n <= 2) break;
      // Proportional estimate, forced to make progress so this always terminates.
      var next = Math.floor(n * room / w);
      if (next >= n) next = n - 1;
      n = Math.max(2, next);
      nameSpan.textContent = clipName(base, n);
    }
  }
  sizeHit(lab);
}

function fitLabels() {
  var compact = (canvasWrap.dataset.detail || "3") === "1";
  var labels = svgRoot.querySelectorAll("text.node-label");
  for (var i = 0; i < labels.length; i++) fitLabel(labels[i], compact);
}
if (detailSlider) {
  var savedDetail = recall("atlas.detail");
  if (savedDetail === "1" || savedDetail === "2" || savedDetail === "3") {
    detailSlider.value = savedDetail;
    canvasWrap.dataset.detail = savedDetail;
  }
  detailSlider.addEventListener("input", function () {
    applyDetail();
    remember("atlas.detail", detailSlider.value);
  });
}

// click / Enter: open the detail panel AND toggle this node's children (index.html behaviour)
function onNodeActivate(d) {
  if (d.data.type !== "root") selectD3(d, false);
  if (d._children) { d.children = d.children ? null : d._children; update(d); }
}

function selectD3(d, pan) {
  d3selected = d;
  gNode.selectAll("g.node").attr("class", nodeClass);
  refreshXEdges();   // `selected` scope follows the selection
  hooks.renderPanel(d.data);
  var panel = document.getElementById("detailPanel"), panelScrim = document.getElementById("panelScrim");
  panel.classList.add("open"); panel.setAttribute("aria-hidden", "false");
  panelScrim.classList.add("open");
  withMapAnchored(function () { document.body.classList.add("panel-open"); });
  if (pan) centerOnNode(d);
  hooks.updateUrl();
}

// navigate to a node by id (from panel sub-node / link rows): expand its ancestors,
// select it, and pan the camera to it.
export function selectNode(id, pan) {
  var d = d3ById[id];
  // Asked for by name (search, deep link, a panel row) but hidden by the age
  // filter — reveal it rather than doing nothing. revealFiltered rebuilds, so
  // re-read the index afterwards.
  if (!d && hooks.revealFiltered(id)) d = d3ById[id];
  if (!d) return;
  d.ancestors().forEach(function (a) { if (a._children && !a.children) a.children = a._children; });
  if (doUpdate) doUpdate(d);
  selectD3(d, pan !== false);
}

// Whether `id` is present in the CURRENT tree (collapsed or not) — used by
// board.html's revealFiltered() and rebuildMap() instead of reaching into
// d3ById directly (see SEAM 1).
export function hasNode(id) { return !!d3ById[id]; }

// The currently selected node's id, or null — for board.html's updateUrl()
// (see SEAM 1; d3selected itself is private).
export function getSelectedId() {
  return (d3selected && d3selected.data.type !== "root") ? d3selected.data.id : null;
}

// Clear the selection and its highlight, without touching the panel or the
// URL — board.html's closePanel() does those itself, then calls this.
export function clearSelection() {
  d3selected = null;
  if (gNode) gNode.selectAll("g.node").attr("class", nodeClass);
}

export function currentTransform() {
  return svgSel ? d3.zoomTransform(svgRoot) : d3.zoomIdentity;
}

// Re-apply a previously captured transform (see currentTransform) — used by
// board.html's rebuildMap() to hold the camera still across a filter change,
// which resets the transform via buildTree()'s own fit().
export function restoreTransform(t) {
  if (t && zoomBehavior && svgSel) svgSel.call(zoomBehavior.transform, t);
}

function centerOnNode(d) {
  if (!zoomBehavior) return;
  var rect = svgRoot.getBoundingClientRect();
  var k = Math.max(currentTransform().k, 1.1);
  var t = d3.zoomIdentity.translate(rect.width / 2 - d.y * k, rect.height / 2 - d.x * k).scale(k);
  svgSel.transition().duration(reduceMotion ? 0 : 400).call(zoomBehavior.transform, t);
}

export function fit(animate) {
  if (!d3root || !zoomBehavior) return;
  var x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  d3root.each(function (d) {
    if (d.x < x0) x0 = d.x; if (d.x > x1) x1 = d.x;
    if (d.y < y0) y0 = d.y; if (d.y > y1) y1 = d.y;
  });
  var rect = svgRoot.getBoundingClientRect();
  // Room for the deepest label column. Both terms scale with the text-size
  // control -- dy already does, and the label allowance is a text width.
  var boxW = (y1 - y0) + dy + 260 * uiScale();
  var boxH = (x1 - x0) + 80;
  var k = Math.min(rect.width / boxW, rect.height / boxH, 1.4);
  if (!isFinite(k) || k <= 0) k = 0.6;
  var cx = (y0 + y1) / 2, cy = (x0 + x1) / 2;
  var t = d3.zoomIdentity.translate(rect.width / 2 - cx * k + 40, rect.height / 2 - cy * k).scale(k);
  var target = animate === false ? svgSel : svgSel.transition().duration(reduceMotion ? 0 : 400);
  target.call(zoomBehavior.transform, t);
}

export function zoomBy(factor) {
  if (zoomBehavior) svgSel.transition().duration(reduceMotion ? 0 : 200).call(zoomBehavior.scaleBy, factor);
}
document.getElementById("zoom-in").addEventListener("click", function () { zoomBy(1.35); });
document.getElementById("zoom-out").addEventListener("click", function () { zoomBy(1 / 1.35); });
document.getElementById("zoom-fit").addEventListener("click", function () { fit(true); });
document.getElementById("collapse-all").addEventListener("click", collapseAll);
export function collapseAll() {
  // collapse everything below the root so only the top buckets and their epics show
  d3root.descendants().forEach(function (d) {
    if (d.depth >= 1 && d._children) d.children = null;
  });
  d3root.children = d3root._children;
  update(d3root);
  fit(true);
}

// collapse everything except the path down to the selected node (keeps its branch open)
document.getElementById("collapse-others").addEventListener("click", collapseAllButSelected);
export function collapseAllButSelected() {
  if (!d3selected || d3selected.data.type === "root") { collapseAll(); return; }
  var keep = new Set();                                  // selected node + all its ancestors
  d3selected.ancestors().forEach(function (a) { keep.add(a); });
  d3root.descendants().forEach(function (d) {
    if (d._children) d.children = keep.has(d) ? d._children : null;
  });
  update(d3root);
  centerOnNode(d3selected);
}

// board.html's workGroupToggle handler switches the work tree between the
// backlog shape and the by-object shape. buildTree() already collapses below
// depth 2, but the unbound bucket IS a depth-1 node whose ~100+ children sit
// at depth 2 — so they all render, and fitting that many nodes lands the map
// at an unreadable zoom level. This collapses that one node; the object
// skeleton is what should be on screen. Exported because board.html owns
// `workGrouping` and decides WHEN to call it, but not the d3 internals this
// needs (see SEAM 1).
export function collapseUnboundBucket() {
  var ub = d3ById["__unbound"];
  if (ub && ub._children && ub.children) { ub.children = null; update(d3root); }
}

// ---------------------------------------------------------------- split resizer
function shiftMap(dx) {
  if (!zoomBehavior || !svgSel || !dx) return;
  var t = currentTransform();
  svgSel.call(zoomBehavior.transform, d3.zoomIdentity.translate(t.x + dx, t.y).scale(t.k));
}

// Hold the map still while the split changes.
//
// .wrap is centred with `margin: 0 auto`, so insetting the body for the panel
// moves the canvas's LEFT edge, not just its right one — and the map, drawn in
// canvas-relative coordinates, slides across the screen with it. That is what
// made the tree crawl left as the panel grew and cost you the node you were
// looking at. Compensating by exactly the left edge's movement keeps every node
// on the same screen pixel, whether the panel is opening, closing or being
// dragged.
export function withMapAnchored(change) {
  var before = canvasWrap.getBoundingClientRect().left;
  change();
  shiftMap(before - canvasWrap.getBoundingClientRect().left);
}

// ---------------------------------------------------------------- cross-edge controls
// Self-contained: these three controls (#xedgeToggle, #xedgeType) only ever
// call this module's own setXEdgeMode/setXEdgeType, so the wiring lives here
// rather than being exported for board.html to attach.
var xedgeToggle = document.getElementById("xedgeToggle");
if (xedgeToggle) {
  xedgeToggle.addEventListener("click", function () {
    setXEdgeMode(XEDGE_MODES[(XEDGE_MODES.indexOf(xedgeMode) + 1) % XEDGE_MODES.length]);
  });
}
var xedgeTypeSel = document.getElementById("xedgeType");
if (xedgeTypeSel) {
  xedgeTypeSel.addEventListener("change", function () { setXEdgeType(this.value); });
}
syncXEdgeControls();
