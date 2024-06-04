from typing import Union
import pandas as pd
import copy
from ....exceptions import (
    DriverError,
    QueryException
)
from .abstract import AbstractTransform
from . import map_functions as dffunctions


class Map(AbstractTransform):
    """Map Transform: changing the shape of the data."""
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        self.replace_columns: bool = kwargs.pop('replace_columns', False)
        try:
            self.reset_index: bool = kwargs['reset_index']
            del kwargs['reset_index']
        except KeyError:
            self.reset_index: bool = True
        super(Map, self).__init__(data, **kwargs)
        if not hasattr(self, 'fields'):
            raise DriverError(
                "Map Transform: Missing Fields for transformation."
            )

    async def run(self):
        await self.start()
        try:
            fields = copy.deepcopy(self.fields)
        except AttributeError:
            raise QueryException(
                "Map Transform: Missing Fields for transformation."
            )
        it = self.data.copy()
        for field, val in fields.items():
            if isinstance(val, str):
                # simple column replacement:
                try:
                    it[val] = it[field]
                    if self.replace_columns is True:
                        it.drop(field, axis="columns", inplace=True)
                    continue
                except KeyError:
                    self._logger.error(f"Column doesn't exists: {val}")
                    continue
                except Exception as e:
                    self._logger.error(f"Error dropping Column: {val}, {e}")
                    continue
            elif isinstance(val, list):
                # value is a function to be called:
                fname = val.pop(0)
                try:
                    args = val[0]
                except IndexError:
                    args = {}
                try:
                    func = getattr(dffunctions, fname)
                    self.logger.debug(
                        f"Calling Function: {fname!s} with args: {args}"
                    )
                    if args:
                        it = func(df=it, field=field, **args)
                    else:
                        it = func(df=it, field=field)
                    it = it.copy()
                except AttributeError:
                    self.logger.error(f"Function not found: {fname!s}")
                    continue
        # at the end
        self.data = it
        if hasattr(self, 'drop_columns'):
            self.data.drop(columns=self.drop_columns, inplace=True)
        self.colum_info(self.data)
        return self.data
