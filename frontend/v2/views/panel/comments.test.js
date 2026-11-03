// @ts-check
import { test } from "node:test";
import assert from "node:assert/strict";
import { authorText, mayChange } from "./comments.js";

const mine = { author: { id: "u1", username: "alex", name: "Alex" } };

test("the author line prefers the name, then the username", () => {
  assert.equal(authorText(mine), "Alex");
  assert.equal(authorText({ author: { username: "agent-x" } }), "agent-x");
  assert.equal(authorText({ author: {} }), "an account that no longer exists");
});

test("only the author edits; the author or an owner deletes", () => {
  assert.deepEqual(mayChange(mine, "alex", "member"), { edit: true, del: true });
  assert.deepEqual(mayChange(mine, "sam", "member"), { edit: false, del: false });
  assert.deepEqual(mayChange(mine, "sam", "owner"), { edit: false, del: true });
  assert.deepEqual(mayChange(mine, null, null), { edit: false, del: false });
});
