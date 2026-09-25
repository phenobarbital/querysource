"""Shared fixtures for the qsurl test suite."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

CORPUS_PATH = Path(__file__).with_name("corpus.json")


@pytest.fixture(scope="session")
def corpus() -> list[dict]:
    """The parity corpus (created by TASK-767); skips when it does not exist yet."""
    if not CORPUS_PATH.exists():
        pytest.skip("tests/qsurl/corpus.json not present yet")
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def stores_df() -> pd.DataFrame:
    """Eight stores covering every residual leaf branch (mixed case, None, '', tz datetimes)."""
    return pd.DataFrame(
        [
            {
                "store_id": 1,
                "name": "Acme Store",
                "city": "San Francisco",
                "state_code": "CA",
                "price": 19.99,
                "opened": "2024-01-01T00:00:00Z",
                "closed_at": None,
            },
            {
                "store_id": 2,
                "name": "acme outlet",
                "city": "san diego",
                "state_code": "CA",
                "price": 5.5,
                "opened": "2023-06-15T08:30:00+02:00",
                "closed_at": "2024-05-01T00:00:00Z",
            },
            {
                "store_id": 3,
                "name": "BETA MART",
                "city": None,
                "state_code": "NY",
                "price": 100.0,
                "opened": "2022-11-20T00:00:00Z",
                "closed_at": None,
            },
            {
                "store_id": 4,
                "name": "Beta Mart Annex",
                "city": "",
                "state_code": "NY",
                "price": 0.0,
                "opened": "2021-03-05T12:00:00+02:00",
                "closed_at": "2022-01-01T00:00:00Z",
            },
            {
                "store_id": 5,
                "name": "Gamma Goods",
                "city": "Austin",
                "state_code": "TX",
                "price": 42.5,
                "opened": "2020-07-04T00:00:00Z",
                "closed_at": None,
            },
            {
                "store_id": 6,
                "name": "gamma express",
                "city": "houston",
                "state_code": "TX",
                "price": 15.25,
                "opened": "2019-09-09T09:09:00Z",
                "closed_at": None,
            },
            {
                "store_id": 7,
                "name": "Delta Depot",
                "city": "Seattle",
                "state_code": "WA",
                "price": 250.0,
                "opened": "2018-12-31T23:59:00+02:00",
                "closed_at": "2020-01-01T00:00:00Z",
            },
            {
                "store_id": 8,
                "name": "delta express",
                "city": "spokane",
                "state_code": "WA",
                "price": 3.75,
                "opened": "2025-02-14T00:00:00Z",
                "closed_at": None,
            },
        ]
    )
