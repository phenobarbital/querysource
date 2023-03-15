# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=True, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
#
import cython
import orjson
from typing import Type
from libcpp cimport bool as bool_t
from libcpp.string cimport string
from querysource.exceptions cimport ParserError


prefixes = frozenset(('(', '[', '{'))

cpdef object is_parseable(str value):
    cdef str c = value[0]
    if c in prefixes:
        if c == '[':
            return ParseList
        elif c == '(':
            return ParseTuple
        elif c == '{':
            return ParseDict
    else:
        return False


cdef str parseString(str value):
    if isinstance(value, str):
        if value.startswith("'"):
            return value[value.startswith('"') and len('"'):-1]
    return value

cdef str remove_enclosing(str value, str prefix):
    return value[value.startswith(prefix) and len(prefix):-1]

class ParseQS:
    prefix: str = ''
    result: list = []

    def parse(self):
        obj = remove_enclosing(self.value, self.prefix)
        elements = obj.split(self.separator)
        if self.instance_class is not None:
           return self.instance_class(elements)
        for key, val in enumerate(elements):
            try:
                self.result.append(parseString(val))
            except ValueError:
                raise ParserError(
                    f'{self.__class__.__name__}: Cannot parse QueryString as Iterable'
                )
        return self.result

    def __new__(cls, value, instance_class: Type = None):
        cls.separator = ','
        cls.result = []
        cls.instance_class = instance_class
        cls.value = value
        return cls.parse(cls)


class ParseList(ParseQS):
    prefix: str = '['

class ParseTuple(ParseQS):
    prefix: str = '('

class ParseDict(ParseQS):
    def parse(self):
        try:
            self.result = orjson.loads(self.value)
            if self.instance_class is not None:
                return self.instance_class(self.result)
            else:
                return self.result
        except Exception as err:
            raise ParserError(
                f'{self.__class__.__name__}: Cannot parse QueryString as Dictionary'
            )
