import { test } from "node:test";
import assert from "node:assert/strict";
import box, { taskRows, model, effectivePriority, LOAD_URL, VISIBLE_ROWS } from "./my-tasks.js";

const item = (id, status, extra = {}) => ({ id, name: `${id} name`, type: "task", status, priority: null, ...extra });

test("in_progress first, then critical, high, medium, low, none, then by id", () => {
  const rows = taskRows([
    item("TASK-9", "pending"),
    item("TASK-10", "pending", { priority: "low" }),
    item("TASK-2", "pending", { priority: "high" }),
    item("TASK-3", "blocked", { priority: "critical" }),
    item("TASK-4", "in_progress"),
    item("TASK-1", "in_progress", { priority: "medium" }),
    item("TASK-8", "pending"),
  ]);
  assert.deepEqual(rows.map((r) => r.id), ["TASK-1", "TASK-4", "TASK-3", "TASK-2", "TASK-10", "TASK-8", "TASK-9"]);
});

test("a CRITICAL: name prefix counts as critical", () => {
  assert.equal(effectivePriority({ name: "CRITICAL: the db is full", priority: null }), "critical");
  assert.equal(effectivePriority({ name: "CRITICAL: x", priority: "low" }), "critical");
  assert.equal(effectivePriority({ name: "not critical: x", priority: "low" }), "low");
  const rows = taskRows([item("TASK-1", "pending", { priority: "high" }), item("TASK-2", "pending", { name: "CRITICAL: fix" })]);
  assert.deepEqual(rows.map((r) => [r.id, r.priority]), [["TASK-2", "critical"], ["TASK-1", "high"]]);
});

test("done, cancelled and decisions are not listed", () => {
  const rows = taskRows([
    item("TASK-1", "done"), item("TASK-2", "cancelled"),
    item("DEC-1", "pending", { type: "decision" }), item("TASK-3", "pending"),
  ]);
  assert.deepEqual(rows.map((r) => r.id), ["TASK-3"]);
});

test("a row is id, name, status and priority", () => {
  assert.deepEqual(taskRows([item("STORY-x", "in_progress", { priority: "high", body: "prose" })]),
    [{ id: "STORY-x", name: "STORY-x name", status: "in_progress", priority: "high" }]);
});

test("the count is the API total less what the page dropped", () => {
  assert.equal(model({ items: [item("TASK-1", "pending"), item("D", "pending", { type: "decision" })], total: 2 }).count, 1);
  assert.equal(model({ items: [item("TASK-1", "pending")], total: 250 }).count, 250);
  assert.equal(model({ items: [], total: 0 }).count, 0);
  assert.deepEqual(model(/** @type {any} */ (null)), { rows: [], count: 0 });
});

test("load asks for my open items only, and the box keeps the contract", async () => {
  assert.match(LOAD_URL, /^\/api\/workitems\?assignee=me&/);
  assert.doesNotMatch(LOAD_URL, /done|cancelled|include_prose/);
  let asked = "";
  const m = await box.load({ fetchJSON: async (u) => { asked = u; return { items: [item("TASK-1", "pending")], total: 1 }; } });
  assert.equal(asked, LOAD_URL);
  assert.equal(box.count(m), 1);
  assert.equal(box.id, "my-tasks");
  assert.equal(box.title, "My tasks");
  assert.equal(VISIBLE_ROWS, 7);
});

test("a status synonym reads as the board draws it", () => {
  const rows = taskRows([item("TASK-1", "planned"), item("TASK-2", "wip"), item("TASK-3", "closed"), item("TASK-4", null)]);
  assert.deepEqual(rows.map((r) => [r.id, r.status]), [["TASK-2", "in_progress"], ["TASK-1", "pending"], ["TASK-4", "pending"]]);
});
