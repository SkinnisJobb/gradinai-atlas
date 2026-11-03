"""Crunch week: a time-boxed release mode straight to production (STORY-atlas-crunch-mode).

An installation's administrator can switch on, for a set time, a release mode in which production
deploys after green CI without the staging / pre-production steps. This module is its state and
its audit trail: what the mode is set to (`current`), what a valid setting is
(`CONFIG_SCHEMA`, `validate_config`) and the writers that change it. The routes are
`routers/crunch.py`; the tables `models_crunch.py`. What the mode changes in the guardrail
matrix, the generated CI/CD and the exported policies is NOT here: those readers ask `current()`.

OFF BY DEFAULT, AND NEVER-ACTIVATED IS TODAY. An install that never activates crunch week has no
crunch table at all (models_crunch.py: created on first activation), `current()` answers None
without a write, and nothing anywhere behaves differently. verify_crunch.py §B proves the
database byte-identical after every route has been called by every kind of caller.

WHO. The instance owner (`principal.is_owner`) in a BROWSER SESSION, with the password step-up
on every change (`auth.reauthenticate`, exactly as `PUT /api/config/{name}` uses it). No token
of any scope and no MCP tool reads the controls or changes them: the routes refuse every
non-session principal, and there is no MCP tool (nothing under mcp/ names /api/crunch).

THE CONFIG
    {"starts_at": iso8601,                 optional: none means NOW, by the server's clock; a
                                           sent one is a SCHEDULED start, in the server's
                                           future, at most MAX_LEAD_DAYS ahead (THE CLOCK)
     "ends_at":   iso8601,                 after starts_at and after now; at most MAX_DAYS
                                           after starts_at. OR, exactly one of the two:
     "duration_hours": int,                1..MAX_DURATION_HOURS; the server computes ends_at
                                           (THE LENGTH). Never stored: the state has ends_at
     "backup":    {"policy": none|once|per_deploy|hourly|daily,      default none
                   "retention_days": int}  0 with policy none, else 1..MAX_RETENTION_DAYS
                                           (default DEFAULT_RETENTION_DAYS)
     "skip_envs": [env, ...]}              environments of the guardrail matrix IN FORCE whose
                                           step is skipped; never a production environment; with
                                           no matrix applied, any non-empty list is refused
A timestamp must carry a timezone (a naive one is refused, not guessed). Every refusal is a
`CrunchConfigError` naming the field (`backup.policy`, `skip_envs[1]`, ...), which the route
answers as 400 `bad_config` with that field.

Backups here are an install's opt-in choice and off by default (`policy: none`); the homelab's
`no-backup` intent on its `atlas` node stands. This module only records the policy and, through
`record_backup`, what a pipeline reports; it never runs a backup.

THE STATE `current(s)` returns: the config plus {"active": True, "activated_by": subject,
"activated_at": iso, "adr_id": str, "revision": int}, or None when off, not yet started, or
expired. `scheduled(s)` returns the same shape with "active": False for a window that is set
but has not started yet.

THE CLOCK IS THE SERVER'S (owner decision, 2026-10-27), so no client can move a window by its own clock.
  * "Start now" is a request with NO starts_at: `put` sets it to the server's `now()` at the
    moment it writes, the same instant as `activated_at`. A client clock running ahead or
    behind cannot make a fresh window read as "not started yet" (seen in CI before this rule:
    a client-sent start 1.7 s after the activation it belonged to).
  * A sent starts_at is only a scheduled future start: one at or before the server's now is
    400 `starts_at` (no backdating), one more than MAX_LEAD_DAYS ahead too. No clamping.
  * The one exception is the set window's own start sent back unchanged (the board resends it
    on a change or extend); a change or extend never moves the start earlier, and with no
    starts_at it keeps the start the window has.
  * ends_at is checked against the server's now and the effective start (after both, at most
    MAX_DAYS after the start). `activated_at`, every history row and the expiry use `now()`.
  * Times are kept to whole milliseconds, what a browser's `Date.toISOString()` carries, so a
    start sent back unchanged compares equal.
`validate_config` checks the request against the clock when the route reads it; `put` decides
the start and re-checks it against the row read in its own transaction.

THE LENGTH. Instead of `ends_at` a request may send `duration_hours` (a whole number, 1 ..
MAX_DURATION_HOURS = 31 days), exactly one of the two (both is 400 `duration_hours`, neither 400
`ends_at`). The server computes the end from its own values, so a client clock cannot shift it:
  * no window set: ends_at = the effective start (server now, or the scheduled start) + length;
  * a window already set (running or scheduled): the request EXTENDS it, ends_at = its current
    server-side ends_at + length (the board's Extend box sends this).
The result is checked like any end (after now and the start, at most MAX_DAYS long), and the
stored state, every event and every ADR carry `ends_at` only, never `duration_hours`.

EXPIRY. `current()` treats `now >= ends_at` as off. The first read that sees an expired window
ends it, in its own transaction: state `active` false with reason `expired`, one history row,
one `node.crunch.end` graph_event with `reason: "expired"` and one ADR. The write is conditioned
on the revision read, so concurrent readers record it exactly once. No person acted, so it is
written with no principal (`actor_kind = "system"`), whoever's read noticed it.

EVERY ACT (activate, change, extend, end) writes, in one transaction:
  * the state row (revision + 1) and one `crunch_history` row;
  * one graph_event `node.crunch.<act>` on `atlas-system`, through `deps.record_graph_event`;
  * one ADR on `atlas-system`, allocated through `adr_alloc` (lock_project, allocate,
    add_atlas_adr, which records its own `node.adr.write`).
`atlas-system` must exist; without it the act is refused (`SystemNodeMissing`, a
`CrunchRefused`) before anything is written, and the message says how to add it.

NO SECRET IN ANY EVENT. The detail is the config's own fields (times, backup policy, the env
names), the ADR id, the revision and, for an end, the reason. The password never reaches this
module: the route checks it and passes the person.

`change` vs `extend`: an act on a window that is set is an `extend` when the only difference is
a LATER ends_at, a `change` otherwise; the same config again is no act at all (nothing written).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, update

import models_crunch
from models_crunch import STATE_ID, CrunchHistory, CrunchState

SYSTEM_NODE = "atlas-system"
DEFAULT_ADR_PREFIX = "SYS"
ADR_SLUG_PREFIX = "crunch-week-"

BACKUP_POLICIES = ("none", "once", "per_deploy", "hourly", "daily")
MAX_DAYS = 31
MAX_LEAD_DAYS = 31
MAX_RETENTION_DAYS = 365
DEFAULT_RETENTION_DAYS = 7
#: A length instead of an end, in whole hours (THE LENGTH): 1 .. MAX_DAYS * 24.
MAX_DURATION_HOURS = MAX_DAYS * 24
CONFIG_KEYS = ("starts_at", "ends_at", "duration_hours", "backup", "skip_envs")
ACTS = ("activate", "change", "extend", "end")
#: The graph_event action per act (models_graph.GRAPH_EVENT_ACTIONS has them).
EVENT_ACTIONS = {a: f"node.crunch.{a}" for a in ACTS + ("backup",)}

#: A JSON-Schema-shaped description of the config, for the board's form and for readers.
CONFIG_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "oneOf": [{"required": ["ends_at"]}, {"required": ["duration_hours"]}],
    "properties": {
        "starts_at": {"type": "string", "format": "date-time",
                      "description": "omit to start now (the server's clock); else a "
                                     "scheduled start in the server's future, at most "
                                     f"{MAX_LEAD_DAYS} days ahead"},
        "ends_at": {"type": "string", "format": "date-time",
                    "description": f"after starts_at and now; at most {MAX_DAYS} days long; "
                                   "or send duration_hours instead"},
        "duration_hours": {"type": "integer", "minimum": 1, "maximum": MAX_DURATION_HOURS,
                           "description": "instead of ends_at: the server computes the end. A "
                                          "new window ends this long after its start (server "
                                          "now, or the scheduled start); a window already set "
                                          "is EXTENDED by it, from its current end"},
        "backup": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "policy": {"enum": list(BACKUP_POLICIES), "default": "none"},
                "retention_days": {"type": "integer", "minimum": 0,
                                   "maximum": MAX_RETENTION_DAYS,
                                   "description": "0 with policy none, else 1.."
                                                  f"{MAX_RETENTION_DAYS} (default "
                                                  f"{DEFAULT_RETENTION_DAYS})"},
            },
        },
        "skip_envs": {"type": "array", "items": {"type": "string"}, "uniqueItems": True,
                      "description": "non-production environments of the guardrail matrix "
                                     "in force; empty when no matrix is applied"},
    },
}


class CrunchConfigError(ValueError):
    """A config that does not validate. `field` names where (the route's 400 carries it)."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


class CrunchRefused(RuntimeError):
    """An act that cannot be recorded. `code` is the route's error name."""
    code = "refused"


class SystemNodeMissing(CrunchRefused):
    """`atlas-system` is not in this graph, and every act is recorded on it."""
    code = "system_node_missing"


class AdrPrefixTaken(CrunchRefused):
    """`atlas-system`'s ADR prefix belongs to another project."""
    code = "adr_prefix_taken"


class Conflict(RuntimeError):
    """Another write changed the state between this one's read and its write."""


class NotActive(RuntimeError):
    """An end with no window set."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: The clock. A function, not a call inline, so verify_crunch.py can move time past `ends_at`
#: in-process instead of sleeping. Nothing else replaces it.
now = _utcnow


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return None if dt is None else dt.astimezone(timezone.utc).isoformat()


def _parse_ts(field: str, value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CrunchConfigError(field, f"{field} must be an ISO 8601 date-time with a timezone")
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise CrunchConfigError(field, f"{field} is not an ISO 8601 date-time: {value[:40]!r}")
    if dt.tzinfo is None:
        raise CrunchConfigError(field, f"{field} has no timezone; give one (e.g. Z or +01:00)")
    return _ms(dt.astimezone(timezone.utc))


def _ms(dt: datetime) -> datetime:
    """Whole milliseconds: what a browser's `Date.toISOString()` carries, so a start the board
    sends back unchanged compares EQUAL to the stored one (THE CLOCK in the header)."""
    return dt.replace(microsecond=dt.microsecond // 1000 * 1000)


def _matrix_envs(matrix: Optional[dict]) -> tuple[list[str], set[str]]:
    if not matrix:
        return [], set()
    envs = [e["name"] for e in matrix.get("environments", [])]
    prod = {e["name"] for e in matrix.get("environments", []) if e.get("production")}
    return envs, prod


def _check_start(starts: datetime, at: datetime, current_start: Optional[datetime]) -> None:
    """A client-sent start (THE CLOCK): the set window's own start unchanged, or a time in the
    server's future (never backdated, never earlier than the set window's start), at most
    MAX_LEAD_DAYS ahead."""
    if current_start is not None and starts == current_start:
        return
    if starts <= at:
        raise CrunchConfigError("starts_at", "starts_at is in the past; to start now send no "
                                "starts_at (the server's clock sets it), and a start is never "
                                "backdated")
    if current_start is not None and starts < current_start:
        raise CrunchConfigError("starts_at", "a change or extend never moves the start earlier")
    if starts > at + timedelta(days=MAX_LEAD_DAYS):
        raise CrunchConfigError("starts_at", f"starts_at is more than {MAX_LEAD_DAYS} days ahead")


def _check_end(ends: datetime, starts: datetime, at: datetime) -> None:
    if ends <= starts:
        raise CrunchConfigError("ends_at", "ends_at must be after starts_at")
    if ends <= at:
        raise CrunchConfigError("ends_at", "ends_at is in the past")
    if ends - starts > timedelta(days=MAX_DAYS):
        raise CrunchConfigError("ends_at", f"crunch week lasts at most {MAX_DAYS} days")


def validate_config(raw: Any, *, matrix: Optional[dict] = None,
                    at: Optional[datetime] = None,
                    current_start: Optional[datetime] = None) -> dict:
    """The normalised config, or `CrunchConfigError` naming the field.

    `matrix` is the guardrail matrix in force (`guardrails.stored_matrix`), or None when none is
    applied; `skip_envs` is checked against it. `at` defaults to `now()`, the server's clock.
    `current_start` is the start of the window already set (`window_start(s)`), if any.

    No starts_at means "now": the result's `starts_at` is None, and `put` sets it from the
    server's clock when it writes (THE CLOCK in the header)."""
    at = _ms(at or now())
    if not isinstance(raw, dict):
        raise CrunchConfigError("config", "config must be an object")
    for key in raw:
        if key not in CONFIG_KEYS:
            raise CrunchConfigError(str(key)[:64], f"unknown config field {str(key)[:64]!r}; "
                                    f"the fields are {', '.join(CONFIG_KEYS)}")
    starts: Optional[datetime] = None
    if raw.get("starts_at") is not None:
        starts = _parse_ts("starts_at", raw["starts_at"])
        _check_start(starts, at, current_start)
    hours = raw.get("duration_hours")
    if raw.get("ends_at") is not None and hours is not None:
        raise CrunchConfigError("duration_hours", "send ends_at or duration_hours, not both")
    if hours is not None:
        if isinstance(hours, bool) or not isinstance(hours, int) \
                or not 1 <= hours <= MAX_DURATION_HOURS:
            raise CrunchConfigError("duration_hours", "duration_hours must be a whole number "
                                    f"of hours, 1..{MAX_DURATION_HOURS}")
        ends = None   # computed by put from the server's clock (THE LENGTH)
    elif raw.get("ends_at") is None:
        raise CrunchConfigError("ends_at", "ends_at (or duration_hours) is required: crunch "
                                "week always ends")
    else:
        ends = _parse_ts("ends_at", raw["ends_at"])
        _check_end(ends, starts or current_start or at, at)

    b = raw.get("backup")
    if b is None:
        b = {}
    if not isinstance(b, dict):
        raise CrunchConfigError("backup", "backup must be an object {policy, retention_days}")
    for key in b:
        if key not in ("policy", "retention_days"):
            raise CrunchConfigError(f"backup.{str(key)[:64]}", "unknown backup field")
    policy = b.get("policy", "none")
    if policy not in BACKUP_POLICIES:
        raise CrunchConfigError("backup.policy", "backup.policy must be one of "
                                + ", ".join(BACKUP_POLICIES))
    keep = b.get("retention_days")
    if keep is not None and (isinstance(keep, bool) or not isinstance(keep, int)):
        raise CrunchConfigError("backup.retention_days", "backup.retention_days must be an integer")
    if policy == "none":
        if keep not in (None, 0):
            raise CrunchConfigError("backup.retention_days",
                                    "backup.retention_days must be 0 when the policy is none")
        keep = 0
    else:
        keep = DEFAULT_RETENTION_DAYS if keep is None else keep
        if not 1 <= keep <= MAX_RETENTION_DAYS:
            raise CrunchConfigError("backup.retention_days",
                                    f"backup.retention_days must be 1..{MAX_RETENTION_DAYS}")

    skip = raw.get("skip_envs")
    skip = [] if skip is None else skip
    if not isinstance(skip, list):
        raise CrunchConfigError("skip_envs", "skip_envs must be a list of environment names")
    envs, prod = _matrix_envs(matrix)
    if skip and not envs:
        raise CrunchConfigError("skip_envs", "no guardrail matrix is applied, so there is no "
                                "environment to skip; apply one first (atlas-admin guardrails "
                                "apply) or leave skip_envs empty")
    seen: list[str] = []
    for i, name in enumerate(skip):
        where = f"skip_envs[{i}]"
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", name):
            raise CrunchConfigError(where, f"{where} is not an environment name")
        if name in seen:
            raise CrunchConfigError(where, f"{where}: {name!r} is listed twice")
        if name not in envs:
            raise CrunchConfigError(where, f"{where}: {name!r} is not an environment of the "
                                    f"guardrail matrix in force ({', '.join(envs)})")
        if name in prod:
            raise CrunchConfigError(where, f"{where}: {name!r} is a production environment; "
                                    "crunch week skips the steps before production, never "
                                    "production itself")
        seen.append(name)
    out = {"starts_at": _iso(starts), "ends_at": _iso(ends),
           "backup": {"policy": policy, "retention_days": keep}, "skip_envs": seen}
    if hours is not None:
        out["duration_hours"] = hours   # put turns it into ends_at and drops it (THE LENGTH)
    return out


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #

