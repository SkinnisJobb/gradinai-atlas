import { test } from "node:test";
import assert from "node:assert/strict";
import {
  crunchTabVisible, offeredEnvs, resolveWindow, buildConfig, extendConfig, configOf,
  durationText, listText, backupText, summaryLines, bannerText, errorText, BACKUP_POLICIES, wireConfig,
} from "./crunch-model.js";

const fmt = (/** @type {string} */ iso) => iso;
const NOW = new Date("2026-10-27T12:00:00.000Z");
const H = 3600e3;

test("visibility: the owner in a browser session, and nobody else", () => {
  assert.equal(crunchTabVisible({ role: "owner", kind: "session", is_service: false }), true);
  assert.equal(crunchTabVisible({ role: "member", kind: "session" }), false);
  assert.equal(crunchTabVisible({ role: "owner", kind: "token" }), false);
  assert.equal(crunchTabVisible({ role: "owner", kind: "session", is_service: true }), false);
  assert.equal(crunchTabVisible(null), false);
  assert.equal(crunchTabVisible({}), false);
});

test("production is never offered as an environment to skip", () => {
  assert.deepEqual(offeredEnvs(["staging", "production", "pre-prod", "Prod", "staging", "", 3]), ["staging", "pre-prod"]);
  assert.deepEqual(offeredEnvs(null), []);
  assert.deepEqual(offeredEnvs([]), []);
});

test("duration: a length counts from the start; now is the default start", () => {
  const w = resolveWindow({ start: "now", end: "length", days: 7, hours: 0 }, NOW);
  assert.ok(w.ok);
  if (!w.ok) return;
  assert.equal(w.starts_at, NOW.toISOString());
  assert.equal(w.ends_at, "2026-11-03T12:00:00.000Z");
  assert.equal(w.ms, 7 * 24 * H);
  const h = resolveWindow({ end: "length", days: "", hours: "5" }, NOW);
  assert.ok(h.ok && h.ends_at === "2026-10-27T17:00:00.000Z");
});

test("duration: a running week keeps its start; a change counts the length from it", () => {
  const w = resolveWindow({ fixedStart: "2026-10-25T12:00:00.000Z", end: "length", days: 3 }, NOW);
  assert.ok(w.ok && w.ends_at === "2026-10-28T12:00:00.000Z");
});

test("duration: refusals name the field", () => {
  const bad = (/** @type {any} */ d) => { const r = resolveWindow(d, NOW); return r.ok ? null : r.field; };
  assert.equal(bad({ end: "length", days: 0, hours: 0 }), "days");
  assert.equal(bad({ end: "length", days: -1 }), "days");
  assert.equal(bad({ end: "length", days: 1.5 }), "days");
  assert.equal(bad({ end: "length", hours: "x" }), "hours");
  assert.equal(bad({ end: "at", endAt: "" }), "end");
  assert.equal(bad({ start: "at", startAt: "" , end: "length", days: 1 }), "start");
  assert.equal(bad({ start: "at", startAt: "2020-01-01T00:00", end: "length", days: 1 }), "start");
  assert.equal(bad({ end: "at", endAt: "2020-01-01T00:00" }), "end");
  assert.equal(bad({ fixedStart: "2026-10-20T00:00:00Z", end: "length", hours: 1 }), "end");
});

