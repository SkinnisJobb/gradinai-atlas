// @ts-check
// views/panel/config.js — the Configuration tab (STORY-atlas-config-panel).
// It shows, on a part node of the install, every setting that belongs to that
// part, its effective value and where it comes from. Read-only unless the
// server has phase 2 on (below).
// - The data is `GET /api/config`, owner-only. The panel asks only for an
//   owner (`role` in the store), so nobody else issues a request that 403s.
// - Which nodes get the tab is the API's answer, never a list here: a node
//   gets it when the response has a part with its id.
// - A secret shows its state only ("set from file", "set from env", "not
//   set"). The server never sends more; `valueText` would not show more if it did.
// - Fetched once per page: the values are the API process's environment,
//   which does not change while it runs (except through the change below,
//   which fetches again).
// - PHASE 2, only when the response says `writes.enabled` (the server's
//   ATLAS_CONFIG_WRITES; off by default, and off this tab is exactly phase 1):
//   a row the server marks `changeable` gets a Change control. Saving asks for
//   the owner's password every time (the step-up `PUT /api/config/{name}`
//   checks), and a setting the environment sets says why it cannot be changed
//   here instead of offering a control: the environment wins. The server
//   decides every refusal; this only declines to offer what it would refuse.
// - An SSO owner (`writes.step_up: "sso"`, TASK-1436) has no password: Change
//   offers "Confirm with SSO" (the step-up for THAT setting), and once the server
//   reports it confirmed, Save sends no password.

import { fetchJSON, sendJSON } from "../../core/api.js";
import { el, button } from "../../ui/dom.js";

/** @type {Promise<any>|null} */
let pending = null;
/** The last configuration loaded, so a part's tab knows whether writes are on. @type {any} */
let current = null;

/** The configuration, fetched once; a failure is retried on the next ask. */
export function loadConfig() {
  if (!pending) {
    pending = fetchJSON("/api/config").then((cfg) => { current = cfg; return cfg; },
      (err) => { pending = null; throw err; });
  }
  return pending;
}

/** Does the server accept changes from the board? @param {any} config */
export function writesOn(config) {
  return !!(config && config.writes && config.writes.enabled === true);
}

/**
 * What a row offers: "edit" (a control), "env" (the environment sets it, which
 * wins) or null (nothing: writes off, or not changeable).
 * @param {any} config @param {any} s
 */
export function editState(config, s) {
  if (!writesOn(config) || !s || s.secret || s.kind !== "plain" || s.changeable !== true) return null;
  return s.source === "env" ? "env" : "edit";
}

/** A boolean setting is chosen on/off; anything else is typed. @param {any} s */
export function inputKind(s) {
  return typeof s.value === "boolean" ? "bool" : "text";
}

/**
 * The change form for one row: the value, the password, Save, and (for a
 * board-set value) Reset to default. `onDone` re-renders with fresh data.
 * @param {any} s @param {() => void} onDone
 */
function editor(s, onDone) {
  const box = el("div", "cfg-edit");
  const form = el("div", "cfg-form");
  form.hidden = true;
  /** @type {HTMLInputElement|HTMLSelectElement} */ let val;
  if (inputKind(s) === "bool") {
    const sel = /** @type {HTMLSelectElement} */ (el("select", "bsel"));
    for (const [v, label] of [["1", "on"], ["0", "off"]]) {
      const o = /** @type {HTMLOptionElement} */ (el("option", null, label)); o.value = v; sel.append(o);
    }
    sel.value = s.value ? "1" : "0";
    val = sel;
  } else {
    const inp = /** @type {HTMLInputElement} */ (el("input", "bsel"));
    inp.type = "text"; inp.value = s.value === null || s.value === undefined ? "" : String(s.value);
    val = inp;
  }
  val.setAttribute("aria-label", "New value of " + s.name);
  // SSO OWNERS HAVE NO PASSWORD HERE (TASK-1436). With ATLAS_OIDC_STEP_UP on, the
  // server says `writes.step_up: "sso"`: the owner confirms THIS setting with a
  // fresh sign-in at the identity provider, comes back to the same node, and saves
  // within the window, once. Everyone else gets the password field as before.
  const w = (current && current.writes) || {};
  const sso = w.step_up === "sso";
  const confirmed = sso && w.step_up_setting === s.name && (w.step_up_seconds_left || 0) > 0;
  const pw = /** @type {HTMLInputElement} */ (el("input", "bsel"));
  pw.type = "password"; pw.autocomplete = "current-password"; pw.placeholder = "Your password";
  pw.setAttribute("aria-label", "Your password, to confirm the change");
  const err = el("div", "err");
  const controls = () => /** @type {HTMLInputElement[]} */ (Array.from(form.querySelectorAll("button, input, select")));
  /** @param {unknown} value */
  const send = (value) => {
    err.textContent = "";
    if (!sso && !pw.value) { err.textContent = "Your password is needed for every change."; pw.focus(); return; }
    for (const x of controls()) x.disabled = true;
    sendJSON("PUT", `/api/config/${encodeURIComponent(s.name)}`, sso ? { value } : { value, password: pw.value })
      .then(() => { pending = null; onDone(); })
      .catch((e) => {
        for (const x of controls()) x.disabled = false;
        pw.value = "";
        err.textContent = (e && e.message) || "The change was not made.";
      });
  };
  if (sso && !confirmed) {
    form.append(button("Confirm with SSO", () => {
      location.href = `${w.step_up_url}?config=${encodeURIComponent(s.name)}&next=`
        + encodeURIComponent(location.pathname + location.search);
    }), el("div", "cfg-sum", "You sign in at your identity provider again first, for this setting. Then come back to this tab and save the change within "
      + Math.max(1, Math.floor((w.step_up_max_age || 300) / 60)) + " min, once."));
    form.append(err);
    box.append(button("Change", () => { form.hidden = !form.hidden; }), form);
    return box;
  }
  if (confirmed) {
    form.append(el("div", "cfg-sum", "Confirmed at your identity provider. Save within "
      + Math.max(1, Math.floor(w.step_up_seconds_left / 60)) + " min, once."));
    form.append(val, button("Save", () => send(val.value)));
  } else {
    form.append(val, pw, button("Save", () => send(val.value)));
  }
  if (s.source === "board") form.append(button("Reset to default", () => send(null)));
  form.append(err);
  box.append(button("Change", () => { form.hidden = !form.hidden; if (!form.hidden) val.focus(); }), form);
  return box;
}

