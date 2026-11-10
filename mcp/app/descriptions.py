"""Every tool's MCP description, in one place, so they can be read and reviewed together.

FastMCP sends a tool's description to every calling agent, every session, whether or not it
ever calls the tool. So each one here says what the tool is for and the rules that do harm
when missed (an irreversible delete, a field that replaces wholesale), and nothing else.
The rest -- every parameter, default, cap and refusal -- is the tools guide, the atlas-mcp
node's document `docs/nodes/atlas-mcp/tools.md`, which an agent reads only when it needs it.
`atlas_doc_read`'s description names that path; the others end in MORE.

Changing what a tool does means changing its entry here AND its section in the guide.
`mcp_server.py` looks each registered tool up by name and refuses to register one without
an entry; `verify_tool_descriptions.py` holds the size budget and checks the two lists match.
"""

MORE = " More: tools guide."

DESCRIPTIONS: dict[str, str] = {
    # ---- read
    "atlas_health":
        "Is atlas-api up? Returns /api/health: node, edge and event counts as a plausibility check, "
        "plus token, undocumented-asset, name, edge-type and certificate warnings and the deploy "
        "status (normally all empty).",
    "atlas_outline":
        "The contains tree as indented text, one line per node: what exists. Defaults to the "
        "world (families=object,bridge); scope with root=, add names=true." + MORE,
    "atlas_context":
        "Tell me about X, in one call: the best-matching nodes, their neighbours, what depends on "
        "them, the intents that apply, related decisions and docs to read next. Pointers within "
        "budgets; each part says when it was cut. Ask in your own words, Swedish works. Start "
        "here for an open question." + MORE,
    "atlas_node_search":
        "Which world node knows about X? Semantic + full-text search (mode=\"hybrid\", the default) "
        "over names, prose and served docs: ask in your own words or in Swedish, or give an exact "
        "name. Returns pointers (id, summary, snippet), not content. mode=\"fulltext\" for word "
        "matching only; expand=true adds graph neighbours." + MORE,
    "atlas_search":
        "List and filter workitems (the types atlas_capabilities lists) by type, status, parent, binding, "
        "priority, label or q. "
        "A listing without acceptance or bindings; page until you have `total`." + MORE,
    "atlas_structure":
        "Where a node sits, without prose: ancestors, children with their child counts, "
        "depends_on both ways, binding. Read it before atlas_get." + MORE,
    "atlas_get":
        "One node, complete: prose, acceptance, result, parents, bindings, dependencies and "
        "intents, own and inherited (the constraints on changing it). To only orient, ask for "
        "less: prose=none|summary|abstract, intents=own|none." + MORE,
    "atlas_tree":
        "A bounded neighbourhood around one node (focus is required). Minimal by default: "
        "depth=1, edges=contains, prose=none, work=none. Raise one at a time." + MORE,
    "atlas_path":
        "How two nodes connect: up to `max` shortest paths (default 3), each hop with its "
        "edge_type and direction. `edges` limits which edge types are walked." + MORE,
    "atlas_intents":
        "Intents where they are authored (own only). A node's inherited intents come from "
        "atlas_get." + MORE,
    "atlas_doc_read":
        "Read a node's document; `path` must be one of the node's attributes.docs[].path, "
        "which atlas_get lists. The tools guide, every tool in detail: node atlas-mcp, path "
        "docs/nodes/atlas-mcp/tools.md.",
    "atlas_adr_search":
        "Find architecture decision records by q (words, an id like AT001, or a legacy number), "
        "project, status or number. Pointers with id, project and source (git or atlas), not "
        "the text." + MORE,
    "atlas_adr_get":
        "One decision record by id (AT001), with status, date, source (a git file, or recorded "
        "in Atlas), links both ways and the markdown. "
        "A legacy number held by two projects is refused with the candidates; add project=." + MORE,
    "atlas_capabilities":
        "This deployment's legal node types, edge types, statuses, workitem types and intent "
        "kinds, and this server's tool list. About 25 KB." + MORE,
    # ---- work
    "atlas_file_work":
        "Create a workitem (a type atlas_capabilities lists) with its acceptance, parent, binding, "
        "depends_on, priority (low|medium|high|critical) and labels in one transaction. A task's id is minted; an epic or story needs a slug "
        "id. `body` is the spec, never a result. Suggest a binding, do not guess one. "
        # Decisions are ADR 0048's planned workitem type; no MCP tool files them yet. The ADR
        # number stays here, not in the text: verify_tool_descriptions.py refuses ticket ids.
        "Decisions are not filed here; their tool is not built yet." + MORE,
    "atlas_update_work":
        "Update a workitem. status, result, priority, labels and parent (moves it) are plain "
        "(null clears, omitted leaves). body, acceptance, depends_on and binding REPLACE WHOLESALE and need "
        "set_<field>=True. Returns the prior values. Acceptance needs a person: "
        "propose_acceptance=True (approved on the board), or approval={by,quote} where the "
        "install allows attested writes." + MORE,
    # ---- graph writes
    "atlas_node_write":
        "Create a node (needs type and name) or patch the fields given. `attributes` merge by "
        "default; attributes_mode=\"replace\" overwrites them. key= sets a project's task-id "
        "prefix." + MORE,
    "atlas_edge_write":
        "Create one edge between two existing nodes. Not for nesting workitems: that is "
        "atlas_file_work." + MORE,
    "atlas_edge_delete":
        "Delete one edge by id. Nothing else changes.",
    "atlas_doc_write":
        "Write a node document (content=) and/or publish it (publish=True); pass both to "
        "publish safely. `path` starts with docs/nodes/." + MORE,
    "atlas_intent_write":
        "Create an intent (node_id, slug, kind, title, statement) or patch one (intent_id). "
        "An intent is still true when all open work is done; if not, file a workitem." + MORE,
    "atlas_intent_delete":
        "Delete one node intent by its integer id.",
    "atlas_delete":
        "PERMANENT delete of a node, edge, doc or intent: no undo. A node delete cascades and "
        "needs expected_edge_count; call once without it and the refusal gives it." + MORE,
    # ---- comments (tools_comments.py)
    "atlas_comments":
        "The comments on one workitem, oldest first, each with its author and id.",
    "atlas_comment_write":
        "Add a comment to a workitem (id, body; markdown), or edit one of your own "
        "(comment_id, body). The comment names you as the author. Deleting is done on the board." + MORE,
    # ---- the first-run setup guide (tools_setup.py)
    "atlas_setup_guide":
        "Setup guide: no answers gives the next topic; topic+answers records a Proposed ADR. "
        "Ask the person; never answer for them." + MORE,
    # ---- session (the procedure itself is in Atlas, see tools_session.py)
    "atlas_save_work":
        "Sign-off: your writes since `since`, and the procedure for recording this "
        "conversation's work in Atlas.",
}
