"""The graph-write verbs — node, edge, doc and intent mutation, plus the
destructive `atlas_delete` facade. `EPIC-atlas-mcp`, TASK-1090/1091/1092/1093.

One module, `register(mcp)` called once from `mcp_server.py`'s bottom line, per
the module-discipline note in this epic's brief (three agents work in `mcp/app/`
in parallel; each owns exactly one `tools_*.py` file and touches `mcp_server.py`
for nothing but that one call).

No `from __future__ import annotations` — same reason as every other module in
this package that defines a `@mcp.tool()`: FastMCP inspects the REAL signature
to build a JSON-Schema, and postponed annotation evaluation breaks that.

Every tool returns the uniform envelope from envelope.py:
  success -> {"ok": True,  "result": <...>}
  failure -> {"ok": False, "error": {"type": <code>, "message": <str>}}

Local validation (bad slug, bad path prefix, missing required field for the
branch taken) raises `AtlasError` BEFORE any network call, exactly like a
remote failure would — the caller sees one error shape regardless of where the
refusal happened, and `envelope()` is what makes that shape uniform.
"""

import os
import re

import httpx

from .client import client
from .envelope import AtlasError, envelope

# --------------------------------------------------------------------------- #
# Vocabulary mirrored from the backend, on purpose kept small and duplicated
# rather than fetched over the wire: these are fixed by ADR 0029 (`docs/nodes/`
# prefix) and ADR 0032 (the four intent kinds, the slug shape) and validating
# them locally means a malformed call never reaches atlas-api at all. If either
# ADR is ever revised, this module needs its own edit — that is the accepted
# cost of not adding a round trip to validate a constant tuple.
# --------------------------------------------------------------------------- #
DOCS_PREFIX = "docs/nodes/"                              # mirrors app.py DOCS_PREFIX
INTENT_KINDS = ("constraint", "principle", "non-goal", "outcome")  # mirrors node_types.INTENT_KINDS
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")       # mirrors app.py's slug check

#: STORY-oss-12: `atlas_node_write(key=...)` — omitted leaves a project's key
#: alone, `null` clears it, a string sets it. The same sentinel idiom as
#: tools_work's `_UNSET`; the shape, permission and immutability checks are all
#: atlas-api's (PATCH /api/node `key`), so nothing is validated here.
_KEY_UNSET = object()


# --------------------------------------------------------------------------- #
# Delete is off by default, and its enabled/disabled state is a plain
# importable value — TASK-1093's own acceptance criterion is that a caller can
# SEE the answer (via atlas_capabilities, built by another agent in
# tools_read.py) rather than discover it by getting refused. Evaluated once at
# import time, same idiom as client.py's ATLAS_BASE_URL check.
# --------------------------------------------------------------------------- #
DELETE_ENABLED = os.environ.get("ATLAS_MCP_ENABLE_DELETE", "").strip().lower() in (
    "1", "true", "yes", "on",
)

#: Why atlas_delete is not registered, or None when it is.
#:
#: REGISTRATION NO LONGER ASKS /api/me (caller auth, 2026-10). It used to check at
#: startup that the service's own token held `admin`, because that one token was
#: every caller. Now there is no service token: each call carries its caller's own
#: credential, so whether a delete is allowed is decided per call, by atlas-api's
#: WRITE_SCOPE_ROUTES (every DELETE route costs `admin`), for that caller. Nothing
#: here grants, checks or caches a scope; a caller without `admin` gets the API's
#: 403 back as `forbidden`. The env flag only decides whether the tool is offered.
DELETE_UNAVAILABLE_REASON: str | None = None


def _delete_enabled() -> bool:
    """True when ATLAS_MCP_ENABLE_DELETE is set. Makes no network call."""
    global DELETE_UNAVAILABLE_REASON
    if not DELETE_ENABLED:
        DELETE_UNAVAILABLE_REASON = "ATLAS_MCP_ENABLE_DELETE is not set"
        return False
    DELETE_UNAVAILABLE_REASON = None
    return True


