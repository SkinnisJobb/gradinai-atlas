"""
Chunking (ADR 0044 §3): what text a vector stands for. Pure functions, no database, no I/O.

NODE CARD, one per world node: a header line `"<name> [<type>]"`, then summary, abstract and
detail. Split only past ~1,500 tokens, on paragraphs.

DOCUMENTS: split on markdown headings, then on paragraphs, to roughly 300-600 tokens, with the
last paragraph of one chunk repeated at the start of the next. Each chunk starts with
`node name › doc title › heading path`, so a section embedded on its own still says what it
is about. A fenced block (mermaid, code) becomes one line naming its label: the prose around
a diagram carries its meaning, and a vector of arrow syntax matches nothing anyone asks.

TOKENS ARE ESTIMATED as characters / 4. The bounds are soft targets for retrieval quality,
not limits a model enforces; bge-m3 accepts 8,192 tokens, so an estimate that is off by half
still fits.

`CHUNKER_VERSION` is part of every content hash (`reconciler.source_hash`). Change the version
whenever a change here should re-embed what is already indexed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CHUNKER_VERSION = "1"

CARD_MAX_TOKENS = 1500
DOC_MIN_TOKENS = 300
DOC_MAX_TOKENS = 600
SEP = " › "

_FENCE = re.compile(r"^(```|~~~)\s*([\w+-]*)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    heading: str
    content: str


def tokens(s: str) -> int:
    return (len(s) + 3) // 4


def _paragraphs(s: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", s) if p.strip()]


def _pack(paras: list[str], max_tokens: int, overlap: bool) -> list[list[str]]:
    """Greedy: fill a group up to `max_tokens`, then start the next one (with the previous
    group's last paragraph when `overlap`). A single paragraph over the bound is its own
    group; it is never cut mid-sentence."""
    groups: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for p in paras:
        t = tokens(p)
        if cur and size + t > max_tokens:
            groups.append(cur)
            cur = [cur[-1]] if overlap and tokens(cur[-1]) < max_tokens // 2 else []
            size = sum(tokens(x) for x in cur)
        cur.append(p)
        size += t
    if cur:
        groups.append(cur)
    return groups


def node_card(name: str, ntype: str, summary: str | None, abstract: str | None,
              detail: str | None) -> list[Chunk]:
    header = f"{name} [{ntype}]"
    body = [x.strip() for x in (summary, abstract, detail) if x and x.strip()]
    paras = [p for part in body for p in _paragraphs(part)]
    if tokens(header + "\n\n" + "\n\n".join(paras)) <= CARD_MAX_TOKENS:
        return [Chunk(0, "", "\n\n".join([header] + paras))]
    return [Chunk(i, "", "\n\n".join([header] + g))
            for i, g in enumerate(_pack(paras, CARD_MAX_TOKENS, overlap=False))]


def _sections(content: str) -> list[tuple[list[str], str]]:
    """(heading path, section text) in document order, fences replaced by a placeholder.
    A `#` inside a fence is not a heading."""
    out: list[tuple[list[str], str]] = []
    path: list[str] = []
    buf: list[str] = []
    fence: str | None = None

    def flush() -> None:
        txt = "\n".join(buf).strip()
        if txt:
            out.append((list(path), txt))
        buf.clear()

    for line in content.splitlines():
        m = _FENCE.match(line.strip())
        if fence is not None:
            if m and m.group(1) == fence:
                fence = None
            continue
        if m:
            fence = m.group(1)
            label = m.group(2) or "code"
            buf.append("")
            buf.append(f"[{label} block omitted]")
            buf.append("")
            continue
        h = _HEADING.match(line)
        if h:
            flush()
            level = len(h.group(1))
            del path[level - 1:]
            path.extend([""] * (level - 1 - len(path)))
            path.append(h.group(2))
            continue
        buf.append(line)
    flush()
    return out


def doc_chunks(node_name: str, doc_title: str, content: str) -> list[Chunk]:
    """Chunks of one served document. Sections smaller than DOC_MIN_TOKENS are merged with the
    next one under the same top-level heading, so a run of short sections is not a run of
    vectors each too small to match anything."""
    pieces: list[tuple[str, list[str]]] = []  # (heading path text, paragraphs)
    for path, txt in _sections(content):
        heading = SEP.join(h for h in path if h)
        paras = _paragraphs(txt)
        if (pieces and tokens("\n\n".join(pieces[-1][1])) < DOC_MIN_TOKENS
                and (pieces[-1][0].split(SEP)[0] == heading.split(SEP)[0])):
            pieces[-1] = (pieces[-1][0], pieces[-1][1] + ([f"{path[-1]}:"] if path else []) + paras)
            continue
        pieces.append((heading, paras))

    chunks: list[Chunk] = []
    for heading, paras in pieces:
        prefix = SEP.join(x for x in (node_name, doc_title, heading) if x)
        for group in _pack(paras, DOC_MAX_TOKENS, overlap=True):
            chunks.append(Chunk(len(chunks), heading, prefix + "\n\n" + "\n\n".join(group)))
    return chunks
