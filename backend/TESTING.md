# Testing Atlas

**The suite is whatever `backend/verify_*.py` contains, and none of it reads a file.** Don't take a
number from this page — ask the disk:

```bash
ls backend/verify_*.py | grep -v verify_all
```

`verify_all.py` discovers them from disk, so a new script joins the suite by existing — unless it is
named in the runner's own skip list, which it prints on every run. Prose does not join anything: this
file opened with a hard count for months, and it went stale twice without anyone deciding
anything — the system merely grew a script (`verify_node_intent.py` with ADR 0032 on 2026-08-14,
`verify_assignment.py` with `EPIC-atlas-assignment` on 2026-08-17). A count here is a claim about
the future of a directory, and it is the one kind of claim this suite cannot make for you.

There are no pytest-style tests. Verification is a set of standalone `verify_*.py` scripts you
run and read — each one prints a `[PASS]`/`[FAIL]` line per claim and exits non-zero if
any failed. That convention predates this document and is deliberate: a failing run tells
you *what claim broke*, in a sentence, without opening a stack trace.

Until 2026-08-12 none of it was wired to anything, and it ran exactly when somebody
remembered to type the commands.

---

## Running it

```bash
cd backend
python verify_all.py                  # everything, ~20s, one exit code
python verify_all.py --only object    # just the scripts whose filename matches
python verify_all.py --verbose        # full output instead of failures only
python -m pytest test_verify_all.py   # the same scripts, one pytest test each (pytest installed)
```

`test_verify_all.py` is a thin pytest wrapper (STORY-oss-17): it reuses `verify_all`'s
discovery and per-script runner, owns one disposable Postgres per session the same way, and
reports a failing script's `[FAIL]` lines as the test failure. It adds no checks of its own.

`verify_all.py` discovers the scripts, starts **one disposable Postgres for the run**,
executes them serially, and prints a per-script breakdown with timings. On a failure it
prints that script's `[FAIL]` lines inline, so a red run says what broke without anyone
opening a log.

Individual scripts still run on their own (`python verify_object_graph.py`) and behave
identically — the runner adds isolation and a verdict, not behaviour. The scripts mint
throwaway peppers as plain env vars, which `settings.read_secret` accepts by default; do not run
them with `ATLAS_REQUIRE_SECRET_FILES=1` (STORY-oss-03's opt-in strict mode) set.
`verify_secret_files.py` needs no database and sets/clears that flag itself.

### Requirements

Docker, and a Python environment with the backend's dependencies (`backend/.venv` on the
workstation). Nothing else. No database needs to exist first.

---

## How a test gets a database

`backend/testdb.py`. Three outcomes, and **there is deliberately no fourth**:

1. `ATLAS_TEST_DB_URL` is set → use it. Unreachable is a hard error, never a fallback.
2. Otherwise → start a disposable `postgres:16-alpine` on a free port with a uuid name,
   and remove it on exit (`atexit`, not left to the caller to remember).
3. Neither works → raise `TestDatabaseUnavailable`, which the caller turns into a clear
   message and `sys.exit(1)`.

**A run must never report success while a section was skipped for lack of a database.**
Before TASK-1044 several scripts fell back to a SQLite scratch file and still printed
something that read as green. SQLite is gone from this repo entirely — no scratch file, no
fallback, no `if dialect == "postgresql"` branch.

**Why that mattered more than it sounds** — this is the whole case for the rule above, and it
lives here now rather than in `AGENTS.md`, where it was costing every session a paragraph to
say one thing. Adding the id sequence, the search index and full-text search put FOUR
`if dialect == "postgresql"` branches into production code where `master` had none, every one
of them there purely so the SQLite harness could run. The worst was `allocate_task_id`, whose
SQLite path was `MAX(id)+1`: a read-then-write race — **precisely the bug the sequence exists
to eliminate.** A green run against it proved the opposite of what it appeared to prove. All
four are deleted, and the harness that wanted them is deleted too.

### Isolation, and the race that shaped it

On 2026-08-10, two agents ran scripts concurrently against one shared Postgres container.
One dropped the schema mid-run of the other; both then reported green on a re-run. The
results were trustworthy only because a human re-ran them serially by hand.

