// @ts-check
// core/graph-model.js — the graph as board v2 reasons about it (TASK-1305).
//
// Pure: no DOM, no fetch. It takes the `/api/graph` payload, so it only ever
// holds what the server already decided this caller may see. Everything the
// panes ask about the graph is a function here, and each one is tested in
// graph-model.test.js:
// - roots, and the steps out from a focus
// - which world node a piece of work belongs to
// - what a node's work board contains
//
// NO MODULE MAY ENCODE THE GRAPH (the atlas-frontend intent). A type
// vocabulary is presentation config, so it lives here. A node id never does.
// THE WORKITEM TYPES AND THE LADDER are not presentation config: the server
// owns them (`/api/vocabulary`, TASK-1389). They are passed into `buildIndex`
// as an argument (TASK-1391) and carried on the index; core/vocabulary.js holds
// the fallback used before the vocabulary loads or when it fails.

import { FALLBACK_VOCABULARY } from "./vocabulary.js";

export const CLOSED = new Set(["done", "cancelled"]);
/**
 * The status a workitem with none is drawn with (TASK-1368). The API reports
 * such a row's status as null (a workitem created without one, or a legacy
 * row): nothing on the server fills it in. The board used to keep it as "",
 * which matches no column, so the card was never drawn. "pending" is the
 * column the API's own default (`atlas_file_work`'s status) puts new work in.
 * Display only: nothing is written back.
 */
export const DEFAULT_WORK_STATUS = "pending";

/**
 * Authored statuses that MEAN one of the settable ones (TASK-1379). A MIRROR of
 * `STATUS_EQUIVALENT` in backend/attention.py (the source; legacy
 * frontend/state.js carries a second copy). Keep all three equal:
 * graph-model.test.js reads attention.py and fails when this one drifts.
 */
export const STATUS_EQUIVALENT = Object.freeze(/** @type {Record<string, string>} */ ({
  planned: "pending", todo: "pending", backlog: "pending",
  not_started: "pending", proposed: "pending",
  wip: "in_progress",
  accepted: "done", closed: "done",
}));

/** Normalise a raw status the way the backend does (attention._norm). @param {unknown} s */
const normStatus = (s) => (s ? String(s).trim().toLowerCase().replace(/[ -]/g, "_") : "");

/**
 * The status a workitem is DRAWN with (TASK-1368, TASK-1379). Display only:
 * nothing is written back, and the panel's status control still reads the
 * API's own value for the item.
 * - none at all: DEFAULT_WORK_STATUS;
 * - a synonym (planned, todo, wip, closed, …): its STATUS_EQUIVALENT bucket, so
 *   `closed` counts as done and obeys the done window like `done` does;
 * - ONE deliberate exception: `proposed` is kept, because board v2 draws it as
 *   its own column (views/work/board.js COLS). The backend buckets it onto
 *   pending only to compare spellings; the board shows the difference.
 * @param {unknown} raw
 */
export function workStatus(raw) {
  const s = normStatus(raw);
  if (!s) return DEFAULT_WORK_STATUS;
  if (s === "proposed") return s;
  return STATUS_EQUIVALENT[s] || s;
}

/** The board's kanban columns, in order (views/work/board.js labels them). */
export const BOARD_COLUMNS = Object.freeze(["proposed", "pending", "in_progress", "blocked", "done"]);
/**
 * The column a card with this (already `workStatus`-ed) status is drawn in, or
 * null for one the board never draws (cancelled). `analyze` is a real settable
 * status with no column of its own: it is the step after pending in the
 * lifecycle (attention.SETTABLE_STATUSES), so it shows as In progress. Any
 * other status no column names lands in To do rather than disappearing.
 * @param {string} status
 */
export function boardColumn(status) {
  if (status === "cancelled") return null;
  if (BOARD_COLUMNS.includes(status)) return status;
  if (status === "analyze") return "in_progress";
  return "pending";
}
/** In "all links" mode, a node with more cross-links than this is shown but not stepped through. */
export const HUB_DEGREE = 15;

