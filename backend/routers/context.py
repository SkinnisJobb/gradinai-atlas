"""`GET /api/context?about=…` — Atlas answers "tell me about X" (ADR 0044 §0, STORY-atlas-context-core).

ONE CALL, A PACKAGE OF POINTERS. An agent asking about X gets, in one bounded response: the nodes
that best match X (the SEEDS), what sits around them (the NEIGHBOURHOOD), what would be affected
if they changed (the IMPACT set), the INTENTS that constrain them, the DECISIONS (ADRs) that bear
on X, and the DOCUMENTS to read next. Every entry is a pointer (id, type, name, summary, where it
came from); the text is one `atlas_get` / `atlas_doc_read` / `atlas_adr_get` away. That is the
`context-minimal` outcome: the fewest tokens that let an agent act correctly, budgets as
parameters, and bytes measured, so every part is capped by a parameter and says `truncated`
when the cap cut it.

CORE, NOT ADD-ON. Seeds come from `/api/search` itself (`routers/search.search_nodes`), so they
are exactly what a search would return. `mode=hybrid` (the default here: a new route changes no
existing answer) uses the retrieval add-on when an instance has it and degrades to full text,
saying so in `degraded`, when it does not; nothing else here touches the add-on.

VISIBILITY, PART BY PART, INSIDE EACH STATEMENT (ADR 0026, `atlas-visibility`):
  * seeds: the search route's own filter;
  * neighbourhood and impact: the caller's visible set is a predicate of the SQL that walks the
    edges, so a hidden node is never returned and NEVER PASSED THROUGH: an impact chain that
    runs through a node the caller may not see stops there, or the far end would disclose that
    something sits in between;
  * intents: `routers/nodes._intents_for(…, visible_ids)`, which drops inherited intents whose
    owner the caller may not see;
  * decisions: the ADR registry's own rule, the record's project must be visible;
  * documents: served `docs[]` entries of visible nodes only.
`verify_context.py` proves deletion-equivalence for the whole package, for a person and an agent.
"""
from __future__ import annotations

import json
from itertools import chain

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

import audit_log
from db import session
from deps import _visible_node_ids

router = APIRouter()

#: (parameter, default, low, high). Budgets are parameters (context-minimal); the highs keep a
#: single call from being an export.
BUDGETS = (
    ("seeds", 5, 1, 20),
    ("neighbours", 20, 0, 100),
    ("impact_depth", 2, 0, 5),
    ("impact", 20, 0, 100),
    ("intents", 20, 0, 100),
    ("decisions", 5, 0, 20),
    ("docs", 10, 0, 50),
)
MODES = ("fulltext", "semantic", "hybrid")
NEIGHBOUR_EDGES = ("contains", "requires", "works_on", "serves")
#: How a neighbour relates to its seed, worded for a reader rather than as an edge direction.
RELATION = {("contains", "in"): "parent", ("contains", "out"): "child",
            ("requires", "out"): "requires", ("requires", "in"): "required_by",
            ("works_on", "out"): "works_on", ("works_on", "in"): "worked_on_by",
            ("serves", "out"): "serves", ("serves", "in"): "served_by"}

#: Each neighbour ONCE, through the best-ranked seed it touches (DISTINCT ON picks that row), then
#: in seed order: a node next to two seeds is one pointer, not two.
NEIGHBOUR_SQL = text("""
SELECT seed, other, edge_type, direction, type, name, summary FROM (
    SELECT DISTINCT ON (x.other)
           x.seed, x.other, x.edge_type, x.direction, n.type, n.name, n.summary,
           array_position(CAST(:seeds AS text[]), x.seed::text) AS pos
    FROM (
        SELECT e.src AS seed, e.dst AS other, e.edge_type, 'out' AS direction
          FROM edge e WHERE e.src = ANY(:seeds) AND e.edge_type = ANY(:edges)
        UNION
        SELECT e.dst AS seed, e.src AS other, e.edge_type, 'in' AS direction
          FROM edge e WHERE e.dst = ANY(:seeds) AND e.edge_type = ANY(:edges)
    ) x
    JOIN node n ON n.id = x.other
    WHERE NOT (x.other = ANY(:seeds))
      AND (:all_visible OR x.other = ANY(:visible))
      AND (:all_families OR n.type IN (SELECT t.id FROM node_type t WHERE t.family = ANY(:families)))
    ORDER BY x.other, array_position(CAST(:seeds AS text[]), x.seed::text), x.edge_type, x.direction
) d
ORDER BY pos, edge_type, direction, other
LIMIT :lim
""")

