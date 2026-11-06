# Atlas on OpenShift: a first install, step by step

This guide installs Atlas on Azure Red Hat OpenShift (ARO) in one project (an OpenShift project
is a Kubernetes namespace). Follow it from top to bottom. You make every decision once, in the
first section, and collect every value once, in the second. After that every command is
copy-paste: nothing in a step needs editing.

Everything is written for **PowerShell 7 on Windows**, run from a checkout of this repository.
[`README.md`](README.md) is the map of the manifests; the design and its reasons are in
[Atlas on Azure Red Hat OpenShift](../../docs/oss/research/openshift-aro.md).

**How to read a step.** Each numbered step has the same four parts:

- what the step does and why, in one sentence;
- the commands;
- **Expected:** what a good run prints, or the line to look for;
- **If not:** the most likely cause and its fix, or a row of the
  [troubleshooting table](#appendix-b-troubleshooting) (rows are numbered T1, T2, ...).

Where your choices from the first section change a step, the step shows each choice as a
labelled block, for example **Path: existing namespace**. Run only the blocks for your path.

**Keep a log.** Copy the output of every step into a text file as you go. If something fails,
that file is what the Atlas maintainers need. Step 1 creates a private folder for it.

## Before you start: choose your path

Settle these five choices now. Each has a default; the last column says when to pick the other
one. You write your choices into the values block in the next section, and the steps follow
them.

| # | Choice | Default | Pick the other one when |
|---|---|---|---|
| a | **Project**: a new project for Atlas (`new`), or an existing namespace shared with other applications (`existing`) | `new` | you may not create projects, or the platform team gives you a namespace. Both are fully supported |
| b | **Images**: pulled straight from ghcr.io (`ghcr`), or from your organisation's own registry (`mirror`) | `ghcr` | the cluster may not pull from the internet, or policy says images come from an internal registry |
| c | **Database**: created by the Atlas overlay with everything else (`overlay`), or installed first from the plain YAML file `deploy/openshift/database/atlas-db.yaml` (`file`) | `overlay` | the platform team owns databases and wants to create or review the database object themselves, or you want to see the database healthy before Atlas exists |
| d | **NetworkPolicies** (firewall rules between pods): Atlas's own policies on (`on`), or off (`off`) | `on` | only for a short trial in a project where you are told not to add policies |
| e | **Size**: two database instances (a primary and a hot standby) and two replicas each of `atlas-api` and `atlas-tls` (`standard`), or one of each (`trial`) | `standard` | the project's quota is too small for `standard` (step 4 checks) and this install holds no real work |

**What each other choice changes**, so you know before you start:

- **a, `existing`.** You skip `oc new-project`. The overlay's `namespace:` is the existing
  namespace (the values block sets it). Step 2 checks that no object named `atlas...` is there
  already and lists the namespace's NetworkPolicies. If one of them is `allow-same-namespace`
  (or `allow-from-same-namespace`), every pod of the other applications in that namespace may
  open a connection to every Atlas pod, and Atlas's policies cannot take that away
  (NetworkPolicies only ever add permissions). The consequence: a pod of another application can
  reach `atlas-tls` directly and set the client address Atlas records for sign-ins. The database
  still needs its password, and every connection is still TLS. Agree with the platform team
  whether that is acceptable. Atlas's policies select only pods labelled
  `app.kubernetes.io/name: atlas`: they never change the traffic of the other applications.
- **b, `mirror`.** Step 7 copies the four images to your registry by digest (a SHA-256 of the
  image, so the copy is provably the same bytes), and the overlay points at your copies. If the
  registry needs credentials, the pods use a pull secret: a Secret of type
  `kubernetes.io/dockerconfigjson` in the namespace. In an existing namespace the platform team
  has usually created one already; in a new project step 7 shows how to create it.
- **c, `file`.** Step 10 installs the database from your copy of `atlas-db.yaml` and waits until
  it is healthy. The overlay then lists the component `cnpg-external` instead of `cnpg`, so it
  never touches the database. Everything that would patch the database (storage class, size,
  mirror, pull secret, the operator's namespace) is written into your copy of `atlas-db.yaml`.
- **d, `off`.** The overlay leaves out the component `networkpolicies`, and step 6 does not need
  the router's namespace label.
  Any pod that can reach the project can then reach Atlas's database port and front door
  directly. If the project already has a policy that denies traffic by default (often called
  `deny-all`), `off` does not work at all: the database never becomes healthy. Step 2 shows it.
- **e, `trial`.** The overlay sets one replica of `atlas-api` and of `atlas-tls` and deletes
  their PodDisruptionBudgets (a PodDisruptionBudget, PDB, tells Kubernetes how many pods must
  stay up while a node is drained; with one replica and `minAvailable: 1` no drain can ever
  proceed). The database gets one instance, smaller volumes and `enablePDB: false`, for the same
  reason: CloudNativePG cannot move a single instance away from a node, so its own PDB would
  block every node drain
  ([CloudNativePG 1.30, "PostgreSQL Clusters used for Development or Testing"](https://cloudnative-pg.io/docs/1.30/kubernetes_upgrade)).
  There is no standby: losing the database volume loses the data.

### What you need on your machine

- PowerShell 7 (`$PSVersionTable.PSVersion` shows 7.x).
- `oc` 4.20 or later, logged in to the cluster (`oc login ...`, as the platform team told you).
  Kustomize is built into `oc`.
- Python 3.10 or later. If `python --version` fails but `py --version` works, run
  `Set-Alias python py` in each new terminal.
- `curl.exe`, which Windows 10 and 11 ship.
- A checkout of this repository at the release you install (its tag, for example
  `v0.0.2-alpha`).
- **Path: images `mirror`**: credentials that may push to your registry.

The steps need no cluster-wide rights. Step 3 checks the project rights.

## The values sheet

Collect each value below. Run the command, read the output, and write the value into the block
that follows the table. A command that may be refused with project rights only says so; then ask
the platform team.

| # | Value | Variable | Command that finds it | What the output looks like |
|---|---|---|---|---|
| V1 | The project (namespace) | `$ns` | `new`: choose a name (lower case letters, digits, `-`). `existing`: `oc projects` | `oc projects` lists the projects you can see; yours is one line of it |
| V2 | The cluster's apps domain | `$appsDomain` | `oc get ingresses.config cluster -o jsonpath='{.spec.domain}'` | one line, such as `apps.<cluster>.<region>.aroapp.io` |
| V3 | Atlas's public host name | `$atlasHost` | none: `atlas.` followed by V2. It must be one label under the apps domain, so that the router's default certificate covers it. If another Route already uses that host (step 13 tells), pick another first label, such as `atlas-<team>` | `atlas.apps.<cluster>.<region>.aroapp.io` |
| V4 | The pod network (the address range pods get) | `$podNet` | `oc get network.config cluster -o jsonpath='{.spec.clusterNetwork[*].cidr}'` | one CIDR, or several separated by spaces. ARO's default is the /14 that starts the 10.128 block. Copy it exactly as printed |
| V5 | The CloudNativePG operator's namespace | `$cnpgNs` | `oc get pods -A -l app.kubernetes.io/name=cloudnative-pg -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name` (may be refused: ask the platform team) | `cnpg-system  cnpg-controller-manager-...` (installed from its manifest or Helm chart), or `openshift-operators  ...` (installed from OperatorHub) |
| V6 | The CloudNativePG operator's version | (checked in step 5) | step 5 | 1.25.3 or later on the 1.25 line, 1.26.1 or later, or any 1.27 or later |
| V7 | The storage class for the database | `$storageClass` | `oc get storageclass` | one line says `(default)` (on ARO usually `managed-csi`). Leave `$storageClass` empty to use the default; set it only if the platform team names another class |
| V8 | Where the Atlas images come from | `$registry` | `ghcr`: `ghcr.io/` followed by the GitHub account the Atlas repository belongs to, in lower case (`github.com/<Owner>/gradinai-atlas` gives `ghcr.io/<owner>`). `mirror`: your registry and path, such as `registry.example.com/team/atlas` | a path with no trailing `/` |
| V9 | The pull secret | `$pullSecret` | `ghcr`: none, leave empty. `mirror`: `oc get secrets -n <V1> --field-selector type=kubernetes.io/dockerconfigjson` | a header line and lines such as `change-me-pull-secret  kubernetes.io/dockerconfigjson  1  12d`. Leave empty if the registry needs no credentials or there is none yet (step 7 tells) |
| V10 | Labels your organisation requires on every object | `$orgLabels` | ask the platform team; the dry run in step 11 names a missing label if there is one | key and value pairs, such as `example.com/cost-center=change-me`. Usually none |
| V11 | A privacy contact | `$privacyContact` | ask the owner of the install: the address people write to about their data | an e-mail address |
| V12 | The first owner account | `$adminUser`, `$actor` | your choice: a username for yourself, and your name for the audit trail | `jdoe`, `Jane Doe` |
| V13 | Azure Key Vault for the pepper backup | `$keyVault` | ask the platform team; leave empty if you back up to another vault | a vault name |
| V14 | Is the router internal? Atlas must not be reachable from the internet, and a Route under the apps domain is exactly as reachable as the router that serves it | (checked in step 6) | `oc get ingresscontroller default -n openshift-ingress-operator -o jsonpath='{.spec.endpointPublishingStrategy.loadBalancer.scope}{" / "}{.status.endpointPublishingStrategy.loadBalancer.scope}'` (needs read access to `openshift-ingress-operator`; if refused, ask the platform team "is the default IngressController's load balancer scope Internal or External?") | `Internal / Internal` or ` / Internal`: internal, nothing to set. `External` in the part after `/` (the status, which is what is in force; the part before it is empty when the cluster uses its default, which on Azure is `External`): the router is on the internet; set V15 or V16 |
| V15 | Only if V14 is `External` (preferred): the label of an internal router shard | `$routerShard` | ask the platform team for an internal IngressController with a `routeSelector` (route sharding: a router that serves only Routes carrying a label), and for the label and the domain it serves. Its domain replaces V2: `oc get ingresscontroller <its name> -n openshift-ingress-operator -o jsonpath='{.spec.domain}'` | a `key=value` pair such as `router=internal`, and a domain such as `apps-internal.<cluster>.<region>.aroapp.io` |
| V16 | Only if V14 is `External` and V15 is not available yet (a stopgap): the source ranges allowed in | `$ipAllowlist` | ask the network team for the organisation's address ranges as the router sees them | CIDRs separated by spaces, such as `change-me-office-range/24 change-me-vpn-range/20`. At most 61 entries |

Save the block below as a file, for example `$HOME\atlas-values.ps1`. Put your choices and values
on the right-hand sides, then run it with a dot and a space in front:
`. $HOME\atlas-values.ps1`. **Run that line again in every new terminal** before you continue:
every command below uses these variables.

```powershell
# ---- Atlas on OpenShift: this install's choices and values (FIRST-INSTALL.md) ----
$repo           = 'C:\src\gradinai-atlas'       # your checkout of this repository
$work           = "$HOME\atlas-install"         # a private folder for this install (step 1 creates it)

# The five choices ("Before you start").
$nsMode         = 'new'        # a: 'new' | 'existing'
$imageSource    = 'ghcr'       # b: 'ghcr' | 'mirror'
$dbMode         = 'overlay'    # c: 'overlay' | 'file'
$netpol         = 'on'         # d: 'on' | 'off'
$size           = 'standard'   # e: 'standard' | 'trial'

# The values sheet.
$ns             = 'atlas'                              # V1
$appsDomain     = 'apps.change-me.example.com'         # V2
$atlasHost      = "atlas.$appsDomain"                  # V3
$podNet         = 'change-me-pod-network-cidr'         # V4, exactly as printed
$cnpgNs         = 'cnpg-system'                        # V5
$storageClass   = ''                                   # V7, '' = the cluster's default class
$registry       = 'ghcr.io/change-me-owner'            # V8, no trailing /
$pullSecret     = ''                                   # V9, '' = none
$orgLabels      = @{}                                  # V10, e.g. @{ 'example.com/cost-center' = 'change-me' }
$privacyContact = 'privacy@example.com'                # V11
$adminUser      = 'change-me-username'                 # V12
$actor          = 'change-me-your-name'                # V12
$keyVault       = ''                                   # V13, '' = another vault
$routerShard    = ''                                   # V15, '' = the default router; e.g. 'router=internal'
$ipAllowlist    = ''                                   # V16, '' = no allowlist; CIDRs separated by spaces
$seed           = 'demo'   # 'demo': one example project on the empty board; 'none': an empty board

# ---- Derived from the checkout. Do not edit below this line. ----
Set-Location $repo
$overlay   = Join-Path $repo 'deploy\openshift\overlays\ours'
$dbFile    = Join-Path $overlay 'atlas-db.yaml'
$example   = Get-Content -Raw deploy\openshift\overlays\example\kustomization.yaml
$release   = [regex]::Match($example, '(?m)^\s+newTag: (\S+)').Groups[1].Value
$digests   = @([regex]::Matches($example, '(?m)^\s+digest: (sha256:[0-9a-f]{64})') | ForEach-Object { $_.Groups[1].Value })
$job       = 'atlas-migrate-' + ($release -replace '\.', '-')
$apiImage  = "$registry/gradinai-atlas-api-ubi:$release@$($digests[0])"
$mcpImage  = "$registry/gradinai-atlas-mcp-ubi:$release@$($digests[1])"
$tlsImage  = "$registry/gradinai-atlas-tls-ubi:$release@$($digests[2])"
$pgSource  = (Select-String -Path deploy\openshift\components\cnpg\cluster.yaml -Pattern '^\s*imageName:\s*(\S+)').Matches[0].Groups[1].Value
$pgImage   = if ($imageSource -eq 'mirror') { "$registry/" + ($pgSource -split '/')[-1] } else { $pgSource }
$leftover  = Get-Variable ns, appsDomain, podNet, registry, adminUser, actor, privacyContact |
             Where-Object { "$($_.Value)" -match 'change-me' }
if ($leftover) { Write-Warning ("Still change-me: " + ($leftover.Name -join ', ')) }
"release $release, job $job, host $atlasHost, namespace $ns"
```

**Expected:** one line such as `release v0.0.2-alpha, job atlas-migrate-v0-0-2-alpha, host
atlas.apps..., namespace atlas`, and no warning.

**If not:** `WARNING: Still change-me: ...` names the values you have not filled in. An error from
`Set-Location` means `$repo` is not your checkout. `release ,` (empty) means `$repo` is not the
root of this repository.

## The steps

### 1. Check your tools and create the private folder

This confirms you are logged in to the right cluster and creates a folder only you can read, for
the peppers, the first password and your log.

```powershell
oc version
oc whoami
python --version
New-Item -ItemType Directory -Force $work | Out-Null
icacls $work /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F"
```

**Expected:** `oc version` prints `Client Version: 4.20` or later and a `Server Version:` of
4.20 or 4.21. `oc whoami` prints your user name. `python --version` prints `Python 3.10` or
later. `icacls` ends with `Successfully processed 1 files; Failed processing 0 files`.

**If not:** `error: You must be logged in to the server (Unauthorized)`: log in again with the
`oc login` command the platform team gave you (its tokens expire). `python` not found: run
`Set-Alias python py`, or install Python 3. Another server version: tell the Atlas maintainers
before you go on ([assumption 3](#appendix-a-the-assumptions)).

### 2. Create the project, or check the existing namespace

This gives Atlas its namespace and shows the NetworkPolicies already in it, because they decide
whether the database can come up.

**Path: new project**

```powershell
oc new-project $ns --display-name 'Atlas'
oc get networkpolicy -n $ns
```

**Path: existing namespace**

```powershell
oc project $ns
oc get networkpolicy -n $ns
oc get deploy,sts,svc,secret,configmap,route,pdb,job,cronjob,networkpolicy,serviceaccount,clusters.postgresql.cnpg.io -n $ns -o name | Select-String -Pattern '/atlas'
```

**Expected:** `new`: `Now using project "atlas" on server "https://api...:6443".` and some
hints. `existing`: `Now using project "atlas" on server ...`, and the last command prints
**nothing** (no object named `atlas...` is there). `oc get networkpolicy` prints either
`No resources found in atlas namespace.` or a list. Read the list against this table:

| Policy name (usual forms) | What it means for Atlas |
|---|---|
| `allow-from-openshift-ingress`, `allow-from-openshift-monitoring` | harmless; keep them |
| `allow-same-namespace`, `allow-from-same-namespace` | every pod in this namespace may connect to every Atlas pod. In a namespace used only by Atlas that is harmless. In a shared namespace it is the risk described under choice a: write it in your log and agree it with the platform team |
| `deny-all`, `default-deny`, or any policy with `podSelector: {}` and no rules | traffic is closed by default: **NetworkPolicies must be `on`** (choice d), or the database never becomes healthy |
| anything else | keep the output for the platform team; Atlas's policies add to it and never remove anything |

**If not:** `new-project` is refused (`Forbidden`): you may not create projects. Ask the
platform team for one, set `$nsMode = 'existing'` in your values file, run it again, and run the
`existing` block. The `Select-String` line prints `atlas...` objects: another Atlas install, or
something else with that name, is in this namespace. Stop and ask its owner; do not install over
it.

### 3. Check your rights in the project

This proves, before anything is created, that you may create every kind of object the install
uses.

```powershell
foreach ($r in 'deployments.apps', 'jobs.batch', 'cronjobs.batch', 'services', 'routes.route.openshift.io', 'routes.route.openshift.io/custom-host', 'configmaps', 'secrets', 'serviceaccounts', 'poddisruptionbudgets.policy', 'networkpolicies.networking.k8s.io', 'clusters.postgresql.cnpg.io', 'databases.postgresql.cnpg.io') {
  "{0,-40} {1}" -f $r, (oc auth can-i create $r -n $ns)
}
```

**Expected:** thirteen lines, every one ending in `yes`.

**If not:** `no` on `clusters...` or `databases...`: the CloudNativePG roles are not given to
project admins; ask the platform team for them, or let them run step 10 for you on database
`file`. `no` on `routes.route.openshift.io/custom-host`: you may create Routes only with
generated host names; ask for the `admin` or `edit` role in the project. `no` on
`networkpolicies...` with NetworkPolicies `on`: ask for the right, or choose `off` only if step 2
showed no deny-all policy.

### 4. Check the quota and limits

This compares what Atlas asks for with what the project allows, so that no pod waits for room
that never comes.

```powershell
oc describe quota -n $ns
oc describe limitrange -n $ns
```

**Expected:** `No resources found in atlas namespace.` twice (no quota, no LimitRange), or, for
each resource, at least this much room between `Used` and `Hard`:

| Part | Pods | CPU request (limit) per pod | Memory request (limit) per pod | Storage |
|---|---|---|---|---|
| atlas-api | 2 (3 during a rollout) | 250m (1) | 256Mi (512Mi) | |
| atlas-tls | 2 | 50m (500m) | 64Mi (192Mi) | |
| atlas-mcp | 1 | 100m (500m) | 128Mi (256Mi) | |
| database (`atlas-db`) | 2 | 500m (2) | 1Gi (2Gi) | 20Gi data + 5Gi WAL each |
| migrate Job, while it runs | 1 | 250m (1) | 256Mi (512Mi) | |
| audit purge CronJob, while it runs | 1 | 50m (250m) | 128Mi (256Mi) | |
| **`standard`, at most** (a rollout, the Job and the CronJob at once) | 10 | **2.25 CPU (9.75)** | **3.4Gi (6.9Gi)** | **50Gi in 4 volumes** |
| **`trial`, at most** | 6 | **1 CPU (4.25)** | **1.3Gi (2.7Gi)** | **7Gi in 2 volumes** |

A quota may also count `pods`, `persistentvolumeclaims` and `requests.storage`. A LimitRange
matters if its `Max` is below a limit above, or its `Min` above a request.

**If not:** less room than `standard` needs: set `$size = 'trial'` in your values file and run it
again (choice e), or ask the platform team for more quota. Less than `trial`: ask for more quota.
A LimitRange `Max` below a limit: ask the platform team ([assumption 3](#appendix-a-the-assumptions)).

### 5. Check the CloudNativePG operator

The database is a CloudNativePG (CNPG) `Cluster`, run by an operator the platform team already
has. This checks that the operator is there, which API it speaks, and that it is new enough to
write the connection key Atlas reads (`fqdn-uri`).

```powershell
oc api-resources --api-group=postgresql.cnpg.io
oc api-resources --api-group=postgresql.k8s.enterprisedb.io
```

Then its version (V6), with the one of these two lines that fits how it was installed. Either may
be refused with project rights only; then ask the platform team for the version.

```powershell
oc get csv -A | Select-String -Pattern 'cloudnative-pg|cloud-native-postgresql'
oc get deploy -n $cnpgNs -o jsonpath='{.items[*].spec.template.spec.containers[*].image}'
```

**Expected:** the first command lists, among others, `clusters` and `databases` with
`APIVERSION` `postgresql.cnpg.io/v1`. The second prints only its header line. Of the two version
lines, the one that does not fit your cluster prints an error (`doesn't have a resource type
"csv"`, `Forbidden` or `NotFound`); ignore it. The version is in
the name (`cloudnative-pg.v1.27.1`) or the image tag (`...cloudnative-pg:1.27.1`): 1.25.3 or
later on the 1.25 line, 1.26.1 or later on the 1.26 line, or any 1.27 or later.

**If not:** the first prints only its header but the second lists `clusters`: the cluster runs
EDB Postgres for Kubernetes; row T13. Both print only headers: no operator; ask the platform team
for community CloudNativePG 1.27 or later. `clusters` without `databases`, or an older version:
row T12.

### 6. Check the router: internal, and its namespace label

The router (OpenShift's IngressController) is what makes the Route reachable. This checks that
the router serving Atlas is internal, so that Atlas is not on the internet, and that Atlas's
NetworkPolicies will recognise it.

```powershell
oc get ingresscontroller default -n openshift-ingress-operator -o jsonpath='{.spec.endpointPublishingStrategy.loadBalancer.scope}{" / "}{.status.endpointPublishingStrategy.loadBalancer.scope}{"\n"}'
if ($routerShard) { oc get ingresscontroller -n openshift-ingress-operator -o custom-columns=NAME:.metadata.name,DOMAIN:.spec.domain,SCOPE:.status.endpointPublishingStrategy.loadBalancer.scope,ROUTES:.spec.routeSelector.matchLabels }
oc get namespace openshift-ingress --show-labels
```

**Expected:**

- The first line ends in `/ Internal` (the part after `/` is what is in force). That is all,
  unless it says `External` (below).
- **Path: `$routerShard` set**: a line for the internal router with your V15 domain, `SCOPE`
  `Internal` and your label under `ROUTES`, and the `default` line with a `ROUTES` selector that
  excludes that label (or the platform team confirms that `default` does not serve it).
  Otherwise the default router serves the Route too, on the internet.
- **Path: NetworkPolicies `on`**: the `LABELS` column of the last line contains
  `policy-group.network.openshift.io/ingress`, followed by `=` and any value or none
  (`...ingress=` and `...ingress=ingress` both work). Every router of the cluster runs in this
  namespace, sharded ones included.

**If not:** the first line ends in `/ External`: the default router is on the internet, and so
would Atlas be. Do not go on with `$routerShard` and `$ipAllowlist` both empty. Ask the platform
team for an internal router shard (V15), and set `$routerShard` and `$appsDomain` (its domain)
in your values file; step 9 then labels the Route for that router. Until it exists, as a
stopgap, set `$ipAllowlist` (V16): step 9 then puts the annotation
`haproxy.router.openshift.io/ip_allowlist` on the Route, and the router drops every request from
an address outside it. Run the values file again after either change. `Forbidden` on the first
line: ask the platform team the question in V14. The `policy-group...` key is missing with
NetworkPolicies `on`: the Route would answer 503; tell the platform team and the Atlas
maintainers before step 12. `Forbidden` on the last line: ask the platform team for the labels
of `openshift-ingress`.

### 7. Check that the cluster can pull the images

Atlas runs four images: `api`, `mcp`, `tls` and the database image. This starts one throwaway pod
with all four, so that a pull problem shows now, not halfway through the install.

**Path: images `mirror`** (skip for `ghcr`). First copy the four images to your registry, by
digest. Your push credentials go into a file in your private folder from a prompt, never onto a
command line. The second prompt asks for the GitHub account the Atlas repository belongs to (the
`<owner>` of V8).

```powershell
$pushRegistry = ($registry -split '/')[0]
$c = Get-Credential -Message "push credentials for $pushRegistry"
$pair = "$($c.UserName):$($c.GetNetworkCredential().Password)"
$auth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
@{ auths = @{ $pushRegistry = @{ auth = $auth } } } | ConvertTo-Json -Depth 3 | Set-Content -Path "$work\registry-auth.json" -NoNewline
Remove-Variable c, pair, auth
$ghcr = 'ghcr.io/' + (Read-Host 'GitHub account of the Atlas repository, lower case')
$copies = @(
  @("$ghcr/gradinai-atlas-api-ubi:$release@$($digests[0])", $apiImage),
  @("$ghcr/gradinai-atlas-mcp-ubi:$release@$($digests[1])", $mcpImage),
  @("$ghcr/gradinai-atlas-tls-ubi:$release@$($digests[2])", $tlsImage),
  @($pgSource, $pgImage))
foreach ($copy in $copies) {
  $srcRepo, $digest = $copy[0] -split '@'
  $dstRepo = ($copy[1] -split '@')[0]
  oc image mirror -a "$work\registry-auth.json" --keep-manifest-list=true "$($srcRepo -replace ':[^:/]+$', '')@$digest" $dstRepo
  oc image info -a "$work\registry-auth.json" --filter-by-os=linux/amd64 "$($dstRepo -replace ':[^:/]+$', '')@$digest" | Select-Object -First 2
}
```

**Expected (mirror):** for each image, `oc image info` prints a `Name:` and a `Digest:` line for
your copy, read **by the original digest**: your registry holds the same bytes. **If not:**
`manifest unknown` means the copy changed the digest; copy that image again with
`skopeo copy --all --preserve-digests` (in WSL or a container). `unauthorized`: the push
credentials. A registry that is a pull-through cache of ghcr.io needs no copy: skip the loop.

**Path: images `mirror`, the registry needs credentials, and V9 found no pull secret**: create
one from an account that may pull (here the push account; use a read-only one if you have it),
then set `$pullSecret = 'atlas-pull-secret'` in your values file and run it again:

```powershell
oc create secret generic atlas-pull-secret -n $ns --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson="$work\registry-auth.json"
```

**All paths**: the pull test.

```powershell
$pullSecrets = if ($pullSecret) { "  imagePullSecrets: [{name: $pullSecret}]" } else { '' }
$sc = '{allowPrivilegeEscalation: false, runAsNonRoot: true, capabilities: {drop: [ALL]}, seccompProfile: {type: RuntimeDefault}}'
@"
apiVersion: v1
kind: Pod
metadata: {name: atlas-pull-test}
spec:
  restartPolicy: Never
$pullSecrets
  containers:
  - {name: api, image: '$apiImage', command: [python, -c, "print('pulled')"], securityContext: $sc}
  - {name: mcp, image: '$mcpImage', command: [python, -c, "print('pulled')"], securityContext: $sc}
  - {name: tls, image: '$tlsImage', command: [caddy, version], securityContext: $sc}
  - {name: db, image: '$pgImage', command: [postgres, --version], securityContext: $sc}
"@ | oc apply -n $ns -f -
oc wait pod/atlas-pull-test -n $ns --for=jsonpath='{.status.phase}'=Succeeded --timeout=5m
foreach ($c in 'api', 'mcp', 'tls', 'db') { "{0}: {1}" -f $c, (oc logs atlas-pull-test -n $ns -c $c) }
oc delete pod atlas-pull-test -n $ns
```

**Expected:** `pod/atlas-pull-test created`, `pod/atlas-pull-test condition met`, then
`api: pulled`, `mcp: pulled`, `tls: v2...` (Caddy's version), `db: postgres (PostgreSQL) 16...`,
and `pod "atlas-pull-test" deleted`.

**If not:** `oc wait` times out: run `oc describe pod atlas-pull-test -n $ns` and read the
`Events` at the end. `ErrImagePull` or `ImagePullBackOff` with `unauthorized` or `not found` on
ghcr.io: `$registry` is wrong or has capitals (V8). With `i/o timeout`, `connection refused` or a
policy message: the cluster may not pull from ghcr.io; set `$imageSource = 'mirror'` and
`$registry` to your registry, run the values file again, and repeat this step. With your
registry and `unauthorized`: the pull secret (the block above). `manifest unknown`: the copy
lacks that digest; copy again. Before you retry: `oc delete pod atlas-pull-test -n $ns`.

### 8. Create the peppers and back them up

Atlas has two **peppers**: random keys mixed into every stored password and every agent token.
They are made once and **never change for the life of the install**: new peppers sign everybody
out and make every token useless, and a database restored without its peppers cannot be signed in
to. This creates them and the Secret `atlas-peppers`, and records their fingerprints.

```powershell
python scripts/openshift/new-secrets.py -n $ns --out "$work\peppers" --dry-run
python scripts/openshift/new-secrets.py -n $ns --out "$work\peppers"
python scripts/openshift/pepper-fingerprint.py "$work\peppers\password_pepper" "$work\peppers\token_pepper" | Set-Content "$work\pepper-fingerprints.txt"
Get-Content "$work\pepper-fingerprints.txt"
oc get secret atlas-peppers -n $ns
```

**Expected:** the dry run prints an `oc ... create secret generic atlas-peppers ...` command with
file paths only, and changes nothing. The second line prints `new-secrets: wrote ...` with the two
file paths and `new-secrets: created Secret atlas-peppers (keys: password_pepper, token_pepper)`,
and no value. The fingerprint file has two lines,
`password_pepper  sha256:<64 hex>` and `token_pepper  sha256:<64 hex>`: fingerprints, never the
values; keep the file with your log. `oc get secret` shows `atlas-peppers  Opaque  2`.

**If not:** `refusing` because the folder already holds a pepper file: you ran this before. If
that run created the Secret (`oc get secret atlas-peppers -n $ns` finds it), go on. If not, run
the second line again with `--reuse` added. `refusing` because the Secret `atlas-peppers` already
exists: an earlier install left it; if that install never held data, add `--force`, otherwise
stop and find its peppers.

**Back the peppers up now**, before anyone stores real work.

**Path: `$keyVault` set** (Azure Key Vault). `--file` makes the Azure CLI read each value from
the file, so it is never on a command line:

```powershell
az keyvault secret set --vault-name $keyVault --name atlas-password-pepper --file "$work\peppers\password_pepper" --encoding utf-8 --query name -o tsv
az keyvault secret set --vault-name $keyVault --name atlas-token-pepper --file "$work\peppers\token_pepper" --encoding utf-8 --query name -o tsv
```

**Expected:** `atlas-password-pepper`, then `atlas-token-pepper`.

**Path: `$keyVault` empty**: store the two files `password_pepper` and `token_pepper` from
`$work\peppers` in your organisation's secret vault, as files, unchanged (a changed line ending
changes the fingerprint).

Restoring the peppers, and proving later that the vault and the pods hold the same ones, is in
[OpenShift: creating the peppers and backing them up](../../docs/oss/guides/openshift-secrets.md).

### 9. Write the overlay

An overlay is the small set of files that says how this install differs from the base: the
namespace, the host, the images, which optional parts are on. This writes yours into
`deploy\openshift\overlays\ours`, entirely from your values: nothing is edited by hand.

```powershell
New-Item -ItemType Directory -Force $overlay | Out-Null

# Whose client address atlas-api and atlas-tls believe: the pod network.
@"
apiVersion: apps/v1
kind: Deployment
metadata: {name: atlas-api}
spec:
  template:
    spec:
      containers:
        - name: atlas-api
          env:
            - {name: ATLAS_TRUSTED_PROXY_CIDRS, value: '$($podNet.Trim() -replace '\s+', ',')'}
"@ | Set-Content "$overlay\patch-trusted-proxy.yaml"
@"
apiVersion: apps/v1
kind: Deployment
metadata: {name: atlas-tls}
spec:
  template:
    spec:
      containers:
        - name: atlas-tls
          env:
            - {name: CADDY_TRUSTED_PROXIES, value: '$($podNet.Trim() -replace '\s+', ' ')'}
"@ | Set-Content "$overlay\patch-tls-trusted-proxy.yaml"

# The settings that differ from the base: the host, the privacy text, the seed.
@"
ATLAS_PUBLIC_HOST=$atlasHost
ATLAS_PUBLIC_URL=https://$atlasHost
ATLAS_MCP_ALLOWED_HOSTS=$atlasHost,${atlasHost}:443,localhost:*,127.0.0.1:*
ATLAS_MCP_ALLOWED_ORIGINS=https://$atlasHost
ATLAS_PRIVACY_CONTACT=$privacyContact
ATLAS_PRIVACY_HOSTING=One PostgreSQL database (CloudNativePG) in this organisation's own OpenShift cluster. It is not copied to a third party.
ATLAS_SEED=$seed
"@ | Set-Content "$overlay\atlas.env"

# The kustomization, from your choices.
$components = @()
if ($dbMode -eq 'overlay') { $components += '  - ../../components/cnpg' }
if ($netpol -eq 'on')      { $components += '  - ../../components/networkpolicies' }
if ($dbMode -eq 'file')    { $components += '  - ../../components/cnpg-external' }
$names = 'api', 'mcp', 'tls'
$images = for ($i = 0; $i -lt 3; $i++) {
  "  - name: registry.example.com/gradinai-atlas/gradinai-atlas-$($names[$i])-ubi`n" +
  "    newName: $registry/gradinai-atlas-$($names[$i])-ubi`n" +
  "    newTag: $release`n" +
  "    digest: $($digests[$i])"
}
$labels = @("  - pairs: {app.kubernetes.io/version: $release}", '    includeSelectors: false', '    includeTemplates: true')
if ($orgLabels.Count) {
  $labels += '  - pairs: {' + (($orgLabels.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name): '$($_.Value)'" }) -join ', ') + '}'
  $labels += '    includeSelectors: false', '    includeTemplates: true'
  if ($dbMode -eq 'overlay') { $labels += '    fields: [{kind: Cluster, path: spec/inheritedMetadata/labels, create: true}]' }
}
$patches = @(
  "  - path: patch-trusted-proxy.yaml`n    target: {kind: Deployment, name: atlas-api}",
  "  - path: patch-tls-trusted-proxy.yaml`n    target: {kind: Deployment, name: atlas-tls}")
if ($netpol -eq 'on' -and $dbMode -eq 'overlay') {
  $patches += "  - target: {kind: NetworkPolicy, name: atlas-database-from-operator}`n    patch: |-`n      - {op: replace, path: /spec/ingress/0/from/0/namespaceSelector/matchLabels/kubernetes.io~1metadata.name, value: $cnpgNs}"
}
if ($routerShard) {
  $key, $value = $routerShard -split '=', 2
  $patches += "  - target: {kind: Route, name: atlas}`n    patch: |-`n      - {op: add, path: /metadata/labels/$($key -replace '/', '~1'), value: '$value'}"
}
if ($ipAllowlist) {
  $patches += "  - target: {kind: Route, name: atlas}`n    patch: |-`n      - {op: add, path: /metadata/annotations/haproxy.router.openshift.io~1ip_allowlist, value: '$($ipAllowlist.Trim())'}"
}
if ($pullSecret) {
  $patches += "  - target: {kind: ServiceAccount, name: atlas}`n    patch: |-`n      - {op: add, path: /imagePullSecrets, value: [{name: $pullSecret}]}"
}
if ($dbMode -eq 'overlay') {
  $db = @()
  if ($imageSource -eq 'mirror') { $db += "      - {op: replace, path: /spec/imageName, value: '$pgImage'}" }
  if ($pullSecret)   { $db += "      - {op: add, path: /spec/imagePullSecrets, value: [{name: $pullSecret}]}" }
  if ($storageClass) { $db += "      - {op: add, path: /spec/storage/storageClass, value: $storageClass}", "      - {op: add, path: /spec/walStorage/storageClass, value: $storageClass}" }
  if ($size -eq 'trial') {
    $db += '      - {op: replace, path: /spec/instances, value: 1}', '      - {op: add, path: /spec/enablePDB, value: false}',
           '      - {op: replace, path: /spec/storage/size, value: 5Gi}', '      - {op: replace, path: /spec/walStorage/size, value: 2Gi}',
           "      - {op: replace, path: /spec/resources, value: {requests: {cpu: 250m, memory: 512Mi}, limits: {cpu: '1', memory: 1Gi}}}"
  }
  if ($db) { $patches += "  - target: {kind: Cluster, name: atlas-db}`n    patch: |-`n" + ($db -join "`n") }
}
if ($size -eq 'trial') {
  foreach ($d in 'atlas-api', 'atlas-tls') {
    $patches += "  - target: {kind: Deployment, name: $d}`n    patch: |-`n      - {op: replace, path: /spec/replicas, value: 1}"
    $patches += "  - target: {kind: PodDisruptionBudget, name: $d}`n    patch: |-`n      `$patch: delete`n      apiVersion: policy/v1`n      kind: PodDisruptionBudget`n      metadata: {name: $d}"
  }
}
@"
# This install's overlay, written by deploy/openshift/FIRST-INSTALL.md, step 9.
# Choices: project $nsMode, images $imageSource, database $dbMode, NetworkPolicies $netpol, size $size.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: $ns

resources:
  - ../../base

components:
$($components -join "`n")

images:
$($images -join "`n")

labels:
$($labels -join "`n")

configMapGenerator:
  - name: atlas-env
    behavior: merge
    envs: [atlas.env]
    options: {disableNameSuffixHash: true}

# The Route's host is ATLAS_PUBLIC_HOST, copied, so the two never differ.
replacements:
  - source: {kind: ConfigMap, name: atlas-env, fieldPath: data.ATLAS_PUBLIC_HOST}
    targets:
      - select: {kind: Route}
        fieldPaths: [spec.host]
        options: {create: true}

patches:
$($patches -join "`n")
"@ | Set-Content "$overlay\kustomization.yaml"
Get-Content "$overlay\kustomization.yaml"
```

**Expected**, on the default path (`new`, `ghcr`, `overlay`, `on`, `standard`), with your
namespace, registry and operator namespace in place of the ones shown:

```yaml
# This install's overlay, written by deploy/openshift/FIRST-INSTALL.md, step 9.
# Choices: project new, images ghcr, database overlay, NetworkPolicies on, size standard.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: atlas

resources:
  - ../../base

components:
  - ../../components/cnpg
  - ../../components/networkpolicies

images:
  - name: registry.example.com/gradinai-atlas/gradinai-atlas-api-ubi
    newName: ghcr.io/<owner>/gradinai-atlas-api-ubi
    newTag: v0.0.2-alpha
    digest: sha256:351309bef465f82977ddc0c2983978fe89cba976ad2f356e7855bc3c39423f60
  - name: registry.example.com/gradinai-atlas/gradinai-atlas-mcp-ubi
    newName: ghcr.io/<owner>/gradinai-atlas-mcp-ubi
    newTag: v0.0.2-alpha
    digest: sha256:d3ad435cae231327c4cbd96aef3afb5fa60b96b7e0d04e464f4b1d971d1e56e7
  - name: registry.example.com/gradinai-atlas/gradinai-atlas-tls-ubi
    newName: ghcr.io/<owner>/gradinai-atlas-tls-ubi
    newTag: v0.0.2-alpha
    digest: sha256:1c80ebfe57035a851aaa10bede3e98b1943f0453ab7a7ec17b116e39d02f27ad

labels:
  - pairs: {app.kubernetes.io/version: v0.0.2-alpha}
    includeSelectors: false
    includeTemplates: true

configMapGenerator:
  - name: atlas-env
    behavior: merge
    envs: [atlas.env]
    options: {disableNameSuffixHash: true}

# The Route's host is ATLAS_PUBLIC_HOST, copied, so the two never differ.
replacements:
  - source: {kind: ConfigMap, name: atlas-env, fieldPath: data.ATLAS_PUBLIC_HOST}
    targets:
      - select: {kind: Route}
        fieldPaths: [spec.host]
        options: {create: true}

patches:
  - path: patch-trusted-proxy.yaml
    target: {kind: Deployment, name: atlas-api}
  - path: patch-tls-trusted-proxy.yaml
    target: {kind: Deployment, name: atlas-tls}
  - target: {kind: NetworkPolicy, name: atlas-database-from-operator}
    patch: |-
      - {op: replace, path: /spec/ingress/0/from/0/namespaceSelector/matchLabels/kubernetes.io~1metadata.name, value: cnpg-system}
```

What each other choice changes in that file (`-` a line that goes, `+` a line that comes; the
step writes this for you):

**Path: database `file`.** The database comes from `atlas-db.yaml` (step 10), which holds the
operator's namespace itself:

```diff
 components:
-  - ../../components/cnpg
   - ../../components/networkpolicies
+  - ../../components/cnpg-external
 ...
-  - target: {kind: NetworkPolicy, name: atlas-database-from-operator}
-    patch: |-
-      - {op: replace, path: /spec/ingress/0/from/0/namespaceSelector/matchLabels/kubernetes.io~1metadata.name, value: cnpg-system}
```

**Path: NetworkPolicies `off`.**

```diff
 components:
   - ../../components/cnpg
-  - ../../components/networkpolicies
 ...
-  - target: {kind: NetworkPolicy, name: atlas-database-from-operator}
-    patch: |-
-      - {op: replace, path: /spec/ingress/0/from/0/namespaceSelector/matchLabels/kubernetes.io~1metadata.name, value: cnpg-system}
```

**Path: images `mirror`**, with a pull secret (without one, the two `imagePullSecrets` lines are
absent; on database `file` the whole `Cluster` entry is absent, because step 10 writes the image
and the pull secret into `atlas-db.yaml`):

```diff
-    newName: ghcr.io/<owner>/gradinai-atlas-api-ubi
+    newName: registry.example.com/team/atlas/gradinai-atlas-api-ubi
   (the same for mcp and tls; newTag and digest stay)
 ...
+  - target: {kind: ServiceAccount, name: atlas}
+    patch: |-
+      - {op: add, path: /imagePullSecrets, value: [{name: change-me-pull-secret}]}
+  - target: {kind: Cluster, name: atlas-db}
+    patch: |-
+      - {op: replace, path: /spec/imageName, value: 'registry.example.com/team/atlas/postgresql:<same tag>@sha256:<same digest>'}
+      - {op: add, path: /spec/imagePullSecrets, value: [{name: change-me-pull-secret}]}
```

**Path: size `trial`** (on database `file` the `Cluster` entry is absent: step 10 writes it into
`atlas-db.yaml`):

```diff
+  - target: {kind: Cluster, name: atlas-db}
+    patch: |-
+      - {op: replace, path: /spec/instances, value: 1}
+      - {op: add, path: /spec/enablePDB, value: false}
+      - {op: replace, path: /spec/storage/size, value: 5Gi}
+      - {op: replace, path: /spec/walStorage/size, value: 2Gi}
+      - {op: replace, path: /spec/resources, value: {requests: {cpu: 250m, memory: 512Mi}, limits: {cpu: '1', memory: 1Gi}}}
+  - target: {kind: Deployment, name: atlas-api}
+    patch: |-
+      - {op: replace, path: /spec/replicas, value: 1}
+  - target: {kind: PodDisruptionBudget, name: atlas-api}
+    patch: |-
+      $patch: delete
+      apiVersion: policy/v1
+      kind: PodDisruptionBudget
+      metadata: {name: atlas-api}
+  (the same two entries for atlas-tls)
```

**A storage class** (`$storageClass` set; database `overlay` only, `file` writes it in step 10):

```diff
+  - target: {kind: Cluster, name: atlas-db}
+    patch: |-
+      - {op: add, path: /spec/storage/storageClass, value: managed-csi}
+      - {op: add, path: /spec/walStorage/storageClass, value: managed-csi}
```

**An internal router shard** (`$routerShard` set, here `router=internal`), and **the stopgap
allowlist** (`$ipAllowlist` set):

```diff
+  - target: {kind: Route, name: atlas}
+    patch: |-
+      - {op: add, path: /metadata/labels/router, value: 'internal'}
+  - target: {kind: Route, name: atlas}
+    patch: |-
+      - {op: add, path: /metadata/annotations/haproxy.router.openshift.io~1ip_allowlist, value: 'change-me-office-range/24 change-me-vpn-range/20'}
```

**Required labels** (`$orgLabels` set):

```diff
 labels:
   - pairs: {app.kubernetes.io/version: v0.0.2-alpha}
     includeSelectors: false
     includeTemplates: true
+  - pairs: {example.com/cost-center: 'change-me'}
+    includeSelectors: false
+    includeTemplates: true
+    fields: [{kind: Cluster, path: spec/inheritedMetadata/labels, create: true}]
```

`includeSelectors: false` keeps the labels out of the selectors (a Deployment's selector cannot
change after it is created); `includeTemplates` puts them on the pods; the `fields` line puts them
on the database pods too. On database `file` the `fields` line is absent; step 10 says where those
labels go.

**If not:** the file differs from the expected one in a line your choices do not explain: check
the choices in your values file for a typo, run it again, and run this step again. It overwrites
the four files.

### 10. Install the database first (database `file` only)

**Path: database `overlay`**: skip this step. The overlay creates the database in step 12.

**Path: database `file`**: this makes your copy of `atlas-db.yaml`, writes your values into its
lines marked `# EDIT:`, and installs the database on its own, before Atlas.

```powershell
Copy-Item deploy\openshift\database\atlas-db.yaml $dbFile -Force
$t = Get-Content -Raw $dbFile
$t = $t -replace '(?m)^(\s+kubernetes\.io/metadata\.name: )cnpg-system(\s+# EDIT:)', "`${1}$cnpgNs`${2}"
if ($imageSource -eq 'mirror') { $t = $t -replace '(?m)^(  imageName: )\S+', "`${1}$pgImage" }
if ($pullSecret) {
  $t = $t -replace '(?m)^  # imagePullSecrets:', '  imagePullSecrets:'
  $t = $t -replace '(?m)^  #   - name: change-me-pull-secret', "    - name: $pullSecret"
}
if ($storageClass) { $t = $t -replace '(?m)^    # storageClass: change-me-storage-class', "    storageClass: $storageClass" }
if ($size -eq 'trial') {
  $t = $t -replace '(?m)^  instances: 2', '  instances: 1'
  $t = $t -replace '(?m)^  # enablePDB: false', '  enablePDB: false'
  $t = $t -replace '(?m)^(    size: )20Gi', '${1}5Gi' -replace '(?m)^(    size: )5Gi(\s+# EDIT: WAL)', '${1}2Gi${2}'
  $t = $t -replace '(?ms)^(  resources:.*?cpu: )"2"(.*?memory: )2Gi(.*?cpu: )500m(.*?memory: )1Gi', '${1}"1"${2}1Gi${3}250m${4}512Mi'
}
Set-Content -Path $dbFile -Value $t -NoNewline
Select-String -Path $dbFile -Pattern '^[^#]*# EDIT:|^\s+(enablePDB|cpu|memory|- name):' | ForEach-Object { $_.Line.Trim() }
```

**Expected:** the lines you may edit, with your values. On the defaults (community CloudNativePG,
`standard`, no mirror, no pull secret, no storage class) exactly:

```text
apiVersion: postgresql.cnpg.io/v1    # EDIT: postgresql.k8s.enterprisedb.io/v1 on EDB Postgres for Kubernetes
imageName: ghcr.io/cloudnative-pg/postgresql:16.15-202611020810-standard-trixie@sha256:b67681006859e6572798d498fc1755016fe4808efecc9fa8bd48059de4efaaeb    # EDIT: your mirror
instances: 2    # EDIT: 2 = a primary and a hot standby; 3 if your standard says so; 1 only for a trial (no standby)
resources:    # EDIT: per instance
cpu: "2"
memory: 2Gi
cpu: 500m
memory: 1Gi
size: 20Gi    # EDIT: data volume per instance
size: 5Gi    # EDIT: WAL volume per instance
apiVersion: postgresql.cnpg.io/v1    # EDIT: postgresql.k8s.enterprisedb.io/v1 on EDB Postgres for Kubernetes
kubernetes.io/metadata.name: cnpg-system    # EDIT: the operator's namespace
```

On `trial`: `instances: 1`, then `enablePDB: false`, `cpu: "1"`, `memory: 1Gi`, `cpu: 250m`,
`memory: 512Mi`, `size: 5Gi` and `size: 2Gi`. With `mirror`: your `$pgImage` on the `imageName`
line. With a pull secret: `imagePullSecrets:` and `- name: <$pullSecret>`. With a storage
class: two `storageClass:` lines. The last line names your `$cnpgNs`.

**Path: database `file` with `$orgLabels` set**: open `$dbFile` in an editor and add each label
under every `labels:` block (the four objects' `metadata.labels`, and the Cluster's
`inheritedMetadata.labels`), at the same indentation as `app.kubernetes.io/name: atlas`. Never
under `matchLabels:`.

Then install it and wait until it is healthy:

```powershell
oc apply -f $dbFile -n $ns --dry-run=server
oc apply -f $dbFile -n $ns
oc wait clusters.postgresql.cnpg.io/atlas-db -n $ns --for=condition=Ready --timeout=15m
oc get clusters.postgresql.cnpg.io atlas-db -n $ns
(oc get secret atlas-db-app -n $ns -o json | ConvertFrom-Json).data.PSObject.Properties.Name -join ' '
```

**Expected:** the dry run prints four lines ending in `(server dry run)`. The apply prints
`cluster.postgresql.cnpg.io/atlas-db created`, `database.postgresql.cnpg.io/atlas-db-atlas
created` and two `networkpolicy.networking.k8s.io/atlas-database... created`. After a few minutes:
`cluster.postgresql.cnpg.io/atlas-db condition met`, then a line with `Cluster in healthy state`,
`INSTANCES 2` and `READY 2` (`1` and `1` on `trial`). The last line lists the key names of the
connection Secret (never the values) and includes `fqdn-uri`.

**If not:** the `oc wait` times out: `oc get clusters.postgresql.cnpg.io,pvc,pods -n $ns`, then
rows T7 (volumes `Pending`), T8 (no status at all) and T9 (`Instance Status Extraction Error`).
No `fqdn-uri`: row T12.

### 11. Render the overlay and dry-run it

This builds what will be applied and sends it to the API server without creating anything: a
missing right, an unknown type, a quota refusal or an admission policy shows up here.

```powershell
oc kustomize $overlay | Set-Content "$work\rendered.yaml"
Select-String -Path "$work\rendered.yaml" -Pattern '^kind: (\w+)' | ForEach-Object { $_.Matches[0].Groups[1].Value } | Group-Object | Sort-Object Name | ForEach-Object { "$($_.Count) $($_.Name)" }
Select-String -Path "$work\rendered.yaml" -Pattern '^\s+(- )?(image|imageName|host|namespace): (\S+)' | ForEach-Object { $_.Line.Trim() -replace '^- ', '' } | Sort-Object -Unique
oc apply -k $overlay --dry-run=server
```

**Expected:** first a count per kind. On the default path:

```text
1 Cluster
4 ConfigMap
1 CronJob
1 Database
3 Deployment
1 Job
8 NetworkPolicy
2 PodDisruptionBudget
1 Route
3 Service
1 ServiceAccount
```

On database `file`: no `Cluster`, no `Database`, and `6 NetworkPolicy` (the database's two are
in `atlas-db.yaml`). With NetworkPolicies `off`: no `NetworkPolicy` line. On `trial`: no
`PodDisruptionBudget` line. Then the `host`, `image`, `imageName` and `namespace` lines: only
your host, only images from your registry (and the database image) that end in `@sha256:` and a
digest, and only your namespace. The dry run prints one line per object, each ending in
`(server dry run)`.

**If not:** `oc kustomize` fails with `testing value /kind failed`: the overlay lists both `cnpg`
and `cnpg-external`; run step 9 again. Any other `oc kustomize` error names the file and line;
fix the value it names and run step 9 again. The dry run is refused with `forbidden`: step 3.
With `exceeded quota`: step 4. With a message from an admission webhook or policy naming a label:
row T16.

### 12. Apply

This creates every Atlas object at once. Pods that need something not yet there (the database's
Secrets, the TLS certificates) wait for it by themselves.

```powershell
oc apply -k $overlay
```

**Expected:** one line per object, each ending in `created` (`configured` or `unchanged` when
you apply again): `serviceaccount/atlas`, the ConfigMaps, the Services,
`deployment.apps/atlas-api`, `atlas-mcp` and `atlas-tls`, `job.batch/atlas-migrate-...`,
`cronjob.batch/atlas-audit-purge`, the PodDisruptionBudgets, the NetworkPolicies,
`route.route.openshift.io/atlas`, and on database `overlay` also
`cluster.postgresql.cnpg.io/atlas-db` and `database.postgresql.cnpg.io/atlas-db-atlas`.

**If not:** the apply is refused although the dry run passed: the message names the object; the
"If not" of step 11 applies. `field is immutable` on the Job: row T10.

### 13. Check that the Route is admitted

The Route is the only way in from outside: the router ends the browser's TLS connection with the
platform's certificate and opens a new TLS connection to `atlas-tls` (re-encrypt). This checks
at once that the router accepted it.

```powershell
oc get route atlas -n $ns
oc get route atlas -n $ns -o jsonpath='{range .status.ingress[*]}{.routerName}{" "}{.conditions[?(@.type=="Admitted")].status}{" "}{.conditions[?(@.type=="Admitted")].reason}{"\n"}{end}'
```

**Expected:** `HOST/PORT` is your `$atlasHost`, `SERVICES` is `atlas-tls`, `TERMINATION` is
`reencrypt/Redirect`. The second command prints `default True` (the router's name and `True`).
**Path: `$routerShard` set**: it prints one line, with the internal router's name and `True`,
and **no** `default` line: a `default` line means the internet-facing router serves Atlas too;
stop and ask the platform team to exclude the label from the default router's `routeSelector`.

**If not:** `False HostAlreadyClaimed`: another Route has this host; choose another `$atlasHost`
(V3), run the values file, then steps 9 and 12 again. `False` with another reason, or a message
about `reencrypt` or the TLS settings: the cluster refuses re-encrypt Routes; stop and tell the
platform team and the Atlas maintainers ([assumption 4](#appendix-a-the-assumptions)). Nothing
printed after a minute: no router serves this namespace; ask the platform team (a router with a
namespace or route selector).

### 14. Wait for the database, the migration and the pods

This waits for each part in the order they depend on each other: the database, then the migrate
Job that creates the tables, then the three Deployments.

```powershell
oc wait clusters.postgresql.cnpg.io/atlas-db -n $ns --for=condition=Ready --timeout=15m
oc wait job/$job -n $ns --for=condition=complete --timeout=10m
oc logs -n $ns (oc get pod -n $ns -l job-name=$job --field-selector=status.phase=Succeeded -o name) -c atlas-migrate --tail=1
oc rollout status deploy/atlas-api -n $ns --timeout=10m
oc rollout status deploy/atlas-mcp -n $ns --timeout=5m
oc rollout status deploy/atlas-tls -n $ns --timeout=5m
oc get pods -n $ns -l app.kubernetes.io/name=atlas
```

**Expected:** `cluster.postgresql.cnpg.io/atlas-db condition met` (at once on database `file`;
after a few minutes on `overlay`: the operator makes the volumes, the first instance, then the
standby). `job.batch/atlas-migrate-... condition met`. The Job's last log line starts with
`[atlas-migrate] upgrade:`. Each rollout prints `deployment "atlas-..." successfully rolled
out`, perhaps after some `Waiting for deployment ... rollout to finish` lines. The pod list shows `atlas-api-...` (two, or one on `trial`), `atlas-mcp-...` (one) and
`atlas-tls-...` (two, or one) `1/1 Running`, `atlas-db-1` (and `atlas-db-2`) `1/1 Running`, and
one migrate Job pod `0/1 Completed`. On database `overlay`, one to three more pods of that Job show
`0/1 Error`: they ran before PostgreSQL accepted connections (`Connection refused` in their log),
and the Job retried, which is what it is built to do. `atlas-api` may show a restart or two: it starts before the
database accepts connections and its startup probe gives up once; that settles by itself.

**If not:** the Cluster wait times out: rows T7, T8, T9. The Job wait times out, or the Job shows
`Failed`: row T10. A rollout times out: `oc describe pod <the pod> -n $ns`. `ContainerCreating`
for minutes: rows T11 and T12. `ImagePullBackOff`: row T3. Restarts with `421` in the events:
row T1. `atlas-tls` stays `0/1`: row T15.

### 15. Check that the pod network was understood

`atlas-api` believes the client address only from inside the pod network you gave (V4). A value
it cannot parse is only logged, and then every sign-in is counted against the proxy's address.
This checks the log.

```powershell
oc logs deploy/atlas-api -n $ns -c atlas-api | Select-String -Pattern 'unparseable'
```

**Expected:** only `Found 2 pods, using pod/atlas-api-...` (`oc` saying which pod's log it read;
nothing at all on `trial`).

**If not:** a line with `unparseable entry`: `$podNet` is not a list of CIDRs; fix V4, run the
values file, then steps 9 and 12 again (the Deployment rolls by itself).

### 16. Create the first owner account

This creates your own account, with the owner role, from inside an `atlas-api` pod. The temporary
password goes to a file, never to the screen.

```powershell
$pod = oc get pod -n $ns -l app.kubernetes.io/component=api -o jsonpath='{.items[0].metadata.name}'
oc exec -n $ns $pod -c atlas-api -- atlas-admin --actor $actor user add $adminUser --role owner --break-glass --out /tmp/first-owner.password
oc exec -n $ns $pod -c atlas-api -- cat /tmp/first-owner.password | Set-Content "$work\first-owner.password"
oc exec -n $ns $pod -c atlas-api -- rm /tmp/first-owner.password
(Get-Item "$work\first-owner.password").Length -gt 0
```

`$pod` names one pod on purpose: the password file exists only in the pod that wrote it.
`--actor` records who ran the command in the audit trail. `--break-glass` marks the one account
whose password still works if sign-in is later switched to single sign-on only.

**Expected:** the second command prints a line starting `created <your username>  id=...
role=owner  break-glass`, followed by notes on the break-glass account and the temporary password
(valid 72 hours, single use); the last line prints `True`. The temporary password is in `$work\first-owner.password`, readable by you alone.

**If not:** `already exists`: the account was made before. If you lost its password, run the
second line again with `user reset-password $adminUser` in place of
`user add $adminUser --role owner --break-glass`, then the rest of the block. `error:
unable to upgrade connection` or `not found`: the pod was replaced; run the block again from its
first line.

### 17. Sign in

This proves the whole path from your browser through the router to Atlas, and records one failed
sign-in from your address, which step 18 checks.

1. Run `"https://$atlasHost/"` to print the address, and open it in your browser.
2. Sign in **once on purpose with a wrong password** for `$adminUser`. Expected: the sign-in page
   again, with an error.
3. Sign in with `$adminUser` and the password in `$work\first-owner.password`. Expected: a forced
   password change. Choose the new password and keep it in your vault: it is the break-glass
   password. Expected after the change: the board.
4. Delete the temporary password: `Remove-Item "$work\first-owner.password"`.

**If not:** the browser warns about the certificate: do not click through (the next request
carries your password); row T6. `421 Misdirected Request`: row T1. `503 Application is not
available`: row T5.

### 18. Verify the install

This checks, through the Route and from inside the namespace, the five things the install
promises: the front door answers, the MCP endpoint works end to end, Atlas records your real
address, the NetworkPolicies hold, and Atlas is not on the internet. It uses a short-lived token for your account.

```powershell
$pod = oc get pod -n $ns -l app.kubernetes.io/component=api -o jsonpath='{.items[0].metadata.name}'
oc exec -n $ns $pod -c atlas-api -- atlas-admin --actor $actor token mint --user $adminUser --purpose install-check --scopes read,admin --days 1 --out /tmp/install-check.token
'Authorization: Bearer ' + (oc exec -n $ns $pod -c atlas-api -- cat /tmp/install-check.token) | Set-Content "$work\install-check.header"
oc exec -n $ns $pod -c atlas-api -- rm /tmp/install-check.token
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"install-check","version":"1"}}}' | Set-Content -Path "$work\mcp-initialize.json" -NoNewline
```

**Expected:** `minted a token for <you>'s agent <you>-install-check  id=...  prefix=...
scopes=read,admin  written to /tmp/install-check.token` and its expiry; never the token itself (it
goes only into the header file).

**a. The front door.**

```powershell
curl.exe -sS -w "`n" "https://$atlasHost/healthz"
curl.exe -sS -o NUL -w "%{http_code} %{redirect_url}`n" "http://$atlasHost/"
```

**Expected:** `{"ok":true}`, then `302 https://<$atlasHost>/` (plain HTTP is redirected).

**b. MCP through the Route**, first without a token, then with it:

```powershell
curl.exe -sS -o NUL -w "%{http_code}`n" -X POST "https://$atlasHost/mcp/" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' --data-binary "@$work\mcp-initialize.json"
curl.exe -sS -X POST "https://$atlasHost/mcp/" -H "@$work\install-check.header" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' --data-binary "@$work\mcp-initialize.json"
```

**Expected:** `401` (atlas-mcp asks for a token), then `event: message` and a `data:` line that
contains `"serverInfo":{"name":"atlas"`.
The second call proves the whole chain: the router, `atlas-tls`, `atlas-mcp`, and back through
`atlas-tls` to `atlas-api`, which checked the token.

**c. Your address in the audit trail.**

```powershell
curl.exe -sS -H "@$work\install-check.header" "https://$atlasHost/api/audit/export?kinds=auth_event" | ConvertFrom-Json | Select-Object at, event, ip | Format-Table -AutoSize
```

**Expected:** among the lines (`user_created`, `password_changed`, `token_created` and others), a
`login_fail` (step 17's wrong password) and a `login_ok`, with the same `ip`: your machine's address as the cluster sees it (behind a company proxy or NAT,
the proxy's). It is **not** inside `$podNet`.

**d. The NetworkPolicies hold** (NetworkPolicies `on`): `atlas-mcp` must not reach the database;
`atlas-api` must.

```powershell
$mcp = oc get pod -n $ns -l app.kubernetes.io/component=mcp -o jsonpath='{.items[0].metadata.name}'
oc exec -n $ns $mcp -c atlas-mcp -- python -c "import socket; socket.create_connection(('atlas-db-rw', 5432), 5); print('open')"
oc exec -n $ns $pod -c atlas-api -- python -c "import socket; socket.create_connection(('atlas-db-rw', 5432), 5); print('open')"
```

**Expected:** the first ends in `TimeoutError: timed out` (blocked, as it must be) and
`command terminated with exit code 1`; the second prints `open`. With NetworkPolicies `off`,
both print `open`.

**e. Not reachable from outside the corporate network.** First see where the name points, from
your machine; then try the health check from a machine **outside** the organisation's network
(a phone on mobile data with its browser, or a computer at home: open
`https://<$atlasHost>/healthz`).

```powershell
Resolve-DnsName $atlasHost -Type A | Select-Object Name, IPAddress
```

**Expected:** an `IPAddress` inside your organisation's private address ranges (an internal
load balancer). From outside: the name does not resolve, or the connection times out or is
reset, and never shows `{"ok":true}`. With only the stopgap `$ipAllowlist` (V16), the name may
resolve to a public address, but the connection from outside must still fail.

**f. Revoke the check token and delete its file.**

```powershell
oc exec -n $ns $pod -c atlas-api -- atlas-admin --actor $actor token revoke --user $adminUser
Remove-Item "$work\install-check.header"
```

**Expected:** `revoked <id> (prefix ...)` and `1 token(s) of <you> revoked.` The command revokes every token of your
account's agents; at this point the check token is the only one.

**If not:** a. `Could not resolve host`: the apps domain's DNS; check V2 and V3. A certificate
error: row T6. `503`: row T5. b. `421`: row T1. `502`: row T15. `401` on the second call too: the
header file is empty; run the first block of this step again. c. The `ip` is inside `$podNet`
(often the same address on every line): row T14. No `login_fail` line: do step 17's wrong
password again. d. The first call prints `open` with NetworkPolicies `on`: the policies are not
enforced. `oc get networkpolicy -n $ns` must list `atlas-mcp`; if it does, tell the platform team
(the cluster's network plugin must enforce NetworkPolicy). The second call times out: row T15.
e. `{"ok":true}` from outside, or a public address without an allowlist: row T17.

The install is done. Write the date, the release (`$release`) and your five choices into your log.

### 19. Connect Claude Code (optional)

Follow [Atlas for Claude Code](../../clients/claude-code/README.md), with three differences from
the Compose install it describes:

- **The token**: sign in to the board and mint your own under *Settings -> Agent credentials*.
- **The URL** in the MCP template is `https://<$atlasHost>/mcp/`.
- **The CA**: only if you imported a CA in row T6, point `NODE_EXTRA_CA_CERTS` at that file, as
  the client page's "Trusting the install's CA" says.

## Removing the install

This removes every Atlas object. **It deletes the data.** The database volumes belong to the
`Cluster`, so Kubernetes deletes them with it, and with them the disks when the storage class's
reclaim policy is `Delete` (the usual default, ARO's `managed-csi` included). To keep the data,
first take a backup ([`README.md`, "The database"](README.md#the-database-task-35)), or have the
platform team set the volumes' PersistentVolumes to `Retain` (a cluster-wide right):

```powershell
oc get pvc -n $ns -l app.kubernetes.io/component=database -o custom-columns=PVC:.metadata.name,PV:.spec.volumeName
# The platform team, for each PV listed:
#   oc patch pv <PV> --type=merge -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
```

Then remove, in this order:

```powershell
oc delete -k $overlay --wait=true
if ($dbMode -eq 'file') { oc delete -f $dbFile -n $ns --wait=true }
oc delete secret atlas-peppers atlas-api-tls atlas-mcp-tls atlas-tls-tls -n $ns --ignore-not-found
oc wait pvc -l app.kubernetes.io/component=database -n $ns --for=delete --timeout=5m
if ($nsMode -eq 'new') { oc delete project $ns }
oc get deploy,sts,svc,secret,configmap,pvc,route,pdb,job,cronjob,networkpolicy,serviceaccount,clusters.postgresql.cnpg.io -n $ns -o name | Select-String -Pattern '/atlas'
```

**Expected:** one `deleted` line per object; the `oc wait` returns once the database volumes are
gone (`persistentvolumeclaim/atlas-db-1 condition met`, one line per volume; `no matching
resources found` if they went first). On `new`, also `project.project.openshift.io "atlas"
deleted`, and the last command prints nothing or reports that the namespace is gone. On
`existing` the last command prints **nothing**: no Atlas object is left, and the other
applications' objects are untouched. **Never delete an existing, shared namespace.**

**If not:** the last command lists objects: delete each with `oc delete <the name printed> -n
$ns`. A PVC stays `Terminating`: a pod still uses it; `oc get pods -n $ns` shows which.

Retained disks stay as `Released` PersistentVolumes, to be bound by hand to a new install or
deleted by the platform team. Keep the peppers (step 8) for as long as you keep any backup of the
database: a backup cannot be signed in to without them.

## Appendix A: the assumptions

This install rests on working assumptions the platform team has not all confirmed yet. The step
in the third column checks each one; the last column says what changes if it is wrong.

| # | Assumption | Checked in | If it is wrong |
|---|---|---|---|
| 1 | The cluster pulls from ghcr.io without a pull secret. **Often wrong** on a company cluster | step 7 | choice b, `mirror` |
| 2 | The cluster is not in FIPS mode | ask the platform team | stop: these images are not built for FIPS. Tell the Atlas maintainers |
| 3 | OpenShift 4.20 or 4.21, the `restricted-v2` SCC (security context constraint, OpenShift's pod security rules), no tight LimitRange | steps 1, 4, 11 | a small quota: choice e, `trial`. A LimitRange below the limits: a patch on `resources` of the Deployment or Job concerned, untested; tell the Atlas maintainers |
| 4 | Re-encrypt Routes are allowed, with a host one label under the apps domain, on the router's default certificate | step 13 | a host outside the apps domain needs its certificate on the Route (`spec.tls.certificate`, `key`, `caCertificate`, by a patch in the overlay). Re-encrypt refused: tell the Atlas maintainers; edge or passthrough loses the client address |
| 5 | Nothing sits in front of the router (no Application Gateway or Front Door), and the router runs on the pod network | step 18 c | row T14 |
| 6 | Community CloudNativePG 1.25.3 or later (`postgresql.cnpg.io/v1`) and a default storage class | steps 5, 10, 14 | EDB: row T13. An older operator: row T12. No default class: V7 |
| 7 | No backup at first install | | before real work is stored: component `cnpg-objectstore-plugin` instead of `cnpg` ([`README.md`, "The database"](README.md#the-database-task-35)). Until then the standby is the only copy, and on `trial` there is none |
| 8 | No External Secrets Operator: plain Secrets from `new-secrets.py` | step 8 | the `external-secrets` component (planned) delivers `atlas-peppers` from Key Vault under the same name and keys |
| 9 | `oc apply -k` by hand, no GitOps | | Argo CD renders the same overlay |
| 10 | Image signatures are not enforced | step 7 | mirror the digests and sign them with the organisation's key |
| 11 | Local accounts first; single sign-on later | step 16 | the `oidc` component (planned) |
| 12 | Egress (outgoing traffic) open enough later for single sign-on | | the `networkpolicies` component limits egress to Atlas's own connections; the `oidc` and `retrieval` components (planned) bring theirs |
| 13 | The cluster's logging collects standard output | `oc logs` works | nothing to change |
| 14 | No service mesh | | keep the Atlas pods out of the mesh: Atlas's own TLS covers every hop |
| 15 | The project's own NetworkPolicies, if any, leave the CloudNativePG operator a way in, or Atlas's are on | step 2 | choice d, `on` |
| 16 | The router serving the Route is internal (load balancer scope `Internal`): Atlas is not reachable from the internet | steps 6, 13, 18 e | an internal router shard (V15), or as a stopgap the allowlist annotation (V16); row T17 |

## Appendix B: troubleshooting

Start with `oc get pods -n $ns` and `oc get events -n $ns --sort-by=.lastTimestamp`. A connection
blocked by a NetworkPolicy is not refused, it times out: the symptoms are waits, not errors.

| Row | What you see | Usually | What to do |
|---|---|---|---|
| T1 | `atlas-api` keeps restarting and `oc describe pod` shows its probes failing with `421`; or `421` in the browser or from `/mcp/` | the request's Host is not `ATLAS_PUBLIC_HOST` (the probes send it; Atlas refuses any other Host), or the Route's host was changed by hand | `oc get configmap atlas-env -n $ns -o jsonpath='{.data.ATLAS_PUBLIC_HOST}'` and `oc get route atlas -n $ns -o jsonpath='{.spec.host}'` must both print `$atlasHost`. Fix V3, run the values file, steps 9 and 12, then `oc rollout restart deploy/atlas-api deploy/atlas-mcp deploy/atlas-tls -n $ns` (a changed `atlas-env` does not roll the pods by itself) |
| T2 | `ModuleNotFoundError: No module named 'psycopg'` in `atlas-api`, the Job or the CronJob | the image is older than `v0.0.2-alpha` | check out this guide's release tag, run the values file, steps 9 and 12 |
| T3 | `ImagePullBackOff` or `ErrImagePull` | `oc describe pod` names the image and the reason | step 7, "If not" |
| T4 | Pods `CreateContainerConfigError`, or `ContainerCreating` on a missing Secret `atlas-peppers` | the Secret was created in another namespace | `oc get secret atlas-peppers -n $ns`; if it is missing, step 8 with `--reuse` added to its second line |
| T5 | The Route answers `503 Application is not available` | the router has no ready `atlas-tls` endpoint, or cannot reach it | `oc get endpoints atlas-tls -n $ns` must list addresses. Empty: `atlas-tls` is not ready (it is ready only when it reaches `atlas-api`; row T15). Listed, with NetworkPolicies `on`: step 6's label. Also `oc get route atlas -n $ns -o yaml` must show `termination: reencrypt` and **no** `destinationCACertificate` |
| T6 | The browser or `curl.exe` warns about the certificate | the router's certificate is from a CA your machine does not trust (the default ARO domain has a publicly trusted one) | ask the platform team which CA to trust. If it is the router's own: `oc get configmap default-ingress-cert -n openshift-config-managed -o jsonpath='{.data.ca-bundle\.crt}' \| Set-Content "$work\ingress-ca.crt"`, then `Import-Certificate -FilePath "$work\ingress-ca.crt" -CertStoreLocation Cert:\CurrentUser\Root` |
| T7 | The database stays in a setting-up phase; `oc get pvc -n $ns` shows `Pending` | no default storage class, or no storage quota | `oc describe pvc -n $ns` says which. A class: V7, then step 9 (database `file`: step 10) and step 12. Quota: ask for it, or choice e. Volume sizes are fixed once made: remove the database first ("Removing the install") if you change them |
| T8 | `oc get clusters.postgresql.cnpg.io -n $ns` shows no `STATUS` at all | the operator does not watch this namespace | ask the platform team to add the namespace to the operator's watch list |
| T9 | `STATUS` `Instance Status Extraction Error: HTTP communication issue`, or only `atlas-db-1` and never `atlas-db-2` | the operator cannot reach the database pods (port 8000): a deny-all policy with NetworkPolicies `off`, or the wrong operator namespace (V5) | `oc get networkpolicy atlas-database-from-operator -n $ns -o jsonpath='{.spec.ingress[0].from[0].namespaceSelector}'` must name V5. Fix V5 (or choose `on`), then steps 9 and 12 (database `file`: steps 10 and 12) |
| T10 | The migrate Job shows `Failed`, `oc wait job` times out, or `oc apply` says `field is immutable` on the Job | the Job started before the database accepted connections, it cannot reach the database, or an earlier Job of the same name is in the way | `oc logs job/$job -n $ns -c atlas-migrate` shows which. Then `oc delete job $job -n $ns` and step 12 again: the migration is safe to repeat. A `timeout` to `atlas-db-rw`: rows T9 and T15 |
| T11 | Pods in `ContainerCreating` for minutes; `oc describe pod` shows `FailedMount` for a `*-tls` Secret | the service CA (OpenShift's internal certificate issuer) has not issued the TLS Secrets | `oc get secret atlas-api-tls atlas-mcp-tls atlas-tls-tls -n $ns`; they appear within a minute of the Services. If never: ask the platform team whether the service CA operator is healthy |
| T12 | `FailedMount` with `couldn't find key fqdn-uri in Secret atlas-db-app`, or no `databases` resource in step 5 | the CloudNativePG operator is older than 1.25.3 / 1.26.1 | best: ask for 1.27 or later. Otherwise copy `deploy\openshift\overlays\example\patch-older-cnpg.yaml` into `$overlay` and add to the end of `$overlay\kustomization.yaml` the two `OLDER CLOUDNATIVEPG` entries of the example's `kustomization.yaml`, uncommented. On database `file`, also delete the last document (`Database`) of the copied patch and the `Database` document of `$dbFile`. Then step 12, and delete the waiting pods |
| T13 | Step 5 lists `clusters` only under `postgresql.k8s.enterprisedb.io` | the cluster runs EDB Postgres for Kubernetes | database `overlay`: add the line `  - ../../components/cnpg-edb` after the `cnpg` line in `$overlay\kustomization.yaml`. Database `file`: change `postgresql.cnpg.io/v1` to `postgresql.k8s.enterprisedb.io/v1` on the two `apiVersion:` lines marked `# EDIT:` in `$dbFile`. In every command, `clusters.postgresql.cnpg.io` becomes `clusters.postgresql.k8s.enterprisedb.io`. EDB is untested |
| T14 | Every audit line shows the same address inside `$podNet`, or a node's address | the router runs on the host network, or a gateway sits in front of it (assumption 5) | ask the platform team. Router on the host network: add the machine network (`oc get network.config cluster -o jsonpath='{.status.machineNetwork}'`, or ask) to `$podNet`, separated by a space, then steps 9 and 12. A gateway: tell the Atlas maintainers (the Route then needs `set-forwarded-headers: append` and the gateway's range) |
| T15 | `atlas-tls` stays `0/1`; or `502` from `/mcp/`; or the migrate Job or `atlas-api` time out on `atlas-db-rw` | a NetworkPolicy blocks one of Atlas's own connections, or DNS | `oc get networkpolicy -n $ns` must list `atlas-default-deny`, `atlas-dns`, `atlas-tls`, `atlas-api`, `atlas-mcp`, `atlas-db-clients`, `atlas-database` and `atlas-database-from-operator`. If they are all there, a cluster-wide AdminNetworkPolicy may deny the connection, which Atlas's policies cannot override (`oc get adminnetworkpolicy`, or ask the platform team). `could not resolve` in a log: DNS, which the policies allow on port 5353 to namespace `openshift-dns` |
| T16 | `oc apply` or its dry run is refused by an admission webhook or policy naming a label | the cluster requires labels of its own | put the labels into `$orgLabels` (V10), run the values file, then step 9 (and 10 on database `file`) and step 11 again |
| T17 | `https://<$atlasHost>/healthz` answers from outside the organisation's network, or the name resolves to a public address | the Route is served by an internet-facing router (V14 `External`) | at once, as a stopgap: set `$ipAllowlist` (V16), run the values file, steps 9 and 12; `oc get route atlas -n $ns -o jsonpath='{.metadata.annotations.haproxy\.router\.openshift\.io/ip_allowlist}'` must print it. Then ask the platform team for an internal router shard (V15) and move to it. A `default True` line in step 13 next to the shard's: the default router serves the label too; the platform team must exclude it there |

## Appendix C: what this install leaves out

On purpose, until the platform questions are answered: backups (assumption 7), single sign-on,
the retrieval add-on, External Secrets and a deployment pipeline. Treat the install as a trial
until those are in. The NetworkPolicies are on by default in this guide, but in the manifests they
are still an opt-in component ([`README.md`, "NetworkPolicies"](README.md#networkpolicies-task-34)).
