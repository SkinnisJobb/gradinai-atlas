"""
Verification for STORY-oss-06-neutral-settings: one typed settings object, with
derived values, and today's values kept when nothing new is configured.

`settings.load()` is the reader; `auth` takes its origins, bearer hosts,
internal hosts and privacy contact from it, and `db` its DSN. The claims:

  A. TODAY'S VALUES, UNCHANGED (no database). The pre-change parsers are frozen
     below as an oracle (`_old_load_origins`, `_old_hosts_from_env`, the old
     privacy literal), copied from `auth.py` as it stood before this story. For
     every origin/bearer list the deploy overrides set today (read from
     `deploy/*.override.yml`, not retyped) plus the suite's own, the new path
     must yield exactly the oracle's origins, Host map and bearer set; internal
     hosts are empty; the privacy contact and DB URL are what they were; and
     with neither ATLAS_LOGIN_ORIGINS nor ATLAS_PUBLIC_URL set auth still
     refuses to boot. The oracle is perturbed once to prove it can notice.
  B. DERIVATION AND PARSING (no database). ATLAS_PUBLIC_URL alone derives the
     origins and the bearer hosts; an explicit ATLAS_LOGIN_ORIGINS or
     ATLAS_BEARER_HOSTS wins over it; a malformed public URL or internal host
     fails closed naming the variable; ATLAS_PRIVACY_CONTACT overrides; the
     internal-host matcher's truth table.
  C. THROUGH THE REAL GATE (a disposable Postgres; one child process per
     configuration, because auth reads its settings at import):
       default      today's config: login + CSRF work; a Bearer request to
                    http://atlas-api:8000 gets 421; the privacy page shows the
                    default mailbox.
       public_url   ONLY ATLAS_PUBLIC_URL set: login works, a CSRF-protected
                    write succeeds with the token and is refused without it or
                    from a foreign Origin, Bearer works on the public host, and
                    any other host is 421.
       internal     ATLAS_INTERNAL_HOSTS=atlas-api (with a NARROW
                    ATLAS_BEARER_HOSTS): a Bearer read and write to
                    http://atlas-api:8000 succeed; a cookie request, an
                    anonymous request, the login page, cookie+Bearer and a
                    non-Bearer Authorization to that host all get 421; a
                    look-alike host is still 421.
       privacy      ATLAS_PRIVACY_CONTACT set: the privacy page shows it, and
                    not the default.
  D. THE DISTRIBUTION SEAM (no database; STORY-oss-23 A1). Every estate-valued
     default `settings` exposes (seed profile and profiles, the v0 switch, privacy
     contact and hosting, deploy hint host and commands) IS `distribution.py`'s
     value, and `load({})` / `load_deploy_hints({})` yield them. Then the neutral
     copy the export overlays (`tools/carve/overlay/backend/distribution.py`) is put
     in front of `backend/` in a child process: `load({})` gives seed `none`, v0
     off, an example.com mailbox, an example.test deploy host whose unset commands
     answer NO_DEPLOY_HINT, and refuses `ATLAS_SEED=homelab`.
     (`verify_fresh_install.py` §G boots the whole app under it.)

The mailbox oracle is pinned by its sha256, so the homelab literal lives only in
`distribution.py`. It applies where `distribution.NAME` is `homelab`; under the
neutral copy (the public export) §A says so and compares against that copy instead.
The deploy hint host is pinned the same way, and §B's "no homelab value" check
compares the sha256 of each word against `_HOMELAB_WORD_SHA256`, so no homelab literal
is spelled out in this file.

Run (from a venv with the backend requirements installed):
    python verify_settings.py
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FAILURES: list[str] = []

#: Every configuration variable this story reads or derives. Cleared from each
#: child's environment so the caller's own shell cannot leak into a claim.
CONFIG_VARS = (
    "ATLAS_PUBLIC_URL", "ATLAS_LOGIN_ORIGINS", "ATLAS_BEARER_HOSTS",
    "ATLAS_INTERNAL_HOSTS", "ATLAS_PRIVACY_CONTACT", "ATLAS_PRODUCT_NAME",
    "ATLAS_MACHINE_GRACE_HOSTS",
)

TEST_ORIGIN = "https://testserver"
PUBLIC_URL = "https://atlas.example.test"
INTERNAL_BASE = "http://atlas-api:8000"
PW = "Correct-Horse-Battery-Settings-9!"


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def _pepper() -> str:
    return base64.b64encode(os.urandom(32)).decode()


# --------------------------------------------------------------------------- #
# The ORACLE: auth.py's parsing as it stood before STORY-oss-06, verbatim but
# for reading `raw` as an argument instead of from os.environ. Do not "fix" it
# to match the new code — its whole value is that it is the old code.
# --------------------------------------------------------------------------- #
import distribution  # noqa: E402  (pure data, imports nothing)

#: sha256 of the mailbox literal auth.py held before this story. A hash, not the literal, so
#: the homelab value is written in distribution.py alone; it pins the value just as exactly.
_OLD_PRIVACY_CONTACT_SHA256 = "4bb71dc9bfd6d950470ee7d51ab6228267c670293784f62cfe26b1d819ddd507"
#: Is this the homelab distribution, where "today's values" are the oracle's?
HOMELAB = distribution.NAME == "homelab"
#: The default mailbox this run expects: distribution's, which §A ties to the oracle hash.
_OLD_PRIVACY_CONTACT = distribution.DEFAULT_PRIVACY_CONTACT


def _sha256(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


#: sha256 of the homelab deploy hint host (A4c). Pinned as a hash for the same reason as
#: the mailbox: the literal lives in distribution.py alone.
_OLD_DEPLOY_HINT_HOST_SHA256 = "a17a65634b8748a893f0f605efc5bb1ca798817b03aae8030903a60f9fd58223"
#: sha256 of each lowercase word that names the homelab estate (its owner, domain and
#: host). §B tokenises the neutral copy's values and fails on any word whose hash is
#: here, so the check reads no homelab literal and the export ships none.
_HOMELAB_WORD_SHA256 = frozenset({
    "d9a31550033ee07d6e14302eea8202c07c266b633154513d817ca8bb91de40d1",
    "7a84143d54b59fe2186d394f66fa59b5b81e12c8edf9cbe71cece88d9388ff45",
    "97dc14c9c2dcff7c603f770ee90c83b01c745000b100c8645509c2acbcf4c387",
    "a17a65634b8748a893f0f605efc5bb1ca798817b03aae8030903a60f9fd58223",
})


def _homelab_words(text: str) -> set[str]:
    """The words of `text` (lowercased; a hyphenated run and each of its parts) whose
    sha256 is in `_HOMELAB_WORD_SHA256`. Empty means the text names none of them."""
    words: set[str] = set()
    for run in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower()):
        words.add(run)
        words.update(run.split("-"))
    return {w for w in words if _sha256(w) in _HOMELAB_WORD_SHA256}


def _old_load_origins(raw: str) -> tuple[tuple[str, ...], dict[str, str]]:
    raw = raw.strip()
    if not raw:
        raise RuntimeError("not set")
    origins: list[str] = []
    hosts: dict[str, str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip().rstrip("/")
        if not chunk:
            continue
        parts = urlsplit(chunk)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise RuntimeError("bad entry %r" % chunk)
        origin = "%s://%s" % (parts.scheme.lower(), parts.netloc.lower())
        origins.append(origin)
        hosts[parts.netloc.lower()] = origin
        host_only, _, port = parts.netloc.lower().rpartition(":")
        if host_only and port.isdigit():
            if (parts.scheme == "https" and port == "443") or (parts.scheme == "http" and port == "80"):
                hosts.setdefault(host_only, origin)
        else:
            default_port = "443" if parts.scheme == "https" else "80"
            hosts.setdefault("%s:%s" % (parts.netloc.lower(), default_port), origin)
    if not origins:
        raise RuntimeError("parsed to nothing")
    return tuple(dict.fromkeys(origins)), hosts


def _old_hosts_from_env(raw: str) -> frozenset[str]:
    return frozenset(chunk.strip().lower() for chunk in raw.split(",") if chunk.strip())


def _compose_defaults(var: str) -> dict[str, str]:
    """`${VAR:-default}` for `var` in every deploy override, by file name."""
    pat = re.compile(r"\b%s:\s*\$\{%s:-([^}]*)\}" % (var, var))
    found: dict[str, str] = {}
    for path in sorted((HERE.parent / "deploy").glob("*.override.yml")):
        for m in pat.finditer(path.read_text(encoding="utf-8")):
            found[path.name] = m.group(1).strip()
    return found


# --------------------------------------------------------------------------- #
# A. today's values, unchanged
# --------------------------------------------------------------------------- #
def section_a() -> None:
    print("\n-- A. with no new configuration every value is today's --")
    import auth
    import settings

    origin_lists = _compose_defaults("ATLAS_LOGIN_ORIGINS")
    bearer_lists = _compose_defaults("ATLAS_BEARER_HOSTS")
    # The homelab's deploy/*.override.yml are not exported (STORY-oss-23): with none in the
    # tree there is no corpus to read, so say so and go on with the suite's own lists. With
    # any present (this repo) the corpus check is exactly as strict as before.
    if not list((HERE.parent / "deploy").glob("*.override.yml")):
        print("SKIP the deploy-override origin/bearer corpus: deploy/*.override.yml is not in this tree")
    else:
        check("the deploy overrides were read (no silent empty corpus)",
              len(origin_lists) >= 3 and len(bearer_lists) >= 2,
              f"origins from {sorted(origin_lists)}, bearer from {sorted(bearer_lists)}")
    origin_lists = dict(origin_lists, suite=TEST_ORIGIN,
                        messy=" HTTPS://Atlas.Example.TEST:443/ ,http://h:80,, http://h2 ")

    for name, raw in origin_lists.items():
        cfg = settings.load({"ATLAS_LOGIN_ORIGINS": raw})
        new = auth._load_origins(cfg)
        old = _old_load_origins(raw)
        check(f"{name}: origins and Host map equal the pre-change parser's", new == old,
              f"{new[0]}")
        check(f"{name}: no bearer hosts, no internal hosts, no public URL derived",
              cfg.bearer_hosts == frozenset() and cfg.internal_hosts == frozenset()
              and cfg.public_url is None)

    for name, raw in dict(bearer_lists, unset="", messy=" A:1 ,,b ").items():
        env = {"ATLAS_LOGIN_ORIGINS": TEST_ORIGIN}
        if raw:
            env["ATLAS_BEARER_HOSTS"] = raw
        got = settings.load(env).bearer_hosts
        check(f"{name}: bearer hosts equal the pre-change parser's",
              got == _old_hosts_from_env(raw), ",".join(sorted(got)) or "<every allowed host>")

    cfg = settings.load({"ATLAS_LOGIN_ORIGINS": TEST_ORIGIN})
    if HOMELAB:
        check("privacy contact is today's literal (the oracle's sha256)",
              _sha256(cfg.privacy_contact) == _OLD_PRIVACY_CONTACT_SHA256, cfg.privacy_contact)
    else:
        print(f"   (distribution.py is {distribution.NAME!r}, not the homelab copy: the mailbox is "
              "checked against that copy, not the homelab oracle)")
    check("privacy contact is distribution.py's", cfg.privacy_contact == _OLD_PRIVACY_CONTACT,
          cfg.privacy_contact)
    if HOMELAB:
        check("deploy hint host is today's literal (its sha256)",
              _sha256(distribution.DEFAULT_DEPLOY_HINT_HOST) == _OLD_DEPLOY_HINT_HOST_SHA256,
              distribution.DEFAULT_DEPLOY_HINT_HOST)
        homelab_blob = json.dumps({n: v for n, v in vars(distribution).items() if n.isupper()})
        check("the homelab-word hashes are not vacuous: the homelab copy's values contain words they match",
              len(_homelab_words(homelab_blob)) >= 3, str(len(_homelab_words(homelab_blob))))
    check("product name defaults to Atlas", cfg.product_name == "Atlas")
    for raw in (None, "", " postgresql+psycopg2://u:p@h/d "):
        env = {} if raw is None else {"ATLAS_DB_URL": raw}
        check(f"DB URL is os.environ.get('ATLAS_DB_URL') exactly ({raw!r})",
              settings.load(env).db_url == env.get("ATLAS_DB_URL"))

    # The module constants this process actually booted with (suite config).
    check("auth module: ALLOWED_ORIGINS/ALLOWED_HOSTS as before",
          (auth.ALLOWED_ORIGINS, auth.ALLOWED_HOSTS) == _old_load_origins(os.environ["ATLAS_LOGIN_ORIGINS"]))
    check("auth module: BEARER_HOSTS as before",
          auth.BEARER_HOSTS == _old_hosts_from_env(os.environ.get("ATLAS_BEARER_HOSTS", "")))
    check("auth module: INTERNAL_HOSTS empty", auth.INTERNAL_HOSTS == frozenset())
    check("auth module: PRIVACY_CONTACT as before", auth.PRIVACY_CONTACT == _OLD_PRIVACY_CONTACT)

    try:
        auth._load_origins(settings.load({}))
        refused, msg = False, ""
    except auth.MissingSecretError as exc:
        refused, msg = True, str(exc)
    check("neither ATLAS_LOGIN_ORIGINS nor ATLAS_PUBLIC_URL: still refuses to boot",
          refused and "ATLAS_LOGIN_ORIGINS" in msg)

    # A check that cannot fail proves nothing: the comparison notices one char.
    check("the oracle comparison notices a one-character perturbation",
          auth._load_origins(settings.load({"ATLAS_LOGIN_ORIGINS": TEST_ORIGIN}))
          != _old_load_origins("https://testservex"))


# --------------------------------------------------------------------------- #
# B. derivation and parsing
# --------------------------------------------------------------------------- #
def section_b() -> None:
    print("\n-- B. ATLAS_PUBLIC_URL derivation, overrides, internal hosts --")
    import auth
    import settings

    cfg = settings.load({"ATLAS_PUBLIC_URL": "https://Atlas.Example.TEST/"})
    check("public URL is normalised", cfg.public_url == PUBLIC_URL, str(cfg.public_url))
    origins, hosts = auth._load_origins(cfg)
    check("only ATLAS_PUBLIC_URL: origins derived from it", origins == (PUBLIC_URL,), str(origins))
    check("...and the Host map is that origin's two forms",
          set(hosts) == {"atlas.example.test", "atlas.example.test:443"}, str(sorted(hosts)))
    check("...and the bearer hosts are the same two forms",
          cfg.bearer_hosts == frozenset({"atlas.example.test", "atlas.example.test:443"}),
          str(sorted(cfg.bearer_hosts)))
    cfg = settings.load({"ATLAS_PUBLIC_URL": "http://atlas.example.test:8095"})
    check("a non-default port derives only that port",
          cfg.bearer_hosts == frozenset({"atlas.example.test:8095"}), str(sorted(cfg.bearer_hosts)))

    cfg = settings.load({"ATLAS_PUBLIC_URL": PUBLIC_URL, "ATLAS_LOGIN_ORIGINS": TEST_ORIGIN,
                         "ATLAS_BEARER_HOSTS": "lan:8443"})
    check("explicit ATLAS_LOGIN_ORIGINS wins over the derived list",
          auth._load_origins(cfg)[0] == (TEST_ORIGIN,))
    check("explicit ATLAS_BEARER_HOSTS wins over the derived list",
          cfg.bearer_hosts == frozenset({"lan:8443"}))

    for bad in ("atlas.example.test", "ftp://x", "https://x/board", "https://x?a=1", "https://"):
        try:
            settings.load({"ATLAS_PUBLIC_URL": bad})
            ok, msg = False, ""
        except settings.SettingsError as exc:
            ok, msg = True, str(exc)
        check(f"malformed ATLAS_PUBLIC_URL {bad!r} fails naming the variable",
              ok and "ATLAS_PUBLIC_URL" in msg)
    for bad in ("http://atlas-api", "*", "atlas-api/x"):
        try:
            settings.load({"ATLAS_INTERNAL_HOSTS": bad})
            ok, msg = False, ""
        except settings.SettingsError as exc:
            ok, msg = True, str(exc)
        check(f"malformed ATLAS_INTERNAL_HOSTS {bad!r} fails naming the variable",
              ok and "ATLAS_INTERNAL_HOSTS" in msg)

    cfg = settings.load({"ATLAS_PRIVACY_CONTACT": " privacy@example.test "})
    check("ATLAS_PRIVACY_CONTACT overrides the mailbox", cfg.privacy_contact == "privacy@example.test")

    cfg = settings.load({"ATLAS_INTERNAL_HOSTS": "Atlas-API, mcp-api:9000"})
    table = {
        "atlas-api": True, "atlas-api:8000": True, "ATLAS-API:1": True,
        "mcp-api:9000": True, "mcp-api:9001": False, "mcp-api": False,
        "atlas-api.evil": False, "evil-atlas-api:8000": False, "": False,
    }
    for host, want in table.items():
        check(f"is_internal_host({host!r}) is {want}", cfg.is_internal_host(host) is want)
    check("unset ATLAS_INTERNAL_HOSTS matches nothing",
          settings.load({}).is_internal_host("atlas-api:8000") is False)


# --------------------------------------------------------------------------- #
# D. the distribution seam (STORY-oss-23 A1)
# --------------------------------------------------------------------------- #
#: settings name -> distribution name. Every estate-valued default settings exposes.
SEAM = {
    "SEED_PROFILES": "SEED_PROFILES",
    "DEFAULT_SEED_PROFILE": "DEFAULT_SEED_PROFILE",
    "DEFAULT_LEGACY_V0": "DEFAULT_LEGACY_V0",
    "DEFAULT_PRIVACY_CONTACT": "DEFAULT_PRIVACY_CONTACT",
    "DEFAULT_PRIVACY_HOSTING": "DEFAULT_PRIVACY_HOSTING",
    "DEFAULT_DEPLOY_HINT_HOST": "DEFAULT_DEPLOY_HINT_HOST",
    "DEFAULT_DEPLOY_HINTS": "DEFAULT_DEPLOY_HINTS",
}
OVERLAY = HERE.parent / "tools" / "carve" / "overlay" / "backend"

#: Run in a child with the neutral copy first on sys.path; prints one JSON line.
_NEUTRAL_CHILD = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import distribution, settings
cfg = settings.load({})
try:
    settings.load({"ATLAS_SEED": "homelab"})
    refused = ""
except settings.SettingsError as exc:
    refused = str(exc)
hints = settings.load_deploy_hints({})
expected = sorted(settings.NO_DEPLOY_HINT.format(name=v) for _t, _c, v in settings.DEPLOY_HINT_ENVS)
print("SEAM " + json.dumps({"file": distribution.__file__, "name": distribution.NAME, "seed": cfg.seed,
                            "legacy_v0": cfg.legacy_v0, "contact": cfg.privacy_contact,
                            "hosting": cfg.privacy_hosting, "host": hints.host,
                            "commands": sorted(hints.rebuild.values()) + sorted(hints.pull_and_rebuild.values()),
                            "no_default": sorted(list(hints.rebuild.values()) + list(hints.pull_and_rebuild.values()))
                            == expected,
                            "refused": refused}))
"""