def _row(s) -> Optional[CrunchState]:
    if not models_crunch.tables_exist(s):
        return None
    return s.get(CrunchState, STATE_ID)


def _state(row: CrunchState, active: bool) -> dict:
    return {**(row.config or {}), "active": active, "activated_by": row.activated_by_subject,
            "activated_at": _iso(row.activated_at), "adr_id": row.adr_id,
            "revision": row.revision}


def _window(row: CrunchState) -> tuple[datetime, datetime]:
    cfg = row.config or {}
    return _parse_ts("starts_at", cfg["starts_at"]), _parse_ts("ends_at", cfg["ends_at"])


def current(s) -> Optional[dict]:
    """The crunch week in force now, or None (off, not started yet, or expired).

    A read. The one exception: the first read after `ends_at` records the end (EXPIRY in the
    header), in its own session, so the caller's transaction is untouched."""
    row = _row(s)
    if row is None or not row.active:
        return None
    starts, ends = _window(row)
    t = now()
    if t >= ends:
        # Recorded in its own session; the caller's session is left as it was (its copy of
        # the row is stale until it next expires it, which `put` does).
        _expire(row.revision)
        return None
    if t < starts:
        return None
    return _state(row, True)


def window_start(s) -> Optional[datetime]:
    """The start of the window that is set (running or scheduled, not expired), or None."""
    row = _row(s)
    if row is None or not row.active:
        return None
    starts, ends = _window(row)
    return starts if now() < ends else None


