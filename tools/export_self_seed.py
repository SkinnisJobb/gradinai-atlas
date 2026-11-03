#!/usr/bin/env python3
"""Export Atlas's own world model into the `self` seed profile (TASK-1461).

A fresh install that sets `ATLAS_SEED=self` starts with the world model Atlas keeps of itself:
the object and bridge nodes under the `atlas` project (API, MCP, board, graph, retrieval, ...),
their prose and documents, the edges between them and a reviewed set of intents. No workitem,
comment, grant, account, event or ADR is exported: users file their own work against these
nodes and edit them with agents. This script writes that profile; `backend/self_seed.py` loads it.

    # 1. read a live Atlas into a snapshot (GET only; the credential is read from the file by
    #    this process, never from argv or the environment, and never printed)
    python tools/export_self_seed.py fetch --base-url https://atlas.example \\
        --token-file ~/.secrets/<read-token> --out <scratch>/snapshot.json
    # 2. build the profile from the snapshot (no network)
    python tools/export_self_seed.py build --snapshot <scratch>/snapshot.json \\
        --denylist ~/.secrets/scrub-denylist.txt
    # 3. CI / review: the committed profile passes the self-check
    python tools/export_self_seed.py check [--denylist PATH]

The snapshot is NOT committed: it is the unfiltered live text. Keep it in a scratch directory.

OUTPUT (all committed, all deterministic: sorted, no timestamps, so a re-run is a minimal diff)
  backend/self_seed_data.py          the profile, as Python data (boot opens no file)
  tools/self_seed/INTENTS-REVIEW.md  every intent in the subtree, with its decision and reason
  tools/self_seed/EXPORT-REPORT.md   what was dropped and why (counts and pattern names only)

WHAT IS LEFT OUT, AND HOW
  * Anything outside the `contains` subtree of `--root` (default `atlas`), and every node whose
    family is not object or bridge (every workitem). An edge with an endpoint outside the kept
    set is dropped, never left dangling.
  * The nodes and subtrees named in `tools/self_seed/scope.json` `exclude_nodes`: deployment
    instances of one estate (its staging copies, its CI runners, its tunnel), not parts of the
    product. Their edges go with them.
  * Every node field except id, type, name, summary, abstract, detail and `attributes.docs`
    (owner, source, host, health, probes and labels describe one estate, not the product).
  * Every intent not marked `include` in `tools/self_seed/intents.json`. A new intent on live
    arrives as `pending` in the review file and is not seeded until a person decides.
  * PROSE THAT MATCHES A FORBIDDEN PATTERN. Prose fields and documents are cut into paragraphs
    (blank-line separated); a paragraph that matches is dropped, and a document or field that is
    left empty is dropped. The report names the node, the field and the pattern, never the text.
    An intent that matches is dropped whole. Fix the text on the source Atlas and re-export.

FORBIDDEN PATTERNS
  The scrub gate's generic patterns (private IPv4, `<host>.local`, email addresses, imported
  from tools/scrub/scan.py), the scrub denylist (personal names, domains, hostnames; read by
  path exactly as CI does: `--denylist` or SCRUB_DENYLIST_FILE, never printed), and this
  script's own: paths under a user's home directory, a secrets directory, Windows user paths.
  Two more, at build time only, because they need the snapshot or a local file:
    outside-node-id   every OTHER world-node id on the source (another project, a host, a
                      device, an excluded instance): prose naming one is about something a
                      fresh install does not have. The ids are read from the snapshot and
                      never written anywhere.
    --extra-denylist  a local file in the denylist format for estate terms the scrub denylist
                      does not carry yet (keep it outside the repo; better, add the terms to the
                      denylist itself).
  Without a denylist the build still runs and says so: the result is then NOT ready to commit.
  `check` re-runs every pattern over the committed output and exits 1 on any hit; pass
  `--snapshot` and `--extra-denylist` to it for the build-time two as well.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE / "scrub"))
import scan  # noqa: E402  (tools/scrub/scan.py: the generic patterns and the denylist loader)

CONFIG_DIR = HERE / "self_seed"
SCOPE_FILE = CONFIG_DIR / "scope.json"
INTENTS_FILE = CONFIG_DIR / "intents.json"
REVIEW_FILE = CONFIG_DIR / "INTENTS-REVIEW.md"
REPORT_FILE = CONFIG_DIR / "EXPORT-REPORT.md"
DATA_FILE = REPO / "backend" / "self_seed_data.py"

WORLD_FAMILIES = "object,bridge"
INTENT_DECISIONS = ("include", "exclude", "borderline", "pending")

#: Patterns this exporter adds to the scrub gate's. Generic: they name nobody.
EXTRA_PATTERNS: dict[str, str] = {
    "home-path-posix": r"(?:/home|/Users)/[A-Za-z0-9._-]+",
    "home-path-windows": r"[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9._-]+",
    "home-tilde-path": r"(?<![\w/])~/[A-Za-z0-9._-]",
    "secrets-dir": r"\.secrets\b",
}

#: Work identifiers: not forbidden (the prose may cite a decision), but counted in the report,
#: because a fresh install has none of these items and the reference reads as a dangling link.
WORK_REF = re.compile(r"\b(?:TASK|STORY|EPIC|BUG)-[A-Za-z0-9-]*\d[A-Za-z0-9-]*\b")


# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #

def load_patterns(denylist: str | None) -> tuple[list, str]:
    """(patterns, denylist state). The denylist is opened by path and its text never shown."""
    pats = scan.generic_patterns()
    pats += [scan.Pattern(n, re.compile(rx, re.IGNORECASE), True) for n, rx in EXTRA_PATTERNS.items()]
    path = denylist or os.environ.get("SCRUB_DENYLIST_FILE") or None
    if not path:
        return pats, "absent"
    p = Path(path).expanduser()
    if not p.is_file():
        return pats, "absent"
    try:
        pats += scan.load_denylist(p)
    except scan.ConfigError as exc:
        raise SystemExit(f"denylist: {exc}")
    return pats, "loaded"


def hits(text: str, patterns) -> list[str]:
    return sorted({p.name for p in patterns if p.regex.search(text or "")})


# --------------------------------------------------------------------------- #
# fetch: live Atlas -> snapshot (GET only)
# --------------------------------------------------------------------------- #

class Api:
    def __init__(self, base_url: str, token_file: Path):
        # https only: the request carries a credential, and urlopen would otherwise also accept
        # file: and other schemes (Bandit B310, reported as medium).
        if urllib.parse.urlsplit(base_url).scheme != "https":
            raise SystemExit("--base-url must be an https:// URL")
        self.base = base_url.rstrip("/")
        # Read here, held in memory, sent only as the Authorization header. Never printed.
        self._token = token_file.read_text(encoding="utf-8").strip()

    def get(self, route: str, **params) -> dict:
        url = self.base + route + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self._token}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise SystemExit(f"GET {route}: HTTP {e.code}") from None


OUTLINE_LINE = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*) \[([a-z_-]+)\]")


def fetch(api: Api, root: str) -> dict:
    outline = api.get("/api/graph/outline", root=root, families=WORLD_FAMILIES)
    ids = sorted({m.group(1) for line in outline["text"].splitlines()
                  for m in [OUTLINE_LINE.search(line)] if m})
    if outline.get("truncated") or len(ids) != outline.get("count"):
        raise SystemExit(f"outline of {root}: {len(ids)} ids parsed, {outline.get('count')} reported")
    # Every OTHER world node id the source holds: prose that names one is about something outside
    # the profile (another project, a host, a device) and is dropped at build time.
    world = api.get("/api/graph/outline", families=WORLD_FAMILIES)
    outside = sorted({m.group(1) for line in world["text"].splitlines()
                      for m in [OUTLINE_LINE.search(line)] if m} - set(ids))
    nodes, docs, edges, intents, types = {}, {}, {}, [], {}
    for nid in ids:
        n = api.get(f"/api/node/{urllib.parse.quote(nid)}")
        nodes[nid] = {k: n.get(k) for k in ("id", "type", "name", "summary", "abstract",
                                             "detail", "attributes")}
        docs[nid] = {}
        for d in (n.get("attributes") or {}).get("docs") or []:
            body = api.get(f"/api/node/{urllib.parse.quote(nid)}/doc", path=d["path"])
            docs[nid][d["path"]] = body.get("content", "")
        g = api.get("/api/graph", focus=nid, depth=1, prose="none", work="none", cap=500)
        for e in g.get("edges", []):
            edges[(e["src"], e["edge_type"], e["dst"])] = None
        for gn in g.get("nodes", []):
            types[gn["id"]] = gn.get("type")
        for it in api.get("/api/intents", node_id=nid).get("intents", []):
            if it.get("node_id", nid) == nid:
                intents.append({k: it.get(k) for k in ("node_id", "slug", "kind", "title",
                                                        "statement", "source", "position")}
                               | {"node_id": nid})
    return {"root": root, "outline": outline["text"], "nodes": nodes, "docs": docs,
            "edges": sorted([list(k) for k in edges]), "intents": intents,
            "outside_world_ids": outside, "types": dict(sorted(types.items()))}


def outside_id_pattern(ids) -> list:
    """One pattern for the source's other world-node ids (none of them is ever printed).

    A hyphenated id (`some-device-x`) is distinctive and matches anywhere, word-bounded. A
    single-word id may be an ordinary word (`property`, `seat`), so it matches only where it is
    used as an id: in backticks, or beside a diagram arrow or an edge label.
    """
    ids = sorted(set(ids), key=lambda x: (-len(x), x))
    if not ids:
        return []
    hy = [re.escape(i) for i in ids if "-" in i or "_" in i or "." in i]
    one = [re.escape(i) for i in ids if not ("-" in i or "_" in i or "." in i)]
    parts = []
    if hy:
        parts.append(r"(?<![\w-])(?:" + "|".join(hy) + r")(?![\w-])")
    if one:
        alt = "(?:" + "|".join(one) + ")"
        parts.append(r"`" + alt + r"`")
        parts.append(r"(?<![\w-])" + alt + r"\s*(?:-->|---|-\.->|==>|\|)")
        parts.append(r"(?:-->|---|-\.->|==>|\|)\s*" + alt + r"(?![\w-])")
    return [scan.Pattern("outside-node-id", re.compile("|".join(parts), re.IGNORECASE), True)]


# --------------------------------------------------------------------------- #
# build: snapshot -> profile
# --------------------------------------------------------------------------- #

def _load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def subtree(root: str, kept: set[str], contains: list[tuple[str, str]]) -> set[str]:
    kids: dict[str, list[str]] = {}
    for src, dst in contains:
        kids.setdefault(src, []).append(dst)
    out, todo = set(), [root]
    while todo:
        n = todo.pop()
        if n in out or n not in kept:
            continue
        out.add(n)
        todo += kids.get(n, [])
    return out


def scrub_prose(text: str | None, patterns, where: str, dropped: list) -> str | None:
    """Drop every paragraph that matches a pattern; None when nothing is left."""
    if not text:
        return None
    paras = re.split(r"\n[ \t]*\n", text.strip("\n"))
    keep = []
    for i, para in enumerate(paras):
        h = hits(para, patterns)
        if h:
            dropped.append((where, f"paragraph {i + 1} of {len(paras)}", ", ".join(h)))
        else:
            keep.append(para)
    if not keep:
        return None
    return "\n\n".join(keep).strip() + ("\n" if text.endswith("\n") else "")


def build(snap: dict, patterns, scope: dict, decisions: dict) -> tuple[dict, dict]:
    root = snap["root"]
    edges = [tuple(e) for e in snap["edges"]]
    excluded = set(scope.get("exclude_nodes", {}))
    world = set(snap["nodes"]) - excluded
    contains = [(s, d) for s, t, d in edges if t == "contains"]
    kept = subtree(root, world, contains)
    report = {"excluded_scope": sorted(excluded & set(snap["nodes"])),
              "excluded_unreachable": sorted(world - kept),
              "dropped_prose": [], "dropped_edges": {}, "dropped_docs": [],
              "dropped_intents": [], "work_refs": {}}
    # Prose naming a node the profile does not carry (another project, a host, an excluded
    # deployment instance) is about something a fresh install does not have.
    patterns = list(patterns) + outside_id_pattern(
        list(snap.get("outside_world_ids", [])) + sorted(set(snap["nodes"]) - kept))
    types = snap.get("types", {})
    work_types = {"epic", "feature", "story", "task", "subtask", "sub-task", "bug", "decision"}

    nodes = []
    for nid in sorted(kept):
        n = snap["nodes"][nid]
        if hits(nid, patterns) or hits(n["name"] or "", patterns):
            raise SystemExit(f"node {nid}: its id or name matches a forbidden pattern; "
                             f"add it to scope.json exclude_nodes or rename it on the source")
        out = {"id": nid, "type": n["type"], "name": n["name"] or nid}
        for f in ("summary", "abstract", "detail"):
            v = scrub_prose(n.get(f), patterns, f"{nid}.{f}", report["dropped_prose"])
            if v:
                out[f] = v
        docs = []
        for d in sorted((n.get("attributes") or {}).get("docs") or [], key=lambda d: d["path"]):
            content = scrub_prose(snap["docs"].get(nid, {}).get(d["path"]), patterns,
                                  f"{nid}:{d['path']}", report["dropped_prose"])
            if hits(d.get("title") or "", patterns):
                content = None
            if not content:
                report["dropped_docs"].append(f"{nid}:{d['path']}")
                continue
            docs.append({"path": d["path"], "kind": d.get("kind") or "design",
                         "title": d.get("title") or d["path"].rsplit("/", 1)[-1],
                         "format": d.get("format") or "md", "content": content})
        out["docs"] = docs
        nodes.append(out)

    kept_edges = []
    for s, t, d in sorted(set(edges)):
        if s in kept and d in kept:
            kept_edges.append({"src": s, "edge_type": t, "dst": d})
        elif s in kept or d in kept:
            # Counted, never listed: the outside endpoint's id is often exactly what is being
            # scrubbed (a host, a person's project), and a workitem is not world model.
            other = d if s in kept else s
            ot = types.get(other) or "unknown"
            kind = ("a workitem" if ot in work_types else
                    "an excluded instance" if other in snap["nodes"] else f"a {ot} outside the subtree")
            key = f"`{t}` {'to' if s in kept else 'from'} {kind}"
            report["dropped_edges"][key] = report["dropped_edges"].get(key, 0) + 1

    intents, review = [], []
    for it in sorted(snap["intents"], key=lambda i: (i["node_id"], i["position"], i["slug"])):
        if it["node_id"] not in kept:
            continue
        key = f"{it['node_id']}:{it['slug']}"
        dec = decisions.get(key, {"decision": "pending", "reason": "new on the source; not reviewed"})
        h = hits(" ".join(str(it.get(k) or "") for k in ("title", "statement")), patterns)
        review.append({**it, "key": key, "decision": dec["decision"], "reason": dec.get("reason", ""),
                       "hits": h})
        if dec["decision"] != "include":
            continue
        if h:
            report["dropped_intents"].append((key, ", ".join(h)))
            continue
        intents.append({"node_id": it["node_id"], "slug": it["slug"], "kind": it["kind"],
                        "title": it["title"], "statement": it["statement"]})
    for n in nodes:
        refs = set(WORK_REF.findall(" ".join([n.get(f) or "" for f in ("summary", "abstract", "detail")]
                                             + [d["content"] for d in n["docs"]])))
        if refs:
            report["work_refs"][n["id"]] = len(refs)
    stale = sorted(set(decisions) - {r["key"] for r in review})
    report["stale_decisions"] = stale
    profile = {"root": root, "nodes": nodes, "edges": kept_edges, "intents": intents}
    return profile, {"report": report, "review": review}


# --------------------------------------------------------------------------- #
# writers
# --------------------------------------------------------------------------- #

DATA_HEADER = '''"""The `self` seed profile's data: Atlas's own world model (TASK-1461). GENERATED.

