// mine.js — the Mine view (TASK-1124/1125): a third top-level view beside the
// World and Work trees, showing the work assigned to somebody.
//
// WHY ITS OWN VIEW rather than a filter on the Work tree (owner decision, 2026-08-17):
// the Work tree answers "what is being built"; Mine answers "what should I do
// next". They want different defaults — the tree wants the whole shape, Mine
// wants the open items with the finished ones out of the way — and stacking
// two purposes on one tree makes both worse.
//
// IT IS NOT A MAP. World and Work are both d3 tidy-trees; this is a list. That
// is the whole reason it needs its own container rather than another
// `buildTree()` call, and it is why `switchTab("mine")` in board.html hides the
// canvas instead of rebuilding it.
//
// NOTHING IS COUNTED HERE. Every item and every number comes from
// `/api/workitems?assignee=`, so what is on screen is what the server — with
// the access filter already applied — said this principal may see. A count
// computed in the browser over an already-filtered list is a number nobody can
// reconcile against the panel, and under the access layer it is arithmetic
// over rows the caller may not hold all of.
//
// NO ACCOUNT NAME OR ID APPEARS IN THIS FILE. Same rule as the graph: if a
// module has to name a person to work, that is a missing endpoint.
import { fetchJSON } from "/assets/api.js";
import { remember, recall, escapeHtml, dismissOnScrim } from "/assets/util.js";
import { statusBucket, STATUS_CHOICES } from "/assets/state.js";

// board.html fills these in — same idiom as map.js/panel.js, which keep their
// internals private and expose a hooks object for the code they cannot import.
export var hooks = {
  openNode: function () {},     // (id) -> focus it in whichever tree owns it
};

var GROUPINGS = [
  { value: "tree", label: "epic / story / task" },
  { value: "status", label: "status" }
];

// The ORDER statuses read in when grouped by status: what is moving first,
// what is stuck next, what is finished last. Alphabetical would put "blocked"
// above "in progress", which is the opposite of a to-do list's job. Both
// tables are keyed on the CANONICAL statuses (state.js's STATUS_CHOICES) —
// every raw value is bucketed through statusBucket() before it reaches
// either one, so a synonym like "planned" groups with "pending" instead of
// forming its own group sorted last.
var STATUS_ORDER = ["in_progress", "blocked", "analyze", "pending", "done", "cancelled"];
var STATUS_LABEL = {
  in_progress: "In progress", blocked: "Blocked", analyze: "Analyze",
  pending: "Pending", done: "Done", cancelled: "Cancelled"
};

var TYPE_RANK = { epic: 0, story: 1, task: 2 };

var root = null;          // the container board.html handed us
var listEl = null;
var whoBtn = null;        // the "Showing …" link that opens the people picker
var groupSel = null;
// WHOSE LIST IS ON SCREEN, held here rather than read back off a control.
// It used to live on a <select>'s `value`/`selectedOptions[0].textContent`,
// which worked only because the control happened to carry both halves; the
// picker's button carries prose ("Bjorn (me)") and the API wants an account
// id, so the two are kept apart explicitly. `whoId` is what load() sends —
// an account id, or the literal "me" the API also accepts — and `whoText` is
// what the empty-state sentence names.
var whoId = "me";
var whoText = "Me";
// The roster, cached from `/api/people`. `null` = never successfully loaded,
// which is what makes the picker able to retry rather than showing an empty
// list as though nobody existed. NO NAME OR ID IS WRITTEN DOWN HERE — see the
// file header; every row in it came from the server on this page load.
var roster = null;
// The signed-in principal's OWN account id. `/api/me` calls it `user_id`;
// `/api/people` calls the same thing `id`. Normalised once, here, rather than
// left for each comparison to get right — the first version compared
// `p.id === me.id`, which is always false because `me.id` does not exist, so
// the selector silently defaulted to whoever sorted first instead of to you.
// It happened to look correct for the one account whose name sorts first.
var myId = null;
var loadSeq = 0;          // guards against an out-of-order response
// The last payload the server gave us. A collapse toggle is a pure re-render —
// refetching 372 rows to fold one branch would be absurd, and would also lose
// the fold on every click.
var lastItems = [], lastTotal = 0, lastTruncated = false;

