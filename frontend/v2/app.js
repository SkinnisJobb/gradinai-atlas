// @ts-check
// app.js — board v2's entry module (ADR 0043).
//
// It is a real file and not an inline script, so every module can be imported
// and none needs a `hooks` object filled in at boot. Its job:
// 1. Create the store and bind the URL to it.
// 2. Lay out the panes.
// 3. Load the graph.
// 4. Hand each pane the store and the graph index.
// The panes never call each other: each reacts to the store.

import { createStore, INITIAL } from "./core/store.js";
import { bindUrl } from "./core/router.js";
import { prefs } from "./core/prefs.js";
import { fetchJSON, sendJSON, setCsrf } from "./core/api.js";
import { buildIndex } from "./core/graph-model.js";
import { vocabularyFrom } from "./core/vocabulary.js";
import { mountPanes } from "./ui/panes.js";
import { el } from "./ui/dom.js";
import { mountGraph } from "./views/graph/graph.js";
import { searchBox } from "./views/search.js";
import { viewPrefControls, themeControl } from "./ui/viewprefs.js";
import { mountUserMenu } from "./ui/usermenu.js";
import { addSettingsTab } from "./views/settings/tabs.js";
import { mirror, SHARED_KEYS } from "./core/sync.js";
import { toast } from "./ui/toast.js";
import { crunchTabVisible } from "./views/settings/crunch-model.js";

// What /api/graph is asked for. `families` seeds the search from the WORLD's
// roots. The search then follows every edge both ways, so the work bound to
// the world arrives with it. `unbound_work=1` (TASK-1472) adds, after that walk
// and within what `cap` has left, the work it cannot reach: a workitem with no
// parent and no binding is connected to nothing, so without it such an item
// was on no board and in no board search.
const GRAPH_URL = "/api/graph?depth=8&families=object,bridge&cap=2000&prose=none&unbound_work=1";

// A POP-OUT WINDOW (`?pane=board` or `?pane=panel`, TASK-1309) shows that one
// pane full width, for a second monitor. It shares the main window's state
// through core/sync.js.
const ONLY = (() => { const p = new URLSearchParams(location.search).get("pane"); return p === "board" || p === "panel" ? p : null; })();
if (ONLY) document.documentElement.setAttribute("data-only", ONLY);

const store = createStore({
  ...INITIAL,
  // `board` and `dashboard` say whether that pane is EXPANDED: both are always
  // there, a collapsed one as its bar (ui/panes.js). The work board starts
  // expanded only where there is room for it beside the graph.
  board: prefs.recall("board.on", window.innerWidth >= 1400),
  // The dashboard pane (TASK-1447) starts collapsed until the person opens it.
  dashboard: prefs.recall("dashboard.on", false),
  links: prefs.recall("graph.links", "contains"),
  rel: prefs.recall("graph.rel", "hover"),
  work: prefs.recall("graph.work", false),
  hideTypes: prefs.recall("graph.hideTypes", []),
  stars: prefs.recall("stars", []),
  collapsed: prefs.recall("graph.collapsed", []),
  views: prefs.recall("graph.views", []),
  me: null,
  role: null,
});
bindUrl(store);
for (const [key, pref] of [["board", "board.on"], ["dashboard", "dashboard.on"], ["links", "graph.links"], ["rel", "graph.rel"], ["work", "graph.work"], ["hideTypes", "graph.hideTypes"], ["stars", "stars"], ["collapsed", "graph.collapsed"], ["views", "graph.views"]]) {
  store.subscribe([key], (s) => prefs.remember(pref, s[key]));
}

const $ = (/** @type {string} */ id) => /** @type {HTMLElement} */ (document.getElementById(id));
const dashPane = $("paneDashboard");
const stage = $("stage"), graphPane = $("paneGraph"), boardPane = $("paneBoard"), panelPane = $("panePanel");
// A click on a pane's bar is the board's and the dashboard's on/off switch.
const panes = mountPanes(stage, prefs, {
  onToggle: (name, collapsed) => { if (!ONLY && (name === "board" || name === "dashboard")) store.set({ [name]: !collapsed }); },
});

