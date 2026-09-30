"""FEAT-160 / TASK-828: write-capability predicates for scheduled multi-queries."""
import json

import pytest

import querysource.queries.multi as multi
from querysource.queries.multi import (
    SOURCE_HOOK_KEYS,
    definition_requires_scheduler_grant,
    pipeline_requires_write_grant,
)

READ_ONLY = {"queries": {"a": {"slug": "report_a"}}, "Output": [{"Table": {"table": "t"}}]}
DELETE = {"queries": {"a": {"slug": "report_a"}}, "Output": [{"TableDelete": {"table": "t"}}]}
HOOKED = {"queries": {"a": {"slug": "report_a", "post-hook": "UPDATE t SET x = 1"}}}
SCHEDULE = {"scheduler": {"schedule_type": "cron", "schedule": {"hour": 1}}}


def _definition(pipeline, *, provider="multi", attributes=SCHEDULE):
    raw = pipeline if isinstance(pipeline, str) else json.dumps(pipeline)
    return {"provider": provider, "query_raw": raw, "attributes": attributes}


def test_pipeline_requires_write_grant(monkeypatch):
    assert pipeline_requires_write_grant(DELETE) is True
    assert pipeline_requires_write_grant(HOOKED) is True
    assert pipeline_requires_write_grant(
        {"queries": {"a": {"slug": "s", "pre-hook": ["DELETE FROM t"]}}}
    ) is True
    assert pipeline_requires_write_grant(READ_ONLY) is False
    for malformed in (None, "x", [], {"queries": ["a"]}, {"queries": {"a": "slug"}}, {"Output": "Table"}):
        assert pipeline_requires_write_grant(malformed) is False
    assert SOURCE_HOOK_KEYS == ("pre-hook", "post-hook")
    # WRITE_DESTINATIONS is read at call time (FEAT-156 adds "ExecuteSQL").
    execute = {"queries": {}, "Output": [{"ExecuteSQL": {"sql": "x"}}]}
    monkeypatch.setattr(multi, "WRITE_DESTINATIONS", frozenset({"TableDelete", "ExecuteSQL"}))
    assert pipeline_requires_write_grant(execute) is True


@pytest.mark.parametrize(
    "definition",
    [
        _definition(DELETE, provider="db"),
        _definition(DELETE, attributes=None),
        _definition(DELETE, attributes={}),
        _definition(DELETE, attributes={"scheduler": {}}),
        _definition(READ_ONLY),
        _definition(""),
        {"provider": "multi", "attributes": SCHEDULE},
        {},
    ],
)
def test_definition_requires_scheduler_grant_false(definition):
    assert definition_requires_scheduler_grant(definition) is False


def test_definition_requires_scheduler_grant_true():
    assert definition_requires_scheduler_grant(_definition(DELETE)) is True
    assert definition_requires_scheduler_grant(_definition(HOOKED)) is True
    # attributes stored as a JSON string, query_raw already parsed
    assert definition_requires_scheduler_grant(
        {"provider": "multi", "attributes": json.dumps(SCHEDULE), "query_raw": DELETE}
    ) is True


@pytest.mark.parametrize("raw", ["SELECT 1", "{not json", "[1, 2]"])
def test_scheduled_multi_with_unparseable_query_raw_fails_closed(raw):
    """A scheduled multi whose query_raw json.loads cannot read requires the grant (review S2)."""
    assert definition_requires_scheduler_grant(_definition(raw)) is True
