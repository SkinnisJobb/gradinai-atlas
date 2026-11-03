import { test } from "node:test";
import assert from "node:assert/strict";
import { ROLES, rank, canGrant, grantableRoles, setBinding, describe, candidates, localMembers, groupable } from "./access-model.js";

test("roles are ordered, and anything else ranks below viewer", () => {
  assert.deepEqual(ROLES, ["viewer", "editor", "maintainer", "owner"]);
  assert.ok(rank("viewer") < rank("editor") && rank("editor") < rank("maintainer") && rank("maintainer") < rank("owner"));
  for (const bad of ["superuser", "", null, undefined, "Owner"]) assert.equal(rank(bad), 0, String(bad));
});

test("a maintainer grants up to maintainer, an owner grants owner, nobody else grants", () => {
  assert.deepEqual(grantableRoles("maintainer"), ["viewer", "editor", "maintainer"]);
  assert.deepEqual(grantableRoles("owner"), ROLES);
  assert.deepEqual(grantableRoles(null), []);
  assert.equal(canGrant("maintainer", "owner"), false);
  assert.equal(canGrant("editor", "viewer"), true);
});

test("setBinding replaces, adds and removes one principal, never mutating the input", () => {
  const start = [{ principal_kind: "user", principal_id: "u1", role: "viewer" }];
  const up = setBinding(start, "user", "u1", "editor");
  assert.deepEqual(up, [{ principal_kind: "user", principal_id: "u1", role: "editor" }]);
  assert.equal(start[0].role, "viewer");
  const two = setBinding(up, "group", "g1", "maintainer");
  assert.equal(two.length, 2);
  assert.deepEqual(setBinding(two, "user", "u1", null), [{ principal_kind: "group", principal_id: "g1", role: "maintainer" }]);
});

test("describe names the principal and locks what the caller cannot grant", () => {
  const accounts = [{ user_id: "u1", username: "ann", name: "Ann" }, { user_id: "b1", username: "bot-ann", is_service: true }];
  const groups = [{ id: "g1", name: "Team A" }];
  assert.deepEqual(describe({ principal_kind: "user", principal_id: "u1", role: "owner" }, accounts, groups, "maintainer"),
    { label: "Ann (ann)", locked: true });
  assert.equal(describe({ principal_kind: "user", principal_id: "b1", role: "viewer" }, accounts, groups, "owner").label, "bot-ann · agent");
  assert.equal(describe({ principal_kind: "group", principal_id: "g1", role: "editor" }, accounts, groups, "owner").label, "Team A · group");
  assert.equal(describe({ principal_kind: "group", principal_id: "gone", role: "editor" }, accounts, groups, "owner").label, "gone · group");
});

test("candidates leave out who is already bound and who is disabled", () => {
  const accounts = [{ user_id: "u1", username: "ann" }, { user_id: "u2", username: "ben", disabled: true },
    { user_id: "b1", username: "bot-x", is_service: true }, { user_id: "u3", username: "cid" }];
  const c = candidates([{ principal_kind: "user", principal_id: "u1", role: "viewer" }], accounts, [{ id: "g1", name: "T" }]);
  assert.deepEqual(c.map((x) => x.id), ["u3", "b1", "g1"]);
});

test("a group's editable members are its local ones, and only people are groupable", () => {
  assert.deepEqual(localMembers({ id: "g", name: "g", members: [{ user_id: "b", source: "local" }, { user_id: "a", source: "idp" }, { user_id: "a", source: "local" }] }), ["a", "b"]);
  assert.deepEqual(groupable([{ user_id: "1", username: "z" }, { user_id: "2", username: "bot", is_service: true },
    { user_id: "3", username: "a" }, { user_id: "4", username: "off", disabled: true }]).map((a) => a.username), ["a", "z"]);
});
