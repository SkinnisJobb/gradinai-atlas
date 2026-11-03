"""
The add-on at run time: boot, the background loop, and what `/api/health` and
`/api/retrieval/*` report. Imported only when `ATLAS_RETRIEVAL=on` (see `main.py`).

THREE STATES, and only the first is quiet:
  on                       configured, storage ready, the last pass (if any) succeeded
  degraded: <last error>   configured and ready, but the last pass could not reach or use the
                           embedder; the index is stale, not wrong, and the loop backs off
  unavailable: <reason>    a configuration or storage problem; there is no loop
All three are INFORMATIONAL. Nothing here fails a boot, a request or a health check: Atlas is
complete without the add-on (ADR 0044 §0), so the add-on being broken is not Atlas being broken.

THE LOOP is one daemon thread per process. It waits one interval before its first pass (so a
restart does not hit the embedder in the same second as the boot), then runs a pass per
interval. After a failed pass the wait doubles, up to `MAX_BACKOFF_S`, and resets after a
success. `nudge()` (the reindex route) wakes it at once. Several processes are safe: a pass
takes the advisory lock, and a process that does not get it skips that round.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from retrieval import Config, load_config, reconciler, setup

log = logging.getLogger("atlas.retrieval")

MAX_BACKOFF_S = 600.0

_lock = threading.Lock()
_wake = threading.Event()
_state: dict = {"engine": None, "cfg": None, "unavailable": "not booted", "thread": None,
                "last_pass_at": None, "last_ok_at": None, "last_error": None,
                "last_stats": None, "passes": 0}


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def boot(engine: Engine, cfg: Optional[Config] = None, *, start_loop: bool = True) -> None:
    """Called once from `main.startup()` after the core schema is ready. Never raises."""
    try:
        cfg = cfg or load_config()
        _set(engine=engine, cfg=cfg)
        problem = cfg.problem or setup.ensure(engine, cfg)
    except Exception as exc:  # noqa: BLE001 — the add-on must never take the boot down
        problem = f"setup failed ({type(exc).__name__})"
    _set(unavailable=problem)
    if problem:
        log.warning("retrieval add-on is on but unavailable: %s", problem)
        return
    log.info("retrieval add-on on: model %s, %d dimensions, endpoint host %s, scope %s",
             cfg.model, cfg.dim, cfg.endpoint_host, cfg.scope)
    if start_loop and _state["thread"] is None:
        t = threading.Thread(target=_loop, name="atlas-retrieval", daemon=True)
        _set(thread=t)
        t.start()


def run_once() -> dict:
    """One pass now, in this thread, recording the outcome. The loop calls it; so do checks."""
    cfg, engine = _state["cfg"], _state["engine"]
    now = datetime.now(timezone.utc).isoformat()
    try:
        stats = reconciler.run_pass(engine, cfg)
    except Exception as exc:  # noqa: BLE001 — reported, never raised into the loop
        stats = {"error": f"pass failed ({type(exc).__name__})"}
        log.exception("retrieval pass failed")
    if stats.get("skipped"):
        return stats
    with _lock:
        _state.update(last_pass_at=now, last_stats=stats, last_error=stats.get("error"),
                      passes=_state["passes"] + 1,
                      **({} if stats.get("error") else {"last_ok_at": now}))
    if stats.get("error"):
        log.warning("retrieval pass: %s", stats["error"])
    return stats


def _loop() -> None:
    wait = _state["cfg"].interval_s
    while True:
        _wake.wait(timeout=wait)
        _wake.clear()
        if _state["unavailable"]:
            wait = _state["cfg"].interval_s
            continue
        stats = run_once()
        wait = (min(wait * 2, MAX_BACKOFF_S) if stats.get("error")
                else _state["cfg"].interval_s)


def nudge() -> None:
    _wake.set()


def query_config() -> tuple[Optional[Config], Optional[str]]:
    """The configuration the query side uses, or why it cannot: the `unavailable` reason (not
    booted, configuration or storage). A `degraded` loop does not stop queries: the index is
    stale then, not wrong, and the query embeds with the endpoint on its own."""
    cfg = _state["cfg"]
    if cfg is None:
        return None, "retrieval has not booted"
    if _state["unavailable"]:
        return None, f"unavailable: {_state['unavailable']}"
    return cfg, None


def has_vectors(cfg: Config) -> bool:
    """Does the index hold any vector of the active model? Tells "nothing visible matched" apart
    from "the first pass has not run yet", which a query reports as degraded."""
    with _state["engine"].connect() as c:
        return bool(c.execute(text(
            "SELECT EXISTS (SELECT 1 FROM retrieval_chunk WHERE model = :m)"), {"m": cfg.model}).scalar())


def health() -> str:
    if _state["unavailable"]:
        return f"unavailable: {_state['unavailable']}"
    if _state["last_error"]:
        return f"degraded: {_state['last_error']}"
    return "on"


def status() -> dict:
    """What `GET /api/retrieval/status` returns. Owner-only, because the counts are over the
    whole index, private nodes included. Names the endpoint's HOST and whether a key file and a
    CA bundle are configured; never a path's contents."""
    cfg: Config = _state["cfg"]
    out: dict = {
        "state": health(),
        "model": cfg.model if cfg else None,
        "dim": cfg.dim if cfg else None,
        "endpoint_host": cfg.endpoint_host if cfg and cfg.url else None,
        "scope": cfg.scope if cfg else None,
        "auth_header": cfg.auth_header if cfg else None,
        "key_file_configured": bool(cfg and cfg.key_file),
        "ca_bundle_configured": bool(cfg and cfg.ca_bundle),
        "interval_s": cfg.interval_s if cfg else None,
        "last_pass_at": _state["last_pass_at"],
        "last_ok_at": _state["last_ok_at"],
        "last_error": _state["last_error"],
        "last_pass": _state["last_stats"],
        "passes": _state["passes"],
    }
    if _state["unavailable"]:
        return out
    engine = _state["engine"]
    with engine.connect() as c:
        out["sources"] = c.execute(text("SELECT count(*) FROM retrieval_source")).scalar()
        out["chunks"] = c.execute(text(
            "SELECT count(*) FROM retrieval_chunk WHERE model = :m"), {"m": cfg.model}).scalar()
        out["chunks_other_models"] = c.execute(text(
            "SELECT count(*) FROM retrieval_chunk WHERE model <> :m"), {"m": cfg.model}).scalar()
        out["nodes"] = c.execute(text(
            "SELECT count(DISTINCT node_id) FROM retrieval_source")).scalar()
    out["pending_sources"] = reconciler.pending(engine, cfg)
    return out


def reindex(rebuild: bool) -> tuple[int, dict]:
    """Force a full re-embed (every hash cleared), or with `rebuild` drop and recreate the
    tables first (the only way to change the dimension). The pass itself runs in the loop."""
    cfg, engine = _state["cfg"], _state["engine"]
    if cfg is None or cfg.problem:
        return 409, {"error": "retrieval_unavailable", "detail": health()}
    if rebuild:
        problem = setup.rebuild(engine, cfg)
        _set(unavailable=problem)
        if problem:
            return 409, {"error": "retrieval_unavailable", "detail": health()}
        if _state["thread"] is None:
            boot(engine, cfg)
    elif _state["unavailable"]:
        return 409, {"error": "retrieval_unavailable", "detail": health()}
    else:
        with engine.begin() as c:
            c.execute(text("UPDATE retrieval_source SET content_hash = ''"))
    nudge()
    return 202, {"ok": True, "rebuild": rebuild,
                 "detail": "every source will be re-embedded by the next pass"}
