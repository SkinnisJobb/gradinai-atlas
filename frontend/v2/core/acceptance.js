// @ts-check
// core/acceptance.js — what the Acceptance section needs to show an agent's
// proposed change (STORY-atlas-acceptance-human-approved): a line diff of the
// current list against the proposed one, and the sentence that says an
// attested change was made on a person's approval. Pure; no DOM.

/**
 * @typedef {{kind: "same"|"removed"|"added", text: string}} DiffLine
 */

/**
 * A line diff, criterion by criterion, from `current` to `proposed`: the
 * longest common subsequence stays `same`, the rest of `current` is `removed`
 * and the rest of `proposed` is `added`, in reading order (a removal before
 * the addition that replaces it). A reworded criterion therefore reads as one
 * removed and one added line, which is what a person approving it should see.
 * @param {string[]} current @param {string[]} proposed
 * @returns {DiffLine[]}
 */
export function acceptanceDiff(current, proposed) {
  const a = current || [], b = proposed || [];
  const n = a.length, m = b.length;
  /** @type {number[][]} */
  const L = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  /** @type {DiffLine[]} */
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ kind: "same", text: a[i] }); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push({ kind: "removed", text: a[i++] });
    else out.push({ kind: "added", text: b[j++] });
  }
  while (i < n) out.push({ kind: "removed", text: a[i++] });
  while (j < m) out.push({ kind: "added", text: b[j++] });
  return out;
}

/** How many lines a diff adds and removes. @param {DiffLine[]} diff */
export function diffCounts(diff) {
  let added = 0, removed = 0;
  for (const d of diff) { if (d.kind === "added") added++; else if (d.kind === "removed") removed++; }
  return { added, removed };
}

/**
 * The board's line for the latest attested change, or null:
 * "changed by <agent> on <person>'s approval: <quote>".
 * @param {any} last  `last_attested` from GET /api/workitem/{id}/acceptance-proposal
 */
export function attestedLine(last) {
  if (!last || !last.quote) return null;
  const who = last.approver ? last.approver.name || last.approver.username : null;
  return `changed by ${last.agent || "an agent"} on ${who || "a person"}'s approval: ${last.quote}`;
}
