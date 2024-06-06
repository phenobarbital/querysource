# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=True, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
# file: obj.pxd

from typing import Optional, Union

cdef class QueryObject:
    cdef public Optional[str] source
    cdef public Optional[str] driver
    cdef public dict conditions
    cdef public Optional[dict] coldef
    cdef public list fields
    cdef public Optional[list] ordering
    cdef public Optional[list] group_by
    cdef public Optional[dict] qry_options
    cdef public Optional[dict] filter
    cdef public Optional[dict] where_cond
    cdef public Optional[dict] and_cond
    cdef public Optional[list] hierarchy
    cdef public int querylimit
    cdef public str query_raw