/**
 * The part with this node's id, or null.
 * @param {any} config @param {string} id
 */
export function partOf(config, id) {
  const parts = config && Array.isArray(config.parts) ? config.parts : [];
  return parts.find((/** @type {any} */ p) => p && p.id === id) || null;
}

/**
 * What the value column shows. A secret is its state and nothing else.
 * @param {any} s
 */
export function valueText(s) {
  if (s.secret) return String(s.state || "not set");
  if (s.error) return "unreadable: " + s.error;
  // TASK-1439: another setting of the same section is the one at fault, and carries the error.
  if (s.blocked_by) return "not read: " + s.blocked_by + " does not parse";
  if (s.inactive) return "(" + s.inactive + ")";
  const v = s.value;
  if (v === null || v === undefined) return "(none)";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "(empty)";
  if (typeof v === "boolean") return v ? "on" : "off";
  return String(v);
}

/**
 * Where the value comes from, in words.
 * @param {any} s
 */
export function sourceText(s) {
  if (s.source === "env") return "env";
  if (s.source === "file") return "file";
  if (s.source === "derived") return "derived from " + ((s.derived_from || []).join(", ") || "another setting");
  if (s.source === "default") return "default";
  if (s.source === "board") return "set on the board";
  return "unset";
}

/**
 * @param {HTMLElement} body
 * @param {any} part  one entry of `/api/config`'s `parts`
 */
export function renderConfig(body, part) {
  if (!part) { body.append(el("div", "empty", "No configuration for this node.")); return; }
  const writes = writesOn(current);
  const sec = el("section", "psec");
  sec.append(el("h3", null, "Configuration"),
    el("div", "note", writes
      ? "The effective value of each setting in the running API, and where it comes from. A secret shows only whether it is set. A setting marked changeable can be changed here "
        + (current.writes.step_up === "sso" ? "after a fresh sign-in at your identity provider" : "with your password")
        + "; the environment wins over the board, and the board over the default."
      : "Read-only. The effective value of each setting in the running API, and where it comes from. A secret shows only whether it is set."));
  // A part that judges its own configuration (the retrieval add-on: off, on, or unavailable
  // with the reason) says so above its settings.
  if (part.status) sec.append(el("div", "note", "Status: " + part.status));
  const settings = /** @type {any[]} */ (part.settings || []);
  if (!settings.length) {
    sec.append(el("div", "empty", part.note || "No setting of this part is read by the API."));
    body.append(sec);
    return;
  }
  const list = el("div", "cfg-list");
  for (const s of settings) {
    const row = el("div", "cfg-row");
    const head = el("div", "cfg-head");
    head.append(el("span", "cfg-name", s.name));
    if (s.kind === "bootstrap") head.append(el("span", "chip", "bootstrap"));
    if (s.secret) head.append(el("span", "chip", "secret"));
    if (editState(current, s)) head.append(el("span", "chip", "changeable"));
    head.append(el("span", "chip cfg-src", sourceText(s)));
    const val = el("div", s.secret ? "cfg-val cfg-secret" : "cfg-val", valueText(s));
    row.append(head, val);
    if (s.summary) row.append(el("div", "cfg-sum", s.summary));
    const state = editState(current, s);
    if (state === "env") row.append(el("div", "cfg-sum", "Set in the server's environment, which wins over the board; change it there."));
    else if (state === "edit") {
      row.append(editor(s, () => {
        loadConfig().then((cfg) => { body.replaceChildren(); renderConfig(body, partOf(cfg, part.id)); },
          () => { body.replaceChildren(el("div", "err", "Saved, but the configuration could not be reloaded.")); });
      }));
    }
    list.append(row);
  }
  sec.append(list);
  body.append(sec);
}