/** @param {HTMLElement} slot @param {string} title @param {string} what */
function placeholder(slot, title, what) {
  const box = el("div", "placeholder");
  box.append(el("h2", null, title), el("p", null, what));
  slot.replaceChildren(box);
}

// Pane visibility: the board and the dashboard are always there (collapsed
// when off), the panel shows on a selection. A pane that is open in its own
// window is hidden here.
// In a pop-out, only that one pane shows.
/** @type {{board: boolean, panel: boolean}} */
const outside = { board: false, panel: false };
function syncVisibility() {
  const s = store.get();
  if (ONLY) {
    graphPane.hidden = true;
    dashPane.hidden = true;
    boardPane.hidden = ONLY !== "board";
    panelPane.hidden = ONLY !== "panel";
  } else {
    boardPane.hidden = outside.board;
    dashPane.hidden = false;
    panelPane.hidden = !s.selected || outside.panel;
    panes.setCollapsed("board", !s.board);
    panes.setCollapsed("dashboard", !s.dashboard);
  }
  panes.apply();
}
const sync = mirror(store, {
  onPeer: (m) => {
    if (m.kind === "pane-open" && !ONLY) {
      // A pop-out just started: hand it everything shared, then step aside.
      /** @type {Record<string, unknown>} */
      const all = {}; for (const k of SHARED_KEYS) all[k] = store.get()[k];
      sync.send({ kind: "patch", patch: all });
      outside[/** @type {"board"|"panel"} */ (m.pane)] = true; syncVisibility();
    } else if (m.kind === "pane-closed" && !ONLY) {
      outside[/** @type {"board"|"panel"} */ (m.pane)] = false; syncVisibility();
    }
  },
});
if (ONLY) {
  document.title = ONLY === "board" ? "Atlas · Work board" : "Atlas · Node panel";
  sync.send({ kind: "pane-open", pane: ONLY });
  window.addEventListener("pagehide", () => sync.send({ kind: "pane-closed", pane: ONLY }));
}
// A pane asks to be popped out by event, so it need not know about windows.
window.addEventListener("atlas:popout", (e) => {
  const pane = /** @type {CustomEvent} */ (e).detail;
  const s = store.get(), q = new URLSearchParams({ pane });
  if (s.selected) q.set("node", String(s.selected));
  if (s.focus) q.set("focus", String(s.focus));
  const w = window.open("/board?" + q.toString(), "atlas-" + pane, "popup,width=1400,height=900");
  if (!w) toast("The browser blocked the new window. Allow pop-ups for this site and try again.", "error");
});
store.subscribe(["board", "selected", "dashboard"], syncVisibility);

/** @type {Promise<{refresh: () => Promise<void>}|null>|null} */
let dash = null;
const syncDash = () => {
  const on = !!store.get().dashboard && !ONLY;
  if (!on) return;
  if (dash) { dash.then((d) => d && d.refresh()); return; }
  dash = Promise.all([import("./views/dashboard/dashboard.js"), import("./views/dashboard/boxes.js")])
    .then(([m, b]) => m.mountDashboard(dashPane, { boxes: b.BOXES, prefs, api: { fetchJSON, sendJSON } }))
    .catch(() => { placeholder(dashPane, "Dashboard", "The dashboard could not load."); return null; });
};
store.subscribe(["dashboard", "rev"], syncDash);
// A dashboard box asks to select a node by event (views/dashboard/my-tasks.js,
// TASK-1450): select it and expand the node panel if it is collapsed.
window.addEventListener("atlas:select", (e) => {
  const id = /** @type {CustomEvent} */ (e).detail;
  if (typeof id !== "string" || !id) return;
  store.set({ selected: id });
  panes.expand("panel");
});
$("barEnd").prepend(viewPrefControls(store));

