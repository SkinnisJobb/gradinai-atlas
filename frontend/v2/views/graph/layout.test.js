import { test } from "node:test";
import assert from "node:assert/strict";
import { hierarchy, tree } from "../../test-support/d3-hierarchy.js";
import { buildIndex, stepSearch } from "../../core/graph-model.js";
import { radialTree, overview, pathLayout, fitTransform, GAP, MAX_STEPS } from "./layout.js";

const d3 = { hierarchy, tree };
const N = (id, type = "component") => ({ id, type, name: id });
const C = (src, dst) => ({ src, dst, edge_type: "contains" });
function fan() {
  // root -> a, b, c ; a -> a1, a2 ; b -> b1 ; a1 -> a1x
  return buildIndex({
    nodes: [N("root", "project"), ...["a", "b", "c", "a1", "a2", "b1", "a1x"].map((i) => N(i))],
    edges: [C("root", "a"), C("root", "b"), C("root", "c"), C("a", "a1"), C("a", "a2"), C("b", "b1"), C("a1", "a1x")],
  });
}
const place = (ix, focus) => radialTree(ix, stepSearch(ix, focus, MAX_STEPS), focus, { d3 });

test("the focus sits at the centre and each step is a ring further out", () => {
  const { all, radii } = place(fan(), "root");
  const at = Object.fromEntries(all.map((p) => [p.id, p]));
  assert.deepEqual([at.root.x, at.root.y], [0, 0]);
  for (const p of all) assert.ok(Math.abs(Math.hypot(p.x, p.y) - radii[p.depth]) < 1e-6, p.id);
  for (let d = 1; d < radii.length; d++) assert.ok(radii[d] - radii[d - 1] >= GAP - 1e-6);
});

test("a child sits in its parent's sector: siblings' subtrees do not interleave", () => {
  const { all } = place(fan(), "root");
  const at = Object.fromEntries(all.map((p) => [p.id, p.a]));
  // a's descendants all lie on the same side of b's as a does of b
  for (const k of ["a1", "a2", "a1x"]) assert.ok((at[k] < at.b) === (at.a < at.b), k);
});

test("positions do not depend on how many rings are shown", () => {
  // layout is computed once at MAX_STEPS; showing fewer steps just filters it
  const once = place(fan(), "root").all;
  const again = place(fan(), "root").all;
  assert.deepEqual(once, again);
  const shownAt2 = once.filter((p) => p.depth <= 2).map((p) => [p.id, p.x, p.y]);
  const fromFull = once.filter((p) => p.depth <= 2).map((p) => [p.id, p.x, p.y]);
  assert.deepEqual(shownAt2, fromFull);
});

test("the opening view puts roots on one ring and stars outside it", () => {
  const { all } = overview(["r1", "r2", "r3"], ["s1"]);
  const rr = (id) => Math.hypot(all.find((p) => p.id === id).x, all.find((p) => p.id === id).y);
  assert.ok(Math.abs(rr("r1") - rr("r2")) < 1e-6);
  assert.ok(rr("s1") > rr("r1"));
});

test("fit puts everything inside the pane", () => {
  const nodes = place(fan(), "root").all;
  for (const [w, h] of [[400, 300], [1700, 1300], [3000, 600]]) {
    const t = fitTransform(nodes, w, h);
    for (const n of nodes) {
      const sx = n.x * t.k + t.x, sy = n.y * t.k + t.y;
      assert.ok(sx >= 0 && sx <= w && sy >= 0 && sy <= h, `${n.id} at ${sx},${sy} in ${w}x${h}`);
    }
  }
});

test("the path view puts from left, to right, one row per route, shared nodes once", () => {
  const H = (from, to, edge, t = "contains", direction = "out") => ({ from, to, edge, edge_type: t, direction });
  const res = { from: "a", to: "d", paths: [
    { length: 2, hops: [H("a", "b", 1), H("b", "d", 2)] },
    { length: 3, hops: [H("a", "b", 1), H("b", "c", 3, "uses"), H("c", "d", 4, "uses", "in")] },
  ] };
  const P = pathLayout(res), at = new Map(P.all.map((p) => [p.id, p]));
  assert.deepEqual([...at.keys()].sort(), ["a", "b", "c", "d"], "b is on both routes and drawn once");
  assert.ok(at.get("a").x < 0 && at.get("d").x > 0 && at.get("a").y === 0 && at.get("d").y === 0);
  assert.ok(at.get("b").y < at.get("c").y, "the second route is its own row");
  assert.equal(P.links.length, 5, "every hop of every route is a link");
  assert.equal(P.links.at(-1).dir, "in");
  assert.deepEqual(pathLayout({ from: "a", to: "a", paths: [{ length: 0, hops: [] }] }).all.map((p) => p.id), ["a"]);
});

test("with a one-step route, no longer route is drawn over its straight line", () => {
  const H = (from, to, edge) => ({ from, to, edge, edge_type: "contains", direction: "out" });
  const P = pathLayout({ from: "a", to: "z", paths: [
    { length: 1, hops: [H("a", "z", 1)] }, { length: 2, hops: [H("a", "m", 2), H("m", "z", 3)] }, { length: 2, hops: [H("a", "n", 4), H("n", "z", 5)] },
  ] });
  const at = new Map(P.all.map((p) => [p.id, p]));
  assert.ok(at.get("m").y !== 0 && at.get("n").y !== 0 && Math.sign(at.get("m").y) !== Math.sign(at.get("n").y));
});
