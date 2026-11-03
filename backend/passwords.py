"""
Atlas password security primitives — TASK-965.

This module is the SINGLE SOURCE for every number in the password/rate-limit
design. TASK-967 (login + AuthGate), TASK-968 (service tokens) and TASK-970
(`admin.py user add / reset-password`) import from here and do not re-choose any
of it at 23:00. Nothing in this file imports FastAPI, SQLAlchemy or any Atlas
module: it is pure logic plus one optional outbound HTTP call, so it can be
exercised without a database, a request or a running app.

────────────────────────────────────────────────────────────────────────────────
1. Storage scheme — `argon2id+hmac-sha256-v1`
────────────────────────────────────────────────────────────────────────────────
    stored = argon2id( HMAC-SHA256(pepper, "password-v1\\0" + NFC(password)) )

and the full PHC string (`$argon2id$v=19$m=65536,t=3,p=4$…`) goes in ONE `TEXT`
column, `auth_user.password_hash`. The PHC string already carries algorithm,
version, parameters and salt, which is NIST's "store a reference to the scheme
and cost factor" without a second column. `auth_user.hash_scheme` records
`HASH_SCHEME` (below) so the pepper application order and the NFC step are
recoverable from the row alone.

Argon2id parameters: m=65536 KiB (64 MiB), t=3, p=4, salt 16 B, hash 32 B —
`argon2-cffi`'s own defaults, which are exactly RFC 9106's second RECOMMENDED
option and comfortably above OWASP's 19 MiB floor. They must be MEASURED on
the server, not on a desktop; `python passwords.py --measure` (below) prints
the median verify time the acceptance criterion asks for. Accepted band is
50-250 ms; above 250 ms lower `time_cost` before `memory_cost`.

`check_needs_rehash()` runs after every successful verify and the caller stores
the returned `VerifyResult.new_hash`. With no Alembic in this repo that is the
ONLY parameter-migration path that exists.

**Deviation from the task text, stated loudly (see the TASK-965 report):** the
task and TASK-966 both describe storing `HMAC-SHA256(pepper, argon2id_phc)` —
peppering AFTER hashing. That form is not verifiable: Argon2 is salted, the salt
lives inside the PHC string, and if only the HMAC of that string is stored there
is nothing left to recompute the Argon2 hash from. The property the task
actually asks for — a database-only disclosure (a leaked `pg_dump`, a SQL
injection read) yields hashes that cannot be attacked offline without a second
secret — is delivered by the PRE-hash form above, which is what OWASP's Password
Storage Cheat Sheet recommends and which keeps the ergonomic `PasswordHasher`
API and `check_needs_rehash()` that the task also requires. Same threat covered,
same pepper, same `hash_scheme` string; only the order changed.

Two things are deliberately absent, and their names are kept out of this file so
the "is it anywhere in the tree?" grep stays clean: the unmaintained hashing
wrapper library every FastAPI tutorial reaches for (last release October 2020,
and the source of the backend breakage in all of them), and the 72-byte-limited
legacy KDF that OWASP has demoted to legacy-only. `argon2-cffi` is used
directly. See TASK-965 §"Hashing" for the full argument.

────────────────────────────────────────────────────────────────────────────────
2. The pepper — fail closed, at import, before uvicorn binds
────────────────────────────────────────────────────────────────────────────────
`ATLAS_PASSWORD_PEPPER` is 32 bytes of CSPRNG output, base64-encoded, and it is
REQUIRED. A missing or malformed value raises `MissingPepperError` at import
time, naming the key. There is deliberately no generated-if-missing fallback: a
per-boot random pepper invalidates every stored hash on every reboot, and this
box reboots unattended.

Rotating the pepper invalidates every stored hash and forces a reset for all
users. That is the accepted cost, documented rather than tooled.

Mint one:

    python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"

and file it in KeePassXC. It is read through `settings.read_secret`: from the
file named by `ATLAS_PASSWORD_PEPPER_FILE` (a Docker secret) when that is set,
otherwise from the plain env var as before; `ATLAS_REQUIRE_SECRET_FILES=1`
opts into refusing the plain env var (STORY-oss-03). Either variable
must reach the container through an explicit `environment:` entry — `atlas-api`
declares no `env_file:`, so a value in `.env` reaches compose's `${}`
interpolation but NOT the process. Verify with `docker compose config`, never
by reading the `.env`.

────────────────────────────────────────────────────────────────────────────────
3. Policy — NIST SP 800-63B rev 4, length-first
────────────────────────────────────────────────────────────────────────────────
| Rule              | Value                                              |
|-------------------|----------------------------------------------------|
| Minimum length    | 15 code points after NFC (SHALL, single-factor)    |
| Maximum length    | 128 code points (DoS bound; SHOULD is >= 64)       |
| Truncation        | never                                              |
| Composition rules | none — no character-class requirement of any kind  |
| Rotation / expiry | none — change only on evidence of compromise       |
| Character set     | all printing ASCII incl. space, plus Unicode       |
| Normalisation     | NFC, before length counting and before hashing     |
| Hints / KBA       | none, ever                                         |
| Blocklist         | whole candidate, at set/change time ONLY            |

15 and not 8 because Atlas is password-only: 8 is the rev-4 number for a
password used as one factor of several. That is the standards cost of
consciously skipping MFA, recorded here so nobody later "corrects" it downward.

Forced change on FIRST login is not rotation — it is retirement of an
admin-minted handover credential (ASVS 6.4.1) — and is enforced by TASK-967's
middleware, not by this module.

────────────────────────────────────────────────────────────────────────────────
4. Breach screening — remote k-anonymity, bundled fallback, WARN on failure
────────────────────────────────────────────────────────────────────────────────
At SET/CHANGE TIME ONLY. Never on login: that is the only place NIST requires
it, and a third-party call in the login hot path is a third party inside the
perimeter.

Default mode (`ATLAS_BREACH_CHECK=remote`) is the HIBP k-anonymity range API:
the first 5 hex characters of the candidate's SHA-1 leave the box, the password
never does. That is an ADR 0011 boundary crossing and must be authorised by ADR
0025 before this default ships; `ATLAS_BREACH_CHECK=bundled` makes it zero
egress with one env var if 0025 rules the other way.

On any remote failure: fall back to the bundled corpus and log WARN — never a
silent skip, and never a blocked account setup, because a third-party outage
must not stop the operator minting an account. If BOTH paths are unavailable the
result carries `source="unavailable"`, `degraded=True` and an ERROR is logged;
the caller writes a `breach_list_unavailable` row to `auth_event`.

Bundled corpus: `backend/data/breached-passwords.txt`, >= 3000 entries, matched
as a WHOLE candidate (never as a substring), NFC-exact or casefold-equal.

    Expanding the bundled corpus
    ----------------------------
    The file is generated from SecLists (MIT), Passwords/Common-Credentials:
    the first 3000 lines of `10k-most-common.txt` plus every entry of
    `100k-most-used-passwords-NCSC.txt` whose NFC length is >= 15 — the latter
    being the only part of a common-password list that can survive the 15 code
    point minimum at all. To expand, download a larger source list, keep every
    entry of length >= MIN_PASSWORD_LENGTH, dedupe against the existing file,
    sort, and keep the `#` header. Size is the only cost: the lookup is a
    frozenset built once per process. One source entry is left out on purpose
    (owner decision, 2026-11-03): it matches a term on the personal-reference
    scrub denylist. After regenerating, run the scrub job; an entry it flags in
    this file is removed, not allowlisted.

    ⚠️ `backend/Dockerfile` copies backend modules by glob (`backend/*.py`),
    which covers no data directory — it carried the corpus in the repo and NOT
    in the image until TASK-1127 added `COPY backend/data ./data`. Keep that
    line: without it this fallback fails OPEN, silently (empty frozenset ->
    `_check_bundled` returns `breached=False` -> the password is admitted), and
    nothing on a workstation reproduces it because the file is right there.
    `verify_object_graph.py` §21 pins the COPY by parsing the Dockerfile, and
    `corpus_status()` exposes the runtime side.

────────────────────────────────────────────────────────────────────────────────
5. Throttling — self-healing backoff, never a hard lock as the common path
────────────────────────────────────────────────────────────────────────────────
Per account (keyed on a HMAC of the SUBMITTED username, so an account that does
not exist is throttled identically to one that does — otherwise the throttle
itself is the enumeration oracle the dummy verify was spent closing):

    required wait after n consecutive failures: 0, 0, 1, 2, 4, 8, … s
    capped at 300 s, reset on success, treated as absent after 30 min idle.

Hard cap at 100 consecutive failures disables the credential (NIST SP 800-63B
§3.2.2 SHALL). Behind a 300 s cap that takes days, so it is the compliance
floor, not the working mechanism.

Why NOT a hard account lockout as the common path: five accounts, guessable
usernames, an internet-facing form, no email-based self-unlock and ONE part-time
operator. A hard lock hands any passer-by a free denial-of-service against a
named human, resolvable only by that operator, at night, from a phone.
Self-healing backoff needs nobody.

Per IP (`IpFailureLimiter`): 20 failures / 15 min → 429 + `Retry-After` for
15 min. In-process sliding window; it resets on container restart, which is
acceptable at one replica and is written down here rather than discovered.

**The limiter counts `POST /auth/login` and Bearer-auth FAILURES only.** A
successful `Authorization: Bearer atlas_svc_…` request is never rate-limited. A
design that throttles a healthy ingest freezes the board silently while
`/api/health` stays 200 — this repo has already lived that failure once.

Client IP: `client_ip()` reads `CF-Connecting-IP` ONLY when the socket peer is
inside `ATLAS_TRUSTED_PROXY_CIDRS`, and never reads `X-Forwarded-For`. Behind
cloudflared every public request arrives from the tunnel's address, so a per-IP
ceiling keyed on the socket peer would become a GLOBAL ceiling — a self-inflicted
DoS. The default trusted set is EMPTY: unconfigured means "use the socket peer",
never "trust the header".

────────────────────────────────────────────────────────────────────────────────
6. Enumeration and timing
────────────────────────────────────────────────────────────────────────────────
One message, one status, one body for unknown username / wrong password /
disabled account / expired temp credential: `GENERIC_LOGIN_FAILURE_MESSAGE`. The
user gets no diagnostic; `auth_event` gets the precise reason.

`dummy_verify()` runs a real Argon2id verification against a hash computed at
import FROM THE LIVE `PasswordHasher` INSTANCE, so it cannot drift when the
parameters are raised (the lesson Django encodes as `harden_runtime`). It is not
a sleep. `verify_password(None, …)` calls it for you, which is the shape that
makes it hard to forget on the username-miss path.

`constant_time_equals()` (i.e. `hmac.compare_digest`) for every non-Argon2
secret comparison: session id digests, bearer token digests, CSRF tokens.

────────────────────────────────────────────────────────────────────────────────
7. Never logged
────────────────────────────────────────────────────────────────────────────────
No function here logs a submitted password, a generated temp password, a service
token or any digest of them (ASVS 16.2.5). Log statements in this module carry
counts, timings and env-var NAMES only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import logging
import math
import os
import re
import secrets
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Deque, Dict, Mapping, Optional, Sequence, Tuple

import argon2
from argon2.exceptions import (
    HashingError,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

import settings

logger = logging.getLogger("atlas.passwords")

__all__ = [
    # scheme + parameters
    "HASH_SCHEME", "ARGON2_TIME_COST", "ARGON2_MEMORY_COST", "ARGON2_PARALLELISM",
    "ARGON2_HASH_LEN", "ARGON2_SALT_LEN",
    # policy
    "MIN_PASSWORD_LENGTH", "MAX_PASSWORD_LENGTH", "POLICY_SUMMARY",
    "PolicyResult", "check_password_policy", "normalise_password", "normalise_username",
    "PasswordPolicyError",
    # hashing
    "hash_password", "verify_password", "dummy_verify", "needs_rehash", "VerifyResult",
    "MissingPepperError",
    # breach screening
    "BreachResult", "check_breached", "corpus_status",
    # throttling
    "MAX_BACKOFF_SECONDS", "THROTTLE_DECAY_SECONDS", "HARD_DISABLE_THRESHOLD",
    "IP_FAILURE_LIMIT", "IP_WINDOW_SECONDS", "IP_BLOCK_SECONDS",
    "ThrottleState", "ThrottleDecision", "throttle_key", "required_backoff_seconds",
    "evaluate_throttle", "record_throttle_failure", "IpFailureLimiter", "IpDecision",
    "RateLimited", "retry_after_headers", "client_ip", "HTTP_TOO_MANY_REQUESTS",
    "GENERIC_LOGIN_FAILURE_MESSAGE",
    # secrets
    "generate_temp_password", "generate_service_token", "SERVICE_TOKEN_PREFIX",
    "TEMP_CREDENTIAL_TTL_HOURS", "temp_credential_expires_at",
    "constant_time_equals", "digest_secret",
    # ops
    "self_check", "measure_verify_ms",
]


# --------------------------------------------------------------------------- #
# 1. Parameters. Every number in this block is pinned by TASK-965.
# --------------------------------------------------------------------------- #

ARGON2_TIME_COST = 3            # t
ARGON2_MEMORY_COST = 65536      # m, in KiB -> 64 MiB
ARGON2_PARALLELISM = 4          # p
ARGON2_HASH_LEN = 32            # bytes
ARGON2_SALT_LEN = 16            # bytes

#: Written to `auth_user.hash_scheme`. Its meaning is defined by this module:
#: NFC(password) -> HMAC-SHA256(pepper, label + password) -> Argon2id(PHC string).
HASH_SCHEME = "argon2id+hmac-sha256-v1"

# Owner decision, 2026-07-30: 8 now, raise to 15 later. SP 800-63B rev 4 sets 8 as the
# absolute floor and RECOMMENDS 15 for single-factor, which this is — so this is the
# floor, not the recommendation, and it deviates from TASK-965's acceptance criteria.
#
# Two consequences worth remembering before "later" arrives:
#   1. Raising this does NOT re-secure existing accounts. A password set at 8 stays 8
#      until that user changes it, so "improve later" means "improve for new passwords".
#      Bumping it needs a forced reset (admin.py user reset-password) to actually bite.
#   2. The blocklist check is INDEPENDENT of length and still applies. `12345678`,
#      `password`, and the rest of the breach corpus are refused at any length.
# The login throttle (429 after a couple of failures) carries more of the load at 8
# than it would at 15, and this form becomes directly internet-facing once the
# Cloudflare Access application is retired (TASK-970).
MIN_PASSWORD_LENGTH = 8         # code points, after NFC. SP 800-63B rev 4 floor.
MAX_PASSWORD_LENGTH = 128       # code points, after NFC. DoS bound; never truncate.

#: Accepted verify-latency band on the target box, in milliseconds.
VERIFY_TARGET_MS = (50.0, 250.0)

TEMP_PASSWORD_ENTROPY_BYTES = 16    # secrets.token_urlsafe(16) -> 128 bits, 22 chars
SERVICE_TOKEN_ENTROPY_BYTES = 32    # secrets.token_urlsafe(32) -> 256 bits, 43 chars
SERVICE_TOKEN_PREFIX = "atlas_svc_"
TEMP_CREDENTIAL_TTL_HOURS = 72

MAX_BACKOFF_SECONDS = 300
THROTTLE_DECAY_SECONDS = 30 * 60
HARD_DISABLE_THRESHOLD = 100

IP_FAILURE_LIMIT = 20
IP_WINDOW_SECONDS = 15 * 60
IP_BLOCK_SECONDS = 15 * 60
IP_MAX_TRACKED = 20000          # bound the in-process table; see IpFailureLimiter

HTTP_TOO_MANY_REQUESTS = 429

#: The ONE message. Unknown username, wrong password, disabled account, expired
#: temp credential and a throttled attempt all produce exactly this, with the
#: same status and the same body (ASVS 6.3.8).
GENERIC_LOGIN_FAILURE_MESSAGE = "Login failed; invalid username or password."

PEPPER_ENV = "ATLAS_PASSWORD_PEPPER"
PEPPER_BYTES = 32

BREACH_MODE_ENV = "ATLAS_BREACH_CHECK"          # "remote" (default) | "bundled"
BREACH_REMOTE_URL = "https://api.pwnedpasswords.com/range/"
BREACH_TIMEOUT_SECONDS = 4.0
BREACH_CORPUS_PATH = Path(__file__).resolve().parent / "data" / "breached-passwords.txt"

TRUSTED_PROXY_ENV = "ATLAS_TRUSTED_PROXY_CIDRS"  # comma-separated; EMPTY by default

POLICY_SUMMARY = (
    "At least {min} characters, at most {max}. Length is the whole requirement: "
    "there are no character-type rules, nothing is ever truncated, spaces and "
    "any Unicode character are fine, and this password never expires. A long "
    "passphrase of ordinary words beats a short complicated one. It is checked "
    "against a public breach corpus when you set it."
).format(min=MIN_PASSWORD_LENGTH, max=MAX_PASSWORD_LENGTH)


class MissingPepperError(RuntimeError):
    """`ATLAS_PASSWORD_PEPPER` absent or malformed. Raised at IMPORT."""


class PasswordPolicyError(ValueError):
    """A candidate that failed the structural policy reached `hash_password()`."""

    def __init__(self, result: "PolicyResult") -> None:
        super().__init__(result.message)
        self.result = result


# --------------------------------------------------------------------------- #
# 2. The pepper. Fail closed, at import, before uvicorn binds (ADR 0015).
# --------------------------------------------------------------------------- #

_PEPPER_HELP = (
    "%s is required and must be %d bytes of CSPRNG output, base64-encoded. "
    "Mint one with:  python -c \"import base64,os;"
    "print(base64.b64encode(os.urandom(%d)).decode())\"  "
    "then file it in KeePassXC and deliver it to the container as the env var "
    "itself or, preferably, as a file named by %s_FILE (a Docker secret; see "
    "settings.py — ATLAS_REQUIRE_SECRET_FILES=1 makes the file mandatory). "
    "atlas-api declares no env_file:, so either variable needs an explicit "
    "`environment:` entry (check with "
    "`docker compose config`). There is no "
    "generated-if-missing fallback: a per-boot random pepper would invalidate "
    "every stored password on every reboot."
) % (PEPPER_ENV, PEPPER_BYTES, PEPPER_BYTES, PEPPER_ENV)


def _load_pepper() -> bytes:
    try:
        raw = settings.read_secret(PEPPER_ENV)
    except settings.SecretError as exc:
        raise MissingPepperError("%s %s" % (exc, _PEPPER_HELP)) from None
    try:
        pepper = base64.b64decode(raw, validate=True)
    except Exception as exc:  # binascii.Error and friends
        raise MissingPepperError(
            "%s is not valid base64 (%s). %s" % (PEPPER_ENV, type(exc).__name__, _PEPPER_HELP)
        ) from None  # `from None`: never let the value near a traceback
    if len(pepper) != PEPPER_BYTES:
        raise MissingPepperError(
            "%s decodes to %d bytes, expected %d. %s"
            % (PEPPER_ENV, len(pepper), PEPPER_BYTES, _PEPPER_HELP)
        )
    return pepper


_PEPPER = _load_pepper()


def _keyed_digest(label: str, message: bytes, key: Optional[bytes] = None) -> bytes:
    """HMAC-SHA256 with domain separation, so two uses of one pepper never collide."""
    return hmac.new(key or _PEPPER, label.encode("utf-8") + b"\x00" + message, hashlib.sha256).digest()


# --------------------------------------------------------------------------- #
# 3. Normalisation
# --------------------------------------------------------------------------- #

def normalise_password(password: str) -> str:
    """NFC, and nothing else.

    No stripping, no case folding, no truncation: the password is verified in
    full exactly as submitted, modulo Unicode normalisation. NFC runs before the
    length count so 'é' as e+U+0301 counts as one code point, not two.
    """
    if not isinstance(password, str):
        raise TypeError("password must be str")
    return unicodedata.normalize("NFC", password)


def normalise_username(username: str) -> str:
    """NFC, strip, lowercase — matching `auth_user.subject`'s CHECK constraint.

    Used for the account lookup AND for the throttle key, so that a submitted
    'ALEX ' and 'alex' share one throttle row.
    """
    if not isinstance(username, str):
        raise TypeError("username must be str")
    return unicodedata.normalize("NFC", username).strip().lower()


#: Local handles are permanent identifiers, not display names: lowercase, no
#: spaces, and IMMUTABLE — a rename is a new row plus a re-grant, because the
#: visibility tables store `id` and never `subject`. The DB CHECK only enforces
#: the lowercasing; this is the rest of it.
HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")

HANDLE_HELP = (
    "2-64 characters, lowercase letters, digits, dot, dash or underscore, "
    "starting with a letter or digit. Usernames are permanent identifiers and "
    "are never renamed."
)


def validated_handle(raw: str) -> str:
    """Normalise and check an account handle, or raise `ValueError`.

    MOVED HERE FROM `admin.py` BY TASK-1115, and the reason is in the comment it
    used to carry: *"applied at the one place accounts are created"*. There are
    two such places now — `admin bot add` and the self-service credential route
    — and a validation rule with two copies is a rule that will disagree with
    itself. It sits beside `normalise_username` because it is the same subject
    and because both `admin.py` and `app.py` already import this module, while
    neither may import the other.
    """
    handle = normalise_username(raw)
    if not HANDLE_RE.match(handle):
        raise ValueError("%r is not a usable username: %s" % (raw, HANDLE_HELP))
    return handle


def agent_base(person) -> "str | None":
    """The prefix of a person's agent names, or None when there is nothing readable.

    ONE RULE FOR BOTH DOORS (TASK-1415): `atlas-admin token mint` and the
    self-service credential route name a person's agent `<base>-<purpose>` with
    this base, so one person gets the same agent name whichever door they use.
    A local person's base is their username. An IdP-backed person's subject is
    an opaque id (an Entra object id, a Keycloak UUID), which would make a
    permanent, unreadable name, so their base is the local part of their email,
    reduced to the handle alphabet. None when an IdP-backed person has no email
    yet: each caller decides what that means (the CLI refuses; the route falls
    back to the subject).

    `person` is an `auth_user` row; only `idp`, `subject` and `email` are read.
    """
    if (person.idp or "local") == "local":
        return person.subject
    local = (person.email or "").split("@", 1)[0].lower()
    base = "".join(c if (c.isascii() and (c.isalnum() or c in "._-")) else "-" for c in local)
    base = base.strip("-._")[:40]
    return base or None


# --------------------------------------------------------------------------- #
# 4. Hashing and verification
# --------------------------------------------------------------------------- #

_HASHER = argon2.PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST,
    parallelism=ARGON2_PARALLELISM,
    hash_len=ARGON2_HASH_LEN,
    salt_len=ARGON2_SALT_LEN,
    type=argon2.Type.ID,
)

_PEPPER_LABEL = "password-v1"


def _peppered(password_nfc: str) -> bytes:
    return _keyed_digest(_PEPPER_LABEL, password_nfc.encode("utf-8"))


#: A fixed dummy credential, hashed at import FROM THE LIVE HASHER so it tracks
#: any future parameter change automatically. Not a secret: it protects nothing,
#: it only costs the same as a real verify.
_DUMMY_PASSWORD = "atlas-dummy-verify-target-do-not-use"
_DUMMY_CANDIDATE = "atlas-dummy-verify-candidate-never-matches"
_DUMMY_HASH = _HASHER.hash(_peppered(normalise_password(_DUMMY_PASSWORD)))


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of one verification.

    `reason` is for `auth_event` and the log. It NEVER reaches the caller of the
    HTTP endpoint — that gets `GENERIC_LOGIN_FAILURE_MESSAGE` in every branch.
    """

    ok: bool
    reason: str                       # ok|mismatch|no_credential|over_length|invalid_hash
    needs_rehash: bool = False
    new_hash: Optional[str] = None    # set only when ok and the parameters moved