Do not edit by hand. Regenerate with `tools/export_self_seed.py` (its docstring has the steps)
and commit the diff; `backend/self_seed.py` loads it into a fresh graph under ATLAS_SEED=self.
Python data rather than a file read at boot: boot opens no file (backend/CLAUDE.md).
"""
'''


def _str_lit(s: str) -> str:
    # A JSON string literal is a valid Python one; U+2028/U+2029 are escaped so no editor or
    # diff tool splits the line.
    return json.dumps(s, ensure_ascii=False).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _py(v, indent: int) -> str:
    """A Python literal for strings, lists and dicts, ONE SOURCE LINE PER TEXT LINE, so a
    one-line edit of a document on the source is a one-line diff in the review."""
    pad, inner = " " * indent, " " * (indent + 4)
    if isinstance(v, str):
        lines = v.splitlines(keepends=True)
        if len(lines) <= 1:
            return _str_lit(v)
        return "(\n" + "".join(f"{inner}{_str_lit(ln)}\n" for ln in lines) + f"{pad})"
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[\n" + "".join(f"{inner}{_py(x, indent + 4)},\n" for x in v) + f"{pad}]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        return "{\n" + "".join(f"{inner}{_str_lit(k)}: {_py(x, indent + 4)},\n"
                               for k, x in v.items()) + f"{pad}}}"
    raise SystemExit(f"profile holds a {type(v).__name__}; only strings, lists and dicts are allowed")


def read_profile(text: str) -> dict:
    """PROFILE out of the data module's text, as a literal: nothing in it is executed."""
    for stmt in ast.parse(text).body:
        if isinstance(stmt, ast.Assign) and [getattr(t, "id", None) for t in stmt.targets] == ["PROFILE"]:
            return ast.literal_eval(stmt.value)
    raise ValueError("no PROFILE assignment")


