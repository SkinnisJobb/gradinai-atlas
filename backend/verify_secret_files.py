"""
Verification for STORY-oss-03-secret-files — secrets may be read from a file.

`settings.read_secret(NAME)` is the one reader. The claims checked here:

  A. The reader itself. DEFAULT (no new config): a plain env var works exactly
     as before; `NAME_FILE` is honoured and wins over `NAME`, with a warning
     naming the variables and never the value; a trailing newline (LF or CRLF)
     is stripped; an empty or whitespace-only file, a missing file and an unset
     secret all fail; a set-but-missing file never falls back to the env var;
     a plain env var set but blank fails naming it (TASK-1348);
     `required=False` returns None instead; `default` only covers "neither
     set". STRICT (`ATLAS_REQUIRE_SECRET_FILES=1`, opt-in): a plain env var is
     refused, `NAME_FILE` is required, `default` does not apply, and the error
     names the missing variable. No message ever carries a value.
  B. Per secret, through the real call sites: ATLAS_PASSWORD_PEPPER
     (passwords._load_pepper), ATLAS_TOKEN_PEPPER (auth._load_token_pepper),
     the three GitHub secrets (github_link._read_file — file-only, as before)
     and ATLAS_DB_URL (admin._db_url) — env only works by default, file only
     works, both set uses the file, strict refuses env, empty fails; for
     ATLAS_DB_URL a blank env value fails while unset keeps the local default.
  C. The edited modules read no secret through `os.environ` directly.
  D. TASK-1349, the reads that still bypassed the reader: the API's DSN
     (`db._load_db_url`, and a real `import db` in a child process) and the
     `--db`/`--db-url` fallback of `bind_unbound.py` and the five
     `migrate_*.py` scripts — env only works as before, file only works, both
     uses the file, blank fails "set but empty", strict refuses env, and the
     API still has NO default (unset fails boot with its old message).
     atlas-mcp's ATLAS_TOKEN is proven by `mcp/verify_secret_token.py`.
  E. The grep, done properly: an AST scan of every product module in
     backend/ and mcp/app (settings.py and the test harness excepted, and
     printed) for a secret-like name read from the environment directly or
     through a local env-reading helper. It is shown to catch each shape on
     synthetic source first, so it cannot pass by matching nothing.

It needs NO database: it calls the loaders, not the app. All values are
throwaway dummies written to a temp directory that is removed afterwards.

Run:
    python verify_secret_files.py
"""
from __future__ import annotations

import base64
import logging
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FAILURES: list[str] = []
TMP = Path(tempfile.mkdtemp(prefix="atlas-secret-files-"))
STRICT = "ATLAS_REQUIRE_SECRET_FILES"

# Every name this script touches, cleared before each case so the caller's own
# environment cannot leak into a claim.
SECRET_NAMES = (
    "ATLAS_PASSWORD_PEPPER", "ATLAS_TOKEN_PEPPER", "ATLAS_DB_URL",
    "ATLAS_GITHUB_OAUTH_CLIENT_ID", "ATLAS_GITHUB_OAUTH_CLIENT_SECRET", "ATLAS_GITHUB_INVITE_TOKEN",
    "ATLAS_VERIFY_DUMMY",
)


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


CAPTURE = _Capture()
logging.getLogger("atlas.settings").addHandler(CAPTURE)


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def clear() -> None:
    os.environ.pop(STRICT, None)
    for name in SECRET_NAMES:
        os.environ.pop(name, None)
        os.environ.pop(name + "_FILE", None)
    CAPTURE.messages.clear()


_n = 0


def secret_file(content: str) -> str:
    global _n
    _n += 1
    p = TMP / f"secret{_n}"
    p.write_bytes(content.encode("utf-8"))
    return str(p)


def raises(fn, exc_types) -> tuple[bool, str]:
    """(raised one of exc_types, its message)."""
    try:
        fn()
    except exc_types as e:  # noqa: B014
        return True, str(e)
    return False, ""


def pepper() -> str:
    return base64.b64encode(os.urandom(32)).decode()


# Import-time setup: passwords and auth load their peppers at IMPORT. Give them
# plain env peppers — the shape every existing deployment uses — so the import
# itself proves that no new configuration is needed.
clear()
os.environ["ATLAS_PASSWORD_PEPPER"] = pepper()
os.environ["ATLAS_TOKEN_PEPPER"] = pepper()
os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")

