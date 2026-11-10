"""The `self` seed profile's data: Atlas's own world model (TASK-1461). GENERATED.

Do not edit by hand. Regenerate with `tools/export_self_seed.py` (its docstring has the steps)
and commit the diff; `backend/self_seed.py` loads it into a fresh graph under ATLAS_SEED=self.
Python data rather than a file read at boot: boot opens no file (backend/CLAUDE.md).
"""

PROFILE = {
    "root": "atlas",
    "nodes": [
        {
            "id": "atlas",
            "type": "project",
            "name": "Atlas",
            "summary": "A self-hostable work and context layer for people and their agents: one node/edge graph in Postgres, shown on a board as a world graph, a work board and a node panel, and reached by API and MCP.",
            "abstract": (
                "**Atlas is one graph, shown three ways.** A single node/edge store in Postgres renders as the **world graph** — what exists and how it relates — the **work board**, the backlog scoped by it, and **Mine**, the work assigned to one account. The two trees live in the same tables and differ only by the family of\n"
                "their node type, which is why a workitem can bind to a world node and why work rolls up while intent\n"
                "rolls down. Mine is a list rather than a third tree, and any signed-in account may open any other\n"
                "account's list.\n"
                "\n"
                "**There are no files behind any view.** The markdown backlog and the world manifest were deleted in\n"
                "August 2026, and boot is a read: nothing at startup opens a file or reconciles a row. That is what lets\n"
                "Atlas own its own data — a board edit survives a restart instead of being overwritten by whatever a\n"
                "file said.\n"
                "\n"
                "**Three kinds of caller reach it**, and all three arrive at the same FastAPI app behind the same\n"
                "deny-by-default gate: a browser holding a session cookie, a machine holding a scoped service token, and\n"
                "— since ADR 0033 — an agent through the `atlas-mcp` tool surface.\n"
                "\n"
                "**Read the Docs tab for how it is actually built.** The *Runtime architecture* document has the diagram\n"
                "of how requests travel, what each component is, which credential each hop needs, and where every piece\n"
                "of code and configuration lives. This node's children are the layers — backend, frontend, ingress, ops,\n"
                "security and the MCP — and each carries its own document about itself."
            ),
            "detail": (
                "**Start with `Runtime architecture` on the Docs tab of this node.** It is the whole-system\n"
                "picture: the request paths as a diagram, the parts and how each is reached, the authorization model per\n"
                "caller kind, where state lives and what deliberately has none, and a map of the repo and its\n"
                "configuration.\n"
                "\n"
                "Decisions live in `docs/adr/` in the `project-atlas` repo — one repo per world root, per ADR 0031.\n"
                "\n"
                "**The open-source repository.** Atlas is published on GitHub as `gradinai-atlas` (Apache-2.0,\n"
                "copyright GradinAI). It is the **upstream**: the original that releases are cut from and that\n"
                "contributions go to. It was created once by `tools/carve/export.sh` as a single scrubbed commit, with no\n"
                "history from `project-atlas`. `project-atlas` is the owner's own downstream installation — the homelab\n"
                "deployment with its private history and files — and takes new versions from the public repository like\n"
                "any other install. The carve-out is STORY-oss-23-carve-out."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas/architecture.md",
                    "kind": "architecture",
                    "title": "Runtime architecture",
                    "format": "mermaid",
                    "content": (
                        "# atlas — architecture\n"
                        "\n"
                        "What Atlas is made of, and how the parts actually talk to each other. This is the runtime picture;\n"
                        "`contains` in the world tree is the **product** decomposition (ADR 0030) and deliberately does not\n"
                        "describe deployment.\n"
                        "\n"
                        "## What it is\n"
                        "\n"
                        "One node/edge store in Postgres, rendered by one board at `/board` (ADR 0043): a radial **graph**\n"
                        "of the world (what exists), a **work board** of the backlog scoped by what the graph selects, a\n"
                        "**node panel** for the node in focus, and a **dashboard** pane beside them, hidden until it is\n"
                        "switched on. World and work live in the same tables and differ only by the `family` of their node\n"
                        "type. The previous three-view board (world tree, work tree, Mine) is frozen at `/board/legacy`.\n"
                        "There are no files behind any of it — the markdown backlog and the world manifest were deleted in\n"
                        "August 2026, and **boot is a read**: nothing at startup opens a file or reconciles a row.\n"
                        "\n"
                        "Three things reach it: a **browser**, a **machine** holding a service token, and — since ADR 0033 —\n"
                        "an **agent** through the MCP tool surface. All three arrive at the same FastAPI app and the same\n"
                        "authorization gate.\n"
                        "\n"
                        "## The parts\n"
                        "\n"
                        "## How a request actually travels\n"
                        "\n"
                        "    subgraph plane[\"MCP plane — separate compose projects\"]\n"
                        "        GW[\"agentgateway :3000<br/>per-tool allow-list\"]\n"
                        "        MCP[\"atlas-mcp :8092<br/>thin facade, no logic\"]\n"
                        "    end\n"
                        "\n"
                        "    subgraph atlasnet[\"project-atlas_default — the ONE shared network\"]\n"
                        "        CAD[\"caddy-lan :8443<br/>TLS terminates here\"]\n"
                        "        CF[\"cloudflared<br/>outbound tunnel\"]\n"
                        "        API[\"atlas-api :8000<br/>ASGI gate, deny by default\"]\n"
                        "        PG[(\"atlas-postgres\")]\n"
                        "    end\n"
                        "\n"
                        "    GH[\"GitHub<br/>OAuth app + repo collaborators API\"]\n"
                        "\n"
                        "The loop in that diagram is real and is the thing people trip over: in the homelab, **`atlas-mcp`\n"
                        "leaves its container and comes back in through the LAN TLS front door, `caddy-lan`**, rather than\n"
                        "talking to `atlas-api` directly. The OSS install has no such loop: atlas-mcp calls\n"
                        "`https://atlas-api:9443`, a front of `atlas-tls` that is published nowhere.\n"
                        "\n"
                        "## Why the MCP takes the long way round\n"
                        "\n"
                        "In the homelab every service is its **own compose project on its own network**. Only `atlas-api`,\n"
                        "`atlas-postgres`, `caddy-lan` and `cloudflared` share one. Everything else — the MCP, the gateway,\n"
                        "the other MCP servers — communicates through **published host ports**.\n"
                        "\n"
                        "That rules out both obvious shortcuts:\n"
                        "\n"
                        "- **`host.docker.internal:8095`** — the pattern the sibling MCP servers use — cannot work, because\n"
                        "  `atlas-api` publishes on `127.0.0.1` only. From inside a container that name resolves to the host's\n"
                        "  bridge address, not its loopback, so the connection is refused.\n"
                        "- **`http://atlas-api:8000`** over a shared network fails differently, and would fail even if the\n"
                        "  networks were joined: the gate checks the `Host` header against an allow-list *before any handler\n"
                        "  runs* and answers **421** to anything not on it. The homelab leaves `ATLAS_INTERNAL_HOSTS`, the one\n"
                        "  setting that admits a Bearer request on an internal name, empty.\n"
                        "\n"
                        "The OSS install takes the short way on purpose. There the name `atlas-api` on the internal `mcp`\n"
                        "network belongs to `atlas-tls`, whose unpublished `:9443` front proxies over HTTPS to atlas-api's own\n"
                        "TLS listener, and the install sets `ATLAS_INTERNAL_HOSTS: atlas-api:9443` so that Host is admitted for\n"
                        "a Bearer request with no cookie. atlas-mcp checks the certificate against the install CA only, and it\n"
                        "shares no network with `atlas-api`, so it has no plain-HTTP route to it at all.\n"
                        "\n"
                        "## Who is allowed to do what\n"
                        "\n"
                        "Authorization is an **ASGI middleware**, deliberately not a dependency — `app.mount()` is invisible to\n"
                        "`Depends()`, which is why the old static mount was deleted outright and assets are served from an\n"
                        "explicit allow-list instead. The gate is deny-by-default over every route, including ones added later\n"
                        "and including 404s.\n"
                        "\n"
                        "Two kinds of caller, two different checks:\n"
                        "\n"
                        "- **Browsers** hold a server-side session behind a `__Host-` prefixed cookie. `__Host-` requires\n"
                        "  `Secure`, so a login **cannot** work over plain HTTP — that is why the raw port is not a usable\n"
                        "  alternative to the TLS hostname, not merely a discouraged one. State-changing requests additionally\n"
                        "  need an Origin match and a synchronizer token.\n"
                        "- **Machines** present `Authorization: Bearer atlas_svc_…`, one principal per caller, hashed at rest.\n"
                        "  A Bearer caller is never redirected: a 302 would hand a script an HTML login page to parse as data.\n"
                        "\n"
                        "Since 2026-08-15 a token's **scopes are enforced**. `guard_write` maps every mutating route, keyed on\n"
                        "`(method, route template)` because several paths carry an ordinary write verb and a destructive one,\n"
                        "and a route with no entry **fails closed**. Before that the scopes were recorded, displayed, and\n"
                        "checked by nothing.\n"
                        "\n"
                        "## Where state lives, and what deliberately has none\n"
                        "\n"
                        "Postgres holds the graph, the backlog with its acceptance criteria, results, node documentation and\n"
                        "node intent. Nothing else is durable: the MCP is stateless, the gateway is stateless, and the board is\n"
                        "files served from an allow-list.\n"
                        "\n"
                        "There is deliberately **no ingest**, and in the homelab **no backup** as a practice (the `no-backup`\n"
                        "intent on this node). Each is a decision rather than an omission — boot being a read is what lets\n"
                        "Atlas own its own data, and losing the server means losing the work, accepted with the numbers in\n"
                        "view. `tools/snapshot.sh` is not a backup: it takes one verified recovery point, deliberately, before\n"
                        "a bounded experiment, and it is never scheduled. The OSS install offers backups as an install's\n"
                        "opt-in (`atlas-admin backup`, and the `backup` compose profile), off unless asked for.\n"
                        "\n"
                        "**Graph writes are audited, and that one is no longer a gap.** `graph_event` shipped on 2026-08-24,\n"
                        "beside `auth_event`, which stays identity-only. It has exactly ONE writer, `deps.record_graph_event`,\n"
                        "and that is enforced rather than requested: `verify_graph_event.py` tokenises every backend module\n"
                        "and fails on a second construction site. The actor comes off the authenticated principal and from\n"
                        "nowhere else, so a request body cannot forge one. `GET /api/events` reads it back, paged like\n"
                        "`GET /api/workitems` and access-filtered inside the query on both governing node columns *before*\n"
                        "`total` is counted. Two properties worth stating rather than assuming: append-only here is a\n"
                        "**convention with no trigger behind it**, nothing prunes the table, and `detail` is not a diff.\n"
                        "\n"
                        "## Staging is a separate stack, not a mirror\n"
                        "\n"
                        "Its own containers, its own database volume, its own TLS terminator on `:8444`. It cannot damage live.\n"
                        "It is where anything schema-shaped gets rehearsed, because the disposable test container is a *fresh*\n"
                        "database and the thing that breaks a deploy is `ALTER` against a populated one.\n"
                        "\n"
                        "Its graph is **not** a copy of live and should not be trusted for anything shape-dependent — check\n"
                        "what it actually holds before drawing conclusions from it.\n"
                        "\n"
                        "## Where the code and the configuration live\n"
                        "\n"
                        "One repo per world root (ADR 0031), so Atlas's code, decisions and deploy notes are all in\n"
                        "`project-atlas`. Nothing about Atlas lives in the seat repo any more except four ADRs that bind more\n"
                        "than one root.\n"
                        "\n"
                        "```\n"
                        "backend/main.py           the FastAPI app and startup; it serves the board too. Boot is a read\n"
                        "backend/routers/          the routes, one module per area; db.py and deps.py hold what they share\n"
                        "backend/app.py            a compatibility shim for `import app` / `uvicorn app:app`, not code\n"
                        "backend/auth.py           the ASGI gate: sessions, service tokens, scopes, CSRF\n"
                        "backend/github_link.py    GitHub OAuth identity + repo invitations; secrets are files\n"
                        "backend/models_graph.py   node / edge / acceptance_criterion / workitem_result / graph_event\n"
                        "backend/node_types.py     the type registries -- node_type and edge_type, seeded every boot\n"
                        "backend/attention.py      status vocabulary, rollups, effective bindings\n"
                        "backend/verify_*.py       the suite. verify_all.py runs it; TESTING.md explains it\n"
                        "backend/migrate_*.py      one-time migrations, dry-run by default, --apply required\n"
                        "frontend/v2/              the board at /board: ES modules, no build step. ASSET_FILES in\n"
                        "                          routers/assets.py is the live list\n"
                        "frontend/board.html       the frozen legacy board at /board/legacy, its modules beside it\n"
                        "frontend/vendor/          D3, marked, mermaid -- vendored, not fetched\n"
                        "mcp/                      the MCP server: Dockerfile, compose, app/{client,tools_*}.py\n"
                        "deploy/prod.override.yml  every env key atlas-api needs, and caddy-lan + cloudflared\n"
                        "deploy/staging.override.yml   the staging stack\n"
                        "deploy/homelab/           the homelab MCP's tracked settings (atlas-mcp.env: ATLAS_BASE_URL)\n"
                        "deploy/oss/               the OSS install: one compose file, its Caddy, its own ./secrets/\n"
                        "deploy/github-empty/      the empty default mounted at /run/github -- feature off\n"
                        "deploy/caddy/             the TLS terminator's Dockerfile and Caddyfile\n"
                        "tools/stage.sh            put a branch on staging\n"
                        "tools/snapshot.sh         one verified recovery point, taken by hand on the server\n"
                        "docs/adr/                 Atlas's own decision records\n"
                        "docs/vision/              the design documents behind the board and the graph\n"
                        "notes/                    deploy, smoke, server-access and recovery notes\n"
                        "```\n"
                        "\n"
                        "**Configuration splits three ways, and the split is the thing to learn** (the homelab; the OSS\n"
                        "install keeps all of it under `deploy/oss/`, with secrets as files in its `./secrets/`):\n"
                        "\n"
                        "In the homelab `atlas-api` declares **no `env_file:`**, so every key it needs is named explicitly in\n"
                        "the overlay. A value sitting in `.env` reaches compose's `${...}` interpolation but not the process.\n"
                        "Check with `docker compose config`, never by reading `.env`. The OSS install does the opposite on\n"
                        "purpose: atlas-api and atlas-mcp take `env_file: .env`, every variable the code reads is listed in\n"
                        "its `.env.example`, and that `.env` holds no secret.\n"
                        "\n"
                        "**Where nothing lives:** there is no backlog directory, no world manifest, no status-override file.\n"
                        "All of it is Postgres. If you find an instruction to run an importer or a sync script, it is stale --\n"
                        "those were deleted, and nothing in this repo can now overwrite the database from a file.\n"
                        "\n"
                        "## Things that will bite\n"
                    ),
                },
                {
                    "path": "docs/nodes/atlas/onboarding.md",
                    "kind": "runbook",
                    "title": "Connect your agent to Atlas",
                    "format": "md",
                    "content": (
                        "# Connect your agent to Atlas\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## What you end up with\n"
                        "\n"
                        "| | |\n"
                        "|---|---|\n"
                        "| **A board login** | You read and edit the work yourself, in a browser |\n"
                        "| **An agent credential** | A token your agent uses to read and write Atlas as you |\n"
                        "| **The same view as you** | Your agent sees exactly the projects you can see — no more |\n"
                        "| **MCP tools** (optional, Step 6) | Your agent calls Atlas-backed services as tools, with the same credential |\n"
                        "\n"
                        "An agent credential is **not** a second account to manage. Atlas creates the\n"
                        "identity behind it for you, and it disappears from your life the moment you\n"
                        "revoke it.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Before you start\n"
                        "\n"
                        "- a **username** — lowercase, permanent, never renamed\n"
                        "- a **temporary password** — shown to him exactly once, so he will hand it over\n"
                        "  directly rather than paste it somewhere\n"
                        "\n"
                        "There is no sign-up and no password-reset link. If the temporary password is\n"
                        "lost, ask him for another; nothing is recoverable.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Step 1 — Sign in and choose your own password\n"
                        "\n"
                        "You will be asked to set your own password immediately. This is not optional and\n"
                        "cannot be postponed — the temporary one dies the moment it is used successfully.\n"
                        "\n"
                        "> **What makes a password acceptable here:** at least 8 characters, and it is\n"
                        "> checked against a bundled list of known-breached passwords. Length beats\n"
                        "> cleverness; a passphrase is easier to type and harder to guess than\n"
                        "> `P@ssw0rd!`.\n"
                        "\n"
                        "When you land on the board, you are done with this step.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Step 2 — Create an agent credential\n"
                        "\n"
                        "Open the **profile menu** (your username, top right) → **Settings** → scroll to\n"
                        "**Agent credentials** → *Create an agent credential*.\n"
                        "\n"
                        "**1. What is this agent for?**\n"
                        "\n"
                        "Name it for its **purpose**, not for yourself. Your username is added\n"
                        "automatically, so typing `claude-code` gives you an agent called\n"
                        "`yourname-claude-code`.\n"
                        "\n"
                        "> Agent names are **permanent** and can never be renamed. If you run more than\n"
                        "> one agent later, `claude-code` and `fleet` will tell them apart; `agent` will\n"
                        "> not.\n"
                        "\n"
                        "**2. What may it do?**\n"
                        "\n"
                        "| Scope | What it allows | When to pick it |\n"
                        "|---|---|---|\n"
                        "| `read` | See the board — projects, work, documents | Always. This is the default |\n"
                        "| `write` | File and edit work: create tasks, change status, edit descriptions; start research rounds over MCP | If you want to do Step 5, or start rounds in Step 6 |\n"
                        "\n"
                        "Read-only is preselected on purpose. You can always create a second credential\n"
                        "with more later; you cannot un-leak one that had more than it needed.\n"
                        "\n"
                        "**3. Expires after**\n"
                        "\n"
                        "Every credential expires. Pick the shortest span you will actually tolerate —\n"
                        "90 days is a reasonable default, and rotating it later takes under a minute\n"
                        "(see *Looking after it*).\n"
                        "\n"
                        "**4. Your password, again**\n"
                        "\n"
                        "Yes, the one you just used to sign in. This is deliberate: without it, anyone\n"
                        "who got hold of your signed-in browser could mint a key that keeps working long\n"
                        "after you changed your password.\n"
                        "\n"
                        "### Then copy it — immediately\n"
                        "\n"
                        "The token appears **once**, in an orange panel that says so. It is not stored\n"
                        "anywhere you can read it back, and no support request can recover it.\n"
                        "\n"
                        "- Click **Copy**\n"
                        "- Put it straight into a file, as Step 3 describes\n"
                        "- Only then click **I have saved it**\n"
                        "\n"
                        "If you lose it, nothing breaks — you revoke it and issue another. But you cannot\n"
                        "look it up.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Step 3 — Store the token properly\n"
                        "\n"
                        "**Do not type it at a shell prompt, and do not paste it into a chat with your\n"
                        "agent.** Both write it down somewhere you did not intend: your shell keeps a\n"
                        "history file, and an agent's conversation is recorded. The runtime should read\n"
                        "the value from a file; the model should never see it.\n"
                        "\n"
                        "### Put it in a file — the editor way\n"
                        "\n"
                        "This is the way to do it. It works everywhere, on every platform, and there is\n"
                        "nothing to get subtly wrong.\n"
                        "\n"
                        "**1. Create the file with the key already in it:**\n"
                        "\n"
                        "**3. Paste the token immediately after the `=`**, so the whole file is one line\n"
                        "with no spaces and no quotes:\n"
                        "\n"
                        "```\n"
                        "ATLAS_TOKEN=atlas_svc_a1b2c3_...\n"
                        "```\n"
                        "\n"
                        "Save and close. That is the whole of it.\n"
                        "\n"
                        "> **What a correct file looks like:** one line, starting `ATLAS_TOKEN=`, and the\n"
                        "> value after the `=` is about 60 characters beginning `atlas_svc_`. No quotes,\n"
                        "> no trailing spaces, nothing on a second line.\n"
                        "\n"
                        "### The terminal way, if you prefer it\n"
                        "\n"
                        "Only in a **real interactive terminal** — not through a tool or an agent that\n"
                        "captures output, where `read` gets no input and silently stores nothing:\n"
                        "\n"
                        "`%s` there is `printf`'s placeholder — it is where your token gets substituted,\n"
                        "not something you edit. Nothing is echoed while you paste; that is `-s` working,\n"
                        "not the terminal ignoring you.\n"
                        "\n"
                        "### Then make it readable only by you\n"
                        "\n"
                        "**macOS or Linux:**\n"
                        "\n"
                        "**Windows** — and this matters, because `chmod` looks like it worked and does\n"
                        "not. Run it in Git Bash and the file still reports `644` afterwards, and Windows\n"
                        "still grants SYSTEM and Administrators full control: the POSIX permission bits\n"
                        "are cosmetic on NTFS. Use the real mechanism instead:\n"
                        "\n"
                        "> Even without that step, a file under `C:\\Users\\<you>\\` is not readable by other\n"
                        "> *standard* users by default. The `icacls` commands additionally remove\n"
                        "> Administrators and SYSTEM, which is the difference between \"other people\n"
                        "> cannot casually read it\" and \"only I can read it\".\n"
                        "\n"
                        "### Check the file before you go on\n"
                        "\n"
                        "**Expect about 60.** If it says `0`, the paste did not land — fix that now,\n"
                        "rather than reading the 401 in Step 4 as a server problem.\n"
                        "\n"
                        "### Why not the obvious ways\n"
                        "\n"
                        "### Point your agent at the file\n"
                        "\n"
                        "Whatever runs your agent should read `ATLAS_TOKEN` from that file rather than\n"
                        "holding a copy. For a shell session that needs it, source the file rather than\n"
                        "retyping it — this is the pattern the rest of this project uses:\n"
                        "\n"
                        "The value is expanded inside that shell and never appears in your history. For\n"
                        "MCP, Step 6 shows how a client reads the same file at connect time.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Which address to use\n"
                        "\n"
                        "There are three addresses in circulation and only one of them is yours:\n"
                        "\n"
                        "> **`:8095` will not work, and it is worth knowing why before you lose an\n"
                        "> afternoon to it.** That port is bound to `127.0.0.1` — the loopback interface —\n"
                        "> so it means something *only on the server itself*. From any other machine the\n"
                        "> connection is refused outright rather than answered with an error: `curl` exits\n"
                        "> with code 7 and prints nothing useful.\n"
                        ">\n"
                        "> `localhost:8095` is worse, because it *looks* plausible and will sometimes\n"
                        "> answer — with **something else entirely** that happens to be running on your own\n"
                        "> machine. This project has already lost a full working session to that exact\n"
                        "> confusion, which is why it is written down here.\n"
                        "\n"
                        "Every public address is HTTPS only: `http://` is redirected to `https://`.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Step 4 — Check it works\n"
                        "\n"
                        "**The real check is your agent.** Ask it to fetch `/api/me` and tell you what\n"
                        "came back. If it can, you are finished.\n"
                        "\n"
                        "To check by hand:\n"
                        "\n"
                        "The length line is there so a failed paste announces itself. `0` means the file\n"
                        "is empty or malformed — go back to Step 3; the 401 below would otherwise look\n"
                        "like a server problem.\n"
                        "\n"
                        "You should see your **agent's** name, not yours:\n"
                        "\n"
                        "```json\n"
                        "{ \"username\": \"yourname-claude-code\", \"is_service\": true,\n"
                        "  \"role\": \"member\", \"scopes\": [\"read\"] }\n"
                        "```\n"
                        "\n"
                        "That is correct and expected. Your agent has its own identity, linked to you —\n"
                        "which is why revoking it does not touch your own sign-in.\n"
                        "\n"
                        "> **A failure is JSON, never a login page.** If the credential is missing, wrong\n"
                        "> or expired you get `401` with a short JSON body. You will **never** be\n"
                        "> redirected to the sign-in screen, and that is deliberate: a script that\n"
                        "> followed a redirect would parse an HTML login page as if it were data and\n"
                        "> report nonsense. So if you ever receive HTML from an `/api/` call, the header\n"
                        "> did not arrive — something stripped it, or it was never sent.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Step 5 — File your first task\n"
                        "\n"
                        "This needs the `write` scope. If you took `read`, issue a replacement credential\n"
                        "with `write` first (*Looking after it*, below) — or just read on.\n"
                        "\n"
                        "### Find something first\n"
                        "\n"
                        "Work is found through the API, never by reading files — there is no markdown\n"
                        "backlog behind Atlas, and has not been since August 2026.\n"
                        "\n"
                        "`q` searches three places at once: the specification, the acceptance criteria and\n"
                        "any recorded outcome. Other filters that compose with it: `type`, `status`,\n"
                        "`parent`, `binding`, `assignee`.\n"
                        "\n"
                        "> **`assignee`, not `owner`.** Who a piece of work belongs to is an **account** —\n"
                        "> `assignee=me` resolves against whoever is calling, so your agent can ask its own\n"
                        "> question without putting a user id in a URL. `owner` was the free-text string\n"
                        "> that preceded it and it is **retired on workitems**: sending it to\n"
                        "> `POST`/`PATCH /api/workitem` is a `400` naming the field, not a silent drop. It\n"
                        "> still means something on a *world* node, where it is \"who looks after this part\n"
                        "> of the world\".\n"
                        "\n"
                        "Read one in full — description and acceptance criteria — with:\n"
                        "\n"
                        "### Then file one\n"
                        "\n"
                        "The response contains the id it was given, e.g. `\"id\": \"TASK-1234\"`.\n"
                        "\n"
                        "Three things about that request worth knowing:\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Step 6 — Connect through MCP (optional)\n"
                        "\n"
                        "MCP lets your agent call services as **tools** instead of building URLs. The same\n"
                        "agent credential from Step 2 is the key, and **Atlas decides who gets in**: each\n"
                        "request's token is checked against Atlas, and against whether your account can see\n"
                        "the project behind the service. There is no second account and no second password.\n"
                        "\n"
                        "### Which MCP servers exist\n"
                        "\n"
                        "```python\n"
                        "import json\n"
                        "from pathlib import Path\n"
                        "\n"
                        "line = (Path.home() / \".atlas\" / \"credentials\").read_text(encoding=\"utf-8\").strip()\n"
                        "token = line.split(\"=\", 1)[1].strip() if line.startswith(\"ATLAS_TOKEN=\") else \"\"\n"
                        "print(json.dumps({\"Authorization\": f\"Bearer {token}\"}))\n"
                        "```\n"
                        "\n"
                        "**Never run it by hand**: what it prints is your token. On Windows, give it the\n"
                        "same `icacls` treatment as the credentials file in Step 3.\n"
                        "\n"
                        "**2. Register the server** (one line; on Windows write the path as\n"
                        "`C:/Users/<you>/.atlas/mcp-header.py`, and on macOS use `python3`):\n"
                        "\n"
                        "**3. Check it:**\n"
                        "\n"
                        "`Status: ✔ Connected` means you are in. Restart Claude Code (or run `/mcp`), and\n"
                        "your agent has six new tools: `small_iteration_round`, `large_iteration_round`,\n"
                        "`round_status`, `leaderboard`, `list_rounds` and `market_moonwalk`.\n"
                        "\n"
                        "### When MCP doesn't connect\n"
                        "\n"
                        "`claude mcp get` shows the server's own answer:\n"
                        "\n"
                        "### Other MCP clients\n"
                        "\n"
                        "Any client that can send an `Authorization: Bearer` header to an HTTP MCP server\n"
                        "works the same way. Prefer one that reads the header from a file or a command at\n"
                        "connect time; never paste the token into a config file that lives in a repository.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## What your agent can see\n"
                        "\n"
                        "**Exactly what you can see.** Not more, and you do not have to configure it.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Looking after the credential\n"
                        "\n"
                        "Everything below lives where you created it: **Settings → Agent credentials**.\n"
                        "\n"
                        "### Rotating it — no downtime\n"
                        "\n"
                        "Replace it periodically, and after any scare. In this order your agent never\n"
                        "stops working:\n"
                        "\n"
                        "Doing it the other way round takes your agent offline in between, which is why\n"
                        "the screen tells you the order.\n"
                        "\n"
                        "This is also how you **change scope**: issue a replacement with `write`, deploy\n"
                        "it, then revoke the old one.\n"
                        "\n"
                        "### Revoking it\n"
                        "\n"
                        "Revoking a credential **does not delete the agent** — the identity stays, still\n"
                        "linked to you, ready for a new credential whenever you want one.\n"
                        "\n"
                        "### Reading the list\n"
                        "\n"
                        "| What it says | What it means |\n"
                        "|---|---|\n"
                        "| **never used** | Nothing has presented it yet. If that surprises you, your agent is not actually using it |\n"
                        "| **in use** | Presented recently |\n"
                        "| **unused N days** | Still valid, nothing is using it. A good candidate to revoke |\n"
                        "| **expired** / **revoked** | Dead. Kept in the list so you can see what happened |\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Rules that matter\n"
                        "\n"
                        "These are short because they are absolute.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## When it doesn't work\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## The endpoints you will want first\n"
                        "\n"
                        "| Endpoint | What it gives you |\n"
                        "|---|---|\n"
                        "| `GET /api/me` | Who Atlas thinks you are — start here |\n"
                        "| `GET /api/graph/tree` | The whole project tree you can see |\n"
                        "| `GET /api/workitems?q=search+terms` | Search across specs, acceptance criteria and results |\n"
                        "| `GET /api/node/{id}` | One item, complete, with its acceptance criteria |\n"
                        "| `POST /api/workitem` | File a new task *(needs `write`)* |\n"
                        "\n"
                        "If your agent speaks MCP, Step 6 lists the MCP servers and how to connect them.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## What Atlas knows about you\n"
                        "\n"
                        "Named accounts, sign-in times, and a security log of who signed in from where.\n"
                        "The full statement is at [**How your data is handled**](/auth/privacy) — linked\n"
                        "from the login page and from your profile, readable without signing in.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-adr-registry",
            "type": "capability",
            "name": "ADR registry",
            "summary": "The registry of architecture decision records: one table that indexes ADR files synced from git and holds decisions made in Atlas, with per-project ids and the project's visibility.",
            "docs": [],
        },
        {
            "id": "atlas-api",
            "type": "service",
            "name": "atlas-api",
            "summary": "Atlas's FastAPI process: the JSON read and write API over the node/edge graph in Postgres, plus the sign-in pages, the board and the handbook, all behind one authorization gate.",
            "abstract": (
                "atlas-api is Project Atlas's backend and API layer — a FastAPI service (`backend/main.py`, routes in `backend/routers/<area>.py`; `app.py` is a compatibility shim) that owns the read and write path over the graph. It is the **backend**, not the UI: `atlas-board-shell` and its siblings own client-side rendering; atlas-api serves JSON plus the HTML entry points (`/`, `/board`) the frontend attaches to.\n"
                "\n"
                "**Boot is a READ.** It used to re-ingest the backlog directory and the world manifest on every startup and reconcile the node/edge store against them; both ingests are deleted. The graph lives in Postgres and changes only through the write API, so nothing at startup opens a file. Boot reads no file and never overwrites graph data. What it does write is schema-shaped: the `node_type` and `edge_type` registries and the `task_id_seq` floor, every boot; on an empty graph, the seed the install's `ATLAS_SEED` profile names (homelab v0 tables, demo, self, plus the system node).\n"
                "\n"
                "Its headline read endpoint is `GET /api/graph?focus&depth&edges&cap` — a bounded-neighbourhood BFS over the node/edge store (both directions, no fixed root), which is the read path the context-economy / agent-retrieval model is built on. `GET /api/graph/tree` projects the same store through `contains` edges only, for tree rendering. `GET /api/tree` is the older v0 Component-tree read with hand-derived RAG rollup, kept for compatibility; 404 when `ATLAS_LEGACY_V0=0` (default on in this distribution, off in the neutral copy).\n"
                "\n"
                "Depends on **atlas-postgres** for every read and write — `docker-compose.yml` encodes this via `depends_on: {atlas-postgres: {condition: service_healthy}}`. Liveness is `GET /healthz` (no DB); readiness is `GET /readyz`, which fails when atlas-postgres is unreachable. `/api/health` is an authenticated read of counts and drift reports, not a health verdict."
            ),
            "detail": "See `docs/nodes/atlas-api/architecture.md` (this node's Docs tab) for the full request-path diagram and the boot sequence, and `docs/nodes/atlas-api/api.md` for the endpoint reference. Operationally it is a single Python process in one Docker container (`backend/Dockerfile`, built from the `project-atlas` repo root), stateless itself — all state lives in atlas-postgres — so redeploying it is a plain container recreate with no data-migration concerns on this side. The schema-reconcile step at startup (`_reconcile_graph_schema`, additive `ALTER TABLE ... ADD COLUMN` only) is a migration shim, not a full migration tool; see the code comment in its docstring in `main.py` for its scope and limits.",
            "docs": [
                {
                    "path": "docs/nodes/atlas-api/api.md",
                    "kind": "api",
                    "title": "atlas-api endpoint reference",
                    "format": "md",
                    "content": (
                        "# atlas-api — API reference\n"
                        "\n"
                        "`docs[]` entry: `{kind: api, title: \"atlas-api endpoint reference\", path: \"docs/nodes/atlas-api/api.md\", format: md}`\n"
                        "\n"
                        "Base URL: the container listens on `:8000`. **This is no longer reachable unauthenticated, and no\n"
                        "longer reachable on the LAN via the raw host port.** Both are corrections to what this document used\n"
                        "to say here — see the note below before trusting anything else in it about the network layer.\n"
                        "\n"
                        "## `GET /api/health`\n"
                        "\n"
                        "**Not a probe, and it must never become one.** It is behind the gate like everything else, and\n"
                        "infrastructure uses `/healthz` (liveness, no DB) and `/readyz` (readiness, DB) precisely so that\n"
                        "nothing needs a healthcheck on this route. What it is instead: an authenticated read of a few numbers\n"
                        "and drift reports worth having in one place.\n"
                        "\n"
                        "- **Request:** no params.\n"
                        "- **Response `200`:**\n"
                        "  ```json\n"
                        "  {\n"
                        "    \"ok\": true,\n"
                        "    \"seeded\": true,\n"
                        "    \"node_count\": 0, \"edge_count\": 0, \"event_count\": 0,\n"
                        "    \"token_warnings\": [],\n"
                        "    \"undocumented_assets\": [],\n"
                        "    \"name_register_warnings\": [],\n"
                        "    \"edge_type_warnings\": []\n"
                        "  }\n"
                        "  ```\n"
                        "  `seeded` is `true` once at least one legacy `Component` row exists — a rough \"has data\" signal.\n"
                        "  `node_count`/`edge_count` replaced the deleted `ingest_*` fields as the \"is there data in here at\n"
                        "  all\" signal (TASK-1058); `event_count` is there because `graph_event` is never pruned on purpose,\n"
                        "  and a decision to let something grow forever is only safe while somebody can see how fast.\n"
                        "\n"
                        "  The four list fields are **drift reports, normally empty**, and they all share one shape:\n"
                        "  `token_warnings` (near-expiry or long-unused service tokens), `undocumented_assets` (first-party\n"
                        "  assets the board serves that no node document mentions), `name_register_warnings` (a slug that\n"
                        "  leaked into a prose-register name — reported, never transformed, see `node_types.PROSE_NAME_TYPES`)\n"
                        "  and `edge_type_warnings` (drift between the `edge_type` registry and what `edge` actually holds:\n"
                        "  used-but-undeclared, declared-active-with-no-rows, a `(src_family, dst_family)` pair the type does\n"
                        "  not declare, and — the hazard — a `dead` type that has gained a row).\n"
                        "\n"
                        "## `GET /api/tree`\n"
                        "\n"
                        "The **v0 Component tree** — hand-seeded/backlog-ingested architecture map with worst-child RAG rollup\n"
                        "(🔴→🟠→🟡→🟢→🟣). Legacy path, predates the ADR 0019 node/edge graph; kept for compatibility; 404 when\n"
                        "`ATLAS_LEGACY_V0=0` (default on in this distribution, off in the neutral copy).\n"
                        "\n"
                        "- **Request:** no params.\n"
                        "- **Response `200`:** `{ \"roots\": [ <node>, ... ] }` where each `<node>` carries `id`, `name`, `category`,\n"
                        "  `kind`, RAG `status`, gates/requirements, and nested `children`.\n"
                        "\n"
                        "## `GET /api/graph`\n"
                        "\n"
                        "**Bounded-neighbourhood** read over the ADR 0019 node/edge store — the primary graph API, and the one\n"
                        "`attributes.docs[]`-driven agent retrieval (ADR 0020) is expected to sit in front of.\n"
                        "\n"
                        "- **Query params:**\n"
                        "  | param | type | default | meaning |\n"
                        "  |---|---|---|---|\n"
                        "  | `focus` | string | *(optional)* | node id to center the BFS on. **Omit it** to seed from every derived root instead — \"a node with no incoming `contains`\", the same derivation `/api/graph/tree` uses — which is how a caller asks for \"the world\" without naming a node |\n"
                        "  | `families` | string | *(none)* | comma-separated `node_type` families to seed from; only meaningful when `focus` is omitted. `object,bridge` is what the World tab wants — without it a rootless read also seeds from the workitem epics, because the work hierarchy and the world hierarchy still share the `contains` edge type |\n"
                        "  | `depth` | int | `2` | hops outward, both directions; clamped to `[0, 20]` |\n"
                        "  | `edges` | string | *(all types)* | comma-separated `edge_type` allowlist (e.g. `contains,works_on`) |\n"
                        "  | `cap` | int | `500` | max nodes returned before truncating |\n"
                        "  | `include_prose` | int | `0` | opt in to the raw `body` field and the body-projection fallback. A neighbourhood is a LISTING, so prose is off by default; authored `abstract`/`detail`/`summary` come back either way |\n"
                        "  | `prose` | string | *(full)* | `none` \\| `summary` \\| `abstract` \\| `full` — drops authored prose tiers. Only ever removes fields |\n"
                        "  | `work` | string | *(items)* | `none` drops the work fields; `counts` keeps `work_load` count/done/completion without its per-workitem list |\n"
                        "  | `meta` | string | *(full)* | `none` drops `owner`, `source`, `attributes`, `assignee`, `completed_at` |\n"
                        "  | `strict` | int | `0` | `1` turns a reached `cap` into `413 too_large` instead of a truncated `200` |\n"
                        "  | `max_bytes` | int | *(none)* | a response body over this size is `413 too_large`, reporting `bytes` |\n"
                        "- **Response `200`:**\n"
                        "  ```json\n"
                        "  {\n"
                        "    \"focus\": \"atlas-api\",\n"
                        "    \"depth\": 2,\n"
                        "    \"edge_types\": null,\n"
                        "    \"nodes\": [\n"
                        "      {\n"
                        "        \"id\": \"atlas-api\", \"type\": \"service\", \"name\": \"atlas-api\",\n"
                        "        \"summary\": \"...\", \"abstract\": \"...\", \"detail\": \"...\",\n"
                        "        \"owner\": null, \"source\": null,   // world-node rows are atlas-origin; the manifest is deleted\n"
                        "        \"attributes\": { \"role\": \"api\", \"...\": \"...\" },\n"
                        "        \"attention\": \"green\", \"completion\": 0.8, \"attention_count\": 0\n"
                        "      }\n"
                        "    ],\n"
                        "    \"edges\": [\n"
                        "      { \"id\": 1, \"src\": \"atlas-api\", \"dst\": \"atlas-postgres\", \"edge_type\": \"works_on\", \"directed\": true }\n"
                        "    ],\n"
                        "    \"truncated\": false\n"
                        "  }\n"
                        "  ```\n"
                        "- **Errors:** `413 { \"error\": \"too_large\", ... }` only when `strict` or `max_bytes` asked for it — the board\n"
                        "  passes neither, and `atlas_tree` passes both. `404 { \"error\": \"unknown focus node '<id>'\" }` when `focus` names a node that does not\n"
                        "  exist **or that this caller may not see** — the two are deliberately indistinguishable, because a\n"
                        "  distinguishable refusal confirms a guessed id.\n"
                        "- **A rootless read that matches no roots is `200` with empty `nodes`/`edges`, not `404`** (changed\n"
                        "  2026-08-28). An empty store, or a `families` filter nothing matches, is a legitimate answer with\n"
                        "  nothing in it. It used to 404, which meant this route and `/api/graph/tree` disagreed about the same\n"
                        "  database off the same derivation, and the board printed *\"Could not load the backlog graph.\"* at a\n"
                        "  brand-new one. The `focus` 404 above is **not** that case and is unchanged: there the caller named a\n"
                        "  node, so there is something to confirm or deny; here nothing was named.\n"
                        "- Never returns an edge whose endpoints aren't both in the returned node set (no dangling references into\n"
                        "  capped-out nodes).\n"
                        "\n"
                        "## `GET /api/graph/tree`\n"
                        "\n"
                        "`contains`-only projection of the node/edge store, rendered as a tree (roots = nodes with no incoming\n"
                        "`contains` edge). Powers the world-tree / work-tree tab views (ADR 0019 addendum).\n"
                        "\n"
                        "- **Request:** no params.\n"
                        "- **Response `200`:** `{ \"roots\": [ <node-with-children>, ... ] }`, each node shaped like `/api/graph`'s\n"
                        "  node objects plus a nested `children` array and (for `object`-family nodes) a `work_load` readout\n"
                        "  (`{count, done, completion, workitem_ids}`).\n"
                        "\n"
                        "## `GET /api/vocabulary`\n"
                        "\n"
                        "The write API's legal vocabulary, machine-readable (TASK-1081). Atlas serves no OpenAPI document on\n"
                        "purpose — `docs_url`/`redoc_url`/`openapi_url` are all `None` — because that would dump the whole route\n"
                        "surface to any authenticated caller; this is one curated, scoped endpoint instead of that. Without it,\n"
                        "a caller of `POST/PATCH /api/node`, `/api/workitem` or `/api/edge` has no way to learn a legal node\n"
                        "type, edge type, status or intent kind except by collecting `400`s until it stops getting them.\n"
                        "\n"
                        "Every value is **derived at request time**, never transcribed by hand, from the same constant or table\n"
                        "the write routes themselves validate against:\n"
                        "\n"
                        "- `node_types` — the live `node_type` table (same rows `POST /api/node`'s `type` FK and\n"
                        "  `validate_node`'s attributes-schema check use).\n"
                        "- `edge_types.rules` — `EDGE_FAMILY_RULES`, the same dict `POST /api/edge` looks `edge_type` up in.\n"
                        "  `edge_types.notes` carries one caveat: the work hierarchy (`epic`→`story`→`task`) reuses the\n"
                        "  `contains` edge-type string with a *different* family rule, enforced only inline in\n"
                        "  `create_workitem`/`patch_workitem`, never through this generic map.\n"
                        "- `workitem_statuses` — `attention.SETTABLE_STATUSES`, the same tuple `POST/PATCH /api/workitem` and\n"
                        "  `POST /api/node/{id}/status` validate against.\n"
                        "- `workitem_types` — `WORKITEM_TYPES` (`epic`, `story`, `task`).\n"
                        "- `intent_kinds` — `node_types.INTENT_KINDS`, the same tuple `POST /api/node/{id}/intent` validates\n"
                        "  against.\n"
                        "\n"
                        "- **Request:** no params.\n"
                        "- **Response `200`:**\n"
                        "  ```json\n"
                        "  {\n"
                        "    \"node_types\": { \"<type id>\": { \"family\": \"...\", \"attributes_schema\": {...}, \"agent_instructions\": \"...\", \"status\": \"...\" } },\n"
                        "    \"edge_types\": { \"rules\": { \"<edge_type>\": { \"src_family\": \"...\", \"dst_family\": \"...\" } }, \"notes\": { \"contains\": \"...\" } },\n"
                        "    \"workitem_statuses\": [\"...\"],\n"
                        "    \"workitem_types\": [\"epic\", \"story\", \"task\"],\n"
                        "    \"intent_kinds\": [\"...\"]\n"
                        "  }\n"
                        "  ```\n"
                        "- Read-only: needs an authenticated principal (any kind), no write scope — it isn't in\n"
                        "  `WRITE_SCOPE_ROUTES` and never mutates anything.\n"
                        "\n"
                        "## `GET /board`\n"
                        "\n"
                        "Serves `board.html` (the static frontend's board view) — a server-rendered HTML entry point, not a JSON\n"
                        "API. `atlas-frontend`'s static assets attach to this page; `atlas-api` only serves the file.\n"
                        "\n"
                        "## `GET /api/search`\n"
                        "\n"
                        "**\"Which node knows about X?\"** Full-text search across the whole graph — the general surface beside\n"
                        "`GET /api/workitems?q=`, which deliberately narrows its own search to the workitem family. MCP:\n"
                        "`atlas_node_search`, which defaults to `families=object,bridge` and ten per page.\n"
                        "\n"
                        "**Storing is not serving.** A `node_doc` row whose path is not in its owning node's `docs[]` allowlist is\n"
                        "refused by `GET /api/node/{id}/doc`, and search refuses it for the same reason: a caller who cannot read\n"
                        "a document must not learn it exists through the search box instead. Access filtering is applied\n"
                        "**inside** the query, so a node the caller cannot see contributes nothing — not to `items`, not to\n"
                        "`total`, and a search for a term only a hidden node contains is indistinguishable from one for a term\n"
                        "that exists nowhere.\n"
                        "\n"
                        "## The rest of the surface — an accurate inventory, not a reference\n"
                        "\n"
                        "This document has never covered every route with a section of its own, and still does not. For the\n"
                        "complete route list, read `backend/routes.snapshot.txt` (checked in CI by `verify_routes.py`); this\n"
                        "document details only the routes below.\n"
                        "\n"
                        "**Graph and work — reads**\n"
                        "\n"
                        "| | |\n"
                        "|---|---|\n"
                        "| `GET /api/node/{id}` | one node complete: prose, acceptance criteria, parents, bindings, dependencies, own and inherited intents |\n"
                        "| `GET /api/workitems` | list/filter/search — `type`, `status`, `parent`, `binding`, `assignee`, `q` (full-text over spec, acceptance and result), plus `include_prose`, `include_ancestors` and paging. `owner` is still accepted as a *filter* here and matches the legacy string column, but it is retired as a **field** — see the writes table |\n"
                        "| `GET /api/intents` | the intent corpus, owner-side, filterable by kind |\n"
                        "| `GET /api/events` | `graph_event` read-back: who changed what in the graph, paged exactly like `/api/workitems` and access-filtered inside the query on both governing node columns *before* `total` is counted. Grant changes are **not** here — they are `auth_event`, because an event naming who was granted a private project would be a new disclosure |\n"
                        "| `GET /api/people` | the account roster for assignment. `?for=<workitem-id>` narrows it to accounts that can SEE that item. A picker is not a gate: `_resolve_assignee` is where the refusals are enforced |\n"
                        "| `GET /api/node/{id}/doc` | one attached document, and only if its exact `path` is in that node's own `docs[]` |\n"
                        "\n"
                        "**Graph and work — writes.** All behind `guard_write`; each maps to `write` or `admin` in\n"
                        "`WRITE_SCOPE_ROUTES`, and a route with no entry fails closed.\n"
                        "\n"
                        "| | |\n"
                        "|---|---|\n"
                        "| `POST /api/node`, `PATCH /api/node/{id}`, `DELETE /api/node/{id}` | node CRUD; delete refuses a node with edges unless `?cascade=1` |\n"
                        "| `POST /api/node/{id}/status` | effective status, the six settable values |\n"
                        "| `POST /api/edge`, `DELETE /api/edge/{id}` | edges, family-checked against `EDGE_FAMILY_RULES` |\n"
                        "| `POST /api/workitem`, `PATCH /api/workitem/{id}` | the only routes that may create a work-hierarchy `contains` edge. **`owner` is retired on a workitem**: both return `400 {\"error\": \"owner_retired\", \"field\": \"owner\"}` naming the field, because who a workitem belongs to is an ACCOUNT (`assignee`, an `auth_user.id`, or `null`). `owner` is still the right field on an object-family node, through `POST`/`PATCH /api/node`. Also: `PATCH`ing `acceptance` on an existing item is **403 for a Bearer principal** — a browser session may, creation is unaffected |\n"
                        "| `PUT /api/node/{id}/doc`, `DELETE /api/node/{id}/doc` | store a document; storing and publishing are separate acts |\n"
                        "| `POST /api/node/{id}/intent`, `PATCH /api/intent/{id}`, `DELETE /api/intent/{id}` | node intents (ADR 0032). Note the asymmetry: create is under the node, patch and delete are under the intent id |\n"
                        "\n"
                        "**Identity and access**\n"
                        "\n"
                        "| | |\n"
                        "|---|---|\n"
                        "| `GET /api/me` | the caller's own principal — kind, username, role, `is_service`, scopes. Defined in `auth.py`, not `app.py` |\n"
                        "| `GET /api/access` | the whole access matrix, accounts and nodes. Owner-only |\n"
                        "| `PUT /api/access/node/{id}` | one node's visibility and its complete grant list. Owner-only *and* `admin` scope |\n"
                        "| `GET /api/auth-events` | the identity audit log — logins, credential lifecycle, grant changes. Owner-only |\n"
                        "| `POST /api/me/agent-credential` | mint an agent credential; creates and links the bot account in the same call |\n"
                        "| `GET /api/me/agent-credentials` | the caller's own agents and their credentials, with last-used and expiry |\n"
                        "| `POST /api/me/agent/{agent}/credential` | rotation — issues a replacement without revoking the old one |\n"
                        "| `POST /api/me/agent-credential/{token_id}/revoke` | permanent, effective on the next request |\n"
                        "\n"
                        "The four `/api/me/` credential routes are **session-only**: every Bearer principal is refused on kind,\n"
                        "regardless of scope, so one leaked token cannot mint another.\n"
                        "\n"
                        "**Legacy v0 Component reads:** `GET /api/summary`, `GET /api/priority`, `GET /api/goals`.\n"
                        "\n"
                        "**Non-API routes on the same app:** `GET /`, `GET /board`, `GET /assets/{name}` (the allowlisted\n"
                        "frontend modules — the old `/static` mount was **deleted**, not narrowed, because a mount is an ASGI\n"
                        "sub-app that route dependencies cannot see), `GET|POST /auth/login`, `POST /auth/logout`,\n"
                        "`GET|POST /auth/password/change`, `GET /auth/denied`, `GET /auth/privacy`, `GET /favicon.ico`,\n"
                        "`GET /healthz`, `GET /readyz`.\n"
                    ),
                },
                {
                    "path": "docs/nodes/atlas-api/architecture.md",
                    "kind": "architecture",
                    "title": "atlas-api architecture",
                    "format": "mermaid",
                    "content": (
                        "# atlas-api — architecture\n"
                        "\n"
                        "`docs[]` entry: `{kind: architecture, title: \"atlas-api architecture\", path: \"docs/nodes/atlas-api/architecture.md\", format: mermaid}`\n"
                        "\n"
                        "## What this is\n"
                        "\n"
                        "`atlas-api` is Project Atlas's **backend/API service** — a FastAPI service (`backend/main.py`, routes\n"
                        "in `backend/routers/<area>.py`; `app.py` is a compatibility shim) that **reads** the graph out of Postgres and serves it to clients over\n"
                        "HTTP. **There is no ingest**: nothing at startup opens a file or overwrites a row because a file said\n"
                        "so — see \"Startup: boot is a READ\" below, which the opening sentence of this document used to\n"
                        "contradict. It is **not** the UI: a separate `atlas-frontend` node owns the client-side rendering;\n"
                        "`atlas-api` serves data (JSON) plus two server-rendered HTML entry points (`/` and `/board`) that the\n"
                        "frontend's static assets attach to.\n"
                        "\n"
                        "## Request path: client → `/api/graph` → node/edge store\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart LR\n"
                        "    subgraph Client\n"
                        "        Browser[\"Browser / agent client\"]\n"
                        "    end\n"
                        "\n"
                        "    subgraph atlas-api[\"atlas-api (FastAPI, container :8000, host :8095)\"]\n"
                        "        Health[\"GET /api/health\"]\n"
                        "        Tree[\"GET /api/tree<br/>(v0 Component tree, RAG rollup)\"]\n"
                        "        Graph[\"GET /api/graph<br/>?focus&depth&edges&cap<br/>(bounded-neighbourhood BFS)\"]\n"
                        "        GraphTree[\"GET /api/graph/tree<br/>(contains-only projection)\"]\n"
                        "        Board[\"GET /board<br/>(serves board.html)\"]\n"
                        "    end\n"
                        "\n"
                        "    subgraph Store[\"node/edge store (atlas-postgres)\"]\n"
                        "        NodeTbl[(\"node\")]\n"
                        "        EdgeTbl[(\"edge\")]\n"
                        "        CompTbl[(\"component<br/>(legacy v0 tree)\")]\n"
                        "    end\n"
                        "\n"
                        "    Browser -->|\"HTTP GET\"| Health\n"
                        "    Browser -->|\"HTTP GET\"| Tree\n"
                        "    Browser -->|\"HTTP GET\"| Graph\n"
                        "    Browser -->|\"HTTP GET\"| GraphTree\n"
                        "    Browser -->|\"HTTP GET\"| Board\n"
                        "\n"
                        "    Tree --> CompTbl\n"
                        "    Graph -->|\"BFS both directions,<br/>capped at 500 nodes\"| NodeTbl\n"
                        "    Graph --> EdgeTbl\n"
                        "    GraphTree --> NodeTbl\n"
                        "    GraphTree -->|\"edge_type = contains\"| EdgeTbl\n"
                        "```\n"
                        "\n"
                        "`/api/graph` is the bounded-neighbourhood read the context-economy model (ADR 0019 / STORY-atlas-context-\n"
                        "economy) is built on: it starts a BFS at `focus` — or, when `focus` is omitted, at every derived root —\n"
                        "walks outward in both directions (no fixed root) up to `depth` hops along the requested `edges` types,\n"
                        "and stops at `cap` nodes (default 500), setting `truncated: true` if it had to cut off. It never loads\n"
                        "the whole graph for one request.\n"
                        "\n"
                        "## Callers: browser, script, and — since ADR 0033 — `atlas-mcp`\n"
                        "\n"
                        "This was exercised from a container on the server, not assumed — see `mcp/app/client.py`, which\n"
                        "documents both dead ends alongside the working path. The base URL lives in `ATLAS_BASE_URL`\n"
                        "(`mcp/docker-compose.yml`); if the real address ever moves, that is the one line to change, not any\n"
                        "production config on `atlas-api`'s side.\n"
                        "\n"
                        "**Authentication and authorization are unchanged by who the caller is.** `atlas-mcp` is a Bearer\n"
                        "caller like any other service token: gated by `AuthGate`, and — since TASK-1086 — by\n"
                        "`WRITE_SCOPE_ROUTES` on every mutating route it calls. See `atlas-identity`'s architecture doc for the\n"
                        "mechanism and `atlas-graph`'s for what a write and a read traverse once past the gate.\n"
                        "\n"
                        "## Startup: boot is a READ (changed 2026-08-11, TASK-1058)\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart TD\n"
                        "    Start([\"container start\"]) --> Reconcile[\"schema reconcile<br/>(ALTER TABLE for new columns,<br/>CREATE INDEX for missing ones)\"]\n"
                        "    Reconcile --> Types[\"node_types.ensure_*_node_types()<br/>→ node_type registry\"]\n"
                        "    Types --> EdgeTypes[\"edge_type registry\"]\n"
                        "    EdgeTypes --> Seq[\"seed the TASK- id sequence\"]\n"
                        "    Seq --> Seed[\"first-boot seed (ATLAS_SEED)<br/>empty graph only\"]\n"
                        "    Seed --> Serve([\"serve /api/* and /board\"])\n"
                        "```\n"
                        "\n"
                        "**Nothing at boot reads a markdown file or the world manifest, and nothing deletes or overwrites a graph\n"
                        "row because a file said so.** Until 2026-08-11 two reconcilers ran here — one over\n"
                        "`AgentPlanningSetup/backlog`, one over `world_manifest.yaml` — and re-derived the node/edge store from\n"
                        "those files on every restart. The data now lives in Postgres and Atlas is authoritative for it\n"
                        "(`EPIC-atlas-data-in-postgres`), so `ingest_graph.py`, `ingest_world.py` and `ingest_backlog.py` are\n"
                        "deleted, along with the PROTECT / `git_snapshot` / divergence machinery that existed to stop a file\n"
                        "overwriting a board edit. `/api/health` therefore carries no `ingest_*` field either: a signal that\n"
                        "could only be permanently green or permanently red is one people learn to ignore.\n"
                        "\n"
                        "Boot reads no file and never overwrites graph data. What it does write is schema-shaped: the `node_type` and `edge_type` registries and the `task_id_seq` floor, every boot; on an empty graph, the seed the install's `ATLAS_SEED` profile names (homelab v0 tables, demo, self, plus the system node).\n"
                        "\n"
                        "This is asserted, not assumed: `verify_object_graph.py` §15 snapshots every node id, status and body plus\n"
                        "the entire edge set, runs a second real `startup()`, and requires the result to be byte-identical.\n"
                        "\n"
                        "Graph rows are created and changed **only** through the write API (`POST`/`PATCH` `/api/node`,\n"
                        "`/api/edge`, `/api/workitem`), which means one authenticated, guarded path rather than a file-shaped\n"
                        "back door.\n"
                        "\n"
                        "## Dependencies\n"
                        "\n"
                        "- **atlas-postgres** (`db` type) — required. `atlas-api` cannot serve `/api/tree`, `/api/graph`, or\n"
                        "  `/api/graph/tree` without it; `docker-compose.yml` encodes this via `depends_on: atlas-postgres` +\n"
                        "  `condition: service_healthy`.\n"
                    ),
                },
                {
                    "path": "docs/nodes/atlas-api/health-on-the-homelab.md",
                    "kind": "architecture",
                    "title": "health-on-the-homelab.md",
                    "format": "md",
                    "content": (
                        "# Reading `/api/health` on the homelab\n"
                        "\n"
                        "Moved here from `backend/CLAUDE.md` (STORY-oss-23 A4, #287), because these commands name this\n"
                        "estate's hosts and do not ship in the public repository.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-api-group-access",
            "type": "component",
            "name": "Access API",
            "summary": "The API's identity and access surface: sign-in, the caller's own account, access administration, and the audit and event trails.",
            "docs": [],
        },
        {
            "id": "atlas-api-group-graph",
            "type": "component",
            "name": "Graph API",
            "summary": "The API's core over the world graph: reading neighbourhoods, trees and paths, and creating, editing and deleting nodes, edges and intents.",
            "docs": [],
        },
        {
            "id": "atlas-api-group-knowledge",
            "type": "component",
            "name": "Knowledge API",
            "summary": "The API's written knowledge: the ADR registry, the documents attached to nodes, and the static handbook site.",
            "docs": [],
        },
        {
            "id": "atlas-api-group-platform",
            "type": "component",
            "name": "Platform API",
            "summary": "The API's install-level routes: health checks, the board and landing pages, configuration, the setup guide and crunch week.",
            "docs": [],
        },
        {
            "id": "atlas-api-group-search",
            "type": "component",
            "name": "Search & context API",
            "summary": "The API's finding routes: node search, the one-call context package for agents, and the optional semantic retrieval add-on's controls.",
            "docs": [],
        },
        {
            "id": "atlas-api-group-work",
            "type": "component",
            "name": "Work API",
            "summary": "The API's work-tracking routes: epics, stories and tasks, their comments and acceptance proposals, and the caller's own statistics.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-access",
            "type": "component",
            "name": "Access routes",
            "summary": "Owner routes that administer who sees what: the access matrix, node visibility and grants, groups, project role bindings and GitHub repo access, plus the assignable-people list.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-adrs",
            "type": "component",
            "name": "ADR routes",
            "summary": "The ADR registry's routes: list and fetch decision records by id or legacy number, and upsert or create them.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-assets",
            "type": "component",
            "name": "Board and asset routes",
            "summary": "Serves the board page, the frontend's static assets and the bare domain, which shows the landing page to visitors and sends signed-in browsers to the board.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-audit",
            "type": "component",
            "name": "Audit export route",
            "summary": "Owner-only export of the audit trail as JSON Lines, for a SIEM to collect graph changes, sign-in events and audited reads.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-auth",
            "type": "component",
            "name": "Sign-in routes",
            "summary": "The browser sign-in pages and actions: password and SSO login, logout, password change, the privacy notice and the refusal page.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-comments",
            "type": "component",
            "name": "Comment routes",
            "summary": "Routes for the conversation on an epic, story or task: list and post comments, edit your own, and hide one as author or owner.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-config",
            "type": "component",
            "name": "Configuration routes",
            "summary": "Owner routes for the install's configuration: read every setting with its source, and, when board writes are enabled, change a safe one.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-context",
            "type": "component",
            "name": "Context route",
            "summary": "Answers \"tell me about X\" in one bounded call: matching nodes, their neighbourhood and impact, intents, related ADRs and documents to read, all as pointers.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-crunch",
            "type": "component",
            "name": "Crunch week routes",
            "summary": "Owner-session routes that turn crunch week on, extend or end it, plus the banner any signed-in browser reads; every token is refused.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-docs",
            "type": "component",
            "name": "Document routes",
            "summary": "Routes that read, write and delete the documents attached to a node, shown in its Docs tab.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-edges",
            "type": "component",
            "name": "Edge routes",
            "summary": "Write routes that create and delete typed edges between nodes.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-events",
            "type": "component",
            "name": "Event routes",
            "summary": "Read routes for the change logs: who changed what in the graph, and the owner-only sign-in and access event trail.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-graph",
            "type": "component",
            "name": "Graph routes",
            "summary": "Read routes over the graph as a whole: a bounded neighbourhood around a focus node, the contains tree, a compact outline, and the path between two nodes.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-handbook",
            "type": "component",
            "name": "Handbook site routes",
            "summary": "Serves the Atlas handbook as a static site under /docs, from a fixed file list, gated unless public docs are switched on.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-health",
            "type": "component",
            "name": "Health routes",
            "summary": "Health checks: unauthenticated liveness and readiness probes for infrastructure, and an authenticated health read with a few numbers.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-intents",
            "type": "component",
            "name": "Intent routes",
            "summary": "Routes that list intents and add, edit or delete the intent constraints attached to a node.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-landing",
            "type": "component",
            "name": "Product site routes",
            "summary": "Serves the product website's own files under /site/ with a strict content policy; mounted only when the newer landing page is selected.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-legacy",
            "type": "component",
            "name": "First-generation routes",
            "summary": "The first-generation read routes over the old component tree (tree, summary, priority, goals), kept for compatibility and answering 404 when the legacy model is switched off.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-me",
            "type": "component",
            "name": "Account routes",
            "summary": "The signed-in person's own account: setting a password, linking GitHub, and issuing, listing and revoking their agents' credentials.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-nodes",
            "type": "component",
            "name": "Node routes",
            "summary": "Routes that read, create, edit and delete a single node, set its status, and return its structure.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-retrieval",
            "type": "component",
            "name": "Retrieval routes",
            "summary": "Owner-only status and forced reindex for the semantic retrieval add-on; the routes exist only when the add-on is switched on.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-search",
            "type": "component",
            "name": "Search route",
            "summary": "Searches every node, world and work alike, by full text or hybrid with semantic retrieval, returning only what the caller may see.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-setup",
            "type": "component",
            "name": "Setup guide routes",
            "summary": "The first-run setup guide's API: where the guide stands, recording a topic's answers as a proposed ADR, and accepting it.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-stats",
            "type": "component",
            "name": "Personal statistics route",
            "summary": "Read-only numbers for the caller's own dashboard: their task flow and their agents' writes, never anyone else's.",
            "docs": [],
        },
        {
            "id": "atlas-api-route-workitems",
            "type": "component",
            "name": "Workitem routes",
            "summary": "Routes that list, create and update epics, stories and tasks, handle acceptance proposals, and serve the work vocabulary.",
            "docs": [],
        },
        {
            "id": "atlas-audit-trail",
            "type": "capability",
            "name": "Audit trail",
            "summary": "The record of who did what: sign-in and credential events, graph changes, and an opt-in log of agent reads, exportable as JSON Lines with retention for reads.",
            "abstract": "`atlas-audit-trail` owns **the record of who did what**: the sign-in and credential events in `auth_event`, the graph changes in `graph_event`, and the opt-in read audit in `audit_read` — reading them, exporting them (`GET /api/audit/export`, JSON Lines) and the retention of reads. Other parts only write their events into it: `atlas-identity` writes `auth_event`, and every graph write records its `graph_event`. The read audit, its retention and the export are described in `docs/nodes/atlas-audit-trail/read-audit.md`.",
            "docs": [
                {
                    "path": "docs/nodes/atlas-audit-trail/read-audit.md",
                    "kind": "architecture",
                    "title": "Read audit",
                    "format": "md",
                    "content": (
                        "# The read audit (STORY-oss-16)\n"
                        "\n"
                        "An opt-in record of agent reads, off unless configured. With it off, no read is recorded. The\n"
                        "credential events written by `atlas-identity` (`auth_event`) and the graph changes (`graph_event`)\n"
                        "are part of the same record and share the export below.\n"
                        "\n"
                        "## What is recorded\n"
                        "\n"
                        "With **`ATLAS_AUDIT_READS=1`**, an agent (service-token or IdP-token) read through a route that\n"
                        "calls `audit_log.record_read` writes one `audit_read` row: `at`, `user_id` (the person behind the\n"
                        "agent), `actor_user_id` / `actor_subject` (the agent), `token_id`, `tool`, `route`, the `node_ids`\n"
                        "actually served, and `request_id`. Never the query text or the content. Browser sessions are not\n"
                        "recorded. Off, the cost is one boolean check.\n"
                        "\n"
                        "It is recorded on the API side, because there the gate has resolved agent, credential and person,\n"
                        "and the route has already decided which ids the caller may see; it also catches token callers that\n"
                        "bypass the MCP. atlas-mcp adds two caller-asserted labels: `X-Atlas-Tool` (the tool name) and\n"
                        "`X-Request-ID` (one id per tool call). **Fail closed:** if the row cannot be written, the caller gets\n"
                        "a 500 and no content.\n"
                        "\n"
                        "Which routes record is in the code: `git grep record_read backend/routers`, and\n"
                        "`backend/TESTING.md` says what each audit verify proves.\n"
                        "\n"
                        "## Table and retention\n"
                        "\n"
                        "`audit_read` lives in `backend/models_audit.py` on the auth metadata, so the normal schema path\n"
                        "creates it and a reseed never drops it. **`ATLAS_AUDIT_RETENTION_DAYS`** with\n"
                        "**`atlas-admin audit purge`**, run from cron (e.g. `0 3 * * * docker exec atlas-api atlas-admin\n"
                        "audit purge`), deletes `audit_read` rows older than N days and nothing else; `graph_event` and\n"
                        "`auth_event` are never purged. Unset = keep forever.\n"
                        "\n"
                        "## Export\n"
                        "\n"
                        "`GET /api/audit/export?since=&until=&kinds=` streams JSON Lines (`application/x-ndjson`): one object\n"
                        "per line, oldest first across kinds, fixed keys per kind, keyset pages so nothing is held in memory.\n"
                        "Allowed: an owner session, or an `admin`-scoped token whose linked person is a live owner (re-read\n"
                        "on every call). Everyone else gets 403. No password, hash, token, session id or CSRF value is ever\n"
                        "in it.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-backend",
            "type": "system",
            "name": "Atlas backend",
            "summary": "The server side of Atlas: the FastAPI process, its Postgres database, and the graph, work and knowledge capabilities they implement.",
            "abstract": (
                "`atlas-backend` is a `system` node — the server side of Atlas named as one thing, so the rest of the tree can point at it without listing every child individually. It sits at level 2 of the `atlas` root beside `atlas-frontend`, `atlas-ingress`, `atlas-security`, `atlas-ops` and `atlas-mcp`, and it contains two kinds of child that are easy to confuse:\n"
                "\n"
                "**Process versus policy is the split, and it is the point of the grouping.** A change to the service or its container binds to `atlas-api` or `atlas-postgres`; a change to what the graph *means* binds to the capability. Both live here, and the pair is what \"backend\" names.\n"
                "\n"
                "**No work binds to this node directly, and that is correct.** Work rolls **up** (ADR 0028), so a `system` node exists to be pointed at rather than worked on — bindings sit on the children and show here by containment."
            ),
            "detail": (
                "See `docs/nodes/atlas-backend/architecture.md` (this node's Docs tab) for the diagram. Two intents are owned here and inherit down every child: `postgres-is-authoritative` — node documentation, the backlog, its acceptance criteria and its recorded results are all rows, and nothing in this repo can overwrite the database from a file — and `no-write-back-to-git` — the git export was built, verified and removed the same day, because it was neither a backup nor an audit trail.\n"
                "\n"
                "**Where the boundaries fall.** Deployment is not here: the staging stack and the verify suite belong to `atlas-ops`, its sibling. The path in — Caddy, the tunnel, the host and origin allow-lists — is `atlas-ingress`; who may pass once a request arrives is `atlas-security`; client-side rendering is `atlas-frontend`. What is left to this node is the server's own behaviour and its data model.\n"
                "\n"
                "**Read the children for detail, not this node.** Each carries its own Overview, and `atlas-api`'s is the one to open first — it holds the read path, the single write guard, and the fact that **boot is a READ**: nothing at startup opens a file.\n"
                "\n"
                "**The shape is recent.** ADR 0030 (2026-08-12) made the level-2 words layer names that transplant to any project and put capability nodes beneath a layer only where volume justified one. `atlas-world-model` came out of that pass, split from `atlas-graph` once binding the backlog had made `atlas-graph` too broad a single bind target."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-backend/architecture.md",
                    "kind": "architecture",
                    "title": "architecture.md",
                    "format": "md",
                    "content": (
                        "# atlas-backend\n"
                        "\n"
                        "## What it is\n"
                        "\n"
                        "`atlas-backend` is a `system` node — the server half of Atlas named as one thing, at level 2 of\n"
                        "the `atlas` root beside `atlas-frontend`, `atlas-ingress`, `atlas-security`, `atlas-ops` and\n"
                        "`atlas-mcp` (ADR 0030). `contains` here is the product decomposition, not a deployment map: this\n"
                        "node holds two runnables and the capabilities, grouped under `atlas-backend-graph-work` and\n"
                        "`atlas-backend-knowledge`, and \"runs on the same box\" is a separate fact\n"
                        "recorded on `hosted_on`, not on this tree.\n"
                        "\n"
                        "Process vs. policy is the split this grouping exists to make: a change to the service or its\n"
                        "container binds to `atlas-api` or `atlas-postgres`; a change to what the graph *means* binds to\n"
                        "the capability underneath it. A change to who may reach any of this is `atlas-security`'s, not\n"
                        "this node's — see that node for the gate. The path a request takes to arrive here at all is\n"
                        "`atlas-ingress`'s.\n"
                        "\n"
                        "## How it works\n"
                        "\n"
                        "**One process, one database, no queue and no cache in front of either.** `atlas-api` is a single\n"
                        "FastAPI application; `session()` (SQLAlchemy) is the only way any route touches\n"
                        "`atlas-postgres`. The core has no message broker, no background worker and no scheduled job —\n"
                        "every derived value is recomputed synchronously, inside the request that changed its inputs. The\n"
                        "opt-in add-ons are the exception: semantic retrieval runs one indexing thread inside atlas-api, and\n"
                        "the opt-in `atlas-backup` container runs its own backup loop.\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart LR\n"
                        "  B[\"browser session<br/>(cookie)\"] --> GW\n"
                        "  T[\"Bearer token<br/>(agent / script / MCP)\"] --> GW\n"
                        "  GW[\"AuthGate (ASGI middleware)<br/>atlas-security\"] -->|principal set| R[\"FastAPI routes<br/>routers/*.py\"]\n"
                        "  R --> WG[\"guard_write<br/>(mutating routes only)\"]\n"
                        "  WG --> R\n"
                        "  R <--> PG[(\"atlas-postgres\")]\n"
                        "  R -->|status/edge write| RD[\"attention.recompute_derived<br/>(same transaction)\"]\n"
                        "  RD --> PG\n"
                        "```\n"
                        "\n"
                        "**Boot builds the schema, then seeds what a fresh database needs.** `main.startup()` runs the\n"
                        "schema steps — `create_all` for each model base, the additive `_reconcile_graph_schema()`, and the\n"
                        "full-text indexes — and then ensures the `node_type` rows (`backend/node_types.py`). It touches no\n"
                        "file. The three importers that used to reconcile the graph from markdown and a world manifest at\n"
                        "every restart are deleted (TASK-1058/1059); a row this system did not put there does not disappear\n"
                        "at the next deploy, and editing a file changes nothing because nothing reads one.\n"
                        "\n"
                        "**The schema half of boot can also be run on its own** (STORY-oss-09):\n"
                        "`docker exec atlas-api atlas-migrate upgrade` (from a checkout: `cd backend && python\n"
                        "migrate_cli.py upgrade`). It calls the same schema functions startup calls, in the same order,\n"
                        "writes no rows, and is idempotent. It needs the same environment the API boots with, because it\n"
                        "imports `main`. `verify_upgrade_command.py` pins its steps to startup's, so a schema step added to\n"
                        "startup and not to the command turns CI red. There is no Alembic, by decision: schema changes stay\n"
                        "new tables or new nullable columns.\n"
                        "\n"
                        "**Secrets are read through one function.** `settings.read_secret(NAME)` prefers `NAME_FILE` (a\n"
                        "path to a file holding the value), falls back to plain `NAME` in the environment, and with\n"
                        "`ATLAS_REQUIRE_SECRET_FILES=1` refuses the plain variable. A required secret that is set but blank\n"
                        "(`NAME=` or whitespace) is an error that names the variable; it never silently selects a default\n"
                        "(TASK-1348). That is what stops a misconfigured deploy from reaching the wrong database: an empty\n"
                        "`ATLAS_DB_URL` fails instead of falling back to the local one. Unset keeps today's default.\n"
                        "\n"
                        "**Derivation is pulled forward to write time, not computed on read.** `attention.recompute_derived`\n"
                        "runs in the same unit of work as any write that changes status or edges — the workitem PATCH\n"
                        "route, the status route, `create_edge`/`delete_edge` — and caches `attention`, `completion` and\n"
                        "`attention_count` onto the `node` row. A read (`/api/graph`, `/api/graph/tree`) serves those\n"
                        "cached columns rather than re-deriving the whole tree per request; `attention.work_load` is the one\n"
                        "exception, computed live on every read because it is a join over the `works_on` bridge edge rather\n"
                        "than a `contains`-subtree rollup, and cheap enough at this graph's scale to skip caching.\n"
                        "\n"
                        "**Prose is split across three tables, deliberately**, because a single `body` column that a routine\n"
                        "write replaces wholesale is how three specs got destroyed recording their own outcomes\n"
                        "(TASK-1051/1053/1055, 3,291/2,513/2,170 characters lost, recovered only because the markdown still\n"
                        "existed at the time). `node.body` is the spec, `workitem_result` is the `## Result`/`## Outcome`\n"
                        "section by section-index position (`models_graph.reassemble` puts it back at the right slot),\n"
                        "`acceptance_criterion` is one row per criterion by position. `PATCH /api/workitem` writes `result`\n"
                        "as its own field and never touches `body` for it. Full-text search (`ensure_search_indexes`, GIN)\n"
                        "covers all three sources independently — dropping the third silently drops 221 sections' worth of\n"
                        "content from what full-text search can find.\n"
                        "\n"
                        "**Node documentation lives the same way.** `node.abstract`/`node.detail` are the two bounded,\n"
                        "curated prose tiers a panel actually renders (ADR 0019 context economy); `node_doc` holds the\n"
                        "heavier documents — this file is one such row, addressed by `(node_id, path)`, and is only\n"
                        "*served* once the node's `attributes.docs[]` allowlist names its path (ADR 0029/0020 — see\n"
                        "`atlas-docs` and `panel-docs` for the read side of that split).\n"
                        "\n"
                        "**Every mutating route shares one write guard and, since 2026-08-15, one scope check.**\n"
                        "`auth.guard_write` is the only place \"may this request change data\" is answered — CSRF for a\n"
                        "cookie session, and for every non-session principal (a Bearer token, or the dormant `grace`\n"
                        "bypass) a lookup in `WRITE_SCOPE_ROUTES` keyed on `(method, route template)`. A route with no\n"
                        "entry there is refused, not allowed, by construction. See `atlas-security` for the mechanism and\n"
                        "its history — it used to be a real gap here (a `--scopes read` token could `DELETE\n"
                        "/api/node/{id}`) and no longer is, as of TASK-1086.\n"
                        "\n"
                        "**No work binds to this node itself** — this node exists to be pointed at by containment, not by\n"
                        "`works_on`; work rolls up (ADR 0028) from the runnables and capabilities it contains, named above —\n"
                        "count them from `/api/graph` rather than trusting a number written here, since this decomposition\n"
                        "has already changed shape once (`atlas-world-model` split out of `atlas-graph` when binding load\n"
                        "made that node's count too high).\n"
                        "\n"
                        "## What will bite someone\n"
                        "\n"
                        "- **Postgres is persistent; `create_all` cannot add a column to a populated table.** Schema\n"
                        "  changes go through `_reconcile_graph_schema()` (in `main.py`), which is additive only (new\n"
                        "  columns, missing indexes) — a type change or a backfill is still a by-hand migration, and a\n"
                        "  change that passes against a fresh test database can still fail on deploy against the real one.\n"
                        "  Verify anything schema-shaped on staging Postgres before merging.\n"
                        "- **`node.origin`/`edge.origin` are provenance only and are not uniform.** Every `node` row is\n"
                        "  `atlas`-origin now, but `edge` is a mix of `git` and `atlas` — a comment or a migration that\n"
                        "  assumes uniformity across both tables is wrong.\n"
                        "- **There is no queue, so there is no retry.** A write that fails mid-transaction fails the whole\n"
                        "  request; there is nothing standing behind `atlas-api` waiting to pick it back up. Anything\n"
                        "  needing eventual consistency has to be built into the caller, not assumed here.\n"
                        "- **A blank secret is now a boot failure, not a default.** Before TASK-1348 a deploy that set\n"
                        "  `ATLAS_DB_URL=` silently used the local database; now it refuses to start with a message naming\n"
                        "  the variable. If a deploy suddenly will not boot after an env change, look for an empty value.\n"
                        "- **`AGENTS.md`/`CLAUDE.md` line counts for this system's files go stale fast** — this file\n"
                        "  deliberately does not repeat any of them. If a fact here depends on a file's size, re-measure it\n"
                        "  rather than trusting a number written on a different day.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-backend-graph-work",
            "type": "component",
            "name": "Graph & work",
            "summary": "Groups the capabilities that store and give meaning to the graph and the work in it: the graph engine, the world model, the workitem store and the attention rollups.",
            "docs": [],
        },
        {
            "id": "atlas-backend-knowledge",
            "type": "component",
            "name": "Knowledge",
            "summary": "Groups the capabilities that hold and find Atlas's knowledge: node documentation, the ADR registry, and context & search, which holds full-text search and the opt-in semantic retrieval.",
            "docs": [],
        },
        {
            "id": "atlas-backup",
            "type": "service",
            "name": "atlas-backup",
            "summary": "The opt-in backup service: verified pg_dump backups of the Atlas database, taken on a crunch policy or a routine schedule, with checksums, retention and restore.",
            "docs": [],
        },
        {
            "id": "atlas-board-core-api",
            "type": "component",
            "name": "API client and sync",
            "summary": "Board v2's single path to the API (fetch, writes, CSRF, login redirect on a lost session) and the BroadcastChannel mirror that keeps pop-out windows in step.",
            "docs": [],
        },
        {
            "id": "atlas-board-core-graph-model",
            "type": "component",
            "name": "Graph model",
            "summary": "Pure functions over the /api/graph payload: roots, steps out from a focus, which world node work belongs to, and what a node's work board contains.",
            "docs": [],
        },
        {
            "id": "atlas-board-core-prefs",
            "type": "component",
            "name": "Preferences",
            "summary": "Board v2's per-viewer settings in browser storage under their own namespace, never throwing, sharing only theme and text size with the legacy board.",
            "docs": [],
        },
        {
            "id": "atlas-board-core-state",
            "type": "component",
            "name": "State and routing",
            "summary": "Board v2's one immutable store that panes subscribe to, and the router that mirrors it into the URL so ?node= deep links work on both boards.",
            "docs": [],
        },
        {
            "id": "atlas-board-core-ui",
            "type": "component",
            "name": "UI kit",
            "summary": "Board v2's shared drawing pieces: element helpers and type, status and attention marks, markdown and Mermaid rendering, toasts, and the resizable, collapsible panes.",
            "docs": [],
        },
        {
            "id": "atlas-board-foundations",
            "type": "component",
            "name": "Foundations",
            "summary": "Board v2's DOM-free core and shared UI helpers: the store, URL routing, API client, window sync, preferences and graph model that every view builds on.",
            "docs": [],
        },
        {
            "id": "atlas-board-shell",
            "type": "capability",
            "name": "Board shell",
            "summary": "The legacy board's host page and everything around the map and panel: top chrome and view tabs, filters, search, filing, Settings, view state and deep links.",
            "abstract": (
                "`atlas-board-shell` is the **host page** of the Atlas board — `frontend/board.html` — served at `/board/legacy` by `routers/assets.py`'s `board_legacy()` route, frozen since board v2 took `/board` (ADR 0043, 2026-10-22). **`/` is not the board any more** (ADR 0035, 2026-08-25): the bare domain is a public informational landing page, `frontend/landing.html`, whose handler redirects a signed-in browser to `/board` (board v2) and serves this page directly only to a machine principal presenting a Bearer token. It is the shell, not the views: `atlas-map` owns the D3 graph and `atlas-panel` owns the detail panel. This node owns everything around and between them.\n"
                "\n"
                "What that is, concretely:\n"
                "\n"
                "- **Top chrome** — the World/Work/Mine view switch, the find-a-node type-ahead (ranked, capped at 10, over `state.NODE_INDEX`; `/` or Ctrl+K focuses it), the status and node-type filter menus, the `+ File work` button that opens `compose.js`'s overlay, the `A-`/`A+` text-size control that steps `--ui-scale` through five stops, the theme toggle, and the account slot `api.js` paints from `GET /api/me`.\n"
                "\n"
                "  **Only two of the three views are maps.** World and Work are D3 tidy-trees off one `buildTree`; **Mine is a list**, and `switchTab(\"mine\")` therefore returns early — before `buildTree`, `selectNode` and `renderFilterHint`, each of which operates on a hierarchy Mine does not have. Anything map-shaped added after that early return simply never runs for the third view.\n"
                "- **The Settings overlay** (`settings.js`, its own module) — reached from the profile menu, owning its own DOM and built entirely with `createElement`/`textContent` because every value in it is server-supplied.\n"
                "\n"
                "  **It is a left rail of named groups, not one scrolling page** (2026-08-25). Four, top to bottom: **Account** (the profile pane, the change-password link, the privacy notice); **Agent credentials**, which every account sees because managing your own credentials administers nobody; and the owner-only **Access & sharing** grid (accounts down, projects across, agents nested under the person they belong to) and **Audit log**, a paged reader over `GET /api/auth-events` — logins, lockouts and access changes, newest first. The two owner-only groups are decided from `me.role`, so a member's rail simply has two items rather than two items and two refusals, and a member never issues a request that 403s.\n"
                "\n"
                "  **Each pane fetches on first reveal, not on open.** Opening Settings used to issue the access, audit and credential requests whether or not you looked at them; a `loaded` flag per pane now defers each one to its first click and keeps the built DOM, so returning to a pane costs nothing. Measured on live 2026-08-25: zero API requests on open, one on first reveal, none on return.\n"
                "\n"
                "  The audit reader is deliberately server-paged: `auth_event` grows with every login, so collecting it the way the access grid collects accounts would mean an unbounded fetch loop each time Settings opens. It states the true total and the exact range beside the page, and is never a silent prefix standing in for the whole. Note it reads **`auth_event`** — identity only. Graph changes live in `graph_event` and are read through `GET /api/events` and the panel's History tab.\n"
                "\n"
                "  An overlay rather than a route — a `/settings` page would need its own boot and its own app bar, and would drop the map state the user came from.\n"
                "- **The graph/detail split** — the panel resizer (drag, arrow keys, double-click to reset; width on desktop, height on the narrow-screen sheet) and the overview strip's disclosure.\n"
                "- **View state and deep links** — a set of preferences under `atlas.*` through `remember`/`recall` (theme, tab, detail, panel width, UI scale, overview state, the status and type filters, the open Settings pane, which people are expanded in the access grid …), and `?tab=&node=` mirrored into the query string with `replaceState` so the address bar *is* the shareable link. A pre-paint classic script applies theme, `--ui-scale` and the overview state before first paint so nothing flashes.\n"
                "\n"
                "  **Filtering is about what is DRAWN, not what exists.** The status and type axes prune a *copy* of whichever tree is on screen and never touch `workData`/`byId`/`NODE_INDEX`, so search, a deep link and the detail panel still reach a node the map is hiding, and asking for one by name reveals it. Neither axis is a filter the API ever sees.\n"
                "- **The boot fetch and the tree projections** — `/api/graph/tree` plus a rootless `/api/graph`, then `projectWorld`, `groupWorkByProject` and `projectWorkByObject`, the age filter's `pruneTree`, and `refreshGraph` after a write.\n"
                "\n"
                "**It cannot be imported from.** An inline `<script type=\"module\">` has no URL, so `map.js` and `panel.js` reach back into it only through the `hooks` objects it fills in at boot (`renderPanel`/`updateUrl`/`revealFiltered`, `navigateTo`/`refreshGraph`). Shared *data* goes the other way, through `state.js`: one exported object mutated by property assignment (`state.TREE = x`), because ES module bindings are live but not assignable across modules.\n"
                "\n"
                "**The public landing page is this node's subject too, and it is deliberately nothing like the board.** That is the default (`ATLAS_LANDING` unset or `v1`); with `ATLAS_LANDING=v2`, `/` serves the product site `frontend/landing-v2/index.html` instead (see `atlas-api-route-landing`, ADR 0049). `frontend/landing.html` carries no JavaScript, no module, no vendored library and no request to any other origin — its CSS is inline and its favicon is a data URI. It is served by a plain `FileResponse` and is **not** an `/assets/*` file, so the allowlist below does not govern it. The same three-way theming rule applies (light on bare `:root`, dark redefining tokens under `prefers-color-scheme`), with one deliberate exception: the orange call-to-action holds one value across both themes so its label contrast (5.43:1 on white) cannot drift with the palette.\n"
                "\n"
                "**The login screens are this node's subject but not its code**: they are `backend/auth.py`'s inline pages — see `detail`."
            ),
            "detail": (
                "See `docs/nodes/atlas-board-shell/architecture.md` (this node's Docs tab) for the module graph. The board is native ES modules with no build step, each one a file beside `board.html`.\n"
                "\n"
                "**`ASSET_FILES` in `backend/routers/assets.py` is the register of which those are** — read it there rather than from a list written down here, which is how this paragraph came to omit `mine.js` and `compose.js` for a week. That allowlist replaced the `/static` mount, which had been serving a byte-identical *unauthenticated* copy of the board (a mount is an ASGI sub-app route dependencies cannot see, so it was deleted rather than narrowed). `/assets/*` sits behind AuthGate, and **a module missing from `ASSET_FILES` 404s** — the way a newly added one fails. `frontend/landing.html` is deliberately outside all of this: it is served by a plain `FileResponse` at `/`, is not an asset, and loads nothing.\n"
                "\n"
                "**A deploy no longer needs a hard reload** (fixed at the origin 2026-08-18, `dfb615f`). `_asset_response` sends `Cache-Control: private, no-cache` (`routers/assets.ASSET_CACHE_CONTROL`), and `no-cache` means *must revalidate*, not *must not store* — `FileResponse` was already sending ETag/Last-Modified, so revalidation costs a 304 and a deploy lands in an already-open tab. Two things that header does not do, both of which bit on the day of the fix:\n"
                "\n"
                "When a browser and the container genuinely disagree, `docker exec atlas-api grep -c <symbol> frontend/<module>.js` still settles it.\n"
                "\n"
                "**The login screens are `backend/auth.py`'s inline pages.** Live `/auth/login` and `/auth/change-password` return `auth.py`'s inline `_PAGE`. The four orphaned HTML files — `frontend/login.html`, `change-password.html` and `logout.html` (authored under TASK-969, never routed) and `frontend/index.html` (the superseded v0 tree view) — were removed on 2026-11-10; git history keeps them.\n"
                "\n"
                "**Three things in the overlay modules that look like taste and are not.** A secret shown once is rendered from the response and never from state, never written to `localStorage` and never put in the URL — dismissing the panel destroys it, which the panel says before it can be dismissed. A destructive confirmation is built **in place**, never with `confirm()`: a browser modal blocks the automation used to verify this board, and it cannot say what this particular action will and will not do. And dismissing an overlay by its scrim goes through `util.js`'s `dismissOnScrim`, which requires the mousedown *and* the mouseup to land on the scrim — `click` is dispatched at the nearest common ancestor of the two, so the obvious `ev.target === root` test reads a text-selection drag that merely ended on the backdrop as a dismiss, and threw the form away mid-typing. That shipped in two copied blocks and is one helper now.\n"
                "\n"
                "**House style is load-bearing.** ES5 idiom (`var`, `function`, no arrows or template literals); element ids reached by `getElementById` from across the modules are a contract — re-parent them, never rename them; every `font-size` must be `calc(<N>px * var(--ui-scale))` or it opts out of the text-size control silently. Mobile breaks at `max-width: 760px`. Note that the Settings panes now build on first reveal, so `accessHost`, `auditHost` and `credentialHost` do not exist in the DOM until their group is opened — a lazier contract than the always-present ids around them."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-board-shell/architecture.md",
                    "kind": "architecture",
                    "title": "Board module graph",
                    "format": "mermaid",
                    "content": (
                        "# atlas-board-shell — architecture\n"
                        "\n"
                        "`docs[]` entry: `{kind: architecture, title: \"Board module graph\", path: \"docs/nodes/atlas-board-shell/architecture.md\", format: mermaid}`\n"
                        "\n"
                        "The board is native ES modules with no build step. `board.html` is an inline `<script type=\"module\">`,\n"
                        "which has **no URL** — so nothing can `import` from it. That one fact shapes every arrow below.\n"
                        "\n"
                        "## Imports go down; callbacks go back up through `hooks`\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart TD\n"
                        "    Shell[\"board.html<br/>inline module — no URL\"]\n"
                        "    Map[\"map.js\"]\n"
                        "    Panel[\"panel.js\"]\n"
                        "    Api[\"api.js\"]\n"
                        "    State[\"state.js\"]\n"
                        "    Glyph[\"glyph.js\"]\n"
                        "    Util[\"util.js\"]\n"
                        "    Mine[\"mine.js\"]\n"
                        "    Settings[\"settings.js\"]\n"
                        "    Compose[\"compose.js\"]\n"
                        "\n"
                        "    Shell -->|import| Map\n"
                        "    Shell -->|import| Panel\n"
                        "    Shell -->|import| Api\n"
                        "    Shell -->|import| State\n"
                        "    Shell -->|import| Glyph\n"
                        "    Shell -->|import| Util\n"
                        "    Shell -->|import| Mine\n"
                        "    Shell -->|\"import() on demand\"| Settings\n"
                        "    Shell -->|\"import() on demand\"| Compose\n"
                        "\n"
                        "    Panel -->|\"import selectNode\"| Map\n"
                        "    Panel --> Api\n"
                        "    Panel --> State\n"
                        "    Panel --> Glyph\n"
                        "    Panel --> Util\n"
                        "    Map --> State\n"
                        "    Map --> Glyph\n"
                        "    Map --> Util\n"
                        "    State --> Util\n"
                        "    Glyph --> Util\n"
                        "    Mine --> Api\n"
                        "    Mine --> State\n"
                        "    Mine --> Util\n"
                        "    Settings --> Api\n"
                        "    Settings --> Util\n"
                        "    Compose --> Api\n"
                        "    Compose --> State\n"
                        "\n"
                        "    Map -.->|\"hooks.renderPanel<br/>hooks.updateUrl<br/>hooks.revealFiltered\"| Shell\n"
                        "    Panel -.->|\"hooks.navigateTo<br/>hooks.refreshGraph\"| Shell\n"
                        "    Mine -.->|\"hooks.openNode\"| Shell\n"
                        "    Compose -.->|\"hooks.afterCreate\"| Shell\n"
                        "```\n"
                        "\n"
                        "Three modules arrived after this diagram was first drawn and were missing from it until 2026-08-24 — which is why `/api/health` now reports `undocumented_assets` rather than trusting anyone to remember. `mine.js` owns the Mine list; `settings.js` owns the Settings overlay; `compose.js` owns the file-work form. The last two are imported **lazily**, by `import()` at the moment they are first opened, so neither costs anything on a board nobody opens them from.\n"
                        "\n"
                        "Solid = a real `import`. Dotted = a plain object the shell fills in once at boot by property\n"
                        "assignment (`mapHooks.renderPanel = renderPanel;`). The five hook targets are function\n"
                        "*declarations*, so they are hoisted and already callable at the top of the script.\n"
                        "\n"
                        "`panel.js` imports `selectNode` from `map.js` directly rather than through a hook, because a\n"
                        "sub-node or Links row only ever points at a node in the **same** tree. Crossing trees is\n"
                        "`navigateTo`, and that is the shell's decision — hence the hook.\n"
                        "\n"
                        "## Shared state is one object, mutated by property\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart LR\n"
                        "    S[\"state.js<br/>export var state = one object\"]\n"
                        "    A[\"board.html\"]\n"
                        "    B[\"map.js\"]\n"
                        "    C[\"panel.js\"]\n"
                        "\n"
                        "    A -->|\"state.TREE = x\"| S\n"
                        "    B -->|\"reads state.FILTER\"| S\n"
                        "    C -->|\"reads state.NODE_INDEX\"| S\n"
                        "```\n"
                        "\n"
                        "ES module bindings are live but **not assignable** across modules: `import { TREE }` then `TREE = x`\n"
                        "either no-ops or throws. Property assignment on an imported *object* is always allowed, so `state.js`\n"
                        "exports one object instead of ten setter functions. Follow the idiom — never shadow a `state.*` name\n"
                        "with a local `var`.\n"
                        "\n"
                        "`map.js` goes further and exports nothing mutable at all: its D3 bookkeeping (`d3root`, `d3ById`,\n"
                        "`d3selected`, `svgSel`, `zoomBehavior`) is reassigned wholesale, so it stays private and the module\n"
                        "exports small **functions** — `hasNode()`, `getSelectedId()`, `clearSelection()`,\n"
                        "`currentTransform()`, `restoreTransform()`, `collapseUnboundBucket()`.\n"
                        "\n"
                        "## Serving\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart LR\n"
                        "    Browser\n"
                        "    Board[\"GET /board/legacy<br/>FileResponse(board.html)\"]\n"
                        "    Root[\"GET / — one route, three answers<br/>no credential: landing.html<br/>signed-in browser: 302 to /board<br/>Bearer principal: board.html\"]\n"
                        "    Assets[\"GET /assets/&#123;name&#125;<br/>ASSET_FILES allowlist\"]\n"
                        "    Gate[\"AuthGate\"]\n"
                        "\n"
                        "    Browser --> Gate\n"
                        "    Gate --> Board\n"
                        "    Gate --> Root\n"
                        "    Gate --> Assets\n"
                        "    Assets --> V[\"vendor: d3 · marked · mermaid\"]\n"
                        "    Assets --> M[\"board.css + every module\"]\n"
                        "```\n"
                        "\n"
                        "**`/` reaches this shell only for a Bearer principal** (ADR 0035); `/board` is board v2 (ADR 0043). It is the only entry in\n"
                        "`auth.PUBLIC_ROUTES` of the three above — and even then it is not one fixed response: the gate still\n"
                        "sets the principal on a public route when a credential *was* presented, and the handler branches on\n"
                        "it. `/board/legacy` itself is gated exactly as `/board` is.\n"
                        "\n"
                        "The allowlist is not a convenience — it **replaced** `app.mount(\"/static\", StaticFiles(...))`, which\n"
                        "route-level dependencies could not see and which was therefore serving a byte-identical\n"
                        "*unauthenticated* copy of this board. A module missing from `ASSET_FILES` 404s without touching the\n"
                        "filesystem. Nothing under `/assets/` is public; the login page needs no asset but the favicon, which\n"
                        "is why it can stay that way. Every asset response carries `Cache-Control: private, no-cache`, so an\n"
                        "already-open tab revalidates after a deploy instead of running last week's modules.\n"
                        "\n"
                        "## No module may encode the graph\n"
                        "\n"
                        "A type vocabulary is defensible presentation config; a node id is not. The boot fetch names\n"
                        "`families=object,bridge` and `edges=contains,contributes_to,hosted_on,uses,for` — both vocabularies.\n"
                        "It names **no node**: `WORLD_ROOT_ID` was deleted when it turned out to hide a root that had no\n"
                        "lateral edge. If a module needs to name a node to work, that is a missing endpoint.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-board-views",
            "type": "component",
            "name": "Board views",
            "summary": "The panes and overlays a person uses on board v2: graph, work board, node panel, dashboard, search, settings and access administration.",
            "docs": [],
        },
        {
            "id": "atlas-ci-images",
            "type": "component",
            "name": "Images and pins",
            "summary": "The CI that builds, checks and on a release tag publishes the install's container images, and the gate requiring every image and action reference to be pinned.",
            "docs": [],
        },
        {
            "id": "atlas-ci-pipelines",
            "type": "component",
            "name": "CI pipelines",
            "summary": "The GitHub Actions workflows that run on Atlas's own repository: the verify suite, install smoke, security scans, image builds and pins, and the scrub and public-export checks.",
            "docs": [],
        },
        {
            "id": "atlas-ci-scrub",
            "type": "component",
            "name": "Scrub and public export",
            "summary": "The CI that scans tracked files for personal references against a secret denylist, and rehearses the single-commit export of the tree to the public repository.",
            "docs": [],
        },
        {
            "id": "atlas-ci-security",
            "type": "component",
            "name": "Security scans",
            "summary": "The security CI: static checks on every pull request (runtime end-of-life, hashed locks, Semgrep), SBOM and vulnerability scans of every image, and ZAP DAST against an ephemeral install.",
            "docs": [],
        },
        {
            "id": "atlas-ci-smoke",
            "type": "component",
            "name": "Install smoke",
            "summary": "The CI that installs Atlas on a clean runner the way a stranger would, then signs in, connects an agent and checks each promise of the install, one step per item.",
            "docs": [],
        },
        {
            "id": "atlas-ci-verify",
            "type": "component",
            "name": "Verify suite",
            "summary": "The backend verify suite: verify_all.py runs every verify_*.py against its own disposable Postgres, with no skip path, locally and in CI on every push and pull request.",
            "docs": [],
        },
        {
            "id": "atlas-cli",
            "type": "component",
            "name": "Admin CLI",
            "summary": "atlas-admin: the operator's command line, run inside the API container, for accounts, agents, tokens, sessions, roles, audit, guardrails, CI/CD, backups and auth tables.",
            "docs": [],
        },
        {
            "id": "atlas-cli-agents",
            "type": "component",
            "name": "Agent accounts",
            "summary": "atlas-admin bot: create machine accounts with no usable password and link each one to the person accountable for it.",
            "docs": [],
        },
        {
            "id": "atlas-cli-audit",
            "type": "component",
            "name": "Read audit",
            "summary": "atlas-admin audit purge: delete read-audit rows older than the configured retention period.",
            "docs": [],
        },
        {
            "id": "atlas-cli-backups",
            "type": "component",
            "name": "Backups",
            "summary": "atlas-admin backup: take verified logical backups of the Atlas database, restore one over the live database, and roll back to the safety copy a restore left.",
            "docs": [],
        },
        {
            "id": "atlas-cli-cicd",
            "type": "component",
            "name": "CI/CD setup",
            "summary": "atlas-admin cicd generate: write a first CI/CD pipeline for GitHub Actions, GitLab CI or Azure Pipelines from the guardrails matrix or the setup guide's answers.",
            "docs": [],
        },
        {
            "id": "atlas-cli-guardrails",
            "type": "component",
            "name": "Guardrails",
            "summary": "atlas-admin guardrails: validate, apply, show and export the per-environment agent guardrails matrix, stored as data on the system node.",
            "docs": [],
        },
        {
            "id": "atlas-cli-init-db",
            "type": "component",
            "name": "Auth table init",
            "summary": "atlas-admin init-db: create any missing auth tables, idempotently, so the first owner can be created before the app has booted on the database.",
            "docs": [],
        },
        {
            "id": "atlas-cli-rbac",
            "type": "component",
            "name": "Project roles",
            "summary": "atlas-admin rbac plan: a read-only comparison of each account's effective permissions today versus under project-scoped roles.",
            "docs": [],
        },
        {
            "id": "atlas-cli-sessions",
            "type": "component",
            "name": "Browser sessions",
            "summary": "atlas-admin session mint: create a short-lived browser session for an account without a password, allowed only where session minting is explicitly enabled.",
            "docs": [],
        },
        {
            "id": "atlas-cli-tokens",
            "type": "component",
            "name": "Service tokens",
            "summary": "atlas-admin token: mint scoped service tokens for agents, shown once or written only to a file, list them without digests, and revoke them.",
            "docs": [],
        },
        {
            "id": "atlas-cli-users",
            "type": "component",
            "name": "User accounts",
            "summary": "atlas-admin user: create, link to an identity provider, reset, disable, enable, list and show human accounts; every action is logged as an auth event.",
            "docs": [],
        },
        {
            "id": "atlas-client-claude-code",
            "type": "component",
            "name": "Claude Code pack",
            "summary": "The Claude Code client pack: MCP config templates, a headers helper that reads the agent token from a file, and a plugin with the atlas-retrieval, file-task and setup-guide skills.",
            "docs": [],
        },
        {
            "id": "atlas-client-packs",
            "type": "component",
            "name": "Client packs",
            "summary": "Ready-made bundles that connect an AI agent client to an Atlas install with the user's own token: MCP server templates, a token header helper and Atlas skills.",
            "docs": [],
        },
        {
            "id": "atlas-context",
            "type": "capability",
            "name": "Context & search",
            "summary": "The one-call answer to \"tell me about X\": the matching nodes, their neighbours, what depends on them, the intents that apply, related ADRs and the docs to read next, as bounded pointers.",
            "abstract": (
                "`atlas-context` is Atlas' answer to an open question — \"tell me about X\", \"what touches Y\", asked in the agent's own words or in Swedish. One call, `GET /api/context?about=…` or the MCP tool `atlas_context`, returns a **package of pointers** in six parts:\n"
                "\n"
                "- **seeds**: the nodes that best match X, exactly what a search returns;\n"
                "- **neighbourhood**: what sits one hop around them — parents and children (`contains`), what they require and what requires them, their bindings — each with how it relates;\n"
                "- **impact**: what would be affected if a seed changed: everything that `requires` it, transitively, to a bounded depth;\n"
                "- **intents**: the constraints and principles that apply to the seeds, their own and inherited down `contains`;\n"
                "- **decisions**: the ADRs that bear on X, from the ADR registry;\n"
                "- **docs**: the documents to read next, the ones the search matched first.\n"
                "\n"
                "Every entry is an id with a type, a name and a one-line summary; the text is one `atlas_get`, `atlas_doc_read` or `atlas_adr_get` away. That is the `context-minimal` outcome made concrete: the fewest tokens that let an agent act correctly, one task-scoped read instead of exploring. **Every part is capped by a parameter and says `truncated` when its cap cut it**, and the response reports its own size in `bytes` (about 7–10 KB with the defaults on this instance).\n"
                "\n"
                "**It is core: it needs no add-on.** Seeds come from the search route itself. With the semantic retrieval add-on (`atlas-retrieval`) on, seeds are hybrid (semantic + full text), so a question in someone's own words finds its starting points; without it, they are full text and the response says `degraded`. Everything else is graph walks and registry reads over what Atlas already holds.\n"
                "\n"
                "**What a caller may not see is absent from every part**, filtered inside each statement, and a dependency chain never runs through a hidden node, because its far end would disclose that something sits in between (`atlas-visibility`).\n"
                "\n"
                "Its answer is only as good as the edges it walks: the impact set reads `requires`, mapped from the prose by STORY-atlas-context-graph-mapping, and decisions are ranked from the ADR registry's text (ADR 0045); no edge links an ADR to the nodes it decides about. Code: `backend/routers/context.py`."
            ),
            "detail": (
                "**Seeds are the search route's answer, not a copy of it.** `context` calls `routers/search.search_nodes` with the same visibility filter, families and mode, so a seed set can never drift from what `atlas_node_search` would show for the same question.\n"
                "\n"
                "**The impact walk filters at every step.** A recursive CTE over `requires`, inward, with the visible set as a predicate of each step and the depth as a parameter (default 2, max 5). Filtering only the end points would let a visible node at depth 2 reveal a hidden node at depth 1.\n"
                "\n"
                "**Decisions are ranked on the question's words and on the seeds' ids and names.** An ADR is written in one language, usually not the question's, but it names the components it decides about; a question in Swedish therefore still finds English ADRs through its seeds. The ranking OR's the terms (an ADR rarely holds every word of a question). A known weakness, left to STORY-atlas-context-measure: an ADR that names many components (the world decomposition, DS019) ranks for many questions.\n"
                "\n"
                "**A neighbour is listed once**, through its best-ranked seed, and a seed is never repeated as a neighbour.\n"
                "\n"
                "**Proven, not asserted** (`backend/verify_context.py`, plain Postgres, add-on off): every part and refusal; deletion-equivalence for the whole package for a person and an agent principal, with a private project that carries the question's words, a parent intent, an ADR, a node a public node requires and the hidden middle of a public impact chain; and bytes bounded by the parameters on a wide fixture (12 KB with the defaults, 46 KB with every budget at its maximum)."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-context/architecture.md",
                    "kind": "architecture",
                    "title": "Context answer architecture",
                    "format": "mermaid",
                    "content": (
                        "# Context answer architecture\n"
                        "\n"
                        "One request, six bounded reads, one package of pointers. Nothing is stored; every part is computed\n"
                        "from the graph, the intents and the ADR registry at request time, with the caller's visible set.\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart TB\n"
                        "  Q[\"GET /api/context?about=X<br/>(MCP atlas_context)\"] --> S\n"
                        "  S[\"seeds = search_nodes(X, mode, families)<br/>hybrid if the retrieval add-on is on, else full text + degraded\"]\n"
                        "  S --> N[\"neighbourhood<br/>one hop: contains both ways, requires both ways, works_on / serves<br/>each neighbour once, via its best seed\"]\n"
                        "  S --> I[\"impact<br/>recursive CTE over requires, inward, depth ≤ impact_depth<br/>visible set applied at EVERY step\"]\n"
                        "  S --> T[\"intents<br/>_intents_for(seed, visible): own + inherited down contains\"]\n"
                        "  S --> D[\"decisions<br/>ADR registry, ranked on X's words OR the seeds' ids and names<br/>visible projects only\"]\n"
                        "  S --> F[\"docs<br/>served docs[] of the seeds; search's document hits first\"]\n"
                        "  N & I & T & D & F --> P[\"package: every part {items, truncated}, budgets, bytes\"]\n"
                        "```\n"
                        "\n"
                        "## Visibility, part by part\n"
                        "\n"
                        "| Part | Where the filter is |\n"
                        "|---|---|\n"
                        "| seeds | `/api/search`'s own query (and the vector statement, for hybrid) |\n"
                        "| neighbourhood | `x.other = ANY(:visible)` inside the walk |\n"
                        "| impact | the same predicate at each recursive step: a chain never passes through a hidden node |\n"
                        "| intents | `_intents_for(…, visible_ids)` drops inherited intents whose owner is hidden |\n"
                        "| decisions | `r.project_node_id = ANY(:visible)`, the ADR registry's rule |\n"
                        "| docs | `docs[]` of visible seeds only |\n"
                        "\n"
                        "The whole package is proven deletion-equivalent for a person and an agent principal in\n"
                        "`backend/verify_context.py` §C.\n"
                        "\n"
                        "## What it depends on\n"
                        "\n"
                        "- `atlas-graph`: the edges it walks. The impact set is only as good as the `requires` edges mapped\n"
                        "  from the prose (STORY-atlas-context-graph-mapping).\n"
                        "- `atlas-visibility`: the visible set every part filters by.\n"
                        "- `atlas-retrieval` (optional): hybrid seeds. Without it the answer is complete, with full-text seeds.\n"
                    ),
                },
                {
                    "path": "docs/nodes/atlas-context/configuration.md",
                    "kind": "integration",
                    "title": "Context answer parameters",
                    "format": "md",
                    "content": (
                        "# Context answer parameters\n"
                        "\n"
                        "There is no instance configuration: the context answer is core and always on. Everything a\n"
                        "caller can change is a request parameter, and every budget is bounded so one call is never an\n"
                        "export. A parameter outside its range is a 400 naming the field.\n"
                        "\n"
                        "| Parameter | Meaning | Default | Range |\n"
                        "|---|---|---|---|\n"
                        "| `about` | the question, in any words or language | (required) | non-empty |\n"
                        "| `mode` | how seeds are searched: `fulltext`, `semantic`, `hybrid` | `hybrid` | |\n"
                        "| `families` | node families for seeds and neighbours; `all` includes the backlog | `object,bridge` | |\n"
                        "| `seeds` | how many matching nodes to start from | 5 | 1–20 |\n"
                        "| `neighbours` | one-hop neighbours | 20 | 0–100 |\n"
                        "| `impact_depth` | how far \"what requires this\" is followed | 2 | 0–5 |\n"
                        "| `impact` | impact entries | 20 | 0–100 |\n"
                        "| `intents` | intent pointers | 20 | 0–100 |\n"
                        "| `intents_full` | `1` adds each intent's statement | 0 | 0–1 |\n"
                        "| `decisions` | ADR pointers | 5 | 0–20 |\n"
                        "| `docs` | document pointers | 10 | 0–50 |\n"
                        "\n"
                        "## The response\n"
                        "\n"
                        "`{about, mode, degraded?, seeds, neighbourhood, impact, intents, decisions, docs, budgets, bytes}`.\n"
                        "Each part is `{items, truncated}`; `truncated: true` means the budget cut it. `degraded` appears\n"
                        "only when the seeds' search could not use the semantic half (the add-on off or unavailable), and\n"
                        "then the seeds are full text.\n"
                        "\n"
                        "## Size\n"
                        "\n"
                        "With the defaults: about 7–10 KB on this instance's live graph (2026-10-26), 12 KB on the test\n"
                        "suite's wide fixture. With every budget at its maximum: 46 KB on that fixture.\n"
                    ),
                },
                {
                    "path": "docs/nodes/atlas-context/evaluation.md",
                    "kind": "design",
                    "title": "Evaluation: recall, bytes and impact (2026-10-26)",
                    "format": "md",
                    "content": (
                        "# Evaluation: recall, bytes and impact (2026-10-26)\n"
                        "\n"
                        "## 1. Search: which node knows about X?\n"
                        "\n"
                        "22 questions: 13 in Swedish and 7 in English in someone's own words, plus 2 exact identifiers (to\n"
                        "check hybrid costs nothing where full text already works). A hit is a target node among the first\n"
                        "five results (`/api/search`, `families=object,bridge`, `page_size=10`).\n"
                        "\n"
                        "| mode | recall@5 | MRR@10 | bytes (median / max) | latency (median) | degraded |\n"
                        "|---|---|---|---|---|---|\n"
                        "| `fulltext` (today's default) | **5/22** | 0.205 | 159 / 5,403 | 381 ms | 0 |\n"
                        "| `semantic` | **22/22** | 0.797 | 6,516 / 6,995 | 724 ms | 0 |\n"
                        "| `hybrid` | **21/22** | 0.748 | 6,512 / 6,991 | 717 ms | 0 |\n"
                        "\n"
                        "- Full text finds nothing for 17 of the 22 questions: its terms are ANDed, so a sentence returns\n"
                        "  an empty list (hence its tiny median size). It found the two identifiers and three short English\n"
                        "  phrases.\n"
                        "- Both semantic modes find the two identifiers at rank 1: nothing is lost where full text works.\n"
                        "- Hybrid's one miss, \"the TLS front door on the LAN\" (rank 6, semantic rank 1): full text matched\n"
                        "  noise for \"LAN\" and its ranks pulled the fusion down. Semantic alone is marginally better on this\n"
                        "  set; with 22 questions the difference is one question.\n"
                        "\n"
                        "## 2. The context answer\n"
                        "\n"
                        "The same 22 questions to `/api/context` (defaults: hybrid seeds, every budget at its default).\n"
                        "\n"
                        "| measure | result |\n"
                        "|---|---|\n"
                        "| a target among the 5 seeds | **21/22** |\n"
                        "| a target among seeds or neighbours | **22/22** |\n"
                        "| bytes (median / max) | 9,672 / 15,544 |\n"
                        "| latency (median) | 933 ms |\n"
                        "| degraded | 0 |\n"
                        "\n"
                        "## 3. Impact, before and after the edge mapping\n"
                        "\n"
                        "Truth: for the ten nodes with the most dependants on live, everything that `requires` them within\n"
                        "two hops, from live's own `requires` edges. Question: \"vad påverkas om <name> går ner\". Found: the\n"
                        "package's impact set, its `required_by` neighbours and its seeds. On the fork the truth is limited\n"
                        "to nodes that exist there.\n"
                        "\n"
                        "| graph | impact recall | impact items returned |\n"
                        "|---|---|---|\n"
                        "| before the mapping (fork: no `requires` edges) | 6/22 (27 %), by coincidence | 0 in every answer |\n"
                        "| after the mapping (live: 29 `requires` edges) | **35/35 (100 %)** | 2–10 per answer |\n"
                        "\n"
                        "The target node was a seed in all ten questions on both graphs: the failure before the mapping was\n"
                        "the missing edges, not finding the node.\n"
                        "\n"
                        "## 4. The evaluation set\n"
                        "\n"
                        "## Limits of this measurement\n"
                        "\n"
                        "- 22 questions, written by the agent that built the feature, with the target chosen from the\n"
                        "  outline before asking. A rank 1 here is a real result; the difference between `semantic` and\n"
                        "  `hybrid` (one question) is inside the noise.\n"
                        "- One principal (the sync token). Visibility is proven separately, by the deletion-equivalence\n"
                        "  oracles in `verify_retrieval.py` and `verify_context.py`.\n"
                        "- Decisions were not scored: ADRs have no `decides` edges yet, so there is no truth to score\n"
                        "  against. Observed: an ADR naming many components (DS019) ranks for many questions.\n"
                    ),
                },
                {
                    "path": "docs/nodes/atlas-context/operations.md",
                    "kind": "runbook",
                    "title": "Context answer operations",
                    "format": "md",
                    "content": (
                        "# Context answer operations\n"
                        "\n"
                        "## Using it\n"
                        "\n"
                        "Start an open question with `atlas_context(about=\"…\")`, then open only what it pointed at:\n"
                        "`atlas_get(id)` for a node (full, with its intents, before you change it), `atlas_doc_read(node_id,\n"
                        "path)` for a document, `atlas_adr_get(id)` for a decision. The `atlas-retrieval` skill gives this\n"
                        "order.\n"
                        "\n"
                        "## Reading the answer\n"
                        "\n"
                        "- **`degraded`**: the seeds are full text because the semantic half was not available. A question\n"
                        "  in someone's own words may then find little; say so rather than trust the miss.\n"
                        "- **`truncated`** on a part: its budget cut it. Raise that budget only (`neighbours=50`), not all.\n"
                        "- **An empty `impact`**: nothing in the graph `requires` the seeds. That is a statement about the\n"
                        "  mapped edges, not a guarantee; the prose may name dependencies the graph does not hold yet.\n"
                        "- **An empty `decisions`**: no ADR's text matched the question's words or the seeds' names.\n"
                        "\n"
                        "## Known limits\n"
                        "\n"
                        "- Decisions come from text ranking until the ADR registry carries `decides` edges; an ADR that\n"
                        "  names many components can rank for unrelated questions. Measured in STORY-atlas-context-measure.\n"
                        "- Workitems are not seeds by default (`families=object,bridge`); `families=all` includes them.\n"
                        "\n"
                        "## Checking it\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-delivery-check",
            "type": "component",
            "name": "Deployed check",
            "summary": "check-deployed.sh: reports whether each running live image is at least as new as the source it was built from, so a missed rebuild shows up instead of looking like a bug.",
            "docs": [],
        },
        {
            "id": "atlas-delivery-deploy",
            "type": "component",
            "name": "Deploy",
            "summary": "deploy.sh: the one fixed way to deploy, a short set of allowlisted actions that refuses unless CI verified the commit, then checks the result and logs each run.",
            "docs": [],
        },
        {
            "id": "atlas-delivery-fork",
            "type": "component",
            "name": "Fork",
            "summary": "fork.sh: ships a branch to the experimental fork, a separate stack holding a scrubbed copy of the live graph where agents may try things not allowed elsewhere.",
            "docs": [],
        },
        {
            "id": "atlas-delivery-scripts",
            "type": "component",
            "name": "Delivery scripts",
            "summary": "The shell scripts that put branches on staging and the fork, deploy master to live, check what is running and take recovery points; run from the desktop, except snapshot, which runs on the server.",
            "docs": [],
        },
        {
            "id": "atlas-delivery-snapshot",
            "type": "component",
            "name": "Snapshot",
            "summary": "snapshot.sh: takes a one-time recovery point before a bounded experiment and verifies it by restoring into a disposable Postgres; deliberately not a backup routine.",
            "docs": [],
        },
        {
            "id": "atlas-delivery-stage",
            "type": "component",
            "name": "Stage",
            "summary": "stage.sh: ships a committed branch from the local clone to the staging stack, its own compose project with separate containers, database and network.",
            "docs": [],
        },
        {
            "id": "atlas-docs",
            "type": "capability",
            "name": "Node documentation",
            "summary": "Node documentation: the node_doc table holding each node's documents, its allowlisted read and write routes, and the standard for what a node's prose contains.",
            "abstract": (
                "`atlas-docs` owns **node documentation**: the `node_doc` table, the allowlisted read route, the write path, and the standard that says what a node's prose should contain. It is the documentation *subsystem*, not the documents' subject matter and not their rendering — the Docs tab that displays them belongs to `atlas-panel`, and the four prose tiers themselves are columns on `node`, which `atlas-graph` owns.\n"
                "\n"
                "Two ADRs define it. **ADR 0020** set the standard: a node carries `summary` (a line), `abstract` (the description), `detail` (behind a disclosure) and `attributes.docs[]` — pointers to heavy artifacts, each with a `kind` from a fixed enum and a `format` from another, shape-checked by the JSON-Schema on every world node type. **ADR 0029 superseded its file model** and moved the content into Postgres, which is the change with consequences: a document is a `node_doc` row, written with `PUT /api/node/{id}/doc` and read with `GET`, and `path` stopped being a filesystem location and became a **stable document key**. It still *looks* like `docs/nodes/<node-id>/architecture.md`, and nothing about `docs[]` changed, but the lookup is now `(node_id, path)` against a table and there is no `open()` left to defend.\n"
                "\n"
                "**Storing is not serving, deliberately.** `PUT /api/node/{id}/doc` stores a document without requiring an allowlist entry; `GET` refuses anything not matching an exact `path` in **that node's own** `docs[]`, before it ever touches the table. So publishing a stored document stays a separate, visible act — a PATCH on the node's attributes. A document can therefore be stored-and-not-served, which is an expected transitional state and not a defect.\n"
                "\n"
                "**There is no open work on this node, and the mechanism is finished.** Every workitem bound here is `done` or `cancelled` — `GET /api/workitems?binding=atlas-docs` is the live answer, and the one to trust over this sentence. The corpus was filled by authoring directly into `node_doc` rows rather than by the generation pipeline that was planned for it, and that pipeline was cancelled with its story. A handful of leaf world nodes still carry only a one-line `summary`; if one of them turns out to matter that is a one-node ticket, not a story."
            ),
            "detail": (
                "See `docs/adr/0020-atlas-node-documentation-standard.md` for the full standard. **The write path arrived late, and that is the thing worth knowing.** ADR 0029 moved storage and moved the read route; it did not move authoring. Until a later task the only writer of a `node_doc` row was a one-time importer, since deleted along with the directory it scanned — which left reads working and nothing able to add a document or fix a typo, a half-migration only visible when someone tried to write the next one. `PUT` and `DELETE` both `commit()` explicitly, because unlike every other write route they change no derived value and so cannot lean on `recompute_derived` to commit for them.\n"
                "\n"
                "`DELETE .../doc` leaves the `docs[]` allowlist entry alone on purpose — the entry is a separate assertion, and removing it uninvited would be the route quietly editing something the caller did not name. A `(node_id, path)` unique index keeps two nodes' documents from colliding, and `_delete_node_dependents` removes a node's rows in code rather than by DDL cascade.\n"
                "\n"
                "An entry marked `external: true` gets its own **200** response saying so, distinguishable from a 404 — that distinction is the whole reason the marking exists, so the board can render a real external reference rather than something indistinguishable from a broken link.\n"
                "\n"
                "**Nothing here is open, and the closures are worth knowing rather than re-proposing.** `STORY-atlas-doc-generation` — probe the system, author from the probe, gate on a fact-checker — was cancelled on 2026-08-17 (*\"doc generation we skip, we already did that\"*) and took `TASK-1004` through `TASK-1008` with it; the documentation arrived without the pipeline. The backfill `TASK-1075` went the same day, its work already done. Both completeness linters are cancelled too: `TASK-1006`'s fact-checker with its story, and `TASK-865`'s advisory `lint_world_docs.py` on 2026-08-27, because all three of its inputs were gone — `ingest_world.py` and `world_manifest.yaml` no longer exist, `required-docs.yaml` is keyed on node types ADR 0030 replaced, and its criterion \"every `docs[]` path exists in git\" tests exactly the storage model ADR 0029 retired.\n"
                "\n"
                "**The accepted consequence, recorded rather than lost: nothing mechanically checks the authored documents for wrong facts.** An agent writing architecture prose produces claims that are fluent, specific, plausible and wrong, and that is the failure a human reviewer is worst at catching. It is an accepted risk, not an oversight — file it if a wrong fact on the board ever bites."
            ),
            "docs": [],
        },
        {
            "id": "atlas-env-fork",
            "type": "component",
            "name": "Test fork",
            "summary": "A third, throwaway Atlas stack holding a scrubbed copy of live's graph under its own credentials, where an agent may run destructive changes without risk to live.",
            "abstract": "An example throwaway environment for agents. Restore a snapshot of live's database into a separate compose project and, before the stack is reachable, empty its sessions, service tokens and login throttle and clear every password hash; measure that all four are zero rather than trusting the script. Give the fork its own credential peppers, so no token or password from live can authenticate there and nothing minted there works on live. Reach it only through its own TLS origin, because the session cookie is Secure-only. An agent may then run destructive changes against real-shaped data; when the fork drifts, throw it away and seed it again from a new snapshot.",
            "docs": [],
        },
        {
            "id": "atlas-env-staging",
            "type": "component",
            "name": "Staging",
            "summary": "A second Atlas stack where a branch is deployed before live, against an older populated database, to prove changes survive real data.",
            "abstract": "An example staging environment. Run a second compose project beside live with an overlay file that renames the containers, gives the database its own volume, puts the stack on its own network and publishes a different port; pass the port in rather than re-declaring it, because compose concatenates port lists. Seed it once with an older, populated database and keep it: a fresh database cannot show whether a schema change survives real rows. Ship a branch to it from git (the code and the overlay both from the staged ref, never a working tree), check the result in a browser on staging's own TLS origin, and only then deploy to live. Session minting without a password may be switched on here for automated checks; never on live.",
            "docs": [],
        },
        {
            "id": "atlas-frontend",
            "type": "component",
            "name": "Atlas frontend",
            "summary": "Atlas's browser board, with no build step: a radial graph of the world, a work board scoped by it, a node panel and a dashboard, all read from /api/*; the old board is frozen at /board/legacy.",
            "abstract": (
                "atlas-frontend is Project Atlas's browser client. It renders the node/edge graph and the work filed against it, and it reads everything from **atlas-api**'s `/api/*` endpoints: it has no database access of its own.\n"
                "\n"
                "**The board is board v2, served at `/board`** (ADR 0043). It is up to four panes side by side: an optional dashboard (opened from the top bar), the graph, the work board and the node panel. Each pane has a full-height bar on its left: a click folds the pane, a drag resizes it.\n"
                "\n"
                "- **The graph.** Opens on the roots and your starred nodes. Clicking a node selects it, and double-clicking focuses it, drawing it as a radial tree. Steps follow structure (`contains`) or every link, stopping at hubs. Open work can be drawn as nodes, other relations appear on hover, and a *Types* menu hides node types. Each glyph shows the node's type, completion ring and markers for intents and documents, with more text as you zoom in.\n"
                "  - **Steps count down from what is chosen.** With a focus, the slider shows that many levels below it. With nothing focused, it counts from the roots. The slider runs from 1 to as deep as the graph goes (1–10 when that is unknown). Only the rings that show are laid out, so changing Steps re-spaces the nodes.\n"
                "  - **A focus shows its own limb.** The way back up to the root is always shown: one container chain, preferring the path you came by. It is faded and dashed, and it always points left, so a refocus never swaps sides. Nodes on other limbs, and relations past the root, are not shown.\n"
                "  - **Hover lights the whole way to the root** and dims the rest lightly.\n"
                "  - **Fold and Views.** A small −/+ at a node's top right folds away what is below it (keys `-`/`+` on a focused node). A small, translucent *Views* box at the top right of the pane, folded by default, saves the folded set together with focus and settings as a named view to swap between, and has an *Expand all* button. Folds and views live in the browser's localStorage (`atlas.v2.graph.collapsed`, `atlas.v2.graph.views`), not on the server.\n"
                "- **The work board.** Scoped to the node focused or selected in the graph: work rolls up, never down. Three views: *Epics* (a card per epic with its progress), *Board* (kanban columns by status with one lane per epic; dragging a card between columns sets its status) and *Backlog* (epic, story and task as a tree). Filters: everyone, mine, unassigned, text, and how long finished work stays visible.\n"
                "- **The node panel.** Everything about the selected node, in the tabs Overview, Intent, Comments (epics, stories and tasks only), History, Docs, Links, Decisions (projects, or nodes with ADRs) and Configuration (owners only, on install parts). For work it edits status, assignee, binding, description and acceptance. The Comments tab (`frontend/v2/views/panel/comments.js`) shows the item's conversation in markdown: anyone who may edit the item writes, the author edits their own, the author or an instance owner deletes. Nothing a comment said is lost: a delete hides the comment (the row stays, with who deleted it), and an edit keeps the replaced text in `workitem_comment_revision`. Both are audit records read in the database; no route returns them.\n"
                "- **The dashboard.** An optional fourth pane, hidden until opened, with boxes about the signed-in person.\n"
                "\n"
                "The work board and the panel can each be moved to a window of their own (for a second monitor) and stay in step with the main window. File work and Settings are the legacy modules, reused. On a phone the panes stack: the board over the graph, the panel as a sheet.\n"
                "\n"
                "**The previous board is frozen and unsupported, at `/board/legacy`.** It is the world tree / work tree / Mine board, unchanged, kept so nothing is lost while v2 is judged. New work goes into v2 only. `/board/v2`, v2's address while it was built, redirects to `/board`. Its child `atlas-frontend-legacy` holds the nodes for that board (`atlas-board-shell`, `atlas-map`); the panel nodes under `atlas-board-views` describe v2, except `panel-work`, the legacy-only Work tab.\n"
                "\n"
                "**Every page is behind a login (ADR 0025)**, with one deliberate exception: `/` (ADR 0035) gives a caller with no credential the public landing page, sends a signed-in browser to `/board`, and serves the legacy board directly only to a machine principal presenting a Bearer token. Assets are served from `/assets/` against the `ASSET_FILES` allowlist in `backend/routers/assets.py`; a file missing from it 404s, and v2's files are listed under their paths (`v2/core/store.js`).\n"
                "\n"
                "Depends on **atlas-api**: atlas-frontend cannot render anything without it. Healthy means `/board` loads and its reads succeed."
            ),
            "detail": (
                "Source: `project-atlas/frontend/v2/` for the board (entry `v2/app.js`; layers `core/` with no DOM and tested by `node --test`, `ui/`, `views/graph`, `views/work`, `views/panel`, `styles/`), and `project-atlas/frontend/` for the frozen legacy board (`board.html` and its modules). House rules for editing either are in `frontend/CLAUDE.md`. See `docs/nodes/atlas-frontend/architecture.md` for the module layers and the dependency chain to atlas-api. No container or port of its own: atlas-api serves the files.\n"
                "\n"
                "The graph's index (`ix`) is one bounded graph read (`GRAPH_URL` in `v2/app.js`: world families, a depth and a cap), so it need not hold every workitem: one filed a moment ago, or one the read does not reach, is missing from it. Anything that shows a workitem, such as the panel's head, falls back to that item's own `/api/node` read for its name and type."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-frontend/architecture.md",
                    "kind": "architecture",
                    "title": "atlas-frontend architecture",
                    "format": "mermaid",
                    "content": (
                        "# atlas-frontend — architecture\n"
                        "\n"
                        "## What this is\n"
                        "\n"
                        "`atlas-frontend` is Project Atlas's **browser client**. The board at `/board` is **board v2** (ADR 0043):\n"
                        "a radial graph of the world, a work board scoped by it, and a dashboard panel per node. It reads all of\n"
                        "its data from **atlas-api**'s `/api/*` endpoints and has no database access of its own.\n"
                        "\n"
                        "The previous board (world tree, work tree, Mine) is **frozen and unsupported** at `/board/legacy`.\n"
                        "\n"
                        "## Board v2: layers\n"
                        "\n"
                        "No build step, no framework: native ES modules that import each other by relative path, so the same\n"
                        "files run in the browser and under `node --test`.\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart TB\n"
                        "    App[\"app.js<br/>(entry: store, URL, panes, pop-out windows)\"]\n"
                        "    subgraph views\n"
                        "      Dash[\"views/dashboard<br/>the signed-in person's own boxes\"]\n"
                        "      Graph[\"views/graph<br/>radial tree, labels, glyphs\"]\n"
                        "      Work[\"views/work<br/>Epics · Board · Backlog\"]\n"
                        "      Panel[\"views/panel<br/>Overview · Intent · History · Docs · Links · Decisions · Comments\"]\n"
                        "      Search[\"views/search\"]\n"
                        "      Access[\"views/access<br/>groups and project roles (owner only)\"]\n"
                        "      Settings[\"views/settings<br/>crunch week · password notice\"]\n"
                        "    end\n"
                        "    UI[\"ui/<br/>dom helpers, panes and their collapse/resize bars, markdown, toasts, view prefs\"]\n"
                        "    Core[\"core/<br/>store, router, prefs, api, graph-model, vocabulary, dashboard, acceptance, access-model, short labels, window sync<br/>(no DOM; tested by node --test)\"]\n"
                        "    Legacy[\"legacy compose.js · settings.js<br/>(File work, Settings — reused)\"]\n"
                        "\n"
                        "    App --> Dash & Graph & Work & Panel & Search & Access & Settings\n"
                        "    App --> Legacy\n"
                        "    Dash & Graph & Work & Panel & Search & Access & Settings --> UI\n"
                        "    Dash & Graph & Work & Panel & Search & Access & Settings --> Core\n"
                        "    UI --> Core\n"
                        "```\n"
                        "\n"
                        "**The panes talk only through the store** (`core/store.js`). Focusing or selecting in the graph sets\n"
                        "`focus`/`selected`/`scope`; the work board and the panel subscribe. Hovering a card sets `hover`, and the\n"
                        "graph rings that card's node. A pane moved to its own window mirrors the shared keys over a\n"
                        "`BroadcastChannel` (`core/sync.js`). A dashboard box cannot reach the store, so it asks by a window\n"
                        "event: `atlas:select` with a node id, which `app.js` turns into a selection and an expanded node panel.\n"
                        "\n"
                        "**`core/graph-model.js` is where the graph's rules live**: the index built from `/api/graph`, the step\n"
                        "search with its hub stop, the type filter, binding inheritance (a task belongs to its epic's node), and\n"
                        "the roll-up that scopes the work board (work rolls up, never down). The workitem types and the\n"
                        "epic > story > task ladder are passed in from `/api/vocabulary` (`core/vocabulary.js`), not hard-coded.\n"
                        "\n"
                        "## Board v2: modules\n"
                        "\n"
                        "Every module the board serves, by the path it is served under (`/assets/<path>`). Each one's header\n"
                        "comment is the longer account. `atlas_health.undocumented_assets` lists any served file this table does\n"
                        "not name; a new module goes here in the same change that adds it to `ASSET_FILES`.\n"
                        "\n"
                        "`v2/test-support/` holds test-only shims (a d3-hierarchy loader for `node --test`) and is not served.\n"
                        "\n"
                        "## Dependency chain: atlas-frontend → atlas-api → atlas-postgres\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart LR\n"
                        "    Frontend[\"atlas-frontend<br/>(browser: /board, /board/legacy)\"]\n"
                        "    Api[\"atlas-api<br/>(FastAPI, container :8000, host :8095)\"]\n"
                        "    Db[(\"atlas-postgres<br/>(postgres:16, container :5432)\")]\n"
                        "\n"
                        "    Frontend -->|\"HTTP /api/*<br/>(reads, and writes with a CSRF token)\"| Api\n"
                        "    Api -->|\"ATLAS_DB_URL\"| Db\n"
                        "    Frontend -.->|\"NOT a direct client\"| Db\n"
                        "```\n"
                        "\n"
                        "A 401 on a read means the session ended: the board goes to the login page and comes back. A 403 on a\n"
                        "write is a refusal shown on the control that caused it.\n"
                        "\n"
                        "## Routes\n"
                        "\n"
                        "| Route | Serves |\n"
                        "|---|---|\n"
                        "| `/board` | board v2 (`frontend/v2/index.html`) |\n"
                        "| `/board/legacy` | the frozen legacy board (`frontend/board.html`) |\n"
                        "| `/board/v2` | a redirect to `/board`, keeping its query |\n"
                        "| `/` | the public landing page; a signed-in browser is sent to `/board` (ADR 0035) |\n"
                        "| `/assets/{path}` | files on the `ASSET_FILES` allowlist only (`backend/routers/assets.py`); v2's files under their paths |\n"
                        "\n"
                        "All of them except `/` are behind the login (ADR 0025).\n"
                        "\n"
                        "## Source\n"
                        "\n"
                        "`project-atlas/frontend/v2/` is the board. `project-atlas/frontend/` holds the legacy board, which is\n"
                        "changed only to keep it loading. `frontend/CLAUDE.md` has the editing rules for both.\n"
                        "\n"
                        "**The four orphaned HTML files in `frontend/` were removed on 2026-11-10** (git history keeps them):\n"
                        "`index.html` (the v0 tree), `login.html`, `change-password.html` and `logout.html`. None had a route;\n"
                        "the live auth pages are a string template in `backend/auth.py`.\n"
                        "\n"
                        "## Dependencies\n"
                        "\n"
                        "- **atlas-api** (`service`, role `api`): required. atlas-frontend cannot render anything without it.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-frontend-legacy",
            "type": "component",
            "name": "Legacy board",
            "summary": "The previous three-view board (world tree, work tree, Mine), frozen and unsupported at /board/legacy, changed only to keep it loading.",
            "docs": [],
        },
        {
            "id": "atlas-gateway",
            "type": "service",
            "name": "atlas-gateway (optional)",
            "summary": "Optional AgentGateway in front of atlas-mcp, started by a compose profile, giving agents one TLS address for several MCP servers and a tool allow-list.",
            "docs": [],
        },
        {
            "id": "atlas-graph",
            "type": "capability",
            "name": "Graph engine",
            "summary": "The machinery that stores and serves the graph: node and edge tables, their type registries, id allocation, and the read and write API over them.",
            "abstract": (
                "`atlas-graph` is the **machinery** of Atlas's object graph: the `node` / `edge` / `node_type` / `edge_type` tables in `backend/models_graph.py`, the write API and the read API over them in `backend/routers/` (nodes, edges, graph). It stores and serves the graph; it does not decide what the graph is allowed to say. That is `atlas-world-model` — the node families, the edge vocabulary and the binding rules live there, and this node would still be here if every one of them changed. It is also not the process: `atlas-api` is the FastAPI service this code runs inside, and `atlas-work-store` owns the workitem-shaped tables (prose, acceptance, result) that hang off `node.id`.\n"
                "\n"
                "What it owns:\n"
                "\n"
                "- **TWO type registries as data, and they behave differently.** A `node_type` row carries a JSON-Schema, a family, a health formula and agent instructions; `node.attributes` is validated against its own type's schema on **every flush**, via a SQLAlchemy `before_flush` event — so a REPL, a script and the API are all validated, not just one call site. An `edge_type` row (added 2026-08-26) carries the legal `(src_family, dst_family)` `pairs`, a description and an `active`/`dead` status — and is **NOT** enforced on write: it is read by the drift check on `/api/health` and by nothing else. That asymmetry is deliberate and the reasoning is beside `EDGE_FAMILY_RULES` in `deps.py`; do not \"fix\" it by wiring `pairs` into the write path without reading that first. Both are seeded every boot by `backend/node_types.py`. Boot reads no file and never overwrites graph data. What it does write is schema-shaped: the `node_type` and `edge_type` registries and the `task_id_seq` floor, every boot; on an empty graph, the seed the install's `ATLAS_SEED` profile names (homelab v0 tables, demo, self, plus the system node).\n"
                "- **`origin` (`git` | `atlas`).** Redefined once the boot ingest was deleted: it is **provenance**, not delete-authority. All the reconcilers that once read it are gone, and the only thing left that branches on it is the `detached_from_git` note in the PATCH response. Nodes are entirely `atlas`-origin now; edges are a mix of both, and nothing should write logic that assumes one value predominates.\n"
                "- **The write API** — `POST/PATCH/DELETE /api/node`, `POST/DELETE /api/edge` — all through `auth.guard_write`. There is no second write guard. Note what the edge route does NOT check: `_check_family` treats an edge type absent from `EDGE_FAMILY_RULES` as a permissive no-op, so several types in live use carry no family constraint here at all.\n"
                "- **Id allocation** — `allocate_task_id` off the Postgres `task_id_seq`. No client scans anything.\n"
                "- **The read path** — `GET /api/graph` (bounded neighbourhood) and `GET /api/graph/tree` (`contains`-only projection)."
            ),
            "detail": (
                "See `docs/nodes/atlas-graph/architecture.md` (this node's Docs tab) for the diagram. `GET /api/graph?focus&depth&edges&cap` walks outward from `focus` in **both directions**, depth clamped to [0, 20], capped at `GRAPH_NODE_CAP` nodes, returning `truncated: true` when it hits the cap and dropping any edge whose other endpoint was capped out. Omit `focus` and it seeds from **every root** — derived as \"a node with no incoming `contains`\", never a hardcoded id — with `?families=object,bridge` for the World tab. `GET /api/graph/tree` projects the same store through `contains` only, cycle-safe.\n"
                "\n"
                "`PATCH /api/node` assigns each supplied field with `exclude_unset`, and **replaces `attributes` wholesale** — read-modify-write it or you will drop `health` and `docs[]`. Invalid attributes return **400 naming the field**. `DELETE /api/node/{id}` refuses a node that has edges unless you pass `?cascade=1`, and reports `edges_deleted`. Rows that FK to a node (`probe`, `acceptance_criterion`, `workitem_result`, `node_doc`, `node_intent`) are cleaned up **in code**, at `_delete_node_dependents`, deliberately not by `ON DELETE CASCADE` — this schema keeps a multi-table delete visible at its call site. Note the one table it deliberately does NOT clean: `graph_event` outlives its node, so an event about a deleted node is readable by an owner and by nobody else.\n"
                "\n"
                "`_reconcile_graph_schema()` at boot only ever emits additive `ALTER TABLE ... ADD COLUMN`, and it reconciles indexes too. It emits no constraints, which is why `origin` deliberately carries no CHECK — a constraint enforced in tests and absent on live is worse than none. A brand-new TABLE is fine, though: `create_all` handles it, which is how `edge_type` arrived without touching this function at all.\n"
                "\n"
                "**Graph writes ARE audited now** — `graph_event`, built 2026-08-24 (TASK-1045), beside `auth_event` which stays identity-only. This paragraph listed it as an open gap until 2026-08-28, which it had not been for four days. One writer, `deps.record_graph_event`, enforced by `verify_graph_event.py` §B tokenising every backend module and failing on a second construction site; the actor comes off `auth.current_principal` and from nowhere else, so a request body cannot forge one. Read it back with `GET /api/events`. Two properties to state plainly rather than assume: it is **append-only by convention, not mechanism** — no trigger, no hash chain — and **nothing prunes it**.\n"
                "\n"
                "No Alembic, by decision (TASK-1015, 2026-08-16); `atlas-migrate upgrade` wraps the startup schema steps. Every `migrate_*.py` here is a one-off, dry-run by default."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-graph/architecture.md",
                    "kind": "architecture",
                    "title": "Graph engine architecture",
                    "format": "mermaid",
                    "content": (
                        "# atlas-graph — architecture\n"
                        "\n"
                        "The store and the two paths through it: what the tables are, and what a write and a read actually\n"
                        "traverse. Row counts are deliberately absent — `GET /api/health` answers those, and a number written\n"
                        "here goes stale because the system RAN, not because anyone decided anything.\n"
                        "\n"
                        "## The store\n"
                        "\n"
                        "`node`, `edge` and the **two** type registries — `node_type` and `edge_type` — are the graph.\n"
                        "Everything else in this diagram is a side table keyed by `node.id`, owned by another capability and\n"
                        "drawn here because a node delete has to clean them up.\n"
                        "\n"
                        "```mermaid\n"
                        "graph TD\n"
                        "  NT[\"node_type<br/>family, attributes_schema (JSON-Schema),<br/>health_formula, agent_instructions\"]\n"
                        "  ET[\"edge_type · added 2026-08-26<br/>pairs of (src_family, dst_family),<br/>description, status active or dead\"]\n"
                        "  N[\"node<br/>id, type, name, owner, source<br/>summary / abstract / detail, body<br/>assignee_user_id, completed_at<br/>attributes (JSONB), attention, origin\"]\n"
                        "  E[\"edge<br/>src, dst, edge_type, directed, origin\"]\n"
                        "  AC[\"acceptance_criterion<br/>(atlas-work-store)\"]\n"
                        "  WR[\"workitem_result<br/>(atlas-work-store)\"]\n"
                        "  ND[\"node_doc<br/>(atlas-docs)\"]\n"
                        "  NI[\"node_intent<br/>(ADR 0032)\"]\n"
                        "  PR[\"probe / probe_result<br/>(out of scope, v1)\"]\n"
                        "\n"
                        "  N -->|type FK| NT\n"
                        "  E -->|src FK| N\n"
                        "  E -->|dst FK| N\n"
                        "  E -.->|\"edge_type is a STRING, no FK:<br/>declared here, not enforced on write\"| ET\n"
                        "  AC -->|node_id FK| N\n"
                        "  WR -->|node_id FK| N\n"
                        "  ND -->|node_id FK| N\n"
                        "  NI -->|node_id FK| N\n"
                        "  PR -->|node_id FK| N\n"
                        "```\n"
                        "\n"
                        "Two columns on `node` that are easy to miss because they arrived after the rest:\n"
                        "`assignee_user_id` is an `auth_user.id` and is what \"whose work is this\" means now — deliberately\n"
                        "not a foreign key, for the same reason `AuthUser.person_user_id` is not one — and `completed_at`\n"
                        "is the derived moment a node reached a done status, which is the axis the board's age filter sorts\n"
                        "on.\n"
                        "\n"
                        "None of the side tables carries `ON DELETE CASCADE`. They are deleted explicitly in\n"
                        "`routers/nodes._delete_node_dependents`, which is the only node-deletion path left since TASK-1058 removed the\n"
                        "boot reconcilers. `graph_event` is the one table that is deliberately NOT cleaned up there — an\n"
                        "event outlives its node.\n"
                        "\n"
                        "## Write in, read out\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart TD\n"
                        "  C[\"board · agent · script · atlas-mcp\"] --> G[\"auth.guard_write<br/>session: Origin allow-list + synchronizer token<br/>token/grace: WRITE_SCOPE_ROUTES scope check (TASK-1086)\"]\n"
                        "  G -- \"missing/wrong scope\" --> E403[\"403 forbidden — fails closed\"]\n"
                        "  G --> W[\"POST/PATCH/DELETE /api/node<br/>POST/DELETE /api/edge\"]\n"
                        "  W --> F[\"_check_family (EDGE_FAMILY_RULES)<br/>_refuse_second_binding (≤1 works_on)\"]\n"
                        "  F --> V[\"validate_node — before_flush<br/>attributes vs node_type JSON-Schema<br/>→ 400 naming the field\"]\n"
                        "  V --> GE[\"record_graph_event — the ONE writer,<br/>and BEFORE the commit the route rides on\"]\n"
                        "  GE --> D[(atlas-postgres)]\n"
                        "  D --> RD[\"recompute_derived — commits\"]\n"
                        "  D --> R1[\"GET /api/graph<br/>bounded BFS, both directions,<br/>cap 500, truncated flag\"]\n"
                        "  D --> R2[\"GET /api/graph/tree<br/>contains-only, roots derived\"]\n"
                        "  D --> R3[\"GET /api/node/{id}\"]\n"
                        "  D --> R4[\"GET /api/events<br/>access-filtered before total\"]\n"
                        "```\n"
                        "\n"
                        "`recompute_derived()` ending in a `commit()` is the **only** commit reachable from a request path in\n"
                        "`app.py`, which is what makes `POST /api/workitem` atomic across its node, its criteria and its four\n"
                        "edges. It is also why a `record_graph_event` call placed *after* a route's commit silently rolls\n"
                        "back with nothing raised — record first.\n"
                        "\n"
                        "**Bearer is exempt from CSRF, not from authorization.** That distinction used to collapse to\n"
                        "nothing — before TASK-1086 (2026-08-15), `guard_write` returned `None` (proceed) for every\n"
                        "non-session principal unconditionally, so \"exempt from CSRF\" and \"exempt from every check\" were the\n"
                        "same statement. They are not any more: a token or the dormant `grace` principal skips the\n"
                        "Origin/synchronizer-token dance (there is nothing ambient a cross-site page could make it send) but\n"
                        "is now priced through `WRITE_SCOPE_ROUTES` like every other write, and a route with no entry in that\n"
                        "map is refused rather than allowed. See `atlas-identity`'s architecture doc for the full mechanism.\n"
                        "\n"
                        "`GET /api/vocabulary` (TASK-1081) reads the same registries the `F`/`V` steps above validate\n"
                        "against — `node_type`, `EDGE_FAMILY_RULES`, `attention.SETTABLE_STATUSES`, `WORKITEM_TYPES` and\n"
                        "`node_types.INTENT_KINDS` — derived at request time, never a second hand-typed copy. It exists\n"
                        "because Atlas serves no OpenAPI document, so it is how a caller (an agent especially) learns the\n"
                        "legal node types, edge-type rules, statuses, workitem types and intent kinds without collecting\n"
                        "`400`s until it stops getting them.\n"
                        "\n"
                        "## Ids\n"
                        "\n"
                        "`allocate_task_id(session)` is the one way a `TASK-NNN` id is minted — `nextval('task_id_seq')`,\n"
                        "which Postgres guarantees distinct under concurrency and does not roll back. That non-transactional\n"
                        "property is the point: the pre-Atlas convention had the *client* scan filenames for the next\n"
                        "integer, a read-then-write race in a repo that runs parallel agents on purpose. The accepted\n"
                        "consequence is that a created-then-deleted workitem burns a number. `seed_task_id_sequence()` runs\n"
                        "at every boot and can only push the floor up, never move it back.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-guardrails",
            "type": "capability",
            "name": "Agent guardrails",
            "summary": "Per-environment rules for what an agent may do on its own and what needs a human, kept as one matrix applied to the install's system node and exported as policy files.",
            "docs": [],
        },
        {
            "id": "atlas-identity",
            "type": "capability",
            "name": "Identity & access",
            "summary": "Who you are and how you prove it: the gate every request passes, login, passwords, sessions, accounts, agents' service tokens (agent credentials on the board), and optional identity providers.",
            "abstract": (
                "`atlas-identity` is **authentication** — who you are and how you prove it. It owns the ASGI gate every request passes, login and logout, password storage and policy, sessions and their cookies, accounts and service tokens; every credential event it handles is written to `auth_event`, and the record itself — reading, export, retention — is `atlas-audit-trail`'s. It is **not** authorisation-to-see: whether a principal may read a given node belongs to `atlas-visibility`, its sibling under `atlas-security`.\n"
                "\n"
                "**The gate is middleware, not a dependency.** `AuthGate` (`backend/auth.py`) is registered with `app.add_middleware`, so it wraps the whole router, every mount and every path that does not exist — including routes added later. A route-level scheme fails *open* when somebody adds a route; middleware fails closed. Deny by default: `auth.PUBLIC_ROUTES` is the whole unauthenticated surface — login, the password-change page, the denied page, the **privacy notice**, liveness/readiness (`/healthz`, `/readyz`), the favicon, and **`/`**. Three opt-in settings extend it with exact paths, never a prefix: the OIDC start and callback routes (`ATLAS_AUTH_PROVIDERS`, ADR 0041), the handbook (`ATLAS_PUBLIC_DOCS=1`, ADR 0047) and the landing site's files (`ATLAS_LANDING=v2`, ADR 0049); unset, the list is exactly the one above. `/api/health` is deliberately excluded because it is a reconnaissance document. **`/` is the one public route that is not a fixed response** (ADR 0035): its handler renders the public landing page for a stranger, serves the legacy board directly to a machine principal presenting a Bearer token, and 302s a signed-in browser to `/board`, which stays gated. `verify_object_graph.py` pins that list exactly, so an addition has to be argued for rather than slipped in.\n"
                "\n"
                "**Exactly two credential branches, never three.** `Authorization: Bearer atlas_svc_...` is the machine principal and is CSRF-exempt; the `__Host-atlas_session` cookie is the human principal and requires a synchronizer token. Both present is a flat `400`. Nothing reads a `Cf-Access-*` header — that code is not written, so the forged-header escalation does not exist here. Bearer is honoured only on the hosts in `ATLAS_BEARER_HOSTS`, and a token presented anywhere else is treated as a bad credential rather than given a distinguishable hint.\n"
                "\n"
                "**Passwords** are `argon2id+hmac-sha256-v1`: NFC -> HMAC-SHA256 under a server pepper -> Argon2id at m=64 MiB, t=3, p=4. Screened against a breach corpus. Service tokens are keyed SHA-256 instead — against a CSPRNG secret there is no dictionary, and Argon2 per request on an internet-facing endpoint is a self-inflicted DoS.\n"
                "\n"
                "**Scopes are enforced.** As of TASK-1086 (2026-08-15), `guard_write` sends every non-session principal through `_check_write_scope`, which maps the request's route template and method to a required scope and calls `Principal.has_scope` before allowing the write — a route with no mapping is refused, never allowed by default. Anything describing scopes as recorded-but-not-checked is stale.\n"
                "\n"
                "**An agent has its own account, linked to a person** (ADR 0034, live 2026-08-16). `auth_user.person_user_id` records who is accountable; grants inherit person → bot while ROLE never does; and disabling a person disables their agents. A credential no longer needs an operator: any signed-in person mints, rotates and revokes their own from Settings → Agent credentials, and the bot account behind it is created and linked in the same call."
            ),
            "detail": (
                "See `docs/nodes/atlas-identity/architecture.md` (this node's Docs tab) — \"The gate, from request to principal\" — for the full diagram.\n"
                "\n"
                "**Sessions.** The id is `secrets.token_urlsafe(32)` and the row's primary key is its HMAC digest — the raw value is never stored, so a `pgdata` leak yields no live sessions. Cookie: `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain`, and **no `Max-Age`** — expiry is server-side because the client controls the cookie. Idle sliding window, absolute ceiling. The id rotates twice: at login and again at password change. Each session records how it was established (`auth_session.auth_method`: `password`, `sso` or `minted`; `_mint_session` requires it), because the account alone no longer says so.\n"
                "\n"
                "**Self-service credentials** (`STORY-atlas-agent-credentials`, 2026-08-16). Four routes under `/api/me/`: create, list, rotate and revoke. Three rules live in one place each so the two doors to a credential cannot drift apart — `models_auth.self_mintable_scopes` (the ceiling is derived from ROLE, because a human holds no scopes at all: every session principal is built with `scopes=()`), `passwords.validated_handle` (moved out of `admin.py` once accounts could be created from two places), and `auth.reauthenticate` (so a failed re-auth reaches the same IP limiter and the same `auth_event` row as any other credential failure). Every one of those routes refuses a Bearer principal on KIND regardless of scope: a leaked token must not be a key factory, and rotation must not be a softer path than first issue.\n"
                "\n"
                "**Rotation does not revoke the old token**, deliberately. `mint_service_token` allows several live tokens per principal precisely so the order can be mint → deploy → confirm `last_used_at` moves → revoke. Revoke-then-mint is an outage, and a credential that cannot be rotated without one is a credential nobody rotates.\n"
                "\n"
                "Near-expiry and long-unused tokens surface on the authenticated `/api/health` as `token_warnings`, and on the owner's own credential list — that live view, not this text, is where to check current account and token counts.\n"
                "\n"
                "**SSO accounts, the IdP step-up and the password fallback (opt-in, OIDC installs only).** An account linked to the IdP (`atlas-admin user link-idp`) is keyed by `(idp, subject)`. Where Atlas would ask a local account to re-enter its password, an SSO person instead proves themselves with a **step-up**: a fresh IdP sign-in (`GET /auth/oidc/step-up`, `prompt=login` + `max_age`, `auth_time` checked) that stores a short-lived, single-use grant bound to one purpose — `config:<NAME>` for a configuration write (TASK-1436), `password:set` for setting a personal password (TASK-1442). A grant for one purpose cannot be spent on another.\n"
                "\n"
                "The forced password change (`must_change_password`) has ONE rule, `identity.forced_change_applies(user, method)`: a local account is always held; a linked account is held unless the credential came through the IdP (`method == sso`), and an unknown or missing method is held (fail closed). So an SSO person whose account still carries the flag reaches the board, which offers to set a personal password via `POST /api/me/password` after a step-up, without the temporary one; IdP access tokens follow the same rule (TASK-1455).\n"
                "\n"
                "That personal password is a **fallback for when the IdP is down** (TASK-1454): `link-idp` records the pre-link username as the account's local sign-in name (`auth_user_idp_state.local_name`; `user set-local-name` sets or clears it for an account linked earlier). Only with both `local` and `oidc` in `ATLAS_AUTH_PROVIDERS` does the password form, on a miss of `idp='local'`, look a linked account up by that name; `ATLAS_LOCAL_LOGIN` then applies as for any local account (`all`, or `break-glass` for flagged accounts only). A local account always wins its own name, and usernames, agent labels and local names share one namespace. Removing a person at the IdP does not stop that password, so offboarding also means `user disable`. Without OIDC none of this is reachable and behaviour is as before.\n"
                "\n"
                "**Fail closed.** `ATLAS_TOKEN_PEPPER`, `ATLAS_LOGIN_ORIGINS` and `ATLAS_PASSWORD_PEPPER` raise at *import*, naming the key, before uvicorn binds — there is no generated-if-missing fallback, because a per-boot random pepper logs everyone out on every unattended reboot. With an empty `auth_user` table the app serves the login page and refuses every credential; the first owner is minted by `python -m admin user add` against the live database. The same import-time configuration is why `atlas-admin backup …` drops the sign-in settings from its own process before importing `auth` (TASK-1437): the backup container reads the same `.env` but holds none of the IdP's files, and backup needs no identity.\n"
                "\n"
                "**Two escape hatches, both config-gated and both off on live.** `ATLAS_MACHINE_GRACE_HOSTS` (a staged rollout that can never let a browser or a bad credential through) is unset; `ATLAS_ALLOW_SESSION_MINT` — a password-free browser session for verification — is set only on non-live stacks (staging and the fork), never on live.\n"
                "\n"
                "**Two public pages a person reads.** `/auth/privacy` (TASK-941) answers without any credential, because a privacy notice you can only read after signing in is not available to the person deciding whether to sign in. `/` (ADR 0035) is the public landing page for a stranger and a redirect to `/board` for a signed-in browser — the one public route whose response depends on who is asking."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-identity/architecture.md",
                    "kind": "architecture",
                    "title": "The gate — from request to principal",
                    "format": "mermaid",
                    "content": (
                        "# atlas-identity — architecture\n"
                        "\n"
                        "How a request becomes a principal, and what a *write* has to pass on top of that.\n"
                        "\n"
                        "## 1. The gate\n"
                        "\n"
                        "`AuthGate` (in `backend/auth.py`) is ASGI middleware added with `app.add_middleware`, so it\n"
                        "runs before any route, any mount, and any 404. Everything below happens in `_evaluate`, in a\n"
                        "threadpool, reading only scope-level attributes — never the body, which the handler still needs.\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart TD\n"
                        "  R[\"Any request, any path\"] --> H{\"Host in ATLAS_LOGIN_ORIGINS?\"}\n"
                        "  H -- no --> E421[\"421 misdirected_request\"]\n"
                        "  H -- yes --> C{\"credentials presented\"}\n"
                        "  C -- \"both Bearer and cookie\" --> E400[\"400 ambiguous_credentials\"]\n"
                        "  C -- \"Authorization: Bearer atlas_svc_\" --> T[\"token branch: prefix lookup,<br/>constant-time HMAC compare\"]\n"
                        "  C -- \"__Host-atlas_session cookie\" --> S[\"session branch: idle 24h,<br/>absolute 14d\"]\n"
                        "  C -- \"none\" --> N[\"anonymous\"]\n"
                        "  T -- invalid --> E401[\"401 + WWW-Authenticate:<br/>Bearer error=invalid_token\"]\n"
                        "  T -- valid --> SET\n"
                        "  S -- unresolvable --> CLR[\"treated as anonymous,<br/>cookie cleared\"]\n"
                        "  S -- valid --> MC{\"must_change_password?<br/>re-read from auth_user every request\"}\n"
                        "  CLR --> N\n"
                        "  N --> PUB{\"path in PUBLIC_ROUTES?\"}\n"
                        "  PUB -- yes --> SERVE[\"handler runs with no principal\"]\n"
                        "  PUB -- no --> DENY[\"302 to /auth/login for a browser GET;<br/>otherwise 401 JSON\"]\n"
                        "  MC -- \"yes, and path outside MUST_CHANGE_ALLOWLIST\" --> BOUNCE[\"302 /auth/password/change,<br/>or 403 JSON\"]\n"
                        "  MC -- no --> SET[\"current_principal set in a ContextVar;<br/>handler runs; reset in a finally\"]\n"
                        "```\n"
                        "\n"
                        "Three rules the picture encodes that are easy to get wrong:\n"
                        "\n"
                        "- **`302` is decided in exactly one place** and needs all four of: method `GET`/`HEAD`,\n"
                        "  `Accept` contains `text/html`, path not under `/api/`, and no `Authorization` header. A\n"
                        "  script that presented a Bearer is never redirected — a redirect it follows returns a `200`\n"
                        "  login page it parses as data and reports as success.\n"
                        "- **`must_change_password` is re-read from `auth_user` on every request**, never cached on the\n"
                        "  session row and never a claim in the cookie. A cached `false` is a bypass with no revocation\n"
                        "  path.\n"
                        "- **Revoked, expired and never-existed are byte-identical**: same status, same body, same\n"
                        "  headers.\n"
                        "\n"
                        "## 2. What a write passes on top\n"
                        "\n"
                        "**Scopes are enforced** (TASK-1085/1086, live 2026-08-15). Until then this section described a\n"
                        "gap: every non-session principal — a Bearer token, and the dormant `grace` bypass alike — passed\n"
                        "`guard_write` unconditionally, before any scope was ever consulted. That is fixed now, not just\n"
                        "recorded at mint time.\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart LR\n"
                        "  W[\"State-changing /api/ call\"] --> G[\"auth.guard_write<br/>one call site per mutating route,<br/>see WRITE_SCOPE_ROUTES\"]\n"
                        "  G --> K{\"principal.kind\"}\n"
                        "  K -- \"session\" --> O[\"Origin/Referer must be allow-listed\"]\n"
                        "  O --> X[\"synchronizer token from GET /api/me,<br/>compared against auth_session.csrf_hash\"]\n"
                        "  X --> ALLOW[\"returns None — proceed\"]\n"
                        "  K -- \"token or grace\" --> SC[\"_check_write_scope:<br/>WRITE_SCOPE_ROUTES[(method, route template)]\"]\n"
                        "  SC -- \"route not in the map\" --> E403A[\"403 forbidden — fails closed,<br/>never defaults to allowed\"]\n"
                        "  SC -- \"principal lacks the needed scope\" --> E403B[\"403 forbidden:<br/>missing required scope\"]\n"
                        "  SC -- \"principal.has_scope(needed)\" --> ALLOW\n"
                        "  ALLOW --> HND[\"handler mutates\"]\n"
                        "```\n"
                        "\n"
                        "`Principal.has_scope` (in `backend/auth.py`) is plain literal membership — there is no\n"
                        "`read < write < admin` implication, so a caller needing both is minted `write,admin` explicitly.\n"
                        "It is called from `_check_write_scope` (in `backend/auth.py`), which every non-session\n"
                        "principal now passes through before a mutation runs — that includes the `ATLAS_MACHINE_GRACE_HOSTS`\n"
                        "bypass, which used to reach the handler with no scope check at all.\n"
                        "\n"
                        "`WRITE_SCOPE_ROUTES` prices every mutating route at `write` or `admin`, keyed on\n"
                        "`(method, route TEMPLATE)` rather than path alone: three templates carry both a `write` verb and\n"
                        "an `admin` verb on the *same* path — `PATCH /api/node/{node_id}` is `write`, `DELETE\n"
                        "/api/node/{node_id}` is `admin`, and the same split applies to `/api/node/{node_id}/doc` and to\n"
                        "`/api/intent/{intent_id}` (intent authorship is priced `write`, deletion `admin` — a reversal of\n"
                        "ADR 0032's \"agents do not write intents\", spent on `atlas-mcp`, see ADR 0033). A route with no\n"
                        "entry in the map is **refused**, not allowed by default. `SCOPES` gained `write` for this\n"
                        "(`backend/models_auth.py`); `ingest` stays in the tuple unused — it named the boot-time reconciler\n"
                        "TASK-1058 already deleted, and repurposing that name for something else it was never built for\n"
                        "would misname the thing it now guards. A token minted `--scopes read` can no longer reach any of\n"
                        "these routes — before this shipped, one could `DELETE /api/node/{id}?cascade=1`.\n"
                        "\n"
                        "The `auth_service_token` table's `scopes` CHECK constraint needed its own migration\n"
                        "(`backend/migrate_add_write_scope.py`) to admit `write` — `_reconcile_graph_schema()` only\n"
                        "reconciles `models_graph` tables, and there is no equivalent for `models_auth`. Deploy order\n"
                        "mattered for exactly that reason: widen the constraint, re-mint every token that needs `write`\n"
                        "(`ATLAS_SYNC_TOKEN` was `read`-scoped and writing daily), and only then ship the code that turns\n"
                        "enforcement on — reversing any step of that order produces a token that is either unmintable or\n"
                        "that stops working mid-deploy.\n"
                        "\n"
                        "The CSRF token is *derived*, not stored: `HMAC(pepper, \"csrf-token-v1\" || sid)`, so the server\n"
                        "can recompute it from the cookie it already holds while an attacker without the cookie cannot\n"
                        "produce it. `SameSite=Lax` stays on as defence in depth — the cookie sits on a hostname whose\n"
                        "whole zone is \"same-site\" to it, so it was never the control.\n"
                    ),
                },
                {
                    "path": "docs/nodes/atlas-identity/github-link.md",
                    "kind": "architecture",
                    "title": "GitHub link and repo access",
                    "format": "mermaid",
                    "content": (
                        "# atlas-identity — GitHub link and repo access\n"
                        "\n"
                        "Live since 2026-10-05 (master `5de3c7b`, TASK-1221). Code: `backend/github_link.py` plus the routes\n"
                        "in `backend/app.py` under `/api/me/github*` and `/api/access/github/*`; tests in\n"
                        "`backend/verify_github_link.py`.\n"
                        "\n"
                        "## What it is for\n"
                        "\n"
                        "`project-atlas` is a private repo under a **personal** GitHub account, so every collaborator gets\n"
                        "**push** — there is no read-only role. Inviting someone therefore has to be (a) to an account that is\n"
                        "provably theirs and (b) a deliberate act by the owner. Hence two halves:\n"
                        "\n"
                        "- **Linking** — a signed-in person proves which GitHub account they own by signing in with GitHub.\n"
                        "  Atlas stores the verified numeric id and login. **Linking grants nothing.**\n"
                        "- **Approving** — the owner ticks *repo access* for that person at Settings → Access & sharing. Atlas\n"
                        "  sends a collaborator invitation; the person accepts on GitHub. Unticking removes them, or cancels a\n"
                        "  pending invitation.\n"
                        "\n"
                        "## The flow\n"
                        "\n"
                        "```mermaid\n"
                        "sequenceDiagram\n"
                        "    autonumber\n"
                        "    actor P as Person (browser session)\n"
                        "    participant A as atlas-api\n"
                        "    participant G as GitHub\n"
                        "    actor O as Owner (browser session)\n"
                        "\n"
                        "    P->>A: GET /api/me/github/start\n"
                        "    A-->>P: 302 to github.com/login/oauth/authorize (state bound to the session)\n"
                        "    P->>G: sign in, authorise the OAuth app\n"
                        "    G-->>P: 302 to /api/me/github/callback?code&state\n"
                        "    P->>A: callback\n"
                        "    A->>G: exchange code (client id + secret from /run/github)\n"
                        "    A->>G: GET /user with the user token\n"
                        "    Note over A: token used once, never stored; id + login saved in github_link\n"
                        "    A-->>P: Settings shows the linked login\n"
                        "\n"
                        "    O->>A: PUT /api/access/github/{user_id} repo_access=true (owner session + CSRF)\n"
                        "    A->>G: GET /user/{id} (re-resolve login from the stored id)\n"
                        "    A->>G: PUT /repos/{repo}/collaborators/{login} (fine-grained invite token)\n"
                        "    G-->>P: invitation e-mail; person accepts on GitHub\n"
                        "    O->>A: POST /api/access/github/{user_id}/refresh\n"
                        "    Note over A: status: invited → collaborator once accepted\n"
                        "```\n"
                        "\n"
                        "## Rules the code enforces\n"
                        "\n"
                        "- **Only a browser session can link.** A Bearer principal is refused on `/api/me/github*` — an agent\n"
                        "  cannot start an OAuth flow or attach an identity.\n"
                        "- **Only the owner can approve**, by exactly the mechanism of `PUT /api/access/node/{id}`:\n"
                        "  `_require_owner()` (a bot's role is always member, so no token qualifies at any scope), then\n"
                        "  `guard_write` (Origin + CSRF), plus an `admin` entry in `WRITE_SCOPE_ROUTES`. Agents propose;\n"
                        "  humans commit.\n"
                        "- **The id is the key, not the login.** Logins can be renamed and re-registered; before inviting or\n"
                        "  removing, the login is re-resolved from the stored id so an invitation never goes to whoever now\n"
                        "  holds an old name.\n"
                        "- **No GitHub user token is retained**, logged or returned.\n"
                        "- **Off unless fully configured.** If any credential file is missing or empty, or the repo is unset,\n"
                        "  the routes answer `404 github_not_configured` and Settings hides the controls. Nothing raises at\n"
                        "  boot.\n"
                        "\n"
                        "## Configuration\n"
                        "\n"
                        "The invite token is a **fine-grained PAT** limited to that one repository with only\n"
                        "*Administration: read and write*. It expires; renew it before the date set on it, by replacing the\n"
                        "file — no restart needed, the file is read on every use. Checked on live 2026-10-05: with it, Atlas\n"
                        "can read the repo, its invitations and its collaborators (all 200).\n"
                        "\n"
                        "## Known gaps\n"
                        "\n"
                        "## Deploy notes\n"
                        "\n"
                        "`tools/stage.sh` ships the staging overlay from **the clone it runs in**, not from the branch being\n"
                        "staged. Staging a branch that changes `deploy/staging.override.yml` from a checkout on another\n"
                        "branch silently drops that change — here, the `/run/github` mount, so the feature reported\n"
                        "\"not configured\". Run `stage.sh` from a worktree of the branch itself (TASK-1224 fixes the script).\n"
                    ),
                },
                {
                    "path": "docs/nodes/atlas-identity/idp-tokens-and-read-audit.md",
                    "kind": "architecture",
                    "title": "IdP access tokens",
                    "format": "md",
                    "content": (
                        "# IdP access tokens\n"
                        "\n"
                        "An opt-in addition to identity, off unless configured. Unset, every path behaves as it did before\n"
                        "it: a Bearer is an `atlas_svc_` token or nothing.\n"
                        "\n"
                        "The read audit is on `atlas-audit-trail`: `docs/nodes/atlas-audit-trail/read-audit.md`.\n"
                        "\n"
                        "## IdP-issued access tokens (STORY-oss-25, ADR 0041 §9)\n"
                        "\n"
                        "An MCP client can authenticate with an OAuth 2.1 access token issued by the install's identity\n"
                        "provider, alongside Atlas-minted `atlas_svc_` tokens. Turned on by **`ATLAS_OAUTH_RESOURCE`**, which\n"
                        "requires `oidc` in `ATLAS_AUTH_PROVIDERS`.\n"
                        "\n"
                        "### Settings\n"
                        "\n"
                        "| Variable | Where | Meaning |\n"
                        "|---|---|---|\n"
                        "| `ATLAS_OAUTH_RESOURCE` | atlas-api and atlas-mcp (same value, one `.env`) | the canonical https URI of the protected resource (the MCP endpoint); no query or fragment |\n"
                        "| `ATLAS_OAUTH_AUDIENCES` | atlas-api | further accepted `aud` values, e.g. an Entra resource app's client id; default is the resource URI only |\n"
                        "| `ATLAS_OAUTH_SCOPE_MAP` | atlas-api | `<idp scope>=<read\\|write\\|admin>`, comma separated; the highest mapped scope wins, nothing mapped = read only |\n"
                        "| `ATLAS_MCP_OAUTH_SCOPES` | atlas-mcp, optional | the scope string put in the 401 challenge; it belongs to the IdP (Entra wants `api://<app>/<scope>`) |\n"
                        "\n"
                        "A set-but-blank value is an error at boot. `ATLAS_OAUTH_AUDIENCES` and `ATLAS_OAUTH_SCOPE_MAP`\n"
                        "without `ATLAS_OAUTH_RESOURCE` are an error too.\n"
                        "\n"
                        "### Where the token is checked\n"
                        "\n"
                        "Validation stays in atlas-api (ADR 0033); atlas-mcp only routes. `auth._resolve_bearer` sends\n"
                        "`atlas_svc_…` down the unchanged token path and a JWT to `backend/identity/access_token.py`, which\n"
                        "checks:\n"
                        "\n"
                        "- **signature** with a key from the OIDC provider's cached, rotation-aware JWKS, under the same\n"
                        "  asymmetric `alg` allow-list as login (never `none`, never HS*);\n"
                        "- **`iss`** equal to the SSO issuer exactly;\n"
                        "- **`aud`** containing the resource URI or an `ATLAS_OAUTH_AUDIENCES` value. Atlas's own\n"
                        "  `ATLAS_OIDC_CLIENT_ID` is refused as an audience, and a token carrying `nonce` (an ID token) is\n"
                        "  refused;\n"
                        "- **`exp` / `nbf` / `iat`** within 60 s of skew.\n"
                        "\n"
                        "Every refusal is a 401 and an `auth_event` `login_fail` with reason `oauth:<why>`.\n"
                        "\n"
                        "### Who the caller is\n"
                        "\n"
                        "- **The person** is found by the SSO preset's subject rule (`oidc.subject_of`, shared with login),\n"
                        "  so the same IdP identity is the same Atlas person.\n"
                        "- **No provisioning from tokens.** A token for an identity with no Atlas person is refused, with a\n"
                        "  detail telling them to sign in with SSO once. SSO login and `link-idp` stay the only ways a person\n"
                        "  is created, because only they run the required-group check, the group sync and the audit trail.\n"
                        "- **Same offboarding rule as minted tokens:** the person must not be disabled, must not be forced to\n"
                        "  change their password, and must have an SSO login within `ATLAS_OIDC_REVALIDATE_DAYS`.\n"
                        "- **One agent account per (person, OAuth client)**, created on first use and linked to the person\n"
                        "  (ADR 0034): `idp='oauth'`, label `oauth:<client>`, role `member`, `is_service`. The client is read\n"
                        "  from `client_id`, then `azp`, then `appid`. Disabling that agent turns off one client for that\n"
                        "  person; disabling the person turns off all of them.\n"
                        "- **Effective permission = scope cap ∩ project role.** The mapped scope is capped by\n"
                        "  `self_mintable_scopes(person.role)` (a member never holds admin) and then by project RBAC, exactly\n"
                        "  as for a minted token.\n"
                        "\n"
                        "### At the MCP door\n"
                        "\n"
                        "With the feature on, atlas-mcp serves RFC 9728 protected-resource metadata at\n"
                        "`/.well-known/oauth-protected-resource/mcp` (plus the root fallback and a trailing-slash twin), with\n"
                        "`authorization_servers` = the SSO issuer. A call without a token gets a 401 whose\n"
                        "`WWW-Authenticate` carries `resource_metadata` (and `scope` when `ATLAS_MCP_OAUTH_SCOPES` is set).\n"
                        "A JWT-shaped token goes through the door to the same `GET /api/me` check as any other. With the\n"
                        "feature off there is no metadata route, the 401 is the old `Bearer realm=\"atlas-mcp\"`, and a JWT is\n"
                        "refused at the door without reaching the API.\n"
                        "\n"
                        "### Entra notes\n"
                        "\n"
                        "- Entra v2 access tokens carry the resource app's client-id GUID in `aud`: put it in\n"
                        "  `ATLAS_OAUTH_AUDIENCES`.\n"
                        "- v1 tokens (`sts.windows.net` issuer) are refused; set `accessTokenAcceptedVersion=2` on the\n"
                        "  resource app.\n"
                        "- A tool call that lacks a scope gets the API's 403 inside the MCP response, not an HTTP\n"
                        "  `insufficient_scope` challenge.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-ingress",
            "type": "capability",
            "name": "Ingress",
            "summary": "The path in: routing, TLS termination, tunnels, hostnames, what is exposed at all.",
            "abstract": (
                "`atlas-ingress` is **the path in** — how a request reaches `atlas-api` at all. It owns what\n"
                "terminates that path: the reverse proxy that terminates TLS in front of the API, any tunnel\n"
                "connector an installation adds for a public hostname, the hostname and origin allow-lists, and the\n"
                "standing decision about what is exposed. An installation's own proxy and tunnel containers sit\n"
                "beneath this node. It is the network path, **not** the policy: whether a request is allowed once it\n"
                "arrives is `atlas-security`'s, its sibling under `atlas`.\n"
                "\n"
                "**The API's own port is never a way in from the network.** A browser or a remote agent reaches\n"
                "`atlas-api` through the TLS-terminating reverse proxy; a public hostname, where an installation has\n"
                "one, is best carried by an **outbound-only** tunnel connector, which opens no inbound port on the\n"
                "network — the property that makes exposing the board acceptable at all. The API either publishes its\n"
                "port on loopback only, for scripts on the host itself, or — as the stock install in `deploy/oss`\n"
                "does — publishes no port at all. From anywhere else its port refuses the connection outright: not a\n"
                "`401`, a refused connection. That is the intended shape, not a fault.\n"
                "\n"
                "**What the bare domain exposes changed on 2026-08-25 (ADR 0035).** `/` no longer redirects a\n"
                "stranger to a login form — it serves a public informational page about the product, and redirects a\n"
                "*signed-in* browser on to `/board`. That is a deliberate widening of what is exposed, which is this\n"
                "node's remit; what may be *read* once you arrive did not move, and is `atlas-security`'s. Nothing\n"
                "else became reachable: `/board` and every `/assets/*` path still refuse an anonymous caller on every\n"
                "hostname.\n"
                "\n"
                "**TLS here is not cosmetic.** The session cookie is `__Host-` prefixed and therefore `Secure`,\n"
                "and `Secure` cookies never travel over plain HTTP — so a browser on a plain-HTTP origin accepts\n"
                "the login form, silently drops the cookie and loops back to the login page with no error. The\n"
                "LAN certificate exists because of that, not for tidiness.\n"
                "\n"
                "**No identity-aware proxy stands in front of Atlas.** Atlas's own login is the sole gate on every\n"
                "path, and a tunnel, where there is one, is transport only — checkable: the public `/api/*` answers\n"
                "Atlas's own `401` with `WWW-Authenticate: Bearer`, not a proxy's sign-in redirect.\n"
                "\n"
                "**Two hostnames are two paths, and a deploy verified on one is not verified on the other.** A LAN\n"
                "hostname served by the reverse proxy goes straight to uvicorn; a public hostname behind a CDN edge\n"
                "and a tunnel passes through a layer the origin does not control, and such an edge has both cached an\n"
                "authenticated asset and rewritten HTML at the origin's expense. Check every hostname after a deploy,\n"
                "or say which one you checked."
            ),
            "detail": (
                "**Two allow-lists, both from the environment, both fail-closed at import.**\n"
                "`ATLAS_LOGIN_ORIGINS` is the origin allow-list for every state-changing POST *and* the **Host**\n"
                "allow-list — anything else gets `421` before a handler runs, which kills host-header injection\n"
                "and DNS rebinding in one rule. `ATLAS_BEARER_HOSTS` narrows where a Bearer is honoured at all.\n"
                "**Whether it includes the public hostname is an installation's trade.** Leave it out and an\n"
                "invited contributor's agent that is not on the installation's network 401s from everywhere, and\n"
                "does so indistinguishably from a bad token — so it reads as \"the MCP is broken\". Put it in and a\n"
                "leaked token works from the open internet. Write the chosen trade beside the value in the\n"
                "deployment's compose override. A Bearer on a host outside the set is still refused as an *invalid\n"
                "token*, never as a distinguishable \"wrong origin\".\n"
                "\n"
                "**Caddy does one thing beyond TLS that matters.** It sets `CF-Connecting-IP` from the real\n"
                "socket peer and *replaces* rather than appends, so a LAN client cannot smuggle a false address;\n"
                "`atlas-api` believes it only because `ATLAS_TRUSTED_PROXY_CIDRS` names Caddy's network. Without\n"
                "that line every LAN request is attributed to Caddy's own container address and the per-IP rate\n"
                "limit quietly degrades into a global one — measured once, on 2026-07-31. Caddy also strips\n"
                "inbound `Cf-Access-*` headers, which Atlas never reads anyway.\n"
                "\n"
                "**Config lives in the repo**: the reverse proxy's `Caddyfile` and the compose file that runs it\n"
                "(`deploy/oss/` for a fresh install). A tunnel connector that runs from a token keeps its routes at\n"
                "the tunnel provider, not in any local file. Caddy's admin API is off.\n"
                "\n"
                "**A tunnel connector may carry more than Atlas.** When an installation's connector also routes\n"
                "other services, restarting it interrupts every route on it, not just Atlas's, and a route change is\n"
                "made at the provider, not in this repo.\n"
                "\n"
                "**Boundary:** an installation's non-production proxies (a staging stack's, say) are deployment\n"
                "concerns and sit under `atlas-ops`, not here. ADR 0022 is Accepted (2026-10-31) and partly superseded by ADR 0025: Atlas's own login, not Cloudflare Access, is the gate."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-ingress/architecture.md",
                    "kind": "architecture",
                    "title": "The path in",
                    "format": "mermaid",
                    "content": (
                        "# atlas-ingress — architecture\n"
                        "\n"
                        "The path in, in the shape every installation shares: browser or script to a TLS terminator or a\n"
                        "tunnel connector, then to `atlas-api`. An installation's own hostnames, ports and addresses belong\n"
                        "in its own operating notes, not here.\n"
                        "\n"
                        "## 1. Live\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart LR\n"
                        "  B1[\"Browser or agent,<br/>public internet\"]\n"
                        "  B2[\"Browser or agent<br/>on the local network\"]\n"
                        "  B3[\"Script on the host itself\"]\n"
                        "  subgraph edge[\"Tunnel provider (optional)\"]\n"
                        "    EDGE[\"edge — the public hostname\"]\n"
                        "  end\n"
                        "  subgraph host[\"The host\"]\n"
                        "    TUN[\"tunnel connector<br/>outbound only, no inbound port\"]\n"
                        "    PRX[\"reverse proxy<br/>TLS terminated here\"]\n"
                        "    API[\"atlas-api<br/>loopback only, or no published port\"]\n"
                        "    PG[(\"atlas-postgres\")]\n"
                        "  end\n"
                        "  B1 -->|\"https, public hostname\"| EDGE\n"
                        "  EDGE -.->|\"tunnel dialled OUT from the host\"| TUN\n"
                        "  TUN --> API\n"
                        "  B2 -->|\"https, LAN hostname\"| PRX\n"
                        "  PRX --> API\n"
                        "  B3 -->|\"http, loopback\"| API\n"
                        "  API --> PG\n"
                        "```\n"
                        "\n"
                        "Read the dotted edge carefully: the connector **dials out**. Nothing on the network listens for\n"
                        "the public path, so there is no port to find and none to forward. An installation without a public\n"
                        "hostname simply has no tunnel.\n"
                        "\n"
                        "`atlas-api` never publishes its port to the network: on loopback only, or — as the stock install in\n"
                        "`deploy/oss` does — not at all, reached only through the proxy. From anywhere else on the network\n"
                        "its port fails to connect at all — not a `401`, not a redirect, a refused connection. Anything that\n"
                        "reports that as an outage is reading the intended shape as a fault.\n"
                        "\n"
                        "**Why every browser path is TLS.** The session cookie is `__Host-atlas_session`: the prefix\n"
                        "requires `Secure`, and `Secure` cookies are never sent over plain HTTP. A browser on a\n"
                        "plain-HTTP origin will accept the login form, silently drop the cookie and land back on the\n"
                        "login page with no error to explain it. Do not \"fix\" a missing cookie by dropping `Secure`;\n"
                        "the certificate is the fix.\n"
                        "\n"
                        "## 2. Other stacks beside it\n"
                        "\n"
                        "An installation may run further copies of Atlas on the same host — a staging stack, a disposable\n"
                        "fork. Each needs its own TLS hostname for the same cookie reason (a browser login on a plain-HTTP\n"
                        "origin cannot hold a `__Host-` cookie) and its own database volume, so a migration tried there\n"
                        "cannot touch live. Those stacks are deployment concerns, contained by `atlas-ops`, not by this\n"
                        "node — they are not the way in to the product.\n"
                        "\n"
                        "## 3. The two allow-lists\n"
                        "\n"
                        "| variable | what it holds | what it decides |\n"
                        "|---|---|---|\n"
                        "| `ATLAS_LOGIN_ORIGINS` | every origin a browser uses: the public one, the LAN one, and the loopback origin the host's own callers use | the **Host** allow-list — anything else is `421` before a handler runs — and the `Origin` allow-list for every state-changing POST |\n"
                        "| `ATLAS_BEARER_HOSTS` | the LAN hostname and loopback, plus the public hostname when agents off the network must reach the API | where `Authorization: Bearer` is honoured. Leaving the public hostname out makes the public path a non-Bearer path; putting it in means a leaked token works from the open internet |\n"
                        "| `ATLAS_TRUSTED_PROXY_CIDRS` | the proxy's container network | whose `CF-Connecting-IP` is believed, which is what keeps the per-IP throttle per-IP |\n"
                        "\n"
                        "Both allow-lists raise at import if unset. There is no permissive default: an unset origin list\n"
                        "would mean \"trust the `Host` header\", which is the bug.\n"
                        "\n"
                        "The loopback origin is load-bearing in both lists rather than belt-and-braces — the machine callers\n"
                        "on the host reach `atlas-api` there, and the reverse proxy and the tunnel connector both forward\n"
                        "`Host` unchanged, so these are the exact strings the app sees.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-map",
            "type": "capability",
            "name": "Board map",
            "summary": "The legacy board's map: the horizontal D3 tree of the world and work trees, with its glyphs, zoom, collapse, cross-edge overlay and map anchoring.",
            "abstract": (
                "`atlas-map` is the legacy board's **map** — `frontend/map.js` plus the glyph it draws with, `frontend/glyph.js`. It is the picture, not the page around it: `atlas-board-shell` owns the chrome, the URL and the data fetch; the legacy panel (`frontend/panel.js`) owns what opens when you click. This node owns the SVG under `#mapsvg` and everything that decides where a mark lands and what it says.\n"
                "\n"
                "What it owns:\n"
                "\n"
                "- **The glyph.** Fill = attention, ring = completion, red badge = `attention_count`, and — on the world tree only — the attachment icons: `◎` intent, `✦` skill. `◎` is fed by `intent_count` on `/api/graph` (**own** intents only, ADR 0032), not by a child node.\n"
                "- **Layout, zoom and collapse.** A horizontal D3 tidy tree, collapsed below depth 2 at build; `d3.zoom` with fit, centre-on-node, collapse all and collapse-others; a three-stop **Detail** slider that is independent of zoom.\n"
                "- **Projection and world/work rendering.** Both tabs render through the same `buildTree`, and the differences are here: the world tree draws the work-load readout and the attachment icons, the work tree draws child counts.\n"
                "- **Map anchoring.** `withMapAnchored()` compensates the d3 pan by exactly the movement of `#canvasWrap`'s left edge, so nodes hold their screen position while the panel opens, closes or is dragged. `.wrap` is centred, so a width change moves the *left* edge too — that arithmetic is load-bearing and must not be simplified.\n"
                "- **The cross-edge overlay** (`hosted_on`, `contributes_to`, `uses`, `for`, …): three modes — off / selected / all, defaulting to **selected**, because drawing every `hosted_on` edge at once would be a near-complete fan onto one host."
            ),
            "detail": (
                "See `docs/nodes/atlas-map/architecture.md` (this node's Docs tab) for the render pipeline.\n"
                "\n"
                "**The layout is a tree; the world is a graph.** Cross-edges are drawn as bowed quadratics between the positions the tidy tree just computed — never from a force simulation, which would move nodes after paint and corrupt `withMapAnchored()`'s `getBoundingClientRect()` arithmetic. Two rules hold: cross-edges sit *beneath* the containment links, and **both endpoints must be visible** or the edge is not drawn, so a collapsed or filtered node never leaves a line pointing at where it would have been (that is an id leak, not a cosmetic bug).\n"
                "\n"
                "**The multi-root anchor renders nothing.** Under ADR 0030 the world has several roots and no owner, so `d3.hierarchy` is given a synthetic `_anchor` node with no mark, no label, no tooltip and no hit target; links out of it are dropped, and it is excluded from `NODE_INDEX`. A visible hub above the roots re-asserts on screen the ownership the data stopped claiming — that defect shipped once.\n"
                "\n"
                "**The internals are private on purpose.** `d3root`, `d3ById`, `d3selected`, `svgSel` and `zoomBehavior` get reassigned wholesale, which the `state.js` property-assignment idiom cannot carry across a module boundary, so the module exports small functions instead. Callbacks come back through `hooks` (`renderPanel`, `updateUrl`, `revealFiltered`)."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-map/architecture.md",
                    "kind": "architecture",
                    "title": "Map render pipeline",
                    "format": "mermaid",
                    "content": (
                        "# atlas-map — architecture\n"
                        "\n"
                        "`docs[]` entry: `{kind: architecture, title: \"Map render pipeline\", path: \"docs/nodes/atlas-map/architecture.md\", format: mermaid}`\n"
                        "\n"
                        "One pass of the map, from a plain nested object to pixels. Everything below `buildTree` is private to\n"
                        "`map.js`.\n"
                        "\n"
                        "## The render pipeline\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart TD\n"
                        "    Data[\"plain hierarchy from board.html<br/>id · name · type · children<br/>already pruned by the age filter\"]\n"
                        "    Build[\"buildTree(root)\"]\n"
                        "    Hier[\"d3.hierarchy → d3root<br/>d3ById · uid per node<br/>children=null below depth 2\"]\n"
                        "    Zoom[\"d3.zoom · scaleExtent 0.05–5<br/>wheel preventDefault\"]\n"
                        "    Update[\"update(source)\"]\n"
                        "    Layout[\"treeLayout(d3root)<br/>nodeSize 34 × 300\"]\n"
                        "    Enter[\"enter: glyph + label + hit rect\"]\n"
                        "    Trans[\"transition 280ms → translate(y, x)\"]\n"
                        "    XE[\"drawXEdges(visibleById)\"]\n"
                        "    Detail[\"applyDetail() — label truncation\"]\n"
                        "    Fit[\"fit(false)\"]\n"
                        "\n"
                        "    Data --> Build --> Hier --> Zoom --> Update\n"
                        "    Update --> Layout --> Enter --> Trans\n"
                        "    Trans --> XE --> Detail\n"
                        "    Build --> Fit\n"
                        "```\n"
                        "\n"
                        "`update()` is re-entered on every collapse, expand and selection. `buildTree()` is not — it wipes\n"
                        "`#mapsvg` and resets the camera, which is why `board.html`'s `rebuildMap()` captures\n"
                        "`currentTransform()` before it and calls `restoreTransform()` after.\n"
                        "\n"
                        "## What decides a mark\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart LR\n"
                        "    N[\"node data\"]\n"
                        "    A{\"_anchor?\"}\n"
                        "    R{\"type == root?\"}\n"
                        "    G[\"buildGlyph()<br/>fill = attention<br/>ring = completion<br/>badge = attention_count\"]\n"
                        "    H[\"hub circle r=7\"]\n"
                        "    Nothing[\"nothing at all —<br/>no mark, no label,<br/>no tooltip, no hit target\"]\n"
                        "    L[\"label right of the mark\"]\n"
                        "    W[\"world tab: ◎ ✦ ▤ icons<br/>+ '· N tasks · X%'\"]\n"
                        "    K[\"work tab: '(N)' child count\"]\n"
                        "\n"
                        "    N --> A\n"
                        "    A -->|yes| Nothing\n"
                        "    A -->|no| R\n"
                        "    R -->|yes| H --> L\n"
                        "    R -->|no| G --> L\n"
                        "    L --> W\n"
                        "    L --> K\n"
                        "```\n"
                        "\n"
                        "The anchor branch is the ADR 0030 rule in code: several roots, no owner, so the single starting point\n"
                        "`d3.hierarchy` needs must be invisible.\n"
                        "\n"
                        "## Cross-edges: an overlay on tree positions, never a simulation\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart TD\n"
                        "    Src[\"WORLD_XEDGES<br/>(non-contains edges from the boot fetch)\"]\n"
                        "    M{\"mode\"}\n"
                        "    T{\"type filter\"}\n"
                        "    V{\"BOTH endpoints<br/>in visibleById?\"}\n"
                        "    Draw[\"bowed quadratic<br/>beneath the contains links\"]\n"
                        "    Drop[\"not drawn\"]\n"
                        "\n"
                        "    Src --> M\n"
                        "    M -->|off| Drop\n"
                        "    M -->|\"selected — incident to focus\"| T\n"
                        "    M -->|all| T\n"
                        "    T --> V\n"
                        "    V -->|no| Drop\n"
                        "    V -->|yes| Draw\n"
                        "```\n"
                        "\n"
                        "`selected` is the default. It narrows *what is asked for*; it never relaxes the both-ends-visible\n"
                        "rule, which exists so a filtered-away node cannot leave a stub pointing at it.\n"
                        "\n"
                        "`refreshXEdges()` redraws the overlay from the last known positions **without** re-running the\n"
                        "layout — selecting a node must not make the whole map animate.\n"
                        "\n"
                        "## Map anchoring, and why the arithmetic is load-bearing\n"
                        "\n"
                        "`.wrap` is centred with `margin: 0 auto`. Insetting the body for the detail panel therefore moves the\n"
                        "canvas's **left** edge, not only its right one, and the map — drawn in canvas-relative coordinates —\n"
                        "slides across the screen with it.\n"
                        "\n"
                        "```mermaid\n"
                        "sequenceDiagram\n"
                        "    participant C as caller (open · close · drag · dblclick · arrow key)\n"
                        "    participant W as withMapAnchored\n"
                        "    participant D as d3 zoom transform\n"
                        "    C->>W: change()\n"
                        "    W->>W: before = canvasWrap.getBoundingClientRect().left\n"
                        "    W->>C: run the layout change\n"
                        "    W->>W: after = canvasWrap.getBoundingClientRect().left\n"
                        "    W->>D: translate x by (before - after)\n"
                        "```\n"
                        "\n"
                        "Never apply CSS `zoom` or `transform: scale()` to the page: both this and the pan/zoom read\n"
                        "`getBoundingClientRect()`, and a page zoom corrupts them silently.\n"
                        "\n"
                        "## Verification note\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-mcp",
            "type": "service",
            "name": "atlas-mcp",
            "summary": "The MCP server agents reach Atlas through: task-shaped tools over streamable HTTP, a thin facade over atlas-api that acts with each caller's own token and holds no logic.",
            "abstract": (
                "Its verbs are task-shaped rather than one-per-node-type: `atlas_file_work` creates a workitem with\n"
                "its acceptance criteria, parent, binding and dependencies in a single transaction, because\n"
                "`POST /api/workitem` already does exactly that. The four fields that replace WHOLESALE -- a\n"
                "workitem's body, its acceptance list, its dependencies, and its binding -- each sit behind an\n"
                "explicit flag and return the prior value, because none of them warn, and `graph_event` records that a field changed and who changed it, not what it held before.\n"
                "\n"
                "**The consequence that will look like a bug.** A hidden node and a nonexistent one return the same\n"
                "404 by design, so a missing grant surfaces as *\"that node does not exist\"* rather than as a\n"
                "permission error. **Whatever an agent reads through this service is the answer for that caller's\n"
                "principal, never evidence of what another member sees.**\n"
                "\n"
                "**Deletes.** `atlas_delete` is registered when `ATLAS_MCP_ENABLE_DELETE` is set, and succeeds only\n"
                "for a caller whose token holds `admin` (the API checks it). It is reachable only on the direct TLS\n"
                "door `:8446`, and deliberately absent from AgentGateway's allow-list: the gateway authorizes per\n"
                "ROUTE, not per caller. Which seats hold `admin` is a fact about the tokens, not about this service: read it from Settings › Agent credentials, not from here. A call without it is refused 403.\n"
                "\n"
                "The tool list arrives with the MCP connection itself (`tools/list`). `atlas_capabilities` also reports\n"
                "which write verbs this deployment has enabled, but it also returns the attribute schemas (each distinct one once, about 25 KB), so it is an expensive call to make just for the tool list."
            ),
            "detail": (
                "**What the tools cannot do, on purpose.**\n"
                "\n"
                "- **No tool sets an assignee.** Neither `atlas_file_work` nor `atlas_update_work` takes one, and `owner` is retired on workitems — the API returns 400 `owner_retired` for a payload that carries the key at all. An agent can read who work belongs to and cannot hand it over; that goes through the HTTP API or the board. Agents are not assignable either (ADR 0034).\n"
                "- **Acceptance criteria on an existing item need a person.** `atlas_update_work` can propose a new list (`propose_acceptance=True`), which a person approves or rejects on board v2; where the install sets `ATLAS_ACCEPTANCE_AGENT_WRITE=attested` it can write one with `approval={by, quote}` naming the person who approved it. A plain `set_acceptance=True` is refused 403 `acceptance_is_not_yours_to_write`.\n"
                "- **`atlas_delete` is registered only on the direct TLS door `:8446`**, never through the gateway, and needs an `admin`-scoped caller token (see the abstract).\n"
                "\n"
                "**The map before the reading: `atlas_structure`** (since 2026-10-02, over `GET /api/node/{id}/structure`). A node's `contains` ancestors, its children one level down with their own child counts, its dependencies, dependants and bindings, every node as `{id, type, name, status}` and never prose; every linked id is filtered by visibility. On live it returned a task in about a sixth of `atlas_get`'s bytes and an epic in about a tenth. The `atlas-retrieval` skill puts it between finding a node and reading it in full.\n"
                "\n"
                "**How X is connected to Y: `atlas_path`** (since 2026-10-23, over `GET /api/graph/path`). The k shortest paths between two nodes, each hop with its edge type and direction, walked over the caller's visible set only. The board's \"Connected to…\" draws the same answer.\n"
                "\n"
                "**A new tool or prompt is invisible until the gateway names it.** AgentGateway's allow-list filters prompts exactly as it filters tools: add `mcp.tool.name == \"<tool>\"` for a tool and `mcp.prompt.name == \"<prompt>\"` for a prompt, unprefixed, then restart the gateway (measured 2026-10-01: `prompts/list` was empty through the gateway until the prompt rule was added).\n"
                "\n"
                "**A tool response may carry `deploy_warning`, and it means the deployment, not the call** (since 2026-09-15). This service checks two things and repeats them on every response while either is true, with nothing added when both are current:\n"
                "\n"
                "- its own image against the source in the server checkout, mounted read-only at `/deploy-src` (`app/deploy_status.py`);\n"
                "- `atlas-api`'s equivalent, read from `GET /api/health`'s `deploy` field and cached for five minutes.\n"
                "\n"
                "How a call travels, where each piece is configured and how to change a tool are in the *guided tour* document on this node's Docs tab; what each tool takes and returns is the *tools guide*."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-mcp/architecture.md",
                    "kind": "architecture",
                    "title": "architecture.md",
                    "format": "md",
                    "content": (
                        "# atlas-mcp\n"
                        "\n"
                        "## What it is\n"
                        "\n"
                        "A Model Context Protocol server that exposes Atlas's graph and backlog as tools. It is a\n"
                        "**facade**: every verb is a call to `atlas-api` over HTTP, and no business logic, validation rule\n"
                        "or copy of the graph lives here. When a tool needs to know something the API cannot answer, the\n"
                        "answer is a missing endpoint -- not a query in this codebase.\n"
                        "\n"
                        "## How it is wired\n"
                        "\n"
                        "Every hop that carries a Bearer token is HTTPS with the certificate verified (OSS-24). Plain HTTP\n"
                        "exists only on loopback or inside one compose network, where the token never reaches a wire.\n"
                        "\n"
                        "## The address, and why it is not the obvious one\n"
                        "\n"
                        "`atlas-api` publishes its raw port on `127.0.0.1` only, so `host.docker.internal:8095` -- the\n"
                        "pattern the sibling MCP services use -- cannot reach it. Joining Atlas's compose network and\n"
                        "calling `http://atlas-api:8000` fails differently: the auth gate's host allow-list returns\n"
                        "**421 misdirected_request** for any `Host` outside `ATLAS_LOGIN_ORIGINS`, before any handler runs.\n"
                        "\n"
                        "## Authority\n"
                        "\n"
                        "No principal of its own: every caller sends its own `Authorization: Bearer atlas_svc_...`, and\n"
                        "each seat or agent has its own bot account (ADR 0025: one principal per non-browser caller, never\n"
                        "shared; ADR 0034). The shared `bot-atlas-mcp` token was revoked on 2026-10-14 (OSS-24).\n"
                        "\n"
                        "**The caller gate** (`app/caller_auth.py`) sits in front of `/mcp/`: a request with no token, a\n"
                        "malformed one, or one atlas-api does not accept gets **401** with a `WWW-Authenticate: Bearer`\n"
                        "challenge before FastMCP sees it, so no session is created and no tool is listed. atlas-api\n"
                        "decides, never this module: the gate asks `GET /api/me` with the caller's token and passes iff it\n"
                        "answers 200. A positive answer is cached for `ATLAS_MCP_AUTH_CACHE_S` (default 60 s), keyed by a\n"
                        "SHA-256 of the token; a negative one is not cached. atlas-api unreachable is a **503**: fail\n"
                        "closed. The code keeps an opt-in migration fallback, `ATLAS_MCP_ALLOW_SHARED_TOKEN=1` (default\n"
                        "off, warns at startup), which lets a request with no Authorization header act as `ATLAS_TOKEN`; a\n"
                        "request that presents a token is always checked as itself.\n"
                        "\n"
                        "**IdP access tokens, opt-in** (STORY-oss-25, ADR 0041 §9). With `ATLAS_OAUTH_RESOURCE` set (the same\n"
                        "value atlas-api reads, from the one `.env`), atlas-mcp is an OAuth 2.1 protected resource:\n"
                        "`app/oauth_resource.py` serves RFC 9728 metadata at `/.well-known/oauth-protected-resource/mcp`\n"
                        "(plus the root fallback and a trailing-slash twin), naming `ATLAS_OIDC_ISSUER` in\n"
                        "`authorization_servers`; the caller gate's 401 then carries `resource_metadata=` pointing at it, and\n"
                        "`scope=` when `ATLAS_MCP_OAUTH_SCOPES` is set. A JWT-shaped token passes the door to the same\n"
                        "`GET /api/me` check, so validation (signature, issuer, audience, person, scopes) stays in atlas-api\n"
                        "(ADR 0033; `atlas-identity` → *IdP access tokens and the read audit*). The API's actionable refusal\n"
                        "(\"sign in with SSO once\") is passed on to the caller. With the variable unset there is no\n"
                        "metadata route, the challenge is the plain `Bearer realm=\"atlas-mcp\"`, and a JWT is refused at the\n"
                        "door without reaching the API.\n"
                        "\n"
                        "**Read-audit labels.** Each tool call sends `X-Atlas-Tool` (the tool name) and `X-Request-ID` (one id\n"
                        "per call) to atlas-api, which records them in `audit_read` when `ATLAS_AUDIT_READS=1`. Both are\n"
                        "caller-asserted labels; the API derives who the caller is from the token, never from them.\n"
                        "\n"
                        "Scopes became enforceable on 2026-08-15 -- before that `Principal.has_scope` had no call sites\n"
                        "and `guard_write` allowed every non-session principal, so a `read` token could cascade-delete a\n"
                        "node. `atlas_delete` is registered only when `ATLAS_MCP_ENABLE_DELETE` is set, and is otherwise\n"
                        "absent from `tools/list` rather than refusing when called, so a client cannot see a capability\n"
                        "the deployment does not have.\n"
                        "\n"
                        "## Transport and session settings\n"
                        "\n"
                        "## Things that will bite\n"
                        "\n"
                        "## Where the code is\n"
                    ),
                },
                {
                    "path": "docs/nodes/atlas-mcp/how-it-works.md",
                    "kind": "runbook",
                    "title": "How atlas-mcp works — a guided tour",
                    "format": "md",
                    "content": (
                        "# How atlas-mcp works — a guided tour\n"
                        "\n"
                        "A learning guide: how an agent's tool call reaches Atlas, where every piece is configured, what\n"
                        "FastMCP is and how a tool is written. The `architecture.md` doc beside this one holds the design\n"
                        "decisions and the traps; this one explains the machine. Written 2026-09-14, updated 2026-10-25.\n"
                        "**What each tool takes and returns** is the third document, the *tools guide* (`tools.md`).\n"
                        "\n"
                        "## 1. The words\n"
                        "\n"
                        "## 2. One tool call, end to end\n"
                        "\n"
                        "Take `atlas_get(id=\"atlas-mcp\")` from this session:\n"
                        "\n"
                        "## 3. Where everything lives\n"
                        "\n"
                        "## 4. The code, file by file\n"
                        "\n"
                        "```\n"
                        "mcp/app/\n"
                        "  main.py          the web server: a FastAPI app, /health for Docker, FastMCP mounted at /mcp\n"
                        "  mcp_server.py    creates the FastMCP instance (allowed hosts, 4 h idle timeout), defines\n"
                        "                   atlas_health, registers every tools_*.py\n"
                        "  caller_auth.py   the caller gate: no valid Bearer token, no MCP\n"
                        "  settings.py      feature flags (ATLAS_DISABLED_FEATURES) and the allowed hosts/origins\n"
                        "  descriptions.py  every tool's description, in one file\n"
                        "  client.py        the only code that talks to atlas-api: base URL, the caller's token, audit headers, error translation\n"
                        "  envelope.py      the {\"ok\": ...} wrapper every tool returns\n"
                        "  tools_read.py    get, tree, outline, node_search, search, structure, path, intents, doc_read, capabilities, context, adr_search, adr_get\n"
                        "  tools_work.py    file_work, update_work\n"
                        "  tools_graph.py   node/edge/doc/intent writes, and atlas_delete\n"
                        "  tools_comments.py comments, comment_write\n"
                        "  tools_session.py save_work, and the signoff prompt\n"
                        "  tools_setup.py   setup_guide\n"
                        "  tls.py, oauth_resource.py, deploy_status.py, deploy_warning.py, distribution.py\n"
                        "                   the outbound TLS rule, OAuth resource metadata, the stale-deploy check and its\n"
                        "                   warning, and the install's default values\n"
                        "mcp/verify_*.py    small check scripts, no network\n"
                        "```\n"
                        "\n"
                        "**`main.py` in three moves.** It builds a FastAPI app. It mounts `mcp.streamable_http_app()` at\n"
                        "`/mcp`, so FastMCP answers every MCP message under that path. And it starts FastMCP's\n"
                        "`session_manager` in the app's lifespan — without that, every request fails with *\"Task group is\n"
                        "not initialized\"*.\n"
                        "\n"
                        "**`mcp_server.py`** creates `FastMCP(\"atlas\", streamable_http_path=\"/\")`. That path setting is why\n"
                        "the address is `/mcp/` and not `/mcp/mcp/`. It then imports each `tools_*.py` and calls its\n"
                        "`register(mcp)`. FastMCP is wrapped in a thin class that gives each tool its description from\n"
                        "`descriptions.py`, looked up by the function's name; a tool with no entry there fails at import.\n"
                        "The same wrapper labels each tool call, so every API request it makes carries `X-Atlas-Tool` and\n"
                        "`X-Request-ID`.\n"
                        "\n"
                        "## 5. How FastMCP makes a tool\n"
                        "\n"
                        "This is the whole of `atlas_get`:\n"
                        "\n"
                        "```python\n"
                        "def register(mcp):\n"
                        "    def _atlas_get(id: str):                      # the work: one API call\n"
                        "        return client.get(f\"/api/node/{id}\")\n"
                        "\n"
                        "    @mcp.tool()                                   # ← registers the function below, with\n"
                        "    def atlas_get(id: str) -> dict:               #   DESCRIPTIONS[\"atlas_get\"] as its description\n"
                        "        return envelope(_atlas_get)(id)           # ← errors become {\"ok\": false, ...}\n"
                        "```\n"
                        "\n"
                        "From that one decorator, FastMCP derives everything `tools/list` shows:\n"
                        "\n"
                        "| MCP field | Comes from |\n"
                        "|---|---|\n"
                        "| tool name | the function name, `atlas_get` |\n"
                        "| description | `descriptions.py`, by that name (until 2026-10-23, the docstring) |\n"
                        "| input schema | the parameters and their type hints — `id: str` becomes a required string |\n"
                        "\n"
                        "So **the description is not documentation for developers. It is text every agent receives** when it\n"
                        "loads the tool, in every session, whether or not it calls it. Keep it to what the tool is for and\n"
                        "the rules that do harm when missed. Every parameter, default and refusal goes in the tools guide,\n"
                        "which an agent reads only when it needs it, and the reasoning goes in `#` comments, which nothing\n"
                        "sends. `mcp/verify_tool_descriptions.py` enforces a size budget and that the registry and\n"
                        "`descriptions.py` list the same tools.\n"
                        "\n"
                        "**How errors travel.** atlas-api answers an error with a status code and `{\"error\", \"detail\",\n"
                        "\"field\"}`. `client.py` turns that into an `AtlasError` with a stable type — 400 `invalid_request`,\n"
                        "403 `forbidden`, 404 `not_found`, 409 `conflict`, 413 `too_large`. `envelope()` catches it and\n"
                        "returns `{\"ok\": false, \"error\": {\"type\", \"message\"}}`, so an agent can branch on the type.\n"
                        "\n"
                        "**Two rules FastMCP imposes.** Never write `from __future__ import annotations` in a tools module:\n"
                        "FastMCP reads the real type hints to build the schema, and that import turns them into strings.\n"
                        "And call `envelope(...)` inside the function rather than decorating with it, or FastMCP sees\n"
                        "`*args` instead of the parameters.\n"
                        "\n"
                        "## 6. The house rule: a facade, not a second brain\n"
                        "\n"
                        "atlas-mcp holds no business logic and no copy of the graph (the `facade-not-second-brain` intent\n"
                        "on this node). Each tool is one API call, plus the parameter defaults it chooses. If a tool needs\n"
                        "something the API cannot answer, the fix is a new API endpoint. For example, `atlas_tree`'s size\n"
                        "limits live in atlas-api as `strict` and `max_bytes`; the tool only asks for them.\n"
                        "\n"
                        "## 7. The gateway's part\n"
                        "\n"
                        "```yaml\n"
                        "mcpAuthorization:\n"
                        "  rules:\n"
                        "    - 'mcp.tool.name == \"atlas_get\"'\n"
                        "    - 'mcp.tool.name == \"atlas_node_search\"'\n"
                        "```\n"
                        "\n"
                        "- **It authorizes per tool, not per caller.** Every client behind the gateway sees the same tools.\n"
                        "  That is why `atlas_delete` exists on the direct TLS door 8446 but is deliberately never listed here.\n"
                        "- **It reads the file only at start.** A new tool needs its line, then `docker restart agentgateway`.\n"
                        "- **A changed tool needs nothing.** A new parameter or description shows up in the gateway's\n"
                        "  `tools/list` once atlas-mcp is rebuilt (measured 2026-09-14).\n"
                        "- **It holds no token.** Each client's own `Authorization: Bearer` passes through to atlas-mcp, which\n"
                        "  checks it against atlas-api and answers 401 without one.\n"
                        "\n"
                        "## 8. Changing things\n"
                        "\n"
                        "**Add a new tool:** the same steps, plus an entry in `descriptions.py`, a section in the tools\n"
                        "guide, a line in the gateway's allow-list and a gateway restart.\n"
                        "\n"
                        "**The tool needs a new API parameter:** deploy atlas-api first, then atlas-mcp. Deployed the other\n"
                        "way round, atlas-api ignores the unknown parameter (and, by default, names it in a `warnings`\n"
                        "entry; under `ATLAS_STRICT_API=1` it is a 422).\n"
                        "\n"
                        "**Why the `mcp` step is separate.** atlas-mcp is its own compose project, so rebuilding atlas-api\n"
                        "never rebuilds it. A stale image looks like a broken tool, not like a stale deploy — check it with\n"
                        "`check-deployed.sh`.\n"
                        "\n"
                        "## 9. Looking around yourself\n"
                        "\n"
                        "## 10. Things that surprise people\n"
                        "\n"
                        "- **One tool, two names.** `atlas_get` on the direct door 8446, `atlas_atlas_get` through the homelab gateway.\n"
                        "- **\"Node not found\" can mean \"not granted\".** A hidden node and a missing one return the same 404,\n"
                        "  by design.\n"
                        "- **A 421 from atlas-mcp** means the `Host` the request arrived with is not in\n"
                        "  `ATLAS_MCP_ALLOWED_HOSTS` — a new door in front of atlas-mcp needs its name added.\n"
                        "- **A session idle for 4 hours is closed**; the client re-initializes.\n"
                        "- **A tool can be missing on purpose.** `ATLAS_DISABLED_FEATURES=save_work` removes\n"
                        "  `atlas_save_work`; `atlas_delete` exists only where `ATLAS_MCP_ENABLE_DELETE` is set.\n"
                        "- **The SDK is pinned exactly.** `mcp` 2.0 removed `mcp.server.fastmcp`, so an unpinned rebuild\n"
                        "  would fail on its first import.\n"
                        "- **The compose file pins `name: atlas-mcp`.** Without it, Compose would create a second project\n"
                        "  named `mcp` and fail on the container-name collision — after a build that looks successful.\n"
                    ),
                },
                {
                    "path": "docs/nodes/atlas-mcp/tools.md",
                    "kind": "api",
                    "title": "The atlas MCP tools — the guide",
                    "format": "md",
                    "content": (
                        "# The atlas MCP tools — the guide\n"
                        "\n"
                        "Every tool's description, as an agent sees it in its tool list, is short on purpose: it is sent to every calling agent in every session, whether or not the tool is ever called. It says what the tool is for and the rules that do harm when missed. **This guide is the rest**: every parameter, default, cap and refusal. Read the section for the tool you are about to use.\n"
                        "\n"
                        "Read it with `atlas_doc_read(node_id=\"atlas-mcp\", path=\"docs/nodes/atlas-mcp/tools.md\")`.\n"
                        "\n"
                        "Every tool returns the same envelope: `{\"ok\": true, \"result\": …}` or `{\"ok\": false, \"error\": {\"type\": …, \"message\": …}}`. Every call acts as YOUR credential, so what you can read is not what another principal can. **An unknown id and one you cannot see return the same 404**, everywhere.\n"
                        "\n"
                        "**Ignored fields are named, not swallowed.** A write (`atlas_file_work`, `atlas_update_work`, `atlas_node_write`) that sends a field the API does not apply still succeeds, and its result gains `warnings: [{\"field\", \"code\", \"detail\"}, …]`, one entry per ignored field (`code` is `unknown_field` or `ignored_field`). Nothing ignored, no `warnings` key. An install that sets `ATLAS_STRICT_API=1` refuses the same request with a **422** naming the field, before anything is written. Read `warnings` when it is there: it is a change you asked for that did not happen.\n"
                        "\n"
                        "For which tool to call first, the reading order is the `atlas-retrieval` skill: outline → node_search → structure → get → doc_read.\n"
                        "\n"
                        "The short descriptions are in the repo at `mcp/app/descriptions.py`; changing what a tool does means changing its entry there and its section here.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Reading\n"
                        "\n"
                        "### atlas_health\n"
                        "Liveness and sanity check against atlas-api (`GET /api/health`). Returns the whole `/api/health` answer: `node_count` and `edge_count` as a plausibility check, not a health verdict, `event_count`, and the warnings `token_warnings`, `undocumented_assets`, `name_register_warnings`, `edge_type_warnings` and `deploy`, plus `retrieval` and `cert_warnings` where they apply. The warnings are normally empty.\n"
                        "\n"
                        "### atlas_outline\n"
                        "The `contains` tree as **indented text**, cheaply: \"what do we actually have?\" (`GET /api/graph/outline`). One line per node: `id [type]`. The loop is this, then `atlas_get` the node you need.\n"
                        "\n"
                        "- `families` defaults to `object,bridge` (the world). The work hierarchy shares the `contains` edge, so an unfiltered walk falls through an epic into every task. `families=all` includes the backlog.\n"
                        "- `root` starts at one node; missing and hidden both 404 the same.\n"
                        "- `max_depth`: 0 or omitted is unlimited, otherwise the answer says `truncated: true` where it cut.\n"
                        "- `names=true` appends ` · <name>` to each line.\n"
                        "- A filter matching nothing is an empty `text`, not a fault: check `count`.\n"
                        "\n"
                        "### atlas_context\n"
                        "\"Tell me about X\" in **one bounded call** (`GET /api/context?about=…`, ADR 0044): the first tool for an open question, in your own words or in Swedish. It returns a package of **pointers**, never prose; open what it points at with `atlas_get`, `atlas_doc_read` or `atlas_adr_get`.\n"
                        "\n"
                        "- **`seeds`**: the best-matching nodes, exactly what `atlas_node_search` returns. `mode` defaults to `hybrid` here (semantic + full text); where the retrieval add-on is off or down it falls back to full text and the response says so in `degraded`. `families` defaults to the world (`object,bridge`); `all` includes the backlog.\n"
                        "- **`neighbourhood`**: one hop from the seeds along `contains`, `requires` and the binding edges, each item with `via: {from, edge, relation}` (`parent`, `child`, `requires`, `required_by`, `works_on`, …). A node next to two seeds is listed once.\n"
                        "- **`impact`**: what REQUIRES the seeds, transitively, to `impact_depth` (default 2, max 5), each with its `depth`. It answers \"what breaks if this changes\".\n"
                        "- **`intents`**: the intents that apply to the seeds, their own and inherited, as `{id, kind, slug, title, owner, applies_to, inherited}`. `intents_full=true` adds each `statement`; read them before you change something.\n"
                        "- **`decisions`**: ADRs ranked on the question's words and the seeds' names, `{id, project, title, status, legacy}`.\n"
                        "- **`docs`**: the documents to read next, `{node_id, path, title, kind, matched}`; documents the search matched come first, `external: true` marks one Atlas does not hold.\n"
                        "- **Budgets are parameters**: `seeds` (5, max 20), `neighbours` (20, max 100), `impact` (20, max 100), `intents` (20, max 100), `decisions` (5, max 20), `docs` (10, max 50). **Every part says `truncated: true` when its budget cut it**, so nothing goes missing silently. `bytes` is the package's size: about 7-10 KB with the defaults on this instance.\n"
                        "- **What you may not read is absent from every part**, and a dependency chain never runs through a node you cannot see. A bad parameter is a 400 naming it.\n"
                        "\n"
                        "- **Returns pointers, not content**: id, type, name, summary, `matched_in`, `doc_paths`, snippet, rank, best first. Read with `atlas_get` or `atlas_doc_read(node_id, path)`.\n"
                        "- With `mode=\"fulltext\"` (or calling `/api/search` directly) terms are ANDed: query one or two distinctive words, not a phrase. Stopwords are dropped silently. A match found only in a document has rank 0.0, which says nothing about quality.\n"
                        "- Labels match exactly, not stemmed: a hit is an item carrying the whole query as one label, or every word of it as labels.\n"
                        "- A hit found only in a workitem's comments has `matched_in: [\"comment\"]` and a snippet from them. Comments match through the full-text half only; the semantic index does not embed them.\n"
                        "- `families` defaults to `object,bridge`; `all` searches everything; `workitem` is the backlog (`atlas_search` fits that better).\n"
                        "- `page_size` defaults to 10 (max 200); `page` for more.\n"
                        "- A node you may not read is absent from the results AND from `total`.\n"
                        "- **`mode`** (ADR 0044, the retrieval add-on): omitted means `hybrid` in this tool; `fulltext` is word matching only. `hybrid` adds **semantic** matching: the question is embedded and compared with every world node's card and every served document passage, and the result is fused with full text by rank (RRF). Use it for a question in your own words or in Swedish, where full text's ANDed terms find nothing. `semantic` is the vector half alone. A hit the semantic half found has `\"semantic\"` in `matched_in`, its passage's `doc_paths`, a `similarity`, and a snippet from the best-matching passage; every item carries a fused `score`. The visibility rule is the same: what you may not read is neither returned nor counted, and an un-published document is never matched.\n"
                        "- **`degraded`**: when the semantic half cannot answer (the add-on is off on that install, unavailable, its embedder unreachable, or its index still empty), the response says why in `degraded`; `hybrid` then returns the full-text result and `semantic` returns nothing. Say so rather than trust a miss.\n"
                        "- **`expand=true`** adds up to 10 graph neighbours of the top 3 results, one hop along `contains`, `requires` and the binding edges, each with `via: {from, edge, direction}` and half its seed's score. Deeper is `atlas_structure`'s job.\n"
                        "- ADRs are a search source too: a project whose ADRs match comes back as the project node with `matched_in` containing `adr` and their ids in `adr_ids`. For ADRs themselves, `atlas_adr_search` is the better tool.\n"
                        "\n"
                        "### atlas_search\n"
                        "List, filter and search **workitems** only (`GET /api/workitems`): epic, story, task. World nodes never appear here; use `atlas_get`, `atlas_node_search` or `atlas_outline` for them.\n"
                        "\n"
                        "- Filters compose: `type` and `status` (both comma-separated; status is bucketed like a board click), `parent` (the immediate `contains` container), `binding` (the `works_on`/`serves` target), `owner`, `priority` and `label`, `q` (full-text over body, acceptance, result and comments).\n"
                        "- `priority`: comma-separated, any of `low`, `medium`, `high`, `critical`. `label`: comma-separated; an item carrying ANY of them matches.\n"
                        "- **A listing**: no `parents`, `bindings` or `acceptance`. Call `atlas_get` per item for those. It does carry each item's abstract, so keep `page_size` small when you need only ids.\n"
                        "- `total` is every match, `count` is this page. Page again (`page=page+1`) rather than assume you have everything.\n"
                        "\n"
                        "### atlas_structure\n"
                        "Where a node sits and what it links to, **without prose**. Read it before `atlas_get` to see the map.\n"
                        "\n"
                        "Returns the node, its `contains` ancestors (nearest first), its children one level down (each with its own child count), what it `depends_on`, what depends on it, and its binding. Each node is `id, type, name, status`. `children_limit` defaults to 50; a longer list says `children_truncated` rather than cutting silently.\n"
                        "\n"
                        "### atlas_get\n"
                        "One node, complete, by id (`GET /api/node/{id}`): a workitem or a world node, one shape.\n"
                        "\n"
                        "- Returns `body`, `result`/`result_heading` (separate from `body`), `acceptance`, `parents`, `bindings` (`works_on`/`serves`), `dependencies`/`dependants`, and `intents.{own, inherited}`. A workitem also carries `priority` (or null) and `labels`; a project node with a task-id key carries `key`.\n"
                        "- **Full by default**, because inherited intents are the constraints on changing this node. Read it full before you change something.\n"
                        "- Ask for less when only orienting: `prose=none|summary|abstract|full`, `intents=all|own|none`. Inherited intents repeat verbatim on every node below their author, so `intents=own` saves the most.\n"
                        "- A workitem's `summary` field can be stale; read `status`.\n"
                        "\n"
                        "### atlas_tree\n"
                        "A bounded **neighbourhood** around ONE node, minimal unless you ask (`GET /api/graph`). It is not a discovery tool: `atlas_node_search` answers \"which node knows X\", `atlas_outline` \"what exists\", `atlas_get` \"this node, complete\".\n"
                        "\n"
                        "- `focus` is **required**; omitting it is refused with a pointer to `atlas_outline`. Hidden and nonexistent both 404 the same.\n"
                        "- Every parameter defaults to minimal and is opt-in; raise one at a time:\n"
                        "  - `depth=1` (clamped at 20)\n"
                        "  - `edges=\"contains\"` or `\"all\"`\n"
                        "  - `prose=none|summary|abstract|full`\n"
                        "  - `work=none|counts|items`\n"
                        "  - `meta=false`; `true` adds owner, source, attributes, assignee, completed_at.\n"
                        "- Over 100 nodes or 32 KB is refused as `too_large`, naming what to narrow.\n"
                        "\n"
                        "### atlas_path\n"
                        "How two nodes connect (`GET /api/graph/path`): the shortest paths between `from_id` and `to_id`.\n"
                        "\n"
                        "- `max`: how many paths, default 3, cap 10. They are the k shortest, shortest first.\n"
                        "- `hops`: the longest path considered, default and cap 8.\n"
                        "- `edges`: a comma list of edge types to walk; default all.\n"
                        "- Each path is `{length, hops: [{from, to, edge_type, direction, edge}]}`. `direction` is `out` (the edge points from → to), `in` (it points back), or `none` (undirected). `nodes` maps every id on a path to its type and name.\n"
                        "- **The walk never passes through a node you cannot see**, so a route through a hidden node does not exist for you.\n"
                        "- No route within `hops` is an empty `paths` list, not an error. A missing or hidden end is the usual 404.\n"
                        "- Size: about 400 B for one 2-hop path, 900 B for three; the 10-path, 8-hop ceiling is about 10 KB.\n"
                        "\n"
                        "### atlas_intents\n"
                        "The intent corpus, **owner side only**: one row per statement, at the node that authored it (`GET /api/intents`).\n"
                        "\n"
                        "- `node_id` filters to statements OWNED by that node. Inherited intents come from `atlas_get`'s `intents.{own, inherited}`; a node that only inherits returns empty here. So use this for an audit and `atlas_get` for work.\n"
                        "- `kind`: `constraint` (a bug when violated), `principle` (a defect), `non-goal` (a removal), `outcome` (direction, no work).\n"
                        "- This only reads; `atlas_intent_write` writes.\n"
                        "\n"
                        "### atlas_doc_read\n"
                        "Read one document attached to a node (`GET /api/node/{id}/doc?path=`).\n"
                        "\n"
                        "- `path` must **exactly** match one of that node's own `attributes.docs[].path` entries (`atlas_get` lists them). Any other path is a 404; there is no content lookup.\n"
                        "- A document declared EXTERNAL answers 200 with `external: true` and NO `content`. That is a complete answer, not a failure to retry.\n"
                        "\n"
                        "### atlas_capabilities\n"
                        "This deployment's write vocabulary plus this server's own tool registry (`GET /api/vocabulary`).\n"
                        "\n"
                        "- `vocabulary`: the legal node types, edge-type family rules, settable statuses, workitem types, the work hierarchy ladder (`workitem_ladder`, top first) and intent kinds, read live, so a value here is one the write API will accept.\n"
                        "- Each distinct attributes schema appears once under `attributes_schemas`; types cite it by `attributes_schema_ref`.\n"
                        "- `tools`: this server's registered tool names; always matches `tools/list`.\n"
                        "- About 25 KB. It is not the way to get the tool list; that arrives with the connection.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Decisions (ADRs)\n"
                        "\n"
                        "A record has a **`source`**:\n"
                        "- `git`: synced one way from a repo's `docs/adr/` file by `tools/adr_sync.py`. The file is the truth; the record carries `repo`, `path` and `commit`, and the legacy number.\n"
                        "- `atlas`: a decision recorded in Atlas itself (ADR 0044: new decisions are born in Atlas). It has no file and no legacy number, starts `proposed`, and a human accepts it. The sync never creates, changes or orphans an atlas record.\n"
                        "\n"
                        "A record in a project you cannot see does not exist for you: it is absent from lists and totals and gets the same 404 as an unknown id.\n"
                        "\n"
                        "### atlas_adr_search\n"
                        "Find decision records (`GET /api/adrs`). Returns **pointers, not the text**: id, project, seq, `legacy` (\"ADR 0041\", or null for an atlas record), number, title, status, date, `orphaned`, `source`.\n"
                        "\n"
                        "### atlas_adr_get\n"
                        "One decision record, complete (`GET /api/adrs/{ref}`): status and its raw line, date, the links both ways (`supersedes` / `superseded_by`, `amends` / `amended_by`, each resolved to `{id, project, title, status}`, or `{id, missing: true}` when the other side is not in the registry), `source`, provenance for a git record, and the markdown `content`.\n"
                        "\n"
                        "- `id` takes a per-project id (`AT014`) or a legacy number (`0041`, `ADR-0041`).\n"
                        "- A legacy number held by two projects is refused **409** with the candidates; add `project=` to choose. Today's corpus has no such collision, but nothing enforces the shared sequence.\n"
                        "- It is a large read for a long ADR (tens of KB); search first and get only the one you need.\n"
                        "\n"
                        "**Writing** is not an MCP tool. Git records are written by `tools/adr_sync.py` (`--config deploy/homelab/adr_sources.json` in the homelab, dry run by default, `--apply` with a token file). An atlas record is created with `PUT /api/adrs/new` (`project`, `title`, `content`; the server allocates the next id under a lock, and a project's first record must name its `prefix`), and updated with `PUT /api/adrs/{id}`. A git write to an id an atlas record holds is refused `held_by_atlas`.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Setup guide\n"
                        "\n"
                        "### atlas_setup_guide\n"
                        "The first-run setup guide's interview: a versioned question set (topics such as guardrails, CI/CD, environments, identity, secrets and the work hierarchy) whose every answered topic becomes a **Proposed** Atlas-native ADR. A thin wrap of the guide's API in `backend/routers/setup.py`, which holds every rule.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Work\n"
                        "\n"
                        "### atlas_file_work\n"
                        "Create a workitem (`POST /api/workitem`): the node, its acceptance, parent edge, binding and `depends_on`, in ONE transaction.\n"
                        "\n"
                        "- `type`: one of the workitem types `GET /api/vocabulary` lists (today epic, story or task). atlas-mcp reads that list once per process; an unknown type is refused locally with the list (`invalid_type`), and when the vocabulary cannot be read the call is refused with `vocabulary_unavailable` rather than guessed. The parent filter uses the same list and its `workitem_ladder`.\n"
                        "- A task takes **no `id`** (the server mints it); an epic or story NEEDS a slug `id`. Both are refused locally if wrong.\n"
                        "- **The minted id** is `TASK-n`, unless the task's project has a key (see `atlas_node_write`'s `key`): then it is `KEY-n`, such as `OPS-7`. The project is the one the task resolves to through its binding, else its parent, then the nearest `contains` ancestor of type project. The id is decided once; moving the item never renumbers it.\n"
                        "- `status` defaults to `pending` (settable values under `atlas_update_work`). `proposed` is board-only and refused locally.\n"
                        "- `priority`: `low`, `medium`, `high` or `critical`, or omitted for none. Anything else is refused locally with the list.\n"
                        "- `labels`: a list of short tags. Stored stripped and lowercased, duplicates dropped; each at most 40 characters, at most 20, no comma (the `label` filter splits on it), none empty.\n"
                        "- `body` is the spec. No `## Result` or `## Outcome` heading: that is `atlas_update_work`'s `result`, and it is refused locally here.\n"
                        "- `binding` (a world node id) is the operator's call: suggest one, do not guess.\n"
                        "- There is no owner or assignee argument: file unassigned and hand off on the board.\n"
                        "- Workitems are visible to invitees: never name employer systems in them.\n"
                        "\n"
                        "### atlas_update_work\n"
                        "Update a workitem (`PATCH /api/workitem/{id}`).\n"
                        "\n"
                        "- **Plain fields, no flags:**\n"
                        "  - `status`: pending, analyze, in_progress, blocked, done, cancelled. `proposed` is refused locally.\n"
                        "  - `result`: the outcome, in its OWN store; it never touches `body`. Omitted = unchanged, a string = set, `None` = DELETE. Omitting and sending null differ.\n"
                        "  - `priority`: one of `low|medium|high|critical`; `None` clears it; omitted leaves it.\n"
                        "  - `labels`: a list REPLACES the whole set (same rules as `atlas_file_work`); `None` or `[]` clears; omitted leaves it. To add one label, read the current list and send it with the new one.\n"
                        "- **Everything else REPLACES WHOLESALE** and only behind its own `set_<field>=True` flag:\n"
                        "  - `body`\n"
                        "  - `acceptance`: see below.\n"
                        "  - `depends_on`\n"
                        "  - `binding`: `None` deletes every `works_on` edge; a value sets one and 409s if one exists.\n"
                        "- `parent` (a plain field, no flag) **moves** the item: an id makes that epic or story its one container, `None` detaches it (top level), omitted leaves it. The API judges the move before writing anything, so a refusal changes nothing; each is a 400 naming the rule:\n"
                        "  - `parent_is_self`: an item cannot contain itself;\n"
                        "  - `parent_order`: the parent must sit strictly above the child in epic > story > task (levels may be skipped, never repeated or inverted);\n"
                        "  - `parent_cycle`: the new parent is one of the item's own work descendants;\n"
                        "  - `child_order`: a `type` change that would put an existing child at or above the item (a story with tasks cannot become a task).\n"
                        "  The `before` image includes the old parent, so a wrong move can be undone with one more call.\n"
                        "- Returns each changed field's prior value as `before`.\n"
                        "\n"
                        "**Acceptance criteria on an existing item** define what the item is judged against, so the agent doing the work cannot simply rewrite them (the intent \"Agents propose; humans commit\" on `atlas`). Three paths:\n"
                        "\n"
                        "1. **Propose** (works on every install): `propose_acceptance=True` with `acceptance=[…]` and an optional `proposal_note`. This changes nothing; it stores a pending proposal (`POST /api/workitem/{id}/acceptance-proposal`) that board v2 shows on the item as a diff with **Approve** and **Reject**. Only a person's session can approve or reject; approving applies the list and records the approver. One pending proposal per item: a new one supersedes the old. Proposing the current list is a 409.\n"
                        "2. **Attested write** (only where the install sets `ATLAS_ACCEPTANCE_AGENT_WRITE=attested`): `set_acceptance=True`, `acceptance=[…]` and `approval={\"by\": \"<a person's username>\", \"quote\": \"<their words approving this exact change>\"}`. `by` must be an existing, active person (not an agent account). The change is recorded with approver, quote, agent and the old and new lists, and the board shows it. Use it only with a real, explicit approval from that person in this conversation; never self-authored.\n"
                        "3. **Plain `set_acceptance=True` without approval**: refused 403 `acceptance_is_not_yours_to_write` on every install, as it always was. Under `attested` the 403 carries a hint naming the setting and the `approval` field.\n"
                        "\n"
                        "`approval` without `acceptance` is a 400; contradictory combinations (propose plus set, approval plus propose) are refused before any call is made.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Comments\n"
                        "\n"
                        "An epic, story or task carries a conversation: its comments, oldest first, each with `id`, `body` (markdown), `author` (`id`, `username`, `name`), `created_at` and `edited_at` (null until edited). Board v2 shows them in the node panel's Comments tab. Only a workitem has comments: any other node is a 409 `not_a_workitem`, and an item you cannot see is the usual 404.\n"
                        "\n"
                        "Comments are searchable: `atlas_search`'s `q` and `atlas_node_search` (full text only) find an item by a word in its comments, with `matched_in: [\"comment\"]`.\n"
                        "\n"
                        "### atlas_comments\n"
                        "The comments on one workitem (`GET /api/workitem/{id}/comments`): `comments` and `count`. A read; it writes nothing.\n"
                        "\n"
                        "### atlas_comment_write\n"
                        "Add a comment (`id` + `body`, `POST /api/workitem/{id}/comments`) or edit one (`comment_id` + `body`, `PATCH /api/comment/{id}`).\n"
                        "\n"
                        "- Pass exactly one of `id` and `comment_id`; both or neither is refused locally, and so is an empty `body`. The API caps `body` at 10,000 characters (400 naming `body`).\n"
                        "- **The author is your credential's account**, taken from the token, never from the call: an agent comments as itself, not as its person.\n"
                        "- **Only the author edits a comment.** Anyone else's is a 403 `not_your_comment`. An edit replaces the body and sets `edited_at`; the replaced text is kept in `workitem_comment_revision` for audit; no route returns it.\n"
                        "- Writing needs a `write` scope, and the item's editor role where project RBAC is on.\n"
                        "- **Deleting is not an MCP tool.** The author or an instance owner deletes on the board (`DELETE /api/comment/{id}`, which costs `admin`).\n"
                        "- Each add, edit and delete is a `node.comment.*` event in the item's History, naming the comment id, never its text.\n"
                        "- A comment is visible to everyone who can see the item, invitees included: never name employer systems in one.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Writing the graph\n"
                        "\n"
                        "### atlas_node_write\n"
                        "Create a node if `id` does not exist, else patch the fields given (`POST /api/node` or `PATCH /api/node/{id}`). Creating needs `type` and `name`.\n"
                        "\n"
                        "- `attributes` is **read-modify-write by default** (`attributes_mode=\"merge\"`). The API assigns it wholesale, so sending only the keys you name would drop the rest; the merge prevents that. `\"replace\"` overwrites it.\n"
                        "- `abstract` and `detail` writes replace the whole field: read what is there first.\n"
                        "- `key`: a **project's** work-item key, the prefix of its tasks' ids (`KEY-n`). Omitted leaves it, a string sets it, `None` clears it. Opt-in: with no keyed project, every task is `TASK-n` as before.\n"
                        "  - 2-10 characters, uppercase A-Z and digits, starting with a letter (`ABC`, `OPS2`); the instance's own prefixes (`TASK`, `STORY`, `EPIC`, …) are reserved. 400 `invalid_key`.\n"
                        "  - Only a `project` node carries one: 400 `key_not_project`.\n"
                        "  - Owner or maintainer only (the project's `maintainer` role where RBAC is on; otherwise the instance owner or an `admin`-scoped token): 403.\n"
                        "  - Unique: 409 `key_taken`. Fixed once a `KEY-n` id has been minted with it: 409 `key_immutable`.\n"
                        "- It cannot create a work-hierarchy edge (epic → story); only `atlas_file_work` can.\n"
                        "\n"
                        "### atlas_edge_write\n"
                        "Create one edge between two EXISTING nodes (`POST /api/edge`). It cannot create a work-hierarchy edge (epic → story); use `atlas_file_work`.\n"
                        "\n"
                        "Two 409s:\n"
                        "- `family_mismatch`: the src/dst types do not match this edge type's families (`atlas_capabilities` has the rules).\n"
                        "- `binding_exists`: a workitem may have at most one `works_on` edge. Delete the old one with `atlas_edge_delete` first.\n"
                        "\n"
                        "### atlas_edge_delete\n"
                        "Delete one edge by its integer id (`DELETE /api/edge/{id}`). No cascade: only this row. To delete a NODE and everything incident to it, see `atlas_delete`.\n"
                        "\n"
                        "### atlas_doc_write\n"
                        "Write a node document's content and/or publish it: two SEPARATE acts.\n"
                        "\n"
                        "- `content=…` upserts the body. `publish=True` adds the `attributes.docs[]` entry that makes the document route serve it. Content can exist unpublished.\n"
                        "- It NEVER publishes a path whose content this call did not write or find already served, so pass both together to publish safely.\n"
                        "- `path` must start with `docs/nodes/`.\n"
                        "- The response says which act(s) happened, and `overwritten: true` if content already existed.\n"
                        "\n"
                        "### atlas_intent_write\n"
                        "Create a node intent (`node_id` + `slug`, `kind`, `title`, `statement`) or patch one (`intent_id` + whatever changed).\n"
                        "\n"
                        "### atlas_intent_delete\n"
                        "Delete one node intent by its integer id.\n"
                        "\n"
                        "### atlas_delete\n"
                        "Destructive delete across node, edge, doc or intent. **No soft delete, no undo.** Needs your credential's `admin` scope, and the tool is not listed unless the deployment enables it.\n"
                        "\n"
                        "- `kind=\"node\"` cascades: the node, every incident edge, its acceptance, result, documents, intents and comments. It REQUIRES `expected_edge_count`: call once without it and let it refuse; the refusal names the real count. Do not compute the count from `atlas_get`, whose lists miss edge types.\n"
                        "- `kind=\"doc\"` removes the `docs[]` entry BEFORE the content, so the node never advertises a dead link.\n"
                        "- `kind=\"edge\"` and `kind=\"intent\"` are single-row deletes.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Session\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-mcp-read",
            "type": "component",
            "name": "Read tools",
            "summary": "The tools that read Atlas and change nothing: the graph, search, recorded knowledge and server information.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-read-graph",
            "type": "component",
            "name": "Graph reads",
            "summary": "Tools for what exists and how it connects: the tree, one node's position, a neighbourhood, the path between two nodes, and a full node read.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-read-knowledge",
            "type": "component",
            "name": "Knowledge reads",
            "summary": "Tools for what has been written down and decided: intents, node documents and architecture decision records.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-read-search",
            "type": "component",
            "name": "Search tools",
            "summary": "Tools for finding things without knowing their id: a bounded context answer, search over world nodes, and filtered listing of workitems.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-read-server",
            "type": "component",
            "name": "Server info",
            "summary": "Tools that describe the install itself: whether it is up, and the vocabulary and tools it offers.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-session",
            "type": "component",
            "name": "Session tools",
            "summary": "Tools that guide a whole session rather than one read or write: signing off an agent's work and the first-run setup interview.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-adr-get",
            "type": "component",
            "name": "ADR get",
            "summary": "One decision record by id, with its status, date, source, links both ways and the text.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-adr-search",
            "type": "component",
            "name": "ADR search",
            "summary": "Finds architecture decision records by words, id, project, status or number.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-capabilities",
            "type": "component",
            "name": "Capabilities",
            "summary": "This install's legal node types, edge types, statuses, workitem types and intent kinds, and the server's tool list.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-comment-write",
            "type": "component",
            "name": "Comment write",
            "summary": "Adds a comment to a workitem, or edits one of your own.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-comments",
            "type": "component",
            "name": "Comments",
            "summary": "The comments on one workitem, oldest first, each with its author and id.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-context",
            "type": "component",
            "name": "Context",
            "summary": "Tell me about X, in one call: the best-matching nodes, their neighbours, what depends on them, the intents that apply, related decisions and docs to read next.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-delete",
            "type": "component",
            "name": "Delete",
            "summary": "Permanently deletes a node with its edges, an edge, a document or an intent, with no undo. Off unless enabled, admin-only and never offered through the gateway.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-doc-read",
            "type": "component",
            "name": "Doc read",
            "summary": "Reads one of a node's documents by the path the node lists.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-doc-write",
            "type": "component",
            "name": "Doc write",
            "summary": "Writes a node document, publishes it, or does both in one call.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-edge-delete",
            "type": "component",
            "name": "Edge delete",
            "summary": "Deletes one edge by its id.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-edge-write",
            "type": "component",
            "name": "Edge write",
            "summary": "Creates one edge between two existing nodes.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-file-work",
            "type": "component",
            "name": "File work",
            "summary": "Creates a workitem with its acceptance, parent, binding, dependencies, priority and labels in one transaction.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-get",
            "type": "component",
            "name": "Get",
            "summary": "One node, complete: prose, acceptance, result, parents, bindings, dependencies and intents, own and inherited.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-health",
            "type": "component",
            "name": "Health",
            "summary": "Is atlas-api up? Returns its health report: node and edge counts as a plausibility check, plus any token, certificate, schema or deploy warnings.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-intent-delete",
            "type": "component",
            "name": "Intent delete",
            "summary": "Deletes one node intent by its id.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-intent-write",
            "type": "component",
            "name": "Intent write",
            "summary": "Creates an intent on a node or patches an existing one.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-intents",
            "type": "component",
            "name": "Intents",
            "summary": "Lists intents where they are authored, optionally filtered by node or kind; a node's inherited intents come from Get.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-node-search",
            "type": "component",
            "name": "Node search",
            "summary": "Which world node knows about X? Hybrid semantic and full-text search over names, prose and served documents; returns pointers, not content.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-node-write",
            "type": "component",
            "name": "Node write",
            "summary": "Creates a node, or patches only the fields given on one that exists.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-outline",
            "type": "component",
            "name": "Outline",
            "summary": "The contains tree as indented text, one line per node: what exists.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-path",
            "type": "component",
            "name": "Path",
            "summary": "How two nodes connect: the shortest paths between them, each hop with its edge type and direction.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-save-work",
            "type": "component",
            "name": "Save work",
            "summary": "Sign-off: your writes since a given time, and the procedure for recording the conversation's work in Atlas.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-search",
            "type": "component",
            "name": "Search",
            "summary": "List and filter workitems by type, status, parent, binding, priority, label or text.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-setup-guide",
            "type": "component",
            "name": "Setup guide",
            "summary": "The first-run setup interview: returns the next topic to ask the person, and records one topic's answers as a proposed ADR.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-structure",
            "type": "component",
            "name": "Structure",
            "summary": "Where a node sits, without prose: ancestors, children with their child counts, dependencies both ways, and binding.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-tree",
            "type": "component",
            "name": "Tree",
            "summary": "A bounded neighbourhood around one node, minimal by default and widened one setting at a time.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-tool-update-work",
            "type": "component",
            "name": "Update work",
            "summary": "Updates a workitem: plain fields directly; body, acceptance, dependencies and binding replace wholesale behind a flag, with the prior values returned. Acceptance changes need a person: propose, or an attested approval.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-work",
            "type": "component",
            "name": "Work tools",
            "summary": "Tools for the backlog: file a workitem, update one, and read or write its comments.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-write",
            "type": "component",
            "name": "Write tools",
            "summary": "The tools that change the world model: nodes, edges, documents and intents, including permanent deletes.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-write-graph",
            "type": "component",
            "name": "Graph writes",
            "summary": "Tools that create, change and remove nodes and edges.",
            "docs": [],
        },
        {
            "id": "atlas-mcp-write-knowledge",
            "type": "component",
            "name": "Knowledge writes",
            "summary": "Tools that write a node's documents and create, change or remove its intents.",
            "docs": [],
        },
        {
            "id": "atlas-op-config",
            "type": "component",
            "name": "Configuration and flags",
            "summary": "How an install is configured: ATLAS_* environment variables, the opt-in board configuration panel where the environment wins, and ATLAS_DISABLED_FEATURES switches.",
            "docs": [],
        },
        {
            "id": "atlas-op-crunch",
            "type": "component",
            "name": "Crunch week",
            "summary": "An opt-in, time-limited release mode: a recorded window during which the generated pipeline lets changes that pass CI skip staging and pre-production; it ends by itself.",
            "docs": [],
        },
        {
            "id": "atlas-op-install",
            "type": "component",
            "name": "Install and upgrade",
            "summary": "The self-hosted install path: clone, configure .env, generate secrets and certificates, start the compose stack, create the first owner, and later upgrade to a new release.",
            "docs": [],
        },
        {
            "id": "atlas-op-migrate",
            "type": "component",
            "name": "Schema upgrade",
            "summary": "atlas-migrate upgrade: a one-shot command that creates missing tables, columns and indexes using the API's own startup schema steps, changing no data.",
            "docs": [],
        },
        {
            "id": "atlas-op-setup",
            "type": "component",
            "name": "Setup guide",
            "summary": "A first-run interview, run by an AI agent, about how the organisation works; each answered topic becomes a proposed ADR and feeds the first CI/CD pipeline.",
            "docs": [],
        },
        {
            "id": "atlas-operator-features",
            "type": "component",
            "name": "Operator features",
            "summary": "What the person running an Atlas install uses to set it up and keep it going: install and upgrade, schema migration, configuration and feature flags, the setup guide and crunch week.",
            "docs": [],
        },
        {
            "id": "atlas-ops",
            "type": "capability",
            "name": "Delivery & operations",
            "summary": "How Atlas is checked, delivered and run: CI pipelines, delivery scripts and admin CLI, non-production environments, client packs, and operator install, config and upgrade features.",
            "abstract": (
                "`atlas-ops` is how a change gets from a branch to the live board, and how it is checked on the way:\n"
                "the verify suite, the staging stack, the deploy path and the dependency pins. It contains the\n"
                "**non-production deployments** (under `atlas-ops-environments`, each an `instance_of` its live\n"
                "counterpart) and the CI pipelines and tooling (under `atlas-ops-delivery`). Which containers\n"
                "those are is a question for the tree beneath this node, not for this sentence: it said \"the three\n"
                "staging containers\" until 2026-08-23, by which point there were six. It covers both sides of running\n"
                "Atlas: **delivery** of Atlas itself (`atlas-ops-delivery`, `atlas-ops-environments`) and the\n"
                "**operator-facing parts of the product** (`atlas-operator-features`, `atlas-client-packs`, and the\n"
                "admin CLI under `atlas-tooling`). `atlas-backend` owns what the API does.\n"
                "\n"
                "**One command runs the suite.** `cd backend && python verify_all.py` discovers every\n"
                "`backend/verify_*.py`, starts **one disposable `postgres:16-alpine` that the run owns** (uuid name,\n"
                "free port, removed at exit via `atexit`), runs them serially and prints one verdict with a\n"
                "per-script breakdown. Two concurrent runs therefore get two containers. That shape comes from\n"
                "2026-08-10, when two agents ran scripts against one shared container, one dropped the other's schema\n"
                "mid-run, and both reported green on the re-run.\n"
                "\n"
                "**There is deliberately no skip path.** An unreachable database is a hard exit, and discovering zero\n"
                "scripts is a hard exit too — an empty suite must never render as green. `backend/testdb.py` has\n"
                "exactly three outcomes and no fourth: honour `ATLAS_TEST_DB_URL` (reachability checked, unreachable\n"
                "is fatal), start a disposable container, or raise `TestDatabaseUnavailable`. **SQLite is gone from\n"
                "this repo entirely** (TASK-1044) — no scratch file, no fallback, no `if dialect == \"postgresql\"`\n"
                "branch. It mattered more than it sounds: the SQLite path for `allocate_task_id` was `MAX(id)+1`, the\n"
                "read-then-write race the Postgres sequence exists to eliminate, so a green run against it proved the\n"
                "opposite of what it appeared to prove.\n"
                "\n"
                "**The suite runs by itself now** (2026-08-23, answering TASK-1070). `.github/workflows/verify.yml`\n"
                "runs it on every push and pull request, alongside a `node --check` over every frontend module and a\n"
                "check that each asset appears in `ASSET_FILES`. It starts its own disposable Postgres on the runner\n"
                "rather than using a `services:` block, so a green CI run means the same thing as a green local run.\n"
                "\n"
                "**What CI does not do is block anything.** A branch ruleset requiring the checks was created and\n"
                "then deliberately removed while this is a proof of concept (a project decision, 2026-08-23), so a red\n"
                "run is information rather than a barrier — which is also what TASK-1070 asked for, since blocking a\n"
                "merge on a suite nobody had been running is a change in working agreement rather than a wiring\n"
                "detail. Staging, measurement and closing the ticket still happen because somebody remembers; the deploy itself refuses a commit CI has not verified."
            ),
            "detail": (
                "**A non-production stack is a second compose project, not a flag.** A staging copy of Atlas on the\n"
                "same host as live stays apart from it by four separate mechanisms: `container_name` (two projects\n"
                "cannot both own `atlas-api`), the volume namespace (compose prefixes the `atlas-pgdata` volume with\n"
                "the project name — separate disks, so a migration tried on staging cannot touch live), a separate\n"
                "default network, and the port, which is passed in as `ATLAS_PORT` rather than re-declared in the\n"
                "overlay because compose **concatenates** port lists and a second declaration would publish live's\n"
                "port too.\n"
                "\n"
                "Two hard-won details in the staging script, `tools/stage.sh`. It carries the untracked `.env` across\n"
                "the directory swap — losing `ATLAS_PASSWORD_PEPPER` / `ATLAS_TOKEN_PEPPER` invalidates every staging\n"
                "account and token at once. And it sends the commit subject over stdin rather than interpolating it\n"
                "into the remote shell: one apostrophe in a subject line aborts under `set -e` *after* the swap and\n"
                "*before* the rebuild — a half-applied deploy that then reports \"nothing staged yet\", because that is\n"
                "the line which writes the stamp `--status` reads.\n"
                "\n"
                "**A third: the overlay ships from the staged ref, like the code.** `stage.sh` reads `deploy/staging.override.yml` from the branch being staged and refuses a ref without one, so an overlay change (such as the `frontend/` bind-mount below) must be committed on that branch.\n"
                "\n"
                "**The frontend loop without a rebuild:** uncomment the `frontend/` bind-mount in\n"
                "`deploy/staging.override.yml` (committed on the branch) and the container serves the staged tree from disk. It replaced\n"
                "`tools/preview.py` (deleted, TASK-1072), which served assets without the `ASSET_FILES` allowlist and\n"
                "so could render green a module that 404s on live.\n"
                "\n"
                "Live deploys run through `tools/deploy.sh` (ADR 0046): an agent runs it, a person approves each live run, and it refuses unless CI's `verify` passed for the commit being pulled. The procedure is `agent/procedures/release.md`; the pipeline\n"
                "and what is missing from it are in the runbook attached to this node."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-ops/cicd.md",
                    "kind": "runbook",
                    "title": "Delivery: how a change gets from a branch to live",
                    "format": "md",
                    "content": (
                        "# Delivery: how a change gets from a branch to live\n"
                        "\n"
                        "The honest summary first: **the checks run by themselves now; the shipping does not.** Several\n"
                        "things run without being asked — the gitleaks pre-commit hook, and since 2026-08-23 a GitHub\n"
                        "Actions workflow on every push and pull request. But that workflow **reports; it does not\n"
                        "block**, and staging, measurement, deploy and closing the ticket still happen because a person\n"
                        "or an agent remembers. This document describes the process as it actually is, marks what is\n"
                        "automated, and names what is missing.\n"
                        "\n"
                        "`agent/procedures/release.md` is the step-by-step to follow when shipping. This is the\n"
                        "map of the whole pipeline and the reasoning behind it.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## The pipeline\n"
                        "\n"
                        "```\n"
                        "  branch off master\n"
                        "        │\n"
                        "        ├─ [AUTOMATED]  gitleaks pre-commit hook — blocks a secret-bearing commit\n"
                        "        │\n"
                        "        ├─ [AUTOMATED]  GitHub Actions, .github/workflows/verify.yml — on every push\n"
                        "        │               and every PR: suite, parse, docs, gate-integrity. Reports.\n"
                        "        │               Deliberately NOT a merge gate — see below\n"
                        "        │\n"
                        "        ├─ parse check     no build step exists, so nothing else catches a syntax error\n"
                        "        │\n"
                        "        ├─ verify suite    cd backend && python verify_all.py     (one command, one exit code)\n"
                        "        │\n"
                        "        ├─ staging         bash tools/stage.sh  →  a second stack, own DB volume\n"
                        "        │\n"
                        "        ├─ measure         in a real browser, asserting numbers, not \"looks fine\"\n"
                        "        │\n"
                        "        ├─ DEPLOY         bash tools/deploy.sh api|mcp|both — agent runs it, a person approves each run; refuses unless verify is green for the commit\n"
                        "        │\n"
                        "        ├─ live            merge → push → tools/deploy.sh (pulls on the server and rebuilds)\n"
                        "        │\n"
                        "        └─ close the loop  PATCH /api/workitem/{id} — status, result, completion date, one call\n"
                        "```\n"
                        "\n"
                        "### 1. Trunk is `master`\n"
                        "\n"
                        "Branch from it, merge back into it. It is what `origin/HEAD` points at, what a fresh clone\n"
                        "gives you, and what the server deploys. There is no develop branch and no release branch.\n"
                        "\n"
                        "### 2. Parse check\n"
                        "\n"
                        "There is no build step and no bundler — `frontend/board.html` is an ES-module shell served\n"
                        "as-is. Nothing will catch a syntax error for you, so run `node --check` over every module\n"
                        "before staging. CI's `parse` job does the same thing, and also checks that every module,\n"
                        "the stylesheet and `board.html` are named in `ASSET_FILES` — a module missing from that\n"
                        "allowlist renders fine anywhere that serves the directory and 404s on live.\n"
                        "\n"
                        "### 3. The verify suite\n"
                        "\n"
                        "```bash\n"
                        "cd backend && python verify_all.py\n"
                        "```\n"
                        "\n"
                        "The `backend/verify_*.py` scripts, **discovered from disk rather than listed**, run serially\n"
                        "against **one disposable Postgres that the run owns**. They cover the attention engine, the\n"
                        "rollup rules, the auth gate, per-node access, the read and write APIs, id allocation, the\n"
                        "record model, binding inheritance, the graph event log, the status lifecycle, the edge-type\n"
                        "registry, the agent identity link and the loop rails. `verify_all.py` prints the set it found\n"
                        "and a per-script breakdown — **that listing is the answer to \"how many\", not a number written\n"
                        "here.** A whole tranche that verified the migration from markdown into Postgres was deleted by\n"
                        "TASK-1059 on 2026-08-12 along with the files it read.\n"
                        "\n"
                        "**`backend/TESTING.md` is the reference** — what each script proves, where the test data\n"
                        "comes from, and the conventions for adding a check. Two things worth knowing here:\n"
                        "\n"
                        "- **There is no skip path.** An unreachable database is a hard failure, never a silent\n"
                        "  pass. A suite that reports success when it could not reach a database is worse than one\n"
                        "  that will not start.\n"
                        "- **A run owns its database.** Two concurrent runs get two containers. On 2026-08-10 two\n"
                        "  agents shared one container and one dropped the other's schema mid-run; both then\n"
                        "  reported green. That is now structurally impossible rather than merely survivable.\n"
                        "\n"
                        "### 4. Staging\n"
                        "\n"
                        "```bash\n"
                        "bash tools/stage.sh <branch>\n"
                        "```\n"
                        "\n"
                        "A separate compose project — own containers, own network, own database volume — so it\n"
                        "cannot damage live. Review anything cookie- or TLS-shaped on staging's TLS hostname, never on\n"
                        "its plain-HTTP port (a plain-HTTP origin cannot hold the session cookie); that origin is also\n"
                        "the only place to look at the mobile layout on a real phone.\n"
                        "\n"
                        "**Staging deploys a committed ref, not your working tree.** Uncommitted edits are silently\n"
                        "absent. `stage.sh` warns; read its output.\n"
                        "\n"
                        "### 5. Measure it\n"
                        "\n"
                        "A deploy that returns 200 on `/api/health` has proved only that the process started.\n"
                        "`release.md` lists the specific assertions — the map holding position across a resize\n"
                        "drag, `#mapLegend` staying on screen, `history.length` constant across tab switches, the\n"
                        "text scale reaching the SVG, deep links restoring the right tab. Every regression this\n"
                        "board has had was found this way.\n"
                        "\n"
                        "### 6. Going live — ask first\n"
                        "\n"
                        "Live deploys run through `tools/deploy.sh` (ADR 0046). Once merged and pushed, an agent runs it\n"
                        "and a person approves each live run; it refuses unless CI's `verify` passed for exactly the commit\n"
                        "being pulled, then pulls on the server, rebuilds and runs the deployed check. Every run is logged:\n"
                        "\n"
                        "```bash\n"
                        "bash tools/deploy.sh api|mcp|both\n"
                        "```\n"
                        "\n"
                        "The server pulls; nothing is pushed to it. Then verify live the same way staging was\n"
                        "verified — **and on every hostname.** A LAN hostname served by the reverse proxy goes straight\n"
                        "to uvicorn; a public hostname behind a CDN edge passes through a layer that has both rewritten\n"
                        "HTML in flight and served an authenticated stylesheet out of its cache. A change verified on one\n"
                        "is not verified on the other.\n"
                        "\n"
                        "### 7. Closing the loop\n"
                        "\n"
                        "The step that gets skipped, because nothing visibly breaks when it is:\n"
                        "\n"
                        "```bash\n"
                        "curl -sS -X PATCH \"$ATLAS_URL/api/workitem/TASK-1234\" \\\n"
                        "  -H \"Authorization: Bearer $ATLAS_TOKEN\" -H 'Content-Type: application/json' \\\n"
                        "  -d '{\"status\":\"done\",\"result\":\"live at <sha>. Measured: …\"}'\n"
                        "```\n"
                        "\n"
                        "One call sets the status, stamps the completion date server-side and records the result\n"
                        "prose — and `result` never touches `body`, so recording an outcome cannot destroy the spec\n"
                        "it is recording against (TASK-1064).\n"
                        "\n"
                        "**Do not edit a markdown file.** `backlog/` was deleted by TASK-1059 on 2026-08-12, and nothing had read it since TASK-1058; there\n"
                        "is no backlog sync any more (TASK-1057) — the board reads Postgres directly, so the API\n"
                        "call *is* the update. No second step, no container restart.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## What is actually automated\n"
                        "\n"
                        "| | |\n"
                        "|---|---|\n"
                        "| **GitHub Actions — `.github/workflows/verify.yml`** | since 2026-08-23, on every push and every pull request. Its jobs include **`suite`**, which runs `verify_all.py` and starts its own disposable `postgres:16-alpine` on the runner; **`parse`**, which runs `node --check` over every `frontend/*.js` and checks the `ASSET_FILES` allowlist; **`docs`**, which runs `verify_docs.py`, the one verify script that needs no database; **`gate-integrity`**, which refuses a loop-authored pull request that DELETES a `backend/verify_*.py` or anything under `.github/`; plus route, MCP, network and compose checks — the workflow file is the list |\n"
                        "| other workflows | images and pins, security scans (SAST, SBOM/SCA, DAST), install smoke, scrub and public export — see `atlas-ci-pipelines` |\n"
                        "| **gitleaks pre-commit** | blocks secret-bearing commits. Dormant until `pre-commit install` is run once per clone |\n"
                        "| everything else | staging, measurement and closing the ticket run when a person or an agent remembers |\n"
                        "\n"
                        "**There is deliberately no `services: postgres` block in the workflow.** That would mean\n"
                        "setting `ATLAS_TEST_DB_URL` — the one variable `backend/TESTING.md` warns against, and the\n"
                        "configuration two concurrent runs raced in on 2026-08-10 — and CI would then exercise a\n"
                        "different path from the one every developer runs. `verify_all.py` starting its own container\n"
                        "on the runner is what makes a green CI run mean the same thing as a green local run. The cost\n"
                        "is one image pull per run.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## What is missing, and what is decided\n"
                        "\n"
                        "**CI runs, and it is deliberately NOT a merge gate.** TASK-1070 — \"decide where the suite runs,\n"
                        "and wire it\" — chose **GitHub Actions**, and it shipped 2026-08-23. The rejected candidates\n"
                        "were the server the live board runs on (the Docker and the Postgres are already there, but a\n"
                        "suite there competes with live) and a pre-push hook (cheapest and matching the gitleaks\n"
                        "precedent, but local-only and skippable, which is how it would eventually be skipped). Actions is\n"
                        "off-box, visible on the commit without anyone opening a log, and cannot be skipped quietly.\n"
                        "\n"
                        "**A workflow reports; a branch protection rule enforces**, and the rule does not exist. That is\n"
                        "a decision, not an omission (the project owner, 2026-08-23): **a red run is information**, and\n"
                        "re-enabling the gate is a repo ruleset on GitHub rather than a change to any file in this repo.\n"
                        "Two settings would have to be turned on there — requiring `suite` and `parse` before a merge to\n"
                        "master, and restricting who may push to master — and neither lives here. Read anything that\n"
                        "claims \"the loop cannot disable the gate\" as unmet until both exist: an agent that can push\n"
                        "here can edit the workflow, so the real wall is a `permissions.deny` rule on `.github/**` and\n"
                        "`backend/verify_*.py` in the operator's settings, plus branch protection.\n"
                        "\n"
                        "**And `gate-integrity` is a tripwire, not a wall.** It identifies loop-authored commits by the\n"
                        "`Co-Authored-By: Claude` trailer, which anything wanting to evade it would simply omit, and it\n"
                        "was narrowed on 2026-08-24 to refuse only DELETION — it had fired on three consecutive pull\n"
                        "requests and all three were legitimate work, and a check that is red on every honest change is\n"
                        "one people learn to merge past. What it therefore no longer catches is the likelier move:\n"
                        "leaving a verify script in place and neutering what is inside it. It prints modifications; it\n"
                        "does not fail on them. Reading the diff of a gate file is still a human job.\n"
                        "\n"
                        "**No scheduled backup, and none is wanted.** `pg_dump` is out of scope by explicit decision\n"
                        "(the project owner, 2026-08-09), reaffirmed since with the size of it in front of him: the\n"
                        "backlog lives in one Postgres, in one Docker volume, on one disk, losing the server means losing\n"
                        "the work, and that is accepted. **Do not propose a dump — including as a side effect of something\n"
                        "else.** An earlier version of this document proposed folding a live `pg_dump` into the CI run to\n"
                        "get an off-box copy \"without anyone having to remember\"; that was a re-opening of a closed\n"
                        "decision dressed as a wiring detail, and it is removed. `tools/snapshot.sh` is a one-time verified\n"
                        "recovery point taken before widening an agent's permissions, not a practice, and it does not\n"
                        "reopen this either.\n"
                        "\n"
                        "**~~No way to author a node document.~~ Fixed by TASK-1076, 2026-08-12.** ADR 0029 moved\n"
                        "node documentation into Postgres but left authoring on `docs/nodes/<id>/*.md` plus\n"
                        "`import_node_docs.py`, a one-time importer that TASK-1059 deletes along with the\n"
                        "directory — so documentation was about to become read-only while the read path carried\n"
                        "on working. `PUT` and `DELETE /api/node/{id}/doc` close that, under the same\n"
                        "`guard_write` as every other write route. Storing stays separate from serving: a stored\n"
                        "document is not served until the node's `docs[]` allowlist names it.\n"
                        "\n"
                        "**This document was the first thing stored through that route.** After TASK-1059 deleted\n"
                        "`docs/nodes/`, it exists only in Postgres — so every edit to it from here on goes through\n"
                        "the route it describes.\n"
                        "\n"
                        "---\n"
                        "\n"
                        "## Environments\n"
                        "\n"
                        "| | |\n"
                        "|---|---|\n"
                        "| live | the installation's public hostname (through a tunnel, where it has one) and its LAN hostname (through the reverse proxy). Each accepts Bearer only if it is in `ATLAS_BEARER_HOSTS` |\n"
                        "| staging | a second compose project with its own TLS hostname and its own DB volume |\n"
                        "| a fork | a third compose project — its own network, volume and peppers — holding a scrubbed copy of live's real graph |\n"
                        "| verification | a disposable `postgres:16-alpine` per run, named by uuid, removed on exit — plus the same suite on a CI runner |\n"
                        "\n"
                        "**A leaked token is usable from wherever its host honours Bearer.** When an installation puts its\n"
                        "public hostname in `ATLAS_BEARER_HOSTS` — needed when invited contributors' agents are not on its\n"
                        "network, since without it every call they make 401s indistinguishably from \"no such thing\" and\n"
                        "presents as \"the MCP is broken\" — a leaked token works from the open internet. Do not assume the\n"
                        "public host refuses Bearer; read the setting. **Assuming it refuses is the dangerous direction to be\n"
                        "wrong in.** The accepted trade, where it is made: the credential is 256 bits of CSPRNG, stored as a\n"
                        "keyed digest, scope-enforced, expiring, revocable on the next request and bounded by its person's\n"
                        "grants — and the host already accepts passwords, which are weaker per credential than any of that.\n"
                        "The operational consequence is the one to carry: **mint a token into a file, never onto stdout.** A\n"
                        "token that reaches a session transcript works from anywhere until it is rotated.\n"
                    ),
                },
                {
                    "path": "docs/nodes/atlas-ops/environments.md",
                    "kind": "architecture",
                    "title": "The environments, and what isolates them",
                    "format": "mermaid",
                    "content": (
                        "# atlas-ops — architecture\n"
                        "\n"
                        "The environments an Atlas installation runs, what actually isolates them from each other, and what\n"
                        "each one is for. The step-by-step for shipping is the sibling runbook on this node; this is the\n"
                        "topology behind it.\n"
                        "\n"
                        "## 1. The environments\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart TB\n"
                        "  subgraph live[\"LIVE — the installation's own compose project, a real checkout\"]\n"
                        "    LA[\"atlas-api<br/>loopback only, or no published port\"]\n"
                        "    LP[(\"atlas-postgres<br/>volume &lt;project&gt;_atlas-pgdata\")]\n"
                        "    LC[\"reverse proxy (TLS)\"]\n"
                        "    LF[\"tunnel connector (optional, outbound)\"]\n"
                        "    LC --> LA\n"
                        "    LF --> LA\n"
                        "    LA --> LP\n"
                        "  end\n"
                        "  subgraph stg[\"STAGING — a second compose project (optional)\"]\n"
                        "    SA[\"atlas-api, its own container name and port\"]\n"
                        "    SP[(\"its own postgres volume\")]\n"
                        "    SC[\"its own TLS proxy\"]\n"
                        "    SC --> SA\n"
                        "    SA --> SP\n"
                        "  end\n"
                        "  subgraph fork[\"FORK — a third compose project (optional)\"]\n"
                        "    FA[\"atlas-api, its own container name and port\"]\n"
                        "    FP[(\"its own postgres volume<br/>SCRUBBED COPY of live's graph\")]\n"
                        "    FC[\"its own TLS proxy\"]\n"
                        "    FC --> FA\n"
                        "    FA --> FP\n"
                        "  end\n"
                        "  subgraph ver[\"VERIFICATION — a workstation or a CI runner\"]\n"
                        "    VR[\"python verify_all.py\"]\n"
                        "    VP[(\"disposable postgres:16-alpine<br/>uuid name, free port, removed at exit\")]\n"
                        "    VR --> VP\n"
                        "  end\n"
                        "```\n"
                        "\n"
                        "Deployed stacks may share one machine and **nothing else** — no database, no network, no container\n"
                        "name, no port, and in a fork's case not even the peppers (§2). That is the whole claim, and each half\n"
                        "of it is a separate mechanism.\n"
                        "\n"
                        "Verification shares nothing with any of them: it never touches a deployed database, and since\n"
                        "2026-08-12 it reads no files either, so it needs Docker and this repo and nothing more. The same\n"
                        "command runs on a CI runner on every push and pull request — deliberately with no\n"
                        "`services: postgres` block, so CI exercises the identical path.\n"
                        "\n"
                        "**Each deployed stack answers a different question.**\n"
                        "\n"
                        "- **Staging** answers *does this migration survive a populated database*, which is why its world\n"
                        "  should be an old, populated one rather than a fresh install.\n"
                        "- **A fork** holds a **scrubbed copy of live's real graph** — every node, edge and workitem, with\n"
                        "  the session and token tables emptied and every `password_hash` nulled before it was ever\n"
                        "  reachable. It is the only one that answers *does this behave correctly against the real graph*,\n"
                        "  and it exists so an agent loop can do things that would be unacceptable anywhere else. Its peppers\n"
                        "  are its own, so **no credential crosses between stacks**.\n"
                        "- **Live** answers nothing; it is what everyone else is looking at.\n"
                        "\n"
                        "## 2. What isolates the stacks from each other\n"
                        "\n"
                        "| | mechanism | fails how, if removed |\n"
                        "|---|---|---|\n"
                        "| containers | `container_name` overridden in each non-live overlay | compose refuses — two projects cannot own the name `atlas-api` |\n"
                        "| database | the base `atlas-pgdata` volume is namespaced by compose project | two stacks would share one disk; a migration tried on one would land on the other |\n"
                        "| network | a separate compose project gets its own default network | `atlas-postgres` would resolve across the boundary |\n"
                        "| port | `ATLAS_PORT` passed in by the script that brings the stack up, **not** re-declared in an overlay — the CI job `compose` fails if one does | compose concatenates port lists, so a stack would publish live's port too and collide |\n"
                        "| bind address | `${ATLAS_BIND:-127.0.0.1}`: loopback unless a stack has a reason to be reached from the network | a stack published to the network is reachable without its TLS proxy |\n"
                        "| credentials | a fork has its **own** `ATLAS_PASSWORD_PEPPER` and `ATLAS_TOKEN_PEPPER` in its own `.env` | the peppers are mixed into every stored verifier and token digest, so sharing them would make a live credential work on a stack an agent is allowed to break |\n"
                        "\n"
                        "One thing is deliberately **not** symmetrical: `ATLAS_ALLOW_SESSION_MINT` is set on non-live stacks\n"
                        "and omitted on live, so `admin session mint` hands an agent a real browser session there and refuses\n"
                        "on live. Verifying the board means driving it in a browser and every route is gated; the alternative\n"
                        "was a standing password for a shared account.\n"
                        "\n"
                        "## 3. Where a branch goes\n"
                        "\n"
                        "A non-live stack does not track a branch. It ships a `git archive` of a branch's **committed** tree\n"
                        "into a fresh directory (so a file deleted on the branch is really gone), swaps it in, carries the\n"
                        "untracked `.env` across, writes its stamp over stdin rather than interpolating it into a remote\n"
                        "shell, and brings the stack up with its own `ATLAS_PORT`. It then holds that frozen snapshot until\n"
                        "someone ships again. **Only live is a real checkout.**\n"
                        "\n"
                        "The overlay is read from the ref being shipped, not from the working tree, and a ref without one is\n"
                        "refused (#171). An overlay change you want staged, such as an uncommented `frontend/` bind-mount,\n"
                        "must be committed on that branch.\n"
                        "\n"
                        "The health poll uses `/healthz`, **not** `/api/health`: every `/api/*` route needs a principal since\n"
                        "TASK-967, so an unauthenticated probe there gets a `401` and the script would report FAILED on a\n"
                        "perfectly healthy stack.\n"
                        "\n"
                        "Live is the opposite direction — the server pulls, nothing is pushed to it,\n"
                        "through `tools/deploy.sh`, which pulls on the server, rebuilds and runs the deployed check; a person approves each run (ADR 0046). After a\n"
                        "deploy, check that every image is built from current source and that the raw API port still refuses\n"
                        "the network.\n"
                        "\n"
                        "## 4. The suite\n"
                        "\n"
                        "`cd backend && python verify_all.py`. The `backend/verify_*.py` scripts, **discovered from disk\n"
                        "rather than listed** — so the runner's own per-script breakdown is the answer to \"which ones\" and\n"
                        "\"how many\", not a number written here — run serially against one disposable Postgres the run owns.\n"
                        "Between scripts isolation is free: the runner passes its DSN down as `ATLAS_TEST_DB_URL`, so each\n"
                        "child takes `testdb`'s branch 1, and `start()` resets the `public` schema on entry while `stop()`\n"
                        "resets it again on exit.\n"
                        "\n"
                        "Three properties the runner is required to keep, and the reason for each:\n"
                        "\n"
                        "- **No skip path.** An unreachable database is a hard exit; zero scripts discovered is a hard exit.\n"
                        "  A runner that turns a missing database into \"0 scripts, all passed\" is worse than no runner.\n"
                        "- **Serial by default.** Concurrency against one database is the bug being fixed, not a feature to\n"
                        "  add later. If it ever parallelises, each worker needs its own database — not a lock, not a schema\n"
                        "  prefix.\n"
                        "- **Own its database.** Setting `ATLAS_TEST_DB_URL` yourself puts two runs back in the 2026-08-10\n"
                        "  configuration. It is honoured — it is `testdb`'s documented contract — but warned about loudly on\n"
                        "  every run.\n"
                        "\n"
                        "`backend/TESTING.md` is the reference for what each script proves and how to write a new check. Its\n"
                        "two rules worth repeating: **derive, don't pin** (`EXPECTED_TOTAL_FILES = 40` and `total_acc == 1493`\n"
                        "both went red for changes that were correct), and **name the case, not the row** — `seed.blocked_leaf`,\n"
                        "never `\"TASK-9008\"`, because a test that pins a production id breaks when somebody closes that item,\n"
                        "and then it is \"stale\", and then it gets waved through. TASK-1068 found six such assertions in one\n"
                        "script; the sixth was not stale at all, it was a real defect hiding among noisy neighbours.\n"
                        "\n"
                        "**A fresh database is not a populated one.** `create_all` adds no column to a populated table, so a\n"
                        "schema change that passes here can still fail on deploy — verify anything schema-shaped on staging,\n"
                        "and anything shape-of-the-real-graph on a fork, before merging.\n"
                        "\n"
                        "## 5. Working on a host\n"
                        "\n"
                        "- **Run a script inside a container by shipping it as a file on stdin, never as a heredoc through\n"
                        "  `ssh 'bash -s'` that contains `docker exec`.** With `bash -s` the script *is* stdin, so the first\n"
                        "  `docker exec -i` consumes every line after it and the rest never runs; and `docker exec` **without**\n"
                        "  `-i` attaches no stdin, so `psql <<'SQL'` connects to empty input. Both exit 0. Safe shapes: the\n"
                        "  file as the only stdin of `docker exec -i <container> python -`, a committed script run by path,\n"
                        "  or `</dev/null` on every `docker exec` inside a piped script — and **measure the effect\n"
                        "  afterwards**, because the exit code of either failure is 0.\n"
                        "- **A recovery point is not a backup practice.** The `no-backup` intent on `atlas` stands. A\n"
                        "  one-time, self-verifying recovery point taken before widening an agent's permissions keeps that\n"
                        "  choice reversible; if one is ever found running on a schedule, a decision has quietly become a\n"
                        "  habit.\n"
                        "- **Minting a service token:** `admin token add` refuses to print the raw value unless told where it\n"
                        "  goes. Write it with `--out` into a file on the host, never `--print` from an agent — a token in a\n"
                        "  transcript has to be rotated.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-ops-delivery",
            "type": "component",
            "name": "Delivery",
            "summary": "How a change to Atlas itself gets from a branch to running: the CI pipelines that check it and the tooling that stages, deploys and verifies it.",
            "docs": [],
        },
        {
            "id": "atlas-ops-environments",
            "type": "component",
            "name": "Environments",
            "summary": "The non-production Atlas stacks and CI capacity an installation runs beside live: a staging stack, an experimental fork and the self-hosted CI runners.",
            "abstract": "An example of how an install can run non-production Atlas stacks beside live. Each environment is a second Docker Compose project on the same or another host: its own containers, its own database volume, its own network and its own published port, so nothing it does can reach live's data. This install keeps two, described in the child nodes: a **staging** stack that proves a branch against a populated database before it goes live, and an experimental **fork** that holds a scrubbed copy of live's graph where an agent may break things. Self-hosted CI runners are an optional third piece of capacity. None of this is required to run Atlas; it is one way to make changes safe to try. The running instances of these stacks belong to one install and are not part of what Atlas ships.",
            "docs": [],
        },
        {
            "id": "atlas-panel",
            "type": "capability",
            "name": "Detail panel",
            "summary": "Board v2's node panel: a full-height view of the selected node, with tabs for overview, intent, comments, history, docs, links, decisions and configuration.",
            "abstract": (
                "`atlas-panel` is board v2's **node panel**, `frontend/v2/views/panel/panel.js`: a full-height view of whatever is selected in the graph or on the work board. It reads and writes only the store (`core/store.js`); there is no `hooks` seam.\n"
                "\n"
                "**The head** is drawn at once from the graph index: type, name, summary, attention/state/urgency chips, status for work, the id and its short label, and the actions *Focus here*, *Star*, *Connected to…* (routes to another node, drawn by the graph) and *Open in a window* (the panel as a pop-out, kept in step over `core/sync.js`).\n"
                "\n"
                "**The tabs** are Overview, Intent, Comments, History, Docs, Links, Decisions and Configuration. Comments shows only on an epic, story or task. Decisions shows on a project, or on any node one probe finds ADRs for. Configuration shows only to an instance owner, on a node `/api/config` names as a part. There is no Work tab: the work board (`atlas-view-work`) replaces it. The legacy Work tab is described on `panel-work`. The chosen tab is remembered per viewer (`atlas.v2.panel.tab`).\n"
                "\n"
                "**One read per selection.** `GET /api/node/{id}?intents=all` feeds Overview, Intent and the head's summary. History, Comments and each document are fetched when their tab or row is first opened. Intent inheritance is the API's answer (`routers/nodes._intents_for`) and is never recomputed here.\n"
                "\n"
                "What sits inside each tab is on the tab's own node: `panel-overview`, `panel-intent`, `panel-comments`, `panel-history`, `panel-docs`, `panel-links`, `panel-decisions`, `panel-config`."
            ),
            "detail": "**The legacy panel** (`frontend/panel.js`, at `/board/legacy`) is frozen; its render pipeline is kept in `docs/nodes/atlas-panel/architecture.md` for reference only.",
            "docs": [
                {
                    "path": "docs/nodes/atlas-panel/architecture.md",
                    "kind": "architecture",
                    "title": "Panel render pipeline",
                    "format": "mermaid",
                    "content": (
                        "# atlas-panel — architecture\n"
                        "\n"
                        "**Legacy only.** This describes `frontend/panel.js` on the frozen `/board/legacy`; board v2's panel is described on the node itself.\n"
                        "\n"
                        "`docs[]` entry: `{kind: architecture, title: \"Panel render pipeline\", path: \"docs/nodes/atlas-panel/architecture.md\", format: mermaid}`\n"
                        "\n"
                        "One `renderPanel(node)` call, and the two things prose keeps getting wrong about it: which sections\n"
                        "are synchronous, and how many network requests a click actually costs.\n"
                        "\n"
                        "## What one selection builds\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart TD\n"
                        "    Sel[\"selectD3(d) in map.js<br/>→ hooks.renderPanel(d.data)\"]\n"
                        "    RP[\"renderPanel(node)<br/>_fullCache = null · panelBody wiped\"]\n"
                        "    Fixed[\"panel-fixed (pinned)<br/>88px glyph · id/name/type<br/>attention · state · urgency chips<br/>tab bar\"]\n"
                        "    Bodies[\"panel-tabpanels (scrolls)\"]\n"
                        "    O[\"Overview\"]\n"
                        "    I[\"Intent\"]\n"
                        "    W[\"Work\"]\n"
                        "    H[\"History\"]\n"
                        "    D[\"Docs\"]\n"
                        "    L[\"Links\"]\n"
                        "\n"
                        "    Sel --> RP\n"
                        "    RP --> Fixed\n"
                        "    RP --> Bodies\n"
                        "    Bodies --> O\n"
                        "    Bodies --> I\n"
                        "    Bodies --> W\n"
                        "    Bodies --> H\n"
                        "    Bodies --> D\n"
                        "    Bodies --> L\n"
                        "```\n"
                        "\n"
                        "Every body is **built on every render**, not lazily on tab click; `showPanelTab` only toggles\n"
                        "`hidden`. The remembered tab (`atlas.panelTab`) decides which one is showing. History is the one\n"
                        "partial exception, and it is about the fetch rather than the build: its section exists from the\n"
                        "start but holds its request back until the tab is first shown (`historyEl.ensureLoaded`).\n"
                        "\n"
                        "## One fetch, many readers\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart LR\n"
                        "    FF[\"fetchFull(node.id)<br/>GET /api/node/&#123;id&#125;<br/>cached per render\"]\n"
                        "    A[\"Acceptance<br/>(Overview)\"]\n"
                        "    R[\"Result<br/>(Overview)\"]\n"
                        "    S[\"Intent strip<br/>(Overview)\"]\n"
                        "    B[\"Binding control<br/>(Overview)\"]\n"
                        "    E[\"Description editor<br/>(Overview, on open)\"]\n"
                        "    T[\"Intent tab\"]\n"
                        "\n"
                        "    FF --> A\n"
                        "    FF --> R\n"
                        "    FF --> S\n"
                        "    FF --> B\n"
                        "    FF --> E\n"
                        "    FF --> T\n"
                        "```\n"
                        "\n"
                        "Every one of those sections wants the same payload — `acceptance`, `result`, `result_heading`,\n"
                        "`body`, `bindings`, `intents.{own,inherited}` — and issuing the request once per section would\n"
                        "multiply the per-click cost for nothing. The cache is keyed by node id and cleared at the top of\n"
                        "`renderPanel`, so re-selecting the same node after a write refetches instead of redisplaying what\n"
                        "was on screen before the write.\n"
                        "\n"
                        "Acceptance and Result are **fetched, not read off the tree**, deliberately: 1,546 criteria across the\n"
                        "backlog (live, 2026-08-14) would otherwise ride in every `/api/graph/tree` payload to serve one panel\n"
                        "at a time. Same rule that keeps `body` and `detail` out of listings.\n"
                        "\n"
                        "## Synchronous vs. fetched, per tab\n"
                        "\n"
                        "| tab | synchronous from the node | fetched |\n"
                        "|---|---|---|\n"
                        "| Overview | status control · completion bar · `abstract`/`detail` prose | intent strip · acceptance · result · the current binding (`fetchFull`) · the assignee roster (`/api/people?for=`) · the bindable-object roster (`/api/vocabulary` + `/api/graph/tree`, once per page life) |\n"
                        "| Intent | — | own + inherited (`fetchFull`) |\n"
                        "| Work — **object** | `work_load` list and counts | — |\n"
                        "| Work — **workitem** | — | `works_on` / `serves` (`/api/graph?edges=works_on,serves,for&depth=1`) |\n"
                        "| History | — | `GET /api/events?node_id=`, paged — **held back until the tab is first shown** |\n"
                        "| Docs | the `docs[]` list | each document body, on first expand (`/api/node/{id}/doc`) |\n"
                        "| Links | the sub-node list | `depends_on` both directions (`/api/graph?depth=1`), workitems only |\n"
                        "\n"
                        "An object and a workitem get **different panels**, and `isObject()` (from `state.js`'s\n"
                        "`OBJECT_TYPES`) is the switch. Objects get no status, assignee or binding control, no acceptance, no\n"
                        "result and no `depends_on`; workitems get no `work_load` list.\n"
                        "\n"
                        "## The seam, and the writes\n"
                        "\n"
                        "```mermaid\n"
                        "sequenceDiagram\n"
                        "    participant U as user\n"
                        "    participant P as panel.js status control\n"
                        "    participant API as POST /api/node/-id-/status\n"
                        "    participant B as board.html\n"
                        "    U->>P: click a status button\n"
                        "    Note over P: no-op if it equals statusBucket(node.status)\n"
                        "    P->>API: postJSON with status + csrf_token\n"
                        "    API-->>P: the updated node\n"
                        "    P->>P: copy fields onto the node the tree holds · repaint\n"
                        "    P->>B: hooks.refreshGraph(node.id)\n"
                        "    B->>B: refetch both trees, keep camera + selection, re-render this panel\n"
                        "```\n"
                        "\n"
                        "`refreshGraph` is not belt-and-braces. A status change moves worst-child rollups, the urgency\n"
                        "frontier and every work-load readout **above** the node — all of which live on other nodes. Patching\n"
                        "one node and leaving its ancestors stale would be a quieter bug than a failed write.\n"
                        "\n"
                        "**The status control is no longer the only write on this panel.** Overview also carries the assignee\n"
                        "control, the binding control (`renderBindingSection`, shipped 2026-08-28), and in-place editing of\n"
                        "the description and the acceptance list — all `PATCH /api/workitem`, all workitems only. Every one of\n"
                        "them follows the same discipline as the status control: render the value the **server** answered\n"
                        "with, never the value that was picked, because a control that shows your choice before the write\n"
                        "lands lets somebody walk away believing a change stuck when it did not.\n"
                        "\n"
                        "**A re-bind is TWO writes, and that is the API's shape rather than a workaround.**\n"
                        "`PATCH /api/workitem` refuses a binding onto an item that already has one — 409 `binding_exists`, at\n"
                        "most one binding per item (ADR 0028) — so *changing* a binding sends `binding: null` and then\n"
                        "`binding: <new>`. The gap between those two writes is why the control also carries a restore: if the\n"
                        "second write fails the item would be left unbound, a change nobody asked for, so the old binding goes\n"
                        "back, and if even that fails the control says so on screen instead of showing a tidy lie. The restore\n"
                        "leg deliberately does **not** call `refreshGraph` — the refresh re-renders the panel and would take\n"
                        "the error message down with it, and on balance nothing changed.\n"
                        "\n"
                        "```mermaid\n"
                        "sequenceDiagram\n"
                        "    participant U as user\n"
                        "    participant P as panel.js binding control\n"
                        "    participant API as PATCH /api/workitem/-id-\n"
                        "    U->>P: pick a different world node\n"
                        "    P->>API: binding null — drops every works_on edge\n"
                        "    API-->>P: ok\n"
                        "    P->>API: binding new-id\n"
                        "    alt second write succeeds\n"
                        "        API-->>P: the updated item\n"
                        "        P->>P: paint the server's answer · hooks.refreshGraph\n"
                        "    else second write fails\n"
                        "        P->>API: binding previous-id — put it back\n"
                        "        P->>P: paint · show the server's own error · NO refreshGraph\n"
                        "    end\n"
                        "```\n"
                        "\n"
                        "`hooks` carries exactly two functions, both of them `board.html`'s: `navigateTo` and `refreshGraph`.\n"
                        "`selectNode` is imported from `map.js` directly, because a sub-node row or a Links row only ever\n"
                        "points at a node in the same tree.\n"
                        "\n"
                        "## Where the components live\n"
                        "\n"
                        "Under ADR 0030 the tab is the binding floor. What sits inside Overview, the lists inside Work and so\n"
                        "on are documented on their tab's node — `panel-overview`, `panel-work`, `panel-docs`, `panel-links`.\n"
                        "**Intent and History have no node yet**; their components are documented here until one earns its\n"
                        "own.\n"
                        "\n"
                        "**There is no promotion threshold, and the old \"~5 open workitems\" one is retired** (2026-08-15).\n"
                        "Promote a component to a node of its own when finding the work in it actually gets hard, and say\n"
                        "*what* got hard. Never count lifetime workitems: that number only rises, so a threshold on it\n"
                        "eventually fires everywhere purely from work being finished. Count OPEN items if you count at all,\n"
                        "as a description of the situation and never as a trigger.\n"
                        "\n"
                        "When you do promote a component, bind its existing workitems to the new node explicitly. Inherited\n"
                        "effective binding resolves *upward* to the nearest bound ancestor; it will not move work that already\n"
                        "points at the tab.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-postgres",
            "type": "db",
            "name": "atlas-postgres",
            "summary": "The PostgreSQL database that holds all of Atlas's persisted state; the bundled container by default, or a managed service an install points Atlas at.",
            "abstract": (
                "atlas-postgres is a PostgreSQL 16 container holding the entirety of Project Atlas's persisted state — the legacy v0 `component`/`gate`/`requirement`/`decision` tables (ADR 0017) and the object-graph tables (`node`, `node_type`, `edge`, `probe`, `probe_result`), plus identity (`auth_user`, `auth_session`, `auth_service_token`, `auth_login_throttle`, `auth_event`) and `node_visibility` — in a single `atlas` database. It is a dedicated instance: it shares no database with any other stack, so Atlas's data has no coupling to any other store.\n"
                "\n"
                "The image is built in the repo from `deploy/postgres/Dockerfile`: the official `postgres:16-trixie` (pinned tag and digest, followed by Dependabot) with its `gosu` Go binary replaced by a small `setpriv` shell shim, which removes the image's only fixable critical vulnerability without changing the entrypoint, PGDATA or the Postgres binaries. The OSS stack builds the same change on the pgvector image (`deploy/postgres/pgvector.Dockerfile`). An image swap is done with a verified snapshot first (`tools/snapshot.sh`, run on the server), then `tools/db_collation_check.sh` after the restart; a `REINDEX` is needed only when it reports a collation mismatch (a glibc change underneath the volume).\n"
                "\n"
                "Passwords are argon2id verifiers and session ids and service tokens are stored as HMAC digests, never in the clear, so a stray dump discloses no usable credential. The database password is a real secret rather than a published constant, and the peppers that key those digests live in the server `.env` and must never be regenerated, because a new pepper invalidates every account and every token at once.\n"
                "\n"
                "The only connection to it is from **atlas-api**, over `ATLAS_DB_URL` built from the server `.env` — no other service or client reaches this database directly. atlas-api is both the sole reader (all `/api/*` endpoints) and sole writer, and every write is a request-time write: there is no startup ingest that reconciles this database against markdown files any more, so boot no longer changes data at all.\n"
                "\n"
                "What persists across a container recreate is exactly the `atlas-pgdata` named volume; removing that volume is a full data reset. No Alembic, by decision (TASK-1015): `atlas-migrate upgrade` runs startup's schema steps (`create_all` plus the additive `_reconcile_graph_schema`) and exits; see `atlas-postgres-migrations`. This install keeps no backup, by decision (intent `no-backup` on `atlas`); the product's opt-in backup service is `atlas-backup`."
            ),
            "detail": "See `docs/nodes/atlas-postgres/architecture.md` (this node's Docs tab) for the connection diagram — atlas-api is the only client — and `docs/nodes/atlas-postgres/design.md` for the full table list and the migration/retention gaps. There is no `api` doc for this node: a `db`-type node does not expose an API surface of its own, all access goes through atlas-api's endpoints, which are documented on that node instead.",
            "docs": [
                {
                    "path": "docs/nodes/atlas-postgres/architecture.md",
                    "kind": "architecture",
                    "title": "atlas-postgres connections",
                    "format": "mermaid",
                    "content": (
                        "# atlas-postgres — architecture (who connects)\n"
                        "\n"
                        "`docs[]` entry: `{kind: architecture, title: \"atlas-postgres connections\", path: \"docs/nodes/atlas-postgres/architecture.md\", format: mermaid}`\n"
                        "\n"
                        "## What this is\n"
                        "\n"
                        "`atlas-postgres` is Project Atlas's **own** PostgreSQL 16 instance (built from `deploy/postgres/Dockerfile` (postgres:16 with a setpriv shim), `atlas-pgdata` volume)\n"
                        "— it shares no database with any other stack. Everything Atlas persists — the legacy v0 Component/Gate/Requirement\n"
                        "tables and the ADR 0019 node/edge/probe graph — lives in this one database (`atlas`).\n"
                        "\n"
                        "## Connections\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart LR\n"
                        "    subgraph atlas-postgres[\"atlas-postgres (postgres:16, container :5432)\"]\n"
                        "        DB[(\"atlas database<br/>node · node_type · edge · probe · probe_result<br/>component · gate · requirement · decision\")]\n"
                        "    end\n"
                        "\n"
                        "    API[\"atlas-api<br/>(FastAPI, container :8000)\"] -->|\"ATLAS_DB_URL=<br/>postgresql+psycopg2://atlas:${ATLAS_DB_PASSWORD}@atlas-postgres:5432/atlas\"| DB\n"
                        "\n"
                        "    Vol[(\"atlas-pgdata volume\")] -.->|\"persists\"| atlas-postgres\n"
                        "```\n"
                        "\n"
                        "**Only `atlas-api` connects to `atlas-postgres`** in the current deployment — there is no direct\n"
                        "client/browser access, no second service, and no cross-database link to any other stack\n"
                        "component. `atlas-api` is the sole reader and sole writer (both the request-time read endpoints and the\n"
                        "startup ingest jobs run inside the `atlas-api` container, against this same connection string).\n"
                        "\n"
                        "## Persistence\n"
                        "\n"
                        "The `atlas-pgdata` named volume is the only thing that survives a container recreate — recreating the\n"
                        "`atlas-postgres` container without removing the volume preserves all data; removing the volume is a full\n"
                        "reset — and since TASK-1058 there is **no startup ingest to re-populate it from**. Boot reads no file and never overwrites graph data. What it does write is schema-shaped: the `node_type` and `edge_type` registries and the `task_id_seq` floor, every boot; on an empty graph, the seed the install's `ATLAS_SEED` profile names (homelab v0 tables, demo, self, plus the system node). So losing this volume loses the graph. That is accepted: backup is\n"
                        "out of scope for Atlas by standing decision.\n"
                    ),
                },
                {
                    "path": "docs/nodes/atlas-postgres/design.md",
                    "kind": "design",
                    "title": "atlas-postgres schema and retention",
                    "format": "md",
                    "content": (
                        "# atlas-postgres — design (schema + retention)\n"
                        "\n"
                        "`docs[]` entry: `{kind: design, title: \"atlas-postgres schema and retention\", path: \"docs/nodes/atlas-postgres/design.md\", format: md}`\n"
                        "\n"
                        "## Schema shape (at a glance)\n"
                        "\n"
                        "Two generations of schema coexist in the same `atlas` database — the legacy v0 tables and the ADR 0019\n"
                        "object-graph tables. Both are defined via SQLAlchemy `DeclarativeBase` models and created with\n"
                        "`create_all()` at `atlas-api` startup (additive-only; see the schema-reconcile note below).\n"
                        "\n"
                        "The legacy v0 tables (ADR 0017: `component`, `gate`, `requirement`, `decision`) are defined in\n"
                        "`backend/db.py`; the object-graph tables (ADR 0019) in `backend/models_graph.py`. The tables, grouped,\n"
                        "are described on the child nodes of `atlas-postgres-schema`: `atlas-postgres-tables-graph`,\n"
                        "`atlas-postgres-tables-accounts`, `atlas-postgres-tables-audit` and `atlas-postgres-tables-settings`.\n"
                        "\n"
                        "`node.attributes` is validated **app-side** against its `node_type`'s JSON-Schema (not a DB constraint) —\n"
                        "see `models_graph.py`'s attribute-validation helpers. This is the\n"
                        "deliberate JSONB-over-table-per-type tradeoff from ADR 0019: structure without a migration per type, at\n"
                        "the cost of pushing validation into the application layer.\n"
                        "\n"
                        "## Migrations\n"
                        "\n"
                        "See `atlas-postgres-migrations`. `atlas-migrate upgrade` (`backend/migrate_cli.py`) runs startup's schema\n"
                        "steps and exits: `create_all()` (creates missing tables) plus the additive-only reconcile step\n"
                        "`_reconcile_graph_schema` in `main.py`, which issues `ALTER TABLE ... ADD COLUMN` for any column present\n"
                        "in the SQLAlchemy models but missing from the live table. It cannot drop/rename columns or backfill\n"
                        "data — a genuinely destructive or renaming schema change still needs a manual `ALTER`/migration outside\n"
                        "this mechanism. Always sanity-check a real schema change against a populated `atlas-postgres` volume,\n"
                        "not just `pytest`.\n"
                        "\n"
                        "## Retention\n"
                        "\n"
                        "No backup on this install, by decision (intent `no-backup` on `atlas`). Nothing re-derives the graph:\n"
                        "there is no ingest, so losing the `atlas-pgdata` volume loses the data. The product ships an opt-in\n"
                        "backup service, `atlas-backup`.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-postgres-config",
            "type": "component",
            "name": "Configuration",
            "summary": "How Atlas finds and uses its database: the connection setting `ATLAS_DB_URL` and the seed profile that decides what an empty database starts with.",
            "docs": [],
        },
        {
            "id": "atlas-postgres-migrations",
            "type": "component",
            "name": "Migrations",
            "summary": "How the schema is built and brought up to date: `atlas-migrate upgrade` runs the same schema steps as API startup, then exits.",
            "docs": [],
        },
        {
            "id": "atlas-postgres-schema",
            "type": "component",
            "name": "Schema",
            "summary": "The tables Atlas keeps its state in, grouped by what they hold: the graph and work, accounts and access, the audit trail, and settings.",
            "docs": [],
        },
        {
            "id": "atlas-postgres-tables-accounts",
            "type": "component",
            "name": "Account tables",
            "summary": "Who may do what: users, sessions, service tokens, groups, project role bindings, node grants and visibility marks.",
            "docs": [],
        },
        {
            "id": "atlas-postgres-tables-audit",
            "type": "component",
            "name": "Audit tables",
            "summary": "The record of what happened: graph changes, sign-in events and, when read audit is on, who read what.",
            "docs": [],
        },
        {
            "id": "atlas-postgres-tables-graph",
            "type": "component",
            "name": "Graph tables",
            "summary": "Nodes, edges and their type registries, plus what hangs off them: node documents, intents, acceptance criteria and proposals, results, comments and ADR records.",
            "docs": [],
        },
        {
            "id": "atlas-postgres-tables-settings",
            "type": "component",
            "name": "Settings tables",
            "summary": "Install-level state that is configuration rather than graph: board-set config values, crunch week state and history, and setup guide answers.",
            "docs": [],
        },
        {
            "id": "atlas-postgres-vector",
            "type": "component",
            "name": "Vector store",
            "summary": "The pgvector tables behind the optional semantic retrieval add-on: text chunks and their embeddings, derived from the database and rebuildable from it.",
            "docs": [],
        },
        {
            "id": "atlas-retrieval",
            "type": "capability",
            "name": "Semantic retrieval (add-on)",
            "summary": "Opt-in semantic search over world-node prose and served documents, embedded into pgvector through any OpenAI-compatible endpoint; Atlas works the same without it.",
            "abstract": (
                "`atlas-retrieval` is the **optional** semantic half of \"tell me about X\" (ADR 0044, EPIC-atlas-context). It keeps a vector index of what Atlas already holds — one *card* per world node (name, type, summary, abstract, detail) and the **served** documents of those nodes, split on headings — and answers searches with it, so a question asked in someone's own words, or in Swedish about something documented in English, finds its starting points. The index lives in Atlas' own Postgres (pgvector), never in another service, and every row of it is derived: it can be dropped and rebuilt at any time.\n"
                "\n"
                "**How it is used.** `GET /api/search` and the MCP tool `atlas_node_search` take `mode=hybrid` (semantic matching fused with full text by rank), `mode=semantic` (the vector half alone) and `expand=1` (one hop of graph neighbours). Without `mode` a search is exactly the full-text search it always was. A hit the semantic half found carries `\"semantic\"` in `matched_in` and a snippet from the best-matching passage; the response is still pointers, never prose. The `atlas-retrieval` skill tells agents when to use it: a question in their own words, `hybrid`; an exact name or id, the default.\n"
                "\n"
                "**Off by default, and off changes nothing.** With `ATLAS_RETRIEVAL` unset there is no route, no extension, no table, no loop and no field on `/api/health`, and asking for `mode=hybrid` returns the full-text result with `degraded: retrieval is off on this instance`. Turned on, a broken configuration never stops Atlas: the add-on reports `unavailable: <reason>` (configuration or storage) or `degraded: <last error>` (the embedder failed), both informational, and a search that could not use it says so in `degraded`.\n"
                "\n"
                "**How it stays current.** A background loop inside atlas-api wakes every interval, hashes what *should* be indexed, re-embeds only what changed, and removes what is gone — a deleted node, a document taken out of `docs[]`, a node that left the embedding scope. A write to the graph never calls the embedder, so a write cannot fail or slow down because the embedder is off; the index is then stale, never wrong, because every query re-checks visibility and document serving against `node` itself.\n"
                "\n"
                "**What it may send out, and what a caller may see.** Two separate controls. `ATLAS_EMBED_SCOPE=shared`, the product default, never sends the text of a node under a private mark to the endpoint. At query time the vector list filters by the caller's own visible set inside its SQL statement, before its limit, exactly as every other read does (`atlas-visibility`).\n"
                "\n"
                "Code: `backend/retrieval/` (`query.py` is the query side). Measuring recall against full text, and whether `hybrid` should ever become a default, is STORY-atlas-context-measure."
            ),
            "detail": (
                "**A model change re-indexes; it never mixes.** Every row carries its model, every source is replaced in one transaction, and a final sweep deletes any chunk whose source row is of another model. A dimension change cannot be done in place: it is `unavailable` until the owner runs `POST /api/retrieval/reindex` with `rebuild: true`, which drops and recreates the two derived tables.\n"
                "\n"
                "**One worker per database.** A pass takes a Postgres advisory lock; a second process or container finds it taken and skips that round rather than queueing.\n"
                "\n"
                "**Not a foreign key, on purpose.** `retrieval_chunk.node_id` is bare text, so deleting a node is unchanged; every reader joins `node`, which makes an orphaned chunk invisible until the next pass sweeps it.\n"
                "\n"
                "**Workitems are not indexed yet** (ADR 0044 phase 6): their visibility resolves through the effective binding and needs its own oracle case."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-retrieval/architecture.md",
                    "kind": "architecture",
                    "title": "Retrieval architecture",
                    "format": "mermaid",
                    "content": (
                        "# Retrieval architecture\n"
                        "\n"
                        "The add-on has two halves that never call each other: the **write path** of the graph, which is\n"
                        "untouched, and the **reconciler**, which reads the graph and keeps the derived index in step.\n"
                        "The query side (STORY-atlas-retrieval-query) reads the index and re-checks visibility and doc\n"
                        "serving against `node` at query time, so a lagging index is stale, never wrong.\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart LR\n"
                        "  subgraph write[\"Graph writes (unchanged)\"]\n"
                        "    W[routes: node / doc / edge] --> PG[(node, node_doc)]\n"
                        "  end\n"
                        "  subgraph loop[\"Reconciler loop in atlas-api (only when ATLAS_RETRIEVAL=on)\"]\n"
                        "    L[wake every interval, or on reindex] --> K{advisory lock}\n"
                        "    K -- taken --> S[skip this round]\n"
                        "    K -- got --> C[collect sources: world cards + SERVED docs, in scope]\n"
                        "    C --> H[chunk + hash]\n"
                        "    H --> D[delete sources that are gone]\n"
                        "    H --> E[embed new/changed, batched]\n"
                        "    E --> X[replace chunks + source row, one transaction]\n"
                        "    X --> SW[sweep chunks of another model]\n"
                        "  end\n"
                        "  PG --> C\n"
                        "  E -->|https, key from file| EMB[OpenAI-compatible /embeddings]\n"
                        "  X --> IDX[(retrieval_source, retrieval_chunk: pgvector)]\n"
                        "  SW --> IDX\n"
                        "```\n"
                        "\n"
                        "## The pieces (`backend/retrieval/`)\n"
                        "\n"
                        "| Module | Job |\n"
                        "|---|---|\n"
                        "| `__init__.py` | the switch (`enabled()`) and `load_config()`; a problem is a string, never an exception |\n"
                        "| `setup.py` | `CREATE EXTENSION vector` and the two tables, idempotent, only when on; `rebuild` for a dimension change |\n"
                        "| `chunking.py` | node cards; documents split on headings and paragraphs (~300–600 tokens, one paragraph of overlap), each chunk prefixed `node › doc › heading`; fences become a one-line placeholder |\n"
                        "| `embedder.py` | one `POST …/embeddings`; key read from the file per call; TLS verified; errors name host and status, never the key |\n"
                        "| `reconciler.py` | one pass: lock, collect, diff by hash, embed, replace, sweep |\n"
                        "| `service.py` | boot, the loop thread, `health()`, `status()`, `reindex()` |\n"
                        "| `routers/retrieval.py` | `GET /api/retrieval/status`, `POST /api/retrieval/reindex` (owner-only) |\n"
                        "\n"
                        "## Invariants, and where they are proven (`backend/verify_retrieval.py`)\n"
                        "\n"
                        "- **Off changes nothing**: no extension, table, route, scope entry or health field (§A, in a child process).\n"
                        "- **On never breaks boot**: no pgvector or no endpoint → `unavailable`, search unaffected (§B).\n"
                        "- **Only served documents**: an unserved or external document, and any workitem, is never indexed or sent (§C).\n"
                        "- **Scope**: `shared` never sends a private node's text; leaving the scope removes what was indexed (§D).\n"
                        "- **No mixed models**: a model change re-embeds everything and leaves one model's vectors (§E).\n"
                        "- **The key**: file only, rotation without restart, never in a log or the status route (§F).\n"
                        "- **Degradation**: embedder down or untrusted certificate → `degraded`, index untouched, `on` after the next good pass (§G).\n"
                        "- **Access**: both routes owner-only; reindex needs `admin` scope and CSRF (§H); the whole access suite passes with the add-on on (§I).\n"
                        "\n"
                        "## Data model\n"
                        "\n"
                        "```\n"
                        "retrieval_source (node_id, source 'prose'|'doc', doc_path, content_hash, model, indexed_at)\n"
                        "                 unique (node_id, source, coalesce(doc_path, ''))\n"
                        "retrieval_chunk  (id, node_id, source, doc_path, ordinal, heading, content, model,\n"
                        "                  embedding vector(ATLAS_EMBED_DIM))\n"
                        "```\n"
                        "\n"
                        "No foreign key and no ANN index, both on purpose (see the node's detail and ADR 0044 §1).\n"
                    ),
                },
                {
                    "path": "docs/nodes/atlas-retrieval/configuration.md",
                    "kind": "integration",
                    "title": "Retrieval configuration",
                    "format": "md",
                    "content": (
                        "# Retrieval configuration\n"
                        "\n"
                        "Everything is configuration; the product names no provider. The variables are read by\n"
                        "`backend/retrieval/__init__.py`, whose docstring is the authoritative list.\n"
                        "\n"
                        "| Variable | Meaning | Default |\n"
                        "|---|---|---|\n"
                        "| `ATLAS_RETRIEVAL` | `on` or `off`. Any other value stops the boot (a typo in a switch is not a state). | `off` |\n"
                        "| `ATLAS_EMBED_URL` | OpenAI-compatible endpoint: a base (`…/v1`, `/embeddings` is appended) or the full embeddings URL, query string included (Azure `?api-version=`). `https://` required except to loopback. | — |\n"
                        "| `ATLAS_EMBED_MODEL` | sent as `model`, stored on every row; changing it re-indexes | — |\n"
                        "| `ATLAS_EMBED_DIM` | vector length the endpoint returns, and the column's size | — |\n"
                        "| `ATLAS_EMBED_KEY_FILE` | PATH of the key, read on every call; optional for an endpoint without a key; not set in `entra` mode | — |\n"
                        "| `ATLAS_EMBED_AUTH_HEADER` | `bearer` (`Authorization: Bearer` with the key), `api-key` (Azure OpenAI's header), or `entra` (an Entra ID token, below) | `bearer` |\n"
                        "| `ATLAS_EMBED_ENTRA_SCOPE` | the token scope in `entra` mode | `https://cognitiveservices.azure.com/.default` |\n"
                        "| `ATLAS_EMBED_CA_BUNDLE` | PEM bundle for an endpoint with a private CA | system trust store |\n"
                        "| `ATLAS_EMBED_SCOPE` | `shared`: never send text under a private mark; `all`: send everything | `shared` |\n"
                        "| `ATLAS_EMBED_DOC_PREFIX` / `ATLAS_EMBED_QUERY_PREFIX` | e.g. nomic's `search_document: ` | empty |\n"
                        "| `ATLAS_EMBED_BATCH` | texts per embeddings call | 32 |\n"
                        "| `ATLAS_RETRIEVAL_INTERVAL_S` | seconds between passes | 60 |\n"
                        "\n"
                        "**Refused, never read:** `ATLAS_EMBED_KEY` as a plain variable. It makes the add-on `unavailable`.\n"
                        "So does `entra` together with a key file: two credentials for one call.\n"
                        "\n"
                        "## Entra ID (`ATLAS_EMBED_AUTH_HEADER=entra`)\n"
                        "\n"
                        "For an Azure OpenAI / AI Foundry deployment that accepts only Entra ID. The token comes from the\n"
                        "identity the container runs as, tried in the order Azure's own SDK uses\n"
                        "(`backend/retrieval/entra.py`, no extra dependency):\n"
                        "\n"
                        "1. **Workload identity**: `AZURE_FEDERATED_TOKEN_FILE`, `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`\n"
                        "   (and `AZURE_AUTHORITY_HOST` for another cloud), as AKS injects them. The file is re-read on\n"
                        "   every exchange, because the platform rotates it.\n"
                        "2. **Managed identity** on App Service / Container Apps: `IDENTITY_ENDPOINT`, `IDENTITY_HEADER`.\n"
                        "3. **Managed identity** through IMDS (a VM); `AZURE_CLIENT_ID` picks a user-assigned identity.\n"
                        "\n"
                        "The token is cached in the process and fetched again five minutes before it expires. It is never\n"
                        "logged or written anywhere. A refused request shows as `degraded: Entra <flow> at <host> answered\n"
                        "<status>: …`, which names the Entra error code and never a token.\n"
                        "\n"
                        "## What \"off\", \"unavailable\" and \"degraded\" look like\n"
                        "\n"
                        "| State | `/api/health` → `retrieval` | Routes | Loop | Search |\n"
                        "|---|---|---|---|---|\n"
                        "| off | field absent | absent (404) | none | full-text |\n"
                        "| `unavailable: <reason>` | that string | status 200, reindex 409 | none | full-text |\n"
                        "| `degraded: <last error>` | that string | both work | backs off, doubling to 10 min | full-text |\n"
                        "| `on` | `on` | both work | every interval | full-text (+ semantic once the query story lands) |\n"
                        "\n"
                        "None of them is red. Atlas is complete without the add-on.\n"
                        "\n"
                        "## Deployment shapes\n"
                        "\n"
                        "- **Homelab compose**: add `deploy/retrieval.override.yml`. It builds the Postgres image from\n"
                        "  `deploy/postgres/pgvector.Dockerfile` — pgvector on postgres:16, **Debian 13**, gosu replaced —\n"
                        "  the same image `deploy/oss` builds; passes the variables above from `.env`; and mounts\n"
                        "  `ATLAS_EMBED_KEY_DIR` read-only at `/run/atlas-embed` with the key in a file named `key`.\n"
                        "- **OSS install**: add the overlay `deploy/oss/retrieval.yml` (atlas-api joins `egress`; the key\n"
                        "  is the compose secret `embed_key`), and set the \"Retrieval\" block of `.env.example`.\n"
                        "- **Hosted provider** (Azure AI Foundry / Azure OpenAI): the deployment URL as given, and either\n"
                        "  `ATLAS_EMBED_AUTH_HEADER=api-key` with the key in the file, or `entra` with no key.\n"
                        "- **A separate embedding node**: its https URL; `ATLAS_EMBED_CA_BUNDLE` if its certificate is\n"
                        "  from a private CA.\n"
                        "\n"
                        "## The Postgres image and collation\n"
                        "\n"
                        "The image swap keeps the data directory (same major, same format), but the **Debian release\n"
                        "must match the one the volume was initialised under**: a glibc change underneath a live data\n"
                        "directory can change collation order, and text indexes then disagree with the data. The plain\n"
                        "`pgvector/pgvector:*-pg16` tag is Debian 12 (glibc 2.36); the homelab volume runs on Debian 13\n"
                        "(glibc 2.41). That is why the override builds the trixie Dockerfile and not the upstream tag.\n"
                        "After any swap, run `bash tools/db_collation_check.sh \"$(docker compose ps -q atlas-postgres)\"`\n"
                        "(SECURITY.md).\n"
                        "\n"
                        "## This instance\n"
                    ),
                },
                {
                    "path": "docs/nodes/atlas-retrieval/operations.md",
                    "kind": "runbook",
                    "title": "Retrieval operations",
                    "format": "md",
                    "content": (
                        "# Retrieval operations\n"
                        "\n"
                        "## Is it working?\n"
                        "\n"
                        "`GET /api/retrieval/status` returns `state`, the model, dimension, endpoint host, scope, the\n"
                        "counts (`sources`, `chunks`, `chunks_other_models`, `nodes`), `pending_sources`, the last pass's\n"
                        "numbers and its error, and `passes`. Healthy is `state: on`, `pending_sources` near zero, and\n"
                        "`chunks_other_models: 0`.\n"
                        "\n"
                        "`/api/health` carries the same `state` as `retrieval`. Without a token, `docker logs <atlas-api>`\n"
                        "shows the boot line (`retrieval add-on on: …` or `… unavailable: <reason>`), and\n"
                        "`tools/retrieval_enable.sh <stack> --verify` reads the index counts straight from Postgres.\n"
                        "\n"
                        "## Force a re-index\n"
                        "\n"
                        "`POST /api/retrieval/reindex` with `{}` clears every content hash and wakes the loop at once; the\n"
                        "pass re-embeds every source. Needs an owner, the `admin` scope for a token, CSRF for a browser.\n"
                        "\n"
                        "## Change the model\n"
                        "\n"
                        "Set `ATLAS_EMBED_MODEL` (and the dimension, if it changes) and restart atlas-api.\n"
                        "\n"
                        "- **Same dimension**: nothing else to do. The next pass re-embeds every source with the new\n"
                        "  model, and the sweep removes the old model's vectors as each source is replaced.\n"
                        "- **New dimension**: the add-on reports `unavailable: the stored vectors have N dimensions …`\n"
                        "  until you run `POST /api/retrieval/reindex` with `{\"rebuild\": true}`. That drops and recreates\n"
                        "  the two derived tables; the next pass fills them.\n"
                        "\n"
                        "## Rotate the key\n"
                        "\n"
                        "## Test the degradation\n"
                        "\n"
                        "## Turn it on or off for a stack (this estate)\n"
                        "\n"
                        "It is configuration in the stack's own server-side `.env`, written by one script, and the stack's\n"
                        "usual tool restarts it. `tools/fork.sh` and `tools/stage.sh` add `deploy/retrieval.override.yml`\n"
                        "when that `.env` says `ATLAS_RETRIEVAL=on`; on live the block also sets `COMPOSE_FILE`, so the\n"
                        "normal `docker compose up -d --build` includes the overlay.\n"
                        "\n"
                        "To turn it off: `bash tools/retrieval_enable.sh <stack> --off`, then restart the stack the same\n"
                        "way. The two tables stay behind and nothing reads them; dropping them is optional and loses only\n"
                        "derived data.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-search",
            "type": "capability",
            "name": "Search",
            "summary": "Full-text search across every node Atlas holds, its prose, bodies, served documents, acceptance criteria, results, comments and ADRs, filtered by what the caller may see.",
            "abstract": "The full-text search route `/api/search`; `atlas-retrieval` adds the optional semantic half, and `atlas-context` uses this search for its seeds.",
            "docs": [],
        },
        {
            "id": "atlas-security",
            "type": "capability",
            "name": "Security",
            "summary": "The policy applied once a request arrives: who may pass, what they may see, what agents may do, and the record of it. Ingress is the path in; this is the policy.",
            "abstract": (
                "`atlas-security` is the **policy** umbrella for Project Atlas: who may pass, and what they may see. It owns no code directly — it is the node its four children hang from. `atlas-identity` is **authentication** (who you are and how you prove it: the gate, login, passwords, sessions, accounts, service tokens). `atlas-visibility` is **authorisation to see** (what a signed-in caller may read: the private mark, its inheritance, the serialiser choke point). `atlas-audit-trail` is the record of who did what (sign-in events, graph changes, the opt-in read audit and its export). `atlas-guardrails` is what an agent may do on its own per environment. Nothing binds to `atlas-security` itself — work sits on the children and rolls up here.\n"
                "\n"
                "Commitments that hold across both children and are the posture of the whole product:\n"
                "\n"
                "- **Deny by default at one gate.** `AuthGate` is ASGI middleware, not route-level `Depends`, so it covers every route, every mount and every path that does not exist — including a route added six months from now. `/api/health` is deliberately not on the public exempt list.\n"
                "- **The exempt list is short, pinned, and reviewed by a failing test.** `PUBLIC_ROUTES` in `backend/auth.py` is the whole of what the gate does not refuse; `verify_object_graph.py` asserts it equals an exact frozenset, so an addition cannot land without someone editing the assertion, and the convention is that an addition carries an ADR note. Two entries are there because a page you can only read *after* signing in is not available to the person deciding whether to sign in: `/auth/privacy` (TASK-941, ADR-less by age) and — since 2026-08-25, ADR 0035 — **`/`, the bare domain**, which now serves a public description of the product instead of the login form. Three opt-in settings extend it with exact paths, never a prefix: the OIDC start and callback routes (`ATLAS_AUTH_PROVIDERS`, ADR 0041), the handbook (`ATLAS_PUBLIC_DOCS=1`, ADR 0047) and the landing site's files (`ATLAS_LANDING=v2`, ADR 0049); unset, the list is exactly the one above.\n"
                "- **A public route is not the same as a fixed response.** `/` reads `auth.current_principal` in its own handler and redirects a signed-in browser to `/board`; the gate is what does not refuse it, and the handler is what decides what it says. `/board` itself was NOT made public, and neither was anything under `/assets/*` — measured on both live hosts on the day it shipped, because a login gate that fails for a path *prefix* is the shape of this project's two worst near-misses.\n"
                "- **Fail closed at boot.** The peppers and the origin allow-list raise at *import*, before uvicorn binds. There is no `ATLAS_AUTH_DISABLED` and no bootstrap account.\n"
                "- **One write guard.** `auth.guard_write` is the only answer to \"may this request change data\", and — since TASK-1086 (2026-08-15) — it enforces the caller's scope on top of authenticating them, fail-closed for any route it has no scope mapping for.\n"
                "\n"
                "Healthy means an unauthenticated caller gets 401 on `/api/*` from every origin, and the audit trail in `auth_event` keeps growing."
            ),
            "detail": (
                "See `docs/nodes/atlas-security/architecture.md` (this node's Docs tab).\n"
                "\n"
                "**Where the code is.** `backend/auth.py` (the gate, sessions, CSRF, service tokens), `backend/passwords.py` (hashing, policy, breach screening, per-IP throttling), `backend/models_auth.py` (the auth tables, `node_visibility` and `node_grant` under their own `Base`, and the RBAC and IdP-link tables), and `backend/admin.py` (the operator CLI).\n"
                "\n"
                "**Decisions.** ADR 0025 (*Atlas grows its own identity*) is **Accepted and live**; ADR 0022 (*external access via an authenticated tunnel*) is Accepted, partly superseded by 0025. **ADR 0026, the per-node visibility decision, has never been written** even though `backend/app.py` and several task files cite it by number — treat any reference to it as a forward reference, not a source.\n"
                "\n"
                "**Human accounts and operator actions** are a CLI on the container, never a web form (a person's own agent credentials are the one exception: Settings → Agent credentials): `docker exec -it atlas-api atlas-admin <group> …` (groups: see `atlas-cli`). There is no self-registration route, no password-reset route and no seeded default account, now or later.\n"
                "\n"
                "**Scope enforcement closed the one gap that mattered here.** Until TASK-1086 (2026-08-15) a service token's scopes were recorded and displayed but never checked; that is fixed — every mutating route now maps to a required scope and refuses a caller who does not carry it. The remaining discipline is choosing what scope to mint, not hoping nothing checks it."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-security/architecture.md",
                    "kind": "architecture",
                    "title": "architecture.md",
                    "format": "md",
                    "content": (
                        "# atlas-security\n"
                        "\n"
                        "## What it is\n"
                        "\n"
                        "`atlas-security` is the **policy** umbrella, at level 2 of `atlas` beside `atlas-backend`,\n"
                        "`atlas-frontend`, `atlas-ingress`, `atlas-ops` and `atlas-mcp` (ADR 0030). It owns no code of its\n"
                        "own — it is the node its four children hang from, and work binds to them, not to it:\n"
                        "\n"
                        "- **`atlas-identity`** — *who you are and how you prove it*: the deny-by-default gate, login,\n"
                        "  passwords, sessions, machine credentials, accounts.\n"
                        "- **`atlas-visibility`** — *what a signed-in caller may read*: the per-node private mark, the\n"
                        "  per-account grant, their inheritance down `contains`, and the one place they are enforced.\n"
                        "- **`atlas-audit-trail`** — *the record of who did what*: sign-in events, graph changes, the\n"
                        "  opt-in read audit and its export.\n"
                        "- **`atlas-guardrails`** — *what an agent may do on its own* per environment.\n"
                        "\n"
                        "**The boundary against `atlas-ingress`**, its sibling: ingress is *the path in* — TLS termination,\n"
                        "routing, tunnels, which hostnames exist at all. Security is the policy applied once a request has\n"
                        "already arrived at `atlas-api`. The two meet at exactly one seam worth remembering: the session\n"
                        "cookie is `__Host-` prefixed and therefore `Secure`, and a `Secure` cookie never travels over plain\n"
                        "HTTP — so login cannot succeed without a real certificate, which is `atlas-ingress`'s problem to\n"
                        "solve and this node's requirement to state.\n"
                        "\n"
                        "## How it works\n"
                        "\n"
                        "**One gate, and it is middleware, not a per-route dependency.** `AuthGate`\n"
                        "(`backend/auth.py`) wraps the whole ASGI app, so it covers every mount, every route registered\n"
                        "before or after it, and every path that does not exist — including `/assets/*`, `/api/health` and\n"
                        "a route added six months from now. A `Depends()`-based scheme would fail *open* on anything it\n"
                        "never got attached to; middleware fails *closed*. `PUBLIC_ROUTES` is a short, explicit allowlist\n"
                        "(`/healthz`, `/readyz`, `/auth/login`, …); adding to it is deliberately treated as a decision, not\n"
                        "a convenience.\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart TD\n"
                        "  Req[incoming request] --> Gate{AuthGate}\n"
                        "  Gate -->|no credential, public route| Pass1[through, unauthenticated]\n"
                        "  Gate -->|no credential, everything else| Deny401[\"401 JSON, or 302 to /auth/login<br/>for a browser GET\"]\n"
                        "  Gate -->|cookie resolves| Sess[\"session principal<br/>must_change re-checked every request\"]\n"
                        "  Gate -->|Bearer resolves| Tok[\"token/grace principal\"]\n"
                        "  Gate -->|both presented| Bad400[\"400 — never guess which wins\"]\n"
                        "  Sess --> Route[route handler]\n"
                        "  Tok --> Route\n"
                        "  Route -->|mutating route| GW{guard_write}\n"
                        "  GW -->|session| CSRF[\"Origin check + synchronizer token\"]\n"
                        "  GW -->|non-session| Scope[\"_check_write_scope:<br/>WRITE_SCOPE_ROUTES lookup, has_scope\"]\n"
                        "  CSRF -->|ok| Do[commit]\n"
                        "  Scope -->|ok| Do\n"
                        "  CSRF -->|fail| R403a[403 csrf_failed]\n"
                        "  Scope -->|fail| R403b[403 forbidden]\n"
                        "```\n"
                        "\n"
                        "**Two credential kinds, deliberately not three.** `Authorization: Bearer atlas_svc_…` is a\n"
                        "machine; the `__Host-atlas_session` cookie is a human; both present is `400`, refused outright\n"
                        "rather than guessed at. There is no third branch reading a Cloudflare Access header — Access is\n"
                        "retired on the public hostname, nothing issues that header on any path Atlas serves, so the class\n"
                        "of bug where a peer forges it does not exist because the code that would read it is not written.\n"
                        "\n"
                        "**CSRF is a synchronizer token, not double-submit**, because the app is genuinely stateful (a\n"
                        "Postgres session table already exists): `csrf_token = HMAC(pepper, \"csrf-token-v1\" || sid)`,\n"
                        "recomputable server-side from the cookie it already has, unproducable by an attacker without it.\n"
                        "A Bearer-authenticated write is exempt from this check — an `Authorization` header is never\n"
                        "ambient, so nothing a third-party page does can attach one — but that exemption used to mean a\n"
                        "Bearer write skipped *all* authorization once past the gate, which is the gap the next paragraph\n"
                        "closes.\n"
                        "\n"
                        "**Scope enforcement is real now, and it was not until 2026-08-15.** `Principal.has_scope` existed\n"
                        "with zero call sites for weeks; `guard_write` returned \"allowed\" for every non-session principal\n"
                        "before scope was ever consulted, so a service token minted `--scopes read` could reach\n"
                        "`DELETE /api/node/{id}?cascade=1`. TASK-1086 closed it: every non-session principal — a token, and\n"
                        "the dormant `grace` bypass, which had the identical hole — now goes through `_check_write_scope`,\n"
                        "which maps the route's *template* path (`request.scope[\"route\"].path`, not the request's literal\n"
                        "URL, so a key like `{node_id}` matches) and method against `WRITE_SCOPE_ROUTES`, and fails closed:\n"
                        "a mutating route with no entry in that map is refused, never allowed by default. The scope domain\n"
                        "is `read, write, ingest, admin, private`; deleting a node or an edge needs `admin`, everything else\n"
                        "that mutates needs `write`. **Until this shipped, \"not issuing the token\" was the only real\n"
                        "limiter on a token's blast radius — that sentence is now historical, not current, and anything\n"
                        "still citing it (this repo's own `CLAUDE.md` and `tools/seed-node-docs/README.md` both did, as of\n"
                        "2026-08-14) is describing a fixed bug as a live one.**\n"
                        "\n"
                        "**And the enforcement is checked structurally, not by sampling.** `verify_node_access.py` §F walks\n"
                        "`app.app.routes` and FAILS the build on any route with no access classification, so an endpoint\n"
                        "added later that forgets the filter cannot ship quietly. §F2 (TASK-1121, 2026-08-28) applies the\n"
                        "same fail-closed rule one level down: every query PARAMETER of an access-filtered route must appear\n"
                        "in `PARAM_COVERAGE`, classified as selecting node rows or as only shaping the response. It exists\n"
                        "because a route stops being the unit of access review once its parameters decide which rows come\n"
                        "back — `?assignee=` arrived on an already-classified `/api/workitems` and §F stayed green the whole\n"
                        "time.\n"
                        "\n"
                        "## What will bite someone\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-signals",
            "type": "capability",
            "name": "Attention & rollup",
            "summary": "Derives the marks a node wears on the board, attention colour, completion and attention count, from statuses and edges, recomputed in the same write that changes them.",
            "abstract": (
                "`atlas-signals` is the derivation layer — one module, `backend/attention.py`, that turns statuses\n"
                "and edges into the three marks a node wears on the board: **fill** (`attention` — \"where do I\n"
                "look?\"), **ring** (`completion` — \"how done?\") and **badge** (`attention_count` — \"how much is lit\n"
                "below me?\"). It owns the colour vocabulary, the rollup rules, and the `completed_at` stamping the\n"
                "board's age-out filter sorts on. It is the **derivation**, not the drawing: `frontend/glyph.js`\n"
                "renders these three values and computes none of them.\n"
                "\n"
                "**Nothing here is hand-set.** ADR 0019's rule is that every value is re-derivable from node status\n"
                "plus edges, so `recompute_derived()` runs in the same unit of work as any write that changes a\n"
                "status or an edge — and there is no scheduler and no cache left to drift. Since the boot ingest was\n"
                "deleted (TASK-1058), recompute-on-write is the *only* trigger there is.\n"
                "\n"
                "**Fill is `worst(state, urgency)`.** State is a node's own condition: `blocked` red,\n"
                "`in_progress`/`analyze`/`done` green, `review` orange, not-started **grey** — and `cancelled` grey\n"
                "too, explicitly, because a cancelled item is terminal but nobody needs to *look* at it. Urgency\n"
                "propagates backward along `depends_on`: a not-done node blocking active-goal work lights up even\n"
                "when its own state is grey — **red** when its own dependencies are all clear (the actionable\n"
                "frontier, \"do this next\") and **orange** when it is itself still blocked. So a dependency chain\n"
                "shows one red frontier, not an all-red chain. A node with children takes `worst()` over them along\n"
                "`contains`, **with grey excluded** — so one no-signal child cannot drag a parent's colour to grey. (A parent whose children are all grey reads grey: the health hint applies to leaves only.)\n"
                "\n"
                "**`purple` is reserved and never returned in v1.** ADR 0019's ageing awaiting-decision state needs a\n"
                "persistent `since` timestamp and a decision store; neither exists, so no node on the board is purple. ADR 0048's `decision` workitem type (not yet built) is the likely decision store.\n"
                "How many nodes wear each colour is a query, not a fact about this capability — the board renders it."
            ),
            "detail": (
                "**Which rollups are safe over several parents, and how.** `max`-shaped operations are idempotent, so\n"
                "they need nothing: `worst()` over children, and the union of ancestor epics that decides goal\n"
                "relevance. `sum`-shaped ones would double-count, so they are written to collect a **set** of\n"
                "reachable ids instead — `leaves()` unions its children's leaf sets and `descendants()` accumulates\n"
                "into a `set`. A node with two `contains` parents is therefore counted once in `completion` and once\n"
                "in `attention_count`. Every recursion carries a cycle guard; `contains` is not constrained acyclic.\n"
                "\n"
                "**`work_load()` is deliberately not a rollup.** It is a per-object readout over *effective* bindings\n"
                "(TASK-1025): a workitem's own `works_on` edge, or the nearest bound ancestor's **in the work\n"
                "hierarchy, never the world one**. It is computed on read rather than cached and attached to every\n"
                "node by `/api/graph`, so the panel renders it without a second fetch.\n"
                "\n"
                "**What that leaves on the World tab, measured on live (snapshot, date not recorded):** no world node\n"
                "carried a non-zero `completion` or `attention_count`. Both are `contains`-subtree measures and world\n"
                "objects have no status, while workitems attach by `works_on`, which no rollup follows. Fill works,\n"
                "from the hints; ring and badge read empty on every object. The two items once filed against this —\n"
                "`subtree_work_load()` (TASK-999) and the original RAG derivation (TASK-846) — are both cancelled, so\n"
                "nothing is currently scheduled to change it."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-signals/architecture.md",
                    "kind": "architecture",
                    "title": "How a status change becomes a mark on screen",
                    "format": "mermaid",
                    "content": (
                        "# atlas-signals — architecture\n"
                        "\n"
                        "How a status change becomes a mark on screen: one write, one recompute, a rollup up `contains`, and\n"
                        "three numbers the glyph draws.\n"
                        "\n"
                        "## 1. The path a change takes\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart TD\n"
                        "  W[\"A write that changes status or edges<br/>PATCH /api/node · PATCH /api/workitem · POST/DELETE /api/edge\"]\n"
                        "  R[\"recompute_derived(session)<br/>SAME unit of work as the write\"]\n"
                        "  D[\"derive(session)<br/>pure computation, no writes\"]\n"
                        "  C[\"node.attention · node.completion · node.attention_count<br/>cached columns\"]\n"
                        "  S[\"stamp_completion_dates()<br/>node.completed_at\"]\n"
                        "  G[\"GET /api/graph<br/>+ completion_counts, state, urgency, work_load\"]\n"
                        "  X[\"frontend/glyph.js buildGlyph()<br/>fill · ring · badge\"]\n"
                        "\n"
                        "  W --> R\n"
                        "  R --> D\n"
                        "  D --> C\n"
                        "  R --> S\n"
                        "  C --> G\n"
                        "  S --> G\n"
                        "  G --> X\n"
                        "```\n"
                        "\n"
                        "There is **no scheduler**. `recompute_derived` used to be called at the end of the boot ingest as\n"
                        "well; that ingest is deleted (TASK-1058), so recompute-on-write is the only trigger. Everything it\n"
                        "writes is a cache of something `derive()` can produce again from status plus edges, which is the\n"
                        "property that makes a stale-colour drift impossible rather than merely unlikely.\n"
                        "\n"
                        "`completed_at` is maintained in that same unit of work on purpose: a click that flips a node to\n"
                        "`done` has to produce its completion date immediately, or the node sits unfiltered by the board's\n"
                        "age-out slider until the next restart.\n"
                        "\n"
                        "## 2. What each of the three marks is derived from\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart LR\n"
                        "  subgraph fill[\"FILL — attention\"]\n"
                        "    ST[\"state: this node's own status\"]\n"
                        "    UR[\"urgency: blocks active-goal work?<br/>backward along depends_on\"]\n"
                        "    WO[\"worst(children) along contains<br/>grey EXCLUDED\"]\n"
                        "    ST --> WA[\"worst()\"]\n"
                        "    UR --> WA\n"
                        "    WO --> WA\n"
                        "  end\n"
                        "  subgraph ring[\"RING — completion\"]\n"
                        "    LV[\"leaves() of the contains subtree<br/>as a SET\"] --> DN[\"done / total\"]\n"
                        "  end\n"
                        "  subgraph badge[\"BADGE — attention_count\"]\n"
                        "    DS[\"descendants() as a SET\"] --> RO[\"count red + orange\"]\n"
                        "  end\n"
                        "```\n"
                        "\n"
                        "**Grey is excluded from `worst()` and that is the load-bearing part.** Grey means *no signal*, not\n"
                        "*healthy* and not *bad*. Without the exclusion, a service with one attached no-signal child would\n"
                        "roll up grey and discard its own health hint. Bridge-family destinations are skipped when the\n"
                        "adjacency is built for the same reason — an attachment is not subordinate work whose health should\n"
                        "become its host's.\n"
                        "\n"
                        "## 3. Which rollups are safe over several parents\n"
                        "\n"
                        "ADR 0030 made several `contains` parents the normal case, so the shape of an operation decides\n"
                        "whether it is safe.\n"
                        "\n"
                        "| operation | shape | why it is safe as written |\n"
                        "|---|---|---|\n"
                        "| `worst()` over children | `max` | idempotent — seeing the same child twice cannot change the answer |\n"
                        "| goal relevance (`ancestor_epics`) | `max` / union | a set union; the same epic reached twice is one entry |\n"
                        "| intent inheritance (elsewhere) | union | same shape, same reason |\n"
                        "| `completion` (`leaves()`) | `sum` | collects a **set** of leaf ids before counting, so a shared leaf counts once |\n"
                        "| `attention_count` (`descendants()`) | `sum` | accumulates into a **set**, same reason |\n"
                        "\n"
                        "A `sum` written as a list append instead of a set would double-count silently — it would still look\n"
                        "plausible, just high. That is the same arithmetic that once reported 334 workitems instead of 323\n"
                        "from a `LEFT JOIN` against `edge`.\n"
                        "\n"
                        "## 4. The two axes are orthogonal — and both are inert on the world tree today\n"
                        "\n"
                        "Fill and ring answer different questions: a green node can be half complete, and a fully complete\n"
                        "node could in principle still be red.\n"
                        "\n"
                        "But measured on live, **every** world node reads `completion = 0` and `attention_count = 0`. Neither\n"
                        "is a bug in the rollup; it is what the rollup is over. Both walk `contains`, world objects carry no\n"
                        "lifecycle status, and workitems hang off objects by `works_on` — an edge no rollup follows.\n"
                        "Fill still works, because object leaves take it from the static `attributes.health` hint.\n"
                        "\n"
                        "So on a world node today: the colour is a hand-set hint rolled up, the ring is empty, and the badge\n"
                        "never appears. The number people actually read on an object comes from `work_load` instead — the\n"
                        "panel's \"N workitems · M done (X%)\" — and the panel says so in as many words: *\"Direct links only —\n"
                        "a rollup over this object's sub-tree isn't computed yet.\"* That rollup (TASK-999) was cancelled; nothing is scheduled to add it.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-tls",
            "type": "service",
            "name": "atlas-tls",
            "summary": "The TLS front: the one published port, the internal origin the MCP calls the API through, the install's certificate authority and optional client certificates.",
            "docs": [],
        },
        {
            "id": "atlas-tooling",
            "type": "component",
            "name": "Atlas tooling",
            "summary": "The command-line tools for running Atlas: the admin CLI used inside the API container, and the desktop scripts that stage, fork, deploy, check and snapshot the stacks.",
            "abstract": (
                "atlas-tooling is the tooling surface of Project Atlas, not a runtime service: the admin CLI (`atlas-cli`, run inside the API container) and the delivery scripts (`atlas-delivery-scripts`: stage, fork, deploy, check, snapshot).\n"
                "\n"
                "It exists as a node because the scripts it holds needed somewhere to bind that wasn't a project root itself — before ADR 0030 gave the tree capability nodes, binding tooling work straight to the `atlas` project root would have turned that root into a shadow backlog. Nothing is deployed here — the scripts run from a workstation (snapshot on the server, by way of `deploy.sh snapshot`) and the CLI inside `atlas-api`, which is why it carries no `hosted_on` edge."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-tooling/architecture.md",
                    "kind": "architecture",
                    "title": "architecture.md",
                    "format": "md",
                    "content": (
                        "# atlas-tooling\n"
                        "\n"
                        "## What it is\n"
                        "\n"
                        "`atlas-tooling` is a `component` contained by `atlas-ops` — the scripts, not the pipeline they run\n"
                        "in. `atlas-ops` is *delivery* (the three environments, the deploy path, what isolates staging from\n"
                        "live — see that node for the topology); `atlas-tooling` is the actual tool inventory: everything\n"
                        "under `tools/*`, `backend/verify_*.py`, and the operator CLI in `backend/admin.py`. Nothing here\n"
                        "runs a product feature; everything here exists so a human or an agent can check, stage or seed one.\n"
                        "\n"
                        "**Three families of tool, sharing no runtime with each other:**\n"
                        "\n"
                        "## How it works\n"
                        "\n"
                        "**The verify suite finds itself; nothing lists it.** `verify_all.py` discovers every\n"
                        "`backend/verify_*.py` on disk, starts one disposable `postgres:16-alpine` (uuid name, free port,\n"
                        "torn down via `atexit`) that the *run* owns, and executes each script serially against it via\n"
                        "`ATLAS_TEST_DB_URL`. That shape is a direct fix for a real incident: on 2026-08-10 two agents ran\n"
                        "scripts concurrently against one shared container and one dropped the other's schema mid-run, and\n"
                        "both then reported green on a re-run. Two runs now always get two containers, and a script never\n"
                        "silently reports success against a database it could not reach — `backend/testdb.py` has exactly\n"
                        "three outcomes (`ATLAS_TEST_DB_URL` if set, else a disposable container, else a hard failure) and\n"
                        "raises rather than falling back to anything, including SQLite, which is gone from this repo\n"
                        "entirely (TASK-1044). Individual scripts still run standalone (`python verify_object_graph.py`)\n"
                        "and behave identically.\n"
                        "\n"
                        "**Two scripts have started deriving their expectations from the application's own source of\n"
                        "truth instead of a hand-copied list, and it is worth knowing which**: `verify_object_graph.py`'s\n"
                        "route inventory is built from `auth.WRITE_SCOPE_ROUTES` directly (`set(auth.WRITE_SCOPE_ROUTES)`,\n"
                        "not a second list somebody has to remember to update), and its §20 asserts every one of those\n"
                        "routes actually refuses a read-only Bearer token — the regression test for the exact gap\n"
                        "`atlas-security` describes being closed by TASK-1086.\n"
                        "\n"
                        "**The seed appliers exist to make one-time authoring reviewable, and refuse to become a second\n"
                        "importer.** Both `tools/seed-intents/apply_intents.py` and `tools/seed-node-docs/apply_node_docs.py`\n"
                        "follow the same shape: the judgment (which statement, which prose, which document) lives in a\n"
                        "committed JSON file, reviewed in a diff before anything is sent; the script only ever **creates**.\n"
                        "`apply_intents.py` POSTs and treats a `409` (an existing `(node_id, slug)`) as SKIP; there is no\n"
                        "`--force`. `apply_node_docs.py` checks presence before writing — a node whose `abstract` is already\n"
                        "non-empty is skipped whole, a `docs[]` path that already has content is skipped, and `attributes`\n"
                        "is read-modify-written rather than replaced, because `PATCH /api/node` assigns the whole dict and\n"
                        "sending only `docs[]` would silently drop everything else already on the node. This is a direct,\n"
                        "named reaction to the three markdown importers ADR 0029 and TASK-1059 killed: they were `setattr`\n"
                        "upserts, so a second run did not top the database up, it reverted it to the file. **The instant\n"
                        "either applier's rows exist in Postgres, the database is authoritative and the source directory is\n"
                        "a historical record — not something to edit and re-run expecting the board to follow.**\n"
                        "\n"
                        "**`tools/stage.sh` ships a committed tree, never the working directory**, over `git archive | ssh`\n"
                        "— so staging always reflects a real commit, and the staging overlay is read from that same ref. See `atlas-ops`\n"
                        "for the isolation mechanics (containers, volumes, network, port) once the branch lands there.\n"
                        "\n"
                        "**The admin CLI is the only way to create or change a *human* account; a person's own agent token\n"
                        "can also be minted from Settings → Agent credentials.** `admin user add` prints a generated temp password once and forces a change on first\n"
                        "login; `admin bot add` creates a service principal with no usable password at all\n"
                        "(`is_service`, and the DB `CHECK` constraint forbids a password on that row); `admin token add`\n"
                        "mints `atlas_svc_<prefix>_<secret>`, requires `--scopes` explicitly (an unscoped token is refused\n"
                        "at creation, by the same domain `atlas-security` enforces at request time now), and shows the raw\n"
                        "value exactly once. `admin session mint` is the one command gated by an environment flag\n"
                        "(`ATLAS_ALLOW_SESSION_MINT`, set only on non-live stacks (staging and the fork), never on live) rather than a role check — it hands out a real,\n"
                        "password-free browser session, which is the only way to drive the synchronizer-token CSRF path\n"
                        "from an automated browser.\n"
                        "\n"
                        "## What will bite someone\n"
                        "\n"
                        "- **CI checks; it does not gate.** The verify suite runs on every push, but nothing blocks a\n"
                        "  merge, and staging, measuring and closing the ticket still depend on someone doing them.\n"
                        "- **A live-facing tool and a test-facing tool look similar and are not interchangeable.**\n"
                        "  `apply_intents.py`/`apply_node_docs.py` need a real `--base` URL and a minted, then revoked,\n"
                        "  Bearer token; `verify_all.py` needs neither — it never touches a deployed database, only a\n"
                        "  disposable one it owns for the run. Pointing the wrong one at the wrong place is an easy typo\n"
                        "  with very different consequences (one writes real rows; the other cannot reach them at all\n"
                        "  without `ATLAS_TEST_DB_URL` deliberately overridden).\n"
                        "- **This node's own summary has been observed to describe deleted machinery** — a prior review of\n"
                        "  this node named \"the arrival gate\" and \"the importers\" among its scripts; both were deleted by\n"
                        "  TASK-1059 on 2026-08-12. Treat any inventory of this component that predates that date as\n"
                        "  unverified until re-checked against `tools/` and `backend/verify_*.py` on disk.\n"
                        "- **Re-running `apply_node_docs.py` against a board that has since diverged is not free of\n"
                        "  judgment even though it is free of overwrite risk.** A skip is silent per-field, not per-node —\n"
                        "  a node could get a new `docs[]` entry appended while its already-authored `abstract` is left\n"
                        "  untouched, which is correct but easy to misread as \"nothing happened.\"\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-view-access",
            "type": "component",
            "name": "Access administration",
            "summary": "Owner-only overlay on board v2 for editing groups and project role bindings, saving each list whole as the access API replaces rather than merges.",
            "docs": [],
        },
        {
            "id": "atlas-view-dashboard",
            "type": "component",
            "name": "Dashboard",
            "summary": "An optional board v2 pane holding a grid of collapsible boxes about the signed-in person, starting with their open tasks.",
            "docs": [],
        },
        {
            "id": "atlas-view-graph",
            "type": "component",
            "name": "Graph view",
            "summary": "Board v2's graph pane: the world drawn as a radial tree around a focused node, grown by steps, where clicking sets the scope the work board and panel follow.",
            "docs": [],
        },
        {
            "id": "atlas-view-graph-glyphs",
            "type": "component",
            "name": "Glyphs",
            "summary": "The graph's node mark: attention fill, a type pictogram, a completion ring, an attention-count badge, and pips for intents, docs and skills.",
            "docs": [],
        },
        {
            "id": "atlas-view-graph-labels",
            "type": "component",
            "name": "Label placement",
            "summary": "Decides which graph labels show and where, by each node's on-screen size and a priority order, using measured text width.",
            "docs": [],
        },
        {
            "id": "atlas-view-graph-layout",
            "type": "component",
            "name": "Radial layout",
            "summary": "Places graph nodes as a radial tree around the focus, laid out for the rings that show, so moving the steps slider re-spaces the graph.",
            "docs": [],
        },
        {
            "id": "atlas-view-search",
            "type": "component",
            "name": "Search box",
            "summary": "Find-as-you-type over the loaded graph by name or id: a world node becomes the focus, a piece of work opens in the panel.",
            "docs": [],
        },
        {
            "id": "atlas-view-settings",
            "type": "component",
            "name": "Settings",
            "summary": "Board v2's settings: it opens the legacy Settings overlay and adds v2's own parts to it, the crunch-week tab and the password notice.",
            "docs": [],
        },
        {
            "id": "atlas-view-settings-crunch",
            "type": "component",
            "name": "Crunch week panel",
            "summary": "The owner's Settings tab for crunch week, a set period when changes that pass CI deploy straight to production, plus the banner everyone sees while it runs.",
            "docs": [],
        },
        {
            "id": "atlas-view-settings-password",
            "type": "component",
            "name": "Password notice",
            "summary": "A dismissible board notice for a single-sign-on account that has no personal password yet, leading through a fresh sign-in to set one.",
            "docs": [],
        },
        {
            "id": "atlas-view-work",
            "type": "component",
            "name": "Work board",
            "summary": "Board v2's work pane, scoped by the graph: epics with progress, a kanban board where dragging a card sets its status, and a backlog tree.",
            "docs": [],
        },
        {
            "id": "atlas-visibility",
            "type": "capability",
            "name": "Visibility & redaction",
            "summary": "What a signed-in caller may SEE: the private flag, its inheritance, the serialiser choke point, every derived surface, and the equivalence oracles that prove a hidden node leaves no shadow.",
            "abstract": (
                "`atlas-visibility` is **authorisation to see** — what a signed-in caller may read. It owns the `node_visibility` mark, the per-account `node_grant` rows, the inheritance of both down `contains`, the resolver that turns them into a set of ids, every read surface that has to apply it, and the equivalence oracles that prove a hidden node leaves no shadow. It is the sibling of `atlas-identity` under `atlas-security`: identity decides *who is asking*, this node decides *what the answer omits*.\n"
                "\n"
                "**Two marks, not one.** `node_visibility` answers *\"is this node private?\"*; it cannot answer *\"private to whom?\"* — one flag has exactly two outcomes, owner-only or everyone. `node_grant` is the missing half: a row means \"this ACCOUNT may see this PRIVATE node\". Grants on a shared node are kept rather than rejected, so flipping shared → private → shared does not silently discard them.\n"
                "\n"
                "**The rule, applied verbatim and fail-closed.** No principal → invisible. Owner → unrestricted. Marked private and not granted → invisible. No `contains` parent → visible, because a root is visible. Otherwise **ALL** parents must be visible — `ALL`, not `ANY`. Union semantics would turn a second parent into a silent un-hide, and multi-parent containment is the normal case here. A `contains` cycle and a parent id that resolves to nothing both fail closed.\n"
                "\n"
                "**A workitem follows what it WORKS ON.** It carries no mark of its own and has no `contains` parent, so containment alone would leave a private project's task names on screen — which is exactly what happened, and was found in a browser after the whole suite passed. Visibility resolves through the effective binding, inherited bindings included.\n"
                "\n"
                "**Grants inherit person → bot** (ADR 0034). An agent's visible set is its own grants union its person's, so granting a contributor a project grants their agents in the same click. ROLE never inherits: an owner holds no grants, so an owner's agent inherits an empty set.\n"
                "\n"
                "**Two mechanisms that are the whole point.** The filter goes *inside* the query as `WHERE id IN (...)`, never as a post-filter over loaded rows — a post-filter still leaks counts, and a caller could infer a hidden node by comparing a filtered page against an unfiltered total. And a mark **outlives the node**: `node_visibility.node_id` and `node_grant.node_id` are bare text, deliberately not foreign keys, so an orphan row re-binds instead of silently unmarking a subtree. Orphans are reported on `GET /api/access`, never cleaned up."
            ),
            "detail": (
                "See `docs/nodes/atlas-visibility/design.md` (this node's Docs tab) for the full resolution rule and the equivalence oracles.\n"
                "\n"
                "**The admin surface exists.** `PUT /api/access/node/{node_id}` is owner-only *and* costs the `admin` scope — both are required, so an owner's `read`-scoped token is still refused. `grants` is the WHOLE list rather than a delta: the grid holds every checkbox for a node on screen at once, so it always knows the full answer, and an add/remove delta pair would merge two conflicting edits into a union, which for an access control is the wrong direction to fail. Setting `shared` DISCARDS the stored mark rather than storing the word — two representations of one state would stop the resolver's fast path firing.\n"
                "\n"
                "**Where the leak actually lives is not row filtering.** `attention`, `completion` and `attention_count` are computed over the graph and persisted on the node row; removing rows from a result does not touch a number derived before the filter existed. So the rollups take the visible set as an INPUT — `attention.derive`, `work_load` and the intent counts all do.\n"
                "\n"
                "**Two oracles, and the precedence between them.** Deletion-equivalence asserts the surface is byte-identical to one served from a database where the private rows were physically removed — the node never existed. Redaction-equivalence asserts it is identical to one where only the gated *fields* were nulled — the node exists and says less. A node carrying both marks resolves to deletion, always: a named husk has already disclosed its existence and its position. Deletion-equivalence is proven in `verify_node_access.py` §C, for a **person and an agent principal** — an agent reaches the resolver by a different route (a union over two ids), so a leak on one path would be invisible on the other.\n"
                "\n"
                "**Semantic search applies the same rule, inside the same kind of statement** (ADR 0044, `atlas-retrieval`). With `mode=semantic|hybrid` the vector list is one SQL statement that filters by the caller's visible set, `JOIN node` and the served-document check BEFORE its `ORDER BY … LIMIT`, so a hidden node can neither appear nor push a visible one off the page; graph expansion keeps only visible neighbours. Two controls, kept apart on purpose: what the add-on INDEXES (`ATLAS_EMBED_SCOPE`: `shared` never sends a private subtree's text to the embedder) and what a caller may SEE (this node's resolver, at query time). Deletion-equivalence for `semantic`, `hybrid` and `expand`, for a person and an agent, is proven in `verify_retrieval.py` §K.\n"
                "\n"
                "**A refusal is indistinguishable from an absence.** A hidden node and a nonexistent one return the same 404, deliberately: a distinguishable refusal confirms a guessed id. The practical consequence is worth knowing before it costs somebody an afternoon — grant drift presents as *\"the MCP says that node does not exist\"* rather than as a permission error, so check grants before believing a 404.\n"
                "\n"
                "**The reporter tier is NOT BUILT and NOT WANTED.** Seeing that a node exists without seeing its detail — a second axis redacting *parts* of a visible item — was proposed by the restructure epic and rejected with it when that epic was cancelled. Access is decided at PROJECT level, and access control hides whole nodes. Do not re-propose it as though the question were open.\n"
                "\n"
                "**ADR 0026 does not exist.** The code and several task files cite it by number; no such file has ever been written. Treat every citation of it as a forward reference."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-visibility/design.md",
                    "kind": "design",
                    "title": "Visibility resolution and the equivalence oracles",
                    "format": "md",
                    "content": (
                        "# atlas-visibility — design\n"
                        "\n"
                        "The resolution rule as implemented, the surfaces that apply it, and the two oracles that prove\n"
                        "a hidden node leaves no shadow.\n"
                        "\n"
                        "## 1. The rule\n"
                        "\n"
                        "`_visible_node_ids` (`backend/app.py`) returns the set of node ids the current principal may\n"
                        "see, or `None` meaning \"no restriction\". It is fail-closed at every branch. **There is no global\n"
                        "choke point** — the `do_orm_execute`-over-every-`Session` design (TASK-974 / ADR 0026) was never\n"
                        "built, and this per-query implementation is what actually refuses. Read that as *one rule applied\n"
                        "at every read call site*, not as a stopgap: because it is per-query, a new route does not inherit\n"
                        "it, and §F of the oracle is what stops somebody forgetting.\n"
                        "\n"
                        "```mermaid\n"
                        "flowchart TD\n"
                        "  A[\"visible(n) for the current principal\"] --> P{\"principal present?\"}\n"
                        "  P -- no --> F1[\"FALSE — should be unreachable,<br/>the gate refused already\"]\n"
                        "  P -- yes --> OW{\"principal is owner?\"}\n"
                        "  OW -- yes --> T1[\"unrestricted — return None\"]\n"
                        "  OW -- no --> Z{\"any node marked private?\"}\n"
                        "  Z -- no --> T0[\"unrestricted — fast path\"]\n"
                        "  Z -- yes --> G{\"every private node<br/>granted to THIS caller?\"}\n"
                        "  G -- yes --> T0b[\"unrestricted — nothing left to block\"]\n"
                        "  G -- no --> M{\"n marked private?\"}\n"
                        "  M -- yes --> GR{\"node_grant row for this caller?<br/>own grants ∪ its person's\"}\n"
                        "  GR -- yes --> T4[\"TRUE — as if never marked\"]\n"
                        "  GR -- no --> F2[\"FALSE\"]\n"
                        "  M -- no --> R{\"n has a contains parent?\"}\n"
                        "  R -- no --> T2[\"TRUE — a root is visible\"]\n"
                        "  R -- yes --> ALL{\"ALL parents visible?\"}\n"
                        "  ALL -- yes --> T3[\"TRUE\"]\n"
                        "  ALL -- no --> F3[\"FALSE\"]\n"
                        "```\n"
                        "\n"
                        "Four refusals and one inheritance that are not obvious from the diagram:\n"
                        "\n"
                        "- **The mark is one half; the grant is the other.** `node_visibility` answers *is this node\n"
                        "  private*, which has exactly two outcomes — owner-only or everyone — and cannot answer *private\n"
                        "  to whom*. A `node_grant` row means \"this ACCOUNT may see this PRIVATE node\", and it changes\n"
                        "  exactly one line: a granted private node stops being blocked, as if it had never been marked.\n"
                        "  Nothing downstream changes, because a grant is a statement about ONE node and never about its\n"
                        "  subtree — a granted node's children then resolve visible for the ordinary reason, which is what\n"
                        "  makes \"grant the project root\" mean \"see that project\" with no separate subtree concept to keep\n"
                        "  in sync. Grants on a shared node are kept rather than rejected, so flipping shared → private →\n"
                        "  shared does not silently discard them.\n"
                        "- **Grants inherit person → bot; ROLE NEVER DOES** (ADR 0034). A caller's grants are read for its\n"
                        "  own `user_id` UNION the person it is linked to — a union, not a replacement, so an agent can\n"
                        "  still hold something its operator does not. The safety property runs the opposite way to\n"
                        "  intuition and is why linking is on by default: an owner is unrestricted **by role**, before a\n"
                        "  grant row is ever read, and therefore holds no grants — so an owner's agent inherits an *empty\n"
                        "  set* and linking makes it LESS capable, not more. A dangling link matches no grant row and\n"
                        "  contributes nothing.\n"
                        "- **`ALL`, never `ANY`.** With several roots and containment that is genuinely many-to-many, union\n"
                        "  semantics would let a second parent silently un-hide a subtree.\n"
                        "- **A `contains` cycle fails closed**, and so does a parent id that does not resolve to a real\n"
                        "  node. Guessing \"visible\" in either case is the concealment-shaped bug this rule exists to\n"
                        "  avoid.\n"
                        "- **The parent walk is built from a raw edge scan**, not from the adjacency the attention\n"
                        "  rollup uses — that one deliberately skips some destinations and would treat a node as a\n"
                        "  parentless, therefore visible, root.\n"
                        "\n"
                        "**A workitem follows what it WORKS ON.** It carries no mark of its own and has no `contains`\n"
                        "parent, so containment alone left a private project's task names on screen — found in a browser\n"
                        "after the whole suite had passed. Visibility resolves through the effective binding, inherited\n"
                        "bindings included.\n"
                        "\n"
                        "## 2. Where it is applied\n"
                        "\n"
                        "| surface | filtered today | note |\n"
                        "|---|---|---|\n"
                        "| `GET /api/node/{id}` | yes | inside the `WHERE`, so hidden and non-existent return the same 404 |\n"
                        "| `GET /api/workitems` | yes | `total` is computed *after* the filter |\n"
                        "| `GET /api/intents` | yes | owner-side rows only |\n"
                        "| `GET /api/graph` | yes | seeds, BFS hops and rollups all filtered (TASK-1040, **done** 2026-08-16) |\n"
                        "| `GET /api/graph/tree` | yes | the node dict is filtered and the roots are derived from it |\n"
                        "| the node-document read route | yes | `_may_see_node`, so a doc on a hidden node 404s identically |\n"
                        "| `GET /api/events` | yes | on BOTH governing node columns, before `total` is counted |\n"
                        "| every `Session` globally | **no, and not planned** | the `do_orm_execute` choke point was never built; the rule is applied per query instead, and `verify_node_access.py` §F is what makes that safe |\n"
                        "\n"
                        "The filter is applied **inside the query**, never as a post-filter over an already-loaded list.\n"
                        "A post-filter strips rows and still leaks counts: a caller compares a filtered page against an\n"
                        "unfiltered total and learns that something is there.\n"
                        "\n"
                        "**A route is not the unit of access review once its parameters decide which rows come back.**\n"
                        "`?assignee=` arrived on an already-classified `/api/workitems` and §F stayed green throughout,\n"
                        "which is why §F2 (TASK-1121, 2026-08-28) classifies every query PARAMETER of every filtered route\n"
                        "as either selecting node rows or only shaping the response, and fails on an unclassified one.\n"
                        "\n"
                        "## 3. Where the leak would live, and why it does not\n"
                        "\n"
                        "Row filtering removes rows. It does not touch a number computed from rows *before* the filter\n"
                        "existed. `attention`, `completion` and `attention_count` are computed over the graph and persisted\n"
                        "on the node row, so a build that filters rows perfectly and serves those three fields unchanged\n"
                        "would have a working filter and a leaking API.\n"
                        "\n"
                        "That is why **the rollups take the visible set as an INPUT, not as a post-filter**:\n"
                        "`attention.derive(..., visible_ids=)`, `attention.work_load(..., visible_ids=)` and the intent\n"
                        "counts all restrict the computation before it runs, so a hidden child cannot colour or count\n"
                        "toward its parent. Asserted by deletion equivalence, not by inspection.\n"
                        "\n"
                        "## 4. The two oracles\n"
                        "\n"
                        "Both compare the real surface against a surface served *unfiltered* from a deliberately\n"
                        "constructed database. Neither can be replaced by grepping the response body for an id.\n"
                        "\n"
                        "| oracle | comparison database | asserts |\n"
                        "|---|---|---|\n"
                        "| **deletion-equivalence** | private rows and their edges physically deleted | the node never existed — no gap, no tombstone, no count |\n"
                        "| **redaction-equivalence** | the gated *fields* nulled in place, nothing else touched | the node exists, by name and in position, and says less |\n"
                        "\n"
                        "**Deletion outranks redaction.** A node carrying both marks must resolve to deletion. A private\n"
                        "node arriving as a named husk with empty prose has already disclosed its existence, a count and\n"
                        "a position in the tree — which is exactly what the \"no gap, no tombstone, no count\" requirement\n"
                        "forbids. Leaving that to whichever check happens to run first is how it becomes a husk after a\n"
                        "refactor, so the precedence is itself a test.\n"
                        "\n"
                        "Deletion-equivalence is proven in `verify_node_access.py` §C **for a person and for an agent\n"
                        "principal**, because an agent reaches the resolver by a different route — a union over two ids —\n"
                        "so a leak on one path would be invisible on the other. §D drives the real routes over HTTP through\n"
                        "the real gate as two principals at once, and asserts that a hidden id 404s identically to a\n"
                        "nonexistent one.\n"
                        "\n"
                        "## 5. The mark outlives the node\n"
                        "\n"
                        "`node_visibility.node_id` is bare text and **not** a foreign key to `node.id`, `node_grant.node_id`\n"
                        "is the same, and `set_by` holds an account id as bare text for the same reason — the table sits\n"
                        "under its own `VisibilityBase`, not the auth `Base`. With a foreign key and `ON DELETE CASCADE`,\n"
                        "any pass that removes and recreates a node silently unmarks a private subtree and the next request\n"
                        "serves it. With a bare string the row waits and re-binds. Orphan rows are harmless and are\n"
                        "reported on `GET /api/access`, never cleaned up.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-work-store",
            "type": "capability",
            "name": "Workitem store",
            "summary": "The subsystem that stores workitem content, spec, acceptance criteria, result record, comments and assignee, and the routes that author and read it; not the workitems themselves.",
            "abstract": (
                "`atlas-work-store` is the **subsystem that stores workitems** — not the workitems themselves. Those are `workitem`-family nodes living in the work tree, and they attach to the world through a `works_on` edge; this node owns the tables their content sits in and the routes that read and write it.\n"
                "\n"
                "What it owns, all keyed by `node.id`:\n"
                "\n"
                "- **`node.body`** — the spec, verbatim markdown. Deliberately *not* a fourth context-economy tier beside summary/abstract/detail: those are curated, bounded projections for a panel, `body` is the source text.\n"
                "- **`acceptance_criterion`** — one row per checklist entry. `position` **is** the ordering and order is meaning; every read path sorts by it.\n"
                "- **`workitem_result`** — the `## Result` / `## Outcome` block in its own row. It exists because `PATCH /api/workitem` replaces `body` wholesale, and recording an outcome therefore used to destroy the spec it was recording against — nearly 8,000 characters lost across four items before the split. A `PATCH` that never touches this table cannot destroy it.\n"
                "- **`workitem_comment`** — the conversation on an epic, story or task (since 2026-10-28): markdown, oldest first, the author copied off the principal. Only the author edits; the author or an instance owner deletes. Board v2's Comments tab and the MCP tools `atlas_comments` / `atlas_comment_write` read and write it; agents cannot delete. Nothing a comment said is lost (since 2026-10-30): a delete hides the row (`deleted_at`, and who), and an edit keeps the replaced text in **`workitem_comment_revision`**. Both are audit records read in the database; no route returns them.\n"
                "- **`node.assignee_user_id`** — who the item is assigned to, an `auth_user.id`; **NULL means unassigned**. It replaced `owner` on workitems: `owner` is still the right field on an object-family node and is refused with a 400 naming the field on `POST`/`PATCH /api/workitem`.\n"
                "- **Authoring.** `POST /api/workitem` is the transactional whole-record path: id, node, acceptance list, parent edge, at-most-one binding and `depends_on` edges as **one** unit of work. `PATCH /api/workitem/{id}` is `exclude_unset`, and `result` is a field that never touches `body`.\n"
                "- **Reading without the filesystem.** `GET /api/workitems` filters on type, status, parent, binding, assignee and `q`, paged, with `include_prose` and `include_ancestors` as opt-ins; `GET /api/node/{id}` returns one complete item. `q` is real Postgres full-text over body+name, acceptance text, result text **and** comments that are not deleted (since 2026-10-29, `matched_in: comment` on `/api/search`) — the replacement for `grep -r backlog/`.\n"
                "\n"
                "Nothing in the repo can overwrite these rows from a file — the markdown that used to back them is deleted."
            ),
            "detail": (
                "See `docs/nodes/atlas-work-store/architecture.md` (this node's Docs tab) for the record layout, field by field.\n"
                "\n"
                "**Prose is per item, never in a listing.** `include_prose` defaults to 0 on `/api/workitems` and `/api/graph`; authored `abstract`/`detail`/`summary` come back either way because they are small. This is a payload rule, not an access rule.\n"
                "\n"
                "## Status\n"
                "\n"
                "**Change a status through the API or the board — nothing reads a file.** Closing a ticket through the API IS the update, with no second step to forget.\n"
                "\n"
                "## Priority\n"
                "\n"
                "**A workitem has no priority field.** The WSJF-style score in `routers/legacy_v0.compute_priority` (`value`, `effort`, `urgency`, `risk`) belongs to world components, not to workitems, so `POST`/`PATCH /api/workitem` accept a `priority` key and store nothing, and the board offers no control for it. Urgency is carried in the name instead, as a `CRITICAL:` prefix (TASK-1208, TASK-1221).\n"
                "\n"
                "## The two prose stores, and who may write what\n"
                "\n"
                "**A workitem's prose is TWO stores.** `node.body` holds the spec; its `## Result`/`## Outcome` block is a `workitem_result` row. **Record an outcome with `result`, never by rewriting `body`** — `result: null` DELETES the row, omitting the key changes nothing. A `body` PATCH still replaces wholesale, deliberately, because the routine operation that used to force one is gone.\n"
                "\n"
                "**Acceptance criteria are not writable by a machine principal** (2026-08-23). A Bearer caller `PATCH`ing `acceptance` on an EXISTING workitem gets **403**; a browser session may, and creation is unaffected. Two paths remain: an agent may PROPOSE criteria (`acceptance_proposal`, approved by a person on the board), and an install with `ATLAS_ACCEPTANCE_AGENT_WRITE=attested` lets an agent write them by citing a person's approval, recorded with the change. `routers/workitems.acceptance_rail_refusal()` carries the reasoning, including why it is deliberately NOT scoped to the assignee.\n"
                "\n"
                "## Assignment\n"
                "\n"
                "**A workitem's assignee is an account, not a string** — `node.assignee_user_id`, NULL for unassigned. `POST`/`PATCH /api/workitem` return **400 `owner_retired`** on any `owner` key, `null` included, because presence is what identifies a stale caller; `routers/workitems.owner_retired_refusal` argues 400 over silently ignoring it. `owner` remains the right field on object-family nodes, where it means who looks after that part of the world.\n"
                "\n"
                "## Search and the result splitter\n"
                "\n"
                "**Search is indexed, and the index and the query are kept textually identical.** `NODE_BODY_SEARCH_SQL`, `AC_TEXT_SEARCH_SQL` and `WORKITEM_RESULT_SEARCH_SQL` in `models_graph.py` are the same strings `_search_node_ids` interpolates, so the planner actually uses the GIN indexes. Those three indexes are created by `ensure_search_indexes()` **after** `create_all`, not declared in `__table_args__`, because a `to_tsvector(...)` expression index has to be a `text()` clause and `create_all` would try to compile it first. The query uses `plainto_tsquery`, so a search string containing `&` or `:*` is words, never operators.\n"
                "\n"
                "**The result splitter is proven, not assumed.** `split_result` / `reassemble` are fence-aware, take the block to the *next* `##` heading rather than to end-of-file, and were round-tripped byte-for-byte over every source file at migration time. `reassemble` is production code — the write API uses it to detect whether a body edit also touched the result section.\n"
                "\n"
                "**Ids come from a Postgres sequence** (`allocate_task_id`); no client scans anything for the next number, and a created-then-deleted workitem burns one — accepted, by decision.\n"
                "\n"
                "**What binds here is a query.** Ask `GET /api/workitems?binding=atlas-work-store` rather than trusting any list in this text. (The per-node backlog endpoint once named here was cancelled on 2026-08-28, and there are no epic files: parenthood is a `contains` edge.)"
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-work-store/architecture.md",
                    "kind": "architecture",
                    "title": "The workitem record — field by field",
                    "format": "md",
                    "content": (
                        "# atlas-work-store — the workitem record\n"
                        "\n"
                        "Where each part of a workitem is stored and what writes it. The reference table this node's\n"
                        "description points at. Counts are live as of 2026-08-14.\n"
                        "\n"
                        "## Field → storage → writer\n"
                        "\n"
                        "| part of the record | stored in | written by | notes |\n"
                        "|---|---|---|---|\n"
                        "| id | `node.id` | `allocate_task_id` (tasks) or caller (epic/story slugs) | a task must **not** supply one; an epic/story must |\n"
                        "| type | `node.type` | create route | `epic` \\| `story` \\| `task`, all `workitem` family |\n"
                        "| title | `node.name` | create/patch | prose register, rendered verbatim |\n"
                        "| status | `node.attributes[\"status\"]` | `POST /api/node/{id}/status`, or the workitem PATCH | one branch for every node since `status_override` was deleted |\n"
                        "| assignee | `node.assignee_user_id` (an `auth_user.id`) | `POST`/`PATCH /api/workitem` | **NULL means unassigned.** Deliberately not a foreign key — the column comment says why. Assignment is **not** a grant: it never enters `_visible_node_ids`, so an item bound to a project the assignee has no grant on is absent from every route for them, `?assignee=me` included |\n"
                        "| spec prose | `node.body` (verbatim markdown) | `PATCH /api/workitem` — **wholesale replace** | 876 KB over 326 items |\n"
                        "| acceptance list | `acceptance_criterion` (`node_id`, `position`, `text`) | create/patch, delete-and-reinsert | 1,546 rows, 307 items, max 21 on one. A **Bearer** principal is refused (403) on an existing item; a browser session may, and creation is unaffected |\n"
                        "| recorded outcome | `workitem_result` (`heading`, `text`, `position`) | the `result` field — **never** touches `body` | 234 rows, 176 KB |\n"
                        "| comments | `workitem_comment` (`node_id`, `body`, `created_at`, `created_by_user_id`/`_subject`, `edited_at`, `deleted_at`, `deleted_by_user_id`/`_subject`) | `POST /api/workitem/{id}/comments`; `PATCH`/`DELETE /api/comment/{id}` (`routers/comments.py`) | since 2026-10-28. The author is copied off the principal, never the body. Only the author edits; the author or an instance owner deletes. Read = whoever sees the item, write = editor. Each act is a `node.comment.*` graph_event naming the id, never the text. Searched since 2026-10-29 (`matched_in: comment`, full text only). **A delete hides, never removes** (since 2026-10-30): `deleted_at` and the deleter are set, the row and its text stay, and every read (list, search, snippet) filters `deleted_at IS NULL`; editing or deleting it again is 404 |\n"
                        "| comment edit history | `workitem_comment_revision` (`comment_id`, `body`, `written_at`, `replaced_at`, `replaced_by_user_id`/`_subject`) | `PATCH /api/comment/{id}`, in the same transaction as the overwrite, only when the text changes | since 2026-10-30. Every text a comment had before an edit replaced it. An audit record: no route reads it, read it in the database |\n"
                        "| parent | `contains` edge, workitem → workitem | create/patch | `WORK_HIERARCHY_EDGE_TYPE` |\n"
                        "| world binding | `works_on` edge, workitem → object | create/patch | **at most one** (ADR 0028), enforced in the generic edge route too |\n"
                        "| dependencies | `depends_on` edges | create/patch | 288 live |\n"
                        "\n"
                        "**`owner` is retired on a workitem and is not in this table on purpose.** Who a workitem belongs to\n"
                        "is an *account* (`assignee`), not a string. `POST`/`PATCH /api/workitem` refuse the field outright —\n"
                        "`400 {\"error\": \"owner_retired\", \"field\": \"owner\"}`, naming the field so a stale caller can act on it\n"
                        "without reading this document. `owner` did not go away in general: on an object-family node it still\n"
                        "means *who looks after this part of the world*, and it is written through `POST`/`PATCH /api/node`.\n"
                        "\n"
                        "`position` is meaning in both side tables, for the same reason: an acceptance list is an ordered\n"
                        "checklist, and a result block is the *n*-th `##` section of its body — a **section index**, not a\n"
                        "byte offset, so it survives edits elsewhere in the document.\n"
                        "\n"
                        "## Why three tables and not one blob\n"
                        "\n"
                        "`PATCH /api/workitem` assigns `body` wholesale. Anything living inside `body` is therefore destroyed\n"
                        "by any edit that does not carefully reproduce it, and that is not hypothetical: recording outcomes\n"
                        "on TASK-1051 / 1053 / 1055 cost 3,291 / 2,513 / 2,170 characters, recoverable then only because the\n"
                        "markdown still existed. Splitting acceptance and result into their own rows makes that class of loss\n"
                        "**structurally impossible** rather than merely discouraged. All 7,974 characters were reconciled\n"
                        "before the files were deleted.\n"
                        "\n"
                        "## Reading it back\n"
                        "\n"
                        "| want | call |\n"
                        "|---|---|\n"
                        "| one complete workitem | `GET /api/node/{id}` |\n"
                        "| a filtered list | `GET /api/workitems?type=&status=&parent=&binding=&assignee=&page=` |\n"
                        "| full-text over prose, acceptance, results and comments | `GET /api/workitems?q=…` |\n"
                        "| one person's list | `?assignee=<auth_user.id>`, or `me`, or `none` for unassigned |\n"
                        "| the `contains` chain per row, root-first and access-filtered | add `?include_ancestors=1` |\n"
                        "| the prose with a listing | add `?include_prose=1` — expensive on purpose |\n"
                        "| an item's comments, oldest first | `GET /api/workitem/{id}/comments` (board: the panel's Comments tab; MCP: `atlas_comments`); deleted ones are not returned |\n"
                        "| a deleted comment, or what a comment said before an edit | the database only: `workitem_comment WHERE deleted_at IS NOT NULL`, `workitem_comment_revision` |\n"
                        "\n"
                        "Status is the one filter that cannot be a single SQL predicate: the effective value is normalised\n"
                        "and bucketed by `attention.status_bucket`, so `pending` and `planned` match each other. It is applied\n"
                        "over the candidate set every other filter has already bounded, so `total` still means \"after every\n"
                        "filter\" — including node visibility, which is applied inside the SQL so a hidden node cannot be\n"
                        "inferred by comparing a page against an unfiltered total.\n"
                    ),
                },
            ],
        },
        {
            "id": "atlas-world-model",
            "type": "capability",
            "name": "World model",
            "summary": "The vocabulary of the graph, node families, edge types, roots and how work binds to world objects: what the graph may say, as opposed to atlas-graph, which stores it.",
            "abstract": "`atlas-world-model` is the **vocabulary** — what the graph is used to say. `atlas-graph` is the machinery that stores and serves it; this node decides what may be stored: which node families exist, what each edge type means, where the roots are, and how a piece of work binds to a world object. Change the storage engine and this node is untouched; change what `contains` means and every consumer moves.",
            "detail": (
                "See `docs/nodes/atlas-world-model/architecture.md` (this node's Docs tab) for the roots and edge vocabulary diagram.\n"
                "\n"
                "**The edge vocabulary is a TABLE now, not a list in prose** (2026-08-26). `edge_type` holds one row per type — its legal `(src_family, dst_family)` pairs, a description of what it means, and a `status` of `active` or `dead` — seeded every boot by `node_types.ensure_edge_types()`, exactly as `node_type` is, and read live at `GET /api/vocabulary`. Query it rather than trusting any list, including the one in this node's own abstract: that prose is the human explanation and this table is the authority.\n"
                "\n"
                "**What renders, and what does not.** On the legacy board (`/board/legacy`), the map draws the non-`contains` world edges as dashed arcs behind the Links control, but only the types in `board.html`'s `WORLD_EDGES` list and its `#xedgeType` dropdown. **`instance_of` is not among them** — it is active and family-validated, yet absent from `WORLD_EDGES`, the dropdown and `board.css`, so it renders nowhere (checked 2026-09-10 and again 2026-09-15: no file under `frontend/` mentions it).\n"
                "\n"
                "**Drift is reported, not enforced.** `/api/health` carries `edge_type_warnings`, a list that is normally empty (the `token_warnings` shape, never a boolean), with four conditions: a type in use that no row declares; an `active` type with zero rows; a `dead` type that has GAINED rows; and a `(src_family, dst_family)` combination in real use that the type's `pairs` does not list. The third is the one the table exists for — `serves` is declared `dead`, and a single `serves` row would bring back the rollup double-count ADR 0028 exists to prevent, so that hazard is now *watched* rather than *remembered*. It returns `[]` on live and legitimately non-empty on staging, whose world predates the ADR 0030 restructure.\n"
                "\n"
                "**Where the vocabulary is still ENFORCED, and the hole that remains.** `OBJECT_TYPES` and `BRIDGE_TYPES` live in `backend/node_types.py`. On the legacy board (`/board/legacy`) they **are copied in `frontend/state.js`** — the frontend copy is what splits world roots from work roots at boot, so a type added to one and not the other is silently treated as a workitem. They move together. Board v2 holds no copy of the families: it filters with `families=` on `/api/graph`. On edges, `EDGE_FAMILY_RULES` in `deps.py` still constrains `contains`, `uses`, `instance_of` and `requires` to object → object and `works_on` to workitem → object, and **`_check_family` treats a type absent from that dict as a permissive NO-OP** — so `depends_on`, `hosted_on`, `contributes_to` and both `dead` types get no family check at all through generic `POST /api/edge`. `_refuse_second_binding` still enforces the single binding on both that route and the workitem routes.\n"
                "\n"
                "## Where work binds\n"
                "\n"
                "Level 1 is the roots: the five ADR 0030 set, plus any project added since without a parent. Level 2 is layer words that transplant to any project — `atlas-frontend`, `atlas-backend`, `atlas-security`, `atlas-ingress`, `atlas-ops` — with capability nodes beneath a layer where finding the work justified one. ADR 0030 carries the two rules that settle a contested case: **a world node outlives the work** (finished when the epic is finished → it is an epic), and **bind to the layer whose files you change, to the capability when the change is policy**. Three things it does not say:\n"
                "\n"
                "- **There is no ceiling and no promotion threshold.** Split a node, or promote a documented sub-component to one, when finding the work in it actually gets hard — and say what got hard. See the `node-holds-open-work` intent.\n"
                "- **Panel sub-components** (the things inside the Overview tab, and the like) are **documented on their tab's node, not contained** beneath it.\n"
                "- **Ask before assigning a binding on someone else's behalf** — the `ask-before-binding` intent.\n"
                "\n"
                "**Unbound work is visible, not blocked.** The required-world-link validator was deleted: it was non-fatal, so it enforced nothing, and it was satisfiable by `serves` while the item still had no world object. The real answer is `attention.effective_bindings()` — an unbound item counts against its nearest bound ancestor instead of vanishing.\n"
                "\n"
                "## Intent: durable purpose, inherited downward\n"
                "\n"
                "ADR 0032 (in `project-atlas/docs/adr/`) is the authority — the union-over-parents rule, slug shadowing as the only override, and the four kinds (`constraint` a bug when violated, `principle` a defect, `non-goal` a removal, `outcome` direction with no work) with what each obliges an agent to DO. **Work rolls UP; intent rolls DOWN**, and that is not a contradiction of ADR 0028: work is a quantity, and rolling it down double-counts; intent is a constraint, and a constraint on the whole binds the part.\n"
                "\n"
                "The surface: `GET /api/intents` is the corpus query (what `atlas_intents` reads, owner side only); `GET /api/node/{id}` carries `intents.{own,inherited}`; `/api/graph` carries `intent_count` — OWN only — for the `◎` glyph. **Agents write intents** (ADR 0033) through `PATCH`/`POST` at `write` scope, and a delete costs `admin`. `tools/seed-intents/` was the initial authoring and documents its own create-only refusal; **the board outranks it** the moment the rows exist.\n"
                "\n"
                "## Things the tree cannot tell you\n"
                "\n"
                "**Two counting traps.** Count workitems per node with `NOT EXISTS`, never a `LEFT JOIN` against `edge` — the join multiplies an item by its binding edges. And a node can have several parents: `max` rollups are idempotent and fine, `sum` rollups must collect a **set** of reachable ids.\n"
                "\n"
                "ADR 0030, which set this shape (and its 2026-08-15 amendment retiring the workitem-count ceiling), is not in this repo. The two items once open here — `decomposes_into` (TASK-1030) and structural roots (TASK-1031) — were both cancelled on 2026-08-27. Several code comments in `app.py`, `attention.py` and `models_graph.py` still say \"until TASK-1030\"; they predate the cancellation and are wrong in the same way this paragraph used to be."
            ),
            "docs": [
                {
                    "path": "docs/nodes/atlas-world-model/architecture.md",
                    "kind": "architecture",
                    "title": "The world model — roots and edge vocabulary",
                    "format": "mermaid",
                    "content": (
                        "# atlas-world-model — roots and edge vocabulary\n"
                        "\n"
                        "The shape the board asserts, and the grammar that produces it. Both diagrams are drawn from the live\n"
                        "graph, not from a design sketch. How many rows each edge type carries is a query — ask\n"
                        "`GET /api/health` — and is deliberately not written down here.\n"
                        "\n"
                        "## The roots\n"
                        "\n"
                        "There is **nothing above these**. A root is derived as \"a node with no incoming `contains`\", so the\n"
                        "list is a measurement, not a constant. The picture shows the five ADR 0030 set. A project added\n"
                        "since without a parent is a root as well; `GET /api/graph/tree` gives the live list, not this sentence.\n"
                        "The lateral edges below are the only relationships between them, drawn here for the five.\n"
                        "\n"
                        "Two things this picture is making a point of:\n"
                        "\n"
                        "## The grammar\n"
                        "\n"
                        "```mermaid\n"
                        "graph LR\n"
                        "  W[\"workitem family<br/>epic · story · task\"]\n"
                        "  O[\"object family<br/>org project system service component<br/>agent db host capability\"]\n"
                        "  B[\"bridge family<br/>skill · vision (doc: retired, still seeded)\"]\n"
                        "\n"
                        "  W -->|contains| W\n"
                        "  W -->|depends_on| W\n"
                        "  W -->|works_on ≤ 1| O\n"
                        "  O -->|contains| O\n"
                        "  O -->|uses| O\n"
                        "  O -->|requires| O\n"
                        "  O -->|instance_of| O\n"
                        "  O -->|hosted_on| O\n"
                        "  O -->|contributes_to| O\n"
                        "  O -->|contains| B\n"
                        "```\n"
                        "\n"
                        "| edge | means | enforced by family on write? |\n"
                        "|---|---|---|\n"
                        "| `contains` | **is part of.** The product decomposition. Not ownership, not permissions. | yes, object → object |\n"
                        "| `works_on` | this work binds to that world object. **At most one per item.** | yes, workitem → object |\n"
                        "| `depends_on` | this work waits on that work | no |\n"
                        "| `hosted_on` | where it **runs**, as opposed to what it is part of | no |\n"
                        "| `instance_of` | a deployment points at what it deploys | yes, object → object |\n"
                        "| `contributes_to` | party → project. Many-to-many. | no |\n"
                        "| `uses` | one object stands on another without owning it | yes, object → object |\n"
                        "| `requires` | a **hard** run-time dependency: this object does not work without that one (ADR 0044). Not `depends_on`, which orders work | yes, object → object |\n"
                        "| `serves` | retired sibling of `works_on`. **Declared `dead`** — see below | n/a |\n"
                        "\n"
                        "## The vocabulary is a table, and drift is watched\n"
                        "\n"
                        "**`edge_type` is the authority, not the table above** (added 2026-08-26). One row per type, carrying\n"
                        "its legal `(src_family, dst_family)` `pairs` — a LIST, because `contains` legitimately has three —\n"
                        "a description of what the type means, and a `status` of `active` or `dead`. It is seeded every boot\n"
                        "by `node_types.ensure_edge_types()`, exactly as `node_type` is, and read live at\n"
                        "`GET /api/vocabulary`.\n"
                        "\n"
                        "What the registry buys instead is `/api/health`'s **`edge_type_warnings`** — a list that is normally\n"
                        "empty, never a boolean that is permanently green — with four conditions: a type in use that no row\n"
                        "declares; an `active` type with zero rows; a `(src_family, dst_family)` combination genuinely in use\n"
                        "that the type's `pairs` does not list; and the one this table exists for, **a `dead` type that has\n"
                        "GAINED rows**.\n"
                        "\n"
                        "**That last condition is about `serves`.** It is the retired sibling of `works_on` for the\n"
                        "workitem → object binding, it is still accepted by the read filters, and it is declared `dead`\n"
                        "deliberately: a single live `serves` row reintroduces the rollup double-count that ADR 0028 exists\n"
                        "to prevent, because an item would then reach a node down two binding edges. The hazard used to be\n"
                        "*remembered*, in prose, in three places. It is *monitored* now. (`for` is declared `dead` too, for\n"
                        "the ordinary reason: retired vocabulary kept for history.)\n"
                        "\n"
                        "## Two rules that decide the arguments\n"
                        "\n"
                        "**A world node outlives the work.** If it is finished when the epic is finished, it is an epic.\n"
                        "\n"
                        "**Bind to the layer whose files you change; bind to the capability when the change is policy.** Login\n"
                        "*screens* are the board shell; password *storage* is identity. Ingress is the path in; security is\n"
                        "who may pass.\n"
                        "\n"
                        "## Sizing\n"
                        "\n"
                        "**There is no ceiling and no promotion threshold.** The 5–25 open-workitem band this section used to\n"
                        "state was **retired on 2026-08-15**, by an amendment to ADR 0030, after it fired on `atlas-mcp` the\n"
                        "moment an epic was filed against it — which is the failure mode, not the rule working. A node\n"
                        "acquires work because work exists, not because it became hard to read, and a number cannot tell you\n"
                        "whether work is findable.\n"
                        "\n"
                        "So: **split a node — or promote a documented sub-component to one — when finding the work in it\n"
                        "actually gets hard, and say what got hard.** A count is a description, never a trigger.\n"
                        "\n"
                        "The one piece of the old reasoning that still stands: if you count at all, count **open** work —\n"
                        "status not `done` or `cancelled`, the same lens the board's age-out filter uses. **Never count\n"
                        "lifetime.** A lifetime count only rises, so a threshold on it fires in every node eventually, purely\n"
                        "from work being finished, splitting the tree forever while findability never actually got worse.\n"
                        "That had already caused one unnecessary split before the rule was written down.\n"
                    ),
                },
            ],
        },
        {
            "id": "panel-comments",
            "type": "component",
            "name": "Comments tab",
            "summary": "The node panel's Comments tab: the markdown conversation on an epic, story or task, where editors write and authors edit or delete their own.",
            "docs": [],
        },
        {
            "id": "panel-config",
            "type": "component",
            "name": "Config tab",
            "summary": "The node panel's owner-only Configuration tab: each setting belonging to an install part, its effective value and where it comes from, with secrets shown as state only.",
            "docs": [],
        },
        {
            "id": "panel-decisions",
            "type": "component",
            "name": "Decisions tab",
            "summary": "The node panel's Decisions tab: the architecture decision records of a project, or of any node with records, as a list, and one record's status, links, source and text.",
            "docs": [],
        },
        {
            "id": "panel-docs",
            "type": "component",
            "name": "Docs tab",
            "summary": "The node panel's Docs tab: a row per entry in the node's docs[], each fetched on open and rendered as markdown with diagrams; external documents are only labelled.",
            "abstract": (
                "`panel-docs` is the node panel's Docs tab, `frontend/v2/views/panel/docs.js`: one expandable row per `docs[]` entry, fetched from `GET /api/node/{id}/doc?path=` when first opened and rendered as markdown with Mermaid diagrams (`ui/md.js`).\n"
                "\n"
                "**`docs[]` is an allowlist, not a directory listing**, and that is the tab's boundary. The read route matches `path` **exactly** against this node's own entries before it touches any table, so a node can only ever read its own documents and an unlisted path gets a 404 with no lookup at all. Since **ADR 0029** the content lives in Postgres (`node_doc`), not on disk: there is no `open()`, and therefore no path-traversal guard — the old one was deleted rather than left in place misstating what the code does.\n"
                "\n"
                "Two states the tab is careful about. An entry marked `external: true` answers **200 with `external: true` and no content**, deliberately distinguishable from a 404 — the point of marking them — so the board can say *\"External reference — kept in git, not served here\"* instead of rendering a convincing blank document. And **\"stored but not served\"** is a real, expected state, not a defect: `node_doc` holds rows that no `docs[]` entry lists, and `GET` 404s those until an entry is added. Storing a document and publishing it are separate acts — which is exactly why adding an entry for a path whose content you did not write publishes somebody else's document."
            ),
            "detail": (
                "**Read-only.** The tab renders documents; it does not author them. `PUT` and `DELETE /api/node/{id}/doc` exist (TASK-1076, the authoring half of ADR 0029) but nothing in this tab calls them, and *publishing* a stored document stays a separate visible act — a PATCH on the node's `attributes`. `PUT` reports `served: false` with an `expected-not-served` note when the allowlist entry is missing, which is how that state is meant to be discovered.\n"
                "\n"
                "**Behaviour worth knowing.** The badge is `docs.length`, known synchronously; most nodes show none and get *\"No docs attached.\"* Viewer content loads lazily on first expand and is not refetched on collapse. A Mermaid failure renders a visible `Diagram render failed: …` line rather than a blank pane. An external entry opens to a note that its content lives outside Atlas; a listed path with no stored content says so instead of rendering blank. A node with one document opens it at once.\n"
                "\n"
                "**Paths are database keys, not files.** `docs/nodes/` was deleted from git on 2026-08-12; writing a file there would not make the board serve anything.\n"
                "\n"
                "One workitem binds here: **TASK-871** (`done`) — serve, link and render node docs, Mermaid included, in the board."
            ),
            "docs": [
                {
                    "path": "docs/nodes/panel-docs/architecture.md",
                    "kind": "architecture",
                    "title": "architecture.md",
                    "format": "md",
                    "content": (
                        "# panel-docs\n"
                        "\n"
                        "**Legacy only.** This describes `frontend/panel.js` on the frozen `/board/legacy`; board v2's Docs tab is described on the node itself.\n"
                        "\n"
                        "## What it is\n"
                        "\n"
                        "`panel-docs` is the detail panel's Docs tab — the reader for a node's authored documentation\n"
                        "(this file, once applied, is exactly the kind of content it renders). It is a region of\n"
                        "`frontend/panel.js`; see `panel-overview` for the tab machinery it shares (badges, the remembered\n"
                        "tab) and for the `marked` configuration this tab's viewer also uses. It lists\n"
                        "`node.attributes.docs[]`, one row per entry (`{kind, title, path, format}` — the shape ADR 0020\n"
                        "fixed), and for entries rooted at `docs/nodes/` the row header is a button that expands an inline\n"
                        "viewer.\n"
                        "\n"
                        "## How it works\n"
                        "\n"
                        "**`docs[]` is metadata on the node; the content is a separate table, and the read route enforces\n"
                        "that split at the gate rather than after loading anything.** `GET /api/node/{id}/doc?path=…`\n"
                        "(`backend/app.py`) first checks whether `path` is a literal match in *this node's own*\n"
                        "`attributes.docs[]` — before it ever queries `node_doc` — and returns 404 with no lookup at all if\n"
                        "it is not there. A node can only ever read its own listed documents; there is no directory\n"
                        "listing and no traversal, because since ADR 0029 there is no `open()` on this path at all — the\n"
                        "content lives in Postgres, addressed by `(node_id, path)`, and the old path-traversal guard that\n"
                        "used to matter when `path` pointed at a real file was deleted rather than left in place describing\n"
                        "code that no longer runs.\n"
                        "\n"
                        "**Three distinguishable outcomes for one path, not two.** An entry marked `external: true`\n"
                        "answers `200` with `{external: true}` and no content — deliberately distinguishable from the 404\n"
                        "an unlisted path gets, so the board can render *\"External reference — kept in git, not served\n"
                        "here\"* instead of a convincing blank document. A path with no `node_doc` row at all, despite being\n"
                        "listed, is a genuine 404 with a distinct error string. And **\"stored but not served\" is a real,\n"
                        "expected state** — `PUT /api/node/{id}/doc` (the authoring route, TASK-1076) does not require a\n"
                        "`docs[]` entry to exist first, so content can sit in `node_doc` with `GET` 404ing it until someone\n"
                        "adds the allowlist entry as a separate, visible act (a `PATCH` on `attributes`). Storing and\n"
                        "publishing are deliberately two acts, not one.\n"
                        "\n"
                        "**Rendering, once fetched, is `marked` plus one extra pass for diagrams.** `renderDocViewer` parses\n"
                        "the returned markdown through the same escaping `marked` configuration `panel-overview` describes,\n"
                        "then finds every ` <code class=\"language-mermaid\"> ` block `marked` produced from a fenced\n"
                        "` ```mermaid ` fence and replaces it with inline SVG via `mermaid.render` (vendored,\n"
                        "`startOnLoad: false`, `securityLevel: \"strict\"`, rendered on demand rather than auto-scanned at\n"
                        "page load). A failed render prints a visible *\"Diagram render failed: …\"* line rather than leaving\n"
                        "a blank pane or throwing.\n"
                        "\n"
                        "```mermaid\n"
                        "sequenceDiagram\n"
                        "  participant U as reader\n"
                        "  participant P as panel.js\n"
                        "  participant API as GET /node/{id}/doc\n"
                        "  participant DB as node_doc\n"
                        "  U->>P: click a docs/nodes/* row\n"
                        "  P->>API: fetch ?path=...\n"
                        "  API->>API: path in this node's docs[]? (checked BEFORE any query)\n"
                        "  alt not listed\n"
                        "    API-->>P: 404, no lookup performed\n"
                        "  else listed, external:true\n"
                        "    API-->>P: 200 {external:true}, no content\n"
                        "  else listed, internal\n"
                        "    API->>DB: select content where (node_id, path)\n"
                        "    DB-->>API: content, or none (404: \"stored\" gap)\n"
                        "    API-->>P: 200 {content}\n"
                        "  end\n"
                        "  P->>P: marked.parse -> escape raw HTML\n"
                        "  P->>P: mermaid.render each fenced block -> inline SVG\n"
                        "```\n"
                        "\n"
                        "## What will bite someone\n"
                        "\n"
                        "- **The tab is read-only, and that is a real gap, not a style choice.** `PUT`/`DELETE\n"
                        "  /api/node/{id}/doc` exist; nothing in this tab calls either. Authoring or fixing a typo in a\n"
                        "  document happens outside the board entirely (`tools/seed-node-docs/apply_node_docs.py`, or a\n"
                        "  direct API call) — the Docs tab has no \"edit\" affordance to reach for.\n"
                        "- **External rows are plain, non-clickable `<div>`s today**, which means `renderDocViewer`'s\n"
                        "  `external: true` branch is currently unreachable from the UI — nothing renders it. It stays in\n"
                        "  the code precisely because it is one deleted filter away (making an external row clickable\n"
                        "  again) from a silent blank page if it were removed instead.\n"
                        "- **A `docs[]` entry and its `node_doc` content are independently writable and independently\n"
                        "  deletable**, and neither write touches the other. Adding an entry for a path whose content was\n"
                        "  never written, or whose content was deleted separately, publishes a document that 404s —\n"
                        "  distinguishable from \"external\" but still a broken row a reader has to click to discover.\n"
                        "- **Paths are database keys, not files, and there is nothing named `docs/nodes/` in this\n"
                        "  repository any more** — that directory was deleted from git on 2026-08-12. Writing a file at a\n"
                        "  path that matches an entry's `path` string does not make the board serve it; only\n"
                        "  `PUT /api/node/{id}/doc` does.\n"
                    ),
                },
            ],
        },
        {
            "id": "panel-history",
            "type": "component",
            "name": "History tab",
            "summary": "The node panel's History tab: who changed this node and on whose authority, read from the graph event log on first open and paged by the server's total.",
            "docs": [],
        },
        {
            "id": "panel-intent",
            "type": "component",
            "name": "Intent tab",
            "summary": "The node panel's Intent tab: the node's own intents, then those it inherits grouped by the node they come from, as the API computes them.",
            "docs": [],
        },
        {
            "id": "panel-links",
            "type": "component",
            "name": "Links tab",
            "summary": "The node panel's Links tab: every edge of the node, world nodes included — dependencies first for work, then the rest grouped by edge type and direction.",
            "abstract": "`panel-links` is the node panel's Links tab, `frontend/v2/views/panel/links.js`: every connection the node has, world nodes included. For work, **Dependencies** come first: *Blocked by* (its `depends_on`) and *Blocks* (what depends on it). Then **Connections**: every other edge, grouped by type and direction (`contains →`, `← contains`, `hosted_on →`, `requires →` / `← required by`, …), at most 30 rows a group. A world node's work is not listed again here; it is on the work board. Clicking a world node focuses the graph on it, and clicking work selects it. The rows come from the graph index the board already holds (one bounded `/api/graph` read, see `atlas-frontend`), with no request of their own. An edge to a node outside that read is therefore not shown.",
            "detail": (
                "The legacy tab (`frontend/panel.js`) showed only `depends_on` and sub-nodes.\n"
                "\n"
                "One workitem binds here: **TASK-862** (`done`) — the cross-link connections panel."
            ),
            "docs": [
                {
                    "path": "docs/nodes/panel-links/architecture.md",
                    "kind": "architecture",
                    "title": "architecture.md",
                    "format": "md",
                    "content": (
                        "# panel-links\n"
                        "\n"
                        "## What it is\n"
                        "\n"
                        "`panel-links` is the detail panel's Links tab — how a node connects **sideways and downwards**, as\n"
                        "distinct from what work is on it (`panel-work`) or what it is for (the Intent tab). It is a region\n"
                        "of `frontend/panel.js`; see `panel-overview` for the tab machinery it shares. It has two sections,\n"
                        "present or absent by `isObject(node)`:\n"
                        "\n"
                        "- a **workitem** gets **Depends on**, both directions — *Blocked by* (this node's outbound\n"
                        "  `depends_on`) and *Blocks* (inbound);\n"
                        "- an **object** gets that section replaced by one line pointing at Work, because a dependency\n"
                        "  between world objects is not a relationship this model expresses.\n"
                        "\n"
                        "Both cases end with **Sub-nodes** — `node.children` off the already-loaded tree payload, one\n"
                        "clickable row per child, or *\"No children — this is a leaf\"* when there are none.\n"
                        "\n"
                        "## How it works\n"
                        "\n"
                        "**The dependency lists come from a second network call the panel already had the data for.**\n"
                        "`GET /api/node/{id}` (the same route `panel-overview`'s `fetchFull` calls for Acceptance, Result\n"
                        "and Intent) already returns `dependencies` and `dependants`, computed server-side from the same\n"
                        "`depends_on` edges. This tab does not use that field — it issues its own\n"
                        "`GET /api/graph?focus={id}&depth=1` and filters the returned edge list client-side for\n"
                        "`edge_type === \"depends_on\"`, splitting on which side of the edge `node.id` sits on. Nothing in\n"
                        "the code explains why the two were not collapsed into one call; treat it as unfinished rather than\n"
                        "as a deliberate design, and as the first thing to fix if this tab is touched for another reason —\n"
                        "collapsing it removes one request per panel open.\n"
                        "\n"
                        "**The Links badge tells you, from the outside, which of the two branches ran.** It starts as\n"
                        "`kids.length` (synchronous, from the tree payload already in memory) and is topped up to\n"
                        "`blockedBy + blocks + children` only once the `depends_on` fetch resolves — which never happens\n"
                        "for an object, so an object's badge staying at the raw child count is correct, not a stuck loading\n"
                        "state. A failed fetch prints *\"Could not load links\"* into both dependency lists and leaves the\n"
                        "badge where it was.\n"
                        "\n"
                        "**Rows here bypass the cross-tab navigation seam on purpose.** Every row (`renderLinkRow`,\n"
                        "`renderSubnodeRow`) calls `selectNode` directly, imported straight from `map.js`, rather than going\n"
                        "through `hooks.navigateTo` the way `panel-work`'s Connections rows do. That is deliberate, not an\n"
                        "inconsistency: a sub-node or a `depends_on` neighbour is always in the *same* tree (World or Work)\n"
                        "as the node already open, so there is never a tab switch to bridge, and the extra seam would be\n"
                        "dead code on this tab specifically.\n"
                        "\n"
                        "## What will bite someone\n"
                        "\n"
                        "- **This tab shows a fraction of the edge types the graph actually has.** It draws `contains`\n"
                        "  (downward, as Sub-nodes) and `depends_on` only. `hosted_on`, `instance_of`, `contributes_to` and\n"
                        "  `uses` — the non-`contains` edges the map draws as dashed arcs behind the Links control — reach\n"
                        "  nowhere in this panel today. A node with real lateral relationships of those kinds shows nothing\n"
                        "  about them here; that is a gap, not a rendering choice about how to display zero.\n"
                        "- **`contains` is rendered downward only.** A node's *parents* — what contains it — are not shown\n"
                        "  in this tab at all (or anywhere else in the panel); Sub-nodes is strictly children.\n"
                        "- **The redundant fetch is a real, standing inefficiency, not a hypothetical one** — every panel\n"
                        "  open on a workitem issues both `fetchFull`'s `/api/node/{id}` and this tab's\n"
                        "  `/api/graph?focus=...&depth=1`, and the latter's only job is to reproduce two fields the former\n"
                        "  already carries.\n"
                    ),
                },
            ],
        },
        {
            "id": "panel-overview",
            "type": "component",
            "name": "Overview tab",
            "summary": "The node panel's default tab: intent titles, work facts and their edit controls, progress, description, acceptance and result, documents, and where the node sits.",
            "abstract": (
                "`panel-overview` is the node panel's default tab, `frontend/v2/views/panel/overview.js`. It answers the questions in the order they arrive: what this is for (intent titles; the statements are on Intent), the small facts, how far along, what it is, where to read more, where it sits.\n"
                "\n"
                "- **For work** (*This item*): status, assignee, priority, labels, *Works on* (the one `works_on` binding, ADR 0028, or the one inherited from the epic), epic, story, parent (a move, TASK-1355) and finished date. Changing an existing binding is not supported on board v2 yet (TASK-1508); remove it first. Status goes through `POST /api/node/{id}/status`; the rest through `PATCH /api/workitem`. The description (`body`) and the acceptance list are edited in place. A pending acceptance proposal from an agent shows as a diff with **Approve** / **Reject**, which work only from a person's session, and the last attested change is named (STORY-atlas-acceptance-human-approved). Result shows under its own heading (`result_heading`).\n"
                "- **For a project**: its task-id key, read-only.\n"
                "- **For any node with work below it**: tiles (done %, active, blocked, need attention), a stacked bar, and a link to the open items on the work board.\n"
                "- **Description**: a world node's `abstract`, with `detail` under *More*; a workitem's `body`.\n"
                "- **Documentation**: one link per `docs[]` entry, opening it on the Docs tab.\n"
                "- **Context** (world nodes): its containers up to a root, the types it contains, and attached skills.\n"
                "\n"
                "World nodes get no write controls."
            ),
            "detail": (
                "See `docs/nodes/panel-overview/architecture.md` (this node's Docs tab).\n"
                "\n"
                "**Where the data comes from.** Everything below the head comes from the panel's one `GET /api/node/{id}?intents=all` per selection; the tiles are counted from the graph index (`workCounts`). An empty acceptance list prints *\"No acceptance criteria yet.\"* rather than dropping its heading.\n"
                "\n"
                "The result heading is read from `result_heading` rather than hardcoded, because some workitems write `## Outcome` instead of `## Result`. Result lives in `workitem_result` and never touches `body`.\n"
                "\n"
                "Two pieces of prior work shaped this tab: rendering prose as markdown rather than literal asterisks, back when the files were still the only copy; and giving Result its own row so the panel could show what actually happened, not just the spec."
            ),
            "docs": [
                {
                    "path": "docs/nodes/panel-overview/architecture.md",
                    "kind": "architecture",
                    "title": "architecture.md",
                    "format": "md",
                    "content": (
                        "# panel-overview\n"
                        "\n"
                        "**Legacy only.** This describes `frontend/panel.js` on the frozen `/board/legacy`; board v2's Overview tab is described on the node itself.\n"
                        "\n"
                        "## What it is\n"
                        "\n"
                        "`panel-overview` is the detail panel's default tab — what shows when a node is selected, and\n"
                        "what `renderPanel` falls back to if the remembered `atlas.panelTab` key names a tab that no longer\n"
                        "exists. It is a **region of `frontend/panel.js`**, not a module of its own: every tab in\n"
                        "`PANEL_TABS` — `overview`, `intent`, `work`, `history`, `docs`, `links` — is a `tabDefs` entry and a\n"
                        "`tabBodies` div built inline by one function, `renderPanel`, not one file each. Four of them have a\n"
                        "component node of their own (`panel-overview`, `panel-work`, `panel-docs`, `panel-links`); Intent\n"
                        "and History do not, and are documented on `atlas-panel` until one earns a node. This node is the\n"
                        "right place for the machinery all the tabs share; see `panel-work`, `panel-docs` and `panel-links`\n"
                        "for what is particular to each.\n"
                        "\n"
                        "## How it works — the machinery every tab shares\n"
                        "\n"
                        "**One `GET /api/node/{id}` fetch per panel open, cached by node id.** `fetchFull(nodeId)` is\n"
                        "called by every section that needs the per-item detail the tree payload deliberately omits\n"
                        "(acceptance, the result block, the current binding, the body the description editor opens with,\n"
                        "and — via the tab machinery — the same payload backs the badge math). It returns the same in-flight\n"
                        "promise to every caller within one render, so opening a node does not multiply the request per\n"
                        "section that wants it, and `renderPanel` resets the cache (`_fullCache = null`) on every call —\n"
                        "including re-selecting the *same* node after a write — so a write is never masked by a stale cached\n"
                        "read.\n"
                        "\n"
                        "**Tabs, badges and the remembered tab, in one pass.** `renderPanel` builds one `tabDefs` entry per\n"
                        "`PANEL_TABS` value, each a button plus a body div; a badge is known either synchronously (docs'\n"
                        "count, an object's `work_load.count`, links' child count) or set later via `setTabBadge` once an\n"
                        "async fetch settles (a workitem's Work/Links counts, Intent's combined own+inherited count,\n"
                        "History's `total` from `GET /api/events`). History is also the one tab whose fetch is deferred:\n"
                        "`showPanelTab` calls its `ensureLoaded` the first time the tab is actually shown, so a panel open\n"
                        "costs nothing until someone asks for the audit trail. The active tab is persisted through\n"
                        "`remember(PANEL_TAB_KEY, which)` / `recall`, under the `atlas.*` namespace like every other piece of\n"
                        "view state, and restored on the next panel open rather than always landing on Overview.\n"
                        "\n"
                        "**Markdown is rendered, not printed, and raw HTML is escaped, not trusted — one configuration,\n"
                        "several call sites.** `renderMarkdown(el, text, inline)` wraps the vendored `marked` parser;\n"
                        "`ensureMarkedConfigured()` runs once and overrides `marked`'s HTML renderer to escape rather than\n"
                        "emit raw `<html>` tokens, because prose reaches the panel from Postgres via an authenticated write\n"
                        "API — authenticated is not a reason to execute whatever it contains. This one configuration backs\n"
                        "Description, the More disclosure, Acceptance (`inline` mode, so a criterion doesn't get wrapped in\n"
                        "its own `<p>`), the Result block, and intent statements. `panel-docs`' doc viewer renders through\n"
                        "the *same* `marked` instance for its own content, plus a second pass that turns fenced\n"
                        "` ```mermaid ` blocks into inline SVG via the vendored `mermaid.render` — that extra step is\n"
                        "specific to documents, not to Overview, and is described on `panel-docs`.\n"
                        "\n"
                        "## How it works — the Overview tab itself\n"
                        "\n"
                        "**Every control on this tab renders the SERVER's answer, never the value that was picked.** A\n"
                        "control that shows your choice before the write lands lets somebody walk away believing a change\n"
                        "stuck when it did not. The status control, the assignee control and the binding control all follow\n"
                        "that rule identically.\n"
                        "\n"
                        "## What will bite someone\n"
                        "\n"
                        "- **A re-bind is two writes.** `PATCH /api/workitem` refuses a binding onto an item that already\n"
                        "  has one — 409 `binding_exists`, at most one binding per item (ADR 0028) — so *changing* a binding\n"
                        "  sends `binding: null` and then `binding: <new>`. If the second write fails the item would be left\n"
                        "  unbound, a change nobody asked for, so the control restores the previous binding, and if even the\n"
                        "  restore fails it says so on screen. The restore leg deliberately does **not** call `refreshGraph`,\n"
                        "  because the refresh re-renders the panel and would take the error message down with it.\n"
                        "- **A stale label survives in the status control.** `renderStatusSection`'s note under the status\n"
                        "  buttons still reads *\"From the backlog file\"* (plus `node.source` if present). There has been no\n"
                        "  backlog file since TASK-1059 deleted the markdown on 2026-08-12 — every workitem is\n"
                        "  `atlas`-origin and every status write goes straight to Postgres through the API. This is stale\n"
                        "  UI copy, not a stale fact about the system; it should say something like \"via the API,\" not\n"
                        "  point at a file that no longer exists.\n"
                        "- **The empty-section rule is per-section, not per-tab.** Acceptance and Result vanish\n"
                        "  independently; a workitem with acceptance criteria but no result shows one section, not neither\n"
                        "  and not both-with-one-blank.\n"
                        "- **`fetchFull`'s cache is keyed on node id alone, not on a request generation.** Two renders of\n"
                        "  the *same* node in quick succession (a rapid re-click, or a status write immediately followed by\n"
                        "  a second one) share whichever promise was in flight at cache-set time; this has not been\n"
                        "  observed to misbehave, but nothing here guards against a genuine race the way the write controls'\n"
                        "  own disable-while-pending does.\n"
                    ),
                },
            ],
        },
        {
            "id": "panel-work",
            "type": "component",
            "name": "Work tab",
            "summary": "The legacy board's Work tab: the work bound directly to a world node, or a workitem's own bindings; board v2 has no Work tab, the work board replaces it.",
            "abstract": (
                "`panel-work` is the **legacy** board's Work tab (`frontend/panel.js`, served at `/board/legacy`, frozen by ADR 0043). **Board v2's panel has no Work tab**: the work board (`atlas-view-work`), scoped by the graph, and Overview's progress tiles replace it. What follows describes the legacy tab. It is a region of `frontend/panel.js`, and it renders **one of two shapes**, chosen by `isObject(node)`:\n"
                "\n"
                "- an **object** gets `renderWorkList` — synchronous, no fetch, reading `node.work_load` which `/api/graph` and `/api/graph/tree` already attach to every node (`attention.work_load`). Count, done, completion percentage, and a clickable row per workitem;\n"
                "- a **workitem** gets `renderConnections` — a live `GET /api/graph?focus={id}&edges=works_on,serves,for&depth=1`, grouped **Works on** / **Serves**, whose settled count sets the tab badge. (A workitem's own decomposition is not here: its children are the Sub-nodes list on the Links tab.)\n"
                "\n"
                "**What it deliberately does not do** is walk `node.children` to compute a subtree rollup. `work_load` is a rollup over the `works_on` *bridge* edge, not over `contains`, and the tab says so on screen — *\"Direct links only — a rollup over this object's sub-tree isn't computed yet.\"* Re-deriving it client-side would duplicate backend logic over a tree the board has already pruned by the age filter and the detail slider, so the number would drift from the badge with nobody able to say which one lied."
            ),
            "detail": (
                "None of this is built in v2.\n"
                "\n"
                "**No open work binds here.** Everything that did was cancelled on backlog triage: the two `STORY-atlas-node-panel-tabs` tasks in August's first pass, and the panel-dashboard pair (the backlog widget and its filter bar) on 2026-08-27. `GET /api/workitems?binding=panel-work` is the live answer; this paragraph is not.\n"
                "\n"
                "**Two counting rules for whoever ever builds a subtree rollup here.** First, `work_load` counts **effective** bindings since TASK-1025: a workitem with no `works_on` of its own counts against its nearest bound ancestor in the work hierarchy, so an unbound item filed tomorrow is not invisible. Second, the world graph is multi-root and a node may have several `contains` parents — `max` rollups (health, attention) are idempotent and safe, but any `sum` rollup must collect a **set** of reachable ids or a node reachable by two paths is counted twice.\n"
                "\n"
                "**Dead vocabulary on screen.** `renderConnections` still draws a **Serves** group labelled `goal`. `serves` is declared `dead` in the `edge_type` registry precisely because a live row of it reintroduces the rollup double-count hazard ADR 0028 exists to prevent, and `/api/health`'s `edge_type_warnings` is empty — which is how you know no row exists. `goal` itself was retired by ADR 0032. So that group renders \"None.\" on every workitem."
            ),
            "docs": [
                {
                    "path": "docs/nodes/panel-work/architecture.md",
                    "kind": "architecture",
                    "title": "architecture.md",
                    "format": "md",
                    "content": (
                        "# panel-work\n"
                        "\n"
                        "## What it is\n"
                        "\n"
                        "`panel-work` is the detail panel's Work tab — *what work touches this node*. Like the other tabs,\n"
                        "it is a region of `frontend/panel.js`'s `renderPanel`, not a file of its own; see\n"
                        "`panel-overview` for the tab machinery (badges, the remembered tab, `fetchFull`) this tab reuses\n"
                        "rather than repeating.\n"
                        "\n"
                        "It renders one of two shapes, chosen by `isObject(node)`:\n"
                        "\n"
                        "- an **object** gets `renderWorkList` — synchronous, no fetch, reading `node.work_load`, which\n"
                        "  `/api/graph` and `/api/graph/tree` already attach to every node from `attention.work_load`;\n"
                        "- a **workitem** gets `renderConnections` — a live `GET /api/graph?focus={id}&edges=works_on,serves,for&depth=1`,\n"
                        "  grouped **Works on** / **Serves**, whose settled count sets the tab badge.\n"
                        "\n"
                        "A workitem's own decomposition is deliberately *not* on this tab: its children render as the\n"
                        "Sub-nodes list on **Links**. That matters for reading the rollup gap below correctly — an epic,\n"
                        "story or task already shows everything under it; a world object does not.\n"
                        "\n"
                        "## How it works\n"
                        "\n"
                        "**`work_load` is a direct-edge rollup, not a subtree rollup, and the tab says so on screen.**\n"
                        "`attention.work_load` counts only `works_on` edges that land on *this* node — after resolving each\n"
                        "workitem's **effective** binding (TASK-1025: a workitem with no `works_on` of its own inherits its\n"
                        "nearest bound ancestor's binding, walking the *work* hierarchy, never the world one). It does not\n"
                        "walk `node.children` to add up what is bound to something this node contains, and the panel is\n"
                        "explicit about the gap: *\"Direct links only — a rollup over this object's sub-tree isn't computed\n"
                        "yet.\"* Re-deriving that client-side would duplicate backend logic over a tree the board has\n"
                        "already pruned by the age filter and the detail slider, and the two numbers would drift with\n"
                        "neither side able to say which one lied — so it deliberately does not try.\n"
                        "\n"
                        "**Counting rule that will surprise anyone who does revive `below`:** the world graph is\n"
                        "multi-root, and a node may legitimately have several `contains` parents. `attention.work_load`\n"
                        "(and every `max` rollup like health/attention) is idempotent under that — visiting a node twice\n"
                        "changes nothing. A `sum` rollup is not: it must collect a **set** of reachable ids before counting,\n"
                        "or a node reachable by two paths gets counted twice. `own` (a direct edge count) cannot double\n"
                        "count today because there is nothing to sum across. TASK-1026's cancellation note kept two more:\n"
                        "apply `_visible_node_ids` INSIDE the query rather than as a post-filter, because a post-filter\n"
                        "strips rows and still leaks counts; and count with `NOT EXISTS` rather than a `LEFT JOIN` against\n"
                        "`edge`, because the join multiplies an item by its binding edges.\n"
                        "\n"
                        "## What will bite someone\n"
                        "\n"
                        "- **The Serves group renders and is dead.** `renderConnections` still draws a **Serves** section\n"
                        "  labelled `goal` for every workitem it fetches. The `goal` node type was retired by ADR 0032, and\n"
                        "  `serves` is declared `dead` in the `edge_type` registry because a live row of it reintroduces the\n"
                        "  rollup double-count hazard ADR 0028 exists to prevent — `/api/health`'s `edge_type_warnings`\n"
                        "  reports a dead type that gains a row, and it is empty. The group is not wrong, it is just\n"
                        "  permanently empty (`\"None.\"`), and nothing currently removes it the way Acceptance/Result remove\n"
                        "  themselves on Overview.\n"
                        "- **A workitem's badge is asynchronous; an object's is not.** `Work` shows `workLoad.count` (or\n"
                        "  nothing) immediately for an object, because `work_load` already rode in on the tree payload; for\n"
                        "  a workitem the badge is blank until `renderConnections`' fetch resolves. Do not assume the badge\n"
                        "  reflects reality the instant the panel opens.\n"
                        "- **Do not compute a subtree rollup by walking `node.children` in this file.** The panel's own\n"
                        "  comment names this as the trap: the backend already has the logic (`attention.effective_bindings`\n"
                        "  / `attention.work_load`), and a client-side re-derivation over a pruned tree will silently drift\n"
                        "  from anything a future backend ships for `below`.\n"
                    ),
                },
            ],
        },
    ],
    "edges": [
        {
            "src": "atlas",
            "edge_type": "contains",
            "dst": "atlas-backend",
        },
        {
            "src": "atlas",
            "edge_type": "contains",
            "dst": "atlas-frontend",
        },
        {
            "src": "atlas",
            "edge_type": "contains",
            "dst": "atlas-ingress",
        },
        {
            "src": "atlas",
            "edge_type": "contains",
            "dst": "atlas-mcp",
        },
        {
            "src": "atlas",
            "edge_type": "contains",
            "dst": "atlas-ops",
        },
        {
            "src": "atlas",
            "edge_type": "contains",
            "dst": "atlas-security",
        },
        {
            "src": "atlas-api",
            "edge_type": "contains",
            "dst": "atlas-api-group-access",
        },
        {
            "src": "atlas-api",
            "edge_type": "contains",
            "dst": "atlas-api-group-graph",
        },
        {
            "src": "atlas-api",
            "edge_type": "contains",
            "dst": "atlas-api-group-knowledge",
        },
        {
            "src": "atlas-api",
            "edge_type": "contains",
            "dst": "atlas-api-group-platform",
        },
        {
            "src": "atlas-api",
            "edge_type": "contains",
            "dst": "atlas-api-group-search",
        },
        {
            "src": "atlas-api",
            "edge_type": "contains",
            "dst": "atlas-api-group-work",
        },
        {
            "src": "atlas-api",
            "edge_type": "requires",
            "dst": "atlas-postgres",
        },
        {
            "src": "atlas-api-group-access",
            "edge_type": "contains",
            "dst": "atlas-api-route-access",
        },
        {
            "src": "atlas-api-group-access",
            "edge_type": "contains",
            "dst": "atlas-api-route-audit",
        },
        {
            "src": "atlas-api-group-access",
            "edge_type": "contains",
            "dst": "atlas-api-route-auth",
        },
        {
            "src": "atlas-api-group-access",
            "edge_type": "contains",
            "dst": "atlas-api-route-events",
        },
        {
            "src": "atlas-api-group-access",
            "edge_type": "contains",
            "dst": "atlas-api-route-me",
        },
        {
            "src": "atlas-api-group-graph",
            "edge_type": "contains",
            "dst": "atlas-api-route-edges",
        },
        {
            "src": "atlas-api-group-graph",
            "edge_type": "contains",
            "dst": "atlas-api-route-graph",
        },
        {
            "src": "atlas-api-group-graph",
            "edge_type": "contains",
            "dst": "atlas-api-route-intents",
        },
        {
            "src": "atlas-api-group-graph",
            "edge_type": "contains",
            "dst": "atlas-api-route-legacy",
        },
        {
            "src": "atlas-api-group-graph",
            "edge_type": "contains",
            "dst": "atlas-api-route-nodes",
        },
        {
            "src": "atlas-api-group-knowledge",
            "edge_type": "contains",
            "dst": "atlas-api-route-adrs",
        },
        {
            "src": "atlas-api-group-knowledge",
            "edge_type": "contains",
            "dst": "atlas-api-route-docs",
        },
        {
            "src": "atlas-api-group-knowledge",
            "edge_type": "contains",
            "dst": "atlas-api-route-handbook",
        },
        {
            "src": "atlas-api-group-platform",
            "edge_type": "contains",
            "dst": "atlas-api-route-assets",
        },
        {
            "src": "atlas-api-group-platform",
            "edge_type": "contains",
            "dst": "atlas-api-route-config",
        },
        {
            "src": "atlas-api-group-platform",
            "edge_type": "contains",
            "dst": "atlas-api-route-crunch",
        },
        {
            "src": "atlas-api-group-platform",
            "edge_type": "contains",
            "dst": "atlas-api-route-health",
        },
        {
            "src": "atlas-api-group-platform",
            "edge_type": "contains",
            "dst": "atlas-api-route-landing",
        },
        {
            "src": "atlas-api-group-platform",
            "edge_type": "contains",
            "dst": "atlas-api-route-setup",
        },
        {
            "src": "atlas-api-group-search",
            "edge_type": "contains",
            "dst": "atlas-api-route-context",
        },
        {
            "src": "atlas-api-group-search",
            "edge_type": "contains",
            "dst": "atlas-api-route-retrieval",
        },
        {
            "src": "atlas-api-group-search",
            "edge_type": "contains",
            "dst": "atlas-api-route-search",
        },
        {
            "src": "atlas-api-group-work",
            "edge_type": "contains",
            "dst": "atlas-api-route-comments",
        },
        {
            "src": "atlas-api-group-work",
            "edge_type": "contains",
            "dst": "atlas-api-route-stats",
        },
        {
            "src": "atlas-api-group-work",
            "edge_type": "contains",
            "dst": "atlas-api-route-workitems",
        },
        {
            "src": "atlas-backend",
            "edge_type": "contains",
            "dst": "atlas-api",
        },
        {
            "src": "atlas-backend",
            "edge_type": "contains",
            "dst": "atlas-backend-graph-work",
        },
        {
            "src": "atlas-backend",
            "edge_type": "contains",
            "dst": "atlas-backend-knowledge",
        },
        {
            "src": "atlas-backend",
            "edge_type": "contains",
            "dst": "atlas-postgres",
        },
        {
            "src": "atlas-backend-graph-work",
            "edge_type": "contains",
            "dst": "atlas-graph",
        },
        {
            "src": "atlas-backend-graph-work",
            "edge_type": "contains",
            "dst": "atlas-signals",
        },
        {
            "src": "atlas-backend-graph-work",
            "edge_type": "contains",
            "dst": "atlas-work-store",
        },
        {
            "src": "atlas-backend-graph-work",
            "edge_type": "contains",
            "dst": "atlas-world-model",
        },
        {
            "src": "atlas-backend-knowledge",
            "edge_type": "contains",
            "dst": "atlas-adr-registry",
        },
        {
            "src": "atlas-backend-knowledge",
            "edge_type": "contains",
            "dst": "atlas-context",
        },
        {
            "src": "atlas-backend-knowledge",
            "edge_type": "contains",
            "dst": "atlas-docs",
        },
        {
            "src": "atlas-board-foundations",
            "edge_type": "contains",
            "dst": "atlas-board-core-api",
        },
        {
            "src": "atlas-board-foundations",
            "edge_type": "contains",
            "dst": "atlas-board-core-graph-model",
        },
        {
            "src": "atlas-board-foundations",
            "edge_type": "contains",
            "dst": "atlas-board-core-prefs",
        },
        {
            "src": "atlas-board-foundations",
            "edge_type": "contains",
            "dst": "atlas-board-core-state",
        },
        {
            "src": "atlas-board-foundations",
            "edge_type": "contains",
            "dst": "atlas-board-core-ui",
        },
        {
            "src": "atlas-board-views",
            "edge_type": "contains",
            "dst": "atlas-panel",
        },
        {
            "src": "atlas-board-views",
            "edge_type": "contains",
            "dst": "atlas-view-access",
        },
        {
            "src": "atlas-board-views",
            "edge_type": "contains",
            "dst": "atlas-view-dashboard",
        },
        {
            "src": "atlas-board-views",
            "edge_type": "contains",
            "dst": "atlas-view-graph",
        },
        {
            "src": "atlas-board-views",
            "edge_type": "contains",
            "dst": "atlas-view-search",
        },
        {
            "src": "atlas-board-views",
            "edge_type": "contains",
            "dst": "atlas-view-settings",
        },
        {
            "src": "atlas-board-views",
            "edge_type": "contains",
            "dst": "atlas-view-work",
        },
        {
            "src": "atlas-ci-pipelines",
            "edge_type": "contains",
            "dst": "atlas-ci-images",
        },
        {
            "src": "atlas-ci-pipelines",
            "edge_type": "contains",
            "dst": "atlas-ci-scrub",
        },
        {
            "src": "atlas-ci-pipelines",
            "edge_type": "contains",
            "dst": "atlas-ci-security",
        },
        {
            "src": "atlas-ci-pipelines",
            "edge_type": "contains",
            "dst": "atlas-ci-smoke",
        },
        {
            "src": "atlas-ci-pipelines",
            "edge_type": "contains",
            "dst": "atlas-ci-verify",
        },
        {
            "src": "atlas-cli",
            "edge_type": "contains",
            "dst": "atlas-cli-agents",
        },
        {
            "src": "atlas-cli",
            "edge_type": "contains",
            "dst": "atlas-cli-audit",
        },
        {
            "src": "atlas-cli",
            "edge_type": "contains",
            "dst": "atlas-cli-backups",
        },
        {
            "src": "atlas-cli",
            "edge_type": "contains",
            "dst": "atlas-cli-cicd",
        },
        {
            "src": "atlas-cli",
            "edge_type": "contains",
            "dst": "atlas-cli-guardrails",
        },
        {
            "src": "atlas-cli",
            "edge_type": "contains",
            "dst": "atlas-cli-init-db",
        },
        {
            "src": "atlas-cli",
            "edge_type": "contains",
            "dst": "atlas-cli-rbac",
        },
        {
            "src": "atlas-cli",
            "edge_type": "contains",
            "dst": "atlas-cli-sessions",
        },
        {
            "src": "atlas-cli",
            "edge_type": "contains",
            "dst": "atlas-cli-tokens",
        },
        {
            "src": "atlas-cli",
            "edge_type": "contains",
            "dst": "atlas-cli-users",
        },
        {
            "src": "atlas-client-packs",
            "edge_type": "contains",
            "dst": "atlas-client-claude-code",
        },
        {
            "src": "atlas-context",
            "edge_type": "contains",
            "dst": "atlas-retrieval",
        },
        {
            "src": "atlas-context",
            "edge_type": "contains",
            "dst": "atlas-search",
        },
        {
            "src": "atlas-context",
            "edge_type": "requires",
            "dst": "atlas-graph",
        },
        {
            "src": "atlas-context",
            "edge_type": "requires",
            "dst": "atlas-visibility",
        },
        {
            "src": "atlas-delivery-scripts",
            "edge_type": "contains",
            "dst": "atlas-delivery-check",
        },
        {
            "src": "atlas-delivery-scripts",
            "edge_type": "contains",
            "dst": "atlas-delivery-deploy",
        },
        {
            "src": "atlas-delivery-scripts",
            "edge_type": "contains",
            "dst": "atlas-delivery-fork",
        },
        {
            "src": "atlas-delivery-scripts",
            "edge_type": "contains",
            "dst": "atlas-delivery-snapshot",
        },
        {
            "src": "atlas-delivery-scripts",
            "edge_type": "contains",
            "dst": "atlas-delivery-stage",
        },
        {
            "src": "atlas-frontend",
            "edge_type": "contains",
            "dst": "atlas-board-foundations",
        },
        {
            "src": "atlas-frontend",
            "edge_type": "contains",
            "dst": "atlas-board-views",
        },
        {
            "src": "atlas-frontend",
            "edge_type": "contains",
            "dst": "atlas-frontend-legacy",
        },
        {
            "src": "atlas-frontend",
            "edge_type": "requires",
            "dst": "atlas-api",
        },
        {
            "src": "atlas-frontend-legacy",
            "edge_type": "contains",
            "dst": "atlas-board-shell",
        },
        {
            "src": "atlas-frontend-legacy",
            "edge_type": "contains",
            "dst": "atlas-map",
        },
        {
            "src": "atlas-ingress",
            "edge_type": "contains",
            "dst": "atlas-gateway",
        },
        {
            "src": "atlas-ingress",
            "edge_type": "contains",
            "dst": "atlas-tls",
        },
        {
            "src": "atlas-mcp",
            "edge_type": "contains",
            "dst": "atlas-mcp-read",
        },
        {
            "src": "atlas-mcp",
            "edge_type": "contains",
            "dst": "atlas-mcp-session",
        },
        {
            "src": "atlas-mcp",
            "edge_type": "contains",
            "dst": "atlas-mcp-work",
        },
        {
            "src": "atlas-mcp",
            "edge_type": "contains",
            "dst": "atlas-mcp-write",
        },
        {
            "src": "atlas-mcp",
            "edge_type": "requires",
            "dst": "atlas-api",
        },
        {
            "src": "atlas-mcp-read",
            "edge_type": "contains",
            "dst": "atlas-mcp-read-graph",
        },
        {
            "src": "atlas-mcp-read",
            "edge_type": "contains",
            "dst": "atlas-mcp-read-knowledge",
        },
        {
            "src": "atlas-mcp-read",
            "edge_type": "contains",
            "dst": "atlas-mcp-read-search",
        },
        {
            "src": "atlas-mcp-read",
            "edge_type": "contains",
            "dst": "atlas-mcp-read-server",
        },
        {
            "src": "atlas-mcp-read-graph",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-get",
        },
        {
            "src": "atlas-mcp-read-graph",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-outline",
        },
        {
            "src": "atlas-mcp-read-graph",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-path",
        },
        {
            "src": "atlas-mcp-read-graph",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-structure",
        },
        {
            "src": "atlas-mcp-read-graph",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-tree",
        },
        {
            "src": "atlas-mcp-read-knowledge",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-adr-get",
        },
        {
            "src": "atlas-mcp-read-knowledge",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-adr-search",
        },
        {
            "src": "atlas-mcp-read-knowledge",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-doc-read",
        },
        {
            "src": "atlas-mcp-read-knowledge",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-intents",
        },
        {
            "src": "atlas-mcp-read-search",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-context",
        },
        {
            "src": "atlas-mcp-read-search",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-node-search",
        },
        {
            "src": "atlas-mcp-read-search",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-search",
        },
        {
            "src": "atlas-mcp-read-server",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-capabilities",
        },
        {
            "src": "atlas-mcp-read-server",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-health",
        },
        {
            "src": "atlas-mcp-session",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-save-work",
        },
        {
            "src": "atlas-mcp-session",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-setup-guide",
        },
        {
            "src": "atlas-mcp-work",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-comment-write",
        },
        {
            "src": "atlas-mcp-work",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-comments",
        },
        {
            "src": "atlas-mcp-work",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-file-work",
        },
        {
            "src": "atlas-mcp-work",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-update-work",
        },
        {
            "src": "atlas-mcp-write",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-delete",
        },
        {
            "src": "atlas-mcp-write",
            "edge_type": "contains",
            "dst": "atlas-mcp-write-graph",
        },
        {
            "src": "atlas-mcp-write",
            "edge_type": "contains",
            "dst": "atlas-mcp-write-knowledge",
        },
        {
            "src": "atlas-mcp-write-graph",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-edge-delete",
        },
        {
            "src": "atlas-mcp-write-graph",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-edge-write",
        },
        {
            "src": "atlas-mcp-write-graph",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-node-write",
        },
        {
            "src": "atlas-mcp-write-knowledge",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-doc-write",
        },
        {
            "src": "atlas-mcp-write-knowledge",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-intent-delete",
        },
        {
            "src": "atlas-mcp-write-knowledge",
            "edge_type": "contains",
            "dst": "atlas-mcp-tool-intent-write",
        },
        {
            "src": "atlas-operator-features",
            "edge_type": "contains",
            "dst": "atlas-op-config",
        },
        {
            "src": "atlas-operator-features",
            "edge_type": "contains",
            "dst": "atlas-op-crunch",
        },
        {
            "src": "atlas-operator-features",
            "edge_type": "contains",
            "dst": "atlas-op-install",
        },
        {
            "src": "atlas-operator-features",
            "edge_type": "contains",
            "dst": "atlas-op-migrate",
        },
        {
            "src": "atlas-operator-features",
            "edge_type": "contains",
            "dst": "atlas-op-setup",
        },
        {
            "src": "atlas-ops",
            "edge_type": "contains",
            "dst": "atlas-client-packs",
        },
        {
            "src": "atlas-ops",
            "edge_type": "contains",
            "dst": "atlas-operator-features",
        },
        {
            "src": "atlas-ops",
            "edge_type": "contains",
            "dst": "atlas-ops-delivery",
        },
        {
            "src": "atlas-ops",
            "edge_type": "contains",
            "dst": "atlas-ops-environments",
        },
        {
            "src": "atlas-ops-delivery",
            "edge_type": "contains",
            "dst": "atlas-ci-pipelines",
        },
        {
            "src": "atlas-ops-delivery",
            "edge_type": "contains",
            "dst": "atlas-tooling",
        },
        {
            "src": "atlas-ops-environments",
            "edge_type": "contains",
            "dst": "atlas-env-fork",
        },
        {
            "src": "atlas-ops-environments",
            "edge_type": "contains",
            "dst": "atlas-env-staging",
        },
        {
            "src": "atlas-panel",
            "edge_type": "contains",
            "dst": "panel-comments",
        },
        {
            "src": "atlas-panel",
            "edge_type": "contains",
            "dst": "panel-config",
        },
        {
            "src": "atlas-panel",
            "edge_type": "contains",
            "dst": "panel-decisions",
        },
        {
            "src": "atlas-panel",
            "edge_type": "contains",
            "dst": "panel-docs",
        },
        {
            "src": "atlas-panel",
            "edge_type": "contains",
            "dst": "panel-history",
        },
        {
            "src": "atlas-panel",
            "edge_type": "contains",
            "dst": "panel-intent",
        },
        {
            "src": "atlas-panel",
            "edge_type": "contains",
            "dst": "panel-links",
        },
        {
            "src": "atlas-panel",
            "edge_type": "contains",
            "dst": "panel-overview",
        },
        {
            "src": "atlas-panel",
            "edge_type": "contains",
            "dst": "panel-work",
        },
        {
            "src": "atlas-postgres",
            "edge_type": "contains",
            "dst": "atlas-backup",
        },
        {
            "src": "atlas-postgres",
            "edge_type": "contains",
            "dst": "atlas-postgres-config",
        },
        {
            "src": "atlas-postgres",
            "edge_type": "contains",
            "dst": "atlas-postgres-migrations",
        },
        {
            "src": "atlas-postgres",
            "edge_type": "contains",
            "dst": "atlas-postgres-schema",
        },
        {
            "src": "atlas-postgres",
            "edge_type": "contains",
            "dst": "atlas-postgres-vector",
        },
        {
            "src": "atlas-postgres-schema",
            "edge_type": "contains",
            "dst": "atlas-postgres-tables-accounts",
        },
        {
            "src": "atlas-postgres-schema",
            "edge_type": "contains",
            "dst": "atlas-postgres-tables-audit",
        },
        {
            "src": "atlas-postgres-schema",
            "edge_type": "contains",
            "dst": "atlas-postgres-tables-graph",
        },
        {
            "src": "atlas-postgres-schema",
            "edge_type": "contains",
            "dst": "atlas-postgres-tables-settings",
        },
        {
            "src": "atlas-retrieval",
            "edge_type": "requires",
            "dst": "atlas-postgres",
        },
        {
            "src": "atlas-security",
            "edge_type": "contains",
            "dst": "atlas-audit-trail",
        },
        {
            "src": "atlas-security",
            "edge_type": "contains",
            "dst": "atlas-guardrails",
        },
        {
            "src": "atlas-security",
            "edge_type": "contains",
            "dst": "atlas-identity",
        },
        {
            "src": "atlas-security",
            "edge_type": "contains",
            "dst": "atlas-visibility",
        },
        {
            "src": "atlas-tooling",
            "edge_type": "contains",
            "dst": "atlas-cli",
        },
        {
            "src": "atlas-tooling",
            "edge_type": "contains",
            "dst": "atlas-delivery-scripts",
        },
        {
            "src": "atlas-view-graph",
            "edge_type": "contains",
            "dst": "atlas-view-graph-glyphs",
        },
        {
            "src": "atlas-view-graph",
            "edge_type": "contains",
            "dst": "atlas-view-graph-labels",
        },
        {
            "src": "atlas-view-graph",
            "edge_type": "contains",
            "dst": "atlas-view-graph-layout",
        },
        {
            "src": "atlas-view-settings",
            "edge_type": "contains",
            "dst": "atlas-view-settings-crunch",
        },
        {
            "src": "atlas-view-settings",
            "edge_type": "contains",
            "dst": "atlas-view-settings-password",
        },
    ],
    "intents": [
        {
            "node_id": "atlas",
            "slug": "work-rolls-up",
            "kind": "principle",
            "title": "Work rolls up, never down",
            "statement": "A workitem bound to a node appears there and on every node containing it, and on nothing inside it. The invariant this buys: if a node's board shows a containment-derived item, that node's numbers count it. A fourth `above` bucket pushing an ancestor's work down onto descendants was proposed and rejected.",
        },
        {
            "node_id": "atlas",
            "slug": "oss-work-graph",
            "kind": "outcome",
            "title": "A self-hostable, open-source work and context layer for humans and their agents",
            "statement": "Atlas is self-hostable and open source. The core is the work: the work hierarchy (Epic > Story > Task), the board, a REST API and an MCP server, so an agent reads and writes the same work a person sees, within limits a person sets. Everything else is an add-on an install turns on when it needs it: semantic retrieval, single sign-on, guardrails, CI integration. The source is open and Atlas documents itself, so a team can change Atlas with its own agents. The product ships without anyone's world in it: an instance is configuration and data, never code.",
        },
        {
            "node_id": "atlas",
            "slug": "agents-propose-humans-commit",
            "kind": "principle",
            "title": "Agents propose; humans commit",
            "statement": "An agent may read anything Atlas holds and propose any change to it. Committing a change that alters what other work is measured against — an intent, a binding, a scope decision, acceptance criteria — stays a human act. A human commits by doing it, by approving an agent's proposal (for acceptance criteria: one click on the board), or, where an install opts in (`ATLAS_ACCEPTANCE_AGENT_WRITE=attested`), by an approval the agent must cite by name and quote, which Atlas records with the change. Read-only is the default for any credential an agent carries.",
        },
        {
            "node_id": "atlas",
            "slug": "context-minimal",
            "kind": "outcome",
            "title": "Give an agent the fewest tokens it needs to act correctly",
            "statement": "Atlas is becoming an enterprise context layer, and token consumption is the hurdle. Give an agent the fewest tokens that let it act correctly: curated knowledge kept current, pointers rather than payloads, budgets as parameters, and one task-scoped read instead of exploring. Measure bytes before and after any read-path change. The reasoning, and what makes other assistants expensive and wrong, is in ADR 0040.",
        },
        {
            "node_id": "atlas-backend",
            "slug": "no-write-back-to-git",
            "kind": "non-goal",
            "title": "Atlas does not write data back into git",
            "statement": "The git export was built, verified and removed the same day. It was not a backup — it covered no auth, override, probe or derived state — and not an audit trail, because snapshot diffs cannot say who or when. Auditing's real answer is the `graph_event` table beside `auth_event`, which shipped on 2026-08-24 with one enforced writer, `deps.record_graph_event`.",
        },
        {
            "node_id": "atlas-backend",
            "slug": "postgres-is-authoritative",
            "kind": "constraint",
            "title": "Nothing Atlas owns lives outside the Atlas database",
            "statement": "The Atlas database (PostgreSQL, hosted wherever the install chooses: the bundled container or a managed service) is the source of truth for everything Atlas owns. Node documentation, the backlog, its acceptance criteria and its recorded results are all rows. The three importers were deleted because a second run did not top the database up — it reverted the database to the files. Nothing in this repo can now overwrite the database from a file, and nothing added should be able to. An add-on may keep a derived copy elsewhere (a vector index, a hosted model's index) only if it can be rebuilt from the database and Atlas works without it.",
        },
        {
            "node_id": "atlas-context",
            "slug": "pointers-within-budgets",
            "kind": "constraint",
            "title": "The context answer is pointers within budgets",
            "statement": "The context answer is pointers within budgets: every part is bounded by a parameter and says when it was cut; the full text is one atlas_get or atlas_doc_read away.",
        },
        {
            "node_id": "atlas-frontend",
            "slug": "no-module-encodes-the-graph",
            "kind": "principle",
            "title": "No module may encode the graph",
            "statement": "Read the graph from `/api/*`. A type vocabulary is defensible presentation config; a node id is not. If a module needs to name a node in order to work, that is a missing endpoint, not a constant.",
        },
        {
            "node_id": "atlas-frontend",
            "slug": "layout-is-judged-not-measured",
            "kind": "constraint",
            "title": "Layout is judged by a human, not measured by an agent",
            "statement": "An automated browser tab runs backgrounded, so `requestAnimationFrame` is throttled, so D3 transitions never advance and every node reports `translate(0,0)`. Node counts, the element-id inventory, computed styles, panel widths and real API writes are reliable. Layout, zoom, pan, collapse and map anchoring are not — stage those and ask.",
        },
        {
            "node_id": "atlas-frontend",
            "slug": "render-names-verbatim",
            "kind": "constraint",
            "title": "`node.name` renders verbatim",
            "statement": "Names carry two registers: prose naming a concept for a human, and the identifier a thing answers to elsewhere — one you can paste into `docker exec`. An uppercase-first transform corrupts every identifier and makes the map disagree with the panel, the API and search. Violations are reported, never mangled.",
        },
        {
            "node_id": "atlas-identity",
            "slug": "token-belongs-to-the-human",
            "kind": "principle",
            "title": "A token belongs to a person, who stays accountable",
            "statement": "A service token belongs to an agent's OWN account, and that account records the person accountable for it. Both halves matter, and collapsing them into one sentence is what put a false premise in EPIC-atlas-contributor-onboarding for six days: \"belongs to the human\" is a claim about ACCOUNTABILITY, never about identity. `admin bot add` creates a distinct `auth_user` per agent and refuses a person outright; `auth.py` refuses any Bearer token whose owning account is not `is_service`. Since ADR 0034, `auth_user.person_user_id` records who is answerable, grants inherit from person to bot, ROLE never does, and disabling a person disables their agents. An owner holds no grants, so an owner's agent inherits an EMPTY set — linking makes it less capable, not more. Two independent bounds apply to every machine caller: scopes bound what it may DO (enforced since 2026-08-15 for every non-session principal, an unmapped route failing closed), and grants bound what it may SEE. Neither removes the accountability of the person who minted it.",
        },
        {
            "node_id": "atlas-mcp",
            "slug": "facade-not-second-brain",
            "kind": "constraint",
            "title": "The MCP holds no business logic and no copy of the graph",
            "statement": "Every verb atlas-mcp exposes is a call to atlas-api. No validation rule, no rollup, no projection and no cached copy of the graph lives in this service. If a tool needs something the API cannot answer, that is a missing endpoint and it gets built in atlas-api -- not worked around here. A facade that starts deciding things becomes a second implementation of the graph, and the two will disagree.",
        },
        {
            "node_id": "atlas-retrieval",
            "slug": "atlas-complete-without-it",
            "kind": "constraint",
            "title": "Atlas is complete without it",
            "statement": "Semantic retrieval is an add-on, never a requirement. With it off, or on but broken (no pgvector, no endpoint, the embedder down), Atlas boots, serves and searches exactly as it does without it; nothing in the core imports, calls or requires it, and its state is reported as information, never as red. Default off; an instance enables it by configuration only.",
        },
        {
            "node_id": "atlas-signals",
            "slug": "probes-and-health-out-of-scope",
            "kind": "non-goal",
            "title": "Probes and health are out of scope",
            "statement": "\"Right now we should focus on presenting information so people can start working and understanding the projects.\" The `probe` and `probe_result` tables and the static `attributes.health` hints stay exactly as they are: do not build on them, and do not file bugs against them.",
        },
        {
            "node_id": "atlas-work-store",
            "slug": "keep-the-id-scheme",
            "kind": "non-goal",
            "title": "A unified, type-free workitem id scheme",
            "statement": "Slug ids for epics and stories (`EPIC-project-atlas`, `STORY-atlas-object-graph`), numbers for tasks (`TASK-850`). Flattening them, into `EPIC-XXXX` numbers or a type-free `ID-XXXXX`, was considered and rejected. No code parses the prefix (type comes from the `type` column), so unifying simplifies nothing and costs legibility in every edge dump, log line and commit. The split matches lifespan: tasks are numerous and disposable, stories and epics are referred to in prose for months. Reopen only if reclassifying a task as a story, or the reverse, becomes common.",
        },
        {
            "node_id": "atlas-world-model",
            "slug": "contains-is-part-of",
            "kind": "principle",
            "title": "`contains` means \"is part of\" — nothing else",
            "statement": "It is the product decomposition, with one stated exception: the environments an install operates beside live (staging, a fork, CI runners) sit under `atlas-ops` so they can be found. Their running instances are excluded from export; the grouping nodes ship only as a generic example of how to set such environments up. Not ownership, not a permission grammar, and not where something runs — that is `hosted_on`'s job. Anything else that is not a part of the product does not get a `contains` edge, however convenient the nesting looks.",
        },
        {
            "node_id": "atlas-world-model",
            "slug": "node-holds-open-work",
            "kind": "constraint",
            "title": "Count OPEN workitems, and never let a count trigger a split",
            "statement": (
                "Status not `done` or `cancelled` -- the same lens the board's age-out filter uses. **Never count lifetime**: a lifetime count only rises, so a threshold on it fires in every node eventually just from work being finished, splitting the tree forever while findability never actually got worse.\n"
                "\n"
                "**The 5-25 ceiling this constraint used to state was RETIRED on 2026-08-15** (ADR 0030 amended). It fired on `atlas-mcp` the moment an epic was filed against it, which is the failure mode rather than the rule working: a node acquires work because work exists, not because it became hard to read. A number cannot tell you whether work is findable.\n"
                "\n"
                "So: split a node when finding the work in it actually gets hard, and say what got hard. A count is a description, never a trigger."
            ),
        },
        {
            "node_id": "atlas-world-model",
            "slug": "ask-before-binding",
            "kind": "constraint",
            "title": "Ask before assigning a binding",
            "statement": "The pause on being asked to map things by hand ended with ADR 0030; the ask-first rule did not. A binding decides where work shows up for everyone who reads the board, so it is not an agent's call to make on someone else's behalf.",
        },
    ],
}
