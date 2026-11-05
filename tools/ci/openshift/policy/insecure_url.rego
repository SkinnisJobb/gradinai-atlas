# [insecure-url] Every configured URL is https:// (TASK-1487).
#
# The design's lint list (docs/oss/research/openshift-aro.md, question 11) asks for "every
# token-carrying URL https://". In Atlas a configured URL nearly always carries one (the API URL
# atlas-mcp sends the caller's Bearer to, the embeddings endpoint and its key, the OIDC issuer),
# and an in-cluster hop is TLS with a service-CA certificate, so the rule is simply: a URL-shaped
# key (`*_URL`, `*_URI`, `*_ENDPOINT`, `*_ISSUER`) with a literal `http://` value is refused,
# in a container's env and in a ConfigMap (which reaches the env through envFrom).
package main

import rego.v1

import data.lib

deny contains msg if {
	some c in lib.containers
	some e in c.env
	is_string(e.value)
	regex.match(lib.url_name_re, e.name)
	startswith(lower(e.value), "http://")
	msg := sprintf("[insecure-url] %s container %q: env %q is plain http://", [lib.name, c.name, e.name])
}

deny contains msg if {
	input.kind == "ConfigMap"
	some k, v in input.data
	regex.match(lib.url_name_re, k)
	startswith(lower(v), "http://")
	msg := sprintf("[insecure-url] %s: key %q is plain http://", [lib.name, k])
}
