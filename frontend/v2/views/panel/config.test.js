import { test } from "node:test";
import assert from "node:assert/strict";
import { partOf, valueText, sourceText, writesOn, editState, inputKind } from "./config.js";

test("phase 2: nothing is editable unless the server says writes are enabled", () => {
  const s = { name: "ATLAS_STRICT_API", kind: "plain", secret: false, changeable: true, source: "default", value: false };
  assert.equal(writesOn({ read_only: true, parts: [] }), false);
  assert.equal(writesOn(null), false);
  assert.equal(editState({ read_only: true, parts: [] }, s), null);
  const on = { read_only: false, writes: { enabled: true }, parts: [] };
  assert.equal(writesOn(on), true);
  assert.equal(editState(on, s), "edit");
  assert.equal(editState(on, { ...s, source: "board" }), "edit");
  assert.equal(editState(on, { ...s, source: "env" }), "env");
  assert.equal(editState(on, { ...s, changeable: false }), null);
  assert.equal(editState(on, { ...s, secret: true }), null);
  assert.equal(editState(on, { ...s, kind: "bootstrap" }), null);
});

test("phase 2: a boolean is chosen on/off, anything else typed; board reads as board", () => {
  assert.equal(inputKind({ value: true }), "bool");
  assert.equal(inputKind({ value: 30 }), "text");
  assert.equal(inputKind({ value: null }), "text");
  assert.equal(sourceText({ source: "board" }), "set on the board");
});

const config = { read_only: true, parts: [{ id: "atlas-system", settings: [] }, { id: "atlas-system-api", settings: [{ name: "X" }] }] };

test("a node's part is found by its id, and a node without one gets none", () => {
  assert.equal(partOf(config, "atlas-system-api").settings[0].name, "X");
  assert.equal(partOf(config, "some-other-node"), null);
  assert.equal(partOf(null, "atlas-system"), null);
  assert.equal(partOf({}, "atlas-system"), null);
});

test("a secret shows its state and nothing else, even if a value were sent", () => {
  assert.equal(valueText({ secret: true, state: "set from file", value: "leaked" }), "set from file");
  assert.equal(valueText({ secret: true, state: "not set" }), "not set");
  assert.equal(valueText({ secret: true }), "not set");
});

test("a plain value is shown in words", () => {
  assert.equal(valueText({ secret: false, value: "projects" }), "projects");
  assert.equal(valueText({ secret: false, value: true }), "on");
  assert.equal(valueText({ secret: false, value: false }), "off");
  assert.equal(valueText({ secret: false, value: 12 }), "12");
  assert.equal(valueText({ secret: false, value: null }), "(none)");
  assert.equal(valueText({ secret: false, value: [] }), "(empty)");
  assert.equal(valueText({ secret: false, value: ["a", "b"] }), "a, b");
  assert.equal(valueText({ secret: false, value: null, error: "x" }), "unreadable: x");
  assert.equal(valueText({ secret: false, value: null, blocked_by: "ATLAS_OIDC_GROUP_MAP_FILE" }),
    "not read: ATLAS_OIDC_GROUP_MAP_FILE does not parse");
  assert.equal(valueText({ secret: false, value: null, inactive: "not read while the retrieval add-on is off" }),
    "(not read while the retrieval add-on is off)");
});

test("the source reads env, file, derived (naming what from) or default", () => {
  assert.equal(sourceText({ source: "env" }), "env");
  assert.equal(sourceText({ source: "file" }), "file");
  assert.equal(sourceText({ source: "default" }), "default");
  assert.equal(sourceText({ source: "derived", derived_from: ["ATLAS_PUBLIC_URL"] }), "derived from ATLAS_PUBLIC_URL");
  assert.equal(sourceText({ source: null }), "unset");
});
