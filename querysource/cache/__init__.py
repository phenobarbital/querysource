"""
Cache Infraestructure for QuerySource.

Using a backend service to store the results of the queries.
Backends available:
- Redis
- BigQuery
- RethinkDB

"""

from .base import QueryCache


__all__ = (
    'QueryCache',
)
