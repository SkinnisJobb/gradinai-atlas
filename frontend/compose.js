// compose.js — the "File work" form: the first frontend caller of
// POST /api/workitem, which has existed since TASK-1048 with no UI in front
// of it (filing work required the MCP or curl). CREATE ONLY — editing an
// existing workitem is another builder's iteration on the detail panel this
// same round; this module never PATCHes.
//
// SEAM: same shape as settings.js — this module owns its own DOM, board.html
// holds no markup for it and only calls openCompose(me) (lazy-imported on
// click, same reason settings.js is: the form is opened by a fraction of
// board loads, and the board's no-build-step design means every static
// import is bytes on the critical path of the map drawing). Everything below
// is built with createElement/textContent, never innerHTML — a node name, a
// person's name and an epic's summary are all server-supplied strings here,
// same rule api.js/settings.js state at length: no server-supplied value is
// ever parsed as HTML.
//
// THE TWO HOOKS (board.html can't import back into an inline <script
// type="module">, same reason map.js/panel.js/mine.js all use this seam):
// `hooks.afterCreate(id)` is called once, after a successful create, so
// board.html can refetch the trees and navigate to the new node the same way
// the search box and Mine already do — see board.html's composeOpenBtn wiring
// for what it does with it. There is no `hooks.refreshGraph` etc. the way
// panel.js needs — this module reads nothing board.html owns.
//
// THE BINDING PICKER — read this before changing it. `works_on` requires a
// (workitem -> object) FAMILY edge (backend/app.py's EDGE_FAMILY_RULES); a
// bridge-family node (skill/doc/vision) is NOT a legal binding target even
// though it renders on the World tab beside the objects. state.js's
// OBJECT_TYPES deliberately bundles object+bridge together for the WORLD
// TREE's own reason (splitting world roots from work roots at boot) and is
// the wrong vocabulary to reuse here — filtering by it would let a user pick
// a `skill`/`doc`/`vision` node and then get a 409 `family_mismatch` the form
// has no reason to expect. So this asks GET /api/vocabulary for each node
// type's OWN family (the same field `create_workitem`'s `_check_family`
// enforces against) and keeps only "object" — the one authoritative place
// that split lives, per AGENTS.md ("no module may encode the graph").
//
// NO NODE ID, EPIC ID OR WORKITEM ID IS HARDCODED ANYWHERE BELOW. Every
// candidate in every picker (binding, parent, assignee) comes from a fetch
// made when the form opens.
import { fetchJSON, CSRF_TOKEN } from "/assets/api.js";
import { STATUS_CHOICES } from "/assets/state.js";
import { dismissOnScrim } from "/assets/util.js";

// board.html fills this in — see this file's header for why the seam exists.
export var hooks = { afterCreate: function () {} };

// -------------------------------------------------------------- utilities
function el(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// A fixed, hand-written label per KNOWN backend value — same idiom
// state.js's STATUS_CHOICES uses for the same reason (a type STRING is
// presentation vocabulary, not a graph id; AGENTS.md's "no module may encode
// the graph" is about ids, not about writing "Epic" for "epic").
var TYPE_LABELS = { epic: "Epic", story: "Story", task: "Task" };

var overlay = null;
var lastFocus = null;

// ---------------------------------------------------------------- the overlay
// Reuses .settings-overlay/.settings-sheet/.settings-head/.settings-close/
// .settings-body — generic modal chrome, not settings-specific despite the
// class name, and a second near-identical block of CSS for the same shell
// would be exactly the kind of drift AGENTS.md warns about ("one place to fix
// ... rather than three that drift"). board.css adds only what settings.js's
// overlay does not already have: the form itself, via .cred-input/.cred-
// label/.cred-btn/.cred-status (settings.js's own credential-form classes,
// generic enough to reuse) plus a handful of compose-only rules.
function buildOverlay() {
  var root = el("div", "settings-overlay");
  root.id = "composeOverlay";
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-modal", "true");
  root.setAttribute("aria-labelledby", "composeTitle");
  root.hidden = true;

  var sheet = el("div", "settings-sheet compose-sheet");

  var head = el("header", "settings-head");
  var title = el("h2", null, "File work");
  title.id = "composeTitle";
  head.appendChild(title);
  var close = el("button", "settings-close", "×");
  close.id = "composeClose";
  close.type = "button";
  close.setAttribute("aria-label", "Close");
  head.appendChild(close);
  sheet.appendChild(head);

  var body = el("div", "settings-body");
  body.id = "composeBody";
  sheet.appendChild(body);

  root.appendChild(sheet);
  document.body.appendChild(root);

  close.addEventListener("click", closeCompose);
  dismissOnScrim(root, closeCompose);   // the scrim, and not a drag that ended on it
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && overlay && !overlay.hidden) closeCompose();
  });
  return root;
}

