#!/usr/bin/env python3
"""Personal-reference scrub gate (STORY-oss-05).

Scans the tracked files of a repository for references that must not ship in
the open-source tree: personal names, a personal domain, homelab hostnames and
LAN addresses.

Two kinds of pattern:

* **Generic patterns** live here, in the repo, because they name nothing
  personal: RFC 1918 IPv4 addresses, ``<host>.local`` names and email-shaped
  strings.
* **Denylist patterns** are the personal terms themselves. They are NEVER
  committed - committing them would leak exactly what is being scrubbed. They
  are read from the file named by ``SCRUB_DENYLIST_FILE`` (or ``--denylist``).
  In CI that file is materialised from the ``SCRUB_DENYLIST`` repository
  secret. If no denylist is available the scan warns and runs the generic
  patterns only; it never fails for that reason.

Denylist file format, one pattern per line::

    # comment lines and blank lines are ignored
    some literal term          <- case-insensitive literal, word-bounded
    re:some[- ]regex           <- case-insensitive regular expression

Denylist patterns are reported as ``denylist[N]`` (N = 1-based position among
the patterns), never by their text, and matched content is never printed, so
neither the log nor the report artifact repeats a personal term.

Allowlist (``tools/scrub/allowlist.txt`` by default), one rule per line::

    <path-glob>                <- every pattern is allowed in matching paths
    <path-glob> <pattern>      <- only that pattern (e.g. rfc1918-ipv4,
                                  denylist[2]) is allowed there

Globs are ``fnmatch`` style against the repo-relative POSIX path; ``*``
also crosses ``/``, so ``docs/examples/*`` covers the whole subtree.

``--mask-github-owner NAME`` (the repository owner's account) allows one account
name in one place: before the patterns run on a file, every case-insensitive
``github.com/NAME`` that is followed by ``/`` or by the end of the token (no
further ``[A-Za-z0-9-]``) is read as ``github.com/`` plus carve.py's owner
placeholder. Nothing else is masked: the bare name, a longer account name and
any other host still hit. The public repository's scrub workflow passes its own
owner; a downstream installation's scrub may leave it out to stay stricter.

Modes:

* ``report``  (default) - print counts per pattern and per file, exit 0.
* ``enforce`` - same output, exit 1 if any non-allowlisted finding remains.

``--path GLOB`` (repeatable) limits the scan to matching paths, so enforce
mode can be switched on for a subtree (for example the paths the OSS export
carries) while the rest of the repo stays in report mode.

Exit codes: 0 clean or report mode, 1 enforce mode with findings, 2 usage or
configuration error (for example an unreadable allowlist or a bad regex).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
# A GitHub account name, as tools/carve/carve.py validates --owner.
GITHUB_ACCOUNT = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
# carve.py's placeholder, split so the export's own --owner substitution leaves it alone.
OWNER_PLACEHOLDER = "<owner-" + "account>"
DEFAULT_ALLOWLIST = HERE / "allowlist.txt"

# Domains that are reserved for documentation (RFC 2606 / RFC 6761) or that are
# well-known no-reply senders. An email at one of these names nobody.
_SAFE_EMAIL_DOMAINS = (
    r"example\.(?:com|org|net)|[a-z0-9.-]*\.example|[a-z0-9.-]*\.test|"
    r"[a-z0-9.-]*\.invalid|[a-z0-9.-]*\.localhost|users\.noreply\.github\.com|"
    r"anthropic\.com"
)

GENERIC_PATTERNS: dict[str, str] = {
    # RFC 1918: 10/8, 172.16/12, 192.168/16. Look-arounds keep version
    # strings like 1.10.0.0.1 and longer dotted runs from matching.
    "rfc1918-ipv4": (
        r"(?<![\d.])(?:"
        r"10(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}"
        r"|172\.(?:1[6-9]|2\d|3[01])(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){2}"
        r"|192\.168(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){2}"
        r")(?![\d]|\.\d)"
    ),
    # mDNS / homelab names (<host> dot "local"). Not ~/.local/bin
    # (no host label before the dot) and not .localhost.
    "dot-local-host": r"\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.local\b(?!-)",
    # Any mailbox outside the reserved / no-reply domains above.
    "email": (
        r"\b[a-z0-9._%+-]+@(?!(?:" + _SAFE_EMAIL_DOMAINS + r")\b)"
        r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b"
    ),
}

BINARY_SNIFF = 8192
MAX_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class Pattern:
    name: str
    regex: re.Pattern[str]
    generic: bool


@dataclass(frozen=True)
class AllowRule:
    glob: str
    pattern: str | None  # None = every pattern


class ConfigError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _meaningful_lines(text: str):
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line and not line.startswith("#"):
            yield lineno, line


def generic_patterns() -> list[Pattern]:
    return [
        Pattern(name, re.compile(rx, re.IGNORECASE), True)
        for name, rx in GENERIC_PATTERNS.items()
    ]


def load_denylist(path: Path) -> list[Pattern]:
    """Parse a denylist. Errors name the line number, never the line text."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read denylist file: {exc.strerror}") from None
    out: list[Pattern] = []
    for lineno, line in _meaningful_lines(text):
        if line.startswith("re:"):
            source = line[3:].strip()
        else:
            source = r"(?<!\w)" + re.escape(line) + r"(?!\w)"
        try:
            rx = re.compile(source, re.IGNORECASE)
        except re.error:
            raise ConfigError(f"denylist line {lineno}: invalid regular expression") from None
        out.append(Pattern(f"denylist[{len(out) + 1}]", rx, False))
    return out