/**
 * @typedef {{
 *   id: string, name: string, type: string, isWork: boolean,
 *   attention: string, completion: number, status: string, attentionCount: number,
 *   intentCount: number, docs: Array<{kind?: string, title?: string, path: string, format?: string, external?: boolean}>,
 *   workLoad: {count: number, done: number, completion: number, ids: string[]},
 *   assignee: {id?: string, username?: string, name?: string} | null,
 *   priority: string|null, labels: string[],
 *   completedAt: string, state: string, urgency: string, override: string|null,
 * }} GNode
 * @typedef {{o: string, t: string, out: boolean}} Adj
 * @typedef {ReturnType<typeof buildIndex>} Index
 */

/**
 * @param {{nodes?: any[], edges?: any[]}} payload  the `/api/graph` response
 * @param {import("./vocabulary.js").Vocabulary} [vocab]  `/api/vocabulary`'s
 *   `workitem_types` and `workitem_ladder` (core/vocabulary.js `vocabularyFrom`).
 *   A node is work when its type is in `workitem_types`; its rung, when it has
 *   one, is its index in `workitem_ladder`. Omitted: FALLBACK_VOCABULARY.
 */
export function buildIndex(payload, vocab = FALLBACK_VOCABULARY) {
  const workTypes = new Set(vocab.workitem_types);
  /** A work type's rung on the ladder, top first. A work type off the ladder has none. */
  const rank = new Map(vocab.workitem_ladder.filter((t) => workTypes.has(t)).map((t, i) => [t, i]));
  /** @type {Map<string, GNode>} */
  const byId = new Map();
  for (const n of payload.nodes || []) {
    const wl = n.work_load || {};
    byId.set(n.id, {
      id: n.id, name: n.name || n.id, type: n.type || "node", isWork: workTypes.has(n.type),
      attention: n.attention || "grey", completion: n.completion || 0, status: workTypes.has(n.type) ? workStatus(n.status) : (n.status || ""),
      attentionCount: n.attention_count || 0, intentCount: n.intent_count || 0,
      docs: ((n.attributes && n.attributes.docs) || []).filter((d) => d && d.path),
      workLoad: { count: wl.count || 0, done: wl.done || 0, completion: wl.completion || 0, ids: wl.workitem_ids || [] },
      assignee: n.assignee || null, priority: n.priority || null, labels: Array.isArray(n.labels) ? n.labels : [],
      completedAt: n.completed_at || "", state: n.state || "", urgency: n.urgency || "",
      // An optional short name carried in the data (TASK-1288). shortlabel.js prefers it.
      override: (n.attributes && typeof n.attributes.short_name === "string" && n.attributes.short_name.trim()) || null,
    });
  }
  /** @type {Map<string, Adj[]>} */
  const adj = new Map([...byId.keys()].map((id) => [id, []]));
  const containsIn = new Set(), xdeg = new Map(), degree = new Map();
  /** @type {Map<string, string>} */ const boundTo = new Map();
  /** @type {Map<string, string>} */ const workParent = new Map();
  /** @type {Map<string, number>} */ const skills = new Map();
  const bump = (m, k) => m.set(k, (m.get(k) || 0) + 1);
  for (const e of payload.edges || []) {
    const a = byId.get(e.src), b = byId.get(e.dst);
    // An edge with an end we were not given is dropped, never drawn as a stub:
    // the end may be a node this caller may not see (LEAK TEST 4).
    if (!a || !b) continue;
    adj.get(a.id).push({ o: b.id, t: e.edge_type, out: true });
    adj.get(b.id).push({ o: a.id, t: e.edge_type, out: false });
    bump(degree, a.id); bump(degree, b.id);
    if (e.edge_type === "contains") {
      containsIn.add(b.id);
      if (a.isWork && b.isWork && !workParent.has(b.id)) workParent.set(b.id, a.id);
      if (b.type === "skill") bump(skills, a.id);
    } else if (!a.isWork && !b.isWork) {
      bump(xdeg, a.id); bump(xdeg, b.id);
    }
    if (e.edge_type === "works_on" && a.isWork && !b.isWork && !boundTo.has(a.id)) boundTo.set(a.id, b.id);
  }
  const roots = [...byId.values()].filter((n) => !n.isWork && !containsIn.has(n.id))
    .sort((x, y) => x.name.localeCompare(y.name)).map((n) => n.id);
  return { byId, adj, roots, xdeg, degree, boundTo, workParent, skills, workTypes, rank };
}

/**
 * Ordering used everywhere a list must be stable: world before work, then by type, then by name.
 * @param {Index} ix
 */
export function cmpNodes(ix) {
  return (/** @type {string} */ a, /** @type {string} */ b) => {
    const A = ix.byId.get(a), B = ix.byId.get(b);
    if (!A || !B) return a.localeCompare(b);
    return (+A.isWork - +B.isWork) || A.type.localeCompare(B.type) || A.name.localeCompare(B.name) || a.localeCompare(b);
  };
}

/** @param {Index} ix @param {string} id */
export function containsParents(ix, id) {
  return (ix.adj.get(id) || []).filter((x) => x.t === "contains" && !x.out).map((x) => x.o).sort(cmpNodes(ix));
}

/** The chain of containers above `id`, outermost first, following the first parent at each step. @param {Index} ix @param {string} id */
export function ancestry(ix, id) {
  const chain = [], seen = new Set([id]);
  let cur = id;
  for (;;) {
    const p = containsParents(ix, cur)[0];
    if (!p || seen.has(p)) break;
    seen.add(p); chain.unshift(p); cur = p;
  }
  return chain;
}

/**
 * @typedef {{links?: "contains"|"all", work?: boolean, hubDegree?: number}} StepOpts
 */

/** @param {Index} ix @param {string} id @param {string|null} focus @param {StepOpts} opts */
export function isHub(ix, id, focus, opts = {}) {
  return opts.links === "all" && id !== focus && (ix.xdeg.get(id) || 0) > (opts.hubDegree ?? HUB_DEGREE);
}

/**
 * The steps out from `focus`, as a breadth-first search to depth `k`. Each node
 * reached records its depth, the node it was reached from and the edge type that
 * did it, and that spanning tree is what the radial layout draws.
 * - A step is any `contains` edge, or any edge at all with `links: "all"`.
 * - With `work: true`, a works_on edge between work and a world node is a step
 *   too, so open work hangs off the node it works on. Closed work is never a
 *   node: it is counted in its node's ring instead.
 * - Hubs are reached but not stepped through.
 * Neighbours are visited in a fixed order, so the same inputs give the same tree.
 * @param {Index} ix @param {string} focus @param {number} k @param {StepOpts} [opts]
 * @returns {Map<string, {depth: number, parent: string|null, via: string|null}>}
 */
export function stepSearch(ix, focus, k, opts = {}) {
  const seen = new Map([[focus, { depth: 0, parent: /** @type {string|null} */ (null), via: /** @type {string|null} */ (null) }]]);
  if (!ix.byId.has(focus)) return new Map();
  const cmp = cmpNodes(ix), q = [focus];
  const nodeOk = (/** @type {string} */ id) => {
    const n = ix.byId.get(id);
    return !!n && (!n.isWork || id === focus || (!!opts.work && !CLOSED.has(n.status)));
  };
  const stepOk = (/** @type {string} */ from, /** @type {Adj} */ x) => {
    if (!nodeOk(x.o)) return false;
    if (opts.links === "all" || x.t === "contains") return true;
    const a = ix.byId.get(from), b = ix.byId.get(x.o);
    return !!opts.work && x.t === "works_on" && !!a && !!b && a.isWork !== b.isWork;
  };
  while (q.length) {
    const id = /** @type {string} */ (q.shift());
    const d = /** @type {{depth: number}} */ (seen.get(id)).depth;
    if (d >= k || isHub(ix, id, focus, opts)) continue;
    const next = (ix.adj.get(id) || []).filter((x) => !seen.has(x.o) && stepOk(id, x))
      .sort((a, b) => (+(a.t !== "contains") - +(b.t !== "contains")) || cmp(a.o, b.o));
    for (const x of next) {
      if (seen.has(x.o)) continue;
      seen.set(x.o, { depth: d + 1, parent: id, via: x.t });
      q.push(x.o);
    }
  }
  return seen;
}

