"""The app's route table as a flat list — what the route-enumerating verifies read.

WHY THIS EXISTS (TASK-1361). Up to FastAPI 0.115, `app.include_router(r)` COPIED
every route of `r` onto the app, so `app.routes` was the whole flat table. Newer
FastAPI (0.159 here) keeps an included router as ONE `_IncludedRouter` entry and
resolves its routes lazily at match time, so `app.routes` now holds the routes
declared on `app` itself plus one opaque entry per `include_router` call. A loop
over `app.routes` silently sees 10 of the 74 routes.

`fastapi.routing.iter_route_contexts` is the flattening FastAPI's own OpenAPI
generator uses. It yields one `RouteContext` per route, in dispatch order, whose
`path` / `name` / `methods` / `endpoint` are the EFFECTIVE values (include prefix
applied) and whose other attributes (`dependant`, `matches`, ...) fall through to
the effective route. `original_route` is the `APIRoute` object as declared.

The route table itself is unchanged by the upgrade; only the way to read it moved.
"""
from __future__ import annotations

from fastapi.routing import iter_route_contexts


def live_routes(app) -> list:
    """Every route `app` serves, flattened through included routers, in dispatch order."""
    return list(iter_route_contexts(app.routes))


def route_class(route) -> str:
    """The declared class name (`APIRoute`, `Mount`, ...), not `RouteContext`."""
    return type(getattr(route, "original_route", route)).__name__
