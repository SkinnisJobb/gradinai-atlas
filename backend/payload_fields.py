"""Fields a write route ignored, and what it says about them (STORY-oss-14-strict-api).

`POST`/`PATCH /api/node` and `/api/workitem` take a Pydantic body, and Pydantic
DROPS an undeclared key in silence. So `{"summray": "..."}`, or `priority` sent to
`/api/node` (only `/api/workitem` has it), used to be a 200 that changed nothing
the caller asked for. The same is true of a few declared keys the route reads and
then deliberately does not apply (`attributes.depends_on` on a workitem: the edge
table is its one home; `approval` when nothing needed approving). This module is
the one place those are named, so the four routes answer them the same way:

  * DEFAULT (`ATLAS_STRICT_API` unset): accepted exactly as before. A success
    response gains `warnings: [{"field", "code", "detail"}, ...]`, one entry per
    ignored field, and ONLY when there is one: a request with nothing ignored
    answers byte for byte what it did before this story.
  * STRICT (`ATLAS_STRICT_API=1`): a 422 naming the field, before anything is
    written. `error`/`field`/`detail` is the house shape (`_resolve_assignee`,
    `owner_retired_refusal`); `fields` lists every one when there are several.

`code` is `unknown_field` (the route has no such field) or `ignored_field` (it
does, and this request's value was not applied; `detail` says why).

`csrf_token` is never reported: it is transport, declared on every body, and a
Bearer caller that sends one is not wrong about anything.
"""
from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel, PrivateAttr, model_validator

import settings

UNKNOWN = "unknown_field"
IGNORED = "ignored_field"


class TracksUnknownFields(BaseModel):
    """A request body that remembers the top-level keys it did not declare.

    Pydantic's default (`extra="ignore"`) is kept on purpose: `extra="allow"`
    would put the stray keys into `model_dump()` and `model_fields_set`, which
    the PATCH routes feed straight into a `setattr` loop over node columns, and
    `extra="forbid"` would be strict mode for everyone. The wrap validator only
    LOOKS at the raw dict, after the model is built, and records what it saw."""

    _unknown_fields: list[str] = PrivateAttr(default_factory=list)

    @model_validator(mode="wrap")
    @classmethod
    def _note_unknown_fields(cls, data: Any, handler):
        model = handler(data)
        if isinstance(data, dict):
            known = set(cls.model_fields)
            model._unknown_fields = [k for k in data if isinstance(k, str) and k not in known]
        return model

    def unknown_field_problems(self) -> list[dict]:
        """One problem per undeclared key, in the order the caller sent them."""
        return [problem(k, UNKNOWN, f"`{k}` is not a field of this route; it was not applied.")
                for k in self._unknown_fields]


def problem(field: str, code: str, detail: str) -> dict:
    return {"field": field, "code": code, "detail": detail}


def strict_api() -> bool:
    """`ATLAS_STRICT_API`, read now through `settings.load_strict_api()`.

    A value that does not parse reads as today's behaviour (False): boot already
    refuses it (main.py loads it at import), so this branch is reachable only if
    the environment changed under a running process."""
    try:
        return settings.load_strict_api()
    except settings.SettingsError:
        return False


def strict_refusal(problems: list[dict]) -> JSONResponse | None:
    """The 422 for `problems` when strict mode is on; None otherwise (or when there are none)."""
    if not problems or not strict_api():
        return None
    first = problems[0]
    names = [p["field"] for p in problems]
    return JSONResponse(
        {"error": first["code"], "field": first["field"], "fields": names,
         "detail": (" ".join(p["detail"] for p in problems)
                    + " This install sets ATLAS_STRICT_API=1, so a field the route would "
                      "ignore is refused instead; nothing was written. Remove it and resend."),
         "problems": problems},
        status_code=422,
    )


def add_warnings(payload: dict, problems: list[dict]) -> dict:
    """Attach `warnings` to a success payload, only when there is something to say."""
    if problems:
        payload["warnings"] = list(problems)
    return payload
