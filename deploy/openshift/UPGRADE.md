# Atlas on OpenShift: upgrading to a new release

For an install made from these manifests ([FIRST-INSTALL.md](FIRST-INSTALL.md), or your own
overlay on `deploy/openshift/base`). Read this page on the default branch: step 2 checks out the
release you upgrade to, which may predate it. PowerShell 7, from a checkout of this repository, with `oc`
logged in to the cluster.

**There is nothing to build.** A release's images are built, scanned and published when it is
tagged. An upgrade points your overlay at the new release's images, applies it, and lets the
migrate Job bring the schema up to date. Schema changes are additive, the Job is idempotent, and
an `atlas-api` pod that starts at the same time waits for it on a database lock.

## What changes, and where

| What | Where | Change to |
|---|---|---|
| The checkout | your clone of this repository | the new release's tag, so `../../base` is the new base |
| The three images | your overlay's `kustomization.yaml`, `images:` | the new release's `-ubi` images, by tag and digest |
| The version label | the same file, `labels:` | the new tag |
| The migrate Job's name | the same file, `patches:` | `atlas-migrate-<new tag, dots as dashes>` (a Job cannot be changed in place, so each release is a new Job) |
| Images in your own registry | your registry (path `mirror` only) | a copy of the three new images, by digest |

Nothing else in the overlay changes: host, settings, database, NetworkPolicies and patches stay
as they are. The database image changes only when the release notes say so.

## Before you start

