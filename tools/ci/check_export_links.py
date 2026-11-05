#!/usr/bin/env python3
"""check_export_links.py: no Markdown link in the exported docs points outside the export.

WHY. The public repository is built from an allowlist of this repository's paths (the
manifest). A relative link that is fine here (`docs/adr/...`, `tools/deploy.sh`) is a dead
link in the export, because its target is not shipped. This check lists every relative link
in the given roots and fails on a target that is outside the include set, or that does not
exist on disk.

WHAT IS PARSED. Every *.md file under each root (a root may also be a single file):
  - inline links and images `[text](target "title")`, `![alt](<target>)`;
  - reference definitions `[id]: target`;
  - HTML `href="..."` and `src="..."` attributes.
Fenced code blocks and inline code spans are skipped (shell examples are not links).
A target with a scheme (`https:`, `mailto:`), a protocol-relative `//host` target and a bare
`#anchor` are not relative and are ignored. The `#fragment` and `?query` are dropped; the path
is URL-decoded and resolved against the linking file's directory (`/x` against the repo root).

THE INCLUDE SET. A list of globs over repo-relative POSIX paths: `*` and `?` stay inside one
path segment, `**` crosses segments, a trailing `/` means everything below that directory, and
a line starting with `!` excludes what it matches (excludes win). Directory targets are in the
export when at least one file below them is. The set comes from `--manifest FILE` (one glob per
line, `#` comments), or else from DEFAULT_INCLUDE / DEFAULT_EXCLUDE below, which mirror "The
allowlist" of the public-repository plan (include, exclude-inside-include and overlay lists).

OVERLAYS. `--overlay DIR=DEST` resolves links in files under DIR as if DIR were placed at DEST
(repo-relative, `.` for the root): an overlay `README.md` links as the root README will.

Usage:
  python tools/ci/check_export_links.py [--manifest FILE] [--overlay DIR=DEST] [--list] ROOT...
  python tools/ci/check_export_links.py --self-test

  --list   print every relative link with its verdict, not only the failures.
Exit 0 when every relative link resolves inside the export, 1 on any failure, 2 on bad usage.
Not wired into any workflow here: the export wires it into the public copy.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]

#: The plan's allowlist: "Include" plus "Overlay". Keep in step with the plan's manifest.
DEFAULT_INCLUDE = [
    "backend/", "frontend/", "mcp/", "clients/",
    "deploy/oss/", "deploy/openshift/", "deploy/gateway/", "deploy/caddy/", "deploy/postgres/",
    "docs/oss/", "scripts/", "tests/",
    "tools/scrub/", "tools/ci/", "tools/self_seed/", "tools/site/", "tools/baseload/",
    "tools/export_self_seed.py", "tools/docs_site.py", "tools/gen_api_ref.py",
    "tools/gen_config_ref.py", "tools/gen_tool_ref.py", "tools/gen_baseload.py",
    "tools/check_docs_audience.py", "tools/check_examples.py", "tools/db_collation_check.sh",
    "tools/adr_sync.py", "tools/adr_sources.example.json",
    ".github/workflows/", ".github/dependabot.yml", ".gitignore",
    "docker-compose.yml", "LICENSE", "NOTICE",
    # overlays: shipped, but from tools/carve/overlay/ rather than as they are here
    "AGENTS.md", "CLAUDE.md", "README.md", "CONTRIBUTING.md", "SECURITY.md",
    "backend/distribution.py", "mcp/app/distribution.py",
]
#: The plan's "Exclude inside included paths".
DEFAULT_EXCLUDE = [
    "backend/seed.py", "backend/plans/",
    "backend/verify_run_guard.py", "backend/verify_run_log.py",
    "tools/ci/test_deploy_sh.sh", "tools/ci/test_snapshot_sh.sh",
    "mcp/.env.example", ".github/workflows/carve.yml",
]

FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
# [text](target) and ![alt](target); text may hold one level of nested brackets.
INLINE = re.compile(r"!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*(<[^>]*>|[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)")
REFDEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)")
HTML_ATTR = re.compile(r"""\b(?:href|src)\s*=\s*["']([^"']+)["']""", re.I)
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def glob_to_regex(pattern: str) -> re.Pattern:
    """`*`/`?` within a segment, `**` across segments, trailing `/` = the whole subtree."""
    if pattern.endswith("/"):
        pattern += "**"
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


class IncludeSet:
    def __init__(self, include: list, exclude: list):
        self.include = [glob_to_regex(p) for p in include]
        self.exclude = [glob_to_regex(p) for p in exclude]

    @classmethod
    def from_lines(cls, lines) -> "IncludeSet":
        inc, exc = [], []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            (exc if line.startswith("!") else inc).append(line.lstrip("!").strip())
        return cls(inc, exc)

    def has_file(self, rel: str) -> bool:
        return any(r.match(rel) for r in self.include) and not any(r.match(rel) for r in self.exclude)

    def has(self, root: Path, rel: str) -> bool:
        """A file is in the export when it matches; a directory when any file below it does."""
        path = root / rel
        if not path.is_dir():
            return self.has_file(rel)
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in filenames:
                sub = Path(dirpath, name).relative_to(root).as_posix()
                if self.has_file(sub):
                    return True
        return False


def links(text: str):
    """Yield (line number, target) for every link outside code blocks and code spans."""
    fence = None
    for n, line in enumerate(text.splitlines(), 1):
        m = FENCE.match(line)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark[0] * len(mark)
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        line = CODE_SPAN.sub("", line)
        found = [m.group(1) for m in INLINE.finditer(line)]
        found += [m.group(1) for m in HTML_ATTR.finditer(line)]
        m = REFDEF.match(line)
        if m:
            found.append(m.group(1))
        for target in found:
            target = target.strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1].strip()
            if target:
                yield n, target


def resolve(target: str, rel_dir: str):
    """The repo-relative POSIX path a relative target names, '' for the root, None if not
    relative, or '..' when it climbs out of the repository."""
    if SCHEME.match(target) or target.startswith("//") or target.startswith("#"):
        return None
    path = unquote(re.split(r"[#?]", target, maxsplit=1)[0])
    if not path:
        return None
    joined = path.lstrip("/") if path.startswith("/") else f"{rel_dir}/{path}" if rel_dir else path
    norm = os.path.normpath(joined).replace("\\", "/")
    if norm == "..":
        return ".."
    if norm.startswith("../"):
        return ".."
    return "" if norm == "." else norm


def md_files(root: Path, top: Path):
    if top.is_file():
        yield top
        return
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in sorted(filenames):
            if name.lower().endswith(".md"):
                yield Path(dirpath, name)


def check(root: Path, tops: list, incl: IncludeSet, overlays: dict, show_all: bool = False):
    """Return (rows, failures): rows are printable lines, failures the failing subset."""
    rows, failures = [], []
    root = root.resolve()
    for top in tops:
        top = (root / top).resolve() if not Path(top).is_absolute() else Path(top).resolve()
        for f in md_files(root, top):
            rel_file = f.relative_to(root).as_posix()
            placed = rel_file
            for src, dest in overlays.items():
                if rel_file == src or rel_file.startswith(src + "/"):
                    tail = rel_file[len(src):].lstrip("/")
                    placed = os.path.normpath(f"{dest}/{tail}").replace("\\", "/")
                    break
            rel_dir = os.path.dirname(placed)
            text = f.read_text(encoding="utf-8", errors="replace")
            for n, target in links(text):
                rel = resolve(target, rel_dir)
                if rel is None:
                    continue
                if rel == "..":
                    verdict = "outside-repo"
                elif rel == "":
                    verdict = "ok"
                elif not (root / rel).exists():
                    verdict = "missing"
                elif not incl.has(root, rel):
                    verdict = "not-exported"
                else:
                    verdict = "ok"
                row = f"{verdict:<13} {rel_file}:{n}: {target}" + (f"  -> {rel}" if rel not in ("..", "") else "")
                if verdict != "ok":
                    failures.append(row)
                if show_all or verdict != "ok":
                    rows.append(row)
    return rows, failures


def self_test() -> int:
    rc = 0
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for p in ["docs/oss/guides/a.md", "docs/oss/b.md", "docs/adr/0051.md", "backend/seed.py",
                  "backend/app.py", "clients/pack/README.md", "tools/deploy.sh", "tools/ci/x.py",
                  "overlay/README.md", "LICENSE", "docs/private/only.md"]:
            (root / p).parent.mkdir(parents=True, exist_ok=True)
            (root / p).write_text("x\n", encoding="utf-8")
        incl = IncludeSet(["docs/oss/", "backend/", "clients/", "tools/ci/", "LICENSE", "README.md"],
                          ["backend/seed.py"])
        cases = [
            ("ok", "[b](../b.md)"),
            ("ok", "[b](../b.md#section)"),
            ("ok", "![img](<../b.md>)"),
            ("ok", "[pack](../../../clients/pack/README.md#three-steps)"),
            ("ok", "[dir](../../../backend/)"),
            ("ok", "[root](/LICENSE)"),
            ("ok", "[web](https://example.com/docs/adr/x.md)"),
            ("ok", "[mail](mailto:security@example.com)"),
            ("ok", "[anchor](#local)"),
            ("ok", "`[code](../../adr/0051.md)`"),
            ("ok", "```\n[fenced](../../adr/0051.md)\n```"),
            ("not-exported", "[ADR](../../adr/0051.md)"),
            ("not-exported", "[seed](../../../backend/seed.py)"),
            ("not-exported", "see [deploy] for more\n\n[deploy]: ../../../tools/deploy.sh"),
            ("not-exported", '<a href="../../private/">x</a>'),
            ("missing", "[gone](../nope.md)"),
            ("outside-repo", "[up](../../../../elsewhere.md)"),
        ]
        target = root / "docs/oss/guides/a.md"
        for want, text in cases:
            target.write_text(text + "\n", encoding="utf-8")
            _, fails = check(root, ["docs/oss/guides/a.md"], incl, {})
            got = fails[0].split()[0] if fails else "ok"
            ok = got == want and len(fails) <= 1
            print(f"self-test {'ok  ' if ok else 'FAIL'} {got:<13} (want {want:<13}) {text!r}")
            rc |= 0 if ok else 1
        # an overlay resolves as if placed at its destination
        (root / "overlay/README.md").write_text("[l](LICENSE) [a](docs/adr/0051.md)\n", encoding="utf-8")
        _, fails = check(root, ["overlay"], incl, {"overlay": "."})
        ok = len(fails) == 1 and fails[0].startswith("not-exported") and "docs/adr/0051.md" in fails[0]
        print(f"self-test {'ok  ' if ok else 'FAIL'} overlay resolves against its destination: {fails}")
        rc |= 0 if ok else 1
        # a manifest file: globs, comments, excludes
        m = IncludeSet.from_lines(["# c", "docs/**/*.md", "!docs/adr/*", "", "tools/ci/*.py"])
        probes = {"docs/oss/b.md": True, "docs/adr/0051.md": False, "tools/ci/x.py": True,
                  "tools/deploy.sh": False, "docs/oss/guides/a.md": True}
        for rel, want in probes.items():
            got = m.has(root, rel)
            ok = got == want
            print(f"self-test {'ok  ' if ok else 'FAIL'} manifest has({rel}) = {got} (want {want})")
            rc |= 0 if ok else 1
        # the default set excludes the private ADRs and the homelab seed
        dflt = IncludeSet(DEFAULT_INCLUDE, DEFAULT_EXCLUDE)
        for rel, want in {"docs/adr/0051.md": False, "backend/seed.py": False, "backend/app.py": True,
                          "tools/deploy.sh": False, "tools/ci/x.py": True, "LICENSE": True}.items():
            got = dflt.has(root, rel)
            ok = got == want
            print(f"self-test {'ok  ' if ok else 'FAIL'} default has({rel}) = {got} (want {want})")
            rc |= 0 if ok else 1
    print("self-test", "passed" if rc == 0 else "FAILED")
    return rc


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="Fail on a relative Markdown link outside the export.")
    ap.add_argument("roots", nargs="*", help="files or directories to scan for *.md")
    ap.add_argument("--manifest", help="include globs, one per line; '!' excludes, '#' comments")
    ap.add_argument("--overlay", action="append", default=[], metavar="DIR=DEST",
                    help="resolve links in DIR as if DIR sat at DEST (repo-relative, '.' = root)")
    ap.add_argument("--list", action="store_true", help="print every relative link, not only failures")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv[1:])
    if args.self_test:
        return self_test()
    if not args.roots:
        ap.print_usage(sys.stderr)
        return 2
    if args.manifest:
        incl = IncludeSet.from_lines(Path(args.manifest).read_text(encoding="utf-8").splitlines())
    else:
        incl = IncludeSet(DEFAULT_INCLUDE, DEFAULT_EXCLUDE)
    overlays = {}
    for spec in args.overlay:
        src, sep, dest = spec.partition("=")
        if not sep:
            print(f"--overlay wants DIR=DEST, got {spec!r}", file=sys.stderr)
            return 2
        overlays[Path(src).as_posix().strip("/")] = dest.strip("/") or "."
    rows, failures = check(ROOT, args.roots, incl, overlays, args.list)
    for row in rows:
        print(row)
    if failures:
        print(f"check_export_links: FAILED ({len(failures)} link(s) outside the export or missing). "
              f"See the header of {Path(__file__).name}.")
        return 1
    print("check_export_links: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
