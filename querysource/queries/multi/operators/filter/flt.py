from pandas import DataFrame
import numpy as np
from .....exceptions import (
    DataNotFound,
    DriverError,
    QueryException
)
from .....types import is_empty
from ..abstract import AbstractOperator
from . import functions as dffunctions


class Filter(AbstractOperator):
    def __init__(self, data: dict, **kwargs) -> None:
        self.conditions = kwargs.pop('conditions', None)
        self.fields: dict = kwargs.pop('fields', {})
        self._filter = kwargs.pop('filter', {})
        self.filter_conditions: dict = {}
        self._applied: list = []
        self._operator: str = kwargs.get('operator', '&')
        super(Filter, self).__init__(data, **kwargs)

    async def start(self):
        for _, data in self.data.items():
            ## TODO: add support for polars and datatables
            if not isinstance(data, DataFrame):
                raise DriverError(
                    f'Wrong type of data for JOIN, required Pandas dataframe: {type(data)}'
                )

    def _create_filter(self, df: DataFrame) -> list:
        conditions = []
        for column, value in self.fields.items():
            print('COLUMN > ', column, value)
            value = value.get('value', None)
            expression = value.get('expression', '==')
            if isinstance(value, str):
                if expression == 'regex':
                    conditions.append(
                        f"df['{column}'].str.match(r'{value}')"
                    )
                elif expression == 'startswith':
                    conditions.append(
                        f"df['{column}'].str.startswith('{value}')"
                    )
                elif expression == 'endswith':
                    conditions.append(
                        f"df['{column}'].str.endswith('{value}')"
                    )
                elif expression == '==':
                    conditions.append(
                        "(df['{column}'].isin({value}))".format_map(
                            value
                        )
                    )
                elif expression == '!=':
                    conditions.append(
                        "(~df['{column}'].isin({value}))".format_map(
                            value
                        )
                    )
                else:
                    value = "'{}'".format(value)
                    conditions.append(
                        "(df['{column}'] {expression} {value})".format_map(
                            value
                        )
                    )
            elif isinstance(value, list):
                if expression == 'startswith':
                    # Use tuple directly with str.startswith
                    val = tuple(value)
                    condition = f"df['{column}'].str.startswith({val})"
                    conditions.append(f"({condition})")
                elif expression == 'endswith':
                    # Use tuple directly with str.endswith
                    val = tuple(value)
                    condition = f"df['{column}'].str.endswith({val})"
                    conditions.append(f"({condition})")
                elif expression == "regex":
                    # Regular expression match
                    conditions.append(
                        f"df['{column}'].str.match(r'{value}')"
                    )
                elif expression == "==":
                    conditions.append(
                        "(df['{column}'].isin({value}))".format_map(
                            value
                        )
                    )
                elif expression == "!=":
                    # not:
                    conditions.append(
                        "(~df['{column}'].isin({value}))".format_map(
                            value
                        )
                    )
            else:
                # Making an expression builder:
                pass
        return conditions

    async def run(self):
        if self.data is None or is_empty(self.data):
            return None
        # start filtering
        if hasattr(self, "clean_strings"):
            u = self.data.select_dtypes(include=["object", "string"])
            self.data[u.columns] = self.data[u.columns].fillna("")
        if hasattr(self, "clean_numbers"):
            u = self.data.select_dtypes(include=["Int64"])
            # self.data[u.columns] = self.data[u.columns].fillna('')
            self.data[u.columns] = self.data[u.columns].replace(
                ["nan", np.nan], 0, regex=True
            )
            u = self.data.select_dtypes(include=["float64"])
            self.data[u.columns] = self.data[u.columns].replace(
                ["nan", np.nan], 0, regex=True
            )
        if hasattr(self, "clean_dates"):
            u = self.data.select_dtypes(include=["datetime64[ns]"])
            self.data[u.columns] = self.data[u.columns].replace({np.nan: None})
            # df[u.columns] = df[u.columns].astype('datetime64[ns]')
        if hasattr(self, "drop_empty"):
            # First filter out those rows which
            # does not contain any data
            self.data.dropna(how="all")
            # removing empty cols
            self.data.is_copy = None
            self.data.dropna(axis=1, how="all")
            self.data.dropna(axis=0, how="all")
        if hasattr(self, "dropna"):
            self.data.dropna(subset=self.dropna, how="all")
        # iterate over all filtering conditions:
        it = self.data.copy()
        for ft, args in self.filter_conditions.items():
            self._applied.append(f"Filter: {ft!s} args: {args}")
            # TODO: create an expression builder
            # condition = dataframe[(dataframe[column].empty) & (dataframe[column]=='')].index
            # check if is a function
            try:
                try:
                    func = getattr(dffunctions, ft)
                except AttributeError:
                    func = globals()[ft]
                if callable(func):
                    it = func(it, **args)
            except Exception as err:
                print(f"Error on {ft}: {err}")
        else:
            df = it
        if df is None or df.empty:
            raise DataNotFound(
                "No Data was Found after Filtering."
            )
        # Applying filter expressions by Column:
        if self.fields:
            conditions = self._create_filter(df)
            # Joining all conditions
            self.condition = f" {self._operator} ".join(conditions)
            print("CONDITION >> ", self.condition)
            df = df.loc[
                eval(self.condition)
            ]  # pylint: disable=W0123
        elif self._filter:
            for column, value in self._filter.items():
                if column in df.columns:
                    df = df[df[column] == value]
        self._print_info(df)
        return df
