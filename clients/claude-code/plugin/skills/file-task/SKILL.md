---
name: file-task
description: File a new workitem (task, story or epic) in an Atlas backlog through the atlas_file_work MCP tool - no files, no id guessing. Use when the person asks to file, log, record or create a task, story, epic, ticket or backlog item in Atlas, or when work you found should be tracked there. It covers picking the parent, drafting acceptance for the person to approve, the one call, and reading back the id the server minted.
---

# Filing a workitem in Atlas

The backlog is rows in the Atlas database. One `atlas_file_work` call creates the item, its parent
edge, its binding and its dependencies in a single transaction, and the board shows it at once.

**Do not write a file for it.** A task written as a markdown file is invisible to Atlas: it is not
on the board, does not roll up and is not found by search.

## What is not your job

- **Do not pick a task id.** Task ids are minted by the server; `atlas_file_work` refuses an `id` on a
  task (`id_is_minted`). Never scan for "the next free number", never reuse an id you saw elsewhere,
  never write an id into prose before the server has returned it. Read the id off the response.
- **Do not update the parent's list of children.** The parent edge is made by the same call.
- **Do not invent ids of any kind**: a `parent`, `binding` or `depends_on` value must be an id you
  have just seen returned by an Atlas tool in this session.

Epics and stories are different: they take a caller-chosen slug id, such as `EPIC-search-rework` or
`STORY-import-csv`. Propose the slug to the person; the call fails (`id_required`) without one.

## 1. Find the parent - cheaply

A task hangs off a story, a story off an epic. Use the `atlas-retrieval` order:

1. `atlas_search(type="story", q="<one or two words>", page_size=10)` to find candidates; filter by
   `parent=<epic id>` when you know the epic. A listing pages: if `total` exceeds what came back, page
   on rather than concluding the parent does not exist.
2. `atlas_structure(<candidate>)` to see where it sits and what is already under it.
3. `atlas_get(<candidate>, prose="summary", intents="own")` only for the one or two that might fit.

**Several stories fit:** list them and let the person choose. **None fits:** say so. Either file under
the closest one and state plainly that it is a poor fit and why, or propose a new story and stop.
Never file somewhere that makes the parent's own acceptance untrue without flagging it.

## 2. Draft it, and show the person before you file

- **Name**: a short imperative title.
- **Body** (the spec, never an outcome): a paragraph on *why* this exists, then `## Steps` and
  `## Out of scope`. Do **not** include a `## Result` / `## Outcome` section - the call refuses it
  (`result_in_body`); outcomes are recorded later through `atlas_update_work(result=...)`.
- **Acceptance**: 2-4 criteria, each checkable by someone who was not in this conversation. "It
  works" is not one.
- **Binding** (optional): the world node the work is about. Suggest one if it is clear; do not set it
  silently. The person decides.
- **Depends on** (optional): ids of items that must finish first.
- **Status**: `pending` unless told otherwise. Settable values are `pending`, `analyze`,
  `in_progress`, `blocked`, `done`, `cancelled`; `proposed` is shown on the board but cannot be set.

**Propose acceptance; do not decide it.** Acceptance is what the work will be judged against, so
show the draft (name, parent, acceptance, any binding) and file it only once the person agrees or
edits it. Changing acceptance AFTER the item exists is refused for an agent token
(`acceptance_is_not_yours_to_write`): use `atlas_update_work(id=..., acceptance=[...],
propose_acceptance=True, proposal_note="why")`, which a person then approves on the board.

## 3. The call

```
atlas_file_work(
  type="task",
  name="Short imperative title",
  parent="STORY-some-slug",
  body="Why this exists.\n\n## Steps\n1. ...\n\n## Out of scope\n- ...",
  acceptance=["...", "..."],
  binding="<world node id, only if the person agreed>",
  depends_on=["<id>"],
  status="pending"
)
```

Leave out the keys you do not need. For a story or epic, add `id="STORY-<slug>"` /
`id="EPIC-<slug>"` and set `type` to match.

## 4. Read it back, then report

`atlas_structure(<the returned id>)` confirms the parent edge and binding in a few hundred bytes.
Report to the person:

- the id **the server returned**,
- the parent it hangs off, and whether that fit was a guess,
- the binding you suggested (set or not),
- anything they should check.

If the call fails, report the error code it returned (`invalid_type`, `id_is_minted`,
`id_required`, `result_in_body`, an access refusal) and what you will change - do not retry with an
invented id or a different parent without saying so.
