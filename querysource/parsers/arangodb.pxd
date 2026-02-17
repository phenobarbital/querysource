# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: arangodb.pxd
"""ArangoDB AQL Parser declaration file."""
from .abstract cimport AbstractParser

cdef class ArangoDBParser(AbstractParser):
    """ArangoDB AQL Parser declaration."""
    cdef:
        public dict _base_query
        public dict _graph_options
        public dict _search_options
        public str _doc_var

    cpdef str query(self)

    # Private fallback methods
    cdef str _build_filter_clause_cy(self)
    cdef str _build_return_clause_cy(self)
    cdef str _build_sort_clause_cy(self)