test("config: the contract's shape, with production and unknown names dropped from skip_envs", () => {
  const r = buildConfig({ window: { end: "length", days: 2 }, policy: "daily", retention: "14",
    skip: ["staging", "production", "nope"], envs: ["staging", "pre-prod", "production"] }, NOW);
  assert.ok(r.ok);
  if (!r.ok) return;
  // Start now sends no starts_at: the server's clock sets it.
  assert.deepEqual(r.config, { ends_at: "2026-10-29T12:00:00.000Z", duration_hours: 48,
    backup: { policy: "daily", retention_days: 14 }, skip_envs: ["staging"] });
  // A length travels as duration_hours alone: the server computes the end from its own start.
  assert.deepEqual(wireConfig(r.config), { duration_hours: 48,
    backup: { policy: "daily", retention_days: 14 }, skip_envs: ["staging"] });
  const endAt = buildConfig({ window: { end: "at", endAt: "2026-10-29T12:00" }, policy: "daily", retention: 1, skip: [], envs: [] }, NOW);
  assert.ok(endAt.ok && typeof wireConfig(endAt.config).ends_at === "string" && !("duration_hours" in endAt.config), "an explicit end time is sent as ends_at");
  const long = buildConfig({ window: { end: "length", days: 32 }, policy: "daily", retention: 1, skip: [], envs: [] }, NOW);
  assert.ok(!long.ok && long.field === "days", "more than 31 days is refused before it is sent");
  const at = buildConfig({ window: { start: "at", startAt: "2026-10-28T08:00", end: "length", days: 2 }, policy: "daily", retention: 1, skip: [], envs: [] }, NOW);
  assert.ok(at.ok && typeof at.config.starts_at === "string", "a chosen start is sent");
  const run = buildConfig({ window: { fixedStart: "2026-10-25T12:00:00.000Z", end: "length", days: 3 }, policy: "daily", retention: 1, skip: [], envs: [] }, NOW);
  assert.ok(run.ok && run.config.starts_at === "2026-10-25T12:00:00.000Z", "a running week's start is sent back");
  assert.ok(run.ok && !("duration_hours" in run.config) && wireConfig(run.config).ends_at === "2026-10-28T12:00:00.000Z",
    "a running week's change keeps an absolute end, counted from its start");
  const p = buildConfig({ window: { end: "length", days: 2 }, policy: "weekly", retention: 1, skip: [], envs: [] }, NOW);
  assert.ok(!p.ok && p.field === "backup.policy");
  const k = buildConfig({ window: { end: "length", days: 2 }, policy: "none", retention: 0, skip: [], envs: [] }, NOW);
  assert.ok(!k.ok && k.field === "backup.retention_days");
  assert.deepEqual(BACKUP_POLICIES.map((b) => b.value), ["none", "once", "per_deploy", "hourly", "daily"]);
});

const STATE = { active: true, activated_by: "alex", activated_at: "2026-10-25T12:00:00.000Z", adr_id: "ADR-1",
  starts_at: "2026-10-25T12:00:00.000Z", ends_at: "2026-10-30T12:00:00.000Z",
  backup: { policy: "once", retention_days: 7 }, skip_envs: ["staging"] };

test("extend: the new end counts from the current end, and the rest is kept", () => {
  const r = extendConfig(STATE, 1, 6);
  assert.ok(r.ok);
  if (!r.ok) return;
  assert.equal(r.config.ends_at, "2026-10-31T18:00:00.000Z");
  assert.equal(r.config.starts_at, STATE.starts_at);
  assert.deepEqual(r.config.skip_envs, ["staging"]);
  assert.equal("activated_by" in r.config, false);
  // Sent as a length the server adds to the end it holds; ends_at is only shown.
  assert.equal(r.config.duration_hours, 30);
  assert.equal("ends_at" in wireConfig(r.config), false);
  assert.equal(wireConfig(r.config).starts_at, STATE.starts_at);
  const z = extendConfig(STATE, 0, 0);
  assert.ok(!z.ok && z.field === "days");
  assert.equal(extendConfig(null, 1, 0).ok, false);
  assert.equal(configOf(null), null);
});

