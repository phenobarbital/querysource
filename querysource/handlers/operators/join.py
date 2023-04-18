import pandas as pd
from navconfig.logging import logging
from querysource.exceptions import (
    DataNotFound,
    DriverError,
    QueryException
)

class Join:
    def __init__(self, data: dict, **kwargs) -> None:
        self._type: str = 'left'
        try:
            self._type = kwargs['type']
            del kwargs['type']
        except KeyError:
            pass
        self._backend = 'pandas'
        self.data = data
        for k, v in kwargs.items():
            setattr(self, k, v)

    async def start(self):
        for _, data in self.data.items():
            ## TODO: add support for polars and datatables
            if isinstance(data, pd.DataFrame):
                self._backend = 'pandas'
            else:
                raise DriverError(
                    f'Wrong type of data for JOIN, required Pandas dataframe: {type(data)}'
                )
        try:
            self.df1 = self.data.pop(self.left)
        except (KeyError, IndexError) as ex:
            raise DriverError(
                f"Missing LEFT Dataframe on Data: {self.data[self.left]}"
            ) from ex
        ### check for empty
        if self.df1.empty:
            raise DataNotFound(
                "Empty Left Dataframe"
            )

    async def run(self):
        await self.start()
        args = {}
        if hasattr(self, 'no_copy'):
            args['copy'] = self.no_copy
        if not self._type:
            self._type = 'inner'
            args['left_index'] = True
        if hasattr(self, 'args') and isinstance(self.args, dict):
            args = {**args, **self.args}
        if hasattr(self, 'operator'):
            operator = self.operator
        else:
            operator = 'and'
            if hasattr(self, 'using'):
                args['on'] = self.using
            else:
                args['left_index'] = True
        try:
            if operator == 'and':
                ldf = None
                for name, data in self.data.items():
                    if data.empty:
                        logging.warning(f'Empty Dataframe: {name}')
                        continue
                    if ldf is None:
                        ldf = self.df1
                    ldf = pd.merge(
                        ldf,
                        data,
                        how=self._type,
                        suffixes=('_left', '_right'),
                        **args
                    )
                    ldf.drop(ldf.columns[ldf.columns.str.contains('_left')], axis=1, inplace=True)
                    # merge.append(ldf)
                    ldf.reset_index(drop=True)
                df = ldf
                if df is None:
                    raise DataNotFound(
                        "Empty Result Dataframe"
                    )
                df.is_copy = None
            else:
                pass
            print('::: Printing Column Information === ')
            for column, t in df.dtypes.items():
                print(column, '->', t, '->', df[column].iloc[0])
            return df
        except DataNotFound:
            raise
        except (ValueError, KeyError) as err:
            raise QueryException(
                f'Cannot Join with missing Column: {err!s}'
            ) from err
        except Exception as err:
            raise QueryException(
                f"Unknown JOIN error {err!s}"
            ) from err
