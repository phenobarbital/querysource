# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: deltatbl.pxd
from .sql cimport SQLParser


cdef class DeltaTableParser(SQLParser):
    cdef public str delta_path
    cdef public str delta_tablename
    cdef public str _factory
    cdef public str _mode