def section_d() -> None:
    print("\n-- D. the estate-valued defaults are read from distribution.py --")
    import json

    import settings

    for name, dname in SEAM.items():
        check(f"settings.{name} is distribution.{dname}",
              getattr(settings, name) is getattr(distribution, dname))
    cfg = settings.load({})
    check("load({}): seed, v0 switch and privacy text are the distribution's defaults",
          cfg.seed == distribution.DEFAULT_SEED_PROFILE and cfg.legacy_v0 is distribution.DEFAULT_LEGACY_V0
          and cfg.privacy_contact == distribution.DEFAULT_PRIVACY_CONTACT
          and cfg.privacy_hosting == distribution.DEFAULT_PRIVACY_HOSTING,
          f"seed={cfg.seed!r} legacy_v0={cfg.legacy_v0}")
    check("an explicit ATLAS_LEGACY_V0 still wins over the default either way",
          settings.load({"ATLAS_SEED": "none", "ATLAS_LEGACY_V0": "0"}).legacy_v0 is False
          and settings.load({"ATLAS_LEGACY_V0": "1"}).legacy_v0 is True)
    hints = settings.load_deploy_hints({})
    check("load_deploy_hints({}): the distribution's host, in the distribution's commands",
          hints.host == distribution.DEFAULT_DEPLOY_HINT_HOST
          and all(getattr(hints, table)[comp] == (
              distribution.DEFAULT_DEPLOY_HINTS[var].format(host=distribution.DEFAULT_DEPLOY_HINT_HOST)
              if var in distribution.DEFAULT_DEPLOY_HINTS else settings.NO_DEPLOY_HINT.format(name=var))
              for table, comp, var in settings.DEPLOY_HINT_ENVS), hints.host)
    check("a variable with no default answers NO_DEPLOY_HINT, naming itself, instead of raising",
          settings.NO_DEPLOY_HINT.format(name="ATLAS_X") == "(no command configured: set ATLAS_X)")

    if not (OVERLAY / "distribution.py").is_file():
        print(f"   (no {OVERLAY.relative_to(HERE.parent)}: this tree is the export, whose own "
              f"distribution.py is {distribution.NAME!r}; the swap below runs against it)")
    neutral_dir = OVERLAY if (OVERLAY / "distribution.py").is_file() else HERE
    r = subprocess.run([sys.executable, "-c", _NEUTRAL_CHILD, str(neutral_dir)], cwd=HERE,
                       capture_output=True, text=True, timeout=120)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("SEAM ")), None)
    got = json.loads(line[len("SEAM "):]) if line else {}
    check("the neutral distribution.py swaps in (the child read it, not backend/'s)",
          r.returncode == 0 and got.get("name") == "neutral"
          and Path(got.get("file") or ".").resolve().parent == neutral_dir.resolve(),
          str(got.get("file")) if got else r.stderr.strip()[-300:])
    check("under it, an unset ATLAS_SEED is `none` and ATLAS_LEGACY_V0 is off",
          got.get("seed") == "none" and got.get("legacy_v0") is False,
          f"seed={got.get('seed')!r} legacy_v0={got.get('legacy_v0')}")
    check("under it, ATLAS_SEED=homelab is refused, naming ATLAS_SEED (the export ships no seed.py)",
          "ATLAS_SEED" in (got.get("refused") or ""), got.get("refused", ""))
    check("under it, the privacy mailbox is example.com and the deploy host example.test",
          (got.get("contact") or "").endswith("@example.com") and (got.get("host") or "").endswith(".example.test"),
          f"{got.get('contact')!r} {got.get('host')!r}")
    check("under it, every unset deploy hint answers NO_DEPLOY_HINT, naming its variable",
          got.get("no_default") is True, str(got.get("commands")))
    blob = json.dumps({k: v for k, v in got.items() if k != "file"})  # the path is this checkout's
    check("under it, no default names a homelab value",
          not _homelab_words(blob) and not re.search(r"project-atlas|Sweden|Cloudflare|deploy/homelab", blob),
          f"{len(_homelab_words(blob))} homelab word(s)")


