# cython: language_level=3
# Copyright (C) 2018-present Jesus Lara
#
"""RethinkDB Parser declaration file."""
from .abstract cimport AbstractParser

cdef class RethinkParser(AbstractParser):
    """RethinkDB Parser declaration."""
    cdef:
        public bint _create_indexes
        public object _join_field
        public object _engine
        public object _connection
        public dict _map
        public list _has_fields

    # Private Cython fallback methods
    cdef object _process_fields_cy(self)
    cdef object _process_ordering_cy(self, query)
    cdef object _prepare_ordering_list(self)
