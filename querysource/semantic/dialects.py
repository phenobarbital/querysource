"""Driver → SQL dialect registry (FEAT-175 §7)."""
from __future__ import annotations

from .exceptions import SemanticResourceError

DRIVER_DIALECTS: dict[str, str] = {
    "pg": "postgres",
    "pg_admin": "postgres",
    "bigquery": "bigquery",
    "mysql": "mysql",
    "mariadb": "mysql",
    "sqlserver": "tsql",
    "oracle": "oracle",
    "sqlite": "sqlite",
}
NON_SQL_DRIVERS: frozenset[str] = frozenset(
    {
        "arangodb",
        "cassandra",
        "delta",
        "documentdb",
        "elastic",
        "hazel",
        "iceberg",
        "influx",
        "mongo",
        "redis",
        "rethink",
        "salesforce",
        "scylladb",
    }
)


def dialect_for_driver(driver: str) -> tuple[str, str | None]:
    """Return ``(dialect, warning)`` for a datasource driver.

    Non-SQL drivers cannot be compiled as semantic SQL queries. Drivers that
    are not in the explicit SQL registry use ANSI SQL and carry a warning.
    """
    if driver in NON_SQL_DRIVERS:
        raise SemanticResourceError(f"Driver {driver} is a non-SQL datasource and cannot compile semantic SQL")
    dialect = DRIVER_DIALECTS.get(driver)
    if dialect is not None:
        return dialect, None
    return "ansi", f"dialect not mapped for driver {driver}; ANSI SQL generated"


def datasource_driver(connection: str | None) -> tuple[str, str]:
    """Return ``(datasource_name, driver)`` for a resource ``connection`` without connecting.

    ``None`` selects the default QuerySource database. Used by the provider and the catalog
    endpoint so both resolve the same datasource and dialect.

    Raises:
        SemanticResourceError: If the named datasource is not registered.
    """
    from ..interfaces.connections import DATASOURCES  # noqa: PLC0415

    if connection is None:
        from ..connections import QueryConnection  # noqa: PLC0415

        return "default", QueryConnection().default_driver_name
    source = DATASOURCES.get(connection)
    if source is None:
        raise SemanticResourceError(f"Datasource '{connection}' not found")
    return connection, source.driver
