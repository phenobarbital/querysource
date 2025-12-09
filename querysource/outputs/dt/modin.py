from .abstract import OutputFormat


class modinFormat(OutputFormat):
    """
    Returns a Pandas Dataframe from a Resultset
    """
    def __init__(self):
        import modin.config as modin_cfg
        from distributed import Client
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
