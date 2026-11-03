"""The curated verify fixture — a small graph that contains every case the rules need.

    import testseed
    seed = testseed.build(session)      # inserts nodes/edges, recomputes derived
    testseed.self_check(session)        # every declared case is really present

WHY THIS EXISTS. Until now, twelve `verify_*.py` scripts got a populated
database the only way that was available when they were written: by importing
the 309 markdown files in `C:\\Projects\\AgentPlanningSetup\\backlog`. That was
never a deliberate choice about test data. It has three consequences, and all
three are biting:

  1. **It pins the suite to one Windows workstation.** The server cannot even
     clone that repo (its deploy key is scoped to `project-atlas`), so the
     suite cannot run where the Docker and the Postgres already are.
  2. **The corpus is frozen.** Workitems have been Postgres-native since
     TASK-1060; TASK-1063 onward were filed through the API and have no files
     at all. The markdown stopped at TASK-1062 and the gap widens with every
     ticket.
  3. **TASK-1059 deletes it**, which would stop those scripts running at all.

But the deeper problem is what it does to the assertions. A test that names
`TASK-825` breaks when somebody closes `TASK-825` — and then it is "stale", and
then it is waved through. TASK-1068 found six such assertions in one script;
the sixth was not stale at all, it was a real defect (a story `done` with four
children still open) that had been dismissed for weeks because its neighbours
were noisy. **Pinning production rows makes a failing test ambiguous**, and an
ambiguous test gets explained away.

So: a fixture engineered to contain each case exactly once, with ids that could
not possibly be mistaken for real work (`TASK-90xx`, `*-seed-*`). A failure
against this graph means the rule broke. It cannot mean somebody closed a
ticket.

WHAT THIS IS NOT FOR. Seven scripts test the file->Postgres path itself —
`verify_workitem_import`, `verify_result_split`, `verify_result_migration`,
`verify_world_import`, `verify_node_docs`, `verify_arrival` and its selftest.
Their SUBJECT is the import, so they need real files and must keep reading
them. They also expire with TASK-1059. Do not point them here.

THE FAILURE MODE THIS MODULE GUARDS AGAINST. A curated fixture rots in a way a
real corpus does not: someone tightens a rule, the case that exercised it stops
being exercised, and the suite goes quietly green over a hole. `self_check()`
exists for exactly that. It re-derives the graph and asserts every case in
`CASES` is genuinely present and genuinely distinct — so a seed that has
stopped covering something fails loudly here, at the fixture, instead of
silently everywhere else. Add a case to `CASES` when you add one to the graph.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

import node_types
from attention import recompute_derived
from models_graph import AcceptanceCriterion, Edge, Node, NodeDoc

# --------------------------------------------------------------------------- #
# The cases this fixture promises to contain. `self_check` proves each one.
# Keyed by the rule being exercised, valued by what must be observably true.
# --------------------------------------------------------------------------- #
CASES: dict[str, str] = {
    "done-leaf-green": "a done task is green and 100% complete",
    "pending-leaf-grey": "a pending task that blocks nothing is grey, not red",
    "blocked-leaf-red": "a blocked task is red from its own state",
    "cancelled-is-terminal": "a cancelled task is closed — it does not hold its parent open",
    "rollup-green-over-grey": "a parent with only green and grey children is green (grey excluded from worst)",
    "rollup-worst-child-red": "a parent with a red child is red",
    "fully-done-subtree": "a parent whose children are all terminal is green and 100% complete",
    "partial-completion": "completion is strictly between 0 and 1 where work is unfinished",
    "attention-count-nonzero": "at least one node counts red/orange descendants",
    "attention-count-zero-on-clean": "a clean parent counts zero, so the count means something",
    "frontier-red": "in a dependency chain, exactly one node is actionable now",
    "behind-frontier-orange": "a node that blocks work but waits on its own deps is orange, not red",
    "chain-tail-no-urgency": "the LAST node in a chain blocks nothing, so it carries no urgency at all",
    "second-goal-frontier": "a red frontier under the OTHER epic, so deactivating one goal is observable",
    "done-carries-no-urgency": "no terminal node carries urgency",
    "closed-parent-has-no-open-children": "the fixture itself satisfies the invariant it tests",
    "two-goals": "two epics, so goal config and deactivating ONE goal are both exercisable",
    "unbound-workitem": "a workitem with no world binding at all",
    "direct-binding": "a workitem bound straight to a world node",
    "inherited-binding": "a workitem bound only through its parent story",
    "acceptance-criteria": "a workitem with rows in acceptance_criterion",
    "documented-world-node": "a world node with a docs[] allowlist entry AND the matching "
                             "node_doc row, so GET /api/node/{id}/doc is exercisable",
    "double-binding-violation": "a workitem with TWO works_on bindings — the pre-existing "
                                "ADR 0028 §1 violation the write API must tolerate without worsening",
}


class SeedIncomplete(RuntimeError):
    """The fixture no longer contains a case it promises. See `CASES`."""


@dataclass(frozen=True)
class Seed:
    """Named handles, so a script never has to type a raw id.

    Scripts should say `seed.blocked_leaf`, not `"TASK-9008"` — the whole point
    is that the assertion names the CASE, not a row.
    """
    epic_platform: str = "EPIC-seed-platform"
    epic_property: str = "EPIC-seed-property"

    story_all_done: str = "STORY-seed-all-done"
    story_mixed: str = "STORY-seed-mixed"
    story_blocked: str = "STORY-seed-blocked-child"
    story_chain: str = "STORY-seed-chain"
    story_open_parent: str = "STORY-seed-open-parent"
    story_property: str = "STORY-seed-property-work"

    done_leaf: str = "TASK-9001"
    cancelled_leaf: str = "TASK-9003"
    pending_leaf: str = "TASK-9006"          # blocks nothing -> grey
    blocked_leaf: str = "TASK-9008"
    chain_frontier: str = "TASK-9010"        # no incomplete deps -> actionable
    chain_second: str = "TASK-9011"
    chain_third: str = "TASK-9012"
    chain_last: str = "TASK-9013"        # still BLOCKS the tail, so still orange
    chain_tail: str = "TASK-9023"        # blocks nobody -> no urgency at all
    second_goal_frontier: str = "TASK-9021"
    unbound_leaf: str = "TASK-9006"          # no works_on edge anywhere above it
    directly_bound_leaf: str = "TASK-9004"
    doubly_bound_leaf: str = "TASK-9020"    # two works_on edges: an ADR 0028 §1 violation
    criteria_leaf: str = "TASK-9004"

    world_system: str = "seed-world-system"
    world_service: str = "seed-world-service"
    world_component: str = "seed-world-component"
    documented_doc_path: str = "docs/nodes/seed-world-service/architecture.md"


SEED = Seed()

# (id, type, name, status) — status None means the node carries no status key.
_WORKITEMS: list[tuple[str, str, str, str | None]] = [
    (SEED.epic_platform, "epic", "Seed platform epic", "in_progress"),
    (SEED.epic_property, "epic", "Seed property epic", "in_progress"),

    # Fully terminal: 2 done + 1 cancelled. Closed parent, no open children.
    (SEED.story_all_done, "story", "Everything under here is finished", "done"),
    ("TASK-9001", "task", "A finished task", "done"),
    ("TASK-9002", "task", "Another finished task", "done"),
    ("TASK-9003", "task", "An abandoned task", "cancelled"),

    # Green + grey children only: the rollup fixture.
    (SEED.story_mixed, "story", "Partly done, nothing wrong", "in_progress"),
    ("TASK-9004", "task", "Done, and bound to a world node", "done"),
    ("TASK-9005", "task", "Also done", "done"),
    ("TASK-9006", "task", "Not started, blocks nothing, bound to nothing", "pending"),

    # A red child, so worst-child rollup and attention_count have something real.
    (SEED.story_blocked, "story", "Something here is blocked", "in_progress"),
    ("TASK-9007", "task", "Done", "done"),
    ("TASK-9008", "task", "Blocked on something external", "blocked"),

    # A dependency chain: exactly one actionable frontier, the rest behind it.
    (SEED.story_chain, "story", "A chain of dependent work", "in_progress"),
    ("TASK-9010", "task", "First in the chain — actionable now", "pending"),
    ("TASK-9011", "task", "Second — waits on the first", "pending"),
    ("TASK-9012", "task", "Third — waits on the second", "pending"),
    ("TASK-9013", "task", "Fourth — waits on the third, blocks the fifth", "pending"),
    # The chain needs a node that blocks NOBODY, or "behind the frontier is
    # orange" cannot be told apart from "at the end of the chain". Four links
    # carry urgency; this one is an ordinary not-started leaf.
    ("TASK-9023", "task", "Last — waits on the fourth, blocks nothing", "pending"),

    # An open parent WITH open children, which is legal and must stay legal.
    (SEED.story_open_parent, "story", "Open, with work still to do", "in_progress"),
    ("TASK-9014", "task", "Still to do", "pending"),
    # Bound to TWO world nodes. ADR 0028 §1 says a workitem has at most one
    # binding, and the live graph violates it on a handful of rows that predate
    # the rule. The write API has to TOLERATE those without making them worse,
    # so the suite needs one to push against -- it used to push against TASK-850,
    # a real ticket, which is precisely the coupling this fixture removes.
    ("TASK-9020", "task", "Bound twice, before the rule existed", "pending"),
    ("TASK-9015", "task", "Finished", "done"),

    # Second epic, so goal config has two goals to switch independently.
    (SEED.story_property, "story", "Work under the second goal", "in_progress"),
    ("TASK-9016", "task", "Not started, blocks nothing", "pending"),
    # A dependency pair under the SECOND epic. Without it, every red-urgency
    # node in the fixture sits under one goal, and "deactivate goal A, watch
    # goal B keep its urgency" cannot be tested at all.
    ("TASK-9021", "task", "Actionable now, under the second goal", "pending"),
    ("TASK-9022", "task", "Waits on TASK-9021", "pending"),
    ("TASK-9017", "task", "Finished", "done"),
]

_WORLD: list[tuple[str, str, str]] = [
    (SEED.world_system, "system", "Seed world system"),
    (SEED.world_service, "service", "Seed world service"),
    (SEED.world_component, "component", "Seed world component"),
]

_CONTAINS: list[tuple[str, str]] = [
    (SEED.epic_platform, SEED.story_all_done),
    (SEED.epic_platform, SEED.story_mixed),
    (SEED.epic_platform, SEED.story_blocked),
    (SEED.epic_platform, SEED.story_chain),
    (SEED.epic_platform, SEED.story_open_parent),
    (SEED.epic_property, SEED.story_property),

    (SEED.story_all_done, "TASK-9001"),
    (SEED.story_all_done, "TASK-9002"),
    (SEED.story_all_done, "TASK-9003"),
    (SEED.story_mixed, "TASK-9004"),
    (SEED.story_mixed, "TASK-9005"),
    (SEED.story_mixed, "TASK-9006"),
    (SEED.story_blocked, "TASK-9007"),
    (SEED.story_blocked, "TASK-9008"),
    (SEED.story_chain, "TASK-9010"),
    (SEED.story_chain, "TASK-9011"),
    (SEED.story_chain, "TASK-9012"),
    (SEED.story_chain, "TASK-9013"),
    (SEED.story_chain, "TASK-9023"),
    (SEED.story_open_parent, "TASK-9014"),
    (SEED.story_open_parent, "TASK-9015"),
    (SEED.story_open_parent, "TASK-9020"),
    (SEED.story_property, "TASK-9016"),
    (SEED.story_property, "TASK-9017"),
    (SEED.story_property, "TASK-9021"),
    (SEED.story_property, "TASK-9022"),

    (SEED.world_system, SEED.world_service),
    (SEED.world_service, SEED.world_component),
]

# src depends on dst: src cannot start until dst is done.
_DEPENDS_ON: list[tuple[str, str]] = [
    ("TASK-9011", "TASK-9010"),
    ("TASK-9012", "TASK-9011"),
    ("TASK-9013", "TASK-9012"),
    ("TASK-9023", "TASK-9013"),
    ("TASK-9022", "TASK-9021"),
]

# Direct binding on a task; story-level binding that TASK-9010..9013 inherit
# rather than declare. TASK-9006 is deliberately reachable from neither.
_WORKS_ON: list[tuple[str, str]] = [
    ("TASK-9004", SEED.world_service),
    (SEED.story_chain, SEED.world_component),
    ("TASK-9020", SEED.world_service),
    ("TASK-9020", SEED.world_component),
]

SEED_DOC_CONTENT = (
    "# Seed world service — architecture\n\nFixture documentation. Byte-for-byte comparison depends on this text, so edit it and the doc-route check moves with you.\n"
)

_ACCEPTANCE: dict[str, list[str]] = {
    "TASK-9004": [
        "The first criterion is checkable by someone who was not in the conversation",
        "The second criterion is a different sentence, so ordering is observable",
    ],
}


def build(s: Session, *, recompute: bool = True) -> Seed:
    """Insert the fixture into an EMPTY graph and return the handles.

    Assumes `app.startup()` (or `create_all` + the node_type seeds) has already
    run — the same assumption every verify script already makes. Nodes are
    `origin="atlas"` with `source=None`, which is the honest description: they
    came from this module, not from a file. Any check that wants "every node
    traces to a file" must scope itself to git-origin rows; that stopped being
    a property of the graph when Postgres became authoritative.
    """
    node_types.ensure_backlog_node_types(s)
    node_types.ensure_world_node_types(s)
    node_types.ensure_edge_types(s)

    for nid, ntype, name, status in _WORKITEMS:
        s.add(Node(
            id=nid, type=ntype, name=name,
            attributes={} if status is None else {"status": status},
            body=f"Fixture node {nid} — see backend/testseed.py.",
            owner="seed", source=None, origin="atlas",
        ))
    for nid, ntype, name in _WORLD:
        # The documented node carries a `docs[]` allowlist entry. The route
        # refuses any path not listed here even when a node_doc row exists, so
        # the attribute and the row are two halves of one case.
        attrs = {}
        if nid == SEED.world_service:
            attrs = {"docs": [{"path": SEED.documented_doc_path, "kind": "architecture",
                               "title": "Seed world service architecture", "format": "md"}]}
        s.add(Node(
            id=nid, type=ntype, name=name, attributes=attrs,
            body=f"Fixture world node {nid} — see backend/testseed.py.",
            owner="seed", source=None, origin="atlas",
        ))
    s.flush()

    for src, dst in _CONTAINS:
        s.add(Edge(src=src, dst=dst, edge_type="contains", origin="atlas"))
    for src, dst in _DEPENDS_ON:
        s.add(Edge(src=src, dst=dst, edge_type="depends_on", origin="atlas"))
    for src, dst in _WORKS_ON:
        s.add(Edge(src=src, dst=dst, edge_type="works_on", origin="atlas"))
    s.add(NodeDoc(node_id=SEED.world_service, path=SEED.documented_doc_path,
                  content=SEED_DOC_CONTENT))
    for nid, texts in _ACCEPTANCE.items():
        for i, text in enumerate(texts):
            s.add(AcceptanceCriterion(node_id=nid, position=i, text=text))
    s.flush()

    if recompute:
        recompute_derived(s)   # commits, exactly as every write path does
    return SEED


# --------------------------------------------------------------------------- #
# self_check — the guard against a fixture that has quietly stopped covering
# something. Every assertion below names a key in CASES.
# --------------------------------------------------------------------------- #
def self_check(s: Session, *, verbose: bool = True) -> None:
    from attention import derive

    d = derive(s)
    nodes = {n.id: n for n in s.scalars(select(Node)).all()}
    missing: list[str] = []

    def require(case: str, ok: bool, detail: str = "") -> None:
        if not ok:
            missing.append(f"{case}: {CASES[case]}" + (f" — {detail}" if detail else ""))

    def A(nid: str) -> str | None:
        return d[nid]["attention"] if nid in d else None

    def U(nid: str) -> str | None:
        return d[nid]["urgency"] if nid in d else None

    def status(nid: str) -> str | None:
        return (nodes[nid].attributes or {}).get("status") if nid in nodes else None

    TERMINAL = {"done", "accepted", "closed", "cancelled"}

    require("done-leaf-green",
            A(SEED.done_leaf) == "green" and d[SEED.done_leaf]["completion"] == 1.0,
            f"attention={A(SEED.done_leaf)} completion={d[SEED.done_leaf]['completion']}")
    require("pending-leaf-grey", A(SEED.pending_leaf) == "grey", f"got {A(SEED.pending_leaf)}")
    require("blocked-leaf-red", A(SEED.blocked_leaf) == "red", f"got {A(SEED.blocked_leaf)}")
    require("cancelled-is-terminal", status(SEED.cancelled_leaf) == "cancelled")

    mixed_kids = [dst for src, dst in _CONTAINS if src == SEED.story_mixed]
    require("rollup-green-over-grey",
            A(SEED.story_mixed) == "green"
            and {A(k) for k in mixed_kids} == {"green", "grey"},
            f"story={A(SEED.story_mixed)} children={sorted({A(k) for k in mixed_kids})}")
    require("rollup-worst-child-red", A(SEED.story_blocked) == "red", f"got {A(SEED.story_blocked)}")
    require("fully-done-subtree",
            A(SEED.story_all_done) == "green" and d[SEED.story_all_done]["completion"] == 1.0,
            f"attention={A(SEED.story_all_done)} completion={d[SEED.story_all_done]['completion']}")
    require("partial-completion", 0.0 < d[SEED.story_mixed]["completion"] < 1.0,
            f"got {d[SEED.story_mixed]['completion']}")

    require("attention-count-nonzero", max(v["attention_count"] for v in d.values()) > 0)
    require("attention-count-zero-on-clean", d[SEED.story_all_done]["attention_count"] == 0,
            f"got {d[SEED.story_all_done]['attention_count']}")

    chain = [SEED.chain_frontier, SEED.chain_second, SEED.chain_third,
             SEED.chain_last, SEED.chain_tail]
    reds = [c for c in chain if U(c) == "red"]
    require("frontier-red", reds == [SEED.chain_frontier],
            f"red-urgency nodes in the chain: {reds}")
    require("second-goal-frontier", U(SEED.second_goal_frontier) == "red",
            f"got {U(SEED.second_goal_frontier)}")
    # Only the MIDDLE of the chain is orange. This distinction is not pedantry:
    # urgency means "blocks active-goal work", so it is earned by what depends on
    # you, not by your position. TASK-9011 and TASK-9012 each block a successor
    # while waiting on a predecessor -> orange. The tail blocks nobody, so it is
    # an ordinary not-started leaf and carries no urgency at all. The first draft
    # of this fixture asserted "everything behind the frontier is orange" and
    # self_check caught it immediately, which is the entire reason self_check
    # exists.
    middle = chain[1:-1]
    require("behind-frontier-orange",
            all(U(c) == "orange" for c in middle),
            f"urgencies in the middle of the chain: {[U(c) for c in middle]}")
    require("chain-tail-no-urgency",
            U(SEED.chain_tail) == "none" and A(SEED.chain_tail) == "grey",
            f"tail urgency={U(SEED.chain_tail)} attention={A(SEED.chain_tail)}")

    terminal_with_urgency = [nid for nid, v in d.items()
                             if status(nid) in TERMINAL and v["urgency"] != "none"]
    require("done-carries-no-urgency", not terminal_with_urgency, f"offenders: {terminal_with_urgency}")

    kids_of: dict[str, list[str]] = {}
    for src, dst in _CONTAINS:
        kids_of.setdefault(src, []).append(dst)
    contradictions = [p for p in nodes
                      if status(p) in {"done", "accepted", "closed"}
                      and any(status(k) not in TERMINAL for k in kids_of.get(p, []))]
    require("closed-parent-has-no-open-children", not contradictions, f"offenders: {contradictions}")

    epics = {n.id for n in nodes.values() if n.type == "epic"}
    require("two-goals", len(epics) >= 2, f"epics: {sorted(epics)}")

    bound_srcs = {src for src, _ in _WORKS_ON}
    require("direct-binding", SEED.directly_bound_leaf in bound_srcs)
    require("inherited-binding", SEED.story_chain in bound_srcs and SEED.chain_frontier not in bound_srcs)
    ancestors_of_unbound = {p for p, kids in kids_of.items() if SEED.unbound_leaf in kids}
    require("unbound-workitem",
            SEED.unbound_leaf not in bound_srcs and not (ancestors_of_unbound & bound_srcs),
            "the unbound task is reachable from a binding")

    double = sorted(dst for src, dst in _WORKS_ON if src == SEED.doubly_bound_leaf)
    require("double-binding-violation", len(double) == 2, f"bindings: {double}")

    doc_row = s.scalar(select(NodeDoc).where(NodeDoc.node_id == SEED.world_service))
    doc_attr = ((nodes[SEED.world_service].attributes or {}).get("docs") or [])
    require("documented-world-node",
            doc_row is not None and any(d.get("path") == SEED.documented_doc_path
                                        for d in doc_attr),
            f"row={doc_row is not None} docs[]={doc_attr}")

    n_criteria = len(s.scalars(select(AcceptanceCriterion)).all())
    require("acceptance-criteria", n_criteria >= 2, f"got {n_criteria}")

    if missing:
        raise SeedIncomplete(
            f"{len(missing)} of {len(CASES)} declared case(s) are NOT present in the fixture:\n  "
            + "\n  ".join(missing)
            + "\n\nThe fixture stopped covering something. Fix the graph in testseed.py "
              "(or, if the case is genuinely obsolete, remove it from CASES and say why)."
        )
    if verbose:
        print(f"[testseed] {len(nodes)} nodes, all {len(CASES)} declared cases present")


if __name__ == "__main__":
    import base64
    import os
    import sys

    import testdb

    # Same incantation every verify script uses: app -> auth -> passwords
    # refuses to import without a pepper, and deliberately has no
    # generated-if-missing fallback. A throwaway one is correct here; there are
    # no stored passwords in a fixture database to invalidate.
    os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
    os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
    os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
    os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")

    try:
        db = testdb.start()
    except testdb.TestDatabaseUnavailable as e:
        print(f"FATAL: {e}")
        sys.exit(1)
    os.environ["ATLAS_DB_URL"] = db.url
    import app  # noqa: E402  — must follow ATLAS_DB_URL

    app.startup()
    with app.session() as s:
        build(s)
    with app.session() as s:
        self_check(s)
    print("[testseed] OK")
