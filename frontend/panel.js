// panel.js — the detail panel: renderPanel, the tab machinery, the Work-tab
// (renderWorkList / renderConnections), the Docs-tab doc viewer, the Links-tab
// depends_on lookup, and the status control. Moved out of board.html's inline
// script (TASK-1023, the final step of STORY-atlas-board-modularize).
//
// SEAM, same idiom as map.js's (see that file's header for the general
// argument): board.html owns navigateTo() (cross-tree navigation — it decides
// whether a click needs to switch the World/Work tab first) and refreshGraph()
// (refetch both trees and redraw after a status write moves rollups above the
// changed node). Neither is importable — board.html is an inline <script
// type="module"> with no URL — so `hooks` below is a plain object board.html
// fills in once at boot by property assignment, exactly like map.js's hooks.
//
// selectNode, by contrast, IS imported directly from map.js: it never needs
// to cross tabs (sub-node rows and Links-tab rows only ever point at a node in
// the SAME tree as the one currently open), so there is no seam to bridge.
import { state, isObject, STATUS_CHOICES, statusBucket } from "/assets/state.js";
import { remember, recall, escapeHtml, daysSince } from "/assets/util.js";
import { attentionOf, standaloneGlyph, ATT_LABEL } from "/assets/glyph.js";
import { selectNode } from "/assets/map.js";
import { fetchJSON, postJSON, patchJSON, CSRF_TOKEN } from "/assets/api.js";

export var hooks = {
  navigateTo: function () {},
  refreshGraph: function () {}
};

var URG_LABEL = { none: "no urgency", orange: "on critical path", red: "do this next" };

// Mermaid (vendored, TASK-871) — startOnLoad off; diagrams are rendered
// on-demand into the doc viewer via mermaid.render(), not auto-scanned. This
// only matters to the Docs tab below, so it moved here with it.
if (typeof mermaid !== "undefined") {
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
}

var panelBody = document.getElementById("panelBody");

function chip(label, colour, strong) {
  var dot = colour ? '<span class="dot" style="background:var(--att-' + colour + ')"></span>' : "";
  return '<span class="chip' + (strong ? " strong" : "") + '">' + dot + label + '</span>';
}

function renderSubnodeRow(child) {
  var wrap = document.createElement("button");
  wrap.type = "button"; wrap.className = "subnode-row";
  wrap.appendChild(standaloneGlyph(child, 28));
  var txt = document.createElement("span");
  txt.innerHTML = '<div class="rn">' + escapeHtml(child.name) + '</div><div class="ri">' + child.id + '</div>';
  wrap.appendChild(txt);
  wrap.addEventListener("click", function () { selectNode(child.id, true); });
  return wrap;
}

function renderLinkRow(neighborNode, relationLabel) {
  var wrap = document.createElement("button");
  wrap.type = "button"; wrap.className = "link-row";
  wrap.appendChild(standaloneGlyph(neighborNode, 24));
  var txt = document.createElement("span");
  txt.innerHTML = '<div class="rn">' + escapeHtml(neighborNode.name) + '</div><div class="ri">' +
    neighborNode.id + ' · ' + relationLabel + '</div>';
  wrap.appendChild(txt);
  wrap.addEventListener("click", function () { selectNode(neighborNode.id, true); });
  return wrap;
}

// a cross-tree connection row: mini glyph + name + relation; click navigates
// (auto-switching tab when the target lives in the other tree).
function connRow(neighborNode, relationLabel) {
  var wrap = document.createElement("button");
  wrap.type = "button"; wrap.className = "link-row";
  wrap.appendChild(standaloneGlyph(neighborNode, 24));
  var txt = document.createElement("span");
  txt.innerHTML = '<div class="rn">' + escapeHtml(neighborNode.name) + '</div><div class="ri">' +
    neighborNode.id + ' · ' + relationLabel + '</div>';
  wrap.appendChild(txt);
  wrap.addEventListener("click", function () { hooks.navigateTo(neighborNode.id); });
  return wrap;
}

function connGroup(title, arr, relationLabel) {
  var wrap = document.createElement("div");
  var h = document.createElement("div"); h.className = "link-sub"; h.textContent = title; wrap.appendChild(h);
  if (!arr.length) {
    var e = document.createElement("div"); e.className = "muted-empty"; e.textContent = "None."; wrap.appendChild(e);
  } else {
    arr.forEach(function (n) { wrap.appendChild(connRow(n, relationLabel)); });
  }
  return wrap;
}

// "Connections" section — a WORKITEM's outbound works_on object(s) + serves
// goal(s), fetched live because the objects/goals it points at need their
// full node data (name, type) to draw a row, which a workitem doesn't carry
// on itself. Objects don't use this any more — see renderWorkList below,
// which reads node.work_load instead of fetching.
//
// onLoaded(count), if given, is called once the fetch settles (success or
// fail-to-zero) so the Work tab's badge can reflect the real count instead
// of staying blank forever.
export function renderConnections(node, onLoaded) {
  var sec = document.createElement("div"); sec.className = "panel-section";
  sec.innerHTML = '<div class="panel-label">Connections · what this works on</div>';
  var body = document.createElement("div");
  body.innerHTML = '<div class="muted-empty">loading…</div>';
  sec.appendChild(body);

  fetchJSON("/api/graph?focus=" + encodeURIComponent(node.id) + "&edges=works_on,serves,for&depth=1")
    .then(function (g) {
      var nById = {}; (g.nodes || []).forEach(function (n) { nById[n.id] = n; });
      body.innerHTML = "";
      var objs = [], goals = [];
      (g.edges || []).forEach(function (e) {
        if (e.edge_type === "works_on" && e.src === node.id && nById[e.dst]) objs.push(nById[e.dst]);
        if (e.edge_type === "serves" && e.src === node.id && nById[e.dst]) goals.push(nById[e.dst]);
      });
      body.appendChild(connGroup("Works on", objs, "object"));
      body.appendChild(connGroup("Serves", goals, "goal"));
      if (onLoaded) onLoaded(objs.length + goals.length);
    })
    .catch(function () {
      body.innerHTML = '<div class="muted-empty">Could not load connections.</div>';
      if (onLoaded) onLoaded(0);
    });
  return sec;
}

// ONE /api/node/{id} fetch per panel render, shared by every section that
// needs the per-item detail the listings deliberately omit (acceptance, and
// since TASK-1066 the result block).
//
// Both sections want the same payload, and issuing the request twice would
// double the per-click cost for nothing. Keyed by node id and reset by
// renderPanel below, so re-opening a node after a status write refetches
// rather than serving what the panel was showing before the write.
var _fullCache = null;

function fetchFull(nodeId) {
  if (_fullCache && _fullCache.id === nodeId) return _fullCache.promise;
  _fullCache = { id: nodeId, promise: fetchJSON("/api/node/" + encodeURIComponent(nodeId)) };
  return _fullCache.promise;
}

// ------------------------------------------------------- prose edit fields
// `board.css` has READ styling for prose (`.prose-block`, `.acceptance-item`)
// and nothing for an editable field — this module owns panel.js only and may
// not add a rule to a stylesheet it does not own (see this repo's file
// ownership rules), so the look is applied inline. The rule this WOULD be,
// if it belonged here:
//   .prose-edit-field { width: 100%; box-sizing: border-box;
//     font-size: calc(12.5px * var(--ui-scale)); line-height: 1.55;
//     color: var(--ink); background: var(--panel-2); border: 1px solid var(--line);
//     border-radius: 6px; padding: 7px 9px; }
// Font size still obeys the house rule (calc() against --ui-scale) even
// though it never reaches board.css.
function styleProseField(el) {
  // Was an inline style block, written that way only because board.css
  // belonged to another builder at the time. The rule now lives beside
  // .prose-block in board.css, where the read and edit views of the same
  // prose stay in step.
  el.className = el.className ? el.className + " prose-edit-field" : "prose-edit-field";
}

function trim(s) { return String(s == null ? "" : s).replace(/^\s+|\s+$/g, ""); }

// ---------------------------------------------------- description editor ---
// Edits `node.body` (TASK: panel prose editing). The RAIL this closes: the
// acceptance-rail refusal (below) tells a refused machine caller to "propose
// the change to a human, who can make it from the board" — until this
// existed the board had no such surface, so that sentence pointed at
// nothing. `body` carries no such rail (only `acceptance` does, per
// `app.acceptance_rail_refusal`), so a session write here needs no special
// handling beyond the ordinary error path.
//
// READ VIEW stays exactly the abstract/detail projection the server already
// derives from `body` (ADR 0020, `app._project_prose_from_body`) — this
// module must not re-derive that split client-side (no module may encode
// server logic any more than it may encode the graph). `PATCH
// /api/workitem`'s response already carries a freshly re-projected
// abstract/detail (`_status_payload` -> `_graph_node_json`, whose
// `include_prose` defaults `True`), so a successful save rebuilds the read
// view from what the SERVER sent back, never from a client-side guess.
//
// Workitems only: `PATCH /api/workitem` 409s `not_a_workitem` for anything
// else, and a world object's description is authored through a different
// field (`abstract`/`detail` directly, not `body`) — offering an editor here
// for a node type the route refuses would be a control with nothing behind
// it (AC 7).
function buildDescriptionReadBody(node) {
  var frag = document.createDocumentFragment();
  if (node.abstract) {
    var absBody = document.createElement("div"); absBody.className = "prose-block doc-prose";
    renderMarkdown(absBody, node.abstract);
    frag.appendChild(absBody);
    if (node.detail) {
      var moreEl = document.createElement("details"); moreEl.className = "detail-more";
      var sumEl = document.createElement("summary"); sumEl.textContent = "More";
      moreEl.appendChild(sumEl);
      var detBody = document.createElement("div"); detBody.className = "prose-block doc-prose";
      renderMarkdown(detBody, node.detail);
      moreEl.appendChild(detBody);
      frag.appendChild(moreEl);
    }
  } else {
    var noAbs = document.createElement("div"); noAbs.className = "muted-empty";
    noAbs.textContent = "No description authored yet.";
    frag.appendChild(noAbs);
  }
  return frag;
}