So `verify_all.py` starts its own container, and two concurrent runs get two containers.
Setting `ATLAS_TEST_DB_URL` puts you back in the configuration that raced — it is honoured
(it is `testdb`'s documented contract) but warned about, loudly, on every run.

Between scripts, isolation comes for free: the runner passes its DSN down as
`ATLAS_TEST_DB_URL`, each child takes branch 1, and `TestDatabase.start()` resets the
`public` schema on entry while `stop()` resets it again on exit. Every script starts clean
and leaves nothing behind.

---

## Where the test data comes from

`backend/testseed.py` — a curated fixture of ~31 nodes built to contain each case the
rules need, exactly once, with ids that cannot be mistaken for real work (`TASK-90xx`,
`*-seed-*`).

```python
import testseed
seed = testseed.build(session)     # inserts nodes/edges, recomputes derived values
testseed.self_check(session)       # every declared case is really present
```

Use `seed.blocked_leaf`, never `"TASK-9008"`. The point is that an assertion names the
**case**, not a row.

### Why a fixture and not the real backlog

Until 2026-08-12, eleven scripts got a populated database by importing the 309 markdown
files in `C:\Projects\project-ai-dev-seat\backlog`. That was never a decision about test
data — it was the nearest available pile of nodes. It cost three things:

- **A test that names `TASK-825` breaks when somebody closes `TASK-825`**, and then it is
  "stale", and then it gets waved through. TASK-1068 found six such assertions in one
  script. The sixth was not stale at all — it was a real defect (a story marked `done`
  with four children still open) that had been dismissed for weeks because its neighbours
  were noisy. **Pinning production rows makes a failing test ambiguous, and ambiguous
  tests get explained away.**
- **It pinned the suite to one Windows workstation.** The server cannot clone the
  planning repo — its deploy key is scoped to `project-atlas` — so the suite could not run
  where the Docker and the Postgres already are.
- **The corpus was frozen.** Workitems have been Postgres-native since TASK-1060;
  everything from TASK-1063 on was filed through the API and has no file. The markdown
  stopped growing at TASK-1062.

### `self_check()` — the guard on the guard

A curated fixture rots in a way a real corpus does not: someone tightens a rule, the case
that exercised it stops being exercised, and the suite goes quietly green over a hole.

So the fixture **declares** its cases in `CASES` and then proves each one against a fresh
derivation. A fixture that has stopped covering something fails loudly at the fixture,
rather than silently everywhere else.

It earned that on its first run. The draft asserted "everything behind the frontier is
orange"; `self_check` reported the chain's tail carrying no urgency at all. It was right:
urgency means *blocks active-goal work*, so it is earned by what depends on you, not by
position in a chain. The tail blocks nobody. That became two correct cases instead of one
wrong one.

**When you add a case to the graph, add it to `CASES`.** When you delete one, say why.

---

## The scripts

Two classes, and the difference decides everything about how a script is written and how
long it lives.

### Behaviour tests — the product's test suite

These assert rules about a running system. They read no files and would be just as true if
the markdown had never existed.

| script | what it proves | ~time |
|---|---|---|
| `verify_object_graph.py` | schema; the attention engine (state + goal-directed urgency); worst-child rollup with grey excluded; `completion`; `attention_count`; *a closed parent has no open children*; the auth gate; the graph read API; graph write API; boot is a read; *an empty store is a 200, not a 404* | 4s |
| `verify_status_lifecycle.py` | settable statuses incl. `cancelled`; completion dates; the age filter's data; that `status_override` is gone | 4s |
| `verify_backlog_read_api.py` | `GET /api/node/{id}`, listings, paging, prose inclusion, `NodeVisibility` applied inside the query, the GIN index plan, `GET /api/node/{id}/doc` | 2s |
| `verify_workitem_write_api.py` | create/update over HTTP; parent and binding family rules; at-most-one binding, and tolerance of a pre-existing double binding without worsening it | 7s |
| `verify_task_id_allocation.py` | server-side id allocation from a Postgres sequence; no rewind across restarts. STORY-oss-12: per-project `KEY-n` ids from `project_key_counter` — real-thread and concurrent-HTTP creates give KEY-1..n with no duplicate or gap (rollbacks included), the column/table arriving on a populated database leave every id unchanged, unkeyed and unanchored tasks still get `TASK-`, the key's refusals (shape, reserved, taken, non-project, immutable once used, member/editor), moving an item never renumbers it | ~10s |
| `verify_workitem_record.py` | `node.body`, `acceptance_criterion`, the abstract/detail projection, `split_result`/`reassemble` round-trip | 1s |
| `verify_effective_binding.py` | single binding plus inherited effective binding (TASK-1025) | 1s |
| `verify_node_access.py` | per-account grants over the private/shared mark: the resolver (ALL-over-parents, `contains` cycles fail closed), the rollup leak proved by *deletion equivalence* and run for a person and an agent, the routes through the real auth gate (a hidden id 404s identically to one that never existed), the admin API, route coverage that fails when a new route forgets the filter, and the asset `Cache-Control` header | — |
| `verify_node_intent.py` | ADR 0032: inheritance downward with nearest-first slug shadowing, a union across several parents, cycle termination, and no leak along `uses`/`hosted_on`; the write API through the real auth gate incl. the CSRF refusal; `GET /api/intents`; intents cleaned up on node delete | — |
| `verify_agent_identity.py` | ADR 0034: the link rule where it lives, the column arriving on a *populated* pre-shape table, grants inheriting down the person link while role does not, offboarding reaching machines, and self-service credentials | — |
| `verify_assignment.py` | assignment end to end — see below | — |
| `verify_secret_files.py` | STORY-oss-03: `settings.read_secret` — default: a plain env secret works unchanged, `NAME_FILE` is honoured and wins (with a warning) when both are set; opt-in `ATLAS_REQUIRE_SECRET_FILES=1` refuses a plain env secret and requires `NAME_FILE`, trailing newline stripped, empty/missing file fails; then per secret through the real loaders (both peppers, the GitHub secrets, `admin`'s DB URL); and no secret read through `os.environ` in the edited modules. No database | <1s |
| `verify_strict_api.py` | STORY-oss-14: `ATLAS_STRICT_API` through `settings.load_strict_api` (blank or unknown raises, and stops `import app`); unset, a clean write answers with no `warnings` key and a write carrying an ignored field (unknown key, `attributes.depends_on`, an unneeded `approval`) still lands and names it in `warnings`, on POST/PATCH `/api/node` and `/api/workitem`; set, the same requests are a 422 naming the field with nothing written and no task id spent; `priority`/`labels` round-trip, refusals, `?priority=`/`?label=` and the `labels` search source; the two columns arriving on a populated table | — |
| `verify_nul_bytes.py` | LCM patch round 1: `nul_guard.NulGuard` — a NUL byte in a query value, key or path segment is a 400 naming the parameter on `/api/search`, `/api/workitems`, `/api/adrs`, `/api/node/{id}` (Postgres used to 500), `%2500` and clean values still 200, the gate still answers a signed-out caller first, and the guard is installed once, inside `AuthGate` | — |
| `verify_settings.py` | STORY-oss-06: `settings.load` — with no new config every origin, Host map, bearer host, privacy contact and DB URL equals the pre-change parsers' (frozen in the script as an oracle, fed the values `deploy/*.override.yml` sets); `ATLAS_PUBLIC_URL` derives origins and bearer hosts only where their own variable is unset, malformed values fail naming the variable; then through the real gate, one child process per configuration: login and CSRF with only `ATLAS_PUBLIC_URL`, and `ATLAS_INTERNAL_HOSTS=atlas-api` admitting Bearer to `http://atlas-api:8000` while a cookie or anonymous request there gets 421 | — |
| `verify_acceptance_approval.py` | STORY-atlas-acceptance-human-approved: `ATLAS_ACCEPTANCE_AGENT_WRITE` through `settings.load`; with it unset or `off` a machine's acceptance PATCH gets the rail's refusal byte for byte (a frozen oracle), approval or not; under `attested` every missing/invalid approval is that 403 plus a hint, a valid one writes and records approver, quote, agent and old/new, with two perturbations; proposals (any mode, session-only approve/reject through the normal PATCH, supersede recorded); a proposal on a hidden item 404s like an absent one and its events are not countable | — |
| `verify_rbac_off_unchanged.py` | STORY-oss-11 (ADR 0041 §0): with `ATLAS_RBAC` unset, `_visible_node_ids` equals a FROZEN copy of the pre-RBAC resolver for every principal of a today-model fixture that also carries RBAC rows which would change the answer if read; flipping RBAC on moves the answer (perturbation) and off restores it; every RBAC reader poisoned and still green; today's write rules over HTTP; bad settings refuse to boot | — |
| `verify_project_isolation.py` | STORY-oss-11 with RBAC on and private by default: a project is absent for an unbound account on get, structure, doc, search, workitem search, outline, tree, events (not countable either), intents and the ADR registry, which inherit it from the resolver with no RBAC code of their own; RBAC on with shared-by-default gives today's visible set (frozen oracle) and write reach, deletes being the one expected difference | — |
| `verify_project_roles.py` | STORY-oss-11: viewer/editor/maintainer/owner over HTTP, the delete rule (project root needs owner, cross-project edge needs both ends), group bindings, a root grant counting as editor, agents capped by scope and never inheriting role, the group/binding API with its 400s and delegation limits, and `admin.rbac_plan_rows` plus switching off | — |
| `verify_project_root_owner.py` | TASK-1468 (ADR 0051, amends 0041) on the work install's settings (RBAC on, private by default, no implicit instance owner): a root created over `POST /api/node` by an owner's linked agent is 201 then 200 for the agent and its person (was 404), a re-create still 409, a `contains` edge between two such roots succeeds; the binding is the PERSON's `owner`, never the agent's; a member is bound themself and may delete their root, another member still 404s; an unlinked agent is 403 with `reason` and nothing is written; an unbound `POST /api/workitem` is the same rule (the owner's board-filed story is read by the owner and listed for the linked agent by an unfiltered `GET /api/workitems`), a bound one creates no binding; each binding is one `project_role_bound` `auth_event` in the access route's shape and nothing in `graph_event`; with RBAC off in process the creates are today's and no SQL touches `project_role_binding` | — |
| `verify_identity_seam.py` | STORY-oss-13 (ADR 0041 §5-§7) with no new config: the login page equals a frozen copy of the pre-story one, `PUBLIC_ROUTES` and the route table are unchanged, and with `auth_user_idp_state` DROPPED a password login and an agent token still work; `settings.load_identity`'s refusals; an unknown provider stops the boot; `LocalPasswordProvider` agrees with `POST /auth/login`; every local account is `idp='local'` with a subject; `atlas-admin token mint|list|revoke --user` (the raw token only in the `--out` file); `atlas-admin user add --out` (TASK-1470: the temporary password only in a new 0600 file, `-`, an existing file, `--no-password` and an uncreatable path refused with no account made, and the default still printing it); and a fake provider registered in code logs in through the real routes (child process) | — |
| `verify_oidc_login.py` | STORY-oss-13 against a stub IdP (`oidc_testkit.py`, httpx MockTransport, key generated at test time, no network): code + PKCE creates a bounded session and stores no IdP token; each broken proof refused (state missing/unknown/replayed/other browser, nonce, PKCE verifier, aud, iss, azp, exp, signature, alg none/HS256, kid, IdP error, token endpoint down, discovery down); Entra/Keycloak/generic presets incl. overage failing closed; JIT only in a required group; break-glass; agent tokens suspended after `ATLAS_OIDC_REVALIDATE_DAYS`; `user link-idp` keeping the account and its agents | — |
| `verify_idp_access_token.py` | STORY-oss-25 (ADR 0041 §9) against the same stub IdP, RBAC on: `settings.load_oauth`'s rules (and atlas-mcp's canonical form agreeing); an access token for `ATLAS_OAUTH_RESOURCE` acts as an auto-created agent of (person, OAuth client), linked to the person, its write in `graph_event` as both; each broken token a 401 with `login_fail oauth:<why>` (aud, iss, exp, nbf, alg none, HS256, other key, unknown kid, tampered payload, no exp, ID token, no client id, unknown person with no account created, disabled person, stale SSO login, disabled agent); mapped scope cap ∩ project role (a viewer cannot write, a member never holds admin); `atlas_svc_` unchanged and feature-off today's 401; JWKS rotation | — |
| `verify_group_sync.py` | STORY-oss-13: the group-map allowlist file and its refusals; at each OIDC login the `source='idp'` memberships are replaced from the claim (allowlisted only), local memberships of the same groups survive, a refused login changes nothing, one user's sync never touches another's | — |
| `verify_audit_export.py` | STORY-oss-16: `ATLAS_AUDIT_READS`/`ATLAS_AUDIT_RETENTION_DAYS` through `settings.load_audit` (blank, unknown or out of range raises, and stops `import app`); `audit_read` made by today's `models_auth.create_all` on a populated database; default off, agent reads of a node, a doc and a search write no row and answer byte for byte as with it on; on, each records person, agent, token id, the declared tool and request id, the route and the node ids served (never the query or content), and a session read, a 404 and a malformed label as documented; `GET /api/audit/export` as JSON Lines in time order with fixed keys, its filters and 400s, 403 for members, non-admin tokens and a member's admin token, 200 for an owner's admin token, no secret in it; `atlas-admin audit purge` deleting only old `audit_read` rows. Its MCP half, the same rows written by real tool calls, is `mcp/verify_audit_reads_api.py` | — |
| `verify_feature_flags.py` | STORY-oss-08: `ATLAS_DISABLED_FEATURES` through `settings.load_features` (unset/empty = everything on, an unknown name raises naming it); the route table per configuration, one child process each — unset and empty are `routes.snapshot.txt` line for line, `github_link` off is the snapshot minus exactly the six GitHub routes, an unknown name stops the import; then every feature off through the real gate: each GitHub route 404s exactly like a path that never existed, and `/api/health` answers without `deploy`. CI runs the whole suite a second time with every feature off; the scripts whose subject is one of those features (`verify_github_link`, `verify_routes`, `verify_object_graph`, `verify_node_access`) pin the default themselves | — |
| `verify_landing_v2.py` | TASK-1463 (ADR 0035 as amended by ADR 0049), no database: `ATLAS_LANDING` unset or `v1` serves `frontend/landing.html` at `/` byte for byte with exactly today's header set (no CSP), `PUBLIC_ROUTES` the default set and no `/site/` route; `v2` serves `frontend/landing-v2/index.html` with the rule-2 CSP, nosniff and `Referrer-Policy`, `PUBLIC_ROUTES` gains exactly `landing_site.public_paths()`, each listed file 200 (or 404 until drawn), anything else under `/site/` gated; the page, its CSS, its JS and every drawn diagram reference nothing off the origin and carry no inline script, style or handler; every heading of `docs/site/content.md` §2-§18, the tagline, the FAQ and the explainers are in the static HTML; a bad value refuses to boot | — |
| `verify_system_node.py` | STORY-atlas-system-node-handbook (ADR 0047): the `atlas-system` set in `system_handbook.py` (root first, one child per part, five handbook areas on the root, no id meeting a homelab node, generic text, skeleton marker); `tools/docs_site.py --check` green on the tree and red after one changed document; the scrub scanner in enforce mode on the module and `frontend/docs/`; a fresh `ATLAS_SEED=none`/`demo` boot seeds every node, `contains` edge, `docs[]` entry and `node_doc` row (no `graph_event`, root marked `shared`) and the read route serves them, also under the OSS RBAC settings; a second boot, a second `seed()` and a second CLI `--apply` write nothing; a homelab-shaped database is byte-identical after every profile's boot, and the opt-in CLI adds only `atlas-system*` rows; `/docs` gated by default, and with `ATLAS_PUBLIC_DOCS=1` exactly the generated files public (CSP, `private, no-cache`, bytes equal to disk), any other `/docs/` path gated, a bad Bearer still 401. `verify_fresh_install.py` counts the set out of its "zero nodes" and "nothing else" criteria | — |
| `verify_baseload.py` | STORY-oss-23 (check C3 of `agent/plans/oss-baseload.md`): the generated `baseload_data.py` on its own (ids under `atlas-system`, parents first, a summary on every node, no dangling edge or intent, only `requires` beside `contains`) and against the code it describes: every router module of the live route table with every route switch on, every `frontend/v2/views/` entry and panel tab, every OSS compose service; no forbidden pattern (C5). A fresh `none`, `demo` and `self` boot each seeds the handbook set plus exactly the baseload (nodes, edges, `node_doc` rows listed in `docs[]`, the shipped intents, no `graph_event`), with no undeclared edge pair and no name-register report for the set (D-B2: `name_register_warnings` skips generated nodes; a rolled-back perturbation proves an unmarked or authored lowercase name is still reported), and a second boot writes nothing; `homelab` seeds none of it and a populated database is byte-identical under every profile; an install with only the seven handbook nodes gets the rest from `system_node.py --apply`, nothing that existed changes, and a person's own node holding a baseload id is never wired in. Freshness (C1, `tools/gen_baseload.py --check`) runs in `scrub.yml`; a tool without a node is `mcp/verify_baseload_tools.py` (C2) | — |
| `verify_self_seed.py` | TASK-1461: the `self` seed profile (`self_seed.py`, data generated into `self_seed_data.py` by `tools/export_self_seed.py`); the exporter's `check` green on the committed profile, no workitem type, the `atlas` project root, no id meeting the `atlas-system` set; a fresh `ATLAS_SEED=self` boot seeds exactly the profile's nodes, edges, `docs[]` entries, `node_doc` rows and included intents plus the handbook set, with no workitem, `graph_event`, account, grant or role binding, root marked `shared`; a second boot writes nothing; a first owner reads a seeded node, document and intents and files an epic bound to a seeded node; a populated database is byte-identical after a `self` boot, and the default profile seeds none of it. `verify_fresh_install.py` §D boots `self` on its populated database too | — |
| `verify_guardrails.py` | STORY-atlas-guardrails-per-env: the matrix format in `guardrails.py` (the shipped example under `data/guardrails/`, each refusal named, every problem reported at once, an install's own environments and a declared custom action accepted with no code change, `matrix_from_answers` rebuilding the example); the Claude Code export denying the prod deploy command and pushes to protected branches (and allowing a command once the matrix allows it, the perturbation), the branch-protection export requiring a review on prod's branch; `atlas-admin guardrails apply` on a fresh `ATLAS_SEED=none` install writing the document, its `docs[]` entry, one `guardrail-<env>` constraint per environment and one Proposed `source=atlas` ADR, each as a `graph_event` naming the operator, read back through `GET /api/node/atlas-system` (own) and a part (inherited); a second apply writing nothing; a changed file updating, deleting a removed environment's intent and superseding the ADR; a refusal without `atlas-system`; and a homelab-shaped database byte-identical when no matrix is applied, with nothing ever written on its `atlas` node | — |
| `verify_cicd.py` | STORY-atlas-first-cicd: `cicd.generate` from the shipped example matrix and `data/cicd/example-answers.yaml` gives the documented files for GitHub Actions, GitLab CI and Azure Pipelines, one job per check (a non-gate may fail), one deploy stage per environment whose matrix entry names a deploy and none for another (perturbed: stst loses its stage), every production trigger; prod's deploy waits for a person on each platform (GitHub `environment: prod` plus required reviewers in the settings, GitLab `when: manual` + `allow_failure: false` + protected environment, Azure a `deployment` job on environment prod), the credential only in environment-bound deploy jobs, no deploy for a pull request; perturbations: a matrix with an agent prod deploy refused by `guardrails.validate` and by `generate`, a template without its gate refused, edited output without the gate failing `check_pipeline`; the generator and the CLI verb import nothing that reaches a network, a process or git, a file-based run needs no database and writes exactly the files under `--out` (refuses to overwrite without `--force`); `guide_answers` reads only questions of the shipped set and keeps prod's deploy with a person; on `ATLAS_SEED=none`, answers through `POST /api/setup/answers`, `--from-guide` reads only, `--record-adr` writes one Proposed `source=atlas` ADR per platform with one `node.adr.write`, idempotent, a changed answer superseding that platform's only; no route, and a homelab-shaped database byte-identical. Platform lint is `.github/workflows/cicd-lint.yml` | — |
| `verify_crunch_backup.py` | STORY-atlas-crunch-mode, the backups (`crunch_backup.py`, `atlas-admin backup run`): backend/Dockerfile's `backup` stage carries the Postgres client and the last stage is still bare `FROM base`; the OSS compose `atlas-backup` service is profile `backup` only, on `data` only, publishes nothing, reads the DB URL from its secret file, and no homelab compose file names it; on a homelab-shaped database a manual backup writes the dump, its `sha256sum -c` file and a manifest whose node/edge/graph_event counts equal the source's at the dump's snapshot, restored into a temporary database in the same Postgres and counted equal (a count perturbed by one is a RESTORE MISMATCH), reported through `crunch.record_backup` (one `crunch_history` backup row and one `node.crunch.backup` event on `atlas-system`, labelled with the file name) and returned by `crunch.last_backup()`, no temporary database left; a flipped byte fails the checksum and a truncated dump with a matching checksum fails the restore (both exit 1 naming why), a missing checksum and an empty directory fail too, and a backup whose restore fails is deleted and recorded as failed; retention deletes only older files of the module's pattern, nothing when unset and nothing after a failure; crunch off or a policy not the caller's runs nothing (no file, no history row, no event, every row and sequence byte-identical, `crunch.current()` None), while hourly/once under a contract-shaped crunch back up when due, once only once, with a back-off after a failure; the DB URL given only as `ATLAS_DB_URL_FILE` with a random-password role, which appears in no output, file, row or event. The client tools run from the test Postgres image through streaming `docker run` wrappers (`--pg-bin`), since the runner may have no pg_dump | — |
| `verify_cert_expiry.py` | TASK-1419 (OSS-26 F): `ATLAS_CERT_EXPIRY_FILE`/`ATLAS_CERT_EXPIRY_WARN_DAYS` through `settings.load_cert_expiry` (unset = no file and 30 days; blank, non-number or out of 1..3650 raises naming it); `cert_expiry.warnings` over certificates made at test time: far from expiry none, inside the window `expires_in_days=N`, past `expired`, a chain judged by its leaf, an unreadable or non-certificate file a warning and never an exception, the window following the setting (a widened window warns, as the perturbation); through the real gate, `/api/health` with no certificate has no `cert_warnings` field, with one far from expiry `[]` and exactly one field more, near expiry the warning | — |
| `verify_crunch_release.py` | STORY-atlas-crunch-mode, the release path (no database): `guardrails.crunch_overlay` removes exactly `skip_envs` from the deploy plan, every other environment byte-identical (production included), a skipped one keeping its branches, protection and commands and losing only its deploy actions; a `skip_envs` naming production, an unknown environment or an unknown key refused; an agent `deploy` / `deploy_via_pipeline` / `approve_deploy` in production refused before the overlay, by it, and in an overlaid matrix edited to add one; the Claude Code export still denying the prod deploy command and adding no allow, branch protection on prod's branch unchanged; the crunch variant of `cicd generate` passing `check_pipeline` and `check_crunch` on all three platforms and every trigger (no skipped-env job, prod depending on the checks alone, prod's person gate kept); `backup.policy: per_deploy` putting `atlas-admin backup run --reason per_deploy` before prod's deploy step, with perturbations (removed, moved after, failure swallowed, a later conditional step, a template without the placeholder) each refused, and no step for any other policy; crunch off byte-identical to the pre-crunch templates for 24 platform x trigger x promotion combinations and through the CLI (`--crunch-from-db` with `crunch.current` stubbed off and on, the stub's state built by `crunch._state` so it has the real shape, `revision` included); a crunch config with `duration_hours`, and the real state, accepted by the overlay and through `--crunch FILE`, while a misspelt `skip_env` is still refused, alone or beside the real state (TASK-1432); `--record-adr` with a crunch flag refused before a file is written. Platform lint of the variant is `tools/ci/cicd_lint.sh` | — |
| `verify_config_panel.py` | STORY-atlas-config-panel, phase 1: `settings_catalog.py` describes every `ATLAS_*` name written in any API module, `retrieval/` included (red on a perturbed source with one more), and every name read through `read_secret`, each on a real `atlas-system*` part; the retrieval values are `retrieval.load_config`'s own, with its verdict, and a refused switch shows no value; `GET /api/config` through the real gate with every secret in a file (strict mode) and with plain env secrets: each value and source, each secret only its state, no secret value or length in the config, health or me bodies; owner session and an owner's admin token 200, anonymous 401, member, member admin token and owner read token 403; one GET and no write route under `/api/config`; a homelab-shaped database byte-identical after an owner reads it | — |
| `verify_config_writes.py` | STORY-atlas-config-panel, phase 2 (`ATLAS_CONFIG_WRITES`, default off): the changeable set is exactly `ATLAS_STRICT_API` and `ATLAS_RETRIEVAL_TOP_K`, and their real readers re-read the environment per use; every bootstrap setting, secret and deliberately excluded setting (RBAC, identity, OAuth, feature flags, ...) refused by name; flag off on a homelab-shaped database, a PUT on the setting route by an owner with CSRF and password (or an owner's admin token) answers byte for byte like a path that never existed, no `config_value` table, GET is phase 1's body, every table byte-identical; flag on, each refusal its named error with nothing written (member, no password, wrong password as a `config_write:` `login_fail`, no CSRF, an owner's admin token, a read token, a member's admin token, unknown, secret, the four bootstrap settings, excluded, bad values), then an owner with the password changes strict mode (source `board`, a workitem POST with an unknown field goes from accepted to 422 at once, one `node.config.set` on `atlas-system-api`), top-k set and reset with null; a second boot applies the board value and is a read; the environment wins (409 `environment_wins`); the flag off again ignores the stored row; and `verify_node_access.py` and `verify_object_graph.py` pass with the flag on | — |
| `verify_settings_blame.py` | TASK-1439: a loader's error names its variable where it is raised (`variable` on `settings.SettingsError`/`SecretError`, `identity.groups.load_group_map`, carried through `auth.MissingSecretError`), messages unchanged; in a child process whose environment is the configuration, `settings_catalog.describe()` puts `error` only on ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS when it is out of range and only on ATLAS_OIDC_GROUP_MAP_FILE when the group map is missing, not YAML or the wrong shape (checked by the catalogue itself when oidc is a provider, as at boot), the section's other settings carrying `blocked_by`; `import auth` with a bad value stops with a `MissingSecretError` naming it; valid configurations (default, OIDC + RBAC with a group map, retrieval on) equal a FROZEN copy of the pre-change describe() byte for byte, which differs on the broken one (perturbation); an error naming no variable is still on every setting of its loader. No database | <1s |
| `verify_config_catalogue.py` | TASK-1433: the settings catalogue covers every service, derived both ways from the files: every ATLAS_* name in `mcp/app/*.py` is described and marked atlas-mcp, every `${X}` in `deploy/oss/*.yml` and every key `.env.example` or an example profile sets is described (compose-only ones marked compose, with what compose does with them), every API name is in `CATALOG` marked atlas-api, shared ones marked both; a new mcp name or interpolation is reported (perturbations); the other services' entries live in `data/config/other-services.json`, read on first use; the panel (`describe()`) is still exactly `CATALOG` and `change_refusal` calls another service's variable unknown; `describe(M)` in a process whose `os.environ` differs equals `describe()` where `os.environ` is M, the five once read from module attributes, the deploy paths and top-k showing M's values; with no pepper it has no error and imports none of auth, passwords, deps, github_link, identity, db; each value equals what the app's own module computes (valid, malformed and empty mappings); `gen_config_ref.read_by` names the reader and stops on an unknown name, CONFIG.md is its output, and `check_examples.known_key` has no fallback. No database | ~5s |
| `verify_crunch.py` | STORY-atlas-crunch-mode, the backend: `crunch.validate_config` normalises a good config and refuses each bad one naming the field (unknown key, ends_at missing/naive/before start/past/too long, start too far ahead, backup policy and retention, skip_envs with no matrix, not in the matrix, a production environment, a duplicate); the five `node.crunch.*` actions, both write routes `admin`, nothing under mcp/ naming /api/crunch; NEVER ACTIVATED on a homelab-shaped database, the four routes answer every caller kind by name (owner and member session, an owner's and a member's admin token, a read token, anonymous; an owner's PUT is 409 `system_node_missing`), no crunch table appears and every table is byte-identical; on an `ATLAS_SEED=none` install with the example matrix, every refusal by name with nothing written (no CSRF, no or wrong password as a `crunch:` `login_fail`, tokens, each bad config), activate, extend, change and end each one `node.crunch.<act>` by the owner's session plus one Accepted ADR on `atlas-system`, the same config again nothing, the banner exactly `{active, ends_at}`; the server's clock decides the start (no starts_at is in force at once, its start equal to `activated_at`; a past or far-future starts_at is 400; a near-future one is scheduled, off until then, never moved earlier, and can be ended); `duration_hours` instead of `ends_at` (exactly one, 1..744) ends that long after the server-set start, extends a set window from its stored end, and never reaches the state; while it is on, `guardrails show|export` and `cicd generate --crunch-from-db` run against the real `crunch.current()` and succeed, its keys all in `guardrails.CRUNCH_KEYS` (TASK-1432); expiry (clock moved past ends_at) records one `node.crunch.end` reason `expired` as `system` and one ADR however many reads follow; no password, pepper or token in any event, history row, ADR or response | — |
| `verify_setup_guide.py` | STORY-atlas-setup-guide-adrs: the shipped question set loads at version 1 and its answer keys equal a pinned contract (a perturbed type or template is refused, naming the place); a homelab-shaped database that never runs the guide is byte-identical, with every existing route answering as before, across reads of `GET /api/setup` and a restart; on an `ATLAS_SEED=none` install the guide walks all six topics (one session, a new client and a restarted server resuming, an owner's admin agent answering one), each answer one Proposed `source=atlas` ADR `SYS00n` in `atlas-system` with the answer as its content and one `node.adr.write` by the caller; members, a member's admin token, an owner's token without `admin` and anonymous refused, and every invalid answer a 400 naming the field with nothing written; accept by an owner session only (tokens, members and the generic `PUT /api/adrs/{id}` refused), a changed answer a new ADR superseding the old (old row byte-identical), the same answer nothing; `ATLAS_SETUP_QUESTIONS_FILE` adds a topic, and each broken file is a 503 naming it and the problem; with `ATLAS_RBAC=projects` and no role binding, the owner's session and admin agent record and accept on `atlas-system` (the guide's one named exemption) while another project through the guide, `PUT /api/adrs/new` direct and a member are still refused, and RBAC off is unchanged. The MCP half is `mcp/verify_setup_guide_tool.py` | — |
| `verify_graph_event.py` | TASK-1045's audit log: the table carrying no FK and no CHECK (both absences asserted, because `_reconcile_graph_schema` could never emit either on live); *exactly one writer*, proved by tokenising the backend rather than trusting convention; an event written on every mutation that changes meaning; the actor taken off the principal and unforgeable from a request body, with the accountable person resolved through ADR 0034's person → bot link; and the access filter proved by attempting the violation — see below | — |

Times are from a 2026-08-12 run and are indicative only; `verify_all.py` prints real per-script
timings on every run, which is the number to read.

**`verify_object_graph.py`'s last section is destructive, and that is why it is last.** TASK-1129's
check needs a store with NO nodes in it — a state the fixture cannot hold at the same time as its
cases — so it reseeds the graph out from under everything above it, through `main.startup()`'s own
`ATLAS_RESEED` path rather than a truncate helper written for the test (that path is deliberately
asymmetric: it wipes the GRAPH and never the ACCOUNTS, so section 12's service token still
authenticates). **Anything added after that call runs against an empty database.** What it proves is
that two routes over ONE derivation agree: `/api/graph` and `/api/graph/tree` both define a root as
"a node with no incoming `contains`", and `/api/graph` used to 404 where the other returned an empty
list — which is what made a brand-new board say "Could not load the backlog graph."

One assertion inside `verify_node_access.py` is worth naming outside its row, because it is about a
header rather than the graph: **asset responses must carry `Cache-Control: private, no-cache`**, and
the check reads it off the RESPONSE, not off the constant. `private` because a shared cache must not
store an authenticated asset — Cloudflare cached `/assets/board.css` and served it `200` to a caller
with no session. `no-cache` because `/assets` URLs carry no cache-busting, so a stored copy is how an
already-open tab keeps running last week's code after a deploy. Asserting the constant alone would
have stayed green while the header was never sent, which is the whole difference between the two.

### `verify_assignment.py` — and the cap that was never hit

Seven sections, and the last one exists because the first six were not enough.

- **A. The column.** `node.assignee_user_id` exists, is nullable, and carries **no foreign key** —
  asserted ABSENT on purpose. `_reconcile_graph_schema`'s entire DDL vocabulary is `ALTER TABLE …
  ADD COLUMN <type>`, with no `REFERENCES`, so a declared FK would exist on every fresh test
  database and never on live. The suite would then be proving a constraint the deployment lacks.
- **B. The write path.** Setting an assignee, clearing it with an explicit `null`, and OMITTING the
  field: three requests, three different outcomes. The omit/null pair is the one that silently
  destroys data when it is got wrong.
- **C. Refusals.** An unknown id, an **agent** (ADR 0034 — agents are not assignable) and a disabled
  account each get a `400` naming the field.
- **D. Serialisation.** `assignee` comes back RESOLVED (`{id, username, name}`), never a bare uuid,
  from every route that projects a node. This is what catches a new `_graph_node_json` caller that
  forgot to pass the `assignees` map — a failure nothing else reports, because an assigned node
  simply renders as unassigned.
- **E. The filter and the roster.** `?assignee=me|none|<id>` composes with the other filters, and
  `GET /api/people` is people-only and active-only.
- **F. Assignment is not a grant.** The load-bearing one. A workitem bound to a private project and
  assigned to an account holding no grant for it is absent from every route for that account —
  including its own `?assignee=me` — and its `404` is indistinguishable from an id that never
  existed. Proved by breaking it, not by reading the query.
- **G. Paging honesty, and `?include_ancestors=1`.** A–F all ran against a handful of fixture rows,
  where the server's `page_size` cap is never reached — so nothing caught the Mine view asking for
  500, getting 200, and rendering them labelled "200 items": a truncated list presented as complete.
  G builds a corpus larger than the cap on purpose. **A cap that is never hit is a cap that is never
  tested**, and that is the transferable half of this section; the assertions are the cheap half.

### `verify_graph_event.py` — and the two absences it asserts

Three of its sections are worth reading before adding a check to this file.

- **B. Exactly one writer.** `deps.record_graph_event` must be the only place in the backend that
  constructs a `GraphEvent`, and that is proved by **tokenising every backend module** and counting
  the construction sites, not by convention and not by a `grep`. The first draft did grep, and turned
  red on the writer's own comment (*"DO NOT INLINE A `s.add(GraphEvent(...))`"*) — a check a comment
  can break is a check people learn to edit around. An audit log assembled from a dozen inline
  inserts is one where half the call sites drift and nobody can say which half by reading it.
- **E. Neither readable NOR countable.** The access section does not assert that a member's page
  omits the private rows — a post-filter would pass that. It measures the member's `total`, has the
  owner then make several changes that are entirely about a private project, and requires the
  member's `total` **not to move** while the owner's rises. A count of changes to a private project
  is information about a private project.
- **G. The routes that could change meaning without leaving a trace.** Section C drives the routes
  wired when the table was built; G drives the holes found afterwards, and its shape is the point.
  `NodePatch.attributes` accepts `{"status": "done"}`, so a status could move through the GENERIC
  node route (and through `PATCH /api/workitem`, which carries a dict too) leaving only a
  `node.update` saying `fields: ["attributes"]` — the log's most-asked question, *who set this to
  done*, had a hole straight through it. Node documents and node intents were recorded nowhere at
  all. **Every newly-wired route is proved twice**: the event is written, and it is access-filtered
  by §E's method — generate the events on a project a member cannot see, and require that member's
  `total` not to move. It also asserts the NEGATIVE case in both directions: a PATCH that carries
  the status it already has writes nothing, and `assignee` is absent from `NodeWrite`/`NodePatch`,
  so adding that field without wiring the event turns the check red rather than opening the hole
  again quietly.

Two consequences the suite pins deliberately, so neither is rediscovered as a bug:

- an event **outlives its node** — `_delete_node_dependents` must never clean this table, or the
  delete is the one act nobody can read;
- and therefore an event about a **deleted** node is visible to an owner (unrestricted) and to
  nobody else, because a deleted id is in no computed visible set. Fail-closed and intended.

### The seven that are gone

Deleted by TASK-1059 on 2026-08-12, together with the files they read:
`verify_arrival.py` (the gate on the deletion), `verify_arrival_selftest.py`,
`verify_workitem_import.py`, `verify_world_import.py`, `verify_result_migration.py`,
`verify_result_split.py` and `verify_node_docs.py`.

Their subject was the one-time move from markdown into Postgres, so after the move there
was nothing left for them to assert. `verify_arrival.py` is the neat case: it authorised
the deletion and was deleted by it.

Two of them carried something durable, salvaged beforehand rather than lost with them:

- the `GET /api/node/{id}/doc` checks moved into `verify_backlog_read_api.py` section I —
  now joined by section J, covering the TASK-1076 write path;
- `split_result` / `reassemble` moved into `verify_workitem_record.py` section R. That
  pair is production code, not a test helper: TASK-1067's write gate uses it on every
  `PATCH` to detect whether a body edit also touched the result section.

## Writing a new check

- **Derive, don't pin.** `EXPECTED_TOTAL_FILES = 40` and `total_acc == 1493` both went red
  for changes that were correct — adding a documentation file, adding an acceptance
  criterion. Assert the *relationship* (every file read is inserted; a restart loses
  nothing), and print the number so a surprising jump is still visible to a human.
- **Name the case, not the row.** `seed.blocked_leaf`, not `"TASK-9008"`.
- **A check that cannot fail proves nothing.** Where a round-trip or a comparison is the
  claim, perturb one character and require the comparison to notice. `verify_workitem_record`
  and `verify_result_split` both do this explicitly.
- **Never skip silently.** No database is a hard failure. Zero scripts discovered is a
  hard failure. If a run bounds its own coverage, it must print what it dropped —
  `verify_all.py` prints its exclusions on every run for this reason.
- **Scope, don't weaken.** "Every node has a `source` resolving to a real file" stopped
  being true of the graph when Postgres became authoritative. It was narrowed to
  *git-origin* rows, and paired with its converse — no atlas-origin row may claim a file
  it does not have — rather than deleted.

---

## Known state

**Green, around twenty seconds, and no file dependencies.** How many scripts that was is
`verify_all.py`'s to print at the end of a run, not this page's to promise ahead of one.

Two scripts were red until 2026-08-12 — `verify_node_docs.py` and
`verify_arrival_selftest.py` — for one shared reason: their fixture world came from
`world_manifest.yaml`, which predated ADR 0030's renames, while the `docs/nodes/` folders
had been renamed onto the current ids by TASK-1074. Nothing was actually wrong; the
arrival gate against the real database reported the docs clean, 41 files and 41 rows. Both
scripts, and the manifest, were deleted by TASK-1059, so the fix was the deletion.

## Where it runs

Today: on the workstation, by hand. **TASK-1070 is the open decision** — GitHub Actions,
the server, or a pre-push hook — and it should be recorded with its reasoning, in
`AGENTS.md` and in an ADR if the answer is server-side.

The constraint that used to force the answer has gone: **no script needs the planning repo
or production data any more** — the ones that did were deleted by TASK-1059 along with the
files they read. Whatever runs the suite needs Docker and this repo, and nothing else.

Two things worth folding into that decision:

- A failure must be visible without reading logs.
- It should **not** be a deploy gate unless that is chosen explicitly. Blocking a deploy
  on a suite nobody has been running is a change in working agreement, not a wiring
  detail.
