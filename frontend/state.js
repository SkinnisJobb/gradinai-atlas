// state.js — the board's shared mutable state, in one place instead of scattered
// across the old IIFE (TASK-1021, step 2 of STORY-atlas-board-modularize).
//
// MUTATION IDIOM, chosen deliberately and applied everywhere in this module graph:
//
//   ES module bindings are live but not assignable across modules. `export var TREE`
//   plus `import { TREE }` gives every importer a read-only view of the binding — it
//   sees a reassignment made INSIDE this module, but `TREE = x` from board.html either
//   silently no-ops or throws "Assignment to constant variable", depending on how it's
//   written. The pre-split code reassigns TREE, byId, workData, worldRoot, TREE_OF,
//   NODE_INDEX and currentTree wholesale from several places (the boot fetch,
//   refreshGraph, switchTab, …), so ten near-identical setter functions would be pure
//   ceremony for no safety this doesn't already have.
//
//   So this module exports ONE object, `state`, and every caller — including the code
//   still left in board.html — mutates its PROPERTIES (`state.TREE = x`) instead of
//   reassigning an imported name. Property assignment on an imported object is always
//   allowed; only reassigning the imported binding itself is restricted. That sidesteps
//   the live-binding limit entirely and needs no accessor functions.
//
//   Apply this consistently: nothing here declares a local `var TREE` that shadows
//   `state.TREE`, and nothing does `import { state } from "./state.js"` and then
//   assigns to a bare `TREE`.
//
// FILTER_DEFAULT_DAYS is exported alongside FILTER because it is FILTER's reset value
// (used both to seed the object below and to fall back to when a stored preference is
// invalid — see board.html's doneFilter setup) and duplicating "14" in two files would
// be its own bug waiting to happen.
//
// BRIDGE_TYPES and isAgedOut() moved in here for the same reason (TASK-1022): both are
// small, dependency-free pieces of the FILTER/world-projection model needed on BOTH
// sides of the board.html / map.js boundary — board.html's projectWorld()+pruneTree()
// and map.js's node-label rendering — and board.html can't export anything for map.js
// to import (it's an inline script, not a module file with a URL). Landing them here,
// next to the FILTER object they're already about, avoids a duplicate definition
// drifting out of sync in either direction.

import { daysSince } from "/assets/util.js";

export var FILTER_DEFAULT_DAYS = 14;

// world (object) node types that are bridge attachments — surfaced as an icon flag on
// their parent (board.html's projectWorld) and, on the map, as a "· <type>" label tag
// for a bridge node with no work_load of its own (map.js's update()).
export var BRIDGE_TYPES = { skill: 1, doc: 1, vision: 1 };   // `goal` retired, ADR 0032

// object/bridge node types make up the WORLD tree; everything else (epic/story/task)
// is the WORK tree. This is what splits world roots from work roots at boot
// (board.html's boot fetch and refreshGraph) — a type missing here is silently
// treated as a WORKITEM and lands in the Backlog tree. Any new object type must be
// added here AND to OBJECT_TYPES in backend/node_types.py (see backend/CLAUDE.md).
// `host` added 2026-08-02 with the host node type; `capability` 2026-08-12 with
// ADR 0030 (the level between a layer and the things that run).
//
// NOTE: that backend constant moved. It used to live in `backend/ingest_world.py`,
// which was DELETED with the boot ingest (STORY-atlas-retire-status-roundtrip);
// the registry seeds are in `backend/node_types.py` now.
//
// Landed here (TASK-1023) for the same reason BRIDGE_TYPES did (TASK-1022): both
// board.html (boot/refreshGraph root filtering) and panel.js (isObject(), deciding
// which panel sections a node gets) need it, and board.html can't export anything
// for panel.js to import.
export var OBJECT_TYPES = { org: 1, project: 1, system: 1, service: 1, component: 1, agent: 1, db: 1, host: 1, capability: 1, skill: 1, doc: 1, vision: 1 };
export function isObject(node) { return node && OBJECT_TYPES[node.type] === 1; }

// The settable workflow statuses (panel.js's status control) and their backlog
// synonyms. Landed here rather than in panel.js (TASK-1023's home for it) for
// the same reason BRIDGE_TYPES/OBJECT_TYPES did (TASK-1022/1023): the board's
// status FILTER (board.html) needs the identical vocabulary the status
// control offers, and board.html can't export for panel.js to import. One
// definition, read by both, instead of two lists that can drift apart. Must
// stay equal to `attention.SETTABLE_STATUSES` on the backend.
export var STATUS_CHOICES = [
  { value: "pending", label: "pending" },
  { value: "analyze", label: "analyze" },
  { value: "in_progress", label: "in progress" },
  { value: "blocked", label: "blocked" },
  { value: "done", label: "done" },
  { value: "cancelled", label: "cancelled" }
];

