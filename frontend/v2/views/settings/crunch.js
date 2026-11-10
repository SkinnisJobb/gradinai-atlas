// @ts-check
// views/settings/crunch.js — the "crunch week" tab in Settings (STORY-atlas-crunch-mode).
//
// WHAT IT IS: for a set time, a change that passes CI deploys to production
// directly, without the staging / pre-production steps. Production keeps its
// gate: a person still approves each production deploy, as the generated crunch
// pipeline does (backend/cicd.py `_crunch_note`, `check_crunch`). The API is
// backend/routers (agent 1's crunch routes):
//   GET  /api/crunch       owner browser session only: state, history, envs, last backup
//   PUT  /api/crunch       activate, change or extend; {config, password, csrf_token}
//   POST /api/crunch/end   end now; {password, csrf_token}
//
// WHERE IT LIVES: Settings is the LEGACY overlay (frontend/settings.js, frozen by
// ADR 0043), reused by board v2 as it is. That file cannot change, so this
// module ADDS ONE TAB to the rail settings.js has just built (views/settings/
// tabs.js). It is only ever imported for the instance owner in a browser
// session (`crunchTabVisible`, decided from /api/me), so for everyone else the
// tab is absent from the DOM, and no request that 403s is made.
//
// Every change asks for the owner's password (the step-up the server checks),
// in a confirmation dialog that first restates everything in plain words.
// The server decides every refusal; this page shows it with its named reason.

import { el, button, svgEl } from "../../ui/dom.js";
import { toast } from "../../ui/toast.js";
import { addSettingsTab } from "./tabs.js";
import {
  crunchTabVisible, offeredEnvs, buildConfig, extendConfig, configOf, summaryLines, errorText, wireConfig,
  durationText, defaultFmt, toLocalInput, listText, BACKUP_POLICIES,
} from "./crunch-model.js";

const CSS = "/assets/v2/views/settings/crunch.css";
const KEY = "crunch";

function ensureStyles() {
  if (document.querySelector(`link[href="${CSS}"]`)) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = CSS;
  document.head.append(l);
}

/** The synchronizer token from /api/me, which every write must send. @type {string|null} */
let csrf = null;

/**
 * A call to the crunch routes. Resolves with the body; rejects with an Error
 * whose message is the API's refusal, named (crunch-model `errorText`). A 403
 * is reported here, never turned into a login bounce: the session is still good.
 * @param {"GET"|"PUT"|"POST"} method @param {string} url @param {Record<string, unknown>} [payload]
 */
async function call(method, url, payload) {
  /** @type {RequestInit} */
  const init = { method, headers: { Accept: "application/json" }, cache: "no-store" };
  if (payload) {
    init.headers = { ...init.headers, "Content-Type": "application/json" };
    init.body = JSON.stringify({ ...payload, csrf_token: csrf });
  }
  const r = await fetch(url, init);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(errorText(r.status, body));
  return body;
}

/** Tell the banner (and anything else listening) that the state changed. */
const announce = () => window.dispatchEvent(new CustomEvent("atlas:crunch-changed"));

// ------------------------------------------------------------ the Settings tab

/**
 * Add the crunch-week tab to the Settings rail that settings.js just built.
 * Called right after `openSettings(me)`; a no-op for anyone but the owner in
 * a browser session, and when the rail is not there.
 * @param {any} me  the /api/me body
 */
export function addCrunchTab(me) {
  if (!crunchTabVisible(me)) return;
  csrf = (me && me.csrf_token) || csrf;
  if (addSettingsTab(KEY, "Crunch", renderCrunchPage, { cls: "crunch-pane" })) ensureStyles();
}

// ------------------------------------------------------------------ the page

/** @param {HTMLElement} host */
export function renderCrunchPage(host) {
  ensureStyles();
  host.replaceChildren(el("p", "settings-note", "Loading crunch week…"));
  call("GET", "/api/crunch").then((data) => draw(host, data), (err) => {
    host.replaceChildren(intro(), el("p", "crunch-err", String(err && err.message)));
  });
}