/**
 * The type filter (legacy's type axis, TASK-1323): drop the nodes whose type is
 * hidden, from a `stepSearch` result. As in legacy, a hidden node that leads to
 * a kept one stays, so the tree never orphans what it kept, and the focus
 * always stays. Filtering is about what is drawn, not what exists.
 * @param {Index} ix @param {ReturnType<typeof stepSearch>} seen @param {Set<string>} hidden  node types
 * @returns {ReturnType<typeof stepSearch>}
 */
export function pruneTypes(ix, seen, hidden) {
  if (!hidden.size) return seen;
  const keep = new Set();
  for (const [id, v] of seen) {
    if (v.parent !== null && hidden.has(/** @type {GNode} */ (ix.byId.get(id)).type)) continue;
    for (let cur = /** @type {string|null} */ (id); cur !== null && !keep.has(cur); cur = /** @type {any} */ (seen.get(cur)).parent) keep.add(cur);
  }
  return new Map([...seen].filter(([id]) => keep.has(id)));
}

// ---- work ---------------------------------------------------------------

/** The world node a workitem belongs to: its own binding, else the nearest bound work container. @param {Index} ix @param {string} id */
export function boundNode(ix, id) {
  let cur = id;
  for (let guard = 0; cur && guard < 12; guard++) {
    const b = ix.boundTo.get(cur);
    if (b) return b;
    cur = /** @type {string} */ (ix.workParent.get(cur));
  }
  return null;
}

/** @param {Index} ix @param {string} id @param {string} type */
function workAncestorOfType(ix, id, type) {
  let cur = id;
  for (let guard = 0; cur && guard < 12; guard++) {
    if (ix.byId.get(cur)?.type === type) return cur;
    cur = /** @type {string} */ (ix.workParent.get(cur));
  }
  return null;
}
/** @param {Index} ix @param {string} id */
export const epicOf = (ix, id) => workAncestorOfType(ix, id, "epic");
/** The story directly containing a task, if any. @param {Index} ix @param {string} id */
export function storyOf(ix, id) {
  const p = ix.workParent.get(id);
  return p && ix.byId.get(p)?.type === "story" ? p : null;
}

/** @param {Index} ix @param {string} id @param {boolean} work  follow work or world `contains` */
function descendants(ix, id, work) {
  const out = new Set([id]), q = [id];
  while (q.length) {
    const x = /** @type {string} */ (q.shift());
    for (const y of ix.adj.get(x) || []) {
      if (y.t === "contains" && y.out && ix.byId.get(y.o)?.isWork === work && !out.has(y.o)) { out.add(y.o); q.push(y.o); }
    }
  }
  return out;
}
/** `id` and every world node it contains. @param {Index} ix @param {string} id */
export const worldDesc = (ix, id) => descendants(ix, id, false);
/** `id` and every workitem it contains. @param {Index} ix @param {string} id */
export const workDesc = (ix, id) => descendants(ix, id, true);