def hash_password(password: str, *, enforce_policy: bool = True) -> str:
    """Return the PHC string to store in `auth_user.password_hash`.

    Structural policy (length, control characters) is enforced here as a
    backstop; the breach check is NOT — it is a separate, network-touching step
    the caller runs at set/change time via `check_password_policy()`.
    """
    normalised = normalise_password(password)
    if enforce_policy:
        result = check_password_policy(normalised, breach_check=False)
        if not result.ok:
            raise PasswordPolicyError(result)
    try:
        return _HASHER.hash(_peppered(normalised))
    except HashingError as exc:
        # Argon2 itself failed (memory allocation, most plausibly). Do not
        # degrade to a weaker hash — that is how a system quietly stops hashing.
        raise RuntimeError("argon2id hashing failed: %s" % type(exc).__name__) from None


def dummy_verify() -> None:
    """Burn one real Argon2id verification. Called on the username-miss path.

    Without this a miss returns in ~1 ms and a hit in ~60-250 ms, and a
    five-account list is enumerable in a single pass. It is a real verify
    against `_DUMMY_HASH`, not a sleep, so it tracks the live parameters.
    """
    try:
        _HASHER.verify(_DUMMY_HASH, _peppered(_DUMMY_CANDIDATE))
    except VerifyMismatchError:
        pass  # expected: the candidate is chosen never to match
    except (VerificationError, InvalidHashError):  # pragma: no cover - would be a bug here
        logger.error("dummy verify failed structurally; the dummy hash is corrupt")