def load_allowlist(path: Path | None) -> list[AllowRule]:
    if path is None or not path.exists():
        return []
    rules: list[AllowRule] = []
    for _, line in _meaningful_lines(path.read_text(encoding="utf-8")):
        parts = line.split()
        if len(parts) > 2:
            raise ConfigError(f"allowlist rule has more than two fields: {line!r}")
        rules.append(AllowRule(parts[0], parts[1] if len(parts) == 2 else None))
    return rules


def is_allowed(rel: str, pattern: str, rules: list[AllowRule]) -> bool:
    return any(
        fnmatch.fnmatchcase(rel, r.glob) and (r.pattern is None or r.pattern == pattern)
        for r in rules
    )


def github_owner_mask(name: str) -> re.Pattern[str]:
    """``github.com/NAME`` as a whole account segment, for --mask-github-owner."""
    if not GITHUB_ACCOUNT.fullmatch(name):
        raise ConfigError("--mask-github-owner is not a GitHub account name")
    return re.compile(
        r"(?<![A-Za-z0-9-])(github\.com/)" + re.escape(name) + r"(?![A-Za-z0-9-])",
        re.IGNORECASE,
    )


# --------------------------------------------------------------------------- #
# File discovery
# --------------------------------------------------------------------------- #


def list_files(root: Path) -> list[str]:
    """Tracked files when root is a git work tree, else every file below it."""
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=True,
        )
        return sorted(p for p in res.stdout.decode("utf-8", "replace").split("\0") if p)
    except (OSError, subprocess.CalledProcessError):
        pass
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            files.append(Path(dirpath, fn).relative_to(root).as_posix())
    return sorted(files)


def read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:BINARY_SNIFF]:
        return None
    return data.decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #


def scan(root: Path, patterns: list[Pattern], allow: list[AllowRule], path_globs: list[str],
         mask: re.Pattern[str] | None = None):
    """Return {path: {pattern: (count, allowed)}} for every file with a hit."""
    results: dict[str, dict[str, tuple[int, bool]]] = {}
    scanned = 0
    for rel in list_files(root):
        if path_globs and not any(fnmatch.fnmatchcase(rel, g) for g in path_globs):
            continue
        text = read_text(root / rel)
        if text is None:
            continue
        scanned += 1
        if mask is not None:
            text = mask.sub(lambda m: m.group(1) + OWNER_PLACEHOLDER, text)
        for p in patterns:
            n = sum(1 for _ in p.regex.finditer(text))
            if n:
                results.setdefault(rel, {})[p.name] = (n, is_allowed(rel, p.name, allow))
    return results, scanned


def summarise(results, patterns: list[Pattern], scanned: int, denylist_state: str, mode: str):
    per_pattern = {}
    for p in patterns:
        files = hits = allowed_files = 0
        for counts in results.values():
            if p.name in counts:
                n, ok = counts[p.name]
                hits += n
                files += 1
                allowed_files += ok
        per_pattern[p.name] = {
            "files": files,
            "hits": hits,
            "allowlisted_files": allowed_files,
            "kind": "generic" if p.generic else "denylist",
        }
    violations = sorted(
        (rel, name)
        for rel, counts in results.items()
        for name, (_, ok) in counts.items()
        if not ok
    )
    return {
        "mode": mode,
        "denylist": denylist_state,
        "files_scanned": scanned,
        "files_with_findings": len(results),
        "violations": len(violations),
        "per_pattern": per_pattern,
        "per_file": {
            rel: {name: {"hits": n, "allowlisted": ok} for name, (n, ok) in sorted(c.items())}
            for rel, c in sorted(results.items())
        },
    }


