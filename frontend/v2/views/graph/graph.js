// @ts-check
// views/graph/graph.js — the graph pane (EPIC-atlas-nodeview).
//
// It reads and writes only the store:
// - It READS focus, steps, links, rel, work, selected, hover and stars.
// - It WRITES focus (double-click, Enter, a crumb), selected and scope (a
//   click) and hover (the pointer).
// The work board and the panel react to those writes; this file never calls
// them. Layout and label placement are pure modules (layout.js, labels.js) with
// tests. This file is the DOM and d3 wiring around them.
//
// d3 is the vendored global (`/assets/d3.min.js`).

import { focusSearch, rootsSearch, ANCHOR, collapseSeen, pruneTypes, isHub, workCounts, containsParents, allContainers } from "../../core/graph-model.js";
import { uniqueShorts } from "../../core/shortlabel.js";
import { radialTree, radiusOf, pathLayout, linkPath, relPath, fitTransform, MAX_STEPS } from "./layout.js";
import { fetchJSON } from "../../core/api.js";
import { placeLabels, lodOf } from "./labels.js";
import { drawGlyph } from "./glyph.js";
import { el, svgEl, button } from "../../ui/dom.js";
import { uiScale } from "../../ui/viewprefs.js";
import { prefs } from "../../core/prefs.js";
import { snapshotView, viewMatches, viewPatch, saveView, deleteView, readViews, nextViewName } from "../../core/views.js";

// How far a press on a node may move and still be a click: the legacy map's
// tolerance. Only node presses have a click handler, so nothing else changes.
const CLICK_SLOP_PX = 20;

const RELATION_CLASSES = new Set(["contains", "works_on", "depends_on", "hosted_on", "requires"]);
const relClass = (/** @type {string} */ t) => (RELATION_CLASSES.has(t) ? t : "other");

/**
 * @param {HTMLElement} pane
 * @param {import("../../core/store.js").Store} store
 * @param {import("../../core/graph-model.js").Index} ix
 * @param {{crumbs: HTMLElement, tools: HTMLElement}} chrome
 */