def verify_password(stored_hash: Optional[str], password: str) -> VerifyResult:
    """Verify `password` against a stored PHC string.

    `stored_hash=None` (unknown username, a bot row, an account with no
    credential) runs `dummy_verify()` and returns a failure, so the miss path
    costs the same as a hit by construction.

    `argon2-cffi.verify()` returns True or RAISES; it never returns False.
    `except Exception: return False` would turn an `InvalidHashError` from a
    corrupt row into an ordinary wrong password and hide real damage, so the
    exceptions are separated here and a structural failure is logged at ERROR.
    """
    if not stored_hash:
        dummy_verify()
        return VerifyResult(False, "no_credential")

    normalised = normalise_password(password)
    if len(normalised) > MAX_PASSWORD_LENGTH:
        # Refuse to run a memory-hard KDF over an unbounded internet-supplied
        # string, but stay indistinguishable: same dummy cost, same generic fail.
        dummy_verify()
        return VerifyResult(False, "over_length")

    try:
        _HASHER.verify(stored_hash, _peppered(normalised))
    except VerifyMismatchError:
        return VerifyResult(False, "mismatch")
    except InvalidHashError:
        logger.error(
            "stored password hash is not a valid argon2 PHC string; treating as a "
            "failure and NOT as a wrong password — this row needs a reset"
        )
        return VerifyResult(False, "invalid_hash")
    except VerificationError:
        logger.error("argon2 verification failed structurally (not a mismatch)")
        return VerifyResult(False, "invalid_hash")

    rehash = False
    new_hash = None
    try:
        rehash = _HASHER.check_needs_rehash(stored_hash)
        if rehash:
            new_hash = _HASHER.hash(_peppered(normalised))
    except Exception:  # noqa: BLE001 - a rehash failure must never fail a good login
        logger.warning("check_needs_rehash/rehash failed; login still succeeds")
        rehash, new_hash = False, None
    return VerifyResult(True, "ok", needs_rehash=rehash, new_hash=new_hash)


