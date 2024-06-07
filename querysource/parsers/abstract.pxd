# query_parser.pxd
from abc import ABC, abstractmethod
from ..models import QueryObject


cdef class AbstractParser:
    cdef public str query_raw
    cdef public dict options
    cdef public QueryObject conditions
    cdef public str query_parsed
    cdef public dict filter
    cdef public int querylimit
    cdef public list fields
    cdef public list ordering
    cdef public list grouping

    def __init__(self, str query, dict options, dict conditions):
        pass
    cpdef void set_conditions(self, dict conditions)
    cpdef void parse_query(self)
    cpdef str build_query(self)
