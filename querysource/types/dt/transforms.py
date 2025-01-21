"""
Function Tree for Pandas-related row/column transformations.

"""
from typing import Any, Dict, List, Optional, Union
import ast
import datetime
from bs4 import BeautifulSoup
from datetime import timedelta, datetime as dtime
from functools import reduce
import traceback
import orjson
import calendar
import timezonefinder
from zoneinfo import ZoneInfo
import locale
import numpy as np
import pandas as pd
from pandas.tseries.offsets import MonthEnd
from navconfig.logging import logging
from ...types.validators import strtobool
from ...utils.getfunc import getFunction
from ...conf import DEFAULT_TIMEZONE


def to_timestamp(df: pd.DataFrame, field: str, remove_nat: bool = False):
    try:
        df[field] = pd.to_datetime(df[field], errors="coerce")
        df[field] = df[field].where(df[field].notnull(), None)
        df[field] = df[field].astype("datetime64[ns]")
    except Exception as err:
        print(err)
    return df


def from_currency(df: pd.DataFrame, field: str, symbol="$", remove_nan: bool = True):
    df[field] = (
        df[field]
        .replace(f"[\\{symbol},) ]", "", regex=True)
        .replace("[(]", "-", regex=True)
        .replace("[ ]+", np.nan, regex=True)
        .str.strip(",")
    )
    if remove_nan is True:
        df[field] = df[field].fillna(0)
    df[field] = pd.to_numeric(df[field], errors="coerce")
    df[field] = df[field].replace([-np.inf, np.inf], np.nan)
    return df

def num_formatter(n: Union[int, Any]):
    """
    Formats a string representing a number, handling negative signs and commas.

    :param n: The string to be formatted.
    :return: The formatted string.
    """
    if type(n) == str:  # noqa
        return (
            f"-{n.rstrip('-').lstrip('(').rstrip(')')}"
            if n.endswith("-") or n.startswith("(")
            else n.replace(",", ".")
        )
    else:
        return n

def convert_to_integer(
    df: pd.DataFrame, field: str, not_null: bool = False, fix_negatives: bool = False
):
    """
    Converts the values in a specified column of a pandas DataFrame to integers,
    optionally fixing negative signs and ensuring no null values.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param not_null: Boolean indicating whether to ensure no null values. Defaults to False.
    :param fix_negatives: Boolean indicating whether to fix negative signs. Defaults to False.
    :return: Modified pandas DataFrame with the values converted to integers.
    """
    try:
        if fix_negatives is True:
            df[field] = df[field].apply(num_formatter)  # .astype('float')
        df[field] = pd.to_numeric(df[field], errors="coerce")
        df[field] = df[field].astype("Int64", copy=False)
    except Exception as err:
        print(field, "->", err)
    if not_null is True:
        df[field] = df[field].fillna(0)
    return df


def apply_function(
    df: pd.DataFrame,
    field: str,
    fname: str,
    column: Optional[str] = None,
    **kwargs
) -> pd.DataFrame:
    """
    Apply any scalar function to a column in the DataFrame.

    Parameters:
    - df: pandas DataFrame
    - field: The column where the result will be stored.
    - fname: The name of the function to apply.
    - column: The column to which the function is applied (if None, apply to `field` column).
    - **kwargs: Additional arguments to pass to the function.
    """

    # Retrieve the scalar function using getFunc
    try:
        func = getFunction(fname)
    except Exception:
        raise

    # If a different column is specified, apply the function to it,
    # but save result in `field`
    try:
        if column is not None:
            df[field] = df[column].apply(lambda x: func(x, **kwargs))
        else:
            if field not in df.columns:
                # column doesn't exist
                df[field] = None
            # Apply the function to the field itself
            df[field] = df[field].apply(lambda x: func(x, **kwargs))
    except Exception as err:
        print(
            f"Error in apply_function for field {field}:", err
        )
    return df

def math_operation(df: pd.DataFrame, field: str, columns: list, operation: str):
    """
    Apply a mathematical operation between columns in a DataFrame and store the result in a new column.

    Parameters:
    df (pd.DataFrame): The DataFrame to operate on.
    field (str): The name of the new column to store the result.
    columns (list): A list of two column names to perform the operation on.
    operation (str): The operation to perform ('add', 'subtract', 'multiply', 'divide').

    Returns:
    pd.DataFrame: The modified DataFrame with the new column added.
    """
    if len(columns) != 2:
        raise ValueError("The 'columns' parameter must contain exactly two column names.")

    col1, col2 = columns

    if col1 not in df.columns or col2 not in df.columns:
        raise KeyError(f"One or both columns {col1}, {col2} not found in the DataFrame.")

    if operation in {'add', 'sum'}:
        df[field] = df[col1] + df[col2]
    elif operation == 'subtract':
        df[field] = df[col1] - df[col2]
    elif operation == 'multiply':
        df[field] = df[col1] * df[col2]
    elif operation == 'divide':
        # Handle division safely, avoiding division by zero
        df[field] = df[col1] / df[col2].replace(0, float('nan'))
    else:
        raise ValueError(
            (
                f"Unsupported operation: {operation}. Supported operations are 'add'"
                " 'subtract', 'multiply', 'divide'."
            )
        )
    return df

def extract_from_array(
    df: pd.DataFrame,
    field: str = "",
    column: str = "",
    index: int = 0
):
    """
    Extracts an element from a list in a specified column of a DataFrame.

    :param df: The DataFrame containing the column with lists.
    :param field: The name of the new field to store the extracted element.
    :param column: The name of the column containing lists.
    :param index: The index of the element to extract
      ('first' for the first element, 'last' for the last element, or an integer index).
    :return: The DataFrame with the extracted element field.
    """
    if index == 'first':
        idx = 0
    elif index == 'last':
        idx = -1
    else:
        idx = index
    try:
        df[field] = df[column].apply(lambda x: x[idx] if isinstance(x, list) and x is not None else None)
    except Exception as err:
        logging.error(f"extract_from_array Error: {err}")
    finally:
        return df


def explode(
    df: pd.DataFrame,
    field: str,
    columns: list = None,
    is_string: bool = True,
    delimiter: str = ",",
):
    """
    Takes a pandas DataFrame and splits the values in a specified column (or columns)
    into multiple rows based on a specified delimiter.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be exploded.
    :param columns: Optional, list of additional columns to be exploded. Defaults to None.
    :param is_string: Optional, a boolean flag that determines whether the values should be treated as strings
        and split using the specified delimiter. Defaults to True.
    :param delimiter: Optional, the character or string used as the delimiter to split the values.
      Defaults to a comma (',').
    :return: Modified pandas DataFrame with the exploded values.
    """
    splitcols = [field]
    if columns is not None:
        splitcols += columns
    ### first: convert all colums to list:
    if is_string:
        for col in splitcols:
            try:
                df[col] = [x.strip("()").split(delimiter) for x in df[col]]
            except KeyError:
                pass  # TODO: remove column from list of columns
            except AttributeError:
                # capturing when col cannot be splitted:
                df[col] = df[col].str.strip("()").str.split(delimiter)
    try:
        df = df.explode(splitcols, ignore_index=True)
    except ValueError as err:
        logging.error(f"Explode Error: {err}")
    finally:
        return df  # pylint: disable=W0150


def sum(df: pd.DataFrame, field: str = "", columns: list = []):
    """
    Takes a pandas DataFrame and sums the values across specified columns,
    storing the result in a new column.

    :param df: pandas DataFrame to be modified.
    :param field: Optional, name of the new column to store the sum of the values. Defaults to an empty string ('').
    :param columns: List of columns in the df DataFrame to be summed. Defaults to an empty list ([]).
    :return: Modified pandas DataFrame with the sum of the specified columns stored in a new column.
    """
    try:
        if columns and isinstance(columns, list):
            df[field] = df[columns].sum(axis=1)
        return df
    except Exception as err:
        print("SUM Error ", err)
        return df


def div(df: pd.DataFrame, field: str, numerator: str, denominator: str):
    """
    Takes a pandas DataFrame and divides the values in one column by the values in another column,
    storing the result in a new column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the new column to store the division results.
    :param numerator: Name of the column in the df DataFrame to be used as the numerator.
    :param denominator: Name of the column in the df DataFrame to be used as the denominator.
    :return: Modified pandas DataFrame with the division results stored in a new column.
    """
    try:
        df[field] = df[numerator].div(df[denominator].values)
        return df
    except Exception as err:
        print("DIV Error ", err)
        return df


def to_time(
    df: pd.DataFrame,
    field: str,
    replace_nulls: bool = False,
    value: str = "00:00:00",
    format: str = "%I:%M %p",
    fallback_format: str = "%I:%M%p",
    as_timedelta: bool = False
):
    """
    Takes a pandas DataFrame and converts the values in a specified column to time objects.
    It can also replace null values and convert the time objects to timedelta if specified.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be converted to time.
    :param replace_nulls: Optional, a boolean flag that determines whether to replace null values
       with a specified value. Defaults to False.
    :param value: Optional, the value to replace nulls with if replace_nulls is True. Defaults to "00:00:00".
    :param format: Optional, the format of the time strings to be converted. Defaults to "%I:%M %p".
    :param as_timedelta: Optional, a boolean flag that determines whether to convert the time objects to timedelta.
      Defaults to False.
    :return: Modified pandas DataFrame with the time values.
    """
    try:
        locale.setlocale(locale.LC_TIME, ('en_US', 'UTF-8'))
    except locale.Error:
        pass  # Locale setting might fail depending on system configuration, so handle it silently.

    # Function to convert a single value, with null handling
    def convert_to_time(value):
        if pd.isnull(value):
            return None
        if isinstance(value, (datetime.time, datetime.datetime, pd.Timestamp)):
            # If value is already a datetime or Timestamp, return its time component
            return value.time() if hasattr(value, 'time') else value
        try:
            return datetime.datetime.strptime(value.strip(), format).time()
        except ValueError:
            try:
                return datetime.datetime.strptime(value.strip(), fallback_format).time()
            except ValueError:
                return None  # Return None if both formats fail

    # Apply the conversion row by row, skipping null values
    try:
        df[field] = df[field].apply(lambda x: convert_to_time(x) if pd.notnull(x) else None)
    except Exception as err:
        print('Conversion Error > ', err)
        return df

    # Optional conversion to timedelta
    try:
        if as_timedelta:
            df[field] = df[field].apply(
                lambda t: pd.to_timedelta(
                    f"{t.hour:02}:{t.minute:02}:{t.second:02}"
                ) if t is not None else pd.NaT
            )
        else:
            time_series = df[field].apply(
                lambda t: pd.Timestamp(
                    year=1900, month=1, day=1, hour=t.hour, minute=t.minute, second=t.second
                ) if t is not None else pd.NaT
            )
            df[field] = pd.to_datetime(time_series)
    except Exception as e:
        print(f"Error converting '{field}' to time: {e}")

    # Handle replacing null values if specified
    if replace_nulls:
        # Ensure nulls are safely replaced with the provided value (keeping consistent types)
        df[field] = df[field].apply(lambda x: value if pd.isnull(x) else x)

    return df


