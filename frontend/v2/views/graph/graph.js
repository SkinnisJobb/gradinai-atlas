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

import { stepSearch, pruneTypes, isHub, workCounts, containsParents } from "../../core/graph-model.js";
import { uniqueShorts } from "../../core/shortlabel.js";
import { radialTree, overview, pathLayout, linkPath, relPath, fitTransform, MAX_STEPS } from "./layout.js";
import { fetchJSON } from "../../core/api.js";
import { placeLabels, lodOf } from "./labels.js";
import { drawGlyph } from "./glyph.js";
import { el, svgEl, button } from "../../ui/dom.js";
import { uiScale } from "../../ui/viewprefs.js";

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
  /** @type {Map<string, number>} node types reachable from the focus, before the type filter, with counts */
  let typesHere = new Map();
  /** @type {any} */ let current = null;
  /** @type {string[]} */ let crumbs = [];
  let labelTimer = 0;
  /** @type {Map<string, {x:number,y:number}>} */ let lastPos = new Map();

  const zoom = d3.zoom().scaleExtent([0.02, 8])
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
    types.hidden = !s.focus || !typesHere.size;
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
    steps.value = String(s.steps); steps.disabled = !s.focus; stepsVal.textContent = String(s.steps);
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
    if (!s.focus || !ix.byId.has(/** @type {string} */ (s.focus))) {
      const extra = stars.filter((id) => ix.byId.has(id) && !ix.roots.includes(id));
      const o = overview(ix.roots, extra);
      return { nodes: o.all, radii: o.radii, overview: true, pos: new Map(o.all.map((p) => [p.id, p])) };
    }
    const focus = /** @type {string} */ (s.focus);
    const opts = { links: /** @type {"contains"|"all"} */ (s.links), work: !!s.work };
    const hidden = new Set(/** @type {string[]} */ (s.hideTypes || []));
    const key = [focus, s.links, s.work, [...hidden].sort().join(",")].join("|");
    if (key !== cachedKey) {
      const full = stepSearch(ix, focus, MAX_STEPS, opts);
      typesHere = new Map();
      for (const id of full.keys()) if (id !== focus) { const t = /** @type {any} */ (ix.byId.get(id)).type; typesHere.set(t, (typesHere.get(t) || 0) + 1); }
      cached = radialTree(ix, pruneTypes(ix, full, hidden), focus, { d3 }); cachedKey = key;
    }
    const k = Number(s.steps) || 2;
    const nodes = /** @type {NonNullable<typeof cached>} */ (cached).all.filter((p) => p.depth <= k);
    return { nodes, radii: /** @type {NonNullable<typeof cached>} */ (cached).radii.slice(0, k + 1), overview: false, pos: new Map(nodes.map((p) => [p.id, p])), opts, focus };
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
    const line = (/** @type {any} */ l) => { const a = L.pos.get(l.s), b = L.pos.get(l.d); return L.hops ? `M${a.x},${a.y}L${b.x},${b.y}` : linkPath(a, b); };
    current.shorts = uniqueShorts(L.nodes.map((n) => ({ ...(/** @type {any} */ (ix.byId.get(n.id))), parent: n.parent })));
    gLabel.selectAll("g").remove();

    const guides = gGuide.selectAll("circle").data(L.overview ? [] : L.radii.slice(1));
    guides.exit().remove();
    guides.enter().append("circle").attr("class", "ring-guide").merge(guides).attr("r", (/** @type {number} */ r) => r);

    const ls = gLink.selectAll("path").data(current.links, (/** @type {any} */ l) => l.key);
    ls.exit().remove();
    ls.enter().append("path").attr("class", "link").style("opacity", 0).attr("d", line)
      .merge(ls).classed("hop", !!L.hops).transition().duration(DUR).style("opacity", null).attr("d", line);

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
    });
    all.attr("class", (/** @type {any} */ n) => "node" + (n.id === focus ? " focus" : "") + (n.id === store.get().selected ? " selected" : ""))
      .attr("aria-label", (/** @type {any} */ n) => { const b = ix.byId.get(n.id); return `${b?.type}, ${b?.name}`; });
    all.select("title").text((/** @type {any} */ n) => tip(n.id));
    all.transition().duration(DUR).style("opacity", null).attr("transform", (/** @type {any} */ n) => `translate(${n.x},${n.y})`);

    lastPos = new Map(L.nodes.map((n) => [n.id, { x: n.x, y: n.y }]));
    applyRelations(); highlight(); renderCrumbs(); syncControls(); syncTypes(); fit(true);
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
    const vip = new Set([s.focus, s.selected, s.hover].filter(Boolean).map(String));
    const stars = new Set(/** @type {string[]} */ (s.stars || []));
    const star = (/** @type {string} */ id) => (stars.has(id) ? "★ " : "");
    const placed = placeLabels(current.nodes, t, {
      width: r.width, height: r.height, vip, focus: /** @type {string|null} */ (s.focus), overview: current.overview,
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
    // A pulse marks a node pointed at from ANOTHER pane (a card on the work board).
    gNode.selectAll("g.node").classed("pulse", (/** @type {any} */ n) => !!h && n.id === h && store.get().hoverFrom !== "graph");
    if (!h || !current) {
      gNode.selectAll("g.node").classed("dim", false); gLink.selectAll("path").classed("dim", false);
      gRel.selectAll("path").classed("dim", false).classed("hot", false); gLabel.selectAll("g").classed("dim", false);
      return;
    }
    const near = new Set([h]);
    for (const e of [...current.links, ...current.rels]) { if (e.s === h) near.add(e.d); if (e.d === h) near.add(e.s); }
    gNode.selectAll("g.node").classed("dim", (/** @type {any} */ n) => !near.has(n.id));
    gLink.selectAll("path").classed("dim", (/** @type {any} */ e) => e.s !== h && e.d !== h);
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
  store.subscribe(["focus", "steps", "links", "work", "stars", "hideTypes"], render);
  store.subscribe(["rev"], () => { wcMemo.clear(); cachedKey = ""; render(); });
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
    '<span><i class="lg contains"></i>contains</span><span><i class="lg works_on"></i>works on</span><span><i class="lg depends_on"></i>depends on</span><span><i class="lg hosted_on"></i>hosted on</span><span><i class="lg requires"></i>requires</span>',
  ].join("");
  return box;
}
