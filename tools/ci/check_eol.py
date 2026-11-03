#!/usr/bin/env python3
"""End-of-life gate for the runtimes Atlas ships (STORY-atlas-lcm-plan, item 2).

WHAT IT CHECKS. Every runtime version the tracked tree names -- the Python of the images
(`FROM python:3.12-slim`), the Postgres of the compose files (`postgres:16`,
`pgvector/pgvector:...-pg16`), the Caddy of deploy/caddy (`caddy:2.11.6`), and the Python and
Node the workflows set up (`python-version:` / `node-version:`) -- is looked up in
tools/ci/eol.json. The run FAILS when one is

  * past its end of security support, or within `margin_days` (183, six months) of it;
  * a cycle the table does not list (add it, with its date, from the source below);
  * and when the table itself was last checked more than `max_age_days` ago.

A waiver in the table (file + product + cycle, with an `until` date and a reason) turns one
finding into a warning until that date; an expired waiver fails like the finding would.

CALENDAR-VERSIONED IMAGES (LCM patch round 1). Some images have no end-of-life cycle, only a
stream of dated releases, and live where Dependabot does not look: `cloudflare/cloudflared:
2026.10.3` in deploy/prod.override.yml (Dependabot's docker-compose ecosystem reads compose*.yml
names only). The table's `calver` section lists them; a pin whose release month (YYYY.M) is more
than that entry's `max_age_days` before today FAILS the gate, so it cannot drift for months
unnoticed (2026.7.3 sat there with 22 fixable highs). The weekly `--online` run also asks the
GitHub releases API for the latest release and WARNS when it is newer than the pin.

WHY A CHECKED-IN TABLE AND NOT THE API ON EVERY PR. The PR gate must not go red because
endoflife.date was slow or down, and must give the same answer for the same commit. The price
is staleness, paid for twice: the gate refuses a table older than max_age_days, and the weekly
security run calls this with `--online`, which compares the table with
https://endoflife.date/api/v1/products/<product>/ and fails on any difference. A network
failure there is a warning (the next week tries again), never a red PR.

Usage:
  python tools/ci/check_eol.py [--today YYYY-MM-DD]       # the gate (offline)
  python tools/ci/check_eol.py --online [--update]        # compare with the API (and rewrite)
  python tools/ci/check_eol.py --self-test

Stdlib only. Exit 0 clean, 1 findings, 2 usage.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TABLE = ROOT / "tools" / "ci" / "eol.json"
API = "https://endoflife.date/api/v1/products/{}/"

# (product, regex with the cycle as group 1, which files). Image references in Dockerfiles and
# compose files; setup-* versions in workflows.
IMAGE_RULES = (
    ("python", re.compile(r"\bpython:(\d+\.\d+)")),
    ("postgresql", re.compile(r"(?<![\w/])postgres:(\d+)")),
    ("postgresql", re.compile(r"\bpgvector/pgvector:[\w.]*-pg(\d+)")),
    ("caddy", re.compile(r"(?<![\w/])caddy:(\d+)")),
)
IMAGE_LINE = re.compile(r"^\s*(FROM\s|-?\s*image:)", re.IGNORECASE)
# product -> regex with year, month, patch as groups 1-3; the policy is the table's `calver` entry.
CALVER_RULES = (
    ("cloudflared", re.compile(r"\bcloudflare/cloudflared:(\d{4})\.(\d{1,2})\.(\d+)")),
)
GITHUB_LATEST = "https://api.github.com/repos/{}/releases/latest"
WORKFLOW_RULES = (
    ("python", re.compile(r"^\s*python-version:\s*[\"']?(\d+\.\d+)")),
    ("nodejs", re.compile(r"^\s*node-version:\s*[\"']?(\d+)")),
)


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def is_image_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if name.endswith((".md", ".py", ".sh", ".txt", ".json")):
        return False
    return "Dockerfile" in name or (name.endswith((".yml", ".yaml")) and not rel.startswith(".github/"))


def scan(root: Path, files: list[str]) -> list[tuple[str, str, str, int]]:
    """(product, cycle, file, line) for every runtime reference in the tracked tree."""
    found = []
    for rel in files:
        if rel.startswith(".github/workflows/") and rel.endswith((".yml", ".yaml")):
            rules = WORKFLOW_RULES
        elif is_image_file(rel):
            rules = IMAGE_RULES
        else:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            # Image rules look only at image references, so `postgres:5432` in a URL is not one.
            if rules is IMAGE_RULES and not IMAGE_LINE.match(line):
                continue
            for product, rx in rules:
                for m in rx.finditer(line):
                    found.append((product, m.group(1), rel, n))
    return found


def scan_calver(root: Path, files: list[str]) -> list[tuple[str, str, str, int]]:
    """(product, version, file, line) for every calendar-versioned image reference."""
    found = []
    for rel in files:
        if rel.startswith(".github/") or not is_image_file(rel):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#") or not IMAGE_LINE.match(line):
                continue
            for product, rx in CALVER_RULES:
                for m in rx.finditer(line):
                    found.append((product, f"{m.group(1)}.{int(m.group(2))}.{m.group(3)}", rel, n))
    return found


def evaluate_calver(found, table: dict, today: dt.date) -> list[str]:
    """Failures: a calendar-versioned pin older than its max_age_days, or one with no policy."""
    fails = []
    for product, version, rel, line in sorted(set(found)):
        spec = table.get("calver", {}).get(product)
        if spec is None:
            fails.append(f"{rel}:{line}: {product} has no `calver` entry in tools/ci/eol.json")
            continue
        year, month = (int(x) for x in version.split(".")[:2])
        age = (today - dt.date(year, month, 1)).days
        limit = int(spec["max_age_days"])
        if age > limit:
            fails.append(f"{rel}:{line}: {product} {version} is a release of {year}-{month:02d}, "
                         f"{age} days old (limit {limit}); bump it to the latest release "
                         f"(https://github.com/{spec['github']}/releases), tag + digest")
    return fails


def online_calver(table: dict, found) -> None:
    """Warn (never fail) when a newer release than the pinned one exists."""
    pinned = {}
    for product, version, rel, line in found:
        pinned.setdefault(product, set()).add(version)
    for product, spec in table.get("calver", {}).items():
        try:
            req = urllib.request.Request(GITHUB_LATEST.format(spec["github"]),
                                         headers={"User-Agent": "atlas-ci-eol-check",
                                                  "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                latest = json.load(r)["tag_name"].lstrip("v")
        except Exception as exc:  # network, rate limit, schema: report, never fail on it
            print(f"::warning::GitHub releases unreachable for {product}: {type(exc).__name__}: {exc}")
            continue
        key = lambda v: tuple(int(x) for x in v.split("."))
        for v in sorted(pinned.get(product, ())):
            if key(latest) > key(v):
                print(f"::warning::{product} {v} is pinned; {latest} is the latest release "
                      f"(https://github.com/{spec['github']}/releases/tag/{latest})")
            else:
                print(f"{product} {v} is the latest release")


def evaluate(found, table: dict, today: dt.date) -> tuple[list[str], list[str]]:
    """(failures, warnings) for the scan against the table."""
    fails, warns = [], []
    margin = dt.timedelta(days=int(table.get("margin_days", 183)))
    checked = dt.date.fromisoformat(table["checked"])
    age = (today - checked).days
    if age > int(table.get("max_age_days", 120)):
        fails.append(f"tools/ci/eol.json was checked {age} days ago (limit {table['max_age_days']}): "
                     "refresh it with `python tools/ci/check_eol.py --online --update`")
    waivers = table.get("waivers", [])
    for product, cycle, rel, line in sorted(set(found)):
        where = f"{rel}:{line}"
        entry = table["products"].get(product, {}).get("cycles", {}).get(cycle)
        if entry is None:
            fails.append(f"{where}: {product} {cycle} is not in tools/ci/eol.json; add its end-of-life date")
            continue
        eol = entry.get("eol")
        if eol is None:
            continue
        eol_d = dt.date.fromisoformat(eol)
        if eol_d - today > margin:
            continue
        state = "is past end of life" if eol_d <= today else "reaches end of life"
        msg = f"{where}: {product} {cycle} {state} on {eol} (gate: {margin.days} days ahead)"
        w = next((w for w in waivers if w["file"] == rel and w["product"] == product
                  and str(w["cycle"]) == cycle), None)
        if w and dt.date.fromisoformat(w["until"]) >= today:
            warns.append(f"{msg}; WAIVED until {w['until']}: {w['reason']}")
        elif w:
            fails.append(f"{msg}; its waiver expired on {w['until']}")
        else:
            fails.append(msg)
    return fails, warns


def fetch(product: str) -> dict[str, str | None]:
    req = urllib.request.Request(API.format(product), headers={"User-Agent": "atlas-ci-eol-check"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    return {rel["name"]: rel.get("eolFrom") for rel in data["result"]["releases"]}


def online(table: dict, update: bool) -> int:
    drift, unreachable = [], []
    for product, spec in table["products"].items():
        try:
            live = fetch(product)
        except Exception as exc:  # network, JSON, schema: report, never fail on it
            unreachable.append(f"{product}: {type(exc).__name__}: {exc}")
            continue
        for cycle, entry in spec["cycles"].items():
            if cycle not in live:
                drift.append(f"{product} {cycle}: not listed by endoflife.date")
            elif live[cycle] != entry.get("eol"):
                drift.append(f"{product} {cycle}: table {entry.get('eol')}, endoflife.date {live[cycle]}")
                if update:
                    entry["eol"] = live[cycle]
    for u in unreachable:
        print(f"::warning::endoflife.date unreachable, not compared: {u}")
    for d in drift:
        print(("updated: " if update else "::error::eol.json drift: ") + d)
    if update and not unreachable:
        table["checked"] = dt.date.today().isoformat()
        TABLE.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {TABLE.relative_to(ROOT)} (checked {table['checked']})")
    if not drift and not unreachable:
        print("eol.json agrees with endoflife.date for every listed cycle")
    return 1 if drift and not update else 0


def report(found, fails, warns) -> str:
    lines = ["## End-of-life check", "", "| runtime | cycle | where |", "|---|---|---|"]
    for product, cycle, rel, line in sorted(set(found)):
        lines.append(f"| {product} | {cycle} | `{rel}:{line}` |")
    lines.append("")
    lines += [f"- FAIL: {f}" for f in fails] + [f"- warning: {w}" for w in warns]
    if not fails:
        lines.append("- every runtime is more than the margin away from its end of security support")
    return "\n".join(lines) + "\n"


def self_test() -> int:
    table = {"checked": "2026-10-01", "max_age_days": 120, "margin_days": 183, "products": {
        "python": {"cycles": {"3.12": {"eol": "2028-10-31"}, "3.10": {"eol": "2026-10-01"}}},
        "nodejs": {"cycles": {"22": {"eol": "2027-04-30"}}},
        "caddy": {"cycles": {"2": {"eol": None}}}},
        "waivers": [{"file": "w.yml", "product": "nodejs", "cycle": "22", "until": "2026-12-01",
                     "reason": "r"}]}
    today = dt.date(2026, 11, 1)
    found = [("python", "3.12", "Dockerfile", 1), ("python", "3.10", "Dockerfile", 2),
             ("nodejs", "22", "w.yml", 3), ("nodejs", "22", "v.yml", 4), ("caddy", "2", "D", 5),
             ("python", "3.9", "x", 6)]
    fails, warns = evaluate(found, table, today)
    ok = (len(fails) == 3 and any("3.10 is past" in f for f in fails)
          and any("v.yml:4" in f for f in fails) and any("3.9 is not in" in f for f in fails)
          and len(warns) == 1 and "WAIVED" in warns[0])
    fails2, _ = evaluate([], table, dt.date(2027, 3, 1))
    ok = ok and len(fails2) == 1 and "checked" in fails2[0]
    fails3, _ = evaluate([("nodejs", "22", "w.yml", 3)], table, dt.date(2026, 12, 2))
    ok = ok and len(fails3) == 1 and "expired" in fails3[0]
    rx = [(p, m.group(1)) for p, r in IMAGE_RULES for m in r.finditer(
        "FROM python:3.12-slim@sha256:ab caddy:2.11.6-builder pgvector/pgvector:0.8.7-pg16-trixie "
        "image: postgres:16@sha256:x cloudflare/cloudflared:2026.7.3")]
    ok = ok and bool(IMAGE_LINE.match("FROM caddy:2")) and bool(IMAGE_LINE.match("    image: postgres:16"))
    ok = ok and not IMAGE_LINE.match("      ATLAS_DB_URL: postgresql://atlas@atlas-postgres:5432/atlas")
    ok = ok and sorted(rx) == [("caddy", "2"), ("postgresql", "16"), ("postgresql", "16"),
                               ("python", "3.12")]
    cv = [(p, f"{m.group(1)}.{int(m.group(2))}.{m.group(3)}") for p, r in CALVER_RULES
          for m in r.finditer("    image: cloudflare/cloudflared:2026.07.3@sha256:ab")]
    ok = ok and cv == [("cloudflared", "2026.7.3")]
    pol = {"calver": {"cloudflared": {"max_age_days": 92, "github": "cloudflare/cloudflared"}}}
    old = evaluate_calver([("cloudflared", "2026.7.3", "p.yml", 1)], pol, dt.date(2026, 10, 25))
    new = evaluate_calver([("cloudflared", "2026.10.3", "p.yml", 1)], pol, dt.date(2026, 10, 25))
    nopol = evaluate_calver([("cloudflared", "2026.10.3", "p.yml", 1)], {}, dt.date(2026, 10, 25))
    ok = ok and len(old) == 1 and "116 days old" in old[0] and new == [] and len(nopol) == 1
    print("self-test", "passed" if ok else f"FAILED: {fails} {warns} {fails2} {fails3} {rx} {cv} {old} {new}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    if a.online:
        online_calver(table, scan_calver(ROOT, tracked_files(ROOT)))
        return online(table, a.update)
    files = tracked_files(ROOT)
    found = scan(ROOT, files)
    fails, warns = evaluate(found, table, a.today)
    fails += evaluate_calver(scan_calver(ROOT, files), table, a.today)
    text = report(found, fails, warns)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text)
    for w in warns:
        print(f"::warning::{w}")
    for f in fails:
        print(f"::error::{f}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
