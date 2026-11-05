# Atlas on OpenShift: the manifests

Kustomize manifests that run Atlas on OpenShift (built for Azure Red Hat OpenShift) in one
namespace, under the default `restricted-v2` SCC, with namespace-scoped rights only. The design,
and the reasons behind every choice here, are in
[Atlas on Azure Red Hat OpenShift](../../docs/oss/research/openshift-aro.md). This directory is
being built task by task; until the install guide (task 5.1) lands, this page is the map.

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
| `kustomization.yaml` | | the resource list, the common labels, the two ConfigMap generators |
| `serviceaccount.yaml` | ServiceAccount `atlas` | no token mounted, no Role bound: Atlas never calls the Kubernetes API |
| `atlas.env` | ConfigMap `atlas-env` | the non-secret settings, loaded with `envFrom` (the counterpart of `.env`) |
| `https_probe.py`, `db_url.py` | ConfigMap `atlas-scripts` | the exec probe and the database-URL init container, mounted at `/run/config/scripts/` |
| `configmap-service-ca.yaml` | ConfigMap `atlas-service-ca` | the OpenShift service CA bundle, injected as `service-ca.crt` |
| `service-api.yaml` | Service `atlas-api` | `:8443`, TLS only; its annotation makes the service CA issue Secret `atlas-api-tls` |
| `deployment-api.yaml` | Deployment `atlas-api` | 2 replicas, uvicorn TLS on `:8443`, every secret a file, read-only root filesystem |
| `pdb-api.yaml` | PodDisruptionBudget `atlas-api` | `minAvailable: 1` |
| `job-migrate.yaml` | Job `atlas-migrate-<version>` | `atlas-migrate upgrade`, once per release |
| `cronjob-audit-purge.yaml` | CronJob `atlas-audit-purge` | `atlas-admin audit purge` daily at 03:00 UTC (task 3.8) |

Every object carries `app.kubernetes.io/name: atlas`, `app.kubernetes.io/part-of: atlas`,
`app.kubernetes.io/version` and an `app.kubernetes.io/component` (`api`, `migrate`,
`audit-purge`, `config`);
the selectors use `name` and `component`, which the NetworkPolicies (task 3.4) will select on.

### What the pods need that the base does not create

| Secret | Keys used | Made by |
|---|---|---|
| `atlas-peppers` | `password_pepper`, `token_pepper` | `scripts/openshift/new-secrets.py` (see [the secrets page](../../docs/oss/guides/openshift-secrets.md)), or the External Secrets Operator |
| `atlas-db-app` | `fqdn-uri` only | the CloudNativePG `Cluster` named `atlas-db` (component `cnpg`, task 3.5) |
| `atlas-db-ca` | `ca.crt` only (never `ca.key`) | the same `Cluster` |
| `atlas-api-tls` | `tls.crt`, `tls.key` | the service CA, from the annotation on Service `atlas-api` |

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

## What an overlay must set

| What | How |
|---|---|
| the namespace | `namespace:` |
| the public host | `configMapGenerator` named `atlas-env` with `behavior: merge`, from the overlay's own env file: `ATLAS_PUBLIC_HOST`, `ATLAS_PUBLIC_URL`, `ATLAS_MCP_ALLOWED_HOSTS`, `ATLAS_MCP_ALLOWED_ORIGINS`, `ATLAS_PRIVACY_CONTACT`, `ATLAS_PRIVACY_HOSTING` |
| the trusted proxy range | a patch on the `atlas-api` container's `ATLAS_TRUSTED_PROXY_CIDRS` env entry: the pod network CIDR (`oc get network.config cluster -o jsonpath='{.spec.clusterNetwork[*].cidr}'`) |
| the images and the release | see the next section; the base's registry is a placeholder |
| one replica instead of two | a `replicas` patch, and delete the PDB (a `minAvailable: 1` on one replica blocks node drains) |

### Images and releases

The base names the Debian-based hardened API image of release `v0.0.1-alpha` by tag and by the
digest that release published (`ghcr.io/<owner>/gradinai-atlas-api`, listed in the release's
`release-images.env`; see [Releases](../../docs/oss/RELEASES.md)). Its registry,
`registry.example.com/gradinai-atlas`, is a placeholder, because the repository names no
account: **every overlay sets the image**, to GHCR or to the organisation's mirror of the same
digest. Task 1.4 publishes the UBI build (`gradinai-atlas-api-ubi`), which is the one to run on
OpenShift. Three things belong to one release and move together in the overlay:

```yaml
images:
  - name: registry.example.com/gradinai-atlas/gradinai-atlas-api    # matches the base, the Job and the init containers
    newName: registry.example.com/atlas/gradinai-atlas-api-ubi
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

## What later tasks add

| Task | Adds |
|---|---|
| 3.2 | `base/`: the `atlas-mcp` Deployment and Service (it reuses `atlas-scripts` and `atlas-service-ca`) |
| 3.3 | `base/`: `atlas-tls` (Caddy on the UBI image) and the re-encrypt Route; the OpenShift Caddyfile |
| 3.4 | `base/`: the NetworkPolicies (default deny, then one allow per compose network edge) |
| 3.5 | `components/cnpg/`: the CloudNativePG `Cluster` `atlas-db` and its backups |
| 3.6 | `components/`: `oidc`, `retrieval`, `mtls`, `external-secrets`, `gitops` |
| 3.7 | `overlays/example/` and a GitLab CI template for the company pipeline |
| 3.8 | `base/`: the audit purge CronJob |
