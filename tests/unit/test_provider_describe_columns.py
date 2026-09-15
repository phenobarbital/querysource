"""FEAT-148 TASK-738 — provider describe_columns hook."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from querysource.providers.abstract import BaseProvider
from querysource.providers.pg import pgProvider


async def test_base_describe_columns_from_names():
    """Test BaseProvider.describe_columns returns typed dicts from column names."""
    # Create a mock provider that has columns() returning a list of names
    provider = MagicMock(spec=BaseProvider)
    provider.columns = AsyncMock(return_value=["a", "b", "c"])
    # Use the actual describe_columns method from BaseProvider
    result = await BaseProvider.describe_columns(provider)
    assert result == [
        {"name": "a", "type": None},
        {"name": "b", "type": None},
        {"name": "c", "type": None},
    ]


async def test_base_describe_columns_from_dicts():
    """Test BaseProvider.describe_columns handles dicts with name/type."""
    provider = MagicMock(spec=BaseProvider)
    provider.columns = AsyncMock(return_value=[
        {"name": "id", "type": "int4"},
        {"name": "created_at", "type": "timestamp"},
    ])
    result = await BaseProvider.describe_columns(provider)
    assert result == [
        {"name": "id", "type": "int4"},
        {"name": "created_at", "type": "timestamp"},
    ]


async def test_base_describe_columns_attribute_error_returns_empty():
    """Test BaseProvider.describe_columns returns [] on AttributeError."""
    provider = MagicMock(spec=BaseProvider)
    provider.columns = AsyncMock(side_effect=AttributeError("no _qs"))
    result = await BaseProvider.describe_columns(provider)
    assert result == []


async def test_base_describe_columns_not_implemented_error_returns_empty():
    """Test BaseProvider.describe_columns returns [] on NotImplementedError."""
    provider = MagicMock(spec=BaseProvider)
    provider.columns = AsyncMock(side_effect=NotImplementedError("not supported"))
    result = await BaseProvider.describe_columns(provider)
    assert result == []


async def test_base_describe_columns_empty_returns_empty():
    """Test BaseProvider.describe_columns returns [] when columns() returns empty."""
    provider = MagicMock(spec=BaseProvider)
    provider.columns = AsyncMock(return_value=[])
    result = await BaseProvider.describe_columns(provider)
    assert result == []


async def test_pg_describe_columns_types_and_columns_unchanged():
    """Test pgProvider.describe_columns returns typed columns and doesn't modify _columns."""
    # Create a pgProvider instance without calling __init__
    provider = pgProvider.__new__(pgProvider)
    provider._query = "SELECT id, name, created_at FROM users"
    provider._columns = []  # Start empty
    provider._connection = MagicMock()

    # Mock the connection context manager - needs to be an async context manager
    mock_stmt = MagicMock()
    # Simulate asyncpg attribute objects
    mock_stmt.get_attributes.return_value = [
        SimpleNamespace(name="id", type=SimpleNamespace(name="int4")),
        SimpleNamespace(name="name", type=SimpleNamespace(name="varchar")),
        SimpleNamespace(name="created_at", type=SimpleNamespace(name="timestamp")),
    ]
    # prepare returns a tuple (stmt, None) - needs to be awaitable
    mock_conn = AsyncMock()
    mock_conn.prepare = AsyncMock(return_value=(mock_stmt, None))
    # The connection() method returns an async context manager
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)
    provider._connection.connection = AsyncMock(return_value=mock_conn)

    # Call describe_columns
    result = await provider.describe_columns()

    # Verify typed output
    assert result == [
        {"name": "id", "type": "int4"},
        {"name": "name", "type": "varchar"},
        {"name": "created_at", "type": "timestamp"},
    ]
    # Verify _columns is unchanged (still empty)
    assert provider._columns == []


async def test_pg_describe_columns_empty_query():
    """Test pgProvider.describe_columns returns [] when _query is empty."""
    provider = pgProvider.__new__(pgProvider)
    provider._query = ""
    provider._columns = ["should_not_change"]
    provider._connection = MagicMock()

    result = await provider.describe_columns()

    assert result == []
    # Verify _columns is unchanged
    assert provider._columns == ["should_not_change"]


async def test_pg_describe_columns_none_query():
    """Test pgProvider.describe_columns returns [] when _query is None."""
    provider = pgProvider.__new__(pgProvider)
    provider._query = None
    provider._columns = ["should_not_change"]
    provider._connection = MagicMock()

    result = await provider.describe_columns()

    assert result == []
    # Verify _columns is unchanged
    assert provider._columns == ["should_not_change"]


async def test_pg_describe_columns_invalid_query_raises_parser_error():
    """Test pgProvider.describe_columns raises ParserError on invalid query."""
    from querysource.exceptions import ParserError

    provider = pgProvider.__new__(pgProvider)
    provider._query = "INVALID SQL"
    provider._columns = []
    provider._connection = MagicMock()

    # Mock connection to raise AttributeError (simulating invalid query)
    mock_conn = AsyncMock()
    mock_conn.prepare = AsyncMock(side_effect=AttributeError("invalid"))
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)
    provider._connection.connection = AsyncMock(return_value=mock_conn)

    with pytest.raises(ParserError) as exc_info:
        await provider.describe_columns()

    assert "Invalid Query or Column for query" in str(exc_info.value)
    # Verify _columns is unchanged
    assert provider._columns == []