def render_data(profile: dict) -> str:
    text = DATA_HEADER + "\nPROFILE = " + _py(profile, 0) + "\n"
    if read_profile(text) != profile:  # round-trip, or refuse to write
        raise SystemExit("rendered profile does not read back equal to the built one")
    return text


def render_review(review: list) -> str:
    out = ["# Self-seed intents: review", "",
           "Generated by `tools/export_self_seed.py build`. Every intent authored in the exported",
           "subtree, with the decision recorded in `tools/self_seed/intents.json`. Only `include`",
           "is seeded. `borderline` and `pending` wait for a person; change the decision in",
           "`intents.json` and re-run `build`.", ""]
    counts = {d: sum(1 for r in review if r["decision"] == d) for d in INTENT_DECISIONS}
    out.append("| decision | count |\n|---|---|")
    out += [f"| {d} | {c} |" for d, c in counts.items()]
    for d in ("borderline", "pending", "include", "exclude"):
        rows = [r for r in review if r["decision"] == d]
        if not rows:
            continue
        out += ["", f"## {d}", ""]
        for r in rows:
            flag = f" **Forbidden pattern: {', '.join(r['hits'])}; not seeded.**" if r["hits"] else ""
            out += [f"### `{r['key']}` ({r['kind']}): {r['title']}", "",
                    f"Reason: {r['reason'] or '-'}{flag}", ""]
            if not r["hits"]:
                out += ["> " + line if line else ">" for line in r["statement"].splitlines()]
                out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_report(report: dict, profile: dict, deny_state: str) -> str:
    docs = sum(len(n["docs"]) for n in profile["nodes"])
    out = ["# Self-seed export report", "",
           "Generated by `tools/export_self_seed.py build`. Names nodes, fields and pattern names,",
           "never the matched text.", "",
           f"Profile: {len(profile['nodes'])} nodes, {len(profile['edges'])} edges, {docs} documents, "
           f"{len(profile['intents'])} intents.", "",
           f"Scrub denylist at build time: {deny_state}.", ""]

    def section(title: str, items: list[str]):
        out.extend([f"## {title} ({len(items)})", ""] + [f"- {i}" for i in items] + [""])

    section("Nodes excluded by scope.json", report["excluded_scope"])
    section("Nodes no longer reachable from the root once those were excluded", report["excluded_unreachable"])
    section("Edges dropped (an endpoint outside the profile; counted, ids not listed)",
            [f"{k}: {v}" for k, v in sorted(report["dropped_edges"].items())])
    section("Documents dropped (empty after scrubbing)", report["dropped_docs"])
    section("Paragraphs dropped (matched a forbidden pattern)",
            [f"`{w}` {p}: {h}" for w, p, h in report["dropped_prose"]])
    section("Included intents dropped (matched a forbidden pattern)",
            [f"`{k}`: {h}" for k, h in report["dropped_intents"]])
    section("Decisions in intents.json with no intent on the source", report["stale_decisions"])
    section("Nodes whose prose cites work ids (none exist on a fresh install)",
            [f"`{k}`: {v} distinct id(s)" for k, v in sorted(report["work_refs"].items())])
    return "\n".join(out).rstrip() + "\n"


