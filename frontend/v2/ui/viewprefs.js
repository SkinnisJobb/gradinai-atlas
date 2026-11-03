// @ts-check
// ui/viewprefs.js — theme and text size, shared with the legacy board.
//
// Both write the same keys legacy writes (`atlas.theme`, `atlas.uiScale`, via
// prefs.js SHARED), so a choice made on either board holds on both. prepaint.js
// applies them before first paint. These controls change them afterwards and
// tell the store, so the graph can re-measure its labels.
// The text size moves through legacy's own stops.

import { prefs } from "../core/prefs.js";
import { el, button } from "./dom.js";

export const UI_SCALES = Object.freeze([0.9, 1, 1.15, 1.3, 1.5]);

/** The current text-size factor, read from the CSS variable (the one source of truth). */
export function uiScale() {
  const v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--ui-scale"));
  return Number.isFinite(v) && v > 0 ? v : 1;
}

/** @param {import("../core/store.js").Store} store */
export function viewPrefControls(store) {
  const root = document.documentElement, box = el("div", "group viewprefs");

  const theme = /** @type {HTMLSelectElement} */ (el("select", "bsel"));
  theme.id = "themeSelect"; theme.setAttribute("aria-label", "Theme");
  for (const [v, label] of [["", "System theme"], ["light", "Light"], ["dark", "Dark"]]) {
    const o = /** @type {HTMLOptionElement} */ (el("option", null, label)); o.value = v; theme.append(o);
  }
  theme.value = root.getAttribute("data-theme") || "";
  theme.addEventListener("change", () => {
    if (theme.value) root.setAttribute("data-theme", theme.value); else root.removeAttribute("data-theme");
    prefs.remember("theme", theme.value || undefined);
    store.set({ theme: theme.value });
  });

  const nearest = () => { const s = uiScale(); return UI_SCALES.reduce((b, x, i) => (Math.abs(x - s) < Math.abs(UI_SCALES[b] - s) ? i : b), 0); };
  /** @param {number} i */
  const setScale = (i) => {
    const s = UI_SCALES[Math.max(0, Math.min(UI_SCALES.length - 1, i))];
    root.style.setProperty("--ui-scale", String(s));
    prefs.remember("uiScale", s);
    down.disabled = s <= UI_SCALES[0]; up.disabled = s >= UI_SCALES[UI_SCALES.length - 1];
    store.set({ uiScale: s });
  };
  const down = button("A−", () => setScale(nearest() - 1), "btn scalebtn");
  const up = button("A+", () => setScale(nearest() + 1), "btn scalebtn");
  down.setAttribute("aria-label", "Smaller text"); up.setAttribute("aria-label", "Larger text");
  down.disabled = uiScale() <= UI_SCALES[0]; up.disabled = uiScale() >= UI_SCALES[UI_SCALES.length - 1];

  box.append(theme, down, up);
  return box;
}
