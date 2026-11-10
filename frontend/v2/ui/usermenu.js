// @ts-check
// ui/usermenu.js — the signed-in person's name in the top bar, and the small
// menu it opens: Settings, Change password, Sign out.
//
// Sign out is a POST carrying the CSRF token, never a link: a link is a
// state-changing GET, and link prefetchers follow those (TASK-967's rule; the
// same form legacy api.js builds). A service principal has no cookie session
// to end and /api/me hands it no token, so it gets no Sign out item rather
// than one the server would refuse.
//
// On a phone the panes have no bars to click (ui/panes.js), so the menu also
// carries a switch for the dashboard and the work board there (`extra`).

import { el } from "./dom.js";

/**
 * @typedef {{label: string, onClick: () => void, pressed?: () => boolean, cls?: string}} MenuAction
 */

/**
 * Fill `slot` with the name button and its menu.
 * @param {HTMLElement} slot  the bar's `#who`
 * @param {any} me  the /api/me body
 * @param {{onSettings: () => void, extra?: MenuAction[]}} opts
 */
export function mountUserMenu(slot, me, opts) {
  const name = (me && (me.name || me.username)) || "Account";
  const trigger = /** @type {HTMLButtonElement} */ (el("button", "who-btn"));
  trigger.type = "button";
  trigger.id = "whoBtn";
  trigger.setAttribute("aria-haspopup", "menu");
  trigger.setAttribute("aria-expanded", "false");
  trigger.title = me && me.username ? "Signed in as " + me.username : "";
  trigger.append(el("span", "who-name", name), el("span", "who-caret", "▾"));

  const menu = el("div", "who-menu");
  menu.id = "whoMenu";
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-labelledby", trigger.id);
  menu.hidden = true;
  trigger.setAttribute("aria-controls", menu.id);

  if (me && me.username) {
    const head = el("div", "who-head");
    // The username under the name, unless the name already is the username.
    head.append(el("span", "who-head-name", name));
    if (name !== me.username) head.append(el("span", "who-head-id", "@" + me.username));
    menu.append(head);
  }

  /** @type {HTMLElement[]} */
  const items = [];
  /** @param {HTMLElement} item */
  const add = (item) => { item.setAttribute("role", "menuitem"); item.tabIndex = -1; items.push(item); return item; };
  /** @param {string} label @param {() => void} fn @param {string} [cls] */
  const action = (label, fn, cls) => {
    const b = /** @type {HTMLButtonElement} */ (el("button", "who-item" + (cls ? " " + cls : ""), label));
    b.type = "button";
    b.addEventListener("click", () => { close(); fn(); });
    return add(b);
  };
  /** @param {string} label @param {string} href */
  const link = (label, href) => {
    const a = /** @type {HTMLAnchorElement} */ (el("a", "who-item", label));
    a.href = href;
    return add(a);
  };

  /** @type {{item: HTMLElement, pressed?: () => boolean}[]} */
  const toggles = [];
  if (opts.extra && opts.extra.length) {
    for (const x of opts.extra) {
      const item = action(x.label, x.onClick, x.cls);
      if (x.pressed) { item.setAttribute("role", "menuitemcheckbox"); toggles.push({ item, pressed: x.pressed }); }
      menu.append(item);
    }
    menu.append(el("div", "who-sep " + (opts.extra[0].cls || "")));
  }
  menu.append(action("Settings", opts.onSettings));
  if (me && me.kind === "session") menu.append(link("Change password", "/auth/password/change"));

  if (me && me.kind === "session" && me.csrf_token) {
    menu.append(el("div", "who-sep"));
    const form = /** @type {HTMLFormElement} */ (el("form", "who-form"));
    form.method = "post";
    form.action = "/auth/logout";
    const token = /** @type {HTMLInputElement} */ (el("input"));
    token.type = "hidden"; token.name = "csrf_token"; token.value = me.csrf_token;
    const out = /** @type {HTMLButtonElement} */ (el("button", "who-item is-signout", "Sign out"));
    out.type = "submit";
    add(out);
    form.append(token, out);
    menu.append(form);
  }

  function open() {
    for (const t of toggles) t.item.setAttribute("aria-checked", String(!!(t.pressed && t.pressed())));
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    const first = items.find((i) => i.offsetParent !== null);
    if (first) first.focus();
  }
  function close() {
    if (menu.hidden) return;
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
  }
  trigger.addEventListener("click", (e) => { e.stopPropagation(); if (menu.hidden) open(); else close(); });
  document.addEventListener("click", (e) => { if (!slot.contains(/** @type {Node} */ (e.target))) close(); });
  slot.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !menu.hidden) { e.preventDefault(); close(); trigger.focus(); return; }
    if (menu.hidden) {
      if (e.target === trigger && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); open(); }
      return;
    }
    // Items hidden on a wide screen (the phone-only switches) are skipped.
    const shown = items.filter((i) => i.offsetParent !== null);
    const at = shown.indexOf(/** @type {HTMLElement} */ (document.activeElement));
    let next = -1;
    if (e.key === "ArrowDown") next = (at + 1) % shown.length;
    else if (e.key === "ArrowUp") next = (at - 1 + shown.length) % shown.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = shown.length - 1;
    else if (e.key === "Tab") { close(); return; }
    else return;
    e.preventDefault();
    shown[next].focus();
  });

  slot.replaceChildren(trigger, menu);
  return { close };
}
