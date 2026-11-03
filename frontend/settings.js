// settings.js — the Settings overlay, and the Security & access grid inside it
// (STORY-atlas-access-admin).
//
// SEAM: this module owns its own DOM. board.html holds no markup for it and
// only calls `openSettings(me)`; everything below is built with createElement
// and textContent, never innerHTML, for the reason api.js states — every value
// rendered here (a username, a node name, an account label) arrives from the
// server, and no server-supplied value is ever parsed as HTML.
//
// WHY AN OVERLAY AND NOT A ROUTE: the board is a single static file behind one
// authenticated path. A /settings route would need its own page, its own boot,
// and its own copy of the app bar — and would drop the map state the user came
// from. The overlay costs one element and restores focus on close.
//
// WHAT THIS DELIBERATELY DOES NOT DO: it never renders a control an ungranted
// caller could not use. `GET /api/access` is owner-only and 403s a member, so
// the Security section is not merely hidden for a member — it is never fetched.
// A settings page that shows a disabled access grid to somebody who may not
// administer access is an invitation to ask why.
//
// LEFT-NAV RESTRUCTURE (owner decision, 2026-08-25: "structure a menu for it, ... go
// from top to bottom, divide the current settings into these menu items").
// The four sections that used to be stacked on one long scrolling page are
// now four PANES behind a left-hand menu (a horizontal chip row on mobile —
// see the media query in board.css). Nothing was added: this is the same
// four groups, same gates, same fetches — just reachable one at a time.
//
// EACH PANE LOADS ON FIRST REVEAL, NOT ON OVERLAY OPEN. `loadAccess`,
// `loadAuthEvents` and `loadCredentials` used to fire eagerly the moment the
// overlay opened, which meant an owner paid for the access grid AND the audit
// log AND the credential list even when all they wanted was to change their
// password. `panes[key].loaded` gates each fetch so switching back to an
// already-visited pane is instant and issues no second request; switching
// AWAY never tears the pane down, so its state (an open confirm, a save in
// flight) survives.
import { fetchJSON, postJSON, putJSON, CSRF_TOKEN } from "/assets/api.js";
import { remember, recall, dismissOnScrim } from "/assets/util.js";

var overlay = null;
var lastFocus = null;

// Which pane was open last, across reloads — same `atlas.*` namespace as every
// other piece of remembered view state. Read defensively: a member who last
// had "Audit log" open (as an owner, before being demoted, or simply because
// the key is stale) must land on the first pane they can actually see, never
// on a blank one.
var PANE_KEY = "atlas.settingsPane";

// Order matters — top to bottom in the menu — and owner-only items stay last,
// per the owner's instruction. Labels here ARE the grouping names; keep them in
// sync with the section heading each pane renders.
var PANE_DEFS = [
  { key: "account", label: "Account", ownerOnly: false },
  { key: "agents", label: "Agent credentials", ownerOnly: false },
  { key: "access", label: "Access & sharing", ownerOnly: true },
  { key: "audit", label: "Audit log", ownerOnly: true }
];

// ---------------------------------------------------------------- utilities
function el(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// ------------------------------------------------------------- the overlay
function buildOverlay() {
  var root = el("div", "settings-overlay");
  root.id = "settingsOverlay";
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-modal", "true");
  root.setAttribute("aria-labelledby", "settingsTitle");
  root.hidden = true;

  var sheet = el("div", "settings-sheet");

  var head = el("header", "settings-head");
  var title = el("h2", null, "Settings");
  title.id = "settingsTitle";
  head.appendChild(title);
  var close = el("button", "settings-close", "×");
  close.id = "settingsClose";
  close.type = "button";
  close.setAttribute("aria-label", "Close settings");
  head.appendChild(close);
  sheet.appendChild(head);

  var body = el("div", "settings-body");
  body.id = "settingsBody";
  sheet.appendChild(body);

  root.appendChild(sheet);
  document.body.appendChild(root);

  close.addEventListener("click", closeSettings);
  dismissOnScrim(root, closeSettings);   // the scrim, and not a drag that ended on it
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && overlay && !overlay.hidden) closeSettings();
  });
  return root;
}

export function closeSettings() {
  if (!overlay) return;
  overlay.hidden = true;
  document.body.classList.remove("settings-open");
  if (lastFocus && lastFocus.focus) lastFocus.focus();
}

// ---------------------------------------------------------- pane builders
//
// Each builder fills the (empty, already-inserted) pane element the first
// time that pane is shown, and is the ONLY place that section's fetch is
// kicked off. They are plain functions rather than object methods because
// `renderProfile`/`loadAccess`/`loadAuthEvents`/`loadCredentials` already
// exist as free functions below — this just relocates where each one is
// called from, not what it does.
function buildAccountPane(panel, me, notice) {
  panel.appendChild(renderProfile(me));
  buildGithubSection(panel, notice);
}

function buildAgentsPane(panel) {
  // FOR EVERYONE, unlike Access & sharing below — a member managing their own
  // credentials administers nobody, and every route behind it is scoped to
  // the caller's own agents inside the query rather than filtered afterwards.
  var creds = el("section", "settings-section");
  creds.appendChild(el("h3", "settings-h3", "Agent credentials"));
  var credNote = el("p", "settings-note");
  credNote.textContent = "A credential lets an agent read and write Atlas as you. "
    + "It sees exactly the projects you can see, and nothing you cannot. "
    + "You can revoke one at any time without affecting your own sign-in.";
  creds.appendChild(credNote);
  var credHost = el("div", "cred-host");
  credHost.id = "credentialHost";
  credHost.appendChild(el("p", "settings-note", "Loading credentials…"));
  creds.appendChild(credHost);
  panel.appendChild(creds);
  loadCredentials(credHost);
}

function buildAccessPane(panel) {
  var section = el("section", "settings-section");
  section.appendChild(el("h3", "settings-h3", "Access & sharing"));
  var note = el("p", "settings-note");
  note.textContent = "A shared project is visible to every signed-in account. "
    + "A private one is visible only to you and the accounts you grant it to. "
    + "Access follows the tree: granting a project grants everything inside it.";
  section.appendChild(note);
  var host = el("div", "access-host");
  host.id = "accessHost";
  host.appendChild(el("p", "settings-note", "Loading access…"));
  section.appendChild(host);
  panel.appendChild(section);
  loadAccess(host);
}

function buildAuditPane(panel) {
  // GET /api/auth-events shipped with no reader at all — curl was the only
  // way to see it. Same owner-only gate as Access & sharing, decided from the
  // principal for the same reason: a member never issues the request, so
  // there is no 403 to handle here either.
  var section = el("section", "settings-section");
  section.appendChild(el("h3", "settings-h3", "Audit log"));
  var note = el("p", "settings-note");
  note.textContent = "Logins, lockouts and access changes across every account, "
    + "newest first.";
  section.appendChild(note);
  var host = el("div", "access-host");
  host.id = "auditHost";
  host.appendChild(el("p", "settings-note", "Loading…"));
  section.appendChild(host);
  panel.appendChild(section);
  loadAuthEvents(host);
}

