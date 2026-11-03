"""
Verification for the public root files kept as overlays in `tools/carve/overlay/`.

WHY THIS EXISTS. The export that builds the public repository copies `tools/carve/overlay/<p>` to
`<p>`, so the overlay `AGENTS.md` and `CLAUDE.md` become that repository's briefing files and are
judged there by `verify_docs.py`. Nothing in this tree runs `verify_docs.py` over them: here it reads
this tree's own root files. An overlay that breaks a budget or a rule would therefore surface only as
a red first commit in the public repository. This script places the overlays at the root of a
temporary tree laid out like the export and runs the real `verify_docs.py` there, so the same rules
are measured before the export ever runs.

`verify_docs.py` takes its root from its own location (`ROOT = <file>/../..`), so a copy of it under
`<tmp>/backend/` measures `<tmp>`. The copy is the unmodified file, so a rule added to it later is
applied to the overlays without a change here.

WHAT IT CHECKS.
  A. The temporary tree: the overlay `AGENTS.md`/`CLAUDE.md` at its root, `backend/verify_docs.py`,
     and every restatement peer `verify_docs.py` names that the export ships (the include set is
     `tools/ci/check_export_links.py`'s). `verify_docs.py` exits 0 there, and actually measured both
     files (no SKIP line, a budget PASS line for each).
  B. The harness can fail: the same tree with a workitem id appended to `AGENTS.md` exits non-zero.
     Without this, a tree laid out wrongly would pass by measuring nothing.
  C. Every relative link in the overlay Markdown files resolves inside the export
     (`check_export_links.check`, with the overlay directory placed at the root).
  D. Content rules from the public-repository decisions: no `Signed-off-by`/DCO requirement in
     either CONTRIBUTING file; both SECURITY files send reports to "Report a vulnerability" and name
     no mailbox; no domain outside a short generic allow-list, and no URL owner other than
     carve.py's owner placeholder.

In the exported tree `tools/carve/overlay/` does not exist (its files ARE the root files, and
`verify_docs.py` judges them directly), so this script says SKIP and exits 0 there.

No database. Run:
    python verify_overlay_docs.py
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ROOT / "tools" / "carve" / "overlay"
OVERLAY_REL = "tools/carve/overlay"
MARKDOWN = ("AGENTS.md", "CLAUDE.md", "README.md", "CONTRIBUTING.md", "SECURITY.md")

#: Domains a public root file may name, with their subdomains. Anything else is a hostname that
#: would need a reason.
ALLOWED_DOMAINS = {"example.com", "example.test", "github.com", "apache.org"}
DOMAIN = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:com|net|org|io|se|dev|app|ai|test))\b", re.I)
GITHUB_OWNER = re.compile(r"github\.com/([^/\s)]+)/")
#: carve.py's placeholder, split so the export's --owner substitution leaves this file alone.
OWNER_PLACEHOLDER = "<owner-" + "account>"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("[PASS] " if ok else "[FAIL] ") + label + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def build_tree(dest: Path, peers: list[str]) -> None:
    (dest / "backend").mkdir(parents=True)
    shutil.copy2(ROOT / "backend" / "verify_docs.py", dest / "backend" / "verify_docs.py")
    for name in ("AGENTS.md", "CLAUDE.md"):
        shutil.copy2(OVERLAY / name, dest / name)
    for rel in peers:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dest / rel)


def run_verify_docs(tree: Path) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, "verify_docs.py"], cwd=tree / "backend",
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> None:
    print("verify_overlay_docs -- the public root files against the rules they ship under")
    print("=" * 70)
    if not OVERLAY.is_dir():
        print(f"SKIP: {OVERLAY_REL}/ is not in this tree (the exported tree has the files at its root)")
        return

    sys.path.insert(0, str(ROOT / "backend"))
    sys.path.insert(0, str(ROOT / "tools" / "ci"))
    import check_export_links as links  # noqa: E402
    import verify_docs  # noqa: E402  (main() is guarded; importing runs no check)

    export = links.IncludeSet(links.DEFAULT_INCLUDE, links.DEFAULT_EXCLUDE)
    for name in MARKDOWN:
        check(f"overlay {name} exists", (OVERLAY / name).is_file())
    if failures:
        sys.exit(1)

    # A. the overlays at the root of an export-shaped tree, judged by the real verify_docs.py
    peers = sorted({p.relative_to(ROOT).as_posix()
                    for pattern in verify_docs.RESTATEMENT_PEERS for p in ROOT.glob(pattern)})
    shipped = [p for p in peers if export.has_file(p)]
    print(f"restatement peers shipped by the export: {shipped}")
    with tempfile.TemporaryDirectory() as d:
        tree = Path(d) / "repo"
        build_tree(tree, shipped)
        rc, out = run_verify_docs(tree)
        check("verify_docs.py passes with the overlays at a repo root", rc == 0,
              "" if rc == 0 else "\n" + out)
        check("verify_docs.py measured both files (no SKIP)", "SKIP" not in out)
        for name in verify_docs.BUDGETS:
            check(f"{name} budget was measured and met",
                  re.search(rf"^\[PASS\] {re.escape(name)} is within its \d+-line budget", out, re.M)
                  is not None)
        for peer in shipped:
            check(f"AGENTS.md compared with {peer}", f"AGENTS.md does not restate {peer}" in out)

        # B. the same tree with a rule broken must fail, or A proved nothing
        with (tree / "AGENTS.md").open("a", encoding="utf-8") as f:
            f.write("\nSee " + "TASK" + "-1 for the history.\n")
        rc_broken, _ = run_verify_docs(tree)
        check("a broken overlay copy fails verify_docs.py (the harness can fail)", rc_broken != 0)

    # C. relative links resolve inside the export, as if the overlay sat at the root
    _, link_failures = links.check(ROOT, [OVERLAY_REL], export, {OVERLAY_REL: "."})
    check("every relative link in the overlays resolves inside the export", not link_failures,
          "; ".join(link_failures[:5]))

    # D. the content decisions
    texts = {name: (OVERLAY / name).read_text(encoding="utf-8") for name in MARKDOWN}
    oss_contributing = (ROOT / "docs/oss/CONTRIBUTING.md").read_text(encoding="utf-8")
    oss_security = (ROOT / "docs/oss/SECURITY.md").read_text(encoding="utf-8")
    for label, text in (("CONTRIBUTING.md", texts["CONTRIBUTING.md"]),
                        ("docs/oss/CONTRIBUTING.md", oss_contributing)):
        check(f"{label} asks for no sign-off",
              re.search(r"Signed-off-by|Certificate of Origin|\bDCO\b|git commit -s", text) is None)
    check("CONTRIBUTING.md links the development guide", "docs/oss/CONTRIBUTING.md" in texts["CONTRIBUTING.md"])
    check("CONTRIBUTING.md names Apache-2.0", "Apache" in texts["CONTRIBUTING.md"])
    for label, text in (("SECURITY.md", texts["SECURITY.md"]), ("docs/oss/SECURITY.md", oss_security)):
        check(f"{label} sends reports to 'Report a vulnerability'", "Report a vulnerability" in text)
        mail = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
        check(f"{label} names no mailbox", not mail, str(mail[:3]))
    for name, text in texts.items():
        domains = {d for d in (m.lower() for m in DOMAIN.findall(text))
                   if not any(d == a or d.endswith("." + a) for a in ALLOWED_DOMAINS)}
        check(f"{name} names no domain outside the allow-list", not domains, str(sorted(domains)))
        owners = set(GITHUB_OWNER.findall(text)) - {OWNER_PLACEHOLDER}
        check(f"{name} names no repository owner but the placeholder", not owners, str(sorted(owners)))

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
