#!/usr/bin/env python3
"""check_oss_headers.py: the OSS public door's browser security headers (LCM patch round 1).

deploy/oss/caddy/Caddyfile's `(security_headers)` snippet sets the CSP, X-Frame-Options,
X-Content-Type-Options, Referrer-Policy and HSTS for every response of the public door. This
checks, without Docker:

  H1  the snippet exists and the public-door site imports it;
  H2  each header is set, with the value the Caddyfile's comment argues for;
  H3  neither CSP allows 'unsafe-inline' or 'unsafe-eval' in script-src, and both carry
      frame-ancestors 'none', object-src 'none' and base-uri 'self';
  H4  the legacy board's CSP lists exactly the sha256 of each inline <script> in
      frontend/board.html (computed here, CRLF normalised to LF as the HTML parser does),
      so an edit to that file cannot silently break /board/legacy behind this proxy;
  H5  board v2 (frontend/v2/index.html) has no inline script, which its CSP relies on.

tools/ci/oss_tls_front.sh reads the same headers off a live response.

  python tools/ci/check_oss_headers.py
  python tools/ci/check_oss_headers.py --self-test

Stdlib only. Exit 0 clean, 1 findings or a failed self-test.
"""
from __future__ import annotations

import base64
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = ROOT / "deploy" / "oss" / "caddy" / "Caddyfile"
LEGACY = ROOT / "frontend" / "board.html"
V2 = ROOT / "frontend" / "v2" / "index.html"

INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script\s*>", re.S | re.I)
EXPECT = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000",
}


def inline_hashes(html: str) -> list[str]:
    html = html.replace("\r\n", "\n").replace("\r", "\n")
    return ["sha256-" + base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest()).decode()
            for m in INLINE_SCRIPT.finditer(html)]


def snippet(caddy: str) -> str | None:
    m = re.search(r"^\(security_headers\)\s*\{\n(.*?)^\}", caddy, re.S | re.M)
    return m.group(1) if m else None


def directives(csp: str) -> dict[str, list[str]]:
    out = {}
    for part in csp.split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


def check(caddy: str, legacy_html: str, v2_html: str) -> list[str]:
    bad = []
    body = snippet(caddy)
    if body is None:
        return ["H1 no (security_headers) snippet in deploy/oss/caddy/Caddyfile"]
    door = re.search(r"^\{\$CADDY_PUBLIC_HOST\}:8443\s*\{\n(.*?)^\}", caddy, re.S | re.M)
    if not door or not re.search(r"^\s*import security_headers\s*$", door.group(1), re.M):
        bad.append("H1 the public-door site ({$CADDY_PUBLIC_HOST}:8443) does not import security_headers")
    for name, value in EXPECT.items():
        if not re.search(rf'\b{re.escape(name)}\s+"{re.escape(value)}"', body):
            bad.append(f'H2 {name} "{value}" is not set in (security_headers)')
    csps = dict(re.findall(r'header\s+@(\w+)\s+Content-Security-Policy\s+"([^"]+)"', body))
    if set(csps) != {"board_legacy", "not_board_legacy"}:
        bad.append(f"H2 expected a CSP for @board_legacy and @not_board_legacy, found {sorted(csps)}")
    for which, csp in csps.items():
        d = directives(csp)
        script = d.get("script-src", d.get("default-src", []))
        for word in ("'unsafe-inline'", "'unsafe-eval'"):
            if word in script:
                bad.append(f"H3 {which}: script-src allows {word}")
        for name, want in (("frame-ancestors", ["'none'"]), ("object-src", ["'none'"]),
                           ("base-uri", ["'self'"])):
            if d.get(name) != want:
                bad.append(f"H3 {which}: {name} is {d.get(name)}, expected {want}")
    if "not_board_legacy" in csps:
        extra = [w for w in directives(csps["not_board_legacy"]).get("script-src", []) if w != "'self'"]
        if extra:
            bad.append(f"H3 not_board_legacy: script-src has more than 'self': {extra}")
    if "board_legacy" in csps:
        listed = sorted(w.strip("'") for w in directives(csps["board_legacy"]).get("script-src", [])
                        if w.startswith("'sha256-"))
        want = sorted(inline_hashes(legacy_html))
        if listed != want:
            bad.append(f"H4 the legacy CSP's script hashes {listed} != frontend/board.html's inline "
                       f"scripts {want}: copy the computed ones into the Caddyfile")
    if inline_hashes(v2_html):
        bad.append("H5 frontend/v2/index.html has an inline <script>; its CSP (script-src 'self') blocks it")
    return bad


def self_test() -> int:
    page = "<html><script>a()</script><script src=x></script><script type=module>\r\nb()</script>"
    h = inline_hashes(page)
    good = (
        "(security_headers) {\n"
        '\theader {\n\t\tX-Content-Type-Options "nosniff"\n\t\tX-Frame-Options "DENY"\n'
        '\t\tReferrer-Policy "same-origin"\n\t}\n'
        "\theader @not_board_legacy Content-Security-Policy \"default-src 'self'; script-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'\"\n"
        f"\theader @board_legacy Content-Security-Policy \"script-src 'self' '{h[0]}' '{h[1]}'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'\"\n"
        '\theader @hsts Strict-Transport-Security "max-age=31536000"\n'
        "}\n\n{$CADDY_PUBLIC_HOST}:8443 {\n\timport security_headers\n}\n"
    )
    ok = len(h) == 2 and check(good, page, "<script src=a></script>") == []
    r = check(good, page + "<script>c()</script>", "<script>x</script>")
    ok = ok and any(x.startswith("H4") for x in r) and any(x.startswith("H5") for x in r)
    r = check(good.replace("script-src 'self';", "script-src 'self' 'unsafe-inline';")
              .replace('"DENY"', '"SAMEORIGIN"').replace("\timport security_headers\n", ""), page, "")
    ok = ok and {x[:2] for x in r} == {"H1", "H2", "H3"}
    ok = ok and check("{$CADDY_PUBLIC_HOST}:8443 {\n}\n", page, "")[0].startswith("H1")
    print("self-test", "passed" if ok else f"FAILED {r}")
    return 0 if ok else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    bad = check(CADDYFILE.read_text(encoding="utf-8"), LEGACY.read_text(encoding="utf-8"),
                V2.read_text(encoding="utf-8"))
    for b in bad:
        print(f"::error file=deploy/oss/caddy/Caddyfile::{b}")
    if not bad:
        print("check_oss_headers: the OSS public door sets every security header; legacy script hashes match")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
