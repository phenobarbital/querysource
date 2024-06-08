# cython: language_level=3
# parser.pxd
from .abstract cimport AbstractParser

cdef class QueryParser(AbstractParser):
    cdef str _tablename
    cdef str _base_sql