def middle_time(df: pd.DataFrame, field: str, columns: list) -> pd.DataFrame:
    """
    Calculates the middle time between two specified columns in a pandas DataFrame
    and stores the result in a new column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the new column to store the middle time.
    :param columns: List of two columns in the df DataFrame to calculate the middle time between.
    :return: Modified pandas DataFrame with the middle time stored in a new column.
    """
    # Calculate the middle time
    c1 = columns[0]
    c2 = columns[1]
    df[field] = df[c1] + (df[c2] - df[c1]) / 2
    return df


def drop_column(df: pd.DataFrame, field: str):
    """
    Takes a pandas DataFrame and drops a specified column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column to be dropped.
    :return: Modified pandas DataFrame with the specified column dropped.
    """
    return df.drop([field], axis=1)


def add_days(df: pd.DataFrame, field: str, column="", days=1):
    """
    Takes a pandas DataFrame and adds a specified number of days to the values in a given column,
    storing the result in a new column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the new column to store the result.
    :param column: Name of the column in the df DataFrame to which days will be added. Defaults to an empty string ("").
    :param days: Number of days to add. Defaults to 1.
    :return: Modified pandas DataFrame with the added days stored in a new column.
    """
    df[field] = df[column] + pd.DateOffset(days=days)
    return df


def add_timestamp_to_time(df: pd.DataFrame, field: str, date: str, time: str):
    """
    Takes a pandas DataFrame and combines the values from a date column and a time column
    to create a new timestamp column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the new column to store the combined timestamp.
    :param date: Name of the column in the df DataFrame containing date values.
    :param time: Name of the column in the df DataFrame containing time values.
    :return: Modified pandas DataFrame with the combined timestamp stored in a new column.
    """
    df[field] = pd.to_datetime(
        df[date].dt.date.astype(str) + " " + df[time].dt.time.astype(str)
    )
    return df


def substract_days(df: pd.DataFrame, field: str, column="", days=1):
    """
    Takes a pandas DataFrame and subtracts a specified number of days from the values in a given column,
    storing the result in a new column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the new column to store the result.
    :param column: Name of the column in the df DataFrame from which days will be subtracted. Defaults to an empty string ("").
    :param days: Number of days to subtract. Defaults to 1.
    :return: Modified pandas DataFrame with the subtracted days stored in a new column.
    """  # noqa
    df[field] = df[column] - pd.DateOffset(days=days)
    return df


def rename_column(df: pd.DataFrame, field: str, rename: str):
    """
    Takes a pandas DataFrame and renames a specified column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column to be renamed.
    :param rename: New name for the column. Defaults to an empty string ("").
    :return: Modified pandas DataFrame with the specified column renamed.
    """
    return df.rename(columns={field: rename})

def rename_prefix(df: pd.DataFrame, field: str, prefix: str, multiple: bool = False) -> pd.DataFrame:
    """
    Rename a column removing a prefix.
    :param df: pandas DataFrame to be modified.
    :param field: Name of the column to be renamed.
    :param prefix: Prefix to be removed.
    :param multiple (bool): find several columns with the same prefix and renamed.
    :return: Modified pandas DataFrame with the specified column renamed.

    """
    if multiple is True:
        # Rename columns by stripping the prefix from columns that start with it
        df.rename(
            columns={col: col[len(prefix):] for col in df.columns if col.startswith(prefix)},
            inplace=True
        )
    else:
        if field.startswith(prefix):
            new_name = field.replace(prefix, '')
            df.rename(
                columns={field: new_name}
            )
    return df


def regex_match(df: pd.DataFrame, field: str, column="", regex=""):
    """
    Takes a pandas DataFrame and extracts substrings that match a specified regular expression
    from a given column, storing the result in a new column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the new column to store the extracted substrings.
    :param column: Name of the column in the df DataFrame from which substrings will be extracted. Defaults to an empty string ("").
    :param regex: The regular expression pattern to match. Defaults to an empty string ("").
    :return: Modified pandas DataFrame with the extracted substrings stored in a new column.
    """  # noqa
    df[field] = df[column].str.extract(regex)
    return df


def zerofill(df: pd.DataFrame, field: str, num_zeros=1):
    """
    Takes a pandas DataFrame and fills the values in a specified column with leading zeroes
    until they reach a specified length.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be zero-filled.
    :param num_zeros: The total length of the string after zero-filling. Defaults to 1.
    :return: Modified pandas DataFrame with the zero-filled values.
    """
    df[field] = df[field].astype(str).str.zfill(num_zeros)
    return df


def pad(df: pd.DataFrame, field: str, num_chars=4, side="left", char="0"):
    """
    Takes a pandas DataFrame and pads the values in a specified column with a specified character
    until they reach a specified length, based on the side (left or right).

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be padded.
    :param num_chars: The total length of the string after padding. Defaults to 4.
    :param side: The side to pad the string on ('left' or 'right'). Defaults to 'left'.
    :param char: The character to pad the string with. Defaults to '0'.
    :return: Modified pandas DataFrame with the padded values.
    """
    df[field] = df[field].astype(str).str.pad(num_chars, side=side, fillchar=char)
    return df


def concat(df: pd.DataFrame, field: str, columns=[], separator=" "):
    """
    Takes a pandas DataFrame and concatenates the values from specified columns into a single column,
    using a specified separator.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the new column to store the concatenated values.
    :param columns: List of columns in the df DataFrame to be concatenated. Defaults to an empty list ([]).
    :param separator: The character or string used to separate the concatenated values. Defaults to a space (" ").
    :return: Modified pandas DataFrame with the concatenated values stored in a new column.
    """
    try:
        if columns and isinstance(columns, list):
            df[field] = (
                df[columns]
                .fillna("")
                .astype(str)
                .apply(lambda x: x.str.cat(sep=separator), axis=1)
            )
        return df
    except Exception as err:
        print("CONCAT Error ", err)
        return df

def concat_column_values(df: pd.DataFrame, field: str, column: str, separator=","):
    """
    Concatenates all values in the specified column of a DataFrame into a single string,
    separated by a specified delimiter, and assigns this string to a new or existing field in the DataFrame.
    """
    try:
        df[field] = separator.join(df[column].fillna("").astype(str))
    except Exception as err:
        print("CONCAT Error: ", err)

    return df


def _apply_affix(df: pd.DataFrame, field: str, column: str, prefix: str = "", suffix: str = ""):
    """Helper function to apply a prefix or suffix to a column."""
    if not column:
        column = field
    try:
        df[field] = df[column].apply(lambda x: f"{prefix!s}{x}{suffix!s}")
        return df
    except Exception as err:
        print("Affix Error ", err)
        return df

def prefix(df: pd.DataFrame, field: str, column: str = None, prefix: str = ""):
    """
    Takes a pandas DataFrame and adds a specified string prefix to the values in a given column,
    storing the result in a new column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the new column to store the prefixed values.
    :param column: Name of the column in the df DataFrame to which the prefix will be added. Defaults to None, which will use the field name.
    :param prefix: The string prefix to add to the values. Defaults to an empty string ("").
    :return: Modified pandas DataFrame with the prefixed values.
    """  # noqa
    return _apply_affix(df, field, column, prefix=prefix)

def suffix(df: pd.DataFrame, field: str, column: str = None, suffix: str = ""):
    """Adding a string suffix to a Column."""
    return _apply_affix(df, field, column, suffix=suffix)

def normalize_strings(
    df: pd.DataFrame,
    field: str,
    column="",
    lowercase: bool = True,
    clean_strings: bool = False,
    replacement: str = "_",
):
    """
    Takes a pandas DataFrame and normalizes the strings in a specified column by converting them to lowercase,
    removing spaces, and optionally cleaning specific characters.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the new column to store the normalized strings.
    :param column: Name of the column in the df DataFrame to be normalized. Defaults to an empty string ("").
    :param lowercase: Optional, a boolean flag that determines whether to convert the strings to lowercase. Defaults to True.
    :param clean_strings: Optional, a boolean flag that determines whether to clean specific characters from the strings. Defaults to False.
    :param replacement: Optional, the character to replace spaces and cleaned characters with. Defaults to an underscore ("_").
    :return: Modified pandas DataFrame with the normalized strings.
    """  # noqa
    try:
        col = col = column or field
        df[field] = df[col]
        if clean_strings:
            charsToRemove = [",", ".", r"\.", r"\'"]
            df[field] = df[field].str.replace(
                r"{}".format(charsToRemove), replacement, regex=True
            )
        if lowercase:
            df[field] = df[field].str.lower()
        df[field] = df[field].str.replace(" ", replacement, regex=True)
        return df
    except Exception as err:
        print("Normalize Error ", err)
        return df


def coalesce(df: pd.DataFrame, field: str, column: str = None, match: str = None):
    """
    Mimics the COALESCE function in databases, replacing null values in a specified column
    with values from another column or a specified value.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be coalesced.
    :param column: Optional, name of the column to use for replacing null values. Defaults to None.
    :param match: Optional, value or function to use for replacing null values. Defaults to None.
    :return: Modified pandas DataFrame with null values replaced.
    """
    if column:
        try:
            df[field] = np.where(df[field].isnull(), df[column], df[field])
        except Exception as err:
            print("COALESCE Error: ", err)
    elif match:
        # if match is string then:
        if isinstance(match, list):
            # is a function
            fname = match[0]
            args = {}
            result = None
            func = globals()[fname]
            if len(match) > 1:
                args = match[1]
            if callable(func):
                try:
                    result = func(**args)
                except Exception as err:
                    print("Coalesce Error ", err)
                    result = func()
            if result:
                try:
                    df[field] = df[field].fillna(result)
                except Exception as err:
                    print("Coalesce Error ", err)
                    return df
        else:
            df[field] = np.where(df[field].isnull(), match, df[field])
    else:
        return df
    return df


