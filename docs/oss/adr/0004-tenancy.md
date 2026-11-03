---
audience: operations-security
---

# 0004. Tenancy: visibility in the query, and opt-in project roles granted to groups

- **Status:** Accepted
- **Tags:** authorization, tenancy, RBAC

## Context and Problem Statement

One Atlas install serves several teams, and some of their projects must not be visible to everyone.
At the same time, a small install with a handful of trusted people should not have to manage roles
at all.

Hiding data is harder than refusing a request. A node that is filtered out of a list can still show
up in a count, a roll-up, a search result, a path between two nodes or an error message that differs
from "not found". Each of those tells the caller the node exists.

Larger installs also manage people in groups, usually in an identity provider. Granting access
person by person does not scale, and the access should follow group membership when someone joins
or leaves.

**Question:** how does one install keep different teams' data apart, and how is access granted?

## Decision Drivers

- A hidden node must not leak through any read: not a listing, count, roll-up, search, path or error.
- One place decides visibility, so rules cannot diverge between routes.
- Small installs need no configuration; team installs need project-level roles.
- Access is granted to groups, so it follows the IdP.
- An agent never has more access than its person.
- Turning the role model on or off must not require converting data.

## Considered Options

1. **Visibility computed inside every query, plus opt-in project roles evaluated in Atlas and
   granted to groups.**
2. **A post-filter** over results already loaded.
3. **An external authorization service** (a policy engine or relationship-based service) asked on
   every read.
4. **A database or schema per tenant.**

## Decision Outcome

Chosen option: **1, visibility inside the query, plus opt-in project RBAC granted to groups.**

**Visibility.**

- **The visibility filter is part of the query.** One resolver computes the set of nodes the caller
  may see, and every read applies it as a predicate inside the SQL, before anything is loaded.
  Roll-ups, counts, search and path finding take that set as input. A hidden node is therefore
  never returned, never counted and never used as a step in a path, and the answer is the one the
  graph would give if the hidden rows did not exist.
- **A refusal looks like an absence.** A node the caller may not see answers `404`, the same as an
  id that does not exist.
- **Without RBAC**, everything is shared unless a subtree is marked private. A private subtree is
  visible to instance owners, to people granted it by name, and to those people's agents. A node
  with several parents is visible only if every path to it is.
- **Only instance owners set visibility and per-person grants.**

**Project roles (opt-in, `ATLAS_RBAC=projects`).**

- **The project is the unit.** A node's project is its nearest `contains` ancestor of type `project`.
  A workitem belongs to the project of what it works on. A node with several parents belongs to all
  their projects, and every check must pass on all of them. A node whose project cannot be resolved
  has no role and is refused.
- **Four ordered roles:** `viewer < editor < maintainer < owner`. A viewer reads, an editor also
  writes, a maintainer also deletes and grants up to `maintainer`, and an owner also grants `owner`
  and deletes the project root. An unrecognised stored role resolves to no role.
- **Roles are granted to groups**, and to individual people. A person's role is the highest of their
  own bindings, their groups' bindings, a grant on the project root, and the install-wide role for
  shared projects. Groups are local or synchronised from the IdP.
- **Agents are capped.** An agent's role is the lower of its token's scope cap (`read` to viewer,
  `write` to editor, `admin` to maintainer) and its person's role. No token ever exercises `owner`,
  and an agent never inherits its person's instance role.
- **Defaults keep the existing behaviour.** With `ATLAS_RBAC` unset, none of the role code runs. With
  it on, a shared project gives everyone `editor` by default, which matches the RBAC-off behaviour.
  Nothing is converted either way, and `atlas-admin rbac plan` shows the effect before it is turned
  on.

### Consequences

- **Positive:** hidden data does not leak through side channels, because every read is computed over
  the caller's visible set rather than filtered afterwards.
- **Positive:** a small install needs no role management, and a team install manages access through
  IdP groups.
- **Positive:** no external service is required, and the RBAC tables are only read when RBAC is on.
- **Negative:** two callers get different answers from the same database at the same moment. That is
  correct, but it surprises people who compare screens.
- **Negative:** computing the visible set has a cost on every read, and every new read route must use
  the resolver. A test fails on any route without an access classification.
- **Neutral:** project maintainers and owners can grant roles on their project, but changing a node's
  visibility mark or per-person grants is an instance-owner action.

## Pros and Cons of the Options

### Option 1: filter in the query, project roles in Atlas *(chosen)*

- Good: no leaks through counts, roll-ups, search or paths.
- Good: one resolver, reused by every route and by the role model.
- Bad: every read pays for the visibility computation.

### Option 2: post-filter

- Good: simple to add to an existing route.
- Bad: leaks counts and existence. A filtered page next to an unfiltered total tells the caller what
  is hidden, and a path search filtered afterwards reveals that a shorter, hidden route exists.

### Option 3: external authorization service

- Good: expressive policies, shared with other applications.
- Bad: another service to run and keep in step, and a network call on the hot path.
- Bad: a graph query still needs the visible set inside the SQL to avoid leaks, so the hard part
  stays in Atlas anyway.

### Option 4: a database per tenant

- Good: strong isolation.
- Bad: projects that relate to each other across teams cannot be linked in one graph.
- Bad: one deployment per team, and no shared view for people who work across teams.

## Links

- [Identity and access](../guides/identity-and-access.md)
- [Architecture: How a request travels](../ARCHITECTURE.md#how-a-request-travels)
- [0001 Graph model](0001-graph-model.md): the graph the filter is computed over.
- [0003 Identity](0003-identity.md): who the caller is.
