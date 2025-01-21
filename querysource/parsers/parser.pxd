# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: parser.pxd
from .abstract cimport AbstractParser

cdef class QueryParser(AbstractParser):
    pass