#: Who REQUIRES the seeds, transitively. The visible set is applied at EVERY step, so a chain
#: never runs through a hidden node (see the module docstring).
IMPACT_SQL = text("""
WITH RECURSIVE walk(id, depth, via) AS (
    SELECT e.src, 1, e.dst
      FROM edge e
     WHERE e.edge_type = 'requires' AND e.dst = ANY(:seeds)
       AND (:all_visible OR e.src = ANY(:visible))
    UNION
    SELECT e.src, w.depth + 1, e.dst
      FROM edge e JOIN walk w ON e.dst = w.id
     WHERE e.edge_type = 'requires' AND w.depth < :depth
       AND (:all_visible OR e.src = ANY(:visible))
)
SELECT w.id, min(w.depth) AS depth, min(w.via) AS via, n.type, n.name, n.summary
FROM walk w JOIN node n ON n.id = w.id
WHERE NOT (w.id = ANY(:seeds))
GROUP BY w.id, n.type, n.name, n.summary
ORDER BY depth, w.id
LIMIT :lim
""")

#: ADRs ranked against the question's words OR'd (an ADR's text rarely holds every word of a
#: question), visible projects only, or named by the seeds' own search hits.
DECISION_SQL = text("""
SELECT r.adr_id, r.project_node_id, r.title, r.status, r.legacy_number,
       ts_rank(to_tsvector('english', coalesce(r.adr_id,'') || ' ' || coalesce(r.title,'') || ' '
               || coalesce(r.content,'')),
               replace(plainto_tsquery('english', :q)::text, '&', '|')::tsquery) AS rank
FROM adr_record r JOIN node n ON n.id = r.project_node_id
WHERE (:all_visible OR r.project_node_id = ANY(:visible))
  AND (r.adr_id = ANY(:named)
       OR (plainto_tsquery('english', :q)::text <> ''
           AND to_tsvector('english', coalesce(r.adr_id,'') || ' ' || coalesce(r.title,'') || ' '
               || coalesce(r.content,''))
               @@ replace(plainto_tsquery('english', :q)::text, '&', '|')::tsquery))
ORDER BY (r.adr_id = ANY(:named)) DESC, rank DESC, r.adr_id
LIMIT :lim
""")


def _bad(field: str, detail: str) -> JSONResponse:
    return JSONResponse({"error": "bad_parameter", "field": field, "detail": detail}, status_code=400)


def _pointer(node_id: str, ntype: str, name: str, summary: str | None, **extra) -> dict:
    return {"id": node_id, "type": ntype, "name": name, "summary": summary, **extra}


def _cut(items: list, limit: int) -> dict:
    """A part as `{items, truncated}`: the caller is told when the budget cut it."""
    return {"items": items[:limit], "truncated": len(items) > limit}


def _seeds(request: Request, about: str, mode: str, families: str | None, limit: int) -> dict:
    """The search route's own answer, so seeds are exactly what a search returns, filter included.
    One more than the budget is asked for, so `truncated` is known."""
    from routers.search import search_nodes
    # TASK-1382: one context call is ONE audit row, the `GET /api/context` row below, which
    # names the seeds among everything else served. The search is this route's own step, not a
    # read the agent asked for, so it records nothing here.
    with audit_log.nested():
        resp = search_nodes(about, request, families=families, page=1, page_size=limit + 1,
                            mode=None if mode == "fulltext" else mode)
    return json.loads(resp.body)


def _docs(s: Session, seeds: list[dict], limit: int) -> dict:
    """Search's document hits first (they matched X), then each seed's other served documents."""
    rows = dict(s.execute(text("SELECT id, attributes FROM node WHERE id = ANY(:ids)"),
                          {"ids": [x["id"] for x in seeds]}).all())
    out, seen = [], set()
    for hit_first in (True, False):
        for seed in seeds:
            for d in (rows.get(seed["id"]) or {}).get("docs") or []:
                if not isinstance(d, dict) or not d.get("path"):
                    continue
                if (d["path"] in seed.get("doc_paths", [])) != hit_first:
                    continue
                key = (seed["id"], d["path"])
                if key in seen:
                    continue
                seen.add(key)
                out.append({"node_id": seed["id"], "path": d["path"], "title": d.get("title"),
                            "kind": d.get("kind"), "matched": hit_first,
                            **({"external": True} if d.get("external") else {})})
    return _cut(out, limit)


