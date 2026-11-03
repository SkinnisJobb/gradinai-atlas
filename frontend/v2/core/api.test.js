import { test } from "node:test";
import assert from "node:assert/strict";
import { safeLocalPath, authDestination } from "./api.js";

test("a redirect target from a response body is used only if it is a local path", () => {
  assert.equal(safeLocalPath("/auth/password/change", "/x"), "/auth/password/change");
  for (const bad of ["https://evil.example/", "//evil.example/", "/\\evil.example", "javascript:alert(1)", 42, null]) {
    assert.equal(safeLocalPath(bad, "/fallback"), "/fallback", String(bad));
  }
});

test("an expired session goes to login and comes back to this page", () => {
  assert.equal(authDestination(null, "/board/v2?node=a b"), "/auth/login?next=%2Fboard%2Fv2%3Fnode%3Da%20b");
});

test("a forced password change goes to the change form, never off-site", () => {
  assert.equal(authDestination({ error: "password_change_required" }, "/board/v2"), "/auth/password/change");
  assert.equal(authDestination({ change_password: "/auth/password/change?x=1" }, "/"), "/auth/password/change?x=1");
  assert.equal(authDestination({ change_password: "https://evil.example" }, "/"), "/auth/password/change");
});