def split_array(df: pd.DataFrame, field: str, column="", separator=" ", trim=False):
    """
    Takes a pandas DataFrame and splits the values in a specified column
    into lists based on a specified separator.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be split.
    :param column: Optional, name of the new column to store the split values. Defaults to an empty string ("").
    :param separator: Optional, the character or string used as the separator to split the values. Defaults to a space (" ").
    :param trim: Optional, a boolean flag that determines whether to trim the split values. Defaults to False.
    :return: Modified pandas DataFrame with the split values stored in a new column.
    """  # noqa
    try:
        col = column if column else field
        df[col] = df[field].str.split(separator)
        # if trim is True:
        #     df[col] = df[col].apply(lambda x: separator.join(x), axis=1)
        return df
    except Exception as err:
        print("Split Error: ", err)
        return df


def split(
    df: pd.DataFrame,
    field: str,
    column: str = None,
    separator: str = " ",
    idx: int = 0,
    is_integer: bool = False,
) -> pd.DataFrame:
    """
    Takes a pandas DataFrame and splits the values in a specified column
      into multiple values based on a specified separator,
    and stores the resulting split values in a new column in the same DataFrame.

    :param df: pandas DataFrame to be modified.
    :param field: Optional, name of the new column to store the split values.
       Defaults to an empty string ('') which will store the split values in a
       column with the same name as the original column, but with the index
       of the split value appended.
    :param column: Name of the column in the df DataFrame to be splitted.
    :param separator: Optional, the character or string used as the separator
        to split the values in the specified column. Defaults to a space (' ').
    :param index: Optional, the index of the split value to be extracted and stored in
        the new column. Defaults to 0 which will store the first split value.
    :param is_integer: Optional, a boolean flag that determines whether the
        split value should be converted to an integer data type. Defaults to False.
    :return: Modified pandas DataFrame with the split values stored in a new column.
    """  # noqa
    try:
        # Split the values in the specified column using the provided separator and
        # store the resulting split values in a new column
        try:
            idx = int(idx)
        except (ValueError, TypeError) as ex:
            idx = 0
        if not field:
            field = column + "_" + str(idx)
        if not column:
            column = field
        df[field] = df[column].str.split(separator).str[idx]
        if is_integer is True:
            df[field] = pd.to_numeric(df[field], errors="coerce")
            df[field] = df[field].astype("Int64")
        return df
    except Exception as err:
        print("Split Error: ", err)
        return df


def split_to_columns(df: pd.DataFrame, field: str, columns: list, regex: str = "\s+", expand: bool = True):
    """
    Takes a pandas DataFrame and splits the values in a specified column into multiple columns based on a given regex pattern.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be split.
    :param columns: List of new column names to store the split values.
    :param regex: The regex pattern used to split the column values. Defaults to "\s+" (one or more whitespace characters).
    :param expand: Whether to expand the split strings into separate columns. Defaults to True.
    :return: Modified pandas DataFrame with the split values in new columns.
    """  # noqa
    try:
        df[columns] = df[field].str.extract(regex, expand=expand)
    except Exception as err:
        print("Split to Columns Error: ", err)
    finally:
        return df


def slice(
    df: pd.DataFrame,
    field: str,
    column="",
    start=0,
    end=1,
    is_integer: bool = False,
):
    """
    Takes a pandas DataFrame and extracts a substring from the values in a specified column,
    storing the result in a new column. The substring is defined by the start and end positions.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the new column to store the sliced substring.
    :param column: Name of the column in the df DataFrame to be sliced. Defaults to an empty string ("").
    :param start: The starting position of the substring. Defaults to 0.
    :param end: The ending position of the substring. Defaults to 1.
    :param is_integer: Optional, a boolean flag that determines whether the sliced substring should be converted to an integer data type. Defaults to False.
    :return: Modified pandas DataFrame with the sliced substring stored in a new column.
    """  # noqa
    try:
        df[field] = df[column].astype(str).str.slice(start, end)
        if is_integer is True:
            df[field] = df[field].astype("Int64")
        return df
    except Exception as err:
        print("Slice Error: ", err)
        return df


def case(
    df: pd.DataFrame,
    field: str,
    column="",
    condition: Any = None,
    match=None,
    notmatch=None,
):
    """
    Generates a selection of options similar to a CASE statement in SQL,
    based on specified conditions.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the new column to store the result.
    :param column: Name of the column in the df DataFrame to be evaluated. Defaults to an empty string ("").
    :param condition: The condition or list of conditions to evaluate.
    :param match: The value to assign if the condition is met.
    :param notmatch: The value to assign if the condition is not met.
    :return: Modified pandas DataFrame with the CASE-like results stored in a new column.
    """
    if type(condition) == list:  # pylint: disable=C0123 # noqa
        # conditions = [df[column].eq(condition[0]) & df[column].eq(condition[1])]
        df[field] = np.select([df[column].isin(condition)], [match], default=notmatch)
    else:
        df[field] = np.select([df[column] == condition], [match], default=notmatch)
    return df


def divide(
    df: pd.DataFrame,
    field: str,
    columns: Optional[list] = None,
    divisor: Union[int, float] = 100,
    not_null: bool = False
):
    """
    Takes a pandas DataFrame and divides the values in a specified column by a given divisor or columns.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be divided.
    :param columns: Optional list of columns for division logic.
    :param divisor: The value by which to divide the column values. Defaults to 100.
    :param not_null: If True, replace NaN resulting from division by zero with 0. Defaults to False.
    :return: Modified pandas DataFrame with the divided values.
    """
    if columns is not None:
        # Ensure numeric and clean columns
        for col in columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df[col] = df[col].replace([-np.inf, np.inf], np.nan)

        if len(columns) == 2:
            df[field] = df[columns[0]] / df[columns[1]]
        elif len(columns) == 1:
            column = columns[0]
            df[field] = df.apply(lambda row: row[field] / row[column] if pd.notna(row[column]) else np.nan, axis=1)
    else:
        df[field] = pd.to_numeric(df[field], errors="coerce")
        df[field] = df[field].replace([-np.inf, np.inf], np.nan)
        df[field] = df[field].apply(lambda x: x / divisor)

    # Handle not_null flag
    if not_null:
        df[field] = df[field].fillna(0)

    return df

def multiply(
    df: pd.DataFrame,
    field: str,
    columns: Optional[list] = None,
    multiplier: Union[int, float] = 1
):
    """
    Takes a pandas DataFrame and multiplies the values in a specified column by a given multiplier or columns.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be multiplied.
    :param columns: Optional list of columns for multiplication logic.
    :param multiplier: The value by which to multiply the column values. Defaults to 1.
    :return: Modified pandas DataFrame with the multiplied values.
    """
    if columns is not None:
        # Ensure numeric and clean columns
        for col in columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df[col] = df[col].replace([-np.inf, np.inf], np.nan)

        if len(columns) == 2:
            df[field] = df[columns[0]] * df[columns[1]]
        elif len(columns) == 1:
            column = columns[0]
            df[field] = df.apply(lambda row: row[field] * row[column] if pd.notna(row[column]) else np.nan, axis=1)
    else:
        df[field] = pd.to_numeric(df[field], errors="coerce")
        df[field] = df[field].replace([-np.inf, np.inf], np.nan)
        df[field] = df[field].apply(lambda x: x * multiplier)

    return df


def nullif(df: pd.DataFrame, field: str, chars=[]):
    """
    Takes a pandas DataFrame and sets the values in a specified column to None
    if they match any value in a given list of characters.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be checked.
    :param chars: List of characters to be checked against. If a value in the column matches any character in this list, it will be set to None. Defaults to an empty list ([]).
    :return: Modified pandas DataFrame with the specified values set to None.
    """  # noqa
    df.loc[(df[field].isin(chars)), field] = None
    return df


def to_null(df: pd.DataFrame, field: str, words: list):
    """
    Takes a pandas DataFrame and replaces specified words in a given column with NaN.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param words: List of words to be replaced with NaN.
    :return: Modified pandas DataFrame with specified words replaced by NaN.
    """
    try:
        df[field] = df[field].apply(lambda x: np.nan if x in words else x)
    except Exception as err:
        print(err)
    return df


def capitalize(df: pd.DataFrame, field: str):
    """
    Takes a pandas DataFrame and capitalizes the first letter of each word in a specified column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be capitalized.
    :return: Modified pandas DataFrame with the capitalized values.
    """
    df[field] = df[field].str.title()
    return df


def to_round(df: pd.DataFrame, field: str, ndecimals: int = 2):
    """
    Takes a pandas DataFrame and rounds the values in a specified column to a given number of decimal places.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be rounded.
    :param ndecimals: The number of decimal places to round to. Defaults to 2.
    :return: Modified pandas DataFrame with the rounded values.
    """
    try:
        df[field].astype("float")
        df[field] = df[field].apply(lambda x: round(x, ndecimals))
    except Exception as err:
        print(err)
    return df


def uppercase(df: pd.DataFrame, field: str, from_column: str = None):
    """
    Takes a pandas DataFrame and converts the values in a specified column to uppercase.
    If a different column is specified, it uses that column's values to convert to uppercase.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param from_column: Name of the column to use for the uppercase conversion.
      If None, the 'field' column is used. Defaults to None.
    :return: Modified pandas DataFrame with the uppercase values.
    """
    if from_column is not None:
        column = from_column
    else:
        column = field
    df[field] = df[column].str.upper()
    return df


def lowercase(df: pd.DataFrame, field: str, from_column: str = None):
    """
    Takes a pandas DataFrame and converts the values in a specified column to lowercase.
    If a different column is specified, it uses that column's values to convert to lowercase.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param from_column: Name of the column to use for the lowercase conversion. If None, the 'field' column is used. Defaults to None.
    :return: Modified pandas DataFrame with the lowercase values.
    """  # noqa
    if from_column is not None:
        column = from_column
    else:
        column = field
    df[field] = df[column].str.lower()
    return df


def both_strip(df: pd.DataFrame, field: str, character=" "):
    """
    Takes a pandas DataFrame and strips the specified character from both ends of the values in a given column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param character: The character to strip from both ends of the column values. Defaults to a space.
    :return: Modified pandas DataFrame with the stripped values.
    """
    df[field] = df[field].str.strip(character)
    return df


