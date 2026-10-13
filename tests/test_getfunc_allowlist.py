"""FEAT-166 TASK-868: getFunction resolves only querysource.utils.functions."""
import pytest

from querysource.utils import functions as qsfunctions
from querysource.utils.getfunc import getFunction


@pytest.mark.parametrize("name", ["today", "now", "to_date", "trim", "fdom"])
def test_allowlisted_names_resolve(name):
    assert getFunction(name) is getattr(qsfunctions, name)


@pytest.mark.parametrize("name", [
    "os.system", "subprocess.run", "importlib.import_module",   # dotted → None
    "eval", "exec", "open", "__import__", "getattr",           # builtins → None
    "importlib", "logging", "qsfunctions", "builtins",         # module globals → None
    "_private", "", None, 42, ["today"],                        # malformed → None
])
def test_disallowed_names_return_none(name):
    assert getFunction(name) is None


def test_non_callable_attribute_returns_none():
    # module-level non-callable attributes of qsfunctions must not be returned
    non_callables = [
        n for n in dir(qsfunctions)
        if not n.startswith('_') and not callable(getattr(qsfunctions, n))
    ]
    for name in non_callables:
        assert getFunction(name) is None, name


def test_getfunc_module_has_no_dynamic_import_machinery():
    import querysource.utils.getfunc as mod

    for forbidden in ("importlib", "builtins", "contextlib"):
        assert not hasattr(mod, forbidden), forbidden


@pytest.mark.parametrize("name", ["Path", "PurePath", "UUID", "ZoneInfo", "relativedelta", "urlparse", "Number"])
def test_reexported_names_are_not_resolvable(name):
    assert callable(getattr(qsfunctions, name))  # they exist on the module...
    assert getFunction(name) is None             # ...but are not QuerySource helpers


@pytest.mark.parametrize("name", ["uri_exists", "to_udf"])
def test_denied_module_functions_are_not_resolvable(name):
    assert getFunction(name) is None


def test_map_only_calls_catalogued_transform_functions():
    import asyncio

    import pandas as pd

    from querysource.queries.multi.transformations.Map import Map

    names = Map.transform_functions()
    assert "getFunction" not in names and "apply_function" in names
    df = pd.DataFrame({"a": [1, 2]})
    # a module attribute that is NOT a transform function must not be called with df=/field=
    op = Map(data=df.copy(), fields={"a": [["getFunction", {}]]})

    async def run():
        async with op as o:
            return await o.run()

    out = asyncio.run(run())
    assert list(out["a"]) == [1, 2]
