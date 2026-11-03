#!/usr/bin/env bash
# Platform validation of the generated first pipelines (STORY-atlas-first-cicd, criterion 1).
#
# Generates every platform's pipeline from the shipped example matrix and example answers,
# once per production deploy trigger (each trigger renders different YAML), through the real
# CLI (`atlas-admin cicd generate`), and again as the crunch-week variant (`--crunch FILE`,
# STORY-atlas-crunch-mode), and validates each with the platform's own definition:
#
#   GitHub Actions   actionlint (the de facto workflow linter, which also reads the workflow
#                    schema and expression syntax), plus schemastore's workflow schema.
#   GitLab CI        GitLab's official CI JSON schema (the one GitLab ships for its editor,
#                    vendored in check-jsonschema as vendor.gitlab-ci). GitLab has NO fully
#                    offline linter: its real lint is the `/ci/lint` API of a running GitLab,
#                    which also resolves includes and checks job references. Schema
#                    validation is what is used here.
#   Azure Pipelines  The Azure Pipelines YAML schema (Microsoft's, vendored as
#                    vendor.azure-pipelines). Azure DevOps has NO offline linter either: the
#                    full check is a "preview run" against an organisation. Schema validation
#                    is what is used here.
#
# A validator that accepts anything proves nothing, so each one is also run on a copy with a
# misspelled key and must REFUSE it (the negative control).
#
# Tools come from the environment: ACTIONLINT and CHECK_JSONSCHEMA (default: on PATH), PYTHON
# (default python3). The workflow pins and checksum-verifies them (.github/workflows/cicd-lint.yml).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PYTHON:-python3}"
AL="${ACTIONLINT:-actionlint}"
CJ="${CHECK_JSONSCHEMA:-check-jsonschema}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# admin.py imports the auth chain, which refuses to load without its peppers. These are
# throwaway values for this process only: generate opens no database and checks no password.
gen_secret() { "$PY" -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())'; }
export ATLAS_PASSWORD_PEPPER="${ATLAS_PASSWORD_PEPPER:-$(gen_secret)}"
export ATLAS_TOKEN_PEPPER="${ATLAS_TOKEN_PEPPER:-$(gen_secret)}"
export ATLAS_LOGIN_ORIGINS="${ATLAS_LOGIN_ORIGINS:-https://ci.invalid}"
# No database for this script: point the URL at nothing, so a code path that tried to
# connect would fail here instead of quietly finding one.
export ATLAS_DB_URL="postgresql+psycopg2://none:none@127.0.0.1:9/none"

fail=0
ok() { echo "[PASS] $*"; }
bad() { echo "[FAIL] $*"; fail=1; }

lint_gh() { "$AL" -no-color "$1" && "$CJ" --builtin-schema vendor.github-workflows "$1"; }
lint_gl() { "$CJ" --builtin-schema vendor.gitlab-ci --data-transform gitlab-ci --regex-variant nonunicode "$1"; }
lint_az() { "$CJ" --builtin-schema vendor.azure-pipelines --data-transform azure-pipelines --regex-variant nonunicode "$1"; }

declare -A FILE=([github]=".github/workflows/atlas-pipeline.yml" [gitlab]=".gitlab-ci.yml"
                 [azure]="azure-pipelines.yml")
declare -A LINT=([github]=lint_gh [gitlab]=lint_gl [azure]=lint_az)

for trigger in on_merge on_tag manual scheduled; do
  answers="$WORK/answers-$trigger.yaml"
  sed "s/^  deploy_trigger: .*/  deploy_trigger: $trigger/" \
    "$ROOT/backend/data/cicd/example-answers.yaml" > "$answers"
  grep -q "deploy_trigger: $trigger\$" "$answers" || { bad "answers for $trigger not written"; continue; }
  for platform in github gitlab azure; do
    out="$WORK/$trigger/$platform"
    if ! (cd "$ROOT/backend" && "$PY" admin.py --actor cicd-lint cicd generate \
            --platform "$platform" --matrix data/guardrails/example-default.yaml \
            --answers "$answers" --out "$out" >"$WORK/gen.log" 2>&1); then
      cat "$WORK/gen.log"; bad "$platform/$trigger: generate failed"; continue
    fi
    f="$out/${FILE[$platform]}"
    if "${LINT[$platform]}" "$f"; then
      ok "$platform/$trigger: ${FILE[$platform]} passes the platform's validation"
    else
      bad "$platform/$trigger: ${FILE[$platform]} fails the platform's validation"
    fi
  done
done

# The crunch-week variant (STORY-atlas-crunch-mode): production deploys after the checks without
# the staging steps, and with backup.policy per_deploy a backup step runs before the production
# deploy. It renders different YAML, so it is validated the same way, per trigger and platform.
crunch="$WORK/crunch.json"
cat > "$crunch" <<'JSON'
{"starts_at": "2026-10-27T08:00:00+00:00", "ends_at": "2026-11-03T08:00:00+00:00",
 "backup": {"policy": "per_deploy", "retention_days": 14},
 "skip_envs": ["stst", "atst", "preprod"]}
JSON
for trigger in on_merge on_tag manual scheduled; do
  answers="$WORK/answers-$trigger.yaml"
  for platform in github gitlab azure; do
    out="$WORK/crunch-$trigger/$platform"
    if ! (cd "$ROOT/backend" && "$PY" admin.py --actor cicd-lint cicd generate \
            --platform "$platform" --matrix data/guardrails/example-default.yaml \
            --answers "$answers" --crunch "$crunch" --out "$out" >"$WORK/gen.log" 2>&1); then
      cat "$WORK/gen.log"; bad "$platform/$trigger crunch: generate failed"; continue
    fi
    f="$out/${FILE[$platform]}"
    if ! grep -q "atlas-admin backup run --reason per_deploy" "$f"; then
      bad "$platform/$trigger crunch: the per_deploy backup step is missing"
    elif "${LINT[$platform]}" "$f"; then
      ok "$platform/$trigger crunch: ${FILE[$platform]} passes the platform's validation"
    else
      bad "$platform/$trigger crunch: ${FILE[$platform]} fails the platform's validation"
    fi
  done
done

# Negative controls: one misspelled key per platform must be refused.
neg="$WORK/neg"; mkdir -p "$neg"
src="$WORK/on_merge"
sed 's/^    runs-on: ubuntu-latest$/    runs-onn: ubuntu-latest/' "$src/github/${FILE[github]}" > "$neg/gh.yml"
sed 's/^  stage: check$/  stage: check\n  scirpt: [x]/' "$src/gitlab/${FILE[gitlab]}" > "$neg/gl.yml"
sed 's/^  - stage: checks$/  - stage: checks\n    dependsOnn: nothing/' "$src/azure/${FILE[azure]}" > "$neg/az.yml"
for pair in "github:gh" "gitlab:gl" "azure:az"; do
  platform="${pair%%:*}"; f="$neg/${pair##*:}.yml"
  if cmp -s "$f" "$src/$platform/${FILE[$platform]}"; then
    bad "$platform: the negative control was not perturbed"
  elif "${LINT[$platform]}" "$f" >/dev/null 2>&1; then
    bad "$platform: the validator accepted a misspelled key (it proves nothing)"
  else
    ok "$platform: the validator refuses a misspelled key (negative control)"
  fi
done

exit "$fail"