# --------------------------------------------------------------------------- #
# Thin request helpers. client.py exposes only `.get()` publicly (TASK-1078
# shipped exactly one tool and needed nothing else); everything here reuses
# the SAME shared client instance via its `_request` method rather than
# constructing a second httpx.Client — same base_url, same Bearer header, same
# no-redirect policy, same error translation into AtlasError.
# --------------------------------------------------------------------------- #
def _post(path: str, payload: dict) -> dict:
    return client.post(path, json=payload)


def _patch(path: str, payload: dict) -> dict:
    return client.patch(path, json=payload)


def _put(path: str, payload: dict) -> dict:
    return client.put(path, json=payload)


def _delete(path: str, payload: dict | None = None, params: dict | None = None) -> dict:
    kwargs: dict = {}
    if payload is not None:
        kwargs["json"] = payload
    if params is not None:
        kwargs["params"] = params
    return client.delete(path, **kwargs)


def _get_node_or_none(node_id: str) -> dict | None:
    try:
        return client.get(f"/api/node/{node_id}")
    except AtlasError as e:
        if e.error_type == "not_found":
            return None
        raise


def _raw(method: str, path: str, json_body: dict | None = None,
         params: dict | None = None) -> tuple[int, dict | None]:
    """Like `client._request`, but returns `(status, body)` instead of raising
    on a 4xx/5xx. Used ONLY where a caller needs a structured field atlas-api's
    error body carries (`edge_count`) that `client._to_atlas_error` discards —
    it folds `detail`/`field` into an English sentence and drops everything
    else, which is right for a message a human reads and wrong for a count a
    program needs to compare. Still goes through the shared `client._client`
    (same auth, same base_url, same no-redirect policy) — this is not a second
    HTTP path, just a second way of reading the one response.
    """
    try:
        resp = client._client.request(method, path, json=json_body, params=params)
    except httpx.RequestError as e:
        raise AtlasError("unreachable", f"{method} {path} failed: {e}") from e
    if resp.is_redirect:
        raise AtlasError(
            "unexpected_redirect",
            f"{method} {path} returned {resp.status_code} toward "
            f"{resp.headers.get('location', '<no location>')!r} — a Bearer caller should "
            "never be redirected.",
        )
    body = None
    if resp.content:
        try:
            body = resp.json()
        except ValueError:
            body = None
    return resp.status_code, body


# --------------------------------------------------------------------------- #
# TASK-1090 — atlas_node_write, atlas_edge_write (+ edge delete by id)
# --------------------------------------------------------------------------- #
def _node_write_impl(id: str, type: str | None, name: str | None, summary: str | None,
                      abstract: str | None, detail: str | None, owner: str | None,
                      source: str | None, attributes: dict | None,
                      attributes_mode: str, key=_KEY_UNSET) -> dict:
    if attributes_mode not in ("merge", "replace"):
        raise AtlasError(
            "invalid_request",
            f"attributes_mode must be 'merge' or 'replace', got {attributes_mode!r}",
        )

    existing = _get_node_or_none(id)

    if existing is None:
        if not type or not name:
            raise AtlasError(
                "invalid_request",
                f"node {id!r} does not exist; creating it requires both type= and name= "
                "— this tool will not guess a node_type or a place in the tree for you "
                "(ADR 0030's layer-vs-capability call is a human judgement, and a tool that "
                "automates it will get it wrong confidently).",
            )
        payload: dict = {"id": id, "type": type, "name": name}
        for field, value in (
            ("summary", summary), ("abstract", abstract), ("detail", detail),
            ("owner", owner), ("source", source),
        ):
            if value is not None:
                payload[field] = value
        if attributes is not None:
            payload["attributes"] = attributes
        created = _post("/api/node", payload)
        created["_mcp_action"] = "created"
        if key is not _KEY_UNSET:
            # POST /api/node takes no key: it is set on the node that now exists,
            # with PATCH's own checks (STORY-oss-12).
            keyed = _patch(f"/api/node/{id}", {"key": key})
            keyed["_mcp_action"] = "created"
            return keyed
        return created

    patch: dict = {}
    for field, value in (
        ("type", type), ("name", name), ("summary", summary), ("abstract", abstract),
        ("detail", detail), ("owner", owner), ("source", source),
    ):
        if value is not None:
            patch[field] = value

    if attributes is not None:
        if attributes_mode == "replace":
            patch["attributes"] = attributes
        else:
            # Read-modify-write. PATCH /api/node assigns `attributes` wholesale
            # (app.py's edit_node — `setattr(n, field, value)` over the raw
            # dict), so sending only the caller's partial dict would silently
            # drop every key it did not name. Same defence
            # tools/seed-node-docs/apply_node_docs.py uses for the same reason.
            merged = dict(existing.get("attributes") or {})
            merged.update(attributes)
            patch["attributes"] = merged

    if key is not _KEY_UNSET:
        patch["key"] = key

    if not patch:
        result = dict(existing)
        result["_mcp_action"] = "noop"
        result["_mcp_note"] = "node already exists and no fields were supplied to change"
        return result

    patched = _patch(f"/api/node/{id}", patch)
    patched["_mcp_action"] = "patched"
    return patched


