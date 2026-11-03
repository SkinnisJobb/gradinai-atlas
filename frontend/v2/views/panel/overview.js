// @ts-check
// views/panel/overview.js — the Overview tab: the node's dashboard.
//
// It carries what the legacy Overview carries, in the order the questions come:
// - FIRST, what this is for: intent titles. The statements are on Intent.
// - FOR WORK, the small facts: status, who, what it works on, epic, story and parent.
// - HOW FAR ALONG: tiles and a breakdown of the rolled-up work.
// - WHAT IT IS: the description (abstract, and the rest under More), then
//   acceptance and result for work.
// - WHERE TO READ MORE: the node's documents.
// - WHERE IT SITS: its containers and what it holds.

import { workCounts, ancestry, boundNode, epicOf, storyOf, cmpNodes, parentChoices } from "../../core/graph-model.js";
import { el, button, statusPill, attColor, priorityTag, labelChips } from "../../ui/dom.js";
import { md, runDiagrams } from "../../ui/md.js";
import { setStatus, STATUS_CHOICES, patchWorkitem, fetchJSON, getAcceptanceProposal, decideAcceptanceProposal } from "../../core/api.js";
import { acceptanceDiff, diffCounts, attestedLine } from "../../core/acceptance.js";
import { toast } from "../../ui/toast.js";

/** @param {HTMLElement} parent @param {string} [title] */
export function section(parent, title) {
  const s = el("section", "psec");
  if (title) s.append(el("h3", null, title));
  parent.append(s);
  return s;
}

/**
 * @param {HTMLElement} body
 * @param {{store: any, ix: import("../../core/graph-model.js").Index, id: string, node: any, full: any, me: string|null, selectTab: (k: string) => void, openDoc: string|null}} ctx
 */