export function mountGraph(pane, store, ix, chrome) {
  const d3 = /** @type {any} */ (window).d3;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const DUR = reduceMotion ? 0 : 450;
  // The graph is fixed for the life of the page, so work counts are computed once per node.
  const wcMemo = new Map();
  const wc = (/** @type {string} */ id) => { if (!wcMemo.has(id)) wcMemo.set(id, workCounts(ix, id)); return wcMemo.get(id); };

  pane.replaceChildren();
  const svgNode = /** @type {SVGSVGElement} */ (svgEl("svg", { class: "graph", role: "img", "aria-label": "Graph of Atlas nodes" }));
  pane.append(svgNode, legend(), el("div", "graph-status", ""));
  const statusEl = /** @type {HTMLElement} */ (pane.querySelector(".graph-status"));
  const svg = d3.select(svgNode);
  const gZoom = svg.append("g"), gGuide = gZoom.append("g"), gRel = gZoom.append("g"), gLink = gZoom.append("g"), gHop = gZoom.append("g").attr("class", "hop-labels"), gNode = gZoom.append("g");
  // Path mode (TASK-1298): `store.path = {from, to}` draws the routes between
  // two nodes instead of the focus's rings, until it is closed.
  const banner = el("div", "path-banner"); banner.hidden = true;
  pane.append(banner);
  /** @type {any} the /api/graph/path answer for store.path, once it arrives */
  let pathRes = null;
  const gLabel = svg.append("g").attr("class", "labels");
  const measureCtx = /** @type {CanvasRenderingContext2D} */ (document.createElement("canvas").getContext("2d"));
  const fontFamily = getComputedStyle(document.body).fontFamily;

  /** @type {{all: import("./layout.js").Placed[], radii: number[]}|null} */
  let cached = null; let cachedKey = "";
  // The search behind the layout, kept apart so a slider move only lays out again:
  // `pruned` is the search after types and collapse, `deepest` how many steps down it goes.
  /** @type {Map<string, any>} */ let pruned = new Map(); let searchKey = "", deepest = 0;
  /** @type {string|null} */ let west = null;
  /** How far down the search looks. The slider ends where the graph does; this only bounds the walk. */
  const SEARCH_DEPTH = 64;
  /** @type {Map<string, number>} node types reachable from the focus, before the type filter, with counts */
  let typesHere = new Map();
  /** @type {any} */ let current = null;
  /** @type {string[]} */ let crumbs = [];
  let labelTimer = 0;
  /** @type {Map<string, {x:number,y:number}>} */ let lastPos = new Map();

  // clickDistance: d3 swallows the click after any pointer movement by default,
  // so a hand that moved a pixel or two between press and release opened nothing.
  const zoom = d3.zoom().scaleExtent([0.02, 8]).clickDistance(CLICK_SLOP_PX)
    .on("zoom", (/** @type {any} */ e) => { gZoom.attr("transform", e.transform); positionLabels(e.transform); })
    .on("end", () => { clearTimeout(labelTimer); labelTimer = window.setTimeout(labelPass, 60); });
  svg.call(zoom).on("dblclick.zoom", null);

  // ---------------------------------------------------------------- controls
  const steps = /** @type {HTMLInputElement} */ (el("input"));
  Object.assign(steps, { type: "range", min: "1", max: String(MAX_STEPS), step: "1", id: "steps" });
  steps.setAttribute("aria-label", "Steps away from the focus");
  const stepsVal = el("span", "steps-val");
  steps.addEventListener("input", () => store.set({ steps: Number(steps.value) }));
  /** @param {string} label @param {Array<[string,string,string]>} opts @param {string} key */
  const seg = (label, opts, key) => {
    const g = el("div", "seg"); g.setAttribute("role", "group"); g.setAttribute("aria-label", label);
    for (const [v, text, title] of opts) {
      const b = button(text, () => store.set({ [key]: v }), "");
      b.title = title; b.dataset.v = v; b.dataset.key = key; g.append(b);
    }
    return g;
  };
  const work = /** @type {HTMLInputElement} */ (el("input")); work.type = "checkbox"; work.id = "openWork";
  work.addEventListener("change", () => store.set({ work: work.checked }));
  const workLabel = el("label", "chk"); workLabel.append(work, " Open work as nodes");
  const group = (/** @type {string} */ name, /** @type {Node[]} */ ...kids) => { const g = el("div", "group"); g.append(el("span", "eyebrow", name), ...kids); return g; };
  // The type filter: a menu of the types around the focus. Unticking one hides
  // those nodes, keeping any that lead to a node still shown (pruneTypes).
  const types = /** @type {HTMLDetailsElement} */ (el("details", "tfilter"));
  const typesSum = el("summary", null, "Types"), typesList = el("div", "tfilter-menu");
  types.append(typesSum, typesList);
  document.addEventListener("click", (e) => { if (types.open && !types.contains(/** @type {Node} */ (e.target))) types.open = false; });
  function syncTypes() {
    const s = store.get(), hidden = new Set(/** @type {string[]} */ (s.hideTypes || []));
    const off = [...hidden].filter((t) => typesHere.has(t)).length;
    typesSum.textContent = off ? `Types · ${off} hidden` : "Types";
    types.classList.toggle("on", off > 0);
    types.hidden = !typesHere.size;
    typesList.replaceChildren();
    for (const [t, n] of [...typesHere].sort((a, b) => a[0].localeCompare(b[0]))) {
      const cb = /** @type {HTMLInputElement} */ (el("input")); cb.type = "checkbox"; cb.checked = !hidden.has(t);
      cb.addEventListener("change", () => {
        const h = new Set(/** @type {string[]} */ (store.get().hideTypes || []));
        if (cb.checked) h.delete(t); else h.add(t);
        store.set({ hideTypes: [...h] });
      });
      const row = el("label", "chk"); row.append(cb, ` ${t.replace(/_/g, " ")} `, el("span", "tcount", String(n))); typesList.append(row);
    }
    if (off) typesList.append(button("Show all types", () => store.set({ hideTypes: [] }), "linkbtn"));
  }
  // Saved views (core/views.js): the focus, steps, types and collapsed nodes under a name.
  // A small see-through box in the graph's top right corner, which the radial
  // layout leaves empty, rather than one more control in the bar. It folds to
  // its title line; folded until opened, and remembered across reloads.
  const views = el("div", "views-box");
  views.setAttribute("role", "region"); views.setAttribute("aria-label", "Saved views");
  const viewsSum = el("span", "vb-title", "Views"), viewsList = el("div", "vb-body");
  const fold = button("", () => setFolded(!views.classList.contains("min")), "linkbtn vb-min");
  /** @param {boolean} min */
  const setFolded = (min) => {
    views.classList.toggle("min", min);
    fold.textContent = min ? "+" : "−";
    fold.title = min ? "Show saved views" : "Fold away";
    fold.setAttribute("aria-expanded", String(!min));
    prefs.remember("graph.viewsFolded", min);
  };
  const vhead = el("div", "vb-head"); vhead.append(viewsSum, fold);
  // Folded, the whole box is the way to open it, not only the small button.
  vhead.addEventListener("click", (e) => { if (e.target !== fold && views.classList.contains("min")) setFolded(false); });
  views.append(vhead, viewsList);
  setFolded(!!prefs.recall("graph.viewsFolded", true));
  pane.append(views);
  const viewName = /** @type {HTMLInputElement} */ (el("input", "vname"));
  Object.assign(viewName, { type: "text", maxLength: 60, placeholder: "Name this view" });
  viewName.setAttribute("aria-label", "Name for the saved view");
  const saveCurrent = () => {
    const s = store.get(), name = viewName.value.trim() || nextViewName(s.views);
    store.set({ views: saveView(s.views, snapshotView(s, name)) });
    viewName.value = "";
  };
  viewName.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); saveCurrent(); } });
  const saveBtn = button("Save view", saveCurrent, "btn");
  saveBtn.title = "Save the focus, steps, types and collapsed nodes as a view";
  function syncViews() {
    const s = store.get(), list = readViews(s.views), on = list.find((v) => viewMatches(v, s));
    viewsSum.textContent = "Views";
    viewsSum.title = on ? `Showing “${on.name}”` : "";
    views.classList.toggle("on", !!on);
    viewsList.replaceChildren();
    for (const v of list) {
      const row = el("div", "vrow");
      const go = button(v.name, () => { store.set(viewPatch(v, (id) => ix.byId.has(id))); }, "linkbtn vgo");
      go.title = `Show “${v.name}”`;
      if (on && on.name === v.name) go.setAttribute("aria-current", "true");
      const del = button("×", () => store.set({ views: deleteView(s.views, v.name) }), "linkbtn vdel");
      del.title = `Delete “${v.name}”`; del.setAttribute("aria-label", `Delete view ${v.name}`);
      row.append(go, del); viewsList.append(row);
    }
    if (!list.length) viewsList.append(el("div", "vnone", "No saved views yet."));
    const add = el("div", "vadd"); add.append(viewName, saveBtn); viewsList.append(add);
    const n = (/** @type {string[]} */ (s.collapsed || [])).length;
    if (n) viewsList.append(button(`Expand all (${n} collapsed)`, () => store.set({ collapsed: [] }), "btn vb-expand"));
  }
  /** Collapse or expand one node. @param {string} id */
  function toggleCollapse(id) {
    const c = new Set(/** @type {string[]} */ (store.get().collapsed || []));
    if (c.has(id)) c.delete(id); else c.add(id);
    store.set({ collapsed: [...c] });
  }
  chrome.tools.prepend(
    group("Steps", steps, stepsVal),
    group("Step along", seg("Which links count as a step", [["contains", "Structure", "Steps follow what contains what"], ["all", "All links", "Steps follow every link, stopping at hubs"]], "links")),
    group("Relations", seg("When to draw the other links", [["hover", "On hover", "Only for the node you hover or select"], ["all", "Always", "Every relation between drawn nodes"]], "rel")),
    workLabel,
    types,
    button("Fit", () => { fit(true); window.setTimeout(labelPass, DUR + 40); }),
  );
  function syncControls() {
    const s = store.get();
    // The slider runs from 1 to as deep as the graph goes below what is chosen (MAX_STEPS when that is not known).
    const top = current && current.maxSteps ? current.maxSteps : MAX_STEPS, shown = current && current.k ? current.k : Number(s.steps);
    steps.max = String(top); steps.value = String(shown); stepsVal.textContent = String(shown);
    steps.title = `1 to ${top} steps: as deep as it goes from here`;
    work.checked = !!s.work;
    for (const b of chrome.tools.querySelectorAll("[data-key]")) {
      const k = /** @type {HTMLElement} */ (b).dataset.key;
      b.setAttribute("aria-pressed", String(s[/** @type {string} */ (k)] === /** @type {HTMLElement} */ (b).dataset.v));
    }
  }

  // ---------------------------------------------------------------- layout
  function layout() {
    const s = store.get(), stars = /** @type {string[]} */ (s.stars || []);
    const want = /** @type {{from: string, to: string}|null} */ (s.path);
    if (want && pathRes && pathRes.from === want.from && pathRes.to === want.to) {
      const P = pathLayout(pathRes);
      return { nodes: P.all, radii: [], overview: false, hops: P.links, pos: new Map(P.all.map((p) => [p.id, p])) };
    }
    // The Steps slider counts DOWN from what is chosen: the focus, whose
    // containers all show however far up they go, or, with no focus, the roots
    // and starred nodes. Laid out for the rings that SHOW (owner decision 2026-11-09): rings
    // further out take no room, so moving the slider re-spaces the graph.
    const opts = { links: /** @type {"contains"|"all"} */ (s.links), work: !!s.work };
    const hidden = new Set(/** @type {string[]} */ (s.hideTypes || []));
    const isRoots = !s.focus || !ix.byId.has(/** @type {string} */ (s.focus));
    const seeds = isRoots ? [...ix.roots, ...stars.filter((id) => ix.byId.has(id) && !ix.roots.includes(id))] : [];
    const centre = isRoots ? ANCHOR : /** @type {string} */ (s.focus);
    const collapsed = new Set(/** @type {string[]} */ (s.collapsed || []));
    const skey = [isRoots ? "roots:" + seeds.join(",") : centre + "<" + crumbs.join(","), s.links, s.work, [...hidden].sort().join(","), [...collapsed].sort().join(",")].join("|");
    if (skey !== searchKey) {
      // The way up follows the trail of focuses that led here, so it is the limb you came down.
      const full = isRoots ? rootsSearch(ix, seeds, SEARCH_DEPTH, opts) : focusSearch(ix, centre, SEARCH_DEPTH, { ...opts, prefer: crumbs });
      typesHere = new Map();
      for (const [id, v] of full) if (id !== centre && v.depth > (isRoots ? 1 : 0)) { const t = /** @type {any} */ (ix.byId.get(id)).type; typesHere.set(t, (typesHere.get(t) || 0) + 1); }
      // The focus's parent sits straight left, so the way back up never swaps sides.
      west = isRoots ? null : [...full].find(([, v]) => v.up && v.depth === 1)?.[0] ?? null;
      // Collapsing lays the tree out again, so a collapsed branch gives its room to the rest.
      pruned = collapseSeen(pruneTypes(ix, full, hidden), collapsed);
      deepest = 0;
      for (const v of pruned.values()) if (!v.up) deepest = Math.max(deepest, v.depth - (isRoots ? 1 : 0));
      searchKey = skey;
    }
    // The chosen step count, held to what exists here; the store keeps the wish for a deeper node.
    const maxSteps = Math.max(1, deepest);
    const k = Math.max(1, Math.min(Number(s.steps) || 2, maxSteps));
    const key = skey + "|" + k;
    if (key !== cachedKey) {
      const lim = isRoots ? k + 1 : k;
      const shown = new Map([...pruned].filter(([, v]) => v.up || v.depth <= lim));
      cached = radialTree(ix, shown, centre, { d3 }, west); cachedKey = key;
    }
    const C = /** @type {NonNullable<typeof cached>} */ (cached);
    if (isRoots) {
      // The anchor is never drawn; the roots are the outermost things, so they get the focus's size.
      const nodes = C.all.filter((p) => p.id !== ANCHOR && p.depth <= k + 1).map((p) => ({ ...p, parent: p.parent === ANCHOR ? null : p.parent, rr: radiusOf(p.depth - 1) }));
      return { nodes, radii: C.radii.slice(0, k + 2), overview: true, seeds, pos: new Map(nodes.map((p) => [p.id, p])), opts, kids: kidsOf(nodes), collapsed, k, maxSteps };
    }
    const nodes = C.all.filter((p) => p.up || p.depth <= k);
    return { nodes, radii: C.radii.slice(0, k + 1), overview: false, pos: new Map(nodes.map((p) => [p.id, p])), opts, focus: centre, kids: kidsOf(nodes), collapsed, k, maxSteps };
  }
  /** The nodes with something drawn below them: those a "−" can collapse. @param {import("./layout.js").Placed[]} nodes */
  function kidsOf(nodes) {
    const k = new Set();
    for (const p of nodes) if (!p.up && p.parent) k.add(p.parent);
    return k;
  }

  /** Tree links are each node's link to the parent it was reached from; relations are every other edge between drawn nodes. */
  function edges(/** @type {any} */ L) {
    const links = [], rels = [], seen = new Set(), tree = new Set();
    for (const n of L.nodes) if (n.parent && L.pos.has(n.parent)) { links.push({ key: n.parent + ">" + n.id, s: n.parent, d: n.id }); tree.add(n.parent + ">" + n.id); tree.add(n.id + ">" + n.parent); }
    for (const n of L.nodes) for (const x of ix.adj.get(n.id) || []) {
      if (!x.out || !L.pos.has(x.o) || tree.has(n.id + ">" + x.o)) continue;
      const k = n.id + "|" + x.o + "|" + x.t;
      if (!seen.has(k)) { seen.add(k); rels.push({ key: k, s: n.id, d: x.o, t: x.t }); }
    }
    return { links, rels };
  }

  // ---------------------------------------------------------------- render
  function render() {
    const L = layout();
    current = L;
    Object.assign(current, L.hops ? { links: L.hops, rels: [] } : edges(L));
    // What lies above the focus, toward its root: drawn faded, so the way back up reads at a glance.
    current.upstream = L.focus ? allContainers(ix, L.focus) : new Set();
    const onWayUp = (/** @type {string} */ id) => id === L.focus || current.upstream.has(id);
    const line = (/** @type {any} */ l) => { const a = L.pos.get(l.s), b = L.pos.get(l.d); return L.hops ? `M${a.x},${a.y}L${b.x},${b.y}` : linkPath(a, b); };
    current.shorts = uniqueShorts(L.nodes.map((n) => ({ ...(/** @type {any} */ (ix.byId.get(n.id))), parent: n.parent })));
    gLabel.selectAll("g").remove();

    const guides = gGuide.selectAll("circle").data(L.radii.slice(1));
    guides.exit().remove();
    guides.enter().append("circle").attr("class", "ring-guide").merge(guides).attr("r", (/** @type {number} */ r) => r);

    const ls = gLink.selectAll("path").data(current.links, (/** @type {any} */ l) => l.key);
    ls.exit().remove();
    ls.enter().append("path").attr("class", "link").style("opacity", 0).attr("d", line)
      .merge(ls).classed("hop", !!L.hops).classed("upstream", (/** @type {any} */ l) => onWayUp(l.s) && onWayUp(l.d)).transition().duration(DUR).style("opacity", null).attr("d", line);

    // In path mode every hop says what kind of edge it is, and which way it points.
    const arrow = (/** @type {string} */ d) => (d === "out" ? " →" : d === "in" ? " ←" : "");
    const hl = gHop.selectAll("text").data(L.hops || [], (/** @type {any} */ l) => l.key);
    hl.exit().remove();
    hl.enter().append("text").merge(hl)
      .attr("x", (/** @type {any} */ l) => (L.pos.get(l.s).x + L.pos.get(l.d).x) / 2)
      .attr("y", (/** @type {any} */ l) => (L.pos.get(l.s).y + L.pos.get(l.d).y) / 2 - 8)
      .text((/** @type {any} */ l) => l.t.replace(/_/g, " ") + arrow(l.dir));

    const rs = gRel.selectAll("path").data(current.rels, (/** @type {any} */ e) => e.key);
    rs.exit().remove();
    const rsIn = rs.enter().append("path").attr("class", (/** @type {any} */ e) => "rel " + relClass(e.t));
    rsIn.append("title").text((/** @type {any} */ e) => `${ix.byId.get(e.s)?.name} — ${e.t.replace(/_/g, " ")} → ${ix.byId.get(e.d)?.name}`);
    rs.merge(rsIn).attr("d", (/** @type {any} */ e) => relPath(L.pos.get(e.s), L.pos.get(e.d), L.overview ? 0.6 : 0.45));

    const ns = gNode.selectAll("g.node").data(L.nodes, (/** @type {any} */ n) => n.id);
    ns.exit().remove();
    const nsIn = ns.enter().append("g").attr("tabindex", 0).attr("role", "button")
      .attr("transform", (/** @type {any} */ n) => { const p = lastPos.get(n.id) || (n.parent && lastPos.get(n.parent)) || n; return `translate(${p.x},${p.y})`; })
      .style("opacity", 0)
      .on("click", (/** @type {any} */ _e, /** @type {any} */ n) => activate(n.id))
      .on("dblclick", (/** @type {any} */ e, /** @type {any} */ n) => { e.preventDefault(); if (store.get().focus) focusOn(n.id); })
      .on("keydown", (/** @type {KeyboardEvent} */ e, /** @type {any} */ n) => {
        if (e.key === "Enter") { e.preventDefault(); store.get().focus ? focusOn(n.id) : activate(n.id); }
        else if (e.key === " ") { e.preventDefault(); select(n.id); }
        else if (e.key === "-" || e.key === "+") { e.preventDefault(); toggleCollapse(n.id); }
      })
      .on("mouseenter", (/** @type {any} */ _e, /** @type {any} */ n) => store.set({ hover: n.id, hoverFrom: "graph" }))
      .on("mouseleave", () => store.set({ hover: null, hoverFrom: null }));
    nsIn.append("title");
    const all = ns.merge(nsIn);
    const focus = store.get().focus;
    all.each(function (/** @type {any} */ n) {
      const node = /** @type {import("../../core/graph-model.js").GNode} */ (ix.byId.get(n.id));
      const c = wc(n.id);
      // @ts-ignore d3 binds `this` to the element
      drawGlyph(this, node, n.rr, {
        completion: node.isWork ? (node.status === "done" ? 1 : node.status === "in_progress" ? 0.5 : 0) : c.completion,
        hasWork: node.isWork || c.count > 0, skills: ix.skills.get(n.id) || 0,
        hub: !!focus && isHub(ix, n.id, /** @type {string} */ (focus), { links: /** @type {any} */ (store.get().links) }),
      });
      // "−" collapses what hangs below; "+" brings it back. Top right; the badge moves to top left for it.
      const shut = !!L.collapsed?.has(n.id);
      if (!L.hops && (shut || L.kids?.has(n.id))) {
        const tr = Math.max(5, n.rr * 0.4), t = svgEl("g", { class: "tog" + (shut ? " on" : ""), transform: `translate(${n.rr * 0.8},${-n.rr * 0.8})`, role: "button" });
        t.setAttribute("aria-label", shut ? "Expand" : "Collapse");
        const tt = svgEl("title"); tt.textContent = shut ? "Expand: show what is below" : "Collapse: hide what is below";
        const tx = svgEl("text", { dy: "0.35em", "font-size": tr * 1.7 }); tx.textContent = shut ? "+" : "−";
        t.append(tt, svgEl("circle", { r: tr }), tx);
        t.addEventListener("click", (e) => { e.stopPropagation(); toggleCollapse(n.id); });
        t.addEventListener("dblclick", (e) => { e.stopPropagation(); e.preventDefault(); });
        // @ts-ignore d3 binds `this` to the element
        this.appendChild(t);
      }
    });
    all.attr("class", (/** @type {any} */ n) => "node" + (n.id === focus ? " focus" : "") + (n.id === store.get().selected ? " selected" : ""))
      .attr("aria-label", (/** @type {any} */ n) => { const b = ix.byId.get(n.id); return `${b?.type}, ${b?.name}`; });
    all.select("title").text((/** @type {any} */ n) => tip(n.id));
    all.transition().duration(DUR).style("opacity", null).attr("transform", (/** @type {any} */ n) => `translate(${n.x},${n.y})`);

    lastPos = new Map(L.nodes.map((n) => [n.id, { x: n.x, y: n.y }]));
    applyRelations(); highlight(); renderCrumbs(); syncControls(); syncTypes(); syncViews(); fit(true);
    window.setTimeout(labelPass, DUR + 40);
    statusEl.textContent = L.hops ? "" : `${L.nodes.length} nodes · ${current.links.length} structure · ${current.rels.length} relations`;
    paintBanner();
  }

  /** @param {string} id */
  function tip(id) {
    const b = ix.byId.get(id); if (!b) return id;
    const c = wc(id), bits = [`${b.name} · ${b.type}`];
    if (c.count) bits.push(`${c.count} workitems · ${Math.round(c.completion * 100)}% done${c.active ? ` · ${c.active} active` : ""}${c.blocked ? ` · ${c.blocked} blocked` : ""}`);
    if (b.attentionCount) bits.push(`${b.attentionCount} need attention`);
    if (b.intentCount) bits.push(`${b.intentCount} intents`);
    if (b.docs.length) bits.push(`${b.docs.length} docs`);
    return bits.join("\n");
  }

  function fit(/** @type {boolean} */ animate) {
    if (!current) return;
    const r = pane.getBoundingClientRect();
    const t = fitTransform(current.nodes, r.width, r.height);
    current.fitK = t.k;
    (animate ? svg.transition().duration(DUR) : svg).call(zoom.transform, d3.zoomIdentity.translate(t.x, t.y).scale(t.k));
  }

  // ---------------------------------------------------------------- labels
  function labelPass() {
    if (!current) return;
    const t = d3.zoomTransform(svgNode), r = pane.getBoundingClientRect(), s = store.get(), scale = uiScale();
    gNode.selectAll("g.node").each(function (/** @type {any} */ n) {
      // @ts-ignore
      const c = this.classList; c.remove("lod1", "lod2", "lod3"); c.add("lod" + lodOf(n.rr * t.k));
    });
    // With nothing focused, every root is named; what hangs below them earns a label by size, like any ring.
    const vip = new Set([s.focus, s.selected, s.hover, ...(current.seeds || [])].filter(Boolean).map(String));
    const stars = new Set(/** @type {string[]} */ (s.stars || []));
    const star = (/** @type {string} */ id) => (stars.has(id) ? "★ " : "");
    const placed = placeLabels(current.nodes, t, {
      width: r.width, height: r.height, vip, focus: /** @type {string|null} */ (s.focus), overview: false,
      full: (id) => { const nm = ix.byId.get(id)?.name || id; return star(id) + (nm.length > 36 ? nm.slice(0, 35) + "…" : nm); },
      short: (id) => star(id) + (current.shorts.get(id) || id),
      work: (id) => { const c = wc(id); return c.count ? `${c.count} ${c.count === 1 ? "item" : "items"} · ${Math.round(c.completion * 100)}%${c.active ? ` · ${c.active} active` : ""}${c.blocked ? ` · ${c.blocked} blocked` : ""}` : null; },
      // Label fonts are `calc(<N>px * var(--ui-scale))`, so measure at the scaled size.
      measure: (text, size, weight) => { measureCtx.font = `${weight} ${size * scale}px ${fontFamily}`; return measureCtx.measureText(text).width; },
      scale,
      rank: (id) => ix.degree.get(id) || 0,
    });
    gLabel.selectAll("g").remove();
    const pos = current.pos;
    const groups = gLabel.selectAll("g").data(placed).enter().append("g")
      .attr("class", (/** @type {any} */ p) => (p.tier === "short" ? "short" : p.id === s.focus ? "focus" : "full"));
    groups.each(function (/** @type {any} */ p) {
      // @ts-ignore
      const tx = d3.select(this).append("text").attr("text-anchor", p.side === 0 ? "middle" : p.side === 1 ? "start" : "end");
      tx.append("tspan").attr("class", "nm").attr("x", 0).attr("dy", "1em").text(p.name);
      if (p.work) tx.append("tspan").attr("class", "wl").attr("x", 0).attr("dy", "1.35em").text(p.work);
      p.node = pos.get(p.id);
    });
    positionLabels(t);
    highlight();
  }
  function positionLabels(/** @type {any} */ t) {
    gLabel.selectAll("g").attr("transform", (/** @type {any} */ p) => {
      const n = p.node; if (!n) return null;
      const sx = t.applyX(n.x), sy = t.applyY(n.y), r = (n.rr + 4) * t.k;
      if (p.side === 0) return `translate(${sx},${sy + r + 1})`;
      return `translate(${p.side === 1 ? sx + r + 4 : sx - r - 4},${sy - p.h / 2 - 1})`;
    });
  }

  // ---------------------------------------------------------------- highlight
  /** The node itself if drawn, else its nearest drawn container. @param {string|null} id */
  function nearestDrawn(id) {
    let cur = id;
    for (let g = 0; cur && g < 12; g++) { if (current && current.pos.has(cur)) return cur; cur = containsParents(ix, cur)[0] || null; }
    return null;
  }
  function applyRelations() {
    const s = store.get(), f = s.hover || s.selected;
    gRel.selectAll("path").classed("off", (/** @type {any} */ e) => s.rel !== "all" && !(f && (e.s === f || e.d === f)));
  }
  function highlight() {
    applyRelations();
    const raw = /** @type {string|null} */ (store.get().hover);
    const h = nearestDrawn(raw);
    // A faded node comes back to full strength while it is the one being looked at.
    const sel = store.get().selected;
    const faded = (/** @type {string} */ id) => !!current && current.upstream.has(id) && id !== sel && id !== h;
    gNode.selectAll("g.node").classed("upstream", (/** @type {any} */ n) => faded(n.id));
    gLabel.selectAll("g").classed("upstream", (/** @type {any} */ p) => faded(p.id));
    // A pulse marks a node pointed at from ANOTHER pane (a card on the work board).
    gNode.selectAll("g.node").classed("pulse", (/** @type {any} */ n) => !!h && n.id === h && store.get().hoverFrom !== "graph");
    if (!h || !current) {
      gNode.selectAll("g.node").classed("dim", false); gLink.selectAll("path").classed("dim", false);
      gRel.selectAll("path").classed("dim", false).classed("hot", false); gLabel.selectAll("g").classed("dim", false);
      return;
    }
    // What stays lit: the node, its neighbours, and its whole way up to the root.
    const chain = new Set([h, ...allContainers(ix, h)]);
    const near = new Set(chain);
    for (const e of [...current.links, ...current.rels]) { if (e.s === h) near.add(e.d); if (e.d === h) near.add(e.s); }
    gNode.selectAll("g.node").classed("dim", (/** @type {any} */ n) => !near.has(n.id));
    gLink.selectAll("path").classed("dim", (/** @type {any} */ e) => e.s !== h && e.d !== h && !(chain.has(e.s) && chain.has(e.d)));
    gRel.selectAll("path").classed("dim", (/** @type {any} */ e) => e.s !== h && e.d !== h).classed("hot", (/** @type {any} */ e) => e.s === h || e.d === h);
    gLabel.selectAll("g").classed("dim", (/** @type {any} */ p) => !near.has(p.id));
  }

  // ---------------------------------------------------------------- navigation
  /** @param {string} id */
  function activate(id) { if (!store.get().focus) focusOn(id); else select(id); }
  /** @param {string} id */
  function select(id) {
    const n = ix.byId.get(id);
    store.set({ selected: id, ...(n && !n.isWork ? { scope: id } : {}) });
  }
  /** @param {string} id */
  function focusOn(id) {
    if (store.get().path) store.set({ path: null });
    const at = crumbs.indexOf(id);
    crumbs = at >= 0 ? crumbs.slice(0, at + 1) : [...crumbs, id];
    store.set({ focus: id, selected: id, scope: id });
  }
  // ---------------------------------------------------------------- path mode
  function loadPath() {
    const want = /** @type {{from: string, to: string}|null} */ (store.get().path);
    pathRes = null;
    if (!want) { render(); return; }
    paintBanner("Looking for routes…");
    const q = new URLSearchParams({ from: want.from, to: want.to, max: "3" });
    fetchJSON("/api/graph/path?" + q).then((r) => {
      const now = /** @type {any} */ (store.get().path);
      if (!now || now.from !== want.from || now.to !== want.to) return;
      pathRes = r; render();
    }).catch(() => paintBanner("Could not look the routes up."));
  }
  /** @param {string} [note] */
  function paintBanner(note) {
    const want = /** @type {{from: string, to: string}|null} */ (store.get().path);
    banner.hidden = !want;
    if (!want) return;
    const name = (/** @type {string} */ id) => ix.byId.get(id)?.name || id;
    banner.replaceChildren();
    const t = el("div", "pb-title");
    t.append("How ", el("b", null, name(want.from)), " connects to ", el("b", null, name(want.to)));
    const sub = el("div", "pb-sub");
    if (note) sub.textContent = note;
    else if (pathRes && !pathRes.paths.length) sub.textContent = `No route within ${pathRes.hops_max} steps that you can see.`;
    else if (pathRes) {
      const k = pathRes.paths.length, short = pathRes.paths[0].length;
      sub.textContent = `${k} shortest route${k === 1 ? "" : "s"} · the shortest is ${short} step${short === 1 ? "" : "s"}`;
    }
    banner.append(t, sub, button("Close", () => store.set({ path: null }), "btn"));
  }

  function renderCrumbs() {
    chrome.crumbs.replaceChildren();
    const s = store.get();
    if (s.focus && !crumbs.includes(/** @type {string} */ (s.focus))) crumbs = [String(s.focus)];
    /** @param {string} label @param {boolean} cur @param {() => void} fn */
    const add = (label, cur, fn) => {
      if (chrome.crumbs.childNodes.length) chrome.crumbs.append(el("span", "sep", "›"));
      const b = button(label, fn, "crumb"); b.title = label;
      if (cur) { b.setAttribute("aria-current", "true"); b.disabled = true; }
      chrome.crumbs.append(b);
    };
    add("Roots", !s.focus, () => { crumbs = []; store.set({ focus: null, selected: null, scope: null }); });
    if (s.focus) crumbs.forEach((id, i) => add(ix.byId.get(id)?.name || id, i === crumbs.length - 1, () => focusOn(id)));
  }

  // ---------------------------------------------------------------- wiring
  store.subscribe(["focus", "steps", "links", "work", "stars", "hideTypes", "collapsed"], render);
  store.subscribe(["views"], syncViews);
  store.subscribe(["rev"], () => { wcMemo.clear(); cachedKey = ""; searchKey = ""; render(); });
  store.subscribe(["path"], loadPath);
  store.subscribe(["rel"], () => { applyRelations(); syncControls(); });
  store.subscribe(["selected"], () => {
    gNode.selectAll("g.node").classed("selected", (/** @type {any} */ n) => n.id === store.get().selected);
    applyRelations(); clearTimeout(labelTimer); labelTimer = window.setTimeout(labelPass, 30);
  });
  store.subscribe(["hover"], highlight);
  store.subscribe(["uiScale"], () => { clearTimeout(labelTimer); labelTimer = window.setTimeout(labelPass, 30); });
  new ResizeObserver(() => { fit(false); clearTimeout(labelTimer); labelTimer = window.setTimeout(labelPass, 60); }).observe(pane);
  // A focus this graph does not hold (a stale or mistyped `?focus=`, a node
  // since removed) would draw the roots while clicks only select: drop it.
  const f0 = /** @type {string|null} */ (store.get().focus);
  if (f0 && !ix.byId.has(f0)) store.set({ focus: null });
  if (store.get().path) loadPath(); else render();

  return { fit, render };
}

