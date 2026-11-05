"""Init container: give Atlas the CloudNativePG URL with the driver it ships (TASK-1485).

THE GAP. CloudNativePG's app Secret holds the database URL as `postgresql://...` (key `fqdn-uri`).
Atlas hands ATLAS_DB_URL to SQLAlchemy's create_engine unchanged (backend/db.py), and SQLAlchemy
2.1 resolves a URL with no driver to psycopg 3, which the image does not ship: it ships psycopg2.
`atlas-migrate upgrade` with the URL as CNPG writes it stops with
"ModuleNotFoundError: No module named 'psycopg'". (The research page's F3 assumed the 2.0
default, psycopg2.)

THE BRIDGE. This script, run in an init container of the same image, reads the projected
`fqdn-uri` and writes the same URL with the scheme `postgresql+psycopg2://` to a file in a
memory-backed emptyDir that only this pod mounts; ATLAS_DB_URL_FILE points at that file. The
main container never mounts the CNPG Secret. The value is never printed, logged or put in the
environment, and the file is mode 0400. The TLS settings stay in PGSSLMODE / PGSSLROOTCERT.
A URL that already names a driver is copied unchanged.

When Atlas accepts a driver-less postgresql:// URL itself, this init container and the
`db-url` volume go, and ATLAS_DB_URL_FILE points at /run/secrets/db/fqdn-uri directly.

    python -I /run/config/scripts/db_url.py
"""
import os
import sys

SOURCE = "/run/secrets/db/fqdn-uri"
TARGET = "/run/db-url/url"
BARE_SCHEMES = ("postgresql", "postgres")


def main():
    try:
        with open(SOURCE, "r", encoding="utf-8") as fh:
            url = fh.read().strip()
    except OSError as exc:
        print("db_url: cannot read %s (%s)" % (SOURCE, type(exc).__name__))
        return 1
    scheme, sep, rest = url.partition("://")
    if not sep or not rest:
        print("db_url: %s does not hold a URL" % SOURCE)
        return 1
    if scheme in BARE_SCHEMES:
        url = "postgresql+psycopg2://" + rest
    elif not scheme.startswith("postgresql+"):
        print("db_url: %s has the scheme %r, not a PostgreSQL one" % (SOURCE, scheme))
        return 1
    # A restarted pod reruns its init containers over the same emptyDir; the old file is 0400.
    try:
        os.unlink(TARGET)
    except FileNotFoundError:
        pass
    fd = os.open(TARGET, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o400)
    try:
        os.write(fd, url.encode("utf-8"))
    finally:
        os.close(fd)
    print("db_url: wrote %s (scheme %s)" % (TARGET, url.partition("://")[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