export function renderDescriptionSection(node) {
  var sec = document.createElement("div"); sec.className = "panel-section";
  var label = document.createElement("div"); label.className = "panel-label";
  label.textContent = "Description";
  sec.appendChild(label);

  var readWrap = document.createElement("div");
  readWrap.appendChild(buildDescriptionReadBody(node));
  sec.appendChild(readWrap);

  // Read-only for anything PATCH /api/workitem would refuse (AC 7).
  if (!WORKITEM_TYPES[node.type]) return sec;

  var editBtn = document.createElement("button");
  editBtn.type = "button"; editBtn.className = "status-btn"; editBtn.textContent = "Edit";
  editBtn.style.marginTop = "8px";
  sec.appendChild(editBtn);

  var editWrap = document.createElement("div");
  editWrap.hidden = true;
  editWrap.style.marginTop = "8px";
  sec.appendChild(editWrap);

  var ta = document.createElement("textarea");
  ta.rows = 8;
  styleProseField(ta);
  editWrap.appendChild(ta);

  var btnRow = document.createElement("div");
  btnRow.className = "prose-edit-actions"; btnRow.style.marginTop = "6px";
  var saveBtn = document.createElement("button");
  saveBtn.type = "button"; saveBtn.className = "status-btn"; saveBtn.textContent = "Save";
  var cancelBtn = document.createElement("button");
  cancelBtn.type = "button"; cancelBtn.className = "status-btn"; cancelBtn.textContent = "Cancel";
  btnRow.appendChild(saveBtn); btnRow.appendChild(cancelBtn);
  editWrap.appendChild(btnRow);

  var confirmWrap = document.createElement("div");
  confirmWrap.style.marginTop = "6px";
  editWrap.appendChild(confirmWrap);

  var err = document.createElement("div"); err.className = "status-error";
  editWrap.appendChild(err);

  // The last text CONFIRMED to be on the server — what the source is,
  // whether or not the panel is looking at it right now. Populated on first
  // Edit (via the shared fetchFull, which panel-full detail like Acceptance
  // and Result already pay for) and replaced with the server's own answer
  // after every successful save, never with what the textarea merely holds.
  var currentBody = null;

  function clearConfirm() { while (confirmWrap.firstChild) confirmWrap.removeChild(confirmWrap.firstChild); }

  function openEdit() {
    editBtn.hidden = true; readWrap.hidden = true; editWrap.hidden = false;
    err.textContent = ""; clearConfirm();
    if (currentBody !== null) { ta.value = currentBody; ta.disabled = false; ta.focus(); return; }
    ta.value = ""; ta.placeholder = "Loading…"; ta.disabled = true; saveBtn.disabled = true;
    fetchFull(node.id).then(function (full) {
      currentBody = (full && full.body) || "";
      ta.value = currentBody; ta.placeholder = ""; ta.disabled = false; saveBtn.disabled = false;
      ta.focus();
    }).catch(function () {
      err.textContent = "Could not load the current description.";
      ta.disabled = false; saveBtn.disabled = false;
    });
  }

  function closeEdit() {
    editWrap.hidden = true; readWrap.hidden = false; editBtn.hidden = false;
  }

  function doSave(text) {
    saveBtn.disabled = true; cancelBtn.disabled = true; ta.disabled = true; err.textContent = ""; clearConfirm();
    patchJSON("/api/workitem/" + encodeURIComponent(node.id),
              { body: text, csrf_token: CSRF_TOKEN })
      .then(function (updated) {
        saveBtn.disabled = false; cancelBtn.disabled = false; ta.disabled = false;
        // Copy what the SERVER holds now onto the node object the rest of
        // the panel reads from — same idiom as the status/assignee controls
        // (never render the value that was merely typed).
        node.body = updated.body;
        node.abstract = updated.abstract;
        node.detail = updated.detail;
        currentBody = updated.body || "";
        while (readWrap.firstChild) readWrap.removeChild(readWrap.firstChild);
        readWrap.appendChild(buildDescriptionReadBody(node));
        closeEdit();
      })
      .catch(function (e) {
        saveBtn.disabled = false; cancelBtn.disabled = false; ta.disabled = false;
        err.textContent = (e && e.message) ? e.message : "Could not save the description.";
      });
  }

  editBtn.addEventListener("click", openEdit);
  cancelBtn.addEventListener("click", function () {
    // Writes nothing (AC 5) — restore whatever the server last confirmed,
    // discarding anything typed since.
    if (currentBody !== null) ta.value = currentBody;
    closeEdit();
  });
  saveBtn.addEventListener("click", function () {
    var text = ta.value;
    // Emptying the whole spec in one save is exactly the "silent clobbering"
    // this file's task asks not to make easy — an in-place confirm (never
    // `window.confirm()`; see settings.js's credential-revoke row for the
    // same house idiom), not a second click that looks like the first.
    if (!trim(text) && currentBody && trim(currentBody)) {
      clearConfirm();
      var msg = document.createElement("span"); msg.className = "status-error";
      msg.textContent = "This clears the whole description. ";
      confirmWrap.appendChild(msg);
      var yes = document.createElement("button");
      yes.type = "button"; yes.className = "status-btn"; yes.textContent = "Clear it";
      yes.addEventListener("click", function () { doSave(text); });
      var no = document.createElement("button");
      no.type = "button"; no.className = "status-btn"; no.textContent = "Keep editing";
      no.style.marginLeft = "6px";
      no.addEventListener("click", clearConfirm);
      confirmWrap.appendChild(yes); confirmWrap.appendChild(no);
      return;
    }
    doSave(text);
  });

  return sec;
}

// Acceptance criteria for one workitem — "how do we know this is done".
//
// FETCHED PER NODE, not read off the tree, and deliberately so: there are
// 1,493 criteria across the backlog, and putting them in /api/graph/tree
// would make every board load carry data that only one panel at a time can
// ever show. That is the same rule that keeps `body`/`detail` out of
// listings (see app._graph_node_json's include_prose note). Same live-fetch
// idiom as renderConnections above.
//
// EDITABLE (workitems only — same `not_a_workitem` boundary as the
// description editor above). The section STAYS IN THE DOM even with zero
// criteria — it used to remove itself, which left no anchor for a first
// criterion to be added to (AC 4); it now shows a stated empty state
// instead, with the same Edit button that handles every other count.
//
// `acceptance` is a WHOLE-LIST REPLACE, not a merge (`app._replace_acceptance`
// deletes and reinserts every row) — Save always sends the complete edited
// list, and blank rows are dropped before it is sent rather than saved as
// empty criteria. Reducing a non-empty list to nothing goes through the same
// in-place confirm the description editor uses, for the same reason.
export function renderAcceptance(node) {
  var sec = document.createElement("div"); sec.className = "panel-section";
  var label = document.createElement("div"); label.className = "panel-label";
  label.textContent = "Acceptance";
  sec.appendChild(label);
  var readWrap = document.createElement("div");
  readWrap.innerHTML = '<div class="muted-empty">loading…</div>';
  sec.appendChild(readWrap);

  var editable = WORKITEM_TYPES[node.type] === 1;
  // The last list CONFIRMED to be on the server — null until the initial
  // fetch settles, [] (not null) once it has even when there are no rows.
  var currentList = null;

  function paintRead(list) {
    while (readWrap.firstChild) readWrap.removeChild(readWrap.firstChild);
    label.textContent = list.length ? ("Acceptance · " + list.length) : "Acceptance";
    if (!list.length) {
      var e = document.createElement("div"); e.className = "muted-empty";
      e.textContent = "No acceptance criteria yet.";
      readWrap.appendChild(e);
      return;
    }
    var ol = document.createElement("ol");
    ol.className = "acceptance-list";
    list.forEach(function (text) {
      var li = document.createElement("li");
      li.className = "acceptance-item doc-prose";
      // INLINE markdown (TASK-1071): these are single lines dense with
      // backticks — `status_source`, `/api/status/pending`, `--apply` — and
      // rendering them as code is most of the readability win. parseInline,
      // not parse, so marked does not wrap every criterion in its own <p>.
      // Raw HTML is still escaped by ensureMarkedConfigured's renderer; one
      // of these strings is literally a YAML fragment.
      renderMarkdown(li, text, true);
      ol.appendChild(li);
    });
    readWrap.appendChild(ol);
  }

  if (!editable) {
    fetchFull(node.id)
      .then(function (full) { paintRead((full && full.acceptance) || []); })
      .catch(function () {
        readWrap.innerHTML = '<div class="muted-empty">Could not load acceptance criteria.</div>';
      });
    return sec;
  }

  var editBtn = document.createElement("button");
  editBtn.type = "button"; editBtn.className = "status-btn"; editBtn.textContent = "Edit";
  editBtn.style.marginTop = "8px";
  editBtn.disabled = true;   // enabled once the initial fetch settles below
  sec.appendChild(editBtn);

  var editWrap = document.createElement("div");
  editWrap.hidden = true; editWrap.style.marginTop = "8px";
  sec.appendChild(editWrap);

  var rowsWrap = document.createElement("div");
  editWrap.appendChild(rowsWrap);

  var addBtn = document.createElement("button");
  addBtn.type = "button"; addBtn.className = "status-btn"; addBtn.textContent = "+ Add criterion";
  editWrap.appendChild(addBtn);

  var btnRow = document.createElement("div");
  btnRow.className = "prose-edit-actions"; btnRow.style.marginTop = "8px";
  var saveBtn = document.createElement("button");
  saveBtn.type = "button"; saveBtn.className = "status-btn"; saveBtn.textContent = "Save";
  var cancelBtn = document.createElement("button");
  cancelBtn.type = "button"; cancelBtn.className = "status-btn"; cancelBtn.textContent = "Cancel";
  btnRow.appendChild(saveBtn); btnRow.appendChild(cancelBtn);
  editWrap.appendChild(btnRow);

  var confirmWrap = document.createElement("div");
  confirmWrap.style.marginTop = "6px";
  editWrap.appendChild(confirmWrap);

  var err = document.createElement("div"); err.className = "status-error";
  editWrap.appendChild(err);

  function clearConfirm() { while (confirmWrap.firstChild) confirmWrap.removeChild(confirmWrap.firstChild); }

  function addRow(text) {
    var row = document.createElement("div");
    row.style.display = "flex"; row.style.gap = "6px"; row.style.marginBottom = "6px";
    var input = document.createElement("input");
    input.type = "text"; input.value = text || "";
    styleProseField(input);
    row.appendChild(input);
    var rm = document.createElement("button");
    rm.type = "button"; rm.className = "status-btn"; rm.textContent = "Remove";
    rm.addEventListener("click", function () { rowsWrap.removeChild(row); });
    row.appendChild(rm);
    rowsWrap.appendChild(row);
    return input;
  }

  function paintRows(list) {
    while (rowsWrap.firstChild) rowsWrap.removeChild(rowsWrap.firstChild);
    if (!list.length) { addRow("").focus(); return; }   // AC 4 — an anchor to type into
    list.forEach(function (text) { addRow(text); });
  }

  function openEdit() {
    if (currentList === null) return;   // editBtn stays disabled until this is true
    readWrap.hidden = true; editBtn.hidden = true; editWrap.hidden = false;
    err.textContent = ""; clearConfirm();
    paintRows(currentList);
  }
  function closeEdit() {
    editWrap.hidden = true; readWrap.hidden = false; editBtn.hidden = false;
  }

  function doSave(list) {
    saveBtn.disabled = true; cancelBtn.disabled = true; addBtn.disabled = true; err.textContent = ""; clearConfirm();
    patchJSON("/api/workitem/" + encodeURIComponent(node.id),
              { acceptance: list, csrf_token: CSRF_TOKEN })
      .then(function (updated) {
        saveBtn.disabled = false; cancelBtn.disabled = false; addBtn.disabled = false;
        currentList = (updated && updated.acceptance) || [];
        paintRead(currentList);
        closeEdit();
      })
      .catch(function (e) {
        saveBtn.disabled = false; cancelBtn.disabled = false; addBtn.disabled = false;
        // The acceptance rail (app.acceptance_rail_refusal) refuses a MACHINE
        // principal with a 403 naming the field and telling it to "propose
        // the change to a human, who can make it from the board" — a session
        // write from here is exactly that human, so this branch should be
        // unreachable in practice, but the message is shown verbatim rather
        // than swallowed if it is ever wrong.
        err.textContent = (e && e.message) ? e.message : "Could not save acceptance criteria.";
      });
  }

  editBtn.addEventListener("click", openEdit);
  addBtn.addEventListener("click", function () { addRow("").focus(); });
  cancelBtn.addEventListener("click", closeEdit);   // writes nothing (AC 5)
  saveBtn.addEventListener("click", function () {
    var list = [];
    Array.prototype.forEach.call(rowsWrap.children, function (row) {
      var input = row.querySelector("input");
      var v = input ? trim(input.value) : "";
      if (v) list.push(v);
    });
    if (!list.length && currentList && currentList.length) {
      clearConfirm();
      var msg = document.createElement("span"); msg.className = "status-error";
      msg.textContent = "This removes all " + currentList.length +
        (currentList.length === 1 ? " criterion. " : " criteria. ");
      confirmWrap.appendChild(msg);
      var yes = document.createElement("button");
      yes.type = "button"; yes.className = "status-btn"; yes.textContent = "Remove all";
      yes.addEventListener("click", function () { doSave([]); });
      var no = document.createElement("button");
      no.type = "button"; no.className = "status-btn"; no.textContent = "Keep editing";
      no.style.marginLeft = "6px";
      no.addEventListener("click", clearConfirm);
      confirmWrap.appendChild(yes); confirmWrap.appendChild(no);
      return;
    }
    doSave(list);
  });

  fetchFull(node.id)
    .then(function (full) {
      currentList = (full && full.acceptance) || [];
      paintRead(currentList);
      editBtn.disabled = false;
    })
    .catch(function () {
      readWrap.innerHTML = '<div class="muted-empty">Could not load acceptance criteria.</div>';
      err.textContent = "Could not load acceptance criteria to edit.";
    });

  return sec;
}