function legend() {
  const box = el("div", "graph-legend");
  box.innerHTML = [
    '<span><svg viewBox="0 0 14 14"><circle cx="7" cy="7" r="5" fill="var(--att-green)"/></svg>colour = attention</span>',
    '<span><svg viewBox="0 0 14 14"><circle cx="7" cy="7" r="5" fill="none" stroke="var(--ring-track)" stroke-width="2.5"/><path d="M7 2 A5 5 0 0 1 12 7" fill="none" stroke="var(--done)" stroke-width="2.5"/></svg>ring = work done</span>',
    '<span><svg viewBox="0 0 14 14"><circle cx="7" cy="7" r="5" fill="var(--att-red)"/></svg>badge = needs attention</span>',
    '<span><svg viewBox="0 0 14 14"><circle cx="7" cy="7" r="5.5" fill="var(--panel)" stroke="var(--line-strong)"/><circle cx="7" cy="7" r="3" fill="none" stroke="var(--att-purple)" stroke-width="1.4"/></svg>intents</span>',
    '<span><i class="lg upstream"></i>faded = above the focus</span>',
    '<span><i class="lg contains"></i>contains</span><span><i class="lg works_on"></i>works on</span><span><i class="lg depends_on"></i>depends on</span><span><i class="lg hosted_on"></i>hosted on</span><span><i class="lg requires"></i>requires</span>',
  ].join("");
  return box;
}
