"""Functions for FilterRows."""
import re
from typing import Optional
import pandas as pd
import numpy as np
from navconfig.logging import logging


def drop_columns(df: pd.DataFrame, columns: list = [], endswith: list = [], startswith: list = []):
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


def drop_rows(df, **kwargs):
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


def drop_duplicates(df: pd.DataFrame, columns=[], **kwargs):
    """
    This function drops duplicate rows from a DataFrame based on specified columns.

    :param dataframe: The DataFrame from which duplicates will be dropped.
    :param columns: List of columns to consider for identifying duplicates.
    :param kwargs: Additional keyword arguments for drop_duplicates method.
    :return: The DataFrame with duplicates dropped.
    """
    if columns and isinstance(columns, list):
        df.set_index(columns, inplace=True, drop=False)
        df.sort_values(by=columns, inplace=True)
        df.drop_duplicates(subset=columns, inplace=True, **kwargs)
    return df


def clean_empty(df: pd.DataFrame, columns=[]):
    """
    This function drops rows from a DataFrame where specified columns are empty, NaN, or contain empty strings.

    :param dataframe: The DataFrame from which rows will be dropped.
    :param columns: List of columns to check for empty values.
    :return: The DataFrame with specified rows dropped.
    """
    if columns and isinstance(columns, list):
        for column in columns:
            condition = df[
                (df[column].empty)
                | (df[column] == "")
                | (df[column].isna())
            ].index
            df.drop(condition, inplace=True)
    return df


def suppress(df: pd.DataFrame, columns=[], **kwargs):
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
            return str(name)[:pos]
        else:
            return name

    if columns and isinstance(columns, list):
        for column in columns:
            df[column] = df[column].astype(str)
            df[column] = df[column].apply(clean_chars)
    return df


def fill_na(df: pd.DataFrame, columns: list = [], fill_value="", **kwargs):
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
        df.dropna(subset=[field], inplace=True)
    elif columns:
        df.dropna(subset=columns, inplace=True)
    return df
