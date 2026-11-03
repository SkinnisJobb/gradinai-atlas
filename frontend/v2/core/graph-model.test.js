import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildIndex, workStatus, boardColumn, STATUS_EQUIVALENT, BOARD_COLUMNS, cmpNodes, stepSearch, pruneTypes, ancestry, boundNode, parentChoices, epicOf, storyOf, worldDesc, scopeItems, workCounts, isHub, HUB_DEGREE,
} from "./graph-model.js";

// An invented world, built to contain each case once. No real node ids.
//   proj ─contains─ comp ─contains─ cap
//    │                 └─ hosted_on ─ host (a hub once it has enough cross-links)
//   other (a second root)
//   epic ─works_on─ comp ; epic ─contains─ story ─contains─ task1, task2
//   lone ─works_on─ cap    (a task bound directly to a deeper node)
//   done1 (done, recently) and old (done, long ago) and gone (cancelled), all on comp
const N = (id, type, extra = {}) => ({ id, type, name: id, ...extra });
const E = (src, dst, edge_type) => ({ src, dst, edge_type });
function world(extraEdges = [], extraNodes = []) {
  return buildIndex({
    nodes: [
      N("proj", "project"), N("comp", "component"), N("cap", "capability"), N("host", "host"), N("other", "project"),
      N("epic", "epic", { status: "in_progress" }), N("story", "story", { status: "pending" }),
      N("task1", "task", { status: "blocked", assignee: { username: "me" } }), N("task2", "task", { status: "pending" }),
      N("lone", "task", { status: "in_progress" }),
      N("done1", "task", { status: "done", completed_at: "2026-10-20T00:00:00Z" }),
      N("old", "task", { status: "done", completed_at: "2026-01-01T00:00:00Z" }),
      N("gone", "task", { status: "cancelled" }),
      ...extraNodes,
    ],
    edges: [
      E("proj", "comp", "contains"), E("comp", "cap", "contains"), E("comp", "host", "hosted_on"),
      E("epic", "comp", "works_on"), E("epic", "story", "contains"), E("story", "task1", "contains"), E("story", "task2", "contains"),
      E("lone", "cap", "works_on"), E("done1", "comp", "works_on"), E("old", "comp", "works_on"), E("gone", "comp", "works_on"),
      ...extraEdges,
    ],
  });
}
const NOW = new Date("2026-10-22T12:00:00Z");

test("a short_name attribute is carried as the label override", () => {
  const ix = buildIndex({ nodes: [{ id: "x", type: "component", name: "x", attributes: { short_name: " Xs " } }, { id: "y", type: "component", name: "y" }], edges: [] });
  assert.equal(ix.byId.get("x").override, "Xs");
  assert.equal(ix.byId.get("y").override, null);
});

test("roots are the world nodes nothing contains; work is never a root", () => {
  assert.deepEqual(world().roots, ["host", "other", "proj"]);
});

test("an edge to a node we were not given is dropped, never kept as a stub", () => {
  const ix = world([E("comp", "secret-node", "uses")]);
  assert.ok(!ix.adj.get("comp").some((x) => x.o === "secret-node"));
  assert.equal(ix.byId.has("secret-node"), false);
  assert.ok(![...stepSearch(ix, "comp", 4, { links: "all" }).keys()].includes("secret-node"));
});

test("structure steps follow contains only, and record how each node was reached", () => {
  const s = stepSearch(world(), "proj", 4);
  assert.deepEqual([...s.keys()], ["proj", "comp", "cap"]);
  assert.deepEqual(s.get("cap"), { depth: 2, parent: "comp", via: "contains" });
});

test("all links reaches across, and k bounds the depth", () => {
  const s = stepSearch(world(), "proj", 2, { links: "all" });
  assert.ok(s.has("host"));
  assert.equal(stepSearch(world(), "proj", 1, { links: "all" }).has("cap"), false);
});

test("open work hangs off its node only when asked for, and closed work never does", () => {
  const ix = world();
  assert.equal(stepSearch(ix, "comp", 3).has("epic"), false);
  const s = stepSearch(ix, "comp", 3, { work: true });
  assert.ok(s.has("epic") && s.has("story") && s.has("task1"));
  assert.equal(s.get("epic").via, "works_on");
  for (const closed of ["done1", "old", "gone"]) assert.equal(s.has(closed), false, closed);
});

