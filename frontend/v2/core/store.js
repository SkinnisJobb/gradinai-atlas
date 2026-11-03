// @ts-check
// core/store.js — the one state every pane reads (ADR 0043).
//
// Legacy board.html mutates a shared `state` object and calls back into panes
// through `hooks`. Nothing tells a pane that something changed, so the shell
// has to remember to call every renderer after every change. Here a pane
// SUBSCRIBES to the keys it draws from and is told when any of them change.
// That is also what makes a pop-out window cheap (TASK-1309): mirroring the
// store's changes over a BroadcastChannel is enough to keep two windows in sync.
//
// The state is replaced, never mutated: `get()` returns a frozen object, and
// `set()` builds a new one. A subscriber can keep the object it was handed
// and compare it with the next one.

import { FALLBACK_VOCABULARY } from "./vocabulary.js";

/** @typedef {Record<string, unknown>} State */
/** @typedef {(state: Readonly<State>, changed: string[]) => void} Listener */

/**
 * What the board needs on first paint.
 * - `selected`: the node whose panel is open.
 * - `focus`: the centre of the graph (null means the roots).
 * - `scope`: what the work board shows.
 * - `hover`: the node under the pointer, in any pane.
 * - `vocab`: the workitem types and ladder (core/vocabulary.js). app.js reads
 *   `/api/vocabulary` once at boot into it; until then, and if that read
 *   fails, it is FALLBACK_VOCABULARY (TASK-1391).
 */
export const INITIAL = Object.freeze({
  focus: null,
  selected: null,
  scope: null,
  hover: null,
  hoverFrom: null,
  steps: 2,
  vocab: FALLBACK_VOCABULARY,
});

/**
 * @param {State} initial
 */
export function createStore(initial) {
  /** @type {Readonly<State>} */
  let state = Object.freeze({ ...initial });
  /** @type {Set<{keys: string[] | null, fn: Listener}>} */
  const subs = new Set();

  return {
    /** @returns {Readonly<State>} */
    get: () => state,

    /**
     * Shallow-merge `patch`. Returns the keys that actually changed. A value
     * that is `Object.is`-equal to the current one is not a change, so a
     * redundant set never wakes anybody.
     * @param {State} patch
     * @returns {string[]}
     */
    set(patch) {
      const changed = Object.keys(patch).filter((k) => !Object.is(state[k], patch[k]));
      if (!changed.length) return changed;
      state = Object.freeze({ ...state, ...patch });
      // Copy first: a listener may unsubscribe itself (or another) mid-loop.
      for (const s of [...subs]) {
        if (!subs.has(s)) continue;
        if (s.keys === null || s.keys.some((k) => changed.includes(k))) s.fn(state, changed);
      }
      return changed;
    },

    /**
     * `subscribe(fn)` hears every change; `subscribe(["focus"], fn)` only
     * changes to those keys. Returns the unsubscribe function.
     * @param {string[] | Listener} keysOrFn
     * @param {Listener} [fn]
     * @returns {() => void}
     */
    subscribe(keysOrFn, fn) {
      const entry = typeof keysOrFn === "function"
        ? { keys: null, fn: keysOrFn }
        : { keys: keysOrFn, fn: /** @type {Listener} */ (fn) };
      subs.add(entry);
      return () => { subs.delete(entry); };
    },
  };
}

/** @typedef {ReturnType<typeof createStore>} Store */