// ============================================================ narrowing (this iteration)
//
// AXES CHOSEN, and why these three: Mine can run to hundreds of rows and had
// no way to cut that down. The maps (board.html) already answer this exact
// question for a tree — "hide what doesn't match, keep the route to what
// does" — on three axes: age, status, type. Mine has no completion-age
// display of its own (no date column, and the age filter's whole point is
// hiding OLD finished work, which "Group by status" already surfaces
// directly), so age is left out; the other two transfer untouched:
//
//   Status — the exact STATUS_CHOICES vocabulary (state.js) the map's status
//     filter and the panel's status control both use, bucketed through the
//     same statusBucket() so "planned" matches a "pending" checkbox same as
//     everywhere else in this app. Same widget idiom, too: a button that
//     opens a checkbox menu (.multiselect-ctl/.multiselect-btn/
//     .multiselect-menu/.multiselect-opt — all classes board.css already
//     ships for the map's identical control, reused verbatim, no new rule).
//   Type — epic/story/task, collected from what is actually loaded
//     (refreshNarrowTypeMenu) rather than hardcoded, same principle
//     collectTypes() in board.html follows and the same reason this repo's
//     own house rule gives: a vocabulary a query can answer is not something
//     to write down by hand.
//   Find — the one axis the maps do NOT have as a view filter (their search
//     box is a global jump-to-node, not a narrowing of what's drawn). A
//     person working from a list of "my work" most wants to jump straight to
//     one item by its id or name, so this is a plain substring match over
//     both, reusing the .search-input pill for its look.
//
// PERSISTENCE: Status and Type ride remember()/recall() under atlas.* —
// `atlas.mineStatusFilter` / `atlas.mineTypeFilter` — the same idiom and the
// same trade board.html already accepted for the identical two axes on the
// map (`atlas.statusFilter`/`atlas.typeFilter`): a filter that quietly stays
// applied across a reload is a real risk ("why is my list empty"), and the
// map's answer is not silence, it's a permanent, clickable, always-visible
// hint while anything is hidden. This view carries the same hint (see
// renderNarrowSummary). Find is NOT persisted — there is no map precedent
// for persisting free text, and unlike a checkbox set a stale query is
// something you'd have to have typed sometime, not something you toggled
// once and forgot existed.
//
// ANCESTOR RULE: Mine's tree grouping puts context rows (ancestors of an
// assigned item, greyed, not assigned themselves — see renderByTree's own
// comment below) above the items you actually hold. Narrowing must not
// strand a matching task under a hidden epic. The rule, matching the map's
// own leaf decision exactly: narrowing is evaluated ONLY on real (assigned)
// rows; a context row is kept if, and only if, something below it survived.
// A context row can never itself "match" a Status/Type/Find narrowing — it
// has no status of its own worth filtering and matching it by name would let
// an unrelated epic with a matching title stay on screen with nothing of
// yours under it, which is what this view exists to avoid.
//
// COLLAPSE INTERACTION: while narrowing is active, every surviving row
// renders EXPANDED regardless of `collapsed[id]` — a match hidden behind a
// fold a person set three items ago would fail acceptance criterion 3
// outright, and there is no way to tell, from the collapsed placeholder
// alone, that something inside it now matches. `collapsed` itself is left
// untouched, so clearing every narrowing axis restores the exact fold state
// that was there before (criterion 4). The one honest cost: the twisty stays
// clickable and keyboard-operable while narrowed (criterion 6 does not lapse
// for it) but has no visible effect until narrowing clears, because the
// force-expand overrides it either way — it still records the preference,
// it just doesn't get to act on it yet.
//
// TRUNCATION: narrowing runs over `lastItems`, the same possibly-truncated,
// already-paged-through array the un-narrowed view already warns about (see
// load()'s PAGE_GUARD comment). Narrowing something already known to be a
// prefix cannot make it complete, so the truncation warning's wording
// changes, while truncated, to say plainly that the narrowed view only ever
// searches what was loaded — seeing fewer results than expected under a
// truncated + narrowed view might mean "no match", or might mean "the match
// is past the load limit", and only the warning can tell those apart.
var narrowStatus = {};    // status bucket -> true. {} = unrestricted (same convention as state.FILTER.statuses, minus the null/{} distinction — {} reads as "nothing forced off" either way here)
var narrowType = {};      // node type -> true. {} = unrestricted
var narrowText = "";      // lowercased substring, "" = unrestricted
var NARROW_STATUS_KEY = "atlas.mineStatusFilter";
var NARROW_TYPE_KEY = "atlas.mineTypeFilter";

var narrowBar = null;
var findInput = null;
var statusFilterBtn = null, statusFilterMenu = null;
var typeFilterBtn = null, typeFilterMenu = null;
var narrowClearBtn = null;

function hasAnyKey(o) {
  if (!o) return false;
  for (var k in o) { if (Object.prototype.hasOwnProperty.call(o, k)) return true; }
  return false;
}

function csvToSet(csv) {
  var out = {};
  if (!csv) return out;
  csv.split(",").forEach(function (v) { v = v.trim(); if (v) out[v] = 1; });
  return out;
}
function setToCsv(set) { return Object.keys(set).sort().join(","); }

function isNarrowingActive() {
  return hasAnyKey(narrowStatus) || hasAnyKey(narrowType) || !!narrowText;
}

// Applies to ASSIGNED rows only — see "ANCESTOR RULE" above for why a
// context row is never tested against this directly.
function itemMatchesNarrow(w) {
  if (hasAnyKey(narrowStatus)) {
    var b = statusBucket(w.status) || "pending";
    if (!narrowStatus[b]) return false;
  }
  if (hasAnyKey(narrowType)) {
    if (!narrowType[w.type]) return false;
  }
  if (narrowText) {
    var hay = (String(w.id || "") + " " + String(w.name || "")).toLowerCase();
    if (hay.indexOf(narrowText) === -1) return false;
  }
  return true;
}

function updateMultiselectLabel(btn, set, plural) {
  if (!btn) return;
  if (!hasAnyKey(set)) { btn.textContent = "all"; return; }
  var keys = Object.keys(set).sort();
  btn.textContent = keys.length === 1 ? keys[0] : (keys.length + " " + plural);
}

function closeMultiselect(menu, btn) {
  if (!menu || menu.hidden) return;
  menu.hidden = true;
  if (btn) btn.setAttribute("aria-expanded", "false");
}
function toggleMultiselect(menu, btn) {
  if (!menu || !btn) return;
  var opening = menu.hidden;
  closeMultiselect(statusFilterMenu, statusFilterBtn);
  closeMultiselect(typeFilterMenu, typeFilterBtn);
  if (opening) { menu.hidden = false; btn.setAttribute("aria-expanded", "true"); }
}

function toggleStatusValue(value, checked) {
  if (checked) narrowStatus[value] = 1; else delete narrowStatus[value];
  remember(NARROW_STATUS_KEY, setToCsv(narrowStatus));
  updateMultiselectLabel(statusFilterBtn, narrowStatus, "statuses");
  redraw();
}
function toggleTypeValue(value, checked) {
  if (checked) narrowType[value] = 1; else delete narrowType[value];
  remember(NARROW_TYPE_KEY, setToCsv(narrowType));
  updateMultiselectLabel(typeFilterBtn, narrowType, "types");
  redraw();
}

function buildStatusFilterMenu() {
  if (!statusFilterMenu) return;
  statusFilterMenu.innerHTML = "";
  STATUS_CHOICES.forEach(function (choice) {
    var row = document.createElement("label");
    row.className = "multiselect-opt";
    var cb = document.createElement("input");
    cb.type = "checkbox"; cb.value = choice.value;
    cb.checked = !!narrowStatus[choice.value];
    cb.addEventListener("change", function () { toggleStatusValue(choice.value, cb.checked); });
    row.appendChild(cb);
    row.appendChild(document.createTextNode(STATUS_LABEL[choice.value] || choice.label));
    statusFilterMenu.appendChild(row);
  });
}

