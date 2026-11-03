---
audience: user
---

# Concepts

This page covers the ideas Atlas is built on and the words the rest of the documentation uses
for them. It is about the model, not the machinery. For the services and how requests travel
through them, see [Architecture](../ARCHITECTURE.md).

## One graph

Atlas keeps one **graph** in Postgres: **nodes** joined by typed, directed **edges**. Everything
else in Atlas reads and writes that graph through the same API: the board, the documentation
site, the API and every MCP tool an agent calls. Nothing is imported from files and nothing is
synced in. The database is the source of truth.

### Nodes

A node has a stable `id` (for example `billing-api` or `TASK-1042`), a `type`, a `name` and an
`attributes` object. Its text comes in three lengths, from short to long:

- `summary`: one line that says what it is;
- `abstract`: a paragraph;
- `detail`: everything else you need to work on it.

Read the shortest text that answers your question. Agents are expected to do the same.

Every node type belongs to one of three **families**:

| Family | Types | What it describes |
|---|---|---|
| object (the *world*) | `org`, `project`, `system`, `service`, `component`, `capability`, `agent`, `db`, `host` | things that exist and outlive any piece of work |
| workitem (the *work*) | `epic`, `story`, `task` | things being done, with a status |
| bridge | `skill`, `doc`, `vision` | material attached to the world: practices, documents and design intent |

The types and their families are in a registry table, `node_type`, which is filled in every time
Atlas starts.

### Edges

An edge goes from one node (`src`) to another (`dst`) and has a type. The types in use:

| Edge | Means | Between |
|---|---|---|
| `contains` | *is part of*. This is the decomposition, not ownership or permissions. | object → object, object → bridge, and workitem → workitem (epic → story → task) |
| `works_on` | this work is about that world object. **At most one per workitem.** | workitem → object |
| `depends_on` | this work waits on that work | workitem → workitem |
| `uses` | one object stands on another without containing it | object → object |
| `hosted_on` | where something **runs**, as opposed to what it is part of | object → object |
| `instance_of` | a deployment points at what it deploys | object → object |
| `contributes_to` | a party contributes to a project; many-to-many | object → object |

Edge types also have a registry, `edge_type`, which you can read at `GET /api/vocabulary`. Each
entry lists the family pairs that type connects, and has a status of `active` or `dead`.
`/api/health` reports drift: a type in use that the registry does not declare, a pair the
registry does not list, or a `dead` type that has gained rows. One retired type, `serves`, is
declared `dead` on purpose. It meant the same as `works_on`, and a workitem bound by both edges
would be counted twice in every roll-up.

A node can have more than one `contains` parent. Atlas allows that, and every roll-up below is
written so that a shared child is counted only once.

**Roots** are not configured. A root is any node with no incoming `contains` edge, so the list of
roots is computed from the graph, not set anywhere.

## The world and the work

Atlas keeps two kinds of thing in the same graph, and the difference between them matters.

**World nodes** describe what exists: an organisation, its projects, the systems in them, the
services, databases and hosts they run on, and the capabilities that span them. They are arranged
with `contains`. A world node has no lifecycle status, though it can carry a `health` hint (for
example `attributes.health: green`). To decide whether something is a world node or a piece of
work, use this test: **a world node outlives the work.** If it is finished when the epic is
finished, it is an epic.

**Workitems** describe what is being done. They form their own hierarchy with `contains`:

```
epic            a body of work with one outcome
 └─ story       a deliverable slice of it
     └─ task    one unit an agent or a person picks up
```

A workitem is attached to the world by **`works_on`**, never by `contains`. `contains` asks "what
is this part of?" and `works_on` asks "what does this work change?". Keeping them separate means
work can be finished and cancelled without moving any part of the world.

A workitem's binding is **inherited down the work hierarchy**. Its own `works_on` edge wins. If it
has none, it takes the binding of its nearest ancestor that has one, so a task under a story that
works on `billing-api` also works on `billing-api` without its own edge. Only the work hierarchy
inherits this way; a world node never inherits a binding.

**Where to bind:** bind work to the node whose files or configuration the change touches. When the
change is about policy (who may do something, or what must always be true), bind it to the
`capability` node that holds that policy.

### What a workitem carries

- a **status**. The values the board can set are `pending`, `analyze`, `in_progress`, `blocked`,
  `done` and `cancelled`. `done` and `cancelled` both end the lifecycle, and both set
  `completed_at`;
- **acceptance criteria**: an ordered list of the statements that must be true for the item to be
  done;
- a **result**: what was actually delivered, recorded separately from the specification so that
  recording an outcome can never overwrite the spec;
- an **assignee**, which is always a person. An agent works on behalf of a person and is not
  assigned work itself;
- `priority`, `labels` and `depends_on` edges.

Every change to the graph is recorded in `graph_event`, with the account that made it.

## Roll-up: work rolls up, intent rolls down

**Work rolls up.** The signals the board draws on a node are computed from the node and everything
`contains` puts below it, so a `system` usually shows the state of the work under its parts, not
work bound to it directly. Work is a quantity: rolling it *down* would count the same task again at
every level.

