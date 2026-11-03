"""atlas_comments, atlas_comment_write — the conversation on an epic, story or task.

A thin facade over atlas-api's comment routes (`backend/routers/comments.py`), like every
other module here: the API decides who may see, write and edit, and this module only shapes
the call. A comment an agent writes names the agent (its token's account), never its person.

Deleting is left to the board on purpose: the route costs `admin`, and an agent that wants a
comment gone can edit its own. Editing is the author's only, so an agent edits only its own.

No `from __future__ import annotations` — same reason as every other module in this package
(FastMCP reads the annotations at registration time).
"""

from .client import client
from .envelope import AtlasError, envelope


def _clean_body(body: str) -> str:
    if not isinstance(body, str) or not body.strip():
        raise AtlasError("invalid_request", "body must be non-empty text")
    return body


def _comments_impl(id: str) -> dict:
    return client.get(f"/api/workitem/{id}/comments")


def _comment_write_impl(id: str | None, comment_id: int | None, body: str) -> dict:
    body = _clean_body(body)
    if comment_id is not None and id is not None:
        raise AtlasError("invalid_request",
                         "pass id= to add a comment or comment_id= to edit one, not both")
    if comment_id is not None:
        return client.patch(f"/api/comment/{comment_id}", json={"body": body})
    if id is not None:
        return client.post(f"/api/workitem/{id}/comments", json={"body": body})
    raise AtlasError("invalid_request", "pass id= to add a comment or comment_id= to edit one")


def register(mcp) -> None:
    """Declare this module's tools on the shared FastMCP instance (see mcp_server.py)."""

    @mcp.tool()
    def atlas_comments(id: str) -> dict:
        return envelope(_comments_impl)(id)

    @mcp.tool()
    def atlas_comment_write(body: str, id: str | None = None,
                            comment_id: int | None = None) -> dict:
        return envelope(_comment_write_impl)(id, comment_id, body)
