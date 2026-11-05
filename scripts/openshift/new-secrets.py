#!/usr/bin/env python3
"""new-secrets.py: the two peppers, and the Atlas Secrets on OpenShift (ARO task 4.1).

The OpenShift counterpart of scripts/init-secrets.sh, for an install whose secrets are plain
Secrets made by a script rather than delivered from Azure Key Vault by the External Secrets
Operator (docs/oss/research/openshift-aro.md, question 7). Standard library only, no shell, so
it runs the same from PowerShell 7 and from bash:

    python scripts/openshift/new-secrets.py -n atlas                 # generate, then create
    python scripts/openshift/new-secrets.py -n atlas --dry-run       # print the oc commands
    python scripts/openshift/new-secrets.py -n atlas --reuse         # Secret from kept files

WHAT IT DOES, in this order, and stops at the first refusal before changing anything:

  1. Generates the two peppers, 32 random bytes each from `secrets`, base64 with a trailing
     newline (the shape init-secrets.sh writes; Atlas strips the newline when it reads the
     file), into OUT/password_pepper and OUT/token_pepper. OUT defaults to ./secrets/openshift,
     which the repository's .gitignore covers (`secrets/` at any depth).
  2. Runs `oc create secret generic <name> --from-file=<key>=<path>` for each Secret below.
     Only file PATHS go on oc's command line; a value is never in argv, in an environment
     variable or in this script's output. oc reads the files itself.

THE SECRETS it creates, with the keys the manifests mount (research document, "The Secrets on
OpenShift" and "The env mapping"):

  atlas-peppers   keys password_pepper, token_pepper. Always. Mounted at /run/secrets/peppers/
                  in atlas-api, the migrate Job and the audit CronJob
                  (ATLAS_PASSWORD_PEPPER_FILE, ATLAS_TOKEN_PEPPER_FILE).
  atlas-oidc      key client_secret (--oidc-client-secret FILE) or client_key
                  (--oidc-client-key FILE). Component `oidc` only. The value comes from your
                  identity provider; this script only copies your file into the Secret.
  atlas-embed     key `key` (--embed-key FILE). Component `retrieval`, key mode only.

Not made here: atlas-db-app and atlas-db-ca (CloudNativePG makes them), the TLS Secrets (the
service CA makes them), the mTLS ones (your PKI) and atlas-backup-azure (its keys follow the
Barman Cloud plugin's ObjectStore, which the manifests task fixes).

THE PEPPERS ARE FIXED FOR THE LIFE OF THE INSTALL. New peppers sign every account out and void
every service token. So the script REFUSES (exit 1) when OUT already holds a pepper file, or
when a Secret it would create already exists in the namespace, unless told otherwise:

  --reuse    use the pepper files already in OUT instead of generating (a reinstall, or files
             restored from Key Vault). They must each hold base64 of 32 bytes.
  --force    overwrite existing pepper files with new ones, and delete and recreate an
             existing Secret. ON AN INSTALL WITH DATA THIS BREAKS EVERY SIGN-IN AND TOKEN.

Then back the peppers up (docs/oss/guides/openshift-secrets.md shows `az keyvault secret set
--file`), record their fingerprints, and keep OUT as protected as the vault, or delete it.

FILE PROTECTION. On Linux and macOS OUT is made 0700 and each pepper file 0600. On Windows
those modes are cosmetic (only the read-only bit exists); what protects the files is the
directory's ACL. Create OUT under your own profile, or restrict it before the files are
written, for example in PowerShell:
    icacls OUT /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F"

OPTIONS
  -n, --namespace NS   the project to create the Secrets in (default: oc's current project)
  --out DIR            where the pepper files are (default: ./secrets/openshift)
  --oc PATH            the oc binary (default: `oc` on PATH)
  --dry-run            write nothing and run nothing that changes the cluster: print the
                       oc commands, with paths only

Exit 0 on success, 1 on a refusal or failure, 2 on a usage error.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

PEPPERS = ("password_pepper", "token_pepper")
PEPPER_BYTES = 32
DEFAULT_OUT = Path("secrets") / "openshift"
WINDOWS = os.name == "nt"


class Refusal(Exception):
    """A reason to stop before changing anything; the message never holds a secret."""


def say(msg: str) -> None:
    print(f"new-secrets: {msg}", flush=True)


def plan_secrets(args, out: Path) -> list[tuple[str, list[tuple[str, Path]]]]:
    """[(secret name, [(key, file path)])], in creation order."""
    plan = [("atlas-peppers", [(k, out / k) for k in PEPPERS])]
    if args.oidc_client_secret and args.oidc_client_key:
        raise Refusal("give --oidc-client-secret or --oidc-client-key, not both")
    if args.oidc_client_secret:
        plan.append(("atlas-oidc", [("client_secret", Path(args.oidc_client_secret))]))
    if args.oidc_client_key:
        plan.append(("atlas-oidc", [("client_key", Path(args.oidc_client_key))]))
    if args.embed_key:
        plan.append(("atlas-embed", [("key", Path(args.embed_key))]))
    return plan


def oc_base(args) -> list[str]:
    cmd = [args.oc]
    if args.namespace:
        cmd += ["-n", args.namespace]
    return cmd


def create_cmd(args, name: str, items: list[tuple[str, Path]]) -> list[str]:
    return oc_base(args) + ["create", "secret", "generic", name] + [
        f"--from-file={key}={path}" for key, path in items]


def show(cmd: list[str]) -> str:
    """A command for display: argv joined, each part quoted only when it needs it."""
    return " ".join(f'"{c}"' if (not c or any(ch in c for ch in ' "\'')) else c for c in cmd)


def run_oc(cmd: list[str]) -> subprocess.CompletedProcess:
    # No shell, so no quoting differs between PowerShell and bash. oc's own output is
    # captured and only its error text is shown: `oc create secret` echoes the name, not data.
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True)


def secret_exists(args, name: str) -> bool:
    r = run_oc(oc_base(args) + ["get", "secret", name, "-o", "name", "--ignore-not-found"])
    if r.returncode != 0:
        raise Refusal(f"`oc get secret {name}` failed ({r.returncode}): {r.stderr.strip()}"
                      " (logged in? `oc whoami`; the right project? -n)")
    return bool(r.stdout.strip())


def check_pepper_file(path: Path) -> None:
    """A kept pepper must be base64 of 32 bytes. Only its length is ever reported."""
    raw = path.read_bytes().strip()
    try:
        n = len(base64.b64decode(raw, validate=True))
    except (binascii.Error, ValueError):
        raise Refusal(f"{path} is not base64 ({len(raw)} characters); a pepper is base64 of "
                      f"{PEPPER_BYTES} bytes") from None
    if n != PEPPER_BYTES:
        raise Refusal(f"{path} decodes to {n} bytes; a pepper is {PEPPER_BYTES}")


def check_not_tracked(out: Path) -> None:
    """Refuse an OUT inside a git work tree that git would not ignore (as init-secrets.sh)."""
    git = shutil.which("git")
    if not git:
        return
    probe = (out / ".new-secrets-probe").resolve()
    here = probe.parent
    while not here.is_dir() and here != here.parent:   # the nearest directory that exists
        here = here.parent
    inside = subprocess.run([git, "-C", str(here), "rev-parse", "--is-inside-work-tree"],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return
    ignored = subprocess.run([git, "-C", str(here), "check-ignore", "-q", str(probe)],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if ignored.returncode != 0:
        raise Refusal(f"{out} is inside a git work tree and NOT ignored; refusing to write "
                      "secrets there (use --out, or a directory named secrets/)")


def write_pepper(path: Path) -> None:
    """Create the file exclusively (never follow or reuse one), 0600, then write the value."""
    value = base64.b64encode(secrets.token_bytes(PEPPER_BYTES)) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, value)
    finally:
        os.close(fd)
    del value


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="new-secrets.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-n", "--namespace")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--oc", default="oc")
    p.add_argument("--dry-run", action="store_true")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--force", action="store_true")
    mode.add_argument("--reuse", action="store_true")
    p.add_argument("--oidc-client-secret", metavar="FILE")
    p.add_argument("--oidc-client-key", metavar="FILE")
    p.add_argument("--embed-key", metavar="FILE")
    args = p.parse_args(argv)

    out = Path(args.out)
    try:
        plan = plan_secrets(args, out)
        oc = shutil.which(args.oc)
        if not oc and not args.dry_run:
            raise Refusal(f"`{args.oc}` is not on PATH (install the OpenShift CLI, or --oc PATH)")
        if oc:
            args.oc = oc

        # 1. The pepper files: refuse, reuse or (re)generate, decided before anything is written.
        existing = [out / k for k in PEPPERS if (out / k).exists()]
        if args.reuse:
            missing = [str(out / k) for k in PEPPERS if not (out / k).is_file()]
            if missing:
                raise Refusal(f"--reuse, but {', '.join(missing)} not found")
            for k in PEPPERS:
                check_pepper_file(out / k)
        elif existing and not args.force:
            raise Refusal(f"refusing to overwrite {', '.join(map(str, existing))}: the peppers are "
                          "fixed for the life of the install (--reuse to create the Secret from "
                          "them, --force only on an install with no data)")
        for name, items in plan[1:]:
            for _, path in items:
                if not path.is_file():
                    raise Refusal(f"{path} (for Secret {name}) is not a file")

        # 2. The Secrets: refuse an existing one unless --force. Checked even in a dry run
        # when oc is there, so the dry run shows the refusal the real run would hit.
        replace = []
        if oc:
            for name, _ in plan:
                if secret_exists(args, name):
                    if not args.force:
                        raise Refusal(f"Secret {name} already exists"
                                      f"{' in ' + args.namespace if args.namespace else ''}; "
                                      "refusing to replace it (--force deletes and recreates it)")
                    replace.append(name)

        if args.dry_run:
            if not args.reuse:
                say(f"would write {', '.join(str(out / k) for k in PEPPERS)} (0600; dir 0700)")
            for name, items in plan:
                if name in replace:
                    say("would run: " + show(oc_base(args) + ["delete", "secret", name]))
                say("would run: " + show(create_cmd(args, name, items)))
            if not oc:
                say(f"(`{args.oc}` is not on PATH, so existing Secrets were not checked)")
            return 0

        if not args.reuse:
            check_not_tracked(out)
            out.mkdir(parents=True, exist_ok=True)
            if not WINDOWS:
                os.chmod(out, 0o700)
            for k in PEPPERS:
                if (out / k).exists():   # only with --force, decided above
                    (out / k).unlink()
                write_pepper(out / k)
            say(f"wrote {', '.join(str(out / k) for k in PEPPERS)}")
            if WINDOWS:
                say("on Windows the 0600 mode is cosmetic: the ACL of "
                    f"{out.resolve()} is what protects the peppers (see --help)")

        for name, items in plan:
            if name in replace:
                r = run_oc(oc_base(args) + ["delete", "secret", name])
                if r.returncode != 0:
                    raise Refusal(f"`oc delete secret {name}` failed: {r.stderr.strip()}")
                say(f"deleted Secret {name} (--force)")
            cmd = create_cmd(args, name, items)
            r = run_oc(cmd)
            if r.returncode != 0:
                raise Refusal(f"`oc create secret generic {name}` failed: {r.stderr.strip()}")
            say(f"created Secret {name} (keys: {', '.join(k for k, _ in items)})")
    except Refusal as e:
        print(f"new-secrets: {e}", file=sys.stderr)
        return 1

    say("done. Next: back the peppers up to Key Vault and record their fingerprints "
        "(docs/oss/guides/openshift-secrets.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