def _translate_edge_conflict(e: AtlasError) -> AtlasError:
    """`create_edge`'s two 409s, translated from atlas-api's generic
    `{"error": ..., "detail": ...}` into a message that names what to do next.

    `client._to_atlas_error` already folds the body's `error` field (the
    machine code, e.g. `family_mismatch`/`binding_exists`) and `detail` (the
    human sentence) into `str(exc)`, so both substrings are reliably present —
    that is what the checks below key on, without needing a second HTTP round
    trip to re-fetch the structured body.
    """
    text = str(e)
    if "family_mismatch" in text:
        return AtlasError(
            "conflict",
            f"{text} — edge-type families are fixed on atlas-api (EDGE_FAMILY_RULES): "
            "'contains'/'uses'/'instance_of' require an object on both ends, 'works_on' "
            "requires a workitem source and an object destination. A work-hierarchy edge "
            "(epic contains story, story contains task) can NEVER be made through "
            "atlas_edge_write at all, matching or not — it only exists through "
            "POST /api/workitem, because EDGE_FAMILY_RULES['contains'] is object->object "
            "and a workitem has no family match there. Re-check what you named as src/dst.",
        )
    if "binding_exists" in text:
        return AtlasError(
            "conflict",
            f"{text} — ADR 0028 §1: at most one works_on binding per workitem. To rebind, "
            "delete the existing edge (the id is named above) with atlas_edge_delete first, "
            "then retry this call.",
        )
    return e


def _edge_write_impl(src: str, dst: str, edge_type: str, directed: bool) -> dict:
    payload = {"src": src, "dst": dst, "edge_type": edge_type, "directed": directed}
    try:
        return _post("/api/edge", payload)
    except AtlasError as e:
        if e.error_type == "conflict":
            raise _translate_edge_conflict(e) from e
        raise


def _edge_delete_impl(edge_id: int) -> dict:
    return _delete(f"/api/edge/{edge_id}")