def scheduled(s) -> Optional[dict]:
    """A window that is set but has not started yet (same shape, "active": False), or None."""
    row = _row(s)
    if row is None or not row.active:
        return None
    starts, ends = _window(row)
    t = now()
    if t >= ends or t >= starts:
        return None
    return _state(row, False)


def history(s, limit: int = 10) -> list[dict]:
    """The last `limit` acts, newest first: {action, at, by} (plus reason for an end)."""
    if not models_crunch.tables_exist(s):
        return []
    rows = s.scalars(select(CrunchHistory).order_by(CrunchHistory.id.desc()).limit(limit)).all()
    out = []
    for r in rows:
        item = {"action": r.action, "at": _iso(r.at), "by": r.by_subject}
        if r.action == "end" and r.detail:
            item["reason"] = r.detail.get("reason")
        out.append(item)
    return out


def last_backup(s) -> Optional[dict]:
    """The latest backup a pipeline reported through `record_backup`, or None."""
    if not models_crunch.tables_exist(s):
        return None
    r = s.scalar(select(CrunchHistory).where(CrunchHistory.action == "backup")
                 .order_by(CrunchHistory.id.desc()).limit(1))
    if r is None:
        return None
    return {"at": _iso(r.at), "by": r.by_subject, **(r.detail or {})}


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #

