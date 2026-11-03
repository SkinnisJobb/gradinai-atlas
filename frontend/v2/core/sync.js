// @ts-check
// core/sync.js — keeps board v2 windows in step, so a pane can live on a second
// monitor (TASK-1309).
//
// Every window has its own store. This mirrors the SHARED keys between them
// over a BroadcastChannel, which works between windows of the same origin and
// needs nothing from the server.
// - Selecting a node in one window updates the others.
// - A write in one window (`stale`) makes every window refetch.
// - Per-window keys are never mirrored: which panes are shown, the signed-in
//   name, the revision counter.
//
// A change that arrived from another window is applied without being sent
// back, so two windows cannot echo a change between them forever.

export const SHARED_KEYS = Object.freeze(["focus", "selected", "scope", "hover", "hoverFrom", "steps", "links", "rel", "work", "stars", "stale", "path"]);

/**
 * @param {import("./store.js").Store} store
 * @param {{channel?: {postMessage: (m: any) => void, onmessage: ((e: {data: any}) => void) | null, close?: () => void} | null, onPeer?: (msg: any) => void}} [opts]
 */
export function mirror(store, opts = {}) {
  const ch = opts.channel !== undefined ? opts.channel
    : typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel("atlas-board-v2");
  if (!ch) return { send: () => {}, close: () => {} };
  let applying = false;
  ch.onmessage = (e) => {
    const m = e.data || {};
    if (m.kind === "patch" && m.patch && typeof m.patch === "object") {
      /** @type {Record<string, unknown>} */
      const clean = {};
      for (const k of SHARED_KEYS) if (k in m.patch) clean[k] = m.patch[k];
      applying = true;
      try { store.set(clean); } finally { applying = false; }
    } else if (opts.onPeer) opts.onPeer(m);
  };
  const off = store.subscribe(SHARED_KEYS.slice(), (s, changed) => {
    if (applying) return;
    /** @type {Record<string, unknown>} */
    const patch = {};
    for (const k of changed) if (SHARED_KEYS.includes(k)) patch[k] = s[k];
    if (Object.keys(patch).length) ch.postMessage({ kind: "patch", patch });
  });
  return {
    /** A message that is not a state change, e.g. "the board pane opened in a window". @param {any} msg */
    send: (msg) => ch.postMessage(msg),
    close: () => { off(); if (ch.close) ch.close(); },
  };
}
