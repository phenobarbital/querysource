"""
querysource.outputs.destinations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

MultiQuery destination components.

This package exports the :data:`DESTINATION_REGISTRY` dict that maps YAML
step-names to destination classes, and the :func:`get_destination` factory
function for safe registry lookups.

New destinations register themselves by importing their class and adding an
entry to ``DESTINATION_REGISTRY`` at the bottom of this file.
"""
import logging as _logging
from typing import Union
import pandas as pd
from ..tables import TableOutput
from ...exceptions import OutputError
from .abstract import AbstractDestination

_pkg_logger = _logging.getLogger(__name__)


class TableOutputAdapter(AbstractDestination):
    """
    Thin adapter that wraps the existing :class:`~querysource.outputs.tables.TableOutput`
    so it can be registered in :data:`DESTINATION_REGISTRY` alongside the new
    destination classes.

    All constructor arguments are forwarded verbatim to :class:`TableOutput`.
    """

    # User-facing catalog override. The Python class is ``TableOutputAdapter``
    # for historical reasons, but the YAML step-name and UI display name are
    # ``TableOutput`` — which is what the pipeline expects. The wrapped
    # ``TableOutput`` accepts **kwargs without declared attributes, so the
    # schema is hand-crafted from the keys observed in real YAML configs.
    _catalog = {
        "display_name": "TableOutput",
        "description": (
            "Write a DataFrame to a database table. Supports PostgreSQL, "
            "MySQL, BigQuery, MongoDB, DocumentDB, and RethinkDB."
        ),
        "usage": (
            "Use as a Destination step to persist a pipeline result into a "
            "relational or document database. Set ``flavor`` to pick the "
            "backend, ``schema`` + ``tablename`` to address the target, and "
            "``if_exists`` to control append / replace behaviour. Provide "
            "``pk`` for primary-key-aware upserts and ``jsonb_columns`` to "
            "preserve dict/list columns as JSONB on PostgreSQL."
        ),
        "icon": "database",
        "attributes": [
            {
                "name": "flavor",
                "type": "str",
                "required": False,
                "default": "postgresql",
                "description": (
                    "Backend engine: ``postgresql`` (default), ``postgres``, "
                    "``mysql``, ``bigquery``, ``mongodb``, ``documentdb``, "
                    "or ``rethink``."
                ),
            },
            {
                "name": "schema",
                "type": "str",
                "required": False,
                "default": "public",
                "description": "Database schema (relational backends).",
            },
            {
                "name": "tablename",
                "type": "str",
                "required": True,
                "default": None,
                "description": "Target table or collection name.",
            },
            {
                "name": "if_exists",
                "type": "str",
                "required": False,
                "default": "append",
                "description": (
                    "Conflict behaviour: ``append``, ``replace``, ``fail``, "
                    "or backend-specific upsert keys."
                ),
            },
            {
                "name": "pk",
                "type": "list",
                "required": False,
                "default": [],
                "description": "Primary-key column names (for upsert).",
            },
            {
                "name": "fk",
                "type": "str",
                "required": False,
                "default": None,
                "description": "Foreign-key column reference.",
            },
            {
                "name": "constraint",
                "type": "list",
                "required": False,
                "default": None,
                "description": "Additional unique-constraint columns.",
            },
            {
                "name": "jsonb_columns",
                "type": "list",
                "required": False,
                "default": [],
                "description": (
                    "Columns to persist as PostgreSQL JSONB (preserves dict "
                    "and list values)."
                ),
            },
            {
                "name": "truncate",
                "type": "bool",
                "required": False,
                "default": False,
                "description": "Truncate the table before writing.",
            },
        ],
        "json_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "title": "TableOutput",
            "description": "Write a DataFrame to a database table.",
            "properties": {
                "flavor": {
                    "type": "string",
                    "enum": [
                        "postgresql", "postgres", "mysql", "bigquery",
                        "mongodb", "documentdb", "rethink",
                    ],
                    "default": "postgresql",
                },
                "schema": {"type": "string", "default": "public"},
                "tablename": {"type": "string"},
                "if_exists": {
                    "type": "string",
                    "default": "append",
                    "description": "append | replace | fail | upsert key.",
                },
                "pk": {"type": "array", "items": {"type": "string"}},
                "fk": {"type": "string"},
                "constraint": {"type": "array", "items": {"type": "string"}},
                "jsonb_columns": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "truncate": {"type": "boolean", "default": False},
            },
            "required": ["tablename"],
            "additionalProperties": True,
        },
        "example": (
            '{\n'
            '  "TableOutput": {\n'
            '    "flavor": "postgres",\n'
            '    "schema": "public",\n'
            '    "tablename": "target_table",\n'
            '    "if_exists": "append",\n'
            '    "pk": ["id"]\n'
            '  }\n'
            '}'
        ),
    }

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        # Call AbstractDestination.__init__ to set self.data and self.logger.
        super().__init__(data, **kwargs)
        # Build the wrapped TableOutput instance.
        self._table_output = TableOutput(data=data, **kwargs)

    async def run(self) -> Union[dict, pd.DataFrame]:
        """Delegate entirely to the wrapped :class:`TableOutput`."""
        result = await self._table_output.run()
        self.data = result
        return result

    async def close(self) -> None:
        """No-op — TableOutput closes its engine inside :meth:`run`."""


# ---------------------------------------------------------------------------
# Destination Registry
# ---------------------------------------------------------------------------
# Maps YAML step-names to destination classes.
# TableOutput is registered under both conventional spellings for backward
# compatibility with existing MultiQuery YAML configs.
DESTINATION_REGISTRY: dict[str, type[AbstractDestination]] = {
    "tableOutput": TableOutputAdapter,
    "TableOutput": TableOutputAdapter,
}

# New destinations are imported and registered below as their tasks complete.
# (Each destination module appends its entry here.)
try:
    from querysource.queries.multi.destinations.sharepoint import ToSharepoint
    DESTINATION_REGISTRY["ToSharepoint"] = ToSharepoint
except ImportError:
    _pkg_logger.debug(
        "ToSharepoint destination not available: msgraph-sdk or azure-identity not installed"
    )

try:
    from querysource.queries.multi.destinations.s3 import ToS3
    DESTINATION_REGISTRY["ToS3"] = ToS3
except ImportError:
    _pkg_logger.debug(
        "ToS3 destination not available: aioboto3 not installed"
    )

try:
    from querysource.queries.multi.destinations.table import TableDestination
    DESTINATION_REGISTRY["Table"] = TableDestination
except ImportError:
    _pkg_logger.debug(
        "Table destination not available"
    )

try:
    from querysource.queries.multi.destinations.dwh import DWHDestination
    DESTINATION_REGISTRY["DWH"] = DWHDestination
except ImportError:
    _pkg_logger.debug(
        "DWH destination not available"
    )


def get_destination(step_name: str) -> type[AbstractDestination]:
    """
    Return the destination class registered under *step_name*.

    :param step_name: The YAML step key (e.g. ``"ToSharepoint"``, ``"tableOutput"``).
    :returns: The destination class.
    :raises OutputError: If *step_name* is not registered.
    """
    cls = DESTINATION_REGISTRY.get(step_name)
    if cls is None:
        registered = ", ".join(sorted(DESTINATION_REGISTRY))
        raise OutputError(
            f"Unknown destination step '{step_name}'. "
            f"Registered destinations: {registered}"
        )
    return cls


__all__ = (
    "AbstractDestination",
    "TableOutputAdapter",
    "DESTINATION_REGISTRY",
    "get_destination",
)
