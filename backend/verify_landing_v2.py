"""
Verification for TASK-1463: the product website (`frontend/landing-v2/`) behind `ATLAS_LANDING`,
under ADR 0035 as amended by ADR 0049.

  A. THE DEFAULT IS TODAY'S PAGE. With ATLAS_LANDING unset (and with `v1`), GET and HEAD `/`
     serve `frontend/landing.html` byte for byte with exactly today's header set (Cache-Control
     `private, no-cache`, `Vary: Cookie`, no Content-Security-Policy, nothing added);
     `auth.PUBLIC_ROUTES` is the documented default set; no `/site/` route is registered and every
     `/site/` path is gated like any other. A bad value refuses to boot, naming the variable.
  B. v2 SERVES THE WEBSITE. With ATLAS_LANDING=v2, `/` serves `frontend/landing-v2/index.html`
     byte for byte with ADR 0049's CSP, nosniff, `Referrer-Policy: same-origin` and the same cache
     pair; `PUBLIC_ROUTES` gains exactly `landing_site.public_paths()`; each listed file that
     exists answers an anonymous caller 200 with its bytes and media type, one not drawn yet
     answers 404 (not 401); any other `/site/` path, a traversal, a POST and a bad Bearer stay
     refused.
  C. THE PAGE LOADS NOTHING FROM ANYONE ELSE AND RUNS NO INLINE SCRIPT (the website story's
     criterion 2). index.html: no URL off this origin anywhere, every <script> has a same-origin
     src and no body, no inline <style>, no style= attribute, no on*= handler, no javascript: URL,
     no frame/object/embed/form/base/meta refresh, every loaded file is a listed public path, every
     image has alt, width and height, one h1 and no skipped heading level, no noindex.
     landing.css: no @import, no url() off the site. landing.js: no eval, new Function, string
     timer, innerHTML/outerHTML/insertAdjacentHTML/document.write, no request API of any kind, no
     URL, cookie or storage read. Every diagram already drawn: no script, no handler, no
     reference off the file. The CSP string itself has every directive rule 2 names and no
     `'unsafe-*'`, hash or nonce.
  D. THE CONTENT IS IN THE HTML WITHOUT SCRIPT (criterion 3). Every section and subsection
     heading of docs/site/content.md §2-§18, the tagline (the h1), every FAQ question and every
     "What is ...?" explainer are in the static HTML's text; every diagram and screenshot slug
     the other builders fill is referenced (screenshots with their 390 px crop).

No database: each configuration is one child process (settings are read at import) that imports
the app with an unreachable database URL and never calls startup, which is itself evidence that
`/` and `/site/*` touch no table. The signed-in (302) and machine-principal branches of `/` need
a session and a token, so a database; their code is unchanged by this task and
verify_object_graph.py covers them.

Run:
    python verify_landing_v2.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import landing_site  # noqa: E402  (pure data)

SITE = REPO / "frontend" / "landing-v2"
V1 = REPO / "frontend" / "landing.html"
CONTENT = REPO / "docs" / "site" / "content.md"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------------------- #
# Children: one process per configuration
# --------------------------------------------------------------------------- #
BASE_ENV = {k: v for k, v in os.environ.items()
            if k not in ("ATLAS_LANDING", "ATLAS_PUBLIC_DOCS", "ATLAS_TEST_DB_URL")}
BASE_ENV.update({
    # Unreachable on purpose: nothing below may need the database.
    "ATLAS_DB_URL": "postgresql+psycopg2://atlas:unused@127.0.0.1:1/unused",
    "ATLAS_PASSWORD_PEPPER": base64.b64encode(os.urandom(32)).decode(),
    "ATLAS_TOKEN_PEPPER": base64.b64encode(os.urandom(32)).decode(),
    "ATLAS_LOGIN_ORIGINS": "https://testserver",
    "ATLAS_BREACH_CHECK": "bundled",
    "ATLAS_SEED": "none",
    "ATLAS_LEGACY_V0": "0",
    "ATLAS_FRONTEND_DIR": str(REPO / "frontend"),
})

CHILD = r'''
import base64, hashlib, json, sys
cfg = json.loads(sys.stdin.read())
import app, auth, route_table
from fastapi.testclient import TestClient
c = TestClient(app.app, base_url="https://testserver", follow_redirects=False)
out = {"public": sorted(auth.PUBLIC_ROUTES), "req": [],
       "site_routes": sorted({getattr(r, "path", "") for r in route_table.live_routes(app.app)
                              if getattr(r, "path", "").startswith("/site")})}
for method, path, headers in cfg["reqs"]:
    r = c.request(method, path, headers=headers)
    out["req"].append({"method": method, "path": path, "status": r.status_code,
                       "headers": {k.lower(): v for k, v in r.headers.items()},
                       "sha": hashlib.sha256(r.content).hexdigest(), "len": len(r.content)})
print("CHILD " + json.dumps(out))
'''


def child(env: dict, reqs: list) -> dict:
    e = dict(BASE_ENV)
    e.update(env)
    p = subprocess.run([sys.executable, "-c", CHILD], cwd=HERE, env=e, input=json.dumps({"reqs": reqs}),
                       capture_output=True, text=True, timeout=300)
    line = next((ln for ln in p.stdout.splitlines() if ln.startswith("CHILD ")), None)
    if line is None:
        print(f"  child env={env} exited {p.returncode}")
        print("  stderr: " + p.stderr[-2000:].replace("\n", "\n  "))
        return {}
    return json.loads(line[len("CHILD "):])


JSON = {"Accept": "application/json"}
HTML = {"Accept": "text/html"}
DEFAULT_PUBLIC = {"/", "/healthz", "/readyz", "/auth/login", "/auth/password/change", "/auth/denied",
                  "/auth/privacy", "/favicon.ico"}
#: The headers `/` answers a stranger with on master before this task (measured 2026-11-01). The
#: values that vary per file (length, date, etag) are compared to the file, not pinned.
V1_HEADER_NAMES = {"cache-control", "vary", "content-type", "accept-ranges", "content-length",
                   "last-modified", "etag"}

# --------------------------------------------------------------------------- #
print("=" * 78)
print("A. ATLAS_LANDING unset (and v1): `/` is today's page, byte for byte, same headers")
print("=" * 78)
v1_bytes = V1.read_bytes()
site_probe = [["GET", landing_site.SITE_PREFIX + f, JSON] for f in ("landing.css", "landing.js")]
reqs_a = [["GET", "/", HTML], ["HEAD", "/", HTML], ["GET", "/", JSON]] + site_probe + [
    ["GET", "/site/landing.css", HTML]]
runs = {"unset": child({}, reqs_a), "v1": child({"ATLAS_LANDING": "v1"}, reqs_a),
        " V1 ": child({"ATLAS_LANDING": " V1 "}, reqs_a)}
for name, out in runs.items():
    r = out.get("req") or []
    check(f"[{name}] the child ran", len(r) == len(reqs_a))
    if len(r) != len(reqs_a):
        continue
    get, head, getj = r[0], r[1], r[2]
    check(f"[{name}] GET / is 200 and frontend/landing.html byte for byte",
          get["status"] == 200 and get["sha"] == sha(v1_bytes) and get["len"] == len(v1_bytes))
    check(f"[{name}] GET / for a JSON client is the same page", getj["sha"] == sha(v1_bytes))
    for label, x in (("GET", get), ("HEAD", head)):
        h = x["headers"]
        check(f"[{name}] {label} / carries exactly today's header set, nothing added",
              set(h) == V1_HEADER_NAMES, str(sorted(set(h) ^ V1_HEADER_NAMES)))
        check(f"[{name}] {label} / keeps `private, no-cache` + `Vary: Cookie`, text/html, no CSP",
              h.get("cache-control") == "private, no-cache" and h.get("vary") == "Cookie"
              and h.get("content-type") == "text/html; charset=utf-8"
              and "content-security-policy" not in h)
        check(f"[{name}] {label} / content-length is the file's", h.get("content-length") == str(len(v1_bytes)))
    check(f"[{name}] HEAD / sends no body", head["status"] == 200 and head["len"] == 0)
    check(f"[{name}] PUBLIC_ROUTES is the documented default set, nothing under /site/",
          set(out["public"]) == DEFAULT_PUBLIC, str(sorted(set(out["public"]) ^ DEFAULT_PUBLIC)))
    check(f"[{name}] no /site/ route is registered (the route table is unchanged)", out["site_routes"] == [],
          str(out["site_routes"]))
    check(f"[{name}] the site's files are gated like any other path (401, and 302 for a browser)",
          r[3]["status"] == 401 and r[4]["status"] == 401 and r[5]["status"] == 302)
if runs["unset"].get("req") and runs["v1"].get("req"):
    same = all(a["sha"] == b["sha"] and {k: v for k, v in a["headers"].items()}
               == {k: v for k, v in b["headers"].items()}
               for a, b in zip(runs["unset"]["req"][:3], runs["v1"]["req"][:3]))
    check("unset and v1 answer `/` identically, headers and body", same)

bad = subprocess.run([sys.executable, "-c", "import app"], cwd=HERE, capture_output=True, text=True,
                     env={**BASE_ENV, "ATLAS_LANDING": "v3"}, timeout=300)
check("ATLAS_LANDING=v3 refuses to boot, naming the variable",
      bad.returncode != 0 and "ATLAS_LANDING" in bad.stderr)

# --------------------------------------------------------------------------- #
print("=" * 78)
print("B. ATLAS_LANDING=v2: `/` serves the website with ADR 0049's CSP; exact paths public")
print("=" * 78)
REQUIRED_CSP = {  # ADR 0049 rule 2, "at least"
    "default-src": {"'none'"}, "script-src": {"'self'"}, "style-src": {"'self'"},
    "img-src": {"'self'", "data:"}, "font-src": {"'self'"}, "connect-src": {"'none'"},
    "object-src": {"'none'"}, "base-uri": {"'none'"}, "form-action": {"'none'"},
    "frame-ancestors": {"'none'"},
}


def parse_csp(value: str) -> dict:
    out = {}
    for part in value.split(";"):
        bits = part.split()
        if bits:
            out[bits[0].lower()] = set(bits[1:])
    return out


v2_bytes = (SITE / "index.html").read_bytes()
files = list(landing_site.SITE_FILES)
odd = [["GET", "/site/index.html", JSON], ["GET", "/site/not-listed.css", JSON],
       ["GET", "/site/../backend/auth.py", JSON], ["GET", "/site/img/diagrams/", JSON],
       ["POST", "/site/landing.css", JSON],
       ["GET", "/site/landing.css", {"Accept": "application/json", "Authorization": "Bearer not-a-token"}]]
reqs_b = [["GET", "/", HTML], ["HEAD", "/", HTML]] + [["GET", landing_site.SITE_PREFIX + f, JSON]
                                                     for f in files] + [
    ["HEAD", "/site/landing.js", JSON]] + odd
on = child({"ATLAS_LANDING": "v2"}, reqs_b)
r = on.get("req") or []
check("the v2 child ran", len(r) == len(reqs_b))
if len(r) == len(reqs_b):
    for label, x in (("GET", r[0]), ("HEAD", r[1])):
        h = x["headers"]
        csp = parse_csp(h.get("content-security-policy", ""))
        check(f"{label} / is 200 with the cache pair of ADR 0035 (`private, no-cache`, `Vary: Cookie`)",
              x["status"] == 200 and h.get("cache-control") == "private, no-cache" and h.get("vary") == "Cookie")
        check(f"{label} / carries the strict CSP: every directive of ADR 0049 rule 2",
              all(csp.get(d) == v for d, v in REQUIRED_CSP.items()), h.get("content-security-policy", "(none)"))
        check(f"{label} / CSP has no 'unsafe-*', hash or nonce anywhere",
              not re.search(r"unsafe-|'sha(256|384|512)-|'nonce-", h.get("content-security-policy", "x")))
        check(f"{label} / also sends nosniff and Referrer-Policy: same-origin",
              h.get("x-content-type-options") == "nosniff" and h.get("referrer-policy") == "same-origin")
    check("GET / is frontend/landing-v2/index.html byte for byte, text/html",
          r[0]["sha"] == sha(v2_bytes) and r[0]["headers"].get("content-type", "").startswith("text/html"))
    check("HEAD / sends no body", r[1]["len"] == 0)
    added = set(on["public"]) - DEFAULT_PUBLIC
    check("PUBLIC_ROUTES gains exactly landing_site.public_paths(), and keeps the default set",
          added == set(landing_site.public_paths()) and DEFAULT_PUBLIC <= set(on["public"]),
          str(sorted(added ^ set(landing_site.public_paths())))[:300])
    check("no public path is a prefix or a directory", all(not p.endswith("/") for p in added))
    served = r[2:2 + len(files)]
    missing = []
    for f, x in zip(files, served):
        disk = SITE / f
        if disk.is_file():
            ext = os.path.splitext(f)[1]
            ok = (x["status"] == 200 and x["sha"] == sha(disk.read_bytes())
                  and x["headers"].get("content-type") == landing_site.MEDIA_TYPES[ext]
                  and x["headers"].get("cache-control") == "private, no-cache"
                  and x["headers"].get("x-content-type-options") == "nosniff")
            if ext == ".svg":
                svg_csp = parse_csp(x["headers"].get("content-security-policy", ""))
                ok = ok and svg_csp.get("default-src") == {"'none'"} and "script-src" not in svg_csp
            check(f"/site/{f}: an anonymous caller gets 200, its bytes, its media type and headers", ok,
                  f"{x['status']} {x['headers'].get('content-type')}")
        else:
            missing.append(f)
            check(f"/site/{f} (not drawn yet): 404, not 401, so the page degrades to its caption",
                  x["status"] == 404)
    print(f"  note: {len(missing)} listed file(s) not on disk yet: {', '.join(missing) or 'none'}")
    hd = r[2 + len(files)]
    check("HEAD /site/landing.js is 200 with no body", hd["status"] == 200 and hd["len"] == 0)
    rest = {f"{m} {p}" + (" bearer" if "Authorization" in hh else ""): x["status"]
            for (m, p, hh), x in zip(odd, r[3 + len(files):])}
    check("an unlisted /site/ path stays gated (401), index.html is not served twice",
          rest.get("GET /site/index.html") == 401 and rest.get("GET /site/not-listed.css") == 401
          and rest.get("GET /site/img/diagrams/") == 401, str(rest))
    check("a traversal is refused (401 or 404)", rest.get("GET /site/../backend/auth.py") in (401, 404), str(rest))
    # A bad Bearer is resolved against the token table, which this run has no database for: 401
    # with one (verify_system_node.py proves that shape for /docs), 503 without. Never 200.
    check("a POST is refused (401 or 405), and a bad Bearer on a public file is not served",
          rest.get("POST /site/landing.css") in (401, 405)
          and rest.get("GET /site/landing.css bearer") in (401, 503),
          str(rest))
    check("the /site/ route is registered under v2", on["site_routes"] != [], str(on["site_routes"]))

# --------------------------------------------------------------------------- #
print("=" * 78)
print("C. no third party, no inline script: index.html, landing.css, landing.js, the diagrams")
print("=" * 78)
import ast  # noqa: E402

#: Read from the source, not imported: importing the router pulls in the app's boot chain.
PAGE_CSP = next(ast.literal_eval(n.value) for n in ast.parse(
    (HERE / "routers" / "landing.py").read_text(encoding="utf-8")).body
    if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "PAGE_CSP" for t in n.targets))
csp = parse_csp(PAGE_CSP)
check("routers/landing.PAGE_CSP has every directive of ADR 0049 rule 2",
      all(csp.get(d) == v for d, v in REQUIRED_CSP.items()), PAGE_CSP)
check("PAGE_CSP has no 'unsafe-*', hash or nonce", not re.search(r"unsafe-|'sha\d+-|'nonce-", PAGE_CSP))

#: An absolute or protocol-relative URL with a host. A bare `https://` in prose ("one published
#: https:// port") names a scheme, not a place, and is allowed.
OFF_ORIGIN = re.compile(r"(?i)\b(?:https?|ftp|wss?)://[\w.-]|(?<![:\w/])//[\w-]+(?:\.[\w-]+)+")
ALLOWED_LINKS = {"/", "/auth/login", "/auth/privacy", "/docs/"}
PUBLIC = landing_site.public_paths() | {"/favicon.ico"}
URL_ATTRS = {"href", "src", "srcset", "poster", "action", "data", "formaction", "xlink:href", "background",
             "cite", "longdesc", "manifest", "ping"}
BANNED_TAGS = {"iframe", "frame", "object", "embed", "form", "base", "applet", "portal", "style"}


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []
        self.loads: list[str] = []        # files the browser fetches
        self.links: list[str] = []        # navigation targets
        self.imgs: list[dict] = []
        self.sources: list[dict] = []
        self.headings: list[tuple[int, str]] = []
        self.ids: set[str] = set()
        self.text: list[str] = []
        self._h: list = []
        self._script = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if "id" in a:
            self.ids.add(a["id"])
        for k, v in a.items():
            if k.startswith("on"):
                self.problems.append(f"<{tag} {k}=...> inline handler")
            if k == "style":
                self.problems.append(f"<{tag} style=...> inline style")
            if k in URL_ATTRS and re.match(r"\s*(javascript|vbscript|data):", v, re.I):
                self.problems.append(f"<{tag} {k}={v[:30]!r}> script or data URL")
            if k == "hidden":
                self.problems.append(f"<{tag} hidden> content hidden without script")
        if tag in BANNED_TAGS:
            self.problems.append(f"<{tag}> is not allowed on this page")
        if tag == "meta" and a.get("http-equiv", "").lower() == "refresh":
            self.problems.append("<meta http-equiv=refresh>")
        if tag == "meta" and "noindex" in a.get("content", "").lower():
            self.problems.append("noindex")
        if tag == "script":
            if not a.get("src"):
                self.problems.append("<script> without src (inline script)")
            else:
                self.loads.append(a["src"])
            self._script = True
        if tag == "link":
            if a.get("href"):
                self.loads.append(a["href"])
        if tag == "img":
            self.imgs.append(a)
            if a.get("src"):
                self.loads.append(a["src"])
        if tag == "source":
            self.sources.append(a)
            for part in a.get("srcset", "").split(","):
                if part.strip():
                    self.loads.append(part.strip().split()[0])
        if tag == "a" and "href" in a:
            self.links.append(a["href"])
        if re.fullmatch(r"h[1-6]", tag):
            self._h.append([int(tag[1]), ""])

    def handle_endtag(self, tag):
        if tag == "script":
            self._script = False
        if re.fullmatch(r"h[1-6]", tag) and self._h:
            lvl, txt = self._h.pop()
            self.headings.append((lvl, " ".join(txt.split())))

    def handle_data(self, data):
        if self._script and data.strip():
            self.problems.append("<script> with a body (inline script)")
        for h in self._h:
            h[1] += data
        self.text.append(data)


html_text = v2_bytes.decode("utf-8")
page = Page()
page.feed(html_text)
check("index.html: no inline script, inline style, handler, script URL, banned element or noindex",
      not page.problems, "; ".join(page.problems[:8]))
check("index.html: no URL off this origin anywhere in the file (comments included)",
      not OFF_ORIGIN.search(html_text), str(OFF_ORIGIN.findall(html_text)[:5]))
bad_loads = [u for u in page.loads if u not in PUBLIC]
check("every file the page loads is a listed public path on this origin (or /favicon.ico)",
      not bad_loads and page.loads, str(bad_loads))
check("it loads exactly one script, /site/landing.js, and one stylesheet, /site/landing.css",
      [u for u in page.loads if u.endswith(".js")] == ["/site/landing.js"]
      and [u for u in page.loads if u.endswith(".css")] == ["/site/landing.css"])
bad_links = [u for u in page.links if not (u.startswith("#") and u[1:] in page.ids) and u not in ALLOWED_LINKS]
check("every link is an in-page anchor that exists, or one of / /auth/login /auth/privacy /docs/",
      not bad_links, str(bad_links))
check("every image has a non-empty alt, a width and a height (no layout jump, no silent picture)",
      page.imgs and all(i.get("alt", "").strip() and i.get("width", "").isdigit() and i.get("height", "").isdigit()
                        for i in page.imgs))
check("every <source> has a width and a height too",
      all(s.get("width", "").isdigit() and s.get("height", "").isdigit() for s in page.sources))
levels = [lvl for lvl, _ in page.headings]
check("exactly one h1, and it comes first", levels.count(1) == 1 and levels[0] == 1)
check("no heading skips a level going down", all(b <= a + 1 for a, b in zip(levels, levels[1:])), str(levels))
check('<html lang="en">', '<html lang="en">' in html_text)

css = (SITE / "landing.css").read_text(encoding="utf-8")
css_urls = re.findall(r"url\(\s*['\"]?([^'\")]+)", css)
check("landing.css: no @import, and no url() at all except a listed site file or data:",
      "@import" not in css and all(u.startswith("data:") or u in PUBLIC for u in css_urls), str(css_urls))
check("landing.css: no URL off this origin", not OFF_ORIGIN.search(css), str(OFF_ORIGIN.findall(css)[:5]))
check("landing.css: no expression() or behavior: (old script-in-CSS)", not re.search(r"expression\(|(?<![\w-])behavior\s*:|-moz-binding", css, re.I))


def strip_js_comments(src: str) -> str:
    """Drops /* */ and // comments outside string literals (the file uses no regex literals)."""
    out, i, n, q = [], 0, len(src), None
    while i < n:
        c = src[i]
        if q:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1]); i += 2; continue
            if c == q:
                q = None
            i += 1
        elif c in "\"'`":
            q = c; out.append(c); i += 1
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2); i = n if j < 0 else j + 2
        elif src.startswith("//", i):
            j = src.find("\n", i); i = n if j < 0 else j
        else:
            out.append(c); i += 1
    return "".join(out)


