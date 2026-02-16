# cython: language_level=3
from .parser cimport QueryParser


cdef class InfluxParser(QueryParser):
    cdef str bucket
    cdef str measurement
