# Atlas on OpenShift: the manifests

Kustomize manifests that run Atlas on OpenShift (built for Azure Red Hat OpenShift) in one
namespace, under the default `restricted-v2` SCC, with namespace-scoped rights only. The design,
and the reasons behind every choice here, are in
[Atlas on Azure Red Hat OpenShift](../../docs/oss/research/openshift-aro.md). This directory is
being built task by task; until the install guide (task 5.1) lands, this page is the map.

**To try an install now**, follow [FIRST-INSTALL.md](FIRST-INSTALL.md): a step-by-step for
PowerShell 7 and `oc`, with the overlay [`overlays/example/`](overlays/example/kustomization.yaml)
(no optional components, no backup yet; the NetworkPolicies are one commented line to switch on,
below).

The Compose install in `deploy/oss/` is unchanged and separate: nothing here is read by it.

## The layout

```text
deploy/openshift/
  base/                 always-on objects, deployable only with a database component and an overlay
  components/<name>/    one opt-in feature each (Kustomize Components)
  overlays/<name>/      an install: namespace, host, images, config, which components are on
```

Plain YAML, applied with `oc apply -k` (Kustomize is built into `oc`) or rendered by Argo CD.
The organisation keeps its own overlay in its own repository, pointing at this base by release
tag.

**Linted on every pull request** that touches this directory, by
`.github/workflows/openshift-lint.yml` (`tools/ci/openshift/lint.sh`): `kustomize build` of the
base, of the base plus each component on its own, and of each overlay; then kubeconform (strict),
kube-linter and Atlas's conftest policies (no secret in the environment, the restricted security
context, no `runAsUser` in the base or a component, images by digest, no ServiceAccount token,
no `http://` URL). `tools/ci/check_pins.py` also checks every `image:` here for a tag and a digest.

## What `base/` holds (task 3.1)