def committed_text() -> list[tuple[str, str]]:
    return [(str(p.relative_to(REPO)), p.read_text(encoding="utf-8"))
            for p in (DATA_FILE, REVIEW_FILE, REPORT_FILE, SCOPE_FILE, INTENTS_FILE) if p.is_file()]


def self_check(patterns, data_only=()) -> list[str]:
    """Every forbidden pattern over the committed output. Returns `file: pattern` problems.

    `data_only` patterns (the outside node ids) apply to the profile alone."""
    probs = []
    if not DATA_FILE.is_file():
        return [f"{DATA_FILE.relative_to(REPO)}: missing"]
    # The review file quotes excluded intents verbatim for a person to read. It is not seed
    # data, but it ships in the repo, so it is held to the same bar as the profile.
    for rel, text in committed_text():
        pats = list(patterns) + (list(data_only) if Path(rel) == DATA_FILE.relative_to(REPO) else [])
        for name in hits(text, pats):
            probs.append(f"{rel}: {name}")
    # The profile must also be loadable Python data of the documented shape.
    try:
        prof = read_profile(DATA_FILE.read_text(encoding="utf-8"))
        ids = {n["id"] for n in prof["nodes"]}
        if prof["root"] not in ids:
            probs.append("profile: the root is not a node")
        for e in prof["edges"]:
            if e["src"] not in ids or e["dst"] not in ids:
                probs.append(f"profile: dangling edge {e['src']} -{e['edge_type']}-> {e['dst']}")
        for it in prof["intents"]:
            if it["node_id"] not in ids:
                probs.append(f"profile: intent {it['node_id']}:{it['slug']} on no node")
    except Exception as exc:  # noqa: BLE001
        probs.append(f"profile: not loadable ({type(exc).__name__})")
    return probs


# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="export_self_seed.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="read a live Atlas into a snapshot (GET only)")
    f.add_argument("--base-url", required=True)
    f.add_argument("--token-file", type=Path, required=True,
                   help="a read credential, opened by this process; never pass the token itself")
    f.add_argument("--root", default="atlas")
    f.add_argument("--out", type=Path, required=True, help="snapshot path (keep it out of the repo)")
    b = sub.add_parser("build", help="snapshot -> backend/self_seed_data.py and the review files")
    b.add_argument("--snapshot", type=Path, required=True)
    c = sub.add_parser("check", help="the self-check over the committed output")
    for p in (b, c):
        p.add_argument("--denylist", help="scrub denylist path (default: $SCRUB_DENYLIST_FILE)")
        p.add_argument("--extra-denylist", action="append", default=[], metavar="PATH",
                       help="another file in the denylist format (repeatable): estate terms you do "
                            "not want in the profile and that are not in the scrub denylist yet. "
                            "Keep it outside the repo.")
    c.add_argument("--snapshot", type=Path,
                   help="also check for the snapshot's outside node ids (as build does)")
    args = ap.parse_args(argv)

    if args.cmd == "fetch":
        out = args.out.expanduser().resolve()
        if REPO in out.parents:
            raise SystemExit("the snapshot is unfiltered live text: write it outside the repo")
        snap = fetch(Api(args.base_url, args.token_file.expanduser()), args.root)
        out.write_text(json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        print(f"snapshot: {len(snap['nodes'])} nodes, {len(snap['edges'])} edges, "
              f"{sum(len(v) for v in snap['docs'].values())} documents, {len(snap['intents'])} intents -> {out}")
        return 0

    patterns, deny_state = load_patterns(args.denylist)
    if deny_state != "loaded":
        print("WARNING: no scrub denylist (--denylist or SCRUB_DENYLIST_FILE): generic patterns "
              "only. Personal names, domains and hostnames are NOT checked.", file=sys.stderr)
    extras = []
    for i, extra in enumerate(args.extra_denylist, 1):
        try:
            extras += [scan.Pattern(f"extra{i}[{p.name.split('[')[1]}", p.regex, False)
                       for p in scan.load_denylist(Path(extra).expanduser())]
        except scan.ConfigError as exc:
            raise SystemExit(f"extra denylist {i}: {exc}")

    snap = None
    if getattr(args, "snapshot", None):
        snap = json.loads(args.snapshot.expanduser().read_text(encoding="utf-8"))
    # The outside ids and the extra terms are checked against the profile only: scope.json and
    # the report name the excluded instances (an estate's tunnel, its staging copies) on purpose.
    data_only = list(extras)
    if snap is not None:
        scope = _load_json(SCOPE_FILE, {})
        data_only += outside_id_pattern(list(snap.get("outside_world_ids", []))
                                        + sorted(set(scope.get("exclude_nodes", {}))))

    if args.cmd == "build":
        scope = _load_json(SCOPE_FILE, {})
        decisions = _load_json(INTENTS_FILE, {}).get("decisions", {})
        profile, extra = build(snap, patterns + extras, scope, decisions)
        DATA_FILE.write_text(render_data(profile), encoding="utf-8", newline="\n")
        REVIEW_FILE.write_text(render_review(extra["review"]), encoding="utf-8", newline="\n")
        REPORT_FILE.write_text(render_report(extra["report"], profile, deny_state), encoding="utf-8",
                               newline="\n")
        docs = sum(len(n["docs"]) for n in profile["nodes"])
        print(f"profile: {len(profile['nodes'])} nodes, {len(profile['edges'])} edges, {docs} documents, "
              f"{len(profile['intents'])} intents (denylist {deny_state})")

    probs = self_check(patterns, data_only)
    if probs:
        print("self-check FAILED (pattern names only):\n  " + "\n  ".join(probs), file=sys.stderr)
        return 1
    print(f"self-check: clean (denylist {deny_state})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
