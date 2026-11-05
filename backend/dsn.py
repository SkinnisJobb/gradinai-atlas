"""The database URL's driver: a URL that names none gets psycopg2, the one the image ships (TASK-1501).

WHY. SQLAlchemy 2.1 resolves a URL with no driver, `postgresql://...`, to psycopg 3, which the
image does not carry: such a URL failed at the first connect with `No module named 'psycopg'`.
`postgres://...` (Heroku's spelling, and some providers') is not a dialect SQLAlchemy 2 knows at
all. CloudNativePG writes a bare `postgresql://` URL into its `fqdn-uri` key, so an operator who
pointed `ATLAS_DB_URL_FILE` at it got a migrate Job that could not start (TASK-1485 bridged that
with an init container that rewrites the scheme; see `deploy/openshift/base/db_url.py`).

WHAT. `with_default_driver(url)` rewrites exactly those two driver names, `postgresql` and
`postgres`, to `postgresql+psycopg2`. A URL that names a driver, `postgresql+psycopg2://` as every
working install has, or any other, comes back as the SAME string, untouched, so this is
behaviour-neutral for every install that works today and there is no setting for it. Everything
else in the URL (role, password, host, port, database, the query parameters libpq reads such as
`sslmode`) is carried over unchanged.

WHERE. Every place that reads `ATLAS_DB_URL(_FILE)` and hands it to SQLAlchemy calls this once,
right after reading it: `db.py` (atlas-api and atlas-migrate), `admin.py`, `crunch_backup.db_url()`
(backups, and through it `backup_restore.py`), `bind_unbound.py` and the one-off `migrate_*.py`
scripts. `pg_dump`/`pg_restore` never see the URL itself: `crunch_backup._conninfo` builds a
libpq conninfo from its parts, so the driver name does not reach them.

SECRECY. The URL carries the database password. Nothing here prints or logs it, and a URL that
does not parse raises a `ValueError` that does not quote it (SQLAlchemy's own parse error does).
"""
from __future__ import annotations

#: The driver names that mean "no driver given". Matched exactly, so `postgresql+asyncpg` or
#: `postgresql+psycopg` (an operator who installed psycopg 3 on purpose) are left alone.
BARE_DRIVERNAMES = ("postgresql", "postgres")
#: What a bare URL becomes: psycopg2, which `requirements-lock.txt` pins and the image ships.
DEFAULT_DRIVERNAME = "postgresql+psycopg2"


class DsnError(ValueError):
    """The URL could not be parsed. The message never contains the URL."""


def with_default_driver(url: str) -> str:
    """`url`, with `postgresql+psycopg2` as its driver when it names none; otherwise unchanged.

    Raises `DsnError` (without the URL in its message) when `url` does not parse as a URL.
    """
    from sqlalchemy.engine import make_url  # here, so importing this module costs nothing

    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 - SQLAlchemy's message quotes the whole URL, password and all
        raise DsnError(
            "the database URL could not be parsed (the value is not shown: it holds the password); "
            "expected postgresql+psycopg2://ROLE:PASSWORD@HOST:5432/DATABASE"
        ) from None
    if parsed.drivername not in BARE_DRIVERNAMES:
        return url
    return parsed.set(drivername=DEFAULT_DRIVERNAME).render_as_string(hide_password=False)
