"""
Function Executor.

This Python file defines a function executor (fnExecutor)
that allows calling arbitrary functions
(including built-in functions and functions defined in specific modules)
with provided arguments.
It also includes a function lookup mechanism and error handling.
Its primary purpose is to provide a flexible way to apply transformations
to data within a larger data processing pipeline.
"""
import logging
import traceback
from typing import Any
from collections.abc import Callable
from ..types.validators import Entity
from .getfunc import getFunction


def exception_fnexecutor(exc: Exception) -> None:
    """
    Exception handler for Function Executor.
    """
    logging.exception(str(exc), exc_info=True, stack_info=True)
    traceback.print_exc()


def fnExecutor(
    value: Any, env: Callable = None, escape: bool = False, quoting: bool = False
) -> Any:
    """Executes a function with given arguments.

    Overview:
    attempts to execute a function specified by `value`
    with optional keyword arguments (`kwargs`). It handles various scenarios,
    including providing an environment context and string escaping.

    Parameters:
        value (Any): If a list, the first element is treated as the function
            name and the second (optional) element as keyword arguments.
            If not a list, the value is returned with potential string formatting.
        env (Callable, optional): An environment callable to pass to the function.
            Defaults to None.
        escape (bool, optional): Whether to escape the string value if `value`
            is a string. Defaults to False.
        quoting (bool, optional): Whether to quote the string value if `value`
            is a string. Defaults to False.

    Returns:
        Any: The result of the function execution, the formatted string value,
            or the original value.  Returns None if the function doesn't exist
            or "" if an error occurs during function execution.

    Raises:
        TypeError: If the function call fails due to incorrect argument types.
        ValueError: If the function call fails due to incorrect argument values.
        NameError: If the specified function name is not found.
        KeyError: If a required keyword argument is missing.
    """
    if isinstance(value, list):
        try:
            fname, kwargs = (value + [{}])[:2]  # Safely unpack with default args
            func = getFunction(fname)
            if not func:
                logging.warning(f"Function {fname} doesn't exist in Builtins or QS.")
                return None
            if kwargs:
                if env is not None:
                    kwargs["env"] = env
                try:
                    try:
                        return func(**kwargs)
                    except TypeError:
                        if "env" in kwargs:
                            del kwargs["env"]
                        return func(**kwargs)
                    except Exception as e:
                        print("FN > ", e)
                        exception_fnexecutor(e)
                except (TypeError, ValueError) as err:
                    exception_fnexecutor(err)
                    return ""
            else:
                try:
                    return func()
                except (TypeError, ValueError):
                    return ""
        except (NameError, KeyError) as err:
            exception_fnexecutor(err)
            return ""
    else:
        if isinstance(value, str):
            if escape is True:
                return f"'{str(value)}'"
            elif quoting is True:
                return Entity.quoteString(value)
            else:
                return f"{str(value)}"
        return value