def needs_rehash(stored_hash: str) -> bool:
    """True when `stored_hash` was made with parameters other than the current ones."""
    return _HASHER.check_needs_rehash(stored_hash)


# --------------------------------------------------------------------------- #
# 5. Policy
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PolicyResult:
    ok: bool
    code: str = ""            # too_short|too_long|control_characters|context_word|breached
    message: str = ""         # shown to the user; safe, specific, non-scolding
    breach: Optional["BreachResult"] = None


_MSG_TOO_SHORT = (
    "Password must be at least %d characters. Length is the only requirement — "
    "a passphrase of a few ordinary words is fine, and there are no rules about "
    "capitals, digits or symbols." % MIN_PASSWORD_LENGTH
)
_MSG_TOO_LONG = (
    "Password must be at most %d characters. It is never truncated, so please "
    "shorten it rather than assuming the extra characters count."
    % MAX_PASSWORD_LENGTH
)
_MSG_CONTROL = (
    "Password must not contain control characters such as tabs or line breaks. "
    "Spaces, punctuation, accented letters and emoji are all accepted."
)
_MSG_CONTEXT = (
    "Password must not contain your username or the name of this service. "
    "Choose something unrelated to this account."
)
_MSG_BREACHED = (
    "This password appears in a public list of passwords exposed in breaches, "
    "so it cannot be used here. Choose a different one — a longer, unrelated "
    "passphrase is the easiest fix."
)

