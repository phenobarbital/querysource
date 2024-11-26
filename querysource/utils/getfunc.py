import importlib
import builtins
from navconfig.logging import logging
from . import functions as qsfunctions


def getFunction(fname: str) -> callable:
    """
    Get any function using name.
    """
    try:
        return getattr(qsfunctions, fname)
    except (TypeError, AttributeError):
        pass
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