**Intent rolls down.** An intent stated on a node binds that node and everything it contains (see
[Intents](#intents)). Intent is a constraint: a rule on the whole also binds each part, which is
exactly what `contains` says.

These are not two contradictory rules. Each one follows from what is being moved.

Roll-ups that combine values use either `max` or a union, both of which give the same answer when
a child is reached twice. Roll-ups that count first collect the ids into a **set**, so that a node
reached through two parents is counted once.

## Attention: where to look

Every node carries three derived signals. They are computed again in the same transaction as any
write that changes a status or an edge, and they are never set by hand. There is no scheduler and
no stored value that can go stale.

| Signal | Question it answers | How it is derived |
|---|---|---|
| **attention**: the node's colour | *Do I need to look at this?* | the worst of the node's own state, its urgency and its children's attention |
| **completion**: the ring | *How done is it?* | done leaves ÷ all leaves of the `contains` subtree |
| **attention count**: the badge | *How many things below need a look?* | the number of red and orange descendants |

Attention is a red / amber / green style signal with one extra colour:

| Colour | Means |
|---|---|
| **red** | blocked, or the next actionable bottleneck on work that a goal waits on |
| **orange** | waiting on someone's attention (in review), or stuck behind a red bottleneck |
| **yellow** | worth watching. Only a world node's `health` hint produces it; no workitem status does. |
| **green** | in progress or done; nobody needs to look |
| **grey** | no signal: not started, nothing waits on it, or no health hint |

A sixth colour, `purple` (awaiting a human decision), is reserved in the schema but not produced
yet.

Two rules make the colours useful:

- **Grey is excluded from "worst".** Grey means *no signal*, not *healthy* and not *bad*. A node
  with one grey child and one green child is green, not grey.
- **Urgency flows backward along `depends_on`.** A task that has not started is grey only while
  nothing important waits on it. If not-done work under an epic depends on it, it lights up. Only
  the *actionable* bottleneck (the one whose own dependencies are all done) is red; the items
  queued behind it are orange. A chain of dependencies therefore shows one red item, the one to
  do next, instead of turning entirely red.

Attention and completion are independent. A green node can be half done.

## Documents

A node can carry **documents**: markdown pages stored with the node (in the `node_doc` table) and
listed in its `attributes.docs`. Each entry has a `path`, a `title` and a `kind` such as
`architecture`, `runbook`, `design` or `integration`. Documents hold what is too long for
`detail`: how a part works, how to operate it, why it was built the way it was. Mermaid diagrams
render on the board.

Agents read a document with `atlas_doc_read` and write one with `atlas_doc_write`. Search covers
the documents a node serves, and returns a pointer to where the match is, not the text of the
document.

## Intents

An **intent** is a lasting statement of purpose attached to a node. It has a `slug`, a `kind`, a
title, the statement itself and an optional source. There are four kinds:

| Kind | Example |
|---|---|
| `constraint` | "Every hop that carries a token is TLS." |
| `principle` | "Prefer adding a setting over changing a default." |
| `non-goal` | "This service does not store files." |
| `outcome` | "An operator can restore the install from one backup." |

Intents are **inherited down `contains`**. A node is bound by its own intents plus those of every
ancestor. A child overrides an inherited intent by stating its own with the **same slug**, and
from that node downward the child's version applies. `atlas_get` returns a node's intents split
into `own` and `inherited`. The rule for anyone changing something, person or agent, is to read
the node's intents first and follow them. `GET /api/intents` searches intents across the whole
graph, for example to list every `constraint`.

Intents are stored one row per statement, not as a list inside the node. That way two people (or
two agent sessions) editing different intents on the same node cannot overwrite each other's
changes.

## Finding things: search, retrieval and context

- **Search** is built in: keyword search in Postgres over names, the three text lengths,
  acceptance criteria, results, labels, decision records and served documents. It answers with
  **pointers**: the node, where the match is and a short snippet.
- **Retrieval** is an optional add-on that adds semantic search (search by meaning, using
  embeddings) alongside keyword search. You point it at an embeddings endpoint of your choice. It
  is off by default, and Atlas works fully without it.
- **Context** (`GET /api/context`, the `atlas_context` tool) answers "tell me about X" in one
  bounded response. It returns the nodes that best match, what sits around them, what would be
  affected by a change, the intents that constrain them, the decisions that bear on them and the
  documents to read next. Each part is a pointer and each part has a size limit. This is how an
  agent gathers context for a task cheaply: it reads the pointers first and opens the full text
  only for what it needs.

In every case, the caller's visibility filter is part of the query. A node you may not see is
never returned, never counted and never used as a path to reach another node.

## The board and `/docs`

- **`/board`** is where people work with the graph. It has three parts: a radial **graph** of
  the world, a **work board** filtered to whatever part of the world you select, and a **node
  panel** that shows one node's text, documents, intents, acceptance criteria and links. The
  colours, rings and badges are the attention signals described above. The previous board stays
  available, frozen, at `/board/legacy`.
- **`/docs`** is the install's own handbook, served as a static site. Every install has an
  `atlas-system` node with one child per part (`atlas-api`, `atlas-mcp`, the database, the
  gateway, search, retrieval), and the handbook is those nodes' documents. The same text appears
  on the board, through `atlas_doc_read` and on `/docs`. `/docs` needs sign-in unless
  `ATLAS_PUBLIC_DOCS=1`.
- **Agents** see the same graph through `atlas-mcp`, with their own token. They see what that
  token may see, and every write is attributed to them. See [Agents](agents.md).

## What a new install contains

A fresh install that sets `ATLAS_SEED=none`, `demo` or `self` starts with Atlas's own world model
under the `atlas-system` node: its services, its capability areas, every MCP tool, every API area,
the board's views and the admin CLI, each with its documents. `demo` adds a sample project, and
`self` adds the `atlas` project. The `homelab` profile seeds none of it, and nothing is written
into a database that already has nodes. An existing install adds the missing nodes with
`python /app/system_node.py --apply`; nothing that exists is changed.
