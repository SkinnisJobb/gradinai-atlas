---
audience: operations-security
---

# 0001. One graph of typed nodes and edges is the source of truth

- **Status:** Accepted
- **Tags:** data model, graph

## Context and Problem Statement

Atlas describes what a team builds and the work it does on it: systems, services, projects and
the people and agents around them, plus the epics, stories and tasks that change them. Humans read
it on a board, and agents read and write it through tools.

The things being described do not form a tree. A service belongs to a product and also runs on a
host. A project sits inside an organisation and depends on another project. A piece of work changes
one thing, but it is planned inside an epic that changes many. Any single-parent hierarchy forces
one of those relationships to be the "real" one and loses the rest.

The data also has to have one home. When some of it lives in files and some in a database, the two
drift apart, and every reader has to know which one wins.

**Question:** what is the shape of Atlas's data, and where does it live?

## Decision Drivers

- Model anything, connected in more than one way, with no fixed root.
- One authoritative copy, so the board, the API and every agent see the same thing.
- New kinds of thing should not need a schema migration each.
- Signals that roll up the graph (attention, completion, counts) must stay correct when a node has
  several parents.
- The structure of the world and the structure of the work answer different questions and must not
  be confused.

## Considered Options

1. **One Postgres graph**: a `node` table, an `edge` table with a typed `edge_type`, and type
   registries stored as data.
2. **A tree with cross-links**: each node has one `parent_id`, and other relationships are
   secondary links.
3. **Files as the source**: markdown or YAML in a repository, imported into a database for
   querying.
4. **A dedicated graph database** beside or instead of Postgres.

## Decision Outcome

Chosen option: **1, one Postgres graph of nodes and typed edges.**

- **The graph is the source of truth.** Every node, edge, document, intent and workitem is a row in
  Postgres. Startup reads no data from files: it creates the schema, seeds the `node_type` and
  `edge_type` registries (vocabulary, not content) and, into an empty graph only, the seed profile
  the install asks for. A second startup changes no row.
- **Types are data.** A node's type is a row in the `node_type` registry, and type-specific fields
  live in a JSON `attributes` column. A new type is a new registry row, not a migration.
- **World and work are separate families.** Every node type belongs to a family: `object` (a thing
  in the world), `workitem` (an epic, story or task), or `bridge`. The world hierarchy is built
  from `contains` edges between objects. A workitem is attached to the world by a `works_on` edge
  to the object it changes, and **a workitem has at most one `works_on` edge**. A workitem without
  one inherits its nearest work ancestor's binding.
- **Work rolls up, intent rolls down.** Completion, attention and workload aggregate upward from
  the work and the things below a node. A node's intent (its durable purpose and constraints)
  applies to everything below it, so reading a node returns its own intents and the ones it
  inherits.
- **Roll-ups tolerate several parents.** Because a node can be reached along more than one path,
  roll-ups use operations that are safe to repeat: the worst colour (a maximum) for attention and
  health, a union for intents. Anything counted is counted over a **set of ids**, never by summing
  children, so a node reached twice is counted once.
- **The `edge_type` registry describes, it does not enforce.** It lists each edge type, what it
  means, the family pairs it legitimately connects and whether it is still in use. Atlas reports
  drift against it (for example, a retired type gaining rows) on its health document. The write
  path enforces only a small, explicit set of family rules, such as `works_on` running from a
  workitem to an object.

### Consequences

- **Positive:** the world can be modelled as it is, with upward and sideways navigation. One copy
  of the data means no sync job and no question of which source wins. New types need no
  migration. Visibility and roll-ups are computed in one place over one structure.
- **Positive:** keeping world and work apart lets the board scope work by the world ("all work
  on this service") without the work tree distorting the world tree.
- **Negative:** graph walks happen in application code and SQL, not in a graph query language.
  Every walk must guard against cycles and must not double-count.
- **Negative:** because the edge registry does not enforce, a client can create edges the
  registry would call unusual. Drift is visible, not prevented.
- **Neutral:** a backup of the database is a backup of everything Atlas knows. There is nothing
  else to export.

## Pros and Cons of the Options

### Option 1: one Postgres graph *(chosen)*

- Good: any relationship, any number of parents, no fixed root.
- Good: one authoritative store, transactional writes and one audit trail (`graph_event`).
- Good: Postgres is already required, and its JSON and full-text features cover the rest.
- Bad: roll-ups must be written to handle several parents and cycles.

### Option 2: a tree with cross-links

- Good: simple roll-ups and an obvious display.
- Bad: forces one relationship to be primary, so moving a node in one view moves it in all of them.
- Bad: the secondary links become a second, weaker graph.

### Option 3: files as the source

- Good: diffable, reviewable in a pull request, editable in any editor.
- Bad: two copies of every fact, and an import step that has to decide which wins on every boot.
- Bad: a write through the API either has to rewrite a file or be lost on the next import.

### Option 4: a dedicated graph database

- Good: native graph queries.
- Bad: a second database to run, back up and secure, for a graph of a size Postgres handles easily.
- Bad: accounts, sessions, tokens and audit would still need a relational store.

## Links

- [Architecture: Persistence](../ARCHITECTURE.md#persistence)
- [Concepts](../guides/concepts.md)
- [0004 Tenancy](0004-tenancy.md): visibility is computed over this graph.