test("a hub is reached but not stepped through, only in all-links mode", () => {
  const spokes = Array.from({ length: HUB_DEGREE + 1 }, (_, i) => N("svc" + i, "service"));
  const ix = world(spokes.map((n) => E(n.id, "host", "hosted_on")), spokes);
  assert.ok(isHub(ix, "host", "proj", { links: "all" }));
  const s = stepSearch(ix, "comp", 3, { links: "all" });
  assert.ok(s.has("host"));
  assert.equal(s.has("svc0"), false, "nothing is reached THROUGH the hub");
  assert.ok(stepSearch(ix, "host", 1, { links: "all" }).has("svc0"), "a hub in focus is stepped through");
});

test("the same inputs give the same tree", () => {
  const a = [...stepSearch(world(), "proj", 4, { links: "all", work: true }).entries()];
  const b = [...stepSearch(world(), "proj", 4, { links: "all", work: true }).entries()];
  assert.deepEqual(a, b);
});

test("ancestry walks up the containers, outermost first", () => {
  assert.deepEqual(ancestry(world(), "cap"), ["proj", "comp"]);
  assert.deepEqual(ancestry(world(), "proj"), []);
});

test("a task with no binding of its own belongs to its epic's node", () => {
  const ix = world();
  assert.equal(boundNode(ix, "task1"), "comp");
  assert.equal(boundNode(ix, "lone"), "cap");
  assert.equal(epicOf(ix, "task1"), "epic");
  assert.equal(storyOf(ix, "task1"), "story");
  assert.equal(storyOf(ix, "story"), null);
});

test("work rolls up, never down", () => {
  const ix = world();
  const opts = { doneDays: 30, now: NOW };
  assert.ok(scopeItems(ix, "proj", opts).includes("lone"), "work on cap shows on proj, which contains it");
  assert.equal(scopeItems(ix, "cap", opts).includes("epic"), false, "work on comp does not show on cap, inside it");
  assert.deepEqual([...worldDesc(ix, "comp")].sort(), ["cap", "comp"]);
});

test("the board hides cancelled work, and done work outside the window", () => {
  const ix = world();
  const ids = scopeItems(ix, "comp", { doneDays: 30, now: NOW });
  assert.ok(ids.includes("done1"));
  assert.equal(ids.includes("old"), false);
  assert.equal(ids.includes("gone"), false);
  assert.equal(scopeItems(ix, "comp", { doneDays: 0, now: NOW }).includes("done1"), false);
});

test("Mine is the signed-in account, and Unassigned is nobody", () => {
  const ix = world();
  assert.deepEqual(scopeItems(ix, "comp", { who: "mine", me: "me", now: NOW }), ["task1"]);
  assert.deepEqual(scopeItems(ix, "comp", { who: "mine", now: NOW }), [], "no signed-in name means nothing is mine");
  assert.equal(scopeItems(ix, "comp", { who: "unassigned", now: NOW }).includes("task1"), false);
});

test("a workitem's scope is itself and what it contains; search matches name or id", () => {
  const ix = world();
  assert.deepEqual(scopeItems(ix, "story", { now: NOW }).sort(), ["story", "task1", "task2"]);
  assert.deepEqual(scopeItems(ix, "comp", { q: "TASK2", now: NOW }), ["task2"]);
});

test("workCounts rolls up and never counts cancelled work", () => {
  const c = workCounts(world(), "proj");
  assert.equal(c.count, 7);           // epic, story, task1, task2, lone, done1, old (not gone)
  assert.equal(c.done, 2);
  assert.equal(c.blocked, 1);
  assert.equal(c.active, 2);          // epic, lone
  assert.equal(c.open.length, 5);
  assert.equal(workCounts(world(), "story").count, 2);
});

