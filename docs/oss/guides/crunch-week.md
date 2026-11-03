---
audience: operations-security
---

# Crunch week

Crunch week is a release mode that runs for a set time. While it is on, a change that passes CI
deploys to production **without the staging and pre-production steps** that your
[guardrail matrix](guardrails.md) normally puts in front of production. Use it for a release push,
when an hour in staging costs more than it protects. It ends by itself at the end time you set.

It is **off by default**. An install that never turns it on has no crunch tables in its database,
and nothing behaves differently.

## What it does, and what it does not

Crunch week is a **recorded decision with a time limit**, not a switch inside your CI system.
Atlas stores the window, shows it, records every change to it, and feeds it to the tools that
produce your release path. Atlas never deploys anything, never pushes to a repository, and never
edits your pipeline in place.

While it is on:

- `atlas-admin cicd generate --crunch...` produces the **crunch variant** of your pipeline. In it,
  the environments you chose to skip have no deploy job, and production deploys after the checks
  alone. See [First CI/CD](first-cicd.md#the-crunch-week-variant).
- `atlas-admin guardrails show --crunch...` and `guardrails export --crunch...` show and export the
  matrix as crunch week runs it (see [below](#what-it-changes-in-the-guardrail-matrix)).
- With a backup policy, backups are taken and verified (see [Backups](#backups)).
- Every signed-in person sees a banner on the board.

It does **not**:

- **Remove CI.** The production deploy job still depends on the checks.
- **Remove production's gate.** Production keeps exactly the gate the matrix gives it. In the
  generated pipeline a person still approves each production deploy.
- **Give agents deploy rights.** An agent never holds deploy rights in a production environment,
  crunch week or not. The matrix refuses it before and after the crunch overlay.
- **Change the matrix stored on the install.** The guardrail document, the `guardrail-<env>`
  intents that agents read with `atlas_get`, and the guardrails ADR stay as they were. The overlay
  is a view that the `--crunch` flags compute; nothing applies it.
- **Change your repository.** You generate the crunch variant, review it and commit it yourself.
  When crunch week ends, generate again without `--crunch` and commit that.
- **Run backups by itself**, unless you turned a backup source on (see [Backups](#backups)).

## Who can turn it on

Only the **instance owner** (the `owner` role), **in a browser session**, and **with their password
on every change**: activate, change, extend and end.

- The control is a tab in **Settings**, labelled *Crunch*. The board builds it only for an owner
  in a browser session. For everyone else it is not in the page at all.
- **No token can read or change it**, whatever its scopes, an owner's `admin` token included. The
  routes answer every token with `403 session_required`. A member's session gets `403 forbidden`.
- **No MCP tool exists for it**, and no agent path reaches it.
- A wrong password is `403 password_incorrect`. It is recorded as a failed login (`login_fail`, reason
  `crunch:...`) and counts against the login rate limiter, exactly like a failed sign-in.
- An owner who signs in **only through single sign-on** has no Atlas password, so the step-up
  cannot pass and that owner cannot change crunch week. Use a local owner account for it.

The routes, for reference (all browser-session only, `Cache-Control: no-store`):

| Route | Who | What |
|---|---|---|
| `GET /api/crunch` | owner | state, last 10 history entries, the matrix's environments, last backup |
| `PUT /api/crunch` | owner, password | activate, change or extend: `{config, password, csrf_token}` |
| `POST /api/crunch/end` | owner, password | end now: `{password, csrf_token}` |
| `GET /api/crunch/banner` | any signed-in person | `{active, ends_at}` and nothing else |

Atlas records every act on the system node `atlas-system`. If that node is missing, every act is
refused with `409 system_node_missing` before anything is written, and the message says how to add
it (`python system_node.py --apply` in the API image).

## The settings

The page in Settings explains the mode with a diagram of the normal path beside the crunch path,
then shows the settings, then an Activate button. Activate opens a confirmation dialog that
restates everything and asks for your password.

The request body is the `config` object below. Unknown fields are refused. Every refusal is
`400 bad_config` and names the field (for example `backup.policy` or `skip_envs[1]`), and a typo is
caught before the password is checked, so it costs no password attempt.

| Field | Meaning |
|---|---|
| `starts_at` | Leave it out to start **now**, by the server's clock. A time you send is a **scheduled** start: it must be in the server's future, at most 31 days ahead. |
| `ends_at` | The end. After the start and after now, at most 31 days after the start. |
| `duration_hours` | Instead of `ends_at`: a whole number of hours, 1 to 744 (31 days). Send exactly one of the two. |
| `backup.policy` | `none` (default), `once`, `per_deploy`, `hourly` or `daily`. |
| `backup.retention_days` | `0` with policy `none`; otherwise 1 to 365, default 7. |
| `skip_envs` | The environments of the guardrail matrix in force whose step is skipped. Never a production environment. |

Every timestamp must carry a timezone; a time without one is refused, not guessed.

### Duration and the server's clock

**The server's clock decides.** No client can move a window with its own clock:

- *Start now* means the request has no `starts_at`. The server sets the start at the moment it
  writes, the same instant as the activation time.
- A start in the past is refused. A start is never backdated, and a change never moves the start
  earlier.
- With `duration_hours`, the server computes the end. For a new window the end is the start plus
  the length. For a window that is already set (running or scheduled) the length is **added to its
  current end**: that is how the Extend box on the page works.
- The stored state, every event and every ADR carry `ends_at` only, never `duration_hours`.

A scheduled window is set but not yet in force: there is no banner, and the release tools treat
crunch week as off until the start. You can still change it or end it.

### Extend, change and end

On a window that is set, a request is an **extend** when the only difference is a later end, and a
**change** otherwise. The same settings again are no act at all: nothing is written. Ending
(`POST /api/crunch/end`) works on a running or a scheduled window. Each of these needs the
confirmation and the password again.

## Expiry

Crunch week is off from the moment `now >= ends_at`: every reader of the state treats it as off at
once. The **first read after the end** also records the end, in its own transaction: the state is
marked ended with reason `expired`, and one history row, one graph event and one ADR are written.
A read is anything that asks for the state, such as the board's banner (it asks every five minutes
for every signed-in person), the Settings page, the backup loop or a `--crunch-from-db` command.
The write is conditioned on the revision it read, so concurrent readers record the end exactly
once. Nobody acted, so it is recorded as `system`.

## What it records

Every act (activate, change, extend, end, by a person or by expiry) writes, in one transaction:

1. **The state row**, with its revision increased by one, and one row in `crunch_history`. That
   table is append-only by convention and feeds the history on the Settings page.
2. **One graph event** on `atlas-system`: `node.crunch.activate`, `node.crunch.change`,
   `node.crunch.extend` or `node.crunch.end`. Its detail holds the ADR id, the revision, the start
   and end, the backup policy and retention, the skipped environments and, for an end, the reason
   (`manual` or `expired`). It holds no password, pepper or token.
3. **One ADR** on `atlas-system`, status **Accepted**: the password step-up on the change is the
   owner's act of deciding, and an expiry is the end date that was decided at activation taking
   effect. It is numbered with `atlas-system`'s ADR prefix (`SYS` when that node has no ADRs yet),
   states the act, who, when, the window, the backup policy and the skipped environments, and an
   ADR for a change, extend or end **amends** the previous one. If the prefix belongs to another
   project, the act is refused with `409 adr_prefix_taken`.

A backup that a pipeline or the backup loop reports adds a `node.crunch.backup` event and a history
row, with detail `{ok, verified, label}`. The label is the dump's file name, never a path or a
credential.

## The banner

While crunch week is in force, every signed-in person sees a line across the top of the board:
*Crunch week: production deploys directly until* followed by the end time. It reads
`GET /api/crunch/banner`, which tells any signed-in browser session whether crunch week is on and
until when, and nothing else. It reads again every five minutes and whenever the Settings page
changes the state, and it hides itself when the end time passes. If the request fails, no banner is
drawn.

## What it changes in the guardrail matrix

The crunch overlay (`guardrails.crunch_overlay`) is a pure function. For each environment in
`skip_envs` it removes the deploy actions (`deploy`, `deploy_via_pipeline`, `approve_deploy`) from
`agent`, `human` and `step_up`, and adds a note. That environment is then no deploy step at all,
and the pipeline generator leaves it out of the path to production. Its branches, their
protection, its other actions and its command patterns stay. A command pattern of a deploy action
the environment no longer holds is exported as a **deny**, so the overlay can only narrow what an
agent may do there.

Production environments are never changed: a `skip_envs` naming one is refused, and the overlay
checks afterwards that every production environment is byte for byte what it was. The result is
validated again, so the standing rules hold after the overlay exactly as before it.

```sh
docker compose exec atlas-api atlas-admin guardrails show --crunch-from-db
docker compose exec atlas-api atlas-admin guardrails export --format claude-code \
    --crunch-from-db --out -
```

`--crunch FILE` reads a crunch config from a JSON or YAML file instead of the database: the same
fields as the [configuration](#the-settings), with `ends_at` or `duration_hours`. The release path
reads only `skip_envs` and `backup`, and refuses a key it does not know, so a misspelt `skip_env`
cannot quietly skip nothing. With a `duration_hours` file no end time is known, so the crunch
variant's header names none. With crunch week off, `--crunch-from-db` changes nothing and prints a
note saying so.

### With no guardrail matrix

Crunch week works without a matrix, but it skips nothing:

- `skip_envs` must be empty. A non-empty list is refused (`400 bad_config`, field `skip_envs`),
  and the message says to apply a matrix first (`atlas-admin guardrails apply`).
- The Settings page offers no environment to skip.
- The window, the banner, the history, the events, the ADRs and the backups work as usual.
- `guardrails show` and `guardrails export` without `--from FILE` stop with "no guardrails matrix
  is applied", crunch flag or not.

The environments are checked against the matrix **in force at the time of the request**. The
release tools check `skip_envs` again against the matrix they are given, so a pipeline generated
from a matrix whose environment names differ is refused.

## Backups

Backups are an install's choice, and the default policy is `none`. Atlas only records the policy;
a backup runs when one of three things asks for it:

| Policy | What takes the backup |
|---|---|
| `none` | Nothing. |
| `once` | The `backup` compose profile's loop: one backup per activation. |
| `per_deploy` | A step in the crunch variant of the pipeline, before each production deploy. |
| `hourly`, `daily` | The `backup` compose profile's loop, on that interval while crunch week runs. |

An operator can also take a backup at any time with `atlas-admin backup run` (reason `manual`).

**The loop** is the `atlas-backup` service, in the opt-in `backup` profile of
`deploy/oss/docker-compose.yml`. It is not started by default:

```sh
docker compose --profile backup up -d        # or COMPOSE_PROFILES=backup in .env
```

Every 60 seconds it asks whether crunch week is in force. It acts only when the policy is `once`,
`hourly` or `daily`; otherwise it says so once and waits. A failed attempt is retried after 15
minutes, not every tick.

**The per-deploy step** runs `atlas-admin backup run --reason per_deploy` in the production deploy
job, after the approval and before the deploy step. A failed backup fails the job, so the deploy
does not run. The command is a placeholder you wire to where your database is; until then it
fails, which also stops the deploy. When crunch week is off, or its policy is not `per_deploy`, the
command says it skipped and exits 0.

A reason that is not the active policy's writes nothing and says so:

```sh
docker compose run --rm atlas-backup atlas-admin backup run --reason per_deploy
docker compose run --rm atlas-backup atlas-admin backup run            # manual, always runs
docker compose run --rm atlas-backup atlas-admin backup run --verify-only /var/lib/atlas/backups
```

### How a backup is verified

A file is not called a backup until it has been restored and counted:

1. A logical `pg_dump --format=custom` of the Atlas database is taken from one database snapshot,
   and the rows of `node`, `edge` and `graph_event` are counted in that same snapshot.
2. The file's sha256 is written beside it.
3. The checksum is checked again, the dump is restored with `pg_restore --exit-on-error` into a
   **temporary database in the same Postgres**, the three tables are counted there, and the
   temporary database is dropped. Any difference is a failure.
4. A manifest (`<file>.json`: counts, sha256, size, reason, versions) is written last.

A dump that fails verification is deleted with its checksum, and the failure is recorded. Files
are named `atlas-<UTC time>-<reason>.dump` and land in the `atlas-backups` volume.

**Retention** is the crunch config's `retention_days`. After a verified backup only, older files
of that naming pattern are deleted. Nothing else in the directory is touched, nothing is pruned
after a failure, and a `manual` run with crunch week off prunes nothing.

**Credentials.** The database URL is read from the same secret file as `atlas-api`. The password
never reaches a command line, an environment value or any output.

For routine backups outside crunch week, see [Operations](operations.md).

## Risks

- **Less testing before production.** Skipped environments catch nothing while crunch week is on.
  Keep the window short, and use a backup policy.
- **A stale crunch pipeline.** The crunch variant is a file you committed. Atlas does not remove
  it when crunch week ends; regenerate without `--crunch` and commit that.
- **Backups need a source.** `once`, `hourly` and `daily` do nothing unless the `backup` profile
  runs. `per_deploy` does nothing unless the crunch variant is committed and its backup step is
  wired.
