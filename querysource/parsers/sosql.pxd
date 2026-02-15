# cython: language_level=3
from .sql cimport SQLParser


cdef class SOQLParser(SQLParser):
    cdef object _connection