@router.get("/api/context")
def context(about: str, request: Request, mode: str = "hybrid", families: str | None = "object,bridge",
            seeds: int = 5, neighbours: int = 20, impact_depth: int = 2, impact: int = 20,
            intents: int = 20, intents_full: int = 0, decisions: int = 5, docs: int = 10) -> JSONResponse:
    """The context package for `about`. Every part is a pointer list capped by its parameter.
    `families=all` lets seeds include workitems; the default is the world."""
    if not about or not about.strip():
        return _bad("about", "about is required: the thing to tell you about.")
    if mode not in MODES:
        return _bad("mode", f"unknown mode {mode!r} — legal: {', '.join(MODES)}.")
    budget = {"seeds": seeds, "neighbours": neighbours, "impact_depth": impact_depth,
              "impact": impact, "intents": intents, "decisions": decisions, "docs": docs}
    for name, _default, low, high in BUDGETS:
        if not low <= budget[name] <= high:
            return _bad(name, f"{name} must be between {low} and {high}.")
    if intents_full not in (0, 1):
        return _bad("intents_full", "intents_full is 0 or 1.")
    fam = None if (families or "").strip().lower() in ("", "all") else families

    found = _seeds(request, about, mode, fam, seeds)
    if "items" not in found:  # the search route refused (e.g. an unknown family): pass it on
        return JSONResponse(found, status_code=400)
    seed_items = found["items"]
    seed_part = _cut(seed_items, seeds)
    seed_ids = [x["id"] for x in seed_part["items"]]
    want = sorted({f.strip() for f in fam.split(",") if f.strip()}) if fam else []

    with session() as s:
        visible = _visible_node_ids(s)
        vis = {"all_visible": visible is None, "visible": sorted(visible or ())}

        rows = s.execute(NEIGHBOUR_SQL, {**vis, "seeds": seed_ids, "edges": list(NEIGHBOUR_EDGES),
                                         "all_families": not want, "families": want,
                                         "lim": neighbours + 1}).all() if seed_ids else []
        neighbour_part = _cut([_pointer(r.other, r.type, r.name, r.summary,
                                        via={"from": r.seed, "edge": r.edge_type,
                                             "relation": RELATION.get((r.edge_type, r.direction))})
                               for r in rows], neighbours)

        rows = s.execute(IMPACT_SQL, {**vis, "seeds": seed_ids, "depth": impact_depth,
                                      "lim": impact + 1}).all() if seed_ids and impact_depth else []
        impact_part = _cut([_pointer(r.id, r.type, r.name, r.summary, depth=r.depth,
                                     requires=r.via) for r in rows], impact)

        from routers.nodes import _intents_for  # the inheritance walk every node read uses
        found_intents, seen = [], set()
        for sid in seed_ids:
            got = _intents_for(s, sid, visible)
            for which in ("own", "inherited"):
                for it in got[which]:
                    if it["id"] in seen:
                        continue
                    seen.add(it["id"])
                    p = {"id": it["id"], "kind": it["kind"], "slug": it["slug"], "title": it["title"],
                         "owner": it["node_id"], "applies_to": sid, "inherited": which == "inherited"}
                    if intents_full:
                        p["statement"] = it["statement"]
                    found_intents.append(p)
        intent_part = _cut(found_intents, intents)

        named = sorted({a for x in seed_items for a in x.get("adr_ids", [])})
        # The question's words AND the seeds' ids and names: an ADR is written in one language,
        # often not the question's, but it names the components it decides about.
        terms = " ".join([about] + [t for x in seed_part["items"] for t in (x["id"], x["name"] or "")])
        rows = s.execute(DECISION_SQL, {**vis, "q": terms, "named": named,
                                        "lim": decisions + 1}).all() if decisions else []
        decision_part = _cut([{"id": r.adr_id, "project": r.project_node_id, "title": r.title,
                               "status": r.status,
                               "legacy": None if r.legacy_number is None else f"ADR {r.legacy_number:04d}"}
                              for r in rows], decisions)

        doc_part = _docs(s, seed_part["items"], docs)

    body = {
        "about": about, "mode": mode,
        **({"degraded": found["degraded"]} if found.get("degraded") else {}),
        "seeds": seed_part, "neighbourhood": neighbour_part, "impact": impact_part,
        "intents": intent_part, "decisions": decision_part, "docs": doc_part,
        "budgets": {**budget, "intents_full": intents_full},
    }
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    body["bytes"] = len(raw.encode("utf-8"))
    # A generator: with ATLAS_AUDIT_READS off it is never iterated (STORY-oss-16, TASK-1382).
    served = chain(seed_ids, (x["id"] for x in neighbour_part["items"]),
                   (x["id"] for x in impact_part["items"]))
    audit_log.record_read(request, "GET /api/context", served)
    return JSONResponse(body)
