"""One uniform envelope for every atlas-mcp tool.

  success -> {"ok": True,  "result": <...>}
  failure -> {"ok": False, "error": {"type": <code>, "message": <str>}}

Same shape as the envelope of the earlier FastMCP service this one was modelled on —
a caller that already knows one MCP surface built this way knows this one too.

Deliberately NOT `from __future__ import annotations` here — nothing in this module
is a tool function, so it would be harmless in THIS file, but the rule in AGENTS.md/
the task brief is "don't do it in any module where tool functions are defined", and
the simplest way to never trip that is to not do it anywhere in this package.
"""

from typing import Any, Callable


class AtlasError(Exception):
    """A structured, catchable error a tool can turn into the failure envelope.

    `error_type` is the stable, machine-readable code a caller can branch on
    (e.g. "not_found", "unauthorized", "invalid_request"); `str(exc)` is the
    human-readable message, already carrying any `field` atlas-api named.
    """

    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type


def envelope(fn: Callable[..., Any]) -> Callable[..., dict]:
    """Wrap a plain function so an AtlasError becomes the uniform failure envelope
    instead of an uncaught exception reaching the MCP transport.

    Used INSIDE a tool function's body (`return envelope(_impl)(*args)`), not as a
    decorator on the tool function itself — decorating the tool directly would hide
    its real parameter list behind `*args, **kwargs` and break FastMCP's signature
    introspection, the same trap the no-`__future__`-annotations rule guards against.
    """

    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            out = {"ok": True, "result": fn(*args, **kwargs)}
        except AtlasError as e:
            out = {"ok": False, "error": {"type": e.error_type, "message": str(e)}}
        # A stale image or an undeployed merge reads, to the caller, as a missing or broken
        # tool. Say so on the response itself, only while it is true (deploy_warning.py).
        # ATLAS_DISABLED_FEATURES=deploy_warning (STORY-oss-08) skips it, and its health read.
        from . import settings
        if settings.feature_enabled("deploy_warning"):
            from . import deploy_warning  # late: keeps envelope importable on its own
            warnings = deploy_warning.current()
            if warnings:
                out["deploy_warning"] = warnings
        return out

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper
