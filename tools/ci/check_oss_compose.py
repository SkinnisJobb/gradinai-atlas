#!/usr/bin/env python3
"""check_oss_compose.py: the OSS compose file, as compose renders it, keeps its promises (OSS-10, OSS-24).

Reads the output of `docker compose config --format json` for deploy/oss/docker-compose.yml
(so every ${...}, anchor and default is already resolved, exactly as `up` would use it) and
fails on any of:

  S1 secrets    an `environment:` value under a secret-shaped key (PEPPER, PASSWORD, TOKEN,
                SECRET, KEY, DB_URL, not ending in _FILE): secrets are compose `secrets:` files.
  S2 one node   an external network, volume or secret; `extra_hosts`; `network_mode`; a
                `depends_on` on a service not in the file; a non-file secret; an environment
                value naming host.docker.internal.
  S3 services   atlas-postgres, atlas-migrate, atlas-api, atlas-mcp, atlas-tls missing, or the
                Postgres image (its `image:`, or the FROM of the Dockerfile it builds) not
                pgvector/pgvector pinned tag@digest. With the overlay release.yml (OSS-26 E) the
                image is the RELEASED build of that Dockerfile, `<registry>/gradinai-atlas-postgres`,
                which must be pinned tag@digest too.
  S4 exposure   any published port other than atlas-tls's (and atlas-gateway's TLS port 8443);
                any publish of container port 8000 or 8092; atlas-mcp sharing a network with
                atlas-api (it must have no plain-HTTP route to the API); atlas-mcp's
                ATLAS_BASE_URL not https://; a non-TLS ingress.
  S5 profiles   without --profile: a service of the `retrieval` or `gateway` profile is
                rendered (it would start by default). With `--expect-profile retrieval`:
                atlas-retrieval missing; with `--expect-profile gateway`: atlas-gateway missing.
  S6 gateway    (when atlas-gateway is rendered) its image not pinned tag@digest; a network
                other than `gateway` (internal) and `gateway-edge`; a network shared with
                atlas-mcp or atlas-api (its only way in is atlas-tls's TLS front); its config
                not mounted read-only; atlas-tls not holding the `atlas-mcp` alias on `gateway`.
  S7 pulled     (OSS-26 E) any service that pulls rather than builds (an `image:` and no
                `build:`) whose image is not pinned tag@digest: the base file's gateway, and
                every service once release.yml swaps its build for a released image.
  S8 origin TLS (OSS-26 D) atlas-api or atlas-mcp not serving TLS itself: its `command` lacks
                `--ssl-certfile` / `--ssl-keyfile` (both given as /run/secrets/ files), listens
                on its old plain-HTTP port (8000, 8092), or its healthcheck probes http://;
                atlas-tls not given the origin CA (/certs/origin-ca.crt) to verify them with.
  S9 external   (TASK-1471) ONLY with `--external-db`, for a render that adds the overlay
     database   external-db.yml. Without the flag nothing below applies and a render with no
                atlas-postgres still fails S3, so the base file is judged exactly as before.
                With it, S3 no longer requires atlas-postgres and instead:
                  atlas-postgres rendered (the overlay is not in effect: it would start);
                  atlas-migrate or atlas-api (and atlas-backup when rendered) not on a
                  non-internal network (no route to the database), not given ATLAS_DB_URL_FILE
                  /run/secrets/db_url, or not mounting the `db_url` and `db_ca` secrets;
                  `db_url` or `db_ca` not a file secret; the `data` network no longer internal;
                  any service publishing PostgreSQL's port 5432 (the database is reached, never
                  exposed); and, on a POSIX host, the host files behind them: `db_url` must
                  exist, be readable by other UIDs (the containers' UID is not yours) and
                  writable by no one but its owner, inside a directory other UIDs cannot read
                  or enter (init-secrets.sh's 0644 in 0700); a `db_ca` that is a regular file
                  must be readable by other UIDs and writable only by its owner.

Usage:
  docker compose config --format json > oss.json && python tools/ci/check_oss_compose.py oss.json
  docker compose --profile retrieval config --format json > r.json && \
      python tools/ci/check_oss_compose.py r.json --expect-profile retrieval
  docker compose --profile gateway config --format json > g.json && \
      python tools/ci/check_oss_compose.py g.json --expect-profile gateway
  docker compose -f docker-compose.yml -f release.yml config --format json > rel.json && \
      python tools/ci/check_oss_compose.py rel.json
  docker compose -f docker-compose.yml -f external-db.yml --profile backup config --format json \
      > ext.json && python tools/ci/check_oss_compose.py ext.json --external-db
  python tools/ci/check_oss_compose.py --self-test

Exit 0 clean, 1 on a violation, 2 on a usage error. Standard library only.
"""

