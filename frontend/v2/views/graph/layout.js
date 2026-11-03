// @ts-check
// views/graph/layout.js — where every node goes (TASK-1287). Pure, so it is
// tested without a browser (layout.test.js).
//
// A RADIAL TREE, ALWAYS (owner decision, 2026-10-22: the force layout was "far too
// messy"). The spanning tree from the step search is laid out once per focus at
// the MAXIMUM step count, and the slider only decides how many rings show.
// So a node that stays on screen never moves when the slider does.
//
// Each child sits in its parent's angular sector, so structure links never
// cross. Each ring is at least GAP further out than the last, and wide enough
// for its nodes side by side.

import { cmpNodes } from "../../core/graph-model.js";

export const GAP = 150, SPACING = 46, MAX_STEPS = 4;
/** Path view spacing: between steps along a route, and between routes. */
export const PATH_COL = 210, PATH_ROW = 130;
export const radiusOf = (/** @type {number} */ depth) => (depth === 0 ? 20 : depth === 1 ? 14 : 10);

/** @param {number} a @param {number} r */
export const polar = (a, r) => ({ x: r * Math.sin(a), y: -r * Math.cos(a) });

/**
 * @typedef {{id: string, depth: number, parent: string|null, via: string|null, a: number, r: number, x: number, y: number, rr: number}} Placed
 */

/**
 * @param {import("../../core/graph-model.js").Index} ix
 * @param {Map<string, {depth: number, parent: string|null, via: string|null}>} seen  stepSearch at MAX_STEPS
 * @param {string} focus
 * @param {{d3: any}} deps  d3-hierarchy's tree, passed in so this file runs in node
 * @returns {{all: Placed[], radii: number[]}}
 */
export function radialTree(ix, seen, focus, deps) {
  const cmp = cmpNodes(ix);
  /** @type {Map<string, string[]>} */
  const kids = new Map();
  for (const [id, v] of seen) {
    if (v.parent == null) continue;
    if (!kids.has(v.parent)) kids.set(v.parent, []);
    /** @type {string[]} */ (kids.get(v.parent)).push(id);
  }
  /** @param {string} id @returns {any} */
  const build = (id) => ({ id, children: (kids.get(id) || []).sort(cmp).map(build) });
  const h = deps.d3.hierarchy(build(focus));
  deps.d3.tree().size([2 * Math.PI, 1])
    .separation((/** @type {any} */ a, /** @type {any} */ b) => (a.parent === b.parent ? 1 : 1.6) / Math.max(1, a.depth))(h);
  /** @type {Map<string, number>} */
  const ang = new Map();
  h.each((/** @type {any} */ d) => ang.set(d.data.id, d.x));

  const cnt = /** @type {number[]} */ ([]);
  for (const v of seen.values()) cnt[v.depth] = (cnt[v.depth] || 0) + 1;
  const maxDepth = cnt.length - 1;
  const radii = [0];
  for (let d = 1; d <= Math.max(maxDepth, 1); d++) radii[d] = Math.max(radii[d - 1] + GAP, ((cnt[d] || 0) * SPACING) / (2 * Math.PI));

  /** @type {Placed[]} */
  const all = [];
  for (const [id, v] of seen) {
    const a = /** @type {number} */ (ang.get(id)), r = radii[v.depth];
    const p = id === focus ? { x: 0, y: 0 } : polar(a, r);
    all.push({ id, depth: v.depth, parent: v.parent, via: v.via, a, r, x: p.x, y: p.y, rr: radiusOf(v.depth) });
  }
  return { all, radii };
}

/**
 * The opening view: the roots on one ring, starred nodes on a ring outside it.
 * @param {string[]} roots @param {string[]} starred  (already excluding roots)
 * @returns {{all: Placed[], radii: number[]}}
 */
export function overview(roots, starred) {
  const r1 = Math.max(160, (roots.length * 120) / (2 * Math.PI)), r2 = r1 + 170;
  /** @type {Placed[]} */
  const all = [];
  roots.forEach((id, i) => { const a = (i / Math.max(1, roots.length)) * 2 * Math.PI, p = polar(a, r1); all.push({ id, depth: 0, parent: null, via: null, a, r: r1, ...p, rr: 22 }); });
  starred.forEach((id, i) => { const a = ((i + 0.5) / starred.length) * 2 * Math.PI, p = polar(a, r2); all.push({ id, depth: 0, parent: null, via: null, a, r: r2, ...p, rr: 16 }); });
  return { all, radii: [] };
}