// What actually happened — the `## Result` block, its own row since TASK-1063
// (`workitem_result`), rendered here since TASK-1066.
//
// WITHOUT THIS SECTION THE SPLIT IS A VISIBLE REGRESSION on 221 of 306 items.
// The panel's Description/More pair is PROJECTED from `node.body` server-side
// (`app._project_prose_tiers`), so while the result section lived inside the
// body it reached the reader inside "More". Moving it to its own table takes
// it off the screen entirely unless something puts it back.
//
// The heading is read from the payload rather than hardcoded to "Result": 3
// workitems in the corpus write `## Outcome`, and `result_heading` carries
// whichever the document actually used. Same reason the raw line is what gets
// stored — see `models_graph.WorkitemResult.heading`.
//
// Prose is RENDERED here, not printed. This comment used to say the opposite —
// that `**bold**` shows literal asterisks and the markdown renderer was
// deliberately not wired to Description, Acceptance or Result — and it was
// already false when TASK-1071 wired all three; the call is a few lines below.
// It stayed wrong for two days and was repeated in CLAUDE.md, which is the
// argument for stating what the code does rather than what a decision was.
export function renderResult(node) {
  var sec = document.createElement("div"); sec.className = "panel-section";
  var label = document.createElement("div"); label.className = "panel-label";
  label.textContent = "Result";
  sec.appendChild(label);
  var body = document.createElement("div");
  body.innerHTML = '<div class="muted-empty">loading…</div>';
  sec.appendChild(body);

  fetchFull(node.id)
    .then(function (full) {
      var text = full && full.result;
      // Same rule as renderAcceptance: remove the whole section rather than
      // leave a header over nothing. 85 of 306 workitems have no result yet,
      // and an empty "Result" label reads as a failed load.
      if (!text || !String(text).trim()) {
        if (sec.parentNode) sec.parentNode.removeChild(sec);
        return;
      }
      if (full.result_heading) label.textContent = full.result_heading;
      body.innerHTML = "";
      var block = document.createElement("div");
      block.className = "prose-block doc-prose";
      // Markdown, not textContent (TASK-1071): several results ARE whole
      // tables, and printing their pipes was the clearest single example of
      // the board showing good data badly. renderMarkdown escapes raw HTML.
      renderMarkdown(block, String(text).replace(/^\n+/, ""));
      body.appendChild(block);
    })
    .catch(function () {
      body.innerHTML = '<div class="muted-empty">Could not load the result.</div>';
    });
  return sec;
}

// Work items directly linked to a WORLD OBJECT (ADR 0019 Addendum,
// TASK-860 `work_load`, attention.py:554) — rendered synchronously, no
// fetch, because /api/graph and /api/graph/tree already attach
// node.work_load to every node in the payload.
//
// SEAM — subtree rollup: work_load is DIRECT works_on edges only; its
// docstring says explicitly that it is not a rollup over the `contains`
// subtree. This list will only ever show work bound straight to THIS
// object, never work bound to something it contains. When the backend
// grows a subtree aggregate, render it as a second block below the direct
// list. Do NOT compute it here by walking node.children — that re-derives
// backend logic on the client and the two will drift.
export function renderWorkList(node) {
  var sec = document.createElement("div"); sec.className = "panel-section";
  sec.innerHTML = '<div class="panel-label">Work items on this object</div>';
  var wl = node.work_load || { count: 0, done: 0, completion: 0, workitems: [] };

  var summary = document.createElement("div"); summary.className = "work-summary";
  summary.textContent = wl.count
    ? (wl.count + (wl.count === 1 ? " workitem" : " workitems") + " · " + wl.done + " done (" +
       Math.round((wl.completion || 0) * 100) + "%)")
    : "No work items are linked to this object.";
  sec.appendChild(summary);

  var note = document.createElement("div"); note.className = "work-seam-note";
  note.textContent = "Direct links only — a rollup over this object's sub-tree isn't computed yet.";
  sec.appendChild(note);

  var items = (wl.workitems || []);
  if (items.length) {
    var list = document.createElement("div"); list.className = "link-list";
    items.forEach(function (w) { list.appendChild(renderWorkItemRow(w)); });
    sec.appendChild(list);
  }
  return sec;
}

// A row for one entry of node.work_load.workitems ({id, status, done,
// completed_at} — attention.work_load, not a full node record), so the
// title comes from state.NODE_INDEX (built from the already-loaded work tree)
// rather than a second fetch. Falls back to the bare id if the workitem
// isn't in the currently loaded tree for some reason (e.g. hidden by a
// future filter) — a row that can still be clicked beats one that vanishes.
function renderWorkItemRow(w) {
  var idx = state.NODE_INDEX[w.id];
  var wrap = document.createElement("button");
  wrap.type = "button"; wrap.className = "link-row";
  var dot = document.createElement("span");
  dot.className = "work-item-dot";
  dot.setAttribute("data-status", statusBucket(w.status) || "");
  wrap.appendChild(dot);
  var txt = document.createElement("span");
  txt.innerHTML = '<div class="rn">' + escapeHtml(idx ? idx.name : w.id) + '</div><div class="ri">' +
    w.id + ' · ' + escapeHtml(statusLabel(w.status)) + '</div>';
  wrap.appendChild(txt);
  if (w.completed_at) wrap.title = "Finished " + w.completed_at.slice(0, 10);
  wrap.addEventListener("click", function () { hooks.navigateTo(w.id); });
  return wrap;
}

// ------------------------------------------------------------ status control
// The board's ONLY write surface. Setting a status here writes the node's
// status in Postgres, which is the only place a status lives.
//
// WHAT THIS COMMENT USED TO SAY, AND WHY IT NO LONGER APPLIES. It described a
// write as an OVERRIDE against backlog markdown, disagreeing with git until
// someone ran `scripts/apply-board-status.py` and committed. None of that
// exists: `status_override` was dropped, the markdown backlog was deleted on
// 2026-08-12 (TASK-1059), and there is no `scripts/` directory in this repo.
// There is nothing left for a board write to disagree WITH.
//
// The last user-visible remnant, "From the backlog file · <node.source>" in
// `paint()` below, was removed on 2026-09-15 together with the `backlog/…`
// paths it printed (backend/migrate_strip_frontmatter_raw.py): there is no
// file for a status to come from, so the note now carries only the finish date.
// `cancelled` (TASK-1049, 2026-08-10) is a second terminal state, appended
// after `done` rather than woven into the pending -> … -> done sequence —
// see attention.SETTABLE_STATUSES, which STATUS_CHOICES must stay equal to.
//
// STATUS_CHOICES and statusBucket() moved to state.js:
// the board's status FILTER (board.html) needs the identical vocabulary this
// control offers, and board.html can't export for panel.js to import — same
// seam BRIDGE_TYPES/OBJECT_TYPES already cross. statusBucket()'s own doc
// (the `planned` == `pending` equivalence, used for both which button lights
// AND whether a click is a change) lives there now too.
var WORKITEM_TYPES = { epic: 1, story: 1, task: 1 };

function statusLabel(value) {
  for (var i = 0; i < STATUS_CHOICES.length; i++) {
    if (STATUS_CHOICES[i].value === value) return STATUS_CHOICES[i].label;
  }
  // A status authored in the markdown that the board does not offer (e.g.
  // `wip`, `review`, `proposed`) is shown as written, never silently
  // renamed to the nearest button. `cancelled` used to be an example here
  // too — as of TASK-1049 it is one of STATUS_CHOICES, so it now hits the
  // loop above like any other settable value.
  return value ? String(value).replace(/_/g, " ") : "none";
}

