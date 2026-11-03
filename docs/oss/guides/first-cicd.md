---
audience: operations-security
---

# A first CI/CD pipeline

Atlas can generate a first pipeline for **GitHub Actions**, **GitLab CI** or **Azure Pipelines**
from your [guardrail matrix](guardrails.md) and the CI/CD answers of the
[setup guide](setup-guide.md). The output is **files** that you review, complete and commit. Atlas
touches no repository, no network and no git command: the generator is a pure function, and the
command only writes the files under `--out`. An install that never runs it has nothing new
anywhere.

```sh
docker compose exec atlas-api atlas-admin cicd generate --platform github|gitlab|azure \
    (--from-guide | --matrix FILE --answers FILE) \
    --out DIR [--force] [--record-adr] [--adr-prefix SYS] \
    [--crunch FILE | --crunch-from-db]
```

The files are written on the machine where the command runs. Run inside the container, copy `DIR`
out afterwards (for example with `docker compose cp`).

## The inputs

**A guardrail matrix.** Either `--matrix FILE` (any matrix file, such as
`backend/data/guardrails/example-default.yaml`), or the matrix built from the setup guide's answers
with `--from-guide`. The generator does not read the matrix applied to the install.

**The CI/CD answers.** With `--matrix`, `--answers FILE` names them; the shipped example is
`backend/data/cicd/example-answers.yaml`:

```yaml
cicd:
  checks: [build, unit_tests, lint, sast, secret_scan]
  gates: [build, unit_tests, lint]
  deploy_trigger: on_merge
environments:
  promotion: sequential
```

The file may also hold the `cicd` answers alone, at the top level. The answers are checked against
the setup guide's own question set, so a typo is named rather than ignored.

| Answer | Values | Meaning |
|---|---|---|
| `checks` | `build`, `unit_tests`, `integration_tests`, `lint`, `type_check`, `sast`, `dependency_scan`, `secret_scan` | One CI job per check. At least one is needed. |
| `gates` | the same values | Checks that must pass before anything deploys and before a merge. A gate is also a check. |
| `deploy_trigger` | `manual` (default), `on_merge`, `on_tag`, `scheduled` | When production deploys. |
| `promotion` | `sequential` (default), `independent` | Whether a change moves through the environments in order. |

`--platform` decides the platform. With `--from-guide`, if the guide's `cicd.ci_platform` names a
different one, a note says so and the file is generated for the platform you asked for.

### From the setup guide

`--from-guide` reads the newest recorded answers of the guide's `environments`, `guardrails` and
`cicd` topics, and fails, naming the topic, if one is not answered. It builds the matrix like this:

- the environments and the production environment come from `environments.names` and
  `environments.production`;
- if `guardrails.approval_gates` includes `deploy_nonprod`, a person approves every non-production
  deploy;
- otherwise, if `guardrails.agent_actions` includes `deploy_nonprod`, the first non-production
  environment is the agents' to deploy, the one before production (with three or more
  environments) needs a person's approval, and the rest deploy through the pipeline after a merge;
- otherwise no agent deploys directly anywhere;
- `cicd.gates` become each protected branch's required checks.

What the matrix cannot say is reported as a note, not dropped in silence. If the answers let an
agent deploy to production, production's deploy stays a person's. If they let agents merge, the
matrix still keeps merging with a person; edit a matrix file by hand for that.

## What is generated

| Platform | Files |
|---|---|
| `github` | `.github/workflows/atlas-pipeline.yml`, `atlas-cicd/github-settings.md`, `atlas-cicd/github-branch-protection.json` |
| `gitlab` | `.gitlab-ci.yml`, `atlas-cicd/gitlab-settings.md` |
| `azure` | `azure-pipelines.yml`, `atlas-cicd/azure-settings.md` |

Existing files under `--out` are not overwritten without `--force`.

**The pipeline:**

- One job per check. A check that is not a gate may fail without stopping anything (GitHub
  `continue-on-error`, GitLab `allow_failure`, Azure `continueOnError`).
- One deploy stage per environment whose matrix entry names a deploy action (`deploy`,
  `deploy_via_pipeline` or `approve_deploy`), and none for any other environment.
- A deploy **waits for a person** when the environment is production, when `approve_deploy` is a
  human action there, or when no agent deploy action is allowed there. An environment where agents
  may deploy deploys on a push to its branches.
- Production deploys on the `deploy_trigger` (a push to its branch, a `v*` tag, a run started by
  hand, or a weekday schedule) and always after a person's approval. Other environments deploy on
  a push to their branches.
- Every deploy uses a `DEPLOY_CREDENTIAL` that the platform holds **for that environment**: a
  GitHub environment secret, a GitLab variable scoped to the environment, or an Azure variable
  group `deploy-<env>`. No credential is repository-wide, and no deploy runs for a pull or merge
  request.
- **Every command is a placeholder that fails** (`TODO(atlas)`, exit 1) until you replace it, so
  nothing deploys from an unfilled pipeline. The generated GitHub workflow pins each action by its
  full commit sha.

**The settings file** lists what makes the gates hold, because anyone who can push can edit a
pipeline file: per environment, its trigger, whether a person approves and where its credential
lives, and then the platform settings to configure (environments and required reviewers, protected
environments and variables, or variable groups and approvals; branch protection or policies). On
GitHub, the branch protection bodies are in `github-branch-protection.json`, in the same format as
`guardrails export --format github-branch-protection`.

**Checked before it is written.** `generate` re-reads its own output and lists every way it breaks
the rules above. If anything is listed, no file is written, so an edited template that drops a gate
cannot produce a pipeline.

## Recording it

`--record-adr` records one **Proposed** Atlas-native ADR for the generated pipeline on
`atlas-system`, slug `first-cicd-<platform>-<digest of the files>`, numbered with that node's
prefix or `--adr-prefix` (default `SYS`). The same files again write nothing. Changed files for the
same platform write a new ADR that supersedes the previous pipeline ADR for that platform. If
`atlas-system` is missing, the command refuses before writing any file.

When it finishes, the command reminds you to review the files, fill every `TODO(atlas)` and commit
them yourself. Nothing is pushed.

## The crunch-week variant

With `--crunch FILE` or `--crunch-from-db`, the generator produces the variant that
[crunch week](crunch-week.md) runs:

- The matrix goes through the crunch overlay first, so the environments in `skip_envs` have no
  deploy job.
- Production's deploy job depends on the checks alone, and keeps exactly the gate the matrix gives
  production: a person still approves each deploy.
- With backup policy `per_deploy`, production's deploy job runs
  `atlas-admin backup run --reason per_deploy` as a step **before** the deploy step, in the same
  job, after the approval. A failed backup fails the job, and the deploy step does not run. Wire
  the command to where your database is; until then it fails, which also stops the deploy. The
  other policies (`once`, `hourly`, `daily`) are not the pipeline's; see
  [Backups](crunch-week.md#backups).
- A comment at the top of the file and a section in the settings file say it is the crunch
  variant, until when, which steps it skips, and to generate again without `--crunch` when crunch
  week ends.
- `--record-adr` is refused with a crunch flag: the variant is temporary, and crunch week records
  its own ADR.

Without a crunch flag, or with `--crunch-from-db` while crunch week is off, the output is byte for
byte the normal pipeline.