def _require_system_node(s) -> None:
    from models_graph import Node
    if s.get(Node, SYSTEM_NODE) is None:
        raise SystemNodeMissing(
            f"`{SYSTEM_NODE}` is not in this graph, and crunch week is recorded on it (a "
            f"graph_event and an ADR per change). Add the system node set first: "
            f"`python system_node.py --apply` (in the image: `python /app/system_node.py "
            f"--apply`), then try again.")


def _adr_prefix(s) -> str:
    from models_adr import AdrRecord
    own = s.scalar(select(AdrRecord.prefix).where(AdrRecord.project_node_id == SYSTEM_NODE).limit(1))
    prefix = own or DEFAULT_ADR_PREFIX
    taken = s.scalar(select(AdrRecord.project_node_id).where(
        AdrRecord.prefix == prefix, AdrRecord.project_node_id != SYSTEM_NODE).limit(1))
    if taken is not None:
        raise AdrPrefixTaken(f"ADR prefix {prefix!r} already belongs to {taken!r}, so "
                                f"{SYSTEM_NODE} cannot number its crunch-week ADRs")
    return prefix


def _adr_text(act: str, cfg: dict, by: str, at: datetime, reason: Optional[str],
              previous: Optional[str]) -> tuple[str, str]:
    title = {"activate": "Crunch week activated", "change": "Crunch week changed",
             "extend": "Crunch week extended", "end": "Crunch week ended"}[act]
    if act == "end":
        title += " (expired)" if reason == "expired" else " (ended by the administrator)"
    lines = [f"# {title}", "",
             "## Status", "",
             # ACCEPTED, not Proposed: the administrator's password step-up on this very change
             # IS the person's act of deciding. An expiry is the decision recorded at activation
             # (the end date) taking effect, so it is the same accepted decision, not a new one.
             "Accepted by the administrator: the password step-up on the change is the "
             "person's act." if reason != "expired" else
             "Accepted: the end date the administrator set at activation took effect.", "",
             "## Context", "",
             "Crunch week is a time-boxed release mode: while it is on, a change reaches "
             "production after green CI without the staging / pre-production steps named "
             "below. CI is still required, and the guardrail rules for agents still apply "
             "(agents never hold production deploy rights).", "",
             "## Decision", "",
             f"- Act: {act}" + (f" ({reason})" if reason else ""),
             f"- By: {by}", f"- At: {_iso(at)}",
             f"- Window: {cfg.get('starts_at')} to {cfg.get('ends_at')}",
             f"- Backup: {cfg.get('backup', {}).get('policy')}, retention "
             f"{cfg.get('backup', {}).get('retention_days')} days",
             "- Skipped environments: " + (", ".join(cfg.get("skip_envs") or []) or "none")]
    if previous:
        lines.append(f"- Amends: {previous}")
    return title, "\n".join(lines) + "\n"


