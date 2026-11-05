# [env-secret] No secret reaches a container through its environment (TASK-1487).
#
# Secrets are files only (docs/oss/research/openshift-aro.md, question 7): every Secret is a
# `secret` volume and the `*_FILE` settings point at it. An environment variable leaks into
# `/proc/<pid>/environ`, crash dumps, `oc describe` and child processes, so:
#   - a `valueFrom.secretKeyRef` in `env` is a violation, whatever the variable is called;
#   - an `envFrom.secretRef` is a violation (it turns every key of the Secret into a variable);
#   - a literal `value` under a secret-shaped name (`*_PEPPER`, `*_DB_URL`, `*_SECRET`, `*_KEY`,
#     `*_TOKEN`, `*_PASSWORD`) is a violation: the secret is then in the manifest itself;
#   - the same secret-shaped key in a ConfigMap is a violation, because the planned `atlas-env`
#     ConfigMap reaches the containers through `envFrom`.
package main

import rego.v1

import data.lib

deny contains msg if {
	some c in lib.containers
	some e in c.env
	ref := e.valueFrom.secretKeyRef
	msg := sprintf(
		"[env-secret] %s container %q: env %q reads Secret %q through secretKeyRef; mount the Secret as a file and set the *_FILE setting",
		[lib.name, c.name, e.name, ref.name],
	)
}

deny contains msg if {
	some c in lib.containers
	some f in c.envFrom
	ref := f.secretRef
	msg := sprintf(
		"[env-secret] %s container %q: envFrom reads Secret %q into the environment; mount it as a file instead",
		[lib.name, c.name, ref.name],
	)
}

deny contains msg if {
	some c in lib.containers
	some e in c.env
	is_string(e.value)
	regex.match(lib.secret_name_re, e.name)
	msg := sprintf(
		"[env-secret] %s container %q: env %q has a literal value; a secret belongs in a Secret mounted as a file",
		[lib.name, c.name, e.name],
	)
}

deny contains msg if {
	input.kind == "ConfigMap"
	some k, _ in input.data
	regex.match(lib.secret_name_re, k)
	msg := sprintf(
		"[env-secret] %s: key %q looks like a secret; a ConfigMap is not a Secret, and its keys reach the environment through envFrom",
		[lib.name, k],
	)
}
