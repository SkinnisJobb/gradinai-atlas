import { test } from "node:test";
import assert from "node:assert/strict";
import { FALLBACK_VOCABULARY, vocabularyFrom } from "./vocabulary.js";

test("the fallback is the lists the board hard-coded before TASK-1391", () => {
  assert.deepEqual([...FALLBACK_VOCABULARY.workitem_types], ["epic", "story", "task"]);
  assert.deepEqual([...FALLBACK_VOCABULARY.workitem_ladder], ["epic", "story", "task"]);
  assert.ok(Object.isFrozen(FALLBACK_VOCABULARY) && Object.isFrozen(FALLBACK_VOCABULARY.workitem_types));
});

test("a failed or unusable read falls back whole", () => {
  for (const body of [null, undefined, {}, { workitem_types: [] }, { workitem_types: "epic" }, { workitem_types: ["epic", 3] }]) {
    assert.equal(vocabularyFrom(body), FALLBACK_VOCABULARY, JSON.stringify(body));
  }
});

test("the server's lists are used as given", () => {
  const v = vocabularyFrom({ workitem_types: ["epic", "story", "task", "decision"], workitem_ladder: ["epic", "story", "task"], node_types: {} });
  assert.deepEqual([...v.workitem_types], ["epic", "story", "task", "decision"]);
  assert.deepEqual([...v.workitem_ladder], ["epic", "story", "task"]);
});

test("a server older than TASK-1389 (no ladder) keeps the fallback ladder, on the types it still has", () => {
  assert.deepEqual([...vocabularyFrom({ workitem_types: ["epic", "story", "task"] }).workitem_ladder], ["epic", "story", "task"]);
  assert.deepEqual([...vocabularyFrom({ workitem_types: ["story", "task"] }).workitem_ladder], ["story", "task"]);
});

test("a ladder rung that is not a work type is dropped", () => {
  assert.deepEqual([...vocabularyFrom({ workitem_types: ["epic", "task"], workitem_ladder: ["epic", "story", "task"] }).workitem_ladder], ["epic", "task"]);
});
