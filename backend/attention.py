"""
Atlas v2 attention + completion engine (ADR 0019, TASK-854 / TASK-855).

DERIVED, never hand-set. Every value here is recomputed from live signals on
each ingest, so a stale-colour drift (the TASK-820 problem the ADR calls out) is
impossible — nothing is persisted that isn't re-derivable from node status +
edges.

Two independent axes (ADR 0019 "Visual model"):
  * FILL  = `attention`  — "where do I look?"  {red, orange, yellow, green, grey}
            + `purple` reserved (see the PURPLE deferral note below).
  * RING  = `completion` — "how done?"  done / in-progress / todo over the
            `contains` subtree; `node.completion` caches the done-fraction 0..1.
  * NUMBER= `attention_count` — count of red+orange descendants (0 = hidden by
            the frontend).
These are orthogonal: a green node can be half-complete, and a fully-complete
node could (in principle) still be red.

ATTENTION = worst(STATE, GOAL-DIRECTED URGENCY)  (ADR 0019 "Goal-directed urgency")
  * STATE (health()) is a node's OWN current condition — done/in-progress green,
    blocked red, not-started grey. Built in TASK-854.
  * URGENCY (urgency_of()) flows from GOALS and propagates BACKWARD along
    `depends_on`: a not-done node that is a bottleneck for other not-done,
    goal-relevant work lights up even though its own state is grey. This is why
    a not-started item is grey ONLY when nothing important waits on it.
    Frontier-only: RED is reserved for the *actionable* bottleneck (own deps all
    done — "do this next"); nodes blocked behind it on the critical path are
    ORANGE. So a dependency chain shows ONE red frontier, not an all-red chain.
  * v1: goals = epics, all active, equal weight 1.0 (money-weighting deferred —
    see default_goal_config + the note there). The discriminator is the blocking
    structure, not the weight.

╔══════════════════════════════════════════════════════════════════════════╗
║ PURPLE / awaiting-decision is DEFERRED (not built here).                  ║
║ ADR 0019's purple state (a node awaiting a human decision, showing the    ║
║ assignee and AGEING toward red past a threshold) needs a persistent       ║
║ `since` timestamp + a node-attached decision store. (It also needed an    ║
║ ingest upsert refactor; the ingests are deleted as of TASK-1058, so that  ║
║ third prerequisite is gone rather than met.) None of the rest             ║
║ exists yet. `attention` can still hold "purple" (the schema/rollup        ║
║ order include it), but `health()` never RETURNS purple in v1. Wiring      ║
║ the aging decision model is a separate later slice.                       ║
╚══════════════════════════════════════════════════════════════════════════╝

WHERE THIS LIVES / HOW RECOMPUTE IS TRIGGERED:
  * All derivation logic is here (not in app.py) so the ingester, the API, and
    a future probe-change hook share one implementation.
  * `recompute_derived(session)` is called in the SAME unit of work as any
    write that changes status or edges — `PATCH /api/node/{id}`, the workitem
    PATCH route, the one-time importers. It used to be called at the end of
    the boot ingest (`ingest_graph.ingest_backlog_graph`); that ingest is
    deleted (TASK-1058), so recompute-on-write is now the only trigger there
    is. When probe execution lands (STORY-atlas-node-types-probes) it should
    call the same function on a probe-result change. There is no scheduler.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models_graph import WORK_HIERARCHY_EDGE_TYPE, Edge, Node, NodeType

# --------------------------------------------------------------------------- #
# Attention colour ordering — worst (red) first, matching app.py's RAG/worst().
# grey = "no signal / not started", excluded from the worst-child rollup.
# --------------------------------------------------------------------------- #
ATTENTION_ORDER = {"red": 0, "orange": 1, "yellow": 2, "green": 3, "purple": 4}

# --------------------------------------------------------------------------- #
# Attention = STATE semantics (ADR 0019): red = *needs attention now*, grey =
# *not started* (NOT red). This deliberately DIVERGES from the maturity model in
# ingest_backlog.STATUS_TO_RAG (where not-started = red = "least mature"): here a
# planned/backlog node is grey, because nobody needs to *look* at it yet.
#
# NOTE — a not-started node CAN still be urgent (e.g. it blocks a goal with a
# deadline, or a dependency just failed). That "goal-directed urgency" is a
# SEPARATE upcoming layer, deliberately NOT built here — this layer maps a
# node's own current state to a colour, nothing more. Dependency/goal-based
# escalation (and purple/awaiting-decision, see the header) come later.
# --------------------------------------------------------------------------- #
STATE_ATTENTION = {
    "blocked": "red",
    "done": "green",
    "accepted": "green",
    "closed": "green",
    "in_progress": "green",
    "wip": "green",
    # `analyze` — work IS in flight, it just isn't implementation yet: somebody
    # is reading, measuring or deciding. Green for the same reason in_progress
    # is green (nobody needs to *look* at it), and distinct from `review`, which
    # is orange because review is work waiting on someone else's attention.
    "analyze": "green",
    "analysis": "green",
    "review": "orange",
    "in_review": "orange",
    "planned": "grey",
    "pending": "grey",
    "backlog": "grey",
    "proposed": "grey",
    "todo": "grey",
    "not_started": "grey",
    # Explicit, not a fallthrough (owner decision, 2026-08-10): a cancelled item is
    # terminal, like `done` — see DONE_STATUSES below — but it is not work
    # that needs a LOOK the way red/orange do, so it gets the same "nobody
    # needs to look" grey as not-started work. Listed explicitly so this is a
    # decision, not the `.get(..., "grey")` default catching an omission.
    "cancelled": "grey",
}

# Completion buckets (for the 3-weight ring). "review" is work-in-flight, not done.
#
# `cancelled` counts as DONE (owner decision, 2026-08-10: "cancelled should be treated
# as the same as complete") — it stamps completed_at (stamp_completion_dates
# below), it ages out under the board's existing filter alongside `done`, and
# it counts toward the completion ring/bar the same way. That last part is a
# real, stated consequence: cancelling a task makes its parent look MORE
# finished, not less — see this task's report for the numbers. Splitting
# "done" from "completion-bar done" would be a one-place change here if that
# turns out to be wrong.
DONE_STATUSES = {"done", "accepted", "closed", "cancelled"}
INPROGRESS_STATUSES = {"in_progress", "wip", "analyze", "analysis", "review", "in_review"}

# The vocabulary the BOARD may write (backlog/README.md "Status values", plus
# `analyze` and, as of TASK-1049, `cancelled`). Deliberately narrower than the
# maps above: those also accept the synonyms that already exist in authored
# files (wip/closed/accepted/…), which stay readable, but a fresh board click
# should only ever mint one canonical spelling per state. Ordered as the
# lifecycle runs — the frontend renders the control in this order.
# `cancelled` is appended after `done` rather than woven into the middle: it
# is a second terminal exit from the lifecycle (abandoned, not finished), not
# a step on the pending -> … -> done path.
SETTABLE_STATUSES = ("pending", "analyze", "in_progress", "blocked", "done", "cancelled")

# Authored statuses that MEAN one of the settable ones. The backlog has
# used `planned` and `pending` interchangeably since long before the board could
# set a status (backlog/README.md: "`pending` / `planned` — not started"), and
# 80 items say `planned`.
#
# THIS IS WHY IT EXISTS (found on staging, 2026-07-31): set a `planned` task to
# `analyze`, then click `pending` to undo. Comparing raw strings, `pending` !=
# `planned`, so the undo stored an override saying "pending" over a file saying
# "planned" — two spellings of not-started — which then presents as a pending
# edit and writes a junk `planned` -> `pending` diff into the backlog. Comparing
# BUCKETS makes that click what the user meant: a clear.
#
# frontend/state.js carries a mirror of this map (STATUS_EQUIVALENT) for the
# same decision on the click. It lived in board.html until TASK-1023 split the
# board into ES modules, then moved from panel.js to state.js so the board's
# status FILTER could read the same vocabulary without a circular import.
# Two runtimes, one rule: keep them equal.
STATUS_EQUIVALENT = {
    "planned": "pending", "todo": "pending", "backlog": "pending",
    "not_started": "pending", "proposed": "pending",
    "wip": "in_progress",
    "accepted": "done", "closed": "done",
}


def status_bucket(status_norm: str | None) -> str | None:
    """Collapse a normalised status onto the settable value it means, so two
    spellings of the same state compare equal."""
    if not status_norm:
        return None
    return STATUS_EQUIVALENT.get(status_norm, status_norm)


def _norm(status) -> str | None:
    """Normalise a raw frontmatter status the same way ingest_backlog does."""
    if not status:
        return None
    return str(status).strip().lower().replace(" ", "_").replace("-", "_")


def authored_status(node: Node) -> str | None:
    """The normalised status as AUTHORED in the backlog markdown, ignoring any
    board override. This is what the markdown says, and what an override is
    reconciled against."""
    return _norm((node.attributes or {}).get("status"))


def status_map(session: Session, nodes: dict[str, Node] | None = None) -> dict[str, str | None]:
    """node id -> the EFFECTIVE normalised status.

    UNTIL 2026-08-10 this preferred a pending board override (models_graph.
    StatusOverride) over the authored frontmatter value. That table is gone
    (TASK-1056, STORY-atlas-retire-status-roundtrip) — every node's status
    lives directly on `node.attributes["status"]` now, set either by a backlog
    file (git-origin nodes) or by the write API (atlas-origin nodes,
    app.set_node_status). This function's SIGNATURE and call sites are kept
    unchanged on purpose: everything downstream (attention, completion,
    work-load, the age filter) still reads THIS, never `attributes["status"]`
    directly, so a future second status source (if one is ever needed again)
    has exactly one place to plug in.
    """
    if nodes is None:
        nodes = {n.id: n for n in session.scalars(select(Node)).all()}
    return {nid: authored_status(n) for nid, n in nodes.items()}


# --------------------------------------------------------------------------- #
# Completion dates — the axis "hide work finished more than N days ago" sorts on.
# --------------------------------------------------------------------------- #
def _parse_authored_date(value) -> datetime | None:
    """Parse a `completed:` frontmatter value into an aware UTC datetime.

    PyYAML turns an unquoted `2026-06-10` into a `datetime.date` while the
    tolerant fallback parser in ingest_backlog returns the string `"2026-06-10"`
    — the same file yields different Python types depending on which parser its
    frontmatter survived, so both are handled here rather than at the call site.
    A value that is neither is ignored (returns None) rather than raising: a
    typo'd date must not take the ingest down.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip().strip("'\"")
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # Last resort: an ISO date with anything trailing it (a timezone, a comment).
    try:
        y, m, d = (int(part) for part in text[:10].split("-"))
        return datetime(y, m, d, tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _aware(moment) -> datetime | None:
    """Normalise to UTC-aware before any comparison — comparing a naive and an
    aware datetime raises TypeError. Postgres itself always returns aware
    values for a `DateTime(timezone=True)` column, so on the one dialect this
    runs against the `moment.tzinfo` branch is the one ever taken; kept as a
    defensive no-op rather than assuming every future caller passes this
    function an already-aware value."""
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def stamp_completion_dates(session: Session, statuses: dict[str, str | None],
                           now: datetime | None = None) -> dict[str, int]:
    """Maintain `node.completed_at` for every node. Returns tallies.

    Three rules, in priority order (see the column's docstring in models_graph):
      1. an authored `completed:` date wins whenever the node is done — it is
         the only source that can speak about work finished before Atlas
         existed, and the only one that survives a DB rebuild;
      2. otherwise a done node with no stamp gets NOW, once — and it is never
         moved again while it stays done, so the age filter measures when the
         work finished rather than when the container last restarted;
      3. a node that is no longer done has its stamp cleared, so reopened work
         re-enters the view instead of staying hidden as ancient history.
    """
    now = now or datetime.now(timezone.utc)
    tally = {"authored": 0, "stamped": 0, "cleared": 0}
    for n in session.scalars(select(Node)).all():
        done = statuses.get(n.id) in DONE_STATUSES
        current = _aware(n.completed_at)
        if not done:
            if current is not None:
                n.completed_at = None
                tally["cleared"] += 1
            continue
        authored = _parse_authored_date((n.attributes or {}).get("completed"))
        if authored is not None:
            if current != authored:
                n.completed_at = authored
                tally["authored"] += 1
        elif current is None:
            n.completed_at = now
            tally["stamped"] += 1
    return tally


def completed_days_ago(node: Node, now: datetime | None = None) -> float | None:
    """Whole-ish days since this node completed, or None if it hasn't."""
    stamped = _aware(node.completed_at)
    if stamped is None:
        return None
    return ((now or datetime.now(timezone.utc)) - stamped).total_seconds() / 86400.0


def worst(colours: list[str]) -> str:
    """Worst-child rollup: min by ATTENTION_ORDER, grey excluded. All-grey -> grey."""
    present = [c for c in colours if c in ATTENTION_ORDER]
    if not present:
        return "grey"
    return min(present, key=lambda c: ATTENTION_ORDER[c])


def health(node_type: str, status_norm: str | None) -> str:
    """A LEAF `workitem`-family node's attention from its OWN current state
    (ADR 0019 STATE semantics): blocked->red, done/in_progress->green,
    review->orange, not-started/empty/unknown->grey.

    v1 reads `status` for every leaf type; probe results (a future signal source)
    would fold in here. Never returns purple in v1 (see the PURPLE deferral note
    above), and does NOT escalate on dependencies/goals — that goal-directed
    urgency is the deferred next layer.
    """
    if not status_norm:
        return "grey"
    return STATE_ATTENTION.get(status_norm, "grey")


# --------------------------------------------------------------------------- #
# object_health — the `object`/`bridge`-family counterpart to health() above
# (ADR 0019 Addendum, TASK-858 "route fill derivation by family"). Objects
# don't carry a workflow `status`; for v1 (no probe runner yet — that's
# STORY-atlas-node-types-probes) they carry a STATIC health hint on
# `attributes.health`, set by the world manifest (TASK-859). Reuses the exact
# same colour vocabulary (red/orange/yellow/green/purple/grey) so the shared
# ring/badge/purple grammar needs no changes downstream.
# --------------------------------------------------------------------------- #
_VALID_ATTENTION_COLOURS = {"red", "orange", "yellow", "green", "purple", "grey"}


def object_health(attributes: dict | None) -> str:
    """A LEAF `object`/`bridge`-family node's attention from its static health
    hint. Unknown/missing hint -> grey ("no signal"), same convention as an
    unset workitem status. A future probe engine plugs in here without
    touching health()/attention_of()'s call sites."""
    hint = (attributes or {}).get("health")
    if not hint:
        return "grey"
    hint = str(hint).strip().lower()
    return hint if hint in _VALID_ATTENTION_COLOURS else "grey"


def default_goal_config(nodes: dict[str, Node]) -> dict[str, dict]:
    """v1 goal config: every `epic` is an active goal/urgency source, equal
    weight 1.0.

    Structured as {epic_id: {"weight": float, "active": bool}} so a later
    MONETIZATION pass can set differential weights (ADR 0019: the shortest path
    to a demonstrable, sellable outcome ranks highest) or deactivate goals —
    WITHOUT touching this engine. In v1 weight is not applied differentially
    (all equal); it only gates urgency on/off (weight>0 and active). Passing a
    modified config to derive() re-derives with the new goals; nothing is
    persisted (safe on purge+rebuild).
    """
    return {nid: {"weight": 1.0, "active": True}
            for nid, n in nodes.items() if n.type == "epic"}


def derive(session: Session, goal_config: dict[str, dict] | None = None,
           visible_ids: set[str] | None = None) -> dict[str, dict]:
    """Pure computation (no writes) of the derived values for every node.

    Returns {node_id: {attention, state, urgency, urgency_blocks, completion,
    done, in_progress, todo, attention_count}}. Used both by recompute_derived
    (which caches attention/completion/attention_count onto node columns) and by
    the read API. `goal_config` overrides the default (all-epics-active) goal set
    — see default_goal_config.

    `visible_ids` (STORY-atlas-access-admin) restricts the computation to a node
    set, and `None` means "no restriction". IT IS NOT A POST-FILTER, and that is
    the entire point: a rollup is a NUMBER, so stripping hidden nodes from the
    RESULT still leaks them through every ancestor's `attention`,
    `attention_count` and `completion` — a red child nobody may see makes its
    parent red, and the colour is the leak. Restricting the INPUT makes each
    number identical to the same query against a database with those rows
    physically deleted, which is the only definition of "absent" that survives
    someone comparing two accounts' boards side by side.

    Every adjacency below already guards `if e.src in nodes and e.dst in nodes`,
    so dropping ids here drops their edges with them and no walk needs to know
    about visibility at all.

    NOTE the one caller that must NOT pass this: `recompute_derived`, which
    caches these values onto node columns. Caching a filtered rollup would
    persist one principal's view as everyone's. It calls with the default.
    """
    nodes = {n.id: n for n in session.scalars(select(Node)).all()}
    if visible_ids is not None:
        nodes = {nid: n for nid, n in nodes.items() if nid in visible_ids}

    # family per node_type (ADR 0019 Addendum, TASK-858): drives which health
    # function a leaf uses below. Unknown/missing type_id -> "workitem" (the
    # pre-0019 default), so this never changes behaviour for a node whose
    # node_type row hasn't been migrated/seeded yet.
    family_of_type: dict[str, str] = dict(
        session.execute(select(NodeType.id, NodeType.family)).all()
    )

    # `contains` -> children/parents, EXCLUDING bridge-family destinations.
    # ADR 0019 Addendum: goal/skill/doc/vision are ATTACHMENTS a `contains`
    # edge nests them under (so the tree/API still traverses to them — see
    # app.py's /api/graph{,/tree}, which builds its own kids_of straight from
    # the DB and is unaffected by this exclusion) — they are not subordinate
    # work/objects whose health should roll into their host's worst-child
    # rollup. Without this, e.g. a service with an attached (grey, no-signal)
    # doc node would itself roll up to grey, discarding its OWN health hint —
    # exactly the bug this fixes. Bridge nodes still get their own
    # attention/completion computed as ordinary (childless) leaves below.
    children: dict[str, list[str]] = defaultdict(list)  # parent -> [child] over contains edges
    parents: dict[str, list[str]] = defaultdict(list)   # child -> [parent] (contains, reversed)
    for e in session.scalars(select(Edge).where(Edge.edge_type == "contains")).all():
        if e.src in nodes and e.dst in nodes:
            if family_of_type.get(nodes[e.dst].type, "workitem") == "bridge":
                continue
            children[e.src].append(e.dst)
            parents[e.dst].append(e.src)

    # depends_on adjacency. Edge (src depends_on dst): src waits on dst, dst
    # blocks src. So dst's *dependents* (work waiting on it) = the srcs.
    deps_of: dict[str, list[str]] = defaultdict(list)        # nid -> things nid depends on
    dependents_of: dict[str, list[str]] = defaultdict(list)  # nid -> things that depend on nid
    for e in session.scalars(select(Edge).where(Edge.edge_type == "depends_on")).all():
        if e.src in nodes and e.dst in nodes:
            deps_of[e.src].append(e.dst)
            dependents_of[e.dst].append(e.src)

    # EFFECTIVE status — a pending board override beats the authored frontmatter
    # value, so clicking "done" on the board moves the fill, the ring, the
    # blocking frontier and the completion date exactly as committing the
    # markdown would.
    status = status_map(session, nodes)

    # "Done for blocking purposes": a LEAF is done by its own status; a PARENT is
    # done when its whole `contains` subtree is done — NOT by its own (untrusted,
    # drift-prone) status flag. This keeps blocking consistent with how state
    # ignores parent status: an in-progress story whose tasks are all finished no
    # longer blocks anything.
    _fd: dict[str, bool] = {}

    def is_done(nid: str, stack: frozenset[str] = frozenset()) -> bool:
        if nid in _fd:
            return _fd[nid]
        if nid in stack:
            return False
        kids = children.get(nid, [])
        if kids:
            r = all(is_done(k, stack | {nid}) for k in kids)
        else:
            r = status.get(nid) in DONE_STATUSES
        _fd[nid] = r
        return r

    if goal_config is None:
        goal_config = default_goal_config(nodes)

    # --- goal relevance: does this node serve an active goal (ancestor epic)? ---
    _anc_epics: dict[str, set[str]] = {}

    def ancestor_epics(nid: str, stack: frozenset[str]) -> set[str]:
        if nid in _anc_epics:
            return _anc_epics[nid]
        if nid in stack:
            return set()
        res: set[str] = {nid} if nodes[nid].type == "epic" else set()
        for p in parents.get(nid, []):
            res |= ancestor_epics(p, stack | {nid})
        _anc_epics[nid] = res
        return res

    _goal_rel: dict[str, bool] = {}

    def goal_relevant(nid: str) -> bool:
        if nid not in _goal_rel:
            _goal_rel[nid] = any(
                (goal_config.get(eid, {}).get("active", False)
                 and goal_config.get(eid, {}).get("weight", 0.0) > 0)
                for eid in ancestor_epics(nid, frozenset())
            )
        return _goal_rel[nid]

    # --- transitive dependents (who waits on nid, directly or via a chain) -----
    _tdep: dict[str, set[str]] = {}

    def transitive_dependents(nid: str, stack: frozenset[str]) -> set[str]:
        if nid in _tdep:
            return _tdep[nid]
        if nid in stack:
            return set()
        res: set[str] = set()
        for x in dependents_of.get(nid, []):
            res.add(x)
            res |= transitive_dependents(x, stack | {nid})
        _tdep[nid] = res
        return res

    # --- goal-directed urgency: is nid a bottleneck for active-goal work? ------
    # FRONTIER-ONLY RED: urgency keys off THIS node's own actionability.
    #   red    = not-done, blocks not-done goal work, AND is actionable NOW (all
    #            its own deps done / none) — the workable bottleneck, "do next".
    #   orange = not-done, blocks not-done goal work, but is itself STILL BLOCKED
    #            (≥1 own dep not done) — on the critical path, can't act yet.
    #   none   = blocks no not-done goal work (state's grey/green stands).
    # In a chain 840→841→842→…, only 840 (no incomplete deps) is red; everything
    # behind it is orange. As 840 completes, 841 becomes the new red frontier.
    def urgency_of(nid: str) -> tuple[str, list[str]]:
        # A done node is cleared — it blocks nothing, no urgency.
        if is_done(nid):
            return "none", []
        waiting = sorted(
            x for x in transitive_dependents(nid, frozenset())
            if not is_done(x) and goal_relevant(x)
        )
        if not waiting:
            return "none", []
        actionable_now = all(is_done(d) for d in deps_of.get(nid, []))
        return ("red" if actionable_now else "orange", waiting)

    urgency: dict[str, tuple[str, list[str]]] = {nid: urgency_of(nid) for nid in nodes}

    # --- attention = worst(state|rollup, urgency), worst-child over contains ----
    att: dict[str, str] = {}
    base_att: dict[str, str] = {}  # attention WITHOUT the urgency layer (state / rollup)

    def attention_of(nid: str, stack: frozenset[str]) -> str:
        if nid in att:
            return att[nid]
        if nid in stack:  # defensive: contains-cycle guard
            return "grey"
        kids = children.get(nid, [])
        if kids:
            base = worst([attention_of(k, stack | {nid}) for k in kids])
        elif family_of_type.get(nodes[nid].type, "workitem") == "workitem":
            base = health(nodes[nid].type, status.get(nid))
        else:  # object | bridge — health() from the static hint (TASK-858)
            base = object_health(nodes[nid].attributes)
        base_att[nid] = base
        u = urgency[nid][0]
        colour = worst([base, u]) if u != "none" else base
        att[nid] = colour
        return colour

    for nid in nodes:
        attention_of(nid, frozenset())

    # --- contains-subtree leaves + descendants (memoised, cycle-safe) ----------
    leaves_cache: dict[str, set[str]] = {}

    def leaves(nid: str, stack: frozenset[str]) -> set[str]:
        if nid in leaves_cache:
            return leaves_cache[nid]
        if nid in stack:
            return set()
        kids = children.get(nid, [])
        res = {nid} if not kids else set().union(*(leaves(k, stack | {nid}) for k in kids))
        leaves_cache[nid] = res
        return res

    desc_cache: dict[str, set[str]] = {}

    def descendants(nid: str, stack: frozenset[str]) -> set[str]:
        if nid in desc_cache:
            return desc_cache[nid]
        if nid in stack:
            return set()
        res: set[str] = set()
        for k in children.get(nid, []):
            res.add(k)
            res |= descendants(k, stack | {nid})
        desc_cache[nid] = res
        return res

    out: dict[str, dict] = {}
    for nid in nodes:
        lv = leaves(nid, frozenset())
        done = sum(1 for x in lv if status.get(x) in DONE_STATUSES)
        ip = sum(1 for x in lv if status.get(x) in INPROGRESS_STATUSES)
        todo = len(lv) - done - ip
        total = len(lv) or 1
        acount = sum(1 for d in descendants(nid, frozenset()) if att.get(d) in ("red", "orange"))
        u_colour, u_blocks = urgency[nid]
        out[nid] = {
            "attention": att[nid],
            "state": base_att.get(nid, "grey"),   # colour BEFORE the urgency layer
            "urgency": u_colour,                    # none|orange|red
            "urgency_blocks": u_blocks[:8],         # sample of not-done work waiting on nid
            "completion": done / total,
            "done": done,
            "in_progress": ip,
            "todo": todo,
            "attention_count": acount,
        }
    return out


def effective_bindings(session: Session) -> dict[str, str]:
    """Every workitem's world binding, inherited down the WORK hierarchy (TASK-1025).

    A workitem's own `works_on` edge wins. Without one, it inherits from the
    nearest ancestor that has one — epic -> story -> task is a single chain of
    work, so a task under a bound story is working on that story's object even
    when nobody wrote the edge.

    THE INHERITANCE IS OVER THE WORK HIERARCHY, NOT THE WORLD ONE. That
    distinction is the task's own warning and it is load-bearing: rollup travels
    up `contains` in the WORLD tree (ADR 0028 — work rolls up, never down), and
    reading the two as one tree is what produced the `above` bucket the owner
    rejected on 2026-08-08. This walks parent links between two WORKITEMS only;
    an object never inherits anything here.

    Returns {workitem_id: object_id}. A workitem with no bound ancestor is
    absent from the map rather than present with None — callers iterate it.

    Note the work hierarchy still shares the `contains` edge type with the world
    hierarchy until TASK-1030 gives it `decomposes_into`, which is exactly why
    both endpoints are family-checked below rather than trusting the edge type.
    """
    fam = {nt.id: nt.family for nt in session.scalars(select(NodeType)).all()}
    node_type = {n.id: n.type for n in session.scalars(select(Node)).all()}

    def is_workitem(nid: str) -> bool:
        return fam.get(node_type.get(nid)) == "workitem"

    own: dict[str, str] = {}
    parent: dict[str, str] = {}
    for e in session.scalars(select(Edge)).all():
        if e.edge_type == "works_on" and e.src in node_type and e.dst in node_type:
            own.setdefault(e.src, e.dst)
        elif e.edge_type == WORK_HIERARCHY_EDGE_TYPE and is_workitem(e.src) and is_workitem(e.dst):
            parent.setdefault(e.dst, e.src)

    resolved: dict[str, str] = dict(own)

    def resolve(nid: str, seen: set[str]) -> str | None:
        if nid in resolved:
            return resolved[nid]
        up = parent.get(nid)
        # `seen` is a cycle guard, not decoration: `contains` is not constrained
        # to be acyclic, and a cycle here would otherwise hang the read path
        # that every /api/graph call goes through.
        if up is None or up in seen:
            return None
        seen.add(up)
        found = resolve(up, seen)
        if found is not None:
            resolved[nid] = found
        return found

    for nid in list(node_type):
        if is_workitem(nid) and nid not in resolved:
            resolve(nid, {nid})
    return resolved


def work_load(session: Session, visible_ids: set[str] | None = None) -> dict[str, dict]:
    """Per-object work-load readout (ADR 0019 Addendum, TASK-860): for every
    node with >=1 inbound `works_on` edge, the count of linked workitems and
    their completion fraction (done / total, by workitem status).

    DESIGN CHOICE (per TASK-860's "state which you chose"): computed HERE, on
    read, rather than cached on Node — it's a rollup over a *bridge* edge
    (works_on), not the `contains` subtree the rest of this module derives, and
    at the graph's current scale a live join is cheap. app.py's /api/graph and
    /api/graph/tree call this and attach the result as `work_load` on every
    node in the response, so the frontend never has to walk edges itself to
    render the World-tree's per-object readout.

    `visible_ids` restricts the computation, `None` meaning no restriction —
    see `derive`'s note for why this is an INPUT filter and not an output one.
    Here the leak it closes is the most direct one on the board: `count` is
    drawn as "· N tasks" beside every world node, so an unfiltered rollup
    states the size of a hidden project's backlog in the sidebar of a project
    the caller CAN see.
    """
    nodes = {n.id: n for n in session.scalars(select(Node)).all()}
    if visible_ids is not None:
        nodes = {nid: n for nid, n in nodes.items() if nid in visible_ids}
    status = status_map(session, nodes)

    # EFFECTIVE bindings, not raw edges (TASK-1025): a workitem with no
    # `works_on` of its own counts against its nearest bound ancestor in the
    # work hierarchy. When this landed every one of the 323 workitems carried an
    # explicit binding, so it changed no count on the board — that identity is
    # the acceptance test (verify_effective_binding.py). It earns its keep from
    # here on: a workitem filed tomorrow without a binding is not invisible.
    by_object: dict[str, list[str]] = defaultdict(list)
    for workitem_id, obj_id in effective_bindings(session).items():
        if workitem_id in nodes and obj_id in nodes:
            by_object[obj_id].append(workitem_id)

    out: dict[str, dict] = {}
    for obj_id, workitem_ids in by_object.items():
        total = len(workitem_ids)
        done = sum(1 for w in workitem_ids if status.get(w) in DONE_STATUSES)
        out[obj_id] = {
            "count": total,
            "done": done,
            "completion": (done / total) if total else 0.0,
            "workitem_ids": sorted(workitem_ids),
            # Per-workitem status + completion date, so the World tree's
            # "· N tasks · X%" readout can be re-derived CLIENT-SIDE against the
            # board's age filter without a round-trip per slider move — and
            # without the World tab having to cross-reference a Work-tree
            # payload it does not otherwise need.
            "workitems": [
                {
                    "id": w,
                    "status": status.get(w),
                    "done": status.get(w) in DONE_STATUSES,
                    "completed_at": (_aware(nodes[w].completed_at).isoformat()
                                     if w in nodes and _aware(nodes[w].completed_at) else None),
                }
                for w in sorted(workitem_ids)
            ],
        }
    return out


def recompute_derived(session: Session, goal_config: dict[str, dict] | None = None) -> dict[str, dict]:
    """Recompute derived values and CACHE attention/completion/attention_count onto
    the node columns. Call after any write that changes status/edges (ingest today,
    a board status override, a probe-result change later). Returns the full
    derived map.

    `completed_at` is maintained here too, in the SAME unit of work: it is
    derived from the same effective status everything else is, and a board click
    that flips a node to done has to produce its completion date immediately —
    otherwise the node would sit unfiltered until the next container restart.
    """
    d = derive(session, goal_config)
    nodes = {n.id: n for n in session.scalars(select(Node)).all()}
    for nid, vals in d.items():
        n = nodes[nid]
        n.attention = vals["attention"]
        n.completion = vals["completion"]
        n.attention_count = vals["attention_count"]
    stamp_completion_dates(session, status_map(session, nodes))
    session.commit()
    return d
