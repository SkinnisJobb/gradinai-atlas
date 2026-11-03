// @ts-check
// views/panel/adrs.js — the Decisions tab (ADR 0045): a project's architecture
// decision records, from `/api/adrs?project=`, and one record's detail from
// `/api/adrs/{id}`.
// - The list: id, legacy number, title, a status chip and the date.
// - The detail: status, date, the links both ways (supersedes / superseded by,
//   amends / amended by), the source (a git file with its GitHub link, or
//   "Recorded in Atlas" for an Atlas-native record), and the markdown.
// - A link into another project selects that project and opens the record there.
// - `?adr=AT001` in the URL opens that record. The router does not own the
//   parameter, so it survives every other URL change untouched.
// - An old server answers 404 for `/api/adrs`; the tab then stays hidden.

import { fetchJSON } from "../../core/api.js";
import { el, button } from "../../ui/dom.js";
import { md, runDiagrams } from "../../ui/md.js";

/** The status chip's CSS class. @param {string|null|undefined} status */
export function statusClass(status) {
  const s = String(status || "unknown").toLowerCase();
  return "adr-st adr-st-" + (["proposed", "accepted", "deprecated", "superseded", "rejected"].includes(s) ? s : "unknown");
}

/**
 * The record's file on GitHub, or null. Pinned to the synced commit when there is one,
 * so the page shows the text Atlas indexed.
 * @param {{repo?: string|null, path?: string|null, commit?: string|null}} r
 */
export function githubUrl(r) {
  if (!r || !r.repo || !r.path || !/^[\w.-]+\/[\w.-]+$/.test(r.repo)) return null;
  return `https://github.com/${r.repo}/blob/${encodeURIComponent(r.commit || "HEAD")}/${r.path.split("/").map(encodeURIComponent).join("/")}`;
}

/**
 * Where a record comes from (TASK-1354), as the detail shows it: a git file with its GitHub
 * link, or "Recorded in Atlas". A server older than the `source` field sends none: git.
 * @param {{source?: string|null, repo?: string|null, path?: string|null, commit?: string|null}} r
 * @returns {{kind: "atlas"|"git", text: string, url: string|null}}
 */
export function sourceInfo(r) {
  if (r && r.source === "atlas") return { kind: "atlas", text: "Recorded in Atlas", url: null };
  const path = r && r.path ? r.path : null;
  return { kind: "git", text: path ? "Git file · " + path : "Git file", url: githubUrl(r) };
}

/** The `adr` URL parameter, or null. @param {string} search */
export function adrParam(search) {
  const v = new URLSearchParams(search).get("adr");
  return v && /^[A-Za-z]{2,4}\d{3,}$/.test(v) ? v.toUpperCase() : null;
}

/** `search` with `adr` set or removed, every other parameter kept. @param {string} search @param {string|null} id */
export function withAdrParam(search, id) {
  const p = new URLSearchParams(search);
  if (id) p.set("adr", id); else p.delete("adr");
  const qs = p.toString();
  return qs ? "?" + qs : "";
}

/** The link rows a detail shows, in order, skipping empty groups. @param {any} r */
export function linkGroups(r) {
  return [["Supersedes", r.supersedes], ["Superseded by", r.superseded_by],
    ["Amends", r.amends], ["Amended by", r.amended_by]]
    .filter(([, list]) => Array.isArray(list) && list.length)
    .map(([label, list]) => ({ label: /** @type {string} */ (label), links: /** @type {any[]} */ (list) }));
}

/** @param {string|null} id */
function setUrl(id) {
  try { history.replaceState(null, "", location.pathname + withAdrParam(location.search, id) + location.hash); } catch { /* not a browser */ }
}

/** @param {any} r */
function chip(r) { return el("span", statusClass(r.status), r.status || "unknown"); }

/**
 * How many ADRs a project has, or null when the server has no registry (404) or fails.
 * @param {string} project
 * @returns {Promise<number|null>}
 */
export function probeDecisions(project) {
  return fetchJSON(`/api/adrs?project=${encodeURIComponent(project)}&page_size=1`)
    .then((r) => (typeof r.total === "number" ? r.total : null), () => null);
}

