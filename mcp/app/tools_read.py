"""Read-only verbs for the `atlas` MCP surface (TASK-1082/1083/1084).

Everything in this module is a THIN wrap of one `GET` route already exposed
by atlas-api (`backend/app.py`) -- no client-side projection, no rollup, no
reshaping. The board already does that work; a tool that reshapes the graph
becomes a second implementation of it, and the two would drift.

Same module-boundary rule as `mcp_server.py` (copied from an earlier
FastMCP service): no `from __future__ import annotations` anywhere in this file --
FastMCP inspects a tool's REAL signature to build its JSON-Schema, and
postponed annotation evaluation breaks that introspection.

Registration is a function, not decorators at import time, because this
package has three agents adding tool files in parallel (TASK-1082/1083/1084,
TASK-1085-ish write verbs, ...) and each owns exactly one `tools_*.py` plus
one `register(mcp)` call appended to `mcp_server.py` -- nobody else's file
moves, and a merge of three PRs is three independent hunks instead of one
shared list of `@mcp.tool()` decorators fighting over ordering.
"""

from typing import Any, Optional

from .client import client
from .envelope import AtlasError, envelope


# MINIMAL BY DEFAULT, AND REFUSED RATHER THAN CUT WHEN TOO BIG (TASK-1163).
#
# Until TASK-1163 this tool returned the board's payload unless the caller
# asked for less, so the agent that forgot was the one that paid: every
# node's authored prose, then -- with `prose=none` -- every bound workitem
# with its status and date. Called with no `focus` it returned the whole
# world. The board still wants all of that, so atlas-api's defaults stay
# full and the minimal defaults live HERE -- as the parameters this tool
# CHOOSES to send, nothing more.
#
# The limits are atlas-api's (`strict`, `max_bytes` on /api/graph), not
# this service's. They were first built as refusals in this file, which
# put a validation rule in a facade whose constraint is that it holds none
# (intent `facade-not-second-brain`), enforced for MCP callers only and
# tested outside CI. This file now only asks for them.
# Measured on live 2026-09-14: `atlas-backend` at these defaults is 1,868 B
# for 9 nodes; a minimal node is ~110 B and an edge ~90 B, so a
# TREE_NODE_LIMIT neighbourhood is ~20-26 KB, under TREE_BYTE_LIMIT.
TREE_NODE_LIMIT = 100
TREE_BYTE_LIMIT = 32_000
NODE_SEARCH_FAMILIES = "object,bridge"
NODE_SEARCH_PAGE_SIZE = 10
_TREE_DEFAULTS = {"depth": 1, "edges": "contains", "prose": "none",
                  "work": "none", "meta": "none"}


