---
audience: user
---

# Contributing

This page covers how to set up a development checkout, run the checks a pull request must pass,
and propose a change to Atlas. Read [Architecture](ARCHITECTURE.md) first if you have not: most
review comments come down to one of the decisions recorded there.

## Development setup

You need:

- **Python 3.12**, the version the images and CI use. A newer Python can pass locally and fail in
  CI.
- **Docker.** The backend suite starts its own disposable PostgreSQL container, so Docker must be
  running. You do not install or configure a database yourself.
- **Node.js 24**, to syntax-check the board's modules and run its `node --test` suites. The board
  has no build step.
- **Bash**, for the shell checks under `tools/ci/`.

Install the dependencies into a virtual environment:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt -r mcp/requirements.txt
pip install -r tests/requirements.txt     # only for the pytest wrapper and the end-to-end tests
```

The repository layout:

| Path | What |
|---|---|
| `backend/` | `atlas-api`: the FastAPI app (`main.py`), the auth gate (`auth.py`), the routes (`routers/`), and the `verify_*.py` suite |
| `mcp/` | `atlas-mcp`: the MCP server, its tools and its own `verify_*.py` scripts |
| `frontend/` | the board, plain ES modules with no build step |
| `deploy/oss/` | the install: the compose file, the Caddy ingress and `.env.example` |
| `tools/` | the reference-page generators, the scrub gate (`tools/scrub/`) and the CI checks (`tools/ci/`) |
| `docs/oss/` | this documentation |

To run a full install locally, follow [Install](INSTALL.md). It is the same compose project CI
starts in its install smoke test.

## Tests

The suite is a set of standalone scripts, not pytest tests. Each `verify_*.py` prints one `[PASS]`
or `[FAIL]` line per claim and exits non-zero if any claim failed, so a failure tells you which
claim broke without a stack trace.

```bash
cd backend
python verify_all.py                  # every backend/verify_*.py, one exit code
python verify_all.py --only object    # only the scripts whose file name matches
python verify_all.py --verbose        # full output, not only the failures
python -m pytest test_verify_all.py   # the same scripts, one pytest test each
```

`verify_all.py` finds the scripts on disk, so a new `backend/verify_<thing>.py` joins the suite by
existing. It starts its own PostgreSQL container for the run. Do not point it at a database of your
own with `ATLAS_TEST_DB_URL`: CI does not, and a run that uses a different database from CI is not
evidence that CI will pass.

The MCP scripts run one at a time:

```bash
cd mcp
for f in verify_*.py; do python "$f" || echo "FAILED: $f"; done
```

Some of them start the real `atlas-api` in-process against a disposable database, which is why
they need both requirement files.

Run the suite a second time with every optional feature switched off. CI does, and a change that
works only with the defaults fails there:

```bash
ATLAS_DISABLED_FEATURES=github_link,save_work,deploy_status,deploy_warning,attention_probes \
  python verify_all.py
