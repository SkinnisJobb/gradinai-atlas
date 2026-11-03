// @ts-check
// core/access-model.js — the pure half of the Access view (STORY-oss-11, ADR 0041).
//
// Groups and project role bindings are edited on screen and saved as WHOLE
// lists (the API replaces, never merges), so the view keeps a draft list and
// these functions are the only thing that changes it. No DOM, so node --test
// can hold them to the rules the server enforces too:
// - roles are ordered viewer < editor < maintainer < owner;
// - a caller may add, change or remove only roles up to `grantable_up_to`
//   (a project maintainer grants up to maintainer, an owner grants owner);
// - one binding per principal per project.

/** @typedef {{principal_kind: "user"|"group", principal_id: string, role: string}} Binding */
/** @typedef {{user_id: string, username: string, name?: string|null, is_service?: boolean, disabled?: boolean}} Account */
/** @typedef {{id: string, name: string, source?: string, members?: Array<{user_id: string, source: string}>}} Group */

export const ROLES = Object.freeze(["viewer", "editor", "maintainer", "owner"]);

/** 0 for anything that is not a role, so an unknown stored role outranks nothing. @param {string|null|undefined} role */
export function rank(role) {
  const i = ROLES.indexOf(String(role));
  return i < 0 ? 0 : i + 1;
}

/** May a caller who grants up to `grantable` touch a binding holding `role`? @param {string|null} grantable @param {string} role */
export function canGrant(grantable, role) {
  return rank(grantable) > 0 && rank(role) <= rank(grantable);
}

/** The roles a caller may hand out, in order. @param {string|null} grantable */
export function grantableRoles(grantable) {
  return ROLES.filter((r) => canGrant(grantable, r));
}

/**
 * `bindings` with `principal` set to `role`, or removed when `role` is null.
 * Returns a new, sorted list; the input is not changed.
 * @param {Binding[]} bindings @param {"user"|"group"} kind @param {string} id @param {string|null} role
 * @returns {Binding[]}
 */
export function setBinding(bindings, kind, id, role) {
  const rest = bindings.filter((b) => !(b.principal_kind === kind && b.principal_id === id));
  if (role) rest.push({ principal_kind: kind, principal_id: id, role });
  return rest.sort((a, b) => (a.principal_kind + a.principal_id).localeCompare(b.principal_kind + b.principal_id));
}

/**
 * What to show for a binding: the person's or group's name, and whether this
 * caller may change it.
 * @param {Binding} b @param {Account[]} accounts @param {Group[]} groups @param {string|null} grantable
 */
export function describe(b, accounts, groups, grantable) {
  let label = b.principal_id;
  if (b.principal_kind === "user") {
    const a = accounts.find((x) => x.user_id === b.principal_id);
    if (a) label = (a.name && a.name !== a.username ? `${a.name} (${a.username})` : a.username) + (a.is_service ? " · agent" : "");
  } else {
    const g = groups.find((x) => x.id === b.principal_id);
    label = (g ? g.name : b.principal_id) + " · group";
  }
  return { label, locked: !canGrant(grantable, b.role) };
}

/**
 * Who can still be bound: every group and every active account not already
 * bound on this project. People first, then agents, then groups.
 * @param {Binding[]} bindings @param {Account[]} accounts @param {Group[]} groups
 * @returns {Array<{kind: "user"|"group", id: string, label: string}>}
 */
export function candidates(bindings, accounts, groups) {
  const taken = new Set(bindings.map((b) => b.principal_kind + ":" + b.principal_id));
  const people = accounts.filter((a) => !a.disabled && !taken.has("user:" + a.user_id))
    .sort((a, b) => Number(!!a.is_service) - Number(!!b.is_service) || a.username.localeCompare(b.username))
    .map((a) => ({ kind: /** @type {"user"} */ ("user"), id: a.user_id, label: a.username + (a.is_service ? " · agent" : "") }));
  const gs = groups.filter((g) => !taken.has("group:" + g.id))
    .map((g) => ({ kind: /** @type {"group"} */ ("group"), id: g.id, label: g.name + " · group" }));
  return [...people, ...gs];
}

/** A group's LOCAL member ids (IdP memberships are the sync's, not this view's). @param {Group} g */
export function localMembers(g) {
  return (g.members || []).filter((m) => m.source === "local").map((m) => m.user_id).sort();
}

/** People who can be put in a group: active, not agents (an agent takes its person's groups). @param {Account[]} accounts */
export function groupable(accounts) {
  return accounts.filter((a) => !a.is_service && !a.disabled).sort((a, b) => a.username.localeCompare(b.username));
}
