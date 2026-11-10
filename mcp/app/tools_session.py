"""The sign-off entry point for the `atlas` MCP surface: saving a session's work.

`atlas_save_work` is a THIN wrap of one GET route already exposed by atlas-api,
`/api/events?actor=me`: the graph writes the calling credential made since a
time, newest first, exactly as atlas-api returns them -- no grouping, no
projection, nothing decided here (intent `facade-not-second-brain`).

It adds one constant: `procedure`, a POINTER to the sign-off procedure, which is
a document on the `skill-signoff` node in Atlas. The procedure therefore lives
in Atlas and changes without a deploy, and every client gets the same steps
whether or not it has the seat-toolkit plugin. Pointer, not payload (intent
`context-minimal`): the agent reads it with `atlas_doc_read` once.

`actor=me` is the CALLER'S credential: each caller sends its own Bearer token,
which the request carries through to atlas-api (caller_auth.py, client.py), so
`writes` is the writes that token made, not every caller's. One token is still not
one conversation -- two sessions on the same credential see each other's writes --
so the procedure document still has the agent check each write against its own
conversation. Only the opt-in shared fallback (ATLAS_MCP_ALLOW_SHARED_TOKEN) puts
several callers on one identity again.

The DESCRIPTION (app/descriptions.py) is one sentence: how and when to use the tool lives in the
procedure document it points at, and in the callers' own instructions (the seat-toolkit `signoff`
skill), not in text every session pays for.

`signoff` is the same thing as an MCP PROMPT (clients list it as a command, e.g. `/mcp__atlas__signoff`
in Claude Code): one GET of that procedure document, returned as the instruction, so a client with nothing
installed still gets the steps from the one copy in Atlas. Prompts carry no tool description, so they cost
the tool budget nothing; they run when invoked, never on their own.

Same module-boundary rules as tools_read.py: no `from __future__ import
annotations` (FastMCP reads the real signature), registration via
`register(mcp)` and one line in mcp_server.py.
"""

from typing import Optional

from .client import client
from .envelope import envelope

PROCEDURE = {"node_id": "skill-signoff", "path": "docs/nodes/skill-signoff/procedure.md"}
DEFAULT_PAGE_SIZE = 50


def register(mcp) -> None:
    @mcp.tool()
    def atlas_save_work(since: str, page: Optional[int] = None,
                        page_size: Optional[int] = None) -> dict:
        def call() -> dict:
            params = {"actor": "me", "since": since,
                      "page_size": page_size if page_size is not None else DEFAULT_PAGE_SIZE}
            if page is not None:
                params["page"] = page
            return {"procedure": dict(PROCEDURE), "writes": client.get("/api/events", params=params)}
        return envelope(call)()

    @mcp.prompt(name="signoff", description="Sign off: record this conversation's work in Atlas")
    def signoff() -> str:
        doc = client.get(f"/api/node/{PROCEDURE['node_id']}/doc", params={"path": PROCEDURE["path"]})
        return ("Run the sign-off for this conversation now, following this procedure exactly. "
                "Start with step 1 unless I have already said to sign off." + "\n\n"
                + (doc or {}).get("content", ""))
