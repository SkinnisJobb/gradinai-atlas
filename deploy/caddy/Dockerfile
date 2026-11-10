# Caddy with the Cloudflare DNS provider compiled in (TASK-964).
#
# WHY A CUSTOM BUILD. The official caddy image cannot solve an ACME DNS-01
# challenge for Cloudflare — the provider is a plugin and is not in the stock
# binary. And DNS-01 is not a preference here: the certificate is for
# atlas.example.com, which resolves to the server's private (RFC1918) LAN address.
# Let's Encrypt cannot reach a private IP, so the HTTP-01 and TLS-ALPN-01
# challenges are structurally impossible. Proving control of the DNS record is
# the only route to a publicly-trusted certificate for a LAN-only host.
#
# The alternative — a self-signed CA installed on every device — was rejected:
# it is per-device manual work forever, and a browser that has been taught to
# click through certificate warnings is a browser that will click through a real
# one.
# Pinned by digest per ADR 0006 / TASK-926 item 1, resolved on the server
# 2026-07-31. Both tags were Caddy v2.11.4 at the time of pinning.
#
# Pin the BUILDER too, not just the runtime: the builder is what compiles the
# plugin into the binary that terminates TLS, so an unpinned builder means the
# thing actually doing the crypto is whatever floated past that day. To bump
# either, re-resolve with `docker pull caddy:2 && docker image inspect caddy:2
# --format '{{index .RepoDigests 0}}'` and record the version here.
# Tag added (TASK-1365): digest unchanged, read on the server 2026-10-25; its image config says CADDY_VERSION=v2.11.4.
FROM caddy:2.11.9-builder@sha256:3fddbec729442a56b942d4c9eaef1df80893dbf2cf5b86701bfae6aad1408eeb AS builder
# go-jose forced to v4.1.6 (TASK-1434): CVE-2026-116587 (HIGH, DoS parsing an RSA JWK), and the
# Caddy 2.11.7 dependency graph resolves v4.1.5. `--replace` writes a go.mod replace directive to an
# exact tag, so the build stays reproducible and only that one module moves; Caddy and the plugin
# are unchanged. Drop this flag once the Caddy release pinned above requires go-jose >= 4.1.6.
# x/net forced to v0.62.0 (2026-11-10) the same way: CVE-2026-78661 (HIGH, x/net/html DoS, fixed in
# 0.62.0) and CVE-2026-78669 (HIGH, fixed in 0.60.0); Caddy 2.11.7 resolves v0.59.0. Drop the flag
# once the Caddy release pinned above requires x/net >= 0.62.0.
RUN xcaddy build --with github.com/caddy-dns/cloudflare     --replace github.com/go-jose/go-jose/v4=github.com/go-jose/go-jose/v4@v4.1.6     --replace golang.org/x/net=golang.org/x/net@v0.62.0

# Tag added (TASK-1365): digest unchanged, read on the server 2026-10-25; its image config says CADDY_VERSION=v2.11.4.
FROM caddy:2.11.9@sha256:0c2243efcf437ab7f7a978f1027fcf0d4f9be8a75df27b1b3fbc5dbe0ffa8cf0
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
# curl is not used here: no healthcheck, Caddyfile or test calls it inside a caddy container
# (atlas-tls checks itself with busybox `nc`), and only curl depends on libcurl. Removed rather
# than upgraded, so the image carries less and the build stays offline: CVE-2026-92392 (HIGH,
# curl 8.22.0-r0, 2026-10-26) had no fixed caddy:2.11.7 digest yet. If a container ever needs an
# HTTP probe, use busybox `wget`, not a package added back here.
RUN apk del --no-cache curl libcurl
