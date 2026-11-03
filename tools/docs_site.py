#!/usr/bin/env python3
"""Render the Atlas handbook (STORY-atlas-system-node-handbook) into the static /docs site.

THE SOURCE is `backend/system_handbook.py`: the system node set and every document's markdown,
as Python data, the way seed content ships (`demo_seed.py`). `backend/system_node.py` seeds the
same data into `node_doc` on a fresh install, so the board, an agent's `atlas_doc_read` and
`/docs` read one text.

THE OUTPUT is `frontend/docs/`: one HTML page per document, an index and one stylesheet. No
script at all, inline or not, and no external URL, so the ingress CSP (script-src 'self',
style-src 'self') needs nothing new. `frontend/` is already in the image, so the Dockerfile and
deploy_status's spec are unchanged. `routers/handbook.py` serves exactly
`system_handbook.SITE_FILES` from it.

WHY COMMIT THE OUTPUT rather than build it in the image: the Dockerfile's default stage must stay
today's image (its own header), the image has no generator step, and a committed output is
reviewable in the diff. The price is staleness, so `--check` re-renders in memory and fails on
any difference, any missing file and any extra file under frontend/docs/. CI runs it
(.github/workflows/verify.yml, the `docs` job); `backend/verify_system_node.py` runs it too.

Standard library only. The markdown subset is small on purpose and is what the handbook uses:
`#`-headings, paragraphs, `-` and `1.` lists, `>` notes, fenced code, inline code, `**bold**`
and links. Everything is HTML-escaped before any markup is added, and a link that is not a `/`
path or an `https://` URL is dropped to its text.

    python tools/docs_site.py           # write frontend/docs/
    python tools/docs_site.py --check   # exit 1 if it is stale
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "backend" / "system_handbook.py"
SITE = ROOT / "frontend" / "docs"
SITE_PREFIX = "/docs/"

CSS = """\
:root { --bg: #ffffff; --fg: #1d1f23; --muted: #5c6370; --rule: #dde1e6; --code: #f3f4f6; --link: #1f5fbf; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #16181c; --fg: #e6e8eb; --muted: #9aa3ad; --rule: #2c3036; --code: #22262c; --link: #7fb0ff; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
       font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { display: flex; gap: 2rem; max-width: 64rem; margin: 0 auto; padding: 1.5rem 1rem; }
nav { flex: 0 0 13rem; font-size: 0.92rem; }
nav h2 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin: 1.2rem 0 0.4rem; }
nav ul { list-style: none; margin: 0; padding: 0; }
nav li { margin: 0.15rem 0; }
nav a[aria-current="page"] { font-weight: 600; }
main { flex: 1 1 auto; min-width: 0; }
a { color: var(--link); }
h1 { font-size: 1.8rem; margin-top: 0; }
h2 { font-size: 1.3rem; margin-top: 2rem; border-bottom: 1px solid var(--rule); padding-bottom: 0.2rem; }
code { background: var(--code); padding: 0.1em 0.3em; border-radius: 3px; font-size: 0.92em; }
pre { background: var(--code); padding: 0.8rem 1rem; border-radius: 4px; overflow-x: auto; }
pre code { padding: 0; background: none; }
blockquote { margin: 1rem 0; padding: 0.4rem 1rem; border-left: 3px solid var(--rule); color: var(--muted); }
.summary { color: var(--muted); }
footer { color: var(--muted); font-size: 0.85rem; border-top: 1px solid var(--rule); margin-top: 3rem; padding-top: 0.8rem; }
@media (max-width: 44rem) { .wrap { flex-direction: column; gap: 0.5rem; } nav { flex-basis: auto; } }
"""


# --------------------------------------------------------------------------- #
# the source
# --------------------------------------------------------------------------- #
def load():
    """`backend/system_handbook.py`, imported by path (it imports nothing), and validated."""
    spec = importlib.util.spec_from_file_location("system_handbook", MODULE)
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    ids = [n["id"] for n in h.NODES]
    if len(set(ids)) != len(ids):
        raise SystemExit("system_handbook: duplicate node id")
    if not ids or ids[0] != h.ROOT:
        raise SystemExit("system_handbook: the root must come first")
    for n in h.NODES:
        if not n["id"].startswith("atlas-system"):
            raise SystemExit(f"system_handbook: {n['id']!r} does not start with 'atlas-system'")
        if n["id"] != h.ROOT and n["parent"] not in ids[: ids.index(n["id"])]:
            raise SystemExit(f"system_handbook: {n['id']!r} has no parent before it in the set")
        for d in n["docs"]:
            if not re.fullmatch(r"[a-z0-9-]+\.md", d["file"]):
                raise SystemExit(f"system_handbook: {n['id']}: bad document file {d['file']!r}")
    return h


# --------------------------------------------------------------------------- #
# markdown -> html, the subset above
# --------------------------------------------------------------------------- #
def _href(url: str) -> str | None:
    if url.startswith("https://") or (url.startswith("/") and not url.startswith("//")):
        return url
    return None


def _inline(text: str) -> str:
    out = []
    for i, part in enumerate(re.split(r"(`[^`]+`)", text)):
        if i % 2:
            out.append("<code>" + html.escape(part[1:-1]) + "</code>")
            continue
        part = html.escape(part, quote=True)
        part = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", part)

        def link(m: re.Match) -> str:
            href = _href(html.unescape(m.group(2)))
            label = m.group(1)
            return f'<a href="{html.escape(href, quote=True)}">{label}</a>' if href else label

        part = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, part)
        out.append(part)
    return "".join(out)


def to_html(md: str) -> str:
    lines = md.split("\n")
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag = ""
    quote: list[str] = []

    def flush() -> None:
        nonlocal list_tag
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()
        if items:
            out.append(f"<{list_tag}>" + "".join(f"<li>{_inline(t)}</li>" for t in items) + f"</{list_tag}>")
            items.clear()
            list_tag = ""
        if quote:
            out.append("<blockquote><p>" + _inline(" ".join(quote)) + "</p></blockquote>")
            quote.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            lang = stripped[3:].strip()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            cls = f' class="language-{html.escape(lang)}"' if re.fullmatch(r"[a-z0-9]+", lang or "") else ""
            out.append(f"<pre><code{cls}>" + html.escape("\n".join(body)) + "</code></pre>")
        elif not stripped:
            flush()
        elif m := re.match(r"^(#{1,4}) (.+)$", stripped):
            flush()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
        elif stripped.startswith("> ") or stripped == ">":
            if para or items:
                flush()
            quote.append(stripped[1:].strip())
        elif m := re.match(r"^(-|\d+\.) (.+)$", stripped):
            tag = "ul" if m.group(1) == "-" else "ol"
            if para or quote or (items and tag != list_tag):
                flush()
            list_tag = tag
            items.append(m.group(2))
        elif items and line.startswith(" "):
            items[-1] += " " + stripped          # a list item's continuation line
        else:
            if items or quote:
                flush()
            para.append(stripped)
        i += 1
    flush()
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# the site
# --------------------------------------------------------------------------- #
def _page(h, title: str, current: str, body: str) -> str:
    nav = []
    for n in h.NODES:
        nav.append(f"<h2>{html.escape(n['name'])}</h2><ul>")
        for d in n["docs"]:
            pg = h.page(n["id"], d)
            cur = ' aria-current="page"' if pg == current else ""
            nav.append(f'<li><a href="{SITE_PREFIX}{pg}"{cur}>{html.escape(d["title"])}</a></li>')
        nav.append("</ul>")
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)} - Atlas handbook</title>\n"
        f'<link rel="stylesheet" href="{SITE_PREFIX}docs.css">\n'
        "</head>\n<body>\n<div class=\"wrap\">\n"
        f'<nav aria-label="Handbook"><a href="{SITE_PREFIX}">Atlas handbook</a>\n' + "\n".join(nav) + "\n</nav>\n"
        f"<main>\n{body}<footer>Generated from the documents of the <code>atlas-system</code> node "
        "(<code>backend/system_handbook.py</code>) by <code>tools/docs_site.py</code>. "
        "Do not edit this file.</footer>\n</main>\n"
        "</div>\n</body>\n</html>\n"
    )


def render_site(h) -> dict[str, str]:
    files: dict[str, str] = {"docs.css": CSS}
    index = ["<h1>Atlas handbook</h1>",
             "<p class=\"summary\">How this Atlas install works: the same documents an agent reads "
             "on the <code>atlas-system</code> node and its children.</p>"]
    for n in h.NODES:
        index.append(f"<h2>{html.escape(n['name'])}</h2>")
        index.append(f"<p class=\"summary\">{html.escape(n['summary'])}</p><ul>")
        for d in n["docs"]:
            pg = h.page(n["id"], d)
            index.append(f'<li><a href="{SITE_PREFIX}{pg}">{html.escape(d["title"])}</a></li>')
            files[pg] = _page(h, d["title"], pg, to_html(d["content"]))
        index.append("</ul>")
    files["index.html"] = _page(h, "Atlas handbook", "", "\n".join(index) + "\n")
    if sorted(files) != list(h.SITE_FILES):
        raise SystemExit("system_handbook.SITE_FILES does not match the pages rendered")
    return dict(sorted(files.items()))


def outputs() -> dict[Path, str]:
    return {SITE / Path(*name.split("/")): text for name, text in render_site(load()).items()}


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def check() -> list[str]:
    """Every stale, missing or extra output file, repo-relative. Empty when up to date."""
    want = outputs()
    problems = [f"stale or missing: {p.relative_to(ROOT).as_posix()}"
                for p, text in want.items() if _read(p) != text]
    if SITE.is_dir():
        problems += [f"not generated (remove it): {p.relative_to(ROOT).as_posix()}"
                     for p in sorted(SITE.rglob("*")) if p.is_file() and p not in want]
    return problems


def write() -> list[str]:
    want = outputs()
    if SITE.is_dir():
        for p in SITE.rglob("*"):
            if p.is_file() and p not in want:
                p.unlink()
    changed = []
    for p, text in want.items():
        if _read(p) != text:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            changed.append(p.relative_to(ROOT).as_posix())
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="fail if the generated outputs are stale")
    args = ap.parse_args(argv)
    if args.check:
        problems = check()
        for p in problems:
            print(f"docs_site: {p}", file=sys.stderr)
        if problems:
            print("docs_site: frontend/docs/ is stale; run `python tools/docs_site.py` "
                  "and commit the result", file=sys.stderr)
            return 1
        print(f"docs_site: up to date ({len(outputs())} files)")
        return 0
    changed = write()
    print(f"docs_site: wrote {len(changed)} file(s)" + "".join(f"\n  {c}" for c in changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