#: Context words rejected as a blocklist entry (SP 800-63B rev 4 explicitly
#: permits context-specific words on the blocklist). This is NOT a composition
#: rule: it constrains content, not character classes. The words are
#: `distribution.PASSWORD_CONTEXT_WORDS` (STORY-oss-23 A4b), unchanged.
_CONTEXT_WORDS = tuple(settings.distribution.PASSWORD_CONTEXT_WORDS)


def _has_control_characters(text: str) -> bool:
    # Cc = C0/C1 controls, Cs = surrogates. Cf (format) is deliberately ALLOWED:
    # U+200D ZERO WIDTH JOINER is Cf and is load-bearing inside emoji sequences.
    return any(unicodedata.category(ch) in ("Cc", "Cs") for ch in text)


def check_password_policy(
    password: str,
    *,
    username: Optional[str] = None,
    breach_check: bool = True,
    context_check: bool = True,
) -> PolicyResult:
    """Apply the full set/change-time policy to a candidate.

    Call at SET and CHANGE time only, never on login. Cheap local checks run
    first so a too-short candidate never triggers an outbound request.
    """
    candidate = normalise_password(password)

    if len(candidate) < MIN_PASSWORD_LENGTH:
        return PolicyResult(False, "too_short", _MSG_TOO_SHORT)
    if len(candidate) > MAX_PASSWORD_LENGTH:
        return PolicyResult(False, "too_long", _MSG_TOO_LONG)
    if _has_control_characters(candidate):
        return PolicyResult(False, "control_characters", _MSG_CONTROL)

    if context_check:
        folded = candidate.casefold()
        words = list(_CONTEXT_WORDS)
        if username:
            handle = normalise_username(username)
            if len(handle) >= 3:
                words.append(handle)
        if any(word.casefold() in folded for word in words if word):
            return PolicyResult(False, "context_word", _MSG_CONTEXT)

    breach = None
    if breach_check:
        breach = check_breached(candidate)
        if breach.breached:
            return PolicyResult(False, "breached", _MSG_BREACHED, breach=breach)

    return PolicyResult(True, breach=breach)


# --------------------------------------------------------------------------- #
# 6. Breach screening
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BreachResult:
    breached: bool
    count: int = 0                 # occurrences reported by HIBP; 0 for the bundled list
    source: str = "bundled"        # remote|bundled|unavailable
    degraded: bool = False         # True when the configured source could not be used


_corpus_lock = threading.Lock()
_corpus: Optional[frozenset] = None
_corpus_folded: Optional[frozenset] = None


def _load_corpus() -> Tuple[frozenset, frozenset]:
    global _corpus, _corpus_folded
    with _corpus_lock:
        if _corpus is not None and _corpus_folded is not None:
            return _corpus, _corpus_folded
        entries = set()
        try:
            with BREACH_CORPUS_PATH.open("r", encoding="utf-8") as handle:
                for line in handle:
                    value = line.rstrip("\r\n")
                    if not value or value.startswith("#"):
                        continue
                    entries.add(unicodedata.normalize("NFC", value))
        except OSError as exc:
            logger.error(
                "bundled breach corpus unreadable at %s (%s) — the zero-egress "
                "fallback is NOT available; backend/Dockerfile must COPY backend/data",
                BREACH_CORPUS_PATH,
                type(exc).__name__,
            )
        _corpus = frozenset(entries)
        _corpus_folded = frozenset(value.casefold() for value in entries)
        return _corpus, _corpus_folded


