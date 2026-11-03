// @ts-check
// views/panel/panel.js — the node panel (EPIC-atlas-node-panel-v2): a
// full-height dashboard for whatever is selected.
//
// The head is drawn at once from the graph index. The tabs are the legacy
// panel's minus Work, which the work board replaces: Overview, Intent,
// History, Docs and Links. Their bodies come from `/api/node/{id}`, fetched
// once per selection.
// - History reads `/api/events` on first open.
// - Comments (an epic, story or task only) are counted by one probe per
//   selection, for the badge, and read in full when the tab opens.
// - Docs read `/api/node/{id}/doc` per document, on open.
// - Decisions (ADR 0045) shows on a project, or on any node with ADRs recorded;
//   one small probe per selection decides, and an old server's 404 hides it.
// - Configuration (STORY-atlas-config-panel) shows, read-only, to an instance
//   owner on a node `/api/config` names as a part; nobody else asks for it.
// The chosen tab is remembered per viewer.

import { fetchJSON } from "../../core/api.js";
import { prefs } from "../../core/prefs.js";
import { isHub } from "../../core/graph-model.js";
import { uniqueShorts } from "../../core/shortlabel.js";
import { el, button, iconSvg, attChip, statusPill } from "../../ui/dom.js";
import { runDiagrams } from "../../ui/md.js";
import { renderOverview } from "./overview.js";
import { renderIntent } from "./intent.js";
import { renderHistory } from "./history.js";
import { renderDocs } from "./docs.js";
import { renderLinks } from "./links.js";
import { renderComments, probeComments } from "./comments.js";
import { renderDecisions, probeDecisions, adrParam } from "./adrs.js";
import { loadConfig, partOf, renderConfig } from "./config.js";
import { searchBox } from "../search.js";

const TABS = /** @type {const} */ (["overview", "intent", "comments", "history", "docs", "links", "decisions", "config"]);
const LABEL = { overview: "Overview", intent: "Intent", comments: "Comments", history: "History", docs: "Docs", links: "Links", decisions: "Decisions", config: "Configuration" };

/**
 * @param {HTMLElement} pane
 * @param {import("../../core/store.js").Store} store
 * @param {import("../../core/graph-model.js").Index} ix
 */