js = strip_js_comments((SITE / "landing.js").read_text(encoding="utf-8"))
JS_BANNED = {
    "eval": r"\beval\s*\(", "new Function": r"\bnew\s+Function\b|\bFunction\s*\(",
    "a string timer": r"set(?:Timeout|Interval)\s*\(\s*['\"`]",
    "innerHTML/outerHTML/insertAdjacentHTML": r"\b(?:inner|outer)HTML\b|insertAdjacentHTML",
    "document.write": r"document\.write", "fetch": r"\bfetch\s*\(", "XMLHttpRequest": r"XMLHttpRequest",
    "sendBeacon": r"sendBeacon", "WebSocket": r"WebSocket", "EventSource": r"EventSource",
    "dynamic import()": r"\bimport\s*\(", "a static import": r"^\s*import\s", "a worker": r"\bWorker\s*\(",
    "storage": r"localStorage|sessionStorage|indexedDB", "cookies": r"document\.cookie",
    "the URL": r"location\.(?:search|hash|href|pathname)|URLSearchParams|document\.referrer|window\.name",
    "setAttribute('style')": r"setAttribute\(\s*['\"]style", "an <a> or <img> built in script":
        r"createElement\(\s*['\"](?:a|img|script|iframe|link|style)['\"]",
}
hits = [k for k, rx in JS_BANNED.items() if re.search(rx, js, re.M)]
check("landing.js: none of eval, new Function, string timers, HTML injection, requests, storage, "
      "cookies or URL reads", not hits, str(hits))
