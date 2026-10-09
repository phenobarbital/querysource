"""FEAT-165: array/json typed values never crash on a plain string."""

from querysource.types.validators import is_array, is_collection, is_valid


def test_is_collection():
    """Recognise collection values while excluding string-like values."""
    assert is_collection(["a"]) and is_collection({"a": 1}) and is_collection(("a",))
    assert not is_collection("vip") and not is_collection(b"vip")


def test_is_valid_array_rejects_str():
    """Do not convert a scalar array value through ``to_unquoted``."""
    assert is_valid("tags", "vip", "array") is not None


def test_array_and_json_collections_are_recognised():
    """Keep collection recognition for the array and JSON validator rows."""
    assert is_collection(["a"])
    assert is_collection({"a": 1})


def test_is_array_still_accepts_strings():
    """Preserve the broader legacy predicate used by other callers."""
    assert is_array("vip")
