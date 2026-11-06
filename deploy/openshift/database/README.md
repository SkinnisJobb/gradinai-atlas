# Atlas's database, installed on its own with plain YAML

[`atlas-db.yaml`](atlas-db.yaml) is Atlas's PostgreSQL database as a CloudNativePG cluster, in
one file, applied with `oc apply -f` the way a CloudNativePG cluster usually is. Use it to put the
database in place **before, and separately from, Atlas**: for example when the database is the
platform team's to create, or to see the database healthy before anything else is installed.

The other way, the default, is to let the Atlas overlay create the database with everything else
(component `components/cnpg`, [`../FIRST-INSTALL.md`](../FIRST-INSTALL.md)). Choose one: a
database installed from this file is then **left out of the overlay**
([below](#then-install-atlas-without-its-own-database)).

All commands are PowerShell 7 with `oc` logged in, run from this directory. Replace `atlas` with
your project's name wherever `-n atlas` appears.

## What it creates

| Object | What it is |
|---|---|
| `Cluster` `atlas-db` | PostgreSQL 16 on CloudNativePG's `standard` image (it carries pgvector), 2 instances (a primary and a hot standby, on different zones when there are zones), database `atlas` owned by `atlas`, the operator's own CA, TLS required. **No backup.** |
| `Database` `atlas-db-atlas` | the `atlas` database, declared; deleting it leaves the data alone (`retain`) |
| `NetworkPolicy` `atlas-database` | the database pods' traffic: in from Atlas (`atlas-api`, the migrate Job, the audit CronJob) on 5432 and from the other instance on 5432 and 8000; out to the other instance, to the Kubernetes API (6443) and to the cluster DNS (`openshift-dns`, 5353) |
| `NetworkPolicy` `atlas-database-from-operator` | the CloudNativePG operator's namespace in, on 8000 and 5432 |

The two NetworkPolicies select the database pods only, so they do the same job whether the
project denies traffic by default or not: on a project that does (many project templates add a
`deny-all`), the database would never become healthy without them. They touch no other pod.

From the `Cluster`, the operator then makes: the pods `atlas-db-1` and `atlas-db-2`, four volumes
(data 20Gi and WAL 5Gi per instance), the services `atlas-db-rw`, `atlas-db-ro` and `atlas-db-r`,
and the two Secrets Atlas reads:

- `atlas-db-app`: the connection details of the `atlas` user. Atlas reads the key `fqdn-uri`
  (`postgresql://atlas:<password>@atlas-db-rw.<namespace>.svc.<cluster domain>:5432/atlas`).
- `atlas-db-ca`: the CA that signed the server's certificate. Atlas reads `ca.crt`.

No namespace is written in the file: it goes where `-n` says.

## Before you apply

**The operator, and its version.** CloudNativePG must run on the cluster and watch your project.
Community CloudNativePG 1.25.3 or later on the 1.25 line, 1.26.1 or later, or any 1.27 or later:
older ones write no `fqdn-uri` key, which Atlas reads.

```powershell
oc api-resources --api-group=postgresql.cnpg.io                     # must list clusters and databases
oc get crd clusters.postgresql.cnpg.io databases.postgresql.cnpg.io  # the same, if you may read CRDs
oc api-resources --api-group=postgresql.k8s.enterprisedb.io         # lists something on EDB instead
oc get csv -n openshift-operators | Select-String -Pattern 'cloudnative-pg|cloud-native-postgresql'   # from OperatorHub: the version is in the name
oc get deploy -n cnpg-system -o jsonpath='{.items[*].spec.template.spec.containers[*].image}'        # from the manifest or Helm: the image tag
```

No `databases`: the operator is older than 1.25. Delete the `Database` document from the file
(from its comment block down to the next `---`); `initdb` still creates the database. Older than
1.25.3 or 1.26.1: the database comes up, but Atlas then needs the `OLDER CLOUDNATIVEPG` patches
in its overlay (`../FIRST-INSTALL.md`, step 1).

**Your rights in the project:**

```powershell
foreach ($r in 'clusters.postgresql.cnpg.io', 'databases.postgresql.cnpg.io', 'networkpolicies') {
  "{0,-32} {1}" -f $r, (oc auth can-i create $r -n atlas)
}
```

**The quota:** 2 pods of 500m CPU and 1Gi memory in requests (2 CPU and 2Gi in limits), and 4
volumes of 50Gi in all. `oc describe quota -n atlas` shows the room.

## Edit the lines marked `# EDIT:`

Open `atlas-db.yaml` (better, a copy of it kept with your install notes). Every line you may
change ends in `# EDIT:` and says what goes there; nothing else needs changing. **Before the first
apply**: volumes keep the size and class they were made with.

| What | Where in the file | Default |
|---|---|---|
| storage class | the two `# storageClass:` lines under `storage:` and `walStorage:`: remove the `# ` and name the class (`oc get storageclass`) | the cluster's default class |
| sizes | `size:` under `storage:` (data) and `walStorage:` (WAL), per instance | 20Gi and 5Gi |
| instances | `instances:` | 2; 1 only for a trial (no standby: losing the volume loses the data) |
| CPU and memory | `resources:`, per instance | 500m / 1Gi requested, 2 / 2Gi limit |
| the operator's namespace | the last policy, `kubernetes.io/metadata.name:` | `cnpg-system` (community, manifest or Helm); `openshift-operators` for an operator from OperatorHub, community or EDB |
| a mirrored registry | `imageName:` | `ghcr.io/cloudnative-pg/postgresql:<tag>@sha256:<digest>` |
| a pull secret | the two `# imagePullSecrets:` lines | none |
| EDB Postgres for Kubernetes | `apiVersion:` of the `Cluster` and of the `Database` | community, `postgresql.cnpg.io/v1` |

Finding the operator's namespace (either command may need more than project rights; then ask the
platform team):

```powershell
oc get pods -A -l app.kubernetes.io/name=cloudnative-pg -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name
```

**A mirrored registry.** Copy the image in `imageName:` to your registry **by digest**, and write
your copy's name with the same tag and the same digest; the digest proves the bytes are the same.
[`../FIRST-INSTALL.md`, "Your own registry"](../FIRST-INSTALL.md#your-own-registry), has the copy
commands (`oc image mirror`, checked with `oc image info`). Keep a `standard` image: `minimal`
has no pgvector. If the registry needs credentials, create the pull secret first and name it in
the `imagePullSecrets:` lines (the operator puts it on the database pods' own ServiceAccount,
`atlas-db`):

```powershell
oc create secret generic change-me-pull-secret -n atlas --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson=<your auth file>
```

**EDB Postgres for Kubernetes.** Its operator is CloudNativePG under another API group: change
`postgresql.cnpg.io/v1` to `postgresql.k8s.enterprisedb.io/v1` on the two lines marked, and set
`imageName:` to the image EDB's subscription provides if the platform team does not run the
community one. Names, Secrets, services and the NetworkPolicies stay the same. In the commands
below, `clusters.postgresql.cnpg.io` becomes `clusters.postgresql.k8s.enterprisedb.io`.

**Another Kubernetes than OpenShift** (a test cluster): the DNS rule in `atlas-database` names
OpenShift's DNS (`openshift-dns`, port 5353); elsewhere it is usually `kube-system` on port 53.

## Apply

```powershell
oc apply -f atlas-db.yaml -n atlas --dry-run=server     # a missing right or an unknown type shows here
oc apply -f atlas-db.yaml -n atlas
oc wait clusters.postgresql.cnpg.io/atlas-db -n atlas --for=condition=Ready --timeout=15m
```

`clusters.postgresql.cnpg.io/atlas-db` is the long form of `cluster/atlas-db`, which avoids any
other resource called `cluster`. It takes a few minutes: the operator makes the volumes, the
first instance, then the standby. `oc get clusters.postgresql.cnpg.io -n atlas` shows the phase
meanwhile. Applying the file again changes only what you changed in it.

## Check it

```powershell
oc get clusters.postgresql.cnpg.io atlas-db -n atlas     # STATUS: Cluster in healthy state, READY 2
oc get pods -n atlas -l app.kubernetes.io/component=database
oc get databases.postgresql.cnpg.io atlas-db-atlas -n atlas -o jsonpath='{.status.applied}'   # true
(oc get secret atlas-db-app -n atlas -o json | ConvertFrom-Json).data.PSObject.Properties.Name  # must include fqdn-uri
(oc get secret atlas-db-ca -n atlas -o json | ConvertFrom-Json).data.PSObject.Properties.Name   # ca.crt, ca.key
oc exec -n atlas atlas-db-1 -c postgres -- psql -tAc "select application_name, state from pg_stat_replication"   # atlas-db-2|streaming
```

The two `Secret` lines print key **names** only, never the values. The last line runs on the
primary (`atlas-db-1` at first; `oc get clusters.postgresql.cnpg.io -n atlas` names the current
one) and shows the standby streaming.

If it does not come up: `oc describe clusters.postgresql.cnpg.io atlas-db -n atlas` and
`oc get events -n atlas --sort-by=.lastTimestamp`. The usual causes, and what they look like, are
in [`../FIRST-INSTALL.md`, troubleshooting](../FIRST-INSTALL.md#troubleshooting):
`Pending` volumes (storage class or quota), no status at all (the operator does not watch the
project), `Instance Status Extraction Error` (the operator's namespace in the last policy is
wrong).

## Then install Atlas, without its own database

Follow [`../FIRST-INSTALL.md`](../FIRST-INSTALL.md) in the same project, with the changes in its
section [Installing the database first, with plain YAML](../FIRST-INSTALL.md#installing-the-database-first-with-plain-yaml):
in the overlay, `../../components/cnpg` is removed and `../../components/cnpg-external` is listed
last. Atlas finds the database by the Secrets' names, which are the same.

## Remove it

```powershell
oc delete -f atlas-db.yaml -n atlas
```

**This deletes the data.** The volumes (`oc get pvc -n atlas`, `atlas-db-1`, `atlas-db-1-wal` and
the standby's) belong to the `Cluster`, so Kubernetes deletes them with it, and with them the
disks when the storage class's reclaim policy is `Delete` (the usual default, ARO's `managed-csi`
included). They do **not** remain. To keep the data, before deleting: take a backup
(`../README.md`, "The database"), or have the platform team set the volumes'
PersistentVolumes to `Retain` (a cluster-wide right):

```powershell
oc get pvc -n atlas -l app.kubernetes.io/component=database -o custom-columns=PVC:.metadata.name,PV:.spec.volumeName
oc patch pv <PV> --type=merge -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'   # per volume; cluster rights (PowerShell 7.3 or later)
```

Retained disks stay as `Released` PersistentVolumes, to be bound by hand to a new install or
deleted by the platform team. Deleting only the `Database` object (`retain`) leaves the database
and its rows alone.

## How this file is kept right

`atlas-db.yaml` is not written by hand from scratch: [`kustomization.yaml`](kustomization.yaml)
next to it renders the same objects from `../components/cnpg` and
`../components/networkpolicies-database`, the components the Atlas overlays use, and
`tools/ci/openshift/lint.sh` fails any pull request in which the file, with its comments
removed, differs from that render by a single byte. It is also linted like every other render
(kubeconform against the CloudNativePG schemas, kube-linter, Atlas's conftest policies).

**Verified** in kind (Kubernetes 1.34, kindnet, which enforces NetworkPolicy) with community
CloudNativePG 1.30.1, the file applied to an empty namespace holding a `deny-all` policy, with the
DNS rule pointed at `kube-system` port 53 (kind's DNS): the Cluster became Ready with 2 instances
streaming, `atlas-db-app` carried `fqdn-uri`, a pod labelled as `atlas-api` connected with
`verify-full` against `ca.crt`, a pod labelled as `atlas-mcp` timed out, and `oc delete -f`
removed the volumes unless their PersistentVolumes had been set to `Retain` first. Not yet on
OpenShift itself: the `openshift-dns` rule and OVN-Kubernetes's enforcement are lint-checked only.