def _record(s, act: str, cfg: dict, *, by_subject: str, by_user_id: Optional[str],
            at: datetime, revision: int, reason: Optional[str] = None,
            previous_adr: Optional[str] = None) -> str:
    """The ADR, the history row and the graph_event of one act. No commit. Returns the ADR id."""
    import adr_alloc

    adr_alloc.lock_project(s, SYSTEM_NODE)
    prefix = _adr_prefix(s)
    seq, adr_id = adr_alloc.allocate(s, SYSTEM_NODE, prefix)
    title, md = _adr_text(act, cfg, by_subject, at, reason, previous_adr)
    adr_alloc.add_atlas_adr(
        s, adr_id=adr_id, project=SYSTEM_NODE, prefix=prefix, seq=seq, title=title,
        slug=f"{ADR_SLUG_PREFIX}{act}-{at.strftime('%Y%m%d%H%M%S')}", status="accepted",
        date=at.date(), content=md, now=at, amends=[previous_adr] if previous_adr else [])
    detail = {"adr_id": adr_id, "revision": revision, "starts_at": cfg.get("starts_at"),
              "ends_at": cfg.get("ends_at"),
              "backup_policy": (cfg.get("backup") or {}).get("policy"),
              "retention_days": (cfg.get("backup") or {}).get("retention_days"),
              "skip_envs": list(cfg.get("skip_envs") or [])}
    if reason:
        detail["reason"] = reason
    s.add(CrunchHistory(at=at, action=act, by_subject=by_subject, by_user_id=by_user_id,
                        adr_id=adr_id, detail=detail))
    _event(s, act, detail)
    return adr_id