export function renderOverview(body, ctx) {
  const { ix, id, full, store } = ctx;
  const n = ctx.node, isWork = !!(n && n.isWork);

  // ---- intent, first
  const own = (full.intents && full.intents.own) || [], inh = (full.intents && full.intents.inherited) || [];
  if (own.length || inh.length) {
    const s = section(body, "Intent"), strip = el("div", "istrip");
    for (const it of own) { const c = button(it.title || it.slug || "intent", () => ctx.selectTab("intent"), "ichip own"); c.title = it.statement || ""; strip.append(c); }
    if (inh.length) strip.append(button(`+ ${inh.length} inherited`, () => ctx.selectTab("intent"), "ichip"));
    s.append(strip);
  }

  // ---- the small authoritative facts, for work
  if (isWork) {
    const s = section(body, "This item"), dl = el("dl", "facts");
    /** @param {string} k @param {Node|string} v */
    const fact = (k, v) => { dl.append(el("dt", null, k)); const dd = el("dd"); dd.append(v); dl.append(dd); };
    fact("Status", statusControl(ctx, full.status || n.status));
    fact("Assignee", assigneeControl(ctx, full.assignee || null));
    const prio = priorityTag(full.priority || null); if (prio) fact("Priority", prio);
    const tags = labelChips(full.labels || []); if (tags) fact("Labels", tags);
    fact("Works on", bindingControl(ctx, full, boundNode(ix, id)));
    const ep = epicOf(ix, id); if (ep && ep !== id) fact("Epic", workLink(ctx, ep));
    const st = storyOf(ix, id); if (st) fact("Story", workLink(ctx, st));
    if (n.type !== "epic") fact("Parent", parentControl(ctx));
    if (full.completed_at) fact("Finished", new Date(full.completed_at).toLocaleDateString());
    s.append(dl);
  } else if (full.key) {
    // A project's key (STORY-oss-12): the prefix its tasks are numbered under. Read-only here.
    const s = section(body, "This project"), dl = el("dl", "facts");
    dl.append(el("dt", null, "Key"));
    const dd = el("dd"); dd.append(el("code", null, `${full.key}-n`)); dl.append(dd);
    s.append(dl);
  }

  // ---- how far along
  const c = workCounts(ix, id);
  if (c.count) {
    const s = section(body, isWork ? `Below this ${n.type}` : "Work on this node");
    const pending = Math.max(0, c.count - c.done - c.active - c.blocked);
    const tiles = el("div", "tiles");
    for (const [v, label, cls] of [[Math.round(c.completion * 100) + "%", "done", "good"], [c.active, "active", ""],
      [c.blocked, "blocked", c.blocked ? "bad" : ""], [n ? n.attentionCount : 0, "need attention", n && n.attentionCount ? "warn" : ""]]) {
      const t = el("div", "tile " + cls); t.append(el("b", null, String(v)), el("span", null, String(label))); tiles.append(t);
    }
    s.append(tiles);
    const parts = [[c.done, "var(--att-green)", "done"], [c.active, "var(--accent)", "active"], [c.blocked, "var(--att-red)", "blocked"], [pending, "var(--line-strong)", "not started"]];
    const stack = el("div", "stack"); stack.setAttribute("role", "img");
    stack.setAttribute("aria-label", parts.map((p) => `${p[0]} ${p[2]}`).join(", "));
    for (const [v, col] of parts) if (v) { const x = el("span"); x.style.width = (Number(v) / c.count * 100) + "%"; x.style.background = String(col); stack.append(x); }
    const key = el("div", "stack-key");
    for (const [v, col, label] of parts) { const k = el("span"), i = el("i"); i.style.background = String(col); k.append(i, `${v} ${label}`); key.append(k); }
    key.append(el("span", null, "of " + c.count));
    s.append(stack, key);
    if (c.open.length) s.append(button(`See the ${c.open.length} open item${c.open.length === 1 ? "" : "s"} on the work board →`,
      () => store.set({ board: true, scope: id }), "linkbtn"));
  }

  // ---- what it is
  const sd = section(body, "Description");
  const text = isWork ? full.body : (full.abstract || full.body);
  if (text) sd.append(md(text)); else sd.append(el("div", "empty", "No description written."));
  if (isWork) sd.append(textEditor(ctx, "Edit description", full.body || "", (v) => ({ body: v })));
  if (!isWork && full.detail) {
    const more = el("details", "more"); more.append(el("summary", null, "More"), md(full.detail));
    more.addEventListener("toggle", () => { if (/** @type {HTMLDetailsElement} */ (more).open) runDiagrams(more); });
    sd.append(more);
  }
  if (isWork) {
    const acc = full.acceptance || [];
    const sa = section(body, acc.length ? `Acceptance · ${acc.length}` : "Acceptance");
    if (!acc.length) sa.append(el("div", "empty", "No acceptance criteria yet."));
    else { const ol = el("ol", "acc"); for (const a of acc) { const li = el("li"); li.append(md(typeof a === "string" ? a : a.text || "")); ol.append(li); } sa.append(ol); }
    // A person may change acceptance from a browser session; an agent's token
    // may not (the API's `acceptance_is_not_yours_to_write`).
    sa.append(textEditor(ctx, "Edit acceptance", acc.map((a) => (typeof a === "string" ? a : a.text || "")).join("\n"),
      (v) => ({ acceptance: v.split("\n").map((x) => x.trim()).filter(Boolean) }), "One criterion per line."));
    sa.append(acceptanceReview(ctx));
    if (full.result) { const sr = section(body, full.result_heading || "Result"); sr.append(md(full.result)); }
  }

  // ---- where to read more
  if (n && n.docs.length) {
    const s = section(body, "Documentation"), list = el("div", "doclinks");
    for (const d of n.docs) {
      const b = button("", () => { ctx.openDoc = d.path; ctx.selectTab("docs"); }, "doclink");
      b.append(el("span", "dkind", d.kind || "doc"), el("span", null, d.title || d.path));
      list.append(b);
    }
    s.append(list);
  }

  // ---- where it sits
  if (!isWork) {
    const s = section(body, "Context");
    const anc = ancestry(ix, id), path = el("div", "path");
    if (anc.length) {
      path.append(el("span", "empty", "Part of"));
      anc.forEach((a, i) => { if (i) path.append(el("span", "sep", "›")); path.append(nodeLink(ctx, a)); });
    } else path.append(el("span", "empty", "A root — nothing contains it"));
    s.append(path);
    /** @type {Map<string, number>} */
    const kinds = new Map();
    for (const y of ix.adj.get(id) || []) if (y.t === "contains" && y.out && !ix.byId.get(y.o)?.isWork) { const t = /** @type {any} */ (ix.byId.get(y.o)).type; kinds.set(t, (kinds.get(t) || 0) + 1); }
    if (kinds.size) {
      const k = el("div", "kids");
      for (const [t, v] of [...kinds].sort((a, b) => b[1] - a[1])) k.append(el("span", "chip", `${v} ${t}${v === 1 ? "" : "s"}`));
      s.append(k);
    }
    const sk = ix.skills.get(id); if (sk) s.append(el("div", "empty", `${sk} skill${sk === 1 ? "" : "s"} attached`));
  }
}

