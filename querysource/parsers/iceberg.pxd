# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: iceberg.pxd
from .sql cimport SQLParser


cdef class IcebergParser(SQLParser):
    cdef public str table_id
    cdef public str namespace
    cdef public str _factory