```

Before you push a board change, syntax-check every module:

```bash
shopt -s globstar
for f in frontend/*.js frontend/v2/**/*.js; do node --check "$f"; done
```

A new board module must also be listed in `ASSET_FILES` in `backend/routers/assets.py`, or it is
not served.

### Rules the suite enforces

Several conventions are checks, not advice. You find out about them either way:

- **A new route needs an access classification.** `verify_node_access.py` enumerates the route
  table and fails on any `/api` route it cannot classify.
- **A new write route needs a scope.** A mutating route that is missing from
  `auth.WRITE_SCOPE_ROUTES` is refused at run time, and the suite notices.
- **The route table is pinned.** `verify_routes.py` compares the live table with
  `backend/routes.snapshot.txt`. Update the snapshot in the same pull request that adds or removes
  a route, so the reviewer sees the change.
- **`auth.PUBLIC_ROUTES` is pinned exactly.** Adding a route that needs no sign-in is a security
  decision. Explain it in the pull request.
- **There is one writer for `graph_event`.** A second place that records a graph event fails the
  suite. Call the existing writer.

## The CI jobs that gate a pull request

Every workflow under `.github/workflows/` runs on a pull request to `master`. Some run only when
the paths they cover change, as noted below. A newer push to the same pull request cancels the
older run.

| Workflow | Job | What fails it |
|---|---|---|
| `verify` | backend suite | any `backend/verify_*.py` (including the board's `node --test` suites), with the default configuration and again with every feature disabled |
| | route table | the registered routes differ from `backend/routes.snapshot.txt` |
| | atlas-mcp verify scripts | any `mcp/verify_*.py`, in both configurations |
| | network exposure and TLS | `tools/ci/check_binds.sh` (a port published to every interface) or `tools/ci/check_tls.sh` (a plain-HTTP token hop, or certificate verification switched off) |
| | gate integrity | a pull request that deletes a verify script or a workflow |
| | frontend parse check | `node --check` on any board module, or a board module missing from the asset allow-list in `backend/routers/assets.py` |
| | briefing files | `backend/verify_docs.py`, and `tools/docs_site.py --check` (the handbook site is stale) |
| | compose | a compose overlay that re-declares `atlas-api`'s ports |
| `oss` | OSS TLS front | the Caddy ingress, the install CA and mutual TLS, end to end |
| | OSS config | `.env.example` out of step with the variables the code reads, the browser security headers, the compose file's network rules, the gateway configuration, or `init-secrets.sh` |
| `install-smoke` | install smoke | installing `deploy/oss` from `.env.example` on a clean runner, then signing in, minting agent tokens and calling the MCP. Runs when deploy, backend, mcp, frontend, scripts, tests or `tools/ci` change. |
| | backend suite via pytest | the pytest wrapper |
| `scrub` | scrub | a personal reference in an enforced path. See [The scrub gate](#the-scrub-gate). |
| `sast` | SAST, secrets, end-of-life, locks | a new high Semgrep or Bandit finding, a leaked secret (gitleaks), a runtime near end of support, or a `requirements-lock.txt` out of step with `requirements.txt` |
| `sbom-sca` | SBOM + SCA | a fixable critical or high vulnerability in an image, a lock file or the vendored JavaScript. Skipped for documentation-only changes. |
| `images` | build, check and scan | the hardened images fail to build, fail their checks or fail the vulnerability gate |
| `dast` | ZAP scan | a high-risk alert. Runs on a pull request only when its own files change, and on a schedule. |
| `pins` | pins, dependabot schema | an invalid `dependabot.yml`. Unpinned images and actions are reported, not failed. |
| `dependabot-security` | auto-merge a Dependabot security update | nothing: on a Dependabot pull request in a `*-security` group it approves and turns on auto-merge, so the pull request merges once every required check is green. Every other pull request is left alone. |
| `tools` | tool tests | the shell tools, the licence and NOTICE files, or the client pack |
| `cicd-lint` | generated pipelines | a generated CI pipeline that fails actionlint or its schema. Runs only when its inputs change. |
| `openshift-lint` | OpenShift manifests | a manifest under `deploy/openshift/` that fails `kustomize build`, kubeconform, kube-linter or a policy in `tools/ci/openshift/policy/` (a secret in the environment, a container outside the restricted security context, an image without a digest, a mounted ServiceAccount token, a plain-HTTP URL, a pinned `runAsUser` in the base), or a policy fixture that no longer fails. Runs only when those files change. |

A check written in shell must not decide on `producer | grep -q` (or `grep -qv`, `grep -m1`,
`| head`) while `set -o pipefail` is on, as it is in the `tools/ci` scripts and in any workflow step
that sets it or runs under `shell: bash`. grep -q exits at its first match, the producer dies of
SIGPIPE writing the rest, and the pipeline reports a miss for a match it found, so a check that
fails on a match passes. Read the output into a variable and grep a here-string
(`grep -q x <<< "$out"`), or grep the file itself. `tools/ci/test_init_certs.sh` and
`tools/ci/openshift/lint.sh --self-test` each plant a match to prove their check still bites.

A workflow reports; it does not block a merge on its own. Which checks are required is set by the
branch protection on `master`. Treat every red check as a blocker unless a maintainer says
otherwise in the pull request.

## Opt-in by configuration

Atlas runs in installs that already exist, and an upgrade must not change what they do. So:

- **New capability is added behind configuration.** A new feature, login method, role model or
  add-on is switched on by a setting. The setting is read and validated in one place
  (`backend/settings.py`, or `mcp/app/settings.py` for the MCP server), and a malformed value stops
  the boot with an error that names the variable.
- **The code default keeps the existing behaviour.** With the new setting unset, the route table,
  the responses and the schema are what they were before your change. Project RBAC, OIDC and the
  retrieval add-on all follow this pattern. The OSS install opts in to what a new team install
  should have through `deploy/oss/.env.example`. The code defaults do not change for that.
- **Off means not running.** When a feature is off, its routes are not registered and its tables are
  not read. Tests prove that the off path is unchanged, often by breaking the feature's code on
  purpose and checking that the answers stay the same. A feature that an install may want to
  remove entirely gets a name in `ATLAS_DISABLED_FEATURES`.
- **Schema changes are additive.** Add new tables or nullable columns, which the reconciler creates
  without a deploy step. Anything else (a `NOT NULL` column, a type change, a rename, a drop) needs
  a `backend/migrate_*.py` script that is a dry run unless given `--apply`. See
  [Architecture](ARCHITECTURE.md#migrations).

A pull request that adds a setting also documents it with a comment in `deploy/oss/.env.example`,
because that comment is the description on the generated [Configuration](CONFIG.md) page.

## Regenerating the reference pages

Three pages are generated from the code and committed. Edit the code, then regenerate the page in
the same pull request. Each generator has a `--check` mode that renders the page in memory and exits
1 if the committed page is stale. Run all three with `--check` before you push.

| Page | Command | Source | Needs |
|---|---|---|---|
| [MCP tools](MCP-TOOLS.md) | `python tools/gen_tool_ref.py` | the tools `atlas-mcp` registers, and their descriptions in `mcp/app/descriptions.py` | `pip install -r mcp/requirements.txt` |
| [Configuration](CONFIG.md) | `python tools/gen_config_ref.py` | every `ATLAS_*` variable the code reads, with the comments in `deploy/oss/.env.example` | the standard library only |
| [REST API](API.md) | `python tools/gen_api_ref.py` | the FastAPI app's own OpenAPI schema | `pip install -r backend/requirements-lock.txt`. The output depends on the FastAPI and Pydantic versions, so use the lock file, not `requirements.txt`. |

```bash
python tools/gen_tool_ref.py --check
python tools/gen_config_ref.py --check
python tools/gen_api_ref.py --check
```

None of the generators needs a database, a network connection or a running install. `gen_api_ref.py`
also runs the scrub gate's generic patterns over the page it writes, and refuses to write a page
that contains a personal reference. Pass `--denylist <file>` to check against a denylist as well.
If it refuses, fix the handler docstring it names. Only the first paragraph of a docstring reaches
the page.

## The scrub gate

The published tree must not contain personal references: people's names, private domains, internal
hostnames, private network addresses or real email addresses. `tools/scrub/scan.py` looks for them.

It has two kinds of pattern:

- **Generic patterns**, in the repository: RFC 1918 IPv4 addresses, `<host>.local` names and
  email-shaped strings.
- **A denylist** of specific terms, which is never committed, because committing it would publish
  the terms. CI reads it from a repository secret. The scanner reports a denylist match as
  `denylist[N]` and never prints the matched text.

The `scrub` workflow enforces the scan on the paths that are published, including `docs/oss/*`
(which covers `docs/oss/guides/` and `docs/oss/adr/`), `clients/*` and `README.md`. Elsewhere it
reports counts and stays green.

Run it locally on the files you changed. The scanner reads the tracked files, so `git add` new files
first:

```bash
git add docs/oss/my-new-page.md
python tools/scrub/scan.py --mode enforce --path 'docs/oss/*' --path README.md
```

Exit code 0 is clean, 1 is a finding, 2 is a usage error. Without a denylist only the generic
patterns run. A pull request from a fork gets the same treatment in CI, because the secret is not
available to it.

In examples, use `example.com` for domains and email addresses, `atlas.example.com` for a host and
addresses from the documentation ranges (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`)
for IP addresses. A genuine exception goes in `tools/scrub/allowlist.txt`, with a comment that says
why. Expect a reviewer to ask about it.

## Architecture decisions

The decisions that shape Atlas are recorded in `docs/oss/adr/`, one file per decision, in the
[MADR](https://adr.github.io/madr/) format. Propose one when a change:

- alters a security boundary, the auth model or the network layout;
- adds a new kind of data, service or dependency;
- changes a default, or makes something mandatory that was optional;
- picks between real alternatives that a later contributor would otherwise reopen.

To propose one:

1. Copy the structure of an existing record. Number it with the next free number in
   `docs/oss/adr/`, and give it a short, lower-case file name: `0006-short-title.md`.
2. Set **Status** to `Proposed`. Fill in *Context and Problem Statement*, *Considered Options*,
   *Decision Outcome* with its *Consequences*, and the pros and cons of each option. State the
   decision a reader can rely on, not the history of how it was reached.
3. Add a line for it under *Architecture decisions* in [the documentation index](README.md).
4. Open a pull request with the record and, where possible, the change it decides. Discussion
   happens on the pull request.
5. When it merges, the status becomes `Accepted`.

A record is not edited after it is accepted, except to fix a typo or a link. To change a decision,
write a new record that supersedes the old one, and set the old one's status to
`Superseded by NNNN`. Where an accepted record and the code disagree, the code is right, and the
record needs a superseding one.

These records are plain files. They are not registered with `tools/adr_sync.py`.

## Pull requests

Anyone may open an issue or a pull request; only the maintainer merges. Contributions are made
under the repository's licence, Apache-2.0 (inbound = outbound), and need no sign-off line. The
root [CONTRIBUTING.md](../../CONTRIBUTING.md) has the terms.

- One topic per pull request. Keep refactoring separate from behaviour changes.
- Say what changed and how you tested it. For a change a person sees, say how you checked it in a
  running install.
- Include the regenerated reference pages, the route snapshot and any `.env.example` comment in the
  same pull request as the code they describe.
- Never commit a secret, a token or a real hostname, even in a test. Tests mint their own
  credentials against the real gate, and there is no setting that turns the gate off.
- Report a security problem privately, as described in [Security](SECURITY.md#reporting-a-vulnerability),
  not in a pull request or an issue.
