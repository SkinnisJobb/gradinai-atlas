import { test } from "node:test";
import assert from "node:assert/strict";
import { buildIndex } from "../core/graph-model.js";
import { findNodes } from "./search.js";

const ix = buildIndex({
  nodes: [
    { id: "board-map", type: "capability", name: "Board map" },
    { id: "map-tiles", type: "component", name: "Map tiles" },
    { id: "TASK-12", type: "task", name: "Fix the map legend" },
    { id: "atlas", type: "project", name: "Atlas" },
  ],
  edges: [],
});

test("an exact id comes first, then prefixes, then anything containing the query", () => {
  assert.deepEqual(findNodes(ix, "map"), ["map-tiles", "board-map", "TASK-12"]);
  assert.deepEqual(findNodes(ix, "task-12"), ["TASK-12"]);
});

test("blank finds nothing, and the search only ever returns loaded nodes", () => {
  assert.deepEqual(findNodes(ix, "   "), []);
  assert.deepEqual(findNodes(ix, "secret"), []);
});