# --------------------------------------------------------------------------- #
# TASK-1091 — atlas_doc_write
# --------------------------------------------------------------------------- #
def _doc_write_impl(node_id: str, path: str, content: str | None, publish: bool) -> dict:
    if not path.startswith(DOCS_PREFIX):
        raise AtlasError(
            "invalid_request",
            f"doc path {path!r} must start with {DOCS_PREFIX!r} — mirrors atlas-api's own "
            "check on PUT /api/node/{id}/doc (the read route serves nothing else), checked "
            "here so a bad path never makes the round trip.",
        )
    if content is None and not publish:
        raise AtlasError(
            "invalid_request",
            "nothing to do: pass content=... to write, publish=True to publish, or both — "
            "the two acts are independent and this call asked for neither.",
        )

    node = client.get(f"/api/node/{node_id}")
    attrs = dict(node.get("attributes") or {})
    docs = list(attrs.get("docs") or [])
    already_published = any(isinstance(d, dict) and d.get("path") == path for d in docs)

    result: dict = {"node_id": node_id, "path": path, "was_already_published": already_published}

    content_written = False
    if content is not None:
        put_resp = _put(f"/api/node/{node_id}/doc", {"path": path, "content": content})
        content_written = True
        result["content_written"] = True
        result["created"] = put_resp.get("created")
        # A PUT over existing content returns 200 with no diff — say so plainly
        # rather than let a 200 read as "nothing happened".
        result["overwritten"] = not put_resp.get("created")
        result["bytes_written"] = put_resp.get("bytes")
        result["served_after_write"] = put_resp.get("served")
    else:
        result["content_written"] = False
        result["overwritten"] = None

    if not publish:
        result["publish_action"] = "not-requested"
        result["published"] = already_published
        return result

    if already_published:
        result["publish_action"] = "already-published"
        result["published"] = True
        return result

    if not content_written:
        # NEVER publish a path this call did not write or verify present.
        # `GET /api/node/{id}/doc` is gated on the docs[] allowlist FIRST (see
        # app.py:node_doc) — for a path not yet published, a pre-check GET
        # 404s on the allowlist regardless of whether content already exists
        # in the table, so "unpublished" and "no content" are indistinguishable
        # from here without writing it. Refusing is what stops the exact live
        # defect TASK-1091 exists to prevent (2026-08-14: an applier added a
        # docs[] entry, correctly refused to overwrite existing content, and
        # published something nobody had reviewed as the net effect).
        result["publish_action"] = "refused-unverified"
        result["published"] = False
        raise AtlasError(
            "invalid_request",
            f"refusing to publish {path!r} on {node_id!r}: this call did not write its "
            "content, and the path is not already served, so presence cannot be verified "
            "without risking publishing stale or absent content. Pass content=... to write "
            "and publish together, or only publish a path that is already served.",
        )

    entry = {"path": path, "kind": "architecture", "title": path.rsplit("/", 1)[-1]}
    docs.append(entry)
    attrs["docs"] = docs
    _patch(f"/api/node/{node_id}", {"attributes": attrs})
    result["publish_action"] = "added"
    result["published"] = True
    result["docs_entry"] = entry
    result["docs_entry_note"] = (
        "kind/title are placeholders (architecture / filename) — edit them with "
        "atlas_node_write(attributes={'docs': [...]}, attributes_mode='replace') if you want "
        "something else; this tool does not accept them as parameters."
    )
    return result


# --------------------------------------------------------------------------- #
# TASK-1092 — atlas_intent_write, atlas_intent_delete
#
# THIS REVERSES A STANDING RULE. CLAUDE.md's "agents read intents and propose;
# they do not write them" was true through 2026-08-13 and was reversed
# for v1 (owner decision, 2026-08-14; the ADR carrying the reversal is TASK-1097's job, not
# this one's — do not edit CLAUDE.md from here). These two tools ARE agent
# write access to node_intent. Treat that as real capability, not a mistake to
# route around.
# --------------------------------------------------------------------------- #
def _validate_intent_kind(kind: str) -> None:
    if kind not in INTENT_KINDS:
        raise AtlasError(
            "invalid_request",
            f"kind must be one of {', '.join(INTENT_KINDS)}, got {kind!r}",
        )


def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise AtlasError(
            "invalid_request",
            f"slug {slug!r} must be lowercase kebab-case, 1-64 chars, starting alphanumeric "
            "(matches [a-z0-9][a-z0-9-]{0,63})",
        )


def _validate_nonempty(field: str, value: str) -> None:
    if not value.strip():
        raise AtlasError("invalid_request", f"{field} must not be empty")


