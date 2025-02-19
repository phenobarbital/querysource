"""Functions for FilterRows."""
import re
from typing import Optional
import pandas as pd
import numpy as np
from navconfig.logging import logging
from ...exceptions import QueryException
from ...utils.getfunc import getFunction


valid_operators = ['+', '-', '*', '/', '%', '==', '!=', '>', '<', '>=', '<=', '/', '//']


def build_condition(expression: str, column: str, value, condition: dict) -> str:
    """Build a filter condition for DataFrame filtering.

    Args:
        expression: The filter expression to be applied.
        column: The column to be filtered.
        value: The value to be compared against.
        df: The DataFrame to which the filters will be applied.

    Returns:
        a string representation of the filter condition.

    Raises:
        QueryException: If the expression is invalid.
    """
    if expression in ("gt", "lt"):
        if not isinstance(value, (int, float)):
            raise QueryException(
                "Value must be numeric for column length filtering."
            )
        op = ">" if expression == "gt" else "<"
        return f"(df['{column}'].str.len() {op} {value})"
    if expression == "is_null":
        return f"df['{column}'].isnull() | (df['{column}'] == '')"
    elif expression == "not_null":
        return f"~(df['{column}'].isnull() | (df['{column}'] == ''))"
    elif expression == "is_empty":
        return f"(df['{column}'] == '')"
    elif isinstance(value, (int, float)):
        condition['value'] = value
        return "(df['{column}'] {expression} {value})".format_map(
            condition
        )
    elif isinstance(value, str):
        if expression in ('regex', 'not_regex', 'fullmatch'):
            if expression == 'regex':
                return f"df['{column}'].str.match(r'{value}', na=False)"
            if expression == 'not_regex':
                return f"~df['{column}'].str.match(r'{value}', na=False)"
            if expression == 'fullmatch':
                return f"df['{column}'].str.fullmatch(r'{value}', na=False)"
        else:
            condition['value'] = f"'{value}'"
            if expression == 'contains':
                return f"df['{column}'].str.contains(r'{value}', na=False, case=False)"
            elif expression == 'not_contains':
                return f"~df['{column}'].str.contains(r'{value}', na=False, case=False)"
            elif expression == 'startswith':
                return f"df['{column}'].str.startswith('{value}')"
            elif expression == 'not_startswith':
                return f"~df['{column}'].str.startswith('{value}')"
            elif expression == 'endswith':
                return f"df['{column}'].str.endswith('{value}')"
            elif expression == 'not_endswith':
                return f"~df['{column}'].str.endswith('{value}')"
            elif expression == '==':
                return "(df['{column}'] {expression} {value})".format_map(
                    condition
                )
            elif expression == '!=':
                return "(df['{column}'] {expression} {value})".format_map(
                    condition
                )
            elif expression in valid_operators:
                # first: validate "expression" to be valid expression on Pandas.
                return "(df['{column}'] {expression} {value})".format_map(
                    condition
                )
            else:
                raise QueryException(
                    f"Invalid expression: {expression}"
                )
    elif isinstance(value, (np.datetime64, np.timedelta64)):
        condition['value'] = value
        return "(df['{column}'] {expression} {value})".format_map(
            condition
        )
    elif isinstance(value, list):
        if expression == 'startswith':
            # Use tuple directly with str.startswith
            val = tuple(value)
            return f"(df['{column}'].str.startswith({val}))"
        elif expression == 'not_startswith':
            val = tuple(value)
            return f"(~df['{column}'].str.startswith({val}))"
        elif expression == 'endswith':
            # Use tuple directly with str.endswith
            val = tuple(value)
            return f"(df['{column}'].str.endswith({val}))"
        elif expression == 'not_endswith':
            val = tuple(value)
            return f"(~df['{column}'].str.endswith({val}))"
        elif expression == 'contains':
            regex_pattern = "|".join(map(re.escape, value))
            return f"df['{column}'].str.contains(r'{regex_pattern}', na=False, case=False)"
        elif expression == 'not_contains':
            regex_pattern = "|".join(map(re.escape, value))
            return f"~df['{column}'].str.contains(r'{regex_pattern}', na=False, case=False)"
        elif expression == "regex":
            # Regular expression match
            regex_pattern = "|".join(map(str, value))
            return f"df['{column}'].str.contains(r'{regex_pattern}', na=False)"
        elif expression == "not_regex":
            # Regular expression match
            regex_pattern = "|".join(map(str, value))
            return f"~df['{column}'].str.contains(r'{regex_pattern}', na=False)"
        elif expression == "fullmatch":
            # Full match
            regex_pattern = "|".join(map(re.escape, value))
            return f"df['{column}'].str.fullmatch(r'{regex_pattern}', na=False)"
        elif expression == "==":
            return f"df['{column}'].isin({value})"
        elif expression == "!=":
            # not:
            return f"~df['{column}'].isin({value})"
        elif expression in [">", ">="]:
            return f"(df['{column}'] {expression} min({value}))"
        elif expression in ["<", "<="]:
            return f"(df['{column}'] {expression} max({value}))"
        elif expression in valid_operators:
            return f"(df['{column}'] {expression} {value!r})"
        else:
            raise QueryException(
                f"tFilter: Invalid expression: {expression}"
            )

