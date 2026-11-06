# Atlas on OpenShift: a first install, step by step

This is the short path to a first real install of Atlas on Azure Red Hat OpenShift (ARO), so
that we learn what the cluster accepts before the full template is built. It uses the overlay in
[`overlays/example/`](overlays/example/kustomization.yaml) and assumes as little as possible: no
optional components, no backup, local accounts only, and Atlas's NetworkPolicies off unless the
project already has policies of its own (step 2).  Everything here is
written for **PowerShell 7 on Windows**, with `oc` already logged in to the cluster
(`oc login ...`) and a checkout of this repository as the current directory.

The full install guide (task 5.1) and the GitLab CI template (task 3.7, second step) come after
this has been tried. [`README.md`](README.md) is the map of the manifests; the design and its
reasons are in [Atlas on Azure Red Hat OpenShift](../../docs/oss/research/openshift-aro.md).

**This install is built on working assumptions** that the platform team has not confirmed yet.
Step 1 checks each one with `oc`; [the table at the end](#the-assumptions-and-what-to-change-if-one-is-wrong)
says what to change when one turns out wrong. Keep what each check printed and send it to the
Atlas maintainers: it answers the platform questions the full template is waiting for.

## What you need

- `oc` 4.20 or later, logged in. Kustomize is built into `oc`; no separate tool is needed.
- Python 3.10 or later on your machine (`python --version`; on some Windows machines the
  command is `py`). The two scripts below use only its standard library.
- A project (namespace) for Atlas in which you can create Deployments, Services, Routes,
  ConfigMaps, Secrets, Jobs, CronJobs and CloudNativePG `Cluster`s. Nothing cluster-wide.
- Room in the project's quota for about 2.3 CPU and 3.4 GiB of memory in requests (about 10 CPU
  and 7 GiB in limits, if the quota counts limits), counting one extra `atlas-api` pod during a
  rollout, and 50 GiB of persistent storage in 4 volumes: two database instances with 20 GiB of
  data and 5 GiB of WAL each (`components/cnpg/cluster.yaml`). Step 1 checks it, with the
  breakdown and how to make a trial smaller.
- Community CloudNativePG 1.25.3 or later on the 1.25 line, 1.26.1 or later, or any 1.27 or
  later. Step 1 checks it; an older operator needs one optional patch.

In the commands below, replace `atlas` with your project's name wherever `-n atlas` appears.

## 1. Check the cluster against the assumptions

```powershell
oc version
oc whoami
```

`oc version` prints the client and the server version: the server should be 4.20 or 4.21.

**Your rights in the project** (each answers `yes` or `no`):

```powershell
foreach ($r in 'deployments', 'jobs', 'cronjobs', 'services', 'routes', 'configmaps', 'secrets', 'clusters.postgresql.cnpg.io', 'databases.postgresql.cnpg.io') {
  "{0,-32} {1}" -f $r, (oc auth can-i create $r -n atlas)
}
```

**The CloudNativePG operator and its API group.** The overlay writes the community API group
`postgresql.cnpg.io`:

```powershell
oc api-resources --api-group=postgresql.cnpg.io
oc api-resources --api-group=postgresql.k8s.enterprisedb.io
```

The first should list `clusters` and `databases`. If you may read CRDs, the same answer
comes from `oc get crd clusters.postgresql.cnpg.io databases.postgresql.cnpg.io` (or the
`postgresql.k8s.enterprisedb.io` names for EDB); `oc api-resources` needs no extra right. If only the second lists anything, the
cluster runs EDB Postgres for Kubernetes: see assumption 6. If `clusters` is there but
`databases` is not, the operator is older than 1.25: see assumption 6 too.

**The CloudNativePG version.** The base reads the database URL from the key `fqdn-uri` of the
Secret `atlas-db-app` that the operator writes, and the `cnpg` component creates a `Database`
resource. Community CloudNativePG writes `fqdn-uri` from 1.25.3 (1.25 line), 1.26.1 (1.26 line)
and every 1.27 or later; it has the `Database` resource from 1.25.0. The `databases` line of the
`oc api-resources` check above answers the second. The operator's version, if you may read the
operator's namespace (either may be refused with project rights only; then ask the platform
team):

```powershell
oc get csv -n openshift-operators | Select-String -Pattern 'cloudnative-pg|cloud-native-postgresql'   # installed from OperatorHub: the version is in the name
oc get deploy -n cnpg-system -o jsonpath='{.items[*].spec.template.spec.containers[*].image}'        # installed from the manifest or Helm: the image tag
```

The final answer comes after step 6, once the Cluster is up: the Secret's key names (never the
values) must include `fqdn-uri`:

```powershell
(oc get secret atlas-db-app -n atlas -o json | ConvertFrom-Json).data.PSObject.Properties.Name
```

**An older operator** (no `fqdn-uri`, or no `databases`): in step 5, in your overlay's
`kustomization.yaml`, uncomment the two entries under `OLDER CLOUDNATIVEPG` at the end of
`patches:`. They make `atlas-api`, the migrate Job and the audit CronJob read the Secret's `uri`
key instead (its host `atlas-db-rw.<project>` is on the operator's server certificate, so the
connection is still verified in full), and leave out the `Database` resource (the `atlas`
database is still created with the Cluster). `overlays/example/patch-older-cnpg.yaml` explains
each change. Found out only after step 6 (pods waiting in `ContainerCreating`): enable them,
`oc apply -k` again, and delete the waiting pods.

**The quota and limits of the project:**

```powershell
oc describe quota -n atlas
oc describe limitrange -n atlas
```

No output means no quota or no LimitRange. Otherwise compare the `Hard` column with what Atlas
asks for (from the manifests; a pod's request is that of its main container, the init containers
being smaller):

