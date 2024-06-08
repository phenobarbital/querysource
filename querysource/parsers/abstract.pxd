# abstract.pxd
from cpython cimport list, dict
from libc.stdint cimport int32_t
from ..types.mapping cimport ClassDict
from ..models import QueryObject


cdef class AbstractParser:
    cdef str _name_
    cdef public object logger
    cdef public str query_raw
    cdef public object definition
    cdef public ClassDict conditions
    cdef public str query_parsed
    cdef public dict filter
    cdef public dict filter_options
    cdef public list fields
    cdef public list ordering
    cdef public list grouping
    cdef public str program_slug
    cdef public bint refresh
    cdef public str table
    cdef public str database
    # Query Options:
    cdef str _slug
    cdef public int querylimit
    cdef dict cond_definition
    cdef str _distinct
    # Parser Options:
    cdef public dict params
    cdef list _hierarchy
    cdef dict _query_filters
    cdef int32_t _limit
    cdef int32_t _offset
    cdef int32_t c_length
    cdef bint _paged
    cdef int32_t _page_
    # internal:
    cdef object _redis

    cdef void set_attributes(self)
    cdef void define_conditions(self, dict conditions)
    cpdef str query(self)
