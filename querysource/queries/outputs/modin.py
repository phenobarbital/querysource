import modin.config as modin_cfg
import modin.pandas as pd  # noqa: F401
from distributed import Client

from .abstract import OutputFormat

client = Client('tcp://127.0.0.1:8786')

class modinFormat(OutputFormat):
    """
    Returns a Pandas Dataframe from a Resultset
    """
    def __init__(self):
        modin_cfg.Engine.put("dask")  # Modin will use HDK
        modin_cfg.IsExperimental.put(True)

    async def serialize(self, result, error, *args, **kwargs):
        df = None
        try:
            # result = [dict(row) for row in result]
            df = pd.DataFrame(
                data=result,
                *args,
                **kwargs
            )
            self._result = df
        except pd.errors.EmptyDataError as err:
            error = Exception(f"Error with Empty Data: error: {err}")
        except pd.errors.ParserError as err:
            self.logger.error(error)
            error = Exception(f"Error parsing Data: error: {err}")
        except ValueError as err:
            self.logger.error(error)
            error = Exception(f"Error Parsing a Column, error: {err}")
        except Exception as err:
            self.logger.error(error)
            error = Exception(f"PandasFormat: Error on Data: error: {err}")
        finally:
            return (df, error)
