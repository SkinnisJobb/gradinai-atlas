"""Is this container running what is deployable? Two questions, answered without anyone asking.

This file exists TWICE, byte-identical: `backend/deploy_status.py` (atlas-api) and
`mcp/app/deploy_status.py` (atlas-mcp). atlas-mcp is its own image and holds no copy of the
backend, so it cannot import this; `backend/verify_deploy_status.py` fails the suite the moment
the two copies differ. Edit one, copy it over the other. Standard library only, for that reason
(plus its sibling `settings`, itself standard library only, for the hint commands; TASK-1356).

WHY IT EXISTS. Twice a component served an old image while its fix sat merged, and both times
the symptom read as a broken tool rather than a missed deploy (TASK-1154): `atlas_file_work`
400ing `owner_retired` for twelve days (2026-08-28), and `atlas_outline` simply absent for four
days (2026-09-14). `tools/check-deployed.sh` could answer both, but only when somebody ran it.

THE TWO FAILURES ARE DIFFERENT, AND SO ARE THE ANSWERS.

  A. PULLED BUT NOT REBUILT (2026-08-28). The server checkout has the fix; the image does not.
     Answered HERE, in-process, with no network: fingerprint the files the Dockerfile copied into
     this image, and the same files in the server checkout, mounted read-only at /deploy-src.
     Content, not commit times, so a docs-only commit is not "stale" and a hand edit on the box
     is. No mount (tests, a local instance, staging) means `unknown`, never `stale`.

  B. MERGED BUT NEVER PULLED (2026-09-14). GitHub has the fix; the server checkout does not.
     A container cannot ask GitHub, so `tools/deploy-watch.sh` runs hourly on the server,
     `git fetch`es with the read-only deploy key, and writes /deploy-watch/status.json. This
     module only reads that file. A merge is reported only after it has waited
     PENDING_GRACE_S, so a merge that is about to be deployed the same day raises nothing.
     A status file that has stopped being refreshed is itself reported, because a watcher that
     died silently is the same failure one level up.

Neither half deploys anything. Deploying to live stays a decision (TASK-1154, out of scope).
"""

import hashlib
import json
import os
import threading
import time

IMAGE_ROOT = "/app"


def paths(environ=None):
    """(checkout root, status file): ATLAS_DEPLOY_SRC and ATLAS_DEPLOY_WATCH_FILE from
    `environ` (default os.environ), as read once below. The configuration catalogue
    (backend/settings_catalog.py, TASK-1433) reads them from the mapping it is given."""
    env = os.environ if environ is None else environ
    return (env.get("ATLAS_DEPLOY_SRC", "/deploy-src"),
            env.get("ATLAS_DEPLOY_WATCH_FILE", "/deploy-watch/status.json"))


CHECKOUT_ROOT, STATUS_FILE = paths()

RECHECK_S = 60
PENDING_GRACE_S = 24 * 3600
STATUS_MAX_AGE_S = 3 * 3600

# (logical prefix, path in the image, path in the checkout, kind[, pattern]).
# `kind` is "file", "tree", or "glob" (one directory level, fnmatch pattern). Each entry mirrors
# a COPY line in that image's Dockerfile, and the checkout paths are the pathspecs in
# tools/deploy-targets -- verify_deploy_status.py fails if either drifts.
API_SPEC = (
    ("backend", "", "backend", "glob", "*.py"),
    ("backend/routers", "routers", "backend/routers", "tree"),
    ("backend/identity", "identity", "backend/identity", "tree"),
    ("backend/retrieval", "retrieval", "backend/retrieval", "tree"),
    ("backend/plans", "plans", "backend/plans", "tree"),
    ("backend/data", "data", "backend/data", "tree"),
    ("frontend", "frontend", "frontend", "tree"),
    ("backend/requirements-lock.txt", "requirements-lock.txt", "backend/requirements-lock.txt", "file"),
    ("backend/Dockerfile", "Dockerfile.build", "backend/Dockerfile", "file"),
)
MCP_SPEC = (
    ("mcp/app", "app", "mcp/app", "tree"),
    ("mcp/requirements-lock.txt", "requirements-lock.txt", "mcp/requirements-lock.txt", "file"),
    ("mcp/Dockerfile", "Dockerfile.build", "mcp/Dockerfile", "file"),
)

# The commands the warnings name, and the host their sentences name (TASK-1356). Configurable
# through ATLAS_DEPLOY_HINT_* (see `load_deploy_hints` in backend/settings.py); unset, they are
# the homelab commands, byte for byte. `settings` is this copy's own sibling: atlas-mcp's
# `app.settings` or atlas-api's top-level `settings` (both standard library only), imported by
# whichever name works so the two copies of this file stay byte-identical. Read once, at import.
# The default atlas-mcp commands carry the TLS overlay (OSS-24): without
# `-f docker-compose.tls.yml` Compose recreates atlas-mcp alone and the :8446 listener
# (atlas-mcp-tls) is dropped. tools/check-deployed.sh prints the same command;
# verify_deploy_status.py section G pins both.
try:
    from . import settings as _settings          # atlas-mcp: app/deploy_status.py
except ImportError:
    import settings as _settings                 # atlas-api: a top-level module
_HINTS = _settings.load_deploy_hints()
HOST = _HINTS.host
REBUILD = dict(_HINTS.rebuild)
PULL_AND_REBUILD = dict(_HINTS.pull_and_rebuild)

_lock = threading.Lock()
_image_cache = {}      # component -> {logical: sha}; the image never changes under a process
_result_cache = {}     # component -> (checked_at, result)


def _skip(name):
    return name == "__pycache__" or name.endswith(".pyc")