function renderStatusSection(node) {
  var sec = document.createElement("div");
  sec.className = "panel-section";
  sec.innerHTML = '<div class="panel-label">Status</div>';

  var row = document.createElement("div");
  row.className = "status-set";
  sec.appendChild(row);
  var note = document.createElement("div");
  note.className = "status-note";
  sec.appendChild(note);
  var err = document.createElement("div");
  err.className = "status-error";
  sec.appendChild(err);

  var buttons = [];

  function paint() {
    var current = statusBucket(node.status);
    buttons.forEach(function (b) {
      b.setAttribute("aria-pressed", b.dataset.value === current ? "true" : "false");
      b.disabled = false;
    });
    err.textContent = "";
    while (note.firstChild) note.removeChild(note.firstChild);
    if (node.completed_at) {
      var age = daysSince(node.completed_at);
      var when = document.createElement("div");
      when.textContent = "Finished " + node.completed_at.slice(0, 10) +
        (age === null ? "" : " · " + Math.round(age) + (Math.round(age) === 1 ? " day ago" : " days ago"));
      note.appendChild(when);
    }
  }

  function write(value) {
    buttons.forEach(function (b) { b.disabled = true; });
    err.textContent = "";
    postJSON("/api/node/" + encodeURIComponent(node.id) + "/status",
             { status: value, csrf_token: CSRF_TOKEN })
      .then(function (updated) {
        // Copy the server's answer onto the node object the tree is holding —
        // it is the same object the glyph and the readouts are drawn from, so
        // the rebuild below picks the change up without a page reload.
        for (var k in updated) {
          if (Object.prototype.hasOwnProperty.call(updated, k) && k !== "children") node[k] = updated[k];
        }
        paint();
        // Rollups, the blocking frontier and the age filter all move when a
        // status does, and those live on OTHER nodes. Refetch rather than
        // patching one node and leaving its ancestors quietly stale.
        hooks.refreshGraph(node.id);
      })
      .catch(function (e) {
        buttons.forEach(function (b) { b.disabled = false; });
        err.textContent = e && e.message ? e.message : "Could not save that status.";
      });
  }

  STATUS_CHOICES.forEach(function (choice) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "status-btn";
    b.dataset.value = choice.value;
    b.textContent = choice.label;
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", function () {
      if (choice.value === statusBucket(node.status)) {
        // Already in this state — nothing to write.
        return;
      }
      write(choice.value);
    });
    buttons.push(b);
    row.appendChild(b);
  });

  paint();
  return sec;
}


// ---------------------------------------------------------- assignee control
// The second write surface on the panel (TASK-1122), and deliberately built
// on the same bones as the status control above: PATCH, then render what the
// SERVER answered. Not optimistic — a control that shows the value you picked
// before the write lands lets somebody walk away believing they handed work
// over when they did not.
//
// THE ROSTER IS FETCHED ON OPEN, NOT ON PANEL RENDER. It is four rows and
// stable, and fetching it per panel-open would put a request behind every node
// click for a control most clicks never touch. `?for=` is passed so the
// options are narrowed to accounts that can actually SEE this item — offering
// a handoff that silently goes nowhere is worse than offering nothing.
//
// NO ACCOUNT NAME OR ID APPEARS IN THIS FILE. Same rule as the graph: if a
// module has to name a person to work, that is a missing endpoint.
var _peopleCache = null;   // { forId: string, promise: Promise }

function peopleFor(nodeId) {
  if (_peopleCache && _peopleCache.forId === nodeId) return _peopleCache.promise;
  _peopleCache = {
    forId: nodeId,
    promise: fetchJSON("/api/people?for=" + encodeURIComponent(nodeId))
      .then(function (d) { return (d && d.people) || []; })
  };
  return _peopleCache.promise;
}

function renderAssigneeSection(node) {
  var sec = document.createElement("div");
  sec.className = "panel-section";
  sec.innerHTML = '<div class="panel-label">Assignee</div>';

  var row = document.createElement("div");
  row.className = "assignee-set";
  sec.appendChild(row);
  var err = document.createElement("div");
  err.className = "status-error";   // same error idiom as the status control
  sec.appendChild(err);

  var select = document.createElement("select");
  select.className = "assignee-select";
  select.setAttribute("aria-label", "Assignee");
  row.appendChild(select);

  // Four states, and they must be distinguishable: unassigned reads as a
  // word, not an empty box that might still be loading.
  // `rostered` says whether `people` is a real answer from the server or the
  // placeholder empty list used before the fetch. It gates the "no longer
  // active" suffix, and it has to: without it the FIRST paint compares the
  // current assignee against an empty roster, concludes they are not in it,
  // and labels a perfectly active person as gone. Caught in a browser, not by
  // reading the code — the logic is right and the initial state was not.
  function optionsFor(people, rostered) {
    while (select.firstChild) select.removeChild(select.firstChild);
    var none = document.createElement("option");
    none.value = "";
    none.textContent = "Unassigned";
    select.appendChild(none);
    people.forEach(function (p) {
      var o = document.createElement("option");
      o.value = p.id;
      o.textContent = p.name || p.username;
      select.appendChild(o);
    });
    // The current assignee may be missing from the roster once it HAS loaded —
    // disabled since, or no longer able to see this item. Render them anyway
    // rather than showing the row as unassigned, which would misreport who is
    // carrying it; but say why the name is there.
    var cur = node.assignee;
    if (cur && !people.some(function (p) { return p.id === cur.id; })) {
      var o = document.createElement("option");
      o.value = cur.id;
      o.textContent = (cur.name || cur.username) + (rostered ? " (no longer active)" : "");
      select.appendChild(o);
    }
    select.value = cur ? cur.id : "";
  }

  // Before the roster arrives there is exactly one honest thing to show: who
  // it is assigned to right now, and that the list is still loading.
  optionsFor([], false);
  var loading = document.createElement("option");
  loading.value = "__loading";
  loading.textContent = "Loading people…";
  loading.disabled = true;
  select.appendChild(loading);

  var loaded = false;
  function loadPeople() {
    if (loaded) return;
    loaded = true;
    var forId = node.id;
    peopleFor(forId).then(function (people) {
      // THE STALE-RESPONSE GUARD. Click one item, click another before this
      // settles, and without the id check the second node's panel gets the
      // first node's roster — narrowed by the wrong item's visibility.
      if (node.id !== forId || !select.isConnected) return;
      optionsFor(people, true);
    }).catch(function () {
      loaded = false;   // let the next open retry
      err.textContent = "Could not load the list of people.";
    });
  }
  select.addEventListener("mousedown", loadPeople);
  select.addEventListener("focus", loadPeople);

  select.addEventListener("change", function () {
    var chosen = select.value || null;
    var previous = node.assignee ? node.assignee.id : null;
    if (chosen === previous) return;
    select.disabled = true;
    err.textContent = "";
    var forId = node.id;
    patchJSON("/api/workitem/" + encodeURIComponent(node.id),
              { assignee: chosen, csrf_token: CSRF_TOKEN })
      .then(function (updated) {
        select.disabled = false;
        if (node.id !== forId) return;
        // Render what the SERVER said, not what was picked.
        node.assignee = updated.assignee || null;
        select.value = node.assignee ? node.assignee.id : "";
      })
      .catch(function (e) {
        select.disabled = false;
        // Revert to what the server still holds. A control left showing the
        // failed choice is the defect this whole branch exists to avoid.
        select.value = previous || "";
        err.textContent = e && e.message ? e.message : "Could not save that assignee.";
      });
  });

  return sec;
}

// ------------------------------------------------------------ binding control
// The third write surface on the panel (TASK-1019, first slice), built on the
// same bones as the assignee control above: read the current value from the
// SERVER, write with PATCH, then render what the server answered — never the
// value that was picked. A control that shows your choice before the write
// lands lets somebody walk away believing a task was re-bound when it was not.
//
// WHAT A BINDING IS. `works_on`, workitem -> world object: the part of the
// world this item's work is about. Work rolls UP that edge (ADR 0028), so
// changing it moves work-load counts on the old world node AND the new one —
// which is why a successful write refreshes the graph, exactly as the status
// control does for the same reason.
//
// THE CANDIDATE LIST IS A QUERY, and the family filter is compose.js's, for
// compose.js's reason (read its "THE BINDING PICKER" header before changing
// this one): `works_on` requires a (workitem -> object) FAMILY edge, and
// state.js's OBJECT_TYPES deliberately bundles object+bridge together for the
// WORLD TREE's own reason — filtering by it here would offer a skill/doc/
// vision node and earn a 409 `family_mismatch` this control has no reason to
// expect. GET /api/vocabulary carries each node type's OWN family, the one
// authoritative place that split lives. NO NODE ID IS WRITTEN IN THIS FILE.
//
// RE-BINDING IS TWO WRITES, and that is the API's shape rather than a
// workaround: `PATCH /api/workitem` refuses a binding onto an item that
// already has one (409 `binding_exists` — at most one, ADR 0028 §1, pinned by
// verify_workitem_write_api.py), so a CHANGE is `binding: null` and then
// `binding: <new>`. The gap between those two writes is the only reason the
// restore below exists: if the second fails, the item is left unbound, which
// is a change nobody asked for — so the old binding goes back, and if even
// that fails the control says so instead of showing a tidy lie.

// One fetch per page life, shared by every panel that opens — unlike the
// assignee roster, which is per item (`?for=` narrows it to accounts that can
// see THAT workitem) and therefore only worth fetching when the control is
// actually used. This list is the same for every workitem, so it is loaded
// when the section renders and reused from then on.
var _bindablesPromise = null;

function bindableObjects() {
  if (_bindablesPromise) return _bindablesPromise;
  _bindablesPromise = Promise.all([
    fetchJSON("/api/vocabulary"),
    // The whole tree — the same endpoint board.html's own boot and
    // refreshGraph read, so there is no second listing route to keep in step
    // with it. Filtered below by the FAMILY /api/vocabulary just reported.
    fetchJSON("/api/graph/tree")
  ]).then(function (res) {
    var nodeTypes = (res[0] && res[0].node_types) || {};
    var out = [];
    (function walk(list) {
      (list || []).forEach(function (n) {
        if (!n || !n.id) return;
        var t = nodeTypes[n.type];
        if (t && t.family === "object") out.push({ id: n.id, name: n.name });
        walk(n.children);
      });
    })((res[1] && res[1].roots) || []);
    out.sort(function (a, b) { return String(a.id).localeCompare(String(b.id)); });
    return out;
  }, function (e) {
    _bindablesPromise = null;   // a failed load must not poison every later open
    throw e;
  });
  return _bindablesPromise;
}

var BINDING_NOTE = "Only object-family world nodes are legal targets — a skill, "
  + "doc or vision attachment cannot be bound to.";

