"""
Verification for the `gate-integrity` job in `.github/workflows/verify.yml` —
RAIL 2's CI half, narrowed on 2026-08-24 to refuse DELETION of the gate rather
than any edit to it.

WHY THIS EXISTS AT ALL. That job is shell embedded in YAML, and nothing tested
it. It shipped, ran on nothing for weeks, then judged three pull requests in a
row and failed all three — every one of them legitimate work. The narrowing is
a behaviour change to a gate, which is exactly the kind of change that must not
rest on somebody having read it carefully once.

IT TESTS THE SHIPPED TEXT, NOT A COPY. The step's script is lifted verbatim out
of the workflow file and run against a scratch git repository. A paraphrase here
would drift from the YAML and then prove nothing about what CI actually does —
which is the same failure mode as a comment that misstates its code.

THE FOUR CASES ARE THE WHOLE POINT, and they are what separates the narrowed
gate from both the old one and from no gate at all:

    A  loop MODIFIES a verify script       PASS   (the old job failed this,
                                                   three times, wrongly)
    B  loop DELETES a verify script        REFUSE
    C  HUMAN deletes a verify script       PASS   (this is what makes the
                                                   trailer check load-bearing)
    D  loop MOVES one out of the suite     REFUSE (`--no-renames`; a move to
                                                   notes/ is a deletion wearing
                                                   a hat)

A and B together prove the narrowing happened. B and C together prove the job
still distinguishes the loop from the operator. D proves the rename hole is
shut. Remove any one of those and one of these checks goes red, which is the
property the project asks for: a check that cannot fail proves nothing.

Run:
    python verify_gate_integrity.py
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "verify.yml"
TRAILER = "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  - {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def extract_step() -> str:
    """Lift the job's `run:` block out of the workflow, verbatim.

    The only substitution is the GitHub expression for the commit range, which
    has no value outside Actions. Everything the job actually decides with —
    the path pattern, the diff filters, the trailer match — is the shipped text.
    """
    y = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(r"- name: no loop-authored DELETION.*?\n        run: \|\n(.*?)\n\n",
                  y, re.S)
    if m is None:
        raise SystemExit(
            "FATAL: could not find the gate step in .github/workflows/verify.yml.\n"
            "If the step was renamed, this script must be updated with it - a "
            "silently-unfound step would make every check below vacuously pass."
        )
    body = "\n".join(line[10:] for line in m.group(1).splitlines())
    return body.replace(
        'range="${{ github.event.pull_request.base.sha }}..${{ github.sha }}"',
        'range="$1"',
    )


class Repo:
    """A scratch git repository, so no case can touch the real one.

    Learned the hard way about an hour before this file existed: running these
    cases on a live branch let a `git commit -am` sweep up an uncommitted edit,
    which then went away with the throwaway branch.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "selftest@example.invalid")
        self.git("config", "user.name", "selftest")
        (path / "backend").mkdir()
        (path / ".github" / "workflows").mkdir(parents=True)
        (path / "notes").mkdir()
        (path / "backend" / "verify_thing.py").write_text("# a check\n", encoding="utf-8")
        (path / ".github" / "workflows" / "verify.yml").write_text("name: verify\n",
                                                                   encoding="utf-8")
        (path / "README.md").write_text("ordinary file\n", encoding="utf-8")
        self.git("add", "-A")
        self.commit("base")

    def git(self, *args) -> str:
        out = subprocess.run(["git"] + list(args), cwd=self.path,
                             capture_output=True, text=True)
        if out.returncode != 0 and args[0] not in ("rev-parse",):
            raise SystemExit(f"FATAL: git {' '.join(args)} failed: {out.stderr.strip()}")
        return out.stdout.strip()

    def commit(self, subject: str, loop: bool = False) -> str:
        msg = subject + (f"\n\n{TRAILER}\n" if loop else "\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")