from __future__ import annotations

import copy
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

SECRET_KEY = re.compile(r"(PEPPER|PASSWORD|TOKEN|SECRET|KEY|DB_URL)$")
TOKEN_PORTS = {8000, 8092}
REQUIRED = ("atlas-postgres", "atlas-migrate", "atlas-api", "atlas-mcp", "atlas-tls")
INGRESS = "atlas-tls"
GATEWAY = "atlas-gateway"
PROFILES = {"retrieval": "atlas-retrieval", "gateway": GATEWAY}
PINNED = re.compile(r"^[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}$")
#: The released atlas-postgres (images.yml publishes deploy/postgres/pgvector.Dockerfile under it).
RELEASED_POSTGRES = re.compile(r"^(?:[^/@\s]+/)+gradinai-atlas-postgres:")
FROM_RE = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", re.IGNORECASE | re.MULTILINE)


def _postgres_base(svc: dict) -> str:
    """atlas-postgres's image: `image:`, or for a `build:` the last FROM of its Dockerfile
    (deploy/postgres/pgvector.Dockerfile: the pgvector image with gosu replaced). A Dockerfile
    that cannot be read yields "" and fails S3 like a wrong image would."""
    build = svc.get("build")
    if not build:
        return svc.get("image", "")
    if isinstance(build, str):
        build = {"context": build}
    path = Path(build.get("context", ".")) / build.get("dockerfile", "Dockerfile")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    froms = [m.group(1) for m in FROM_RE.finditer(text)]
    return froms[-1] if froms else ""


def _env(svc: dict) -> dict:
    env = svc.get("environment") or {}
    if isinstance(env, list):
        env = dict(e.split("=", 1) if "=" in e else (e, "") for e in env)
    return {k: ("" if v is None else str(v)) for k, v in env.items()}


#: OSS-26 D: the services that terminate TLS on the ingress's legs, and the CA atlas-tls checks.
ORIGIN_TLS = ("atlas-api", "atlas-mcp")
ORIGIN_CA_TARGET = "/certs/origin-ca.crt"


def _origin_tls(name: str, svc: dict) -> list:
    """S8: `name` terminates TLS itself on its ingress leg, and serves no plain-HTTP port."""
    bad = []
    cmd = svc.get("command") or []
    if isinstance(cmd, str):
        cmd = cmd.split()
    cmd = [str(c) for c in cmd]

    def opt(flag: str) -> str:
        for i, c in enumerate(cmd):
            if c == flag and i + 1 < len(cmd):
                return cmd[i + 1]
            if c.startswith(flag + "="):
                return c.split("=", 1)[1]
        return ""
    for flag in ("--ssl-certfile", "--ssl-keyfile"):
        val = opt(flag)
        if not val.startswith("/run/secrets/"):
            bad.append(f"S8 {name}: command has no {flag} /run/secrets/... (its ingress leg would be plain HTTP)")
    port = opt("--port")
    if not port.isdigit() or int(port) in TOKEN_PORTS:
        bad.append(f"S8 {name}: command listens on {port or 'the default port'!r}, not a TLS-only port")
    test = " ".join(str(x) for x in ((svc.get("healthcheck") or {}).get("test") or []))
    if "http://" in test or "https://" not in test:
        bad.append(f"S8 {name}: healthcheck does not probe its TLS listener over https://")
    return bad


#: TASK-1471, S9: the services that talk to the database, and the overlay's fixed names.
DB_CLIENTS = ("atlas-migrate", "atlas-api", "atlas-backup")
DB_URL_TARGET = "/run/secrets/db_url"
PG_PORT = 5432


