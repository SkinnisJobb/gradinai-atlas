"""Compatibility shim: the API used to be this one file.

STORY-oss-04 split `app.py` into `main.py` (the FastAPI app and startup),
`db.py` (engine, session, v0 models), `deps.py` (helpers shared by routers) and
`routers/<area>.py` (the routes). Nothing in this file is code to edit; it
exists so that everything which already says `app` keeps working unchanged:

  - `uvicorn app:app` (the Dockerfile CMD) — `app` below is `main.app`.
  - `import app` / `from app import X` in seed.py, testseed.py and the verify
    scripts: any name the old module had resolves here, from whichever module
    now defines it.
  - ASSIGNING through it. Several verify scripts perturb a check by
    replacing a function — `app._visible_node_ids = lambda s: None`, then
    restoring it — to prove the check fails without the code under test. A
    plain re-export would rebind the name here and nowhere that calls it, so
    the perturbation would silently do nothing and the check would pass for
    the wrong reason. Assignment therefore rebinds the name in every split
    module that holds the same object, which is what rebinding a global in
    the old single module did.
"""
from __future__ import annotations

import sys as _sys
import types as _types

import main as _main
from main import app  # noqa: F401  — `uvicorn app:app`

import db as _db
import deps as _deps
from routers import (
    access as _r_access, assets as _r_assets, docs as _r_docs, edges as _r_edges,
    events as _r_events, graph as _r_graph, health as _r_health, intents as _r_intents,
    legacy_v0 as _r_legacy_v0, me as _r_me, nodes as _r_nodes, search as _r_search,
    workitems as _r_workitems,
)

# Lookup order for a name more than one module binds (an import such as
# `select`, or a helper a router imported from deps): they are the same object,
# so the order only decides which module answers first.
_MODULES = (_main, _db, _deps, _r_health, _r_legacy_v0, _r_graph, _r_nodes, _r_edges,
            _r_workitems, _r_intents, _r_search, _r_events, _r_docs, _r_access, _r_me,
            _r_assets)
_MISSING = object()


class _AppShim(_types.ModuleType):
    def __getattr__(self, name: str):
        for m in _MODULES:
            v = vars(m).get(name, _MISSING)
            if v is not _MISSING:
                return v
        raise AttributeError(f"module 'app' has no attribute {name!r}")

    def __setattr__(self, name: str, value) -> None:
        if name in vars(self):
            return super().__setattr__(name, value)
        try:
            current = self.__getattr__(name)
        except AttributeError:
            return super().__setattr__(name, value)
        for m in _MODULES:
            if vars(m).get(name, _MISSING) is current:
                setattr(m, name, value)


_sys.modules[__name__].__class__ = _AppShim