1. **Read the release notes** for every release between yours and the new one
   ([RELEASES.md](../../docs/oss/RELEASES.md#release-notes), or the GitHub release). They say
   whether there is a new setting or a one-time step.
2. **Back up the database** if your install has backups set up
   ([README, "Backups need one more policy"](README.md#backups-need-one-more-policy)).
3. **Set the values** for this upgrade. Run the block in every new terminal.

```powershell
$repo     = 'C:\src\gradinai-atlas'                       # your checkout of this repository
$overlay  = "$repo\deploy\openshift\overlays\ours"        # your overlay folder
$ns       = 'atlas'                                       # the project (namespace)
$new      = 'v0.0.3-alpha'                                # the release you upgrade to
$owner    = 'change-me-owner'                             # GitHub account of the Atlas repository, lower case
$registry = "ghcr.io/$owner"                              # or your own registry and path (path mirror)
$job      = 'atlas-migrate-' + ($new -replace '\.', '-')
$work     = "$HOME\atlas-upgrade-$new"; New-Item -ItemType Directory -Force $work | Out-Null
```

## 1. Record what runs now

```powershell
$cols = 'KIND:.kind,NAME:.metadata.name,VERSION:.metadata.labels.app\.kubernetes\.io/version,IMAGES:.spec.template.spec.containers[*].image'
oc get deploy -n $ns -l app.kubernetes.io/name=atlas -o custom-columns=$cols
$old = oc get deploy atlas-api -n $ns -o jsonpath='{.metadata.labels.app\.kubernetes\.io/version}'
Copy-Item -Recurse $overlay "$work\overlay-$old"
"running $old, upgrading to $new"
```

**Expected:** `atlas-api`, `atlas-mcp` and `atlas-tls`, all with the same version and images by
digest; then `running <old tag>, upgrading to <new tag>`. The copy in
`$work` is the way back.

## 2. Check out the new release

```powershell
git -C $repo fetch --tags
git -C $repo switch --detach $new
Test-Path "$overlay\kustomization.yaml"
```

**Expected:** `HEAD is now at ...`, then `True`. An overlay under `overlays\ours` is ignored by
git and stays as it is. **If not:** `False`: restore it with
`Copy-Item -Recurse "$work\overlay-$old" $overlay`.

## 3. Get the new images

The release lists its images by tag and digest in `release-images.env`:

```powershell
$env_ = curl.exe -fsSL "https://github.com/$owner/gradinai-atlas/releases/download/$new/release-images.env"
$ref = @{}
foreach ($n in 'api', 'mcp', 'tls') { $ref[$n] = ($env_ | Select-String "^ATLAS_IMAGE_$($n.ToUpper())_UBI=(\S+)").Matches[0].Groups[1].Value; $ref[$n] }
```

**Expected:** three lines, `ghcr.io/<owner>/gradinai-atlas-{api,mcp,tls}-ubi:<new>@sha256:...`.
**If not:** `404`: `$owner` or `$new` is wrong.

**Path: mirror** (skip for ghcr.io). Copy the three images to your registry by digest, with the
credentials file from FIRST-INSTALL step 7:

```powershell
foreach ($n in 'api', 'mcp', 'tls') {
  $src, $digest = $ref[$n] -split '@'
  oc image mirror -a "$HOME\atlas-install\registry-auth.json" --keep-manifest-list=true "$($src -replace ':[^:/]+$', '')@$digest" "$registry/gradinai-atlas-$n-ubi:$new"
}
```

**Expected:** each copy ends with the same `sha256:` as in `$ref`. **If not:** see
[FIRST-INSTALL step 7](FIRST-INSTALL.md#7-check-that-the-cluster-can-pull-the-images).

## 4. Change the overlay

This rewrites the `images:` block, the version label and the migrate Job's name. The `images:`
block is rewritten whole, because the base's image names can change between releases (they
became `-ubi` in `v0.0.3-alpha`), and an entry whose `name:` no longer matches the base is
silently ignored.

```powershell
$k = Get-Content -Raw "$overlay\kustomization.yaml"
if ($k -notmatch '(?m)^images:') { throw 'the overlay has no images: block' }
$images = foreach ($n in 'api', 'mcp', 'tls') {
  "  - name: registry.example.com/gradinai-atlas/gradinai-atlas-$n-ubi`n" +
  "    newName: $registry/gradinai-atlas-$n-ubi`n    newTag: $new`n    digest: $(($ref[$n] -split '@')[1])"
}
$k = [regex]::Replace($k, '(?ms)^images:\r?\n.*?(?=^\S)', "images:`n$($images -join "`n")`n`n")
$k = [regex]::Replace($k, 'app\.kubernetes\.io/version: v[^\s}]+', "app.kubernetes.io/version: $new")
$baseJob = (Select-String -Path "$repo\deploy\openshift\base\job-migrate.yaml" -Pattern '^\s+name: (atlas-migrate-\S+)').Matches[0].Groups[1].Value
if ($k -match 'value: atlas-migrate-') {
  $k = [regex]::Replace($k, '(name: )atlas-migrate-[^\s}]+', "`${1}$baseJob")
  $k = [regex]::Replace($k, '(value: )atlas-migrate-[^\s}]+', "`${1}$job")
} elseif ($baseJob -ne $job) {
  $patch = "  - target: {kind: Job, name: $baseJob}`n    patch: |-`n      - {op: replace, path: /metadata/name, value: $job}`n"
  if ($k -match '(?m)^patches:') { $k = ([regex]'(?m)^patches:\r?\n').Replace($k, "patches:`n$patch", 1) } else { $k += "`npatches:`n$patch" }
}
Set-Content "$overlay\kustomization.yaml" $k -NoNewline
git diff --no-index "$work\overlay-$old\kustomization.yaml" "$overlay\kustomization.yaml"
```

**Expected:** a diff with only the `images:` entries, the version and the Job name (or a Job
patch added). **If not:** `the overlay has no images: block`: your overlay names its images some
other way; set the three entries by hand as shown in
[README, "Images and releases"](README.md#images-and-releases).

## 5. Render it and compare with what runs

```powershell
oc kustomize $overlay | Set-Content "$work\rendered.yaml"
Select-String -Path "$work\rendered.yaml" -Pattern '^\s+(- )?(image|imageName): (\S+)' | ForEach-Object { $_.Matches[0].Groups[3].Value } | Sort-Object -Unique
Select-String -Path "$work\rendered.yaml" -Pattern "^  name: $job$" | Measure-Object | Select-Object -ExpandProperty Count
oc diff -k $overlay | Set-Content "$work\diff.txt"; Get-Content "$work\diff.txt" | Select-String '^[-+]\s+(image|app\.kubernetes\.io/version|name: atlas-migrate)'
```

**Expected:** the image lines are your three new images, each ending in `@sha256:` and the
digest, plus the database image (unchanged); **no** `registry.example.com`. The Job count is
`1`. The diff shows the images and the version moving in the three Deployments and the CronJob,
and the new Job; `$work\diff.txt` has the whole diff for your log. **If not:** a
`registry.example.com` image: an `images:` entry does not match the base; check step 4's
diff. Count `0`: the Job patch; check step 4's diff.

## 6. Apply

```powershell
oc apply -k $overlay
```

**Expected:** `configured` for the three Deployments, the CronJob and the labelled objects,
`created` for `job.batch/<$job>`, `unchanged` for the rest. **If not:** `field is immutable` on
the Job: its name did not change; step 4.

## 7. Wait for the migration and the new pods

```powershell
oc wait job/$job -n $ns --for=condition=complete --timeout=10m
oc logs -n $ns (oc get pod -n $ns -l job-name=$job --field-selector=status.phase=Succeeded -o name) -c atlas-migrate --tail=1
oc rollout status deploy/atlas-api -n $ns --timeout=10m
oc rollout status deploy/atlas-mcp -n $ns --timeout=5m
oc rollout status deploy/atlas-tls -n $ns --timeout=5m
```

**Expected:** `condition met`; a last log line starting `[atlas-migrate] upgrade:`; each
Deployment `successfully rolled out`. **If not:** the troubleshooting rows of
[FIRST-INSTALL](FIRST-INSTALL.md#appendix-b-troubleshooting): T10 for the Job, T3 for
`ImagePullBackOff`.

## 8. Check it

```powershell
oc get deploy -n $ns -l app.kubernetes.io/name=atlas -o custom-columns=$cols
oc get cronjob atlas-audit-purge -n $ns -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[*].image}{"
"}'
$atlasHost = oc get route atlas -n $ns -o jsonpath='{.spec.host}'
curl.exe -sS -w "`n" "https://$atlasHost/healthz"
```

**Expected:** every Deployment shows `$new` and the new digests, and so does the CronJob's image; `{"ok":true}`. Then sign in in the
browser: the board loads, and your work is there.

The old Job stays for seven days, for its log, and then removes itself. `oc apply` does not
delete objects a new release no longer has; such leftovers are named under "For this release"
below.

## If it goes wrong: back to the old release

The schema change is additive, so the old release runs on the upgraded database.

```powershell
git -C $repo switch --detach $old
Remove-Item -Recurse $overlay; Copy-Item -Recurse "$work\overlay-$old" $overlay
oc apply -k $overlay
oc rollout status deploy/atlas-api -n $ns --timeout=10m
```

Then tell the Atlas maintainers what step 7 printed.

## For this release

**`v0.0.2-alpha` or older to `v0.0.3-alpha`:**

- The base's images are now the `-ubi` ones (Red Hat UBI 9). An install made from the
  `v0.0.2-alpha` tag ran the `v0.0.1-alpha` Debian images (`gradinai-atlas-api`, no `-ubi`);
  step 1 shows which. Step 4 rewrites the `images:` block, so either way the overlay ends up
  right.
- `atlas-api`, the migrate Job and the audit CronJob no longer have the `db-url` init container:
  the image reads CloudNativePG's database URL itself. The base still has an `atlas-scripts`
  ConfigMap (the `https_probe.py` that `atlas-api` and `atlas-mcp` mount), now under a new hash;
  the old `atlas-scripts-<hash>` that also held `db_url.py` is left behind. When the upgrade is
  checked, delete the old one only, never the one the Deployments use:

  ```powershell
  $current = oc get deploy atlas-api -n $ns -o jsonpath='{.spec.template.spec.volumes[?(@.name=="scripts")].configMap.name}'
  if (-not $current) { throw 'no scripts ConfigMap found on atlas-api; delete nothing' }
  oc get configmap -n $ns -o name | Select-String atlas-scripts | Where-Object { $_ -notmatch "/$current$" } | ForEach-Object { oc delete -n $ns $_ }
  ```

  **Expected:** one `configmap "atlas-scripts-<old hash>" deleted`, and `$current` still listed
  by `oc get configmap -n $ns`.
- The migrate Job retries up to six times (was one).
- The database's NetworkPolicies moved to their own component (`networkpolicies` includes it, so
  an existing overlay keeps both policies under the same names). `atlas-database` gains one
  egress rule, to the cluster DNS (`openshift-dns`, port 5353), so a standby finds its primary
  by service name; step 5's diff shows it.
- No new setting, no schema change that needs a step, and the database image is unchanged.
