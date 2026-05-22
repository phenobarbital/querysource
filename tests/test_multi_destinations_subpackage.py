"""Subpackage skeleton — TASK-669 and subsequent tasks."""
import pytest


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
