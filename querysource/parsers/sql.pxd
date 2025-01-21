# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: sql.pxd
from .abstract cimport AbstractParser

cdef class SQLParser(AbstractParser):
    cdef public str _base_sql
    cdef public object valid_operators
