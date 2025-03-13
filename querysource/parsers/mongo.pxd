# cython: language_level=3
# distutils: language = c++
# Copyright (C) 2018-present Jesus Lara
#
"""
MongoDB/DocumentDB Parser declaration file.
"""
from typing import Any, Union, Optional, Dict, List, Tuple
from .abstract cimport AbstractParser

cdef class MongoParser(AbstractParser):
    """MongoDB Parser declaration."""
    cdef:
        public tuple valid_operators
        public dict operator_map
        public dict _base_query

    cpdef str query(self)
