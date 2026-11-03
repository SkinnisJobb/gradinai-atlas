// prepaint.js — the only classic (non-module) script in board v2, loaded
// blocking in <head>. A chosen theme or text size must not paint the default
// first and then jump. It reads the SAME keys as the legacy board
// (`atlas.theme`, `atlas.uiScale`), so one choice holds in both.
(function () {
  var root = document.documentElement;
  root.lang = "en";
  try {
    var t = localStorage.getItem("atlas.theme");
    if (t === "dark" || t === "light") root.setAttribute("data-theme", t);
  } catch (e) { /* private mode: the system preference applies */ }
  try {
    var s = parseFloat(localStorage.getItem("atlas.uiScale"));
    if (isFinite(s) && s > 0) root.style.setProperty("--ui-scale", String(s));
  } catch (e) { /* private mode: scale 1 */ }
})();