import settings  # noqa: E402
import passwords  # noqa: E402
import auth  # noqa: E402
import github_link  # noqa: E402
import admin  # noqa: E402


def section_a() -> None:
    print("\n== 0. import with env-only peppers and no new config ==")
    check("passwords and auth imported with plain env peppers", True)

    print("\n== A. settings.read_secret — default (no new config) ==")
    name = "ATLAS_VERIFY_DUMMY"
    E = settings.SecretError

    clear()
    os.environ[name] = "from-env-value"
    check("env only: accepted, unchanged", settings.read_secret(name) == "from-env-value")
    check("env only: no warning", not CAPTURE.messages)
    os.environ[STRICT] = "0"
    check("strict=0 is not a yes", settings.read_secret(name) == "from-env-value")

    clear()
    os.environ[name + "_FILE"] = secret_file("from-file\n")
    check("file only: read", settings.read_secret(name) == "from-file")
    check("file only: no warning", not CAPTURE.messages)

    clear()
    os.environ[name + "_FILE"] = secret_file("crlf-value\r\n")
    check("trailing CRLF stripped", settings.read_secret(name) == "crlf-value")

    clear()
    os.environ[name + "_FILE"] = secret_file("from-file\n")
    os.environ[name] = "from-env-value"
    check("both set: file wins", settings.read_secret(name) == "from-file")
    warned = " ".join(CAPTURE.messages)
    check("both set: a warning names the variable", name in warned and name + "_FILE" in warned, warned[:80])
    check("both set: the warning carries no value",
          "from-env-value" not in warned and "from-file" not in warned)

    clear()
    os.environ[name + "_FILE"] = secret_file("")
    ok, msg = raises(lambda: settings.read_secret(name), E)
    check("empty file fails", ok, msg[:80])
    check("empty file, optional: None", settings.read_secret(name, required=False) is None)
    os.environ[name + "_FILE"] = secret_file(" \n\n")
    check("whitespace-only file fails", raises(lambda: settings.read_secret(name), E)[0])

    clear()
    missing = str(TMP / "does-not-exist")
    os.environ[name + "_FILE"] = missing
    os.environ[name] = "env-fallback"
    ok, msg = raises(lambda: settings.read_secret(name), E)
    check("missing file fails (no fall-through to env)", ok, msg[:80])
    check("missing-file error names the path", repr(missing) in msg)
    check("missing-file error carries no value", "env-fallback" not in msg)
    check("missing file, optional: None", settings.read_secret(name, required=False) is None)
    check("missing file ignores default", raises(lambda: settings.read_secret(name, default="d"), E)[0])

    clear()
    ok, msg = raises(lambda: settings.read_secret(name), E)
    check("neither set fails", ok, msg[:80])
    check("neither set: error names the variable", name in msg)
    check("neither set, optional: None", settings.read_secret(name, required=False) is None)
    check("neither set, default: default", settings.read_secret(name, default="dflt") == "dflt")
    for blank in ("", "   "):
        os.environ[name] = blank
        ok, msg = raises(lambda: settings.read_secret(name, default="dflt"), E)
        check(f"blank env {blank!r} fails, never the default (TASK-1348)", ok, msg[:80])
        check(f"blank env {blank!r}: error names the variable as set but empty",
              name in msg and "set but empty" in msg)
        check(f"blank env {blank!r}, no default: fails", raises(lambda: settings.read_secret(name), E)[0])
        check(f"blank env {blank!r}, optional: None", settings.read_secret(name, required=False) is None)

    clear()
    os.environ[name] = "env-value"
    check("allow_env=False ignores the plain var",
          settings.read_secret(name, required=False, allow_env=False) is None)

    print("\n== A. settings.read_secret — strict (ATLAS_REQUIRE_SECRET_FILES=1) ==")
    clear()
    os.environ[STRICT] = "1"
    os.environ[name] = "from-env-value"
    ok, msg = raises(lambda: settings.read_secret(name), E)
    check("strict, env only: refused", ok, msg[:80])
    check("strict refusal names the variable, its _FILE and the flag",
          name in msg and name + "_FILE" in msg and STRICT in msg)
    check("strict refusal does not carry the value", "from-env-value" not in msg)
    check("strict, env only, optional: None", settings.read_secret(name, required=False) is None)
    check("strict: default does not rescue a refused env value",
          raises(lambda: settings.read_secret(name, default="dflt"), E)[0])

    clear()
    os.environ[STRICT] = "true"
    ok, msg = raises(lambda: settings.read_secret(name, default="dflt"), E)
    check("strict, neither set: fails even with a default", ok, msg[:80])
    check("strict, neither set: error names the missing _FILE variable", name + "_FILE" in msg)

    clear()
    os.environ[STRICT] = "1"
    os.environ[name + "_FILE"] = secret_file("from-file\n")
    check("strict, file only: read", settings.read_secret(name) == "from-file")


