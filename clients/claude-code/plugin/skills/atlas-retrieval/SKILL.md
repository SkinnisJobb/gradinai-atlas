---
name: atlas-retrieval
description: How to read context out of Atlas cheaply over its MCP tools, and where to write it back. Use BEFORE the first atlas_* read of a task - whenever you need to know what exists, which node knows about something, or what a node says - and before writing node prose. It gives the retrieval order, what each wrong first call costs, and when a full read is the right one.
---

# Reading Atlas without dumping it

Atlas holds the context: world nodes say what each part of the estate IS, workitems say what is
being done about it, and node documents hold the procedures. Reading it is cheap **if you ask the
narrowest question first**. Every tool will happily return far more than you need and nothing stops
you, so this order is the whole discipline.

## The order

1. **Structure: `atlas_outline`.** Answers "what do we actually have?" One line per node, `id [type]`;
   `names=true` adds the display name. Scope it with `root=<id>`. It defaults to the world
   (`families=object,bridge`), because the work hierarchy shares the `contains` edge and an
   unfiltered walk falls through every epic into every task.
2. **Discovery: `atlas_node_search(q=...)`.** Answers "which node knows about X?" It returns POINTERS
   (id, summary, where it matched, a snippet), never the prose. It covers world nodes and served
   documents. For the backlog use `atlas_search` with filters (`binding=`, `parent=`, `status=`, `q=`)
   and a small `page_size`.
   **Query with one or two distinctive words**, not a phrase: terms are ANDed, so a long query
   returns nothing, and stopwords are dropped silently. An identifier like `record_graph_event` is
   split into ordinary words and ranks loosely, so grep the code for code symbols instead. A match
   found only in a document comes back with `rank` 0.0, which says nothing about how good it is.
3. **Map: `atlas_structure(id)` before reading a workitem (or any node).** Answers "where does this
   sit and what does it touch?": its `contains` ancestors (task -> story -> epic), its children one
   level down (each with its own child count), what it `depends_on`, what depends on it, and its
   binding. Every node is `id, type, name, status` and nothing else, so a whole epic's shape costs
   less than one full read of a single task. Then open only what the map says matters. A long child
   list is capped (`children_limit`) and says `children_truncated` rather than cutting silently.
4. **Detail: `atlas_get(id)` on the one or two nodes that matter.** The first call that returns prose.
   It is full by default, because a node's inherited intents are the constraints on changing it - but
   inherited intents repeat verbatim on every descendant and can be a third of the payload. When you
   are only orienting, ask for less: `intents=own` or `intents=none`, and
   `prose=none|summary|abstract`. **Before you CHANGE something, read it full.**
5. **Procedure: `atlas_doc_read(node_id, path)`** only when you are about to follow what the document
   says. `path` must be one of that node's own `attributes.docs[].path` values; `atlas_get` lists them.

`atlas_capabilities` is not a way to get the tool list - the list arrives with the MCP connection. It
also returns every node type's attribute schema, tens of kilobytes.

`atlas_tree` is a **neighbourhood** tool, not a discovery tool. It needs `focus` and is minimal by
default (`depth=1`, `edges=contains`, `prose=none`, `work=none`). Raise one knob at a time.

## What the wrong first call costs

The shape of what was measured on a populated install:

| Call | Returned | What was needed |
|---|---|---|
| `atlas_tree(<large subtree>, depth=2)` with prose and work on | ~200 KB, over the tool-result limit | ~25 ids and names |
| `atlas_tree(<subtree>, depth=3)` with prose on | ~60 KB | 4 ids |
| `atlas_search(q=...)`, 12 hits, default page | ~60 KB: full abstracts plus raw attributes | 12 ids and statuses |
| `atlas_outline(root=<subtree>, names=true)` | ~2 KB | - this is the cheap version |

A listing (`atlas_search`) still carries each item's abstract and attributes. Keep `page_size` small
when you only need ids. If a result spills to a file, extract the fields you need from the file; do
not read the whole file back into context.

## Before you change something

**Read the bound node with `atlas_get` and treat every intent it returns, inherited ones included, as
a constraint on the change.** Standing decisions live there, not in the repository's briefing files,
so skipping this read means working without them. `atlas_intents(node_id=...)` lists only what a node
itself states: use it for an audit and `atlas_get` for work. Operational procedure lives in the
node's documents.

## Traps that read as "Atlas is wrong"

- **One principal's answer.** The MCP acts as the credential YOUR client sends (your agent account,
  with your person's grants). What you can read is not what another person can read.
- **Hidden and nonexistent return the same 404.** A missing node may be a missing grant.
- **A workitem's `summary` can be stale** (for example `task - status: pending` on a cancelled item).
  Read `status`, not `summary`.
- **Node prose can be out of date.** It is authored, and nothing turns red when it drifts. The claims
  that go stale are almost always *status* claims: "pending", "disabled", "not built". Before acting
  on a claim that something is or is not done, check the workitem or the code it names.

## When a full read IS right

- **Authoring or correcting a node's prose.** `abstract`/`detail` writes replace the whole field, so
  read what is there first.
- **Auditing a node** against the code or the files: you need its whole claim set.
- **Following a runbook** in a node document: read all of it, not a snippet.

Being thorough there is correct. The failure this skill exists for is reading everything in order to
*find* something.

## Writing it back

- **Prose belongs on the node the work BINDS to.** Update that node's `summary`/`abstract`/`detail`
  and its documents in the same piece of work that changed the behaviour.
- **Prose says what a thing IS; the data says how much and in what state.** Do not write counts, and
  do not write another item's status into prose ("pending on TASK-N"). Name the item and let the
  reader ask for its status.
- **Assume others can read it.** Board content is visible to everyone with access to the project;
  keep credentials, private hostnames and anything confidential out of node prose, docs and
  workitems.
- **Read back what you wrote.** The write tools echo the stored row; check the echo.
- **An intent is a constraint others are measured against.** Correcting a fact inside one is fine.
  Changing what it requires is the owning person's call, not the agent's.