# --------------------------------------------------------------------------- #
# C. through the real gate, one child process per configuration
# --------------------------------------------------------------------------- #
CASES: dict[str, dict[str, str]] = {
    "default": {"ATLAS_LOGIN_ORIGINS": TEST_ORIGIN},
    "public_url": {"ATLAS_PUBLIC_URL": PUBLIC_URL},
    "internal": {"ATLAS_LOGIN_ORIGINS": TEST_ORIGIN, "ATLAS_BEARER_HOSTS": "testserver",
                 "ATLAS_INTERNAL_HOSTS": "atlas-api"},
    "privacy": {"ATLAS_LOGIN_ORIGINS": TEST_ORIGIN,
                "ATLAS_PRIVACY_CONTACT": "privacy@example.test"},
}


def section_c(db_url: str) -> None:
    print("\n-- C. through the real gate (one process per configuration) --")
    for name, extra in CASES.items():
        env = {k: v for k, v in os.environ.items() if k not in CONFIG_VARS}
        env.update(extra)
        env["ATLAS_TEST_DB_URL"] = db_url
        print(f"\n   [{name}] " + " ".join(f"{k}={v}" for k, v in extra.items()), flush=True)
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", name],
                           env=env, capture_output=True, text=True, timeout=300)
        lines = [ln for ln in r.stdout.splitlines() if ln.startswith(("[PASS]", "[FAIL]"))]
        for ln in lines:
            print("   " + ln)
        check(f"{name}: child ran its checks and exited 0", r.returncode == 0 and bool(lines),
              f"exit {r.returncode}" + ("" if r.returncode == 0 else
                                        f"; stderr tail: {r.stderr.strip()[-600:]}"))