function renderBindingSection(node) {
  var sec = document.createElement("div");
  sec.className = "panel-section";
  sec.innerHTML = '<div class="panel-label">Binding · what this works on</div>';

  var row = document.createElement("div");
  row.className = "binding-set";
  sec.appendChild(row);
  var note = document.createElement("div");
  note.className = "status-note";     // same quiet idiom as the status control
  sec.appendChild(note);
  var err = document.createElement("div");
  err.className = "status-error";     // and the same error idiom
  sec.appendChild(err);

  var select = document.createElement("select");
  select.className = "binding-select";
  select.setAttribute("aria-label", "Binding");
  row.appendChild(select);

  var objects = [];        // the candidate roster; empty until it loads
  var rostered = false;    // is `objects` a real answer, or the pre-fetch placeholder
  var known = false;       // has the server told us the CURRENT binding yet
  var currentId = null;
  var currentName = null;
  var edgeCount = 0;       // how many works_on edges this item actually has

  // Four states and they must be distinguishable: "not bound" reads as a
  // phrase, never as an empty box that might still be loading. `rostered`
  // gates the "not in the list" suffix for the same reason the assignee
  // control's own `rostered` gates its suffix: before the roster arrives
  // EVERY current value is missing from an empty list, and labelling a
  // perfectly ordinary binding as unavailable would be a lie told by the
  // first paint.
  function paint() {
    while (select.firstChild) select.removeChild(select.firstChild);
    if (!known) {
      var loading = document.createElement("option");
      loading.value = "";
      loading.textContent = "Loading…";
      select.appendChild(loading);
      select.disabled = true;
      return;
    }
    select.disabled = false;
    var none = document.createElement("option");
    none.value = "";
    none.textContent = "Not bound";
    select.appendChild(none);
    objects.forEach(function (o) {
      var opt = document.createElement("option");
      opt.value = o.id;
      // "name (id)" — the id is what the API round-trips on, the name is what
      // a human recognises, and neither alone picks correctly among world
      // nodes sharing a prefix. Same idiom as compose.js's picker.
      opt.textContent = (o.name || o.id) + "  (" + o.id + ")";
      select.appendChild(opt);
    });
    // The current binding can be absent from the roster: not object-family (a
    // `serves`-era row), or not visible to this account. Render it anyway —
    // showing "Not bound" for an item that IS bound misreports the graph —
    // but say that it is not one of the choices.
    if (currentId && !objects.some(function (o) { return o.id === currentId; })) {
      var cur = document.createElement("option");
      cur.value = currentId;
      cur.textContent = (currentName || currentId) + "  (" + currentId + ")"
        + (rostered ? " — not in the list" : "");
      select.appendChild(cur);
    }
    select.value = currentId || "";
    note.textContent = edgeCount > 1
      ? BINDING_NOTE + " This item carries " + edgeCount + " works_on edges; at most one is "
        + "allowed (ADR 0028 §1), and choosing here replaces all of them."
      : BINDING_NOTE;
  }

  paint();

  // The CURRENT binding rides the panel's shared /api/node/{id} fetch — the
  // tree listings do not carry it, and this section must not issue a second
  // request for a payload two other sections are already waiting on.
  var forId = node.id;
  fetchFull(forId).then(function (full) {
    // THE STALE-RESPONSE GUARD, same as the assignee control's: click one
    // item, click another before this settles, and without the id check the
    // second node's panel paints the first node's binding.
    if (node.id !== forId || !select.isConnected) return;
    var wo = ((full && full.bindings) || []).filter(function (b) {
      return b.edge_type === "works_on";
    });
    known = true;
    edgeCount = wo.length;
    // `_edge_targets` sorts by (edge_type, id), so this is the lowest TARGET
    // id while the API's own `_existing_binding` picks the lowest EDGE id.
    // The two can only disagree for an item with more than one works_on edge
    // — precisely the case the note above calls out on screen.
    currentId = wo.length ? wo[0].id : null;
    currentName = wo.length ? wo[0].name : null;
    paint();
  }, function () {
    if (!select.isConnected) return;
    err.textContent = "Could not read this item's binding.";
  });

  var rosterLoading = false;
  function loadRoster() {
    if (rostered || rosterLoading) return;
    rosterLoading = true;
    bindableObjects().then(function (list) {
      rosterLoading = false;
      if (!select.isConnected) return;
      objects = list;
      rostered = true;
      paint();
    }, function () {
      rosterLoading = false;   // let the next open retry
      if (!select.isConnected) return;
      err.textContent = "Could not load the list of world nodes.";
    });
  }
  loadRoster();
  // Retry hooks for the failure path only — the call above has already asked,
  // and bindableObjects() serves the cached list to everything after it.
  select.addEventListener("mousedown", loadRoster);
  select.addEventListener("focus", loadRoster);

  function patchBinding(value) {
    return patchJSON("/api/workitem/" + encodeURIComponent(node.id),
                     { binding: value, csrf_token: CSRF_TOKEN });
  }

  // Render the SERVER's answer. `_workitem_payload` reports `binding` as the
  // one id the API considers current, so this needs no second read.
  function settle(updated) {
    select.disabled = false;
    if (!select.isConnected) return;
    currentId = (updated && updated.binding) || null;
    currentName = null;          // the response carries the id; the roster has the name
    edgeCount = currentId ? 1 : 0;
    paint();
  }

  function message(e, fallback) {
    // The server names the offending field in `detail` (unknown_binding,
    // family_mismatch, binding_exists, an invalid attributes payload) and
    // api.js's sendJSON preserves that string verbatim. Showing it beats any
    // sentence written here, so the fallback only covers a network failure
    // with no body at all.
    return (e && e.message) ? e.message : fallback;
  }

  select.addEventListener("change", function () {
    var chosen = select.value || null;
    var previous = currentId;
    if (chosen === previous) return;
    select.disabled = true;
    err.textContent = "";

    if (chosen && previous) {
      // The unbind-then-bind path — see this section's header. `binding: null`
      // removes EVERY works_on edge, which is also how the one pre-existing
      // double binding gets unwound, so the set that follows cannot hit
      // `binding_exists`.
      patchBinding(null).then(function () {
        patchBinding(chosen).then(function (updated) {
          settle(updated);
          hooks.refreshGraph(node.id);
        }, function (e) {
          var msg = message(e, "Could not bind that.");
          patchBinding(previous).then(function (restored) {
            settle(restored);
            // No refreshGraph on this path ON PURPOSE: board.html's refresh
            // re-renders the panel for the selected node, which would take
            // this message down with it — and nothing changed on balance.
            err.textContent = msg + " The previous binding was put back.";
          }, function () {
            select.disabled = false;
            currentId = null; currentName = null; edgeCount = 0;
            paint();
            err.textContent = msg + " The previous binding could NOT be put back — "
              + "this item is now unbound.";
          });
        });
      }, function (e) {
        select.disabled = false;
        paint();          // back to what the server still holds — never the rejected pick
        err.textContent = message(e, "Could not change that binding.");
      });
      return;
    }

    patchBinding(chosen).then(function (updated) {
      settle(updated);
      hooks.refreshGraph(node.id);
    }, function (e) {
      select.disabled = false;
      paint();            // back to what the server still holds
      err.textContent = message(e, "Could not save that binding.");
    });
  });

  return sec;
}

// ---------------------------------------------------------------- doc viewer (TASK-871)
// Node docs[] entries rooted at "docs/nodes/" are clickable: fetch the
// allowlisted markdown from /api/node/<id>/doc, render it with marked, and
// draw any ```mermaid fenced blocks as inline SVG with mermaid. Cross-repo/
// external doc paths (e.g. an ADR in AgentPlanningSetup) are never fetched —
// they're tagged "external" and stay non-clickable text.
var DOC_INTERNAL_PREFIX = "docs/nodes/";
var markedConfigured = false;
var mermaidSeq = 0;

function isInternalDoc(d) {
  return !!(d && typeof d.path === "string" && d.path.indexOf(DOC_INTERNAL_PREFIX) === 0);
}

// Render authored prose as markdown into `el` (TASK-1071).
//
// WHY THIS EXISTS. `node.body` is verbatim markdown and always has been — the
// arrival check compares all 306 workitems against their files byte-for-byte.
// The panel used to print it with textContent, so a spec table came out as
// literal `| thing | where |` pipes with its newlines collapsed. Nothing was
// wrong with the data; the board was just the worst view of it. That mattered
// little while the markdown files were still on disk and readable, and matters
// a great deal once TASK-1059 makes this the ONLY way to read the backlog.
//
// Same security posture as the doc viewer below, deliberately reusing its
// configuration rather than a second one: `ensureMarkedConfigured` overrides
// marked's `html` renderer to ESCAPE raw HTML instead of emitting it. Prose
// reaches here from Postgres via the write API, which is authenticated but not
// a reason to execute whatever it contains.
//
// `inline` uses marked.parseInline for content that lives inside an existing
// element (an acceptance <li>), so backticks become <code> without marked
// wrapping each item in its own <p>.
function renderMarkdown(el, text, inline) {
  var src = text == null ? "" : String(text);
  if (typeof marked === "undefined") {
    // Fallback, not decoration: if the vendored parser is missing, show the
    // text rather than throwing or leaving the section blank.
    el.textContent = src;
    return el;
  }
  ensureMarkedConfigured();
  el.innerHTML = inline ? marked.parseInline(src) : marked.parse(src);
  return el;
}

// Trusted-repo content, but still don't let raw HTML in a doc pass through
// unescaped — override marked's `html` renderer to escape instead of emit.
function ensureMarkedConfigured() {
  if (markedConfigured || typeof marked === "undefined") return;
  markedConfigured = true;
  marked.use({
    renderer: {
      html: function (token) {
        var raw = typeof token === "string" ? token : (token && token.text) || "";
        return escapeHtml(raw);
      }
    }
  });
}

function renderDocViewer(container, node, doc) {
  container.innerHTML = '<div class="muted-empty"><span class="doc-spinner" aria-hidden="true"></span>Loading…</div>';
  var url = "/api/node/" + encodeURIComponent(node.id) + "/doc?path=" + encodeURIComponent(doc.path);
  fetchJSON(url).then(function (d) {
    // A declared-external entry answers 200 with `external: true` and NO
    // content (ADR 0029) — deliberately distinguishable from the 404 an
    // unlisted path gets. Say so, rather than falling through to
    // `marked.parse(undefined || "")` and rendering a convincing blank
    // document. Unreachable today because isInternalDoc keeps these
    // non-clickable, which is exactly why it is worth handling: this is one
    // deleted filter away from a silent empty page.
    if (d && d.external) {
      container.innerHTML = "";
      var ext = document.createElement("div");
      ext.className = "muted-empty";
      ext.textContent = "External reference — kept in git, not served here: " + (d.path || "");
      container.appendChild(ext);
      return;
    }
    ensureMarkedConfigured();
    var html = (typeof marked !== "undefined")
      ? marked.parse(d.content || "")
      : "<pre>" + escapeHtml(d.content || "") + "</pre>";
    var prose = document.createElement("div");
    prose.className = "doc-prose";
    prose.innerHTML = html;
    container.innerHTML = "";
    container.appendChild(prose);

    // marked emits fenced ```mermaid blocks as <pre><code class="language-mermaid">
    var blocks = prose.querySelectorAll("code.language-mermaid");
    if (blocks.length && typeof mermaid !== "undefined") {
      blocks.forEach(function (codeEl) {
        var src = codeEl.textContent;
        var pre = codeEl.parentElement;
        var holder = document.createElement("div");
        holder.className = "doc-mermaid";
        var id = "docmermaid-" + (++mermaidSeq);
        mermaid.render(id, src).then(function (r) {
          holder.innerHTML = r.svg;
        }).catch(function (err) {
          holder.innerHTML = '<div class="doc-viewer-error">Diagram render failed: ' +
            escapeHtml(String((err && err.message) || err)) + "</div>";
        });
        pre.replaceWith(holder);
      });
    }
  }).catch(function (err) {
    container.innerHTML = '<div class="doc-viewer-error">Could not load doc: ' +
      escapeHtml(String((err && err.message) || err)) + "</div>";
  });
}

