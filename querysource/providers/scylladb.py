"""ScyllaDB.

Data Provider for ScyllaDB.
"""
from ..parsers.cql import CQLParser
from .sql import sqlProvider


class scylladbProvider(sqlProvider):
    """scylladbProvider.

    Querysource Provider ScyllaDB with CQL Support.
    """
    __parser__ = CQLParser

    async def columns(self):
        # TODO: getting the columns of a prepared sentence
        return self._columns

    async def dry_run(self):
        """Running Build Query and return the Query to be executed (without execution).
        """
        return (self._query, None)