/**
 * An agent's pending acceptance proposal, and the latest attested change
 * (STORY-atlas-acceptance-human-approved). Fetched for work items only, after
 * the section is drawn, so the panel never waits on it. The diff is against
 * the item's CURRENT acceptance, as the server reports it in the same answer.
 * Approve and Reject name the proposal by id, so a proposal replaced while the
 * panel was open is refused (409) rather than applied unseen.
 * @param {any} ctx
 */
function acceptanceReview(ctx) {
  const box = el("div", "accreview");
  getAcceptanceProposal(ctx.id).then((d) => {
    if (!d) return;
    const line = attestedLine(d.last_attested);
    if (line) {
      const p = el("div", "attested", line);
      if (d.last_attested.at) p.title = new Date(d.last_attested.at).toLocaleString();
      box.append(p);
    }
    const prop = d.pending;
    if (!prop) return;
    const card = el("div", "proposal");
    const diff = acceptanceDiff(d.acceptance || [], prop.proposed || []), n = diffCounts(diff);
    const who = (prop.proposed_by && prop.proposed_by.username) || "an agent";
    card.append(el("div", "proposal-head",
      `Proposed change by ${who}` + (prop.proposed_at ? ` · ${new Date(prop.proposed_at).toLocaleDateString()}` : "")
      + ` · +${n.added} −${n.removed}`));
    if (prop.note) card.append(el("div", "proposal-note", prop.note));
    const list = el("ul", "accdiff");
    list.setAttribute("aria-label", "Proposed acceptance, compared with the current list");
    for (const x of diff) {
      const li = el("li", "d-" + x.kind);
      li.append(el("span", "d-mark", x.kind === "added" ? "+" : x.kind === "removed" ? "−" : " "), md(x.text));
      li.title = x.kind === "same" ? "unchanged" : x.kind;
      list.append(li);
    }
    card.append(list);
    const err = el("div", "err"), row = el("div", "pactions");
    /** @param {"approve"|"reject"} verb */
    const decide = (verb) => {
      for (const b of row.querySelectorAll("button")) /** @type {HTMLButtonElement} */ (b).disabled = true;
      err.textContent = "";
      decideAcceptanceProposal(ctx.id, prop.id, verb).then(() => {
        toast(`${ctx.id}: proposal ${verb === "approve" ? "approved and applied" : "rejected"}`);
        ctx.store.set({ stale: Date.now() });
      }).catch((e) => {
        for (const b of row.querySelectorAll("button")) /** @type {HTMLButtonElement} */ (b).disabled = false;
        err.textContent = (e && e.message) || `Could not ${verb} the proposal.`;
      });
    };
    row.append(button("Approve", () => decide("approve"), "btn primary"), button("Reject", () => decide("reject")));
    card.append(row, err);
    box.append(card);
  }).catch(() => { box.append(el("div", "err", "Could not load the acceptance proposal.")); });
  return box;
}

/** A world node, as a chip that focuses it. @param {any} ctx @param {string} nodeId */
export function nodeLink(ctx, nodeId) {
  const n = ctx.ix.byId.get(nodeId);
  const b = button("", () => ctx.store.set({ focus: nodeId, selected: nodeId, scope: nodeId }), "nchip");
  if (!n) { b.textContent = nodeId; b.disabled = true; return b; }
  const dot = el("i"); dot.style.background = attColor(n.attention);
  b.append(dot, n.name); b.title = `${n.name} (${n.type}): focus it in the graph`;
  return b;
}
/** A workitem, as a link that selects it. @param {any} ctx @param {string} wid */
export function workLink(ctx, wid) {
  const n = ctx.ix.byId.get(wid);
  const b = button(n ? n.name : wid, () => ctx.store.set({ selected: wid }), "linkbtn");
  b.title = wid;
  return b;
}

