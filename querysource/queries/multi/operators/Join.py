from pandas import DataFrame
from uuid import uuid4
from navconfig.logging import logging
from ....exceptions import (
    DataNotFound,
    DriverError,
    QueryException
)
from .abstract import AbstractOperator


class Join(AbstractOperator):
    def __init__(self, data: dict, **kwargs) -> None:
        self._type: str = kwargs.get('type', 'inner')
        # Left Operator
        self._left = kwargs.pop('left', None)
        # Right Operator
        self._right = kwargs.pop('right', None)
        super(Join, self).__init__(data, **kwargs)
        self.data = data

    async def start(self):
        for _, data in self.data.items():
            ## TODO: add support for polars and datatables
            if isinstance(data, DataFrame):
                self._backend = 'pandas'
            else:
                raise DriverError(
                    f'Wrong type of data for JOIN, required Pandas dataframe: {type(data)}'
                )

    async def run(self):
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
                        _key = f"{self._left}.{self._right}"
                        self.data[_key] = df
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
                    finally:
                        del df1
                        del df2
                else:
                    df1 = None
                    if self._left:
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
            else:
                raise QueryException(
                    f"Unsupported Operator: {operator}"
                )
            return list(self.data.values())[0] if len(self.data) == 1 else self.data
        except DataNotFound:
            raise
        except QueryException:
            raise
        except (ValueError, KeyError) as err:
            raise QueryException(
                f'Cannot Join with missing Column: {err!s}'
            ) from err
        except Exception as err:
            raise QueryException(
                f"Unknown JOIN error {err!s}"
            ) from err

    def _join(self, df1: DataFrame, df2: DataFrame, **kwargs):
        try:
            df = self._pd.merge(
                df1,
                df2,
                how=self._type,
                suffixes=('_left', '_right'),
                **kwargs
            )
            if df is None or df.empty:
                raise DataNotFound(
                    "Empty Result Dataframe"
                )
        except DataNotFound:
            raise
        except Exception as err:
            print('Error on Join > ', err)
            raise QueryException(
                f"Error on Join: {err!s}"
            ) from err
        # remove the _left and _right
        df.drop(
            df.columns[df.columns.str.contains('_left')],
            axis=1,
            inplace=True
        )
        df.reset_index(drop=True)
        self._print_info(df)
        return df
