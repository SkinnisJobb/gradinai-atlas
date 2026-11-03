# backend/ — orientation, and where the reasoning actually lives

Loads when Claude reads anything in this directory. The root `AGENTS.md` keeps the rules that must
be known *before* deciding to act; this file is the map to the detail, which in this codebase is
overwhelmingly **in the comments beside the thing it describes**. Read those rather than a summary
of them — they move when the code moves and a summary does not.

The code is split by job: `main.py` holds the FastAPI app and startup, `db.py` the engine and v0
models, `deps.py` the helpers routers share, `routers/<area>.py` the routes. `app.py` is a shim that
keeps `import app` and `uvicorn app:app` working; never edit code there.

| If you are about to | Read first |
|---|---|
| add a mutating route | `auth.WRITE_SCOPE_ROUTES` (unmapped fails closed) and `verify_node_access.py` §F |
| add a mutation to the graph | the header block above `deps.record_graph_event` — the ONE writer |
| change how a status is set | `routers/nodes.set_node_status`, `attention.status_map`, `attention.SETTABLE_STATUSES` |
| touch a workitem's prose | the `models_graph.WorkitemResult` header and `models_graph.reassemble` |
| add or change a column | `main._reconcile_graph_schema` — including its "WHAT THIS CANNOT DO" block |
| add a node type | `OBJECT_TYPES`/`BRIDGE_TYPES` in `node_types.py` **and** in `frontend/state.js` — a type missing from the frontend copy is silently rendered as a workitem |
| write an access check | `deps._visible_node_ids`, and `models_auth.AuthUser.person_user_id` for ADR 0034 |
| add a write route, with project RBAC in mind | `rbac.py`'s header, and `deps._require_project_role` after the route's visibility check (inert unless `ATLAS_RBAC=projects`, ADR 0041) |
| touch identity or tokens | `auth.Principal`, `auth._resolve_token`, `verify_agent_identity.py` |
| add or change a login method (OIDC, break-glass, a provider) | `identity/__init__.py`'s header and `identity/oidc.py`'s check list; the routes stay in `auth.py` §14b (inert unless `ATLAS_AUTH_PROVIDERS` names more than `local`, ADR 0041 §5-§7) |
| add or change a test | `backend/TESTING.md` |

## The two rules a verify script enforces, so you find out either way

- `verify_node_access.py` §F enumerates the live route table (`route_table.py`) and FAILS on any
  `/api` route with no access classification. Add a route, add its entry — that check is the reason
  "somebody forgets" stops being the failure mode.
- `verify_graph_event.py` §B tokenises every backend module and fails on a second `GraphEvent`
  construction site. There is one writer because a check says so, not because a document asked.

## Migrations

`migrate_*.py` are **one-time and dry-run by default** — `--apply` writes, and a destructive one
refuses a non-empty table without `--force`. Each script's own docstring carries what it did, when it
ran and what it measured first; `migrate_drop_status_override.py` is the model for the shape.

They are separate scripts rather than boot-time DDL on purpose: `_reconcile_graph_schema` is
**additive only** (`ADD COLUMN`, `CREATE INDEX IF NOT EXISTS`), and destructive DDL against a
database with no backup is a deliberate act taken with the owner, not something that fires the next time
`atlas-api` restarts.

## Boot is a read

Nothing at startup opens a file. `verify_object_graph.py` §15 proves it rather than asserting it: it
snapshots every node id, status and body plus the whole edge set, runs a second real `startup()`, and
asserts byte-identical.

**Type-registry seeding survived** and moved here, to `backend/node_types.py`
(`ensure_backlog_node_types` / `ensure_world_node_types`), called unconditionally every boot. A fresh
database still gets its full `node_type` registry. It lives there rather than in `seed.py` because
`seed.py` imports `app`, which drags in the fail-closed pepper chain.

## The gate, and the surprises it produces

Relocated out of `AGENTS.md` on 2026-08-28, because these only fire once you are editing this
directory. Every mechanism is commented at length where it runs; read the comment, not this list.

**Three kinds of caller, one gate.** A browser, a machine with a service token and an agent through
the MCP all reach the same FastAPI app and the same ASGI authorization middleware (`backend/auth.py`
— deliberately not `Depends()`, which cannot see a mount), deny-by-default over every route including
404s and anything added later. **That module's header is the design; the table above says which
symbol to read before which change.**

- **A Bearer caller is never redirected** — a 302 would hand a script a login page to parse as data,
  so machine callers get 401 JSON. Bearer is honoured on the public hostname as well as the LAN, and
  a token presented to a host off `auth.BEARER_HOSTS` is treated as a **bad credential**, never as a
  distinct "wrong origin" hint — which is why the LAN-only period read as "the MCP is broken". The
  trade and the alternatives are argued in `deploy/prod.override.yml` beside the value.