def _pepper_cases(label: str, name: str, loader, exc_type) -> None:
    print(f"\n== B. {label} ({name}) ==")
    value, other = pepper(), pepper()

    def works(case: str) -> None:
        try:
            check(f"{name}: {case}", loader() == base64.b64decode(value))
        except Exception as e:  # noqa: BLE001
            check(f"{name}: {case}", False, type(e).__name__)

    clear()
    os.environ[name] = value
    works("env only works (default, unchanged)")

    clear()
    os.environ[name + "_FILE"] = secret_file(value + "\n")
    works("file only works")

    clear()
    os.environ[name + "_FILE"] = secret_file(value + "\n")
    os.environ[name] = other
    works("both set: the file wins")
    check(f"{name}: both set logs a warning", any(name in m for m in CAPTURE.messages))

    clear()
    os.environ[STRICT] = "1"
    os.environ[name] = value
    ok, msg = raises(loader, exc_type)
    check(f"{name}: strict refuses env only", ok)
    check(f"{name}: strict refusal names {name}_FILE", name + "_FILE" in msg)
    check(f"{name}: the failure does not carry the value", value not in msg)
    os.environ[name + "_FILE"] = secret_file(value + "\n")
    works("strict with a file works")

    clear()
    os.environ[STRICT] = "1"
    ok, msg = raises(loader, exc_type)
    check(f"{name}: strict, nothing set fails naming {name}_FILE", ok and name + "_FILE" in msg)

    clear()
    os.environ[name + "_FILE"] = secret_file("\n")
    check(f"{name}: empty file fails", raises(loader, exc_type)[0])

    clear()
    os.environ[name + "_FILE"] = str(TMP / "absent")
    check(f"{name}: missing file fails", raises(loader, exc_type)[0])

    clear()
    bad = "not base64 !!" + value
    os.environ[name + "_FILE"] = secret_file(bad)
    ok, msg = raises(loader, exc_type)
    check(f"{name}: malformed fails without echoing it", ok and bad not in msg)


def section_b() -> None:
    _pepper_cases("passwords", "ATLAS_PASSWORD_PEPPER", passwords._load_pepper, passwords.MissingPepperError)
    _pepper_cases("auth", "ATLAS_TOKEN_PEPPER", auth._load_token_pepper, auth.MissingSecretError)

    print("\n== B. github_link (optional, file-only secrets — unchanged) ==")
    for file_var in (github_link.ENV_CLIENT_ID_FILE, github_link.ENV_CLIENT_SECRET_FILE,
                     github_link.ENV_INVITE_TOKEN_FILE):
        name = file_var[: -len("_FILE")]
        clear()
        os.environ[file_var] = secret_file("gh-value\n")
        check(f"{name}: file only works", github_link._read_file(file_var) == "gh-value")
        clear()
        os.environ[name] = "gh-env"
        check(f"{name}: plain env is still not read (as before)", github_link._read_file(file_var) is None)
        clear()
        os.environ[STRICT] = "1"
        os.environ[file_var] = secret_file("gh-value\n")
        check(f"{name}: strict, file works", github_link._read_file(file_var) == "gh-value")
        clear()
        os.environ[file_var] = secret_file("")
        check(f"{name}: empty file is off", github_link._read_file(file_var) is None)
        os.environ[file_var] = str(TMP / "absent")
        check(f"{name}: missing file is off", github_link._read_file(file_var) is None)

    print("\n== B. admin (ATLAS_DB_URL) ==")
    url = "postgresql+psycopg2://u:pw-dummy@h:5432/db"
    clear()
    os.environ["ATLAS_DB_URL"] = url
    check("ATLAS_DB_URL: env only works (default, unchanged)", admin._db_url() == url)
    clear()
    os.environ["ATLAS_DB_URL_FILE"] = secret_file(url + "\n")
    check("ATLAS_DB_URL: file only works", admin._db_url() == url)
    os.environ["ATLAS_DB_URL"] = "postgresql+psycopg2://u:other@h:5432/db"
    check("ATLAS_DB_URL: both set uses the file", admin._db_url() == url)
    clear()
    os.environ[STRICT] = "1"
    os.environ["ATLAS_DB_URL"] = url
    ok, msg = raises(admin._db_url, SystemExit)
    check("ATLAS_DB_URL: strict refuses env only", ok)
    check("ATLAS_DB_URL: the failure does not carry the value", "pw-dummy" not in msg)
    clear()
    os.environ[STRICT] = "1"
    check("ATLAS_DB_URL: strict, nothing set fails (no default)", raises(admin._db_url, SystemExit)[0])
    clear()
    os.environ["ATLAS_DB_URL_FILE"] = secret_file("")
    check("ATLAS_DB_URL: empty file fails", raises(admin._db_url, SystemExit)[0])
    for blank in ("", " \t "):
        clear()
        os.environ["ATLAS_DB_URL"] = blank
        ok, msg = raises(admin._db_url, SystemExit)
        check(f"ATLAS_DB_URL: set to {blank!r} fails (no default, TASK-1348)", ok, msg[:80])
        check(f"ATLAS_DB_URL: {blank!r} error names ATLAS_DB_URL as set but empty",
              "ATLAS_DB_URL" in msg and "set but empty" in msg)
    clear()
    check("ATLAS_DB_URL: neither set keeps the local default", "atlas-postgres" in admin._db_url())


