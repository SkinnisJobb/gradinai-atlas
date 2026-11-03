// @ts-check
// views/access/access.js — groups and project role bindings (STORY-oss-11, ADR 0041 §3).
//
// A self-contained overlay, lazy-imported from the bar's "Access" button, which
// app.js shows to an instance owner only (a member never issues a request that
// 403s, the legacy Settings rule). It owns its DOM and its stylesheet, and
// talks to four routes in backend/routers/access.py:
//   GET  /api/access/groups                      groups and their members
//   POST /api/access/groups, PUT .../{id}/members, DELETE .../{id}
//   GET  /api/access/projects                    projects, bindings, roster
//   PUT  /api/access/project/{id}/bindings       the WHOLE list, replaced
// Every list is edited as a draft and saved whole, because the API replaces;
// core/access-model.js holds the rules the draft follows.
//
// The bindings are inert until ATLAS_RBAC=projects (they can be prepared before
// the switch is flipped), and the overlay says which mode the server is in.

import { el, button } from "../../ui/dom.js";
import { sendJSON } from "../../core/api.js";
import { toast } from "../../ui/toast.js";
import { ROLES, grantableRoles, setBinding, describe, candidates, localMembers, groupable } from "../../core/access-model.js";

const CSS = "/assets/v2/views/access/access.css";

/** A read that reports a refusal to this view instead of bouncing to login. @param {string} url */
async function getJSON(url) {
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || body.error || "HTTP " + r.status);
  return body;
}

function ensureStyles() {
  if (document.querySelector(`link[href="${CSS}"]`)) return;
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = CSS;
  document.head.append(l);
}

/** @type {{overlay: HTMLElement, body: HTMLElement}|null} */
let ui = null;
/** @type {{project: string|null}} */
const view = { project: null };

function build() {
  const overlay = el("div", "settings-overlay");
  overlay.id = "accessOverlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", "Access: groups and project roles");
  const sheet = el("div", "settings-sheet acc-sheet");
  const head = el("div", "settings-head");
  const close = button("×", () => { overlay.hidden = true; }, "settings-close");
  close.setAttribute("aria-label", "Close");
  head.append(el("h2", null, "Access: groups and project roles"), close);
  const body = el("div", "settings-body");
  sheet.append(head, body);
  overlay.append(sheet);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.hidden = true; });
  overlay.addEventListener("keydown", (e) => { if (e.key === "Escape") overlay.hidden = true; });
  document.body.append(overlay);
  return { overlay, body };
}

export function openAccess() {
  ensureStyles();
  if (!ui) ui = build();
  ui.overlay.hidden = false;
  return render();
}

async function render() {
  if (!ui) return;
  const body = ui.body;
  body.replaceChildren(el("p", "acc-empty", "Loading…"));
  let projects, groups;
  try {
    [projects, groups] = await Promise.all([
      getJSON("/api/access/projects"),
      getJSON("/api/access/groups").catch(() => null),  // owner-only; a project maintainer has none
    ]);
  } catch (err) {
    body.replaceChildren(el("p", "acc-error", "Access could not load: " + String(err && /** @type {Error} */ (err).message)));
    return;
  }
  const accounts = projects.accounts || [];
  const groupRows = (groups && groups.groups) || projects.groups || [];
  body.replaceChildren(modeNote(projects.rbac),
    ...(groups ? [groupsSection(groupRows, accounts)] : []),
    projectsSection(projects.projects || [], accounts, groupRows));
}

/** @param {any} rbac */
function modeNote(rbac) {
  const p = el("p", "acc-mode");
  if (rbac && rbac.enabled) {
    p.append("Project RBAC is ", el("b", null, "on"), `. Unbound projects are ${rbac.default_project_visibility}; ` +
      `a shared project gives every account the role ${rbac.shared_project_role}.`);
  } else {
    p.append("Project RBAC is ", el("b", null, "off"), " (ATLAS_RBAC=off): groups and bindings are stored, " +
      "and take effect only when the server is switched to ATLAS_RBAC=projects. Until then access is the " +
      "visibility and grants under Settings.");
  }
  return p;
}

