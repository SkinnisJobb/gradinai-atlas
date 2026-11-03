#!/usr/bin/env python3
"""install_smoke_sizing.py: peak memory and CPU per service, per leg, of an install smoke run
(STORY-oss-17, the sizing criterion).

Inputs, written by .github/workflows/install-smoke.yml:
  stats.tsv   "<epoch>\t<docker stats --no-stream --format '{{json .}}' line>", sampled ~1/s
              by a background loop for the whole run
  legs.tsv    "<epoch>\t<leg name>", one line when each leg starts
  cgroup.tsv  optional: "<service>\t<memory.peak bytes>\t<cpu usage_usec>" read from each
              container's cgroup (v2) just before teardown: the true peak, not a sample

Output: a Markdown report (stdout, and appended to $GITHUB_STEP_SUMMARY when set) and a JSON
file with the same numbers (--json). Standard library only.

    python tools/ci/install_smoke_sizing.py --dir "$SMOKE_DIR" --json sizing.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

def mib(b: float) -> str:
    return f"{b / 1024 ** 2:,.1f} MiB"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    d = Path(args.dir)

    legs = []
    if (d / "legs.tsv").exists():
        for line in (d / "legs.tsv").read_text().splitlines():
            t, _, name = line.partition("\t")
            if name:
                legs.append((float(t), name))
    legs.sort()

    def leg_at(t: float) -> str:
        cur = "before-first-leg"
        for start, name in legs:
            if t >= start:
                cur = name
        return cur

    peak = defaultdict(lambda: {"mem": 0.0, "cpu": 0.0, "samples": 0})
    per_leg = defaultdict(lambda: defaultdict(lambda: {"mem": 0.0, "cpu": 0.0}))
    last: dict = {}   # service -> (t, usage_usec), for CPU between consecutive samples
    n = 0
    src = d / "stats.tsv"
    for line in src.read_text(errors="replace").splitlines() if src.exists() else []:
        parts = line.split("	")
        if len(parts) != 4 or not parts[1]:
            continue
        try:
            t, svc, mem, usec = float(parts[0]), parts[1], float(parts[2]), float(parts[3])
        except ValueError:
            continue
        cpu = 0.0
        if svc in last and t > last[svc][0] and usec >= last[svc][1]:
            cpu = (usec - last[svc][1]) / 1e6 / (t - last[svc][0]) * 100
        last[svc] = (t, usec)
        p = peak[svc]
        p["mem"], p["cpu"], p["samples"] = max(p["mem"], mem), max(p["cpu"], cpu), p["samples"] + 1
        lg = per_leg[leg_at(t)][svc]
        lg["mem"], lg["cpu"] = max(lg["mem"], mem), max(lg["cpu"], cpu)
        n += 1

    cg = {}
    if (d / "cgroup.tsv").exists():
        for line in (d / "cgroup.tsv").read_text().splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                cg[parts[0]] = {"memory_peak": int(parts[1]) if parts[1].isdigit() else None,
                                "cpu_seconds": int(parts[2]) / 1e6 if parts[2].isdigit() else None}

    services = sorted(set(peak) | set(cg))
    out = ["## Install smoke: sizing (peak per service)", "",
           f"{n} samples (cgroup v2, every ~0.5 s per container) over {len(legs)} legs. Memory is "
           "memory.current minus inactive_file, as `docker stats` reports it; CPU is percent of one "
           "core between consecutive samples (100% = one core busy). The cgroup column is the "
           "kernel's own high-water mark (memory.peak, page cache included) for the whole run; "
           "a one-shot that has exited (atlas-migrate) has no cgroup left to read.", "",
           "| service | peak memory (sampled) | peak memory (cgroup memory.peak) | peak CPU | CPU time (s) | samples |",
           "|---|---:|---:|---:|---:|---:|"]
    for s in services:
        p, c = peak.get(s, {"mem": 0, "cpu": 0, "samples": 0}), cg.get(s, {})
        mp = c.get("memory_peak")
        cs = c.get("cpu_seconds")
        out.append(f"| {s} | {mib(p['mem'])} | {mib(mp) if mp else 'n/a'} | {p['cpu']:.1f}% | "
                   f"{f'{cs:.1f}' if cs is not None else 'n/a'} | {p['samples']} |")
    total_mem = sum(p["mem"] for p in peak.values())
    out += ["", f"Sum of per-service sampled peaks: **{mib(total_mem)}** "
                "(an upper bound on the stack's footprint; the peaks need not coincide).", "",
            "### Per leg (peak memory / peak CPU)", "",
            "| leg | " + " | ".join(services) + " |", "|---|" + "---:|" * len(services)]
    order = ["before-first-leg"] + [name for _, name in legs]
    for leg in dict.fromkeys(order):
        if leg not in per_leg:
            continue
        cells = [f"{mib(per_leg[leg][s]['mem'])} / {per_leg[leg][s]['cpu']:.0f}%" if s in per_leg[leg] else "-"
                 for s in services]
        out.append(f"| {leg} | " + " | ".join(cells) + " |")
    report = "\n".join(out) + "\n"
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    if args.json:
        Path(args.json).write_text(json.dumps({
            "samples": n, "legs": [name for _, name in legs],
            "peak": {s: {"memory_bytes": peak[s]["mem"], "cpu_percent": peak[s]["cpu"],
                         **cg.get(s, {})} for s in services},
            "per_leg": {leg: {s: {"memory_bytes": v["mem"], "cpu_percent": v["cpu"]}
                              for s, v in svcs.items()} for leg, svcs in per_leg.items()},
        }, indent=2))
    if n == 0:
        print("no samples: the sampler did not run", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
