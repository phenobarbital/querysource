"""FEAT-097 — verify get_destination dispatches to migrated classes."""


def test_get_destination_returns_migrated_class():
    from querysource.outputs.destinations import get_destination
    from querysource.queries.multi.destinations.sharepoint import ToSharepoint
    from querysource.queries.multi.destinations.s3 import ToS3
    from querysource.queries.multi.destinations.table import TableDestination
    from querysource.queries.multi.destinations.dwh import DWHDestination

    assert get_destination("ToSharepoint") is ToSharepoint
    assert get_destination("ToS3") is ToS3
    assert get_destination("Table") is TableDestination
    assert get_destination("DWH") is DWHDestination


def test_get_destination_table_output_still_routes_to_adapter():
    from querysource.outputs.destinations import get_destination, TableOutputAdapter
    assert get_destination("tableOutput") is TableOutputAdapter
    assert get_destination("TableOutput") is TableOutputAdapter


def test_migrated_classes_are_abstractdestination_subclasses():
    from querysource.outputs.destinations.abstract import AbstractDestination
    from querysource.queries.multi.destinations.sharepoint import ToSharepoint
    from querysource.queries.multi.destinations.s3 import ToS3
    from querysource.queries.multi.destinations.table import TableDestination
    from querysource.queries.multi.destinations.dwh import DWHDestination

    for cls in (ToSharepoint, ToS3, TableDestination, DWHDestination):
        assert issubclass(cls, AbstractDestination), (
            f"{cls.__name__} does not inherit AbstractDestination"
        )


def test_migrated_classes_have_category_destinations():
    from querysource.queries.multi.destinations.sharepoint import ToSharepoint
    from querysource.queries.multi.destinations.s3 import ToS3
    from querysource.queries.multi.destinations.table import TableDestination
    from querysource.queries.multi.destinations.dwh import DWHDestination

    for cls in (ToSharepoint, ToS3, TableDestination, DWHDestination):
        desc = cls.get_description()
        assert desc.get("category") == "Destinations", (
            f"{cls.__name__}.get_description() returned category={desc.get('category')!r}; "
            "expected 'Destinations'"
        )


def test_shim_class_identity_sharepoint():
    """outputs/destinations/sharepoint.py shim must re-export the exact same class object."""
    from querysource.outputs.destinations.sharepoint import ToSharepoint as ShimClass
    from querysource.queries.multi.destinations.sharepoint import ToSharepoint as RealClass
    assert ShimClass is RealClass


def test_shim_class_identity_s3():
    from querysource.outputs.destinations.s3 import ToS3 as ShimClass
    from querysource.queries.multi.destinations.s3 import ToS3 as RealClass
    assert ShimClass is RealClass


def test_shim_class_identity_table():
    from querysource.outputs.destinations.table import TableDestination as ShimClass
    from querysource.queries.multi.destinations.table import TableDestination as RealClass
    assert ShimClass is RealClass


def test_shim_class_identity_dwh():
    from querysource.outputs.destinations.dwh import DWHDestination as ShimClass
    from querysource.queries.multi.destinations.dwh import DWHDestination as RealClass
    assert ShimClass is RealClass