check("landing.js: no URL off this origin", not OFF_ORIGIN.search(js), str(OFF_ORIGIN.findall(js)[:5]))
node = subprocess.run(["node", "--check", str(SITE / "landing.js")], capture_output=True, text=True) \
    if any((Path(p) / ("node.exe" if os.name == "nt" else "node")).is_file()
           for p in os.environ.get("PATH", "").split(os.pathsep)) else None
check("landing.js parses (node --check)", node is not None and node.returncode == 0,
      "node not found" if node is None else node.stderr[-300:])

svgs = sorted((SITE / "img" / "diagrams").glob("*.svg")) if (SITE / "img" / "diagrams").is_dir() else []
for svg in svgs:
    t = svg.read_text(encoding="utf-8", errors="replace")
    body = re.sub(r"\sxmlns(?::\w+)?\s*=\s*(['\"])[^'\"]*\1", "", t)  # namespace names are not requests
    body = re.sub(r"<!DOCTYPE[^>]*>", "", body, flags=re.I)
    refs = re.findall(r"(?:href|src)\s*=\s*['\"]([^'\"]*)", body) + re.findall(r"url\(\s*['\"]?([^'\")]+)", body)
    probs = []
    if re.search(r"<script\b", body, re.I):
        probs.append("<script>")
    if re.search(r"\son\w+\s*=", body, re.I):
        probs.append("on*= handler")
    if re.search(r"<foreignObject\b", body, re.I) and re.search(r"<(iframe|object|embed)\b", body, re.I):
        probs.append("embedded document")
    probs += [f"ref {u!r}" for u in refs if not (u.startswith("#") or u.startswith("data:image/"))]
    if OFF_ORIGIN.search(body):
        probs.append("off-origin URL " + OFF_ORIGIN.search(body).group(0))
    check(f"diagram {svg.name}: no script, no handler, no reference outside the file", not probs, "; ".join(probs))
