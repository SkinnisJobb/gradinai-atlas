---
audience: operations-security
---

# Agent guardrails per environment

The guardrail matrix states, as data, what AI agents may do in each of **your own** environments:
which branches belong to each environment, which actions an agent takes on its own, which need a
person, and which need a person with a step-up sign-in. Atlas validates the matrix, stores it on
the install's system node where agents read it, and exports it as policy files for your agents and
your repository host.

Nothing applies a matrix by itself. An install that never applies one has no guardrail document,
intent or ADR, and behaves exactly as before.

```sh
docker compose exec atlas-api atlas-admin guardrails validate FILE
docker compose exec atlas-api atlas-admin guardrails apply FILE [--dry-run] [--adr-prefix SYS]
docker compose exec atlas-api atlas-admin guardrails show [--crunch FILE | --crunch-from-db]
docker compose exec atlas-api atlas-admin guardrails export \
    --format claude-code|github-branch-protection [--env NAME ...] [--from FILE] --out PATH|- \
    [--crunch FILE | --crunch-from-db]
```

The file is read inside the container, so put it where the container can see it (for example with
`docker compose cp`).

## The matrix format

A matrix is a JSON or YAML file. The shipped example, `backend/data/guardrails/example-default.yaml`,
is a starting point to copy and edit; nothing reads it unless you name it.

```yaml
version: 1                       # required; the only version Atlas reads
name: "Example default"          # optional label, carried into the document and the ADR
description: "..."               # optional
custom_actions:                  # optional: actions outside the vocabulary, name -> meaning
  run_migration: "apply a database migration"
environments:                    # required, ORDERED: first is closest to the developer
  - name: dev
    branches: [dev, "feature/*"]
    protected: false
    agent: [branch, commit, push, open_pr, deploy, read_logs, propose]
    human: [merge]
  - name: preprod
    branches: [preprod]
    agent: [read_logs, propose]
    human: [merge, approve_deploy, deploy]
    commands:
      deploy: ["tools/deploy.sh preprod*"]
  - name: prod
    branches: [main]
    production: true
    agent: [read_logs]
    human: [merge, approve_deploy, deploy]
    step_up: [deploy]
    required_reviews: 1
    commands:
      deploy: ["tools/deploy.sh prod*"]
```

Each environment has these keys, and no others:

| Key | Meaning | Default |
|---|---|---|
| `name` | Lowercase kebab-case, 1 to 40 characters, unique. | required |
| `branches` | At least one branch or pattern; `*` may appear anywhere. No pattern may belong to two environments. | required |
| `production` | Marks a production environment. At least one environment must be production. | `false` |
| `protected` | Whether its branches are protected. | `true`, unless agents may `merge` there |
| `agent` | Actions an agent may take here on its own. | `[]` |
| `human` | Actions that need a person here; an agent may only propose them. | `[]` |
| `step_up` | Of `human`: those that also need a step-up sign-in. Each must be in `human`. | `[]` |
| `required_reviews` | Reviews on a protected branch, 0 to 6. | 1 when protected, else 0 |
| `required_checks` | CI check names a protected branch requires. | `[]` |
| `commands` | Command patterns per vocabulary action, which the Claude Code export allows or denies. | none |
| `notes` | Free text, carried into the environment's intent. | none |

An action that is in neither `agent` nor `human` is not done in that environment at all, which for
an agent is the same as denied. An action may not be in both lists.

### The action vocabulary

| Action | Meaning |
|---|---|
| `branch` | create a branch |
| `commit` | commit |
| `push` | push to the environment's branches |
| `open_pr` | open a pull request |
| `merge` | merge a pull request into the environment's branches |
| `deploy` | deploy directly |
| `deploy_via_pipeline` | deploy only through the pipeline, after a merge |
| `read_logs` | read logs |
| `propose` | propose a change (an issue, a comment, a draft) without making it |
| `approve_deploy` | approve a deploy |

**Custom actions** are allowed when declared under `custom_actions` with a description. An
undeclared unknown name is an error, so a typo never passes as a new action. Custom actions are
carried into the intents, the document and the ADR, so agents read them. The exports ignore them,
because an export can only translate an action whose meaning it knows, so `commands` may not name
one.

