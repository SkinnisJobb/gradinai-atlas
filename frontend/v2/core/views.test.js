import { test } from "node:test";
import assert from "node:assert/strict";
import { snapshotView, viewMatches, viewPatch, saveView, deleteView, readViews, nextViewName } from "./views.js";

const STATE = { focus: "comp", steps: 1, links: "contains", work: false, hideTypes: ["host"], collapsed: ["b", "a", "a"], selected: "x", hover: "y" };

test("a view captures the graph settings and nothing else, lists sorted and unique", () => {
  const v = snapshotView(STATE, "  Infra  ");
  assert.deepEqual(v, { name: "Infra", focus: "comp", steps: 1, links: "contains", work: false, hideTypes: ["host"], collapsed: ["a", "b"] });
});

test("a view matches the state it was taken from, and not after a collapse changes", () => {
  const v = snapshotView(STATE, "Infra");
  assert.ok(viewMatches(v, { ...STATE, collapsed: ["a", "b"] }));
  assert.ok(!viewMatches(v, { ...STATE, collapsed: ["a"] }));
  assert.ok(!viewMatches(v, { ...STATE, focus: null }));
});

test("applying a view sets focus, selection and scope together, and drops a focus that is gone", () => {
  const v = snapshotView(STATE, "Infra");
  const p = viewPatch(v, () => true);
  assert.equal(p.focus, "comp"); assert.equal(p.selected, "comp"); assert.equal(p.scope, "comp");
  assert.deepEqual(p.collapsed, ["a", "b"]);
  assert.equal(p.path, null);
  assert.equal(viewPatch(v, () => false).focus, null);
});

test("saving replaces a view of the same name, ignoring case, else appends", () => {
  let list = saveView([], snapshotView(STATE, "Infra"));
  list = saveView(list, snapshotView({ ...STATE, steps: 3 }, "Other"));
  list = saveView(list, snapshotView({ ...STATE, steps: 4 }, "infra"));
  assert.deepEqual(list.map((v) => [v.name, v.steps]), [["infra", 4], ["Other", 3]]);
  assert.deepEqual(saveView(list, snapshotView(STATE, "  ")), list);
});

test("deleting removes by name; unreadable stored entries are dropped", () => {
  const list = saveView(saveView([], snapshotView(STATE, "A")), snapshotView(STATE, "B"));
  assert.deepEqual(deleteView(list, "A").map((v) => v.name), ["B"]);
  assert.deepEqual(readViews([null, 3, { name: "" }, { name: "Old", steps: 2 }]).map((v) => v.name), ["Old"]);
  assert.deepEqual(readViews("nonsense"), []);
});

test("a new view gets the first free default name", () => {
  assert.equal(nextViewName([]), "View 1");
  assert.equal(nextViewName([{ name: "View 1" }, { name: "view 2" }]), "View 3");
});
