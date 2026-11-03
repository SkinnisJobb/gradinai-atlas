"""`legacy_v0` routes, split out of `app.py` (STORY-oss-04); the code is moved, not changed.
Registered on the app by `main.py` via `include_router`.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

import db
from db import Component, Gate, Decision, session


def _legacy_v0_enabled() -> None:
    """ATLAS_LEGACY_V0=0 (STORY-oss-07): every route below answers 404, the same response as
    a path that does not exist, and the v0 tables are not created (`db.LEGACY_V0`). The routes
    stay registered, so the route table and its access classification do not change."""
    if not db.LEGACY_V0:
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(dependencies=[Depends(_legacy_v0_enabled)])
# NB: `attention` (two-axis derived values, TASK-854/855) is imported LAZILY
# inside the graph handlers, kept that way after TASK-1058 removed the
# `ingest_backlog` cycle that originally motivated it — `attention` itself
# only imports `models_graph`, so the cycle risk is gone, but nothing forces
# eager import back on and the lazy form costs nothing.

# --------------------------------------------------------------------------- #
# RAG maturity ordering: red is worst, purple is best. Rollup = worst child.
# --------------------------------------------------------------------------- #
RAG = ["red", "orange", "yellow", "green", "purple"]
RAG_ORDER = {c: i for i, c in enumerate(RAG)}


def worst(colours: list[str]) -> str:
    present = [c for c in colours if c in RAG_ORDER]
    if not present:
        return "grey"
    return min(present, key=lambda c: RAG_ORDER[c])


# --------------------------------------------------------------------------- #
# Phase-aware prioritization.
#
# The score is WSJF-lite: (value + urgency + risk) / effort — then multiplied by
# a phase weight per category, so the CURRENT focus steers what floats to the top.
# In "development" we down-weight infra/ops/security hardening; when we start
# shipping we shift focus to "hardening"/"production"/"maintenance" and Atlas
# re-ranks toward exactly the work that phase cares about.
# --------------------------------------------------------------------------- #
FOCUS_WEIGHTS = {
    "development": {"Backend": 1.0, "Frontend": 1.0, "Data": 1.0, "Agent": 0.9,
                    "Infra": 0.55, "Security": 0.6, "Ops": 0.35},
    "hardening":   {"Backend": 0.8, "Frontend": 0.7, "Data": 0.85, "Agent": 0.7,
                    "Infra": 0.9, "Security": 1.0, "Ops": 0.85},
    "production":  {"Backend": 0.6, "Frontend": 0.6, "Data": 0.7, "Agent": 0.6,
                    "Infra": 1.0, "Security": 1.0, "Ops": 1.0},
    "maintenance": {"Backend": 0.5, "Frontend": 0.5, "Data": 0.7, "Agent": 0.5,
                    "Infra": 0.9, "Security": 0.9, "Ops": 1.0},
}
FOCUS_BLURB = {
    "development": "Building things. Feature + data + agent work ranks high; production hardening waits.",
    "hardening":   "Getting ready to ship. Security, tests, and infra rise; new features cool off.",
    "production":  "Live. Reliability, security, and ops dominate; feature work is secondary.",
    "maintenance": "Steady state. Ops and upkeep lead; only high-value features get through.",
}


def focus_weight(category: str, focus: str) -> float:
    return FOCUS_WEIGHTS.get(focus, FOCUS_WEIGHTS["development"]).get(category, 0.7)


def prio_base(c: Component) -> float | None:
    """WSJF-lite intrinsic score. None if this node has no priority inputs."""
    if not c.effort or c.value is None:
        return None
    return (c.value + (c.urgency or 0) + (c.risk or 0)) / c.effort


def compute_priority(s: Session, focus: str) -> list[dict]:
    """Ranked schedulable nodes for a given focus, with blocker priority-inheritance."""
    comps = {c.id: c for c in s.scalars(select(Component)).all()}
    weighted: dict[str, float] = {}
    for cid, c in comps.items():
        b = prio_base(c)
        if b is not None:
            weighted[cid] = b * focus_weight(c.category, focus)

    def blocks_of(c: Component) -> list[str]:
        return [b.strip() for b in (c.blocks or "").split(",") if b.strip()]

    def eff(cid: str, seen: set[str]) -> float:
        if cid in seen:
            return weighted.get(cid, 0.0)
        seen.add(cid)
        own = weighted.get(cid, 0.0)
        for b in blocks_of(comps[cid]):
            if b in comps:
                own = max(own, eff(b, seen))
        return own

    kids_of = build_children(s)
    out = []
    for cid in weighted:
        c = comps[cid]
        own = weighted[cid]
        e = eff(cid, set())
        blocked = [comps[b].name for b in blocks_of(c) if b in comps]
        # a short human reason
        bits = []
        if (c.effort or 9) <= 2:
            bits.append("quick win")
        if (c.value or 0) >= 8:
            bits.append("high-leverage")
        if e > own + 0.01:
            bits.append(f"unblocks {len(blocked)} higher-priority item(s)")
        w = focus_weight(c.category, focus)
        if w < 0.65:
            bits.append(f"{c.category} — down-weighted in {focus} phase")
        elif w >= 1.0 and focus != "development":
            bits.append(f"{c.category} — front-and-center in {focus} phase")
        out.append({
            "id": cid, "name": c.name, "category": c.category,
            "status": effective_status(c, kids_of),
            "value": c.value, "effort": c.effort, "urgency": c.urgency, "risk": c.risk,
            "base": round(prio_base(c), 2), "weighted": round(own, 2), "score": round(e, 2),
            "inherited": e > own + 0.01, "blocks": blocked,
            "reason": ", ".join(bits) or "steady contribution",
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def gate_colour(gate: Gate) -> str:
    """A gate's colour: all requirements pass -> promotes_to; any fail -> orange; else yellow."""
    states = [r.state for r in gate.requirements]
    if states and all(s == "pass" for s in states):
        return gate.promotes_to
    if any(s == "fail" for s in states):
        return "orange"
    return "yellow"


