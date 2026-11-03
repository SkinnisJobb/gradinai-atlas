"""
Verification for TASK-1433: the settings catalogue covers every service, and describe(env) reads
only the mapping it is given.

  A. COVERAGE, both ways, derived from the files (no pinned list):
       * every ATLAS_* name written in mcp/app/*.py is described, marked as read by atlas-mcp,
         and every entry marked atlas-mcp is written there;
       * every `${X}` that deploy/oss/*.yml interpolates is described, and every compose-only
         entry is either interpolated there or one of docker compose's own COMPOSE_* names;
       * every key deploy/oss/.env.example assigns (live or commented) and every key an example
         profile (docs/oss/examples/env/*.env) sets is described (the profiles' AZURE_* names
         the retrieval add-on reads excepted, as check_examples.py does);
       * every ATLAS_* name written in the API is in CATALOG and marked atlas-api; a CATALOG
         name also written in mcp/app is marked atlas-mcp too;
       * no name is in both CATALOG and the other services' file, each other entry has a known
         part and a summary, each compose entry says what compose does with it;
       * perturbations: a new name in an mcp module, and one in a compose file, are reported.
  B. THE PANEL IS atlas-api's, as before: describe() lists exactly CATALOG, none of the other
     services' entries, and change_refusal() calls another service's variable an unknown
     setting, the same answer as before it was catalogued.
  C. describe(env) EVALUATES EVERY SETTING FROM `env`. In a child whose os.environ (O) differs
     from the mapping (M) in every setting this checks, including the five once read from
     module attributes (ATLAS_GITHUB_REPO, ATLAS_ALLOW_SESSION_MINT, ATLAS_MACHINE_GRACE_HOSTS,
     ATLAS_TRUSTED_PROXY_CIDRS, ATLAS_FRONTEND_DIR) and ATLAS_DEPLOY_SRC/_WATCH_FILE and
     ATLAS_RETRIEVAL_TOP_K: describe(M) there equals, as JSON, describe() in a child whose
     os.environ IS M; and those entries' values in describe(M) differ from describe(O)'s, so the
     comparison could see a leak.
  D. NO PEPPERS NEEDED: with no pepper, database URL or login origin in os.environ, describe(M)
     returns with no error entry, and imports none of auth, passwords, deps, github_link,
     identity or db.
  E. THE SAME RULES AS THE RUNNING APP (the homelab's /api/config unchanged): in a child whose
     os.environ is M (with throwaway peppers), each of those entries equals what the module
     itself computes (auth.session_mint_enabled(), auth.MACHINE_GRACE_HOSTS,
     passwords.trusted_proxy_networks(), deps.FRONTEND, github_link.repo(),
     deploy_status.CHECKOUT_ROOT/STATUS_FILE, retrieval.query.top_k()), for a valid mapping, a
     malformed one (bad CIDR entries, a repo that is not owner/name, blank values) and an empty one.
  F. THE CONSUMERS: tools/gen_config_ref.py's `read_by` names atlas-api, atlas-mcp or compose
     from the catalogue and stops on a name the catalogue lacks; tools/check_examples.py's
     `known_key` has no fallback to mcp/app or the compose files (a compose name removed from
     the catalogue is then unknown).

No database. Values are throwaway, in a temporary directory.

Run:
    python verify_config_catalogue.py
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

FAILURES: list = []
FIVE = ("ATLAS_GITHUB_REPO", "ATLAS_ALLOW_SESSION_MINT", "ATLAS_MACHINE_GRACE_HOSTS",
        "ATLAS_TRUSTED_PROXY_CIDRS", "ATLAS_FRONTEND_DIR")
MORE = ("ATLAS_DEPLOY_SRC", "ATLAS_DEPLOY_WATCH_FILE", "ATLAS_RETRIEVAL_TOP_K")
PEPPER_MODULES = ("auth", "passwords", "deps", "github_link", "identity", "db")


def check(label: str, ok: bool, detail: str = "") -> None:
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", label, (" -- " + detail) if detail and not ok else ""))
    if not ok:
        FAILURES.append(label)


# --------------------------------------------------------------------------- the children

def _child(mode: str) -> int:
    mapping = json.loads(sys.stdin.read() or "null")
    import settings_catalog as C
    out: dict = {}
    if mode == "describe":
        out["body"] = C.describe(mapping)
        out["loaded"] = sorted(m for m in PEPPER_MODULES if m in sys.modules)
    elif mode == "modules":
        # What the running app's own modules compute from os.environ (E).
        import auth
        import deploy_status
        import deps
        import github_link
        import passwords
        from retrieval import query
        out["modules"] = {
            "ATLAS_GITHUB_REPO": github_link.repo(),
            "ATLAS_ALLOW_SESSION_MINT": auth.session_mint_enabled(),
            "ATLAS_MACHINE_GRACE_HOSTS": sorted(auth.MACHINE_GRACE_HOSTS),
            "ATLAS_TRUSTED_PROXY_CIDRS": [str(n) for n in passwords.trusted_proxy_networks()],
            "ATLAS_FRONTEND_DIR": deps.FRONTEND,
            "ATLAS_DEPLOY_SRC": deploy_status.CHECKOUT_ROOT,
            "ATLAS_DEPLOY_WATCH_FILE": deploy_status.STATUS_FILE,
            "ATLAS_RETRIEVAL_TOP_K": query.top_k(),
        }
        out["body"] = C.describe()
    print(json.dumps(out))
    return 0


if len(sys.argv) > 2 and sys.argv[1] == "--_child":
    sys.exit(_child(sys.argv[2]))


import settings_catalog as C  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="atlas-config-catalogue-"))


def _pepper() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def _file(name: str, text: str) -> str:
    p = TMP / name
    p.write_text(text, encoding="utf-8")
    return str(p)


PEPPERS = {
    "ATLAS_PASSWORD_PEPPER": _pepper(),
    "ATLAS_TOKEN_PEPPER": _pepper(),
    "ATLAS_LOGIN_ORIGINS": "https://atlas.example.com",
    "ATLAS_DB_URL": "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none",
}


def run(mode: str, os_env: dict, mapping=None) -> dict:
    env = dict(os_env)
    for keep in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "LANG", "APPDATA", "LOCALAPPDATA",
                 "USERPROFILE", "VIRTUAL_ENV", "LD_LIBRARY_PATH"):
        if os.environ.get(keep):
            env.setdefault(keep, os.environ[keep])
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p and os.path.isdir(p))
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_child", mode], env=env,
                       input=json.dumps(mapping), capture_output=True, text=True, timeout=180,
                       cwd=str(HERE))
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise SystemExit("[FAIL] the %s child crashed: %s" % (mode, r.stderr.strip()[-1500:]))


def entries(body: dict) -> dict:
    return {s["name"]: s for p in body["parts"] for s in p["settings"]}


def services_of(name: str) -> tuple:
    item = C.lookup(name)
    if item is None and name.endswith("_FILE"):
        base = C.lookup(name[: -len("_FILE")])
        item = base if base is not None and base.secret else None
    return item.services if item is not None else ()


_INTERP = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)")
_ASSIGN = re.compile(r"^\s*#?\s*([A-Z_][A-Z0-9_]*)=")


def mcp_names(extra: str = "") -> set:
    names = set()
    for p in sorted((ROOT / "mcp" / "app").glob("*.py")):
        names |= C.names_in_source(p.read_text(encoding="utf-8"))
    return names | (C.names_in_source(extra) if extra else set())


def interpolated(extra: str = "") -> set:
    names = set()
    for p in sorted((ROOT / "deploy" / "oss").glob("*.yml")):
        names |= set(_INTERP.findall(p.read_text(encoding="utf-8")))
    return names | set(_INTERP.findall(extra))


def azure_read_by_api() -> set:
    names = set()
    for p in sorted((HERE / "retrieval").glob("*.py")):
        names |= set(re.findall(r'"(AZURE_[A-Z0-9_]*[A-Z0-9])"', p.read_text(encoding="utf-8")))
    return names


try:
    # ------------------------------------------------------------------ A
    others = C.other_services()
    mcp = mcp_names()
    missing = sorted(n for n in mcp if C.MCP_SVC not in services_of(n))
    check("A: every ATLAS_* name in mcp/app/*.py is described as read by atlas-mcp",
          not missing, str(missing) if missing else "%d names" % len(mcp))
    stray = sorted(s.name for s in C.all_settings() if C.MCP_SVC in s.services and s.name not in mcp
                   and s.name + "_FILE" not in mcp)
    check("A: ... and every entry marked atlas-mcp is written there", not stray, str(stray))
    pert = sorted(n for n in mcp_names('X = "ATLAS_MCP_SOMETHING_NEW"\n')
                  if C.MCP_SVC not in services_of(n))
    check("A: ... perturbation: a new name in an mcp module is reported",
          pert == ["ATLAS_MCP_SOMETHING_NEW"], str(pert))

    interp = interpolated()
    missing = sorted(n for n in interp if not C.is_described(n))
    check("A: every ${X} in deploy/oss/*.yml is described", not missing,
          str(missing) if missing else "%d names" % len(interp))
    compose_entries = [s for s in others if C.COMPOSE_SVC in s.services]
    def read_by_script(s) -> bool:  # TASK-1460: a deploy script that reads it from .env itself
        path = ROOT / s.reader if s.reader else None
        return bool(path and path.is_file() and f'"{s.name}"' in path.read_text(encoding="utf-8"))

    stray = sorted(s.name for s in compose_entries
                   if s.name not in interp and not s.name.startswith("COMPOSE_") and not read_by_script(s))
    check("A: ... and every compose-only entry is interpolated there (or is a COMPOSE_* name, or "
          "its `reader` script names it)", not stray, str(stray))
    check("A: ... ATLAS_GATEWAY_SESSION_KEY_FILE is read by render-gateway.py (TASK-1460)",
          any(s.name == "ATLAS_GATEWAY_SESSION_KEY_FILE" and read_by_script(s) for s in compose_entries))
    pert = sorted(n for n in interpolated("x: ${ATLAS_SOMETHING_NEW:-y}\n") if not C.is_described(n))
    check("A: ... perturbation: a new interpolation is reported", pert == ["ATLAS_SOMETHING_NEW"], str(pert))
    check("A: the task's compose-only names are described as read by compose",
          all(C.COMPOSE_SVC in services_of(n) for n in
              ("ATLAS_PUBLIC_HOST", "ATLAS_TLS_MODE", "ATLAS_TLS_PORT", "ATLAS_TLS_BIND",
               "ATLAS_TLS_CLIENT_AUTH", "ATLAS_GATEWAY_BIND", "ATLAS_GATEWAY_PORT",
               "ATLAS_ORIGIN_SUBNET", "COMPOSE_PROFILES")))

    env_example = (ROOT / "deploy" / "oss" / ".env.example").read_text(encoding="utf-8")
    keys = {m.group(1) for line in env_example.splitlines() for m in [_ASSIGN.match(line)] if m}
    missing = sorted(k for k in keys if not C.is_described(k))
    check("A: every key deploy/oss/.env.example assigns is described", not missing,
          str(missing) if missing else "%d keys" % len(keys))
    azure = azure_read_by_api()
    prof_keys = set()
    for p in sorted((ROOT / "docs" / "oss" / "examples" / "env").glob("*.env")):
        prof_keys |= {m.group(1) for line in p.read_text(encoding="utf-8").splitlines()
                      for m in [re.match(r"^([A-Z_][A-Z0-9_]*)=", line)] if m}
    missing = sorted(k for k in prof_keys if not C.is_described(k) and k not in azure)
    check("A: every key an example profile sets is described (the add-on's AZURE_* reads aside)",
          not missing, str(missing) if missing else "%d keys" % len(prof_keys))

    api = C.names_in_api()
    not_api = sorted(n for n in api if not C.is_described(n, api_only=True)
                     or C.API_SVC not in services_of(n))
    check("A: every ATLAS_* name in the API is in CATALOG, marked atlas-api", not not_api, str(not_api))
    check("A: every CATALOG entry is marked atlas-api", all(C.API_SVC in s.services for s in C.CATALOG))
    shared = sorted(s.name for s in C.CATALOG if (s.name in mcp or s.name + "_FILE" in mcp)
                    and C.MCP_SVC not in s.services)
    check("A: a CATALOG name atlas-mcp also reads is marked atlas-mcp too", not shared, str(shared))
    check("A: ... ATLAS_DISABLED_FEATURES, the deploy hints and ATLAS_OIDC_ISSUER among them",
          all(set(services_of(n)) == {C.API_SVC, C.MCP_SVC} for n in
              ("ATLAS_DISABLED_FEATURES", "ATLAS_DEPLOY_HINT_HOST", "ATLAS_OIDC_ISSUER")))
    dup = sorted(s.name for s in others if s.name in C.BY_NAME)
    check("A: no name is both atlas-api's and another service's entry", not dup, str(dup))
    names = [s.name for s in others]
    check("A: no other-service name is described twice", len(names) == len(set(names)))
    check("A: every other entry has a known part, kind, a summary, one service, no loader",
          all(s.part in C.PARTS and s.kind in C.KINDS and s.summary.strip() and len(s.services) == 1
              and s.services[0] in C.SERVICES and s.loader is None for s in others))
    check("A: every compose entry says what compose does with it",
          all(s.compose_use.strip() for s in compose_entries))
    check("A: the other services' file is read on first use, not at import",
          "OTHER_FILE" in vars(C) and "json.loads" not in
          Path(C.__file__).read_text(encoding="utf-8").split("def other_services")[0])

    # ------------------------------------------------------------------ B
    body = C.describe({"ATLAS_LOGIN_ORIGINS": "https://atlas.example.com"})
    listed = [s["name"] for p in body["parts"] for s in p["settings"]]
    check("B: describe() lists exactly CATALOG (the panel's set, as before)",
          sorted(listed) == sorted(s.name for s in C.CATALOG) and len(listed) == len(C.CATALOG))
    check("B: ... and none of the other services' entries", not set(listed) & {s.name for s in others})
    refusals = {n: C.change_refusal(n) for n in ("ATLAS_MCP_ENABLE_DELETE", "ATLAS_PUBLIC_HOST",
                                                 "COMPOSE_PROFILES")}
    check("B: change_refusal() calls another service's variable an unknown setting, as before",
          all(r == ("unknown_setting", "%s is not a setting this API reads." % n)
              for n, r in refusals.items()), str(refusals))
    empty_mcp = next(p for p in body["parts"] if p["id"] == C.MCP)
    check("B: the atlas-mcp part is still empty on the panel, with its note",
          empty_mcp["settings"] == [] and empty_mcp.get("note") == C.EMPTY_PART_NOTES[C.MCP])

    # ------------------------------------------------------------------ C, D, E
    os.makedirs(TMP / "fe-m", exist_ok=True)
    group_map = _file("groups.yml", "groups:\n  idp-engineers: engineers\n")
    M = {
        **{k: v for k, v in PEPPERS.items() if k == "ATLAS_LOGIN_ORIGINS"},
        "ATLAS_GITHUB_REPO": "example-org/atlas-m",
        "ATLAS_ALLOW_SESSION_MINT": "yes",
        "ATLAS_MACHINE_GRACE_HOSTS": "Grace-M.example, other-m.example",
        "ATLAS_TRUSTED_PROXY_CIDRS": "198.51.100.0/24,2001:db8::/32",
        "ATLAS_FRONTEND_DIR": str(TMP / "fe-m"),
        "ATLAS_DEPLOY_SRC": "/srv/m-src",
        "ATLAS_DEPLOY_WATCH_FILE": "/srv/m-watch.json",
        "ATLAS_RETRIEVAL": "on", "ATLAS_EMBED_URL": "http://localhost:9/v1", "ATLAS_EMBED_MODEL": "m",
        "ATLAS_EMBED_DIM": "8", "ATLAS_RETRIEVAL_TOP_K": "17",
        "ATLAS_RBAC": "projects", "ATLAS_PUBLIC_URL": "https://atlas.example.com",
        "ATLAS_AUTH_PROVIDERS": "local,oidc", "ATLAS_OIDC_ISSUER": "https://idp.example.com",
        "ATLAS_OIDC_CLIENT_ID": "atlas-m",
        "ATLAS_OIDC_CLIENT_SECRET_FILE": _file("client-secret", "throwaway\n"),
        "ATLAS_OIDC_GROUP_MAP_FILE": group_map,
        "ATLAS_DISABLED_FEATURES": "github_link",
    }
    O = {
        **PEPPERS,
        "ATLAS_GITHUB_REPO": "other-org/atlas-o",
        "ATLAS_ALLOW_SESSION_MINT": "0",
        "ATLAS_MACHINE_GRACE_HOSTS": "grace-o.example",
        "ATLAS_TRUSTED_PROXY_CIDRS": "192.0.2.0/24",
        "ATLAS_FRONTEND_DIR": "/opt/o-frontend",
        "ATLAS_DEPLOY_SRC": "/srv/o-src",
        "ATLAS_DEPLOY_WATCH_FILE": "/srv/o-watch.json",
        "ATLAS_RETRIEVAL": "on", "ATLAS_EMBED_URL": "http://localhost:9/v1", "ATLAS_EMBED_MODEL": "o",
        "ATLAS_EMBED_DIM": "8", "ATLAS_RETRIEVAL_TOP_K": "5",
    }
    from_mapping = run("describe", O, M)
    as_environ = run("describe", {**M}, None)
    check("C: describe(M) in a process whose os.environ is O equals describe() where os.environ is M",
          json.dumps(from_mapping["body"]) == json.dumps(as_environ["body"]))
    em, eo = entries(from_mapping["body"]), entries(run("describe", O, O)["body"])
    for n in FIVE + MORE:
        check("C: ... %s is M's value, not os.environ's (%r vs %r)" % (n, em[n].get("value"), eo[n].get("value")),
              em[n].get("value") != eo[n].get("value") and "error" not in em[n] and em[n]["source"] == "env",
              str(em[n]))
    check("C: ... and no entry of describe(M) is an error",
          not [n for n, e in em.items() if "error" in e or "blocked_by" in e],
          str([n for n, e in em.items() if "error" in e or "blocked_by" in e]))

    bare = {k: v for k, v in M.items() if k != "ATLAS_LOGIN_ORIGINS"}
    no_pepper = run("describe", bare, M)
    check("D: with no pepper, database URL or origin in os.environ, describe(M) has no error entry",
          not [n for n, e in entries(no_pepper["body"]).items() if "error" in e or "blocked_by" in e])
    check("D: ... and imports none of %s" % ", ".join(PEPPER_MODULES), no_pepper["loaded"] == [],
          str(no_pepper["loaded"]))

    malformed = {**M, "ATLAS_TRUSTED_PROXY_CIDRS": "203.0.113.0/24, not-a-net, ,300.1.1.1",
                 "ATLAS_GITHUB_REPO": "no-slash", "ATLAS_MACHINE_GRACE_HOSTS": " , ",
                 "ATLAS_ALLOW_SESSION_MINT": "maybe", "ATLAS_FRONTEND_DIR": ""}
    for label, mapping in (("valid", M), ("malformed", malformed), ("empty", {})):
        child = run("modules", {**PEPPERS, **mapping}, None)
        got = entries(child["body"])
        # With the add-on off (the empty mapping), top-k is not read at all: shown as inactive.
        bad = {n: (got[n].get("value"), v) for n, v in child["modules"].items()
               if got[n].get("value") != v and "inactive" not in got[n]}
        check("E: %s mapping: each entry equals what the app's own module computes" % label,
              not bad, str(bad))

    # ------------------------------------------------------------------ F
    spec = importlib.util.spec_from_file_location("gen_config_ref", ROOT / "tools" / "gen_config_ref.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    check("F: gen_config_ref.read_by names who reads each kind of variable",
          gen.read_by("ATLAS_RBAC") == "atlas-api" and gen.read_by("ATLAS_MCP_ENABLE_DELETE") == "atlas-mcp"
          and gen.read_by("ATLAS_DISABLED_FEATURES") == "atlas-api, atlas-mcp"
          and gen.read_by("ATLAS_PUBLIC_HOST").startswith("docker compose: ")
          and gen.read_by("ATLAS_TOKEN_FILE") == "atlas-mcp",
          str([gen.read_by(n) for n in ("ATLAS_RBAC", "ATLAS_MCP_ENABLE_DELETE", "ATLAS_PUBLIC_HOST")]))
    try:
        gen.read_by("ATLAS_NOT_IN_THE_CATALOGUE")
        stopped = False
    except SystemExit:
        stopped = True
    check("F: ... and stops on a name the catalogue lacks", stopped)
    want = gen.render()
    have = (ROOT / "docs" / "oss" / "CONFIG.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    check("F: docs/oss/CONFIG.md is the generator's output (regenerated)", want == have)
    check("F: CONFIG.md names who reads every variable",
          have.count("- **Read by:** ") == have.count("\n### `"), "%d vs %d" % (
              have.count("- **Read by:** "), have.count("\n### `")))

    spec = importlib.util.spec_from_file_location("check_examples", ROOT / "tools" / "check_examples.py")
    ce = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ce)
    check("F: check_examples.known_key knows atlas-mcp's and compose's names from the catalogue",
          ce.known_key("ATLAS_MCP_ENABLE_DELETE") and ce.known_key("ATLAS_PUBLIC_HOST")
          and ce.known_key("COMPOSE_PROFILES") and not ce.known_key("ATLAS_NOT_IN_THE_CATALOGUE"))
    real = C._OTHER
    C._OTHER = tuple(s for s in C.other_services() if s.name not in ("ATLAS_PUBLIC_HOST", "ATLAS_MCP_ENABLE_DELETE"))
    try:
        gone = (ce.known_key("ATLAS_PUBLIC_HOST"), ce.known_key("ATLAS_MCP_ENABLE_DELETE"))
    finally:
        C._OTHER = real
    check("F: ... with no fallback: removed from the catalogue, a compose or mcp name is unknown",
          gone == (False, False), str(gone))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
if FAILURES:
    print("%d FAILED" % len(FAILURES))
    sys.exit(1)
print("all passed")
