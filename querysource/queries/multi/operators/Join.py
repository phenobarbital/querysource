import pandas as pd
from uuid import uuid4
from navconfig.logging import logging
from ....exceptions import (
    DataNotFound,
    DriverError,
    QueryException
)

class Join:
    def __init__(self, data: dict, **kwargs) -> None:
        self._type: str = kwargs.get('type', 'inner')
        self._backend = 'pandas'
        self.data = data
        # Left Operator
        self._left = kwargs.pop('left', None)
        # Right Operator
        self._right = kwargs.pop('right', None)
        for k, v in kwargs.items():
            setattr(self, k, v)

    async def start(self):
        for name, data in self.data.items():
            ## TODO: add support for polars and datatables
            if isinstance(data, pd.DataFrame):
                self._backend = 'pandas'
            else:
                raise DriverError(
                    f'Wrong type of data for JOIN, required Pandas dataframe: {type(data)}'
                )

    async def run(self):
        await self.start()
        args = {}
        if hasattr(self, 'no_copy'):
            args['copy'] = self.no_copy
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
                if self._left and self._right:
                    # making a Join between 2 dataframes
                    df1 = self.data.pop(self._left)
                    df2 = self.data.pop(self._right)
                    try:
                        if df1.empty:
                            raise DataNotFound(
                                f'Empty Dataframe: {self._left}'
                            )
                        if df2.empty:
                            raise DataNotFound(
                                f'Empty Dataframe: {self._right}'
                            )
                        df = self._join(df1=df1, df2=df2, **args)
                        self.data[f"{self._left}.{self._right}"] = df
                        return self.data
                    finally:
                        del df1
                        del df2
                elif self._right:
                    # if right, then left is last dataframe attached:
                    key, df1 = self.data.popitem()
                    df2 = self.data.pop(self._right)
                    try:
                        df = self._join(df1=df1, df2=df2, **args)
                        self.data[f"{key}.{self._right}"] = df
                        return self.data
                    finally:
                        del df1
                        del df2
                elif self._left:
                    # if left, will be joined with all dataframes on data:
                    df1 = self.data.pop(self._left)
                else:
                    df1 = list(self.data.values())[0]
                # on both cases, iterate over all dataframes:
                ldf = None
                for name, data in list(self.data.items()):
                    if data.empty:
                        logging.warning(
                            f'Empty Dataframe: {name}'
                        )
                        continue
                    if ldf is None:
                        ldf = df1
                    ldf = self._join(df1=ldf, df2=data, **args)
                    self.data.pop(name)
                df = ldf
                self.data[f"{uuid4()!s}"] = df
                return self.data
            else:
                return None
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
        finally:
            if len(self.data) == 1:
                # return the last one:
                return list(self.data.values())[0]

    def _join(self, df1: pd.DataFrame, df2: pd.DataFrame, **kwargs):
        df = pd.merge(
            df1,
            df2,
            how=self._type,
            suffixes=('_left', '_right'),
            **kwargs
        )
        if df is None:
            raise DataNotFound(
                "Empty Result Dataframe"
            )
        elif df.empty:
            raise DataNotFound(
                "Empty Result Dataframe"
            )
        df.drop(
            df.columns[df.columns.str.contains('_left')],
            axis=1,
            inplace=True
        )
        df.reset_index(drop=True)
        print(
            '::: Printing Column Information === '
        )
        for column, t in df.dtypes.items():
            print(column, '->', t, '->', df[column].iloc[0])
        return df
