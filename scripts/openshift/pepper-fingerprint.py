#!/usr/bin/env python3
"""pepper-fingerprint.py: the SHA-256 of each pepper file, never its value (ARO task 4.2).

Proves that the peppers in the vault, in a restored copy and in the running pods are the same,
without anyone seeing one. The fingerprint is the SHA-256 of the file's bytes EXACTLY AS WRITTEN:
the base64 of 32 bytes plus the trailing newline, the shape scripts/openshift/new-secrets.py
writes and the Secret holds. So it equals `sha256sum FILE` and `Get-FileHash -Algorithm SHA256
FILE` on the same file. A fingerprint of 32 random bytes reveals nothing usable.

Standard library only, ASCII only, no shell: it runs the same from PowerShell 7 and bash, and
inside the atlas-api pod, whose image has python but no copy of this script and a read-only root
filesystem. Feed it to python on stdin there; the script writes nothing:

    oc exec -i deploy/atlas-api -- python - < scripts/openshift/pepper-fingerprint.py      # bash
    Get-Content -Raw scripts/openshift/pepper-fingerprint.py |
        oc exec -i deploy/atlas-api -- python -                                         # PowerShell

Locally, on files created by new-secrets.py or downloaded from Key Vault:

    python scripts/openshift/pepper-fingerprint.py secrets/openshift/password_pepper secrets/openshift/token_pepper
    python scripts/openshift/pepper-fingerprint.py --compare recorded.txt secrets/openshift/*_pepper

OUTPUT. One line per file on stdout, `<file name>  sha256:<hex>`, and nothing else; refusals and
mismatches go to stderr. That output, saved to a file, is the recorded fingerprint list.

CHECKS. A file that is missing, or that is not base64 of 32 bytes (ASCII whitespace around it
ignored, as Atlas ignores it), is refused: only its path and length are reported, never its
content, and no fingerprint is printed for it.

OPTIONS
  FILE ...              the pepper files (default: the two mounted in the pod,
                        /run/secrets/peppers/password_pepper and /run/secrets/peppers/token_pepper)
  --compare LIST        compare against a recorded list: lines `<name>  sha256:<hex>` (this
                        script's output) or `<hex>  <path>` (sha256sum's). Matched by file name;
                        every file must be in LIST with the same fingerprint.
  --from-list ACTUAL    with --compare: take the actual fingerprints from ACTUAL (for example what
                        the pod printed, saved to a file) instead of hashing FILEs.

Exit 0 when every file is a valid pepper (and, with --compare, matches), 1 otherwise, 2 on a
usage error.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import os
import re
import sys

DEFAULT_FILES = ("/run/secrets/peppers/password_pepper", "/run/secrets/peppers/token_pepper")
PEPPER_BYTES = 32
_HEX = re.compile(r"^[0-9a-f]{64}$")


class Refusal(Exception):
    """A reason to fail; the message never holds a pepper."""


def err(msg: str) -> None:
    print(f"pepper-fingerprint: {msg}", file=sys.stderr, flush=True)


def name_of(path: str) -> str:
    return os.path.basename(path.replace("\\", "/").rstrip("/")) or path


def fingerprint(path: str) -> str:
    """'sha256:<hex>' of the file's bytes as written, after checking it is a pepper."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        raise Refusal(f"{path}: not found") from None
    except IsADirectoryError:
        raise Refusal(f"{path}: is a directory, not a pepper file") from None
    except OSError as e:
        raise Refusal(f"{path}: cannot be read ({e.strerror})") from None
    try:
        raw = data.strip()
        try:
            n = len(base64.b64decode(raw, validate=True))
        except (binascii.Error, ValueError):
            raise Refusal(f"{path}: not base64 ({len(raw)} characters); a pepper is base64 of "
                          f"{PEPPER_BYTES} bytes") from None
        if n != PEPPER_BYTES:
            raise Refusal(f"{path}: decodes to {n} bytes; a pepper is {PEPPER_BYTES}")
        return "sha256:" + hashlib.sha256(data).hexdigest()
    finally:
        del data