test("the type filter hides a type but keeps what leads to a kept node, and the focus", () => {
  const ix = world();
  const seen = stepSearch(ix, "proj", 3, { links: "all" });
  const ids = (m) => [...m.keys()].sort();
  assert.deepEqual(ids(pruneTypes(ix, seen, new Set())), ids(seen), "nothing hidden: unchanged");
  const noHost = pruneTypes(ix, seen, new Set(["host"]));
  assert.ok(!noHost.has("host") && noHost.has("cap"));
  const noComp = pruneTypes(ix, seen, new Set(["component"]));
  assert.ok(noComp.has("comp"), "comp stays: it leads to cap and host");
  const onlyProj = pruneTypes(ix, seen, new Set(["component", "capability", "host"]));
  assert.deepEqual(ids(onlyProj), ["proj"]);
  assert.ok(pruneTypes(ix, seen, new Set(["project"])).has("proj"), "the focus always stays");
});

test("priority and labels are carried from the API, and absent ones read as none", () => {
  const ix = world([], [N("tagged", "task", { priority: "high", labels: ["backend", "auth"] })]);
  assert.equal(ix.byId.get("tagged").priority, "high");
  assert.deepEqual(ix.byId.get("tagged").labels, ["backend", "auth"]);
  assert.equal(ix.byId.get("task1").priority, null, "a node written before the field reads as unset");
  assert.deepEqual(ix.byId.get("task1").labels, []);
});

test("a workitem with no status reads as pending, so the board draws it (TASK-1368)", () => {
  const ix = buildIndex({
    nodes: [N("comp", "component"), N("nostat", "task", { status: null }), N("absent", "story"),
      N("empty", "task", { status: "" }), N("world", "component", { status: null })],
    edges: [E("nostat", "comp", "works_on"), E("absent", "comp", "works_on"), E("empty", "comp", "works_on")],
  });
  for (const id of ["nostat", "absent", "empty"]) assert.equal(ix.byId.get(id).status, "pending", id);
  assert.equal(ix.byId.get("world").status, "", "a world node is not given a work status");
  assert.deepEqual(scopeItems(ix, "comp", { now: NOW }).sort(), ["absent", "empty", "nostat"],
    "and the board's scope keeps them, as open work");
});

test("parentChoices: a workitem moves only under one ranked above it, never under itself or below (TASK-1355)", () => {
  const ix = world([E("story", "epic2", "contains")], [N("epic2", "epic"), N("story2", "story")]);
  assert.deepEqual(parentChoices(ix, "task1"), ["epic", "epic2", "story", "story2"].sort(cmpNodes(ix)));
  assert.deepEqual(parentChoices(ix, "story"), ["epic"], "not epic2, which the story contains here");
  assert.deepEqual(parentChoices(ix, "epic"), [], "an epic has no parent to move to");
  assert.deepEqual(parentChoices(ix, "comp"), [], "a world node is not work");
});

test("the types and the ladder come from the vocabulary passed in, not from graph-model (TASK-1391)", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("./graph-model.js", import.meta.url), "utf8");
  assert.doesNotMatch(src, /\[\s*"epic"\s*,\s*"story"\s*,\s*"task"\s*\]/, "no hard-coded type list");
  assert.doesNotMatch(src, /epic:\s*0/, "no hard-coded rank");
  const vocab = { workitem_types: ["epic", "story", "task", "decision"], workitem_ladder: ["epic", "story", "task"] };
  const nodes = [N("comp", "component"), N("epic", "epic"), N("story", "story"), N("task", "task"), N("dec", "decision", { status: "proposed" })];
  const edges = [E("epic", "story", "contains"), E("story", "task", "contains"), E("dec", "comp", "works_on")];
  const ix = buildIndex({ nodes, edges }, vocab);
  assert.equal(ix.byId.get("dec").isWork, true, "a type outside the ladder is still work");
  assert.equal(ix.byId.get("dec").status, "proposed", "and gets a work status");
  assert.deepEqual(scopeItems(ix, "comp"), ["dec"], "and is on its node's board");
  assert.deepEqual(parentChoices(ix, "dec"), [], "but is offered no parent rung");
  assert.ok(!parentChoices(ix, "task").includes("dec"), "and is never offered as a parent");
  // The move picker offers what it offered before, for each rung.
  const before = buildIndex({ nodes, edges });
  assert.deepEqual(parentChoices(ix, "task"), ["epic", "story"].sort(cmpNodes(ix)));
  assert.deepEqual(parentChoices(ix, "story"), ["epic"]);
  assert.deepEqual(parentChoices(ix, "epic"), []);
  for (const id of ["epic", "story", "task"]) assert.deepEqual(parentChoices(ix, id), parentChoices(before, id), id);
  assert.equal(before.byId.get("dec").isWork, false, "the fallback vocabulary has no decision type");
});

