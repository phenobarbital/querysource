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
