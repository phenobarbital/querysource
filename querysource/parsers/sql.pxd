# cython: language_level=3
# parser.pxd
from .abstract cimport AbstractParser

cdef class SQLParser(AbstractParser):
    cdef public str _base_sql
    cdef public object valid_operators
