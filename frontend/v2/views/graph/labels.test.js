import { test } from "node:test";
import assert from "node:assert/strict";
import { placeLabels, lodOf } from "./labels.js";

const measure = (s, size) => s.length * size * 0.6;
const base = (over = {}) => ({
  width: 1000, height: 800, vip: new Set(), focus: null, overview: false,
  full: (id) => "Full name " + id, short: (id) => id, work: () => null, measure, ...over,
});
const T = { k: 1, x: 500, y: 400 };

test("level of detail follows the radius on screen", () => {
  assert.deepEqual([lodOf(3), lodOf(8), lodOf(20)], [1, 2, 3]);
});

test("a small node gets a short label, a large one its full name, a tiny one none", () => {
  const p = placeLabels([
    { id: "tiny", x: -300, y: 0, rr: 3, depth: 2 },
    { id: "small", x: 0, y: 0, rr: 8, depth: 2 },
    { id: "big", x: 300, y: 0, rr: 14, depth: 1 },
  ], T, base());
  const by = Object.fromEntries(p.map((l) => [l.id, l]));
  assert.equal(by.tiny, undefined);
  assert.equal(by.small.tier, "short");
  assert.equal(by.big.tier, "full");
});

test("no two placed labels overlap, however crowded", () => {
  const nodes = Array.from({ length: 60 }, (_, i) => ({ id: "n" + i, x: (i % 10) * 30 - 150, y: Math.floor(i / 10) * 20, rr: 12, depth: 2 }));
  const p = placeLabels(nodes, T, base());
  const box = (l) => {
    const n = nodes.find((m) => m.id === l.id), r = n.rr + 4, x = n.x + T.x, y = n.y + T.y;
    const c = [{ x: x - l.w / 2, y: y + r + 2 }, { x: x + r + 4, y: y - l.h / 2 }, { x: x - r - 4 - l.w, y: y - l.h / 2 }][l.side];
    return { ...c, w: l.w, h: l.h };
  };
  const bs = p.map(box);
  for (let i = 0; i < bs.length; i++) for (let j = i + 1; j < bs.length; j++) {
    const a = bs[i], b = bs[j];
    assert.ok(!(a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y), `${p[i].id} overlaps ${p[j].id}`);
  }
  assert.ok(p.length < nodes.length, "some were dropped rather than overlapped");
});

test("the focus always keeps a label, in full, even where everything collides", () => {
  const nodes = Array.from({ length: 30 }, (_, i) => ({ id: "n" + i, x: 0, y: 0, rr: 12, depth: 2 }));
  nodes.push({ id: "f", x: 0, y: 0, rr: 20, depth: 0 });
  const p = placeLabels(nodes, T, base({ vip: new Set(["f"]), focus: "f" }));
  assert.ok(p.find((l) => l.id === "f"));
});

test("a node with work gets a second line only when there is room to read it", () => {
  const o = base({ work: () => "12 items · 40%" });
  const far = placeLabels([{ id: "a", x: 0, y: 0, rr: 14, depth: 1 }], { k: 0.6, x: 500, y: 400 }, o)[0];
  const near = placeLabels([{ id: "a", x: 0, y: 0, rr: 14, depth: 1 }], { k: 1.2, x: 500, y: 400 }, o)[0];
  assert.equal(far.work, null);
  assert.equal(near.work, "12 items · 40%");
});
