import importlib
import contextlib
import builtins
from navconfig.logging import logging
from . import functions as qsfunctions


def getFunction(fname: str) -> callable:
    """
    Get a function from a predefined whitelist of allowed functions.

    Args:
        fname (str): Name of the function to retrieve.

    Returns:
        callable: The requested function.

    Raises:
        ValueError: If the requested function is not in the whitelist.
    """
    with contextlib.suppress(TypeError, AttributeError):
        return getattr(qsfunctions, fname)
    try:
        func = globals().get(fname)
        if func:
            return func
    except AttributeError:
        pass
    try:
        return getattr(builtins, fname)
    except AttributeError:
        pass
    # If the function name contains dots, try to import the module and get the attribute
    if '.' in fname:
        components = fname.split('.')
        module_name = '.'.join(components[:-1])
        attr_name = components[-1]
        try:
            module = importlib.import_module(module_name)
            func = getattr(module, attr_name)
            return func
        except (ImportError, AttributeError) as e:
            # Function doesn't exists:
            print(f'Cannot find Module {e}')
    logging.warning(
        f"Function {fname} not found in any known modules."
    )
    return None