def _event(s, act: str, detail: dict) -> None:
    """The act's graph_event on atlas-system, through the one writer. One LITERAL call per
    action, on purpose: verify_graph_event.py §A reads the emitted vocabulary off the source
    (each call whose second argument is a quoted action), and one built from a variable
    would read as declared-but-never-emitted."""
    import deps
    kw = {"entity_type": "node", "entity_id": SYSTEM_NODE, "detail": detail}
    if act == "activate":
        deps.record_graph_event(s, "node.crunch.activate", **kw)
    elif act == "change":
        deps.record_graph_event(s, "node.crunch.change", **kw)
    elif act == "extend":
        deps.record_graph_event(s, "node.crunch.extend", **kw)
    elif act == "end":
        deps.record_graph_event(s, "node.crunch.end", **kw)
    elif act == "backup":
        deps.record_graph_event(s, "node.crunch.backup", **kw)
    else:  # pragma: no cover - a caller bug
        raise ValueError(f"unknown crunch act {act!r}")


def _classify(row: Optional[CrunchState], cfg: dict) -> Optional[str]:
    """activate / change / extend, or None when `cfg` is what is already set."""
    if row is None or not row.active or now() >= _window(row)[1]:
        return "activate"
    old = row.config or {}
    if old == cfg:
        return None
    same_but_end = {k: v for k, v in old.items() if k != "ends_at"} == {
        k: v for k, v in cfg.items() if k != "ends_at"}
    if same_but_end and _parse_ts("ends_at", cfg["ends_at"]) > _parse_ts("ends_at", old["ends_at"]):
        return "extend"
    return "change"


