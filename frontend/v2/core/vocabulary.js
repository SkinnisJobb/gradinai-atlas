// @ts-check
// core/vocabulary.js — the workitem types and the hierarchy ladder (TASK-1391).
//
// The server owns both: `GET /api/vocabulary` returns `workitem_types` (every
// type that is work) and `workitem_ladder` (the rungs a workitem may be moved
// between, top first; TASK-1389). app.js fetches it once at boot into the
// store and hands it to `buildIndex` (core/graph-model.js), which holds no list
// of its own.
//
// FALLBACK. Until the vocabulary has loaded, or if the read fails or answers
// something unusable, the board uses FALLBACK_VOCABULARY: exactly the lists
// graph-model.js hard-coded before this task (epic, story, task; ranked in
// that order). So a failed read leaves the board as it was, never broken.
// Pure: no DOM, no fetch.

/** @typedef {{workitem_types: readonly string[], workitem_ladder: readonly string[]}} Vocabulary */

/** @type {Readonly<Vocabulary>} */
export const FALLBACK_VOCABULARY = Object.freeze({
  workitem_types: Object.freeze(["epic", "story", "task"]),
  workitem_ladder: Object.freeze(["epic", "story", "task"]),
});

/** @param {unknown} v @returns {string[]|null} a list of non-empty strings, else null */
const strings = (v) => (Array.isArray(v) && v.length && v.every((x) => typeof x === "string" && x)
  ? [...new Set(/** @type {string[]} */ (v))] : null);

/**
 * The vocabulary the board uses, from a `/api/vocabulary` body (or null when
 * the read failed).
 * - No usable `workitem_types`: the whole fallback.
 * - No usable `workitem_ladder` (a server older than TASK-1389): the fallback
 *   ladder, kept to the rungs that are still work types.
 * - A ladder rung that is not a work type is dropped: only work is moved.
 * @param {any} body
 * @returns {Readonly<Vocabulary>}
 */
export function vocabularyFrom(body) {
  const types = strings(body && body.workitem_types);
  if (!types) return FALLBACK_VOCABULARY;
  const ladder = (strings(body.workitem_ladder) || [...FALLBACK_VOCABULARY.workitem_ladder]).filter((t) => types.includes(t));
  return Object.freeze({ workitem_types: Object.freeze(types), workitem_ladder: Object.freeze(ladder) });
}
