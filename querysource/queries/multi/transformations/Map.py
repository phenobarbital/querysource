from collections.abc import Callable
from typing import Union
import inspect
import pandas as pd
import copy
from ....exceptions import (
    DriverError,
    QueryException
)
from .abstract import AbstractTransform
from ....types.dt import transforms as dffunctions
from ....utils.getfunc import getFunction


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
        except AttributeError as e:
            raise QueryException(
                "Map Transform: Missing Fields for transformation."
            ) from e
        it = self.data.copy()
        for field, val in fields.items():
            if isinstance(val, str):
                # simple column replacement:
                try:
                    it[field] = it[val]
                    # if self.replace_columns is True:
                    it.drop(val, axis="columns", inplace=True)
                    continue
                except KeyError:
                    self.logger.error(
                        f"Column doesn't exists: {val}"
                    )
                    continue
                except Exception as e:
                    self.logger.error(
                        f"Error dropping Column: {val}, {e}"
                    )
                    continue
            elif isinstance(val, dict):
                # multiple functions to be called at once
                for fname, args in val.items():
                    it = self._call_fn(field, fname, args, it)
                continue
            elif isinstance(val, list):
                if len(val) > 1:
                    # multiple functions to be called at once
                    for v in val:
                        it = self._run_one(v, it, field)
                else:
                    it = self._run_one(val, it, field)
                print(it)
                continue
        # at the end
        self.data = it
        if hasattr(self, 'drop_columns'):
            self.data.drop(columns=self.drop_columns, inplace=True)
        self.colum_info(self.data)
        return self.data

    def _call_fn(self, field: str, fname: Callable, args: dict, it: pd.DataFrame) -> pd.DataFrame:
        """
        Call a function.
        """
        try:
            func = getattr(dffunctions, fname)
            self.logger.debug(
                f"Calling Function: {fname!s} with args: {args}"
            )
            it = func(df=it, field=field, **args)
            # it = it.copy()
            return it
        except AttributeError:
            pass
        try:
            func = getFunction(fname)
        except AttributeError:
            self.logger.error(f"Function not found: {fname!s}")
            return it
        if not callable(func):
            return it
        self.logger.debug(
            f"Calling Scalar: {fname!s}: {func}"
        )
        try:
            # applying the function func with argments "args" to dataframe it
            if self.is_series_function(func, args):
                it[field] = it[field].apply(func, **args)
            else:
                # r = {field: func(**args)}
                # it = it.assign(**r)
                it[field] = func(**args)
            return it
        except Exception as e:
            self.logger.error(
                f"Error applying function: {fname!s}, {e}"
            )
            return it

    def _run_one(self, val: list, it: pd.DataFrame, field: str) -> pd.DataFrame:
        """
        Run a single function on the data.
        val is a list of arguments to be passed to the function:
        """
        element = val.pop(0)
        if isinstance(element, list):
            # split into function and arguments:
            fname = element.pop(0)
            try:
                args = element[0]
            except IndexError:
                args = {}
        else:
            fname = element
            try:
                args = val[0]
            except IndexError:
                args = {}
        try:
            it = self._call_fn(field, fname, args, it)
            return it
        except Exception as e:
            self.logger.error(
                f"Error applying function: {fname!s}, {e}"
            )
            return it

    def is_series_function(self, func, args):
        """
        Determines if a function can operate on a pandas Series or is a scalar function.

        :param func: The function to inspect.
        :param args: The arguments to pass to the function.
        :return: True if the function can operate on a pandas Series, False if it is scalar.
        """
        # Try calling the function with a dummy Series
        try:
            dummy_series = pd.Series([1, 2, 3])
            func(dummy_series, **args)
            return True
        except Exception:
            pass
        # Check if func can handle scalars
        try:
            func(**args)
            return False
        except Exception as e:
            raise ValueError(
                f"Map: cannot handle scalar or Series input with given arguments: {e}"
            )
