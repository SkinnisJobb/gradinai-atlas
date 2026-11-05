#!/usr/bin/env bash
# Lint the OpenShift manifests (TASK-1487, ARO 6.1; design: docs/oss/research/openshift-aro.md,
# question 11 and story 6).
#
# WHAT IT LINTS. Every Kustomize target under ROOT (default deploy/openshift), found rather than
# listed, so a new component or overlay is linted the day it lands:
#   base/                  built on its own;
#   components/<name>/     each built as base/ plus that one component (a Component cannot be
#                          built alone, and two components may be alternatives, such as cnpg and
#                          cnpg-objectstore-plugin, so they are not all stacked at once);
#   overlays/<name>/       each built as it is.
# A kustomization anywhere else under ROOT fails the run: it would not be linted, so it must be
# placed in that layout or added here. A ROOT that is absent, or holds no kustomization, passes
# with a note (the manifests arrive in later tasks; this check lands first).
#
# EACH RENDER then goes through:
#   kubeconform  strict mode, Kubernetes K8S_VERSION (OpenShift 4.20 is Kubernetes 1.33), the
#                OpenShift schemas (Route) and the CRDs catalog (CloudNativePG); a kind with no
#                schema is an error, not a skip. Both schema sources are pinned to a commit.
#   kube-linter  its default checks (tools/ci/openshift/kube-linter.yaml).
#   conftest     Atlas's policies (tools/ci/openshift/policy/): namespace `main` on every render,
#                and namespace `base` (no pinned runAsUser) on the base and component renders only.
#                The kustomization files themselves go through namespace `kustomization` (every
#                `images:` entry carries a digest).
#
# --self-test proves the tools and the policies refuse what they must: the fixture tree under
# fixtures/tree/ (base, a component, an overlay) passes the whole chain; every file in
# fixtures/conftest-bad/ fails conftest with exactly the rule its name starts with, and every
# policy rule has at least one such file; every file in fixtures/kubeconform-bad/ fails
# kubeconform; kube-linter fails a privileged container; an absent or empty tree passes; a
# kustomization outside the layout fails.
#
# Tools come from the environment: KUSTOMIZE, KUBECONFORM, KUBE_LINTER, CONFTEST (default: on
# PATH). .github/workflows/openshift-lint.yml pins and checksum-verifies them.
#
# Usage:
#   tools/ci/openshift/lint.sh [ROOT]
#   tools/ci/openshift/lint.sh --self-test
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
POLICY="$HERE/policy"
FIXTURES="$HERE/fixtures"

KUSTOMIZE="${KUSTOMIZE:-kustomize}"
KUBECONFORM="${KUBECONFORM:-kubeconform}"
KUBE_LINTER="${KUBE_LINTER:-kube-linter}"
CONFTEST="${CONFTEST:-conftest}"
K8S_VERSION="${K8S_VERSION:-1.33.0}"

# Schema sources, each pinned to a commit so a moved branch cannot change what passes.
# yannh/kubernetes-json-schema: the Kubernetes types. datreeio/CRDs-catalog: the OpenShift types
# (openshift/v4.15-strict, its newest OpenShift set; Route v1 has not changed since) and the
# CRDs by group (postgresql.cnpg.io, barmancloud.cnpg.io, external-secrets.io).
K8S_SCHEMAS_SHA=102361970337d65537d40141dc29b991b8e95786
CRDS_CATALOG_SHA=c86448cbf1a840ca62d119259864863897c8748a
SCHEMA_LOCATIONS=(
  "https://raw.githubusercontent.com/yannh/kubernetes-json-schema/${K8S_SCHEMAS_SHA}/{{.NormalizedKubernetesVersion}}-standalone{{.StrictSuffix}}/{{.ResourceKind}}{{.KindSuffix}}.json"
  "https://raw.githubusercontent.com/datreeio/CRDs-catalog/${CRDS_CATALOG_SHA}/openshift/v4.15-strict/{{.ResourceKind}}_{{.Group}}_{{.ResourceAPIVersion}}.json"
  "https://raw.githubusercontent.com/datreeio/CRDs-catalog/${CRDS_CATALOG_SHA}/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
)

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
CACHE="$WORK/schema-cache"
mkdir -p "$CACHE"

kubeconform_run() {
  local args=(-strict -summary -output text -kubernetes-version "$K8S_VERSION" -cache "$CACHE")
  local loc
  for loc in "${SCHEMA_LOCATIONS[@]}"; do args+=(-schema-location "$loc"); done
  "$KUBECONFORM" "${args[@]}" "$@"
}

has_kustomization() {
  [ -f "$1/kustomization.yaml" ] || [ -f "$1/kustomization.yml" ] || [ -f "$1/Kustomization" ]
}

