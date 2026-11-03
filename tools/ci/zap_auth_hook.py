"""ZAP hook for the authenticated API scan (.github/workflows/dast.yml, STORY-atlas-lcm-plan item 6).

Loaded INSIDE the ZAP container by `zap-api-scan.py --hook /zap/wrk/zap_auth_hook.py`. It reads
the session's Cookie header from /zap/wrk/session.cookie (written by tools/ci/dast_session.py)
and adds a Replacer rule that sets it on every request ZAP sends. The value travels from a file
to ZAP's local API: it is on no command line and in no environment variable.

Excluded from the scan: the sign-out and password routes (a scan that signs itself out, or
changes the test user's password, scans the rest of the API unauthenticated). /auth/login gets
the passive baseline scan instead.
"""

COOKIE_FILE = "/zap/wrk/session.cookie"
EXCLUDE = (r".*/auth/logout.*", r".*/auth/password.*", r".*/auth/login.*")


def zap_started(zap, target):
    with open(COOKIE_FILE, encoding="utf-8") as fh:
        cookie = fh.read().strip()
    zap.replacer.add_rule(description="atlas-session", enabled="true", matchtype="REQ_HEADER",
                          matchregex="false", matchstring="Cookie", replacement=cookie)
    for rx in EXCLUDE:
        zap.ascan.exclude_from_scan(rx)
        zap.spider.exclude_from_scan(rx)
    print("zap_auth_hook: session header rule added; excluded: " + ", ".join(EXCLUDE))
