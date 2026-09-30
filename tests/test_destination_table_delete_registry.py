"""FEAT-155 / TASK-816: the ``TableDelete`` step name resolves to TableDeleteDestination."""
from querysource.outputs.destinations import DESTINATION_REGISTRY, get_destination
from querysource.queries.multi.destinations.table_delete import TableDeleteDestination


def test_registry_has_tabledelete():
    assert get_destination("TableDelete") is TableDeleteDestination
    assert DESTINATION_REGISTRY["TableDelete"] is TableDeleteDestination
