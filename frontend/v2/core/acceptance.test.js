import { test } from "node:test";
import assert from "node:assert/strict";
import { acceptanceDiff, diffCounts, attestedLine } from "./acceptance.js";

const kinds = (d) => d.map((x) => `${x.kind[0]}:${x.text}`);

test("identical lists are all `same`", () => {
  assert.deepEqual(kinds(acceptanceDiff(["a", "b"], ["a", "b"])), ["s:a", "s:b"]);
});

test("an added criterion is one added line, in place", () => {
  assert.deepEqual(kinds(acceptanceDiff(["a", "c"], ["a", "b", "c"])), ["s:a", "a:b", "s:c"]);
});

test("a reworded criterion reads as removed then added", () => {
  assert.deepEqual(kinds(acceptanceDiff(["a", "old b", "c"], ["a", "new b", "c"])),
    ["s:a", "r:old b", "a:new b", "s:c"]);
});

test("from nothing, and to nothing", () => {
  assert.deepEqual(kinds(acceptanceDiff([], ["x"])), ["a:x"]);
  assert.deepEqual(kinds(acceptanceDiff(["x"], [])), ["r:x"]);
  assert.deepEqual(acceptanceDiff(null, null), []);
});

test("every input line appears exactly once on its side", () => {
  const cur = ["1", "2", "3", "4"], pro = ["2", "5", "4", "1"];
  const d = acceptanceDiff(cur, pro);
  assert.deepEqual(d.filter((x) => x.kind !== "added").map((x) => x.text), cur);
  assert.deepEqual(d.filter((x) => x.kind !== "removed").map((x) => x.text), pro);
  assert.deepEqual(diffCounts(d), { added: pro.length - d.filter((x) => x.kind === "same").length,
    removed: cur.length - d.filter((x) => x.kind === "same").length });
});

test("the attested line names the agent, the person and their words", () => {
  assert.equal(attestedLine({ agent: "bot-claude", approver: { username: "alex", name: "Alex" }, quote: "yes" }),
    "changed by bot-claude on Alex's approval: yes");
  assert.equal(attestedLine({ agent: "bot", approver: { username: "alex" }, quote: "ok" }),
    "changed by bot on alex's approval: ok");
  assert.equal(attestedLine(null), null);
});