// Type vocabulary is whatever is actually assigned right now — collected
// fresh from `items` every time the underlying list changes (called from
// load()'s success path), never a hand-written epic/story/task list. Kept
// separate from the checkbox TOGGLE handler (which must not tear this menu
// down — see the file-level comment on the collapse/redraw split) so a
// mid-menu click never loses its own checkbox out from under the pointer.
function refreshNarrowTypeMenu(items) {
  if (!typeFilterMenu) return;
  var seen = {};
  items.forEach(function (w) { if (w.type) seen[w.type] = 1; });
  var types = Object.keys(seen).sort(function (a, b) {
    var ta = TYPE_RANK[a], tb = TYPE_RANK[b];
    if (ta !== tb) return (ta == null ? 9 : ta) - (tb == null ? 9 : tb);
    return a < b ? -1 : a > b ? 1 : 0;
  });
  typeFilterMenu.innerHTML = "";
  if (!types.length) { typeFilterMenu.innerHTML = '<div class="multiselect-empty">no items</div>'; return; }
  types.forEach(function (t) {
    var row = document.createElement("label");
    row.className = "multiselect-opt";
    var cb = document.createElement("input");
    cb.type = "checkbox"; cb.value = t;
    cb.checked = !!narrowType[t];
    cb.addEventListener("change", function () { toggleTypeValue(t, cb.checked); });
    row.appendChild(cb);
    row.appendChild(document.createTextNode(t));
    typeFilterMenu.appendChild(row);
  });
  updateMultiselectLabel(typeFilterBtn, narrowType, "types");
}

function clearNarrowing() {
  narrowStatus = {}; narrowType = {}; narrowText = "";
  remember(NARROW_STATUS_KEY, "");
  remember(NARROW_TYPE_KEY, "");
  if (findInput) findInput.value = "";
  buildStatusFilterMenu();
  refreshNarrowTypeMenu(lastItems);
  updateMultiselectLabel(statusFilterBtn, narrowStatus, "statuses");
  updateMultiselectLabel(typeFilterBtn, narrowType, "types");
  redraw();
}

function buildNarrowBar() {
  narrowBar = document.createElement("div");
  narrowBar.className = "mine-ctl";

  var findWrap = document.createElement("span");
  findWrap.className = "filter-ctl";
  var findLabel = document.createElement("label");
  findLabel.setAttribute("for", "mineFind");
  findLabel.textContent = "Find";
  findInput = document.createElement("input");
  findInput.type = "text";
  findInput.id = "mineFind";
  findInput.className = "search-input";
  findInput.placeholder = "id or name…";
  findInput.setAttribute("autocomplete", "off");
  findInput.title = "Narrow this list to items whose id or name matches";
  findInput.addEventListener("input", function () {
    narrowText = findInput.value.trim().toLowerCase();
    redraw();
  });
  findWrap.appendChild(findLabel);
  findWrap.appendChild(findInput);

  var statusWrap = document.createElement("span");
  statusWrap.className = "filter-ctl multiselect-ctl";
  var statusLabel = document.createElement("label");
  statusLabel.id = "mineStatusFilterLabel";
  statusLabel.setAttribute("for", "mineStatusFilterBtn");
  statusLabel.textContent = "Status";
  statusFilterBtn = document.createElement("button");
  statusFilterBtn.type = "button"; statusFilterBtn.id = "mineStatusFilterBtn";
  statusFilterBtn.className = "multiselect-btn";
  statusFilterBtn.setAttribute("aria-haspopup", "true");
  statusFilterBtn.setAttribute("aria-expanded", "false");
  statusFilterBtn.setAttribute("aria-controls", "mineStatusFilterMenu");
  statusFilterBtn.title = "Restrict this list to one or more statuses";
  statusFilterMenu = document.createElement("div");
  statusFilterMenu.className = "multiselect-menu";
  statusFilterMenu.id = "mineStatusFilterMenu";
  statusFilterMenu.hidden = true;
  statusFilterBtn.addEventListener("click", function () { toggleMultiselect(statusFilterMenu, statusFilterBtn); });
  statusWrap.appendChild(statusLabel);
  statusWrap.appendChild(statusFilterBtn);
  statusWrap.appendChild(statusFilterMenu);

  var typeWrap = document.createElement("span");
  typeWrap.className = "filter-ctl multiselect-ctl";
  var typeLabel = document.createElement("label");
  typeLabel.id = "mineTypeFilterLabel";
  typeLabel.setAttribute("for", "mineTypeFilterBtn");
  typeLabel.textContent = "Type";
  typeFilterBtn = document.createElement("button");
  typeFilterBtn.type = "button"; typeFilterBtn.id = "mineTypeFilterBtn";
  typeFilterBtn.className = "multiselect-btn";
  typeFilterBtn.setAttribute("aria-haspopup", "true");
  typeFilterBtn.setAttribute("aria-expanded", "false");
  typeFilterBtn.setAttribute("aria-controls", "mineTypeFilterMenu");
  typeFilterBtn.title = "Restrict this list to one or more node types";
  typeFilterMenu = document.createElement("div");
  typeFilterMenu.className = "multiselect-menu";
  typeFilterMenu.id = "mineTypeFilterMenu";
  typeFilterMenu.hidden = true;
  typeFilterBtn.addEventListener("click", function () { toggleMultiselect(typeFilterMenu, typeFilterBtn); });
  typeWrap.appendChild(typeLabel);
  typeWrap.appendChild(typeFilterBtn);
  typeWrap.appendChild(typeFilterMenu);

  narrowClearBtn = document.createElement("button");
  narrowClearBtn.type = "button";
  narrowClearBtn.id = "mineNarrowClear";
  narrowClearBtn.className = "multiselect-btn";
  narrowClearBtn.textContent = "Clear filters";
  narrowClearBtn.hidden = true;
  narrowClearBtn.addEventListener("click", clearNarrowing);

  narrowBar.appendChild(findWrap);
  narrowBar.appendChild(statusWrap);
  narrowBar.appendChild(typeWrap);
  narrowBar.appendChild(narrowClearBtn);

  document.addEventListener("click", function (e) {
    if (statusFilterMenu && !statusFilterMenu.hidden && !statusFilterMenu.contains(e.target) && e.target !== statusFilterBtn) {
      closeMultiselect(statusFilterMenu, statusFilterBtn);
    }
    if (typeFilterMenu && !typeFilterMenu.hidden && !typeFilterMenu.contains(e.target) && e.target !== typeFilterBtn) {
      closeMultiselect(typeFilterMenu, typeFilterBtn);
    }
  });

  buildStatusFilterMenu();
  updateMultiselectLabel(statusFilterBtn, narrowStatus, "statuses");
  updateMultiselectLabel(typeFilterBtn, narrowType, "types");

  return narrowBar;
}

