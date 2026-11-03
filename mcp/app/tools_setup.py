"""The setup guide's agent interview -- `atlas_setup_guide` (STORY-atlas-setup-guide-adrs).

A thin wrap of the guide's state API (backend/routers/setup.py), which holds every rule: the
question set and its validation, who may run the guide (an owner, or an owner's agent whose
token carries `admin`), the Proposed ADR each answer becomes, and supersede on a changed
answer. This module only chooses the call:

  * no `answers`  -> GET /api/setup (`?topic=` when given): where the guide stands, the next
                     unanswered question, and the topic to ask in full;
  * `answers`     -> POST /api/setup/answers for `topic`: one topic's answers, recorded as a
                     Proposed ADR. `answers` without `topic` is refused here, before any call.

There is deliberately NO accept here. Accepting a guide-made ADR is a person's act from a
browser session (POST /api/setup/adrs/{id}/accept refuses every token), so an agent can only
point the person at it. The client pack's `setup-guide` skill carries how to run the interview:
ask the person each question, never answer for them.

A live install also needs this tool on the gateway's allow-list; that config is not in this
repo.

No `from __future__ import annotations` here -- FastMCP inspects the real signature.
"""

from typing import Any, Optional

from .client import client
from .envelope import AtlasError, envelope


def register(mcp) -> None:
    """Declare `atlas_setup_guide` on `mcp`. Called once, from mcp_server.py."""

    def _atlas_setup_guide(topic: Optional[str], answers: Optional[dict],
                           project: Optional[str], prefix: Optional[str]) -> Any:
        if answers is None:
            return client.get("/api/setup", params={"topic": topic} if topic else None)
        if not topic:
            raise AtlasError("invalid_request",
                             "answers are one topic's: pass the topic they answer as `topic`. "
                             "Call atlas_setup_guide() with no arguments to see the next topic.")
        body: dict = {"topic": topic, "answers": answers}
        if project is not None:
            body["project"] = project
        if prefix is not None:
            body["prefix"] = prefix
        return client.post("/api/setup/answers", json=body)

    @mcp.tool()
    def atlas_setup_guide(topic: Optional[str] = None, answers: Optional[dict] = None,
                          project: Optional[str] = None, prefix: Optional[str] = None) -> dict:
        return envelope(_atlas_setup_guide)(topic, answers, project, prefix)
