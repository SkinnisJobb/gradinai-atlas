"""Kubelet exec probe: GET a path on this pod's own TLS listener, certificate verified (TASK-1485).

Why an exec probe and not `httpGet`: the kubelet's HTTPS `httpGet` probe does not verify the
certificate, and Atlas's rule is that the origin listener is checked against the CA that issued
it, as the compose healthchecks do (deploy/oss/docker-compose.yml, atlas-api and atlas-mcp). So
this script, run inside the container with the image's own Python, does what the compose
healthcheck does, adapted to the OpenShift service CA:

  - it dials 127.0.0.1:<port> (this pod, not the Service, so one sick replica fails its own probe);
  - it verifies the certificate against the injected service-CA bundle
    (/run/config/service-ca/service-ca.crt) AND checks the name in it. The service CA issues for
    `<service>.<namespace>.svc` only (no 127.0.0.1 SAN, unlike the compose origin certificates),
    so the TLS server name is set to that and the socket still goes to loopback;
  - with `public` as the fourth argument it sends `Host: $ATLAS_PUBLIC_HOST`: atlas-api's Host
    allow-list answers 421 to any other Host, /healthz and /readyz included (research F8).
    Without it the Host is the service name (atlas-mcp's /health is outside its Host check).

Exit 0 on HTTP 200, 1 on anything else (a refused connection, a bad certificate, another status).
Standard library only; it prints one line on failure, which the kubelet records in the pod's
events. It reads no secret and sends no token.

    python -I /run/config/scripts/https_probe.py PORT PATH SERVICE [public]
"""
import os
import socket
import ssl
import sys

CA_FILE = "/run/config/service-ca/service-ca.crt"


def main(argv):
    if len(argv) not in (4, 5) or (len(argv) == 5 and argv[4] != "public"):
        print("usage: https_probe.py PORT PATH SERVICE [public]")
        return 2
    port, path, service = int(argv[1]), argv[2], argv[3]
    server_name = "%s.%s.svc" % (service, os.environ["POD_NAMESPACE"])
    host = os.environ["ATLAS_PUBLIC_HOST"] if len(argv) == 5 else server_name
    context = ssl.create_default_context(cafile=CA_FILE)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=4) as raw:
            with context.wrap_socket(raw, server_hostname=server_name) as tls:
                tls.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n"
                             % (path, host)).encode("ascii"))
                status_line = tls.recv(256).split(b"\r\n", 1)[0]
    except (OSError, ssl.SSLError) as exc:
        print("probe %s %s: %s" % (server_name, path, exc))
        return 1
    parts = status_line.split(b" ", 2)
    if len(parts) >= 2 and parts[1] == b"200":
        return 0
    print("probe %s %s: %s" % (server_name, path, status_line.decode("ascii", "replace")))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