def trim(df: pd.DataFrame, field: str, characters=" ", remove_empty: bool = False):
    """
    Takes a pandas DataFrame and trims the specified characters from both ends of the values in a given column.
    Optionally, it can also remove empty strings.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param characters: The characters to trim from both ends of the column values. Defaults to a space.
    :param remove_empty: Whether to remove empty strings after trimming. Defaults to False.
    :return: Modified pandas DataFrame with the trimmed values.
    """
    df[field] = df[field].str.strip()
    df[field] = df[field].str.strip(characters)
    if remove_empty is True:
        df[field] = df[field].replace("", None)
    return df


def ltrim(df: pd.DataFrame, field: str, characters=" "):
    """
    Takes a pandas DataFrame and trims the specified characters from the left end of the values in a given column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param characters: The characters to trim from the left end of the column values. Defaults to a space.
    :return: Modified pandas DataFrame with the left-trimmed values.
    """
    df[field] = df[field].str.strip()
    try:
        df[field] = df[field].str.lstrip(characters)
    except Exception as err:
        print(err)
    return df


def ltrip(df: pd.DataFrame, field: str, nchars: int = 0):
    """
    Takes a pandas DataFrame and removes a specified number of characters from the left end of the values in a given column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param nchars: The number of characters to remove from the left end of the column values. Defaults to 0.
    :return: Modified pandas DataFrame with the left-trimmed values.
    """  # noqa
    df[field] = [i[nchars:] for i in df[field]]
    return df


def rtrip(df: pd.DataFrame, field: str, nchars: int = 0):
    """
    Takes a pandas DataFrame and removes a specified number of characters from the right end of the values in a given column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param nchars: The number of characters to remove from the right end of the column values. Defaults to 0.
    :return: Modified pandas DataFrame with the right-trimmed values.
    """  # noqa
    df[field] = [i[:nchars] for i in df[field]]
    return df


def left_strip(df: pd.DataFrame, field: str, column: str = None, character=" "):
    """
    Removes leading characters from the values in a specified column of a pandas DataFrame.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param column: Name of the column to use for the operation. Defaults to the same as field.
    :param character: The character to remove from the left end of the column values. Defaults to a space.
    :return: Modified pandas DataFrame with the left-trimmed values.
    """
    try:
        if not column:
            column = field
        df[field] = df[column].str.lstrip(character)
    except Exception as err:
        print(traceback.format_exc())
        print(err)
    return df


def right_strip(df: pd.DataFrame, field: str, character=" "):
    """
    Removes trailing characters from the values in a specified column of a pandas DataFrame.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param character: The character to remove from the right end of the column values. Defaults to a space.
    :return: Modified pandas DataFrame with the right-trimmed values.
    """
    df[field] = df[field].str.rstrip(character)
    return df


def string_replace(
    df: pd.DataFrame,
    field: str,
    column: str = None,
    to_replace: str = "",
    value: str = "",
):
    """
    Replaces a specified string with another string in a given column of a pandas DataFrame.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param column: Name of the column to use for the operation. Defaults to the same as field.
    :param to_replace: The string to be replaced.
    :param value: The string to replace with.
    :return: Modified pandas DataFrame with the replaced values.
    """
    if not column:
        column = field
    try:
        df[column] = df[field].str.replace(to_replace, value)
    except Exception as err:
        print(err)
    return df


def replace_regex(df: pd.DataFrame, field: str, to_replace="", value=""):
    """
    Replaces a specified pattern with another string in a given column of a pandas DataFrame using regular expressions.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param to_replace: The pattern to be replaced. Can be a string or a list of strings.
    :param value: The string to replace with.
    :return: Modified pandas DataFrame with the replaced values.
    """
    try:
        if isinstance(to_replace, list):
            for rplc in to_replace:
                df[field] = df[field].str.replace(rplc, value, regex=True)
        else:
            df[field] = df[field].astype(str).str.replace(to_replace, value, regex=True)
    except Exception as err:
        print(traceback.format_exc())
        print(err)
    return df


def ereplace(df: pd.DataFrame, field: str, columns=[], newvalue=""):
    """
    Replaces occurrences of a value from one column with another value in a specified column of a pandas DataFrame.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param columns: List of two columns to use for the operation.
    :param newvalue: The string to replace with.
    :return: Modified pandas DataFrame with the replaced values.
    """
    col1 = columns[0]
    col2 = columns[1]
    df[field] = df.apply(lambda x: x[col1].replace((x[col2]), ""), 1)
    return df

def replace_to_nan(df: pd.DataFrame, field: str, to_replace=[]):
    df[field] = df[field].replace(to_replace, np.nan)
    return df

def convert_to_object(df: pd.DataFrame, field: str = "", remove_nan: bool = False):
    """
    Converts the values in a specified column of a pandas DataFrame to strings and optionally removes NaN values.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param remove_nan: Boolean indicating whether to remove NaN values. Defaults to False.
    :return: Modified pandas DataFrame with the values converted to strings.
    """
    df[field] = df[field].astype(str)
    df[field] = df[field].fillna("")
    if remove_nan is True:
        df[field] = df[field].str.replace(np.nan, "", regex=True)
    return df


def convert_to_string(
    df: pd.DataFrame,
    field: str = "",
    remove_nan: bool = False,
    avoid_empty: bool = True,
):
    """
    Converts the values in a specified column of a pandas DataFrame to strings, optionally removes NaN values, and optionally avoids empty strings.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param remove_nan: Boolean indicating whether to remove NaN values. Defaults to False.
    :param avoid_empty: Boolean indicating whether to avoid empty strings. Defaults to True.
    :return: Modified pandas DataFrame with the values converted to strings.
    """  # noqa
    try:
        df[field] = df[field].astype(str, errors="raise")
        df[field] = df[field].fillna("")
        if remove_nan is True:
            df[field] = df[field].str.replace(np.nan, "", regex=True)
        if avoid_empty is True:
            df[field] = df[field].astype(str).replace(r"^\s*$", None, regex=True)
    except TypeError:
        raise
    finally:
        return df


def to_string(
    df: pd.DataFrame, field: str, remove_nan: bool = False
) -> pd.DataFrame:
    """
    Converts the values in a specified column of a pandas DataFrame to strings and optionally removes NaN values.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param remove_nan: Boolean indicating whether to remove NaN values. Defaults to False.
    :return: Modified pandas DataFrame with the values converted to strings.
    """
    df[field] = df[field].astype("string")
    if remove_nan is True:
        df[field] = df[field].str.replace(np.nan, "", regex=True)
    return df

def to_list(
    df: pd.DataFrame,
    field: str,
    column: str = None,
    columns: list = [],
) -> pd.DataFrame:
    """to_list.

    Converts the values in a specified columns of a pandas DataFrame to a list.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param columns: List of columns to convert to a list.
    """
    try:
        if column is not None:
            field = column
        df[field] = df[columns].values.tolist()
    except Exception as err:
        print(err)
    return df


def convert_to_array(
    df: pd.DataFrame,
    field: str,
    column: str = None,
    separator=",",
    remove_empty: bool = False,
    no_duplicates: bool = False,
):
    """
    Converts the values in a specified column of a pandas DataFrame to arrays,
      optionally removing empty strings and duplicates.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param column: Name of the column to use for the operation. Defaults to the same as field.
    :param separator: The separator to use for splitting the values. Defaults to a comma.
    :param remove_empty: Boolean indicating whether to remove empty strings. Defaults to False.
    :param no_duplicates: Boolean indicating whether to remove duplicate values. Defaults to False.
    :return: Modified pandas DataFrame with the values converted to arrays.
    """  # noqa
    # Convert NaN to empty string
    col = column if column else field
    df[field] = df[col].fillna("")
    df[field] = df[field].str.split(pat=separator)
    if no_duplicates is True:
        # Trim whitespace from each word, handle "nan" strings, and remove duplicates
        df[field] = df[field].apply(
            lambda x: list(set([word.strip() for word in x if word.strip() != "nan"]))
        )
    else:
        # Trim whitespace from each word and handle "nan" strings
        df[field] = df[field].apply(
            lambda x: [word.strip() for word in x if word.strip() != "nan"]
        )
    if remove_empty is True:
        df[field] = df[field].apply(lambda x: list(filter(lambda y: y != "", x)))
        df[field] = df[field].apply(lambda x: x if x else np.nan)
    return df


def to_json(df: pd.DataFrame, field: str):
    """
    Converts the values in a specified column of a pandas DataFrame to JSON format.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :return: Modified pandas DataFrame with the values converted to JSON format.
    """
    try:
        # remove Nan
        df[field].fillna("[]", inplace=True)
        df[field] = df[field].str.replace("'", '"', regex=True)
        df[field] = df[field].apply(orjson.loads)
    except Exception as err:
        print(err)
    return df


def convert_json(value: str) -> str:
    """
    Converts a string to JSON format.

    :param value: The string to be converted.
    :return: The converted JSON string.
    """
    try:
        value = ast.literal_eval(value)
    except Exception:
        pass
    try:
        value = orjson.dumps(value).decode("utf-8")
    except Exception as err:
        print(err)
    return value


def convert_to_json(df: pd.DataFrame, field: str):
    """
    Converts the values in a specified column of a pandas DataFrame to JSON format using a helper function.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :return: Modified pandas DataFrame with the values converted to JSON format.
    """
    try:
        df[field] = df.apply(lambda x: convert_json(x[field]), axis=1)
    except Exception as err:
        print(err)
    return df


def convert_to_datetime(df: pd.DataFrame, field: str, remove_nat=False):
    """
    Converts the values in a specified column of a pandas DataFrame to datetime format.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param remove_nat: Boolean indicating whether to remove NaT values. Defaults to False.
    :return: Modified pandas DataFrame with the values converted to datetime format.
    """
    try:
        df[field] = pd.to_datetime(df[field], errors="coerce")
        df[field] = df[field].where(df[field].notnull(), None)
        df[field] = df[field].astype("datetime64[ns]")
    except Exception as err:
        print(err)
    return df


def to_datetime(df: pd.DataFrame, field: str, format="%Y-%m-%d"):
    """
    Converts the values in a specified column of a pandas DataFrame to datetime format using a specified format.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param format: The format to use for conversion. Defaults to "%Y-%m-%d".
    :return: Modified pandas DataFrame with the values converted to datetime format.
    """
    try:
        df[field] = pd.to_datetime(df[field], format=format, errors="coerce")
    except Exception as err:
        print(err)
    return df


