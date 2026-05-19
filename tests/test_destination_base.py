"""
Unit tests for AbstractDestination and DESTINATION_REGISTRY.

Tests credential resolution logic, registry lookup, and the TableOutputAdapter
backward-compatibility wrapper.
"""
import pytest
import pandas as pd
from unittest.mock import patch

from querysource.outputs.destinations.abstract import AbstractDestination
from querysource.outputs.destinations import DESTINATION_REGISTRY, get_destination
from querysource.exceptions import OutputError


# ---------------------------------------------------------------------------
# Concrete test subclass
# ---------------------------------------------------------------------------

class ConcreteDestination(AbstractDestination):
    """Minimal concrete subclass for testing the abstract base."""

    async def run(self) -> pd.DataFrame:
        return self.data


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

class TestCredentialResolution:
    def test_literal_values_passed_through(self):
        """Literal values (not ALL_CAPS) stay unchanged."""
        dest = ConcreteDestination(data=pd.DataFrame())
        result = dest.resolve_credentials({"key": "literal_value", "num": 42})
        assert result == {"key": "literal_value", "num": 42}

    @patch("navconfig.config.get", return_value="resolved_secret")
    def test_navconfig_variables_resolved(self, mock_get):
        """ALL_CAPS_SNAKE_CASE values are resolved via navconfig."""
        dest = ConcreteDestination(data=pd.DataFrame())
        result = dest.resolve_credentials({"client_id": "SHAREPOINT_APP_ID"})
        assert result["client_id"] == "resolved_secret"
        mock_get.assert_called_once_with("SHAREPOINT_APP_ID")

    @patch("navconfig.config.get", return_value=None)
    def test_unresolvable_variable_kept_as_is(self, mock_get):
        """If navconfig returns None, keep the original value."""
        dest = ConcreteDestination(data=pd.DataFrame())
        result = dest.resolve_credentials({"key": "UNKNOWN_VAR"})
        assert result["key"] == "UNKNOWN_VAR"

    def test_mixed_credentials(self):
        """Dict with both literal and navconfig values."""
        dest = ConcreteDestination(data=pd.DataFrame())
        with patch("navconfig.config.get", return_value="prod-secret"):
            result = dest.resolve_credentials({
                "host": "localhost",
                "password": "DB_PASSWORD",
                "port": 5432,
            })
        assert result["host"] == "localhost"
        assert result["password"] == "prod-secret"
        assert result["port"] == 5432

    def test_empty_credentials(self):
        """Empty dict returns empty dict."""
        dest = ConcreteDestination(data=pd.DataFrame())
        result = dest.resolve_credentials({})
        assert result == {}

    def test_single_uppercase_char_not_treated_as_var(self):
        """Single uppercase letter is NOT matched (pattern requires 2+ chars)."""
        dest = ConcreteDestination(data=pd.DataFrame())
        result = dest.resolve_credentials({"key": "A"})
        # "A" matches ^[A-Z][A-Z0-9_]+$ ? — no, needs at least 2 chars total.
        # Actually "A" is length 1; [A-Z0-9_]+ requires 1+ more → total min 2.
        assert result["key"] == "A"

    def test_lowercase_value_not_treated_as_var(self):
        """Lowercase values are not resolved as navconfig vars."""
        dest = ConcreteDestination(data=pd.DataFrame())
        result = dest.resolve_credentials({"key": "some_lower_value"})
        assert result["key"] == "some_lower_value"


# ---------------------------------------------------------------------------
# AbstractDestination interface
# ---------------------------------------------------------------------------

class TestAbstractDestinationInterface:
    def test_data_stored_on_init(self):
        """data attribute is set in __init__."""
        df = pd.DataFrame({"x": [1, 2, 3]})
        dest = ConcreteDestination(data=df)
        assert dest.data is df

    @pytest.mark.asyncio
    async def test_close_is_noop_by_default(self):
        """close() does nothing on the base class implementation."""
        dest = ConcreteDestination(data=pd.DataFrame())
        await dest.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_run_returns_data(self):
        """ConcreteDestination.run() returns self.data."""
        df = pd.DataFrame({"val": [1]})
        dest = ConcreteDestination(data=df)
        result = await dest.run()
        assert result is df


# ---------------------------------------------------------------------------
# DESTINATION_REGISTRY
# ---------------------------------------------------------------------------

class TestDestinationRegistry:
    def test_table_output_registered(self):
        assert "tableOutput" in DESTINATION_REGISTRY
        assert "TableOutput" in DESTINATION_REGISTRY

    def test_get_destination_known(self):
        cls = get_destination("TableOutput")
        assert cls is not None

    def test_get_destination_tableoutput_lowercase(self):
        cls = get_destination("tableOutput")
        assert cls is not None

    def test_get_destination_unknown(self):
        with pytest.raises(OutputError):
            get_destination("NonExistent")

    def test_get_destination_error_message_contains_name(self):
        """Error message should mention the requested name."""
        with pytest.raises(OutputError, match="NonExistent"):
            get_destination("NonExistent")

    def test_registry_values_are_classes(self):
        """Every registry entry should be a class, not an instance."""
        for name, cls in DESTINATION_REGISTRY.items():
            assert isinstance(cls, type), f"Registry['{name}'] is not a class"


# ---------------------------------------------------------------------------
# TableOutputAdapter backward compatibility
# ---------------------------------------------------------------------------

class TestTableOutputAdapter:
    def test_adapter_is_abstract_destination(self):
        from querysource.outputs.destinations import TableOutputAdapter
        df = pd.DataFrame({"a": [1]})
        adapter = TableOutputAdapter(data=df, flavor="postgresql", tablename="t", schema="s")
        assert isinstance(adapter, AbstractDestination)

    def test_adapter_stores_data(self):
        from querysource.outputs.destinations import TableOutputAdapter
        df = pd.DataFrame({"a": [1]})
        adapter = TableOutputAdapter(data=df, flavor="postgresql", tablename="t", schema="s")
        assert adapter.data is df