function renderDocRow(node, d) {
  var row = document.createElement("div"); row.className = "doc-row";
  // Tagged so the Overview tab's Documentation pointer can find this exact row
  // and open it, rather than guessing by index -- external docs render a
  // different head element, so index arithmetic would drift.
  row.setAttribute("data-doc-path", d.path);
  var internal = isInternalDoc(d);
  var head = document.createElement(internal ? "button" : "div");
  if (internal) { head.type = "button"; }
  head.className = "doc-row-head" + (internal ? " clickable" : "");
  head.innerHTML =
    '<div class="dn"><span class="doc-kind">' + escapeHtml(d.kind) + '</span>' + escapeHtml(d.title) +
    (internal ? "" : '<span class="doc-ext-tag">external</span>') + '</div>' +
    '<div class="dp">' + escapeHtml(d.path) + '</div>';
  row.appendChild(head);

  if (internal) {
    var viewer = document.createElement("div"); viewer.className = "doc-viewer"; viewer.hidden = true;
    row.appendChild(viewer);
    var loaded = false;
    head.addEventListener("click", function () {
      var opening = viewer.hidden;
      viewer.hidden = !viewer.hidden;
      if (opening && !loaded) { loaded = true; renderDocViewer(viewer, node, d); }
    });
  }
  return row;
}

// ---------------------------------------------------------------- Intent ---
// A node's durable PURPOSE (ADR 0032). Two groups, and the split is the whole
// point: "On this node" is what this node OWNS and is the only part editable
// here; "Inherited" arrives from an ancestor along `contains` and is shown
// read-only, with a row that navigates to the node that owns it. Editing an
// inherited statement means editing that node — which changes it for every
// descendant — so the panel makes you go there rather than offering a field
// that silently rewrites 28 nodes' worth of meaning from a local-looking box.
//
// WORK ROLLS UP; INTENT ROLLS DOWN. The inheritance itself is computed
// SERVER-side (`app._intents_for`) and arrives on `/api/node/{id}` already
// split. This module must not walk ancestors to derive it: no module may
// encode the graph, and an ancestor walk is exactly that.
//
// Same live-fetch idiom (and the same shared `fetchFull` request) as
// renderAcceptance above — intent is per-node detail, and putting it in
// `/api/graph/tree` would make every board load carry text only one panel at
// a time can show.
// Set by renderPanel each time it builds the tab machinery. The strip needs
// to switch tabs, and `showPanelTab` is a closure inside renderPanel — this is
// a module-local handle, NOT part of the `hooks` seam, which exists only for
// the two functions board.html owns and this module cannot import.
var _showPanelTab = function () {};

function intentOwnerRow(item) {
  var b = document.createElement("button");
  b.type = "button"; b.className = "intent-owner";
  b.textContent = "from " + (item.owner_name || item.node_id);
  b.title = "Open " + item.node_id + " — the node that owns this intent";
  b.addEventListener("click", function () { hooks.navigateTo(item.node_id); });
  return b;
}

function intentRow(item, inherited) {
  var wrap = document.createElement("div");
  wrap.className = "intent-row" + (inherited ? " is-inherited" : "");

  var head = document.createElement("div"); head.className = "intent-head";
  var k = document.createElement("span");
  // Class derived from the kind STRING, so a kind added to
  // `node_types.INTENT_KINDS` renders (with the default accent) without a
  // frontend edit. The vocabulary is not copied here — a node id would be a
  // graph fact this module may not encode, and a kind list would just be one
  // more thing to keep in sync for no gain.
  k.className = "intent-kind k-" + String(item.kind || "").replace(/[^a-z-]/g, "");
  k.textContent = item.kind || "";
  head.appendChild(k);
  var t = document.createElement("span"); t.className = "intent-title";
  t.textContent = item.title || "";
  head.appendChild(t);
  wrap.appendChild(head);

  var body = document.createElement("div");
  body.className = "prose-block doc-prose intent-statement";
  renderMarkdown(body, item.statement || "");
  wrap.appendChild(body);

  var foot = document.createElement("div"); foot.className = "intent-foot";
  if (inherited) foot.appendChild(intentOwnerRow(item));
  if (item.source) {
    var src = document.createElement("span"); src.className = "intent-source";
    src.textContent = item.source;
    foot.appendChild(src);
  }
  if (foot.childNodes.length) wrap.appendChild(foot);
  return wrap;
}

function intentGroup(title, arr, emptyText) {
  var wrap = document.createElement("div");
  var h = document.createElement("div"); h.className = "link-sub"; h.textContent = title;
  wrap.appendChild(h);
  if (!arr.length) {
    var e = document.createElement("div"); e.className = "muted-empty";
    e.textContent = emptyText; wrap.appendChild(e);
  } else {
    arr.forEach(function (i) { wrap.appendChild(intentRow(i, title !== "On this node")); });
  }
  return wrap;
}

// The compact Overview strip: titles only, above everything else. Intent is
// the first thing a human OR an agent should read about a node, and a tab you
// have to click is not that — but the full inherited set would swamp Overview,
// which is why the detail lives in its own tab and only the headline is here.
function intentStrip(node, onLoaded) {
  var sec = document.createElement("div"); sec.className = "panel-section intent-strip";
  sec.hidden = true;
  var label = document.createElement("div"); label.className = "panel-label";
  label.textContent = "Intent";
  sec.appendChild(label);
  var list = document.createElement("div"); list.className = "intent-strip-list";
  sec.appendChild(list);

  fetchFull(node.id).then(function (full) {
    var got = (full && full.intents) || { own: [], inherited: [] };
    var all = (got.own || []).concat(got.inherited || []);
    if (onLoaded) onLoaded(all.length);
    if (!all.length) return;   // stays hidden — no empty header
    all.forEach(function (i) {
      var row = document.createElement("button");
      row.type = "button"; row.className = "intent-strip-row";
      var k = document.createElement("span");
      k.className = "intent-kind k-" + String(i.kind || "").replace(/[^a-z-]/g, "");
      k.textContent = i.kind || "";
      row.appendChild(k);
      var t = document.createElement("span"); t.className = "intent-title";
      t.textContent = i.title || "";
      row.appendChild(t);
      row.addEventListener("click", function () { _showPanelTab("intent"); });
      list.appendChild(row);
    });
    sec.hidden = false;
  }).catch(function () { if (onLoaded) onLoaded(0); });

  return sec;
}

export function renderIntents(node) {
  var sec = document.createElement("div"); sec.className = "panel-section";
  var body = document.createElement("div");
  body.innerHTML = '<div class="muted-empty">loading…</div>';
  sec.appendChild(body);

  fetchFull(node.id).then(function (full) {
    var got = (full && full.intents) || { own: [], inherited: [] };
    body.innerHTML = "";
    body.appendChild(intentGroup("On this node", got.own || [],
      "Nothing authored here yet."));
    body.appendChild(intentGroup("Inherited", got.inherited || [],
      "Nothing inherited — no node above this one carries an intent."));
  }).catch(function () {
    body.innerHTML = '<div class="muted-empty">Could not load intent.</div>';
  });

  return sec;
}

// ------------------------------------------------------------- History ---
// Who changed this node, and on whose authority (TASK-1045's `graph_event`,
// read back through `GET /api/events?node_id=`). Zero call sites read that
// table before this one — it existed and nobody could see it.
//
// ACTION VOCABULARY mirrors `models_graph.GRAPH_EVENT_ACTIONS` (app.py's
// `record_graph_event` call sites): node create/update/status/assignee/
// delete, edge create/delete, and the node sub-records — doc write/delete,
// intent create/update/delete. An action this map doesn't know is shown
// verbatim rather than dropped — a future action the frontend hasn't been
// taught yet must still be legible.
var EVENT_ACTION_LABEL = {
  "node.create": "Created",
  "node.update": "Updated",
  "node.status": "Status changed",
  "node.assignee": "Assignee changed",
  "node.delete": "Deleted",
  "edge.create": "Link added",
  "edge.delete": "Link removed",
  "node.doc.write": "Document saved",
  "node.doc.delete": "Document removed",
  "node.intent.create": "Intent added",
  "node.intent.update": "Intent updated",
  "node.intent.delete": "Intent removed"
};
function eventActionLabel(action) {
  return EVENT_ACTION_LABEL[action] || String(action || "change");
}

// `detail` is not a diff (app.record_graph_event says so explicitly) — it is
// whatever the call site captured, at whatever granularity it captured it.
// This reads the shapes that actually occur rather than inventing a schema:
// {from,to} for a status/assignee change, {fields:[...]} for a body edit,
// {edge_type,src,dst} for an edge, {type,name[,via]} for a create, and the
// delete route's {type,name,edges_deleted}.
function eventDetailFmt(v) {
  return (v === null || v === undefined || v === "") ? "(none)" : String(v);
}

// `node.assignee`'s from/to are RESOLVED by the API (app._graph_event_json)
// into person objects — {id, username, name} when the account still exists,
// {id} alone when it doesn't. Never blank and never "null": no account is
// "(unassigned)", a fact about the workitem, and an id with nothing behind it
// still shows the id rather than vanishing.
function eventAssigneeText(v) {
  if (v === null || v === undefined) return "(unassigned)";
  if (typeof v === "object") return eventPersonLabel(v) || "(unknown account)";
  return String(v);
}

