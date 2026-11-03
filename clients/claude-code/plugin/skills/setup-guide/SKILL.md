---
name: setup-guide
description: Run the Atlas first-run setup guide with the person who installed Atlas - an interview about their guardrails, CI/CD, environments, identity, secrets handling and work hierarchy, recorded through the atlas_setup_guide MCP tool as Proposed decision records (ADRs). Use when the person asks to set up, configure or onboard a new Atlas install, to "run the setup guide", to record how their organisation works, or to change an answer they gave before.
---

# Running the Atlas setup guide

The setup guide asks the person who runs this Atlas install how their organisation works, one
topic at a time, and records each answered topic as a decision record (ADR) in Atlas. You run
the interview; **the person gives every answer**. The guide's state lives in Atlas, so it can be
stopped and resumed at any time, by you in a later session or by anyone else.

## Before you start

- **Only an owner can run it.** The tool works with an owner's own agent token that carries the
  `admin` scope (minted with `--scopes read,write,admin`). A `forbidden` error means the token is
  someone else's or lacks `admin`: say so and stop; do not look for another way in.
- If an error says a project role is needed (`needed_role`), the install decides access per
  project: the owner must give themselves the editor role on the project the ADRs go into
  (by default `atlas-system`) before the guide can record anything there.

## The loop

1. **`atlas_setup_guide()`** with no arguments. Read `answered` / `total`, `next` and `ask`:
   `ask` is the next unanswered topic with all its questions, options and whether each one is
   `required`. If `complete` is true, every topic is answered: go to "Changing an answer" or
   "Finishing".
2. **Ask the person the topic's questions** in your own words, one topic at a time. For a
   `choice` question offer its options; for `multi` they may pick several or none; `text` is
   their own words. Say which questions are optional.
3. **Never answer for them.** Do not fill in an answer from the codebase, from what other
   organisations do, or from what seems obvious. You may explain what an option means, or say
   what you have observed ("the repository has a GitHub Actions workflow"), but the person
   decides. If they are unsure, leave an optional question out, or stop and come back later.
4. **Read the answers back** as a short list and ask them to confirm before you record anything.
5. **Record the topic:** `atlas_setup_guide(topic="<topic id>", answers={...})`, with option
   **values** (not labels) as the answers: a string for `choice`, a list of strings for `multi`,
   a string for `text`. For example:

   ```
   atlas_setup_guide(topic="guardrails", answers={
     "agent_actions": ["read", "file_work", "open_pr"],
     "approval_gates": ["merge", "deploy_prod"],
     "who_merges": "human_only",
     "who_deploys": "agent_after_approval"
   })
   ```

   The response names the ADR it created (`adr_id`, status `proposed`) and the `next` question.
   A 400 names the field that was wrong (`answers.who_merges`): fix that one and record again.
6. Tell the person the ADR id, then go back to step 1 for the next topic.

The first ADR in a project fixes that project's ADR prefix (`SYS` by default, giving `SYS001`,
`SYS002`, ...). If the response says the prefix is taken, ask the person for another two to four
capital letters and pass it as `prefix=`.

## Every ADR is Proposed until a person accepts it

Say this to the person every time you record a topic, and again at the end: **what you recorded
is a proposal**. It becomes the organisation's decision only when they accept it themselves,
signed in to Atlas in their browser (the guide's accept action,
`POST /api/setup/adrs/<id>/accept`). No agent token can accept one, whatever its scopes, so do
not try, and do not describe a Proposed ADR as decided. `atlas_setup_guide()` lists the ones still
waiting under `awaiting_acceptance`.

## Changing an answer

Ask for the topic in full with `atlas_setup_guide(topic="<topic id>")`; it shows the current
answers. Record the new answers exactly as in step 5. A changed answer creates a **new** Proposed
ADR that supersedes the old one; the old one is kept as it was, never edited. Recording the same
answers again changes nothing (`unchanged: true`).

## Finishing

When `complete` is true, give the person:

- each topic with its ADR id and status,
- the ADRs still awaiting their acceptance,
- that they can resume or change any answer later through this skill.

Topics the install added itself (`source: "extra"`) are asked the same way as the shipped ones.
