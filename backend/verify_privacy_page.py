"""
Verification for TASK-1459: the privacy page states the change record.

Until 2026-10-31 `/auth/privacy` said "Atlas records no author on the content you
write", while `deps.record_graph_event` writes the actor and the accountable person
on every graph change and `GET /api/events` serves it. A privacy notice that
understates what is recorded is wrong in the worst direction, so these claims pin
the corrected text where a later edit cannot quietly drop it:

  A. The page says there is a change record, that it names who made each change,
     that nothing prunes it, and who can read it (anyone who can see the item, and
     owners all of it, with the export).
  B. The false sentence is gone: no "records no author", no "no per-change history",
     and no "nothing in it that names you" about the security log after an erasure.
  C. The read-audit paragraph follows the install: switched off by default, switched
     on when `audit_log.READS_ENABLED` is true, and the retention sentence follows
     ATLAS_AUDIT_RETENTION_DAYS.
  D. The rendered page is what the route serves: `GET /auth/privacy` without a
     credential returns 200 and the change-record section.
  E. "Where it lives" is ATLAS_PRIVACY_HOSTING: unset, the paragraph is exactly the
     original install's text (frozen below); set, it replaces it, HTML-escaped (a
     child process, because auth reads its settings at import).
  F. The record is kept on purpose (owner decision, 2026-10-31): the page says Atlas records
     who made every change and who wrote every comment so it can be traced and
     reviewed, that erasing an account does not rewrite it and that this is not
     changed on request, and it says what an erasure does do (name and email
     cleared, account disabled, sessions ended, agents stopped). The old offer to
     replace the copied ids and usernames is gone: no code path does that.
  G. The controller paragraph comes from `distribution.py` (STORY-oss-23 A4b): in the
     homelab copy it is byte for byte the paragraph auth.py held before (a hash, frozen
     below), and the other three A4b values equal the literals they replaced. The neutral
     copy is checked by `verify_fresh_install.py` §G.

It needs NO database: it renders `auth._privacy_page()` and drives the route through
a TestClient on the app's auth middleware alone. Throwaway peppers are set as plain
env vars, as every verify script does.

Run:
    python verify_privacy_page.py
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FAILURES: list[str] = []

for _k in ("ATLAS_AUDIT_READS", "ATLAS_AUDIT_RETENTION_DAYS", "ATLAS_REQUIRE_SECRET_FILES"):
    os.environ.pop(_k, None)
if "--child-hosting" not in sys.argv:  # the child is given its override by the parent
    os.environ.pop("ATLAS_PRIVACY_HOSTING", None)
os.environ.setdefault("ATLAS_PASSWORD_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_TOKEN_PEPPER", base64.b64encode(os.urandom(32)).decode())
os.environ.setdefault("ATLAS_LOGIN_ORIGINS", "https://testserver")
# Never connected to: nothing here opens a session.
os.environ.setdefault("ATLAS_DB_URL", "postgresql+psycopg2://verify:verify@127.0.0.1:1/none")

#: The "Where it lives" paragraph as this distribution serves it by default. It comes
#: from `distribution.py` (STORY-oss-23 A1), whose homelab values `verify_settings.py`
#: pins byte for byte, so the homelab's page does not move when an install describes
#: its own hosting, and the neutral overlay is checked against its own text.
import distribution  # noqa: E402

FROZEN_HOSTING = distribution.DEFAULT_PRIVACY_HOSTING
#: sha256 of the controller paragraph, of `db.Component.owner`'s default and of
#: repr(`passwords._CONTEXT_WORDS`) as they stood before STORY-oss-23 A4b moved them into
#: `distribution.py`. Hashes, so the homelab values are written in distribution.py alone.
_OLD_CONTROLLER_PARAGRAPH_SHA256 = "ad7253c4f90e08c67ddadee6b8ace28418115f69c1b5f544d569f581ff3c5071"
_OLD_COMPONENT_OWNER_SHA256 = "f0365f540d6c3abf1c55b0a233c80f5502f2921eafcf779ef74b11ebe02e924c"
_OLD_CONTEXT_WORDS_SHA256 = "ddd9023916d65b72d687a72048d10011aefa3754f6951c3265155fc9d41a3e84"
HOSTILE_HOSTING = "Our <b>own</b> server & \"nobody\" else's <script>x</script>"


def check(label: str, condition: bool, detail: str = "") -> None:
    print(("[PASS] " if condition else "[FAIL] ") + label + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def _child_hosting() -> int:
    """Render the page with ATLAS_PRIVACY_HOSTING set (the parent sets it)."""
    import auth
    sys.stdout.write(auth._privacy_page())
    return 0


def main() -> int:
    import html
    import subprocess

    import audit_log
    import auth

    page = auth._privacy_page()

    print("A. the change record is stated")
    check("A1 the page has a 'The change record' section",
          "<summary>The change record</summary>" in page)
    check("A2 it says every change to the board is written to it",
          "Every change to the board is written to a change record" in page)
    check("A3 it says each entry names who did it, the account and the credential",
          "who did it" in page and "its username at the time" in page
          and "which credential" in page)
    check("A4 it says the person an agent acts for is recorded",
          "the person it acts for" in page)
    check("A5 it says nothing prunes it",
          "nothing prunes it" in page)
    check("A6 it says who can read it: anyone who can see the item, owners all of it",
          "Anyone who can see an item can see its change record" in page
          and "owner accounts can see all of it" in page)
    check("A7 it says comments carry their author",
          "Comments carry their author" in page)

    print("B. the false sentences are gone")
    check("B1 no 'records no author'", "records no author" not in page)
    check("B2 no 'no per-change history'", "no per-change" not in page)
    check("B3 no claim that the security log names nothing after an erasure",
          "nothing in it that names you" not in page)

    print("C. the read audit follows the install")
    check("C1 default: the read audit is described as switched off",
          "It is switched off here." in page, "READS_ENABLED=%r" % audit_log.READS_ENABLED)
    check("C2 default: no retention period is stated as set",
          "no retention period is set" in page)
    was = audit_log.READS_ENABLED
    os.environ["ATLAS_AUDIT_RETENTION_DAYS"] = "90"
    try:
        audit_log.READS_ENABLED = True
        on = auth._privacy_page()
    finally:
        audit_log.READS_ENABLED = was
        os.environ.pop("ATLAS_AUDIT_RETENTION_DAYS", None)
    check("C3 switched on: the page says so", "<strong>switched on</strong> here" in on)
    check("C4 a retention setting is named in days", "older than 90 days" in on)

    print("D. the route serves it, with no credential")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    # `auth.install` puts the real gate and the real auth routes on a bare app. The
    # engine is lazy and points nowhere: a public route without a cookie never opens
    # a connection, so a 200 here also proves the page needs no database.
    app = FastAPI()
    auth.install(app, create_engine(os.environ["ATLAS_DB_URL"]))
    r = TestClient(app, base_url="https://testserver").get("/auth/privacy")
    check("D1 GET /auth/privacy with no credential is 200", r.status_code == 200,
          f"HTTP {r.status_code}")
    check("D2 ...and serves the change-record section",
          "<summary>The change record</summary>" in r.text)
    check("D3 ...and not the old sentence", "records no author" not in r.text)

    print("E. 'Where it lives' is ATLAS_PRIVACY_HOSTING")
    import settings
    check("E1 the default is the distribution's text",
          settings.DEFAULT_PRIVACY_HOSTING == FROZEN_HOSTING)
    check("E2 unset: the page shows exactly that paragraph",
          "<p>%s</p>" % html.escape(FROZEN_HOSTING) in page)
    check("E3 settings.load reads the override, stripped",
          settings.load({"ATLAS_LOGIN_ORIGINS": "https://testserver",
                         "ATLAS_PRIVACY_HOSTING": "  Ours.  "}).privacy_hosting == "Ours.")
    check("E4 a blank override keeps the default",
          settings.load({"ATLAS_LOGIN_ORIGINS": "https://testserver",
                         "ATLAS_PRIVACY_HOSTING": "   "}).privacy_hosting == FROZEN_HOSTING)
    env = dict(os.environ, ATLAS_PRIVACY_HOSTING=HOSTILE_HOSTING, PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child-hosting"],
                         env=env, capture_output=True, text=True, encoding="utf-8")
    child = out.stdout
    check("E5 set: the child rendered the page", out.returncode == 0 and "</details>" in child,
          out.stderr[-300:])
    check("E6 set: the override replaces the paragraph, HTML-escaped",
          "<p>%s</p>" % html.escape(HOSTILE_HOSTING) in child)
    check("E7 set: no markup from the override reaches the page",
          "<b>own</b>" not in child and "<script>" not in child)
    check("E8 set: the distribution's default text is gone",
          html.escape(FROZEN_HOSTING) not in child)

    print("F. the change record is kept on purpose, and the offer to rewrite it is gone")
    check("F1 the change record says it is kept on purpose",
          "This record is kept on purpose." in page)
    check("F2 ...that every change and every comment names who made it, for review",
          "Atlas records who made every change and who wrote every comment, so that "
          "what people and agents did on the board can be traced and reviewed "
          "afterwards." in page)
    check("F3 ...and that closing or erasing an account does not rewrite it",
          "Closing or erasing an account does not rewrite it." in page)
    check("F4 the deletion section says what an erasure does",
          "the operator clears the display name and email address on your account "
          "and closes it." in page
          and "Closing it ends your sessions, and the agent credentials you created "
              "and the agents linked to you stop working." in page)
    check("F5 ...that the copies are kept by design and not changed on request",
          "Erasing an account does not rewrite history." in page
          and "the username as it was when each entry was written" in page
          and "it is not changed on request" in page)
    check("F6 the offer to replace the copies is gone",
          "replaced too" not in page and "if you want those copies" not in page)
    check("F7 ...from the served page as well", "replaced too" not in r.text)

    print("G. the controller paragraph and the other A4b values come from distribution.py")
    import hashlib
    import db
    import passwords
    start = page.find("<p><strong>")
    para = page[start:page.find("</p>", start) + 4] if start >= 0 else ""
    check("G1 the controller paragraph is the distribution's controller and note",
          para == "<p><strong>%s</strong> holds it.%s</p>" % (
              html.escape(distribution.DEFAULT_PRIVACY_CONTROLLER),
              (" " + distribution.PRIVACY_COMPANY_NOTE) if distribution.PRIVACY_COMPANY_NOTE else ""),
          para[:200])
    check("G2 the Component.owner default and the password context words are the distribution's",
          db.Component.__table__.c.owner.default.arg == distribution.DEFAULT_COMPONENT_OWNER
          and passwords._CONTEXT_WORDS == tuple(distribution.PASSWORD_CONTEXT_WORDS))
    if distribution.NAME == "homelab":
        check("G3 homelab: the controller paragraph is byte for byte the one before the seam",
              hashlib.sha256(para.encode("utf-8")).hexdigest() == _OLD_CONTROLLER_PARAGRAPH_SHA256)
        check("G4 homelab: the Component.owner default is the literal before the seam",
              hashlib.sha256(distribution.DEFAULT_COMPONENT_OWNER.encode("utf-8")).hexdigest()
              == _OLD_COMPONENT_OWNER_SHA256)
        check("G5 homelab: the password context words are the literal before the seam",
              hashlib.sha256(repr(tuple(passwords._CONTEXT_WORDS)).encode("utf-8")).hexdigest()
              == _OLD_CONTEXT_WORDS_SHA256)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} claim(s) FAILED")
        return 1
    print("all claims passed")
    return 0


if __name__ == "__main__":
    sys.exit(_child_hosting() if "--child-hosting" in sys.argv else main())