def _secret_sources(svc: dict) -> set:
    return {s.get("source") if isinstance(s, dict) else s for s in svc.get("secrets") or []}


def _file_mode(label: str, path: str, dir_private: bool) -> list:
    """S9, POSIX only: the host file a DB secret is made from is readable by the containers' UID
    (not its owner's) and writable by nobody else; for the URL, its directory is closed to others.
    On Windows the mode bits mean nothing, so nothing is reported there."""
    if os.name != "posix":
        return []
    try:
        st = os.stat(path)
    except OSError:
        return [f"S9 secret {label}: its file {path!r} does not exist"]
    if not stat.S_ISREG(st.st_mode):
        # /dev/null, the overlay's default for db_ca: an empty mount nothing reads.
        return [f"S9 secret db_url: {path!r} is not a regular file"] if label == "db_url" else []
    bad = []
    mode = stat.S_IMODE(st.st_mode)
    if not mode & stat.S_IROTH:
        bad.append(f"S9 secret {label}: {path!r} is {mode:04o}; the containers run under a UID that "
                   "is not yours and cannot read it (0644, inside a 0700 directory)")
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        bad.append(f"S9 secret {label}: {path!r} is {mode:04o}, writable by group or others")
    if dir_private:
        dmode = stat.S_IMODE(os.stat(os.path.dirname(os.path.abspath(path))).st_mode)
        if dmode & (stat.S_IROTH | stat.S_IXOTH):
            bad.append(f"S9 secret {label}: the directory of {path!r} is {dmode:04o}; other users "
                       "could read the database password (0700, as init-secrets.sh makes it)")
    return bad


def _external_db(cfg: dict) -> list:
    """S9: the overlay external-db.yml is in effect and keeps its promises."""
    bad = []
    services = cfg.get("services") or {}
    nets = cfg.get("networks") or {}
    secrets = cfg.get("secrets") or {}
    if "atlas-postgres" in services:
        bad.append("S9 atlas-postgres is rendered: with external-db.yml it must not start "
                   "(profile bundled-postgres)")
    for name in DB_CLIENTS:
        svc = services.get(name)
        if svc is None:
            continue  # atlas-backup without --profile backup; S3 reports the other two missing
        own = set(svc.get("networks") or {})
        if all((nets.get(n) or {}).get("internal") for n in own):
            bad.append(f"S9 {name}: on internal networks only {sorted(own)}; it cannot reach the database")
        if _env(svc).get("ATLAS_DB_URL_FILE") != DB_URL_TARGET:
            bad.append(f"S9 {name}: ATLAS_DB_URL_FILE is not {DB_URL_TARGET}")
        for sec in ("db_url", "db_ca"):
            if sec not in _secret_sources(svc):
                bad.append(f"S9 {name}: does not mount the secret {sec}")
    if not (nets.get("data") or {}).get("internal"):
        bad.append("S9 the `data` network is not internal")
    for name, svc in services.items():
        for p in svc.get("ports") or []:
            if not isinstance(p, dict):
                continue
            published = str(p.get("published", ""))
            if PG_PORT in (int(p.get("target", 0)), int(published) if published.isdigit() else 0):
                bad.append(f"S9 {name} publishes PostgreSQL's port {PG_PORT}")
    for sec, dir_private in (("db_url", True), ("db_ca", False)):
        spec = secrets.get(sec)
        if not spec or not spec.get("file"):
            bad.append(f"S9 secret {sec} is not declared as a file")
            continue
        bad.extend(_file_mode(sec, spec["file"], dir_private))
    return bad


