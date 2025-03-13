# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: abstract.pxd
from libc.stdint cimport int32_t
from ..models import QueryObject


cdef class AbstractParser:
    cdef str _name_
    cdef public object logger
    cdef public str query_raw
    cdef public object definition
    cdef public object conditions
    cdef public str query_parsed
    cdef public object query_object
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
    cdef public dict attributes
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
    cdef public bint _safe_substitution
    cdef public bint string_literal

    # methods:
    cpdef object sentence(self, str sentence)
    cdef void set_attributes(self)
    cdef void define_conditions(self, object conditions)
    cpdef dict get_query_filters(self)
    cpdef object where_cond(self, dict where)
    cpdef str query(self)
    cpdef str filtering_options(self, str sentence)
    cdef object _get_function_replacement(self, object function, str key, object val)
    cdef object _merge_conditions_and_filters(self, dict conditions)
    cdef bint _handle_keys(self, str key, object val, dict _filter)
