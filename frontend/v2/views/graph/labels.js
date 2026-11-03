// @ts-check
// views/graph/labels.js — which labels show, and where (TASK-1290). Pure: text
// width comes from a `measure` function the caller supplies (canvas in the
// browser, a stub in labels.test.js).
//
// LEVEL OF DETAIL comes from a node's radius ON SCREEN, not from the zoom level.
// So a small node far out stays quiet while the large focus keeps its name.
// - under 6 px: a dot (lod 1)
// - under 13 px: ring, icon and badge (lod 2)
// - from 13 px: pips, full name and a work line (lod 3)
//
// Labels are placed in priority order: the focus, the selected and hovered
// nodes, then by depth, then by size. Each goes into a coarse occupancy grid,
// below its node first, then right, then left. A label that collides
// everywhere is downgraded to its short form, then dropped. The VIP nodes always
// keep a label.

export const LOD = Object.freeze({ dot: 6, rich: 13 });
export const LINE = 15, LINE2 = 29;

/** @param {number} rs  on-screen radius */
export const lodOf = (rs) => (rs < LOD.dot ? 1 : rs < LOD.rich ? 2 : 3);

/**
 * @typedef {{id: string, x: number, y: number, rr: number, depth: number}} LNode
 * @typedef {{id: string, side: 0|1|2, tier: "full"|"short", name: string, work: string|null, w: number, h: number}} Placed
 */

/**
 * @param {LNode[]} nodes
 * @param {{k: number, x: number, y: number}} t  the zoom transform
 * @param {{
 *   width: number, height: number,
 *   vip: Set<string>, focus: string|null, overview: boolean,
 *   full: (id: string) => string, short: (id: string) => string, work: (id: string) => string|null,
 *   measure: (text: string, size: number, weight: number) => number,
 *   rank?: (id: string) => number, scale?: number,
 * }} o
 * @returns {Placed[]}
 */
export function placeLabels(nodes, t, o) {
  const cell = 48, grid = new Map(), placed = /** @type {Placed[]} */ ([]);
  const sx = (/** @type {LNode} */ n) => n.x * t.k + t.x, sy = (/** @type {LNode} */ n) => n.y * t.k + t.y;
  /** @param {{x:number,y:number,w:number,h:number}} b */
  function cells(b) {
    const out = [];
    for (let gx = Math.floor(b.x / cell); gx <= Math.floor((b.x + b.w) / cell); gx++)
      for (let gy = Math.floor(b.y / cell); gy <= Math.floor((b.y + b.h) / cell); gy++) out.push(gx + ":" + gy);
    return out;
  }
  /** @param {{x:number,y:number,w:number,h:number,node?:string}} b */
  function put(b) { for (const c of cells(b)) { if (!grid.has(c)) grid.set(c, []); grid.get(c).push(b); } }
  /** @param {{x:number,y:number,w:number,h:number}} b @param {string} id */
  function hits(b, id) {
    return cells(b).some((c) => (grid.get(c) || []).some((q) => q.node !== id
      && b.x < q.x + q.w && b.x + b.w > q.x && b.y < q.y + q.h && b.y + b.h > q.y));
  }
  // every node's mark is occupied space, so no label sits on a circle
  for (const n of nodes) { const r = (n.rr + 4) * t.k; put({ x: sx(n) - r, y: sy(n) - r, w: 2 * r, h: 2 * r, node: n.id }); }

  const rank = o.rank || (() => 0);
  const order = nodes.slice().sort((a, b) =>
    ((o.vip.has(a.id) ? -1 : a.depth) - (o.vip.has(b.id) ? -1 : b.depth)) || (b.rr - a.rr) || (rank(b.id) - rank(a.id)) || a.id.localeCompare(b.id));

  for (const n of order) {
    const x = sx(n), y = sy(n), r = (n.rr + 4) * t.k, rs = n.rr * t.k;
    if (x < -250 || x > o.width + 250 || y < -80 || y > o.height + 80) continue;
    const vip = o.vip.has(n.id) || o.overview;
    /** @type {Array<"full"|"short">} */
    const tiers = vip ? ["full", "short"] : rs < 5 ? [] : rs < 11 ? ["short"] : ["full", "short"];
    let done = false;
    for (let i = 0; i < tiers.length && !done; i++) {
      const tier = tiers[i], isFocus = n.id === o.focus;
      const name = tier === "short" ? o.short(n.id) : o.full(n.id);
      const size = tier === "short" ? 11 : isFocus ? 13 : 12, weight = tier === "short" ? 400 : isFocus ? 700 : 500;
      const work = tier === "full" && (rs >= 9 || vip) ? o.work(n.id) : null;
      const w = Math.max(o.measure(name, size, weight), work ? o.measure(work, 10.5, 400) : 0);
      const h = (work ? LINE2 : LINE) * (o.scale || 1);
      const cands = [{ x: x - w / 2, y: y + r + 2 }, { x: x + r + 4, y: y - h / 2 }, { x: x - r - 4 - w, y: y - h / 2 }];
      for (let j = 0; j < cands.length; j++) {
        const b = { ...cands[j], w, h, node: n.id };
        const last = vip && i === tiers.length - 1 && j === cands.length - 1;
        if (!hits(b, n.id) || last) {
          put(b);
          placed.push({ id: n.id, side: /** @type {0|1|2} */ (j), tier, name, work, w, h });
          done = true; break;
        }
      }
    }
  }
  return placed;
}