// ---- groups ------------------------------------------------------------------
/** @param {any[]} groups @param {any[]} accounts */
function groupsSection(groups, accounts) {
  const sec = el("section", "acc-section");
  sec.append(el("h3", "acc-h3", "Groups"));
  const name = /** @type {HTMLInputElement} */ (el("input"));
  name.type = "text"; name.placeholder = "New group name"; name.maxLength = 128;
  name.setAttribute("aria-label", "New group name");
  const create = button("Create group", () => act(
    sendJSON("POST", "/api/access/groups", { name: name.value }), `Group “${name.value.trim()}” created.`));
  const row = el("div", "acc-row");
  row.append(name, create);
  sec.append(row);
  if (!groups.length) sec.append(el("p", "acc-empty", "No groups yet."));
  const people = groupable(accounts);
  for (const g of groups) sec.append(groupCard(g, people));
  return sec;
}

/** @param {any} g @param {any[]} people */
function groupCard(g, people) {
  const card = el("div", "acc-card");
  const head = el("div", "acc-card-head");
  const title = el("span", null, g.name);
  if (g.source && g.source !== "local") title.append(" ", el("span", "acc-faint", `(${g.source})`));
  head.append(title, button("Delete group", () => {
    if (!confirm(`Delete the group “${g.name}”, its memberships and every role it holds?`)) return;
    act(sendJSON("DELETE", `/api/access/groups/${encodeURIComponent(g.id)}`, {}), `Group “${g.name}” deleted.`);
  }));
  const chosen = new Set(localMembers(g));
  const box = el("div", "acc-members");
  if (!people.length) box.append(el("span", "acc-faint", "No people to add."));
  for (const a of people) {
    const lab = el("label");
    const cb = /** @type {HTMLInputElement} */ (el("input"));
    cb.type = "checkbox"; cb.checked = chosen.has(a.user_id);
    cb.addEventListener("change", () => { if (cb.checked) chosen.add(a.user_id); else chosen.delete(a.user_id); });
    lab.append(cb, a.name && a.name !== a.username ? `${a.name} (${a.username})` : a.username);
    box.append(lab);
  }
  const idp = (g.members || []).filter((/** @type {any} */ m) => m.source !== "local").length;
  card.append(head, box);
  if (idp) card.append(el("p", "acc-faint", `${idp} member(s) come from the identity provider and are not edited here.`));
  card.append(button("Save members", () => act(
    sendJSON("PUT", `/api/access/groups/${encodeURIComponent(g.id)}/members`, { members: [...chosen] }),
    `Members of “${g.name}” saved.`)));
  return card;
}

// ---- project roles ------------------------------------------------------------
/** @param {any[]} projects @param {any[]} accounts @param {any[]} groups */
function projectsSection(projects, accounts, groups) {
  const sec = el("section", "acc-section");
  sec.append(el("h3", "acc-h3", "Project roles"));
  if (!projects.length) { sec.append(el("p", "acc-empty", "No project you can administer.")); return sec; }
  if (!view.project || !projects.some((p) => p.id === view.project)) view.project = projects[0].id;
  const pick = /** @type {HTMLSelectElement} */ (el("select"));
  pick.setAttribute("aria-label", "Project");
  for (const p of projects) {
    const o = /** @type {HTMLOptionElement} */ (el("option", null, `${p.name || p.id} (${p.id}) · ${p.visibility}`));
    o.value = p.id; o.selected = p.id === view.project;
    pick.append(o);
  }
  const holder = el("div");
  pick.addEventListener("change", () => { view.project = pick.value; draw(); });
  const row = el("div", "acc-row");
  row.append(el("span", "acc-faint", "Project"), pick);
  sec.append(row, holder);

  function draw() {
    const p = projects.find((x) => x.id === view.project);
    holder.replaceChildren(p ? bindingsEditor(p, accounts, groups) : el("p", "acc-empty", ""));
  }
  draw();
  return sec;
}