export function closeCompose() {
  if (!overlay) return;
  overlay.hidden = true;
  document.body.classList.remove("compose-open");
  if (lastFocus && lastFocus.focus) lastFocus.focus();
}

// ----------------------------------------------------------------- data fetch
// One request per field per open — the form is opened rarely, and refetching
// keeps a second-open form current with anything created since the first
// (including, notably, a workitem this same form just filed).
function loadVocabulary() {
  return fetchJSON("/api/vocabulary");
}
function loadWorldObjects() {
  // The WHOLE tree (world nodes and workitems both) — same endpoint
  // board.html's own boot/refreshGraph read, so there is no second listing
  // route to keep in sync with it. Filtered client-side below by the FAMILY
  // /api/vocabulary just reported, never by a type list this module invents.
  return fetchJSON("/api/graph/tree");
}
function loadPeople() {
  // No `?for=` — there is no node yet to narrow visibility against. The
  // create route resolves/refuses the chosen assignee itself either way
  // (app._resolve_assignee); this is a convenience list, not a gate.
  return fetchJSON("/api/people");
}

function flattenTree(roots) {
  var out = [];
  (roots || []).forEach(function walk(n) {
    if (n && n.id) out.push(n);
    (n.children || []).forEach(walk);
  });
  return out;
}

// ------------------------------------------------------------ filterable picker
// A <select> (native keyboard nav, native screen-reader semantics) plus a
// plain-text filter box above it that hides non-matching <option>s — not a
// custom combobox widget, because ~70 world nodes and a growing-but-still-
// small workitem list do not need one, and a native <select> is exactly as
// searchable as this without a line of extra ARIA plumbing (Chrome respects
// `<option hidden>` — the HTML5 default `[hidden] { display: none }` rule
// applies to it like any other element).
function buildPicker(labelText, noteText) {
  var wrap = el("div", "compose-picker");
  wrap.appendChild(el("label", "cred-label", labelText));
  var filterInput = el("input", "cred-input compose-picker-filter");
  filterInput.type = "text";
  filterInput.placeholder = "Filter…";
  filterInput.autocomplete = "off";
  wrap.appendChild(filterInput);
  var select = el("select", "cred-input compose-picker-select");
  wrap.appendChild(select);
  if (noteText) wrap.appendChild(el("p", "cred-note", noteText));
  var err = el("div", "cred-status is-bad compose-field-error");
  err.hidden = true;
  wrap.appendChild(err);

  var none = document.createElement("option");
  none.value = "";
  none.textContent = "— none —";
  select.appendChild(none);

  var rows = [];   // {id, label, option}
  function setOptions(candidates) {
    while (select.childNodes.length > 1) select.removeChild(select.lastChild);
    rows = candidates.map(function (c) {
      var o = document.createElement("option");
      o.value = c.id;
      // "name (id)" — the id is what the API round-trips on, the name is
      // what a human recognises; neither alone is enough to pick correctly
      // among ~70 candidates with the same prefix.
      o.textContent = (c.name || c.id) + "  (" + c.id + ")";
      select.appendChild(o);
      return { id: c.id, label: (c.name || "") + " " + c.id, option: o };
    });
  }
  function applyFilter() {
    var q = filterInput.value.trim().toLowerCase();
    rows.forEach(function (r) {
      r.option.hidden = q.length > 0 && r.label.toLowerCase().indexOf(q) === -1;
    });
  }
  filterInput.addEventListener("input", applyFilter);

  return { wrap: wrap, select: select, filterInput: filterInput, err: err, setOptions: setOptions };
}