// ============================================================ the people picker
//
// WHY AN OVERLAY AND NOT A <select> (owner decision, STORY-atlas-people-picker). The
// roster came out of `/api/people` into a native dropdown, which is fine at
// the size the board launched with and gets worse on its own: a <select> has
// no filter, so the only way to find somebody is to read the whole list, and
// the list grows every time a contributor is added. A freetext filter over
// name AND username stays the same size whatever the roster does.
//
// NO SECOND FETCH PATH, in either direction. The roster has ONE loader
// (`loadRoster`, called from initMine and again from the picker only when the
// first attempt left `roster` null), and picking somebody calls the module's
// existing `load()` — the picker writes `whoId`/`whoText` and nothing else.
// Anything that wants to know whose list is on screen reads those two, which
// is why nothing outside this section knows the control changed shape.
//
// THE SHELL IS SETTINGS.JS'S SHELL, deliberately. `.settings-overlay` /
// `.settings-sheet` / `.settings-head` / `.settings-close` / `.settings-body`
// are generic modal chrome despite the name — compose.js already reuses them
// for "File work" — and a third near-identical block of CSS for the same box
// is exactly the drift AGENTS.md warns about. So is the behaviour: Escape
// closes, `dismissOnScrim` closes on a backdrop click without eating a drag
// that merely ENDED on the scrim (util.js carries that fix precisely so the
// next overlay inherits it), and closing returns focus to the control that
// opened it. board.css adds only `.people-*`.
//
// KEYBOARD. The filter box takes focus on open, so typing narrows without a
// click. ArrowDown/ArrowUp walk REAL BUTTONS rather than an aria-activedescendant
// listbox — each row is a `<button>`, so Enter and Space are native and this
// module reimplements neither. From the filter box ArrowDown enters the list;
// from the first row ArrowUp goes back to the filter box, so a person can
// keep typing without reaching for the mouse. Home/End jump to the ends, and
// Enter in the filter box picks the first row that survived the filter — the
// "type three letters and commit" gesture the old <select> gave for free.
var peopleOverlay = null;
var peopleFilter = null;
var peopleListEl = null;
var peopleStatus = null;
var peopleLastFocus = null;

function peopleLabel(p) {
  // "name (me)" — the same text the <select> carried, so the empty-state
  // sentence ("Nothing is assigned to …") reads exactly as it did.
  return (p.name || p.username) + (myId && p.id === myId ? " (me)" : "");
}

// The one roster loader. Resolves either way — a picker that can still be
// opened and says why it is empty beats one that throws into a console.
function loadRoster() {
  return fetchJSON("/api/people").then(function (d) {
    roster = d.people || [];
    return roster;
  }, function () {
    roster = null;
    return null;
  });
}

function setWho(id, text) {
  whoId = id;
  whoText = text;
  if (whoBtn) whoBtn.textContent = text;
  load();
}

function peopleOptions() {
  return peopleListEl
    ? Array.prototype.slice.call(peopleListEl.querySelectorAll(".people-opt"))
    : [];
}

function focusOption(i) {
  var opts = peopleOptions();
  if (!opts.length) { if (peopleFilter) peopleFilter.focus(); return; }
  if (i < 0) { if (peopleFilter) peopleFilter.focus(); return; }
  if (i >= opts.length) i = opts.length - 1;
  opts[i].focus();
}

function renderPeopleList() {
  if (!peopleListEl) return;
  peopleListEl.innerHTML = "";
  if (roster === null) {
    peopleStatus.textContent = "The roster could not be loaded. Close this and try again — "
      + "your own list is still on screen.";
    peopleStatus.hidden = false;
    return;
  }
  var q = peopleFilter ? peopleFilter.value.trim().toLowerCase() : "";
  // NAME AND USERNAME BOTH. A person known to one colleague by their display
  // name and to another by the account they push commits with must be
  // findable either way; matching one field would make half the roster
  // unsearchable for half the readers.
  var shown = roster.filter(function (p) {
    if (!q) return true;
    var hay = ((p.name || "") + " " + (p.username || "")).toLowerCase();
    return hay.indexOf(q) !== -1;
  });

  if (!shown.length) {
    // Distinguishable from a failed load above, on purpose — "nobody matches
    // what you typed" and "we could not find out who exists" must never look
    // the same.
    peopleStatus.textContent = roster.length
      ? "No account matches that."
      : "No accounts to choose from.";
    peopleStatus.hidden = false;
    return;
  }
  peopleStatus.hidden = true;

  shown.forEach(function (p) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "people-opt" + (p.id === whoId ? " is-current" : "");
    b.setAttribute("data-person", p.id);
    // Pressed, not checked: this is a set of buttons, and exactly one of them
    // describes what is currently on screen.
    b.setAttribute("aria-pressed", p.id === whoId ? "true" : "false");
    var name = document.createElement("span");
    name.className = "people-opt-name";
    name.textContent = peopleLabel(p);
    var user = document.createElement("span");
    user.className = "people-opt-user";
    // The username is shown as well as matched on — a filter that matches
    // something invisible reads as a bug ("why did THAT row survive?").
    user.textContent = p.username || "";
    b.appendChild(name);
    b.appendChild(user);
    b.addEventListener("click", function () {
      setWho(p.id, peopleLabel(p));
      closePeoplePicker();
    });
    peopleListEl.appendChild(b);
  });
}

