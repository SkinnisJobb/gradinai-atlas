# [run-as-user] The base and the components pin no UID (TASK-1487).
#
# On OpenShift the restricted-v2 SCC assigns each pod a UID from the namespace's range and
# rejects a fixed runAsUser outside it, so the shipped manifests must leave it unset
# (docs/oss/research/openshift-aro.md, question 11). Only a tree that knows its cluster may pin
# one: the kind CI overlay sets the SCC's UID on purpose, and an organisation's overlay may too.
#
# This rule is in its own namespace, `base`, so tools/ci/openshift/lint.sh runs it on the
# renders of `base/` and of `base/` plus each component, and not on the overlays.
package base

import rego.v1

import data.lib

deny contains msg if {
	lib.pod_spec.securityContext.runAsUser
	msg := sprintf("[run-as-user] %s: the pod pins runAsUser; leave it to the SCC", [lib.name])
}

deny contains msg if {
	some c in lib.containers
	c.securityContext.runAsUser
	msg := sprintf("[run-as-user] %s container %q: pins runAsUser; leave it to the SCC", [lib.name, c.name])
}