// "File work" and Settings are the LEGACY modules, reused as they are
// (TASK-1307). Both are lazy-imported, own their DOM, and write with legacy
// api.js's CSRF token, which only legacy initAccount() sets. So that runs once,
// on first use. Its account-slot rendering finds no slot here and does nothing.
/** @type {Promise<any>|null} */
let legacyMe = null;
const legacyAccount = () => (legacyMe ||= import("/assets/api.js").then((m) => m.initAccount()));
const fileWork = /** @type {HTMLButtonElement} */ (el("button", "btn", "File work"));
fileWork.type = "button";
fileWork.title = "File a new task, story or epic";
fileWork.addEventListener("click", () => {
  Promise.all([legacyAccount(), import("/assets/compose.js")]).then(([me, mod]) => {
    mod.hooks.afterCreate = (/** @type {string} */ id) => store.set({ stale: Date.now(), selected: id });
    mod.openCompose(me || null);
  }).catch(() => toast("File work could not load.", "error"));
});
// Crunch week (STORY-atlas-crunch-mode) is one more Settings tab, added by
// views/settings/crunch.js to the rail the legacy module just built, for the
// instance owner in a browser session only: for anyone else it is never built
// and its module is never loaded.
// Two more are board v2's own (views/settings/tabs.js): Appearance (the theme)
// for everyone, and Groups & roles for an instance owner, which was the top
// bar's Access button.
/** @param {any} me @param {any} mod @param {string} [outcome] */
function openSettings(me, mod, outcome) {
  mod.openSettings(me, outcome);
  addSettingsTab("appearance", "Appearance", (pane) => {
    const sec = el("section", "settings-section");
    const label = el("label", "settings-field");
    label.append(el("span", "settings-h3", "Theme"), themeControl(store));
    sec.append(label, el("p", "settings-note", "System follows your device's light or dark setting. The choice is kept in this browser."));
    pane.append(sec);
  });
  if (me && me.role === "owner") {
    addSettingsTab("groups", "Groups & roles", (pane) => {
      import("./views/access/access.js").then((m) => m.renderAccess(pane))
        .catch(() => { pane.replaceChildren(el("p", "settings-note", "Groups & roles could not load.")); });
    });
  }
  if (crunchTabVisible(me)) {
    import("./views/settings/crunch.js").then((c) => c.addCrunchTab(me))
      .catch(() => toast("The crunch-week tab could not load.", "error"));
  }
}
const showSettings = () => {
  Promise.all([legacyAccount(), import("/assets/settings.js")]).then(([me, mod]) => openSettings(me, mod))
    .catch(() => toast("Settings could not load.", "error"));
};
$("barEnd").prepend(fileWork);
// Returning from "Sign in with GitHub": the server redirects to
// /board?settings=account&github=<linked|error>[&reason=...]. Open Settings with
// the outcome, then drop the params so a reload is quiet (legacy board.html's rule).
const gh = /[?&]github=([a-z]+)(?:&reason=([a-z_]+))?/.exec(location.search);
if (gh) {
  const outcome = gh[1] === "linked" ? "linked" : (gh[2] || "github");
  const q = new URLSearchParams(location.search);
  ["settings", "github", "reason"].forEach((k) => q.delete(k));
  try { history.replaceState(null, "", location.pathname + (q.size ? "?" + q : "") + location.hash); } catch { /* ignore */ }
  Promise.all([legacyAccount(), import("/assets/settings.js")]).then(([me, mod]) => { if (me) openSettings(me, mod, outcome); })
    .catch(() => toast("Settings could not load.", "error"));
}
// Returning from "Confirm with SSO" (TASK-1415): the step-up lands on
// /board?stepup=1. Reopen Settings, which remembers the Agent credentials pane,
// so the person can create the credential inside the step-up window.
if (/[?&]stepup=1(?:&|$)/.test(location.search)) {
  const q = new URLSearchParams(location.search);
  q.delete("stepup");
  try { history.replaceState(null, "", location.pathname + (q.size ? "?" + q : "") + location.hash); } catch { /* ignore */ }
  Promise.all([legacyAccount(), import("/assets/settings.js")]).then(([me, mod]) => { if (me) openSettings(me, mod); })
    .catch(() => toast("Settings could not load.", "error"));
}
syncVisibility();
syncDash();
// The crunch-week banner, for every signed-in person while it is active. It
// reads its own route and draws nothing when off or refused.
import("./views/settings/crunch-banner.js").then((m) => m.mountCrunchBanner($("bar"))).catch(() => { /* no banner */ });

if (!ONLY) placeholder(graphPane, "Loading the graph…", "");