def corpus_status() -> Dict[str, object]:
    """Surface the bundled corpus for a boot-time / `/readyz` check."""
    exact, _ = _load_corpus()
    return {
        "path": str(BREACH_CORPUS_PATH),
        "present": BREACH_CORPUS_PATH.is_file(),
        "entries": len(exact),
        "sufficient": len(exact) >= 3000,
    }


def _check_bundled(candidate: str, *, degraded: bool = False) -> BreachResult:
    exact, folded = _load_corpus()
    if not exact:
        return BreachResult(False, 0, "unavailable", True)
    hit = candidate in exact or candidate.casefold() in folded
    return BreachResult(hit, 0, "bundled", degraded)


def _check_remote(candidate: str) -> BreachResult:
    """HIBP k-anonymity range query. Raises on any transport/protocol failure."""
    import httpx  # imported lazily: this module must import with no network stack

    digest = hashlib.sha1(candidate.encode("utf-8")).hexdigest().upper()  # noqa: S324
    prefix, suffix = digest[:5], digest[5:]
    response = httpx.get(
        BREACH_REMOTE_URL + prefix,
        headers={"Add-Padding": "true", "User-Agent": "atlas-password-check"},
        timeout=BREACH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    hit_count = 0
    for line in response.text.splitlines():
        line_suffix, _, line_count = line.partition(":")
        if not _:
            continue
        try:
            count = int(line_count.strip())
        except ValueError:
            continue
        if count <= 0:
            continue  # padding entries are emitted with a count of 0
        if hmac.compare_digest(line_suffix.strip().upper(), suffix):
            hit_count = count
    return BreachResult(hit_count > 0, hit_count, "remote", False)


def check_breached(password: str) -> BreachResult:
    """Screen a candidate against the compromised-password corpus.

    SET/CHANGE TIME ONLY. Failure of the remote source falls back to the bundled
    corpus and logs WARN — never a silent skip, and never a blocked account
    setup. The password itself never leaves the process; in remote mode a
    5-hex-character SHA-1 prefix does.
    """
    candidate = normalise_password(password)
    mode = os.environ.get(BREACH_MODE_ENV, "remote").strip().lower() or "remote"

    if mode == "bundled":
        return _check_bundled(candidate)
    if mode != "remote":
        logger.warning(
            "%s=%r is not one of remote|bundled; using the bundled corpus",
            BREACH_MODE_ENV, mode,
        )
        return _check_bundled(candidate, degraded=True)

    try:
        return _check_remote(candidate)
    except Exception as exc:  # noqa: BLE001 - any failure means "use the fallback"
        logger.warning(
            "breach range API unavailable (%s); falling back to the bundled corpus. "
            "Account setup is NOT blocked and the check was NOT skipped — record "
            "breach_list_unavailable in auth_event",
            type(exc).__name__,
        )
        return _check_bundled(candidate, degraded=True)


# --------------------------------------------------------------------------- #
# 7. Per-account throttling — pure functions over the auth_login_throttle row
# --------------------------------------------------------------------------- #
#
# Column names assumed (owned by TASK-966 / backend/models_auth.py):
#     key_hash, failed_count, first_failed_at, last_failed_at, retry_after
# `retry_after` is an ABSOLUTE timestamp, so a row is self-healing and any
# writer may prune a row whose last_failed_at is older than the decay window.
# Nothing here touches a Session: the caller reads the row, calls these, writes
# the row back.

@dataclass(frozen=True)
class ThrottleState:
    """The four fields of an `auth_login_throttle` row that matter to the logic."""

    failed_count: int = 0
    first_failed_at: Optional[datetime] = None
    last_failed_at: Optional[datetime] = None
    retry_after: Optional[datetime] = None


@dataclass(frozen=True)
class ThrottleDecision:
    allowed: bool
    retry_after_seconds: int = 0
    effective_failed_count: int = 0
    decayed: bool = False          # the row aged out and is treated as absent
    hard_disable: bool = False     # >= 100 consecutive failures (NIST §3.2.2 SHALL)


def throttle_key(submitted_username: str) -> str:
    """`auth_login_throttle.key_hash` — 64 hex chars.

    Keyed on the SUBMITTED username, hashed, whether or not the account exists.
    Two consequences, both deliberate: an unknown username is throttled exactly
    like a known one (so the throttle is not an enumeration oracle), and the
    table never holds a plaintext handle an attacker chose.
    """
    return _keyed_digest("throttle-key-v1", normalise_username(submitted_username).encode("utf-8")).hex()


def required_backoff_seconds(failed_count: int) -> int:
    """Wait required after `failed_count` consecutive failures.

    n:    0  1  2  3  4  5  6 ...  11+
    wait: 0  0  1  2  4  8 16 ...  300 (capped)

    This is TASK-965's `min(2**(n-2), 300)`, with the two leading zeros of its
    stated `0,0,1,2,4,8,…` sequence taken as authoritative for n=0 and n=1 (the
    prose says "for n>=3" against a 1-indexed attempt number; the sequence and
    the formula agree once n is read as the failure COUNT, which is what the
    row actually stores).
    """
    if failed_count < 2:
        return 0
    return min(2 ** (failed_count - 2), MAX_BACKOFF_SECONDS)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def evaluate_throttle(state: Optional[ThrottleState], now: Optional[datetime] = None) -> ThrottleDecision:
    """Decide whether an attempt may proceed. Call BEFORE touching the user row.

    A refusal gets `GENERIC_LOGIN_FAILURE_MESSAGE` plus `Retry-After`, byte
    identical to a wrong password, so the throttle discloses nothing.
    """
    now = _aware(now or datetime.now(timezone.utc))
    if state is None or state.failed_count <= 0:
        return ThrottleDecision(True)

    if state.last_failed_at is not None:
        idle = (now - _aware(state.last_failed_at)).total_seconds()
        if idle >= THROTTLE_DECAY_SECONDS:
            # Older than the decay window: treated as absent, and prunable.
            return ThrottleDecision(True, decayed=True)

    hard = state.failed_count >= HARD_DISABLE_THRESHOLD
    if state.retry_after is not None:
        remaining = (_aware(state.retry_after) - now).total_seconds()
        if remaining > 0:
            return ThrottleDecision(
                False,
                retry_after_seconds=max(1, int(math.ceil(remaining))),
                effective_failed_count=state.failed_count,
                hard_disable=hard,
            )
    return ThrottleDecision(
        True, effective_failed_count=state.failed_count, hard_disable=hard
    )


def record_throttle_failure(
    state: Optional[ThrottleState], now: Optional[datetime] = None
) -> Tuple[ThrottleState, ThrottleDecision]:
    """Fold one failure into the row. Returns the row to persist and the decision.

    `decision.hard_disable` True means the caller stamps `auth_user.disabled_at`
    — but ONLY if a matching user row exists. Disabling on a username that was
    never real would leak nothing, and cost a write; the guard is the caller's.
    A success elsewhere DELETES the row (`evaluate_throttle(None)` is allowed).
    """
    now = _aware(now or datetime.now(timezone.utc))
    previous = state.failed_count if state else 0
    if state and state.last_failed_at is not None:
        if (now - _aware(state.last_failed_at)).total_seconds() >= THROTTLE_DECAY_SECONDS:
            previous = 0
    count = previous + 1
    wait = required_backoff_seconds(count)
    first = state.first_failed_at if (state and previous and state.first_failed_at) else now
    updated = ThrottleState(
        failed_count=count,
        first_failed_at=first,
        last_failed_at=now,
        retry_after=now + timedelta(seconds=wait),
    )
    decision = ThrottleDecision(
        allowed=False,
        retry_after_seconds=wait,
        effective_failed_count=count,
        hard_disable=count >= HARD_DISABLE_THRESHOLD,
    )
    return updated, decision


# --------------------------------------------------------------------------- #
# 8. Per-IP ceiling — in-process sliding window
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class IpDecision:
    allowed: bool
    retry_after_seconds: int = 0
    failures_in_window: int = 0


class IpFailureLimiter:
    """20 failures / 15 min from one client IP -> 429 for 15 min.

    COUNTS FAILURES ONLY. `POST /auth/login` failures and Bearer-auth failures.
    A successful request — in particular a valid `Authorization: Bearer
    atlas_svc_…` from the ingest — never reaches `record_failure()`, so a
    healthy ingest cannot be throttled no matter how many requests it makes.

    Secondary defence by design: OWASP is explicit that IP blocking must not be
    the sole or primary control, because it is trivially circumvented. The
    per-account backoff is the primary one.

    State is in-process and resets on container restart. At one replica and
    0 SEK that is the accepted trade; it is written down here rather than
    discovered later.

    Thread-safe: FastAPI runs `def` handlers in a threadpool, so two requests
    genuinely touch this concurrently.
    """

    def __init__(
        self,
        *,
        limit: int = IP_FAILURE_LIMIT,
        window_seconds: int = IP_WINDOW_SECONDS,
        block_seconds: int = IP_BLOCK_SECONDS,
        max_tracked: int = IP_MAX_TRACKED,
        clock=time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.block_seconds = block_seconds
        self.max_tracked = max_tracked
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: Dict[str, Deque[float]] = {}
        self._blocked_until: Dict[str, float] = {}

    # -- internals ---------------------------------------------------------- #
    def _trim(self, key: str, now: float) -> Deque[float]:
        bucket = self._failures.get(key)
        if bucket is None:
            bucket = deque()
            self._failures[key] = bucket
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        return bucket

    def _evict(self, now: float) -> None:
        """Bound memory: drop keys with no live state, then the oldest."""
        if len(self._failures) <= self.max_tracked:
            return
        for key in [k for k, v in self._failures.items() if not v and self._blocked_until.get(k, 0) <= now]:
            self._failures.pop(key, None)
            self._blocked_until.pop(key, None)
        while len(self._failures) > self.max_tracked:
            oldest = min(self._failures, key=lambda k: self._failures[k][0] if self._failures[k] else 0.0)
            self._failures.pop(oldest, None)
            self._blocked_until.pop(oldest, None)

    # -- API ---------------------------------------------------------------- #
    def check(self, ip: str) -> IpDecision:
        """Read-only: may this IP attempt? Call before doing any work."""
        now = self._clock()
        with self._lock:
            blocked = self._blocked_until.get(ip)
            if blocked is not None:
                remaining = blocked - now
                if remaining > 0:
                    return IpDecision(False, max(1, int(math.ceil(remaining))), self.limit)
                self._blocked_until.pop(ip, None)
            return IpDecision(True, 0, len(self._trim(ip, now)))

    def record_failure(self, ip: str) -> IpDecision:
        """Count one failure; returns the decision for the NEXT attempt."""
        now = self._clock()
        with self._lock:
            blocked = self._blocked_until.get(ip)
            if blocked is not None and blocked - now > 0:
                return IpDecision(False, max(1, int(math.ceil(blocked - now))), self.limit)
            bucket = self._trim(ip, now)
            bucket.append(now)
            if len(bucket) >= self.limit:
                self._blocked_until[ip] = now + self.block_seconds
                bucket.clear()
                self._evict(now)
                return IpDecision(False, self.block_seconds, self.limit)
            self._evict(now)
            return IpDecision(True, 0, len(bucket))

    def reset(self, ip: str) -> None:
        """Forget an IP. Called on a successful login from it."""
        with self._lock:
            self._failures.pop(ip, None)
            self._blocked_until.pop(ip, None)

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            now = self._clock()
            return {
                "tracked_ips": len(self._failures),
                "blocked_ips": sum(1 for until in self._blocked_until.values() if until > now),
            }


class RateLimited(Exception):
    """Raise to produce a 429. `retry_after` is whole seconds."""

    def __init__(self, retry_after: int, message: str = GENERIC_LOGIN_FAILURE_MESSAGE) -> None:
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))
        self.message = message
        self.status_code = HTTP_TOO_MANY_REQUESTS


