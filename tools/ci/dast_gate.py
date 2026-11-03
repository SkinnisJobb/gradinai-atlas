#!/usr/bin/env python3
"""The DAST gate over OWASP ZAP's JSON reports (STORY-atlas-lcm-plan, item 6).

A HIGH-risk alert (riskcode 3) fails the run, unless tools/ci/dast-accepted.json lists its ZAP
plugin id with a reason and an `until` date that has not passed. Medium, low and informational
alerts are listed in the job summary and kept in the report artifact, never failing.

  python tools/ci/dast_gate.py REPORT.json [REPORT.json ...]
  python tools/ci/dast_gate.py --self-test

Exit 0 pass, 1 high alert, 2 a report is missing (the scan did not run: that is not a pass).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCEPTED = ROOT / "tools" / "ci" / "dast-accepted.json"
RISK = {"3": "High", "2": "Medium", "1": "Low", "0": "Informational"}


def alerts(report: dict):
    for site in report.get("site") or []:
        for a in site.get("alerts") or []:
            yield site.get("@name", ""), a


def evaluate(reports: dict[str, dict], accepted: list[dict], today: dt.date):
    rows, fail = [], []
    for name, rep in reports.items():
        for site, a in alerts(rep):
            risk = str(a.get("riskcode", "0"))
            uris = sorted({i.get("uri", "") for i in a.get("instances") or []})
            row = {"scan": name, "site": site, "plugin": str(a.get("pluginid", "")),
                   "alert": a.get("alert") or a.get("name", ""), "risk": RISK.get(risk, risk),
                   "count": int(a.get("count") or len(uris)), "example": uris[0] if uris else ""}
            rows.append(row)
            if risk == "3":
                acc = next((x for x in accepted if str(x["plugin"]) == row["plugin"]), None)
                if acc and dt.date.fromisoformat(acc["until"]) >= today:
                    row["note"] = f"accepted until {acc['until']}: {acc['reason']}"
                else:
                    fail.append(row)
    order = {"High": 0, "Medium": 1, "Low": 2, "Informational": 3}
    rows.sort(key=lambda r: (order.get(r["risk"], 9), r["scan"], r["alert"]))
    return rows, fail


def render(rows, fail) -> str:
    out = ["## DAST (OWASP ZAP)", "", f"**{len(fail)} high alert(s) failing.**", "",
           "| scan | risk | alert | plugin | instances | example | note |", "|---|---|---|---|---|---|---|"]
    out += [f"| {r['scan']} | {r['risk']} | {r['alert']} | {r['plugin']} | {r['count']} | "
            f"`{r['example']}` | {r.get('note', '')} |" for r in rows]
    return "\n".join(out) + "\n"


def self_test() -> int:
    rep = {"site": [{"@name": "https://atlas.test", "alerts": [
        {"pluginid": "1", "alert": "SQLi", "riskcode": "3", "instances": [{"uri": "https://atlas.test/api/x"}]},
        {"pluginid": "2", "alert": "XSS", "riskcode": "3", "instances": []},
        {"pluginid": "3", "alert": "Header", "riskcode": "1", "count": "4"}]}]}
    rows, fail = evaluate({"api": rep}, [{"plugin": "2", "until": "2026-12-01", "reason": "r"}],
                          dt.date(2026, 11, 1))
    ok = [r["plugin"] for r in fail] == ["1"] and len(rows) == 3 and rows[-1]["count"] == 4
    print("self-test", "passed" if ok else f"FAILED {rows} {fail}")
    return 0 if ok else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    reports = {}
    for p in map(Path, sys.argv[1:]):
        if not p.is_file():
            print(f"::error::ZAP report {p} is missing: the scan did not complete")
            return 2
        reports[p.stem] = json.loads(p.read_text(encoding="utf-8"))
    accepted = json.loads(ACCEPTED.read_text(encoding="utf-8")).get("accepted", []) if ACCEPTED.exists() else []
    rows, fail = evaluate(reports, accepted, dt.date.today())
    text = render(rows, fail)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text)
    for r in fail:
        print(f"::error::ZAP high: {r['alert']} (plugin {r['plugin']}) at {r['example']}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