export function openSettings(me, notice) {
  if (!overlay) overlay = buildOverlay();
  lastFocus = document.activeElement;
  overlay.hidden = false;
  document.body.classList.add("settings-open");

  var body = document.getElementById("settingsBody");
  clear(body);

  // OWNER ONLY, decided from the principal rather than from a failed fetch: a
  // member never even sees the menu item, so there is no 403 in their devtools
  // log to explain and no disabled control inviting the question "why can't
  // I click this". Same rule as before the restructure — it just now governs
  // which MENU ITEMS exist rather than which sections render.
  var isOwner = !!(me && me.role === "owner");
  var defs = PANE_DEFS.filter(function (d) { return !d.ownerOnly || isOwner; });

  // OWNER-ONLY BUILDERS ARE DECLARED ONLY WHEN OFFERED (mirrors the fetch
  // gate above): a member's `defs` never contains "access"/"audit", so their
  // pane map never gets an entry that could be reached by, say, a stale
  // remembered key surviving a role change.
  var builders = { account: function (panel) { buildAccountPane(panel, me, notice); },
                   agents: buildAgentsPane };
  if (isOwner) {
    builders.access = buildAccessPane;
    builders.audit = buildAuditPane;
  }

  var shell = el("div", "settings-shell");
  var nav = el("div", "settings-nav");
  nav.setAttribute("role", "tablist");
  nav.setAttribute("aria-orientation", "vertical");
  nav.setAttribute("aria-label", "Settings sections");
  var content = el("div", "settings-content");

  var panes = {};     // key -> { btn, panel, loaded }
  var order = [];
  var current = null;

  function select(key) {
    if (!panes[key]) key = order[0];
    current = key;
    order.forEach(function (k) {
      var p = panes[k];
      var active = k === key;
      p.panel.hidden = !active;
      p.btn.setAttribute("aria-selected", active ? "true" : "false");
      p.btn.tabIndex = active ? 0 : -1;
      p.btn.classList.toggle("is-active", active);
    });
    remember(PANE_KEY, key);
    var p = panes[key];
    if (!p.loaded) {
      p.loaded = true;
      builders[key](p.panel);
    }
  }

  defs.forEach(function (def) {
    var btn = el("button", "settings-nav-btn", def.label);
    btn.type = "button";
    btn.id = "settingsNav-" + def.key;
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", "false");
    btn.tabIndex = -1;
    nav.appendChild(btn);

    var panel = el("div", "settings-pane");
    panel.id = "settingsPane-" + def.key;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", btn.id);
    panel.hidden = true;
    content.appendChild(panel);
    btn.setAttribute("aria-controls", panel.id);

    panes[def.key] = { btn: btn, panel: panel, loaded: false };
    order.push(def.key);

    btn.addEventListener("click", function () { select(def.key); });
  });

  // Arrow-key roving focus, the standard tablist pattern: the arrow both
  // moves focus AND activates, so a keyboard user never lands on a tab whose
  // pane is not the one on screen. Escape still closes the whole overlay —
  // handled globally in buildOverlay(), untouched here.
  nav.addEventListener("keydown", function (ev) {
    var idx = order.indexOf(current);
    var next = -1;
    if (ev.key === "ArrowDown") next = (idx + 1) % order.length;
    else if (ev.key === "ArrowUp") next = (idx - 1 + order.length) % order.length;
    else if (ev.key === "Home") next = 0;
    else if (ev.key === "End") next = order.length - 1;
    else return;
    ev.preventDefault();
    var key = order[next];
    select(key);
    panes[key].btn.focus();
  });

  shell.appendChild(nav);
  shell.appendChild(content);
  body.appendChild(shell);

  // A remembered pane the current account cannot see (owner demoted to
  // member, or a stale value from before this account existed) falls back to
  // the first item rather than leaving every pane hidden.
  var wanted = notice ? "account" : recall(PANE_KEY);   // returning from GitHub lands on Account
  select((wanted && panes[wanted]) ? wanted : order[0]);

  var closeBtn = document.getElementById("settingsClose");
  if (closeBtn) closeBtn.focus();
}

// ------------------------------------------------- GitHub (link + repo access)
//
// Two halves of one feature, both rendering the server and nothing else:
//   - Account pane, EVERY signed-in person: link / unlink their own GitHub
//     identity. "Link" is a plain navigation to /api/me/github/start (Sign in
//     with GitHub); the server verifies the identity, this screen never types or
//     sends a username. Linking grants nothing.
//   - Access & sharing pane, OWNER only: each person's linked login and a "Repo
//     access" box. Ticking asks the server to send a collaborator invitation;
//     the person accepts on GitHub. The route behind it is owner-session-only,
//     so a member's browser or an agent token cannot reach it however this file
//     is edited.
// Not configured on the server -> the Account section is not drawn at all, and
// the owner's section says so in one line.
var GITHUB_NOTICES = {
  linked: "GitHub account linked.",
  denied: "GitHub sign-in was cancelled. Nothing was linked.",
  github: "GitHub did not complete the sign-in. Try again.",
  taken: "That GitHub account is already linked to another Atlas account.",
  already_linked: "You already have a different GitHub account linked. Unlink it first."
};

function githubStateLabel(state, repo) {
  if (state === "collaborator") return "Collaborator on " + (repo || "the repository");
  if (state === "invited") return "Invitation sent - accept it on GitHub";
  if (state === "linked") return "Linked - repository access not approved";
  return "Not linked";
}

function buildGithubSection(panel, notice) {
  var section = el("section", "settings-section");
  section.hidden = true;                    // until the server says it is configured
  panel.appendChild(section);
  loadGithubSection(section, notice);
}

function loadGithubSection(section, notice) {
  fetchJSON("/api/me/github").then(function (g) {
    renderGithubSection(section, g, notice);
  }, function () { section.hidden = true; });
}

function renderGithubSection(section, g, notice) {
  clear(section);
  if (!g || !g.configured) { section.hidden = true; return; }
  section.hidden = false;
  section.appendChild(el("h3", "settings-h3", "GitHub"));
  var note = el("p", "settings-note");
  note.textContent = "Link your GitHub account so the owner can invite you to the project's "
    + "repository. Linking proves who you are and grants nothing; the owner approves access "
    + "separately and you accept the invitation on GitHub.";
  section.appendChild(note);
  if (notice) {
    section.appendChild(el("p", notice === "linked" ? "settings-note" : "settings-warn",
      GITHUB_NOTICES[notice] || GITHUB_NOTICES.github));
  }
  if (!g.linked) {
    var go = el("a", "cred-btn", "Link your GitHub account");
    go.id = "githubLink";
    go.href = "/api/me/github/start";
    section.appendChild(go);
    return;
  }
  var dl = el("dl", "settings-dl");
  dl.appendChild(el("dt", null, "GitHub account"));
  dl.appendChild(el("dd", null, g.login));
  dl.appendChild(el("dt", null, "Repository"));
  dl.appendChild(el("dd", null, githubStateLabel(g.repo_access, g.repo)));
  section.appendChild(dl);
  var unlink = el("button", "cred-btn is-quiet", "Unlink");
  unlink.type = "button";
  unlink.id = "githubUnlink";
  var wrap = el("span", "cred-confirm");
  unlink.addEventListener("click", function () {
    clear(wrap);
    wrap.appendChild(el("span", "cred-note", g.repo_access === "linked"
      ? "Unlink this GitHub account?"
      : "Unlink, and remove your access to the repository?"));
    var yes = el("button", "cred-btn is-danger", "Unlink");
    yes.type = "button";
    yes.addEventListener("click", function () {
      yes.disabled = true;
      postJSON("/api/me/github/unlink", { csrf_token: CSRF_TOKEN }).then(function () {
        loadGithubSection(section, null);
      }, function (err) {
        yes.disabled = false;
        clear(wrap);
        wrap.appendChild(el("span", "cred-status is-bad",
          (err && err.message) ? err.message : "could not unlink"));
      });
    });
    var no = el("button", "cred-btn is-quiet", "Keep it");
    no.type = "button";
    no.addEventListener("click", function () { clear(wrap); });
    wrap.appendChild(yes);
    wrap.appendChild(no);
  });
  section.appendChild(unlink);
  section.appendChild(wrap);
}