- **Two callers get different answers from one database at one instant, and both are correct.** The
  filter is inside the query and rollups take the visible set as an INPUT, never as a post-filter,
  because a post-filter strips rows and still leaks counts. If a board looks emptier than you
  remember, check whose session you are in before looking for a fault.
- **A workitem follows what it WORKS ON.** It is not marked itself and has no `contains` parent, so
  visibility resolves through the effective binding. Found in a browser after the whole suite passed;
  `verify_node_access.py` covers it now.
- **Linking an agent to a person makes an OWNER's agent LESS capable, not more** (ADR 0034). Grants
  inherit person → bot and **ROLE NEVER DOES**, and an owner holds no grants to inherit — which is
  the safety property, and why linking is on by default. Disabling a person disables their agents;
  `user enable` is deliberately not symmetric. An agent with no person inherits nothing and is
  untouched by anybody's disable — `admin bot list` names those.
- **The gateway authorizes per ROUTE, not per caller, and its config is not in this repo.** A tool
  federated there is exposed to every client behind it; the lever is `tools.include` on the client,
  or not federating the tool. **The MCP itself holds no logic and no copy of the graph** — a tool
  that needs something the API cannot answer is a missing endpoint, not a query to write in the MCP.
- **Every write route goes through `auth.guard_write`** — Origin plus a synchronizer token for
  cookie sessions, Bearer principals exempt from CSRF and scope-checked instead. **Do not invent a
  second guard.**
- **An invalid `attributes` payload returns 400 NAMING THE FIELD**, rather than a generic refusal —
  the same shape `_resolve_assignee` uses.

**`auth.PUBLIC_ROUTES` is the entire unauthenticated surface**, pinned exactly by
`verify_object_graph.py` so an addition gets reviewed. **`/` is on that
list but is not one fixed response** — the handler renders `frontend/landing.html` for a stranger and
302s a signed-in browser to `/board`, which is itself gated. The four ORPHANED `frontend/*.html`
files that pair with it are in `frontend/CLAUDE.md`.

**Accounts, credentials, boot secrets.** `docker exec atlas-api python -m admin user add <name>
--role owner|member` mints a person; secrets print once, reset is admin-driven, there is no mail
sender. Machines use `admin token add`, or **`POST /api/me/agent-credential`, which any signed-in
person calls from Settings → Agent credentials** — that one creates the bot account and links it in
the same call, so a contributor never runs `bot add`, and **its four refusals are argued in
`routers/me.create_agent_credential`: read them before routing around one.** **Rotating a credential does
NOT revoke the old token** — revoke-then-mint is an outage. Four env vars are required or
**the app refuses to boot** (`ATLAS_PASSWORD_PEPPER`, `ATLAS_TOKEN_PEPPER`, `ATLAS_LOGIN_ORIGINS`,
`ATLAS_DB_URL`), and **the peppers are FIXED secrets** — regenerating one invalidates every password
and token at once. `atlas-api` has no `env_file:`, so every key needs an explicit `environment:`
entry; check with `docker compose config`, never by reading `.env`.

## Querying the graph without over-counting

- **Rollups must tolerate several parents.** `max` (health, attention) and union (intent) are
  idempotent and fine; `sum` (work-load counts) double-counts and must collect a **set** of reachable
  ids — reasoning in the node-intent header block in `deps.py`; the walk is `routers/nodes._intents_for`.
- **Count workitems with `NOT EXISTS`, never a `LEFT JOIN` against `edge`** — the join multiplies an
  item by its binding edges, which is how an early draft of ADR 0030 over-reported the total. The
  rule stands even though that example no longer reproduces: `serves` is still a live edge type and
  still in the binding filters, so one row of it brings the hazard straight back. For a total, ask
  `GET /api/workitems`.

## Status, prose and provenance

`status_override`, the three-way merge and both boot ingests are deleted; the markdown went with
them, and `git log` still has it. `routers/nodes.set_node_status` now has ONE branch for every node
regardless of origin, and there is no transport left to reintroduce.

- **Effective status is read through `attention.status_map()`, never `attributes["status"]`
  directly.** Two call sites; keep it that way — its docstring says why the shape outlived the
  second source it used to merge.
- **`node.completed_at`** is when an item reached a done status; the column's comment in
  `models_graph.py` carries the three rules. It is what the board's age filter measures, and the
  filter hides nothing without a date: no date means Atlas cannot say when it finished, and hiding
  on a guess loses work.