/**
 * The status control: the same choices and the same route as the legacy panel
 * (`POST /api/node/{id}/status`). A refusal is shown under the control, in the
 * API's own words. Success asks app.js to refetch the graph, because the change
 * moves rollups and board columns on other nodes too.
 * @param {any} ctx @param {string} current
 */
function statusControl(ctx, current) {
  const box = el("div", "statusctl"), row = el("div", "seg"), err = el("div", "err");
  row.setAttribute("role", "group"); row.setAttribute("aria-label", "Set the status");
  for (const c of STATUS_CHOICES) {
    const b = button(c.label, () => {
      if (c.value === current) return;
      for (const x of row.querySelectorAll("button")) /** @type {HTMLButtonElement} */ (x).disabled = true;
      err.textContent = "";
      setStatus(ctx.id, c.value).then(() => {
        toast(`${ctx.id} is now ${c.label}`);
        ctx.store.set({ stale: Date.now() });
      }).catch((e) => {
        for (const x of row.querySelectorAll("button")) /** @type {HTMLButtonElement} */ (x).disabled = false;
        err.textContent = (e && e.message) || "Could not save that status.";
      });
    }, "");
    b.setAttribute("aria-pressed", String(c.value === current));
    row.append(b);
  }
  if (!STATUS_CHOICES.some((c) => c.value === current) && current) row.prepend(statusPill(current));
  box.append(row, err);
  return box;
}

/**
 * Who is doing it. The roster comes from the API (`/api/people?for=`), narrowed
 * to people who may see this item. It is fetched when the control is first
 * opened, not on every selection. Agents are not assignable (ADR 0034), and the
 * roster never offers one.
 * @param {any} ctx @param {any} current
 */
function assigneeControl(ctx, current) {
  const box = el("div", "statusctl"), sel = /** @type {HTMLSelectElement} */ (el("select", "bsel")), err = el("div", "err");
  sel.setAttribute("aria-label", "Assignee");
  const opt = (/** @type {string} */ v, /** @type {string} */ label) => { const o = /** @type {HTMLOptionElement} */ (el("option", null, label)); o.value = v; return o; };
  const you = (/** @type {any} */ p) => (p.name || p.username) + (p.username === ctx.me ? " (you)" : "");
  sel.append(opt("", "Unassigned"));
  if (current) sel.append(opt(current.id, you(current)));
  sel.value = current ? current.id : "";
  let loaded = false;
  const load = () => {
    if (loaded) return;
    loaded = true;
    fetchJSON(`/api/people?for=${encodeURIComponent(ctx.id)}`).then((d) => {
      for (const p of (d && d.people) || []) if (!current || p.id !== current.id) sel.append(opt(p.id, you(p)));
    }).catch(() => { loaded = false; err.textContent = "Could not load the list of people."; });
  };
  sel.addEventListener("mousedown", load);
  sel.addEventListener("focus", load);
  sel.addEventListener("change", () => {
    sel.disabled = true; err.textContent = "";
    patchWorkitem(ctx.id, { assignee: sel.value || null }).then((u) => {
      const a = u && u.assignee;
      toast(`${ctx.id}: ${a ? "assigned to " + (a.name || a.username) : "unassigned"}`);
      ctx.store.set({ stale: Date.now() });
    }).catch((e) => {
      sel.disabled = false; sel.value = current ? current.id : "";
      err.textContent = (e && e.message) || "Could not change the assignee.";
    });
  });
  box.append(sel, err);
  return box;
}

/**
 * What it works on: the one world node it is bound to (ADR 0028 allows at most
 * one). A task with no binding of its own works on its epic's node, and the
 * empty choice says so.
 * @param {any} ctx @param {any} full @param {string|null} effective
 */
