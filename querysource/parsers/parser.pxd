# cython: language_level=3
# parser.pxd
from .abstract cimport AbstractParser

cdef class QueryParser(AbstractParser):
    cdef public str _tablename
    cdef public str _base_sql
