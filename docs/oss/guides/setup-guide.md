---
audience: operations-security
---

# The setup guide

The setup guide interviews whoever installed Atlas about how their organisation works: guardrails
for agents, CI/CD, environments, identity, secrets handling and the work hierarchy. It records each
answered topic as a **Proposed** decision record (an Atlas-native ADR), which becomes the
organisation's decision only when a person accepts it. Its answers also feed the
[first CI/CD pipeline](first-cicd.md).

An install that never runs the guide has an empty `setup_answer` table, no guide-made ADR, and
behaves exactly as before.

## Running it

Today the guide has one client: an **AI agent interview** through the MCP tool
`atlas_setup_guide`. The Claude Code client pack has a `setup-guide` skill that carries how to run
the interview: the agent asks the person each question and never answers for them. There is no
board wizard yet; it would be a later client of the same API.

**Who may run it:** the instance owner in a browser session, or an agent token that holds the
`admin` scope **and** is linked to a live owner. Everyone else gets `403 forbidden`. In practice the
owner mints their own agent token with `read,write,admin` and runs the interview with it. The
answers describe how the whole install is governed, which is the owner's to state.

The MCP tool has two forms:

| Call | Does |
|---|---|
| `atlas_setup_guide()`, or with `topic=` | Reads where the guide stands, and the topic to ask next in full (`GET /api/setup`). |
| `atlas_setup_guide(topic=..., answers={...}, project=..., prefix=...)` | Records one topic's answers as a Proposed ADR (`POST /api/setup/answers`). |

`answers` without `topic` is refused before any call. There is deliberately **no accept** in the
tool (see [Accepting](#accepting-an-adr)). On a live install the tool must also be on your MCP
gateway's allow-list, if you run one.

### What the state shows

`GET /api/setup` returns the question set's version, every topic with whether it is answered, its
ADR and that ADR's status, who answered and when, the next unanswered question (`next`), the topic
to ask in full (`ask`), `answered` and `total`, `complete`, and the ADRs still awaiting acceptance.
The guide can be stopped and resumed at any time, by any client.

## What it asks

The shipped question set is version `1`, in `backend/data/setup_guide/questions.v1.json`. Every
question is required except `guardrails.notes`. Answers use the option **values** below, never the
labels.

| Topic | Question | Type | Values |
|---|---|---|---|
| `guardrails` | `agent_actions` | multi | `read`, `file_work`, `write_docs`, `open_pr`, `merge`, `deploy_nonprod`, `deploy_prod`, `change_config` |
| | `approval_gates` | multi | `merge`, `deploy_nonprod`, `deploy_prod`, `schema_change`, `acceptance_change`, `secret_change`, `access_change` |
| | `who_merges` | choice | `human_only`, `agent_after_approval`, `agent_when_green` |
| | `who_deploys` | choice | `human_only`, `agent_after_approval`, `pipeline_on_merge` |
| | `notes` | text | optional |
| `cicd` | `ci_platform` | choice | `github_actions`, `gitlab_ci`, `jenkins`, `azure_pipelines`, `other`, `none` |
| | `checks` | multi | `build`, `unit_tests`, `integration_tests`, `lint`, `type_check`, `sast`, `dependency_scan`, `secret_scan` |
| | `gates` | multi | the same values as `checks` |
| | `deploy_trigger` | choice | `manual`, `on_merge`, `on_tag`, `scheduled` |
| `environments` | `names` | text | comma-separated, least to most protected |
| | `production` | text | one of the names |
| | `promotion` | choice | `sequential`, `independent` |
| `identity` | `login` | choice | `local`, `oidc`, `both` |
| | `rbac` | choice | `off`, `projects` |
| | `default_visibility` | choice | `shared`, `private` |
| `secrets` | `storage` | choice | `files`, `vault`, `cloud_secret_manager`, `ci_secrets`, `other` |
| | `agent_access` | choice | `never`, `path_only`, `scoped` |
| | `rotation` | choice | `manual`, `scheduled`, `on_incident` |
| `work_hierarchy` | `levels` | choice | `epic_story_task`, `story_task`, `task_only` |
| | `who_files` | multi | `people`, `agents` |
| | `acceptance` | choice | `people`, `agent_proposes` |

A wrong answer is a `400` that names the field, for example `answers.who_merges`.

**The answer keys are a contract.** Other parts of Atlas read answers by `<topic>.<question>` and the
option values. A new meaning gets a new id or a new question-set version, never a reused one.

### Adding your own topics

Name a second file of the same shape in `ATLAS_SETUP_QUESTIONS_FILE`. Its topics are asked after the
shipped ones. A topic id that repeats one already loaded is an error, so an install can add topics
but never silently redefine one. Both files are read and validated each time the guide is used,
never at boot. A bad file makes the guide's routes answer `503 bad_question_set`, naming the file
and the place in it. Check a file before you set it:

```sh
docker compose exec atlas-api python setup_guide.py --check FILE
```

Each topic has an `adr` block (`title`, `context`, `decision`, `consequences`) whose
`{question_id}` placeholders are filled with the answers as a person reads them.

## What it produces

**One Proposed ADR per answered topic**, written through the ADR registry's own write, so it gets
the registry's id allocation and its `node.adr.write` event, attributed to the caller (an agent's
event also names the person it belongs to). The ADR's sections come from the topic's `adr` block,
and its content ends with an `## Answers` table of the raw keys, so the exact values can be read
back from the record itself.

- **Where.** The project named in `project`, else the project the guide already records into, else
  `atlas-system`, the install's own system node.
- **Prefix.** The first ADR in a project with none fixes its prefix: `prefix`, else `SYS`.
- **The same answer again** writes nothing: no ADR, no event, no row.
- **A changed answer** writes a new Proposed ADR that supersedes the topic's previous one, which is
  left untouched. A changed answer must go to the project the topic was first recorded in.
- **Project roles.** With `ATLAS_RBAC=projects`, recording on `atlas-system` does not need a role
  binding there, for the callers allowed above. Any other project keeps the registry's role check.

The guide also keeps its own state row per answer (`setup_answer`), with the question-set version,
the answers, the ADR, and who answered.

**Feeding other tools.** `atlas-admin cicd generate --from-guide` reads the newest answers of the
`environments`, `guardrails` and `cicd` topics to build a guardrail matrix and the pipeline input;
see [First CI/CD](first-cicd.md#from-the-setup-guide). The answers of the other topics are recorded
in their ADRs; nothing changes the install's configuration from them.

## Accepting an ADR

Accepting is **a person's act, from a browser session**: `POST /api/setup/adrs/{adr_id}/accept`
by the instance owner, with the session's CSRF token. Every token is refused, whatever its scopes
and whoever it is linked to, and a member gets `403 forbidden`. The ADR's status becomes Accepted,
with "Accepted by" the owner and the date.

- An ADR that a later answer superseded is refused (`409 superseded`), naming the newer one.
- An ADR that is not Proposed is refused (`409 not_proposed`).
- An ADR the guide did not record is `404 unknown_guide_adr`.

The registry's generic ADR edit refuses to change a guide-made ADR, so this route is the one way to
accept one, and a new answer the one way to replace one. There is no reject: answer again to
supersede. The board has no accept button yet, so the owner calls the route from a signed-in
browser session.
