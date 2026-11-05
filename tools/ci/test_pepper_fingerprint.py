#!/usr/bin/env python3
"""test_pepper_fingerprint.py: scripts/openshift/pepper-fingerprint.py (ARO task 4.2).

Runs the script for real on fixed pepper files and asserts:

  * each fingerprint is the SHA-256 of the file bytes exactly as written (base64 of 32 bytes
    plus the trailing newline), equal to hashlib's and, where it is installed, to `sha256sum`'s;
  * stdout is exactly `<name>  sha256:<hex>` per file, and no pepper (its base64 text, its raw
    bytes as hex) appears on stdout or stderr, in a valid run or in a refusal;
  * a missing file, a file that is not base64, and base64 of the wrong length are refused
    (exit 1) without printing a fingerprint for them;
  * --compare passes on a recorded list (this script's format and sha256sum's), fails on a
    changed pepper or a missing name, and --from-list compares two saved lists;
  * the script runs when fed to python on stdin (`python - < script`), as in the pod.

Standard library only; runs on Linux (CI) and Windows.

    python tools/ci/test_pepper_fingerprint.py
"""
from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "openshift" / "pepper-fingerprint.py"

# Fixed, obviously-test peppers: 32 bytes each, base64, plus the newline new-secrets.py writes.
FIXED = {
    "password_pepper": base64.b64encode(bytes(range(32))) + b"\n",
    "token_pepper": base64.b64encode(bytes(range(32, 64))) + b"\n",
}
# `sha256sum` of these exact bytes, computed once and pinned, so a change to the definition
# (stripping the newline, hashing the decoded bytes) fails here even where sha256sum is absent.
KNOWN = {
    "password_pepper": "7d3a1c8c0fb3f26a4cb1cd78f37751d39dd8d37addb78103d8a346d010eeccdc",
    "token_pepper": "7669622d81b3d9dadbda12e4bef64a4b190d3a8da24e71a1dd67e10de0f0a792",
}


