import { test } from "node:test";
import assert from "node:assert/strict";
import { parse, format, bindUrl } from "./router.js";
import { createStore, INITIAL } from "./store.js";

test("parse reads node, focus and a bounded steps", () => {
  assert.deepEqual(parse("?node=atlas-map&focus=atlas&steps=3"), { node: "atlas-map", focus: "atlas", steps: 3 });
  assert.equal(parse("?steps=0").steps, null);
  assert.equal(parse("?steps=9").steps, 9);
  assert.equal(parse("?steps=65").steps, null);
  assert.equal(parse("?steps=2.5").steps, 2, "parseInt, as the slider only ever writes integers");
  assert.deepEqual(parse(""), { node: null, focus: null, steps: null });
});

test("a legacy deep link opens the same node", () => {
  assert.equal(parse("?tab=world&node=TASK-1287").node, "TASK-1287");
});

test("format keeps every parameter it does not own", () => {
  assert.equal(format("?tab=world&node=a", { node: "b", focus: null, steps: null }), "?tab=world&node=b");
  assert.equal(format("?node=a", { node: null }), "");
  assert.equal(format("", { node: "x", focus: "y", steps: 2 }), "?node=x&focus=y&steps=2");
});

test("bindUrl seeds the store from the URL and writes it back with replaceState", () => {
  const calls = [];
  const win = {
    location: { search: "?tab=world&node=n1&steps=4", pathname: "/board/v2", hash: "" },
    history: { replaceState: (_s, _t, url) => { calls.push(url); win.location.search = url.slice(url.indexOf("?")); } },
  };
  const store = createStore({ ...INITIAL });
  bindUrl(store, /** @type {any} */ (win));
  assert.equal(store.get().selected, "n1");
  assert.equal(store.get().steps, 4);
  store.set({ focus: "atlas" });
  assert.deepEqual(calls, ["/board/v2?tab=world&node=n1&steps=4&focus=atlas"]);
  store.set({ hover: "x" });
  assert.equal(calls.length, 1, "hover is not part of the URL");
});