def check(cfg: dict, expect_profile: str = "", external_db: bool = False) -> list:
    bad = []
    services = cfg.get("services") or {}

    for name, svc in services.items():
        for key, val in _env(svc).items():
            if SECRET_KEY.search(key) and not key.endswith("_FILE") and val:
                bad.append(f"S1 {name}: environment {key} carries a value; pass it as a secret file")
            if "host.docker.internal" in val:
                bad.append(f"S2 {name}: environment {key} names host.docker.internal (outside this file)")
        if svc.get("extra_hosts"):
            bad.append(f"S2 {name}: extra_hosts reaches outside the file")
        if svc.get("network_mode"):
            bad.append(f"S2 {name}: network_mode {svc['network_mode']!r}")
        for dep in (svc.get("depends_on") or {}):
            if dep not in services:
                bad.append(f"S2 {name}: depends_on {dep!r}, which is not in this file")
    for kind in ("networks", "volumes", "secrets"):
        for key, spec in (cfg.get(kind) or {}).items():
            if (spec or {}).get("external"):
                bad.append(f"S2 top-level {kind[:-1]} {key!r} is external")
            if kind == "secrets" and not (spec or {}).get("file"):
                bad.append(f"S2 secret {key!r} is not a file")

    # S3. With --external-db (S9) the database is not in the file: atlas-postgres must be ABSENT,
    # which _external_db checks. Every other required service, and every other rule, is unchanged.
    for name in REQUIRED:
        if external_db and name == "atlas-postgres":
            continue
        if name not in services:
            bad.append(f"S3 service {name} is missing")
    if external_db:
        bad.extend(_external_db(cfg))
    else:
        pg = services.get("atlas-postgres") or {}
        image = _postgres_base(pg)
        if RELEASED_POSTGRES.match(image) and not pg.get("build"):
            if not PINNED.match(image):
                bad.append(f"S3 atlas-postgres image {image!r} is not pinned tag@digest")
        elif not image.startswith("pgvector/pgvector:"):
            bad.append(f"S3 atlas-postgres image {image!r} is not pgvector/pgvector")
        elif not PINNED.match(image):
            bad.append(f"S3 atlas-postgres image {image!r} is not pinned tag@digest")

    for name, svc in services.items():
        for p in svc.get("ports") or []:
            target = int(p.get("target", 0)) if isinstance(p, dict) else 0
            if name == GATEWAY and target == 8443:
                pass  # the gateway's own TLS door (S6 checks the rest of it)
            elif name != INGRESS:
                bad.append(f"S4 {name} publishes port {target}; only {INGRESS} may publish")
            if target in TOKEN_PORTS:
                bad.append(f"S4 {name} publishes plain-HTTP token port {target}")
    nets = lambda s: set((services.get(s) or {}).get("networks") or {})  # noqa: E731
    shared = nets("atlas-mcp") & nets("atlas-api")
    if shared:
        bad.append(f"S4 atlas-mcp shares network(s) {sorted(shared)} with atlas-api's plain listener")
    base = _env(services.get("atlas-mcp") or {}).get("ATLAS_BASE_URL", "")
    if not base.startswith("https://"):
        bad.append(f"S4 atlas-mcp ATLAS_BASE_URL {base!r} is not https://")
    if _env(services.get(INGRESS) or {}).get("CADDY_TLS_MODE", "") not in ("files", "internal", "acme-cloudflare"):
        bad.append(f"S4 {INGRESS} CADDY_TLS_MODE is not a TLS mode")

    for profile, svc_name in PROFILES.items():
        profiled = [n for n, s in services.items() if profile in (s.get("profiles") or [])]
        if expect_profile == profile:
            if svc_name not in profiled:
                bad.append(f"S5 --profile {profile} renders no {svc_name} service")
        elif profiled:
            bad.append(f"S5 {profiled} rendered without --profile {profile}: it would start by default")

    for name, svc in services.items():
        if svc.get("image") and not svc.get("build") and not PINNED.match(svc["image"]):
            bad.append(f"S7 {name} pulls {svc['image']!r}, which is not pinned tag@digest")

    for name in ORIGIN_TLS:
        if name in services:
            bad.extend(_origin_tls(name, services[name]))
    if INGRESS in services:
        targets = [s.get("target") for s in (services[INGRESS].get("secrets") or []) if isinstance(s, dict)]
        if ORIGIN_CA_TARGET not in targets:
            bad.append(f"S8 {INGRESS} has no {ORIGIN_CA_TARGET}: it cannot verify atlas-api's and atlas-mcp's listeners")

    gw = services.get(GATEWAY)
    if gw is not None:
        if not PINNED.match(gw.get("image") or ""):
            bad.append(f"S6 {GATEWAY} image {gw.get('image')!r} is not pinned tag@digest")
        gnets = set(gw.get("networks") or {})
        if gnets != {"gateway", "gateway-edge"}:
            bad.append(f"S6 {GATEWAY} networks {sorted(gnets)}; want exactly gateway, gateway-edge")
        if not ((cfg.get("networks") or {}).get("gateway") or {}).get("internal"):
            bad.append("S6 the `gateway` network is not internal")
        for other in ("atlas-mcp", "atlas-api"):
            if gnets & nets(other):
                bad.append(f"S6 {GATEWAY} shares network(s) {sorted(gnets & nets(other))} with {other}")
        mounts = [v for v in gw.get("volumes") or [] if isinstance(v, dict)
                  and v.get("target") == "/etc/agentgateway/config.yaml"]
        if not mounts or not all(m.get("read_only") for m in mounts):
            bad.append(f"S6 {GATEWAY} configuration is not mounted read-only")
        tls_gw = ((services.get(INGRESS) or {}).get("networks") or {}).get("gateway") or {}
        if "atlas-mcp" not in (tls_gw.get("aliases") or []):
            bad.append(f"S6 {INGRESS} does not answer as atlas-mcp on the `gateway` network")
    return bad


