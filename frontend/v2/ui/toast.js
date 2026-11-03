// @ts-check
// ui/toast.js — a short message at the bottom of the screen, for the outcome of
// an action taken somewhere the answer has no natural place to appear (a card
// dropped on the board). Errors stay until dismissed; confirmations fade.

import { el, button } from "./dom.js";

/** @param {string} text @param {"ok"|"error"} [kind] */
export function toast(text, kind = "ok") {
  let host = document.getElementById("toasts");
  if (!host) { host = el("div", "toasts"); host.id = "toasts"; host.setAttribute("role", "status"); host.setAttribute("aria-live", "polite"); document.body.append(host); }
  const t = el("div", "toast " + kind, text);
  const close = button("×", () => t.remove(), "toast-x"); close.setAttribute("aria-label", "Dismiss");
  t.append(close);
  host.append(t);
  if (kind === "ok") setTimeout(() => t.remove(), 3500);
}