// -------------------------------------------------------- acceptance criteria
function buildAcceptanceSection() {
  var wrap = el("div", "compose-picker");
  wrap.appendChild(el("label", "cred-label", "Acceptance criteria (optional)"));
  var list = el("div", "compose-acc-list");
  wrap.appendChild(list);
  var add = el("button", "cred-btn is-quiet", "+ Add criterion");
  add.type = "button";
  wrap.appendChild(add);

  function addRow(value) {
    var row = el("div", "compose-acc-row");
    var input = el("input", "cred-input");
    input.type = "text";
    input.value = value || "";
    input.placeholder = "A single, checkable criterion";
    row.appendChild(input);
    var rm = el("button", "cred-btn is-quiet compose-acc-remove", "×");
    rm.type = "button";
    rm.setAttribute("aria-label", "Remove this criterion");
    rm.addEventListener("click", function () { list.removeChild(row); });
    row.appendChild(rm);
    list.appendChild(row);
    return input;
  }
  add.addEventListener("click", function () { addRow("").focus(); });

  function values() {
    return Array.prototype.slice.call(list.querySelectorAll("input"))
      .map(function (i) { return i.value.trim(); })
      .filter(function (v) { return v.length > 0; });
  }
  function reset() { clear(list); }

  return { wrap: wrap, addRow: addRow, values: values, reset: reset };
}

// --------------------------------------------------------------- the request
// Deliberately NOT api.js's postJSON: that helper (rightly, for its own
// callers) collapses a failure to `new Error(body.detail || body.error)`,
// which throws away `body.field` — exactly the thing this form needs to
// route a 400 to the control that caused it (criterion 3). This is a fetch
// wrapper, not a second write-guard implementation: guard_write/CSRF/Origin
// enforcement all still happen server-side, unchanged; this only preserves
// what the server already told us.
function postWorkitem(payload) {
  return fetch("/api/workitem", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  }).then(function (r) {
    return r.json().catch(function () { return {}; }).then(function (body) {
      if (r.status === 401) {
        // Same destination api.js's authFailure sends a GET to — a session
        // that vanished mid-form gets the same login redirect every other
        // route on the board gives it, not a silently-eaten error.
        location.replace("/auth/login?next=" + encodeURIComponent(location.pathname + location.search));
        return new Promise(function () {});   // never settles — see api.js's authFailure for why
      }
      if (!r.ok) {
        var err = new Error(body.detail || body.error || ("HTTP " + r.status));
        err.body = body;
        err.status = r.status;
        throw err;
      }
      return body;
    });
  });
}

// ------------------------------------------------------------------ the form
// Error code -> form field, for the responses that name what went wrong
// WITHOUT an explicit `field` key (invalid_assignee and invalid_attributes DO
// carry one already and are read from `body.field` directly — see
// showApiError below). Every one of these is read straight off
// create_workitem's own JSONResponse bodies in backend/app.py.
var ERROR_FIELD = {
  invalid_type: "type",
  unknown_status: "status",
  id_is_minted: "id",
  id_required: "id",
  node_exists: "id",
  unknown_parent: "parent",
  unknown_binding: "binding",
};