class PepperFingerprint(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.files = {}
        for k, v in FIXED.items():
            (self.dir / k).write_bytes(v)
            self.files[k] = self.dir / k

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self, *args, stdin_script=False):
        if stdin_script:
            return subprocess.run([sys.executable, "-", *map(str, args)],
                                  input=SCRIPT.read_text(encoding="ascii"),
                                  capture_output=True, text=True, timeout=60)
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)

    def assert_never_shown(self, r, values):
        for v in values:
            for needle in (v.strip().decode(), base64.b64decode(v.strip()).hex()):
                self.assertNotIn(needle, r.stdout + r.stderr, "a pepper value was shown")

    def expected_stdout(self, names):
        return "".join(f"{n}  sha256:{hashlib.sha256(FIXED[n]).hexdigest()}\n" for n in names)

    def test_fingerprint_is_sha256_of_the_bytes_as_written(self):
        r = self.run_script(*self.files.values())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, self.expected_stdout(FIXED))
        self.assertEqual(r.stderr, "")
        for k, v in FIXED.items():
            self.assertTrue(v.endswith(b"\n"))
            self.assertIn(f"{k}  sha256:{KNOWN[k]}", r.stdout)
            # Not the hash of the stripped text, nor of the decoded bytes.
            self.assertNotIn(hashlib.sha256(v.strip()).hexdigest(), r.stdout)
            self.assertNotIn(hashlib.sha256(base64.b64decode(v)).hexdigest(), r.stdout)
        self.assert_never_shown(r, FIXED.values())

    @unittest.skipUnless(shutil.which("sha256sum"), "sha256sum not installed")
    def test_equals_sha256sum(self):
        r = self.run_script(*self.files.values())
        # Run in the directory with bare names: GNU sha256sum escapes a path holding a backslash.
        s = subprocess.run([shutil.which("sha256sum"), *FIXED], cwd=self.dir,
                           capture_output=True, text=True, check=True)
        sums = {Path(line.split(None, 1)[1].lstrip("*")).name: line.split()[0]
                for line in s.stdout.splitlines()}
        for k in FIXED:
            self.assertIn(f"{k}  sha256:{sums[k]}", r.stdout)

    def test_runs_from_stdin_as_in_the_pod(self):
        r = self.run_script(*self.files.values(), stdin_script=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, self.expected_stdout(FIXED))

    def test_refuses_missing_and_malformed_without_showing_them(self):
        bad_b64 = b"this is not base64 at all!!\n"
        short = base64.b64encode(b"sixteen bytes!!!") + b"\n"
        (self.dir / "not_base64").write_bytes(bad_b64)
        (self.dir / "short").write_bytes(short)
        for path, why in ((self.dir / "missing", "not found"),
                          (self.dir / "not_base64", "not base64"),
                          (self.dir / "short", "decodes to 16 bytes")):
            r = self.run_script(path)
            self.assertEqual(r.returncode, 1, path)
            self.assertEqual(r.stdout, "")
            self.assertIn(why, r.stderr)
            self.assertNotIn("sha256:", r.stderr)
        r = self.run_script(self.dir / "short", self.files["token_pepper"])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, self.expected_stdout(["token_pepper"]))
        self.assertNotIn("sixteen", r.stdout + r.stderr)
        self.assertNotIn(short.strip().decode(), r.stdout + r.stderr)
        self.assertNotIn(bad_b64.strip().decode(), r.stdout + r.stderr)

    def test_compare(self):
        recorded = self.dir / "recorded.txt"
        recorded.write_text(self.run_script(*self.files.values()).stdout, encoding="utf-8")

        r = self.run_script("--compare", recorded, *self.files.values())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("all 2 fingerprints match", r.stderr)

        # sha256sum's format, with the binary-mode `*`, is accepted too.
        sums = self.dir / "sums.txt"
        sums.write_text("".join(f"{KNOWN[k]} *{self.files[k]}\n" for k in FIXED), encoding="utf-8")
        r = self.run_script("--compare", sums, *self.files.values())
        self.assertEqual(r.returncode, 0, r.stderr)

        # A restored copy without its newline: valid pepper, different fingerprint, a hint.
        restored = self.dir / "restored"
        restored.mkdir()
        (restored / "password_pepper").write_bytes(FIXED["password_pepper"].strip())
        (restored / "token_pepper").write_bytes(FIXED["token_pepper"])
        r = self.run_script("--compare", recorded, *(restored / k for k in FIXED))
        self.assertEqual(r.returncode, 1)
        self.assertIn("password_pepper: MISMATCH", r.stderr)
        self.assertIn("no trailing newline", r.stderr)
        self.assertNotIn("token_pepper: MISMATCH", r.stderr)

        # A different pepper fails.
        (restored / "password_pepper").write_bytes(base64.b64encode(bytes(32)) + b"\n")
        r = self.run_script("--compare", recorded, *(restored / k for k in FIXED))
        self.assertEqual(r.returncode, 1)
        self.assertIn("password_pepper: MISMATCH", r.stderr)
        self.assert_never_shown(r, list(FIXED.values()) + [base64.b64encode(bytes(32)) + b"\n"])

        # A file whose name is not recorded fails.
        partial = self.dir / "partial.txt"
        partial.write_text(f"token_pepper  sha256:{KNOWN['token_pepper']}\n", encoding="utf-8")
        r = self.run_script("--compare", partial, *self.files.values())
        self.assertEqual(r.returncode, 1)
        self.assertIn("password_pepper: not in", r.stderr)

        # --from-list: the pod's saved output against the record, including a UTF-16 file
        # (what Windows PowerShell 5.1's `>` writes).
        pod = self.dir / "pod.txt"
        pod.write_bytes(self.run_script(*self.files.values()).stdout.encode("utf-16"))
        r = self.run_script("--compare", recorded, "--from-list", pod)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_script("--compare", recorded, "--from-list", partial)
        self.assertEqual(r.returncode, 1)
        self.assertIn("password_pepper: recorded in", r.stderr)

        # A malformed list is a refusal.
        junk = self.dir / "junk.txt"
        junk.write_text("password_pepper  deadbeef\n", encoding="utf-8")
        r = self.run_script("--compare", junk, *self.files.values())
        self.assertEqual(r.returncode, 1)

    def test_usage_errors(self):
        r = self.run_script("--from-list", self.dir / "x")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