// Owner view: one row per PERSON account (agents never link a GitHub identity).
function renderRepoAccess(data) {
  var section = el("section", "settings-section");
  section.appendChild(el("h3", "settings-h3", "Repository access"));
  var gh = data.github || {};
  if (!gh.configured) {
    section.appendChild(el("p", "settings-note",
      "GitHub linking is not configured on this server, so repository access cannot be managed here."));
    return section;
  }
  var note = el("p", "settings-note");
  note.textContent = "Repository " + gh.repo + ". Everyone invited gets push access (a personal "
    + "repository has no read-only role). An account must link its own GitHub account first; "
    + "ticking sends the invitation, unticking removes the collaborator or cancels the "
    + "invitation.";
  section.appendChild(note);

  var people = (data.accounts || []).filter(function (a) { return !a.is_service; });
  var table = el("table", "access-table");
  var thead = el("thead");
  var hr = el("tr");
  ["Account", "GitHub", "State", "Repo access"].forEach(function (h) { hr.appendChild(el("th", null, h)); });
  thead.appendChild(hr);
  table.appendChild(thead);
  var tbody = el("tbody");

  people.forEach(function (a) {
    var tr = el("tr", "gh-row");
    tr.setAttribute("data-user", a.user_id);
    tr.appendChild(el("th", "access-td-acct", a.username));
    var loginTd = el("td", null, a.github_login || "—");
    var stateTd = el("td");
    var accessTd = el("td");
    var box = document.createElement("input");
    box.type = "checkbox";
    box.className = "access-box";
    box.setAttribute("aria-label", "Repository access for " + a.username);
    var status = el("span", "cred-status");
    status.setAttribute("aria-live", "polite");
    var refresh = el("button", "cred-btn is-quiet", "Check");
    refresh.type = "button";
    refresh.title = "Ask GitHub whether the invitation was accepted";

    function paint() {
      var s = a.repo_access;
      clear(stateTd);
      stateTd.appendChild(el("span", "cred-state " + (s === "collaborator" ? "is-ok"
        : s === "invited" ? "is-warn" : ""), githubStateLabel(s, gh.repo)));
      loginTd.textContent = a.github_login || "—";
      box.checked = (s === "invited" || s === "collaborator");
      box.disabled = (s === "not_linked");
      refresh.hidden = (s !== "invited");
    }
    function apply(res) {
      a.repo_access = res.repo_access;
      a.github_login = res.github_login || a.github_login;
      paint();
    }
    function failed(err, revert) {
      status.textContent = (err && err.message) ? err.message : "failed";
      status.className = "cred-status is-bad";
      if (revert) revert();
    }

    box.addEventListener("change", function () {
      var want = box.checked;
      box.disabled = true;
      status.textContent = want ? "Sending invitation…" : "Removing…";
      status.className = "cred-status";
      putJSON("/api/access/github/" + encodeURIComponent(a.user_id),
              { repo_access: want, csrf_token: CSRF_TOKEN }).then(function (res) {
        status.textContent = "";
        apply(res);
      }, function (err) { failed(err, paint); });
    });
    refresh.addEventListener("click", function () {
      refresh.disabled = true;
      postJSON("/api/access/github/" + encodeURIComponent(a.user_id) + "/refresh",
               { csrf_token: CSRF_TOKEN }).then(function (res) {
        refresh.disabled = false;
        status.textContent = "";
        apply(res);
      }, function (err) { refresh.disabled = false; failed(err, null); });
    });

    accessTd.appendChild(box);
    accessTd.appendChild(refresh);
    accessTd.appendChild(status);
    tr.appendChild(loginTd);
    tr.appendChild(stateTd);
    tr.appendChild(accessTd);
    tbody.appendChild(tr);
    paint();
  });
  table.appendChild(tbody);
  var scroll = el("div", "access-scroll");
  scroll.appendChild(table);
  section.appendChild(scroll);
  return section;
}

// -------------------------------------------------------------- profile pane
function renderProfile(me) {
  var section = el("section", "settings-section");
  section.appendChild(el("h3", "settings-h3", "Profile"));
  var dl = el("dl", "settings-dl");
  function row(label, value) {
    dl.appendChild(el("dt", null, label));
    dl.appendChild(el("dd", null, value));
  }
  row("Signed in as", (me && me.username) || "—");
  if (me && me.name) row("Name", me.name);
  row("Role", me && me.role === "owner" ? "owner" : "member");
  // The scope list is the honest answer to "what can this session do", and it
  // is the thing that explains a refusal when one happens.
  if (me && me.scopes && me.scopes.length) row("Scopes", me.scopes.join(", "));
  section.appendChild(dl);
  var link = el("a", "settings-link", "Change password");
  link.href = "/auth/password/change";
  section.appendChild(link);
  // The same notice the login page links to (TASK-941). Reachable from inside
  // as well as outside, because the question "what do you hold about me" does
  // not stop being asked once somebody has signed in.
  var privacy = el("a", "settings-link", "How your data is handled");
  privacy.href = "/auth/privacy";
  section.appendChild(privacy);
  return section;
}

// --------------------------------------------------- the access matrix
//
// ACCOUNTS DOWN, PROJECTS ACROSS — transposed on 2026-08-16 (owner decision). The first
// version had it the other way, and the axis was the wrong way round for a
// reason that gets worse over time: accounts grow by roughly TWO per contributor
// (a person and the agents they bring) while projects are five and change when a
// decision changes. Putting the growing set on the horizontal axis meant the
// grid needed sideways scrolling at nine accounts, and the far end of a
// sideways-scrolling table is where an owner stops reading.
//
// The other half of the fix is that an agent now belongs to a person (ADR 0034),
// so the account axis is a TREE and not a list: a person is a row, their agents
// are rows nested under it, and a collapsed person is one line. An owner with
// four agents was five columns of noise; it is now one row with a chevron.
//
// WHAT THIS SCREEN CAN AND CANNOT SAY. It renders `GET /api/access` and nothing
// else — no node id, no account, no relationship is written down here. That is
// the same rule the map modules live under: a module that encodes the graph
// becomes a second, disagreeing copy of it.
function loadAccess(host) {
  fetchJSON("/api/access").then(function (data) {
    clear(host);
    host.appendChild(renderRepoAccess(data));
    host.appendChild(renderAccessMatrix(data));
    if (data.orphan_marks && data.orphan_marks.length) {
      // Reported, never silently cleaned — see the API's own note. A mark whose
      // node is gone is usually a rename, and the owner is the only one who can
      // say which.
      var warn = el("p", "settings-warn");
      warn.textContent = "Marks with no matching node: " + data.orphan_marks.join(", ")
        + ". These grant nothing until a node with that id exists again.";
      host.appendChild(warn);
    }
  }, function (err) {
    clear(host);
    var msg = el("p", "settings-warn");
    msg.textContent = "Could not load access settings: " + (err && err.message ? err.message : "unknown error");
    host.appendChild(msg);
  });
}

// Which people are expanded, across reloads. Same `atlas.*` namespace and the
// same safe wrappers as every other piece of remembered view state; a person
// whose agents you are actively sorting out should still be open tomorrow.
var EXPANDED_KEY = "atlas.accessExpanded";

function readExpanded() {
  var raw = recall(EXPANDED_KEY);
  var open = {};
  if (!raw) return open;
  raw.split(",").forEach(function (id) { if (id) open[id] = true; });
  return open;
}

function writeExpanded(open) {
  var ids = [];
  for (var id in open) { if (open[id]) ids.push(id); }
  remember(EXPANDED_KEY, ids.join(","));
}