def _fixture() -> dict:
    svc = lambda **kw: {"networks": {"data": None}, **kw}  # noqa: E731
    return {
        "services": {
            "atlas-postgres": svc(image="pgvector/pgvector:0.8.7-pg16-trixie@sha256:" + "a" * 64,
                                  environment={"POSTGRES_PASSWORD_FILE": "/run/secrets/db_password"}),
            "atlas-migrate": svc(depends_on={"atlas-postgres": {}}),
            "atlas-api": {"networks": {"data": None, "origin": None},
                          "environment": {"ATLAS_TOKEN_PEPPER_FILE": "/run/secrets/token_pepper"},
                          "command": ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8443",
                                      "--ssl-certfile", "/run/secrets/api_origin_cert",
                                      "--ssl-keyfile", "/run/secrets/api_origin_key"],
                          "healthcheck": {"test": ["CMD", "python", "-c", "u.urlopen('https://127.0.0.1:8443/healthz')"]}},
            "atlas-mcp": {"networks": {"mcp": None}, "environment": {"ATLAS_BASE_URL": "https://atlas-api:9443"},
                          "command": ["python", "-m", "uvicorn", "app.main:app", "--port=8493",
                                      "--ssl-certfile=/run/secrets/mcp_origin_cert",
                                      "--ssl-keyfile=/run/secrets/mcp_origin_key"],
                          "healthcheck": {"test": ["CMD", "python", "-c", "u.urlopen('https://127.0.0.1:8493/health')"]}},
            "atlas-tls": {"networks": {"edge": None, "origin": None, "mcp": None},
                          "environment": {"CADDY_TLS_MODE": "files"},
                          "secrets": [{"source": "origin_ca", "target": "/certs/origin-ca.crt"}],
                          "ports": [{"target": 8443, "published": "443", "host_ip": "0.0.0.0"}]},
        },
        "networks": {"data": {"internal": True}, "origin": {}, "mcp": {}, "edge": {}},
        "secrets": {"token_pepper": {"file": "/x/secrets/token_pepper"}},
        "volumes": {"atlas-pgdata": {}},
    }