function eventDetailText(detail, action) {
  if (!detail || typeof detail !== "object") return null;
  var parts = [];
  if (Object.prototype.hasOwnProperty.call(detail, "from") ||
      Object.prototype.hasOwnProperty.call(detail, "to")) {
    // node.status's from/to are status strings, formatted as before.
    // node.assignee's are the resolved person objects above — a raw
    // auth_user id in this row is exactly the defect this branch fixes.
    if (action === "node.assignee") {
      parts.push(eventAssigneeText(detail.from) + " → " + eventAssigneeText(detail.to));
    } else {
      parts.push(eventDetailFmt(detail.from) + " → " + eventDetailFmt(detail.to));
    }
  }
  if (detail.fields && detail.fields.length) parts.push("fields: " + detail.fields.join(", "));
  if (detail.edge_type) {
    parts.push(detail.edge_type + ": " + eventDetailFmt(detail.src) + " → " + eventDetailFmt(detail.dst));
  }
  if (detail.type) parts.push("type: " + detail.type);
  if (detail.name) parts.push("name: " + detail.name);
  if (detail.via) parts.push("via " + detail.via);
  if (detail.cascade_of) parts.push("cascade of " + detail.cascade_of);
  if (typeof detail.edges_deleted === "number") {
    parts.push(detail.edges_deleted + (detail.edges_deleted === 1 ? " edge removed" : " edges removed"));
  }
  return parts.length ? parts.join(" · ") : null;
}

function eventPersonLabel(account) {
  if (!account) return null;
  return account.name || account.username || account.id || null;
}

