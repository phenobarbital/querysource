from typing import Union
import pandas as pd
from ....exceptions import (
    DriverError,
    QueryException
)
from .abstract import AbstractTransform


class pivot(AbstractTransform):
    """Reshape a DataFrame using a pivot or pivot_table operation.

    Converts a long-format DataFrame into a wide-format table by pivoting
    values from one column into multiple columns.

    Usage: Use in a MultiQuery ``Transform`` step to turn row-level records
    into a column-per-category layout (e.g. monthly columns from a date column).

    Attributes:
        index: Column(s) to use as the pivot table row index. Required.
        columns: Column whose unique values become the new column headers. Required.
        values: Column whose values populate the pivot table cells. Required for
            ``pivot_table``; optional for ``pivot``.
        aggfunc: Aggregation function for duplicate entries (e.g. ``'sum'``, ``'mean'``).
            Only applies when ``type`` is ``'pivot_table'``.
        type: Pivot variant — ``'pivot'`` or ``'pivot_table'``. Default: ``'pivot'``.
        multilevel: If ``True``, keep multi-level column headers. Default: ``False``.
        fill_value: Value to use for missing entries in the pivot result.
        pd_args: Extra keyword arguments passed to the underlying pandas call.
        reset_index: If ``True``, reset the result index. Default: ``True``.

    Example:
        {
            "Transform": [
                {
                    "pivot": {
                        "index": "product",
                        "columns": "month",
                        "values": "revenue",
                        "aggfunc": "sum",
                        "type": "pivot_table",
                        "fill_value": 0
                    }
                }
            ]
        }
    """

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        self.reset_index: bool = kwargs.pop('reset_index', True)
        self._type = kwargs.pop('type', 'pivot')
        self._multilevel = kwargs.pop('multilevel', False)
        self._pd_args = kwargs.pop('pd_args', {})
        self._fill_value = kwargs.pop('fill_value', None)
        super().__init__(data, **kwargs)
        if not hasattr(self, 'index'):
            raise DriverError(
                "Crosstab Transform: Missing Index on definition"
            )
        if not hasattr(self, 'columns'):
            raise DriverError(
                "Crosstab Transform: Missing Columns on definition"
            )

    async def run(self):
        await self.start()
        args = {
            ## "normalize": 'columns',
            ## "dropna": False
        }
        if not hasattr(self, 'values'):
            self.values = None
        if hasattr(self, 'aggregate'):
            args['aggfunc'] = self.aggregate
            args['values'] = [self.data[i] for i in self.values]  # pylint: disable=E1133
        if hasattr(self, 'totals'):
            tname = self.totals['name']
            args['margins'] = True
            args['margins_name'] = tname
        if self._pd_args:
            args = {**args, **self._pd_args}
        try:
            if self._type == 'crosstab':
                df = pd.crosstab(
                    index=[self.data[i] for i in self.index],
                    columns=[self.data[i] for i in self.columns],
                    **args
                )
            elif self._type == 'pivot':
                args = {'fill_value': self._fill_value}
                if self._pd_args:
                    args |= self._pd_args
                aggfunc = self.aggregate if hasattr(self, 'aggregate') else 'first'
                df = pd.pivot_table(
                    self.data,
                    index=self.index,
                    columns=self.columns,
                    aggfunc=aggfunc,
                    values=self.values,
                    **args
                )
            # Flattening the multi-level columns
            if self._multilevel is False:
                df.columns = [f'{col[0]}_{col[1].lower()}' for col in df.columns]

            if self.reset_index is True:
                df.reset_index(inplace=True)
            self.colum_info(df)
            return df
        except (ValueError, KeyError) as err:
            raise QueryException(
                f'Crosstab Error: {err!s}'
            ) from err
        except Exception as err:
            raise QueryException(
                f"Unknown error {err!s}"
            ) from err