def section_c() -> None:
    print("\n== C. no secret read through os.environ in the edited modules ==")
    secret = re.compile(r"PEPPER|SECRET|TOKEN|PASSWORD|DB_URL|CLIENT_ID", re.I)
    for mod in ("auth.py", "passwords.py", "github_link.py", "admin.py"):
        bad = []
        for i, line in enumerate((HERE / mod).read_text(encoding="utf-8").splitlines(), 1):
            if "os.environ" in line or "os.getenv" in line:
                if secret.search(line):
                    bad.append(f"{mod}:{i}")
        check(f"{mod}: every os.environ read is non-secret config", not bad, ", ".join(bad))


# --------------------------------------------------------------------------- #
# D. TASK-1349: the API's DSN and the scripts' --db-url fallback
# --------------------------------------------------------------------------- #
DSN = "postgresql+psycopg2://u:pw-dummy@h:5432/db"
DSN_OTHER = "postgresql+psycopg2://u:pw-other@h:5432/db"

#: (module, the flag that overrides the env var).
SCRIPTS = (
    ("bind_unbound", "--db"),
    ("migrate_add_write_scope", "--db-url"),
    ("migrate_drop_status_override", "--db-url"),
    ("migrate_mark_docs_external", "--db-url"),
    ("migrate_result_split", "--db-url"),
    ("migrate_strip_frontmatter_raw", "--db-url"),
)


class _Engine(Exception):
    """Raised by the stand-in `create_engine`: the script got this far, with this URL."""

    def __init__(self, url: str) -> None:
        super().__init__("engine")
        self.url = url


def _fake_engine(url, *a, **k):
    raise _Engine(str(url))


def _run_script(mod, argv: list[str]) -> tuple[str, str]:
    """Run `mod.main()` up to the engine. ("engine:<url>" | "exit:<code>", stderr)."""
    import contextlib
    import io

    err, out = io.StringIO(), io.StringIO()
    saved = sys.argv
    sys.argv = [mod.__name__ + ".py"] + argv
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            try:
                rc = mod.main()
                result = f"exit:{rc}"
            except _Engine as e:
                result = "engine:" + e.url
            except SystemExit as e:
                result = f"exit:{e.code}"
    finally:
        sys.argv = saved
    return result, err.getvalue() + out.getvalue()


def _child_import_db(env_extra: dict) -> "subprocess.CompletedProcess":
    import subprocess

    env = {k: v for k, v in os.environ.items()
           if k not in (STRICT, "ATLAS_DB_URL", "ATLAS_DB_URL_FILE")}
    env.update(env_extra)
    # The expected DSN goes in argv, not the code: a traceback quotes the `-c` source.
    code = "import sys, db; print('URL-OK', db.DB_URL == sys.argv[1])"
    return subprocess.run([sys.executable, "-c", code, DSN], cwd=str(HERE), env=env,
                          capture_output=True, text=True, timeout=120)


