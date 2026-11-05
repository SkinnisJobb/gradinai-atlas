# [security-context] Every container runs under the restricted profile (TASK-1487).
#
# What OpenShift's `restricted-v2` SCC and Pod Security `restricted` both demand, written into
# the manifests so they hold on any cluster (docs/oss/research/openshift-aro.md, question 11):
#   - runAsNonRoot: true (on the container, or on the pod and not overridden to false);
#   - allowPrivilegeEscalation: false on every container (there is no pod-level field);
#   - not privileged, and capabilities.drop includes ALL with nothing added;
#   - seccompProfile.type RuntimeDefault (on the container, or on the pod and not overridden).
# Whether a fixed runAsUser is allowed is a separate rule (base/run_as_user.rego): it depends on
# which tree the manifest comes from.
package main

import rego.v1

import data.lib

non_root(c) if c.securityContext.runAsNonRoot == true

non_root(c) if {
	lib.pod_spec.securityContext.runAsNonRoot == true
	not c.securityContext.runAsNonRoot == false
}

seccomp_default(c) if c.securityContext.seccompProfile.type == "RuntimeDefault"

seccomp_default(c) if {
	lib.pod_spec.securityContext.seccompProfile.type == "RuntimeDefault"
	not c.securityContext.seccompProfile.type
}

deny contains msg if {
	some c in lib.containers
	not non_root(c)
	msg := sprintf("[security-context] %s container %q: runAsNonRoot is not true", [lib.name, c.name])
}

deny contains msg if {
	some c in lib.containers
	not c.securityContext.allowPrivilegeEscalation == false
	msg := sprintf("[security-context] %s container %q: allowPrivilegeEscalation is not false", [lib.name, c.name])
}

deny contains msg if {
	some c in lib.containers
	c.securityContext.privileged == true
	msg := sprintf("[security-context] %s container %q: privileged", [lib.name, c.name])
}

deny contains msg if {
	some c in lib.containers
	not "ALL" in object.get(c, ["securityContext", "capabilities", "drop"], [])
	msg := sprintf("[security-context] %s container %q: capabilities.drop does not include ALL", [lib.name, c.name])
}

deny contains msg if {
	some c in lib.containers
	count(object.get(c, ["securityContext", "capabilities", "add"], [])) > 0
	msg := sprintf("[security-context] %s container %q: capabilities.add is not empty", [lib.name, c.name])
}

deny contains msg if {
	some c in lib.containers
	not seccomp_default(c)
	msg := sprintf("[security-context] %s container %q: seccompProfile.type is not RuntimeDefault", [lib.name, c.name])
}