def create_filter_chain(expression: list, column: str, df: pd.DataFrame) -> list:
    """Construct filter conditions for DataFrame filtering.

    Using an expression builder to create a Pandas Filter condition.

    Args:
        expression: A list of filter dictionaries, each specifying a column,
            an expression, and a value.
        column: The column to be filtered.
        df: The DataFrame to which the filters will be applied.

    Returns:
        A list of string conditions that can be used to filter the DataFrame.

    Raises:
        QueryException: If a column name is missing, not found in the DataFrame,
            or if an invalid expression is provided.
    """
    conditions = []
    for condition in expression:
        expression = condition.get('expression', '==')
        value = condition.get('value', None)
        column = condition.get('column')
        new_condition = build_condition(expression, column, value, condition)
        conditions.append(f"({new_condition})")
    # Combine all conditions with a logical AND
    if conditions:
        return np.logical_and.reduce(conditions)
    return None


def create_filter(_filter: list, df: pd.DataFrame) -> list:
    """Construct filter conditions for DataFrame filtering.

    This method takes a list of filter specifications and converts them
    into a list of string conditions suitable for DataFrame filtering.

    Args:
        _filter: A list of filter dictionaries, each specifying a column,
            an expression, and a value.
        df: The DataFrame to which the filters will be applied.

    Returns:
        A list of string conditions that can be used to filter the DataFrame.

    Raises:
        QueryException: If a column name is missing, not found in the DataFrame,
            or if an invalid expression is provided.
    """
    conditions = []
    for condition in _filter:
        column = condition.get('column')
        if not column:
            raise QueryException(
                "Column name is required for filtering."
            )
        if column not in df.columns:
            raise QueryException(
                f"tFilter: Column {column} not found in DataFrame."
            )
        expression = condition.get('expression', '==')
        value = condition.get('value', None)

        # --- New rule for filtering on column length ---
        # If the expression is "gt" or "lt", compare the length of the column values.
        if expression in ('func', 'function'):
            # Using .apply() to apply the function to Dataframe.
            fn = getFunction(value)
            if fn:
                df = df[df[column].apply(fn)]
            continue
        if isinstance(expression, list):
            # Using a method chain to create a new expression builder.
            new_conditions = create_filter_chain(expression, column, df)
            conditions.extend(new_conditions)
            continue
        else:
            new_condition = build_condition(expression, column, value, condition)
            conditions.append(f"({new_condition})")
            continue
    return conditions


def drop_columns(df: pd.DataFrame, columns: list = None, endswith: list = None, startswith: list = None):
    """
    This function drops specified columns from a DataFrame based on exact names, suffixes, or prefixes.

    :param dataframe: The DataFrame from which columns will be dropped.
    :param columns: List of exact column names to drop.
    :param endswith: List of suffixes; columns ending with these will be dropped.
    :param startswith: List of prefixes; columns starting with these will be dropped.
    :return: The DataFrame with specified columns dropped.
    """
    if columns and isinstance(columns, list):
        df.drop(axis=1, columns=columns, inplace=True, errors="ignore")
    elif endswith and isinstance(endswith, list):
        cols_to_drop = [col for col in df.columns if col.endswith(tuple(endswith))]
        dataframe = df.drop(columns=cols_to_drop)
    elif startswith and isinstance(startswith, list):
        cols_to_drop = [col for col in df.columns if col.startswith(tuple(startswith))]
        dataframe = df.drop(columns=cols_to_drop)
    return df


def drop_rows(df: pd.DataFrame, **kwargs):
    """
    This function drops rows from a DataFrame based on specified column values.

    :param df: The DataFrame from which rows will be dropped.
    :param kwargs: Column names and their corresponding values to drop rows.
    :return: The DataFrame with specified rows dropped.
    """
    for column, expression in kwargs.items():
        if isinstance(expression, list):
            mask = df[column].isin(expression)
            df = df[~mask]
            df.head()
    return df

