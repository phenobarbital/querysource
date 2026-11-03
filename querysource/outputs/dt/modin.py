from querysource.exceptions import QueryException

from .abstract import OutputFormat


class modinFormat(OutputFormat):
    """Returns a Pandas Dataframe from a Resultset.

    .. warning:: Best-effort on pandas >= 3

       modin <= 0.37.1 declares ``pandas < 2.4``.  This output format is
       available only when modin imports successfully; on pandas 3 it may
       raise at init time, which is surfaced as a ``QueryException``.
       Install with ``uv pip install querysource[modin]`` (pip cannot
       resolve the extra against pandas >= 3).
    """
    def __init__(self):
        try:
            import modin.config as modin_cfg
            from distributed import Client
        except Exception as exc:  # modin may fail on import against pandas 3
            raise QueryException(
                "modinFormat requires a working modin install "
                f"(pip install querysource[modin]; modin is best-effort on pandas 3): {exc}"
            ) from exc
        from ...conf import MODIN_SERVER

        try:
             # Check if client is already initialized
             self.client = Client.current()
        except ValueError:
             self.client = Client(MODIN_SERVER)

        modin_cfg.Engine.put("dask")  # Modin will use HDK
        modin_cfg.IsExperimental.put(True)

    async def serialize(self, result, error, *args, **kwargs):
        import modin.pandas as pd
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
            error = Exception(f"ModinFormat: Error on Data: error: {err}")
        finally:
            return (df, error)
