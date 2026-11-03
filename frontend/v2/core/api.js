// @ts-check
// core/api.js — every read the board makes goes through here.
//
// The rules are the legacy board's (frontend/api.js), because the API behind
// both is the same:
// - A 401 or 403 on a READ means the session is gone, so go to the login page
//   and come back here afterwards.
// - A body naming `password_change_required` goes to the change form instead.
// - Only one navigation wins, however many reads fail at once.
// - The destination is always a local path, never a host the body names.

let navigated = false;

/**
 * `value` if it is a same-origin path, else `fallback`. A redirect target that
 * comes out of a response body must never become an open redirect.
 * @param {unknown} value
 * @param {string} fallback
 */
export function safeLocalPath(value, fallback) {
  return typeof value === "string" && value.startsWith("/") && !value.startsWith("//") && !value.includes("\\")
    ? value : fallback;
}

/**
 * Where a failed read sends the browser. Pure, so it can be tested.
 * @param {any} body  the parsed error body, or null
 * @param {string} here  pathname + search of the current page
 */
export function authDestination(body, here) {
  if (body && (body.error === "password_change_required" || body.change_password)) {
    return safeLocalPath(body.change_password, "/auth/password/change");
  }
  return "/auth/login?next=" + encodeURIComponent(here);
}

/**
 * @param {string} url
 * @returns {Promise<any>}
 */
export async function fetchJSON(url) {
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  if (r.status === 401 || r.status === 403) {
    const body = await r.json().catch(() => null);
    if (!navigated) {
      navigated = true;
      // replace(), not assign(): Back should not return to a board that failed to load.
      location.replace(authDestination(body, location.pathname + location.search));
    }
    throw new Error("AUTH " + r.status + " for " + url);
  }
  if (!r.ok) throw new Error("HTTP " + r.status + " for " + url);
  return r.json();
}

// ---- writes ---------------------------------------------------------------
// Every write route goes through `auth.guard_write`: a cookie session must
// send the synchronizer token from `/api/me`, and that is the only way a browser
// can write. app.js sets it once the first `/api/me` answers.
// A 401 means the session is gone (go to login). A 403 is a refusal on a
// session that is still valid (stale token, wrong origin), so it is reported
// to the control that caused it and never turned into a login bounce. The rule
// is legacy api.js's.

/** @type {string|null} */
let csrf = null;
/** @param {string|null} token */
export function setCsrf(token) { csrf = token; }

/**
 * @param {"POST"|"PATCH"|"PUT"|"DELETE"} method @param {string} url @param {Record<string, unknown>} payload
 * @returns {Promise<any>}  the response body; rejects with the API's own message
 */
export async function sendJSON(method, url, payload) {
  const r = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ ...payload, csrf_token: csrf }),
  });
  const body = await r.json().catch(() => ({}));
  if (r.status === 401) {
    if (!navigated) { navigated = true; location.replace(authDestination(body, location.pathname + location.search)); }
    throw new Error("Your session has ended.");
  }
  if (!r.ok) throw new Error(body.detail || body.error || "HTTP " + r.status);
  return body;
}

/** The statuses the status control writes (legacy state.js STATUS_CHOICES). */
export const STATUS_CHOICES = Object.freeze([
  { value: "pending", label: "pending" }, { value: "analyze", label: "analyze" },
  { value: "in_progress", label: "in progress" }, { value: "blocked", label: "blocked" },
  { value: "done", label: "done" }, { value: "cancelled", label: "cancelled" },
]);

/** @param {string} id @param {string} status */
export const setStatus = (id, status) => sendJSON("POST", `/api/node/${encodeURIComponent(id)}/status`, { status });

/**
 * Change a workitem's fields: `body`, `assignee` (a person's id, or null),
 * `binding` (a world node's id, or null), `acceptance` (a list of strings).
 * The server answers with the item as it now stands, and that answer is what
 * gets drawn, never the value that was typed.
 * @param {string} id @param {Record<string, unknown>} fields
 */
export const patchWorkitem = (id, fields) => sendJSON("PATCH", `/api/workitem/${encodeURIComponent(id)}`, fields);

// ---- acceptance proposals (STORY-atlas-acceptance-human-approved) ----------
// An agent cannot change an item's acceptance; it may PROPOSE a list, and a
// person approves or rejects it from the panel. Approve applies it server-side
// through the same PATCH a person's own edit uses.

/**
 * The item's acceptance, its pending proposal (or null) and its latest
 * attested change (or null).
 * @param {string} id
 */
export const getAcceptanceProposal = (id) => fetchJSON(`/api/workitem/${encodeURIComponent(id)}/acceptance-proposal`);

/**
 * Approve or reject the proposal the person was shown, named by its id.
 * @param {string} id @param {number} proposalId @param {"approve"|"reject"} verb
 */
export const decideAcceptanceProposal = (id, proposalId, verb) =>
  sendJSON("POST", `/api/workitem/${encodeURIComponent(id)}/acceptance-proposal/${encodeURIComponent(String(proposalId))}/${verb}`, {});