### Rules the matrix enforces

These encode standing decisions, not only the shape:

- An agent never holds deploy rights in a production environment: `deploy`, `deploy_via_pipeline`
  and `approve_deploy` may not be in a production environment's `agent` list.
- Approving a deploy is a human act: `approve_deploy` is never in any `agent` list.
- A protected production environment needs at least one review.

`validate` reports every problem at once, each naming the environment and the key.

## Applying it

`apply` writes to the system node `atlas-system`, never to a project of your own. If that node is
missing, it refuses and says how to add it. Each change is one graph event. It writes:

- the document `docs/nodes/atlas-system/guardrails.md` on the node: a readable table, plus the
  normalised matrix as a fenced JSON block that `show` and `export` read back;
- **one `constraint` intent per environment**, slug `guardrail-<env>`. Its statement is generated
  from the matrix (what an agent may do, what needs a person, what is not an agent action, the
  review rule, the production rule and the notes), and its source names the matrix version, its
  digest and the file. An environment you remove has its intent deleted;
- a **Proposed** ADR (an Atlas-native ADR), slug `agent-guardrails-<digest>`, numbered with
  `atlas-system`'s prefix if it has ADRs already, else `--adr-prefix` (default `SYS`).

**Agents read it** with `atlas_get`: the intents are returned on `atlas-system` and, inherited, on
every node below it.

**Idempotent.** The digest is the sha256 of the normalised matrix. Applying the same file again
writes nothing. A changed matrix updates the document and the intents and writes a **new** Proposed
ADR that supersedes the previous guardrails ADR. If the previous one was still Proposed it becomes
Superseded; an Accepted one stays Accepted until a person accepts the new one.

`--dry-run` lists what would change and writes nothing.

**Who it is attributed to.** A command-line write has no signed-in principal, so its events carry
`actor_kind = "system"`. The operator name (`--actor`, else `ATLAS_ADMIN_ACTOR`, else the OS user)
and the command travel in each event's detail as `operator` and `via`.

## The exports

`export` reads the applied matrix, or a file with `--from FILE`. `--env NAME` (repeatable)
restricts it to those environments. `--out -` writes to standard output.

### `claude-code`

A settings fragment, `{"permissions": {"allow": [...], "deny": [...]}}`, to merge into a Claude Code
`.claude/settings.json`. It denies:

- pushes to the branches of every protected environment where agents may not `push`;
- force pushes wherever any selected branch is protected;
- `gh pr merge`, unless agents may `merge` in every selected environment;
- each `commands` pattern of an action agents may **not** take in that environment.

A `commands` pattern of an action agents **may** take becomes an allow rule. Deny wins over allow,
both in the fragment and in Claude Code. These rules are defence in depth for an agent's own
session: the boundary is the branch protection on your repository host, which a local setting
cannot weaken.

### `github-branch-protection`

For each protected branch pattern, the JSON body for GitHub's
`PUT /repos/{owner}/{repo}/branches/{branch}/protection`: the required reviews, stale reviews
dismissed, no force push, no deletion, the required checks, and on production environments admins
included and the last push approved. An empty check list is marked as a TODO to fill. A pattern
containing `*` cannot go through that endpoint, which takes one branch name, and is marked for a
repository ruleset or GraphQL `createBranchProtectionRule` instead. Atlas calls no GitHub API; you
apply the bodies.

### During crunch week

`--crunch FILE` or `--crunch-from-db` shows or exports the matrix as [crunch week](crunch-week.md)
runs it: the skipped environments lose their deploy actions, and production is unchanged. See
[What it changes in the guardrail matrix](crunch-week.md#what-it-changes-in-the-guardrail-matrix),
and the known limitation of `--crunch-from-db` described there.

## A matrix from the setup guide

`guardrails.matrix_from_answers` builds a matrix from a short set of answers. The setup guide's
answers feed it when you generate a pipeline with `atlas-admin cicd generate --from-guide`; see
[First CI/CD](first-cicd.md#from-the-setup-guide). That matrix is used for the generated pipeline
only: it is not applied to the install. To store it, write it as a matrix file and `apply` it.