| File | Object | What it is |
|---|---|---|
| `kustomization.yaml` | | the resource list, the common labels, the ConfigMap generators |
| `serviceaccount.yaml` | ServiceAccount `atlas` | no token mounted, no Role bound: Atlas never calls the Kubernetes API |
| `atlas.env` | ConfigMap `atlas-env` | the non-secret settings, loaded with `envFrom` (the counterpart of `.env`) |
| `https_probe.py`, `db_url.py` | ConfigMap `atlas-scripts` | the exec probe and the database-URL init container, mounted at `/run/config/scripts/` |
| `configmap-service-ca.yaml` | ConfigMap `atlas-service-ca` | the OpenShift service CA bundle, injected as `service-ca.crt` |
| `service-api.yaml` | Service `atlas-api` | `:8443`, TLS only; its annotation makes the service CA issue Secret `atlas-api-tls` |
| `deployment-api.yaml` | Deployment `atlas-api` | 2 replicas, uvicorn TLS on `:8443`, every secret a file, read-only root filesystem |
| `pdb-api.yaml` | PodDisruptionBudget `atlas-api` | `minAvailable: 1` |
| `job-migrate.yaml` | Job `atlas-migrate-<version>` | `atlas-migrate upgrade`, once per release |
| `cronjob-audit-purge.yaml` | CronJob `atlas-audit-purge` | `atlas-admin audit purge` daily at 03:00 UTC (task 3.8) |
| `service-mcp.yaml` | Service `atlas-mcp` | `:8493`, TLS only; its annotation makes the service CA issue Secret `atlas-mcp-tls` (task 3.2) |
| `deployment-mcp.yaml` | Deployment `atlas-mcp` | 1 replica, uvicorn TLS on `:8493`, reaches the API only through `atlas-tls`'s `:9443` front, no secret mounted (task 3.2) |
| `caddy/Caddyfile`, `caddy/client-auth-*.caddy` | ConfigMap `atlas-caddy` | atlas-tls's configuration, mounted at `/etc/caddy/` (task 3.3) |
| `service-tls.yaml` | Service `atlas-tls` | `:8443` (the public door) and `:9443` (atlas-mcp's door to the API); its annotation makes the service CA issue Secret `atlas-tls-tls` (task 3.3) |
| `deployment-tls.yaml` | Deployment `atlas-tls` | Caddy, 2 replicas, read-only root filesystem (task 3.3) |
| `pdb-tls.yaml` | PodDisruptionBudget `atlas-tls` | `minAvailable: 1` (task 3.3) |
| `route-atlas.yaml` | Route `atlas` | re-encrypt to `atlas-tls:8443`, the only way in from outside (task 3.3) |

Every object carries `app.kubernetes.io/name: atlas`, `app.kubernetes.io/part-of: atlas`,
`app.kubernetes.io/version` and an `app.kubernetes.io/component` (`api`, `mcp`, `tls`, `migrate`,
`audit-purge`, `config`);
the selectors use `name` and `component`, which the NetworkPolicies (task 3.4) select on.

### What the pods need that the base does not create

| Secret | Keys used | Made by |
|---|---|---|
| `atlas-peppers` | `password_pepper`, `token_pepper` | `scripts/openshift/new-secrets.py` (see [the secrets page](../../docs/oss/guides/openshift-secrets.md)), or the External Secrets Operator |
| `atlas-db-app` | `fqdn-uri` only | the CloudNativePG `Cluster` named `atlas-db` (component `cnpg`, task 3.5) |
| `atlas-db-ca` | `ca.crt` only (never `ca.key`) | the same `Cluster` |
| `atlas-api-tls` | `tls.crt`, `tls.key` | the service CA, from the annotation on Service `atlas-api` |
| `atlas-mcp-tls` | `tls.crt`, `tls.key` | the service CA, from the annotation on Service `atlas-mcp` |
| `atlas-tls-tls` | `tls.crt`, `tls.key` | the service CA, from the annotation on Service `atlas-tls` |

A database named other than `atlas-db` means patching the two `secretName`s in the Deployment, the
Job and the CronJob.

### Decisions made in the base

- **Secrets are files, never environment variables.** Each is a `secret` volume (mode 0440, only
  the keys needed), read through the `*_FILE` settings, and `ATLAS_REQUIRE_SECRET_FILES=1` makes
  Atlas refuse the plain forms. The database TLS settings are `PGSSLMODE=verify-full` and
  `PGSSLROOTCERT=/run/secrets/db-ca/ca.crt`, which libpq reads; they are not secrets.
- **The database URL goes through an init container.** CloudNativePG writes `postgresql://...`,
  which SQLAlchemy 2.1 resolves to psycopg 3; the image ships psycopg2. The `db-url` init
  container rewrites the scheme to `postgresql+psycopg2://` into a memory-backed `emptyDir`, and
  only it mounts the CNPG Secret. `base/db_url.py` explains it, and what removes it.
- **The `db-url` bridge goes once the base references a release containing TASK-1501**, which
  makes Atlas itself read a `postgresql://` URL as psycopg2 (`backend/dsn.py`); then
  `ATLAS_DB_URL_FILE` points at the CNPG Secret's `fqdn-uri` directly.
- **Probes are exec probes that verify the certificate.** The kubelet's HTTPS `httpGet` probe
  does not verify certificates. `base/https_probe.py`, run with the image's Python, dials the
  pod's own `:8443`, verifies the certificate and its name (`atlas-api.<namespace>.svc`) against
  the service CA, and sends `Host: $ATLAS_PUBLIC_HOST` (any other Host is a 421). Startup and
  liveness use `/healthz` (no database), readiness `/readyz`.
- **No `runAsUser`, no `fsGroup`.** The SCC assigns both from the namespace's range; the image
  runs under any UID in group 0.
- **Service links are off** (`enableServiceLinks: false`): Kubernetes would otherwise inject
  variables such as `ATLAS_API_PORT` for the Service `atlas-api`, inside Atlas's own `ATLAS_`
  namespace.
- **`atlas-env` has no content hash in its name.** The migrate Job loads it, and a Job's pod
  template cannot change under one name. After changing it, run
  `oc rollout restart deploy/atlas-api`.
- **`ATLAS_TRUSTED_PROXY_CIDRS` is a documentation range** (`198.51.100.0/24`) in the base, which
  trusts no real peer and so fails safe. The overlay sets the cluster's pod network CIDR.
- **atlas-mcp reaches the API only through `atlas-tls`**, as in Compose: `ATLAS_BASE_URL` is
  `https://atlas-tls.<namespace>.svc:9443` (the `:9443` front, task 3.3), verified against the
  service CA alone (`ATLAS_CA_BUNDLE=/run/config/service-ca/service-ca.crt`). That URL's host and
  port are exactly `ATLAS_INTERNAL_HOSTS` on `atlas-api`, the one Host on which a Bearer request
  needs no cookie: the two are fixed on the containers and change together. atlas-mcp never
  dials `atlas-api:8443`, and the NetworkPolicies (component `networkpolicies`, task 3.4) give it
  no route to it or to the database.
- **atlas-mcp holds no secret.** Each tool call forwards its caller's own Bearer token, so the pod
  mounts only its serving certificate, the service-CA bundle, the probe script and `/tmp`: no
  pepper, no database Secret, no database CA. Its exec probe is the same `https_probe.py`, on
  `:8493` `/health`, server name `atlas-mcp.<namespace>.svc`, without `public` (`/health` is
  outside the MCP transport's Host check).
- **One atlas-mcp replica, stateful transport, no PDB.** atlas-mcp keeps each MCP session in the
  memory of the pod that answered `initialize`, so a second replica would refuse the requests that
  land on it. `ATLAS_MCP_STATELESS_HTTP=1` lifts that, but the release this base names
  (`v0.0.1-alpha`) predates the switch and ignores it, so the base pins it to `0` on the container
  and runs one replica; a PodDisruptionBudget on one replica would block every node drain. A
  restart or a rollout ends the open sessions and clients start new ones. Running two is an
  overlay change, below.

### The audit purge (task 3.8)

CronJob `atlas-audit-purge` enforces `ATLAS_AUDIT_RETENTION_DAYS` (365 in `atlas-env`): it runs
`atlas-admin audit purge`, which deletes `audit_read` rows older than that and never touches
`graph_event` or `auth_event`. With the setting removed it deletes nothing. It is the cron line the
Compose install documents (`0 3 * * *`, see
[Operations](../../docs/oss/guides/operations.md#8-audit-export)), at 03:00 UTC, with
`concurrencyPolicy: Forbid`. It has the migrate Job's wiring: `atlas-env`, the `db-url` init
container, the database CA and the peppers. To run it once now, for example to check the
retention after changing it:

```sh
oc create job --from=cronjob/atlas-audit-purge atlas-audit-purge-manual
oc wait --for=condition=complete job/atlas-audit-purge-manual --timeout=15m
oc logs job/atlas-audit-purge-manual
```

The log line says how many rows it purged. `oc create job --from` makes the CronJob the Job's
owner, so the CronJob's history limits (three successful, three failed) remove it in time;
`oc delete job/atlas-audit-purge-manual` before running it by hand again. To pause it, `oc patch cronjob/atlas-audit-purge -p
'{"spec":{"suspend":true}}'`; an overlay that wants another time patches `spec.schedule`.

### The front door: atlas-tls and the Route (task 3.3)

```text
client --https--> router --re-encrypt--> atlas-tls:8443 --https--> atlas-api:8443 / atlas-mcp:8493
                                          atlas-mcp --https--> atlas-tls:9443 --https--> atlas-api:8443
```

`base/caddy/Caddyfile` is the OpenShift counterpart of `deploy/oss/caddy/Caddyfile` and explains
every door and leg; the decisions:

- **Re-encrypt, `destinationCACertificate` left empty.** The router terminates the public TLS
  with the platform's certificate and verifies Caddy's service-CA certificate and its name
  `atlas-tls.<namespace>.svc`. It sends no SNI on that leg, so the Caddyfile sets `default_sni`.
  A plain-HTTP request gets the router's redirect (`insecureEdgeTerminationPolicy: Redirect`).
- **The client's address survives.** The Route sets
  `haproxy.router.openshift.io/set-forwarded-headers: replace`, so X-Forwarded-For holds exactly
  the address the router saw. Caddy believes it only from `CADDY_TRUSTED_PROXIES` and sets
  `CF-Connecting-IP` from it, replacing any value a client sent; atlas-api believes
  `CF-Connecting-IP` only from `ATLAS_TRUSTED_PROXY_CIDRS`. Both are documentation ranges in the
  base (they fail safe: the sign-in throttle then counts the proxy) and both are set by the
  overlay.
- **The security headers and HSTS come from Caddy**, the same snippet as Compose, checked by
  `tools/ci/check_oss_headers.py` (which fails if the two copies differ). The Route has no
  `hsts_header` annotation, so the header is sent once.
- **No ACME.** `auto_https off`: every certificate is a file, and Caddy has no `:80` listener.
- **The image.** The base names the released Compose Caddy (`gradinai-atlas-tls`, Alpine, with
  an unused Cloudflare DNS plugin), which runs this Caddyfile unchanged under an arbitrary UID.
  From `v0.0.2-alpha` on, a release also publishes the UBI build of Caddy
  (`deploy/caddy/Dockerfile.ubi`) as `gradinai-atlas-tls-ubi`; the overlay moves to it
  ("Images and releases" below).
- **Probes are not exec probes**: the image has no shell, Python or HTTP client. Startup and
  liveness are `tcpSocket :8443`; readiness is an HTTPS `httpGet` of `/healthz`, which the
  Caddyfile's catch-all `:8443` site sends through its verified leg to atlas-api, so a front that
  cannot reach atlas-api leaves the Route. These probes come from the node over the network.
- **mTLS on `:9443` stays optional**: `CADDY_CLIENT_AUTH=off` here; the `mtls` component (task
  3.6) sets `require` and mounts the client CA at `/run/secrets/client-ca/ca.crt`.
- **The MCP timeout.** The router's default is 30 s of silence; the Route allows 5 minutes
  (`haproxy.router.openshift.io/timeout`) for long tool calls and event streams.
- **No gateway leg** (Compose's `:9444`): no gateway on OpenShift in v1.

## What an overlay must set

| What | How |
|---|---|
| the namespace | `namespace:` |
| the public host | `configMapGenerator` named `atlas-env` with `behavior: merge`, from the overlay's own env file: `ATLAS_PUBLIC_HOST`, `ATLAS_PUBLIC_URL`, `ATLAS_MCP_ALLOWED_HOSTS`, `ATLAS_MCP_ALLOWED_ORIGINS`, `ATLAS_PRIVACY_CONTACT`, `ATLAS_PRIVACY_HOSTING` |
| the Route's host | a patch on Route `atlas`: `spec.host` is `ATLAS_PUBLIC_HOST`. A certificate of the organisation's own goes on the Route too (`spec.tls.certificate`, `key`); without one the router serves the platform's default |
| the trusted proxy ranges | two env patches, usually the same value: `CADDY_TRUSTED_PROXIES` on the `atlas-tls` container (the router's range, space-separated) and `ATLAS_TRUSTED_PROXY_CIDRS` on the `atlas-api` container (atlas-tls's range, comma-separated). On ARO the router and atlas-tls are both on the pod network, so both are the pod network CIDR (`oc get network.config cluster -o jsonpath='{.spec.clusterNetwork[*].cidr}'`; ARO's default is the /14 that starts the 10.128 block). A router on the host network (`HostNetwork` strategy) is seen from the node's address: add the machine network. A gateway in front of the router (Application Gateway, Front Door): set the Route's `set-forwarded-headers` to `append` and add the gateway's range to `CADDY_TRUSTED_PROXIES` |
| the images and the release | see the next section; the base's registry is a placeholder |
| one replica instead of two | a `replicas` patch on `atlas-api` or `atlas-tls`, and delete its PDB (a `minAvailable: 1` on one replica blocks node drains) |
| two `atlas-mcp` replicas (optional) | only on a release that has `ATLAS_MCP_STATELESS_HTTP`; see "Two atlas-mcp replicas" |

### Images and releases

The base names the Debian-based hardened API image of release `v0.0.1-alpha` by tag and by the
digest that release published (`ghcr.io/<owner>/gradinai-atlas-api`, listed in the release's
`release-images.env`; see [Releases](../../docs/oss/RELEASES.md)). Its registry,
`registry.example.com/gradinai-atlas`, is a placeholder, because the repository names no
account: **every overlay sets the image**, to GHCR or to the organisation's mirror of the same
digest.

**The `-ubi` images are the ones to run on OpenShift.** From `v0.0.2-alpha` on, every release
publishes them beside the Compose images (task 1.4; [Releases](../../docs/oss/RELEASES.md#what-a-release-publishes)):
`gradinai-atlas-api-ubi`, `gradinai-atlas-mcp-ubi` and `gradinai-atlas-tls-ubi`, on Red Hat UBI 9,
non-root under any UID in group 0, checked with Red Hat's preflight, with `/licenses` and their
own labels. An overlay switches to them by changing `newName` to the `-ubi` name; the `name:`
it matches stays the base's (`gradinai-atlas-api`, `-mcp`, `-tls`), because that is what the
manifests say. Their digests are the release's `release-images.env` lines
`ATLAS_IMAGE_API_UBI`, `ATLAS_IMAGE_MCP_UBI` and `ATLAS_IMAGE_TLS_UBI` (the part after `@`); the
lines without `_UBI` are the Compose images. The example overlay still names the `v0.0.1-alpha`
Compose images until it is moved in its own change. Three things belong to one release and move
together in the overlay:

```yaml
images:
  - name: registry.example.com/gradinai-atlas/gradinai-atlas-api    # matches the base, the Job and the init containers
    newName: registry.example.com/atlas/gradinai-atlas-api-ubi
    newTag: v0.2.0
    digest: sha256:<the release's digest>
  - name: registry.example.com/gradinai-atlas/gradinai-atlas-mcp    # matches the atlas-mcp Deployment
    newName: registry.example.com/atlas/gradinai-atlas-mcp-ubi
    newTag: v0.2.0
    digest: sha256:<the release's digest>
  - name: registry.example.com/gradinai-atlas/gradinai-atlas-tls    # matches the atlas-tls Deployment
    newName: registry.example.com/atlas/gradinai-atlas-tls-ubi
    newTag: v0.2.0
    digest: sha256:<the release's digest>
labels:
  - pairs: {app.kubernetes.io/version: v0.2.0}
    includeSelectors: false
    includeTemplates: true
patches:
  - target: {kind: Job, name: atlas-migrate-v0-0-1-alpha}
    patch: |-
      - op: replace
        path: /metadata/name
        value: atlas-migrate-v0-2-0
```

**The migrate Job is named per release** (`atlas-migrate-<version>`, dots as dashes) so that each
release is a new Job: a Job's pod template cannot change, the previous run's log stays for the
audit trail until `ttlSecondsAfterFinished` (7 days) removes it, and the pipeline step is apply,
`oc wait --for=condition=complete job/atlas-migrate-<version>`, then roll the Deployments.
Forgetting the rename is loud: `oc apply` refuses the changed pod template. Running the Job
again after its TTL is harmless (the upgrade is idempotent), and an `atlas-api` replica starting
alongside it waits on the same Postgres advisory lock.

### Two atlas-mcp replicas

On a release that has the stateless transport (`ATLAS_MCP_STATELESS_HTTP`, after `v0.0.1-alpha`),
an overlay may run two `atlas-mcp` replicas. Three things go together, in one change: the switch
(no session is kept and each request stands alone, so any replica can answer it), the replica
count, and a PodDisruptionBudget so a drain keeps one serving. Never two replicas without the
switch: a request that lands on the pod that did not answer `initialize` is refused.

```yaml
patches:
  - target: {kind: Deployment, name: atlas-mcp}
    patch: |-
      apiVersion: apps/v1
      kind: Deployment
      metadata: {name: atlas-mcp}
      spec:
        replicas: 2
        template:
          spec:
            containers:
              - name: atlas-mcp
                env:
                  - {name: ATLAS_MCP_STATELESS_HTTP, value: "1"}
resources:
  - pdb-mcp.yaml   # the overlay's own: base/pdb-api.yaml with `mcp` in place of `api`
```

The spread and anti-affinity rules for two replicas are already in the base Deployment.

## The database (task 3.5)

Atlas's database is a CloudNativePG (CNPG) `Cluster` named `atlas-db`, run by an operator the
platform team already has; these components hold only namespaced objects (no CRD, operator or
ClusterRole). An overlay lists **one** of the first two:

| Component | Adds | Needs on the cluster |
|---|---|---|
| `components/cnpg/` | `Cluster` `atlas-db` (PostgreSQL 16, 2 instances, database `atlas` owned by `atlas`, TLS required) and `Database` `atlas-db-atlas`. **No backup.** | the CNPG operator and a default storage class |
| `components/cnpg-objectstore-plugin/` | everything in `cnpg` (it includes it), plus the Barman Cloud plugin's `ObjectStore` `atlas-backups` (Azure Blob, 30-day recovery window), the Cluster's WAL archive through the plugin, and `ScheduledBackup` `atlas-db-daily` (02:30 every day, the first one at once) | also the Barman Cloud plugin (with cert-manager), the `ObjectStore` role granted to the namespace's admins, the blob container, Secret `atlas-backup-azure` |
| `components/cnpg-edb/` | nothing of its own: it moves every `postgresql.cnpg.io` object to EDB's group. List it **after** one of the two above | EDB Postgres for Kubernetes instead of community CNPG |

`cnpg` is what a first install applies: base plus `cnpg` on a fresh namespace comes up with
nothing else to create. **Until `cnpg-objectstore-plugin` replaces it there is no backup**, and
the standby is the only copy of the data. Listing both is refused by `kustomize build`
(Cluster `atlas-db` would be added twice). The in-tree `spec.backup.barmanObjectStore` method is
not offered: it is deprecated since CNPG 1.26 and removed in 1.32.

**What the Cluster makes, and the base mounts.** Secret `atlas-db-app` (the base reads only
`fqdn-uri`, `postgresql://atlas:<password>@atlas-db-rw.<namespace>.svc.<cluster domain>:5432/atlas`,
through the `db-url` init container) and Secret `atlas-db-ca` (the base reads only `ca.crt`, never
`ca.key`). The operator's own CA signs a server certificate for every service name, so
`PGSSLMODE=verify-full` against `ca.crt` holds for the host in `fqdn-uri`. A `pg_hba` rule refuses
any connection without TLS. The `postgres` superuser has no password, and `atlas` has no
`CREATEDB`.

**Community or EDB.** The manifests are written for community CloudNativePG,
`postgresql.cnpg.io/v1`, the default. On EDB Postgres for Kubernetes
(`postgresql.k8s.enterprisedb.io/v1`, open question 6 of the research), add the switch:

```yaml
components:
  - ../../components/cnpg          # or cnpg-objectstore-plugin
  - ../../components/cnpg-edb      # after it: it patches what the one above added
```

Names, Secrets and services stay the same. EDB labels its pods `k8s.enterprisedb.io/...` instead of
`cnpg.io/...`; the Cluster copies `app.kubernetes.io/name: atlas` and
`app.kubernetes.io/component: database` onto its pods under either operator, and those are the
labels to select on. The lint's schema catalog has no EDB schemas: the base-plus-`cnpg-edb`
render holds no database object and passes, but an overlay that lists `cnpg-edb` fails
kubeconform's strict mode until the lint maps that group to the CNPG schemas. On EDB, `imageName`
is usually EDB's own image (next table).

**What an overlay may change** (each a patch on the object named):

| What | Patch |
|---|---|
| storage class and sizes | `Cluster` `atlas-db`: `/spec/storage/storageClass`, `/spec/storage/size` (20Gi), `/spec/walStorage/...` (5Gi); without a class, the cluster default |
| instances, resources | `/spec/instances` (2), `/spec/resources` |
| the PostgreSQL image | `/spec/imageName`, by tag **and digest** (the lint refuses a tag alone): a mirror of `ghcr.io/cloudnative-pg/postgresql:16.<minor>-<build>-standard-trixie`, or the platform team's image. Keep a `standard` image: `minimal` has no pgvector |
| the backup destination | `ObjectStore` `atlas-backups`: `/spec/configuration/destinationPath`, `https://<account>.blob.core.windows.net/<container>/atlas` (the shipped value is a placeholder that cannot resolve) |
| workload identity instead of a SAS token | `ObjectStore` `atlas-backups`: replace `/spec/configuration/azureCredentials` with `{inheritFromAzureAD: true}` |

**pgvector, only with retrieval.** The `standard` image ships the `vector` extension, but `atlas`
cannot create it (it is not a trusted extension). The operator does, from the `Database` object.
`cnpg` lists no extension, as in the Compose install, and the `retrieval` component (task 3.6)
carries this patch:

```yaml
patches:
  - target: {kind: Database, name: atlas-db-atlas}
    patch: |-
      - op: add
        path: /spec/extensions
        value:
          - {name: vector, ensure: present}
```

**Secret `atlas-backup-azure`** (for `cnpg-objectstore-plugin` only; never in this repository):

| Key | Value |
|---|---|
| `AZURE_STORAGE_ACCOUNT` | the storage account's name |
| `AZURE_STORAGE_SAS_TOKEN` | a SAS token scoped to the backup container, with read, add, create, write, delete and list (delete is how the 30-day window removes old backups) |

The blob container must exist before the first WAL is archived: Barman does not create it. The
storage account should have soft delete and an immutability policy (open question 7).

**Backups and restores** (`cnpg-objectstore-plugin`). `oc get cluster atlas-db -o yaml` shows the
conditions `ContinuousArchiving` and `LastBackupSucceeded`. The `immediate` backup is taken the
moment the `ScheduledBackup` is created; when `cnpg` is swapped for this component on a running
Cluster, the instances restart to load the plugin and that first backup fails on the race. Take
one at once rather than wait for the nightly one. The deploy pipeline takes the same on-demand
backup before each migrate Job, with a name unique to the release:

```yaml
apiVersion: postgresql.cnpg.io/v1      # postgresql.k8s.enterprisedb.io/v1 on EDB
kind: Backup
metadata:
  name: atlas-db-before-v0-2-0
spec:
  cluster: {name: atlas-db}
  method: plugin
  pluginConfiguration: {name: barman-cloud.cloudnative-pg.io}
```

A restore is always a new `Cluster` bootstrapped from the object store (`bootstrap.recovery`),
never in place; question 4 of the research describes the quarterly drill, and task 5.2 writes the
runbook.

## NetworkPolicies (task 3.4)

`components/networkpolicies/` is the Compose networks' isolation as Kubernetes NetworkPolicies:
**default deny for every Atlas pod, in and out, then one allow per edge Atlas uses.** It is an
**opt-in component for now**, not part of `base/`: a first install may need it at once (on a
cluster whose project template already holds NetworkPolicies, the database does not come up
without it, below) or add it after the trial. It moves into `base/` once it has run on the
company cluster. To switch it on, list it in the overlay:

```yaml
components:
  - ../../components/cnpg
  - ../../components/networkpolicies
```

### The edges

Every policy selects pods by `app.kubernetes.io/name: atlas` and `app.kubernetes.io/component`;
the database pods carry both through the Cluster's `inheritedMetadata` (component `cnpg`).

| From | To | Port | Policy |
|---|---|---|---|
| the OpenShift router (namespace labelled `policy-group.network.openshift.io/ingress`) | atlas-tls | 8443 | `atlas-tls` |
| atlas-mcp | atlas-tls | 9443 | `atlas-tls`, `atlas-mcp` |
| atlas-tls | atlas-api | 8443 | `atlas-tls`, `atlas-api` |
| atlas-tls | atlas-mcp | 8493 | `atlas-tls`, `atlas-mcp` |
| atlas-api, the migrate Job, the audit purge CronJob | the database pods | 5432 | `atlas-api`, `atlas-db-clients`, `atlas-database` |
| a database pod | the other database pods | 5432, 8000 | `atlas-database` |
| the CloudNativePG operator's namespace | the database pods | 8000, 5432 | `atlas-database-from-operator` |
| the database pods | the Kubernetes API (any address) | 6443 | `atlas-database` |
| every Atlas pod | the cluster DNS (namespace `openshift-dns`) | 5353 TCP and UDP | `atlas-dns` |

Nothing else. In particular **atlas-mcp reaches neither the database nor `atlas-api:8443`**, no
other namespace reaches anything but the router to `atlas-tls:8443`, and no Atlas pod but the
database reaches the Kubernetes API (none mounts a token).

- **Probes need no rule.** atlas-api and atlas-mcp use exec probes (inside the pod, to 127.0.0.1).
  atlas-tls's tcpSocket and httpGet probes come from the pod's own node, and a NetworkPolicy cannot
  block traffic from the resident node: Kubernetes says so, and so does OpenShift's network policy
  documentation for OVN-Kubernetes. Verified in kind (below); the OpenShift Local run (task 6.3)
  confirms it on OVN-Kubernetes itself.
- **DNS is port 5353**, the DNS pods' own port: OVN-Kubernetes checks the policy after the
  Service's 53 is translated.
- **The database needs the Kubernetes API.** CloudNativePG's instance manager, in every database
  pod, reads its `Cluster` through the API; without the 6443 rule a new or restarted instance
  waits forever (`dial tcp ...:443: i/o timeout` in its log). The API server listens on the
  control-plane nodes' host network, which no pod selector names, so the rule names the port only.
- **The CloudNativePG operator** polls each instance's status on 8000 and, per CloudNativePG's
  networking documentation, needs 5432 too. Without it the Cluster's phase reads `Instance Status
  Extraction Error: HTTP communication issue` and the standby is never created. Every pod of the
  operator's namespace is admitted (the operator's own pod labels differ between community CNPG
  and EDB, and a wrong label would stop the database).

### The operator's namespace

The component admits `cnpg-system`, community CloudNativePG's default (its manifest and Helm
chart). An operator installed from OperatorHub (OLM, community or EDB) usually runs in
`openshift-operators`. Ask the platform team, or look (either may need more than project rights):

```powershell
oc get pods -A -l app.kubernetes.io/name=cloudnative-pg
oc get csv -A | Select-String -Pattern 'cloudnative-pg|cloud-native-postgresql'
```

Then set it in the overlay:

```yaml
patches:
  - target: {kind: NetworkPolicy, name: atlas-database-from-operator}
    patch: |-
      - op: replace
        path: /spec/ingress/0/from/0/namespaceSelector/matchLabels/kubernetes.io~1metadata.name
        value: openshift-operators
```

### Backups need one more policy

With `cnpg-objectstore-plugin`, the Barman Cloud sidecar in each database pod writes to Azure Blob
over HTTPS, which `atlas-database` does not allow. Until the plugin component carries it, add this
to the overlay's resources. A NetworkPolicy cannot name a host: narrow `0.0.0.0/0` to the storage
account's address ranges, or to the platform team's egress proxy if they run one.

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: atlas-database-backup
spec:
  podSelector:
    matchLabels: {app.kubernetes.io/name: atlas, app.kubernetes.io/component: database}
  policyTypes: [Egress]
  egress:
    - to: [{ipBlock: {cidr: 0.0.0.0/0}}]
      ports: [{protocol: TCP, port: 443}]
```

Without it the `ContinuousArchiving` condition (`oc get clusters.postgresql.cnpg.io atlas-db -o
yaml`) turns false. The `oidc` and `retrieval` components (task 3.6) bring atlas-api's own egress.

### Next to the project template's own policies

Many OpenShift clusters create every new project from a template that already holds
NetworkPolicies. The usual set, as OpenShift's "multitenant isolation" documentation writes it:

| Policy | Selects | Allows in |
|---|---|---|
| `allow-from-openshift-ingress` | every pod | from the router's namespace, every port |
| `allow-from-openshift-monitoring` | every pod | from namespaces labelled `network.openshift.io/policy-group: monitoring`, every port |
| `allow-same-namespace` (or `allow-from-same-namespace`) | every pod | from every pod of the same namespace, every port |
| `allow-from-kube-apiserver-operator` (sometimes) | every pod | from the kube-apiserver operator (its webhook health check) |

They are ingress only; usually there is no egress policy, so egress stays open. Some templates
add a `deny-all` (`podSelector: {}` and no rules).

**NetworkPolicies add up.** A pod selected by any policy accepts the union of what the policies
selecting it allow; no policy can take away what another allows. Next to that template, this
component:

- **adds egress control**, which the template does not have: each Atlas pod may open only the
  connections in the table above. Because the sender is checked too, the Atlas pods stay isolated
  from each other inside the namespace: **atlas-mcp still cannot reach the database or
  `atlas-api:8443`**, because its own egress allows only `atlas-tls:9443` (verified in kind with
  the template's three policies applied).
- **lets the CloudNativePG operator in.** The template admits only the router, monitoring and the
  namespace itself, not the operator's namespace. **With the template alone the database never
  becomes healthy**: verified in kind, a fresh `atlas-db` stopped after its first instance with
  `Instance Status Extraction Error`, and came up 2/2 within minutes of this component being
  applied. Unless the platform team already admits the operator by a policy of its own, this
  component (or at least `atlas-database-from-operator`) is needed for the install to work at all.
- **makes a `deny-all` template workable**: without our allows the Route answers 503, the migrate
  Job hangs on the database, and the operator cannot reach the instances.

What it **cannot** do while `allow-same-namespace` exists: keep a **non-Atlas pod** of the same
namespace (a debug pod, anything without the Atlas labels, which no egress policy restricts) away
from the Atlas ports: `atlas-api:8443`, the database's 5432, and `atlas-tls:8443`. The last one
matters: atlas-tls believes `X-Forwarded-For` from the whole pod network
(`CADDY_TRUSTED_PROXIES`), so a pod that reaches `:8443` directly can forge the client address
the sign-in throttle and the audit trail see. Our router-only rule prevents exactly that. The
template already keeps other namespaces out, but not the same namespace. Likewise
`allow-from-openshift-ingress` admits the router's namespace to every port, not only
`atlas-tls:8443`, which is harmless while only routers run there.

**For the platform team:** keep `allow-from-openshift-ingress` and `allow-from-openshift-monitoring`
(ours needs neither, and they do no harm); **remove `allow-same-namespace` from the Atlas project,
or accept** that the namespace is the trust boundary and run nothing but Atlas in it.

### AdminNetworkPolicy and BaselineAdminNetworkPolicy

OVN-Kubernetes also has cluster-wide policies that only cluster administrators write, checked in
this order: **AdminNetworkPolicy (ANP)**, then the namespace's NetworkPolicies, then the one
**BaselineAdminNetworkPolicy (BANP)**, named `default`.

- An ANP rule that says **Deny** or **Allow** is final: our policies cannot override an ANP deny
  (of egress to the Kubernetes API, say, or between namespaces). An ANP **Pass** hands the
  decision to the NetworkPolicies.
- The BANP decides only what no NetworkPolicy decided. Once our policies select a pod they decide
  its traffic, so **a BANP deny is overridden by our allows**, and equally **a BANP allow no
  longer applies to the Atlas pods**: anything the platform lets through by BANP (monitoring,
  say) must then be in a NetworkPolicy.

### Verified, and how

`tools/ci/openshift/lint.sh` lints base plus this component, and the example overlay. Live, in
kind 0.33 (three nodes, kindnet, which enforces NetworkPolicy) with CloudNativePG 1.30.1, the
component applied before the Cluster, and stand-in pods carrying the Atlas labels and ports:

- the Cluster came up with 2 instances and streaming replication under the policies;
- atlas-mcp to the database and to `atlas-api:8443`: refused; to `atlas-tls:9443`: open. A
  `verify-full` `psql` from atlas-mcp times out; from atlas-api and the migrate Job it connects;
- atlas-tls to atlas-api and atlas-mcp: open; to the database: refused;
- a pod in a namespace carrying the router's label: `atlas-tls:8443` open, `:9443`, atlas-api and
  the database refused. A pod in an unlabelled namespace, and an unlabelled pod in the Atlas
  namespace: everything refused;
- the httpGet and tcpSocket probes passed under default deny;
- without the 6443 rule a restarted instance waited on the API; without the operator's policy the
  Cluster reported `Instance Status Extraction Error`;
- with the template's three policies added, atlas-mcp to the database and to `atlas-api:8443`
  stayed refused, while the router's namespace and an unlabelled pod of the namespace reached every
  port.

Lint only, because kind cannot show them: the real OpenShift labels and namespaces
(`policy-group.network.openshift.io/ingress` on the real router, `openshift-dns` on 5353, the
operator's namespace) and OVN-Kubernetes's own enforcement. The OpenShift Local run (task 6.3)
and the first install settle those.

## What later tasks add

| Task | Adds |
|---|---|
| 3.2 | `base/`: the `atlas-mcp` Deployment and Service (it reuses `atlas-scripts` and `atlas-service-ca`) |
| 3.3 | `base/`: `atlas-tls` (Caddy on the UBI image) and the re-encrypt Route; the OpenShift Caddyfile |
| 3.4 | `components/networkpolicies/` (done, opt-in): default deny, then one allow per edge; moves into `base/` once proven on the company cluster |
| 3.5 | `components/cnpg/`: the CloudNativePG `Cluster` `atlas-db` and its backups |
| 3.6 | `components/`: `oidc`, `retrieval`, `mtls`, `external-secrets`, `gitops` |
| 3.7 | `overlays/example/` and `FIRST-INSTALL.md` (first step, done); then the full overlay and a GitLab CI template for the company pipeline |
| 3.8 | `base/`: the audit purge CronJob |
