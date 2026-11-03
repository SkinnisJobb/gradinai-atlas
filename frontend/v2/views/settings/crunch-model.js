// @ts-check
// views/settings/crunch-model.js — the pure half of the crunch-week page
// (STORY-atlas-crunch-mode). No DOM, so `node --test` covers it: who sees the
// control, how a duration becomes a start and an end, how a configuration is
// said back in plain words, and how an API refusal is named.
//
// The server decides everything (backend/crunch.py validates the config, the
// step-up checks the password). These functions only decline to OFFER what the
// server would refuse, and say clearly what is about to be sent.

const HOUR = 3600e3;
const DAY = 24 * HOUR;
/** The server's bound on `duration_hours` (backend crunch.py MAX_DURATION_HOURS, 31 days). */
export const MAX_DURATION_HOURS = 744;

/** The backup policies the contract names, in the order they are offered. */
export const BACKUP_POLICIES = Object.freeze([
  { value: "none", label: "No backup", note: "Nothing is backed up. This is the default." },
  { value: "once", label: "Once, at the start", note: "One backup when crunch week starts." },
  { value: "per_deploy", label: "Before every deploy", note: "A backup before each production deploy." },
  { value: "hourly", label: "Every hour", note: "A backup every hour while it runs." },
  { value: "daily", label: "Every day", note: "A backup every day while it runs." },
]);
const POLICY_VALUES = BACKUP_POLICIES.map((p) => p.value);

/**
 * WHO SEES THE CONTROL: the instance owner in a browser session, and nobody
 * else. Decided from `/api/me`, so for everyone else the control is never
 * built (absent from the DOM, not hidden) and no request that 403s is made.
 * @param {any} me  the `/api/me` body
 */
export function crunchTabVisible(me) {
  return !!me && me.role === "owner" && me.kind === "session" && me.is_service !== true;
}

/**
 * The environments a person may choose to skip. Production is never offered:
 * crunch week skips the steps BEFORE production, never production itself.
 * @param {unknown} envs  `GET /api/crunch`'s `envs`
 * @returns {string[]}
 */
export function offeredEnvs(envs) {
  if (!Array.isArray(envs)) return [];
  const out = [];
  for (const e of envs) {
    if (typeof e !== "string" || !e) continue;
    if (/^prod(uction)?$/i.test(e.trim())) continue;
    if (!out.includes(e)) out.push(e);
  }
  return out;
}

/**
 * A `datetime-local` value ("2026-10-27T14:30") as a Date in local time, or null.
 * @param {string|null|undefined} s
 */
