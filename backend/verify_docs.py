"""
Verification for the two briefing files -- `AGENTS.md` and `CLAUDE.md` -- against
the rules they state about themselves.

WHY THIS EXISTS. Between 2026-07-31 and 2026-08-25 those two files grew from 297
lines to 1,296, and across the forty most recent commits touching them only four
reduced a file's length -- by 29 lines against 1,078 added. They are not
maintained; they are appended to. Every session that learns something writes it
down and nothing has ever had the job of taking something out. The restructure
that shrank them is a one-time act; without a check, the growth resumes the same
week, because the cost of an addition falls on every FUTURE session and never on
the one making it.

So this turns three of the habits that produced the bloat into a red run:

  A. NO SUPERSESSION MARKERS. A `~~strikethrough~~` or a `Shipped 20xx` heading
     means the file is being used as a changelog. A superseded fact belongs in
     git, in the ADR that replaced it, or in the code comment beside the thing
     it describes -- not kept in the always-loaded payload with a line through
     it. The specific hazard is not length: it is that a file arguing with
     itself gives a reader two rules and no way to choose, and one bullet in
     CLAUDE.md contradicted itself fourteen lines apart for six days.

  B. NO WORKITEM IDS. `TASK-`/`STORY-`/`EPIC-` in a briefing is a citation to
     something with a status the board already knows and this file cannot. The
     board outranks the prose the moment they disagree, and they disagree
     silently. Ids stay legal in commits, ADRs and workitem prose -- this rule
     is about the two files that load before anyone asks for them.

  C. A LINE BUDGET, checked (the numbers are in BUDGETS below). A budget nobody measures is a wish; measured, it forces the
     removal decision at the moment of an addition, which is the only moment
     anyone is looking at the file.

     Those numbers were MEASURED on 2026-08-28, having been aspirational
     before it. AGENTS.md's old 250 was missed by roughly 2x across three
     compression attempts, and the check was downgraded to a warning whose
     own text said the work was "not finished" -- so a budget nobody
     intended to hit taught everyone to ignore the checker, which is the
     failure mode this script exists to prevent. The fix was to relocate the
     directory-specific detail into `backend/CLAUDE.md` and
     `frontend/CLAUDE.md` (which cost nothing until those directories are
     opened) and then to set the budget to what the remaining, genuinely
     always-needed content actually measures, plus a little headroom. It is
     a FAILURE again, because a target that is met is allowed to be one.

     Lowered again on 2026-09-15 (420 -> 300, 110 -> 100), when ADR 0040 made
     the files a ROUTER: bootstrap facts plus pointers to the node that
     describes each part, measured at 269 and 87 lines.

     Lowered a third time the same day (300 -> 90, 100 -> 25), when the
     gotchas and standing decisions left too: each one was either already
     enforced by code or a check, made into one (token add, check-deployed.sh,
     the CI `compose` job), or moved to the Atlas node it concerns. Measured
     at 80 and 16 lines. The numbers now live ONLY here -- the files point at
     this script rather than stating a number that could disagree with it.

  D. NO RESTATEMENT. A budget alone fails on length and passes a file that
     spends its lines copying something an agent loads anyway. So neither
     briefing file may share a run of RESTATEMENT_WORDS consecutive words
     with the other, or with a file that sits in the same context the moment
     it matters: a nested `CLAUDE.md`, a skill, `backend/TESTING.md`,
     `mcp/README.md`, the ADR index. A router names where a thing is written;
     it does not say it twice. On 2026-09-15 this found `verify-board`
     carrying AGENTS.md's done check verbatim.

     The run length is set where it catches a copied sentence and not a
     shared idiom: at 8 words, "a stale overview is what an invitee reads"
     would have tripped; at 12, nothing in the tree trips. Node prose is NOT
     compared, because this script touches no database -- the retrieval
     skill's write-side rules cover that half.

     Procedures and bindings under `agent/` are excluded on purpose: they are
     handed to runtimes that do not load AGENTS.md, so a copied trap there is
     the point rather than a restatement.

WHAT IT DELIBERATELY DOES NOT DO. It does not judge content, count words, or
check that a claim is true -- nothing can, and a check that pretends to would be
worse than none. It reads two files and the peers they must not copy, and applies four
mechanical rules. It also does not touch a database, which makes it the one script in the suite that needs
no Postgres.

ALLOWANCES ARE EXPLICIT AND NARROW. `ALLOWED_IDS` exists because a rule
sometimes IS about one specific piece of work; adding to it is a visible diff
and a decision, which is the property the old habit lacked.

Run:
    python verify_docs.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: file -> maximum lines. The ONLY place these numbers are written; raising one
#: is a decision, so say why in the commit -- and first ask which Atlas node the
#: addition belongs on instead (ADR 0040).
BUDGETS = {
    "AGENTS.md": 90,
    "CLAUDE.md": 25,
}

#: D. Files an agent has in context alongside the briefing files at the moment
#: they are relevant. Globs, relative to the repo root.
RESTATEMENT_PEERS = (
    "backend/CLAUDE.md",
    "frontend/CLAUDE.md",
    "backend/TESTING.md",
    "mcp/README.md",
    "docs/adr/README.md",
    ".claude/skills/*/SKILL.md",
)
RESTATEMENT_WORDS = 12

#: Workitem ids that earn their place, with the reason. Near-empty is the
#: intended steady state. An entry here is a claim that the rule genuinely
#: cannot be stated without naming the work -- rare, and reviewable because it
#: is a diff.
ALLOWED_IDS: dict[str, str] = {
    "TASK-985": "CLAUDE.md's example of how to name work to the owner -- the rule is about writing an id",
}

SUPERSESSION_PATTERNS = [
    (re.compile(r"~~[^~]+~~"), "struck-through text kept for the record"),
    (re.compile(r"(?mi)^#+\s*Shipped\s+20\d\d"), "'Shipped 20xx' changelog heading"),
]

ID_PATTERN = re.compile(r"\b(?:TASK|STORY|EPIC)-[A-Za-z0-9-]+")

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("[PASS] " if ok else "[FAIL] ") + label + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def strip_code_fences(text: str) -> str:
    """Fenced blocks are quoted material -- a shell transcript may legitimately
    contain anything. Only prose is judged."""
    out, in_fence = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def word_runs(text: str, n: int = RESTATEMENT_WORDS) -> set[str]:
    """Every run of `n` consecutive words in the prose, normalised so that
    re-wrapping, bold markers and backticks do not hide a copy."""
    words = re.findall(r"[a-z0-9_./-]+", strip_code_fences(text).lower())
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def restatements(a: str, b: str) -> list[str]:
    return sorted(word_runs(a) & word_runs(b))


def main() -> None:
    print("verify_docs -- the briefing files against their own rules")
    print("=" * 70)

    for name, budget in BUDGETS.items():
        path = ROOT / name
        if not path.exists():
            # A tree without the briefing files (a checkout that ships its own or none) has
            # nothing for these rules to measure; say so rather than fail on a file it never had.
            print(f"SKIP {name}'s rules: {name} is not in this tree")
            continue
        check(f"{name} exists", True, str(path))

        raw = path.read_text(encoding="utf-8")
        lines = raw.count("\n") + (0 if raw.endswith("\n") else 1)
        prose = strip_code_fences(raw)

        # C. the budget. A FAILURE since 2026-08-28, when the numbers stopped
        # being aspirations and became measurements (see the module docstring).
        #
        # It was a warning for as long as the target was unreachable, because a
        # signal that is always red is one people learn to ignore -- the exact
        # reasoning that deleted the `ingest_*` fields from /api/health. That
        # argument cuts the other way now: a budget the files MEET, left as a
        # warning, is a signal that can never say anything, and the growth these
        # rules exist to stop resumes without a single red run.
        #
        # Raising a number here is still the wrong first move. The first move is
        # the one that produced the headroom: relocate what only fires inside a
        # directory into that directory's CLAUDE.md, which costs a session
        # nothing until it opens the directory.
        check(f"{name} is within its {budget}-line budget",
              lines <= budget, f"{lines} lines")

        # A. supersession markers
        for pattern, what in SUPERSESSION_PATTERNS:
            hits = pattern.findall(prose)
            check(f"{name} carries no {what}",
                  not hits, f"{len(hits)} found: {hits[:3]}")

        # B. workitem ids
        ids = [i for i in ID_PATTERN.findall(prose) if i not in ALLOWED_IDS]
        check(f"{name} cites no workitem id",
              not ids, f"{len(ids)} found: {sorted(set(ids))[:5]}")

    # D. no restatement -- between the two briefing files, and between each of
    # them and every peer that is in context when it matters.
    briefing = {n: (ROOT / n).read_text(encoding="utf-8")
                for n in BUDGETS if (ROOT / n).exists()}
    pairs = [("AGENTS.md", "CLAUDE.md")] if len(briefing) == 2 else []
    peers = sorted({p.relative_to(ROOT).as_posix()
                    for pattern in RESTATEMENT_PEERS for p in ROOT.glob(pattern)})
    pairs += [(b, p) for b in briefing for p in peers]
    for a, b in pairs:
        text_a = briefing[a]
        text_b = briefing.get(b) or (ROOT / b).read_text(encoding="utf-8")
        hits = restatements(text_a, text_b)
        check(f"{a} does not restate {b}", not hits,
              f"{len(hits)} shared {RESTATEMENT_WORDS}-word run(s), e.g. {hits[:1]}")

    # The nested files are exempt from the budget on purpose -- they cost
    # nothing until the directory is opened, which is the whole reason they
    # exist -- but the same two content rules apply, for the same reason.
    for nested in ("frontend/CLAUDE.md", "backend/CLAUDE.md"):
        path = ROOT / nested
        if not path.exists():
            continue
        prose = strip_code_fences(path.read_text(encoding="utf-8"))
        ids = [i for i in ID_PATTERN.findall(prose) if i not in ALLOWED_IDS]
        # A nested file may name the work that introduced a mechanism it
        # documents, so this is a warning line rather than a failure.
        if ids:
            print(f"[note]  {nested} names {len(ids)} workitem id(s): "
                  f"{sorted(set(ids))[:5]}")

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