def convert_to_time(
    df: pd.DataFrame, field: str, format="%H:%M:%S", not_null=False
):
    """
    Converts the values in a specified column of a pandas DataFrame to time format.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param format: The format to use for conversion. Defaults to "%H:%M:%S".
    :param not_null: Boolean indicating whether to replace NaT values with midnight. Defaults to False.
    :return: Modified pandas DataFrame with the values converted to time format.
    """
    df[field] = df[field].where(df[field].notnull(), None)
    df[field] = pd.to_datetime(df[field], format=format, errors="coerce").apply(
        pd.Timestamp
    )
    if not_null:
        df[field] = df[field].where(df[field].notnull(), datetime.time(0, 0))
    return df


def column_to_date(df: pd.DataFrame, field: str, column="", format="%Y-%m-%d"):
    """
    Converts the values in a specified column of a pandas DataFrame to date format using another column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param column: Name of the column to use for the operation. Defaults to the same as field.
    :param format: The format to use for conversion. Defaults to "%Y-%m-%d".
    :return: Modified pandas DataFrame with the values converted to date format.
    """
    if not column:
        column = field
    df[field] = pd.to_datetime(df[column], format=format, errors="coerce")
    df[field] = df[field].astype(object).where(df[field].notnull(), None)
    df[field] = df[field].astype("datetime64[ns]")
    # df1 = df.assign(field=e.values)
    return df


def to_date(df: pd.DataFrame, field: str, format="%Y-%m-%d", use_utc: bool = True):
    """
    Converts the values in a specified column of a pandas DataFrame to date format using a specified format and optionally using UTC.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param format: The format to use for conversion. Defaults to "%Y-%m-%d".
    :param use_utc: Boolean indicating whether to use UTC. Defaults to True.
    :return: Modified pandas DataFrame with the values converted to date format.
    """  # noqa
    df[field] = pd.to_datetime(
        df[field], utc=use_utc, format=format, errors="coerce"
    )
    return df


def datetime_format(df: pd.DataFrame, field: str, column="", format="%Y-%m-%d"):
    """
    Formats the values in a specified column of a pandas DataFrame to a specified datetime format using another column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param column: Name of the column to use for the operation. Defaults to the same as field.
    :param format: The format to use for conversion. Defaults to "%Y-%m-%d".
    :return: Modified pandas DataFrame with the values formatted to the specified datetime format.
    """
    try:
        if not column:
            column = field
        df[field] = df[column].dt.strftime(format)
    except Exception as err:
        print("format_from_column error:", err)
    return df


def column_to_integer(df: pd.DataFrame, field: str, column=""):
    """
    Converts the values in a specified column of a pandas DataFrame to integers.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param column: Name of the column to use for the operation. Defaults to the same as field.
    :return: Modified pandas DataFrame with the values converted to integers.
    """
    df[field] = pd.to_numeric(df[column], errors="coerce")
    df[field] = df[field].astype("Int64")
    return df


def convert_to_date(
    df: pd.DataFrame, field: str, format="%Y-%m-%d", remove_nat=False
):
    """
    Converts the values in a specified column of a pandas DataFrame to date format.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param format: The format to use for conversion. Defaults to "%Y-%m-%d".
    :param remove_nat: Boolean indicating whether to remove NaT values. Defaults to False.
    :return: Modified pandas DataFrame with the values converted to date format.
    """
    df[field] = pd.to_datetime(df[field], format=format, errors="coerce")
    df[field] = df[field].astype("datetime64[ns]")
    df[field] = df[field].dt.normalize()
    if remove_nat:
        df[field] = df[field].where(df[field].notnull(), None)
    return df


def string_to_date(df: pd.DataFrame, field: str, column="", format="%Y-%m-%d"):
    """
    Converts the values in a specified column of a pandas DataFrame to date format using another column.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param column: Name of the column to use for the operation. Defaults to the same as field.
    :param format: The format to use for conversion. Defaults to "%Y-%m-%d".
    :return: Modified pandas DataFrame with the values converted to date format.
    """
    df[field] = pd.to_datetime(df[column], format=format, errors="coerce")
    df[field] = df[field].replace({pd.NaT: None})
    df[field].astype("datetime64[ns]")
    return df


def epoch_to_date(
    df: pd.DataFrame, field: str, column: str = None, unit: str = "ms"
):
    """
    Converts epoch time values in a specified column of a pandas DataFrame to date format.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param column: Name of the column to use for the operation. Defaults to the same as field.
    :param unit: The unit of the epoch time. Defaults to milliseconds.
    :return: Modified pandas DataFrame with the values converted to date format.
    """
    if column:
        # using another column instead current:
        try:
            df[column] = df[column].astype("Int64", copy=False)
            df[field] = pd.to_datetime(df[column], unit=unit, errors="coerce")
        except Exception as err:
            logging.error(err)
    else:
        try:
            df[field] = pd.to_numeric(df[field], errors="coerce")
            df[field] = df[field].astype("Int64", copy=False)
            df[field] = pd.to_datetime(df[field], unit=unit, errors="coerce")
        except Exception as err:
            logging.error(err)
    df[field].astype("datetime64[ns]")
    return df


def convert_to_numeric(
    df: pd.DataFrame, field: str, remove_nan: bool = True, fix_negatives: bool = False
):
    """
    Converts the values in a specified column of a pandas DataFrame to numeric format,
    optionally removing NaN values and fixing negative signs.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :param remove_nan: Boolean indicating whether to remove NaN values. Defaults to True.
    :param fix_negatives: Boolean indicating whether to fix negative signs. Defaults to False.
    :return: Modified pandas DataFrame with the values converted to numeric format.
    """
    if fix_negatives is True:
        mask = df[field].str.endswith("-")
        df.loc[mask, field] = "-" + df.loc[mask, field].str[:-1]
    try:
        df[field] = pd.to_numeric(df[field], errors="coerce")
        df[field] = df[field].replace([-np.inf, np.inf], np.nan)
    except Exception as err:
        print(field, "->", err)
    if remove_nan is True:
        df[field] = df[field].fillna(0)
    return df


def to_integer(df: pd.DataFrame, field: str):
    """
    Converts the values in a specified column of a pandas DataFrame to integers, handling various exceptions.

    :param df: pandas DataFrame to be modified.
    :param field: Name of the column in the df DataFrame to be modified.
    :return: Modified pandas DataFrame with the values converted to integers.
    """
    try:
        df[field] = pd.to_numeric(df[field], errors="coerce")
        df[field] = df[field].astype("Int64", copy=False)
    except TypeError as err:
        print(f"TO Integer {field}: Unable to safely cast non-equivalent float to int.")
        df[field] = np.floor(pd.to_numeric(df[field], errors="coerce")).astype(
            "Int64"
        )
        print(err)
    except ValueError as err:
        print(
            f"TO Integer {field}: Unable to safely cast float to int due to out-of-range values: {err}"
        )
        df[field] = np.floor(pd.to_numeric(df[field], errors="coerce")).astype(
            "Int64"
        )
    except Exception as err:
        print(f"TO Integer {field}: An error occurred during conversion.")
        print(err)
    return df


def convert_to_boolean(
    df: pd.DataFrame,
    field: str,
    boolDict={"True": True, "False": False},
    true_values: Optional[dict] = None,
    nan: bool = False,
    preserve_nulls: bool = False,
):
    """
    Converts a specified field in a DataFrame to boolean values based on given mappings.

    :param df: The DataFrame containing the field to be converted.
    :param field: The name of the field to be converted.
    :param boolDict: A dictionary mapping string representations to boolean values.
    :param true_values: Optional dictionary of values to be considered as True.
    :param nan: The value to be used for NaNs.
    :param preserve_nulls: Whether to preserve null values.
    :return: The DataFrame with the converted field.
    """
    if field not in df.columns:
        # column doesn't exist
        df = df.assign(field=nan)
    try:
        if true_values is not None:
            if preserve_nulls is True:
                df[field] = df[field].map(
                    lambda x: True if x in true_values else (x if pd.isna(x) else False)
                )
            else:
                df[field] = df[field].map(
                    lambda x: True if x in true_values else False
                )
        else:
            if preserve_nulls is True:
                df[field] = df[field].map(boolDict).where(df[field].notna(), df[field])
            else:
                pd.set_option('future.no_silent_downcasting', True)
                df[field] = df[field].fillna(nan).astype(str).replace(boolDict).infer_objects(copy=False)
                df[field] = df[field].astype(bool)
    except Exception as err:
        print("TO Boolean Error: ", err)
    return df


to_boolean = convert_to_boolean


def string_to_bool(df: pd.DataFrame, field: str) -> pd.DataFrame:
    """
    Converts a specified field in a DataFrame from string to boolean.

    :param df: The DataFrame containing the field to be converted.
    :param field: The name of the field to be converted.
    :return: The DataFrame with the converted field.
    """
    df[field] = df[field].apply(strtobool)
    df[field] = df[field].astype(bool)
    return df


def replace_args(df: pd.DataFrame, field: str, column: str, args: Union[List, Dict] = None):
    """
    Replaces values in a specified field based on a list or dictionary of arguments.

    :param df: The DataFrame containing the field to be replaced.
    :param field: The name of the field to be replaced.
    :param column: The column to be used for replacement.
    :param args: A list or dictionary of arguments for replacement.
    :return: The DataFrame with the replaced field.
    """
    if isinstance(args, list):
        for arg in args:
            df[field] = df[column].astype(str).replace(arg)
    else:
        df[field] = df[column].astype(str).replace(args)
    return df


def replace(df: pd.DataFrame, field: str, args: List = None, is_regex: bool = False):
    """
    Replaces values in a specified field based on a list of arguments, with optional regex support.

    :param df: The DataFrame containing the field to be replaced.
    :param field: The name of the field to be replaced.
    :param args: A list of arguments for replacement.
    :param is_regex: Whether to use regex for replacement.
    :return: The DataFrame with the replaced field.
    """
    if isinstance(args, list):
        if len(args) > 1:
            for arg in args:
                df[field] = df[field].astype(str).replace(*arg, regex=is_regex)
        else:
            df[field] = df[field].astype(str).replace(*args, regex=is_regex)
    else:
        df[field] = df[field].astype(str).replace(args, regex=is_regex)
    return df


def to_percentile(df: pd.DataFrame, field: str, symbol="%", divisor=None, remove_nan=True):
    """
    Converts a specified field in a DataFrame to percentile values, handling symbols and NaNs.

    :param df: The DataFrame containing the field to be converted.
    :param field: The name of the field to be converted.
    :param symbol: The symbol to be removed from the field.
    :param divisor: Optional divisor to adjust the values.
    :param remove_nan: Whether to replace NaN values with 0.
    :return: The DataFrame with the converted field.
    """
    df[field] = (
        df[field]
        .replace("[\\{},) ]".format(symbol), "", regex=True)
        .replace("[(]", "-", regex=True)
        .replace("[ ]+", np.nan, regex=True)
        .str.strip(",")
    )
    df[field] = pd.to_numeric(df[field], errors="coerce")
    df[field] = df[field].replace([-np.inf, np.inf], np.nan)
    if divisor is not None:
        df[field] = df[field].apply(lambda x: x / divisor)
    if remove_nan is True:
        df[field] = df[field].fillna(0)
    else:
        df[field] = df[field].where(df[field].notnull(), None)
    return df


