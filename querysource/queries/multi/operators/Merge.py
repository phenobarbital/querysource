from pandas import DataFrame
from ....exceptions import (
    DataNotFound,
    DriverError,
    QueryException
)
from .abstract import AbstractOperator

class Merge(AbstractOperator):
    """
    Merge two DataFrames with support for various join types including anti-join.

    Parameters:
    - on: Column(s) to join on
    - how: Join type (inner, left, right, outer, anti)
    - left_on: Column(s) from left DataFrame
    - right_on: Column(s) from right DataFrame
    - suffixes: Suffixes for overlapping columns
    """
    def __init__(self, data: dict, **kwargs) -> None:
        self._on = kwargs.pop('on', None)  # Column(s) to join on
        self._how = kwargs.pop('how', 'inner')  # Join type (inner, left, right, outer, anti)
        self._left_on = kwargs.pop('left_on', None)  # Column(s) from left DataFrame
        self._right_on = kwargs.pop('right_on', None)  # Column(s) from right DataFrame
        self._suffixes = kwargs.pop('suffixes', ('_x', '_y'))  # Suffixes for overlapping columns
        super(Merge, self).__init__(data, **kwargs)

    async def start(self):
        if len(self.data) < 2:
            raise DriverError(
                f'Merge requires at least two DataFrames, got {len(self.data)}'
            )

        for _, data in self.data.items():
            if not isinstance(data, DataFrame):
                raise DriverError(
                    f'Wrong type of data for Merge, required a Pandas dataframe: {type(data)}'
                )
            self._backend = 'pandas'

        try:
            self.left_df = self.data.pop(self.using)
        except (KeyError, IndexError) as ex:
            raise DriverError(
                f"Missing LEFT Dataframe on Data: {self.using}"
            ) from ex

        if self.left_df.empty:
            raise DataNotFound(
                f"Empty LEFT {self.using} Dataframe"
            )

        try:
            self.right_df = self.data.popitem()[1]
        except (KeyError, IndexError) as ex:
            raise DriverError(
                "Missing RIGHT Dataframe"
            ) from ex

        if self.right_df.empty:
            raise DataNotFound(
                "Empty RIGHT Dataframe"
            )

    async def run(self):
        merge_args = {
            'suffixes': self._suffixes
        }

        # Set merge columns
        if self._on is not None:
            merge_args['on'] = self._on
        elif self._left_on is not None and self._right_on is not None:
            merge_args['left_on'] = self._left_on
            merge_args['right_on'] = self._right_on
        else:
            raise QueryException(
                "Merge requires either 'on' or both 'left_on' and 'right_on' parameters"
            )

        try:
            # Handle anti-join separately
            if self._how == 'anti':
                # Anti-join is implemented as a left join with indicator, then filtering
                merge_args['how'] = 'left'
                merge_args['indicator'] = True

                merged_df = self._pd.merge(
                    self.left_df,
                    self.right_df,
                    **merge_args
                )

                # Keep only rows from left that don't match in right
                result = merged_df[merged_df['_merge'] == 'left_only'].drop(columns='_merge')
            else:
                # Standard merge for other join types
                merge_args['how'] = self._how

                result = self._pd.merge(
                    self.left_df,
                    self.right_df,
                    **merge_args
                )

            self._print_info(result)
            return result

        except DataNotFound:
            raise
        except (ValueError, KeyError) as err:
            raise QueryException(
                f'Cannot merge with missing Column: {err!s}'
            ) from err
        except Exception as err:
            raise QueryException(
                f"Unknown merge error: {err!s}"
            ) from err