function eventWhenText(iso) {
  if (!iso) return "unknown time";
  var d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

// The agent-vs-person distinction IS the point of this tab (TASK-1045's
// `responsible_user_id`, ADR 0034's person -> bot link). `actor.kind ===
// "token"` is a machine caller (an agent's own service token); a session
// caller acted as themself and has no separate "on whose authority" line to
// draw. When a token caller has no linked person, that is said in words —
// never left blank, which would read as "nobody to hold accountable" when it
// actually means "unrecorded".
function renderEventRow(ev) {
  var row = document.createElement("div"); row.className = "event-row";

  var when = document.createElement("div"); when.className = "event-when";
  when.textContent = eventWhenText(ev.at);
  if (ev.at) when.title = ev.at;
  row.appendChild(when);

  var action = document.createElement("div"); action.className = "event-action";
  action.textContent = eventActionLabel(ev.action);
  row.appendChild(action);

  var actor = ev.actor || {};
  var isAgent = actor.kind === "token";
  var actorName = eventPersonLabel(actor.account) || actor.subject ||
    (actor.kind === "system" ? "system" : "unknown caller");
  var responsibleName = eventPersonLabel(ev.responsible);

  var who = document.createElement("div"); who.className = "event-who";
  var actorSpan = document.createElement("span");
  actorSpan.className = "event-actor" + (isAgent ? " event-actor-agent" : "");
  actorSpan.textContent = actorName + (isAgent ? " (agent)" : "");
  who.appendChild(actorSpan);
  if (isAgent) {
    var authSpan = document.createElement("span");
    if (responsibleName) {
      authSpan.className = "event-on-behalf";
      authSpan.textContent = "on behalf of " + responsibleName;
    } else {
      authSpan.className = "event-no-responsible";
      authSpan.textContent = "no responsible person recorded";
    }
    who.appendChild(authSpan);
  }
  row.appendChild(who);

  var detailText = eventDetailText(ev.detail, ev.action);
  if (detailText) {
    var det = document.createElement("div"); det.className = "event-detail";
    det.textContent = detailText;
    row.appendChild(det);
  }

  return row;
}

// Pages honestly (AC 4): the label is always the server's own `total`, never
// a count of what happened to load, and running out of loaded rows shows a
// "Load more" control rather than silently stopping. Same idiom as mine.js's
// page-and-say-so, but click-driven rather than auto-paging — a node's
// history can run long and this is one section of a panel, not the whole view.
//
// DEFERRED (2026-08-24): this used to fetch on every node selection, whether
// or not History was the tab on screen — a `GET /api/events` round trip paid
// by every click, for a tab most sessions never open. The section is built
// synchronously as before, but the fetch is held behind `.ensureLoaded()`
// until the caller (renderPanel, below) actually shows the tab — once per
// node, on first activation, not on every re-activation of an already-loaded
// one. See renderPanel's showPanelTab for the call site and the badge
// trade-off it accepts by doing this.
export function renderHistory(node, onLoaded) {
  var sec = document.createElement("div"); sec.className = "panel-section";
  var body = document.createElement("div");
  body.innerHTML = '<div class="muted-empty">Not loaded — open History to fetch it.</div>';
  sec.appendChild(body);

  var items = [], total = 0, reqSeq = 0, loadMoreBtn = null, loadedPage = 0, started = false;

  function paint() {
    body.innerHTML = "";
    if (!items.length) {
      body.innerHTML = '<div class="muted-empty">No recorded history for this node.</div>';
      return;
    }
    var list = document.createElement("div"); list.className = "event-list";
    items.forEach(function (ev) { list.appendChild(renderEventRow(ev)); });
    body.appendChild(list);

    // ALWAYS the server's own `total` (AC 4) — never a count of what happened
    // to load. Matches mine.js's page-and-say-so label: complete counts read
    // as a plain total, a partial load says both numbers so it can never be
    // mistaken for the whole history.
    var status = document.createElement("div"); status.className = "event-list-status";
    status.textContent = items.length < total
      ? ("Showing " + items.length + " of " + total + (total === 1 ? " event" : " events"))
      : (total + (total === 1 ? " event" : " events"));
    body.appendChild(status);

    if (items.length < total) {
      loadMoreBtn = document.createElement("button");
      loadMoreBtn.type = "button"; loadMoreBtn.className = "event-load-more";
      loadMoreBtn.textContent = "Load more";
      loadMoreBtn.addEventListener("click", function () { load(loadedPage + 1); });
      body.appendChild(loadMoreBtn);
    }
  }

  function load(page) {
    var seq = ++reqSeq;
    if (page > 1 && loadMoreBtn) { loadMoreBtn.disabled = true; loadMoreBtn.textContent = "Loading…"; }
    fetchJSON("/api/events?node_id=" + encodeURIComponent(node.id) + "&page=" + page)
      .then(function (d) {
        if (seq !== reqSeq) return;
        total = d.total || 0;
        loadedPage = d.page || page;
        items = page === 1 ? (d.items || []) : items.concat(d.items || []);
        if (onLoaded) onLoaded(total || null);
        paint();
      })
      .catch(function () {
        if (seq !== reqSeq) return;
        body.innerHTML = '<div class="muted-empty">Could not load history.</div>';
      });
  }

  // Exposed on the returned element rather than fired here — see the
  // deferred-loading note above `renderHistory`. `started` makes repeat calls
  // (switching to History, away, and back) a no-op: exactly one fetch per
  // node, not one per activation.
  sec.ensureLoaded = function () {
    if (started) return;
    started = true;
    body.innerHTML = '<div class="muted-empty">loading…</div>';
    load(1);
  };

  return sec;
}

// Which panel tab (Overview/Intent/Work/History/Docs/Links) is showing —
// separate from atlas.tab, which is the World/Work TREE switch above the
// map. This is a view preference, not navigation state, so it persists
// across node selections (browsing several objects' Work tabs in a row
// keeps you on Work) but does NOT ride in the URL the way tab+node do.
var PANEL_TAB_KEY = "atlas.panelTab";
var PANEL_TABS = ["overview", "intent", "work", "history", "docs", "links"];
var panelTab = PANEL_TABS.indexOf(recall(PANEL_TAB_KEY)) !== -1 ? recall(PANEL_TAB_KEY) : "overview";

export function renderPanel(node) {
  // Drop the shared /api/node/{id} payload from the previous render — see
  // fetchFull. Selecting the SAME node again (after a status write, say) must
  // refetch, not redisplay what was on screen before the write.
  _fullCache = null;
  panelBody.innerHTML = "";

  // ---- pinned header: glyph/id/name/type + attention/state/urgency chips,
  // then the tab bar. Stays on screen while the tab body below it scrolls. --
  var fixed = document.createElement("div"); fixed.className = "panel-fixed";
  panelBody.appendChild(fixed);

  var head = document.createElement("div"); head.className = "panel-head";
  head.appendChild(standaloneGlyph(node, 88));
  var meta = document.createElement("div");
  meta.innerHTML = '<div class="panel-id">' + node.id + '</div><div class="panel-name">' +
    escapeHtml(node.name) + '</div><div class="panel-type">' + node.type + '</div>';
  head.appendChild(meta);
  fixed.appendChild(head);

  var chips = document.createElement("div"); chips.className = "chips";
  chips.innerHTML =
    chip("attention: " + ATT_LABEL[attentionOf(node)], attentionOf(node), true) +
    chip("state: " + ATT_LABEL[node.state || "grey"], node.state || "grey") +
    chip("urgency: " + (URG_LABEL[node.urgency || "none"] || node.urgency));
  fixed.appendChild(chips);

  var docs = (node.attributes && node.attributes.docs) || [];
  var kids = node.children || [];
  var workLoad = node.work_load || { count: 0 };

  var tabDefs = [
    { key: "overview", label: "Overview" },
    // Intent sits second, before Work: it is the standing answer to "what is
    // this for", which is the question that comes before "what is being done
    // about it". Its badge counts own + inherited and is set once the shared
    // /api/node/{id} fetch settles, below — a node with no intent of its own
    // still usually inherits one, so a synchronous zero would be wrong.
    { key: "intent", label: "Intent" },
    // Work's badge is known synchronously for an object (work_load is
    // already on the node); for a workitem it's set once renderConnections'
    // fetch settles, below.
    { key: "work", label: "Work", badge: isObject(node) ? (workLoad.count || null) : null },
    // History's badge is the `total` GET /api/events reports for this node,
    // once the first page settles below — never counted from what happened
    // to be loaded (AGENTS.md: never write a count a query could answer).
    { key: "history", label: "History" },
    { key: "docs", label: "Docs", badge: docs.length || null },
    // Links' badge starts as just the sub-node count; the depends_on fetch
    // (workitems only) tops it up once it resolves, below.
    { key: "links", label: "Links", badge: kids.length || null }
  ];

  var tabbar = document.createElement("div"); tabbar.className = "panel-tabbar";
  tabbar.setAttribute("role", "tablist");
  tabbar.setAttribute("aria-label", "Node detail sections");
  fixed.appendChild(tabbar);

  var tabpanels = document.createElement("div"); tabpanels.className = "panel-tabpanels";
  panelBody.appendChild(tabpanels);

  var tabButtons = {}, tabBodies = {};
  // Set once the History section is built, below; showPanelTab reads it to
  // trigger the deferred fetch on first activation. Declared here (rather
  // than only where it's assigned) so showPanelTab's closure — defined
  // before that point in the function — closes over the right variable.
  var historyEl = null;
  tabDefs.forEach(function (t) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "panel-tab-btn"; b.setAttribute("role", "tab");
    b.appendChild(document.createTextNode(t.label));
    if (t.badge) {
      var badge = document.createElement("span"); badge.className = "panel-tab-badge";
      badge.textContent = t.badge; b.appendChild(badge);
    }
    b.addEventListener("click", function () { showPanelTab(t.key); });
    tabbar.appendChild(b);
    tabButtons[t.key] = b;

    var tb = document.createElement("div"); tb.className = "panel-tabbody"; tb.setAttribute("role", "tabpanel");
    tabpanels.appendChild(tb);
    tabBodies[t.key] = tb;
  });

  function setTabBadge(key, n) {
    var b = tabButtons[key]; if (!b) return;
    var existing = b.querySelector(".panel-tab-badge");
    if (!n) { if (existing) existing.parentNode.removeChild(existing); return; }
    if (existing) { existing.textContent = n; return; }
    var badge = document.createElement("span"); badge.className = "panel-tab-badge";
    badge.textContent = n; b.appendChild(badge);
  }

  function showPanelTab(which) {
    panelTab = which;
    remember(PANEL_TAB_KEY, which);
    tabDefs.forEach(function (t) {
      var active = t.key === which;
      tabButtons[t.key].setAttribute("aria-selected", active ? "true" : "false");
      tabButtons[t.key].tabIndex = active ? 0 : -1;
      tabBodies[t.key].hidden = !active;
    });
    // Fires the History section's held-back fetch the first time (and only
    // the first time) the tab is actually shown — see renderHistory's
    // deferred-loading note.
    if (which === "history" && historyEl && historyEl.ensureLoaded) historyEl.ensureLoaded();
  }
  _showPanelTab = showPanelTab;

  // ---------------------------------------------------------- Overview ---
  // Intent first, above everything: it is what a human or an agent should
  // read about this node before its status or its numbers. Titles only —
  // the statements live in the Intent tab. Hidden entirely when there are
  // none, so a node without intent shows no empty header.
  tabBodies.overview.appendChild(intentStrip(node, function (n) {
    setTabBadge("intent", n);
  }));

  // Lifecycle status — settable, and only for workitems. A world-tree object
  // has no workflow status: its colour comes from a health hint, not a
  // lifecycle (attention.object_health), and the API refuses to set one.
  if (WORKITEM_TYPES[node.type]) tabBodies.overview.appendChild(renderStatusSection(node));

  // WHO IS DOING IT (TASK-1122). Sits directly under Status because the two
  // are the same kind of thing — a small authoritative fact about this item
  // that you change in place — and because a status you can set beside an
  // assignee you cannot would read as an oversight. Workitems only, for the
  // same reason: a world object has no assignee and the API refuses one.
  if (WORKITEM_TYPES[node.type]) tabBodies.overview.appendChild(renderAssigneeSection(node));

  // WHAT IT WORKS ON (TASK-1019). Third in the same run of small authoritative
  // facts you change in place — status, then who is doing it, then what part
  // of the world it is about. Workitems only, and for the harder of the two
  // reasons the assignee control is: `PATCH /api/workitem` 409s
  // `not_a_workitem` for anything else, AND a world object has no binding to
  // edit at all — its own place in the world is a `contains` edge, which is
  // not this control's business (see this section's header).
  if (WORKITEM_TYPES[node.type]) tabBodies.overview.appendChild(renderBindingSection(node));

  var cc = node.completion_counts || { done: 0, in_progress: 0, todo: 0 };
  var total = (cc.done || 0) + (cc.in_progress || 0) + (cc.todo || 0);
  var doneF = total ? cc.done / total : (node.completion || 0);
  var inpF = total ? cc.in_progress / total : 0;
  var compSec = document.createElement("div"); compSec.className = "panel-section";
  compSec.innerHTML =
    '<div class="panel-label">Completion</div>' +
    '<div class="completion-bar"><div class="seg-done" style="width:' + (doneF * 100) + '%"></div>' +
    '<div class="seg-inprog" style="width:' + (inpF * 100) + '%"></div></div>' +
    '<div class="completion-counts"><span><b>' + cc.done + '</b> done</span><span><b>' + cc.in_progress +
    '</b> in progress</span><span><b>' + cc.todo + '</b> todo</span></div>';
  tabBodies.overview.appendChild(compSec);

  // Authored node documentation (ADR 0020, TASK-864/866): abstract/detail
  // prose, passed through from the API onto the node. Editable for workitems
  // (writes `node.body`, see renderDescriptionSection's own header); read-only
  // for everything else, same display as before.
  tabBodies.overview.appendChild(renderDescriptionSection(node));

  // A pointer from Overview to this node's own documentation (owner
  // decision, 2026-08-16). The Overview is a SUMMARY; the architecture write-up lives on
  // the Docs tab, and nothing on screen said so -- you had to already know to
  // look. Clicking switches tabs and opens that document, so the pointer is a
  // route rather than a label.
  if (docs.length) {
    var dlSec = document.createElement("div"); dlSec.className = "panel-section";
    dlSec.innerHTML = '<div class="panel-label">Documentation</div>';
    var dlList = document.createElement("div"); dlList.className = "overview-doclinks";
    docs.forEach(function (d) {
      var lb = document.createElement("button");
      lb.type = "button"; lb.className = "overview-doclink";
      lb.innerHTML = '<span class="doc-kind">' + escapeHtml(d.kind) + "</span>" +
                     '<span class="dl-title">' + escapeHtml(d.title) + "</span>";
      lb.addEventListener("click", function () {
        showPanelTab("docs");
        var row = tabBodies.docs.querySelector('[data-doc-path="' + d.path + '"]');
        if (!row) return;
        var head = row.querySelector(".doc-row-head.clickable");
        var viewer = row.querySelector(".doc-viewer");
        if (head && viewer && viewer.hidden) head.click();
        if (row.scrollIntoView) row.scrollIntoView({ block: "nearest" });
      });
      dlList.appendChild(lb);
    });
    dlSec.appendChild(dlList);
    tabBodies.overview.appendChild(dlSec);
  }

  // Acceptance criteria, directly under the description: "what is this" then
  // "how do we know it is done" is the order the question actually arrives in
  // (owner decision, 2026-08-10). Workitems only — `isObject` covers the object AND
  // bridge families, and neither carries acceptance.
  // Then what happened, last: "what is this" -> "how do we know it is done"
  // -> "what actually happened" is the order the questions arrive in, and the
  // result is the only one of the three that is often absent.
  if (!isObject(node)) {
    tabBodies.overview.appendChild(renderAcceptance(node));
    tabBodies.overview.appendChild(renderResult(node));
  }

  // ------------------------------------------------------------- Intent ---
  tabBodies.intent.appendChild(renderIntents(node));

  // --------------------------------------------------------------- Work ---
  // Object: the direct work_load list (synchronous, see renderWorkList).
  // Workitem: what IT works on / serves (live fetch, see renderConnections).
  if (isObject(node)) {
    tabBodies.work.appendChild(renderWorkList(node));
  } else {
    tabBodies.work.appendChild(renderConnections(node, function (n) { setTabBadge("work", n); }));
  }

  // ------------------------------------------------------------ History ---
  historyEl = renderHistory(node, function (n) { setTabBadge("history", n); });
  tabBodies.history.appendChild(historyEl);

  // -------------------------------------------------------------- Docs ---
  if (docs.length) {
    var docsList = document.createElement("div"); docsList.className = "docs-list";
    docs.forEach(function (d) { docsList.appendChild(renderDocRow(node, d)); });
    tabBodies.docs.appendChild(docsList);
  } else {
    var noDocs = document.createElement("div"); noDocs.className = "muted-empty";
    noDocs.textContent = "No docs attached.";
    tabBodies.docs.appendChild(noDocs);
  }

  // ------------------------------------------------------------- Links ---
  // depends_on only makes sense for workitems (backlog nodes); an object
  // gets a pointer to Work instead of an empty "Blocked by / Blocks" pair.
  if (!isObject(node)) {
    var linksSec = document.createElement("div"); linksSec.className = "panel-section";
    linksSec.innerHTML = '<div class="panel-label">Depends on</div><div class="link-sub">Blocked by (depends on)</div>';
    var blockedByWrap = document.createElement("div"); blockedByWrap.className = "link-list";
    blockedByWrap.innerHTML = '<div class="muted-empty">loading…</div>';
    linksSec.appendChild(blockedByWrap);
    var blocksHdr = document.createElement("div"); blocksHdr.className = "link-sub"; blocksHdr.textContent = "Blocks (depended on by)";
    linksSec.appendChild(blocksHdr);
    var blocksWrap = document.createElement("div"); blocksWrap.className = "link-list";
    blocksWrap.innerHTML = '<div class="muted-empty">loading…</div>';
    linksSec.appendChild(blocksWrap);
    tabBodies.links.appendChild(linksSec);

    fetchJSON("/api/graph?focus=" + encodeURIComponent(node.id) + "&depth=1").then(function (g) {
      var nodesById = {}; (g.nodes || []).forEach(function (n) { nodesById[n.id] = n; });
      var blockedBy = [], blocks = [];
      (g.edges || []).forEach(function (e) {
        if (e.edge_type !== "depends_on") return;
        if (e.src === node.id && nodesById[e.dst]) blockedBy.push(nodesById[e.dst]);
        if (e.dst === node.id && nodesById[e.src]) blocks.push(nodesById[e.src]);
      });
      blockedByWrap.innerHTML = "";
      if (!blockedBy.length) blockedByWrap.innerHTML = '<div class="muted-empty">Nothing — no upstream dependency.</div>';
      else blockedBy.forEach(function (n) { blockedByWrap.appendChild(renderLinkRow(n, "blocks this")); });
      blocksWrap.innerHTML = "";
      if (!blocks.length) blocksWrap.innerHTML = '<div class="muted-empty">Nothing depends on this.</div>';
      else blocks.forEach(function (n) { blocksWrap.appendChild(renderLinkRow(n, "waiting on this")); });
      setTabBadge("links", blockedBy.length + blocks.length + kids.length);
    }).catch(function () {
      blockedByWrap.innerHTML = '<div class="muted-empty">Could not load links.</div>';
      blocksWrap.innerHTML = '<div class="muted-empty">Could not load links.</div>';
    });
  } else {
    var linksNote = document.createElement("div"); linksNote.className = "muted-empty";
    linksNote.textContent = "World objects don't carry dependency links — see Work for what's attached to this object.";
    tabBodies.links.appendChild(linksNote);
  }

  var subSec = document.createElement("div"); subSec.className = "panel-section";
  subSec.innerHTML = '<div class="panel-label">Sub-nodes</div>';
  var subList = document.createElement("div"); subList.className = "subnode-list";
  if (!kids.length) subList.innerHTML = '<div class="muted-empty">No children — this is a leaf.</div>';
  else kids.forEach(function (c) { subList.appendChild(renderSubnodeRow(c)); });
  subSec.appendChild(subList);
  tabBodies.links.appendChild(subSec);

  // land on whichever tab is remembered; fall back to Overview if that key
  // somehow doesn't exist on this render (it always does today — defensive).
  showPanelTab(tabButtons[panelTab] ? panelTab : "overview");
}
