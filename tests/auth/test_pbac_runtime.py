"""Unit tests for the process-wide PBAC runtime handle (FEAT-150, TASK-749)."""
import sys
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web

from querysource.auth.pbac import clear_pbac_runtime, get_pbac_runtime, setup_pbac


@pytest.fixture(autouse=True)
def _reset_runtime():
    clear_pbac_runtime()
    yield
    clear_pbac_runtime()


def test_bootstrap_records_runtime(tmp_path):
    """AC-1: a successful fresh bootstrap records the exact app instances."""
    app = web.Application()
    _pdp, _evaluator, guardian = setup_pbac(app, policy_dir=str(tmp_path))
    if guardian is None:
        pytest.skip("navigator-auth PBAC bootstrap unavailable in this environment")
    runtime = get_pbac_runtime()
    assert runtime is not None
    assert runtime.evaluator is app["policy_evaluator"]
    assert runtime.guardian is app["security"]


def test_reuse_branch_records_existing():
    """AC-2: the reuse branch records the pre-existing guardian/evaluator pair."""
    app = web.Application()
    pre_guardian = MagicMock()
    pre_evaluator = MagicMock()
    app["security"] = pre_guardian
    app["policy_evaluator"] = pre_evaluator

    setup_pbac(app, policy_dir="/any/path")

    runtime = get_pbac_runtime()
    assert runtime is not None
    assert runtime.guardian is pre_guardian
    assert runtime.evaluator is pre_evaluator


def test_failure_leaves_runtime_none():
    """AC-3: navigator-auth import failure leaves the runtime unset."""
    app = web.Application()
    blocked = {
        "navigator_auth.abac.pdp": None,
        "navigator_auth.abac.guardian": None,
        "navigator_auth.abac.policies.evaluator": None,
        "navigator_auth.abac.policies.abstract": None,
        "navigator_auth.abac.storages.yaml_storage": None,
    }
    with patch.dict(sys.modules, blocked):
        result = setup_pbac(app, policy_dir="/any/path")

    assert result == (None, None, None)
    assert get_pbac_runtime() is None


def test_clear():
    """AC-4: clear_pbac_runtime() resets the handle to None."""
    app = web.Application()
    app["security"] = MagicMock()
    app["policy_evaluator"] = MagicMock()
    setup_pbac(app, policy_dir="/any/path")
    assert get_pbac_runtime() is not None

    clear_pbac_runtime()

    assert get_pbac_runtime() is None
