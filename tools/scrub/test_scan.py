#!/usr/bin/env python3
"""Self-test for tools/scrub/scan.py. Run: python tools/scrub/test_scan.py

Every fixture term is fake. Generic-pattern fixtures are assembled at runtime
(".".join(...)) so this file does not itself trip the generic patterns.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scan  # noqa: E402

FAKE_NAME = "Zorblax"
FAKE_DOMAIN = "quuxcorp" + ".test-fixture"
LAN_IP = ".".join(["192", "168", "7", "42"])
TEN_IP = ".".join(["10", "0", "3", "9"])
PUBLIC_IP = ".".join(["8", "8", "8", "8"])
MDNS = "printer" + ".local"
MAILBOX = "someone" + "@" + "mail-fixture.org"
SAFE_MAILBOX = "someone" + "@" + "example.com"
FAKE_OWNER = "octo-example"


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


class ScrubTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.deny = Path(self.tmp.name) / "denylist.txt"
        self.deny.write_text(
            "# fake terms only\n\n" + FAKE_NAME + "\nre:" + FAKE_DOMAIN.replace(".", r"\.") + "\n",
            encoding="utf-8",
        )
        self.allow = Path(self.tmp.name) / "allowlist.txt"
        self.allow.write_text("", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def args(self, *extra):
        return ["--root", str(self.root), "--allowlist", str(self.allow), *extra]

    # -- the acceptance criterion: a denylisted fixture fails enforce mode -- #
    def test_denylisted_fixture_fails_enforce(self):
        self.write("docs/a.md", f"Ask {FAKE_NAME}'s box at {FAKE_DOMAIN}.\n")
        code, out, _ = run(self.args("--mode", "enforce", "--denylist", str(self.deny)))
        self.assertEqual(code, 1)
        self.assertIn("denylist[1]", out)
        self.assertIn("denylist[2]", out)
        # The report names patterns by index, never by their text.
        self.assertNotIn(FAKE_NAME, out)
        self.assertNotIn("quuxcorp", out)

    def test_report_mode_never_fails(self):
        self.write("a.txt", f"{FAKE_NAME} {LAN_IP}\n")
        code, out, _ = run(self.args("--mode", "report", "--denylist", str(self.deny)))
        self.assertEqual(code, 0)
        self.assertIn("violations=2", out)

    def test_clean_tree_passes_enforce(self):
        self.write("a.txt", f"public {PUBLIC_IP}, {SAFE_MAILBOX}, ~/.local/bin, localhost\n")
        code, out, _ = run(self.args("--mode", "enforce", "--denylist", str(self.deny)))
        self.assertEqual(code, 0, out)

    def test_generic_patterns(self):
        self.write("a.txt", f"{LAN_IP} {TEN_IP} {MDNS} {MAILBOX}\n")
        code, _, _ = run(self.args("--generic-only", "--json", str(self.root.parent / "r.json")))
        self.assertEqual(code, 0)
        r = json.loads((self.root.parent / "r.json").read_text())
        self.assertEqual(r["per_pattern"]["rfc1918-ipv4"]["hits"], 2)
        self.assertEqual(r["per_pattern"]["dot-local-host"]["hits"], 1)
        self.assertEqual(r["per_pattern"]["email"]["hits"], 1)

    def test_missing_denylist_warns_and_runs_generic(self):
        self.write("a.txt", f"{FAKE_NAME} {LAN_IP}\n")
        code, out, err = run(self.args("--mode", "enforce", "--denylist", str(self.root / "nope")))
        # stderr locally, a ::warning annotation on stdout under GitHub Actions
        self.assertRegex(out + err, r"WARNING|::warning")
        self.assertIn("generic patterns only", out)
        self.assertEqual(code, 1)  # the LAN IP still counts
        self.assertNotIn("denylist[1]", out)

    def test_allowlist(self):
        self.write("docs/example.md", f"{LAN_IP}\n")
        self.write("src/x.py", f"{FAKE_NAME}\n")
        self.allow.write_text("docs/* rfc1918-ipv4\nsrc/x.py\n", encoding="utf-8")
        code, out, _ = run(self.args("--mode", "enforce", "--denylist", str(self.deny)))
        self.assertEqual(code, 0, out)

    def test_pattern_specific_allow_does_not_cover_others(self):
        self.write("docs/example.md", f"{LAN_IP} {FAKE_NAME}\n")
        self.allow.write_text("docs/* rfc1918-ipv4\n", encoding="utf-8")
        code, _, _ = run(self.args("--mode", "enforce", "--denylist", str(self.deny)))
        self.assertEqual(code, 1)

    def test_path_filter(self):
        self.write("export/ok.md", "clean\n")
        self.write("private/notes.md", f"{FAKE_NAME}\n")
        code, _, _ = run(self.args("--mode", "enforce", "--denylist", str(self.deny), "--path", "export/*"))
        self.assertEqual(code, 0)

    def test_bad_regex_is_config_error_without_echo(self):
        self.deny.write_text("re:secret-term(\n", encoding="utf-8")
        code, out, err = run(self.args("--denylist", str(self.deny)))
        self.assertEqual(code, 2)
        self.assertNotIn("secret-term", out + err)

    # -- --mask-github-owner: the account name only inside github.com links -- #
    def owner_run(self, text, *extra):
        self.deny.write_text(FAKE_OWNER + "\n", encoding="utf-8")
        self.write("SECURITY.md", text)
        return run(self.args("--mode", "enforce", "--denylist", str(self.deny), *extra))

    def test_owner_masked_in_github_link(self):
        for link in (f"https://github.com/{FAKE_OWNER}/repo/security",
                     f"see https://GitHub.com/{FAKE_OWNER.upper()}.",
                     f"https://www.github.com/{FAKE_OWNER}"):
            code, out, _ = self.owner_run(link + "\n", "--mask-github-owner", FAKE_OWNER)
            self.assertEqual(code, 0, (link, out))

    def test_owner_not_masked_elsewhere(self):
        for text in (f"ask {FAKE_OWNER} first",
                     f"https://github.com/{FAKE_OWNER}-other/repo",
                     f"https://gitlab.com/{FAKE_OWNER}/repo",
                     f"https://notgithub.com/{FAKE_OWNER}/repo",
                     f"https://github.com/{FAKE_OWNER}/repo by {FAKE_OWNER}"):
            code, out, _ = self.owner_run(text + "\n", "--mask-github-owner", FAKE_OWNER)
            self.assertEqual(code, 1, text)
            self.assertIn("denylist[1]", out)
            self.assertNotIn(FAKE_OWNER, out.lower())

    def test_owner_not_masked_without_option(self):
        code, _, _ = self.owner_run(f"https://github.com/{FAKE_OWNER}/repo\n")
        self.assertEqual(code, 1)

    def test_placeholder_survives_the_export_substitution(self):
        # carve.py --owner rewrites the placeholder in every exported text file; were scan.py to
        # spell it out, the exported mask would write the owner name back in.
        placeholder = "<owner-" + "account>"
        self.assertEqual(scan.OWNER_PLACEHOLDER, placeholder)
        self.assertNotIn(placeholder, Path(scan.__file__).read_text(encoding="utf-8"))
        self.assertEqual(scan.github_owner_mask(FAKE_OWNER).sub(
            lambda m: m.group(1) + placeholder, f"github.com/{FAKE_OWNER}/x"), f"github.com/{placeholder}/x")

    def test_owner_mask_rejects_bad_name_without_echo(self):
        code, out, err = self.owner_run("clean\n", "--mask-github-owner", "bad/name(")
        self.assertEqual(code, 2)
        self.assertNotIn("bad/name", out + err)

    def test_binary_files_skipped(self):
        (self.root / "b.bin").write_bytes(b"\0\0" + FAKE_NAME.encode())
        code, _, _ = run(self.args("--mode", "enforce", "--denylist", str(self.deny)))
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
