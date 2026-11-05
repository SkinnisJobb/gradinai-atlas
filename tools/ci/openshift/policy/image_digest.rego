# [image-digest] Every image is referenced by digest (TASK-1487).
#
# A tag is whatever the registry serves that day; a digest is the image that was built, scanned
# and attested. The organisation's pipeline mirrors upstream's images by digest and verifies
# their provenance (docs/oss/research/openshift-aro.md, question 11), which only means anything
# if the manifests name digests. `name:tag@sha256:...` and `name@sha256:...` both pass.
# Containers in every workload kind are checked, and so is a CloudNativePG Cluster's imageName
# (the operator starts PostgreSQL from it).
package main

import rego.v1

import data.lib

digest_re := `@sha256:[0-9a-f]{64}$`

deny contains msg if {
	some c in lib.containers
	not regex.match(digest_re, c.image)
	msg := sprintf("[image-digest] %s container %q: image %q is not pinned by digest", [lib.name, c.name, c.image])
}

deny contains msg if {
	input.kind == "Cluster"
	startswith(input.apiVersion, "postgresql.cnpg.io/")
	image := input.spec.imageName
	not regex.match(digest_re, image)
	msg := sprintf("[image-digest] %s: imageName %q is not pinned by digest", [lib.name, image])
}
