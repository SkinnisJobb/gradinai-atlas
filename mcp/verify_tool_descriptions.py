"""Prove the atlas-mcp tool descriptions stay small and caller-facing (TASK-1164).

FastMCP sends each tool function's DOCSTRING as its MCP `description` -- that
text is what every calling agent pays for, on every session, whether or not it
ever calls the tool. This script imports the real `app.mcp_server` module (a
stub env, no real network) and reads the descriptions back off the live
FastMCP registry, the same way `atlas_capabilities` does, then checks:

  - every tool has a non-empty description, and it is the one app/descriptions.py holds
    for that name: the registry and that file list the same tools
  - no single description exceeds 400 characters
  - the total across every registered tool does not exceed 10,000 characters (see
    TOTAL_MAX_CHARS for its history)
  - no description contains a ticket-id pattern (TASK-\\d+, STORY-, EPIC-,
    ADR \\d+) -- those belong in code comments and commit messages, not in
    text a calling agent is billed context for on every session

`atlas_delete` registers whenever ATLAS_MCP_ENABLE_DELETE is set (set below).
Since caller auth (2026-10) registration makes NO network call -- there is no
service token to ask `/api/me` about; each caller's own scope is checked by the
API per call. `httpx.Client.request` is patched to raise on any call before
`app.mcp_server` is imported, so this script also proves that importing and
registering the whole surface talks to nobody.

    python mcp/verify_tool_descriptions.py      # needs only httpx installed

RUN IT UNDER THE IMAGE'S PYTHON (3.12). Python 3.13+ dedents docstrings at
compile time, so on a newer interpreter the indentation check cannot fail and
every size reads smaller than what the container sends -- which is how a
first version of this script passed at 7,980 chars while live sent 9,159:
    docker run --rm -v "$PWD/mcp:/mcp" -w /mcp python:3.12-slim sh -c       'pip install -q mcp==1.12.4 httpx==0.28.1 && python verify_tool_descriptions.py'

Same convention as backend/verify_*.py and mcp/verify_atlas_tree.py: one
[PASS]/[FAIL] line per claim, a size table, exit 1 if anything failed.
"""

import inspect
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("ATLAS_BASE_URL", "https://atlas.invalid")
os.environ.setdefault("ATLAS_MCP_ENABLE_DELETE", "1")
# This script proves a feature that ATLAS_DISABLED_FEATURES can switch off, so it pins the
# default (every feature on); verify_feature_flags.py proves the switched-off side.
os.environ.pop("ATLAS_DISABLED_FEATURES", None)
sys.path.insert(0, str(Path(__file__).resolve().parent))

DESCRIPTION_MAX_CHARS = 400
# History: 8,000; raised to 8,400 on 2026-10-02 for atlas_structure and to 8,700 on 2026-10-23 for
# atlas_path, both owner decisions. Then, the same day, the owner asked for the descriptions to be split
# instead: each is now the short form in app/descriptions.py, and the detail moved to the tools
# guide (atlas-mcp's docs/nodes/atlas-mcp/tools.md), read on demand. The cut took the total from
# 8,698 to 3,224, so the budget was lowered to 4,500 to keep it there rather than to refill it.
# Raised to 10,000 (owner decision, 2026-10-26), so the setup-guide tools and
# whatever follows fit without a fight over every word. The per-tool cap and the other rules
# below are unchanged; the short-form-plus-tools-guide split still holds.
TOTAL_MAX_CHARS = 10_000
#: TASK-1234, STORY-some-slug, EPIC-some-slug, "ADR 1234" -- ids that belong in
#: a comment or a commit, not in text sent to every calling agent every session.
_TICKET_ID_RE = re.compile(r"TASK-\d+|STORY-|EPIC-|ADR \d+")

failures: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def budget_violations(descriptions: dict, *, max_chars: int = DESCRIPTION_MAX_CHARS,
                       total_max: int = TOTAL_MAX_CHARS) -> list:
    """Every reason `descriptions` (name -> text) fails the budget, as a list
    of short strings. Empty means the budget holds. Pure function, no I/O, so
    it can be exercised directly against a synthetic case (see §D below) --
    proof this check can actually fail, not just that it has not yet."""
    problems = []
    total = 0
    for name, desc in descriptions.items():
        text = desc or ""
        total += len(text)
        if not text.strip():
            problems.append(f"{name}: empty description")
            continue
        if len(text) > max_chars:
            problems.append(f"{name}: {len(text)} chars > {max_chars}")
        if text != inspect.cleandoc(text):
            problems.append(f"{name}: carries source indentation "
                            f"({len(text) - len(inspect.cleandoc(text))} chars)")
        hit = _TICKET_ID_RE.search(text)
        if hit:
            problems.append(f"{name}: contains a ticket-id pattern ({hit.group(0)!r})")
    if total > total_max:
        problems.append(f"TOTAL: {total} chars > {total_max}")
    return problems