def build_children(s: Session) -> dict[str | None, list[Component]]:
    """Group every component by parent_id → the adjacency index we build the tree from."""
    from collections import defaultdict
    idx: dict[str | None, list[Component]] = defaultdict(list)
    for c in s.scalars(select(Component)).all():
        idx[c.parent_id].append(c)
    return idx


def subtree_counts(c: Component, kids_of: dict) -> dict[str, int]:
    """Count this node + all descendants by effective maturity — feeds the 5 status squares."""
    counts: dict[str, int] = {}

    def walk(x: Component) -> None:
        st = effective_status(x, kids_of)
        counts[st] = counts.get(st, 0) + 1
        for ch in kids_of.get(x.id, []):
            walk(ch)

    walk(c)
    return counts


def effective_status(c: Component, kids_of: dict) -> str:
    """Leaf: explicit status, tempered by its own gates. Parent: worst child."""
    kids = kids_of.get(c.id, [])
    if kids:
        return worst([effective_status(ch, kids_of) for ch in kids])
    base = c.explicit_status or "grey"
    if c.gates:
        return worst([base] + [gate_colour(g) for g in c.gates])
    return base


def node(c: Component, kids_of: dict, dec_of: dict) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "kind": c.kind,
        "category": c.category,
        "wbs_path": c.wbs_path,
        "wbs_code": c.wbs_code,
        "owner": c.owner,
        "scope_label": c.scope_label,
        "description": c.description,
        "plan": c.plan,
        "goal": json.loads(c.goal) if c.goal else None,
        "priority": (
            {"value": c.value, "effort": c.effort, "urgency": c.urgency, "risk": c.risk,
             "base": round(prio_base(c), 2)}
            if prio_base(c) is not None else None
        ),
        "rollup": subtree_counts(c, kids_of),
        "decisions": dec_of.get(c.id, []),
        "status": effective_status(c, kids_of),
        "gates": [
            {
                "id": g.id, "name": g.name, "status": gate_colour(g),
                "promotes_to": g.promotes_to,
                "requirements": [
                    {"text": r.text, "axis": r.axis, "verify": r.verify, "state": r.state}
                    for r in g.requirements
                ],
            }
            for g in c.gates
        ],
        "children": [
            node(ch, kids_of, dec_of)
            for ch in sorted(kids_of.get(c.id, []), key=lambda x: x.wbs_code)
        ],
    }


def build_decisions(s: Session) -> dict[str, list[dict]]:
    dmap: dict[str, list[dict]] = {}
    for d in s.scalars(select(Decision)).all():
        dmap.setdefault(d.node_id, []).append(
            {"kind": d.kind, "ref": d.ref, "title": d.title,
             "status": d.status, "detail": d.detail, "path": d.path}
        )
    return dmap


@router.get("/api/tree")
def tree() -> JSONResponse:
    with session() as s:
        kids_of = build_children(s)
        dec_of = build_decisions(s)
        roots = sorted(kids_of.get(None, []), key=lambda x: x.wbs_code)
        return JSONResponse({"roots": [node(r, kids_of, dec_of) for r in roots]})


@router.get("/api/summary")
def summary() -> dict:
    counts = {c: 0 for c in RAG + ["grey"]}
    with session() as s:
        kids_of = build_children(s)
        for c in s.scalars(select(Component)).all():
            st = effective_status(c, kids_of)
            counts[st] = counts.get(st, 0) + 1
    return {"counts": counts}


@router.get("/api/priority")
def priority(focus: str = "development") -> dict:
    if focus not in FOCUS_WEIGHTS:
        focus = "development"
    with session() as s:
        return {
            "focus": focus,
            "focuses": list(FOCUS_WEIGHTS.keys()),
            "blurb": FOCUS_BLURB[focus],
            "items": compute_priority(s, focus),
        }


@router.get("/api/goals")
def goals() -> dict:
    """Every node that carries a gate/goal, with its rolled-up maturity."""
    with session() as s:
        kids_of = build_children(s)
        out = []
        for c in s.scalars(select(Component)).all():
            if not c.goal:
                continue
            g = json.loads(c.goal)
            out.append({
                "id": c.id, "name": c.name, "category": c.category,
                "kind": c.kind, "status": effective_status(c, kids_of), "goal": g,
            })
        return {"goals": out}
