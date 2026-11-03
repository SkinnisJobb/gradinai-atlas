import { test } from "node:test";
import assert from "node:assert/strict";
import { shortLabel, uniqueShorts } from "./shortlabel.js";

test("a task is its number", () => {
  assert.equal(shortLabel({ id: "TASK-1287", name: "Graph: roots", isWork: true }), "#1287");
});

test("a project-keyed task keeps its whole id, even reached from a node sharing its prefix", () => {
  assert.equal(shortLabel({ id: "ABC-12", name: "Fix the thing", isWork: true }), "ABC-12");
  assert.equal(shortLabel({ id: "OPS2-7", name: "Fix the thing", isWork: true }, "OPS2-3"), "OPS2-7");
  assert.equal(shortLabel({ id: "TASK-12", name: "Fix the thing", isWork: true }), "#12", "TASK- unchanged");
});

test("an identifier drops the prefix it shares with the node it was reached from", () => {
  assert.equal(shortLabel({ id: "atlas-backend", name: "atlas-backend" }, "atlas"), "backend");
  assert.equal(shortLabel({ id: "atlas-backend", name: "atlas-backend" }), "atlas-backend");
  assert.equal(shortLabel({ id: "atlas", name: "atlas" }, "atlas"), "atlas", "never strips to nothing");
});

test("an epic or story uses its id without the EPIC-/STORY- prefix", () => {
  assert.equal(shortLabel({ id: "EPIC-atlas-work-board", name: "Board v2: the work board", isWork: true }, "atlas"), "work-board");
});

test("a prose name keeps its first significant words, case untouched", () => {
  assert.equal(shortLabel({ id: "market-experiments", name: "Market experiments" }), "Market");
  assert.equal(shortLabel({ id: "ai-dev-seat", name: "AI Developer Seat" }), "AI Developer");
  assert.equal(shortLabel({ id: "x", name: "The Model & memory platform" }), "Model memory");
});

test("long results are clipped, and an override wins", () => {
  assert.equal(shortLabel({ id: "a-very-long-identifier-name", name: "a-very-long-identifier-name" }).length, 14);
  assert.equal(shortLabel({ id: "x", name: "Something", override: "Sx" }), "Sx");
});

test("labels on screen are unique", () => {
  const m = uniqueShorts([
    { id: "a-api", name: "a-api", parent: "a" }, { id: "b-api", name: "b-api", parent: "b" }, { id: "c", name: "c" },
  ]);
  assert.deepEqual([...m.values()], ["api", "api·2", "c"]);
});
