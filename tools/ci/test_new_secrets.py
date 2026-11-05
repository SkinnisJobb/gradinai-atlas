#!/usr/bin/env python3
"""test_new_secrets.py: scripts/openshift/new-secrets.py never shows a secret (ARO task 4.1).

Runs the script for real against a fake `oc` placed first on PATH. The fake records every argv
and environment it is started with, and keeps the Secrets it is asked to create (reading the
`--from-file` paths, as the real oc does). The tests assert:

  * no generated pepper (its base64 text, or its raw bytes as hex) appears on the script's
    stdout or stderr, in any argv the fake oc received, or in the fake oc's environment;
  * the Secret atlas-peppers holds exactly the two files' contents under the keys
    password_pepper and token_pepper;
  * a second run without --force or --reuse refuses (exit 1) and leaves the files unchanged;
    --reuse refuses an existing Secret; --force replaces both; --dry-run changes nothing;
  * an operator-provided file (atlas-oidc) also reaches oc only as a path.

Standard library only; runs on Linux (CI) and Windows (the fake oc is then an oc.cmd).

    python tools/ci/test_new_secrets.py
"""
from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "openshift" / "new-secrets.py"

FAKE_OC = r'''
import json, os, sys
from pathlib import Path
state = Path(os.environ["FAKE_OC_STATE"])
with open(os.environ["FAKE_OC_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ)}) + "\n")
a = sys.argv[1:]
if a[:1] == ["-n"]:
    a = a[2:]
if a[:2] == ["get", "secret"]:
    if (state / a[2]).exists():
        print("secret/" + a[2])
    sys.exit(0)
if a[:3] == ["create", "secret", "generic"]:
    name = a[3]
    if (state / name).exists():
        print('Error from server (AlreadyExists): secrets "%s" already exists' % name, file=sys.stderr)
        sys.exit(1)
    data = {}
    for arg in a[4:]:
        key, path = arg[len("--from-file="):].split("=", 1)
        data[key] = Path(path).read_bytes().hex()
    (state / name).write_text(json.dumps(data), encoding="utf-8")
    print("secret/%s created" % name)
    sys.exit(0)
if a[:2] == ["delete", "secret"]:
    (state / a[2]).unlink()
    print('secret "%s" deleted' % a[2])
    sys.exit(0)
print("fake oc: unexpected " + " ".join(a), file=sys.stderr)
sys.exit(3)
'''