def run_gate(script: str, cwd: Path, rng: str) -> tuple[int, str]:
    bash = shutil.which("bash")
    if not bash:
        raise SystemExit(
            "FATAL: no bash on PATH. The gate step is a shell script and this "
            "check runs it for real; there is deliberately no skip path, because "
            "a suite that reports success when it could not run the thing is "
            "worse than one that will not start."
        )
    p = subprocess.run([bash, "-c", script, "gate", rng], cwd=cwd,
                       capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    check("the workflow exists", WORKFLOW.is_file(), str(WORKFLOW))
    script = extract_step()
    check("the gate step was lifted from the workflow, not reimplemented here",
          "diff-filter=D" in script and "Co-Authored-By" in script,
          f"{len(script.splitlines())} lines")

    tmp = Path(tempfile.mkdtemp(prefix="atlas-gate-verify-"))
    try:
        print("\n== A. a loop-authored MODIFICATION passes ==")
        # The narrowing, stated as a test. The old job refused this, and did so
        # on three consecutive pull requests that were all legitimate.
        r = Repo(tmp / "a")
        base = r.head()
        (r.path / "backend" / "verify_thing.py").write_text("# a check\n# better\n",
                                                            encoding="utf-8")
        head = r.commit("improve a check", loop=True)
        rc, out = run_gate(script, r.path, f"{base}..{head}")
        check("modifying a verify script is allowed", rc == 0, f"rc={rc}")
        check("...and is still reported, so it stays visible in the log",
              "verify_thing.py" in out and "note" in out, out.strip()[-80:])

        print("\n== B. a loop-authored DELETION is refused ==")
        r2 = Repo(tmp / "b")
        base2 = r2.head()
        r2.git("rm", "-q", "backend/verify_thing.py")
        head2 = r2.commit("remove a check", loop=True)
        rc2, out2 = run_gate(script, r2.path, f"{base2}..{head2}")
        check("deleting a verify script is refused", rc2 == 1, f"rc={rc2}")
        check("and the refusal names the file", "verify_thing.py" in out2)
        check("deleting the workflow itself is caught by the same pattern",
              "backend/verify_" in script and ".github/" in script)

        print("\n== C. a HUMAN deletion passes, which is what makes B mean anything ==")
        # If this failed, the job would be refusing deletions rather than
        # refusing THE LOOP's deletions, and the trailer check would be dead
        # weight rather than the thing doing the work.
        r3 = Repo(tmp / "c")
        base3 = r3.head()
        r3.git("rm", "-q", "backend/verify_thing.py")
        head3 = r3.commit("operator removes a check")   # no trailer
        rc3, out3 = run_gate(script, r3.path, f"{base3}..{head3}")
        check("a human may delete a gate file", rc3 == 0, f"rc={rc3}")
        check("and it is logged as human-authored rather than silently ignored",
              "human-authored" in out3, out3.strip()[-70:])

        print("\n== D. moving a verify script OUT of the suite is a deletion ==")
        # `--no-renames` in the step. With rename detection on, this reads as a
        # rename and sails through - deletion from the suite wearing a hat.
        r4 = Repo(tmp / "d")
        base4 = r4.head()
        r4.git("mv", "backend/verify_thing.py", "notes/thing.py")
        head4 = r4.commit("tidy a file away", loop=True)
        rc4, out4 = run_gate(script, r4.path, f"{base4}..{head4}")
        check("moving it out of backend/ is refused", rc4 == 1, f"rc={rc4}")
        check("--no-renames is what does that",
              "--no-renames" in script)

        print("\n== E. an ordinary change touches none of this ==")
        r5 = Repo(tmp / "e")
        base5 = r5.head()
        (r5.path / "README.md").write_text("edited\n", encoding="utf-8")
        head5 = r5.commit("ordinary work", loop=True)
        rc5, out5 = run_gate(script, r5.path, f"{base5}..{head5}")
        check("a normal loop commit is silent and passes", rc5 == 0, f"rc={rc5}")
        check("...and says nothing about files it does not guard",
              "README" not in out5, out5.strip()[-60:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): " + "; ".join(FAILURES))
        return 1
    print("verify_gate_integrity: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
