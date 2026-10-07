"""Pin the record shape the Mongo/DocumentDB destination hands to the driver.

pandas 3 string columns mark missing values with ``NaN`` (a float), which
``insert_many`` would persist as a NaN double. The destination must send
``None`` so the document stores ``null``.
"""
import math

import pandas as pd

from querysource.utils.dataframes import df_to_records


def test_missing_text_becomes_none_not_nan():
    df = pd.DataFrame({"name": ["x", None], "qty": [1.5, None]})
    records = df_to_records(df)
    assert records[0] == {"name": "x", "qty": 1.5}
    assert records[1]["name"] is None
    assert records[1]["qty"] is None
    assert not any(
        isinstance(v, float) and math.isnan(v)
        for rec in records for v in rec.values()
    )
