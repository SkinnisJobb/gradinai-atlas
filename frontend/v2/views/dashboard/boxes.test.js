import { test } from "node:test";
import assert from "node:assert/strict";
import { registry, MAX_BOXES } from "../../core/dashboard.js";
import { BOXES } from "./boxes.js";

test("every registered box keeps the contract, no id twice, and they fit the 3 x 3 grid", () => {
  assert.doesNotThrow(() => registry(BOXES));
  assert.ok(BOXES.length <= MAX_BOXES);
});