export function mountPanel(pane, store, ix) {
  let seq = 0;
  /** @type {string} */
  let tab = prefs.recall("panel.tab", "overview");
  if (!TABS.includes(/** @type {any} */ (tab))) tab = "overview";

  function render() {
    const id = /** @type {string|null} */ (store.get().selected);
    pane.replaceChildren();
    if (!id) {
      if (document.documentElement.hasAttribute("data-only")) pane.append(el("div", "placeholder", "Select a node in the main window, and it opens here."));
      return;
    }
    const mine = ++seq;
    const n = ix.byId.get(id);
    const ctx = { store, ix, id, node: n || null, full: /** @type {any} */ (null), config: /** @type {any} */ (null), me: /** @type {string|null} */ (store.get().me), selectTab, openDoc: /** @type {string|null} */ (null) };

    // ---- head, from the index: on screen at once
    const head = el("div", "phead");
    const close = button("×", () => store.set({ selected: null }), "pclose"); close.setAttribute("aria-label", "Close the panel");
    const ty = el("div", "ptype"); ty.append(iconSvg(n ? n.type : "_", "ti"), n ? n.type : "node");
    const pname = el("h2", "pname", n ? n.name : id);
    head.append(close, ty, pname);
    const summary = el("div", "psummary"); head.append(summary);
    const meta = el("div", "pmeta");
    if (n) {
      meta.append(attChip("attention: " + n.attention, n.attention));
      if (n.state) meta.append(attChip("state: " + n.state, n.state));
      if (n.urgency && n.urgency !== "none") meta.append(el("span", "chip", "urgency: " + n.urgency));
      if (n.isWork && n.status) meta.append(statusPill(n.status));
      const s = store.get();
      if (s.focus && isHub(ix, id, /** @type {string} */ (s.focus), { links: /** @type {any} */ (s.links) })) meta.append(el("span", "chip", "hub"));
    }
    head.append(meta);
    const short = n ? uniqueShorts([{ ...n, parent: null }]).get(id) : null;
    head.append(el("div", "pid", id + (short && short !== id ? "  ·  short: " + short : "")));
    const act = el("div", "pactions");
    const s0 = store.get();
    const fb = button(s0.focus === id ? "In focus" : "Focus here", () => store.set({ focus: id, scope: id }), "btn primary");
    fb.disabled = s0.focus === id || !n;
    const stars = /** @type {string[]} */ (s0.stars || []);
    const sb = button(stars.includes(id) ? "★ Starred" : "☆ Star", () => {
      const cur = /** @type {string[]} */ (store.get().stars || []);
      store.set({ stars: cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id] });
      render();
    });
    sb.setAttribute("aria-pressed", String(stars.includes(id)));
    act.append(fb, sb);
    if (n) {
      // How is this node connected to another (TASK-1298)? Pick one; the graph draws the routes.
      const cb = button("Connected to…", () => { pick.hidden = !pick.hidden; if (!pick.hidden) /** @type {HTMLInputElement} */ (pick.querySelector("input")).focus(); });
      cb.title = "Find the routes between this node and another one";
      const pick = searchBox(store, ix, { id: "pathPick", placeholder: "Connected to which node?", onPick: (to) => store.set({ path: { from: id, to } }) });
      pick.classList.add("path-pick"); pick.hidden = true;
      act.append(cb);
      queueMicrotask(() => act.after(pick));
    }
    if (!document.documentElement.hasAttribute("data-only")) {
      const pop = button("Open in a window ↗", () => window.dispatchEvent(new CustomEvent("atlas:popout", { detail: "panel" })), "linkbtn");
      pop.title = "Move this panel to its own window, e.g. on a second monitor";
      act.append(pop);
    }
    head.append(act);
    pane.append(head);

    // ---- tabs
    const bar = el("div", "ptabs"); bar.setAttribute("role", "tablist"); bar.setAttribute("aria-label", "Node detail sections");
    /** @type {Record<string, HTMLElement>} */ const bodies = {};
    /** @type {Record<string, HTMLButtonElement>} */ const btns = {};
    for (const k of TABS) {
      const b = button(LABEL[k], () => selectTab(k), "ptab"); b.setAttribute("role", "tab"); b.id = "ptab-" + k;
      bar.append(b); btns[k] = b;
      const body = el("div", "pbody"); body.setAttribute("role", "tabpanel"); body.setAttribute("aria-labelledby", b.id);
      bodies[k] = body;
    }
    pane.append(bar, ...TABS.map((k) => bodies[k]));
    /** @param {string} k @param {number|string|null} v */
    function badge(k, v) {
      const b = btns[k]; if (!b) return;
      b.querySelector(".pbadge")?.remove();
      if (v) b.append(el("span", "pbadge", String(v)));
    }
    if (n) { badge("docs", n.docs.length); badge("intent", n.intentCount || null); }

    /** @type {Record<string, boolean>} */ const built = {};
    function build(/** @type {string} */ k) {
      if (built[k] || !ctx.full) return;
      built[k] = true;
      const body = bodies[k];
      body.replaceChildren();
      if (k === "overview") renderOverview(body, ctx);
      else if (k === "intent") badge("intent", renderIntent(body, ctx));
      else if (k === "comments") renderComments(body, ctx, (total) => badge("comments", total));
      else if (k === "history") renderHistory(body, ctx, (total) => badge("history", total));
      else if (k === "docs") renderDocs(body, ctx);
      else if (k === "links") badge("links", renderLinks(body, ctx));
      else if (k === "decisions") renderDecisions(body, ctx, (total) => badge("decisions", total));
      else if (k === "config") renderConfig(body, ctx.config);
      runDiagrams(body);
    }
    function selectTab(/** @type {string} */ k, remember = true) {
      if (btns[k] && btns[k].hidden) k = "overview";
      tab = k; if (remember) prefs.remember("panel.tab", k);
      for (const t of TABS) { const on = t === k; btns[t].setAttribute("aria-selected", String(on)); btns[t].tabIndex = on ? 0 : -1; bodies[t].hidden = !on; }
      build(k);
      runDiagrams(bodies[k]);
    }
    ctx.selectTab = selectTab;
    for (const k of TABS) bodies[k].append(el("div", "empty", "Loading…"));
    // Decisions: visible at once on a project, otherwise only once the probe finds ADRs.
    const isProject = !!(n && n.type === "project");
    btns.decisions.hidden = !isProject;
    btns.config.hidden = true;
    // Comments: on a workitem only (the API refuses anything else with a 409).
    btns.comments.hidden = !(n && n.isWork);
    if (n && n.isWork) probeComments(id).then((total) => { if (mine === seq) badge("comments", total); });
    const remembered = tab;
    selectTab(tab, false);
    probeDecisions(id).then((total) => {
      if (mine !== seq) return;
      btns.decisions.hidden = !(isProject && total !== null) && !total;
      badge("decisions", total || null);
      if (btns.decisions.hidden) { if (tab === "decisions") selectTab("overview", false); return; }
      if ((total && adrParam(location.search)) || remembered === "decisions") selectTab("decisions", false);
    });

    // Configuration: an owner only (the route is owner-only, and a 403 on a read
    // would send the browser to the login page), on a node the API names as a part.
    if (store.get().role === "owner") {
      loadConfig().then((cfg) => {
        if (mine !== seq) return;
        const part = partOf(cfg, id);
        if (!part) return;
        ctx.config = part;
        btns.config.hidden = false;
        badge("config", (part.settings || []).length || null);
        if (remembered === "config") selectTab("config", false);
      }, () => { /* not shown; the other tabs are unaffected */ });
    }

    fetchJSON(`/api/node/${encodeURIComponent(id)}?intents=all`).then((full) => {
      if (mine !== seq) return;      // another node was selected meanwhile
      ctx.full = full;
      // A node the index does not hold yet (one just filed, before the refetch
      // lands) still gets its name and type in the head, from the full read.
      if (!n && full.name) {
        pname.textContent = full.name;
        ty.replaceChildren(iconSvg(full.type || "_", "ti"), full.type || "node");
      }
      if (full.summary) summary.textContent = full.summary;
      const own = (full.intents && full.intents.own) || [], inh = (full.intents && full.intents.inherited) || [];
      badge("intent", own.length + inh.length || null);
      build(tab);
    }).catch((err) => {
      if (mine !== seq) return;
      for (const k of TABS) bodies[k].replaceChildren(el("div", "empty", "Could not load this node: " + (err && err.message)));
    });
  }

  store.subscribe(["selected", "rev"], render);
  render();
}
