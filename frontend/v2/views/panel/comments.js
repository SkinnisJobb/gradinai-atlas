// @ts-check
// views/panel/comments.js — the Comments tab: the conversation on an epic,
// story or task (`/api/workitem/{id}/comments`, routers/comments.py).
// Oldest first, newest at the bottom beside the box you write in.
// The author edits and deletes their own; an owner may also delete. The
// server enforces both, and these controls only decline to offer the rest.

import { fetchJSON, sendJSON } from "../../core/api.js";
import { el, button } from "../../ui/dom.js";
import { md } from "../../ui/md.js";
import { toast } from "../../ui/toast.js";

/** The comment count for the tab badge, or null when the item has none or the read fails. @param {string} id */
export function probeComments(id) {
  return fetchJSON(`/api/workitem/${encodeURIComponent(id)}/comments`)
    .then((r) => (r && r.count) || null, () => null);
}

/** What the author line says. Pure, so it can be tested. @param {any} c */
export function authorText(c) {
  const a = (c && c.author) || {};
  return a.name || a.username || "an account that no longer exists";
}

/**
 * May this viewer edit / delete this comment? Mirrors the server's rule.
 * @param {any} c @param {string|null} me  username @param {string|null} role
 */
export function mayChange(c, me, role) {
  const mine = !!me && !!c && !!c.author && c.author.username === me;
  return { edit: mine, del: mine || role === "owner" };
}

/**
 * @param {HTMLElement} body
 * @param {{id: string, me: string|null, store: any}} ctx
 * @param {(n: number|null) => void} onCount
 */
export function renderComments(body, ctx, onCount) {
  const list = el("div", "comments");
  list.append(el("div", "empty", "Loading…"));
  body.append(list, composer());
  const url = `/api/workitem/${encodeURIComponent(ctx.id)}/comments`;

  function load() {
    fetchJSON(url).then((r) => {
      const rows = (r && r.comments) || [];
      onCount(rows.length || null);
      list.replaceChildren();
      if (!rows.length) list.append(el("div", "empty", "No comments yet."));
      for (const c of rows) list.append(card(c));
    }).catch((e) => list.replaceChildren(el("div", "empty", "Could not load the comments: " + (e && e.message))));
  }

  function composer() {
    const box = el("div", "comment-new");
    const ta = /** @type {HTMLTextAreaElement} */ (el("textarea", "bsel comment-text"));
    ta.placeholder = "Write a comment (markdown)…";
    ta.setAttribute("aria-label", "New comment");
    const err = el("div", "err");
    const send = button("Comment", () => {
      if (!ta.value.trim()) { ta.focus(); return; }
      err.textContent = ""; send.disabled = true;
      sendJSON("POST", url, { body: ta.value })
        .then(() => { ta.value = ""; load(); })
        .catch((e) => { err.textContent = (e && e.message) || "The comment was not saved."; })
        .finally(() => { send.disabled = false; });
    }, "btn primary");
    // Ctrl/Cmd+Enter sends, as in most comment boxes.
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send.click(); } });
    box.append(ta, send, err);
    return box;
  }

  /** @param {any} c */
  function card(c) {
    const may = mayChange(c, ctx.me, ctx.store.get().role);
    const box = el("div", "comment");
    const top = el("div", "ctop");
    const when = c.created_at ? new Date(c.created_at).toLocaleString() : "";
    top.append(el("b", null, authorText(c)), el("span", "cwhen", when + (c.edited_at ? " · edited" : "")));
    if (c.edited_at) top.lastElementChild?.setAttribute("title", "Edited " + new Date(c.edited_at).toLocaleString());
    const text = md(c.body);
    box.append(top, text);
    if (may.edit || may.del) {
      const acts = el("span", "cacts");
      if (may.edit) acts.append(button("Edit", () => edit(box, text, c), "linkbtn"));
      if (may.del) acts.append(button("Delete", () => {
        sendJSON("DELETE", `/api/comment/${c.id}`, {})
          .then(load, (e) => toast((e && e.message) || "The comment was not deleted.", "error"));
      }, "linkbtn"));
      top.append(acts);
    }
    return box;
  }

  /** @param {HTMLElement} box @param {HTMLElement} text @param {any} c */
  function edit(box, text, c) {
    if (box.querySelector(".comment-edit")) return;
    const form = el("div", "comment-edit");
    const ta = /** @type {HTMLTextAreaElement} */ (el("textarea", "bsel comment-text"));
    ta.value = c.body; ta.setAttribute("aria-label", "Edit comment");
    const err = el("div", "err");
    const save = button("Save", () => {
      err.textContent = ""; save.disabled = true;
      sendJSON("PATCH", `/api/comment/${c.id}`, { body: ta.value })
        .then(load, (e) => { save.disabled = false; err.textContent = (e && e.message) || "The edit was not saved."; });
    }, "btn primary");
    const cancel = button("Cancel", () => { form.remove(); text.hidden = false; });
    form.append(ta, save, cancel, err);
    text.hidden = true;
    box.append(form);
    ta.focus();
  }

  load();
}
