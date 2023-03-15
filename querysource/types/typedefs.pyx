# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=True, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
#
class SafeDict(dict):
    """
    SafeDict.

    Allow to using partial format strings

    """
    def __missing__(self, str key):
        """Missing method for SafeDict."""
        return "{" + key + "}"

class AttrDict(dict):
    """
    AttrDict.
    Allow to using a dictionary like an object
    """
    def __init__(self, *args, **kwargs):
        super(AttrDict, self).__init__(*args, **kwargs)
        self.__dict__ = self

    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class NullDefault(dict):
    """NullDefault.

    When an attribute is missing, return default.
    """
    def __missing__(self, key):
        return ''


cdef class Singleton(type):
    """Singleton.
    Metaclass for Singleton instances.
    Returns:
        cls: a singleton version of the class, there are only one
        version of the instance any time.
    """
    cdef dict _instances

    def __call__(object cls, *args, **kwargs):
        if cls._instances is None:
            cls._instances = {}
        if cls not in cls._instances:
            cls._instances[cls] = super(
                Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]

    cdef object __new__(cls, args, kwargs):
        if cls._instances is None:
            cls._instances = {}
        if cls not in cls._instances:
            cls._instances[cls] = super(
                Singleton, cls).__new__(cls, *args, **kwargs)
            setattr(cls, '__initialized__', True)
        return cls._instances[cls]


cpdef object strtobool(str val):
    """Convert a string representation of truth to true (1) or false (0).

    True values are 'y', 'yes', 't', 'true', 'on', and '1'; false values
    are 'n', 'no', 'f', 'false', 'off', and '0'.  Raises ValueError if
    'val' is anything else.
    """
    val = val.lower()
    if val in ('y', 'yes', 't', 'true', 'on', '1'):
        return True
    elif val in ('n', 'no', 'f', 'false', 'off', '0', 'null'):
        return False
    else:
        raise ValueError(
            f"invalid truth value for {val}"
        )