def _intent_create_impl(node_id: str, slug: str, kind: str, title: str, statement: str,
                         source: str | None, position: int | None) -> dict:
    _validate_intent_kind(kind)
    _validate_slug(slug)
    _validate_nonempty("title", title)
    _validate_nonempty("statement", statement)

    payload: dict = {"slug": slug, "kind": kind, "title": title, "statement": statement}
    if source is not None:
        payload["source"] = source
    if position is not None:
        payload["position"] = position

    try:
        return _post(f"/api/node/{node_id}/intent", payload)
    except AtlasError as e:
        if e.error_type == "conflict":
            # A duplicate (node_id, slug) is a real answer, not a failure —
            # same instinct tools/seed-intents/apply_intents.py already uses
            # (409 -> SKIP, no --force).
            return {"already_present": True, "node_id": node_id, "slug": slug, "detail": str(e)}
        raise


def _intent_patch_impl(intent_id: int, slug: str | None, kind: str | None, title: str | None,
                        statement: str | None, source: str | None,
                        position: int | None) -> dict:
    if kind is not None:
        _validate_intent_kind(kind)
    if slug is not None:
        _validate_slug(slug)
    if title is not None:
        _validate_nonempty("title", title)
    if statement is not None:
        _validate_nonempty("statement", statement)

    payload: dict = {}
    for field, value in (
        ("slug", slug), ("kind", kind), ("title", title), ("statement", statement),
        ("source", source), ("position", position),
    ):
        if value is not None:
            payload[field] = value
    if not payload:
        raise AtlasError("invalid_request", "nothing to change: pass at least one field")

    try:
        return _patch(f"/api/intent/{intent_id}", payload)
    except AtlasError as e:
        if e.error_type == "conflict":
            return {"already_present": True, "intent_id": intent_id, "detail": str(e)}
        raise


def _intent_write_impl(node_id: str | None, intent_id: int | None, slug: str | None,
                        kind: str | None, title: str | None, statement: str | None,
                        source: str | None, position: int | None) -> dict:
    if intent_id is not None:
        return _intent_patch_impl(intent_id, slug, kind, title, statement, source, position)
    if node_id is not None:
        missing = [n for n, v in (
            ("slug", slug), ("kind", kind), ("title", title), ("statement", statement),
        ) if v is None]
        if missing:
            raise AtlasError(
                "invalid_request",
                f"creating an intent (no intent_id given, so this is a create against "
                f"node_id={node_id!r}) requires {', '.join(missing)}",
            )
        return _intent_create_impl(node_id, slug, kind, title, statement, source, position)
    raise AtlasError("invalid_request", "pass node_id= to create an intent or intent_id= to patch one")


def _intent_delete_impl(intent_id: int) -> dict:
    # DELETE /api/intent/{n} requires a JSON body (IntentDelete has no
    # server-side default on this route, unlike /api/edge/{id}) — a bodyless
    # call gets 422, not 200. `{}` satisfies it (every field is optional).
    return _delete(f"/api/intent/{intent_id}", payload={})


# --------------------------------------------------------------------------- #
# TASK-1093 — atlas_delete. Off unless ATLAS_MCP_ENABLE_DELETE is truthy.
# --------------------------------------------------------------------------- #
def _incident_edge_count(node_id: str) -> int:
    """Count every edge incident to `node_id`, using a READ route.

    This exists because the two obvious sources are both wrong:

    * `GET /api/node/{id}`'s projections (`parents` + `bindings` +
      `dependencies` + `dependants`) MISS whole edge types. Measured
      2026-08-16 on `atlas-mcp`: those projections total **1** while the node
      actually has **12** incident edges (10 `works_on`, 1 `hosted_on`,
      1 `contains`). A caller who confirmed `expected_edge_count=1` from that
      would have authorised the destruction of twelve.
    * `DELETE /api/node/{id}`'s own 409 reports the true count, but it is a
      DELETE, so it needs the `admin` scope. A token without `admin` gets 403
      before the count is ever reported — meaning the guardrail silently lost
      the ability to tell a right count from a wrong one at exactly the moment
      it mattered. A check that depends on a call you may not make is not a
      check.

    `/api/graph?focus=…` is a read, needs no `admin`, and returns the real
    edges. `truncated` is checked because a capped neighbourhood would
    undercount, and undercounting here authorises deletion.
    """
    doc = client.get("/api/graph", params={"focus": node_id, "depth": 1})
    if doc.get("truncated"):
        raise AtlasError(
            "conflict",
            f"the neighbourhood around {node_id!r} came back truncated, so its edge count "
            "cannot be established. Refusing rather than guessing at a number that "
            "authorises a cascade delete.",
        )
    return sum(
        1 for e in doc.get("edges", [])
        if e.get("src") == node_id or e.get("dst") == node_id
    )


