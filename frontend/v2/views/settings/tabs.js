// @ts-check
// views/settings/tabs.js — board v2's own tabs in the legacy Settings overlay.
//
// Settings is the LEGACY overlay (frontend/settings.js, frozen by ADR 0043),
// reused by board v2 as it is. That file cannot change, so a v2 tab is ADDED to
// the rail settings.js has just built, in the same shape as its own
// (`settingsNav-<key>` / `settingsPane-<key>`, role=tab / tabpanel). A tab draws
// its pane on first reveal, the legacy rule, so opening Settings stays free.
//
// Call `addSettingsTab` right after `openSettings(me)`: settings.js rebuilds
// the rail on every open, so the tab is added again each time.

import { el } from "../../ui/dom.js";

/**
 * Add one tab to the Settings rail. A no-op when the rail is not there or the
 * tab already is.
 * @param {string} key  the id suffix, unique among the rail's tabs
 * @param {string} label  the text on the rail
 * @param {(pane: HTMLElement) => void} draw  called once, on first reveal
 * @param {{select?: boolean, cls?: string}} [opts]  `select`: show it now
 * @returns {boolean} whether the tab was added
 */
export function addSettingsTab(key, label, draw, opts = {}) {
  const root = document.getElementById("settingsBody");
  const nav = root && root.querySelector(".settings-nav");
  const content = root && root.querySelector(".settings-content");
  if (!nav || !content || document.getElementById("settingsNav-" + key)) return false;

  const btn = /** @type {HTMLButtonElement} */ (el("button", "settings-nav-btn", label));
  btn.type = "button";
  btn.id = "settingsNav-" + key;
  btn.setAttribute("role", "tab");
  btn.setAttribute("aria-selected", "false");
  btn.tabIndex = -1;
  const panel = el("div", "settings-pane" + (opts.cls ? " " + opts.cls : ""));
  panel.id = "settingsPane-" + key;
  panel.setAttribute("role", "tabpanel");
  panel.setAttribute("aria-labelledby", btn.id);
  btn.setAttribute("aria-controls", panel.id);
  panel.hidden = true;
  nav.append(btn);
  content.append(panel);

  let loaded = false;
  const show = () => {
    for (const b of nav.querySelectorAll(".settings-nav-btn")) {
      b.setAttribute("aria-selected", "false"); /** @type {HTMLElement} */ (b).tabIndex = -1; b.classList.remove("is-active");
    }
    for (const p of content.querySelectorAll(".settings-pane")) /** @type {HTMLElement} */ (p).hidden = true;
    btn.setAttribute("aria-selected", "true"); btn.tabIndex = 0; btn.classList.add("is-active");
    panel.hidden = false;
    if (!loaded) { loaded = true; draw(panel); }
  };
  btn.addEventListener("click", show);
  // settings.js's own tabs select themselves and know nothing of this one, so
  // when one of them (or another v2 tab) is chosen, this one steps aside.
  const stepAside = () => {
    panel.hidden = true; btn.setAttribute("aria-selected", "false"); btn.tabIndex = -1; btn.classList.remove("is-active");
  };
  nav.addEventListener("click", (e) => {
    const t = /** @type {HTMLElement} */ (e.target).closest(".settings-nav-btn");
    if (t && t !== btn) stepAside();
  });
  nav.addEventListener("keydown", (e) => {
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(/** @type {KeyboardEvent} */ (e).key)) stepAside();
  });
  if (opts.select) show();
  return true;
}
