# [automount-token] No pod gets a Kubernetes API token (TASK-1487).
#
# Nothing in Atlas calls the Kubernetes API, so a mounted ServiceAccount token is only something
# to steal (docs/oss/research/openshift-aro.md, question 7). Every pod spec must say
# `automountServiceAccountToken: false` itself: the pod's field wins over its ServiceAccount's,
# so setting it on the pod holds whichever ServiceAccount an overlay assigns.
package main

import rego.v1

import data.lib

deny contains msg if {
	spec := lib.pod_spec
	not spec.automountServiceAccountToken == false
	msg := sprintf("[automount-token] %s: automountServiceAccountToken is not false", [lib.name])
}