function intro() {
  const sec = el("section", "settings-section crunch-intro");
  const h = el("h3", "settings-h3");
  h.append("Crunch week: ", el("span", "crunch-red", "production"), " directly");
  sec.append(h, el("p", "settings-note",
    "For a time you choose, a change that passes CI goes straight to production. It skips the "
    + "environments before production. A person still approves each production deploy, as "
    + "always. It ends by itself at the end time, and you can end it earlier. Use it for a "
    + "release push, when every hour in staging costs more than it protects."));
  return sec;
}

/** @param {HTMLElement} host @param {any} data  GET /api/crunch */
function draw(host, data) {
  const state = data && data.active ? data.state : null;
  const envs = offeredEnvs(data && data.envs);
  host.replaceChildren(intro(), statusCard(state), diagram(state ? (state.skip_envs || []) : envs, !!state), holds());
  host.append(configForm(host, state, envs));
  if (state) host.append(extendBox(host, state), endBox(host));
  host.append(historyBox(data && data.history, data && data.last_backup));
}

/** @param {any} state */
function statusCard(state) {
  const card = el("section", "crunch-status " + (state ? "is-on" : "is-off"));
  card.setAttribute("aria-live", "polite");
  if (!state) {
    card.append(el("strong", null, "Off."), " Production takes the normal path.");
    return card;
  }
  const left = new Date(state.ends_at).getTime() - Date.now();
  card.append(el("strong", null, "Active."),
    ` Production deploys directly until ${defaultFmt(state.ends_at)} (${durationText(left)} left).`);
  const dl = el("dl", "settings-dl");
  const row = (/** @type {string} */ k, /** @type {string} */ v) => dl.append(el("dt", null, k), el("dd", null, v));
  row("Started", defaultFmt(state.starts_at));
  if (state.activated_by) row("Activated by", state.activated_by);
  if (state.activated_at) row("Activated at", defaultFmt(state.activated_at));
  if (state.adr_id) row("Decision", state.adr_id);
  card.append(dl);
  return card;
}

// ---- the diagram: the normal path beside the crunch path -------------------

/**
 * Two lanes of boxes and arrows. Colours are CSS classes on the theme tokens,
 * so it follows light and dark; its width is in `--ui-scale` units (crunch.css).
 * @param {string[]} skipped  the environments the crunch path leaves out (or can, when off)
 * @param {boolean} active
 */
