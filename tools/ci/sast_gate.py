#!/usr/bin/env python3
"""The SAST + secrets gate: only NEW high findings fail (STORY-atlas-lcm-plan, item 5).

Reads the JSON reports of Semgrep (Python + JavaScript rules), Bandit (Python) and gitleaks
(credentials in git history), turns each finding into a stable fingerprint, and compares the
high ones with the triaged baseline tools/ci/security-baseline.json:

  * a HIGH finding whose fingerprint is not in the baseline FAILS the run;
  * on a pull request (`--changed-files`), a new high finding in a file the PR does not touch
    is a WARNING instead: the code is old and the RULE is new (Semgrep's registry rules
    change under us), so it is triaged into the baseline, not blamed on the PR;
  * medium and low findings are listed in the summary and never fail;
  * a baseline entry nothing matches any more is reported as fixed (delete it).

SEVERITY. Semgrep ERROR = high (WARNING medium, INFO low). Bandit severity HIGH with
confidence MEDIUM or HIGH = high. Every gitleaks finding is high: a credential has no
"medium".

FINGERPRINTS survive line moves: tool | rule | path | the finding's first source line with
whitespace removed, sha256, 16 hex. Two identical lines in one file share a fingerprint and are
counted. gitleaks findings use gitleaks' own fingerprint (commit:file:rule:line), which never
moves because commits do not. No secret value is read, written or printed here: gitleaks runs
with --redact and only its RuleID/File/Commit/StartLine fields are used.

  python tools/ci/sast_gate.py --semgrep S.json --bandit B.json --gitleaks G.json \\
      [--changed-files FILE] [--report OUT.json] [--write-baseline]
  python tools/ci/sast_gate.py --self-test

Exit 0 pass, 1 new high finding, 2 usage / unreadable report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "tools" / "ci" / "security-baseline.json"


def _rel(path: str) -> str:
    p = path.replace("\\", "/")
    root = str(ROOT).replace("\\", "/").rstrip("/") + "/"
    if p.startswith(root):
        p = p[len(root):]
    return p[2:] if p.startswith("./") else p


def _line(path: str, n: int, root: Path) -> str:
    try:
        lines = (root / path).read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[n - 1] if 0 < n <= len(lines) else ""
    except OSError:
        return ""


def fingerprint(tool: str, rule: str, path: str, text: str) -> str:
    raw = "|".join((tool, rule, path, re.sub(r"\s+", "", text)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def from_semgrep(data: dict, root: Path) -> list[dict]:
    sev = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}
    out = []
    for r in data.get("results", []):
        path, line = _rel(r["path"]), r["start"]["line"]
        rule = r["check_id"]
        out.append({"tool": "semgrep", "rule": rule, "path": path, "line": line,
                    "severity": sev.get(r.get("extra", {}).get("severity", ""), "low"),
                    "message": (r.get("extra", {}).get("message") or "")[:200],
                    "fingerprint": fingerprint("semgrep", rule, path, _line(path, line, root))})
    return out


def from_bandit(data: dict, root: Path) -> list[dict]:
    out = []
    for r in data.get("results", []):
        path, line = _rel(r["filename"]), r["line_number"]
        s, c = r.get("issue_severity", "LOW"), r.get("issue_confidence", "LOW")
        if s == "HIGH":
            severity = "high" if c in ("MEDIUM", "HIGH") else "medium"
        else:
            severity = s.lower()
        rule = f"{r['test_id']}:{r['test_name']}"
        out.append({"tool": "bandit", "rule": rule, "path": path, "line": line,
                    "severity": severity, "message": (r.get("issue_text") or "")[:200],
                    "fingerprint": fingerprint("bandit", rule, path, _line(path, line, root))})
    return out


def from_gitleaks(data: list, root: Path) -> list[dict]:
    out = []
    for r in data or []:
        # Only these fields: Secret / Match are redacted anyway and never copied.
        path, rule, commit = _rel(r.get("File", "")), r.get("RuleID", ""), r.get("Commit", "")
        out.append({"tool": "gitleaks", "rule": rule, "path": path,
                    "line": r.get("StartLine", 0), "severity": "high",
                    "message": f"credential-shaped string ({rule}) in commit {commit[:12]}",
                    "fingerprint": f"{commit}:{path}:{rule}:{r.get('StartLine', 0)}"})
    return out


def evaluate(findings: list[dict], baseline: dict, changed: set[str] | None):
    known = Counter(e["fingerprint"] for e in baseline.get("findings", []))
    seen: Counter = Counter()
    new_fail, new_warn, accepted = [], [], []
    for f in (f for f in findings if f["severity"] == "high"):
        seen[f["fingerprint"]] += 1
        if seen[f["fingerprint"]] <= known[f["fingerprint"]]:
            accepted.append(f)
        elif changed is not None and f["tool"] != "gitleaks" and f["path"] not in changed:
            new_warn.append(f)
        else:
            new_fail.append(f)
    # On a pull request gitleaks scans the PR's commits only, so a history entry it did not see
    # is not "fixed": only a whole-history run can say that.
    fixed = [e for e in baseline.get("findings", []) if seen[e["fingerprint"]] == 0
             and not (changed is not None and e.get("tool") == "gitleaks")]
    return new_fail, new_warn, accepted, fixed


def summary(findings, new_fail, new_warn, accepted, fixed) -> str:
    by = Counter((f["tool"], f["severity"]) for f in findings)
    out = ["## SAST + secrets", "",
           "| tool | high | medium | low |", "|---|---|---|---|"]
    for tool in ("semgrep", "bandit", "gitleaks"):
        out.append(f"| {tool} | {by[(tool, 'high')]} | {by[(tool, 'medium')]} | {by[(tool, 'low')]} |")
    out += ["", f"High findings: **{len(new_fail)} new (failing)**, {len(new_warn)} new in files "
            f"this PR does not touch (warning), {len(accepted)} in the triaged baseline, "
            f"{len(fixed)} baseline entries no longer found."]

    def rows(title, items):
        if items:
            out.extend(["", f"### {title}", "", "| tool | rule | where | fingerprint |", "|---|---|---|---|"])
            out.extend(f"| {f['tool']} | `{f['rule']}` | `{f['path']}:{f['line']}` | `{f['fingerprint']}` |"
                       for f in items)

    rows("New high findings (fail)", new_fail)
    rows("New high findings in untouched files (triage into the baseline)", new_warn)
    rows("Baseline entries no longer found (delete them)", fixed)
    mediums = [f for f in findings if f["severity"] == "medium"]
    rows("Medium findings (report only)", mediums[:50])
    return "\n".join(out) + "\n"


def write_baseline(findings, old: dict) -> None:
    triage = {e["fingerprint"]: e.get("triage", "") for e in old.get("findings", [])}
    highs = sorted((f for f in findings if f["severity"] == "high"),
                   key=lambda f: (f["tool"], f["path"], f["line"]))
    doc = {"//": old.get("//", "Triaged high findings of tools/ci/sast_gate.py; only findings "
                               "NOT listed here fail the SAST job."),
           "findings": [{k: f[k] for k in ("tool", "rule", "path", "line", "fingerprint")}
                        | {"triage": triage.get(f["fingerprint"], "TODO: triage")} for f in highs]}
    BASELINE.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {BASELINE.relative_to(ROOT)}: {len(highs)} high findings")


def self_test() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.py").write_text("x = 1\nhashlib.sha1(b)\n", encoding="utf-8")
        sg = {"results": [{"path": str(root / "a.py"), "start": {"line": 2}, "check_id": "r1",
                           "extra": {"severity": "ERROR", "message": "m"}},
                          {"path": "a.py", "start": {"line": 1}, "check_id": "r2",
                           "extra": {"severity": "WARNING"}}]}
        bd = {"results": [{"filename": "./b.py", "line_number": 3, "issue_severity": "HIGH",
                           "issue_confidence": "LOW", "test_id": "B1", "test_name": "t"}]}
        gl = [{"RuleID": "generic-api-key", "File": "c.py", "StartLine": 4, "Commit": "abc",
               "Secret": "REDACTED", "Match": "REDACTED"}]
        global ROOT
        ROOT = root
        fs = from_semgrep(sg, root) + from_bandit(bd, root) + from_gitleaks(gl, root)
        fp1 = fs[0]["fingerprint"]
        (root / "a.py").write_text("\n\nx = 1\n  hashlib.sha1(b)\n", encoding="utf-8")
        moved = from_semgrep({"results": [dict(sg["results"][0], start={"line": 4})]}, root)
        ok = moved[0]["fingerprint"] == fp1 and fs[0]["path"] == "a.py"
        ok = ok and [f["severity"] for f in fs] == ["high", "medium", "medium", "high"]
        ok = ok and "Secret" not in json.dumps(fs) and "REDACTED" not in json.dumps(fs)
        base = {"findings": [{"fingerprint": fp1}, {"fingerprint": "gone"}]}
        nf, nw, acc, fixed = evaluate(fs, base, None)
        ok = ok and len(acc) == 1 and len(nf) == 1 and nf[0]["tool"] == "gitleaks" and len(fixed) == 1
        nf2, nw2, _, _ = evaluate(fs + [dict(fs[0])], base, {"other.py"})
        ok = ok and len(nw2) == 1 and len(nf2) == 1  # duplicate counted; gitleaks never downgraded
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--semgrep")
    ap.add_argument("--bandit")
    ap.add_argument("--gitleaks", action="append", default=[])
    ap.add_argument("--changed-files")
    ap.add_argument("--report")
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    findings: list[dict] = []
    try:
        if a.semgrep:
            findings += from_semgrep(json.loads(Path(a.semgrep).read_text(encoding="utf-8")), ROOT)
        if a.bandit:
            findings += from_bandit(json.loads(Path(a.bandit).read_text(encoding="utf-8")), ROOT)
        for g in a.gitleaks:
            text = Path(g).read_text(encoding="utf-8").strip()
            findings += from_gitleaks(json.loads(text) if text else [], ROOT)
    except (OSError, ValueError, KeyError) as exc:
        print(f"::error::cannot read a scanner report: {exc}")
        return 2
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    if a.write_baseline:
        write_baseline(findings, baseline)
        return 0
    changed = None
    if a.changed_files:
        changed = {_rel(x) for x in Path(a.changed_files).read_text(encoding="utf-8").split() if x}
    new_fail, new_warn, accepted, fixed = evaluate(findings, baseline, changed)
    text = summary(findings, new_fail, new_warn, accepted, fixed)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text)
    if a.report:
        Path(a.report).write_text(json.dumps({"findings": findings, "new_failing": new_fail,
                                              "new_warning": new_warn, "fixed": fixed}, indent=2),
                                  encoding="utf-8")
    for f in new_warn:
        print(f"::warning file={f['path']},line={f['line']}::{f['tool']} {f['rule']} (new rule on old code: triage into tools/ci/security-baseline.json)")
    for f in new_fail:
        print(f"::error file={f['path']},line={f['line']}::{f['tool']} {f['rule']}: {f['message']}")
    return 1 if new_fail else 0


if __name__ == "__main__":
    sys.exit(main())
