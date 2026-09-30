"""ExecuteSQL destination: run guarded SQL on PostgreSQL (``DB*`` credentials), pass data through."""
from typing import List, Union

import pandas as pd

from querysource.exceptions import OutputError
from querysource.interfaces.guarded_sql import GuardedSQLError, execute_guarded, guard_statements
from querysource.outputs.destinations.abstract import AbstractDestination

SUPPORTED_DRIVERS = frozenset({"pg", "postgres", "postgresql"})
DEFAULT_TIMEOUT: float = 3600.0


class ExecuteSQLDestination(AbstractDestination):
    """Run guarded SQL statements on PostgreSQL (DB* credentials) in one transaction.

    Step name: ``ExecuteSQL``. Returns the input data unchanged.
    """

    _catalog = {
        "display_name": "ExecuteSQL",
        "icon": "terminal",
        "description": (
            "Run guarded maintenance SQL on PostgreSQL inside one transaction "
            "and pass the pipeline data through unchanged."
        ),
        "usage": (
            "Use as an Output step, typically before ``Table``, to run "
            "maintenance SQL such as a range DELETE before an append. All "
            "statements of the step run in ONE transaction with the full-access "
            "``DB*`` credentials (all-or-nothing). Destructive statements "
            "(DROP, TRUNCATE, ALTER ... DROP, DO blocks, GRANT/REVOKE, role "
            "management, COPY ... PROGRAM) and transaction control "
            "(BEGIN/COMMIT/ROLLBACK/SAVEPOINT) are rejected before anything "
            "reaches the database. The input data is returned unchanged, so a "
            "``Table`` step can follow."
        ),
        "attributes": [
            {
                "name": "sql",
                "type": "str | list",
                "required": True,
                "default": "",
                "description": "One SQL script or a list of scripts (each may hold several statements).",
            },
            {
                "name": "driver",
                "type": "str",
                "required": False,
                "default": "pg",
                "description": "Backend driver. Only PostgreSQL: ``pg`` / ``postgres`` / ``postgresql``.",
            },
            {
                "name": "timeout",
                "type": "float",
                "required": False,
                "default": 3600,
                "description": "Per-statement timeout in seconds (must be > 0).",
            },
        ],
        "json_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "title": "ExecuteSQL",
            "description": "Run guarded SQL on PostgreSQL in one transaction.",
            "properties": {
                "sql": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
                "driver": {
                    "type": "string",
                    "enum": ["pg", "postgres", "postgresql"],
                    "default": "pg",
                },
                "timeout": {"type": "number", "exclusiveMinimum": 0, "default": 3600},
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
        "example": (
            '{\n'
            '  "Output": [\n'
            '    {\n'
            '      "ExecuteSQL": {\n'
            '        "sql": "DELETE FROM wm_assembly.employee_detail_profile '
            'WHERE activity_date >= (date_trunc(\'month\', CURRENT_DATE) - INTERVAL \'5 months\')::date"\n'
            '      }\n'
            '    },\n'
            '    {\n'
            '      "Table": {\n'
            '        "schema": "wm_assembly",\n'
            '        "table": "employee_detail_profile",\n'
            '        "method": "append"\n'
            '      }\n'
            '    }\n'
            '  ]\n'
            '}'
        ),
    }

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        """Read ``sql`` (str | list[str], required), ``driver`` (default ``pg``), ``timeout`` (s, default 3600).

        Raises:
            OutputError: missing/empty ``sql``, non-string items, unsupported driver, timeout <= 0.
        """
        super().__init__(data, **kwargs)
        sql = kwargs.get("sql")
        scripts: List[str] = [sql] if isinstance(sql, str) else sql if isinstance(sql, list) else []
        if not scripts or any(not isinstance(s, str) or not s.strip() for s in scripts):
            raise OutputError(
                "ExecuteSQL: 'sql' is required and must be a non-empty string or list of non-empty strings",
                category="data",
            )
        driver = str(kwargs.get("driver", "pg") or "pg").lower()
        if driver not in SUPPORTED_DRIVERS:
            raise OutputError(
                f"ExecuteSQL: unsupported driver '{driver}'. Supported: {', '.join(sorted(SUPPORTED_DRIVERS))}",
                category="data",
            )
        timeout = kwargs.get("timeout", 3600)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise OutputError(f"ExecuteSQL: 'timeout' must be a number of seconds > 0, got {timeout!r}", category="data")
        self._sql: List[str] = scripts
        self._driver: str = "pg"
        self._timeout: float = float(timeout)
        self.results: List[str] = []

    async def run(self) -> Union[dict, pd.DataFrame]:
        """``guard_statements`` → ``execute_guarded``; store ``self.results``.

        Returns:
            ``self.data`` unchanged.

        Raises:
            OutputError: wraps ``GuardedSQLError`` keeping its ``category``.
        """
        try:
            statements = guard_statements(self._sql)
            self.results = await execute_guarded(statements, timeout=self._timeout)
        except GuardedSQLError as err:
            raise OutputError(str(err), category=err.category) from err
        self.logger.info("ExecuteSQL: %d statement(s) committed: %s", len(self.results), self.results)
        return self.data