def split_cols(df: pd.DataFrame, field: str, separator=",", columns=[], numcols=2):
    """
    Splits a specified field in a DataFrame into multiple columns based on a separator.

    :param df: The DataFrame containing the field to be split.
    :param field: The name of the field to be split.
    :param separator: The separator to use for splitting the field.
    :param columns: The list of new column names.
    :param numcols: The number of columns to split into.
    :return: The DataFrame with the split columns.
    """
    if not columns:
        columns = [f"{field}_part{i + 1}" for i in range(numcols)]

    if isinstance(columns, list):
        numcols = len(columns)
        try:
            # Split each row and fill missing parts with None
            split_data = df[field].apply(
                lambda x: (x.split(separator) + [None] * numcols)[:numcols]
            )

            # Assign the split data to new columns in the DataFrame
            df[columns] = pd.DataFrame(split_data.tolist(), index=df.index)
        except Exception as err:
            print(
                "Error on split_cols:", err
            )
    return df


def startofweek(df: pd.DataFrame, field: str, column=""):
    """
    Calculates the start of the week for a specified date field in a DataFrame.

    :param df: The DataFrame containing the date field.
    :param field: The name of the new field to store the start of the week.
    :param column: The name of the date field to be used.
    :return: The DataFrame with the start of the week field.
    """
    try:
        df[field] = df[column] - pd.to_timedelta(df[column].dt.dayofweek, unit="d")
    except Exception as err:
        print(f'Error in startofweek: {err}')
    return df


def endofweek(df: pd.DataFrame, field: str, column=""):
    """
    Calculates the end of the week for a specified date field in a DataFrame.

    :param df: The DataFrame containing the date field.
    :param field: The name of the new field to store the end of the week.
    :param column: The name of the date field to be used.
    :return: The DataFrame with the end of the week field.
    """
    try:
        df[field] = df[column] - pd.to_timedelta(df[column].dt.dayofweek, unit="d") + pd.to_timedelta(6, unit="d")
    except Exception as err:
        print(f'Error in endofweek: {err}')
    return df

def to_excel_date(df: pd.DataFrame, field: str, column: str = None):
    """
    Converts a datetime column to a naive datetime suitable for Excel.

    Args:
        :param df: The DataFrame containing the date field.
        :param field: The name of the new field to store the converted datetime.
        :param column: The name of the date field to be used.
        :return: The DataFrame with the converted datetime field.
    """
    if not column:
        column = field

    try:
        # Ensure the column is converted to datetime
        df[column] = pd.to_datetime(df[column], errors='coerce')

        # Handle timezone-aware datetimes
        if df[column].dt.tz is not None:
            # Convert to UTC and then remove the timezone
            df[field] = df[column].dt.tz_convert('UTC').dt.tz_localize(None)
        else:
            # Already naive, no tz conversion needed
            df[field] = df[column].dt.tz_localize(None)

    except Exception as e:
        print('Error on to_excel_date: ', e)
        raise e  # Re-raise the exception after logging it

    return df


def to_rethink_date(df: pd.DataFrame, field: str, column: str = None, tz: str = 'UTC'):
    """to_rethink_date.

    Add Timezone information to a Datetime Column.

    Useful to be saved into RethinkDB.

    Args:
        :param df: The DataFrame containing the date field.
        :param field: The name of the new field to store the formatted datetime.
        :param column: The name of the date field to be used.
        :param tz: The timezone to apply if the column does not have one.
        :return: The DataFrame with the formatted datetime field.
    """
    if not column:
        column = field

    # Check if the column has a timezone, and localize if not
    if df[column].dt.tz is None:
        df[column] = df[column].dt.tz_localize(tz)

    # Format the datetime to ISO 8601 string
    df[field] = df[column].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    return df


def xlsdate(excel_time):
    """
    Converts an Excel date serial number to a pandas datetime object.

    :param excel_time: The Excel date serial number.
    :return: The corresponding pandas datetime object.
    """
    if excel_time:
        return pd.to_datetime("1899-12-30") + pd.to_timedelta(excel_time, "D")
    else:
        return pd.NaT


def excel_to_date(df: pd.DataFrame, field: str):
    """
    Converts a specified field in a DataFrame from Excel date serial numbers to pandas datetime objects.

    :param df: The DataFrame containing the field to be converted.
    :param field: The name of the field to be converted.
    :return: The DataFrame with the converted field.
    """
    try:
        df[field] = df.apply(lambda row: xlsdate(row[field]), axis=1)
    except Exception as err:
        print("Error in excel_to_date:", err)
    return df


def is_datetime_column(column) -> bool:
    """
    Checks if a column is of datetime type.

    :param column: The column to be checked.
    :return: True if the column is of datetime type, False otherwise.
    """
    return pd.api.types.is_datetime64_any_dtype(column)


def is_time_column(column) -> bool:
    """
    Checks if a column is of time type.

    :param column: The column to be checked.
    :return: True if the column is of time type, False otherwise.
    """
    return pd.api.types.is_datetime64_any_dtype(column) and not pd.api.types.is_timedelta64_dtype(column)


def extract(df: pd.DataFrame, field: str, column="", to_date=None, value="day"):
    """
    Extracts a specified date component from a datetime field in a DataFrame.

    :param df: The DataFrame containing the datetime field.
    :param field: The name of the new field to store the extracted component.
    :param column: The name of the datetime field to be used.
    :param to_date: Optional parameter to convert the field to date.
    :param value: The date component to extract (e.g., "day", "month", "year").
    :return: The DataFrame with the extracted date component field.
    """
    if not column:
        column = field
    if to_date is not None:
        df = convert_to_date(df, field, to_date)

    value = value.lower()

    if not is_datetime_column(df[column]):
        # convert to datetime, but only if not time:
        if not is_time_column(df[column]):
            try:
                df[column] = pd.to_datetime(df[column])
            except TypeError:
                pass

    if value == "day":
        df[field] = df[column].dt.day
    elif value == "dow" or value.lower() == "dayofweek":
        df[field] = df[column].dt.dayofweek
    elif value == "doy" or value.lower() == "dayofyear":
        df[field] = df[column].dt.dayofyear
    elif value == "month":
        df[field] = df[column].dt.month
        df[field] = df[field].astype("Int64")
    elif value == "year":
        df[field] = df[column].dt.year
        df[field] = df[field].astype("Int64")
    elif value == "quarter":
        df[field] = df[column].dt.quarter
    elif value == "hour":
        try:
            df[field] = df[column].dt.hour
        except AttributeError:
            df[field] = df[column].dt.components.hours
    elif value == "minute":
        df[field] = df[column].dt.minute
    elif value == "second":
        df[field] = df[column].dt.second
    elif value == "ldom":
        df[field] = df[column] + MonthEnd(0)
    else:
        df[field] = df[column].dt.to_period(value)
    return df


def date_trunc(df: pd.DataFrame, field: str, column: str, value: str = "dow", iso: bool = True):
    """
    Truncates a datetime field in a DataFrame to the start of a specified time unit.

    :param df: The DataFrame containing the datetime field.
    :param field: The name of the new field to store the truncated date.
    :param column: The name of the datetime field to be used.
    :param value: The time unit to truncate to (e.g., "dow" for day of week).
    :param iso: Whether to use ISO week date system.
    :return: The DataFrame with the truncated date field.
    """
    if value == "dow":
        unit = "d"
        if iso is False:
            df[field] = df[column] - pd.to_timedelta(
                (df[column].dt.weekday + 1) - 7, unit="d"
            )
        else:
            df[field] = df[column] - pd.to_timedelta(
                df[column].dt.dayofweek, unit=unit
            )
    return df


def date_diff(df: pd.DataFrame, field: str, end: str, start: str, unit: str = "s"):
    """
    Calculates the difference between two date fields in a DataFrame.

    :param df: The DataFrame containing the date fields.
    :param field: The name of the new field to store the difference.
    :param end: The name of the end date field.
    :param start: The name of the start date field.
    :param unit: The unit of time for the difference (default is seconds).
    :return: The DataFrame with the date difference field.
    """
    df[field] = (df[end] - df[start]) / np.timedelta64(1, unit)
    return df


def replace_nulls(df: pd.DataFrame, field: str = "", value: Any = None):
    """
    Replaces null values in a specified field with a given value.

    :param df: The DataFrame containing the field.
    :param field: The name of the field to replace nulls in.
    :param value: The value to replace nulls with.
    :return: The DataFrame with nulls replaced.
    """
    try:
        df[field] = df[field].fillna(value)
    except Exception as err:
        print(err)
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


def fill_column(df: pd.DataFrame, field: str, value: Any, variables: Any = None):
    """
    Fills a specified field with a given value, optionally using a dictionary of variables.

    :param df: The DataFrame containing the field.
    :param field: The name of the field to fill.
    :param value: The value to fill the field with.
    :param variables: Optional dictionary of variables to use for filling.
    :return: The DataFrame with the filled field.
    """
    if variables is not None:
        if value in variables:
            value = variables[value]
    if field not in df.columns.values:
        df[field] = value
    else:
        df[field] = df[field].replace(df[field], value)
    return df


def fn_get_timezone(latitudes, longitudes):
    """
    Determines the timezone for given latitudes and longitudes.

    :param latitudes: Array of latitude values.
    :param longitudes: Array of longitude values.
    :return: Array of timezone names.
    """
    tz = timezonefinder.TimezoneFinder()
    zones = np.full_like(latitudes, DEFAULT_TIMEZONE, dtype=object)
    mask = ~np.isnan(latitudes) & ~np.isnan(longitudes)
    latitudes = latitudes[mask]
    longitudes = longitudes[mask]
    if len(latitudes) > 0:
        zones[mask] = [tz.timezone_at(lng=long, lat=lat) for lat, long in zip(latitudes, longitudes)]
        zones[zones == "uninhabited"] = DEFAULT_TIMEZONE
        zones[zones is None] = DEFAULT_TIMEZONE
    return zones


