# Shared helpers for the OpenShift manifest policies (TASK-1487, ARO 6.1).
#
# conftest feeds each rendered document to the policies on its own; these helpers find the pod
# spec in whatever workload kind carries one, and every container in it. A kind that carries no
# pod spec (Service, Route, ConfigMap, a CNPG Cluster) has none, so the container rules skip it.
package lib

import rego.v1

# Kinds whose pod spec sits at spec.template.spec.
template_kinds := {
	"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet",
	"ReplicationController", "Job", "DeploymentConfig",
}

pod_spec := input.spec if input.kind == "Pod"

pod_spec := input.spec.template.spec if input.kind in template_kinds

pod_spec := input.spec.jobTemplate.spec.template.spec if input.kind == "CronJob"

containers contains c if some c in pod_spec.containers

containers contains c if some c in pod_spec.initContainers

containers contains c if some c in pod_spec.ephemeralContainers

# "Deployment/atlas-api", for messages.
name := sprintf("%s/%s", [input.kind, input.metadata.name])

# An env or ConfigMap key whose value is a secret: a pepper, a database URL (it carries the
# password), a secret, a key, a token or a password. `_FILE` names (the path to the mounted
# file) do not match, and neither do words that merely end in the letters (`MONKEY`).
secret_name_re := `(?i)(^|_)(PEPPER|DB_URL|SECRET|KEY|TOKEN|PASSWORD|PASSWD)$`

# A key whose value is a URL.
url_name_re := `(?i)(^|_)(URL|URI|ENDPOINT|ISSUER)$`
