"""The sign-in settings, as environment names, for the commands that must not configure sign-in.

`auth` configures the identity providers when it is imported (`auth._load_identity`): with
ATLAS_AUTH_PROVIDERS naming oidc it opens the OIDC group map and client secret (or key) that the
ATLAS_OIDC_*_FILE settings name, and raises MissingSecretError on a file it cannot read. On the OSS
install those files are compose secrets mounted into atlas-api only (deploy/oss/oidc.yml), while
the one-shot and side containers (atlas-migrate, atlas-backup) read the same `.env`. A command run
there that imports `auth` without signing anybody in would stop at import.

Two such commands drop these names from their OWN process environment, as a program, before
`auth` is imported, so `auth` comes up with the default provider (`local`) and opens no file:

  * `atlas-admin backup …` (admin.py, TASK-1437): dump, verify, restore, roll back.
  * `atlas-migrate upgrade` (migrate_cli.py, TASK-1457): the schema steps of startup. The
    sign-in tables are created whatever the providers are (`models_auth.create_all`), and the
    OIDC flow tables are created on first use by atlas-api (`identity.flows.ensure`), so the
    schema it builds does not depend on these settings.

This module imports nothing from the backend on purpose: it has to be usable before `auth`.
Nothing else changes: atlas-api never runs either module as a program, and the other admin verbs
still configure sign-in as before.
"""
from __future__ import annotations

from typing import MutableMapping

IDENTITY_ENV_NAMES = ("ATLAS_AUTH_PROVIDERS", "ATLAS_LOCAL_LOGIN")
#: Families, not names: every member is catalogued on its own (settings_catalog), whose scan
#: reads each `ATLAS_*` string literal here as one setting's name, hence the formatting.
IDENTITY_ENV_PREFIXES = tuple("ATLAS_%s_" % family for family in ("OIDC", "OAUTH"))


def is_identity_env(name: str) -> bool:
    return name in IDENTITY_ENV_NAMES or name.startswith(IDENTITY_ENV_PREFIXES)


def drop_identity_env(environ: MutableMapping[str, str]) -> list:
    """Remove the sign-in settings from `environ`; return the names removed, sorted."""
    names = sorted(n for n in environ if is_identity_env(n))
    for name in names:
        del environ[name]
    return names