def _effective_start(row: Optional[CrunchState], cfg: dict, at: datetime) -> dict:
    """`cfg` with its start decided by the SERVER's clock `at` (THE CLOCK in the header): no
    starts_at is `at` for a new window and the set window's own start otherwise; a sent one is
    re-checked here against the row read in this transaction. A `duration_hours` becomes the
    `ends_at` it means (THE LENGTH), so the stored config never holds one. Raises
    CrunchConfigError."""
    set_start = None
    if row is not None and row.active:
        old_start, old_end = _window(row)
        if at < old_end:
            set_start = old_start
    if cfg.get("starts_at") is None:
        starts = set_start or at
    else:
        starts = _parse_ts("starts_at", cfg["starts_at"])
        _check_start(starts, at, set_start)
    cfg = dict(cfg)
    hours = cfg.pop("duration_hours", None)
    if hours is not None:
        # THE LENGTH: a window already set is extended from its current end; a new one ends
        # this long after its start. Either way from server-side values only.
        base = old_end if set_start is not None else starts
        cfg["ends_at"] = _iso(base + timedelta(hours=hours))
    _check_end(_parse_ts("ends_at", cfg["ends_at"]), starts, at)
    if set_start is not None and starts == set_start:
        # The set window's own start, kept as the row stores it, so an unchanged start never
        # reads as a change (a row written before starts were whole milliseconds included).
        return {**cfg, "starts_at": row.config["starts_at"]}
    return {**cfg, "starts_at": _iso(starts)}


def _conditional_update(s, revision: int, **values) -> bool:
    res = s.execute(update(CrunchState).where(CrunchState.id == STATE_ID,
                                              CrunchState.revision == revision)
                    .values(revision=revision + 1, **values))
    return res.rowcount == 1


