// @ts-check
// ui/md.js — prose renders as markdown, and ```mermaid fences as diagrams, as
// they do everywhere in the legacy board (`renderMarkdown` in panel.js).
//
// marked is the vendored global from `/assets/marked.min.js`, loaded by
// index.html. mermaid is 3 MB, so it is loaded on first use, never on boot.

import { el } from "./dom.js";

/** @param {string|null|undefined} text */
export function md(text) {
  const box = el("div", "md");
  if (!text) return box;
  const marked = /** @type {any} */ (window).marked;
  try {
    if (!marked) throw new Error("marked missing");
    box.innerHTML = marked.parse(text, { gfm: true, breaks: false });
  } catch {
    box.textContent = text;
    return box;
  }
  for (const code of box.querySelectorAll("pre > code.language-mermaid")) {
    const pre = el("pre", "mermaid");
    pre.textContent = code.textContent;
    /** @type {HTMLElement} */ (code.parentNode).replaceWith(pre);
  }
  for (const a of box.querySelectorAll("a[href]")) { a.setAttribute("target", "_blank"); a.setAttribute("rel", "noopener"); }
  return box;
}

/** @type {Promise<any>|null} */
let loading = null;
function loadMermaid() {
  const w = /** @type {any} */ (window);
  if (w.mermaid) return Promise.resolve(w.mermaid);
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "/assets/mermaid.min.js";
      s.onload = () => {
        const root = document.documentElement, theme = root.getAttribute("data-theme");
        const dark = theme === "dark" || (!theme && window.matchMedia("(prefers-color-scheme: dark)").matches);
        w.mermaid.initialize({ startOnLoad: false, theme: dark ? "dark" : "neutral", securityLevel: "strict" });
        resolve(w.mermaid);
      };
      s.onerror = () => reject(new Error("mermaid did not load"));
      document.head.append(s);
    });
  }
  return loading;
}

/** Draw any diagrams inside `scope` that are not drawn yet. @param {ParentNode} scope */
export function runDiagrams(scope) {
  const nodes = [...scope.querySelectorAll("pre.mermaid:not([data-processed])")];
  if (!nodes.length) return;
  loadMermaid().then((m) => m.run({ nodes })).catch(() => {
    for (const n of nodes) n.classList.add("mermaid-failed");
  });
}