- **`node.source` and `node.origin` are provenance and nothing branches on either.** Do not assume
  the two tables agree; `SELECT origin, count(*) FROM edge GROUP BY origin` if it matters.

The `WorkitemResult` model header carries the incident that split the table out (three specs
destroyed by the outcomes being recorded against them) and why `position` is a SECTION INDEX;
`models_graph.reassemble` is the inverse.

**Search reads EIGHT sources**: `node.body` (and `name`), the authored `summary`/`abstract`/`detail`
(where world nodes keep their context), `acceptance_criterion.text`, `workitem_result.text`,
`workitem_comment.body`, `node_doc.content` — SERVED documents only, through the same boundary `routers/docs.node_doc`
enforces — `adr_record` (ADR 0045), whose hit is the ADR's project node plus `adr_ids`, and a
workitem's `node.labels` (exact tags, STORY-oss-14). `deps._search_hits` names each and why it was added, and `GET /api/search` returns POINTERS
(id, summary, where it matched, a snippet) — never the prose it matched in. Splitting a store out
without adding it to search removes it from the thing that replaced `grep -r backlog/`, in
silence — which is the whole reason the count is written here.

Verify with `backend/verify_status_lifecycle.py` — it drives the real API through the real gate
against a real Postgres.

## Assignment

`routers/workitems.owner_retired_refusal` argues 400-over-silent-ignore; `/api/node` is untouched.

- **`routers/workitems._resolve_assignee` is where "agents are not assignable" (ADR 0034) is ENFORCED**, with a
  400 naming the field. `GET /api/people` merely declines to *offer* the choice — a picker is not a
  gate, and only one of the two is reachable by a machine caller. It is deliberately not a foreign
  key; the column comment says why, and it is the same reason `AuthUser.person_user_id` is not one.

## `graph_event`, beyond "call the one writer"

The header block above `deps.record_graph_event` also states plainly what the table does NOT
give you: append-only is a CONVENTION with no trigger behind it, nothing prunes it, and `detail` is
not a diff.

- **Record the event BEFORE whichever commit the route uses.** Two commit shapes; one placed after
  the commit its mutation rode on silently rolls back and nothing raises.
- **A grant change is recorded in `auth_event`, not here.** `graph_event` has a read route, so an
  event naming who was granted a private project would be a new disclosure to everyone else granted
  it; the reasoning sits at the `PUT /api/access/node/{id}` handler.
- **An event outlives its node**, deliberately — `_delete_node_dependents` does not clean this
  table. Fail-closed consequence: an event about a deleted node is readable by an owner and nobody
  else, because a deleted id is in no computed visible set.

## Tests and CI

- **A schema change that passes against a FRESH database can still fail on deploy.** Postgres is
  persistent and `create_all` adds no column to a populated table — as true of the disposable test
  container as of live. The additive-only DDL vocabulary and its limits are in
  `main._reconcile_graph_schema`'s "WHAT THIS CANNOT DO" block; §Migrations above says
  when a `migrate_*.py` is the answer instead.
- **A fresh database is not a populated one.** `create_all` cannot add a column to a populated table,
  and that is as true of the disposable container as it was of SQLite. **Verify anything
  schema-shaped on staging before merging** (owner decision, 2026-08-09).
- **Do not set `ATLAS_TEST_DB_URL`** — it is the one setting that lets CI and a workstation exercise
  different code, and it is the configuration two concurrent runs once raced in.
- **CI runs it** — `.github/workflows/verify.yml`, plus `node --check` over every frontend module
  (nothing else catches a syntax error; there is no build step) and an `ASSET_FILES` completeness
  check. **Deliberately not a gate**: a red run is information, and re-enabling the gate is a repo
  ruleset rather than a code change (owner decision, 2026-08-23).

## Reading `/api/health`

`/api/health` is behind the gate: it needs a service token (`Authorization: Bearer`). `/healthz` is
liveness only (no token, no database touch); use it for probes. An install's own commands for
reaching them belong with its deploy documentation, not in this file.

**The Host allow-list matches the PORT too, and a mismatch is a 421 on every route.** With
`https://atlas.example.com:8443` in `ATLAS_LOGIN_ORIGINS`, `-H "Host: atlas.example.com"` returns
**421** and `atlas.example.com:8443` returns 200. The trap is that 421 is returned for a real route
and a nonsense one alike, so **a Host-rejected probe looks exactly like "that route does not
exist"** — which is how a correctly deployed new endpoint can read as missing. Send the Host
exactly as the allow-list spells it, port included.
