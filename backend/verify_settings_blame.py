"""
Verification for TASK-1439: the settings catalogue blames the variable that is wrong.

`settings_catalog.describe()` evaluates a setting through its section's loader. When a loader
refused the environment, every setting of that section used to carry the same error, so the
first one in the catalogue (ATLAS_AUTH_PROVIDERS for an out-of-range
ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS; ATLAS_ALLOW_SESSION_MINT for a broken OIDC group map, which
stops `import auth`) was the one an operator read first. Now the exception says which variable
it is about (`variable`, set where it is raised: settings._NamedError, identity.groups,
auth.MissingSecretError), and describe() reports it there.

  A. Where the errors are raised: an out-of-range or non-numeric step-up max age is a
     SettingsError whose `variable` is ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS; a missing, non-YAML
     or wrongly shaped group map is a SettingsError whose `variable` is
     ATLAS_OIDC_GROUP_MAP_FILE; a SecretError names its secret; every message is the text it
     was before; `import auth` with the out-of-range value stops with a MissingSecretError
     that carries the variable of the settings error it wraps (the startup error).
  B. describe(), in a child process whose whole environment is the configuration (so the
     modules read at import, `auth` among them, see it too), with throwaway peppers and an
     unreachable database URL:
       * ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS=3600 -> that variable, and only it, carries `error`;
         ATLAS_AUTH_PROVIDERS and the other identity settings carry `blocked_by` naming it;
       * a missing group map file, a file that is not YAML, a file of the wrong shape ->
         ATLAS_OIDC_GROUP_MAP_FILE, and only it, carries `error`; ATLAS_ALLOW_SESSION_MINT
         keeps its value: since TASK-1433 its reader no longer imports `auth`, and the
         catalogue checks the group map itself (settings.load_group_map, only when oidc is a
         provider, as the provider does at boot; a map set without oidc is not read).
  C. Valid configurations describe exactly as before: the output equals, byte for byte as JSON,
     a FROZEN copy of the pre-change describe() (below) for the default, an OIDC + RBAC install
     with a valid group map, and the retrieval add-on on; perturbation: the frozen copy and the
     new code DIFFER on the out-of-range configuration, so the comparison can see a change.
  D. An error that names no catalogued plain setting is still reported against every setting
     of its loader, as before.

No database: describe() reads no row. Values are throwaway, in a temporary directory.

Run:
    python verify_settings_blame.py
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FAILURES: list = []
STEP_UP = "ATLAS_OIDC_STEP_UP_MAX_AGE_SECONDS"
GROUP_MAP = "ATLAS_OIDC_GROUP_MAP_FILE"


def check(label: str, ok: bool, detail: str = "") -> None:
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", label, (" -- " + detail) if detail and not ok else ""))
    if not ok:
        FAILURES.append(label)


# --------------------------------------------------------------------------- #
# The FROZEN pre-change describe() (master 117669c), reading the module's own catalogue and
# loaders. It is the oracle for C: do not edit it to make C pass.
# --------------------------------------------------------------------------- #

def frozen_describe(C, environ=None):
    env = os.environ if environ is None else environ
    makers = C._loaders(env, None, None)
    cache: dict = {}

    def loaded(name):
        if name not in cache:
            try:
                cache[name] = (True, makers[name]())
            except C._Inactive:
                cache[name] = (None, None)
            except Exception as exc:  # noqa: BLE001
                cache[name] = (False, type(exc).__name__)
        return cache[name]

    parts = {p: [] for p in C.PARTS}
    # Every entry of the pre-change catalogue was one atlas-api reads; TASK-1433 added atlas-mcp
    # and compose-only entries, which describe() leaves out. The loop is otherwise untouched.
    for item in (i for i in C.CATALOG if "atlas-api" in getattr(i, "services", ("atlas-api",))):
        entry = {"name": item.name, "kind": item.kind, "secret": item.secret,
                 "summary": item.summary}
        if item.secret:
            entry["state"], entry["source"] = C.secret_state(env, item)
        else:
            if C._set(env, item.name):
                entry["source"] = "env"
            elif any(C._set(env, d) for d in item.derived_from):
                entry["source"] = "derived"
                entry["derived_from"] = [d for d in item.derived_from if C._set(env, d)]
            else:
                entry["source"] = "default"
            ok, result = loaded(item.loader)
            if ok:
                entry["value"] = C._jsonable(item.value(result))
            elif ok is None:
                entry["value"] = None
                entry["inactive"] = "not read while the retrieval add-on is off"
            else:
                entry["value"] = None
                entry["error"] = "the current environment does not parse (%s)" % result
        parts[item.part].append(entry)
    out = []
    for p in C.PARTS:
        part = {"id": p, "settings": parts[p]}
        if not parts[p] and p in C.EMPTY_PART_NOTES:
            part["note"] = C.EMPTY_PART_NOTES[p]
        if p == C.RETRIEVAL:
            part["status"] = C._retrieval_status(loaded)
        out.append(part)
    return {"read_only": True, "parts": out}


def _child() -> int:
    """In the child: the process environment IS the configuration. Prints both describes."""
    import settings_catalog as C
    new = C.describe()
    old = frozen_describe(C)
    print(json.dumps({"new": new, "old": old}, sort_keys=False))
    return 0


if len(sys.argv) > 1 and sys.argv[1] == "--_child":
    sys.exit(_child())


import settings as S  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="atlas-settings-blame-"))


def _pepper() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def _file(name: str, text: str) -> str:
    p = TMP / name
    p.write_text(text, encoding="utf-8")
    return str(p)


BASE = {
    "ATLAS_PASSWORD_PEPPER": _pepper(),
    "ATLAS_TOKEN_PEPPER": _pepper(),
    "ATLAS_LOGIN_ORIGINS": "https://atlas.example.com",
    # Unreachable on purpose: nothing here connects.
    "ATLAS_DB_URL": "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none",
}
OIDC = {
    "ATLAS_PUBLIC_URL": "https://atlas.example.com",
    "ATLAS_AUTH_PROVIDERS": "local,oidc",
    "ATLAS_OIDC_ISSUER": "https://idp.example.com",
    "ATLAS_OIDC_CLIENT_ID": "atlas-test",
    "ATLAS_OIDC_CLIENT_SECRET_FILE": _file("client-secret", "throwaway-client-secret\n"),
    "ATLAS_RBAC": "projects",
}
GOOD_MAP = _file("groups.yml", "groups:\n  idp-engineers: engineers\n")


def run(extra: dict) -> dict:
    env = {**BASE, **extra}
    for keep in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "LANG", "APPDATA", "LOCALAPPDATA",
                 "USERPROFILE", "VIRTUAL_ENV", "LD_LIBRARY_PATH"):
        if os.environ.get(keep):
            env.setdefault(keep, os.environ[keep])
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p and os.path.isdir(p))
    r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_child"], env=env,
                       capture_output=True, text=True, timeout=120, cwd=str(HERE))
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise SystemExit("[FAIL] the child crashed: %s" % r.stderr.strip()[-1500:])


def entries(body: dict) -> dict:
    return {s["name"]: s for p in body["parts"] for s in p["settings"]}


def errors(body: dict) -> list:
    return sorted(n for n, s in entries(body).items() if "error" in s)


try:
    # ------------------------------------------------------------------ A
    try:
        S.load_identity({STEP_UP: "3600"})
        exc = None
    except S.SettingsError as e:
        exc = e
    check("A: an out-of-range step-up max age raises SettingsError naming that variable",
          exc is not None and exc.variable == STEP_UP, repr(getattr(exc, "variable", None)))
    lo, hi = S.OIDC_STEP_UP_MAX_AGE_BOUNDS
    check("A: ... and its message is unchanged",
          exc is not None and str(exc) == "%s=%r is outside %d..%d." % (STEP_UP, "3600", lo, hi),
          str(exc))
    try:
        S.load_identity({STEP_UP: "soon"})
        exc = None
    except S.SettingsError as e:
        exc = e
    check("A: a non-numeric step-up max age names that variable",
          exc is not None and exc.variable == STEP_UP)

    # identity imports passwords, which needs a pepper: the throwaway ones, as plain env vars.
    os.environ.update(BASE)
    from identity import groups  # noqa: E402
    for label, path in (("a missing file", str(TMP / "absent.yml")),
                        ("a file that is not YAML", _file("bad.yml", "groups: [unclosed\n")),
                        ("a file of the wrong shape", _file("shape.yml", "teams:\n  a: b\n"))):
        try:
            groups.load_group_map(path)
            exc = None
        except S.SettingsError as e:
            exc = e
        check("A: %s as the group map raises SettingsError naming %s" % (label, GROUP_MAP),
              exc is not None and exc.variable == GROUP_MAP, repr(getattr(exc, "variable", None)))
        check("A: ... and its message starts as before (%s=<path>)" % GROUP_MAP,
              exc is not None and str(exc).startswith("%s=%r" % (GROUP_MAP, path)), str(exc))
    check("A: a valid group map still loads", groups.load_group_map(GOOD_MAP) == {"idp-engineers": "engineers"})

    try:
        S.read_secret("ATLAS_VERIFY_BLAME_DUMMY")
        exc = None
    except S.SecretError as e:
        exc = e
    check("A: a SecretError names the secret", exc is not None and exc.variable == "ATLAS_VERIFY_BLAME_DUMMY")
    check("A: an error raised with no variable has variable None", S.SettingsError("x").variable is None)

    # The startup path: auth wraps the identity error at import; the wrap keeps the variable.
    os.environ[STEP_UP] = "3600"
    try:
        import auth  # noqa: F401
        exc = None
    except Exception as e:  # noqa: BLE001
        exc = e
    finally:
        del os.environ[STEP_UP]
        for mod in [m for m in sys.modules if m == "auth"]:
            del sys.modules[mod]
    check("A: import auth with the out-of-range value: MissingSecretError naming %s" % STEP_UP,
          exc is not None and type(exc).__name__ == "MissingSecretError"
          and getattr(exc, "variable", None) == STEP_UP, repr(exc))

    # ------------------------------------------------------------------ B
    bad = run({**OIDC, STEP_UP: "3600"})
    new = entries(bad["new"])
    check("B: out-of-range step-up: only %s carries an error" % STEP_UP,
          errors(bad["new"]) == [STEP_UP], str(errors(bad["new"])))
    check("B: ... its error is the catalogue's usual text, with the exception's type",
          new[STEP_UP]["error"] == "the current environment does not parse (SettingsError)"
          and new[STEP_UP]["value"] is None, str(new[STEP_UP]))
    check("B: ... ATLAS_AUTH_PROVIDERS is not blamed: no error, blocked_by %s" % STEP_UP,
          "error" not in new["ATLAS_AUTH_PROVIDERS"]
          and new["ATLAS_AUTH_PROVIDERS"].get("blocked_by") == STEP_UP
          and new["ATLAS_AUTH_PROVIDERS"]["value"] is None, str(new["ATLAS_AUTH_PROVIDERS"]))
    identity_names = [s.name for s in __import__("settings_catalog").CATALOG
                      if s.loader == "identity" and s.name != STEP_UP]
    check("B: ... every other identity setting is blocked_by it",
          all(new[n].get("blocked_by") == STEP_UP for n in identity_names))
    check("B: ... the frozen describe blamed ATLAS_AUTH_PROVIDERS (what TASK-1439 found)",
          "ATLAS_AUTH_PROVIDERS" in errors(bad["old"]))
    check("B: ... the group map's own reader (it needs the identity settings) is blocked_by it too",
          new[GROUP_MAP].get("blocked_by") == STEP_UP, str(new[GROUP_MAP]))

    for label, path in (("missing", str(TMP / "absent.yml")),
                        ("not YAML", str(TMP / "bad.yml")),
                        ("wrong shape", str(TMP / "shape.yml"))):
        out = run({**OIDC, GROUP_MAP: path})
        new = entries(out["new"])
        check("B: group map %s: only %s carries an error" % (label, GROUP_MAP),
              errors(out["new"]) == [GROUP_MAP], str(errors(out["new"])))
        check("B: ... its error is the catalogue's usual text, with the exception's type",
              new[GROUP_MAP]["error"] == "the current environment does not parse (SettingsError)"
              and new[GROUP_MAP]["value"] is None, str(new[GROUP_MAP]))
        check("B: ... ATLAS_ALLOW_SESSION_MINT is not blamed, and still shows its value",
              "error" not in new["ATLAS_ALLOW_SESSION_MINT"]
              and "blocked_by" not in new["ATLAS_ALLOW_SESSION_MINT"]
              and new["ATLAS_ALLOW_SESSION_MINT"]["value"] is False,
              str(new["ATLAS_ALLOW_SESSION_MINT"]))
    # The same broken file with oidc NOT a provider is not read at boot, so not here either.
    out = run({**BASE, GROUP_MAP: str(TMP / "absent.yml")})
    check("B: a group map set while oidc is not a provider is not read, as at boot: no error",
          not errors(out["new"]), str(errors(out["new"])))

    # ------------------------------------------------------------------ C
    valid = {
        "the default (peppers, origins, database URL only)": {},
        "OIDC + RBAC with a valid group map": {**OIDC, GROUP_MAP: GOOD_MAP, STEP_UP: "300"},
        "the retrieval add-on on": {"ATLAS_RETRIEVAL": "on", "ATLAS_EMBED_URL": "http://embed.invalid/v1",
                                    "ATLAS_EMBED_MODEL": "m", "ATLAS_EMBED_DIM": "8"},
    }
    for label, extra in valid.items():
        out = run(extra)
        same = json.dumps(out["new"]) == json.dumps(out["old"])
        check("C: %s describes byte for byte as before" % label, same)
        check("C: ... and reports no error", not errors(out["new"]), str(errors(out["new"])))
    check("C: perturbation: the frozen copy and the new code differ on the out-of-range case",
          json.dumps(bad["new"]) != json.dumps(bad["old"]))

    # ------------------------------------------------------------------ D
    import settings_catalog as C  # noqa: E402
    real = S.load_rbac

    def unnamed(environ=None):
        raise S.SettingsError("an error that names no variable")
    S.load_rbac = unnamed
    try:
        d = entries(C.describe({"ATLAS_LOGIN_ORIGINS": "https://atlas.example.com"}))
    finally:
        S.load_rbac = real
    rbac = [s.name for s in C.CATALOG if s.loader == "rbac"]
    check("D: an error naming no variable is still reported against every setting of its loader",
          all("error" in d[n] and "blocked_by" not in d[n] for n in rbac), str([d[n] for n in rbac]))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
if FAILURES:
    print("%d FAILED" % len(FAILURES))
    sys.exit(1)
print("all passed")
