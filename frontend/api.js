// api.js — every fetch the board makes, plus CSRF token handling and the
// account slot the token comes attached to. Moved out of board.html's inline
// script verbatim (TASK-1023, step 4 of STORY-atlas-board-modularize).
//
// CSRF, precisely: POST /api/node/{id}/status goes through auth.guard_write,
// which applies an Origin allow-list plus a synchronizer token for cookie
// sessions (Bearer principals are exempt — no ambient credential to abuse).
// The token comes from GET /api/me (`csrf_token` on the response) and is
// cached in the module-level CSRF_TOKEN below for as long as the page lives —
// there is no refresh: a session that outlives its token (a long-open tab
// after a server restart) will get a 403 back from postJSON, surfaced next to
// the control that sent it (see renderStatusSection in panel.js), not
// silently retried. initAccount() is the one and only writer of CSRF_TOKEN;
// everything else — including panel.js's status-write code — only ever reads
// it, via the live import binding ES modules give a reader for a name only
// the exporting module reassigns (see state.js's header for the general rule
// this is a instance of).
//
// safeLocalPath/authFailure/fetchJSON/CSRF_TOKEN/postJSON stayed adjacent in
// board.html because postJSON's 401 handling reuses authFailure and fetchJSON
// shares its shape; that adjacency carried over unchanged into this file.
// renderAccount/initAccount came along too: capturing CSRF_TOKEN and painting
// the account slot happen in the same GET /api/me response handler, and
// splitting them across two files would only reintroduce the ordering bug the
// original comment already warns about (capture the token BEFORE
// renderAccount, which returns early for a principal with no username).

// Auth navigation happens at most once per page life: the boot fires several
// fetches in parallel and an expired session fails all of them, so without
// this latch each one would race to call location.replace().
var authNavigated = false;

// Only ever navigate to a same-origin ABSOLUTE PATH. The value below comes
// from our own API, but validating it here means no server response can ever
// turn into an off-site redirect ("//evil.example" is a protocol-relative URL,
// not a path — that is the case the second test catches).
function safeLocalPath(p, fallback) {
  if (typeof p !== "string" || p.charAt(0) !== "/" || p.charAt(1) === "/") return fallback;
  return p;
}

// 401/403 is not a data error and must never be painted as one. The board is
// gated by AuthGate (TASK-967): /api/* NEVER redirects, it answers JSON, so
// the navigation decision belongs here.
//   401 -> the session is gone or was never there  -> /auth/login?next=…
//   403 + {"error":"password_change_required"}     -> the forced-change screen
// The returned promise deliberately NEVER SETTLES. That is load-bearing, not a
// trick: callers downstream would otherwise paint "Could not load /api/graph"
// across a page that is already navigating away, and the user would read an
// error message instead of a login form. If the navigation itself throws, we
// reject with an "AUTH …" message so the boot handler can tell an auth
// failure apart from a data failure.
function authFailure(response, url) {
  return new Promise(function (resolve, reject) {
    function go(body) {
      var dest = "/auth/login?next=" + encodeURIComponent(location.pathname + location.search);
      if (body && (body.error === "password_change_required" || body.change_password)) {
        dest = safeLocalPath(body.change_password, "/auth/password/change");
      }
      if (authNavigated) return;            // another fetch already won the race
      authNavigated = true;
      try {
        // replace(), not assign(): the page we are leaving is a board that
        // failed to load, and Back should not return to it.
        location.replace(dest);
      } catch (e) {
        reject(new Error("AUTH " + response.status + " for " + url));
      }
    }
    // The reason lives in the JSON body; a body that will not parse still
    // means "not authenticated", so both paths navigate.
    response.json().then(go, function () { go(null); });
  });
}

export function fetchJSON(url) {
  return fetch(url, { headers: { Accept: "application/json" } }).then(function (r) {
    if (r.status === 401 || r.status === 403) return authFailure(r, url);
    if (!r.ok) throw new Error("HTTP " + r.status + " for " + url);
    return r.json();
  });
}

// The synchronizer token from GET /api/me. Every state-changing POST carries
// it; the raw session id never reaches JavaScript (see renderAccount below).
// Only initAccount() ever assigns to this — everything else imports it and
// reads the live binding.
export var CSRF_TOKEN = null;

// POST, for the board's one write surface (the status control).
//
// It does NOT reuse fetchJSON's 401/403 handling, and the difference matters:
// there, both mean "not authenticated" and navigating to the login page is
// right. Here a 403 is a CSRF/Origin refusal on a session that is still
// perfectly valid — bouncing to a login form the user is already past would
// hide a real, fixable error (usually a stale token after a long-open tab).
// Only 401 navigates; 403 surfaces next to the control that caused it.
export function postJSON(url, payload) {
  return sendJSON("POST", url, payload);
}

// PUT, for the access-administration surface (settings.js). Same body, same
// token, same error handling as postJSON — the method is the only difference,
// so the two share one implementation rather than drifting apart the first
// time somebody fixes an error message in one of them.
export function putJSON(url, payload) {
  return sendJSON("PUT", url, payload);
}

// PATCH, for the workitem write API (the assignee control, TASK-1122). Third
// verb, same implementation, same reason as putJSON above: one place to fix an
// error message rather than three that drift.
export function patchJSON(url, payload) {
  return sendJSON("PATCH", url, payload);
}

function sendJSON(method, url, payload) {
  return fetch(url, {
    method: method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload)
  }).then(function (r) {
    if (r.status === 401) return authFailure(r, url);
    return r.json().catch(function () { return {}; }).then(function (body) {
      if (!r.ok) throw new Error(body.detail || body.error || ("HTTP " + r.status));
      return body;
    });
  });
}

