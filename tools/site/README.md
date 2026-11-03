# tools/site: the website's screenshots

The screenshots on the website (`frontend/landing-v2/`) are taken from a fresh, disposable demo
install, never from a real instance (`docs/site/content.md` section 19). This folder and the
`site-shots` workflow make them again on demand.

| File | What it does |
|---|---|
| `demo_work.py` | `accounts`: the demo person `alex` (owner, shown as "Alex") and the agent `alex-claude` linked to Alex, made by `atlas-admin` in the container. `work`: the demo work, written through the API on top of the seed. `--dry-run` prints the plan and checks it, with no stack |
| `capture.py` | Playwright. Signs in as Alex and captures each slug. `--dry-run` prints every shot's URL, preferences, region and wait conditions, with no browser |
| `../../.github/workflows/site-shots.yml` | Boots the OSS stack from `deploy/oss/.env.example` with `ATLAS_SEED=self`, runs the two scripts, scrubs, and uploads the shots |

## Trigger it

The workflow is manual only (`workflow_dispatch`):

```sh
gh workflow run site-shots.yml --ref master
gh run watch "$(gh run list --workflow site-shots.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run download --name site-shots --dir shots/      # once it is green
```

`--ref <branch>` runs a branch's version of the scripts and the workflow, but GitHub accepts a
dispatch only for a workflow file that already exists on the default branch. So the first run is
a dispatch after this file is merged to `master`; from then on any branch can be run with
`--ref`.

It runs on the stack runner (`<CI_RUNNER>-stack`), the same one as `install-smoke`, one job at a
time: the OSS stack has fixed host ports. Allow about 15 minutes, mostly the image build.

## What it produces

The `site-shots` artifact (kept 14 days), only from a run where every step passed, the scrub
included:

| Slug | Phase | Region | Shows |
|---|---|---|---|
| `fresh-seed-graph` | seed, before any work | graph pane | the Atlas system node and its six parts |
| `backlog-view` | work | work board pane | Backlog view narrowed to the "Hybrid search across the map" epic |
| `history-tab` | work | node panel, 480 px | History of a task: rows by Alex and by alex-claude "on behalf of Alex" |
| `proposal-overview` | work | node panel, 480 px | Overview of a story with alex-claude's pending acceptance proposal |
| `board-whole` | work | whole window | graph with `atlas-backend` selected, Board view with two epic lanes, node panel collapsed to its bar |
| `my-tasks` | work | dashboard pane, default width | My tasks with six rows, one in progress at the top |
| `docs-tab` | work | node panel, 640 px | a seed node's document with a rendered Mermaid diagram |

Each slug gives two crops of its region, captured in a 1440 x 900 CSS px window at 2x, light
theme, sized to the landing page's fixed frames:

| Crop | Frame | Size |
|---|---|---|
| `<slug>.png`, wide shots (`backlog-view`, `board-whole`, `my-tasks`, `fresh-seed-graph`) | 16:10 | the largest 16:10 rectangle in the region; `board-whole` is the whole 1440 x 900 window |
| `<slug>.png`, panel shots (`history-tab`, `proposal-overview`, `docs-tab`) | 4:5 | the panel's width (480 or 640 px) by 600 or 800 px |
| `<slug>-390.png`, every slug | 3:4 | 390 x 520 CSS px |

Both crops are centred on the shot's focal element (`focal` in `SHOTS`) and never leave the
region. The frames are `WIDE`, `PANEL` and `PHONE` at the top of `capture.py`.
`dom-text/<slug>.txt` is the page's visible text at capture time; it is what the scrub step
scanned, and it is there for the reviewer.

The demo data, in short (`python tools/site/demo_work.py --dry-run` prints all of it):
two epics, four stories and ten tasks bound to seed nodes under `atlas-backend`, with statuses
in every column the API can set (to do, in progress, blocked, done); six open tasks assigned to
Alex, one in progress; one blocked task with a task depending on it; one pending acceptance
proposal by alex-claude; comments by both; status changes and an edit by the agent, so its
history rows name Alex. Every credential is generated in the job and lives only in the job's
0700 directory.

## Refresh the pictures on the site

1. Run the workflow and download the artifact, as above.
2. Look at every PNG at full size. Nothing but demo data may show: no real name, host or count.
3. Copy the PNGs, not `dom-text/`, into `frontend/landing-v2/img/shots/` under the same names,
   and commit them in a pull request. The page references the slugs, so a new name breaks it.

To change what a shot shows, edit `SHOTS` in `capture.py` (preferences, region, waits) or the
plan in `demo_work.py`, and check the result with each script's `--dry-run` before dispatching.
A wait names the condition the shot shows (rows, the diagram, the selected node); keep it that
way rather than adding delays.

## Run it against your own disposable install

On a machine with Docker, from the repository root, with an install made the same way
(`.env.example`, `ATLAS_SEED=self`, `atlas.example.com` resolving to it, and Chromium trusting
`deploy/oss/secrets/tls/ca.crt`):

```sh
pip install -r mcp/requirements.txt -r tests/requirements.txt
python -m playwright install chromium
export SMOKE_URL=https://atlas.example.com SMOKE_CA=deploy/oss/secrets/tls/ca.crt
export SMOKE_DIR="$(mktemp -d)"
python tools/site/demo_work.py accounts --compose-dir deploy/oss
python tools/site/capture.py --phase seed
python tools/site/demo_work.py work
python tools/site/capture.py --phase work --ids "$SMOKE_DIR/demo-ids.json"
```

Run `demo_work.py work` once per fresh install: task ids are minted, so a second run files the
tasks again. Never point either script at a real instance.
