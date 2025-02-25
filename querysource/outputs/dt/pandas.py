from decimal import Decimal
import pandas as pd
try:
    from google.cloud.bigquery.table import RowIterator
except ImportError:
    RowIterator = None
from .abstract import OutputFormat


class pandasFormat(OutputFormat):
    """
    Returns a Pandas Dataframe from a Resultset
    """
    async def serialize(self, result, error, *args, **kwargs):
        df = None
        try:
            if isinstance(result, RowIterator):
                # convert directy into pandas format
                df = result.to_dataframe()
            elif isinstance(result, list):
                result = [dict(row) for row in result]
                df = pd.DataFrame(
                    data=result,
                    *args,
                    **kwargs
                )
            elif isinstance(result, dict):
                df = pd.DataFrame(
                    data=[result],
                    *args,
                    **kwargs
                )
            elif isinstance(result, pd.DataFrame):
                df = result
            else:
                df = pd.DataFrame(
                    data=result,
                    *args,
                    **kwargs
                )
            ## TODO: adding mapping
            df.infer_objects()
            df.convert_dtypes(
                infer_objects=True,
                convert_string=True,
                convert_integer=True,
                convert_floating=True
            )
            ## convert Decimal columns to floats:
            for col in df.columns:
                df[col] = df[col].apply(lambda x: float(x) if isinstance(x, Decimal) else x)
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