def _delete_node_impl(node_id: str, expected_edge_count: int | None) -> dict:
    if expected_edge_count is None:
        raise AtlasError(
            "invalid_request",
            "expected_edge_count is required to delete a node — call this once and let it "
            "refuse: the refusal names the real count and deletes nothing. Do NOT count "
            "`parents`+`bindings`+`dependencies`+`dependants` from GET /api/node/{id}; those "
            "projections miss whole edge types and undercount (measured: 1 against a real 12 "
            "on a live node), and an undercount here is what authorises a cascade. "
            "This friction is deliberate: a two-step with a fresh read in between, before a "
            "cascade that also removes acceptance criteria, results, documents and intents.",
        )

    # Establish the real count with a READ first. Two reasons this comes before
    # any DELETE: it works on a token without `admin` (so the guardrail cannot
    # silently degrade into a 403), and a mismatch is then refused without a
    # destructive verb ever reaching atlas-api.
    real_count = _incident_edge_count(node_id)
    if real_count != expected_edge_count:
        raise AtlasError(
            "conflict",
            f"node {node_id!r} actually has {real_count} edge(s), not the "
            f"{expected_edge_count} you passed — nothing was deleted and no delete was "
            f"attempted. Re-read and retry with expected_edge_count={real_count} if that is "
            "really what you intend to remove.",
        )

    status, body = _raw("DELETE", f"/api/node/{node_id}", params={})
    if status == 404:
        raise AtlasError("not_found", (body or {}).get("error") or f"unknown node {node_id!r}")
    if status == 401:
        raise AtlasError("unauthorized", "authentication required")
    if status == 200:
        # No edges existed — deleted outright, cascade was never needed.
        result = dict(body or {})
        result["cascade_used"] = False
        return result
    if status == 409 and isinstance(body, dict) and body.get("error") == "node_has_edges":
        real_count = body.get("edge_count")
        if real_count != expected_edge_count:
            raise AtlasError(
                "conflict",
                f"node {node_id!r} actually has {real_count} edge(s), not the "
                f"{expected_edge_count} you passed — nothing was deleted. Re-read and retry "
                f"with expected_edge_count={real_count} if that is really what you intend to "
                "remove.",
            )
        status2, body2 = _raw("DELETE", f"/api/node/{node_id}", params={"cascade": 1})
        if status2 != 200:
            raise AtlasError(
                f"http_{status2}",
                f"cascade delete of {node_id!r} failed after the count matched: "
                f"{status2} {body2}",
            )
        result = dict(body2 or {})
        result["cascade_used"] = True
        return result
    raise AtlasError(f"http_{status}", f"DELETE /api/node/{node_id} -> {status} {body}")


