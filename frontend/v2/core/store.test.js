import { test } from "node:test";
import assert from "node:assert/strict";
import { createStore, INITIAL } from "./store.js";
import { FALLBACK_VOCABULARY } from "./vocabulary.js";

test("the vocabulary starts as the fallback, so the board works before /api/vocabulary answers (TASK-1391)", () => {
  assert.equal(createStore({ ...INITIAL }).get().vocab, FALLBACK_VOCABULARY);
});

test("set merges, returns what changed, and freezes the new state", () => {
  const s = createStore({ ...INITIAL });
  const before = s.get();
  assert.deepEqual(s.set({ focus: "atlas", steps: 2 }), ["focus"]);
  assert.equal(s.get().focus, "atlas");
  assert.equal(before.focus, null, "the old state object is not mutated");
  assert.ok(Object.isFrozen(s.get()));
});

test("a redundant set wakes nobody", () => {
  const s = createStore({ focus: "a" });
  let calls = 0;
  s.subscribe(() => calls++);
  assert.deepEqual(s.set({ focus: "a" }), []);
  assert.equal(calls, 0);
});

test("a keyed subscriber hears only its keys, and is told which changed", () => {
  const s = createStore({ focus: null, hover: null });
  const heard = [];
  s.subscribe(["focus"], (_st, changed) => heard.push(changed));
  s.set({ hover: "x" });
  s.set({ focus: "y", hover: "z" });
  assert.deepEqual(heard, [["focus", "hover"]]);
});

test("unsubscribing during a notification does not skip or break the others", () => {
  const s = createStore({ n: 0 });
  const order = [];
  let offB;
  s.subscribe(() => { order.push("a"); offB(); });
  offB = s.subscribe(() => order.push("b"));
  s.subscribe(() => order.push("c"));
  s.set({ n: 1 });
  assert.deepEqual(order, ["a", "c"], "b was removed by a before its turn");
  s.set({ n: 2 });
  assert.deepEqual(order, ["a", "c", "a", "c"]);
});