def retry_after_headers(seconds: int) -> Dict[str, str]:
    """`Retry-After` in delta-seconds (RFC 9110 §10.2.3)."""
    return {"Retry-After": str(max(1, int(seconds)))}


# --------------------------------------------------------------------------- #
# 9. Client IP — get this wrong and per-IP limiting inverts into a self-DoS
# --------------------------------------------------------------------------- #

def _parse_networks(raw: str) -> Tuple:
    # The rule is settings.parse_networks (TASK-1433); the log line stays here.
    return settings.parse_networks(raw, on_bad=lambda chunk: logger.error(
        "%s contains an unparseable entry %r — ignoring it", TRUSTED_PROXY_ENV, chunk))


def trusted_proxy_networks() -> Tuple:
    """Networks whose `CF-Connecting-IP` header is believed. EMPTY by default."""
    return _parse_networks(os.environ.get(TRUSTED_PROXY_ENV, ""))


def client_ip(
    peer: Optional[str],
    headers: Optional[Mapping[str, str]] = None,
    *,
    trusted: Optional[Sequence] = None,
) -> str:
    """The address the limiter counts against.

    `CF-Connecting-IP` is read ONLY when the socket peer is inside the trusted
    proxy set — i.e. only when the request genuinely arrived from cloudflared.
    Unconfigured means the header is ignored, never that it is trusted.
    `X-Forwarded-For` is never read at all: on the LAN vhost nothing legitimately
    sets it and anyone may.

    Behind the tunnel every public request has the SAME socket peer, so keying
    the ceiling on the peer would turn a per-IP limit into a global one — the
    first attacker to trip it locks out every other public visitor.
    """
    peer_clean = (peer or "").strip()
    networks = trusted_proxy_networks() if trusted is None else tuple(trusted)
    if networks and headers:
        try:
            peer_addr = ipaddress.ip_address(peer_clean)
        except ValueError:
            peer_addr = None
        if peer_addr is not None and any(peer_addr in net for net in networks):
            for name in ("cf-connecting-ip", "CF-Connecting-IP"):
                candidate = headers.get(name)
                if candidate:
                    candidate = candidate.strip()
                    try:
                        return str(ipaddress.ip_address(candidate))
                    except ValueError:
                        logger.warning("CF-Connecting-IP was not an IP address; using the socket peer")
                    break
    return peer_clean or "unknown"


