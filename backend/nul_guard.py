"""A NUL byte in the request URL is a 400, in ONE place (LCM patch round 1).

THE BUG. `GET /api/search?q=%00` (and /api/workitems, /api/adrs, any route that hands a query
parameter or path segment to Postgres) answered 500: a PostgreSQL text value cannot contain
U+0000, so psycopg raises `ValueError: A string literal cannot contain NUL (0x00) characters`
from deep inside the query, and nothing above it turns that into a client error. The DAST scan
(.github/workflows/dast.yml) found it by fuzzing.

WHY A MIDDLEWARE, NOT A CHECK PER ROUTE. Every route that takes a string would need the
check, and the next route added would not have it. An ASGI middleware sees every request before
any router, the same reason the auth gate is one (auth.py §1). It looks at the URL only: the
decoded path, and every query key and value as Starlette will decode them (percent and `+`).
A literal "%00" written as `%2500` decodes to the text "%00", not to NUL, and passes.

WHERE IT SITS. main.py adds it BEFORE `auth.install`, so the gate wraps it: an unauthenticated
request is still answered by the gate (401 / 302) exactly as before, and only a request the gate
let through can reach this 400. Deny-by-default stays the first answer.

NOT COVERED: a NUL inside a JSON or form BODY. Those are parsed per route (pydantic), and the
write routes have their own 400s naming the field; a body check would need to buffer every
request here. backend/verify_nul_bytes.py pins the URL cases.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qsl

NUL = "\x00"


def offending(scope: dict) -> str | None:
    """Where the request URL carries a NUL, as a sentence; None when it does not."""
    if NUL in scope.get("path", ""):
        return "the request path contains a NUL byte (%00)"
    raw = scope.get("query_string", b"")
    if not raw:
        return None
    text = raw.decode("latin-1")
    for key, value in parse_qsl(text, keep_blank_values=True):
        if NUL in key:
            return "a query parameter name contains a NUL byte (%00)"
        if NUL in value:
            return f"query parameter '{key}' contains a NUL byte (%00)"
    return None


class NulGuard:
    """Pure ASGI: no request body is read, nothing is buffered."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            why = offending(scope)
            if why:
                body = json.dumps({"detail": why}).encode()
                await send({"type": "http.response.start", "status": 400,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