function buildPeopleOverlay() {
  var rootEl = document.createElement("div");
  rootEl.className = "settings-overlay";
  rootEl.id = "minePeopleOverlay";
  rootEl.setAttribute("role", "dialog");
  rootEl.setAttribute("aria-modal", "true");
  rootEl.setAttribute("aria-labelledby", "minePeopleTitle");
  rootEl.hidden = true;

  // Local: nothing outside buildPeopleOverlay needs the sheet, and a module
  // variable nobody reads is state that outlives its reason.
  var sheet = document.createElement("div");
  sheet.className = "settings-sheet people-sheet";

  var head = document.createElement("header");
  head.className = "settings-head";
  var title = document.createElement("h2");
  title.id = "minePeopleTitle";
  title.textContent = "Whose work to show";
  head.appendChild(title);
  var close = document.createElement("button");
  close.type = "button";
  close.className = "settings-close";
  close.id = "minePeopleClose";
  close.textContent = "×";
  close.setAttribute("aria-label", "Close");
  close.addEventListener("click", closePeoplePicker);
  head.appendChild(close);
  sheet.appendChild(head);

  var body = document.createElement("div");
  body.className = "settings-body";

  peopleFilter = document.createElement("input");
  peopleFilter.type = "text";
  peopleFilter.id = "minePeopleFilter";
  peopleFilter.className = "cred-input people-filter";
  peopleFilter.placeholder = "Filter by name or username…";
  peopleFilter.setAttribute("autocomplete", "off");
  peopleFilter.setAttribute("aria-label", "Filter people by name or username");
  peopleFilter.setAttribute("aria-controls", "minePeopleList");
  peopleFilter.addEventListener("input", renderPeopleList);
  body.appendChild(peopleFilter);

  peopleStatus = document.createElement("p");
  peopleStatus.className = "settings-note people-status";
  peopleStatus.setAttribute("aria-live", "polite");
  peopleStatus.hidden = true;
  body.appendChild(peopleStatus);

  peopleListEl = document.createElement("div");
  peopleListEl.className = "people-list";
  peopleListEl.id = "minePeopleList";
  body.appendChild(peopleListEl);

  sheet.appendChild(body);
  rootEl.appendChild(sheet);
  document.body.appendChild(rootEl);

  dismissOnScrim(rootEl, closePeoplePicker);

  sheet.addEventListener("keydown", function (ev) {
    var opts = peopleOptions();
    var idx = opts.indexOf(document.activeElement);
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      focusOption(idx + 1);
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      // From the first row, back to the filter box rather than wrapping to
      // the end: the filter is where the next keystroke usually wants to go.
      focusOption(idx <= 0 ? -1 : idx - 1);
    } else if (ev.key === "Home" && idx >= 0) {
      ev.preventDefault();
      focusOption(0);
    } else if (ev.key === "End" && idx >= 0) {
      ev.preventDefault();
      focusOption(opts.length - 1);
    } else if (ev.key === "Enter" && document.activeElement === peopleFilter) {
      // Only when something survived the filter — Enter over an empty list
      // must not pick whoever happened to be first before you typed.
      if (opts.length) { ev.preventDefault(); opts[0].click(); }
    }
  });

  // Escape is global, matching settings.js/compose.js: the overlay may hold
  // focus in a text field, and a keydown handler bound to the sheet alone
  // would miss a press made while focus sat on the scrim.
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && peopleOverlay && !peopleOverlay.hidden) closePeoplePicker();
  });

  return rootEl;
}

export function closePeoplePicker() {
  if (!peopleOverlay) return;
  peopleOverlay.hidden = true;
  document.body.classList.remove("people-open");
  // Back to the control that opened it — the one thing a keyboard user loses
  // for good if an overlay forgets to do it.
  if (peopleLastFocus && peopleLastFocus.focus) peopleLastFocus.focus();
}

function openPeoplePicker() {
  if (!peopleOverlay) peopleOverlay = buildPeopleOverlay();
  peopleLastFocus = document.activeElement;
  peopleOverlay.hidden = false;
  document.body.classList.add("people-open");
  peopleFilter.value = "";      // a stale query is not a preference worth keeping
  peopleStatus.hidden = true;

  if (roster === null) {
    // The init fetch failed (or has not landed). Retry here rather than
    // leaving the picker permanently broken for the rest of the session —
    // still the one loader, not a second fetch path.
    peopleListEl.innerHTML = "";
    peopleStatus.textContent = "Loading…";
    peopleStatus.hidden = false;
    loadRoster().then(renderPeopleList);
  } else {
    renderPeopleList();
  }
  peopleFilter.focus();
}