def _delete_doc_impl(node_id: str, path: str) -> dict:
    node = client.get(f"/api/node/{node_id}")
    attrs = dict(node.get("attributes") or {})
    docs = list(attrs.get("docs") or [])
    entry_present = any(isinstance(d, dict) and d.get("path") == path for d in docs)

    # Remove the docs[] entry BEFORE the content, never after. If content were
    # deleted first and the entry-removal PATCH then failed, the node would be
    # left advertising a document whose GET 404s — exactly the dangling state
    # this ordering exists to make unreachable. The reverse order's worst case
    # (entry removed, content-delete then fails) is harmless: orphaned,
    # unpublished content nobody can reach.
    removed_entry = False
    if entry_present:
        remaining = [d for d in docs if not (isinstance(d, dict) and d.get("path") == path)]
        attrs["docs"] = remaining
        _patch(f"/api/node/{node_id}", {"attributes": attrs})
        removed_entry = True

    content_deleted = False
    note = None
    try:
        _delete(f"/api/node/{node_id}/doc", params={"path": path})
        content_deleted = True
    except AtlasError as e:
        if e.error_type == "not_found":
            note = "no stored content at this path" + (
                " (the docs[] entry has been removed; nothing else to delete)"
                if removed_entry else " (and no docs[] entry either — nothing to delete)"
            )
        else:
            raise

    return {
        "node_id": node_id, "path": path,
        "docs_entry_removed": removed_entry,
        "content_deleted": content_deleted,
        "note": note,
    }


def _delete_impl(kind: str, node_id: str | None, edge_id: int | None, path: str | None,
                  intent_id: int | None, expected_edge_count: int | None) -> dict:
    if kind == "node":
        if not node_id:
            raise AtlasError("invalid_request", "kind='node' requires node_id")
        return _delete_node_impl(node_id, expected_edge_count)
    if kind == "edge":
        if edge_id is None:
            raise AtlasError("invalid_request", "kind='edge' requires edge_id")
        return _edge_delete_impl(edge_id)
    if kind == "doc":
        if not node_id or not path:
            raise AtlasError("invalid_request", "kind='doc' requires node_id and path")
        return _delete_doc_impl(node_id, path)
    if kind == "intent":
        if intent_id is None:
            raise AtlasError("invalid_request", "kind='intent' requires intent_id")
        return _intent_delete_impl(intent_id)
    raise AtlasError("invalid_request", f"kind must be one of node/edge/doc/intent, got {kind!r}")


# --------------------------------------------------------------------------- #
# Registration. Called once from mcp_server.py's bottom line.
# --------------------------------------------------------------------------- #
def register(mcp) -> None:
    @mcp.tool()
    def atlas_node_write(id: str, type: str | None = None, name: str | None = None,
                          summary: str | None = None, abstract: str | None = None,
                          detail: str | None = None, owner: str | None = None,
                          source: str | None = None, attributes: dict | None = None,
                          attributes_mode: str = "merge",
                          key: str | None = _KEY_UNSET) -> dict:
        return envelope(_node_write_impl)(
            id, type, name, summary, abstract, detail, owner, source, attributes,
            attributes_mode, key,
        )

    @mcp.tool()
    def atlas_edge_write(src: str, dst: str, edge_type: str, directed: bool = True) -> dict:
        return envelope(_edge_write_impl)(src, dst, edge_type, directed)

    @mcp.tool()
    def atlas_edge_delete(edge_id: int) -> dict:
        return envelope(_edge_delete_impl)(edge_id)

    @mcp.tool()
    def atlas_doc_write(node_id: str, path: str, content: str | None = None,
                         publish: bool = False) -> dict:
        return envelope(_doc_write_impl)(node_id, path, content, publish)

    @mcp.tool()
    def atlas_intent_write(node_id: str | None = None, intent_id: int | None = None,
                            slug: str | None = None, kind: str | None = None,
                            title: str | None = None, statement: str | None = None,
                            source: str | None = None, position: int | None = None) -> dict:
        return envelope(_intent_write_impl)(
            node_id, intent_id, slug, kind, title, statement, source, position,
        )

    @mcp.tool()
    def atlas_intent_delete(intent_id: int) -> dict:
        return envelope(_intent_delete_impl)(intent_id)

    if _delete_enabled():
        @mcp.tool()
        def atlas_delete(kind: str, node_id: str | None = None, edge_id: int | None = None,
                          path: str | None = None, intent_id: int | None = None,
                          expected_edge_count: int | None = None) -> dict:
            return envelope(_delete_impl)(kind, node_id, edge_id, path, intent_id, expected_edge_count)
