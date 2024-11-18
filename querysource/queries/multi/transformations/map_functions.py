import numpy as np
import pandas


def to_timestamp(df: pandas.DataFrame, field: str, remove_nat=False):
    try:
        df[field] = pandas.to_datetime(df[field], errors="coerce")
        df[field] = df[field].where(df[field].notnull(), None)
        df[field] = df[field].astype("datetime64[ns]")
    except Exception as err:
        print(err)
    return df


def from_currency(df, field: str, symbol="$", remove_nan=True):
    df[field] = (
        df[field]
        .replace("[\\{},) ]".format(symbol), "", regex=True)
        .replace("[(]", "-", regex=True)
        .replace("[ ]+", np.nan, regex=True)
        .str.strip(",")
    )
    if remove_nan is True:
        df[field] = df[field].fillna(0)
    df[field] = pandas.to_numeric(df[field], errors="coerce")
    df[field] = df[field].replace([-np.inf, np.inf], np.nan)
    return df

def num_formatter(n):
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
    df: pandas.DataFrame, field: str, not_null=False, fix_negatives: bool = False
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
        df[field] = pandas.to_numeric(df[field], errors="coerce")
        df[field] = df[field].astype("Int64", copy=False)
    except Exception as err:
        print(field, "->", err)
    if not_null is True:
        df[field] = df[field].fillna(0)
    return df