test("the move picker ranks by the ladder it is given, whatever its types are called (TASK-1391)", () => {
  const ix = buildIndex({ nodes: [N("a", "initiative"), N("b", "epic"), N("c", "task")], edges: [] },
    { workitem_types: ["initiative", "epic", "task"], workitem_ladder: ["initiative", "epic", "task"] });
  assert.deepEqual(parentChoices(ix, "c"), ["a", "b"].sort(cmpNodes(ix)));
  assert.deepEqual(parentChoices(ix, "b"), ["a"]);
  assert.deepEqual(parentChoices(ix, "a"), []);
});

test("a synonym status is drawn in the column it means, never dropped (TASK-1379)", () => {
  const syn = ["analyze", "planned", "todo", "backlog", "not_started", "proposed", "wip", "accepted", "closed",
    "In Progress", "not-started", "review"];
  const ix = buildIndex({
    nodes: [N("comp", "component"), ...syn.map((s, i) => N("w" + i, "task", { status: s, completed_at: "2026-10-20T00:00:00Z" }))],
    edges: syn.map((_s, i) => E("w" + i, "comp", "works_on")),
  });
  const col = (i) => boardColumn(ix.byId.get("w" + i).status);
  assert.deepEqual(syn.map((_s, i) => col(i)), [
    "in_progress", "pending", "pending", "pending", "pending", "proposed", "in_progress", "done", "done",
    "in_progress", "pending", "pending",
  ]);
  for (let i = 0; i < syn.length; i++) assert.ok(BOARD_COLUMNS.includes(col(i)), syn[i] + " lands in a column");
  assert.equal(ix.byId.get("w8").status, "done", "closed counts as done");
  assert.equal(scopeItems(ix, "comp", { now: NOW, doneDays: 30 }).length, syn.length, "every one is on the board");
  assert.equal(scopeItems(ix, "comp", { now: NOW, doneDays: 0 }).length, syn.length - 2,
    "accepted and closed obey the done window, like done");
  assert.equal(workCounts(ix, "comp").done, 2);
  assert.equal(boardColumn("cancelled"), null, "cancelled is never drawn");
  assert.equal(workStatus(null), "pending");
  assert.equal(workStatus("analyze"), "analyze", "a settable status is kept as it is");
});

test("STATUS_EQUIVALENT mirrors backend/attention.py exactly (TASK-1379)", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../../../backend/attention.py", import.meta.url), "utf8");
  const m = src.match(/^STATUS_EQUIVALENT = \{([\s\S]*?)\n\}/m);
  assert.ok(m, "attention.py still declares STATUS_EQUIVALENT");
  const backend = Object.fromEntries([...m[1].matchAll(/"(\w+)":\s*"(\w+)"/g)].map((x) => [x[1], x[2]]));
  assert.deepEqual({ ...STATUS_EQUIVALENT }, backend);
});

// TASK-1472: /api/graph?unbound_work=1 now delivers work connected to nothing.
// The index must put it on the unscoped board and in its text filter, and never
// make it a root of the world graph.
test("unbound, parentless work is on the unscoped board and is no graph root (TASK-1472)", () => {
  const ix = world([E("loose-epic", "loose-kid", "contains")], [
    N("loose", "story", { status: "pending" }), N("loose-epic", "epic"), N("loose-kid", "task", { status: "pending" }),
  ]);
  const all = scopeItems(ix, null, { now: NOW });
  for (const id of ["loose", "loose-epic", "loose-kid"]) assert.ok(all.includes(id), `${id} is on the unscoped board`);
  assert.deepEqual(scopeItems(ix, null, { q: "loose-k", now: NOW }), ["loose-kid"], "the board's text filter finds it");
  assert.equal(boundNode(ix, "loose"), null);
  assert.equal(scopeItems(ix, "proj", { now: NOW }).includes("loose"), false, "it is in no world node's scope");
  assert.equal(ix.byId.get("loose-epic").status, "pending", "a missing status is drawn as pending");
  assert.equal(ix.roots.some((r) => r.startsWith("loose")), false, "it is not a root of the world graph");
});