def _files(root, spec, side):
    """{logical path: absolute path} for one side ("image" or "checkout"); None if absent."""
    out = {}
    for entry in spec:
        logical, image_rel, checkout_rel, kind = entry[:4]
        base = os.path.join(root, image_rel if side == "image" else checkout_rel)
        if kind == "file":
            if os.path.isfile(base):
                out[logical] = base
        elif kind == "glob":
            import fnmatch
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                path = os.path.join(base, name)
                if os.path.isfile(path) and fnmatch.fnmatch(name, entry[4]) and not _skip(name):
                    out[logical + "/" + name] = path
        elif kind == "tree":
            if not os.path.isdir(base):
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = sorted(d for d in dirnames if not _skip(d))
                for name in sorted(filenames):
                    if _skip(name):
                        continue
                    path = os.path.join(dirpath, name)
                    rel = os.path.relpath(path, base).replace(os.sep, "/")
                    out[logical + "/" + rel] = path
    return out


def _sha(path):
    """Hashed every time, deliberately. A (size, mtime) cache was tried first and was WRONG: a
    file changed and changed back inside one timestamp tick keeps its old digest, and
    verify_deploy_status.py caught exactly that. The whole result is cached for RECHECK_S
    anyway, and the sets are small -- 120 files for atlas-api, 12 for atlas-mcp."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _fingerprint(files):
    return {logical: _sha(path) for logical, path in files.items()}


def image_state(component, spec, image_root=IMAGE_ROOT, checkout_root=CHECKOUT_ROOT):
    """A: does this image hold the same source files as the server checkout?"""
    if not os.path.isdir(checkout_root):
        return {"state": "unknown", "reason": "no server checkout mounted at %s" % checkout_root}
    if component not in _image_cache:
        _image_cache[component] = _fingerprint(_files(image_root, spec, "image"))
    image = _image_cache[component]
    checkout = _fingerprint(_files(checkout_root, spec, "checkout"))
    if not checkout:
        return {"state": "unknown", "reason": "the mounted checkout holds none of this image's files"}
    differs = sorted(k for k in set(image) | set(checkout) if image.get(k) != checkout.get(k))
    if not differs:
        return {"state": "current", "files": len(checkout)}
    return {"state": "stale", "differs": differs[:5], "differs_count": len(differs)}


def pending_state(component, status_file=STATUS_FILE, now=None):
    """B: have merges touching this component waited on GitHub without being pulled?"""
    now = time.time() if now is None else now
    try:
        with open(status_file, encoding="utf-8") as fh:
            status = json.load(fh)
    except FileNotFoundError:
        return {"state": "unknown", "reason": "no deploy watch on this stack"}
    except (OSError, ValueError) as exc:
        return {"state": "watch_broken", "reason": "unreadable %s: %s" % (status_file, exc)}
    checked_at = status.get("checked_at") or 0
    if status.get("error"):
        return {"state": "watch_broken", "reason": status["error"], "checked_at": checked_at}
    if now - checked_at > STATUS_MAX_AGE_S:
        return {"state": "watch_broken", "checked_at": checked_at,
                "reason": "tools/deploy-watch.sh has not run for %dh" % ((now - checked_at) // 3600)}
    entry = (status.get("pending") or {}).get(component) or {}
    count = int(entry.get("count") or 0)
    if not count:
        return {"state": "current", "checked_at": checked_at}
    waited = now - (entry.get("oldest_merged_at") or now)
    return {"state": "pending" if waited > PENDING_GRACE_S else "recent",
            "count": count, "waited_h": int(waited // 3600),
            "oldest_subject": entry.get("oldest_subject") or "", "checked_at": checked_at}


def warnings_for(component, image, pending, hints=None):
    """The sentences an agent or operator sees. Each names the remedy, not only the gap.

    `hints` (a `settings.DeployHints`) defaults to the ones read at import; a verify passes its own.
    """
    host = HOST if hints is None else hints.host
    rebuild = REBUILD if hints is None else hints.rebuild
    pull_and_rebuild = PULL_AND_REBUILD if hints is None else hints.pull_and_rebuild
    out = []
    if image.get("state") == "stale":
        out.append("%s is running an image older than the source on %s (%d file(s) differ, "
                   "e.g. %s). Rebuild: %s" % (component, host, image["differs_count"], image["differs"][0],
                                               rebuild[component]))
    if pending.get("state") == "pending":
        out.append("%d merged change(s) touching %s have been on GitHub master for %dh without being "
                   "pulled onto %s (oldest: %r). Deploy, with the owner's OK: %s"
                   % (pending["count"], component, pending["waited_h"], host, pending["oldest_subject"][:80],
                      pull_and_rebuild[component]))
    if pending.get("state") == "watch_broken":
        out.append("The deploy watch on %s is not reporting (%s), so merges waiting on GitHub "
                   "would go unnoticed. Check the cron entry for tools/deploy-watch.sh."
                   % (host, pending["reason"]))
    return out


def check(component, spec, image_root=IMAGE_ROOT, checkout_root=CHECKOUT_ROOT,
          status_file=STATUS_FILE, max_age_s=RECHECK_S):
    """Both halves for one component, cached for `max_age_s`. Never raises: a broken check must
    not break the route or the tool call it rides along with."""
    with _lock:
        cached = _result_cache.get(component)
        if cached and time.time() - cached[0] < max_age_s:
            return cached[1]
        try:
            image = image_state(component, spec, image_root, checkout_root)
        except Exception as exc:  # noqa: BLE001 -- reported, never raised
            image = {"state": "unknown", "reason": "fingerprint failed: %s" % exc}
        pending = pending_state(component, status_file)
        result = {"component": component, "image": image, "master": pending,
                  "warnings": warnings_for(component, image, pending)}
        _result_cache[component] = (time.time(), result)
        return result
