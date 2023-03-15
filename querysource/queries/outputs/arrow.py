import logging
import pyarrow as pa
from .abstract import OutputFormat


class arrowFormat(OutputFormat):
    """
    Returns an Apache Arrow Table from a Resultset
    """
    async def serialize(self, result, error, *args, **kwargs):
        table = None
        try:
            names = result[0].keys()
            result = [dict(row) for row in result]
            table = pa.Table.from_arrays(
                result,
                names=names,
                *args,
                **kwargs
            )
            self._result = table
        except ValueError as err:
            self.logger.error(f'Arrow Serialization Error: {err}')
            error = Exception(
                f"arrowFormat: Error Parsing Column: {err}"
            )
        except Exception as err:
            self.logger.exception(
                f'Arrow Serialization Error: {err}',
                stack_info=True
            )
            error = Exception(
                f"arrowFormat: Error on Data: error: {err}"
            )
        finally:
            return (table, error)
