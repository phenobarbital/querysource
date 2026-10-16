"""Regression coverage for the semantic PBAC resource type."""

from querysource.auth._resource_types import ResourceType


def test_resource_type_semantic_shim() -> None:
    """Expose semantic as a string resource under both shim implementations."""
    assert ResourceType.SEMANTIC == "semantic"