/**
 * The SVG path for a structure link in the radial tree: out along the parent's
 * radius, round the arc, in to the child. So links follow the rings instead of
 * cutting across them.
 * @param {Placed} a  parent @param {Placed} b  child
 */
/**
 * How two nodes connect (TASK-1298), laid out flat: `from` on the left, `to` on
 * the right, one row per route with its steps evenly between them. A node on
 * more than one route is drawn once, where it first appears.
 * @param {{from: string, to: string, paths: Array<{length: number, hops: Array<{from: string, to: string, edge_type: string, direction: string, edge: number}>}>}} res
 *   the `/api/graph/path` answer
 * @returns {{all: Placed[], links: Array<{key: string, s: string, d: string, t: string, dir: string}>}}
 */
export function pathLayout(res) {
  const n = res.paths.length, longest = Math.max(1, ...res.paths.map((p) => p.length));
  const half = (longest * PATH_COL) / 2;
  /** @type {Placed[]} */
  const all = [];
  const at = new Set();
  const place = (/** @type {string} */ id, /** @type {number} */ x, /** @type {number} */ y, /** @type {number} */ depth, /** @type {string|null} */ parent) => {
    if (at.has(id)) return;
    at.add(id);
    all.push({ id, depth, parent, via: null, a: 0, r: 0, x, y, rr: radiusOf(depth) });
  };
  place(res.from, -half, 0, 0, null);
  place(res.to, half, 0, 0, null);
  /** @type {Array<{key: string, s: string, d: string, t: string, dir: string}>} */
  const links = [];
  // A one-step route is the straight line between the ends, so when there is
  // one, no other route may take the middle row: they alternate above and below.
  const direct = res.paths.some((p) => p.length <= 1);
  let k = 0;
  res.paths.forEach((p, i) => {
    const y = p.length <= 1 ? 0 : direct ? (k % 2 ? 1 : -1) * PATH_ROW * (Math.floor(k / 2) + 1) : (i - (n - 1) / 2) * PATH_ROW;
    if (p.length > 1) k++;
    p.hops.forEach((h, j) => {
      if (j < p.hops.length - 1) place(h.to, -half + (2 * half * (j + 1)) / p.length, y, 1, h.from);
      links.push({ key: `${i}:${j}:${h.edge}`, s: h.from, d: h.to, t: h.edge_type, dir: h.direction });
    });
  });
  return { all, links };
}

export function linkPath(a, b) {
  const rm = (a.r + b.r) / 2;
  if (!a.r) { const p0 = polar(b.a, rm); return `M0,0L${p0.x},${p0.y}L${b.x},${b.y}`; }
  const p1 = polar(a.a, rm), p2 = polar(b.a, rm);
  const sweep = b.a > a.a ? 1 : 0, large = Math.abs(b.a - a.a) > Math.PI ? 1 : 0;
  return `M${a.x},${a.y}L${p1.x},${p1.y}A${rm},${rm} 0 ${large} ${sweep} ${p2.x},${p2.y}L${b.x},${b.y}`;
}

/** A relation (any non-tree link), bowed through the open middle rather than along a ring's labels. @param {{x:number,y:number}} a @param {{x:number,y:number}} b @param {number} [pull] */
export function relPath(a, b, pull = 0.45) {
  const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
  return `M${a.x},${a.y}Q${mx * pull},${my * pull} ${b.x},${b.y}`;
}

/**
 * The zoom transform that fits `nodes` into a `w`×`h` pane.
 * @param {Array<{x:number,y:number,rr:number}>} nodes @param {number} w @param {number} h
 */
export function fitTransform(nodes, w, h) {
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const n of nodes) { x0 = Math.min(x0, n.x - n.rr); x1 = Math.max(x1, n.x + n.rr); y0 = Math.min(y0, n.y - n.rr); y1 = Math.max(y1, n.y + n.rr); }
  if (!nodes.length) return { k: 1, x: w / 2, y: h / 2 };
  const padX = 90, padY = 60;
  let k = Math.min(w / (x1 - x0 + 2 * padX), h / (y1 - y0 + 2 * padY), 2.2) * 0.94;
  if (!Number.isFinite(k) || k <= 0) k = 1;
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  return { k, x: w / 2 - cx * k, y: h / 2 - cy * k };
}