class NewSecrets(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        t = Path(self._tmp.name)
        self.bin, self.state, self.out = t / "bin", t / "state", t / "secrets" / "openshift"
        self.bin.mkdir()
        self.state.mkdir()
        self.log = t / "oc.log"
        (self.bin / "fake_oc.py").write_text(FAKE_OC, encoding="utf-8")
        if os.name == "nt":
            (self.bin / "oc.cmd").write_text(
                f'@"{sys.executable}" "%~dp0fake_oc.py" %*\r\n', encoding="utf-8")
        else:
            oc = self.bin / "oc"
            oc.write_text(f"#!{sys.executable}\n" + FAKE_OC, encoding="utf-8")
            oc.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                        FAKE_OC_LOG=str(self.log), FAKE_OC_STATE=str(self.state))

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), "-n", "atlas", "--out", str(self.out),
                               *args], env=self.env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def peppers(self) -> dict[str, bytes]:
        return {k: (self.out / k).read_bytes() for k in ("password_pepper", "token_pepper")}

    def assert_never_shown(self, values, *runs):
        needles = []
        for v in values:
            text = v.strip().decode()
            needles += [text, base64.b64decode(text).hex(), v.hex()]
        seen = [r.stdout for r in runs] + [r.stderr for r in runs]
        for c in self.calls():
            seen += c["argv"]
            seen += [f"{k}={v}" for k, v in c["env"].items()]
        for n in needles:
            for s in seen:
                self.assertNotIn(n, s, "a secret value was shown")

    def test_first_run_then_refusals_then_force(self):
        r1 = self.run_script()
        self.assertEqual(r1.returncode, 0, r1.stderr)
        first = self.peppers()
        for v in first.values():
            self.assertEqual(len(base64.b64decode(v.strip(), validate=True)), 32)
            self.assertTrue(v.endswith(b"\n"))
        self.assertNotEqual(first["password_pepper"], first["token_pepper"])
        if os.name != "nt":
            for k in first:
                self.assertEqual(stat.S_IMODE((self.out / k).stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o700)
        stored = json.loads((self.state / "atlas-peppers").read_text(encoding="utf-8"))
        self.assertEqual({k: bytes.fromhex(v) for k, v in stored.items()}, first)
        creates = [c["argv"] for c in self.calls() if "create" in c["argv"]]
        self.assertEqual(len(creates), 1)
        self.assertIn(f"--from-file=password_pepper={self.out / 'password_pepper'}", creates[0])

        # A second run without a flag refuses, before touching the files or the cluster.
        n_calls = len(self.calls())
        r2 = self.run_script()
        self.assertEqual(r2.returncode, 1)
        self.assertIn("refusing to overwrite", r2.stderr)
        self.assertEqual(self.peppers(), first)
        self.assertFalse(any("create" in c["argv"] for c in self.calls()[n_calls:]))

        # --reuse keeps the files but still refuses the existing Secret.
        r3 = self.run_script("--reuse")
        self.assertEqual(r3.returncode, 1)
        self.assertIn("already exists", r3.stderr)
        self.assertEqual(self.peppers(), first)

        # --dry-run with --force: prints the commands, changes nothing.
        r4 = self.run_script("--force", "--dry-run")
        self.assertEqual(r4.returncode, 0, r4.stderr)
        self.assertIn("would run:", r4.stdout)
        self.assertIn("delete secret atlas-peppers", r4.stdout)
        self.assertEqual(self.peppers(), first)
        self.assertEqual(json.loads((self.state / "atlas-peppers").read_text(encoding="utf-8")), stored)

        # --force replaces both peppers and the Secret.
        r5 = self.run_script("--force")
        self.assertEqual(r5.returncode, 0, r5.stderr)
        second = self.peppers()
        self.assertNotEqual(second["password_pepper"], first["password_pepper"])
        stored2 = json.loads((self.state / "atlas-peppers").read_text(encoding="utf-8"))
        self.assertEqual({k: bytes.fromhex(v) for k, v in stored2.items()}, second)

        self.assert_never_shown(list(first.values()) + list(second.values()), r1, r2, r3, r4, r5)

    def test_dry_run_on_a_clean_namespace_writes_nothing(self):
        r = self.run_script("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("create secret generic atlas-peppers", r.stdout)
        self.assertFalse(self.out.exists())
        self.assertFalse(any("create" in c["argv"] for c in self.calls()))

    def test_reuse_creates_the_secret_from_kept_files(self):
        self.out.mkdir(parents=True)
        kept = {k: base64.b64encode(os.urandom(32)) + b"\n" for k in ("password_pepper", "token_pepper")}
        for k, v in kept.items():
            (self.out / k).write_bytes(v)
        r = self.run_script("--reuse")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.peppers(), kept)
        stored = json.loads((self.state / "atlas-peppers").read_text(encoding="utf-8"))
        self.assertEqual({k: bytes.fromhex(v) for k, v in stored.items()}, kept)
        self.assert_never_shown(list(kept.values()), r)

    def test_reuse_refuses_a_file_that_is_not_a_pepper(self):
        self.out.mkdir(parents=True)
        (self.out / "password_pepper").write_bytes(base64.b64encode(b"short") + b"\n")
        (self.out / "token_pepper").write_bytes(base64.b64encode(os.urandom(32)) + b"\n")
        r = self.run_script("--reuse")
        self.assertEqual(r.returncode, 1)
        self.assertIn("decodes to 5 bytes", r.stderr)
        self.assertFalse(any("create" in c["argv"] for c in self.calls()))

    def test_operator_file_reaches_oc_as_a_path_only(self):
        src = Path(self._tmp.name) / "client_secret"
        value = b"oidc-" + os.urandom(16).hex().encode()
        src.write_bytes(value)
        r = self.run_script("--oidc-client-secret", str(src))
        self.assertEqual(r.returncode, 0, r.stderr)
        stored = json.loads((self.state / "atlas-oidc").read_text(encoding="utf-8"))
        self.assertEqual(stored, {"client_secret": value.hex()})
        for c in self.calls():
            self.assertNotIn(value.decode(), " ".join(c["argv"]))
        self.assertNotIn(value.decode(), r.stdout + r.stderr)
        self.assert_never_shown(list(self.peppers().values()), r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
