import pandas as pd
import polars as polar
from .abstract import OutputFormat


class polarsFormat(OutputFormat):
    """
    Returns a PyPolars Dataframe from a Resultset
    """
    async def serialize(self, result, error, *args, **kwargs):
        df = None
        try:
            result = [dict(row) for row in result]
            df = polar.from_dicts(result, infer_schema_length=20)
            self._result = df
        except ValueError as err:
            print(err)
            error = Exception(f"PolarFormat: Error Parsing Column: {err}")
        except Exception as err:
            self.logger.exception(
                f'Polars Serialization Error: {err}',
                stack_info=True
            )
            error = Exception(f"PolarFormat: Error on Data: error: {err}")
        finally:
            return (df, error)