def drop_duplicates(df: pd.DataFrame, columns: Optional[list] = None, **kwargs):
    """
    This function drops duplicate rows from a DataFrame based on specified columns.

    :param dataframe: The DataFrame from which duplicates will be dropped.
    :param columns: List of columns to consider for identifying duplicates.
    :param kwargs: Additional keyword arguments for drop_duplicates method.
    :return: The DataFrame with duplicates dropped.
    """
    if columns and isinstance(columns, list):
        df.set_index(columns, inplace=True, drop=False)
        df = df.sort_values(by=columns)
        df = df.drop_duplicates(subset=columns, **kwargs)
    return df


def clean_empty(df: pd.DataFrame, columns: Optional[list] = None):
    """
    This function drops rows from a DataFrame where specified columns are empty, NaN, or contain empty strings.

    :param dataframe: The DataFrame from which rows will be dropped.
    :param columns: List of columns to check for empty values.
    :return: The DataFrame with specified rows dropped.
    """
    if columns and isinstance(columns, list):
        for column in columns:
            condition = df[
                (df[column].empty) | (df[column] == "") | (df[column].isna())
            ].index
            df.drop(condition, inplace=True)
    return df


def suppress(df: pd.DataFrame, columns: Optional[list] = None, **kwargs):
    """
    This function suppresses parts of string values in specified columns based on a regex pattern.

    :param dataframe: The DataFrame containing the columns to be modified.
    :param columns: List of columns to apply the suppression.
    :param kwargs: Additional keyword arguments, including 'pattern' for the regex.
    :return: The DataFrame with suppressed string values.
    """
    pattern = kwargs.get('pattern', None)

    def clean_chars(field):
        name = str(field)
        if not pattern:
            return name
        if re.search(pattern, name):
            pos = re.search(pattern, name).start()
            # return str(name)[:pos]
            return name[:pos]
        else:
            return name

    if columns and isinstance(columns, list):
        for column in columns:
            df[column] = df[column].astype(str)
            df[column] = df[column].apply(clean_chars)
    return df


def fill_na(df: pd.DataFrame, columns: Optional[list] = None, fill_value="", **kwargs):
    """
    This function fills NaN values in specified columns with a given fill value.

    :param df: The DataFrame containing the columns to be filled.
    :param columns: List of columns to fill NaN values.
    :param fill_value: The value to replace NaN values with.
    :param kwargs: Additional keyword arguments.
    :return: The DataFrame with NaN values filled.
    """
    df[columns] = (
        df[columns].astype(str).replace(["nan", np.nan], fill_value, regex=True)
    )
    return df

def fill_nulls(df: pd.DataFrame, field: str, column: str):
    """
    Fills null values in a specified field with values from another column.

    :param df: The DataFrame containing the fields.
    :param field: The name of the field to fill nulls in.
    :param column: The name of the column to use for filling nulls.
    :return: The DataFrame with nulls filled.
    """
    # Check if field exists in DataFrame, if not, create it using the column's values
    if field not in df.columns:
        df[field] = df[column]
    else:
        # Replace empty strings with nulls, only if the field is of string type
        try:
            df[field] = df[field].apply(
                lambda x: x.strip() if isinstance(x, str) else x
            ).replace("", np.nan)
        except Exception as err:
            print("ERROR = ", err)

        # Ensure there are no duplicate index labels before filling nulls
        if df.index.duplicated().any():
            print("Warning: Duplicate index labels found. Resetting index to prevent issues.")
            df = df.reset_index(drop=True)

        # Fill null values in the field with values from the specified column
        try:
            df.loc[df[field].isnull(), field] = df[column]
        except KeyError:
            logging.error(f"Fill Nulls: Column {column} doesn't exist")
        except ValueError as e:
            logging.error(f"Error during fill_nulls: {e}")

    return df


def drop_na(df: pd.DataFrame, field: str, columns: Optional[list] = None) -> pd.DataFrame:
    """
    Drops rows with NaN values in a specified field or columns.

    :param df: The DataFrame containing the field or columns.
    :param field: The name of the field to drop NaN values in.
    :param columns: List of columns to drop NaN values in.
    :return: The DataFrame with NaN values dropped.
    """
    if field in df.columns:
        df = df.dropna(subset=[field])
    elif columns:
        df.dropna(subset=columns, inplace=True)
    return df
