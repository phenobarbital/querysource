# cython: language_level=3
from .sql cimport SQLParser


cdef class msSQLParser(SQLParser):
    cdef bint _procedure
