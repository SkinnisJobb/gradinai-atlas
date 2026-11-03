#!/usr/bin/env python3
"""Sync the ADR files in each repo's docs/adr/ into the Atlas ADR registry (ADR 0045).

ONE WAY, git -> Atlas. Git is the source of truth; Atlas holds an index with provenance.
DRY-RUN BY DEFAULT: without --apply it reads the checkouts, prints what it would write, and
talks to nobody. It NEVER DELETES: a file that has gone becomes an orphaned record, kept.
Stdlib only.

    python tools/adr_sync.py --config my_sources.json     # dry run over that config
    python tools/adr_sync.py --config my_sources.json --root /path/to/checkouts
    python tools/adr_sync.py --config my_sources.json --apply --base-url https://atlas.example:8443 \
        --token-file ~/.secrets/atlas-adr-sync.token      # write (needs the `write` scope)

THE CONFIG lists the repos, their Atlas project and their prefix. It comes from --config PATH,
else the ATLAS_ADR_SOURCES environment variable (a path). With neither, the tool prints how to
configure it and exits 0 having done nothing. `tools/adr_sources.example.json` shows the shape.
The homelab's own config is `deploy/homelab/adr_sources.json`:

    python tools/adr_sync.py --config deploy/homelab/adr_sources.json --root C:/Projects

THE IDS (ADR 0045). Each ADR gets its project's prefix plus a 3-digit sequence: AT001. A
project's sequence follows its files' legacy numbers in order, and an id once written is never
reassigned: on --apply the records already in Atlas are read first, keep their ids, and only a
new file takes the next free sequence. The legacy number stays on the record as an alias.

ATLAS-NATIVE RECORDS (source atlas, TASK-1354) share a project's id space but are not this
tool's: they were born in Atlas and have no file. The sync reads them only to step over their
ids. It never creates, changes or orphans one, a new file's id skips every sequence one holds,
and a planned write to an id one holds is an error, not a write (the API refuses it as well).

THE CREDENTIAL is read from --token-file by this process, never from argv or the environment.

Exit status: 0 on success (warnings included), 1 on a parse failure or a refused write.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_ENV = "ATLAS_ADR_SOURCES"
EXAMPLE_CONFIG = HERE / "adr_sources.example.json"

FILE_RE = re.compile(r"^(\d{4})-(.+)\.md$")
TITLE_RE = re.compile(r"^#\s+(\d{4})\.\s+(.+?)\s*$")
FIELD_RE = re.compile(r"^-\s+\*\*([A-Za-z ]+?)(?::\*\*|\*\*:|:)\s*(.*)$")
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
#: A link to another ADR file: `(0031-one-repo.md)` or a GitHub URL ending in one.
LINK_RE = re.compile(r"\]\(([^)]*?)(\d{4})-[^)/]*\.md\)")
GITHUB_REPO_RE = re.compile(r"github\.com/[^/]+/([^/]+)/")
STATUSES = ("proposed", "accepted", "deprecated", "superseded", "rejected")


@dataclass
class Adr:
    """One parsed file. `links` hold (repo_dir or None for 'this repo', legacy number)."""
    number: int
    slug: str
    title: str
    status: str
    status_raw: str
    date: str | None
    path: str
    content: str
    supersedes: list = field(default_factory=list)
    superseded_by: list = field(default_factory=list)
    amends: list = field(default_factory=list)


def _links(text: str) -> list[tuple[str | None, int]]:
    out = []
    for m in LINK_RE.finditer(text):
        prefix, num = m.group(1), int(m.group(2))
        repo = GITHUB_REPO_RE.search(prefix)
        out.append((repo.group(1) if repo else None, num))
    return out


def normalise_status(raw: str) -> str:
    word = re.sub(r"[^a-z]", " ", raw.lower()).split()
    return word[0] if word and word[0] in STATUSES else "unknown"


def parse_adr(text: str, filename: str) -> Adr:
    """Parse one MADR file. Raises ValueError naming what is missing."""
    fm = FILE_RE.match(filename)
    if not fm:
        raise ValueError(f"{filename}: not named NNNN-slug.md")
    lines = text.splitlines()
    title_line = next((ln for ln in lines if ln.startswith("# ")), None)
    tm = TITLE_RE.match(title_line or "")
    if not tm:
        raise ValueError(f"{filename}: no '# NNNN. Title' heading")
    if int(tm.group(1)) != int(fm.group(1)):
        raise ValueError(f"{filename}: heading says {tm.group(1)}, file name says {fm.group(1)}")
    fields: dict[str, str] = {}
    # The header block: list items until the first `## ` section. A field can wrap onto
    # indented continuation lines (0027's Amends does), so they are joined on.
    last = None
    for ln in lines[lines.index(title_line) + 1:]:
        if ln.startswith("## "):
            break
        m = FIELD_RE.match(ln)
        if m:
            last = m.group(1).strip().lower()
            fields.setdefault(last, m.group(2).strip())
        elif last and ln.startswith("  ") and ln.strip():
            fields[last] += " " + ln.strip()
        elif not ln.strip():
            last = None
    status_raw = fields.get("status", "")
    status = normalise_status(status_raw)
    date = DATE_RE.search(fields.get("date", ""))
    a = Adr(number=int(fm.group(1)), slug=fm.group(2), title=tm.group(2), status=status,
            status_raw=status_raw or None, date=date.group(1) if date else None,
            path=f"docs/adr/{filename}", content=text)
    if status == "superseded":
        a.superseded_by = _links(status_raw)
    a.supersedes = _links(fields.get("supersedes", ""))
    a.amends = _links(fields.get("amends", ""))
    return a


def read_repo(repo_dir: Path) -> tuple[list[Adr], list[str]]:
    adr_dir = repo_dir / "docs" / "adr"
    adrs, errors = [], []
    if not adr_dir.is_dir():
        return adrs, [f"{repo_dir.name}: no docs/adr/ directory"]
    for f in sorted(adr_dir.iterdir()):
        if not FILE_RE.match(f.name) or f.name.startswith("0000-"):
            continue
        try:
            adrs.append(parse_adr(f.read_text(encoding="utf-8"), f.name))
        except ValueError as e:
            errors.append(f"{repo_dir.name}: {e}")
    adrs.sort(key=lambda a: a.number)
    return adrs, errors


def head_commit(repo_dir: Path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def assign_ids(prefix: str, adrs: list[Adr], existing: dict[int, str],
               held: set[str] | frozenset[str] = frozenset()) -> dict[int, str]:
    """legacy number -> adr id. Existing ids are kept; new files take the next sequence.
    `held` are ids Atlas-native records hold in this project: never handed to a file."""
    ids = dict(existing)
    used = {int(re.sub(r"^[A-Z]+", "", v)) for v in list(existing.values()) + list(held)}
    nxt = max(used, default=0) + 1
    for a in adrs:
        if a.number not in ids:
            ids[a.number] = f"{prefix}{nxt:03d}"
            nxt += 1
    return ids


def plan(config: dict, root: Path, existing: dict[str, dict[int, str]] | None = None,
         held: dict[str, set[str]] | None = None):
    """Everything --apply would write, computed offline. Returns (records, warnings, errors).
    `held` maps a project to the ids its Atlas-native records hold (see the module header)."""
    existing = existing or {}
    held = held or {}
    repos = config["repos"]
    prefixes = [r["prefix"] for r in repos]
    errors: list[str] = []
    warnings: list[str] = []
    if len(set(prefixes)) != len(prefixes):
        errors.append(f"adr_sources.json: prefixes are not unique: {prefixes}")
    for r in repos:
        if not re.match(r"^[A-Z]{2,4}$", r["prefix"]):
            errors.append(f"adr_sources.json: prefix {r['prefix']!r} is not 2-4 capital letters")
    parsed: dict[str, list[Adr]] = {}
    commits: dict[str, str | None] = {}
    for r in repos:
        d = root / r["dir"]
        if not d.is_dir():
            warnings.append(f"{r['dir']}: no checkout under {root}; skipped")
            continue
        adrs, errs = read_repo(d)
        errors += errs
        parsed[r["dir"]] = adrs
        commits[r["dir"]] = head_commit(d)
    ids = {r["dir"]: assign_ids(r["prefix"], parsed.get(r["dir"], []),
                                existing.get(r["project"], {}), held.get(r["project"], set()))
           for r in repos}
    for r in repos:
        clash = sorted(set(ids[r["dir"]].values()) & set(held.get(r["project"], set())))
        if clash:
            errors.append(f"{r['dir']}: {', '.join(clash)} held by an Atlas-native record; "
                          f"the sync never writes to one")
    # The shared sequence was a convention, never enforced: report a collision, do not refuse it.
    seen: dict[int, list[str]] = {}
    for d, adrs in parsed.items():
        for a in adrs:
            seen.setdefault(a.number, []).append(d)
    for num, where in sorted(seen.items()):
        if len(where) > 1:
            warnings.append(f"legacy number {num:04d} is used in {len(where)} repos: "
                            f"{', '.join(where)} (distinct records; resolve by id)")
    by_github_name = {r["github"].split("/")[-1]: r["dir"] for r in repos}

    def resolve(this_dir: str, link: tuple[str | None, int]) -> str:
        repo_name, num = link
        # A link into a repo outside adr_sources.json stays unresolved, never this repo's same number.
        target = by_github_name.get(repo_name) if repo_name else this_dir
        hit = ids.get(target, {}).get(num)
        if hit is None and repo_name is None:
            # An in-repo link to a number this repo does not hold: try the only other holder.
            holders = [d for d in parsed if num in ids.get(d, {})]
            hit = ids[holders[0]][num] if len(holders) == 1 else None
        return hit or f"ADR-{num:04d}"

    records = []
    for r in repos:
        d = r["dir"]
        for a in parsed.get(d, []):
            records.append({
                "adr_id": ids[d][a.number], "project": r["project"], "legacy_number": a.number,
                "slug": a.slug, "title": a.title, "status": a.status, "status_raw": a.status_raw,
                "date": a.date, "supersedes": [resolve(d, x) for x in a.supersedes],
                "superseded_by": [resolve(d, x) for x in a.superseded_by],
                "amends": [resolve(d, x) for x in a.amends],
                "repo": r["github"], "path": a.path, "commit": commits.get(d),
                "content": a.content, "orphaned": False,
            })
        # Orphans: a record Atlas holds whose file is gone. Kept, marked.
        on_disk = {a.number for a in parsed.get(d, [])}
        for num, adr_id in sorted(existing.get(r["project"], {}).items()):
            if num not in on_disk and d in parsed:
                records.append({"adr_id": adr_id, "project": r["project"], "orphan_of": num})
    return records, warnings, errors


class Api:
    def __init__(self, base_url: str, token_file: Path):
        self.base = base_url.rstrip("/")
        self._token = token_file.read_text(encoding="utf-8").strip()

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(
            self.base + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}


def registry_ids(api: Api, projects: list[str]) -> tuple[dict[str, dict[int, str]],
                                                         dict[str, set[str]]]:
    """(git records as legacy number -> id, ids held by Atlas-native records), per project.
    A server older than TASK-1354 sends no `source`: every record is then git, as before."""
    out: dict[str, dict[int, str]] = {}
    held: dict[str, set[str]] = {}
    for p in projects:
        code, body = api.call("GET", "/api/adrs?" + urllib.parse.urlencode(
            {"project": p, "page_size": 200}))
        if code == 404:
            raise SystemExit("this Atlas has no /api/adrs: deploy the ADR registry first")
        if code != 200:
            raise SystemExit(f"GET /api/adrs?project={p}: {code} {body}")
        items = body.get("items", [])
        native = [i for i in items if i.get("source") == "atlas"]
        out[p] = {i["number"]: i["id"] for i in items
                  if i.get("number") is not None and i not in native}
        held[p] = {i["id"] for i in native}
    return out, held


def existing_ids(api: Api, projects: list[str]) -> dict[str, dict[int, str]]:
    return registry_ids(api, projects)[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", type=Path,
                    help=f"the sources config (default: ${CONFIG_ENV}; there is no default file)")
    ap.add_argument("--root", type=Path, default=HERE.parent.parent,
                    help="folder holding the repo checkouts (default: this repo's parent)")
    ap.add_argument("--apply", action="store_true", help="write to Atlas (default: dry run)")
    ap.add_argument("--base-url")
    ap.add_argument("--token-file", type=Path)
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")
    args = ap.parse_args(argv)

    env_path = os.environ.get(CONFIG_ENV)
    config_path = args.config or (Path(env_path) if env_path else None)
    if config_path is None:
        print(f"adr_sync: no config, nothing done. Pass --config PATH or set {CONFIG_ENV}=PATH.\n"
              f"The shape is in tools/{EXAMPLE_CONFIG.name}; "
              f"the homelab runs --config deploy/homelab/adr_sources.json.")
        return 0
    config = json.loads(config_path.read_text(encoding="utf-8"))
    api = None
    existing: dict[str, dict[int, str]] = {}
    held: dict[str, set[str]] = {}
    if args.apply:
        if not (args.base_url and args.token_file):
            ap.error("--apply needs --base-url and --token-file")
        api = Api(args.base_url, args.token_file.expanduser())
        for r in config["repos"]:
            code, _ = api.call("GET", f"/api/node/{urllib.parse.quote(r['project'])}?prose=none")
            if code != 200:
                raise SystemExit(f"project node {r['project']!r} ({r['dir']}): {code}. "
                                 f"Fix {config_path} before applying.")
        existing, held = registry_ids(api, [r["project"] for r in config["repos"]])

    records, warnings, errors = plan(config, args.root.resolve(), existing, held)
    if args.json:
        print(json.dumps({"records": [{k: v for k, v in r.items() if k != "content"}
                                      for r in records],
                          "warnings": warnings, "errors": errors}, indent=1))
    else:
        counts: dict[str, int] = {}
        for r in records:
            if "orphan_of" in r:
                print(f"  ORPHAN {r['adr_id']:<7} {r['project']:<16} legacy {r['orphan_of']:04d}: file gone")
                continue
            counts[r["project"]] = counts.get(r["project"], 0) + 1
            print(f"  {r['adr_id']:<7} {r['project']:<16} ADR {r['legacy_number']:04d}  "
                  f"{r['status']:<10} {r['date'] or '-':<10}  {r['title'][:60]}")
        print("\nper project: " + ", ".join(f"{p}={n}" for p, n in counts.items())
              + f"  (total {sum(counts.values())})")
    for w in warnings:
        print(f"[WARN] {w}", file=sys.stderr)
    for e in errors:
        print(f"[ERROR] {e}", file=sys.stderr)
    if errors:
        return 1
    if not args.apply:
        if not args.json:
            print("\ndry run: nothing written. --apply --base-url URL --token-file PATH to write.")
        return 0

    failed = 0
    for r in records:
        if "orphan_of" in r:
            code, cur = api.call("GET", f"/api/adrs/{r['adr_id']}")
            if code != 200 or cur.get("orphaned_at"):
                continue
            body = {k: cur.get(k) for k in ("title", "slug", "status", "status_raw", "date",
                                            "repo", "path", "commit", "content")}
            body.update({"project": r["project"], "legacy_number": r["orphan_of"], "orphaned": True,
                         "supersedes": [x["id"] for x in cur.get("supersedes", [])],
                         "amends": [x["id"] for x in cur.get("amends", [])],
                         "superseded_by": []})
        else:
            body = {k: v for k, v in r.items() if k != "adr_id"}
        code, resp = api.call("PUT", f"/api/adrs/{r['adr_id']}", body)
        verdict = {201: "created", 200: "updated" if resp.get("changed") else "unchanged"}
        if code in verdict:
            print(f"  {verdict[code]:<9} {r['adr_id']}")
        else:
            failed += 1
            print(f"  REFUSED   {r['adr_id']}: {code} {resp}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