def render_markdown(summary) -> str:
    lines = [
        f"## Scrub report ({summary['mode']} mode)",
        "",
        f"Denylist: **{summary['denylist']}**. Files scanned: {summary['files_scanned']}. "
        f"Files with findings: {summary['files_with_findings']}. "
        f"Non-allowlisted (file, pattern) pairs: **{summary['violations']}**.",
        "",
        "| pattern | kind | files | hits | allowlisted files |",
        "|---|---|---:|---:|---:|",
    ]
    for name, s in summary["per_pattern"].items():
        lines.append(
            f"| `{name}` | {s['kind']} | {s['files']} | {s['hits']} | {s['allowlisted_files']} |"
        )
    lines += ["", "<details><summary>Per file</summary>", "", "| file | pattern | hits | allowlisted |", "|---|---|---:|---|"]
    for rel, counts in summary["per_file"].items():
        for name, c in counts.items():
            lines.append(f"| `{rel}` | `{name}` | {c['hits']} | {'yes' if c['allowlisted'] else ''} |")
    lines += ["", "</details>", ""]
    return "\n".join(lines)


def render_text(summary) -> str:
    out = [
        f"scrub: mode={summary['mode']} denylist={summary['denylist']} "
        f"files_scanned={summary['files_scanned']} "
        f"files_with_findings={summary['files_with_findings']} violations={summary['violations']}",
        "",
        "per pattern (files / hits / allowlisted files):",
    ]
    for name, s in summary["per_pattern"].items():
        out.append(f"  {name:<16} {s['files']:>5} {s['hits']:>7} {s['allowlisted_files']:>5}  ({s['kind']})")
    out += ["", "per file:"]
    for rel, counts in summary["per_file"].items():
        bits = ", ".join(
            f"{name}={c['hits']}{' (allowed)' if c['allowlisted'] else ''}" for name, c in counts.items()
        )
        out.append(f"  {rel}: {bits}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _warn(msg: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning title=scrub::{msg}")
    else:
        print(f"WARNING: {msg}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=("report", "enforce"), default=os.environ.get("SCRUB_MODE", "report"))
    ap.add_argument("--root", default=".", help="repository root (default: cwd)")
    ap.add_argument("--denylist", default=os.environ.get("SCRUB_DENYLIST_FILE") or None,
                    help="denylist file (default: $SCRUB_DENYLIST_FILE)")
    ap.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST))
    ap.add_argument("--path", action="append", default=[], dest="paths",
                    help="only scan paths matching this glob (repeatable)")
    ap.add_argument("--generic-only", action="store_true", help="ignore any denylist")
    ap.add_argument("--mask-github-owner", metavar="NAME",
                    help="allow NAME only as the account in github.com/NAME links (OSS export)")
    ap.add_argument("--json", help="write the full report as JSON here")
    ap.add_argument("--markdown", help="write a Markdown report here")
    ap.add_argument("--quiet", action="store_true", help="print only the per-pattern totals")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    patterns = generic_patterns()
    try:
        if args.generic_only:
            denylist_state = "skipped (--generic-only)"
        elif not args.denylist:
            denylist_state = "absent - generic patterns only"
            _warn("no denylist (SCRUB_DENYLIST_FILE unset): scanning generic patterns only")
        elif not Path(args.denylist).is_file() or Path(args.denylist).stat().st_size == 0:
            denylist_state = "absent - generic patterns only"
            _warn("denylist file missing or empty: scanning generic patterns only")
        else:
            deny = load_denylist(Path(args.denylist))
            patterns += deny
            denylist_state = f"loaded ({len(deny)} patterns)"
            if not deny:
                _warn("denylist file holds no patterns: scanning generic patterns only")
        allow = load_allowlist(Path(args.allowlist) if args.allowlist else None)
        mask = github_owner_mask(args.mask_github_owner) if args.mask_github_owner else None
    except ConfigError as exc:
        print(f"scrub: configuration error: {exc}", file=sys.stderr)
        return 2

    results, scanned = scan(root, patterns, allow, args.paths, mask)
    summary = summarise(results, patterns, scanned, denylist_state, args.mode)

    text = render_text(summary)
    if args.quiet:
        text = text.split("\n\nper file:")[0]
    print(text)
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    md = render_markdown(summary)
    if args.markdown:
        Path(args.markdown).write_text(md, encoding="utf-8")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(md)

    if args.mode == "enforce" and summary["violations"]:
        print(f"\nscrub: FAIL - {summary['violations']} non-allowlisted (file, pattern) pairs", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