def _import_with_no_network() -> None:
    """Import and register the whole surface with every HTTP call made fatal.

    `httpx.Client.request` is patched at the CLASS level before `app.client`
    builds its shared client. Registration -- atlas_delete's included -- must
    not need atlas-api: there is no credential at startup to call it with.
    """
    import httpx

    def _no_network(self, method, url, *a, **kw):
        raise AssertionError(
            f"unexpected HTTP call while registering tools: {method} {url} -- "
            "registration must make no call; atlas-mcp holds no token of its own."
        )

    httpx.Client.request = _no_network

    from app import mcp_server  # noqa: E402,F401 (path set above)


def main() -> None:
    _import_with_no_network()
    from app import mcp_server  # already imported by the helper above; re-import is a no-op

    tools = sorted(mcp_server.mcp._tool_manager.list_tools(), key=lambda t: t.name)
    descriptions = {t.name: (t.description or "") for t in tools}
    from app.descriptions import DESCRIPTIONS

    print("-- A. every registered tool has a description, and it is descriptions.py's --")
    for name, desc in descriptions.items():
        check(f"{name} has a non-empty description", bool(desc.strip()))
        check(f"{name} sends descriptions.py's text", desc == DESCRIPTIONS.get(name))
    stale = sorted(set(DESCRIPTIONS) - set(descriptions))
    check("descriptions.py names no tool that is not registered", not stale, stale)
    check("atlas_delete registered (ATLAS_MCP_ENABLE_DELETE alone, no network call)",
          "atlas_delete" in descriptions, sorted(descriptions))

    print("\n-- B. per-tool size table --")
    total = 0
    for name, desc in sorted(descriptions.items(), key=lambda kv: -len(kv[1])):
        n = len(desc)
        total += n
        print(f"  {n:5d}  {name}")
    print(f"  {'-----':>5}")
    print(f"  {total:5d}  TOTAL ({len(descriptions)} tools)")

    print("\n-- C. the real budget holds --")
    problems = budget_violations(descriptions)
    check(f"every description <= {DESCRIPTION_MAX_CHARS} chars",
          not any("chars >" in p and not p.startswith("TOTAL") for p in problems),
          "; ".join(p for p in problems if "chars >" in p and not p.startswith("TOTAL")))
    check(f"total across every tool <= {TOTAL_MAX_CHARS} chars",
          total <= TOTAL_MAX_CHARS, f"{total} chars")
    check("no description carries source indentation (sent verbatim; Python < 3.13 keeps it)",
          not any("source indentation" in p for p in problems),
          "; ".join(p for p in problems if "source indentation" in p))
    check("no description contains a ticket-id pattern (TASK-\\d+/STORY-/EPIC-/ADR \\d+)",
          not any("ticket-id pattern" in p for p in problems),
          "; ".join(p for p in problems if "ticket-id pattern" in p))

    print("\n-- D. the budget function can actually fail (proof, not assertion) --")
    synthetic_ok = {"fine_tool": "a short, caller-facing description."}
    synthetic_bad = {
        "over_budget_tool": "x" * (DESCRIPTION_MAX_CHARS + 1),
        "ticket_tool": "do the thing, see TASK-9999 for background.",
        "indented_tool": "First line.\n\n        Indented the way a 3.12 docstring arrives.",
    }
    check("a clean synthetic set has no violations", budget_violations(synthetic_ok) == [])
    bad_problems = budget_violations(synthetic_bad)
    check(f"a {DESCRIPTION_MAX_CHARS + 1}-char synthetic description is flagged",
          any("over_budget_tool" in p and "chars >" in p for p in bad_problems), str(bad_problems))
    check("a synthetic TASK- reference is flagged",
          any("ticket_tool" in p and "ticket-id pattern" in p for p in bad_problems), str(bad_problems))
    check("a synthetic 3.12-style indented docstring is flagged",
          any("indented_tool" in p and "source indentation" in p for p in bad_problems), str(bad_problems))

    print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'ALL PASSED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