function diagram(skipped, active) {
  const fig = el("figure", "crunch-fig");
  const W = 720, BH = 40, BW = 112, GAP = 22;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} 250`, role: "img", class: "crunch-svg",
    "aria-label": "Normal path: pull request, CI, staging and pre-production, a person approves, production. "
      + "Crunch path: pull request, CI, a person approves, production." });
  const defs = svgEl("defs");
  const mk = svgEl("marker", { id: "crunchArrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" });
  mk.append(svgEl("path", { d: "M0 0L10 5L0 10z", class: "cf-arrowhead" }));
  defs.append(mk);
  svg.append(defs);
  const text = (/** @type {number} */ x, /** @type {number} */ y, /** @type {string} */ s, /** @type {string} */ cls, anchor = "middle") => {
    const t = svgEl("text", { x, y, class: cls, "text-anchor": anchor, "dominant-baseline": "middle" });
    t.textContent = s; return t;
  };
  /** @param {number} x @param {number} y @param {string} label @param {string} cls */
  const box = (x, y, label, cls) => {
    const g = svgEl("g", { class: "cf-box " + cls });
    g.append(svgEl("rect", { x, y, width: BW, height: BH, rx: 8 }), text(x + BW / 2, y + BH / 2, label, "cf-label"));
    svg.append(g);
  };
  /** @param {number} x1 @param {number} x2 @param {number} y @param {string} cls */
  const arrow = (x1, x2, y, cls) => svg.append(svgEl("line", { x1, y1: y, x2, y2: y, class: "cf-line " + cls, "marker-end": "url(#crunchArrow)" }));

  const xs = [0, 1, 2, 3, 4].map((i) => 8 + i * (BW + GAP + 14));
  const normal = ["Pull request", "CI", "Staging / pre-prod", "Person approves", "Production"];
  // Lane 1: the normal path.
  svg.append(text(8, 20, "Normal path", "cf-lane", "start"));
  normal.forEach((label, i) => {
    box(xs[i], 40, label, i === 4 ? "cf-prod" : i === 2 || i === 3 ? "cf-gate" : "cf-step");
    if (i < 4) arrow(xs[i] + BW + 2, xs[i + 1] - 4, 60, "");
  });
  // Lane 2: the crunch path — the same first two steps, past the environments
  // before production, to the same person's approval and production. The
  // approval stays: the generated crunch pipeline keeps production's gate.
  svg.append(text(8, 140, "Crunch path", "cf-lane cf-lane-red", "start"));
  box(xs[0], 160, normal[0], "cf-step");
  arrow(xs[0] + BW + 2, xs[1] - 4, 180, "");
  box(xs[1], 160, normal[1], "cf-step");
  // The skipped step, ghosted, with the direct arrow running past it.
  box(xs[2], 160, normal[2], "cf-skipped");
  svg.append(svgEl("path", { d: `M${xs[1] + BW + 2} 170 C ${xs[2]} 138, ${xs[3] - 40} 138, ${xs[3] - 4} 170`,
    class: "cf-line cf-direct", fill: "none", "marker-end": "url(#crunchArrow)" }));
  svg.append(text(xs[2] + BW / 2, 222, "skipped while crunch week runs", "cf-note"));
  box(xs[3], 160, normal[3], "cf-gate");
  arrow(xs[3] + BW + 2, xs[4] - 4, 180, "");
  box(xs[4], 160, normal[4], "cf-prod");
  fig.append(svg, el("figcaption", "settings-note",
    "Above, a change waits in the environments before production, then a person approves it. "
    + "Below, during crunch week, it goes from a green CI run straight to production's approval: "
    + "a person still approves each production deploy."
    + (skipped.length ? (active ? ` Skipped now: ${listText(skipped)}.` : ` On this install, the environments that can be skipped are ${listText(skipped)}.`) : "")));
  return fig;
}

/** What still holds while crunch week runs. */
function holds() {
  const sec = el("section", "settings-section");
  sec.append(el("h3", "settings-h3", "What still holds"));
  const ul = el("ul", "crunch-holds");
  for (const [head, rest] of [
    ["CI is still required.", " A change whose CI is red never deploys, crunch week or not."],
    ["Agents never deploy production.", " Crunch week changes the path a change takes, not who may deploy."],
    ["Backups are taken as you configure them below.", " None by default, so choose one if you want one."],
    ["It ends by itself", " at the end time you set. You can extend it, change it or end it at any time."],
    ["Only you can change it,", " signed in here with your password. No token and no MCP tool can read or change it."],
    ["Every change is recorded", " as a decision (ADR) on atlas-system and in the history below."],
  ]) {
    const li = el("li");
    li.append(el("strong", null, head), rest);
    ul.append(li);
  }
  sec.append(ul);
  return sec;
}

// ---- the configuration ----------------------------------------------------

/** @param {string} name @param {string} value @param {string} label @param {boolean} checked */
function radio(name, value, label, checked) {
  const lab = el("label", "crunch-opt");
  const r = /** @type {HTMLInputElement} */ (el("input"));
  r.type = "radio"; r.name = name; r.value = value; r.checked = checked;
  lab.append(r, " " + label);
  return { lab, r };
}
/** @param {string} cls @param {string} type @param {string} aria */
function input(cls, type, aria) {
  const i = /** @type {HTMLInputElement} */ (el("input", "cred-input " + cls));
  i.type = type; i.setAttribute("aria-label", aria);
  return i;
}

let uid = 0;

/**
 * The three configuration boxes and the Activate (or Change) control.
 * @param {HTMLElement} host @param {any} state  the running state, or null @param {string[]} envs
 */
function configForm(host, state, envs) {
  const n = ++uid;
  const cfg = configOf(state);
  const wrap = el("section", "settings-section");
  wrap.append(el("h3", "settings-h3", state ? "Change the settings" : "Configure"));
  const grid = el("div", "crunch-grid");

  // Duration.
  const dur = el("fieldset", "crunch-box");
  dur.append(el("legend", null, "Duration"));
  const startNow = radio("cstart" + n, "now", "Start now", true);
  const startAt = radio("cstart" + n, "at", "Start at", false);
  const startInput = input("crunch-dt", "datetime-local", "Start date and time");
  startInput.value = toLocalInput(new Date(Date.now() + 3600e3));
  const endLen = radio("cend" + n, "length", state ? "Length from its start" : "Length", true);
  const endAt = radio("cend" + n, "at", "End at", false);
  const days = input("crunch-num", "number", "Days"); days.min = "0"; days.step = "1"; days.value = "7";
  const hours = input("crunch-num", "number", "Hours"); hours.min = "0"; hours.max = "23"; hours.step = "1"; hours.value = "0";
  const endInput = input("crunch-dt", "datetime-local", "End date and time");
  endInput.value = toLocalInput(new Date(cfg ? cfg.ends_at : Date.now() + 7 * 24 * 3600e3));
  if (cfg) {
    const ms = new Date(cfg.ends_at).getTime() - new Date(cfg.starts_at).getTime();
    days.value = String(Math.floor(ms / 86400e3)); hours.value = String(Math.round((ms % 86400e3) / 3600e3));
    dur.append(el("p", "crunch-fixed", "Started " + defaultFmt(cfg.starts_at) + ". A running crunch week keeps its start."));
  } else {
    const row1 = el("div", "crunch-row"); row1.append(startNow.lab);
    const row2 = el("div", "crunch-row"); row2.append(startAt.lab, startInput);
    dur.append(row1, row2);
  }
  const row3 = el("div", "crunch-row"); row3.append(endLen.lab, days, el("span", null, "days"), hours, el("span", null, "hours"));
  const row4 = el("div", "crunch-row"); row4.append(endAt.lab, endInput);
  const preview = el("p", "crunch-preview");
  preview.setAttribute("aria-live", "polite");
  dur.append(row3, row4, preview);

  // Backup.
  const bk = el("fieldset", "crunch-box");
  bk.append(el("legend", null, "Backup"));
  const policyRadios = BACKUP_POLICIES.map((p) => {
    const o = radio("cbk" + n, p.value, p.label, (cfg ? cfg.backup.policy : "none") === p.value);
    o.lab.title = p.note;
    const row = el("div", "crunch-row"); row.append(o.lab, el("span", "crunch-hint", p.note));
    bk.append(row);
    return o.r;
  });
  const keep = input("crunch-num", "number", "Keep each backup for this many days");
  keep.min = "1"; keep.step = "1"; keep.value = String(cfg ? cfg.backup.retention_days : 7);
  const keepRow = el("div", "crunch-row"); keepRow.append(el("span", null, "Keep each backup for"), keep, el("span", null, "days"));
  bk.append(keepRow);

  // Environments to skip.
  const ev = el("fieldset", "crunch-box");
  ev.append(el("legend", null, "Environments to skip"));
  /** @type {HTMLInputElement[]} */
  const envBoxes = [];
  if (!envs.length) {
    ev.append(el("p", "settings-note", "This install's guardrail matrix names no environment before production, so there is nothing to skip."));
  }
  for (const name of envs) {
    const lab = el("label", "crunch-opt");
    const c = /** @type {HTMLInputElement} */ (el("input"));
    c.type = "checkbox"; c.value = name; c.checked = cfg ? cfg.skip_envs.includes(name) : true;
    lab.append(c, " " + name);
    const row = el("div", "crunch-row"); row.append(lab);
    ev.append(row);
    envBoxes.push(c);
  }
  ev.append(el("p", "crunch-hint", "Production is never offered: it is what crunch week deploys to."));
  grid.append(dur, bk, ev);
  wrap.append(grid);

  const err = el("p", "crunch-err");
  err.setAttribute("role", "alert");
  /** The form as crunch-model `buildConfig` reads it. */
  const read = () => buildConfig({
    window: { start: startAt.r.checked ? "at" : "now", startAt: startInput.value,
      end: endAt.r.checked ? "at" : "length", endAt: endInput.value, days: days.value, hours: hours.value,
      fixedStart: cfg ? cfg.starts_at : null },
    policy: (policyRadios.find((r) => r.checked) || { value: "" }).value,
    retention: keep.value,
    skip: envBoxes.filter((c) => c.checked).map((c) => c.value),
    envs,
  }, new Date());
  const refresh = () => {
    startInput.disabled = !startAt.r.checked;
    days.disabled = hours.disabled = !endLen.r.checked;
    endInput.disabled = !endAt.r.checked;
    keep.disabled = (policyRadios.find((r) => r.checked) || { value: "none" }).value === "none";
    const r = read();
    preview.textContent = r.ok
      ? `From ${r.config.starts_at ? defaultFmt(r.config.starts_at) : "now"} to ${defaultFmt(r.config.ends_at)}: ${durationText(new Date(r.config.ends_at).getTime() - new Date(r.config.starts_at || Date.now()).getTime())}.`
      : r.reason;
    preview.classList.toggle("is-bad", !r.ok);
  };
  wrap.addEventListener("input", refresh);
  wrap.addEventListener("change", refresh);
  refresh();

  const action = state ? "change" : "activate";
  const go = button(state ? "Change…" : "Activate crunch week…", () => {
    err.textContent = "";
    const r = read();
    if (!r.ok) { err.textContent = `${r.reason} (${r.field})`; return; }
    confirmDialog(action, summaryLines(action, r.config, { previous: cfg }),
      (password) => call("PUT", "/api/crunch", { config: wireConfig(r.config), password }), host);
  }, "btn crunch-go");
  const foot = el("div", "crunch-foot");
  foot.append(go, err);
  wrap.append(foot);
  return wrap;
}

/** Extend a running crunch week by days and hours. @param {HTMLElement} host @param {any} state */
function extendBox(host, state) {
  const sec = el("section", "settings-section");
  sec.append(el("h3", "settings-h3", "Extend"));
  const days = input("crunch-num", "number", "Extend by days"); days.min = "0"; days.value = "1";
  const hours = input("crunch-num", "number", "Extend by hours"); hours.min = "0"; hours.max = "23"; hours.value = "0";
  const err = el("p", "crunch-err"); err.setAttribute("role", "alert");
  const go = button("Extend…", () => {
    err.textContent = "";
    const r = extendConfig(state, days.value, hours.value);
    if (!r.ok) { err.textContent = `${r.reason} (${r.field})`; return; }
    confirmDialog("extend", summaryLines("extend", r.config, { previous: configOf(state) }),
      (password) => call("PUT", "/api/crunch", { config: wireConfig(r.config), password }), host);
  }, "btn");
  const row = el("div", "crunch-row");
  row.append(el("span", null, "Add"), days, el("span", null, "days"), hours, el("span", null, "hours to the end"), go);
  sec.append(row, err);
  return sec;
}

/** End it now. @param {HTMLElement} host */
function endBox(host) {
  const sec = el("section", "settings-section");
  sec.append(el("h3", "settings-h3", "End now"),
    el("p", "settings-note", "Production goes back to the normal path at once."));
  sec.append(button("End crunch week now…", () => {
    confirmDialog("end", summaryLines("end", null), (password) => call("POST", "/api/crunch/end", { password }), host);
  }, "btn crunch-go"));
  return sec;
}

/** The last changes and the last backup. @param {any} history @param {any} lastBackup */
function historyBox(history, lastBackup) {
  const sec = el("section", "settings-section");
  sec.append(el("h3", "settings-h3", "History"));
  const rows = Array.isArray(history) ? history : [];
  if (!rows.length) sec.append(el("p", "settings-note", "Crunch week has never been activated on this install."));
  else {
    const ol = el("ol", "crunch-hist");
    for (const h of rows) {
      const li = el("li");
      li.append(el("strong", null, String(h.action || "?")), ` · ${h.at ? defaultFmt(h.at) : "?"}`,
        h.by ? ` · ${h.by}` : "", h.reason ? ` · ${h.reason}` : "");
      ol.append(li);
    }
    sec.append(ol);
  }
  sec.append(el("h3", "settings-h3", "Last backup"));
  if (!lastBackup || typeof lastBackup !== "object") sec.append(el("p", "settings-note", "None taken during crunch week."));
  else {
    // The contract leaves the backup record's shape open, so every plain field is shown.
    const dl = el("dl", "settings-dl");
    for (const [k, v] of Object.entries(lastBackup)) {
      if (v === null || typeof v === "object") continue;
      const text = typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v) ? defaultFmt(v) : String(v);
      dl.append(el("dt", null, k.replace(/_/g, " ")), el("dd", null, text));
    }
    sec.append(dl);
  }
  return sec;
}

// ---- the confirmation dialog ---------------------------------------------

const TITLES = { activate: "Activate crunch week?", change: "Change crunch week?", extend: "Extend crunch week?", end: "End crunch week now?" };

/** The large red warning triangle. */
function warningIcon() {
  const s = svgEl("svg", { viewBox: "0 0 64 64", class: "crunch-warn-ico", "aria-hidden": "true" });
  s.append(svgEl("path", { d: "M32 5 L61 57 H3 Z", class: "cw-tri" }),
    svgEl("path", { d: "M32 22 V40", class: "cw-mark" }),
    svgEl("circle", { cx: 32, cy: 48, r: 2.8, class: "cw-dot" }));
  return s;
}

/**
 * Restate, ask for the password, and send. `send(password)` is the write;
 * on success the page is drawn again from the server's answer.
 * @param {"activate"|"change"|"extend"|"end"} action @param {string[]} lines
 * @param {(password: string) => Promise<any>} send @param {HTMLElement} host
 */
function confirmDialog(action, lines, send, host) {
  const before = /** @type {HTMLElement|null} */ (document.activeElement);
  const overlay = el("div", "settings-overlay crunch-confirm");
  overlay.setAttribute("role", "alertdialog");
  overlay.setAttribute("aria-modal", "true");
  const sheet = el("div", "settings-sheet crunch-confirm-sheet");
  const titleId = "crunchConfirmTitle";
  const head = el("div", "settings-head");
  const h = el("h2", null, TITLES[action]); h.id = titleId;
  overlay.setAttribute("aria-labelledby", titleId);
  const close = () => { overlay.remove(); if (before && before.focus) before.focus(); };
  const x = button("×", close, "settings-close"); x.setAttribute("aria-label", "Cancel");
  head.append(h, x);

  const body = el("div", "settings-body crunch-confirm-body");
  const top = el("div", "crunch-confirm-top");
  const ul = el("ul", "crunch-summary");
  for (const l of lines) ul.append(el("li", null, l));
  top.append(warningIcon(), ul);
  const form = /** @type {HTMLFormElement} */ (el("form", "crunch-confirm-form"));
  const lab = el("label", "cred-label", "Your password, to confirm");
  const pw = /** @type {HTMLInputElement} */ (el("input", "cred-input"));
  pw.type = "password"; pw.autocomplete = "current-password"; pw.required = true; pw.id = "crunchPassword";
  lab.setAttribute("for", pw.id);
  const err = el("p", "crunch-err"); err.setAttribute("role", "alert");
  const cancel = button("Cancel", close, "btn");
  const accept = /** @type {HTMLButtonElement} */ (el("button", "btn crunch-go", "Accept"));
  accept.type = "submit";
  const btns = el("div", "crunch-confirm-btns"); btns.append(cancel, accept);
  form.append(lab, pw, err, btns);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    err.textContent = "";
    if (!pw.value) { err.textContent = "Your password is needed for every change. (step_up_required)"; pw.focus(); return; }
    accept.disabled = cancel.disabled = pw.disabled = true;
    send(pw.value).then(() => {
      close();
      toast(action === "end" ? "Crunch week has ended." : action === "activate" ? "Crunch week is active." : "Crunch week is updated.");
      announce();
      renderCrunchPage(host);
    }, (er) => {
      accept.disabled = cancel.disabled = pw.disabled = false;
      pw.value = "";
      err.textContent = String(er && er.message);
      pw.focus();
    });
  });
  body.append(top, form);
  sheet.append(head, body);
  overlay.append(sheet);
  // Escape cancels this dialog only; it must not also close Settings behind it.
  overlay.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.body.append(overlay);
  pw.focus();
}