// The workitem types and the ladder (TASK-1391), read once. A failed read is
// never fatal: the store keeps FALLBACK_VOCABULARY, the lists the board had
// hard-coded before (core/vocabulary.js).
const VOCAB = fetchJSON("/api/vocabulary").catch(() => null);

Promise.all([fetchJSON("/api/me"), fetchJSON(GRAPH_URL), VOCAB]).then(async ([me, graph, vocabBody]) => {
  // The name opens the user menu: Settings, Change password, Sign out. On a
  // phone, where the panes have no bars, it also switches the dashboard and the
  // work board (`who-phone`, shown only there by layout.css).
  /** @param {"board"|"dashboard"} k */
  const flip = (k) => store.set({ [k]: !store.get()[k] });
  mountUserMenu($("who"), me, {
    onSettings: showSettings,
    extra: ONLY ? [] : [
      { label: "Work board", onClick: () => flip("board"), pressed: () => !!store.get().board, cls: "who-phone" },
      { label: "Dashboard", onClick: () => flip("dashboard"), pressed: () => !!store.get().dashboard, cls: "who-phone" },
    ],
  });
  // `role` decides which owner-only reads a pane may make (the panel's Configuration).
  store.set({ me: (me && me.username) || null, role: (me && me.role) || null });
  setCsrf((me && me.csrf_token) || null);
  // An SSO person with no personal password yet (TASK-1442): `/api/me` says so
  // with `password_setup`, and only then is the notice's module loaded.
  if (me && me.password_setup && !ONLY) {
    import("./views/settings/password-notice.js").then((m) => m.mountPasswordNotice($("bar"), me))
      .catch(() => { /* no notice */ });
  }
  store.set({ vocab: vocabularyFrom(vocabBody) });
  const vocab = () => /** @type {import("./core/vocabulary.js").Vocabulary} */ (store.get().vocab);
  const ix = buildIndex(graph, vocab());
  // AFTER A WRITE, REFETCH; NEVER PATCH ONE NODE. Rollups, attention and the
  // board's columns all move when one status does, and they live on other
  // nodes. A pane that writes sets `stale`. The index is replaced IN PLACE, so
  // every pane's reference stays good, and `rev` tells the panes to redraw.
  store.subscribe(["stale"], () => {
    fetchJSON(GRAPH_URL).then((g) => {
      Object.assign(ix, buildIndex(g, vocab()));
      store.set({ rev: (Number(store.get().rev) || 0) + 1 });
    }).catch(() => { /* the next write or reload tries again */ });
  });
  if (!ix.byId.size) { placeholder(graphPane, "Nothing to show yet", "Atlas has no nodes you can see."); return; }
  // A deep link names a node and a focus but no board scope. Scope the board to
  // the linked world node, else to the focus, as a click would have.
  const s0 = store.get();
  if (!s0.scope) {
    const sel = s0.selected ? ix.byId.get(String(s0.selected)) : null;
    store.set({ scope: sel && !sel.isWork ? sel.id : s0.focus || null });
  }
  if (!ONLY) mountGraph(graphPane, store, ix, { crumbs: $("crumbs"), tools: $("barTools") });
  $("searchSlot").append(searchBox(store, ix));

  // The board and the panel are loaded on demand, so a failure in one never
  // blanks the graph.
  const [{ mountBoard }, { mountPanel }] = await Promise.all([
    import("./views/work/board.js").catch(() => ({ mountBoard: null })),
    import("./views/panel/panel.js").catch(() => ({ mountPanel: null })),
  ]);
  if (mountBoard) mountBoard(boardPane, store, ix);
  else placeholder(boardPane, "Work board", "The work board could not load.");
  if (mountPanel) mountPanel(panelPane, store, ix);
  else placeholder(panelPane, "Node panel", "The panel could not load.");
}).catch((err) => {
  // fetchJSON has already navigated on an auth failure; anything else is shown.
  if (!String(err && err.message).startsWith("AUTH")) placeholder(graphPane, "Could not load the graph", String(err && err.message));
});

// For the console while the board is being built; nothing reads it.
Object.defineProperty(window, "__atlasStore", { value: store });
