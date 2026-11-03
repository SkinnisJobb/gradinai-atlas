"""Prove atlas-mcp reads its one secret, ATLAS_TOKEN, like the API reads its own (TASK-1349).

`app/client.py` `_require_secret` is the MCP's reader: the same semantics as
`backend/settings.read_secret` for a required secret with no default. ATLAS_TOKEN is
read only when the opt-in migration fallback ATLAS_MCP_ALLOW_SHARED_TOKEN=1 is set, so
every case below sets it. One child process per configuration, because the module
reads its environment at import:

  A. DEFAULT, unchanged: a plain ATLAS_TOKEN (mcp/.env through compose's env_file:)
     still starts the fallback; nothing set still refuses, naming ATLAS_TOKEN.
  B. ATLAS_TOKEN_FILE works, wins over a plain ATLAS_TOKEN (saying so, never the
     value), and an empty, missing or blank-env value refuses to start.
  C. STRICT (ATLAS_REQUIRE_SECRET_FILES=1): the plain env var is refused; the file
     works; nothing set names ATLAS_TOKEN_FILE.
  D. Without the flag, a set ATLAS_TOKEN_FILE is IGNORED and said to be (the file is
     never opened: a path to nothing still starts).

No token value is ever printed: every case checks stdout and stderr for it.

    pip install -r mcp/requirements.txt && python mcp/verify_secret_token.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="atlas-mcp-secret-"))
CLEARED = ("ATLAS_TOKEN", "ATLAS_TOKEN_FILE", "ATLAS_MCP_ALLOW_SHARED_TOKEN",
           "ATLAS_REQUIRE_SECRET_FILES")

T_FILE = "atlas_svc_ffffff_" + "f" * 43
T_ENV = "atlas_svc_eeeeee_" + "e" * 43

failures: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def secret_file(name: str, content: str) -> str:
    p = TMP / name
    p.write_bytes(content.encode("utf-8"))
    return str(p)


def run(env_extra: dict, flag: bool = True) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in CLEARED}
    env.setdefault("ATLAS_BASE_URL", "https://atlas.invalid")
    if flag:
        env["ATLAS_MCP_ALLOW_SHARED_TOKEN"] = "1"
    env.update(env_extra)
    # The fakes go in argv, not the code: a traceback quotes the `-c` source.
    code = ("import sys, app.client as c; print('SHARED', {None: 'none', sys.argv[1]: 'file', "
            "sys.argv[2]: 'env'}.get(c.SHARED_TOKEN, 'other'))")
    return subprocess.run([sys.executable, "-c", code, T_FILE, T_ENV], cwd=str(HERE), env=env,
                          capture_output=True, text=True, timeout=60)


def quiet(r: subprocess.CompletedProcess) -> bool:
    out = r.stdout + r.stderr
    return T_FILE not in out and T_ENV not in out


def tail(r: subprocess.CompletedProcess) -> str:
    return r.stderr.strip()[-200:]


def main() -> int:
    good = secret_file("token", T_FILE + "\n")
    empty = secret_file("empty", "\n")
    absent = str(TMP / "absent")

    print("-- A. default: the plain env var, exactly as before --")
    r = run({"ATLAS_TOKEN": T_ENV})
    check("plain ATLAS_TOKEN starts the fallback (homelab shape, unchanged)",
          r.returncode == 0 and "SHARED env" in r.stdout, tail(r))
    check("...without printing it", quiet(r))
    r = run({})
    check("nothing set refuses to start, naming ATLAS_TOKEN",
          r.returncode != 0 and "ATLAS_TOKEN is not set" in r.stderr, tail(r))

    print("-- B. ATLAS_TOKEN_FILE --")
    r = run({"ATLAS_TOKEN_FILE": good})
    check("file only: starts with the file's token (trailing newline stripped)",
          r.returncode == 0 and "SHARED file" in r.stdout, tail(r))
    check("...without printing it", quiet(r))
    r = run({"ATLAS_TOKEN_FILE": good, "ATLAS_TOKEN": T_ENV})
    check("both set: the file wins", r.returncode == 0 and "SHARED file" in r.stdout, tail(r))
    check("both set: says so, naming both variables",
          "ATLAS_TOKEN_FILE and ATLAS_TOKEN are both set" in r.stderr, tail(r))
    check("...without printing either value", quiet(r))
    r = run({"ATLAS_TOKEN_FILE": empty})
    check("empty file refuses to start, naming the variable",
          r.returncode != 0 and "ATLAS_TOKEN_FILE" in r.stderr and "empty" in r.stderr, tail(r))
    r = run({"ATLAS_TOKEN_FILE": absent, "ATLAS_TOKEN": T_ENV})
    check("missing file refuses to start (no fall-through to the env var)",
          r.returncode != 0 and "cannot be read" in r.stderr and repr(absent) in r.stderr, tail(r))
    check("...without printing the env value", quiet(r))
    r = run({"ATLAS_TOKEN": "  "})
    check("blank ATLAS_TOKEN refuses as set but empty (TASK-1348)",
          r.returncode != 0 and "ATLAS_TOKEN is set but empty" in r.stderr, tail(r))
    bad = secret_file("bad", "atlas_svc_…placeholder\n")
    r = run({"ATLAS_TOKEN_FILE": bad})
    check("a placeholder in the file is still refused by the shape check, not echoed",
          r.returncode != 0 and "does not look like a service token" in r.stderr
          and "placeholder" not in r.stderr, tail(r))

    print("-- C. strict: ATLAS_REQUIRE_SECRET_FILES=1 --")
    r = run({"ATLAS_REQUIRE_SECRET_FILES": "1", "ATLAS_TOKEN": T_ENV})
    check("strict refuses the plain env var",
          r.returncode != 0 and "ATLAS_REQUIRE_SECRET_FILES=1 refuses" in r.stderr
          and "ATLAS_TOKEN_FILE" in r.stderr, tail(r))
    check("...without printing it", quiet(r))
    r = run({"ATLAS_REQUIRE_SECRET_FILES": "1", "ATLAS_TOKEN_FILE": good})
    check("strict with a file starts", r.returncode == 0 and "SHARED file" in r.stdout, tail(r))
    r = run({"ATLAS_REQUIRE_SECRET_FILES": "1"})
    check("strict, nothing set refuses naming ATLAS_TOKEN_FILE",
          r.returncode != 0 and "ATLAS_TOKEN_FILE is not set" in r.stderr, tail(r))
    r = run({"ATLAS_REQUIRE_SECRET_FILES": "1"}, flag=False)
    check("strict without the fallback flag: starts, no shared identity (nothing to read)",
          r.returncode == 0 and "SHARED none" in r.stdout, tail(r))

    print("-- D. without the flag, the token is never read --")
    r = run({"ATLAS_TOKEN_FILE": absent}, flag=False)
    check("ATLAS_TOKEN_FILE set without the flag: starts, IGNORED, the file is not opened",
          r.returncode == 0 and "SHARED none" in r.stdout and "IGNORED" in r.stderr, tail(r))
    r = run({"ATLAS_TOKEN": T_ENV}, flag=False)
    check("ATLAS_TOKEN set without the flag: IGNORED as before",
          r.returncode == 0 and "SHARED none" in r.stdout and "IGNORED" in r.stderr, tail(r))
    check("...without printing it", quiet(r))

    print()
    if failures:
        print(f"FAILED — {len(failures)} check(s): " + "; ".join(failures))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
