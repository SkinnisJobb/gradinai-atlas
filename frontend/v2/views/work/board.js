// @ts-check
// views/work/board.js — the work board (EPIC-atlas-work-board): Jira, scoped by
// the graph.
//
// WHAT IT SHOWS is `scopeItems(ix, store.scope, filters)`. Scope is the node
// focused or selected in the graph, and everything that node contains: work
// rolls up, never down. With no scope it shows all work.
// THREE VIEWS:
// - Epics: one card per epic in scope, with its progress. Clicking one opens
//   the board on that epic. Epics carry their own colour (--epic) everywhere.
// - Board: kanban columns by status, one lane per epic. A lane folds, and the
//   board can be narrowed to one epic.
// - Backlog: epic, story and task as a tree.
// IT IS LINKED TO THE GRAPH ONLY THROUGH THE STORE:
// - Hovering a card sets `hover` to the card's node, and the graph rings it.
// - Hovering a node in the graph outlines the cards in that node's scope.

import { scopeItems, boardColumn, boundNode, epicOf, storyOf, worldDesc, containsParents, cmpNodes, workCounts } from "../../core/graph-model.js";
import { shortLabel } from "../../core/shortlabel.js";
import { prefs } from "../../core/prefs.js";
import { el, button, iconSvg, statusPill, avatar, attColor, priorityTag, labelChips } from "../../ui/dom.js";
import { setStatus, STATUS_CHOICES } from "../../core/api.js";
import { toast } from "../../ui/toast.js";

/** The columns a card may be dropped on: the statuses the API lets a person set. */
const SETTABLE = new Set(STATUS_CHOICES.map((c) => c.value));

/** Keys in `BOARD_COLUMNS` order (core/graph-model.js); a card goes in `boardColumn(status)` (TASK-1379). */
const COLS = [
  { key: "proposed", label: "Proposed" }, { key: "pending", label: "To do" }, { key: "in_progress", label: "In progress" },
  { key: "blocked", label: "Blocked" }, { key: "done", label: "Done" },
];
const plural = (/** @type {number} */ k, /** @type {string} */ w) => `${k} ${w}${k === 1 ? "" : "s"}`;
/** How urgently a status wants looking at, for ordering lanes and lists. */
const RANK = /** @type {Record<string, number>} */ ({ blocked: 0, in_progress: 1, proposed: 2, pending: 3, done: 4 });
const rankOf = (/** @type {string} */ s) => { const c = boardColumn(s); return c && c in RANK ? RANK[c] : 9; };

/**
 * @param {HTMLElement} pane
 * @param {import("../../core/store.js").Store} store
 * @param {import("../../core/graph-model.js").Index} ix
 */
