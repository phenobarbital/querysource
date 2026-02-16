# cython: language_level=3
from .sql cimport SQLParser


cdef class CQLParser(SQLParser):
    cdef str _tablename