print(f"  note: {len(svgs)} diagram(s) on disk checked")
# A diagram's frame is its own viewBox: the img/source width and height reserve exactly the
# drawing's shape, so the text below never moves when it arrives.
frames = {}
for a in page.imgs + page.sources:
    url = a.get("src") or a.get("srcset", "").split()[0] if (a.get("src") or a.get("srcset")) else ""
    if url.startswith("/site/img/diagrams/"):
        frames[url] = (a.get("width"), a.get("height"))
mismatch = []
for svg in svgs:
    vb = re.search(r'viewBox\s*=\s*"\s*[\d.]+\s+[\d.]+\s+([\d.]+)\s+([\d.]+)', svg.read_text(encoding="utf-8"))
    want = (str(int(float(vb.group(1)))), str(int(float(vb.group(2))))) if vb else None
    got = frames.get("/site/img/diagrams/" + svg.name)
    if got is not None and got != want:
        mismatch.append(f"{svg.name}: page {got}, viewBox {want}")
check("each drawn diagram's width and height on the page are its viewBox",
      not mismatch and len(frames) == len(landing_site.DIAGRAMS), "; ".join(mismatch) or str(len(frames)))

# --------------------------------------------------------------------------- #
print("=" * 78)
print("D. the content is in the HTML without script (docs/site/content.md)")
print("=" * 78)