| Part | Pods | CPU request (limit) per pod | Memory request (limit) per pod | Storage |
|---|---|---|---|---|
| atlas-api | 2 (3 during a rollout) | 250m (1) | 256Mi (512Mi) | |
| atlas-tls | 2 | 50m (500m) | 64Mi (192Mi) | |
| atlas-mcp | 1 | 100m (500m) | 128Mi (256Mi) | |
| database (`atlas-db`) | 2 | 500m (2) | 1Gi (2Gi) | 20Gi data + 5Gi WAL each |
| migrate Job, while it runs | 1 | 250m (1) | 256Mi (512Mi) | |
| audit purge CronJob, while it runs | 1 | 50m (250m) | 128Mi (256Mi) | |
| **Running** | 7 | **1.7 CPU (7.5)** | **2.75Gi (5.6Gi)** | **50Gi in 4 volumes** |
| **At most** (a rollout, the Job and the CronJob at once) | 10 | **2.25 CPU (9.75)** | **3.4Gi (6.9Gi)** | 50Gi |

The quota may also count pods, `persistentvolumeclaims` (4) and `requests.storage` (50Gi), or
storage per class. A LimitRange matters if its `Max` is below a limit above, or its `Min` above a
request. **Too small for a trial:** in your overlay, `replicas: 1` on `atlas-api` and on
`atlas-tls` and delete their PodDisruptionBudgets (both named like the Deployment), and on
`Cluster` `atlas-db` `spec.instances: 1` (no standby), smaller `spec.storage.size` and
`spec.walStorage.size` (say 5Gi and 2Gi) and smaller `spec.resources`. That runs in about
0.65 CPU, 1Gi of memory and 7Gi of storage in requests. The patches are below, under
[The patches the table names](#the-patches-the-table-names) (assumption 3). Volume sizes are
fixed once the Cluster has made its volumes: shrink before the first apply.

**The storage class.** The database volumes use the cluster's default class:

```powershell
oc get storageclass
```

One line should say `(default)` (on ARO usually `managed-csi`). If the command is refused,
ask the platform team which class is the default.

**The pod network** (for step 5, values 4 and 4b):

```powershell
oc get network.config cluster -o jsonpath='{.spec.clusterNetwork[*].cidr}'
```

**The apps domain** (for step 5): the Route's host is one label under it.

```powershell
oc get ingresses.config cluster -o jsonpath='{.spec.domain}'
```

**Can the cluster pull from ghcr.io?** Start one throwaway pod from the released API image,
with `<owner>` the GitHub account the Atlas repository belongs to, in lower case
(`github.com/<Owner>/gradinai-atlas` gives `ghcr.io/<owner>/...`):

```powershell
oc run atlas-pull-test -n atlas --restart=Never --image=ghcr.io/<owner>/gradinai-atlas-api:v0.0.1-alpha@sha256:fe919e98baf5375eeee650e746d98d4a240e470da11f29b37f2690e548d8afb9 --command -- python -c "print('pulled')"
oc get pod atlas-pull-test -n atlas -w     # Ctrl+C once it says Completed or an error
oc logs atlas-pull-test -n atlas           # prints: pulled
oc delete pod atlas-pull-test -n atlas
```

`ImagePullBackOff` or `ErrImagePull` here means assumption 1 is wrong: read the pod's events
(`oc describe pod atlas-pull-test -n atlas`), then follow [Your own registry](#your-own-registry)
for steps 4 and 5. A warning about Pod Security when the pod is created is expected and harmless
for this test pod. The database image comes from ghcr.io too (`ghcr.io/cloudnative-pg/postgresql`),
so the same answer applies to it.

**The router's namespace label** (for Atlas's NetworkPolicies, step 5):

```powershell
oc get namespace openshift-ingress --show-labels
```

The `LABELS` column must contain the key `policy-group.network.openshift.io/ingress`, followed by
`=` and any value or none (`...ingress=` and `...ingress=ingress` are both fine). Atlas's
NetworkPolicies admit the router by that key alone, whatever its value. If the command is
refused, ask the platform team for the labels of the router's namespace. If the key is missing,
tell the Atlas maintainers before you switch the NetworkPolicies on: the Route would answer 503.

## 2. The project

If the platform team has not made it for you and you are allowed to make projects:

```powershell
oc new-project atlas
```

Otherwise use the one you were given. Every later command names it with `-n atlas`.

**Did the project come with NetworkPolicies?** Many clusters create every project from a template
that adds some:

```powershell
oc get networkpolicy -n atlas
```

If this lists anything (typically `allow-from-openshift-ingress`, `allow-from-openshift-monitoring`,
`allow-same-namespace`, sometimes a `deny-all`), switch on Atlas's own NetworkPolicies in step 5:
without them the CloudNativePG operator cannot reach the database pods, and the database never
becomes healthy. Keep the list for the platform team questions in
[`README.md`](README.md#next-to-the-project-templates-own-policies). If it lists nothing, leave
them off for this first install, or switch them on anyway: they work either way.

## 3. The peppers

Atlas has two peppers, random keys mixed into every password and every agent token. They are
made once and **never change for the life of the install**: new peppers sign everybody out and
make every token useless, and a database restored without its peppers cannot be signed in to.
[OpenShift: creating the peppers and backing them up](../../docs/oss/guides/openshift-secrets.md)
is the full page; the short form, from the repository root:

```powershell
New-Item -ItemType Directory -Force secrets\openshift | Out-Null
icacls secrets\openshift /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F"
python scripts/openshift/new-secrets.py -n atlas --dry-run
python scripts/openshift/new-secrets.py -n atlas
python scripts/openshift/pepper-fingerprint.py secrets/openshift/password_pepper secrets/openshift/token_pepper > pepper-fingerprints.txt
```

The `icacls` line makes the directory readable by you alone (on Windows the file modes the
script sets mean nothing; the ACL does). `--dry-run` shows the `oc` commands and changes
nothing; the next line writes the two pepper files and creates the Secret `atlas-peppers`. No
value is printed. `pepper-fingerprints.txt` holds one SHA-256 fingerprint per pepper, never the
value: keep it with your notes of this install.

**Back the peppers up before anyone stores real work in this install**: to Azure Key Vault as
the full page shows, or, for a throwaway test install, at least keep `secrets\openshift\` where
only you can read it. `secrets/` is in `.gitignore`; never commit it.

## 4. Copy the overlay

Keep the example as it is and work on a copy next to it, so the relative paths to the base and
the components stay right:

```powershell
Copy-Item -Recurse deploy\openshift\overlays\example deploy\openshift\overlays\ours
```

(In the full template the overlay will live in your own GitLab repository and point at this one
by release; for a first try a copy in the checkout is enough.)

## 5. Fill in the values

Every value to change says `change-me`. All of them are in `deploy\openshift\overlays\ours\`:

| # | File | What | Where it comes from |
|---|---|---|---|
| 1 | `kustomization.yaml` | `namespace: change-me-namespace` | your project, step 2 |
| 2 | `kustomization.yaml` | `change-me-owner` in the three `newName` lines | the GitHub account in lower case, as in the pull test. A cluster that cannot pull from ghcr.io: your registry's path instead ([Your own registry](#your-own-registry)) |
| 3 | `atlas.env` | `atlas.change-me.example.com`, four times | `atlas.` followed by the apps domain from step 1, for example `atlas.apps.<cluster domain>` |
| 4 | `patch-trusted-proxy.yaml` (atlas-api) | `change-me-pod-network-cidr` | the pod network from step 1; several: comma-separated, no spaces |
| 4b | `patch-tls-trusted-proxy.yaml` (atlas-tls, Caddy) | `change-me-pod-network-cidr` | the same pod network; several: separated by spaces |

Both 4 and 4b are the pod network because both peers live on it: atlas-api believes the
client address only from atlas-tls, and atlas-tls believes the router's `X-Forwarded-For` only
from the router pods. If the platform team says the router runs on the host network, add the
machine network to 4b as well; if a gateway sits in front of the router, see assumption 5.

All of them can be replaced in one go; put your own values in the first two lines (the third
reads the pod network from the cluster):

```powershell
$owner = 'yourowner'                   # lower case
$hostname = 'atlas.apps.example.com'   # atlas.<apps domain>
$podnet = oc get network.config cluster -o jsonpath='{.spec.clusterNetwork[*].cidr}'
$dir = 'deploy\openshift\overlays\ours'
(Get-Content "$dir\kustomization.yaml") -replace 'change-me-owner', $owner | Set-Content "$dir\kustomization.yaml"
(Get-Content "$dir\atlas.env") -replace 'atlas\.change-me\.example\.com', $hostname | Set-Content "$dir\atlas.env"
(Get-Content "$dir\patch-trusted-proxy.yaml") -replace 'change-me-pod-network-cidr', ($podnet -replace ' ', ',') | Set-Content "$dir\patch-trusted-proxy.yaml"
(Get-Content "$dir\patch-tls-trusted-proxy.yaml") -replace 'change-me-pod-network-cidr', $podnet | Set-Content "$dir\patch-tls-trusted-proxy.yaml"
```

**NetworkPolicies** (if step 2 listed any, or to have them from the start): in
`kustomization.yaml`, remove the `#` in front of `- ../../components/networkpolicies` under
`components:`. If the CloudNativePG operator does not run in `cnpg-system` (an operator from
OperatorHub usually runs in `openshift-operators`; ask the platform team), add the patch from
[`README.md`, "The operator's namespace"](README.md#the-operators-namespace) under `patches:`.

Also read the two privacy lines and `ATLAS_SEED` in `atlas.env`. `ATLAS_SEED=demo` puts one
neutral example project on the board of the empty database, which is what you want for a first
try; set `none` before the first apply if this install is to hold real work from day one (the
seed is read once, at the first start, and changing it later does nothing).

The Route's host is copied from `ATLAS_PUBLIC_HOST` by the overlay, so you type it in
`atlas.env` only. Then check that nothing is left, and look at what will be applied:

```powershell
Select-String -Path deploy\openshift\overlays\ours\* -Pattern '^[^#]*change-me'   # must print nothing
oc kustomize deploy\openshift\overlays\ours | Select-String -Pattern 'image:|host:|namespace:' | Sort-Object -Unique
oc apply -k deploy\openshift\overlays\ours --dry-run=server
```

The server dry run sends everything to the API server without creating it: a missing right, an
unknown resource type or a quota refusal shows up here, before anything exists.

**Labels your organisation requires.** Some clusters refuse, by an admission policy, any object
or pod that lacks certain labels (an environment, a cost centre, an owner). If the dry run or the
apply is refused with a message naming a missing label, ask the platform team for the keys and
values, then in `kustomization.yaml` uncomment the block under `LABELS YOUR ORGANISATION REQUIRES`
in `labels:` and put them in place of the two examples. It labels every object and every pod
(the database pods too) and leaves the selectors alone.

## 6. Apply

```powershell
oc apply -k deploy\openshift\overlays\ours
```

This creates everything at once: the database `Cluster`, the ConfigMaps, the Services (whose
annotations make the service CA issue the internal TLS certificates), the migrate Job, the
Deployments `atlas-api`, `atlas-mcp` and `atlas-tls`, the audit purge CronJob and the Route.
Pods that need something not yet there (the database's Secrets, a TLS Secret) wait for it. Then
wait for each part, in order:

```powershell
oc wait clusters.postgresql.cnpg.io/atlas-db -n atlas --for=condition=Ready --timeout=15m
oc wait job/atlas-migrate-v0-0-1-alpha -n atlas --for=condition=complete --timeout=10m
oc logs job/atlas-migrate-v0-0-1-alpha -n atlas -c atlas-migrate
oc rollout status deploy/atlas-api -n atlas --timeout=10m
oc rollout status deploy/atlas-mcp -n atlas --timeout=5m
oc rollout status deploy/atlas-tls -n atlas --timeout=5m
oc get route -n atlas
```

- `clusters.postgresql.cnpg.io/atlas-db` is the long form of `cluster/atlas-db`; it avoids any
  other resource called `cluster`. Under EDB it is `clusters.postgresql.k8s.enterprisedb.io/atlas-db`.
- The database takes a few minutes: the operator creates the volumes, the first instance, then
  the standby. `oc get clusters.postgresql.cnpg.io -n atlas` shows its phase meanwhile.
- The migrate Job's log ends with an `[atlas-migrate] upgrade:` line saying what it created.
  If the Job ran before the database was ready it may have failed: see troubleshooting.
- `atlas-api` restarts once or twice while the database comes up; that is its startup probe
  giving up and Kubernetes trying again, and it settles by itself.

Then check that the pod network you gave was understood. This must print nothing:

```powershell
oc logs deploy/atlas-api -n atlas -c atlas-api | Select-String unparseable
```

## 7. The first owner

Create the first account, with the owner role. Pick a pod by name first: `atlas-api` runs two
replicas, and each `oc exec` on `deploy/atlas-api` may land on either one, while the password
file below exists only in the pod that wrote it.

```powershell
$pod = oc get pod -n atlas -l app.kubernetes.io/component=api -o jsonpath='{.items[0].metadata.name}'
oc exec -n atlas $pod -c atlas-api -- atlas-admin --actor <your name> user add <username> --role owner --break-glass --out /tmp/first-owner.password
```

It prints `created <username> ...`, and the **temporary password** goes into the file, not to
the screen. `--actor` records who ran the command in the audit trail (inside the pod you are an
anonymous user ID). `--break-glass` marks the one account whose password still works if sign-in
is later switched to SSO only. Fetch the file into a directory only you can read, then delete
the copy in the pod:

```powershell
New-Item -ItemType Directory -Force "$HOME\atlas-first-owner" | Out-Null
icacls "$HOME\atlas-first-owner" /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F"
oc exec -n atlas $pod -c atlas-api -- cat /tmp/first-owner.password > "$HOME\atlas-first-owner\password.txt"
oc exec -n atlas $pod -c atlas-api -- rm /tmp/first-owner.password
```

Without `--out` the password is printed instead: then run the command only in a terminal that is
not recorded (not inside an agent session such as Claude Code, not in a logged CI job). More
accounts are added the same way, without `--break-glass` and with `--role member` or another
role; [Identity and access](../../docs/oss/guides/identity-and-access.md) describes the roles.

## 8. Sign in

Open `https://<the host from step 5>/` and sign in with the username and the temporary password.
You are taken straight to a forced password change; after it you land on the board. File a first
story to see that writes work. Then delete `$HOME\atlas-first-owner\`. For the break-glass
account, the password to keep in the vault is the one set at that change, not the temporary one.

**The certificate.** The browser sees the router's certificate, which is the platform's. On an
ARO cluster with its default domain that certificate is publicly trusted and nothing is needed.
If the browser warns instead, the router uses a certificate from a CA your machine does not
trust. Every logged-in user can read the router's CA bundle:

```powershell
oc get configmap default-ingress-cert -n openshift-config-managed -o jsonpath='{.data.ca-bundle\.crt}' > ingress-ca.crt
Import-Certificate -FilePath ingress-ca.crt -CertStoreLocation Cert:\CurrentUser\Root
```

Check with the platform team that this is the CA they intend you to trust before you import it.
Never click through the warning: the next request carries your password.

## 9. Connect Claude Code

Follow [Atlas for Claude Code](../../clients/claude-code/README.md), with two differences from
the Compose install it describes:

- **The token**: sign in to the board and mint your own under *Settings -> Agent credentials*,
  which needs no terminal at all. To mint one for someone else from the cluster instead, use the
  same `$pod` and the same `--out` and `cat` pattern as in step 7, with
  `atlas-admin --actor <your name> token mint --user <them> --purpose claude-code --scopes read,write --out /tmp/claude-code.token`.
- **The URL** in the MCP template is `https://<the host from step 5>/mcp/`.
- **The CA**: if you imported `ingress-ca.crt` in step 8, point `NODE_EXTRA_CA_CERTS` at it as the
  client page's "Trusting the install's CA" says; a publicly trusted router certificate needs
  nothing.

## Your own registry

For a cluster that may not pull from ghcr.io (the pull test in step 1 failed, or the platform
team says images come from an internal mirror). You copy each image the install uses to your
registry **by digest**, point the overlay at the copies with the same digests, and give the pods a
pull secret if the registry needs one. Do this between steps 4 and 6; the commands use the copy
`deploy\openshift\overlays\ours` from step 4.

**The images.** Four, for the release the overlay names (`newTag` in its `images:`):

| Image | Used by | Its digest comes from |
|---|---|---|
| `ghcr.io/<owner>/gradinai-atlas-api` | atlas-api, its init container, the migrate Job, the audit CronJob | the release's `release-images.env`, line `ATLAS_IMAGE_API` |
| `ghcr.io/<owner>/gradinai-atlas-mcp` | atlas-mcp | line `ATLAS_IMAGE_MCP` |
| `ghcr.io/<owner>/gradinai-atlas-tls` | atlas-tls | line `ATLAS_IMAGE_TLS` |
| `ghcr.io/cloudnative-pg/postgresql` | the database pods | `imageName` in `components/cnpg/cluster.yaml` (not in `release-images.env`) |

`release-images.env` is attached to every GitHub release of the Atlas repository; each line is
`NAME=<image>:<tag>@sha256:<digest>`, and the digests are the same ones the example overlay
carries. An overlay that runs the `-ubi` images ([`README.md`](README.md#images-and-releases))
takes the `ATLAS_IMAGE_API_UBI`, `_MCP_UBI` and `_TLS_UBI` lines instead. Read them, and the
database image, into variables (`gh` is the GitHub CLI; without it, save the file from the
release page into the repository root):

```powershell
$tag   = 'v0.0.1-alpha'                      # the overlay's newTag
$owner = 'yourowner'                         # as in step 5, lower case
$dest  = 'registry.example.com/team/atlas'   # your registry and path, no trailing /
gh release download $tag -R "$owner/gradinai-atlas" -p release-images.env
$img = @{}
foreach ($line in Get-Content release-images.env) {
  if ($line -match '^(ATLAS_IMAGE_\w+)=(.+)$') { $img[$Matches[1]] = $Matches[2] }
}
$sources = @($img['ATLAS_IMAGE_API'], $img['ATLAS_IMAGE_MCP'], $img['ATLAS_IMAGE_TLS'])
$sources += (Select-String -Path deploy\openshift\components\cnpg\cluster.yaml -Pattern '^\s*imageName:\s*(\S+)').Matches[0].Groups[1].Value
$sources                                     # four lines, each <image>:<tag>@sha256:<digest>
```

**Credentials for pushing.** Write them to a file in the directory step 3 locked to you, from a
prompt, so the password is never on a command line (the same file format serves the pull secret
below, if the same account may pull):

```powershell
$registry = ($dest -split '/')[0]
$c = Get-Credential -Message "push credentials for $registry"
$pair = "$($c.UserName):$($c.GetNetworkCredential().Password)"
$auth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
@{ auths = @{ $registry = @{ auth = $auth } } } | ConvertTo-Json -Depth 3 | Set-Content -NoNewline secrets\openshift\registry-auth.json
```

**Copy by digest.** Each image is copied whole: the published digests are of image indexes with
more than one entry (the image and its build attestation, or several architectures), and copying
only the `linux/amd64` entry would give a different digest. `oc image mirror` reads the source by
digest and pushes it under the same tag; ghcr.io needs no credentials to read:

```powershell
foreach ($src in $sources) {
  $repo, $digest = $src -split '@'
  $name = ($repo -split '/')[-1]                  # gradinai-atlas-api:v0.0.1-alpha, postgresql:<tag>
  oc image mirror -a secrets\openshift\registry-auth.json --keep-manifest-list=true "$($repo -replace ':[^:/]+$', '')@$digest" "$dest/$name"
  oc image info -a secrets\openshift\registry-auth.json --filter-by-os=linux/amd64 "$dest/$(($name -split ':')[0])@$digest" | Select-Object -First 2
}
```

The `oc image info` line is the check: it reads each copy **by the original digest**, so an
answer means your registry holds the same bytes. `manifest unknown` means the copy changed the
digest; copy that image again with `skopeo` (in WSL or a container), which keeps it or fails:
`skopeo copy --all --preserve-digests --dest-authfile <file> docker://<source> docker://<dest>/<name>`.
A registry that is a pull-through (remote) repository of ghcr.io needs no copy at all: its path
to the same image serves the same digest on the first pull.

**Point the overlay at the copies.** The three Atlas images: `newName` only, the digest stays.
This replaces value 2 of step 5 (whether or not step 5's owner was filled in already):

```powershell
$dir = 'deploy\openshift\overlays\ours'
(Get-Content "$dir\kustomization.yaml") -replace 'ghcr\.io/[^/\s]+/gradinai-atlas-', "$dest/gradinai-atlas-" | Set-Content "$dir\kustomization.yaml"
"$dest/" + ($sources[3] -split '/')[-1]          # the database image's new value, printed
```

The database image: in `kustomization.yaml`, at the end of `patches:`, uncomment the entry with
`target: {kind: Cluster, name: atlas-db}` (all five lines) and set its `value:` to the line the
last command printed: your registry's name, the same tag and the same digest.

**The pull secret**, if your registry needs credentials to pull. The platform team often puts one
in the project already (`oc get secrets -n atlas --field-selector type=kubernetes.io/dockerconfigjson`);
otherwise create it from a file of the same format as above, with an account that may pull:

```powershell
oc create secret generic atlas-pull-secret -n atlas --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson=secrets\openshift\registry-auth.json
Remove-Item secrets\openshift\registry-auth.json
```

The pods must then use it. Every Atlas pod runs as ServiceAccount `atlas`, and Kubernetes adds a
ServiceAccount's `imagePullSecrets` to each pod that uses it; that works although the base sets
`automountServiceAccountToken: false` (no API token is mounted, the pull secret is still used).
Two ways:

- **In the overlay** (preferred: the next install from it has it too). In `kustomization.yaml`,
  uncomment `- path: patch-pull-secret.yaml` at the end of `patches:`. That file adds
  `imagePullSecrets` to ServiceAccount `atlas` and `spec.imagePullSecrets` to Cluster `atlas-db`.
  If the secret has another name than `atlas-pull-secret`, change it in both places in the file.
- **By hand**, after step 6 has created the ServiceAccount:
  `oc secrets link atlas atlas-pull-secret --for=pull -n atlas`, then delete the pods stuck in
  `ImagePullBackOff` so they are made again with it. This covers the Atlas pods only.

The database pods do not run as `atlas`: CloudNativePG gives them a ServiceAccount of its own,
named after the Cluster (`atlas-db`), and copies the Cluster's `spec.imagePullSecrets` onto it,
which is why the patch sets both. Then check what will be applied, as in step 5:

```powershell
oc kustomize deploy\openshift\overlays\ours | Select-String -Pattern 'image:|imageName:|imagePullSecrets' -Context 0,1
```

Every image must name your registry and end in `@sha256:` and a digest.

## Troubleshooting

Start with `oc get pods -n atlas` and `oc get events -n atlas --sort-by=.lastTimestamp`.

| What you see | Usually | What to do |
|---|---|---|
| `atlas-api` keeps restarting; `oc describe pod` shows the startup or liveness probe failing with `421` | the request's Host is not `ATLAS_PUBLIC_HOST`. The probes send `Host: $ATLAS_PUBLIC_HOST`, and Atlas refuses any other Host with 421 | `oc get configmap atlas-env -n atlas -o yaml`: all four host lines must carry the same host. Fix `atlas.env`, `oc apply -k` again, then `oc rollout restart deploy/atlas-api deploy/atlas-mcp deploy/atlas-tls -n atlas` (atlas-env changes do not roll the pods by themselves) |
| `421` in the browser | the Route's host and `ATLAS_PUBLIC_HOST` differ (someone edited the Route by hand) | `oc apply -k` again: the overlay copies the host into the Route |
| `ModuleNotFoundError: No module named 'psycopg'` in `atlas-api` or the migrate Job | the database URL reached Atlas without the `+psycopg2` driver: the image ships psycopg2, and CloudNativePG writes a plain `postgresql://` URL. The `db-url` init container exists to rewrite it | `oc logs <pod> -n atlas -c db-url`. Check that nothing mounts the CNPG Secret into the main container or sets `ATLAS_DB_URL_FILE` to another path than `/run/db-url/url`; [`base/db_url.py`](base/db_url.py) explains the bridge |
| `ImagePullBackOff` / `ErrImagePull` | `oc describe pod` names the image and the reason. From ghcr.io: `unauthorized` or `not found` means the owner in `newName` is wrong or has capitals; `i/o timeout`, `connection refused` or a policy refusal means the cluster may not pull from ghcr.io (assumption 1). From your own registry: `unauthorized` means no pull secret reached the pod; `manifest unknown` means the copy lacks that digest | owner: fix value 2. Not from ghcr.io: [Your own registry](#your-own-registry). No pull secret: `oc get pod <pod> -n atlas -o jsonpath='{.spec.imagePullSecrets}'` must name it; if not, enable `patch-pull-secret.yaml` (it covers the database pods too) and delete the stuck pods so they are made again. Wrong digest: copy again, by digest |
| `atlas-api`, the migrate Job or the audit CronJob stay in `ContainerCreating`; `oc describe pod` shows `FailedMount` with `couldn't find key fqdn-uri in Secret atlas-db-app` | the CloudNativePG operator is older than the base expects | step 1, "The CloudNativePG version": enable the `OLDER CLOUDNATIVEPG` entries, `oc apply -k`, delete the waiting pods |
| `oc apply` is refused by an admission webhook or policy naming a missing label | the cluster requires labels of its own | step 5, "Labels your organisation requires" |
| `atlas-db` stays in a setting-up phase, its PVCs `Pending` | no default storage class, or the quota allows no more storage (assumption 6) | `oc describe pvc -n atlas` says which. Name a class, assumption 6 in the table, or ask for quota |
| `oc get clusters.postgresql.cnpg.io -n atlas` shows no status at all | the operator does not watch this namespace | ask the platform team to include the namespace in the operator's watch list |
| The migrate Job shows `Failed` | it ran before the database accepted connections | `oc logs job/atlas-migrate-v0-0-1-alpha -n atlas -c atlas-migrate` to confirm, then `oc delete job atlas-migrate-v0-0-1-alpha -n atlas` and `oc apply -k` again: the step is idempotent |
| Pods `CreateContainerConfigError` or stuck in `ContainerCreating` on a missing Secret | `atlas-peppers` was created in another project, or a TLS Secret is not issued yet | `oc get secrets -n atlas`. `atlas-peppers`: rerun step 3 with the right `-n`. The `*-tls` Secrets come from the service CA within a minute of the Service; if they never appear, ask the platform team whether the service CA operator is healthy |
| Route answers `503` "Application is not available" | the router has no ready `atlas-tls` endpoint, or cannot complete the re-encrypt leg | `oc get endpoints atlas-tls -n atlas` must list addresses. If it does: `oc get route -n atlas -o yaml` must have `termination: reencrypt` and **no** `destinationCACertificate` (left empty, the router verifies `atlas-tls` against the service CA, including its name); `oc logs deploy/atlas-tls -n atlas` shows a TLS handshake error if not |
| `unparseable entry` in the `atlas-api` log | value 4 is not a CIDR | fix `patch-trusted-proxy.yaml`, `oc apply -k`; the Deployment rolls by itself |
| `atlas-tls` fails to start with a Caddyfile error about `trusted_proxies` | value 4b is not a CIDR list (Caddy wants spaces, not commas) | fix `patch-tls-trusted-proxy.yaml`, `oc apply -k` |
| Every sign-in in the audit trail shows the same pod address | 4b does not cover the router's address (router on the host network, or a gateway in front) | assumption 5 |
| Pods `Pending` with `exceeded quota` or a `LimitRange` message | the project's quota or limits are tighter than the manifests' requests (assumption 3) | `oc describe quota -n atlas`, `oc describe limitrange -n atlas`; ask for more, or patch the resources (assumption 3) |
| The database, the migrate Job or the Route waits and times out instead of failing | a NetworkPolicy blocks the connection | the next section |

### A cluster that restricts traffic (NetworkPolicies)

Traffic between pods can be closed by NetworkPolicies in the project (from the project template,
or Atlas's own), or by cluster-wide policies the platform team writes (AdminNetworkPolicy, and
the one BaselineAdminNetworkPolicy). A blocked connection is not refused, it times out, so the
symptoms are waits rather than errors:

| What you see | Usually blocked |
|---|---|
| `oc get clusters.postgresql.cnpg.io -n atlas` shows the phase `Instance Status Extraction Error: HTTP communication issue`, or stops at one instance and no standby (`atlas-db-2`) is ever created | the CloudNativePG operator cannot reach the database pods on 8000 |
| a database pod stays `0/1 Running`; `oc logs <pod> -n atlas -c postgres` repeats `wait-for-get-cluster` with `i/o timeout` | the database pod cannot reach the Kubernetes API (6443) |
| the migrate Job runs for minutes and then fails with a connection `timeout` to `atlas-db-rw` (not `connection refused`, not a password error) | atlas-api, the Job or the CronJob cannot reach the database on 5432 |
| `atlas-api` never becomes ready (`/readyz` fails) and its log shows database timeouts | the same |
| `oc get route -n atlas` looks right, but the browser gets `503` "Application is not available" | the router cannot reach `atlas-tls:8443`, or atlas-tls is not ready (next line). With Atlas's policies on, the router's namespace must carry the key `policy-group.network.openshift.io/ingress` (step 1, "The router's namespace label") |
| `atlas-tls` stays `0/1` (readiness fails) while `atlas-api` is ready | atlas-tls cannot reach `atlas-api:8443` (its readiness goes through to atlas-api) |
| MCP clients get `502` from `/mcp` while the board works | atlas-tls cannot reach `atlas-mcp:8493`, or atlas-mcp cannot reach `atlas-tls:9443` |
| any of the above with `could not resolve` or `Name or service not known` | DNS (`openshift-dns`, port 5353) |

**Look at what is in force:**

```powershell
oc get networkpolicy -n atlas
oc describe networkpolicy -n atlas
oc get adminnetworkpolicy,baselineadminnetworkpolicy     # cluster-wide; refused without the right: then ask the platform team
oc get pods -n atlas --show-labels                       # the policies select on app.kubernetes.io/name and app.kubernetes.io/component
oc get endpoints atlas-tls -n atlas
```

**Test one connection by hand**, from inside a pod (a timeout means blocked; refused or an answer
means the network is open). The atlas-api image has Python:

```powershell
$pod = oc get pod -n atlas -l app.kubernetes.io/component=api -o jsonpath='{.items[0].metadata.name}'
oc exec -n atlas $pod -c atlas-api -- python -c "import socket; socket.create_connection(('atlas-db-rw', 5432), 5); print('open')"
```

**The fix**, in your overlay (`deploy\openshift\overlays\ours\kustomization.yaml`):

1. Switch on Atlas's NetworkPolicies: remove the `#` in front of
   `- ../../components/networkpolicies` under `components:`. They allow exactly Atlas's own edges,
   the operator's and the API server's included, on top of whatever the project already has.
2. Find the operator's namespace and set it if it is not `cnpg-system`:

   ```powershell
   oc get pods -A -l app.kubernetes.io/name=cloudnative-pg -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name
   ```

   If that is refused or prints nothing, ask the platform team (OperatorHub installs, community or
   EDB, usually run in `openshift-operators`). Then add the patch from
   [`README.md`, "The operator's namespace"](README.md#the-operators-namespace) under `patches:`.
3. Apply and check:

   ```powershell
   oc apply -k deploy\openshift\overlays\ours
   oc get networkpolicy -n atlas
   oc wait clusters.postgresql.cnpg.io/atlas-db -n atlas --for=condition=Ready --timeout=15m
   ```

   A migrate Job that already failed: `oc delete job atlas-migrate-v0-0-1-alpha -n atlas`, then
   `oc apply -k` again.

If it still waits after that, a cluster-wide policy is the likely cause: an AdminNetworkPolicy
**Deny** cannot be overridden from the project (a BaselineAdminNetworkPolicy deny can, and Atlas's
policies do). Send the platform team the output of the commands above and the edge list in
[`README.md`, "NetworkPolicies"](README.md#networkpolicies-task-34).

## The assumptions and what to change if one is wrong

These are the working assumptions of the ARO epic (2026-11-06). Each can be changed in the
overlay, without touching the base.

| # | Assumption | Checked in | If it is wrong, change |
|---|---|---|---|
| 1 | The cluster pulls straight from ghcr.io, without a pull secret. **Likely wrong**: a company cluster usually pulls only from an internal mirror, with a pull secret | step 1, pull test | [Your own registry](#your-own-registry): copy the four images (api, mcp, tls and the database image) by digest, change only `newName` in `images:` (the digest stays: same bytes) and the Cluster's `imageName`, and enable `patch-pull-secret.yaml` if the registry needs credentials (it covers ServiceAccount `atlas` and the Cluster's own) |
| 2 | The cluster is not in FIPS mode | ask the platform team | Stop: these images are not built for FIPS (task 1.6 is the FIPS build, not started). Tell the Atlas maintainers |
| 3 | OpenShift 4.20 or 4.21, `restricted-v2` SCC, no tight LimitRange | `oc version`; the dry run in step 5 | Tighter limits: a patch on `resources` of the Deployment or Job concerned, or on `spec.resources` of `Cluster` `atlas-db`. A smaller quota: `replicas: 1` on `atlas-api` and on `atlas-tls`, and delete each one's PodDisruptionBudget (`minAvailable: 1` on one replica blocks every node drain), and `spec.instances: 1` on the Cluster (no standby: test installs only); [`README.md`](README.md), "What an overlay must set" |
| 4 | Re-encrypt Routes are allowed, with our own host under the apps domain, on the router's default certificate | step 6, the Route is admitted; step 8 | A host outside the apps domain: the platform's certificate for it goes on the Route (`spec.tls.certificate`, `key`, `caCertificate`, by a patch on the Route), or the platform team provides it. Re-encrypt refused: tell the Atlas maintainers; edge or passthrough loses the client address (the design, question 3) |
| 5 | Nothing sits in front of the router (no Application Gateway or Front Door), and the router keeps the client address | after step 8: one deliberately wrong password, then the sign-in events (`GET /api/audit/export` as owner) must show your own address, not a pod's | Router on the host network: add the machine network to 4b. A gateway in front: add its range to 4b, and set the Route `atlas`'s annotation `haproxy.router.openshift.io/set-forwarded-headers` to `append` with a patch, so the router keeps the client address the gateway put in the header (the design, question 3). Value 4 stays the pod network |
| 6 | Community CloudNativePG 1.25 or later (`postgresql.cnpg.io/v1`), the default storage class | step 1 | **EDB Postgres for Kubernetes**: add `- ../../components/cnpg-edb` after `../../components/cnpg` in `components:` (an overlay with it applies fine, but the repository's kubeconform lint has no EDB schemas and refuses it, so keep that change in your own copy). **No default class or another one wanted**: a patch on `Cluster` `atlas-db` setting `spec.storage.storageClass` and `spec.walStorage.storageClass`. **Operator older than 1.25.3 / 1.26.1** (no `fqdn-uri` key, or no `databases`): the two `OLDER CLOUDNATIVEPG` entries in the overlay's `patches:` (step 1, "The CloudNativePG version"); they read the `uri` key and leave out the `Database` `atlas-db-atlas`, and the `atlas` database is still created by the Cluster's `initdb` |
| 7 | No backup at first install | | Before real work is stored: replace `../../components/cnpg` with `../../components/cnpg-objectstore-plugin` in `components:` (it needs the Barman Cloud plugin on the cluster and a storage account); [`README.md`](README.md), "The database". Until then the standby is the only copy |
| 8 | No External Secrets Operator: plain Secrets from `new-secrets.py` | step 3 | The `external-secrets` component (task 3.6, not built yet) delivers `atlas-peppers` from Key Vault instead; the Secret's name and keys stay the same, so nothing else changes |
| 9 | `oc apply -k` by hand (later from GitLab), no GitOps | | Argo CD renders the same overlay; the `gitops` component (task 3.6) adds what it needs |
| 10 | Image signatures are not enforced | the pods start | If the cluster enforces signatures, mirror the digests and sign them with the organisation's key (the design, question 12) |
| 11 | Local accounts first; Entra ID later | step 7 | The `oidc` component (task 3.6), with the client secret made by `new-secrets.py --oidc-client-secret` |
| 12 | Egress open enough later for OIDC | | The `networkpolicies` component closes egress to Atlas's own edges; the `oidc` and `retrieval` components (task 3.6) bring theirs |
| 13 | The cluster's default logging collects stdout | `oc logs` works | nothing to change in Atlas |
| 14 | No service mesh | | If the namespace is meshed, keep the Atlas pods out of it (Atlas's own TLS already covers every hop) |
| 15 | The project's own NetworkPolicies (if any) leave the operator a way in, or Atlas's are switched on | step 2, `oc get networkpolicy` | Switch on `components/networkpolicies` and set the operator's namespace: troubleshooting, "A cluster that restricts traffic" |

### The patches the table names

Each goes under `patches:` in your overlay's `kustomization.yaml` (the example already has a
`patches:` list; add to it). Preview with `oc kustomize` before applying.

A smaller trial install, for a tight quota (assumption 3; step 1, "The quota and limits"). One
replica each of `atlas-api` and `atlas-tls` without their PodDisruptionBudgets, and one smaller
database instance with no standby. Not for real work: a node drain or a lost volume takes it down.

```yaml
  - target: {kind: Deployment, name: atlas-api}
    patch: |-
      - {op: replace, path: /spec/replicas, value: 1}
  - target: {kind: Deployment, name: atlas-tls}
    patch: |-
      - {op: replace, path: /spec/replicas, value: 1}
  - patch: |-
      $patch: delete
      apiVersion: policy/v1
      kind: PodDisruptionBudget
      metadata:
        name: atlas-api
  - patch: |-
      $patch: delete
      apiVersion: policy/v1
      kind: PodDisruptionBudget
      metadata:
        name: atlas-tls
  - target: {kind: Cluster, name: atlas-db}
    patch: |-
      - {op: replace, path: /spec/instances, value: 1}
      - {op: replace, path: /spec/storage/size, value: 5Gi}
      - {op: replace, path: /spec/walStorage/size, value: 2Gi}
      - {op: replace, path: /spec/resources, value: {requests: {cpu: 250m, memory: 512Mi}, limits: {cpu: "1", memory: 1Gi}}}
```

A storage class other than the default (assumption 6):

```yaml
  - target: {kind: Cluster, name: atlas-db}
    patch: |-
      - op: add
        path: /spec/storage/storageClass
        value: <class>
      - op: add
        path: /spec/walStorage/storageClass
        value: <class>
```

An operator older than 1.25, which has no `Database` resource (assumption 6; `patch-older-cnpg.yaml`
in the example overlay already does this, together with the `uri` key):

```yaml
  - patch: |-
      $patch: delete
      apiVersion: postgresql.cnpg.io/v1
      kind: Database
      metadata:
        name: atlas-db-atlas
```

The database image from a mirror (assumption 1; the example overlay has this entry commented
out, and [Your own registry](#your-own-registry) prints the value); the digest of the tag in
`components/cnpg/cluster.yaml`, copied to your registry:

```yaml
  - target: {kind: Cluster, name: atlas-db}
    patch: |-
      - op: replace
        path: /spec/imageName
        value: <registry>/cloudnative-pg/postgresql:<same tag>@sha256:<same digest>
```

**Not in this first install, on purpose, unless you switched them on:** Atlas's NetworkPolicies
(`components/networkpolicies`); without them, and without a project template's policies, nothing
stops other pods in the cluster from reaching `atlas-api` or the database directly; backups; SSO; the
retrieval add-on; the GitLab pipeline. Treat this install as a trial until those are in.