function bindingControl(ctx, full, effective) {
  const own = (full.bindings || []).find((/** @type {any} */ b) => !b.edge_type || b.edge_type === "works_on");
  const ownId = own ? own.id : null;
  const box = el("div", "statusctl"), sel = /** @type {HTMLSelectElement} */ (el("select", "bsel")), err = el("div", "err");
  sel.setAttribute("aria-label", "Works on");
  const first = /** @type {HTMLOptionElement} */ (el("option", null,
    !ownId && effective ? `Inherited: ${ctx.ix.byId.get(effective)?.name || effective}` : "Not bound"));
  first.value = "";
  sel.append(first);
  const cmp = cmpNodes(ctx.ix);
  for (const wid of [...ctx.ix.byId.values()].filter((x) => !x.isWork).map((x) => x.id).sort(cmp)) {
    const w = ctx.ix.byId.get(wid);
    const o = /** @type {HTMLOptionElement} */ (el("option", null, `${w.name} · ${w.type}`));
    o.value = wid; sel.append(o);
  }
  sel.value = ownId || "";
  sel.addEventListener("change", () => {
    sel.disabled = true; err.textContent = "";
    patchWorkitem(ctx.id, { binding: sel.value || null }).then(() => {
      toast(`${ctx.id}: ${sel.value ? "now works on " + (ctx.ix.byId.get(sel.value)?.name || sel.value) : "binding removed"}`);
      ctx.store.set({ stale: Date.now() });
    }).catch((e) => {
      sel.disabled = false; sel.value = ownId || "";
      err.textContent = (e && e.message) || "Could not change what this works on.";
    });
  });
  if (effective) box.append(nodeLink(ctx, effective));
  box.append(sel, err);
  return box;
}

/**
 * Where it sits in the work hierarchy (TASK-1355): the workitem directly above
 * it, changed through PATCH /api/workitem's `parent`. The choices are the items
 * ranked above it, never itself or anything below it (`parentChoices`); the API
 * still decides, and its refusal is shown here. "No parent" detaches it.
 * @param {any} ctx
 */
function parentControl(ctx) {
  const cur = ctx.ix.workParent.get(ctx.id) || null;
  const box = el("div", "statusctl"), sel = /** @type {HTMLSelectElement} */ (el("select", "bsel")), err = el("div", "err");
  sel.setAttribute("aria-label", "Parent");
  const none = /** @type {HTMLOptionElement} */ (el("option", null, "No parent"));
  none.value = ""; sel.append(none);
  for (const wid of parentChoices(ctx.ix, ctx.id)) {
    const w = ctx.ix.byId.get(wid);
    const o = /** @type {HTMLOptionElement} */ (el("option", null, `${w.name} · ${w.type} · ${wid}`));
    o.value = wid; sel.append(o);
  }
  sel.value = cur || "";
  sel.addEventListener("change", () => {
    sel.disabled = true; err.textContent = "";
    patchWorkitem(ctx.id, { parent: sel.value || null }).then(() => {
      toast(`${ctx.id}: ${sel.value ? "moved under " + (ctx.ix.byId.get(sel.value)?.name || sel.value) : "parent removed"}`);
      ctx.store.set({ stale: Date.now() });
    }).catch((e) => {
      sel.disabled = false; sel.value = cur || "";
      err.textContent = (e && e.message) || "Could not move this item.";
    });
  });
  box.append(sel, err);
  return box;
}

/**
 * An Edit link that opens a textarea, saved through PATCH /api/workitem. The
 * server's answer is drawn after the refetch, never the typed text.
 * @param {any} ctx @param {string} label @param {string} value
 * @param {(v: string) => Record<string, unknown>} toFields @param {string} [hint]
 */
function textEditor(ctx, label, value, toFields, hint) {
  const box = el("div", "editor"), form = el("div", "editform"), err = el("div", "err");
  form.hidden = true;
  const ta = /** @type {HTMLTextAreaElement} */ (el("textarea", "edit-ta"));
  ta.value = value; ta.rows = Math.min(16, Math.max(5, value.split("\n").length + 1)); ta.setAttribute("aria-label", label);
  const open = button(label, () => { open.hidden = true; form.hidden = false; ta.focus(); }, "linkbtn");
  const save = button("Save", () => {
    save.disabled = true; err.textContent = "";
    patchWorkitem(ctx.id, toFields(ta.value)).then(() => { toast(`${ctx.id}: saved`); ctx.store.set({ stale: Date.now() }); })
      .catch((e) => { save.disabled = false; err.textContent = (e && e.message) || "Could not save."; });
  }, "btn primary");
  const cancel = button("Cancel", () => { ta.value = value; form.hidden = true; open.hidden = false; err.textContent = ""; });
  const row = el("div", "pactions");
  row.append(save, cancel);
  form.append(ta);
  if (hint) form.append(el("div", "empty", hint));
  form.append(row, err);
  box.append(open, form);
  return box;
}
