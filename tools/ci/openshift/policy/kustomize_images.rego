# [image-digest] A Kustomize `images:` entry pins a digest (TASK-1487).
#
# The same rule as image_digest.rego, applied to the kustomization files rather than to their
# render: an overlay that moves an image with `newName` / `newTag` must also give `digest`
# (`sha256:` and 64 hex). The render check alone would let through an entry that matches no
# container today and starts matching one after a rename. A tag smuggled into `newTag` as
# `1.0@sha256:...` is not accepted either: `digest` is the field Kustomize and Dependabot read.
#
# In its own namespace, `kustomization`: tools/ci/openshift/lint.sh feeds it every kustomization
# file under deploy/openshift/, and only those.
package kustomization

import rego.v1

name := object.get(input, ["metadata", "name"], "kustomization")

deny contains msg if {
	some img in input.images
	not regex.match(`^sha256:[0-9a-f]{64}$`, object.get(img, "digest", ""))
	msg := sprintf(
		"[image-digest] %s: images entry %q has no digest (newName %q, newTag %q); set `digest: sha256:...`",
		[name, object.get(img, "name", ""), object.get(img, "newName", ""), object.get(img, "newTag", "")],
	)
}
