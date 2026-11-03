"""The `demo` seed profile (STORY-oss-07): one neutral project, one epic, two stories.

Selected with `ATLAS_SEED=demo` (see `settings.SEED_PROFILES`); `main.startup()` calls
`seed_if_empty` after the node/edge type registries exist. The default profile is still
`homelab` (`seed.py`, unchanged), and `none` seeds nothing.

It writes ONLY into a graph with no node at all, so a database that already holds work is
never touched, and a second boot adds nothing. `ATLAS_RESEED` empties the graph first, so a
reseed under this profile gives the demo graph again.

The shape follows the world model: the project is a world node, the epic WORKS ON it
(a workitem binds through `works_on`, never through `contains` from the world), and the
epic CONTAINS the two stories. The rows are written directly, the way `testseed.py` writes
its fixture: a seed is not a user mutation, so it records no `graph_event`.

The content is deliberately neutral: no person, no organisation, no host, no domain.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models_graph import Edge, Node

PROJECT_ID = "demo-project"
EPIC_ID = "EPIC-demo-getting-started"
STORY_IDS = ("STORY-demo-explore-the-board", "STORY-demo-file-first-work")

# (id, type, name, status or None, body)
NODES: tuple[tuple[str, str, str, str | None, str], ...] = (
    (PROJECT_ID, "project", "Demo project", None,
     "An example project, created by the demo seed. Rename it, or delete it once "
     "your own projects exist."),
    (EPIC_ID, "epic", "Get started with Atlas", "in_progress",
     "An example epic. It works on the demo project and contains two stories."),
    (STORY_IDS[0], "story", "Explore the board", "in_progress",
     "Open the board, select the demo project and look at the work bound to it."),
    (STORY_IDS[1], "story", "File the first piece of work", "pending",
     "Create a story or a task of your own and bind it to a project."),
)

# (src, edge_type, dst)
EDGES: tuple[tuple[str, str, str], ...] = (
    (EPIC_ID, "works_on", PROJECT_ID),
    (EPIC_ID, "contains", STORY_IDS[0]),
    (EPIC_ID, "contains", STORY_IDS[1]),
)


def seed_if_empty(s: Session) -> bool:
    """Insert the demo graph if the graph has no node. Returns whether it did.

    Assumes the node_type and edge_type registries are already seeded (startup does that
    first). Recomputes the derived columns, which commits, as every write path does.
    """
    if s.scalar(select(Node.id).limit(1)) is not None:
        return False
    for nid, ntype, name, status, body in NODES:
        s.add(Node(
            id=nid, type=ntype, name=name,
            attributes={} if status is None else {"status": status},
            body=body, source=None, origin="atlas",
        ))
    s.flush()
    for src, edge_type, dst in EDGES:
        s.add(Edge(src=src, dst=dst, edge_type=edge_type, origin="atlas"))
    s.flush()
    from attention import recompute_derived  # lazy, like the graph handlers
    recompute_derived(s)
    print(f"[atlas] seed: demo profile, {len(NODES)} nodes and {len(EDGES)} edges")
    return True