def section_d() -> None:
    import importlib

    print("\n== D. db._load_db_url (the API's DSN, no default) ==")
    clear()
    os.environ["ATLAS_DB_URL"] = DSN       # the import itself: plain env, as compose gives it
    import db
    check("db imports with a plain env ATLAS_DB_URL (homelab shape)", db.DB_URL == DSN)

    clear()
    os.environ["ATLAS_DB_URL"] = DSN
    check("ATLAS_DB_URL: env only works (default, unchanged)", db._load_db_url() == DSN)
    clear()
    os.environ["ATLAS_DB_URL_FILE"] = secret_file(DSN + "\n")
    check("ATLAS_DB_URL: file only works", db._load_db_url() == DSN)
    os.environ["ATLAS_DB_URL"] = DSN_OTHER
    check("ATLAS_DB_URL: both set uses the file", db._load_db_url() == DSN)
    clear()
    ok, msg = raises(db._load_db_url, RuntimeError)
    check("ATLAS_DB_URL unset: fails (still no default)", ok)
    check("ATLAS_DB_URL unset: the old boot message, unchanged in substance",
          msg.startswith("ATLAS_DB_URL is not set. It must be an explicit DSN — there is no default."),
          msg[:80])
    for blank in ("", "  "):
        clear()
        os.environ["ATLAS_DB_URL"] = blank
        ok, msg = raises(db._load_db_url, RuntimeError)
        check(f"ATLAS_DB_URL={blank!r}: fails as set but empty (TASK-1348)",
              ok and "ATLAS_DB_URL" in msg and "set but empty" in msg, msg[:80])
    clear()
    os.environ[STRICT] = "1"
    os.environ["ATLAS_DB_URL"] = DSN
    ok, msg = raises(db._load_db_url, RuntimeError)
    check("ATLAS_DB_URL: strict refuses env only", ok and "ATLAS_DB_URL_FILE" in msg and STRICT in msg,
          msg[:80])
    check("ATLAS_DB_URL: the refusal does not carry the value", "pw-dummy" not in msg)
    os.environ["ATLAS_DB_URL_FILE"] = secret_file(DSN + "\n")
    check("ATLAS_DB_URL: strict with a file works", db._load_db_url() == DSN)
    clear()
    os.environ[STRICT] = "1"
    ok, msg = raises(db._load_db_url, RuntimeError)
    check("ATLAS_DB_URL: strict, nothing set fails naming ATLAS_DB_URL_FILE",
          ok and "ATLAS_DB_URL_FILE" in msg)
    clear()
    os.environ["ATLAS_DB_URL_FILE"] = secret_file("")
    check("ATLAS_DB_URL: empty file fails", raises(db._load_db_url, RuntimeError)[0])
    os.environ["ATLAS_DB_URL_FILE"] = str(TMP / "absent")
    check("ATLAS_DB_URL: missing file fails", raises(db._load_db_url, RuntimeError)[0])

    print("\n== D. a real `import db`, one child process per configuration ==")
    r = _child_import_db({"ATLAS_DB_URL": DSN})
    check("import db: plain env (compose's shape) boots", r.returncode == 0 and "URL-OK True" in r.stdout,
          r.stderr.strip()[-200:])
    r = _child_import_db({"ATLAS_DB_URL_FILE": secret_file(DSN + "\n")})
    check("import db: ATLAS_DB_URL_FILE boots", r.returncode == 0 and "URL-OK True" in r.stdout,
          r.stderr.strip()[-200:])
    r = _child_import_db({})
    check("import db: unset still refuses to boot with 'ATLAS_DB_URL is not set'",
          r.returncode != 0 and "ATLAS_DB_URL is not set" in r.stderr, r.stderr.strip()[-200:])
    r = _child_import_db({STRICT: "1", "ATLAS_DB_URL": DSN})
    check("import db: strict refuses the plain env var",
          r.returncode != 0 and "refuses" in r.stderr and "pw-dummy" not in r.stderr,
          r.stderr.strip()[-200:])

    print("\n== D. the scripts' --db / --db-url fallback ==")
    for name, flag in SCRIPTS:
        clear()
        mod = importlib.import_module(name)
        mod.create_engine = _fake_engine
        if hasattr(mod, "load_map"):
            mod.load_map = lambda: []     # its map file is not in the tree; not the claim here

        clear()
        os.environ["ATLAS_DB_URL"] = DSN
        res, _ = _run_script(mod, [])
        check(f"{name}: env only works (default, unchanged)", res == "engine:" + DSN, res[:40])
        clear()
        os.environ["ATLAS_DB_URL_FILE"] = secret_file(DSN + "\n")
        os.environ["ATLAS_DB_URL"] = DSN_OTHER
        res, _ = _run_script(mod, [])
        check(f"{name}: ATLAS_DB_URL_FILE works and wins over the env var", res == "engine:" + DSN,
              res[:40])
        clear()
        os.environ[STRICT] = "1"
        os.environ["ATLAS_DB_URL"] = DSN
        res, err = _run_script(mod, [])
        check(f"{name}: strict refuses the plain env var (exit 2)",
              res == "exit:2" and "ATLAS_DB_URL_FILE" in err and STRICT in err, err.strip()[-120:])
        check(f"{name}: the refusal does not carry the value", "pw-dummy" not in err)
        res, _ = _run_script(mod, [flag, DSN_OTHER])
        check(f"{name}: an explicit {flag} is used as before", res == "engine:" + DSN_OTHER, res[:40])
        clear()
        os.environ["ATLAS_DB_URL"] = " "
        res, err = _run_script(mod, [])
        check(f"{name}: blank ATLAS_DB_URL fails as set but empty",
              res == "exit:2" and "set but empty" in err, err.strip()[-120:])
        clear()
        res, err = _run_script(mod, [])
        check(f"{name}: nothing set refuses to run (no default DSN)",
              res == "exit:2" and "ATLAS_DB_URL is not set" in err, err.strip()[-120:])


