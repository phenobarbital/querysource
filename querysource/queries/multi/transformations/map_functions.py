from typing import Optional, Union, Any
import numpy as np
import pandas as pd
from ....utils.getfunc import getFunction


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

    if operation in ('add', 'sum', ):
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
