# cython: language_level=3
# distutils: language = c++
# Copyright (C) 2018-present Jesus Lara
#
"""Elasticsearch Parser declaration file."""
from typing import Any, Union, Optional, Dict, List, Tuple
from .abstract cimport AbstractParser

cdef class ElasticParser(AbstractParser):
    """Elasticsearch Parser declaration."""
    cdef:
        public tuple valid_operators
        public dict operator_map
        public dict _base_query

    cpdef str query(self)

    # Private fallback methods
    cdef object _process_fields_cy(self)
    cdef object _filter_conditions_cy(self)
    cdef object _process_ordering_cy(self)
