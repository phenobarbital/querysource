# abstract.pxd
from cpython cimport list, dict, tuple
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
    cdef public str tablename
    cdef public bint schema_based
    cdef public str schema
    cdef public str database
    # Query Options:
    cdef str _slug
    cdef public int querylimit
    cdef public dict cond_definition
    cdef public dict _conditions
    cdef public int32_t _limit
    cdef public int32_t _offset
    cdef str _distinct
    # Parser Options:
    cdef public dict params
    cdef public dict _query_filters
    cdef public list _hierarchy
    cdef int32_t c_length
    cdef bint _paged
    cdef int32_t _page_
    # internal:
    cdef public object _redis
    cdef bint _add_fields

    # methods:
    cpdef object sentence(self, str sentence)
    cdef void set_attributes(self)
    cdef void define_conditions(self, object conditions)
    cpdef dict get_query_filters(self)
    cpdef object where_cond(self, dict where)
    cpdef str query(self)
    cpdef str filtering_options(self, str sentence)
    cdef object _get_function_replacement(self, object function, str key, object val)
    cdef dict _merge_conditions_and_filters(self, dict conditions)
    cdef bint _handle_keys(self, str key, object val, dict _filter)