def self_test() -> int:
    rc = 0

    def case(want: int, label: str, mutate, profile: str = "") -> None:
        nonlocal rc
        cfg = copy.deepcopy(_fixture())
        mutate(cfg)
        got = 1 if check(cfg, profile) else 0
        print(f"self-test {'ok  ' if got == want else 'FAIL'} exit={got} (want {want})  {label}")
        rc |= 0 if got == want else 1

    s = lambda c: c["services"]  # noqa: E731
    case(0, "the fixture is clean", lambda c: None)
    case(1, "S1 a pepper as an env value", lambda c: s(c)["atlas-api"]["environment"].update(ATLAS_TOKEN_PEPPER="abc"))
    case(1, "S1 a DSN as an env value", lambda c: s(c)["atlas-api"]["environment"].update(ATLAS_DB_URL="postgresql://u:p@h/d"))
    case(1, "S2 an external network", lambda c: c["networks"].update(ext={"external": True}))
    case(1, "S2 extra_hosts", lambda c: s(c)["atlas-api"].update(extra_hosts=["db:192.0.2.1"]))
    case(1, "S2 depends_on outside the file", lambda c: s(c)["atlas-api"].update(depends_on={"outside-gateway": {}}))
    case(1, "S2 an environment secret", lambda c: c["secrets"].update(env_one={"environment": "X"}))
    case(1, "S3 plain postgres image", lambda c: s(c)["atlas-postgres"].update(image="postgres:16"))
    case(1, "S3 pgvector image without a digest",
         lambda c: s(c)["atlas-postgres"].update(image="pgvector/pgvector:0.8.7-pg16-trixie"))
    with tempfile.TemporaryDirectory() as tmp:
        def built(c, base: str | None, dockerfile: str = "pgvector.Dockerfile") -> None:
            if base is not None:
                (Path(tmp) / dockerfile).write_text(f"# a comment\nFROM {base}\nCOPY gosu /usr/local/bin/gosu\n",
                                                    encoding="utf-8")
            pg = s(c)["atlas-postgres"]
            pg.pop("image")
            pg["build"] = {"context": tmp, "dockerfile": dockerfile}
        case(0, "S3 built FROM a pinned pgvector image",
             lambda c: built(c, "pgvector/pgvector:0.8.7-pg16-trixie@sha256:" + "b" * 64))
        case(1, "S3 built FROM plain postgres",
             lambda c: built(c, "postgres:16-trixie@sha256:" + "b" * 64))
        case(1, "S3 built from a Dockerfile that is not there", lambda c: built(c, None, "missing.Dockerfile"))
    case(1, "S3 atlas-mcp missing", lambda c: s(c).pop("atlas-mcp"))
    rel = lambda name, tag: f"ghcr.io/example/gradinai-atlas-{name}:{tag}"  # noqa: E731

    def released(c, digest: str = "@sha256:" + "c" * 64) -> None:
        for svc, name in (("atlas-postgres", "postgres"), ("atlas-migrate", "api"), ("atlas-api", "api"),
                          ("atlas-mcp", "mcp"), ("atlas-tls", "tls")):
            s(c)[svc]["image"] = rel(name, "v0.0.1-alpha") + digest
    case(0, "S3/S7 release.yml: every service a pinned released image", released)
    case(1, "S3 released postgres without a digest", lambda c: released(c, ""))
    case(1, "S7 a released atlas-api pinned by tag only",
         lambda c: (released(c), s(c)["atlas-api"].update(image=rel("api", "v0.0.1-alpha"))))
    case(1, "S3 another registry's postgres image",
         lambda c: s(c)["atlas-postgres"].update(image="ghcr.io/example/postgres:16@sha256:" + "c" * 64))
    case(1, "S4 atlas-api publishes 8000 on loopback",
         lambda c: s(c)["atlas-api"].update(ports=[{"target": 8000, "published": "8095", "host_ip": "127.0.0.1"}]))
    case(1, "S4 atlas-mcp on atlas-api's network", lambda c: s(c)["atlas-mcp"]["networks"].update(origin=None))
    case(1, "S4 plain ATLAS_BASE_URL", lambda c: s(c)["atlas-mcp"]["environment"].update(ATLAS_BASE_URL="http://atlas-api:8000"))
    case(1, "S8 atlas-api on the image's CMD (plain HTTP :8000)", lambda c: s(c)["atlas-api"].pop("command"))
    case(1, "S8 atlas-api without --ssl-keyfile",
         lambda c: s(c)["atlas-api"].update(command=["uvicorn", "app:app", "--port", "8443",
                                                     "--ssl-certfile", "/run/secrets/api_origin_cert"]))
    case(1, "S8 atlas-mcp TLS on the old plain port 8092",
         lambda c: s(c)["atlas-mcp"]["command"].__setitem__(3, "--port=8092"))
    case(1, "S8 atlas-mcp healthcheck over http://",
         lambda c: s(c)["atlas-mcp"].update(healthcheck={"test": ["CMD", "curl", "http://localhost:8092/health"]}))
    case(1, "S8 atlas-mcp with no healthcheck of its own (the image's probes plain :8092)",
         lambda c: s(c)["atlas-mcp"].pop("healthcheck"))
    case(1, "S8 atlas-tls without the origin CA", lambda c: s(c)["atlas-tls"].pop("secrets"))
    case(1, "S5 a retrieval service rendered by default",
         lambda c: s(c).update({"atlas-retrieval": {"profiles": ["retrieval"], "networks": {"egress": None}}}))
    case(0, "S5 the retrieval service with --profile retrieval",
         lambda c: s(c).update({"atlas-retrieval": {"profiles": ["retrieval"], "networks": {"egress": None}}}), "retrieval")
    case(1, "S5 --profile retrieval without the service", lambda c: None, "retrieval")

    def with_gw(c, **over):
        s(c)["atlas-tls"]["networks"]["gateway"] = {"aliases": ["atlas-mcp"]}
        c["networks"].update({"gateway": {"internal": True}, "gateway-edge": {}})
        gw = {"profiles": ["gateway"],
              "image": "cr.agentgateway.dev/agentgateway:v1.7.0@sha256:" + "a" * 64,
              "networks": {"gateway": None, "gateway-edge": None},
              "ports": [{"target": 8443, "published": "8444", "host_ip": "0.0.0.0"}],
              "volumes": [{"type": "bind", "source": "/x/gateway/agentgateway.yaml",
                           "target": "/etc/agentgateway/config.yaml", "read_only": True}]}
        gw.update(over)
        s(c)[GATEWAY] = gw

    case(0, "S5/S6 the gateway with --profile gateway", with_gw, "gateway")
    case(1, "S5 the gateway rendered by default", with_gw)
    case(1, "S5 --profile gateway without the service", lambda c: None, "gateway")
    case(1, "S6 gateway image without a digest",
         lambda c: with_gw(c, image="cr.agentgateway.dev/agentgateway:v1.7.0"), "gateway")
    case(1, "S6 gateway on the mcp network (a plain route to atlas-mcp)",
         lambda c: with_gw(c, networks={"gateway": None, "gateway-edge": None, "mcp": None}), "gateway")
    case(1, "S4 gateway publishing another port",
         lambda c: with_gw(c, ports=[{"target": 15000, "published": "15000", "host_ip": "0.0.0.0"}]), "gateway")
    case(1, "S6 gateway configuration writable",
         lambda c: with_gw(c, volumes=[{"type": "bind", "source": "/x", "target": "/etc/agentgateway/config.yaml"}]),
         "gateway")
    case(1, "S6 the gateway network not internal",
         lambda c: (with_gw(c), c["networks"].update(gateway={})), "gateway")

    # S9 (TASK-1471): the overlay external-db.yml. The base fixture without atlas-postgres is
    # still an S3 failure when the flag is absent: the base rules did not loosen.
    case(1, "S3 no atlas-postgres, without --external-db", lambda c: s(c).pop("atlas-postgres"))
    with tempfile.TemporaryDirectory() as tmp:
        sec = Path(tmp) / "secrets"
        sec.mkdir()
        url, ca = sec / "db_url", sec / "db-ca.crt"
        url.write_text("postgresql+psycopg2://u:p@db.example:5432/atlas?sslmode=verify-full\n", encoding="utf-8")
        ca.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")

        def modes(dir_mode: int = 0o700, url_mode: int = 0o644, ca_mode: int = 0o644) -> None:
            if os.name == "posix":
                os.chmod(sec, dir_mode)
                os.chmod(url, url_mode)
                os.chmod(ca, ca_mode)

        def ext(c, **over) -> None:
            """The fixture as docker-compose.yml + external-db.yml (+ --profile backup) renders it."""
            modes(**over)
            s(c).pop("atlas-postgres")
            c["networks"].update(egress={})
            c["secrets"].update(db_url={"file": str(url)}, db_ca={"file": str(ca)})
            env = {"ATLAS_DB_URL_FILE": "/run/secrets/db_url"}
            s(c)["atlas-migrate"] = {"networks": {"data": None, "egress": None}, "environment": dict(env),
                                     "secrets": [{"source": "db_url"}, {"source": "db_ca"}]}
            s(c)["atlas-backup"] = {"profiles": ["backup"], "networks": {"data": None, "egress": None},
                                    "environment": dict(env), "secrets": [{"source": "db_url"}, {"source": "db_ca"}]}
            api = s(c)["atlas-api"]
            api["networks"]["egress"] = None
            api["environment"].update(env)
            api["secrets"] = [{"source": "db_url"}, {"source": "db_ca"}, {"source": "token_pepper"}]

        def ext_case(want: int, label: str, mutate=lambda c: None, **over) -> None:
            nonlocal rc
            cfg = copy.deepcopy(_fixture())
            ext(cfg, **over)
            mutate(cfg)
            got = 1 if check(cfg, external_db=True) else 0
            print(f"self-test {'ok  ' if got == want else 'FAIL'} exit={got} (want {want})  {label}")
            rc |= 0 if got == want else 1
            modes()

        ext_case(0, "S9 external-db.yml as shipped (0644 files in a 0700 directory)")
        ext_case(0, "S9 db_ca from /dev/null (sslmode=require, no CA)",
                 lambda c: c["secrets"].update(db_ca={"file": os.devnull}))
        ext_case(1, "S9 atlas-postgres still rendered",
                 lambda c: s(c).update({"atlas-postgres": _fixture()["services"]["atlas-postgres"]}))
        ext_case(1, "S9 atlas-migrate on internal networks only",
                 lambda c: s(c)["atlas-migrate"].update(networks={"data": None}))
        ext_case(1, "S9 atlas-backup on internal networks only",
                 lambda c: s(c)["atlas-backup"].update(networks={"data": None}))
        ext_case(1, "S9 atlas-api without the db_ca secret",
                 lambda c: s(c)["atlas-api"].update(secrets=[{"source": "db_url"}]))
        ext_case(1, "S9 atlas-migrate reading the URL from another path",
                 lambda c: s(c)["atlas-migrate"]["environment"].update(ATLAS_DB_URL_FILE="/tmp/url"))
        ext_case(1, "S1 the URL as an environment value instead",
                 lambda c: s(c)["atlas-api"]["environment"].update(ATLAS_DB_URL="postgresql://u:p@h/d"))
        ext_case(1, "S9 the data network made non-internal", lambda c: c["networks"].update(data={}))
        ext_case(1, "S9 a published 5432",
                 lambda c: s(c)["atlas-tls"]["ports"].append({"target": 5432, "published": "5432"}))
        ext_case(1, "S9 db_ca not a file secret", lambda c: c["secrets"].update(db_ca={"environment": "X"}))
        ext_case(1, "S9 S3 atlas-api missing still fails", lambda c: s(c).pop("atlas-api"))
        if os.name == "posix":
            ext_case(1, "S9 db_url 0600 (the containers' UID cannot read it)", url_mode=0o600)
            ext_case(1, "S9 db_url group-writable", url_mode=0o664)
            ext_case(1, "S9 db_url in a directory others can enter", dir_mode=0o755)
            ext_case(1, "S9 db_ca world-writable", ca_mode=0o666)
            ext_case(1, "S9 db_url file missing",
                     lambda c: c["secrets"].update(db_url={"file": str(sec / "absent")}))
        else:
            print("self-test skip  S9 file modes: not a POSIX host")
    return rc


def main(argv: list) -> int:
    args = argv[1:]
    if args == ["--self-test"]:
        return self_test()
    profile = ""
    if "--expect-profile" in args:
        i = args.index("--expect-profile")
        if i + 1 >= len(args):
            return 2
        profile = args[i + 1]
        del args[i:i + 2]
    external_db = "--external-db" in args
    if external_db:
        args.remove("--external-db")
    if len(args) != 1:
        print(__doc__.split("Usage:")[1].split("Exit")[0], file=sys.stderr)
        return 2
    with open(args[0], encoding="utf-8") as fh:
        cfg = json.load(fh)
    bad = check(cfg, profile, external_db)
    for b in bad:
        print("FAIL " + b)
    if bad:
        print(f"check_oss_compose: FAILED ({len(bad)})")
        return 1
    print(f"check_oss_compose: clean ({len(cfg.get('services') or {})} services"
          f"{', profile ' + profile if profile else ''}{', external database' if external_db else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