def put(s, cfg: dict, *, user_id: str, subject: str) -> Optional[str]:
    """Activate, change or extend to the VALIDATED `cfg`, for the person `user_id`. Commits.

    Returns the act, or None when nothing changed. Raises SystemNodeMissing (nothing written),
    Conflict (a concurrent write won) or CrunchConfigError (the start or end no longer holds
    against the server's clock and the row read here; THE CLOCK). The caller has done every
    check (owner, step-up)."""
    _require_system_node(s)
    current(s)        # records an expiry first, if the window set has run out
    s.expire_all()    # ...which was written in another session: re-read the row
    from db import engine
    models_crunch.ensure_tables(engine)
    row = s.get(CrunchState, STATE_ID)
    at = _ms(now())
    cfg = _effective_start(row, cfg, at)
    act = _classify(row, cfg)
    if act is None:
        return None
    rev = 0 if row is None else row.revision
    previous = row.adr_id if (row is not None and row.active and act != "activate") else None
    adr_id = _record(s, act, cfg, by_subject=subject, by_user_id=user_id, at=at,
                     revision=rev + 1, previous_adr=previous)
    values = {"active": True, "config": cfg, "adr_id": adr_id, "updated_at": at,
              "ended_at": None, "ended_reason": None}
    if act == "activate":
        values.update(activated_by_user_id=user_id, activated_by_subject=subject, activated_at=at)
    if row is None:
        s.add(CrunchState(id=STATE_ID, revision=1, **values))
        try:
            s.flush()
        except Exception as exc:  # noqa: BLE001 - a concurrent first activation
            s.rollback()
            raise Conflict("crunch week was changed by another request; reload") from exc
    elif not _conditional_update(s, rev, **values):
        s.rollback()
        raise Conflict("crunch week was changed by another request; reload")
    s.commit()
    return act


def end(s, *, user_id: Optional[str], subject: str, reason: str = "manual",
        expected_revision: Optional[int] = None) -> None:
    """End the window now. Commits. Raises NotActive, SystemNodeMissing or Conflict."""
    row = _row(s)
    if row is None or not row.active:
        raise NotActive("crunch week is not on")
    rev = row.revision if expected_revision is None else expected_revision
    if reason == "manual":
        _require_system_node(s)
        if now() >= _window(row)[1]:
            current(s)   # records the expiry instead (once)
            raise NotActive("crunch week had already expired")
    at = now()
    from models_graph import Node
    if s.get(Node, SYSTEM_NODE) is not None:
        _record(s, "end", row.config or {}, by_subject=subject, by_user_id=user_id,
                         at=at, revision=rev + 1, reason=reason, previous_adr=row.adr_id)
    if not _conditional_update(s, rev, active=False, ended_at=at, ended_reason=reason,
                               updated_at=at):
        s.rollback()
        raise Conflict("crunch week was changed by another request; reload")
    s.commit()


def _expire(revision: int) -> None:
    """Record the end of an expired window once (EXPIRY in the header). Own session, no
    principal: nobody acted, so the event is `actor_kind = "system"`."""
    import auth
    from db import session

    token = auth.current_principal.set(None)
    try:
        with session() as s2:
            row = s2.get(CrunchState, STATE_ID)
            if row is None or not row.active or row.revision != revision:
                return   # another reader recorded it, or a person changed it meanwhile
            try:
                end(s2, user_id=None, subject="system", reason="expired",
                    expected_revision=revision)
            except Conflict:
                pass     # a concurrent reader won; it recorded the one end event
    finally:
        auth.current_principal.reset(token)


def record_backup(s, *, ok: bool, verified: bool, label: str = "",
                  subject: str = "pipeline") -> None:
    """A pipeline reports one backup taken under crunch week's policy: a history row and a
    `node.crunch.backup` event on atlas-system. `label` is a NAME for the backup (never a path
    with credentials, never a secret). No commit; the caller owns the transaction."""
    from db import engine
    models_crunch.ensure_tables(engine)
    detail = {"ok": bool(ok), "verified": bool(verified), "label": str(label)[:200]}
    s.add(CrunchHistory(at=now(), action="backup", by_subject=subject, detail=detail))
    _event(s, "backup", detail)


__all__ = ["CONFIG_SCHEMA", "BACKUP_POLICIES", "CrunchConfigError", "CrunchRefused",
           "SystemNodeMissing", "AdrPrefixTaken", "Conflict", "NotActive", "validate_config", "current", "scheduled", "window_start",
           "history",
           "last_backup", "put", "end", "record_backup"]