def register(mcp) -> None:
    """Declare every read-verb tool on `mcp`. Called once, from mcp_server.py."""

    # ----------------------------------------------------------------- #
    # atlas_get -- TASK-1082
    # ----------------------------------------------------------------- #
    def _atlas_get(id: str, prose: Optional[str], intents: Optional[str]) -> Any:
        params = {}
        if prose is not None:
            params["prose"] = prose
        if intents is not None:
            params["intents"] = intents
        return client.get(f"/api/node/{id}", params=params or None)

    @mcp.tool()
    def atlas_get(id: str, prose: Optional[str] = None,
                  intents: Optional[str] = None) -> dict:
        return envelope(_atlas_get)(id, prose, intents)

    # ----------------------------------------------------------------- #
    # atlas_tree -- TASK-1082, minimal-by-default since TASK-1163
    # ----------------------------------------------------------------- #
    def _atlas_tree(focus: Optional[str], depth: Optional[int], edges: Optional[str],
                    prose: Optional[str], work: Optional[str],
                    meta: Optional[bool]) -> Any:
        if not focus:
            raise AtlasError(
                "invalid_request",
                "atlas_tree needs `focus` -- a node id to centre on. For the shape of "
                "the whole world use atlas_outline, which returns every node as one "
                "indented line; then atlas_tree(focus=<id>) around the part you need.",
            )
        params = {
            "focus": focus,
            "depth": _TREE_DEFAULTS["depth"] if depth is None else depth,
            "prose": prose or _TREE_DEFAULTS["prose"],
            "work": work or _TREE_DEFAULTS["work"],
            "meta": "full" if meta else _TREE_DEFAULTS["meta"],
            "cap": TREE_NODE_LIMIT,
            "strict": 1,
            "max_bytes": TREE_BYTE_LIMIT,
        }
        edges = _TREE_DEFAULTS["edges"] if edges is None else edges
        if edges.strip().lower() != "all":
            params["edges"] = edges
        return client.get("/api/graph", params=params)

    @mcp.tool()
    def atlas_tree(focus: Optional[str] = None, depth: Optional[int] = None,
                   edges: Optional[str] = None, prose: Optional[str] = None,
                   work: Optional[str] = None, meta: Optional[bool] = None) -> dict:
        return envelope(_atlas_tree)(focus, depth, edges, prose, work, meta)

    # ----------------------------------------------------------------- #
    # atlas_outline
    # ----------------------------------------------------------------- #
    def _atlas_outline(root: Optional[str], families: Optional[str],
                       max_depth: Optional[int], names: Optional[bool]) -> Any:
        params: dict = {}
        if root is not None:
            params["root"] = root
        if families is not None:
            params["families"] = families
        if max_depth is not None:
            params["max_depth"] = max_depth
        if names:
            params["names"] = 1
        return client.get("/api/graph/outline", params=params or None)

    @mcp.tool()
    def atlas_outline(root: Optional[str] = None, families: Optional[str] = None,
                      max_depth: Optional[int] = None,
                      names: Optional[bool] = None) -> dict:
        return envelope(_atlas_outline)(root, families, max_depth, names)

    # ----------------------------------------------------------------- #
    # atlas_search -- TASK-1083
    # ----------------------------------------------------------------- #
    def _atlas_search(type: Optional[str], status: Optional[str], parent: Optional[str],
                       binding: Optional[str], owner: Optional[str], q: Optional[str],
                       page: Optional[int], page_size: Optional[int],
                       priority: Optional[str] = None, label: Optional[str] = None) -> Any:
        params: dict = {}
        if type is not None:
            params["type"] = type
        if status is not None:
            params["status"] = status
        if parent is not None:
            params["parent"] = parent
        if binding is not None:
            params["binding"] = binding
        if owner is not None:
            params["owner"] = owner
        if q is not None:
            params["q"] = q
        # STORY-oss-14: comma-separated, as atlas-api takes them.
        if priority is not None:
            params["priority"] = priority
        if label is not None:
            params["label"] = label
        if page is not None:
            params["page"] = page
        if page_size is not None:
            params["page_size"] = page_size
        return client.get("/api/workitems", params=params)

    @mcp.tool()
    def atlas_search(type: Optional[str] = None, status: Optional[str] = None,
                      parent: Optional[str] = None, binding: Optional[str] = None,
                      owner: Optional[str] = None, q: Optional[str] = None,
                      page: Optional[int] = None, page_size: Optional[int] = None,
                      priority: Optional[str] = None, label: Optional[str] = None) -> dict:
        return envelope(_atlas_search)(type, status, parent, binding, owner, q, page, page_size,
                                       priority, label)

    # ----------------------------------------------------------------- #
    # atlas_context -- ADR 0044 §0: "tell me about X" in one bounded call
    # ----------------------------------------------------------------- #
    def _atlas_context(about: str, **budgets: Any) -> Any:
        # Only what was given: the API owns every default and every bound, and says which part
        # a budget cut (`truncated`), so this stays a pass-through.
        params: dict = {"about": about}
        params.update({k: v for k, v in budgets.items() if v is not None})
        if params.get("intents_full") is not None:
            params["intents_full"] = 1 if params["intents_full"] else 0
        return client.get("/api/context", params=params)

    @mcp.tool()
    def atlas_context(about: str, mode: Optional[str] = None, families: Optional[str] = None,
                      seeds: Optional[int] = None, neighbours: Optional[int] = None,
                      impact_depth: Optional[int] = None, impact: Optional[int] = None,
                      intents: Optional[int] = None, intents_full: Optional[bool] = None,
                      decisions: Optional[int] = None, docs: Optional[int] = None) -> dict:
        return envelope(_atlas_context)(
            about, mode=mode, families=families, seeds=seeds, neighbours=neighbours,
            impact_depth=impact_depth, impact=impact, intents=intents, intents_full=intents_full,
            decisions=decisions, docs=docs)

    # ----------------------------------------------------------------- #
    # atlas_node_search -- TASK-1156
    # ----------------------------------------------------------------- #
    def _atlas_node_search(q: str, families: Optional[str], page: Optional[int],
                           page_size: Optional[int], mode: Optional[str] = None,
                           expand: Optional[bool] = None) -> Any:
        params: dict = {"q": q, "page_size": NODE_SEARCH_PAGE_SIZE if page_size is None else page_size}
        fam = NODE_SEARCH_FAMILIES if families is None else families
        if fam.strip().lower() != "all":
            params["families"] = fam
        if page is not None:
            params["page"] = page
        # ADR 0044 §5. THE AGENT DEFAULT IS HYBRID (owner decision, 2026-10-26, on STORY-atlas-context-measure's
        # numbers: full text found the target for 5 of 22 real questions, hybrid for 21, and both rank
        # an exact identifier first). /api/search itself keeps `fulltext` as its default, so the API,
        # the board and other callers are unchanged; `mode="fulltext"` here asks for today's search.
        # Where the add-on is off, the API answers full text and says so in `degraded`.
        params["mode"] = "hybrid" if mode is None else mode
        if expand:
            params["expand"] = 1
        return client.get("/api/search", params=params)

    @mcp.tool()
    def atlas_node_search(q: str, families: Optional[str] = None, page: Optional[int] = None,
                          page_size: Optional[int] = None, mode: Optional[str] = None,
                          expand: Optional[bool] = None) -> dict:
        return envelope(_atlas_node_search)(q, families, page, page_size, mode, expand)

    # ----------------------------------------------------------------- #
    # atlas_intents -- TASK-1084
    # ----------------------------------------------------------------- #
    def _atlas_intents(node_id: Optional[str], kind: Optional[str]) -> Any:
        params: dict = {}
        if node_id is not None:
            params["node_id"] = node_id
        if kind is not None:
            params["kind"] = kind
        return client.get("/api/intents", params=params)

    @mcp.tool()
    def atlas_intents(node_id: Optional[str] = None, kind: Optional[str] = None) -> dict:
        return envelope(_atlas_intents)(node_id, kind)

    # ----------------------------------------------------------------- #
    # atlas_doc_read -- TASK-1084
    # ----------------------------------------------------------------- #
    def _atlas_doc_read(node_id: str, path: str) -> Any:
        return client.get(f"/api/node/{node_id}/doc", params={"path": path})

    @mcp.tool()
    def atlas_doc_read(node_id: str, path: str) -> dict:
        return envelope(_atlas_doc_read)(node_id, path)

    # ----------------------------------------------------------------- #
    # atlas_capabilities -- TASK-1084
    # ----------------------------------------------------------------- #
    def _registered_tool_names() -> list:
        # `FastMCP.list_tools()` is async (it awaits into the underlying
        # ToolManager); calling it from inside a synchronous tool body would
        # need `asyncio.run()`, which can raise "cannot be called from a
        # running event loop" depending on how FastMCP dispatches sync tool
        # functions. `mcp._tool_manager.list_tools()` is the SAME data,
        # synchronously, one layer below the public FastMCP wrapper -- it is
        # what this reaches for instead. If a future `mcp` release renames
        # `_tool_manager`, this needs to move with it; nothing else in this
        # file touches FastMCP internals.
        return sorted(t.name for t in mcp._tool_manager.list_tools())

    def _atlas_capabilities() -> Any:
        # schemas=shared: each distinct attributes schema once instead of one
        # identical copy per node type (TASK-1157).
        vocabulary = client.get("/api/vocabulary", params={"schemas": "shared"})
        return {"vocabulary": vocabulary, "tools": _registered_tool_names()}

    # A node's STRUCTURE (TASK-1210): the map before the reading. A thin wrap
    # of GET /api/node/{id}/structure -- the endpoint selects the fields and
    # applies visibility to every linked id; nothing is reshaped here.
    def _atlas_structure(node_id: str, children_limit: Optional[int]) -> Any:
        params = {} if children_limit is None else {"children_limit": children_limit}
        return client.get(f"/api/node/{node_id}/structure", params=params)

    @mcp.tool()
    def atlas_structure(node_id: str, children_limit: Optional[int] = None) -> dict:
        return envelope(_atlas_structure)(node_id, children_limit)

    # How X is connected to Y (TASK-1298). A thin wrap of GET /api/graph/path:
    # the search, the caps and the visibility rule (the walk never passes
    # through a node the caller cannot see) are all the API's. Small by
    # construction rather than by a byte limit here: measured in
    # backend/verify_graph_path.py at ~400 B for one 2-hop path, ~900 B for
    # three, ~130 B a hop -- the 10-path, 8-hop ceiling is ~10 KB.
    def _atlas_path(from_id: str, to_id: str, max: Optional[int], edges: Optional[str],
                    hops: Optional[int]) -> Any:
        params: dict = {"from": from_id, "to": to_id}
        if max is not None:
            params["max"] = max
        if edges is not None:
            params["edges"] = edges
        if hops is not None:
            params["hops"] = hops
        return client.get("/api/graph/path", params=params)

    @mcp.tool()
    def atlas_path(from_id: str, to_id: str, max: Optional[int] = None,
                   edges: Optional[str] = None, hops: Optional[int] = None) -> dict:
        return envelope(_atlas_path)(from_id, to_id, max, edges, hops)

    # The ADR registry (ADR 0045). Thin wraps of GET /api/adrs and GET /api/adrs/{ref}:
    # the id scheme, the 409 for an ambiguous legacy number and the per-project
    # visibility are all the API's. A listing is pointers; the record has the body. Both
    # carry the API's `source` ("git" for a synced file, "atlas" for an Atlas-native record).
    def _atlas_adr_search(q: Optional[str], project: Optional[str], status: Optional[str],
                          number: Optional[int]) -> Any:
        params: dict = {}
        for k, v in (("q", q), ("project", project), ("status", status), ("number", number)):
            if v is not None:
                params[k] = v
        return client.get("/api/adrs", params=params or None)

    @mcp.tool()
    def atlas_adr_search(q: Optional[str] = None, project: Optional[str] = None,
                         status: Optional[str] = None, number: Optional[int] = None) -> dict:
        return envelope(_atlas_adr_search)(q, project, status, number)

    def _atlas_adr_get(id: str, project: Optional[str]) -> Any:
        return client.get(f"/api/adrs/{id}", params={"project": project} if project else None)

    @mcp.tool()
    def atlas_adr_get(id: str, project: Optional[str] = None) -> dict:
        return envelope(_atlas_adr_get)(id, project)

    @mcp.tool()
    def atlas_capabilities() -> dict:
        return envelope(_atlas_capabilities)()