function buildForm() {
  var form = el("form", "cred-form");
  form.noValidate = true;

  var banner = el("div", "compose-banner");
  banner.hidden = true;
  banner.setAttribute("aria-live", "polite");
  form.appendChild(banner);

  // -- type --------------------------------------------------------------
  var typeWrap = el("div", "compose-picker");
  typeWrap.appendChild(el("label", "cred-label", "Type"));
  var typeSelect = el("select", "cred-input");
  typeWrap.appendChild(typeSelect);
  var typeErr = el("div", "cred-status is-bad compose-field-error");
  typeErr.hidden = true;
  typeWrap.appendChild(typeErr);
  form.appendChild(typeWrap);

  // -- id (slug) — epic/story only, per the id-scheme decision -----------
  var idWrap = el("div", "compose-picker");
  idWrap.appendChild(el("label", "cred-label", "Id (a short slug — a task's id is minted for you)"));
  var idInput = el("input", "cred-input");
  idInput.type = "text";
  idInput.placeholder = "e.g. atlas-widget-thing";
  idWrap.appendChild(idInput);
  var idErr = el("div", "cred-status is-bad compose-field-error");
  idErr.hidden = true;
  idWrap.appendChild(idErr);
  form.appendChild(idWrap);

  function syncIdVisibility() {
    var isTask = typeSelect.value === "task";
    idWrap.hidden = isTask;
    if (isTask) idInput.value = "";
  }
  typeSelect.addEventListener("change", syncIdVisibility);

  // -- name ----------------------------------------------------------------
  var nameWrap = el("div", "compose-picker");
  nameWrap.appendChild(el("label", "cred-label", "Name"));
  var nameInput = el("input", "cred-input");
  nameInput.type = "text";
  nameWrap.appendChild(nameInput);
  var nameErr = el("div", "cred-status is-bad compose-field-error");
  nameErr.hidden = true;
  nameWrap.appendChild(nameErr);
  form.appendChild(nameWrap);

  // -- summary ---------------------------------------------------------------
  var summaryWrap = el("div", "compose-picker");
  summaryWrap.appendChild(el("label", "cred-label", "Summary (optional, one line)"));
  var summaryInput = el("input", "cred-input");
  summaryInput.type = "text";
  summaryWrap.appendChild(summaryInput);
  form.appendChild(summaryWrap);

  // -- description (node.body) ---------------------------------------------
  var bodyWrap = el("div", "compose-picker");
  bodyWrap.appendChild(el("label", "cred-label", "Description (optional — the spec, markdown)"));
  var bodyInput = el("textarea", "cred-input compose-textarea");
  bodyWrap.appendChild(bodyInput);
  form.appendChild(bodyWrap);

  // -- acceptance ------------------------------------------------------------
  var acceptance = buildAcceptanceSection();
  form.appendChild(acceptance.wrap);

  // -- binding (works_on -> an OBJECT-family world node) --------------------
  var binding = buildPicker("Binding (optional — the part of the world this works on)",
    "Only object-family world nodes are legal targets — a skill/doc/vision "
    + "attachment cannot be bound to.");
  form.appendChild(binding.wrap);

  // -- parent (contains, workitem -> workitem) -------------------------------
  var parent = buildPicker("Parent (optional — the epic or story this belongs under)", null);
  form.appendChild(parent.wrap);

  // -- assignee --------------------------------------------------------------
  var assigneeWrap = el("div", "compose-picker");
  assigneeWrap.appendChild(el("label", "cred-label", "Assignee (optional)"));
  var assigneeSelect = el("select", "cred-input");
  var assigneeNone = document.createElement("option");
  assigneeNone.value = "";
  assigneeNone.textContent = "Unassigned";
  assigneeSelect.appendChild(assigneeNone);
  assigneeWrap.appendChild(assigneeSelect);
  var assigneeErr = el("div", "cred-status is-bad compose-field-error");
  assigneeErr.hidden = true;
  assigneeWrap.appendChild(assigneeErr);
  form.appendChild(assigneeWrap);

  // -- status ------------------------------------------------------------------
  var statusWrap = el("div", "compose-picker");
  statusWrap.appendChild(el("label", "cred-label", "Status (optional)"));
  var statusSelect = el("select", "cred-input");
  var statusNone = document.createElement("option");
  statusNone.value = "";
  statusNone.textContent = "— leave unset —";
  statusSelect.appendChild(statusNone);
  STATUS_CHOICES.forEach(function (c) {
    var o = document.createElement("option");
    o.value = c.value;
    o.textContent = c.label;
    statusSelect.appendChild(o);
  });
  statusWrap.appendChild(statusSelect);
  var statusErr = el("div", "cred-status is-bad compose-field-error");
  statusErr.hidden = true;
  statusWrap.appendChild(statusErr);
  form.appendChild(statusWrap);

  // -- actions -----------------------------------------------------------------
  var actions = el("div", "cred-form-actions");
  var status = el("span", "cred-status");
  status.setAttribute("aria-live", "polite");
  actions.appendChild(status);
  var cancelBtn = el("button", "cred-btn is-quiet", "Cancel");
  cancelBtn.type = "button";
  actions.appendChild(cancelBtn);
  var submitBtn = el("button", "cred-btn is-primary", "Create workitem");
  submitBtn.type = "submit";
  actions.appendChild(submitBtn);
  form.appendChild(actions);

  cancelBtn.addEventListener("click", closeCompose);   // files NOTHING — no fetch on this path

  // ---------------------------------------------------------- field routing
  var FIELD_CONTROL = {
    type: { input: typeSelect, err: typeErr },
    id: { input: idInput, err: idErr },
    name: { input: nameInput, err: nameErr },
    status: { input: statusSelect, err: statusErr },
    parent: { input: parent.select, err: parent.err },
    binding: { input: binding.select, err: binding.err },
    assignee: { input: assigneeSelect, err: assigneeErr },
  };

  function clearErrors() {
    banner.hidden = true; banner.textContent = "";
    for (var k in FIELD_CONTROL) {
      if (!Object.prototype.hasOwnProperty.call(FIELD_CONTROL, k)) continue;
      var c = FIELD_CONTROL[k];
      c.err.hidden = true; c.err.textContent = "";
      if (c.input.removeAttribute) c.input.removeAttribute("aria-invalid");
    }
  }

  function showFieldError(fieldKey, message) {
    var c = FIELD_CONTROL[fieldKey];
    if (!c) return false;
    c.err.textContent = message;
    c.err.hidden = false;
    if (c.input.setAttribute) c.input.setAttribute("aria-invalid", "true");
    if (typeof c.input.focus === "function") c.input.focus();
    return true;
  }

  function showApiError(err) {
    var body = err && err.body;
    var detail = (body && (body.detail || body.error)) || (err && err.message) || "Could not file that.";
    var field = (body && body.field) || (body && ERROR_FIELD[body.error]) || null;
    // family_mismatch (409) names neither field explicitly — it fires for
    // EITHER the parent edge (the WORK hierarchy) or the binding edge
    // (works_on), from the same _check_family helper, so the edge-type
    // token in its own message (single-quoted, Python `!r`) is the only
    // thing that tells the two apart.
    if (!field && body && body.error === "family_mismatch" && typeof detail === "string") {
      if (detail.indexOf("'works_on'") !== -1) field = "binding";
      else if (detail.indexOf("'contains'") !== -1) field = "parent";
    }
    if (field && showFieldError(field, detail)) return;
    banner.textContent = detail;
    banner.hidden = false;
  }

  // -------------------------------------------------------------- submit
  var submitting = false;
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    if (submitting) return;   // criterion 5: one click, one workitem

    clearErrors();

    var type = typeSelect.value;
    var name = nameInput.value.trim();
    if (!name) { showFieldError("name", "A name is required."); return; }
    var idValue = idInput.value.trim();
    if (type !== "task" && !idValue) {
      showFieldError("id", "Epics and stories need a short id (no minter exists for them yet).");
      return;
    }

    var payload = {
      type: type,
      name: name,
      summary: summaryInput.value.trim() || null,
      body: bodyInput.value.trim() || null,
      acceptance: acceptance.values(),
      binding: binding.select.value || null,
      parent: parent.select.value || null,
      assignee: assigneeSelect.value || null,
      status: statusSelect.value || null,
      csrf_token: CSRF_TOKEN,
    };
    if (type !== "task") payload.id = idValue;

    submitting = true;
    submitBtn.disabled = true;
    cancelBtn.disabled = true;
    status.className = "cred-status";
    status.textContent = "Creating…";

    postWorkitem(payload).then(function (created) {
      submitting = false;
      submitBtn.disabled = false;
      cancelBtn.disabled = false;
      status.textContent = "";
      closeCompose();
      // criterion 6: the caller (board.html) refetches the trees and
      // navigates to the new node — see this file's header for the seam.
      hooks.afterCreate(created.id);
    }, function (err) {
      submitting = false;
      submitBtn.disabled = false;
      cancelBtn.disabled = false;
      status.textContent = "";
      showApiError(err);
    });
  });

  return {
    form: form,
    typeSelect: typeSelect,
    nameInput: nameInput,
    summaryInput: summaryInput,
    bodyInput: bodyInput,
    idInput: idInput,
    acceptance: acceptance,
    binding: binding,
    parent: parent,
    assigneeSelect: assigneeSelect,
    statusSelect: statusSelect,
    clearErrors: clearErrors,
    syncIdVisibility: syncIdVisibility,
  };
}