// AGENTS ARE ROWS UNDER THEIR PERSON, and an agent with no person recorded is
// NOT hidden — it goes in a trailing group of its own. A column that vanishes is
// a grant nobody can find again, and an unlinked agent is precisely the row
// somebody needs to act on (`admin bot link`). A link naming an account that is
// not in the list — a dangling one — counts as unlinked for the same reason.
function accountTree(accounts) {
  var known = {};
  accounts.forEach(function (a) { known[a.user_id] = true; });

  var people = [];
  var agentsOf = {};
  var orphans = [];
  accounts.forEach(function (a) {
    if (!a.is_service) { people.push(a); agentsOf[a.user_id] = agentsOf[a.user_id] || []; return; }
    if (a.person_user_id && known[a.person_user_id]) {
      agentsOf[a.person_user_id] = agentsOf[a.person_user_id] || [];
      agentsOf[a.person_user_id].push(a);
    } else {
      orphans.push(a);
    }
  });
  return { people: people, agentsOf: agentsOf, orphans: orphans };
}

function renderAccessMatrix(data) {
  var nodes = data.nodes || [];
  var tree = accountTree(data.accounts || []);
  var open = readExpanded();
  var byId = {};
  (data.accounts || []).forEach(function (a) { byId[a.user_id] = a; });

  // The whole matrix, keyed [node id][user id] -> boolean. Held here rather than
  // read back off the checkboxes at save time for one reason: PUT
  // /api/access/node/{id} replaces a node's WHOLE grant list, so saving one
  // account's tick requires sending every OTHER account's answer for that node
  // too. A row-shaped screen has to know the column, which means it has to hold
  // the full matrix — and a collapsed row's checkboxes must therefore still
  // exist. They do: collapsing hides rows, it never drops them.
  var granted = {};
  nodes.forEach(function (n) {
    granted[n.id] = {};
    (n.grants || []).forEach(function (uid) { granted[n.id][uid] = true; });
  });

  // WHAT AN ACCOUNT ACTUALLY SEES, as opposed to what is stored against it.
  // Derived here rather than asked of the API on purpose: `node.grants` must
  // stay the STORED list, because it is what the save writes back. An endpoint
  // returning effective grants would make every save materialise an inherited
  // grant as a real row on the agent — quietly turning inheritance into a
  // hundred copies of itself, and breaking the day somebody unticks the person.
  //
  // Union, matching `_visible_node_ids`: own grant OR the person's.
  function effective(nodeId, account) {
    if (granted[nodeId][account.user_id]) return true;
    if (account.is_service && account.person_user_id) {
      return !!granted[nodeId][account.person_user_id];
    }
    return false;
  }

  var wrap = el("div", "access-scroll");
  var table = el("table", "access-table");
  var boxes = [];          // {node, user, input}
  var visSelects = {};     // node id -> select
  var dirtyNodes = {};     // node id -> true
  var repaint = function () {};

  // ---------------------------------------------------------------- header
  var thead = el("thead");
  var hr = el("tr");
  var corner = el("th", "access-th-acct", "Account");
  hr.appendChild(corner);
  nodes.forEach(function (n) {
    var th = el("th", "access-th-proj");
    th.setAttribute("data-node", n.id);
    th.appendChild(el("div", "access-proj-name", n.name || n.id));
    th.appendChild(el("div", "access-proj-id", n.id));

    // VISIBILITY IS A PROPERTY OF THE PROJECT, so it belongs in the project's
    // header and not in some row. It was per-row before only because rows were
    // projects.
    var sel = el("select", "access-vis");
    sel.setAttribute("aria-label", "Visibility of " + (n.name || n.id));
    [["shared", "shared"], ["private", "private"]].forEach(function (pair) {
      var opt = document.createElement("option");
      opt.value = pair[0];
      opt.textContent = pair[1];
      if (n.visibility === pair[0]) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.addEventListener("change", function () { dirtyNodes[n.id] = true; repaint(); });
    th.appendChild(sel);
    visSelects[n.id] = sel;

    var status = el("div", "access-col-status");
    status.setAttribute("aria-live", "polite");
    th.appendChild(status);
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  table.appendChild(thead);

  // ------------------------------------------------------------------ rows
  var tbody = el("tbody");

  function cellFor(account, node, isPersonRow) {
    var td = el("td", "access-td-cell");
    if (account.role === "owner") {
      // An owner is not GRANTED a project, an owner sees every project. A box
      // here — ticked, unticked or disabled — would all claim the row means
      // something. It means nothing, so it says so.
      var dot = el("span", "access-always", "•");
      dot.title = account.username + " is an owner and sees every project";
      td.appendChild(dot);
      return td;
    }
    var box = document.createElement("input");
    box.type = "checkbox";
    box.className = "access-box";
    box.checked = !!granted[node.id][account.user_id];
    box.setAttribute("data-user", account.user_id);
    box.setAttribute("data-node", node.id);
    box.setAttribute("aria-label", "Grant " + account.username + " access to " + (node.name || node.id));
    box.addEventListener("change", function () {
      granted[node.id][account.user_id] = box.checked;
      dirtyNodes[node.id] = true;
      repaint();
    });
    td.appendChild(box);
    boxes.push({ node: node.id, user: account.user_id, input: box });

    // INHERITED, NOT STORED (TASK-1114). An agent sees what its person is
    // granted, and that grant is a row on the PERSON — there is nothing on the
    // agent to untick. A plain tick here would be a lie in the one direction
    // that matters: you would clear it, save, and watch it come back.
    //
    // So the marker is drawn beside the box and the box is disabled, with the
    // reason on the element rather than in a legend nobody reads. What this
    // screen writes stays exactly what the API stores.
    if (account.is_service && account.person_user_id) {
      td.appendChild(el("span", "access-inherited", "↳"));
    }

    if (isPersonRow) {
      // THE DISAGREEMENT MARK (owner decision, 2026-08-16). A person granted a project
      // whose agent is not is the exact drift ADR 0034 exists to remove, and
      // collapsed it is invisible — the person's row shows a tick and the agent
      // that 404s is one chevron away. So the row says so.
      //
      // It retires itself: once TASK-1114 makes an agent inherit its person's
      // grants, a disagreement can only come from a deliberate per-agent grant,
      // which is when you WANT the mark to stay.
      var mark = el("span", "access-mixed", "▨");
      td.appendChild(mark);
    }
    return td;
  }

  function addAccountRow(account, kind, person) {
    var tr = el("tr", "access-arow is-" + kind);
    tr.setAttribute("data-user", account.user_id);
    if (person) tr.setAttribute("data-person", person.user_id);

    var head = el("th", "access-td-acct");
    head.setAttribute("scope", "row");

    var agents = tree.agentsOf[account.user_id] || [];
    if (kind === "person" && agents.length) {
      var btn = el("button", "access-expand");
      btn.type = "button";
      btn.setAttribute("data-user", account.user_id);
      btn.setAttribute("aria-expanded", open[account.user_id] ? "true" : "false");
      btn.setAttribute("aria-label", (open[account.user_id] ? "Collapse " : "Expand ")
        + account.username + "'s agents");
      btn.textContent = "▸";
      btn.addEventListener("click", function () {
        open[account.user_id] = !open[account.user_id];
        writeExpanded(open);
        repaint();
      });
      head.appendChild(btn);
    } else {
      // A spacer, not nothing: without it every person with no agents sits one
      // chevron-width left of every person who has some, and the column reads
      // as two columns.
      head.appendChild(el("span", "access-expand is-empty", ""));
    }

    head.appendChild(el("span", "access-acct-name", account.username));
    if (account.role === "owner") head.appendChild(el("span", "access-acct-tag", "owner"));
    else if (account.is_service) head.appendChild(el("span", "access-acct-tag is-bot", "agent"));
    if (account.disabled) head.appendChild(el("span", "access-acct-tag is-off", "disabled"));
    // AN AGENT WHOSE PERSON IS DISABLED IS REFUSED, even when its own row still
    // says enabled (TASK-1113). That happens when the agent was linked to an
    // already-disabled person, so no `user disable` cascade ever ran for it —
    // `auth._resolve_token` catches it, and without this tag the grid would show
    // a working agent that 401s on every call. `admin bot list` says the same
    // thing; the two must not disagree.
    else if (account.is_service && account.person_user_id
             && byId[account.person_user_id] && byId[account.person_user_id].disabled) {
      var off = el("span", "access-acct-tag is-off", "person disabled");
      off.title = account.username + " cannot authenticate: "
        + byId[account.person_user_id].username + " is disabled. Re-enable them, or "
        + "unlink this agent if it should outlive them.";
      head.appendChild(off);
    }
    if (kind === "person" && agents.length) {
      head.appendChild(el("span", "access-agent-count",
        agents.length === 1 ? "1 agent" : agents.length + " agents"));
    }
    head.title = account.username + (account.name ? " — " + account.name : "")
      + (account.is_service ? " — an agent account; its tokens see what THIS account is granted" : "");
    tr.appendChild(head);

    nodes.forEach(function (n) { tr.appendChild(cellFor(account, n, kind === "person")); });
    tbody.appendChild(tr);
    return tr;
  }

  tree.people.forEach(function (p) {
    addAccountRow(p, "person", null);
    (tree.agentsOf[p.user_id] || []).forEach(function (bot) {
      addAccountRow(bot, "agent", p);
    });
  });

  if (tree.orphans.length) {
    var sep = el("tr", "access-arow is-sep");
    var sepHead = el("th", "access-td-acct", "no person recorded");
    sepHead.setAttribute("scope", "row");
    sepHead.colSpan = 1;
    sep.appendChild(sepHead);
    nodes.forEach(function () { sep.appendChild(el("td", "access-td-cell")); });
    tbody.appendChild(sep);
    tree.orphans.forEach(function (bot) { addAccountRow(bot, "agent", null); });
  }

  table.appendChild(tbody);
  wrap.appendChild(table);

  // ------------------------------------------------------------- the actions
  var actions = el("div", "access-actions");
  var status = el("span", "access-status");
  status.setAttribute("aria-live", "polite");
  var save = el("button", "access-save", "Save changes");
  save.type = "button";
  save.disabled = true;
  actions.appendChild(status);
  actions.appendChild(save);

  // NOTE ON WHAT THE SAVE SENDS: `granted` holds STORED grants only, and a
  // disabled inherited box never writes into it, so an inherited grant can
  // never be materialised as a row on the agent by saving this screen. That is
  // the invariant `effective()` exists to protect.
  //
  // ONE SAVE, not one per row, and the reason is the API. A row is an account
  // and a write is a NODE — so an account row's save is already several PUTs.
  // Once the button is a batch, making it the whole grid's batch costs nothing
  // and removes the question "which of these nine buttons applies to the
  // visibility dropdown I just changed" (answer, in a per-row design: none of
  // them).
  repaint = function () {
    var anyDirty = false;
    for (var id in dirtyNodes) { if (dirtyNodes[id]) { anyDirty = true; break; } }
    save.disabled = !anyDirty;
    if (anyDirty) status.textContent = "";

    Array.prototype.forEach.call(tbody.querySelectorAll(".access-arow.is-agent"), function (tr) {
      var person = tr.getAttribute("data-person");
      tr.hidden = !!person && !open[person];
    });
    Array.prototype.forEach.call(tbody.querySelectorAll(".access-expand"), function (btn) {
      var uid = btn.getAttribute("data-user");
      if (!uid) return;
      btn.textContent = open[uid] ? "▾" : "▸";
      btn.setAttribute("aria-expanded", open[uid] ? "true" : "false");
    });

    // The private/shared paint, per COLUMN now. Grants on a shared project are
    // kept by the server across a shared -> private -> shared round trip, so the
    // boxes stay live rather than being disabled — what changes is that the
    // column stops looking like it is doing anything.
    nodes.forEach(function (n) {
      var isPrivate = visSelects[n.id].value === "private";
      var th = table.querySelector('th[data-node="' + n.id + '"]');
      if (th) th.classList.toggle("is-private", isPrivate);
      Array.prototype.forEach.call(
        table.querySelectorAll('.access-box[data-node="' + n.id + '"]'),
        function (b) { b.parentNode.classList.toggle("is-private", isPrivate); });
    });

    paintInherited();
    paintDisagreements();
  };

  // An inherited cell is disabled and says why. Recomputed on every repaint
  // rather than once at build time, because ticking the PERSON's box changes
  // what their agents inherit before anything is saved — the screen has to
  // follow that immediately or the preview is wrong.
  function paintInherited() {
    boxes.forEach(function (b) {
      var account = byId[b.user];
      if (!account || !account.is_service || !account.person_user_id) return;
      var person = byId[account.person_user_id];
      var inherited = !!granted[b.node] && !!granted[b.node][account.person_user_id];
      var td = b.input.parentNode;
      td.classList.toggle("is-inherited", inherited);
      b.input.disabled = inherited;
      var project = (nodes.filter(function (n) { return n.id === b.node; })[0] || {});
      var name = project.name || b.node;
      if (inherited) {
        b.input.title = account.username + " sees " + name + " because "
          + ((person && person.username) || "its person") + " is granted it. Remove it there"
          + (granted[b.node][b.user] ? "; the direct grant here is kept but adds nothing."
                                     : " and it goes here too.");
      } else {
        b.input.title = "";
      }
    });
  }

  // A person's cell disagrees when any agent of theirs holds a different answer
  // for that project. Shown only while the row is COLLAPSED: expanded, the agent
  // rows are on screen saying it themselves, and two ways of saying one thing is
  // how a screen starts contradicting itself.
  function paintDisagreements() {
    tree.people.forEach(function (p) {
      var agents = tree.agentsOf[p.user_id] || [];
      var row = tbody.querySelector('.access-arow[data-user="' + p.user_id + '"]');
      if (!row) return;
      nodes.forEach(function (n, i) {
        var td = row.children[i + 1];
        if (!td) return;
        var mixed = false;
        // An OWNER is excluded, and permanently. An owner sees every project and
        // their agents see only what they are granted, so an owner's row
        // disagrees with its agents on every private project by design (ADR
        // 0034: an owner's agent is deliberately LESS capable). Marking that
        // would be a permanent warning about the thing working correctly.
        //
        // EFFECTIVE, not stored (TASK-1114). Since grants inherit, an agent can
        // no longer be missing what its person holds — so this mark now fires
        // only when an agent has something EXTRA, which is exactly what the
        // previous version of this comment predicted would happen to it.
        if (!open[p.user_id] && p.role !== "owner") {
          for (var k = 0; k < agents.length; k++) {
            if (effective(n.id, agents[k]) !== effective(n.id, p)) { mixed = true; break; }
          }
        }
        td.classList.toggle("is-mixed", mixed);
        var mark = td.querySelector(".access-mixed");
        if (mark) {
          mark.title = mixed
            ? p.username + " and their agents disagree about " + (n.name || n.id)
              + ": an agent holds a grant of its own that " + p.username
              + " does not. Expand the row to see which."
            : "";
        }
      });
    });
  }

  save.addEventListener("click", function () {
    var pending = [];
    nodes.forEach(function (n) { if (dirtyNodes[n.id]) pending.push(n); });
    if (!pending.length) return;
    save.disabled = true;
    status.textContent = "saving…";
    status.className = "access-status";

    var results = pending.map(function (n) {
      var users = [];
      for (var uid in granted[n.id]) { if (granted[n.id][uid]) users.push(uid); }
      return putJSON("/api/access/node/" + encodeURIComponent(n.id), {
        visibility: visSelects[n.id].value,
        grants: users,
        csrf_token: CSRF_TOKEN
      }).then(function (res) {
        // Repaint from the SERVER's answer, never from what was sent: shared
        // discards the stored mark, so the two differ whenever a rule rewrote
        // the request, and showing the request back reports a state the
        // database does not hold.
        visSelects[n.id].value = res.visibility || "shared";
        granted[n.id] = {};
        (res.grants || []).forEach(function (uid) { granted[n.id][uid] = true; });
        boxes.forEach(function (b) {
          if (b.node === n.id) b.input.checked = !!granted[n.id][b.user];
        });
        dirtyNodes[n.id] = false;
        setColumnStatus(n.id, "", "");
        return null;
      }, function (err) {
        // Named per COLUMN. A batch that reports "3 of 5 saved" and stops there
        // leaves the owner to work out which two, on a screen where the wrong
        // guess is an access control.
        setColumnStatus(n.id, "not saved", "is-bad");
        return (n.name || n.id) + ": " + ((err && err.message) ? err.message : "failed");
      });
    });

    Promise.all(results).then(function (errs) {
      var failed = errs.filter(function (e) { return !!e; });
      repaint();
      if (!failed.length) {
        status.textContent = "saved";
        status.className = "access-status is-ok";
        save.disabled = true;
      } else {
        status.textContent = failed.join("; ");
        status.className = "access-status is-bad";
        save.disabled = false;   // a stale CSRF token is one more click, not a dead end
      }
    });
  });

  function setColumnStatus(nodeId, text, cls) {
    var th = table.querySelector('th[data-node="' + nodeId + '"]');
    if (!th) return;
    var s = th.querySelector(".access-col-status");
    if (!s) return;
    s.textContent = text;
    s.className = "access-col-status " + (cls || "");
  }

  // A fragment, not a wrapper div: the two children are the scrolling table and
  // the action bar, and a wrapping element would need a class that styles
  // nothing — dead vocabulary that outlives whoever added it.
  var frag = document.createDocumentFragment();
  frag.appendChild(wrap);
  frag.appendChild(actions);
  repaint();
  return frag;
}

// --------------------------------------------------- access audit trail
//
// `GET /api/auth-events` shipped with no reader at all — the only way to see
// it was `psql` or a raw curl. This renders it. Same owner-only gate and same
// reason as the access matrix above: these rows carry login/lockout detail
// about EVERY account (including failed logins, which often name no account
// at all), and there is no per-row visibility axis for a member to be
// filtered against — see the route's own comment in app.py.
//
// WHAT IS DELIBERATELY NOT RENDERED. `ip` and `user_agent` ride on every row
// and stay off this screen: they answer "trace this one incident", not "who
// changed what, and when", which is the question this pane exists to answer
// first. Putting a client IP address on screen by default is easy to add and
// hard to justify removing later, so it is left out of a first pass rather
// than added because it was there. `detail` is attached as the row's `title`
// (a hover, not a column) for the same reason — supplementary, not primary.
// An owner who needs any of the three still has the endpoint directly.
//
// PAGED, NEVER COLLECTED. `frontend/mine.js` pages through a person's WHOLE
// assigned list because that list is bounded. This table is not — it "grows
// with every login" — so collecting it the same way would mean an unbounded
// fetch loop every time Settings opens. Ordinary server-side paging instead:
// one page on screen, the true total and the exact range always stated next
// to it, Newer/Older to move. Nothing here is ever a silent prefix of a
// bigger list standing in for the whole of it — the standard `mine.js` itself
// insists on (see its own header comment on this exact failure mode).
function loadAuthEvents(host) {
  var page = 1;
  var PAGE_SIZE = 50;   // the API's own default (AUTH_EVENT_LIST_DEFAULT_PAGE_SIZE)

  function fetchPage() {
    clear(host);
    host.appendChild(el("p", "settings-note", "Loading…"));
    fetchJSON("/api/auth-events?page=" + page + "&page_size=" + PAGE_SIZE).then(
      function (data) {
        clear(host);
        host.appendChild(renderAuthEventsPage(data));
      },
      function (err) {
        // Distinguishable from the empty case below on purpose — "nothing
        // happened" and "we could not find out" must never look the same.
        clear(host);
        var msg = el("p", "settings-warn");
        msg.textContent = "Could not load the access audit trail: "
          + (err && err.message ? err.message : "unknown error");
        host.appendChild(msg);
      }
    );
  }

  function renderAuthEventsPage(data) {
    var items = data.items || [];
    var total = data.total || 0;

    if (!total) {
      // A fresh install, or an owner who has never had a failed login, reads
      // as "nothing has happened yet" — not as a table that quietly rendered
      // zero rows for a reason nobody can tell from the screen.
      return el("p", "settings-note", "No access-related events recorded yet.");
    }

    var frag = document.createDocumentFragment();
    var wrap = el("div", "access-scroll");
    var table = el("table", "access-table");

    var thead = el("thead");
    var hr = el("tr");
    ["Time", "Event", "Account", "Actor", "Reason"].forEach(function (h) {
      hr.appendChild(el("th", null, h));
    });
    thead.appendChild(hr);
    table.appendChild(thead);

    var tbody = el("tbody");
    items.forEach(function (row) {
      var tr = el("tr");
      // ISO 8601 with the T swapped for a space and the fraction/zone dropped
      // — this pane says "when", not "to the millisecond, in what zone".
      var at = row.at ? String(row.at).replace("T", " ").slice(0, 19) : "—";
      tr.appendChild(el("td", null, at));
      tr.appendChild(el("td", null, row.event || "—"));
      // The SUBJECT of the event — whose login, whose access changed — not
      // who did it. A row whose account was since deleted still resolves
      // (`_auth_event_json` returns `account: null` for it) rather than
      // going blank, so the row says that plainly instead of leaving a gap.
      var acct = row.account ? row.account.username
        : (row.user_id ? "(deleted account)" : "—");
      tr.appendChild(el("td", null, acct));
      tr.appendChild(el("td", null, row.actor || "—"));
      tr.appendChild(el("td", null, row.reason || "—"));
      if (row.detail) tr.title = row.detail;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    frag.appendChild(wrap);

    var from = (page - 1) * PAGE_SIZE + 1;
    var to = from + items.length - 1;
    var bar = el("div", "access-actions");
    // ALWAYS the server's total and the server's own count for this page —
    // never a number this module guessed — so the range on screen can never
    // disagree with what was actually fetched.
    bar.appendChild(el("span", "access-status",
      "Showing " + from + "–" + to + " of " + total));

    var newer = el("button", "cred-btn is-quiet", "Newer");
    newer.type = "button";
    newer.disabled = page <= 1;
    newer.addEventListener("click", function () { page -= 1; fetchPage(); });
    bar.appendChild(newer);

    var older = el("button", "cred-btn is-quiet", "Older");
    older.type = "button";
    older.disabled = to >= total;
    older.addEventListener("click", function () { page += 1; fetchPage(); });
    bar.appendChild(older);

    frag.appendChild(bar);
    return frag;
  }

  fetchPage();
}

// ------------------------------------------------------- agent credentials
//
// THE SCREEN THAT MAKES THE EPIC'S TITLE TRUE (TASK-1117): a contributor points
// an agent at Atlas without a shell. It replaces three operator-only commands —
// `ssh` to the server, `admin bot add`, `admin token add` — with a form.
//
// FOR EVERYONE, not owner-only, unlike Security & access above it. A member
// managing their own credentials is administering nobody, and every route
// behind this section is scoped to the caller's own agents in the query.
//
// WHAT THIS SECTION MUST NOT SOFTEN. The CLI shows a secret exactly once and
// makes you choose a scope; a GUI that is gentler than the shell it replaces is
// a downgrade wearing a nicer hat. So: read-only is preselected, `admin` is not
// offered to a member AT ALL (a control that always fails is worse than an
// absent one), the password field is part of the form with its reason beside
// it, and the secret panel is rendered from the response and never from state.
function loadCredentials(host) {
  fetchJSON("/api/me/agent-credentials").then(function (data) {
    clear(host);
    host.appendChild(renderCredentials(data, host));
  }, function (err) {
    clear(host);
    var msg = el("p", "settings-warn");
    msg.textContent = "Could not load your credentials: "
      + (err && err.message ? err.message : "unknown error");
    host.appendChild(msg);
  });
}

function reloadCredentials(host) {
  loadCredentials(host);
}

// The secret, shown once. NEVER stored: not in `state`, not in localStorage,
// not in the URL, and not re-rendered from anything. It exists in one DOM node
// and in the clipboard if the reader asks for it, and closing the panel is the
// end of it — which the panel says BEFORE it is closed.
function renderSecret(token, agent, host) {
  var box = el("div", "cred-secret");
  box.appendChild(el("div", "cred-secret-head", "Copy this now — it is not shown again"));
  var val = el("code", "cred-secret-value", token);
  box.appendChild(val);

  var row = el("div", "cred-secret-actions");
  var copy = el("button", "cred-btn", "Copy");
  copy.type = "button";
  var said = el("span", "cred-status");
  said.setAttribute("aria-live", "polite");
  copy.addEventListener("click", function () {
    // `navigator.clipboard` needs a SECURE CONTEXT, and every origin this
    // board runs on now is one. Measured 2026-08-25 on both live hosts:
    // `window.isSecureContext === true` and the API is present on
    // the LAN origin (https://atlas-lan.example.com:8443) and the public one
    // (https://atlas.example.com) alike. This comment used to say the LAN origin was plain HTTP and that
    // execCommand was "the real path there" — true before `caddy-lan`
    // terminated TLS, false since. There is no insecure origin left to fall
    // back FOR: `:8095` binds loopback only, and the one plain-HTTP port
    // (staging `:8096`) cannot hold a session at all, because the cookie is
    // `__Host-` prefixed and therefore `Secure` — so this panel is
    // unreachable there by construction.
    //
    // The fallback still EARNS ITS PLACE, for a different reason than the one
    // it was written for: `writeText` returns a promise that REJECTS at
    // runtime in a perfectly secure context — clipboard permission denied, or
    // the document not focused. That is why it is wired as the rejection
    // handler below rather than as an `isSecureContext` branch. Do not delete
    // it as dead code.
    //
    // Both paths need genuine user activation, so a scripted `.click()`
    // reports a false failure; that is a testing note, not a bug.
    function fell_back() {
      var ta = document.createElement("textarea");
      ta.value = token;
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      said.textContent = ok ? "copied" : "select it and copy by hand";
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(token).then(function () {
        said.textContent = "copied";
      }, fell_back);
    } else {
      fell_back();
    }
  });
  row.appendChild(copy);

  var done = el("button", "cred-btn is-quiet", "I have saved it");
  done.type = "button";
  done.addEventListener("click", function () {
    // The ONLY way out, and it destroys the value rather than hiding it.
    if (box.parentNode) box.parentNode.removeChild(box);
    reloadCredentials(host);
  });
  row.appendChild(done);
  row.appendChild(said);
  box.appendChild(row);

  box.appendChild(el("p", "cred-note",
    "Put it in the agent's configuration, never in a chat and never in a repository. "
    + "It belongs to " + agent + " and can be revoked below at any time."));
  return box;
}

function credentialForm(data, host, opts) {
  // `opts.agent` set => rotation for an existing agent; unset => a new one.
  var rotating = !!(opts && opts.agent);
  var wrap = el("div", "cred-form");

  var purpose = null;
  if (!rotating) {
    var pl = el("label", "cred-label", "What is this agent for?");
    pl.setAttribute("for", "credPurpose");
    wrap.appendChild(pl);
    purpose = document.createElement("input");
    purpose.id = "credPurpose";
    purpose.className = "cred-input";
    purpose.type = "text";
    purpose.placeholder = "claude-code";
    wrap.appendChild(purpose);
    // SAY WHY THE NAME MATTERS WHERE IT IS TYPED. Usernames are permanent and
    // are never renamed, so a name chosen for the wrong reason is wrong forever
    // — and the account is created from it.
    wrap.appendChild(el("p", "cred-note",
      "Name it for its PURPOSE, not for yourself — your username is added "
      + "automatically. Agent names are permanent and cannot be renamed."));
  }

  var sl = el("label", "cred-label", "What may it do?");
  wrap.appendChild(sl);
  var scopes = el("div", "cred-scopes");
  var available = data.scopes_available || ["read"];
  var boxes = [];
  available.forEach(function (sc) {
    var lab = el("label", "cred-scope");
    var b = document.createElement("input");
    b.type = "checkbox";
    b.className = "cred-check";
    b.value = sc;
    // READ-ONLY IS THE DEFAULT and write is a deliberate second choice.
    if (sc === "read") b.checked = true;
    lab.appendChild(b);
    lab.appendChild(el("span", "cred-scope-name", sc));
    lab.appendChild(el("span", "cred-scope-why",
      sc === "read" ? "see the board"
        : sc === "write" ? "file and edit work"
        : "delete things"));
    scopes.appendChild(lab);
    boxes.push(b);
  });
  wrap.appendChild(scopes);
  // `admin` is absent for a member rather than present and refused — the server
  // decides what is offered here, so no control on this screen can be one that
  // is guaranteed to fail.

  var el_days = el("label", "cred-label", "Expires after");
  wrap.appendChild(el_days);
  var days = el("select", "cred-input");
  var bounds = data.expiry_days || { min: 1, max: 365 };
  [[30, "30 days"], [90, "90 days"], [180, "180 days"], [365, "365 days"]].forEach(function (p) {
    if (p[0] < bounds.min || p[0] > bounds.max) return;
    var o = document.createElement("option");
    o.value = String(p[0]);
    o.textContent = p[1];
    if (p[0] === 90) o.selected = true;
    days.appendChild(o);
  });
  wrap.appendChild(days);

  // SSO PEOPLE HAVE NO PASSWORD HERE (TASK-1415). With ATLAS_OIDC_STEP_UP on,
  // the server says `reauth: "sso"` and they confirm with a fresh sign-in at
  // their identity provider instead; it comes back to the board with ?stepup=1,
  // which reopens Settings on this pane. Everyone else (and every install
  // without step-up) gets the password field exactly as before.
  var sso = data.reauth === "sso";
  var pw = document.createElement("input");
  if (sso) {
    // CONSENT IS NAMED (TASK-1415): the step-up approves ONE credential, by name,
    // and the server refuses a mint of any other name. Say which before the IdP.
    var prefix = data.agent_prefix || "";
    var account = data.account || "";
    var nameOf = function () {
      return rotating ? opts.agent
        : prefix + "-" + ((purpose && purpose.value.trim()) || "<purpose>");
    };
    var sentence = el("p", "cred-note");
    var say = function () {
      sentence.textContent = (rotating
        ? "You are issuing a new credential for the agent " + nameOf()
        : "You are creating an agent credential named " + nameOf())
        + " for " + account + ".";
    };
    say();
    if (purpose) purpose.addEventListener("input", say);
    var left = data.step_up_seconds_left || 0;
    var approved = data.step_up_agent || "";
    // Back from the IdP: the form was rebuilt, so put the approved purpose back.
    if (left > 0 && approved && purpose && !rotating
        && approved.indexOf(prefix + "-") === 0) {
      purpose.value = approved.slice(prefix.length + 1);
      say();
    }
    wrap.appendChild(sentence);
    if (left > 0 && approved === nameOf()) {
      wrap.appendChild(el("p", "cred-note",
        "Confirmed at your identity provider for " + approved + ". Create it within "
        + Math.max(1, Math.floor(left / 60)) + " min, or confirm again."));
    } else {
      var su = el("button", "cred-btn", "Confirm with SSO");
      su.type = "button";
      su.addEventListener("click", function () {
        var what = rotating ? "agent=" + encodeURIComponent(opts.agent)
          : "purpose=" + encodeURIComponent(purpose ? purpose.value.trim() : "");
        location.href = data.step_up_url + "?" + what + "&next="
          + encodeURIComponent(location.pathname + "?stepup=1");
      });
      wrap.appendChild(su);
      wrap.appendChild(el("p", "cred-note",
        "You sign in at your identity provider again first, for this one credential, "
        + "because it is a key that outlives your session. Without it, anyone who got "
        + "hold of your signed-in browser could make one."));
    }
  } else {
    var pw_label = el("label", "cred-label", "Your password");
    wrap.appendChild(pw_label);
    pw.className = "cred-input";
    pw.type = "password";
    pw.autocomplete = "current-password";
    wrap.appendChild(pw);
    // THE REASON, BESIDE THE FIELD. Somebody asked to re-enter a password they
    // just used deserves to know why, and "a stolen session is not a stolen
    // credential" is the whole of it.
    wrap.appendChild(el("p", "cred-note",
      "Asked again because this creates a key that outlives your session. "
      + "Without it, anyone who got hold of your signed-in browser could make one."));
  }

  var actions = el("div", "cred-form-actions");
  var status = el("span", "cred-status");
  status.setAttribute("aria-live", "polite");
  var go = el("button", "cred-btn is-primary",
              rotating ? "Issue a replacement" : "Create the credential");
  go.type = "button";
  actions.appendChild(status);
  actions.appendChild(go);
  wrap.appendChild(actions);

  go.addEventListener("click", function () {
    var wanted = boxes.filter(function (b) { return b.checked; })
                      .map(function (b) { return b.value; });
    var payload = {
      purpose: purpose ? purpose.value : (opts.agent || ""),
      scopes: wanted,
      expires_days: parseInt(days.value, 10),
      csrf_token: CSRF_TOKEN
    };
    if (!sso) payload.password = pw.value;
    go.disabled = true;
    status.textContent = "creating…";
    status.className = "cred-status";
    var url = rotating
      ? "/api/me/agent/" + encodeURIComponent(opts.agent) + "/credential"
      : "/api/me/agent-credential";
    postJSON(url, payload).then(function (res) {
      pw.value = "";                       // out of the DOM at once
      go.disabled = false;
      status.textContent = "";
      wrap.parentNode.insertBefore(renderSecret(res.token, res.agent, host), wrap);
      if (res.old_still_valid) {
        var warn = el("p", "cred-warn");
        warn.textContent = "The previous credential for " + res.agent
          + " still works. Deploy this one, check it, then revoke the old one below "
          + "— in that order, so the agent never goes dark.";
        wrap.parentNode.insertBefore(warn, wrap);
      }
    }, function (err) {
      go.disabled = false;
      // The server names the field it refused; showing its sentence is more
      // use than a generic failure, and every one of them is written to be read.
      status.textContent = (err && err.message) ? err.message : "could not create it";
      status.className = "cred-status is-bad";
    });
  });
  return wrap;
}

function renderCredentials(data, host) {
  var frag = document.createDocumentFragment();
  var agents = data.agents || [];

  if (!agents.length) {
    frag.appendChild(el("p", "settings-note",
      "You have no agents yet. Create one below and point it at Atlas — it will "
      + "see exactly the projects you can see, with nothing else to set up."));
  }

  agents.forEach(function (a) {
    var card = el("div", "cred-agent");
    var head = el("div", "cred-agent-head");
    head.appendChild(el("span", "cred-agent-name", a.agent));
    if (a.disabled) head.appendChild(el("span", "access-acct-tag is-off", "disabled"));
    card.appendChild(head);

    var live = (a.credentials || []).filter(function (c) { return c.live; });
    if (!live.length) {
      card.appendChild(el("p", "cred-note",
        "No usable credential. The agent still exists and is still yours — issue "
        + "a replacement below."));
    }

    (a.credentials || []).forEach(function (c) {
      var row = el("div", "cred-row");
      row.appendChild(el("code", "cred-prefix", c.prefix));
      row.appendChild(el("span", "cred-scopes-txt", (c.scopes || []).join(", ")));

      var state = el("span", "cred-state");
      if (c.revoked_at) { state.textContent = "revoked"; state.className += " is-off"; }
      else if (c.expired) { state.textContent = "expired"; state.className += " is-off"; }
      else if (c.never_used) { state.textContent = "never used"; state.className += " is-warn"; }
      else if (c.days_unused !== null && c.days_unused >= 90) {
        state.textContent = "unused " + c.days_unused + " days"; state.className += " is-warn";
      } else { state.textContent = "in use"; state.className += " is-ok"; }
      row.appendChild(state);

      row.appendChild(el("span", "cred-when",
        "expires " + (c.expires_at || "").slice(0, 10)));

      if (c.live) {
        var revoke = el("button", "cred-btn is-quiet", "Revoke");
        revoke.type = "button";
        var confirmWrap = el("span", "cred-confirm");
        revoke.addEventListener("click", function () {
          // IN PLACE, never `confirm()`. A browser modal blocks the page, is
          // not the house pattern, and cannot say what this particular revoke
          // will and will not do.
          clear(confirmWrap);
          confirmWrap.appendChild(el("span", "cred-note",
            "Revoke now? It stops working immediately. The agent itself stays."));
          var yes = el("button", "cred-btn is-danger", "Revoke it");
          yes.type = "button";
          yes.addEventListener("click", function () {
            yes.disabled = true;
            postJSON("/api/me/agent-credential/" + encodeURIComponent(c.id) + "/revoke",
                     { csrf_token: CSRF_TOKEN })
              .then(function () { reloadCredentials(host); },
                    function (err) {
                      yes.disabled = false;
                      clear(confirmWrap);
                      confirmWrap.appendChild(el("span", "cred-status is-bad",
                        (err && err.message) ? err.message : "could not revoke"));
                    });
          });
          var no = el("button", "cred-btn is-quiet", "Keep it");
          no.type = "button";
          no.addEventListener("click", function () { clear(confirmWrap); });
          confirmWrap.appendChild(yes);
          confirmWrap.appendChild(no);
        });
        row.appendChild(revoke);
        row.appendChild(confirmWrap);
      }
      card.appendChild(row);
    });

    // ROTATION, on the agent it belongs to. Names are permanent, so this is the
    // only way to replace a credential without inventing a second agent.
    var rotate = el("details", "cred-rotate");
    var sum = el("summary", null, "Issue a replacement credential");
    rotate.appendChild(sum);
    rotate.appendChild(credentialForm(data, host, { agent: a.agent }));
    card.appendChild(rotate);

    frag.appendChild(card);
  });

  var maker = el("details", "cred-new");
  if (!agents.length) maker.open = true;
  maker.appendChild(el("summary", null, "Create an agent credential"));
  maker.appendChild(credentialForm(data, host, null));
  frag.appendChild(maker);
  return frag;
}
