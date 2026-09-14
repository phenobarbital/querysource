from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from querysource.outputs.tables.TableOutput.postgres import PgOutput
from querysource.outputs.tables.TableOutput.table import TableOutput


def _output_with_constraints(constraints):
    output = object.__new__(PgOutput)
    output._engine = MagicMock()
    output.logger = MagicMock()
    connection = output._engine.begin.return_value.__enter__.return_value
    connection.execute.return_value.all.return_value = constraints
    return output, connection


def _statements(connection):
    return [str(call.args[0]) for call in connection.execute.call_args_list]


def test_matching_primary_key_is_reused_for_upsert():
    output, connection = _output_with_constraints([("p", ["id"])])

    output.ensure_upsert_constraint("public", "customers", ["id"])

    assert len(connection.execute.call_args_list) == 1
    assert "pg_constraint" in _statements(connection)[0]
    output.logger.warning.assert_not_called()


def test_matching_unique_constraint_is_reused_for_upsert():
    output, connection = _output_with_constraints([("u", ["external_id"])])

    output.ensure_upsert_constraint("public", "customers", ["external_id"])

    assert len(connection.execute.call_args_list) == 1
    output.logger.warning.assert_not_called()


def test_matching_composite_constraint_is_reused_regardless_of_column_order():
    output, connection = _output_with_constraints(
        [("u", ["external_id", "source"])]
    )

    output.ensure_upsert_constraint(
        "public", "customers", ["source", "external_id"]
    )

    assert len(connection.execute.call_args_list) == 1


def test_new_table_without_primary_key_creates_configured_primary_key():
    output, connection = _output_with_constraints([])

    output.ensure_upsert_constraint(
        "public", "customers", ["id"], create_primary_key=True
    )

    assert _statements(connection)[1] == (
        'ALTER TABLE "public"."customers" ADD PRIMARY KEY ("id")'
    )
    output.logger.warning.assert_called_once()


def test_existing_table_without_primary_key_creates_unique_constraint():
    output, connection = _output_with_constraints([])

    output.ensure_upsert_constraint("public", "customers", ["id"])

    assert _statements(connection)[1] == (
        'ALTER TABLE "public"."customers" ADD UNIQUE ("id")'
    )
    output.logger.warning.assert_called_once()


def test_different_primary_key_creates_unique_upsert_constraint():
    output, connection = _output_with_constraints([("p", ["id"])])

    output.ensure_upsert_constraint(
        "public", "customers", ["external_id", "source"]
    )

    assert _statements(connection)[1] == (
        'ALTER TABLE "public"."customers" '
        'ADD UNIQUE ("external_id", "source")'
    )
    output.logger.warning.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("table_exists", "create_primary_key"),
    [(True, False), (False, True)],
)
async def test_table_output_only_requests_primary_key_for_new_tables(
    table_exists, create_primary_key
):
    data = pd.DataFrame({"id": [1], "name": ["Ana"]})
    output = TableOutput(
        data=data,
        tablename="customers",
        schema="public",
        if_exists="append",
        pk=["id"],
    )
    output._engine = MagicMock()
    output._engine.is_external = False
    output._engine.engine.return_value = MagicMock()
    output._engine.table_exists.return_value = table_exists

    with patch.object(pd.DataFrame, "to_sql"):
        await output.table_output(output, data)

    output._engine.ensure_upsert_constraint.assert_called_once_with(
        "public",
        "customers",
        ["id"],
        create_primary_key=create_primary_key,
    )
