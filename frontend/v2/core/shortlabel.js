// @ts-check
// core/shortlabel.js — a short name for a small node (TASK-1288).
//
// DERIVED, NEVER STORED IN CODE. Writing a short name per node in a module would
// encode the graph. The rules below work from the node's own name and id:
// - A task is its number: `#1287`.
// - An identifier-style name (`atlas-backend`) drops the prefix it shares with
//   the node it was reached from, so under `atlas` it reads `backend`.
// - A prose name keeps its first significant words, up to 12 characters.
// - An optional `attributes.short_name`, if the data ever carries one, wins
//   (the caller passes it as `override`).
// The CASE of a name is never changed (the `render-names-verbatim` intent).

const STOP = new Set(["the", "a", "an", "and", "of", "for", "to", "in", "on", "&", "with"]);
const MAX = 14;

const clip = (/** @type {string} */ s) => (s.length > MAX ? s.slice(0, MAX - 1) + "…" : s);
const idSegs = (/** @type {string} */ id) => id.replace(/^(EPIC|STORY)-/, "").split("-");

/**
 * @param {{id: string, name: string, isWork?: boolean, override?: string|null}} n
 * @param {string|null} [parentId]  the node this one was reached from
 */
export function shortLabel(n, parentId) {
  if (n.override) return clip(n.override);
  const task = /^TASK-(\d+)$/.exec(n.id);
  if (task) return "#" + task[1];
  // A project-keyed task id (STORY-oss-12: `ABC-12`) is already short, and its key is what
  // tells two projects' #12 apart, so it is shown whole rather than prefix-stripped.
  if (/^(?!EPIC-|STORY-)[A-Z][A-Z0-9]{1,9}-\d+$/.test(n.id)) return clip(n.id);
  if (n.isWork || /^[a-z0-9][a-z0-9._-]*$/.test(n.name)) {
    let segs = idSegs(n.id);
    if (parentId) {
      const ps = idSegs(parentId);
      let i = 0;
      while (i < ps.length && i < segs.length - 1 && segs[i] === ps[i]) i++;
      segs = segs.slice(i);
    }
    return clip(segs.join("-"));
  }
  const words = n.name.split(/[\s/·:—–]+/).filter((w) => w && !STOP.has(w.toLowerCase()));
  let out = words[0] || n.name;
  for (let j = 1; j < words.length && (out + " " + words[j]).length <= 12; j++) out += " " + words[j];
  return clip(out);
}

/**
 * Short labels for every node on screen, unique among them: a repeat gets `·2`, `·3`.
 * @param {Array<{id: string, name: string, isWork?: boolean, override?: string|null, parent?: string|null}>} items
 * @returns {Map<string, string>}
 */
export function uniqueShorts(items) {
  const raw = new Map(), count = new Map(), used = new Map();
  for (const it of items) {
    const s = shortLabel(it, it.parent);
    raw.set(it.id, s); count.set(s, (count.get(s) || 0) + 1);
  }
  const out = new Map();
  for (const it of items) {
    const s = raw.get(it.id);
    if (count.get(s) > 1) {
      const n = (used.get(s) || 0) + 1; used.set(s, n);
      out.set(it.id, n > 1 ? s + "·" + n : s);
    } else out.set(it.id, s);
  }
  return out;
}