def norm(s: str) -> str:
    s = s.replace("**", "").replace("`", "").replace("’", "'").replace("“", '"').replace("”", '"')
    return " ".join(s.split()).strip()


page_text = norm(" ".join(page.text))
if not CONTENT.exists():
    # The site drafts are not in every tree; the page checks below need none of them.
    print("SKIP the content.md headings, tagline, FAQ and explainers are in the HTML: "
          "docs/site/content.md is not in this tree")
else:
    md = CONTENT.read_text(encoding="utf-8")
    body_md = md.split("\n## 19.", 1)[0]
    heads = []
    for m in re.finditer(r"^(##|###) (.+)$", body_md, re.M):
        title = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", m.group(2).strip())
        if title != "Hero":
            heads.append(norm(title))
    html_heads = {norm(t) for _, t in page.headings}
    missing_heads = [h for h in heads if h not in html_heads]
    check("every section and subsection heading of content.md §2-§18 is a heading in the HTML",
          heads and not missing_heads, str(missing_heads))
    print(f"  ({len(heads)} headings compared)")
    tag_m = re.search(r"^> \*\*(A plan .+?)\*\*$", md, re.M)
    h1 = [t for lvl, t in page.headings if lvl == 1]
    check("the tagline (variant A) is the h1", bool(tag_m) and h1 == [norm(tag_m.group(1))], str(h1))
    faq = re.findall(r"^\*\*(.+\?)\*\*$", body_md.split("## 18. FAQ", 1)[-1], re.M)
    missing_faq = [q for q in faq if norm(q) not in html_heads]
    check("every FAQ question is a heading in the HTML", faq and not missing_faq, str(missing_faq))
    explainers = re.findall(r"^> \*\*(What (?:is|are) [^*]+\?)\*\*", md, re.M)
    missing_ex = [e for e in explainers if norm(e) not in page_text]
    check("every 'What is ...?' explainer is in the page text", explainers and not missing_ex, str(missing_ex))
for phrase in ("A plan your agents work from.", "Atlas is where a team and its AI agents plan and track the work together",
               "Everything in this section is not built yet", "This design is agreed but not built yet"):
    check(f"the static text says: {phrase[:60]!r}", norm(phrase) in page_text)
check("no copyright line", not re.search(r"(?i)copyright|©|&copy;", html_text))
loads = set(page.loads)
miss_d = [s for s in landing_site.DIAGRAMS if f"/site/img/diagrams/{s}.svg" not in loads]
miss_s = [s for s in landing_site.SHOTS
          if f"/site/img/shots/{s}.png" not in loads or f"/site/img/shots/{s}-390.png" not in loads]
check("every diagram slug is on the page", not miss_d, str(miss_d))
check("every screenshot slug is on the page, with its 390 px crop", not miss_s, str(miss_s))
check("each picture sits in a <figure> with a caption (a missing file keeps its caption)",
      html_text.count("<figure") == html_text.count("<figcaption>")
      and html_text.count("<figure") >= len(landing_site.DIAGRAMS) + len(landing_site.SHOTS))

# --------------------------------------------------------------------------- #
print("=" * 78)
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("ALL PASSED")