export function initMine(container, principal) {
  root = container;
  myId = principal ? (principal.user_id || principal.id || null) : null;

  narrowStatus = csvToSet(recall(NARROW_STATUS_KEY));
  narrowType = csvToSet(recall(NARROW_TYPE_KEY));
  narrowText = "";

  var bar = document.createElement("div");
  bar.className = "mine-ctl";

  var whoWrap = document.createElement("span");
  whoWrap.className = "filter-ctl";
  var whoLabel = document.createElement("label");
  // `for` still points at `mineWho` — a <button> is a labelable element, so
  // the label keeps working across the change of element type, and the id
  // itself is a contract (frontend/CLAUDE.md): moved and restyled, never
  // renamed or dropped.
  whoLabel.setAttribute("for", "mineWho");
  whoLabel.textContent = "Showing";
  whoBtn = document.createElement("button");
  whoBtn.type = "button";
  whoBtn.id = "mineWho";
  whoBtn.className = "multiselect-btn mine-who-btn";
  whoBtn.title = "Choose whose assigned work to show";
  // A dialog, not a menu — aria-haspopup says which, so a screen reader
  // announces what pressing this actually does.
  whoBtn.setAttribute("aria-haspopup", "dialog");
  whoBtn.textContent = whoText;
  whoBtn.addEventListener("click", openPeoplePicker);
  whoWrap.appendChild(whoLabel);
  whoWrap.appendChild(whoBtn);

  var groupWrap = document.createElement("span");
  groupWrap.className = "filter-ctl";
  var groupLabel = document.createElement("label");
  groupLabel.setAttribute("for", "mineGroup");
  groupLabel.textContent = "Group by";
  groupSel = document.createElement("select");
  groupSel.id = "mineGroup";
  GROUPINGS.forEach(function (g) {
    var o = document.createElement("option");
    o.value = g.value; o.textContent = g.label;
    groupSel.appendChild(o);
  });
  // Epic/story/task is the default — that is the "tree structure" in the ask,
  // and it is what makes a bare task id mean something.
  groupSel.value = recall("atlas.mineGroup") === "status" ? "status" : "tree";
  groupWrap.appendChild(groupLabel);
  groupWrap.appendChild(groupSel);

  bar.appendChild(whoWrap);
  bar.appendChild(groupWrap);
  root.appendChild(bar);
  root.appendChild(buildNarrowBar());

  listEl = document.createElement("div");
  listEl.className = "mine-list";
  root.appendChild(listEl);

  groupSel.addEventListener("change", function () {
    remember("atlas.mineGroup", groupSel.value);
    load();
  });
  // The roster, so an owner can look at somebody else's list. Deliberately NOT
  // remembered across reloads: a view left pointing at another person is a trap
  // for its own author — three weeks later an empty list reads as "I have no
  // work" rather than "you are looking at Bjorn". It resets to you every load,
  // and the control says whose list it is while you are on it.
  //
  // THE DEFAULT IS YOU, and if the roster cannot say which row is you it is
  // the literal "me" the API resolves from the session — never whoever
  // happened to sort first, which is what the old <select> silently fell back
  // to when `myId` did not match a row.
  loadRoster().then(function (people) {
    var mine = null;
    (people || []).forEach(function (p) { if (myId && p.id === myId) mine = p; });
    if (mine) setWho(mine.id, peopleLabel(mine));
    else setWho("me", (principal && (principal.name || principal.username)) || "Me");
  });
}

function setMessage(kind, text) {
  listEl.innerHTML = "";
  var p = document.createElement("div");
  p.className = kind;      // mine-empty | mine-loading | mine-error
  p.textContent = text;
  listEl.appendChild(p);
}

export function load() {
  if (!listEl) return;
  var seq = ++loadSeq;
  var who = whoId || "me";
  collapsed = {};   // a fold belongs to the list it was made on

  // Loading and empty must not look the same. A blank pane that is still
  // fetching reads as "you have no work", which is a lie people act on.
  setMessage("mine-loading", "Loading…");

  // PAGE THROUGH. The server caps page_size (200), and the first version asked
  // for 500, got 200, and rendered them under the label "200 items" — a
  // truncated list presented as a complete one. That is the silent cap this
  // repo's own principles forbid: if coverage is bounded, say what was
  // dropped. Now it collects every page and the label is the real total.
  var acc = [];
  var PAGE_GUARD = 40;   // 40 x 200 = 8,000 items; a backstop, not a policy

  function page(n) {
    return fetchJSON("/api/workitems?assignee=" + encodeURIComponent(who) +
                     "&include_ancestors=1&page=" + n)
      .then(function (d) {
        if (seq !== loadSeq) return null;          // a later request superseded this
        acc = acc.concat(d.items || []);
        var more = acc.length < (d.total || 0) && (d.items || []).length > 0;
        if (more && n < PAGE_GUARD) return page(n + 1);
        // If the guard stopped us short, SAY SO rather than quietly showing
        // a prefix — the whole point of the fix above.
        return { total: d.total || 0, truncated: acc.length < (d.total || 0) };
      });
  }

  page(1)
    .then(function (res) {
      if (res === null || seq !== loadSeq) return;
      lastItems = acc; lastTotal = res.total; lastTruncated = res.truncated;
      refreshNarrowTypeMenu(lastItems);
      render(acc, res.total, res.truncated);
    })
    .catch(function (e) {
      if (seq !== loadSeq) return;
      setMessage("mine-error", "Could not load the work list — " +
                 (e && e.message ? e.message : "unknown error"));
    });
}


function render(items, total, truncated) {
  listEl.innerHTML = "";
  if (!items.length) {
    setMessage("mine-empty",
               "Nothing is assigned to " + (whoText || "this account") + " right now.");
    return;
  }

  var narrowing = isNarrowingActive();
  var narrowedItems = narrowing ? items.filter(itemMatchesNarrow) : items;

  var count = document.createElement("div");
  count.className = "mine-count";
  // ALWAYS the server's total. If what is drawn is fewer, the label says both
  // numbers — a count that silently shrank is indistinguishable from a
  // smaller world.
  count.textContent = items.length < total
    ? (items.length + " of " + total + " items")
    : (total + (total === 1 ? " item" : " items"));
  listEl.appendChild(count);
  if (truncated) {
    var warn = document.createElement("div");
    warn.className = "mine-error";
    // Narrowing over a KNOWN-INCOMPLETE array cannot make it complete — say
    // so explicitly the moment a person actually narrows, not just that
    // the base list is short (see the file-level "TRUNCATION" note).
    warn.textContent = narrowing
      ? ("Only the first " + items.length + " of " + total + " could be loaded, and this narrowed " +
         "view only searches those " + items.length + " — a missing result may exist among the rest.")
      : ("Only the first " + items.length +
         " could be loaded. Narrow the view rather than trusting this list to be complete.");
    listEl.appendChild(warn);
  }

  if (narrowing) {
    var summary = document.createElement("div");
    summary.className = "mine-count";
    summary.id = "mineNarrowSummary";
    summary.textContent = narrowedItems.length + " of " + items.length + " loaded match" +
      (narrowedItems.length === 1 ? "" : "es") + " the current filters.";
    listEl.appendChild(summary);
  }
  if (narrowClearBtn) narrowClearBtn.hidden = !narrowing;

  if (narrowing && !narrowedItems.length) {
    // Distinguishable from "nothing assigned" (above) on purpose — this is
    // NOT "you have no work", it is "your filters hid all of it".
    var empty = document.createElement("div");
    empty.className = "mine-empty";
    empty.textContent = "No loaded item matches the current filters. Clear filters to see everything again.";
    listEl.appendChild(empty);
    return;
  }

  if (groupSel.value === "status") {
    renderByStatus(narrowedItems);
  } else {
    // renderByTree's prune step wants an id -> true LOOKUP, not the array
    // itself (an array indexed by numeric position would make
    // `narrowedIds[n.row.id]` look up a non-existent string property and
    // always read undefined — every row would then fail "self matches" and
    // the whole tree would prune to nothing regardless of what was typed or
    // checked. Build the lookup here, once, rather than inside the
    // recursive prune()).
    var narrowedIdSet = null;
    if (narrowing) {
      narrowedIdSet = {};
      narrowedItems.forEach(function (w) { narrowedIdSet[w.id] = true; });
    }
    renderByTree(items, narrowedIdSet);
  }
}


