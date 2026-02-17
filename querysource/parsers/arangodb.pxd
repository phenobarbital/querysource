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

    # Private cdef methods
    cdef str _build_filter_clause_cy(self)
    cdef list _build_filter_clauses_list(self)
    cdef str _build_return_clause_cy(self)
    cdef str _build_sort_clause_cy(self)
    cdef str _build_graph_for_clause(self)
    cdef str _build_search_clause(self)
    cdef str _build_query_cy(self, str collection, int limit, int offset)