// Authored statuses that MEAN one of the settable ones above — see panel.js's
// status control for the fuller story (the backlog used `planned` for
// `pending` long before that control existed).
export var STATUS_EQUIVALENT = {
  planned: "pending", todo: "pending", backlog: "pending",
  not_started: "pending", proposed: "pending",
  wip: "in_progress",
  accepted: "done", closed: "done"
};

export function statusBucket(value) {
  if (!value) return null;
  return STATUS_EQUIVALENT[value] || value;
}

export var state = {
  TREE: null,
  byId: {},          // id -> node data (from the tree payload)
  allNodes: [],       // flat list of every WORK node (data objects) — feeds the summary

  // two-tree state (TASK-861/862): the board renders either the WORLD tree
  // (a `contains` projection over the object/bridge families) or the WORK tree
  // (the backlog). The world has FIVE roots and no single top node (ADR 0030);
  // this used to say it was rooted at one named org node, which stopped being an id
  // at all when that node was deleted.
  currentTree: "world",   // "world" | "work" — World is the default strategic view
  worldRoot: null,        // projected world hierarchy root (a render-only anchor over the roots)
  // the work tree as drawn: the epics bucketed by the PROJECT they belong to,
  // under the same invisible multi-root anchor the world tree uses (board.html's
  // groupWorkByProject). Falls back to one flat { id:"__root", name:"Backlog" }
  // hub over the epics when the world tree is unavailable.
  workData: null,
  workByObjectData: null, // the same work, hung off the objects it changes

  TREE_OF: {},            // node id -> "world" | "work" (for cross-link navigation)
  NODE_INDEX: {},         // node id -> {id,name,type,tree} — flat index for the find-a-node search

  // ============================================================ the age filter
  // "Hide work that finished more than N days ago." A backlog accumulates: most
  // of what is on this map is finished, and a wall of months-old green buries
  // the handful of things that are actually live.
  //
  // It prunes a COPY of the tree (pruneTree in board.html) and never touches
  // workData / byId / NODE_INDEX. That is deliberate: search, deep links and the
  // detail panel must still reach a node the map is hiding — filtering is about
  // what is drawn, not about what exists. Asking for a hidden node reveals it
  // (revealFiltered).
  //
  // EXTENDING IT: everything the filter decides goes through isAgedOut() and
  // this FILTER object. A second axis — hide a node type, an owner, a whole
  // epic — is a field here and a clause in isAgedOut's caller, not a rewrite.
  //
  // `statuses` and `types` are that second and third
  // axis: null means unrestricted on that axis (same convention as
  // doneAfterDays === 0), else an object map of the values still shown
  // ({done:1, cancelled:1, ...}). See isStatusHidden/isTypeHidden below —
  // board.html's pruneTree calls all three predicates at the same leaf
  // decision isAgedOut already sat at, so ancestor-preservation ("a matching
  // descendant keeps its parent on the map") is free for every axis at once.
  FILTER: { doneAfterDays: FILTER_DEFAULT_DAYS, statuses: null, types: null }  // 0/null = show everything
};

// Aged out = finished, and finished longer ago than the threshold. Anything without a
// completion date is NEVER hidden: no date means Atlas cannot say when (or whether) it
// finished, and hiding on a guess loses work.
export function isAgedOut(n) {
  if (!state.FILTER.doneAfterDays) return false;
  var d = daysSince(n && n.completed_at);
  return d !== null && d > state.FILTER.doneAfterDays;
}

function hasAnyKey(o) {
  if (!o) return false;
  for (var k in o) { if (Object.prototype.hasOwnProperty.call(o, k)) return true; }
  return false;
}

// Status-filtered = the filter is restricted to a set AND this node carries a
// workflow status AND that status (bucketed the same way the status control
// buckets it — `planned` counts as `pending`) is not in the set. A node with
// no status at all — every world-tree object (AGENTS.md: "a world-tree object
// has no workflow status") — is NEVER hidden by this axis, same principle as
// isAgedOut never hiding a node with no completion date.
export function isStatusHidden(n) {
  if (!hasAnyKey(state.FILTER.statuses)) return false;
  var bucket = statusBucket(n && n.status);
  if (!bucket) return false;
  return !state.FILTER.statuses[bucket];
}

// Type-filtered = the filter is restricted to a set AND this node's own type
// is not in it.
export function isTypeHidden(n) {
  if (!hasAnyKey(state.FILTER.types)) return false;
  if (!n || !n.type) return false;
  return !state.FILTER.types[n.type];
}
