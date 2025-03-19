"""
Iterable.

Output format returning a simple list of dictionaries.
"""
import pandas
try:
    from google.cloud.bigquery.table import RowIterator
except ImportError:
    RowIterator = None
from .abstract import OutputFormat


class iterFormat(OutputFormat):
    """
    Most Basic Definition of Format.
    """
    async def serialize(self, result, error, *args, **kwargs):
        if isinstance(result, pandas.DataFrame):
            data = result
        elif isinstance(result, (RowIterator, list)):
            data = [dict(row) for row in result]
        else:
            data = dict(result)
        return (data, error)
