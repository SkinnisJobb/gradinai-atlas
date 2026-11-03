// util.js — small pure helpers with no dependency on board state or the DOM tree
// built by board.html. Moved out verbatim (TASK-1021, step 2 of the board split);
// see STORY-atlas-board-modularize for the plan and AGENTS.md "board.html — the
// rules that matter" for the house style these keep (ES5 idiom — no arrow
// functions, no template literals, `var`/`function` only).
//
// ES modules are strict mode already, so no "use strict" needed here.

// View preferences are a nice-to-have: private mode or disabled storage must never
// break the board, so every read/write of them goes through these two.
export function remember(key, value) { try { localStorage.setItem(key, String(value)); } catch (e) { /* no-op */ } }
export function recall(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }

var NS = "http://www.w3.org/2000/svg";

export function svgEl(tag, attrs) {
  var el = document.createElementNS(NS, tag);
  for (var k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

export function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

export function cleanName(n, type) {
  var s = n || "";
  if (type === "epic") s = s.replace(/^EPIC\s*[—-]\s*/i, "").replace(/\s*[—-]\s*Epic$/i, "");
  if (type === "story") s = s.replace(/^STORY\s*[—-]\s*/i, "").replace(/^P\d\w?\s*[—-]\s*/i, "");
  return s;
}

export function daysSince(iso) {
  if (!iso) return null;
  var t = Date.parse(iso);
  return isNaN(t) ? null : (Date.now() - t) / 86400000;
}

// Dismissing an overlay by its scrim, WITHOUT eating a drag that merely ENDED
// there. `click` is dispatched at the nearest common ancestor of the mousedown
// and mouseup targets — so selecting text inside the sheet and releasing on the
// scrim arrives here with `target === scrim`, indistinguishable from a real
// backdrop click. That threw away whatever had been typed, which is the worst
// possible moment to close a form. Require BOTH endpoints on the scrim.
//
// This lived as a copied block in compose.js and settings.js, which is how one
// bug shipped twice; it is a helper now so the next overlay inherits the fix.
export function dismissOnScrim(scrim, onDismiss) {
  var pressed = false;
  var released = false;
  scrim.addEventListener("mousedown", function (ev) { pressed = (ev.target === scrim); });
  scrim.addEventListener("mouseup", function (ev) { released = (ev.target === scrim); });
  scrim.addEventListener("click", function () {
    if (pressed && released) onDismiss();
    pressed = false;
    released = false;
  });
}
