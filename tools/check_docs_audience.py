#!/usr/bin/env python3
"""Check that every page under docs/oss/ declares its audience (TASK-1429, STORY-oss-20).

THE CONTRACT. Every `.md` file under docs/oss/, at any depth (so future guides/ and examples/
pages are covered without touching this script), begins with exactly

    ---
    audience: user
    ---

or the same block with `audience: operations-security`, then one blank line, then the page's
own first line. No other keys. `user` is how to use Atlas (concepts, agents, the MCP tools, the
API, guides); `operations-security` is installing, configuring, running and securing an install.
docs/oss/README.md, the index, groups the pages under the same two headings.

The three generated pages (CONFIG.md, MCP-TOOLS.md, API.md) get the block from their generators
(tools/gen_*_ref.py), so regenerating keeps it.

Standard library only. Line endings are normalised (CRLF -> LF) before checking, as the
generators' --check does, so a Windows checkout passes.

    python tools/check_docs_audience.py              # exit 1 and one line per bad page
    python tools/check_docs_audience.py --self-test  # prove a bad page makes the check fail
    python tools/check_docs_audience.py --root DIR   # check another tree (used by the self-test)
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = Path("docs") / "oss"
AUDIENCES = ("user", "operations-security")

_BLOCK = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.S)


def page_error(text: str) -> str | None:
    """Return why `text` breaks the contract, or None when it is fine."""
    text = text.replace("\r\n", "\n")
    m = _BLOCK.match(text)
    if not m:
        return "does not start with an audience frontmatter block (--- / audience: ... / ---)"
    lines = m.group("body").split("\n")
    if len(lines) != 1 or not lines[0].startswith("audience:"):
        return "frontmatter must hold exactly one line, `audience: <value>`"
    value = lines[0][len("audience:"):]
    if not value.startswith(" ") or value.strip() not in AUDIENCES or value != " " + value.strip():
        return f"audience is {value.strip()!r}; it must be exactly one of {', '.join(AUDIENCES)}"
    rest = text[m.end():]
    if not rest.startswith("\n") or rest[1:2] in ("", "\n"):
        return "frontmatter must be followed by exactly one blank line and then the page"
    return None


def check(root: Path) -> list[str]:
    docs = root / DOCS
    pages = sorted(docs.rglob("*.md")) if docs.is_dir() else []
    if not pages:
        return [f"{DOCS.as_posix()}: no .md pages found under {docs}"]
    errors = []
    for p in pages:
        err = page_error(p.read_text(encoding="utf-8"))
        if err:
            errors.append(f"{p.relative_to(root).as_posix()}: {err}")
    return errors


def self_test() -> int:
    good_user = "---\naudience: user\n---\n\n# A page\n\nBody.\n"
    good_ops = "---\naudience: operations-security\n---\n\n# 0001. A decision\n\n- **Status:** Accepted\n"
    bad = {
        "missing": "# A page with no frontmatter\n",
        "wrong value": "---\naudience: public\n---\n\n# A page\n",
        "empty value": "---\naudience:\n---\n\n# A page\n",
        "extra key": "---\naudience: user\ntitle: x\n---\n\n# A page\n",
        "other key only": "---\ntitle: x\n---\n\n# A page\n",
        "no blank line": "---\naudience: user\n---\n# A page\n",
        "two blank lines": "---\naudience: user\n---\n\n\n# A page\n",
        "unclosed": "---\naudience: user\n\n# A page\n",
        "not at the top": "\n---\naudience: user\n---\n\n# A page\n",
        "capitalised": "---\naudience: User\n---\n\n# A page\n",
    }
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        docs = root / DOCS
        (docs / "guides" / "deeper").mkdir(parents=True)
        (docs / "README.md").write_text(good_user, encoding="utf-8")
        (docs / "adr.md").write_text(good_ops, encoding="utf-8")
        (docs / "crlf.md").write_bytes(good_user.replace("\n", "\r\n").encode())
        if check(root):
            failures.append(f"good tree reported errors: {check(root)}")
        for name, text in bad.items():
            # Nested two levels down, to prove the check is recursive.
            page = docs / "guides" / "deeper" / "bad.md"
            page.write_text(text, encoding="utf-8")
            errs = check(root)
            page.unlink()
            if len(errs) != 1 or not errs[0].startswith("docs/oss/guides/deeper/bad.md:"):
                failures.append(f"bad page ({name}) not caught: {errs}")
            else:
                print(f"self-test: caught a bad page ({name}): {errs[0]}")
        for p in list(docs.rglob("*.md")):
            p.unlink()
        if not check(root):
            failures.append("an empty docs/oss passed")
    for f in failures:
        print(f"self-test FAILED: {f}", file=sys.stderr)
    if not failures:
        print("self-test: ok")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--self-test", action="store_true", help="prove that bad pages fail the check")
    ap.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this repo)")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    errors = check(args.root)
    for e in errors:
        print(f"::error file={e.split(':', 1)[0]},title=docs audience::{e}")
    if errors:
        print(f"{len(errors)} page(s) under {DOCS.as_posix()} break the audience contract "
              f"(see tools/check_docs_audience.py)", file=sys.stderr)
        return 1
    n = len(list((args.root / DOCS).rglob("*.md")))
    print(f"docs audience: all {n} pages under {DOCS.as_posix()} declare user or operations-security")
    return 0


if __name__ == "__main__":
    sys.exit(main())
