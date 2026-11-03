import { test } from "node:test";
import assert from "node:assert/strict";
import { createPrefs } from "./prefs.js";

function memory() {
  const m = new Map();
  return { m, getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
}

test("v2 keys live under atlas.v2.* and round-trip as JSON", () => {
  const st = memory(), p = createPrefs(st);
  p.remember("pane.board", 512);
  assert.equal(st.m.get("atlas.v2.pane.board"), "512");
  assert.equal(p.recall("pane.board", 0), 512);
  assert.equal(p.recall("never.set", "fallback"), "fallback");
});

test("theme and text size are shared with legacy under their legacy names, as plain strings", () => {
  const st = memory(), p = createPrefs(st);
  st.m.set("atlas.theme", "dark");          // what legacy writes: not JSON
  assert.equal(p.recall("theme", "light"), "dark");
  p.remember("uiScale", 1.25);
  assert.equal(st.m.get("atlas.uiScale"), "1.25");
});

test("storage that throws, or none at all, reads as nothing stored and never throws", () => {
  const broken = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("quota"); }, removeItem() { throw new Error("x"); } };
  for (const p of [createPrefs(broken), createPrefs(null)]) {
    assert.equal(p.recall("x", 7), 7);
    assert.doesNotThrow(() => p.remember("x", 1));
  }
});

test("remembering undefined removes the key", () => {
  const st = memory(), p = createPrefs(st);
  p.remember("k", 1); p.remember("k", undefined);
  assert.equal(st.m.has("atlas.v2.k"), false);
});
