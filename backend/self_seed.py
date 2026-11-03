"""The `self` seed profile (TASK-1461): Atlas's own world model, and no work.

Selected with `ATLAS_SEED=self` (see `settings.SEED_PROFILES`); opt-in only, so an install that
does not choose it, and the homelab default, behave exactly as before. `main.startup()` calls
`seed_if_empty` after the node/edge type registries exist, and then seeds the `atlas-system`
handbook set as it does for `none` and `demo`.

WHAT IT WRITES. The data is `self_seed_data.py`, generated from a running Atlas by
`tools/export_self_seed.py` and reviewed in a pull request: the `atlas` project and the object
and bridge nodes under it (the API, the MCP service, the board and its panel, the graph, search
and retrieval, identity and visibility, ...), their summary/abstract/detail, their documents
(`docs[]` plus `node_doc`), the edges between them, and the intents a person marked `include`.
No workitem, comment, acceptance criterion, ADR, grant, account or event. A fresh install files
its own work against these nodes, and edits them like any other node (they are ordinary rows,
`origin='atlas'`).

It writes ONLY into a graph with no node at all, exactly like `demo_seed.py`, so a database
that already holds anything is never touched and a second boot adds nothing. `ATLAS_RESEED`
empties the graph first, so a reseed under this profile gives the profile again. The rows are
written directly: a seed is not a user mutation and records no `graph_event`. Boot opens no
file: the data is a Python module (backend/CLAUDE.md, "Boot is a read").

The root is marked `shared` for the same reason `system_node.seed` marks `atlas-system`: under
`ATLAS_RBAC=projects` an unmarked project takes the install's default visibility, `private` on
the OSS install, and nobody would hold a role on it.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models_auth import VISIBILITY_SHARED, NodeVisibility
from models_graph import Edge, Node, NodeDoc, NodeIntent, NodeType
from self_seed_data import PROFILE

ROOT = PROFILE["root"]


def seed_if_empty(s: Session) -> bool:
    """Insert the profile if the graph has no node. Returns whether it did."""
    if s.scalar(select(Node.id).limit(1)) is not None:
        return False
    known = set(s.scalars(select(NodeType.id)))
    for n in PROFILE["nodes"]:
        if n["type"] not in known:
            raise RuntimeError(f"self seed {n['id']}: node type {n['type']!r} is not registered")
    for n in PROFILE["nodes"]:
        s.add(Node(
            id=n["id"], type=n["type"], name=n["name"],
            summary=n.get("summary"), abstract=n.get("abstract"), detail=n.get("detail"),
            attributes={"docs": [{"path": d["path"], "kind": d["kind"], "title": d["title"],
                                  "format": d["format"]} for d in n["docs"]]},
            body=None, source=None, origin="atlas",
        ))
    s.flush()
    for n in PROFILE["nodes"]:
        for d in n["docs"]:
            s.add(NodeDoc(node_id=n["id"], path=d["path"], content=d["content"]))
    for e in PROFILE["edges"]:
        s.add(Edge(src=e["src"], dst=e["dst"], edge_type=e["edge_type"], origin="atlas"))
    position: dict[str, int] = {}
    for it in PROFILE["intents"]:
        pos = position.get(it["node_id"], 0)
        position[it["node_id"]] = pos + 1
        s.add(NodeIntent(node_id=it["node_id"], slug=it["slug"], kind=it["kind"],
                         title=it["title"], statement=it["statement"], source=None, position=pos))
    if s.get(NodeVisibility, ROOT) is None:
        s.add(NodeVisibility(node_id=ROOT, visibility=VISIBILITY_SHARED, set_by=None))
    s.flush()
    from attention import recompute_derived  # lazy, like the graph handlers
    recompute_derived(s)
    print(f"[atlas] seed: self profile, {len(PROFILE['nodes'])} nodes, {len(PROFILE['edges'])} edges, "
          f"{sum(len(n['docs']) for n in PROFILE['nodes'])} documents, {len(PROFILE['intents'])} intents")
    return True
