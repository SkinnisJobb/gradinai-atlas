// @ts-check
// views/settings/crunch-banner.js — the board's crunch-week banner
// (STORY-atlas-crunch-mode). While crunch week is active, every signed-in
// person sees "Crunch week: production deploys directly until <date>" across
// the top of the board.
//
// It reads `GET /api/crunch/banner` (any signed-in browser session; it carries
// only `active` and `ends_at`). Off, a 403, a missing route or any failure:
// nothing is drawn, and nothing navigates (core/api.js's fetchJSON would bounce
// a 403 to the login page, which is wrong for a banner, so it is not used).
// It reads again every few minutes, when the crunch page changes the state,
// and hides itself when the end time passes.

import { el } from "../../ui/dom.js";
import { bannerText } from "./crunch-model.js";

const CSS = "/assets/v2/views/settings/crunch.css";
const EVERY = 5 * 60e3;

function ensureStyles() {
  if (document.querySelector(`link[href="${CSS}"]`)) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = CSS;
  document.head.append(l);
}

/** @returns {Promise<any>} the banner body, or null on any failure */
async function readBanner() {
  try {
    const r = await fetch("/api/crunch/banner", { headers: { Accept: "application/json" }, cache: "no-store" });
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

/**
 * Mount the banner at the start of `bar` (the board's top bar). It takes its
 * own full-width line there, so the page grid is not changed.
 * @param {HTMLElement} bar
 */
export function mountCrunchBanner(bar) {
  /** @type {HTMLElement|null} */
  let node = null;
  /** @type {ReturnType<typeof setTimeout>|null} */
  let endTimer = null;
  const draw = (/** @type {any} */ b) => {
    const text = bannerText(b);
    if (endTimer) { clearTimeout(endTimer); endTimer = null; }
    if (!text) { if (node) { node.remove(); node = null; } return; }
    if (!node) {
      ensureStyles();
      node = el("div", "crunch-banner");
      node.id = "crunchBanner";
      node.setAttribute("role", "status");
      bar.prepend(node);
    }
    node.replaceChildren(el("span", "crunch-banner-ico", "⚠"), text);
    node.querySelector(".crunch-banner-ico")?.setAttribute("aria-hidden", "true");
    // Hide it the moment the end passes, not at the next read.
    const left = b && b.ends_at ? new Date(b.ends_at).getTime() - Date.now() : NaN;
    if (left > 0 && left < 2 ** 31 - 1) endTimer = setTimeout(() => draw(b), left + 1000);
  };
  const refresh = () => { readBanner().then(draw); };
  refresh();
  setInterval(refresh, EVERY);
  window.addEventListener("atlas:crunch-changed", refresh);
}
