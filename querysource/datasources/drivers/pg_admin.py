"""
pg_admin — PostgreSQL datasource using full-access DB* credentials.

Distinct registered datasource from ``postgres`` (which uses read-only
PG_* credentials). Policy gating decides which users can list/use this
datasource. Admins receive policies granting ``datasource:list`` and
``datasource:use`` for ``pg_admin``; regular users are restricted to
the ``postgres`` datasource.

See FEAT-091 (pbac-support), Module 9.
"""
from ...conf import (
    # full-access DB admin credentials
    DBHOST,
    DBPORT,
    DBUSER,
    DBPWD,
    DBNAME,
    default_dsn,
)
from .pg import pgDriver


class pg_adminDriver(pgDriver):  # noqa: N801 — follows postgresDriver naming convention
    """PostgreSQL driver using full-access DB* credentials (admin tier)."""
    driver: str = 'pg_admin'
    name: str = 'pg_admin'
    defaults: str = default_dsn
    credential_prefix: str = "DB"  # FEAT-091: resolves from DB* env vars


try:
    pg_admin_default = pg_adminDriver(
        dsn=default_dsn,
        host=DBHOST,
        port=DBPORT,
        database=DBNAME,
        user=DBUSER,
        password=DBPWD,
    )
except ValueError:
    # DB* env vars not configured — silently skip this driver.
    # default_sources() in datasource.py:40 skips None instances.
    pg_admin_default = None
