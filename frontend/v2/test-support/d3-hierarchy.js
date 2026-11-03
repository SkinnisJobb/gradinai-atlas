// test-support/d3-hierarchy.js — the vendored d3, loaded for node --test.
//
// The browser gets d3 as a classic script (`/assets/d3.min.js`, a global).
// Layout code takes it as a parameter rather than reading the global, and the
// tests pass in this copy, so they lay out with exactly the same d3. Nothing
// under test-support/ is served.
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const d3 = require("../../vendor/d3.min.js");

export const hierarchy = d3.hierarchy;
export const tree = d3.tree;
