---
audience: user
---

# Agents

This guide takes you from an Atlas account to an AI agent that reads and writes Atlas as you, and
then explains how that agent is expected to work with the graph. It is written for Claude Code,
which has a ready-made client pack, but any MCP client that can send an `Authorization: Bearer`
header works the same way.

Related pages:

- [MCP tools](../MCP-TOOLS.md) lists every tool, its parameters and its description. *Generated.*
- [Install, section 7](../INSTALL.md#7-connect-claude-code-clientsclaude-code) is the same connection
  seen from the operator's side, right after an install.
- [Identity and access](identity-and-access.md#what-an-agent-can-do-as-its-user) says what an
  agent may see and do as its person.
- [`clients/claude-code/README.md`](../../../clients/claude-code/README.md) is the client pack's own
  guide, with every Windows variant.

## How an agent connects

```
agent (Claude Code) --https, Bearer token--> atlas-mcp /mcp/ --same token--> atlas-api
```

- **An agent is its own account, linked to you.** It authenticates with its own token
  (`atlas_svc_...`), never with your password or your browser session. Everything it writes is
  recorded against the agent, and so against you.
- **atlas-mcp holds no rules.** It checks each caller's token with atlas-api at the door and then
  makes every API call with that same token. atlas-api decides, on every call, what the token may
  read and write.
- **The agent sees at most what you see.** Its scopes (`read`, `write`, `admin`) cap it, your role
  caps the scopes, and with project RBAC on it gets the lower of its scope and your project role.
  The details are in [Identity and access](identity-and-access.md#what-an-agent-can-do-as-its-user).

## From token to working session

You need Claude Code, the install's public URL (below `https://atlas.example.com`), an Atlas
account, and, if the install uses its own certificate authority, the install's CA certificate
(`ca.crt` only, never `ca.key`).

### 1. Get a token

There are three ways. Pick the first one that applies to you.

**You sign in through SSO.** On an install with single sign-on, signing in through your IdP is the
default way to connect an agent, and there are two forms of it:

- **IdP sign-in in the client (the default).** Where the install has turned on IdP access tokens
  (`ATLAS_OAUTH_RESOURCE`), Claude Code signs in through your IdP and needs no minted token at
  all. Skip to [Using your IdP sign-in](../../../clients/claude-code/README.md#using-your-idp-sign-in-instead-of-a-minted-token)
  in the client pack; *IdP access tokens* in
  [Identity and access](identity-and-access.md#what-an-agent-can-do-as-its-user) explains what the
  agent may then do.
- **A minted token, confirmed at your IdP.** For a client that cannot sign in itself, mint one in
  **Settings**, **Agent credentials**, as below, with one difference: there is no password to
  re-enter. Type the purpose first; the screen shows the name you are approving (*You are creating
  an agent credential named `<name>` for `<you>`*). Press **Confirm with SSO**, check that name on
  the page that follows, sign in at your IdP again (it asks even if you are signed in there), and
  come back to the screen. Create **that** credential within the time it shows (5 minutes by
  default); a different name is refused (`step_up_agent_mismatch`), and after one credential you
  confirm again. Your agent is named after your email (`<name before the @>-<purpose>`), the same
  name an operator's `token mint` gives. This needs the
  operator to have set `ATLAS_OIDC_STEP_UP=on`
  ([Identity and access](identity-and-access.md#agent-credentials-for-sso-people)). Without it the
  board refuses an SSO account with `password_incorrect`, and an operator mints the token for you
  (below; `--user` takes `<idp>:<subject>`, or your email once you have signed in with SSO).

**You sign in to the board with a password.** Mint your own: profile menu, **Settings**, **Agent
credentials**, *Create an agent credential*.

- Name it for its purpose (`claude-code`), not for yourself. Your username is prefixed
  automatically, so the agent becomes `<you>-claude-code`. Agent names are permanent.
- Choose scopes: `read` is the default; filing and updating work needs `write`. Take the least you
  need. You can issue a replacement with more later.
- Choose an expiry. Every token expires.
- Re-enter your password. A signed-in browser alone cannot mint a long-lived key.
- The token is shown **once**. Save it straight into the file in step 2, then confirm.

**An operator mints it.** On the machine that runs the stack, in `deploy/oss/`. The token is
written only to the `--out` file inside the container and never printed:

```sh
docker compose exec -T atlas-api atlas-admin \
  token mint --user <you> --purpose claude-code --scopes read,write --out /tmp/claude-code.token
```

`--scopes` defaults to `read` and is capped by your role; `--days` sets the expiry (at most 365).
The agent is named `<you>-claude-code` and linked to you. Moving the file to your machine without
printing it is step 1 of the [client pack README](../../../clients/claude-code/README.md#three-steps).

### 2. Store it in a file only you can read

The token lives in exactly one place: `~/.config/atlas/token`, one line, readable by you alone.

```sh
mkdir -p ~/.config/atlas && chmod 700 ~/.config/atlas
# put the token in ~/.config/atlas/token (paste it in an editor, or move the operator's file)
chmod 600 ~/.config/atlas/token
cp clients/claude-code/helpers/atlas-mcp-headers.sh ~/.config/atlas/
```

On Windows, `chmod` does nothing real on NTFS. Use `icacls` and the PowerShell helper, as the
[client pack README](../../../clients/claude-code/README.md#1-get-a-token-into-configatlastoken)
shows.

Never put the token where it gets copied:

| Do not | Because |
|---|---|
| type it at a shell prompt or `export` it | it lands in your shell history |
| pass it as a command-line argument | every user on the machine can see it in the process list |
| write it into `.mcp.json` or `claude mcp add --header` | it is stored in plain text in a config file, often inside a repository |
| paste it into a chat with the agent | the conversation is stored, and the model never needs it |
| put it in a URL | query strings reach server logs |

Claude Code reads the token through a **headers helper**: a small script it runs on every
connection, which reads the file and prints `{"Authorization":"Bearer ..."}` to Claude Code only.
The helper refuses a token file that others can read. Check it without printing the token:

```sh
sh ~/.config/atlas/atlas-mcp-headers.sh | sed -E 's/(Bearer )[^"]+/\1<redacted>/'
# {"Authorization":"Bearer <redacted>"}
```

### 3. Point the template at your install

The pack has `clients/claude-code/mcp.template.json` (Linux, macOS) and
`mcp.template.windows.json`. Write your copy next to the token, outside any checkout, replace the
example URL with yours and keep the `/mcp/` path with its trailing slash:

```sh
sed 's#https://atlas.example.com#https://atlas.yourcompany.example#' \
  clients/claude-code/mcp.template.json > ~/.config/atlas/atlas.mcp.json
```

```json
{
  "mcpServers": {
    "atlas": {
      "type": "http",
      "url": "https://atlas.yourcompany.example/mcp/",
      "headersHelper": "sh \"$HOME/.config/atlas/atlas-mcp-headers.sh\""
    }
  }
}
```

### 4. Trust the install's CA

The default install makes its own certificate authority (`deploy/oss/secrets/tls/ca.crt`). Claude
Code must trust it, or the connection fails with a certificate error. A publicly trusted
certificate (for example from ACME) needs nothing here.

Copy `ca.crt` to your machine, for example `~/.config/atlas/ca.crt`, and set `NODE_EXTRA_CA_CERTS`
in the `env` block of `~/.claude/settings.json`, which also reaches background sessions:

```json
{ "env": { "NODE_EXTRA_CA_CERTS": "/home/you/.config/atlas/ca.crt" } }
```

Then **restart Claude Code**: Node reads the variable only when the process starts, so `/mcp`
reconnect in a running session is not enough. Exporting it in the shell you start `claude` from
also works, for that shell only. This adds the CA for Claude Code only. Adding
it to the operating system's trust store also works, but then every program on the machine trusts
that CA; the [client pack README](../../../clients/claude-code/README.md#trusting-the-installs-ca)
has the commands.

**Never turn certificate checking off** (`NODE_TLS_REJECT_UNAUTHORIZED=0`). The token would go to
whoever answers.

### 5. Add the server and check it

For every project, at user scope:

```sh
claude mcp add-json atlas "$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["mcpServers"]["atlas"]))' ~/.config/atlas/atlas.mcp.json)" --scope user
claude mcp list
# atlas: https://atlas.yourcompany.example/mcp/ (HTTP) - ✔ Connected
```

Or for one project: copy `~/.config/atlas/atlas.mcp.json` in as that project's `.mcp.json` (Claude
Code asks you to trust the folder before it runs the helper). The Windows command is in the client
pack README.

Check that `claude mcp get atlas` (or `/mcp` in a session) shows **your install's URL**. A
project's `.mcp.json` that defines a server with the same name takes precedence over the
user-scope one, with no warning; start `claude` outside such a project, or add yours under another
name.

Then, in a session, `/mcp` shows the server and its tools. Ask the agent to call `atlas_health`;
an answer with node and edge counts means the session works end to end.

| You see | It means |
|---|---|
| `Failed to connect`, certificate error | step 4 is missing, points at the wrong file, or Claude Code was not restarted after it |
| 401 | the token file is wrong, revoked or expired; run the helper by hand as in step 2 |
| 403 naming a scope | the token lacks it, for example `write`; issue a replacement with it |
| 503 | atlas-mcp could not reach atlas-api; it fails closed. Try again shortly |
| 404 on something you can see on the board | a missing grant, not a bug (see [Guardrails](#guardrails-and-what-is-refused)) |

### 6. Install the plugin (recommended)

The pack's `atlas` plugin adds three skills that carry the working model below into every session:

- **`atlas-retrieval`**: the reading order, and which calls return tens of kilobytes.
- **`file-task`**: filing a workitem with `atlas_file_work`, with acceptance the person approves.
- **`setup-guide`**: running the first-run setup interview with an install's owner. It needs an
  owner's token with `admin`.

```sh
claude plugin marketplace add ./clients/claude-code
claude plugin install atlas@atlas-client
```

### Other MCP clients

Any client that speaks MCP over streamable HTTP works: the endpoint is `https://<host>/mcp/` and
each request carries `Authorization: Bearer <token>`. Prefer a client that reads the header from a
file or a command at connect time. If the client is a Node program, `NODE_EXTRA_CA_CERTS` is the CA
setting; otherwise use the client's own CA option, or the system trust store.

## Behind a gateway

An install can put [AgentGateway](https://agentgateway.dev) in front of atlas-mcp and other MCP
servers (the `gateway` profile, set up in
[Install, section 6](../INSTALL.md#6-optional-the-mcp-gateway---profile-gateway)). For the agent,
only the URL changes:

```json
"url": "https://atlas.example.com:8444/mcp"
```

(port `ATLAS_GATEWAY_PORT`). Everything else stays the same:

- **Same token, same CA.** The gateway forwards your own `Authorization` header to atlas-mcp
  (`forward_authorization: true` in `deploy/gateway/gateway.yaml`), so atlas-api still decides
  every call as you. A gateway that strips the header gets a 401 for every caller.
- **Same tool names.** The gateway does not prefix them: through it the tools are still
  `atlas_get`, `atlas_file_work` and so on.
- **The gateway may show fewer tools.** Each server has a tool allow-list in the gateway
  configuration. A tool not on it is absent from the tool list and refused when called.

If you run your own gateway instead, it must forward the client's `Authorization` header to
atlas-mcp unchanged and send a Host name that atlas-mcp accepts (`ATLAS_MCP_ALLOWED_HOSTS`;
anything else gets 421).

## How an agent should work with the graph

Atlas holds the context: **world nodes** say what each part of your estate is, **workitems** (epic,
story, task) say what is being done about it, **intents** are the standing constraints, and
**documents** on nodes hold procedures. The tools return far more than needed if you let them, so
the working model is mostly about asking the narrowest question first.

### Orient first

1. **An open question: `atlas_context(about=...)`.** One bounded call, in your own words: the
   best-matching nodes, their neighbours, what depends on them, the intents that apply, related
   decisions and the documents to read next. It returns pointers, and each part says when its
   budget cut it.
2. **What exists: `atlas_outline`.** One line per node. It defaults to the world; scope it with
   `root=`, add `names=true`.
3. **Which node knows about X: `atlas_node_search(q=...)`.** Pointers, not prose. For the backlog
   use `atlas_search` with filters (`type`, `status`, `parent`, `binding`, `q`) and a small
   `page_size`, and page until you have `total`.
4. **Where it sits: `atlas_structure(id)`.** Ancestors, children, dependencies and binding, without
   prose. It is cheaper than one full read.
5. **Detail: `atlas_get(id)`** on the one or two nodes that matter. When only orienting, pass
   `prose=summary` and `intents=own`.
6. **Procedure: `atlas_doc_read(node_id, path)`** when you are about to follow what the document
   says. `path` must be one the node lists in `attributes.docs[]`.

`atlas_tree` is for a neighbourhood around one known node, not for discovery; raise its knobs one
at a time. `atlas_capabilities` is about 25 KB; the tool list already arrives with the connection.

### Read before you write

- **Read the node full before you change it.** `atlas_get` with its defaults returns the node's own
  and inherited intents, and those are the constraints on the change.
- **Prose fields are replaced whole.** `abstract`, `detail` and a workitem's `body` are overwritten
  by a write, so read what is there first.
- **Check status claims against the data.** Prose can drift; a workitem's `summary` can be stale.
  Read `status`, and check the item or the code before acting on "this is not built yet".
- **Read back what you wrote.** Write tools echo the stored row. A write that sent a field the API
  ignores still succeeds, with a `warnings` list naming the field: that is a change that did not
  happen.

### File work as epic > story > task

- **Work is rows, not files.** File it with `atlas_file_work`: the item, its parent edge, binding
  and dependencies in one transaction. A markdown task file is invisible to Atlas.
- **The hierarchy is epic > story > task.** A task hangs off a story, a story off an epic. Levels
  may be skipped, never repeated or inverted. Only `atlas_file_work` and `atlas_update_work(parent=)`
  make or move these edges.
- **Find the parent cheaply** (`atlas_search(type="story", q=...)`, then `atlas_structure`). If
  several fit, ask the person. If none fits, say so.
- **Never invent an id.** A task's id is minted by the server; read it off the response. An epic or
  story takes a slug id (`EPIC-search-rework`) that you propose to the person. `parent`, `binding`
  and `depends_on` must be ids an Atlas tool returned in this session.
- **`body` is the spec, not the outcome.** Record outcomes later with
  `atlas_update_work(result=...)`.
- **Suggest a binding, do not guess one.** Which world node the work is about is the person's call.
- **Mind null versus omitted.** In `atlas_update_work`, some fields are cleared by `null` and left
  alone when omitted, and `body`, `acceptance`, `depends_on` and `binding` replace wholesale behind
  a `set_<field>=True` flag. [MCP tools](../MCP-TOOLS.md) explains how the tables mark this; leave
  out a parameter you do not mean to change.

### Intents are constraints

An intent is a standing statement on a node that is still true when all open work is done:
a `constraint` (a bug when violated), a `principle` (a defect), a `non-goal` (a removal) or an
`outcome` (direction). It applies to the node and everything below it.

- Treat every intent `atlas_get` returns, inherited ones included, as a constraint on your change.
- Correcting a fact inside an intent is fine. Changing what it requires is the owning person's
  decision: propose it, do not write it.
- Something that stops being true once the work is done is not an intent; file it as a workitem.

### Acceptance needs a person

Acceptance criteria are what a workitem is judged against, so the agent doing the work does not
decide them.

- **When filing:** draft 2-4 checkable criteria and show them to the person before you call
  `atlas_file_work`.
- **On an existing item:** `atlas_update_work(id=..., acceptance=[...], propose_acceptance=True,
  proposal_note="why")`. This stores a proposal that the board shows as a diff; only a person,
  signed in, can approve or reject it.
- **A plain `set_acceptance=True` is refused** for every token (`acceptance_is_not_yours_to_write`).
  An install that sets `ATLAS_ACCEPTANCE_AGENT_WRITE=attested` also accepts a write carrying
  `approval={by, quote}`: a real person's username and their own words approving this exact
  change, given in the conversation. Never author that quote yourself.
- The same rule covers decisions: an ADR recorded through `atlas_setup_guide` stays Proposed until
  the owner accepts it on the board.

### Writing prose others will read

Board content is visible to everyone with access to the project. Keep credentials, private hostnames
and anything confidential out of node prose, documents and workitems. Write what a thing *is* on the
node the work binds to; leave counts and other items' status to the data.

## Guardrails and what is refused

The API enforces these; the agent cannot talk its way past them.

| Guardrail | What the agent sees |
|---|---|
| A hidden node and a missing node look the same | 404 for both, everywhere. A node you cannot see is absent from every search, total and path. If the agent says a project does not exist and you can see it, check the agent's grants |
| Scopes gate writes | 403 naming the missing scope; a `read` token cannot write, and deletes and access administration need `admin` |
| No token exercises `owner` | granting roles and other owner acts are always done by a person |
| A token cannot mint tokens | creating an agent credential needs a signed-in person and their password, or for an SSO person a fresh IdP sign-in (`session_required`, `password_incorrect`, `step_up_required`) |
| Task ids are minted | `id_is_minted` on a task with an `id`; `id_required` on an epic or story without one |
| Outcomes are not spec | `result_in_body` when `body` has a `## Result` or `## Outcome` section |
| Hierarchy edges only through work tools | `atlas_node_write` and `atlas_edge_write` cannot make an epic > story > task edge; illegal moves are refused before anything is written (`parent_order`, `parent_cycle`, ...) |
| Acceptance needs a person | `acceptance_is_not_yours_to_write` |
| Decisions need a person | `decision_needs_a_person` when a token tries to accept a setup-guide ADR |
| Deletes are off by default | `atlas_delete` is not in the tool list unless the operator sets `ATLAS_MCP_ENABLE_DELETE`, and then needs `admin`. It is permanent; a node delete demands `expected_edge_count` |
| Strict installs refuse unknown fields | with `ATLAS_STRICT_API=1`, a 422 naming the field instead of a `warnings` entry |
| Browser origins | atlas-mcp answers 421 to an unknown Host and 403 to an unknown `Origin`, against DNS rebinding |

`atlas_save_work` (the end-of-session sign-off: your writes since a time, plus the procedure for
recording the session's work) is offered unless the operator lists `save_work` in
`ATLAS_DISABLED_FEATURES`.

## Rotating and revoking

- **Rotate without downtime:** issue a replacement (board: *Issue a replacement credential*;
  operator: `token mint` with the same `--purpose`), replace the file, reconnect (`/mcp`, then
  reconnect), check `claude mcp list`, and only then revoke the old token.
- **Revoke:** on the board under Agent credentials, or `atlas-admin token list --user <you>` and
  `atlas-admin token revoke <id>`. atlas-api checks the token on every call, so a revoked token
  stops reading and writing at once. atlas-mcp caches a positive check at its door for up to 60
  seconds (`ATLAS_MCP_AUTH_CACHE_S`), so a revoked token may still open a session in that window,
  but every tool call it makes fails.
- Revoking a token does not delete the agent. Disabling a person disables every agent linked to
  them, and an SSO person's agent tokens are suspended once that person goes too long without an
  SSO login (see [Identity and access](identity-and-access.md#what-an-agent-can-do-as-its-user)).

## Every tool

[MCP tools](../MCP-TOOLS.md) lists all 25 tools by group (read, work, graph writes, the setup
guide, session), with the description the server sends and each parameter's type and default.
Many descriptions end in *More: tools guide.*: the longer guide for every tool is a document the
agent reads with `atlas_doc_read`, as the `atlas_doc_read` entry there says.