# lint ROOT: build every target under ROOT and lint each render. Exit 1 on any finding. Every
# failure returns explicitly: the self-test calls this under `if`, where `set -e` is off.
lint() {
  local root="$1" out="$WORK/render-$RANDOM"
  mkdir -p "$out"
  if [ ! -d "$root" ]; then
    echo "lint: $root does not exist; nothing to lint"
    return 0
  fi
  root="$(cd "$root" && pwd)"

  # Find every kustomization and refuse one outside the layout.
  local found=0 stray=0 f dir rel kfiles=()
  while IFS= read -r f; do
    found=1
    kfiles+=("$f")
    dir="$(dirname "$f")"
    rel="${dir#"$root"/}"
    case "$rel" in
      base | components/* | overlays/*)
        case "$rel" in */*/*) ;; *) continue ;; esac ;;
    esac
    echo "lint: FAIL $rel has a kustomization outside base/, components/<name>/ and overlays/<name>/; it would not be linted" >&2
    stray=1
  done < <(find "$root" \( -name kustomization.yaml -o -name kustomization.yml -o -name Kustomization \) -type f | sort)
  [ "$stray" -eq 0 ] || return 1
  if [ "$found" -eq 0 ]; then
    echo "lint: $root holds no kustomization; nothing to lint"
    return 0
  fi

  # Build. Each render is "<label>.yaml"; the base-only rules apply to the names in base_labels.
  local labels=() base_labels=() name d
  if has_kustomization "$root/base"; then
    echo "lint: kustomize build base"
    "$KUSTOMIZE" build "$root/base" > "$out/base.yaml" || return 1
    labels+=(base); base_labels+=(base)
  fi
  for d in "$root"/components/*/; do
    [ -d "$d" ] && has_kustomization "$d" || continue
    name="$(basename "$d")"
    if ! has_kustomization "$root/base"; then
      echo "lint: FAIL component $name exists but base/ does not; a component is built on the base" >&2
      return 1
    fi
    mkdir -p "$WORK/component-$name"
    # Kustomize refuses an absolute path in a kustomization, so the generated one points back
    # with relative ones.
    printf 'apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n  - %s\ncomponents:\n  - %s\n' \
      "$(realpath --relative-to="$WORK/component-$name" "$root/base")" \
      "$(realpath --relative-to="$WORK/component-$name" "${d%/}")" \
      > "$WORK/component-$name/kustomization.yaml"
    echo "lint: kustomize build base + component $name"
    "$KUSTOMIZE" build "$WORK/component-$name" > "$out/component-$name.yaml" || return 1
    labels+=("component-$name"); base_labels+=("component-$name")
  done
  for d in "$root"/overlays/*/; do
    [ -d "$d" ] && has_kustomization "$d" || continue
    name="$(basename "$d")"
    echo "lint: kustomize build overlay $name"
    "$KUSTOMIZE" build "$d" > "$out/overlay-$name.yaml" || return 1
    labels+=("overlay-$name")
  done

  local rc=0 l ns
  # The kustomization files themselves: an `images:` entry must carry a digest (namespace
  # `kustomization`). The render check would miss one that matches no container today.
  echo "--- conftest (${#kfiles[@]} kustomization file(s): --namespace kustomization)"
  "$CONFTEST" test --no-color --parser yaml --policy "$POLICY" --namespace kustomization \
    "${kfiles[@]}" || rc=1
  for l in "${labels[@]}"; do
    echo "::group::lint $l"
    echo "--- kubeconform ($l)"
    kubeconform_run "$out/$l.yaml" || rc=1
    echo "--- kube-linter ($l)"
    "$KUBE_LINTER" lint --config "$HERE/kube-linter.yaml" "$out/$l.yaml" || rc=1
    ns=(--namespace main)
    case " ${base_labels[*]} " in *" $l "*) ns+=(--namespace base) ;; esac
    echo "--- conftest ($l: ${ns[*]})"
    "$CONFTEST" test --no-color --policy "$POLICY" "${ns[@]}" "$out/$l.yaml" || rc=1
    echo "::endgroup::"
  done
  if [ "$rc" -ne 0 ]; then
    echo "lint: FAIL (see above)" >&2
    return 1
  fi
  echo "lint: ok (${#labels[@]} render(s): ${labels[*]})"
}

# other_rule_failures RULE OUT: the FAIL lines of conftest output OUT that are not RULE's. One awk
# over a here-string, never `grep "^FAIL" | grep -qv`: under pipefail grep -q exits at its first
# hit, the grep before it dies of SIGPIPE writing the rest, and the pipeline's status turns the hit
# into a miss, so a fixture that also failed a second rule passed (TASK-1504).
other_rule_failures() {
  awk -v tag="[$1]" '/^FAIL/ && index($0, tag) == 0' <<< "$2"
}

self_test() {
  local fails=0 f rule out rc rules r big verdict
  fail() { echo "SELF-TEST FAIL: $*" >&2; fails=$((fails + 1)); }

  echo "== the one-rule check sees a second rule (a planted FAIL line ahead of ~1 MB of output)"
  big="FAIL - planted.yaml - main - [other-rule] planted"$'\n'"$(for _ in $(seq 1 12000); do
    echo "FAIL - planted.yaml - main - [some-rule] the fixture's own rule, repeated to fill the pipe"
  done)"
  if [ -n "$(other_rule_failures some-rule "$big")" ]; then
    echo "ok   the planted [other-rule] line is reported"
  else
    fail "other_rule_failures missed a planted [other-rule] line"
  fi
  if [ -n "$(other_rule_failures some-rule "${big#*$'\n'}")" ]; then
    fail "other_rule_failures reported a line of the fixture's own rule"
  fi
  # For the record only (its outcome is a race): the old pipeline on the same output.
  if grep "^FAIL" <<< "$big" | grep -qv "\[some-rule\]"; then verdict=caught; else verdict=missed; fi
  echo "     (for the record, the old grep | grep -qv on that output $verdict it)"

  echo "== the fixture tree passes the whole chain"
  lint "$FIXTURES/tree" || fail "fixtures/tree did not pass"

  echo "== an absent tree and an empty tree pass"
  lint "$WORK/absent" > /dev/null || fail "an absent root failed"
  mkdir -p "$WORK/empty/base"
  lint "$WORK/empty" > /dev/null || fail "an empty root failed"

  echo "== a kustomization outside the layout fails"
  mkdir -p "$WORK/stray/extra"
  cp -r "$FIXTURES/tree/base" "$WORK/stray/base"
  printf 'resources: []\n' > "$WORK/stray/extra/kustomization.yaml"
  if lint "$WORK/stray" > /dev/null 2>&1; then fail "a stray kustomization passed"; fi

  echo "== each conftest-bad fixture fails with exactly its own rule"
  for f in "$FIXTURES"/conftest-bad/*.yaml; do
    rule="$(basename "$f")"; rule="${rule%%--*}"
    set +e
    out="$("$CONFTEST" test --no-color --policy "$POLICY" --all-namespaces "$f" 2>&1)"
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then fail "$(basename "$f") passed conftest"; continue; fi
    if ! grep -q "^FAIL .*\[$rule\]" <<< "$out"; then
      fail "$(basename "$f") did not fail [$rule]:"$'\n'"$out"; continue
    fi
    if [ -n "$(other_rule_failures "$rule" "$out")" ]; then
      fail "$(basename "$f") also failed another rule:"$'\n'"$out"; continue
    fi
    echo "ok   $(basename "$f") -> [$rule]"
  done

  echo "== every policy rule has a failing fixture"
  rules="$(grep -ho '"\[[a-z-]*\]' "$POLICY"/*.rego | tr -d '"[]' | sort -u)"
  [ -n "$rules" ] || fail "no rule ids found in $POLICY"
  for r in $rules; do
    if compgen -G "$FIXTURES/conftest-bad/$r--*.yaml" > /dev/null; then
      echo "ok   [$r] has a fixture"
    else
      fail "rule [$r] has no fixture in fixtures/conftest-bad/"
    fi
  done

  echo "== each kubeconform-bad fixture fails kubeconform"
  for f in "$FIXTURES"/kubeconform-bad/*.yaml; do
    if kubeconform_run "$f" > "$WORK/kc.out" 2>&1; then
      fail "$(basename "$f") passed kubeconform:"$'\n'"$(cat "$WORK/kc.out")"
    else
      echo "ok   $(basename "$f") refused: $(grep -m1 -E 'is invalid|could not find schema' "$WORK/kc.out" || head -1 "$WORK/kc.out")"
    fi
  done

  echo "== kube-linter refuses a privileged container"
  if "$KUBE_LINTER" lint --config "$HERE/kube-linter.yaml" \
      "$FIXTURES/conftest-bad/security-context--privileged.yaml" > "$WORK/kl.out" 2>&1; then
    fail "kube-linter passed a privileged container"
  elif ! grep -q privileged-container "$WORK/kl.out"; then
    fail "kube-linter failed, but not on privileged-container:"$'\n'"$(cat "$WORK/kl.out")"
  else
    echo "ok   privileged-container"
  fi

  echo "self-test: $([ "$fails" -eq 0 ] && echo ok || echo FAILED) ($fails failure(s))"
  [ "$fails" -eq 0 ]
}

case "${1:-}" in
  --self-test) self_test ;;
  -h | --help) sed -n '2,/^set -euo/p' "$0" | sed '$d' ;;
  "") lint "$REPO/deploy/openshift" ;;
  *) lint "$1" ;;
esac