// -------------------------------------------------------------- account slot
// The app bar slot has been empty since the board existed, with a comment
// saying why ("a control with nothing behind it lies"). GET /api/me is the
// thing that was missing: the board is a static file, so the signed-in
// username can only come from the API.
//
// Everything below is built with createElement + textContent, never
// innerHTML. Both values it renders — the username and the CSRF token —
// arrive from the server, and the whole shape of this story is that no
// server-supplied value is ever parsed as HTML.
export function renderAccount(me) {
  var slot = document.getElementById("accountSlot");
  if (!slot || !me || !me.username) return;
  while (slot.firstChild) slot.removeChild(slot.firstChild);

  // The USERNAME, not an email. The username is the credential now; showing
  // an email that is only ever contact metadata would invite someone to sign
  // in with the wrong field.
  var trigger = document.createElement("button");
  trigger.type = "button";
  trigger.id = "acctMenuBtn";
  trigger.className = "acct-trigger";
  trigger.setAttribute("aria-haspopup", "menu");
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute("aria-controls", "acctMenu");
  trigger.title = me.username;

  var name = document.createElement("span");
  name.className = "acct-name";
  name.textContent = me.username;
  trigger.appendChild(name);
  var caret = document.createElement("span");
  caret.className = "acct-caret";
  caret.setAttribute("aria-hidden", "true");
  caret.textContent = "⌄";
  trigger.appendChild(caret);
  slot.appendChild(trigger);

  var menu = document.createElement("div");
  menu.id = "acctMenu";
  menu.className = "acct-menu";
  menu.setAttribute("role", "menu");
  menu.hidden = true;

  // The role is stated, not inferred from what happens to be on screen. An
  // owner and a member see genuinely different boards now, so "which am I"
  // stops being a curiosity the moment two accounts compare notes.
  var who = document.createElement("div");
  who.className = "acct-who";
  var whoName = document.createElement("div");
  whoName.className = "acct-who-name";
  whoName.textContent = me.name || me.username;
  who.appendChild(whoName);
  var whoRole = document.createElement("div");
  whoRole.className = "acct-who-role";
  whoRole.textContent = me.role === "owner" ? "owner" : "member";
  who.appendChild(whoRole);
  menu.appendChild(who);

  // Settings is a real button, not a link: it opens an overlay in this
  // document rather than navigating, and a link that does not navigate is a
  // lie to anything that reads the page (middle-click, screen readers, the
  // status bar). board.html wires the click — this module never imports
  // settings.js, so the menu still renders if that module fails to load.
  var settings = document.createElement("button");
  settings.type = "button";
  settings.id = "acctSettings";
  settings.className = "acct-item";
  settings.setAttribute("role", "menuitem");
  settings.textContent = "Settings";
  menu.appendChild(settings);

  var change = document.createElement("a");
  change.className = "acct-item";
  change.setAttribute("role", "menuitem");
  change.href = "/auth/password/change";
  change.textContent = "Change password";
  menu.appendChild(change);

  // Sign-out is a POST carrying the token, never a link — a link is a
  // state-changing GET and link prefetchers follow those. A service principal
  // has no cookie session to end and /api/me hands it no token, so it gets no
  // button rather than a button that would be refused.
  if (me.kind === "session" && me.csrf_token) {
    var form = document.createElement("form");
    form.className = "acct-form";
    form.method = "post";
    form.action = "/auth/logout";
    var token = document.createElement("input");
    token.type = "hidden";
    token.name = "csrf_token";
    token.value = me.csrf_token;
    form.appendChild(token);
    var btn = document.createElement("button");
    btn.type = "submit";
    btn.className = "acct-item is-signout";
    btn.setAttribute("role", "menuitem");
    btn.textContent = "Sign out";
    form.appendChild(btn);
    menu.appendChild(form);
  }
  slot.appendChild(menu);

  function setOpen(open) {
    menu.hidden = !open;
    trigger.setAttribute("aria-expanded", open ? "true" : "false");
  }
  trigger.addEventListener("click", function (ev) {
    ev.stopPropagation();
    setOpen(menu.hidden);
  });
  // Close on an outside click or Escape. Without both, the menu stays open
  // over the map and swallows the next click on a node.
  document.addEventListener("click", function (ev) {
    if (!menu.hidden && !slot.contains(ev.target)) setOpen(false);
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && !menu.hidden) { setOpen(false); trigger.focus(); }
  });

  slot.classList.add("is-signed-in");
}

// Fetches GET /api/me once, captures its CSRF token and paints the account
// slot. A failure here is deliberately silent. A 401/403 has already
// navigated inside fetchJSON; anything else leaves the slot exactly as the
// board shipped for two years — 30px of reserved, honest emptiness — rather
// than claiming an identity the board could not confirm. board.html calls
// this once at boot, at the same point in the load order the inline version
// used to run at.
// RETURNS the promise now, resolving with the principal (or null on failure),
// so board.html can wire the Settings item only once it knows who is signed in.
// Nothing awaits it for the account slot itself — that still paints from inside.
export function initAccount() {
  return fetchJSON("/api/me").then(function (me) {
    // Capture the token BEFORE renderAccount, which returns early for a
    // principal with no username — the status control would then be dead for
    // a reason nothing on screen explains.
    if (me && me.csrf_token) CSRF_TOKEN = me.csrf_token;
    renderAccount(me);
    return me;
  }, function () { return null; });
}