/**
 * The workitems `id` may be moved under (TASK-1355): every one ranked above it
 * on the ladder the index was built with (`ix.rank`, from `workitem_ladder`;
 * TASK-1391): an epic or a story for a task, an epic for a story, nothing for
 * the top rung. Never itself or anything beneath it, which would make a loop.
 * A work type off the ladder is offered no parent, and is never offered as one.
 * Ordered as the board orders nodes. The API still decides; this only keeps
 * the choice sane.
 * @param {Index} ix @param {string} id @returns {string[]}
 */
export function parentChoices(ix, id) {
  const n = ix.byId.get(id);
  const rungOf = (/** @type {GNode} */ w) => (w.isWork ? ix.rank.get(w.type) : undefined);
  const rank = n ? rungOf(n) : undefined;
  if (!n || rank === undefined) return [];
  const below = workDesc(ix, id);
  return [...ix.byId.values()]
    .filter((w) => { const r = rungOf(w); return r !== undefined && r < rank && !below.has(w.id); })
    .map((w) => w.id).sort(cmpNodes(ix));
}

/**
 * The work a board scoped to `scopeId` shows. WORK ROLLS UP, NEVER DOWN (the
 * `atlas` intent): a world node's board holds the work bound to it AND to
 * every node it contains, and nothing bound to a node containing it.
 * - A workitem scope means that item and everything under it.
 * - No scope means all work.
 * - Cancelled work is never shown. Done work shows only inside `doneDays`.
 * @param {Index} ix
 * @param {string|null} scopeId
 * @param {{who?: "all"|"mine"|"unassigned", me?: string, q?: string, doneDays?: number, now?: Date}} [f]
 */
export function scopeItems(ix, scopeId, f = {}) {
  const all = [...ix.byId.values()].filter((n) => n.isWork).map((n) => n.id);
  let ids;
  if (!scopeId || !ix.byId.has(scopeId)) ids = all;
  else if (ix.byId.get(scopeId)?.isWork) ids = [...workDesc(ix, scopeId)];
  else {
    const ws = worldDesc(ix, scopeId);
    ids = all.filter((w) => ws.has(/** @type {string} */ (boundNode(ix, w))));
  }
  const now = f.now || new Date(), q = (f.q || "").trim().toLowerCase();
  return ids.filter((id) => {
    const n = /** @type {GNode} */ (ix.byId.get(id));
    if (n.status === "cancelled") return false;
    if (n.status === "done") {
      if (!f.doneDays || !n.completedAt) return false;
      if ((now.getTime() - Date.parse(n.completedAt)) / 864e5 > f.doneDays) return false;
    }
    const who = n.assignee && n.assignee.username;
    if (f.who === "mine" && (!f.me || who !== f.me)) return false;
    if (f.who === "unassigned" && who) return false;
    if (q && !n.name.toLowerCase().includes(q) && !id.toLowerCase().includes(q)) return false;
    return true;
  });
}

/**
 * Counts for a node's dashboard and glyph. For a world node, the rolled-up work
 * scopeItems would show with no filter (open and done, never cancelled). For a
 * workitem, everything beneath it.
 * @param {Index} ix @param {string} id
 */
export function workCounts(ix, id) {
  const n = ix.byId.get(id);
  let ids;
  if (!n) ids = [];
  else if (n.isWork) ids = [...workDesc(ix, id)].filter((x) => x !== id);
  else { const ws = worldDesc(ix, id); ids = [...ix.byId.values()].filter((w) => w.isWork && ws.has(/** @type {string} */ (boundNode(ix, w.id)))).map((w) => w.id); }
  const live = ids.map((x) => /** @type {GNode} */ (ix.byId.get(x))).filter((w) => w.status !== "cancelled");
  const c = { count: live.length, done: 0, active: 0, blocked: 0, open: /** @type {string[]} */ ([]) };
  for (const w of live) {
    if (w.status === "done") c.done++;
    else { c.open.push(w.id); if (w.status === "in_progress") c.active++; if (w.status === "blocked") c.blocked++; }
  }
  return { ...c, completion: c.count ? c.done / c.count : 0 };
}
