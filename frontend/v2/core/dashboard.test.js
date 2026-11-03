import { test } from "node:test";
import assert from "node:assert/strict";
import {
  defineBox, registry, boxColumns, readCollapsed, toggleCollapsed, storedCollapsed, loadBoxes, planBoxes,
  COLLAPSED_KEY, SHOWN_KEY, MAX_BOXES, BOX_WIDTH, BOX_GAP,
} from "./dashboard.js";
import { createPrefs } from "./prefs.js";

const box = (id, extra = {}) => ({ id, title: id.toUpperCase(), load: () => ({ n: 2 }), render: () => {}, ...extra });

function memory() {
  const m = new Map();
  return { m, getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
}

test("a box needs an id, a title, load and render; count and drawn are optional functions", () => {
  const b = defineBox(box("my-tasks"));
  assert.equal(b.id, "my-tasks");
  assert.ok(Object.isFrozen(b));
  assert.throws(() => defineBox(/** @type {any} */ ({ ...box("x"), id: "" })), /id/);
  assert.throws(() => defineBox(/** @type {any} */ ({ ...box("x"), id: "My Tasks" })), /id/);
  assert.throws(() => defineBox(/** @type {any} */ ({ ...box("x"), title: " " })), /title/);
  assert.throws(() => defineBox(/** @type {any} */ ({ ...box("x"), load: null })), /load/);
  assert.throws(() => defineBox(/** @type {any} */ ({ ...box("x"), render: "no" })), /render/);
  assert.throws(() => defineBox(/** @type {any} */ ({ ...box("x"), count: 3 })), /count/);
  assert.throws(() => defineBox(/** @type {any} */ ({ ...box("x"), drawn: true })), /drawn/);
  assert.throws(() => defineBox(/** @type {any} */ (null)), TypeError);
});

test("the registry keeps order and refuses an id twice", () => {
  const r = registry([box("a"), box("b")]);
  assert.deepEqual(r.map((b) => b.id), ["a", "b"]);
  assert.throws(() => registry([box("a"), box("a")]), /twice/);
});

test("columns follow the width: 3, 2 or 1 boxes of about 400 px", () => {
  const w = (n) => n * BOX_WIDTH + (n - 1) * BOX_GAP;
  assert.equal(boxColumns(w(3)), 3);
  assert.equal(boxColumns(w(3) - 1), 2);
  assert.equal(boxColumns(w(2)), 2);
  assert.equal(boxColumns(w(2) - 1), 1);
  assert.equal(boxColumns(3440), 3, "never more than 3");
  assert.equal(boxColumns(320), 1, "never fewer than 1, even at phone width");
  assert.equal(boxColumns(0), 1);
  assert.equal(boxColumns(NaN), 1);
});

test("the collapsed boxes are kept under dashboard.collapsed, and none collapsed stores nothing", () => {
  assert.equal(COLLAPSED_KEY, "dashboard.collapsed");
  assert.equal(SHOWN_KEY, "dashboard.on");
  const st = memory(), p = createPrefs(st);
  let list = readCollapsed(p.recall(COLLAPSED_KEY, []));
  assert.deepEqual(list, []);
  list = toggleCollapsed(list, "my-tasks");
  p.remember(COLLAPSED_KEY, storedCollapsed(list));
  assert.equal(st.m.get("atlas.v2.dashboard.collapsed"), '["my-tasks"]');
  // a reload reads it back
  assert.deepEqual(readCollapsed(createPrefs(st).recall(COLLAPSED_KEY, [])), ["my-tasks"]);
  list = toggleCollapsed(list, "my-tasks");
  p.remember(COLLAPSED_KEY, storedCollapsed(list));
  assert.equal(st.m.has("atlas.v2.dashboard.collapsed"), false);
});

test("a stored value that is not a list of ids reads as none collapsed", () => {
  assert.deepEqual(readCollapsed(null), []);
  assert.deepEqual(readCollapsed("my-tasks"), []);
  assert.deepEqual(readCollapsed(["a", 3, "", "a", "b"]), ["a", "b"]);
});

test("every box loads with the api it is handed; one failing does not stop the others", async () => {
  const api = { tag: "api" };
  /** @type {unknown[]} */
  const seen = [];
  const loaded = await loadBoxes(registry([
    box("ok", { load: (a) => { seen.push(a); return Promise.resolve([1, 2]); } }),
    box("throws", { load: () => { throw new Error("boom"); } }),
    box("rejects", { load: () => Promise.reject(new Error("403")) }),
  ]), api);
  assert.deepEqual(seen, [api]);
  assert.deepEqual(loaded.map((l) => [l.box.id, l.data ?? null, l.error ?? null]), [["ok", [1, 2], null], ["throws", null, "boom"], ["rejects", null, "403"]]);
});

test("a box whose drawn(data) is false is not drawn; a failed box is drawn with its error", () => {
  const boxes = registry([
    box("empty", { drawn: (d) => d.length > 0 }),
    box("full", { drawn: (d) => d.length > 0, count: (d) => d.length }),
    box("plain"),
    box("failed", { drawn: () => false }),
  ]);
  const plan = planBoxes([
    { box: boxes[0], data: [] },
    { box: boxes[1], data: [1, 2, 3] },
    { box: boxes[2], data: "x" },
    { box: boxes[3], error: "500" },
  ], ["plain"]);
  assert.deepEqual(plan.map((p) => [p.id, p.count, p.collapsed, p.error]), [
    ["full", 3, false, null],
    ["plain", null, true, null],
    ["failed", null, false, "500"],
  ]);
});

test("a count that is not a finite number, or throws, shows no count", () => {
  const boxes = registry([box("a", { count: () => "7" }), box("b", { count: () => { throw new Error("x"); } }), box("c", { count: () => 0 })]);
  const plan = planBoxes(boxes.map((b) => ({ box: b, data: {} })), []);
  assert.deepEqual(plan.map((p) => p.count), [null, null, 0]);
});

test("at most 3 × 3 boxes are drawn", () => {
  const boxes = registry(Array.from({ length: 12 }, (_, i) => box(`b${i}`)));
  assert.equal(planBoxes(boxes.map((b) => ({ box: b, data: 1 })), []).length, MAX_BOXES);
  assert.equal(MAX_BOXES, 9);
});
