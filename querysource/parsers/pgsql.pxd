# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: pgsql.pxd
from .sql cimport SQLParser

cdef class pgSQLParser(SQLParser):
    pass