var formRefs = null;
var currentMe = null;   // the `me` openCompose was last called with — see the assignee default below

function resetForm() {
  formRefs.clearErrors();
  formRefs.typeSelect.value = "task";
  formRefs.syncIdVisibility();
  formRefs.idInput.value = "";
  formRefs.nameInput.value = "";
  formRefs.summaryInput.value = "";
  formRefs.bodyInput.value = "";
  formRefs.acceptance.reset();
  formRefs.binding.select.value = "";
  formRefs.binding.filterInput.value = "";
  formRefs.parent.select.value = "";
  formRefs.parent.filterInput.value = "";
  formRefs.assigneeSelect.value = "";
  formRefs.statusSelect.value = "";
}

function populateFromApi() {
  Promise.all([loadVocabulary(), loadWorldObjects(), loadPeople()]).then(function (res) {
    var vocab = res[0] || {};
    var tree = res[1] || { roots: [] };
    var people = (res[2] && res[2].people) || [];

    // Type options, in the vocabulary's own order (WORKITEM_TYPES on the
    // backend) — no hand-written list of type strings here beyond the
    // display labels (see TYPE_LABELS's own comment for why that is fine).
    clear(formRefs.typeSelect);
    (vocab.workitem_types || ["epic", "story", "task"]).forEach(function (t) {
      var o = document.createElement("option");
      o.value = t;
      o.textContent = TYPE_LABELS[t] || t;
      formRefs.typeSelect.appendChild(o);
    });
    formRefs.typeSelect.value = "task";
    formRefs.syncIdVisibility();

    var families = {};
    var nodeTypes = vocab.node_types || {};
    for (var t2 in nodeTypes) {
      if (Object.prototype.hasOwnProperty.call(nodeTypes, t2)) families[t2] = nodeTypes[t2].family;
    }

    var flat = flattenTree(tree.roots);
    var objectNodes = flat.filter(function (n) { return families[n.type] === "object"; });
    var workitemNodes = flat.filter(function (n) { return families[n.type] === "workitem"; });
    objectNodes.sort(function (a, b) { return String(a.id).localeCompare(String(b.id)); });
    workitemNodes.sort(function (a, b) { return String(a.id).localeCompare(String(b.id)); });

    formRefs.binding.setOptions(objectNodes);
    formRefs.parent.setOptions(workitemNodes);

    clear(formRefs.assigneeSelect);
    var none = document.createElement("option");
    none.value = "";
    none.textContent = "Unassigned";
    formRefs.assigneeSelect.appendChild(none);
    people.forEach(function (p) {
      var o = document.createElement("option");
      o.value = p.id;
      o.textContent = p.name || p.username;
      formRefs.assigneeSelect.appendChild(o);
    });
    // Default to "assigned to me" — the common case (GitHub/Jira's own
    // default) — but only once the roster has actually loaded and only for
    // a real signed-in PERSON (never a bot: this button is only reachable
    // through a browser session in the first place, but `_resolve_assignee`
    // would refuse a bot's id anyway, and a default nobody can submit is
    // worse than no default). Left alone if the caller changes it.
    if (currentMe && currentMe.kind === "session" && currentMe.user_id
        && people.some(function (p) { return p.id === currentMe.user_id; })) {
      formRefs.assigneeSelect.value = currentMe.user_id;
    }
  }, function () {
    formRefs.clearErrors();
    var banner = formRefs.form.querySelector(".compose-banner");
    if (banner) {
      banner.textContent = "Could not load the binding/parent/assignee choices — "
        + "you can still file work with no binding, parent or assignee.";
      banner.hidden = false;
    }
  });
}

export function openCompose(me) {
  currentMe = me || null;
  if (!overlay) {
    overlay = buildOverlay();
    formRefs = buildForm();
    var body = document.getElementById("composeBody");
    body.appendChild(formRefs.form);
  }
  lastFocus = document.activeElement;
  overlay.hidden = false;
  document.body.classList.add("compose-open");
  resetForm();
  populateFromApi();
  formRefs.nameInput.focus();
}