# --------------------------------------------------------------------------- #
# E. No secret-like env read outside the readers (AST, not a line grep)
# --------------------------------------------------------------------------- #
REPO = HERE.parent
#: A secret-like variable NAME. `_FILE` names hold a path, not the secret.
SECRET_NAME = re.compile(
    r"^[A-Z][A-Z0-9_]*(PEPPER|SECRET|TOKEN|PASSWORD|DB_URL|CLIENT_ID|API_KEY|PRIVATE_KEY)[A-Z0-9_]*$")
#: Names that match the pattern but are flags, not secrets. Each one argued here.
NOT_SECRET = {
    "ATLAS_MCP_ALLOW_SHARED_TOKEN",  # an on/off flag (mcp/app/client.py), not the token
    STRICT,                          # ATLAS_REQUIRE_SECRET_FILES: the strict-mode flag itself
}
#: Where a secret IS read from the environment, on purpose: (path, function or "*").
READERS = {
    ("backend/settings.py", "*"),
    ("mcp/app/client.py", "_require_secret"),
    ("mcp/app/client.py", "_secret_is_set"),
}
_ENV_CALLS = {"os.environ.get", "os.getenv", "os.environ.pop", "os.environ.setdefault",
              "environ.get", "getenv"}


def _dotted(node) -> str:
    import ast
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def scan_source(src: str, rel: str) -> list[str]:
    """Every `rel:line NAME` where a secret-like NAME is read from the environment."""
    import ast

    tree = ast.parse(src)
    consts = {}
    for st in tree.body:
        if isinstance(st, ast.Assign) and isinstance(st.value, ast.Constant) \
                and isinstance(st.value.value, str):
            for t in st.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = st.value.value
    exempt_all = (rel, "*") in READERS
    if exempt_all:
        return []
    exempt_fns = {fn for (p, fn) in READERS if p == rel}

    def key(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return consts.get(node.id)
        return None

    def env_key_nodes(root):
        """(key expression, lineno) for every direct environment read under `root`."""
        for n in ast.walk(root):
            if isinstance(n, ast.Call) and _dotted(n.func) in _ENV_CALLS and n.args:
                yield n.args[0], n.lineno
            elif isinstance(n, ast.Subscript) and _dotted(n.value) in ("os.environ", "environ") \
                    and isinstance(n.ctx, ast.Load):
                yield n.slice, n.lineno
            elif isinstance(n, ast.Compare) and any(isinstance(o, (ast.In, ast.NotIn)) for o in n.ops) \
                    and any(_dotted(c) in ("os.environ", "environ") for c in n.comparators):
                yield n.left, n.lineno

    # Local helpers that read the environment with one of their own parameters
    # (`_require_env(name)`, `_truthy(name)`): a call to one is an env read too.
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    helpers = set()
    for fn in funcs:
        if fn.name in exempt_fns:
            continue
        params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
        for k, _ in env_key_nodes(fn):
            if any(isinstance(x, ast.Name) and x.id in params for x in ast.walk(k)):
                helpers.add(fn.name)
                break

    skip = set()
    for fn in funcs:
        if fn.name in exempt_fns:
            skip.update(id(x) for x in ast.walk(fn))

    hits = []

    def flag(k, line):
        name = key(k)
        if name and SECRET_NAME.match(name) and not name.endswith("_FILE") and name not in NOT_SECRET:
            hits.append(f"{rel}:{line} {name}")

    for k, line in env_key_nodes(tree):
        if id(k) not in skip:
            flag(k, line)
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and id(n) not in skip and n.args \
                and _dotted(n.func).split(".")[-1] in helpers:
            flag(n.args[0], n.lineno)
    return sorted(set(hits))


def section_e() -> None:
    print("\n== E. no secret-like name read from os.environ outside the readers (AST) ==")
    # A check that cannot fail proves nothing: every shape is caught on synthetic source.
    shapes = {
        "direct get": 'import os\nX = os.environ.get("ATLAS_DB_URL")\n',
        "getenv via a module constant": 'import os\nV = "ATLAS_TOKEN"\ndef f():\n    return os.getenv(V)\n',
        "subscript": 'import os\nP = os.environ["ATLAS_PASSWORD_PEPPER"]\n',
        "membership": 'import os\nB = "ATLAS_TOKEN" in os.environ\n',
        "argparse default": 'import os\nap.add_argument("--db", default=os.environ.get("ATLAS_DB_URL"))\n',
        "through a local helper": ('import os\ndef _req(name):\n    return os.environ.get(name, "")\n'
                                   'T = _req("ATLAS_TOKEN")\n'),
    }
    for label, src in shapes.items():
        check(f"the scan catches: {label}", bool(scan_source(src, "synthetic.py")))
    clean = ('import os\ndef _truthy(name):\n    return os.environ.get(name, "")\n'
             'A = os.environ.get("ATLAS_BASE_URL")\nF = _truthy("ATLAS_MCP_ALLOW_SHARED_TOKEN")\n'
             'G = os.environ.get("ATLAS_TOKEN_FILE")\n')
    check("the scan passes non-secret config, a flag and a _FILE path", not scan_source(clean, "s.py"),
          ", ".join(scan_source(clean, "s.py")))
    check("the reader exemption is per function, not per file",
          bool(scan_source('import os\nX = os.environ.get("ATLAS_TOKEN")\n', "mcp/app/client.py")))

    files = sorted((REPO / "backend").rglob("*.py")) + sorted((REPO / "mcp" / "app").rglob("*.py"))
    harness = [f for f in files if f.parent == REPO / "backend"
               and (f.name.startswith("verify_") or f.name.startswith("test"))]
    product = [f for f in files if f not in harness]
    print(f"   scanning {len(product)} product modules; excepted: backend/settings.py (the reader) "
          f"and {len(harness)} test-harness files (backend/verify_*.py, backend/test*.py), which set "
          f"the environment for the code under test")
    rels = {f.relative_to(REPO).as_posix() for f in product}
    must = {"backend/db.py", "mcp/app/client.py", "backend/auth.py", "backend/admin.py"} | {
        f"backend/{m}.py" for m, _ in SCRIPTS}
    check("the scan covers every module TASK-1349 moved (and the earlier ones)", must <= rels,
          ", ".join(sorted(must - rels)))
    hits = []
    for f in product:
        hits += scan_source(f.read_text(encoding="utf-8"), f.relative_to(REPO).as_posix())
    check("no secret-like name is read from the environment outside the readers", not hits,
          "; ".join(hits))


def main() -> int:
    try:
        section_a()
        section_b()
        section_c()
        section_d()
        section_e()
    finally:
        clear()
        shutil.rmtree(TMP, ignore_errors=True)
    print()
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} check(s): " + "; ".join(FAILURES))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
