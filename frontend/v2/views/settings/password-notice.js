// @ts-check
// views/settings/password-notice.js — "You have no personal password yet"
// (TASK-1442).
//
// An account made with a temporary password and then moved to single sign-on
// (`atlas-admin user add`, `user link-idp`) signs in through the identity
// provider and is not sent to the forced password-change page. `/api/me` then
// carries `password_setup`, and this draws a dismissible line across the top of
// the board. "Set a password" sends the person through a fresh sign-in at the
// identity provider for exactly this (the step-up of TASK-1415/1436, with
// `password=1`); back on the board, within the window, they choose the password
// and it is saved with POST /api/me/password, no temporary password needed. The
// server decides every refusal; this only offers what it would accept.
//
// Dismissing hides it for this browser session (sessionStorage); coming back
// from the identity provider shows it again, open.

import { el, button } from "../../ui/dom.js";
import { sendJSON } from "../../core/api.js";
import { toast } from "../../ui/toast.js";
import { noticeState, stepUpHref, formError, RETURN_PARAM } from "./password-notice-model.js";

const CSS = "/assets/v2/views/settings/password-notice.css";
const DISMISSED = "atlas.v2.passwordNotice.dismissed";

function ensureStyles() {
  if (document.querySelector(`link[href="${CSS}"]`)) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = CSS;
  document.head.append(l);
}

const dismissed = () => { try { return sessionStorage.getItem(DISMISSED) === "1"; } catch { return false; } };
const dismiss = () => { try { sessionStorage.setItem(DISMISSED, "1"); } catch { /* ignore */ } };

/** Drop RETURN_PARAM from the address bar, so a reload is quiet. @returns {boolean} it was there */
function takeReturnParam() {
  const q = new URLSearchParams(location.search);
  if (!q.has(RETURN_PARAM)) return false;
  q.delete(RETURN_PARAM);
  try { history.replaceState(null, "", location.pathname + (q.size ? "?" + q : "") + location.hash); } catch { /* ignore */ }
  return true;
}

/** @param {string} label @param {string} autocomplete */
function pwInput(label, autocomplete) {
  const i = /** @type {HTMLInputElement} */ (el("input", "pwn-input"));
  i.type = "password"; i.autocomplete = autocomplete; i.placeholder = label;
  i.setAttribute("aria-label", label);
  return i;
}

/**
 * Mount the notice at the start of `bar` (the board's top bar), if `/api/me`
 * says this person has no personal password yet. Draws nothing otherwise.
 * @param {HTMLElement} bar @param {any} me  the `/api/me` body
 */
export function mountPasswordNotice(bar, me) {
  const returning = takeReturnParam();
  const st = noticeState(me, { dismissed: dismissed(), returning });
  if (!st) return;
  ensureStyles();
  const node = el("div", "pwn");
  node.id = "passwordNotice";
  node.setAttribute("role", "region");
  node.setAttribute("aria-label", "Personal password");
  const line = el("div", "pwn-line");
  const text = el("span", "pwn-text");
  text.append(el("strong", null, "You have no personal password yet. "),
    st.mode === "off"
      ? "You signed in through single sign-on. A personal Atlas password cannot be set from the board on this install; ask your operator."
      : "You signed in through single sign-on. You can set a personal Atlas password; your identity provider confirms it is you first.");
  const body = el("div", "pwn-body");
  body.hidden = !st.open;
  line.append(text);
  if (st.mode !== "off") {
    const toggle = button("Set a password", () => { body.hidden = !body.hidden; if (!body.hidden) focusFirst(); });
    toggle.id = "passwordNoticeOpen";
    line.append(toggle);
  }
  const close = button("×", () => { dismiss(); node.remove(); }, "btn pwn-x");
  close.setAttribute("aria-label", "Dismiss");
  close.title = "Hide this for now";
  line.append(close);
  node.append(line, body);

  const err = el("div", "pwn-err");
  err.setAttribute("role", "alert");
  /** @type {HTMLInputElement|null} */
  let first = null;
  const focusFirst = () => { if (first) first.focus(); };

  if (st.mode === "confirm") {
    body.append(
      el("p", "pwn-sum", `First sign in at your identity provider again, for this. Then come back here and choose the password within ${st.minutes} min.`),
      button("Confirm with SSO", () => { location.href = stepUpHref(st.url, location.pathname + location.search); }, "btn primary"));
  } else if (st.mode === "form") {
    const pw = pwInput("New password", "new-password");
    const again = pwInput("New password again", "new-password");
    first = pw;
    const save = button("Save password", () => {
      err.textContent = formError(pw.value, again.value);
      if (err.textContent) return;
      save.disabled = true;
      sendJSON("POST", "/api/me/password", { new_password: pw.value, confirm_password: again.value })
        .then(() => { node.remove(); toast("Your personal password is set."); })
        .catch((/** @type {any} */ e) => { save.disabled = false; err.textContent = (e && e.message) || "The password was not set."; });
    }, "btn primary");
    const form = el("form", "pwn-form");
    form.addEventListener("submit", (e) => { e.preventDefault(); save.click(); });
    form.append(pw, again, save);
    body.append(el("p", "pwn-sum", `Confirmed at your identity provider. Choose the password within ${st.minutes} min, once.`), form);
  }
  body.append(err);
  bar.prepend(node);
  if (st.open) focusFirst();
}