def get_timezone(df: pd.DataFrame, field: str, lat: str, long: str) -> pd.DataFrame:
    """
    Adds a timezone field to a DataFrame based on latitude and longitude columns.

    :param df: The DataFrame containing the latitude and longitude columns.
    :param field: The name of the new field to store the timezone.
    :param lat: The name of the latitude column.
    :param long: The name of the longitude column.
    :return: The DataFrame with the timezone field.
    """
    if not set([lat, long]).issubset(df.columns):
        df[field] = None
    try:
        df[lat] = pd.to_numeric(df[lat], errors='coerce')
        df[long] = pd.to_numeric(df[long], errors='coerce')
    except Exception as err:
        print("Failed to convert Lat/Long to Float: ", err)
    try:
        df[field] = fn_get_timezone(df[lat].values, df[long].values)
    except Exception as err:
        print("GET TIMEZONE ERROR: ", err)
    return df


def split_into_series(df: pd.DataFrame, field: str):
    """
    Splits a specified field in a DataFrame into a series of columns.

    :param df: The DataFrame containing the field to be split.
    :param field: The name of the field to be split.
    :return: The DataFrame with the split series.
    """
    try:
        df = df[field].apply(pd.Series)
    except Exception as err:
        print("Split Series Error: ", err)
    finally:
        return df


def change_timezone(df: pd.DataFrame, field: str, from_tz: str = None, to_tz: str = "UTC"):
    """
    Changes the timezone of a datetime field in a DataFrame.

    :param df: The DataFrame containing the datetime field.
    :param field: The name of the field to change the timezone for.
    :param from_tz: The original timezone of the field.
    :param to_tz: The target timezone for the field.
    :return: The DataFrame with the timezone changed field.
    """
    df[field] = pd.to_datetime(df[field], errors="coerce")
    try:
        infer_dst = np.array([False] * df.shape[0])
        df[field] = (
            df[field].dt.tz_localize(from_tz, ambiguous=infer_dst).dt.tz_convert(to_tz)
        )
    except Exception as err:
        print("Error Changing timezone: ", err)
    finally:
        return df


def to_numeric(df: pd.DataFrame, field: str, remove_alpha: bool = True, to_integer: bool = False):
    """
    Converts a column of a pandas DataFrame to numeric values, optionally removing
    non-numeric characters and converting to integer.

    Args:
    - df (pd.DataFrame): The DataFrame containing the column to be converted.
    - field (str): The name of the column to be converted.
    - remove_alpha (bool, optional): If True, removes non-numeric characters from the
      column before converting. Default is True.
    - to_integer (bool, optional): If True, converts the column to integer dtype
      after converting to numeric. Default is False.

    Returns:
    - pd.DataFrame: The input DataFrame with the converted column.

    Raises:
    - Exception: If an error occurs during the conversion process.

    Example:
    >>> import pandas as pd
    >>> data = {'A': ['1', '2', '3'], 'B': ['4', '5', '6a']}
    >>> df = pd.DataFrame(data)
    >>> df = to_numeric(df, 'B', remove_alpha=True, to_integer=True)
    >>> print(df)
         A  B
    0  1.0  4
    1  2.0  5
    2  3.0  6
    """
    try:
        if remove_alpha is True:
            df[field] = df[field].astype("string")
            df[field] = df[field].str.replace(r"\D+", "", regex=True)
        df[field] = pd.to_numeric(df[field], errors="coerce")
        if to_integer is True:
            df[field] = df[field].astype("Int64", copy=False)
    except Exception as err:
        print(f"TO Integer {field}:", err)
    return df


def remove_scientific_notation(df: pd.DataFrame, field: str):
    """
    Removes scientific notation from a specified field in a DataFrame.

    :param df: The DataFrame containing the field.
    :param field: The name of the field to remove scientific notation from.
    :return: The DataFrame with the field formatted as a string without scientific notation.
    """
    # df[field].apply(lambda x: '%.17f' % x).values.tolist()
    pd.set_option("display.float_format", lambda x: f"%.{len(str(x % 1)) - 2}f" % x)
    # use regular expressions to remove scientific notation
    df[field] = df[field].str.replace(
        r"(\d+\.\d+)([Ee])([\+\-]?\d+)", r"\1*10^\3", regex=True
    )
    df[field] = df[field].str.replace(
        r"(\d+)([Ee])([\+\-]?\d+)", r"\1*10^\3", regex=True
    )
    df[field] = df[field].str.replace(r"\*", r"", regex=True)
    # convert the column to a string data type
    df[field] = df[field].astype("string")
    return df


def first_not_null(
    df: pd.DataFrame, field: str, columns: list[str]
) -> pd.DataFrame:
    """
    Creates a new column filled with the first non-null value from a list of columns.

    :param df: The DataFrame containing the columns.
    :param field: The name of the new field to store the first non-null value.
    :param columns: The list of columns to check for non-null values.
    :return: The DataFrame with the new field.
    """
    # Create a new Series containing the first non-null value from
    try:
        series = df[columns].apply(lambda x: x.dropna().iloc[0], axis=1)
        # Add the new Series as a new column in the DataFrame
        df[field] = series
    except Exception as err:
        print(f"Error first_not_null {field}:", err)
    return df


def remove_html_tags(text):
    """
    Removes HTML tags from a text string.

    :param text: The text string to clean.
    :return: The cleaned text string.
    """
    soup = BeautifulSoup(text, "html.parser")
    cleaned_text = soup.get_text()
    return cleaned_text


def clean_html_tags(df: pd.DataFrame, field: str) -> pd.DataFrame:
    """
    Cleans all HTML tags from a specified field in a DataFrame.

    :param df: The DataFrame containing the field.
    :param field: The name of the field to clean.
    :return: The DataFrame with the cleaned field.
    """
    try:
        # Apply the 'remove_html_tags' function to the desired column
        df[field] = df[field].apply(remove_html_tags)
    except Exception as err:
        print(f"Error on clean_html_tags {field}:", err)
    return df


def autoincrement(df: pd.DataFrame, field: str) -> pd.DataFrame:
    """
    Fills a specified field with auto-increment values.

    :param df: The DataFrame containing the field.
    :param field: The name of the field to fill with auto-increment values.
    :return: The DataFrame with the auto-increment field.
    """
    try:
        # Add a correlative auto-increment column
        df[field] = range(1, len(df) + 1)
    except Exception as err:
        print(f"Error on autoincrement {field}:", err)
    return df


def row_to_column(
    df: pd.DataFrame,
    field: str,
    column: str,
    row_to_pivot: str,
    value: str,
    pivot: list,
):
    """
    Add a pivoted column to the dataframe based on the given column name.

    Parameters:
    - df: The input dataframe.
    - field: The name of the column to be transposed.
    - pivot: The column name[s] to pivot.
    - value: Column name for extracting the value.

    Returns:
    - Dataframe with the new pivoted column.
    """
    # Filter the dataframe to only include rows with the desired column_name
    try:
        df_filtered = df[df[column] == row_to_pivot]
    except KeyError as e:
        logging.warning(f"Missing Column: {e}")
        return df
    print(df_filtered)
    # Pivot the filtered dataframe
    df_pivot = df_filtered.pivot_table(
        index=pivot, columns=column, values=value, aggfunc="first"
    ).reset_index()

    df_pivot = df_pivot.rename(columns={row_to_pivot: field})

    # Merge the pivoted dataframe with the original dataframe
    df_merged = pd.merge(df, df_pivot, on=pivot, how="left")
    # Drop the original column_name and value columns for the pivoted rows
    df_merged = df_merged.drop(df_merged[(df_merged[column] == row_to_pivot)].index)
    return df_merged


def datetime_to_string(df: pd.DataFrame, field: str, mask: str, column: str = None):
    """
    datetime_to_string.

    Converts a Datetime Column to an string using a Format.

    Args:
        df (pd.DataFrame): Pandas Dataframe.
        field (str): Column used to create a new column.
        column (str): Column used for transformation.
        mask (list): Format Mask for transformation
    """
    if not column:
        column = field
    try:
        df[field] = df[column].dt.strftime(mask)
    except Exception as err:
        print(f"Error on datetime_to_string {field}:", err)
    return df


def flatten_array_row(row, field: str, attribute=None, prefix=""):
    """
    Flattens a dictionary or nested dictionary in a specified field of a DataFrame row.

    :param row: The DataFrame row containing the field.
    :param field: The name of the field to flatten.
    :param attribute: Optional attribute to specify a nested dictionary within the field.
    :param prefix: Optional prefix to add to the new flattened keys.
    :return: The DataFrame row with the flattened field.
    """
    if pd.notna(row[field]):
        if attribute is not None:
            # Check if the attribute exists in the field
            if attribute in row[field]:
                for key, value in row[field][attribute].items():
                    row[f"{prefix}{key}"] = value
        else:
            # If no attribute is specified, assume the field itself is a dictionary
            for key, value in row[field].items():
                row[f"{prefix}{key}"] = value
    return row


def flatten_array(
    df: pd.DataFrame, field: str, attribute: str = None, prefix: str = ""
):
    """flatten_array.
    Converts a nested value in a column to a flat fields in dataframe.

    Args:
        df (pd.DataFrame): Pandas Dataframe.
        field (str): Column used to create a new column.
        column (str): Column used for transformation.
        mask (list): Format Mask for transformation
    """
    try:
        df = df.apply(flatten_array_row, axis=1, args=(field, attribute, prefix))
    except Exception:
        print(f"Error on flatten_array {field}:")
    return df


def extract_json_data(row, field: str, column: str):
    """
    Extracts data from a JSON-like dictionary or list in a specified column of a DataFrame row.

    :param row: The DataFrame row containing the JSON-like data.
    :param field: The key to extract from the JSON data.
    :param column: The name of the column containing the JSON data.
    :return: The extracted data or None if the key is not found.
    """
    try:
        rows = row[column]
        if isinstance(rows, list):
            # Extract the column from the dictionary column
            _data = [row[field] for row in rows]
            return _data
        else:
            # Extract the column from the dictionary column
            return row[column][field]
    except KeyError:
        # Return None or some default value if 'column' key is not found
        return None


def extract_column(df: pd.DataFrame, field: str, column: str):
    """
    extract_column.
    Converts a nested JSON value in a column in dataframe.

    Args:
        df (pd.DataFrame): Pandas Dataframe.
        field (str): Column used to create a new column.
        column (str): Column used for transformation
    """
    try:
        df[field] = df.apply(extract_json_data, axis=1, args=(field, column))
    except Exception as exc:
        print(f"Error on extract_column {field}: {exc}")
    return df