function groupHeader(text, n) {
  var h = document.createElement("div");
  h.className = "mine-group";
  h.innerHTML = '<span class="mine-group-name"></span>' +
                '<span class="mine-group-count"></span>';
  h.querySelector(".mine-group-name").textContent = text;
  h.querySelector(".mine-group-count").textContent = n;
  return h;
}

function renderByStatus(items) {
  var buckets = {};
  items.forEach(function (w) {
    var k = statusBucket(w.status) || "pending";
    (buckets[k] = buckets[k] || []).push(w);
  });
  var keys = Object.keys(buckets).sort(function (a, b) {
    var ia = STATUS_ORDER.indexOf(a), ib = STATUS_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  keys.forEach(function (k) {
    listEl.appendChild(groupHeader(STATUS_LABEL[k] || k, buckets[k].length));
    buckets[k].sort(byTypeThenId).forEach(function (w) {
      listEl.appendChild(row(w, 0, false));
    });
  });
}

// A REAL TREE: epic > story > task, indented, collapsible (owner decision, 2026-08-18 —
// the first version sorted by TYPE, so it listed every epic, then every story,
// then every task. That is a grouped list wearing a tree's name).
//
// `?include_ancestors=1` puts the FULL `contains` chain on each row, root-first
// and already access-filtered, so the shape is assembled from data the listing
// already carried — no per-row lookup, and no reliance on the board's in-memory
// work tree, which the age filter and the detail slider prune.
//
// Ancestors are drawn as CONTEXT (greyed) unless they are themselves assigned.
// The distinction has to survive, or a story of yours with no assigned children
// reads as a heading over nothing.
var collapsed = {};        // node id -> true when its children are folded away

// `narrowedIds` is null when narrowing is off (draw the whole tree, respect
// `collapsed` normally) or a Set-like {id:true} of the items that survived
// itemMatchesNarrow — see the file-level "ANCESTOR RULE" / "COLLAPSE
// INTERACTION" comments for the two rules this implements.
function renderByTree(items, narrowedIds) {
  var assigned = {};
  items.forEach(function (w) { assigned[w.id] = true; });

  // Build the forest. Each node is {row, kids[]}; `row` is the workitem itself
  // for an assigned item, or a bare {id,type,name} for a context ancestor.
  var byId = {};
  var roots = [];

  function nodeFor(rec) {
    var n = byId[rec.id];
    if (!n) { n = byId[rec.id] = { row: rec, kids: [], ctx: !assigned[rec.id] }; }
    // An assigned item may first appear as somebody's ancestor; upgrade it.
    if (assigned[rec.id]) { n.row = rec.status !== undefined ? rec : n.row; n.ctx = false; }
    return n;
  }

  items.forEach(function (w) {
    var chain = (w.ancestors || []).concat([w]);
    var parent = null;
    chain.forEach(function (rec) {
      var n = nodeFor(rec);
      if (parent) {
        if (parent.kids.indexOf(n) === -1) parent.kids.push(n);
      } else if (roots.indexOf(n) === -1) {
        roots.push(n);
      }
      parent = n;
    });
  });

  function sortKids(n) {
    n.kids.sort(byTypeThenId2);
    n.kids.forEach(sortKids);
  }
  roots.sort(byTypeThenId2);
  roots.forEach(sortKids);

  // Prune to the narrowed set, keeping any ancestor with a surviving
  // descendant — the SAME leaf decision board.html's pruneTree uses for the
  // map's age/status/type filters ("a node survives if ... anything below it
  // survived"). A context row is never tested directly (n.ctx is always
  // excluded from the "self matches" test) — see "ANCESTOR RULE" above.
  function prune(n) {
    var kids = [];
    n.kids.forEach(function (k) {
      var kept = prune(k);
      if (kept) kids.push(kept);
    });
    var selfMatches = !n.ctx && narrowedIds && narrowedIds[n.row.id];
    if (!selfMatches && !kids.length) return null;
    return { row: n.row, kids: kids, ctx: n.ctx };
  }
  var drawRoots = roots;
  if (narrowedIds) {
    drawRoots = [];
    roots.forEach(function (r) { var kept = prune(r); if (kept) drawRoots.push(kept); });
  }

  function draw(n, depth) {
    var hasKids = n.kids.length > 0;
    // Narrowing overrides the fold — see "COLLAPSE INTERACTION" above.
    // `collapsed` itself is untouched, so this is purely a rendering choice.
    var isCollapsed = narrowedIds ? false : !!collapsed[n.row.id];
    listEl.appendChild(row(n.row, depth, n.ctx, hasKids, isCollapsed));
    if (hasKids && !isCollapsed) n.kids.forEach(function (k) { draw(k, depth + 1); });
  }
  drawRoots.forEach(function (r) { draw(r, 0); });
}

function byTypeThenId2(a, b) { return byTypeThenId(a.row, b.row); }

function redraw() { render(lastItems, lastTotal, lastTruncated); }

// redraw() starts with `listEl.innerHTML = ""` (see render/setMessage above),
// which tears the focused twisty out of the document -- focus falls to
// <body> and a keyboard user folding one row is thrown to the top of the
// list. Every row carries its workitem id as `data-row-id`, so the same row
// can be found again after the rebuild and refocused. Called ONLY from the
// twisty's own click handler, right after the redraw it caused.
function focusRowTwisty(id) {
  if (!listEl || !listEl.querySelector) return;
  var sel = '.mine-row[data-row-id="' + String(id).replace(/"/g, '\\"') + '"] .mine-twisty.is-toggle';
  var el = listEl.querySelector(sel);
  if (el && el.focus) el.focus();
}

function byTypeThenId(a, b) {
  var ta = TYPE_RANK[a.type], tb = TYPE_RANK[b.type];
  if (ta !== tb) return (ta == null ? 9 : ta) - (tb == null ? 9 : tb);
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

// `context` = drawn only because it CONTAINS something assigned, not because it
// is assigned itself. The distinction has to be visible or a story of yours
// with no assigned children reads as a heading over nothing.
//
// TWO SIBLING BUTTONS, NOT ONE BUTTON WITH A CONTROL NESTED INSIDE IT. The
// original markup was a single `<button class="mine-row">` with a `<span
// role="button">` twisty inside it — invalid (interactive content is not
// allowed inside a <button>'s content model), unreachable by keyboard for the
// fold action (a role="button" span with no tabindex is not focusable at
// all, and even if it had been, a keydown on the OUTER button always reports
// itself as the target, never the child), and incoherent to a screen reader.
// The fix is structural rather than a keydown handler bolted on: the twisty
// and the "open" surface are real, independent, non-nested `<button>`
// elements side by side inside a plain `<div class="mine-row">`. Each gets
// its own tab stop, and Enter/Space on either is native <button> behaviour —
// nothing here reimplements the keyboard, which is exactly why it cannot
// drift out of sync with the mouse gesture again.
//
// THE ROW ITSELF OPENS, TOO. `.mine-row` (board.css) draws hover/pointer
// across its full box -- the indent, the horizontal and vertical padding,
// the gap between the twisty and the open button -- and before this file's
// keyboard-fold fix that whole box WAS the button, so all of it opened the
// node. Restricting the click target to just `.mine-row-open` after the
// restructure left that indent/padding/gap strip showing a live affordance
// that did nothing, which is its own defect. Resolved by giving `el` itself
// a click handler: a click anywhere in the row opens the node UNLESS it
// originated on the twisty, which stops it from bubbling here (see below).
// That keeps every highlighted pixel truthful rather than shrinking the
// highlight to match a narrower hit area.
function row(w, depth, context, hasKids, isCollapsed) {
  var el = document.createElement("div");
  el.className = "mine-row" + (context ? " is-context" : "");
  el.style.paddingLeft = (10 + depth * 20) + "px";
  // Lets a focused twisty be found again by id after a redraw tears the DOM
  // down and rebuilds it — see focusRowTwisty() above.
  el.setAttribute("data-row-id", w.id);

  var twistyHtml;
  if (hasKids) {
    // The arrow is a CSS triangle, not a character — see board.css for why.
    // The class/aria pair only says WHICH way it points; board.css owns the
    // triangle itself and is untouched by this fix.
    twistyHtml = '<button type="button" class="mine-twisty is-toggle' +
      (isCollapsed ? ' is-collapsed' : '') + '" aria-expanded="' +
      (isCollapsed ? "false" : "true") + '" title="' +
      (isCollapsed ? "Expand" : "Collapse") + '" aria-label="' +
      (isCollapsed ? "Expand" : "Collapse") + '"></button>';
  } else {
    // A leaf gets an EMPTY box of the same footprint, so ids stay in one
    // column whether or not a sibling row folds. Nothing to operate here,
    // so it stays a plain, non-interactive span — a leaf never needed a tab
    // stop and adding one would be a keyboard trap of a different kind.
    twistyHtml = '<span class="mine-twisty"></span>';
  }

  el.innerHTML = twistyHtml +
    '<button type="button" class="mine-row-open">' +
      '<span class="mine-type"></span>' +
      '<span class="mine-id"></span>' +
      '<span class="mine-name"></span>' +
      '<span class="mine-status"></span>' +
    '</button>';

  // `.mine-row-open`'s layout (flex row, gap, chrome reset, cursor) is a real
  // board.css rule now — this file owns no inline style for it. It used to
  // carry `style.cssText` here because board.css belonged to another builder
  // when the twisty fix was written; that boundary has moved.

  el.querySelector(".mine-type").textContent = (w.type || "").toUpperCase();
  el.querySelector(".mine-id").textContent = w.id;
  el.querySelector(".mine-name").textContent = w.name || "";
  var st = el.querySelector(".mine-status");
  if (w.status) {
    // Bucketed, not raw — board.css's `.mine-status[data-status=...]` rules
    // (and panel.js's identical `.work-item-dot` idiom) are written against
    // the six canonical statuses, not every backlog synonym.
    var bucket = statusBucket(w.status);
    st.textContent = STATUS_LABEL[bucket] || bucket;
    st.setAttribute("data-status", bucket);
  }

  if (hasKids) {
    el.querySelector(".mine-twisty").addEventListener("click", function (e) {
      // Stop the fold gesture from ALSO opening the node via the row-level
      // handler below — a click on the twisty folds and does not open,
      // exactly as it did before the row itself became clickable.
      if (e && e.stopPropagation) e.stopPropagation();
      collapsed[w.id] = !collapsed[w.id];
      redraw();
      // redraw() rebuilds the list from scratch (listEl.innerHTML = ""),
      // which tears out whatever was focused. Put focus back on THIS row's
      // twisty so a keyboard user can keep folding down the list instead of
      // being thrown to <body> and having to tab all the way back in.
      focusRowTwisty(w.id);
    });
  }
  // One handler on the row itself, not on `.mine-row-open` alone: clicking
  // the open button (mouse, or a native Enter/Space activation) dispatches a
  // "click" that bubbles up to `el` same as a click anywhere else in the
  // row's padding/gap/indent, so this single listener covers the whole box
  // — matching what `.mine-row:hover`/`cursor:pointer` (board.css) actually
  // highlights. The twisty's own listener above stops its clicks from
  // reaching here.
  el.addEventListener("click", function () {
    hooks.openNode(w.id);
  });
  return el;
}
