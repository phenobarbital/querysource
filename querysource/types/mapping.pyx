# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=False, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
# mapping.pyx
import sys
from typing import Optional, Union, Any
from cpython cimport dict
from datamodel import Field
from collections.abc import Iterator, Iterable
if sys.version_info < (3, 10):
    from typing_extensions import ParamSpec
else:
    from typing import ParamSpec
P = ParamSpec("P")


cdef class ClassDict(dict):

    def __cinit__(
        self,
        *args,
        data: Optional[dict] = None,
        default: Optional[Union[list,dict]] = None,
        **kwargs: P.kwargs
    ):
        self.mapping = {}
        self._columns = []
        self.default = default
        self.mapping.update(*args, **kwargs)
        self.update(data, **kwargs)

    def update(self, items: Optional[dict]=None, **kwargs: P.kwargs):
        if isinstance(items, dict):
            for key, value in items.items():
                print(key, value)
                self.mapping[key] = value
        else:
            for k, v in kwargs.items():
                attr = getattr(self, k)
                if isinstance(attr, Field):
                    try:
                        fn = attr.default
                        if callable(fn):
                            v = fn(v)
                            setattr(self, k, v)
                        else:
                            v = fn
                            setattr(self, k, fn)
                    except (TypeError, KeyError) as exc:
                        print(exc)
                        pass
                self.mapping[k] = v
        self._columns = list(self.mapping.keys())

    def __missing__(self, key: str):
        return self.default

    def __len__(self):
        """Returns the number of items in the dictionary."""
        return len(self.mapping)

    def _str__(self):
        return f"<{type(self).__name__}({self.mapping})>"

    def __repr__(self):
        return f"<{type(self).__name__}({self.mapping})>"

    def __contains__(self, key: str):
        """Check if key is in the dictionary."""
        return key in self._columns

    def get(self, key: str, default: Any = None):
        return self.mapping.get(key, default)

    def __delitem__(self, key: str):
        if key in self.mapping:
            self.mapping.pop(key, None)
            self._columns.remove(key)
            if hasattr(self, key):
                setattr(self, key, None)
        else:
            raise KeyError(key)

    def __setitem__(self, key: str, value: Any):
        self.mapping[key] = value
        if key not in self._columns:
            self._columns.append(key)

    def __getitem__(self, key: str):
        if isinstance(key, list):
            return [self.mapping[k] for k in key]
        try:
            try:
                return self.mapping[key]
            except KeyError:
                return None
        except KeyError:
            return self.default

    def keys(self):
        return self.mapping.keys()

    def values(self):
        return self.mapping.values()

    def items(self):
        return self.mapping.items()

    def pop(self, key: str, default: Any = None):
        try:
            value = self[key]
            del self[key]
            return value
        except KeyError:
            return default

    def clear(self):
        self.mapping.clear()
        self._columns = []

    def __iter__(self) -> Iterator:
        for value in self.mapping:
            yield value

    def __getattr__(self, attr: str) -> Any:
        """
        Attributes for dict keys
        """
        if attr in self.mapping:
            return self.mapping[attr]
        elif attr in self._columns:
            return self.mapping[attr]

        raise KeyError(
            f"Mapping: invalid field name {attr} on {type(self).__name__}"
        )

    def __delattr__(self, attr: str) -> None:
        if attr in self.mapping:
            self.pop(attr, None)
