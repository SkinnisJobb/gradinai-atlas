import { test } from "node:test";
import assert from "node:assert/strict";
import { createStore, INITIAL } from "./store.js";
import { mirror } from "./sync.js";

/** Two in-memory channel ends that deliver to each other, like a BroadcastChannel pair. */
function pair() {
  const a = { onmessage: null, sent: 0, postMessage(m) { a.sent++; b.onmessage && b.onmessage({ data: structuredClone(m) }); } };
  const b = { onmessage: null, sent: 0, postMessage(m) { b.sent++; a.onmessage && a.onmessage({ data: structuredClone(m) }); } };
  return [a, b];
}

test("a shared change in one window reaches the other", () => {
  const [ca, cb] = pair();
  const A = createStore({ ...INITIAL, board: true }), B = createStore({ ...INITIAL, board: false });
  mirror(A, { channel: ca }); mirror(B, { channel: cb });
  A.set({ selected: "n1", focus: "root" });
  assert.equal(B.get().selected, "n1");
  assert.equal(B.get().focus, "root");
});

test("per-window keys stay in their window", () => {
  const [ca, cb] = pair();
  const A = createStore({ ...INITIAL, board: true }), B = createStore({ ...INITIAL, board: false });
  mirror(A, { channel: ca }); mirror(B, { channel: cb });
  A.set({ board: false, me: "x", rev: 3 });
  assert.equal(B.get().board, false, "unchanged: it was already false");
  A.set({ board: true });
  assert.equal(B.get().board, false, "board visibility is per window");
  assert.equal(B.get().rev, undefined);
});

test("a change applied from the other window is not echoed back", () => {
  const [ca, cb] = pair();
  const A = createStore({ ...INITIAL }), B = createStore({ ...INITIAL });
  mirror(A, { channel: ca }); mirror(B, { channel: cb });
  A.set({ hover: "h" });
  assert.equal(ca.sent, 1);
  assert.equal(cb.sent, 0, "B applied it without sending it back");
});

test("a message that is not a patch goes to onPeer, and junk is ignored", () => {
  const [ca, cb] = pair();
  const seen = [];
  const B = createStore({ ...INITIAL });
  mirror(createStore({ ...INITIAL }), { channel: ca });
  mirror(B, { channel: cb, onPeer: (m) => seen.push(m.kind) });
  ca.postMessage({ kind: "pane-open", pane: "board" });
  ca.postMessage({ kind: "patch", patch: { selected: "ok", me: "attacker", board: true } });
  assert.deepEqual(seen, ["pane-open"]);
  assert.equal(B.get().selected, "ok");
  assert.equal(B.get().me, undefined, "only shared keys are taken from a patch");
});