test("words: durations, lists, backup policies", () => {
  assert.equal(durationText(7 * 24 * H), "7 days");
  assert.equal(durationText(26 * H), "1 day 2 hours");
  assert.equal(durationText(5 * H), "5 hours");
  assert.equal(durationText(40 * 60e3), "40 minutes");
  assert.equal(durationText(0), "no time");
  assert.equal(listText(["a"]), "a");
  assert.equal(listText(["a", "b"]), "a and b");
  assert.equal(listText(["a", "b", "c"]), "a, b and c");
  assert.equal(backupText({ policy: "none", retention_days: 7 }), "No backup is taken.");
  assert.equal(backupText({ policy: "per_deploy", retention_days: 1 }), "A backup is taken before every production deploy. Each backup is kept for 1 day.");
  assert.match(backupText({ policy: "hourly", retention_days: 3 }), /every hour.*3 days/);
});

test("summary: everything entered is restated, and what still holds is said", () => {
  const cfg = { starts_at: NOW.toISOString(), ends_at: "2026-11-03T12:00:00.000Z",
    backup: { policy: "daily", retention_days: 14 }, skip_envs: ["staging", "pre-prod"] };
  const lines = summaryLines("activate", cfg, { fmt, now: NOW });
  assert.equal(lines[0], "Crunch week starts now and ends by itself 2026-11-03T12:00:00.000Z (7 days).");
  assert.match(lines[1], /without staging and pre-prod\. A person still approves each production deploy\./);
  assert.doesNotMatch(lines[1], /without waiting for a person/);
  assert.match(lines[2], /every day.*14 days/);
  assert.ok(lines.some((l) => /CI must still pass/.test(l)));
  assert.ok(lines.some((l) => /Agents still never deploy production/.test(l)));
  const later = summaryLines("activate", { ...cfg, starts_at: "2026-10-28T08:00:00.000Z" }, { fmt, now: NOW });
  assert.match(later[0], /^Crunch week starts 2026-10-28T08:00:00.000Z/);
  const { starts_at: _s, ...noStart } = cfg;
  assert.equal(summaryLines("activate", noStart, { fmt, now: NOW })[0],
    "Crunch week starts now and ends by itself 2026-11-03T12:00:00.000Z (7 days).");
  const none = summaryLines("activate", { ...cfg, skip_envs: [] }, { fmt, now: NOW });
  assert.match(none[1], /No environment is skipped/);
  const ext = summaryLines("extend", { ...cfg, ends_at: "2026-11-04T12:00:00.000Z" }, { fmt, previous: cfg });
  assert.match(ext[0], /extended by 1 day: it now ends 2026-11-04T12:00:00.000Z instead of 2026-11-03T12:00:00.000Z/);
  assert.match(summaryLines("change", cfg, { fmt })[0], /keeps running/);
  assert.match(summaryLines("end", null, { fmt })[0], /ends now/);
});

test("banner: shown only while active and not yet ended", () => {
  assert.equal(bannerText({ active: true, ends_at: "2026-11-03T12:00:00.000Z" }, fmt, NOW),
    "Crunch week: production deploys directly until 2026-11-03T12:00:00.000Z");
  assert.equal(bannerText({ active: false, ends_at: null }, fmt, NOW), null);
  assert.equal(bannerText(null, fmt, NOW), null);
  assert.equal(bannerText({ active: true, ends_at: "2026-10-27T11:00:00.000Z" }, fmt, NOW), null);
});

test("errors: the server's named reason is always shown", () => {
  assert.match(errorText(403, { error: "password_incorrect", detail: "x" }), /not correct.*\(password_incorrect\)$/);
  assert.match(errorText(403, { error: "step_up_required" }), /password is needed.*\(step_up_required\)$/);
  assert.match(errorText(403, { error: "session_required" }), /\(session_required\)$/);
  assert.equal(errorText(400, { error: "bad_config", detail: "ends_at must be after starts_at", field: "ends_at" }),
    "The server refused the configuration. ends_at must be after starts_at Field: ends_at. (bad_config)");
  assert.equal(errorText(409, { error: "something_new", detail: "Said by the server." }), "Said by the server. (something_new)");
  assert.equal(errorText(500, null), "The request failed. (HTTP 500)");
  assert.match(errorText(401, {}), /session has ended/);
});
