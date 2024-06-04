from typing import Union
import pandas as pd
from ....exceptions import (
    DriverError,
    QueryException
)
from .abstract import AbstractTransform


# Define the categorize_correlation function as in the previous answer
def categorize_correlation(coefficient):
    if coefficient == 1:
        return "Perfect positive correlation"
    elif coefficient < 1 and coefficient >= 0.8:
        return "Very strong positive correlation"
    elif coefficient >= 0.6 and coefficient < 0.8:
        return "Strong positive correlation"
    elif coefficient >= 0.4 and coefficient < 0.6:
        return "Moderate positive correlation"
    elif coefficient >= 0.2 and coefficient < 0.4:
        return "Weak positive correlation"
    elif coefficient > 0 and coefficient < 0.2:
        return "Very weak positive correlation"
    if coefficient == -1:
        return "Perfect negative correlation"
    elif coefficient > -1 and coefficient <= -0.8:
        return "Very strong negative correlation"
    elif coefficient <= -0.6 and coefficient > -0.8:
        return "Strong negative correlation"
    elif coefficient <= -0.4 and coefficient > -0.6:
        return "Moderate negative correlation"
    elif coefficient <= -0.2 and coefficient > -0.4:
        return "Weak negative correlation"
    elif coefficient < 0 and coefficient > -0.2:
        return "Very weak negative correlation"
    elif coefficient == 0:
        return "No correlation"
    else:
        return "Undetermined"

class correlation(AbstractTransform):
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        self.reset_index: bool = bool(kwargs.pop('reset_index', True))
        self.numeric_only: bool = bool(kwargs.pop('numeric_only', True))
        super(correlation, self).__init__(data, **kwargs)
        if not hasattr(self, 'columns'):
            raise DriverError(
                "Correlation Transform: Missing Columns on definition"
            )
        if not hasattr(self, 'method'):
            self.method = 'pearson'

    async def run(self):
        await self.start()
        try:
            if hasattr(self, 'grouped'):
                first_col = self.columns[0]
                second_col = self.columns[1]
                colname = self.grouped['col_name']
                group_col = self.grouped['column']
                correlation = self.data.groupby(group_col).apply(lambda x: x[first_col].corr(x[second_col]))
                self.data[colname] = correlation
            if hasattr(self, 'by_group'):
                # Calculate the Correlation by Program:
                corr = self.data.groupby(self.by_group).apply(
                    lambda group: group[self.columns].corr(method=self.method).iloc[0, 1]
                )
                print('CORRELATION > ', corr)
                df = corr.reset_index()
                df.columns = [self.by_group, 'Correlation']  # Renaming the columns appropriately
            else:
                # Calculate the Pearson's correlation for these columns
                df = self.data[self.columns].corr(method=self.method, numeric_only=self.numeric_only)
            # Add a new column for the correlation categories
            if hasattr(self, 'add_categorization'):
                try:
                    # Add a new column for the correlation categories
                    if hasattr(self, 'by_group'):
                        df['Correlation Category'] = df['Correlation'].apply(categorize_correlation)
                    else:
                        category_series = df.apply(lambda x: x.map(categorize_correlation))
                        category_matrix = pd.concat([df, category_series.add_suffix('_category')], axis=1)
                        df = category_matrix
                except Exception as err:
                    print(err)
            if self.reset_index is True:
                df.reset_index(inplace=True)
            self.colum_info(df)
            return df
        except (ValueError, KeyError) as err:
            raise QueryException(
                f'Correlation Error: {err!s}'
            ) from err
        except Exception as err:
            raise QueryException(
                f"Unknown error {err!s}"
            ) from err