/** @param {any} p @param {any[]} accounts @param {any[]} groups */
function bindingsEditor(p, accounts, groups) {
  const wrap = el("div");
  /** @type {import("../../core/access-model.js").Binding[]} */
  let draft = (p.bindings || []).map((/** @type {any} */ b) => ({ ...b }));
  const grantable = p.grantable_up_to || null;
  const roles = grantableRoles(grantable);

  function roleSelect(/** @type {string} */ current, /** @type {boolean} */ locked) {
    const s = /** @type {HTMLSelectElement} */ (el("select"));
    s.setAttribute("aria-label", "Role");
    const offered = locked || !ROLES.includes(current) ? [current] : roles;
    for (const r of offered) {
      const o = /** @type {HTMLOptionElement} */ (el("option", null, r));
      o.value = r; o.selected = r === current;
      s.append(o);
    }
    s.disabled = locked;
    return s;
  }

  function redraw() {
    const table = el("table", "acc-table");
    const thead = el("thead"), tr = el("tr");
    tr.append(el("th", null, "Who"), el("th", null, "Role"), el("th", null, ""));
    thead.append(tr);
    const tbody = el("tbody");
    if (!draft.length) {
      const r = el("tr"), td = el("td", "acc-empty", "Nobody is bound on this project.");
      td.setAttribute("colspan", "3"); r.append(td); tbody.append(r);
    }
    for (const b of draft) {
      const { label, locked } = describe(b, accounts, groups, grantable);
      const r = el("tr");
      const sel = roleSelect(b.role, locked);
      sel.addEventListener("change", () => { draft = setBinding(draft, b.principal_kind, b.principal_id, sel.value); redraw(); });
      const rm = button("Remove", () => { draft = setBinding(draft, b.principal_kind, b.principal_id, null); redraw(); });
      rm.disabled = locked;
      const tdSel = el("td"), tdRm = el("td");
      tdSel.append(sel); tdRm.append(rm);
      r.append(el("td", null, label), tdSel, tdRm);
      tbody.append(r);
    }
    table.append(thead, tbody);

    const add = el("div", "acc-row");
    const who = /** @type {HTMLSelectElement} */ (el("select"));
    who.setAttribute("aria-label", "Person, agent or group to bind");
    for (const c of candidates(draft, accounts, groups)) {
      const o = /** @type {HTMLOptionElement} */ (el("option", null, c.label));
      o.value = c.kind + ":" + c.id;
      who.append(o);
    }
    const role = roleSelect(roles.includes("editor") ? "editor" : roles[0] || "viewer", !roles.length);
    const addBtn = button("Add", () => {
      if (!who.value) return;
      const [kind, ...rest] = who.value.split(":");
      draft = setBinding(draft, /** @type {"user"|"group"} */ (kind), rest.join(":"), role.value);
      redraw();
    });
    addBtn.disabled = !who.options.length || !roles.length;
    add.append(el("span", "acc-faint", "Add"), who, el("span", "acc-faint", "as"), role, addBtn);

    const save = button("Save roles", () => act(
      sendJSON("PUT", `/api/access/project/${encodeURIComponent(p.id)}/bindings`, { bindings: draft }),
      `Roles on ${p.name || p.id} saved.`));
    const foot = el("div", "acc-row");
    foot.append(save, el("span", "acc-faint", `You may grant up to ${grantable || "nothing"} here.`));
    wrap.replaceChildren(table, add, foot);
  }
  redraw();
  return wrap;
}

/** Run a write; on success say so and redraw from the server's answer. @param {Promise<any>} p @param {string} ok */
function act(p, ok) {
  return p.then(() => { toast(ok); return render(); })
    .catch((err) => toast(String(err && err.message || err), "error"));
}
