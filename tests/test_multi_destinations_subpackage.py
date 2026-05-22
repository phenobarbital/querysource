"""Subpackage skeleton — TASK-669 and subsequent tasks."""


def test_shim_reexports_abstract_destination():
    from querysource.queries.multi.destinations import AbstractDestination as ADestNew
    from querysource.outputs.destinations.abstract import AbstractDestination as ADestOrig
    assert ADestNew is ADestOrig


def test_registry_is_dict():
    from querysource.queries.multi.destinations import DESTINATION_REGISTRY
    assert isinstance(DESTINATION_REGISTRY, dict)


def test_registry_starts_empty_or_only_contains_abstractdestination_subclasses():
    """At this stage no concrete destinations have been moved here yet (TASK-670).

    Should the test run after TASK-670 lands, the registry will contain
    ToSharepoint, ToS3, TableDestination, DWHDestination. In either case
    every entry must be an AbstractDestination subclass.
    """
    from querysource.queries.multi.destinations import (
        AbstractDestination,
        DESTINATION_REGISTRY,
    )
    for name, cls in DESTINATION_REGISTRY.items():
        assert issubclass(cls, AbstractDestination), (
            f"{name} is registered but does not inherit AbstractDestination"
        )


def test_legacy_registry_still_has_six_keys():
    from querysource.outputs.destinations import DESTINATION_REGISTRY
    expected = {"tableOutput", "TableOutput", "ToSharepoint", "ToS3", "Table", "DWH"}
    assert expected.issubset(set(DESTINATION_REGISTRY))


def test_legacy_registry_points_to_migrated_classes():
    from querysource.outputs.destinations import DESTINATION_REGISTRY
    from querysource.queries.multi.destinations.sharepoint import ToSharepoint
    from querysource.queries.multi.destinations.s3 import ToS3
    from querysource.queries.multi.destinations.table import TableDestination
    from querysource.queries.multi.destinations.dwh import DWHDestination
    assert DESTINATION_REGISTRY["ToSharepoint"] is ToSharepoint
    assert DESTINATION_REGISTRY["ToS3"] is ToS3
    assert DESTINATION_REGISTRY["Table"] is TableDestination
    assert DESTINATION_REGISTRY["DWH"] is DWHDestination


def test_table_output_adapter_still_available():
    from querysource.outputs.destinations import TableOutputAdapter
    from querysource.outputs.destinations.abstract import AbstractDestination
    assert issubclass(TableOutputAdapter, AbstractDestination)
