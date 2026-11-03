import { test } from "node:test";
import assert from "node:assert/strict";
import { statusClass, githubUrl, adrParam, withAdrParam, linkGroups, sourceInfo } from "./adrs.js";

test("the source is a git file with its GitHub link, or recorded in Atlas", () => {
  assert.deepEqual(sourceInfo({ source: "atlas", repo: null, path: null }),
    { kind: "atlas", text: "Recorded in Atlas", url: null });
  assert.deepEqual(sourceInfo({ source: "git", repo: "o/r", path: "docs/adr/0041-x.md", commit: "abc" }),
    { kind: "git", text: "Git file · docs/adr/0041-x.md", url: "https://github.com/o/r/blob/abc/docs/adr/0041-x.md" });
  // A server older than the field sends no source: that record is a git one.
  assert.equal(sourceInfo({ repo: "o/r", path: "docs/adr/a.md" }).kind, "git");
  assert.deepEqual(sourceInfo({ source: "git", repo: null, path: null }), { kind: "git", text: "Git file", url: null });
});

test("a status chip's class is the normalised word, and anything else is unknown", () => {
  assert.equal(statusClass("Accepted"), "adr-st adr-st-accepted");
  assert.equal(statusClass("superseded"), "adr-st adr-st-superseded");
  assert.equal(statusClass("maybe"), "adr-st adr-st-unknown");
  assert.equal(statusClass(null), "adr-st adr-st-unknown");
});

test("the GitHub link is pinned to the synced commit, and absent without a repo", () => {
  assert.equal(githubUrl({ repo: "o/project-atlas", path: "docs/adr/0041-x y.md", commit: "abc123" }),
    "https://github.com/o/project-atlas/blob/abc123/docs/adr/0041-x%20y.md");
  assert.equal(githubUrl({ repo: "o/r", path: "docs/adr/a.md", commit: null }),
    "https://github.com/o/r/blob/HEAD/docs/adr/a.md");
  assert.equal(githubUrl({ repo: null, path: "docs/adr/a.md" }), null);
  assert.equal(githubUrl({ repo: "javascript:alert(1)//x", path: "a" }), null);
});

test("?adr= is read only when it is an ADR id, and writing it keeps every other parameter", () => {
  assert.equal(adrParam("?node=atlas&adr=at001"), "AT001");
  assert.equal(adrParam("?adr=0041"), null);
  assert.equal(adrParam("?node=atlas"), null);
  assert.equal(withAdrParam("?node=atlas&focus=x", "HA001"), "?node=atlas&focus=x&adr=HA001");
  assert.equal(withAdrParam("?node=atlas&adr=HA001", null), "?node=atlas");
  assert.equal(withAdrParam("?adr=HA001", null), "");
});

test("link groups come in a fixed order and empty ones are dropped", () => {
  const g = linkGroups({ supersedes: [], superseded_by: [{ id: "DS020" }], amends: [{ id: "ADR-0999", missing: true }] });
  assert.deepEqual(g.map((x) => x.label), ["Superseded by", "Amends"]);
  assert.equal(g[1].links[0].missing, true);
  assert.deepEqual(linkGroups({}), []);
});