# --------------------------------------------------------------------------- #
# 10. Generated secrets
# --------------------------------------------------------------------------- #

def generate_temp_password() -> str:
    """A minted handover credential: `secrets.token_urlsafe(16)` — 128 bits.

    22 URL-safe characters, so it satisfies the 15 code point minimum by
    construction. Generated, never chosen — the operator relays it, never
    invents it (ASVS 6.4.6). Single use, 72 h expiry, hashed at rest in the same
    column as any other password, shown ONCE at mint time.
    """
    return secrets.token_urlsafe(TEMP_PASSWORD_ENTROPY_BYTES)


def temp_credential_expires_at(now: Optional[datetime] = None) -> datetime:
    """`auth_user.temp_expires_at` — absolute, checked server-side against now()."""
    return _aware(now or datetime.now(timezone.utc)) + timedelta(hours=TEMP_CREDENTIAL_TTL_HOURS)


def generate_service_token() -> Tuple[str, str]:
    """Return `(prefix, token)` where token is `atlas_svc_<6 hex>_<43 urlsafe>`.

    256 bits of CSPRNG in the secret half. TASK-968 owns storage: such a token
    is stored as a keyed SHA-256 digest and compared with `compare_digest`, NOT
    with Argon2 — there is nothing to guess in 256 bits of random, and running a
    memory-hard KDF per API request is a self-inflicted DoS.
    """
    prefix = secrets.token_hex(3)  # 6 hex characters, indexed and unique per row
    secret = secrets.token_urlsafe(SERVICE_TOKEN_ENTROPY_BYTES)
    return prefix, "%s%s_%s" % (SERVICE_TOKEN_PREFIX, prefix, secret)


def constant_time_equals(left: object, right: object) -> bool:
    """`hmac.compare_digest` for every non-Argon2 secret comparison.

    Session id digests, bearer token digests, CSRF tokens. Plain `==` on a digest
    is a timing oracle.
    """
    if isinstance(left, str) and isinstance(right, str):
        return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
    if isinstance(left, (bytes, bytearray)) and isinstance(right, (bytes, bytearray)):
        return hmac.compare_digest(bytes(left), bytes(right))
    return False


def digest_secret(value: str, *, label: str = "service-token-v1", key: Optional[bytes] = None) -> str:
    """Keyed SHA-256 digest, hex, 64 chars — for high-entropy secrets only.

    Sessions ids, CSRF tokens and service tokens. NEVER for a user password.
    TASK-968 passes its own `ATLAS_TOKEN_PEPPER` as `key=` if it keeps the two
    peppers separate.
    """
    return _keyed_digest(label, value.encode("utf-8"), key=key).hex()


# --------------------------------------------------------------------------- #
# 11. Operational self-checks
# --------------------------------------------------------------------------- #

def self_check() -> Dict[str, object]:
    """A dict safe to surface on `/readyz`. Contains no secret and no digest."""
    return {
        "hash_scheme": HASH_SCHEME,
        "argon2": {
            "type": "argon2id",
            "memory_cost": ARGON2_MEMORY_COST,
            "time_cost": ARGON2_TIME_COST,
            "parallelism": ARGON2_PARALLELISM,
            "hash_len": ARGON2_HASH_LEN,
            "salt_len": ARGON2_SALT_LEN,
        },
        "pepper_present": True,  # unreachable otherwise: import would have raised
        "policy": {"min_length": MIN_PASSWORD_LENGTH, "max_length": MAX_PASSWORD_LENGTH},
        "breach_mode": os.environ.get(BREACH_MODE_ENV, "remote"),
        "breach_corpus": corpus_status(),
        "dummy_hash_ready": bool(_DUMMY_HASH),
    }


def measure_verify_ms(repeat: int = 21) -> float:
    """Median Argon2id VERIFY latency in ms, over `repeat` runs.

    Run this INSIDE the atlas-api container on the server — a desktop number
    is not evidence. Accepted band is 50-250 ms; above 250 ms lower `time_cost`
    before `memory_cost` and re-measure.
    """
    peppered = _peppered(normalise_password("x" * 16))
    stored = _HASHER.hash(peppered)
    timings = []
    for _ in range(repeat):
        started = time.perf_counter()
        _HASHER.verify(stored, peppered)
        timings.append((time.perf_counter() - started) * 1000.0)
    timings.sort()
    return timings[len(timings) // 2]


if __name__ == "__main__":  # pragma: no cover - operator tooling
    import json
    import sys

    if "--measure" in sys.argv:
        median = measure_verify_ms()
        low, high = VERIFY_TARGET_MS
        verdict = "OK" if low <= median <= high else ("TOO SLOW" if median > high else "TOO FAST")
        print("argon2id verify median: %.1f ms over 21 runs -> %s (band %.0f-%.0f ms)"
              % (median, verdict, low, high))
    else:
        print(json.dumps(self_check(), indent=2))
