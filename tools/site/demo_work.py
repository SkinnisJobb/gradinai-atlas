#!/usr/bin/env python3
"""Demo work for the website's screenshots (TASK-1465), written on top of the `self` seed.

docs/site/content.md section 19: every screenshot comes from a fresh, disposable demo install,
never from a real instance. The graph and node views show the seed (ATLAS_SEED=self); the board
and work views show the demo work this script writes through the API, so it can be recreated.
It is not a copy of any backlog. Run it ONCE per fresh install: tasks get minted ids, so a
second run files them again.

Two steps, in this order (.github/workflows/site-shots.yml runs both):

  accounts   the demo person `alex` (owner, shown as "Alex") and the agent `alex-claude`
             linked to Alex, through `atlas-admin` in the atlas-api container, exactly as the
             install smoke's leg 3 makes its people; then Alex's first sign-in and forced
             password change through the real form (tests/bootstrap_users.py).
  work       Alex is bound owner of the seed project `atlas`; then epics, stories and tasks
             bound to seed nodes, statuses spread over the board's columns, six open tasks
             assigned to Alex (one in progress), one pending acceptance proposal by
             alex-claude, a few comments, and history by both (the agent's rows read
             "on behalf of Alex"). The ids it got are written to SMOKE_DIR/demo-ids.json
             for capture.py.

    python tools/site/demo_work.py --dry-run              # the plan, no stack needed
    python tools/site/demo_work.py accounts --compose-dir deploy/oss
    python tools/site/demo_work.py work

SECRETS. Every credential is generated at runtime: the temporary and the new password by the
install (atlas-admin, bootstrap_users.py), the agent token by `atlas-admin token add --out -`
into a 0600 file in SMOKE_DIR. Nothing secret is on a command line, in an environment variable
or printed; each value is registered with the Actions log masker. Configuration is the install
smoke's (tests/atlas_smoke.py): SMOKE_URL, SMOKE_CA, SMOKE_DIR.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

# --------------------------------------------------------------------------- #
# The demo, as data. All text is neutral and product-like.
# --------------------------------------------------------------------------- #
PERSON = "alex"                 # username; display name below
PERSON_NAME = "Alex"
AGENT = "alex-claude"           # `bot add` label = its username; linked to PERSON
PROJECT = "atlas"               # the self seed's project root

#: Epics and stories carry caller-supplied ids; tasks are minted by the server and are named
#: here by a local key (T1..). `status` is the final status; every item is created `pending`
#: and moved by the step list below, so the moves are history rows.
EPICS = [
    {"id": "EPIC-hybrid-search", "name": "Hybrid search across the map", "binding": "atlas-context",
     "summary": "Keyword and meaning search merged into one ranked answer.", "status": "in_progress"},
    {"id": "EPIC-signed-history", "name": "Every change signed by a person", "binding": "atlas-work-store",
     "summary": "Each write names who made it, and an agent's write names its person.",
     "status": "in_progress"},
]
STORIES = [
    {"id": "STORY-ranked-merge", "parent": "EPIC-hybrid-search", "binding": "atlas-retrieval",
     "name": "Merge keyword and meaning results", "status": "in_progress",
     "summary": "One list, where an item that ranks well in either list rises."},
    {"id": "STORY-search-fallback", "parent": "EPIC-hybrid-search", "binding": "atlas-context",
     "name": "Keep search working when the add-on is off", "status": "pending",
     "summary": "Without the embedding service, search answers with keyword results and says so."},
    {"id": "STORY-history-behalf", "parent": "EPIC-signed-history", "binding": "atlas-work-store",
     "name": "History names the person behind an agent", "status": "in_progress",
     "summary": "The History tab shows the agent and the person it acts for."},
    {"id": "STORY-acceptance-review", "parent": "EPIC-signed-history", "binding": "atlas-work-store",
     "name": "Agents propose acceptance, people decide", "status": "pending",
     "summary": "An agent proposes a change to the acceptance criteria; a person approves or rejects it.",
     "acceptance": [
         "An agent's change to the acceptance criteria is stored as a proposal, not applied.",
         "The proposal shows the removed and added criteria against the current list.",
         "Only a person signed in on the board can approve or reject it.",
     ]},
]
TASKS = [
    {"key": "T1", "parent": "STORY-ranked-merge", "binding": "atlas-retrieval", "status": "done",
     "name": "Weight keyword matches by field", "priority": "medium"},
    {"key": "T2", "parent": "STORY-ranked-merge", "binding": "atlas-retrieval", "status": "in_progress",
     "name": "Merge both ranked lists into one", "priority": "high", "assignee": PERSON,
     "summary": "Reciprocal rank fusion over the keyword and the meaning list."},
    {"key": "T3", "parent": "STORY-ranked-merge", "binding": "atlas-context", "status": "pending",
     "name": "Show which list a result came from", "priority": "medium", "assignee": PERSON},
    {"key": "T4", "parent": "STORY-search-fallback", "binding": "atlas-context", "status": "blocked",
     "name": "Fall back to keyword results when the embedder is down", "priority": "high",
     "assignee": PERSON},
    {"key": "T5", "parent": "STORY-search-fallback", "binding": "atlas-context", "status": "pending",
     "name": "Name degraded search in the response", "priority": "medium", "depends_on": ["T4"]},
    {"key": "T6", "parent": "STORY-history-behalf", "binding": "atlas-work-store", "status": "done",
     "name": "Record the responsible person on agent writes", "priority": "high"},
    {"key": "T7", "parent": "STORY-history-behalf", "binding": "atlas-work-store", "status": "in_progress",
     "name": "Show \"on behalf of\" in the History tab", "priority": "medium"},
    {"key": "T8", "parent": "STORY-history-behalf", "binding": "atlas-work-store", "status": "pending",
     "name": "Page the history list by the server's total", "priority": "low", "assignee": PERSON},
    {"key": "T9", "parent": "STORY-acceptance-review", "binding": "atlas-work-store", "status": "pending",
     "name": "Draw the proposal diff in the Overview tab", "priority": "high", "assignee": PERSON},
    {"key": "T10", "parent": "STORY-acceptance-review", "binding": "atlas-work-store", "status": "pending",
     "name": "Approve and reject from the node panel", "priority": "medium", "assignee": PERSON},
]

#: The one pending proposal, made by the agent on the story above.
PROPOSAL = {
    "on": "STORY-acceptance-review",
    "acceptance": [
        "An agent's change to the acceptance criteria is stored as a proposal, not applied.",
        "The proposal shows the removed and added criteria against the current list.",
        "Only a person signed in on the board can approve or reject it, and the decision is recorded in History.",
        "A proposal replaced while the panel is open is refused, not applied unseen.",
    ],
    "note": "Two criteria from the review: record the decision, and refuse a stale approval.",
}

#: Everything after the items exist, in order, by who does it. `alex` is Alex's browser
#: session; `agent` is alex-claude's token (history rows "alex-claude (agent), on behalf of Alex").
STEPS = [
    ("agent", "status", "T2", "in_progress"),
    ("agent", "patch", "T2", {"summary": "Reciprocal rank fusion over the keyword and the meaning "
                                         "list; ties keep the keyword order."}),
    ("agent", "comment", "T2", "Both lists now merge by reciprocal rank fusion. Ties keep the keyword "
                                "order, so results do not reshuffle between reloads."),
    ("alex", "comment", "T2", "Looks right. Please add a test for an item that only one list returns."),
    ("agent", "comment", "T2", "Added: an item found only by meaning still ranks, below the shared items."),
    ("agent", "status", "T1", "done"),
    ("agent", "status", "T6", "done"),
    ("agent", "status", "T7", "in_progress"),
    ("alex", "status", "T4", "blocked"),
    ("alex", "comment", "T4", "Blocked until the test suite has a stub embedder to switch off."),
    ("alex", "status", "STORY-ranked-merge", "in_progress"),
    ("alex", "status", "STORY-history-behalf", "in_progress"),
    ("alex", "status", "EPIC-hybrid-search", "in_progress"),
    ("alex", "status", "EPIC-signed-history", "in_progress"),
    ("agent", "propose", PROPOSAL["on"], PROPOSAL),
    ("agent", "comment", PROPOSAL["on"], "Proposed two acceptance changes from the review. "
                                         "They wait for a person's decision."),
]

#: Who the screenshots open, by slug (capture.py reads these from demo-ids.json).
SHOT_TARGETS = {"history-tab": "T2", "proposal-overview": PROPOSAL["on"],
                "backlog-view": "EPIC-hybrid-search"}


def plan_problems() -> list[str]:
    """Where the step list would not leave each item at its declared status, or the demo
    misses what a screenshot needs. Empty when the plan is consistent."""
    final = {i.get("key") or i["id"]: "pending" for i in [*EPICS, *STORIES, *TASKS]}
    for _who, act, ref, arg in STEPS:
        if act == "status":
            final[ref] = arg
    bad = [f"{k}: steps leave it {final[k]!r}, declared {i['status']!r}"
           for i in [*EPICS, *STORIES, *TASKS] for k in [i.get("key") or i["id"]]
           if final[k] != i["status"]]
    mine = [t for t in TASKS if t.get("assignee") == PERSON and t["status"] not in ("done", "cancelled")]
    if not 5 <= len(mine) <= 6:
        bad.append(f"My tasks would hold {len(mine)} rows, not 5-6")
    if sum(t["status"] == "in_progress" for t in mine) != 1:
        bad.append("My tasks should hold exactly one row in progress")
    cols = {i["status"] for i in [*STORIES, *TASKS]}
    if not {"pending", "in_progress", "blocked", "done"} <= cols:
        bad.append(f"the board's columns are not all used: {sorted(cols)}")
    if not any(w == "agent" for w, *_ in STEPS) or not any(w == "alex" for w, *_ in STEPS):
        bad.append("history needs rows by both the person and the agent")
    return bad


def plan_lines() -> list[str]:
    """The plan as text, for --dry-run and for the log."""
    out = [f"install: {os.environ.get('SMOKE_URL', 'https://atlas.example.com')} (ATLAS_SEED=self)",
           f"accounts: person {PERSON!r} (owner, name {PERSON_NAME!r}); agent {AGENT!r} linked to "
           f"{PERSON!r}, token scopes read,write",
           f"binding: {PERSON} owner of project {PROJECT!r}"]
    for e in EPICS:
        out.append(f"epic  {e['id']:<26} -> {e['binding']:<17} {e['status']:<12} {e['name']}")
    for s in STORIES:
        acc = f" ({len(s.get('acceptance', []))} criteria)" if s.get("acceptance") else ""
        out.append(f"story {s['id']:<26} -> {s['binding']:<17} {s['status']:<12} {s['name']}{acc}")
    for t in TASKS:
        who = t.get("assignee") or "-"
        dep = f" depends on {','.join(t['depends_on'])}" if t.get("depends_on") else ""
        out.append(f"task  {t['key']:<4} in {t['parent']:<24}{t['status']:<12} {t.get('priority') or '-':<7}"
                   f"{who:<6} {t['name']}{dep}")
    mine = [t for t in TASKS if t.get("assignee") == PERSON and t["status"] not in ("done", "cancelled")]
    out.append(f"My tasks for {PERSON}: {len(mine)} rows, "
               f"{sum(t['status'] == 'in_progress' for t in mine)} in progress")
    for who, act, ref, arg in STEPS:
        text = arg if isinstance(arg, str) else json.dumps(arg)[:70]
        out.append(f"step  {who:<5} {act:<8} {ref:<24} {text[:80]}")
    return out


# --------------------------------------------------------------------------- #
# accounts: atlas-admin in the container, as install-smoke's leg 3
# --------------------------------------------------------------------------- #
def accounts(compose_dir: Path) -> int:
    import atlas_smoke as smoke
    import bootstrap_users

    smoke.DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)
    base = ["docker", "compose", "exec", "-T", "atlas-api", "atlas-admin", "--actor", "site-shots"]

    def admin(*args: str, out: Path | None = None) -> None:
        # Output that holds a secret goes straight to a 0600 file; it is never read into
        # this process's log. Everything else is printed.
        if out is None:
            subprocess.run([*base, *args], cwd=compose_dir, check=True)
            return
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            subprocess.run([*base, *args], cwd=compose_dir, check=True, stdout=fh)

    add = smoke.DIR / f"{PERSON}.add"
    admin("user", "add", PERSON, "--role", "owner", "--name", PERSON_NAME, out=add)
    print(f"{PERSON}: " + next((ln for ln in add.read_text(encoding="utf-8").splitlines()
                                 if ln.startswith("created")), "created"))
    admin("bot", "add", AGENT, "--name", AGENT, "--person", PERSON)
    tok = smoke.DIR / f"{AGENT}.token"
    admin("token", "add", AGENT, "--scopes", "read,write", "--label", "site-shots", "--out", "-", out=tok)
    if not tok.read_text(encoding="utf-8").strip():
        print(f"{AGENT}: token add wrote nothing", file=sys.stderr)
        return 1
    smoke.secret(tok.name)          # masks it; the value is not kept
    print(f"{AGENT}: linked to {PERSON}, token in SMOKE_DIR (0600)")
    return bootstrap_users.main([PERSON])


# --------------------------------------------------------------------------- #
# work: the API, as Alex's session and as alex-claude's token
# --------------------------------------------------------------------------- #
class Agent:
    """alex-claude through the REST API with its own Bearer token (no CSRF: a token write is
    checked for scope instead, auth.guard_write)."""

    def __init__(self) -> None:
        import httpx
        import atlas_smoke as smoke
        self.http = httpx.Client(base_url=smoke.URL, verify=smoke.ssl_ctx(), timeout=30,
                                 headers={"Authorization": "Bearer " + smoke.token(AGENT),
                                          "Accept": "application/json"})

    def write(self, method: str, path: str, body: dict):
        return self.http.request(method, path, json=body)

    def get(self, path: str, **params):
        return self.http.get(path, params=params or None)


def _ok(r, what: str, codes=(200, 201)) -> dict:
    if r.status_code not in codes:
        raise SystemExit(f"{what}: {r.status_code} {r.text[:300]}")
    return r.json() if r.content else {}


def work(out: Path) -> int:
    import atlas_smoke as smoke

    alex = smoke.signed_in(PERSON)
    agent = Agent()
    me = _ok(agent.get("/api/me"), "agent /api/me")
    print(f"agent signed in as {me.get('username')!r}")

    # Alex holds no role on the seed project until bound (the OSS install: shared projects are
    # viewer, no implicit owner). The agent inherits the person's binding, capped at editor.
    alex_id = smoke.user_id(alex, PERSON)
    _ok(alex.write("PUT", f"/api/access/project/{PROJECT}/bindings", {"bindings": [
        {"principal_kind": "user", "principal_id": alex_id, "role": "owner"}]}), "bind")
    print(f"{PERSON} bound owner of {PROJECT}")

    ids: dict[str, str] = {}

    def create(body: dict, label: str) -> str:
        r = alex.write("POST", "/api/workitem", body)
        got = _ok(r, f"create {label}")
        ids[label] = got["id"]
        print(f"created {body['type']:<5} {got['id']:<26} {body['name']}")
        return got["id"]

    for e in EPICS:
        create({"type": "epic", "id": e["id"], "name": e["name"], "summary": e["summary"],
                "binding": e["binding"], "status": "pending"}, e["id"])
    for s in STORIES:
        create({"type": "story", "id": s["id"], "name": s["name"], "summary": s["summary"],
                "parent": s["parent"], "binding": s["binding"], "status": "pending",
                "acceptance": s.get("acceptance", [])}, s["id"])
    for t in TASKS:
        body = {"type": "task", "name": t["name"], "parent": t["parent"], "binding": t["binding"],
                "status": "pending", "priority": t.get("priority")}
        if t.get("summary"):
            body["summary"] = t["summary"]
        if t.get("assignee"):
            body["assignee"] = alex_id
        if t.get("depends_on"):
            body["depends_on"] = [ids[k] for k in t["depends_on"]]
        create(body, t["key"])

    for who, act, ref, arg in STEPS:
        nid = ids.get(ref, ref)
        actor = agent if who == "agent" else alex
        if act == "status":
            r = actor.write("POST", f"/api/node/{nid}/status", {"status": arg})
        elif act == "patch":
            r = actor.write("PATCH", f"/api/workitem/{nid}", arg)
        elif act == "comment":
            r = actor.write("POST", f"/api/workitem/{nid}/comments", {"body": arg})
        elif act == "propose":
            r = actor.write("POST", f"/api/workitem/{nid}/acceptance-proposal",
                            {"acceptance": arg["acceptance"], "note": arg["note"]})
        else:
            raise SystemExit(f"unknown step {act}")
        _ok(r, f"{who} {act} {nid}")
        print(f"{who:<5} {act:<8} {nid}")

    # What the screenshots rely on, checked here so a wrong demo fails before capture.
    mine = _ok(alex.get("/api/workitems", assignee="me", page_size=200,
                        status="pending,analyze,in_progress,blocked"), "my tasks")
    rows = [i for i in mine.get("items", []) if i.get("type") != "decision"]
    print(f"My tasks: {len(rows)} rows")
    if not 5 <= len(rows) <= 6:
        raise SystemExit(f"My tasks should hold 5-6 rows, has {len(rows)}")
    ev = _ok(alex.get("/api/events", node_id=ids["T2"]), "history")
    kinds = {((e.get("actor") or {}).get("kind")) for e in ev.get("items", [])}
    if not {"session", "token"} <= kinds:
        raise SystemExit(f"{ids['T2']} history should carry both a session and a token row: {kinds}")
    prop = _ok(alex.get(f"/api/workitem/{ids[PROPOSAL['on']]}/acceptance-proposal"), "proposal")
    if not (prop.get("pending") or {}).get("id"):
        raise SystemExit("no pending acceptance proposal")
    print("checks: My tasks rows, history by both, one pending proposal")

    out.write_text(json.dumps({"ids": ids, "targets": {k: ids[v] for k, v in SHOT_TARGETS.items()},
                               "person": PERSON, "agent": AGENT}, indent=2) + "\n", encoding="utf-8")
    print(f"ids -> {out}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", nargs="?", choices=("accounts", "work"), help="omit with --dry-run")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; touch nothing")
    ap.add_argument("--compose-dir", default=str(ROOT / "deploy" / "oss"))
    ap.add_argument("--out", default=None, help="the ids file (default SMOKE_DIR/demo-ids.json)")
    a = ap.parse_args(argv)
    problems = plan_problems()
    if a.dry_run:
        print("\n".join(plan_lines()))
        for p in problems:
            print("PLAN PROBLEM: " + p)
        return 1 if problems else 0
    if problems:
        print("\n".join("PLAN PROBLEM: " + p for p in problems), file=sys.stderr)
        return 1
    if not a.step:
        ap.error("name a step (accounts | work), or pass --dry-run")
    if a.step == "accounts":
        return accounts(Path(a.compose_dir))
    import atlas_smoke as smoke
    return work(Path(a.out) if a.out else smoke.DIR / "demo-ids.json")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