def sum_columns(df: pd.DataFrame, field: str, startswith: str = None, columns: list = None):
    """sum_columns.
    Sums all columns that start with a given string.

    Args:
        df (pd.DataFrame): Pandas Dataframe.
        field (str): Column used to create a new column.
        startswith (str): String to filter columns.
        columns (list): List of columns.
    """
    if startswith is not None:
        try:
            # Filter columns that start with the given string
            columns = [col for col in df.columns if col.startswith(startswith)]
            # Sum the values in the columns
            df[field] = df[columns].sum(axis=1)
        except Exception as exc:
            print(f"Error on sum_columns {field}: {exc}")
    elif columns is not None:
        try:
            if columns and isinstance(columns, list):
                df[field] = df[columns].astype(float).sum(axis=1)
        except Exception as exc:
            print(f"Error on sum_columns {field}: {exc}")
    return df


def sub_columns(df: pd.DataFrame, field: str, startswith: str = None, columns: list = None):
    """sub_columns.
    Subtract all columns that start with a given string.

    Args:
        df (pd.DataFrame): Pandas Dataframe.
        field (str): Column used to create a new column.
        startswith (str): String to filter columns.
        columns (list): List of columns.
    """
    if startswith is not None:
        try:
            # Filter columns that start with the given string
            columns = [col for col in df.columns if col.startswith(startswith)]
            # Sum the values in the columns
            df[field] = reduce(lambda x, y: x.sub(y), [df[col] for col in columns])
        except Exception as exc:
            print(f"Error on sub_columns {field}: {exc}")
    elif columns is not None:
        try:
            if columns and isinstance(columns, list):
                df[field] = reduce(lambda x, y: x.sub(y), [df[col].astype(float) for col in columns])
        except Exception as exc:
            print(f"Error on sub_columns {field}: {exc}")
    return df


def autoincrement_by_group(df: pd.DataFrame, field: str, group_column: str) -> pd.DataFrame:
    """
    autoincrement_by_group.

    Autoincrement a column (if empty or NAN) based on a group column.

    Args:
        df (pd.DataFrame): Pandas Dataframe.
        field (str): Column used to create a new column.
        group_column (str): Column used for grouping.

    Returns:
        pd.DataFrame: Dataframe with the new autoincremented column.
    """
    try:
        # Apply a group function to every row in group:
        def auto_group_function(group):
            i = 1
            for idx in group.index:
                if pd.isna(group.at[idx, field]) or group.at[idx, field] == "":
                    group.at[idx, field] = i
                    i += 1
            return group

        # Apply the group function to the dataframe
        df = df.groupby(group_column).apply(auto_group_function).reset_index(drop=True)
        return df
    except Exception as err:
        print(f"Error on autoincrement_by_group {field}:", err)
        return df


def autoincrement_by_column(
    df: pd.DataFrame,
    field: str,
    group_column: str,
    suffix: bool = True,
    separator: str = ''
) -> pd.DataFrame:
    """
    autoincrement_by_column.

    Add an autoincrement number (prefix, suffix) to a column based on a group column.

    Args:
        df (pd.DataFrame): Pandas Dataframe.
        field (str): Column used to create a new column.
        group_column (str): Column used for grouping.

    Returns:
        pd.DataFrame: Dataframe with the new autoincremented column.
    """
    try:
        # Create a group key that will maintain order within groups
        df['group_key'] = df.groupby(group_column).cumcount() + 1
        # Generate the new field values by combining the original field value with the group_key
        if suffix:
            df[field] = df.apply(lambda x: f"{x[field]}{separator}{x['group_key']}", axis=1)
        else:
            df[field] = df.apply(lambda x: f"{x['group_key']}{separator}{x[field]}", axis=1)

        # Clean up the temporary group_key column
        df.drop('group_key', axis=1, inplace=True)
        return df
    except Exception as err:
        print(f"Error on autoincrement_by_group {field}:", err)
        return df

def fill_map(
    df: pd.DataFrame,
    field: str,
    column: str
) -> pd.DataFrame:
    try:
        # Replace empty strings with None
        df[field] = df[field].replace('', None)

        # Create the map explicitly
        mapa = df.groupby(column)[field].apply(
            lambda x: x.dropna().iloc[0] if not x.dropna().empty else None
        ).to_dict()

        # Apply the map explicitly
        df[field] = df.apply(
            lambda row: mapa.get(row[column], row[field]) if pd.isnull(row[field]) else row[field],
            axis=1
        )

        return df
    except Exception as err:
        print(f"Error en fill_map {field}:", err)
        return df

def fill_current_week_midnight(
    df: pd.DataFrame,
    field: str
):
    # Get today's date in UTC
    tz = ZoneInfo("UTC")
    today = datetime.datetime.now(tz)

    # Calculate the first day of the current week (Monday)
    start_of_week = today - timedelta(days=today.weekday())  # Monday of this week
    start_of_week = start_of_week.replace(
        hour=0, minute=0, second=0, microsecond=0
    )  # Set time to midnight

    # Convert the start of the week to epoch time (in UTC)
    week_start_epoch = calendar.timegm(start_of_week.timetuple())

    # Add a new column with the same epoch value for all rows
    df[field] = week_start_epoch

    return df

def reduce_fields(
    df: pd.DataFrame,
    field: str,
    columns: list
) -> pd.DataFrame:
    """
    Reduce the number of fields in the nested column.
    """
    try:
        df[field] = df[field].apply(
            lambda nested_items: [
                {col: nested_item[col] for col in columns if col in nested_item}
                for nested_item in nested_items
            ] if isinstance(nested_items, list) and nested_items else []
        )
    except Exception as err:
        print(f"Error in reduce_fields for field {field}:", err)
    return df


def fill_current_date_midnight(
    df: pd.DataFrame,
    field: str,
    as_epoch: bool = False,
) -> pd.DataFrame:
    """fill_current_date_midnight.
        Function to fill the DataFrame with the current date at midnight in epoch time.
    """
    # Get today's date in UTC
    tz = ZoneInfo("UTC")
    today = datetime.datetime.now(tz)

    # Set time to midnight
    midnight_today = today.replace(hour=0, minute=0, second=0, microsecond=0)

    if as_epoch:
        # Convert midnight to epoch time (in UTC)
        midnight_today = calendar.timegm(midnight_today.timetuple())

    # Add a new column with the same epoch value for all rows
    df[field] = midnight_today

    return df


def create_daterange_from_columns(df: pd.DataFrame, field: str, start_column: str, end_column: str):
    """
    Crea una nueva columna con el formato 'daterange' de PostgreSQL usando dos columnas.
    Si la columna de fin está vacía (NULL o cadena vacía), se usará 'infinity' como fecha de fin.

    :param df: El DataFrame que contiene las columnas de fechas de inicio y fin.
    :param field: El nombre de la nueva columna para almacenar el rango de fechas.
    :param start_column: Nombre de la columna con la fecha de inicio.
    :param end_column: Nombre de la columna con la fecha de fin (puede estar vacía).
    :return: DataFrame con la nueva columna de rango de fechas.
    """
    df[field] = df.apply(
        lambda row: f"[{row[start_column]}, infinity)"
        if pd.isnull(row[end_column]) or row[end_column].strip() == ""
        else f"[{row[start_column]}, {row[end_column]})",
        axis=1
    )
    return df

def into_columns(
    df: pd.DataFrame,
    field: str,
    column_names: list,
    enumerate_cols: Optional[int] = None,
    axis: int = 1,
    fill_zeros: bool = True
) -> pd.DataFrame:
    """into_columns.

    Split a column into several columns providing the column names to be used.
    """
    def validate_str(value):
        if isinstance(value, str):
            try:
                return ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return value

    def validate_or_fill(value):
        """
        Ensures the value is a list and matches the expected length.
        If not, replaces it with a zero-filled list.
        """
        if isinstance(value, str):
            try:
                value = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return [0] * len(column_names)
        if isinstance(value, list) and len(value) == len(column_names):
            return value
        return [0] * len(column_names)
    if fill_zeros is True:
        df[field] = df[field].apply(validate_or_fill)
    else:
        df[field] = df[field].apply(validate_str)
    try:
        if enumerate_cols:
            column_names = [
                f"{col}_{num}" for col in column_names for num in range(1, enumerate_cols + 1)
            ]
        df_columns = pd.DataFrame(df[field].to_list(), columns=column_names)
        df = pd.concat([df, df_columns], axis=axis)
    except Exception as e:
        print(
            f"Error on into_columns function: {e}"
        )
    return df


def flatten_column(row, field: str, column: str, agg_column: str = None, agg_func: str = None):
    _data = row[column]
    flattened_data = []
    for key, val in _data.items():
        if agg_func and agg_func == 'sum':
            total_data = sum(data[agg_column] for data in val.values())
            flattened_data.append({field: key, column: total_data})
    return pd.DataFrame(flattened_data)


def flatten(
    df: pd.DataFrame,
    field: str,
    column: str = None,
    flatten_func: callable = None,
    agg_column: str = None,
    agg_func: callable = None,
) -> pd.DataFrame:
    """
    Flattens a DataFrame by applying a row-wise transformation function.
    Optionally applies an aggregation function to a specified column.

    Args:
        df (pd.DataFrame): Input DataFrame.
        field (str): The result column name.
        column (str): Column containing nested data to be flattened.
        flatten_func (callable): A function to transform a single row into a new DataFrame.
        agg_column (str, optional): Column name to apply the aggregation function.
        agg_func (callable, optional): Aggregation function to apply to the specified column.

    Returns:
        pd.DataFrame: Flattened and optionally aggregated DataFrame.
    """
    try:
        if not flatten_func:
            flatten_func = flatten_column
        if not column:
            column = field
        # Apply the flatten_row_func to each row
        args = {
            "field": field,
            "column": column,
            "flatten_func": flatten_func,
            "agg_column": agg_column,
            "agg_func": agg_func,
        }
        flattened_rows = df.apply(flatten_func, axis=1, **args).to_list()

        # Combine the resulting DataFrames
        flattened_df = pd.concat(flattened_rows, ignore_index=True)

        # If an aggregation is specified
        if agg_column and agg_func:
            grouped = flattened_df.groupby(list(flattened_df.columns.difference([agg_column])))
            flattened_df = grouped[agg_column].agg(agg_func).reset_index()

        return flattened_df
    except Exception as e:
        print(f"Error in flatten function: {e}")
        raise