def _child(name: str) -> int:
    import testdb
    try:
        DB = testdb.start()
    except testdb.TestDatabaseUnavailable as e:  # no skip path, ever
        print(f"[FAIL] {name}: no database — {e}")
        return 1
    os.environ["ATLAS_DB_URL"] = DB.url
    os.environ["ATLAS_RESEED"] = "1"
    os.environ.setdefault("ATLAS_FRONTEND_DIR", str(HERE.parent / "frontend"))

    from datetime import datetime, timezone

    from fastapi.testclient import TestClient

    import app
    import auth
    import passwords
    from models_auth import AuthUser

    app.startup()
    with app.session() as s:
        s.add(AuthUser(id="u-set-owner", subject="set-owner", name="Owner", role="owner",
                       is_service=False, must_change_password=False,
                       password_hash=passwords.hash_password(PW),
                       created_at=datetime.now(timezone.utc)))
        s.add(AuthUser(id="u-set-bot", subject="bot-set", name="Bot", role="member",
                       is_service=True, must_change_password=False, password_hash=None,
                       created_at=datetime.now(timezone.utc)))
        s.commit()
        raw_token, _ = auth.mint_service_token(s, owner=s.get(AuthUser, "u-set-bot"),
                                               scopes=["read", "write"], label="settings")
        s.commit()

    def sign_in(base: str):
        c = TestClient(app.app, base_url=base)
        page = c.get("/auth/login")
        if page.status_code != 200:
            return c, None, page.status_code
        token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
        r = c.post("/auth/login", data={"username": "set-owner", "password": PW,
                                        "csrf_token": token},
                   headers={"Origin": base}, follow_redirects=False)
        me = c.get("/api/me")
        return c, (me.json().get("csrf_token") if me.status_code == 200 else None), r.status_code

    def bearer(base: str, **headers) -> TestClient:
        return TestClient(app.app, base_url=base,
                          headers=dict({"Authorization": f"Bearer {raw_token}"}, **headers))

    def node(i: int) -> dict:
        return {"id": f"SET-{name.upper()}-{i}", "type": "project", "name": f"settings {i}"}

    if name in ("default", "public_url"):
        base = TEST_ORIGIN if name == "default" else PUBLIC_URL
        c, csrf, st = sign_in(base)
        check(f"{name}: login on {base} gives a session", csrf is not None, f"login HTTP {st}")
        r = c.post("/api/node", json=dict(node(1), csrf_token=csrf or ""), headers={"Origin": base})
        check(f"{name}: a write with the CSRF token and our Origin succeeds",
              r.status_code == 201, f"HTTP {r.status_code} {r.text[:120]}")
        r = c.post("/api/node", json=node(2), headers={"Origin": base})
        check(f"{name}: the same write without the token is refused (CSRF)",
              r.status_code == 403, f"HTTP {r.status_code}")
        r = c.post("/api/node", json=dict(node(3), csrf_token=csrf or ""),
                   headers={"Origin": "https://evil.example"})
        check(f"{name}: the same write from a foreign Origin is refused",
              r.status_code == 403, f"HTTP {r.status_code}")
        r = bearer(base).get("/api/me")
        check(f"{name}: Bearer works on {base}", r.status_code == 200, f"HTTP {r.status_code}")
        r = bearer(INTERNAL_BASE).get("/api/me")
        check(f"{name}: Bearer to {INTERNAL_BASE} is 421 (no internal hosts configured)",
              r.status_code == 421, f"HTTP {r.status_code}")
    if name == "default":
        r = TestClient(app.app, base_url=TEST_ORIGIN).get("/auth/privacy")
        check("default: the privacy page shows today's mailbox",
              r.status_code == 200 and _OLD_PRIVACY_CONTACT in r.text, f"HTTP {r.status_code}")
    if name == "public_url":
        check("public_url: allowed origins are exactly the public URL",
              auth.ALLOWED_ORIGINS == (PUBLIC_URL,), str(auth.ALLOWED_ORIGINS))
        r = TestClient(app.app, base_url=TEST_ORIGIN).get("/auth/login")
        check("public_url: any other host is 421", r.status_code == 421, f"HTTP {r.status_code}")

    if name == "internal":
        c, csrf, st = sign_in(TEST_ORIGIN)
        check("internal: login on the origin still works", csrf is not None, f"login HTTP {st}")
        sid = c.cookies.get(auth.SESSION_COOKIE) or ""
        check("internal: the session cookie was issued", bool(sid))
        cookie = {"Cookie": f"{auth.SESSION_COOKIE}={sid}"}

        r = bearer(INTERNAL_BASE).get("/api/me")
        check(f"internal: a Bearer read to {INTERNAL_BASE} succeeds", r.status_code == 200,
              f"HTTP {r.status_code} {r.text[:120]}")
        r = bearer(INTERNAL_BASE).post("/api/node", json=node(1))
        check(f"internal: a Bearer write to {INTERNAL_BASE} succeeds", r.status_code == 201,
              f"HTTP {r.status_code} {r.text[:120]}")
        r = TestClient(app.app, base_url=INTERNAL_BASE).get("/api/me", headers=cookie)
        check(f"internal: a cookie request to {INTERNAL_BASE} is 421", r.status_code == 421,
              f"HTTP {r.status_code}")
        r = TestClient(app.app, base_url=INTERNAL_BASE).get("/api/me")
        check("internal: an anonymous request to it is 421", r.status_code == 421,
              f"HTTP {r.status_code}")
        r = TestClient(app.app, base_url=INTERNAL_BASE).get("/auth/login")
        check("internal: the login page on it is 421", r.status_code == 421, f"HTTP {r.status_code}")
        r = bearer(INTERNAL_BASE, **cookie).get("/api/me")
        check("internal: Bearer plus a cookie to it is 421", r.status_code == 421,
              f"HTTP {r.status_code}")
        r = TestClient(app.app, base_url=INTERNAL_BASE,
                       headers={"Authorization": "Basic Zm9vOmJhcg=="}).get("/api/me")
        check("internal: a non-Bearer Authorization to it is 421", r.status_code == 421,
              f"HTTP {r.status_code}")
        r = bearer("http://atlas-api.evil:8000").get("/api/me")
        check("internal: a look-alike host is still 421", r.status_code == 421, f"HTTP {r.status_code}")
        r = TestClient(app.app, base_url="https://other.example").get("/api/me", headers=cookie)
        check("internal: a cookie on any other non-origin host is still 421",
              r.status_code == 421, f"HTTP {r.status_code}")
        r = bearer(TEST_ORIGIN).get("/api/me")
        check("internal: Bearer on the (narrow) bearer host still works", r.status_code == 200,
              f"HTTP {r.status_code}")
        r = c.get("/api/me")
        check("internal: the cookie session on the origin still works", r.status_code == 200,
              f"HTTP {r.status_code}")

    if name == "privacy":
        r = TestClient(app.app, base_url=TEST_ORIGIN).get("/auth/privacy")
        check("privacy: the page shows ATLAS_PRIVACY_CONTACT",
              r.status_code == 200 and "privacy@example.test" in r.text, f"HTTP {r.status_code}")
        check("privacy: ...and not the default mailbox", _OLD_PRIVACY_CONTACT not in r.text)

    return 1 if FAILURES else 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        return _child(sys.argv[2])

    for var in CONFIG_VARS:
        os.environ.pop(var, None)
    os.environ["ATLAS_LOGIN_ORIGINS"] = TEST_ORIGIN
    os.environ.setdefault("ATLAS_PASSWORD_PEPPER", _pepper())
    os.environ.setdefault("ATLAS_TOKEN_PEPPER", _pepper())
    os.environ.setdefault("ATLAS_BREACH_CHECK", "bundled")

    section_a()
    section_b()
    section_d()

    import testdb
    try:
        DB = testdb.start()
    except testdb.TestDatabaseUnavailable as e:  # no skip path, ever
        print(f"FATAL: {e}")
        return 1
    section_c(DB.url)

    print()
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} check(s):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