def shape_hint(path: str) -> str:
    """Why a valid pepper may hash differently: its line ending. Says nothing about the value."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return ""
    try:
        if data.endswith(b"\r\n"):
            return " (the file ends in CRLF; new-secrets.py writes a single LF)"
        if not data.endswith(b"\n"):
            return " (the file has no trailing newline; new-secrets.py writes one)"
        if data.endswith(b"\n\n") or data != data.lstrip():
            return " (the file has extra whitespace; new-secrets.py writes base64 plus one LF)"
        return ""
    finally:
        del data


def read_list(path: str) -> dict[str, str]:
    """{file name: 'sha256:<hex>'} from this script's output or sha256sum's."""
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
    except OSError as e:
        raise Refusal(f"{path}: cannot read the fingerprint list ({e.strerror})") from None
    # Windows PowerShell 5.1's `>` writes UTF-16 with a BOM; PowerShell 7 and bash write UTF-8.
    try:
        text = blob.decode("utf-16" if blob[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig")
    except UnicodeDecodeError:
        raise Refusal(f"{path}: not a text file") from None
    lines = text.splitlines()
    found: dict[str, str] = {}
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise Refusal(f"{path}:{i}: not `<name>  sha256:<hex>` or `<hex>  <path>`")
        a, b = parts[0], parts[1].strip()
        if a.startswith("\\") and _HEX.match(a[1:].lower()):   # sha256sum's escaped-name mark
            a = a[1:]
        if b.lower().startswith("sha256:") and _HEX.match(b[7:].lower()):
            name, digest = a, b[7:].lower()
        elif _HEX.match(a.lower()):
            name, digest = name_of(b.lstrip("*")), a.lower()
        else:
            raise Refusal(f"{path}:{i}: not `<name>  sha256:<hex>` or `<hex>  <path>`")
        if name in found and found[name] != "sha256:" + digest:
            raise Refusal(f"{path}: {name} is listed twice with different fingerprints")
        found[name] = "sha256:" + digest
    if not found:
        raise Refusal(f"{path}: no fingerprints in it")
    return found


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="pepper-fingerprint.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="*", metavar="FILE")
    p.add_argument("--compare", metavar="LIST")
    p.add_argument("--from-list", metavar="ACTUAL")
    args = p.parse_args(argv)
    if args.from_list and not args.compare:
        p.error("--from-list needs --compare")
    if args.from_list and args.files:
        p.error("give FILEs or --from-list, not both")

    failed = False
    actual: dict[str, str] = {}
    paths: dict[str, str] = {}
    try:
        if args.from_list:
            actual = read_list(args.from_list)
        else:
            for path in args.files or DEFAULT_FILES:
                name = name_of(path)
                if args.compare and name in paths:
                    raise Refusal(f"two files are named {name}; --compare matches by name")
                try:
                    fp = fingerprint(path)
                except Refusal as e:
                    err(str(e))
                    failed = True
                    continue
                actual[name] = fp
                paths[name] = path
                print(f"{name}  {fp}", flush=True)

        if args.compare:
            expected = read_list(args.compare)
            for name, fp in actual.items():
                want = expected.get(name)
                if want is None:
                    err(f"{name}: not in {args.compare}")
                    failed = True
                elif want != fp:
                    hint = shape_hint(paths[name]) if name in paths else ""
                    err(f"{name}: MISMATCH, recorded {want}{hint}")
                    failed = True
            if args.from_list:
                for name in expected:
                    if name not in actual:
                        err(f"{name}: recorded in {args.compare} but not in {args.from_list}")
                        failed = True
            if not failed:
                err(f"all {len(actual)} fingerprints match {args.compare}")
    except Refusal as e:
        err(str(e))
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