export function mountBoard(pane, store, ix) {
  const cmp = cmpNodes(ix);
  const B = {
    view: prefs.recall("work.view", "board"),
    who: prefs.recall("work.who", "all"),
    doneDays: prefs.recall("work.doneDays", 30),
    q: "",
    /** @type {string|null} the one epic the board is narrowed to */
    epic: prefs.recall("work.epic", null),
    /** @type {Set<string>} lanes folded shut, by epic id */
    fold: new Set(/** @type {string[]} */ (prefs.recall("work.fold", []))),
  };
  const save = () => {
    prefs.remember("work.view", B.view); prefs.remember("work.who", B.who); prefs.remember("work.doneDays", B.doneDays);
    prefs.remember("work.epic", B.epic); prefs.remember("work.fold", [...B.fold]);
  };
  const byId = (/** @type {string} */ id) => /** @type {import("../../core/graph-model.js").GNode} */ (ix.byId.get(id));

  /** A chip naming the world node a card belongs to; clicking it selects that node. @param {string|null} nodeId */
  function nodeChip(nodeId) {
    const b = /** @type {HTMLButtonElement} */ (el("button", "nchip")); b.type = "button";
    if (!nodeId || !ix.byId.has(nodeId)) { b.textContent = "unbound"; b.disabled = true; return b; }
    const n = byId(nodeId), dot = el("i");
    dot.style.background = attColor(n.attention);
    b.append(dot, shortLabel(n, containsParents(ix, nodeId)[0] || null));
    b.title = `${n.name} (${n.type}): show in the graph`;
    b.addEventListener("click", (e) => { e.stopPropagation(); store.set({ selected: nodeId, scope: nodeId }); });
    return b;
  }

  /** @param {string} id */
  function card(id) {
    const n = byId(id), c = el("article", "wcard st-" + n.status);
    c.tabIndex = 0; c.dataset.id = id; c.dataset.node = boundNode(ix, id) || "";
    const top = el("div", "wtop"); top.append(iconSvg(n.type, "ti"), el("span", "wid", id));
    const prio = priorityTag(n.priority); if (prio) top.append(prio);
    c.append(top);
    c.append(el("div", "wname", n.name));
    const tags = labelChips(n.labels); if (tags) c.append(tags);
    const st = storyOf(ix, id); if (st) c.append(el("div", "wctx", "in " + byId(st).name));
    const bot = el("div", "wbot"); bot.append(nodeChip(boundNode(ix, id)), avatar(n.assignee, /** @type {string|null} */ (store.get().me))); c.append(bot);
    hookRow(c, id);
    c.draggable = true;
    c.addEventListener("dragstart", (e) => {
      /** @type {DataTransfer} */ (e.dataTransfer).setData("text/x-atlas-work", id);
      /** @type {DataTransfer} */ (e.dataTransfer).effectAllowed = "move";
      c.classList.add("dragging");
    });
    c.addEventListener("dragend", () => c.classList.remove("dragging"));
    return c;
  }

  /**
   * Dropping a card on a column sets its status (TASK-1315). The card moves at
   * once. If the API refuses, the board redraws from the graph, so the card goes
   * back, and the refusal is shown.
   * @param {HTMLElement} cell @param {string} status
   */
  function dropTarget(cell, status) {
    if (!SETTABLE.has(status)) return;
    cell.dataset.status = status;
    cell.addEventListener("dragover", (e) => {
      if (![.../** @type {DataTransfer} */ (e.dataTransfer).types].includes("text/x-atlas-work")) return;
      e.preventDefault(); cell.classList.add("dropok");
    });
    cell.addEventListener("dragleave", () => cell.classList.remove("dropok"));
    cell.addEventListener("drop", (e) => {
      e.preventDefault(); cell.classList.remove("dropok");
      const id = /** @type {DataTransfer} */ (e.dataTransfer).getData("text/x-atlas-work");
      const n = ix.byId.get(id);
      if (!n || boardColumn(n.status) === status) return;
      const moved = /** @type {HTMLElement|null} */ (pane.querySelector(`.wcard[data-id="${CSS.escape(id)}"]`));
      if (moved) cell.append(moved);
      setStatus(id, status).then(() => {
        toast(`${id} moved to ${status.replace(/_/g, " ")}`);
        store.set({ stale: Date.now() });
      }).catch((err) => { toast(`${id}: ${(err && err.message) || "could not change status"}`, "error"); render(); });
    });
  }
  /** @param {HTMLElement} row @param {string} id */
  function hookRow(row, id) {
    row.addEventListener("click", () => store.set({ selected: id }));
    row.addEventListener("keydown", (e) => { if (e.key === "Enter") store.set({ selected: id }); });
    row.addEventListener("mouseenter", () => store.set({ hover: boundNode(ix, id), hoverFrom: "board" }));
    row.addEventListener("mouseleave", () => store.set({ hover: null, hoverFrom: null }));
    if (id === store.get().selected) row.classList.add("sel");
  }

  function render() {
    const s = store.get(), scope = /** @type {string|null} */ (s.scope);
    if (B.epic && ix.byId.get(B.epic)?.type !== "epic") B.epic = null;
    const inScope = scopeItems(ix, scope, { who: /** @type {any} */ (B.who), me: /** @type {string} */ (s.me || ""), q: B.q, doneDays: B.doneDays });
    const items = B.epic && B.view !== "epics" ? inScope.filter((i) => i === B.epic || epicOf(ix, i) === B.epic) : inScope;
    const keepScroll = pane.scrollTop, keepScrollX = pane.scrollLeft, hadFocus = document.activeElement && document.activeElement.id === "boardSearch";
    pane.replaceChildren();

    const hd = el("div", "bhead"), t = el("div", "btitle");
    t.append(el("span", "eyebrow", "Work"), el("h2", null, scope && ix.byId.has(scope) ? byId(scope).name : "All of Atlas"));
    if (scope && ix.byId.has(scope) && !byId(scope).isWork) {
      const below = worldDesc(ix, scope).size - 1;
      if (below) t.append(el("span", "bsub", `and the ${below} node${below === 1 ? "" : "s"} it contains`));
    }
    if (scope && scope !== s.focus) {
      const back = button(s.focus ? "← back to " + (byId(String(s.focus))?.name || s.focus) : "← all of Atlas",
        () => store.set({ scope: s.focus || null }), "linkbtn");
      t.append(back);
    }
    if (B.epic && B.view !== "epics") {
      const ep = el("span", "epicfilter"), x = button("×", () => { B.epic = null; save(); render(); }, "epicfilter-x");
      x.title = "Show every epic again"; x.setAttribute("aria-label", x.title);
      ep.append(iconSvg("epic", "ti"), button(byId(B.epic).name, () => store.set({ selected: /** @type {string} */ (B.epic) }), "linkbtn"), x);
      t.append(ep);
    }
    hd.append(t);
    /** @type {Record<string, number>} */
    const cnt = {};
    for (const id of items) { const st = boardColumn(byId(id).status); if (!st) continue; cnt[st] = (cnt[st] || 0) + 1; }
    const sum = el("div", "bsum");
    for (const [k, label] of [["in_progress", "in progress"], ["blocked", "blocked"], ["pending", "to do"], ["done", `done ≤ ${B.doneDays}d`]]) {
      if (k === "done" && !B.doneDays) continue;
      const x = el("span", "sumi st-" + k); x.append(el("b", null, String(cnt[k] || 0)), " " + label); sum.append(x);
    }
    hd.append(sum);

    const tools = el("div", "btools");
    /** @param {Array<[string,string]>} opts @param {string} cur @param {(v: string) => void} fn @param {string} label */
    const seg = (opts, cur, fn, label) => {
      const g = el("div", "seg"); g.setAttribute("role", "group"); g.setAttribute("aria-label", label);
      for (const [v, text] of opts) { const b = button(text, () => { fn(v); save(); render(); }, ""); b.setAttribute("aria-pressed", String(cur === v)); g.append(b); }
      return g;
    };
    tools.append(
      seg([["epics", "Epics"], ["board", "Board"], ["backlog", "Backlog"]], B.view, (v) => { B.view = v; }, "View"),
      seg([["all", "Everyone"], ["mine", "Mine"], ["unassigned", "Unassigned"]], B.who, (v) => { B.who = v; }, "Assignee"),
    );
    const qi = /** @type {HTMLInputElement} */ (el("input", "bsearch")); qi.type = "search"; qi.id = "boardSearch"; qi.placeholder = "Filter work…"; qi.value = B.q;
    qi.setAttribute("aria-label", "Filter work by name or id");
    qi.addEventListener("input", () => { B.q = qi.value; const at = qi.selectionStart; render(); const n = /** @type {HTMLInputElement} */ (document.getElementById("boardSearch")); n.focus(); try { n.setSelectionRange(at, at); } catch { /* not a text field */ } });
    const ds = /** @type {HTMLSelectElement} */ (el("select", "bsel")); ds.id = "boardDone"; ds.setAttribute("aria-label", "Finished work to show");
    for (const [v, label] of [[0, "Hide done"], [7, "Done ≤ 7 days"], [30, "Done ≤ 30 days"], [90, "Done ≤ 90 days"]]) {
      const o = /** @type {HTMLOptionElement} */ (el("option", null, String(label))); o.value = String(v); o.selected = v === B.doneDays; ds.append(o);
    }
    ds.addEventListener("change", () => { B.doneDays = Number(ds.value); save(); render(); });
    tools.append(qi, ds);
    if (!document.documentElement.hasAttribute("data-only")) {
      const pop = button("Open in a window ↗", () => window.dispatchEvent(new CustomEvent("atlas:popout", { detail: "board" })), "linkbtn");
      pop.title = "Move the work board to its own window, e.g. on a second monitor";
      tools.append(pop);
    }
    hd.append(tools);
    pane.append(hd);

    if (!items.length) {
      pane.append(el("div", "empty bempty", "No work matches here. Try Everyone, a longer done window, or a node higher up."));
    } else if (B.view === "board") kanban(items); else if (B.view === "epics") epicCards(items); else backlog(items);
    pane.style.setProperty("--bh", hd.offsetHeight + "px");
    pane.scrollTop = keepScroll; pane.scrollLeft = keepScrollX;
    if (hadFocus) { const n = document.getElementById("boardSearch"); if (n) n.focus(); }
    lightCards();
  }

  /** @param {string[]} items */
  function lanes(items) {
    /** @type {Map<string, {epic: string|null, items: string[]}>} */
    const m = new Map();
    for (const id of items) {
      const n = byId(id), e = n.type === "epic" ? id : epicOf(ix, id), k = e || "__none";
      if (!m.has(k)) m.set(k, { epic: e, items: [] });
      if (n.type !== "epic") /** @type {any} */ (m.get(k)).items.push(id);
    }
    const heat = (/** @type {{epic: string|null, items: string[]}} */ l) => Math.min(l.epic ? rankOf(byId(l.epic).status) : 9, ...l.items.map((i) => rankOf(byId(i).status)));
    return [...m.values()].sort((a, b) => (+!a.epic - +!b.epic) || heat(a) - heat(b) || (a.epic && b.epic ? byId(a.epic).name.localeCompare(byId(b.epic).name) : 0));
  }
  /** @param {{epic: string|null}} l @param {number} span */
  function laneHead(l, span) {
    const key = l.epic || "__none", shut = B.fold.has(key);
    const h = el("div", "lane-head" + (l.epic ? " epic" : "") + (shut ? " shut" : "")); h.style.gridColumn = `1 / span ${span}`;
    const fold = button(shut ? "▸" : "▾", () => { if (shut) B.fold.delete(key); else B.fold.add(key); save(); render(); }, "lane-fold");
    fold.setAttribute("aria-expanded", String(!shut)); fold.title = shut ? "Show this lane's cards" : "Fold this lane";
    h.append(fold);
    if (!l.epic) { h.append(el("span", "lane-name none", "Not in an epic"), el("span", "lane-count", plural(l.items.length, "card"))); return h; }
    const n = byId(l.epic), c = workCounts(ix, l.epic);
    const nm = button(n.name, () => store.set({ selected: /** @type {string} */ (l.epic) }), "lane-name"); nm.title = l.epic;
    h.append(iconSvg("epic", "ti"), nm, statusPill(n.status), nodeChip(boundNode(ix, l.epic)));
    if (c.count) h.append(progress(c));
    if (shut) h.append(el("span", "lane-count", plural(l.items.length, "card") + " here"));
    if (B.epic !== l.epic) h.append(button("Only this epic", () => { B.epic = l.epic; save(); render(); }, "linkbtn lane-only"));
    return h;
  }
  /** A progress bar and "done/count". @param {{count: number, done: number, completion: number}} c */
  function progress(c) {
    const pr = el("span", "lane-prog"), bar = el("span", "mini"), fill = el("span"); fill.style.width = (c.completion * 100) + "%";
    bar.append(fill); pr.append(bar, `${c.done}/${c.count}`);
    return pr;
  }
  /** One card per epic in scope: the lanes, seen from above. @param {string[]} items */
  function epicCards(items) {
    /** @type {Map<string, string[]>} */
    const m = new Map();
    let loose = 0;
    for (const id of items) {
      const e = byId(id).type === "epic" ? id : epicOf(ix, id);
      if (!e) { loose++; continue; }
      if (!m.has(e)) m.set(e, []);
      if (e !== id) /** @type {string[]} */ (m.get(e)).push(id);
    }
    const heat = (/** @type {string} */ e) => Math.min(rankOf(byId(e).status), .../** @type {string[]} */ (m.get(e)).map((i) => rankOf(byId(i).status)));
    const grid = el("div", "epics");
    for (const e of [...m.keys()].sort((a, b) => heat(a) - heat(b) || byId(a).name.localeCompare(byId(b).name))) {
      const n = byId(e), c = workCounts(ix, e), here = /** @type {string[]} */ (m.get(e));
      const k = el("article", "ecard st-" + n.status); k.tabIndex = 0; k.dataset.id = e; k.dataset.node = boundNode(ix, e) || "";
      const top = el("div", "etop"); top.append(iconSvg("epic", "ti"), el("span", "wid", e), statusPill(n.status)); k.append(top);
      k.append(el("h3", "ename", n.name));
      const bar = el("div", "ebar"), fill = el("span"); fill.style.width = (c.completion * 100) + "%"; bar.append(fill);
      k.append(bar, el("div", "epct", c.count ? `${Math.round(c.completion * 100)}% · ${c.done} of ${c.count} done` : "No work filed under it yet"));
      const nums = el("div", "enums");
      for (const [st, label] of [["in_progress", "in progress"], ["blocked", "blocked"], ["pending", "to do"]]) {
        const x = here.filter((i) => boardColumn(byId(i).status) === st).length;
        if (x) { const sp = el("span", "sumi st-" + st); sp.append(el("b", null, String(x)), " " + label); nums.append(sp); }
      }
      if (nums.childElementCount) k.append(nums);
      const bot = el("div", "wbot"); bot.append(nodeChip(boundNode(ix, e)), avatar(n.assignee, /** @type {string|null} */ (store.get().me))); k.append(bot);
      const open = button("Open on the board →", () => { B.epic = e; B.view = "board"; save(); render(); }, "linkbtn eopen");
      open.addEventListener("click", (ev) => ev.stopPropagation());
      k.append(open);
      hookRow(k, e);
      grid.append(k);
    }
    pane.append(grid);
    if (loose) pane.append(el("div", "empty bempty", `${plural(loose, "item")} in scope ${loose === 1 ? "is" : "are"} in no epic: see Board or Backlog.`));
  }
  /** @param {string[]} items */
  function kanban(items) {
    const used = COLS.filter((c) => (c.key !== "proposed" || items.some((i) => boardColumn(byId(i).status) === "proposed")) && (c.key !== "done" || B.doneDays));
    const grid = el("div", "kanban");
    grid.style.gridTemplateColumns = `repeat(${used.length}, minmax(240px, 1fr))`;
    grid.style.minWidth = `${used.length * 250 + 36}px`;
    for (const c of used) {
      const n = items.filter((i) => boardColumn(byId(i).status) === c.key && byId(i).type !== "epic").length;
      const h = el("div", "col-head st-" + c.key); h.append(el("span", null, c.label), el("b", null, String(n))); grid.append(h);
    }
    for (const l of lanes(items)) {
      if (!l.items.length && !l.epic) continue;
      grid.append(laneHead(l, used.length));
      if (B.fold.has(l.epic || "__none")) continue;
      for (const c of used) {
        const cell = el("div", "cell");
        dropTarget(cell, c.key);
        for (const id of l.items.filter((i) => boardColumn(byId(i).status) === c.key).sort(cmp)) cell.append(card(id));
        grid.append(cell);
      }
    }
    pane.append(grid);
  }
  /** @param {string[]} items */
  function backlog(items) {
    const set = new Set(items), list = el("div", "backlog");
    const bySt = (/** @type {string} */ a, /** @type {string} */ b) => (rankOf(byId(a).status) - rankOf(byId(b).status)) || cmp(a, b);
    const kids = (/** @type {string} */ id) => (ix.adj.get(id) || []).filter((y) => y.t === "contains" && y.out && set.has(y.o)).map((y) => y.o);
    /** @param {string} id @param {number} depth */
    const row = (id, depth) => {
      const n = byId(id), r = el("div", "brow st-" + n.status + " type-" + n.type); r.style.paddingLeft = (8 + depth * 18) + "px"; r.tabIndex = 0;
      r.dataset.id = id; r.dataset.node = boundNode(ix, id) || "";
      const nm = el("span", "bname"); nm.append(el("span", "bn", n.name));
      if (n.type === "epic") { const c = workCounts(ix, id); if (c.count) nm.append(progress(c)); }
      r.append(iconSvg(n.type, "ti"), nm, el("span", "wid", id), statusPill(n.status), nodeChip(boundNode(ix, id)), avatar(n.assignee, /** @type {string|null} */ (store.get().me)));
      hookRow(r, id); list.append(r);
    };
    /** @param {string} id @param {number} d */
    const walk = (id, d) => { row(id, d); for (const k of kids(id).sort(bySt)) walk(k, d + 1); };
    const tops = items.filter((i) => { const p = ix.workParent.get(i); return !p || !set.has(p); })
      .sort((a, b) => (+(byId(a).type !== "epic") - +(byId(b).type !== "epic")) || bySt(a, b));
    for (const id of tops) walk(id, 0);
    pane.append(list);
  }

  /** Outline the cards in the hovered node's scope (graph → board). */
  function lightCards() {
    // Only a hover that started in the graph lights cards; a card hovering itself would light its neighbours.
    const h = store.get().hoverFrom === "graph" ? /** @type {string|null} */ (store.get().hover) : null;
    const ws = h && ix.byId.has(h) && !byId(h).isWork ? worldDesc(ix, h) : null;
    for (const c of pane.querySelectorAll("[data-node]")) {
      const nd = /** @type {HTMLElement} */ (c).dataset.node;
      c.classList.toggle("lit", !!(ws && nd && ws.has(nd)));
    }
  }
  function markSelected() {
    const sel = store.get().selected;
    for (const c of pane.querySelectorAll("[data-id]")) c.classList.toggle("sel", /** @type {HTMLElement} */ (c).dataset.id === sel);
  }

  store.subscribe(["scope", "me", "focus", "rev"], render);
  store.subscribe(["hover"], lightCards);
  store.subscribe(["selected"], markSelected);
  render();
  return { render };
}
