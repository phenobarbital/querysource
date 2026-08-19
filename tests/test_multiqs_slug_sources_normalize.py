"""Regression tests: MultiQS must normalize `sources` on the slug path.

Bug: when a multi-query is loaded from a stored slug whose ``sources`` uses the
dict convenience form (``{"alias": {"type": "SharepointSource", ...}}``), the
slug path assigned ``self._sources`` the raw dict WITHOUT normalizing it. The
downstream dispatch loop does::

    for entry in self._sources:
        for source_type, config in entry.items():

Iterating a dict yields its string keys, so ``entry.items()`` raised
``AttributeError: 'str' object has no attribute 'items'``. The inline (POST)
path worked because ``__init__`` normalizes via ``_normalize_sources``; only the
slug path was affected.

These tests pin the normalization behaviour that the fix relies on, and assert
that the dispatch-style iteration no longer breaks on the dict form.
"""
import pytest

from querysource.queries import MultiQS


def _dispatch_iterates(sources):
    """Mimic the dispatch loop in MultiQS.query(): each entry must be a dict."""
    pairs = []
    for entry in sources:
        for source_type, config in entry.items():
            pairs.append((source_type, config))
    return pairs


def test_normalize_dict_form_to_list_of_dicts():
    """The frontend dict form is converted to the canonical list form,
    dropping the ``type`` field into the entry key."""
    raw = {
        "sharepoint": {
            "type": "SharepointSource",
            "credentials": {"client_id": "X"},
            "source": {"url": "u"},
        }
    }
    normalized = MultiQS._normalize_sources(raw)
    assert normalized == [
        {
            "SharepointSource": {
                "credentials": {"client_id": "X"},
                "source": {"url": "u"},
            }
        }
    ]


def test_normalize_list_form_is_idempotent():
    """The canonical list form is returned unchanged."""
    raw = [{"SharepointSource": {"source": {"url": "u"}}}]
    assert MultiQS._normalize_sources(raw) == raw


def test_dict_form_breaks_dispatch_without_normalization():
    """Guard: iterating the raw dict form the old way raises the exact
    production error, proving the normalization is required."""
    raw = {"sharepoint": {"type": "SharepointSource", "source": {"url": "u"}}}
    with pytest.raises(AttributeError, match="'str' object has no attribute 'items'"):
        _dispatch_iterates(raw)


def test_dict_form_dispatch_works_after_normalization():
    """After normalization the dispatch loop iterates dict entries cleanly."""
    raw = {"sharepoint": {"type": "SharepointSource", "source": {"url": "u"}}}
    normalized = MultiQS._normalize_sources(raw)
    pairs = _dispatch_iterates(normalized)
    assert pairs == [("SharepointSource", {"source": {"url": "u"}})]