/**
 * @param {HTMLElement} body
 * @param {{id: string, store: any}} ctx
 * @param {(total: number|null) => void} [onCount]
 */
export function renderDecisions(body, ctx, onCount) {
  body.replaceChildren(el("div", "empty", "Loading…"));
  fetchJSON(`/api/adrs?project=${encodeURIComponent(ctx.id)}&page_size=200`).then((r) => {
    if (onCount) onCount(r.total || null);
    const want = adrParam(location.search);
    if (want && r.items.some((/** @type {any} */ i) => i.id === want)) { renderDetail(body, ctx, want); return; }
    renderList(body, ctx, r);
  }).catch((err) => {
    if (onCount) onCount(null);
    body.replaceChildren(el("div", "empty", "Could not load the decisions: " + (err && err.message)));
  });
}

/** @param {HTMLElement} body @param {{id: string, store: any}} ctx @param {any} r */
function renderList(body, ctx, r) {
  body.replaceChildren();
  if (!r.items.length) { body.append(el("div", "empty", "No ADRs recorded")); return; }
  const list = el("div", "adr-list");
  for (const i of r.items) {
    const row = button("", () => { setUrl(i.id); renderDetail(body, ctx, i.id); }, "adr-row");
    row.append(el("span", "adr-id", i.id), el("span", "pid", i.legacy || ""), el("b", "adr-title", i.title),
      chip(i), el("span", "pid", i.date || ""));
    if (i.orphaned) row.append(el("span", "chip", "file gone"));
    if (i.source === "atlas") row.append(el("span", "chip", "in Atlas"));
    list.append(row);
  }
  body.append(list, el("div", "pid", `${r.total} decision${r.total === 1 ? "" : "s"}`));
}

/** @param {HTMLElement} body @param {{id: string, store: any}} ctx @param {string} adrId */
function renderDetail(body, ctx, adrId) {
  body.replaceChildren(el("div", "empty", "Loading…"));
  fetchJSON(`/api/adrs/${encodeURIComponent(adrId)}`).then((r) => {
    body.replaceChildren();
    const back = button("← All decisions", () => { setUrl(null); renderDecisions(body, ctx); }, "linkbtn");
    const head = el("div", "adr-head");
    head.append(el("span", "adr-id", r.id), el("span", "pid", r.legacy || ""), chip(r), el("span", "pid", r.date || "no date"));
    body.append(back, head, el("h3", "adr-name", r.title));
    if (r.status_raw && r.status_raw.toLowerCase() !== String(r.status)) body.append(el("div", "pid", r.status_raw));
    if (r.orphaned_at) body.append(el("div", "note", "This record's file is no longer in git; Atlas keeps the last synced text."));
    for (const g of linkGroups(r)) {
      const line = el("div", "adr-links");
      line.append(el("span", "dkind", g.label));
      for (const l of g.links) {
        if (l.missing) { line.append(el("span", "chip", l.id + " (not in Atlas)")); continue; }
        const b = button(`${l.id} ${l.title}`, () => {
          setUrl(l.id);
          if (l.project === ctx.id) renderDetail(body, ctx, l.id);
          else ctx.store.set({ selected: l.project });
        }, "linkbtn adr-link");
        b.title = `${l.status} · ${l.project}`;
        line.append(b);
      }
      body.append(line);
    }
    const src = sourceInfo(r);
    const srcLine = el("div", "adr-links");
    srcLine.append(el("span", "dkind", "Source"), el("span", "pid", src.text));
    if (src.url) {
      const a = /** @type {HTMLAnchorElement} */ (el("a", "linkbtn", "Open the file on GitHub ↗"));
      a.href = src.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      srcLine.append(a);
    }
    body.append(srcLine);
    const holder = el("div", "docbody");
    if (r.content) holder.append(md(r.content)); else holder.append(el("div", "empty", "No text stored."));
    body.append(holder);
    runDiagrams(holder);
  }).catch((err) => {
    body.replaceChildren(button("← All decisions", () => { setUrl(null); renderDecisions(body, ctx); }, "linkbtn"),
      el("div", "empty", "Could not load " + adrId + ": " + (err && err.message)));
  });
}
