#!/usr/bin/env python3
"""The SCA gate over Trivy's JSON reports (STORY-atlas-lcm-plan, item 4).

Every report in the directory (one per image, lock file and the vendored-JS SBOM) is read and:

  * a CRITICAL or HIGH vulnerability WITH a fixed version FAILS the run: there is something
    to do, and the patch SLA in SECURITY.md says how soon;
  * one WITHOUT a fix is reported (summary + artifact), never failing: nothing to apply yet;
  * medium and low are counted and listed in the artifact.

ACCEPTING A FINDING. tools/ci/sca-accepted.json lists {id, pkg, in, until, reason}: a fixable
high that cannot be patched today (e.g. waiting for the base image's next digest, which
Dependabot will raise). It turns that one finding into a warning UNTIL the date; after it, the
finding fails again. No open-ended exceptions.

`in` is required: the artefacts the acceptance holds for, each named as its report in
REPORT_DIR without `.json`. sbom-sca.yml: atlas-api, atlas-mcp, atlas-caddy, atlas-postgres,
atlas-postgres-pgvector, python-backend, python-mcp, vendored-js, and every pulled image as the
last path part of its repository plus `-<tag>` (cloudflare/cloudflared:2026.10.6@sha256:... is
cloudflared-2026.10.6). images.yml reports an image built from the same recipe under the same
name (its atlas-tls as atlas-caddy, its atlas-postgres as atlas-postgres-pgvector), plus
atlas-api-hardened, atlas-mcp-hardened and atlas-backup (IMAGES_YML_ONLY: a full scan never has
their reports, so naming them in `in` is not "gone"). The same CVE in an artefact not listed
still fails.

AN ACCEPTANCE THAT MATCHES NOTHING FAILS. For each artefact in its `in`:
  * the artefact's report is in REPORT_DIR but no finding there used the acceptance: FAIL, it
    is stale (fixed, or never there); remove the entry, or the artefact from `in`;
  * the report is not in REPORT_DIR: with --full-scan (sbom-sca.yml, which scans everything
    the repo ships) FAIL, the artefact is gone or renamed (an image bumped to a new tag);
    without it (images.yml scans only the OSS images) it is skipped: that run never looked.

ADVISORY (--advisory, 2026-11-10). A pull request that changes nothing the scanned artefacts
are built from (sbom-sca.yml decides, from the changed files) cannot have brought a finding in:
a new CVE against an unchanged base image or lock lands on every open pull request at once. On
such a run the failures are printed as warnings and the gate exits 0, so unrelated work is not
held up; the fix arrives as a Dependabot security pull request, which changes a dependency and
therefore meets the full gate. Master, the weekly run, a release and every pull request that
touches a dependency are never advisory, so a release cannot be cut past a fixable finding.

  python tools/ci/sca_gate.py REPORT_DIR [--full-scan] [--advisory] [--summary-md OUT.md]
  python tools/ci/sca_gate.py --self-test

Exit 0 pass (or advisory), 1 a fixable high/critical or an unused acceptance, 2 no reports.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCEPTED = ROOT / "tools" / "ci" / "sca-accepted.json"
GATE = ("CRITICAL", "HIGH")
# Built and scanned only by images.yml, never by sbom-sca.yml's --full-scan: an acceptance naming
# one of these is not "gone" just because the full scan has no report for it.
IMAGES_YML_ONLY = frozenset({"atlas-api-hardened", "atlas-mcp-hardened", "atlas-backup"})


def vulns(report: dict):
    for res in report.get("Results") or []:
        for v in res.get("Vulnerabilities") or []:
            yield res.get("Target", ""), v


def matches(acc: dict, row: dict) -> bool:
    return (acc["id"] == row["id"] and acc.get("pkg", row["pkg"]) == row["pkg"]
            and row["artifact"] in (acc.get("in") or []))


def evaluate(reports: dict[str, dict], accepted: list[dict], today: dt.date, full_scan: bool = False):
    """(fail, warn, unfixed, counts, unused). `unused` lists the acceptance problems: an entry
    with no `in`, or an (acceptance, artefact) pair that accepted no finding (see the docstring)."""
    fail, warn, unfixed, counts = [], [], [], {}
    used: set[tuple[int, str]] = set()
    for name, rep in sorted(reports.items()):
        c = Counter()
        seen = set()
        for target, v in vulns(rep):
            key = (v["VulnerabilityID"], v.get("PkgName"), v.get("InstalledVersion"), target)
            if key in seen:
                continue
            seen.add(key)
            sev = v.get("Severity", "UNKNOWN")
            fixed = (v.get("FixedVersion") or "").strip()
            c[(sev, bool(fixed))] += 1
            if sev not in GATE:
                continue
            row = {"artifact": name, "target": target, "id": v["VulnerabilityID"],
                   "pkg": v.get("PkgName", ""), "installed": v.get("InstalledVersion", ""),
                   "fixed": fixed, "severity": sev, "title": (v.get("Title") or "")[:100]}
            if not fixed:
                unfixed.append(row)
                continue
            i, acc = next(((i, a) for i, a in enumerate(accepted) if matches(a, row)), (None, None))
            if acc:
                used.add((i, name))
            if acc and dt.date.fromisoformat(acc["until"]) >= today:
                row["accepted"] = f"until {acc['until']}: {acc['reason']}"
                warn.append(row)
            else:
                if acc:
                    row["accepted"] = f"EXPIRED {acc['until']}"
                fail.append(row)
        counts[name] = c
    unused = []
    for i, a in enumerate(accepted):
        label = f"{a.get('id')} {a.get('pkg', '*')}"
        if not a.get("in"):
            unused.append({"acceptance": label, "artifact": "-",
                           "why": "no `in` list: name the artefacts it holds for"})
            continue
        for art in a["in"]:
            if (i, art) in used:
                continue
            if art in reports:
                unused.append({"acceptance": label, "artifact": art,
                               "why": "scanned, but no fixable critical/high finding there matches it: stale"})
            elif full_scan and art not in IMAGES_YML_ONLY:
                unused.append({"acceptance": label, "artifact": art,
                               "why": "no report of that name in a full scan: the artefact is gone or renamed"})
    return fail, warn, unfixed, counts, unused


def render(fail, warn, unfixed, counts, unused=()) -> str:
    out = ["## SCA (Trivy)", "",
           "| artifact | critical fix/no-fix | high fix/no-fix | medium | low |", "|---|---|---|---|---|"]
    for name, c in counts.items():
        med = c[("MEDIUM", True)] + c[("MEDIUM", False)]
        low = c[("LOW", True)] + c[("LOW", False)]
        out.append(f"| {name} | {c[('CRITICAL', True)]}/{c[('CRITICAL', False)]} | "
                   f"{c[('HIGH', True)]}/{c[('HIGH', False)]} | {med} | {low} |")

    def table(title, rows):
        if not rows:
            return
        out.extend(["", f"### {title}", "", "| artifact | id | package | installed | fixed in | severity | note |",
                    "|---|---|---|---|---|---|---|"])
        out.extend(f"| {r['artifact']} | {r['id']} | {r['pkg']} | {r['installed']} | {r['fixed'] or '-'} | "
                   f"{r['severity']} | {r.get('accepted', r['title'])} |" for r in rows)

    table("Fixable critical/high (FAIL)", fail)
    table("Fixable critical/high, accepted for now (tools/ci/sca-accepted.json)", warn)
    table("Critical/high with no fix yet (reported, not failing)", unfixed)
    if unused:
        out.extend(["", "### Acceptances that match nothing (FAIL, tools/ci/sca-accepted.json)", "",
                    "| acceptance | artifact | why |", "|---|---|---|"])
        out.extend(f"| {u['acceptance']} | {u['artifact']} | {u['why']} |" for u in unused)
    if not fail and not unused:
        out += ["", "No fixable critical or high finding outside the accepted list."]
    return "\n".join(out) + "\n"


def self_test() -> int:
    def v(cve, pkg, fixed, sev):
        return {"VulnerabilityID": cve, "PkgName": pkg, "InstalledVersion": "1", "FixedVersion": fixed, "Severity": sev}
    rep = {"Results": [{"Target": "t", "Vulnerabilities": [
        v("CVE-1", "a", "2", "HIGH"), v("CVE-1", "a", "2", "HIGH"), v("CVE-2", "b", "", "CRITICAL"),
        v("CVE-3", "c", "3", "CRITICAL"), v("CVE-4", "d", "3", "MEDIUM"), v("CVE-5", "e", "3", "HIGH")]}]}
    # The accepted CVE-3 again, in a second artefact the acceptance does not name.
    other = {"Results": [{"Target": "u", "Vulnerabilities": [v("CVE-3", "c", "3", "CRITICAL")]}]}
    acc = [{"id": "CVE-3", "pkg": "c", "in": ["img"], "until": "2026-12-01", "reason": "r"},
           {"id": "CVE-5", "pkg": "e", "in": ["img"], "until": "2026-01-01", "reason": "r"}]
    today = dt.date(2026, 11, 1)
    checks = []

    f, w, u, c, un = evaluate({"img": rep}, acc, today, full_scan=True)
    checks.append(("base", [r["id"] for r in f] == ["CVE-1", "CVE-5"] and [r["id"] for r in w] == ["CVE-3"]
                   and [r["id"] for r in u] == ["CVE-2"] and "EXPIRED" in f[1]["accepted"]
                   and c["img"][("MEDIUM", True)] == 1 and un == []))

    f, w, u, c, un = evaluate({"img": rep, "other": other}, acc, today, full_scan=True)
    checks.append(("the same CVE in an artefact not in `in` fails",
                   [(r["artifact"], r["id"]) for r in f] == [("img", "CVE-1"), ("img", "CVE-5"), ("other", "CVE-3")]
                   and [(r["artifact"], r["id"]) for r in w] == [("img", "CVE-3")] and un == []))

    stale = acc + [{"id": "CVE-9", "pkg": "z", "in": ["img"], "until": "2026-12-01", "reason": "r"},
                   {"id": "CVE-3", "pkg": "c", "in": ["img", "gone-1.0"], "until": "2026-12-01", "reason": "r"},
                   {"id": "CVE-1", "pkg": "a", "until": "2026-12-01", "reason": "no in"}]
    # A partial scan skips the unscanned gone-1.0; the scanned-but-unmatched pairs are reported,
    # including the duplicate CVE-3 entry for img (the first entry already took that finding).
    f, w, u, c, un = evaluate({"img": rep}, stale, today)
    checks.append(("an unused acceptance is reported (partial scan)",
                   [(x["acceptance"], x["artifact"]) for x in un]
                   == [("CVE-9 z", "img"), ("CVE-3 c", "img"), ("CVE-1 a", "-")]
                   and [r["id"] for r in f] == ["CVE-1", "CVE-5"]))
    f, w, u, c, un = evaluate({"img": rep}, stale, today, full_scan=True)
    checks.append(("an unused acceptance is reported (full scan: an unscanned artefact too)",
                   [(x["acceptance"], x["artifact"]) for x in un]
                   == [("CVE-9 z", "img"), ("CVE-3 c", "img"), ("CVE-3 c", "gone-1.0"), ("CVE-1 a", "-")]))
    checks.append(("an unused acceptance is in the summary", "match nothing" in render(f, w, u, c, un)))
    only = [{"id": "CVE-1", "pkg": "a", "in": ["img", "atlas-backup"], "until": "2026-12-01", "reason": "r"}]
    f, w, u, c, un = evaluate({"img": rep}, only, today, full_scan=True)
    checks.append(("an images.yml-only artefact is not missing from a full scan", un == []))

    f, w, u, c, un = evaluate({"img": rep}, acc, today, full_scan=True)
    checks.append(("advisory: a fixable finding passes, the same run without it fails",
                   exit_code(f, un, advisory=True, quiet=True) == 0 and exit_code(f, un, advisory=False) == 1
                   and exit_code([], [], advisory=False) == 0))

    bad = [n for n, ok in checks if not ok]
    print("self-test", "passed" if not bad else f"FAILED: {bad}")
    return 0 if not bad else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    md_out = sys.argv[sys.argv.index("--summary-md") + 1] if "--summary-md" in sys.argv else None
    if md_out:
        args.remove(md_out)
    reports = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(args[0]).glob("*.json"))}
    if not reports:
        print(f"::error::no Trivy reports in {args[0]}")
        return 2
    accepted = json.loads(ACCEPTED.read_text(encoding="utf-8")).get("accepted", []) if ACCEPTED.exists() else []
    fail, warn, unfixed, counts, unused = evaluate(reports, accepted, dt.date.today(),
                                                   full_scan="--full-scan" in sys.argv)
    text = render(fail, warn, unfixed, counts, unused)
    print(text)
    if md_out:
        Path(md_out).write_text(text, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text)
    advisory = "--advisory" in sys.argv
    level = "warning" if advisory else "error"
    for r in warn:
        print(f"::warning::{r['artifact']}: {r['id']} {r['pkg']} {r['installed']} -> {r['fixed']} accepted {r['accepted']}")
    for r in fail:
        print(f"::{level}::{r['artifact']}: {r['severity']} {r['id']} in {r['pkg']} {r['installed']}, fixed in {r['fixed']}")
    for u in unused:
        print(f"::{level}::tools/ci/sca-accepted.json: {u['acceptance']} in {u['artifact']}: {u['why']}")
    return exit_code(fail, unused, advisory)


def exit_code(fail: list, unused: list, advisory: bool, quiet: bool = False) -> int:
    """1 when the gate fails; an advisory run (see the docstring) never does."""
    if (fail or unused) and advisory:
        if quiet:
            return 0
        note = ("ADVISORY: this pull request changes no dependency, so the findings above are not "
                "its doing and do not fail it. Master, the weekly run and a release still fail on them.")
        print(f"::notice::{note}")
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
                fh.write(f"\n\n**{note}**\n")
        return 0
    return 1 if fail or unused else 0


if __name__ == "__main__":
    sys.exit(main())
