"""Unit tests for the enriched OutputError exception (FEAT-146, TASK-711)."""
from querysource.exceptions import OutputError, QueryException


def test_backwards_compatible_message():
    err = OutputError("boom")
    assert str(err) == "boom"
    assert isinstance(err, QueryException)
    assert err.step_name is None
    assert err.category is None


def test_carries_step_name_and_category():
    err = OutputError("boom", step_name="TableOutput", category="data")
    assert err.step_name == "TableOutput"
    assert err.category == "data"


def test_defaults_when_not_provided():
    err = OutputError("boom", step_name="TableOutput")
    assert err.step_name == "TableOutput"
    assert err.category is None