export function parseLocal(s) {
  if (!s || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(s)) return null;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** A Date as a `datetime-local` value, in local time. @param {Date} d */
export function toLocalInput(d) {
  const p = (/** @type {number} */ n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** A non-negative whole number from a form field, or null. @param {unknown} v */
function wholeNumber(v) {
  if (v === "" || v === null || v === undefined) return 0;
  const n = Number(v);
  return Number.isInteger(n) && n >= 0 ? n : null;
}

/**
 * DURATION MATH. The form gives a start (now, or a time) and an end (a time,
 * or a length in days and hours counted from the start). The answer is the
 * contract's `starts_at` / `ends_at`, in UTC ISO 8601, or the field at fault.
 * @param {{start?: "now"|"at", startAt?: string, end?: "at"|"length", endAt?: string, days?: unknown, hours?: unknown, fixedStart?: string|null}} d
 *   `fixedStart` is the running crunch week's start: a change keeps it.
 * @param {Date} now
 * @returns {{ok: true, starts_at: string, ends_at: string, ms: number} | {ok: false, field: string, reason: string}}
 */
export function resolveWindow(d, now) {
  let start;
  if (d.fixedStart) {
    start = new Date(d.fixedStart);
    if (Number.isNaN(start.getTime())) return { ok: false, field: "start", reason: "The running crunch week has no readable start." };
  } else if (d.start === "at") {
    start = parseLocal(d.startAt);
    if (!start) return { ok: false, field: "start", reason: "Choose a start date and time." };
    if (start.getTime() < now.getTime() - 60e3) return { ok: false, field: "start", reason: "The start is in the past. Choose Now, or a later time." };
  } else {
    start = now;
  }
  let end;
  if (d.end === "at") {
    end = parseLocal(d.endAt);
    if (!end) return { ok: false, field: "end", reason: "Choose an end date and time." };
  } else {
    const days = wholeNumber(d.days), hours = wholeNumber(d.hours);
    if (days === null) return { ok: false, field: "days", reason: "Days must be a whole number, 0 or more." };
    if (hours === null) return { ok: false, field: "hours", reason: "Hours must be a whole number, 0 or more." };
    if (days === 0 && hours === 0) return { ok: false, field: "days", reason: "Give a length of at least one hour." };
    end = new Date(start.getTime() + days * DAY + hours * HOUR);
  }
  if (end.getTime() <= start.getTime()) return { ok: false, field: "end", reason: "The end must be after the start." };
  if (end.getTime() <= now.getTime()) return { ok: false, field: "end", reason: "The end is already in the past." };
  return { ok: true, starts_at: start.toISOString(), ends_at: end.toISOString(), ms: end.getTime() - start.getTime() };
}

/**
 * The contract's config from the form, or the field at fault.
 * @param {{window: Parameters<typeof resolveWindow>[0], policy: string, retention: unknown, skip: string[], envs: unknown}} f
 * @param {Date} now
 * @returns {{ok: true, config: any} | {ok: false, field: string, reason: string}}
 */
export function buildConfig(f, now) {
  const w = resolveWindow(f.window, now);
  if (!w.ok) return w;
  if (!POLICY_VALUES.includes(f.policy)) return { ok: false, field: "backup.policy", reason: "Choose a backup policy." };
  const keep = Number(f.retention);
  if (!Number.isInteger(keep) || keep < 1) return { ok: false, field: "backup.retention_days", reason: "Keep backups for a whole number of days, 1 or more." };
  const offered = offeredEnvs(f.envs);
  const skip = (f.skip || []).filter((e) => offered.includes(e));
  // "Start now" sends NO starts_at: the server sets it from its own clock (backend crunch.py,
  // THE CLOCK), so this browser's clock cannot make the week start early or late. A chosen
  // time, or a running week's own start, is sent as it is.
  const startsNow = !f.window.fixedStart && f.window.start !== "at";
  // A length for a NEW window travels as `duration_hours`, so the server computes the end from
  // its own start (backend crunch.py, THE LENGTH). A running week's change keeps an absolute
  // end, counted from its server-side start; `ends_at` here is also what the summary shows.
  const length = !f.window.fixedStart && f.window.end !== "at" ? Math.round(w.ms / HOUR) : null;
  if (length !== null && length > MAX_DURATION_HOURS) return { ok: false, field: "days", reason: "Crunch week lasts at most 31 days." };
  return { ok: true, config: { ...(startsNow ? {} : { starts_at: w.starts_at }), ends_at: w.ends_at, ...(length !== null ? { duration_hours: length } : {}), backup: { policy: f.policy, retention_days: keep }, skip_envs: skip } };
}

/**
 * The config part of a running state (the contract's state is config + who/when).
 * @param {any} state
 */
export function configOf(state) {
  if (!state) return null;
  const b = state.backup || {};
  return { starts_at: state.starts_at, ends_at: state.ends_at,
    backup: { policy: b.policy || "none", retention_days: Number(b.retention_days) || 1 },
    skip_envs: Array.isArray(state.skip_envs) ? state.skip_envs.slice() : [] };
}

/**
 * Extend a running crunch week by a length. The new end counts from the
 * current end, never from now.
 * @param {any} state @param {unknown} days @param {unknown} hours
 * @returns {{ok: true, config: any} | {ok: false, field: string, reason: string}}
 */
export function extendConfig(state, days, hours) {
  const cfg = configOf(state);
  if (!cfg) return { ok: false, field: "state", reason: "Crunch week is not active." };
  const d = wholeNumber(days), h = wholeNumber(hours);
  if (d === null || h === null) return { ok: false, field: "days", reason: "Days and hours must be whole numbers, 0 or more." };
  if (d === 0 && h === 0) return { ok: false, field: "days", reason: "Extend by at least one hour." };
  const end = new Date(cfg.ends_at);
  if (Number.isNaN(end.getTime())) return { ok: false, field: "ends_at", reason: "The running crunch week has no readable end." };
  cfg.ends_at = new Date(end.getTime() + d * DAY + h * HOUR).toISOString();
  // Sent as a length: the server adds it to the end it holds (THE LENGTH), so the extension
  // is exact even if this page's copy of the end is stale. `ends_at` stays for the summary.
  cfg.duration_hours = d * 24 + h;
  if (cfg.duration_hours > MAX_DURATION_HOURS) return { ok: false, field: "days", reason: "Crunch week lasts at most 31 days." };
  return { ok: true, config: cfg };
}

/**
 * What PUT /api/crunch is sent: the config, minus the `ends_at` a `duration_hours` stands
 * in for (the server takes exactly one of the two).
 * @param {any} config
 */
export function wireConfig(config) {
  if (!config || config.duration_hours == null) return config;
  const { ends_at: _shown, ...rest } = config;
  return rest;
}

/**
 * A length in words: "7 days", "2 days 3 hours", "5 hours", "40 minutes".
 * @param {number} ms
 */
export function durationText(ms) {
  if (!(ms > 0)) return "no time";
  const mins = Math.round(ms / 60e3);
  if (mins < 60) return mins + (mins === 1 ? " minute" : " minutes");
  const totalH = Math.round(mins / 60), days = Math.floor(totalH / 24), hours = totalH % 24;
  const parts = [];
  if (days) parts.push(days + (days === 1 ? " day" : " days"));
  if (hours) parts.push(hours + (hours === 1 ? " hour" : " hours"));
  return parts.join(" ");
}

/** A list in words: "a", "a and b", "a, b and c". @param {string[]} xs */
export function listText(xs) {
  if (xs.length <= 1) return xs.join("");
  return xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1];
}

/** A date and time as the person reads it. @param {string} iso */
export function defaultFmt(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** The backup policy said in words. @param {{policy: string, retention_days: number}} b */
export function backupText(b) {
  const keep = ` Each backup is kept for ${b.retention_days} ${b.retention_days === 1 ? "day" : "days"}.`;
  switch (b.policy) {
    case "none": return "No backup is taken.";
    case "once": return "One backup is taken when crunch week starts." + keep;
    case "per_deploy": return "A backup is taken before every production deploy." + keep;
    case "hourly": return "A backup is taken every hour while crunch week runs." + keep;
    case "daily": return "A backup is taken every day while crunch week runs." + keep;
    default: return "Backup policy: " + b.policy + ".";
  }
}

/**
 * EVERYTHING ENTERED, RESTATED IN PLAIN WORDS — what the confirmation dialog
 * shows before the password is asked for.
 * @param {"activate"|"change"|"extend"|"end"} action
 * @param {any} config  the config about to be sent (ignored for "end")
 * @param {{fmt?: (iso: string) => string, previous?: any, now?: Date}} [ctx]  `previous` is the running config
 * @returns {string[]}
 */
export function summaryLines(action, config, ctx = {}) {
  const fmt = ctx.fmt || defaultFmt;
  if (action === "end") {
    return [
      "Crunch week ends now.",
      "Production goes back to the normal path: every step before production, then a person's approval.",
      "This is recorded as an ADR on atlas-system and in the crunch-week history.",
    ];
  }
  const lines = [];
  const now = ctx.now || new Date();
  // No starts_at: the server starts it now (buildConfig).
  const start = config.starts_at || now.toISOString();
  const len = durationText(new Date(config.ends_at).getTime() - new Date(start).getTime());
  const startsNow = !config.starts_at || Math.abs(new Date(start).getTime() - now.getTime()) < 120e3;
  if (action === "extend" && ctx.previous) {
    const more = durationText(new Date(config.ends_at).getTime() - new Date(ctx.previous.ends_at).getTime());
    lines.push(`Crunch week is extended by ${more}: it now ends ${fmt(config.ends_at)} instead of ${fmt(ctx.previous.ends_at)}.`);
  } else if (action === "change") {
    lines.push(`Crunch week keeps running, with these settings, until ${fmt(config.ends_at)} (${len} from its start, ${fmt(start)}).`);
  } else {
    lines.push(`Crunch week starts ${startsNow ? "now" : fmt(start)} and ends by itself ${fmt(config.ends_at)} (${len}).`);
  }
  const skip = config.skip_envs || [];
  lines.push(skip.length
    ? `During it, a change that passes CI deploys to production directly, without ${listText(skip)}. A person still approves each production deploy.`
    : "No environment is skipped, so changes still take the normal path to production.");
  lines.push(backupText(config.backup));
  lines.push("CI must still pass: a red build never deploys.");
  lines.push("Agents still never deploy production.");
  lines.push("This is recorded as an ADR on atlas-system and in the crunch-week history, under your name.");
  return lines;
}

/**
 * The board banner's text, or null when there is nothing to show.
 * @param {any} banner  `GET /api/crunch/banner`'s body
 * @param {(iso: string) => string} [fmt] @param {Date} [now]
 */
export function bannerText(banner, fmt = defaultFmt, now = new Date()) {
  if (!banner || banner.active !== true) return null;
  if (!banner.ends_at) return "Crunch week: production deploys directly until it is ended";
  if (new Date(banner.ends_at).getTime() <= now.getTime()) return null;
  return "Crunch week: production deploys directly until " + fmt(banner.ends_at);
}

const REASONS = /** @type {Record<string, string>} */ ({
  session_required: "This can only be done from a signed-in browser session; a token cannot read or change crunch week.",
  forbidden: "Only the instance owner can see or change crunch week.",
  step_up_required: "Your password is needed for every change to crunch week.",
  password_incorrect: "That password is not correct. Nothing was changed.",
  bad_config: "The server refused the configuration.",
  csrf_failed: "The page's security token is stale. Reload the board and try again.",
  rate_limited: "Too many wrong passwords. Wait a while before trying again.",
});

/**
 * AN API REFUSAL, NAMED: what went wrong in words, plus the server's own
 * reason code (and the field, for a `bad_config`), so it can be looked up.
 * @param {number} status @param {any} body
 */
export function errorText(status, body) {
  const code = body && typeof body.error === "string" ? body.error : null;
  if (status === 401) return "Your session has ended. Sign in again (401).";
  const words = (code && REASONS[code]) || (body && typeof body.detail === "string" && body.detail) || "The request failed.";
  const field = body && body.field ? ` Field: ${body.field}.` : "";
  // A bad_config's detail says what is wrong with the field, so it is shown too.
  const detail = code === "bad_config" && typeof body.detail === "string" && body.detail ? " " + body.detail : "";
  return `${words}${detail}${field} (${code || "HTTP " + status})`;
}
