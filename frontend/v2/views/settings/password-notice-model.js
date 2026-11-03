// @ts-check
// views/settings/password-notice-model.js — what the "no personal password yet"
// notice shows (TASK-1442). Pure, so it runs under node --test.
//
// The server decides everything: `/api/me` carries `password_setup` only for an
// SSO person whose account still has a temporary password they were never meant
// to use. The notice reads it and nothing else.

/** The query parameter the step-up comes back with. */
export const RETURN_PARAM = "setpw";

/**
 * What the notice shows, or null for no notice.
 *  - "off":     step-up is off on this install; the board cannot set a password.
 *  - "confirm": a fresh sign-in at the identity provider comes first.
 *  - "form":    confirmed; the new password can be saved within `minutes`.
 * @param {any} me  the `/api/me` body
 * @param {{dismissed?: boolean, returning?: boolean}} [opts]
 * @returns {null | {mode: "off"|"confirm"|"form", url: string, minutes: number, open: boolean}}
 */
export function noticeState(me, opts = {}) {
  const s = me && me.kind === "session" && !me.is_service ? me.password_setup : null;
  if (!s || typeof s !== "object") return null;
  // Coming back from the identity provider shows the notice even if it was
  // dismissed: the person just asked for it.
  if (opts.dismissed && !opts.returning) return null;
  if (s.step_up !== "sso" || typeof s.step_up_url !== "string" || !s.step_up_url.startsWith("/")) {
    return { mode: "off", url: "", minutes: 0, open: false };
  }
  const left = Number(s.step_up_seconds_left) || 0;
  if (left > 0) return { mode: "form", url: s.step_up_url, minutes: minutesOf(left), open: true };
  return { mode: "confirm", url: s.step_up_url, minutes: minutesOf(Number(s.step_up_max_age) || 300), open: !!opts.returning };
}

/** Whole minutes, at least one. @param {number} seconds */
export function minutesOf(seconds) { return Math.max(1, Math.floor(seconds / 60)); }

/**
 * Where "Confirm with SSO" goes: the step-up for a password, coming back to
 * this page with RETURN_PARAM set.
 * @param {string} url  `password_setup.step_up_url`
 * @param {string} here  pathname + search of the current page
 */
export function stepUpHref(url, here) {
  const [path, query = ""] = here.split("?");
  const q = new URLSearchParams(query);
  q.set(RETURN_PARAM, "1");
  return `${url}?password=1&next=${encodeURIComponent(path + "?" + q.toString())}`;
}

/**
 * Refuse the obvious before a request: the server checks the policy too.
 * @param {string} pw @param {string} again
 * @returns {string} the message to show, or "" to send
 */
export function formError(pw, again) {
  if (!pw) return "Choose a password.";
  if (pw !== again) return "The two passwords do not match.";
  return "";
}
