# cython: language_level=3
from .parser cimport QueryParser


cdef class InfluxParser(QueryParser):
    cdef public str bucket
    cdef public str measurement
